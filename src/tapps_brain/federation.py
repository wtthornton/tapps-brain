"""Cross-project memory federation.

Enables memory sharing across projects via a central hub store
at ``~/.tapps-mcp/memory/federated.db``.  Projects explicitly publish
shared-scope memories to the hub and subscribe to memories from
other projects.

Epic 64 — all operations are explicit (no automatic sharing).
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry
    from tapps_brain.store import MemoryStore

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_DEFAULT_HUB_DIR = Path.home() / ".tapps-brain" / "memory"
_MAX_PROJECTS = 50
_MAX_SUBSCRIPTIONS = 50


# ---------------------------------------------------------------------------
# Configuration models
# ---------------------------------------------------------------------------


class FederationProject(BaseModel):
    """A registered project in the federation hub."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(description="Unique project identifier (slug).")
    project_root: str = Field(description="Absolute path to project root.")
    registered_at: str = Field(default="", description="ISO-8601 UTC registration time.")
    tags: list[str] = Field(default_factory=list, description="Project tags for filtering.")


class FederationSubscription(BaseModel):
    """Subscription from one project to others."""

    model_config = ConfigDict(extra="forbid")

    subscriber: str = Field(description="Subscribing project ID.")
    sources: list[str] = Field(
        default_factory=list,
        description="Source project IDs to subscribe to (empty = all).",
    )
    tag_filter: list[str] = Field(
        default_factory=list,
        description="Only import memories with these tags.",
    )
    min_confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold for imported memories.",
    )


class FederationConfig(BaseModel):
    """Root config for federation stored at ~/.tapps-mcp/memory/federation.yaml."""

    model_config = ConfigDict(extra="forbid")

    hub_path: str = Field(
        default="",
        description="Path to federated.db (default: ~/.tapps-mcp/memory/federated.db).",
    )
    projects: list[FederationProject] = Field(default_factory=list)
    subscriptions: list[FederationSubscription] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Config file management
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    """Return the path to federation.yaml."""
    return _DEFAULT_HUB_DIR / "federation.yaml"


def load_federation_config() -> FederationConfig:
    """Load federation config from disk, creating defaults if absent."""
    path = _config_path()
    if not path.exists():
        return FederationConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None or not isinstance(raw, dict):
        return FederationConfig()
    return FederationConfig(**raw)


