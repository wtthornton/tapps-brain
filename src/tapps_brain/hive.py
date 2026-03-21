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
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field

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
            agent = AgentRegistration(**agent_data)
            self._agents[agent.id] = agent

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

            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

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

        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM hive_memories "
                "WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()

        if existing is not None:
            resolved = self._resolve_conflict(
                policy=policy,
                existing=dict(existing),
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
                return self._supersede_existing(
                    existing=dict(existing),
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

        # Normal write (no conflict, or last_write_wins)
        return self._write_entry(
            key=key,
            value=value,
            namespace=namespace,
            source_agent=source_agent,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
            valid_at=valid_at,
            invalid_at=invalid_at,
            superseded_by=superseded_by,
            now=now,
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
        """Apply conflict policy. Returns action or None (reject)."""
        if policy == ConflictPolicy.last_write_wins:
            return "overwrite"

        if policy == ConflictPolicy.source_authority:
            # Reject if the source agent doesn't match namespace
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

    def _supersede_existing(
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
        """Mark old version invalid, write new version."""
        # Mark old entry as superseded
        new_key = f"{key}-v{now.replace(':', '').replace('-', '')[:15]}"
        with self._lock:
            self._conn.execute(
                "UPDATE hive_memories SET invalid_at = ?, superseded_by = ? "
                "WHERE namespace = ? AND key = ?",
                (now, new_key, namespace, key),
            )
            self._conn.commit()

        # Write new version
        return self._write_entry(
            key=new_key,
            value=value,
            namespace=namespace,
            source_agent=source_agent,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
            valid_at=now,
            invalid_at=None,
            superseded_by=None,
            now=now,
        )

    def _write_entry(
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
        valid_at: str | None,
        invalid_at: str | None,
        superseded_by: str | None,
        now: str,
    ) -> dict[str, Any]:
        """Perform the actual INSERT OR REPLACE and return the entry dict."""
        tags_json = json.dumps(tags or [])

        with self._lock:
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
                    now,
                    now,
                    valid_at,
                    invalid_at,
                    superseded_by,
                ),
            )
            # Rebuild FTS index
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute(
                    "INSERT INTO hive_fts(hive_fts) VALUES('rebuild')"
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
            "created_at": now,
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
                    (query, min_confidence, limit * 2),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 fallback: simple LIKE search
                rows = self._conn.execute(
                    """
                    SELECT *, 0.0 as rank
                    FROM hive_memories
                    WHERE (key LIKE ? OR value LIKE ?)
                    AND confidence >= ?
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", min_confidence, limit * 2),
                ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            d = self._row_to_dict(row)
            if namespaces and d["namespace"] not in namespaces:
                continue
            results.append(d)

        return results[:limit]

    def list_namespaces(self) -> list[str]:
        """Return distinct namespace names that have at least one entry."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT namespace FROM hive_memories ORDER BY namespace"
            ).fetchall()
        return [row[0] for row in rows]


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
        elif (
            auto_propagate_tiers
            and tier in auto_propagate_tiers
            and effective_scope == "private"
        ):
            effective_scope = "domain"

        if effective_scope == "private":
            return None

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
