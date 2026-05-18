"""Unit tests for install_tool_filter — EPIC-073 STORY-073.3.

Tests cover:
- tools/list filtering: full profile (fast path / no filtering).
- tools/list filtering: restricted profile returns subset.
- tools/list filtering: None contextvar value → falls back to "full".
- tools/list filtering: unknown profile → fail open (return all tools).
- tools/call enforcement: full profile → all tools callable.
- tools/call enforcement: allowed tool in restricted profile → passes.
- tools/call enforcement: disallowed tool in restricted profile → McpError.
- tools/call enforcement: None contextvar value → falls back to "full" (pass).
- tools/call enforcement: unknown profile → fail open (allow).
- McpError data fields: code=-32602 (INVALID_PARAMS) with
  ``data.reason = "out_of_profile"`` so consumers distinguish "hidden by
  profile" from "tool does not exist" (-32601). See TAP-1579.
"""

from __future__ import annotations

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from tapps_brain.mcp_server.profile_registry import ProfileRegistry, UnknownProfileError
from tapps_brain.mcp_server.tool_filter import (
    _DEFAULT_PROFILE,
    clear_tools_list_cache,
    get_profile_filter_metrics_snapshot,
    install_tool_filter,
    reset_profile_filter_counters,
)


@pytest.fixture(autouse=True)
def _clear_tool_filter_module_state() -> None:
    """Reset module-level caches between tests so TAP-1985 deferred-tool assertions
    are not contaminated by a prior test that cached a fuller catalog under the
    same profile name."""
    clear_tools_list_cache()
    reset_profile_filter_counters()


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _make_tool(name: str) -> MagicMock:
    """Return a mock tool info object with a ``.name`` attribute."""
    tool = MagicMock()
    tool.name = name
    return tool


def _make_mock_tool_manager(tool_names: list[str]) -> MagicMock:
    """Return a mock ToolManager with list_tools and call_tool."""
    manager = MagicMock()
    manager.list_tools.return_value = [_make_tool(n) for n in tool_names]
    manager.call_tool = AsyncMock(return_value="ok")
    return manager


def _make_mock_mcp(tool_names: list[str]) -> MagicMock:
    """Return a mock FastMCP instance."""
    mcp = MagicMock()
    mcp._tool_manager = _make_mock_tool_manager(tool_names)
    return mcp


def _make_registry(profile_map: dict[str, frozenset[str]]) -> MagicMock:
    """Return a mock ProfileRegistry with a specific profile→toolset mapping."""
    registry = MagicMock(spec=ProfileRegistry)

    def _get(name: str) -> frozenset[str]:
        if name not in profile_map:
            raise UnknownProfileError(name, list(profile_map))
        return profile_map[name]

    registry.get.side_effect = _get
    return registry


# Shared tool lists for tests
ALL_TOOLS = ["brain_recall", "brain_remember", "memory_save", "memory_delete", "agent_delete"]
CODER_TOOLS = frozenset({"brain_recall", "brain_remember", "memory_save"})
RESTRICTED_REGISTRY = {
    "coder": CODER_TOOLS,
    "full": frozenset(ALL_TOOLS),
}


# ---------------------------------------------------------------------------
# tools/list filtering
# ---------------------------------------------------------------------------


