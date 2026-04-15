"""Unit tests for :mod:`tapps_brain.project_registry` (EPIC-069 STORY-069.2).

Uses a mocked :class:`PostgresConnectionManager` — the goal is to verify
SQL shape, parameter binding, and the lax/strict resolution branches,
not to exercise a live database (that is the job of the integration
suite once it lands).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def _make_registry() -> tuple:
    from tapps_brain.project_registry import ProjectRegistry

    mock_cm = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cm.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
    # EPIC-069 STORY-069.8: registry now runs under admin_context so it can
    # bypass the per-tenant RLS policy on project_profiles.
    mock_cm.admin_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return ProjectRegistry(mock_cm), mock_cursor, mock_conn


def _repo_brain_row(project_id: str = "alpaca", approved: bool = True) -> tuple:
    """Fabricate a SELECT row shape matching the 008 migration."""
    from tapps_brain.profile import get_builtin_profile

    profile = get_builtin_profile("repo-brain")
    return (
        project_id,
        profile.model_dump(mode="json"),
        approved,
        "admin",
        "",
    )


class TestGet:
    def test_returns_none_when_missing(self) -> None:
        reg, cur, _ = _make_registry()
        cur.fetchone.return_value = None
        assert reg.get("unknown") is None
        cur.execute.assert_called_once()
        sql = cur.execute.call_args[0][0]
        assert "project_profiles" in sql
        assert cur.execute.call_args[0][1] == ("unknown",)

    def test_returns_record_when_present(self) -> None:
        reg, cur, _ = _make_registry()
        cur.fetchone.return_value = _repo_brain_row("alpaca")
        record = reg.get("alpaca")
        assert record is not None
        assert record.project_id == "alpaca"
        assert record.approved is True
        assert record.source == "admin"
        assert record.profile.name == "repo-brain"

    def test_handles_jsonb_as_dict_or_string(self) -> None:
        reg, cur, _ = _make_registry()
        pid, profile_json, *rest = _repo_brain_row()
        # Simulate psycopg returning JSON-as-string (some driver configs).
        cur.fetchone.return_value = (pid, json.dumps(profile_json), *rest)
        record = reg.get("alpaca")
        assert record is not None
        assert record.profile.name == "repo-brain"


class TestRegister:
    def test_register_admin_defaults(self) -> None:
        from tapps_brain.profile import get_builtin_profile

        reg, cur, conn = _make_registry()
        profile = get_builtin_profile("repo-brain")
        record = reg.register("alpaca", profile)
        assert record.source == "admin"
        assert record.approved is True
        cur.execute.assert_called_once()
        sql, params = cur.execute.call_args[0]
        assert "INSERT INTO project_profiles" in sql
        assert "ON CONFLICT (project_id) DO UPDATE" in sql
        assert params[0] == "alpaca"
        assert params[2] is True  # approved
        assert params[3] == "admin"
        conn.commit.assert_called_once()

    def test_rejects_invalid_source(self) -> None:
        from tapps_brain.profile import get_builtin_profile

        reg, _, _ = _make_registry()
        profile = get_builtin_profile("repo-brain")
        with pytest.raises(ValueError, match="Invalid source"):
            reg.register("alpaca", profile, source="bogus")


class TestApprove:
    def test_returns_true_on_update(self) -> None:
        reg, cur, _ = _make_registry()
        cur.rowcount = 1
        assert reg.approve("alpaca") is True

    def test_returns_false_when_unknown(self) -> None:
        reg, cur, _ = _make_registry()
        cur.rowcount = 0
        assert reg.approve("unknown") is False


class TestResolve:
    def test_returns_registered_profile(self) -> None:
        reg, cur, _ = _make_registry()
        cur.fetchone.return_value = _repo_brain_row("alpaca")
        profile = reg.resolve("alpaca")
        assert profile.name == "repo-brain"

    def test_strict_mode_rejects_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.project_registry import ProjectNotRegisteredError

        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        reg, cur, _ = _make_registry()
        cur.fetchone.return_value = None
        with pytest.raises(ProjectNotRegisteredError) as exc:
            reg.resolve("unknown")
        assert exc.value.project_id == "unknown"

    def test_lax_mode_auto_registers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        reg, cur, _ = _make_registry()
        cur.fetchone.return_value = None
        profile = reg.resolve("new-project")
        assert profile.name == "repo-brain"
        # Two execute calls: one SELECT (get), one INSERT (register).
        assert cur.execute.call_count == 2
        insert_sql = cur.execute.call_args_list[1][0][0]
        insert_params = cur.execute.call_args_list[1][0][1]
        assert "INSERT INTO project_profiles" in insert_sql
        assert insert_params[0] == "new-project"
        assert insert_params[2] is False  # approved
        assert insert_params[3] == "auto"  # source
