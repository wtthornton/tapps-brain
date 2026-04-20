"""Idempotency key store for HTTP write operations (EPIC-070 STORY-070.5).

Feature-gated by ``TAPPS_BRAIN_IDEMPOTENCY=1``.

When enabled, ``POST /v1/remember`` and ``POST /v1/reinforce`` accept an
``X-Idempotency-Key`` header (UUID).  A duplicate key within 24 hours returns
the original response body and status code instead of re-processing the write.

The MCP equivalent uses ``params._meta.idempotency_key`` with identical
semantics.

Usage::

    store = IdempotencyStore(dsn)
    cached = store.check(project_id, ikey)
    if cached is not None:
        status, body = cached
        return replay_response(status, body)
    result = do_write_operation(...)
    store.save(project_id, ikey, 200, result)
    return result

The underlying Postgres table ``idempotency_keys`` is created by migration
``010_idempotency_keys.sql``.  :class:`IdempotencyStore` degrades gracefully
(logs a warning, treats every check as a cache miss) when the table does not
exist yet.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

#: Default TTL for idempotency keys (hours).
IDEMPOTENCY_TTL_HOURS: int = 24

#: Maximum stored response body size (bytes).
_MAX_RESPONSE_BYTES: int = 65_536  # 64 KiB

_IDEMPOTENCY_ENV = "TAPPS_BRAIN_IDEMPOTENCY"


def is_idempotency_enabled() -> bool:
    """Return ``True`` when the ``TAPPS_BRAIN_IDEMPOTENCY`` env var equals ``"1"``."""
    return os.environ.get(_IDEMPOTENCY_ENV, "").strip() == "1"


class IdempotencyStore:
    """Postgres-backed idempotency key store.

    Requires migration ``010_idempotency_keys.sql`` to be applied against the
    private schema.  When the table is absent, :meth:`check` returns ``None``
    and :meth:`save` is a no-op (with a logged warning).

    Parameters
    ----------
    dsn:
        PostgreSQL DSN (``postgres://`` or ``postgresql://``).
    ttl_hours:
        Lifetime of a stored key.  Keys older than this are ignored on
        :meth:`check` and deleted by :meth:`sweep_expired`.
    """

    def __init__(self, dsn: str, *, ttl_hours: int = IDEMPOTENCY_TTL_HOURS) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        self._cm = PostgresConnectionManager(dsn, min_size=1, max_size=3)
        self.ttl_hours = ttl_hours

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, project_id: str, key: str) -> tuple[int, dict[str, Any]] | None:
        """Return ``(status_code, response_body)`` for an existing key, or ``None``.

        The key must have been stored within :attr:`ttl_hours`.  Returns
        ``None`` on any Postgres error so that the caller can fall through to
        the real write path.
        """
        try:
            with self._cm.get_connection() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                        SELECT response_status, response_json
                          FROM idempotency_keys
                         WHERE key = %s
                           AND project_id = %s
                           AND created_at > now() - make_interval(hours => %s)
                         LIMIT 1
                        """,
                    (key, project_id, self.ttl_hours),
                )
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001 — psycopg+connection errors are heterogeneous; idempotency check failure treated as miss
            logger.warning(
                "idempotency.check_failed",
                key=key,
                project_id=project_id,
                error=str(exc),
            )
            return None

        if row is None:
            return None
        status, body_json = row
        try:
            return int(status), json.loads(body_json)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "idempotency.decode_failed",
                key=key,
                project_id=project_id,
                error=str(exc),
            )
            return None

    def save(
        self,
        project_id: str,
        key: str,
        status: int,
        body: dict[str, Any],
    ) -> None:
        """Persist an idempotency key → response mapping.

        Uses ``ON CONFLICT DO NOTHING`` so concurrent requests with the same
        key only store the first response.  Silently skips responses larger
        than :data:`_MAX_RESPONSE_BYTES`.
        """
        body_json = json.dumps(body, ensure_ascii=False)
        if len(body_json.encode()) > _MAX_RESPONSE_BYTES:
            logger.warning(
                "idempotency.response_too_large",
                key=key,
                project_id=project_id,
                size=len(body_json.encode()),
            )
            return
        try:
            with self._cm.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO idempotency_keys
                               (key, project_id, response_status, response_json)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (key, project_id) DO NOTHING
                        """,
                        (key, project_id, status, body_json),
                    )
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — psycopg+connection errors are heterogeneous; idempotency save failure logged and retried by caller
            logger.warning(
                "idempotency.save_failed",
                key=key,
                project_id=project_id,
                error=str(exc),
            )

    def sweep_expired(self, ttl_hours: int | None = None) -> int:
        """Delete keys older than *ttl_hours* and return the row count.

        When *ttl_hours* is ``None``, :attr:`ttl_hours` is used.
        """
        hours = ttl_hours if ttl_hours is not None else self.ttl_hours
        try:
            with self._cm.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM idempotency_keys"
                        " WHERE created_at < now() - make_interval(hours => %s)",
                        (hours,),
                    )
                    deleted: int = cur.rowcount or 0
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — psycopg+connection errors are heterogeneous; sweep failure returns 0 deleted
            logger.warning("idempotency.sweep_failed", error=str(exc))
            return 0
        logger.debug("idempotency.sweep_complete", deleted=deleted, ttl_hours=hours)
        return deleted

    def close(self) -> None:
        """Shut down the underlying connection pool."""
        self._cm.close()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> IdempotencyStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def sweep_expired_keys(
    dsn: str | None = None,
    ttl_hours: int = IDEMPOTENCY_TTL_HOURS,
) -> int:
    """Convenience function: sweep expired keys and return rows deleted.

    When *dsn* is ``None``, the function reads ``TAPPS_BRAIN_DATABASE_URL``
    from the environment.  Returns ``0`` and logs a warning when no DSN is
    available.
    """
    resolved_dsn = dsn or os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip()
    if not resolved_dsn:
        logger.debug("idempotency.sweep_skipped", reason="no DSN configured")
        return 0
    with IdempotencyStore(resolved_dsn, ttl_hours=ttl_hours) as store:
        return store.sweep_expired()


__all__ = [
    "IDEMPOTENCY_TTL_HOURS",
    "IdempotencyStore",
    "is_idempotency_enabled",
    "sweep_expired_keys",
]