class TestListToolsFilter:
    """Tests for the wrapped list_tools method."""

    def test_full_profile_returns_all_tools_fast_path(self) -> None:
        """Profile 'full' must return all tools without filtering."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == set(ALL_TOOLS)
        # Verify the registry.get was NOT called for the fast path.
        registry.get.assert_not_called()

    def test_none_contextvar_falls_back_to_full_profile(self) -> None:
        """When contextvar is None, filter falls back to 'full' (no filtering)."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        # Don't set cv → value is None
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == set(ALL_TOOLS)
        registry.get.assert_not_called()

    def test_restricted_profile_returns_only_allowed_tools(self) -> None:
        """Profile 'coder' must hide tools not in its allowed set."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        names = {t.name for t in result}
        assert names == CODER_TOOLS
        assert "memory_delete" not in names
        assert "agent_delete" not in names

    def test_restricted_profile_tool_count(self) -> None:
        """Filtered list must have exactly the coder-profile count."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert len(result) == len(CODER_TOOLS)

    def test_unknown_profile_fails_open_returns_all_tools(self) -> None:
        """Unknown profile falls open for list_tools (returns all tools)."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("nonexistent_profile")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == set(ALL_TOOLS)

    def test_bundled_coder_profile_returns_18_tools(self) -> None:
        """Real ProfileRegistry.get('coder') must return exactly 18 tools.

        6 facade + 4 hook-callable + 3 quality + 5 cross-repo/graph
        (`hive_search`, `memory_find_related`, `brain_get_neighbors`,
        `brain_explain_connection`, `brain_record_events_batch` — TAP-1973).
        """
        real_registry = ProfileRegistry()
        coder_tools = real_registry.get("coder")
        assert len(coder_tools) == 18

        all_tool_names = list(real_registry.get("full"))
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == coder_tools

    def test_bundled_full_profile_returns_8_eager_tools(self) -> None:
        """TAP-1985: `full` profile defaults to 8 daily drivers; 55 deferred.

        Replaces the prior "returns 59 tools" assertion. The callable surface
        (``registry.get('full')``) is 63 post-TAP-2099, but the default
        ``tools/list`` payload returns only the 8 eager daily drivers so the
        eager catalog stays within the per-server budget (parent epic
        TAP-1983).
        """
        real_registry = ProfileRegistry()
        all_tool_names = list(real_registry.get("full"))
        assert len(all_tool_names) == 63  # callable surface (TAP-2099: +1)

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        # Simulate "no X-Brain-Profile header" → contextvar is None → "full"
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert len(result) == 8
        assert {t.name for t in result} == {
            "brain_recall",
            "brain_remember",
            "brain_status",
            "brain_get_neighbors",
            "brain_explain_connection",
            "memory_search",
            "memory_find_related",
            "hive_search",
        }

    def test_bundled_full_profile_omits_deferred_tools_from_list(self) -> None:
        """TAP-1985: deferred tool names must not appear in the default list."""
        real_registry = ProfileRegistry()
        all_tool_names = list(real_registry.get("full"))

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        visible = {t.name for t in result}
        for sample in (
            "memory_save",
            "memory_delete",
            "agent_create",
            "flywheel_process",
            "tapps_brain_session_end",
        ):
            assert sample in real_registry.get_deferred("full")
            assert sample not in visible


# ---------------------------------------------------------------------------
# tools/call enforcement
# ---------------------------------------------------------------------------


class TestCallToolEnforcement:
    """Tests for the wrapped call_tool method."""

    @pytest.mark.asyncio
    async def test_full_profile_allows_any_tool(self) -> None:
        """Profile 'full' must allow calls to any registered tool."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        # Should not raise for any tool
        result = await mcp._tool_manager.call_tool("memory_delete", {})
        assert result == "ok"
        registry.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_contextvar_falls_back_to_full_allows_any_tool(self) -> None:
        """None contextvar → full profile → all tools callable."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = await mcp._tool_manager.call_tool("memory_delete", {})
        assert result == "ok"
        registry.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_tool_in_restricted_profile_passes(self) -> None:
        """Calling an allowed tool in a restricted profile must succeed."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = await mcp._tool_manager.call_tool("brain_recall", {})
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_disallowed_tool_raises_mcp_error(self) -> None:
        """Calling a tool outside the profile must raise McpError(-32602).

        TAP-1579: code is INVALID_PARAMS (-32602) with
        ``data.reason = "out_of_profile"`` so MCP-bridge consumers can
        distinguish "hidden by profile" from "tool does not exist" (-32601).
        """
        try:
            from mcp.shared.exceptions import McpError
        except ImportError:
            pytest.skip("mcp package not installed")

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(McpError) as exc_info:
            await mcp._tool_manager.call_tool("memory_delete", {})

        err = exc_info.value
        assert err.error.code == -32602  # INVALID_PARAMS, distinct from -32601
        assert err.error.data is not None
        assert err.error.data["reason"] == "out_of_profile"
        assert err.error.data["tool"] == "memory_delete"
        assert err.error.data["profile"] == "coder"

    @pytest.mark.asyncio
    async def test_disallowed_tool_error_message_contains_tool_and_profile(self) -> None:
        """McpError message must reference both the tool name and profile."""
        try:
            from mcp.shared.exceptions import McpError
        except ImportError:
            pytest.skip("mcp package not installed")

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(McpError) as exc_info:
            await mcp._tool_manager.call_tool("agent_delete", {})

        msg = str(exc_info.value)
        assert "agent_delete" in msg
        assert "coder" in msg

    @pytest.mark.asyncio
    async def test_unknown_profile_fails_open_allows_call(self) -> None:
        """Unknown profile fails open for call_tool (allows the call)."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("nonexistent_profile")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        # Should not raise — fails open
        result = await mcp._tool_manager.call_tool("memory_delete", {})
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_kwargs_forwarded_to_original_call_tool(self) -> None:
        """Extra kwargs (context, convert_result) must be forwarded."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)
        # Record what call_tool was called with
        original_call = mcp._tool_manager.call_tool

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        await mcp._tool_manager.call_tool(
            "brain_recall", {"query": "test"}, context="ctx", convert_result=True
        )
        original_call.assert_called_once_with(
            "brain_recall", {"query": "test"}, context="ctx", convert_result=True
        )

    @pytest.mark.asyncio
    async def test_bundled_coder_memory_delete_is_denied(self) -> None:
        """memory_delete not in coder profile → denied via real ProfileRegistry."""
        try:
            from mcp.shared.exceptions import McpError
        except ImportError:
            pytest.skip("mcp package not installed")

        real_registry = ProfileRegistry()
        all_tool_names = list(real_registry.get("full"))

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        with pytest.raises(McpError) as exc_info:
            await mcp._tool_manager.call_tool("memory_delete", {})

        assert exc_info.value.error.code == -32602


