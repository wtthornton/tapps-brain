"""Memory injection into expert and research responses.

Provides a helper that retrieves relevant memories and formats them
for injection into tool responses. RAG safety is applied as a
defense-in-depth measure before injection.

**Ordering:** Injected memories follow the retriever's ranking: composite score
**descending** (same order as ``MemoryRetriever.search``). There is no
diversity re-ranking in this helper.

**Token counts:** By default, ``estimate_tokens`` (char heuristic) sizes each
candidate line for the injection budget. Pass ``InjectionConfig.count_tokens``
for tokenizer-aligned weights (optional explicit dependency — e.g. tiktoken —
stays in the caller's environment).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import structlog

from tapps_brain.profile import HybridFusionConfig
from tapps_brain.recall_diagnostics import (
    RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
    RECALL_EMPTY_ENGAGEMENT_LOW,
    RECALL_EMPTY_GROUP_EMPTY,
    RECALL_EMPTY_NO_RANKED_MATCHES,
    RECALL_EMPTY_RAG_BLOCKED,
    RECALL_EMPTY_SEARCH_FAILED,
    RECALL_EMPTY_STORE_EMPTY,
)
from tapps_brain.retrieval import MemoryRetriever
from tapps_brain.safety import check_content_safety

if TYPE_CHECKING:
    from tapps_brain.decay import DecayConfig
    from tapps_brain.lexical import LexicalRetrievalConfig
    from tapps_brain.profile import ScoringConfig
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_INJECT_HIGH = 5
_MAX_INJECT_MEDIUM = 3
# Minimum composite score to inject a result. Set to 0.2 (not 0.3) to account for
# source-trust multipliers: an "agent" source with trust=0.7 on a borderline entry
# (~0.36 raw) would score ~0.25 after the multiplier, which still deserves inclusion.
_MIN_SCORE = 0.2
_MIN_CONFIDENCE_MEDIUM = 0.5


def _visible_entry_count(store: MemoryStore, memory_group: str | None) -> int:
    """Count entries visible for retrieval (respects ``memory_group`` when set)."""
    return len(store.list_all(memory_group=memory_group))


def _recall_diag_payload(
    *,
    empty_reason: str | None,
    retriever_hits: int = 0,
    visible_entries: int | None = None,
) -> dict[str, Any]:
    return {
        "empty_reason": empty_reason,
        "retriever_hits": retriever_hits,
        "visible_entries": visible_entries,
    }


def _injection_empty(
    *,
    empty_reason: str | None,
    retriever_hits: int = 0,
    visible_entries: int | None = None,
    injection_telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    telem = injection_telemetry or _default_injection_telemetry("heuristic")
    return {
        "memory_section": "",
        "memory_injected": 0,
        "memories": [],
        "truncated": False,
        "injected_tokens": 0,
        "injection_telemetry": telem,
        "recall_diagnostics": _recall_diag_payload(
            empty_reason=empty_reason,
            retriever_hits=retriever_hits,
            visible_entries=visible_entries,
        ),
    }


def _default_injection_telemetry(token_counter: str) -> dict[str, Any]:
    """Stable shape for metrics on every ``inject_memories`` return path."""
    return {
        "dropped_below_min_score": 0,
        "dropped_by_safety": 0,
        "omitted_by_token_budget": 0,
        "token_counter": token_counter,
        "rerank_applied": False,
        "rerank_provider": None,
        "rerank_candidates_in": None,
        "rerank_top_k": None,
        "rerank_latency_ms": None,
        "rerank_results_out": None,
        "rerank_error": None,
    }


def _merge_rerank_telemetry(telem: dict[str, Any], retriever: MemoryRetriever) -> None:
    """Copy ``retriever.last_rerank_stats`` into injection telemetry (EPIC-042.6)."""
    stats = getattr(retriever, "last_rerank_stats", None)
    if not stats:
        return
    telem["rerank_applied"] = bool(stats.get("applied"))
    telem["rerank_provider"] = stats.get("provider")
    telem["rerank_candidates_in"] = stats.get("candidates_in")
    telem["rerank_top_k"] = stats.get("top_k")
    telem["rerank_latency_ms"] = stats.get("latency_ms")
    telem["rerank_results_out"] = stats.get("results_out")
    telem["rerank_error"] = stats.get("error")


def _entry_token_cost(text: str, count_tokens: Callable[[str], int] | None) -> int:
    """Token weight for one formatted line; always at least 1 for non-empty budgeting."""
    if count_tokens is None:
        return estimate_tokens(text)
    return max(1, int(count_tokens(text)))


def _telemetry_token_label(count_tokens: Callable[[str], int] | None) -> str:
    return "custom" if count_tokens is not None else "heuristic"


@dataclass
class InjectionConfig:
    """Configuration for memory injection.

    Standalone replacement for reading TappsMCP settings.
    Consumers pass this to control reranker and token budget behavior.

    ``count_tokens``: optional callable ``(text) -> int`` for tokenizer-aligned
    budgets (caller supplies tiktoken or another backend). When ``None``, the
    built-in ``estimate_tokens`` heuristic is used.
    """

    reranker_enabled: bool = True
    reranker_top_k: int = 10
    injection_max_tokens: int = 2000
    count_tokens: Callable[[str], int] | None = None


def estimate_tokens(text: str) -> int:
    """Estimate token count. Approximation: 1 token ~ 4 characters."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Injection logic
