"""Hive service functions (EPIC-070 STORY-070.1).

Hive tools historically resolved a transient Hive backend via the
``_hive_for_tools()`` closure inside ``create_server``. The service layer
preserves that pattern: the wrapper passes a ``hive_resolver`` callable that
returns ``(hive_backend, should_close)``.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)


HiveResolver = Callable[[], tuple[Any, bool]]


def hive_status(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    hive_resolver: HiveResolver,
) -> dict[str, Any]:
    try:
        from tapps_brain.backends import AgentRegistry

        hive, should_close = hive_resolver()
        try:
            ns_counts = hive.count_by_namespace()
            agent_counts = hive.count_by_agent()

            registry = AgentRegistry()
            agents = [
                {
                    "id": a.id,
                    "profile": a.profile,
                    "skills": a.skills,
                    "entries_contributed": agent_counts.get(a.id, 0),
                }
                for a in registry.list_agents()
            ]
        finally:
            if should_close:
                hive.close()
        return {
            "namespaces": ns_counts,
            "total_entries": sum(ns_counts.values()),
            "agents": agents,
        }
    except Exception as exc:
        logger.exception("hive_tool_error", tool="hive_status")
        return {"error": "hive_error", "message": str(exc)}


def hive_search(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    hive_resolver: HiveResolver,
    query: str,
    namespace: str | None = None,
) -> dict[str, Any]:
    try:
        hive, should_close = hive_resolver()
        try:
            ns_list = [namespace] if namespace else None
            results = hive.search(query, namespaces=ns_list, limit=20)
        finally:
            if should_close:
                hive.close()
        return {"results": results, "count": len(results)}
    except Exception as exc:
        logger.exception("hive_tool_error", tool="hive_search")
        return {"error": "hive_error", "message": str(exc)}


def hive_propagate(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    hive_resolver: HiveResolver,
    key: str,
    agent_scope: str = "hive",
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    entry = store.get(key)
    if entry is None:
        return {"error": "not_found", "message": f"Key '{key}' not found."}

    try:
        from tapps_brain.agent_scope import (
            agent_scope_valid_values_for_errors,
            normalize_agent_scope,
        )
        from tapps_brain.backends import PropagationEngine

        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            return {
                "error": "invalid_agent_scope",
                "message": str(exc),
                "valid_values": agent_scope_valid_values_for_errors(),
            }

        if agent_scope == "private":
            return {"propagated": False, "reason": "scope is private"}

        hive, should_close = hive_resolver()
        hive_agent_id = getattr(store, "_hive_agent_id", "mcp-user")
        profile_name = "repo-brain"
        auto_propagate: list[str] | None = None
        private_tiers: list[str] | None = None
        if store.profile is not None:
            profile_name = getattr(store.profile, "name", "repo-brain")
            hc = getattr(store.profile, "hive", None)
            if hc is not None:
                auto_propagate = hc.auto_propagate_tiers
                private_tiers = hc.private_tiers

        tier_val = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        try:
            result = PropagationEngine.propagate(
                key=entry.key,
                value=entry.value,
                agent_scope=agent_scope,
                agent_id=hive_agent_id,
                agent_profile=profile_name,
                tier=tier_val,
                confidence=entry.confidence,
                source=entry.source.value,
                tags=entry.tags,
                hive_store=hive,
                auto_propagate_tiers=auto_propagate,
                private_tiers=private_tiers,
                bypass_profile_hive_rules=force,
                dry_run=dry_run,
            )
        finally:
            if should_close:
                hive.close()
        if result is None:
            return {"propagated": False, "reason": "scope is private"}
        return {"propagated": True, **result}
    except Exception as exc:
        logger.exception("hive_tool_error", tool="hive_propagate")
        return {"error": "hive_error", "message": str(exc)}


def hive_push(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    hive_resolver: HiveResolver,
    agent_scope: str = "hive",
    push_all: bool = False,
    tags: str = "",
    tier: str | None = None,
    keys: str = "",
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    try:
        from tapps_brain.agent_scope import (
            agent_scope_valid_values_for_errors,
            normalize_agent_scope,
        )
        from tapps_brain.backends import (
            push_memory_entries_to_hive,
            select_local_entries_for_hive_push,
        )

        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            return {
                "error": "invalid_agent_scope",
                "message": str(exc),
                "valid_values": agent_scope_valid_values_for_errors(),
            }
        if agent_scope == "private":
            return {
                "error": "invalid_agent_scope",
                "message": "hive_push requires domain, hive, or group:<name>.",
                "valid_values": agent_scope_valid_values_for_errors(),
            }

        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None
        try:
            entries = select_local_entries_for_hive_push(
                store,
                push_all=push_all,
                tags=tag_list,
                tier=tier,
                keys=key_list or None,
                include_superseded=False,
            )
        except ValueError as ve:
            return {"error": "invalid_args", "message": str(ve)}

        hive, should_close = hive_resolver()
        hive_agent_id = getattr(store, "_hive_agent_id", "mcp-user")
        profile_name = "repo-brain"
        auto_propagate: list[str] | None = None
        private_tiers: list[str] | None = None
        if store.profile is not None:
            profile_name = getattr(store.profile, "name", "repo-brain")
            hc = getattr(store.profile, "hive", None)
            if hc is not None:
                auto_propagate = hc.auto_propagate_tiers
                private_tiers = hc.private_tiers

        try:
            report = push_memory_entries_to_hive(
                entries,
                hive_store=hive,
                agent_id=hive_agent_id,
                agent_profile=profile_name,
                agent_scope=agent_scope,
                auto_propagate_tiers=auto_propagate,
                private_tiers=private_tiers,
                bypass_profile_hive_rules=force,
                dry_run=dry_run,
            )
        finally:
            if should_close:
                hive.close()
        return report
    except Exception as exc:
        logger.exception("hive_tool_error", tool="hive_push")
        return {"error": "hive_error", "message": str(exc)}


def hive_write_revision(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    hive_resolver: HiveResolver,
) -> dict[str, Any]:
    try:
        hive, should_close = hive_resolver()
        try:
            state = hive.get_write_notify_state()
        finally:
            if should_close:
                hive.close()
        return state
    except Exception as exc:
        logger.exception("hive_tool_error", tool="hive_write_revision")
        return {"error": "hive_error", "message": str(exc)}


def hive_wait_write(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    hive_resolver: HiveResolver,
    since_revision: int = 0,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    try:
        hive, should_close = hive_resolver()
        try:
            cap = min(60.0, max(0.0, float(timeout_seconds)))
            state = hive.get_write_notify_state()
            if state["revision"] > since_revision:
                return {**state, "changed": True, "timed_out": False}
            result = hive.wait_for_write_notify(
                since_revision=since_revision,
                timeout_sec=cap,
                poll_interval_sec=0.25,
            )
        finally:
            if should_close:
                hive.close()
        return result
    except Exception as exc:
        logger.exception("hive_tool_error", tool="hive_wait_write")
        return {"error": "hive_error", "message": str(exc)}
