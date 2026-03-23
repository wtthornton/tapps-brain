"""Unit tests for profile-based memory seeding (tapps_brain.seeding)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from tapps_brain.seeding import (
    _SEEDED_FROM,
    _SEEDED_TAG,
    _slugify,
    reseed_from_profile,
    seed_from_profile,
)
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Stub profile
# ---------------------------------------------------------------------------


@dataclass
class _TechStack:
    languages: list[str] = field(default_factory=list)
    libraries: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


@dataclass
class _FakeProfile:
    project_type: str = ""
    project_type_confidence: float = 0.9
    tech_stack: _TechStack = field(default_factory=_TechStack)
    test_frameworks: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    ci_systems: list[str] = field(default_factory=list)
    has_docker: bool = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path):
    return MemoryStore(tmp_path)


def _full_profile():
    return _FakeProfile(
        project_type="python",
        project_type_confidence=0.95,
        tech_stack=_TechStack(
            languages=["Python"],
            frameworks=["FastAPI"],
        ),
        test_frameworks=["pytest"],
        package_managers=["uv"],
        ci_systems=["github-actions"],
        has_docker=True,
    )


# ---------------------------------------------------------------------------
# Tests: seed_from_profile
# ---------------------------------------------------------------------------


class TestSeedFromProfile:
    def test_seeds_empty_store(self, store):
        profile = _full_profile()
        result = seed_from_profile(store, profile)
        assert result["skipped"] is False
        # 1 project-type + 1 language + 1 framework + 1 test-fw + 1 pm + 1 ci + 1 docker
        assert result["seeded_count"] == 7
        assert store.count() == 7

    def test_skips_non_empty_store(self, store):
        store.save(key="existing", value="already here")
        result = seed_from_profile(store, _full_profile())
        assert result["skipped"] is True
        assert result["seeded_count"] == 0
        assert store.count() == 1

    def test_seeded_from_field_set(self, store):
        seed_from_profile(store, _full_profile())
        entry = store.get("project-type")
        assert entry is not None
        assert entry.seeded_from == _SEEDED_FROM

    def test_auto_seeded_tag_present(self, store):
        seed_from_profile(store, _full_profile())
        entry = store.get("project-type")
        assert entry is not None
        assert _SEEDED_TAG in entry.tags

    def test_confidence_from_profile(self, store):
        profile = _FakeProfile(project_type="python", project_type_confidence=0.99)
        seed_from_profile(store, profile)
        entry = store.get("project-type")
        assert entry is not None
        assert entry.confidence == 0.99

    def test_confidence_minimum_is_default(self, store):
        profile = _FakeProfile(project_type="python", project_type_confidence=0.5)
        seed_from_profile(store, profile)
        entry = store.get("project-type")
        assert entry is not None
        # max(0.9, 0.5) == 0.9
        assert entry.confidence == 0.9


# ---------------------------------------------------------------------------
# Tests: Missing / empty profile fields
# ---------------------------------------------------------------------------


class TestSeedPartialProfile:
    def test_empty_profile_seeds_nothing(self, store):
        profile = _FakeProfile()
        result = seed_from_profile(store, profile)
        assert result["seeded_count"] == 0
        assert result["skipped"] is False
        assert store.count() == 0

    def test_only_docker(self, store):
        profile = _FakeProfile(has_docker=True)
        result = seed_from_profile(store, profile)
        assert result["seeded_count"] == 1
        entry = store.get("has-docker")
        assert entry is not None
        assert "Docker" in entry.value

    def test_multiple_languages(self, store):
        profile = _FakeProfile(
            tech_stack=_TechStack(languages=["Python", "Rust"]),
        )
        result = seed_from_profile(store, profile)
        assert result["seeded_count"] == 2
        assert store.get("language-python") is not None
        assert store.get("language-rust") is not None

    def test_empty_string_items_skipped(self, store):
        """Empty strings in list fields should be ignored, not saved as malformed keys."""
        profile = _FakeProfile(
            tech_stack=_TechStack(languages=["", "Python", ""], frameworks=["", "Django"]),
            test_frameworks=[""],
            package_managers=[""],
            ci_systems=[""],
        )
        result = seed_from_profile(store, profile)
        # Only "Python" language + "Django" framework — all empty strings skipped
        assert result["seeded_count"] == 2
        assert store.get("language-") is None
        assert store.get("language-python") is not None
        assert store.get("framework-django") is not None
        assert store.get("framework-") is None


# ---------------------------------------------------------------------------
# Tests: reseed_from_profile
# ---------------------------------------------------------------------------


class TestReseedFromProfile:
    def test_reseed_replaces_auto_seeded(self, store):
        seed_from_profile(store, _full_profile())
        original_count = store.count()

        # Reseed with a different profile
        new_profile = _FakeProfile(project_type="rust", project_type_confidence=0.95)
        result = reseed_from_profile(store, new_profile)
        assert result["deleted_old"] == original_count
        assert result["seeded_count"] == 1
        entry = store.get("project-type")
        assert entry is not None
        assert "rust" in entry.value.lower()

    def test_reseed_preserves_human_entries(self, tmp_path):
        # Use a fresh store so seed_from_profile sees an empty store first
        s = MemoryStore(tmp_path / "reseed-preserve")
        seed_from_profile(s, _full_profile())
        count_after_seed = s.count()
        assert count_after_seed > 0

        # Now add a human entry
        s.save(key="human-note", value="Important note", source="human")

        # Reseed with empty profile: all auto-seeded entries removed, human kept
        reseed_from_profile(s, _FakeProfile())
        assert s.get("human-note") is not None
        assert s.count() == 1  # only human entry remains


# ---------------------------------------------------------------------------
# Tests: _slugify helper
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("FastAPI") == "fastapi"

    def test_spaces(self):
        assert _slugify("My Framework") == "my-framework"

    def test_underscores(self):
        assert _slugify("my_lib") == "my-lib"

    def test_mixed(self):
        assert _slugify("My_Lib Name") == "my-lib-name"
