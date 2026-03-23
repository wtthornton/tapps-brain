"""Audit trail query API for the JSONL audit log (EPIC-007).

Provides ``AuditReader`` to query the existing append-only JSONL audit log
written by ``MemoryPersistence``. Reads lazily using seek/readline —
does not load the entire file into memory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """A single entry from the audit trail."""

    timestamp: str = Field(description="ISO-8601 UTC timestamp.")
    event_type: str = Field(description="Event type (save, delete, etc.).")
    key: str = Field(description="Memory entry key affected.")
    details: dict[str, object] = Field(default_factory=dict, description="Additional details.")


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


class AuditReader:
    """Query interface for the JSONL audit log.

    Reads line-by-line from the file to avoid loading the entire log
    into memory.
    """

    def __init__(self, audit_path: Path) -> None:
        self._path = audit_path

    def query(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit log entries with optional filters.

        Args:
            key: Filter by memory entry key.
            event_type: Filter by event type (save, delete, etc.).
            since: ISO-8601 lower bound (inclusive).
            until: ISO-8601 upper bound (inclusive).
            limit: Maximum number of entries to return.

        Returns:
            Matching audit entries, most recent last.
        """
        if not self._path.exists():
            return []

        results: list[AuditEntry] = []

        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Map the existing audit log format to AuditEntry
                entry_key = record.get("key", "")
                entry_action = record.get("action", "")
                entry_ts = record.get("timestamp", "")

                # Apply filters
                if key is not None and entry_key != key:
                    continue
                if event_type is not None and entry_action != event_type:
                    continue
                if since is not None and entry_ts < since:
                    continue
                if until is not None and entry_ts > until:
                    continue

                # Build details from any extra fields
                details: dict[str, object] = {
                    k: v for k, v in record.items() if k not in ("key", "action", "timestamp")
                }

                results.append(
                    AuditEntry(
                        timestamp=entry_ts,
                        event_type=entry_action,
                        key=entry_key,
                        details=details,
                    )
                )

                if len(results) >= limit:
                    break

        return results

    def count(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
    ) -> int:
        """Count matching audit entries without returning them."""
        if not self._path.exists():
            return 0

        count = 0
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if key is not None and record.get("key", "") != key:
                    continue
                if event_type is not None and record.get("action", "") != event_type:
                    continue
                count += 1

        return count
