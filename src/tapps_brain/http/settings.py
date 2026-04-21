"""Process-wide settings resolved from environment (TAP-604).

Extracted from ``tapps_brain.http_adapter`` to its own module.
``tapps_brain.http_adapter`` re-exports all public names for backward compat.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _service_version() -> str:
    """Return the installed package version or ``"unknown"``."""
    try:
        from importlib.metadata import version

        return version("tapps-brain")
    except Exception:
        return "unknown"


def _filter_snapshot_by_project(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
    """STORY-069.7: filter diagnostics/feedback to a single project_id."""
    filtered = dict(payload)
    for key in ("diagnostics_history", "feedback_events"):
        rows = filtered.get(key) or []
        filtered[key] = [
            row for row in rows if isinstance(row, dict) and row.get("project_id") == project_id
        ]
    return filtered


# ---------------------------------------------------------------------------
# Settings class
# ---------------------------------------------------------------------------


class _Settings:
    """Process-wide configuration resolved from env at app startup."""

    def __init__(self) -> None:
        self.dsn = self._resolve_dsn()
        self.auth_token = self._resolve_auth_token()
        self.admin_token = self._resolve_admin_token()
        # TAP-547: optional bearer token gating /metrics.  When set, the
        # endpoint serves the full per-(project_id, agent_id) counter
        # surface only to callers presenting the correct token; anonymous
        # callers receive a redacted (tenant-label-stripped) body.  When
        # unset, we still serve the redacted body so anonymous scrapes
        # can't enumerate tenants.
        self.metrics_token = self._resolve_metrics_token()
        self.allowed_origins = self._resolve_allowed_origins()
        self.version = _service_version()
        # Optional store injected by the CLI entry point / tests.
        self.store: MemoryStore | None = None
        # Snapshot cache
        self.snapshot_lock = threading.Lock()
        self.snapshot_cache: Any = None
        self.snapshot_cache_at: float = 0.0
        # TAP-548: process-wide ``IdempotencyStore`` singleton, built in
        # the FastAPI lifespan startup hook when
        # ``TAPPS_BRAIN_IDEMPOTENCY=1`` and a DSN is configured, and
        # closed on shutdown.  Re-using one store reuses one
        # ``PostgresConnectionManager`` pool instead of opening a fresh
        # psycopg connection per write — the previous per-request
        # construction bypassed the hardened pool and raced
        # ``max_connections`` under load.
        self.idempotency_store: Any = None

    @staticmethod
    def _resolve_dsn() -> str | None:
        dsn = (
            os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            or ""
        ).strip()
        return dsn or None

    @staticmethod
    def _read_secret(env_name: str, file_env_name: str) -> str | None:
        tok = os.environ.get(env_name, "").strip()
        if tok:
            return tok
        file_ = os.environ.get(file_env_name, "").strip()
        if file_:
            try:
                return Path(file_).read_text().strip() or None
            except OSError:
                return None
        return None

    @classmethod
    def _resolve_auth_token(cls) -> str | None:
        # STORY-070.3: accept either new (TAPPS_BRAIN_AUTH_TOKEN) or legacy
        # (TAPPS_BRAIN_HTTP_AUTH_TOKEN) name for the data-plane token.
        return cls._read_secret(
            "TAPPS_BRAIN_AUTH_TOKEN", "TAPPS_BRAIN_AUTH_TOKEN_FILE"
        ) or cls._read_secret("TAPPS_BRAIN_HTTP_AUTH_TOKEN", "TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE")

    @classmethod
    def _resolve_admin_token(cls) -> str | None:
        return cls._read_secret("TAPPS_BRAIN_ADMIN_TOKEN", "TAPPS_BRAIN_ADMIN_TOKEN_FILE")

    @classmethod
    def _resolve_metrics_token(cls) -> str | None:
        return cls._read_secret("TAPPS_BRAIN_METRICS_TOKEN", "TAPPS_BRAIN_METRICS_TOKEN_FILE")

    @staticmethod
    def _resolve_allowed_origins() -> list[str]:
        raw = (os.environ.get("TAPPS_BRAIN_ALLOWED_ORIGINS") or "").strip()
        if not raw:
            return []
        return [o.strip() for o in raw.split(",") if o.strip()]


# Module-level singleton — resolved once at import time.
_settings: _Settings = _Settings()


def get_settings() -> _Settings:
    """Return the process-wide :class:`_Settings` singleton."""
    return _settings
