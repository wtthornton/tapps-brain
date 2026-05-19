"""Integration tests for TappsBrainClient / AsyncTappsBrainClient failure modes (STORY-071.5).

Exercises the full SDK wire shape — MCP ``initialize`` handshake followed by
``tools/call`` — through a real :class:`httpx.Client` / :class:`httpx.AsyncClient`
backed by :class:`httpx.MockTransport`. No live brain server required.

Covers three acceptance criteria from EPIC-071 / TAP-2133 #5:

1. Wrong token → :class:`TappsBrainAuthError` on the first ``tools/call`` with
   **no retry**, even when ``RetryConfig(max_attempts=3)`` is configured.
2. Two 503s followed by 200 → ``tools/call`` succeeds after retry under
   ``RetryConfig(max_attempts=3)``.
3. ``async with AsyncTappsBrainClient(...)`` → the underlying connection pool
   is properly closed on exit, and subsequent calls fail loudly.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from tapps_brain.client import (
    AsyncTappsBrainClient,
    RetryConfig,
    TappsBrainClient,
)
from tapps_brain.exceptions import TappsBrainAuthError

# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------


def _initialize_response(session_id: str | None = "test-session") -> httpx.Response:
    """Return a successful MCP ``initialize`` response.

    Mirrors what FastMCP 3.10.0 returns for a stateful server: a 200 carrying
    ``Mcp-Session-Id`` and a JSON-RPC ``initialize`` result envelope.
    """
    headers: dict[str, str] = {}
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    return httpx.Response(
        200,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "result": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
    )


def _tool_success_response(payload: object) -> httpx.Response:
    """Wrap *payload* in the MCP ``tools/call`` result envelope."""
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"text": json.dumps(payload)}]},
        },
    )


def _is_initialize(request: httpx.Request) -> bool:
    try:
        body = json.loads(request.content)
    except (ValueError, UnicodeDecodeError):
        return False
    return body.get("method") == "initialize"


class ScriptedHandler:
    """MCP request scripter for ``httpx.MockTransport``.

    *initialize* requests get a canned success response. *tools/call*
    requests are answered by popping the next entry from *tool_responses*,
    so tests express scenarios as an ordered list of HTTP responses.
    """

    def __init__(
        self,
        tool_responses: list[httpx.Response],
        *,
        initialize_response_factory: Callable[[], httpx.Response] = _initialize_response,
    ) -> None:
        self._tool_responses = list(tool_responses)
        self._initialize_response_factory = initialize_response_factory
        self.initialize_calls = 0
        self.tool_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if _is_initialize(request):
            self.initialize_calls += 1
            return self._initialize_response_factory()
        self.tool_calls += 1
        if not self._tool_responses:
            raise AssertionError(
                "ScriptedHandler ran out of tool responses — test scripted "
                "fewer responses than the SDK issued tools/call requests"
            )
        return self._tool_responses.pop(0)


def _swap_sync_transport(client: TappsBrainClient, handler: ScriptedHandler) -> None:
    """Replace *client*'s real ``httpx.Client`` with one backed by *handler*.

    Done after construction (mirrors the TAP-991 pattern in
    ``tests/unit/test_client.py``) so the SDK's own timeout / header wiring
    is exercised end-to-end up to the transport boundary.
    """
    client._http_client.close()
    client._http_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
        headers=dict(client._http_client.headers)
        if hasattr(client._http_client, "headers")
        else {},
    )


def _swap_async_transport(client: AsyncTappsBrainClient, handler: ScriptedHandler) -> None:
    """Replace *client*'s real ``httpx.AsyncClient`` with one backed by *handler*."""
    client._http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )


# ---------------------------------------------------------------------------
# AC1 — Wrong token → TappsBrainAuthError on first try, no retry
# ---------------------------------------------------------------------------


def test_sync_auth_failure_raises_immediately_without_retry() -> None:
    """401 on ``tools/call`` → :class:`TappsBrainAuthError`, exactly one attempt.

    Even with ``RetryConfig(max_attempts=3)`` enabled, auth failures are
    permanent and must not consume retry budget. Verified by asserting the
    handler saw exactly one tool call (plus the unavoidable initialize hop).
    """
    handler = ScriptedHandler(
        tool_responses=[
            httpx.Response(401, json={"detail": "invalid bearer token"}),
        ]
    )
    client = TappsBrainClient(
        "http://brain:8080",
        project_id="p1",
        agent_id="a1",
        auth_token="wrong-token",
        retry_config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False, max_delay=0.0),
    )
    _swap_sync_transport(client, handler)
    try:
        with pytest.raises(TappsBrainAuthError) as exc_info:
            client.recall("anything")
    finally:
        client.close()

    assert exc_info.value.status_code == 401
    assert handler.initialize_calls == 1
    # Critically: exactly one tools/call, not three — auth errors don't retry.
    assert handler.tool_calls == 1


