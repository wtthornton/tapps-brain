"""Client-facing exception taxonomy for the tapps-brain SDK (STORY-071.1).

This module provides the **semantic** exception hierarchy that integrators
catch in their own code:

* :class:`TappsBrainError` — root; catch this to handle any SDK error.
* :class:`TappsBrainTransportError` — network/connection failure before the
  request reached the brain (DNS, TCP, TLS, timeout).
* :class:`TappsBrainAuthError` — 401 / 403; missing or invalid bearer token,
  or the project_id is not registered.
* :class:`TappsBrainTransientError` — 429 / 5xx; the server failed but the
  request is safe to retry (the SDK's retry layer already handles this when
  ``retry_config`` is enabled).
* :class:`TappsBrainNotFoundError` — 404; the resource does not exist.
* :class:`TappsBrainValidationError` — 400 / 409 / 422; the request body
  was malformed, conflicted with server state, or failed schema validation.

The server-side wire-code taxonomy in :mod:`tapps_brain.errors`
(``BrainDegradedError`` / ``BrainRateLimitedError`` / ``ProjectNotFoundError``
/ ``InvalidRequestError`` / ``IdempotencyConflictError`` / ``NotFoundError``
/ ``InternalError``) is layered on top of this hierarchy via multiple
inheritance, so existing ``except BrainDegradedError`` blocks continue to
work and new code can catch the broader semantic supertype.

Catch by intent
---------------

::

    from tapps_brain import (
        AsyncTappsBrainClient,
        TappsBrainAuthError,
        TappsBrainTransientError,
        TappsBrainTransportError,
    )

    async with AsyncTappsBrainClient(url, auth_token=tok) as brain:
        try:
            await brain.remember("...")
        except TappsBrainAuthError:
            # Fix the bearer token / project registration and stop.
            raise
        except TappsBrainTransientError:
            # The SDK's retry layer already gave up — escalate to a human.
            log.warning("brain transiently unavailable")
        except TappsBrainTransportError:
            # Network problem — degrade gracefully.
            log.warning("brain unreachable")
"""

from __future__ import annotations

from typing import Any


class TappsBrainError(Exception):
    """Root of the tapps-brain SDK exception hierarchy.

    All errors raised by :class:`~tapps_brain.client.TappsBrainClient` and
    :class:`~tapps_brain.client.AsyncTappsBrainClient` are subclasses of this.
    Catch this to handle anything that might come out of the SDK without
    pattern-matching on more specific subclasses.

    Attributes
    ----------
    message:
        Human-readable description.
    status_code:
        HTTP status code returned by the brain, or ``None`` for transport
        failures that happened before a response was received.
    body:
        Parsed response body if the server returned JSON, else an empty dict.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body: dict[str, Any] = body or {}


class TappsBrainTransportError(TappsBrainError):
    """The request never reached the brain.

    Wraps :class:`httpx.RequestError` (DNS failure, TCP refused, TLS error,
    connect/read timeout, etc.).  Retrying may succeed if the network
    recovers, but the SDK does not auto-retry transport errors by default
    because they often indicate a misconfigured URL or a downed brain.
    """


class TappsBrainAuthError(TappsBrainError):
    """HTTP 401 / 403 — auth or authorization failure.

    The bearer token is missing/invalid, or the ``project_id`` header
    refers to a project the brain has not registered.  Permanent until
    the caller fixes the credential or registers the project.
    """


class TappsBrainTransientError(TappsBrainError):
    """HTTP 429 / 5xx — the server failed but the request is safe to retry.

    The SDK's ``RetryConfig`` layer (STORY-071.2) retries this class
    automatically when enabled; this exception only escapes when the retry
    budget is exhausted.
    """


class TappsBrainNotFoundError(TappsBrainError):
    """HTTP 404 — the requested resource does not exist."""


class TappsBrainValidationError(TappsBrainError):
    """HTTP 400 / 409 / 422 — the request was rejected as malformed.

    Covers schema validation failures (``invalid_request``), idempotency
    conflicts (the server already saw a different body for this key), and
    422 Unprocessable Entity responses from FastAPI-style validators.
    """


# ---------------------------------------------------------------------------
# Status-code → exception class mapping
# ---------------------------------------------------------------------------

_AUTH_STATUSES = frozenset({401, 403})
_VALIDATION_STATUSES = frozenset({400, 409, 422})
_NOT_FOUND_STATUS = 404
_RATE_LIMITED_STATUS = 429
_SERVER_ERROR_MIN = 500
_SERVER_ERROR_MAX = 600


def exception_for_status(status_code: int) -> type[TappsBrainError]:
    """Return the :class:`TappsBrainError` subclass appropriate for *status_code*.

    Used by the HTTP client when the server returned a status with no
    recognised wire-format error code in the body.  Callers that have a
    parsed body should prefer :func:`raise_for_response` instead.
    """
    if status_code in _AUTH_STATUSES:
        return TappsBrainAuthError
    if status_code == _NOT_FOUND_STATUS:
        return TappsBrainNotFoundError
    if status_code in _VALIDATION_STATUSES:
        return TappsBrainValidationError
    if status_code == _RATE_LIMITED_STATUS or _SERVER_ERROR_MIN <= status_code < _SERVER_ERROR_MAX:
        return TappsBrainTransientError
    return TappsBrainError


def raise_for_response(
    status_code: int,
    body: dict[str, Any] | None = None,
    *,
    message: str = "",
) -> TappsBrainError:
    """Build (but do not raise) the right :class:`TappsBrainError` for a response.

    The caller is responsible for raising — separating construction from
    raising lets the retry layer inspect the type without losing the
    traceback frame.

    Args:
        status_code: HTTP status code from the response.
        body: Parsed JSON body, or ``None`` if the body was not JSON.
        message: Human-readable description; falls back to the body's
            ``"message"`` field, then ``"HTTP <status_code>"``.
    """
    body = body or {}
    if not message:
        message = str(body.get("message") or f"HTTP {status_code}")
    return exception_for_status(status_code)(
        message,
        status_code=status_code,
        body=body,
    )


__all__ = [
    "TappsBrainAuthError",
    "TappsBrainError",
    "TappsBrainNotFoundError",
    "TappsBrainTransientError",
    "TappsBrainTransportError",
    "TappsBrainValidationError",
    "exception_for_status",
    "raise_for_response",
]
