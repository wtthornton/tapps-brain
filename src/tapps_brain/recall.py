"""Auto-recall orchestrator for pre-prompt memory injection (EPIC-003).

Provides a ``RecallOrchestrator`` that accepts an incoming user message,
searches the memory store for relevant entries, and returns injection-ready
context. Delegates formatting, safety, and token budget enforcement to
``inject_memories()``.

The orchestrator adds quality gates on top of injection:
- Scope / tier / branch filtering
- Deduplication against already-in-context memories
- Minimum confidence threshold for Hive results (``RecallConfig.min_confidence``)
- Timing measurement

Thread-safe: multiple concurrent ``recall()`` calls are safe.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypeVar

import structlog

from tapps_brain import recall_quality_buffer
from tapps_brain.injection import InjectionConfig, estimate_tokens, inject_memories
from tapps_brain.models import (
    KGEdgeView,
    KGEntityView,
    KGEvidenceView,
    MemoryEntry,
    MemoryScope,
    MemoryTier,
    RecallDiagnostics,
    RecallResult,
)
from tapps_brain.recall_diagnostics import RECALL_EMPTY_POST_FILTER
from tapps_brain.recall_digest import compute_recall_digest

if TYPE_CHECKING:
    from tapps_brain._protocols import HiveBackend, KnowledgeGraphBackend
    from tapps_brain.decay import DecayConfig
    from tapps_brain.retrieval import MemoryRetriever
    from tapps_brain.store import MemoryStore

T = TypeVar("T")

logger = structlog.get_logger(__name__)

# Default weight applied to Hive confidence when merging into local recall.
_DEFAULT_HIVE_RECALL_WEIGHT = 0.8


def _compute_recall_quality(
    memories: list[dict[str, object]],
) -> tuple[float | None, float | None]:
    """Compute ``(top_score, oldest_returned_age_days)`` for TAP-2094 telemetry.

    Returns ``(None, None)`` for an empty *memories* list.  Skips entries with
    a missing or malformed ``last_accessed`` when computing the oldest age —
    if every entry is malformed, returns ``oldest_age_days=None`` rather than
    silently reporting age=0.
    """
    if not memories:
        return None, None

    scores: list[float] = []
    ages_days: list[float] = []
    now = datetime.now(tz=UTC)
    for mem in memories:
        raw_score = mem.get("score", 0.0)
        if isinstance(raw_score, (int, float)):
            scores.append(float(raw_score))
        raw_ts = mem.get("last_accessed", "")
        if isinstance(raw_ts, str) and raw_ts:
            try:
                ts = datetime.fromisoformat(raw_ts)
                # Imported / legacy rows may be tz-naive — assume UTC (matches
                # tapps_brain.models._parse_iso / decay._days_since).
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                continue
            age = (now - ts).total_seconds() / 86400.0
            if age >= 0:
                ages_days.append(age)

    top_score = max(scores) if scores else None
    oldest_age = max(ages_days) if ages_days else None
    return top_score, oldest_age


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RecallConfig:
    """Configuration for the recall orchestrator."""

    engagement_level: str = "high"
    max_tokens: int = 3000
    min_score: float = 0.3
    min_confidence: float = 0.1
    scope_filter: MemoryScope | None = None
    tier_filter: MemoryTier | None = None
    branch: str | None = None
    memory_group: str | None = None
    dedupe_window: list[str] = field(default_factory=list)
    use_graph_boost: bool = False
    graph_boost_factor: float = 0.15
    # Temporal range filtering (Issue #70)
    since: str | None = None
    until: str | None = None
    time_field: str = "created_at"


# ---------------------------------------------------------------------------
# RecallOrchestrator
# ---------------------------------------------------------------------------


class RecallOrchestrator:
    """Orchestrates auto-recall: search → filter → inject → return.

    Delegates formatting/safety/budget to ``inject_memories()`` and adds
    quality gates (scope, tier, branch, deduplication, timing).

    Thread-safe: ``recall()`` keeps all per-call state on the stack — the
    orchestrator instance holds only immutable configuration after init.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        retriever: MemoryRetriever | None = None,
        config: RecallConfig | None = None,
        decay_config: DecayConfig | None = None,
        hive_store: HiveBackend | None = None,
        hive_recall_weight: float | None = None,
        hive_agent_profile: str = "repo-brain",
        hive_agent_id: str = "unknown",
        kg_backend: KnowledgeGraphBackend | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever
        self._config = config or RecallConfig()
        self._decay_config = decay_config
        self._hive_store = hive_store
        self._hive_recall_weight = hive_recall_weight
        self._hive_agent_profile = hive_agent_profile
        self._hive_agent_id = hive_agent_id
        self._kg_backend = kg_backend

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------

    def recall(self, message: str, **kwargs: object) -> RecallResult:  # noqa: PLR0915
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
            min_score=cfg.min_score,
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
            memory_group=cfg.memory_group,
            since=cfg.since,
            until=cfg.until,
            time_field=cfg.time_field,
            kg_backend=self._kg_backend,
        )

        diag_raw = result.get("recall_diagnostics")
        recall_diag: RecallDiagnostics | None
        if isinstance(diag_raw, dict):
            recall_diag = RecallDiagnostics.model_validate(diag_raw)
        else:
            recall_diag = None

        # Graph boost: boost scores of entries connected via relation graph
        memories_raw = result.get("memories", [])
        memories: list[Any] = memories_raw if isinstance(memories_raw, list) else []
        memory_section: str = result.get("memory_section", "") or ""

        if cfg.use_graph_boost and memories:
            memories = self._apply_graph_boost(memories, cfg.graph_boost_factor)

        # Hive recall: merge local + Hive results (EPIC-011).
        # Low engagement is the recall off-switch — inject_memories honors it
        # ("never inject"), so the Hive merge must too, or a low-engagement
        # recall with a Hive backend returns memories anyway.
        hive_truncated = False
        hive_merged = False
        hive_quality_warning: str | None = None
        if self._hive_store is not None and cfg.engagement_level != "low":
            hive_memories, _, hive_search_failed = self._search_hive(message, memories, cfg)
            if hive_search_failed:
                hive_quality_warning = "hive_search_unavailable"
            if hive_memories:
                memories = self._merge_hive_results(memories, hive_memories)
                hive_merged = True

        # Post-filter (scope, tier, branch, dedupe) BEFORE budget truncation —
        # entries the filter is about to discard must not consume token budget
        # and evict entries that would have fit.
        count_before_post_filter = len(memories)
        if memories and self._needs_post_filter(cfg):
            memories, memory_section = self._apply_post_filters(memories, cfg)

        if hive_merged and memories:
            # Enforce the recall token budget on the merged list — the
            # injection budget only covered local results, so unbounded
            # Hive additions could otherwise blow past cfg.max_tokens.
            memories, hive_truncated = self._truncate_to_budget(memories, cfg.max_tokens)
            # Rebuild section to include Hive results
            memory_section = self._rebuild_section(memories)

        hive_count = sum(1 for m in memories if m.get("source") == "hive")

        if not memories and count_before_post_filter > 0 and self._needs_post_filter(cfg):
            # Preserve the KG/lifecycle counters inject_memories computed
            # (mentions, graph hits, stale/low-confidence drops) — rebuilding
            # from scratch zeroed them exactly when diagnostics matter most.
            if recall_diag is not None:
                recall_diag = recall_diag.model_copy(
                    update={"empty_reason": RECALL_EMPTY_POST_FILTER}
                )
            else:
                recall_diag = RecallDiagnostics(empty_reason=RECALL_EMPTY_POST_FILTER)

        # Recompute token count from the final section so Hive additions are reflected.
        # inject_memories() token count only covers local results; _rebuild_section()
        # changes the section text, so we re-estimate to keep the count accurate.
        if not memories:
            token_count = 0
        elif memory_section:
            token_count = estimate_tokens(memory_section)
        else:
            token_count = result.get("injected_tokens", 0)

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # TAP-2094: compute top_score + oldest_returned_age_days and record
        # the sample into the in-process ring buffer before returning.
        top_score, oldest_age_days = _compute_recall_quality(memories)
        if recall_diag is not None:
            recall_diag = recall_diag.model_copy(
                update={
                    "top_score": top_score,
                    "oldest_returned_age_days": oldest_age_days,
                }
            )
        elif memories:
            recall_diag = RecallDiagnostics(
                top_score=top_score,
                oldest_returned_age_days=oldest_age_days,
            )
        project_id = getattr(self._store, "_project_id", None) or ""
        if project_id:
            recall_quality_buffer.record(
                project_id=project_id,
                top_score=top_score,
                oldest_returned_age_days=oldest_age_days,
                memory_count=len(memories),
            )

        # Extract KG fields from injection result (STORY-076.3).
        def _as_list(key: str, cls: type[T]) -> list[T]:
            items = result.get(key, [])
            return [x for x in items if isinstance(x, cls)]

        # Merge injection-level warnings (e.g. "memory search failed: ...")
        # with the hive outage warning so callers see both signals.
        injection_warning = result.get("quality_warning")
        warnings = [
            w for w in (injection_warning, hive_quality_warning) if isinstance(w, str) and w
        ]
        quality_warning = "; ".join(warnings) if warnings else None

        # TAP-6583: name the set that actually reached the prompt. Computed
        # here rather than inside inject_memories because the Hive merge,
        # post-filters, and budget truncation above all run afterwards — a
        # digest taken earlier would describe a candidate pool, not the prompt.
        recall_digest, memory_versions = compute_recall_digest(memories)

        return RecallResult(
            memory_section=memory_section,
            memories=memories,
            recall_digest=recall_digest,
            memory_versions=memory_versions,
            token_count=token_count,
            recall_time_ms=round(elapsed_ms, 2),
            truncated=bool(result.get("truncated", False)) or hive_truncated,
            memory_count=len(memories),
            hive_memory_count=hive_count,
            recall_diagnostics=recall_diag,
            quality_warning=quality_warning,
            entities=_as_list("entities", KGEntityView),
            edges=_as_list("edges", KGEdgeView),
            evidence=_as_list("evidence", KGEvidenceView),
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
    ) -> list[str]:
        """Extract and persist new facts from an agent response.

        Delegates to ``store.ingest_context()`` for rule-based extraction
        and deduplication.

        Args:
            response: The agent's response text to scan for facts.
            source: Source attribution for created entries.
            agent_scope: Hive propagation scope for captured facts —
                ``'private'`` (default), ``'domain'``, ``'hive'``, or ``'group:<name>'``.

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
        cfg: RecallConfig | None = None,
    ) -> tuple[list[dict[str, object]], int, bool]:
        """Search the Hive for relevant memories not already in local results.

        Returns ``(hive_memories, count, search_failed)``. The failure flag is
        returned (not stashed on ``self``) so concurrent ``recall()`` calls
        cannot clobber each other's outage signal.
        """
        if self._hive_store is None:
            return [], 0, False
        effective_cfg = cfg if cfg is not None else self._config

        # An explicitly passed constructor weight wins; otherwise consult the
        # store's profile-backed getter, falling back to the 0.8 default.
        # (Previously the getter unconditionally shadowed the constructor arg.)
        if self._hive_recall_weight is not None:
            eff_weight = self._hive_recall_weight
        else:
            eff_weight = _DEFAULT_HIVE_RECALL_WEIGHT
            _getter = getattr(self._store, "get_hive_recall_weight", None)
            if callable(_getter):
                try:
                    eff_weight = float(_getter())
                except (TypeError, ValueError):
                    eff_weight = _DEFAULT_HIVE_RECALL_WEIGHT

        local_keys = {str(m.get("key", "")) for m in local_memories}

        # Universal + profile namespace + Hive membership group namespaces (GitHub #52).
        namespaces = ["universal", self._hive_agent_profile]
        # TAP-6695: membership is project-scoped — a falsy project_id (no
        # resolved project context) makes get_agent_groups return [] rather
        # than matching the migration's fail-closed backfill sentinel.
        project_id = getattr(self._store, "_project_id", None) or ""
        try:
            extra_groups = self._hive_store.get_agent_groups(self._hive_agent_id, project_id)
        except Exception:
            logger.warning("hive_recall_agent_groups_failed", exc_info=True)
            extra_groups = []
        for g in extra_groups:
            if g not in namespaces:
                namespaces.append(g)
        try:
            hive_results = self._hive_store.search(
                message,
                namespaces=namespaces,
                min_confidence=effective_cfg.min_confidence,
                limit=20,
            )
        except Exception:
            logger.warning("hive_recall_search_failed", exc_info=True)
            # Report failure so recall() can surface quality_warning (empty ≠ healthy).
            return [], 0, True

        hive_memories: list[dict[str, object]] = []
        for entry in hive_results:
            key = str(entry.get("key", ""))
            if key in local_keys:
                continue  # Deduplicate — local wins

            # Apply hive weight to confidence
            raw_conf = entry.get("confidence", 0.6)
            conf = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.6
            score = round(conf * eff_weight, 4)
            # Apply the same quality floor local results get inside
            # inject_memories — otherwise min_score only gates local memories
            # and post-weight Hive scores far below it leak into the result.
            if score < effective_cfg.min_score:
                continue
            hive_memories.append(
                {
                    "key": key,
                    "confidence": score,
                    "tier": entry.get("tier", "pattern"),
                    "score": score,
                    "source": "hive",
                    "namespace": entry.get("namespace", "universal"),
                    "value": entry.get("value", ""),
                }
            )

        return hive_memories, len(hive_memories), False

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

    _SECTION_HEADER = "### Project Memory"

    @staticmethod
    def _format_memory_line(mem: dict[str, object]) -> str:
        """Format one memory as the section line ``_rebuild_section`` emits.

        Shared with ``_truncate_to_budget`` so the budget charges the *actual*
        line cost — a cheaper cost model made merged sections overshoot
        ``max_tokens`` while reporting ``truncated=False``.
        """
        key = str(mem.get("key", ""))
        raw_conf = mem.get("confidence", 0.0)
        conf = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0
        tier = str(mem.get("tier", "pattern"))
        value = str(mem.get("value") or key)
        src = str(mem.get("source", "local"))
        origin = f" [hive:{mem.get('namespace', '')}]" if src == "hive" else ""
        return f"- **{key}** (confidence: {conf:.2f}, tier: {tier}{origin}): {value}"

    @classmethod
    def _truncate_to_budget(
        cls,
        memories: list[dict[str, object]],
        max_tokens: int,
    ) -> tuple[list[dict[str, object]], bool]:
        """Cut the merged memory list to fit *max_tokens* (estimate-based).

        Walks the list in ranked order, accumulating the estimated token cost
        of each formatted line (the exact format ``_rebuild_section`` emits,
        plus the section header), and stops once the budget is exceeded.
        Always keeps at least the first memory (mirrors the injection budget
        rule). Returns ``(kept_memories, truncated)``.
        """
        kept: list[dict[str, object]] = []
        used = estimate_tokens(cls._SECTION_HEADER)
        for mem in memories:
            cost = estimate_tokens(cls._format_memory_line(mem))
            if kept and used + cost > max_tokens:
                break
            kept.append(mem)
            used += cost
        return kept, len(kept) < len(memories)

    @classmethod
    def _rebuild_section(cls, memories: list[dict[str, object]]) -> str:
        """Rebuild the formatted memory section from merged results."""
        if not memories:
            return ""
        lines = [cls._SECTION_HEADER]
        lines.extend(cls._format_memory_line(mem) for mem in memories)
        return "\n".join(lines)

    def _apply_graph_boost(
        self,
        memories: list[dict[str, object]],
        boost_factor: float,
    ) -> list[dict[str, object]]:
        """Boost scores of memories connected via the relation graph.

        For each memory in the result set, find graph-connected entries.
        If a connected entry is also in the result set, boost its score
        additively by ``boost_factor / hop_distance`` (a 2-hop neighbour
        gets half the boost of a 1-hop neighbour), capped at 1.0.  The
        boosted list is re-sorted by descending score.
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
            item = mem
            if key in connected:
                hop = connected[key]
                # Boost inversely proportional to hop distance
                hop_boost = boost_factor / hop
                raw_score = mem.get("score", 0.0)
                score = float(raw_score) if isinstance(raw_score, (int, float)) else 0.0
                new_score = min(score + hop_boost, 1.0)
                item = {**mem, "score": new_score, "graph_boosted": True}
            boosted.append(item)

        # Re-sort by score descending
        def _score(m: dict[str, object]) -> float:
            raw = m.get("score", 0.0)
            return float(raw) if isinstance(raw, (int, float)) else 0.0

        boosted.sort(key=_score, reverse=True)
        return boosted

    def _effective_config(self, overrides: dict[str, object]) -> RecallConfig:
        """Build effective config by merging base config with per-call overrides.

        Derives the field set from the dataclass definition so newly added
        ``RecallConfig`` fields (e.g. ``since`` / ``until`` / ``time_field``)
        are never silently dropped or reset to defaults.
        """
        if not overrides:
            return self._config

        vals: dict[str, object] = dataclasses.asdict(self._config)
        vals["dedupe_window"] = list(self._config.dedupe_window)
        unknown = [k for k in overrides if k not in vals]
        if unknown:
            # A typo'd override (max_token vs max_tokens) silently running
            # with defaults is the worst failure mode for an override API.
            logger.warning(
                "recall.unknown_config_overrides",
                unknown=sorted(unknown),
                known=sorted(vals),
            )
        for k, v in overrides.items():
            if k in vals:
                vals[k] = v
        return RecallConfig(**vals)  # type: ignore[arg-type]

    def _needs_post_filter(self, cfg: RecallConfig) -> bool:
        """Check whether any post-filter is active.

        ``memory_group`` is deliberately absent: it is threaded into ranked
        retrieval (``inject_memories`` → ``retriever.search``), so every local
        result already belongs to the group and hive results are exempt —
        re-checking here was per-entry dead work on the hot path.
        """
        return bool(cfg.scope_filter or cfg.tier_filter or cfg.branch or cfg.dedupe_window)

    def _passes_entry_filters(
        self,
        entry: MemoryEntry,
        cfg: RecallConfig,
    ) -> bool:
        """Return True when *entry* satisfies all active scope/tier/branch filters."""
        if cfg.scope_filter and entry.scope != cfg.scope_filter:
            return False
        if cfg.tier_filter and entry.tier != cfg.tier_filter:
            return False
        return not (cfg.branch and entry.scope == MemoryScope.branch and entry.branch != cfg.branch)

    def _build_filtered_section(
        self,
        filtered: list[dict[str, object]],
        entry_cache: dict[str, MemoryEntry | None],
    ) -> str:
        """Format *filtered* memories into the '### Project Memory' section string."""
        lines = ["### Project Memory"]
        for mem in filtered:
            key = str(mem.get("key", ""))
            raw_conf = mem.get("confidence", 0.0)
            # Only accept numeric types — strings could raise ValueError in float()
            conf = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0
            tier = str(mem.get("tier", "pattern"))
            # Reuse cached entry when available; fall back to a read-only lookup.
            store_entry: MemoryEntry | None = (
                entry_cache[key] if key in entry_cache else (self._peek_entry(key) if key else None)
            )
            # Hive memories are not in the local store — use the carried value
            # (and origin marker) rather than degrading to the bare key.
            value = store_entry.value if store_entry is not None else str(mem.get("value") or key)
            src = str(mem.get("source", "local"))
            origin = f" [hive:{mem.get('namespace', '')}]" if src == "hive" else ""
            lines.append(f"- **{key}** (confidence: {conf:.2f}, tier: {tier}{origin}): {value}")
        return "\n".join(lines)

    def _peek_entry(self, key: str) -> MemoryEntry | None:
        """Read-only entry lookup for filtering/formatting.

        Avoids ``store.get()``, which bumps ``access_count`` and resets
        ``last_accessed`` — filtering must not reinforce entries it may
        then discard (frequency/recency scoring would be skewed).
        """
        ensure = getattr(self._store, "_ensure_entry_cached", None)
        if callable(ensure):
            try:
                entry = ensure(key)
            except Exception:
                return None
            return entry if isinstance(entry, MemoryEntry) else None
        return self._store.get(key)

    def _apply_post_filters(
        self,
        memories: list[dict[str, object]],
        cfg: RecallConfig,
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

            # Scope / tier / branch filter: read-only lookup so filtering
            # does not bump access_count on entries it may discard.
            if cfg.scope_filter or cfg.tier_filter or cfg.branch:
                entry = self._peek_entry(key) if key else None
                if key:
                    entry_cache[key] = entry  # cache for section rebuild below
                if entry is None:
                    if str(mem.get("source", "")) == "hive":
                        # Hive memories never resolve locally — enforce the
                        # tier filter from the carried tier value instead of
                        # keeping them unconditionally. Scope/branch filters
                        # don't apply: hive rows carry no local scope.
                        if cfg.tier_filter and str(mem.get("tier", "")) != str(
                            cfg.tier_filter.value
                            if isinstance(cfg.tier_filter, MemoryTier)
                            else cfg.tier_filter
                        ):
                            continue
                        filtered.append(mem)
                        continue
                    # Local entry vanished mid-recall — keep it (defensive).
                    filtered.append(mem)
                    continue
                if not self._passes_entry_filters(entry, cfg):
                    continue

            filtered.append(mem)

        if not filtered:
            return [], ""

        return filtered, self._build_filtered_section(filtered, entry_cache)
