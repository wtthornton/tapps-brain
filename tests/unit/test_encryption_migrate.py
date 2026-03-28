"""Tests for encrypt / decrypt / rekey helpers (GitHub #23)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tapps_brain.encryption_migrate import (
    decrypt_to_plain_database,
    encrypt_plain_database,
    rekey_database,
)
from tapps_brain.sqlcipher_util import connect_sqlite, sqlcipher_available


def test_encrypt_plain_database_import_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("tapps_brain.encryption_migrate.pysqlcipher_dbapi2", lambda: None)
    with pytest.raises(ImportError, match="pysqlcipher3"):
        encrypt_plain_database(tmp_path / "a.db", tmp_path / "b.db", "pw")


def test_encrypt_plain_database_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    with pytest.raises(FileNotFoundError, match="Plain database not found"):
        encrypt_plain_database(tmp_path / "missing.db", tmp_path / "out.db", "pw")


def test_encrypt_plain_database_dest_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    plain = tmp_path / "p.db"
    sqlite3.connect(str(plain)).close()
    enc = tmp_path / "e.db"
    enc.write_text("exists", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        encrypt_plain_database(plain, enc, "pw")


def test_decrypt_import_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tapps_brain.encryption_migrate.pysqlcipher_dbapi2", lambda: None)
    with pytest.raises(ImportError, match="pysqlcipher3"):
        decrypt_to_plain_database(tmp_path / "e.db", "pw", tmp_path / "p.db")


def test_decrypt_encrypted_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    with pytest.raises(FileNotFoundError, match="Encrypted database not found"):
        decrypt_to_plain_database(tmp_path / "nope.db", "pw", tmp_path / "out.db")


def test_decrypt_plain_dest_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    enc = tmp_path / "e.db"
    sqlite3.connect(str(enc)).close()
    out = tmp_path / "p.db"
    out.write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        decrypt_to_plain_database(enc, "pw", out)


def test_rekey_import_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("tapps_brain.encryption_migrate.pysqlcipher_dbapi2", lambda: None)
    with pytest.raises(ImportError, match="pysqlcipher3"):
        rekey_database(tmp_path / "db.db", "old", "new")


def test_rekey_database_not_found(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    with pytest.raises(FileNotFoundError, match="Database not found"):
        rekey_database(tmp_path / "missing.db", "old", "new")


def test_encrypt_plain_database_runs_backup_with_stub_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    plain = tmp_path / "p.db"
    src = sqlite3.connect(str(plain), check_same_thread=False)
    try:
        src.execute("CREATE TABLE t (x INTEGER)")
        src.execute("INSERT INTO t VALUES (42)")
        src.commit()
    finally:
        src.close()
    enc = tmp_path / "e.db"

    def fake_connect(
        path: object,
        *,
        encryption_key: str | None,
        check_same_thread: bool,
    ) -> sqlite3.Connection:
        assert encryption_key == "pw"
        return sqlite3.connect(str(path), check_same_thread=check_same_thread)

    monkeypatch.setattr("tapps_brain.encryption_migrate.connect_sqlite", fake_connect)
    encrypt_plain_database(plain, enc, "pw")
    dst = sqlite3.connect(str(enc), check_same_thread=False)
    try:
        row = dst.execute("SELECT x FROM t").fetchone()
        assert row is not None and int(row[0]) == 42
    finally:
        dst.close()


def test_decrypt_to_plain_runs_backup_with_stub_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: sqlite3.connect,
    )
    enc = tmp_path / "e.db"
    es = sqlite3.connect(str(enc), check_same_thread=False)
    try:
        es.execute("CREATE TABLE u (y TEXT)")
        es.execute("INSERT INTO u VALUES ('z')")
        es.commit()
    finally:
        es.close()
    out = tmp_path / "plain_out.db"

    def fake_connect(
        path: object,
        *,
        encryption_key: str | None,
        check_same_thread: bool,
    ) -> sqlite3.Connection:
        assert encryption_key == "sekrit"
        return sqlite3.connect(str(path), check_same_thread=check_same_thread)

    monkeypatch.setattr("tapps_brain.encryption_migrate.connect_sqlite", fake_connect)
    decrypt_to_plain_database(enc, "sekrit", out)
    q = sqlite3.connect(str(out), check_same_thread=False)
    try:
        row = q.execute("SELECT y FROM u").fetchone()
        assert row is not None and row[0] == "z"
    finally:
        q.close()


def test_rekey_database_commits_with_fake_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "hive.db"
    db.write_bytes(b"x")

    class FakeCursor:
        def __init__(self, sql: str) -> None:
            self._sql = sql

        def fetchone(self) -> tuple[str] | None:
            if "cipher_version" in self._sql:
                return ("4",)
            return None

    class FakeConn:
        def __init__(self) -> None:
            self.row_factory = sqlite3.Row
            self.commits = 0

        def execute(self, sql: str, *_a: object) -> FakeCursor:
            return FakeCursor(sql)

        def commit(self) -> None:
            self.commits += 1

        def close(self) -> None:
            pass

    captured: dict[str, FakeConn] = {}

    def factory(_path: str, **kwargs: object) -> FakeConn:
        c = FakeConn()
        captured["conn"] = c
        return c

    monkeypatch.setattr("tapps_brain.encryption_migrate.pysqlcipher_dbapi2", lambda: factory)
    rekey_database(db, "old-pass", "new'pass")
    assert captured["conn"].commits == 1


def test_rekey_database_bad_cipher_version_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "bad.db"
    db.write_bytes(b"x")

    class FakeCursor:
        def __init__(self, sql: str) -> None:
            self._sql = sql

        def fetchone(self) -> tuple[str] | None:
            if "cipher_version" in self._sql:
                return ("",)
            return None

    class FakeConn:
        def __init__(self) -> None:
            self.row_factory = sqlite3.Row

        def execute(self, sql: str, *_a: object) -> FakeCursor:
            return FakeCursor(sql)

        def commit(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "tapps_brain.encryption_migrate.pysqlcipher_dbapi2",
        lambda: lambda *_a, **_k: FakeConn(),
    )
    with pytest.raises(sqlite3.DatabaseError, match="Invalid old passphrase"):
        rekey_database(db, "old", "new")


@pytest.mark.requires_encryption
@pytest.mark.skipif(not sqlcipher_available(), reason="SQLCipher not available")
def test_encrypt_decrypt_roundtrip(tmp_path: Path) -> None:
    plain = tmp_path / "plain.db"
    enc = tmp_path / "secret.db"
    out = tmp_path / "out.db"
    p = sqlite3.connect(str(plain), check_same_thread=False)
    try:
        p.execute("CREATE TABLE t (x TEXT)")
        p.execute("INSERT INTO t VALUES ('hi')")
        p.commit()
    finally:
        p.close()

    encrypt_plain_database(plain, enc, "pw1")
    decrypt_to_plain_database(enc, "pw1", out)

    q = sqlite3.connect(str(out), check_same_thread=False)
    try:
        row = q.execute("SELECT x FROM t").fetchone()
        assert row is not None and row[0] == "hi"
    finally:
        q.close()


@pytest.mark.requires_encryption
@pytest.mark.skipif(not sqlcipher_available(), reason="SQLCipher not available")
def test_rekey_database(tmp_path: Path) -> None:
    plain = tmp_path / "p.db"
    enc = tmp_path / "e.db"
    p = sqlite3.connect(str(plain), check_same_thread=False)
    try:
        p.execute("CREATE TABLE u (n INTEGER)")
        p.execute("INSERT INTO u VALUES (7)")
        p.commit()
    finally:
        p.close()
    encrypt_plain_database(plain, enc, "old-secret")

    rekey_database(enc, "old-secret", "new-secret")

    c = connect_sqlite(enc, encryption_key="new-secret", check_same_thread=False)
    try:
        row = c.execute("SELECT n FROM u").fetchone()
        assert row is not None and int(row[0]) == 7
    finally:
        c.close()

    with pytest.raises(sqlite3.DatabaseError):
        connect_sqlite(enc, encryption_key="old-secret", check_same_thread=False)
