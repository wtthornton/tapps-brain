"""VAL-19: ``/v1/remember`` must persist invocation provenance carried on the wire.

Root cause: neither ``body["metadata"]["invocation_id"]`` (AgentForge's shape —
``backend/api/routes/ingest.py`` + ``backend/memory/brain.py``) nor the
``X-Origin-Invocation-Id`` header were ever read by the handler, so every write
was unattributable by construction — ``run_id``, ``triggered_by`` and
``source_session_id`` stayed empty regardless of what the caller sent.

``_extract_invocation_id`` (``http_adapter.py``) recovers that identity and maps
it onto the existing ``run_id`` provenance column (no new column, no changed
default semantics — SC-10).  These tests cover the pure extraction logic, the
service-layer plumbing (``memory_save`` / ``async_memory_save``), and an
end-to-end HTTP round trip that reads the persisted entry back out of the
resolved store.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.datastructures import Headers
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _mod
from tapps_brain.http_adapter import (
    _extract_invocation_id,
    _service_version,
    _Settings,
    create_app,
)
from tapps_brain.services import memory_service as ms
from tapps_brain.store import MemoryStore


class _FakeRequest:
    """Minimal stand-in exposing only the ``.headers`` surface the helper reads."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = Headers(headers)


# ---------------------------------------------------------------------------
# _extract_invocation_id: pure function, no store needed
# ---------------------------------------------------------------------------


class TestExtractInvocationId:
    def test_metadata_invocation_id_wins(self) -> None:
        body = {"metadata": {"invocation_id": " abc-123 "}}
        request = _FakeRequest({})
        assert _extract_invocation_id(body, request) == "abc-123"

    def test_falls_back_to_header_when_no_metadata(self) -> None:
        body: dict[str, Any] = {}
        request = _FakeRequest({"X-Origin-Invocation-Id": "hdr-456"})
        assert _extract_invocation_id(body, request) == "hdr-456"

    def test_metadata_takes_precedence_over_header(self) -> None:
        body = {"metadata": {"invocation_id": "from-metadata"}}
        request = _FakeRequest({"X-Origin-Invocation-Id": "from-header"})
        assert _extract_invocation_id(body, request) == "from-metadata"

    def test_neither_present_returns_none(self) -> None:
        assert _extract_invocation_id({}, _FakeRequest({})) is None

    def test_empty_metadata_invocation_id_falls_back_to_header(self) -> None:
        body = {"metadata": {"invocation_id": "   "}}
        request = _FakeRequest({"X-Origin-Invocation-Id": "hdr-789"})
        assert _extract_invocation_id(body, request) == "hdr-789"

    def test_non_dict_metadata_is_ignored(self) -> None:
        body = {"metadata": "not-a-dict"}
        request = _FakeRequest({"X-Origin-Invocation-Id": "hdr-fallback"})
        assert _extract_invocation_id(body, request) == "hdr-fallback"

    def test_non_string_invocation_id_is_ignored(self) -> None:
        body = {"metadata": {"invocation_id": 12345}}
        request = _FakeRequest({})
        assert _extract_invocation_id(body, request) is None


# ---------------------------------------------------------------------------
# Service layer: run_id reaches the persisted MemoryEntry
# ---------------------------------------------------------------------------


