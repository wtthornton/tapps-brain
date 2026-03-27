"""Tests for native health check (issue #15)."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from unittest.mock import MagicMock

import pytest

from tapps_brain.health_check import HealthReport, run_health_check


def test_health_report_exit_code_ok_warn_error() -> None:
    r = HealthReport(status="ok")
    assert r.exit_code() == 0
    r.status = "warn"
    assert r.exit_code() == 1
    r.status = "error"
    assert r.exit_code() == 2


def test_run_health_check_smoke(tmp_path: Path) -> None:
    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert report.status in ("ok", "warn", "error")
    assert report.store.schema_version != "unknown" or report.store.entries >= 0
    assert any("empty" in w.lower() for w in report.warnings)


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
    hr.schema_version = 15
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
    mock_store.sqlite_vec_enabled = False
    mock_store.sqlite_vec_row_count = 0
    mock_store.close = MagicMock()
    mock_store._lock = Lock()
    mock_store._entries = {}
    mock_store._persistence = MagicMock()
    mock_store.verify_integrity.return_value = {"tampered_keys": []}
    mock_store._persistence.list_relations.return_value = []

    monkeypatch.setattr("tapps_brain.store.MemoryStore", lambda *a, **k: mock_store)

    report = run_health_check(project_root=tmp_path, check_hive=False)
    assert any("capacity" in w.lower() for w in report.warnings)


def test_run_health_check_hive_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tapps_brain.hive.HiveStore", MagicMock(side_effect=OSError("hive down")))
    report = run_health_check(project_root=tmp_path, check_hive=True)
    assert report.hive.status == "warn"
    assert report.hive.connected is False
    assert any("hive" in w.lower() for w in report.warnings)


def test_run_health_check_hive_no_agents_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_hive = MagicMock()
    mock_hive.count_by_namespace.return_value = {"default": 1}
    mock_hive.close = MagicMock()
    mock_reg = MagicMock()
    mock_reg.list_agents.return_value = []

    monkeypatch.setattr("tapps_brain.hive.HiveStore", lambda: mock_hive)
    monkeypatch.setattr("tapps_brain.hive.AgentRegistry", lambda: mock_reg)

    report = run_health_check(project_root=tmp_path, check_hive=True)
    assert report.hive.connected is True
    assert any("agents" in w.lower() for w in report.warnings)


def test_run_health_check_integrity_corrupted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_store = MagicMock()
    mock_store.health.return_value = _store_health_return(entry_count=1, max_entries=5000)
    mock_store.sqlite_vec_enabled = False
    mock_store.sqlite_vec_row_count = 0
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
    mock_store.sqlite_vec_enabled = False
    mock_store.sqlite_vec_row_count = 0
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
    mock_store.sqlite_vec_enabled = False
    mock_store.sqlite_vec_row_count = 0
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
            m.sqlite_vec_enabled = True
            m.sqlite_vec_row_count = 2
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
