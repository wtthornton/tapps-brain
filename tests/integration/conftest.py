"""Shared fixtures for the Postgres integration test suite.

Solves the connection-pool exhaustion problem described in TAP-362:
several integration test fixtures construct ``PostgresConnectionManager``
instances and never close them, so connections leak between tests.  When
later tests (e.g. ``test_tenant_isolation.py`` or ``test_rls_spike.py``)
try to acquire a connection, the pool is saturated by zombie connections
from earlier modules and the test fails with::

    psycopg_pool.PoolTimeout: couldn't get a connection after 5.00 sec

This conftest provides two layers of defence:

1.  Sane defaults for the pool size so each test pool stays small.
2.  An autouse, function-scoped fixture that wraps
    :class:`PostgresConnectionManager` to track every instance created
    during a test and force-closes any that are still open at teardown.

Individual test files SHOULD still close their pools explicitly — this
conftest is the safety net, not the primary mechanism.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


# ---------------------------------------------------------------------------
# Pool-size defaults for the integration test environment.
#
# The library's default pool is min=2 / max=10 — fine for production but
# wasteful in a test suite where each test creates one or more managers
# against a single local Postgres.  Cap defaults at min=1 / max=5 unless
# the operator has overridden them in the environment.
# ---------------------------------------------------------------------------


def _set_default_env(name: str, value: str) -> None:
    if name not in os.environ:
        os.environ[name] = value


_set_default_env("TAPPS_BRAIN_PG_POOL_MIN", "1")
_set_default_env("TAPPS_BRAIN_PG_POOL_MAX", "5")


# ---------------------------------------------------------------------------
# Connection-pool tracking
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _track_and_close_postgres_pools(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Track every PostgresConnectionManager and force-close on teardown.

    Wraps ``PostgresConnectionManager.__init__`` so each instance is
    appended to a per-test list.  After the test runs, every tracked
    manager is closed unconditionally — making it impossible for a test
    or fixture to leak an open pool to subsequent tests.

    Tests that close their managers explicitly are unaffected:
    :meth:`PostgresConnectionManager.close` is idempotent (it sets
    ``self._pool = None``).
    """
    # Lazy import — the integration tests already import this module, but
    # we keep the import inside the fixture so the conftest itself does
    # not require psycopg at collection time.
    try:
        from tapps_brain.postgres_connection import PostgresConnectionManager
    except Exception:
        # If the library cannot be imported, skip tracking; the tests
        # themselves will skip via their own guards.
        yield
        return

    tracked: list[PostgresConnectionManager] = []
    original_init = PostgresConnectionManager.__init__

    def _wrapped_init(self: PostgresConnectionManager, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        tracked.append(self)

    monkeypatch.setattr(PostgresConnectionManager, "__init__", _wrapped_init)
    try:
        yield
    finally:
        for cm in tracked:
            try:
                cm.close()
            except Exception:
                # Best-effort cleanup — never let teardown raise.
                pass
        tracked.clear()
