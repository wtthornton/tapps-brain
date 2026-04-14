"""Memory-domain service functions (EPIC-070 STORY-070.1).

All functions return JSON-serialisable Python objects (dict / list / str / int /
bool). Wrappers in ``mcp_server`` / ``http_adapter`` are responsible for
``json.dumps`` and request-context resolution.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import structlog

from tapps_brain.services._common import _MAX_CONFIDENCE_BOOST

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# brain_* simplified Agent Brain tools (EPIC-057)
# ---------------------------------------------------------------------------


def brain_remember(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    fact: str,
    tier: str = "procedural",
    share: bool = False,
    share_with: str = "",
) -> dict[str, Any]:
    from tapps_brain.agent_brain import _content_key
    from tapps_brain.otel_tracer import start_mcp_tool_span

    with start_mcp_tool_span("brain_remember", extra_attributes={"memory.tier": tier}):
        key = _content_key(fact)
        agent_scope = "private"
        if share:
            agent_scope = "group"
        elif share_with == "hive":
            agent_scope = "hive"
        elif share_with:
            agent_scope = f"group:{share_with}"
        result = store.save(key=key, value=fact, tier=tier, agent_scope=agent_scope)
        if isinstance(result, dict) and "error" in result:
            return result
        return {"saved": True, "key": key}


def brain_recall(
    store: Any, project_id: str, agent_id: str, *, query: str, max_results: int = 5
) -> list[Any]:
    from tapps_brain.otel_tracer import start_mcp_tool_span

    with start_mcp_tool_span("brain_recall"):
        entries = store.search(query)
        results: list[Any] = []
        for entry in entries[:max_results]:
            if isinstance(entry, dict):
                results.append(entry)
            else:
                results.append(
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "tier": str(entry.tier),
                        "confidence": entry.confidence,
                        "tags": list(entry.tags) if entry.tags else [],
                    }
                )
        return results


def brain_forget(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    from tapps_brain.otel_tracer import start_mcp_tool_span

    with start_mcp_tool_span("brain_forget"):
        entry = store.get(key)
        if entry is None:
            return {"forgotten": False, "reason": "not_found"}
        store.delete(key)
        return {"forgotten": True, "key": key}


def brain_learn_success(
    store: Any, project_id: str, agent_id: str, *, task_description: str, task_id: str = ""
) -> dict[str, Any]:
    from tapps_brain.agent_brain import _content_key
    from tapps_brain.otel_tracer import start_mcp_tool_span

    with start_mcp_tool_span("brain_learn_success"):
        key = _content_key(f"success-{task_description}")
        tags = ["success"]
        if task_id:
            tags.append(f"task:{task_id}")
        store.save(key=key, value=task_description, tier="procedural", tags=tags)
        return {"learned": True, "key": key}


def brain_learn_failure(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    description: str,
    task_id: str = "",
    error: str = "",
) -> dict[str, Any]:
    from tapps_brain.agent_brain import _content_key
    from tapps_brain.otel_tracer import start_mcp_tool_span

    with start_mcp_tool_span("brain_learn_failure"):
        key = _content_key(f"failure-{description}")
        value = f"{description}\n\nError: {error}" if error else description
        tags = ["failure"]
        if task_id:
            tags.append(f"task:{task_id}")
        store.save(key=key, value=value, tier="procedural", tags=tags)
        return {"learned": True, "key": key}


def brain_status(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    return {
        "agent_id": getattr(store, "agent_id", None),
        "groups": getattr(store, "groups", []),
        "expert_domains": getattr(store, "expert_domains", []),
        "memory_count": len(store.list_all()),
        "hive_connected": store._hive_store is not None,
    }


# ---------------------------------------------------------------------------
# memory_* core CRUD
# ---------------------------------------------------------------------------


def memory_save(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    value: str,
    tier: str = "pattern",
    source: str = "agent",
    tags: list[str] | None = None,
    scope: str = "project",
    confidence: float = -1.0,
    agent_scope: str = "private",
    source_agent: str = "",
    group: str | None = None,
) -> dict[str, Any]:
    from tapps_brain.agent_scope import (
        agent_scope_valid_values_for_errors,
        normalize_agent_scope,
    )
    from tapps_brain.memory_group import MEMORY_GROUP_UNSET
    from tapps_brain.models import MemoryTier
    from tapps_brain.tier_normalize import normalize_save_tier

    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {
            "error": "invalid_agent_scope",
            "message": str(exc),
            "valid_values": agent_scope_valid_values_for_errors(),
        }

    tier = normalize_save_tier(tier, store.profile)

    _valid_tiers: frozenset[str] = (
        frozenset(store.profile.layer_names)
        if store.profile is not None
        else frozenset(m.value for m in MemoryTier)
    )
    if tier not in _valid_tiers:
        _sorted_valid = sorted(_valid_tiers)
        return {
            "error": "invalid_tier",
            "message": f"Invalid tier {tier!r}. Valid values: {_sorted_valid}",
            "valid_values": _sorted_valid,
        }
    _valid_sources = ("human", "agent", "inferred", "system")
    if source not in _valid_sources:
        return {
            "error": "invalid_source",
            "message": f"Invalid source {source!r}. Valid values: {list(_valid_sources)}",
            "valid_values": list(_valid_sources),
        }
    resolved_agent = source_agent if source_agent else agent_id
    memory_group_arg: object = MEMORY_GROUP_UNSET if group is None else group
    result = store.save(
        key=key,
        value=value,
        tier=tier,
        source=source,
        tags=tags,
        scope=scope,
        confidence=confidence,
        agent_scope=agent_scope,
        source_agent=resolved_agent,
        memory_group=memory_group_arg,
    )
    if isinstance(result, dict):
        return result
    return {
        "status": "saved",
        "key": result.key,
        "tier": str(result.tier),
        "confidence": result.confidence,
        "memory_group": result.memory_group,
    }


def memory_get(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    entry = store.get(key)
    if entry is None:
        return {"error": "not_found", "key": key}
    return entry.model_dump(mode="json")


def memory_delete(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    deleted = store.delete(key)
    return {"deleted": deleted, "key": key}


def memory_search(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    query: str,
    tier: str | None = None,
    scope: str | None = None,
    as_of: str | None = None,
    group: str | None = None,
    since: str = "",
    until: str = "",
    time_field: str = "created_at",
) -> list[dict[str, Any]] | dict[str, Any]:
    if as_of is not None:
        try:
            from datetime import datetime

            datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError:
            return {
                "error": "invalid_as_of",
                "message": f"as_of must be a valid ISO-8601 timestamp, got {as_of!r}",
            }
    results = store.search(
        query,
        tier=tier,
        scope=scope,
        as_of=as_of,
        memory_group=group,
        since=since.strip() or None,
        until=until.strip() or None,
        time_field=time_field,
    )
    return [
        {
            "key": e.key,
            "value": e.value,
            "tier": str(e.tier),
            "confidence": e.confidence,
            "tags": e.tags,
            "memory_group": e.memory_group,
        }
        for e in results
    ]


def memory_list(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    tier: str | None = None,
    scope: str | None = None,
    include_superseded: bool = False,
    group: str | None = None,
) -> list[dict[str, Any]]:
    entries = store.list_all(
        tier=tier,
        scope=scope,
        include_superseded=include_superseded,
        memory_group=group,
    )
    return [
        {
            "key": e.key,
            "value": e.value[:200],
            "tier": str(e.tier),
            "confidence": e.confidence,
            "tags": e.tags,
            "scope": e.scope.value,
            "memory_group": e.memory_group,
        }
        for e in entries
    ]


def memory_list_groups(store: Any, project_id: str, agent_id: str) -> list[str]:
    return store.list_memory_groups()


# ---------------------------------------------------------------------------
# Lifecycle: recall, reinforce, ingest, supersede, history
# ---------------------------------------------------------------------------


def memory_recall(
    store: Any, project_id: str, agent_id: str, *, message: str, group: str | None = None
) -> dict[str, Any]:
    result = store.recall(message, memory_group=group)
    payload: dict[str, Any] = {
        "memory_section": result.memory_section,
        "memory_count": result.memory_count,
        "token_count": result.token_count,
        "recall_time_ms": result.recall_time_ms,
        "truncated": result.truncated,
        "memories": result.memories,
    }
    if result.recall_diagnostics is not None:
        payload["recall_diagnostics"] = result.recall_diagnostics.model_dump(mode="json")
    if result.quality_warning:
        payload["quality_warning"] = result.quality_warning
    return payload


def memory_reinforce(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    confidence_boost: float = 0.0,
) -> dict[str, Any]:
    if not (0.0 <= confidence_boost <= _MAX_CONFIDENCE_BOOST):
        return {
            "error": "invalid_confidence_boost",
            "message": (
                f"confidence_boost must be in [0.0, {_MAX_CONFIDENCE_BOOST}],"
                f" got {confidence_boost}"
            ),
        }
    try:
        entry = store.reinforce(key, confidence_boost=confidence_boost)
    except KeyError:
        return {"error": "not_found", "key": key}
    return {
        "status": "reinforced",
        "key": entry.key,
        "confidence": entry.confidence,
        "access_count": entry.access_count,
    }


def memory_ingest(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    context: str,
    source: str = "agent",
    agent_scope: str = "private",
) -> dict[str, Any]:
    from tapps_brain.agent_scope import normalize_agent_scope

    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {"error": "invalid_agent_scope", "message": str(exc)}

    created_keys = store.ingest_context(context, source=source, agent_scope=agent_scope)
    return {
        "status": "ingested",
        "created_keys": created_keys,
        "count": len(created_keys),
    }


def memory_supersede(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    old_key: str,
    new_value: str,
    key: str | None = None,
    tier: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if key is not None:
        kwargs["key"] = key
    if tier is not None:
        kwargs["tier"] = tier
    if tags is not None:
        kwargs["tags"] = tags
    try:
        entry = store.supersede(old_key, new_value, **kwargs)
    except KeyError:
        return {"error": "not_found", "key": old_key}
    except ValueError as exc:
        return {"error": "already_superseded", "message": str(exc)}
    return {
        "status": "superseded",
        "old_key": old_key,
        "new_key": entry.key,
        "tier": str(entry.tier),
        "confidence": entry.confidence,
    }


def memory_history(
    store: Any, project_id: str, agent_id: str, *, key: str
) -> list[dict[str, Any]] | dict[str, Any]:
    try:
        chain = store.history(key)
    except KeyError:
        return {"error": "not_found", "key": key}
    if not chain:
        return {"error": "not_found", "key": key}
    return [
        {
            "key": e.key,
            "value": e.value[:200],
            "tier": str(e.tier),
            "confidence": e.confidence,
            "valid_at": e.valid_at,
            "invalid_at": e.invalid_at,
            "superseded_by": e.superseded_by,
        }
        for e in chain
    ]


# ---------------------------------------------------------------------------
# Session indexing / capture
# ---------------------------------------------------------------------------


def memory_index_session(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    session_id: str,
    chunks: list[str],
) -> dict[str, Any]:
    stored = store.index_session(session_id, chunks)
    return {
        "status": "indexed",
        "session_id": session_id,
        "chunks_stored": stored,
    }


def memory_search_sessions(
    store: Any, project_id: str, agent_id: str, *, query: str, limit: int = 10
) -> dict[str, Any]:
    results = store.search_sessions(query, limit=limit)
    return {
        "results": results,
        "count": len(results),
    }


def memory_capture(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    response: str,
    source: str = "agent",
    agent_scope: str = "private",
) -> dict[str, Any]:
    from tapps_brain.agent_scope import normalize_agent_scope
    from tapps_brain.recall import RecallOrchestrator

    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        return {"error": "invalid_agent_scope", "message": str(exc)}

    orchestrator = RecallOrchestrator(store)
    created_keys = orchestrator.capture(response, source=source, agent_scope=agent_scope)
    return {
        "status": "captured",
        "created_keys": created_keys,
        "count": len(created_keys),
    }


# ---------------------------------------------------------------------------
# Export / Import
# ---------------------------------------------------------------------------


def memory_export(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    project_root: str,
    tier: str | None = None,
    scope: str | None = None,
    min_confidence: float | None = None,
) -> dict[str, Any]:
    entries = store.list_all(tier=tier, scope=scope)
    if min_confidence is not None:
        entries = [e for e in entries if e.confidence >= min_confidence]
    return {
        "memories": [e.model_dump(mode="json") for e in entries],
        "entry_count": len(entries),
        "project_root": project_root,
    }


def memory_import(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    memories_json: str,
    overwrite: bool = False,
) -> dict[str, Any]:
    try:
        data = json.loads(memories_json)
    except json.JSONDecodeError as exc:
        return {"error": "invalid_json", "message": str(exc)}

    if not isinstance(data, dict) or "memories" not in data:
        return {"error": "invalid_format", "message": "Expected {'memories': [...]}"}

    memories = data["memories"]
    if not isinstance(memories, list):
        return {"error": "invalid_format", "message": "'memories' must be a list"}

    imported = 0
    skipped = 0
    errors = 0

    for mem in memories:
        if not isinstance(mem, dict) or "key" not in mem or "value" not in mem:
            errors += 1
            continue

        key = mem["key"]
        existing = store.get(key)
        if existing is not None and not overwrite:
            skipped += 1
            continue

        try:
            result = store.save(
                key=key,
                value=mem["value"],
                tier=mem.get("tier", "pattern"),
                source=mem.get("source", "system"),
                tags=mem.get("tags"),
                scope=mem.get("scope", "project"),
            )
        except ValueError as exc:
            logger.warning("memory_import_save_error", key=key, error=str(exc))
            errors += 1
            continue
        if isinstance(result, dict):
            errors += 1
        else:
            imported += 1

    return {
        "status": "imported",
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# GC / consolidation config
# ---------------------------------------------------------------------------


def memory_gc_config(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    return store.get_gc_config().to_dict()


def memory_gc_config_set(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    floor_retention_days: int | None = None,
    session_expiry_days: int | None = None,
    contradicted_threshold: float | None = None,
) -> dict[str, Any]:
    from tapps_brain.gc import GCConfig

    current = store.get_gc_config()
    new_cfg = GCConfig(
        floor_retention_days=(
            floor_retention_days
            if floor_retention_days is not None
            else current.floor_retention_days
        ),
        session_expiry_days=(
            session_expiry_days
            if session_expiry_days is not None
            else current.session_expiry_days
        ),
        contradicted_threshold=(
            contradicted_threshold
            if contradicted_threshold is not None
            else current.contradicted_threshold
        ),
    )
    store.set_gc_config(new_cfg)
    return {"status": "updated", **new_cfg.to_dict()}


def memory_consolidation_config(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    return store.get_consolidation_config().to_dict()


def memory_consolidation_config_set(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    enabled: bool | None = None,
    threshold: float | None = None,
    min_entries: int | None = None,
) -> dict[str, Any]:
    from tapps_brain.store import ConsolidationConfig

    current = store.get_consolidation_config()
    new_cfg = ConsolidationConfig(
        enabled=enabled if enabled is not None else current.enabled,
        threshold=threshold if threshold is not None else current.threshold,
        min_entries=min_entries if min_entries is not None else current.min_entries,
    )
    store.set_consolidation_config(new_cfg)
    return {"status": "updated", **new_cfg.to_dict()}


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


def memory_relations(store: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
    relations = store.get_relations(key)
    return {"key": key, "relations": relations, "count": len(relations)}


def memory_relations_get_batch(
    store: Any, project_id: str, agent_id: str, *, keys_json: str
) -> dict[str, Any]:
    try:
        keys = json.loads(keys_json)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"error": "invalid_keys_json", "message": str(exc)}
    if not isinstance(keys, list):
        return {"error": "invalid_keys_json", "message": "Expected a JSON array of strings."}
    results = store.get_relations_batch([str(k) for k in keys])
    total = sum(len(v) for v in results.values())
    return {"results": results, "total_count": total}


def memory_find_related(
    store: Any, project_id: str, agent_id: str, *, key: str, max_hops: int = 2
) -> dict[str, Any]:
    if max_hops < 1:
        return {"error": "invalid_max_hops", "message": "max_hops must be >= 1"}
    try:
        results = store.find_related(key, max_hops=max_hops)
        return {
            "key": key,
            "max_hops": max_hops,
            "related": [{"key": k, "hops": h} for k, h in results],
            "count": len(results),
        }
    except KeyError:
        return {"error": "not_found", "message": f"Entry '{key}' not found."}


def memory_query_relations(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    subject: str = "",
    predicate: str = "",
    object_entity: str = "",
) -> dict[str, Any]:
    matches = store.query_relations(
        subject=subject or None,
        predicate=predicate or None,
        object_entity=object_entity or None,
    )
    return {"relations": matches, "count": len(matches)}


# ---------------------------------------------------------------------------
# Audit / tags
# ---------------------------------------------------------------------------


def memory_audit(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str = "",
    event_type: str = "",
    since: str = "",
    until: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    if limit < 1:
        return {"error": "invalid_limit", "message": "limit must be >= 1"}
    entries = store.audit(
        key=key or None,
        event_type=event_type or None,
        since=since or None,
        until=until or None,
        limit=limit,
    )
    return {
        "events": [e.model_dump(mode="json") for e in entries],
        "count": len(entries),
    }


def memory_list_tags(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    counts = store.list_tags()
    tags_list = sorted(
        [{"tag": t, "count": c} for t, c in counts.items()],
        key=lambda x: (-x["count"], x["tag"]),
    )
    return {"tags": tags_list, "total": len(tags_list)}


def memory_update_tags(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    key: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    result = store.update_tags(key, add=add, remove=remove)
    if isinstance(result, dict):
        return result
    return {
        "status": "updated",
        "key": result.key,
        "tags": result.tags,
    }


def memory_entries_by_tag(
    store: Any, project_id: str, agent_id: str, *, tag: str, tier: str = ""
) -> dict[str, Any]:
    entries = store.entries_by_tag(tag, tier=tier or None)
    return {
        "tag": tag,
        "entries": [
            {
                "key": e.key,
                "value": e.value,
                "tier": str(e.tier),
                "confidence": e.confidence,
                "tags": e.tags,
            }
            for e in entries
        ],
        "count": len(entries),
    }