class TestMemorySavePersistsRunId:
    def test_run_id_persists_on_the_entry(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        result = ms.memory_save(
            store,
            "proj",
            "agent",
            key="direct-run-id-key",
            value="a value carrying invocation provenance",
            tier="context",
            run_id="11111111-1111-1111-1111-111111111111",
        )
        assert result["status"] == "saved"
        entry = store.get("direct-run-id-key")
        assert entry is not None
        assert entry.run_id == "11111111-1111-1111-1111-111111111111"

    def test_omitted_run_id_leaves_column_unset(self, tmp_path: Path) -> None:
        """SC-10 backward compat: a caller sending no metadata/header behaves
        exactly as it did before this change."""
        store = MemoryStore(tmp_path)
        result = ms.memory_save(
            store,
            "proj",
            "agent",
            key="no-run-id-key",
            value="a value with no invocation provenance at all",
            tier="context",
        )
        assert result["status"] == "saved"
        entry = store.get("no-run-id-key")
        assert entry is not None
        assert entry.run_id is None

    @pytest.mark.asyncio
    async def test_async_memory_save_persists_run_id(self, tmp_path: Path) -> None:
        from tapps_brain.aio import AsyncMemoryStore

        store = MemoryStore(tmp_path)
        async_store = AsyncMemoryStore(store)
        result = await ms.async_memory_save(
            async_store,
            "proj",
            "agent",
            key="async-run-id-key",
            value="an async-path value carrying invocation provenance",
            tier="context",
            run_id="22222222-2222-2222-2222-222222222222",
        )
        assert result["status"] == "saved"
        entry = store.get("async-run-id-key")
        assert entry is not None
        assert entry.run_id == "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# HTTP end-to-end: /v1/remember -> persisted run_id, both request shapes
# ---------------------------------------------------------------------------


def _settings(store: Any) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = "tok"
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


class TestV1RememberPersistsInvocationProvenance:
    """End-to-end through the FastAPI route.

    ``X-Agent-Id: http-adapter`` (matching the store's own resolved
    ``_agent_id`` fallback) makes ``_get_tenant_store_or_503`` reuse the exact
    injected ``MemoryStore`` instead of building a fresh per-tenant one, so the
    test can read the persisted entry straight back off it.
    """

    @contextmanager
    def _client_and_headers(self, store: MemoryStore):
        settings = _settings(store)
        headers = {
            "Authorization": "Bearer tok",
            "X-Project-Id": store._project_id,
            "X-Agent-Id": "http-adapter",
        }
        with (
            patch.object(_mod, "_settings", settings),
            patch.object(_mod, "get_settings", return_value=settings),
        ):
            mcp_dummy = MagicMock()
            mcp_dummy.session_manager = None
            app = create_app(mcp_server=mcp_dummy)
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, headers

    def test_metadata_invocation_id_becomes_run_id(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, agent_id="http-adapter")
        with self._client_and_headers(store) as (client, headers):
            resp = client.post(
                "/v1/remember",
                json={
                    "key": "http-metadata-run-id",
                    "value": "AgentForge-shaped write carrying metadata.invocation_id",
                    "tier": "context",
                    "metadata": {"invocation_id": "33333333-3333-3333-3333-333333333333"},
                },
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "saved"

        entry = store.get("http-metadata-run-id")
        assert entry is not None
        assert entry.run_id == "33333333-3333-3333-3333-333333333333"

    def test_header_invocation_id_becomes_run_id(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path, agent_id="http-adapter")
        with self._client_and_headers(store) as (client, headers):
            resp = client.post(
                "/v1/remember",
                json={
                    "key": "http-header-run-id",
                    "value": "a write identified only by the origin-invocation header",
                    "tier": "context",
                },
                headers={
                    **headers,
                    "X-Origin-Invocation-Id": "44444444-4444-4444-4444-444444444444",
                },
            )
            assert resp.status_code == 200, resp.text

        entry = store.get("http-header-run-id")
        assert entry is not None
        assert entry.run_id == "44444444-4444-4444-4444-444444444444"

    def test_no_metadata_no_header_is_unchanged(self, tmp_path: Path) -> None:
        """SC-10: a request carrying neither signal behaves exactly as before."""
        store = MemoryStore(tmp_path, agent_id="http-adapter")
        with self._client_and_headers(store) as (client, headers):
            resp = client.post(
                "/v1/remember",
                json={
                    "key": "http-no-provenance",
                    "value": "a plain write with no invocation identity at all",
                    "tier": "context",
                    "tags": ["plain-write"],
                },
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["status"] == "saved"

        entry = store.get("http-no-provenance")
        assert entry is not None
        assert entry.run_id is None
        # Tags are unaffected by the provenance change on a fresh write.
        assert entry.tags == ["plain-write"]

    def test_the_join_val19_asserts_key_to_invocation(self, tmp_path: Path) -> None:
        """The row this write produced joins back to the invocation that wrote it."""
        store = MemoryStore(tmp_path, agent_id="http-adapter")
        invocation_id = "55555555-5555-5555-5555-555555555555"
        with self._client_and_headers(store) as (client, headers):
            client.post(
                "/v1/remember",
                json={
                    "key": "val19-join-probe",
                    "value": "provenance join proof for VAL-19",
                    "tier": "context",
                    "metadata": {"invocation_id": invocation_id},
                },
                headers=headers,
            )

        entry = store.get("val19-join-probe")
        assert entry is not None
        assert entry.run_id == invocation_id, "memory row does not join back to its invocation"
