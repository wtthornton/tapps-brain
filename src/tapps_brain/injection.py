"""Memory injection into expert and research responses.

Provides a helper that retrieves relevant memories and formats them
for injection into tool responses. RAG safety is applied as a
defense-in-depth measure before injection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.retrieval import MemoryRetriever
from tapps_brain.safety import check_content_safety

if TYPE_CHECKING:
    from tapps_brain.decay import DecayConfig
    from tapps_brain.profile import ScoringConfig
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_INJECT_HIGH = 5
_MAX_INJECT_MEDIUM = 3
_MIN_SCORE = 0.3
_MIN_CONFIDENCE_MEDIUM = 0.5


@dataclass
class InjectionConfig:
    """Configuration for memory injection.

    Standalone replacement for reading TappsMCP settings.
    Consumers pass this to control reranker and token budget behavior.
    """

    reranker_enabled: bool = False
    reranker_provider: str = "noop"
    reranker_top_k: int = 10
    reranker_api_key: str | None = None
    injection_max_tokens: int = 2000


def estimate_tokens(text: str) -> int:
    """Estimate token count. Approximation: 1 token ~ 4 characters."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Injection logic
# ---------------------------------------------------------------------------


def inject_memories(
    question: str,
    store: MemoryStore,
    engagement_level: str = "high",
    *,
    decay_config: DecayConfig | None = None,
    config: InjectionConfig | None = None,
    scoring_config: ScoringConfig | None = None,
) -> dict[str, Any]:
    """Search for and format relevant memories for injection.

    Args:
        question: The user's query to match against memories.
        store: The memory store to search.
        engagement_level: "high", "medium", or "low".
        decay_config: Optional decay configuration.
        config: Optional injection configuration. Defaults to InjectionConfig().
        scoring_config: Optional scoring configuration (weights + source_trust
            multipliers). When ``None``, the store's active profile scoring
            config is used if available, otherwise module defaults apply.

    Returns:
        Dict with:
        - ``memory_section``: Formatted markdown section (or empty string).
        - ``memory_injected``: Number of memories injected.
        - ``memories``: List of injected memory summaries.
    """
    # Low engagement: never inject
    if engagement_level == "low":
        return {"memory_section": "", "memory_injected": 0, "memories": []}

    config = config or InjectionConfig()

    # Resolve scoring_config: prefer explicit arg, fall back to store profile.
    # This ensures source_trust multipliers and scoring weights from the active
    # profile are respected rather than always using module-level defaults.
    if scoring_config is None:
        profile = getattr(store, "profile", None)
        if profile is not None:
            scoring_config = getattr(profile, "scoring", None)

    from tapps_brain.reranker import get_reranker

    reranker = (
        get_reranker(
            enabled=config.reranker_enabled,
            provider=config.reranker_provider,
            top_k=config.reranker_top_k,
            api_key=config.reranker_api_key,
        )
        if config.reranker_enabled
        else None
    )
    retriever = MemoryRetriever(
        config=decay_config,
        reranker=reranker,
        reranker_enabled=config.reranker_enabled,
        scoring_config=scoring_config,
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
        )
    except Exception:
        logger.debug("memory_injection_search_failed", question=question[:80])
        return {"memory_section": "", "memory_injected": 0, "memories": []}

    # Filter by minimum score
    results = [r for r in results if r.score >= _MIN_SCORE]

    if not results:
        return {"memory_section": "", "memory_injected": 0, "memories": []}

    # RAG safety check on values before injection (defense-in-depth)
    safe_results = []
    for scored in results:
        safety = check_content_safety(scored.entry.value)
        if safety.safe:
            safe_results.append(scored)
        else:
            logger.warning(
                "memory_injection_blocked",
                key=scored.entry.key,
                patterns=safety.flagged_patterns,
            )

    if not safe_results:
        return {"memory_section": "", "memory_injected": 0, "memories": []}

    # Context budget enforcement
    max_tokens = config.injection_max_tokens
    budgeted_results: list[Any] = []
    used_tokens = 0
    for scored in safe_results[:max_inject]:
        entry = scored.entry
        tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        entry_text = (
            f"- **{entry.key}** (confidence: {scored.effective_confidence:.2f}, "
            f"tier: {tier}): {entry.value}"
        )
        entry_tokens = estimate_tokens(entry_text)
        if used_tokens + entry_tokens > max_tokens and budgeted_results:
            break
        budgeted_results.append(scored)
        used_tokens += entry_tokens

    truncated = len(budgeted_results) < len(safe_results[:max_inject])

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
                "confidence": scored.effective_confidence,
                "tier": tier,
                "score": scored.score,
                "stale": scored.stale,
            }
        )

    return {
        "memory_section": "\n".join(lines),
        "memory_injected": len(budgeted_results),
        "memories": summaries,
        "truncated": truncated,
        "injected_tokens": used_tokens,
    }


def append_memory_to_answer(answer: str, memory_result: dict[str, Any]) -> str:
    """Append memory section to an expert/research answer if available."""
    section = memory_result.get("memory_section", "")
    if not section:
        return answer
    return f"{answer}\n\n---\n\n{section}"
