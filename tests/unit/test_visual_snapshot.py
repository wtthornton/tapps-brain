"""Tests for brain visual JSON snapshot (aggregated metadata only)."""

from __future__ import annotations

import json
from pathlib import Path

from tapps_brain.store import MemoryStore
from tapps_brain.visual_snapshot import (
    VISUAL_SNAPSHOT_SCHEMA_VERSION,
    build_visual_snapshot,
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
    assert snap.hive_health.status in {"ok", "warn"}
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
