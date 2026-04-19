"""Unit tests for the AgentBrain unified facade (EPIC-057)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.agent_brain import AgentBrain, BrainValidationError, _content_key, _parse_csv_env

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_brain(tmp_path: Path, **kwargs) -> AgentBrain:
    """Create an AgentBrain using a tmp_path and no Hive DSN."""
    defaults = {
        "agent_id": "test-agent",
        "project_dir": tmp_path,
    }
    defaults.update(kwargs)
    return AgentBrain(**defaults)


# ---------------------------------------------------------------------------
# STORY-057.1: Construction
# ---------------------------------------------------------------------------


class TestAgentBrainCreation:
    def test_basic_construction(self, tmp_path: Path) -> None:
        brain = _make_brain(tmp_path)
        assert brain.agent_id == "test-agent"
        assert brain.store is not None
        brain.close()

    def test_construction_with_no_agent_id(self, tmp_path: Path) -> None:
        brain = _make_brain(tmp_path, agent_id=None)
        assert brain.agent_id is None
        brain.close()

    def test_construction_with_groups(self, tmp_path: Path) -> None:
        brain = _make_brain(tmp_path, groups=["dev-pipeline", "frontend"])
        assert brain.groups == ["dev-pipeline", "frontend"]
        brain.close()

    def test_construction_with_expert_domains(self, tmp_path: Path) -> None:
        brain = _make_brain(tmp_path, expert_domains=["css", "react"])
        assert brain.expert_domains == ["css", "react"]
        brain.close()


class TestAgentBrainFromEnvVars:
    def test_agent_id_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_AGENT_ID", "env-agent")
        brain = AgentBrain(project_dir=tmp_path)
        assert brain.agent_id == "env-agent"
        brain.close()

    def test_project_dir_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_PROJECT_DIR", str(tmp_path))
        brain = AgentBrain(agent_id="test")
        assert brain._project_dir == tmp_path.resolve()
        brain.close()

    def test_groups_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_GROUPS", "grp-a, grp-b")
        brain = AgentBrain(agent_id="test", project_dir=tmp_path)
        assert brain.groups == ["grp-a", "grp-b"]
        brain.close()

    def test_expert_domains_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_EXPERT_DOMAINS", "css,react")
        brain = AgentBrain(agent_id="test", project_dir=tmp_path)
        assert brain.expert_domains == ["css", "react"]
        brain.close()


# ---------------------------------------------------------------------------
# STORY-057.7: Context Manager
# ---------------------------------------------------------------------------


class TestAgentBrainContextManager:
    def test_context_manager(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            assert brain.agent_id == "test-agent"
        # After exiting, brain should be closed
        assert brain._closed is True

    def test_double_close(self, tmp_path: Path) -> None:
        brain = _make_brain(tmp_path)
        brain.close()
        # Second close should not raise
        brain.close()
        assert brain._closed is True


# ---------------------------------------------------------------------------
# STORY-057.2: Core methods
# ---------------------------------------------------------------------------


class TestRemember:
    def test_remember_returns_key(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            key = brain.remember("Use Tailwind for styling")
            assert isinstance(key, str)
            assert len(key) > 0

    def test_remember_saves_to_store(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            key = brain.remember("Use Tailwind for styling")
            entry = brain.store.get(key)
            assert entry is not None
            assert entry.value == "Use Tailwind for styling"

    def test_remember_deterministic_key(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            key1 = brain.remember("Use Tailwind for styling")
            # Same content -> same key
            key2 = _content_key("Use Tailwind for styling")
            assert key1 == key2

    def test_remember_with_tier(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            key = brain.remember("Use React 18", tier="architectural")
            entry = brain.store.get(key)
            assert entry is not None
            assert str(entry.tier) == "architectural"


class TestRecall:
    def test_recall_returns_results(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Use Tailwind for styling components")
            results = brain.recall("styling")
            assert isinstance(results, list)
            assert len(results) >= 1
            assert "key" in results[0]
            assert "value" in results[0]

    def test_recall_max_results(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            for i in range(10):
                brain.remember(f"Memory fact number {i} about testing recall limits")
            results = brain.recall("memory fact", max_results=3)
            assert len(results) <= 3

    def test_recall_tracks_keys(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.remember("Use Tailwind for styling components")
            brain.recall("styling")
            assert len(brain._last_recalled_keys) >= 1


class TestRememberValidation:
    """TAP-632: remember() enforces BrainValidationError contract for share_with."""

    def test_remember_share_with_empty_string_raises(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            with pytest.raises(BrainValidationError, match="share_with"):
                brain.remember("some fact", share_with="")

    def test_remember_share_with_whitespace_only_raises(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            with pytest.raises(BrainValidationError, match="share_with"):
                brain.remember("some fact", share_with="   ")

    def test_remember_share_with_hive_is_valid(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            # "hive" is a valid share_with value — must not raise
            key = brain.remember("some fact", share_with="hive")
            assert isinstance(key, str)

    def test_remember_share_with_nonempty_group_is_valid(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            key = brain.remember("some fact", share_with="my-group")
            assert isinstance(key, str)


class TestRecallValidation:
    """TAP-632: recall() enforces BrainValidationError contract for max_results."""

    def test_recall_max_results_zero_raises(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            with pytest.raises(BrainValidationError, match="max_results"):
                brain.recall("query", max_results=0)

    def test_recall_max_results_negative_raises(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            with pytest.raises(BrainValidationError, match="max_results"):
                brain.recall("query", max_results=-5)

    def test_recall_max_results_negative_one_raises(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            with pytest.raises(BrainValidationError, match="max_results"):
                brain.recall("query", max_results=-1)

    def test_recall_max_results_positive_is_valid(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            results = brain.recall("query", max_results=1)
            assert isinstance(results, list)


class TestForget:
    def test_forget_archives_memory(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            key = brain.remember("Temporary note to forget")
            assert brain.store.get(key) is not None
            result = brain.forget(key)
            assert result is True
            assert brain.store.get(key) is None

    def test_forget_nonexistent(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            result = brain.forget("nonexistent-key")
            assert result is False


# ---------------------------------------------------------------------------
# STORY-057.3: Learning methods
# ---------------------------------------------------------------------------


class TestLearning:
    def test_learn_from_success_saves(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.learn_from_success("Styled the sidebar component")
            key = _content_key("success-Styled the sidebar component")
            entry = brain.store.get(key)
            assert entry is not None
            assert "success" in entry.tags

    def test_learn_from_success_with_task_id(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.learn_from_success("Styled sidebar", task_id="TASK-42")
            key = _content_key("success-Styled sidebar")
            entry = brain.store.get(key)
            assert entry is not None
            assert "task:TASK-42" in entry.tags

    def test_learn_from_success_with_context(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.set_task_context("TASK-99")
            brain.learn_from_success("Built the navbar")
            key = _content_key("success-Built the navbar")
            entry = brain.store.get(key)
            assert entry is not None
            assert "task:TASK-99" in entry.tags

    def test_learn_from_failure_saves(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.learn_from_failure("CSS grid broke on mobile", error="overflow")
            key = _content_key("failure-CSS grid broke on mobile")
            entry = brain.store.get(key)
            assert entry is not None
            assert "failure" in entry.tags
            assert "overflow" in entry.value

    def test_learn_from_failure_with_task_id(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            brain.learn_from_failure("Build failed", task_id="TASK-7")
            key = _content_key("failure-Build failed")
            entry = brain.store.get(key)
            assert entry is not None
            assert "task:TASK-7" in entry.tags


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestProperties:
    def test_agent_id_property(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path, agent_id="my-agent") as brain:
            assert brain.agent_id == "my-agent"

    def test_groups_property(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path, groups=["g1", "g2"]) as brain:
            assert brain.groups == ["g1", "g2"]

    def test_expert_domains_property(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path, expert_domains=["css"]) as brain:
            assert brain.expert_domains == ["css"]

    def test_store_property(self, tmp_path: Path) -> None:
        with _make_brain(tmp_path) as brain:
            assert brain.store is not None

    def test_hive_property(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
        with _make_brain(tmp_path) as brain:
            # v3 Postgres-only: without a DSN the Hive backend is None
            assert brain.hive is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_parse_csv_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_CSV_VAR", raising=False)
        assert _parse_csv_env("TEST_CSV_VAR") == []

    def test_parse_csv_env_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_CSV_VAR", "a, b, c")
        assert _parse_csv_env("TEST_CSV_VAR") == ["a", "b", "c"]

    def test_content_key_deterministic(self) -> None:
        key1 = _content_key("hello world")
        key2 = _content_key("hello world")
        assert key1 == key2

    def test_content_key_different_for_different_content(self) -> None:
        key1 = _content_key("hello world")
        key2 = _content_key("goodbye world")
        assert key1 != key2

    def test_content_key_has_slug(self) -> None:
        key = _content_key("Use Tailwind for styling")
        assert "use" in key
        assert "-" in key
