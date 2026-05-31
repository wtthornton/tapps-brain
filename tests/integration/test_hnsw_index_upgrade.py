"""Integration tests for TAP-2676: hive + federation IVFFlat -> HNSW upgrade.

Verifies that after applying the hive (003) and federation (002) migrations:
  - ``hive_memories`` / ``federated_memories`` carry an HNSW embedding index
    matching the private-memories config (m=16, ef_construction=200,
    vector_cosine_ops) and no longer carry the old IVFFlat index.
  - The query planner picks the HNSW index for an ``embedding <=> '[...]'``
    nearest-neighbour ``ORDER BY``.
  - The migrations are idempotent (re-running is a no-op).

Requires: ``TAPPS_TEST_POSTGRES_DSN`` pointing to a live pgvector/pg17 instance.
Tests are skipped when the variable is not set. Use the throwaway-DB pattern:

    docker run -d --rm --name tb-test-pg -e POSTGRES_USER=tapps \\
        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=tapps_brain_test -p 55432:5432 \\
        pgvector/pgvector:pg17
    docker exec tb-test-pg psql -U tapps -d tapps_brain_test \\
        -c "CREATE EXTENSION IF NOT EXISTS vector;"
"""

from __future__ import annotations

import os

import pytest

_PG_DSN = os.environ.get("TAPPS_TEST_POSTGRES_DSN", "")
_SKIP_PG = not _PG_DSN

pytestmark = pytest.mark.skipif(_SKIP_PG, reason="TAPPS_TEST_POSTGRES_DSN not set")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(seed: float) -> str:
    """Return a 384-dim pgvector literal whose values vary by *seed*."""
    return "[" + ",".join(str((seed + i) % 7 * 0.1) for i in range(384)) + "]"


def _owner_conn() -> object:
    import psycopg

    return psycopg.connect(_PG_DSN, autocommit=False)


def _index_def(conn: object, index_name: str) -> str | None:
    with conn.cursor() as cur:  # type: ignore[union-attr]
        cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = %s", (index_name,))
        row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture(scope="module", autouse=True)
def _apply_migrations() -> None:
    """Apply hive + federation migrations once per module."""
    if _SKIP_PG:
        return
    from tapps_brain.postgres_migrations import (
        apply_federation_migrations,
        apply_hive_migrations,
    )

    apply_hive_migrations(_PG_DSN)
    apply_federation_migrations(_PG_DSN)


# ---------------------------------------------------------------------------
# Hive
# ---------------------------------------------------------------------------


class TestHiveHnswUpgrade:
    def test_hnsw_index_exists_ivfflat_gone(self) -> None:
        """idx_hive_embedding_hnsw exists with m=16/ef_construction=200; IVFFlat dropped."""
        with _owner_conn() as conn:
            hnsw = _index_def(conn, "idx_hive_embedding_hnsw")
            ivf = _index_def(conn, "idx_hive_embedding_ivfflat")
        assert hnsw is not None, "idx_hive_embedding_hnsw not created"
        assert "hnsw" in hnsw.lower()
        assert "vector_cosine_ops" in hnsw
        assert "m='16'" in hnsw and "ef_construction='200'" in hnsw, hnsw
        assert ivf is None, "old IVFFlat index should have been dropped"

    def test_schema_version_3_recorded(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM hive_schema_version WHERE version = 3")
                row = cur.fetchone()
        assert row is not None, "hive schema version 3 not recorded"

    def test_planner_picks_hnsw_index(self) -> None:
        """EXPLAIN on an embedding <=> ORDER BY query uses the HNSW index."""
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                for i in range(5):
                    cur.execute(
                        "INSERT INTO hive_memories (namespace, key, value, embedding)"
                        " VALUES (%s, %s, %s, %s::vector)"
                        " ON CONFLICT (namespace, key) DO UPDATE SET embedding = EXCLUDED.embedding",
                        ("tap2676-hive", f"k{i}", f"v{i}", _vec(float(i))),
                    )
                conn.commit()
                cur.execute("SET LOCAL enable_seqscan = off")
                # A pure ANN ORDER BY is the query an HNSW index serves; an extra
                # equality predicate on another column would steer the planner to
                # the matching btree filter index instead (cheaper on tiny tables).
                cur.execute(
                    "EXPLAIN SELECT key FROM hive_memories"
                    " ORDER BY embedding <=> %s::vector LIMIT 5",
                    (_vec(0.0),),
                )
                plan = "\n".join(r[0] for r in cur.fetchall())
                cur.execute("DELETE FROM hive_memories WHERE namespace = %s", ("tap2676-hive",))
            conn.commit()
        assert "idx_hive_embedding_hnsw" in plan, plan


# ---------------------------------------------------------------------------
# Federation
# ---------------------------------------------------------------------------


class TestFederationHnswUpgrade:
    def test_hnsw_index_exists_ivfflat_gone(self) -> None:
        with _owner_conn() as conn:
            hnsw = _index_def(conn, "idx_fed_embedding_hnsw")
            ivf = _index_def(conn, "idx_fed_embedding_ivfflat")
        assert hnsw is not None, "idx_fed_embedding_hnsw not created"
        assert "hnsw" in hnsw.lower()
        assert "vector_cosine_ops" in hnsw
        assert "m='16'" in hnsw and "ef_construction='200'" in hnsw, hnsw
        assert ivf is None, "old IVFFlat index should have been dropped"

    def test_schema_version_2_recorded(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM federation_schema_version WHERE version = 2")
                row = cur.fetchone()
        assert row is not None, "federation schema version 2 not recorded"

    def test_planner_picks_hnsw_index(self) -> None:
        with _owner_conn() as conn:
            with conn.cursor() as cur:
                for i in range(5):
                    cur.execute(
                        "INSERT INTO federated_memories (project_id, key, value, embedding)"
                        " VALUES (%s, %s, %s, %s::vector)"
                        " ON CONFLICT (project_id, key) DO UPDATE SET embedding = EXCLUDED.embedding",
                        ("tap2676-fed", f"k{i}", f"v{i}", _vec(float(i))),
                    )
                conn.commit()
                cur.execute("SET LOCAL enable_seqscan = off")
                cur.execute(
                    "EXPLAIN SELECT key FROM federated_memories"
                    " ORDER BY embedding <=> %s::vector LIMIT 5",
                    (_vec(0.0),),
                )
                plan = "\n".join(r[0] for r in cur.fetchall())
                cur.execute(
                    "DELETE FROM federated_memories WHERE project_id = %s", ("tap2676-fed",)
                )
            conn.commit()
        assert "idx_fed_embedding_hnsw" in plan, plan


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    def test_reapply_is_noop(self) -> None:
        """Re-running the migrations applies nothing new (already at head)."""
        from tapps_brain.postgres_migrations import (
            apply_federation_migrations,
            apply_hive_migrations,
        )

        assert apply_hive_migrations(_PG_DSN) == []
        assert apply_federation_migrations(_PG_DSN) == []
