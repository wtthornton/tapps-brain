"""Sliding window rate limiter for memory write operations.

Provides warn-only rate limiting to detect anomalous write bursts
without blocking legitimate operations. Configurable per-minute and
per-session limits with batch context exemptions.

Security model
--------------
Rate-limit exemptions are granted exclusively through :func:`batch_exempt_scope`,
a context manager that sets a thread/task-local :class:`~contextvars.ContextVar`.
No public entry-point (HTTP, MCP, CLI) accepts a caller-supplied exemption string;
``MemoryStore.save()`` no longer exposes ``batch_context`` as a parameter.

Trusted internal callers (markdown importer, seeding, federation sync, memory
relay, auto-consolidation) wrap their writes with::

    with batch_exempt_scope("import_markdown"):
        store.save(key=..., value=...)
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Generator

import structlog

logger = structlog.get_logger(__name__)

# Default limits
_DEFAULT_WRITES_PER_MINUTE = 20
_DEFAULT_WRITES_PER_SESSION = 100

# Batch contexts exempt from rate limiting (H6b).
# This frozenset is the authority — batch_exempt_scope() validates against it.
BATCH_EXEMPT_CONTEXTS: frozenset[str] = frozenset(
    {
        "import_markdown",
        "memory_relay",
        "seed",
        "federation_sync",
        "consolidate",
    }
)

# Internal contextvar — set only by batch_exempt_scope(); never from user input.
_batch_ctx_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_batch_ctx_var", default=None
)


@contextmanager
def batch_exempt_scope(context: str) -> Generator[None, None, None]:
    """Grant rate-limit exemption for the current thread/task context.

    Only trusted *internal* entry-points (markdown importer, federation sync,
    seeding, consolidation, memory relay) should call this.  Never pass a
    caller-supplied string through this function.

    Args:
        context: Must be one of :data:`BATCH_EXEMPT_CONTEXTS`.

    Raises:
        ValueError: If *context* is not a recognised exempt context name.

    Example::

        with batch_exempt_scope("import_markdown"):
            store.save(key="x", value="y")
    """
    if context not in BATCH_EXEMPT_CONTEXTS:
        raise ValueError(
            f"Unknown batch context {context!r}. "
            f"Valid contexts: {sorted(BATCH_EXEMPT_CONTEXTS)}"
        )
    token = _batch_ctx_var.set(context)
    try:
        yield
    finally:
        _batch_ctx_var.reset(token)


@dataclass
class RateLimiterConfig:
    """Configuration for the sliding window rate limiter."""

    writes_per_minute: int = _DEFAULT_WRITES_PER_MINUTE
    writes_per_session: int = _DEFAULT_WRITES_PER_SESSION
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.writes_per_minute < 1:
            raise ValueError(f"writes_per_minute must be >= 1, got {self.writes_per_minute}")
        if self.writes_per_session < 1:
            raise ValueError(f"writes_per_session must be >= 1, got {self.writes_per_session}")


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool = True
    minute_exceeded: bool = False
    session_exceeded: bool = False
    current_minute_count: int = 0
    current_session_count: int = 0
    message: str = ""


@dataclass
class RateLimiterStats:
    """Accumulated anomaly statistics for health reporting."""

    minute_anomalies: int = 0
    session_anomalies: int = 0
    total_writes: int = 0
    exempt_writes: int = 0


class SlidingWindowRateLimiter:
    """Sliding window rate limiter for memory writes.

    Uses a ``collections.deque`` of timestamps for the per-minute window.
    Pruning is O(k) via ``popleft()`` where k = number of expired entries.
    Thread-safe via ``threading.Lock``.

    In warn-only mode (default), writes are never blocked — the limiter
    logs warnings and tracks anomaly counts for ``memory_health()``.
    """

    def __init__(self, config: RateLimiterConfig | None = None) -> None:
        self._config = config or RateLimiterConfig()
        self._lock = threading.Lock()
        self._timestamps: deque[float] = deque()
        self._session_count: int = 0
        self._stats = RateLimiterStats()

    @property
    def config(self) -> RateLimiterConfig:
        """Return the active configuration."""
        return self._config

    @property
    def stats(self) -> RateLimiterStats:
        """Return accumulated anomaly statistics."""
        with self._lock:
            return RateLimiterStats(
                minute_anomalies=self._stats.minute_anomalies,
                session_anomalies=self._stats.session_anomalies,
                total_writes=self._stats.total_writes,
                exempt_writes=self._stats.exempt_writes,
            )

    def check(self) -> RateLimitResult:
        """Check rate limits and record the write.

        Exemption is granted when the caller is inside a
        :func:`batch_exempt_scope` context manager — never from a
        caller-supplied parameter.

        Returns:
            ``RateLimitResult`` indicating whether limits were exceeded.
            In warn-only mode, ``allowed`` is always ``True``.
        """
        if not self._config.enabled:
            return RateLimitResult()

        # Batch context exemption (H6b) — read from contextvar only; never
        # from user-supplied data.
        _ctx = _batch_ctx_var.get()
        if _ctx is not None and _ctx in BATCH_EXEMPT_CONTEXTS:
            with self._lock:
                self._stats.exempt_writes += 1
                self._stats.total_writes += 1
            return RateLimitResult()

        now = time.monotonic()
        window_start = now - 60.0

        with self._lock:
            # Prune timestamps older than 1 minute (O(k) popleft, k = expired entries)
            while self._timestamps and self._timestamps[0] <= window_start:
                self._timestamps.popleft()

            # Record this write
            self._timestamps.append(now)
            self._session_count += 1
            self._stats.total_writes += 1

            minute_count = len(self._timestamps)
            session_count = self._session_count

            minute_exceeded = minute_count > self._config.writes_per_minute
            session_exceeded = session_count > self._config.writes_per_session

            if minute_exceeded:
                self._stats.minute_anomalies += 1
            if session_exceeded:
                self._stats.session_anomalies += 1

        # Build result
        result = RateLimitResult(
            allowed=True,  # Warn-only: never block
            minute_exceeded=minute_exceeded,
            session_exceeded=session_exceeded,
            current_minute_count=minute_count,
            current_session_count=session_count,
        )

        # Log warnings for exceeded limits
        if minute_exceeded:
            result.message = (
                f"Rate limit warning: {minute_count} writes in last minute "
                f"(limit: {self._config.writes_per_minute})"
            )
            logger.warning(
                "rate_limit_minute_exceeded",
                current=minute_count,
                limit=self._config.writes_per_minute,
            )

        if session_exceeded:
            msg = (
                f"Rate limit warning: {session_count} writes this session "
                f"(limit: {self._config.writes_per_session})"
            )
            if result.message:
                result.message += f"; {msg}"
            else:
                result.message = msg
            logger.warning(
                "rate_limit_session_exceeded",
                current=session_count,
                limit=self._config.writes_per_session,
            )

        return result

    def reset(self) -> None:
        """Reset all counters and timestamps (useful for testing)."""
        with self._lock:
            self._timestamps.clear()
            self._session_count = 0
            self._stats = RateLimiterStats()
