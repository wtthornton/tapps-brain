"""Tests for brain visual JSON snapshot (aggregated metadata only)."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.metrics import StoreHealthReport
from tapps_brain.store import MemoryStore
from tapps_brain.visual_snapshot import (
    VISUAL_SNAPSHOT_SCHEMA_VERSION,
    DiagnosticsSummary,
    HiveHealthSummary,
    HiveNamespaceRow,
    MemoryVelocity,
    RetrievalDetail,
    RetrievalMetrics,
    SnapshotAggregates,
    _access_stats_from_entries,
    _build_scorecard,
    _collect_agent_registry,
    _collect_hive_health,
    _collect_retrieval_detail,
    _collect_retrieval_metrics,
    _collect_velocity,
    _snapshot_aggregates_from_entries,
    build_kg_graph,
    build_kg_health,
    build_visual_snapshot,
    capture_png,
    compute_fingerprint_hex,
    snapshot_to_json,
    theme_from_fingerprint,
)


def test_compute_fingerprint_hex_stable() -> None:
    identity = {"a": 1, "b": {"z": 9, "y": 8}}
    assert compute_fingerprint_hex(identity) == compute_fingerprint_hex(identity)


def test_compute_fingerprint_hex_key_order_invariant() -> None:
    """Canonical JSON sorts keys so insertion order does not matter."""
    h1 = compute_fingerprint_hex({"z": 1, "a": 2})
    h2 = compute_fingerprint_hex({"a": 2, "z": 1})
    assert h1 == h2


def test_theme_from_fingerprint_deterministic() -> None:
    fp = "a" * 64
    t1 = theme_from_fingerprint(fp)
    t2 = theme_from_fingerprint(fp)
    assert t1.model_dump() == t2.model_dump()


def test_theme_from_fingerprint_short_hex_pads() -> None:
    """Sub-64-bit hex still yields a valid theme (padding branch)."""
    t = theme_from_fingerprint("c0ffee")
    assert 0 <= t.hue_primary <= 359
    assert 0 <= t.flow_angle_deg <= 359


def test_theme_from_fingerprint_stays_in_amber_wedge() -> None:
    """Accent hues stay in the NLT amber/gold range (not blue/cyan/purple)."""
    for fp in (
        "a" * 64,
        "0" * 64,
        "f" * 64,
        "deadbeef" * 8,
        "c0ffee",
    ):
        t = theme_from_fingerprint(fp)
        assert 28 <= t.hue_primary <= 47
        assert 28 <= t.hue_accent <= 48
        assert t.hue_accent >= t.hue_primary


def test_build_visual_snapshot_shape(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        store.save(key="k1", value="secret body", tier="pattern", agent_scope="private")
        store.save(key="k2", value="other secret", tier="architectural", agent_scope="hive")
        snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()

    assert snap.schema_version == VISUAL_SNAPSHOT_SCHEMA_VERSION
    assert snap.identity_schema_version == 2
    assert snap.privacy_tier == "standard"
    assert len(snap.fingerprint_sha256) == 64
    assert snap.hive_attached is False
    assert snap.hive_health.status in {"ok", "warn", "skipped"}
    assert snap.agent_scope_counts.get("private") == 1
    assert snap.agent_scope_counts.get("hive") == 1
    assert snap.diagnostics is None
    assert snap.access_stats is not None
    assert len(snap.access_stats.buckets) == 4
    assert snap.access_stats.buckets[1].label == "1-5"
    assert snap.memory_group_count == 0
    assert snap.memory_group_counts is None
    assert snap.tag_stats is None
    assert snap.retrieval_effective_mode != ""
    assert len(snap.scorecard) >= 8
    assert any(c.id == "store_entries" for c in snap.scorecard)
    diag_rows = [c for c in snap.scorecard if c.id == "diagnostics_data"]
    assert len(diag_rows) == 1 and diag_rows[0].status == "unknown"
    assert "secret" not in snapshot_to_json(snap)
    assert "k1" not in snapshot_to_json(snap)


def test_build_visual_snapshot_with_diagnostics(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        # EPIC-078: the snapshot now reuses the most recent persisted diagnostics
        # row instead of recomputing at request time, so record one first.
        store.diagnostics(record_history=True)
        snap = build_visual_snapshot(store, skip_diagnostics=False)
    finally:
        store.close()
    assert snap.diagnostics is not None
    assert snap.diagnostics.circuit_state in {"closed", "degraded", "open", "half_open"}
    assert 0.0 <= snap.diagnostics.composite_score <= 1.0
    ids = {c.id for c in snap.scorecard}
    assert "diagnostics_data" in ids
    assert "diagnostics_circuit" in ids
    assert "diagnostics_composite" in ids
    dd = next(c for c in snap.scorecard if c.id == "diagnostics_data")
    assert dd.status == "ok"


def test_snapshot_json_sort_keys(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        raw = snapshot_to_json(build_visual_snapshot(store, skip_diagnostics=True))
    finally:
        store.close()
    lines = raw.splitlines()
    assert lines[0].startswith("{")
    data = json.loads(raw)
    keys = list(data.keys())
    assert keys == sorted(keys)


def test_fingerprint_changes_with_tier_distribution(tmp_path: Path) -> None:
    a = MemoryStore(tmp_path / "a")
    b = MemoryStore(tmp_path / "b")
    try:
        a.save(key="x", value="v", tier="context")
        b.save(key="x", value="v", tier="architectural")
        fa = build_visual_snapshot(a, skip_diagnostics=True).fingerprint_sha256
        fb = build_visual_snapshot(b, skip_diagnostics=True).fingerprint_sha256
    finally:
        a.close()
        b.close()
    assert fa != fb


def test_privacy_strict_redacts_health_path(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=True, privacy="strict")
    finally:
        store.close()
    assert snap.health.get("store_path") == "<redacted>"
    assert snap.health.get("integrity_tampered_keys") == []
    assert snap.privacy_tier == "strict"


def test_health_skip_consolidation_scan_reuses_cached_gauge(tmp_path: Path) -> None:
    """EPIC-078: the fast path must not run the O(n^2) consolidation scan."""
    store = MemoryStore(tmp_path)
    try:
        store._last_consolidation_candidates = 7
        with patch("tapps_brain.similarity.find_consolidation_groups") as mock_groups:
            report = store.health(skip_consolidation_scan=True)
        mock_groups.assert_not_called()
        assert report.consolidation_candidates == 7
    finally:
        store.close()


def test_build_snapshot_size_guards_consolidation_scan(tmp_path: Path) -> None:
    """EPIC-078/TAP-4332: /snapshot must size-guard the O(n^2) consolidation scan."""
    store = MemoryStore(tmp_path)
    captured: dict[str, Any] = {}
    real_health = store.health

    def _spy(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_health(**kwargs)

    try:
        with patch.object(store, "health", _spy):
            build_visual_snapshot(store, skip_diagnostics=True, privacy="standard")
        assert captured.get("consolidation_scan_max_entries") is not None
    finally:
        store.close()


def test_health_consolidation_scan_max_entries_caps_scan(tmp_path: Path) -> None:
    """TAP-4332: over the entry cap, health() reuses the cached gauge (no scan)."""
    store = MemoryStore(tmp_path)
    try:
        store.save("k1", "v1")
        store.save("k2", "v2")
        store._last_consolidation_candidates = 42
        with patch("tapps_brain.similarity.find_consolidation_groups") as mock_groups:
            report = store.health(consolidation_scan_max_entries=1)
        mock_groups.assert_not_called()
        assert report.consolidation_candidates == 42
    finally:
        store.close()


def test_health_consolidation_scan_max_entries_runs_when_small(tmp_path: Path) -> None:
    """TAP-4332: at/under the cap, health() runs the live scan and refreshes the gauge."""
    store = MemoryStore(tmp_path)
    try:
        store.save("k1", "v1")
        report = store.health(consolidation_scan_max_entries=1000)
        # Live scan ran: gauge reflects the freshly computed value (0 for unique entries).
        assert report.consolidation_candidates == store._last_consolidation_candidates
    finally:
        store.close()


def test_maybe_refresh_diagnostics_throttled(tmp_path: Path) -> None:
    """TAP-4332: a fresh diagnostics row within the TTL is not re-recorded."""
    from datetime import UTC, datetime

    from tapps_brain.visual_snapshot import _maybe_refresh_diagnostics

    store = MemoryStore(tmp_path)
    try:
        recent = {"recorded_at": datetime.now(UTC).isoformat(), "composite_score": 0.9}
        with (
            patch.object(store, "diagnostics_history", return_value=[recent]),
            patch.object(store, "diagnostics") as mock_diag,
        ):
            _maybe_refresh_diagnostics(store)
        mock_diag.assert_not_called()
    finally:
        store.close()


def test_maybe_refresh_diagnostics_records_when_stale(tmp_path: Path) -> None:
    """TAP-4332: a stale (or missing) diagnostics row triggers a fresh record."""
    from tapps_brain.visual_snapshot import _maybe_refresh_diagnostics

    store = MemoryStore(tmp_path)
    try:
        with (
            patch.object(store, "diagnostics_history", return_value=[]),
            patch.object(store, "diagnostics") as mock_diag,
        ):
            _maybe_refresh_diagnostics(store)
        mock_diag.assert_called_once()
        assert mock_diag.call_args.kwargs.get("run_remediation") is False
    finally:
        store.close()


def test_scorecard_reports_no_recent_diagnostics(tmp_path: Path) -> None:
    """TAP-4332: empty diagnostics (not skipped) says 'no recent', not --skip-diagnostics."""
    store = MemoryStore(tmp_path)
    store.save("k", "v")
    try:
        with (
            patch.object(store, "diagnostics_history", return_value=[]),
            patch.object(store, "diagnostics"),
        ):
            snap = build_visual_snapshot(store, skip_diagnostics=False)
    finally:
        store.close()
    checks = {c.id: c for c in snap.scorecard}
    assert "No recent diagnostics recorded yet" in checks["diagnostics_data"].detail


def test_privacy_local_includes_tags_and_groups(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        store.save(
            key="a",
            value="body",
            tier="pattern",
            tags=["alpha", "beta"],
            memory_group="team-a",
        )
        store.save(key="b", value="body2", tier="pattern", tags=["alpha"], memory_group="team-a")
        snap = build_visual_snapshot(store, skip_diagnostics=True, privacy="local")
    finally:
        store.close()
    assert snap.tag_stats is not None
    tags = {t.tag: t.count for t in snap.tag_stats}
    assert tags.get("alpha") == 2
    assert tags.get("beta") == 1
    assert snap.memory_group_counts is not None
    assert snap.memory_group_counts.get("team-a") == 2
    assert snap.memory_group_count == 1


# ---------------------------------------------------------------------------
# PNG capture — unit tests (no live browser required)
# ---------------------------------------------------------------------------


def test_capture_png_importable() -> None:
    """capture_png is exported from the module."""
    from tapps_brain.visual_snapshot import capture_png as _cp

    assert callable(_cp)


def test_capture_png_raises_when_playwright_missing(tmp_path: Path) -> None:
    """RuntimeError with install hint when playwright is not available."""
    blocked = {"playwright": None, "playwright.sync_api": None}
    with patch.dict(sys.modules, blocked), pytest.raises(RuntimeError, match="playwright"):
        capture_png(
            html_path=tmp_path / "index.html",
            json_path=tmp_path / "snap.json",
            output=tmp_path / "out.png",
        )


def test_capture_png_raises_file_not_found_html(tmp_path: Path) -> None:
    """FileNotFoundError when html_path does not exist (after playwright import)."""
    # Only test this when playwright is actually installed; skip otherwise.
    pytest.importorskip("playwright")
    (tmp_path / "snap.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=r"index\.html"):
        capture_png(
            html_path=tmp_path / "index.html",
            json_path=tmp_path / "snap.json",
            output=tmp_path / "out.png",
        )


def test_capture_png_raises_file_not_found_json(tmp_path: Path) -> None:
    """FileNotFoundError when json_path does not exist (after playwright import)."""
    pytest.importorskip("playwright")
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match=r"snap\.json"):
        capture_png(
            html_path=tmp_path / "index.html",
            json_path=tmp_path / "snap.json",
            output=tmp_path / "out.png",
        )


# ---------------------------------------------------------------------------
# _access_stats_from_entries — branch coverage
# ---------------------------------------------------------------------------


def test_access_stats_nonzero_buckets() -> None:
    """Entries with various access_count values fill all buckets."""
    entries = []
    for ac in [0, 1, 3, 5, 6, 15, 20, 25, 100]:
        e = MagicMock()
        e.access_count = ac
        e.total_access_count = ac + 1
        e.useful_access_count = max(0, ac - 1)
        entries.append(e)
    stats = _access_stats_from_entries(entries)
    assert stats.sum_access_count == sum([0, 1, 3, 5, 6, 15, 20, 25, 100])
    assert stats.entries_with_access == 8
    b = {b.label: b.count for b in stats.buckets}
    assert b["0"] == 1
    assert b["1-5"] == 3  # 1, 3, 5
    assert b["6-20"] == 3  # 6, 15, 20
    assert b["21+"] == 2  # 25, 100
    assert stats.sum_total_access_count > 0
    assert stats.sum_useful_access_count >= 0


# ---------------------------------------------------------------------------
# _build_scorecard — branch coverage via mocked StoreHealthReport
# ---------------------------------------------------------------------------


def _make_report(**kwargs: object) -> StoreHealthReport:
    defaults: dict[str, object] = {
        "store_path": "/tmp/test",
        "entry_count": 10,
        "max_entries": 5000,
    }
    defaults.update(kwargs)
    return StoreHealthReport(**defaults)  # type: ignore[arg-type]


def _scorecard_ids(checks: list) -> dict:
    return {c.id: c for c in checks}


def test_scorecard_empty_store() -> None:
    report = _make_report(entry_count=0)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["store_entries"].status == "info"


def test_scorecard_diagnostics_degraded_circuit() -> None:
    report = _make_report()
    diag = DiagnosticsSummary(composite_score=0.8, circuit_state="degraded", recorded_at="now")
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=diag,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=False,
        )
    )
    assert checks["diagnostics_circuit"].status == "warn"


def test_scorecard_diagnostics_open_circuit() -> None:
    report = _make_report()
    diag = DiagnosticsSummary(composite_score=0.8, circuit_state="open", recorded_at="now")
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=diag,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=False,
        )
    )
    assert checks["diagnostics_circuit"].status == "fail"


def test_scorecard_diagnostics_unknown_circuit() -> None:
    report = _make_report()
    diag = DiagnosticsSummary(composite_score=0.8, circuit_state="weird_state", recorded_at="now")
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=diag,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=False,
        )
    )
    assert checks["diagnostics_circuit"].status == "warn"


def test_scorecard_diagnostics_warn_score() -> None:
    report = _make_report()
    diag = DiagnosticsSummary(composite_score=0.6, circuit_state="closed", recorded_at="now")
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=diag,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=False,
        )
    )
    assert checks["diagnostics_composite"].status == "warn"


def test_scorecard_diagnostics_fail_score() -> None:
    report = _make_report()
    diag = DiagnosticsSummary(composite_score=0.3, circuit_state="closed", recorded_at="now")
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=diag,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=False,
        )
    )
    assert checks["diagnostics_composite"].status == "fail"


def test_scorecard_integrity_tampered() -> None:
    report = _make_report(integrity_tampered=3)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["integrity_tampered"].status == "fail"


def test_scorecard_integrity_key_mismatch_warn() -> None:
    """TAP-4331: all hashed entries failing -> warn + resign hint, not fail."""
    report = _make_report(integrity_tampered=4402, integrity_likely_key_mismatch=True)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    check = checks["integrity_tampered"]
    assert check.status == "warn"
    assert check.title == "Integrity (key mismatch)"
    assert "resign-integrity" in check.ticket_hint


def test_scorecard_integrity_no_hash_warn() -> None:
    report = _make_report(integrity_no_hash=5)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["integrity_no_hash"].status == "warn"


def test_scorecard_capacity_warn() -> None:
    report = _make_report(entry_count=4200, max_entries=5000)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["store_capacity"].status == "warn"


def test_scorecard_capacity_fail() -> None:
    report = _make_report(entry_count=4800, max_entries=5000)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["store_capacity"].status == "fail"


def test_scorecard_rate_limit_anomalies() -> None:
    report = _make_report(rate_limit_minute_anomalies=2, rate_limit_lifetime_anomalies=1)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["rate_limits"].status == "warn"


def test_scorecard_maintenance_backlog_warn() -> None:
    report = _make_report(gc_candidates=100, consolidation_candidates=150)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["maintenance_backlog"].status == "warn"


def test_scorecard_maintenance_backlog_info() -> None:
    report = _make_report(gc_candidates=5, consolidation_candidates=0)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["maintenance_backlog"].status == "info"


def test_scorecard_hive_attached_not_connected() -> None:
    report = _make_report()
    hive = HiveHealthSummary(connected=False, status="warn")
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=True,
            hive_health=hive,
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["hive_hub"].status == "warn"


def test_scorecard_hive_attached_connected_no_agents() -> None:
    report = _make_report()
    hive = HiveHealthSummary(connected=True, status="ok", agents=0)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=True,
            hive_health=hive,
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["hive_hub"].status == "warn"


def test_scorecard_hive_attached_connected_with_agents() -> None:
    report = _make_report()
    hive = HiveHealthSummary(connected=True, status="ok", agents=3, entries=100)
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=True,
            hive_health=hive,
            retrieval_mode="bm25_only",
            skip_diagnostics=True,
        )
    )
    assert checks["hive_hub"].status == "ok"


def test_scorecard_retrieval_hybrid_pgvector_empty() -> None:
    report = _make_report()
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="hybrid_pgvector_empty",
            skip_diagnostics=True,
        )
    )
    assert checks["retrieval_stack"].status == "warn"


def test_scorecard_retrieval_hybrid_on_the_fly() -> None:
    report = _make_report()
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="hybrid_on_the_fly_embeddings",
            skip_diagnostics=True,
        )
    )
    assert checks["retrieval_stack"].status == "info"


def test_scorecard_retrieval_unknown() -> None:
    report = _make_report()
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="unknown",
            skip_diagnostics=True,
        )
    )
    assert checks["retrieval_stack"].status == "warn"


def test_scorecard_retrieval_other_mode() -> None:
    report = _make_report()
    checks = _scorecard_ids(
        _build_scorecard(
            report,
            diagnostics=None,
            hive_attached=False,
            hive_health=HiveHealthSummary(),
            retrieval_mode="custom_mode",
            skip_diagnostics=True,
        )
    )
    assert checks["retrieval_stack"].status == "info"


# SQLCipher scorecard check was removed in ADR-007 stage 2 (2026-04-11) —
# at-rest encryption is delegated to the storage layer (pg_tde) and no
# longer surfaces in the brain-visual scorecard.


# ---------------------------------------------------------------------------
# HiveNamespaceRow and HiveHealthSummary.namespaces (STORY-078.10 / EPIC-065.4)
# ---------------------------------------------------------------------------


def test_hive_namespace_row_defaults() -> None:
    """HiveNamespaceRow has sensible defaults and accepts all fields."""
    row = HiveNamespaceRow(name="repo-brain", entry_count=42, last_write_at="2026-01-01T00:00:00Z")
    assert row.name == "repo-brain"
    assert row.entry_count == 42
    assert row.last_write_at == "2026-01-01T00:00:00Z"


def test_hive_namespace_row_none_last_write() -> None:
    row = HiveNamespaceRow(name="empty-ns")
    assert row.entry_count == 0
    assert row.last_write_at is None


def test_hive_health_summary_namespaces_default() -> None:
    hh = HiveHealthSummary()
    assert hh.namespaces == []


def test_hive_health_summary_namespaces_populated() -> None:
    rows = [
        HiveNamespaceRow(name="alpha", entry_count=10, last_write_at="2026-01-01T00:00:00Z"),
        HiveNamespaceRow(name="beta", entry_count=5, last_write_at=None),
    ]
    hh = HiveHealthSummary(connected=True, status="ok", entries=15, agents=2, namespaces=rows)
    assert len(hh.namespaces) == 2
    assert hh.namespaces[0].name == "alpha"
    assert hh.namespaces[1].entry_count == 5


def test_hive_health_summary_serialises_namespaces() -> None:
    """HiveHealthSummary.namespaces round-trips through model_dump/model_validate."""
    rows = [HiveNamespaceRow(name="ns1", entry_count=7, last_write_at="2026-04-01T10:00:00Z")]
    hh = HiveHealthSummary(connected=True, status="ok", entries=7, agents=1, namespaces=rows)
    dumped = hh.model_dump()
    assert dumped["namespaces"] == [
        {"name": "ns1", "entry_count": 7, "last_write_at": "2026-04-01T10:00:00Z"}
    ]
    restored = HiveHealthSummary.model_validate(dumped)
    assert restored.namespaces[0].name == "ns1"
    assert restored.namespaces[0].entry_count == 7


def test_collect_hive_health_uses_namespace_detail_list(tmp_path: Path) -> None:
    """_collect_hive_health() populates namespaces when hive has namespace_detail_list()."""
    store = MemoryStore(tmp_path)
    try:
        mock_hive = MagicMock()
        mock_hive.namespace_detail_list.return_value = [
            {
                "namespace": "personal",
                "entry_count": 20,
                "last_write_at": "2026-04-01T12:00:00+00:00",
            },
            {"namespace": "repo-brain", "entry_count": 55, "last_write_at": None},
        ]
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["agent-a", "agent-b", "agent-c"]

        with (
            patch("tapps_brain.visual_snapshot.resolve_agent_registry", return_value=mock_registry),
            patch("tapps_brain.visual_snapshot.resolve_hive_backend_from_env", return_value=mock_hive),
        ):
            result = _collect_hive_health(store)

        assert result.connected is True
        assert result.status == "ok"
        assert result.entries == 75
        assert result.agents == 3
        assert len(result.namespaces) == 2
        # sorted by namespace name
        assert result.namespaces[0].name == "personal"
        assert result.namespaces[0].entry_count == 20
        assert result.namespaces[1].name == "repo-brain"
        assert result.namespaces[1].last_write_at is None
        mock_hive.close.assert_called_once()
    finally:
        store.close()


def test_collect_hive_health_falls_back_when_no_namespace_detail_list(tmp_path: Path) -> None:
    """_collect_hive_health() falls back to count_by_namespace() when method absent."""
    store = MemoryStore(tmp_path)
    try:
        mock_hive = MagicMock(spec=["count_by_namespace", "close"])
        mock_hive.count_by_namespace.return_value = {"alpha": 3, "beta": 7}
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["agent-x"]

        with (
            patch("tapps_brain.visual_snapshot.resolve_agent_registry", return_value=mock_registry),
            patch("tapps_brain.visual_snapshot.resolve_hive_backend_from_env", return_value=mock_hive),
        ):
            result = _collect_hive_health(store)

        assert result.connected is True
        assert result.entries == 10
        assert result.agents == 1
        assert len(result.namespaces) == 2
        assert result.namespaces[0].name == "alpha"
        assert result.namespaces[0].entry_count == 3
        assert result.namespaces[0].last_write_at is None
        mock_hive.close.assert_called_once()
    finally:
        store.close()


def test_collect_hive_health_empty_namespaces(tmp_path: Path) -> None:
    """_collect_hive_health() returns empty namespaces when hive is fresh."""
    store = MemoryStore(tmp_path)
    try:
        mock_hive = MagicMock()
        mock_hive.namespace_detail_list.return_value = []
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []

        with (
            patch("tapps_brain.visual_snapshot.resolve_agent_registry", return_value=mock_registry),
            patch("tapps_brain.visual_snapshot.resolve_hive_backend_from_env", return_value=mock_hive),
        ):
            result = _collect_hive_health(store)

        assert result.connected is True
        assert result.entries == 0
        assert result.namespaces == []
    finally:
        store.close()


def test_collect_hive_health_not_reachable(tmp_path: Path) -> None:
    """_collect_hive_health() returns connected=False when DSN not set."""
    store = MemoryStore(tmp_path)
    try:
        with patch("tapps_brain.visual_snapshot.resolve_hive_backend_from_env", return_value=None):
            result = _collect_hive_health(store)

        assert result.connected is False
        assert result.status == "skipped"
        assert result.namespaces == []
        assert result.entries == 0
    finally:
        store.close()


# ---------------------------------------------------------------------------
# _collect_agent_registry — STORY-065.5
# ---------------------------------------------------------------------------


def _make_mock_backend_with_rows(rows: list[tuple[str, str, str, str | None]]) -> MagicMock:
    """Build a mock hive backend whose _cm executes the agent registry query."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchall.return_value = rows
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cm = MagicMock()
    mock_cm.get_connection.return_value = mock_conn
    mock_backend = MagicMock()
    mock_backend._cm = mock_cm
    return mock_backend


