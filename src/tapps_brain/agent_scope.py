"""Hive ``agent_scope`` normalization (GitHub #52 / EPIC-041 STORY-041.2).

Supports ``private``, ``domain``, ``hive``, and ``group:<name>`` where *name* is a
Hive membership group (namespace = group name). Distinct from project-local
``memory_group`` / CLI ``--group``.
"""

from __future__ import annotations

import re

from tapps_brain.memory_group import normalize_memory_group

# Canonical prefix for cross-agent Hive groups (namespace = *name*).
GROUP_AGENT_SCOPE_PREFIX: str = "group:"

_VALID_PRIMITIVE_SCOPES: frozenset[str] = frozenset({"private", "domain", "hive", "group"})

# Documented valid_values for API error responses (stable order for tests).
_AGENT_SCOPE_DOC_VALUES: tuple[str, ...] = (
    "private",
    "domain",
    "hive",
    "group",
    "group:<name>",
)


def normalize_agent_scope(raw: str) -> str:
    """Return canonical ``agent_scope`` or raise ``ValueError``."""
    s = raw.strip()
    if not s:
        msg = "agent_scope must not be empty"
        raise ValueError(msg)
    sl = s.lower()
    if sl in _VALID_PRIMITIVE_SCOPES:
        return sl
    m = re.match(r"(?i)^group:\s*(.*)$", s)
    if not m:
        msg = (
            f"Invalid agent_scope {raw!r}. Use private, domain, hive, or group:<name> "
            "(see docs/guides/hive.md)."
        )
        raise ValueError(msg)
    inner = m.group(1).strip()
    name = normalize_memory_group(inner)
    if name is None:
        msg = "agent_scope group:<name> requires a non-empty group name"
        raise ValueError(msg)
    return f"{GROUP_AGENT_SCOPE_PREFIX}{name}"


def hive_group_name_from_scope(normalized_scope: str) -> str | None:
    """If *normalized_scope* is ``group:<name>``, return *name*; else ``None``."""
    if not normalized_scope.startswith(GROUP_AGENT_SCOPE_PREFIX):
        return None
    rest = normalized_scope[len(GROUP_AGENT_SCOPE_PREFIX) :]
    return rest if rest else None


def agent_scope_valid_values_for_errors() -> list[str]:
    """Values to include in ``invalid_agent_scope`` error payloads."""
    return list(_AGENT_SCOPE_DOC_VALUES)
