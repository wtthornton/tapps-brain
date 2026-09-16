"""Unit tests for scripts/check_test_collection.py (TAP-7660).

TAP-7660 widened the collection guard from `tests/` root only to every
`test_*.py` under `tests/`, with a per-file, per-reason exclusion list for
the benchmark suite. These tests pin: the exclusion list stays honest
(files exist, reasons are non-empty, entries are files not directories), the
guard still passes against the real repo, it refuses (exit 2) rather than
silently passing when the CI workflow is unreadable, and a throwaway file
dropped anywhere under `tests/` — including a brand-new subdirectory — is
still caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import check_test_collection as ctc


class TestExclusionList:
    """The exclusion list is itself a suppression surface — keep it honest."""

    def test_every_excluded_file_has_a_written_reason(self) -> None:
        for path, reason in ctc.EXCLUDED_FILES.items():
            assert reason.strip(), f"{path} has an empty/blank reason"

    def test_every_excluded_file_exists_on_disk(self) -> None:
        for path in ctc.EXCLUDED_FILES:
            assert (ctc.REPO_ROOT / path).is_file(), f"{path} no longer exists — stale exclusion"

    def test_exclusions_are_files_not_directories(self) -> None:
        for path in ctc.EXCLUDED_FILES:
            assert path.endswith(".py") and "*" not in path, (
                f"{path} must be a single file exclusion, never a directory prefix"
            )


class TestMainAgainstRealWorkflow:
    def test_passes_against_current_repo(self) -> None:
        assert ctc.main() == 0


class TestUnreadableWorkflowRefuses:
    """VAL-03: unreadable input must exit 2, never 0."""

    def test_missing_workflow_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ctc, "CI_WORKFLOW", ctc.REPO_ROOT / ".github" / "workflows" / "does-not-exist.yml"
        )
        assert ctc.main() == 2

    def test_mutation_control_catches_a_silent_pass_regression(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If `main()` ever regressed to returning 0 on unreadable input, this
        assertion — not a human reading a log — is what would go red."""
        monkeypatch.setattr(
            ctc, "CI_WORKFLOW", ctc.REPO_ROOT / ".github" / "workflows" / "does-not-exist.yml"
        )
        result = ctc.main()
        assert result != 0, "must refuse (non-zero) on an unreadable workflow, never silently pass"
        assert result == 2


class TestNegativeAndPositiveControls:
    """b3/b4: a file anywhere uncollected under tests/ fails; a clean tree passes."""

    def test_uncollected_file_in_existing_subdir_fails(self) -> None:
        probe = ctc.REPO_ROOT / "tests" / "regression" / "test_zz_val02_probe.py"
        probe.write_text("def test_zz() -> None:\n    pass\n")
        try:
            assert ctc.main() == 1
        finally:
            probe.unlink()

    def test_uncollected_file_in_brand_new_subdir_fails(self) -> None:
        probe_dir = ctc.REPO_ROOT / "tests" / "zz_probe_unit_test"
        probe_dir.mkdir()
        probe = probe_dir / "test_zz_probe.py"
        probe.write_text("def test_zz() -> None:\n    pass\n")
        try:
            assert ctc.main() == 1
        finally:
            probe.unlink()
            probe_dir.rmdir()

    def test_clean_tree_passes(self) -> None:
        assert ctc.main() == 0
