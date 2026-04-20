"""Post-save fan-out helpers: group routing + expert auto-publish.

Extracted from ``MemoryStore.save`` (TAP-602).  Hive-side exceptions are
logged as warnings and never re-raised — the save has already succeeded
locally and propagation is best-effort.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tapps_brain._protocols import HiveBackend
    from tapps_brain.models import MemoryEntry

logger = structlog.get_logger(__name__)


def _tier_str(entry: MemoryEntry) -> str:
    return entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)


def _source_str(entry: MemoryEntry) -> str:
    return entry.source.value if hasattr(entry.source, "value") else str(entry.source)


def propagate_group_save(
    *,
    entry: MemoryEntry,
    agent_scope: str,
    groups: list[str],
    hive_store: HiveBackend | None,
) -> None:
    """Route a saved entry to the appropriate group namespace(s).

    STORY-056.3.  ``agent_scope == "group"`` fans out to every declared group
    membership; ``agent_scope == "group:<name>"`` targets a single namespace
    (membership is validated earlier in the save path).  A ``None`` hive or
    empty groups list is a no-op.
    """
    if hive_store is None or not groups:
        return
    if not (agent_scope == "group" or agent_scope.startswith("group:")):
        return

    tier = _tier_str(entry)
    src = _source_str(entry)

    targets: list[str]
    if agent_scope == "group":
        targets = list(groups)
    else:
        targets = [agent_scope[6:]]

    for group_name in targets:
        try:
            hive_store.save(
                namespace=f"group:{group_name}",
                key=entry.key,
                value=entry.value,
                tier=tier,
                confidence=entry.confidence,
                source=src,
                source_agent=entry.source_agent,
                tags=entry.tags,
            )
        except Exception:  # noqa: BLE001 — best-effort, always logged
            logger.warning(
                "group_save_propagation_failed",
                group=group_name,
                key=entry.key,
                exc_info=True,
            )


def publish_to_experts(
    *,
    entry: MemoryEntry,
    tier: str,
    agent_scope: str,
    expert_domains: list[str],
    hive_store: HiveBackend | None,
    auto_publish: bool,
) -> None:
    """Auto-publish ``architectural`` / ``pattern`` private saves to experts.

    STORY-056.2.  Only fires for ``agent_scope == "private"`` entries in the
    two shareable tiers and when the agent has declared expert domains.  Tags
    are augmented with ``expert:<domain>`` markers so recall can distinguish
    expert-published memories from domain-scope ones.
    """
    if not auto_publish:
        return
    if hive_store is None or not expert_domains:
        return
    if tier not in ("architectural", "pattern"):
        return
    if agent_scope != "private":
        return

    expert_tags = [f"expert:{d}" for d in expert_domains]
    all_tags = list(entry.tags or []) + expert_tags
    try:
        hive_store.save(
            namespace="universal",
            key=entry.key,
            value=entry.value,
            tier=_tier_str(entry),
            confidence=entry.confidence,
            source=_source_str(entry),
            source_agent=entry.source_agent,
            tags=all_tags,
        )
    except Exception:  # noqa: BLE001 — best-effort, always logged
        logger.warning(
            "expert_auto_publish_failed",
            key=entry.key,
            exc_info=True,
        )
