"""Tests for per-(project_id, agent_id) request counter cardinality cap.

TAP-599: ``_record_labeled_request`` must use O(1) membership checks via
``_DISTINCT_AGENTS_PER_PROJECT`` instead of an O(N) set-comprehension.

TAP-600: bounded project LRU — running 100k distinct project_ids must not
grow ``_LABELED_REQUEST_COUNTS`` past ``_MAX_PROJECT_CARDINALITY`` entries;
evictions are counted in ``_TENANT_LABELS_EVICTED_TOTAL``.

Coverage targets:
* Normal increment path.
* 100-agent cap: first 100 unique agent_ids stored as-is.
* 101st unique agent bucketed as ``"other"``.
* Already-known agent never bucketed to ``"other"`` even when at cap.
* ``"other"`` itself accumulates across overflow agents.
* ``_DISTINCT_AGENTS_PER_PROJECT`` stays in sync with ``_LABELED_REQUEST_COUNTS``.
* Projects are independent — overflow in project A does not affect project B.
* TAP-600: project LRU bounds the number of tracked projects.
* TAP-600: LRU eviction increments ``_TENANT_LABELS_EVICTED_TOTAL``.
* TAP-600: most-recently-used project survives when LRU eviction fires.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

import tapps_brain.http_adapter as _mod
from tapps_brain.http_adapter import (
    _LABELED_REQUEST_COUNTS_LOCK,
    _MAX_AGENT_ID_CARDINALITY,
    _record_labeled_request,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def _clean_state() -> Generator[None, None, None]:
    """Zero all counter state and restore on exit to prevent test-order effects."""
    with _LABELED_REQUEST_COUNTS_LOCK:
        prior_counts = dict(_mod._LABELED_REQUEST_COUNTS)
        prior_distinct = {k: set(v) for k, v in _mod._DISTINCT_AGENTS_PER_PROJECT.items()}
        prior_lru: OrderedDict[str, None] = OrderedDict(_mod._PROJECT_LRU)
        prior_evicted = _mod._TENANT_LABELS_EVICTED_TOTAL
        _mod._LABELED_REQUEST_COUNTS.clear()
        _mod._DISTINCT_AGENTS_PER_PROJECT.clear()
        _mod._PROJECT_LRU.clear()
        _mod._TENANT_LABELS_EVICTED_TOTAL = 0
    try:
        yield
    finally:
        with _LABELED_REQUEST_COUNTS_LOCK:
            _mod._LABELED_REQUEST_COUNTS.clear()
            _mod._LABELED_REQUEST_COUNTS.update(prior_counts)
            _mod._DISTINCT_AGENTS_PER_PROJECT.clear()
            _mod._DISTINCT_AGENTS_PER_PROJECT.update(prior_distinct)
            _mod._PROJECT_LRU.clear()
            _mod._PROJECT_LRU.update(prior_lru)
            _mod._TENANT_LABELS_EVICTED_TOTAL = prior_evicted


# ---------------------------------------------------------------------------
# Basic increment
# ---------------------------------------------------------------------------


class TestBasicIncrement:
    def test_single_call_increments_counter(self) -> None:
        with _clean_state():
            _record_labeled_request("proj-a", "agent-1")
            assert _mod._LABELED_REQUEST_COUNTS[("proj-a", "agent-1")] == 1

    def test_repeated_calls_accumulate(self) -> None:
        with _clean_state():
            for _ in range(5):
                _record_labeled_request("proj-b", "agent-x")
            assert _mod._LABELED_REQUEST_COUNTS[("proj-b", "agent-x")] == 5

    def test_distinct_agents_set_updated(self) -> None:
        with _clean_state():
            _record_labeled_request("proj-c", "agent-1")
            _record_labeled_request("proj-c", "agent-2")
            assert _mod._DISTINCT_AGENTS_PER_PROJECT["proj-c"] == {"agent-1", "agent-2"}


# ---------------------------------------------------------------------------
# Cardinality cap (AC12)
# ---------------------------------------------------------------------------


class TestCardinalityCap:
    def test_first_100_agents_stored_as_is(self) -> None:
        with _clean_state():
            for i in range(_MAX_AGENT_ID_CARDINALITY):
                _record_labeled_request("proj-cap", f"agent-{i}")

            distinct = _mod._DISTINCT_AGENTS_PER_PROJECT["proj-cap"]
            assert len(distinct) == _MAX_AGENT_ID_CARDINALITY
            assert "agent-0" in distinct
            assert f"agent-{_MAX_AGENT_ID_CARDINALITY - 1}" in distinct
            assert "other" not in distinct

    def test_101st_unique_agent_bucketed_as_other(self) -> None:
        with _clean_state():
            for i in range(_MAX_AGENT_ID_CARDINALITY):
                _record_labeled_request("proj-overflow", f"agent-{i}")

            _record_labeled_request("proj-overflow", "overflow-agent")

            assert ("proj-overflow", "overflow-agent") not in _mod._LABELED_REQUEST_COUNTS
            assert _mod._LABELED_REQUEST_COUNTS.get(("proj-overflow", "other"), 0) == 1

    def test_multiple_overflow_agents_accumulate_under_other(self) -> None:
        with _clean_state():
            for i in range(_MAX_AGENT_ID_CARDINALITY):
                _record_labeled_request("proj-multi-overflow", f"agent-{i}")

            for j in range(5):
                _record_labeled_request("proj-multi-overflow", f"overflow-{j}")

            assert _mod._LABELED_REQUEST_COUNTS[("proj-multi-overflow", "other")] == 5


# ---------------------------------------------------------------------------
# Overflow-to-"other" path (AC13)
# ---------------------------------------------------------------------------


class TestOverflowToOther:
    def test_known_agent_at_cap_not_bucketed(self) -> None:
        """An agent already in the registry is never re-bucketed to 'other'."""
        with _clean_state():
            # Fill to cap - 1, then add known-agent
            for i in range(_MAX_AGENT_ID_CARDINALITY - 1):
                _record_labeled_request("proj-known", f"a-{i}")
            _record_labeled_request("proj-known", "known-agent")
            # Now exactly at cap; known-agent must not be bucketed
            _record_labeled_request("proj-known", "known-agent")

            key = ("proj-known", "known-agent")
            assert _mod._LABELED_REQUEST_COUNTS[key] == 2
            assert ("proj-known", "other") not in _mod._LABELED_REQUEST_COUNTS

    def test_other_itself_counts_when_already_in_registry(self) -> None:
        """If 'other' was already registered normally it increments without double-counting."""
        with _clean_state():
            # 'other' is a legitimate agent_id up to the cap
            _record_labeled_request("proj-legit-other", "other")
            _record_labeled_request("proj-legit-other", "other")
            assert _mod._LABELED_REQUEST_COUNTS[("proj-legit-other", "other")] == 2


# ---------------------------------------------------------------------------
# Project isolation
# ---------------------------------------------------------------------------


class TestProjectIsolation:
    def test_overflow_in_one_project_does_not_affect_another(self) -> None:
        with _clean_state():
            for i in range(_MAX_AGENT_ID_CARDINALITY):
                _record_labeled_request("proj-full", f"agent-{i}")

            # proj-empty has its own independent cap
            _record_labeled_request("proj-empty", "agent-new")

            assert ("proj-empty", "other") not in _mod._LABELED_REQUEST_COUNTS
            assert _mod._LABELED_REQUEST_COUNTS[("proj-empty", "agent-new")] == 1

    def test_distinct_sets_per_project_are_independent(self) -> None:
        with _clean_state():
            _record_labeled_request("proj-x", "shared-agent")
            _record_labeled_request("proj-y", "shared-agent")

            assert "shared-agent" in _mod._DISTINCT_AGENTS_PER_PROJECT["proj-x"]
            assert "shared-agent" in _mod._DISTINCT_AGENTS_PER_PROJECT["proj-y"]
            # Modifying one project's set should not affect the other
            _mod._DISTINCT_AGENTS_PER_PROJECT["proj-x"].discard("shared-agent")
            assert "shared-agent" in _mod._DISTINCT_AGENTS_PER_PROJECT["proj-y"]


# ---------------------------------------------------------------------------
# State consistency invariant
# ---------------------------------------------------------------------------


class TestStateConsistency:
    def test_distinct_set_is_superset_of_counter_keys(self) -> None:
        """Every agent_id in _LABELED_REQUEST_COUNTS also appears in _DISTINCT_AGENTS_PER_PROJECT."""
        with _clean_state():
            agents = ["alpha", "beta", "gamma"]
            for aid in agents:
                _record_labeled_request("proj-invar", aid)

            distinct = _mod._DISTINCT_AGENTS_PER_PROJECT.get("proj-invar", set())
            for aid in agents:
                assert aid in distinct

    def test_cardinality_matches_distinct_set_size(self) -> None:
        with _clean_state():
            for i in range(10):
                _record_labeled_request("proj-size", f"agent-{i}")

            distinct = _mod._DISTINCT_AGENTS_PER_PROJECT.get("proj-size", set())
            assert len(distinct) == 10


# ---------------------------------------------------------------------------
# TAP-600: bounded project-LRU cardinality
# ---------------------------------------------------------------------------


class TestProjectLRUBound:
    """Verify that _LABELED_REQUEST_COUNTS never exceeds the project cardinality cap."""

    def test_100k_projects_do_not_exceed_cap(self) -> None:
        """Running 100k distinct project_ids keeps the dict within the cap."""
        cap = 50  # use a small cap for speed
        with _clean_state():
            with patch.object(_mod, "_MAX_PROJECT_CARDINALITY", cap):
                for i in range(100_000):
                    _record_labeled_request(f"proj-{i}", "agent-1")

            # Number of distinct project_ids tracked must not exceed cap.
            tracked_projects = {k[0] for k in _mod._LABELED_REQUEST_COUNTS}
            assert len(tracked_projects) <= cap

    def test_eviction_increments_evicted_total(self) -> None:
        """Each project eviction increments _TENANT_LABELS_EVICTED_TOTAL."""
        cap = 3
        with _clean_state():
            with patch.object(_mod, "_MAX_PROJECT_CARDINALITY", cap):
                # Fill to cap without triggering eviction.
                for i in range(cap):
                    _record_labeled_request(f"proj-{i}", "agent-x")
                assert _mod._TENANT_LABELS_EVICTED_TOTAL == 0

                # One more project forces one eviction.
                _record_labeled_request("proj-overflow", "agent-x")
                assert _mod._TENANT_LABELS_EVICTED_TOTAL == 1

                # Three more overflow → three more evictions.
                for j in range(3):
                    _record_labeled_request(f"proj-extra-{j}", "agent-x")
                assert _mod._TENANT_LABELS_EVICTED_TOTAL == 4

    def test_lru_order_most_recently_used_survives(self) -> None:
        """The MRU project must survive eviction; the LRU project is evicted."""
        cap = 2
        with _clean_state():
            with patch.object(_mod, "_MAX_PROJECT_CARDINALITY", cap):
                _record_labeled_request("proj-old", "agent-1")   # LRU
                _record_labeled_request("proj-new", "agent-1")   # MRU

                # Re-touch proj-old to make it MRU; proj-new becomes LRU.
                _record_labeled_request("proj-old", "agent-2")

                # Adding a brand-new project evicts the LRU (proj-new).
                _record_labeled_request("proj-third", "agent-1")

                tracked = {k[0] for k in _mod._LABELED_REQUEST_COUNTS}
                assert "proj-new" not in tracked, "LRU project was not evicted"
                assert "proj-old" in tracked, "MRU project must survive"
                assert "proj-third" in tracked

    def test_evicted_project_entries_removed_from_distinct(self) -> None:
        """Evicted project must also be removed from _DISTINCT_AGENTS_PER_PROJECT."""
        cap = 1
        with _clean_state():
            with patch.object(_mod, "_MAX_PROJECT_CARDINALITY", cap):
                _record_labeled_request("proj-evict-me", "agent-1")
                assert "proj-evict-me" in _mod._DISTINCT_AGENTS_PER_PROJECT

                _record_labeled_request("proj-new", "agent-1")
                assert "proj-evict-me" not in _mod._DISTINCT_AGENTS_PER_PROJECT

    def test_repeated_access_same_project_no_eviction(self) -> None:
        """A single project accessed many times never triggers eviction."""
        cap = 5
        with _clean_state():
            with patch.object(_mod, "_MAX_PROJECT_CARDINALITY", cap):
                for _ in range(10_000):
                    _record_labeled_request("proj-stable", "agent-1")
                assert _mod._TENANT_LABELS_EVICTED_TOTAL == 0
                assert len({k[0] for k in _mod._LABELED_REQUEST_COUNTS}) == 1


class TestEvictionMetricExposed:
    """_collect_metrics must include tapps_brain_tenant_labels_evicted_total."""

    def test_eviction_counter_in_metrics_output(self) -> None:
        from tapps_brain.http_adapter import _collect_metrics

        with _clean_state():
            with patch.object(_mod, "_TENANT_LABELS_EVICTED_TOTAL", 42):
                output = _collect_metrics(dsn=None)

        assert "tapps_brain_tenant_labels_evicted_total" in output
        assert "42" in output

    def test_eviction_counter_zero_still_emitted(self) -> None:
        """Counter must always be emitted (even at zero) for stable Prometheus series."""
        from tapps_brain.http_adapter import _collect_metrics

        with _clean_state():
            output = _collect_metrics(dsn=None)

        assert "tapps_brain_tenant_labels_evicted_total 0" in output
