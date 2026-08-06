"""HTTP contract tests for /v1/recall:tool_paths (TAP-5545).

Drives ``create_app()`` with a stubbed store. The route's whole job is to be
fail-closed on the wire: an unapproved learning must never reach a consumer,
and "nothing approved yet" must arrive as an empty 200 rather than a 404 or a
downgraded list of candidates.
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
from tapps_brain.models import LearningStatus, MemoryEntry, PromotionSignal

_HEADERS = {"X-Project-Id": "test-proj", "X-Agent-Id": "test-agent"}


def _entry(
    key: str,
    *,
    tags: list[str] | None = None,
    learning_status: LearningStatus = LearningStatus.approved,
    confidence: float = 0.8,
) -> MemoryEntry:
    approved = learning_status is LearningStatus.approved
    return MemoryEntry(
        key=key,
        value=f"value for {key}",
        tags=tags if tags is not None else ["fleet:learning"],
        confidence=confidence,
        learning_status=learning_status,
        promoted_by="eval-run-42" if approved else None,
        promoted_at="2026-08-05T00:00:00+00:00" if approved else None,
        promotion_signal=PromotionSignal.eval if approved else None,
    )


def _make_store(entries: list[MemoryEntry] | None = None) -> MagicMock:
    store = MagicMock()
    store._profile = None
    store._metrics = None
    store._tapps_project_id = _HEADERS["X-Project-Id"]
    store._project_id = _HEADERS["X-Project-Id"]
    store._agent_id = _HEADERS["X-Agent-Id"]
    store.search.return_value = list(entries or [])
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


def _post(store: MagicMock, body: dict[str, Any]) -> Any:
    with _client(store) as client:
        return client.post("/v1/recall:tool_paths", json=body, headers=_HEADERS)


class TestApprovedOnly:
    def test_approved_entry_is_returned(self) -> None:
        resp = _post(_make_store([_entry("k1")]), {"task_type": "refactor"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 1
        assert body["learning_status"] == "approved"
        assert body["tool_paths"][0]["key"] == "k1"

    def test_candidate_does_not_reach_the_wire(self) -> None:
        store = _make_store(
            [_entry("k1", learning_status=LearningStatus.candidate, confidence=0.99)]
        )
        resp = _post(store, {"task_type": "refactor"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 0

    def test_demoted_is_excluded_even_under_any(self) -> None:
        store = _make_store([_entry("k1", learning_status=LearningStatus.demoted)])
        resp = _post(store, {"task_type": "refactor", "learning_status": "any"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["count"] == 0


class TestFailClosed:
    def test_no_matches_is_an_empty_200_not_a_404(self) -> None:
        """The contract AF depends on: empty, never absent."""
        resp = _post(_make_store([]), {"task_type": "refactor"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == 0
        assert body["tool_paths"] == []
        assert "error" not in body

    def test_only_candidates_available_still_returns_empty(self) -> None:
        store = _make_store(
            [
                _entry("c1", learning_status=LearningStatus.candidate),
                _entry("c2", learning_status=LearningStatus.candidate),
            ]
        )
        resp = _post(store, {"task_type": "refactor"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["tool_paths"] == []


class TestRequestValidation:
    def test_missing_task_type_is_400(self) -> None:
        resp = _post(_make_store([]), {})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"

    def test_unknown_learning_status_is_400(self) -> None:
        resp = _post(_make_store([]), {"task_type": "refactor", "learning_status": "promoted"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"

    def test_out_of_range_limit_is_400(self) -> None:
        resp = _post(_make_store([]), {"task_type": "refactor", "limit": 51})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"

    def test_non_numeric_limit_is_400_not_500(self) -> None:
        """A bad type is the caller's error, so it must not surface as a crash."""
        resp = _post(_make_store([]), {"task_type": "refactor", "limit": "lots"})
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"] == "invalid_request"


class TestResponseShapeForAgentForge:
    def test_items_carry_the_fields_af_reads(self) -> None:
        resp = _post(_make_store([_entry("k1", tags=["tool:ruff"])]), {"task_type": "refactor"})
        item = resp.json()["tool_paths"][0]
        assert item["value"] == "value for k1"
        assert item["tags"] == ["tool:ruff"]
        assert item["confidence"] == 0.8
        assert item["project_id"] == _HEADERS["X-Project-Id"]
        assert item["promoted_by"] == "eval-run-42"
        assert item["promotion_signal"] == "eval"

    def test_task_type_is_what_drives_the_search(self) -> None:
        store = _make_store([_entry("k1")])
        _post(store, {"task_type": "deploy-helm-chart"})
        assert store.search.call_args[0][0] == "deploy-helm-chart"
