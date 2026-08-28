"""Maintenance-cycle orchestration tests (TAP-6698) — skip-free (see tests/_pg_fixture.py).

Covers deliverable 1 (the compose service's scheduling loop) and VAL-02:

* A dry-run cycle never writes tenant data — consolidation and the flywheel
  cursor advance have no dry-run mode upstream, so dry-run must skip them
  rather than silently write while claiming to preview (see the module
  docstring in ``services/maintenance_cycle.py``).
* Every pass writes one ``audit_log`` row.
* One apply-mode cycle advances the ``flywheel_meta`` cursor for **every**
  tenant with feedback events, not only the one the container serves — the
  cursor pass is cross-tenant (``flywheel_all_tenants``), which is what VAL-02
  actually asserts.
* An apply-mode cycle against a fixture seeded with duplicate rows produces
  the ``consolidation_merge`` / ``trigger='periodic_scan'`` audit shape KB-3.4
  and the recon anchors describe as already correct upstream — this lane's
  job is proving the scheduler invokes it, not re-authoring the merge logic.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tapps_brain.services.maintenance_cycle import run_maintenance_cycle
from tests._pg_fixture import resolve_fixture_dsn


@pytest.fixture(scope="session")
def cycle_fixture_dsn() -> str:
    return resolve_fixture_dsn()


@pytest.fixture()
def conn(cycle_fixture_dsn: str):
    with psycopg.connect(cycle_fixture_dsn) as c:
        yield c


@pytest.fixture()
def project_root(tmp_path, cycle_fixture_dsn: str, monkeypatch: pytest.MonkeyPatch):
    """MemoryStore resolves its own DSN from TAPPS_BRAIN_DATABASE_URL (not a param).

    The disposable fixture connects as the ``postgres`` superuser (no
    ``tapps_runtime``/RLS role split like the deployed brain has), so this
    also sets the same CI/dev override CI's own workflow uses
    (``TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1``, ``.github/workflows/ci.yml``).
    """
    monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", cycle_fixture_dsn)
    monkeypatch.setenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", "1")
    return tmp_path


class TestDryRunCycleNeverWrites:
    def test_dry_run_skips_the_two_passes_with_no_upstream_dry_run(
        self, project_root, cycle_fixture_dsn
    ) -> None:
        result = run_maintenance_cycle(
            project_root=project_root, dsn=cycle_fixture_dsn, dry_run=True
        )
        assert result["dry_run"] is True
        assert result["passes"]["consolidation"] == {
            "skipped": True,
            "reason": "no dry-run mode upstream",
        }
        assert result["passes"]["flywheel_process"] == {
            "skipped": True,
            "reason": "no dry-run mode upstream",
        }

    def test_dry_run_previews_the_passes_that_support_it(
        self, project_root, cycle_fixture_dsn
    ) -> None:
        result = run_maintenance_cycle(
            project_root=project_root, dsn=cycle_fixture_dsn, dry_run=True
        )
        assert result["passes"]["decay_refresh"]["dry_run"] is True
        assert result["passes"]["gc"]["dry_run"] is True
        assert result["passes"]["partition_precreate"]["dry_run"] is True
        assert result["passes"]["namespace_reaper"]["dry_run"] is True

    def test_dry_run_writes_one_audit_row_per_pass(
        self, project_root, cycle_fixture_dsn, conn
    ) -> None:
        run_maintenance_cycle(project_root=project_root, dsn=cycle_fixture_dsn, dry_run=True)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT event_type FROM audit_log "
                "WHERE details->>'trigger' = 'maintenance_cycle'"
            )
            events = {r[0] for r in cur.fetchall()}
        for name in (
            "maintenance_consolidation",
            "maintenance_decay_refresh",
            "maintenance_demote_contradicted",
            "maintenance_gc",
            "maintenance_flywheel_process",
        ):
            assert name in events, f"missing audit row for {name}; got {events}"

    def test_dry_run_still_writes_the_heartbeat(
        self, project_root, cycle_fixture_dsn, conn
    ) -> None:
        from tapps_brain.services.maintenance_heartbeat import has_recent_heartbeat

        run_maintenance_cycle(project_root=project_root, dsn=cycle_fixture_dsn, dry_run=True)
        assert has_recent_heartbeat(conn) is True


class TestFlywheelCursorIsCrossTenant:
    """VAL-02: every ``flywheel_meta`` cursor must be <= 48h behind its tenant.

    The cycle's flywheel pass used to run only for the tenant the container
    serves (``_run_single_tenant_passes``), so one apply cycle advanced exactly
    one cursor. On the deployed brain that left 100 of 128 tenants violating —
    99 of them with no cursor row at all, and only 32 cursor rows for 128
    tenant groups.

    Two seeded tenants, neither of which is the served tenant, are the smallest
    fixture that can tell "advances every tenant" apart from "advances the one
    it serves". The positive control comes first: SLO 4 must *fail* on this
    fixture before the cycle runs, otherwise the post-cycle assertion is
    vacuous.
    """

    @staticmethod
    def _seed_tenant(conn, project_id: str, agent_id: str) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO feedback_events "
                "(project_id, agent_id, id, event_type, timestamp) "
                "VALUES (%s, %s, %s, 'implicit_positive', now())",
                (project_id, agent_id, str(uuid.uuid4())),
            )
        conn.commit()

    @staticmethod
    def _cursor_rows(conn, project_ids: list[str]) -> dict[str, object]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT project_id, updated_at FROM flywheel_meta "
                "WHERE key = 'feedback_cursor' AND project_id = ANY(%s)",
                (project_ids,),
            )
            return {str(r[0]): r[1] for r in cur.fetchall()}

    def test_one_apply_cycle_advances_the_cursor_for_every_tenant(
        self, project_root, cycle_fixture_dsn, conn
    ) -> None:
        from tapps_brain.services import retention_slo

        marker = uuid.uuid4().hex[:8]
        tenants = [(f"vf-{marker}-a", "agent-a"), (f"vf-{marker}-b", "agent-b")]
        project_ids = [pid for pid, _ in tenants]
        for project_id, agent_id in tenants:
            self._seed_tenant(conn, project_id, agent_id)
        try:
            # Positive control: the clause is measurable on this fixture.
            before = retention_slo.check_flywheel_lag(conn)
            assert before["ok"] is False
            violating_before = {
                v["project_id"] for v in before["violations"] if v["project_id"] in project_ids
            }
            assert violating_before == set(project_ids), before
            assert self._cursor_rows(conn, project_ids) == {}

            run_maintenance_cycle(project_root=project_root, dsn=cycle_fixture_dsn, dry_run=False)

            after = self._cursor_rows(conn, project_ids)
            assert set(after) == set(project_ids), (
                f"cursor advanced for {sorted(after)} but not {sorted(project_ids)} — "
                "the pass is still single-tenant"
            )
            still_violating = {
                v["project_id"]
                for v in retention_slo.check_flywheel_lag(conn)["violations"]
                if v["project_id"] in project_ids
            }
            assert still_violating == set()
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM feedback_events WHERE project_id = ANY(%s)", (project_ids,)
                )
                cur.execute("DELETE FROM flywheel_meta WHERE project_id = ANY(%s)", (project_ids,))
            conn.commit()

    def test_the_pass_reports_the_tenant_population_it_served(
        self, project_root, cycle_fixture_dsn, conn
    ) -> None:
        marker = uuid.uuid4().hex[:8]
        tenants = [(f"vr-{marker}-a", "agent-a"), (f"vr-{marker}-b", "agent-b")]
        project_ids = [pid for pid, _ in tenants]
        for project_id, agent_id in tenants:
            self._seed_tenant(conn, project_id, agent_id)
        try:
            result = run_maintenance_cycle(
                project_root=project_root, dsn=cycle_fixture_dsn, dry_run=False
            )
            flywheel = result["passes"]["flywheel_all_tenants"]
            served = {a["project_id"] for a in flywheel["advanced"]}
            assert set(project_ids) <= served
            assert flywheel["tenants_total"] >= 2
            assert flywheel["tenants_failed"] == 0, flywheel["failures"]
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM feedback_events WHERE project_id = ANY(%s)", (project_ids,)
                )
                cur.execute("DELETE FROM flywheel_meta WHERE project_id = ANY(%s)", (project_ids,))
            conn.commit()

    def test_dry_run_skips_the_cross_tenant_pass_rather_than_faking_a_preview(
        self, project_root, cycle_fixture_dsn
    ) -> None:
        """SC-6: ``process_feedback`` applies confidence deltas and has no
        dry-run mode, so a "preview" that called it would write."""
        result = run_maintenance_cycle(
            project_root=project_root, dsn=cycle_fixture_dsn, dry_run=True
        )
        assert result["passes"]["flywheel_all_tenants"] == {
            "skipped": True,
            "reason": "no dry-run mode upstream",
        }

    def test_dry_run_writes_no_cursor_row_for_a_seeded_tenant(
        self, project_root, cycle_fixture_dsn, conn
    ) -> None:
        """The skip must be a real skip, asserted against the DB, not the summary."""
        marker = uuid.uuid4().hex[:8]
        project_id = f"vd-{marker}"
        self._seed_tenant(conn, project_id, "agent-a")
        try:
            run_maintenance_cycle(project_root=project_root, dsn=cycle_fixture_dsn, dry_run=True)
            assert self._cursor_rows(conn, [project_id]) == {}
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM feedback_events WHERE project_id = %s", (project_id,))
                cur.execute("DELETE FROM flywheel_meta WHERE project_id = %s", (project_id,))
            conn.commit()


class TestApplyModeConsolidation:
    def test_apply_cycle_produces_a_periodic_scan_merge_when_duplicates_exist(
        self, project_root, cycle_fixture_dsn, conn
    ) -> None:
        """VAL-02: prove OUR wiring invokes the real merge path, seeded fresh.

        Seeded via ``store.save()`` (not raw SQL) so the entries are real
        rows the periodic scan actually sees. Save-time auto-consolidation is
        disabled for the seed store (``ConsolidationConfig(enabled=False)``)
        so nothing merges before the periodic scan gets a chance — otherwise
        there would be no separate entries left for it to find, and the
        ``trigger='periodic_scan'`` signal this test exists to prove would
        never fire.

        Discovered while writing this test (documented here, not a bug to
        fix — out of this lane's scope): ``load_all``/``list_all`` on a
        freshly-constructed ``MemoryStore`` deliberately never hydrate the
        ``embedding`` column (``postgres_private.py`` — large vectors, loaded
        on-demand via ``knn_search`` only). That means ``run_periodic_consolidation_scan``,
        whenever driven from a fresh process — the pre-existing CLI
        (``maintenance consolidate --force``) exactly as much as this lane's
        scheduler — always falls back to the text+tag Jaccard/TF-IDF path in
        ``similarity.py``, never the embedding-cosine path. Two entries with
        identical text but no tags score 0.6 there (text_weight alone, below
        the 0.7 default threshold) — matching, shared tags are required to
        push the combined score over threshold. This is exactly the shape a
        real near-duplicate save (same topic, same tags) already has.

        Second discovery, same root cause category: ``consolidate()``'s
        content-loss guard (``_content_preservation_ratio`` in
        ``auto_consolidation.py``) computes ``len(merged) / sum(len(sources))``
        and blocks the merge below a 0.6 floor. Three EQUAL-length duplicates
        always compute ~0.33 there regardless of how identical their content
        is — the guard approximates "bytes retained," not "unique information
        retained," so a symmetric 3-way duplicate merge is always blocked by
        design. One long "canonical" entry plus two short near-duplicate
        fragments (this fixture's shape) is what actually clears both gates —
        and is also the realistic shape (a full note plus two partial
        restatements), not an edge case this test manufactured artificially.
        """
        from tapps_brain.backends import resolve_hive_backend_from_env
        from tapps_brain.store import ConsolidationConfig, MemoryStore

        marker = uuid.uuid4().hex[:8]
        long_value = (
            f"marker-{marker}: the release gate runs ruff, mypy, pytest, then packaging, "
            "then publishes to the internal index, then tags the release, then notifies "
            "the release channel with a summary of what changed and who approved it, "
            "then archives the build logs."
        )
        short_value = long_value[:40]
        tag = f"maintenance-cycle-test-{marker}"
        seed_store = MemoryStore(
            project_root,
            hive_store=resolve_hive_backend_from_env(),
            consolidation_config=ConsolidationConfig(enabled=False),
        )
        try:
            project_id = seed_store._project_id
            agent_id = seed_store.agent_id or "default"
            seed_store.save(key=f"dup-{marker}-0", value=long_value, tier="pattern", tags=[tag])
            seed_store.save(key=f"dup-{marker}-1", value=short_value, tier="pattern", tags=[tag])
            seed_store.save(key=f"dup-{marker}-2", value=short_value, tier="pattern", tags=[tag])
        finally:
            seed_store.close()

        result = run_maintenance_cycle(
            project_root=project_root, dsn=cycle_fixture_dsn, dry_run=False
        )
        assert result["passes"]["consolidation"].get("skipped") is not True

        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM audit_log "
                "WHERE event_type = 'consolidation_merge' "
                "AND details->>'trigger' = 'periodic_scan' "
                "AND project_id = %s AND agent_id = %s",
                (project_id, agent_id),
            )
            count = cur.fetchone()[0]
        assert count >= 1, (
            f"expected >=1 periodic_scan consolidation_merge row for {project_id}/{agent_id}, "
            f"got {count} — consolidation result was {result['passes']['consolidation']}"
        )
