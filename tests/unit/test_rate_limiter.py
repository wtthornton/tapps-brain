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
    batch_exempt_scope,
)


class TestRateLimiterConfig:
    """Test RateLimiterConfig defaults."""

    def test_defaults(self) -> None:
        cfg = RateLimiterConfig()
        assert cfg.writes_per_minute == 20
        assert cfg.lifetime_write_warn_at == 100
        assert cfg.enabled is True

    def test_custom_config(self) -> None:
        cfg = RateLimiterConfig(writes_per_minute=5, lifetime_write_warn_at=50, enabled=False)
        assert cfg.writes_per_minute == 5
        assert cfg.lifetime_write_warn_at == 50
        assert cfg.enabled is False

    def test_invalid_writes_per_minute_raises(self) -> None:
        """writes_per_minute < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="writes_per_minute"):
            RateLimiterConfig(writes_per_minute=0)
        with pytest.raises(ValueError, match="writes_per_minute"):
            RateLimiterConfig(writes_per_minute=-5)

    def test_invalid_lifetime_write_warn_at_raises(self) -> None:
        """lifetime_write_warn_at < 1 must raise ValueError."""
        with pytest.raises(ValueError, match="lifetime_write_warn_at"):
            RateLimiterConfig(lifetime_write_warn_at=0)
        with pytest.raises(ValueError, match="lifetime_write_warn_at"):
            RateLimiterConfig(lifetime_write_warn_at=-1)


class TestRateLimitResult:
    """Test RateLimitResult defaults."""

    def test_defaults(self) -> None:
        result = RateLimitResult()
        assert result.allowed is True
        assert result.minute_exceeded is False
        assert result.lifetime_exceeded is False
        assert result.current_minute_count == 0
        assert result.current_lifetime_count == 0
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
            RateLimiterConfig(writes_per_minute=3, lifetime_write_warn_at=100)
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

    def test_over_lifetime_limit_warns(self) -> None:
        """Exceeding per-process-lifetime write threshold should warn but still allow."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1000, lifetime_write_warn_at=5)
        )
        for _ in range(5):
            result = limiter.check()
            assert result.lifetime_exceeded is False

        result = limiter.check()
        assert result.allowed is True
        assert result.lifetime_exceeded is True
        assert result.current_lifetime_count == 6
        assert "lifetime" in result.message

    def test_both_limits_exceeded(self) -> None:
        """Both limits exceeded at once should produce combined message."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=2, lifetime_write_warn_at=2)
        )
        limiter.check()
        limiter.check()
        result = limiter.check()
        assert result.minute_exceeded is True
        assert result.lifetime_exceeded is True
        assert "minute" in result.message
        assert "lifetime" in result.message

    def test_disabled_limiter(self) -> None:
        """Disabled limiter should always return default result."""
        limiter = SlidingWindowRateLimiter(RateLimiterConfig(enabled=False, writes_per_minute=1))
        for _ in range(10):
            result = limiter.check()
            assert result.allowed is True
            assert result.minute_exceeded is False
            assert result.lifetime_exceeded is False

    def test_sliding_window_expiry(self) -> None:
        """Old timestamps should be pruned from the sliding window."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=3, lifetime_write_warn_at=100)
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
        # Lifetime count still accumulates across window expirations
        assert result.current_lifetime_count == 4

    def test_batch_context_exempt(self) -> None:
        """batch_exempt_scope() bypasses rate limiting for trusted contexts."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, lifetime_write_warn_at=1)
        )
        # Use up the limit
        limiter.check()

        # Exempt contexts entered via batch_exempt_scope should not trigger warnings
        for ctx in BATCH_EXEMPT_CONTEXTS:
            with batch_exempt_scope(ctx):
                result = limiter.check()
            assert result.allowed is True
            assert result.minute_exceeded is False
            assert result.lifetime_exceeded is False

    def test_batch_context_non_exempt(self) -> None:
        """Writes outside batch_exempt_scope are rate limited normally."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, lifetime_write_warn_at=100)
        )
        limiter.check()  # Use the limit

        # No exemption scope — should be rate limited
        result = limiter.check()
        assert result.minute_exceeded is True

    def test_stats_tracking(self) -> None:
        """Stats should accumulate correctly."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=2, lifetime_write_warn_at=100)
        )
        limiter.check()
        limiter.check()
        limiter.check()  # exceeds minute limit

        stats = limiter.stats
        assert stats.total_writes == 3
        assert stats.minute_anomalies == 1
        assert stats.lifetime_anomalies == 0
        assert stats.exempt_writes == 0

    def test_stats_exempt_tracking(self) -> None:
        """Exempt writes via batch_exempt_scope should be tracked in stats."""
        limiter = SlidingWindowRateLimiter(RateLimiterConfig())
        with batch_exempt_scope("seed"):
            limiter.check()
        with batch_exempt_scope("consolidate"):
            limiter.check()

        stats = limiter.stats
        assert stats.exempt_writes == 2
        assert stats.total_writes == 2

    def test_reset(self) -> None:
        """Reset should clear all state."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, lifetime_write_warn_at=1)
        )
        limiter.check()
        limiter.check()

        limiter.reset()

        stats = limiter.stats
        assert stats.total_writes == 0
        assert stats.minute_anomalies == 0
        assert stats.lifetime_anomalies == 0

        result = limiter.check()
        assert result.minute_exceeded is False
        assert result.lifetime_exceeded is False
        assert result.current_lifetime_count == 1

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
            RateLimiterConfig(writes_per_minute=10, lifetime_write_warn_at=100)
        )
        now = time.monotonic()
        # Inject one old and one recent timestamp directly
        limiter._timestamps.append(now - 120.0)  # 2 minutes ago — will be pruned
        limiter._timestamps.append(now - 30.0)  # 30 s ago — kept
        limiter._lifetime_writes += 2

        result = limiter.check()
        # Old entry pruned; recent entry + this write = 2 in window
        assert result.current_minute_count == 2
        assert result.minute_exceeded is False

    def test_lifetime_counter_never_resets_across_minute_windows(self) -> None:
        """lifetime_write_warn_at is a process-lifetime counter, NOT per-session or per-minute.

        It must keep accumulating even after the per-minute window rolls over, so that
        the warning threshold reflects total non-exempt writes since process start.
        """
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=100, lifetime_write_warn_at=200)
        )
        old_time = time.monotonic() - 120.0  # 2 minutes ago

        # Write 150 times in the "past" (outside the current minute window)
        with patch("tapps_brain.rate_limiter.time.monotonic", return_value=old_time):
            for _ in range(150):
                limiter.check()

        # Minute window has rolled over — per-minute count resets to 0 effectively
        # But lifetime counter must still be at 150
        result = limiter.check()
        assert result.current_minute_count == 1  # only the current write in window
        assert result.current_lifetime_count == 151  # all 150 old + 1 current
        assert result.lifetime_exceeded is False  # 151 <= 200


