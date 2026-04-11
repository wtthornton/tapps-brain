"""Tests for native health check (issue #15)."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from unittest.mock import MagicMock

import pytest

from tapps_brain.health_check import HealthReport, HiveHealth, run_health_check


def test_health_report_exit_code_ok_warn_error() -> None:
    r = HealthReport(status="ok")
    assert r.exit_code() == 0
    r.status = "warn"
    assert r.exit_code() == 1
    r.status = "error"
    assert r.exit_code() == 2


def test_run_health_check_includes_max_entries_per_group(tmp_path: Path) -> None:
    from tapps_brain.profile import LayerDefinition, LimitsConfig, MemoryProfile
    from tapps_brain.store import MemoryStore

    prof = MemoryProfile(
        name="cap-prof",
        layers=[
            LayerDefinition(name="pattern", half_life_days=60, confidence_floor=0.1),
        ],
        limits=LimitsConfig(max_entries=500, max_entries_per_group=42),
    )
    ms = MemoryStore(tmp_path, profile=prof)
    try:
        r = run_health_check(project_root=tmp_path, check_hive=False, store=ms)
        assert r.store.max_entries_per_group == 42
    finally:
        ms.close()


def test_run_health_check_includes_profile_seed_version(tmp_path: Path) -> None:
    from tapps_brain.profile import LayerDefinition, MemoryProfile, SeedingConfig
    from tapps_brain.store import MemoryStore

    prof = MemoryProfile(
        name="native-seed",
        layers=[
            LayerDefinition(
                name="pattern",
                half_life_days=60,
                confidence_floor=0.1,
            ),
        ],
        seeding=SeedingConfig(seed_version="3.0.0"),
    )
    ms = MemoryStore(tmp_path, profile=prof)
    try:
        r = run_health_check(project_root=tmp_path, check_hive=False, store=ms)
        assert r.store.profile_seed_version == "3.0.0"
    finally:
        ms.close()


def test_run_health_check_smoke(tmp_path: Path) -> None:
    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert report.status in ("ok", "warn", "error")
    assert report.store.schema_version != "unknown" or report.store.entries >= 0
    assert any("empty" in w.lower() for w in report.warnings)
    assert report.store.retrieval_effective_mode in (
        "bm25_only",
        "hybrid_pgvector_hnsw",
        "hybrid_pgvector_empty",
        "hybrid_on_the_fly_embeddings",
    )
    assert "CLI `memory search`" in report.store.retrieval_summary
    assert report.store.save_phase_summary == ""


def test_run_health_check_save_phase_summary_with_reused_store(tmp_path: Path) -> None:
    """In-process save metrics require the same ``MemoryStore`` instance (e.g. MCP server)."""
    from tapps_brain.store import MemoryStore

    ms = MemoryStore(project_root=tmp_path)
    try:
        ms.save(key="x", value="y")
        report = run_health_check(project_root=tmp_path, check_hive=False, store=ms)
        assert report.store.save_phase_summary
        assert "persist_ms" in report.store.save_phase_summary
    finally:
        ms.close()


def test_run_health_check_store_file_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tapps_brain.store.MemoryStore",
        MagicMock(side_effect=FileNotFoundError("missing")),
    )
    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert report.store.status == "warn"
    assert any("not found" in w.lower() for w in report.warnings)


def test_run_health_check_store_generic_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tapps_brain.store.MemoryStore",
        MagicMock(side_effect=RuntimeError("db broken")),
    )
    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert report.store.status == "error"
    assert any("Store error" in e for e in report.errors)


def _store_health_return(**overrides: object) -> MagicMock:
    hr = MagicMock()
    hr.entry_count = 4600
    hr.max_entries = 5000
    hr.schema_version = 1
    hr.tier_distribution = {"pattern": 1}
    hr.gc_candidates = 0
    hr.consolidation_candidates = 0
    for k, v in overrides.items():
        setattr(hr, k, v)
    return hr


def test_run_health_check_near_capacity_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_store = MagicMock()
    mock_store.health.return_value = _store_health_return()
    mock_store.vector_index_enabled = True
    mock_store.vector_row_count = 0
    mock_store.close = MagicMock()
    mock_store._lock = Lock()
    mock_store._entries = {}
    mock_store._persistence = MagicMock()
    mock_store.verify_integrity.return_value = {"tampered_keys": []}
    mock_store._persistence.list_relations.return_value = []

    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: mock_store)

    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert any("capacity" in w.lower() for w in report.warnings)


# ---------------------------------------------------------------------------
# HiveHealth new fields (EPIC-059.7)
# ---------------------------------------------------------------------------


def test_hive_health_model_has_pool_saturation_and_migration_version() -> None:
    """HiveHealth model must expose pool_saturation and migration_version fields."""
    hh = HiveHealth()
    assert hh.pool_saturation is None
    assert hh.migration_version is None


def test_hive_health_model_pool_saturation_set() -> None:
    hh = HiveHealth(pool_saturation=0.45)
    assert hh.pool_saturation == pytest.approx(0.45)


def test_hive_health_model_migration_version_set() -> None:
    hh = HiveHealth(migration_version=3)
    assert hh.migration_version == 3


def _make_mock_store(tmp_path: Path, entry_count: int = 0) -> MagicMock:
    mock_store = MagicMock()
    mock_store.health.return_value = _store_health_return(entry_count=entry_count)
    mock_store.vector_index_enabled = True
    mock_store.vector_row_count = 0
    mock_store.close = MagicMock()
    mock_store._lock = Lock()
    mock_store._entries = {}
    mock_store._persistence = MagicMock()
    mock_store.verify_integrity.return_value = {"tampered_keys": []}
    mock_store._persistence.list_relations.return_value = []
    return mock_store


def test_run_health_check_hive_pool_saturation_populated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """pool_saturation is read from the backend's connection manager when present."""
    mock_cm = MagicMock()
    mock_cm.get_pool_stats.return_value = {
        "pool_saturation": 0.25,
        "pool_size": 5,
        "pool_available": 3,
        "pool_min": 2,
        "pool_max": 20,
        "idle_timeout": 300,
    }

    mock_hive = MagicMock()
    mock_hive._cm = mock_cm
    mock_hive.count_by_namespace.return_value = {"universal": 10}
    mock_hive.close = MagicMock()

    mock_registry = MagicMock()
    mock_registry.list_agents.return_value = []

    monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "postgres://localhost/test")
    monkeypatch.setattr("tapps_brain.backends.create_hive_backend", lambda dsn: mock_hive)
    monkeypatch.setattr("tapps_brain.backends.AgentRegistry", lambda *a, **k: mock_registry)

    # Skip migration version lookup to keep this test focused on pool_saturation.
    import tapps_brain.postgres_migrations as _pm

    monkeypatch.setattr(_pm, "get_hive_schema_status", MagicMock(side_effect=Exception("skip")))
    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: _make_mock_store(tmp_path))

    report = run_health_check(project_root=tmp_path, check_hive=True)
    assert report.hive.pool_saturation == pytest.approx(0.25)


