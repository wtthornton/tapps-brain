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

from pydantic import BaseModel, Field

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
    sqlite_vec_enabled: bool = False
    sqlite_vec_rows: int = 0
    # GitHub #63 — effective retrieval / vector stack (no model load on health check)
    retrieval_effective_mode: str = Field(
        default="unknown",
        description=(
            "Machine-readable mode: bm25_only | hybrid_sqlite_vec_knn | "
            "hybrid_sqlite_vec_empty | hybrid_on_the_fly_embeddings | unknown"
        ),
    )
    retrieval_summary: str = Field(
        default="",
        description="One-line explanation of BM25 vs vector vs sqlite-vec for operators.",
    )
    save_phase_summary: str = Field(
        default="",
        description="Save-phase p50 latencies from in-process metrics; empty if none.",
    )
    profile_seed_version: str | None = Field(
        default=None,
        description="Profile seed recipe label when ``MemoryProfile.seeding.seed_version`` is set.",
    )


class HiveHealth(BaseModel):
    """Health of the Hive shared store."""

    status: str = "ok"  # ok | warn | error
    connected: bool = False
    namespaces: list[str] = Field(default_factory=list)
    entries: int = 0
    agents: int = 0


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
    """Public alias for dashboards/visual export: BM25 vs hybrid vs sqlite-vec (no model load)."""
    return _retrieval_health_from_store(store)


def _retrieval_health_from_store(store: object) -> tuple[str, str]:
    """Derive retrieval mode from installed extras and store state (GitHub #63).

    Does not load sentence-transformers models — uses feature detection only.
    """
    from tapps_brain._feature_flags import feature_flags

    st_ok = feature_flags.sentence_transformers
    sv_raw = getattr(store, "sqlite_vec_enabled", False)
    sv_on = bool(sv_raw() if callable(sv_raw) else sv_raw)
    sv_n_raw = getattr(store, "sqlite_vec_row_count", 0)
    sv_n = int(sv_n_raw() if callable(sv_n_raw) else sv_n_raw)

    cli_note = " CLI `memory search` default: BM25-only."

    if not st_ok:
        return (
            "bm25_only",
            "Vector leg unavailable: sentence-transformers not installed "
            "(e.g. `uv sync --extra vector`). Hybrid recall still calls the vector "
            "path but it returns no candidates, so results are BM25-only." + cli_note,
        )
    if sv_on and sv_n > 0:
        return (
            "hybrid_sqlite_vec_knn",
            f"Hybrid BM25+vector; sqlite-vec KNN index on ({sv_n} rows)." + cli_note,
        )
    if sv_on:
        return (
            "hybrid_sqlite_vec_empty",
            "Hybrid-capable: sqlite-vec extension on but `memory_vec` empty — "
            "vector leg may embed on the fly." + cli_note,
        )
    return (
        "hybrid_on_the_fly_embeddings",
        "Hybrid BM25+vector; no sqlite-vec KNN — vector leg uses on-the-fly embedding "
        "(heavier on large corpora)." + cli_note,
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
) -> HealthReport:
    """Run all health checks and return a structured report.

    Args:
        project_root: Path to the project root (defaults to cwd).
        check_hive: Whether to check Hive connectivity (may be slow if unreachable).
        store: When set (e.g. MCP server's ``MemoryStore``), reuse it for the store
            health slice so fields like ``save_phase_summary`` reflect in-process
            metrics. Caller must not close it. When omitted, a temporary store is
            opened and closed.

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
            store_health.sqlite_vec_enabled = ms.sqlite_vec_enabled
            store_health.sqlite_vec_rows = ms.sqlite_vec_row_count
            mode, summary = retrieval_health_slice(ms)
            store_health.retrieval_effective_mode = mode
            store_health.retrieval_summary = summary
            store_health.save_phase_summary = getattr(report, "save_phase_summary", "") or ""
            store_health.profile_seed_version = getattr(report, "profile_seed_version", None)

            # Size on disk
            db_path = root / ".tapps-brain" / "memory" / "memory.db"
            if db_path.exists():
                store_health.size_bytes = db_path.stat().st_size

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
    except Exception as exc:
        store_health.status = "error"
        errors.append(f"Store error: {exc}")

    # ------------------------------------------------------------------
    # Hive health
    # ------------------------------------------------------------------
    hive_health = HiveHealth()
    if check_hive:
        try:
            from tapps_brain.hive import AgentRegistry, HiveStore

            hive = HiveStore()
            try:
                ns_counts = hive.count_by_namespace()
                hive_health.connected = True
                hive_health.namespaces = sorted(ns_counts.keys())
                hive_health.entries = sum(ns_counts.values())

                registry = AgentRegistry()
                hive_health.agents = len(registry.list_agents())

                if hive_health.agents == 0:
                    warnings.append("No agents registered in Hive")
            finally:
                hive.close()
            hive_health.status = "ok"
        except Exception as exc:
            hive_health.status = "warn"
            hive_health.connected = False
            warnings.append(f"Hive unavailable: {exc}")

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
            except Exception:
                pass
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
    except Exception as exc:
        integrity_health.status = "warn"
        warnings.append(f"Integrity check failed: {exc}")

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
