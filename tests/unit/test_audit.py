"""Tests for the audit trail query API (STORY-007.3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from tapps_brain.audit import AuditEntry, AuditReader

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def audit_file(tmp_path: Path) -> Path:
    """Create a JSONL audit file with test data."""
    path = tmp_path / "memory_log.jsonl"
    now = datetime.now(tz=UTC)

    records = []
    for i in range(50):
        ts = (now - timedelta(hours=50 - i)).isoformat()
        action = "save" if i % 3 != 0 else "delete"
        key = f"key-{i % 5}"
        records.append(json.dumps({"action": action, "key": key, "timestamp": ts}))

    path.write_text("\n".join(records) + "\n", encoding="utf-8")
    return path


class TestAuditReader:
    def test_query_all(self, audit_file: Path):
        reader = AuditReader(audit_file)
        results = reader.query(limit=200)
        assert len(results) == 50
        assert all(isinstance(r, AuditEntry) for r in results)

    def test_query_by_key(self, audit_file: Path):
        reader = AuditReader(audit_file)
        results = reader.query(key="key-0")
        assert all(r.key == "key-0" for r in results)
        assert len(results) == 10  # 50 entries, key-0 appears for i % 5 == 0

    def test_query_by_event_type(self, audit_file: Path):
        reader = AuditReader(audit_file)
        saves = reader.query(event_type="save")
        deletes = reader.query(event_type="delete")
        assert all(r.event_type == "save" for r in saves)
        assert all(r.event_type == "delete" for r in deletes)
        assert len(saves) + len(deletes) == 50

    def test_query_by_time_range(self, audit_file: Path):
        reader = AuditReader(audit_file)
        now = datetime.now(tz=UTC)
        since = (now - timedelta(hours=10)).isoformat()
        until = (now - timedelta(hours=5)).isoformat()
        results = reader.query(since=since, until=until)
        for r in results:
            assert r.timestamp >= since
            assert r.timestamp <= until

    def test_query_limit(self, audit_file: Path):
        reader = AuditReader(audit_file)
        results = reader.query(limit=5)
        assert len(results) == 5

    def test_query_combined_filters(self, audit_file: Path):
        reader = AuditReader(audit_file)
        results = reader.query(key="key-0", event_type="save")
        assert all(r.key == "key-0" for r in results)
        assert all(r.event_type == "save" for r in results)

    def test_query_nonexistent_file(self, tmp_path: Path):
        reader = AuditReader(tmp_path / "does_not_exist.jsonl")
        results = reader.query()
        assert results == []

    def test_query_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        reader = AuditReader(path)
        results = reader.query()
        assert results == []

    def test_query_malformed_lines(self, tmp_path: Path):
        path = tmp_path / "bad.jsonl"
        lines = [
            '{"action": "save", "key": "good", "timestamp": "2025-01-01T00:00:00"}',
            "not json at all",
            '{"action": "delete", "key": "good2", "timestamp": "2025-01-01T01:00:00"}',
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        reader = AuditReader(path)
        results = reader.query()
        assert len(results) == 2

    def test_count(self, audit_file: Path):
        reader = AuditReader(audit_file)
        assert reader.count() == 50
        assert reader.count(key="key-0") == 10
        assert reader.count(event_type="delete") > 0

    def test_count_nonexistent(self, tmp_path: Path):
        reader = AuditReader(tmp_path / "missing.jsonl")
        assert reader.count() == 0


class TestAuditEntry:
    def test_model_serialization(self):
        entry = AuditEntry(
            timestamp="2025-01-01T00:00:00",
            event_type="save",
            key="test-key",
            details={"extra": "data"},
        )
        d = entry.model_dump()
        assert d["key"] == "test-key"
        assert d["details"]["extra"] == "data"
