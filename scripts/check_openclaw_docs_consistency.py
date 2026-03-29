#!/usr/bin/env python3
"""Fail on OpenClaw doc drift: install commands, tool/resource counts, stale claims.

Remediation: docs/guides/openclaw-runbook.md, EPIC-035/036, docs/planning/STATUS.md.
Run: python scripts/check_openclaw_docs_consistency.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Must match shipped MCP surface (see CLAUDE.md / mcp_server).
EXPECTED_TOOL_COUNT = 64
EXPECTED_RESOURCE_COUNT = 8

# User-facing OpenClaw paths to scan for banned / inconsistent patterns.
DOC_GLOBS = [
    "docs/guides/openclaw.md",
    "docs/guides/openclaw-install-from-git.md",
    "docs/guides/openclaw-runbook.md",
    "openclaw-plugin/README.md",
    "openclaw-plugin/UPGRADING.md",
    "openclaw-skill/SKILL.md",
]

BANNED_SUBSTRINGS = [
    (
        "openclaw plugins install",
        "Use singular: openclaw plugin install (see docs/guides/openclaw-runbook.md)",
    ),
]

# Stale capability headlines / claims (not historical epic titles elsewhere).
BANNED_REGEX = [
    (
        re.compile(r"##\s+All\s+41\s+MCP\s+Tools", re.IGNORECASE),
        "Update heading to current tool count (54); see openclaw-skill/SKILL.md",
    ),
    (
        re.compile(r"\bAll\s+41\s+MCP\s+Tools\b", re.IGNORECASE),
        "Replace stale '41 MCP tools' wording with current count or remove duplicate list",
    ),
]


def _skill_frontmatter() -> str:
    skill = PROJECT_ROOT / "openclaw-skill" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    match = re.search(r"^---\s*\r?\n(.*?)\r?\n---", text, re.DOTALL)
    if not match:
        msg = "openclaw-skill/SKILL.md: missing YAML frontmatter"
        raise SystemExit(msg)
    return match.group(1)


def _count_yaml_sequence_items(block: str, key: str) -> int:
    """Count list entries like '  - name:' or '  - uri:' under a top-level key."""
    if key == "tools":
        pattern = re.compile(r"^\s+-\s+name:\s", re.MULTILINE)
    elif key == "resources":
        pattern = re.compile(r"^\s+-\s+uri:\s", re.MULTILINE)
    else:
        msg = f"unknown key: {key}"
        raise ValueError(msg)
    return len(pattern.findall(block))


def _extract_section(frontmatter: str, start_key: str, end_key: str) -> str:
    """Slice frontmatter from `start_key:` through line before `end_key:`."""
    start_re = re.compile(rf"^{re.escape(start_key)}:\s*$", re.MULTILINE)
    end_re = re.compile(rf"^{re.escape(end_key)}:\s*$", re.MULTILINE)
    sm = start_re.search(frontmatter)
    if not sm:
        msg = f"SKILL.md frontmatter: missing {start_key}:"
        raise SystemExit(msg)
    em = end_re.search(frontmatter, sm.end())
    if not em:
        msg = f"SKILL.md frontmatter: missing {end_key}: after {start_key}"
        raise SystemExit(msg)
    return frontmatter[sm.end() : em.start()]


def _check_skill_counts() -> list[str]:
    errors: list[str] = []
    fm = _skill_frontmatter()
    tools_block = _extract_section(fm, "tools", "resources")
    resources_block = _extract_section(fm, "resources", "prompts")
    n_tools = _count_yaml_sequence_items(tools_block, "tools")
    n_res = _count_yaml_sequence_items(resources_block, "resources")
    if n_tools != EXPECTED_TOOL_COUNT:
        errors.append(
            f"SKILL.md tools: expected {EXPECTED_TOOL_COUNT} tools, found {n_tools}. "
            "Update EXPECTED_TOOL_COUNT in this script when MCP surface changes."
        )
    if n_res != EXPECTED_RESOURCE_COUNT:
        errors.append(
            f"SKILL.md resources: expected {EXPECTED_RESOURCE_COUNT} URIs, found {n_res}. "
            "Update EXPECTED_RESOURCE_COUNT in this script when MCP surface changes."
        )
    return errors


def _check_scanned_files() -> list[str]:
    errors: list[str] = []
    for rel in DOC_GLOBS:
        path = PROJECT_ROOT / rel
        if not path.is_file():
            errors.append(f"Missing expected doc: {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for banned, hint in BANNED_SUBSTRINGS:
            if banned in lowered:
                errors.append(f"{rel}: contains banned substring {banned!r}. {hint}")
        for pattern, hint in BANNED_REGEX:
            if pattern.search(text):
                errors.append(f"{rel}: matches stale pattern {pattern.pattern!r}. {hint}")
    return errors


def _check_runbook_exists() -> list[str]:
    errors: list[str] = []
    runbook = PROJECT_ROOT / "docs" / "guides" / "openclaw-runbook.md"
    if not runbook.is_file():
        errors.append("Canonical runbook missing: docs/guides/openclaw-runbook.md")
        return errors
    rb = runbook.read_text(encoding="utf-8")
    if "openclaw plugin install" not in rb:
        errors.append("openclaw-runbook.md must include: openclaw plugin install")
    if "tapps-brain[mcp]" not in rb:
        errors.append("openclaw-runbook.md must document pip install tapps-brain[mcp]")
    return errors


def main() -> int:
    errors: list[str] = []
    errors.extend(_check_runbook_exists())
    errors.extend(_check_skill_counts())
    errors.extend(_check_scanned_files())
    if errors:
        print("check_openclaw_docs_consistency: FAILED", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nRemediation: docs/guides/openclaw-runbook.md, openclaw-skill/SKILL.md, "
            "docs/planning/epics/EPIC-035.md",
            file=sys.stderr,
        )
        return 1
    print("check_openclaw_docs_consistency: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
