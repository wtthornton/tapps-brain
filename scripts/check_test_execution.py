#!/usr/bin/env python3
"""Fail when a `test_*.py` file under `tests/` produced no `<testcase>` in
the JUnit XML from the CI "Integration + compat tests" step (TAP-6829,
widened by TAP-7660).

`scripts/check_test_collection.py` proves the CI config *names* every
`tests/**/test_*.py` file as a path argument to some pytest invocation. It
never invokes pytest, so it cannot tell a file that is actually collected
apart from one that is named in the command but silently never reaches the
collector (a stale glob, a conftest collection error swallowed elsewhere,
etc.) — under `-q`, that invocation prints no `collected N items` line and no
test ids, so nothing in the log would show the difference either.

This script closes that gap at runtime: it reads the JUnit XML the same
invocation now emits (`--junitxml=`) and checks that every test file has at
least one `<testcase>` — passed, failed, or skipped all count, since all
three mean the file was collected and its tests ran. A file that is missing
from the run entirely produces no `<testcase>` with a matching classname at
all, which is exactly the failure mode this guards against.

Originally scoped to `tests/` root only; TAP-7660 widened the walk to every
subdirectory, matching `check_test_collection.py`'s `EXCLUDED_FILES` list —
see that script's docstring for why exclusions are per-file, not per-directory.

Accepts one JUnit XML per CI step that can produce one: CI runs
`tests/regression/test_brain_recall_shape.py` in its own step (a literal
file, not the whole directory — see the comment on that step in ci.yml) with
its own `--junitxml=`, distinct from the integration+compat step's XML. A
`<testcase>` matching a given file need only appear in *one* of the supplied
files.

Usage:
    python scripts/check_test_execution.py <path-to-junit.xml> [more.xml ...]

Exit codes:
    0 — every `tests/**/test_*.py` file (outside EXCLUDED_FILES) has at
        least one matching `<testcase>` across the supplied JUnit XML files
    1 — one or more files have no matching `<testcase>` (names printed)
    2 — no JUnit XML path given, or one could not be read/parsed at all
        (unknown must refuse, never silently pass)
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from check_test_collection import EXCLUDED_FILES

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"
_MIN_ARGC = 2


def _test_files() -> list[Path]:
    """Every `tests/**/test_*.py` file, excluding EXCLUDED_FILES."""
    excluded = {(REPO_ROOT / rel).resolve() for rel in EXCLUDED_FILES}
    return sorted(p for p in TESTS_ROOT.rglob("test_*.py") if p.resolve() not in excluded)


def _classname_prefix(test_file: Path) -> str:
    """The JUnit `classname` (or its dotted prefix) pytest emits for a test file.

    pytest's default JUnit XML has no `file` attribute on `<testcase>` — only
    a dotted `classname` derived from the module path relative to the repo
    root, e.g. `tests.test_maintenance_cycle` or
    `tests.regression.test_brain_recall_shape`, with `.TestClassName`
    appended for class-based tests. A module-level test's classname equals
    the prefix exactly; a class-based test's classname starts with
    `prefix + "."`.
    """
    rel = test_file.resolve().relative_to(REPO_ROOT.resolve())
    return ".".join(rel.with_suffix("").parts)


def _classnames_in(junit_xml: Path) -> set[str]:
    tree = ET.parse(junit_xml)
    root = tree.getroot()
    return {
        testcase.get("classname", "")
        for testcase in root.iter("testcase")
        if testcase.get("classname")
    }


def _print_exclusion_list() -> None:
    print("check_test_execution: excluded files (each with a written reason):")
    for path, reason in sorted(EXCLUDED_FILES.items()):
        print(f"  {path} — {reason}")


def main(argv: list[str]) -> int:
    if len(argv) < _MIN_ARGC:
        print(
            "usage: check_test_execution.py <path-to-junit.xml> [more.xml ...]",
            file=sys.stderr,
        )
        return 2

    junit_paths = [Path(a) for a in argv[1:]]
    classnames: set[str] = set()
    for junit_xml in junit_paths:
        try:
            classnames |= _classnames_in(junit_xml)
        except (OSError, ET.ParseError) as exc:
            print(
                f"check_test_execution: cannot read JUnit XML at {junit_xml}: {exc}",
                file=sys.stderr,
            )
            return 2

    _print_exclusion_list()

    test_files = _test_files()
    missing: list[Path] = []
    for test_file in test_files:
        prefix = _classname_prefix(test_file)
        if not any(cn == prefix or cn.startswith(prefix + ".") for cn in classnames):
            missing.append(test_file)

    joined = ", ".join(str(p) for p in junit_paths)
    if missing:
        print(
            "check_test_execution: the following tests/ test files have no "
            f"<testcase> in [{joined}] — collected by CI config but never actually run:",
            file=sys.stderr,
        )
        for p in missing:
            print(f"  {p.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    print(
        f"check_test_execution: all {len(test_files)} tests/**/test_*.py files have at "
        f"least one executed testcase across [{joined}]."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
