"""Unit tests for backend factory helpers and propagation utilities."""

from __future__ import annotations

from unittest.mock import MagicMock

from tapps_brain._store_query import _filter_memory_entries
from tapps_brain.backends import (
    _apply_hive_profile_scope_rules,
    _propagation_private_outcome,
    _resolve_propagation_namespace,
)
from tests.factories import make_entry


def test_apply_hive_profile_scope_rules_private_tier() -> None:
    scope, rule = _apply_hive_profile_scope_rules(
        "hive",
        "context",
        auto_propagate_tiers=None,
        private_tiers=["context"],
        bypass_profile_hive_rules=False,
    )
    assert scope == "private"
    assert rule == "private_tiers"


def test_apply_hive_profile_scope_rules_auto_propagate() -> None:
    scope, rule = _apply_hive_profile_scope_rules(
        "private",
        "architectural",
        auto_propagate_tiers=["architectural"],
        private_tiers=None,
        bypass_profile_hive_rules=False,
    )
    assert scope == "domain"
    assert rule == "auto_propagate_tiers"


def test_propagation_private_outcome_client_scope() -> None:
    outcome = _propagation_private_outcome(
        requested_scope="private",
        tier="context",
        key="k1",
        rule_applied=None,
    )
    assert outcome["decision"] == "refused_client_scope"
    assert outcome["propagated"] is False


def test_resolve_propagation_namespace_group_denied() -> None:
    hive = MagicMock()
    hive.agent_is_group_member.return_value = False
    namespace, refusal = _resolve_propagation_namespace(
        effective_scope="group:alpha",
        agent_profile="profile-a",
        agent_id="agent-1",
        key="k1",
        requested_scope="group:alpha",
        tier="context",
        hive_store=hive,
    )
    assert namespace is None
    assert refusal is not None
    assert refusal["decision"] == "refused_group_not_member"


def test_filter_memory_entries_by_tier_and_tags() -> None:
    entry = make_entry(key="a", tags=["alpha"], tier="pattern")
    other = make_entry(key="b", tags=["beta"], tier="context")
    filtered = _filter_memory_entries(
        [entry, other],
        tier="pattern",
        tags=["alpha"],
    )
    assert filtered == [entry]
