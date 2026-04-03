"""Optional sqlite-vec ANN index for semantic retrieval (GitHub #30).

Loads the sqlite-vec extension when the ``sqlite-vec`` package is installed,
maintains a ``memory_vec`` vec0 table keyed by memory ``key``, and exposes
KNN search. Safe no-op when the extension is unavailable or fails to load.

**Distance metric:** The vec0 DDL uses ``embedding float[N]`` (default *N* =
:data:`DEFAULT_VEC_DIM`) without ``distance_metric=``, so sqlite-vec applies
its default for float vectors — **L2 (Euclidean) distance**. :func:`knn_search` runs::

    SELECT key, distance FROM memory_vec
    WHERE embedding MATCH ? AND k = ?

The ``distance`` column is that metric; **lower is better**. Default
embeddings are L2-normalized, so L2 ranking is order-equivalent to cosine
ranking for same-length unit vectors. Operators: ``docs/guides/sqlite-vec-operators.md``.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tapps_brain.models import MemoryEntry

logger = structlog.get_logger(__name__)

# Default embedding width (sentence-transformers all-MiniLM-L6-v2).
DEFAULT_VEC_DIM = 384

_VEC_TABLE = "memory_vec"


def try_load_extension(conn: sqlite3.Connection) -> bool:
    """Load sqlite-vec into *conn*. Returns False if unavailable or load fails."""
    try:
        import sqlite_vec
    except ImportError:
        logger.debug("sqlite_vec_package_missing")
        return False
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    except (AttributeError, OSError, sqlite3.OperationalError) as e:
        logger.debug("sqlite_vec_load_failed", error=str(e))
        return False
    return True


def vec_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_VEC_TABLE,),
    ).fetchone()
    return row is not None


def ensure_memory_vec_table(conn: sqlite3.Connection, *, dim: int = DEFAULT_VEC_DIM) -> None:
    """Create ``memory_vec`` vec0 table if missing."""
    if vec_table_exists(conn):
        return
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE {_VEC_TABLE} USING vec0(
          key TEXT,
          embedding float[{dim}]
        )
        """
    )
    conn.commit()


def delete_vec_key(conn: sqlite3.Connection, key: str) -> None:
    """Remove *key* from the vector index if present."""
    if not vec_table_exists(conn):
        return
    conn.execute(f"DELETE FROM {_VEC_TABLE} WHERE key = ?", (key,))


def upsert_vec_row(conn: sqlite3.Connection, key: str, embedding: list[float]) -> None:
    """Replace the vector row for *key* (DELETE then INSERT with a new ``rowid``).

    Per-save sync uses this path; there is no multi-row batching (see operator doc).
    """
    import sqlite_vec

    if not vec_table_exists(conn):
        return
    blob = sqlite_vec.serialize_float32(embedding)
    conn.execute(f"DELETE FROM {_VEC_TABLE} WHERE key = ?", (key,))
    rid_row = conn.execute(f"SELECT COALESCE(MAX(rowid), 0) + 1 FROM {_VEC_TABLE}").fetchone()
    rid = max(int(rid_row[0]) if rid_row else 1, 1)
    conn.execute(
        f"INSERT INTO {_VEC_TABLE}(rowid, key, embedding) VALUES (?, ?, ?)",
        (rid, key, blob),
    )


def knn_search(
    conn: sqlite3.Connection,
    query_embedding: list[float],
    k: int,
    *,
    dim: int = DEFAULT_VEC_DIM,
) -> list[tuple[str, float]]:
    """Return up to *k* nearest neighbors as ``(key, distance)``.

    ``distance`` is sqlite-vec vec0 **L2** distance from the query blob (lower = closer).
    """
    import sqlite_vec

    if not vec_table_exists(conn) or k <= 0:
        return []
    if len(query_embedding) != dim:
        logger.debug("sqlite_vec_knn_dim_mismatch", expected=dim, got=len(query_embedding))
        return []
    qblob = sqlite_vec.serialize_float32(query_embedding)
    try:
        rows = conn.execute(
            f"""
            SELECT key, distance
            FROM {_VEC_TABLE}
            WHERE embedding MATCH ? AND k = ?
            """,
            (qblob, k),
        ).fetchall()
    except sqlite3.OperationalError as e:
        logger.debug("sqlite_vec_knn_failed", error=str(e))
        return []
    return [(str(r[0]), float(r[1])) for r in rows]


def vec_row_count(conn: sqlite3.Connection) -> int:
    if not vec_table_exists(conn):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {_VEC_TABLE}").fetchone()
    return int(row[0]) if row else 0


def backfill_from_memories(
    conn: sqlite3.Connection,
    entries: list[MemoryEntry],
    *,
    dim: int = DEFAULT_VEC_DIM,
) -> int:
    """Insert vec rows for entries that have embeddings of length *dim*. Returns count."""
    if not vec_table_exists(conn):
        return 0
    n = 0
    for e in entries:
        if e.embedding is None or len(e.embedding) != dim:
            continue
        upsert_vec_row(conn, e.key, e.embedding)
        n += 1
    return n


def maybe_backfill_if_empty(conn: sqlite3.Connection, entries: list[MemoryEntry]) -> int:
    """Backfill when the vec table is empty but memories carry embeddings."""
    if not vec_table_exists(conn):
        return 0
    if vec_row_count(conn) > 0:
        return 0
    return backfill_from_memories(conn, entries)
