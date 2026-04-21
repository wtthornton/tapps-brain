"""Bearer-token authentication dependencies for the HTTP adapter (TAP-604).

Extracted from ``tapps_brain.http_adapter``.

All functions call ``get_settings()`` through the ``tapps_brain.http_adapter``
module namespace (lazy import inside function bodies) so that unit tests that
patch ``tapps_brain.http_adapter.get_settings`` continue to work correctly
without modification.
"""

from __future__ import annotations

import collections
import hmac
import os
import threading
import time

import structlog

try:
    from fastapi import HTTPException, Request
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "tapps_brain.http.auth requires the [http] extra.  "
        "Install it with:  uv sync --extra http  (or --extra all)."
    ) from exc

logger = structlog.get_logger(__name__)

_BEARER_PREFIX = "bearer "

# TAP-780: in-process sliding-window rate limiter for /admin/* endpoints.
# Configurable via env: TAPPS_BRAIN_ADMIN_RATE_LIMIT (requests, default 10)
# and TAPPS_BRAIN_ADMIN_RATE_WINDOW (seconds, default 60).
_ADMIN_RATE_LIMIT = int(os.environ.get("TAPPS_BRAIN_ADMIN_RATE_LIMIT", "10"))
_ADMIN_RATE_WINDOW = int(os.environ.get("TAPPS_BRAIN_ADMIN_RATE_WINDOW", "60"))
_admin_rate_lock = threading.Lock()
_admin_rate_buckets: dict[str, collections.deque[float]] = {}


def _check_admin_rate_limit(remote_addr: str) -> bool:
    """Sliding-window rate check for /admin/* brute-force protection (TAP-780).

    Returns ``True`` when the request is within the limit, ``False`` when the
    caller has exceeded ``TAPPS_BRAIN_ADMIN_RATE_LIMIT`` attempts in the last
    ``TAPPS_BRAIN_ADMIN_RATE_WINDOW`` seconds.
    """
    now = time.monotonic()
    cutoff = now - _ADMIN_RATE_WINDOW
    with _admin_rate_lock:
        dq = _admin_rate_buckets.setdefault(remote_addr, collections.deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _ADMIN_RATE_LIMIT:
            return False
        dq.append(now)
        return True


def _extract_bearer(request: Request) -> str | None:
    """Extract the bearer token string from the ``Authorization`` header.

    Returns:
        ``None``  — header absent.
        ``""``    — header present but malformed (not "Bearer …").
        ``str``   — the token value (may be empty string if header is "Bearer ").
    """
    header = request.headers.get("authorization") or ""
    if not header:
        return None
    if not header.lower().startswith(_BEARER_PREFIX):
        return ""
    return header[len(_BEARER_PREFIX) :].strip()


def _per_tenant_auth_enabled() -> bool:
    """Return ``True`` when ``TAPPS_BRAIN_PER_TENANT_AUTH=1`` is set."""
    return os.environ.get("TAPPS_BRAIN_PER_TENANT_AUTH", "") == "1"


def _verify_per_tenant_token(project_id: str, token: str, dsn: str) -> bool | None:
    """Check *token* against the project's stored argon2id hash.

    Returns:
        ``True``  — token verified against per-tenant hash.
        ``False`` — project has a token but *token* doesn't match.
        ``None``  — project has no per-tenant token; caller falls back to
                    the global ``TAPPS_BRAIN_AUTH_TOKEN`` check.
    """
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.project_registry import ProjectRegistry

    cm = PostgresConnectionManager(dsn)
    try:
        return ProjectRegistry(cm).verify_token(project_id, token)
    finally:
        cm.close()


def require_data_plane_auth(request: Request) -> None:
    """Dependency: data-plane bearer-token check.

    When ``TAPPS_BRAIN_PER_TENANT_AUTH=1``:
      * ``X-Project-Id`` header is **required** — 400 when missing or empty.
      * If no DSN is configured alongside the flag, fails closed with 500
        (misconfiguration) rather than falling through to the global token.
      * Verifies the bearer token against the project's argon2id hash in
        ``project_profiles.hashed_token``.
      * If the project has **no** per-tenant token configured, falls back to
        the global ``TAPPS_BRAIN_AUTH_TOKEN`` check so deployments that have
        not yet issued per-tenant tokens continue to work unchanged.
      * The global token is NOT accepted as a substitute when
        ``X-Project-Id`` is absent — that would defeat per-tenant isolation
        (TAP-626).

    When the flag is unset (default), behaves exactly as before: checks
    the global ``TAPPS_BRAIN_AUTH_TOKEN`` only.

    When the global token is also unset, requests pass through
    (not-for-production).
    """
    # Lazy import so unit tests can patch tapps_brain.http_adapter.get_settings.
    import tapps_brain.http_adapter as _http_mod

    cfg = _http_mod.get_settings()
    tok = _extract_bearer(request)

    # ---- per-tenant path (STORY-070.8) ----
    if _per_tenant_auth_enabled():
        # TAP-626: flag on but no DSN is a server misconfiguration — fail closed
        # rather than silently falling through to the global-token check (which
        # would reproduce the supertoken bypass this fix is meant to close).
        if not cfg.dsn:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "server_misconfiguration",
                    "detail": (
                        "TAPPS_BRAIN_PER_TENANT_AUTH is enabled but no database DSN is configured."
                    ),
                },
            )
        project_id = (request.headers.get("x-project-id") or "").strip()
        # TAP-626: reject instead of falling through to the global-token check.
        # Allowing the global token when X-Project-Id is absent makes it a
        # supertoken that bypasses per-tenant isolation entirely.
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "bad_request",
                    "detail": ("X-Project-Id header is required when per-tenant auth is enabled."),
                },
            )
        # project_id is now guaranteed non-empty (rejected above if empty)
        if tok is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unauthorized",
                    "detail": "Authorization header required (Bearer token).",
                },
            )
        if tok == "":
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unauthorized",
                    "detail": "Malformed Authorization header — expected 'Bearer <token>'.",
                },
            )
        # TAP-781: catch any DB / registry error and fail closed.  Without this
        # guard an unhandled exception would propagate as FastAPI 500 or, if
        # a future caller added a broad except, could silently fall through to
        # the global-token check and bypass per-tenant isolation entirely.
        try:
            result = _verify_per_tenant_token(project_id, tok, cfg.dsn)
        except Exception as exc:
            logger.error(
                "auth.per_tenant_verify_error",
                project_id=project_id,
                detail="token verification failed due to backend error; failing closed",
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "service_unavailable",
                    "detail": "Token verification temporarily unavailable.",
                },
            ) from exc
        if result is True:
            return  # authenticated by per-tenant token
        if result is False:
            # Project has a token — wrong credential → 403
            raise HTTPException(
                status_code=403,
                detail={"error": "forbidden", "detail": "Invalid token."},
            )
        # result is None → project has no per-tenant token, fall through to global check

    # ---- global token fallback ----
    if not cfg.auth_token:
        return
    if tok is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Authorization header required (Bearer token).",
            },
        )
    if tok == "":
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Malformed Authorization header — expected 'Bearer <token>'.",
            },
        )
    # TAP-544: constant-time comparison to avoid byte-by-byte timing recovery.
    if not hmac.compare_digest(tok.encode("utf-8"), cfg.auth_token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid token."},
        )


