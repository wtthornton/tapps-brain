"""Unit tests for ProfileResolver — EPIC-073 STORY-073.2.

Tests cover:
- resolve() with X-Brain-Profile header (takes precedence).
- resolve() with agent-registry lookup (header absent, agent registered).
- resolve() with server default (header absent, agent unregistered).
- Unknown-profile header returns structured 400 via the middleware path.
- TTL cache: hit/miss counters and invalidation.
- TAPPS_BRAIN_DEFAULT_PROFILE env-var override.
- REQUEST_PROFILE contextvar is exported from mcp_server.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from tapps_brain.mcp_server.profile_registry import ProfileRegistry, UnknownProfileError
from tapps_brain.mcp_server.profile_resolver import ProfileResolver

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry(profiles: dict[str, list[str]] | None = None) -> ProfileRegistry:
    """Return a ProfileRegistry with a small in-memory profile set."""
    reg = ProfileRegistry.__new__(ProfileRegistry)
    if profiles is None:
        profiles = {
            "full": ["tool_a", "tool_b", "tool_c"],
            "coder": ["tool_a"],
            "reviewer": ["tool_b"],
        }
    reg._profiles = {name: frozenset(tools) for name, tools in profiles.items()}
    return reg


def _make_resolver(
    *,
    getter: Any = None,
    default_profile: str = "full",
    cache_ttl: float = 60.0,
) -> ProfileResolver:
    return ProfileResolver(
        registry=_make_registry(),
        agent_profile_getter=getter,
        default_profile=default_profile,
        cache_ttl=cache_ttl,
    )


# ---------------------------------------------------------------------------
# Precedence 1: X-Brain-Profile header
# ---------------------------------------------------------------------------

class TestHeaderPrecedence:
    def test_header_profile_returned_directly(self) -> None:
        resolver = _make_resolver()
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile="coder",
        )
        assert result == "coder"

    def test_header_wins_over_agent_registry(self) -> None:
        # Even if agent has a different profile in registry, header wins.
        getter = MagicMock(return_value="reviewer")
        resolver = _make_resolver(getter=getter)
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile="coder",
        )
        assert result == "coder"
        getter.assert_not_called()  # getter not invoked when header present

    def test_header_wins_over_default(self) -> None:
        resolver = _make_resolver(default_profile="full")
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile="reviewer",
        )
        assert result == "reviewer"


# ---------------------------------------------------------------------------
# Precedence 2: Agent-registry lookup
# ---------------------------------------------------------------------------

class TestAgentRegistryPrecedence:
    def test_registered_agent_profile_returned(self) -> None:
        getter = MagicMock(return_value="reviewer")
        resolver = _make_resolver(getter=getter)
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile=None,
        )
        assert result == "reviewer"
        getter.assert_called_once_with("proj1", "agent1")

    def test_registered_agent_empty_string_falls_through_to_default(self) -> None:
        getter = MagicMock(return_value="")
        resolver = _make_resolver(getter=getter, default_profile="full")
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile=None,
        )
        assert result == "full"

    def test_registered_agent_none_falls_through_to_default(self) -> None:
        getter = MagicMock(return_value=None)
        resolver = _make_resolver(getter=getter, default_profile="full")
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile=None,
        )
        assert result == "full"


# ---------------------------------------------------------------------------
# Precedence 3: Server default
# ---------------------------------------------------------------------------

class TestServerDefault:
    def test_unregistered_agent_returns_default(self) -> None:
        getter = MagicMock(return_value=None)
        resolver = _make_resolver(getter=getter, default_profile="full")
        result = resolver.resolve(
            project_id="proj1",
            agent_id="unknown_agent",
            header_profile=None,
        )
        assert result == "full"

    def test_no_getter_returns_default(self) -> None:
        resolver = _make_resolver(getter=None, default_profile="full")
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile=None,
        )
        assert result == "full"

    def test_custom_default_profile(self) -> None:
        resolver = _make_resolver(getter=None, default_profile="reviewer")
        result = resolver.resolve(
            project_id="proj1",
            agent_id="agent1",
            header_profile=None,
        )
        assert result == "reviewer"

    def test_default_profile_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_DEFAULT_PROFILE", "coder")
        resolver = ProfileResolver(
            registry=_make_registry(),
            agent_profile_getter=None,
            default_profile=None,  # should read from env
        )
        result = resolver.resolve(project_id="p", agent_id="a", header_profile=None)
        assert result == "coder"

    def test_default_is_full_when_env_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_DEFAULT_PROFILE", raising=False)
        resolver = ProfileResolver(
            registry=_make_registry(),
            agent_profile_getter=None,
            default_profile=None,
        )
        result = resolver.resolve(project_id="p", agent_id="a", header_profile=None)
        assert result == "full"


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

class TestTtlCache:
    def test_second_call_hits_cache(self) -> None:
        getter = MagicMock(return_value="coder")
        resolver = _make_resolver(getter=getter, cache_ttl=60.0)

        resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)
        resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)

        # DB getter called only once — second call hit the cache.
        getter.assert_called_once()

    def test_different_keys_each_query_db(self) -> None:
        getter = MagicMock(return_value="coder")
        resolver = _make_resolver(getter=getter, cache_ttl=60.0)

        resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)
        resolver.resolve(project_id="proj1", agent_id="agent2", header_profile=None)

        assert getter.call_count == 2

    def test_expired_entry_refetched(self) -> None:
        getter = MagicMock(side_effect=["coder", "reviewer"])
        resolver = _make_resolver(getter=getter, cache_ttl=0.0)

        result1 = resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)
        time.sleep(0.01)  # ensure monotonic clock advances past TTL=0
        result2 = resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)

        assert result1 == "coder"
        assert result2 == "reviewer"
        assert getter.call_count == 2

    def test_invalidate_forces_refetch(self) -> None:
        getter = MagicMock(side_effect=["coder", "reviewer"])
        resolver = _make_resolver(getter=getter, cache_ttl=60.0)

        result1 = resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)
        resolver.invalidate("proj1", "agent1")
        result2 = resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)

        assert result1 == "coder"
        assert result2 == "reviewer"

    def test_invalidate_nonexistent_key_is_noop(self) -> None:
        resolver = _make_resolver()
        # Should not raise.
        resolver.invalidate("no_project", "no_agent")

    def test_cache_stats_initial(self) -> None:
        resolver = _make_resolver()
        stats = resolver.cache_stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0.0

    def test_cache_stats_after_miss_and_hit(self) -> None:
        getter = MagicMock(return_value="coder")
        resolver = _make_resolver(getter=getter, cache_ttl=60.0)

        resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)  # miss
        resolver.resolve(project_id="proj1", agent_id="agent1", header_profile=None)  # hit

        stats = resolver.cache_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Unknown-profile header rejection (validated by the registry in the middleware)
# ---------------------------------------------------------------------------

class TestUnknownProfileValidation:
    """Verify that UnknownProfileError is raised by the registry so the
    McpTenantMiddleware can catch it and return a 400.  ProfileResolver
    itself trusts that header_profile is already validated — this test
    exercises the registry validation path that the middleware calls first.
    """

    def test_registry_raises_for_unknown_profile(self) -> None:
        reg = _make_registry()
        with pytest.raises(UnknownProfileError) as exc_info:
            reg.get("nonexistent_profile")
        err = exc_info.value
        assert err.name == "nonexistent_profile"
        assert isinstance(err.available, list)

    def test_registry_raises_with_available_list(self) -> None:
        reg = _make_registry()
        with pytest.raises(UnknownProfileError) as exc_info:
            reg.get("invalid")
        assert "full" in exc_info.value.available
        assert "coder" in exc_info.value.available


# ---------------------------------------------------------------------------
# REQUEST_PROFILE contextvar exported from mcp_server
# ---------------------------------------------------------------------------

class TestRequestProfileContextvar:
    def test_contextvar_exported(self) -> None:
        import contextvars

        from tapps_brain import mcp_server as _mcp_mod
        assert hasattr(_mcp_mod, "REQUEST_PROFILE")
        assert isinstance(_mcp_mod.REQUEST_PROFILE, contextvars.ContextVar)

    def test_contextvar_default_is_none(self) -> None:
        from tapps_brain import mcp_server as _mcp_mod
        assert _mcp_mod.REQUEST_PROFILE.get() is None

    def test_contextvar_can_be_set_and_reset(self) -> None:
        from tapps_brain import mcp_server as _mcp_mod
        token = _mcp_mod.REQUEST_PROFILE.set("coder")
        try:
            assert _mcp_mod.REQUEST_PROFILE.get() == "coder"
        finally:
            _mcp_mod.REQUEST_PROFILE.reset(token)
        assert _mcp_mod.REQUEST_PROFILE.get() is None
