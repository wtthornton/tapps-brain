"""PostgreSQL connection management with pooling for Hive and Federation backends.

EPIC-055 STORY-055.2 — provides a connection pool manager using psycopg + psycopg_pool.
All psycopg imports are lazy so the rest of the package works without Postgres deps.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


class PostgresConnectionManager:
    """Connection pool manager using psycopg + psycopg_pool.

    Parameters
    ----------
    dsn:
        PostgreSQL connection string (``postgres://user:pass@host/db``).
    min_size:
        Minimum pool connections.  Falls back to ``TAPPS_BRAIN_HIVE_POOL_MIN`` env var, then ``2``.
    max_size:
        Maximum pool connections.  Falls back to ``TAPPS_BRAIN_HIVE_POOL_MAX`` env var, then ``10``.
    connect_timeout:
        Seconds to wait when acquiring a connection.  Falls back to
        ``TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT`` env var, then ``5``.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int | None = None,
        max_size: int | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size or int(os.environ.get("TAPPS_BRAIN_HIVE_POOL_MIN", "2"))
        self._max_size = max_size or int(os.environ.get("TAPPS_BRAIN_HIVE_POOL_MAX", "10"))
        self._connect_timeout = connect_timeout or float(
            os.environ.get("TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT", "5")
        )
        self._pool: Any = None

    # -- Pool lifecycle --------------------------------------------------------

    def _ensure_pool(self) -> None:
        """Create the connection pool on first use (lazy initialisation)."""
        if self._pool is not None:
            return
        try:
            from psycopg_pool import ConnectionPool  # type: ignore[import-not-found]
        except ImportError:
            raise ImportError(
                "psycopg and psycopg_pool are required for PostgreSQL backends.\n"
                "Install with: pip install 'psycopg[binary]' psycopg_pool"
            ) from None

        self._pool = ConnectionPool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            timeout=self._connect_timeout,
        )
        logger.info(
            "postgres.pool_created",
            min_size=self._min_size,
            max_size=self._max_size,
        )

    @contextmanager
    def get_connection(self) -> Iterator[Any]:
        """Yield a connection from the pool (context-managed).

        Usage::

            with manager.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        """
        self._ensure_pool()
        with self._pool.connection() as conn:
            yield conn

    def close(self) -> None:
        """Shut down the connection pool, releasing all connections."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            logger.info("postgres.pool_closed")

    @property
    def dsn(self) -> str:
        """Return the DSN this manager was created with."""
        return self._dsn

    @property
    def is_open(self) -> bool:
        """Return whether the pool has been created and not yet closed."""
        return self._pool is not None
