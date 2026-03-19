"""Shared fixtures for performance benchmarks.

Provides a pre-populated MemoryStore with 500 entries across mixed tiers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path

_TIERS = ["architectural", "pattern", "procedural", "context"]
_SOURCES = ["human", "agent", "inferred", "system"]


@pytest.fixture()
def populated_store(tmp_path: Path) -> MemoryStore:
    """Create a MemoryStore pre-populated with 500 entries across mixed tiers."""
    store = MemoryStore(tmp_path)
    for i in range(500):
        tier = _TIERS[i % len(_TIERS)]
        source = _SOURCES[i % len(_SOURCES)]
        store.save(
            key=f"bench-key-{i:04d}",
            value=f"Benchmark entry {i} about {tier} decisions and {source} observations "
            f"for project component {i % 20}. This contains enough text to exercise "
            f"BM25 tokenization and FTS5 indexing properly.",
            tier=tier,
            source=source,
            tags=[f"tag-{i % 10}", f"component-{i % 20}"],
            confidence=0.5 + (i % 50) / 100.0,
        )
    yield store
    store.close()


@pytest.fixture()
def empty_store(tmp_path: Path) -> MemoryStore:
    """Create an empty MemoryStore for write benchmarks."""
    store = MemoryStore(tmp_path)
    yield store
    store.close()
