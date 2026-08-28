"""One maintenance-service cycle (TAP-6698, KB-3.4).

Runs every pass the KB anchors call for: periodic consolidation scan, decay
refresh, demotion of contradicted learnings, GC of expired entries, flywheel
cursor advance, partition pre-create + drop, and the (dry-run-only)
namespace reaper. Every pass writes one audit row.

``dry_run=True`` never writes tenant data (SC-6). Decay refresh, demote-
contradicted, GC, and the partition passes all support a true preview mode
upstream. Consolidation and the flywheel cursor advance do not — those two
store methods have no dry-run concept at all — so a dry-run cycle skips them
entirely rather than silently writing while claiming to preview. Proving
those two passes is a fixture-only, apply-mode exercise (see
``tests/test_maintenance_cycle.py``); a dry-run cycle is the only shape safe
to point at a live, deployed DSN.

Single-tenant passes (consolidation, decay refresh, demote-contradicted, GC)
run against the ``MemoryStore`` for the container's configured serve tenant —
the same tenant ``tapps-brain-http`` serves, matching how the pre-existing
``tapps-brain maintenance ...`` CLI already operates (one ``project_dir`` per
invocation). Cross-tenant / schema-level passes (partitions, namespace reaper)
open their own connection directly against the DSN, since they are not scoped
to any one tenant's store.

The **flywheel cursor advance is cross-tenant** (``flywheel_all_tenants``,
TAP-6698 / VAL-02). It was single-tenant, which meant one apply cycle advanced
the cursor only for the tenant it served: on the live brain that left 100 of
128 tenants violating the <= 48h cursor-lag SLO, 99 of them with no cursor row
at all. ``_run_flywheel_passes`` iterates every ``(project_id, agent_id)``
present in ``feedback_events`` — the population the SLO is stated over. The
serve tenant's own ``flywheel_process`` pass is kept as-is; a second
``process_feedback`` for it is a no-op because its cursor has already advanced
past every event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.services import namespace_reaper, partition_manager
from tapps_brain.services.maintenance_heartbeat import record_heartbeat

if TYPE_CHECKING:
    from pathlib import Path

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


def _parse_retention_months(retention_env: str) -> int | None:
    value = retention_env.strip()
    if not value:
        return None
    try:
        months = int(value)
    except ValueError:
        logger.warning("maintenance_cycle.invalid_retention_months", value=value)
        return None
    return months if months > 0 else None


def _run_single_tenant_passes(
    *, project_root: Path, dry_run: bool, sample_size: int
) -> dict[str, Any]:
    from tapps_brain.backends import resolve_hive_backend_from_env
    from tapps_brain.services.maintenance_service import maintenance_consolidate, maintenance_gc
    from tapps_brain.store import MemoryStore

    store = MemoryStore(project_root, hive_store=resolve_hive_backend_from_env())
    passes: dict[str, Any] = {}
    try:
        project_id = store._project_id or ""
        agent_id = store.agent_id or ""

        # Consolidation and the flywheel cursor advance have no dry-run mode
        # in the underlying store methods (pre-existing, out of this lane's
        # scope to add) — a "dry-run cycle" that called them anyway would
        # silently write, breaking the SC-6 guarantee that dry_run never
        # mutates tenant data. Skip both rather than fake a preview.
        if dry_run:
            passes["consolidation"] = {"skipped": True, "reason": "no dry-run mode upstream"}
            passes["flywheel_process"] = {"skipped": True, "reason": "no dry-run mode upstream"}
        else:
            passes["consolidation"] = maintenance_consolidate(
                store, project_id, agent_id, project_root=project_root, force=True
            )
            passes["flywheel_process"] = store.process_feedback()

        passes["decay_refresh"] = store.refresh_decay(dry_run=dry_run, sample_size=sample_size)
        passes["demote_contradicted"] = store.decay_learnings(dry_run=dry_run)
        passes["gc"] = maintenance_gc(store, project_id, agent_id, dry_run=dry_run)

        for pass_name, result in passes.items():
            store._persistence.append_audit(
                f"maintenance_{pass_name}",
                "",
                extra={"trigger": "maintenance_cycle", "dry_run": dry_run, "result": result},
            )
    finally:
        store.close()
    return passes


def list_feedback_tenants(conn: Any) -> list[tuple[str, str]]:
    """Every ``(project_id, agent_id)`` that has at least one feedback event.

    VAL-02 asserts *every* ``flywheel_meta`` cursor is <= 48h behind that
    tenant's newest ``feedback_events`` row, so the population the cycle must
    serve is defined by ``feedback_events``, not by which tenants already
    happen to have a cursor row — on the live brain 99 of the 100 violating
    tenants have no cursor row at all, and scoping the pass to existing cursors
    would make the assertion vacuous for exactly the tenants that fail it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT project_id, agent_id FROM feedback_events "
            "ORDER BY project_id, agent_id"
        )
        return [(str(r[0]), str(r[1])) for r in cur.fetchall()]


