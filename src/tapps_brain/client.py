"""Official TappsBrainClient — sync and async (STORY-070.11).

Provides a unified, typed client that mirrors the :class:`AgentBrain` method
signatures and dispatches to one of two transports:

* ``http://`` / ``https://`` — HTTP adapter REST endpoints (STORY-070.3).
* ``mcp+http://`` — MCP Streamable HTTP transport via ``/mcp`` (STORY-070.1).

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
import os
import time
import uuid
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol — the shared public surface
# ---------------------------------------------------------------------------


@runtime_checkable
class BrainClientProtocol(Protocol):
    """Minimal protocol that both sync and async clients satisfy."""

    def remember(self, fact: str, *, tier: str = "procedural", share: bool = False) -> str: ...
    def recall(self, query: str, *, max_results: int = 5) -> list[dict[str, Any]]: ...
    def forget(self, key: str) -> bool: ...
    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# URL scheme detection
# ---------------------------------------------------------------------------

_SCHEME_HTTP = ("http://", "https://")
_SCHEME_MCP_HTTP = "mcp+http://"


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
    max_retries: int = 2,
) -> Any:
    """POST to /v1/tools/{tool_name} with error parsing and transparent retry.

    On a retryable response (429 / 503), the same *idempotency_key* is reused
    so duplicate writes are prevented.  The ``Retry-After`` response header is
    honoured when present.

    Raises the appropriate :class:`~tapps_brain.errors.TaxonomyError` subclass
    for known error codes; falls back to ``httpx.HTTPStatusError`` for
    unrecognised status codes.
    """
    from tapps_brain.errors import RetryPolicy

    headers = _build_headers(project_id, agent_id, auth_token, idempotency_key=idempotency_key)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        resp = client.post(
            f"{base}/v1/tools/{tool_name}",
            headers=headers,
            content=json.dumps(arguments).encode(),
        )
        if resp.is_success:
            return resp.json()

        # Parse structured error body
        try:
            body: dict[str, Any] = resp.json()
        except Exception:
            body = {}

        exc = _parse_error_response(resp.status_code, body)
        if exc is not None:
            from tapps_brain.errors import TaxonomyError

            _retryable = (
                RetryPolicy.RETRY_SAFE,
                RetryPolicy.RETRY_WITH_BACKOFF,
                RetryPolicy.RETRY_SAFE_ONCE,
            )
            if isinstance(exc, TaxonomyError) and exc.retry in _retryable and attempt < max_retries:
                retry_after = float(body.get("retry_after") or 2**attempt)
                time.sleep(retry_after)
                last_exc = exc
                continue
            raise exc

        # Fallback — raise the underlying HTTP error
        resp.raise_for_status()

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"POST {tool_name}: unexpected state after {max_retries + 1} attempts")


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
    max_retries: int = 2,
) -> Any:
    """Async version of :func:`_post_tool`."""
    from tapps_brain.errors import RetryPolicy

    headers = _build_headers(project_id, agent_id, auth_token, idempotency_key=idempotency_key)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        resp = await client.post(
            f"{base}/v1/tools/{tool_name}",
            headers=headers,
            content=json.dumps(arguments).encode(),
        )
        if resp.is_success:
            return resp.json()

        try:
            body: dict[str, Any] = resp.json()
        except Exception:
            body = {}

        exc = _parse_error_response(resp.status_code, body)
        if exc is not None:
            from tapps_brain.errors import TaxonomyError

            _retryable = (
                RetryPolicy.RETRY_SAFE,
                RetryPolicy.RETRY_WITH_BACKOFF,
                RetryPolicy.RETRY_SAFE_ONCE,
            )
            if isinstance(exc, TaxonomyError) and exc.retry in _retryable and attempt < max_retries:
                retry_after = float(body.get("retry_after") or 2**attempt)
                await asyncio.sleep(retry_after)
                last_exc = exc
                continue
            raise exc

        resp.raise_for_status()

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"POST {tool_name}: unexpected state after {max_retries + 1} attempts")


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------


class TappsBrainClient:
    """Synchronous tapps-brain client.

    Supports two transports selected by *url* scheme:

    * ``http://`` / ``https://`` — :mod:`httpx` HTTP calls to the HTTP adapter
      REST endpoints.
    * ``mcp+http://`` — MCP Streamable HTTP transport; sends MCP JSON-RPC
      requests to the ``/mcp`` endpoint of the brain HTTP adapter.

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
        HTTP timeout in seconds (default 30).
    max_retries:
        Maximum retry attempts for transient failures (default 2).
    """

    def __init__(
        self,
        url: str = "http://localhost:8080",
        *,
        project_id: str | None = None,
        agent_id: str | None = None,
        auth_token: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._url = url
        self._scheme = _detect_scheme(url)
        self._project_id = project_id or os.environ.get("TAPPS_BRAIN_PROJECT", "default")
        self._agent_id = agent_id or os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")
        self._auth_token = auth_token or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client: Any = None
        self._closed = False

        if self._scheme in ("http", "mcp+http"):
            self._init_http()

    def _init_http(self) -> None:
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "TappsBrainClient requires httpx. Install it with: pip install httpx"
            ) from exc
        self._http_client = httpx.Client(timeout=self._timeout)

    def _tool(self, name: str, **kwargs: Any) -> Any:
        """Call a tool via the active transport.

        For write tools, an idempotency key is automatically generated and
        passed through the transport-specific mechanism.
        """
        ikey: str | None = None
        if name in _WRITE_TOOLS:
            ikey = str(uuid.uuid4())

        if self._scheme == "http":
            return self._http_tool(name, kwargs, idempotency_key=ikey)
        else:
            return self._mcp_http_tool(name, kwargs, idempotency_key=ikey)

    def _http_tool(
        self, name: str, arguments: dict[str, Any], *, idempotency_key: str | None = None
    ) -> Any:
        """POST to HTTP adapter /v1/tools/{name}."""
        return _post_tool(
            self._http_client,
            _http_base(self._url),
            name,
            arguments,
            self._project_id,
            self._agent_id,
            self._auth_token,
            idempotency_key=idempotency_key,
            max_retries=self._max_retries,
        )

    def _mcp_http_tool(
        self, name: str, arguments: dict[str, Any], *, idempotency_key: str | None = None
    ) -> Any:
        """Send a tools/call request to the MCP streamable-HTTP endpoint."""
        base = _http_base(self._url.replace("mcp+http://", "http://", 1))
        headers = _build_headers(
            self._project_id, self._agent_id, self._auth_token, idempotency_key=idempotency_key
        )
        meta: dict[str, Any] = {"project_id": self._project_id, "agent_id": self._agent_id}
        if idempotency_key:
            meta["idempotency_key"] = idempotency_key
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": meta},
            "id": 1,
        }
        resp = self._http_client.post(
            f"{base}/mcp",
            headers=headers,
            content=json.dumps(payload).encode(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "result" in data:
            content = data["result"].get("content", [])
            if content and isinstance(content, list):
                raw = content[0].get("text", "{}")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        return data

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
        agent_id: str = "",
    ) -> str:
        """Save a memory. Returns the generated key."""
        result = self._tool(
            "brain_remember",
            fact=fact,
            tier=tier,
            share=share,
            share_with=share_with,
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

    def learn_success(self, task_description: str, *, task_id: str = "", agent_id: str = "") -> str:
        """Record a successful task outcome."""
        result = self._tool(
            "brain_learn_success",
            task_description=task_description,
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

    def memory_recall(self, message: str, **kwargs: Any) -> dict[str, Any]:
        """Run auto-recall for a message."""
        return self._tool("memory_recall", message=message, **kwargs)

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
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        self._url = url
        self._scheme = _detect_scheme(url)
        self._project_id = project_id or os.environ.get("TAPPS_BRAIN_PROJECT", "default")
        self._agent_id = agent_id or os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")
        self._auth_token = auth_token or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        self._timeout = timeout
        self._max_retries = max_retries
        self._http_client: Any = None
        self._closed = False

    async def _ensure_client(self) -> None:
        """Lazily initialise the httpx.AsyncClient."""
        if self._http_client is None:
            try:
                import httpx
            except ImportError as exc:
                raise ImportError(
                    "AsyncTappsBrainClient requires httpx. Install it with: pip install httpx"
                ) from exc
            self._http_client = httpx.AsyncClient(timeout=self._timeout)

    async def _tool(self, name: str, **kwargs: Any) -> Any:
        """Call a tool via the active transport (async).

        Write tools automatically get an idempotency key.
        """
        await self._ensure_client()

        ikey: str | None = None
        if name in _WRITE_TOOLS:
            ikey = str(uuid.uuid4())

        if self._scheme == "http":
            return await self._http_tool(name, kwargs, idempotency_key=ikey)
        else:
            return await self._mcp_http_tool(name, kwargs, idempotency_key=ikey)

    async def _http_tool(
        self, name: str, arguments: dict[str, Any], *, idempotency_key: str | None = None
    ) -> Any:
        return await _async_post_tool(
            self._http_client,
            _http_base(self._url),
            name,
            arguments,
            self._project_id,
            self._agent_id,
            self._auth_token,
            idempotency_key=idempotency_key,
            max_retries=self._max_retries,
        )

    async def _mcp_http_tool(
        self, name: str, arguments: dict[str, Any], *, idempotency_key: str | None = None
    ) -> Any:
        base = _http_base(self._url.replace("mcp+http://", "http://", 1))
        headers = _build_headers(
            self._project_id, self._agent_id, self._auth_token, idempotency_key=idempotency_key
        )
        meta: dict[str, Any] = {"project_id": self._project_id, "agent_id": self._agent_id}
        if idempotency_key:
            meta["idempotency_key"] = idempotency_key
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": meta},
            "id": 1,
        }
        resp = await self._http_client.post(
            f"{base}/mcp",
            headers=headers,
            content=json.dumps(payload).encode(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "result" in data:
            content = data["result"].get("content", [])
            if content and isinstance(content, list):
                raw = content[0].get("text", "{}")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
        return data

    # --- Context manager ---

    async def __aenter__(self) -> AsyncTappsBrainClient:
        await self._ensure_client()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._closed:
            return
        self._closed = True
        if self._http_client is not None:
            await self._http_client.aclose()

    # --- AgentBrain-compatible async API ---

    async def remember(
        self,
        fact: str,
        *,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
        agent_id: str = "",
    ) -> str:
        """Save a memory. Returns the generated key."""
        result = await self._tool(
            "brain_remember",
            fact=fact,
            tier=tier,
            share=share,
            share_with=share_with,
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
        self, task_description: str, *, task_id: str = "", agent_id: str = ""
    ) -> str:
        """Record a successful task outcome."""
        result = await self._tool(
            "brain_learn_success",
            task_description=task_description,
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

    async def memory_recall(self, message: str, **kwargs: Any) -> dict[str, Any]:
        """Run auto-recall for a message."""
        return await self._tool("memory_recall", message=message, **kwargs)

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
