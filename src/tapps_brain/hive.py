"""Multi-agent shared brain with domain namespaces.

The Hive enables memory sharing across agents on the same machine via
a central SQLite store at ``~/.tapps-brain/hive/hive.db``.  Agents write
to namespaces (``universal`` for hive-wide, or a domain name matching
the agent's profile) and read across namespaces with configurable weighting.

EPIC-011 — backward compatible: single-agent behavior is unchanged
when the Hive is disabled (the default).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_DEFAULT_HIVE_DIR = Path.home() / ".tapps-brain" / "hive"


# ---------------------------------------------------------------------------
# Conflict Resolution (011-G)
# ---------------------------------------------------------------------------


class ConflictPolicy(StrEnum):
    """How the Hive resolves conflicting writes for the same (namespace, key)."""

    supersede = "supersede"
    source_authority = "source_authority"
    confidence_max = "confidence_max"
    last_write_wins = "last_write_wins"


# ---------------------------------------------------------------------------
# Agent Registration (011-C)
# ---------------------------------------------------------------------------


class AgentRegistration(BaseModel):
    """A registered agent in the Hive."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique agent identifier (slug).")
    name: str = Field(default="", description="Human-readable agent name.")
    profile: str = Field(
        default="repo-brain",
        description="Memory profile name (determines domain namespace).",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Skills this agent provides (e.g. ['code-review', 'testing']).",
    )
    project_root: str | None = Field(
        default=None,
        description="Absolute path to the agent's project root (if project-scoped).",
    )


