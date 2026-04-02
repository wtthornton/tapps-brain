"""Optional SQLCipher (encrypted SQLite) connection helpers (GitHub #23).

Requires optional extra ``tapps-brain[encryption]`` (``pysqlcipher3``) and a
system SQLCipher build where the wheel does not bundle it.

Environment:
    TAPPS_BRAIN_ENCRYPTION_KEY — passphrase for project ``memory.db``
    TAPPS_BRAIN_HIVE_ENCRYPTION_KEY — optional override for ``hive.db``;
        if unset, falls back to ``TAPPS_BRAIN_ENCRYPTION_KEY``.
    TAPPS_SQLITE_BUSY_MS — SQLite ``PRAGMA busy_timeout`` in milliseconds for all
        ``connect_sqlite`` paths (memory, Hive, feedback, diagnostics). Default ``5000``
        when unset or invalid; clamped to ``0``..``3600000``. See
        ``docs/guides/sqlite-database-locked.md``.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from typing import cast

# pysqlcipher3.dbapi2 mirrors sqlite3 for connections used here.
_ConnectFactory = Callable[..., sqlite3.Connection]


def resolve_memory_encryption_key(explicit: str | None) -> str | None:
    """Return passphrase for the project memory store."""
    if explicit is not None:
        return explicit.strip() or None
    return os.environ.get("TAPPS_BRAIN_ENCRYPTION_KEY", "").strip() or None


def resolve_hive_encryption_key(explicit: str | None) -> str | None:
    """Return passphrase for Hive ``hive.db`` (hive-specific env or shared)."""
    if explicit is not None:
        return explicit.strip() or None
    h = os.environ.get("TAPPS_BRAIN_HIVE_ENCRYPTION_KEY", "").strip()
    if h:
        return h
    return os.environ.get("TAPPS_BRAIN_ENCRYPTION_KEY", "").strip() or None


def pysqlcipher_dbapi2() -> _ConnectFactory | None:
    """Return SQLCipher connect factory, or ``None`` if not installed."""
    try:
        from pysqlcipher3 import dbapi2 as sc

        return cast("_ConnectFactory", sc.connect)
    except ImportError:
        return None


def sqlcipher_available() -> bool:
    return pysqlcipher_dbapi2() is not None


_DEFAULT_SQLITE_BUSY_MS = 5000
_MAX_SQLITE_BUSY_MS = 3_600_000


def resolve_sqlite_busy_timeout_ms() -> int:
    """Return ``PRAGMA busy_timeout`` value from ``TAPPS_SQLITE_BUSY_MS`` or default."""
    raw = os.environ.get("TAPPS_SQLITE_BUSY_MS", "").strip()
    if not raw:
        return _DEFAULT_SQLITE_BUSY_MS
    try:
        ms = int(raw, 10)
    except ValueError:
        return _DEFAULT_SQLITE_BUSY_MS
    if ms < 0 or ms > _MAX_SQLITE_BUSY_MS:
        return _DEFAULT_SQLITE_BUSY_MS
    return ms


def pragma_key_statement(passphrase: str) -> str:
    """Build ``PRAGMA key`` with single-quote escaping."""
    esc = passphrase.replace("'", "''")
    return f"PRAGMA key = '{esc}'"


def _require_sqlcipher() -> _ConnectFactory:
    c = pysqlcipher_dbapi2()
    if c is None:
        msg = (
            "Encrypted database requested but pysqlcipher3 is not installed. "
            "Install: pip install 'tapps-brain[encryption]' (and system SQLCipher "
            "where required). See docs/guides/sqlcipher.md."
        )
        raise ImportError(msg)
    return c


def connect_sqlite(
    path: str | os.PathLike[str],
    *,
    encryption_key: str | None,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """Open SQLite or SQLCipher with the same pragmas as ``MemoryPersistence``."""
    path_str = str(path)
    if encryption_key:
        connect_fn = _require_sqlcipher()
        conn = connect_fn(path_str, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row
        conn.execute(pragma_key_statement(encryption_key))
        try:
            row = conn.execute("PRAGMA cipher_version").fetchone()
            if row is None or not str(row[0] or "").strip():
                conn.close()
                msg = "SQLCipher PRAGMA key failed or cipher_version missing"
                raise sqlite3.DatabaseError(msg)
        except sqlite3.Error:
            conn.close()
            raise
    else:
        conn = sqlite3.connect(path_str, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    busy_ms = resolve_sqlite_busy_timeout_ms()
    conn.execute(f"PRAGMA busy_timeout={busy_ms}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
