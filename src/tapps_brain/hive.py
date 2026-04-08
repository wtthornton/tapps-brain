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
import time
from collections.abc import Sequence  # noqa: TC003
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import types

    from tapps_brain.store import MemoryStore

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tapps_brain.agent_scope import hive_group_name_from_scope
from tapps_brain.sqlcipher_util import connect_sqlite, resolve_hive_encryption_key

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

    def __init__(self, db_path: Path | None = None, *, encryption_key: str | None = None) -> None:
        self._db_path = db_path or (_DEFAULT_HIVE_DIR / "hive.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._encryption_key = resolve_hive_encryption_key(encryption_key)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Connection & Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(
            self._db_path,
            encryption_key=self._encryption_key,
            check_same_thread=False,
        )

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
                        content_rowid=rowid,
                        tokenize='porter unicode61'
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

            # Group tables (040.21)
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS hive_groups (
                    name        TEXT    PRIMARY KEY,
                    description TEXT    NOT NULL DEFAULT '',
                    created_at  TEXT    NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hive_group_members (
                    group_name  TEXT    NOT NULL REFERENCES hive_groups(name),
                    agent_id    TEXT    NOT NULL,
                    role        TEXT    NOT NULL DEFAULT 'member',
                    joined_at   TEXT    NOT NULL,
                    PRIMARY KEY (group_name, agent_id)
                );

                -- Monotonic revision for pub-sub / watch (GitHub #12); single row id=1.
                CREATE TABLE IF NOT EXISTS hive_write_notify (
                    id          INTEGER PRIMARY KEY CHECK (id = 1),
                    revision    INTEGER NOT NULL DEFAULT 0,
                    updated_at  TEXT    NOT NULL DEFAULT ''
                );
            """)

            self._conn.execute(
                "INSERT OR IGNORE INTO hive_write_notify (id, revision, updated_at) "
                "VALUES (1, 0, '')"
            )

            self._conn.commit()

            # Forward migrations — run unconditionally; IF NOT EXISTS guards make
            # them idempotent so re-running on an already-migrated DB is safe.
            self._migrate_add_memory_group()

    def _migrate_add_memory_group(self) -> None:
        """Add ``memory_group`` column to ``hive_memories`` if not present (multi-scope / Hive)."""
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(hive_memories)").fetchall()}
        if "memory_group" not in cols:
            self._conn.execute("ALTER TABLE hive_memories ADD COLUMN memory_group TEXT")
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
    # Write notifications (GitHub #12 — lightweight pub-sub / watch)
    # ------------------------------------------------------------------

    def _write_sidecar_notify_locked(self, revision: int, updated_at: str) -> None:
        """Update ``~/.tapps-brain/hive/.hive_write_notify`` for file-based watchers."""
        path = self._db_path.parent / ".hive_write_notify"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{revision}\n{updated_at}\n", encoding="utf-8")

    def get_write_notify_state(self) -> dict[str, Any]:
        """Return monotonic ``revision`` and ``updated_at`` for hive memory writes."""
        with self._lock:
            row = self._conn.execute(
                "SELECT revision, updated_at FROM hive_write_notify WHERE id = 1"
            ).fetchone()
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
        """Block until ``revision > since_revision`` or *timeout_sec* elapses."""
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
        memory_group: str | None = None,
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
                        memory_group=memory_group,
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
                    memory_group=memory_group,
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
                memory_group=memory_group,
            )

    def patch_confidence(
        self,
        *,
        namespace: str,
        key: str,
        confidence: float,
    ) -> bool:
        """Update confidence in place (EPIC-031 cross-project flywheel)."""
        now = datetime.now(tz=UTC).isoformat()
        c = max(0.05, min(1.0, float(confidence)))
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE hive_memories SET confidence = ?, updated_at = ?
                WHERE namespace = ? AND key = ?
                """,
                (c, now, namespace, key),
            )
            if cur.rowcount > 0:
                self._conn.execute(
                    "UPDATE hive_write_notify SET revision = revision + 1, updated_at = ? "
                    "WHERE id = 1",
                    (now,),
                )
            self._conn.commit()
            changed = cur.rowcount > 0
            if changed:
                n_row = self._conn.execute(
                    "SELECT revision, updated_at FROM hive_write_notify WHERE id = 1"
                ).fetchone()
                if n_row is not None:
                    self._write_sidecar_notify_locked(int(n_row[0]), str(n_row[1] or ""))
            return changed

    def get_confidence(self, *, namespace: str, key: str) -> float | None:
        """Return current confidence for a Hive row, if it exists."""
        with self._lock:
            row = self._conn.execute(
                "SELECT confidence FROM hive_memories WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        if row is None:
            return None
        return float(row["confidence"])

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
        memory_group: str | None = None,
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
            memory_group=memory_group,
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
        memory_group: str | None = None,
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
             valid_at, invalid_at, superseded_by, memory_group)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                memory_group,
            ),
        )
        self._conn.execute(
            "UPDATE hive_write_notify SET revision = revision + 1, updated_at = ? WHERE id = 1",
            (now,),
        )
        self._conn.commit()

        n_row = self._conn.execute(
            "SELECT revision, updated_at FROM hive_write_notify WHERE id = 1"
        ).fetchone()
        if n_row is not None:
            self._write_sidecar_notify_locked(int(n_row[0]), str(n_row[1] or ""))

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
            "memory_group": memory_group,
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
        """Search across namespaces using FTS5.

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
                        FROM hive_fts
                        JOIN hive_memories hm ON hive_fts.rowid = hm.rowid
                        WHERE hive_fts MATCH ?
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
                        FROM hive_fts
                        JOIN hive_memories hm ON hive_fts.rowid = hm.rowid
                        WHERE hive_fts MATCH ?
                        AND hm.confidence >= ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (query, min_confidence, limit),
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []

        return [self._row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Groups (040.21)
    # ------------------------------------------------------------------

    def create_group(self, name: str, description: str = "") -> dict[str, Any]:
        """Create a new agent group."""
        now = datetime.now(tz=UTC).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO hive_groups (name, description, created_at) "
                "VALUES (?, ?, ?)",
                (name, description, now),
            )
            self._conn.commit()
        logger.info("hive.group.created", name=name)
        return {"name": name, "description": description, "created_at": now}

    def add_group_member(self, group_name: str, agent_id: str, role: str = "member") -> bool:
        """Add an agent to a group. Returns True on success."""
        now = datetime.now(tz=UTC).isoformat()
        with self._lock:
            # Ensure group exists
            row = self._conn.execute(
                "SELECT name FROM hive_groups WHERE name = ?", (group_name,)
            ).fetchone()
            if row is None:
                logger.warning("hive.group.not_found", group_name=group_name)
                return False
            self._conn.execute(
                "INSERT OR REPLACE INTO hive_group_members (group_name, agent_id, role, joined_at) "
                "VALUES (?, ?, ?, ?)",
                (group_name, agent_id, role, now),
            )
            self._conn.commit()
        logger.info("hive.group.member_added", group_name=group_name, agent_id=agent_id, role=role)
        return True

    def remove_group_member(self, group_name: str, agent_id: str) -> bool:
        """Remove an agent from a group. Returns True if membership existed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM hive_group_members WHERE group_name = ? AND agent_id = ?",
                (group_name, agent_id),
            )
            self._conn.commit()
        removed = cur.rowcount > 0
        if removed:
            logger.info("hive.group.member_removed", group_name=group_name, agent_id=agent_id)
        return removed

    def list_groups(self) -> list[dict[str, Any]]:
        """List all groups."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM hive_groups ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def get_group_members(self, group_name: str) -> list[dict[str, Any]]:
        """Get members of a group."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hive_group_members WHERE group_name = ? ORDER BY joined_at",
                (group_name,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_agent_groups(self, agent_id: str) -> list[str]:
        """Get all group names an agent belongs to."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT group_name FROM hive_group_members WHERE agent_id = ? ORDER BY group_name",
                (agent_id,),
            ).fetchall()
        return [row[0] for row in rows]

    def agent_is_group_member(self, group_name: str, agent_id: str) -> bool:
        """Return True if *agent_id* is registered in *group_name*."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM hive_group_members WHERE group_name = ? AND agent_id = ?",
                (group_name, agent_id),
            ).fetchone()
        return row is not None

    def search_with_groups(
        self,
        query: str,
        agent_id: str,
        agent_namespace: str | None = None,
        **kwargs: Any,  # noqa: ANN401
    ) -> list[dict[str, Any]]:
        """Search across agent's own namespace + group namespaces + universal.

        The agent's own namespace defaults to ``agent_id`` when not supplied.
        Group memories are stored with namespace = group name.
        """
        own_ns = agent_namespace or agent_id
        group_names = self.get_agent_groups(agent_id)
        namespaces = list({own_ns, *group_names, "universal"})
        return self.search(query, namespaces=namespaces, **kwargs)

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

    def count_by_agent(self) -> dict[str, int]:
        """Return a mapping of source_agent → entry count across all namespaces.

        Used by ``hive status`` to show how many entries each agent has
        contributed, regardless of namespace (universal or domain).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_agent, COUNT(*) FROM hive_memories GROUP BY source_agent"
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
    - ``group:<name>`` → saved to Hive namespace *name* when *agent_id* is a
      member of that group (see ``HiveStore.create_group`` / ``add_group_member``)

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
        bypass_profile_hive_rules: bool = False,
        dry_run: bool = False,
        memory_group: str | None = None,
    ) -> dict[str, Any] | None:
        """Propagate a memory entry to the Hive if appropriate.

        Returns the saved Hive entry dict, or None if the entry stayed private.
        When *dry_run* is True, does not write to SQLite; returns a minimal dict
        with ``namespace`` and ``key`` if propagation would occur.

        *bypass_profile_hive_rules*: when True, ignore *private_tiers* and
        *auto_propagate_tiers* so explicit *agent_scope* from the caller wins
        (used for CLI/MCP batch push — GitHub #18).
        """
        effective_scope = agent_scope

        if not bypass_profile_hive_rules:
            # Private tiers override everything
            if private_tiers and tier in private_tiers:
                effective_scope = "private"
            # Auto-propagation for configured tiers
            elif (
                auto_propagate_tiers
                and tier in auto_propagate_tiers
                and effective_scope == "private"
            ):
                effective_scope = "domain"

        if effective_scope == "private":
            return None

        group_ns = hive_group_name_from_scope(effective_scope)
        if group_ns is not None:
            if not hive_store.agent_is_group_member(group_ns, agent_id):
                logger.warning(
                    "hive.propagate.group_denied",
                    group_name=group_ns,
                    agent_id=agent_id,
                    key=key,
                    reason="not_a_member",
                )
                return None
            namespace = group_ns
        elif effective_scope == "hive":
            namespace = "universal"
        elif effective_scope == "domain":
            namespace = agent_profile
        else:
            logger.warning(
                "hive.propagate.unknown_scope",
                effective_scope=effective_scope,
                agent_id=agent_id,
                key=key,
                fallback="domain",
            )
            namespace = agent_profile

        if dry_run:
            logger.debug(
                "hive.propagate_dry_run",
                key=key,
                namespace=namespace,
                agent_id=agent_id,
            )
            return {"namespace": namespace, "key": key, "dry_run": True}

        result = hive_store.save(
            key=key,
            value=value,
            namespace=namespace,
            source_agent=agent_id,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
            memory_group=memory_group,
        )

        logger.info(
            "hive.propagated",
            key=key,
            scope=effective_scope,
            namespace=namespace,
            agent_id=agent_id,
        )

        return result


def select_local_entries_for_hive_push(
    store: MemoryStore,
    *,
    push_all: bool = False,
    tags: list[str] | None = None,
    tier: str | None = None,
    keys: list[str] | None = None,
    include_superseded: bool = False,
) -> list[Any]:
    """Select project memories for batch Hive push (GitHub #18).

    *keys*: explicit entry keys (highest priority). *push_all*: all entries,
    optionally narrowed by *tier* and/or *tags*. Otherwise require at least one
    of *tier* or *tags*.

    Raises:
        ValueError: When selection criteria are empty or *tier* is invalid.
    """
    from tapps_brain.models import MemoryTier

    if keys:
        resolved: list[Any] = []
        for k in keys:
            entry = store.get(k)
            if entry is not None:
                resolved.append(entry)
        return resolved

    tier_enum: MemoryTier | None = None
    if tier is not None:
        try:
            tier_enum = MemoryTier(tier)
        except ValueError as exc:
            msg = f"Unknown tier '{tier}'"
            raise ValueError(msg) from exc

    if push_all:
        return store.list_all(
            tier=tier_enum,
            tags=tags,
            include_superseded=include_superseded,
        )

    if tier_enum is None and not tags:
        msg = "Specify keys, push_all=True, and/or tier/tags filters"
        raise ValueError(msg)

    return store.list_all(
        tier=tier_enum,
        tags=tags,
        include_superseded=include_superseded,
    )


def push_memory_entries_to_hive(
    entries: Sequence[Any],
    *,
    hive_store: HiveStore,
    agent_id: str,
    agent_profile: str,
    agent_scope: str,
    auto_propagate_tiers: list[str] | None = None,
    private_tiers: list[str] | None = None,
    bypass_profile_hive_rules: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push local *entries* to the Hive using :meth:`PropagationEngine.propagate`.

    Returns a JSON-serializable report: ``pushed``, ``skipped``, ``failed``,
    each a list of per-key records. *skipped* means propagation returned
    ``None`` (would stay private under current rules).
    """
    from tapps_brain.models import MemorySource, tier_str

    pushed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for entry in entries:
        tier_val = tier_str(entry.tier)
        src = entry.source
        source_val = src.value if isinstance(src, MemorySource) else str(src)
        try:
            result = PropagationEngine.propagate(
                key=entry.key,
                value=entry.value,
                agent_scope=agent_scope,
                agent_id=agent_id,
                agent_profile=agent_profile,
                tier=tier_val,
                confidence=float(entry.confidence),
                source=source_val,
                tags=entry.tags,
                hive_store=hive_store,
                auto_propagate_tiers=auto_propagate_tiers,
                private_tiers=private_tiers,
                bypass_profile_hive_rules=bypass_profile_hive_rules,
                dry_run=dry_run,
                memory_group=entry.memory_group,
            )
        except Exception as exc:
            logger.warning(
                "hive.push_entry_failed",
                key=entry.key,
                error=str(exc),
                exc_info=True,
            )
            failed.append({"key": entry.key, "error": str(exc)})
            continue
        if result is None:
            skipped.append({"key": entry.key, "reason": "not_propagated_private_rules"})
        else:
            ns = str(result.get("namespace", ""))
            pushed.append({"key": entry.key, "namespace": ns})

    return {
        "dry_run": dry_run,
        "agent_scope": agent_scope,
        "count_selected": len(entries),
        "count_pushed": len(pushed),
        "count_skipped": len(skipped),
        "count_failed": len(failed),
        "pushed": pushed,
        "skipped": skipped,
        "failed": failed,
    }
