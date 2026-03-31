#!/usr/bin/env python3
"""Emit a JSON manifest of MCP tools + resources from ``mcp_server.py``.

Run from repo root:

    python scripts/generate_mcp_tool_manifest.py

Writes ``docs/generated/mcp-tools-manifest.json`` (stable ordering for diffs).
Canonical **tool_count** / **resource_count** for docs and drift checks.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MCP_PATH = PROJECT_ROOT / "src" / "tapps_brain" / "mcp_server.py"
OUT_PATH = PROJECT_ROOT / "docs" / "generated" / "mcp-tools-manifest.json"


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
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": "src/tapps_brain/mcp_server.py",
        "tool_count": len(tools),
        "resource_count": len(resources),
        "tools": tools,
        "resources": resources,
    }
    OUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(tools)} tools, {len(resources)} resources)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