def save_federation_config(config: FederationConfig) -> None:
    """Persist federation config to disk."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    path.write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info("federation_config.saved", path=str(path))


def register_project(
    project_id: str,
    project_root: str,
    tags: list[str] | None = None,
) -> FederationConfig:
    """Register a project in the federation hub.

    Returns the updated config.
    """
    config = load_federation_config()

    # Check for duplicates
    existing_ids = {p.project_id for p in config.projects}
    if project_id in existing_ids:
        logger.info("federation.project_already_registered", project_id=project_id)
        # Update tags if provided
        for proj in config.projects:
            if proj.project_id == project_id:
                if tags is not None:
                    proj.tags = tags
                proj.project_root = project_root
                break
        save_federation_config(config)
        return config

    if len(config.projects) >= _MAX_PROJECTS:
        msg = f"Federation hub has reached max projects ({_MAX_PROJECTS})"
        raise ValueError(msg)

    config.projects.append(
        FederationProject(
            project_id=project_id,
            project_root=project_root,
            registered_at=datetime.now(tz=UTC).isoformat(),
            tags=tags or [],
        )
    )
    save_federation_config(config)
    logger.info("federation.project_registered", project_id=project_id)
    return config


def unregister_project(project_id: str) -> FederationConfig:
    """Remove a project from the federation hub."""
    config = load_federation_config()
    config.projects = [p for p in config.projects if p.project_id != project_id]
    config.subscriptions = [s for s in config.subscriptions if s.subscriber != project_id]
    save_federation_config(config)
    logger.info("federation.project_unregistered", project_id=project_id)
    return config


def add_subscription(
    subscriber: str,
    sources: list[str] | None = None,
    tag_filter: list[str] | None = None,
    min_confidence: float = 0.5,
) -> FederationConfig:
    """Add or update a subscription for a project."""
    config = load_federation_config()

    # Validate subscriber exists
    project_ids = {p.project_id for p in config.projects}
    if subscriber not in project_ids:
        msg = f"Subscriber '{subscriber}' is not registered in the federation hub"
        raise ValueError(msg)

    # Validate sources exist
    if sources:
        unknown = set(sources) - project_ids
        if unknown:
            msg = f"Unknown source project(s): {sorted(unknown)}"
            raise ValueError(msg)

    # Remove existing subscription for this subscriber (replace)
    config.subscriptions = [s for s in config.subscriptions if s.subscriber != subscriber]

    if len(config.subscriptions) >= _MAX_SUBSCRIPTIONS:
        msg = f"Federation hub has reached max subscriptions ({_MAX_SUBSCRIPTIONS})"
        raise ValueError(msg)

    config.subscriptions.append(
        FederationSubscription(
            subscriber=subscriber,
            sources=sources or [],
            tag_filter=tag_filter or [],
            min_confidence=min_confidence,
        )
    )
    save_federation_config(config)
    logger.info(
        "federation.subscription_added",
        subscriber=subscriber,
        sources=sources or ["all"],
    )
    return config


# ---------------------------------------------------------------------------
# Federated Hub Store (SQLite)
# ---------------------------------------------------------------------------


class FederatedStore:
    """Central hub for cross-project memory federation.

    Uses a SQLite database at ``~/.tapps-mcp/memory/federated.db`` with
    composite primary key ``(project_id, key)`` and FTS5 for search.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or (_DEFAULT_HUB_DIR / "federated.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._ensure_schema()

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
                CREATE TABLE IF NOT EXISTS federated_memories (
                    project_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    tier TEXT NOT NULL DEFAULT 'pattern',
                    confidence REAL NOT NULL DEFAULT 0.6,
                    source TEXT NOT NULL DEFAULT 'agent',
                    source_agent TEXT NOT NULL DEFAULT 'unknown',
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    origin_project_root TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (project_id, key)
                );

                CREATE INDEX IF NOT EXISTS idx_fed_project
                    ON federated_memories(project_id);
                CREATE INDEX IF NOT EXISTS idx_fed_confidence
                    ON federated_memories(confidence);
                CREATE INDEX IF NOT EXISTS idx_fed_tier
                    ON federated_memories(tier);

                CREATE TABLE IF NOT EXISTS federation_meta (
                    project_id TEXT PRIMARY KEY,
                    last_sync TEXT NOT NULL,
                    entry_count INTEGER NOT NULL DEFAULT 0
                );
            """)

            # FTS5 table -- created separately (can't use executescript reliably)
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS federated_fts
                    USING fts5(key, value, tags, content=federated_memories, content_rowid=rowid)
                """)

            self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Publish / Unpublish
    # ------------------------------------------------------------------

    def publish(
        self,
        project_id: str,
        entries: list[MemoryEntry],
        project_root: str = "",
    ) -> int:
        """Publish memories to the federation hub.

        Args:
            project_id: Source project identifier.
            entries: Memory entries to publish.
            project_root: Source project root path for traceability.

        Returns:
            Number of entries published.
        """
        now = datetime.now(tz=UTC).isoformat()
        published = 0

        with self._lock:
            for entry in entries:
                tags_json = json.dumps(entry.tags)
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO federated_memories
                    (project_id, key, value, tier, confidence, source,
                     source_agent, tags, created_at, updated_at,
                     published_at, origin_project_root)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        entry.key,
                        entry.value,
                        entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier),
                        entry.confidence,
                        entry.source.value if hasattr(entry.source, "value") else str(entry.source),
                        entry.source_agent,
                        tags_json,
                        entry.created_at,
                        entry.updated_at,
                        now,
                        project_root,
                    ),
                )
                published += 1

            # Update FTS index
            with contextlib.suppress(sqlite3.OperationalError):
                self._conn.execute("INSERT INTO federated_fts(federated_fts) VALUES('rebuild')")

            # Update meta
            self._conn.execute(
                """
                INSERT OR REPLACE INTO federation_meta
                (project_id, last_sync, entry_count)
                VALUES (?, ?, (SELECT COUNT(*) FROM federated_memories WHERE project_id = ?))
                """,
                (project_id, now, project_id),
            )
            self._conn.commit()

        logger.info(
            "federation.published",
            project_id=project_id,
            count=published,
        )
        return published

    def unpublish(self, project_id: str, keys: list[str] | None = None) -> int:
        """Remove memories from the hub.

        Args:
            project_id: Source project identifier.
            keys: Specific keys to remove. If None, removes all for this project.

        Returns:
            Number of entries removed.
        """
        with self._lock:
            if keys:
                placeholders = ",".join("?" for _ in keys)
                cursor = self._conn.execute(
                    "DELETE FROM federated_memories"
                    f" WHERE project_id = ? AND key IN ({placeholders})",
                    [project_id, *keys],
                )
            else:
                cursor = self._conn.execute(
                    "DELETE FROM federated_memories WHERE project_id = ?",
                    (project_id,),
                )
            removed = cursor.rowcount
            self._conn.commit()

        logger.info(
            "federation.unpublished",
            project_id=project_id,
            count=removed,
        )
        return removed

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
    ) -> list[dict[str, Any]]:
        """Search federated memories with optional filtering.

        Args:
            query: Full-text search query.
            project_ids: Limit to specific projects.
            tags: Require entries to have at least one of these tags.
            min_confidence: Minimum confidence threshold.
            limit: Maximum results.

        Returns:
            List of matching entries as dicts with project attribution.
        """
        with self._lock:
            # Try FTS5 first
            try:
                rows = self._conn.execute(
                    """
                    SELECT fm.*, rank
                    FROM federated_fts fts
                    JOIN federated_memories fm ON fts.rowid = fm.rowid
                    WHERE fts MATCH ?
                    AND fm.confidence >= ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (query, min_confidence, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS5 fallback: simple LIKE search
                rows = self._conn.execute(
                    """
                    SELECT *, 0.0 as rank
                    FROM federated_memories
                    WHERE (key LIKE ? OR value LIKE ?)
                    AND confidence >= ?
                    LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", min_confidence, limit),
                ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            row_dict = dict(row)
            row_tags = json.loads(row_dict.get("tags", "[]"))

            # Filter by project_ids
            if project_ids and row_dict["project_id"] not in project_ids:
                continue

            # Filter by tags
            if tags and not set(tags) & set(row_tags):
                continue

            row_dict["tags"] = row_tags
            results.append(row_dict)

        return results[:limit]

    def get_project_entries(
        self,
        project_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get all entries for a specific project."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM federated_memories
                WHERE project_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            row_dict = dict(row)
            row_dict["tags"] = json.loads(row_dict.get("tags", "[]"))
            results.append(row_dict)
        return results

    def get_stats(self) -> dict[str, Any]:
        """Return federation hub statistics."""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM federated_memories").fetchone()[0]

            project_counts = self._conn.execute(
                """
                SELECT project_id, COUNT(*) as cnt
                FROM federated_memories
                GROUP BY project_id
                """,
            ).fetchall()

            meta_rows = self._conn.execute("SELECT * FROM federation_meta").fetchall()

        return {
            "total_entries": total,
            "projects": {row[0]: row[1] for row in project_counts},
            "meta": [dict(row) for row in meta_rows],
        }


# ---------------------------------------------------------------------------
# Sync operations
# ---------------------------------------------------------------------------


def sync_to_hub(
    store: MemoryStore,
    federated_store: FederatedStore,
    project_id: str,
    project_root: str = "",
    keys: list[str] | None = None,
) -> dict[str, int]:
    """Publish shared-scope memories from local store to federation hub.

    Args:
        store: Local MemoryStore instance.
        federated_store: Federation hub store.
        project_id: This project's federation ID.
        project_root: This project's root path.
        keys: Specific keys to publish. If None, publishes all shared-scope.

    Returns:
        Dict with published and skipped counts.
    """
    all_entries = store.list_all(scope="shared")

    entries = [e for e in all_entries if e.key in set(keys)] if keys else all_entries

    if not entries:
        return {"published": 0, "skipped": 0}

    published = federated_store.publish(
        project_id=project_id,
        entries=entries,
        project_root=project_root,
    )

    return {
        "published": published,
        "skipped": len(all_entries) - published,
    }


def sync_from_hub(
    store: MemoryStore,
    federated_store: FederatedStore,
    project_id: str,
    config: FederationConfig | None = None,
) -> dict[str, int]:
    """Pull subscribed memories from federation hub into local store.

    Args:
        store: Local MemoryStore instance.
        federated_store: Federation hub store.
        project_id: This project's federation ID.
        config: Federation config (loaded if None).

    Returns:
        Dict with imported, skipped, and conflict counts.
    """
    if config is None:
        config = load_federation_config()

    # Find subscription for this project
    subscription = None
    for sub in config.subscriptions:
        if sub.subscriber == project_id:
            subscription = sub
            break

    if subscription is None:
        return {"imported": 0, "skipped": 0, "conflicts": 0}

    # Query hub for matching entries
    source_ids = subscription.sources or [
        p.project_id for p in config.projects if p.project_id != project_id
    ]

    imported = 0
    skipped = 0
    conflicts = 0

    for source_id in source_ids:
        entries = federated_store.get_project_entries(source_id)

        for entry_dict in entries:
            # Apply confidence filter
            if entry_dict.get("confidence", 0) < subscription.min_confidence:
                skipped += 1
                continue

            # Apply tag filter
            entry_tags = entry_dict.get("tags", [])
            if subscription.tag_filter and not set(subscription.tag_filter) & set(entry_tags):
                skipped += 1
                continue

            key = entry_dict["key"]

            # Check for local conflict
            local = store.get(key)
            if local is not None:
                # Local always wins
                conflicts += 1
                continue

            # Import into local store
            store.save(
                key=key,
                value=entry_dict["value"],
                tier=entry_dict.get("tier", "pattern"),
                source="system",
                source_agent=f"federated:{source_id}",
                scope="project",
                tags=[*entry_tags, "federated", f"from:{source_id}"],
            )
            imported += 1

    logger.info(
        "federation.synced_from_hub",
        project_id=project_id,
        imported=imported,
        skipped=skipped,
        conflicts=conflicts,
    )

    return {
        "imported": imported,
        "skipped": skipped,
        "conflicts": conflicts,
    }


# ---------------------------------------------------------------------------
# Federated search
# ---------------------------------------------------------------------------


@dataclass
class FederatedSearchResult:
    """A search result with project attribution."""

    key: str
    value: str
    source: str  # "local" or "federated"
    project_id: str
    confidence: float = 0.0
    tier: str = "pattern"
    tags: list[str] = field(default_factory=list)
    relevance_score: float = 0.0


def federated_search(
    query: str,
    local_store: MemoryStore,
    federated_store: FederatedStore,
    project_id: str,
    include_local: bool = True,
    include_hub: bool = True,
    max_results: int = 20,
) -> list[FederatedSearchResult]:
    """Search across local and federated memory stores.

    Local results get a 1.2x relevance boost. Results are deduplicated
    by key (local wins on collision).

    Args:
        query: Search query string.
        local_store: Local MemoryStore instance.
        federated_store: Federation hub store.
        project_id: This project's federation ID.
        include_local: Include local store results.
        include_hub: Include hub results.
        max_results: Maximum results to return.

    Returns:
        Sorted list of FederatedSearchResult.
    """
    results: list[FederatedSearchResult] = []
    seen_keys: set[str] = set()

    # Local results first (with boost)
    if include_local:
        local_results = local_store.search(query)
        for entry in local_results:
            if entry.key in seen_keys:
                continue
            seen_keys.add(entry.key)
            results.append(
                FederatedSearchResult(
                    key=entry.key,
                    value=entry.value,
                    source="local",
                    project_id=project_id,
                    confidence=entry.confidence,
                    tier=entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier),
                    tags=entry.tags,
                    relevance_score=entry.confidence * 1.2,  # local boost
                )
            )

    # Hub results
    if include_hub:
        hub_results = federated_store.search(
            query=query,
            limit=max_results * 2,  # fetch more, filter after dedup
        )
        for row in hub_results:
            key = row["key"]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            results.append(
                FederatedSearchResult(
                    key=key,
                    value=row["value"],
                    source="federated",
                    project_id=row["project_id"],
                    confidence=row.get("confidence", 0.0),
                    tier=row.get("tier", "pattern"),
                    tags=row.get("tags", []),
                    relevance_score=row.get("confidence", 0.0),
                )
            )

    # Sort by relevance (descending)
    results.sort(key=lambda r: r.relevance_score, reverse=True)

    return results[:max_results]