def _metrics_request_authenticated(request: Request, cfg: object) -> bool:
    """TAP-547: gate for the Prometheus ``/metrics`` endpoint.

    Return value semantics:

    * ``True``  — caller presented a valid ``TAPPS_BRAIN_METRICS_TOKEN``
      bearer; serve the full per-(project_id, agent_id) label surface.
    * ``False`` — no metrics token is configured on the server.  The
      endpoint still responds 200 but with tenant labels stripped (see
      ``_collect_metrics(redact_tenant_labels=True)``) so reachable-but-
      unprivileged callers cannot enumerate tenants.

    Raises ``HTTPException`` with:

    * 401 when a token IS configured and the bearer header is missing or
      malformed.
    * 403 when a token IS configured and the bearer does not match.
    """
    token = getattr(cfg, "metrics_token", None)
    if not token:
        return False
    tok = _extract_bearer(request)
    if tok is None or tok == "":
        raise HTTPException(
            status_code=401,
            detail={
                "error": "unauthorized",
                "detail": "Bearer token required for /metrics.",
            },
        )
    # TAP-544-style constant-time comparison: the metrics token grants
    # cross-tenant label visibility, so we avoid byte-by-byte timing
    # recovery here too.
    if not hmac.compare_digest(tok.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid metrics token."},
        )
    return True


def require_admin_auth(request: Request) -> None:
    """Dependency: ``TAPPS_BRAIN_ADMIN_TOKEN`` check for ``/admin/*``.

    When the admin token is unset, the route returns 503 — admin without a
    token would bypass the trust model (EPIC-069).

    TAP-780: applies a sliding-window rate limit per remote IP to prevent
    brute-force token guessing.  Limit defaults to 10 attempts / 60 s and is
    configurable via ``TAPPS_BRAIN_ADMIN_RATE_LIMIT`` /
    ``TAPPS_BRAIN_ADMIN_RATE_WINDOW``.
    """
    # Lazy import so unit tests can patch tapps_brain.http_adapter.get_settings.
    import tapps_brain.http_adapter as _http_mod

    cfg = _http_mod.get_settings()

    # TAP-780: rate-limit before touching token bytes to avoid timing oracle.
    remote_addr = (request.client.host if request.client else "unknown") or "unknown"
    if not _check_admin_rate_limit(remote_addr):
        logger.warning("auth.admin_rate_limited", remote_addr=remote_addr)
        raise HTTPException(
            status_code=429,
            detail={"error": "rate_limited", "detail": "Too many admin requests."},
        )

    if not cfg.admin_token:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "admin_disabled",
                "detail": "Admin routes require TAPPS_BRAIN_ADMIN_TOKEN to be set.",
            },
        )
    tok = _extract_bearer(request)
    if tok is None or tok == "":
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "detail": "Bearer token required for admin routes."},
        )
    # TAP-544: constant-time comparison protects TAPPS_BRAIN_ADMIN_TOKEN from
    # statistical timing recovery — admin routes grant cross-tenant power.
    if not hmac.compare_digest(tok.encode("utf-8"), cfg.admin_token.encode("utf-8")):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "detail": "Invalid admin token."},
        )
