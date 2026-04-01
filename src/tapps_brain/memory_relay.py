"""Structured memory relay format for cross-node / sub-agent handoff (GitHub #19).

Sub-agents without a local tapps-brain can build a relay JSON payload (via MCP
``tapps_brain_relay_export``) for the primary node to import with
``tapps-brain relay import``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.agent_scope import normalize_agent_scope
from tapps_brain.memory_group import normalize_memory_group
from tapps_brain.models import MemoryScope
from tapps_brain.tier_normalize import normalize_save_tier

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

RELAY_VERSION: str = "1.0"
SUPPORTED_RELAY_VERSIONS: frozenset[str] = frozenset({"1.0"})

_MEMORY_SCOPES: frozenset[str] = frozenset(m.value for m in MemoryScope)

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


@dataclass
class RelayImportResult:
    """Outcome of applying a relay payload to a store."""

    imported: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "warnings": self.warnings,
        }


def normalize_relay_tier(raw: str | None) -> str:
    """Map common aliases; return a string suitable for ``MemoryStore.save`` tier=."""
    return normalize_save_tier(raw, None)


def resolve_relay_scopes(item: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(memory_scope, agent_scope)`` or None if configuration is invalid."""
    vis = item.get("visibility") or item.get("memory_scope")
    ag = item.get("agent_scope")
    legacy = item.get("scope")

    ag_norm: str | None = None

    if vis is not None:
        vs = str(vis).strip().lower()
        if vs not in _MEMORY_SCOPES:
            return None
        vis = vs
    if ag is not None:
        try:
            ag_norm = normalize_agent_scope(str(ag).strip())
        except ValueError:
            return None

    if legacy is not None:
        raw_legacy = str(legacy).strip()
        ls = raw_legacy.lower()
        if ls in _MEMORY_SCOPES:
            vis = vis or ls
            ag_norm = ag_norm or "private"
        else:
            try:
                legacy_ag = normalize_agent_scope(raw_legacy)
            except ValueError:
                return None
            ag_norm = ag_norm or legacy_ag
            vis = vis or MemoryScope.project.value

    return (vis or MemoryScope.project.value), (ag_norm or "private")


def parse_relay_document(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and lightly validate relay JSON.

    Returns ``(payload, None)`` on success, or ``(None, error_message)``.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid_json: {exc}"

    if not isinstance(data, dict):
        return None, "relay root must be a JSON object"

    ver = data.get("relay_version")
    if not isinstance(ver, str) or ver not in SUPPORTED_RELAY_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_RELAY_VERSIONS))
        return None, f"unsupported or missing relay_version (supported: {supported})"

    src = data.get("source_agent", "")
    if not isinstance(src, str) or not src.strip():
        return None, "source_agent must be a non-empty string"

    items = data.get("items")
    if not isinstance(items, list):
        return None, "items must be a JSON array"

    return data, None


