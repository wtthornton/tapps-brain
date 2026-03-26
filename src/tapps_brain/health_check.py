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

import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class StoreHealth(BaseModel):
    """Health of the local memory store."""

    status: str = "ok"  # ok | warn | error
    entries: int = 0
    max_entries: int = 0
    schema_version: str = "unknown"
    size_bytes: int = 0
    tiers: dict[str, int] = Field(default_factory=dict)
    gc_candidates: int = 0
    consolidation_candidates: int = 0


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


def _severity(errors: list[str], warnings: list[str]) -> str:
    if errors:
        return "error"
    if warnings:
        return "warn"
    return "ok"


def run_health_check(
    project_root: Path | None = None,
    *,
    check_hive: bool = True,
) -> HealthReport:
    """Run all health checks and return a structured report.

    Args:
        project_root: Path to the project root (defaults to cwd).
        check_hive: Whether to check Hive connectivity (may be slow if unreachable).

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
        store = MemoryStore(project_root=root)
        try:
            report = store.health()
            store_health.entries = report.entry_count
            store_health.max_entries = report.max_entries
            store_health.schema_version = str(report.schema_version)
            store_health.tiers = dict(report.tier_distribution)
            store_health.gc_candidates = report.gc_candidates
            store_health.consolidation_candidates = report.consolidation_candidates

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
            store.close()
        store_health.status = "ok"
    except FileNotFoundError:
        store_health.status = "warn"
        warnings.append("Store database not found (may be first run)")
    except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
            with store._lock:  # type: ignore[attr-defined]
                all_keys = set(store._entries.keys())  # type: ignore[attr-defined]
            orphaned = 0
            try:
                all_relations = store._persistence.list_relations()  # type: ignore[attr-defined]
                for rel in all_relations:
                    for src_key in rel.get("source_entry_keys", []):
                        if src_key not in all_keys:
                            orphaned += 1
            except Exception:  # noqa: BLE001
                pass
            integrity_health.orphaned_relations = orphaned

            # Expired entries (past valid_at)
            now_iso = datetime.now(tz=UTC).isoformat()
            expired = 0
            with store._lock:  # type: ignore[attr-defined]
                for entry in store._entries.values():  # type: ignore[attr-defined]
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
    except Exception as exc:  # noqa: BLE001
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
