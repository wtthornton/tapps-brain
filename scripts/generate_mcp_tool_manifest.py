#!/usr/bin/env python3
"""Emit a JSON manifest of MCP tools + resources from ``mcp_server.py``.

Run from repo root:

    python scripts/generate_mcp_tool_manifest.py

Writes ``docs/generated/mcp-tools-manifest.json`` (stable ordering for diffs).
Canonical **tool_count** / **resource_count** for docs and drift checks.

The manifest also includes a **core_tools** list — the frozen set of tools that
every agent session exposes by default.  Operator / maintenance tools not in this
list will move behind the ``--enable-operator-tools`` flag (STORY-062.4).

To update the core set, edit ``CORE_TOOL_NAMES`` in this file and re-run.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MCP_PATH = PROJECT_ROOT / "src" / "tapps_brain" / "mcp_server.py"
OUT_PATH = PROJECT_ROOT / "docs" / "generated" / "mcp-tools-manifest.json"

# ---------------------------------------------------------------------------
# Core agent tool set — frozen as of STORY-062.3
#
# These are the tools exposed in every default agent session.  They cover the
# primary remember/recall/forget lifecycle plus Hive basics and health.
# Operator/maintenance tools (diagnostics, flywheel, GC config, etc.) are NOT
# in this set and will be gated behind --enable-operator-tools (STORY-062.4).
# ---------------------------------------------------------------------------
CORE_TOOL_NAMES: frozenset[str] = frozenset(
    [
        # Agent Brain facade (EPIC-057) — primary entry points for agents
        "brain_remember",
        "brain_recall",
        "brain_forget",
        "brain_learn_success",
        "brain_learn_failure",
        "brain_status",
        # Memory CRUD
        "memory_save",
        "memory_get",
        "memory_search",
        "memory_list",
        "memory_recall",
        "memory_delete",
        # Context extraction
        "memory_capture",
        "memory_ingest",
        # Reinforce
        "memory_reinforce",
        # Hive basics
        "hive_search",
        "hive_status",
        "hive_propagate",
        # Health
        "tapps_brain_health",
    ]
)


def _is_mcp_tool_decorator(dec: ast.expr) -> bool:
    if isinstance(dec, ast.Call):
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "tool":
            val = func.value
            return isinstance(val, ast.Name) and val.id == "mcp"
    return False


def _is_mcp_resource_decorator(dec: ast.expr) -> bool:
    if isinstance(dec, ast.Call):
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "resource":
            val = func.value
            return isinstance(val, ast.Name) and val.id == "mcp"
    return False


def _resource_uri_from_decorator(dec: ast.expr) -> str | None:
    if isinstance(dec, ast.Call) and dec.args:
        arg0 = dec.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            return arg0.value
    return None


def _find_create_server(tree: ast.Module) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "create_server":
            return node
    return None


def _iter_mcp_tools(create_fn: ast.FunctionDef) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for node in ast.walk(create_fn):
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
            doc = ast.get_docstring(node) or ""
            first = (doc.strip().split("\n") or [""])[0].strip()
            out.append((node.name, first))
    return sorted(out, key=lambda x: x[0])


def _iter_mcp_resources(create_fn: ast.FunctionDef) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for node in ast.walk(create_fn):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if _is_mcp_resource_decorator(dec):
                uri = _resource_uri_from_decorator(dec)
                if uri:
                    doc = ast.get_docstring(node) or ""
                    first = (doc.strip().split("\n") or [""])[0].strip()
                    out.append((uri, first))
                break
    return sorted(out, key=lambda x: x[0])


def main() -> int:
    if not MCP_PATH.is_file():
        print(f"missing {MCP_PATH}", file=sys.stderr)
        return 1
    tree = ast.parse(MCP_PATH.read_text(encoding="utf-8"))
    cs = _find_create_server(tree)
    if cs is None:
        print("create_server not found in mcp_server.py", file=sys.stderr)
        return 1
    tools = [{"name": n, "description": d} for n, d in _iter_mcp_tools(cs)]
    resources = [{"uri": u, "description": d} for u, d in _iter_mcp_resources(cs)]

    # Validate core tool names are present in the full tool list
    all_tool_names = {t["name"] for t in tools}
    missing_core = CORE_TOOL_NAMES - all_tool_names
    if missing_core:
        print(
            f"WARNING: CORE_TOOL_NAMES contains names not found in mcp_server.py: "
            f"{sorted(missing_core)}",
            file=sys.stderr,
        )

    core_tools = sorted(CORE_TOOL_NAMES & all_tool_names)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "src/tapps_brain/mcp_server.py",
        "tool_count": len(tools),
        "resource_count": len(resources),
        "core_tool_count": len(core_tools),
        "core_tools": core_tools,
        "tools": tools,
        "resources": resources,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Wrote {OUT_PATH} ({len(tools)} tools, {len(resources)} resources, "
        f"{len(core_tools)} core tools)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
