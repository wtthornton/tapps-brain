"""Negative tests for scope enforcement — EPIC-063 STORY-063.7.

Verifies that:
1. ``PropagationEngine.propagate()`` rejects group writes when the agent is not
   a member of the target group (``hive_store.agent_is_group_member`` → False).
2. ``PropagationEngine.propagate()`` silently drops private-scoped entries.
3. Profile ``private_tiers`` override forces entries to stay private regardless
   of the caller-supplied ``agent_scope``.
4. The ``bypass_profile_hive_rules=True`` flag skips tier overrides (sanity check
   for the --force / batch-push path).
5. Cross-tenant writes are structurally impossible: a ``PrivateBackend`` keyed to
   ``(project_id, agent_id)`` cannot write rows visible to a different agent.

These are pure unit tests — no real Postgres instance required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hive_mock(*, member: bool = True) -> MagicMock:
    """Return a ``HiveBackend``-shaped mock.

    :param member: Value returned by ``agent_is_group_member``.
    """
    hive = MagicMock()
    hive.agent_is_group_member.return_value = member
    hive.save.return_value = {"namespace": "grp-a", "key": "k"}
    return hive


def _propagate(
    *,
    agent_scope: str = "hive",
    agent_id: str = "agent-1",
    agent_profile: str = "profile-a",
    tier: str = "context",
    hive_store: MagicMock | None = None,
    auto_propagate_tiers: list[str] | None = None,
    private_tiers: list[str] | None = None,
    bypass_profile_hive_rules: bool = False,
) -> object:
    from tapps_brain.backends import PropagationEngine

    if hive_store is None:
        hive_store = _make_hive_mock()

    return PropagationEngine.propagate(
        key="test-key",
        value="test-value",
        agent_scope=agent_scope,
        agent_id=agent_id,
        agent_profile=agent_profile,
        tier=tier,
        confidence=0.8,
        source="agent",
        tags=None,
        hive_store=hive_store,
        auto_propagate_tiers=auto_propagate_tiers,
        private_tiers=private_tiers,
        bypass_profile_hive_rules=bypass_profile_hive_rules,
    )


# ---------------------------------------------------------------------------
# Group membership rejection
# ---------------------------------------------------------------------------


class TestGroupMembershipRejection:
    """Wrong group → propagate() returns None; no hive write occurs."""

    def test_group_scope_denied_when_not_member(self) -> None:
        """Agent not a group member → propagate returns None."""
        hive = _make_hive_mock(member=False)
        result = _propagate(agent_scope="group:restricted", hive_store=hive)

        assert result is None

    def test_group_scope_denied_no_hive_save_called(self) -> None:
        """When rejected, hive.save must NOT be called."""
        hive = _make_hive_mock(member=False)
        _propagate(agent_scope="group:restricted", hive_store=hive)

        hive.save.assert_not_called()

    def test_group_scope_allowed_when_member(self) -> None:
        """Agent IS a group member → write proceeds and returns dict."""
        hive = _make_hive_mock(member=True)
        result = _propagate(agent_scope="group:allowed", hive_store=hive)

        assert result is not None
        hive.save.assert_called_once()

    def test_group_membership_checked_with_correct_args(self) -> None:
        """agent_is_group_member receives the group name and agent_id."""
        hive = _make_hive_mock(member=False)
        _propagate(
            agent_scope="group:my-team",
            agent_id="agent-xyz",
            hive_store=hive,
        )

        hive.agent_is_group_member.assert_called_once_with("my-team", "agent-xyz")

    def test_group_denied_returns_none_not_raises(self) -> None:
        """Rejection is silent (returns None, not an exception)."""
        hive = _make_hive_mock(member=False)
        try:
            result = _propagate(agent_scope="group:secret", hive_store=hive)
        except Exception as exc:
            raise AssertionError(f"Expected None, got exception: {exc}") from exc
        assert result is None

    def test_different_group_names_each_checked(self) -> None:
        """Each group scope string triggers its own membership check."""
        hive = _make_hive_mock(member=False)

        _propagate(agent_scope="group:alpha", agent_id="a", hive_store=hive)
        _propagate(agent_scope="group:beta", agent_id="a", hive_store=hive)

        expected_calls = [
            call("alpha", "a"),
            call("beta", "a"),
        ]
        hive.agent_is_group_member.assert_has_calls(expected_calls, any_order=False)


# ---------------------------------------------------------------------------
# Private scope — always silent drop
# ---------------------------------------------------------------------------


class TestPrivateScopeDrops:
    """Private scope must never reach the Hive backend."""

    def test_private_scope_returns_none(self) -> None:
        hive = _make_hive_mock()
        result = _propagate(agent_scope="private", hive_store=hive)

        assert result is None

    def test_private_scope_no_hive_save(self) -> None:
        hive = _make_hive_mock()
        _propagate(agent_scope="private", hive_store=hive)

        hive.save.assert_not_called()

    def test_private_scope_no_membership_check(self) -> None:
        hive = _make_hive_mock()
        _propagate(agent_scope="private", hive_store=hive)

        hive.agent_is_group_member.assert_not_called()


# ---------------------------------------------------------------------------
# Profile tier overrides — private_tiers forces private
# ---------------------------------------------------------------------------


class TestProfileTierOverrides:
    """private_tiers in profile forces scope to private regardless of agent_scope."""

    def test_private_tier_overrides_hive_scope(self) -> None:
        """Tier in private_tiers → entry stays private even if scope=hive."""
        hive = _make_hive_mock()
        result = _propagate(
            agent_scope="hive",
            tier="context",
            private_tiers=["context"],
            hive_store=hive,
        )

        assert result is None
        hive.save.assert_not_called()

    def test_private_tier_overrides_group_scope(self) -> None:
        """Tier in private_tiers → entry stays private even if scope=group:<name>."""
        hive = _make_hive_mock(member=True)
        result = _propagate(
            agent_scope="group:my-team",
            tier="context",
            private_tiers=["context"],
            hive_store=hive,
        )

        assert result is None
        hive.save.assert_not_called()

    def test_non_private_tier_not_overridden(self) -> None:
        """Tier NOT in private_tiers → propagation proceeds normally."""
        hive = _make_hive_mock()
        result = _propagate(
            agent_scope="hive",
            tier="pattern",
            private_tiers=["context"],
            hive_store=hive,
        )

        # "pattern" not in private_tiers → should propagate
        assert result is not None

    def test_bypass_profile_hive_rules_ignores_private_tiers(self) -> None:
        """bypass=True: private_tiers must NOT force the scope."""
        hive = _make_hive_mock()
        result = _propagate(
            agent_scope="hive",
            tier="context",
            private_tiers=["context"],
            bypass_profile_hive_rules=True,
            hive_store=hive,
        )

        # With bypass, the tier override is skipped → should propagate
        assert result is not None
        hive.save.assert_called_once()


# ---------------------------------------------------------------------------
# Cross-tenant private write isolation (structural, no Postgres needed)
# ---------------------------------------------------------------------------


class TestCrossTenantWriteIsolation:
    """Verify that PostgresPrivateBackend is keyed at construction time.

    The backend stores ``(project_id, agent_id)`` immutably and filters ALL
    queries by those values. Cross-tenant writes are therefore structurally
    impossible without constructing a separate backend instance.

    These tests use mocked DB calls to verify the keying logic.
    """

    def _make_mock_backend(self, project_id: str, agent_id: str) -> object:
        """Return a ``PostgresPrivateBackend`` with mocked connection manager."""
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_private import PostgresPrivateBackend

        mock_cm = MagicMock(spec=PostgresConnectionManager)
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_cursor.fetchone.return_value = None

        mock_cm.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        # EPIC-069 STORY-069.8: project_context is the tenant-scoped entry;
        # route it to the same mock connection so RLS-wired paths still work.
        mock_cm.project_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.project_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_cm.admin_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        return PostgresPrivateBackend(mock_cm, project_id=project_id, agent_id=agent_id)

    def test_backend_stores_project_and_agent_at_construction(self) -> None:
        """Constructor captures project_id and agent_id; these are used for all queries."""
        from tapps_brain.postgres_private import PostgresPrivateBackend

        mock_cm = MagicMock()
        backend = PostgresPrivateBackend(mock_cm, project_id="proj-a", agent_id="agent-x")
        assert backend._project_id == "proj-a"
        assert backend._agent_id == "agent-x"

    def test_save_includes_owning_agent_id_in_query(self) -> None:
        """save() must embed the backend's own agent_id in the INSERT, not a caller-supplied one."""
        from tapps_brain.models import MemoryEntry

        mock_cm = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        mock_cm.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        # EPIC-069 STORY-069.8: project_context is the tenant-scoped entry;
        # route it to the same mock connection so RLS-wired paths still work.
        mock_cm.project_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.project_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_cm.admin_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        from tapps_brain.postgres_private import PostgresPrivateBackend

        backend = PostgresPrivateBackend(mock_cm, project_id="proj-z", agent_id="owner")
        entry = MemoryEntry(key="k1", value="sensitive data")
        backend.save(entry)

        # The INSERT must include "owner" (the construction-time agent_id) in its params
        all_calls = mock_cursor.execute.call_args_list
        # Find the INSERT call
        insert_calls = [c for c in all_calls if "INSERT" in str(c)]
        assert insert_calls, "No INSERT executed — save() may not have run"
        insert_params = insert_calls[0][0][1]  # positional params tuple
        # project_id and agent_id must appear in INSERT params
        param_str = str(insert_params)
        assert "proj-z" in param_str, "project_id not in INSERT params"
        assert "owner" in param_str, "agent_id not in INSERT params"

    def test_two_agent_backends_in_same_project_are_independent(self) -> None:
        """Backends for agent-A and agent-B in the same project have disjoint agent_id keys."""
        backend_a = self._make_mock_backend("shared-proj", "agent-a")
        backend_b = self._make_mock_backend("shared-proj", "agent-b")

        # Each backend carries its own agent_id — they cannot share rows
        from tapps_brain.postgres_private import PostgresPrivateBackend

        assert isinstance(backend_a, PostgresPrivateBackend)
        assert isinstance(backend_b, PostgresPrivateBackend)
        assert backend_a._agent_id == "agent-a"  # type: ignore[attr-defined]
        assert backend_b._agent_id == "agent-b"  # type: ignore[attr-defined]
        # Crucially: backend_a cannot be coerced into reading agent-b's rows
        assert backend_a._agent_id != backend_b._agent_id  # type: ignore[attr-defined]

    def test_different_projects_have_independent_project_ids(self) -> None:
        """Same agent_id in different projects yields fully disjoint backends."""
        backend_x = self._make_mock_backend("proj-x", "same-agent")
        backend_y = self._make_mock_backend("proj-y", "same-agent")

        from tapps_brain.postgres_private import PostgresPrivateBackend

        assert isinstance(backend_x, PostgresPrivateBackend)
        assert isinstance(backend_y, PostgresPrivateBackend)
        assert backend_x._project_id != backend_y._project_id  # type: ignore[attr-defined]

    def test_load_all_filters_include_agent_id(self) -> None:
        """load_all() must pass the construction-time agent_id as a query filter."""
        mock_cm = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        mock_cm.get_connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.get_connection.return_value.__exit__ = MagicMock(return_value=False)
        # EPIC-069 STORY-069.8: project_context is the tenant-scoped entry;
        # route it to the same mock connection so RLS-wired paths still work.
        mock_cm.project_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.project_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_cm.admin_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        from tapps_brain.postgres_private import PostgresPrivateBackend

        backend = PostgresPrivateBackend(mock_cm, project_id="proj-q", agent_id="agent-q")
        backend.load_all()

        all_calls = mock_cursor.execute.call_args_list
        select_calls = [c for c in all_calls if "SELECT" in str(c)]
        assert select_calls, "No SELECT executed — load_all() may not have run"
        select_params = select_calls[0][0][1]
        param_str = str(select_params)
        assert "agent-q" in param_str, "agent_id not in SELECT params — cross-agent reads possible"
        assert "proj-q" in param_str, "project_id not in SELECT params"


