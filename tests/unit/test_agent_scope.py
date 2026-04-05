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


# ---------------------------------------------------------------------------
# Edge-case / fuzz tests for group: normalization (STORY-045.3)
# ---------------------------------------------------------------------------


class TestGroupNormalizationEdgeCases:
    """Edge-case tests for ``group:`` scope normalization."""

    def test_empty_group_name_rejected(self) -> None:
        """``group:`` with nothing after the colon is rejected."""
        with pytest.raises(ValueError, match="non-empty group name"):
            normalize_agent_scope("group:")

    def test_whitespace_only_group_name_rejected(self) -> None:
        """``group:`` followed by only whitespace is rejected."""
        with pytest.raises(ValueError, match="non-empty group name"):
            normalize_agent_scope("group:   ")
        with pytest.raises(ValueError, match="non-empty group name"):
            normalize_agent_scope("group:\t")

    def test_very_long_group_name_rejected(self) -> None:
        """Group names exceeding 64 characters are rejected via memory_group rules."""
        long_name = "a" * 65
        with pytest.raises(ValueError, match="exceeds max length"):
            normalize_agent_scope(f"group:{long_name}")

    def test_long_group_name_at_boundary_accepted(self) -> None:
        """Group names exactly at 64 characters are accepted."""
        name_64 = "b" * 64
        result = normalize_agent_scope(f"group:{name_64}")
        assert result == f"group:{name_64}"

    def test_group_with_special_characters(self) -> None:
        """Groups with printable special characters are accepted."""
        assert normalize_agent_scope("group:team-alpha") == "group:team-alpha"
        assert normalize_agent_scope("group:team_beta") == "group:team_beta"
        assert normalize_agent_scope("group:team.gamma") == "group:team.gamma"
        assert normalize_agent_scope("group:team/delta") == "group:team/delta"
        assert normalize_agent_scope("group:team@org") == "group:team@org"

    def test_group_with_control_characters_rejected(self) -> None:
        """Groups with ASCII control characters are rejected."""
        with pytest.raises(ValueError, match="control characters"):
            normalize_agent_scope("group:team\x00alpha")
        with pytest.raises(ValueError, match="control characters"):
            normalize_agent_scope("group:team\x1falpha")

    def test_case_normalization_prefix(self) -> None:
        """The ``group:`` prefix is case-insensitive; group name preserves case."""
        assert normalize_agent_scope("Group:FOO") == "group:FOO"
        assert normalize_agent_scope("GROUP:Bar") == "group:Bar"
        assert normalize_agent_scope("gRoUp:baz") == "group:baz"

    def test_group_name_with_surrounding_whitespace_trimmed(self) -> None:
        """Whitespace around the group name is stripped."""
        assert normalize_agent_scope("group:  my-team  ") == "group:my-team"
        assert normalize_agent_scope("GROUP:  my-team  ") == "group:my-team"

    def test_group_name_with_internal_spaces(self) -> None:
        """Group names with internal spaces are preserved (printable chars)."""
        assert normalize_agent_scope("group:my team") == "group:my team"

    def test_group_name_unicode(self) -> None:
        """Unicode group names above ASCII 32 are accepted."""
        assert normalize_agent_scope("group:equipe-\u00e9") == "group:equipe-\u00e9"

    def test_group_colon_only_whitespace_variations(self) -> None:
        """Various whitespace-only forms after group: are all rejected."""
        for ws in ["group: ", "group:  ", "group:\t\t", "group: \t "]:
            with pytest.raises(ValueError, match="non-empty group name"):
                normalize_agent_scope(ws)
