"""Unit tests for TappsBrainClient / AsyncTappsBrainClient (STORY-070.11).

All tests use unittest.mock — no real HTTP server required.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tapps_brain.client import (
    _WRITE_TOOLS,
    AsyncTappsBrainClient,
    BrainClientProtocol,  # noqa: F401 — tested via hasattr + __all__
    TappsBrainClient,
    _detect_scheme,
    _parse_error_response,
)
from tapps_brain.errors import (
    BrainDegradedError,
    BrainRateLimitedError,
    InternalError,
    ProjectNotFoundError,
)

# ---------------------------------------------------------------------------
# _detect_scheme
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:8080", "http"),
        ("https://brain.internal", "http"),
        ("HTTP://BRAIN.EXAMPLE.COM", "http"),
        ("mcp+http://brain.internal:8080", "mcp+http"),
    ],
)
def test_detect_scheme(url: str, expected: str) -> None:
    assert _detect_scheme(url) == expected


def test_detect_scheme_invalid() -> None:
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        _detect_scheme("grpc://brain:9090")


# ---------------------------------------------------------------------------
# _parse_error_response
# ---------------------------------------------------------------------------


def test_parse_error_brain_degraded() -> None:
    exc = _parse_error_response(503, {"error": "brain_degraded", "message": "db down"})
    assert isinstance(exc, BrainDegradedError)
    assert "db down" in str(exc)


def test_parse_error_rate_limited() -> None:
    exc = _parse_error_response(429, {"error": "brain_rate_limited", "message": "slow down"})
    assert isinstance(exc, BrainRateLimitedError)


def test_parse_error_project_not_registered() -> None:
    exc = _parse_error_response(
        403,
        {
            "error": "project_not_registered",
            "message": "unknown project",
            "project_id": "my-proj",
        },
    )
    assert isinstance(exc, ProjectNotFoundError)
    assert exc.project_id == "my-proj"


def test_parse_error_internal() -> None:
    exc = _parse_error_response(500, {"error": "internal_error", "message": "oops"})
    assert isinstance(exc, InternalError)


def test_parse_error_unknown_code() -> None:
    # Unknown error code → None; caller should use raise_for_status
    exc = _parse_error_response(500, {"error": "unknown_custom_code", "message": "¯\\_(ツ)_/¯"})
    assert exc is None


def test_parse_error_missing_body() -> None:
    exc = _parse_error_response(503, {})
    assert exc is None


# ---------------------------------------------------------------------------
# WRITE_TOOLS set
# ---------------------------------------------------------------------------


def test_write_tools_contains_expected() -> None:
    assert "brain_remember" in _WRITE_TOOLS
    assert "brain_learn_success" in _WRITE_TOOLS
    assert "brain_learn_failure" in _WRITE_TOOLS
    assert "memory_save" in _WRITE_TOOLS
    assert "memory_reinforce" in _WRITE_TOOLS


def test_read_tools_not_in_write_tools() -> None:
    # read-only tools must NOT be in the write set
    assert "brain_recall" not in _WRITE_TOOLS
    assert "memory_get" not in _WRITE_TOOLS
    assert "tapps_brain_health" not in _WRITE_TOOLS


# ---------------------------------------------------------------------------
# BrainClientProtocol structural check
# ---------------------------------------------------------------------------


def test_brain_client_protocol_structural() -> None:
    """TappsBrainClient satisfies BrainClientProtocol at runtime."""
    # Protocol is runtime_checkable — we can use isinstance on the class itself
    # Only instance checks work; we just verify the relevant methods exist.
    assert hasattr(TappsBrainClient, "remember")
    assert hasattr(TappsBrainClient, "recall")
    assert hasattr(TappsBrainClient, "forget")
    assert hasattr(TappsBrainClient, "close")


# ---------------------------------------------------------------------------
# TappsBrainClient — HTTP transport (mocked)
# ---------------------------------------------------------------------------


def _make_sync_client(**kwargs: Any) -> TappsBrainClient:
    """Return a sync client whose httpx.Client is replaced with a MagicMock.

    ``_initialized`` is pre-set to ``True`` so the MCP initialize handshake is
    skipped — tests that exercise session management do this themselves.
    """
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    mock_http = MagicMock()
    client._http_client = mock_http
    client._initialized = True  # skip session init in unit tests
    return client


def _mock_success(body: Any) -> MagicMock:
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = body
    return resp


def _mock_error(status: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status
    resp.json.return_value = body
    return resp


def test_remember_returns_key() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success({"key": "abc-123"})

    key = client.remember("Use ruff")
    assert key == "abc-123"


def test_remember_sends_idempotency_key() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success({"key": "abc-123"})

    client.remember("Use ruff")

    call_kwargs = client._http_client.post.call_args
    # The headers arg is passed as keyword arg 'headers'
    headers: dict[str, str] = call_kwargs.kwargs["headers"]
    assert "X-Idempotency-Key" in headers
    # Value must be a valid UUID
    key_str = headers["X-Idempotency-Key"]
    uuid.UUID(key_str)  # raises ValueError if invalid


def test_recall_does_not_send_idempotency_key() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success([{"key": "k", "value": "v"}])

    client.recall("linting")

    headers: dict[str, str] = client._http_client.post.call_args.kwargs["headers"]
    assert "X-Idempotency-Key" not in headers


def test_recall_returns_list() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success([{"key": "k1"}, {"key": "k2"}])

    results = client.recall("test query")
    assert isinstance(results, list)
    assert len(results) == 2


def test_forget_returns_bool() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success({"forgotten": True})

    assert client.forget("some-key") is True


def test_forget_returns_false_when_not_found() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success({"forgotten": False})

    assert client.forget("missing-key") is False


def test_raises_brain_degraded_on_503() -> None:
    client = _make_sync_client(max_retries=0)
    client._http_client.post.return_value = _mock_error(
        503, {"error": "brain_degraded", "message": "down"}
    )

    with pytest.raises(BrainDegradedError):
        client.remember("x")


def test_raises_project_not_found_on_403() -> None:
    client = _make_sync_client(max_retries=0)
    client._http_client.post.return_value = _mock_error(
        403,
        {
            "error": "project_not_registered",
            "message": "not found",
            "project_id": "p1",
        },
    )

    with pytest.raises(ProjectNotFoundError) as exc_info:
        client.remember("x")
    assert exc_info.value.project_id == "p1"


def test_retry_on_503_reuses_idempotency_key() -> None:
    """On retry, the same idempotency key must be sent so the server can deduplicate."""
    client = _make_sync_client(max_retries=1)
    first_call = _mock_error(503, {"error": "brain_degraded", "message": "down"})
    second_call = _mock_success({"key": "abc"})
    client._http_client.post.side_effect = [first_call, second_call]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        result = client.remember("x")

    assert result == "abc"
    assert client._http_client.post.call_count == 2

    # Both calls must have the same X-Idempotency-Key
    headers_1 = client._http_client.post.call_args_list[0].kwargs["headers"]
    headers_2 = client._http_client.post.call_args_list[1].kwargs["headers"]
    assert headers_1["X-Idempotency-Key"] == headers_2["X-Idempotency-Key"]


def test_retry_on_429_honours_retry_after() -> None:
    """Server-supplied retry_after hint is used as the base; jitter keeps it within ±20%."""
    client = _make_sync_client(max_retries=1)
    first_call = _mock_error(
        429, {"error": "brain_rate_limited", "message": "slow down", "retry_after": 5}
    )
    second_call = _mock_success({"key": "ok"})
    client._http_client.post.side_effect = [first_call, second_call]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        client.remember("x")
        mock_time.sleep.assert_called_once()
        actual = mock_time.sleep.call_args.args[0]
        # base=5.0, jitter 0.8–1.2 → 4.0–6.0
        assert 4.0 <= actual <= 6.0, f"sleep({actual}) outside expected 4.0–6.0 range"


def test_retry_backoff_capped_at_high_attempt_number() -> None:
    """At attempt=10, sleep duration must be ≤ 36 s (cap 30 s × max jitter 1.2)."""
    client = _make_sync_client(max_retries=11)
    # Build 11 failures followed by one success
    side_effects = [
        _mock_error(503, {"error": "brain_degraded", "message": "down"}) for _ in range(11)
    ]
    side_effects.append(_mock_success({"key": "ok"}))
    client._http_client.post.side_effect = side_effects

    sleep_calls: list[float] = []
    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock(side_effect=lambda s: sleep_calls.append(s))
        client.remember("stress")

    # Every sleep duration must be ≤ 36 s (cap 30 × jitter 1.2)
    for i, duration in enumerate(sleep_calls):
        assert duration <= 36.0, f"attempt {i}: sleep({duration}) exceeded 36 s cap"


def test_retry_backoff_spreads_across_concurrent_calls() -> None:
    """100 independent retries at attempt=0 must not all sleep for the same duration."""
    import random as _random

    # Seed-independent: collect 100 jittered values for attempt=0 (base=1.0)
    durations = [min(2.0**0, 30.0) * _random.uniform(0.8, 1.2) for _ in range(100)]
    # If there is no jitter every value equals 1.0; with jitter they spread 0.8–1.2
    unique_values = len({round(d, 6) for d in durations})
    assert unique_values > 1, "All 100 retry sleeps are identical — jitter is missing"


def test_health_returns_dict() -> None:
    client = _make_sync_client()
    client._http_client.post.return_value = _mock_success({"status": "ok"})

    result = client.health()
    assert isinstance(result, dict)


def test_context_manager_closes() -> None:
    with _make_sync_client() as client:
        pass
    assert client._closed is True


def test_close_idempotent() -> None:
    client = _make_sync_client()
    client.close()
    client.close()  # should not raise


# ---------------------------------------------------------------------------
# TappsBrainClient — mcp+http transport
# ---------------------------------------------------------------------------


def test_http_scheme_routes_through_mcp_endpoint() -> None:
    """Regression: http:// scheme must POST to /mcp/ (trailing slash).

    TAP-743 (v3.10.0): Starlette ``redirect_slashes=True`` 307-redirects
    POST /mcp → /mcp/; httpx drops the body on redirect, breaking every call.
    Fix: client posts directly to /mcp/ to avoid the redirect entirely.

    TAP-509 (v3.7.3) guard: path must not double to /mcp/mcp/ (FastMCP
    inner path collapse regression from that era)."""
    client = _make_sync_client()  # constructed with http://brain:8080
    client._http_client.post.return_value = _mock_success({"key": "abc-123"})

    client.remember("verify http scheme")

    url_called: str = client._http_client.post.call_args.args[0]
    assert url_called.endswith("/mcp/"), (
        f"Expected POST to end with '/mcp/' (TAP-743 — trailing slash required), got {url_called!r}"
    )
    assert not url_called.endswith("/mcp/mcp/"), (
        f"TAP-509 regression — path must not double to /mcp/mcp/, got {url_called!r}"
    )
    payload_bytes: bytes = client._http_client.post.call_args.kwargs["content"]
    payload = json.loads(payload_bytes)
    assert payload["method"] == "tools/call"
    assert payload["params"]["name"] == "brain_remember"


def test_mcp_http_tool_uses_mcp_endpoint() -> None:
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("mcp+http://brain:8080", project_id="p1", agent_id="a1")
    mock_http = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"result": {"content": [{"text": '{"key": "k1"}'}]}}
    mock_http.post.return_value = resp
    client._http_client = mock_http
    client._initialized = True  # skip initialize handshake in this unit test

    result = client._mcp_http_tool("brain_remember", {"fact": "x"})
    assert result == {"key": "k1"}

    url_called: str = mock_http.post.call_args.args[0]
    assert url_called.endswith("/mcp/"), (
        f"Expected POST to end with '/mcp/' (TAP-743), got {url_called!r}"
    )


def test_mcp_http_tool_embeds_idempotency_key_in_meta() -> None:
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("mcp+http://brain:8080", project_id="p1", agent_id="a1")
    mock_http = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"result": {"content": [{"text": "{}"}]}}
    mock_http.post.return_value = resp
    client._http_client = mock_http
    client._initialized = True  # skip initialize handshake in this unit test

    ikey = str(uuid.uuid4())
    client._mcp_http_tool("brain_remember", {"fact": "x"}, idempotency_key=ikey)

    payload_bytes: bytes = mock_http.post.call_args.kwargs["content"]
    payload = json.loads(payload_bytes)
    assert payload["params"]["_meta"]["idempotency_key"] == ikey


# ---------------------------------------------------------------------------
# AsyncTappsBrainClient (mocked)
# ---------------------------------------------------------------------------


def _make_async_client(**kwargs: Any) -> AsyncTappsBrainClient:
    """Return an async client with a mocked HTTP backend.

    ``_initialized`` is pre-set to ``True`` so the MCP initialize handshake is
    skipped — tests that exercise session management do this themselves.
    """
    client = AsyncTappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    mock_http = AsyncMock()
    client._http_client = mock_http
    client._initialized = True  # skip session init in unit tests
    return client


def _async_mock_success(body: Any) -> AsyncMock:
    resp = AsyncMock()
    resp.is_success = True
    resp.json = MagicMock(return_value=body)
    return resp


def _async_mock_error(status: int, body: dict[str, Any]) -> AsyncMock:
    resp = AsyncMock()
    resp.is_success = False
    resp.status_code = status
    resp.json = MagicMock(return_value=body)
    return resp


@pytest.mark.asyncio
async def test_async_remember_returns_key() -> None:
    client = _make_async_client()
    client._http_client.post.return_value = _async_mock_success({"key": "xyz"})

    key = await client.remember("Use mypy")
    assert key == "xyz"


@pytest.mark.asyncio
async def test_async_remember_sends_idempotency_key() -> None:
    client = _make_async_client()
    client._http_client.post.return_value = _async_mock_success({"key": "xyz"})

    await client.remember("Use mypy")

    headers: dict[str, str] = client._http_client.post.call_args.kwargs["headers"]
    assert "X-Idempotency-Key" in headers
    uuid.UUID(headers["X-Idempotency-Key"])  # must be valid UUID


@pytest.mark.asyncio
async def test_async_recall_no_idempotency_key() -> None:
    client = _make_async_client()
    client._http_client.post.return_value = _async_mock_success([])

    await client.recall("query")

    headers: dict[str, str] = client._http_client.post.call_args.kwargs["headers"]
    assert "X-Idempotency-Key" not in headers


@pytest.mark.asyncio
async def test_async_raises_brain_degraded() -> None:
    client = _make_async_client(max_retries=0)
    client._http_client.post.return_value = _async_mock_error(
        503, {"error": "brain_degraded", "message": "db down"}
    )

    with pytest.raises(BrainDegradedError):
        await client.remember("x")


@pytest.mark.asyncio
async def test_async_retry_reuses_idempotency_key() -> None:
    client = _make_async_client(max_retries=1)
    first = _async_mock_error(503, {"error": "brain_degraded", "message": "down"})
    second = _async_mock_success({"key": "abc"})
    client._http_client.post.side_effect = [first, second]

    with patch("tapps_brain.client.asyncio") as mock_asyncio:
        mock_asyncio.sleep = AsyncMock()
        result = await client.remember("x")

    assert result == "abc"
    assert client._http_client.post.call_count == 2

    h1 = client._http_client.post.call_args_list[0].kwargs["headers"]
    h2 = client._http_client.post.call_args_list[1].kwargs["headers"]
    assert h1["X-Idempotency-Key"] == h2["X-Idempotency-Key"]


@pytest.mark.asyncio
async def test_async_context_manager_closes() -> None:
    client = _make_async_client()
    async with client:
        pass
    assert client._closed is True


@pytest.mark.asyncio
async def test_async_close_idempotent() -> None:
    client = _make_async_client()
    await client.close()
    await client.close()  # should not raise


# ---------------------------------------------------------------------------
# __init__ re-exports
# ---------------------------------------------------------------------------


def test_top_level_exports() -> None:
    import tapps_brain

    assert hasattr(tapps_brain, "TappsBrainClient")
    assert hasattr(tapps_brain, "AsyncTappsBrainClient")
    assert hasattr(tapps_brain, "BrainClientProtocol")
    assert "TappsBrainClient" in tapps_brain.__all__
    assert "AsyncTappsBrainClient" in tapps_brain.__all__
    assert "BrainClientProtocol" in tapps_brain.__all__


# ---------------------------------------------------------------------------
# Session management (TAP-744) — stateful FastMCP 3.10.0 compatibility
# ---------------------------------------------------------------------------


def _mock_init_response(session_id: str | None) -> MagicMock:
    """Build a mock httpx response for the MCP initialize handshake."""
    resp = MagicMock()
    resp.is_success = True
    resp.raise_for_status = MagicMock()
    headers: dict[str, str] = {}
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    resp.headers = headers
    resp.json.return_value = {
        "jsonrpc": "2.0",
        "id": 0,
        "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
    }
    return resp


def _make_uninitialised_sync_client(**kwargs: Any) -> TappsBrainClient:
    """Return a sync client that has NOT yet performed the initialize handshake."""
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    mock_http = MagicMock()
    client._http_client = mock_http
    # _initialized stays False — the client must do the handshake itself.
    return client


def test_sync_performs_initialize_before_first_tool_call() -> None:
    """Sync client must POST initialize to /mcp before the first tools/call."""
    client = _make_uninitialised_sync_client()
    init_resp = _mock_init_response("test-session-abc")
    tool_resp = _mock_success({"key": "k1"})
    client._http_client.post.side_effect = [init_resp, tool_resp]

    client.recall("query")

    assert client._http_client.post.call_count == 2
    init_call = client._http_client.post.call_args_list[0]
    assert "/mcp" in init_call.args[0]
    payload = json.loads(init_call.kwargs["content"])
    assert payload["method"] == "initialize"


def test_sync_attaches_session_id_header_after_initialize() -> None:
    """After initialize, Mcp-Session-Id must appear on every tools/call."""
    client = _make_uninitialised_sync_client()
    init_resp = _mock_init_response("my-sid-xyz")
    tool_resp = _mock_success([])
    client._http_client.post.side_effect = [init_resp, tool_resp]

    client.recall("query")

    assert client._mcp_session_id == "my-sid-xyz"
    tool_call = client._http_client.post.call_args_list[1]
    headers: dict[str, str] = tool_call.kwargs["headers"]
    assert headers.get("Mcp-Session-Id") == "my-sid-xyz"


def test_sync_stateless_server_no_session_header() -> None:
    """For a stateless server (no Mcp-Session-Id), no session header is sent."""
    client = _make_uninitialised_sync_client()
    init_resp = _mock_init_response(None)  # stateless: no session ID returned
    tool_resp = _mock_success([])
    client._http_client.post.side_effect = [init_resp, tool_resp]

    client.recall("query")

    assert client._mcp_session_id is None
    tool_call = client._http_client.post.call_args_list[1]
    headers: dict[str, str] = tool_call.kwargs["headers"]
    assert "Mcp-Session-Id" not in headers


def test_sync_reinitializes_on_missing_session_error() -> None:
    """When the server returns 400 'Missing session ID', the client reinitialises."""
    import httpx

    client = _make_uninitialised_sync_client(max_retries=0)
    init_resp_1 = _mock_init_response("sid-first")
    # tools/call fails with 'Missing session ID'
    missing_session_resp = MagicMock()
    missing_session_resp.status_code = 400
    missing_session_resp.text = '{"detail": "Missing session ID"}'
    missing_session_resp.is_success = False
    missing_session_resp.json.return_value = {"detail": "Missing session ID"}
    missing_session_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("400", request=MagicMock(), response=missing_session_resp)
    )
    # After reinit
    init_resp_2 = _mock_init_response("sid-second")
    tool_success = _mock_success({"key": "ok"})

    client._http_client.post.side_effect = [
        init_resp_1,
        missing_session_resp,
        init_resp_2,
        tool_success,
    ]

    result = client.recall("query")
    assert result == []  # _mock_success({"key": "ok"}) → list fallback
    assert client._mcp_session_id == "sid-second"


def test_sync_initialize_skipped_on_subsequent_calls() -> None:
    """Initialize should only be called once per client lifetime."""
    client = _make_uninitialised_sync_client()
    init_resp = _mock_init_response("sid-123")
    tool_resp_1 = _mock_success([{"key": "k1"}])
    tool_resp_2 = _mock_success([{"key": "k2"}])
    client._http_client.post.side_effect = [init_resp, tool_resp_1, tool_resp_2]

    client.recall("first")
    client.recall("second")

    # init (1) + tool (1) + tool (1) = 3 calls total
    assert client._http_client.post.call_count == 3
    # First call is initialize
    assert (
        json.loads(client._http_client.post.call_args_list[0].kwargs["content"])["method"]
        == "initialize"
    )
    # Remaining two are tools/call
    assert (
        json.loads(client._http_client.post.call_args_list[1].kwargs["content"])["method"]
        == "tools/call"
    )
    assert (
        json.loads(client._http_client.post.call_args_list[2].kwargs["content"])["method"]
        == "tools/call"
    )


# ---------------------------------------------------------------------------
# Async session management (TAP-744)
# ---------------------------------------------------------------------------


def _async_mock_init_response(session_id: str | None) -> AsyncMock:
    resp = AsyncMock()
    resp.is_success = True
    resp.raise_for_status = MagicMock()
    headers: dict[str, str] = {}
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    resp.headers = headers
    resp.json = MagicMock(
        return_value={
            "jsonrpc": "2.0",
            "id": 0,
            "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
        }
    )
    return resp


def _make_uninitialised_async_client(**kwargs: Any) -> AsyncTappsBrainClient:
    client = AsyncTappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    mock_http = AsyncMock()
    client._http_client = mock_http
    # _initialized stays False
    return client


@pytest.mark.asyncio
async def test_async_performs_initialize_before_first_tool_call() -> None:
    """Async client must POST initialize to /mcp before the first tools/call."""
    client = _make_uninitialised_async_client()
    init_resp = _async_mock_init_response("async-sid")
    tool_resp = _async_mock_success([])
    client._http_client.post.side_effect = [init_resp, tool_resp]

    await client.recall("q")

    assert client._http_client.post.call_count == 2
    init_call = client._http_client.post.call_args_list[0]
    payload = json.loads(init_call.kwargs["content"])
    assert payload["method"] == "initialize"


@pytest.mark.asyncio
async def test_async_attaches_session_id_header_after_initialize() -> None:
    """Async: after initialize, Mcp-Session-Id must appear on every tools/call."""
    client = _make_uninitialised_async_client()
    init_resp = _async_mock_init_response("async-sid-xyz")
    tool_resp = _async_mock_success([])
    client._http_client.post.side_effect = [init_resp, tool_resp]

    await client.recall("q")

    assert client._mcp_session_id == "async-sid-xyz"
    tool_call = client._http_client.post.call_args_list[1]
    headers: dict[str, str] = tool_call.kwargs["headers"]
    assert headers.get("Mcp-Session-Id") == "async-sid-xyz"


@pytest.mark.asyncio
async def test_async_stateless_server_no_session_header() -> None:
    """Async: stateless server — no Mcp-Session-Id header on tool calls."""
    client = _make_uninitialised_async_client()
    init_resp = _async_mock_init_response(None)
    tool_resp = _async_mock_success([])
    client._http_client.post.side_effect = [init_resp, tool_resp]

    await client.recall("q")

    assert client._mcp_session_id is None
    tool_call = client._http_client.post.call_args_list[1]
    headers: dict[str, str] = tool_call.kwargs["headers"]
    assert "Mcp-Session-Id" not in headers
