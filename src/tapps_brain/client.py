"""Official TappsBrainClient — sync and async (STORY-070.11).

Provides a unified, typed client that mirrors the :class:`AgentBrain` method
signatures and dispatches to one of two transports:

* ``http://`` / ``https://`` — HTTP adapter REST endpoints (STORY-070.3).
* ``mcp+http://`` — MCP Streamable HTTP transport via ``/mcp/`` (STORY-070.1; TAP-743).

Both transports target the deployed ``docker-tapps-brain-http`` container.
A single container serves all agents on a box; there is no per-agent subprocess.

Usage::

    from tapps_brain.client import TappsBrainClient, AsyncTappsBrainClient

    # HTTP transport (default for deployed brains)
    with TappsBrainClient(
        "http://brain.internal:8080",
        project_id="my-project",
        agent_id="my-agent",
        auth_token="<token>",
    ) as brain:
        brain.remember("Use ruff for linting")
        results = brain.recall("linting conventions")

    # Async HTTP transport
    async with AsyncTappsBrainClient(
        "http://brain.internal:8080",
        project_id="my-project",
        agent_id="my-agent",
        auth_token="<token>",
    ) as brain:
        await brain.remember("Use ruff for linting")
        results = await brain.recall("linting conventions")
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol — the shared public surface
# ---------------------------------------------------------------------------


@runtime_checkable
class BrainClientProtocol(Protocol):
    """Minimal protocol that both sync and async clients satisfy."""

    def remember(
        self,
        fact: str,
        *,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
    ) -> str: ...
    def recall(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]: ...
    def forget(self, key: str) -> bool: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# URL scheme detection
# ---------------------------------------------------------------------------

_SCHEME_HTTP = ("http://", "https://")
_SCHEME_MCP_HTTP = "mcp+http://"

#: MCP protocol version sent in the ``initialize`` handshake.
#: Update if the upstream MCP SDK changes the negotiated version.
_MCP_PROTOCOL_VERSION = "2025-06-18"

#: Per-leg timeout defaults (STORY-071.4). Applied when neither the new per-leg
#: params nor the legacy ``timeout`` param are passed. The connect default is
#: tighter than the read default because the brain is normally on the same
#: host or LAN — a 5 s TCP handshake is already an outlier worth failing fast.
_DEFAULT_CONNECT_TIMEOUT: float = 5.0
_DEFAULT_READ_TIMEOUT: float = 30.0


def _resolve_timeouts(
    timeout: float | None,
    connect_timeout: float | None,
    read_timeout: float | None,
) -> tuple[float, float]:
    """Resolve the connect / read timeout pair (STORY-071.4).

    Per-leg params win when set. Otherwise fall back to the legacy ``timeout``
    (preserves prior behaviour for callers still using the single-knob API).
    When everything is unset, use the per-leg defaults.
    """
    connect = (
        connect_timeout
        if connect_timeout is not None
        else (timeout if timeout is not None else _DEFAULT_CONNECT_TIMEOUT)
    )
    read = (
        read_timeout
        if read_timeout is not None
        else (timeout if timeout is not None else _DEFAULT_READ_TIMEOUT)
    )
    return connect, read


def _build_httpx_timeout(connect: float, read: float) -> Any:
    """Construct an ``httpx.Timeout`` carrying the resolved connect/read pair.

    Write and pool legs piggyback on the read timeout — most calls are small
    JSON bodies, so a separate write knob would be cosmetic.
    """
    import httpx

    return httpx.Timeout(read, connect=connect, read=read, write=read, pool=read)


# ---------------------------------------------------------------------------
# Retry config (STORY-071.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryConfig:
    """Retry policy for transient brain failures (STORY-071.2).

    The SDK retries only :class:`~tapps_brain.exceptions.TappsBrainTransientError`
    (HTTP 429 / 5xx). Permanent errors — auth, validation, not-found — are
    raised on the first attempt regardless of this config.

    Retries are **off by default** on the clients: when neither ``retry_config``
    nor the legacy ``max_retries`` is passed the SDK falls back to
    ``RetryConfig(max_attempts=1)`` and never retries. Opt in by passing
    ``retry_config=RetryConfig()`` (3 attempts, 0.5 s base, ±20 % jitter, 30 s
    cap) or a custom configuration.

    Attributes
    ----------
    max_attempts:
        Maximum number of HTTP attempts including the first one. ``1`` disables
        retry; ``3`` (the dataclass default) gives the first attempt plus two
        retries.
    base_delay:
        Initial backoff in seconds. The computed delay for attempt *n* (0-indexed)
        is ``base_delay * 2 ** n``, capped at ``max_delay``.
    jitter:
        When True, the delay is multiplied by ``random.uniform(0.8, 1.2)`` —
        prevents thundering-herd retries from concurrent clients.
    max_delay:
        Hard cap on the computed delay, in seconds. Server-supplied
        ``Retry-After`` hints bypass this cap.
    """

    max_attempts: int = 3
    base_delay: float = 0.5
    jitter: bool = True
    max_delay: float = 30.0


#: Sentinel retry config that means "do not retry"; resolved when both
#: ``retry_config`` and the legacy ``max_retries`` are unset on the client.
_NO_RETRY: RetryConfig = RetryConfig(max_attempts=1)


def _resolve_retry_config(
    retry_config: RetryConfig | None,
    max_retries: int | None,
) -> RetryConfig:
    """Pick the effective :class:`RetryConfig` for a client construction call.

    Resolution order: explicit ``retry_config`` wins; otherwise derive from
    the legacy ``max_retries`` (preserves prior behaviour for callers using
    the single-knob API); otherwise default to no-retry.
    """
    if retry_config is not None:
        return retry_config
    if max_retries is not None:
        # Legacy schedule matched ``min(2.0 ** attempt, 30.0)`` with
        # base 1.0 s — preserve it exactly so back-compat callers keep
        # the same sleep pattern.
        return RetryConfig(
            max_attempts=max_retries + 1,
            base_delay=1.0,
            jitter=True,
            max_delay=30.0,
        )
    return _NO_RETRY


def _retry_after_seconds(resp: Any) -> float:
    """Parse the HTTP ``Retry-After`` header as float seconds (0.0 if absent).

    Only the delta-seconds form is honoured — the HTTP-date form is rare in
    brain responses and falls through to the body hint or computed backoff.
    Defensive against test mocks that lack a real headers mapping.

    Lookup is case-insensitive so plain ``dict`` mocks and HTTP libraries that
    preserve the canonical ``Retry-After`` casing still work (httpx ``Headers``
    is already case-insensitive; a bare ``dict`` is not).
    """
    headers = getattr(resp, "headers", None)
    if headers is None:
        return 0.0
    try:
        value = headers.get("retry-after")
        if value is None:
            value = headers.get("Retry-After")
        if value is None:
            # Slow path for plain dicts / mappings that are case-sensitive.
            for key, val in headers.items():
                if isinstance(key, str) and key.lower() == "retry-after":
                    value = val
                    break
    except (AttributeError, TypeError):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else 0.0
    if not isinstance(value, str):
        return 0.0
    try:
        parsed = float(value)
    except ValueError:
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _backoff_delay(config: RetryConfig, attempt: int, hint_seconds: float) -> float:
    """Compute the sleep duration for a retry attempt.

    Server-supplied *hint_seconds* (Retry-After header or body hint) overrides
    the computed schedule when greater than zero — the server knows when it
    will be ready better than we do. Jitter applies in both cases when enabled.
    """
    if hint_seconds > 0:
        base = hint_seconds
    else:
        base = min(config.base_delay * (2**attempt), config.max_delay)
    if config.jitter:
        return base * random.uniform(0.8, 1.2)
    return base


def _detect_scheme(url: str) -> str:
    if url.startswith(_SCHEME_MCP_HTTP):
        return "mcp+http"
    if url.lower().startswith(("http://", "https://")):
        return "http"
    raise ValueError(
        f"Unsupported URL scheme in {url!r}. Use http://, https://, or mcp+http://. "
        "The mcp+stdio:// transport has been removed — connect to the deployed "
        "docker-tapps-brain-http container instead."
    )


# ---------------------------------------------------------------------------
# Write tools — these carry an idempotency key
# ---------------------------------------------------------------------------

#: Tools that mutate state; these get an auto-generated idempotency key.
_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "brain_remember",
        "brain_learn_success",
        "brain_learn_failure",
        "memory_save",
        "memory_reinforce",
        "memory_save_many",
        "memory_reinforce_many",
        "memory_supersede",
    }
)

# ---------------------------------------------------------------------------
# Error taxonomy helpers
# ---------------------------------------------------------------------------


def _parse_error_response(status_code: int, body: dict[str, Any]) -> Exception | None:
    """Parse an HTTP error response body into a taxonomy exception.

    Returns the appropriate :class:`~tapps_brain.errors.TaxonomyError` subclass
    when the response body contains a recognised ``error`` field, or ``None``
    for unrecognised error bodies (let ``raise_for_status`` handle those).
    """
    # Lazy import — errors module has no heavy deps but we avoid a top-level
    # circular import risk.
    from tapps_brain.errors import EXCEPTION_BY_CODE, ErrorCode, ProjectNotFoundError

    error_code_str = body.get("error", "")
    message: str = body.get("message", f"HTTP {status_code}")

    try:
        code = ErrorCode(error_code_str)
    except ValueError:
        return None

    exc_class = EXCEPTION_BY_CODE.get(code)
    if exc_class is None:
        return None

    if code == ErrorCode.PROJECT_NOT_REGISTERED:
        project_id: str = body.get("project_id", "unknown")
        return ProjectNotFoundError(project_id, message)

    return exc_class(message)


# ---------------------------------------------------------------------------
# HTTP backend helpers
# ---------------------------------------------------------------------------


def _http_base(url: str) -> str:
    """Strip any trailing slash from the URL."""
    return url.rstrip("/")


def _build_headers(
    project_id: str,
    agent_id: str,
    auth_token: str | None,
    scope: str | None = None,
    group: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers: dict[str, str] = {
        "X-Project-Id": project_id,
        "X-Tapps-Agent": agent_id,
        "Content-Type": "application/json",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    if scope:
        headers["X-Tapps-Scope"] = scope
    if group:
        headers["X-Tapps-Group"] = group
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    return headers


_INITIALIZE_PAYLOAD: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": _MCP_PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "tapps-brain-client", "version": "1.0"},
    },
}


def _initialize_headers(project_id: str, agent_id: str, auth_token: str | None) -> dict[str, str]:
    # MCP streamable-HTTP requires both JSON and SSE in Accept; auth headers
    # must be present so the handshake itself passes the server's tenant gate
    # (see TAP-743 / TAP-747 follow-up: bare-header initialize was 307→401).
    headers = _build_headers(project_id, agent_id, auth_token)
    headers["Accept"] = "application/json, text/event-stream"
    return headers


_HTTP_OK_MIN = 200
_HTTP_OK_MAX = 300


def _raise_for_initialize_status(resp: Any) -> None:
    # Redirects (3xx) are not raised by raise_for_status — which silently
    # turned a /mcp → /mcp/ 307 into "no session" before. Fail loudly.
    status = resp.status_code
    if _HTTP_OK_MIN <= status < _HTTP_OK_MAX:
        return
    snippet = (resp.text or "")[:200]
    raise RuntimeError(f"MCP initialize failed: HTTP {status} from {resp.request.url} — {snippet}")


def _do_initialize(
    http_client: Any,
    base: str,
    project_id: str,
    agent_id: str,
    auth_token: str | None,
) -> str | None:
    """Perform the MCP ``initialize`` handshake against ``base/mcp/``.

    Sends the same tenant + auth headers as regular tools/call requests so
    that auth-gated FastMCP deployments admit the handshake (TAP-747). Uses
    the canonical trailing-slash URL (``/mcp/``) to avoid Starlette's 307
    redirect dropping the POST body.

    Returns the ``Mcp-Session-Id`` header value from the server's response, or
    ``None`` when the server runs in stateless mode (no session header issued).
    Raises ``RuntimeError`` on any non-2xx response, including 3xx redirects.
    """
    payload = json.dumps(_INITIALIZE_PAYLOAD).encode()
    headers = _initialize_headers(project_id, agent_id, auth_token)
    try:
        resp = http_client.post(f"{base}/mcp/", headers=headers, content=payload)
    except Exception as exc:
        raise _wrap_request_error(exc, where="MCP initialize") from exc
    _raise_for_initialize_status(resp)
    return resp.headers.get("mcp-session-id")


async def _async_do_initialize(
    http_client: Any,
    base: str,
    project_id: str,
    agent_id: str,
    auth_token: str | None,
) -> str | None:
    """Async version of :func:`_do_initialize`."""
    payload = json.dumps(_INITIALIZE_PAYLOAD).encode()
    headers = _initialize_headers(project_id, agent_id, auth_token)
    try:
        resp = await http_client.post(f"{base}/mcp/", headers=headers, content=payload)
    except Exception as exc:
        raise _wrap_request_error(exc, where="MCP initialize") from exc
    _raise_for_initialize_status(resp)
    return resp.headers.get("mcp-session-id")


def _is_missing_session_error(exc: BaseException) -> bool:
    """Return True when *exc* is a 400 HTTP error caused by a missing/expired MCP session.

    FastMCP 3.10.0 rejects requests without a valid ``Mcp-Session-Id`` with
    ``HTTP 400`` and a body that contains ``"Missing session ID"``.
    """
    # Match wrapped TappsBrainValidationError first (the typed exception
    # raised by _post_tool's fallback path now that raw httpx.HTTPStatusError
    # is no longer surfaced — STORY-071.1).
    from tapps_brain.exceptions import TappsBrainValidationError

    _http_400 = 400
    if isinstance(exc, TappsBrainValidationError) and exc.status_code == _http_400:
        message = (exc.body.get("detail") or exc.message or "").lower()
        if "missing session id" in message:
            return True
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == _http_400:
            return "missing session id" in exc.response.text.lower()
    except ImportError:
        pass
    return False


def _wrap_request_error(exc: BaseException, *, where: str) -> BaseException:
    """Translate an :mod:`httpx` request error into :class:`TappsBrainTransportError`.

    Returns the original exception unchanged when it isn't an
    :class:`httpx.RequestError`, so callers can ``raise _wrap_request_error(exc)``
    without losing taxonomy errors that happen to escape the same call site.
    """
    try:
        import httpx
    except ImportError:
        return exc
    if isinstance(exc, httpx.RequestError):
        from tapps_brain.exceptions import TappsBrainTransportError

        url = getattr(getattr(exc, "request", None), "url", "<unknown>")
        return TappsBrainTransportError(
            f"{where} failed: {type(exc).__name__} contacting {url} — {exc}",
        )
    return exc


def _mcp_envelope(
    tool_name: str,
    arguments: dict[str, Any],
    project_id: str,
    agent_id: str,
    idempotency_key: str | None,
) -> bytes:
    """Build an MCP ``tools/call`` JSON-RPC request body."""
    meta: dict[str, Any] = {"project_id": project_id, "agent_id": agent_id}
    if idempotency_key:
        meta["idempotency_key"] = idempotency_key
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments, "_meta": meta},
        "id": 1,
    }
    return json.dumps(payload).encode()


def _unwrap_mcp_result(data: Any) -> Any:
    """Pull the tool return value out of an MCP ``tools/call`` response.

    Accepts either an unwrapped dict (legacy REST shape) or a JSON-RPC
    envelope; returns the tool's Python-side value in both cases.
    """
    if isinstance(data, dict) and "result" in data:
        content = data["result"].get("content", [])
        if content and isinstance(content, list):
            first = content[0]
            if isinstance(first, dict):
                raw = first.get("text", "{}")
                try:
                    return json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    return raw
    return data


def _post_tool(
    client: Any,
    base: str,
    tool_name: str,
    arguments: dict[str, Any],
    project_id: str,
    agent_id: str,
    auth_token: str | None,
    *,
    idempotency_key: str | None = None,
    retry_config: RetryConfig | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """POST an MCP ``tools/call`` to the brain's ``/mcp/`` endpoint.

    Retries only :class:`~tapps_brain.exceptions.TappsBrainTransientError`
    (HTTP 429 / 5xx) and reuses the same idempotency key across attempts so
    server-side dedup catches duplicate writes (STORY-071.2). Permanent errors
    — auth, validation, not-found — propagate on the first attempt.

    The brain exposes tools exclusively over the streamable-HTTP MCP
    transport; the previously specced ``/v1/tools/{name}`` REST route
    was never shipped server-side.  Both ``http://`` and ``mcp+http://``
    client URLs therefore land on ``/mcp/`` (trailing slash required —
    TAP-743: Starlette ``redirect_slashes=True`` 307-redirects ``/mcp``
    to ``/mcp/`` and POST bodies are dropped on redirect).

    *extra_headers* are merged into the request headers after the standard
    header set; used to inject ``Mcp-Session-Id`` for stateful servers.
    """
    from tapps_brain.exceptions import TappsBrainTransientError, raise_for_response

    config = retry_config or _NO_RETRY
    headers = _build_headers(project_id, agent_id, auth_token, idempotency_key=idempotency_key)
    if extra_headers:
        headers.update(extra_headers)
    body_bytes = _mcp_envelope(tool_name, arguments, project_id, agent_id, idempotency_key)
    last_exc: Exception | None = None

    for attempt in range(config.max_attempts):
        try:
            resp = client.post(f"{base}/mcp/", headers=headers, content=body_bytes)
        except Exception as exc:
            raise _wrap_request_error(exc, where=f"POST {tool_name}") from exc
        if resp.is_success:
            return _unwrap_mcp_result(resp.json())

        try:
            body: dict[str, Any] = resp.json()
        except ValueError:
            body = {}

        parsed_exc = _parse_error_response(resp.status_code, body)
        if parsed_exc is None:
            # Server returned an unrecognised error code (or no body).
            # Classify by HTTP status into the TappsBrain* semantic taxonomy
            # so callers never see a raw httpx.HTTPStatusError. (STORY-071.1)
            parsed_exc = raise_for_response(resp.status_code, body)

        is_last_attempt = attempt == config.max_attempts - 1
        if isinstance(parsed_exc, TappsBrainTransientError) and not is_last_attempt:
            hint = _retry_after_seconds(resp) or float(body.get("retry_after") or 0.0)
            time.sleep(_backoff_delay(config, attempt, hint))
            last_exc = parsed_exc
            continue
        raise parsed_exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"POST {tool_name}: unexpected state after {config.max_attempts} attempts")


async def _async_post_tool(
    client: Any,
    base: str,
    tool_name: str,
    arguments: dict[str, Any],
    project_id: str,
    agent_id: str,
    auth_token: str | None,
    *,
    idempotency_key: str | None = None,
    retry_config: RetryConfig | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """Async version of :func:`_post_tool`.

    *extra_headers* are merged into the request headers after the standard
    header set; used to inject ``Mcp-Session-Id`` for stateful servers.
    """
    from tapps_brain.exceptions import TappsBrainTransientError, raise_for_response

    config = retry_config or _NO_RETRY
    headers = _build_headers(project_id, agent_id, auth_token, idempotency_key=idempotency_key)
    if extra_headers:
        headers.update(extra_headers)
    body_bytes = _mcp_envelope(tool_name, arguments, project_id, agent_id, idempotency_key)
    last_exc: Exception | None = None

    for attempt in range(config.max_attempts):
        try:
            resp = await client.post(f"{base}/mcp/", headers=headers, content=body_bytes)
        except Exception as exc:
            raise _wrap_request_error(exc, where=f"POST {tool_name}") from exc
        if resp.is_success:
            return _unwrap_mcp_result(resp.json())

        try:
            body: dict[str, Any] = resp.json()
        except ValueError:
            body = {}

        parsed_exc = _parse_error_response(resp.status_code, body)
        if parsed_exc is None:
            parsed_exc = raise_for_response(resp.status_code, body)

        is_last_attempt = attempt == config.max_attempts - 1
        if isinstance(parsed_exc, TappsBrainTransientError) and not is_last_attempt:
            hint = _retry_after_seconds(resp) or float(body.get("retry_after") or 0.0)
            await asyncio.sleep(_backoff_delay(config, attempt, hint))
            last_exc = parsed_exc
            continue
        raise parsed_exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"POST {tool_name}: unexpected state after {config.max_attempts} attempts")


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------


class TappsBrainClient:
    """Synchronous tapps-brain client.

    Supports two transports selected by *url* scheme:

    * ``http://`` / ``https://`` — :mod:`httpx` HTTP calls to the HTTP adapter
      REST endpoints.
    * ``mcp+http://`` — MCP Streamable HTTP transport; sends MCP JSON-RPC
      requests to the ``/mcp/`` endpoint of the brain HTTP adapter.

    Both transports target the deployed ``docker-tapps-brain-http`` container.
    One container serves all agents on a host; no subprocess spawning occurs.

    All write operations (``remember``, ``learn_success``, ``memory_save``,
    etc.) automatically generate an idempotency key.  If the call is retried
    due to a transient failure the **same key** is reused, preventing duplicate
    writes.

    Server-side errors are translated into typed exceptions from
    :mod:`tapps_brain.errors`:

    * ``503`` → :class:`~tapps_brain.errors.BrainDegradedError`
    * ``429`` → :class:`~tapps_brain.errors.BrainRateLimitedError`
    * ``403`` → :class:`~tapps_brain.errors.ProjectNotFoundError`
    * ``400`` → :class:`~tapps_brain.errors.InvalidRequestError`
    * ``409`` → :class:`~tapps_brain.errors.IdempotencyConflictError`
    * ``404`` → :class:`~tapps_brain.errors.NotFoundError`
    * ``500`` → :class:`~tapps_brain.errors.InternalError`

    Parameters
    ----------
    url:
        Transport URL, e.g. ``"http://brain.internal:8080"`` or
        ``"mcp+http://brain.internal:8080"``.
    project_id:
        tapps-brain project identifier.  Falls back to
        ``TAPPS_BRAIN_PROJECT`` env var.
    agent_id:
        Agent identifier.  Falls back to ``TAPPS_BRAIN_AGENT_ID`` env var.
    auth_token:
        Bearer token for the HTTP adapter.  Falls back to
        ``TAPPS_BRAIN_AUTH_TOKEN`` env var.
    timeout:
        **Deprecated** (STORY-071.4) single-knob HTTP timeout in seconds.
        When set, it applies to both connect and read legs unless a per-leg
        timeout is also provided. Prefer ``connect_timeout`` / ``read_timeout``.
    connect_timeout:
        TCP / TLS connect-leg timeout in seconds. Defaults to 5 s when nothing
        else is set; falls back to ``timeout`` when the legacy param is passed.
    read_timeout:
        Per-request read-leg timeout in seconds. Defaults to 30 s when nothing
        else is set; falls back to ``timeout`` when the legacy param is passed.
    retry_config:
        :class:`RetryConfig` controlling retry on
        :class:`~tapps_brain.exceptions.TappsBrainTransientError` (HTTP 429 /
        5xx). When unset, retries are off; pass ``RetryConfig()`` to opt in
        with sensible defaults (3 attempts, 0.5 s base, ±20 % jitter).
    max_retries:
        **Deprecated** (STORY-071.2) single-knob retry count, kept for
        back-compat. When ``retry_config`` is unset and ``max_retries`` is
        set, the SDK derives a back-compat :class:`RetryConfig` matching the
        prior schedule. Prefer ``retry_config``.
    """

    def __init__(
        self,
        url: str = "http://localhost:8080",
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        auth_token: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self._url = url
        self._scheme = _detect_scheme(url)
        self._project_id = project_id or os.environ.get("TAPPS_BRAIN_PROJECT", "default")
        self._agent_id = agent_id or os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")
        self._auth_token = auth_token or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        self._connect_timeout, self._read_timeout = _resolve_timeouts(
            timeout, connect_timeout, read_timeout
        )
        # Preserve a scalar ``_timeout`` for callers (and existing tests) that
        # poke the legacy attribute — mirrors the read leg, which is the most
        # generous bound.
        self._timeout = self._read_timeout
        self._retry_config = _resolve_retry_config(retry_config, max_retries)
        # ``_max_retries`` is the legacy mirror — exposed for back-compat
        # readers; the loop only consults ``_retry_config``.
        self._max_retries = self._retry_config.max_attempts - 1
        self._http_client: Any = None
        self._closed = False
        # MCP session state — set by _ensure_session() before first tools/call.
        # None until initialized; stays None when the server is stateless.
        self._mcp_session_id: str | None = None
        self._initialized: bool = False

        if self._scheme in ("http", "mcp+http"):
            self._init_http()

    def _init_http(self) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "TappsBrainClient requires httpx. Install it with: pip install httpx"
            ) from exc
        # TAP-991: pin the bearer token at httpx.Client construction so any
        # helper that reaches `self._http_client.post(...)` directly inherits
        # auth automatically. Per-call `headers=` (via _build_headers) still
        # wins where set — httpx merges request-level headers over client
        # defaults — so the explicit auth path is untouched. Closes the
        # regression class that produced TAP-747 / TAP-990 (initialize was
        # added later than _build_headers and silently skipped auth).
        default_headers: dict[str, str] = {}
        if self._auth_token:
            default_headers["Authorization"] = f"Bearer {self._auth_token}"
        self._http_client = httpx.Client(
            timeout=_build_httpx_timeout(self._connect_timeout, self._read_timeout),
            headers=default_headers,
        )

    def _tool(self, name: str, **kwargs: Any) -> Any:
        """Call a tool via the active transport.

        For write tools, an idempotency key is automatically generated and
        passed through the transport-specific mechanism.
        """
        ikey: str | None = None
        if name in _WRITE_TOOLS:
            ikey = str(uuid.uuid4())

        # Both http:// and mcp+http:// route through MCP streamable-HTTP.
        # The deployed brain exposes tools over MCP at /mcp/ (trailing slash
        # required — TAP-743), not at /v1/tools/{name} — the latter was
        # specced but never shipped server-side.  http:// is a convenience alias.
        return self._mcp_http_tool(name, kwargs, idempotency_key=ikey)

    def _mcp_http_tool(
        self, name: str, arguments: dict[str, Any], *, idempotency_key: str | None = None
    ) -> Any:
        """Send a ``tools/call`` to ``/mcp/`` with retry + error taxonomy.

        Performs the MCP ``initialize`` handshake on first call so the client
        works against both stateless (``TAPPS_BRAIN_STATELESS_HTTP=1``) and
        stateful (default since FastMCP 3.10.0) servers.  For stateful servers
        the ``Mcp-Session-Id`` returned by initialize is attached to every
        subsequent request.  If the session expires mid-use the client
        reinitialises automatically and retries the failed call once.
        """
        base = _http_base(self._url.replace("mcp+http://", "http://", 1))
        if not self._initialized:
            self._mcp_session_id = _do_initialize(
                self._http_client,
                base,
                self._project_id,
                self._agent_id,
                self._auth_token,
            )
            self._initialized = True
        extra_headers: dict[str, str] | None = (
            {"Mcp-Session-Id": self._mcp_session_id} if self._mcp_session_id else None
        )
        try:
            return _post_tool(
                self._http_client,
                base,
                name,
                arguments,
                self._project_id,
                self._agent_id,
                self._auth_token,
                idempotency_key=idempotency_key,
                retry_config=self._retry_config,
                extra_headers=extra_headers,
            )
        except Exception as exc:
            if _is_missing_session_error(exc):
                # Session expired — reinitialize and retry once.
                self._mcp_session_id = _do_initialize(
                    self._http_client,
                    base,
                    self._project_id,
                    self._agent_id,
                    self._auth_token,
                )
                extra_headers = (
                    {"Mcp-Session-Id": self._mcp_session_id} if self._mcp_session_id else None
                )
                return _post_tool(
                    self._http_client,
                    base,
                    name,
                    arguments,
                    self._project_id,
                    self._agent_id,
                    self._auth_token,
                    idempotency_key=idempotency_key,
                    retry_config=self._retry_config,
                    extra_headers=extra_headers,
                )
            raise

    # --- Context manager ---

    def __enter__(self) -> TappsBrainClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._closed:
            return
        self._closed = True
        if self._http_client is not None:
            self._http_client.close()

    # --- AgentBrain-compatible API ---

    def remember(
        self,
        fact: str,
        *,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
        agent_scope: str = "",
        memory_group: str = "",
        agent_id: str = "",
    ) -> str:
        """Save a memory. Returns the generated key.

        Scope (TAP-989): pass ``agent_scope`` directly as ``"private"`` /
        ``"domain"`` / ``"hive"`` / ``"group:<name>"`` for explicit Hive-namespace
        control. When ``agent_scope`` is empty, the legacy ``share`` /
        ``share_with`` params are derived for back-compat (explicit wins).

        ``memory_group`` is a project-local partition (orthogonal to the Hive
        scope axis) — leave empty unless you need group-filtered retrieval.
        """
        result = self._tool(
            "brain_remember",
            fact=fact,
            tier=tier,
            share=share,
            share_with=share_with,
            agent_scope=agent_scope,
            memory_group=memory_group,
            agent_id=agent_id,
        )
        return result.get("key", "") if isinstance(result, dict) else str(result)

    def recall(
        self,
        query: str,
        *,
        max_results: int = 5,
        agent_id: str = "",
    ) -> list[dict[str, Any]]:
        """Recall memories matching *query*."""
        result = self._tool("brain_recall", query=query, max_results=max_results, agent_id=agent_id)
        if isinstance(result, list):
            return result
        return result if isinstance(result, list) else []

    def forget(self, key: str, agent_id: str = "") -> bool:
        """Archive a memory by key."""
        result = self._tool("brain_forget", key=key, agent_id=agent_id)
        return bool(result.get("forgotten")) if isinstance(result, dict) else False

    def learn_success(self, description: str, *, task_id: str = "", agent_id: str = "") -> str:
        """Record a successful task outcome."""
        result = self._tool(
            "brain_learn_success",
            description=description,
            task_id=task_id,
            agent_id=agent_id,
        )
        return result.get("key", "") if isinstance(result, dict) else str(result)

    def learn_failure(
        self, description: str, *, task_id: str = "", error: str = "", agent_id: str = ""
    ) -> str:
        """Record a failed task outcome."""
        result = self._tool(
            "brain_learn_failure",
            description=description,
            task_id=task_id,
            error=error,
            agent_id=agent_id,
        )
        return result.get("key", "") if isinstance(result, dict) else str(result)

    def memory_save(self, key: str, value: str, **kwargs: Any) -> dict[str, Any]:
        """Save a raw memory entry."""
        return self._tool("memory_save", key=key, value=value, **kwargs)

    def memory_get(self, key: str) -> dict[str, Any]:
        """Retrieve a memory entry by key."""
        return self._tool("memory_get", key=key)

    def memory_search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Search memory entries."""
        result = self._tool("memory_search", query=query, **kwargs)
        return result if isinstance(result, list) else []

    def memory_recall(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Run auto-recall for a query."""
        return self._tool("memory_recall", query=query, **kwargs)

    def memory_reinforce(self, key: str, *, confidence_boost: float = 0.0) -> dict[str, Any]:
        """Reinforce a memory entry."""
        return self._tool("memory_reinforce", key=key, confidence_boost=confidence_boost)

    def memory_save_many(self, entries: list[dict[str, Any]], agent_id: str = "") -> dict[str, Any]:
        """Bulk save memory entries."""
        return self._tool("memory_save_many", entries=entries, agent_id=agent_id)

    def memory_recall_many(self, queries: list[str], agent_id: str = "") -> dict[str, Any]:
        """Bulk recall across multiple queries."""
        return self._tool("memory_recall_many", queries=queries, agent_id=agent_id)

    def memory_reinforce_many(
        self, entries: list[dict[str, Any]], agent_id: str = ""
    ) -> dict[str, Any]:
        """Bulk reinforce memory entries."""
        return self._tool("memory_reinforce_many", entries=entries, agent_id=agent_id)

    def status(self, agent_id: str = "") -> dict[str, Any]:
        """Return agent status."""
        return self._tool("brain_status", agent_id=agent_id)

    def health(self) -> dict[str, Any]:
        """Return brain health report."""
        return self._tool("tapps_brain_health")


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class AsyncTappsBrainClient:
    """Asynchronous tapps-brain client.

    Identical transport support and API as :class:`TappsBrainClient` but all
    methods are ``async``.  Uses a pooled :class:`httpx.AsyncClient` for both
    the HTTP REST and MCP Streamable HTTP transports.

    Both transports target the deployed ``docker-tapps-brain-http`` container.

    Write operations auto-generate an idempotency key that is reused on retry,
    preventing duplicate writes on transient failures.

    Server-side errors raise the same typed exceptions as
    :class:`TappsBrainClient`.

    Parameters are identical to :class:`TappsBrainClient`.
    """

    def __init__(
        self,
        url: str = "http://localhost:8080",
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        auth_token: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        retry_config: RetryConfig | None = None,
    ) -> None:
        self._url = url
        self._scheme = _detect_scheme(url)
        self._project_id = project_id or os.environ.get("TAPPS_BRAIN_PROJECT", "default")
        self._agent_id = agent_id or os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")
        self._auth_token = auth_token or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        self._connect_timeout, self._read_timeout = _resolve_timeouts(
            timeout, connect_timeout, read_timeout
        )
        # See sync client: legacy scalar attribute mirrors the read leg.
        self._timeout = self._read_timeout
        self._retry_config = _resolve_retry_config(retry_config, max_retries)
        self._max_retries = self._retry_config.max_attempts - 1
        self._http_client: Any = None
        self._closed = False
        # MCP session state — see TappsBrainClient for rationale.
        self._mcp_session_id: str | None = None
        self._initialized: bool = False

    async def _ensure_client(self) -> None:
        """Lazily initialise the httpx.AsyncClient."""
        if self._http_client is None:
            try:
                import httpx
            except ImportError as exc:
                raise ImportError(
                    "AsyncTappsBrainClient requires httpx. Install it with: pip install httpx"
                ) from exc
            # TAP-991: see TappsBrainClient._init_http() for rationale.
            default_headers: dict[str, str] = {}
            if self._auth_token:
                default_headers["Authorization"] = f"Bearer {self._auth_token}"
            self._http_client = httpx.AsyncClient(
                timeout=_build_httpx_timeout(self._connect_timeout, self._read_timeout),
                headers=default_headers,
            )

    async def _tool(self, name: str, **kwargs: Any) -> Any:
        """Call a tool via the active transport (async).

        Write tools automatically get an idempotency key.
        """
        await self._ensure_client()

        ikey: str | None = None
        if name in _WRITE_TOOLS:
            ikey = str(uuid.uuid4())

        # See sync _call_tool: both schemes route through MCP.
        return await self._mcp_http_tool(name, kwargs, idempotency_key=ikey)

    async def _mcp_http_tool(
        self, name: str, arguments: dict[str, Any], *, idempotency_key: str | None = None
    ) -> Any:
        """Async ``tools/call`` to ``/mcp/`` with retry + error taxonomy.

        Mirrors the session-management logic of the sync
        :meth:`TappsBrainClient._mcp_http_tool`; see its docstring for details.
        """
        base = _http_base(self._url.replace("mcp+http://", "http://", 1))
        if not self._initialized:
            self._mcp_session_id = await _async_do_initialize(
                self._http_client,
                base,
                self._project_id,
                self._agent_id,
                self._auth_token,
            )
            self._initialized = True
        extra_headers: dict[str, str] | None = (
            {"Mcp-Session-Id": self._mcp_session_id} if self._mcp_session_id else None
        )
        try:
            return await _async_post_tool(
                self._http_client,
                base,
                name,
                arguments,
                self._project_id,
                self._agent_id,
                self._auth_token,
                idempotency_key=idempotency_key,
                retry_config=self._retry_config,
                extra_headers=extra_headers,
            )
        except Exception as exc:
            if _is_missing_session_error(exc):
                # Session expired — reinitialize and retry once.
                self._mcp_session_id = await _async_do_initialize(
                    self._http_client,
                    base,
                    self._project_id,
                    self._agent_id,
                    self._auth_token,
                )
                extra_headers = (
                    {"Mcp-Session-Id": self._mcp_session_id} if self._mcp_session_id else None
                )
                return await _async_post_tool(
                    self._http_client,
                    base,
                    name,
                    arguments,
                    self._project_id,
                    self._agent_id,
                    self._auth_token,
                    idempotency_key=idempotency_key,
                    retry_config=self._retry_config,
                    extra_headers=extra_headers,
                )
            raise

    # --- Context manager ---

    async def __aenter__(self) -> AsyncTappsBrainClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` (idempotent, STORY-071.3).

        Prefer the ``async with`` context manager when the client's lifetime
        matches a single scope. Use ``aclose()`` directly for clients that
        outlive their construction site — module-level singletons, app
        startup/shutdown hooks, etc. — so the connection pool is released
        instead of being garbage-collected with sockets still open.
        """
        if self._closed:
            return
        self._closed = True
        if self._http_client is not None:
            await self._http_client.aclose()

    async def close(self) -> None:
        """Alias for :meth:`aclose` — kept so callers symmetric with the sync
        client's ``close()`` API don't need to special-case the async surface."""
        await self.aclose()

    # --- AgentBrain-compatible async API ---

    async def remember(
        self,
        fact: str,
        *,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
        agent_scope: str = "",
        memory_group: str = "",
        agent_id: str = "",
    ) -> str:
        """Save a memory. Returns the generated key.

        Scope (TAP-989): pass ``agent_scope`` directly as ``"private"`` /
        ``"domain"`` / ``"hive"`` / ``"group:<name>"`` for explicit Hive-namespace
        control. When ``agent_scope`` is empty, the legacy ``share`` /
        ``share_with`` params are derived for back-compat (explicit wins).

        ``memory_group`` is a project-local partition (orthogonal to the Hive
        scope axis) — leave empty unless you need group-filtered retrieval.
        """
        result = await self._tool(
            "brain_remember",
            fact=fact,
            tier=tier,
            share=share,
            share_with=share_with,
            agent_scope=agent_scope,
            memory_group=memory_group,
            agent_id=agent_id,
        )
        return result.get("key", "") if isinstance(result, dict) else str(result)

    async def recall(
        self,
        query: str,
        *,
        max_results: int = 5,
        agent_id: str = "",
    ) -> list[dict[str, Any]]:
        """Recall memories matching *query*."""
        result = await self._tool(
            "brain_recall", query=query, max_results=max_results, agent_id=agent_id
        )
        return result if isinstance(result, list) else []

    async def forget(self, key: str, agent_id: str = "") -> bool:
        """Archive a memory by key."""
        result = await self._tool("brain_forget", key=key, agent_id=agent_id)
        return bool(result.get("forgotten")) if isinstance(result, dict) else False

    async def learn_success(
        self, description: str, *, task_id: str = "", agent_id: str = ""
    ) -> str:
        """Record a successful task outcome."""
        result = await self._tool(
            "brain_learn_success",
            description=description,
            task_id=task_id,
            agent_id=agent_id,
        )
        return result.get("key", "") if isinstance(result, dict) else str(result)

    async def learn_failure(
        self, description: str, *, task_id: str = "", error: str = "", agent_id: str = ""
    ) -> str:
        """Record a failed task outcome."""
        result = await self._tool(
            "brain_learn_failure",
            description=description,
            task_id=task_id,
            error=error,
            agent_id=agent_id,
        )
        return result.get("key", "") if isinstance(result, dict) else str(result)

    async def memory_save(self, key: str, value: str, **kwargs: Any) -> dict[str, Any]:
        """Save a raw memory entry."""
        return await self._tool("memory_save", key=key, value=value, **kwargs)

    async def memory_get(self, key: str) -> dict[str, Any]:
        """Retrieve a memory entry by key."""
        return await self._tool("memory_get", key=key)

    async def memory_search(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Search memory entries."""
        result = await self._tool("memory_search", query=query, **kwargs)
        return result if isinstance(result, list) else []

    async def memory_recall(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Run auto-recall for a query."""
        return await self._tool("memory_recall", query=query, **kwargs)

    async def memory_reinforce(self, key: str, *, confidence_boost: float = 0.0) -> dict[str, Any]:
        """Reinforce a memory entry."""
        return await self._tool("memory_reinforce", key=key, confidence_boost=confidence_boost)

    async def memory_save_many(
        self, entries: list[dict[str, Any]], agent_id: str = ""
    ) -> dict[str, Any]:
        """Bulk save memory entries."""
        return await self._tool("memory_save_many", entries=entries, agent_id=agent_id)

    async def memory_recall_many(self, queries: list[str], agent_id: str = "") -> dict[str, Any]:
        """Bulk recall across multiple queries."""
        return await self._tool("memory_recall_many", queries=queries, agent_id=agent_id)

    async def memory_reinforce_many(
        self, entries: list[dict[str, Any]], agent_id: str = ""
    ) -> dict[str, Any]:
        """Bulk reinforce memory entries."""
        return await self._tool("memory_reinforce_many", entries=entries, agent_id=agent_id)

    async def status(self, agent_id: str = "") -> dict[str, Any]:
        """Return agent status."""
        return await self._tool("brain_status", agent_id=agent_id)

    async def health(self) -> dict[str, Any]:
        """Return brain health report."""
        return await self._tool("tapps_brain_health")
