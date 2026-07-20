"""Feedback + flywheel methods for :class:`~tapps_brain.store.MemoryStore` (TAP-2833).

Extracted from ``store.py`` as a mixin.  Covers the flywheel surface
(zero-result gap signals, feedback processing, gap analysis, quality report) and
the explicit feedback API (rate / report / record / query, the lazy feedback
store, and Hive feedback propagation).  Behaviour is identical to the original
in-class definitions.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, cast

import structlog

from tapps_brain._store_base import _MemoryStoreBase
from tapps_brain.feedback import (
    FeedbackConfig,
    FeedbackEvent,
    FeedbackStore,
    InMemoryFeedbackStore,
)

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

#: Utility scores for the ``rate_recall`` convenience API (EPIC-029).
_RATING_SCORES: dict[str, float] = {
    "helpful": 1.0,
    "partial": 0.5,
    "irrelevant": 0.0,
    "outdated": 0.0,
}

#: Guards creation of the per-backend ``_feedback_events_lock`` attribute so
#: two MemoryStore instances sharing one backend cannot each install a
#: different lock (each store serializes only on its own ``_serialized()``).
_shared_feedback_lock_guard = threading.Lock()


class FeedbackMixin(_MemoryStoreBase):
    """Flywheel + explicit-feedback API methods (TAP-2833)."""

    # ------------------------------------------------------------------
    # Flywheel (EPIC-031)
    # ------------------------------------------------------------------

    def zero_result_gap_signals(self) -> list[tuple[str, str]]:
        """Return (query, timestamp) pairs for recalls that returned no memories."""
        with self._serialized():
            return list(self._zero_result_queries)

    def process_feedback(
        self,
        *,
        since: str | None = None,
        config: Any = None,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Apply queued feedback events to entry confidence (Bayesian update)."""
        from tapps_brain.flywheel import FeedbackProcessor, FlywheelConfig

        cfg = config if config is not None else FlywheelConfig()
        return FeedbackProcessor(cfg).process_feedback(cast("MemoryStore", self), since=since)

    def knowledge_gaps(
        self,
        limit: int = 10,
        *,
        semantic: bool = False,
    ) -> list[Any]:
        """Ranked knowledge gaps (explicit reports + zero-result recall)."""
        from tapps_brain.flywheel import GapTracker

        if limit <= 0:
            # Slicing with a negative limit would drop a suffix instead.
            return []
        gaps = GapTracker().analyze_gaps(
            cast("MemoryStore", self), use_semantic_clustering=semantic
        )
        return gaps[:limit]

    def generate_report(self, **kwargs: Any) -> Any:  # noqa: ANN401
        """Build markdown + structured quality report (flywheel)."""
        from tapps_brain.flywheel import generate_report as flywheel_generate_report

        qr = flywheel_generate_report(cast("MemoryStore", self), **kwargs)
        with self._serialized():
            self._latest_quality_report = qr.model_dump(mode="json")
        return qr

    def latest_quality_report(self) -> dict[str, Any] | None:
        """Last report from ``generate_report`` (None if never run)."""
        with self._serialized():
            return self._latest_quality_report

    # ------------------------------------------------------------------
    # Feedback API (EPIC-029)
    # ------------------------------------------------------------------

    def _get_feedback_store(self) -> FeedbackStore | InMemoryFeedbackStore:
        """Return the lazily-initialized feedback store.

        Returns a :class:`~tapps_brain.feedback.FeedbackStore` when the active
        backend is a :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`
        with a connection manager.  Falls back to an
        :class:`~tapps_brain.feedback.InMemoryFeedbackStore` when the backend
        has no Postgres connection (e.g. the unit-test ``InMemoryPrivateBackend``).
        The in-memory store persists events for the lifetime of the
        :class:`MemoryStore` instance only — it is not durable.
        """
        if self._feedback_store_instance is not None:
            return self._feedback_store_instance
        # Double-checked init under the store lock: an unlocked check-then-act
        # let two threads racing the first feedback op each build a store, and
        # events recorded through the discarded instance were silently lost on
        # the in-memory fallback path.
        with self._serialized():
            if self._feedback_store_instance is not None:
                return self._feedback_store_instance
            cm = getattr(self._persistence, "_cm", None)
            project_id = getattr(self._persistence, "_project_id", None)
            agent_id = getattr(self._persistence, "_agent_id", None)

            config: FeedbackConfig | None = None
            if self._profile is not None:
                config = getattr(self._profile, "feedback", None)

            if cm is None or project_id is None or agent_id is None:
                # No Postgres connection — fall back to in-memory store.
                # Use backend._feedback_events if available so all MemoryStore
                # instances sharing the same InMemoryPrivateBackend (same
                # project_root in tests) see the same feedback data.
                shared = getattr(self._persistence, "_feedback_events", None)
                shared_lock: threading.Lock | None = None
                if shared is not None:
                    # One lock per shared list, stored on the backend so every
                    # sharer serializes on the same object (a per-instance
                    # lock cannot protect a cross-instance list).
                    with _shared_feedback_lock_guard:
                        shared_lock = getattr(self._persistence, "_feedback_events_lock", None)
                        if shared_lock is None:
                            shared_lock = threading.Lock()
                            try:
                                self._persistence._feedback_events_lock = (  # type: ignore[attr-defined]
                                    shared_lock
                                )
                            except AttributeError:
                                shared_lock = None
                self._feedback_store_instance = InMemoryFeedbackStore(
                    config=config, shared_events=shared, shared_lock=shared_lock
                )
            else:
                self._feedback_store_instance = FeedbackStore(
                    cm,
                    project_id=project_id,
                    agent_id=agent_id,
                    config=config,
                )
            return self._feedback_store_instance

    def _propagate_feedback_to_hive(self, event: FeedbackEvent, session_id: str | None) -> None:
        """Mirror feedback to the Hive when the entry was Hive-sourced (STORY-029.7).

        Resolves namespace from the per-session hive recall index, or from
        ``event.details[\"hive_namespace\"]`` when set explicitly.

        Failure-tolerant: Hive write errors are logged and do not affect local
        feedback persistence.
        """
        if self._hive_store is None:
            return
        ek = event.entry_key
        if not ek:
            return
        ns: str | None = None
        if session_id:
            with self._serialized():
                ns = self._hive_feedback_key_index.get(session_id, {}).get(ek)
        if ns is None:
            d = event.details if isinstance(event.details, dict) else {}
            hn = d.get("hive_namespace")
            if isinstance(hn, str) and hn.strip():
                ns = hn.strip()
        if ns is None:
            return
        try:
            details_out: dict[str, Any] = (
                dict(event.details) if isinstance(event.details, dict) else {}
            )
            self._hive_store.record_feedback_event(
                event_id=event.id,
                namespace=ns,
                entry_key=ek,
                event_type=event.event_type,
                session_id=event.session_id,
                utility_score=event.utility_score,
                details=details_out,
                timestamp=event.timestamp,
                source_project=str(self._project_root.resolve()),
            )
        except Exception:
            logger.warning(
                "hive_feedback_propagate_failed",
                entry_key=ek,
                namespace=ns,
                exc_info=True,
            )

    def rate_recall(
        self,
        entry_key: str,
        *,
        rating: str = "helpful",
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Record a user rating for a recalled memory entry.

        Convenience wrapper that creates a ``recall_rated`` feedback event.

        Args:
            entry_key: The memory entry key that was recalled.
            rating: Quality rating — ``"helpful"`` (1.0), ``"partial"`` (0.5),
                ``"irrelevant"`` (0.0), or ``"outdated"`` (0.0).
            session_id: Optional calling session identifier.
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.

        Raises:
            ValueError: If *rating* is not a recognised value.
        """
        if rating not in _RATING_SCORES:
            raise ValueError(f"Unknown rating {rating!r}. Valid values: {sorted(_RATING_SCORES)}")

        log = logger.bind(project_id=self._project_id, op="feedback", event_type="recall_rated")
        log.debug("store.feedback.recall_rated")
        event = FeedbackEvent(
            event_type="recall_rated",
            entry_key=entry_key,
            session_id=session_id,
            utility_score=_RATING_SCORES[rating],
            # Caller details spread FIRST so the canonical, validated key
            # wins — the flywheel reads details["rating"], and a colliding
            # caller key silently flipped the Bayesian update direction.
            details={**(details or {}), "rating": rating},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.recall_rated")
        self._propagate_feedback_to_hive(event, session_id)
        return event

    def report_gap(
        self,
        query: str,
        *,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Report a knowledge gap — a query that returned insufficient results.

        Creates a ``gap_reported`` feedback event.  The *query* string is
        stored in ``details["query"]`` for later clustering and analysis.

        Args:
            query: The query or topic that was not well served.
            session_id: Optional calling session identifier.
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.
        """
        log = logger.bind(project_id=self._project_id, op="feedback", event_type="gap_reported")
        log.debug("store.feedback.gap_reported")
        event = FeedbackEvent(
            event_type="gap_reported",
            session_id=session_id,
            details={**(details or {}), "query": query},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.gap_reported")
        return event

    def report_issue(
        self,
        entry_key: str,
        issue: str,
        *,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Flag a quality issue with a specific memory entry.

        Creates an ``issue_flagged`` feedback event.  The *issue* description
        is stored in ``details["issue"]``.

        Args:
            entry_key: The memory entry key that has the quality issue.
            issue: Human-readable description of the issue.
            session_id: Optional calling session identifier.
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.
        """
        log = logger.bind(project_id=self._project_id, op="feedback", event_type="issue_flagged")
        log.debug("store.feedback.issue_flagged")
        event = FeedbackEvent(
            event_type="issue_flagged",
            entry_key=entry_key,
            session_id=session_id,
            details={**(details or {}), "issue": issue},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.issue_flagged")
        self._propagate_feedback_to_hive(event, session_id)
        return event

    def record_feedback(
        self,
        event_type: str,
        *,
        entry_key: str | None = None,
        session_id: str | None = None,
        utility_score: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Record a generic feedback event (built-in or custom event type).

        This is the low-level API that accepts any valid Object-Action
        snake_case ``event_type``.  Use the typed convenience methods
        (``rate_recall``, ``report_gap``, ``report_issue``) for standard
        events, and this method for custom event types registered via
        ``FeedbackConfig.custom_event_types``.

        Args:
            event_type: Object-Action snake_case event name (open enum).
            entry_key: Optional memory entry key this event relates to.
            session_id: Optional calling session identifier.
            utility_score: Numeric utility signal in [-1.0, 1.0].
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.

        Raises:
            ValueError: If *event_type* fails pattern validation, or if
                strict event types are enabled and the type is unknown.
        """
        log = logger.bind(project_id=self._project_id, op="feedback", event_type=event_type)
        log.debug("store.feedback.recorded")
        event = FeedbackEvent(
            event_type=event_type,
            entry_key=entry_key,
            session_id=session_id,
            utility_score=utility_score,
            details=details or {},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.recorded")
        self._propagate_feedback_to_hive(event, session_id)
        return event

    def query_feedback(
        self,
        *,
        event_type: str | None = None,
        entry_key: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackEvent]:
        """Query recorded feedback events with optional filters.

        Convenience wrapper around ``FeedbackStore.query()``.

        Args:
            event_type: Filter by exact event type (or None for all).
            entry_key: Filter by related memory entry key.
            session_id: Filter by session identifier.
            since: ISO-8601 lower bound (inclusive) on timestamp.
            until: ISO-8601 upper bound (inclusive) on timestamp.
            limit: Maximum number of results (default 100).

        Returns:
            Matching ``FeedbackEvent`` objects ordered by timestamp ascending.
        """
        return self._get_feedback_store().query(
            event_type=event_type,
            entry_key=entry_key,
            session_id=session_id,
            since=since,
            until=until,
            limit=limit,
        )

    def count_feedback(
        self,
        *,
        event_type: str | None = None,
        entry_key: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> int:
        """Count recorded feedback events with optional filters (no row LIMIT)."""
        fb = self._get_feedback_store()
        count_fn = getattr(fb, "count", None)
        if callable(count_fn):
            return int(
                count_fn(
                    event_type=event_type,
                    entry_key=entry_key,
                    session_id=session_id,
                    since=since,
                    until=until,
                )
            )
        # Older / minimal fakes that only implement query().
        return len(
            fb.query(
                event_type=event_type,
                entry_key=entry_key,
                session_id=session_id,
                since=since,
                until=until,
                limit=2**31 - 1,
            )
        )
