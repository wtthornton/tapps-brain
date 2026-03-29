"""Project-local memory partition labels (GitHub #49).

``memory_group`` is stored in SQLite as column ``memory_group`` (not Hive namespace,
not profile tier). Use :data:`MEMORY_GROUP_UNSET` with :meth:`MemoryStore.save` to
preserve an existing group on update when the caller omits the parameter.
"""

from __future__ import annotations

from typing import Any

# Max length aligned with tag keys — human-chosen partition names (e.g. team-a, feature-x).
MAX_MEMORY_GROUP_LENGTH: int = 64
# Reject ASCII control characters (space 0x20 and above allowed).
_MIN_PRINTABLE_ASCII: int = 32


class _MemoryGroupUnsetType:
    """Sentinel: caller did not specify memory_group (preserve on update)."""

    __slots__ = ()


MEMORY_GROUP_UNSET: Any = _MemoryGroupUnsetType()


def normalize_memory_group(raw: str | None) -> str | None:
    """Trim, enforce length and printable ASCII-ish content; empty → None (ungrouped).

    Raises:
        ValueError: If the string is too long or contains ASCII control characters.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if len(s) > MAX_MEMORY_GROUP_LENGTH:
        msg = f"memory_group exceeds max length ({len(s)} > {MAX_MEMORY_GROUP_LENGTH})."
        raise ValueError(msg)
    if any(ord(c) < _MIN_PRINTABLE_ASCII for c in s):
        msg = "memory_group must not contain control characters."
        raise ValueError(msg)
    return s
