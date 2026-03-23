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

from tapps_brain.injection import InjectionConfig, estimate_tokens, inject_memories
from tapps_brain.models import MemoryEntry, MemoryScope, MemoryTier, RecallResult

if TYPE_CHECKING:
    from tapps_brain.decay import DecayConfig
    from tapps_brain.hive import HiveStore
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
    use_graph_boost: bool = False
    graph_boost_factor: float = 0.15


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
        hive_store: HiveStore | None = None,
        hive_recall_weight: float = 0.8,
        hive_agent_profile: str = "repo-brain",
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._config = config or RecallConfig()
        self._decay_config = decay_config
        self._hive_store = hive_store
        self._hive_recall_weight = hive_recall_weight
        self._hive_agent_profile = hive_agent_profile

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

        # Delegate to inject_memories for search + format + safety + budget.
        # Thread the profile's scoring_config so source_trust multipliers and
        # weight overrides from the active profile are applied consistently.
        injection_config = InjectionConfig(
            injection_max_tokens=cfg.max_tokens,
        )
        profile = getattr(self._store, "profile", None)
        scoring_config = getattr(profile, "scoring", None) if profile is not None else None
        result = inject_memories(
            message,
            self._store,
            engagement_level=cfg.engagement_level,
            decay_config=self._decay_config,
            config=injection_config,
            scoring_config=scoring_config,
        )

        # Graph boost: boost scores of entries connected via relation graph
        memories = result.get("memories", [])
        memory_section: str = result.get("memory_section", "")

        if cfg.use_graph_boost and memories:
            memories = self._apply_graph_boost(memories, cfg.graph_boost_factor)

        # Hive recall: merge local + Hive results (EPIC-011)
        hive_count = 0
        if self._hive_store is not None:
            hive_memories, hive_count = self._search_hive(message, memories)
            if hive_memories:
                memories = self._merge_hive_results(memories, hive_memories)
                # Rebuild section to include Hive results
                memory_section = self._rebuild_section(memories)

        # Post-filter: scope, tier, branch, dedupe

        if memories and self._needs_post_filter(cfg):
            memories, memory_section = self._apply_post_filters(memories, cfg, message)
            # Recount Hive memories after post-filtering — some may have been removed.
            hive_count = sum(1 for m in memories if m.get("source") == "hive")

        # Recompute token count from the final section so Hive additions are reflected.
        # inject_memories() token count only covers local results; _rebuild_section()
        # changes the section text, so we re-estimate to keep the count accurate.
        token_count = (
            estimate_tokens(memory_section) if memory_section else result.get("injected_tokens", 0)
        )

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return RecallResult(
            memory_section=memory_section,
            memories=memories,
            token_count=token_count,
            recall_time_ms=round(elapsed_ms, 2),
            truncated=result.get("truncated", False),
            memory_count=len(memories),
            hive_memory_count=hive_count,
        )

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        response: str,
        *,
        source: str = "agent",
        agent_scope: str = "private",
        **kwargs: object,
    ) -> list[str]:
        """Extract and persist new facts from an agent response.

        Delegates to ``store.ingest_context()`` for rule-based extraction
        and deduplication.

        Args:
            response: The agent's response text to scan for facts.
            source: Source attribution for created entries.
            agent_scope: Hive propagation scope for captured facts —
                ``'private'`` (default), ``'domain'``, or ``'hive'``.

        Returns:
            List of keys for newly created memory entries.
        """
        if not response or not response.strip():
            return []

        return self._store.ingest_context(response, source=source, agent_scope=agent_scope)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Hive recall helpers (EPIC-011)
    # ------------------------------------------------------------------

    def _search_hive(
        self,
        message: str,
        local_memories: list[dict[str, object]],
    ) -> tuple[list[dict[str, object]], int]:
        """Search the Hive for relevant memories not already in local results.

        Returns (hive_memories, count).
        """
        if self._hive_store is None:
            return [], 0

        eff_weight = self._hive_recall_weight
        _getter = getattr(self._store, "get_hive_recall_weight", None)
        if callable(_getter):
            try:
                eff_weight = float(_getter())
            except (TypeError, ValueError):
                eff_weight = self._hive_recall_weight

        local_keys = {str(m.get("key", "")) for m in local_memories}

        # Search universal + agent's domain namespace
        namespaces = ["universal", self._hive_agent_profile]
        try:
            hive_results = self._hive_store.search(
                message,
                namespaces=namespaces,
                min_confidence=self._config.min_confidence,
                limit=20,
            )
        except Exception:
            logger.debug("hive_recall_search_failed", exc_info=True)
            return [], 0

        hive_memories: list[dict[str, object]] = []
        for entry in hive_results:
            key = str(entry.get("key", ""))
            if key in local_keys:
                continue  # Deduplicate — local wins

            # Apply hive weight to confidence
            raw_conf = entry.get("confidence", 0.6)
            conf = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.6
            hive_memories.append(
                {
                    "key": key,
                    "confidence": round(conf * eff_weight, 4),
                    "tier": entry.get("tier", "pattern"),
                    "score": round(conf * eff_weight, 4),
                    "source": "hive",
                    "namespace": entry.get("namespace", "universal"),
                    "value": entry.get("value", ""),
                }
            )

        return hive_memories, len(hive_memories)

    @staticmethod
    def _merge_hive_results(
        local: list[dict[str, object]],
        hive: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """Merge local and Hive results, sorted by score descending."""
        merged = [*local, *hive]

        def _score(m: dict[str, object]) -> float:
            raw = m.get("score", 0.0)
            return float(raw) if isinstance(raw, (int, float)) else 0.0

        merged.sort(key=_score, reverse=True)
        return merged

    @staticmethod
    def _rebuild_section(memories: list[dict[str, object]]) -> str:
        """Rebuild the formatted memory section from merged results."""
        if not memories:
            return ""
        lines = ["### Project Memory"]
        for mem in memories:
            key = str(mem.get("key", ""))
            raw_conf = mem.get("confidence", 0.0)
            conf = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0
            tier = str(mem.get("tier", "pattern"))
            value = str(mem.get("value", key))
            src = str(mem.get("source", "local"))
            origin = f" [hive:{mem.get('namespace', '')}]" if src == "hive" else ""
            lines.append(f"- **{key}** (confidence: {conf:.2f}, tier: {tier}{origin}): {value}")
        return "\n".join(lines)

    def _apply_graph_boost(
        self,
        memories: list[dict[str, object]],
        boost_factor: float,
    ) -> list[dict[str, object]]:
        """Boost scores of memories connected via the relation graph.

        For each memory in the result set, find graph-connected entries.
        If a connected entry is also in the result set, boost its score
        by *boost_factor* (additive, capped at 1.0).  The boosted list
        is re-sorted by descending score.
        """
        result_keys = {str(m.get("key", "")) for m in memories}
        # Collect all graph-connected keys and their hop distances
        connected: dict[str, int] = {}
        for mem in memories:
            key = str(mem.get("key", ""))
            if not key:
                continue
            try:
                related = self._store.find_related(key, max_hops=2)
            except KeyError:
                continue
            for rel_key, hop in related:
                if rel_key in result_keys and (
                    rel_key not in connected or hop < connected[rel_key]
                ):
                    connected[rel_key] = hop

        if not connected:
            return memories

        # Apply boost: closer hops get more boost
        boosted: list[dict[str, object]] = []
        for mem in memories:
            key = str(mem.get("key", ""))
            if key in connected:
                hop = connected[key]
                # Boost inversely proportional to hop distance
                hop_boost = boost_factor / hop
                raw_score = mem.get("score", 0.0)
                score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
                new_score = min(score + hop_boost, 1.0)
                mem = {**mem, "score": new_score, "graph_boosted": True}
            boosted.append(mem)

        # Re-sort by score descending
        def _score(m: dict[str, object]) -> float:
            raw = m.get("score", 0.0)
            return float(raw) if isinstance(raw, (int, float)) else 0.0

        boosted.sort(key=_score, reverse=True)
        return boosted

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
            "use_graph_boost": self._config.use_graph_boost,
            "graph_boost_factor": self._config.graph_boost_factor,
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
        # Cache entries fetched during filtering so the section rebuild can reuse them.
        entry_cache: dict[str, MemoryEntry | None] = {}

        for mem in memories:
            key = str(mem.get("key", ""))

            # Dedupe
            if key in dedupe_set:
                continue

            # Scope / tier / branch filter: look up entry in store
            if cfg.scope_filter or cfg.tier_filter or cfg.branch:
                entry = self._store.get(key) if key else None
                if key:
                    entry_cache[key] = entry  # cache for section rebuild below
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
            # Only accept numeric types — strings could raise ValueError in float()
            conf = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0
            tier = str(mem.get("tier", "pattern"))
            # Reuse cached entry when available; fall back to a fresh lookup.
            cached = entry_cache.get(key)
            store_entry: MemoryEntry | None = (
                cached if key in entry_cache else (self._store.get(key) if key else None)
            )
            value = store_entry.value if store_entry else str(key)
            lines.append(f"- **{key}** (confidence: {conf:.2f}, tier: {tier}): {value}")

        return filtered, "\n".join(lines)
