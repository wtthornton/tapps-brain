"""Unit tests for diagnostics scoring, correlation, breaker, and custom dimensions."""

from __future__ import annotations

import json

import pytest

from tapps_brain.diagnostics import (
    AnomalyDetector,
    CircuitBreaker,
    CircuitState,
    DiagnosticsConfig,
    DiagnosticsReport,
    DimensionScore,
    _dimension_recommendations,
    _hive_namespace_scores,
    adjust_weights_for_correlation,
    hive_recall_multiplier,
    load_custom_dimensions,
    normalize_weights,
    pearson_r,
    run_diagnostics,
)
from tapps_brain.store import MemoryStore


def test_dimension_recommendations_context_heavy_freshness() -> None:
    """Low freshness dominated by context tier yields the structural explanation."""
    scores = {
        "freshness": DimensionScore(
            name="freshness",
            score=0.38,
            raw_details={"context_share": 0.80, "tier_counts": {"context": 80, "procedural": 20}},
        ),
    }
    recs = _dimension_recommendations(scores)
    assert len(recs) == 1
    assert "80% of entries are context tier" in recs[0]
    assert "not data corruption" in recs[0]


def test_dimension_recommendations_low_freshness_not_context() -> None:
    """Low freshness without a context-tier majority yields the generic hint."""
    scores = {
        "freshness": DimensionScore(
            name="freshness",
            score=0.40,
            raw_details={"context_share": 0.10},
        ),
    }
    recs = _dimension_recommendations(scores)
    assert len(recs) == 1
    assert "review entry ages vs tier half-lives" in recs[0]


def test_dimension_recommendations_gc_candidates() -> None:
    """Staleness with GC candidates yields a maintenance gc hint."""
    scores = {
        "staleness": DimensionScore(
            name="staleness",
            score=0.99,
            raw_details={"gc_candidates": 3},
        ),
    }
    recs = _dimension_recommendations(scores)
    assert len(recs) == 1
    assert "3 GC candidate(s)" in recs[0]
    assert "maintenance gc" in recs[0]


def test_dimension_recommendations_healthy_store_silent() -> None:
    """Healthy freshness + zero GC candidates produce no recommendations."""
    scores = {
        "freshness": DimensionScore(
            name="freshness", score=0.90, raw_details={"context_share": 0.80}
        ),
        "staleness": DimensionScore(name="staleness", score=1.0, raw_details={"gc_candidates": 0}),
    }
    assert _dimension_recommendations(scores) == []


def test_normalize_weights_equal_sum() -> None:
    w = normalize_weights({"a": 2.0, "b": 2.0})
    assert abs(w["a"] - 0.5) < 1e-9
    assert abs(w["b"] - 0.5) < 1e-9


def test_normalize_weights_single_positive_key() -> None:
    w = normalize_weights({"only": 2.0})
    assert abs(w["only"] - 1.0) < 1e-9


def test_normalize_weights_all_nonpositive_splits_evenly() -> None:
    w = normalize_weights({"a": 0.0, "b": 0.0})
    assert abs(w["a"] - 0.5) < 1e-9
    assert abs(w["b"] - 0.5) < 1e-9


def test_normalize_weights_empty_dict() -> None:
    assert normalize_weights({}) == {}


def test_pearson_r_perfect_positive() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [2.0, 4.0, 6.0, 8.0]
    r = pearson_r(xs, ys)
    assert r is not None
    assert abs(r - 1.0) < 1e-9


def test_adjust_weights_correlated_pair() -> None:
    dim_names = ["d1", "d2"]
    base = {"d1": 0.5, "d2": 0.5}
    rows = []
    # Strong linear co-movement so Pearson r > 0.7 (constant series have undefined r).
    for i in range(25):
        v = 0.2 + (i / 24.0) * 0.6
        rows.append({"dimensions": {"d1": v, "d2": v * 0.98 + 0.01}})
    w, adj = adjust_weights_for_correlation(dim_names, base, rows, min_rows=20)
    assert adj is True
    assert abs(sum(w.values()) - 1.0) < 1e-6


def test_load_custom_dimension_from_tests_module() -> None:
    dims = load_custom_dimensions(["tests.diagnostics_dimensions.make_always_good"])
    assert len(dims) == 1
    assert dims[0].name == "always_good"


