#!/usr/bin/env python3
"""Fail when a `test_*.py` file anywhere under `tests/` is not collected by
any CI pytest invocation (TAP-6829, widened by TAP-7660).

Originally scoped to `tests/` root only: files placed directly at `tests/`
(as opposed to `tests/unit/`, `tests/integration/`, or `tests/compat/`) were
never collected by CI — `pyproject.toml`'s `testpaths = ["tests"]` only
applies when pytest is given no path argument, and every CI invocation gives
one. Ten such files accumulated undetected, several guarding the
strict-tenancy contract (RLS/isolation/migration suites).

TAP-7660 widened the walk to every subdirectory of `tests/`, not just its
root: a file placed in a subdirectory that no CI invocation happens to name
was exactly as invisible as a root file, and one such file
(`tests/regression/test_brain_recall_shape.py`) had accumulated. A directory
is never wholesale-excluded here — `tests/benchmarks/` files are excluded
individually, by name, each with a written reason, in `EXCLUDED_FILES`
below. This guard prints that exclusion list on every run so a reader always
sees what is deliberately not checked.

This reads the actual `run:` steps in `.github/workflows/ci.yml` — not a
hardcoded copy of them — so it re-derives the covered set from the CI
config that will actually execute, and cannot drift out of sync with it.

Usage:
    python scripts/check_test_collection.py

Exit codes:
    0 — every `tests/**/test_*.py` file (outside EXCLUDED_FILES) is named by
        a path/glob argument to at least one `pytest` invocation in ci.yml
    1 — one or more files are collected by none of them (names are printed)
    2 — `.github/workflows/ci.yml` could not be read or parsed at all
"""

from __future__ import annotations

import glob
import shlex
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Files under tests/ that are deliberately not collected by any CI pytest
# invocation. Every entry needs a written reason — this list is itself a
# suppression surface, so it stays a named, reasoned exception list rather
# than a wholesale directory exclusion (TAP-7660).
EXCLUDED_FILES: dict[str, str] = {
    "tests/benchmarks/test_benchmark_adapters.py": (
        "benchmark job removed in the 2026-04-27 cost-discipline pass "
        "(ci.yml:5-8); run locally via "
        "`uv run pytest tests/benchmarks/ -v --benchmark-only`"
    ),
    "tests/benchmarks/test_benchmarks.py": (
        "benchmark job removed in the 2026-04-27 cost-discipline pass "
        "(ci.yml:5-8); run locally via "
        "`uv run pytest tests/benchmarks/ -v --benchmark-only`"
    ),
    "tests/benchmarks/test_decay_perf.py": (
        "benchmark job removed in the 2026-04-27 cost-discipline pass "
        "(ci.yml:5-8); run locally via "
        "`uv run pytest tests/benchmarks/ -v --benchmark-only`"
    ),
    "tests/benchmarks/test_http_adapter_tools_list.py": (
        "benchmark job removed in the 2026-04-27 cost-discipline pass "
        "(ci.yml:5-8); run locally via "
        "`uv run pytest tests/benchmarks/ -v --benchmark-only`"
    ),
}


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


def _print_exclusion_list() -> None:
    print("check_test_collection: excluded files (each with a written reason):")
    for path, reason in sorted(EXCLUDED_FILES.items()):
        print(f"  {path} — {reason}")


def main() -> int:
    try:
        workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(
            f"check_test_collection: cannot read {CI_WORKFLOW}: {exc}",
            file=sys.stderr,
        )
        return 2

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
    on_disk = {p.resolve() for p in (REPO_ROOT / "tests").rglob("test_*.py")}
    excluded = {(REPO_ROOT / rel).resolve() for rel in EXCLUDED_FILES}

    _print_exclusion_list()

    uncollected = sorted(p.relative_to(REPO_ROOT) for p in (on_disk - collected - excluded))
    if uncollected:
        print(
            "check_test_collection: the following tests/ test files are not "
            "collected by any pytest invocation in .github/workflows/ci.yml:",
            file=sys.stderr,
        )
        for p in uncollected:
            print(f"  {p}", file=sys.stderr)
        return 1

    checked = len(on_disk) - len(excluded & on_disk)
    print(f"check_test_collection: all {checked} tests/**/test_*.py files are collected by CI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