def _run_flywheel_passes(*, dsn: str, project_root: Path, dry_run: bool) -> dict[str, Any]:
    """Advance the feedback cursor for **every** tenant with feedback events.

    The decay / GC / consolidation passes stay scoped to the container's serve
    tenant (see :func:`_run_single_tenant_passes`) because they are expensive
    and tenant-shaped.  The flywheel cursor is different: it is the one pass
    VAL-02 asserts across the whole population, and running it for only the
    served tenant left every other tenant's cursor permanently unset.

    ``dry_run`` skips the whole pass rather than faking a preview —
    ``process_feedback`` has no dry-run mode upstream and applies confidence
    deltas, so a "preview" that called it would write (same SC-6 reasoning as
    the single-tenant passes).
    """
    if dry_run:
        return {"skipped": True, "reason": "no dry-run mode upstream"}

    from tapps_brain.backends import create_private_backend
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.store import MemoryStore

    cm = PostgresConnectionManager(dsn, min_size=1, max_size=2)
    try:
        with cm.get_connection() as conn:
            tenants = list_feedback_tenants(conn)
    finally:
        cm.close()

    advanced: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for project_id, agent_id in tenants:
        # MemoryStore derives project_id from project_root's path hash, so an
        # arbitrary tenant can only be addressed by handing it a backend that
        # is already bound to that (project_id, agent_id).
        backend = create_private_backend(dsn, project_id=project_id, agent_id=agent_id)
        store = MemoryStore(project_root, agent_id=agent_id, private_backend=backend)
        try:
            result = store.process_feedback()
            advanced.append({"project_id": project_id, "agent_id": agent_id, "result": result})
            store._persistence.append_audit(
                "maintenance_flywheel_process",
                "",
                extra={"trigger": "maintenance_cycle", "dry_run": False, "result": result},
            )
        except Exception as exc:
            # One tenant's failure must not abort the remaining 127. The
            # failure is recorded and returned, not swallowed: a non-empty
            # ``failed`` list is visible in the cycle summary and the audit row.
            logger.warning(
                "maintenance_cycle.flywheel_tenant_failed",
                project_id=project_id,
                agent_id=agent_id,
                error=str(exc),
            )
            failed.append({"project_id": project_id, "agent_id": agent_id, "error": str(exc)})
        finally:
            store.close()

    return {
        "tenants_total": len(tenants),
        "tenants_advanced": len(advanced),
        "tenants_failed": len(failed),
        "failures": failed,
        "advanced": advanced,
    }


def _run_cross_tenant_passes(
    *,
    dsn: str,
    dry_run: bool,
    partition_months_ahead: int,
    retention_env: str,
    single_entry_age_days: int,
) -> dict[str, Any]:
    from tapps_brain.postgres_connection import PostgresConnectionManager

    passes: dict[str, Any] = {}
    cm = PostgresConnectionManager(dsn, min_size=1, max_size=2)
    try:
        with cm.get_connection() as conn:
            passes["partition_precreate"] = partition_manager.pre_create_partitions(
                conn, months_ahead=partition_months_ahead, dry_run=dry_run
            )
            retention_months = _parse_retention_months(retention_env)
            if retention_months is not None:
                passes["partition_drop"] = partition_manager.drop_old_partitions(
                    conn, retention_months=retention_months, dry_run=dry_run
                )
            # Always dry-run: live apply for the reaper is a later wave (Ruling 14).
            passes["namespace_reaper"] = namespace_reaper.scan_reapable(
                conn, age_days=single_entry_age_days
            )
            record_heartbeat(conn, details={"dry_run": dry_run, "passes": sorted(passes.keys())})
    finally:
        cm.close()
    return passes


def run_maintenance_cycle(
    *,
    project_root: Path,
    dsn: str,
    dry_run: bool = True,
    partition_months_ahead: int = 3,
    retention_env: str = "",
    single_entry_age_days: int = 30,
    sample_size: int = 10,
) -> dict[str, Any]:
    """Run one full maintenance cycle; return a summary dict per pass.

    Every pass writes an ``audit_log`` row (single-tenant passes via the
    store's own ``append_audit``, cross-tenant passes via the heartbeat row
    plus each pass's own dry-run/apply report), so a cycle's effect is always
    reconstructable from ``audit_log`` even when the process log is gone.
    """
    passes: dict[str, Any] = {}
    passes.update(
        _run_single_tenant_passes(
            project_root=project_root, dry_run=dry_run, sample_size=sample_size
        )
    )
    passes["flywheel_all_tenants"] = _run_flywheel_passes(
        dsn=dsn, project_root=project_root, dry_run=dry_run
    )
    passes.update(
        _run_cross_tenant_passes(
            dsn=dsn,
            dry_run=dry_run,
            partition_months_ahead=partition_months_ahead,
            retention_env=retention_env,
            single_entry_age_days=single_entry_age_days,
        )
    )
    return {"dry_run": dry_run, "passes": passes}
