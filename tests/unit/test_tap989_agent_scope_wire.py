"""TAP-989 — agent_scope + memory_group through the brain_remember wire.

Covers the four-layer plumbing:

1. ``services/memory_service.brain_remember`` — the service-layer entry
   point. Accepts explicit ``agent_scope`` / ``memory_group``, normalises
   ``agent_scope``, and applies the precedence rule (explicit wins over
   the legacy ``share`` / ``share_with`` derivation).
2. ``mcp_server/tools_brain.brain_remember`` — the MCP tool wrapper.
3. ``TappsBrainClient.remember`` (sync).
4. ``AsyncTappsBrainClient.remember`` (async).

Each layer must forward the new kwargs without mutation. The service layer
owns scope normalisation and the ``invalid_agent_scope`` error envelope.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_store(saved_key: str = "k") -> MagicMock:
    from tapps_brain.models import MemoryEntry

    store = MagicMock()
    entry = MemoryEntry(key=saved_key, value="x")
    store.save.return_value = entry
    return store


# ---------------------------------------------------------------------------
# Service layer — memory_service.brain_remember
# ---------------------------------------------------------------------------


class TestBrainRememberAgentScopeService:
    """Direct tests against ``memory_service.brain_remember``."""

    @pytest.mark.parametrize(
        "scope_in,scope_expected",
        [
            ("private", "private"),
            ("domain", "domain"),
            ("hive", "hive"),
            ("group:frontend", "group:frontend"),
        ],
    )
    def test_explicit_agent_scope_forwarded_to_store(
        self, scope_in: str, scope_expected: str
    ) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="x", agent_scope=scope_in)
        kwargs = store.save.call_args.kwargs
        assert kwargs["agent_scope"] == scope_expected

    def test_invalid_agent_scope_returns_error_envelope(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        result = brain_remember(store, "proj", "agent", fact="x", agent_scope="bogus-scope")
        assert result["error"] == "invalid_agent_scope"
        assert "valid_values" in result
        # Service must NOT call store.save when validation fails.
        store.save.assert_not_called()

    def test_explicit_agent_scope_overrides_share_true(self) -> None:
        """Precedence: explicit agent_scope wins over share=True (which would derive 'group')."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(
                store,
                "proj",
                "agent",
                fact="x",
                share=True,  # legacy → would derive "group"
                agent_scope="domain",  # explicit → wins
            )
        assert store.save.call_args.kwargs["agent_scope"] == "domain"

    def test_explicit_agent_scope_overrides_share_with_hive(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(
                store,
                "proj",
                "agent",
                fact="x",
                share_with="hive",  # legacy → would derive "hive"
                agent_scope="private",  # explicit → wins
            )
        assert store.save.call_args.kwargs["agent_scope"] == "private"

    def test_legacy_share_true_still_works_when_agent_scope_empty(self) -> None:
        """Back-compat: empty agent_scope falls back to legacy derivation."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="x", share=True)
        assert store.save.call_args.kwargs["agent_scope"] == "group"

    def test_legacy_share_with_named_group_still_works(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="x", share_with="frontend")
        assert store.save.call_args.kwargs["agent_scope"] == "group:frontend"

    def test_default_no_args_yields_private(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="x")
        assert store.save.call_args.kwargs["agent_scope"] == "private"

    def test_memory_group_forwarded_when_set(self) -> None:
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="x", memory_group="team-a")
        assert store.save.call_args.kwargs["memory_group"] == "team-a"

    def test_memory_group_not_forwarded_when_empty(self) -> None:
        """Empty memory_group must not appear as a save kwarg (preserves MEMORY_GROUP_UNSET semantics)."""
        from tapps_brain.services.memory_service import brain_remember

        store = _make_store()
        with patch("tapps_brain.agent_brain._content_key", return_value="k"):
            brain_remember(store, "proj", "agent", fact="x")
        assert "memory_group" not in store.save.call_args.kwargs


# ---------------------------------------------------------------------------
# Client surface — TappsBrainClient.remember + AsyncTappsBrainClient.remember
# ---------------------------------------------------------------------------


class TestBrainRememberAgentScopeClient:
    """The client-side wrappers must forward the new kwargs into the tool call."""

    def test_sync_client_remember_forwards_agent_scope(self) -> None:
        from tapps_brain.client import TappsBrainClient

        with patch("tapps_brain.client.TappsBrainClient._init_http"):
            client = TappsBrainClient(
                "http://brain:8080",
                project_id="p1",
                agent_id="a1",
                auth_token="t",
            )
        with patch.object(client, "_tool", return_value={"key": "k"}) as mocked:
            client.remember("hello", agent_scope="hive", memory_group="team-a")
        kwargs = mocked.call_args.kwargs
        assert kwargs["agent_scope"] == "hive"
        assert kwargs["memory_group"] == "team-a"

    @pytest.mark.asyncio
    async def test_async_client_remember_forwards_agent_scope(self) -> None:
        from tapps_brain.client import AsyncTappsBrainClient

        client = AsyncTappsBrainClient(
            "http://brain:8080",
            project_id="p1",
            agent_id="a1",
            auth_token="t",
        )

        async def _fake_tool(_name: str, **_kwargs: Any) -> dict[str, str]:
            return {"key": "k"}

        with patch.object(client, "_tool", side_effect=_fake_tool) as mocked:
            await client.remember("hello", agent_scope="domain")
        kwargs = mocked.call_args.kwargs
        assert kwargs["agent_scope"] == "domain"
        assert kwargs["memory_group"] == ""
        await client.close()

    def test_sync_client_default_remember_passes_empty_agent_scope(self) -> None:
        """When the caller doesn't pass agent_scope, the wire still carries the default empty string.

        The service layer interprets empty as 'derive from share/share_with'.
        """
        from tapps_brain.client import TappsBrainClient

        with patch("tapps_brain.client.TappsBrainClient._init_http"):
            client = TappsBrainClient(
                "http://brain:8080",
                project_id="p1",
                agent_id="a1",
                auth_token="t",
            )
        with patch.object(client, "_tool", return_value={"key": "k"}) as mocked:
            client.remember("hello")
        kwargs = mocked.call_args.kwargs
        assert kwargs["agent_scope"] == ""


# ---------------------------------------------------------------------------
# MCP tool surface — tools_brain.brain_remember signature
# ---------------------------------------------------------------------------


class TestBrainRememberMcpToolSignature:
    """The MCP tool wrapper must declare the new parameters so they ride the wire."""

    def test_mcp_tool_signature_includes_new_kwargs(self) -> None:
        import inspect

        # The tool is registered via a closure inside register_brain_tools; we
        # verify the source declares the kwargs (signature-level guarantee).
        from tapps_brain.mcp_server import tools_brain

        src = inspect.getsource(tools_brain.register_brain_tools)
        # Both kwargs must appear in the brain_remember tool definition.
        assert "agent_scope: str = " in src, (
            "MCP tool brain_remember must declare agent_scope param (TAP-989)"
        )
        assert "memory_group: str = " in src, (
            "MCP tool brain_remember must declare memory_group param (TAP-989)"
        )
        # And both must be forwarded to the service-layer call.
        assert "agent_scope=agent_scope" in src
        assert "memory_group=memory_group" in src
