"""Unit tests for the client-facing exception taxonomy (STORY-071.1).

Covers:
* status_code → semantic exception class mapping
* :func:`raise_for_response` populates message / status_code / body
* existing wire-code taxonomy classes inherit from the new semantic classes
* :class:`TappsBrainClient` raises a typed exception (not a raw
  ``httpx.HTTPStatusError``) when the server returns an unrecognised error
  code or no JSON body
* network-level ``httpx.RequestError`` is translated to
  :class:`TappsBrainTransportError`
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tapps_brain.client import AsyncTappsBrainClient, TappsBrainClient
from tapps_brain.errors import (
    BrainDegradedError,
    BrainRateLimitedError,
    IdempotencyConflictError,
    InternalError,
    InvalidRequestError,
    NotFoundError,
    ProjectNotFoundError,
    TaxonomyError,
)
from tapps_brain.exceptions import (
    TappsBrainAuthError,
    TappsBrainError,
    TappsBrainNotFoundError,
    TappsBrainTransientError,
    TappsBrainTransportError,
    TappsBrainValidationError,
    exception_for_status,
    raise_for_response,
)

# ---------------------------------------------------------------------------
# exception_for_status — status → class mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, TappsBrainAuthError),
        (403, TappsBrainAuthError),
        (400, TappsBrainValidationError),
        (409, TappsBrainValidationError),
        (422, TappsBrainValidationError),
        (404, TappsBrainNotFoundError),
        (429, TappsBrainTransientError),
        (500, TappsBrainTransientError),
        (502, TappsBrainTransientError),
        (503, TappsBrainTransientError),
        (504, TappsBrainTransientError),
        # Outside the mapped ranges falls back to the base class so callers
        # can still catch TappsBrainError.
        (418, TappsBrainError),
        (200, TappsBrainError),
    ],
)
def test_exception_for_status(status: int, expected: type[TappsBrainError]) -> None:
    assert exception_for_status(status) is expected


# ---------------------------------------------------------------------------
# raise_for_response — instance shape
# ---------------------------------------------------------------------------


def test_raise_for_response_uses_body_message() -> None:
    exc = raise_for_response(404, {"message": "no such key"})
    assert isinstance(exc, TappsBrainNotFoundError)
    assert exc.message == "no such key"
    assert exc.status_code == 404
    assert exc.body == {"message": "no such key"}


def test_raise_for_response_falls_back_to_http_status_message() -> None:
    exc = raise_for_response(503, None)
    assert isinstance(exc, TappsBrainTransientError)
    assert exc.message == "HTTP 503"
    assert exc.body == {}


def test_raise_for_response_explicit_message_wins() -> None:
    exc = raise_for_response(401, {"message": "from body"}, message="explicit")
    assert exc.message == "explicit"


# ---------------------------------------------------------------------------
# Semantic catchability — existing wire-code classes must be catchable
# under the new semantic supertype.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc_factory,expected_supertype",
    [
        (lambda: BrainDegradedError("db down"), TappsBrainTransientError),
        (lambda: BrainRateLimitedError("slow"), TappsBrainTransientError),
        (lambda: InternalError("oops"), TappsBrainTransientError),
        (lambda: ProjectNotFoundError("p1", "unknown"), TappsBrainAuthError),
        (lambda: InvalidRequestError("bad"), TappsBrainValidationError),
        (lambda: IdempotencyConflictError("dup"), TappsBrainValidationError),
        (lambda: NotFoundError("nope"), TappsBrainNotFoundError),
    ],
)
def test_taxonomy_class_inherits_semantic_supertype(
    exc_factory: Any, expected_supertype: type[TappsBrainError]
) -> None:
    exc = exc_factory()
    # Caught under both the wire-code base AND the new semantic supertype.
    assert isinstance(exc, TaxonomyError)
    assert isinstance(exc, expected_supertype)
    assert isinstance(exc, TappsBrainError)


def test_taxonomy_class_carries_status_code_attribute() -> None:
    """STORY-071.1: taxonomy instances expose the SDK status_code attribute."""
    assert BrainDegradedError("x").status_code == 503
    assert ProjectNotFoundError("p1").status_code == 403
    assert NotFoundError("x").status_code == 404
    assert IdempotencyConflictError("x").status_code == 409


# ---------------------------------------------------------------------------
# Client: unrecognised wire codes raise typed TappsBrain* errors, not httpx.
# ---------------------------------------------------------------------------


def _make_sync_client(**kwargs: Any) -> TappsBrainClient:
    with patch("tapps_brain.client.TappsBrainClient._init_http"):
        client = TappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    client._http_client = MagicMock()
    client._initialized = True
    return client


def _error_response(status: int, body: dict[str, Any] | None) -> MagicMock:
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = status
    resp.json.return_value = body if body is not None else {}
    return resp


@pytest.mark.parametrize(
    "status,expected",
    [
        (401, TappsBrainAuthError),
        (404, TappsBrainNotFoundError),
        (422, TappsBrainValidationError),
        (502, TappsBrainTransientError),
    ],
)
def test_client_unrecognised_status_raises_typed_error(
    status: int, expected: type[TappsBrainError]
) -> None:
    """Server returns an HTTP status with no recognised wire-code; the SDK
    must classify it into the TappsBrain* hierarchy, not surface raw httpx."""
    client = _make_sync_client(max_retries=0)
    client._http_client.post.return_value = _error_response(status, {"detail": "boom"})

    with pytest.raises(expected) as exc_info:
        client.remember("x")

    exc = exc_info.value
    assert isinstance(exc, TappsBrainError)
    assert exc.status_code == status


def test_client_empty_error_body_still_typed() -> None:
    """No JSON body on the response — must still raise a typed exception."""
    client = _make_sync_client(max_retries=0)
    resp = MagicMock()
    resp.is_success = False
    resp.status_code = 401
    resp.json.side_effect = ValueError("no body")
    client._http_client.post.return_value = resp

    with pytest.raises(TappsBrainAuthError):
        client.remember("x")


def test_client_transport_error_wraps_httpx_connect_error() -> None:
    """Network failure (httpx.ConnectError) must surface as TappsBrainTransportError."""
    client = _make_sync_client(max_retries=0)
    client._http_client.post.side_effect = httpx.ConnectError(
        "connection refused", request=httpx.Request("POST", "http://brain:8080/mcp/")
    )

    with pytest.raises(TappsBrainTransportError) as exc_info:
        client.recall("q")

    assert "ConnectError" in exc_info.value.message
    # Underlying httpx exception preserved via __cause__
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


def test_client_transport_error_wraps_httpx_timeout() -> None:
    """Timeouts are httpx.RequestError subclasses and must be wrapped too."""
    client = _make_sync_client(max_retries=0)
    client._http_client.post.side_effect = httpx.ReadTimeout(
        "slow", request=httpx.Request("POST", "http://brain:8080/mcp/")
    )

    with pytest.raises(TappsBrainTransportError):
        client.recall("q")


# ---------------------------------------------------------------------------
# Async client — equivalent transport/typing behaviour.
# ---------------------------------------------------------------------------


def _make_async_client(**kwargs: Any) -> AsyncTappsBrainClient:
    from unittest.mock import AsyncMock

    client = AsyncTappsBrainClient("http://brain:8080", project_id="p1", agent_id="a1", **kwargs)
    client._http_client = AsyncMock()
    client._initialized = True
    return client


@pytest.mark.asyncio
async def test_async_client_unrecognised_status_raises_typed_error() -> None:
    from unittest.mock import AsyncMock

    client = _make_async_client(max_retries=0)
    resp = AsyncMock()
    resp.is_success = False
    resp.status_code = 422
    resp.json = MagicMock(return_value={"detail": "schema fail"})
    client._http_client.post.return_value = resp

    with pytest.raises(TappsBrainValidationError) as exc_info:
        await client.remember("x")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_async_client_transport_error_wraps_httpx_connect_error() -> None:
    client = _make_async_client(max_retries=0)
    client._http_client.post.side_effect = httpx.ConnectError(
        "down", request=httpx.Request("POST", "http://brain:8080/mcp/")
    )

    with pytest.raises(TappsBrainTransportError):
        await client.recall("q")


# ---------------------------------------------------------------------------
# Package-level re-exports
# ---------------------------------------------------------------------------


def test_package_exports_semantic_taxonomy() -> None:
    import tapps_brain

    for name in (
        "TappsBrainError",
        "TappsBrainAuthError",
        "TappsBrainTransientError",
        "TappsBrainNotFoundError",
        "TappsBrainValidationError",
        "TappsBrainTransportError",
    ):
        assert hasattr(tapps_brain, name), f"{name} not re-exported from tapps_brain"
        assert name in tapps_brain.__all__, f"{name} missing from __all__"
