#!/usr/bin/env python3
"""Emit a JSON manifest of MCP tools + resources from ``mcp_server/``.

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
MCP_DIR = PROJECT_ROOT / "src" / "tapps_brain" / "mcp_server"
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
        # Health for agents is brain_status (already listed above); do not
        # include operator-gated tapps_brain_health in the core set.
    ]
)

# Must stay in sync with ``_OPERATOR_TOOL_NAMES`` in
# ``src/tapps_brain/mcp_server/server.py`` (default sessions strip these).
OPERATOR_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "maintenance_consolidate",
        "maintenance_gc",
        "maintenance_stale",
        "tapps_brain_health",
        "memory_gc_config",
        "memory_gc_config_set",
        "memory_consolidation_config",
        "memory_consolidation_config_set",
        "memory_export",
        "memory_import",
        "tapps_brain_relay_export",
        "flywheel_evaluate",
        "flywheel_hive_feedback",
    }
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


def _first_doc_line(node: ast.FunctionDef) -> str:
    doc = ast.get_docstring(node) or ""
    return (doc.strip().split("\n") or [""])[0].strip()


def _collect_from_file(path: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return ``(tools, resources)`` from one ``tools_*.py`` module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tools: list[tuple[str, str]] = []
    resources: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
            tools.append((node.name, _first_doc_line(node)))
            continue
        for dec in node.decorator_list:
            if _is_mcp_resource_decorator(dec):
                uri = _resource_uri_from_decorator(dec)
                if uri:
                    resources.append((uri, _first_doc_line(node)))
                break
    return tools, resources


def main() -> int:
    if not MCP_DIR.is_dir():
        print(f"missing {MCP_DIR}", file=sys.stderr)
        return 1

    tool_map: dict[str, str] = {}
    resource_map: dict[str, str] = {}
    sources: list[str] = []
    for path in sorted(MCP_DIR.glob("tools_*.py")):
        tools, resources = _collect_from_file(path)
        if tools or resources:
            sources.append(str(path.relative_to(PROJECT_ROOT)))
        for name, desc in tools:
            tool_map[name] = desc
        for uri, desc in resources:
            resource_map[uri] = desc

    resources_out = [{"uri": u, "description": resource_map[u]} for u in sorted(resource_map)]

    all_tool_names = set(tool_map)
    missing_core = CORE_TOOL_NAMES - all_tool_names
    if missing_core:
        print(
            f"WARNING: CORE_TOOL_NAMES contains names not found in mcp_server/: "
            f"{sorted(missing_core)}",
            file=sys.stderr,
        )

    # Default /mcp/ sessions omit operator tools (STORY-062.4). Split the
    # catalog so docs/tool_count match live tools/list, not the AST union.
    default_names = sorted(all_tool_names - OPERATOR_TOOL_NAMES)
    operator_names = sorted(all_tool_names & OPERATOR_TOOL_NAMES)
    default_tools_out = [{"name": n, "description": tool_map[n]} for n in default_names]
    operator_tools_out = [{"name": n, "description": tool_map[n]} for n in operator_names]
    core_tools = sorted((CORE_TOOL_NAMES & all_tool_names) - OPERATOR_TOOL_NAMES)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "src/tapps_brain/mcp_server/tools_*.py",
        "sources": sources,
        "tool_count": len(default_tools_out),
        "operator_tool_count": len(operator_tools_out),
        "resource_count": len(resources_out),
        "core_tool_count": len(core_tools),
        "core_tools": core_tools,
        "tools": default_tools_out,
        "operator_tools": operator_tools_out,
        "resources": resources_out,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Wrote {OUT_PATH} ({len(default_tools_out)} default tools, "
        f"{len(operator_tools_out)} operator tools, {len(resources_out)} resources, "
        f"{len(core_tools)} core tools)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
