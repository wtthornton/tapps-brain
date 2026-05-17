"""Tests for the TAP-1920 AgentForge consumer-feedback children.

Covers TAP-1929 (REST profile gating), TAP-1930 (utility_score on edge
feedback), TAP-1932 (native list shapes on brain_record_event), TAP-1933
(configurable max_hops), TAP-1934 (POST /v1/experience:batch validation),
TAP-1936 (dual agent_id disambiguation), and TAP-1940 (evidence body size).
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# TAP-1936 — _resolve_per_call_agent_id warns on header/kwarg mismatch
# ---------------------------------------------------------------------------


class TestAgentIdDisambiguation:
    def test_returns_kwarg_when_only_kwarg_set(self) -> None:
        from tapps_brain.mcp_server.context import _resolve_per_call_agent_id

        assert _resolve_per_call_agent_id("kwarg-agent", default="server") == "kwarg-agent"

    def test_returns_default_when_nothing_set(self) -> None:
        from tapps_brain.mcp_server.context import _resolve_per_call_agent_id

        assert _resolve_per_call_agent_id("", default="server") == "server"

    def test_returns_contextvar_when_only_header_set(self) -> None:
        from tapps_brain.mcp_server import context as ctx_mod

        token = ctx_mod.REQUEST_AGENT_ID.set("header-agent")
        try:
            assert ctx_mod._resolve_per_call_agent_id("", default="server") == "header-agent"
        finally:
            ctx_mod.REQUEST_AGENT_ID.reset(token)

    def test_kwarg_wins_when_both_agree(self) -> None:
        from tapps_brain.mcp_server import context as ctx_mod

        token = ctx_mod.REQUEST_AGENT_ID.set("same-agent")
        try:
            assert (
                ctx_mod._resolve_per_call_agent_id("same-agent", default="server") == "same-agent"
            )
        finally:
            ctx_mod.REQUEST_AGENT_ID.reset(token)

    def test_disagree_logs_warning_and_kwarg_wins(self) -> None:
        from tapps_brain.mcp_server import context as ctx_mod

        # The module uses structlog (lazy-imported via _get_logger). Patch
        # the lazy getter to return a recording mock so we can assert on
        # the warning emission without depending on logging propagation.
        warnings_seen: list[tuple[str, dict[str, Any]]] = []

        class _RecorderLogger:
            def warning(self, event: str, **fields: Any) -> None:
                warnings_seen.append((event, fields))

            def debug(self, *_a: Any, **_kw: Any) -> None:
                return

            def info(self, *_a: Any, **_kw: Any) -> None:
                return

        with mock.patch.object(ctx_mod, "_get_logger", lambda: _RecorderLogger()):
            token = ctx_mod.REQUEST_AGENT_ID.set("header-agent")
            try:
                resolved = ctx_mod._resolve_per_call_agent_id("kwarg-agent", default="server")
            finally:
                ctx_mod.REQUEST_AGENT_ID.reset(token)

        assert resolved == "kwarg-agent"
        events = [event for event, _ in warnings_seen]
        assert "agent_id.mismatch" in events
        fields = next(f for e, f in warnings_seen if e == "agent_id.mismatch")
        assert fields["kwarg_agent_id"] == "kwarg-agent"
        assert fields["header_agent_id"] == "header-agent"

    def test_strict_mode_raises_on_disagreement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.mcp_server import context as ctx_mod

        monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")
        token = ctx_mod.REQUEST_AGENT_ID.set("header-agent")
        try:
            with pytest.raises(ValueError, match="agent_id mismatch"):
                ctx_mod._resolve_per_call_agent_id("kwarg-agent", default="server")
        finally:
            ctx_mod.REQUEST_AGENT_ID.reset(token)


# ---------------------------------------------------------------------------
# TAP-1932 — native list shapes coercion helpers
# ---------------------------------------------------------------------------


class TestNativeListCoercion:
    def test_native_payload_wins_over_legacy_json(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        assert _coerce_payload({"a": 1}, '{"b": 2}') == {"a": 1}

    def test_legacy_payload_json_falls_back_with_warning(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        with pytest.warns(DeprecationWarning):
            assert _coerce_payload(None, '{"x": 42}') == {"x": 42}

    def test_empty_payload_returns_dict(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_payload

        assert _coerce_payload(None, "") == {}
        assert _coerce_payload(None, "{}") == {}

    def test_native_list_wins_over_legacy_json(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        native = [{"a": 1}]
        assert _coerce_list(native, '[{"b": 2}]', "entities") == native

    def test_legacy_list_json_falls_back_with_warning(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        with pytest.warns(DeprecationWarning):
            result = _coerce_list(None, '[{"x": 1}]', "entities")
        assert result == [{"x": 1}]

    def test_empty_list_returns_empty(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        assert _coerce_list(None, "", "edges") == []
        assert _coerce_list(None, "[]", "edges") == []

    def test_malformed_list_json_returns_empty(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        # Malformed JSON should silently return [] (backward compat with old behaviour)
        assert _coerce_list(None, "not json", "evidence") == []

    def test_filters_non_dict_items(self) -> None:
        from tapps_brain.mcp_server.tools_kg import _coerce_list

        assert _coerce_list([{"ok": 1}, "skip", 42], "", "entities") == [{"ok": 1}]


# ---------------------------------------------------------------------------
# TAP-1933 — configurable max_hops + branching factor
# ---------------------------------------------------------------------------


class TestExplainHopsConfig:
    def test_default_ceiling_is_three(self) -> None:
        from tapps_brain.services.kg_service import explain_max_hops_ceiling

        assert explain_max_hops_ceiling() == 3

    def test_default_branching_factor_is_fifty(self) -> None:
        from tapps_brain.services.kg_service import explain_branching_factor

        assert explain_branching_factor() == 50

    def test_max_hops_ceiling_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.services.kg_service import explain_max_hops_ceiling

        monkeypatch.setenv("TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS", "5")
        assert explain_max_hops_ceiling() == 5

    def test_branching_factor_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.services.kg_service import explain_branching_factor

        monkeypatch.setenv("TAPPS_BRAIN_KG_EXPLAIN_BRANCHING_FACTOR", "200")
        assert explain_branching_factor() == 200

    def test_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.services.kg_service import explain_max_hops_ceiling

        monkeypatch.setenv("TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS", "not-a-number")
        assert explain_max_hops_ceiling() == 3

    def test_minimum_is_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tapps_brain.services.kg_service import explain_max_hops_ceiling

        monkeypatch.setenv("TAPPS_BRAIN_KG_EXPLAIN_MAX_HOPS", "0")
        assert explain_max_hops_ceiling() == 1


# ---------------------------------------------------------------------------
# TAP-1930 — utility_score validation on record_kg_feedback
# ---------------------------------------------------------------------------


class TestEdgeUtilityScore:
    def test_invalid_feedback_type_rejected(self) -> None:
        from tapps_brain.services.kg_service import record_kg_feedback

        result = record_kg_feedback(
            store=mock.Mock(),
            project_id="p",
            agent_id="a",
            edge_id="e",
            feedback_type="not_valid",
        )
        assert result["error"] == "bad_request"

    def test_utility_score_out_of_range_returns_400(self) -> None:
        from tapps_brain.services.kg_service import record_kg_feedback

        result = record_kg_feedback(
            store=mock.Mock(),
            project_id="p",
            agent_id="a",
            edge_id="e",
            feedback_type="edge_helpful",
            utility_score=1.5,
        )
        assert result["error"] == "bad_request"
        assert "utility_score" in result["detail"]

    def test_utility_score_negative_out_of_range_returns_400(self) -> None:
        from tapps_brain.services.kg_service import record_kg_feedback

        result = record_kg_feedback(
            store=mock.Mock(),
            project_id="p",
            agent_id="a",
            edge_id="e",
            feedback_type="edge_misleading",
            utility_score=-1.5,
        )
        assert result["error"] == "bad_request"

    def test_utility_score_boundary_values_accepted(self) -> None:
        from tapps_brain.services.kg_service import record_kg_feedback

        # Mock feedback_service.feedback_record + the CM resolver
        with (
            mock.patch(
                "tapps_brain.services.feedback_service.feedback_record",
                return_value={"recorded": True},
            ),
            mock.patch(
                "tapps_brain.services.kg_service._get_or_create_cm",
                return_value=None,
            ),
        ):
            for score in (-1.0, 0.0, 0.5, 1.0):
                result = record_kg_feedback(
                    store=mock.Mock(),
                    project_id="p",
                    agent_id="a",
                    edge_id="e",
                    feedback_type="edge_helpful",
                    utility_score=score,
                )
                assert result.get("error") is None, f"score {score} should be accepted"

    def test_nonzero_score_weights_misleading_delta(self) -> None:
        """|utility_score| * 0.1 replaces the legacy 0.05 default on edge_misleading."""
        from tapps_brain.services import kg_service

        captured: dict[str, float] = {}

        class _StubKg:
            def __init__(self, *_a: Any, **_kw: Any) -> None:
                return

            def apply_edge_feedback(
                self, edge_id: str, feedback_type: str, confidence_delta: float
            ) -> dict[str, Any]:
                captured["delta"] = float(confidence_delta)
                return {"applied": True}

            def close(self) -> None:
                return

        with (
            mock.patch(
                "tapps_brain.services.feedback_service.feedback_record",
                return_value={"recorded": True},
            ),
            mock.patch(
                "tapps_brain.services.kg_service._get_or_create_cm",
                return_value=object(),
            ),
            mock.patch("tapps_brain.postgres_kg.PostgresKnowledgeGraphStore", _StubKg),
        ):
            kg_service.record_kg_feedback(
                store=mock.Mock(),
                project_id="p",
                agent_id="a",
                edge_id="e",
                feedback_type="edge_misleading",
                utility_score=1.0,
            )
            assert captured["delta"] == pytest.approx(0.1)

            kg_service.record_kg_feedback(
                store=mock.Mock(),
                project_id="p",
                agent_id="a",
                edge_id="e",
                feedback_type="edge_misleading",
                utility_score=0.3,
            )
            assert captured["delta"] == pytest.approx(0.03)

    def test_explicit_zero_score_keeps_legacy_default(self) -> None:
        """utility_score=0.0 is "no useful signal" — fixed 0.05 step applies.

        Regression guard for the transport-divergence bug found in review:
        MCP used to coerce 0.0 → None and REST passed 0.0 raw, so the same
        input produced two different deltas.  Both transports now pass the
        raw value; service layer ignores explicit 0.0 to preserve the legacy
        misleading penalty.
        """
        from tapps_brain.services import kg_service

        captured: dict[str, float] = {}

        class _StubKg:
            def __init__(self, *_a: Any, **_kw: Any) -> None:
                return

            def apply_edge_feedback(
                self, edge_id: str, feedback_type: str, confidence_delta: float
            ) -> dict[str, Any]:
                captured["delta"] = float(confidence_delta)
                return {"applied": True}

            def close(self) -> None:
                return

        with (
            mock.patch(
                "tapps_brain.services.feedback_service.feedback_record",
                return_value={"recorded": True},
            ),
            mock.patch(
                "tapps_brain.services.kg_service._get_or_create_cm",
                return_value=object(),
            ),
            mock.patch("tapps_brain.postgres_kg.PostgresKnowledgeGraphStore", _StubKg),
        ):
            kg_service.record_kg_feedback(
                store=mock.Mock(),
                project_id="p",
                agent_id="a",
                edge_id="e",
                feedback_type="edge_misleading",
                utility_score=0.0,
            )
            assert captured["delta"] == pytest.approx(0.05), "explicit 0.0 → legacy step"

            kg_service.record_kg_feedback(
                store=mock.Mock(),
                project_id="p",
                agent_id="a",
                edge_id="e",
                feedback_type="edge_misleading",
                utility_score=None,
            )
            assert captured["delta"] == pytest.approx(0.05), "None → legacy step"


# ---------------------------------------------------------------------------
# TAP-1934 — record_events_batch validation
# ---------------------------------------------------------------------------


class TestExperienceBatchValidation:
    def test_non_list_events_returns_400(self) -> None:
        from tapps_brain.services.kg_service import record_events_batch

        result = record_events_batch(
            cm=mock.Mock(),
            project_id="p",
            brain_id="b",
            agent_id="a",
            events="not a list",  # type: ignore[arg-type]
        )
        assert result["error"] == "bad_request"

    def test_empty_events_returns_400(self) -> None:
        from tapps_brain.services.kg_service import record_events_batch

        result = record_events_batch(
            cm=mock.Mock(),
            project_id="p",
            brain_id="b",
            agent_id="a",
            events=[],
        )
        assert result["error"] == "bad_request"

    def test_event_without_event_type_returns_400(self) -> None:
        from tapps_brain.services.kg_service import record_events_batch

        result = record_events_batch(
            cm=mock.Mock(),
            project_id="p",
            brain_id="b",
            agent_id="a",
            events=[{"event_type": "ok"}, {"event_type": ""}],
        )
        assert result["error"] == "bad_request"
        assert "events[1]" in result["detail"]


# ---------------------------------------------------------------------------
# TAP-1929 — REST profile gate route mapping + drift detection
# ---------------------------------------------------------------------------


class TestRestProfileGate:
    def test_known_route_maps_to_tool(self) -> None:
        from tapps_brain.http.rest_profile_gate import resolve_tool_for_path

        assert resolve_tool_for_path("/v1/remember") == "brain_remember"
        assert resolve_tool_for_path("/v1/experience") == "brain_record_event"
        assert resolve_tool_for_path("/v1/experience:batch") == "brain_record_event"
        assert resolve_tool_for_path("/v1/kg/feedback") == "brain_record_feedback"

    def test_unknown_route_returns_none(self) -> None:
        from tapps_brain.http.rest_profile_gate import resolve_tool_for_path

        assert resolve_tool_for_path("/v1/admin/something") is None
        assert resolve_tool_for_path("/mcp") is None
        assert resolve_tool_for_path("/health") is None

    def test_public_paths_includes_tools_list(self) -> None:
        from tapps_brain.http.rest_profile_gate import PUBLIC_PATHS

        assert "/v1/tools/list" in PUBLIC_PATHS
        assert "/health" in PUBLIC_PATHS
        assert "/metrics" in PUBLIC_PATHS

    def test_validate_route_map_accepts_complete_catalog(self) -> None:
        from tapps_brain.http.rest_profile_gate import (
            REST_ROUTE_TO_TOOL,
            validate_rest_route_map,
        )

        known = frozenset(REST_ROUTE_TO_TOOL.values())
        # No raise expected when every mapped tool is known.
        validate_rest_route_map(known)

    def test_validate_route_map_raises_on_drift(self) -> None:
        from tapps_brain.http.rest_profile_gate import validate_rest_route_map

        with pytest.raises(ValueError, match="REST route drift"):
            validate_rest_route_map(frozenset())  # empty catalog → every route drifts

    def test_out_of_profile_response_body_shape(self) -> None:
        from tapps_brain.http.rest_profile_gate import out_of_profile_response_body

        body = out_of_profile_response_body(tool="brain_forget", profile="reviewer")
        assert body["error"] == "out_of_profile"
        assert body["data"]["reason"] == "out_of_profile"
        assert body["data"]["tool"] == "brain_forget"
        assert body["data"]["profile"] == "reviewer"
        assert "detail" in body

    def test_every_mapped_tool_exists_in_canonical_profile(self) -> None:
        """Drift guard: every REST-mapped tool must exist in the `full` profile."""
        from tapps_brain.http.rest_profile_gate import REST_ROUTE_TO_TOOL
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry

        registry = ProfileRegistry()
        full_tools = registry.get("full")
        for route, tool in REST_ROUTE_TO_TOOL.items():
            assert tool in full_tools, f"{route!r} → {tool!r} not in `full` profile"


# ---------------------------------------------------------------------------
# TAP-1940 — evidence body size constants
# ---------------------------------------------------------------------------


class TestEvidenceBodyLimit:
    def test_experience_limit_is_higher_than_kg(self) -> None:
        from tapps_brain.http_adapter import (
            _EXPERIENCE_MAX_BODY_BYTES,
            _KG_MAX_BODY_BYTES,
        )

        assert _EXPERIENCE_MAX_BODY_BYTES > _KG_MAX_BODY_BYTES
        assert _EXPERIENCE_MAX_BODY_BYTES == 262_144  # 256 KB

    def test_experience_batch_limit_is_higher_than_single(self) -> None:
        from tapps_brain.http_adapter import (
            _EXPERIENCE_BATCH_MAX_BODY_BYTES,
            _EXPERIENCE_MAX_BODY_BYTES,
        )

        assert _EXPERIENCE_BATCH_MAX_BODY_BYTES > _EXPERIENCE_MAX_BODY_BYTES
        assert _EXPERIENCE_BATCH_MAX_BODY_BYTES == 1_048_576  # 1 MiB
