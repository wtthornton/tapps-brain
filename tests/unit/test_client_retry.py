"""Unit tests for the client retry layer (STORY-071.2).

Covers the four AC bullets from EPIC-071.md §STORY-071.2:

1. ``RetryConfig`` constructor defaults and the off-by-default sentinel
   (``RetryConfig(max_attempts=1)``).
2. Only ``TappsBrainTransientError`` triggers retry — permanent errors
   pass straight through.
3. The HTTP ``Retry-After`` header (and the JSON-body ``retry_after`` hint)
   override the computed backoff.
4. Retry count and backoff interval bounds.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tapps_brain.client import (
    _NO_RETRY,
    RetryConfig,
    TappsBrainClient,
    _backoff_delay,
    _resolve_retry_config,
    _retry_after_seconds,
)
from tapps_brain.errors import BrainDegradedError, BrainRateLimitedError
from tapps_brain.exceptions import (
    TappsBrainAuthError,
    TappsBrainNotFoundError,
    TappsBrainTransientError,
    TappsBrainValidationError,
)

# ---------------------------------------------------------------------------
# RetryConfig dataclass
# ---------------------------------------------------------------------------


def test_retry_config_default_values() -> None:
    """Spec: ``RetryConfig(max_attempts=3, base_delay=0.5, jitter=True)``."""
    cfg = RetryConfig()
    assert cfg.max_attempts == 3
    assert cfg.base_delay == 0.5
    assert cfg.jitter is True
    assert cfg.max_delay == 30.0


def test_retry_config_is_frozen() -> None:
    """Configs are value-objects — mutating fields must raise."""
    cfg = RetryConfig()
    with pytest.raises((AttributeError, TypeError)):
        cfg.max_attempts = 99  # type: ignore[misc]


def test_no_retry_sentinel_disables_retry() -> None:
    """``_NO_RETRY`` is the off-by-default value: a single attempt, no retries."""
    assert _NO_RETRY.max_attempts == 1


# ---------------------------------------------------------------------------
# _resolve_retry_config — explicit > legacy max_retries > off-by-default
# ---------------------------------------------------------------------------


def test_resolve_explicit_retry_config_wins() -> None:
    explicit = RetryConfig(max_attempts=7, base_delay=1.5, jitter=False, max_delay=12.0)
    assert _resolve_retry_config(explicit, max_retries=99) is explicit


def test_resolve_falls_back_to_legacy_max_retries() -> None:
    cfg = _resolve_retry_config(None, max_retries=4)
    # Legacy schedule: max_attempts = max_retries + 1, base_delay = 1.0 s.
    assert cfg.max_attempts == 5
    assert cfg.base_delay == 1.0
    assert cfg.jitter is True
    assert cfg.max_delay == 30.0


def test_resolve_defaults_to_no_retry() -> None:
    assert _resolve_retry_config(None, None) is _NO_RETRY


def test_client_default_retry_is_off() -> None:
    """Constructing a client with no retry args must yield the off sentinel."""
    client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1")
    try:
        assert client._retry_config.max_attempts == 1
        assert client._max_retries == 0  # legacy mirror
    finally:
        client._http_client.close()


def test_client_explicit_retry_config_propagates() -> None:
    cfg = RetryConfig(max_attempts=5, base_delay=2.0, jitter=False)
    client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", retry_config=cfg)
    try:
        assert client._retry_config is cfg
        assert client._max_retries == 4  # legacy mirror tracks max_attempts - 1
    finally:
        client._http_client.close()


# ---------------------------------------------------------------------------
# _retry_after_seconds — header parsing
# ---------------------------------------------------------------------------


def test_retry_after_header_parses_seconds() -> None:
    resp = MagicMock()
    resp.headers = {"retry-after": "12"}
    assert _retry_after_seconds(resp) == 12.0


def test_retry_after_header_missing_returns_zero() -> None:
    resp = MagicMock()
    resp.headers = {}
    assert _retry_after_seconds(resp) == 0.0


def test_retry_after_header_invalid_format_returns_zero() -> None:
    # HTTP-date form ("Wed, 21 Oct 2026 07:28:00 GMT") isn't parsed — falls
    # through to body hint or computed backoff. Same for any bad string.
    resp = MagicMock()
    resp.headers = {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert _retry_after_seconds(resp) == 0.0


def test_retry_after_no_headers_attribute_returns_zero() -> None:
    """Defensive against test mocks lacking a headers mapping."""

    class Bare:
        pass

    assert _retry_after_seconds(Bare()) == 0.0


# ---------------------------------------------------------------------------
# _backoff_delay — schedule
# ---------------------------------------------------------------------------


def test_backoff_delay_no_jitter_matches_exponential_schedule() -> None:
    cfg = RetryConfig(base_delay=0.5, jitter=False, max_delay=30.0)
    assert _backoff_delay(cfg, attempt=0, hint_seconds=0.0) == 0.5
    assert _backoff_delay(cfg, attempt=1, hint_seconds=0.0) == 1.0
    assert _backoff_delay(cfg, attempt=2, hint_seconds=0.0) == 2.0
    assert _backoff_delay(cfg, attempt=3, hint_seconds=0.0) == 4.0


def test_backoff_delay_capped_at_max_delay() -> None:
    cfg = RetryConfig(base_delay=1.0, jitter=False, max_delay=10.0)
    # 2 ** 5 = 32 > 10 → capped.
    assert _backoff_delay(cfg, attempt=5, hint_seconds=0.0) == 10.0
    assert _backoff_delay(cfg, attempt=10, hint_seconds=0.0) == 10.0


def test_backoff_delay_hint_overrides_schedule() -> None:
    cfg = RetryConfig(base_delay=0.5, jitter=False, max_delay=30.0)
    # Server-supplied hint wins, even when it exceeds the cap (server knows
    # better than we do when it will be ready).
    assert _backoff_delay(cfg, attempt=0, hint_seconds=60.0) == 60.0


def test_backoff_delay_jitter_within_pm20_pct() -> None:
    cfg = RetryConfig(base_delay=1.0, jitter=True, max_delay=30.0)
    samples = [_backoff_delay(cfg, attempt=0, hint_seconds=0.0) for _ in range(200)]
    # Every sample must land in [0.8, 1.2] (base 1.0 × jitter 0.8-1.2 inclusive).
    assert min(samples) >= 0.8
    assert max(samples) <= 1.2
    # And we should see spread, not a stuck value.
    assert len({round(s, 6) for s in samples}) > 10


# ---------------------------------------------------------------------------
# End-to-end behaviour through TappsBrainClient
# ---------------------------------------------------------------------------


def _make_client(**kwargs: Any) -> TappsBrainClient:
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    client._http_client = MagicMock()
    client._initialized = True
    return client


def _mock_success(body: Any) -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = body
    resp.headers = {}
    return resp


def _mock_error(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> MagicMock:
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status
    resp.json.return_value = body
    resp.headers = headers or {}
    return resp


def test_off_by_default_does_not_retry_on_503() -> None:
    """With no retry config, a single 503 raises immediately — no retry attempt."""
    client = _make_client()  # default → _NO_RETRY
    client._http_client.post.return_value = _mock_error(
        503, {"error": "brain_degraded", "message": "down"}
    )

    with pytest.raises(BrainDegradedError):
        client.remember("x")
    # Only the one attempt — no retry.
    assert client._http_client.post.call_count == 1


@pytest.mark.parametrize(
    "status,error_code,expected_exc",
    [
        (401, "unauthorized", TappsBrainAuthError),
        (403, "project_not_registered", TappsBrainAuthError),
        (404, "not_found", TappsBrainNotFoundError),
        (400, "invalid_request", TappsBrainValidationError),
        (409, "idempotency_conflict", TappsBrainValidationError),
        (422, "unprocessable", TappsBrainValidationError),
    ],
)
def test_permanent_errors_never_retry_even_when_retry_enabled(
    status: int, error_code: str, expected_exc: type[Exception]
) -> None:
    """Auth / validation / not-found are permanent — raise on first attempt."""
    cfg = RetryConfig(max_attempts=5, base_delay=0.0, jitter=False)
    client = _make_client(retry_config=cfg)
    body = {"error": error_code, "message": "nope"}
    if status == 403:
        body["project_id"] = "p1"
    client._http_client.post.return_value = _mock_error(status, body)

    with pytest.raises(expected_exc):
        client.remember("x")
    assert client._http_client.post.call_count == 1


def test_retry_count_honours_max_attempts() -> None:
    """``RetryConfig(max_attempts=N)`` produces at most N HTTP calls on transient."""
    cfg = RetryConfig(max_attempts=4, base_delay=0.0, jitter=False)
    client = _make_client(retry_config=cfg)
    # All four attempts fail transiently.
    side_effects = [
        _mock_error(503, {"error": "brain_degraded", "message": "down"}) for _ in range(4)
    ]
    client._http_client.post.side_effect = side_effects

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        with pytest.raises(BrainDegradedError):
            client.remember("x")

    assert client._http_client.post.call_count == 4
    # 3 sleeps between 4 attempts.
    assert mock_time.sleep.call_count == 3


def test_retry_succeeds_on_second_attempt() -> None:
    """A single transient failure followed by success returns the success body."""
    cfg = RetryConfig(max_attempts=3, base_delay=0.0, jitter=False)
    client = _make_client(retry_config=cfg)
    client._http_client.post.side_effect = [
        _mock_error(503, {"error": "brain_degraded", "message": "down"}),
        _mock_success({"key": "ok"}),
    ]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        result = client.remember("x")

    assert result == "ok"
    assert client._http_client.post.call_count == 2


def test_retry_after_http_header_overrides_computed_backoff() -> None:
    """Server-supplied ``Retry-After`` header wins; no jitter → exact sleep value."""
    cfg = RetryConfig(max_attempts=2, base_delay=0.1, jitter=False)
    client = _make_client(retry_config=cfg)
    client._http_client.post.side_effect = [
        _mock_error(
            429, {"error": "brain_rate_limited", "message": "slow"}, headers={"retry-after": "7"}
        ),
        _mock_success({"key": "ok"}),
    ]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        client.remember("x")

    mock_time.sleep.assert_called_once_with(7.0)


def test_retry_after_body_hint_used_when_header_missing() -> None:
    """When no header is present, the body's ``retry_after`` field is honoured."""
    cfg = RetryConfig(max_attempts=2, base_delay=0.1, jitter=False)
    client = _make_client(retry_config=cfg)
    client._http_client.post.side_effect = [
        _mock_error(
            429,
            {"error": "brain_rate_limited", "message": "slow", "retry_after": 3},
        ),
        _mock_success({"key": "ok"}),
    ]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        client.remember("x")

    mock_time.sleep.assert_called_once_with(3.0)


