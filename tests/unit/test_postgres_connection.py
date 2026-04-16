"""Unit tests for PostgresConnectionManager (mocked — no real PG needed).

EPIC-055 STORY-055.2
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestPostgresConnectionManager:
    """Tests for connection pool setup and lifecycle."""

    def test_init_stores_dsn_and_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Integration conftest sets TAPPS_BRAIN_PG_POOL_MIN/MAX globally;
        # clear them so this unit test sees library defaults.
        for _v in (
            "TAPPS_BRAIN_PG_POOL_MIN",
            "TAPPS_BRAIN_PG_POOL_MAX",
            "TAPPS_BRAIN_HIVE_POOL_MIN",
            "TAPPS_BRAIN_HIVE_POOL_MAX",
        ):
            monkeypatch.delenv(_v, raising=False)

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

        # Clear canonical PG_POOL vars so legacy HIVE_POOL fallbacks take effect.
        monkeypatch.delenv("TAPPS_BRAIN_PG_POOL_MIN", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_PG_POOL_MAX", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_PG_POOL_CONNECT_TIMEOUT_SECONDS", raising=False)
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

    # -- idle_timeout ----------------------------------------------------------

    def test_init_idle_timeout_default(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm._idle_timeout == 300.0

    def test_init_idle_timeout_explicit(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test", idle_timeout=600.0)
        assert cm._idle_timeout == 600.0

    def test_init_idle_timeout_zero_disables_eviction(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test", idle_timeout=0)
        assert cm._idle_timeout == 0

    def test_init_idle_timeout_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_IDLE_TIMEOUT", "120")
        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm._idle_timeout == 120.0

    # -- get_pool_stats --------------------------------------------------------

    def test_get_pool_stats_before_pool_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for _v in (
            "TAPPS_BRAIN_PG_POOL_MIN",
            "TAPPS_BRAIN_PG_POOL_MAX",
            "TAPPS_BRAIN_HIVE_POOL_MIN",
            "TAPPS_BRAIN_HIVE_POOL_MAX",
        ):
            monkeypatch.delenv(_v, raising=False)

        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test")
        stats = cm.get_pool_stats()
        assert stats["pool_min"] == 2
        assert stats["pool_max"] == 10
        assert stats["pool_size"] == 0
        assert stats["pool_available"] == 0
        assert stats["pool_saturation"] == 0.0
        assert stats["idle_timeout"] == 300.0

    def test_get_pool_stats_with_mock_pool(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        mock_pool = MagicMock()
        mock_pool.get_stats.return_value = {"pool_size": 6, "pool_available": 4}

        cm = PostgresConnectionManager(
            "postgres://localhost/test",
            min_size=2,
            max_size=10,
        )
        cm._pool = mock_pool

        stats = cm.get_pool_stats()
        assert stats["pool_size"] == 6
        assert stats["pool_available"] == 4
        # (6 - 4) / 10 = 0.2
        assert stats["pool_saturation"] == pytest.approx(0.2, abs=1e-4)

    def test_get_pool_stats_saturation_clamped_to_one(self) -> None:
        """Pool stats should never report saturation > 1.0 even with bad data."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        mock_pool = MagicMock()
        # pool_size > max_size can theoretically happen with burst; clamp to 1.
        mock_pool.get_stats.return_value = {"pool_size": 15, "pool_available": 0}

        cm = PostgresConnectionManager("postgres://localhost/test", max_size=10)
        cm._pool = mock_pool

        stats = cm.get_pool_stats()
        assert stats["pool_saturation"] <= 1.0

    def test_get_pool_stats_returns_zero_saturation_when_get_stats_raises(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        mock_pool = MagicMock()
        mock_pool.get_stats.side_effect = RuntimeError("pool gone")

        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = mock_pool

        # Should not raise; falls back to zero values.
        stats = cm.get_pool_stats()
        assert stats["pool_saturation"] == 0.0
        assert stats["pool_size"] == 0

    # -- New canonical env vars (TAPPS_BRAIN_PG_POOL_*) -----------------------

    def test_init_reads_new_pg_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TAPPS_BRAIN_PG_POOL_MAX/MIN/CONNECT_TIMEOUT_SECONDS are honoured."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.setenv("TAPPS_BRAIN_PG_POOL_MIN", "3")
        monkeypatch.setenv("TAPPS_BRAIN_PG_POOL_MAX", "15")
        monkeypatch.setenv("TAPPS_BRAIN_PG_POOL_CONNECT_TIMEOUT_SECONDS", "20")

        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm._min_size == 3
        assert cm._max_size == 15
        assert cm._connect_timeout == 20.0

    def test_pg_env_vars_take_precedence_over_hive_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """New TAPPS_BRAIN_PG_POOL_* vars override legacy TAPPS_BRAIN_HIVE_* vars."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.setenv("TAPPS_BRAIN_PG_POOL_MIN", "4")
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_MIN", "99")  # should be ignored
        monkeypatch.setenv("TAPPS_BRAIN_PG_POOL_MAX", "20")
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_MAX", "99")  # should be ignored

        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm._min_size == 4
        assert cm._max_size == 20

    def test_legacy_hive_env_vars_still_work(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Legacy TAPPS_BRAIN_HIVE_* vars remain functional as a fallback."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.delenv("TAPPS_BRAIN_PG_POOL_MIN", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_PG_POOL_MAX", raising=False)
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_MIN", "5")
        monkeypatch.setenv("TAPPS_BRAIN_HIVE_POOL_MAX", "25")

        cm = PostgresConnectionManager("postgres://localhost/test")
        assert cm._min_size == 5
        assert cm._max_size == 25

    # -- DSN validation -------------------------------------------------------

    def test_malformed_dsn_raises_value_error(self) -> None:
        """Non-postgres scheme raises ValueError with ADR-007 reference."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        with pytest.raises(ValueError, match="ADR-007"):
            PostgresConnectionManager("sqlite:///test.db")

    def test_dsn_without_scheme_raises_value_error(self) -> None:
        """DSN without scheme raises ValueError."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        with pytest.raises(ValueError, match="postgres://"):
            PostgresConnectionManager("localhost/mydb")

    def test_empty_dsn_raises_value_error(self) -> None:
        """Empty DSN raises ValueError."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        with pytest.raises(ValueError):
            PostgresConnectionManager("")

    def test_postgresql_scheme_is_accepted(self) -> None:
        """Both 'postgres://' and 'postgresql://' schemes are valid."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgresql://localhost/test")
        assert cm.dsn == "postgresql://localhost/test"

    # -- Pool size constraints ------------------------------------------------

    def test_pool_max_less_than_one_raises(self) -> None:
        """max_size < 1 raises ValueError."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        with pytest.raises(ValueError, match="max_size"):
            PostgresConnectionManager("postgres://localhost/test", max_size=0)

    def test_pool_min_greater_than_max_raises(self) -> None:
        """min_size > max_size raises ValueError."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        with pytest.raises(ValueError, match="min_size"):
            PostgresConnectionManager("postgres://localhost/test", min_size=10, max_size=5)


# ---------------------------------------------------------------------------
# TAP-512 — non-owner role startup assertion.
# ---------------------------------------------------------------------------


def _mock_pool_with_role(
    *,
    rolsuper: bool = False,
    rolbypassrls: bool = False,
    owned_tables: list[str] | None = None,
    current_user: str = "tapps_runtime",
) -> MagicMock:
    """Build a mock pool whose connection.cursor() returns canned role rows."""
    cur = MagicMock()
    cur.fetchone.return_value = (current_user, rolsuper, rolbypassrls)
    cur.fetchall.return_value = [(t,) for t in (owned_tables or [])]
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cur)

    pool_ctx = MagicMock()
    pool_ctx.__enter__ = MagicMock(return_value=conn)
    pool_ctx.__exit__ = MagicMock(return_value=False)

    pool = MagicMock()
    pool.connection = MagicMock(return_value=pool_ctx)
    return pool


class TestNonPrivilegedRoleAssertion:
    """TAP-512: refuse to start when the connected role can bypass RLS."""

    def test_non_privileged_role_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.delenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", raising=False)
        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = _mock_pool_with_role()

        # Should not raise.
        cm._assert_non_privileged_role()

    def test_superuser_role_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.delenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", raising=False)
        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = _mock_pool_with_role(rolsuper=True, current_user="postgres")

        with pytest.raises(RuntimeError, match="rolsuper=true"):
            cm._assert_non_privileged_role()

    def test_bypassrls_role_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.delenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", raising=False)
        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = _mock_pool_with_role(rolbypassrls=True)

        with pytest.raises(RuntimeError, match="rolbypassrls=true"):
            cm._assert_non_privileged_role()

    def test_table_owner_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.delenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", raising=False)
        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = _mock_pool_with_role(
            owned_tables=["private_memories", "project_profiles"],
            current_user="tapps_migrator",
        )

        with pytest.raises(RuntimeError, match="owns tenanted tables"):
            cm._assert_non_privileged_role()

    def test_override_env_downgrades_violation_to_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.setenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", "1")
        cm = PostgresConnectionManager("postgres://localhost/test")
        cm._pool = _mock_pool_with_role(rolsuper=True, current_user="postgres")

        # Should not raise even though role is privileged.
        cm._assert_non_privileged_role()

    def test_failed_assertion_closes_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pool created in _ensure_pool is closed when assertion raises."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        monkeypatch.delenv("TAPPS_BRAIN_ALLOW_PRIVILEGED_ROLE", raising=False)
        bad_pool = _mock_pool_with_role(rolsuper=True, current_user="postgres")

        cm = PostgresConnectionManager("postgres://localhost/test")
        with patch("psycopg_pool.ConnectionPool", return_value=bad_pool):
            with pytest.raises(RuntimeError, match="rolsuper"):
                cm._ensure_pool()
        bad_pool.close.assert_called_once()
        assert cm._pool is None


# ---------------------------------------------------------------------------
# TAP-514 — pool reset callback wipes tapps session variables on release.
# ---------------------------------------------------------------------------


class TestSessionVarResetCallback:
    """The reset callback runs on connection return and clears every tapps
    session variable so identity cannot leak across pool borrows."""

    def test_reset_runs_all_resets_in_one_execute(self) -> None:
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.cursor = MagicMock(return_value=cur)

        PostgresConnectionManager._reset_session_vars(conn)

        cur.execute.assert_called_once()
        sql_text = cur.execute.call_args[0][0]
        for var in ("app.project_id", "app.agent_id", "app.is_admin", "tapps.current_namespace"):
            assert f"RESET {var}" in sql_text

    def test_reset_callback_wired_into_pool(self) -> None:
        """ConnectionPool is constructed with reset=cm._reset_session_vars."""
        from tapps_brain.postgres_connection import PostgresConnectionManager

        cm = PostgresConnectionManager("postgres://localhost/test")
        captured: dict = {}

        def fake_pool_ctor(dsn: str, **kwargs: object) -> MagicMock:
            captured.update(kwargs)
            captured["dsn"] = dsn
            # Return a pool whose role-check passes (TAP-512 added the
            # assertion in _ensure_pool — give it a clean tapps_runtime
            # row so we can isolate the reset-callback wiring assertion).
            return _mock_pool_with_role()

        with patch("psycopg_pool.ConnectionPool", side_effect=fake_pool_ctor):
            cm._ensure_pool()

        assert captured["reset"] is cm._reset_session_vars
