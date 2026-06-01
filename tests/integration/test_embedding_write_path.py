"""Postgres integration tests for the embedding write-path fix (TAP-2672).

Proves that a normal ``save`` now persists the pgvector ``embedding`` column
(previously the computed vector was discarded at the SQL layer, leaving every
row NULL).  Also covers the Hive save path and the backfill script's
idempotency.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (skipped otherwise). Mark:
``requires_postgres``.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
_EMBED_DIM = 384


class _StubProvider:
    """Deterministic embedding provider stub (no model download)."""

    model_id = "stub@v1"
    dimension = _EMBED_DIM

    def embed(self, text: str) -> list[float]:
        return [0.05] * _EMBED_DIM

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.05] * _EMBED_DIM for _ in texts]


def _cm() -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    return PostgresConnectionManager(_PG_DSN)


def _private_backend(cm: Any, project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_private import PostgresPrivateBackend

    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_hive_migrations, apply_private_migrations

    apply_private_migrations(_PG_DSN)
    apply_hive_migrations(_PG_DSN)


def _entry(key: str, value: str, embedding: list[float] | None) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value, embedding=embedding)


def test_private_save_persists_embedding_column() -> None:
    _apply_migrations()
    cm = _cm()
    project_id = f"emb-proj-{uuid.uuid4().hex[:8]}"
    agent_id = f"emb-agent-{uuid.uuid4().hex[:8]}"
    backend = _private_backend(cm, project_id, agent_id)

    vec = [0.05] * _EMBED_DIM
    backend.save(_entry("with-emb", "semantic content", vec))
    backend.save(_entry("no-emb", "lexical only", None))

    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT key, embedding IS NOT NULL FROM private_memories "
            "WHERE project_id = %s AND agent_id = %s ORDER BY key",
            (project_id, agent_id),
        )
        rows = dict(cur.fetchall())
    assert rows == {"no-emb": False, "with-emb": True}
    cm.close()


def test_private_save_embedding_is_searchable() -> None:
    _apply_migrations()
    cm = _cm()
    project_id = f"emb-proj-{uuid.uuid4().hex[:8]}"
    agent_id = f"emb-agent-{uuid.uuid4().hex[:8]}"
    backend = _private_backend(cm, project_id, agent_id)

    target = [1.0] + [0.0] * (_EMBED_DIM - 1)
    backend.save(_entry("target", "needle", target))
    hits = backend.knn_search(target, k=5)
    assert any(key == "target" for key, _ in hits)
    cm.close()


def test_hive_save_persists_embedding_column() -> None:
    _apply_migrations()
    cm = _cm()
    from tapps_brain.postgres_hive import PostgresHiveBackend

    hive = PostgresHiveBackend(cm)
    namespace = f"ns-{uuid.uuid4().hex[:8]}"
    vec = [0.05] * _EMBED_DIM

    hive.save(key="h-with", value="shared", namespace=namespace, embedding=vec)
    hive.save(key="h-without", value="shared2", namespace=namespace, embedding=None)

    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT key, embedding IS NOT NULL FROM hive_memories "
            "WHERE namespace = %s ORDER BY key",
            (namespace,),
        )
        rows = dict(cur.fetchall())
    assert rows == {"h-with": True, "h-without": False}
    cm.close()


def test_backfill_table_is_idempotent() -> None:
    _apply_migrations()
    cm = _cm()
    project_id = f"bf-proj-{uuid.uuid4().hex[:8]}"
    agent_id = f"bf-agent-{uuid.uuid4().hex[:8]}"
    backend = _private_backend(cm, project_id, agent_id)

    # Seed rows with NULL embeddings (no provider on the backend write).
    for i in range(3):
        backend.save(_entry(f"row-{i}", f"content {i}", None))

    from scripts.backfill_embeddings import _PLANS, backfill_table

    # Scope the plan to just this test's rows via the count/select WHERE clause:
    # the table-wide plan is fine because other rows in a fresh test DB are also
    # NULL; we assert on our own keys.
    plan = _PLANS["private_memories"]
    first = backfill_table(cm, _StubProvider(), plan, batch_size=2)
    assert first >= 3

    with cm.get_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM private_memories "
            "WHERE project_id = %s AND agent_id = %s AND embedding IS NULL",
            (project_id, agent_id),
        )
        remaining = cur.fetchone()[0]
    assert remaining == 0

    # Re-run is a no-op for our rows (idempotent).
    second = backfill_table(cm, _StubProvider(), plan, batch_size=2)
    assert second == 0
    cm.close()
