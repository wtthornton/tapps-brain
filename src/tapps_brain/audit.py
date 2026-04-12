"""Audit trail query API (EPIC-007).

Provides :class:`AuditReader`, which queries the Postgres ``audit_log`` table
created by migration 005.  Under the v3 Postgres-only persistence plane
(ADR-007) the legacy JSONL audit file is gone; ``AuditReader`` now wraps a
:class:`~tapps_brain.postgres_private.PostgresPrivateBackend` directly.

For unit tests that pass a :class:`~pathlib.Path` directly (legacy JSONL API),
``AuditReader`` falls back to reading the JSONL file.  This preserves
backward compatibility with tests that pre-date the Postgres migration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


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
    """Query interface for the audit trail.

    Accepts either:

    * A :class:`~pathlib.Path` pointing to a JSONL audit file (unit-test / legacy
      mode).  Each line is a JSON object with at least ``key`` and ``action``
      (or ``event_type``) fields and an optional ``timestamp`` field.
    * A backend object (typically
      :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`) that
      exposes a ``query_audit`` method.  Falls back to an empty result set when
      the backend has no audit support (e.g. unit-test ``InMemoryPrivateBackend``
      without ``query_audit``).
    """

    def __init__(self, backend: Any) -> None:  # noqa: ANN401
        self._backend = backend

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit log entries with optional filters."""
        # Legacy / unit-test path: backend is a file path.
        if isinstance(self._backend, Path):
            return self._query_file(
                key=key, event_type=event_type, since=since, until=until, limit=limit
            )
        # Postgres backend path.
        query_audit = getattr(self._backend, "query_audit", None)
        if query_audit is None:
            return []
        rows: list[dict[str, Any]] = query_audit(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
        )
        return [
            AuditEntry(
                timestamp=str(r.get("timestamp", "")),
                event_type=str(r.get("event_type", "")),
                key=str(r.get("key", "")),
                details=dict(r.get("details") or {}),
            )
            for r in rows
        ]

    def count(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
    ) -> int:
        """Count matching audit entries."""
        return len(self.query(key=key, event_type=event_type, limit=10_000))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _query_file(
        self,
        *,
        key: str | None,
        event_type: str | None,
        since: str | None,
        until: str | None,
        limit: int,
    ) -> list[AuditEntry]:
        """Read and filter a JSONL audit file (legacy / unit-test path)."""
        path = self._backend  # Path object
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        results: list[AuditEntry] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Support both 'action' (legacy) and 'event_type' field names.
            ev_type = str(rec.get("event_type") or rec.get("action", ""))
            rec_key = str(rec.get("key", ""))
            ts = str(rec.get("timestamp", ""))
            details = {
                k: v
                for k, v in rec.items()
                if k not in ("action", "key", "timestamp", "event_type")
            }

            if key is not None and rec_key != key:
                continue
            if event_type is not None and ev_type != event_type:
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue

            results.append(
                AuditEntry(timestamp=ts, event_type=ev_type, key=rec_key, details=details)
            )
            if len(results) >= limit:
                break
        return results
