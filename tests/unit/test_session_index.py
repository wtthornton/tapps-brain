"""Tests for session indexing (Epic 65.10)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.session_index import (
    delete_expired_sessions,
    index_session,
    search_session_index,
)

if TYPE_CHECKING:
    import pytest


def test_index_session_stores_chunks(tmp_path: pytest.TempPathFactory) -> None:
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


def test_index_session_truncates_to_max_chunks(tmp_path: pytest.TempPathFactory) -> None:
    """index_session respects max_chunks."""
    chunks = [f"chunk {i}" for i in range(60)]
    count = index_session(tmp_path, "sess-2", chunks, max_chunks=50)
    assert count == 50


def test_index_session_truncates_chunk_length(tmp_path: pytest.TempPathFactory) -> None:
    """index_session truncates long chunks to max_chars_per_chunk."""
    long_content = "deploy workflow " * 100
    count = index_session(
        tmp_path, "sess-3", [long_content], max_chars_per_chunk=100
    )
    assert count == 1
    results = search_session_index(tmp_path, "deploy")
    assert len(results) >= 1
    assert len(results[0]["content"]) <= 100


def test_search_session_index_empty_query(tmp_path: pytest.TempPathFactory) -> None:
    """search_session_index returns [] for empty query."""
    index_session(tmp_path, "sess-4", ["some content"])
    assert search_session_index(tmp_path, "") == []
    assert search_session_index(tmp_path, "   ") == []


def test_delete_expired_sessions(tmp_path: pytest.TempPathFactory) -> None:
    """delete_expired_sessions returns count (no crash)."""
    index_session(tmp_path, "sess-5", ["content"])
    deleted = delete_expired_sessions(tmp_path, ttl_days=3650)
    assert isinstance(deleted, int)
    assert deleted >= 0