# ---------------------------------------------------------------------------


def inject_memories(  # noqa: PLR0915
    question: str,
    store: MemoryStore,
    engagement_level: str = "high",
    *,
    decay_config: DecayConfig | None = None,
    config: InjectionConfig | None = None,
    scoring_config: ScoringConfig | None = None,
    lexical_config: LexicalRetrievalConfig | None = None,
    memory_group: str | None = None,
    since: str | None = None,
    until: str | None = None,
    time_field: str = "created_at",
) -> dict[str, Any]:
    """Search for and format relevant memories for injection.

    Args:
        question: The user's query to match against memories.
        store: The memory store to search.
        engagement_level: "high", "medium", or "low".
        decay_config: Optional decay configuration.
        config: Optional injection configuration (token budget, reranker, optional
            ``count_tokens`` callable). Defaults to ``InjectionConfig()``.
        scoring_config: Optional scoring configuration (weights + source_trust
            multipliers). When ``None``, the store's active profile scoring
            config is used if available, otherwise module defaults apply.
        memory_group: When set, restrict ranked retrieval to this project-local
            group (GitHub #49). Hive merge in recall is unchanged.
        lexical_config: BM25 / lexical options. When ``None``, uses the store's
            active profile ``lexical`` block when present.

    Returns:
        Dict with:
        - ``memory_section``: Formatted markdown section (or empty string).
        - ``memory_injected``: Number of memories injected.
        - ``memories``: List of injected memory summaries (same order as ranking).
        - ``truncated``: True if any candidate within the per-engagement cap was
          omitted due to the token budget.
        - ``injected_tokens``: Sum of token weights for included lines.
        - ``injection_telemetry``: Counts — ``dropped_below_min_score``,
          ``dropped_by_safety``, ``omitted_by_token_budget``, and
          ``token_counter`` (``\"heuristic\"`` or ``\"custom\"``). When reranking
          ran during ``search()``, also ``rerank_applied``, ``rerank_provider``,
          ``rerank_candidates_in``, ``rerank_top_k``, ``rerank_latency_ms``,
          ``rerank_results_out``, ``rerank_error`` (see EPIC-042.6).
        - ``recall_diagnostics``: ``empty_reason`` (code or null), ``retriever_hits``,
          ``visible_entries`` — for agents when recall is empty.
    """
    config = config or InjectionConfig()
    tlabel = _telemetry_token_label(config.count_tokens)

    # Low engagement: never inject
    if engagement_level == "low":
        vis = _visible_entry_count(store, memory_group)
        return _injection_empty(
            empty_reason=RECALL_EMPTY_ENGAGEMENT_LOW,
            retriever_hits=0,
            visible_entries=vis,
            injection_telemetry=_default_injection_telemetry(tlabel),
        )

    # Resolve scoring_config: prefer explicit arg, fall back to store profile.
    # This ensures source_trust multipliers and scoring weights from the active
    # profile are respected rather than always using module-level defaults.
    profile = getattr(store, "profile", None)
    if scoring_config is None and profile is not None:
        scoring_config = getattr(profile, "scoring", None)
    if lexical_config is None and profile is not None:
        lexical_config = getattr(profile, "lexical", None)
    hybrid_config = None
    if profile is not None:
        raw_hf = getattr(profile, "hybrid_fusion", None)
        if isinstance(raw_hf, HybridFusionConfig):
            hybrid_config = raw_hf

    ruleset_ver: str | None = None
    if profile is not None:
        _safety_prof = getattr(profile, "safety", None)
        if _safety_prof is not None:
            ruleset_ver = getattr(_safety_prof, "ruleset_version", None)
    injection_metrics = getattr(store, "_metrics", None)

    from tapps_brain.reranker import get_reranker, reranker_provider_label

    reranker = get_reranker(enabled=config.reranker_enabled) if config.reranker_enabled else None
    retriever = MemoryRetriever(
        config=decay_config,
        reranker=reranker,
        semantic_enabled=True,
        hybrid_config=hybrid_config,
        reranker_enabled=config.reranker_enabled,
        reranker_provider=reranker_provider_label(reranker) if reranker else "noop",
        relations_enabled=True,
        scoring_config=scoring_config,
        lexical_config=lexical_config,
    )

    # Determine limits based on engagement level
    if engagement_level == "medium":
        max_inject = _MAX_INJECT_MEDIUM
        min_confidence = _MIN_CONFIDENCE_MEDIUM
    else:
        max_inject = _MAX_INJECT_HIGH
        min_confidence = _MIN_SCORE

    try:
        results = retriever.search(
            question,
            store,
            limit=max_inject,
            min_confidence=min_confidence,
            memory_group=memory_group,
            since=since,
            until=until,
            time_field=time_field,
        )
    except Exception:
        logger.warning(
            "memory_injection_search_failed",
            question=question[:80],
            exc_info=True,
        )
        vis_err: int | None = None
        try:
            vis_err = _visible_entry_count(store, memory_group)
        except Exception:
            vis_err = None
        return _injection_empty(
            empty_reason=RECALL_EMPTY_SEARCH_FAILED,
            retriever_hits=0,
            visible_entries=vis_err,
            injection_telemetry=_default_injection_telemetry(tlabel),
        )

    telem = _default_injection_telemetry(tlabel)
    _merge_rerank_telemetry(telem, retriever)

    visible = _visible_entry_count(store, memory_group)
    n_retriever = len(results)

    # Filter by minimum score
    dropped_below_min_score = n_retriever - sum(1 for r in results if r.score >= _MIN_SCORE)
    results = [r for r in results if r.score >= _MIN_SCORE]
    telem["dropped_below_min_score"] = dropped_below_min_score

    if not results:
        if n_retriever == 0:
            if visible == 0:
                reason = RECALL_EMPTY_GROUP_EMPTY if memory_group else RECALL_EMPTY_STORE_EMPTY
            else:
                reason = RECALL_EMPTY_NO_RANKED_MATCHES
        else:
            reason = RECALL_EMPTY_BELOW_SCORE_THRESHOLD
        return _injection_empty(
            empty_reason=reason,
            retriever_hits=n_retriever,
            visible_entries=visible,
            injection_telemetry=telem,
        )

    # RAG safety check on values before injection (defense-in-depth)
    safe_results = []
    for scored in results:
        safety = check_content_safety(
            scored.entry.value,
            ruleset_version=ruleset_ver,
            metrics=injection_metrics,
        )
        if safety.safe:
            if safety.sanitised_content is not None:
                scored = scored.model_copy(
                    update={
                        "entry": scored.entry.model_copy(
                            update={"value": safety.sanitised_content},
                        ),
                    },
                )
            safe_results.append(scored)
        else:
            logger.warning(
                "memory_injection_blocked",
                key=scored.entry.key,
                patterns=safety.flagged_patterns,
                ruleset_version=safety.ruleset_version,
            )

    dropped_by_safety = len(results) - len(safe_results)

    if not safe_results:
        telem["dropped_by_safety"] = dropped_by_safety
        return _injection_empty(
            empty_reason=RECALL_EMPTY_RAG_BLOCKED,
            retriever_hits=n_retriever,
            visible_entries=visible,
            injection_telemetry=telem,
        )

    # Context budget enforcement (order = retriever score descending, capped by max_inject)
    max_tokens = config.injection_max_tokens
    budgeted_results: list[Any] = []
    used_tokens = 0
    candidates = safe_results[:max_inject]
    for scored in candidates:
        entry = scored.entry
        tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        entry_text = (
            f"- **{entry.key}** (confidence: {scored.effective_confidence:.2f}, "
            f"tier: {tier}): {entry.value}"
        )
        entry_tokens = _entry_token_cost(entry_text, config.count_tokens)
        if used_tokens + entry_tokens > max_tokens and budgeted_results:
            break
        budgeted_results.append(scored)
        used_tokens += entry_tokens

    truncated = len(budgeted_results) < len(candidates)
    omitted_by_token_budget = len(candidates) - len(budgeted_results)

    # Format the injection section
    lines = ["### Project Memory"]
    summaries = []
    for scored in budgeted_results:
        entry = scored.entry
        tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        lines.append(
            f"- **{entry.key}** (confidence: {scored.effective_confidence:.2f}, "
            f"tier: {tier}): {entry.value}"
        )
        summaries.append(
            {
                "key": entry.key,
                "value": entry.value,
                "confidence": scored.effective_confidence,
                "tier": tier,
                "score": scored.score,
                "stale": scored.stale,
            }
        )

    telem["dropped_by_safety"] = dropped_by_safety
    telem["omitted_by_token_budget"] = omitted_by_token_budget

    return {
        "memory_section": "\n".join(lines),
        "memory_injected": len(budgeted_results),
        "memories": summaries,
        "truncated": truncated,
        "injected_tokens": used_tokens,
        "injection_telemetry": telem,
        "recall_diagnostics": _recall_diag_payload(
            empty_reason=None,
            retriever_hits=n_retriever,
            visible_entries=visible,
        ),
    }


def append_memory_to_answer(answer: str, memory_result: dict[str, Any]) -> str:
    """Append memory section to an expert/research answer if available."""
    section = memory_result.get("memory_section", "")
    if not section:
        return answer
    return f"{answer}\n\n---\n\n{section}"
