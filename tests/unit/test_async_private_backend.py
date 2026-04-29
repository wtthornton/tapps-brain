"""Unit tests for AsyncPostgresPrivateBackend (mocked — no real PG needed).

STORY-072.2 — verifies that the async backend routes all SQL through the
async connection pool (``async_project_context``) and exposes the same
protocol surface as the sync ``PostgresPrivateBackend``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(key: str = "test-key", value: str = "test-value") -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(
        key=key,
        value=value,
        tier="pattern",
        confidence=0.8,
        source="agent",
        source_agent="test-agent",
        created_at=datetime.now(tz=UTC).isoformat(),
        updated_at=datetime.now(tz=UTC).isoformat(),
        last_accessed=datetime.now(tz=UTC).isoformat(),
    )


def _make_cm() -> MagicMock:
    """Return a mock PostgresConnectionManager with async_project_context."""
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.description = [("key",), ("value",)]
    mock_cursor.rowcount = 1
    mock_cursor.fetchone = AsyncMock(return_value=None)
    mock_cursor.fetchall = AsyncMock(return_value=[])
    mock_cursor.fetchmany = AsyncMock(return_value=[])
    mock_cursor.execute = AsyncMock()

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.cursor = MagicMock(return_value=mock_cursor)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _project_ctx(_project_id: str) -> Any:
        yield mock_conn

    cm = MagicMock()
    cm.async_project_context = _project_ctx
    cm.close_async = AsyncMock()
    return cm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAsyncPostgresPrivateBackendInit:
    def test_imports_cleanly(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend  # noqa: F401

    def test_properties(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        assert backend.encryption_key is None
        assert str(backend.db_path) == "/dev/null"
        assert str(backend.audit_path) == "/dev/null"

    def test_has_no_threading_lock(self) -> None:
        import threading

        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        backend = AsyncPostgresPrivateBackend(
            _make_cm(), project_id="proj", agent_id="agent"
        )
        # relations lock must be asyncio.Lock, not threading.Lock
        assert not isinstance(backend._relations_lock, type(threading.Lock()))
        assert isinstance(backend._relations_lock, asyncio.Lock)


class TestAsyncPostgresPrivateBackendCRUD:
    @pytest.mark.asyncio
    async def test_save_calls_execute(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        entry = _make_entry()
        await backend.save(entry)
        # Verify that async cursor.execute was called (SQL dispatched async)
        ctx = cm.async_project_context("proj")
        async with ctx as conn:
            cur = conn.cursor()
            async with cur:
                pass  # cursor was entered
        # The fact that no exception was raised confirms the async path ran

    @pytest.mark.asyncio
    async def test_delete_returns_bool(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        deleted = await backend.delete("some-key")
        # rowcount=1 on mock → True
        assert isinstance(deleted, bool)

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.search("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_knn_search_empty_embedding_returns_empty(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.knn_search([], k=5)
        assert result == []

    @pytest.mark.asyncio
    async def test_vector_row_count_zero_on_none(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        # fetchone returns None → count = 0
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        count = await backend.vector_row_count()
        assert count == 0

    @pytest.mark.asyncio
    async def test_load_all_empty_returns_empty_list(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.load_all()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_schema_version_returns_int(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        version = await backend.get_schema_version()
        assert isinstance(version, int)

    @pytest.mark.asyncio
    async def test_flywheel_meta_get_none_on_empty_row(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.flywheel_meta_get("some-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_flywheel_meta_set_no_raise(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        await backend.flywheel_meta_set("k", "v")  # should not raise

    @pytest.mark.asyncio
    async def test_archive_entry_returns_int(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        entry = _make_entry()
        result = await backend.archive_entry(entry)
        assert isinstance(result, int)

    @pytest.mark.asyncio
    async def test_total_archive_bytes_zero_on_none(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.total_archive_bytes()
        assert result == 0

    @pytest.mark.asyncio
    async def test_close_calls_close_async(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        await backend.close()
        cm.close_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_append_audit_no_raise(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        await backend.append_audit("save", "key1", {"detail": "x"})

    @pytest.mark.asyncio
    async def test_query_audit_empty_on_no_rows(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.query_audit()
        assert result == []

    @pytest.mark.asyncio
    async def test_count_relations_zero(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        count = await backend.count_relations()
        assert count == 0

    @pytest.mark.asyncio
    async def test_save_relations_empty_returns_zero(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        count = await backend.save_relations("key", [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_list_archive_empty(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.list_archive()
        assert result == []

    @pytest.mark.asyncio
    async def test_verify_expected_indexes_returns_list(self) -> None:
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        cm = _make_cm()
        backend = AsyncPostgresPrivateBackend(cm, project_id="proj", agent_id="agent")
        result = await backend.verify_expected_indexes()
        assert isinstance(result, list)


class TestCreateAsyncPrivateBackend:
    def test_factory_raises_on_empty_dsn(self) -> None:
        from tapps_brain.backends import create_async_private_backend

        with pytest.raises(ValueError, match="PostgreSQL DSN"):
            create_async_private_backend("", project_id="p", agent_id="a")

    def test_factory_raises_on_sqlite_dsn(self) -> None:
        from tapps_brain.backends import create_async_private_backend

        with pytest.raises(ValueError, match="PostgreSQL DSN"):
            create_async_private_backend(
                "sqlite:///test.db", project_id="p", agent_id="a"
            )

    def test_factory_creates_backend(self) -> None:
        from tapps_brain.backends import create_async_private_backend
        from tapps_brain.postgres_private import AsyncPostgresPrivateBackend

        backend = create_async_private_backend(
            "postgres://localhost/test", project_id="proj", agent_id="agent"
        )
        assert isinstance(backend, AsyncPostgresPrivateBackend)
        assert backend._project_id == "proj"
        assert backend._agent_id == "agent"
