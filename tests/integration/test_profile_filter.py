"""Contract tests for EPIC-073 profile-based MCP tool filtering.

STORY-073.6: Contract tests + rollout plan for profile filtering.

Tests cover:
- Golden-file contracts: each profile has exactly the expected tool set.
- Drift detection: ProfileRegistry.get("full") covers every registered tool.
- Filter integration: list_tools() and call_tool enforcement with real
  ProfileRegistry (no Postgres required for filter-behaviour tests).
- Backwards-compat: no X-Brain-Profile header → same 55-tool set as "full".
- End-to-end profile selection via the MCP session (requires mcp extra).

Postgres-dependent tests are marked with ``pytest.mark.requires_postgres`` and
are skipped when ``TAPPS_BRAIN_DATABASE_URL`` / ``TAPPS_TEST_POSTGRES_DSN`` is
not set.
"""

from __future__ import annotations

import contextvars
import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from tapps_brain.mcp_server.profile_registry import ProfileRegistry
from tapps_brain.mcp_server.tool_filter import install_tool_filter

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "profile_tool_sets"
_MCP_INIT = (
    Path(__file__).parent.parent.parent / "src" / "tapps_brain" / "mcp_server" / "__init__.py"
)

# Profiles exposed on the standard server (no operator tools).
_STANDARD_PROFILES = ["coder", "full", "reviewer", "seeder"]
# Profiles that include operator-only tools.
_ALL_PROFILES = ["coder", "full", "operator", "reviewer", "seeder"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_golden(profile: str) -> frozenset[str]:
    """Load the golden tool-set for *profile* from the fixture file."""
    path = _FIXTURE_DIR / f"{profile}.txt"
    return frozenset(line.strip() for line in path.read_text().splitlines() if line.strip())


def _all_registered_tools() -> frozenset[str]:
    """Return every tool name decorated with ``@mcp.tool()`` in __init__.py."""
    content = _MCP_INIT.read_text()
    pattern = r"@mcp\.tool\(\)[^\n]*\n\s+(?:async )?def ([a-z_]+)\("
    return frozenset(re.findall(pattern, content))


def _make_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    return tool


def _make_mock_mcp(tool_names: list[str]) -> MagicMock:
    """Return a lightweight mock FastMCP with list_tools + call_tool."""
    mcp = MagicMock()
    mgr = MagicMock()
    mgr.list_tools.return_value = [_make_tool(n) for n in tool_names]
    mgr.call_tool = AsyncMock(return_value="ok")
    mcp._tool_manager = mgr
    return mcp


# ---------------------------------------------------------------------------
# 1. Golden-file contract tests (no Postgres required)
# ---------------------------------------------------------------------------


class TestGoldenFileContracts:
    """Each profile's tool set must exactly match its golden fixture file.

    To update a golden file: regenerate from the YAML, review the diff in PR,
    and commit — the review is the intentional gate against silent drift.
    """

    @pytest.mark.parametrize("profile", _ALL_PROFILES)
    def test_profile_matches_golden_file(self, profile: str) -> None:
        """ProfileRegistry.get(profile) must equal the contents of <profile>.txt."""
        registry = ProfileRegistry()
        actual = registry.get(profile)
        golden = _load_golden(profile)
        missing_from_golden = actual - golden
        extra_in_golden = golden - actual
        assert not missing_from_golden, (
            f"Profile '{profile}': tools present in registry but MISSING from golden file "
            f"— update tests/fixtures/profile_tool_sets/{profile}.txt:\n"
            f"  {sorted(missing_from_golden)}"
        )
        assert not extra_in_golden, (
            f"Profile '{profile}': tools in golden file but ABSENT from registry "
            f"— update tests/fixtures/profile_tool_sets/{profile}.txt:\n"
            f"  {sorted(extra_in_golden)}"
        )

    def test_full_golden_has_55_tools(self) -> None:
        """Golden file for 'full' must list exactly 55 tools."""
        assert len(_load_golden("full")) == 55

    def test_operator_golden_has_68_tools(self) -> None:
        """Golden file for 'operator' must list exactly 68 tools."""
        assert len(_load_golden("operator")) == 68

    def test_coder_golden_has_15_tools(self) -> None:
        """Golden file for 'coder' must list exactly 15 tools."""
        assert len(_load_golden("coder")) == 15

    def test_reviewer_golden_has_8_tools(self) -> None:
        """Golden file for 'reviewer' must list exactly 8 tools."""
        assert len(_load_golden("reviewer")) == 8

    def test_seeder_golden_has_6_tools(self) -> None:
        """Golden file for 'seeder' must list exactly 6 tools."""
        assert len(_load_golden("seeder")) == 6

    @pytest.mark.parametrize("profile", _STANDARD_PROFILES)
    def test_restricted_profiles_are_subsets_of_full(self, profile: str) -> None:
        """All non-operator profiles must be strict subsets of 'full'."""
        if profile == "full":
            pytest.skip("'full' is not a subset of itself")
        registry = ProfileRegistry()
        assert registry.get(profile).issubset(registry.get("full")), (
            f"Profile '{profile}' contains tools not in 'full'"
        )

    def test_operator_is_superset_of_full(self) -> None:
        """'operator' must contain every tool in 'full'."""
        registry = ProfileRegistry()
        assert registry.get("full").issubset(registry.get("operator"))

    def test_golden_files_are_sorted(self) -> None:
        """Golden files must be sorted — makes PR diffs readable."""
        for profile in _ALL_PROFILES:
            path = _FIXTURE_DIR / f"{profile}.txt"
            lines = [l for l in path.read_text().splitlines() if l.strip()]
            assert lines == sorted(lines), (
                f"tests/fixtures/profile_tool_sets/{profile}.txt is not sorted"
            )


# ---------------------------------------------------------------------------
# 2. Drift detection (no Postgres required)
# ---------------------------------------------------------------------------


class TestDriftDetection:
    """Catch new tools added to __init__.py without being classified.

    When a developer adds ``@mcp.tool()`` to __init__.py they MUST also add
    the tool to at least the ``full`` and ``operator`` profiles in
    mcp_profiles.yaml. These tests fail loudly if that step is skipped.
    """

    def test_full_profile_covers_all_standard_tools(self) -> None:
        """'full' must contain every standard (non-operator) tool.

        Standard tools = all registered tools minus the 13 operator-only tools
        that are gated behind enable_operator_tools=True.
        """
        registry = ProfileRegistry()
        all_tools = _all_registered_tools()
        full_tools = registry.get("full")
        operator_only = registry.get("operator") - registry.get("full")

        # Every standard tool must be in 'full'.
        standard_tools = all_tools - operator_only
        unclassified = standard_tools - full_tools
        assert not unclassified, (
            "New tools added to __init__.py are NOT in the 'full' profile — "
            "add them to mcp_profiles.yaml:\n"
            f"  {sorted(unclassified)}"
        )

    def test_operator_profile_covers_all_68_tools(self) -> None:
        """'operator' must contain all registered tools (standard + operator-only)."""
        registry = ProfileRegistry()
        all_tools = _all_registered_tools()
        operator_tools = registry.get("operator")
        unclassified = all_tools - operator_tools
        assert not unclassified, (
            "New tools added to __init__.py are NOT in the 'operator' profile — "
            "add them to mcp_profiles.yaml:\n"
            f"  {sorted(unclassified)}"
        )

    def test_registered_tool_count_is_68(self) -> None:
        """The MCP server must have exactly 68 registered tools (55 standard + 13 operator)."""
        all_tools = _all_registered_tools()
        assert len(all_tools) == 68, (
            f"Expected 68 registered tools, found {len(all_tools)}. "
            "Update mcp_profiles.yaml if you added or removed a tool."
        )

    def test_profile_validate_against_passes_for_all_registered_tools(self) -> None:
        """ProfileRegistry.validate_against() must pass when given all 68 registered tools.

        This is the same check create_server() performs at startup — if it
        raises here, the server would refuse to start.
        """
        registry = ProfileRegistry()
        all_tools = _all_registered_tools()
        # Should not raise
        registry.validate_against(all_tools)


# ---------------------------------------------------------------------------
# 3. Profile filter integration (no Postgres required)
# ---------------------------------------------------------------------------


class TestProfileFilterIntegration:
    """Test install_tool_filter with the real ProfileRegistry (no Postgres).

    Uses a lightweight mock FastMCP so the filter logic runs against the
    bundled mcp_profiles.yaml without needing a live MCP server or Postgres.
    """

    @pytest.mark.parametrize("profile", _ALL_PROFILES)
    def test_list_tools_returns_exact_golden_set(self, profile: str) -> None:
        """list_tools() with a given profile must return exactly the golden tool set."""
        registry = ProfileRegistry()
        golden = _load_golden(profile)

        # The 'full' profile is the filter's fast-path default.
        # For 'operator', supply all 68 tools as the registered set.
        if profile == "operator":
            all_names = list(registry.get("operator"))
        else:
            all_names = list(registry.get("full"))

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"test_profile_{profile}", default=None
        )
        cv.set(profile)
        mcp = _make_mock_mcp(all_names)
        install_tool_filter(
            mcp,
            profile_registry=registry,
            profile_contextvar=cv,
            default_profile="full",
        )

        result = mcp._tool_manager.list_tools()
        actual = {t.name for t in result}
        assert actual == golden, (
            f"Profile '{profile}': filter returned unexpected tools.\n"
            f"  Extra (returned but not in golden): {sorted(actual - golden)}\n"
            f"  Missing (in golden but not returned): {sorted(golden - actual)}"
        )

    def test_no_profile_header_returns_full_set(self) -> None:
        """No profile (contextvar None) → same 55-tool surface as 'full'."""
        registry = ProfileRegistry()
        full_tools = list(registry.get("full"))

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_no_profile", default=None
        )
        # Deliberately NOT setting cv → simulates "no X-Brain-Profile header"
        mcp = _make_mock_mcp(full_tools)
        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert {t.name for t in result} == registry.get("full")
        assert len(result) == 55

    def test_coder_excludes_destructive_ops(self) -> None:
        """'coder' profile must never expose destructive operations."""
        registry = ProfileRegistry()
        full_tools = list(registry.get("full"))
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "test_coder_destructive", default=None
        )
        cv.set("coder")
        mcp = _make_mock_mcp(full_tools)
        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result_names = {t.name for t in mcp._tool_manager.list_tools()}
        for dangerous_tool in ("memory_delete", "agent_delete", "maintenance_gc", "memory_export"):
            assert dangerous_tool not in result_names, (
                f"'coder' profile must not expose '{dangerous_tool}'"
            )

    def test_reviewer_contains_only_read_ops(self) -> None:
        """'reviewer' profile must contain only read/search tools."""
        registry = ProfileRegistry()
        reviewer_tools = registry.get("reviewer")
        write_tools = {
            "memory_save",
            "memory_save_many",
            "memory_delete",
            "memory_ingest",
            "memory_capture",
            "memory_supersede",
            "memory_reinforce",
        }
        overlap = reviewer_tools & write_tools
        assert not overlap, f"'reviewer' profile contains write tools: {sorted(overlap)}"


