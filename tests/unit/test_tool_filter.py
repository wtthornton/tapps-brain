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
- McpError data fields: code=-32601, data includes tool + profile.
"""

from __future__ import annotations

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from tapps_brain.mcp_server.profile_registry import ProfileRegistry, UnknownProfileError
from tapps_brain.mcp_server.tool_filter import _DEFAULT_PROFILE, install_tool_filter

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

    def test_bundled_coder_profile_returns_15_tools(self) -> None:
        """Real ProfileRegistry.get('coder') must return exactly 15 tools."""
        # 6 facade + 4 hook-callable + 3 quality + 2 cross-repo = 15
        real_registry = ProfileRegistry()
        coder_tools = real_registry.get("coder")
        assert len(coder_tools) == 15

        all_tool_names = list(real_registry.get("full"))
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == coder_tools

    def test_bundled_full_profile_returns_55_tools(self) -> None:
        """Real ProfileRegistry with 'full' profile — no header → all 55 tools."""
        real_registry = ProfileRegistry()
        all_tool_names = list(real_registry.get("full"))
        assert len(all_tool_names) == 55

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_profile", default=None
        )
        # Simulate "no X-Brain-Profile header" → contextvar is None
        mcp = _make_mock_mcp(all_tool_names)

        install_tool_filter(mcp, profile_registry=real_registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert len(result) == 55


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
        """Calling a tool outside the profile must raise McpError(-32601)."""
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
        assert err.error.code == -32601  # METHOD_NOT_FOUND
        assert err.error.data is not None
        assert err.error.data["error"] == "tool_not_in_profile"
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

        assert exc_info.value.error.code == -32601


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