class AgentRegistry:
    """YAML-backed registry of agents participating in the Hive.

    Persisted at ``~/.tapps-brain/hive/agents.yaml``.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self._path = registry_path or (_DEFAULT_HIVE_DIR / "agents.yaml")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentRegistration] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "agents" not in raw:
            return
        for agent_data in raw["agents"]:
            try:
                agent = AgentRegistration(**agent_data)
                self._agents[agent.id] = agent
            except (ValidationError, TypeError) as exc:
                logger.warning(
                    "hive.agent_registry.load_skipped",
                    agent_data=agent_data,
                    error=str(exc),
                )

    def _save(self) -> None:
        data = {"agents": [a.model_dump(mode="json") for a in self._agents.values()]}
        self._path.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def register(self, agent: AgentRegistration) -> None:
        """Add or update an agent registration."""
        self._agents[agent.id] = agent
        self._save()
        logger.info("hive.agent_registered", agent_id=agent.id, profile=agent.profile)

    def unregister(self, agent_id: str) -> bool:
        """Remove an agent. Returns True if it existed."""
        if agent_id not in self._agents:
            return False
        del self._agents[agent_id]
        self._save()
        logger.info("hive.agent_unregistered", agent_id=agent_id)
        return True

    def get(self, agent_id: str) -> AgentRegistration | None:
        """Look up an agent by ID."""
        return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentRegistration]:
        """Return all registered agents."""
        return list(self._agents.values())

    def agents_for_domain(self, domain_name: str) -> list[AgentRegistration]:
        """Return agents whose profile matches the given domain name."""
        return [a for a in self._agents.values() if a.profile == domain_name]


class HiveStore:
    """Central hub for cross-agent memory sharing.

    Uses a SQLite database at ``~/.tapps-brain/hive/hive.db`` with
    WAL mode, FTS5 full-text search, and namespace-aware schema.
    Thread-safe via ``threading.Lock``.

    The primary key is ``(namespace, key)`` — the same key can exist
    in different namespaces without conflict.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or (_DEFAULT_HIVE_DIR / "hive.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection & Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS hive_memories (
                    namespace   TEXT    NOT NULL DEFAULT 'universal',
                    key         TEXT    NOT NULL,
                    value       TEXT    NOT NULL,
                    tier        TEXT    NOT NULL DEFAULT 'pattern',
                    confidence  REAL    NOT NULL DEFAULT 0.6,
                    source      TEXT    NOT NULL DEFAULT 'agent',
                    source_agent TEXT   NOT NULL DEFAULT 'unknown',
                    tags        TEXT    NOT NULL DEFAULT '[]',
                    created_at  TEXT    NOT NULL,
                    updated_at  TEXT    NOT NULL,
                    valid_at    TEXT,
                    invalid_at  TEXT,
                    superseded_by TEXT,
                    PRIMARY KEY (namespace, key)
                );

                CREATE INDEX IF NOT EXISTS idx_hive_namespace
                    ON hive_memories(namespace);
                CREATE INDEX IF NOT EXISTS idx_hive_confidence
                    ON hive_memories(confidence);
                CREATE INDEX IF NOT EXISTS idx_hive_tier
                    ON hive_memories(tier);
                CREATE INDEX IF NOT EXISTS idx_hive_source_agent
                    ON hive_memories(source_agent);

                CREATE TABLE IF NOT EXISTS hive_feedback_events (
                    id              TEXT    NOT NULL PRIMARY KEY,
                    namespace       TEXT    NOT NULL,
                    entry_key       TEXT,
                    event_type      TEXT    NOT NULL,
                    session_id      TEXT,
                    utility_score   REAL,
                    details         TEXT    NOT NULL DEFAULT '{}',
                    timestamp       TEXT    NOT NULL,
                    source_project  TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_hive_fb_namespace
                    ON hive_feedback_events(namespace);
                CREATE INDEX IF NOT EXISTS idx_hive_fb_entry_key
                    ON hive_feedback_events(entry_key);
                CREATE INDEX IF NOT EXISTS idx_hive_fb_event_type
                    ON hive_feedback_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_hive_fb_timestamp
                    ON hive_feedback_events(timestamp);
            """)

            # FTS5 table — created separately (can't use executescript reliably)
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS hive_fts
                    USING fts5(
                        key, value, tags,
                        content=hive_memories,
                        content_rowid=rowid
                    )
                """)

            # FTS5 sync triggers — O(1) incremental updates instead of full rebuild.
            # INSERT OR REPLACE fires DELETE then INSERT, so these triggers keep
            # the FTS index in sync automatically on every write.
            _fts_triggers = [
                """CREATE TRIGGER IF NOT EXISTS hive_fts_ai
                   AFTER INSERT ON hive_memories BEGIN
                       INSERT INTO hive_fts(rowid, key, value, tags)
                       VALUES (new.rowid, new.key, new.value, new.tags);
                   END""",
                """CREATE TRIGGER IF NOT EXISTS hive_fts_ad
                   AFTER DELETE ON hive_memories BEGIN
                       INSERT INTO hive_fts(hive_fts, rowid, key, value, tags)
                       VALUES ('delete', old.rowid, old.key, old.value, old.tags);
                   END""",
                """CREATE TRIGGER IF NOT EXISTS hive_fts_au
                   AFTER UPDATE ON hive_memories BEGIN
                       INSERT INTO hive_fts(hive_fts, rowid, key, value, tags)
                       VALUES ('delete', old.rowid, old.key, old.value, old.tags);
                       INSERT INTO hive_fts(rowid, key, value, tags)
                       VALUES (new.rowid, new.key, new.value, new.tags);
                   END""",
            ]
            for trigger_sql in _fts_triggers:
                with contextlib.suppress(sqlite3.OperationalError):
                    self._conn.execute(trigger_sql)

            self._conn.commit()

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
        """Persist a feedback event mirrored from a project store (EPIC-029).

        Thread-safe.  Raises ``sqlite3.Error`` on database failure — callers
        that need failure tolerance should catch and log.
        """
        payload = json.dumps(details, default=str)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO hive_feedback_events (
                    id, namespace, entry_key, event_type, session_id,
                    utility_score, details, timestamp, source_project
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    namespace,
                    entry_key,
                    event_type,
                    session_id,
                    utility_score,
                    payload,
                    timestamp,
                    source_project,
                ),
            )
            self._conn.commit()

    def query_feedback_events(
        self,
        *,
        namespace: str | None = None,
        entry_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return recent hive feedback rows (newest first)."""
        clauses: list[str] = []
        params: list[Any] = []
        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if entry_key is not None:
            clauses.append("entry_key = ?")
            params.append(entry_key)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM hive_feedback_events {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            raw_details = d.get("details", "{}")
            try:
                d["details"] = json.loads(raw_details) if isinstance(raw_details, str) else {}
            except json.JSONDecodeError:
                d["details"] = {}
            out.append(d)
        return out

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> HiveStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Convert a sqlite3.Row to a dict, deserializing JSON tags."""
        d = dict(row)
        d["tags"] = json.loads(d.get("tags", "[]"))
        return d

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
        conflict_policy: ConflictPolicy | str = ConflictPolicy.supersede,
    ) -> dict[str, Any] | None:
        """Save a memory entry to the Hive.

        When an entry already exists at ``(namespace, key)``, the
        *conflict_policy* determines what happens:

        - ``supersede`` (default): marks old version with ``invalid_at``,
          saves new version with a versioned key.
        - ``source_authority``: rejects the write if the source agent's
          profile doesn't match the namespace domain.
        - ``confidence_max``: keeps the version with higher confidence.
        - ``last_write_wins``: overwrites unconditionally.

        Returns the saved entry dict, or ``None`` if the write was rejected.
        """
        policy = ConflictPolicy(conflict_policy)
        now = datetime.now(tz=UTC).isoformat()

        # Hold the lock for the entire read-then-write sequence to prevent
        # TOCTOU races where two concurrent saves to the same (namespace, key)
        # could both read "no existing entry" and then both write.
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM hive_memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()

            if existing is not None:
                existing_dict = dict(existing)
                resolved = self._resolve_conflict(
                    policy=policy,
                    existing=existing_dict,
                    new_value=value,
                    new_confidence=confidence,
                    source_agent=source_agent,
                    namespace=namespace,
                    key=key,
                    now=now,
                )
                if resolved is None:
                    return None
                if resolved == "supersede_version":
                    return self._supersede_existing_locked(
                        existing=existing_dict,
                        key=key,
                        value=value,
                        namespace=namespace,
                        source_agent=source_agent,
                        tier=tier,
                        confidence=confidence,
                        source=source,
                        tags=tags,
                        now=now,
                    )
                # "overwrite" path — preserve original created_at
                return self._write_entry_locked(
                    key=key,
                    value=value,
                    namespace=namespace,
                    source_agent=source_agent,
                    tier=tier,
                    confidence=confidence,
                    source=source,
                    tags=tags,
                    created_at=existing_dict.get("created_at", now),
                    now=now,
                    valid_at=valid_at,
                    invalid_at=invalid_at,
                    superseded_by=superseded_by,
                )

            # No existing entry — normal write
            return self._write_entry_locked(
                key=key,
                value=value,
                namespace=namespace,
                source_agent=source_agent,
                tier=tier,
                confidence=confidence,
                source=source,
                tags=tags,
                created_at=now,
                now=now,
                valid_at=valid_at,
                invalid_at=invalid_at,
                superseded_by=superseded_by,
            )

    def _resolve_conflict(
        self,
        *,
        policy: ConflictPolicy,
        existing: dict[str, Any],
        new_value: str,
        new_confidence: float,
        source_agent: str,
        namespace: str,
        key: str,
        now: str,
    ) -> str | None:
        """Apply conflict policy.

        Returns:
            ``"overwrite"``: caller should update in-place (preserves ``created_at``).
            ``"supersede_version"``: caller should mark old version invalid and write
                a new versioned key.
            ``None``: write rejected — caller should return ``None`` to the caller.
        """
        if policy == ConflictPolicy.last_write_wins:
            return "overwrite"

        if policy == ConflictPolicy.source_authority:
            # Reject if the new writer differs from the agent that made the original
            # write.  The semantics are "only the original author may update this
            # entry"; comparing source_agent IDs is the correct check here.
            if source_agent != existing.get("source_agent", ""):
                logger.warning(
                    "hive.conflict.source_authority_rejected",
                    key=key,
                    namespace=namespace,
                    source_agent=source_agent,
                    existing_agent=existing.get("source_agent"),
                )
                return None
            return "overwrite"

        if policy == ConflictPolicy.confidence_max:
            old_confidence = existing.get("confidence", 0.0)
            if new_confidence <= old_confidence:
                logger.info(
                    "hive.conflict.confidence_max_kept_existing",
                    key=key,
                    new_confidence=new_confidence,
                    old_confidence=old_confidence,
                )
                return None
            return "overwrite"

        # supersede (default)
        return "supersede_version"

    def _supersede_existing_locked(
        self,
        *,
        existing: dict[str, Any],
        key: str,
        value: str,
        namespace: str,
        source_agent: str,
        tier: str,
        confidence: float,
        source: str,
        tags: list[str] | None,
        now: str,
    ) -> dict[str, Any]:
        """Mark old version invalid and write new version. Caller must hold ``self._lock``."""
        # Include microseconds (22 chars) so that two rapid supersedes on the
        # same key within the same second produce distinct versioned keys.
        # Pattern: "20260323T123456.123456" from "2026-03-23T12:34:56.123456+00:00".
        new_key = f"{key}-v{now.replace(':', '').replace('-', '').replace('+', '')[:22]}"
        self._conn.execute(
            "UPDATE hive_memories SET invalid_at = ?, superseded_by = ? "
            "WHERE namespace = ? AND key = ?",
            (now, new_key, namespace, key),
        )
        self._conn.commit()
        return self._write_entry_locked(
            key=new_key,
            value=value,
            namespace=namespace,
            source_agent=source_agent,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
            created_at=now,
            now=now,
            valid_at=now,
            invalid_at=None,
            superseded_by=None,
        )

    def _write_entry_locked(
        self,
        *,
        key: str,
        value: str,
        namespace: str,
        source_agent: str,
        tier: str,
        confidence: float,
        source: str,
        tags: list[str] | None,
        created_at: str,
        now: str,
        valid_at: str | None,
        invalid_at: str | None,
        superseded_by: str | None,
    ) -> dict[str, Any]:
        """Perform the actual INSERT OR REPLACE and return the entry dict.

        Caller must hold ``self._lock``. ``created_at`` is passed explicitly so
        that update paths (overwrite/supersede) can preserve the original
        creation timestamp instead of resetting it to ``now``.
        """
        tags_json = json.dumps(tags or [])
        self._conn.execute(
            """
            INSERT OR REPLACE INTO hive_memories
            (namespace, key, value, tier, confidence, source,
             source_agent, tags, created_at, updated_at,
             valid_at, invalid_at, superseded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                namespace,
                key,
                value,
                tier,
                confidence,
                source,
                source_agent,
                tags_json,
                created_at,
                now,
                valid_at,
                invalid_at,
                superseded_by,
            ),
        )
        self._conn.commit()

        logger.info(
            "hive.saved",
            key=key,
            namespace=namespace,
            source_agent=source_agent,
        )

        return {
            "namespace": namespace,
            "key": key,
            "value": value,
            "tier": tier,
            "confidence": confidence,
            "source": source,
            "source_agent": source_agent,
            "tags": tags or [],
            "created_at": created_at,
            "updated_at": now,
            "valid_at": valid_at,
            "invalid_at": invalid_at,
            "superseded_by": superseded_by,
        }

    def get(self, key: str, namespace: str = "universal") -> dict[str, Any] | None:
        """Retrieve a single entry by key within a namespace."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hive_memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search across namespaces using FTS5, falling back to LIKE.

        Args:
            query: Full-text search query.
            namespaces: Limit search to these namespaces (None = all).
            min_confidence: Minimum confidence threshold.
            limit: Maximum results.

        Returns:
            List of matching entries as dicts.
        """
        with self._lock:
            try:
                if namespaces:
                    placeholders = ",".join("?" * len(namespaces))
                    rows = self._conn.execute(
                        f"""
                        SELECT hm.*, rank
                        FROM hive_fts fts
                        JOIN hive_memories hm ON fts.rowid = hm.rowid
                        WHERE fts MATCH ?
                        AND hm.confidence >= ?
                        AND hm.namespace IN ({placeholders})
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (query, min_confidence, *namespaces, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """
                        SELECT hm.*, rank
                        FROM hive_fts fts
                        JOIN hive_memories hm ON fts.rowid = hm.rowid
                        WHERE fts MATCH ?
                        AND hm.confidence >= ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (query, min_confidence, limit),
                    ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 fallback: simple LIKE search
                if namespaces:
                    placeholders = ",".join("?" * len(namespaces))
                    rows = self._conn.execute(
                        f"""
                        SELECT *, 0.0 as rank
                        FROM hive_memories
                        WHERE (key LIKE ? OR value LIKE ?)
                        AND confidence >= ?
                        AND namespace IN ({placeholders})
                        LIMIT ?
                        """,
                        (f"%{query}%", f"%{query}%", min_confidence, *namespaces, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """
                        SELECT *, 0.0 as rank
                        FROM hive_memories
                        WHERE (key LIKE ? OR value LIKE ?)
                        AND confidence >= ?
                        LIMIT ?
                        """,
                        (f"%{query}%", f"%{query}%", min_confidence, limit),
                    ).fetchall()

        return [self._row_to_dict(row) for row in rows]

    def list_namespaces(self) -> list[str]:
        """Return distinct namespace names that have at least one entry."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT namespace FROM hive_memories ORDER BY namespace"
            ).fetchall()
        return [row[0] for row in rows]

    def count_by_namespace(self) -> dict[str, int]:
        """Return a mapping of namespace → entry count for all namespaces."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT namespace, COUNT(*) FROM hive_memories GROUP BY namespace"
            ).fetchall()
        return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------------------
# Propagation Engine (011-E)
# ---------------------------------------------------------------------------


class PropagationEngine:
    """Routes memory entries to the Hive based on ``agent_scope``.

    - ``private`` → stays local (no propagation)
    - ``domain`` → saved to Hive namespace matching the agent's profile name
    - ``hive`` → saved to the ``universal`` namespace

    Auto-propagation: if the entry's tier is in the profile's
    ``hive.auto_propagate_tiers``, scope is upgraded to ``domain``.
    If the tier is in ``hive.private_tiers``, scope is forced to ``private``.
    """

    @staticmethod
    def propagate(
        *,
        key: str,
        value: str,
        agent_scope: str,
        agent_id: str,
        agent_profile: str,
        tier: str,
        confidence: float,
        source: str,
        tags: list[str] | None,
        hive_store: HiveStore,
        auto_propagate_tiers: list[str] | None = None,
        private_tiers: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Propagate a memory entry to the Hive if appropriate.

        Returns the saved Hive entry dict, or None if the entry stayed private.
        """
        effective_scope = agent_scope

        # Private tiers override everything
        if private_tiers and tier in private_tiers:
            effective_scope = "private"
        # Auto-propagation for configured tiers
        elif auto_propagate_tiers and tier in auto_propagate_tiers and effective_scope == "private":
            effective_scope = "domain"

        if effective_scope == "private":
            return None

        if effective_scope not in ("domain", "hive"):
            logger.warning(
                "hive.propagate.unknown_scope",
                effective_scope=effective_scope,
                agent_id=agent_id,
                key=key,
                fallback="domain",
            )

        namespace = "universal" if effective_scope == "hive" else agent_profile

        result = hive_store.save(
            key=key,
            value=value,
            namespace=namespace,
            source_agent=agent_id,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
        )

        logger.info(
            "hive.propagated",
            key=key,
            scope=effective_scope,
            namespace=namespace,
            agent_id=agent_id,
        )

        return result
