"""HTTP contract tests for /v1/learning:promote and :demote (TAP-5542).

Drives ``create_app()`` with a stubbed store — verifies the status-code
mapping (200 / 400 / 404 / 409) the published gated-learning contract
promises AgentForge, plus the request/response field shapes.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import tapps_brain.http_adapter as _adapter_mod
from tapps_brain.errors import ConflictError, InvalidRequestError, NotFoundError
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)
from tapps_brain.models import LearningStatus, MemoryEntry, PromotionSignal

_HEADERS = {"X-Project-Id": "test-proj", "X-Agent-Id": "test-agent"}


def _approved_entry() -> MemoryEntry:
    return MemoryEntry(
        key="k",
        value="v",
        learning_status=LearningStatus.approved,
        promoted_by="eval-run-42",
        promoted_at="2026-08-05T00:00:00+00:00",
        promotion_signal=PromotionSignal.eval,
    )


def _demoted_entry() -> MemoryEntry:
    return MemoryEntry(
        key="k",
        value="v",
        learning_status=LearningStatus.demoted,
        demotion_reason="contradicted by TAP-1",
    )


def _make_store() -> MagicMock:
    store = MagicMock()
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


class TestPromoteRoute:
    def test_promote_returns_the_new_promotion_state(self) -> None:
        store = _make_store()
        store.promote_learning.return_value = _approved_entry()
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:promote",
                json={"key": "k", "signal": "eval", "actor": "eval-run-42"},
                headers=_HEADERS,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["promoted"] is True
        assert body["learning_status"] == "approved"
        assert body["promotion_signal"] == "eval"
        assert body["promoted_by"] == "eval-run-42"

    def test_promote_forwards_signal_actor_and_evidence(self) -> None:
        store = _make_store()
        store.promote_learning.return_value = _approved_entry()
        with _client(store) as client:
            client.post(
                "/v1/learning:promote",
                json={
                    "key": "k",
                    "signal": "human",
                    "actor": "alice",
                    "evidence": "reviewed in TAP-1",
                },
                headers=_HEADERS,
            )
        _, kwargs = store.promote_learning.call_args
        assert kwargs["signal"] == "human"
        assert kwargs["actor"] == "alice"
        assert kwargs["evidence"] == "reviewed in TAP-1"

    def test_unknown_key_is_404(self) -> None:
        store = _make_store()
        store.promote_learning.side_effect = NotFoundError("no memory entry with key 'k'")
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:promote",
                json={"key": "k", "signal": "eval", "actor": "eval-run-1"},
                headers=_HEADERS,
            )
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_already_approved_is_409(self) -> None:
        store = _make_store()
        store.promote_learning.side_effect = ConflictError("entry 'k' is already approved")
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:promote",
                json={"key": "k", "signal": "eval", "actor": "eval-run-1"},
                headers=_HEADERS,
            )
        assert resp.status_code == 409
        assert resp.json()["error"] == "conflict"

    def test_bad_signal_is_400(self) -> None:
        """Frequency is not a promotion signal, and the wire says so."""
        store = _make_store()
        store.promote_learning.side_effect = InvalidRequestError("signal must be one of ...")
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:promote",
                json={"key": "k", "signal": "frequency", "actor": "agent"},
                headers=_HEADERS,
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_missing_project_header_is_400(self) -> None:
        store = _make_store()
        store.promote_learning.return_value = _approved_entry()
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:promote",
                json={"key": "k", "signal": "eval", "actor": "eval-run-1"},
            )
        assert resp.status_code == 400


class TestDemoteRoute:
    def test_demote_returns_the_new_state(self) -> None:
        store = _make_store()
        store.demote_learning.return_value = _demoted_entry()
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:demote",
                json={"key": "k", "reason": "contradicted by TAP-1"},
                headers=_HEADERS,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["demoted"] is True
        assert body["learning_status"] == "demoted"
        assert body["demotion_reason"] == "contradicted by TAP-1"

    def test_missing_reason_is_400(self) -> None:
        store = _make_store()
        store.demote_learning.side_effect = InvalidRequestError("reason is required")
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:demote",
                json={"key": "k"},
                headers=_HEADERS,
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    def test_unknown_key_is_404(self) -> None:
        store = _make_store()
        store.demote_learning.side_effect = NotFoundError("no memory entry with key 'k'")
        with _client(store) as client:
            resp = client.post(
                "/v1/learning:demote",
                json={"key": "k", "reason": "wrong"},
                headers=_HEADERS,
            )
        assert resp.status_code == 404


class TestProfileGateWiring:
    """The startup drift check refuses to boot on an unmapped route."""

    def test_routes_map_to_tools_in_the_bundled_catalog(self) -> None:
        from tapps_brain.http.rest_profile_gate import (
            REST_ROUTE_TO_TOOL,
            validate_rest_route_map,
        )
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry

        assert REST_ROUTE_TO_TOOL["/v1/learning:promote"] == "brain_promote_learning"
        assert REST_ROUTE_TO_TOOL["/v1/learning:demote"] == "brain_demote_learning"
        registry = ProfileRegistry()
        validate_rest_route_map(frozenset(registry.get("operator")))

    def test_coder_profile_cannot_promote(self) -> None:
        """A coding agent approving its own learnings is the gate approving itself."""
        from tapps_brain.mcp_server.profile_registry import ProfileRegistry

        coder = ProfileRegistry().get("coder")
        assert "brain_promote_learning" not in coder
        assert "brain_demote_learning" not in coder