def test_collect_agent_registry_full_registry() -> None:
    """Full registry: 3 agents returned with correct mapping."""
    rows = [
        (
            "agent-alpha-12345678",
            "repo-brain",
            "2026-01-01T00:00:00+00:00",
            "2026-04-10T10:00:00+00:00",
        ),
        (
            "agent-beta-87654321",
            "universal",
            "2026-02-01T00:00:00+00:00",
            "2026-04-09T08:00:00+00:00",
        ),
        ("agent-gamma-aabbcc", "custom-ns", "2026-03-01T00:00:00+00:00", None),
    ]
    backend = _make_mock_backend_with_rows(rows)
    result = _collect_agent_registry(backend, privacy="local")

    assert len(result) == 3
    # Full agent_id preserved in local tier
    assert result[0]["agent_id"] == "agent-alpha-12345678"
    assert result[0]["namespace"] == "repo-brain"
    assert result[0]["scope"] == "hive"
    assert result[0]["registered_at"] == "2026-01-01T00:00:00+00:00"
    assert result[0]["last_write_at"] == "2026-04-10T10:00:00+00:00"


def test_collect_agent_registry_privacy_standard_truncates_agent_id() -> None:
    """Standard privacy tier truncates agent_id to 8 chars + ellipsis."""
    rows = [("agent-alpha-long-id-here", "repo-brain", "2026-01-01T00:00:00+00:00", None)]
    backend = _make_mock_backend_with_rows(rows)
    result = _collect_agent_registry(backend, privacy="standard")

    assert len(result) == 1
    assert result[0]["agent_id"] == "agent-al\u2026"  # 8 chars + …


