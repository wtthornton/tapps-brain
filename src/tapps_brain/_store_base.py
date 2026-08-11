"""Shared typing anchor for ``MemoryStore`` mixins (TAP-2833).

``MemoryStore`` is split across cohesive mixin modules (``_store_relations``,
``_store_feedback``, ``_store_health``, ``_store_session``).  Each mixin's
methods reference instance state created in ``MemoryStore.__init__`` plus a few
cross-cutting helpers defined on the core class or sibling mixins.

This base declares those attributes (annotation-only) and the shared method
signatures (under ``TYPE_CHECKING``) so the mixins satisfy ``mypy --strict``
without adding any runtime footprint: there are no executable statements here,
so every real implementation lives on ``MemoryStore`` or a mixin and is resolved
through the normal MRO at runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading
    from collections import deque
    from contextlib import AbstractContextManager
    from pathlib import Path

    from tapps_brain._protocols import HiveBackend, PrivateBackend
    from tapps_brain.feedback import FeedbackStore, InMemoryFeedbackStore
    from tapps_brain.metrics import MetricsCollector
    from tapps_brain.models import MemoryEntry
    from tapps_brain.rate_limiter import SlidingWindowRateLimiter
    from tapps_brain.store import ConsolidationConfig


class _MemoryStoreBase:
    """Typing-only base shared by ``MemoryStore`` and its mixins (TAP-2833).

    Contains no runtime code — only attribute annotations and ``TYPE_CHECKING``
    method stubs so the split-out mixins type-check.  See the module docstring.
    """

    if TYPE_CHECKING:
        # --- instance attributes set in MemoryStore.__init__ -----------------
        _project_root: Path
        _agent_id: str | None
        _groups: list[str]
        _expert_domains: list[str]
        _profile: Any
        _persistence: PrivateBackend
        _project_id: str | None
        _lock: threading.Lock
        _lock_timeout_sec: float | None
        _consolidation_config: ConsolidationConfig
        _consolidation_in_progress: bool
        _embedding_provider: Any
        _write_rules: Any
        _lookup_engine: Any
        _write_policy: Any
        _gc_config: Any
        _metrics: MetricsCollector
        _rate_limiter: SlidingWindowRateLimiter
        _hive_store: HiveBackend | None
        _hive_agent_id: str
        _entries: dict[str, MemoryEntry]

        def _propagate_to_hive(self, entry: MemoryEntry) -> None: ...

        _bloom: Any
        _entity_index: dict[str, set[str]]
        _relations: dict[str, list[dict[str, Any]]]
        _feedback_store_instance: FeedbackStore | InMemoryFeedbackStore | None
        _session_recall_log: dict[str, list[tuple[str, float]]]
        _session_reinforced: dict[str, set[str]]
        _session_query_log: dict[str, list[tuple[str, list[str], float]]]
        _session_recalled_values: dict[str, list[tuple[str, str, float]]]
        _hive_feedback_key_index: dict[str, dict[str, str]]
        _circuit_breaker: Any
        _anomaly_detector: Any
        _diagnostics_history_store: Any
        _hive_recall_weight_multiplier: float
        _zero_result_queries: deque[tuple[str, str]]
        _latest_quality_report: dict[str, Any] | None
        _last_consolidation_candidates: int
        _last_gc_candidates: int
        _removal_epoch: int
        _removed_at: dict[str, int]

        # --- cross-cutting helpers implemented on MemoryStore / sibling mixins
        def _serialized(self) -> AbstractContextManager[None]: ...
        def _durable_view(self) -> AbstractContextManager[None]: ...
        def _resolve_scope(self, key: str, scope: str, branch: str) -> MemoryEntry | None: ...
        def _remove_entry_entities(self, key: str) -> None: ...
        def _merge_durable_entries(
            self, *, limit: int | None = None, allow_over_cap: bool = False
        ) -> None: ...
        def _note_removed_locked(self, key: str) -> None: ...
        def _ensure_entry_cached(self, key: str) -> MemoryEntry | None: ...
        def _drop_if_concurrently_removed(self, key: str) -> None: ...
