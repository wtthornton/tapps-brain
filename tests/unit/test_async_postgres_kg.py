"""Unit tests for async KG batch helpers."""

from __future__ import annotations

from tapps_brain.async_postgres_kg import _merge_async_alias_batch_hits


def test_merge_async_alias_batch_hits_single_match() -> None:
    result: dict[str, tuple[str, float, str]] = {}
    _merge_async_alias_batch_hits({"bar": [("e1", 0.95)]}, result)
    assert result["bar"] == ("e1", 0.95, "alias_match")


def test_edge_decay_adapter_maps_domain_layer() -> None:
    from datetime import UTC, datetime

    from tapps_brain.postgres_kg import PostgresKnowledgeGraphStore, _EdgeDecayAdapter

    adapter = _EdgeDecayAdapter(
        stability=0.0,
        difficulty=0.0,
        layer="domain",
        last_reinforced=None,
        updated_at=datetime.now(tz=UTC),
    )
    assert adapter.tier == "architectural"
    store = object.__new__(PostgresKnowledgeGraphStore)
    new_s, new_d = PostgresKnowledgeGraphStore._compute_fsrs(
        store,
        stability=0.0,
        difficulty=0.0,
        layer="domain",
        last_reinforced=None,
        updated_at=datetime.now(tz=UTC),
        was_useful=True,
    )
    assert isinstance(new_s, float)
    assert isinstance(new_d, float)