def test_collect_agent_registry_privacy_strict_truncates_agent_id() -> None:
    """Strict privacy tier also truncates agent_id."""
    rows = [("agent-beta-long-id-here", "universal", "2026-01-01T00:00:00+00:00", None)]
    backend = _make_mock_backend_with_rows(rows)
    result = _collect_agent_registry(backend, privacy="strict")

    assert len(result) == 1
    assert result[0]["agent_id"] == "agent-be\u2026"


def test_collect_agent_registry_short_id_not_truncated() -> None:
    """Agent IDs of 8 chars or fewer are not truncated even on standard tier."""
    rows = [("short-id", "ns", "2026-01-01T00:00:00+00:00", None)]
    backend = _make_mock_backend_with_rows(rows)
    result = _collect_agent_registry(backend, privacy="standard")

    assert result[0]["agent_id"] == "short-id"
    assert "\u2026" not in result[0]["agent_id"]


def test_collect_agent_registry_null_last_write_at() -> None:
    """Agent that has never written has last_write_at=None."""
    rows = [("agent-gamma", "repo-brain", "2026-03-01T00:00:00+00:00", None)]
    backend = _make_mock_backend_with_rows(rows)
    result = _collect_agent_registry(backend, privacy="local")

    assert result[0]["last_write_at"] is None


