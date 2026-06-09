"""EPIC-079 — async Postgres backend propagates DB failures on read paths."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_async_postgres_private import _make_async_cursor, _make_manager


class TestAsyncBackendErrorPropagation:
    def test_knn_search_raises_on_db_failure(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        cm = _make_manager(cur)
        backend = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        with pytest.raises(RuntimeError, match="connection refused"):
            asyncio.run(backend.knn_search([0.1, 0.2], k=3))

    def test_query_audit_raises_on_db_failure(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        cm = _make_manager(cur)
        backend = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        with pytest.raises(RuntimeError, match="connection refused"):
            asyncio.run(backend.query_audit(limit=10))

    def test_flywheel_meta_get_raises_on_db_failure(self) -> None:
        from tapps_brain.async_postgres_private import AsyncPostgresPrivateBackend

        cur = _make_async_cursor()
        cur.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        cm = _make_manager(cur)
        backend = AsyncPostgresPrivateBackend(cm, project_id="p", agent_id="a")
        with pytest.raises(RuntimeError, match="connection refused"):
            asyncio.run(backend.flywheel_meta_get("checkpoint"))
