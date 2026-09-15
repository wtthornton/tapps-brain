#!/usr/bin/env python3
"""Fail when a test file directly at `tests/` produced no `<testcase>` in the
JUnit XML from the CI "Integration + compat tests" step (TAP-6829).

`scripts/check_test_collection.py` proves the CI config *names* every
`tests/test_*.py` root file as a path argument to some pytest invocation. It
never invokes pytest, so it cannot tell a file that is actually collected
apart from one that is named in the command but silently never reaches the
collector (a stale glob, a conftest collection error swallowed elsewhere,
etc.) — under `-q`, that invocation prints no `collected N items` line and no
test ids, so nothing in the log would show the difference either.

This script closes that gap at runtime: it reads the JUnit XML the same
invocation now emits (`--junitxml=`) and checks that every root file has at
least one `<testcase>` — passed, failed, or skipped all count, since all
three mean the file was collected and its tests ran. A file that is missing
from the run entirely produces no `<testcase>` with a matching classname at
all, which is exactly the failure mode this guards against.

Scoped to `tests/` root only (not the whole tree), matching
`check_test_collection.py`'s scope — see that script's docstring for why.
Widening the scope is TAP-7660's job, not this one's.

Usage:
    python scripts/check_test_execution.py <path-to-junit.xml>

Exit codes:
    0 — every `tests/test_*.py` root file has at least one matching
        `<testcase>` in the JUnit XML
    1 — one or more root files have no matching `<testcase>` (names printed)
    2 — the JUnit XML could not be read or parsed at all (unknown must
        refuse, never silently pass)
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_ROOT = REPO_ROOT / "tests"
_EXPECTED_ARGC = 2


def _root_test_files() -> list[Path]:
    """Every `tests/test_*.py` file directly at `tests/` (non-recursive)."""
    return sorted(TESTS_ROOT.glob("test_*.py"))


def _classname_prefix(test_file: Path) -> str:
    """The JUnit `classname` (or its dotted prefix) pytest emits for a root file.

    pytest's default JUnit XML has no `file` attribute on `<testcase>` — only
    a dotted `classname` derived from the module path, e.g.
    `tests.test_maintenance_cycle`, with `.TestClassName` appended for
    class-based tests. A module-level test's classname equals the prefix
    exactly; a class-based test's classname starts with `prefix + "."`.
    """
    return f"tests.{test_file.stem}"


def _classnames_in(junit_xml: Path) -> set[str]:
    tree = ET.parse(junit_xml)
    root = tree.getroot()
    return {
        testcase.get("classname", "")
        for testcase in root.iter("testcase")
        if testcase.get("classname")
    }


def main(argv: list[str]) -> int:
    if len(argv) != _EXPECTED_ARGC:
        print("usage: check_test_execution.py <path-to-junit.xml>", file=sys.stderr)
        return 2

    junit_xml = Path(argv[1])
    try:
        classnames = _classnames_in(junit_xml)
    except (OSError, ET.ParseError) as exc:
        print(
            f"check_test_execution: cannot read JUnit XML at {junit_xml}: {exc}",
            file=sys.stderr,
        )
        return 2

    missing: list[Path] = []
    for test_file in _root_test_files():
        prefix = _classname_prefix(test_file)
        if not any(cn == prefix or cn.startswith(prefix + ".") for cn in classnames):
            missing.append(test_file)

    if missing:
        print(
            "check_test_execution: the following tests/ root test files have no "
            f"<testcase> in {junit_xml} — collected by CI config but never actually run:",
            file=sys.stderr,
        )
        for p in missing:
            print(f"  {p.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    root_files = _root_test_files()
    print(
        f"check_test_execution: all {len(root_files)} tests/*.py files have at least "
        f"one executed testcase in {junit_xml}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
