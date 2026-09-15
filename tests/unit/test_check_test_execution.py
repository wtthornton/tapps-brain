"""Unit tests for scripts/check_test_execution.py (TAP-7660).

TAP-7660 widened the execution guard from `tests/` root only to every
`test_*.py` under `tests/`, accepting multiple JUnit XML files since the
regression file (`tests/regression/test_brain_recall_shape.py`) runs in its
own CI step with its own `--junitxml=`, separate from the integration+compat
step's XML. These tests build a synthetic `tests/` tree under `tmp_path` so
they never touch the real repo's test files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import check_test_execution as cte

_JUNIT_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest">
    {testcases}
  </testsuite>
</testsuites>
"""


def _junit(tmp_path: Path, name: str, classnames: list[str]) -> Path:
    testcases = "\n".join(
        f'<testcase classname="{cn}" name="test_it" time="0.0" />' for cn in classnames
    )
    path = tmp_path / name
    path.write_text(_JUNIT_TEMPLATE.format(testcases=testcases), encoding="utf-8")
    return path


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_root.py").write_text("def test_root(): pass\n")
    regression_dir = tests_root / "regression"
    regression_dir.mkdir()
    (regression_dir / "test_brain_recall_shape.py").write_text("def test_shape(): pass\n")
    monkeypatch.setattr(cte, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cte, "TESTS_ROOT", tests_root)
    monkeypatch.setattr(cte, "EXCLUDED_FILES", {})
    return tmp_path


class TestClassnamePrefix:
    def test_root_file_prefix(self, fake_repo: Path) -> None:
        test_file = fake_repo / "tests" / "test_root.py"
        assert cte._classname_prefix(test_file) == "tests.test_root"

    def test_subdirectory_file_prefix(self, fake_repo: Path) -> None:
        test_file = fake_repo / "tests" / "regression" / "test_brain_recall_shape.py"
        assert cte._classname_prefix(test_file) == "tests.regression.test_brain_recall_shape"


class TestMultipleJunitFiles:
    def test_passes_when_files_split_across_two_junit_reports(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        junit_a = _junit(tmp_path, "a.xml", ["tests.test_root"])
        junit_b = _junit(tmp_path, "b.xml", ["tests.regression.test_brain_recall_shape"])
        assert cte.main(["prog", str(junit_a), str(junit_b)]) == 0

    def test_fails_when_a_file_is_in_neither_junit_report(
        self, fake_repo: Path, tmp_path: Path
    ) -> None:
        junit_a = _junit(tmp_path, "a.xml", ["tests.test_root"])
        junit_b = _junit(tmp_path, "b.xml", [])
        assert cte.main(["prog", str(junit_a), str(junit_b)]) == 1


class TestRefusesOnBadInput:
    def test_no_junit_args_exits_2(self, fake_repo: Path) -> None:
        assert cte.main(["prog"]) == 2

    def test_unreadable_junit_exits_2(self, fake_repo: Path, tmp_path: Path) -> None:
        assert cte.main(["prog", str(tmp_path / "does-not-exist.xml")]) == 2
