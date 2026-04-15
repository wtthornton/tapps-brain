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
        Must begin with ``postgres://`` or ``postgresql://`` (ADR-007);
        a ``ValueError`` is raised at construction time if the scheme is wrong.
    min_size:
        Minimum pool connections.  Falls back to ``TAPPS_BRAIN_PG_POOL_MIN``
        (or legacy ``TAPPS_BRAIN_HIVE_POOL_MIN``) env var, then ``2``.
    max_size:
        Maximum pool connections.  Falls back to ``TAPPS_BRAIN_PG_POOL_MAX``
        (or legacy ``TAPPS_BRAIN_HIVE_POOL_MAX``) env var, then ``10``.
    connect_timeout:
        Seconds to wait when acquiring a connection.  Falls back to
        ``TAPPS_BRAIN_PG_POOL_CONNECT_TIMEOUT_SECONDS``
        (or legacy ``TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT``) env var, then ``5``.
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
        # Validate DSN scheme at construction time (ADR-007 — Postgres-only).
        if not dsn or not dsn.startswith(("postgres://", "postgresql://")):
            scheme = dsn.split("://")[0] if "://" in dsn else "(no scheme)"
            raise ValueError(
                f"Invalid PostgreSQL DSN: must begin with 'postgres://' or 'postgresql://' "
                f"(ADR-007 — Postgres-only). Got scheme '{scheme}'. "
                f"Raw DSN is not logged to protect secrets."
            )
        self._dsn = dsn
        # New canonical env vars (TAPPS_BRAIN_PG_POOL_*) take precedence;
        # legacy TAPPS_BRAIN_HIVE_* names remain for backward compatibility.
        # NOTE: Use explicit None-checks rather than truthiness tests so that
        # caller-supplied 0 (invalid but intentional for validation) is not
        # silently overridden by the env-var default.
        self._min_size = (
            min_size
            if min_size is not None
            else int(
                os.environ.get("TAPPS_BRAIN_PG_POOL_MIN")
                or os.environ.get("TAPPS_BRAIN_HIVE_POOL_MIN", "2")
            )
        )
        self._max_size = (
            max_size
            if max_size is not None
            else int(
                os.environ.get("TAPPS_BRAIN_PG_POOL_MAX")
                or os.environ.get("TAPPS_BRAIN_HIVE_POOL_MAX", "10")
            )
        )
        self._connect_timeout = (
            connect_timeout
            if connect_timeout is not None
            else float(
                os.environ.get("TAPPS_BRAIN_PG_POOL_CONNECT_TIMEOUT_SECONDS")
                or os.environ.get("TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT", "5")
            )
        )
        _idle_env = float(os.environ.get("TAPPS_BRAIN_HIVE_POOL_IDLE_TIMEOUT", "300"))
        self._idle_timeout = idle_timeout if idle_timeout is not None else _idle_env
        # Validate pool size constraints.
        if self._max_size < 1:
            raise ValueError(
                f"Pool max_size must be >= 1 (TAPPS_BRAIN_PG_POOL_MAX). Got {self._max_size}."
            )
        if self._min_size > self._max_size:
            raise ValueError(
                f"Pool min_size ({self._min_size}) must be <= max_size ({self._max_size}). "
                f"Check TAPPS_BRAIN_PG_POOL_MIN / TAPPS_BRAIN_PG_POOL_MAX."
            )
        self._pool: Any = None

    # -- Pool lifecycle --------------------------------------------------------

    def _ensure_pool(self) -> None:
        """Create the connection pool on first use (lazy initialisation)."""
        if self._pool is not None:
            return
        try:
            from psycopg_pool import ConnectionPool
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
                from psycopg import sql as pgsql

                cur.execute(
                    pgsql.SQL("SET LOCAL tapps.current_namespace = {}").format(
                        pgsql.Literal(namespace)
                    )
                )
            yield conn

    @contextmanager
    def project_context(self, project_id: str) -> Iterator[Any]:
        """Yield a connection with ``app.project_id`` session variable set.

        EPIC-069 STORY-069.8 — enforces tenant Row Level Security on
        ``private_memories`` and ``project_profiles`` (migration
        ``private/009_project_rls.sql``).  Within the yielded transaction
        RLS restricts visible rows to those whose ``project_id`` column
        matches *project_id*; rows for any other tenant are invisible
        (and cross-tenant INSERTs fail the WITH CHECK clause).

        ``SET LOCAL`` is used so the variable is automatically cleared when
        the transaction ends (commit or rollback) — safe for pooled
        connections; no identity can leak across pool borrows.

        Parameters
        ----------
        project_id:
            The tenant identity to bind for this transaction.  Must be a
            non-empty string.  An empty string would collapse into the
            fail-closed policy and hide every row; callers that want to
            list all projects must use :meth:`admin_context` instead.

        Raises
        ------
        ValueError
            If *project_id* is empty or whitespace only.
        """
        if not project_id or not project_id.strip():
            raise ValueError(
                "project_context requires a non-empty project_id; "
                "use admin_context() for registry / admin paths."
            )
        self._ensure_pool()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                from psycopg import sql as pgsql

                cur.execute(
                    pgsql.SQL("SET LOCAL app.project_id = {}").format(pgsql.Literal(project_id))
                )
            yield conn

    @contextmanager
    def agent_context(self, agent_id: str) -> Iterator[Any]:
        """Yield a connection with ``app.agent_id`` session variable set.

        STORY-070.7 — threads the per-call ``agent_id`` into Postgres so
        any row-level security policy (or audit trigger) that filters by
        agent can see the caller identity for this transaction.

        ``SET LOCAL`` ensures the variable is cleared when the transaction
        ends (commit or rollback), making it safe for pooled connections:
        no agent identity can leak across pool borrows.

        Parameters
        ----------
        agent_id:
            The agent identity to bind for this transaction.  Must be a
            non-empty string.

        Raises
        ------
        ValueError
            If *agent_id* is empty or whitespace-only.
        """
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_context requires a non-empty agent_id")
        self._ensure_pool()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                from psycopg import sql as pgsql

                cur.execute(
                    pgsql.SQL("SET LOCAL app.agent_id = {}").format(pgsql.Literal(agent_id))
                )
            yield conn

    @contextmanager
    def admin_context(self) -> Iterator[Any]:
        """Yield a connection with ``app.is_admin = 'true'`` set.

        EPIC-069 STORY-069.8 — unlocks the admin-bypass policy on
        ``project_profiles`` so the registry (list_all / register / approve
        / delete) can see and mutate every row regardless of tenant.

        ``SET LOCAL`` ensures the elevated flag dies with the transaction
        and cannot leak across pool borrows.  This context does NOT unlock
        ``private_memories`` — that table is fail-closed and has no admin
        policy; genuine admin maintenance against ``private_memories``
        must connect as the table owner (``tapps_migrator``) which bypasses
        RLS by default.
        """
        self._ensure_pool()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.is_admin = 'true'")
            yield conn

    @property
    def dsn(self) -> str:
        """Return the DSN this manager was created with."""
        return self._dsn

    @property
    def is_open(self) -> bool:
        """Return whether the pool has been created and not yet closed."""
        return self._pool is not None
