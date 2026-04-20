"""Native health check for tapps-brain (issue #15).

Provides a structured, machine-readable health report covering:
- Store connectivity and entry counts
- Hive connectivity and agent/entry counts
- Integrity verification (corrupted/orphaned entries)
- Schema version
- MCP plugin version

Designed to complete in < 2 s on a Pi 5 and to be usable by
automated orchestrators (OpenClaw cron jobs, monitoring tools).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class StoreHealth(BaseModel):
    """Health of the local memory store."""

    status: str = "ok"  # ok | warn | error
    entries: int = 0
    max_entries: int = 0
    max_entries_per_group: int | None = Field(
        default=None,
        description="Profile per-``memory_group`` cap when set (STORY-044.7).",
    )
    schema_version: str = "unknown"
    size_bytes: int = 0
    tiers: dict[str, int] = Field(default_factory=dict)
    gc_candidates: int = 0
    consolidation_candidates: int = 0
    vector_index_enabled: bool = True
    vector_index_rows: int = 0
    # Effective retrieval / vector stack (no model load on health check)
    retrieval_effective_mode: str = Field(
        default="unknown",
        description=(
            "Machine-readable mode: bm25_only | hybrid_pgvector_hnsw | "
            "hybrid_pgvector_empty | hybrid_on_the_fly_embeddings | unknown"
        ),
    )
    retrieval_summary: str = Field(
        default="",
        description="One-line explanation of BM25 vs pgvector HNSW for operators.",
    )
    save_phase_summary: str = Field(
        default="",
        description="Save-phase p50 latencies from in-process metrics; empty if none.",
    )
    profile_seed_version: str | None = Field(
        default=None,
        description="Profile seed recipe label when ``MemoryProfile.seeding.seed_version`` is set.",
    )
    pool_min: int | None = Field(
        default=None,
        description="Configured pool min_size; None when no pool.",
    )
    pool_max: int | None = Field(
        default=None,
        description="Configured pool max_size; None when no pool.",
    )
    pool_saturation: float | None = Field(
        default=None,
        description=(
            "Fraction of private-backend pool max_size currently in use (0.0-1.0). "
            "None when the backend has no pool (e.g. InMemoryPrivateBackend in tests) "
            "or when pool stats are unavailable."
        ),
    )
    pool_idle: int | None = Field(
        default=None,
        description=(
            "Number of idle connections available in the private-backend pool. "
            "None when the backend has no pool."
        ),
    )
    last_migration_version: int | None = Field(
        default=None,
        description=(
            "Highest applied private-memory schema migration version. "
            "None when the DSN is unavailable or the version table is absent."
        ),
    )


class HiveHealth(BaseModel):
    """Health of the Hive shared store."""

    status: str = "ok"  # ok | warn | error
    connected: bool = False
    hive_reachable: bool = Field(
        default=False,
        description=(
            "True when the hive.db file exists on disk, even if the store "
            "cannot be opened (e.g. encryption mismatch). Distinguishes "
            "'file missing' from 'file present but connection failed'."
        ),
    )
    namespaces: list[str] = Field(default_factory=list)
    entries: int = 0
    agents: int = 0
    pool_min: int | None = Field(
        default=None,
        description="Configured pool min_size; None when no pool.",
    )
    pool_max: int | None = Field(
        default=None,
        description="Configured pool max_size; None when no pool.",
    )
    pool_saturation: float | None = Field(
        default=None,
        description=(
            "Fraction of pool max_size currently in use (0.0-1.0). "
            "None when the pool has not been opened or stats are unavailable."
        ),
    )
    pool_idle: int | None = Field(
        default=None,
        description=(
            "Number of idle connections available in the Hive pool. "
            "None when the pool has not been opened or stats are unavailable."
        ),
    )
    migration_version: int | None = Field(
        default=None,
        description=(
            "Highest applied Hive schema migration version. "
            "None when the DB is unreachable or version table is absent."
        ),
    )


class IntegrityHealth(BaseModel):
    """Health of entry integrity checks."""

    status: str = "ok"  # ok | warn | error
    corrupted_entries: int = 0
    orphaned_relations: int = 0
    expired_entries: int = 0


class HealthReport(BaseModel):
    """Complete tapps-brain health report."""

    status: str = "ok"  # ok | warn | error
    generated_at: str = Field(default_factory=lambda: datetime.now(tz=UTC).isoformat())
    elapsed_ms: float = 0.0
    store: StoreHealth = Field(default_factory=StoreHealth)
    hive: HiveHealth = Field(default_factory=HiveHealth)
    integrity: IntegrityHealth = Field(default_factory=IntegrityHealth)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def exit_code(self) -> int:
        """Return shell exit code: 0=ok, 1=warn, 2=error."""
        if self.status == "error":
            return 2
        if self.status == "warn":
            return 1
        return 0


# ---------------------------------------------------------------------------
# Health check implementation
# ---------------------------------------------------------------------------


def retrieval_health_slice(store: object) -> tuple[str, str]:
    """Public alias for dashboards/visual export: BM25 vs hybrid vs pgvector HNSW (no model load)."""  # noqa: E501
    return _retrieval_health_from_store(store)


def _retrieval_health_from_store(store: object) -> tuple[str, str]:
    """Derive retrieval mode from store state (ADR-007 — pgvector HNSW always on).

    Does not load sentence-transformers models — uses feature detection only.
    """
    sv_n_raw = getattr(store, "vector_row_count", 0)
    sv_n = int(sv_n_raw() if callable(sv_n_raw) else sv_n_raw)

    cli_note = " CLI `memory search` default: BM25-only."

    if sv_n > 0:
        return (
            "hybrid_pgvector_hnsw",
            f"Hybrid BM25+vector; pgvector HNSW index ({sv_n} embedded rows)." + cli_note,
        )
    return (
        "hybrid_pgvector_empty",
        "Hybrid-capable: pgvector HNSW index ready but no embedded rows yet — "
        "vector leg may embed on the fly." + cli_note,
    )


def _severity(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "error"
    if warnings:
        return "warn"
    return "ok"


def run_health_check(  # noqa: PLR0915
    project_root: Path | None = None,
    *,
    check_hive: bool = True,
    store: object | None = None,
    hive_store: object | None = None,
) -> HealthReport:
    """Run all health checks and return a structured report.

    Args:
        project_root: Path to the project root (defaults to cwd).
        check_hive: Whether to check Hive connectivity (may be slow if unreachable).
        store: When set (e.g. MCP server's ``MemoryStore``), reuse it for the store
            health slice so fields like ``save_phase_summary`` reflect in-process
            metrics. Caller must not close it. When omitted, a temporary store is
            opened and closed.
        hive_store: Optional pre-configured Hive backend (``HiveBackend`` protocol).
            When provided, this backend is used to probe Hive health.  When
            omitted the function checks ``store._hive_store`` and then
            ``TAPPS_BRAIN_HIVE_DSN`` (Postgres-only — ADR-007).

    Returns:
        HealthReport with structured results.
    """
    t0 = time.perf_counter()
    errors: list[str] = []
    warnings: list[str] = []

    # ------------------------------------------------------------------
    # Store health
    # ------------------------------------------------------------------
    store_health = StoreHealth()
    try:
        from tapps_brain.store import MemoryStore

        root = project_root or Path.cwd()
        reuse = store is not None
        ms: MemoryStore = store if reuse else MemoryStore(project_root=root)  # type: ignore[assignment]
        if reuse:
            root = Path(getattr(ms, "_project_root", root))
        try:
            report = ms.health()
            store_health.entries = report.entry_count
            store_health.max_entries = report.max_entries
            store_health.max_entries_per_group = report.max_entries_per_group
            store_health.schema_version = str(report.schema_version)
            store_health.tiers = dict(report.tier_distribution)
            store_health.gc_candidates = report.gc_candidates
            store_health.consolidation_candidates = report.consolidation_candidates
            store_health.vector_index_enabled = ms.vector_index_enabled
            store_health.vector_index_rows = ms.vector_row_count
            mode, summary = retrieval_health_slice(ms)
            store_health.retrieval_effective_mode = mode
            store_health.retrieval_summary = summary
            store_health.save_phase_summary = getattr(report, "save_phase_summary", "") or ""
            store_health.profile_seed_version = getattr(report, "profile_seed_version", None)

            # Size on disk is no longer reported under the Postgres backend —
            # the database lives in shared infrastructure (ADR-007).
            store_health.size_bytes = 0

            # Pool stats from private backend's connection manager (Postgres only).
            _priv_backend = getattr(ms, "_persistence", None)
            _priv_cm = getattr(_priv_backend, "_cm", None)
            if _priv_cm is not None and hasattr(_priv_cm, "get_pool_stats"):
                try:
                    _ps = _priv_cm.get_pool_stats()
                    store_health.pool_min = int(_ps.get("pool_min", 0))
                    store_health.pool_max = int(_ps.get("pool_max", 0))
                    store_health.pool_saturation = float(_ps.get("pool_saturation", 0.0))
                    store_health.pool_idle = int(_ps.get("pool_available", 0))
                except (AttributeError, TypeError, ValueError, KeyError):
                    pass  # pool stats unavailable; health check continues without them

            # Last applied private-memory migration version.
            import os as _os

            _db_url = _os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            if _db_url:
                try:
                    from tapps_brain.postgres_migrations import get_private_schema_status

                    _schema = get_private_schema_status(_db_url)
                    store_health.last_migration_version = _schema.current_version
                except Exception:  # nosec B110 — best-effort; missing migration info must not abort health check
                    pass

            # Checks
            if report.entry_count == 0:
                warnings.append("Store is empty — no entries found")
            if report.entry_count > report.max_entries * 0.9:
                warnings.append(
                    f"Store is at {report.entry_count}/{report.max_entries} entries "
                    f"({report.entry_count * 100 // report.max_entries}% capacity)"
                )

        finally:
            if not reuse:
                ms.close()
        store_health.status = "ok"
    except FileNotFoundError:
        store_health.status = "warn"
        warnings.append("Store database not found (may be first run)")
    except Exception:
        logger.exception("health_check.store_error")
        store_health.status = "error"
        errors.append("Store error: internal error (see logs).")

    # ------------------------------------------------------------------
    # Hive health
    # ------------------------------------------------------------------
    hive_health = HiveHealth()
    if check_hive:
        try:
            import os

            from tapps_brain.backends import AgentRegistry

            # Resolve which backend to probe:
            #   1. Explicit hive_store parameter
            #   2. store._hive_store (already-configured backend from MemoryStore)
            #   3. TAPPS_BRAIN_HIVE_DSN env var → Postgres via create_hive_backend (ADR-007)
            _hive_dsn = os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            _resolved_hive: object | None = hive_store or getattr(store, "_hive_store", None)
            _owns_hive = False  # whether we opened it and must close it

            if _resolved_hive is None and _hive_dsn:
                from tapps_brain.backends import create_hive_backend

                _resolved_hive = create_hive_backend(_hive_dsn)
                _owns_hive = True

            if _resolved_hive is None:
                hive_health.status = "skipped"
                hive_health.connected = False
                warnings.append(
                    "Hive not configured (set TAPPS_BRAIN_HIVE_DSN for Postgres; ADR-007)"
                )
            else:
                hive = _resolved_hive
                if _hive_dsn:
                    hive_health.hive_reachable = True
                try:
                    ns_counts = hive.count_by_namespace()  # type: ignore[attr-defined]
                    hive_health.connected = True
                    hive_health.hive_reachable = True
                    hive_health.namespaces = sorted(ns_counts.keys())
                    hive_health.entries = sum(ns_counts.values())

                    registry = AgentRegistry()
                    hive_health.agents = len(registry.list_agents())

                    if hive_health.agents == 0:
                        warnings.append("No agents registered in Hive")

                    # Pool saturation: available from connection manager if exposed.
                    _cm = getattr(hive, "_cm", None)
                    if _cm is not None and hasattr(_cm, "get_pool_stats"):
                        try:
                            _ps = _cm.get_pool_stats()
                            hive_health.pool_min = int(_ps.get("pool_min", 0))
                            hive_health.pool_max = int(_ps.get("pool_max", 0))
                            hive_health.pool_saturation = float(_ps.get("pool_saturation", 0.0))
                            hive_health.pool_idle = int(_ps.get("pool_available", 0))
                        except (AttributeError, TypeError, ValueError, KeyError):
                            pass  # hive pool stats unavailable; health check continues without them

                    # Migration version: last applied Hive schema version.
                    if _hive_dsn:
                        try:
                            from tapps_brain.postgres_migrations import get_hive_schema_status

                            _schema = get_hive_schema_status(_hive_dsn)
                            hive_health.migration_version = _schema.current_version
                        except Exception:  # nosec B110 — best-effort; missing hive migration info must not abort health check
                            pass

                finally:
                    if _owns_hive and hasattr(hive, "close"):
                        hive.close()
                hive_health.status = "ok"
        except Exception:
            logger.exception("health_check.hive_unavailable")
            hive_health.status = "warn"
            hive_health.connected = False
            warnings.append("Hive unavailable: internal error (see logs).")

    # ------------------------------------------------------------------
    # Integrity health
    # ------------------------------------------------------------------
    integrity_health = IntegrityHealth()
    try:
        from tapps_brain.store import MemoryStore

        root = project_root or Path.cwd()
        store = MemoryStore(project_root=root)
        try:
            integrity = store.verify_integrity()
            corrupted = len(integrity.get("tampered_keys", []))
            integrity_health.corrupted_entries = corrupted

            # Orphaned relations: relations pointing to missing keys
            with store._lock:
                all_keys = set(store._entries.keys())
            orphaned = 0
            try:
                all_relations = store._persistence.list_relations()
                for rel in all_relations:
                    for src_key in rel.get("source_entry_keys", []):
                        if src_key not in all_keys:
                            orphaned += 1
            except (AttributeError, TypeError):
                pass  # _persistence.list_relations not available; skip orphan check
            integrity_health.orphaned_relations = orphaned

            # Expired entries (past valid_at)
            now_iso = datetime.now(tz=UTC).isoformat()
            expired = 0
            with store._lock:
                for entry in store._entries.values():
                    valid_at = getattr(entry, "valid_at", None)
                    if valid_at and valid_at < now_iso:
                        expired += 1
            integrity_health.expired_entries = expired

            if corrupted > 0:
                errors.append(f"{corrupted} entry/entries have integrity hash mismatch")
            if orphaned > 0:
                warnings.append(f"{orphaned} orphaned relation(s) pointing to missing entries")
            if expired > 0:
                warnings.append(
                    f"{expired} entry/entries past valid_at — consider running maintenance"
                )
        finally:
            store.close()
        integrity_health.status = _severity(
            [e for e in errors if "integrity" in e.lower() or "corrupted" in e.lower()],
            [w for w in warnings if "integrity" in w.lower() or "orphaned" in w.lower()],
        )
    except Exception:
        logger.exception("health_check.integrity_check_failed")
        integrity_health.status = "warn"
        warnings.append("Integrity check failed: internal error (see logs).")

    # ------------------------------------------------------------------
    # Roll up overall status
    # ------------------------------------------------------------------
    overall_status = _severity(errors, warnings)

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return HealthReport(
        status=overall_status,
        elapsed_ms=round(elapsed_ms, 1),
        store=store_health,
        hive=hive_health,
        integrity=integrity_health,
        errors=errors,
        warnings=warnings,
    )