# ---------------------------------------------------------------------------
# defer_loading — TAP-1985
# ---------------------------------------------------------------------------


class TestDeferredToolBehavior:
    """TAP-1985: deferred tools omitted from list_tools but still callable.

    Tool Search BETA reaches deferred tools on demand because the call path
    treats them as part of the profile's allowed set — only the visibility
    curtain is applied at list_tools time.
    """

    @pytest.mark.asyncio
    async def test_deferred_tool_remains_callable_in_full_profile(self) -> None:
        """Tool Search recovery — a deferred tool's full definition is reachable
        because call_tool still accepts it. The list curtain is visibility-only.
        """
        real_registry = ProfileRegistry()
        all_tool_names = list(real_registry.get("full"))

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        # `memory_save` is deferred — must not appear in tools/list...
        list_result = mcp._tool_manager.list_tools()
        assert "memory_save" not in {t.name for t in list_result}
        # ...but must still execute via tools/call (Tool Search recovery).
        call_result = await mcp._tool_manager.call_tool("memory_save", {})
        assert call_result == "ok"

    def test_bundled_operator_returns_8_eager_tools(self) -> None:
        """Operator profile shares the 8-tool daily-driver budget."""
        real_registry = ProfileRegistry()
        all_tool_names = list(real_registry.get("operator"))
        assert len(all_tool_names) == 76  # callable surface (TAP-2099: +1)

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("operator")
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert len(result) == 8

    def test_small_profiles_unchanged_by_defer_loading(self) -> None:
        """coder/reviewer/seeder/agent_brain are all-eager (no deferred entries)."""
        real_registry = ProfileRegistry()
        full_tools = list(real_registry.get("full"))

        # TAP-1973: coder + agent_brain each gained `brain_record_events_batch`.
        # TAP-2093: agent_brain gained `brain_audit_consumers` (11 → 12).
        expected_counts = {"coder": 18, "reviewer": 8, "seeder": 6, "agent_brain": 12}
        for profile_name, expected_count in expected_counts.items():
            cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
                f"test_profile_{profile_name}", default=None
            )
            cv.set(profile_name)
            # Build a per-profile mcp so list_tools returns just that profile's
            # callable set when filtered (mock the registry's allow-set against
            # all known tool names).
            all_known = list(set(full_tools) | set(real_registry.get("operator")))
            mcp = _make_mock_mcp(all_known)
            install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)
            result = mcp._tool_manager.list_tools()
            assert len(result) == expected_count, (
                f"{profile_name}: expected {expected_count}, got {len(result)}"
            )


# ---------------------------------------------------------------------------
# default_profile parameter
# ---------------------------------------------------------------------------


