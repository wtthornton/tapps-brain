#!/usr/bin/env python3
"""Fail when a test file directly at `tests/` is not collected by any CI
pytest invocation (TAP-6829).

Before this script existed, files placed directly at `tests/` (as opposed to
`tests/unit/`, `tests/integration/`, or `tests/compat/`) were never collected
by CI: `pyproject.toml`'s `testpaths = ["tests"]` only applies when pytest is
given no path argument, and every CI invocation gives one. Ten such files
accumulated undetected, several guarding the strict-tenancy contract
(RLS/isolation/migration suites).

Scoped to `tests/` root only (not the whole tree): `tests/benchmarks/` is a
deliberate, separately-documented exclusion (CI's 2026-04-27 cost-discipline
pass — see the bottom of ci.yml), and this ticket does not extend the CI
surface any further than the root-file gap it fixes.

This reads the actual `run:` steps in `.github/workflows/ci.yml` — not a
hardcoded copy of them — so it re-derives the covered set from the CI
config that will actually execute, and cannot drift out of sync with it.

Usage:
    python scripts/check_test_collection.py

Exit codes:
    0 — every `tests/test_*.py` file is named by a path/glob argument to at
        least one `pytest` invocation in ci.yml
    1 — one or more files are collected by none of them (names are printed)
"""

from __future__ import annotations

import glob
import shlex
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _pytest_invocations(workflow: dict) -> list[str]:
    """Return every `run:` step string that invokes `pytest`, across all jobs."""
    invocations = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []):
            run = step.get("run")
            if run and "pytest" in run:
                invocations.append(run)
    return invocations


def _is_shlexable(line: str) -> bool:
    try:
        shlex.split(line)
    except ValueError:
        return False
    return True


def _path_args(invocation: str) -> list[str]:
    """Extract the `tests/...` path/glob tokens from one `run:` block.

    Skips flag tokens (`-q`, `--tb=short`, ...) and the `-m "not benchmark"`
    marker-expression pair; anything left that starts with `tests` is a path
    or glob argument pytest will collect from.
    """
    tokens: list[str] = []
    for line in invocation.splitlines():
        line = line.strip()
        if not line or line == "pytest" or not _is_shlexable(line):
            continue
        tokens.extend(shlex.split(line))

    paths = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in ("-m", "-k"):
            skip_next = True
            continue
        if tok.startswith("-"):
            continue
        if tok.startswith("tests"):
            paths.append(tok)
    return paths


def _collected_files(paths: list[str]) -> set[Path]:
    """Expand each path/glob argument to the concrete `test_*.py` files it collects."""
    collected: set[Path] = set()
    for path in paths:
        matches = sorted(glob.glob(str(REPO_ROOT / path)))
        if not matches and not glob.has_magic(path):
            matches = [str(REPO_ROOT / path)]
        for match in matches:
            p = Path(match)
            if p.is_file() and p.name.startswith("test_") and p.suffix == ".py":
                collected.add(p.resolve())
            elif p.is_dir():
                collected.update(f.resolve() for f in p.rglob("test_*.py"))
    return collected


def main() -> int:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    invocations = _pytest_invocations(workflow)
    if not invocations:
        print(
            f"check_test_collection: no `pytest` invocation found in {CI_WORKFLOW}", file=sys.stderr
        )
        return 1

    all_paths: list[str] = []
    for inv in invocations:
        all_paths.extend(_path_args(inv))

    collected = _collected_files(all_paths)
    on_disk = {p.resolve() for p in (REPO_ROOT / "tests").glob("test_*.py")}

    uncollected = sorted(p.relative_to(REPO_ROOT) for p in (on_disk - collected))
    if uncollected:
        print(
            "check_test_collection: the following tests/ root test files are not "
            "collected by any pytest invocation in .github/workflows/ci.yml:",
            file=sys.stderr,
        )
        for p in uncollected:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"check_test_collection: all {len(on_disk)} tests/*.py files are collected by CI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