def test_collect_agent_registry_no_cm_returns_empty() -> None:
    """Backend without _cm returns empty list (non-Postgres backend)."""
    mock_backend = MagicMock(spec=[])  # no _cm attribute
    result = _collect_agent_registry(mock_backend)
    assert result == []


def test_collect_agent_registry_registry_table_missing_returns_empty() -> None:
    """Returns [] without raising when agent_registry table does not exist."""
    mock_cm = MagicMock()
    mock_cm.get_connection.side_effect = Exception("relation agent_registry does not exist")
    mock_backend = MagicMock()
    mock_backend._cm = mock_cm
    result = _collect_agent_registry(mock_backend)
    assert result == []


def test_collect_agent_registry_empty_registry() -> None:
    """Empty agent_registry table returns [] (no error)."""
    backend = _make_mock_backend_with_rows([])
    result = _collect_agent_registry(backend)
    assert result == []


def test_collect_agent_registry_null_profile_defaults_to_universal() -> None:
    """Null profile field defaults namespace to 'universal'."""
    rows = [("agent-xyz", None, "2026-01-01T00:00:00+00:00", None)]
    backend = _make_mock_backend_with_rows(rows)
    result = _collect_agent_registry(backend, privacy="local")
    assert result[0]["namespace"] == "universal"


def test_visual_snapshot_has_agent_registry_field(tmp_path: Path) -> None:
    """build_visual_snapshot returns agent_registry field (empty when no hive)."""
    store = MemoryStore(tmp_path)
    try:
        with patch("tapps_brain.visual_snapshot.resolve_hive_backend_from_env", return_value=None):
            snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()
    assert hasattr(snap, "agent_registry")
    assert isinstance(snap.agent_registry, list)


def test_visual_snapshot_agent_registry_populated_from_hive(tmp_path: Path) -> None:
    """build_visual_snapshot populates agent_registry when hive backend is available."""
    rows = [
        (
            "agent-abc-12345678",
            "repo-brain",
            "2026-01-01T00:00:00+00:00",
            "2026-04-10T10:00:00+00:00",
        ),
        ("agent-def-abcdefgh", "universal", "2026-02-01T00:00:00+00:00", None),
    ]
    mock_hive = _make_mock_backend_with_rows(rows)
    mock_hive.close = MagicMock()
    store = MemoryStore(tmp_path)
    try:
        with (
            patch("tapps_brain.visual_snapshot.resolve_hive_backend_from_env", return_value=mock_hive),
            patch(
                "tapps_brain.visual_snapshot._collect_hive_health",
                return_value=HiveHealthSummary(connected=False, status="skipped"),
            ),
        ):
            snap = build_visual_snapshot(store, skip_diagnostics=True, privacy="local")
    finally:
        store.close()
    assert len(snap.agent_registry) == 2
    assert snap.agent_registry[0]["agent_id"] == "agent-abc-12345678"
    assert snap.agent_registry[1]["last_write_at"] is None


# ── MemoryVelocity / _collect_velocity ──────────────────────────────────────


def _make_mock_store_with_velocity_row(row: tuple[int, int, int, int]) -> MagicMock:
    """Build a mock MemoryStore whose backend._cm returns a single velocity row."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = row
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cm = MagicMock()
    mock_cm.get_connection.return_value = mock_conn
    mock_backend = MagicMock()
    mock_backend._cm = mock_cm
    mock_backend._project_id = "test-project"
    mock_backend._agent_id = "test-agent"
    mock_store = MagicMock()
    mock_store._persistence = mock_backend
    return mock_store


def test_collect_velocity_returns_zeros_when_no_cm(tmp_path: Path) -> None:
    """_collect_velocity returns all zeros when the backend has no Postgres _cm."""
    store = MemoryStore(tmp_path)
    try:
        vel = _collect_velocity(store)
    finally:
        store.close()
    assert vel == MemoryVelocity(writes_1h=0, recalls_1h=0, writes_24h=0, recalls_24h=0)


def test_collect_velocity_maps_row_correctly() -> None:
    """_collect_velocity maps Postgres COUNT row to MemoryVelocity fields."""
    # Row order: (writes_1h, writes_24h, recalls_1h, recalls_24h)
    mock_store = _make_mock_store_with_velocity_row((5, 20, 2, 8))
    vel = _collect_velocity(mock_store)
    assert vel.writes_1h == 5
    assert vel.writes_24h == 20
    assert vel.recalls_1h == 2
    assert vel.recalls_24h == 8


def test_collect_velocity_returns_zeros_on_none_row() -> None:
    """_collect_velocity returns all-zero when cursor returns None."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: s
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = None
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur
    mock_cm = MagicMock()
    mock_cm.get_connection.return_value = mock_conn
    mock_backend = MagicMock()
    mock_backend._cm = mock_cm
    mock_backend._project_id = "test-project"
    mock_backend._agent_id = "test-agent"
    mock_store = MagicMock()
    mock_store._persistence = mock_backend
    vel = _collect_velocity(mock_store)
    assert vel == MemoryVelocity()


