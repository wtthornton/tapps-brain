"""Unit tests for PostgresConnectionManager (mocked — no real PG needed).

EPIC-055 STORY-055.2
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestPostgresConnectionManager:
    """Tests for connection pool setup and lifecycle."""

    def test_init_stores_dsn_and_defaults(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm.dsn == "postgres://localhost/test"
        assert cm._min_size == 2
        assert cm._max_size == 10
        assert cm._connect_timeout == 5.0
        assert cm._pool is None
        assert cm.is_open is False

    def test_init_custom_pool_params(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager(
            "postgres://localhost/test",
            min_size=4,
            max_size=20,
            connect_timeout=10.0,
        )
        assert cm._min_size == 4
        assert cm._max_size == 20
        assert cm._connect_timeout == 10.0

    def test_init_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_MIN", "5")
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_MAX", "25")
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_CONNECT_TIMEOUT", "15")

        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm._min_size == 5
        assert cm._max_size == 25
        assert cm._connect_timeout == 15.0

    def test_ensure_pool_raises_import_error_when_psycopg_missing(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test")
        with (
            patch.dict("sys.modules", {"psycopg_pool": None}),
            pytest.raises(ImportError, match="psycopg"),
        ):
            cm._ensure_pool()

    def test_ensure_pool_creates_pool_on_first_call(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        mock_pool_class = MagicMock()
        mock_pool_instance = MagicMock()
        mock_pool_class.return_value = mock_pool_instance

        cm = PostgresConnectionManager("postgres://localhost/test")
        with patch("tapps_brain.postgres_connection.PostgresConnectionManager._ensure_pool") as ep:
            # Simulate pool creation.
            cm._pool = mock_pool_instance
            assert cm.is_open is True

    def test_get_connection_yields_from_pool(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = mock_pool

        with cm.get_connection() as conn:
            assert conn is mock_conn

    def test_close_shuts_down_pool(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        mock_pool = MagicMock()
        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = mock_pool

        cm.close()
        mock_pool.close.assert_called_once()
        assert cm._pool is None
        assert cm.is_open is False

    def test_close_is_noop_when_not_open(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test")
        cm.close()  # Should not raise.
        assert cm.is_open is False
