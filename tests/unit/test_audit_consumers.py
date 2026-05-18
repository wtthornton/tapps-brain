"""Tests for ``memory_service.audit_consumers`` (TAP-2093).

Covers empty registry + counts, all-silent agents, all-active agents,
orphan agent_ids in the counter that are not in the registry, and shape
validation of the ``since`` parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from tapps_brain.services import memory_service

if TYPE_CHECKING:
    pass


@pytest.fixture(autouse=True)
def _isolate_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the otel tool-call counter with a fresh empty dict per test.

    The counter is process-global; without this fixture, calls from other tests
    in the same session would leak in.
    """
    import tapps_brain.otel_tracer as ot

    monkeypatch.setattr(ot, "_TOOL_CALL_COUNTS", {})


def _patch_registry(monkeypatch: pytest.MonkeyPatch, agent_ids: list[str]) -> None:
    """Stub :class:`AgentRegistry` to return one bare ``AgentRegistration`` per id."""
    from tapps_brain.models import AgentRegistration

    agents = [AgentRegistration(id=aid) for aid in agent_ids]
    mock_reg = MagicMock()
    mock_reg.list_agents.return_value = agents
    monkeypatch.setattr("tapps_brain.backends.AgentRegistry", lambda *a, **k: mock_reg)


def _record(project_id: str, agent_id: str, tool: str, n: int = 1) -> None:
    import tapps_brain.otel_tracer as ot

    key = (project_id, agent_id, tool, "success")
    ot._TOOL_CALL_COUNTS[key] = ot._TOOL_CALL_COUNTS.get(key, 0) + n


def test_empty_registry_and_empty_counter_returns_empty_lists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, [])

    report = memory_service.audit_consumers(store=None, project_id="proj-a", agent_id="cli")

    assert report["project_id"] == "proj-a"
    assert report["declared_silent"] == []
    assert report["active"] == []
    assert report["unregistered_active"] == []
    assert report["window_effective"] == "process_start"
    assert "as_of" in report


def test_all_silent_when_no_calls_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, ["alpha", "beta", "gamma"])

    report = memory_service.audit_consumers(store=None, project_id="proj-a", agent_id="cli")

    assert sorted(report["declared_silent"]) == ["alpha", "beta", "gamma"]
    assert report["active"] == []
    assert report["unregistered_active"] == []


def test_active_agents_sorted_by_total_desc(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, ["alpha", "beta", "gamma"])
    _record("proj-a", "alpha", "brain_recall", 3)
    _record("proj-a", "beta", "brain_recall", 10)
    _record("proj-a", "beta", "brain_remember", 2)
    _record("proj-a", "gamma", "brain_status", 1)

    report = memory_service.audit_consumers(store=None, project_id="proj-a", agent_id="cli")

    assert report["declared_silent"] == []
    assert [r["agent_id"] for r in report["active"]] == ["beta", "alpha", "gamma"]
    beta = report["active"][0]
    assert beta["total_calls"] == 12
    assert beta["tools"] == {"brain_recall": 10, "brain_remember": 2}


def test_orphan_agents_flagged_as_unregistered(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, ["alpha"])
    _record("proj-a", "alpha", "brain_recall", 1)
    _record("proj-a", "ghost", "brain_recall", 5)

    report = memory_service.audit_consumers(store=None, project_id="proj-a", agent_id="cli")

    assert report["declared_silent"] == []
    assert [r["agent_id"] for r in report["active"]] == ["ghost", "alpha"]
    assert report["unregistered_active"] == ["ghost"]


def test_counts_for_other_projects_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, ["alpha"])
    _record("proj-other", "alpha", "brain_recall", 50)

    report = memory_service.audit_consumers(store=None, project_id="proj-a", agent_id="cli")

    assert report["declared_silent"] == ["alpha"]
    assert report["active"] == []


def test_non_brain_tools_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ``brain_*`` calls count toward 'active' — memory_*/hive_* are noise."""
    _patch_registry(monkeypatch, ["alpha"])
    _record("proj-a", "alpha", "memory_search", 10)
    _record("proj-a", "alpha", "hive_search", 10)

    report = memory_service.audit_consumers(store=None, project_id="proj-a", agent_id="cli")

    assert report["declared_silent"] == ["alpha"]
    assert report["active"] == []


def test_target_project_id_overrides_caller_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, ["alpha"])
    _record("proj-target", "alpha", "brain_recall", 1)

    report = memory_service.audit_consumers(
        store=None,
        project_id="proj-caller",
        agent_id="cli",
        target_project_id="proj-target",
    )

    assert report["project_id"] == "proj-target"
    assert report["declared_silent"] == []
    assert [r["agent_id"] for r in report["active"]] == ["alpha"]


def test_invalid_since_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_registry(monkeypatch, [])

    report = memory_service.audit_consumers(
        store=None,
        project_id="proj-a",
        agent_id="cli",
        since="not-a-timestamp",
    )

    assert report["error"] == "invalid_since"
    assert "not-a-timestamp" in report["message"]


def test_valid_since_is_echoed_in_since_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_registry(monkeypatch, [])
    ts = "2026-05-17T12:00:00Z"

    report = memory_service.audit_consumers(
        store=None,
        project_id="proj-a",
        agent_id="cli",
        since=ts,
    )

    assert report["since_requested"] == ts
    assert report["window_effective"] == "process_start"
