# Error Taxonomy and Retry Semantics

tapps-brain uses a **stable error code vocabulary** so client circuit-breakers and retry policies can be written once, against documented codes, without parsing human-readable messages.

## Quick reference

| Error code                  | HTTP | JSON-RPC | Retry policy        | When to expect it                                    |
|-----------------------------|------|----------|---------------------|------------------------------------------------------|
| `brain_degraded`            | 503  | -32001   | retry-safe          | Postgres unavailable or connection pool exhausted    |
| `brain_rate_limited`        | 429  | -32029   | retry-with-backoff  | Caller exceeded per-project rate limit               |
| `project_not_registered`    | 403  | -32002   | retry-never         | `project_id` not in the registry                    |
| `invalid_request`           | 400  | -32600   | retry-never         | Malformed payload or logically invalid parameter     |
| `idempotency_conflict`      | 409  | -32009   | retry-never         | Different response already stored for idempotency key|
| `not_found`                 | 404  | -32004   | retry-never         | Requested resource (memory key, session) not found   |
| `internal_error`            | 500  | -32500   | retry-safe-once     | Unexpected server-side failure                       |

## Retry policy semantics

| Policy               | Meaning                                                                       |
|----------------------|-------------------------------------------------------------------------------|
| `retry-safe`         | Transient failure — retry with exponential back-off; alert if persistent      |
| `retry-with-backoff` | Rate limit — wait for the `Retry-After` header value before retrying          |
| `retry-never`        | Permanent client error — fix the request or configuration before retrying     |
| `retry-safe-once`    | Unexpected server error — retry exactly once; escalate if still failing       |

## HTTP response shape

All HTTP error responses carry a JSON body in the following shape:

```json
{
  "error": "brain_degraded",
  "message": "Postgres connection pool exhausted",
  "retry_after": 30,
  "project_id": "my-project"
}
```

Fields:

| Field         | Type   | Required | Description                                                  |
|---------------|--------|----------|--------------------------------------------------------------|
| `error`       | string | yes      | Stable error code from the taxonomy table above              |
| `message`     | string | yes      | Human-readable description (may change without notice)       |
| `retry_after` | int    | no       | Seconds to wait before retrying (429 and 503 only)           |
| `project_id`  | string | no       | Offending project identifier (`project_not_registered` only) |

For `brain_degraded` (503) and `brain_rate_limited` (429) the server also sets the
`Retry-After` HTTP header to the same value as `retry_after` in the body.

## MCP JSON-RPC error shape

MCP tool calls that fail return a JSON-RPC error object:

```json
{
  "code": -32002,
  "message": "project_not_registered",
  "data": {
    "error": "project_not_registered",
    "project_id": "unknown-project"
  }
}
```

Fields:

| Field            | Type   | Description                                          |
|------------------|--------|------------------------------------------------------|
| `code`           | int    | JSON-RPC integer code from the taxonomy table        |
| `message`        | string | Stable error code string (matches `data.error`)      |
| `data.error`     | string | Stable error code (same as HTTP `error` field)       |
| `data.*`         | any    | Optional extra context (e.g. `project_id`)           |

## Client implementation guide

### Python (httpx / requests)

```python
import httpx

def save_memory(client: httpx.Client, payload: dict) -> dict:
    for attempt in range(3):
        resp = client.post("/v1/remember", json=payload)
        body = resp.json()
        error = body.get("error", "")

        if resp.status_code < 400:
            return body
        if error == "brain_degraded":
            # retry-safe: exponential back-off
            wait = body.get("retry_after", 30) * (2 ** attempt)
            time.sleep(wait)
            continue
        if error == "brain_rate_limited":
            # retry-with-backoff: honour Retry-After
            wait = int(resp.headers.get("Retry-After", body.get("retry_after", 60)))
            time.sleep(wait)
            continue
        # retry-never codes: raise immediately
        raise BrainAPIError(error, body)
    raise BrainAPIError("brain_degraded", {"error": "brain_degraded", "message": "Giving up after retries"})
```

### Matching on `error`, not on status code

Always match on the `error` string field, not on the numeric HTTP status code.
The server may introduce new sub-codes within the same HTTP status without a
major version bump.  For example, a future `brain_quota_exceeded` could share
the 403 status with `project_not_registered` but carry a different `retry` policy.

## Implementation details

Error codes are defined in `tapps_brain.errors` as the `ErrorCode` enum.  API
layers (`http_adapter.py`, `mcp_server.py`) import the taxonomy helpers
(`http_body`, `mcp_error_data`, `jsonrpc_code`) to build consistent responses.

The `TaxonomyError` base class and its subclasses (`BrainDegradedError`,
`BrainRateLimitedError`, `ProjectNotFoundError`, `InvalidRequestError`,
`IdempotencyConflictError`, `NotFoundError`, `InternalError`) can be raised from
any service layer and will be caught and serialised automatically by the registered
exception handlers.

For the legacy `ProjectNotRegisteredError` (from `project_registry.py`) the HTTP
adapter preserves the existing wire shape (`error: project_not_registered`,
`project_id: <id>`) for backward compatibility with EPIC-069 clients.
