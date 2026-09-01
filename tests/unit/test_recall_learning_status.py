"""``brain_recall`` must filter and report promotion state (TAP-6826).

``learning_status`` was already on :class:`~tapps_brain.models.MemoryEntry` and
already selected by the recall SQL, but it reached neither the argument list nor
the wire — so a consumer could not ask for promoted learnings and could not tell
a promoted one from a candidate in what it got back.  AgentForge's
``## Learned pitfalls`` block was consequently assembled from any
keyword-relevant row: feeding it *only unpromoted candidates* produced a block
byte-identical to the all-rows block.

Two properties are load-bearing here and both are asserted, not assumed:

* **The filter runs in SQL, before the top-K cut.**  ``_SEARCH_ORDER_LIMIT_SQL``
  caps the query at 100 rows by rank and ``brain_recall`` then cuts to
  ``max_results``; a Python post-filter after either cut returns fewer rows than
  asked for whenever unpromoted rows outrank promoted ones, which is
  indistinguishable from "no promoted learnings exist" — the very bug this
  filter exists to end.  ``TestMaxResultsInteraction`` builds exactly that
  ranking inversion.
* **Absence of the argument changes nothing.**  ``TestFilterIsAdditive``
  compares the generated SQL against the shipped constants byte for byte and
  asserts the store is called with the exact keyword set it was called with
  before.

The discriminating control the defect report demanded is
``TestDiscriminatingControl``: against a fixture holding both promoted and
unpromoted rows the filtered recall must return *strictly fewer* results than
the same unfiltered recall.  A filter that cannot make the counts differ is not
filtering, and a green test over it proves nothing.

Real-Postgres execution of the same SQL lives in
``tests/test_recall_learning_status_pg.py``; this module is DB-free.
"""

from __future__ import annotations

from typing import Any

import pytest

from tapps_brain import _postgres_private_sql as _sql
from tapps_brain.models import LearningStatus, MemoryEntry
from tapps_brain.services import memory_service

_BASE_SEARCH_KWARGS: dict[str, Any] = {
    "memory_group": None,
    "since": None,
    "until": None,
    "time_field": "created_at",
    "memory_class": None,
    "as_of": None,
}


def _entry(key: str, status: LearningStatus, **kwargs: Any) -> MemoryEntry:
    return MemoryEntry(
        key=key,
        value=f"{key}: kingfisher telemetry pitfall",
        tier="pattern",
        learning_status=status,
        **kwargs,
    )


class _SqlLikeStore:
    """Store double whose ``search`` honours ``learning_status`` the way SQL does.

    The filter is applied to the *candidate pool*, before anything is ranked or
    truncated — that is the whole point of pushing it into the WHERE clause, and
    a double that post-filtered instead would quietly pass the very tests that
    exist to catch a post-filter.  ``calls`` records the keyword set each call
    received so the additive-only property is checkable.
    """

    def __init__(self, entries: list[MemoryEntry]) -> None:
        self._entries = list(entries)
        self.calls: list[dict[str, Any]] = []

    def search(self, query: str, **kwargs: Any) -> list[MemoryEntry]:
        self.calls.append(dict(kwargs))
        wanted = kwargs.get("learning_status")
        if not wanted:
            return list(self._entries)
        return [e for e in self._entries if str(e.learning_status) in set(wanted)]


class TestArgumentShape:
    """The argument is list-shaped; a bare string is a one-element list."""

    def test_enum_carries_exactly_three_values(self) -> None:
        """Pin the enum the argument is validated against (docs quote it)."""
        assert {s.value for s in LearningStatus} == {"candidate", "approved", "demoted"}

    def test_every_enum_member_is_accepted(self) -> None:
        """Tracks the enum rather than a hand-copied list, so widening it here
        cannot leave the filter silently rejecting a legal status."""
        for status in LearningStatus:
            assert memory_service.normalize_learning_status_filter(status.value) == [status.value]

    def test_bare_string_normalises_to_a_single_element_list(self) -> None:
        assert memory_service.normalize_learning_status_filter("approved") == ["approved"]

    def test_list_is_preserved_in_caller_order(self) -> None:
        assert memory_service.normalize_learning_status_filter(["demoted", "approved"]) == [
            "demoted",
            "approved",
        ]

    def test_repeats_are_collapsed(self) -> None:
        assert memory_service.normalize_learning_status_filter(
            ["approved", "approved", "candidate"]
        ) == ["approved", "candidate"]

    @pytest.mark.parametrize("empty", [None, "", [], ["", "  "]])
    def test_empty_forms_mean_unfiltered(self, empty: Any) -> None:
        assert memory_service.normalize_learning_status_filter(empty) is None

    def test_unknown_status_raises_rather_than_returning_nothing(self) -> None:
        """A typo must not degrade to an empty result set.

        Silently matching zero rows is indistinguishable from "nothing is
        promoted" — the exact ambiguity TAP-6826 exists to remove.
        """
        with pytest.raises(ValueError, match="unknown value"):
            memory_service.normalize_learning_status_filter(["aproved"])


