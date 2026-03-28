"""In-place encrypt / decrypt / rekey for SQLite files using SQLCipher (GitHub #23)."""

from __future__ import annotations

import sqlite3
from pathlib import Path  # noqa: TC003

from tapps_brain.sqlcipher_util import connect_sqlite, pragma_key_statement, pysqlcipher_dbapi2


def encrypt_plain_database(plain_path: Path, encrypted_path: Path, passphrase: str) -> None:
    """Copy a standard SQLite file to a new SQLCipher-encrypted file."""
    if pysqlcipher_dbapi2() is None:
        msg = "pysqlcipher3 is required for encryption"
        raise ImportError(msg)
    if not plain_path.is_file():
        msg = f"Plain database not found: {plain_path}"
        raise FileNotFoundError(msg)
    if encrypted_path.exists():
        msg = f"Refusing to overwrite existing file: {encrypted_path}"
        raise FileExistsError(msg)

    src = sqlite3.connect(str(plain_path), check_same_thread=False)
    try:
        dst = connect_sqlite(encrypted_path, encryption_key=passphrase, check_same_thread=False)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def decrypt_to_plain_database(encrypted_path: Path, passphrase: str, plain_path: Path) -> None:
    """Copy a SQLCipher database to a new standard SQLite file."""
    if pysqlcipher_dbapi2() is None:
        msg = "pysqlcipher3 is required for decryption"
        raise ImportError(msg)
    if not encrypted_path.is_file():
        msg = f"Encrypted database not found: {encrypted_path}"
        raise FileNotFoundError(msg)
    if plain_path.exists():
        msg = f"Refusing to overwrite existing file: {plain_path}"
        raise FileExistsError(msg)

    src = connect_sqlite(encrypted_path, encryption_key=passphrase, check_same_thread=False)
    try:
        dst = sqlite3.connect(str(plain_path), check_same_thread=False)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def rekey_database(db_path: Path, old_passphrase: str, new_passphrase: str) -> None:
    """Change passphrase in place (SQLCipher ``PRAGMA rekey``)."""
    if pysqlcipher_dbapi2() is None:
        msg = "pysqlcipher3 is required for rekey"
        raise ImportError(msg)
    if not db_path.is_file():
        msg = f"Database not found: {db_path}"
        raise FileNotFoundError(msg)

    connect_fn = pysqlcipher_dbapi2()
    assert connect_fn is not None
    conn = connect_fn(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(pragma_key_statement(old_passphrase))
        row = conn.execute("PRAGMA cipher_version").fetchone()
        if row is None or not str(row[0] or "").strip():
            msg = "Invalid old passphrase or not a SQLCipher database"
            raise sqlite3.DatabaseError(msg)
        esc_new = new_passphrase.replace("'", "''")
        conn.execute(f"PRAGMA rekey = '{esc_new}'")
        conn.commit()
    finally:
        conn.close()
