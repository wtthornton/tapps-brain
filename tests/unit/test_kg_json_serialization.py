"""Unit tests — TAP-4275 KG row JSON serialization."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

from tapps_brain.postgres_kg import json_safe_kg_value
from tapps_brain.services import kg_service


class TestJsonSafeKgValue:
    def test_uuid_becomes_string(self) -> None:
        uid = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        assert json_safe_kg_value(uid) == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    def test_datetime_becomes_isoformat(self) -> None:
        ts = datetime(2026, 6, 22, 12, 30, 0, tzinfo=UTC)
        assert json_safe_kg_value(ts) == "2026-06-22T12:30:00+00:00"

    def test_scalars_pass_through(self) -> None:
        assert json_safe_kg_value("uses") == "uses"
        assert json_safe_kg_value(1) == 1


class TestGetNeighborsJsonSerialization:
    def test_get_neighbors_serializes_datetime_fields(self) -> None:
        ts = datetime(2026, 6, 22, 12, 30, 0, tzinfo=UTC)
        uid = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
        kg = type("KG", (), {})()
        kg.get_neighbors_multi = lambda *a, **k: [
            {
                "edge_id": uid,
                "predicate": "uses",
                "last_reinforced": ts,
                "edge_updated_at": ts,
                "neighbor_id": uid,
                "hop": 1,
            }
        ]
        kg.close = lambda: None

        with patch.object(kg_service, "_kg_store", return_value=kg):
            result = kg_service.get_neighbors(
                object(),
                "tenant-a",
                "tapps-brain",
                entity_ids=["a1b2c3d4-e5f6-7890-abcd-ef1234567890"],
            )

        json.dumps(result)
        neighbor = result["neighbors"][0]
        assert neighbor["last_reinforced"] == "2026-06-22T12:30:00+00:00"
        assert neighbor["edge_updated_at"] == "2026-06-22T12:30:00+00:00"
        assert neighbor["edge_id"] == str(uid)