# ---------------------------------------------------------------------------
# 4. Call enforcement (no Postgres required for denial tests)
# ---------------------------------------------------------------------------


class TestCallEnforcement:
    """Verify that tools/call correctly enforces the profile boundary.

    Uses the mock FastMCP so enforcement is tested without a Postgres store.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "profile,allowed_tool",
        [
            ("coder", "brain_recall"),
            ("coder", "memory_reinforce"),
            ("reviewer", "memory_search"),
            ("reviewer", "hive_search"),
            ("seeder", "memory_save"),
            ("seeder", "memory_ingest"),
        ],
    )
    async def test_in_profile_tool_is_callable(self, profile: str, allowed_tool: str) -> None:
        """An in-profile tool must pass through to the original call_tool."""
        registry = ProfileRegistry()
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"cv_{profile}_{allowed_tool}", default=None
        )
        cv.set(profile)
        mcp = _make_mock_mcp(list(registry.get("full")))
        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = await mcp._tool_manager.call_tool(allowed_tool, {})
        assert result == "ok"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "profile,denied_tool",
        [
            ("coder", "memory_delete"),
            ("coder", "agent_delete"),
            ("coder", "maintenance_gc"),
            ("reviewer", "memory_save"),
            ("reviewer", "memory_delete"),
            ("seeder", "memory_delete"),
            ("seeder", "brain_recall"),
        ],
    )
    async def test_out_of_profile_tool_raises_mcp_error(
        self, profile: str, denied_tool: str
    ) -> None:
        """An out-of-profile tool call must raise McpError with code -32601."""
        try:
            from mcp.shared.exceptions import McpError
        except ImportError:
            pytest.skip("mcp package not installed")

        registry = ProfileRegistry()
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            f"cv_deny_{profile}_{denied_tool}", default=None
        )
        cv.set(profile)
        mcp = _make_mock_mcp(list(registry.get("full")))
        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(McpError) as exc_info:
            await mcp._tool_manager.call_tool(denied_tool, {})

        err = exc_info.value
        assert err.error.code == -32601, f"Expected METHOD_NOT_FOUND (-32601), got {err.error.code}"
        assert err.error.data is not None
        assert err.error.data["error"] == "tool_not_in_profile"
        assert err.error.data["tool"] == denied_tool
        assert err.error.data["profile"] == profile

    @pytest.mark.asyncio
    async def test_out_of_profile_error_message_is_informative(self) -> None:
        """The McpError message must identify both the tool and the profile."""
        try:
            from mcp.shared.exceptions import McpError
        except ImportError:
            pytest.skip("mcp package not installed")

        registry = ProfileRegistry()
        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar("cv_msg_test", default=None)
        cv.set("reviewer")
        mcp = _make_mock_mcp(list(registry.get("full")))
        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        with pytest.raises(McpError) as exc_info:
            await mcp._tool_manager.call_tool("memory_delete", {})

        msg = exc_info.value.error.message
        assert "memory_delete" in msg
        assert "reviewer" in msg


# ---------------------------------------------------------------------------
# 5. Backwards-compatibility contract (no Postgres required)
# ---------------------------------------------------------------------------


class TestBackwardsCompat:
    """No X-Brain-Profile header → same tool surface as before EPIC-073.

    The pre-EPIC-073 surface is the 'full' profile (55 tools). Clients that
    never set the header must see zero behaviour change.
    """

    def test_default_profile_env_var_falls_back_to_full(self) -> None:
        """When TAPPS_BRAIN_DEFAULT_PROFILE is unset, 'full' is the default."""
        import os

        from tapps_brain.mcp_server.profile_resolver import ProfileResolver

        env_backup = os.environ.pop("TAPPS_BRAIN_DEFAULT_PROFILE", None)
        try:
            registry = ProfileRegistry()
            resolver = ProfileResolver(registry)
            # No header, no agent registry → must resolve to "full"
            profile = resolver.resolve(
                project_id="test-proj",
                agent_id="test-agent",
                header_profile=None,
            )
            assert profile == "full"
        finally:
            if env_backup is not None:
                os.environ["TAPPS_BRAIN_DEFAULT_PROFILE"] = env_backup

    def test_no_header_list_tools_returns_55_tools(self) -> None:
        """No profile header → list_tools returns exactly 55 tools (same as 'full')."""
        registry = ProfileRegistry()
        full_tools = list(registry.get("full"))
        assert len(full_tools) == 55

        cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "cv_compat_no_header", default=None
        )
        # cv is None — simulates no X-Brain-Profile header
        mcp = _make_mock_mcp(full_tools)
        install_tool_filter(mcp, profile_registry=registry, profile_contextvar=cv)

        result = mcp._tool_manager.list_tools()
        assert len(result) == 55
        assert {t.name for t in result} == registry.get("full")

    def test_full_profile_explicit_header_matches_no_header(self) -> None:
        """Explicit 'full' header must produce an identical result to no header."""
        registry = ProfileRegistry()
        full_tools = list(registry.get("full"))

        # No header
        cv_none: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "cv_no_header", default=None
        )
        mcp_none = _make_mock_mcp(full_tools)
        install_tool_filter(mcp_none, profile_registry=registry, profile_contextvar=cv_none)
        no_header_result = {t.name for t in mcp_none._tool_manager.list_tools()}

        # Explicit 'full' header
        cv_full: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "cv_explicit_full", default=None
        )
        cv_full.set("full")
        mcp_full = _make_mock_mcp(full_tools)
        install_tool_filter(mcp_full, profile_registry=registry, profile_contextvar=cv_full)
        full_result = {t.name for t in mcp_full._tool_manager.list_tools()}

        assert no_header_result == full_result, (
            "Explicit 'full' header must return the same tools as no header"
        )


# ---------------------------------------------------------------------------
# 6. End-to-end with real FastMCP session (requires mcp + Postgres)
# ---------------------------------------------------------------------------

pytest.importorskip("mcp")
pytestmark_mcp = pytest.mark.requires_mcp


@pytest.fixture()
def _project_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def _mcp_server(_project_dir: Path):
    """Create a standard (non-operator) FastMCP server for end-to-end tests."""
    import os

    from tapps_brain.mcp_server import create_server

    dsn = os.environ.get("TAPPS_TEST_POSTGRES_DSN") or os.environ.get("TAPPS_BRAIN_DATABASE_URL")
    if not dsn:
        pytest.skip("requires TAPPS_TEST_POSTGRES_DSN / TAPPS_BRAIN_DATABASE_URL")

    server = create_server(_project_dir)
    yield server
    if hasattr(server, "_tapps_store"):
        server._tapps_store.close()


class TestEndToEndProfileFiltering:
    """End-to-end contract tests via a real MCP session (requires mcp + Postgres).

    These tests boot a real FastMCP server (create_server()) and set
    REQUEST_PROFILE directly on the installed tool_manager to simulate what
    ProfileResolutionMiddleware does in the HTTP adapter.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "profile,expected_count",
        [
            ("full", 55),
            ("coder", 15),
            ("reviewer", 8),
            ("seeder", 6),
        ],
    )
    async def test_list_tools_count_per_profile(
        self, _mcp_server, profile: str, expected_count: int
    ) -> None:
        """tools/list via direct tool_manager access must return the right count per profile."""
        from tapps_brain.mcp_server import REQUEST_PROFILE

        token = REQUEST_PROFILE.set(profile)
        try:
            tools = _mcp_server._tool_manager.list_tools()
            names = {t.name for t in tools}
            assert len(names) == expected_count, (
                f"Profile '{profile}': expected {expected_count} tools, got {len(names)}. "
                f"Actual: {sorted(names)}"
            )
        finally:
            REQUEST_PROFILE.reset(token)

    @pytest.mark.asyncio
    async def test_no_profile_returns_full_tool_set(self, _mcp_server) -> None:
        """No REQUEST_PROFILE (None) → same 55-tool surface as 'full' profile."""
        from tapps_brain.mcp_server import REQUEST_PROFILE

        # Ensure contextvar is None (simulates no X-Brain-Profile header)
        token = REQUEST_PROFILE.set(None)
        try:
            tools = _mcp_server._tool_manager.list_tools()
            assert len(tools) == 55
        finally:
            REQUEST_PROFILE.reset(token)

    @pytest.mark.asyncio
    async def test_coder_profile_tool_names_match_golden(self, _mcp_server) -> None:
        """In end-to-end mode, 'coder' profile tool names must match the golden file."""
        from tapps_brain.mcp_server import REQUEST_PROFILE

        golden = _load_golden("coder")
        token = REQUEST_PROFILE.set("coder")
        try:
            tools = _mcp_server._tool_manager.list_tools()
            actual = {t.name for t in tools}
            assert actual == golden, (
                f"End-to-end 'coder' filter mismatch.\n"
                f"  Extra: {sorted(actual - golden)}\n"
                f"  Missing: {sorted(golden - actual)}"
            )
        finally:
            REQUEST_PROFILE.reset(token)

    @pytest.mark.asyncio
    async def test_out_of_profile_call_is_denied_end_to_end(self, _mcp_server) -> None:
        """Out-of-profile call_tool raises McpError with code -32601 on a real server."""
        from mcp.shared.exceptions import McpError

        from tapps_brain.mcp_server import REQUEST_PROFILE

        token = REQUEST_PROFILE.set("reviewer")
        try:
            with pytest.raises(McpError) as exc_info:
                await _mcp_server._tool_manager.call_tool("memory_save", {"key": "k", "value": "v"})
            assert exc_info.value.error.code == -32601
        finally:
            REQUEST_PROFILE.reset(token)

    @pytest.mark.asyncio
    async def test_server_registry_stored_on_server_instance(self, _mcp_server) -> None:
        """create_server() must attach _tapps_profile_registry to the server instance."""
        assert hasattr(_mcp_server, "_tapps_profile_registry"), (
            "create_server() must attach _tapps_profile_registry for introspection"
        )
        registry = _mcp_server._tapps_profile_registry
        assert isinstance(registry, ProfileRegistry)
        assert "full" in registry.profiles
        assert "coder" in registry.profiles
