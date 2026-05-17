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

    def test_get_full_returns_60_tools(self) -> None:
        # TAP-1973 added `brain_record_events_batch` to the full profile.
        reg = ProfileRegistry()
        tools = reg.get("full")
        assert len(tools) == 60
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

    def test_get_operator_returns_73_tools(self) -> None:
        # TAP-1973 added `brain_record_events_batch` to the full profile,
        # which is a subset of operator.
        reg = ProfileRegistry()
        tools = reg.get("operator")
        assert len(tools) == 73
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

    def test_get_coder_contains_kg_discovery_tools(self) -> None:
        """TAP-2006: KG discovery primitives must be on the coder profile."""
        reg = ProfileRegistry()
        coder = reg.get("coder")
        # Both multi-hop primitives + the path verifier.
        assert "memory_find_related" in coder
        assert "brain_get_neighbors" in coder
        assert "brain_explain_connection" in coder

    def test_get_coder_tool_count_is_17(self) -> None:
        """TAP-2006: pin the coder profile size at 17 tools.

        Six facade + four hook-callable + three quality-loop + four cross-repo /
        KG discovery (`hive_search`, `memory_find_related`,
        `brain_get_neighbors`, `brain_explain_connection`). Bump this in lockstep
        with the profile YAML when the surface changes.
        """
        reg = ProfileRegistry()
        coder = reg.get("coder")
        assert len(coder) == 17, sorted(coder)

    def test_coder_description_calls_out_kg_discovery_tools(self) -> None:
        """TAP-2006: description must surface the discovery primitives by name.

        The description is what agents read when picking a profile — leaving the
        KG tools un-named there hides the most useful part of the surface.
        """
        reg = ProfileRegistry()
        description = reg.get_description("coder")
        assert "memory_find_related" in description
        assert "brain_get_neighbors" in description

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

    def test_get_agent_brain_returns_11_tools(self) -> None:
        """TAP-1579 (+TAP-1973): 'agent_brain' profile exposes the brain_* facade tools.

        Started at 10 tools in TAP-1579; TAP-1973 added
        `brain_record_events_batch` for partial-success N-event backfill.
        """
        reg = ProfileRegistry()
        agent_brain = reg.get("agent_brain")
        assert len(agent_brain) == 11
        assert "brain_record_events_batch" in agent_brain

    def test_get_agent_brain_contains_facade_tools(self) -> None:
        """TAP-1579: agent_brain must contain the 6 core AgentBrain facade tools."""
        reg = ProfileRegistry()
        agent_brain = reg.get("agent_brain")
        for tool in (
            "brain_recall",
            "brain_remember",
            "brain_forget",
            "brain_learn_success",
            "brain_learn_failure",
            "brain_status",
        ):
            assert tool in agent_brain, f"Expected agent_brain profile to contain {tool!r}"

    def test_get_agent_brain_excludes_memory_star_tools(self) -> None:
        """TAP-1579: agent_brain hides all low-level memory_* tools."""
        reg = ProfileRegistry()
        agent_brain = reg.get("agent_brain")
        for tool in agent_brain:
            assert not tool.startswith("memory_"), (
                f"agent_brain profile must not contain memory_* tools, found {tool!r}"
            )

    def test_get_agent_brain_contains_only_brain_star_tools(self) -> None:
        """TAP-1579: every tool in agent_brain must start with brain_."""
        reg = ProfileRegistry()
        agent_brain = reg.get("agent_brain")
        non_brain = [t for t in agent_brain if not t.startswith("brain_")]
        assert not non_brain, (
            f"agent_brain profile must contain only brain_* tools, found {sorted(non_brain)}"
        )

    def test_get_agent_brain_is_subset_of_full(self) -> None:
        reg = ProfileRegistry()
        assert reg.get("agent_brain").issubset(reg.get("full"))

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
        """All bundled profiles must pass validation against the live tool set.

        Tool count is pinned to detect drift; bump when adding/removing a
        ``@mcp.tool`` decorated function.  TAP-1973 added
        `brain_record_events_batch` → 73.
        """
        import re

        # After tap-605, tools live in tools_*.py submodules, not __init__.py.
        tool_files = sorted(Path("src/tapps_brain/mcp_server/").glob("tools_*.py"))
        tool_files.append(Path("src/tapps_brain/mcp_server/__init__.py"))
        content = "\n".join(p.read_text() for p in tool_files)
        pattern = r"@mcp\.tool\(\)[^\n]*\n\s+(?:async )?def ([a-z_]+)\("
        all_tools = frozenset(re.findall(pattern, content))
        assert len(all_tools) == 73, f"Expected 73 tools, found {len(all_tools)}"

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


# ---------------------------------------------------------------------------
# defer_loading — TAP-1985
# ---------------------------------------------------------------------------


