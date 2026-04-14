"""Unit tests for the error taxonomy module (STORY-070.4).

Verifies that every exception type maps to its documented HTTP status,
JSON-RPC code, and retry policy, and that the body-builder helpers
produce the expected wire shapes.
"""

from __future__ import annotations

import pytest

from tapps_brain.errors import (
    EXCEPTION_BY_CODE,
    BrainDegradedError,
    BrainRateLimitedError,
    ErrorCode,
    IdempotencyConflictError,
    InternalError,
    InvalidRequestError,
    NotFoundError,
    ProjectNotFoundError,
    RetryPolicy,
    TaxonomyError,
    http_body,
    http_status,
    jsonrpc_code,
    mcp_error_data,
    retry_policy,
)

# ---------------------------------------------------------------------------
# AC1 - AC7: documented taxonomy values
# ---------------------------------------------------------------------------


class TestTaxonomyValues:
    """Verify HTTP status, JSON-RPC code, and retry policy for every entry."""

    def test_ac1_brain_degraded_503_retry_safe(self) -> None:
        assert http_status(ErrorCode.BRAIN_DEGRADED) == 503
        assert retry_policy(ErrorCode.BRAIN_DEGRADED) == RetryPolicy.RETRY_SAFE

    def test_ac2_brain_rate_limited_429_retry_with_backoff(self) -> None:
        assert http_status(ErrorCode.BRAIN_RATE_LIMITED) == 429
        assert retry_policy(ErrorCode.BRAIN_RATE_LIMITED) == RetryPolicy.RETRY_WITH_BACKOFF

    def test_ac3_project_not_registered_403_retry_never(self) -> None:
        assert http_status(ErrorCode.PROJECT_NOT_REGISTERED) == 403
        assert retry_policy(ErrorCode.PROJECT_NOT_REGISTERED) == RetryPolicy.RETRY_NEVER

    def test_ac4_invalid_request_400_retry_never(self) -> None:
        assert http_status(ErrorCode.INVALID_REQUEST) == 400
        assert retry_policy(ErrorCode.INVALID_REQUEST) == RetryPolicy.RETRY_NEVER

    def test_ac5_idempotency_conflict_409_retry_never(self) -> None:
        assert http_status(ErrorCode.IDEMPOTENCY_CONFLICT) == 409
        assert retry_policy(ErrorCode.IDEMPOTENCY_CONFLICT) == RetryPolicy.RETRY_NEVER

    def test_ac6_not_found_404_retry_never(self) -> None:
        assert http_status(ErrorCode.NOT_FOUND) == 404
        assert retry_policy(ErrorCode.NOT_FOUND) == RetryPolicy.RETRY_NEVER

    def test_ac7_internal_error_500_retry_safe_once(self) -> None:
        assert http_status(ErrorCode.INTERNAL_ERROR) == 500
        assert retry_policy(ErrorCode.INTERNAL_ERROR) == RetryPolicy.RETRY_SAFE_ONCE


# ---------------------------------------------------------------------------
# AC8 - AC11: HTTP response body shape
# ---------------------------------------------------------------------------


class TestHttpBody:
    """http_body() produces {error, message, retry_after?, project_id?, ...}."""

    def test_ac8_error_code_field(self) -> None:
        body = http_body(ErrorCode.BRAIN_DEGRADED, "pool exhausted")
        assert body["error"] == "brain_degraded"

    def test_ac9_message_str(self) -> None:
        body = http_body(ErrorCode.INVALID_REQUEST, "missing key")
        assert body["message"] == "missing key"

    def test_ac10_retry_after_int(self) -> None:
        body = http_body(ErrorCode.BRAIN_DEGRADED, "down", retry_after=30)
        assert body["retry_after"] == 30

    def test_retry_after_omitted_when_none(self) -> None:
        body = http_body(ErrorCode.BRAIN_DEGRADED, "down")
        assert "retry_after" not in body

    def test_ac11_project_id_optional(self) -> None:
        body = http_body(
            ErrorCode.PROJECT_NOT_REGISTERED,
            "not found",
            project_id="my-proj",
        )
        assert body["project_id"] == "my-proj"

    def test_project_id_omitted_when_none(self) -> None:
        body = http_body(ErrorCode.PROJECT_NOT_REGISTERED, "not found")
        assert "project_id" not in body

    def test_extra_fields_merged(self) -> None:
        body = http_body(ErrorCode.INTERNAL_ERROR, "oops", foo="bar")
        assert body["foo"] == "bar"


# ---------------------------------------------------------------------------
# AC12 - AC15: MCP JSON-RPC error shape
# ---------------------------------------------------------------------------


class TestMcpErrorData:
    """mcp_error_data() and jsonrpc_code() produce the documented shape."""

    def test_ac12_jsonrpc_code_int(self) -> None:
        code = jsonrpc_code(ErrorCode.PROJECT_NOT_REGISTERED)
        assert isinstance(code, int)
        assert code == -32002  # backward compat with EPIC-069

    def test_ac13_message_via_errorcode(self) -> None:
        # The caller uses ErrorCode.value as the message
        assert ErrorCode.BRAIN_DEGRADED.value == "brain_degraded"

    def test_ac14_data_error_field(self) -> None:
        data = mcp_error_data(ErrorCode.BRAIN_DEGRADED, "pool exhausted")
        assert data["error"] == "brain_degraded"

    def test_ac15_data_extra_fields(self) -> None:
        data = mcp_error_data(
            ErrorCode.PROJECT_NOT_REGISTERED,
            "not registered",
            project_id="proj-x",
        )
        assert data["project_id"] == "proj-x"
        assert data["error"] == "project_not_registered"

    def test_jsonrpc_codes_all_defined(self) -> None:
        for code in ErrorCode:
            assert isinstance(jsonrpc_code(code), int)