def test_collect_velocity_returns_zeros_on_exception() -> None:
    """_collect_velocity returns all-zero when an exception is raised."""
    mock_cm = MagicMock()
    mock_cm.get_connection.side_effect = Exception("connection refused")
    mock_backend = MagicMock()
    mock_backend._cm = mock_cm
    mock_backend._project_id = "test-project"
    mock_backend._agent_id = "test-agent"
    mock_store = MagicMock()
    mock_store._persistence = mock_backend
    vel = _collect_velocity(mock_store)
    assert vel == MemoryVelocity()


def test_collect_velocity_handles_null_counts_as_zero() -> None:
    """Null values in the COUNT row (edge case) coerce to 0."""
    mock_store = _make_mock_store_with_velocity_row((None, None, None, None))  # type: ignore[arg-type]
    vel = _collect_velocity(mock_store)
    assert vel.writes_1h == 0
    assert vel.recalls_1h == 0
    assert vel.writes_24h == 0
    assert vel.recalls_24h == 0


def test_collect_velocity_no_project_id_returns_zeros() -> None:
    """_collect_velocity returns zeros when _project_id is None (pre-migration)."""
    mock_cm = MagicMock()
    mock_backend = MagicMock()
    mock_backend._cm = mock_cm
    mock_backend._project_id = None
    mock_backend._agent_id = "test-agent"
    mock_store = MagicMock()
    mock_store._persistence = mock_backend
    vel = _collect_velocity(mock_store)
    assert vel == MemoryVelocity()


def test_build_visual_snapshot_includes_velocity_field(tmp_path: Path) -> None:
    """build_visual_snapshot includes a velocity field (zeros on in-memory backend)."""
    store = MemoryStore(tmp_path)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()
    assert hasattr(snap, "velocity")
    assert isinstance(snap.velocity, MemoryVelocity)
    # In-memory backend has no Postgres _cm → all zeros
    assert snap.velocity.writes_1h == 0
    assert snap.velocity.recalls_1h == 0
    assert snap.velocity.writes_24h == 0
    assert snap.velocity.recalls_24h == 0


def test_snapshot_json_includes_velocity_keys(tmp_path: Path) -> None:
    """snapshot_to_json includes velocity fields in output JSON."""
    store = MemoryStore(tmp_path)
    try:
        raw = snapshot_to_json(build_visual_snapshot(store, skip_diagnostics=True))
    finally:
        store.close()
    data = json.loads(raw)
    assert "velocity" in data
    v = data["velocity"]
    assert "writes_1h" in v
    assert "recalls_1h" in v
    assert "writes_24h" in v
    assert "recalls_24h" in v


# ---------------------------------------------------------------------------
# STORY-065.7: RetrievalMetrics and _collect_retrieval_metrics tests
# ---------------------------------------------------------------------------


def test_retrieval_metrics_defaults() -> None:
    """RetrievalMetrics defaults to zeros."""
    rm = RetrievalMetrics()
    assert rm.total_queries == 0
    assert rm.bm25_hits == 0
    assert rm.vector_hits == 0
    assert rm.rrf_fusions == 0
    assert rm.mean_latency_ms == 0.0


def test_collect_retrieval_metrics_returns_zeros_when_no_queries() -> None:
    """_collect_retrieval_metrics returns RetrievalMetrics with zeros if no queries run."""
    import tapps_brain.otel_tracer as _otel

    # Save original counter state and reset to ensure clean test
    orig = (
        _otel._rm_recall_total,
        _otel._rm_bm25_candidates,
        _otel._rm_vector_candidates,
        _otel._rm_rrf_fusions,
        _otel._rm_latency_sum_ms,
        _otel._rm_latency_count,
    )
    try:
        _otel._rm_recall_total = 0
        _otel._rm_bm25_candidates = 0
        _otel._rm_vector_candidates = 0
        _otel._rm_rrf_fusions = 0
        _otel._rm_latency_sum_ms = 0.0
        _otel._rm_latency_count = 0

        rm = _collect_retrieval_metrics()
        assert rm.total_queries == 0
        assert rm.bm25_hits == 0
        assert rm.vector_hits == 0
        assert rm.rrf_fusions == 0
        assert rm.mean_latency_ms == 0.0
    finally:
        _otel._rm_recall_total = orig[0]
        _otel._rm_bm25_candidates = orig[1]
        _otel._rm_vector_candidates = orig[2]
        _otel._rm_rrf_fusions = orig[3]
        _otel._rm_latency_sum_ms = orig[4]
        _otel._rm_latency_count = orig[5]


def test_collect_retrieval_metrics_reflects_increments() -> None:
    """_collect_retrieval_metrics reads incremented values from otel_tracer accumulators."""
    import tapps_brain.otel_tracer as _otel

    orig = (
        _otel._rm_recall_total,
        _otel._rm_bm25_candidates,
        _otel._rm_vector_candidates,
        _otel._rm_rrf_fusions,
        _otel._rm_latency_sum_ms,
        _otel._rm_latency_count,
    )
    try:
        _otel._rm_recall_total = 5
        _otel._rm_bm25_candidates = 12
        _otel._rm_vector_candidates = 8
        _otel._rm_rrf_fusions = 3
        _otel._rm_latency_sum_ms = 250.0
        _otel._rm_latency_count = 5  # mean = 50.0

        rm = _collect_retrieval_metrics()
        assert rm.total_queries == 5
        assert rm.bm25_hits == 12
        assert rm.vector_hits == 8
        assert rm.rrf_fusions == 3
        assert abs(rm.mean_latency_ms - 50.0) < 0.001
    finally:
        _otel._rm_recall_total = orig[0]
        _otel._rm_bm25_candidates = orig[1]
        _otel._rm_vector_candidates = orig[2]
        _otel._rm_rrf_fusions = orig[3]
        _otel._rm_latency_sum_ms = orig[4]
        _otel._rm_latency_count = orig[5]


def test_collect_retrieval_metrics_fallback_when_import_fails() -> None:
    """_collect_retrieval_metrics returns zeros when otel_tracer is unavailable."""
    with patch.dict(sys.modules, {"tapps_brain.otel_tracer": None}):
        rm = _collect_retrieval_metrics()
    assert rm.total_queries == 0
    assert rm.mean_latency_ms == 0.0


def test_snapshot_includes_retrieval_metrics(tmp_path: Path) -> None:
    """build_visual_snapshot includes retrieval_metrics with all 5 fields."""
    store = MemoryStore(tmp_path)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()
    rm = snap.retrieval_metrics
    assert isinstance(rm, RetrievalMetrics)
    assert isinstance(rm.total_queries, int)
    assert isinstance(rm.bm25_hits, int)
    assert isinstance(rm.vector_hits, int)
    assert isinstance(rm.rrf_fusions, int)
    assert isinstance(rm.mean_latency_ms, float)
    assert rm.total_queries >= 0
    assert rm.mean_latency_ms >= 0.0


def test_snapshot_json_includes_retrieval_metrics(tmp_path: Path) -> None:
    """snapshot_to_json serializes retrieval_metrics with all 5 expected keys."""
    store = MemoryStore(tmp_path)
    try:
        raw = snapshot_to_json(build_visual_snapshot(store, skip_diagnostics=True))
    finally:
        store.close()
    data = json.loads(raw)
    assert "retrieval_metrics" in data
    rm = data["retrieval_metrics"]
    assert "total_queries" in rm
    assert "bm25_hits" in rm
    assert "vector_hits" in rm
    assert "rrf_fusions" in rm
    assert "mean_latency_ms" in rm


def test_otel_tracer_increment_functions() -> None:
    """rm_* helper functions in otel_tracer update module-level counters."""
    import tapps_brain.otel_tracer as _otel
    from tapps_brain.otel_tracer import (
        get_retrieval_meter_snapshot,
        rm_add_bm25_candidates,
        rm_add_recall_latency_ms,
        rm_add_vector_candidates,
        rm_increment_recall_total,
        rm_increment_rrf_fusions,
    )

    orig = (
        _otel._rm_recall_total,
        _otel._rm_bm25_candidates,
        _otel._rm_vector_candidates,
        _otel._rm_rrf_fusions,
        _otel._rm_latency_sum_ms,
        _otel._rm_latency_count,
    )
    try:
        _otel._rm_recall_total = 0
        _otel._rm_bm25_candidates = 0
        _otel._rm_vector_candidates = 0
        _otel._rm_rrf_fusions = 0
        _otel._rm_latency_sum_ms = 0.0
        _otel._rm_latency_count = 0

        rm_increment_recall_total()
        rm_increment_recall_total()
        rm_add_bm25_candidates(5)
        rm_add_vector_candidates(3)
        rm_increment_rrf_fusions()
        rm_add_recall_latency_ms(100.0)
        rm_add_recall_latency_ms(200.0)

        snap = get_retrieval_meter_snapshot()
        assert snap["total_queries"] == 2
        assert snap["bm25_hits"] == 5
        assert snap["vector_hits"] == 3
        assert snap["rrf_fusions"] == 1
        assert abs(snap["mean_latency_ms"] - 150.0) < 0.001
    finally:
        _otel._rm_recall_total = orig[0]
        _otel._rm_bm25_candidates = orig[1]
        _otel._rm_vector_candidates = orig[2]
        _otel._rm_rrf_fusions = orig[3]
        _otel._rm_latency_sum_ms = orig[4]
        _otel._rm_latency_count = orig[5]


