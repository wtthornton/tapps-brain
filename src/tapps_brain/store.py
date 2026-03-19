"""In-memory cache backed by SQLite for the shared memory subsystem.

Provides fast reads from an in-memory dict with write-through to SQLite.
RAG safety checks on save prevent prompt injection in stored content.
Auto-consolidation triggers on save when enabled (Epic 58).
"""

from __future__ import annotations

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

from tapps_brain.safety import check_content_safety

logger = structlog.get_logger(__name__)

# Maximum number of memories per project.
_MAX_ENTRIES = 500

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
    ) -> None:
        self._project_root = project_root
        self._persistence = MemoryPersistence(project_root, store_dir=store_dir)
        self._lock = threading.Lock()
        self._consolidation_config = consolidation_config or ConsolidationConfig()
        self._embedding_provider = embedding_provider
        self._write_rules = write_rules
        self._consolidation_in_progress = False

        # Cold-start: load all entries into memory
        self._entries: dict[str, MemoryEntry] = {}
        for entry in self._persistence.load_all():
            self._entries[entry.key] = entry

        logger.info(
            "memory_store_initialized",
            project_root=str(project_root),
            entry_count=len(self._entries),
            auto_consolidation=self._consolidation_config.enabled,
        )

    @property
    def project_root(self) -> Path:
        """Return the project root path."""
        return self._project_root

    def set_consolidation_config(self, config: ConsolidationConfig) -> None:
        """Update the consolidation configuration."""
        self._consolidation_config = config

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def save(
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
        *,
        skip_consolidation: bool = False,
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
            tags: Tags for categorization.
            branch: Git branch name (required when scope=branch).
            confidence: Confidence score (-1.0 for auto from source).
            skip_consolidation: If True, skip auto-consolidation check.
        """
        # Write rules validation (Epic 65.17)
        wr_error = _validate_write_rules(key, value, self._write_rules)
        if wr_error is not None:
            return {
                "error": "write_rules_violation",
                "message": wr_error,
            }

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

        now = _utc_now_iso()
        with self._lock:
            existing = self._entries.get(key)

            entry = MemoryEntry(
                key=key,
                value=value,
                tier=MemoryTier(tier),
                confidence=confidence,
                source=MemorySource(source),
                source_agent=source_agent,
                scope=MemoryScope(scope),
                tags=tags or [],
                created_at=existing.created_at if existing else now,
                updated_at=now,
                last_accessed=now,
                access_count=existing.access_count if existing else 0,
                branch=branch,
                # Preserve reserved fields on update
                last_reinforced=existing.last_reinforced if existing else None,
                reinforce_count=existing.reinforce_count if existing else 0,
                contradicted=existing.contradicted if existing else False,
                contradiction_reason=(existing.contradiction_reason if existing else None),
                seeded_from=existing.seeded_from if existing else None,
            )

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
                    self._entries[key] = entry
            except Exception:
                logger.debug("embedding_compute_failed", key=key, exc_info=True)

        self._persistence.save(entry)

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
        with self._lock:
            if scope is not None and branch is not None:
                entry = self._resolve_scope(key, scope, branch)
            else:
                entry = self._entries.get(key)

            if entry is None:
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

        self._persistence.save(updated)
        return updated

    def list_all(
        self,
        tier: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        """List entries with optional filters."""
        with self._lock:
            entries = list(self._entries.values())

        if tier is not None:
            entries = [e for e in entries if e.tier == tier]
        if scope is not None:
            entries = [e for e in entries if e.scope == scope]
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set.intersection(e.tags)]

        return entries

    def delete(self, key: str) -> bool:
        """Delete a memory entry by key. Returns True if deleted."""
        with self._lock:
            if key not in self._entries:
                return False
            del self._entries[key]

        self._persistence.delete(key)
        return True

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
        tier: str | None = None,
        scope: str | None = None,
    ) -> list[MemoryEntry]:
        """Search via FTS5, with optional post-filters."""
        results = self._persistence.search(query)

        if tier is not None:
            results = [r for r in results if r.tier == tier]
        if scope is not None:
            results = [r for r in results if r.scope == scope]
        if tags:
            tag_set = set(tags)
            results = [r for r in results if tag_set.intersection(r.tags)]

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

        self._persistence.save(updated)
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
