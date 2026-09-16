"""TAP-7295: the tenant agent-axis gate must refuse ``X-Agent-Id: default``.

``STRICT_REFUSED_AGENT_LITERALS`` in ``project_resolver.py`` previously only
listed ``"unknown"``, while ``_ANONYMOUS_AGENT_IDS`` in ``http/middleware.py``
(the pre-existing ``TAPPS_BRAIN_STRICT_IDENTITY`` gate) already refused both
``"unknown"`` and ``"default"``. This left an inconsistency on the
``resolve_tenant_or_refuse`` (TAP-7243) choke point: an explicit
``X-Agent-Id: default`` sailed through under ``TAPPS_BRAIN_STRICT_AGENT_ID=1``
even though the literal is exactly the kind of anonymous placeholder that
flag exists to refuse.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from tapps_brain.http.middleware import resolve_tenant_or_refuse


def _request_with_agent_header(agent_id: str | None) -> Request:
    headers = [(b"x-project-id", b"acme-widgets")]
    if agent_id is not None:
        headers.append((b"x-agent-id", agent_id.encode()))
    return Request({"type": "http", "headers": headers})


class TestStrictAgentIdRefusesLiteralDefault:
    def test_literal_default_agent_id_refused_under_strict_agent_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VAL-1: 'default' is refused with the same envelope as 'unknown'."""
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")

        _, _, refusal = resolve_tenant_or_refuse(_request_with_agent_header("default"))
        assert refusal is not None
        assert refusal.status_code == 400
        assert refusal.detail["code"] == "tenant_agent_literal"
        assert refusal.detail["gate"] == "tenant_scope"

        # Same envelope shape 'unknown' produces on the same axis.
        _, _, unknown_refusal = resolve_tenant_or_refuse(_request_with_agent_header("unknown"))
        assert unknown_refusal is not None
        assert unknown_refusal.detail["code"] == refusal.detail["code"]
        assert unknown_refusal.status_code == refusal.status_code

    def test_literal_default_agent_id_passes_when_flag_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative control: with the flag unset, 'default' behaves exactly
        as it does on base — no 400. A test that passes in both flag states
        would not be testing the gate."""
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_AGENT_ID", raising=False)

        _, agent_id, refusal = resolve_tenant_or_refuse(_request_with_agent_header("default"))
        assert refusal is None
        assert agent_id == "default"

    def test_real_agent_id_still_passes_under_strict_agent_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VAL-2 positive control: a genuine agent id is unaffected by the
        flag, so VAL-1 isn't passing because everything gets refused."""
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")

        _, agent_id, refusal = resolve_tenant_or_refuse(_request_with_agent_header("ci-runner-7"))
        assert refusal is None
        assert agent_id == "ci-runner-7"
