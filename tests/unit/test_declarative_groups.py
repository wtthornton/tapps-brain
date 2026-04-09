"""Unit tests for declarative group membership and expert publishing (EPIC-056)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


def _make_hive_mock() -> MagicMock:
    """Create a mock HiveBackend with the required interface."""
    hive = MagicMock()
    hive._db_path = MagicMock()
    hive._db_path.parent = MagicMock()
    hive.create_group.return_value = {"name": "test-group"}
    hive.add_group_member.return_value = True
    hive.save.return_value = {"key": "test"}
    hive.search.return_value = []
    return hive


def _make_store(
    tmp_path: Path,
    *,
    groups: list[str] | None = None,
    expert_domains: list[str] | None = None,
    hive_store: Any = None,
    agent_id: str | None = None,
) -> MemoryStore:
    """Create a MemoryStore with optional group/expert config."""
    return MemoryStore(
        tmp_path,
        embedding_provider=None,
        groups=groups,
        expert_domains=expert_domains,
        hive_store=hive_store,
        agent_id=agent_id,
        auto_register=False,
    )


# ---------------------------------------------------------------------------
# STORY-056.1: Declarative group membership
# ---------------------------------------------------------------------------


class TestDeclarativeGroups:
    """Tests for groups parameter on MemoryStore (STORY-056.1)."""

    def test_store_accepts_groups_parameter(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, groups=["dev-pipeline", "qa-team"])
        assert store.groups == ["dev-pipeline", "qa-team"]

    def test_store_groups_default_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.groups == []

    def test_store_groups_returns_copy(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, groups=["dev"])
        groups = store.groups
        groups.append("mutated")
        assert store.groups == ["dev"]

    def test_group_auto_join_on_construction(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        _make_store(
            tmp_path,
            groups=["dev-pipeline", "qa-team"],
            hive_store=hive,
            agent_id="test-agent",
        )
        # Should have created both groups
        assert hive.create_group.call_count == 2
        hive.create_group.assert_any_call("dev-pipeline")
        hive.create_group.assert_any_call("qa-team")
        # Should have added agent to both groups
        assert hive.add_group_member.call_count == 2
        hive.add_group_member.assert_any_call("dev-pipeline", "test-agent")
        hive.add_group_member.assert_any_call("qa-team", "test-agent")

    def test_group_auto_join_skipped_without_hive(self, tmp_path: Path) -> None:
        """Groups are stored but no auto-join happens without a hive_store."""
        store = _make_store(tmp_path, groups=["dev-pipeline"], agent_id="test-agent")
        assert store.groups == ["dev-pipeline"]

    def test_group_auto_join_skipped_without_agent_id(self, tmp_path: Path) -> None:
        """Groups + hive but no agent_id: auto-join is skipped."""
        hive = _make_hive_mock()
        store = _make_store(tmp_path, groups=["dev-pipeline"], hive_store=hive)
        assert store.groups == ["dev-pipeline"]
        hive.create_group.assert_not_called()


# ---------------------------------------------------------------------------
# STORY-056.1 (expert_domains property)
# ---------------------------------------------------------------------------


class TestExpertDomains:
    """Tests for expert_domains parameter on MemoryStore (STORY-056.1)."""

    def test_store_accepts_expert_domains(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, expert_domains=["security", "performance"])
        assert store.expert_domains == ["security", "performance"]

    def test_store_expert_domains_default_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.expert_domains == []

    def test_store_expert_domains_returns_copy(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, expert_domains=["sec"])
        domains = store.expert_domains
        domains.append("mutated")
        assert store.expert_domains == ["sec"]


# ---------------------------------------------------------------------------
# STORY-056.2: Expert auto-publishing
# ---------------------------------------------------------------------------


class TestExpertAutoPublish:
    """Tests for expert domain auto-publishing (STORY-056.2)."""

    def test_expert_auto_publish_architectural(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            expert_domains=["security"],
            hive_store=hive,
            agent_id="sec-agent",
        )
        result = store.save(
            key="sec-pattern-1",
            value="Always validate input at boundaries",
            tier="architectural",
            agent_scope="private",
        )
        # Should have auto-published to universal namespace with expert tags
        hive_save_calls = hive.save.call_args_list
        # Find the expert auto-publish call (namespace="universal" with expert tags)
        expert_calls = [
            c
            for c in hive_save_calls
            if c.kwargs.get("namespace") == "universal"
            and any("expert:security" in t for t in (c.kwargs.get("tags") or []))
        ]
        assert len(expert_calls) == 1
        call_tags = expert_calls[0].kwargs["tags"]
        assert "expert:security" in call_tags

    def test_expert_auto_publish_pattern_tier(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            expert_domains=["performance"],
            hive_store=hive,
            agent_id="perf-agent",
        )
        store.save(
            key="perf-pattern-1",
            value="Use connection pooling for database access",
            tier="pattern",
            agent_scope="private",
        )
        expert_calls = [
            c
            for c in hive.save.call_args_list
            if c.kwargs.get("namespace") == "universal"
            and any("expert:performance" in t for t in (c.kwargs.get("tags") or []))
        ]
        assert len(expert_calls) == 1

    def test_expert_no_publish_context_tier(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            expert_domains=["security"],
            hive_store=hive,
            agent_id="sec-agent",
        )
        store.save(
            key="ctx-1",
            value="Current session context information",
            tier="context",
            agent_scope="private",
        )
        # No expert auto-publish calls for context tier
        expert_calls = [
            c
            for c in hive.save.call_args_list
            if c.kwargs.get("namespace") == "universal"
            and any("expert:" in t for t in (c.kwargs.get("tags") or []))
        ]
        assert len(expert_calls) == 0

    def test_expert_no_publish_when_auto_publish_false(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            expert_domains=["security"],
            hive_store=hive,
            agent_id="sec-agent",
        )
        store.save(
            key="sec-pattern-opt-out",
            value="This should not be auto-published",
            tier="architectural",
            agent_scope="private",
            auto_publish=False,
        )
        expert_calls = [
            c
            for c in hive.save.call_args_list
            if c.kwargs.get("namespace") == "universal"
            and any("expert:" in t for t in (c.kwargs.get("tags") or []))
        ]
        assert len(expert_calls) == 0

    def test_expert_no_publish_when_not_private(self, tmp_path: Path) -> None:
        """Expert auto-publish only fires for agent_scope=private."""
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            expert_domains=["security"],
            groups=["dev-team"],
            hive_store=hive,
            agent_id="sec-agent",
        )
        store.save(
            key="sec-shared",
            value="Shared security pattern for the group",
            tier="architectural",
            agent_scope="group:dev-team",
        )
        expert_calls = [
            c
            for c in hive.save.call_args_list
            if c.kwargs.get("namespace") == "universal"
            and any("expert:" in t for t in (c.kwargs.get("tags") or []))
        ]
        assert len(expert_calls) == 0


# ---------------------------------------------------------------------------
# STORY-056.3: Group-scoped save routing
# ---------------------------------------------------------------------------


class TestGroupScopedSave:
    """Tests for group-scoped save routing (STORY-056.3)."""

    def test_group_scoped_save_propagates_to_all_groups(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            groups=["dev-pipeline", "qa-team"],
            hive_store=hive,
            agent_id="test-agent",
        )
        # Reset call count from __init__ group setup
        hive.save.reset_mock()

        store.save(
            key="shared-memory-1",
            value="Shared across all groups",
            tier="pattern",
            agent_scope="group",
        )
        # Should propagate to both group namespaces
        group_calls = [
            c
            for c in hive.save.call_args_list
            if c.kwargs.get("namespace", "").startswith("group:")
        ]
        namespaces = {c.kwargs["namespace"] for c in group_calls}
        assert "group:dev-pipeline" in namespaces
        assert "group:qa-team" in namespaces

    def test_group_scoped_save_to_specific_group(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            groups=["dev-pipeline", "qa-team"],
            hive_store=hive,
            agent_id="test-agent",
        )
        hive.save.reset_mock()

        store.save(
            key="dev-only-1",
            value="Dev pipeline specific memory",
            tier="pattern",
            agent_scope="group:dev-pipeline",
        )
        group_calls = [
            c
            for c in hive.save.call_args_list
            if c.kwargs.get("namespace", "").startswith("group:")
        ]
        assert len(group_calls) == 1
        assert group_calls[0].kwargs["namespace"] == "group:dev-pipeline"

    def test_save_to_nonmember_group_fails(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(
            tmp_path,
            groups=["dev-pipeline"],
            hive_store=hive,
            agent_id="test-agent",
        )
        result = store.save(
            key="bad-save",
            value="Trying to save to a group I'm not in",
            tier="pattern",
            agent_scope="group:unknown-group",
        )
        assert isinstance(result, dict)
        assert result["error"] == "invalid_agent_scope"
        assert "unknown-group" in result["message"]


# ---------------------------------------------------------------------------
# STORY-056.6: Profile schema extension
# ---------------------------------------------------------------------------


class TestProfileSchema:
    """Tests for HiveConfig extensions (STORY-056.6)."""

    def test_hive_config_groups_default(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig()
        assert cfg.groups == []

    def test_hive_config_expert_domains_default(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig()
        assert cfg.expert_domains == []

    def test_hive_config_recall_weights_default(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig()
        assert cfg.recall_weights == {"local": 0.5, "group": 0.3, "hive": 0.2}

    def test_hive_config_auto_publish_tiers_default(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig()
        assert cfg.auto_publish_tiers == ["architectural", "pattern"]

    def test_hive_config_with_groups(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig(groups=["dev-pipeline", "qa-team"])
        assert cfg.groups == ["dev-pipeline", "qa-team"]

    def test_hive_config_with_expert_domains(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig(expert_domains=["security", "performance"])
        assert cfg.expert_domains == ["security", "performance"]

    def test_hive_config_custom_recall_weights(self) -> None:
        from tapps_brain.profile import HiveConfig

        cfg = HiveConfig(recall_weights={"local": 0.7, "group": 0.2, "hive": 0.1})
        assert cfg.recall_weights["local"] == 0.7


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Ensure stores without groups/expert_domains work exactly as before."""

    def test_store_without_groups_saves_normally(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.save(key="normal-1", value="Normal save without groups", tier="pattern")
        assert hasattr(result, "key")
        assert result.key == "normal-1"

    def test_store_without_expert_domains_no_auto_publish(self, tmp_path: Path) -> None:
        hive = _make_hive_mock()
        store = _make_store(tmp_path, hive_store=hive, agent_id="agent-1")
        hive.save.reset_mock()
        store.save(
            key="no-expert-1",
            value="Should not auto-publish",
            tier="architectural",
            agent_scope="private",
        )
        # No expert tags in any hive save call
        expert_calls = [
            c
            for c in hive.save.call_args_list
            if any("expert:" in t for t in (c.kwargs.get("tags") or []))
        ]
        assert len(expert_calls) == 0
