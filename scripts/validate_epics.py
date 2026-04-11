#!/usr/bin/env python3
"""Validate YAML frontmatter in epic planning docs.

Usage:
    python scripts/validate_epics.py [PATH ...]

    # Validate all v3 epics (059–063):
    python scripts/validate_epics.py docs/planning/epics/EPIC-059.md \
        docs/planning/epics/EPIC-060.md docs/planning/epics/EPIC-061.md \
        docs/planning/epics/EPIC-062.md docs/planning/epics/EPIC-063.md

    # Validate every epic in the directory:
    python scripts/validate_epics.py docs/planning/epics/

Exit codes:
    0 — all files valid
    1 — one or more files failed validation
    2 — unexpected error (missing file, parse error)

Required frontmatter fields:
    id, title, status, priority, created, tags, depends_on, blocks

Valid status values:  planned, in-progress, complete
Valid priority values: critical, high, medium, low

This script is intentionally dependency-free (stdlib only) so it runs in any
Python 3.12+ environment without installing tapps-brain extras.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ─── constants ───────────────────────────────────────────────────────────────

REQUIRED_FIELDS: list[str] = [
    "id",
    "title",
    "status",
    "priority",
    "created",
    "tags",
    "depends_on",
    "blocks",
]

VALID_STATUSES: set[str] = {"planned", "in-progress", "complete"}
VALID_PRIORITIES: set[str] = {"critical", "high", "medium", "low"}

# Matches a date like 2026-04-10
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Matches an EPIC id like EPIC-059
_EPIC_ID_RE = re.compile(r"^EPIC-\d{3,}$")


# ─── frontmatter parser ───────────────────────────────────────────────────────

def _parse_frontmatter(text: str) -> dict[str, object] | None:
    """Extract YAML frontmatter between the first pair of ``---`` fences.

    Returns a flat dict with string values (no recursive YAML parsing beyond
    simple key: value and list [a, b, c] or block-style lines).
    Returns None when no frontmatter block is found.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    # Find closing ---
    close_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        return None

    fm_lines = lines[1:close_idx]

    result: dict[str, object] = {}
    for line in fm_lines:
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        # Inline list: [a, b, c] or []
        if raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1].strip()
            if not inner:
                result[key] = []
            else:
                result[key] = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
        else:
            # Bare string — strip optional quotes
            result[key] = raw_value.strip('"').strip("'")

    return result


# ─── validation ───────────────────────────────────────────────────────────────

def validate_file(path: Path) -> list[str]:
    """Return a list of error messages for *path* (empty = valid)."""
    errors: list[str] = []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"cannot read file: {exc}"]

    fm = _parse_frontmatter(text)
    if fm is None:
        return ["no YAML frontmatter found (expected --- block at top of file)"]

    # Required fields present
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"missing required field: '{field}'")

    # id format
    if "id" in fm:
        id_val = str(fm["id"])
        if not _EPIC_ID_RE.match(id_val):
            errors.append(f"'id' must match EPIC-NNN format, got: {id_val!r}")
        else:
            # id should match stem of filename (e.g. EPIC-062)
            expected_stem = path.stem  # "EPIC-062"
            if id_val != expected_stem:
                errors.append(
                    f"'id' value {id_val!r} does not match filename stem {expected_stem!r}"
                )

    # title non-empty
    if "title" in fm and not str(fm["title"]).strip():
        errors.append("'title' must not be empty")

    # status
    if "status" in fm:
        status_val = str(fm["status"]).lower()
        if status_val not in VALID_STATUSES:
            errors.append(
                f"'status' must be one of {sorted(VALID_STATUSES)}, got: {status_val!r}"
            )

    # priority
    if "priority" in fm:
        priority_val = str(fm["priority"]).lower()
        if priority_val not in VALID_PRIORITIES:
            errors.append(
                f"'priority' must be one of {sorted(VALID_PRIORITIES)}, got: {priority_val!r}"
            )

    # created date
    if "created" in fm:
        created_val = str(fm["created"])
        if not _DATE_RE.match(created_val):
            errors.append(f"'created' must be YYYY-MM-DD, got: {created_val!r}")

    return errors


# ─── entry point ─────────────────────────────────────────────────────────────

def _collect_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        p = Path(arg)
        if p.is_dir():
            paths.extend(sorted(p.glob("EPIC-*.md")))
        else:
            paths.append(p)
    return paths


def main(argv: list[str] | None = None) -> int:
    args = (argv if argv is not None else sys.argv[1:]) or []
    if not args:
        # Default: validate all v3 epics
        default_dir = Path(__file__).resolve().parent.parent / "docs" / "planning" / "epics"
        paths = sorted(default_dir.glob("EPIC-05[9].md")) + sorted(default_dir.glob("EPIC-06[0-4].md"))
        if not paths:
            print("validate_epics: no epic files found in default location", file=sys.stderr)
            return 2
    else:
        try:
            paths = _collect_paths(args)
        except Exception as exc:
            print(f"validate_epics: error collecting paths: {exc}", file=sys.stderr)
            return 2

    if not paths:
        print("validate_epics: no files to validate", file=sys.stderr)
        return 2

    failed = 0
    for path in paths:
        errors = validate_file(path)
        if errors:
            failed += 1
            print(f"FAIL  {path}")
            for err in errors:
                print(f"      {err}")
        else:
            print(f"OK    {path}")

    print()
    if failed:
        print(f"validate_epics: {failed}/{len(paths)} file(s) failed validation")
        return 1
    else:
        print(f"validate_epics: all {len(paths)} file(s) valid")
        return 0


if __name__ == "__main__":
    sys.exit(main())
