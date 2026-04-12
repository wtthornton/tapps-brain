"""Tests for brain visual JSON snapshot (aggregated metadata only)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.metrics import StoreHealthReport
from tapps_brain.store import MemoryStore
from tapps_brain.visual_snapshot import (
    VISUAL_SNAPSHOT_SCHEMA_VERSION,
    DiagnosticsSummary,
    HiveHealthSummary,
    NamespaceDetail,
    _access_stats_from_entries,
    _build_scorecard,
    _collect_agent_registry,
    _collect_hive_health,
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
    report = _make_report(rate_limit_minute_anomalies=2, rate_limit_session_anomalies=1)
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
# NamespaceDetail and HiveHealthSummary.namespace_detail
# ---------------------------------------------------------------------------


def test_namespace_detail_defaults() -> None:
    """NamespaceDetail has sensible defaults and accepts all fields."""
    nd = NamespaceDetail(namespace="repo-brain", entry_count=42, last_write_at="2026-01-01T00:00:00Z")
    assert nd.namespace == "repo-brain"
    assert nd.entry_count == 42
    assert nd.last_write_at == "2026-01-01T00:00:00Z"


def test_namespace_detail_none_last_write() -> None:
    nd = NamespaceDetail(namespace="empty-ns")
    assert nd.entry_count == 0
    assert nd.last_write_at is None


def test_hive_health_summary_namespace_detail_default() -> None:
    hh = HiveHealthSummary()
    assert hh.namespace_detail == []


def test_hive_health_summary_namespace_detail_populated() -> None:
    details = [
        NamespaceDetail(namespace="alpha", entry_count=10, last_write_at="2026-01-01T00:00:00Z"),
        NamespaceDetail(namespace="beta", entry_count=5, last_write_at=None),
    ]
    hh = HiveHealthSummary(connected=True, status="ok", entries=15, agents=2, namespace_detail=details)
    assert len(hh.namespace_detail) == 2
    assert hh.namespace_detail[0].namespace == "alpha"
    assert hh.namespace_detail[1].entry_count == 5


def test_hive_health_summary_serialises_namespace_detail() -> None:
    """HiveHealthSummary with namespace_detail round-trips through model_dump/model_validate."""
    details = [NamespaceDetail(namespace="ns1", entry_count=7, last_write_at="2026-04-01T10:00:00Z")]
    hh = HiveHealthSummary(connected=True, status="ok", entries=7, agents=1, namespace_detail=details)
    dumped = hh.model_dump()
    restored = HiveHealthSummary.model_validate(dumped)
    assert restored.namespace_detail[0].namespace == "ns1"
    assert restored.namespace_detail[0].entry_count == 7


def test_collect_hive_health_uses_namespace_detail_list(tmp_path: Path) -> None:
    """_collect_hive_health() populates namespace_detail when hive has namespace_detail_list()."""
    store = MemoryStore(tmp_path)
    try:
        mock_hive = MagicMock()
        mock_hive.namespace_detail_list.return_value = [
            {"namespace": "personal", "entry_count": 20, "last_write_at": "2026-04-01T12:00:00+00:00"},
            {"namespace": "repo-brain", "entry_count": 55, "last_write_at": None},
        ]
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["agent-a", "agent-b", "agent-c"]

        with (
            patch("tapps_brain.backends.AgentRegistry", return_value=mock_registry),
            patch("tapps_brain.backends.resolve_hive_backend_from_env", return_value=mock_hive),
        ):
            result = _collect_hive_health(store)

        assert result.connected is True
        assert result.status == "ok"
        assert result.entries == 75
        assert result.agents == 3
        assert len(result.namespace_detail) == 2
        # sorted by namespace name
        assert result.namespace_detail[0].namespace == "personal"
        assert result.namespace_detail[0].entry_count == 20
        assert result.namespace_detail[1].namespace == "repo-brain"
        assert result.namespace_detail[1].last_write_at is None
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
            patch("tapps_brain.backends.AgentRegistry", return_value=mock_registry),
            patch("tapps_brain.backends.resolve_hive_backend_from_env", return_value=mock_hive),
        ):
            result = _collect_hive_health(store)

        assert result.connected is True
        assert result.entries == 10
        assert result.agents == 1
        assert result.namespace_detail == []  # no detail when fallback path used
        assert sorted(result.namespaces) == ["alpha", "beta"]
        mock_hive.close.assert_called_once()
    finally:
        store.close()


def test_collect_hive_health_empty_namespaces(tmp_path: Path) -> None:
    """_collect_hive_health() returns empty namespace_detail when hive is fresh."""
    store = MemoryStore(tmp_path)
    try:
        mock_hive = MagicMock()
        mock_hive.namespace_detail_list.return_value = []
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []

        with (
            patch("tapps_brain.backends.AgentRegistry", return_value=mock_registry),
            patch("tapps_brain.backends.resolve_hive_backend_from_env", return_value=mock_hive),
        ):
            result = _collect_hive_health(store)

        assert result.connected is True
        assert result.entries == 0
        assert result.namespace_detail == []
    finally:
        store.close()


def test_collect_hive_health_not_reachable(tmp_path: Path) -> None:
    """_collect_hive_health() returns connected=False when DSN not set."""
    store = MemoryStore(tmp_path)
    try:
        with patch("tapps_brain.backends.resolve_hive_backend_from_env", return_value=None):
            result = _collect_hive_health(store)

        assert result.connected is False
        assert result.status == "skipped"
        assert result.namespace_detail == []
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
        ("agent-alpha-12345678", "repo-brain", "2026-01-01T00:00:00+00:00", "2026-04-10T10:00:00+00:00"),
        ("agent-beta-87654321", "universal", "2026-02-01T00:00:00+00:00", "2026-04-09T08:00:00+00:00"),
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
        with patch("tapps_brain.backends.resolve_hive_backend_from_env", return_value=None):
            snap = build_visual_snapshot(store, skip_diagnostics=True)
    finally:
        store.close()
    assert hasattr(snap, "agent_registry")
    assert isinstance(snap.agent_registry, list)


def test_visual_snapshot_agent_registry_populated_from_hive(tmp_path: Path) -> None:
    """build_visual_snapshot populates agent_registry when hive backend is available."""
    rows = [
        ("agent-abc-12345678", "repo-brain", "2026-01-01T00:00:00+00:00", "2026-04-10T10:00:00+00:00"),
        ("agent-def-abcdefgh", "universal", "2026-02-01T00:00:00+00:00", None),
    ]
    mock_hive = _make_mock_backend_with_rows(rows)
    mock_hive.close = MagicMock()
    store = MemoryStore(tmp_path)
    try:
        with (
            patch("tapps_brain.backends.resolve_hive_backend_from_env", return_value=mock_hive),
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
    mock_hive.close.assert_called_once()