def test_rm_add_bm25_candidates_ignores_nonpositive() -> None:
    """rm_add_bm25_candidates ignores n <= 0."""
    import tapps_brain.otel_tracer as _otel
    from tapps_brain.otel_tracer import rm_add_bm25_candidates

    orig = _otel._rm_bm25_candidates
    try:
        _otel._rm_bm25_candidates = 10
        rm_add_bm25_candidates(0)
        rm_add_bm25_candidates(-5)
        assert _otel._rm_bm25_candidates == 10
    finally:
        _otel._rm_bm25_candidates = orig


def test_rm_add_recall_latency_ignores_negative() -> None:
    """rm_add_recall_latency_ms ignores negative values."""
    import tapps_brain.otel_tracer as _otel
    from tapps_brain.otel_tracer import rm_add_recall_latency_ms

    orig_sum = _otel._rm_latency_sum_ms
    orig_count = _otel._rm_latency_count
    try:
        _otel._rm_latency_sum_ms = 0.0
        _otel._rm_latency_count = 0
        rm_add_recall_latency_ms(-1.0)
        assert _otel._rm_latency_count == 0
    finally:
        _otel._rm_latency_sum_ms = orig_sum
        _otel._rm_latency_count = orig_count


def test_get_retrieval_latency_detail_empty() -> None:
    """get_retrieval_latency_detail returns null percentiles when no samples."""
    import tapps_brain.otel_tracer as _otel
    from tapps_brain.otel_tracer import get_retrieval_latency_detail

    orig_samples = _otel._rm_latency_samples
    try:
        _otel._rm_latency_samples = []
        detail = get_retrieval_latency_detail()
        assert detail["latency_p50_ms"] is None
        assert detail["latency_p95_ms"] is None
        assert detail["latency_p99_ms"] is None
        assert detail["latency_histogram"] is None
    finally:
        _otel._rm_latency_samples = orig_samples


def test_get_retrieval_latency_detail_with_samples() -> None:
    """Percentiles and histogram reflect recorded recall latencies."""
    import tapps_brain.otel_tracer as _otel
    from tapps_brain.otel_tracer import get_retrieval_latency_detail, rm_add_recall_latency_ms

    orig_samples = _otel._rm_latency_samples
    try:
        _otel._rm_latency_samples = []
        for ms in (5.0, 15.0, 30.0, 80.0, 120.0):
            rm_add_recall_latency_ms(ms)
        detail = get_retrieval_latency_detail()
        assert isinstance(detail["latency_p50_ms"], float)
        assert isinstance(detail["latency_p95_ms"], float)
        assert isinstance(detail["latency_p99_ms"], float)
        hist = detail["latency_histogram"]
        assert isinstance(hist, list)
        assert hist
        assert sum(int(row["count"]) for row in hist) == 5
    finally:
        _otel._rm_latency_samples = orig_samples


def test_retrieval_latency_p50_interpolates_even_samples() -> None:
    """Even-length latency samples must not bias p50 to the upper nearest-rank."""
    import tapps_brain.otel_tracer as _otel
    from tapps_brain.otel_tracer import get_retrieval_latency_detail, rm_add_recall_latency_ms

    orig_samples = _otel._rm_latency_samples
    try:
        _otel._rm_latency_samples = []
        rm_add_recall_latency_ms(1.0)
        rm_add_recall_latency_ms(100.0)
        detail = get_retrieval_latency_detail()
        assert detail["latency_p50_ms"] == pytest.approx(50.5)
    finally:
        _otel._rm_latency_samples = orig_samples


def test_collect_retrieval_detail_from_store(tmp_path: Path) -> None:
    """_collect_retrieval_detail includes embedding model when provider is set."""
    store = MemoryStore(tmp_path)
    try:
        provider = MagicMock()
        provider.model_id = "sentence-transformers/all-MiniLM-L6-v2"
        store._embedding_provider = provider
        detail = _collect_retrieval_detail(store)
        assert isinstance(detail, RetrievalDetail)
        assert detail.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    finally:
        store.close()


def test_snapshot_includes_retrieval_detail(tmp_path: Path) -> None:
    """build_visual_snapshot includes retrieval latency block."""
    store = MemoryStore(tmp_path)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()
    assert snap.retrieval is not None
    assert isinstance(snap.retrieval, RetrievalDetail)


def test_snapshot_json_includes_retrieval_block(tmp_path: Path) -> None:
    """snapshot_to_json serializes retrieval with latency fields."""
    store = MemoryStore(tmp_path)
    try:
        raw = snapshot_to_json(build_visual_snapshot(store, skip_diagnostics=True))
    finally:
        store.close()
    data = json.loads(raw)
    assert "retrieval" in data
    retrieval = data["retrieval"]
    assert "latency_p50_ms" in retrieval
    assert "latency_p95_ms" in retrieval
    assert "latency_p99_ms" in retrieval
    assert "latency_histogram" in retrieval


# ---------------------------------------------------------------------------
# STORY-069.7: per-tenant filtering of diagnostics_history + feedback_events
# ---------------------------------------------------------------------------


def _fake_store_with_tenant_data(
    *,
    project_id: str = "tenant-a",
    extra_history: list[dict] | None = None,
    extra_feedback: list | None = None,
) -> MagicMock:
    """Build a minimal MemoryStore-like mock for build_visual_snapshot."""
    from tapps_brain.feedback import FeedbackEvent

    store = MagicMock()
    store._project_id = project_id

    # health() report
    report = StoreHealthReport(
        entry_count=0,
        tier_distribution={},
        schema_version=1,
        store_path="/tmp/mock",
        profile_name="default",
        federation_enabled=False,
    )
    store.health.return_value = report
    store.list_all.return_value = []
    store.vector_row_count = 0

    # diagnostics() is called when skip_diagnostics is False; return a stub.
    _diag = MagicMock()
    _diag.composite_score = 0.9
    _diag.circuit_state = "closed"
    _diag.recorded_at = "2026-04-14T00:00:00+00:00"
    store.diagnostics.return_value = _diag

    # diagnostics_history() → mix of project-scoped + a legacy row (no pid)
    history = [
        {
            "id": "d1",
            "recorded_at": "2026-04-14T00:00:00+00:00",
            "composite_score": 0.9,
            "dimension_scores": "{}",
            "circuit_state": "closed",
            "full_report": {},
            "project_id": project_id,
        },
        {
            "id": "d2",
            "recorded_at": "2026-04-13T00:00:00+00:00",
            "composite_score": 0.8,
            "dimension_scores": "{}",
            "circuit_state": "closed",
            "full_report": {},
            "project_id": None,  # legacy
        },
    ]
    if extra_history:
        history.extend(extra_history)
    store.diagnostics_history.return_value = history

    events = [
        FeedbackEvent(
            event_type="recall_rated",
            entry_key="k1",
            utility_score=1.0,
            project_id=project_id,
        ),
        FeedbackEvent(event_type="gap_reported", project_id=None),  # legacy
    ]
    if extra_feedback:
        events.extend(extra_feedback)
    store.query_feedback.return_value = events

    # agent_scope_counts etc. — provide empty dicts via attribute access
    store._hive_store = None
    return store


