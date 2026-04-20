"""Tests for the sliding window rate limiter (H6a/H6b)."""

from __future__ import annotations

import time
from collections import deque
from unittest.mock import patch

import pytest

from tapps_brain.rate_limiter import (
    BATCH_EXEMPT_CONTEXTS,
    RateLimiterConfig,
    RateLimitResult,
    SlidingWindowRateLimiter,
)


class TestRateLimiterConfig:
    """Test RateLimiterConfig defaults."""

    def test_defaults(self) -> None:
        cfg = RateLimiterConfig()
        assert cfg.writes_per_minute == 20
        assert cfg.writes_per_session == 100
        assert cfg.enabled is True

    def test_custom_config(self) -> None:
        cfg = RateLimiterConfig(writes_per_minute=5, writes_per_session=50, enabled=False)
        assert cfg.writes_per_minute == 5
        assert cfg.writes_per_session == 50
        assert cfg.enabled is False

    def test_invalid_writes_per_minute_raises(self) -> None:
        """writes_per_minute < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="writes_per_minute"):
            RateLimiterConfig(writes_per_minute=0)
        with pytest.raises(ValueError, match="writes_per_minute"):
            RateLimiterConfig(writes_per_minute=-5)

    def test_invalid_writes_per_session_raises(self) -> None:
        """writes_per_session < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="writes_per_session"):
            RateLimiterConfig(writes_per_session=0)
        with pytest.raises(ValueError, match="writes_per_session"):
            RateLimiterConfig(writes_per_session=-1)


class TestRateLimitResult:
    """Test RateLimitResult defaults."""

    def test_defaults(self) -> None:
        result = RateLimitResult()
        assert result.allowed is True
        assert result.minute_exceeded is False
        assert result.session_exceeded is False
        assert result.current_minute_count == 0
        assert result.current_session_count == 0
        assert result.message == ""


