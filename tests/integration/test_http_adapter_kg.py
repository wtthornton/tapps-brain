"""Integration tests for /v1/kg/* UUID validation (TAP-2140).

Non-UUID strings POSTed to UUID-bound fields previously surfaced as HTTP 500
with a raw psycopg.errors.InvalidTextRepresentation traceback because UUID
validation only happened when the DB cursor bound the parameter. These tests
lock in the new behavior: 422 with a typed field-level error, and no psycopg
substring leakage in the response body.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("httpx")
pytest.importorskip("fastapi")

import httpx

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
        transport = httpx.ASGITransport(app=app)
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
