"""Unit tests for session_index.py in-memory fallback (TAP-640).

Covers:
- O(1) upsert correctness (no duplicates after re-index)
- Per-bucket size cap with oldest-first eviction
- search_session_index relevance + no full-list copy under lock
- delete_expired_sessions with new dict-backed structure
- Concurrent readers-vs-writer thread safety
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from tapps_brain import session_index as _mod
from tapps_brain.session_index import (
    delete_expired_sessions,
    index_session,
    search_session_index,
)


@pytest.fixture(autouse=True)
def _clear_index(tmp_path: Path) -> None:  # type: ignore[misc]
    """Reset the global in-memory index before each test."""
    with _mod._in_memory_lock:
        _mod._in_memory_index.clear()
    yield
    with _mod._in_memory_lock:
        _mod._in_memory_index.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bucket(root: Path) -> dict:
    """Return the raw bucket dict for *root* (must hold no lock)."""
    return _mod._in_memory_index.get(str(root), {})


# ---------------------------------------------------------------------------
# Basic indexing
# ---------------------------------------------------------------------------


def test_index_session_returns_stored_count(tmp_path: Path) -> None:
    count = index_session(tmp_path, "s1", ["alpha", "beta", "gamma"])
    assert count == 3


def test_index_session_empty_session_id_returns_zero(tmp_path: Path) -> None:
    assert index_session(tmp_path, "", ["chunk"]) == 0
    assert index_session(tmp_path, "  ", ["chunk"]) == 0


def test_index_session_empty_chunks_returns_zero(tmp_path: Path) -> None:
    assert index_session(tmp_path, "s1", []) == 0
    assert index_session(tmp_path, "s1", ["", "  "]) == 0


def test_index_session_trims_to_max_chars(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["abcdef"], max_chars_per_chunk=3)
    bucket = _bucket(tmp_path)
    assert bucket[("s1", 0)]["content"] == "abc"


def test_index_session_respects_max_chunks(tmp_path: Path) -> None:
    chunks = [f"chunk-{i}" for i in range(10)]
    stored = index_session(tmp_path, "s1", chunks, max_chunks=4)
    assert stored == 4
    bucket = _bucket(tmp_path)
    assert len(bucket) == 4


# ---------------------------------------------------------------------------
# O(1) upsert — no duplicates
# ---------------------------------------------------------------------------


def test_upsert_replaces_existing_chunk(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["original content"])
    index_session(tmp_path, "s1", ["updated content"])
    bucket = _bucket(tmp_path)
    # Only one entry for (s1, 0) — no duplicate
    entries = [v for k, v in bucket.items() if k[0] == "s1" and k[1] == 0]
    assert len(entries) == 1
    assert entries[0]["content"] == "updated content"


def test_upsert_does_not_grow_bucket_on_reindex(tmp_path: Path) -> None:
    for _ in range(5):
        index_session(tmp_path, "s1", ["chunk a", "chunk b"])
    # Re-indexing the same session 5 times should not grow the bucket beyond 2
    assert len(_bucket(tmp_path)) == 2


def test_different_sessions_coexist(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["alpha"])
    index_session(tmp_path, "s2", ["beta"])
    bucket = _bucket(tmp_path)
    assert ("s1", 0) in bucket
    assert ("s2", 0) in bucket


# ---------------------------------------------------------------------------
# Per-bucket size cap + eviction
# ---------------------------------------------------------------------------


def test_cap_limits_bucket_size(tmp_path: Path) -> None:
    cap = 10
    # Insert 20 unique sessions (each with 1 chunk) → bucket must not exceed cap
    for i in range(20):
        index_session(tmp_path, f"sess-{i}", [f"content {i}"], _max_in_memory=cap)
    assert len(_bucket(tmp_path)) <= cap


def test_cap_enforces_size_after_overflow(tmp_path: Path) -> None:
    """After inserting more sessions than the cap, bucket stays at cap size."""
    cap = 5
    for i in range(cap + 3):
        index_session(tmp_path, f"sess-{i}", [f"content {i}"], _max_in_memory=cap)

    bucket = _bucket(tmp_path)
    assert len(bucket) == cap


@pytest.mark.slow
def test_cap_20001_chunks_yields_10000(tmp_path: Path) -> None:
    """Acceptance criterion: 20 001 inserts with cap=10 000 → exactly 10 000.

    Marked slow — skipped in fast unit-test runs; run with -m slow explicitly.
    """
    cap = 10_000
    # Each unique (session_id, chunk_index) is a distinct key; use many sessions.
    for i in range(20_001):
        session_id = f"s{i}"
        index_session(tmp_path, session_id, ["x"], _max_in_memory=cap)

    assert len(_bucket(tmp_path)) == cap


def test_cap_scaled_yields_exact_cap(tmp_path: Path) -> None:
    """Scaled-down equivalent of the 20 001/10 000 acceptance criterion (fast)."""
    cap = 50
    for i in range(120):
        index_session(tmp_path, f"sess-{i}", ["x"], _max_in_memory=cap)
    assert len(_bucket(tmp_path)) == cap


def test_upsert_existing_does_not_trigger_eviction(tmp_path: Path) -> None:
    """Re-indexing an existing (session, chunk) should never evict when at cap."""
    cap = 5
    for i in range(cap):
        index_session(tmp_path, f"sess-{i}", ["content"], _max_in_memory=cap)
    # Bucket is exactly at cap; re-indexing sess-0 should keep size at cap
    index_session(tmp_path, "sess-0", ["updated"], _max_in_memory=cap)
    assert len(_bucket(tmp_path)) == cap


# ---------------------------------------------------------------------------
# search_session_index
# ---------------------------------------------------------------------------


def test_search_returns_empty_for_blank_query(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["hello world"])
    assert search_session_index(tmp_path, "") == []
    assert search_session_index(tmp_path, "   ") == []


def test_search_finds_matching_chunks(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["python async programming", "database query"])
    results = search_session_index(tmp_path, "python")
    assert len(results) == 1
    assert results[0]["content"] == "python async programming"


def test_search_ranks_by_overlap(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["foo bar baz"])
    index_session(tmp_path, "s2", ["foo bar"])
    results = search_session_index(tmp_path, "foo bar baz")
    # "foo bar baz" matches 3 words of s1[0] vs 2 of s2[0]
    assert results[0]["session_id"] == "s1"


def test_search_respects_limit(tmp_path: Path) -> None:
    for i in range(20):
        index_session(tmp_path, f"s{i}", [f"common word content {i}"])
    results = search_session_index(tmp_path, "common", limit=5)
    assert len(results) <= 5


def test_search_returns_copies_not_references(tmp_path: Path) -> None:
    """Mutating a search result must not corrupt the in-memory index."""
    index_session(tmp_path, "s1", ["original"])
    results = search_session_index(tmp_path, "original")
    assert results
    results[0]["content"] = "MUTATED"
    # The bucket entry must be unchanged
    bucket = _bucket(tmp_path)
    assert bucket[("s1", 0)]["content"] == "original"


# ---------------------------------------------------------------------------
# delete_expired_sessions
# ---------------------------------------------------------------------------


def test_delete_expired_zero_ttl_returns_zero(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["chunk"])
    assert delete_expired_sessions(tmp_path, 0) == 0
    assert delete_expired_sessions(tmp_path, -1) == 0


def test_delete_expired_removes_old_chunks(tmp_path: Path) -> None:
    """Manually backdating created_at simulates expired entries."""
    index_session(tmp_path, "s1", ["old chunk"])
    # Backdate the entry to 100 days ago
    with _mod._in_memory_lock:
        from datetime import UTC, datetime, timedelta

        old_ts = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        _mod._in_memory_index[str(tmp_path)][("s1", 0)]["created_at"] = old_ts

    deleted = delete_expired_sessions(tmp_path, ttl_days=1)
    assert deleted == 1
    # Bucket is now empty — the project-root key must also be removed.
    assert str(tmp_path) not in _mod._in_memory_index


def test_delete_expired_keeps_fresh_chunks(tmp_path: Path) -> None:
    index_session(tmp_path, "s1", ["fresh chunk"])
    deleted = delete_expired_sessions(tmp_path, ttl_days=30)
    assert deleted == 0
    assert len(_bucket(tmp_path)) == 1


def test_delete_expired_missing_key_returns_zero(tmp_path: Path) -> None:
    # No entries for this root
    assert delete_expired_sessions(tmp_path / "nonexistent", 1) == 0


# ---------------------------------------------------------------------------
# Thread safety: concurrent readers-vs-writer
# ---------------------------------------------------------------------------


def test_concurrent_index_and_search_no_error(tmp_path: Path) -> None:
    """Concurrent writers and readers must not raise exceptions or corrupt state."""
    errors: list[Exception] = []
    cap = 500

    def writer(n: int) -> None:
        try:
            for i in range(n):
                index_session(
                    tmp_path,
                    f"thread-{threading.get_ident()}-{i}",
                    [f"content {i} keyword"],
                    _max_in_memory=cap,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(50):
                search_session_index(tmp_path, "keyword", limit=10)
                time.sleep(0)  # yield
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(30,)) for _ in range(4)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"Thread errors: {errors}"
    # Bucket size must be bounded by cap
    assert len(_bucket(tmp_path)) <= cap


def test_concurrent_writers_no_duplicate_keys(tmp_path: Path) -> None:
    """Two threads writing the same session/chunk must produce one entry."""
    cap = 1000
    done = threading.Barrier(2)

    def write_same() -> None:
        done.wait()
        for i in range(20):
            index_session(tmp_path, "shared-session", [f"chunk {i}"], _max_in_memory=cap)

    t1 = threading.Thread(target=write_same)
    t2 = threading.Thread(target=write_same)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    bucket = _bucket(tmp_path)
    # Both threads write chunks 0-19 for the same session; dict enforces one entry
    # per (session_id, chunk_index) key, so exactly 20 keys must exist.
    assert len(bucket) == 20
