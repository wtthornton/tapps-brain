"""Tests for TAP-545 — operator-tool gate fails open silently.

Hardens the gate in ``create_server`` against silent regression by:

1. Removing the ``contextlib.suppress(Exception)`` around
   ``mcp._tool_manager.remove_tool(...)`` so startup fails loudly if
   FastMCP ever renames or moves that API.
2. Asserting after the removal loop that no operator tool name remains
   in the tool registry.
3. Adding a belt-and-suspenders runtime gate inside every operator tool
   body that refuses to execute when ``mcp._tapps_operator_tools_enabled``
   is not truthy.

These tests pin all three behaviours as regressions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.requires_mcp


# Keep this mirror in sync with ``_OPERATOR_TOOL_NAMES`` inside
# ``src/tapps_brain/mcp_server/__init__.py``.  Drift is caught by
# ``test_operator_server_registers_every_operator_tool`` below and by the
# existing ``test_operator_tools_present_when_enabled`` in
# ``test_mcp_server.py`` — any operator tool added to production that is
# missing from this mirror will fail the "all registered" assertion.
_EXPECTED_OPERATOR_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "maintenance_consolidate",
        "maintenance_gc",
        "maintenance_stale",
        "tapps_brain_health",
        "memory_gc_config",
        "memory_gc_config_set",
        "memory_consolidation_config",
        "memory_consolidation_config_set",
        "memory_export",
        "memory_import",
        "tapps_brain_relay_export",
        "flywheel_evaluate",
        "flywheel_hive_feedback",
    }
)


def _close(server: Any) -> None:
    """Close the store(s) attached to a FastMCP server by ``create_server``."""
    store = getattr(server, "_tapps_store", None)
    if store is not None:
        hive = getattr(store, "_hive_store", None)
        if hive is not None:
            try:
                hive.close()
            except Exception:  # pragma: no cover — defensive in teardown
                pass
        try:
            store.close()
        except Exception:  # pragma: no cover — defensive in teardown
            pass


def _tool_names(server: Any) -> set[str]:
    return {t.name for t in server._tool_manager.list_tools()}


def _tool_fn(server: Any, name: str):
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool.fn
    msg = f"tool not found: {name}"
    raise KeyError(msg)


@pytest.fixture
def store_dir(tmp_path):
    return tmp_path


class TestStartupFailsOnRemovalError:
    """Acceptance: server refuses to start if operator-tool removal fails."""

    def test_startup_raises_when_remove_tool_raises(self, store_dir):
        """If FastMCP's ``remove_tool`` raises (API drift / upgrade break),
        ``create_server`` must propagate the exception so the server never
        comes up with operator tools silently left on a standard session.
        """
        from tapps_brain.mcp_server import create_server

        def _boom(self: Any, name: str) -> None:
            raise RuntimeError(f"simulated FastMCP drift: remove_tool({name!r})")

        with patch(
            "mcp.server.fastmcp.tools.tool_manager.ToolManager.remove_tool",
            _boom,
        ):
            with pytest.raises(RuntimeError, match="simulated FastMCP drift"):
                create_server(store_dir, enable_hive=False, enable_operator_tools=False)

    def test_startup_raises_when_removal_is_a_noop(self, store_dir):
        """If a future patch makes ``remove_tool`` silently succeed without
        actually removing the tool, the post-loop assertion must catch it
        and refuse to start the server.
        """
        from tapps_brain.mcp_server import create_server

        def _noop(self: Any, name: str) -> None:
            return None  # pretend success but leave the tool registered

        with patch(
            "mcp.server.fastmcp.tools.tool_manager.ToolManager.remove_tool",
            _noop,
        ):
            with pytest.raises(RuntimeError, match="operator-tool gate failed"):
                create_server(store_dir, enable_hive=False, enable_operator_tools=False)


class TestStandardServerRegistryExcludesOperators:
    """Acceptance: standard server tool list excludes every operator name."""

    def test_standard_server_excludes_every_operator_tool(self, store_dir):
        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir, enable_hive=False, enable_operator_tools=False)
        try:
            names = _tool_names(server)
            leaked = names & _EXPECTED_OPERATOR_TOOL_NAMES
            assert not leaked, f"standard server leaked operator tools: {sorted(leaked)}"
            assert server._tapps_operator_tools_enabled is False
        finally:
            _close(server)

    def test_operator_server_registers_every_operator_tool(self, store_dir):
        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir, enable_hive=False, enable_operator_tools=True)
        try:
            names = _tool_names(server)
            missing = _EXPECTED_OPERATOR_TOOL_NAMES - names
            assert not missing, f"operator server missing tools: {sorted(missing)}"
            assert server._tapps_operator_tools_enabled is True
        finally:
            _close(server)


class TestPerToolRuntimeGate:
    """Belt-and-suspenders: each operator tool body must refuse to execute
    when ``mcp._tapps_operator_tools_enabled`` is not truthy.  This guards
    against a future regression where registry removal succeeds but the
    enabled-flag is mis-wired, or where a caller obtains a tool reference
    before removal runs.
    """

    @pytest.mark.parametrize("tool_name", sorted(_EXPECTED_OPERATOR_TOOL_NAMES))
    def test_operator_tool_refuses_when_flag_is_false(self, store_dir, tool_name):
        from tapps_brain.mcp_server import create_server

        # Build an operator server so the tool is registered, then flip
        # the enabled flag to simulate the fail-open scenario the gate
        # is supposed to catch.
        server = create_server(store_dir, enable_hive=False, enable_operator_tools=True)
        try:
            fn = _tool_fn(server, tool_name)
            server._tapps_operator_tools_enabled = False

            with pytest.raises(RuntimeError, match="operator-tool gate failed open"):
                # Every operator tool guard runs before argument handling,
                # so we can probe with a harmless no-arg call.  A few tools
                # have required positional args — pass empty strings / zeros
                # since the guard raises before the service layer runs.
                if tool_name == "flywheel_evaluate":
                    fn("/tmp/does-not-matter", k=1)
                elif tool_name == "tapps_brain_relay_export":
                    fn("src-agent", "[]")
                elif tool_name == "memory_import":
                    fn("[]")
                else:
                    fn()
        finally:
            _close(server)

    def test_operator_tool_runs_normally_when_flag_is_true(self, store_dir):
        """Sanity: the runtime gate must not break the happy path on a
        properly-configured operator server.  We pick ``memory_gc_config``
        because it is a pure read with no required arguments.
        """
        import json

        from tapps_brain.mcp_server import create_server

        server = create_server(store_dir, enable_hive=False, enable_operator_tools=True)
        try:
            fn = _tool_fn(server, "memory_gc_config")
            result = fn()
            # Result is a JSON-encoded config dict — must round-trip cleanly.
            parsed = json.loads(result)
            assert isinstance(parsed, dict)
        finally:
            _close(server)
