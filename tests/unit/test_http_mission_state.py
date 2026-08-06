"""HTTP contract tests for /v1/mission/state:set and :get (TAP-5544).

Drives ``create_app()`` with a stubbed store. The property that matters on the
wire is isolation: two missions under one ``project_id`` must not read each
other, and an unwritten slot must be a 200 rather than a 404.
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
from tapps_brain.models import MemoryEntry, MemoryScope

_HEADERS = {"X-Project-Id": "test-proj", "X-Agent-Id": "test-agent"}


class _FakeStore(MagicMock):
    """MagicMock with a real key-value ``save``/``get`` pair."""

    def _init_state(self) -> None:
        self.entries: dict[str, MemoryEntry] = {}

    def save(self, **kwargs: Any) -> MemoryEntry:
        entry = MemoryEntry(
            key=kwargs["key"],
            value=kwargs["value"],
            scope=MemoryScope(kwargs.get("scope", "project")),
            mission_id=kwargs.get("mission_id"),
            run_id=kwargs.get("run_id"),
            tags=kwargs.get("tags") or [],
        )
        self.entries[entry.key] = entry
        return entry

    def get(self, key: str, *_a: Any, **_kw: Any) -> MemoryEntry | None:
        return self.entries.get(key)


def _make_store() -> _FakeStore:
    store = _FakeStore()
    store._init_state()
    store._profile = None
    store._metrics = None
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


class TestRoundTrip:
    def test_set_then_get_over_http(self) -> None:
        store = _make_store()
        with _client(store) as client:
            set_resp = client.post(
                "/v1/mission/state:set",
                json={"mission_id": "m-1", "kind": "contract", "value": {"goal": "ship"}},
                headers=_HEADERS,
            )
            get_resp = client.post(
                "/v1/mission/state:get",
                json={"mission_id": "m-1", "kind": "contract"},
                headers=_HEADERS,
            )
        assert set_resp.status_code == 200, set_resp.text
        assert set_resp.json()["saved"] is True
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["value"] == {"goal": "ship"}


class TestIsolationOverHttp:
    def test_one_mission_cannot_read_another(self) -> None:
        store = _make_store()
        with _client(store) as client:
            client.post(
                "/v1/mission/state:set",
                json={"mission_id": "m-1", "kind": "findings", "value": {"secret": 1}},
                headers=_HEADERS,
            )
            resp = client.post(
                "/v1/mission/state:get",
                json={"mission_id": "m-2", "kind": "findings"},
                headers=_HEADERS,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["found"] is False
        assert body["value"] is None


class TestMissingSlot:
    def test_unwritten_slot_is_200_found_false_not_404(self) -> None:
        """A worker picking up a mission must not have to treat this as failure."""
        with _client(_make_store()) as client:
            resp = client.post(
                "/v1/mission/state:get",
                json={"mission_id": "m-fresh", "kind": "contract"},
                headers=_HEADERS,
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["found"] is False


class TestRequestValidation:
    def test_missing_mission_id_is_400(self) -> None:
        with _client(_make_store()) as client:
            resp = client.post(
                "/v1/mission/state:set",
                json={"kind": "contract", "value": {}},
                headers=_HEADERS,
            )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"

    def test_unknown_kind_is_400(self) -> None:
        with _client(_make_store()) as client:
            resp = client.post(
                "/v1/mission/state:set",
                json={"mission_id": "m-1", "kind": "vibes", "value": {}},
                headers=_HEADERS,
            )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"

    def test_key_unsafe_mission_id_is_400_not_500(self) -> None:
        """A caller mistake must not surface as a model ValidationError."""
        with _client(_make_store()) as client:
            resp = client.post(
                "/v1/mission/state:set",
                json={"mission_id": "Mission One", "kind": "contract", "value": {}},
                headers=_HEADERS,
            )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"
