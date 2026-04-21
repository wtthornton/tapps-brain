"""Upfront validation + safety helpers for ``MemoryStore.save``.

Extracted from ``store.py`` (TAP-602).  The functions in this module return
either a normalised value or a structured error dict with the same
``{"error": ..., "message": ...}`` shape the public ``save()`` has always
returned; callers short-circuit by returning the dict up to the API boundary
without mutating state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from tapps_brain.agent_scope import (
    agent_scope_valid_values_for_errors,
    normalize_agent_scope,
)
from tapps_brain.memory_group import MEMORY_GROUP_UNSET, normalize_memory_group

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ScopeAndGroup:
    """Result of :func:`validate_scope_and_group` on the happy path."""

    agent_scope: str
    mg_explicit: str | None | object  # MEMORY_GROUP_UNSET sentinel preserved


def validate_scope_and_group(
    *,
    agent_scope: str,
    memory_group: str | None | object,
    groups: list[str],
) -> ScopeAndGroup | dict[str, Any]:
    """Normalise ``agent_scope`` + ``memory_group`` or return an error dict.

    This is the single source of truth for save-time scope validation; the
    rules here mirror what ``save()`` has enforced since STORY-056.3 and
    GitHub #49 — only the packaging has changed.
    """
    try:
        normalized_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {
            "error": "invalid_agent_scope",
            "message": str(exc),
            "valid_values": agent_scope_valid_values_for_errors(),
        }

    # STORY-056.3: Validate group membership for group-scoped saves
    if normalized_scope.startswith("group:") and normalized_scope != "group":
        group_name = normalized_scope[6:]
        if group_name not in groups:
            return {
                "error": "invalid_agent_scope",
                "message": f"Agent not a member of group '{group_name}'",
            }

    mg_explicit: str | None | object = MEMORY_GROUP_UNSET
    if memory_group is not MEMORY_GROUP_UNSET:
        if memory_group is None:
            mg_explicit = None
        else:
            try:
                mg_explicit = normalize_memory_group(str(memory_group))
            except ValueError as exc:
                return {"error": "invalid_memory_group", "message": str(exc)}

    return ScopeAndGroup(agent_scope=normalized_scope, mg_explicit=mg_explicit)


@dataclass(frozen=True)
class SafetyResult:
    """Outcome of the content-safety + sanitisation step."""

    value: str  # the (possibly sanitised) value to persist
    ruleset_version: str | None


def apply_safety_check(
    *,
    key: str,
    value: str,
    profile: Any,  # noqa: ANN401 — MemoryProfile | None
    metrics: Any,  # noqa: ANN401 — MetricsCollector
) -> SafetyResult | dict[str, Any]:
    """Run the RAG safety check and return the (possibly sanitised) value.

    Returns a ``{"error": "content_blocked", ...}`` dict when the safety
    layer flags the value as unsafe.  Metrics counters (``store.save.errors``
    and ``store.save.errors.content_blocked``) are incremented in the blocked
    case so the monitoring surface matches pre-refactor behaviour exactly.
    """
    from tapps_brain.safety import check_content_safety

    ruleset_version: str | None = None
    if profile is not None:
        safety_cfg = getattr(profile, "safety", None)
        if safety_cfg is not None:
            ruleset_version = getattr(safety_cfg, "ruleset_version", None)

    safety = check_content_safety(
        value,
        ruleset_version=ruleset_version,
        metrics=metrics,
    )
    if not safety.safe:
        logger.warning(
            "memory_save_blocked",
            key=key,
            match_count=safety.match_count,
            patterns=safety.flagged_patterns,
            ruleset_version=safety.ruleset_version,
        )
        metrics.increment("store.save.errors")
        metrics.increment("store.save.errors.content_blocked")
        return {
            "error": "content_blocked",
            "message": "Memory value blocked by RAG safety filter.",
            "flagged_patterns": safety.flagged_patterns,
        }

    new_value = safety.sanitised_content if safety.sanitised_content is not None else value
    return SafetyResult(value=new_value, ruleset_version=safety.ruleset_version)
