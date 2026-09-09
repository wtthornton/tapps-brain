"""Tenant-scope refusal gate for HTTP data-plane routes (TAP-7243, ADR-010).

Under ``TAPPS_BRAIN_STRICT_PROJECTS=1`` and/or ``TAPPS_BRAIN_STRICT_AGENT_ID=1``,
every write and global-scope read (``/v1/remember``, ``/v1/recall``,
``/v1/kg/neighbors``, ...) must refuse — before any store is touched — a
request whose project resolves to a literal placeholder / unregistered id, or
whose agent resolves to the anonymous ``"unknown"`` placeholder. One envelope
shape covers both axes (:func:`tapps_brain.errors.tenant_refusal_body`).

With both flags unset, behaviour must be byte-identical to base.

Local fixtures only — this module intentionally does not touch
``tests/conftest.py`` (owned by the L4 lane).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _mod
from tapps_brain.http_adapter import _service_version, _Settings, create_app

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Local helpers (mirrors tests/unit/test_http_adapter.py — not imported from
# there to keep this module self-contained per the L2 file partition).
# ---------------------------------------------------------------------------


def _make_settings(*, store: Any = None, auth_token: str | None = None) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = None
    s.auth_token = auth_token
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    return s


@contextmanager
def _client(settings: _Settings):
    with (
        patch.object(_mod, "_settings", settings),
        patch.object(_mod, "get_settings", return_value=settings),
    ):
        mcp_dummy = MagicMock()
        mcp_dummy.session_manager = None
        app = create_app(mcp_server=mcp_dummy)
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


@pytest.fixture(autouse=True)
def _clear_probe_db_cache() -> Any:
    import tapps_brain.http.probe_cache as _pc

    _pc._PROBE_CACHE.clear()
    yield
    _pc._PROBE_CACHE.clear()


_BAD_REQUEST_BODY = {"error": "bad_request", "detail": "X-Project-Id header is required."}


# ---------------------------------------------------------------------------
# Pure predicate — no HTTP, no DB (TAPPS_BRAIN_STRICT_PROJECTS reads the flag
# at call time; project_registry._strict_mode_enabled is the same reader the
# registry itself uses, so the two layers can never disagree).
# ---------------------------------------------------------------------------


class TestResolveTenantOrRefuseFlagsOff:
    """With both flags unset the function must be a no-op except for the
    pre-existing (flag-independent) missing-project 400."""

    def test_missing_project_still_400s_with_legacy_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from starlette.requests import Request

        from tapps_brain.http.middleware import resolve_tenant_or_refuse

        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_AGENT_ID", raising=False)
        scope = {"type": "http", "headers": []}
        request = Request(scope)
        project_id, agent_id, refusal = resolve_tenant_or_refuse(request)
        assert project_id == ""
        assert agent_id == "unknown"
        assert refusal is not None
        assert refusal.status_code == 400
        assert refusal.detail == _BAD_REQUEST_BODY

    def test_literal_project_and_unknown_agent_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from starlette.requests import Request

        from tapps_brain.http.middleware import resolve_tenant_or_refuse

        monkeypatch.delenv("TAPPS_BRAIN_STRICT_PROJECTS", raising=False)
        monkeypatch.delenv("TAPPS_BRAIN_STRICT_AGENT_ID", raising=False)
        scope = {
            "type": "http",
            "headers": [(b"x-project-id", b"default")],
        }
        request = Request(scope)
        project_id, agent_id, refusal = resolve_tenant_or_refuse(request)
        assert project_id == "default"
        assert agent_id == "unknown"
        assert refusal is None


# ---------------------------------------------------------------------------
# HTTP layer — project axis
# ---------------------------------------------------------------------------


class TestProjectAxisRefusal:
    def test_absent_project_id_refused_under_strict_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        get_store_mock = MagicMock()
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                get_store_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post("/v1/remember", json={"key": "k", "value": "v"})
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "tenant_project_missing"
        assert body["gate"] == "tenant_scope"
        assert body["category"] == "user_input"
        assert body["retryable"] is False
        assert "X-Project-Id" in body["remediation"]
        get_store_mock.assert_not_called()

    @pytest.mark.parametrize("literal", ["default", "repo-brain", "api", "main"])
    def test_literal_project_id_refused_under_strict_projects(
        self, monkeypatch: pytest.MonkeyPatch, literal: str
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        get_store_mock = MagicMock()
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                get_store_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post(
                "/v1/remember",
                json={"key": "k", "value": "v"},
                headers={"X-Project-Id": literal},
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "tenant_project_literal"
        assert body["gate"] == "tenant_scope"
        assert literal in body["remediation"]
        get_store_mock.assert_not_called()

    def test_unregistered_project_id_refused_under_strict_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ProjectRegistry.resolve() only raises ProjectNotRegisteredError under
        TAPPS_BRAIN_STRICT_PROJECTS=1 (project_registry.py) — this test proves
        _get_tenant_store_or_503 translates that into the new tenant-scope
        envelope instead of the pre-TAP-7243 404."""
        from tapps_brain.project_registry import ProjectNotRegisteredError

        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        raising_mock = MagicMock(side_effect=ProjectNotRegisteredError("ghost-project"))
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                raising_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post(
                "/v1/remember",
                json={"key": "k", "value": "v"},
                headers={"X-Project-Id": "ghost-project"},
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert body["code"] == "tenant_project_unregistered"
        assert body["gate"] == "tenant_scope"
        raising_mock.assert_called_once()


# ---------------------------------------------------------------------------
# HTTP layer — agent axis
# ---------------------------------------------------------------------------


class TestAgentAxisRefusal:
    def test_absent_agent_id_refused_under_strict_agent_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")
        get_store_mock = MagicMock()
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                get_store_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post(
                "/v1/remember",
                json={"key": "k", "value": "v"},
                headers={"X-Project-Id": "acme-widgets"},
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "tenant_agent_missing"
        assert body["gate"] == "tenant_scope"
        assert "X-Agent-Id" in body["remediation"]
        get_store_mock.assert_not_called()

    def test_literal_unknown_agent_id_refused_under_strict_agent_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")
        get_store_mock = MagicMock()
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                get_store_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post(
                "/v1/remember",
                json={"key": "k", "value": "v"},
                headers={"X-Project-Id": "acme-widgets", "X-Agent-Id": "unknown"},
            )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "tenant_agent_literal"
        assert body["gate"] == "tenant_scope"
        get_store_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Positive control (VAL-06) + flag-off byte-identical-to-base
# ---------------------------------------------------------------------------


class TestBothFlagsPositiveAndBaseBehaviour:
    def test_both_flags_on_registered_headers_persist_under_that_tenant(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """VAL-06 positive control: a request naming a real project/agent (not
        a literal, not 'unknown') is never refused and its write is durable
        under that exact (project_id, agent_id) pair."""
        from tapps_brain.store import MemoryStore

        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_AGENT_ID", "1")
        store = MemoryStore(tmp_path)
        settings = _make_settings(store=store)
        headers = {"X-Project-Id": "acme-widgets", "X-Agent-Id": "ci-runner-7"}
        with _client(settings) as c:
            resp = c.post(
                "/v1/remember",
                json={"key": "tap-7243-val-06", "value": "positive-control", "tier": "context"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] == "saved"
            assert body["key"] == "tap-7243-val-06"

            recall_resp = c.post(
                "/v1/recall",
                json={"query": "positive-control", "max_results": 5},
                headers=headers,
            )
        assert recall_resp.status_code == 200, recall_resp.text
        recalled_keys = [r["key"] for r in recall_resp.json()["results"]]
        assert "tap-7243-val-06" in recalled_keys

    def test_both_flags_unset_headerless_post_matches_base(self) -> None:
        """Proof the change is flag-gated: with both flags unset, a headerless
        POST gets the exact pre-TAP-7243 body/status — unchanged."""
        get_store_mock = MagicMock()
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                get_store_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post("/v1/remember", json={"key": "k", "value": "v"})
        assert resp.status_code == 400
        assert resp.json() == _BAD_REQUEST_BODY
        get_store_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Reads — /v1/recall and /v1/kg/neighbors refuse on the same predicate
# ---------------------------------------------------------------------------


class TestReadPathsRefuseTooOnStrictProjects:
    def test_recall_without_project_refused_under_strict_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        get_store_mock = MagicMock()
        with (
            patch(
                "tapps_brain.mcp_server.context._get_store_for_project",
                get_store_mock,
            ),
            _client(_make_settings(store=MagicMock())) as c,
        ):
            resp = c.post("/v1/recall", json={"query": "anything"})
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "tenant_project_missing"
        assert body["gate"] == "tenant_scope"
        get_store_mock.assert_not_called()

    def test_neighbors_without_project_refused_under_strict_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        with _client(_make_settings(store=MagicMock())) as c:
            resp = c.post("/v1/kg/neighbors", json={"entity_ids": []})
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["code"] == "tenant_project_missing"
        assert body["gate"] == "tenant_scope"

    def test_neighbors_literal_project_refused_under_strict_projects(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")
        with _client(_make_settings(store=MagicMock())) as c:
            resp = c.post(
                "/v1/kg/neighbors",
                json={"entity_ids": []},
                headers={"X-Project-Id": "repo-brain"},
            )
        assert resp.status_code == 400, resp.text
        assert resp.json()["code"] == "tenant_project_literal"


# ---------------------------------------------------------------------------
# "Measure it first" — the brief's headline trap: does strict-mode refuse a
# *registered but unapproved* project? (No — ProjectRegistry.resolve() only
# checks whether a row exists, never its approved flag; see resolve() at
# project_registry.py.) Verified here with a mocked connection manager so no
# live Postgres is required (mirrors tests/unit/test_project_registry.py).
# ---------------------------------------------------------------------------


class TestUnapprovedRowsUnderStrictMode:
    def test_registered_unapproved_project_resolves_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tapps_brain.profile import get_builtin_profile
        from tapps_brain.project_registry import ProjectRegistry

        monkeypatch.setenv("TAPPS_BRAIN_STRICT_PROJECTS", "1")

        mock_cm = MagicMock()
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cm.admin_context.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_cm.admin_context.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        profile = get_builtin_profile("repo-brain")
        mock_cursor.fetchone.return_value = (
            "nlt-orchestrator",
            profile.model_dump(mode="json"),
            False,  # approved=False — TAP-7256: nlt-orchestrator is registered/unapproved
            "auto",
            "",
        )

        registry = ProjectRegistry(mock_cm)
        # Must NOT raise ProjectNotRegisteredError: resolve() only checks row
        # existence, not the approved flag (project_registry.py: "if record is
        # not None: return record.profile" — approval is never consulted).
        resolved = registry.resolve("nlt-orchestrator")
        assert resolved.name == "repo-brain"
