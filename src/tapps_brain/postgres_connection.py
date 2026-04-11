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
    idle_timeout:
        Seconds before an idle connection is closed and evicted from the pool.
        Falls back to ``TAPPS_BRAIN_HIVE_POOL_IDLE_TIMEOUT`` env var, then ``300`` (5 min).
        Pass ``0`` to disable idle eviction.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int | None = None,
        max_size: int | None = None,
        connect_timeout: float | None = None,
        idle_timeout: float | None = None,
    ) -> None:
        self._dsn = dsn
        self._min_size = min_size or int(os.environ.get("TAPPS_BRAIN_HIVE_POOL_MIN", "2"))
        self._max_size = max_size or int(os.environ.get("TAPPS_BRAIN_HIVE_POOL_MAX", "10"))
        self._connect_timeout = connect_timeout or float(
            os.environ.get("TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT", "5")
        )
        _idle_env = float(os.environ.get("TAPPS_BRAIN_HIVE_POOL_IDLE_TIMEOUT", "300"))
        self._idle_timeout = idle_timeout if idle_timeout is not None else _idle_env
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

        kwargs: dict[str, Any] = {
            "min_size": self._min_size,
            "max_size": self._max_size,
            "timeout": self._connect_timeout,
        }
        if self._idle_timeout > 0:
            kwargs["max_idle"] = self._idle_timeout
        self._pool = ConnectionPool(self._dsn, **kwargs)
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

    def get_pool_stats(self) -> dict[str, Any]:
        """Return current pool statistics.

        Returns a dict with at least:

        - ``pool_min`` — configured minimum connections
        - ``pool_max`` — configured maximum connections
        - ``pool_size`` — current open connections (0 if pool not initialised)
        - ``pool_available`` — idle connections ready to serve requests
        - ``pool_saturation`` — fraction of max_size in use (0.0 - 1.0)
        - ``idle_timeout`` — configured idle eviction timeout in seconds

        When the pool has not been opened yet (e.g. lazy init not triggered)
        ``pool_size`` and ``pool_available`` will be 0 and ``pool_saturation``
        will be 0.0.
        """
        base: dict[str, Any] = {
            "pool_min": self._min_size,
            "pool_max": self._max_size,
            "pool_size": 0,
            "pool_available": 0,
            "pool_saturation": 0.0,
            "idle_timeout": self._idle_timeout,
        }
        if self._pool is None:
            return base
        try:
            raw = self._pool.get_stats()
            size = int(raw.get("pool_size", 0))
            available = int(raw.get("pool_available", 0))
            saturation = (size - available) / self._max_size if self._max_size > 0 else 0.0
            base.update(
                {
                    "pool_size": size,
                    "pool_available": available,
                    "pool_saturation": round(max(0.0, min(1.0, saturation)), 4),
                }
            )
        except Exception:
            pass
        return base

    @contextmanager
    def namespace_context(self, namespace: str) -> Iterator[Any]:
        """Yield a connection with ``tapps.current_namespace`` session variable set.

        This is the documented pattern for enforcing namespace-based Row Level
        Security (RLS) on ``hive_memories`` (EPIC-063 STORY-063.3).  Within the
        yielded transaction the ``hive_namespace_isolation`` policy will restrict
        visible rows to those whose ``namespace`` column matches *namespace*.

        ``SET LOCAL`` is used so the variable is automatically cleared when the
        transaction ends (commit or rollback) — safe for pooled connections.

        Usage::

            with manager.namespace_context("project-alpha") as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT * FROM hive_memories WHERE key = %s",
                        (key,),
                    )

        When RLS is enabled (migration ``hive/002_rls_spike.sql`` applied),
        this call restricts all reads and writes within the transaction to rows
        with ``namespace = <namespace>``.  Without the migration or when
        connecting as a superuser/table-owner, the ``SET LOCAL`` is harmless.

        Parameters
        ----------
        namespace:
            The namespace value to bind for this transaction.  Must not be
            empty; pass ``""`` only to explicitly invoke the admin-bypass policy
            (all rows visible, no isolation).
        """
        self._ensure_pool()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL tapps.current_namespace = %s", (namespace,))
            yield conn

    @property
    def dsn(self) -> str:
        """Return the DSN this manager was created with."""
        return self._dsn

    @property
    def is_open(self) -> bool:
        """Return whether the pool has been created and not yet closed."""
        return self._pool is not None