class TestFilterIsAdditive:
    """No filter in, no change out — asserted on the SQL and on the store call."""

    @pytest.mark.parametrize("empty", [None, []])
    def test_search_sql_is_unchanged_without_learning_status(self, empty: Any) -> None:
        baseline, baseline_params = _sql.build_search_sql(**_BASE_SEARCH_KWARGS)
        widened, params = _sql.build_search_sql(**_BASE_SEARCH_KWARGS, learning_status=empty)
        assert widened == baseline
        assert params == baseline_params
        assert "learning_status = ANY" not in baseline

    @pytest.mark.parametrize("empty", [None, []])
    def test_knn_sql_is_unchanged_without_learning_status(self, empty: Any) -> None:
        baseline, baseline_params = _sql.build_knn_search_sql()
        widened, params = _sql.build_knn_search_sql(learning_status=empty)
        assert widened == baseline
        assert params == baseline_params
        assert "learning_status = ANY" not in baseline

    def test_unfiltered_recall_passes_no_learning_status_kwarg(self) -> None:
        """An existing caller must issue the exact call it issued before.

        Passing ``learning_status=None`` explicitly would break every backend
        and test double whose signature predates TAP-6826, so the kwarg is
        omitted entirely rather than defaulted.
        """
        store = _SqlLikeStore([_entry("a", LearningStatus.approved)])
        memory_service.brain_recall(store, "proj", "agent", query="kingfisher")
        assert "learning_status" not in store.calls[0]

    def test_filtered_recall_forwards_the_normalised_list(self) -> None:
        store = _SqlLikeStore([_entry("a", LearningStatus.approved)])
        memory_service.brain_recall(
            store, "proj", "agent", query="kingfisher", filter_learning_status="approved"
        )
        assert store.calls[0]["learning_status"] == ["approved"]


class TestSqlClauseShape:
    """The clause itself: parameterised, set-valued, and indexable."""

    def test_clause_is_appended_with_a_single_array_bind(self) -> None:
        sql, params = _sql.build_search_sql(
            **_BASE_SEARCH_KWARGS, learning_status=["approved", "candidate"]
        )
        assert " AND learning_status = ANY(%s::text[])" in sql
        assert params == [["approved", "candidate"]]

    def test_status_values_are_bound_never_interpolated(self) -> None:
        sql, _ = _sql.build_search_sql(**_BASE_SEARCH_KWARGS, learning_status=["approved"])
        assert "approved" not in sql

    def test_knn_clause_bind_leads_mid_params(self) -> None:
        """Bind order must track clause order or the KNN query mis-binds."""
        sql, mid_params = _sql.build_knn_search_sql(
            learning_status=["approved"], as_of="2026-01-01T00:00:00+00:00"
        )
        assert sql.index("learning_status = ANY") < sql.index("valid_at IS NULL")
        assert mid_params[0] == ["approved"]
        assert mid_params[1:] == ["2026-01-01T00:00:00+00:00"] * 4

    def test_clause_composes_with_the_live_row_predicate(self) -> None:
        sql, _ = _sql.build_search_sql(**_BASE_SEARCH_KWARGS, learning_status=["approved"])
        assert "status = 'active'" in sql
        assert sql.rstrip().endswith("ORDER BY _rank DESC LIMIT 100")


class TestDiscriminatingControl:
    """Filtered must return strictly fewer rows than unfiltered — or it is inert."""

    @staticmethod
    def _mixed_store() -> _SqlLikeStore:
        return _SqlLikeStore(
            [
                *(_entry(f"cand-{i}", LearningStatus.candidate) for i in range(5)),
                *(_entry(f"appr-{i}", LearningStatus.approved) for i in range(3)),
                _entry("demo-0", LearningStatus.demoted),
            ]
        )

    def test_filtered_recall_returns_strictly_fewer_than_unfiltered(self) -> None:
        store = self._mixed_store()
        unfiltered = memory_service.brain_recall(
            store, "proj", "agent", query="kingfisher", max_results=50
        )
        filtered = memory_service.brain_recall(
            store,
            "proj",
            "agent",
            query="kingfisher",
            max_results=50,
            filter_learning_status=["approved"],
        )
        assert len(unfiltered) == 9
        assert len(filtered) == 3
        assert len(filtered) < len(unfiltered)
        assert {item["key"] for item in filtered} == {"appr-0", "appr-1", "appr-2"}

    def test_a_multi_status_filter_widens_the_set_it_returns(self) -> None:
        """The list shape has to actually buy something over a single status."""
        store = self._mixed_store()
        one = memory_service.brain_recall(
            store,
            "proj",
            "agent",
            query="kingfisher",
            max_results=50,
            filter_learning_status=["approved"],
        )
        two = memory_service.brain_recall(
            store,
            "proj",
            "agent",
            query="kingfisher",
            max_results=50,
            filter_learning_status=["approved", "demoted"],
        )
        assert len(one) == 3
        assert len(two) == 4


