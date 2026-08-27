"""VAL-25-flag (TAP-6696): TAPPS_BRAIN_STRICT_IDENTITY, default OFF.

Scope note (this lane covers the flag itself, not the deployed-brain flip or
the AgentForge/tapps-mcp pin — see docker/.env.example and CLAUDE.md's env
table for the rollout sequencing).

- Off (default / unset): a write with X-Agent-Id: default or no X-Agent-Id
  header at all behaves exactly as before.
- On: both are refused with a 400 identity_required envelope naming the
  anonymous agent_id.
"""

from __future__ import annotations

import pytest

from tapps_brain.http.middleware import strict_identity_refusal
from tapps_brain.http.settings import is_strict_identity_enabled


class TestFlagReader:
    def test_unset_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_IDENTITY", raising=False)
        assert is_strict_identity_enabled() is False

    def test_zero_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_IDENTITY", "0")
        assert is_strict_identity_enabled() is False

    def test_one_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_IDENTITY", "1")
        assert is_strict_identity_enabled() is True


class TestStrictIdentityRefusal:
    def test_flag_off_never_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_IDENTITY", raising=False)
        assert strict_identity_refusal("unknown") is None
        assert strict_identity_refusal("default") is None
        assert strict_identity_refusal("real-agent-name") is None

    def test_flag_on_refuses_implicit_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_IDENTITY", "1")
        refusal = strict_identity_refusal("unknown")
        assert refusal is not None
        assert refusal["error"] == "identity_required"
        assert refusal["agent_id"] == "unknown"

    def test_flag_on_refuses_explicit_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_IDENTITY", "1")
        refusal = strict_identity_refusal("default")
        assert refusal is not None
        assert refusal["error"] == "identity_required"
        assert refusal["agent_id"] == "default"

    def test_flag_on_permits_real_agent_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_IDENTITY", "1")
        assert strict_identity_refusal("claude-code-wtthornton") is None
