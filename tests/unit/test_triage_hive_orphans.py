"""Unit tests for scripts/triage_hive_orphans.py (TAP-6816, b4 report half).

Covers:
- VAL-08 no-write guard: every SQL statement the script issues is a SELECT.
- VAL-08 classification: a fixture orphan row and a fixture non-orphan row
  (plus archived-vs-absent sub-cases) are classified correctly.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import triage_hive_orphans as triage

_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


class RecordingCursor:
    """Fake DB-API cursor that records every statement passed to execute()."""

    def __init__(self, tables: dict[str, list[tuple[object, ...]]]) -> None:
        self._tables = tables
        self._last_result: list[tuple[object, ...]] = []
        self.executed: list[str] = []

    def execute(self, query: str, params: object | None = None) -> None:
        self.executed.append(query)
        query_upper = " ".join(query.split()).upper()
        if "COUNT(*) FROM HIVE_MEMORIES" in query_upper:
            self._last_result = [(len(self._tables["hive_memories"]),)]
        elif "FROM HIVE_MEMORIES" in query_upper:
            self._last_result = list(self._tables["hive_memories"])
        elif "FROM PRIVATE_MEMORIES" in query_upper:
            self._last_result = list(self._tables["private_memories"])
        elif "FROM GC_ARCHIVE" in query_upper:
            self._last_result = list(self._tables["gc_archive"])
        else:
            msg = f"RecordingCursor has no fixture for query: {query}"
            raise AssertionError(msg)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._last_result

    def fetchone(self) -> tuple[object, ...] | None:
        return self._last_result[0] if self._last_result else None


def _now() -> datetime:
    return datetime(2026, 9, 15, tzinfo=UTC)


def _fixture_tables() -> dict[str, list[tuple[object, ...]]]:
    now = _now()
    return {
        "hive_memories": [
            # non-orphan: live private counterpart exists (status=active)
            ("universal", "key-live", "agent-a", now - timedelta(days=1)),
            # orphan, counterpart absent: no private row, no archive row at all
            ("universal", "key-absent", "agent-b", now - timedelta(days=10)),
            # orphan, counterpart archived via private_memories.status
            ("universal", "key-archived-private", "agent-c", now - timedelta(days=40)),
            # orphan, counterpart archived via gc_archive (private row was hard-deleted)
            ("universal", "key-archived-gc", "agent-d", now - timedelta(days=400)),
        ],
        "private_memories": [
            ("proj-a", "agent-a", "key-live", "active"),
            ("proj-c", "agent-c", "key-archived-private", "archived"),
        ],
        "gc_archive": [
            ("proj-d", "agent-d", "key-archived-gc"),
        ],
    }


def test_every_statement_issued_is_select() -> None:
    tables = _fixture_tables()
    cur = RecordingCursor(tables)
    triage.run_triage(cur, cur, now=_now())

    assert cur.executed, "expected the script to issue at least one statement"
    for statement in cur.executed:
        assert _SELECT_RE.match(statement), f"non-SELECT statement issued: {statement!r}"


def test_classification_orphan_and_non_orphan() -> None:
    tables = _fixture_tables()
    cur = RecordingCursor(tables)
    report = triage.run_triage(cur, cur, now=_now())

    assert report.hive_total == 4
    assert report.orphan_count == 3

    by_key = {o.key: o for o in report.orphans}
    assert "key-live" not in by_key  # live private counterpart -> not an orphan

    absent = by_key["key-absent"]
    assert absent.counterpart == "absent"
    assert absent.project_id == "unknown"
    assert absent.age_bucket == "7-30d"

    archived_private = by_key["key-archived-private"]
    assert archived_private.counterpart == "archived"
    assert archived_private.project_id == "proj-c"
    assert archived_private.age_bucket == "30-90d"

    archived_gc = by_key["key-archived-gc"]
    assert archived_gc.counterpart == "archived"
    assert archived_gc.project_id == "proj-d"
    assert archived_gc.age_bucket == "365d+"


def test_build_report_mentions_no_decision() -> None:
    tables = _fixture_tables()
    cur = RecordingCursor(tables)
    report = triage.run_triage(cur, cur, now=_now())
    text = triage.build_report(report, _now())

    assert "Not made here" in text
    assert "Orphans found: 3" in text