async def test_async_auth_failure_raises_immediately_without_retry() -> None:
    """Async parity for AC1."""
    handler = ScriptedHandler(
        tool_responses=[
            httpx.Response(403, json={"detail": "forbidden"}),
        ]
    )
    client = AsyncTappsBrainClient(
        "http://brain:8080",
        project_id="p1",
        agent_id="a1",
        auth_token="wrong-token",
        retry_config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False, max_delay=0.0),
    )
    await client._ensure_client()
    _swap_async_transport(client, handler)
    try:
        with pytest.raises(TappsBrainAuthError) as exc_info:
            await client.recall("anything")
    finally:
        await client.aclose()

    assert exc_info.value.status_code == 403
    assert handler.initialize_calls == 1
    assert handler.tool_calls == 1


# ---------------------------------------------------------------------------
# AC2 — 503 twice then 200 → success after retry with RetryConfig(max_attempts=3)
# ---------------------------------------------------------------------------


def test_sync_two_503s_then_200_succeeds_after_retry() -> None:
    """Transient 5xx responses retry until the budget is consumed or success.

    Scripts ``503 → 503 → 200`` and asserts the third attempt's payload is
    returned to the caller and the handler saw all three tool attempts.
    """
    handler = ScriptedHandler(
        tool_responses=[
            httpx.Response(503, json={"detail": "brain warming up"}),
            httpx.Response(503, json={"detail": "still warming up"}),
            _tool_success_response([{"key": "k", "value": "ok"}]),
        ]
    )
    client = TappsBrainClient(
        "http://brain:8080",
        project_id="p1",
        agent_id="a1",
        # base_delay=0 + jitter off keeps the test near-instant while still
        # exercising the real retry loop.
        retry_config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False, max_delay=0.0),
    )
    _swap_sync_transport(client, handler)
    try:
        result = client.recall("anything")
    finally:
        client.close()

    assert result == [{"key": "k", "value": "ok"}]
    assert handler.initialize_calls == 1
    assert handler.tool_calls == 3


async def test_async_two_503s_then_200_succeeds_after_retry() -> None:
    """Async parity for AC2."""
    handler = ScriptedHandler(
        tool_responses=[
            httpx.Response(503, json={"detail": "brain warming up"}),
            httpx.Response(503, json={"detail": "still warming up"}),
            _tool_success_response([{"key": "k", "value": "ok"}]),
        ]
    )
    client = AsyncTappsBrainClient(
        "http://brain:8080",
        project_id="p1",
        agent_id="a1",
        retry_config=RetryConfig(max_attempts=3, base_delay=0.0, jitter=False, max_delay=0.0),
    )
    await client._ensure_client()
    _swap_async_transport(client, handler)
    try:
        result = await client.recall("anything")
    finally:
        await client.aclose()

    assert result == [{"key": "k", "value": "ok"}]
    assert handler.initialize_calls == 1
    assert handler.tool_calls == 3


# ---------------------------------------------------------------------------
# AC3 — async with → connection properly closed
# ---------------------------------------------------------------------------


async def test_async_context_manager_closes_connection_pool() -> None:
    """``async with AsyncTappsBrainClient`` must release the connection pool on exit.

    Verifies three signals so a future bug that flips only one of them still
    fails the test:

    * the SDK's own ``_closed`` flag flips to True
    * the underlying :class:`httpx.AsyncClient` reports ``is_closed``
    * calls after exit raise (``RuntimeError`` from httpx-on-closed-client)
    """
    handler = ScriptedHandler(tool_responses=[_tool_success_response([{"key": "k", "value": "v"}])])

    async with AsyncTappsBrainClient(
        "http://brain:8080",
        project_id="p1",
        agent_id="a1",
    ) as client:
        # Swap transport AFTER __aenter__ has created the real AsyncClient.
        _swap_async_transport(client, handler)
        result = await client.recall("anything")
        assert result == [{"key": "k", "value": "v"}]
        underlying = client._http_client
        assert underlying.is_closed is False

    # Post-exit state
    assert client._closed is True
    assert underlying.is_closed is True
    with pytest.raises(RuntimeError, match="closed"):
        await client.recall("anything")
