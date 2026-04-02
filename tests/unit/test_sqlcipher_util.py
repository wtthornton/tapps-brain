"""Unit tests for SQLCipher helpers (GitHub #23)."""

from __future__ import annotations

import sqlite3
import sys
import types
from pathlib import Path

import pytest

from tapps_brain import sqlcipher_util
from tapps_brain.sqlcipher_util import (
    connect_sqlite,
    connect_sqlite_readonly,
    pragma_key_statement,
    resolve_hive_encryption_key,
    resolve_memory_encryption_key,
    resolve_memory_readonly_search_enabled,
    resolve_sqlite_busy_timeout_ms,
)


def test_resolve_memory_encryption_key_explicit_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPPS_BRAIN_ENCRYPTION_KEY", "from-env")
    assert resolve_memory_encryption_key("  explicit  ") == "explicit"
    assert resolve_memory_encryption_key("") is None
    assert resolve_memory_encryption_key("   ") is None


def test_resolve_memory_encryption_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAPPS_BRAIN_ENCRYPTION_KEY", raising=False)
    assert resolve_memory_encryption_key(None) is None
    monkeypatch.setenv("TAPPS_BRAIN_ENCRYPTION_KEY", "  k  ")
    assert resolve_memory_encryption_key(None) == "k"


def test_resolve_hive_encryption_key_hive_env_then_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAPPS_BRAIN_HIVE_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_ENCRYPTION_KEY", raising=False)
    assert resolve_hive_encryption_key(None) is None
    monkeypatch.setenv("TAPPS_BRAIN_ENCRYPTION_KEY", "mem")
    assert resolve_hive_encryption_key(None) == "mem"
    monkeypatch.setenv("TAPPS_BRAIN_HIVE_ENCRYPTION_KEY", "hive-only")
    assert resolve_hive_encryption_key(None) == "hive-only"


def test_resolve_hive_encryption_key_explicit_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPPS_BRAIN_ENCRYPTION_KEY", "fallback")
    assert resolve_hive_encryption_key("  ") is None
    assert resolve_hive_encryption_key("ok") == "ok"


def test_pragma_key_statement_escapes_quotes() -> None:
    assert "''" in pragma_key_statement("a'b")


def test_resolve_memory_readonly_search_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAPPS_SQLITE_MEMORY_READONLY_SEARCH", raising=False)
    assert resolve_memory_readonly_search_enabled() is False
    monkeypatch.setenv("TAPPS_SQLITE_MEMORY_READONLY_SEARCH", "1")
    assert resolve_memory_readonly_search_enabled() is True
    monkeypatch.setenv("TAPPS_SQLITE_MEMORY_READONLY_SEARCH", "true")
    assert resolve_memory_readonly_search_enabled() is True
    monkeypatch.setenv("TAPPS_SQLITE_MEMORY_READONLY_SEARCH", "no")
    assert resolve_memory_readonly_search_enabled() is False


def test_resolve_sqlite_busy_timeout_ms_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAPPS_SQLITE_BUSY_MS", raising=False)
    assert resolve_sqlite_busy_timeout_ms() == 5000


def test_resolve_sqlite_busy_timeout_ms_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPPS_SQLITE_BUSY_MS", "12000")
    assert resolve_sqlite_busy_timeout_ms() == 12000
    monkeypatch.setenv("TAPPS_SQLITE_BUSY_MS", "0")
    assert resolve_sqlite_busy_timeout_ms() == 0


def test_resolve_sqlite_busy_timeout_ms_invalid_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPPS_SQLITE_BUSY_MS", "not-int")
    assert resolve_sqlite_busy_timeout_ms() == 5000
    monkeypatch.setenv("TAPPS_SQLITE_BUSY_MS", "-1")
    assert resolve_sqlite_busy_timeout_ms() == 5000
    monkeypatch.setenv("TAPPS_SQLITE_BUSY_MS", "3600001")
    assert resolve_sqlite_busy_timeout_ms() == 5000


