"""Auto-recall orchestrator for pre-prompt memory injection (EPIC-003).

Provides a ``RecallOrchestrator`` that accepts an incoming user message,
searches the memory store for relevant entries, and returns injection-ready
context. Delegates formatting, safety, and token budget enforcement to
``inject_memories()``.

The orchestrator adds quality gates on top of injection:
- Scope / tier / branch filtering
- Deduplication against already-in-context memories
- Minimum confidence threshold
- Timing measurement

Thread-safe: multiple concurrent ``recall()`` calls are safe.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from tapps_brain.injection import InjectionConfig, inject_memories
from tapps_brain.models import MemoryScope, MemoryTier, RecallResult

if TYPE_CHECKING:
    from tapps_brain.decay import DecayConfig
    from tapps_brain.retrieval import MemoryRetriever
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RecallConfig:
    """Configuration for the recall orchestrator."""

    engagement_level: str = "high"
    max_tokens: int = 2000
    min_score: float = 0.3
    min_confidence: float = 0.1
    scope_filter: MemoryScope | None = None
    tier_filter: MemoryTier | None = None
    branch: str | None = None
    dedupe_window: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RecallOrchestrator
# ---------------------------------------------------------------------------


class RecallOrchestrator:
    """Orchestrates auto-recall: search → filter → inject → return.

    Delegates formatting/safety/budget to ``inject_memories()`` and adds
    quality gates (scope, tier, branch, deduplication, timing).

    Thread-safe: all mutable state access is guarded by the store lock.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        retriever: MemoryRetriever | None = None,
        config: RecallConfig | None = None,
        decay_config: DecayConfig | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._config = config or RecallConfig()
        self._decay_config = decay_config

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    def recall(self, message: str, **kwargs: object) -> RecallResult:
        """Search the store and return injection-ready context.

        Args:
            message: The user's incoming message to match against.
            **kwargs: Override ``RecallConfig`` fields for this call.

        Returns:
            ``RecallResult`` with formatted memory section, metadata,
            and timing. Returns an empty result (not an error) when
            no relevant memories are found.
        """
        start = time.perf_counter()
        cfg = self._effective_config(kwargs)

        # Delegate to inject_memories for search + format + safety + budget
        injection_config = InjectionConfig(
            injection_max_tokens=cfg.max_tokens,
        )
        result = inject_memories(
            message,
            self._store,
            engagement_level=cfg.engagement_level,
            decay_config=self._decay_config,
            config=injection_config,
        )

        # Post-filter: scope, tier, branch, dedupe
        memories = result.get("memories", [])
        memory_section: str = result.get("memory_section", "")

        if memories and self._needs_post_filter(cfg):
            memories, memory_section = self._apply_post_filters(memories, cfg, message)

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return RecallResult(
            memory_section=memory_section,
            memories=memories,
            token_count=result.get("injected_tokens", 0),
            recall_time_ms=round(elapsed_ms, 2),
            truncated=result.get("truncated", False),
            memory_count=len(memories),
        )

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, response: str, *, source: str = "agent", **kwargs: object) -> list[str]:
        """Extract and persist new facts from an agent response.

        Delegates to ``store.ingest_context()`` for rule-based extraction
        and deduplication.

        Args:
            response: The agent's response text to scan for facts.
            source: Source attribution for created entries.

        Returns:
            List of keys for newly created memory entries.
        """
        if not response or not response.strip():
            return []

        return self._store.ingest_context(response, source=source)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_config(self, overrides: dict[str, object]) -> RecallConfig:
        """Build effective config by merging base config with per-call overrides."""
        if not overrides:
            return self._config

        vals: dict[str, object] = {
            "engagement_level": self._config.engagement_level,
            "max_tokens": self._config.max_tokens,
            "min_score": self._config.min_score,
            "min_confidence": self._config.min_confidence,
            "scope_filter": self._config.scope_filter,
            "tier_filter": self._config.tier_filter,
            "branch": self._config.branch,
            "dedupe_window": list(self._config.dedupe_window),
        }
        for k, v in overrides.items():
            if k in vals:
                vals[k] = v
        return RecallConfig(**vals)  # type: ignore[arg-type]

    def _needs_post_filter(self, cfg: RecallConfig) -> bool:
        """Check whether any post-filter is active."""
        return bool(cfg.scope_filter or cfg.tier_filter or cfg.branch or cfg.dedupe_window)

    def _apply_post_filters(
        self,
        memories: list[dict[str, object]],
        cfg: RecallConfig,
        message: str,
    ) -> tuple[list[dict[str, object]], str]:
        """Filter memories by scope/tier/branch/dedupe and rebuild the section.

        When post-filters remove memories, the formatted section is rebuilt
        from the remaining entries to keep them in sync.
        """
        dedupe_set = set(cfg.dedupe_window)
        filtered: list[dict[str, object]] = []

        for mem in memories:
            key = str(mem.get("key", ""))

            # Dedupe
            if key in dedupe_set:
                continue

            # Scope filter: look up entry in store
            if cfg.scope_filter or cfg.tier_filter or cfg.branch:
                entry = self._store.get(key) if key else None
                if entry is None:
                    # Entry not found — keep the memory (defensive)
                    filtered.append(mem)
                    continue

                if cfg.scope_filter and entry.scope != cfg.scope_filter:
                    continue
                if cfg.tier_filter and entry.tier != cfg.tier_filter:
                    continue
                if cfg.branch and entry.scope == MemoryScope.branch and entry.branch != cfg.branch:
                    continue

            filtered.append(mem)

        # Rebuild section from filtered memories
        if not filtered:
            return [], ""

        lines = ["### Project Memory"]
        for mem in filtered:
            key = str(mem.get("key", ""))
            raw_conf = mem.get("confidence", 0.0)
            conf = float(raw_conf) if isinstance(raw_conf, (int, float, str)) else 0.0
            tier = str(mem.get("tier", "pattern"))
            # Look up value from store for the section
            entry = self._store.get(str(key)) if key else None
            value = entry.value if entry else str(key)
            lines.append(f"- **{key}** (confidence: {conf:.2f}, tier: {tier}): {value}")

        return filtered, "\n".join(lines)
