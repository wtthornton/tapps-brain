"""Session indexing for searchable past sessions (EPIC-002, EPIC-065.10).

Stores session chunks (summaries or key facts) in the Postgres
``session_chunks`` table with a tsvector search index.  Scoped to a single
``(project_id, agent_id)`` pair — same isolation model as
:class:`~tapps_brain.postgres_private.PostgresPrivateBackend`.

Trade-off: more coverage, more noise.  Flush-prompt quality becomes critical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger = structlog.get_logger(__name__)

# Limits per EPIC-065.10
_MAX_CHUNKS_DEFAULT = 50
_MAX_CHARS_DEFAULT = 500


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
