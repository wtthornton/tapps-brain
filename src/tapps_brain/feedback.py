"""Feedback collection data model and storage (EPIC-029).

``FeedbackEvent`` is a Pydantic model with an *open* ``event_type`` field —
any Object-Action snake_case string is accepted, not a closed Literal enum.

``FeedbackConfig`` allows host projects to register custom event types and
optionally enable strict validation (reject unknown types at record time).

``FeedbackStore`` is a SQLite-backed store for feedback events, sharing the
project's ``memory.db`` via a write-through connection.  Thread-safe via
``threading.Lock``.  Audit log emission is handled by the store's
``append_audit`` callback.

Standard event types (non-exhaustive — open enum):
    recall_rated, gap_reported, issue_flagged,
    implicit_positive, implicit_negative, implicit_correction.
"""

from __future__ import annotations

import json
import re
import sqlite3  # noqa: TC003
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field, field_validator

from tapps_brain.sqlcipher_util import connect_sqlite

if TYPE_CHECKING:
    from pathlib import Path

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

    Profile YAML example
    --------------------
    .. code-block:: yaml

       profile:
         ...
         feedback:
           custom_event_types:
             - deploy_completed
             - pr_review_requested
           strict_event_types: true
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
        """Validate that each custom event type matches the naming pattern."""
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
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1  # Internal schema version for the feedback_events table.

_CREATE_FEEDBACK_TABLE = """
    CREATE TABLE IF NOT EXISTS feedback_events (
        id          TEXT NOT NULL PRIMARY KEY,
        event_type  TEXT NOT NULL,
        entry_key   TEXT,
        session_id  TEXT,
        utility_score REAL,
        details     TEXT NOT NULL DEFAULT '{}',
        timestamp   TEXT NOT NULL
    )
"""

_CREATE_FEEDBACK_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_feedback_event_type  ON feedback_events(event_type)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_timestamp   ON feedback_events(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_entry_key   ON feedback_events(entry_key)",
    "CREATE INDEX IF NOT EXISTS idx_feedback_session_id  ON feedback_events(session_id)",
]


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

    Standard built-in event types
    ------------------------------
    * ``recall_rated``       — user rated a recall result (utility_score: 1=helpful, 0=irrelevant)
    * ``gap_reported``       — user indicated missing knowledge
    * ``issue_flagged``      — user flagged a quality issue with a memory entry
    * ``implicit_positive``  — recall followed by reinforce (implicit good signal)
    * ``implicit_negative``  — recall NOT followed by reinforce within window (weak bad signal)
    * ``implicit_correction`` — recall followed by store with overlapping content

    Custom event types follow the same Object-Action snake_case convention
    and are registered via ``FeedbackConfig.custom_event_types`` (STORY-029.8).
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
    """SQLite-backed store for feedback events.

    Shares the project's ``memory.db`` file via its own WAL-mode connection.
    Thread-safe via ``threading.Lock``.

    The ``feedback_events`` table is created by ``MemoryPersistence._create_schema``
    (persistence.py) and also idempotently on first use here.

    Args:
        db_path: Path to the SQLite database file (usually ``memory.db``).
        audit_path: Path to the JSONL audit log (for audit emission).
        encryption_key: Same passphrase as ``MemoryPersistence`` when using SQLCipher.
    """

    def __init__(
        self,
        db_path: Path,
        audit_path: Path | None = None,
        config: FeedbackConfig | None = None,
        *,
        encryption_key: str | None = None,
    ) -> None:
        self._db_path = db_path
        self._audit_path = audit_path
        self._config: FeedbackConfig = config if config is not None else FeedbackConfig()
        self._encryption_key = encryption_key
        self._lock = threading.Lock()
        self._conn = self._connect()
        self._ensure_table()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a WAL-mode SQLite or SQLCipher connection (same key as ``MemoryPersistence``)."""
        return connect_sqlite(
            self._db_path,
            encryption_key=self._encryption_key,
            check_same_thread=False,
        )

    def _ensure_table(self) -> None:
        """Create ``feedback_events`` table and indexes if not present (idempotent)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(_CREATE_FEEDBACK_TABLE)
            for stmt in _CREATE_FEEDBACK_INDEXES:
                cur.execute(stmt)
            self._conn.commit()

    def _emit_audit(self, action: str, event_id: str, extra: dict[str, Any] | None = None) -> None:
        """Append a line to the JSONL audit log (best-effort, never raises)."""
        if self._audit_path is None:
            return
        record: dict[str, Any] = {
            "action": action,
            "key": event_id,
            "timestamp": _utc_now_iso(),
        }
        if extra:
            record.update(extra)
        try:
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            logger.debug("feedback_audit_write_failed", event_id=event_id, action=action)

    # ------------------------------------------------------------------
    # Public API (record / query implemented in STORY-029.1 / 029-1b)
    # ------------------------------------------------------------------

    def record(self, event: FeedbackEvent) -> None:
        """Persist a feedback event and emit an audit log entry.

        If ``FeedbackConfig.strict_event_types`` is True, the event's
        ``event_type`` must be in the known set (built-ins + custom); otherwise
        a ``ValueError`` is raised before any write occurs.

        Implemented in story 029-1b (base) + 029-2 (strict validation).
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
        with self._lock:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO feedback_events
                    (id, event_type, entry_key, session_id, utility_score, details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.event_type,
                    event.entry_key,
                    event.session_id,
                    event.utility_score,
                    json.dumps(event.details, ensure_ascii=False),
                    event.timestamp,
                ),
            )
            self._conn.commit()
        self._emit_audit(
            "feedback_record",
            event.id,
            extra={"event_type": event.event_type, "entry_key": event.entry_key},
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

        Args:
            event_type: Exact match on ``event_type`` (or None for all).
            entry_key: Filter by related memory entry key.
            session_id: Filter by session identifier.
            since: ISO-8601 lower bound (inclusive) on ``timestamp``.
            until: ISO-8601 upper bound (inclusive) on ``timestamp``.
            limit: Maximum number of results (default 100).

        Returns:
            Matching events ordered by ``timestamp`` ascending.

        Implemented in story 029-1b.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if event_type is not None:
            conditions.append("event_type = ?")
            params.append(event_type)
        if entry_key is not None:
            conditions.append("entry_key = ?")
            params.append(entry_key)
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM feedback_events {where} ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        results: list[FeedbackEvent] = []
        for row in rows:
            try:
                details = json.loads(row["details"]) if row["details"] else {}
                results.append(
                    FeedbackEvent(
                        id=row["id"],
                        event_type=row["event_type"],
                        entry_key=row["entry_key"],
                        session_id=row["session_id"],
                        utility_score=row["utility_score"],
                        details=details,
                        timestamp=row["timestamp"],
                    )
                )
            except Exception:
                logger.warning("feedback.query_row_skipped", row_id=row["id"])
        return results

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