def test_retry_reuses_idempotency_key() -> None:
    """All retry attempts must send the same idempotency key for server-side dedup."""
    cfg = RetryConfig(max_attempts=3, base_delay=0.0, jitter=False)
    client = _make_client(retry_config=cfg)
    client._http_client.post.side_effect = [
        _mock_error(503, {"error": "brain_degraded", "message": "down"}),
        _mock_error(503, {"error": "brain_degraded", "message": "down"}),
        _mock_success({"key": "ok"}),
    ]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        client.remember("x")

    keys = {
        call.kwargs["headers"]["X-Idempotency-Key"]
        for call in client._http_client.post.call_args_list
    }
    assert len(keys) == 1, f"Each retry must reuse the original key, got {keys}"


def test_brain_rate_limited_is_transient_class() -> None:
    """Smoke: BrainRateLimitedError is-a TappsBrainTransientError (retryable)."""
    exc = BrainRateLimitedError("slow")
    assert isinstance(exc, TappsBrainTransientError)


# ---------------------------------------------------------------------------
# Async surface — narrower sanity check; the bulk of behaviour is shared code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_off_by_default_does_not_retry() -> None:
    from tapps_brain.client import AsyncTappsBrainClient

    client = AsyncTappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1")
    mock_http = AsyncMock()
    resp = AsyncMock()
    resp.is_success = False
    resp.status_code = 503
    resp.json = MagicMock(return_value={"error": "brain_degraded", "message": "down"})
    resp.headers = {}
    mock_http.post.return_value = resp
    client._http_client = mock_http
    client._initialized = True

    with pytest.raises(BrainDegradedError):
        await client.remember("x")
    assert mock_http.post.call_count == 1


@pytest.mark.asyncio
async def test_async_retry_count_honours_max_attempts() -> None:
    from tapps_brain.client import AsyncTappsBrainClient

    cfg = RetryConfig(max_attempts=3, base_delay=0.0, jitter=False)
    client = AsyncTappsBrainClient(
        "http://brain:8080", project_id="p1", agent_id="a1", retry_config=cfg
    )
    mock_http = AsyncMock()

    def _err() -> AsyncMock:
        resp = AsyncMock()
        resp.is_success = False
        resp.status_code = 503
        resp.json = MagicMock(return_value={"error": "brain_degraded", "message": "down"})
        resp.headers = {}
        return resp

    mock_http.post.side_effect = [_err(), _err(), _err()]
    client._http_client = mock_http
    client._initialized = True

    with patch("tapps_brain.client.asyncio") as mock_asyncio:
        mock_asyncio.sleep = AsyncMock()
        with pytest.raises(BrainDegradedError):
            await client.remember("x")

    assert mock_http.post.call_count == 3
    assert mock_asyncio.sleep.call_count == 2
