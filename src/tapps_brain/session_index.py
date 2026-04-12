"""Session indexing for searchable past sessions (EPIC-002, EPIC-065.10).

Stores session chunks (summaries or key facts) in the Postgres
``session_chunks`` table with a tsvector search index.  Scoped to a single
``(project_id, agent_id)`` pair — same isolation model as
:class:`~tapps_brain.postgres_private.PostgresPrivateBackend`.

Trade-off: more coverage, more noise.  Flush-prompt quality becomes critical.

Module-level convenience functions (``index_session``, ``search_session_index``,
``delete_expired_sessions``) provide a simple path-based API used by
:class:`~tapps_brain.store.MemoryStore`.  In environments without a Postgres
connection (e.g. unit tests) they use a lightweight in-process dict-based
index keyed by the project-root path.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.postgres_connection import PostgresConnectionManager

logger = structlog.get_logger(__name__)

# Limits per EPIC-065.10
_MAX_CHUNKS_DEFAULT = 50
_MAX_CHARS_DEFAULT = 500

# ---------------------------------------------------------------------------
# Module-level in-memory index (unit-test / no-Postgres path)
# ---------------------------------------------------------------------------
# Keyed by str(project_root).  Each value is a list of chunk dicts with keys:
# session_id, chunk_index, content, created_at.
# Production code paths (with a real Postgres connection) use SessionIndex
# directly and never touch this dict.

_in_memory_index: dict[str, list[dict[str, Any]]] = {}
_in_memory_lock = threading.Lock()


def index_session(
    project_root: Path,
    session_id: str,
    chunks: list[str],
    *,
    max_chunks: int = _MAX_CHUNKS_DEFAULT,
    max_chars_per_chunk: int = _MAX_CHARS_DEFAULT,
) -> int:
    """Index session chunks for later search (in-memory fallback).

    Production code should use :class:`SessionIndex` with a real Postgres
    connection.  This function is provided for environments where Postgres is
    unavailable (e.g. unit tests).

    Args:
        project_root: Project root path — used as the isolation key.
        session_id: Session identifier.
        chunks: Text chunks to index.
        max_chunks: Maximum number of chunks to store.
        max_chars_per_chunk: Maximum characters per chunk.

    Returns:
        Number of chunks actually stored.
    """
    if not session_id or not session_id.strip():
        return 0
    trimmed = [c[:max_chars_per_chunk] for c in chunks[:max_chunks] if c and c.strip()]
    if not trimmed:
        return 0

    key = str(project_root)
    now = datetime.now(UTC).isoformat()
    with _in_memory_lock:
        bucket = _in_memory_index.setdefault(key, [])
        for idx, content in enumerate(trimmed):
            # Upsert: replace existing (session_id, chunk_index) pair.
            existing = next(
                (
                    i
                    for i, r in enumerate(bucket)
                    if r["session_id"] == session_id and r["chunk_index"] == idx
                ),
                None,
            )
            record: dict[str, Any] = {
                "session_id": session_id,
                "chunk_index": idx,
                "content": content,
                "created_at": now,
            }
            if existing is not None:
                bucket[existing] = record
            else:
                bucket.append(record)
    return len(trimmed)


def search_session_index(
    project_root: Path,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search the in-memory session index by plain-text query.

    Returns dicts with ``session_id``, ``chunk_index``, ``content``,
    ``created_at`` keys, sorted by a simple relevance heuristic.
    """
    if not query or not query.strip():
        return []
    q_words = set(query.lower().split())
    key = str(project_root)
    with _in_memory_lock:
        bucket = list(_in_memory_index.get(key, []))

    results: list[tuple[int, dict[str, Any]]] = []
    for record in bucket:
        content_words = set(record["content"].lower().split())
        overlap = len(q_words & content_words)
        if overlap > 0:
            results.append((overlap, record))

    results.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in results[:limit]]


def delete_expired_sessions(project_root: Path, ttl_days: int) -> int:
    """Delete session chunks older than *ttl_days* from the in-memory index.

    Returns count of deleted chunks.
    """
    if ttl_days < 1:
        return 0
    from datetime import timedelta

    cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat()
    key = str(project_root)
    with _in_memory_lock:
        bucket = _in_memory_index.get(key, [])
        before = len(bucket)
        _in_memory_index[key] = [r for r in bucket if r.get("created_at", "") >= cutoff]
        return before - len(_in_memory_index[key])


class SessionIndex:
    """Postgres-backed session chunk index.

    Args:
        connection_manager: Shared :class:`PostgresConnectionManager`.
        project_id: Canonical project identifier.
        agent_id: Agent identifier string.
    """

    def __init__(
        self,
        connection_manager: PostgresConnectionManager,
        *,
        project_id: str,
        agent_id: str,
    ) -> None:
        self._cm = connection_manager
        self._project_id = project_id
        self._agent_id = agent_id

    def save_chunks(
        self,
        session_id: str,
        chunks: list[str],
        *,
        max_chunks: int = _MAX_CHUNKS_DEFAULT,
        max_chars_per_chunk: int = _MAX_CHARS_DEFAULT,
    ) -> int:
        """Persist session chunks, returning the number stored."""
        if not session_id or not session_id.strip():
            logger.warning("session_index_skip_empty_id")
            return 0

        trimmed = [c[:max_chars_per_chunk] for c in chunks[:max_chunks] if c and c.strip()]
        if not trimmed:
            return 0

        with self._cm.get_connection() as conn, conn.cursor() as cur:
            for idx, content in enumerate(trimmed):
                cur.execute(
                    """
                    INSERT INTO session_chunks
                        (project_id, agent_id, session_id, chunk_index, content)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, agent_id, session_id, chunk_index)
                    DO UPDATE SET content = EXCLUDED.content
                    """,
                    (self._project_id, self._agent_id, session_id, idx, content),
                )
        logger.debug(
            "session_indexed",
            session_id=session_id,
            chunks_stored=len(trimmed),
            chunks_input=len(chunks),
        )
        return len(trimmed)

    def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search session chunks by plain-text query.

        Returns dicts with ``session_id``, ``chunk_index``, ``content``,
        ``created_at`` keys, ranked by ``ts_rank`` descending.
        """
        if not query or not query.strip():
            return []

        sql = (
            "SELECT session_id, chunk_index, content, created_at, "
            "       ts_rank(search_vector, plainto_tsquery('english', %s)) AS _rank "
            "FROM session_chunks "
            "WHERE project_id = %s AND agent_id = %s "
            "  AND search_vector @@ plainto_tsquery('english', %s) "
            "ORDER BY _rank DESC "
            "LIMIT %s"
        )
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, (query, self._project_id, self._agent_id, query, limit))
            rows = cur.fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            created_raw = row[3]
            created_str = (
                created_raw.isoformat() if hasattr(created_raw, "isoformat") else str(created_raw)
            )
            results.append(
                {
                    "session_id": str(row[0]),
                    "chunk_index": int(row[1]),
                    "content": str(row[2]),
                    "created_at": created_str,
                }
            )
        return results

    def delete_expired(self, ttl_days: int) -> int:
        """Delete session chunks older than *ttl_days*.  Returns count deleted."""
        if ttl_days < 1:
            return 0
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM session_chunks
                WHERE project_id = %s
                  AND agent_id = %s
                  AND created_at < now() - make_interval(days => %s)
                """,
                (self._project_id, self._agent_id, ttl_days),
            )
            return int(cur.rowcount or 0)