def test_store_binds_project_id_into_structured_logs(tmp_path: Path) -> None:
    """STORY-069.7: save/recall/feedback bind project_id into the logger.

    Uses structlog.testing.capture_logs to intercept bound context.
    """
    import structlog
    from structlog.testing import capture_logs

    store = MemoryStore(tmp_path)
    try:
        with capture_logs() as events:
            # Re-configure structlog for the test so DEBUG events are emitted
            # (the MCP server configures CRITICAL at import time globally).
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(0),
            )
            store.save(key="log-k", value="v", tier="pattern", agent_scope="private")
            store.recall("log-k")
            store.record_feedback("recall_rated", entry_key="log-k", utility_score=1.0)

        ops = {e.get("op") for e in events if "op" in e}
        assert {"save", "recall", "feedback"}.issubset(ops), (
            f"Expected save/recall/feedback bound ops, got {ops}"
        )
        # Each captured event must carry project_id (may be None when backend
        # doesn't expose one — but the key MUST be in the bound context).
        for ev in events:
            if ev.get("op") in {"save", "recall", "feedback"}:
                assert "project_id" in ev, f"project_id missing from event: {ev}"
    finally:
        store.close()


def test_build_visual_snapshot_carries_project_id_in_history_and_events() -> None:
    """STORY-069.7: diagnostics_history and feedback_events include project_id."""
    store = _fake_store_with_tenant_data(project_id="tenant-a")
    snap = build_visual_snapshot(store, skip_diagnostics=True, privacy="standard")
    assert len(snap.diagnostics_history) == 2
    assert snap.diagnostics_history[0]["project_id"] == "tenant-a"
    # Legacy row's project_id stays as None (don't silently impute).
    assert snap.diagnostics_history[1]["project_id"] is None

    assert len(snap.feedback_events) == 2
    assert snap.feedback_events[0]["project_id"] == "tenant-a"
    assert snap.feedback_events[1]["project_id"] is None


def test_filter_snapshot_by_project_excludes_legacy_rows() -> None:
    """STORY-069.7: the filter helper drops rows missing project_id."""
    from tapps_brain.http_adapter import _filter_snapshot_by_project

    payload = {
        "diagnostics_history": [
            {"id": "d1", "project_id": "tenant-a"},
            {"id": "d2", "project_id": "tenant-b"},
            {"id": "d3", "project_id": None},
        ],
        "feedback_events": [
            {"id": "e1", "project_id": "tenant-a"},
            {"id": "e2", "project_id": None},
        ],
        "schema_version": 2,
    }
    out = _filter_snapshot_by_project(payload, "tenant-a")
    assert [r["id"] for r in out["diagnostics_history"]] == ["d1"]
    assert [r["id"] for r in out["feedback_events"]] == ["e1"]
    # Unrelated keys pass through unchanged.
    assert out["schema_version"] == 2
    # Unknown project yields empty arrays, not missing keys.
    out2 = _filter_snapshot_by_project(payload, "ghost")
    assert out2["diagnostics_history"] == []
    assert out2["feedback_events"] == []


# ---------------------------------------------------------------------------
# STORY-078.2: snapshot SQL aggregates (no list_all on Postgres path)
# ---------------------------------------------------------------------------


def _deterministic_golden_entries(count: int = 100) -> list[Any]:
    from tapps_brain.models import MemoryEntry, MemoryTier

    tiers = [
        MemoryTier.architectural,
        MemoryTier.pattern,
        MemoryTier.procedural,
        MemoryTier.context,
    ]
    scopes = ["private", "hive", "domain"]
    entries: list[Any] = []
    for i in range(count):
        entries.append(
            MemoryEntry(
                key=f"k{i:03d}",
                value=f"secret-body-{i}",
                tier=tiers[i % len(tiers)],
                agent_scope=scopes[i % len(scopes)],
                access_count=i % 25,
                total_access_count=i * 2,
                useful_access_count=i,
                tags=[f"tag-{i % 5}", "common"] if i % 2 == 0 else ["common"],
                memory_group=f"group-{i % 3}" if i % 4 == 0 else None,
            )
        )
    return entries


def _aggregate_snapshot_fields(snap: Any) -> dict[str, Any]:
    return {
        "agent_scope_counts": snap.agent_scope_counts,
        "access_stats": snap.access_stats.model_dump() if snap.access_stats else None,
        "memory_group_count": snap.memory_group_count,
        "memory_group_counts": snap.memory_group_counts,
        "tag_stats": [t.model_dump() for t in (snap.tag_stats or [])],
    }


def _seed_store_from_entries(store: MemoryStore, entries: list[Any]) -> None:
    for entry in entries:
        store.save(
            key=entry.key,
            value=entry.value,
            tier=entry.tier.value,
            agent_scope=entry.agent_scope,
            tags=entry.tags,
            memory_group=entry.memory_group,
        )
        loaded = store.get(entry.key)
        assert loaded is not None
        loaded.access_count = entry.access_count
        loaded.total_access_count = entry.total_access_count
        loaded.useful_access_count = entry.useful_access_count
        store._persistence.save(loaded)


def test_snapshot_empty_store_valid_scorecard(tmp_path: Path) -> None:
    """Empty store → zero counts, valid scorecard."""
    store = MemoryStore(tmp_path)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()

    assert snap.access_stats is not None
    assert snap.access_stats.sum_access_count == 0
    assert all(b.count == 0 for b in snap.access_stats.buckets)
    assert snap.agent_scope_counts == {}
    assert snap.memory_group_count == 0
    assert len(snap.scorecard) >= 8


def test_snapshot_golden_100_entry_parity(tmp_path: Path) -> None:
    """100-entry fixture → list_all path matches aggregate path fields."""
    entries = _deterministic_golden_entries(100)
    store = MemoryStore(tmp_path)
    try:
        _seed_store_from_entries(store, entries)
        snap_list_all = build_visual_snapshot(store, skip_diagnostics=True, privacy="local")

        aggregates = _snapshot_aggregates_from_entries(store.list_all())
        store._persistence.snapshot_aggregates = lambda project_id: aggregates  # type: ignore[attr-defined]

        with patch.object(
            store,
            "list_all",
            side_effect=AssertionError("list_all must not be called on aggregate path"),
        ):
            snap_agg = build_visual_snapshot(store, skip_diagnostics=True, privacy="local")
    finally:
        store.close()

    assert _aggregate_snapshot_fields(snap_list_all) == _aggregate_snapshot_fields(snap_agg)
    assert "secret-body" not in snapshot_to_json(snap_agg)


def test_build_visual_snapshot_postgres_skips_list_all() -> None:
    """Postgres aggregate path must not invoke MemoryStore.list_all()."""
    from tapps_brain.postgres_private import PostgresPrivateBackend

    class _FakeCM:
        def __init__(self, obj: Any) -> None:
            self._obj = obj

        def __enter__(self) -> Any:
            return self._obj

        def __exit__(self, *args: Any) -> bool:
            return False

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

    cur.fetchone.side_effect = [
        (10, 20, 5, 2, 1, 1, 0, 0, 3),  # access stats
    ]
    cur.fetchall.side_effect = [
        [("pattern", 2), ("context", 1)],  # tiers
        [("private", 2), ("hive", 1)],  # scopes
        [("team-a", 1)],  # groups
        [("alpha", 2)],  # tags
    ]

    cm = MagicMock()
    cm.get_connection.return_value = _FakeCM(conn)
    cm.project_context.return_value = _FakeCM(conn)

    backend = PostgresPrivateBackend(cm, project_id="proj-abc", agent_id="agent-1")
    store = MagicMock()
    store._persistence = backend
    store._project_id = "proj-abc"
    store._hive_store = None
    store.vector_row_count = 0
    store.health.return_value = StoreHealthReport(
        entry_count=3,
        tier_distribution={"pattern": 2, "context": 1},
        schema_version=1,
        store_path="/tmp/mock",
        profile_name="default",
        federation_enabled=False,
    )
    store.diagnostics_history.return_value = []
    store.query_feedback.return_value = []

    with patch.object(
        store,
        "list_all",
        side_effect=AssertionError("list_all must not be called"),
    ):
        snap = build_visual_snapshot(store, skip_diagnostics=True, privacy="local")

    assert snap.agent_scope_counts == {"hive": 1, "private": 2}
    assert snap.memory_group_counts == {"team-a": 1}
    assert snap.tag_stats is not None
    assert snap.tag_stats[0].tag == "alpha"