class TestBatchExemptContexts:
    """Test batch exempt context constants."""

    def test_expected_contexts(self) -> None:
        assert "import_markdown" in BATCH_EXEMPT_CONTEXTS
        assert "memory_relay" in BATCH_EXEMPT_CONTEXTS
        assert "seed" in BATCH_EXEMPT_CONTEXTS
        assert "federation_sync" in BATCH_EXEMPT_CONTEXTS
        assert "consolidate" in BATCH_EXEMPT_CONTEXTS

    def test_is_frozenset(self) -> None:
        assert isinstance(BATCH_EXEMPT_CONTEXTS, frozenset)


class TestBatchExemptScope:
    """Tests for the batch_exempt_scope context manager (TAP-714 security fix)."""

    def test_unknown_context_raises(self) -> None:
        """batch_exempt_scope with an unknown context raises ValueError."""
        with pytest.raises(ValueError, match="Unknown batch context"):
            with batch_exempt_scope("federation_sync_evil"):
                pass  # should not reach here

    def test_magic_string_outside_scope_not_exempt(self) -> None:
        """Passing a magic string without batch_exempt_scope does NOT grant exemption.

        This is the core security test for TAP-714: a caller cannot bypass
        rate limiting by simply calling check() outside of batch_exempt_scope(),
        even if they know the exempt context names.
        """
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, writes_per_session=100)
        )
        limiter.check()  # Use up the per-minute limit

        # check() has no batch_context parameter — external callers cannot
        # inject the exemption string without using batch_exempt_scope().
        result = limiter.check()
        assert result.minute_exceeded is True, (
            "A caller without batch_exempt_scope must not bypass rate limiting"
        )

    def test_scope_restores_after_exit(self) -> None:
        """batch_exempt_scope resets the contextvar on exit — no exemption leaks."""
        limiter = SlidingWindowRateLimiter(
            RateLimiterConfig(writes_per_minute=1, writes_per_session=100)
        )
        limiter.check()  # use up limit

        with batch_exempt_scope("seed"):
            inside = limiter.check()
        outside = limiter.check()  # after exiting the scope

        assert inside.minute_exceeded is False, "Should be exempt inside scope"
        assert outside.minute_exceeded is True, "Should NOT be exempt outside scope"

    def test_scope_is_context_var_not_global(self) -> None:
        """batch_exempt_scope does not set a global flag; it uses a ContextVar."""
        import contextvars

        from tapps_brain.rate_limiter import _batch_ctx_var

        assert isinstance(_batch_ctx_var, contextvars.ContextVar)
        # Before entering a scope, the contextvar has no value
        assert _batch_ctx_var.get() is None
        with batch_exempt_scope("seed"):
            assert _batch_ctx_var.get() == "seed"
        # After exiting the scope, the contextvar is reset
        assert _batch_ctx_var.get() is None

    def test_scope_validates_against_known_set(self) -> None:
        """Only known context names are accepted — typos are rejected."""
        with pytest.raises(ValueError, match="federation_sync_extra"):
            with batch_exempt_scope("federation_sync_extra"):
                pass