# ---------------------------------------------------------------------------
# AC16: EPIC-069 backward compat
# ---------------------------------------------------------------------------


class TestEpic069BackwardCompat:
    """EPIC-069 existing 403/-32002 codes preserved without breaking shape."""

    def test_project_not_registered_http_status_is_403(self) -> None:
        assert http_status(ErrorCode.PROJECT_NOT_REGISTERED) == 403

    def test_project_not_registered_jsonrpc_code_is_32002(self) -> None:
        # -32002 is the code used by EPIC-069; must remain unchanged.
        assert jsonrpc_code(ErrorCode.PROJECT_NOT_REGISTERED) == -32002

    def test_project_not_registered_body_shape(self) -> None:
        body = http_body(
            ErrorCode.PROJECT_NOT_REGISTERED,
            "not registered",
            project_id="old-project",
        )
        assert body["error"] == "project_not_registered"
        assert body["project_id"] == "old-project"

    def test_mcp_data_project_id_field(self) -> None:
        data = mcp_error_data(
            ErrorCode.PROJECT_NOT_REGISTERED,
            "project_not_registered",
            project_id="old-project",
        )
        assert data["project_id"] == "old-project"


# ---------------------------------------------------------------------------
# AC18: every exception type maps to its documented code
# ---------------------------------------------------------------------------


class TestExceptionClasses:
    """Each TaxonomyError subclass carries the right error_code and properties."""

    @pytest.mark.parametrize(
        ("exc_cls", "code", "http", "jrpc", "policy"),
        [
            (BrainDegradedError, ErrorCode.BRAIN_DEGRADED, 503, -32001,
             RetryPolicy.RETRY_SAFE),
            (BrainRateLimitedError, ErrorCode.BRAIN_RATE_LIMITED, 429, -32029,
             RetryPolicy.RETRY_WITH_BACKOFF),
            (ProjectNotFoundError, ErrorCode.PROJECT_NOT_REGISTERED, 403, -32002,
             RetryPolicy.RETRY_NEVER),
            (InvalidRequestError, ErrorCode.INVALID_REQUEST, 400, -32600,
             RetryPolicy.RETRY_NEVER),
            (IdempotencyConflictError, ErrorCode.IDEMPOTENCY_CONFLICT, 409, -32009,
             RetryPolicy.RETRY_NEVER),
            (NotFoundError, ErrorCode.NOT_FOUND, 404, -32004,
             RetryPolicy.RETRY_NEVER),
            (InternalError, ErrorCode.INTERNAL_ERROR, 500, -32500,
             RetryPolicy.RETRY_SAFE_ONCE),
        ],
    )
    def test_exception_maps_to_documented_code(
        self,
        exc_cls: type[TaxonomyError],
        code: ErrorCode,
        http: int,
        jrpc: int,
        policy: RetryPolicy,
    ) -> None:
        exc = exc_cls("test-proj") if exc_cls is ProjectNotFoundError else exc_cls("test message")

        assert exc.error_code == code
        assert exc.http_status == http
        assert exc.jsonrpc_code == jrpc
        assert exc.retry == policy

    def test_taxonomy_error_is_base(self) -> None:
        exc = BrainDegradedError("down")
        assert isinstance(exc, TaxonomyError)
        assert isinstance(exc, Exception)

    def test_project_not_found_project_id(self) -> None:
        exc = ProjectNotFoundError("my-proj")
        assert exc.project_id == "my-proj"
        assert "my-proj" in exc.details.get("project_id", "")

    def test_http_body_on_instance(self) -> None:
        exc = BrainDegradedError("pool exhausted")
        body = exc.http_body(retry_after=30)
        assert body["error"] == "brain_degraded"
        assert body["message"] == "pool exhausted"
        assert body["retry_after"] == 30

    def test_mcp_data_on_instance(self) -> None:
        exc = ProjectNotFoundError("proj-x")
        data = exc.mcp_data()
        assert data["error"] == "project_not_registered"
        assert data["project_id"] == "proj-x"

    def test_exception_by_code_mapping_complete(self) -> None:
        """EXCEPTION_BY_CODE must cover every ErrorCode value."""
        for code in ErrorCode:
            assert code in EXCEPTION_BY_CODE, f"Missing: {code}"
            exc_cls = EXCEPTION_BY_CODE[code]
            assert issubclass(exc_cls, TaxonomyError)


# ---------------------------------------------------------------------------
# Retry policy string values
# ---------------------------------------------------------------------------


class TestRetryPolicyValues:
    def test_retry_safe_value(self) -> None:
        assert RetryPolicy.RETRY_SAFE == "retry-safe"

    def test_retry_with_backoff_value(self) -> None:
        assert RetryPolicy.RETRY_WITH_BACKOFF == "retry-with-backoff"

    def test_retry_never_value(self) -> None:
        assert RetryPolicy.RETRY_NEVER == "retry-never"

    def test_retry_safe_once_value(self) -> None:
        assert RetryPolicy.RETRY_SAFE_ONCE == "retry-safe-once"