def test_run_health_check_hive_migration_version_populated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """migration_version is read from schema status when Postgres DSN is set."""
    from tapps_brain.postgres_migrations import SchemaStatus

    mock_hive = MagicMock()
    mock_hive._cm = None  # no pool stats
    mock_hive.count_by_namespace.return_value = {}
    mock_hive.close = MagicMock()

    mock_registry = MagicMock()
    mock_registry.list_agents.return_value = []

    monkeypatch.setenv("TAPPS_BRAIN_HIVE_DSN", "postgres://localhost/test")
    monkeypatch.setattr("tapps_brain.backends.create_hive_backend", lambda dsn: mock_hive)
    monkeypatch.setattr("tapps_brain.backends.AgentRegistry", lambda *a, **k: mock_registry)

    schema_status = SchemaStatus(current_version=5, applied_versions=[1, 2, 3, 4, 5])
    import tapps_brain.postgres_migrations as _pm

    monkeypatch.setattr(_pm, "get_hive_schema_status", lambda dsn: schema_status)
    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: _make_mock_store(tmp_path))

    report = run_health_check(project_root=tmp_path, check_hive=True)
    assert report.hive.migration_version == 5


def test_run_health_check_hive_no_dsn_reports_skipped(tmp_path: Path) -> None:
    """Without TAPPS_BRAIN_HIVE_DSN and no explicit hive_store, hive status is 'skipped'."""
    # v3: no SQLite fallback — hive requires Postgres DSN (ADR-007)
    report = run_health_check(project_root=tmp_path, check_hive=True)
    assert report.hive.status == "skipped"
    assert report.hive.connected is False
    assert any("hive" in w.lower() for w in report.warnings)


