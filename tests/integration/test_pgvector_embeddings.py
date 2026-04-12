"""Postgres integration tests for pgvector embedding storage and knn_search.

STORY-066.13: Replaces deleted SQLite-coupled test_memory_embeddings_persistence
and test_sqlite_vec_index test files with Postgres pgvector equivalents.

Tests cover:
- Writing embedding vectors directly to the private_memories table
- knn_search returning nearest neighbours by cosine distance
- vector_row_count reflecting stored embeddings
- Empty knn_search returns [] when no embeddings stored

Requires: ``TAPPS_BRAIN_DATABASE_URL`` environment variable (skipped otherwise).
Mark: ``requires_postgres``
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")

# Embedding dimension used by the schema (migration 001: vector(384))
_EMBED_DIM = 384


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_backend(project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


def _make_entry(key: str, value: str) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value)


def _unit_vector(index: int, dim: int = _EMBED_DIM) -> list[float]:
    """Return a unit vector with a 1.0 at *index* and 0.0 elsewhere.

    Using orthogonal unit vectors guarantees predictable cosine distances in
    knn_search: identical vectors have distance 0, orthogonal vectors have
    distance 1 (cosine distance = 1 - cosine_similarity).
    """
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


def _set_embedding(backend: Any, key: str, embedding: list[float]) -> None:
    """Directly update the embedding column for the given key.

    ``PostgresPrivateBackend.save()`` does not write the embedding column
    (the embedding is computed by MemoryStore's save phase and written via
    a separate UPDATE).  For integration tests we inject the vector directly
    to test knn_search without requiring a sentence-transformers model.
    """
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    with backend._cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE private_memories SET embedding = %s::vector "
            "WHERE project_id = %s AND agent_id = %s AND key = %s",
            (vec_str, backend._project_id, backend._agent_id, key),
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


@pytest.fixture
def backend(request: Any) -> Any:
    b = _make_backend(_unique_project(), _unique_agent())
    yield b
    b.close()


# ---------------------------------------------------------------------------
# vector_row_count
# ---------------------------------------------------------------------------


class TestVectorRowCount:
    def test_count_zero_when_no_embeddings(self, backend: Any) -> None:
        backend.save(_make_entry("count-k1", "no embedding yet"))
        assert backend.vector_row_count() == 0

    def test_count_increments_after_embedding_write(self, backend: Any) -> None:
        backend.save(_make_entry("count-k2", "will get embedding"))
        _set_embedding(backend, "count-k2", _unit_vector(0))
        assert backend.vector_row_count() == 1

    def test_count_reflects_multiple_embeddings(self, backend: Any) -> None:
        for i in range(3):
            key = f"count-multi-{i}"
            backend.save(_make_entry(key, f"entry {i}"))
            _set_embedding(backend, key, _unit_vector(i))
        assert backend.vector_row_count() == 3


# ---------------------------------------------------------------------------
# knn_search
# ---------------------------------------------------------------------------


class TestKnnSearch:
    def test_knn_search_empty_when_no_embeddings(self, backend: Any) -> None:
        backend.save(_make_entry("knn-empty", "no embedding stored"))
        results = backend.knn_search(_unit_vector(0), k=5)
        assert results == []

    def test_knn_search_empty_query_returns_empty(self, backend: Any) -> None:
        assert backend.knn_search([], k=5) == []

    def test_knn_search_finds_identical_vector(self, backend: Any) -> None:
        """Searching with the exact stored vector should return distance ≈ 0."""
        key = "knn-identical"
        vec = _unit_vector(10)
        backend.save(_make_entry(key, "identical vector test"))
        _set_embedding(backend, key, vec)

        results = backend.knn_search(vec, k=1)
        assert len(results) == 1
        found_key, distance = results[0]
        assert found_key == key
        assert distance < 1e-4, f"Expected near-zero distance, got {distance}"

    def test_knn_search_ranks_by_distance(self, backend: Any) -> None:
        """Closer vectors should rank higher (lower distance)."""
        query_vec = _unit_vector(5)

        # 'near' entry: same direction as query → distance ~0
        near_key = "knn-near"
        backend.save(_make_entry(near_key, "near vector"))
        _set_embedding(backend, near_key, _unit_vector(5))

        # 'far' entry: orthogonal to query → distance ~1
        far_key = "knn-far"
        backend.save(_make_entry(far_key, "far vector"))
        _set_embedding(backend, far_key, _unit_vector(6))

        results = backend.knn_search(query_vec, k=2)
        assert len(results) == 2
        keys_in_order = [r[0] for r in results]
        # Near entry must rank first (lower distance)
        assert keys_in_order[0] == near_key, f"Expected {near_key} first, got {keys_in_order}"

    def test_knn_search_respects_k_limit(self, backend: Any) -> None:
        """knn_search must return at most k results."""
        for i in range(10):
            key = f"knn-limit-{i}"
            backend.save(_make_entry(key, f"entry {i}"))
            _set_embedding(backend, key, _unit_vector(i))

        results = backend.knn_search(_unit_vector(0), k=3)
        assert len(results) <= 3

    def test_knn_search_returns_key_and_distance_tuple(self, backend: Any) -> None:
        """Each result must be a (str, float) tuple."""
        backend.save(_make_entry("knn-tuple", "tuple shape test"))
        _set_embedding(backend, "knn-tuple", _unit_vector(20))

        results = backend.knn_search(_unit_vector(20), k=1)
        assert len(results) == 1
        key, dist = results[0]
        assert isinstance(key, str)
        assert isinstance(dist, float)

    def test_knn_search_scoped_per_agent(self) -> None:
        """knn_search must not return another agent's vectors."""
        project_id = _unique_project()
        agent_a = _make_backend(project_id, "vec-agent-a")
        agent_b = _make_backend(project_id, "vec-agent-b")
        try:
            vec = _unit_vector(50)
            agent_a.save(_make_entry("a-vec-key", "agent a vector"))
            _set_embedding(agent_a, "a-vec-key", vec)

            # Agent B has no embeddings — should return empty
            results_b = agent_b.knn_search(vec, k=5)
            assert results_b == []
        finally:
            agent_a.close()
            agent_b.close()

    def test_knn_search_distance_between_orthogonal_vectors(self, backend: Any) -> None:
        """Cosine distance between two orthogonal unit vectors should be ≈ 1."""
        key = "knn-ortho"
        backend.save(_make_entry(key, "orthogonal test"))
        _set_embedding(backend, key, _unit_vector(100))

        # Query with an orthogonal vector (index 101 is perpendicular to index 100)
        results = backend.knn_search(_unit_vector(101), k=1)
        assert len(results) == 1
        _, distance = results[0]
        # Cosine distance of orthogonal vectors = 1.0 (1 - cos(90°))
        assert abs(distance - 1.0) < 0.05, f"Expected distance ≈ 1.0, got {distance}"