# ---------------------------------------------------------------------------
# Dry-run mode: no writes even when propagation would succeed
# ---------------------------------------------------------------------------


class TestDryRun:
    """dry_run=True must never call hive_store.save()."""

    def test_dry_run_hive_scope_no_write(self) -> None:
        hive = _make_hive_mock()
        result = _propagate.__wrapped__ if hasattr(_propagate, "__wrapped__") else None
        from tapps_brain.backends import PropagationEngine

        result = PropagationEngine.propagate(
            key="dr-key",
            value="dr-value",
            agent_scope="hive",
            agent_id="agent-1",
            agent_profile="prof-1",
            tier="context",
            confidence=0.9,
            source="agent",
            tags=None,
            hive_store=hive,
            dry_run=True,
        )

        hive.save.assert_not_called()
        assert result is not None
        assert result.get("dry_run") is True  # type: ignore[union-attr]

    def test_dry_run_group_scope_no_write(self) -> None:
        """dry_run with group scope: membership is still checked, but save is skipped."""
        hive = _make_hive_mock(member=True)
        from tapps_brain.backends import PropagationEngine

        result = PropagationEngine.propagate(
            key="dr-grp",
            value="dr-grp-value",
            agent_scope="group:dry-team",
            agent_id="agent-1",
            agent_profile="prof-1",
            tier="context",
            confidence=0.9,
            source="agent",
            tags=None,
            hive_store=hive,
            dry_run=True,
        )

        hive.save.assert_not_called()
        assert result is not None
        assert result.get("dry_run") is True  # type: ignore[union-attr]
