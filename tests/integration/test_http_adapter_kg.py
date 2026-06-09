"""Integration tests for /v1/kg/* UUID validation (TAP-2140) and the
populate-then-retrieve KG flow (TAP-2723 / TAP-2727).

UUID-validation tests lock in the behaviour that was broken: non-UUID strings
POSTed to UUID-bound fields previously surfaced as HTTP 500 with a raw
psycopg.errors.InvalidTextRepresentation traceback.  The new behaviour is 422
with a typed field-level error and no psycopg substring in the response body.

Populate-then-retrieve tests verify the end-to-end path through the HTTP
adapter using mocked kg_service calls:
  1. POST /v1/kg/resolve_entity  — name → UUID
  2. POST /v1/experience          — record event + edge with that UUID
  3. POST /v1/kg/neighbors        — read back the edge neighbourhood
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("fastapi")

import httpx

import tapps_brain.embeddings as _embeddings_mod
import tapps_brain.http_adapter as _http_mod
from tapps_brain.http_adapter import _service_version, _Settings, create_app

_AUTH_TOKEN = "test-bearer-token"
_HEADERS = {
    "X-Project-Id": "proj",
    "X-Agent-Id": "agent",
    "Authorization": f"Bearer {_AUTH_TOKEN}",
}


def _make_sync_store() -> MagicMock:
    store = MagicMock()
    store.profile = None
    store._project_id = "proj"
    store._agent_id = "agent"
    return store


def _make_settings(*, sync_store: Any = None) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = _AUTH_TOKEN
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = sync_store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    s.idempotency_store = None
    s.async_store = None
    return s


async def _post(path: str, payload: dict[str, Any]) -> httpx.Response:
    settings = _make_settings(sync_store=_make_sync_store())
    _mcp_dummy = MagicMock()
    _mcp_dummy.session_manager = None
    with (
        patch.object(_http_mod, "_settings", settings),
        patch.object(_http_mod, "get_settings", return_value=settings),
    ):
        app = create_app(mcp_server=_mcp_dummy)
        # raise_app_exceptions=False so the test observes the client-facing
        # response (the sanitized 500 from the app's catch-all handler) rather
        # than the exception ServerErrorMiddleware re-raises for the ASGI
        # server to log.  See TestResolveEntityEndpoint
        # ::test_resolve_entity_no_psycopg_in_error_response (TAP-2727).
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload, headers=_HEADERS)


class TestKgFeedbackUuidValidation:
    """`/v1/kg/feedback` — edge_id is kg_edges.id (UUID)."""

    @pytest.mark.asyncio
    async def test_non_uuid_edge_id_returns_422(self) -> None:
        resp = await _post(
            "/v1/kg/feedback",
            {"edge_id": "contract.smoke.edge", "feedback_type": "edge_helpful"},
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.asyncio
    async def test_non_uuid_edge_id_response_does_not_leak_psycopg(self) -> None:
        resp = await _post(
            "/v1/kg/feedback",
            {"edge_id": "contract.smoke.edge", "feedback_type": "edge_helpful"},
        )
        body_lower = resp.text.lower()
        assert "psycopg" not in body_lower
        assert "invalidtextrepresentation" not in body_lower

    @pytest.mark.asyncio
    async def test_non_uuid_edge_id_response_carries_field_locator(self) -> None:
        resp = await _post(
            "/v1/kg/feedback",
            {"edge_id": "not-a-uuid", "feedback_type": "edge_helpful"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("field") == "edge_id"
        assert body.get("error") == "validation_error"


class TestKgExplainUuidValidation:
    """`/v1/kg/explain` — subject_id / object_id are kg_entities.id (UUID)."""

    @pytest.mark.asyncio
    async def test_non_uuid_subject_id_returns_422(self) -> None:
        resp = await _post(
            "/v1/kg/explain",
            {
                "subject_id": "contract.smoke.subject",
                "object_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert resp.status_code == 422, resp.text
        assert "psycopg" not in resp.text.lower()
        assert resp.json().get("field") == "subject_id"

    @pytest.mark.asyncio
    async def test_non_uuid_object_id_returns_422(self) -> None:
        resp = await _post(
            "/v1/kg/explain",
            {
                "subject_id": "00000000-0000-0000-0000-000000000000",
                "object_id": "contract.smoke.object",
            },
        )
        assert resp.status_code == 422, resp.text
        assert resp.json().get("field") == "object_id"


class TestKgNeighborsUuidValidation:
    """`/v1/kg/neighbors` — entity_ids[*] are kg_entities.id (UUID)."""

    @pytest.mark.asyncio
    async def test_non_uuid_entity_id_returns_422(self) -> None:
        resp = await _post(
            "/v1/kg/neighbors",
            {"entity_ids": ["contract.smoke.entity"]},
        )
        assert resp.status_code == 422, resp.text
        assert "psycopg" not in resp.text.lower()
        assert resp.json().get("field") == "entity_ids[0]"

    @pytest.mark.asyncio
    async def test_mixed_uuid_and_non_uuid_returns_422_on_first_invalid(self) -> None:
        resp = await _post(
            "/v1/kg/neighbors",
            {
                "entity_ids": [
                    "00000000-0000-0000-0000-000000000000",
                    "not-a-uuid",
                ],
            },
        )
        assert resp.status_code == 422
        assert resp.json().get("field") == "entity_ids[1]"


# ---------------------------------------------------------------------------
# Populate-then-retrieve flow (TAP-2723 / TAP-2727)
# ---------------------------------------------------------------------------

_ENTITY_UUID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_EDGE_UUID = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
_EVENT_UUID = "c3d4e5f6-a7b8-9012-cdef-123456789012"


def _kg_svc_patches(
    resolve_result: dict[str, Any] | None = None,
    record_result: dict[str, Any] | None = None,
    neighbors_result: dict[str, Any] | None = None,
) -> tuple[Any, Any, Any, Any]:
    """Return four (patcher, patcher, patcher, patcher) for the kg_service calls.

    The four patches are: _get_or_create_cm, resolve_entity, record_event,
    get_neighbors.  Call .start()/.stop() or use as context managers.
    """
    cm_mock = MagicMock()
    p_cm = patch(
        "tapps_brain.services.kg_service._get_or_create_cm",
        return_value=cm_mock,
    )
    p_resolve = patch(
        "tapps_brain.services.kg_service.resolve_entity",
        return_value=resolve_result
        or {
            "entity_id": _ENTITY_UUID,
            "entity_type": "module",
            "canonical_name": "retrieval",
            "created": True,
            "confidence": 0.6,
            "reason": "created",
        },
    )
    p_record = patch(
        "tapps_brain.services.kg_service.record_event",
        return_value=record_result
        or {
            "event_id": _EVENT_UUID,
            "memory_key": None,
            "entity_ids": [_ENTITY_UUID],
            "edge_ids": [_EDGE_UUID],
            "evidence_ids": [],
        },
    )
    p_neighbors = patch(
        "tapps_brain.services.kg_service.get_neighbors",
        return_value=neighbors_result
        or {
            "neighbors": [
                {
                    "edge_id": _EDGE_UUID,
                    "predicate": "uses",
                    "edge_confidence": 0.6,
                    "neighbor_id": _ENTITY_UUID,
                    "entity_type": "module",
                    "canonical_name": "retrieval",
                    "hop": 1,
                }
            ],
            "entity_ids": [_ENTITY_UUID],
        },
    )
    return p_cm, p_resolve, p_record, p_neighbors


class TestResolveEntityEndpoint:
    """`/v1/kg/resolve_entity` — name → UUID (TAP-2725)."""

    @pytest.mark.asyncio
    async def test_resolve_entity_returns_entity_id_uuid(self) -> None:
        p_cm, p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, p_resolve:
            resp = await _post(
                "/v1/kg/resolve_entity",
                {"entity_type": "module", "canonical_name": "retrieval"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entity_id"] == _ENTITY_UUID
        assert body["entity_type"] == "module"
        assert body["canonical_name"] == "retrieval"
        assert body["created"] is True

    @pytest.mark.asyncio
    async def test_resolve_entity_idempotent_returns_same_uuid(self) -> None:
        existing = {
            "entity_id": _ENTITY_UUID,
            "entity_type": "agent",
            "canonical_name": "ralph",
            "created": False,
            "confidence": 0.9,
            "reason": "canonical_match",
        }
        p_cm, p_resolve, _p_record, _p_neighbors = _kg_svc_patches(resolve_result=existing)
        with p_cm, p_resolve:
            resp = await _post(
                "/v1/kg/resolve_entity",
                {"entity_type": "agent", "canonical_name": "ralph"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entity_id"] == _ENTITY_UUID
        assert body["created"] is False
        assert body["reason"] == "canonical_match"

    @pytest.mark.asyncio
    async def test_resolve_entity_missing_entity_type_returns_400(self) -> None:
        resp = await _post("/v1/kg/resolve_entity", {"canonical_name": "retrieval"})
        assert resp.status_code == 400
        assert "entity_type" in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_resolve_entity_missing_canonical_name_returns_400(self) -> None:
        resp = await _post("/v1/kg/resolve_entity", {"entity_type": "module"})
        assert resp.status_code == 400
        assert "canonical_name" in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_resolve_entity_no_psycopg_in_error_response(self) -> None:
        """Service errors must not leak Postgres implementation details."""
        p_cm, _, _p_record, _p_neighbors = _kg_svc_patches()
        with (
            p_cm,
            patch(
                "tapps_brain.services.kg_service.resolve_entity",
                side_effect=RuntimeError("connection lost"),
            ),
        ):
            resp = await _post(
                "/v1/kg/resolve_entity",
                {"entity_type": "module", "canonical_name": "retrieval"},
            )
        # Should be a 5xx error, and the traceback must not leak psycopg details
        assert resp.status_code >= 500
        assert "psycopg" not in resp.text.lower()


class TestResolveEntitiesEndpoint:
    """`/v1/kg/resolve_entities` — batch name → UUID (TAP-3249)."""

    @pytest.mark.asyncio
    async def test_resolve_entities_batch_returns_ordered_results(self) -> None:
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        batch_result = {
            "entity_ids": ["uuid-a", "uuid-b"],
            "results": [
                {
                    "entity_id": "uuid-a",
                    "entity_type": "module",
                    "canonical_name": "retrieval",
                    "created": True,
                },
                {
                    "entity_id": "uuid-b",
                    "entity_type": "agent",
                    "canonical_name": "ralph",
                    "created": False,
                },
            ],
        }
        with (
            p_cm,
            patch(
                "tapps_brain.services.kg_service.resolve_entity_refs",
                return_value=batch_result,
            ),
        ):
            resp = await _post(
                "/v1/kg/resolve_entities",
                {
                    "entity_refs": [
                        {"entity_type": "module", "canonical_name": "retrieval"},
                        {"type": "agent", "id": "ralph"},
                    ]
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["entity_ids"] == ["uuid-a", "uuid-b"]
        assert len(body["results"]) == 2
        assert body["results"][0]["created"] is True
        assert body["results"][1]["created"] is False

    @pytest.mark.asyncio
    async def test_resolve_entities_missing_array_returns_400(self) -> None:
        resp = await _post("/v1/kg/resolve_entities", {})
        assert resp.status_code == 400


class TestKgPopulateThenRetrieve:
    """End-to-end populate-then-retrieve KG flow (TAP-2723 / TAP-2727).

    1. POST /v1/kg/resolve_entity  — obtain a UUID for a named entity.
    2. POST /v1/experience          — record an event with an edge using that UUID.
    3. POST /v1/kg/neighbors        — read back the neighbourhood.

    All calls use the ASGI transport against a real (test) FastAPI app with
    kg_service functions mocked so no live Postgres connection is required.
    """

    @pytest.mark.asyncio
    async def test_record_event_with_valid_entity_uuid_succeeds(self) -> None:
        p_cm, _p_resolve, p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, p_record:
            resp = await _post(
                "/v1/experience",
                {
                    "event_type": "tool_called",
                    "subject_key": "recall.step",
                    "entities": [
                        {
                            "entity_type": "module",
                            "canonical_name": "retrieval",
                        }
                    ],
                    "edges": [
                        {
                            "subject_entity_id": _ENTITY_UUID,
                            "predicate": "uses",
                            "object_entity_id": _ENTITY_UUID,
                        }
                    ],
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("event_id") == _EVENT_UUID
        assert _EDGE_UUID in body.get("edge_ids", [])

    @pytest.mark.asyncio
    async def test_neighbors_returned_for_resolved_entity(self) -> None:
        p_cm, _p_resolve, _p_record, p_neighbors = _kg_svc_patches()
        with p_cm, p_neighbors:
            resp = await _post(
                "/v1/kg/neighbors",
                {"entity_ids": [_ENTITY_UUID], "hops": 1},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        neighbors = body.get("neighbors", [])
        assert len(neighbors) == 1
        assert neighbors[0]["edge_id"] == _EDGE_UUID
        assert neighbors[0]["predicate"] == "uses"
        assert neighbors[0]["hop"] == 1

    @pytest.mark.asyncio
    async def test_full_populate_then_retrieve_flow(self) -> None:
        """Resolve → record event → get neighbours in one test."""
        p_cm, p_resolve, p_record, p_neighbors = _kg_svc_patches()
        with p_cm, p_resolve, p_record, p_neighbors:
            # Step 1: resolve entity name → UUID
            resolve_resp = await _post(
                "/v1/kg/resolve_entity",
                {"entity_type": "module", "canonical_name": "retrieval"},
            )
            assert resolve_resp.status_code == 200, resolve_resp.text
            entity_id = resolve_resp.json()["entity_id"]
            assert entity_id == _ENTITY_UUID

            # Step 2: record an event referencing the resolved entity UUID
            event_resp = await _post(
                "/v1/experience",
                {
                    "event_type": "workflow_completed",
                    "subject_key": "memory.recall",
                    "utility_score": 0.8,
                    "entities": [{"entity_type": "module", "canonical_name": "retrieval"}],
                    "edges": [
                        {
                            "subject_entity_id": entity_id,
                            "predicate": "produces",
                            "object_entity_id": entity_id,
                        }
                    ],
                },
            )
            assert event_resp.status_code == 200, event_resp.text
            assert _EDGE_UUID in event_resp.json().get("edge_ids", [])

            # Step 3: read back the neighbourhood — the edge we recorded should appear
            neighbors_resp = await _post(
                "/v1/kg/neighbors",
                {"entity_ids": [entity_id], "hops": 1, "limit": 10},
            )
            assert neighbors_resp.status_code == 200, neighbors_resp.text
            neighbors = neighbors_resp.json().get("neighbors", [])
            assert len(neighbors) >= 1
            first = neighbors[0]
            assert first["edge_id"] == _EDGE_UUID
            assert first["neighbor_id"] == _ENTITY_UUID


# ---------------------------------------------------------------------------
# Malformed-spec resilience (TAP-2865 de-masking + TAP-2866 resilient writes)
# ---------------------------------------------------------------------------
#
# Regression for the production incident where every POST /v1/experience from
# agent ``nlt-ideas-scout-market-signal`` returned HTTP 500 ``internal_error``
# (brain 3.22.0).  The agent posted ``edges`` entries missing the required
# ``subject_entity_id`` / ``object_entity_id`` UUIDs; ``record_event`` coerced
# them via ``EdgeSpec(**item)`` which raised ``pydantic.ValidationError``, and
# that propagated to the catch-all ``Exception`` handler (TAP-2727) and was
# masked as a generic 500.
#
# TAP-2866 makes the brain *resilient* (no consumer change needed): a malformed
# KG side-effect spec is skipped and reported under ``warnings`` while the core
# experience event still records and returns 200.  A genuinely malformed *core*
# request (e.g. a non-dict ``payload``) still surfaces the de-masked typed 422
# from TAP-2865.
#
# ``_get_or_create_cm`` is patched so ``_get_kg_cm_or_503`` resolves and
# ``record_event`` runs for real against a MagicMock connection manager;
# ``get_embedding_provider`` is patched to None so no embedding model loads.

# The exact production shape: predicate + metadata, no entity UUIDs.
_BAD_EDGE_BODY = {
    "event_type": "approach_failed",
    "edges": [
        {
            "predicate": "agent_solved_problem",
            "confidence": 0.5,
            "metadata": {"retry_count": 0},
        }
    ],
}


def _no_embedding() -> Any:
    """Patch get_embedding_provider → None so the real recorder loads no model."""
    return patch.object(_embeddings_mod, "get_embedding_provider", return_value=None)


class TestExperienceMalformedSpec:
    """`/v1/experience` — malformed side-effects are non-fatal (TAP-2866)."""

    @pytest.mark.asyncio
    async def test_edge_missing_entity_ids_records_event_with_warning(self) -> None:
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, _no_embedding():
            resp = await _post("/v1/experience", _BAD_EDGE_BODY)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Core event still recorded.
        assert body.get("event_id")
        # The malformed edge is reported, not silently dropped.
        warnings = body.get("warnings", [])
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "edge"
        errors = warnings[0]["errors"]
        assert any(
            err.get("field") in {"subject_entity_id", "subject_key", ""}
            or "subject" in err.get("msg", "")
            for err in errors
        )

    @pytest.mark.asyncio
    async def test_edge_missing_entity_ids_is_not_500_or_422(self) -> None:
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, _no_embedding():
            resp = await _post("/v1/experience", _BAD_EDGE_BODY)
        assert resp.status_code == 200
        assert "internal_error" not in resp.text

    @pytest.mark.asyncio
    async def test_warning_does_not_echo_caller_payload(self) -> None:
        """include_input=False — warnings must not reflect the posted payload."""
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, _no_embedding():
            resp = await _post("/v1/experience", _BAD_EDGE_BODY)
        assert "agent_solved_problem" not in resp.text
        assert "retry_count" not in resp.text

    @pytest.mark.asyncio
    async def test_batch_malformed_edge_records_event_with_warning(self) -> None:
        """`/v1/experience:batch` shares the resilient coercion path."""
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, _no_embedding():
            resp = await _post("/v1/experience:batch", {"events": [_BAD_EDGE_BODY]})
        assert resp.status_code == 200, resp.text
        results = resp.json().get("results", [])
        assert len(results) == 1
        assert results[0].get("event_id")
        assert results[0].get("warnings", [])[0]["kind"] == "edge"

    @pytest.mark.asyncio
    async def test_evidence_without_attachment_records_event_with_warning(self) -> None:
        """TAP-2868: the AgentForge payload — evidence with neither edge_id nor
        entity_id — was a DB CheckViolation 500; now skipped + warned (200)."""
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, _no_embedding():
            resp = await _post(
                "/v1/experience",
                {
                    "event_type": "approach_failed",
                    "evidence": [{"source_type": "agent", "quote": "no attachment"}],
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("event_id")
        assert body.get("warnings", [])[0]["kind"] == "evidence"
        assert "internal_error" not in resp.text


class TestExperienceCoreValidation:
    """`/v1/experience` — a malformed *core* request still returns typed 422."""

    @pytest.mark.asyncio
    async def test_non_dict_payload_returns_422_not_500(self) -> None:
        p_cm, _p_resolve, _p_record, _p_neighbors = _kg_svc_patches()
        with p_cm, _no_embedding():
            resp = await _post(
                "/v1/experience",
                {"event_type": "tool_called", "payload": "not-a-dict"},
            )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body.get("error") == "validation_error"
        assert "internal_error" not in resp.text


class TestObservability:
    """TAP-2866 — error counter + deep readiness probe surface broken paths."""

    def test_http_error_counter_appears_in_metrics(self) -> None:
        _http_mod._record_http_error("/v1/experience", 500)
        text = _http_mod._collect_metrics(None)
        assert 'tapps_brain_http_errors_total{path="/v1/experience",status="500"}' in text

    def test_metrics_includes_experience_writable_gauge(self) -> None:
        text = _http_mod._collect_metrics(None)
        assert "tapps_brain_experience_writable" in text

    def test_probe_experience_schema_no_dsn(self) -> None:
        ok, detail = _http_mod._probe_experience_schema(None)
        assert ok is False
        assert "no DSN" in detail
