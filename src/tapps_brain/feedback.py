"""Feedback collection data model and storage (EPIC-029).

``FeedbackEvent`` is a Pydantic model with an *open* ``event_type`` field —
any Object-Action snake_case string is accepted, not a closed Literal enum.

``FeedbackConfig`` allows host projects to register custom event types and
optionally enable strict validation (reject unknown types at record time).

``FeedbackStore`` is a Postgres-backed store for feedback events, scoped to
``(project_id, agent_id)`` and persisted in the ``feedback_events`` table
defined by ``migrations/private/003_feedback_and_session.sql``.  Thread-safe
via ``threading.Lock``.

``InMemoryFeedbackStore`` is a lightweight in-process implementation of the
same interface — used in tests and offline environments where no Postgres
connection is available.

Standard event types (non-exhaustive — open enum):
    recall_rated, gap_reported, issue_flagged,
    implicit_positive, implicit_negative, implicit_correction.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from tapps_brain.postgres_connection import PostgresConnectionManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Object-Action snake_case: at least two segments separated by underscores.
# Segments: [a-z][a-z0-9]* (lower-case, starts with a letter).
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+$")

# Built-in event types shipped with tapps-brain.  Custom event types
# registered via ``FeedbackConfig.custom_event_types`` extend this set.
BUILTIN_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "recall_rated",
        "gap_reported",
        "issue_flagged",
        "implicit_positive",
        "implicit_negative",
        "implicit_correction",
        # EPIC-076 STORY-076.6: edge-level feedback events
        "edge_helpful",
        "edge_misleading",
    }
)

# ---------------------------------------------------------------------------
# FeedbackConfig
# ---------------------------------------------------------------------------


class FeedbackConfig(BaseModel):
    """Configuration for feedback collection.

    Allows host projects to:

    * Register additional event type names beyond the standard built-ins via
      ``custom_event_types``.  Each name is validated as Object-Action
      snake_case (same pattern as ``FeedbackEvent.event_type``).
    * Enable strict validation via ``strict_event_types=True``, which makes
      ``FeedbackStore.record()`` reject any event type not in the known set
      (built-ins + custom).
    """

    custom_event_types: list[str] = Field(
        default_factory=list,
        description=(
            "Additional event type names registered by the host project.  "
            "Each must match Object-Action snake_case pattern: "
            "``[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+``."
        ),
    )
    strict_event_types: bool = Field(
        default=True,
        description=(
            "When True, ``FeedbackStore.record()`` rejects event types that are "
            "not in the built-in or registered custom set.  "
            "Default True enforces a stable, audited event vocabulary."
        ),
    )
    implicit_feedback_window_seconds: int = Field(
        default=300,
        gt=0,
        description=(
            "Window in seconds for implicit feedback tracking (STORY-029.3).  "
            "If a recalled entry is not reinforced within this window it emits an "
            "``implicit_negative`` event (utility_score=-0.1).  "
            "If it is reinforced within the window an ``implicit_positive`` event "
            "(utility_score=1.0) is emitted.  Default 300 s (5 minutes)."
        ),
    )

    @field_validator("custom_event_types")
    @classmethod
    def _validate_custom_event_types(cls, v: list[str]) -> list[str]:
        for name in v:
            if not _EVENT_TYPE_RE.match(name):
                raise ValueError(
                    f"Custom event type {name!r} does not match Object-Action "
                    f"snake_case pattern (e.g. 'deploy_completed').  "
                    f"Must match: {_EVENT_TYPE_RE.pattern}"
                )
        return v

    @property
    def known_event_types(self) -> frozenset[str]:
        """Return the full set of known event types (built-ins + custom)."""
        return BUILTIN_EVENT_TYPES | frozenset(self.custom_event_types)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


class FeedbackEvent(BaseModel):
    """A single feedback signal recorded against the memory store.

    ``event_type`` is an *open* validated string — any Object-Action
    snake_case name is accepted (e.g. ``recall_rated``, ``gap_reported``,
    ``my_custom_event``).  This is intentionally NOT a closed enum so that
    downstream projects can extend it without forking the model.
    """

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID for this feedback event.",
    )
    event_type: str = Field(
        description=(
            "Object-Action snake_case event name (open enum).  "
            "Must match ``[a-z][a-z0-9]*(_[a-z][a-z0-9]*)+``."
        ),
    )
    entry_key: str | None = Field(
        default=None,
        description="Memory entry key this event relates to (if any).",
    )
    session_id: str | None = Field(
        default=None,
        description="Calling session identifier (optional).",
    )
    utility_score: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Numeric utility signal in [-1.0, 1.0].  None = unscored.",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary additional metadata for this event.",
    )
    timestamp: str = Field(
        default_factory=_utc_now_iso,
        description="ISO-8601 UTC timestamp of when the event was recorded.",
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "STORY-069.7: project_id this event was recorded under. None for "
            "legacy single-tenant events or backends without a resolved id."
        ),
    )

    @field_validator("event_type")
    @classmethod
    def _validate_event_type(cls, v: str) -> str:
        if not _EVENT_TYPE_RE.match(v):
            raise ValueError(
                f"event_type {v!r} does not match Object-Action snake_case pattern "
                f"(e.g. 'recall_rated', 'gap_reported').  "
                f"Must match: {_EVENT_TYPE_RE.pattern}"
            )
        return v


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class FeedbackStore:
    """Postgres-backed store for feedback events.

    Scoped to a single ``(project_id, agent_id)`` pair — same isolation
    model as :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`.
    The ``feedback_events`` table is created by migration
    ``003_feedback_and_session.sql``.

    Args:
        connection_manager: Shared :class:`PostgresConnectionManager` —
            typically the same one used by ``PostgresPrivateBackend`` so that
            feedback events live in the same physical database as the private
            memories they describe.
        project_id: Canonical project identifier (see
            :func:`~tapps_brain.backends.derive_project_id`).
        agent_id: Agent identifier (e.g. ``'claude-code'``).
        config: Optional :class:`FeedbackConfig` for strict-mode validation
            and custom event types.
    """

    def __init__(
        self,
        connection_manager: PostgresConnectionManager,
        *,
        project_id: str,
        agent_id: str,
        config: FeedbackConfig | None = None,
    ) -> None:
        self._cm = connection_manager
        self._project_id = project_id
        self._agent_id = agent_id
        self._config: FeedbackConfig = config if config is not None else FeedbackConfig()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, event: FeedbackEvent) -> None:
        """Persist a feedback event.

        If ``FeedbackConfig.strict_event_types`` is True, the event's
        ``event_type`` must be in the known set (built-ins + custom); otherwise
        a ``ValueError`` is raised before any write occurs.
        """
        if (
            self._config.strict_event_types
            and event.event_type not in self._config.known_event_types
        ):
            known = sorted(self._config.known_event_types)
            raise ValueError(
                f"Unknown event_type {event.event_type!r}. "
                f"With strict_event_types=True only known types are allowed. "
                f"Register it via FeedbackConfig.custom_event_types or disable strict mode. "
                f"Known types: {known}"
            )
        with self._lock, self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO feedback_events
                    (project_id, agent_id, id, event_type, entry_key,
                     session_id, utility_score, details, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (project_id, agent_id, id) DO NOTHING
                """,
                (
                    self._project_id,
                    self._agent_id,
                    event.id,
                    event.event_type,
                    event.entry_key,
                    event.session_id,
                    event.utility_score,
                    json.dumps(event.details, ensure_ascii=False),
                    event.timestamp,
                ),
            )
        logger.debug(
            "feedback.recorded",
            event_id=event.id,
            event_type=event.event_type,
            entry_key=event.entry_key,
        )

    def query(
        self,
        *,
        event_type: str | None = None,
        entry_key: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackEvent]:
        """Query feedback events with optional filters.

        Results are ordered by ``timestamp`` ascending (oldest first).
        """
        conditions: list[str] = ["project_id = %s", "agent_id = %s"]
        params: list[Any] = [self._project_id, self._agent_id]

        if event_type is not None:
            conditions.append("event_type = %s")
            params.append(event_type)
        if entry_key is not None:
            conditions.append("entry_key = %s")
            params.append(entry_key)
        if session_id is not None:
            conditions.append("session_id = %s")
            params.append(session_id)
        if since is not None:
            conditions.append("timestamp >= %s")
            params.append(since)
        if until is not None:
            conditions.append("timestamp <= %s")
            params.append(until)

        where = " AND ".join(conditions)
        sql = (
            "SELECT id, event_type, entry_key, session_id, utility_score, "
            "       details, timestamp "
            f"FROM feedback_events WHERE {where} "
            "ORDER BY timestamp ASC LIMIT %s"
        )
        params.append(limit)

        with self._lock, self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        results: list[FeedbackEvent] = []
        for row in rows:
            details_raw = row[5]
            if isinstance(details_raw, dict):
                details = details_raw
            elif isinstance(details_raw, str):
                try:
                    details = json.loads(details_raw)
                except (json.JSONDecodeError, TypeError):
                    details = {}
            else:
                details = {}
            ts = row[6]
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            try:
                results.append(
                    FeedbackEvent(
                        id=str(row[0]),
                        event_type=str(row[1]),
                        entry_key=row[2],
                        session_id=row[3],
                        utility_score=row[4],
                        details=details,
                        timestamp=ts_str,
                        project_id=self._project_id,
                    )
                )
            except Exception:
                logger.warning("feedback.query_row_skipped", row_id=row[0])
        return results

    def close(self) -> None:
        """No-op: the connection manager is owned by the caller."""
        return None


# ---------------------------------------------------------------------------
# In-memory store (unit tests / no-Postgres environments)
# ---------------------------------------------------------------------------


class InMemoryFeedbackStore:
    """Dict-backed FeedbackStore for unit tests — never used in production.

    Satisfies the same ``record()`` / ``query()`` interface as
    :class:`FeedbackStore` so that :class:`~tapps_brain.store.MemoryStore`
    can run feedback operations in test environments that have no Postgres
    connection.  Thread-safe via :class:`threading.Lock`.
    """

    def __init__(
        self,
        config: FeedbackConfig | None = None,
        *,
        shared_events: list[Any] | None = None,
    ) -> None:
        # When shared_events is provided (e.g. from InMemoryPrivateBackend),
        # all MemoryStore instances sharing the same backend see the same events.
        self._events: list[FeedbackEvent] = shared_events if shared_events is not None else []
        self._lock = threading.Lock()
        self._config: FeedbackConfig = config if config is not None else FeedbackConfig()

    def record(self, event: FeedbackEvent) -> None:
        """Persist a feedback event in memory."""
        if (
            self._config.strict_event_types
            and event.event_type not in self._config.known_event_types
        ):
            known = sorted(self._config.known_event_types)
            raise ValueError(
                f"Unknown event_type {event.event_type!r}. "
                f"With strict_event_types=True only known types are allowed. "
                f"Known types: {known}"
            )
        with self._lock:
            self._events.append(event)

    def query(
        self,
        *,
        event_type: str | None = None,
        entry_key: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackEvent]:
        """Query feedback events with optional filters (oldest-first)."""
        with self._lock:
            results = list(self._events)
        if event_type is not None:
            results = [e for e in results if e.event_type == event_type]
        if entry_key is not None:
            results = [e for e in results if e.entry_key == entry_key]
        if session_id is not None:
            results = [e for e in results if e.session_id == session_id]
        if since is not None:
            results = [e for e in results if e.timestamp >= since]
        if until is not None:
            results = [e for e in results if e.timestamp <= until]
        return results[:limit]

    def close(self) -> None:
        """No-op."""
        return None
