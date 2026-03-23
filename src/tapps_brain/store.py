"""In-memory cache backed by SQLite for the shared memory subsystem.

Provides fast reads from an in-memory dict with write-through to SQLite.
RAG safety checks on save prevent prompt injection in stored content.
Auto-consolidation triggers on save when enabled (Epic 58).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.models import (
    MemoryEntry,
    MemoryScope,
    MemorySnapshot,
    MemorySource,
    MemoryTier,
    _utc_now_iso,
)
from tapps_brain.persistence import MemoryPersistence

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.embeddings import EmbeddingProvider
    from tapps_brain.hive import HiveStore

from tapps_brain.metrics import MetricsCollector, MetricsSnapshot, MetricsTimer, StoreHealthReport
from tapps_brain.rate_limiter import RateLimiterConfig, SlidingWindowRateLimiter
from tapps_brain.relations import RelationEntry, extract_relations
from tapps_brain.safety import check_content_safety

logger = structlog.get_logger(__name__)

# Maximum number of memories per project.
_MAX_ENTRIES = 500

# Valid Hive propagation scope values.
VALID_AGENT_SCOPES: tuple[str, ...] = ("private", "domain", "hive")

# RAG safety match count threshold for blocking content.
# (moved up for visibility, original kept below for compat)


def _validate_write_rules(
    key: str,
    value: str,
    write_rules: Any,  # noqa: ANN401
) -> str | None:
    """Validate memory save against write rules (Epic 65.17).

    Returns None if valid, or an error message string if invalid.
    """
    if write_rules is None:
        return None

    enforced = getattr(write_rules, "enforced", False)
    if not enforced:
        return None

    # Check blocked keywords
    blocked = getattr(write_rules, "block_sensitive_keywords", [])
    combined = f"{key} {value}".lower()
    for kw in blocked:
        if kw.lower() in combined:
            return f"Blocked by write rule: contains sensitive keyword '{kw}'"

    # Check min length
    min_len = getattr(write_rules, "min_value_length", 0)
    if min_len > 0 and len(value) < min_len:
        return f"Value too short ({len(value)} < {min_len} chars)"

    # Check max length
    max_len = getattr(write_rules, "max_value_length", 4096)
    if len(value) > max_len:
        return f"Value too long ({len(value)} > {max_len} chars)"

    return None


# RAG safety match count threshold for blocking content.
_RAG_BLOCK_THRESHOLD = 3


@dataclass
class ConsolidationConfig:
    """Configuration for auto-consolidation on save."""

    enabled: bool = False
    threshold: float = 0.7
    min_entries: int = 3

    def to_dict(self) -> dict[str, object]:
        """Return config as a plain dict."""
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "min_entries": self.min_entries,
        }


class MemoryStore:
    """In-memory cache with SQLite write-through persistence.

    Thread-safe via ``threading.Lock``. Write-through: every mutation
    updates both the in-memory dict and SQLite synchronously.
    Auto-consolidation triggers on save when enabled (Epic 58).
    """

    def __init__(
        self,
        project_root: Path,
        *,
        store_dir: str = ".tapps-brain",
        consolidation_config: ConsolidationConfig | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        write_rules: Any = None,  # noqa: ANN401
        lookup_engine: Any = None,  # noqa: ANN401
        profile: Any = None,  # noqa: ANN401  # MemoryProfile | None (EPIC-010)
        hive_store: HiveStore | None = None,
        hive_agent_id: str = "unknown",
        rate_limiter_config: RateLimiterConfig | None = None,
    ) -> None:
        self._project_root = project_root
        self._rate_limiter = SlidingWindowRateLimiter(rate_limiter_config)
        try:
            self._persistence = MemoryPersistence(project_root, store_dir=store_dir)
        except sqlite3.DatabaseError:
            _db_path = project_root / store_dir / "memory" / "memory.db"
            logger.error(
                "database_corrupt",
                path=str(_db_path),
                message=f"Database corrupt: {_db_path}. Back up and delete to recover.",
            )
            raise
        self._lock = threading.Lock()
        self._consolidation_config = consolidation_config or ConsolidationConfig()
        self._embedding_provider = embedding_provider
        self._write_rules = write_rules
        self._lookup_engine = lookup_engine
        self._consolidation_in_progress = False
        from tapps_brain.gc import GCConfig as _GCConfig

        self._gc_config = _GCConfig()
        self._metrics = MetricsCollector()
        self._hive_store = hive_store
        self._hive_agent_id = hive_agent_id

        # EPIC-010: resolve and store the active profile
        self._profile = self._resolve_profile(project_root, profile)

        # Cold-start: load all entries into memory
        self._entries: dict[str, MemoryEntry] = {}
        for entry in self._persistence.load_all():
            self._entries[entry.key] = entry

        # Cold-start: load all relations into memory, indexed by entry key
        self._relations: dict[str, list[dict[str, Any]]] = {}
        all_relations = self._persistence.list_relations()
        for rel in all_relations:
            for src_key in rel["source_entry_keys"]:
                self._relations.setdefault(src_key, []).append(rel)

        logger.info(
            "memory_store_initialized",
            project_root=str(project_root),
            entry_count=len(self._entries),
            relation_count=len(all_relations),
            auto_consolidation=self._consolidation_config.enabled,
        )

    @property
    def project_root(self) -> Path:
        """Return the project root path."""
        return self._project_root

    def get_consolidation_config(self) -> ConsolidationConfig:
        """Return the active consolidation configuration."""
        return self._consolidation_config

    def set_consolidation_config(self, config: ConsolidationConfig) -> None:
        """Update the consolidation configuration."""
        self._consolidation_config = config

    def get_gc_config(self) -> Any:  # noqa: ANN401
        """Return the active GCConfig instance."""
        return self._gc_config

    def set_gc_config(self, config: Any) -> None:  # noqa: ANN401
        """Update the GC configuration at runtime."""
        self._gc_config = config

    @property
    def rate_limiter(self) -> SlidingWindowRateLimiter:
        """Return the rate limiter instance for stats/config access."""
        return self._rate_limiter

    @staticmethod
    def _resolve_profile(project_root: Path, profile: Any) -> Any:  # noqa: ANN401
        """Resolve the active memory profile (EPIC-010).

        When *profile* is an explicit ``MemoryProfile``, use it directly.
        Otherwise, attempt resolution from project/user/built-in defaults.
        Falls back gracefully to ``None`` if the profile module isn't
        available or no profile files exist.
        """
        if profile is not None:
            return profile
        try:
            from tapps_brain.profile import resolve_profile as _resolve

            return _resolve(project_root)
        except Exception:
            return None

    @property
    def profile(self) -> Any:  # noqa: ANN401
        """Return the active ``MemoryProfile``, or ``None``."""
        return self._profile

    def _get_decay_config(self) -> Any:  # noqa: ANN401
        """Return a ``DecayConfig`` derived from the active profile (EPIC-010)."""
        if self._profile is not None:
            try:
                from tapps_brain.decay import decay_config_from_profile

                return decay_config_from_profile(self._profile)
            except Exception:
                pass
        from tapps_brain.decay import DecayConfig

        return DecayConfig()

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def save(  # noqa: PLR0915
        self,
        key: str,
        value: str,
        tier: str = "pattern",
        source: str = "agent",
        source_agent: str = "unknown",
        scope: str = "project",
        tags: list[str] | None = None,
        branch: str | None = None,
        confidence: float = -1.0,
        agent_scope: str = "private",
        *,
        skip_consolidation: bool = False,
        batch_context: str | None = None,
    ) -> MemoryEntry | dict[str, Any]:
        """Save or update a memory entry.

        Returns the saved ``MemoryEntry``, or an error dict if RAG safety
        blocks the content.

        Args:
            key: Unique identifier for the memory.
            value: Memory content.
            tier: Memory tier (architectural, pattern, context).
            source: Source of the memory (human, agent, inferred, system).
            source_agent: Identifier of the agent saving the memory.
            scope: Visibility scope (project, branch, session).
            agent_scope: Hive propagation scope (private, domain, hive).
            tags: Tags for categorization.
            branch: Git branch name (required when scope=branch).
            confidence: Confidence score (-1.0 for auto from source).
            skip_consolidation: If True, skip auto-consolidation check.
            batch_context: Operation context for rate limit exemption
                (e.g. "import_markdown", "seed", "federation_sync", "consolidate").
        """
        # agent_scope enum validation
        if agent_scope not in VALID_AGENT_SCOPES:
            return {
                "error": "invalid_agent_scope",
                "message": (
                    f"Invalid agent_scope {agent_scope!r}. Valid values: {list(VALID_AGENT_SCOPES)}"
                ),
                "valid_values": list(VALID_AGENT_SCOPES),
            }

        # Write rules validation (Epic 65.17)
        wr_error = _validate_write_rules(key, value, self._write_rules)
        if wr_error is not None:
            return {
                "error": "write_rules_violation",
                "message": wr_error,
            }

        # Rate limit check (H6a) — warn-only, never blocks
        rate_result = self._rate_limiter.check(batch_context=batch_context)
        if rate_result.minute_exceeded or rate_result.session_exceeded:
            logger.warning(
                "memory_save_rate_warning",
                key=key,
                minute_count=rate_result.current_minute_count,
                session_count=rate_result.current_session_count,
            )

        # RAG safety check on value
        safety = check_content_safety(value)
        if not safety.safe and safety.match_count >= _RAG_BLOCK_THRESHOLD:
            logger.warning(
                "memory_save_blocked",
                key=key,
                match_count=safety.match_count,
                patterns=safety.flagged_patterns,
            )
            return {
                "error": "content_blocked",
                "message": "Memory value blocked by RAG safety filter.",
                "flagged_patterns": safety.flagged_patterns,
            }

        # Sanitise if flagged but not blocked
        if not safety.safe and safety.sanitised_content:
            value = safety.sanitised_content

        self._metrics.increment("store.save")
        with MetricsTimer(self._metrics, "store.save_ms"):
            now = _utc_now_iso()
            with self._lock:
                existing = self._entries.get(key)

                # EPIC-010: Accept profile layer names as tier values.
                # Try MemoryTier enum first; if it fails, accept the raw
                # string when the active profile defines a layer with that name.
                try:
                    tier_val: MemoryTier | str = MemoryTier(tier)
                except ValueError:
                    if self._profile is not None and tier in self._profile.layer_names:
                        tier_val = tier
                    else:
                        tier_val = MemoryTier(tier)  # Raise original error

                entry = MemoryEntry(
                    key=key,
                    value=value,
                    tier=tier_val,
                    confidence=confidence,
                    source=MemorySource(source),
                    source_agent=source_agent,
                    scope=MemoryScope(scope),
                    agent_scope=agent_scope,
                    tags=tags or [],
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                    last_accessed=now,
                    access_count=existing.access_count if existing else 1,
                    branch=branch,
                    # Preserve reserved fields on update
                    last_reinforced=existing.last_reinforced if existing else None,
                    reinforce_count=existing.reinforce_count if existing else 0,
                    contradicted=existing.contradicted if existing else False,
                    contradiction_reason=(
                        existing.contradiction_reason if existing else None
                    ),
                    seeded_from=existing.seeded_from if existing else None,
                    # Preserve temporal fields on update (EPIC-004)
                    valid_at=existing.valid_at if existing else None,
                    invalid_at=existing.invalid_at if existing else None,
                    superseded_by=existing.superseded_by if existing else None,
                )

                # Compute integrity hash (H4a)
                from tapps_brain.integrity import compute_integrity_hash as _compute_hash

                _tier_str = (
                    entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                )
                _source_str = (
                    entry.source.value
                    if hasattr(entry.source, "value")
                    else str(entry.source)
                )
                _hash = _compute_hash(entry.key, entry.value, _tier_str, _source_str)
                entry = entry.model_copy(update={"integrity_hash": _hash})

                # Max entries enforcement: evict lowest-confidence entry
                if key not in self._entries and len(self._entries) >= _MAX_ENTRIES:
                    self._evict_lowest_confidence()

                self._entries[key] = entry

            # Compute embedding when semantic search is enabled (Epic 65.7)
            if self._embedding_provider is not None:
                try:
                    emb = self._embedding_provider.embed(value)
                    entry = entry.model_copy(update={"embedding": emb})
                    with self._lock:
                        # Re-read current entry to avoid overwriting concurrent
                        # updates (e.g. another save/update_fields in between).
                        current = self._entries.get(key)
                        if current is not None and current.key == entry.key:
                            entry = current.model_copy(update={"embedding": emb})
                        self._entries[key] = entry
                except Exception:
                    logger.debug("embedding_compute_failed", key=key, exc_info=True)

            # Persist to SQLite — rollback in-memory cache on failure to
            # maintain write-through consistency.
            try:
                self._persistence.save(entry)
            except Exception:
                with self._lock:
                    if existing is not None:
                        self._entries[key] = existing
                    else:
                        self._entries.pop(key, None)
                raise

            # Hive propagation (EPIC-011)
            if self._hive_store is not None:
                self._propagate_to_hive(entry)

            # Extract and persist relations (EPIC-006)
            relations = extract_relations(key, value)
            if relations:
                self._persistence.save_relations(key, relations)
                # Reload from persistence to keep timestamps consistent
                with self._lock:
                    self._relations[key] = self._persistence.load_relations(key)

            # Auto-consolidation check (Epic 58)
            if (
                self._consolidation_config.enabled
                and not skip_consolidation
                and not self._consolidation_in_progress
            ):
                self._maybe_consolidate(entry)

        return entry

    def _maybe_consolidate(self, entry: MemoryEntry) -> None:
        """Check if the saved entry should trigger consolidation.

        Runs consolidation in a non-reentrant manner to prevent infinite
        loops when consolidation saves new entries.
        """
        if self._consolidation_in_progress:
            return

        self._consolidation_in_progress = True
        try:
            from tapps_brain.auto_consolidation import check_consolidation_on_save

            result = check_consolidation_on_save(
                entry,
                self,
                threshold=self._consolidation_config.threshold,
                min_entries=self._consolidation_config.min_entries,
            )

            if result.triggered:
                self._metrics.increment("store.consolidate")
                self._metrics.increment("store.consolidate.merged", len(result.source_keys))
                logger.info(
                    "auto_consolidation_on_save",
                    entry_key=entry.key,
                    consolidated_key=result.consolidated_entry.key
                    if result.consolidated_entry
                    else None,
                    source_keys=result.source_keys,
                )
        except Exception:
            logger.debug("auto_consolidation_check_failed", exc_info=True)
        finally:
            self._consolidation_in_progress = False

    def _propagate_to_hive(self, entry: MemoryEntry) -> None:
        """Propagate a saved entry to the Hive if appropriate (EPIC-011)."""
        if self._hive_store is None:
            return
        try:
            from tapps_brain.hive import PropagationEngine

            # Read Hive config from profile if available
            auto_propagate: list[str] | None = None
            private: list[str] | None = None
            agent_profile = "repo-brain"
            if self._profile is not None:
                hive_cfg = getattr(self._profile, "hive", None)
                if hive_cfg is not None:
                    auto_propagate = hive_cfg.auto_propagate_tiers
                    private = hive_cfg.private_tiers
                agent_profile = getattr(self._profile, "name", "repo-brain")

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)

            PropagationEngine.propagate(
                key=entry.key,
                value=entry.value,
                agent_scope=entry.agent_scope,
                agent_id=self._hive_agent_id,
                agent_profile=agent_profile,
                tier=tier_str,
                confidence=entry.confidence,
                source=entry.source.value if hasattr(entry.source, "value") else str(entry.source),
                tags=entry.tags,
                hive_store=self._hive_store,
                auto_propagate_tiers=auto_propagate,
                private_tiers=private,
            )
        except Exception:
            logger.debug("hive_propagation_failed", key=entry.key, exc_info=True)

    def get(
        self,
        key: str,
        scope: str | None = None,
        branch: str | None = None,
    ) -> MemoryEntry | None:
        """Retrieve a memory entry by key.

        When *scope* and *branch* are provided, applies scope resolution:
        session > branch > project (most specific wins).

        Updates ``last_accessed`` and ``access_count`` on read.
        """
        self._metrics.increment("store.get")
        with MetricsTimer(self._metrics, "store.get_ms"):
            with self._lock:
                if scope is not None and branch is not None:
                    entry = self._resolve_scope(key, scope, branch)
                else:
                    entry = self._entries.get(key)

                if entry is None:
                    self._metrics.increment("store.get.miss")
                    return None

                # Update access metadata
                now = _utc_now_iso()
                updated = entry.model_copy(
                    update={
                        "last_accessed": now,
                        "access_count": entry.access_count + 1,
                    }
                )
                self._entries[updated.key] = updated

            self._metrics.increment("store.get.hit")
            # Persist access metadata — rollback on failure.
            try:
                self._persistence.save(updated)
            except Exception:
                with self._lock:
                    self._entries[updated.key] = entry
                raise
            return updated

    def list_all(
        self,
        tier: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        include_superseded: bool = True,
    ) -> list[MemoryEntry]:
        """List entries with optional filters.

        Args:
            tier: Filter by tier.
            scope: Filter by scope.
            tags: Filter by tags.
            include_superseded: When ``False``, exclude temporally invalid
                (superseded/expired) entries. Default ``True`` for backward
                compatibility.
        """
        with self._lock:
            entries = list(self._entries.values())

        if tier is not None:
            entries = [e for e in entries if e.tier == tier]
        if scope is not None:
            entries = [e for e in entries if e.scope == scope]
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set.intersection(e.tags)]
        if not include_superseded:
            entries = [e for e in entries if e.is_temporally_valid()]

        return entries

    def delete(self, key: str) -> bool:
        """Delete a memory entry by key. Returns True if deleted."""
        with self._lock:
            if key not in self._entries:
                return False
            removed = self._entries.pop(key)

        # Persist deletion — rollback in-memory cache on failure to
        # maintain write-through consistency.
        try:
            self._persistence.delete(key)
        except Exception:
            with self._lock:
                self._entries[key] = removed
            raise
        return True

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
        tier: str | None = None,
        scope: str | None = None,
        as_of: str | None = None,
    ) -> list[MemoryEntry]:
        """Search via FTS5, with optional post-filters.

        Args:
            query: Search query string.
            tags: Filter by tags.
            tier: Filter by tier.
            scope: Filter by scope.
            as_of: ISO-8601 timestamp for point-in-time temporal filtering.
                When set, only entries valid at that time are returned.
                When ``None`` (default), temporally invalid entries are excluded
                using the current time.
        """
        self._metrics.increment("store.search")
        with MetricsTimer(self._metrics, "store.search_ms"):
            results = self._persistence.search(query)

            if tier is not None:
                results = [r for r in results if r.tier == tier]
            if scope is not None:
                results = [r for r in results if r.scope == scope]
            if tags:
                tag_set = set(tags)
                results = [r for r in results if tag_set.intersection(r.tags)]

            # Temporal filtering (EPIC-004)
            results = [r for r in results if r.is_temporally_valid(as_of)]

            self._metrics.increment("store.search.results", len(results))
            return results

    def update_fields(self, key: str, **fields: Any) -> MemoryEntry | None:  # noqa: ANN401
        """Partial update of specific fields on an existing entry.

        Preserves immutable fields like ``created_at``. Used by Epic 24
        decay/contradiction/reinforcement systems.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None

            fields["updated_at"] = _utc_now_iso()
            updated = entry.model_copy(update=fields)
            self._entries[key] = updated

        # Persist — rollback in-memory cache on failure.
        try:
            self._persistence.save(updated)
        except Exception:
            with self._lock:
                self._entries[key] = entry
            raise
        return updated

    def count(self) -> int:
        """Return the total number of memory entries."""
        with self._lock:
            return len(self._entries)

    def snapshot(self) -> MemorySnapshot:
        """Return a serializable snapshot of the full memory state."""
        with self._lock:
            entries = list(self._entries.values())

        tier_counts: dict[str, int] = {}
        for entry in entries:
            tier_val = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1

        return MemorySnapshot(
            project_root=str(self._project_root),
            entries=entries,
            total_count=len(entries),
            tier_counts=tier_counts,
        )

    def get_schema_version(self) -> int:
        """Return the current SQLite schema version."""
        return self._persistence.get_schema_version()

    # ------------------------------------------------------------------
    # Reinforcement (Story 002.2)
    # ------------------------------------------------------------------

    def reinforce(self, key: str, *, confidence_boost: float = 0.0) -> MemoryEntry:
        """Reinforce a memory entry, resetting its decay clock atomically.

        Args:
            key: The memory entry key to reinforce.
            confidence_boost: Optional confidence increase (0.0-0.2).

        Returns:
            The updated ``MemoryEntry``.

        Raises:
            KeyError: If the entry does not exist.
        """
        from tapps_brain.reinforcement import reinforce as _reinforce

        self._metrics.increment("store.reinforce")
        decay_cfg = self._get_decay_config()

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                raise KeyError(key)

            updates = _reinforce(entry, decay_cfg, confidence_boost=confidence_boost)
            updated = entry.model_copy(update=updates)
            self._entries[key] = updated

        self._persistence.save(updated)

        # EPIC-010: Check promotion after reinforcement
        if self._profile is not None:
            try:
                from tapps_brain.promotion import PromotionEngine

                engine = PromotionEngine(decay_cfg)
                target_tier = engine.check_promotion(updated, self._profile)
                if target_tier is not None:
                    old_tier = str(updated.tier)
                    promoted = updated.model_copy(
                        update={"tier": target_tier, "updated_at": _utc_now_iso()}
                    )
                    with self._lock:
                        self._entries[key] = promoted
                    self._persistence.save(promoted)
                    self._persistence.append_audit(
                        action="promote",
                        key=key,
                        extra={
                            "from_tier": old_tier,
                            "to_tier": target_tier,
                            "access_count": updated.access_count,
                            "reinforce_count": updated.reinforce_count,
                        },
                    )
                    logger.info(
                        "memory_promoted",
                        key=key,
                        from_tier=old_tier,
                        to_tier=target_tier,
                    )
                    return promoted
            except Exception:
                logger.debug("promotion_check_failed", key=key, exc_info=True)

        return updated

    # ------------------------------------------------------------------
    # Extraction ingestion (Story 002.3)
    # ------------------------------------------------------------------

    def ingest_context(
        self,
        context: str,
        *,
        source: str = "agent",
        capture_prompt: str = "",
        agent_scope: str = "private",
    ) -> list[str]:
        """Extract durable facts from context and save new entries.

        Uses rule-based pattern matching to find decision-like statements
        and saves them as memory entries. Existing keys are skipped.

        Args:
            context: Raw session/transcript text to scan.
            source: Source attribution for created entries.
            capture_prompt: Optional guidance for extraction.
            agent_scope: Hive propagation scope for captured facts —
                ``'private'`` (default), ``'domain'``, or ``'hive'``.

        Returns:
            List of keys for newly created entries.
        """
        from tapps_brain.extraction import extract_durable_facts

        facts = extract_durable_facts(context, capture_prompt)
        created_keys: list[str] = []

        for fact in facts:
            key = fact["key"]
            # Skip if already exists
            with self._lock:
                if key in self._entries:
                    continue

            result = self.save(
                key=key,
                value=fact["value"],
                tier=fact["tier"],
                source=source,
                agent_scope=agent_scope,
            )
            if isinstance(result, MemoryEntry):
                created_keys.append(key)

        return created_keys

    # ------------------------------------------------------------------
    # Session indexing (Story 002.4)
    # ------------------------------------------------------------------

    def index_session(self, session_id: str, chunks: list[str]) -> int:
        """Index session chunks for later search.

        Args:
            session_id: Session identifier.
            chunks: List of text chunks to index.

        Returns:
            Number of chunks stored.
        """
        from tapps_brain.session_index import index_session as _index_session

        try:
            return _index_session(self._project_root, session_id, chunks)
        except Exception:
            logger.debug("session_index_failed", session_id=session_id, exc_info=True)
            return 0

    def search_sessions(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search session index by query.

        Returns list of dicts with keys: session_id, chunk_index, content, created_at.
        """
        from tapps_brain.session_index import search_session_index

        try:
            return search_session_index(self._project_root, query, limit=limit)
        except Exception:
            logger.debug("session_search_failed", query=query, exc_info=True)
            return []

    def cleanup_sessions(self, *, ttl_days: int = 90) -> int:
        """Delete session chunks older than ttl_days.

        Returns:
            Count of deleted chunks.
        """
        from tapps_brain.session_index import delete_expired_sessions

        try:
            return delete_expired_sessions(self._project_root, ttl_days)
        except Exception:
            logger.debug("session_cleanup_failed", exc_info=True)
            return 0

    # ------------------------------------------------------------------
    # Doc validation (Story 002.1)
    # ------------------------------------------------------------------

    def validate_entries(
        self,
        *,
        keys: list[str] | None = None,
    ) -> Any:  # noqa: ANN401
        """Validate memory entries against authoritative documentation.

        Requires a lookup engine to be configured at construction time.
        When no lookup engine is set, returns an empty ``ValidationReport``.

        Args:
            keys: Optional list of entry keys to validate. If None,
                validates all entries.

        Returns:
            A ``ValidationReport`` with per-entry results. Changes are
            applied back to the store automatically.
        """
        import asyncio

        from tapps_brain.doc_validation import MemoryDocValidator, ValidationReport

        if self._lookup_engine is None:
            return ValidationReport()

        validator = MemoryDocValidator(self._lookup_engine)

        # Collect entries to validate
        with self._lock:
            if keys is not None:
                entries = [self._entries[k] for k in keys if k in self._entries]
            else:
                entries = list(self._entries.values())

        # Run async validation and result application in a single event loop
        # (store is synchronous by design — two asyncio.run() calls would create
        # two separate event loops, which is unnecessary overhead).
        async def _run_validation() -> Any:  # noqa: ANN401
            rep = await validator.validate_batch(entries)
            await validator.apply_results(rep, self)
            return rep

        report = asyncio.run(_run_validation())

        return report

    # ------------------------------------------------------------------
    # Bi-temporal versioning (EPIC-004)
    # ------------------------------------------------------------------

    def supersede(self, old_key: str, new_value: str, **kwargs: Any) -> MemoryEntry:  # noqa: ANN401
        """Atomically supersede an existing entry with a new one.

        Sets ``invalid_at`` and ``superseded_by`` on the old entry and
        creates a new entry with ``valid_at`` set to now.

        Args:
            old_key: Key of the entry to supersede.
            new_value: Value for the replacement entry.
            **kwargs: Additional fields for the new entry (tier, tags, etc.).

        Returns:
            The newly created ``MemoryEntry``.

        Raises:
            KeyError: If *old_key* does not exist.
            ValueError: If *old_key* is already superseded.
        """
        self._metrics.increment("store.supersede")
        now = _utc_now_iso()

        with self._lock:
            old_entry = self._entries.get(old_key)
            if old_entry is None:
                raise KeyError(old_key)

            if old_entry.invalid_at is not None:
                msg = (
                    f"Entry '{old_key}' is already superseded (invalid_at={old_entry.invalid_at})."
                )
                raise ValueError(msg)

            # Derive new key from old key or kwargs
            new_key = kwargs.pop("key", f"{old_key}.v{self._version_count(old_key) + 1}")

            # Invalidate the old entry
            invalidated = old_entry.model_copy(
                update={
                    "invalid_at": now,
                    "superseded_by": new_key,
                    "updated_at": now,
                }
            )
            self._entries[old_key] = invalidated

        # Persist the invalidated entry
        self._persistence.save(invalidated)

        # Create the new entry
        new_kwargs: dict[str, Any] = {
            "tier": str(old_entry.tier),
            "source": old_entry.source.value,
            "source_agent": old_entry.source_agent,
            "scope": old_entry.scope.value,
            "tags": list(old_entry.tags),
            "branch": old_entry.branch,
            "confidence": old_entry.confidence,
        }
        new_kwargs.update(kwargs)

        new_entry = self.save(key=new_key, value=new_value, **new_kwargs)
        if isinstance(new_entry, dict):
            msg = f"Failed to create superseding entry: {new_entry.get('message', '')}"
            raise ValueError(msg)

        # Set valid_at on the new entry
        with self._lock:
            updated_new = new_entry.model_copy(update={"valid_at": now})
            self._entries[new_key] = updated_new
        self._persistence.save(updated_new)

        # Transfer relations from old entry to new entry
        old_relations = self.get_relations(old_key)
        if old_relations:
            transferred = [
                RelationEntry(
                    subject=r["subject"],
                    predicate=r["predicate"],
                    object_entity=r["object_entity"],
                    source_entry_keys=[new_key],
                    confidence=float(r.get("confidence", 0.8)),
                )
                for r in old_relations
            ]
            self._persistence.save_relations(new_key, transferred)
            with self._lock:
                self._relations[new_key] = self._persistence.load_relations(new_key)

        return updated_new

    def history(self, key: str) -> list[MemoryEntry]:
        """Return the full temporal chain for a key, ordered by ``valid_at``.

        Follows the ``superseded_by`` chain forward from the given key
        to find all successors, and backward to find all predecessors.

        Args:
            key: Any key in the version chain.

        Returns:
            All entries in the chain, ordered by ``valid_at`` ascending
            (entries without ``valid_at`` sort first).

        Raises:
            KeyError: If *key* does not exist.
        """
        with self._lock:
            if key not in self._entries:
                raise KeyError(key)

            # Build reverse index: superseded_by -> source key
            reverse: dict[str, str] = {}
            for e in self._entries.values():
                if e.superseded_by:
                    reverse[e.superseded_by] = e.key

            # Walk backward to the root.
            # Guard against corrupted cyclic chains (e.g. A→B→A).
            root = key
            backward_visited: set[str] = {root}
            while root in reverse:
                root = reverse[root]
                if root in backward_visited:
                    logger.warning("history_backward_cycle_detected", key=key, cycle_key=root)
                    break
                backward_visited.add(root)

            # Walk forward from root collecting the chain.
            # Track visited keys to guard against corrupted cyclic superseded_by chains.
            chain: list[MemoryEntry] = []
            chain_visited: set[str] = set()
            current: str | None = root
            while current is not None:
                if current in chain_visited:
                    logger.warning("history_cycle_detected", key=key, cycle_key=current)
                    break
                entry = self._entries.get(current)
                if entry is None:
                    break
                chain_visited.add(current)
                chain.append(entry)
                current = entry.superseded_by

        # Sort by valid_at (None sorts first)
        chain.sort(key=lambda e: e.valid_at or "")
        return chain

    def _version_count(self, key: str) -> int:
        """Count how many versions of a key exist (for generating version suffixes).

        Must be called while holding ``self._lock``.
        """
        count = 0
        for k in self._entries:
            if k == key or k.startswith(f"{key}.v"):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Auto-recall (EPIC-003)
    # ------------------------------------------------------------------

    def recall(self, message: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Search for relevant memories and return injection-ready context.

        Convenience wrapper around ``RecallOrchestrator.recall()``. The
        orchestrator is created lazily on first call and reused after.

        Args:
            message: The user's incoming message to match against.
            **kwargs: Override ``RecallConfig`` fields for this call.

        Returns:
            ``RecallResult`` with formatted memory section, metadata,
            and timing information.
        """
        from tapps_brain.recall import RecallOrchestrator

        self._metrics.increment("store.recall")
        with self._lock:
            if not hasattr(self, "_recall_orchestrator"):
                # Wire Hive store and profile for hive-aware recall (EPIC-011)
                hive_weight = 0.8
                agent_profile = "repo-brain"
                if self._profile is not None:
                    hive_cfg = getattr(self._profile, "hive", None)
                    if hive_cfg is not None:
                        hive_weight = hive_cfg.recall_weight
                    agent_profile = getattr(self._profile, "name", "repo-brain")
                self._recall_orchestrator = RecallOrchestrator(
                    self,
                    hive_store=self._hive_store,
                    hive_recall_weight=hive_weight,
                    hive_agent_profile=agent_profile,
                )

        with MetricsTimer(self._metrics, "store.recall_ms"):
            return self._recall_orchestrator.recall(message, **kwargs)

    def health(self) -> StoreHealthReport:
        """Return a structured health report for this store."""
        from datetime import UTC, datetime

        from tapps_brain.federation import load_federation_config
        from tapps_brain.gc import MemoryGarbageCollector
        from tapps_brain.similarity import find_consolidation_groups

        with self._lock:
            entries = list(self._entries.values())

        tier_counts: dict[str, int] = {}
        for entry in entries:
            tier_val = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1

        schema_ver = self._persistence.get_schema_version()

        oldest_age = 0.0
        now = datetime.now(tz=UTC)
        for entry in entries:
            try:
                raw = entry.created_at.replace("Z", "+00:00")
                created = datetime.fromisoformat(raw)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                days = (now - created).total_seconds() / 86400.0
                oldest_age = max(oldest_age, days)
            except (ValueError, TypeError, AttributeError):
                continue

        gc = MemoryGarbageCollector(gc_config=self._gc_config)
        gc_candidates = gc.identify_candidates(entries)

        groups = find_consolidation_groups(
            entries,
            threshold=self._consolidation_config.threshold,
        )
        consolidation_candidates = sum(len(g) for g in groups)

        fed = load_federation_config()
        federation_project_count = len(fed.projects)

        # Integrity verification (H4c)
        integrity = self.verify_integrity()

        # Rate limiter anomaly counts (H6c)
        rl_stats = self._rate_limiter.stats

        return StoreHealthReport(
            store_path=str(self._project_root),
            entry_count=len(entries),
            max_entries=_MAX_ENTRIES,
            schema_version=schema_ver,
            tier_distribution=tier_counts,
            oldest_entry_age_days=oldest_age,
            consolidation_candidates=consolidation_candidates,
            gc_candidates=len(gc_candidates),
            federation_enabled=federation_project_count > 0,
            federation_project_count=federation_project_count,
            integrity_verified=integrity["verified"],
            integrity_tampered=integrity["tampered"],
            integrity_no_hash=integrity["no_hash"],
            integrity_tampered_keys=integrity["tampered_keys"][:20],
            rate_limit_minute_anomalies=rl_stats.minute_anomalies,
            rate_limit_session_anomalies=rl_stats.session_anomalies,
            rate_limit_total_writes=rl_stats.total_writes,
            rate_limit_exempt_writes=rl_stats.exempt_writes,
            relation_count=self.count_relations(),
        )

    def gc(self, *, dry_run: bool = False) -> Any:  # noqa: ANN401
        """Run garbage collection on the store.

        Args:
            dry_run: If True, only identify candidates without archiving.

        Returns:
            ``GCResult`` with archived count and keys.
        """
        from tapps_brain.gc import GCResult, MemoryGarbageCollector

        self._metrics.increment("store.gc")
        gc_collector = MemoryGarbageCollector(gc_config=self._gc_config)
        with self._lock:
            entries = list(self._entries.values())
        candidates = gc_collector.identify_candidates(entries)
        candidate_keys = [c.key for c in candidates]

        if dry_run:
            return GCResult(
                archived_count=0,
                remaining_count=len(entries),
                archived_keys=candidate_keys,
            )

        # Archive to JSONL and delete from store
        archive_path = self._persistence.store_dir / "gc_archive.jsonl"
        MemoryGarbageCollector.append_to_archive(candidates, archive_path)
        for key in candidate_keys:
            self.delete(key)

        self._metrics.increment("store.gc.archived", len(candidate_keys))
        return GCResult(
            archived_count=len(candidate_keys),
            remaining_count=len(entries) - len(candidate_keys),
            archived_keys=candidate_keys,
        )

    def audit(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        """Query the JSONL audit trail.

        Convenience wrapper around ``AuditReader.query()``.

        Args:
            key: Filter by memory entry key.
            event_type: Filter by event type (save, delete, etc.).
            since: ISO-8601 lower bound (inclusive).
            until: ISO-8601 upper bound (inclusive).
            limit: Maximum number of entries to return.

        Returns:
            List of ``AuditEntry`` objects matching the filters.
        """
        from tapps_brain.audit import AuditReader

        reader = AuditReader(self._persistence.audit_path)
        return reader.query(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
        )

    def get_metrics(self) -> MetricsSnapshot:
        """Return a snapshot of in-process operation metrics."""
        return self._metrics.snapshot()

    # ------------------------------------------------------------------
    # Relations (EPIC-006)
    # ------------------------------------------------------------------

    def count_relations(self) -> int:
        """Return the total number of stored relation triples."""
        return self._persistence.count_relations()

    def get_relations(self, key: str) -> list[dict[str, Any]]:
        """Return all relations associated with a memory entry key.

        Args:
            key: The memory entry key.

        Returns:
            List of relation dicts with subject, predicate, object_entity,
            source_entry_keys, confidence, and created_at.
        """
        return list(self._relations.get(key, []))

    def find_related(
        self,
        key: str,
        *,
        max_hops: int = 2,
    ) -> list[tuple[str, int]]:
        """Find entries related to *key* via BFS traversal of the relation graph.

        Two entries are considered connected when they share an entity
        (subject or object_entity) in their extracted relations.

        Args:
            key: Starting entry key.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            List of ``(entry_key, hop_distance)`` tuples, ordered by hop
            distance (ascending) then key name.  The starting key is
            **not** included in the results.

        Raises:
            KeyError: If *key* does not exist in the store.
        """
        with self._lock:
            if key not in self._entries:
                raise KeyError(key)

            # Build entity -> set[entry_key] index from all relations
            entity_to_keys: dict[str, set[str]] = {}
            for entry_key, rels in self._relations.items():
                for rel in rels:
                    for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                        entity_to_keys.setdefault(entity, set()).add(entry_key)

            # BFS
            visited: set[str] = {key}
            result: list[tuple[str, int]] = []
            frontier: set[str] = {key}

            for hop in range(1, max_hops + 1):
                next_frontier: set[str] = set()
                for current_key in frontier:
                    # Collect entities from current_key's relations
                    for rel in self._relations.get(current_key, []):
                        for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                            for neighbor_key in entity_to_keys.get(entity, set()):
                                if neighbor_key not in visited:
                                    visited.add(neighbor_key)
                                    result.append((neighbor_key, hop))
                                    next_frontier.add(neighbor_key)
                frontier = next_frontier

        # Sort by hop distance, then key name for determinism
        result.sort(key=lambda t: (t[1], t[0]))
        return result

    def query_relations(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object_entity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter relations by subject, predicate, and/or object_entity.

        All filters use case-insensitive matching.  When multiple filters are
        provided they are combined with AND logic.  Passing no filters returns
        all relations.

        Args:
            subject: Filter by subject entity.
            predicate: Filter by predicate/relationship type.
            object_entity: Filter by object entity.

        Returns:
            List of matching relation dicts.
        """
        with self._lock:
            matches: list[dict[str, Any]] = []
            for rels in self._relations.values():
                for rel in rels:
                    if subject is not None and rel["subject"].lower() != subject.lower():
                        continue
                    if predicate is not None and rel["predicate"].lower() != predicate.lower():
                        continue
                    if (
                        object_entity is not None
                        and rel["object_entity"].lower() != object_entity.lower()
                    ):
                        continue
                    matches.append(dict(rel))
            # Deduplicate by (subject, predicate, object_entity) triple
            seen: set[tuple[str, str, str]] = set()
            deduped: list[dict[str, Any]] = []
            for m in matches:
                triple = (m["subject"].lower(), m["predicate"].lower(), m["object_entity"].lower())
                if triple not in seen:
                    seen.add(triple)
                    deduped.append(m)
        return deduped

    # ------------------------------------------------------------------
    # Tag management (EPIC-015)
    # ------------------------------------------------------------------

    def list_tags(self) -> dict[str, int]:
        """Return all unique tags across all entries with their usage counts.

        Returns:
            Dict mapping tag → count of entries that carry that tag.
        """
        with self._lock:
            entries = list(self._entries.values())
        counts: dict[str, int] = {}
        for entry in entries:
            for tag in entry.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def update_tags(
        self,
        key: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> MemoryEntry | dict[str, str]:
        """Atomically add and/or remove tags on an existing entry.

        Args:
            key: The memory entry key.
            add: Tags to add (ignored when already present).
            remove: Tags to remove (ignored when not present).

        Returns:
            The updated ``MemoryEntry`` on success, or a dict with
            ``"error"`` and ``"message"`` keys on failure.
        """
        from tapps_brain.models import MAX_TAGS

        add_set = set(add or [])
        remove_set = set(remove or [])

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return {"error": "not_found", "message": f"Entry '{key}' not found."}

            current = list(entry.tags)
            # Remove first, then add (preserves existing order)
            updated_tags = [t for t in current if t not in remove_set]
            for tag in add_set:
                if tag not in updated_tags:
                    updated_tags.append(tag)

            if len(updated_tags) > MAX_TAGS:
                return {
                    "error": "too_many_tags",
                    "message": (
                        f"Cannot have more than {MAX_TAGS} tags ({len(updated_tags)} would result)."
                    ),
                }

            from tapps_brain.models import _utc_now_iso

            updated = entry.model_copy(update={"tags": updated_tags, "updated_at": _utc_now_iso()})
            self._entries[key] = updated

        self._persistence.save(updated)
        return updated

    def entries_by_tag(
        self,
        tag: str,
        *,
        tier: str | None = None,
    ) -> list[MemoryEntry]:
        """Return all entries that carry a specific tag.

        Args:
            tag: The tag to filter by.
            tier: Optional tier filter.

        Returns:
            List of matching ``MemoryEntry`` objects.
        """
        return self.list_all(tags=[tag], tier=tier)

    # ------------------------------------------------------------------
    # Integrity verification (H4b)
    # ------------------------------------------------------------------

    def verify_integrity(self) -> dict[str, Any]:
        """Scan all entries and verify their HMAC integrity hashes.

        For each entry that has a stored ``integrity_hash``, recomputes the
        HMAC-SHA256 and checks for a match. Entries without a stored hash
        (pre-v8 or NULL) are reported separately.

        Returns:
            Dict with ``total``, ``verified``, ``tampered``, ``no_hash``,
            ``tampered_keys``, ``missing_hash_keys``, ``tampered_details``.
        """
        from tapps_brain.integrity import compute_integrity_hash, verify_integrity_hash

        self._metrics.increment("store.verify_integrity")

        with self._lock:
            entries = list(self._entries.values())

        total = len(entries)
        verified = 0
        tampered: list[str] = []
        tampered_details: list[dict[str, str]] = []
        missing_hash_keys: list[str] = []

        for entry in entries:
            stored_hash = getattr(entry, "integrity_hash", None)
            if not stored_hash:
                missing_hash_keys.append(entry.key)
                continue

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
            if verify_integrity_hash(entry.key, entry.value, tier_str, source_str, stored_hash):
                verified += 1
            else:
                tampered.append(entry.key)
                expected = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
                tampered_details.append(
                    {
                        "key": entry.key,
                        "stored_hash": stored_hash,
                        "expected_hash": expected,
                    }
                )
                logger.warning(
                    "integrity_verification_failed",
                    key=entry.key,
                    tier=tier_str,
                )

        return {
            "total": total,
            "verified": verified,
            "tampered": len(tampered),
            "no_hash": len(missing_hash_keys),
            "tampered_keys": tampered,
            "missing_hash_keys": missing_hash_keys,
            "tampered_details": tampered_details,
        }

    def close(self) -> None:
        """Close the underlying persistence layer."""
        self._persistence.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evict_lowest_confidence(self) -> None:
        """Evict the entry with the lowest confidence to make room.

        Must be called while holding ``self._lock``.
        """
        if not self._entries:
            return

        lowest_key = min(self._entries, key=lambda k: self._entries[k].confidence)
        del self._entries[lowest_key]
        self._persistence.delete(lowest_key)
        logger.info("memory_evicted", key=lowest_key, reason="max_entries")

    def _resolve_scope(self, key: str, scope: str, branch: str) -> MemoryEntry | None:
        """Resolve scope precedence: session > branch > project.

        Must be called while holding ``self._lock``.
        """
        # Try most specific first
        for try_scope in [MemoryScope.session, MemoryScope.branch, MemoryScope.project]:
            if try_scope.value == scope or _scope_rank(try_scope) >= _scope_rank(
                MemoryScope(scope)
            ):
                for entry in self._entries.values():
                    if entry.key == key and entry.scope == try_scope:
                        if try_scope == MemoryScope.branch and entry.branch != branch:
                            continue
                        return entry
        return None


def _scope_rank(scope: MemoryScope) -> int:
    """Return numeric rank for scope precedence (higher = more specific)."""
    return {
        MemoryScope.project: 0,
        MemoryScope.branch: 1,
        MemoryScope.session: 2,
    }.get(scope, 0)
