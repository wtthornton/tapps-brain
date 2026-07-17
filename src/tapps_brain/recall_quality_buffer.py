"""Bounded in-process ring buffer for recall-quality telemetry (TAP-2094).

Records one ``(timestamp, top_score, oldest_returned_age_days, memory_count)``
tuple per recall call, keyed by ``project_id``.  Backs the
``recall_quality_metrics`` MCP tool — a windowed aggregator over the last *N*
recalls without touching Postgres or the hot recall path.

Cross-restart persistence is **explicitly out of scope** — the buffer resets
on process restart.  Mirror of the ``_TOOL_CALL_COUNTS`` pattern in
:mod:`tapps_brain.otel_tracer`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import NamedTuple

#: Default maximum samples retained per project before FIFO eviction.
DEFAULT_MAX_SAMPLES_PER_PROJECT: int = 1000


class RecallQualitySample(NamedTuple):
    """One observation of recall quality.

    Attributes:
        timestamp: Unix epoch seconds (``time.time()`` at recall completion).
        top_score: Highest composite score in the returned memories; ``None``
            when the recall was empty.
        oldest_returned_age_days: Age in days of the oldest returned memory;
            ``None`` when the recall was empty or no ``last_accessed`` was
            available.
        memory_count: Number of memories returned by the recall (≥ 0).
    """

    timestamp: float
    top_score: float | None
    oldest_returned_age_days: float | None
    memory_count: int


_buffers: dict[str, deque[RecallQualitySample]] = {}
_lock: threading.Lock = threading.Lock()
_max_samples: int = DEFAULT_MAX_SAMPLES_PER_PROJECT


def configure(max_samples_per_project: int) -> None:
    """Reset the buffer with a new per-project maxlen (test/operator hook).

    Drops every existing sample — call only during process startup or in tests.
    Negative or zero values fall back to :data:`DEFAULT_MAX_SAMPLES_PER_PROJECT`.
    """
    global _max_samples, _buffers
    with _lock:
        _max_samples = (
            int(max_samples_per_project)
            if max_samples_per_project > 0
            else DEFAULT_MAX_SAMPLES_PER_PROJECT
        )
        _buffers = {}


def record(
    project_id: str,
    top_score: float | None,
    oldest_returned_age_days: float | None,
    memory_count: int,
    *,
    timestamp: float | None = None,
) -> None:
    """Append one :class:`RecallQualitySample` for *project_id*.

    Thread-safe.  Excess samples are FIFO-evicted by ``deque(maxlen=...)``.
    *timestamp* defaults to ``time.time()`` and is exposed only so tests can
    seed deterministic data.
    """
    ts = time.time() if timestamp is None else float(timestamp)
    sample = RecallQualitySample(
        timestamp=ts,
        top_score=top_score,
        oldest_returned_age_days=oldest_returned_age_days,
        memory_count=max(0, int(memory_count)),
    )
    with _lock:
        buf = _buffers.get(project_id)
        if buf is None:
            buf = deque(maxlen=_max_samples)
            _buffers[project_id] = buf
        buf.append(sample)


def snapshot(project_id: str) -> list[RecallQualitySample]:
    """Return a list copy of the current samples for *project_id* (oldest first).

    Empty list when no samples have been recorded for the project.
    """
    with _lock:
        buf = _buffers.get(project_id)
        return list(buf) if buf is not None else []


def reset() -> None:
    """Drop every sample for every project (test hook)."""
    with _lock:
        _buffers.clear()
