"""Shared import-size limits for memory I/O (TAP-5034)."""

from __future__ import annotations

import os

NATIVE_FORMAT = "tapps-memory"
NATIVE_FORMAT_VERSION = "1.0"

DEFAULT_MAX_IMPORT_ENTRIES = 500
CLI_DEFAULT_MAX_IMPORT_ENTRIES = 50_000
_ENV_MAX_IMPORT = "TAPPS_BRAIN_MAX_IMPORT_ENTRIES"


def resolve_max_import_entries(max_entries: int | None = None) -> int:
    """Resolve the import size limit (param > env > default 500)."""
    if max_entries is not None:
        if max_entries < 1:
            msg = f"max_entries must be >= 1, got {max_entries}"
            raise ValueError(msg)
        return max_entries
    raw = os.environ.get(_ENV_MAX_IMPORT, "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError as exc:
            msg = f"{_ENV_MAX_IMPORT} must be an integer, got {raw!r}"
            raise ValueError(msg) from exc
        if parsed < 1:
            msg = f"{_ENV_MAX_IMPORT} must be >= 1, got {parsed}"
            raise ValueError(msg)
        return parsed
    return DEFAULT_MAX_IMPORT_ENTRIES


def enforce_import_limit(count: int, limit: int) -> None:
    """Raise ValueError when *count* exceeds *limit* (no silent truncate)."""
    if count > limit:
        msg = (
            f"Import exceeds max entries ({count} > {limit}). "
            f"Raise the limit via max_entries= or {_ENV_MAX_IMPORT}."
        )
        raise ValueError(msg)
