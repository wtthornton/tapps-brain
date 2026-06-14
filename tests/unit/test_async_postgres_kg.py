"""Unit tests for async KG batch helpers."""

from __future__ import annotations

from tapps_brain.async_postgres_kg import _merge_async_alias_batch_hits


def test_merge_async_alias_batch_hits_single_match() -> None:
    result: dict[str, tuple[str, float, str]] = {}
    _merge_async_alias_batch_hits({"bar": [("e1", 0.95)]}, result)
    assert result["bar"] == ("e1", 0.95, "alias_match")