class TestMaxResultsInteraction:
    """The bug a Python post-filter would introduce, built deliberately."""

    @staticmethod
    def _inverted_ranking_store() -> _SqlLikeStore:
        """Six candidates that outrank three approved learnings on every signal.

        ``brain_recall`` scores by rank position + confidence + recency +
        frequency, so high-confidence, heavily-accessed candidates sit above
        low-confidence approved rows in the unfiltered ordering.
        """
        return _SqlLikeStore(
            [
                *(
                    _entry(f"cand-{i}", LearningStatus.candidate, confidence=0.99, access_count=100)
                    for i in range(6)
                ),
                *(
                    _entry(f"appr-{i}", LearningStatus.approved, confidence=0.10, access_count=0)
                    for i in range(3)
                ),
            ]
        )

    def test_control_unpromoted_rows_win_the_unfiltered_cut(self) -> None:
        """Without this control the next test proves nothing about ranking."""
        results = memory_service.brain_recall(
            self._inverted_ranking_store(), "proj", "agent", query="kingfisher", max_results=3
        )
        assert [item["key"] for item in results] == ["cand-0", "cand-1", "cand-2"]
        assert all(item["learning_status"] == "candidate" for item in results)

    def test_filter_still_returns_the_requested_count(self) -> None:
        """A post-filter applied after the ``max_results`` cut would return 0."""
        results = memory_service.brain_recall(
            self._inverted_ranking_store(),
            "proj",
            "agent",
            query="kingfisher",
            max_results=3,
            filter_learning_status=["approved"],
        )
        assert len(results) == 3
        assert all(item["learning_status"] == "approved" for item in results)


class TestStatusIsReported:
    """Promotion state reaches the wire whether or not the caller filtered."""

    def test_every_unfiltered_result_carries_learning_status(self) -> None:
        store = _SqlLikeStore(
            [
                _entry("a", LearningStatus.candidate),
                _entry("b", LearningStatus.approved),
                _entry("c", LearningStatus.demoted),
            ]
        )
        results = memory_service.brain_recall(
            store, "proj", "agent", query="kingfisher", max_results=10
        )
        assert {item["key"]: item["learning_status"] for item in results} == {
            "a": "candidate",
            "b": "approved",
            "c": "demoted",
        }

    def test_learning_status_is_the_only_added_key(self) -> None:
        """Backwards compatibility: the pre-TAP-6826 payload is untouched.

        Everything an existing consumer already reads keeps its exact value;
        the single new key is the deliverable, and it is additive.
        """
        store = _SqlLikeStore([_entry("a", LearningStatus.approved, confidence=0.42)])
        (item,) = memory_service.brain_recall(store, "proj", "agent", query="kingfisher")
        assert set(item) - {"learning_status"} == {
            "key",
            "value",
            "tier",
            "confidence",
            "tags",
            "score",
        }
        assert item["confidence"] == 0.42
        assert item["tier"] == "pattern"

    def test_dict_shaped_legacy_rows_are_left_alone(self) -> None:
        """The plain-dict search path has no entry object to read status from."""
        store = _SqlLikeStore([])
        store._entries = [{"key": "legacy", "value": "v", "confidence": 0.5}]  # type: ignore[list-item]
        (item,) = memory_service.brain_recall(store, "proj", "agent", query="kingfisher")
        assert "learning_status" not in item


class _CapturingMcp:
    """Collects the functions ``register_brain_tools`` decorates."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self):  # mirrors FastMCP's decorator factory
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return _decorator


class TestMcpToolSurface:
    """The MCP tool named in the issue must accept the filter too."""

    @staticmethod
    def _brain_recall_tool(store: _SqlLikeStore):
        from types import SimpleNamespace

        from tapps_brain.mcp_server.tools_brain import register_brain_tools

        mcp = _CapturingMcp()
        register_brain_tools(
            mcp,
            SimpleNamespace(
                server_agent_id="agent",
                resolve_store_for_call=lambda _aid: store,
                pid=lambda: "proj",
                resolve_per_call_agent_id=lambda aid, default="": aid or default,
            ),
        )
        return mcp.tools["brain_recall"]

    def test_comma_separated_statuses_become_a_list(self) -> None:
        store = _SqlLikeStore([_entry("a", LearningStatus.approved)])
        tool = self._brain_recall_tool(store)
        tool(query="kingfisher", filter_learning_status="approved, demoted")
        assert store.calls[0]["learning_status"] == ["approved", "demoted"]

    def test_default_leaves_the_call_unfiltered(self) -> None:
        store = _SqlLikeStore([_entry("a", LearningStatus.approved)])
        tool = self._brain_recall_tool(store)
        tool(query="kingfisher")
        assert "learning_status" not in store.calls[0]
