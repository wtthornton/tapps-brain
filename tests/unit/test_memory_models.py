"""Unit tests for memory models (Epic 23, Story 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tapps_brain.models import (
    MAX_KEY_LENGTH,
    MAX_TAGS,
    MAX_VALUE_LENGTH,
    MemoryEntry,
    MemoryScope,
    MemorySnapshot,
    MemorySource,
    MemoryTier,
)


class TestMemoryTier:
    """Tests for MemoryTier enum."""

    def test_values(self) -> None:
        assert MemoryTier.architectural == "architectural"
        assert MemoryTier.pattern == "pattern"
        assert MemoryTier.procedural == "procedural"  # Epic 65.11
        assert MemoryTier.context == "context"

    def test_member_count(self) -> None:
        assert len(MemoryTier) == 4


class TestMemorySource:
    """Tests for MemorySource enum."""

    def test_values(self) -> None:
        assert MemorySource.human == "human"
        assert MemorySource.agent == "agent"
        assert MemorySource.inferred == "inferred"
        assert MemorySource.system == "system"

    def test_member_count(self) -> None:
        assert len(MemorySource) == 4


class TestMemoryScope:
    """Tests for MemoryScope enum."""

    def test_values(self) -> None:
        assert MemoryScope.project == "project"
        assert MemoryScope.branch == "branch"
        assert MemoryScope.session == "session"
        assert MemoryScope.shared == "shared"

    def test_member_count(self) -> None:
        assert len(MemoryScope) == 4


class TestMemoryEntry:
    """Tests for MemoryEntry model."""

    def test_minimal_creation(self) -> None:
        entry = MemoryEntry(key="test-key", value="Some value")
        assert entry.key == "test-key"
        assert entry.value == "Some value"
        assert entry.tier == MemoryTier.pattern
        assert entry.source == MemorySource.agent
        assert entry.scope == MemoryScope.project
        assert entry.confidence == 0.6  # agent default
        assert entry.access_count == 0
        assert entry.tags == []
        assert entry.branch is None
        assert entry.created_at
        assert entry.updated_at
        assert entry.last_accessed

    def test_source_confidence_defaults(self) -> None:
        human = MemoryEntry(key="k1", value="v", source=MemorySource.human)
        assert human.confidence == 0.95

        agent = MemoryEntry(key="k2", value="v", source=MemorySource.agent)
        assert agent.confidence == 0.6

        inferred = MemoryEntry(key="k3", value="v", source=MemorySource.inferred)
        assert inferred.confidence == 0.4

        system = MemoryEntry(key="k4", value="v", source=MemorySource.system)
        assert system.confidence == 0.9

    def test_explicit_confidence_overrides_default(self) -> None:
        entry = MemoryEntry(
            key="test-key", value="v", source=MemorySource.human, confidence=0.5
        )
        assert entry.confidence == 0.5

    def test_key_valid_slug(self) -> None:
        # Valid keys
        MemoryEntry(key="a", value="v")
        MemoryEntry(key="test-key", value="v")
        MemoryEntry(key="test.key", value="v")
        MemoryEntry(key="test_key", value="v")
        MemoryEntry(key="0-start-with-digit", value="v")

    def test_key_invalid_uppercase(self) -> None:
        with pytest.raises(ValidationError, match="slug"):
            MemoryEntry(key="UPPER", value="v")

    def test_key_invalid_starts_with_special(self) -> None:
        with pytest.raises(ValidationError, match="slug"):
            MemoryEntry(key="-starts-with-dash", value="v")

    def test_key_invalid_empty(self) -> None:
        with pytest.raises(ValidationError, match="slug"):
            MemoryEntry(key="", value="v")

    def test_key_max_length(self) -> None:
        # Exactly at limit is fine
        long_key = "a" * MAX_KEY_LENGTH
        MemoryEntry(key=long_key, value="v")

        # One over limit fails
        with pytest.raises(ValidationError, match="slug"):
            MemoryEntry(key="a" * (MAX_KEY_LENGTH + 1), value="v")

    def test_value_max_length(self) -> None:
        # Exactly at limit is fine
        MemoryEntry(key="k", value="x" * MAX_VALUE_LENGTH)

        # One over limit fails
        with pytest.raises(ValidationError, match="max length"):
            MemoryEntry(key="k", value="x" * (MAX_VALUE_LENGTH + 1))

    def test_value_empty_rejected(self) -> None:
        with pytest.raises(ValidationError, match="empty"):
            MemoryEntry(key="k", value="   ")

    def test_tags_max_count(self) -> None:
        # Exactly at limit
        MemoryEntry(key="k", value="v", tags=["t"] * MAX_TAGS)

        # One over limit
        with pytest.raises(ValidationError, match="Too many tags"):
            MemoryEntry(key="k", value="v", tags=["t"] * (MAX_TAGS + 1))

    def test_branch_required_for_branch_scope(self) -> None:
        with pytest.raises(ValidationError, match="Branch name is required"):
            MemoryEntry(key="k", value="v", scope=MemoryScope.branch)

    def test_branch_scope_with_branch_succeeds(self) -> None:
        entry = MemoryEntry(
            key="k", value="v", scope=MemoryScope.branch, branch="main"
        )
        assert entry.branch == "main"

    def test_reserved_fields_default_to_none_or_zero(self) -> None:
        entry = MemoryEntry(key="k", value="v")
        assert entry.last_reinforced is None
        assert entry.reinforce_count == 0
        assert entry.contradicted is False
        assert entry.contradiction_reason is None
        assert entry.seeded_from is None

    def test_serialization_roundtrip(self) -> None:
        entry = MemoryEntry(
            key="roundtrip-test",
            value="some value",
            tier=MemoryTier.architectural,
            source=MemorySource.human,
            tags=["python", "testing"],
        )
        data = entry.model_dump()
        restored = MemoryEntry.model_validate(data)
        assert restored == entry


class TestMemorySnapshot:
    """Tests for MemorySnapshot model."""

    def test_empty_snapshot(self) -> None:
        snap = MemorySnapshot(project_root="/some/path")
        assert snap.entries == []
        assert snap.total_count == 0
        assert snap.tier_counts == {}
        assert snap.exported_at

    def test_snapshot_with_entries(self) -> None:
        entries = [
            MemoryEntry(key="k1", value="v1", tier=MemoryTier.architectural),
            MemoryEntry(key="k2", value="v2", tier=MemoryTier.pattern),
        ]
        snap = MemorySnapshot(
            project_root="/p",
            entries=entries,
            total_count=2,
            tier_counts={"architectural": 1, "pattern": 1},
        )
        assert snap.total_count == 2
        assert len(snap.entries) == 2
