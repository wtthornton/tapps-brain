"""Unit tests for ProfileRegistry — EPIC-073 STORY-073.1.

Tests cover:
- Loading profiles from the bundled default YAML.
- ProfileRegistry.get() happy path and UnknownProfileError.
- ProfileRegistry.profiles property.
- Drift detection via validate_against().
- Custom config_path loading.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tapps_brain.mcp_server.profile_registry import ProfileRegistry, UnknownProfileError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(yaml_text: str, tmp_path: Path) -> ProfileRegistry:
    """Write *yaml_text* to a temp file and return a ProfileRegistry from it."""
    cfg = tmp_path / "profiles.yaml"
    cfg.write_text(yaml_text, encoding="utf-8")
    return ProfileRegistry(config_path=cfg)


# ---------------------------------------------------------------------------
# Bundled default YAML
# ---------------------------------------------------------------------------


class TestBundledProfiles:
    """Tests against the bundled mcp_profiles.yaml (no custom path)."""

    def test_profiles_property_lists_known_names(self) -> None:
        reg = ProfileRegistry()
        names = reg.profiles
        assert "full" in names
        assert "operator" in names
        assert "coder" in names
        assert "reviewer" in names
        assert "seeder" in names
        # Sorted
        assert names == sorted(names)

    def test_get_full_returns_55_tools(self) -> None:
        reg = ProfileRegistry()
        tools = reg.get("full")
        assert len(tools) == 55
        # Spot-check key tools
        assert "brain_recall" in tools
        assert "brain_remember" in tools
        assert "memory_save" in tools
        assert "tapps_brain_session_end" in tools
        # Operator-only tools must NOT be in full
        assert "maintenance_consolidate" not in tools
        assert "tapps_brain_health" not in tools
        assert "memory_export" not in tools
        assert "flywheel_evaluate" not in tools

    def test_get_operator_returns_68_tools(self) -> None:
        reg = ProfileRegistry()
        tools = reg.get("operator")
        assert len(tools) == 68
        # Operator-only tools must be present
        assert "maintenance_consolidate" in tools
        assert "tapps_brain_health" in tools
        assert "memory_export" in tools
        assert "flywheel_evaluate" in tools
        assert "flywheel_hive_feedback" in tools
        # Must also contain all full tools
        assert reg.get("full").issubset(tools)

    def test_get_coder_contains_facade_tools(self) -> None:
        reg = ProfileRegistry()
        coder = reg.get("coder")
        # Facade (6)
        for tool in (
            "brain_recall",
            "brain_remember",
            "brain_forget",
            "brain_learn_success",
            "brain_learn_failure",
            "brain_status",
        ):
            assert tool in coder, f"Expected coder profile to contain {tool!r}"

    def test_get_coder_contains_hook_callable_tools(self) -> None:
        reg = ProfileRegistry()
        coder = reg.get("coder")
        for tool in (
            "memory_index_session",
            "memory_capture",
            "tapps_brain_session_end",
            "memory_search_sessions",
        ):
            assert tool in coder, f"Expected coder profile to contain {tool!r}"

    def test_get_coder_contains_quality_loop_tools(self) -> None:
        reg = ProfileRegistry()
        coder = reg.get("coder")
        for tool in ("memory_reinforce", "feedback_rate", "feedback_gap"):
            assert tool in coder

    def test_get_coder_contains_cross_repo_tools(self) -> None:
        reg = ProfileRegistry()
        coder = reg.get("coder")
        assert "hive_search" in coder
        assert "memory_find_related" in coder

    def test_get_coder_excludes_destructive_ops(self) -> None:
        reg = ProfileRegistry()
        coder = reg.get("coder")
        assert "memory_delete" not in coder
        assert "agent_delete" not in coder
        assert "maintenance_gc" not in coder

    def test_get_coder_is_subset_of_full(self) -> None:
        reg = ProfileRegistry()
        assert reg.get("coder").issubset(reg.get("full"))

    def test_get_reviewer_is_read_only(self) -> None:
        reg = ProfileRegistry()
        reviewer = reg.get("reviewer")
        assert len(reviewer) == 8
        assert "brain_recall" in reviewer
        assert "memory_search" in reviewer
        assert "memory_get" in reviewer
        assert "memory_list" in reviewer
        assert "memory_search_sessions" in reviewer
        assert "hive_search" in reviewer
        assert "memory_relations" in reviewer
        assert "memory_find_related" in reviewer
        # No writes
        assert "memory_save" not in reviewer
        assert "memory_delete" not in reviewer

    def test_get_reviewer_is_subset_of_full(self) -> None:
        reg = ProfileRegistry()
        assert reg.get("reviewer").issubset(reg.get("full"))

    def test_get_seeder_contains_bulk_write_tools(self) -> None:
        reg = ProfileRegistry()
        seeder = reg.get("seeder")
        assert len(seeder) == 6
        for tool in (
            "brain_status",
            "memory_capture",
            "memory_ingest",
            "memory_save",
            "memory_save_many",
            "memory_supersede",
        ):
            assert tool in seeder, f"Expected seeder profile to contain {tool!r}"

    def test_get_seeder_is_subset_of_full(self) -> None:
        reg = ProfileRegistry()
        assert reg.get("seeder").issubset(reg.get("full"))

    def test_get_returns_frozenset(self) -> None:
        reg = ProfileRegistry()
        assert isinstance(reg.get("full"), frozenset)
        assert isinstance(reg.get("coder"), frozenset)


# ---------------------------------------------------------------------------
# UnknownProfileError
# ---------------------------------------------------------------------------


class TestUnknownProfileError:
    def test_raises_unknown_profile_error_for_missing_name(self) -> None:
        reg = ProfileRegistry()
        with pytest.raises(UnknownProfileError) as exc_info:
            reg.get("nonexistent_profile")
        err = exc_info.value
        assert err.name == "nonexistent_profile"
        assert "coder" in err.available
        assert "full" in err.available

    def test_unknown_profile_error_is_key_error(self) -> None:
        """UnknownProfileError must subclass KeyError for dict-like semantics."""
        reg = ProfileRegistry()
        with pytest.raises(KeyError):
            reg.get("does_not_exist")

    def test_unknown_profile_error_message_contains_name(self) -> None:
        reg = ProfileRegistry()
        with pytest.raises(UnknownProfileError) as exc_info:
            reg.get("bad_profile")
        assert "bad_profile" in str(exc_info.value)


# ---------------------------------------------------------------------------
# validate_against — drift detection
# ---------------------------------------------------------------------------


class TestValidateAgainst:
    def test_passes_with_superset(self) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              simple:
                tools:
                  - tool_a
                  - tool_b
        """)
        reg = ProfileRegistry.__new__(ProfileRegistry)
        import yaml

        data = yaml.safe_load(yaml_text)
        reg._profiles = {"simple": frozenset(["tool_a", "tool_b"])}
        # Does not raise when known_tools is a superset
        reg.validate_against(frozenset(["tool_a", "tool_b", "tool_c"]))

    def test_passes_with_exact_match(self) -> None:
        reg = ProfileRegistry.__new__(ProfileRegistry)
        reg._profiles = {"simple": frozenset(["tool_x"])}
        reg.validate_against(frozenset(["tool_x"]))  # exact match — OK

    def test_raises_on_unknown_tool(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              test_profile:
                tools:
                  - real_tool
                  - ghost_tool
        """)
        reg = _make_registry(yaml_text, tmp_path)
        with pytest.raises(ValueError) as exc_info:
            reg.validate_against(frozenset(["real_tool"]))
        msg = str(exc_info.value)
        assert "ghost_tool" in msg
        assert "test_profile" in msg

    def test_raises_lists_all_offending_profiles(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              profile_a:
                tools:
                  - good_tool
                  - bad_tool_1
              profile_b:
                tools:
                  - good_tool
                  - bad_tool_2
        """)
        reg = _make_registry(yaml_text, tmp_path)
        with pytest.raises(ValueError) as exc_info:
            reg.validate_against(frozenset(["good_tool"]))
        msg = str(exc_info.value)
        assert "bad_tool_1" in msg
        assert "bad_tool_2" in msg
        assert "profile_a" in msg
        assert "profile_b" in msg

    def test_bundled_profiles_validate_against_all_tools(self) -> None:
        """All bundled profiles must pass validation against the full 68-tool set."""
        import re

        content = Path("src/tapps_brain/mcp_server/__init__.py").read_text()
        pattern = r"@mcp\.tool\(\)[^\n]*\n\s+(?:async )?def ([a-z_]+)\("
        all_tools = frozenset(re.findall(pattern, content))
        assert len(all_tools) == 68, f"Expected 68 tools, found {len(all_tools)}"

        reg = ProfileRegistry()
        # Should not raise
        reg.validate_against(all_tools)


# ---------------------------------------------------------------------------
# Custom config_path
# ---------------------------------------------------------------------------


class TestCustomConfigPath:
    def test_loads_from_custom_path(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              custom_profile:
                description: "Custom test profile"
                tools:
                  - tool_one
                  - tool_two
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert "custom_profile" in reg.profiles
        assert reg.get("custom_profile") == frozenset(["tool_one", "tool_two"])

    def test_empty_tools_list_gives_empty_frozenset(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              empty_profile:
                tools: []
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert reg.get("empty_profile") == frozenset()

    def test_profiles_not_in_yaml_raises(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              only_one:
                tools:
                  - tool_a
        """)
        reg = _make_registry(yaml_text, tmp_path)
        with pytest.raises(UnknownProfileError):
            reg.get("coder")

    def test_profiles_property_sorted(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              zzz:
                tools: []
              aaa:
                tools: []
              mmm:
                tools: []
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert reg.profiles == ["aaa", "mmm", "zzz"]
