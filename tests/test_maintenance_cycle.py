"""Maintenance-cycle orchestration tests (TAP-6698) — skip-free (see tests/_pg_fixture.py).

Covers deliverable 1 (the compose service's scheduling loop) and VAL-02:

* A dry-run cycle never writes tenant data — consolidation and the flywheel
  cursor advance have no dry-run mode upstream, so dry-run must skip them
  rather than silently write while claiming to preview (see the module
  docstring in ``services/maintenance_cycle.py``).
* Every pass writes one ``audit_log`` row.
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
