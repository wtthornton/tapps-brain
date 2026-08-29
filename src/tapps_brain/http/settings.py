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


# Scorecard rows derived from the process-default store's health — not safe to
# show under ``?project=`` (they describe a different tenant).
_STORE_SCOPED_SCORECARD_IDS = frozenset(
    {
        "store_entries",
        "store_capacity",
        "integrity_tampered",
        "integrity_no_hash",
        "maintenance_backlog",
        "diagnostics_data",
        "diagnostics_circuit",
        "diagnostics_composite",
        "retrieval_stack",
        "rate_limits",
    }
)

# Health keys that reflect the default MemoryStore tenant, not the filter.
_STORE_SCOPED_HEALTH_KEYS = frozenset(
    {
        "store_path",
        "entry_count",
        "max_entries",
        "max_entries_per_group",
        "tier_distribution",
        "oldest_entry_age_days",
        "consolidation_candidates",
        "gc_candidates",
        "integrity_verified",
        "integrity_tampered",
        "integrity_no_hash",
        "integrity_tampered_keys",
        "integrity_likely_key_mismatch",
        "relation_count",
        "rate_limit_minute_anomalies",
        "rate_limit_lifetime_anomalies",
        "rate_limit_total_writes",
        "rate_limit_exempt_writes",
        "save_phase_summary",
        "rag_safety_blocked_count",
        "rag_safety_sanitized_count",
        "gc_runs_total",
        "gc_archived_rows_total",
        "gc_archive_bytes_total",
        "document_count",
        "document_total_bytes",
        "active_session_count",
        "federation_enabled",
        "federation_project_count",
        "profile_name",
        "profile_seed_version",
    }
)


def _filter_snapshot_by_project(payload: dict[str, Any], project_id: str) -> dict[str, Any]:
    """STORY-069.7: filter diagnostics/feedback to a single project_id.

    Also strips store-global ``health`` / ``scorecard`` / ``diagnostics`` fields
    that belong to the process-default tenant.  Leaving them in place made
    ``?project=api`` show another project's ``integrity_tampered_keys`` /
    entry counts / circuit state.
    """
    filtered = dict(payload)
    for key in ("diagnostics_history", "feedback_events"):
        rows = filtered.get(key) or []
        filtered[key] = [
            row for row in rows if isinstance(row, dict) and row.get("project_id") == project_id
        ]

    health = filtered.get("health")
    if isinstance(health, dict):
        scrubbed = {k: v for k, v in health.items() if k not in _STORE_SCOPED_HEALTH_KEYS}
        scrubbed["project_filter"] = project_id
        scrubbed["store_scoped_omitted"] = True
        filtered["health"] = scrubbed

    scorecard = filtered.get("scorecard")
    if isinstance(scorecard, list):
        filtered["scorecard"] = [
            row
            for row in scorecard
            if isinstance(row, dict) and row.get("id") not in _STORE_SCOPED_SCORECARD_IDS
        ]

    # Process-default store diagnostics (composite/circuit) — not project-scoped.
    if "diagnostics" in filtered:
        filtered["diagnostics"] = None

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
        # TAP-826 (EPIC-072 STORY-072.5): async-native write path.
        self.async_store: Any = None

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


def is_strict_mode() -> bool:
    """True when ``TAPPS_BRAIN_STRICT=1`` (production/docker deploy profile)."""
    return os.environ.get("TAPPS_BRAIN_STRICT", "").strip() == "1"


def is_strict_identity_enabled() -> bool:
    """True when ``TAPPS_BRAIN_STRICT_IDENTITY=1`` (TAP-6696 / VAL-25-flag).

    Default OFF — zero behavior change for existing callers. When on, writes
    resolving ``agent_id`` to the anonymous placeholders ``"unknown"`` /
    ``"default"`` are refused (Ruling 9: agent identity must be a logical
    name — ``AgentConfig.name`` / ``CLAUDE_AGENT_ID`` / repo slug — never a
    per-checkout hash or the unset default). Read once per call (not cached)
    so a deployed brain can be retuned without a restart, matching
    :func:`is_strict_mode`'s convention.
    """
    return os.environ.get("TAPPS_BRAIN_STRICT_IDENTITY", "").strip() == "1"


def get_settings() -> _Settings:
    """Return the process-wide :class:`_Settings` singleton."""
    return _settings
