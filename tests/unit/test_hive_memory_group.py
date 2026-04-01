"""Tests for ``memory_group`` on Hive ``hive_memories`` (multi-scope memory / propagation)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tapps_brain.hive import HiveStore, PropagationEngine
from tapps_brain.models import MemoryScope, MemorySource, MemoryTier
from tests.factories import make_entry

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def hive_store(tmp_path: Path) -> HiveStore:
    """A fresh HiveStore backed by a temp file."""
    store = HiveStore(db_path=tmp_path / "hive.db")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# Schema migration — column must exist after init
# ---------------------------------------------------------------------------


def test_schema_has_memory_group_column(tmp_path: Path) -> None:
    """memory_group column exists in hive_memories after store initialisation."""
    store = HiveStore(db_path=tmp_path / "hive.db")
    try:
        cols = {
            row[1] for row in store._conn.execute("PRAGMA table_info(hive_memories)").fetchall()
        }
        assert "memory_group" in cols
    finally:
        store.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Opening the store twice does not raise (migration guard is safe)."""
    db = tmp_path / "hive.db"
    s1 = HiveStore(db_path=db)
    s1.close()
    s2 = HiveStore(db_path=db)
    s2.close()


# ---------------------------------------------------------------------------
# HiveStore.save / HiveStore.get round-trip
# ---------------------------------------------------------------------------


def test_save_and_get_with_memory_group(hive_store: HiveStore) -> None:
    """Saving with memory_group='team-a' round-trips correctly."""
    hive_store.save(
        key="my-key",
        value="some value",
        namespace="universal",
        source_agent="agent-1",
        memory_group="team-a",
    )
    entry = hive_store.get("my-key", namespace="universal")
    assert entry is not None
    assert entry["memory_group"] == "team-a"


def test_save_and_get_without_memory_group(hive_store: HiveStore) -> None:
    """Saving without memory_group stores None and round-trips correctly."""
    hive_store.save(
        key="no-group-key",
        value="another value",
        namespace="universal",
        source_agent="agent-1",
    )
    entry = hive_store.get("no-group-key", namespace="universal")
    assert entry is not None
    assert entry["memory_group"] is None


# ---------------------------------------------------------------------------
# PropagationEngine carries memory_group
# ---------------------------------------------------------------------------


def test_propagate_with_memory_group(hive_store: HiveStore) -> None:
    """PropagationEngine.propagate passes memory_group into the hive entry."""
    result = PropagationEngine.propagate(
        key="prop-key",
        value="propagated value",
        agent_scope="hive",
        agent_id="test-agent",
        agent_profile="repo-brain",
        tier="pattern",
        confidence=0.8,
        source="agent",
        tags=[],
        hive_store=hive_store,
        memory_group="team-a",
    )
    assert result is not None
    assert result["memory_group"] == "team-a"

    # Verify via get() as well
    stored = hive_store.get("prop-key", namespace="universal")
    assert stored is not None
    assert stored["memory_group"] == "team-a"


def test_propagate_without_memory_group(hive_store: HiveStore) -> None:
    """PropagationEngine.propagate stores None when memory_group is omitted."""
    result = PropagationEngine.propagate(
        key="prop-key-no-group",
        value="propagated value",
        agent_scope="hive",
        agent_id="test-agent",
        agent_profile="repo-brain",
        tier="pattern",
        confidence=0.8,
        source="agent",
        tags=[],
        hive_store=hive_store,
    )
    assert result is not None
    assert result["memory_group"] is None

    stored = hive_store.get("prop-key-no-group", namespace="universal")
    assert stored is not None
    assert stored["memory_group"] is None


def test_propagate_private_scope_stays_local(hive_store: HiveStore) -> None:
    """Private-scoped entries are never propagated (memory_group unchanged)."""
    result = PropagationEngine.propagate(
        key="private-key",
        value="private value",
        agent_scope="private",
        agent_id="test-agent",
        agent_profile="repo-brain",
        tier="pattern",
        confidence=0.8,
        source="agent",
        tags=[],
        hive_store=hive_store,
        memory_group="team-a",
    )
    assert result is None
    assert hive_store.get("private-key", namespace="universal") is None


# ---------------------------------------------------------------------------
# Full round-trip via MemoryEntry → _propagate_to_hive
# ---------------------------------------------------------------------------


class TestPropagationGroupScope:
    """GitHub #52: agent_scope group:<name> + Hive membership."""

    def test_propagate_group_member(self, hive_store: HiveStore) -> None:
        hive_store.create_group("team-alpha")
        assert hive_store.add_group_member("team-alpha", "agent-1")
        result = PropagationEngine.propagate(
            key="gkey",
            value="group shared",
            agent_scope="group:team-alpha",
            agent_id="agent-1",
            agent_profile="repo-brain",
            tier="pattern",
            confidence=0.8,
            source="agent",
            tags=[],
            hive_store=hive_store,
        )
        assert result is not None
        assert result["namespace"] == "team-alpha"
        got = hive_store.get("gkey", namespace="team-alpha")
        assert got is not None
        assert got["value"] == "group shared"

    def test_propagate_group_non_member(self, hive_store: HiveStore) -> None:
        hive_store.create_group("team-beta")
        result = PropagationEngine.propagate(
            key="no-access",
            value="secret",
            agent_scope="group:team-beta",
            agent_id="outsider",
            agent_profile="repo-brain",
            tier="pattern",
            confidence=0.8,
            source="agent",
            tags=[],
            hive_store=hive_store,
        )
        assert result is None
        assert hive_store.get("no-access", namespace="team-beta") is None

    def test_agent_is_group_member(self, hive_store: HiveStore) -> None:
        hive_store.create_group("g")
        assert not hive_store.agent_is_group_member("g", "a")
        hive_store.add_group_member("g", "a")
        assert hive_store.agent_is_group_member("g", "a")


def test_round_trip_via_memory_entry(tmp_path: Path) -> None:
    """memory_group on a MemoryEntry is preserved after propagation to hive."""
    from tapps_brain.hive import HiveStore, PropagationEngine

    hive = HiveStore(db_path=tmp_path / "hive.db")
    try:
        entry = make_entry(
            key="rt-key",
            value="round-trip value",
            scope=MemoryScope.shared,
            source=MemorySource.agent,
            tier=MemoryTier.pattern,
        )
        # Manually set memory_group and agent_scope (not in factory defaults)
        object.__setattr__(entry, "memory_group", "partition-x")
        object.__setattr__(entry, "agent_scope", "hive")

        PropagationEngine.propagate(
            key=entry.key,
            value=entry.value,
            agent_scope=entry.agent_scope,
            agent_id="rt-agent",
            agent_profile="repo-brain",
            tier=entry.tier.value,
            confidence=entry.confidence,
            source=entry.source.value,
            tags=entry.tags,
            hive_store=hive,
            memory_group=entry.memory_group,
        )

        stored = hive.get("rt-key", namespace="universal")
        assert stored is not None
        assert stored["memory_group"] == "partition-x"
    finally:
        hive.close()