def test_run_health_check_hive_no_agents_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit hive_store with 0 agents generates a warning."""
    mock_hive = MagicMock()
    mock_hive.count_by_namespace.return_value = {"default": 1}
    mock_hive.close = MagicMock()
    mock_reg = MagicMock()
    mock_reg.list_agents.return_value = []

    # Patch AgentRegistry in backends (where health_check.py imports it from)
    monkeypatch.setattr("tapps_brain.backends.AgentRegistry", lambda **_: mock_reg)

    report = run_health_check(project_root=tmp_path, check_hive=True, hive_store=mock_hive)
    assert report.hive.connected is True
    assert any("agents" in w.lower() for w in report.warnings)


def test_run_health_check_hive_store_parameter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit hive_store parameter (e.g. Postgres backend) is used directly."""
    mock_hive = MagicMock()
    mock_hive.count_by_namespace.return_value = {"pg-ns": 5}
    mock_reg = MagicMock()
    mock_reg.list_agents.return_value = ["agent-a"]

    # Patch AgentRegistry in backends (where health_check.py imports it from)
    monkeypatch.setattr("tapps_brain.backends.AgentRegistry", lambda **_: mock_reg)

    report = run_health_check(project_root=tmp_path, check_hive=True, hive_store=mock_hive)

    assert report.hive.connected is True
    assert report.hive.hive_reachable is True
    assert report.hive.entries == 5
    assert report.hive.namespaces == ["pg-ns"]
    assert report.hive.agents == 1
    assert report.hive.status == "ok"


def test_run_health_check_integrity_corrupted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_store = MagicMock()
    mock_store.health.return_value = _store_health_return(entry_count=1, max_entries=5000)
    mock_store.vector_index_enabled = True
    mock_store.vector_row_count = 0
    mock_store.close = MagicMock()
    mock_store._lock = Lock()
    mock_store._entries = {"a": MagicMock(valid_at=None)}
    mock_store._persistence = MagicMock()
    mock_store.verify_integrity.return_value = {"tampered_keys": ["bad"]}
    mock_store._persistence.list_relations.return_value = []

    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: mock_store)

    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert any("integrity" in e.lower() or "mismatch" in e.lower() for e in report.errors)


def test_run_health_check_integrity_orphaned_and_expired(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_store = MagicMock()
    mock_store.health.return_value = _store_health_return(entry_count=1, max_entries=5000)
    mock_store.vector_index_enabled = True
    mock_store.vector_row_count = 0
    mock_store.close = MagicMock()
    mock_store._lock = Lock()
    mock_store._persistence = MagicMock()
    mock_store.verify_integrity.return_value = {"tampered_keys": []}
    mock_store._persistence.list_relations.return_value = [
        {"source_entry_keys": ["ghost"]},
    ]
    expired_ent = MagicMock(valid_at="1999-01-01T00:00:00+00:00")
    mock_store._entries = {"present": expired_ent}

    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: mock_store)

    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert any("orphaned" in w.lower() for w in report.warnings)
    assert any("valid_at" in w.lower() or "expired" in w.lower() for w in report.warnings)


def test_run_health_check_list_relations_raises_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_store = MagicMock()
    mock_store.health.return_value = _store_health_return(entry_count=1, max_entries=5000)
    mock_store.vector_index_enabled = True
    mock_store.vector_row_count = 0
    mock_store.close = MagicMock()
    mock_store._lock = Lock()
    mock_store._entries = {}
    mock_store._persistence = MagicMock()
    mock_store.verify_integrity.return_value = {"tampered_keys": []}
    mock_store._persistence.list_relations.side_effect = RuntimeError("no relations")

    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: mock_store)

    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert report.integrity.orphaned_relations == 0


def test_run_health_check_integrity_outer_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[int] = []

    def boom_store(*a: object, **k: object) -> MagicMock:
        calls.append(1)
        if len(calls) == 1:
            m = MagicMock()
            m.health.return_value = _store_health_return(entry_count=2, max_entries=5000)
            m.vector_index_enabled = True
            m.vector_row_count = 2
            m.close = MagicMock()
            return m
        raise RuntimeError("integrity open failed")

    monkeypatch.setattr("tapps_brain.store.MemoryStore", boom_store)

    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert any("integrity check failed" in w.lower() for w in report.warnings)


def test_run_health_check_skips_hive_when_disabled(tmp_path: Path) -> None:
    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert report.hive.connected is False
    assert report.hive.entries == 0
