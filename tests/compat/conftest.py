"""Fixtures for the compat test suite.

ADR-007 (2026-04-11) removed SQLite.  The only backend is now Postgres.

Tests that exercise ``AgentBrain`` (remember/recall/forget) are marked
``requires_postgres`` and run only when ``TAPPS_BRAIN_DATABASE_URL`` is set.
Tests that only check class hierarchy or static invariants run in every CI job
without a live DB.

The ``_force_embedded_backend`` fixture below preserves the original isolation
contract: tests **without** ``requires_postgres`` have both DSN env vars
cleared so they can never accidentally reach a live database.  After ADR-007,
the only tests in this category are the static ``issubclass`` checks in
``TestErrorTypes`` — they don't need a backend at all.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _force_embedded_backend(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clear DSN env vars for tests that don't declare ``requires_postgres``.

    Tests marked ``requires_postgres`` use the live Postgres URL as-is.
    All other tests in this suite are static assertions (class hierarchy,
    constant checks) that must not reach a live database.
    """
    if "requires_postgres" not in request.keywords:
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
