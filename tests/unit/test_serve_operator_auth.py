"""EPIC-078 — constant-time operator MCP bearer auth in serve.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.cli.serve import operator_mcp_bearer_ok


class TestOperatorMcpBearerOk:
    def test_accepts_matching_token(self) -> None:
        assert operator_mcp_bearer_ok("Bearer secret-token", "secret-token") is True

    def test_rejects_missing_bearer_prefix(self) -> None:
        assert operator_mcp_bearer_ok("secret-token", "secret-token") is False

    def test_rejects_wrong_token(self) -> None:
        assert operator_mcp_bearer_ok("Bearer wrong", "secret-token") is False

    def test_uses_hmac_compare_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        compare_spy = MagicMock(return_value=True)
        with patch("tapps_brain.cli.serve.hmac.compare_digest", compare_spy):
            assert operator_mcp_bearer_ok("Bearer abc", "abc") is True
        compare_spy.assert_called_once_with(b"abc", b"abc")