def test_connect_sqlite_readonly_plain_select(tmp_path: Path) -> None:
    db = tmp_path / "ro.db"
    w = connect_sqlite(db, encryption_key=None, check_same_thread=False)
    try:
        w.execute("CREATE TABLE t (x INTEGER)")
        w.execute("INSERT INTO t VALUES (42)")
        w.commit()
    finally:
        w.close()

    r = connect_sqlite_readonly(db, encryption_key=None, check_same_thread=False)
    try:
        row = r.execute("SELECT x FROM t").fetchone()
        assert row is not None and int(row[0]) == 42
        with pytest.raises(sqlite3.OperationalError):
            r.execute("INSERT INTO t VALUES (2)")
    finally:
        r.close()


def test_connect_sqlite_plain_tmp_path(tmp_path: Path) -> None:
    db = tmp_path / "p.db"
    conn = connect_sqlite(db, encryption_key=None, check_same_thread=False)
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
        mode = conn.execute("PRAGMA journal_mode").fetchone()
        assert mode is not None
        assert str(mode[0]).upper() == "WAL"
    finally:
        conn.close()


def test_connect_sqlite_busy_timeout_follows_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TAPPS_SQLITE_BUSY_MS", "7777")
    db = tmp_path / "busy.db"
    conn = connect_sqlite(db, encryption_key=None, check_same_thread=False)
    try:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        assert row is not None and int(row[0]) == 7777
    finally:
        conn.close()


def test_connect_sqlite_encrypted_requires_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlcipher_util, "pysqlcipher_dbapi2", lambda: None)
    db = tmp_path / "e.db"
    with pytest.raises(ImportError, match="pysqlcipher3"):
        connect_sqlite(db, encryption_key="secret", check_same_thread=False)


def test_pysqlcipher_dbapi2_returns_connect_with_injected_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = types.ModuleType("pysqlcipher3")
    sub = types.ModuleType("pysqlcipher3.dbapi2")
    sub.connect = sqlite3.connect
    monkeypatch.setitem(sys.modules, "pysqlcipher3", root)
    monkeypatch.setitem(sys.modules, "pysqlcipher3.dbapi2", sub)
    root.dbapi2 = sub  # type: ignore[attr-defined]
    fn = sqlcipher_util.pysqlcipher_dbapi2()
    assert fn is sqlite3.connect


def test_connect_sqlite_encryption_key_with_plain_sqlite_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If connect factory is std sqlite3, ``cipher_version`` is empty after ``PRAGMA key``."""
    monkeypatch.setattr(sqlcipher_util, "pysqlcipher_dbapi2", lambda: sqlite3.connect)
    db = tmp_path / "plain.db"
    with pytest.raises(sqlite3.DatabaseError, match=r"cipher_version|SQLCipher"):
        connect_sqlite(db, encryption_key="secret", check_same_thread=False)


@pytest.mark.requires_encryption
@pytest.mark.skipif(
    not sqlcipher_util.sqlcipher_available(),
    reason="pysqlcipher3 / SQLCipher not available",
)
def test_connect_sqlite_encrypted_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "enc.db"
    conn = connect_sqlite(db, encryption_key="k1", check_same_thread=False)
    try:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        ver = conn.execute("PRAGMA cipher_version").fetchone()
        assert ver is not None and str(ver[0]).strip()
    finally:
        conn.close()

    conn2 = connect_sqlite(db, encryption_key="k1", check_same_thread=False)
    try:
        row = conn2.execute("SELECT x FROM t").fetchone()
        assert row is not None and int(row[0]) == 1
    finally:
        conn2.close()

    with pytest.raises(sqlite3.DatabaseError):
        connect_sqlite(db, encryption_key="wrong", check_same_thread=False)


@pytest.mark.requires_encryption
@pytest.mark.skipif(
    not sqlcipher_util.sqlcipher_available(),
    reason="pysqlcipher3 / SQLCipher not available",
)
def test_connect_sqlite_readonly_encrypted_select(tmp_path: Path) -> None:
    db = tmp_path / "enc_ro.db"
    conn_w = connect_sqlite(db, encryption_key="k1", check_same_thread=False)
    try:
        conn_w.execute("CREATE TABLE t (x INTEGER)")
        conn_w.execute("INSERT INTO t VALUES (7)")
        conn_w.commit()
    finally:
        conn_w.close()

    conn_r = connect_sqlite_readonly(db, encryption_key="k1", check_same_thread=False)
    try:
        row = conn_r.execute("SELECT x FROM t").fetchone()
        assert row is not None and int(row[0]) == 7
    finally:
        conn_r.close()