class TestDefaultProfile:
    def test_custom_default_profile_is_fast_path(self) -> None:
        """Setting default_profile='coder' means 'coder' is the fast path."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")  # same as default_profile
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(
            mcp,
            profile_registry=registry,
            profile_contextvar=cv,
            default_profile="coder",
        )

        # "coder" is now the default → fast path, no filtering, all tools returned.
        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == set(ALL_TOOLS)
        registry.get.assert_not_called()

    def test_default_profile_constant(self) -> None:
        """_DEFAULT_PROFILE must be 'full'."""
        assert _DEFAULT_PROFILE == "full"


# ---------------------------------------------------------------------------
# STORY-073.4: observability — metric counter tests
# ---------------------------------------------------------------------------


class TestProfileFilterMetrics:
    """Tests for module-level counter increments (STORY-073.4)."""

    def setup_method(self) -> None:
        """Reset counters before each test to avoid cross-test pollution."""
        reset_profile_filter_counters()

    # ------------------------------------------------------------------
    # list_tools counters
    # ------------------------------------------------------------------

    def test_list_tools_full_profile_increments_list_total(self) -> None:
        """tools/list with full profile increments mcp_tools_list_total."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics1", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        mcp._tool_manager.list_tools()

        snap = get_profile_filter_metrics_snapshot()
        assert snap["list_total"].get("full", 0) == 1
        assert snap["list_visible"].get("full", 0) == len(ALL_TOOLS)

    def test_list_tools_restricted_profile_increments_list_total(self) -> None:
        """tools/list with coder profile increments list_total with visible subset."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics2", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        mcp._tool_manager.list_tools()

        snap = get_profile_filter_metrics_snapshot()
        assert snap["list_total"].get("coder", 0) == 1
        assert snap["list_visible"].get("coder", 0) == len(CODER_TOOLS)

    def test_list_tools_accumulates_across_calls(self) -> None:
        """Multiple tools/list calls accumulate in list_total counter."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics3", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        mcp._tool_manager.list_tools()
        mcp._tool_manager.list_tools()
        mcp._tool_manager.list_tools()

        snap = get_profile_filter_metrics_snapshot()
        assert snap["list_total"].get("full", 0) == 3

    # ------------------------------------------------------------------
    # call_tool counters
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_allowed_call_increments_allowed_counter(self) -> None:
        """Allowed tool call increments mcp_tools_call_total with outcome=allowed."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics4", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        await mcp._tool_manager.call_tool("brain_recall", {})

        snap = get_profile_filter_metrics_snapshot()
        call_total = snap["call_total"]
        assert call_total.get(("coder", "brain_recall", "allowed"), 0) == 1

    @pytest.mark.asyncio
    async def test_denied_call_increments_denied_profile_counter(self) -> None:
        """Denied tool call increments mcp_tools_call_total with outcome=denied_profile."""
        try:
            from mcp.shared.exceptions import McpError
        except ImportError:
            pytest.skip("mcp package not installed")

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics5", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        with pytest.raises(McpError):
            await mcp._tool_manager.call_tool("memory_delete", {})

        snap = get_profile_filter_metrics_snapshot()
        call_total = snap["call_total"]
        assert call_total.get(("coder", "memory_delete", "denied_profile"), 0) == 1

    @pytest.mark.asyncio
    async def test_full_profile_call_increments_allowed_counter(self) -> None:
        """Full-profile call increments allowed counter with empty tool label (fast path)."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics6", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        await mcp._tool_manager.call_tool("memory_delete", {})

        snap = get_profile_filter_metrics_snapshot()
        call_total = snap["call_total"]
        assert call_total.get(("full", "", "allowed"), 0) == 1

    @pytest.mark.asyncio
    async def test_unknown_profile_call_increments_error_counter(self) -> None:
        """Unknown profile fail-open increments error counter."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics7", default=None
        )
        cv.set("nonexistent_profile")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        await mcp._tool_manager.call_tool("memory_delete", {})

        snap = get_profile_filter_metrics_snapshot()
        call_total = snap["call_total"]
        assert call_total.get(("nonexistent_profile", "memory_delete", "error"), 0) == 1

    # ------------------------------------------------------------------
    # reset helper
    # ------------------------------------------------------------------

    def test_reset_clears_all_counters(self) -> None:
        """reset_profile_filter_counters clears list_total, list_visible, call_total."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile_metrics8", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)
        mcp._tool_manager.list_tools()

        snap_before = get_profile_filter_metrics_snapshot()
        assert snap_before["list_total"].get("full", 0) > 0

        reset_profile_filter_counters()

        snap_after = get_profile_filter_metrics_snapshot()
        assert snap_after["list_total"] == {}
        assert snap_after["list_visible"] == {}
        assert snap_after["call_total"] == {}


# ---------------------------------------------------------------------------
# TAP-1580: ValidationError enrichment with required-field hints
# ---------------------------------------------------------------------------


def _build_validation_error(field_names: list[str]) -> Exception:
    """Return a pydantic ValidationError for a model whose required fields
    (``field_names``) are missing from the input."""
    from pydantic import ValidationError, create_model

    fields: dict[str, Any] = dict.fromkeys(field_names, (str, ...))
    args_model = create_model("argsModel", **fields)  # type: ignore[call-overload]
    try:
        args_model.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected ValidationError")


def _wrap_in_tool_error(name: str, validation_exc: Exception) -> Exception:
    """Mirror FastMCP's ``Tool.run`` wrapping of ``pydantic.ValidationError``."""
    from mcp.server.fastmcp.exceptions import ToolError

    return ToolError(f"Error executing tool {name}: {validation_exc}")