class TestSlidingWindowRateLimiter:
    """Test the sliding window rate limiter."""

    def test_under_limit_allowed(self) -> None:
        """Writes under the limit should not trigger warnings."""
        limiter = SlidingWindowRateLimiter(RateLimiterConfig(writes_per_minute=5))
        for _ in range(5):
            result = limiter.check()
            assert result.allowed is True
            assert result.minute_exceeded is False

    def test_over_minute_limit_warns(self) -> None:
        """Exceeding per-minute limit should warn but still allow."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=3, writes_per_session=100)
        )
        # First 3 writes are fine
        for _ in range(3):
            result = limiter.check()
            assert result.minute_exceeded is False

        # 4th write exceeds minute limit
        result = limiter.check()
        assert result.allowed is True  # Warn-only, never blocks
        assert result.minute_exceeded is True
        assert result.current_minute_count == 4
        assert "Rate limit warning" in result.message

    def test_over_session_limit_warns(self) -> None:
        """Exceeding per-session limit should warn but still allow."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1000, writes_per_session=5)
        )
        for _ in range(5):
            result = limiter.check()
            assert result.session_exceeded is False

        result = limiter.check()
        assert result.allowed is True
        assert result.session_exceeded is True
        assert result.current_session_count == 6
        assert "session" in result.message

    def test_both_limits_exceeded(self) -> None:
        """Both limits exceeded at once should produce combined message."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=2, writes_per_session=2)
        )
        limiter.check()
        limiter.check()
        result = limiter.check()
        assert result.minute_exceeded is True
        assert result.session_exceeded is True
        assert "minute" in result.message
        assert "session" in result.message

    def test_disabled_limiter(self) -> None:
        """Disabled limiter should always return default result."""
        limiter = SlidingWindowRateLimiter(RateLimiterConfig(enabled=False, writes_per_minute=1))
        for _ in range(10):
            result = limiter.check()
            assert result.allowed is True
            assert result.minute_exceeded is False
            assert result.session_exceeded is False

    def test_sliding_window_expiry(self) -> None:
        """Old timestamps should be pruned from the sliding window."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=3, writes_per_session=100)
        )
        # Simulate timestamps from 2 minutes ago by patching time.monotonic
        old_time = time.monotonic() - 120.0  # 2 minutes ago

        with patch("tapps_brain.rate_limiter.time.monotonic", return_value=old_time):
            limiter.check()
            limiter.check()
            limiter.check()

        # Now check at current time — old entries should be pruned
        result = limiter.check()
        assert result.minute_exceeded is False
        assert result.current_minute_count == 1
        # Session count still accumulates
        assert result.current_session_count == 4

    def test_batch_context_exempt(self) -> None:
        """Batch context exemptions bypass rate limiting."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, writes_per_session=1)
        )
        # Use up the limit
        limiter.check()

        # Exempt contexts should not trigger warnings
        for ctx in BATCH_EXEMPT_CONTEXTS:
            result = limiter.check(batch_context=ctx)
            assert result.allowed is True
            assert result.minute_exceeded is False
            assert result.session_exceeded is False

    def test_batch_context_non_exempt(self) -> None:
        """Non-exempt batch contexts are rate limited normally."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, writes_per_session=100)
        )
        limiter.check()  # Use the limit

        result = limiter.check(batch_context="not_exempt")
        assert result.minute_exceeded is True

    def test_stats_tracking(self) -> None:
        """Stats should accumulate correctly."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=2, writes_per_session=100)
        )
        limiter.check()
        limiter.check()
        limiter.check()  # exceeds minute limit

        stats = limiter.stats
        assert stats.total_writes == 3
        assert stats.minute_anomalies == 1
        assert stats.session_anomalies == 0
        assert stats.exempt_writes == 0

    def test_stats_exempt_tracking(self) -> None:
        """Exempt writes should be tracked in stats."""
        limiter = SlidingWindowRateLimiter(RateLimiterConfig())
        limiter.check(batch_context="seed")
        limiter.check(batch_context="consolidate")

        stats = limiter.stats
        assert stats.exempt_writes == 2
        assert stats.total_writes == 2

    def test_reset(self) -> None:
        """Reset should clear all state."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, writes_per_session=1)
        )
        limiter.check()
        limiter.check()

        limiter.reset()

        stats = limiter.stats
        assert stats.total_writes == 0
        assert stats.minute_anomalies == 0
        assert stats.session_anomalies == 0

        result = limiter.check()
        assert result.minute_exceeded is False
        assert result.session_exceeded is False
        assert result.current_session_count == 1

    def test_config_property(self) -> None:
        """Config property should return the active config."""
        cfg = RateLimiterConfig(writes_per_minute=42)
        limiter = SlidingWindowRateLimiter(cfg)
        assert limiter.config is cfg
        assert limiter.config.writes_per_minute == 42

    def test_timestamps_stored_as_deque(self) -> None:
        """Internal timestamp store must be a deque for O(1) popleft pruning."""
        limiter = SlidingWindowRateLimiter()
        limiter.check()
        assert isinstance(limiter._timestamps, deque)

    def test_sliding_window_prunes_only_expired(self) -> None:
        """Pruning should only remove entries older than 60 s, not newer ones."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=10, writes_per_session=100)
        )
        now = time.monotonic()
        # Inject one old and one recent timestamp directly
        limiter._timestamps.append(now - 120.0)  # 2 minutes ago — will be pruned
        limiter._timestamps.append(now - 30.0)  # 30 s ago — kept
        limiter._session_count += 2

        result = limiter.check()
        # Old entry pruned; recent entry + this write = 2 in window
        assert result.current_minute_count == 2
        assert result.minute_exceeded is False


class TestBatchExemptContexts:
    """Test batch exempt context constants."""

    def test_expected_contexts(self) -> None:
        assert "import_markdown" in BATCH_EXEMPT_CONTEXTS
        assert "memory_relay" in BATCH_EXEMPT_CONTEXTS
        assert "seed" in BATCH_EXEMPT_CONTEXTS
        assert "federation_sync" in BATCH_EXEMPT_CONTEXTS
        assert "consolidate" in BATCH_EXEMPT_CONTEXTS
        assert "sync_from_markdown" in BATCH_EXEMPT_CONTEXTS

    def test_is_frozenset(self) -> None:
        assert isinstance(BATCH_EXEMPT_CONTEXTS, frozenset)

    def test_all_source_batch_context_literals_are_exempt_or_known_non_batch(self) -> None:
        """Drift guard: every batch_context="..." literal in src/ must be either
        in BATCH_EXEMPT_CONTEXTS (batch loop → should bypass rate limiting) or in
        the declared non-batch set (single writes that are intentionally rate-limited).

        If this test fails, either add the new context to BATCH_EXEMPT_CONTEXTS
        (if it's a bulk-import loop) or to KNOWN_NON_BATCH_CONTEXTS below
        (if it's a single write that should remain rate-limited).
        """
        import re
        from pathlib import Path

        # Single-write contexts that are intentionally rate-limited (not batch loops).
        # Update this set when adding new single-write call sites.
        KNOWN_NON_BATCH_CONTEXTS: frozenset[str] = frozenset(
            {
                "session_summary",  # one write per session index, not a bulk loop
            }
        )

        src_root = Path(__file__).parent.parent.parent / "src" / "tapps_brain"
        literal_pattern = re.compile(r'batch_context\s*=\s*"([^"]+)"')

        found: set[str] = set()
        for py_file in src_root.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for match in literal_pattern.finditer(text):
                found.add(match.group(1))

        all_known = BATCH_EXEMPT_CONTEXTS | KNOWN_NON_BATCH_CONTEXTS
        unknown = found - all_known
        assert not unknown, (
            f"Unknown batch_context literal(s) found in src/tapps_brain/: {sorted(unknown)}. "
            "Add to BATCH_EXEMPT_CONTEXTS (bulk-import loop) or KNOWN_NON_BATCH_CONTEXTS "
            "(intentionally rate-limited single write)."
        )
