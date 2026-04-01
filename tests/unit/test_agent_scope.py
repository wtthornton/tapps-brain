"""Unit tests for ``agent_scope`` normalization (GitHub #52)."""

from __future__ import annotations

import pytest

from tapps_brain.agent_scope import (
    agent_scope_valid_values_for_errors,
    hive_group_name_from_scope,
    normalize_agent_scope,
)


def test_normalize_primitives_lowercased() -> None:
    assert normalize_agent_scope("PRIVATE") == "private"
    assert normalize_agent_scope("  domain  ") == "domain"
    assert normalize_agent_scope("Hive") == "hive"


def test_normalize_group_prefix() -> None:
    assert normalize_agent_scope("group:team-alpha") == "group:team-alpha"
    assert normalize_agent_scope("GROUP:  team-alpha  ") == "group:team-alpha"


def test_normalize_group_empty_name_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty group name"):
        normalize_agent_scope("group:")
    with pytest.raises(ValueError, match="non-empty group name"):
        normalize_agent_scope("group:   ")


def test_normalize_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid agent_scope"):
        normalize_agent_scope("public")
    with pytest.raises(ValueError, match="must not be empty"):
        normalize_agent_scope("")


def test_hive_group_name_from_scope() -> None:
    assert hive_group_name_from_scope("group:my-team") == "my-team"
    assert hive_group_name_from_scope("private") is None


def test_agent_scope_valid_values_for_errors() -> None:
    v = agent_scope_valid_values_for_errors()
    assert "group:<name>" in v
    assert "private" in v