def test_run_diagnostics_empty_store(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        rep = run_diagnostics(store)
        assert 0.0 <= rep.composite_score <= 1.0
        assert "integrity" in rep.dimensions
    finally:
        store.close()


def test_run_diagnostics_with_recall_feedback(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        store.save("fb-k", "value", tier="pattern")
        store.rate_recall("fb-k", rating="helpful", session_id="sess-a")
        rep = run_diagnostics(store)
        re = rep.dimensions.get("retrieval_effectiveness")
        assert re is not None
        assert 0.0 <= re.score <= 1.0
        fr = rep.dimensions.get("freshness")
        assert fr is not None
        assert fr.raw_details.get("tier_counts", {}).get("pattern") == 1
    finally:
        store.close()


def test_run_diagnostics_with_custom_path(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        cfg = DiagnosticsConfig(
            custom_dimension_paths=["tests.diagnostics_dimensions.make_always_good"],
        )
        rep = run_diagnostics(store, config=cfg)
        assert "always_good" in rep.dimensions
        assert rep.dimensions["always_good"].score == 1.0
    finally:
        store.close()


def test_circuit_breaker_transitions() -> None:
    cb = CircuitBreaker()
    cb.transition(0.8)
    assert cb.state == CircuitState.CLOSED
    cb.transition(0.4)
    assert cb.state == CircuitState.DEGRADED
    cb.transition(0.2)
    assert cb.state == CircuitState.OPEN
    cb.state = CircuitState.HALF_OPEN
    cb.transition(0.65)
    assert cb.state == CircuitState.CLOSED


def test_hive_recall_multiplier_states() -> None:
    assert hive_recall_multiplier(CircuitState.CLOSED) == 1.0
    assert hive_recall_multiplier(CircuitState.DEGRADED) == 0.5
    assert hive_recall_multiplier(CircuitState.OPEN) == 0.0


def test_anomaly_detector_steady_scores_no_crash() -> None:
    det = AnomalyDetector(min_obs=5, confirm_window=5)
    rep = DiagnosticsReport(
        composite_score=0.9,
        dimensions={"d": DimensionScore(name="d", score=0.72, raw_details={})},
        recorded_at="t",
    )
    for _ in range(12):
        out = det.detect(rep)
        assert isinstance(out, list)


def test_anomaly_detector_reset_from_history() -> None:
    det = AnomalyDetector(min_obs=1)
    rows = [
        {"dimension_scores": json.dumps({"d": 0.5})},
        {"dimension_scores": json.dumps({"d": 0.55})},
    ]
    det.reset_from_history(rows)
    assert det._ewma.get("d") is not None
    assert det._obs_count.get("d", 0) >= 1


@pytest.mark.skip(
    reason=(
        "DiagnosticsHistoryStore requires a PostgresConnectionManager (ADR-007 stage 2). "
        "The old SQLite db_path API was removed. Use integration tests with a live Postgres "
        "connection to validate diagnostics history persistence."
    )
)
def test_diagnostics_history_dimension_scores_json_roundtrip(tmp_path) -> None:
    from tapps_brain.diagnostics import DiagnosticsHistoryStore

    store = MemoryStore(tmp_path)
    try:
        store.diagnostics(record_history=True)
        hist = store.diagnostics_history(limit=1)
        assert hist
        raw = hist[0]["dimension_scores"]
        d = json.loads(raw)
        assert isinstance(d, dict)
        assert len(d) >= 1
    finally:
        store.close()

    # Direct store path (same DB file)
    db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
    hs = DiagnosticsHistoryStore(db_path)
    try:
        rows = hs.history(limit=2)
        assert rows
    finally:
        hs.close()


def test_maybe_remediate_open_state_returns_actions(tmp_path) -> None:
    from tapps_brain.diagnostics import maybe_remediate

    store = MemoryStore(tmp_path)
    try:
        cb = CircuitBreaker()
        cb.state = CircuitState.OPEN
        rep = DiagnosticsReport(
            composite_score=0.2,
            dimensions={
                "duplication": DimensionScore(name="duplication", score=0.2, raw_details={}),
                "staleness": DimensionScore(name="staleness", score=0.2, raw_details={}),
                "integrity": DimensionScore(name="integrity", score=0.5, raw_details={}),
            },
            recorded_at="t",
        )
        out = maybe_remediate(store, rep, cb, now_mono=0.0)
        assert isinstance(out, list)
    finally:
        store.close()


def test_maybe_remediate_consolidate_and_gc(monkeypatch, tmp_path) -> None:
    from tapps_brain.diagnostics import maybe_remediate

    store = MemoryStore(tmp_path)
    try:
        cb = CircuitBreaker()
        cb.state = CircuitState.OPEN
        cb._last_remediation_mono.clear()
        rep = DiagnosticsReport(
            composite_score=0.2,
            dimensions={
                "duplication": DimensionScore(name="duplication", score=0.4, raw_details={}),
                "staleness": DimensionScore(name="staleness", score=0.4, raw_details={}),
                "integrity": DimensionScore(name="integrity", score=0.5, raw_details={}),
            },
            recorded_at="t",
        )

        monkeypatch.setattr(
            "tapps_brain.auto_consolidation.run_periodic_consolidation_scan",
            lambda **kwargs: None,
        )
        gc_calls: list[bool] = []

        def _gc_track(*, dry_run: bool) -> None:
            gc_calls.append(dry_run)

        monkeypatch.setattr(store, "gc", _gc_track)

        t0 = 10_000.0
        acts = maybe_remediate(store, rep, cb, now_mono=t0)
        assert "consolidate" in acts
        assert "gc" in acts
        assert "integrity_alert" in acts
        assert gc_calls == [False]

        acts2 = maybe_remediate(store, rep, cb, now_mono=t0 + 1.0)
        assert acts2 == []
    finally:
        store.close()


@pytest.mark.skip(
    reason=(
        "DiagnosticsHistoryStore requires a PostgresConnectionManager (ADR-007 stage 2). "
        "The old SQLite db_path API was removed. Use integration tests with a live Postgres "
        "connection to validate rolling_average against real history data."
    )
)
def test_diagnostics_history_rolling_average(tmp_path) -> None:
    from tapps_brain.diagnostics import DiagnosticsHistoryStore

    store = MemoryStore(tmp_path)
    try:
        store.diagnostics(record_history=True)
    finally:
        store.close()

    db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
    hs = DiagnosticsHistoryStore(db_path)
    try:
        avg = hs.rolling_average(dimension="retrieval_effectiveness", window=5)
        assert avg is not None
    finally:
        hs.close()


def test_circuit_record_probe_to_degraded() -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.HALF_OPEN
    cb.half_open_probes = 2
    cb.record_probe(0.5)
    assert cb.half_open_probes == 3
    assert cb.state == CircuitState.DEGRADED


def test_enter_half_open_false_when_too_soon() -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.OPEN
    assert cb.enter_half_open_if_cooled(now_mono=1.0) is False


def test_pearson_r_short_series() -> None:
    assert pearson_r([1.0], [2.0]) is None
    assert pearson_r([], []) is None


def test_pearson_r_zero_variance() -> None:
    assert pearson_r([1.0, 1.0, 1.0], [2.0, 3.0, 4.0]) is None


def test_record_probe_in_half_open_insufficient_score() -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.HALF_OPEN
    cb.record_probe(0.4)
    assert cb.half_open_probes == 1
    assert cb.state == CircuitState.HALF_OPEN


def test_adjust_weights_short_history() -> None:
    w, adj = adjust_weights_for_correlation(
        ["a", "b"],
        {"a": 0.5, "b": 0.5},
        [{"dimensions": {"a": 1.0, "b": 1.0}}],
        min_rows=20,
    )
    assert adj is False
    assert w == {"a": 0.5, "b": 0.5}


def test_adjust_weights_skips_bad_json_dimension_scores() -> None:
    """Malformed dimension_scores JSON rows are ignored (coverage: parse errors)."""
    dim_names = ["d1", "d2"]
    base = {"d1": 0.5, "d2": 0.5}
    rows = [{"dimension_scores": "not-json"}] * 25
    _w, adj = adjust_weights_for_correlation(dim_names, base, rows, min_rows=20)
    assert adj is False


def test_circuit_half_open_low_composite_opens() -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.HALF_OPEN
    cb.transition(0.2)
    assert cb.state == CircuitState.OPEN


def test_circuit_half_open_mid_composite_degraded() -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.HALF_OPEN
    cb.transition(0.4)
    assert cb.state == CircuitState.DEGRADED


def test_circuit_transition_open_from_low_score() -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.CLOSED
    cb.transition(0.2)
    assert cb.state == CircuitState.OPEN


def test_circuit_transition_degraded_band() -> None:
    cb = CircuitBreaker()
    cb.transition(0.45)
    assert cb.state == CircuitState.DEGRADED


def test_circuit_half_open_after_cooldown(monkeypatch) -> None:
    cb = CircuitBreaker()
    cb.state = CircuitState.OPEN
    cb._last_remediation_mono.clear()
    monkeypatch.setattr("tapps_brain.diagnostics.random.random", lambda: 0.0)
    # First observation while OPEN starts the cooldown clock (missing ≠ epoch 0).
    assert cb.enter_half_open_if_cooled(now_mono=100.0) is False
    assert cb.state == CircuitState.OPEN
    assert cb.enter_half_open_if_cooled(now_mono=100.0 + cb.cooldown_seconds + 1.0) is True
    assert cb.state == CircuitState.HALF_OPEN


def test_circuit_open_does_not_half_open_on_long_uptime(monkeypatch) -> None:
    """Long process uptime must not skip OPEN cooldown (missing timestamp ≠ 0)."""
    cb = CircuitBreaker()
    monkeypatch.setattr("tapps_brain.diagnostics.random.random", lambda: 0.0)
    cb.transition(0.1)
    assert cb.state == CircuitState.OPEN
    # Simulate a process that has been up for days.
    assert cb.enter_half_open_if_cooled(now_mono=1_000_000.0) is False
    assert cb.state == CircuitState.OPEN
    assert cb.enter_half_open_if_cooled(now_mono=1_000_000.0 + cb.cooldown_seconds + 1.0) is True
    assert cb.state == CircuitState.HALF_OPEN


def test_hive_namespace_scores_empty_without_hive(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        d, w = _hive_namespace_scores(store)
        assert d == {}
        assert w is None
    finally:
        store.close()


class _HiveCountBackend:
    def count_by_namespace(self) -> dict[str, int]:
        return {"universal": 42, "repo-brain": 5}

    def search(
        self, query: str, namespaces: list[str] | None = None, **kwargs: object
    ) -> list[dict[str, str]]:
        raise AssertionError(f"search should not be called when counts exist: {query!r}")


class _HiveSearchBackend:
    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        **kwargs: object,
    ) -> list[dict[str, str]]:
        return [{"key": "k1"}]


def test_hive_namespace_scores_uses_namespace_counts(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store._hive_store = _HiveCountBackend()
    try:
        d, worst = _hive_namespace_scores(store)
        assert worst == 0.85
        assert d["universal"]["freshness"].score == 1.0
        assert d["universal"]["freshness"].raw_details["probe"] == "namespace_count"
        assert d["universal"]["freshness"].raw_details["hits"] == 42
    finally:
        store.close()


def test_hive_namespace_scores_search_fallback(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store._hive_store = _HiveSearchBackend()
    try:
        d, worst = _hive_namespace_scores(store)
        assert d["universal"]["freshness"].score == 1.0
        assert d["universal"]["freshness"].raw_details["probe"] == "search"
        assert d["universal"]["freshness"].raw_details["hits"] == 1
        assert worst == 0.85
    finally:
        store.close()


@pytest.mark.skip(
    reason="SQLite HiveStore removed in v3 (ADR-007); Hive diagnostics require PostgresHiveBackend"
)
def test_hive_namespace_scores_with_hive(tmp_path) -> None:
    raise RuntimeError("HiveStore (SQLite) removed in v3 — see ADR-007")


def test_typo_dimension_weight_does_not_dilute_composite(tmp_path) -> None:
    """Unknown dimension_weights keys are dropped (with a warning), not normalized in.

    A typo'd key otherwise absorbs weight mass the composite sum never pays
    back, dragging a healthy store toward the DEGRADED circuit threshold.
    """
    store = MemoryStore(tmp_path)
    try:
        store.save("diag-k1", "some healthy value", tier="pattern", source="agent")
        control = run_diagnostics(store)
        typo = run_diagnostics(
            store, config=DiagnosticsConfig(dimension_weights={"freshness_typo": 0.5})
        )
        assert typo.composite_score == pytest.approx(control.composite_score)
    finally:
        store.close()


def test_anomaly_detector_z_score_uses_pre_update_ewma() -> None:
    """Outlier z must be measured against the baseline before folding it in."""
    import math

    det = AnomalyDetector(lam=0.3, min_obs=3, warn_sigma=1.5, crit_sigma=3.0, confirm_window=1)
    for s in (0.8, 0.8, 0.8):
        det.detect(
            DiagnosticsReport(
                composite_score=s,
                dimensions={"x": DimensionScore(name="x", score=s)},
                recorded_at="t",
            )
        )
    prev_m = det._ewma["x"]
    prev_v = det._var_ewma["x"]
    expected_z = abs(0.2 - prev_m) / math.sqrt(max(1e-9, prev_v))
    alerts = det.detect(
        DiagnosticsReport(
            composite_score=0.2,
            dimensions={"x": DimensionScore(name="x", score=0.2)},
            recorded_at="t",
        )
    )
    assert alerts, "expected anomaly alert for large drop"
    assert abs(alerts[0]["z_score"] - round(expected_z, 4)) < 1e-6