class TestValidationErrorEnrichment:
    """TAP-1580: ToolError messages must include a ``required_fields`` hint
    when the underlying cause is a ``pydantic.ValidationError`` reporting
    missing required fields."""

    @pytest.mark.asyncio
    async def test_single_missing_required_field_adds_hint(self) -> None:
        """A single missing required field is named in the enriched message."""
        from mcp.server.fastmcp.exceptions import ToolError

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_validation_enrich1", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        validation_exc = _build_validation_error(["query"])
        wrapped = _wrap_in_tool_error("memory_recall", validation_exc)
        wrapped.__cause__ = validation_exc
        mcp._tool_manager.call_tool = AsyncMock(side_effect=wrapped)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(ToolError) as exc_info:
            await mcp._tool_manager.call_tool("memory_recall", {})

        msg = str(exc_info.value)
        assert "required_fields" in msg
        assert "query" in msg
        assert "memory_recall" in msg

    @pytest.mark.asyncio
    async def test_multiple_missing_required_fields_all_listed(self) -> None:
        """Multiple missing required fields are listed in the hint."""
        from mcp.server.fastmcp.exceptions import ToolError

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_validation_enrich2", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        validation_exc = _build_validation_error(["key", "value"])
        wrapped = _wrap_in_tool_error("memory_save", validation_exc)
        wrapped.__cause__ = validation_exc
        mcp._tool_manager.call_tool = AsyncMock(side_effect=wrapped)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(ToolError) as exc_info:
            await mcp._tool_manager.call_tool("memory_save", {})

        msg = str(exc_info.value)
        assert "required_fields" in msg
        assert "key" in msg
        assert "value" in msg

    @pytest.mark.asyncio
    async def test_original_pydantic_message_preserved(self) -> None:
        """Original pydantic error text remains in the enriched message."""
        from mcp.server.fastmcp.exceptions import ToolError

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_validation_enrich3", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        validation_exc = _build_validation_error(["query"])
        original_text = str(validation_exc)
        wrapped = _wrap_in_tool_error("memory_recall", validation_exc)
        wrapped.__cause__ = validation_exc
        mcp._tool_manager.call_tool = AsyncMock(side_effect=wrapped)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(ToolError) as exc_info:
            await mcp._tool_manager.call_tool("memory_recall", {})

        msg = str(exc_info.value)
        assert "Field required" in original_text  # sanity check
        assert "Field required" in msg

    @pytest.mark.asyncio
    async def test_non_missing_validation_error_no_required_fields_hint(self) -> None:
        """Type errors on present fields don't synthesize a required_fields hint."""
        from mcp.server.fastmcp.exceptions import ToolError
        from pydantic import BaseModel, ValidationError

        class _Args(BaseModel):
            limit: int

        try:
            _Args.model_validate({"limit": "not-an-int"})
        except ValidationError as exc:
            validation_exc: Exception = exc
        else:
            raise AssertionError("expected ValidationError")

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_validation_enrich4", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        wrapped = _wrap_in_tool_error("memory_search", validation_exc)
        wrapped.__cause__ = validation_exc
        mcp._tool_manager.call_tool = AsyncMock(side_effect=wrapped)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(ToolError) as exc_info:
            await mcp._tool_manager.call_tool("memory_search", {"limit": "not-an-int"})

        msg = str(exc_info.value)
        assert "required_fields" not in msg

    @pytest.mark.asyncio
    async def test_non_validation_error_unchanged(self) -> None:
        """RuntimeError (non-validation) passes through untouched."""
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_validation_enrich5", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        original = RuntimeError("boom — totally unrelated")
        mcp._tool_manager.call_tool = AsyncMock(side_effect=original)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(RuntimeError) as exc_info:
            await mcp._tool_manager.call_tool("memory_recall", {})

        assert exc_info.value is original

    @pytest.mark.asyncio
    async def test_toolerror_without_validation_cause_unchanged(self) -> None:
        """ToolError that doesn't wrap a ValidationError isn't modified."""
        from mcp.server.fastmcp.exceptions import ToolError

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_validation_enrich6", default=None
        )
        cv.set("full")
        mcp = _make_mock_mcp(ALL_TOOLS)
        registry = _make_registry(RESTRICTED_REGISTRY)

        original = ToolError("Error executing tool memory_recall: internal blowup")
        mcp._tool_manager.call_tool = AsyncMock(side_effect=original)

        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(ToolError) as exc_info:
            await mcp._tool_manager.call_tool("memory_recall", {})

        msg = str(exc_info.value)
        assert "required_fields" not in msg
        assert "internal blowup" in msg