class TestDeferredTools:
    """Per-tool ``defer_loading: true`` annotation parsing + ``get_deferred``."""

    def test_bundled_full_has_52_deferred_tools(self) -> None:
        """`full` profile keeps 8 eager daily drivers + 52 deferred (TAP-1973: +1)."""
        reg = ProfileRegistry()
        deferred = reg.get_deferred("full")
        eager = reg.get("full") - deferred
        assert len(deferred) == 52
        assert len(eager) == 8

    def test_bundled_full_eager_set_matches_daily_drivers(self) -> None:
        """The 8 eager tools in `full` are the documented daily-driver budget."""
        reg = ProfileRegistry()
        eager = reg.get("full") - reg.get_deferred("full")
        assert eager == frozenset(
            {
                "brain_recall",
                "brain_remember",
                "brain_status",
                "brain_get_neighbors",
                "brain_explain_connection",
                "memory_search",
                "memory_find_related",
                "hive_search",
            }
        )

    def test_bundled_operator_has_65_deferred_tools(self) -> None:
        """`operator` profile shares 8 daily drivers; remaining 65 deferred (TAP-1973: +1)."""
        reg = ProfileRegistry()
        deferred = reg.get_deferred("operator")
        eager = reg.get("operator") - deferred
        assert len(deferred) == 65
        assert len(eager) == 8

    def test_bundled_operator_eager_matches_full(self) -> None:
        """Daily-driver budget is identical between `full` and `operator`."""
        reg = ProfileRegistry()
        full_eager = reg.get("full") - reg.get_deferred("full")
        operator_eager = reg.get("operator") - reg.get_deferred("operator")
        assert full_eager == operator_eager

    def test_bundled_coder_has_no_deferred_tools(self) -> None:
        """Small profiles (coder/reviewer/seeder/agent_brain) keep all eager."""
        reg = ProfileRegistry()
        assert reg.get_deferred("coder") == frozenset()
        assert reg.get_deferred("reviewer") == frozenset()
        assert reg.get_deferred("seeder") == frozenset()
        assert reg.get_deferred("agent_brain") == frozenset()

    def test_deferred_tools_still_in_callable_set(self) -> None:
        """Deferred tools remain in ``get()`` — they're callable, just hidden."""
        reg = ProfileRegistry()
        deferred = reg.get_deferred("full")
        all_tools = reg.get("full")
        assert deferred.issubset(all_tools)

    def test_get_deferred_returns_frozenset(self) -> None:
        reg = ProfileRegistry()
        assert isinstance(reg.get_deferred("full"), frozenset)
        assert isinstance(reg.get_deferred("coder"), frozenset)

    def test_get_deferred_raises_unknown_profile_error(self) -> None:
        reg = ProfileRegistry()
        with pytest.raises(UnknownProfileError):
            reg.get_deferred("nonexistent_profile")

    def test_dict_form_with_defer_loading_true_marks_deferred(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              p:
                tools:
                  - eager_tool
                  - name: deferred_tool
                    defer_loading: true
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert reg.get("p") == frozenset({"eager_tool", "deferred_tool"})
        assert reg.get_deferred("p") == frozenset({"deferred_tool"})

    def test_dict_form_with_defer_loading_false_is_eager(self, tmp_path: Path) -> None:
        """Explicit ``defer_loading: false`` keeps the tool eager."""
        yaml_text = textwrap.dedent("""\
            profiles:
              p:
                tools:
                  - name: explicit_eager
                    defer_loading: false
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert reg.get("p") == frozenset({"explicit_eager"})
        assert reg.get_deferred("p") == frozenset()

    def test_dict_form_without_defer_loading_key_is_eager(self, tmp_path: Path) -> None:
        """Dict entries missing ``defer_loading`` default to eager."""
        yaml_text = textwrap.dedent("""\
            profiles:
              p:
                tools:
                  - name: dict_no_flag
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert reg.get("p") == frozenset({"dict_no_flag"})
        assert reg.get_deferred("p") == frozenset()

    def test_malformed_dict_entry_missing_name_raises(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              p:
                tools:
                  - defer_loading: true
        """)
        with pytest.raises(ValueError) as exc_info:
            _make_registry(yaml_text, tmp_path)
        assert "name" in str(exc_info.value)
        assert "'p'" in str(exc_info.value)

    def test_malformed_entry_unsupported_type_raises(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              p:
                tools:
                  - 42
        """)
        with pytest.raises(ValueError) as exc_info:
            _make_registry(yaml_text, tmp_path)
        assert "string" in str(exc_info.value)
        assert "'p'" in str(exc_info.value)

    def test_mixed_string_and_dict_entries(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent("""\
            profiles:
              mixed:
                tools:
                  - plain_string_tool
                  - name: dict_eager_tool
                    defer_loading: false
                  - name: dict_deferred_tool
                    defer_loading: true
                  - another_string_tool
        """)
        reg = _make_registry(yaml_text, tmp_path)
        assert reg.get("mixed") == frozenset(
            {
                "plain_string_tool",
                "dict_eager_tool",
                "dict_deferred_tool",
                "another_string_tool",
            }
        )
        assert reg.get_deferred("mixed") == frozenset({"dict_deferred_tool"})
