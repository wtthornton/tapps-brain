"""Stable error taxonomy for tapps-brain public APIs (STORY-070.4).

Every error that crosses an API boundary (HTTP or MCP JSON-RPC) carries a
stable *error code* string and a documented retry policy.  Client
implementors MUST match on the ``error`` field, not on HTTP status codes
or prose messages, so that the server can refine messages without breaking
clients.

Error codes and retry semantics
--------------------------------

+-----------------------------+--------+------------+--------------------+
| Error code                  | HTTP   | JSON-RPC   | Retry policy       |
+=============================+========+============+====================+
| brain_degraded              | 503    | -32001     | retry-safe         |
| brain_rate_limited          | 429    | -32029     | retry-with-backoff |
| project_not_registered      | 403    | -32002     | retry-never        |
| invalid_request             | 400    | -32600     | retry-never        |
| idempotency_conflict        | 409    | -32009     | retry-never        |
| not_found                   | 404    | -32004     | retry-never        |
| internal_error              | 500    | -32500     | retry-safe-once    |
+-----------------------------+--------+------------+--------------------+

HTTP response shape::

    {
      "error": "<code>",
      "message": "<human readable>",
      "retry_after": <seconds>,   # optional — 429 and 503 only
      "project_id": "<id>"        # optional — project_not_registered only
    }

MCP JSON-RPC error shape::

    {
      "code": <int>,
      "message": "<human readable>",
      "data": {
        "error": "<code>",
        ...                        # optional extra fields
      }
    }
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Core enumerations
# ---------------------------------------------------------------------------


class ErrorCode(StrEnum):
    """Stable error code strings exchanged over the wire.

    Values are intentionally short, snake_case strings so they can be
    matched by downstream clients without parsing.
    """

    BRAIN_DEGRADED = "brain_degraded"
    BRAIN_RATE_LIMITED = "brain_rate_limited"
    PROJECT_NOT_REGISTERED = "project_not_registered"
    INVALID_REQUEST = "invalid_request"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"


class RetryPolicy(StrEnum):
    """Documented retry semantics for each :class:`ErrorCode`."""

    RETRY_SAFE = "retry-safe"
    """Transient failure — retry immediately with exponential back-off."""

    RETRY_WITH_BACKOFF = "retry-with-backoff"
    """Rate limit — retry after honouring the ``Retry-After`` header."""

    RETRY_NEVER = "retry-never"
    """Permanent client error — the request must be fixed before retrying."""

    RETRY_SAFE_ONCE = "retry-safe-once"
    """Unexpected server error — retry once; escalate if still failing."""


# ---------------------------------------------------------------------------
# Taxonomy table: ErrorCode → (http_status, jsonrpc_code, RetryPolicy)
# ---------------------------------------------------------------------------

_TAXONOMY: dict[ErrorCode, tuple[int, int, RetryPolicy]] = {
    ErrorCode.BRAIN_DEGRADED: (503, -32001, RetryPolicy.RETRY_SAFE),
    ErrorCode.BRAIN_RATE_LIMITED: (429, -32029, RetryPolicy.RETRY_WITH_BACKOFF),
    ErrorCode.PROJECT_NOT_REGISTERED: (403, -32002, RetryPolicy.RETRY_NEVER),
    ErrorCode.INVALID_REQUEST: (400, -32600, RetryPolicy.RETRY_NEVER),
    ErrorCode.IDEMPOTENCY_CONFLICT: (409, -32009, RetryPolicy.RETRY_NEVER),
    ErrorCode.NOT_FOUND: (404, -32004, RetryPolicy.RETRY_NEVER),
    ErrorCode.INTERNAL_ERROR: (500, -32500, RetryPolicy.RETRY_SAFE_ONCE),
}


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def http_status(code: ErrorCode) -> int:
    """Return the HTTP status code for *code*."""
    return _TAXONOMY[code][0]


def jsonrpc_code(code: ErrorCode) -> int:
    """Return the JSON-RPC integer error code for *code*.

    These are in the implementation-defined server-error range (-32000 to
    -32099) except for ``invalid_request`` which reuses the standard
    JSON-RPC -32600 "Invalid Request" code.
    """
    return _TAXONOMY[code][1]


def retry_policy(code: ErrorCode) -> RetryPolicy:
    """Return the :class:`RetryPolicy` for *code*."""
    return _TAXONOMY[code][2]


# ---------------------------------------------------------------------------
# Body builders
# ---------------------------------------------------------------------------


def http_body(
    code: ErrorCode,
    message: str,
    *,
    retry_after: int | None = None,
    project_id: str | None = None,
    **extra: Any,  # noqa: ANN401
) -> dict[str, Any]:
    """Build the standard HTTP JSON response body for *code*.

    Shape: ``{error, message, retry_after?, project_id?, ...extra}``

    Args:
        code: The :class:`ErrorCode` for this error.
        message: Human-readable description.
        retry_after: Seconds the client should wait before retrying.  Only
            meaningful for 429 and 503 responses.
        project_id: The offending project identifier, included when the
            error concerns a specific project.
        **extra: Additional key/value pairs merged into the body.

    Returns:
        A plain :class:`dict` ready to be serialised as JSON.
    """
    body: dict[str, Any] = {"error": code.value, "message": message}
    if retry_after is not None:
        body["retry_after"] = retry_after
    if project_id is not None:
        body["project_id"] = project_id
    body.update(extra)
    return body


def mcp_error_data(
    code: ErrorCode,
    message: str,
    **extra: Any,  # noqa: ANN401
) -> dict[str, Any]:
    """Build the ``data`` payload for an MCP JSON-RPC ``ErrorData`` object.

    Shape: ``{error: code_str, ...extra}``

    The caller is responsible for wrapping this in
    ``ErrorData(code=jsonrpc_code(code), message=message, data=<result>)``.

    Args:
        code: The :class:`ErrorCode` for this error.
        message: Human-readable description (used as the MCP error message).
        **extra: Additional fields merged into ``data`` (e.g. ``project_id``).

    Returns:
        A plain :class:`dict` for the ``data`` field of ``ErrorData``.
    """
    data: dict[str, Any] = {"error": code.value}
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# Exception classes — one per taxonomy entry
# ---------------------------------------------------------------------------


class TaxonomyError(Exception):
    """Base class for all taxonomy-bound exceptions.

    Each subclass carries a class-level ``error_code`` that determines
    the HTTP status and JSON-RPC code used by the API handler layer.
    Callers may catch :class:`TaxonomyError` generically to handle any
    taxonomy error, or a specific subclass for finer control.
    """

    error_code: ErrorCode  # set on each subclass

    def __init__(self, message: str, **details: Any) -> None:  # noqa: ANN401
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details

    def http_body(self, *, retry_after: int | None = None) -> dict[str, Any]:
        """Return the standard HTTP response body for this error."""
        return http_body(
            self.error_code,
            self.message,
            retry_after=retry_after,
            **self.details,
        )

    def mcp_data(self) -> dict[str, Any]:
        """Return the ``data`` dict for an MCP ``ErrorData`` payload."""
        return mcp_error_data(self.error_code, self.message, **self.details)

    @property
    def http_status(self) -> int:
        """HTTP status code for this error."""
        return http_status(self.error_code)

    @property
    def jsonrpc_code(self) -> int:
        """JSON-RPC integer code for this error."""
        return jsonrpc_code(self.error_code)

    @property
    def retry(self) -> RetryPolicy:
        """Retry policy for this error."""
        return retry_policy(self.error_code)


class BrainDegradedError(TaxonomyError):
    """Postgres or connection pool unavailable; retry with back-off (503)."""

    error_code = ErrorCode.BRAIN_DEGRADED


class BrainRateLimitedError(TaxonomyError):
    """Caller exceeded a rate limit; honour ``Retry-After`` header (429)."""

    error_code = ErrorCode.BRAIN_RATE_LIMITED


class ProjectNotFoundError(TaxonomyError):
    """project_id is not registered; fix before retrying (403).

    Note
    ----
    This is the *taxonomy* exception.  The legacy
    :class:`~tapps_brain.project_registry.ProjectNotRegisteredError` is
    mapped to the same HTTP/JSON-RPC codes by the handler layer.
    """

    error_code = ErrorCode.PROJECT_NOT_REGISTERED

    def __init__(self, project_id: str, message: str | None = None) -> None:
        super().__init__(
            message or f"project_id '{project_id}' is not registered",
            project_id=project_id,
        )
        self.project_id = project_id


class InvalidRequestError(TaxonomyError):
    """Caller supplied a malformed or logically invalid request (400)."""

    error_code = ErrorCode.INVALID_REQUEST


class IdempotencyConflictError(TaxonomyError):
    """A different response already exists for the idempotency key (409)."""

    error_code = ErrorCode.IDEMPOTENCY_CONFLICT


class NotFoundError(TaxonomyError):
    """The requested resource does not exist (404)."""

    error_code = ErrorCode.NOT_FOUND


class InternalError(TaxonomyError):
    """Unexpected server-side failure; retry once (500)."""

    error_code = ErrorCode.INTERNAL_ERROR


# ---------------------------------------------------------------------------
# Convenience mapping (useful for handler registration)
# ---------------------------------------------------------------------------

#: All taxonomy exception classes keyed by error code.
EXCEPTION_BY_CODE: dict[ErrorCode, type[TaxonomyError]] = {
    ErrorCode.BRAIN_DEGRADED: BrainDegradedError,
    ErrorCode.BRAIN_RATE_LIMITED: BrainRateLimitedError,
    ErrorCode.PROJECT_NOT_REGISTERED: ProjectNotFoundError,
    ErrorCode.INVALID_REQUEST: InvalidRequestError,
    ErrorCode.IDEMPOTENCY_CONFLICT: IdempotencyConflictError,
    ErrorCode.NOT_FOUND: NotFoundError,
    ErrorCode.INTERNAL_ERROR: InternalError,
}
