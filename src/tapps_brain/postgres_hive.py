"""PostgreSQL implementation of HiveBackend and AgentRegistryBackend protocols.

EPIC-055 STORIES 055.3-055.6 — full Postgres-backed Hive with:
- Parameterized SQL queries (no f-strings with user input)
- tsvector @@ plainto_tsquery() for full-text search
- embedding <-> query_embedding for semantic similarity
- LISTEN/NOTIFY with fallback to polling for write notifications
- CRUD on agent_registry table
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import psycopg

    from tapps_brain.models import AgentRegistration
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class PostgresHiveBackend:
    """PostgreSQL-backed :class:`HiveBackend` implementation.

    Satisfies the ``HiveBackend`` protocol defined in ``_protocols.py``.
    Uses parameterized queries throughout for safety.
    """

    def __init__(self, connection_manager: PostgresConnectionManager) -> None:
        self._cm = connection_manager
        # _db_path is required by the HiveBackend protocol; use a sentinel for PG.
        self._db_path = Path("/dev/null")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save(
        self,
        *,
        key: str,
        value: str,
        namespace: str = "universal",
        source_agent: str = "unknown",
        tier: str = "pattern",
        confidence: float = 0.6,
        source: str = "agent",
        tags: list[str] | None = None,
        valid_at: str | None = None,
        invalid_at: str | None = None,
        superseded_by: str | None = None,
        conflict_policy: str = "supersede",
        memory_group: str | None = None,
    ) -> dict[str, Any] | None:
        """Save a memory entry to PostgreSQL.

        Uses INSERT ... ON CONFLICT for upsert semantics.
        Conflict policies are evaluated in Python to match SQLite backend behavior.
        """
        now = datetime.now(tz=UTC).isoformat()
        tags_json = json.dumps(tags or [])
        policy = str(conflict_policy)

        with self._cm.get_connection() as conn, conn.cursor() as cur:
            # Check for existing entry.
            cur.execute(
                "SELECT * FROM hive_memories WHERE namespace = %s AND key = %s",
                (namespace, key),
            )
            existing = cur.fetchone()

            if existing is not None:
                col_names = [desc[0] for desc in cur.description]
                existing_dict = dict(zip(col_names, existing, strict=False))

                resolved = self._resolve_conflict(
                    policy=policy,
                    existing=existing_dict,
                    new_confidence=confidence,
                    source_agent=source_agent,
                )
                if resolved is None:
                    return None
                if resolved == "supersede_version":
                    return self._supersede_existing(
                        cur,
                        existing=existing_dict,
                        key=key,
                        value=value,
                        namespace=namespace,
                        source_agent=source_agent,
                        tier=tier,
                        confidence=confidence,
                        source=source,
                        tags_json=tags_json,
                        now=now,
                        memory_group=memory_group,
                    )

            # Normal write or overwrite.
            created_at = existing_dict.get("created_at", now) if existing is not None else now
            return self._write_entry(
                cur,
                key=key,
                value=value,
                namespace=namespace,
                source_agent=source_agent,
                tier=tier,
                confidence=confidence,
                source=source,
                tags_json=tags_json,
                created_at=created_at,
                now=now,
                valid_at=valid_at,
                invalid_at=invalid_at,
                superseded_by=superseded_by,
                conflict_policy=policy,
                memory_group=memory_group,
            )

    def _resolve_conflict(
        self,
        *,
        policy: str,
        existing: dict[str, Any],
        new_confidence: float,
        source_agent: str,
    ) -> str | None:
        """Apply conflict policy. Returns 'overwrite', 'supersede_version', or None."""
        if policy == "last_write_wins":
            return "overwrite"
        if policy == "source_authority":
            if source_agent != existing.get("source_agent", ""):
                return None
            return "overwrite"
        if policy == "confidence_max":
            old_confidence = existing.get("confidence", 0.0)
            if new_confidence <= old_confidence:
                return None
            return "overwrite"
        # supersede (default)
        return "supersede_version"

    def _supersede_existing(
        self,
        cur: psycopg.Cursor[Any],
        *,
        existing: dict[str, Any],
        key: str,
        value: str,
        namespace: str,
        source_agent: str,
        tier: str,
        confidence: float,
        source: str,
        tags_json: str,
        now: str,
        memory_group: str | None,
    ) -> dict[str, Any]:
        """Mark old version invalid and write new versioned key."""
        new_key = f"{key}-v{now.replace(':', '').replace('-', '').replace('+', '')[:22]}"
        cur.execute(
            "UPDATE hive_memories SET invalid_at = %s, superseded_by = %s "
            "WHERE namespace = %s AND key = %s",
            (now, new_key, namespace, key),
        )
        return self._write_entry(
            cur,
            key=new_key,
            value=value,
            namespace=namespace,
            source_agent=source_agent,
            tier=tier,
            confidence=confidence,
            source=source,
            tags_json=tags_json,
            created_at=now,
            now=now,
            valid_at=now,
            invalid_at=None,
            superseded_by=None,
            conflict_policy="supersede",
            memory_group=memory_group,
        )

    def _write_entry(
        self,
        cur: psycopg.Cursor[Any],
        *,
        key: str,
        value: str,
        namespace: str,
        source_agent: str,
        tier: str,
        confidence: float,
        source: str,
        tags_json: str,
        created_at: str,
        now: str,
        valid_at: str | None,
        invalid_at: str | None,
        superseded_by: str | None,
        conflict_policy: str,
        memory_group: str | None,
    ) -> dict[str, Any]:
        """Perform the actual INSERT ... ON CONFLICT DO UPDATE."""
        cur.execute(
            """
            INSERT INTO hive_memories
                (namespace, key, value, source_agent, tier, confidence, source,
                 tags, valid_at, invalid_at, superseded_by, memory_group,
                 conflict_policy, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s,
                    %s, %s, %s)
            ON CONFLICT (namespace, key) DO UPDATE SET
                value = EXCLUDED.value,
                source_agent = EXCLUDED.source_agent,
                tier = EXCLUDED.tier,
                confidence = EXCLUDED.confidence,
                source = EXCLUDED.source,
                tags = EXCLUDED.tags,
                valid_at = EXCLUDED.valid_at,
                invalid_at = EXCLUDED.invalid_at,
                superseded_by = EXCLUDED.superseded_by,
                memory_group = EXCLUDED.memory_group,
                conflict_policy = EXCLUDED.conflict_policy,
                updated_at = EXCLUDED.updated_at
            """,
            (
                namespace,
                key,
                value,
                source_agent,
                tier,
                confidence,
                source,
                tags_json,
                valid_at,
                invalid_at,
                superseded_by,
                memory_group,
                conflict_policy,
                created_at,
                now,
            ),
        )

        # Bump write-notify revision.
        cur.execute(
            "UPDATE hive_write_notify SET revision = revision + 1, updated_at = %s WHERE id = 1",
            (now,),
        )

        logger.info("hive.pg.saved", key=key, namespace=namespace, source_agent=source_agent)

        return {
            "namespace": namespace,
            "key": key,
            "value": value,
            "tier": tier,
            "confidence": confidence,
            "source": source,
            "source_agent": source_agent,
            "tags": json.loads(tags_json),
            "created_at": created_at,
            "updated_at": now,
            "valid_at": valid_at,
            "invalid_at": invalid_at,
            "superseded_by": superseded_by,
            "memory_group": memory_group,
        }

    def get(self, key: str, namespace: str = "universal") -> dict[str, Any] | None:
        """Retrieve a single entry by (namespace, key)."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM hive_memories WHERE namespace = %s AND key = %s",
                (namespace, key),
            )
            row = cur.fetchone()
            if row is None:
                return None
            col_names = [desc[0] for desc in cur.description]
            return self._row_to_dict(dict(zip(col_names, row, strict=False)))

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Full-text search using tsvector @@ plainto_tsquery()."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            if namespaces:
                cur.execute(
                    """
                        SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
                        FROM hive_memories
                        WHERE search_vector @@ plainto_tsquery('english', %s)
                          AND confidence >= %s
                          AND namespace = ANY(%s)
                        ORDER BY rank DESC
                        LIMIT %s
                        """,
                    (query, query, min_confidence, namespaces, limit),
                )
            else:
                cur.execute(
                    """
                        SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
                        FROM hive_memories
                        WHERE search_vector @@ plainto_tsquery('english', %s)
                          AND confidence >= %s
                        ORDER BY rank DESC
                        LIMIT %s
                        """,
                    (query, query, min_confidence, limit),
                )
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            return [self._row_to_dict(dict(zip(col_names, r, strict=False))) for r in rows]

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def patch_confidence(self, *, namespace: str, key: str, confidence: float) -> bool:
        """Update confidence for a (namespace, key) entry."""
        now = datetime.now(tz=UTC).isoformat()
        c = max(0.05, min(1.0, float(confidence)))
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    UPDATE hive_memories SET confidence = %s, updated_at = %s
                    WHERE namespace = %s AND key = %s
                    """,
                (c, now, namespace, key),
            )
            changed = cur.rowcount > 0
            if changed:
                cur.execute(
                    "UPDATE hive_write_notify SET revision = revision + 1, updated_at = %s "
                    "WHERE id = 1",
                    (now,),
                )
            return bool(changed)

    def get_confidence(self, *, namespace: str, key: str) -> float | None:
        """Return the current confidence for a row, or None."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT confidence FROM hive_memories WHERE namespace = %s AND key = %s",
                (namespace, key),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return float(row[0])

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    def create_group(self, name: str, description: str = "") -> dict[str, Any]:
        now = datetime.now(tz=UTC).isoformat()
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO hive_groups (name, description, created_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
                    """,
                (name, description, now),
            )
        return {"name": name, "description": description, "created_at": now}

    def add_group_member(self, group_name: str, agent_id: str, role: str = "member") -> bool:
        now = datetime.now(tz=UTC).isoformat()
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT name FROM hive_groups WHERE name = %s", (group_name,))
            if cur.fetchone() is None:
                return False
            cur.execute(
                """
                    INSERT INTO hive_group_members (group_name, agent_id, role, joined_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (group_name, agent_id) DO UPDATE SET role = EXCLUDED.role
                    """,
                (group_name, agent_id, role, now),
            )
        return True

    def remove_group_member(self, group_name: str, agent_id: str) -> bool:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM hive_group_members WHERE group_name = %s AND agent_id = %s",
                (group_name, agent_id),
            )
            return bool(cur.rowcount > 0)

    def list_groups(self) -> list[dict[str, Any]]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT name, description, created_at FROM hive_groups ORDER BY name")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, r, strict=False)) for r in rows]

    def get_group_members(self, group_name: str) -> list[dict[str, Any]]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT group_name, agent_id, role, joined_at "
                "FROM hive_group_members WHERE group_name = %s ORDER BY joined_at",
                (group_name,),
            )
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            return [dict(zip(col_names, r, strict=False)) for r in rows]

    def get_agent_groups(self, agent_id: str) -> list[str]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT group_name FROM hive_group_members WHERE agent_id = %s ORDER BY group_name",
                (agent_id,),
            )
            return [row[0] for row in cur.fetchall()]

    def agent_is_group_member(self, group_name: str, agent_id: str) -> bool:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM hive_group_members WHERE group_name = %s AND agent_id = %s",
                (group_name, agent_id),
            )
            return cur.fetchone() is not None

    def search_with_groups(
        self,
        query: str,
        agent_id: str,
        agent_namespace: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> list[dict[str, Any]]:
        """Search across agent's own namespace + group namespaces + universal."""
        own_ns = agent_namespace or agent_id
        group_names = self.get_agent_groups(agent_id)
        namespaces = list({own_ns, *group_names, "universal"})
        return self.search(query, namespaces=namespaces, **kwargs)

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def record_feedback_event(
        self,
        *,
        event_id: str,
        namespace: str,
        entry_key: str | None,
        event_type: str,
        session_id: str | None,
        utility_score: float | None,
        details: dict[str, Any],
        timestamp: str,
        source_project: str | None = None,
    ) -> None:
        details_json = json.dumps(details, default=str)
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO hive_feedback_events
                        (id, namespace, entry_key, event_type, session_id,
                         utility_score, details, timestamp, source_project)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                (
                    event_id,
                    namespace,
                    entry_key,
                    event_type,
                    session_id,
                    utility_score,
                    details_json,
                    timestamp,
                    source_project,
                ),
            )

    def query_feedback_events(
        self,
        *,
        namespace: str | None = None,
        entry_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        from psycopg import sql as pgsql

        conditions: list[pgsql.Composable] = []
        params: list[Any] = []
        if namespace is not None:
            conditions.append(pgsql.SQL("namespace = {}").format(pgsql.Placeholder()))
            params.append(namespace)
        if entry_key is not None:
            conditions.append(pgsql.SQL("entry_key = {}").format(pgsql.Placeholder()))
            params.append(entry_key)
        where = (
            pgsql.SQL("WHERE {}").format(pgsql.SQL(" AND ").join(conditions))
            if conditions
            else pgsql.SQL("")
        )
        stmt = pgsql.SQL(
            "SELECT * FROM hive_feedback_events {} ORDER BY timestamp DESC LIMIT {}"
        ).format(where, pgsql.Placeholder())
        params.append(limit)
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            results = []
            for r in rows:
                d = dict(zip(col_names, r, strict=False))
                # Ensure details is a dict (may already be parsed by psycopg).
                if isinstance(d.get("details"), str):
                    try:
                        d["details"] = json.loads(d["details"])
                    except json.JSONDecodeError:
                        d["details"] = {}
                results.append(d)
            return results

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_namespaces(self) -> list[str]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT DISTINCT namespace FROM hive_memories ORDER BY namespace")
            return [row[0] for row in cur.fetchall()]

    def count_by_namespace(self) -> dict[str, int]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT namespace, COUNT(*) FROM hive_memories GROUP BY namespace")
            return {row[0]: row[1] for row in cur.fetchall()}

    def namespace_detail_list(self) -> list[dict[str, Any]]:
        """Return per-namespace entry count and last write time in a single GROUP BY query."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    namespace,
                    COUNT(*) AS entry_count,
                    COALESCE(
                        MAX(updated_at), MAX(created_at)
                    )::text AS last_write_at
                FROM hive_memories
                GROUP BY namespace
                ORDER BY namespace
                """
            )
            return [
                {
                    "namespace": row[0],
                    "entry_count": int(row[1]),
                    "last_write_at": row[2],
                }
                for row in cur.fetchall()
            ]

    def count_by_agent(self) -> dict[str, int]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT source_agent, COUNT(*) FROM hive_memories GROUP BY source_agent")
            return {row[0]: row[1] for row in cur.fetchall()}

    # ------------------------------------------------------------------
    # Write notifications (LISTEN/NOTIFY with polling fallback)
    # ------------------------------------------------------------------

    def get_write_notify_state(self) -> dict[str, Any]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT revision, updated_at FROM hive_write_notify WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                return {"revision": 0, "updated_at": ""}
            return {"revision": int(row[0]), "updated_at": str(row[1] or "")}

    def wait_for_write_notify(
        self,
        *,
        since_revision: int,
        timeout_sec: float,
        poll_interval_sec: float = 0.25,
    ) -> dict[str, Any]:
        """Wait for a new write.

        Attempts LISTEN/NOTIFY first; falls back to polling if LISTEN
        is unavailable (e.g., pooled connections that don't support it).
        """
        # Try LISTEN/NOTIFY approach first.
        try:
            return self._wait_with_listen(
                since_revision=since_revision,
                timeout_sec=timeout_sec,
            )
        except Exception:  # noqa: BLE001 — LISTEN/NOTIFY errors are heterogeneous; fallback to polling is safe
            # Fallback to polling.
            return self._wait_with_polling(
                since_revision=since_revision,
                timeout_sec=timeout_sec,
                poll_interval_sec=poll_interval_sec,
            )

    def _wait_with_listen(
        self,
        *,
        since_revision: int,
        timeout_sec: float,
    ) -> dict[str, Any]:
        """Use PostgreSQL LISTEN/NOTIFY for change detection."""
        import select as _select

        with self._cm.get_connection() as conn:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("LISTEN hive_memories_changed")

            # Check current state first.
            state = self._get_state_from_conn(conn)
            if state["revision"] > since_revision:
                return {**state, "changed": True, "timed_out": False}

            # Wait for notification.
            fd = conn.fileno()
            ready = _select.select([fd], [], [], max(0.0, timeout_sec))
            if ready[0]:
                conn.poll()
                # Drain notifications.
                while conn.notifies:
                    conn.notifies.pop(0)
                state = self._get_state_from_conn(conn)
                return {
                    **state,
                    "changed": state["revision"] > since_revision,
                    "timed_out": False,
                }

            state = self._get_state_from_conn(conn)
            return {
                **state,
                "changed": state["revision"] > since_revision,
                "timed_out": True,
            }

    def _wait_with_polling(
        self,
        *,
        since_revision: int,
        timeout_sec: float,
        poll_interval_sec: float,
    ) -> dict[str, Any]:
        """Fallback polling approach matching SQLite backend behavior."""
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        poll = max(0.05, float(poll_interval_sec))
        while time.monotonic() < deadline:
            state = self.get_write_notify_state()
            if state["revision"] > since_revision:
                return {**state, "changed": True, "timed_out": False}
            time.sleep(poll)
        state = self.get_write_notify_state()
        return {
            **state,
            "changed": state["revision"] > since_revision,
            "timed_out": True,
        }

    def _get_state_from_conn(self, conn: psycopg.Connection[Any]) -> dict[str, Any]:
        """Read write_notify state using an existing connection."""
        with conn.cursor() as cur:
            cur.execute("SELECT revision, updated_at FROM hive_write_notify WHERE id = 1")
            row = cur.fetchone()
            if row is None:
                return {"revision": 0, "updated_at": ""}
            return {"revision": int(row[0]), "updated_at": str(row[1] or "")}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._cm.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(d: dict[str, Any]) -> dict[str, Any]:
        """Normalise a row dict — ensure tags is a Python list."""
        tags = d.get("tags")
        if isinstance(tags, str):
            try:
                d["tags"] = json.loads(tags)
            except json.JSONDecodeError:
                d["tags"] = []
        elif tags is None:
            d["tags"] = []
        # Remove internal PG columns from public API.
        d.pop("search_vector", None)
        d.pop("rank", None)
        return d


# ---------------------------------------------------------------------------
# PostgreSQL Agent Registry
# ---------------------------------------------------------------------------


class PostgresAgentRegistry:
    """PostgreSQL-backed :class:`AgentRegistryBackend` implementation.

    Uses the ``agent_registry`` table created by the hive migration.
    """

    def __init__(self, connection_manager: PostgresConnectionManager) -> None:
        self._cm = connection_manager

    def register(self, agent: AgentRegistration) -> None:
        """Add or update an agent registration.

        *agent* should be an :class:`AgentRegistration` (or duck-typed equivalent).
        """
        now = datetime.now(tz=UTC).isoformat()
        skills = json.dumps(getattr(agent, "skills", []))
        groups = json.dumps(getattr(agent, "groups", []))
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                    INSERT INTO agent_registry
                        (id, name, profile, skills, project_root, groups,
                         registered_at, last_seen_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        profile = EXCLUDED.profile,
                        skills = EXCLUDED.skills,
                        project_root = EXCLUDED.project_root,
                        groups = EXCLUDED.groups,
                        last_seen_at = EXCLUDED.last_seen_at
                """,
                (
                    agent.id,
                    getattr(agent, "name", ""),
                    getattr(agent, "profile", "repo-brain"),
                    skills,
                    getattr(agent, "project_root", None),
                    groups,
                    now,
                    now,
                ),
            )
        logger.info("hive.pg.agent_registered", agent_id=agent.id)

    def unregister(self, agent_id: str) -> bool:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM agent_registry WHERE id = %s", (agent_id,))
            return bool(cur.rowcount > 0)

    def get(self, agent_id: str) -> dict[str, Any] | None:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM agent_registry WHERE id = %s", (agent_id,))
            row = cur.fetchone()
            if row is None:
                return None
            col_names = [desc[0] for desc in cur.description]
            d = dict(zip(col_names, row, strict=False))
            # Parse JSONB fields if they come back as strings.
            for jf in ("skills", "groups"):
                if isinstance(d.get(jf), str):
                    d[jf] = json.loads(d[jf])
            return d

    def list_agents(self) -> list[Any]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM agent_registry ORDER BY id")
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            results = []
            for r in rows:
                d = dict(zip(col_names, r, strict=False))
                for jf in ("skills", "groups"):
                    if isinstance(d.get(jf), str):
                        d[jf] = json.loads(d[jf])
                results.append(d)
            return results

    def agents_for_domain(self, domain_name: str) -> list[Any]:
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agent_registry WHERE profile = %s ORDER BY id",
                (domain_name,),
            )
            rows = cur.fetchall()
            col_names = [desc[0] for desc in cur.description]
            results = []
            for r in rows:
                d = dict(zip(col_names, r, strict=False))
                for jf in ("skills", "groups"):
                    if isinstance(d.get(jf), str):
                        d[jf] = json.loads(d[jf])
                results.append(d)
            return results
