"""Fixtures for the compat test suite.

All non-Postgres compat tests (anything without ``requires_postgres``) must
run against the embedded SQLite backend regardless of what env vars are set in
CI.  This autouse fixture ensures that isolation.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_embedded_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clear TAPPS_BRAIN_DATABASE_URL for tests that don't need Postgres.

    TestPostgresParity tests are marked ``requires_postgres``; they use the DB
    URL as-is.  All other compat tests are pinning *embedded* (SQLite) behavior
    and must not pick up the live-Postgres DSN from the CI environment.
    """
    if "requires_postgres" not in request.keywords:
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
