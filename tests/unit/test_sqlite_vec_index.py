"""sqlite-vec index helpers (GitHub #30)."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

pytest.importorskip("sqlite_vec")

from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.sqlite_vec_index import (
    DEFAULT_VEC_DIM,
    backfill_from_memories,
    delete_vec_key,
    ensure_memory_vec_table,
    knn_search,
    maybe_backfill_if_empty,
    try_load_extension,
    upsert_vec_row,
    vec_row_count,
)


def _entry(key: str, emb: list[float]) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value="v",
        tier=MemoryTier.pattern,
        confidence=0.5,
        source=MemorySource.agent,
        embedding=emb,
    )


def test_sqlite_vec_roundtrip_knn() -> None:
    conn = sqlite3.connect(":memory:")
    assert try_load_extension(conn)
    ensure_memory_vec_table(conn, dim=4)
    emb = [1.0, 0.0, 0.0, 0.0]
    upsert_vec_row(conn, "k1", emb)
    upsert_vec_row(conn, "k2", [0.0, 1.0, 0.0, 0.0])
    conn.commit()
    assert vec_row_count(conn) == 2
    q = [0.9, 0.1, 0.0, 0.0]
    hits = knn_search(conn, q, k=2, dim=4)
    assert len(hits) >= 1
    assert hits[0][0] == "k1"
    # vec0 default metric is L2; nearer key should have strictly lower distance.
    assert len(hits) == 2
    assert hits[0][1] < hits[1][1]


def test_backfill_from_memories() -> None:
    conn = sqlite3.connect(":memory:")
    assert try_load_extension(conn)
    ensure_memory_vec_table(conn, dim=DEFAULT_VEC_DIM)
    emb = [0.1] * DEFAULT_VEC_DIM
    entries = [_entry("a", emb), _entry("b", emb)]
    n = backfill_from_memories(conn, entries, dim=DEFAULT_VEC_DIM)
    assert n == 2
    conn.commit()
    assert vec_row_count(conn) == 2


def test_knn_search_wrong_embedding_dim() -> None:
    conn = sqlite3.connect(":memory:")
    assert try_load_extension(conn)
    ensure_memory_vec_table(conn, dim=4)
    assert knn_search(conn, [0.0, 0.0, 0.0], k=3, dim=4) == []
    assert knn_search(conn, [1.0, 0.0, 0.0, 0.0], k=0, dim=4) == []
    conn.close()


def test_try_load_extension_when_load_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import sqlite_vec

    def _boom(_conn: sqlite3.Connection) -> None:
        msg = "load failed"
        raise OSError(msg)

    monkeypatch.setattr(sqlite_vec, "load", _boom)
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    assert try_load_extension(conn) is False
    conn.close()


def test_vec_row_count_without_vec_table() -> None:
    conn = sqlite3.connect(":memory:")
    assert vec_row_count(conn) == 0
    conn.close()


def test_maybe_backfill_if_empty_without_vec_table() -> None:
    conn = sqlite3.connect(":memory:")
    emb = [0.1] * DEFAULT_VEC_DIM
    assert maybe_backfill_if_empty(conn, [_entry("x", emb)]) == 0
    conn.close()


def test_delete_vec_key_and_upsert_noop_without_table() -> None:
    conn = sqlite3.connect(":memory:")
    delete_vec_key(conn, "any")
    upsert_vec_row(conn, "k", [0.0] * DEFAULT_VEC_DIM)
    conn.close()


def test_backfill_from_memories_no_table() -> None:
    conn = sqlite3.connect(":memory:")
    emb = [0.1] * DEFAULT_VEC_DIM
    n = backfill_from_memories(conn, [_entry("a", emb)], dim=DEFAULT_VEC_DIM)
    assert n == 0
    conn.close()


def test_backfill_skips_wrong_embedding_length() -> None:
    conn = sqlite3.connect(":memory:")
    assert try_load_extension(conn)
    ensure_memory_vec_table(conn, dim=DEFAULT_VEC_DIM)
    short = [0.1] * 10
    n = backfill_from_memories(conn, [_entry("a", short)], dim=DEFAULT_VEC_DIM)
    assert n == 0
    conn.close()


def test_knn_search_operational_error_on_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tapps_brain.sqlite_vec_index as svi

    monkeypatch.setattr(svi, "vec_table_exists", lambda _c: True)

    def execute(sql: str, _params: object = ()) -> MagicMock:
        if "MATCH" in sql:
            raise sqlite3.OperationalError("vec fail")
        m = MagicMock()
        m.fetchone.return_value = (1,)
        return m

    conn = MagicMock()
    conn.execute = execute
    q = [0.1] * DEFAULT_VEC_DIM
    assert knn_search(conn, q, k=3, dim=DEFAULT_VEC_DIM) == []


def test_maybe_backfill_if_empty_noop_when_already_populated() -> None:
    conn = sqlite3.connect(":memory:")
    assert try_load_extension(conn)
    ensure_memory_vec_table(conn, dim=DEFAULT_VEC_DIM)
    emb = [0.1] * DEFAULT_VEC_DIM
    entries = [_entry("only", emb)]
    assert maybe_backfill_if_empty(conn, entries) == 1
    assert maybe_backfill_if_empty(conn, entries) == 0
    conn.close()