def build_relay_json(
    *,
    source_agent: str,
    items: list[dict[str, Any]],
    relay_version: str = RELAY_VERSION,
) -> str:
    """Build a canonical relay JSON string for sub-agents to hand off."""
    payload = {
        "relay_version": relay_version,
        "source_agent": source_agent.strip(),
        "items": items,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _relay_memory_group_save_kw(
    raw: dict[str, Any], prefix: str
) -> tuple[dict[str, Any], str | None]:
    """Build ``memory_group`` kwargs for ``MemoryStore.save``; ``({}, None)`` if unset."""
    mem_g_raw = raw.get("memory_group")
    grp_raw = raw.get("group")
    if mem_g_raw is None and grp_raw is None:
        return {}, None
    chosen = mem_g_raw if mem_g_raw is not None else grp_raw
    if not isinstance(chosen, str):
        return {}, f"{prefix}: memory_group/group must be a string when provided, skipped"
    try:
        normalized = normalize_memory_group(chosen)
    except ValueError as exc:
        return {}, f"{prefix}: invalid memory_group ({exc}), skipped"
    return {"memory_group": normalized}, None


def _coerce_relay_item_save_kwargs(  # noqa: PLR0911
    raw: dict[str, Any],
    *,
    prefix: str,
    default_agent: str,
    profile: object | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(save_kwargs, None)`` or ``(None, skip_reason)``."""
    key = raw.get("key")
    value = raw.get("value")
    if not isinstance(key, str) or not key.strip():
        return None, f"{prefix}: missing or invalid key, skipped"
    if not isinstance(value, str):
        return None, f"{prefix}: value must be a string, skipped"

    key = key.strip()
    if not _KEY_RE.match(key):
        return None, f"{prefix}: key {key!r} does not match slug pattern, skipped"

    scopes = resolve_relay_scopes(raw)
    if scopes is None:
        return None, f"{prefix}: invalid scope / visibility / agent_scope combination, skipped"
    mem_scope, agent_scope = scopes

    _tier_in = raw.get("tier")
    tier_raw = normalize_save_tier(_tier_in if isinstance(_tier_in, str) else None, profile)

    tags = raw.get("tags")
    if tags is not None and not isinstance(tags, list):
        return None, f"{prefix}: tags must be a list when provided, skipped"
    tag_list: list[str] | None = [str(t) for t in tags] if isinstance(tags, list) else None

    src_raw = raw.get("source", "agent")
    src = "agent" if not isinstance(src_raw, str) or not src_raw.strip() else src_raw.strip()

    sa_raw = raw.get("source_agent", default_agent)
    sa = default_agent if not isinstance(sa_raw, str) or not sa_raw.strip() else sa_raw.strip()

    conf = raw.get("confidence", -1.0)
    if isinstance(conf, bool):
        confidence = -1.0
    elif isinstance(conf, (int, float)):
        confidence = float(conf)
    else:
        confidence = -1.0

    branch = raw.get("branch")
    branch_s: str | None = None
    if isinstance(branch, str) and branch.strip():
        branch_s = branch.strip()

    mg_kw, mg_skip = _relay_memory_group_save_kw(raw, prefix)
    if mg_skip is not None:
        return None, mg_skip

    save_kw: dict[str, Any] = {
        "key": key,
        "value": value,
        "tier": tier_raw,
        "source": src,
        "source_agent": sa,
        "scope": mem_scope,
        "agent_scope": agent_scope,
        "tags": tag_list,
        "confidence": confidence,
        "branch": branch_s,
        "batch_context": "memory_relay",
        **mg_kw,
    }
    return save_kw, None


def import_relay_to_store(store: MemoryStore, payload: dict[str, Any]) -> RelayImportResult:
    """Persist relay items; invalid rows are skipped with warnings (no hard failure)."""
    result = RelayImportResult()
    items = payload.get("items")
    if not isinstance(items, list):
        result.warnings.append("items is not a list; nothing imported")
        return result

    default_agent = payload.get("source_agent", "unknown")
    if not isinstance(default_agent, str):
        default_agent = "unknown"

    for idx, raw in enumerate(items):
        prefix = f"items[{idx}]"
        if not isinstance(raw, dict):
            msg = f"{prefix}: not an object, skipped"
            logger.warning("relay_skip", reason=msg)
            result.warnings.append(msg)
            result.skipped += 1
            continue

        save_kw, skip = _coerce_relay_item_save_kwargs(
            raw,
            prefix=prefix,
            default_agent=default_agent,
            profile=store.profile,
        )
        if skip is not None:
            logger.warning("relay_skip", reason=skip)
            result.warnings.append(skip)
            result.skipped += 1
            continue

        assert save_kw is not None

        try:
            out = store.save(**save_kw)
        except (TypeError, ValueError) as exc:
            msg = f"{prefix}: save error ({exc}), skipped"
            logger.warning("relay_skip", reason=msg)
            result.warnings.append(msg)
            result.skipped += 1
            continue

        if isinstance(out, dict):
            err = out.get("error", "unknown")
            msg = f"{prefix}: save blocked ({err}), skipped"
            logger.warning("relay_skip", reason=msg)
            result.warnings.append(msg)
            result.skipped += 1
            continue

        result.imported += 1

    return result
