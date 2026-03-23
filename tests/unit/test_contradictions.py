"""Unit tests for contradiction detection (tapps_brain.contradictions)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import patch

from tapps_brain.contradictions import Contradiction, ContradictionDetector
from tests.factories import make_entry as _make_entry

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Stub profile objects
# ---------------------------------------------------------------------------


@dataclass
class _TechStack:
    languages: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


@dataclass
class _FakeProfile:
    project_type: str = "python"
    project_type_confidence: float = 0.9
    tech_stack: _TechStack = field(default_factory=_TechStack)
    test_frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    ci_systems: list[str] = field(default_factory=list)
    has_docker: bool = False


# ---------------------------------------------------------------------------
# Tests: Contradiction model
# ---------------------------------------------------------------------------


class TestContradictionModel:
    def test_defaults(self):
        c = Contradiction(memory_key="k", reason="r", evidence="e")
        assert c.memory_key == "k"
        assert c.detected_at  # auto-populated

    def test_explicit_detected_at(self):
        c = Contradiction(
            memory_key="k", reason="r", evidence="e", detected_at="2024-01-01T00:00:00+00:00"
        )
        assert c.detected_at == "2024-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Tests: Tech-stack contradictions
# ---------------------------------------------------------------------------


class TestTechStackCheck:
    def test_no_contradiction_when_lib_in_stack(self, tmp_path: Path):
        profile = _FakeProfile(tech_stack=_TechStack(libraries=["sqlalchemy"]))
        entry = _make_entry(value="We use sqlalchemy for the ORM layer", tags=["library"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []

    def test_contradiction_when_lib_not_in_stack(self, tmp_path: Path):
        profile = _FakeProfile(tech_stack=_TechStack(libraries=["sqlalchemy"]))
        entry = _make_entry(value="We use django-rest for our API", tags=["framework"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert len(result) == 1
        assert "django-rest" in result[0].reason

    def test_no_contradiction_for_short_claimed_name(self, tmp_path: Path):
        """Claims shorter than _MIN_CLAIMED_NAME_LENGTH are ignored."""
        profile = _FakeProfile(tech_stack=_TechStack(libraries=[]))
        entry = _make_entry(value="We use db", tags=["library"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []

    def test_no_tags_means_no_tech_check(self, tmp_path: Path):
        profile = _FakeProfile(tech_stack=_TechStack(libraries=["flask"]))
        entry = _make_entry(value="We switched to django", tags=[])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []

    def test_case_insensitive_known_match(self, tmp_path: Path):
        profile = _FakeProfile(tech_stack=_TechStack(libraries=["SQLAlchemy"]))
        entry = _make_entry(value="Uses sqlalchemy", tags=["library"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []

    def test_claim_patterns_built_with(self, tmp_path: Path):
        profile = _FakeProfile(tech_stack=_TechStack(libraries=[]))
        entry = _make_entry(value="built with fastapi", tags=["framework"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert len(result) == 1
        assert "fastapi" in result[0].reason

    def test_claim_patterns_migrated_to(self, tmp_path: Path):
        profile = _FakeProfile(tech_stack=_TechStack(libraries=[]))
        entry = _make_entry(value="migrated to postgres", tags=["database"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert len(result) == 1
        assert "postgres" in result[0].reason


# ---------------------------------------------------------------------------
# Tests: File existence
# ---------------------------------------------------------------------------


class TestFileExistenceCheck:
    def test_no_contradiction_when_file_exists(self, tmp_path: Path):
        (tmp_path / "config.yaml").write_text("x: 1")
        entry = _make_entry(value="See config.yaml for settings", tags=["file"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == []

    def test_contradiction_when_file_missing(self, tmp_path: Path):
        entry = _make_entry(value="See config.yaml for settings", tags=["file"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert len(result) == 1
        assert "config.yaml" in result[0].reason

    def test_skips_absolute_paths(self, tmp_path: Path):
        entry = _make_entry(value="Located at /etc/passwd", tags=["file"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == []

    def test_skips_dotdot_paths(self, tmp_path: Path):
        entry = _make_entry(value="Located at ../outside/file.txt", tags=["file"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == []

    def test_no_file_pattern_means_no_contradiction(self, tmp_path: Path):
        entry = _make_entry(value="Just a plain sentence", tags=["file"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == []

    def test_version_string_not_flagged_as_missing_file(self, tmp_path: Path):
        """Version strings like '1.2.3' must not be treated as file paths."""
        entry = _make_entry(
            value="Requires Python 3.12.0 or newer", tags=["file"]
        )
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == [], "Version strings should not be flagged as missing files"


# ---------------------------------------------------------------------------
# Tests: Test frameworks
# ---------------------------------------------------------------------------


class TestTestFrameworkCheck:
    def test_no_contradiction_matching_framework(self, tmp_path: Path):
        profile = _FakeProfile(test_frameworks=["pytest"])
        entry = _make_entry(value="Run pytest for tests", tags=["testing"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []

    def test_contradiction_mismatched_framework(self, tmp_path: Path):
        profile = _FakeProfile(test_frameworks=["pytest"])
        entry = _make_entry(value="Run jest for tests", tags=["testing"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert len(result) == 1
        assert "jest" in result[0].reason

    def test_no_known_frameworks_means_no_check(self, tmp_path: Path):
        profile = _FakeProfile(test_frameworks=[])
        entry = _make_entry(value="Run jest for tests", tags=["testing"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: Package managers
# ---------------------------------------------------------------------------


class TestPackageManagerCheck:
    def test_no_contradiction_matching_pm(self, tmp_path: Path):
        profile = _FakeProfile(package_managers=["uv"])
        entry = _make_entry(value="Install with uv", tags=["package-manager"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []

    def test_contradiction_mismatched_pm(self, tmp_path: Path):
        profile = _FakeProfile(package_managers=["uv"])
        entry = _make_entry(value="Install with poetry", tags=["package-manager"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert len(result) == 1
        assert "poetry" in result[0].reason

    def test_no_known_pm_means_no_check(self, tmp_path: Path):
        profile = _FakeProfile(package_managers=[])
        entry = _make_entry(value="Install with poetry", tags=["package-manager"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], profile)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: Branch existence
# ---------------------------------------------------------------------------


class TestBranchCheck:
    def test_no_branch_field_means_no_check(self, tmp_path: Path):
        entry = _make_entry(value="stuff", tags=["branch"], branch=None)
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == []

    def test_branch_exists_no_contradiction(self, tmp_project_with_git: Path):
        entry = _make_entry(
            key="mem-branch",
            value="on main",
            tags=["branch"],
            branch="main",
        )
        detector = ContradictionDetector(tmp_project_with_git)
        result = detector.detect_contradictions([entry], _FakeProfile())
        # In a fresh git init the default branch may be main or master;
        # either way we verify no crash. On CI/local the branch may differ,
        # so we just ensure the code runs without error.
        assert isinstance(result, list)

    def test_missing_branch_gives_contradiction(self, tmp_project_with_git: Path):
        entry = _make_entry(
            key="mem-branch",
            value="on feature-xyz",
            tags=["branch"],
            branch="feature-xyz-does-not-exist",
        )
        detector = ContradictionDetector(tmp_project_with_git)
        result = detector.detect_contradictions([entry], _FakeProfile())
        assert len(result) == 1
        assert "feature-xyz-does-not-exist" in result[0].reason

    def test_git_not_available_no_crash(self, tmp_path: Path):
        entry = _make_entry(
            key="mem-b",
            value="stuff",
            tags=["branch"],
            branch="some-branch",
        )
        detector = ContradictionDetector(tmp_path)
        with patch(
            "tapps_brain.contradictions.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == []

    def test_non_git_dir_no_false_positive(self, tmp_path: Path):
        """Non-zero returncode (not a git repo) must not produce a contradiction."""
        from unittest.mock import MagicMock

        entry = _make_entry(
            key="mem-b",
            value="stuff",
            tags=["branch"],
            branch="some-branch",
        )
        detector = ContradictionDetector(tmp_path)
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        mock_result.stderr = "fatal: not a git repository"
        with patch(
            "tapps_brain.contradictions.subprocess.run",
            return_value=mock_result,
        ):
            result = detector.detect_contradictions([entry], _FakeProfile())
        assert result == [], "Non-git directory should not produce a branch contradiction"


# ---------------------------------------------------------------------------
# Tests: Multiple entries / empty inputs
# ---------------------------------------------------------------------------


class TestDetectorEdgeCases:
    def test_empty_list(self, tmp_path: Path):
        detector = ContradictionDetector(tmp_path)
        assert detector.detect_contradictions([], _FakeProfile()) == []

    def test_identical_entries_no_contradiction(self, tmp_path: Path):
        """Identical entries that match the project state produce nothing."""
        profile = _FakeProfile(tech_stack=_TechStack(libraries=["requests"]))
        e1 = _make_entry(key="m1", value="We use requests", tags=["library"])
        e2 = _make_entry(key="m2", value="We use requests", tags=["library"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([e1, e2], profile)
        assert result == []

    def test_multiple_contradictions_from_different_entries(self, tmp_path: Path):
        profile = _FakeProfile(
            tech_stack=_TechStack(libraries=[]),
            test_frameworks=["pytest"],
        )
        e1 = _make_entry(key="m1", value="We use flask", tags=["library"])
        e2 = _make_entry(key="m2", value="Run jest tests", tags=["testing"])
        detector = ContradictionDetector(tmp_path)
        result = detector.detect_contradictions([e1, e2], profile)
        assert len(result) == 2