def test_postgres_snapshot_aggregates_empty() -> None:
    """Empty Postgres scope returns zeroed SnapshotAggregates."""
    from tapps_brain.postgres_private import PostgresPrivateBackend

    class _FakeCM:
        def __init__(self, obj: Any) -> None:
            self._obj = obj

        def __enter__(self) -> Any:
            return self._obj

        def __exit__(self, *args: Any) -> bool:
            return False

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = (0, 0, 0, 0, 0, 0, 0, 0, 0)
    cur.fetchall.return_value = []

    cm = MagicMock()
    cm.get_connection.return_value = _FakeCM(conn)
    cm.project_context.return_value = _FakeCM(conn)

    backend = PostgresPrivateBackend(cm, project_id="proj-empty", agent_id="agent-1")
    aggs = backend.snapshot_aggregates("proj-empty")

    assert isinstance(aggs, SnapshotAggregates)
    assert aggs.tier_distribution == {}
    assert aggs.agent_scope_counts == {}
    assert aggs.access_stats.sum_access_count == 0
    assert all(b.count == 0 for b in aggs.access_stats.buckets)


@pytest.mark.requires_postgres
def test_snapshot_5000_entry_postgres_under_3s(tmp_path: Path) -> None:
    """5000-entry Postgres fixture builds snapshot in <3s without list_all."""
    from tapps_brain.models import MemoryEntry, MemoryTier
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_migrations import apply_private_migrations
    from tapps_brain.postgres_private import PostgresPrivateBackend

    dsn = os.environ["TAPPS_BRAIN_DATABASE_URL"]
    apply_private_migrations(dsn)

    project_id = f"snap-perf-{uuid.uuid4().hex[:8]}"
    agent_id = f"agent-{uuid.uuid4().hex[:8]}"
    cm = PostgresConnectionManager(dsn)
    backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    entries = [
        MemoryEntry(
            key=f"perf-{i:05d}",
            value=f"payload-{i}",
            tier=MemoryTier.pattern,
            agent_scope="private" if i % 2 == 0 else "hive",
            access_count=i % 30,
            total_access_count=i,
            useful_access_count=i // 2,
            tags=[f"tag-{i % 7}"],
            memory_group=f"g-{i % 11}" if i % 3 == 0 else None,
        )
        for i in range(5000)
    ]
    try:
        backend.save_many(entries)
        store = MemoryStore(tmp_path, private_backend=backend)
        try:
            with patch.object(
                store,
                "list_all",
                side_effect=AssertionError("list_all must not be called"),
            ):
                started = time.perf_counter()
                snap = build_visual_snapshot(store, skip_diagnostics=True, privacy="local")
                elapsed = time.perf_counter() - started
        finally:
            store.close()
    finally:
        backend.close()

    assert elapsed < 3.0
    assert snap.health["entry_count"] == 5000
    assert snap.access_stats is not None
    assert snap.access_stats.sum_access_count > 0


# ---------------------------------------------------------------------------
# build_kg_graph — KG focus-view serializer (P1)
# ---------------------------------------------------------------------------


def _neighbor_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "edge_id": "e1",
        "predicate": "depends_on",
        "edge_confidence": 0.8,
        "edge_status": "active",
        "contradicted": False,
        "stability": 12.5,
        "evidence_count": 3,
        "neighbor_id": "n1",
        "entity_type": "module",
        "canonical_name": "auth",
        "entity_confidence": 0.9,
        "hop": 1,
    }
    row.update(overrides)
    return row


def test_build_kg_graph_empty_neighbors_returns_lone_root() -> None:
    graph = build_kg_graph("root-1", [], root_label="Root")
    assert graph["root"] == "root-1"
    assert graph["node_count"] == 1
    assert graph["edge_count"] == 0
    assert graph["nodes"][0]["is_root"] is True
    assert graph["nodes"][0]["label"] == "Root"


def test_build_kg_graph_maps_edge_and_node_signals() -> None:
    graph = build_kg_graph("root-1", [_neighbor_row()])
    assert graph["node_count"] == 2
    assert graph["edge_count"] == 1
    edge = graph["edges"][0]
    assert edge["source"] == "root-1"
    assert edge["target"] == "n1"
    assert edge["predicate"] == "depends_on"
    assert edge["confidence"] == 0.8
    assert edge["status"] == "active"
    assert edge["contradicted"] is False
    assert edge["stability"] == 12.5
    assert edge["evidence_count"] == 3
    neighbor = next(n for n in graph["nodes"] if not n["is_root"])
    assert neighbor["label"] == "auth"
    assert neighbor["type"] == "module"
    assert neighbor["confidence"] == 0.9


def test_build_kg_graph_dedupes_repeated_neighbor_into_one_node_two_edges() -> None:
    rows = [
        _neighbor_row(edge_id="e1", predicate="depends_on"),
        _neighbor_row(edge_id="e2", predicate="calls"),
    ]
    graph = build_kg_graph("root-1", rows)
    assert graph["node_count"] == 2  # root + single deduped neighbor
    assert graph["edge_count"] == 2
    assert {e["predicate"] for e in graph["edges"]} == {"depends_on", "calls"}


def test_build_kg_graph_skips_rows_without_neighbor_id() -> None:
    graph = build_kg_graph("root-1", [_neighbor_row(neighbor_id="")])
    assert graph["node_count"] == 1
    assert graph["edge_count"] == 0


def test_build_kg_graph_carries_contradicted_and_stale_signals() -> None:
    row = _neighbor_row(contradicted=True, edge_status="stale", edge_confidence=0.1)
    graph = build_kg_graph("root-1", [row])
    edge = graph["edges"][0]
    assert edge["contradicted"] is True
    assert edge["status"] == "stale"
    assert edge["confidence"] == 0.1


def test_build_kg_graph_tolerates_missing_numeric_fields() -> None:
    row = _neighbor_row(edge_confidence=None, entity_confidence=None, evidence_count=None)
    graph = build_kg_graph("root-1", [row])
    edge = graph["edges"][0]
    assert edge["confidence"] == 0.0
    assert edge["evidence_count"] == 0
    neighbor = next(n for n in graph["nodes"] if not n["is_root"])
    assert neighbor["confidence"] == 0.0


# ---------------------------------------------------------------------------
# build_kg_health — KG-graph health scorecard (P5)
# ---------------------------------------------------------------------------


def _health_counts(**overrides: int) -> dict[str, int]:
    counts: dict[str, int] = {
        "entities_active": 100,
        "orphan_entities": 5,
        "edges_total": 200,
        "edges_active": 190,
        "edges_stale": 5,
        "edges_superseded": 5,
        "edges_contradicted": 2,
    }
    counts.update(overrides)
    return counts


def test_build_kg_health_healthy_graph_is_ok() -> None:
    h = build_kg_health(_health_counts())
    assert h["status"] == "ok"
    assert h["recommendations"] == []
    assert h["orphan_ratio"] == 0.05
    assert h["stale_ratio"] == 0.05
    assert h["contradicted_ratio"] == 0.01


def test_build_kg_health_empty_graph_no_division_error() -> None:
    h = build_kg_health(
        {
            "entities_active": 0,
            "orphan_entities": 0,
            "edges_total": 0,
            "edges_active": 0,
            "edges_stale": 0,
            "edges_superseded": 0,
            "edges_contradicted": 0,
        }
    )
    assert h["status"] == "ok"
    assert h["orphan_ratio"] == 0.0
    assert h["stale_ratio"] == 0.0
    assert h["contradicted_ratio"] == 0.0


def test_build_kg_health_orphan_heavy_warns() -> None:
    h = build_kg_health(_health_counts(entities_active=100, orphan_entities=40))
    assert h["status"] == "warn"
    assert any("orphaned" in r for r in h["recommendations"])


def test_build_kg_health_contradicted_over_threshold_warns() -> None:
    h = build_kg_health(_health_counts(edges_total=100, edges_contradicted=10))
    assert h["status"] == "warn"
    assert any("contradicted" in r for r in h["recommendations"])


def test_build_kg_health_multiple_issues_degrade() -> None:
    h = build_kg_health(
        _health_counts(
            entities_active=100,
            orphan_entities=50,  # 0.50 > 0.30
            edges_total=100,
            edges_stale=30,
            edges_superseded=20,  # 0.50 > 0.40
            edges_contradicted=10,  # 0.10 > 0.05
        )
    )
    assert h["status"] == "degraded"
    assert len(h["recommendations"]) == 3


def test_build_kg_health_tolerates_missing_and_negative_counts() -> None:
    h = build_kg_health({"entities_active": -5})
    assert h["status"] == "ok"
    assert h["entities_active"] == 0
    assert h["edges_total"] == 0
