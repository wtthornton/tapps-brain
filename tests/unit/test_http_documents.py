"""HTTP contract tests for the /v1/documents routes (TAP-5003).

Drives ``create_app()`` with a stubbed store + in-memory fake document
store — verifies status-code mapping (400 / 404 / 413 / 503) and the
happy paths for put / get / list / search / delete.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import tapps_brain.http_adapter as _adapter_mod
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)
from tests.unit.test_document_service import FakeDocumentStore, FakeEmbedder

_HEADERS = {"X-Project-Id": "test-proj", "X-Agent-Id": "test-agent"}


def _make_store(doc_store: FakeDocumentStore | None) -> MagicMock:
    store = MagicMock()
    store._profile = None
    store._metrics = None
    store._embedding_provider = FakeEmbedder()
    store.document_store = lambda: doc_store
    # Match the request tenant so _get_store_for_project reuses this store
    # instead of building a fresh MemoryStore for the project.
    store._tapps_project_id = _HEADERS["X-Project-Id"]
    store._project_id = _HEADERS["X-Project-Id"]
    store._agent_id = _HEADERS["X-Agent-Id"]
    return store


def _make_settings(*, store: Any = None) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = None
    s.admin_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


@contextmanager
def _client(store: Any):  # type: ignore[no-untyped-def]
    settings = _make_settings(store=store)
    mcp_dummy = MagicMock()
    mcp_dummy.session_manager = None
    with (
        patch.object(_adapter_mod, "_settings", settings),
        patch.object(_adapter_mod, "get_settings", return_value=settings),
    ):
        app = create_app(store=store, mcp_server=mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


class TestDocumentsPutRoute:
    def test_put_stores_and_indexes(self) -> None:
        doc_store = FakeDocumentStore()
        with _client(_make_store(doc_store)) as client:
            resp = client.put(
                "/v1/documents",
                json={"title": "Report", "content": "durable knowledge body", "tags": ["r1"]},
                headers=_HEADERS,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "stored"
        assert body["index_status"] == "indexed"
        assert doc_store.count() == 1

    def test_put_requires_project_header(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.put("/v1/documents", json={"title": "t", "content": "c"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_put_rejects_non_list_tags(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.put(
                "/v1/documents",
                json={"title": "t", "content": "c", "tags": "not-a-list"},
                headers=_HEADERS,
            )
        assert resp.status_code == 400

    def test_put_oversize_content_is_413_document_too_large(self) -> None:
        # Default documents.max_doc_bytes is 2 MiB; the HTTP body ceiling is
        # 3 MiB, so 2.2 MiB of content passes transport and fails the cap.
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.put(
                "/v1/documents",
                json={"title": "big", "content": "x" * 2_300_000},
                headers=_HEADERS,
            )
        assert resp.status_code == 413
        assert resp.json()["error"] == "document_too_large"

    def test_put_oversize_body_is_413_payload_too_large(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.put(
                "/v1/documents",
                json={"title": "huge", "content": "x" * 3_200_000},
                headers=_HEADERS,
            )
        assert resp.status_code == 413
        assert resp.json()["error"] == "payload_too_large"

    def test_put_without_document_store_is_503(self) -> None:
        with _client(_make_store(None)) as client:
            resp = client.put(
                "/v1/documents",
                json={"title": "t", "content": "c"},
                headers=_HEADERS,
            )
        assert resp.status_code == 503
        assert resp.json()["error"] == "documents_unavailable"


class TestDocumentsReadRoutes:
    def _seed(self, client: Any) -> str:
        resp = client.put(
            "/v1/documents",
            json={"title": "Seeded", "content": "seeded searchable content", "tags": ["seed"]},
            headers=_HEADERS,
        )
        return str(resp.json()["doc_id"])

    def test_get_returns_document(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            doc_id = self._seed(client)
            resp = client.get(f"/v1/documents/{doc_id}", headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["content"] == "seeded searchable content"

    def test_get_meta_only_omits_content(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            doc_id = self._seed(client)
            resp = client.get(f"/v1/documents/{doc_id}?meta_only=1", headers=_HEADERS)
        assert resp.status_code == 200
        assert "content" not in resp.json()

    def test_get_unknown_is_404(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.get("/v1/documents/nope", headers=_HEADERS)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_list_filters_by_tag(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            self._seed(client)
            resp = client.get("/v1/documents?tag=seed", headers=_HEADERS)
            missing = client.get("/v1/documents?tag=other", headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert missing.json()["count"] == 0

    def test_search_returns_hits(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            self._seed(client)
            resp = client.post(
                "/v1/documents:search",
                json={"query": "seeded searchable", "limit": 5},
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] >= 1
        assert body["results"][0]["title"] == "Seeded"

    def test_search_blank_query_is_400(self) -> None:
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.post("/v1/documents:search", json={"query": " "}, headers=_HEADERS)
        assert resp.status_code == 400

    def test_delete_removes_document(self) -> None:
        doc_store = FakeDocumentStore()
        with _client(_make_store(doc_store)) as client:
            doc_id = self._seed(client)
            resp = client.delete(f"/v1/documents/{doc_id}", headers=_HEADERS)
            missing = client.delete(f"/v1/documents/{doc_id}", headers=_HEADERS)
        assert resp.status_code == 200
        assert doc_store.count() == 0
        assert missing.status_code == 404


class TestDocumentsQueryValidationEnvelope:
    def test_limit_zero_returns_flat_validation_error(self) -> None:
        """Query constraints must use the flat {error, detail} envelope, not detail[]."""
        with _client(_make_store(FakeDocumentStore())) as client:
            resp = client.get("/v1/documents?limit=0", headers=_HEADERS)
        assert resp.status_code == 422
        body = resp.json()
        assert body.get("error") == "validation_error"
        assert "detail" in body
        assert isinstance(body["detail"], str)
        assert "errors" in body
