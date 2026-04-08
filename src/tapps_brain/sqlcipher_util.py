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
    TAPPS_SQLITE_MEMORY_READONLY_SEARCH — when ``1``/``true``/``yes``, project
        ``MemoryPersistence`` uses a second **read-only** SQLite connection for
        FTS search and sqlite-vec KNN (WAL snapshot reads) so those queries do not
        contend on the writer connection lock. See ``connect_sqlite_readonly`` and
        ``docs/guides/sqlite-database-locked.md``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

# SQLite 3.51.3 fixed a WAL-reset bug that can cause database corruption.
_MIN_SQLITE_VERSION = (3, 51, 3)
_sqlite_version_warned = False

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


def resolve_memory_readonly_search_enabled() -> bool:
    """True when ``TAPPS_SQLITE_MEMORY_READONLY_SEARCH`` opts into the read-only search conn."""
    raw = os.environ.get("TAPPS_SQLITE_MEMORY_READONLY_SEARCH", "").strip().lower()
    return raw in ("1", "true", "yes")


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


def _warn_sqlite_version_once() -> None:
    """Log a warning once if the SQLite version has the WAL-reset corruption bug."""
    global _sqlite_version_warned  # noqa: PLW0603
    if _sqlite_version_warned:
        return
    _sqlite_version_warned = True
    ver = sqlite3.sqlite_version_info
    if ver < _MIN_SQLITE_VERSION:
        logger.warning(
            "SQLite %s detected; versions before 3.51.3 have a WAL-reset "
            "bug that can cause database corruption in rare cases. "
            "Upgrade to SQLite 3.51.3+ is recommended.",
            sqlite3.sqlite_version,
        )


def connect_sqlite(
    path: str | os.PathLike[str],
    *,
    encryption_key: str | None,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """Open SQLite or SQLCipher with the same pragmas as ``MemoryPersistence``."""
    _warn_sqlite_version_once()
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


def connect_sqlite_readonly(
    path: str | os.PathLike[str],
    *,
    encryption_key: str | None,
    check_same_thread: bool = False,
) -> sqlite3.Connection:
    """Open a **read-only** SQLite or SQLCipher handle to an existing WAL database.

    Uses a ``file:`` URI with ``mode=ro`` so the connection cannot mutate schema or
    data. Skips ``PRAGMA journal_mode`` (not appropriate on RO). Applies the same
    ``busy_timeout`` and ``foreign_keys`` as ``connect_sqlite`` for consistency.

    The primary writer must have created/opened the database first (migrations, WAL).
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    if encryption_key:
        connect_fn = _require_sqlcipher()
        conn = connect_fn(uri, uri=True, check_same_thread=check_same_thread)
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
        conn = sqlite3.connect(uri, uri=True, check_same_thread=check_same_thread)
        conn.row_factory = sqlite3.Row

    busy_ms = resolve_sqlite_busy_timeout_ms()
    conn.execute(f"PRAGMA busy_timeout={busy_ms}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
