"""PostgreSQL connection management with pooling for Hive and Federation backends.

EPIC-055 STORY-055.2 — provides a connection pool manager using psycopg + psycopg_pool.
All psycopg imports are lazy so the rest of the package works without Postgres deps.

STORY-072.1 — also exposes an asyncio-native ``psycopg_pool.AsyncConnectionPool``
alongside the sync pool.  Both pools share DSN + env-var configuration and the
non-privileged-role guard, but their lifecycles are independent: callers in a
sync context use :meth:`get_connection` (sync pool); callers in an asyncio
event loop use :meth:`get_async_connection` (async pool).  The two pools never
share connections.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

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
    max_waiting:
        Maximum number of requests that may queue waiting for a free connection.
        Falls back to ``TAPPS_BRAIN_PG_POOL_MAX_WAITING`` env var, then ``20``.
        Prevents unbounded backpressure under sustained overload.
    max_lifetime:
        Maximum lifetime of a connection in seconds; psycopg_pool will close
        and replace connections that exceed this age.  Falls back to
        ``TAPPS_BRAIN_PG_POOL_MAX_LIFETIME_SECONDS`` env var, then ``3600`` (1 hour).
        Pass ``0`` to disable recycling.
    """

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int | None = None,
        max_size: int | None = None,
        connect_timeout: float | None = None,
        idle_timeout: float | None = None,
        max_waiting: int | None = None,
        max_lifetime: float | None = None,
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
        self._max_waiting = (
            max_waiting
            if max_waiting is not None
            else int(os.environ.get("TAPPS_BRAIN_PG_POOL_MAX_WAITING", "20"))
        )
        self._max_lifetime = (
            max_lifetime
            if max_lifetime is not None
            else float(os.environ.get("TAPPS_BRAIN_PG_POOL_MAX_LIFETIME_SECONDS", "3600"))
        )
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
        # STORY-072.1: async-native pool slot.  Lifecycle is independent from
        # the sync pool — created lazily on first ``get_async_connection`` /
        # ``get_async_pool`` call inside an event loop, closed by
        # ``close_async``.  Operators that never enter the async path never
        # pay the AsyncConnectionPool import / open cost.
        self._async_pool: Any = None
        self._async_init_lock: asyncio.Lock | None = None

    # -- Pool lifecycle --------------------------------------------------------

    @staticmethod
    def _reset_session_vars(conn: Any) -> None:  # noqa: ANN401 — psycopg Connection
        """TAP-514: clear tenant/agent session variables on connection release.

        ``project_context`` / ``agent_context`` / ``admin_context`` /
        ``namespace_context`` use SESSION-level ``SET`` (not ``SET LOCAL``)
        so the bound identity survives multiple transactions inside one
        pool borrow.  This callback runs when the connection is returned
        to the pool and wipes those variables so the next borrower starts
        clean.

        Raised exceptions cause psycopg_pool to close the connection
        rather than recycle it — fail-safe.
        """
        # ``cur.execute`` on a non-autocommit connection opens an implicit
        # transaction; without an explicit commit the connection goes back
        # to the pool in INTRANS state, psycopg_pool discards it as BAD,
        # and we lose the pooling benefit on every release.  Commit (not
        # rollback) — RESET is transactional in Postgres, so a rollback
        # would silently undo the RESETs and leak the previous borrower's
        # session identity into the next borrow.
        with conn.cursor() as cur:
            cur.execute(
                "RESET app.project_id; "
                "RESET app.agent_id; "
                "RESET app.is_admin; "
                "RESET tapps.current_namespace"
            )
        conn.commit()

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
            "max_waiting": self._max_waiting,
            "reset": self._reset_session_vars,
        }
        if self._idle_timeout > 0:
            kwargs["max_idle"] = self._idle_timeout
        if self._max_lifetime > 0:
            kwargs["max_lifetime"] = self._max_lifetime
        self._pool = ConnectionPool(self._dsn, **kwargs)
        logger.info(
            "postgres.pool_created",
            min_size=self._min_size,
            max_size=self._max_size,
            max_waiting=self._max_waiting,
            max_lifetime=self._max_lifetime,
        )

        # TAP-512: fail fast if the connected role can bypass RLS.  RLS is
        # only meaningful when the runtime role is non-owner with
        # BYPASSRLS=false; deploying as the table owner (tapps_migrator) or
        # a superuser silently disables tenant isolation.  Operators that
        # genuinely need a privileged role (CI, dev, one-off maintenance)
        # set TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 to acknowledge the risk.
        try:
            self._assert_non_privileged_role()
        except Exception:
            self._pool.close()
            self._pool = None
            raise

    def _assert_non_privileged_role(self) -> None:
        """Verify the connected role cannot bypass RLS on tenanted tables.

        Raises ``RuntimeError`` when the role is a superuser, has
        ``BYPASSRLS = true``, or owns ``private_memories`` /
        ``project_profiles`` — unless ``TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1``
        is set, in which case the violation is logged at WARNING but
        startup proceeds.
        """
        allow_override = os.environ.get("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", "") == "1"
        # TAP-783: assert is stripped by Python -O; use an explicit guard.
        if self._pool is None:  # pragma: no cover
            raise RuntimeError(
                "_assert_non_privileged_role called before connection pool was created"
            )
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = cur.fetchone()
            if row is None:
                # No matching pg_roles row — extremely unlikely but treat as
                # privileged out of caution.
                current_user, is_super, bypass_rls = "(unknown)", True, True
            else:
                current_user, is_super, bypass_rls = row[0], bool(row[1]), bool(row[2])

            cur.execute(
                "SELECT relname FROM pg_class "
                "JOIN pg_namespace ON pg_class.relnamespace = pg_namespace.oid "
                "WHERE relname IN ('private_memories', 'project_profiles') "
                "  AND pg_get_userbyid(relowner) = current_user"
            )
            owned = sorted(r[0] for r in cur.fetchall())

        violations: list[str] = []
        if is_super:
            violations.append("rolsuper=true (superuser bypasses RLS)")
        if bypass_rls:
            violations.append("rolbypassrls=true (BYPASSRLS bypasses RLS)")
        if owned:
            violations.append(
                f"role owns tenanted tables {owned} (table owners bypass RLS unless FORCE is set)"
            )

        if not violations:
            logger.info(
                "postgres.role_check_ok",
                current_user=current_user,
                tables_force_rls=["private_memories", "project_profiles"],
            )
            return

        if allow_override:
            # TAP-783: emit a prominent audit record so operators can detect
            # accidental production use of the override flag.  Level is ERROR
            # (not WARNING) so log aggregators can alert on it.
            logger.error(
                "postgres.privileged_role_audit_override",
                current_user=current_user,
                violations=violations,
                allow_privileged_role_env="TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1",
                detail=(
                    "TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 is set; tenant "
                    "isolation is NOT enforced for this connection.  "
                    "Acceptable in CI/dev only — must not appear in production logs."
                ),
            )
            return

        raise RuntimeError(
            "tapps-brain refuses to start as a privileged Postgres role "
            f"({current_user}): {'; '.join(violations)}.  Connect as a "
            "non-owner role with BYPASSRLS=false (see "
            "migrations/roles/001_db_roles.sql for the recommended "
            "tapps_runtime role).  To override for CI/dev, set "
            "TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1."
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
        - ``pool_stats_available`` — ``True`` when live stats were successfully
          read from the pool; ``False`` when the pool is not yet open or when
          ``get_stats()`` raised.  Operators can use this flag to distinguish
          "healthy idle pool" from "observability gap".

        When the pool has not been opened yet (e.g. lazy init not triggered)
        ``pool_size`` and ``pool_available`` will be 0, ``pool_saturation``
        will be 0.0, and ``pool_stats_available`` will be ``False``.
        """
        base: dict[str, Any] = {
            "pool_min": self._min_size,
            "pool_max": self._max_size,
            "pool_size": 0,
            "pool_available": 0,
            "pool_saturation": 0.0,
            "idle_timeout": self._idle_timeout,
            "max_waiting": self._max_waiting,
            "max_lifetime": self._max_lifetime,
            "pool_stats_available": False,
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
                    "pool_stats_available": True,
                }
            )
        except Exception as exc:
            # get_stats() can raise if the pool is in a transient bad state or
            # if the psycopg_pool API has changed (e.g. renamed method).  Log
            # at DEBUG so operators can detect the observability gap without
            # noisy ERROR-level alerts on a non-critical path.
            logger.debug(
                "postgres_connection.pool_stats_unavailable",
                error=type(exc).__name__,
                detail=str(exc),
            )
            # pool_stats_available stays False; size/saturation stay at 0.
        return base

    # -- Async pool (STORY-072.1) ---------------------------------------------

    @staticmethod
    async def _reset_session_vars_async(conn: Any) -> None:  # noqa: ANN401
        """Async equivalent of :meth:`_reset_session_vars`.

        psycopg_pool's ``AsyncConnectionPool`` invokes the reset callback as
        ``await reset(conn)`` when the connection is returned to the pool.
        Wipes the same session variables as the sync path so the next
        borrower starts with a clean tenant identity.
        """
        # See _reset_session_vars for the rationale: commit closes the
        # implicit transaction so psycopg_pool recycles the connection
        # instead of discarding it, and a commit (not rollback) is
        # required to make the RESETs persist past the next borrow.
        async with conn.cursor() as cur:
            await cur.execute(
                "RESET app.project_id; "
                "RESET app.agent_id; "
                "RESET app.is_admin; "
                "RESET tapps.current_namespace"
            )
        await conn.commit()

    async def _ensure_async_pool(self) -> None:
        """Create the async connection pool on first use (lazy, event-loop-safe).

        psycopg's ``AsyncConnectionPool`` must be opened inside a running
        event loop, so this method is async — unlike the sync ``_ensure_pool``
        which can run from any context.  Concurrent first calls are
        serialised by ``self._async_init_lock`` to avoid double-open.
        """
        if self._async_pool is not None:
            return
        if self._async_init_lock is None:
            # Lock is created lazily because creating it requires a running
            # loop; one manager instance can be reused across loops in tests
            # so we recreate it if the loop changed.
            self._async_init_lock = asyncio.Lock()
        async with self._async_init_lock:
            if self._async_pool is not None:
                return
            try:
                from psycopg_pool import AsyncConnectionPool
            except ImportError:
                raise ImportError(
                    "psycopg and psycopg_pool are required for PostgreSQL backends.\n"
                    "Install with: pip install 'psycopg[binary]' psycopg_pool"
                ) from None

            kwargs: dict[str, Any] = {
                "min_size": self._min_size,
                "max_size": self._max_size,
                "timeout": self._connect_timeout,
                "max_waiting": self._max_waiting,
                "reset": self._reset_session_vars_async,
                # Required by psycopg_pool >= 3.2 — opening implicitly inside
                # __init__ is deprecated for AsyncConnectionPool because it
                # would block the event loop on first connect.
                "open": False,
            }
            if self._idle_timeout > 0:
                kwargs["max_idle"] = self._idle_timeout
            if self._max_lifetime > 0:
                kwargs["max_lifetime"] = self._max_lifetime
            pool = AsyncConnectionPool(self._dsn, **kwargs)
            await pool.open()
            self._async_pool = pool
            logger.info(
                "postgres.async_pool_created",
                min_size=self._min_size,
                max_size=self._max_size,
                max_waiting=self._max_waiting,
                max_lifetime=self._max_lifetime,
            )

            # TAP-512 / TAP-783 parity with sync path — refuse to start as a
            # privileged role unless TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1.
            try:
                await self._assert_non_privileged_role_async()
            except Exception:
                await self._async_pool.close()
                self._async_pool = None
                raise

    async def _assert_non_privileged_role_async(self) -> None:
        """Async parity for :meth:`_assert_non_privileged_role`."""
        allow_override = os.environ.get("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", "") == "1"
        if self._async_pool is None:  # pragma: no cover
            raise RuntimeError(
                "_assert_non_privileged_role_async called before async pool was created"
            )
        async with self._async_pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT current_user, rolsuper, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = await cur.fetchone()
            if row is None:
                current_user, is_super, bypass_rls = "(unknown)", True, True
            else:
                current_user, is_super, bypass_rls = row[0], bool(row[1]), bool(row[2])

            await cur.execute(
                "SELECT relname FROM pg_class "
                "JOIN pg_namespace ON pg_class.relnamespace = pg_namespace.oid "
                "WHERE relname IN ('private_memories', 'project_profiles') "
                "  AND pg_get_userbyid(relowner) = current_user"
            )
            owned = sorted(r[0] for r in await cur.fetchall())

        violations: list[str] = []
        if is_super:
            violations.append("rolsuper=true (superuser bypasses RLS)")
        if bypass_rls:
            violations.append("rolbypassrls=true (BYPASSRLS bypasses RLS)")
        if owned:
            violations.append(
                f"role owns tenanted tables {owned} (table owners bypass RLS unless FORCE is set)"
            )

        if not violations:
            logger.info(
                "postgres.async_role_check_ok",
                current_user=current_user,
                tables_force_rls=["private_memories", "project_profiles"],
            )
            return

        if allow_override:
            logger.error(
                "postgres.privileged_role_audit_override",
                current_user=current_user,
                violations=violations,
                allow_privileged_role_env="TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1",
                pool="async",
                detail=(
                    "TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1 is set; tenant "
                    "isolation is NOT enforced for this connection.  "
                    "Acceptable in CI/dev only — must not appear in production logs."
                ),
            )
            return

        raise RuntimeError(
            "tapps-brain refuses to start as a privileged Postgres role "
            f"({current_user}): {'; '.join(violations)}.  Connect as a "
            "non-owner role with BYPASSRLS=false (see "
            "migrations/roles/001_db_roles.sql for the recommended "
            "tapps_runtime role).  To override for CI/dev, set "
            "TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE=1."
        )

    async def get_async_pool(self) -> Any:  # noqa: ANN401 — psycopg AsyncConnectionPool
        """Return the lazily-initialised ``psycopg_pool.AsyncConnectionPool``.

        Most callers should prefer :meth:`get_async_connection` for the
        context-managed checkout.  Direct pool access is intended for
        advanced uses (e.g. ``await pool.wait()`` in a FastAPI lifespan
        handler to pre-warm connections).
        """
        await self._ensure_async_pool()
        return self._async_pool

    @asynccontextmanager
    async def get_async_connection(self) -> AsyncIterator[Any]:
        """Yield an async connection from the pool (context-managed).

        Usage::

            async with manager.get_async_connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
        """
        await self._ensure_async_pool()
        async with self._async_pool.connection() as conn:
            yield conn

    async def close_async(self) -> None:
        """Shut down the async pool, releasing all connections.

        Idempotent — calling on a never-opened or already-closed manager
        is a no-op.  Sync pool (if any) is left untouched; close it
        separately via :meth:`close`.
        """
        if self._async_pool is not None:
            await self._async_pool.close()
            self._async_pool = None
            logger.info("postgres.async_pool_closed")

    def get_async_pool_stats(self) -> dict[str, Any]:
        """Return current async pool statistics, parallel to :meth:`get_pool_stats`.

        Returns the same keys as the sync version; ``pool_stats_available``
        flips to ``True`` once the async pool has been opened and live
        stats can be read.  Safe to call from sync code: it does not touch
        the event loop, only inspects in-memory pool state.
        """
        base: dict[str, Any] = {
            "pool_min": self._min_size,
            "pool_max": self._max_size,
            "pool_size": 0,
            "pool_available": 0,
            "pool_saturation": 0.0,
            "idle_timeout": self._idle_timeout,
            "max_waiting": self._max_waiting,
            "max_lifetime": self._max_lifetime,
            "pool_stats_available": False,
        }
        if self._async_pool is None:
            return base
        try:
            raw = self._async_pool.get_stats()
            size = int(raw.get("pool_size", 0))
            available = int(raw.get("pool_available", 0))
            saturation = (size - available) / self._max_size if self._max_size > 0 else 0.0
            base.update(
                {
                    "pool_size": size,
                    "pool_available": available,
                    "pool_saturation": round(max(0.0, min(1.0, saturation)), 4),
                    "pool_stats_available": True,
                }
            )
        except Exception as exc:
            logger.debug(
                "postgres_connection.async_pool_stats_unavailable",
                error=type(exc).__name__,
                detail=str(exc),
            )
        return base

    @contextmanager
    def namespace_context(self, namespace: str) -> Iterator[Any]:
        """Yield a connection with ``tapps.current_namespace`` session variable set.

        Documented pattern for enforcing namespace-based Row Level
        Security (RLS) on ``hive_memories`` (EPIC-063 STORY-063.3).  All
        transactions executed against the yielded connection see the
        ``hive_namespace_isolation`` policy applied with the bound
        namespace.

        TAP-514 — uses session-level ``SET`` (not ``SET LOCAL``) so the
        binding survives multiple transactions inside one borrow; the
        pool's ``reset`` callback wipes it on connection return so no
        identity leaks across borrows.

        Parameters
        ----------
        namespace:
            The namespace value to bind for this borrow.  Must not be
            empty; pass ``""`` only to explicitly invoke the admin-bypass
            policy (all rows visible, no isolation).
        """
        self._ensure_pool()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                from psycopg import sql as pgsql

                cur.execute(
                    pgsql.SQL("SET tapps.current_namespace = {}").format(pgsql.Literal(namespace))
                )
            yield conn

    @contextmanager
    def project_context(self, project_id: str) -> Iterator[Any]:
        """Yield a connection with ``app.project_id`` session variable set.

        EPIC-069 STORY-069.8 — enforces tenant Row Level Security on
        ``private_memories`` and ``project_profiles`` (migration
        ``private/009_project_rls.sql``).  All transactions executed
        against the yielded connection see RLS restricted to rows whose
        ``project_id`` column matches *project_id*; cross-tenant INSERTs
        fail the WITH CHECK clause.

        TAP-514 — uses session-level ``SET`` (not ``SET LOCAL``) so the
        binding survives multiple transactions inside one borrow.  Earlier
        ``SET LOCAL`` semantics let a caller commit mid-block and silently
        lose the RLS context for the next transaction on the same
        connection — fail-closed policies then hid every row, looking like
        an empty tenant.  The pool's ``reset`` callback wipes
        ``app.project_id`` on connection return so no identity leaks
        across borrows.

        Parameters
        ----------
        project_id:
            The tenant identity to bind for this borrow.  Must be a
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

                cur.execute(pgsql.SQL("SET app.project_id = {}").format(pgsql.Literal(project_id)))
            yield conn

    @contextmanager
    def agent_context(self, agent_id: str) -> Iterator[Any]:
        """Yield a connection with ``app.agent_id`` session variable set.

        STORY-070.7 — threads the per-call ``agent_id`` into Postgres so
        any row-level security policy (or audit trigger) that filters by
        agent can see the caller identity.

        TAP-514 — uses session-level ``SET`` so the binding survives
        multiple transactions inside one borrow; the pool's ``reset``
        callback wipes it on connection return so no agent identity leaks
        across borrows.

        Parameters
        ----------
        agent_id:
            The agent identity to bind for this borrow.  Must be a
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

                cur.execute(pgsql.SQL("SET app.agent_id = {}").format(pgsql.Literal(agent_id)))
            yield conn

    @contextmanager
    def admin_context(self) -> Iterator[Any]:
        """Yield a connection with ``app.is_admin = 'true'`` set.

        EPIC-069 STORY-069.8 — unlocks the admin-bypass policy on
        ``project_profiles`` so the registry (list_all / register /
        approve / delete) can see and mutate every row regardless of
        tenant.

        TAP-514 — uses session-level ``SET`` so the flag survives
        multiple transactions inside one borrow; the pool's ``reset``
        callback wipes ``app.is_admin`` on connection return so the
        elevation cannot leak across borrows.  This context does NOT
        unlock ``private_memories`` — that table is fail-closed and has
        no admin policy; genuine admin maintenance against
        ``private_memories`` must connect as a role with BYPASSRLS or
        temporarily DISABLE the table's RLS (TAP-512 added FORCE so
        owner-bypass no longer works).
        """
        self._ensure_pool()
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SET app.is_admin = 'true'")
            yield conn

    @property
    def dsn(self) -> str:
        """Return the DSN this manager was created with."""
        return self._dsn

    @property
    def is_open(self) -> bool:
        """Return whether the sync pool has been created and not yet closed."""
        return self._pool is not None

    @property
    def is_async_open(self) -> bool:
        """Return whether the async pool has been opened and not yet closed."""
        return self._async_pool is not None

    # -- Async tenant-scoped contexts (STORY-072.2 — RLS parity) -------------

    @asynccontextmanager
    async def async_project_context(self, project_id: str) -> AsyncIterator[Any]:
        """Async parity for :meth:`project_context`.

        Yields an async connection with ``app.project_id`` set so RLS on
        ``private_memories`` / ``project_profiles`` restricts every read
        and write to *project_id*.  Uses session-level ``SET`` (not ``SET
        LOCAL``) so the binding survives multiple transactions inside one
        borrow; the pool's async reset callback wipes it on connection
        return so no identity leaks across borrows.
        """
        if not project_id or not project_id.strip():
            raise ValueError(
                "async_project_context requires a non-empty project_id; "
                "use async_admin_context() for registry / admin paths."
            )
        await self._ensure_async_pool()
        async with self._async_pool.connection() as conn:
            async with conn.cursor() as cur:
                from psycopg import sql as pgsql

                await cur.execute(
                    pgsql.SQL("SET app.project_id = {}").format(pgsql.Literal(project_id))
                )
            yield conn

    @asynccontextmanager
    async def async_admin_context(self) -> AsyncIterator[Any]:
        """Async parity for :meth:`admin_context`."""
        await self._ensure_async_pool()
        async with self._async_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SET app.is_admin = 'true'")
            yield conn
