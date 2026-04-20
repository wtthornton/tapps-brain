"""PostgreSQL implementation of FederationBackend protocol.

EPIC-055 STORY-055.7 — full Postgres-backed Federation with:
- Parameterized SQL queries
- tsvector @@ plainto_tsquery() for full-text search
- JSONB tag containment for tag filtering
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class PostgresFederationBackend:
    """PostgreSQL-backed :class:`FederationBackend` implementation.

    Satisfies the ``FederationBackend`` protocol defined in ``_protocols.py``.
    """

    def __init__(self, connection_manager: PostgresConnectionManager) -> None:
        self._cm = connection_manager

    # ------------------------------------------------------------------
    # Publish / Unpublish
    # ------------------------------------------------------------------

    def publish(
        self,
        project_id: str,
        entries: list[Any],
        project_root: str = "",
    ) -> int:
        """Publish memory entries to the federation hub."""
        now = datetime.now(tz=UTC).isoformat()
        published = 0

        with self._cm.get_connection() as conn, conn.cursor() as cur:
            for entry in entries:
                tags_json = json.dumps(
                    entry.tags if hasattr(entry, "tags") else getattr(entry, "tags", [])
                )
                tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                source = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
                cur.execute(
                    """
                        INSERT INTO federated_memories
                            (project_id, key, value, tier, confidence, source,
                             source_agent, tags, created_at, updated_at,
                             published_at, origin_project_root, memory_group)
                        VALUES (%s, %s, %s, %s, %s, %s, %s,
                                %s::jsonb, %s, %s, %s, %s, %s)
                        ON CONFLICT (project_id, key) DO UPDATE SET
                            value = EXCLUDED.value,
                            tier = EXCLUDED.tier,
                            confidence = EXCLUDED.confidence,
                            source = EXCLUDED.source,
                            source_agent = EXCLUDED.source_agent,
                            tags = EXCLUDED.tags,
                            updated_at = EXCLUDED.updated_at,
                            published_at = EXCLUDED.published_at,
                            origin_project_root = EXCLUDED.origin_project_root,
                            memory_group = EXCLUDED.memory_group
                        """,
                    (
                        project_id,
                        entry.key,
                        entry.value,
                        tier,
                        entry.confidence,
                        source,
                        entry.source_agent,
                        tags_json,
                        entry.created_at,
                        entry.updated_at,
                        now,
                        project_root,
                        getattr(entry, "memory_group", None),
                    ),
                )
                published += 1

            # Update federation metadata.
            cur.execute(
                """
                    INSERT INTO federation_meta (project_id, last_sync, entry_count)
                    VALUES (
                        %s, %s,
                        (SELECT COUNT(*) FROM federated_memories WHERE project_id = %s)
                    )
                    ON CONFLICT (project_id) DO UPDATE SET
                        last_sync = EXCLUDED.last_sync,
                        entry_count = EXCLUDED.entry_count
                    """,
                (project_id, now, project_id),
            )

        logger.info("federation.pg.published", project_id=project_id, count=published)
        return published

    def unpublish(self, project_id: str, keys: list[str] | None = None) -> int:
        """Remove memories from the federation hub."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            if keys:
                cur.execute(
                    "DELETE FROM federated_memories WHERE project_id = %s AND key = ANY(%s)",
                    (project_id, keys),
                )
            else:
                cur.execute(
                    "DELETE FROM federated_memories WHERE project_id = %s",
                    (project_id,),
                )
            removed = cur.rowcount

        logger.info("federation.pg.unpublished", project_id=project_id, count=removed)
        return int(removed)

    # ------------------------------------------------------------------
    # Search & Query
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        project_ids: list[str] | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
        memory_group: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search with optional project, tag, and memory_group filters."""
        clauses = [
            "search_vector @@ plainto_tsquery('english', %s)",
            "confidence >= %s",
        ]
        params: list[Any] = [query, min_confidence]

        if project_ids:
            clauses.append("project_id = ANY(%s)")
            params.append(project_ids)

        if memory_group is not None:
            clauses.append("memory_group = %s")
            params.append(memory_group)

        # For tag filtering, use JSONB containment: tags @> ANY of the requested tags.
        # We fetch extra rows and filter in Python (same approach as SQLite backend)
        # since JSONB array overlap is less straightforward.
        sql_limit = limit * 3 if tags else limit
        params.append(sql_limit)

        from psycopg import sql as pgsql

        where = pgsql.SQL(" AND ").join(pgsql.SQL(c) for c in clauses)
        stmt = pgsql.SQL(
            "SELECT *, ts_rank(search_vector, plainto_tsquery('english', {})) AS rank "
            "FROM federated_memories "
            "WHERE {} "
            "ORDER BY rank DESC "
            "LIMIT {}"
        ).format(pgsql.Placeholder(), where, pgsql.Placeholder())

        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(stmt, [query, *params])
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(zip(col_names, r, strict=False))
            row_tags = d.get("tags", [])
            if isinstance(row_tags, str):
                row_tags = json.loads(row_tags)
            # Python-side tag filtering.
            if tags and not set(tags) & set(row_tags):
                continue
            d["tags"] = row_tags
            d.pop("search_vector", None)
            d.pop("rank", None)
            results.append(d)

        return results[:limit]

    def get_project_entries(
        self,
        project_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get all entries for a specific project."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    SELECT * FROM federated_memories
                    WHERE project_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                (project_id, limit),
            )
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]

        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(zip(col_names, r, strict=False))
            tags = d.get("tags", [])
            if isinstance(tags, str):
                d["tags"] = json.loads(tags)
            d.pop("search_vector", None)
            results.append(d)
        return results

    def get_stats(self) -> dict[str, Any]:
        """Return federation hub statistics."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM federated_memories")
            total = cur.fetchone()[0]

            cur.execute("SELECT project_id, COUNT(*) FROM federated_memories GROUP BY project_id")
            project_counts = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute("SELECT * FROM federation_meta")
            meta_rows = cur.fetchall()
            meta_cols = [desc[0] for desc in cur.description]
            meta = [dict(zip(meta_cols, r, strict=False)) for r in meta_rows]

        return {
            "total_entries": total,
            "projects": project_counts,
            "meta": meta,
        }

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._cm.close()
