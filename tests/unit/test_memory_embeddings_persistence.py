"""Unit tests for memory persistence with embeddings (Epic 65.7)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.models import (
    MemoryEntry,
    MemorySource,
    MemoryTier,
)
from tapps_brain.persistence import MemoryPersistence

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def persistence(tmp_path: Path) -> MemoryPersistence:
    """Create a MemoryPersistence instance backed by a temp directory."""
    return MemoryPersistence(tmp_path)


class TestEmbeddingPersistence:
    """Tests for embedding column storage and retrieval."""

    def test_save_and_load_entry_with_embedding(
        self, persistence: MemoryPersistence
    ) -> None:
        embedding = [0.1, 0.2, 0.3] * 128  # 384 dims
        entry = MemoryEntry(
            key="embed-test",
            value="Test with embedding",
            tier=MemoryTier.pattern,
            source=MemorySource.agent,
            embedding=embedding,
        )
        persistence.save(entry)
        loaded = persistence.get("embed-test")
        assert loaded is not None
        assert loaded.embedding is not None
        assert loaded.embedding == embedding

    def test_save_and_load_entry_without_embedding(
        self, persistence: MemoryPersistence
    ) -> None:
        entry = MemoryEntry(
            key="no-embed",
            value="No embedding",
            tier=MemoryTier.pattern,
            source=MemorySource.agent,
        )
        persistence.save(entry)
        loaded = persistence.get("no-embed")
        assert loaded is not None
        assert loaded.embedding is None

    def test_schema_version_2_after_init(self, persistence: MemoryPersistence) -> None:
        assert persistence.get_schema_version() >= 2  # v3 adds session_index (Epic 65.10)

    def test_existing_entries_null_embedding(
        self, tmp_path: Path
    ) -> None:
        p = MemoryPersistence(tmp_path)
        entry = MemoryEntry(key="legacy", value="Before embedding")
        p.save(entry)
        loaded = p.get("legacy")
        assert loaded is not None
        assert loaded.embedding is None
        p.close()
