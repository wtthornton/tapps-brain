"""MemoryPersistence + sqlite-vec integration (GitHub #30)."""

from __future__ import annotations

import sqlite3

import pytest

pytest.importorskip("sqlite_vec")

import tapps_brain.sqlite_vec_index as sqlite_vec_mod
from tapps_brain.models import MemoryEntry, MemorySource, MemoryTier
from tapps_brain.persistence import MemoryPersistence
from tapps_brain.sqlite_vec_index import DEFAULT_VEC_DIM


def test_setup_sqlite_vec_raises_when_extension_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_conn: object) -> None:
        raise OSError("extension load failed")

    monkeypatch.setattr(sqlite_vec_mod, "load_extension", _boom)
    with pytest.raises(OSError, match="extension load failed"):
        MemoryPersistence(tmp_path)


def test_persistence_sqlite_vec_knn_after_save(tmp_path) -> None:
    mp = MemoryPersistence(tmp_path)
    emb = [0.02] * DEFAULT_VEC_DIM
    entry = MemoryEntry(
        key="vec-key",
        value="semantic hello world",
        tier=MemoryTier.pattern,
        confidence=0.5,
        source=MemorySource.agent,
        embedding=emb,
    )
    mp.save(entry)
    if not mp._sqlite_vec_enabled:
        mp.close()
        pytest.skip("sqlite-vec extension not available in this environment")
    assert mp.sqlite_vec_row_count() >= 1
    hits = mp.sqlite_vec_knn_search(emb, k=5)
    assert any(h[0] == "vec-key" for h in hits)
    mp.delete("vec-key")
    mp.close()


def test_persistence_sqlite_vec_removes_on_delete(tmp_path) -> None:
    mp = MemoryPersistence(tmp_path)
    emb = [0.03] * DEFAULT_VEC_DIM
    mp.save(
        MemoryEntry(
            key="del-me",
            value="x",
            tier=MemoryTier.pattern,
            confidence=0.5,
            source=MemorySource.agent,
            embedding=emb,
        )
    )
    if not mp._sqlite_vec_enabled:
        mp.close()
        pytest.skip("sqlite-vec extension not available")
    before = mp.sqlite_vec_row_count()
    mp.delete("del-me")
    after = mp.sqlite_vec_row_count()
    assert after < before
    mp.close()


def test_vec_table_exists_false_without_extension() -> None:
    from tapps_brain.sqlite_vec_index import vec_table_exists

    conn = sqlite3.connect(":memory:")
    assert vec_table_exists(conn) is False
    conn.close()
