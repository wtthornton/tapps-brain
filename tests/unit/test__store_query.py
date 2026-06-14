"""Unit tests for QueryMixin list/search helpers."""

from __future__ import annotations

from tapps_brain._store_query import QueryMixin


def test_parse_relative_time_days() -> None:
    iso = QueryMixin._parse_relative_time("7d")
    assert iso.endswith("+00:00") or "T" in iso
