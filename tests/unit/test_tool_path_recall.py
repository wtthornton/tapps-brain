"""TAP-5545: approved-only tool-path recall.

The endpoint AgentForge's ``learning_injection`` calls. Two rules carry the
weight: an unapproved learning must never reach the wire, and "nothing
approved yet" must never degrade into "here are some candidates".

Result items are shaped for AF's ``candidates_from_tool_path_learnings``
(``value`` / ``tags`` / ``confidence`` / ``project_id``) so the consumer needs
no translation layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.models import LearningStatus, MemoryEntry, MemoryStatus, PromotionSignal
from tapps_brain.services import memory_service

if TYPE_CHECKING:
    from collections.abc import Sequence


class _FakeStore:
    """Minimal store stand-in: ``search`` returns a fixed ranked list."""

    def __init__(self, entries: Sequence[MemoryEntry]) -> None:
        self._entries = list(entries)
        self.search_calls: list[str] = []

    def search(self, query: str, **_kwargs: object) -> list[MemoryEntry]:
        self.search_calls.append(query)
        return list(self._entries)


def _entry(
    key: str,
    *,
    tags: list[str] | None = None,
    learning_status: LearningStatus = LearningStatus.approved,
    confidence: float = 0.8,
    status: MemoryStatus = MemoryStatus.active,
) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=f"value for {key}",
        tags=tags if tags is not None else ["fleet:learning"],
        confidence=confidence,
        status=status,
        learning_status=learning_status,
        promoted_by="eval-run-1" if learning_status is LearningStatus.approved else None,
        promoted_at="2026-08-05T00:00:00+00:00"
        if learning_status is LearningStatus.approved
        else None,
        promotion_signal=PromotionSignal.eval
        if learning_status is LearningStatus.approved
        else None,
    )


def _recall(store: _FakeStore, **kwargs: object) -> dict[str, object]:
    params: dict[str, object] = {"task_type": "refactor-python-module"}
    params.update(kwargs)
    return memory_service.brain_recall_tool_paths(store, "proj", "agent", **params)  # type: ignore[arg-type]


class TestApprovedOnlyDefault:
    def test_approved_entry_is_returned(self) -> None:
        result = _recall(_FakeStore([_entry("k1")]))
        assert result["count"] == 1
        assert result["tool_paths"][0]["key"] == "k1"  # type: ignore[index]

    def test_candidate_is_withheld_by_default(self) -> None:
        """A high-confidence candidate is still a candidate."""
        store = _FakeStore(
            [_entry("k1", learning_status=LearningStatus.candidate, confidence=0.99)]
        )
        result = _recall(store)
        assert result["count"] == 0
        assert result["tool_paths"] == []

    def test_demoted_is_withheld_by_default(self) -> None:
        store = _FakeStore([_entry("k1", learning_status=LearningStatus.demoted)])
        assert _recall(store)["count"] == 0

    def test_default_learning_status_is_reported(self) -> None:
        assert _recall(_FakeStore([]))["learning_status"] == "approved"


class TestFailClosed:
    def test_no_approved_matches_returns_empty_not_candidates(self) -> None:
        """The whole point: an empty answer, never a downgraded one."""
        store = _FakeStore(
            [
                _entry("c1", learning_status=LearningStatus.candidate),
                _entry("c2", learning_status=LearningStatus.candidate),
            ]
        )
        result = _recall(store)
        assert result["count"] == 0
        assert result["tool_paths"] == []

    def test_empty_result_is_not_an_error(self) -> None:
        """ "Nothing approved yet" is a normal answer, so callers need no special case."""
        assert "error" not in _recall(_FakeStore([]))


class TestLearningStatusAny:
    def test_any_includes_candidates(self) -> None:
        store = _FakeStore(
            [
                _entry("k1"),
                _entry("k2", learning_status=LearningStatus.candidate),
            ]
        )
        result = _recall(store, learning_status="any")
        assert result["count"] == 2

    def test_any_still_excludes_demoted(self) -> None:
        """Demoted means withdrawn — no opt-in brings it back."""
        store = _FakeStore([_entry("k1", learning_status=LearningStatus.demoted)])
        assert _recall(store, learning_status="any")["count"] == 0

    def test_unknown_learning_status_is_rejected(self) -> None:
        result = _recall(_FakeStore([_entry("k1")]), learning_status="promoted")
        assert result["error"] == "invalid_request"


class TestTagConvention:
    def test_tool_prefixed_tag_qualifies(self) -> None:
        store = _FakeStore([_entry("k1", tags=["tool:ruff"])])
        assert _recall(store)["count"] == 1

    def test_fleet_learning_tag_qualifies(self) -> None:
        assert _recall(_FakeStore([_entry("k1", tags=["fleet:learning"])]))["count"] == 1

    def test_untagged_approved_entry_is_not_a_tool_path(self) -> None:
        """Approval alone does not make a memory a tool path."""
        assert _recall(_FakeStore([_entry("k1", tags=[])]))["count"] == 0

    def test_unrelated_tags_do_not_qualify(self) -> None:
        assert _recall(_FakeStore([_entry("k1", tags=["architecture", "critical"])]))["count"] == 0


class TestFiltersAndLimits:
    def test_stale_entries_are_excluded(self) -> None:
        store = _FakeStore([_entry("k1", status=MemoryStatus.stale)])
        assert _recall(store)["count"] == 0

    def test_limit_caps_results(self) -> None:
        store = _FakeStore([_entry(f"k{i}") for i in range(10)])
        assert _recall(store, limit=3)["count"] == 3

    def test_default_limit_is_five(self) -> None:
        store = _FakeStore([_entry(f"k{i}") for i in range(10)])
        assert _recall(store)["count"] == 5

    @pytest.mark.parametrize("bad", [0, -1, 51])
    def test_limit_out_of_range_is_rejected(self, bad: int) -> None:
        assert _recall(_FakeStore([]), limit=bad)["error"] == "invalid_request"

    def test_min_confidence_filters(self) -> None:
        store = _FakeStore([_entry("low", confidence=0.3), _entry("high", confidence=0.9)])
        result = _recall(store, min_confidence=0.5)
        assert [p["key"] for p in result["tool_paths"]] == ["high"]  # type: ignore[index]

    def test_missing_task_type_is_rejected(self) -> None:
        result = memory_service.brain_recall_tool_paths(
            _FakeStore([]),  # type: ignore[arg-type]
            "proj",
            "agent",
            task_type="   ",
        )
        assert result["error"] == "invalid_request"

    def test_task_type_drives_the_search(self) -> None:
        store = _FakeStore([_entry("k1")])
        _recall(store, task_type="deploy-helm-chart")
        assert store.search_calls == ["deploy-helm-chart"]


class TestResponseShapeForAgentForge:
    def test_items_carry_the_fields_af_reads(self) -> None:
        """AF's candidates_from_tool_path_learnings reads these keys directly."""
        result = _recall(_FakeStore([_entry("k1", tags=["tool:ruff"])]))
        item = result["tool_paths"][0]  # type: ignore[index]
        assert item["value"] == "value for k1"
        assert item["tags"] == ["tool:ruff"]
        assert item["confidence"] == 0.8
        assert item["project_id"] == "proj"

    def test_promotion_provenance_is_surfaced(self) -> None:
        """A consumer auditing an injection needs to see what approved it."""
        item = _recall(_FakeStore([_entry("k1")]))["tool_paths"][0]  # type: ignore[index]
        assert item["promoted_by"] == "eval-run-1"
        assert item["promotion_signal"] == "eval"
