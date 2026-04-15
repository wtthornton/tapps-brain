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
        ("mcp+stdio://localhost", "mcp+stdio"),
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
    """Return a sync client whose httpx.Client is replaced with a MagicMock."""
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    mock_http = MagicMock()
    client._http_client = mock_http
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
    client = _make_sync_client(max_retries=1)
    first_call = _mock_error(
        429, {"error": "brain_rate_limited", "message": "slow down", "retry_after": 5}
    )
    second_call = _mock_success({"key": "ok"})
    client._http_client.post.side_effect = [first_call, second_call]

    with patch("tapps_brain.client.time") as mock_time:
        mock_time.sleep = MagicMock()
        client.remember("x")
        mock_time.sleep.assert_called_once_with(5.0)


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


def test_mcp_http_tool_uses_mcp_endpoint() -> None:
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("mcp+http://brain:8080", project_id="p1", agent_id="a1")
    mock_http = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"result": {"content": [{"text": '{"key": "k1"}'}]}}
    mock_http.post.return_value = resp
    client._http_client = mock_http

    result = client._mcp_http_tool("brain_remember", {"fact": "x"})
    assert result == {"key": "k1"}

    url_called: str = mock_http.post.call_args.args[0]
    assert url_called.endswith("/mcp")


def test_mcp_http_tool_embeds_idempotency_key_in_meta() -> None:
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("mcp+http://brain:8080", project_id="p1", agent_id="a1")
    mock_http = MagicMock()
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"result": {"content": [{"text": "{}"}]}}
    mock_http.post.return_value = resp
    client._http_client = mock_http

    ikey = str(uuid.uuid4())
    client._mcp_http_tool("brain_remember", {"fact": "x"}, idempotency_key=ikey)

    payload_bytes: bytes = mock_http.post.call_args.kwargs["content"]
    payload = json.loads(payload_bytes)
    assert payload["params"]["_meta"]["idempotency_key"] == ikey


# ---------------------------------------------------------------------------
# AsyncTappsBrainClient (mocked)
# ---------------------------------------------------------------------------


def _make_async_client(**kwargs: Any) -> AsyncTappsBrainClient:
    client = AsyncTappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    mock_http = AsyncMock()
    client._http_client = mock_http
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
