"""Tests for session indexing (Epic 65.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.session_index import (
    delete_expired_sessions,
    index_session,
    search_session_index,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# GCConfig — session_index_ttl_days (STORY-048.1)
# ---------------------------------------------------------------------------


def test_gcconfig_has_session_index_ttl_days() -> None:
    """GCConfig exposes session_index_ttl_days with a sensible default."""
    from tapps_brain.gc import GCConfig

    cfg = GCConfig()
    assert cfg.session_index_ttl_days == 90


def test_gcconfig_to_dict_includes_session_index_ttl_days() -> None:
    """GCConfig.to_dict() serialises session_index_ttl_days."""
    from tapps_brain.gc import GCConfig

    cfg = GCConfig(session_index_ttl_days=30)
    d = cfg.to_dict()
    assert d["session_index_ttl_days"] == 30


def test_gc_result_has_session_chunks_deleted() -> None:
    """GCResult carries a session_chunks_deleted field defaulting to 0."""
    from tapps_brain.gc import GCResult

    result = GCResult()
    assert result.session_chunks_deleted == 0


def test_store_gc_prunes_session_index(tmp_path: Path) -> None:
    """store.gc() deletes session index rows older than session_index_ttl_days."""
    import sqlite3

    from tapps_brain.gc import GCConfig
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path)
    try:
        store.index_session("sess-stale", ["stale content to prune"])
        store.index_session("sess-fresh", ["fresh content to keep"])

        # Backdate sess-stale to 200 days ago.
        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE session_index SET created_at = datetime('now', '-200 days') "
            "WHERE session_id = 'sess-stale'"
        )
        conn.commit()
        conn.close()

        store.set_gc_config(GCConfig(session_index_ttl_days=30))
        result = store.gc()
        assert result.session_chunks_deleted >= 1  # type: ignore[union-attr]

        # Verify stale session is gone but fresh one remains.
        remaining = store.search_sessions("content")
        ids = [r["session_id"] for r in remaining]
        assert "sess-stale" not in ids
        assert "sess-fresh" in ids
    finally:
        store.close()


def test_store_gc_dry_run_does_not_prune_session_index(tmp_path: Path) -> None:
    """store.gc(dry_run=True) does not delete session index rows."""
    import sqlite3

    from tapps_brain.gc import GCConfig
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path)
    try:
        store.index_session("sess-old", ["old content"])
        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE session_index SET created_at = datetime('now', '-200 days') "
            "WHERE session_id = 'sess-old'"
        )
        conn.commit()
        conn.close()

        store.set_gc_config(GCConfig(session_index_ttl_days=30))
        store.gc(dry_run=True)

        # Session index rows must still be there after a dry run.
        results = store.search_sessions("old content")
        assert any(r["session_id"] == "sess-old" for r in results)
    finally:
        store.close()


def test_index_session_stores_chunks(tmp_path: Path) -> None:
    """index_session stores chunks and they are searchable."""
    chunks = [
        "User asked about deploy workflow. We discussed CI/CD steps.",
        "Decided to use GitHub Actions for builds.",
    ]
    count = index_session(tmp_path, "sess-1", chunks)
    assert count == 2

    results = search_session_index(tmp_path, "deploy")
    assert len(results) >= 1
    assert any("deploy" in r["content"].lower() for r in results)


def test_index_session_truncates_to_max_chunks(tmp_path: Path) -> None:
    """index_session respects max_chunks."""
    chunks = [f"chunk {i}" for i in range(60)]
    count = index_session(tmp_path, "sess-2", chunks, max_chunks=50)
    assert count == 50


def test_index_session_truncates_chunk_length(tmp_path: Path) -> None:
    """index_session truncates long chunks to max_chars_per_chunk."""
    long_content = "deploy workflow " * 100
    count = index_session(tmp_path, "sess-3", [long_content], max_chars_per_chunk=100)
    assert count == 1
    results = search_session_index(tmp_path, "deploy")
    assert len(results) >= 1
    assert len(results[0]["content"]) <= 100


def test_search_session_index_empty_query(tmp_path: Path) -> None:
    """search_session_index returns [] for empty query."""
    index_session(tmp_path, "sess-4", ["some content"])
    assert search_session_index(tmp_path, "") == []
    assert search_session_index(tmp_path, "   ") == []


def test_delete_expired_sessions(tmp_path: Path) -> None:
    """delete_expired_sessions returns count (no crash)."""
    index_session(tmp_path, "sess-5", ["content"])
    deleted = delete_expired_sessions(tmp_path, ttl_days=3650)
    assert isinstance(deleted, int)
    assert deleted >= 0


def test_delete_expired_sessions_rejects_non_positive_ttl(tmp_path: Path) -> None:
    """ttl_days < 1 is a no-op (returns 0 without opening persistence)."""
    assert delete_expired_sessions(tmp_path, ttl_days=0) == 0
    assert delete_expired_sessions(tmp_path, ttl_days=-1) == 0
