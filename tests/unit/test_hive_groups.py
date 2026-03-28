"""Tests for HiveStore group management (040.21 — GitHub #37)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tapps_brain.hive import HiveStore


@pytest.fixture
def store(tmp_path: Path) -> HiveStore:
    """HiveStore backed by a temp directory."""
    return HiveStore(db_path=tmp_path / "hive.db")


class TestCreateGroup:
    def test_create_group_returns_dict(self, store: HiveStore) -> None:
        result = store.create_group("team-alpha", description="Alpha team")
        assert result["name"] == "team-alpha"
        assert result["description"] == "Alpha team"
        assert "created_at" in result

    def test_create_group_no_description(self, store: HiveStore) -> None:
        result = store.create_group("team-beta")
        assert result["description"] == ""

    def test_create_group_idempotent(self, store: HiveStore) -> None:
        store.create_group("team-gamma", description="First")
        store.create_group("team-gamma", description="Updated")
        groups = store.list_groups()
        names = [g["name"] for g in groups]
        assert names.count("team-gamma") == 1


class TestListGroups:
    def test_list_empty(self, store: HiveStore) -> None:
        assert store.list_groups() == []

    def test_list_multiple(self, store: HiveStore) -> None:
        store.create_group("z-group")
        store.create_group("a-group")
        groups = store.list_groups()
        assert len(groups) == 2
        # Should be sorted by name
        assert groups[0]["name"] == "a-group"
        assert groups[1]["name"] == "z-group"


class TestAddGroupMember:
    def test_add_member_success(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        result = store.add_group_member("team-alpha", "agent-1")
        assert result is True

    def test_add_member_nonexistent_group(self, store: HiveStore) -> None:
        result = store.add_group_member("no-such-group", "agent-1")
        assert result is False

    def test_add_member_custom_role(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        store.add_group_member("team-alpha", "agent-lead", role="lead")
        members = store.get_group_members("team-alpha")
        roles = {m["agent_id"]: m["role"] for m in members}
        assert roles["agent-lead"] == "lead"

    def test_add_member_default_role(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        store.add_group_member("team-alpha", "agent-1")
        members = store.get_group_members("team-alpha")
        assert members[0]["role"] == "member"


class TestRemoveGroupMember:
    def test_remove_existing_member(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        store.add_group_member("team-alpha", "agent-1")
        result = store.remove_group_member("team-alpha", "agent-1")
        assert result is True
        assert store.get_group_members("team-alpha") == []

    def test_remove_nonexistent_member(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        result = store.remove_group_member("team-alpha", "ghost-agent")
        assert result is False


class TestGetGroupMembers:
    def test_empty_group(self, store: HiveStore) -> None:
        store.create_group("team-empty")
        assert store.get_group_members("team-empty") == []

    def test_multiple_members(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        store.add_group_member("team-alpha", "agent-1")
        store.add_group_member("team-alpha", "agent-2")
        members = store.get_group_members("team-alpha")
        agent_ids = {m["agent_id"] for m in members}
        assert agent_ids == {"agent-1", "agent-2"}


class TestGetAgentGroups:
    def test_agent_no_groups(self, store: HiveStore) -> None:
        assert store.get_agent_groups("agent-1") == []

    def test_agent_multiple_groups(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        store.create_group("team-beta")
        store.add_group_member("team-alpha", "agent-1")
        store.add_group_member("team-beta", "agent-1")
        groups = store.get_agent_groups("agent-1")
        assert set(groups) == {"team-alpha", "team-beta"}

    def test_agent_groups_sorted(self, store: HiveStore) -> None:
        store.create_group("z-team")
        store.create_group("a-team")
        store.add_group_member("z-team", "agent-1")
        store.add_group_member("a-team", "agent-1")
        groups = store.get_agent_groups("agent-1")
        assert groups == ["a-team", "z-team"]


class TestSearchWithGroups:
    def test_searches_own_namespace(self, store: HiveStore) -> None:
        store.save(key="private-fact", value="I know Python", namespace="agent-1")
        results = store.search_with_groups("Python", agent_id="agent-1", agent_namespace="agent-1")
        keys = [r["key"] for r in results]
        assert "private-fact" in keys

    def test_searches_group_namespace(self, store: HiveStore) -> None:
        store.create_group("team-alpha")
        store.add_group_member("team-alpha", "agent-1")
        # Shared group memory stored in namespace = group name
        store.save(key="shared-fact", value="Deploy on Fridays is bad", namespace="team-alpha")
        results = store.search_with_groups("Fridays", agent_id="agent-1", agent_namespace="agent-1")
        keys = [r["key"] for r in results]
        assert "shared-fact" in keys

    def test_searches_universal_namespace(self, store: HiveStore) -> None:
        store.save(key="global-fact", value="Always use HTTPS", namespace="universal")
        results = store.search_with_groups("HTTPS", agent_id="agent-1", agent_namespace="agent-1")
        keys = [r["key"] for r in results]
        assert "global-fact" in keys

    def test_excludes_other_agent_private_namespace(self, store: HiveStore) -> None:
        store.save(key="secret", value="Agent 2 secret password", namespace="agent-2")
        results = store.search_with_groups("secret", agent_id="agent-1", agent_namespace="agent-1")
        keys = [r["key"] for r in results]
        assert "secret" not in keys

    def test_agent_namespace_defaults_to_agent_id(self, store: HiveStore) -> None:
        store.save(key="my-fact", value="Coffee is essential", namespace="agent-42")
        results = store.search_with_groups("Coffee", agent_id="agent-42")
        keys = [r["key"] for r in results]
        assert "my-fact" in keys
