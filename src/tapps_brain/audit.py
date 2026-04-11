"""Audit trail query API (EPIC-007).

Provides :class:`AuditReader`, which queries the Postgres ``audit_log`` table
created by migration 005.  Under the v3 Postgres-only persistence plane
(ADR-007) the legacy JSONL audit file is gone; ``AuditReader`` now wraps a
:class:`~tapps_brain.postgres_private.PostgresPrivateBackend` directly.
"""

from __future__ import annotations

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
    """Query interface for the Postgres ``audit_log`` table (migration 005).

    Wraps a backend (typically a
    :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`) that
    exposes a ``query_audit`` method.  Falls back to an empty result set when
    the backend has no audit support (e.g. unit-test InMemoryPrivateBackend).
    """

    def __init__(self, backend: Any) -> None:  # noqa: ANN401
        self._backend = backend

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
