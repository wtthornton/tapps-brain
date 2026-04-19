"""Unit tests for tapps_brain.idempotency (EPIC-070 STORY-070.5).

Tests cover:
- is_idempotency_enabled() feature flag
- IdempotencyStore.check() — hit, miss, error-as-miss, decode-error-as-miss
- IdempotencyStore.save() — store and ON CONFLICT behaviour
- IdempotencyStore.sweep_expired() — deletes expired rows, returns count
- sweep_expired_keys() convenience helper
- HTTP routes: POST /v1/remember and POST /v1/reinforce
  - Accept X-Idempotency-Key header (UUID)
  - Duplicate key within 24h returns original response (Idempotency-Replayed: true)
  - Feature-flagged OFF by default
- Acceptance criteria from the story
"""

from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

import tapps_brain.http_adapter as _adapter_mod
from tapps_brain.http_adapter import (
    _service_version,
    _Settings,
    create_app,
)
from tapps_brain.idempotency import (
    IDEMPOTENCY_TTL_HOURS,
    IdempotencyStore,
    is_idempotency_enabled,
    sweep_expired_keys,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    dsn: str | None = None,
    auth_token: str | None = None,
    store: Any = None,
) -> _Settings:
    s = _Settings.__new__(_Settings)
    s.dsn = dsn
    s.auth_token = auth_token
    s.admin_token = None
    s.metrics_token = None
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
    # TAP-548: lifespan startup builds the singleton when
    # TAPPS_BRAIN_IDEMPOTENCY=1 and a DSN is set; otherwise it stays None.
    s.idempotency_store = None
    return s


def _make_store() -> MagicMock:
    """Return a mock MemoryStore with a mock profile."""
    store = MagicMock()
    profile = MagicMock()
    profile.layer_names = ["architectural", "pattern", "procedural", "context"]
    store.profile = profile
    return store


def _client_with_store(settings: _Settings) -> TestClient:
    """Build TestClient with isolated settings and a dummy MCP server."""
    mcp_dummy = MagicMock()
    mcp_dummy.session_manager = None
    with (
        patch.object(_adapter_mod, "_settings", settings),
        patch.object(_adapter_mod, "get_settings", return_value=settings),
    ):
        app = create_app(store=settings.store, mcp_server=mcp_dummy)
        # TestClient is used as a context manager in individual tests.
        return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# AC-8: Feature flag — OFF by default
# ---------------------------------------------------------------------------


class TestIsIdempotencyEnabled:
    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TAPPS_BRAIN_IDEMPOTENCY unset → disabled."""
        monkeypatch.delenv("TAPPS_BRAIN_IDEMPOTENCY", raising=False)
        assert is_idempotency_enabled() is False

    def test_enabled_when_set_to_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TAPPS_BRAIN_IDEMPOTENCY=1 → enabled."""
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        assert is_idempotency_enabled() is True

    def test_not_enabled_for_other_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TAPPS_BRAIN_IDEMPOTENCY=true → disabled (only '1' is accepted)."""
        for val in ("true", "yes", "on", "0", ""):
            monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", val)
            assert is_idempotency_enabled() is False


# ---------------------------------------------------------------------------
# IdempotencyStore unit tests (mocked psycopg)
# ---------------------------------------------------------------------------


def _make_cursor_with_row(row: Any) -> MagicMock:
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = row
    cur.rowcount = 1
    return cur


def _make_conn(cursor: MagicMock) -> MagicMock:
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cursor
    return conn


def _make_cm(conn: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.get_connection = MagicMock(
        return_value=MagicMock(
            __enter__=MagicMock(return_value=conn),
            __exit__=MagicMock(return_value=False),
        )
    )
    cm.close = MagicMock()
    return cm


class TestIdempotencyStoreCheck:
    """AC-2: check() returns stored response on hit, None on miss."""

    def _store_with_row(self, row: Any) -> tuple[IdempotencyStore, MagicMock]:
        cur = _make_cursor_with_row(row)
        conn = _make_conn(cur)
        cm = _make_cm(conn)
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._cm = cm
        store.ttl_hours = IDEMPOTENCY_TTL_HOURS
        return store, cur

    def test_miss_returns_none(self) -> None:
        istore, _ = self._store_with_row(None)
        assert istore.check("proj", "key-123") is None

    def test_hit_returns_status_and_body(self) -> None:
        body = {"status": "saved", "key": "foo"}
        istore, _ = self._store_with_row((200, json.dumps(body)))
        result = istore.check("proj", "key-123")
        assert result is not None
        status, returned_body = result
        assert status == 200
        assert returned_body == body

    def test_decode_error_treated_as_miss(self) -> None:
        istore, _ = self._store_with_row((200, "NOT_VALID_JSON"))
        assert istore.check("proj", "key-123") is None

    def test_exception_treated_as_miss(self) -> None:
        store = IdempotencyStore.__new__(IdempotencyStore)
        cm = MagicMock()
        cm.get_connection.side_effect = RuntimeError("db down")
        store._cm = cm
        store.ttl_hours = 24
        assert store.check("proj", "key") is None


class TestIdempotencyStoreSave:
    """AC-1, AC-5: save() inserts response JSON; oversized response skipped."""

    def _make_istore(self) -> tuple[IdempotencyStore, MagicMock]:
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        cm = _make_cm(conn)
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._cm = cm
        store.ttl_hours = IDEMPOTENCY_TTL_HOURS
        return store, cur

    def test_save_calls_insert(self) -> None:
        istore, cur = self._make_istore()
        istore.save("proj", "ikey", 200, {"saved": True})
        assert cur.execute.called
        sql_arg = cur.execute.call_args[0][0]
        assert "INSERT INTO idempotency_keys" in sql_arg

    def test_oversized_response_skipped(self) -> None:
        istore, cur = self._make_istore()
        big_body = {"data": "x" * 70_000}
        istore.save("proj", "ikey", 200, big_body)
        # execute should NOT have been called for the INSERT
        assert not cur.execute.called

    def test_exception_silenced(self) -> None:
        store = IdempotencyStore.__new__(IdempotencyStore)
        cm = MagicMock()
        cm.get_connection.side_effect = RuntimeError("db down")
        store._cm = cm
        store.ttl_hours = 24
        # Should not raise
        store.save("proj", "ikey", 200, {"ok": True})


class TestIdempotencyStoreSweep:
    """AC-7: sweep_expired() deletes expired rows."""

    def test_sweep_returns_deleted_count(self) -> None:
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.rowcount = 5
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        cm = _make_cm(conn)
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._cm = cm
        store.ttl_hours = IDEMPOTENCY_TTL_HOURS
        assert store.sweep_expired() == 5

    def test_sweep_exception_returns_zero(self) -> None:
        store = IdempotencyStore.__new__(IdempotencyStore)
        cm = MagicMock()
        cm.get_connection.side_effect = RuntimeError("db down")
        store._cm = cm
        store.ttl_hours = 24
        assert store.sweep_expired() == 0

    def test_sweep_uses_custom_ttl(self) -> None:
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)
        cur.rowcount = 0
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        cm = _make_cm(conn)
        store = IdempotencyStore.__new__(IdempotencyStore)
        store._cm = cm
        store.ttl_hours = IDEMPOTENCY_TTL_HOURS
        store.sweep_expired(ttl_hours=48)
        # Verify the custom ttl was passed
        call_args = cur.execute.call_args
        assert 48 in call_args[0][1]


class TestSweepExpiredKeys:
    """sweep_expired_keys() convenience helper."""

    def test_no_dsn_returns_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
        assert sweep_expired_keys(dsn=None) == 0

    def test_uses_env_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "postgresql://fake/db")
        with patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.sweep_expired.return_value = 3
            mock_inst.__enter__ = MagicMock(return_value=mock_inst)
            mock_inst.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = mock_inst
            result = sweep_expired_keys()
        assert result == 3

    def test_explicit_dsn_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "postgresql://env/db")
        with patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.sweep_expired.return_value = 7
            mock_inst.__enter__ = MagicMock(return_value=mock_inst)
            mock_inst.__exit__ = MagicMock(return_value=False)
            mock_cls.return_value = mock_inst
            result = sweep_expired_keys(dsn="postgresql://explicit/db")
        expected_dsn = "postgresql://explicit/db"
        mock_cls.assert_called_once_with(expected_dsn, ttl_hours=IDEMPOTENCY_TTL_HOURS)
        assert result == 7


# ---------------------------------------------------------------------------
# AC-1: POST /v1/remember accepts X-Idempotency-Key
# ---------------------------------------------------------------------------


class TestV1RememberRoute:
    """HTTP POST /v1/remember (STORY-070.5)."""

    def _setup(
        self,
        *,
        save_return: dict[str, Any] | None = None,
        dsn: str | None = None,
    ) -> tuple[TestClient, MagicMock]:
        store = _make_store()
        if save_return is None:
            save_return = {
                "status": "saved",
                "key": "test-key",
                "tier": "pattern",
                "confidence": 0.8,
                "memory_group": None,
            }
        store.save.return_value = MagicMock(
            key="test-key",
            tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8,
            memory_group=None,
        )
        settings = _make_settings(dsn=dsn, store=store)
        client = _client_with_store(settings)
        return client, store

    def test_missing_project_id_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/remember", json={"key": "k", "value": "v"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_missing_key_or_value_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/remember", headers={"x-project-id": "proj"}, json={"key": "k"})
        assert resp.status_code == 400

    def test_successful_save_returns_200(self) -> None:
        client, _ = self._setup()
        with (
            patch(
                "tapps_brain.services.memory_service.memory_save",
                return_value={
                    "status": "saved",
                    "key": "k",
                    "tier": "pattern",
                    "confidence": 0.8,
                    "memory_group": None,
                },
            ),
            client,
        ):
            resp = client.post(
                "/v1/remember",
                headers={"x-project-id": "proj", "x-agent-id": "agent-1"},
                json={"key": "k", "value": "v"},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    def test_no_store_returns_503(self) -> None:
        settings = _make_settings(store=None)
        client = _client_with_store(settings)
        with client:
            resp = client.post(
                "/v1/remember",
                headers={"x-project-id": "proj"},
                json={"key": "k", "value": "v"},
            )
        assert resp.status_code == 503

    def test_idempotency_key_accepted_when_disabled(self) -> None:
        """X-Idempotency-Key is accepted (and ignored) when feature is OFF."""
        with patch(
            "tapps_brain.services.memory_service.memory_save",
            return_value={
                "status": "saved",
                "key": "k",
                "tier": "pattern",
                "confidence": 0.8,
                "memory_group": None,
            },
        ):
            client, _ = self._setup()
            with client:
                resp = client.post(
                    "/v1/remember",
                    headers={
                        "x-project-id": "proj",
                        "x-idempotency-key": "550e8400-e29b-41d4-a716-446655440000",
                    },
                    json={"key": "k", "value": "v"},
                )
        assert resp.status_code == 200
        # No Idempotency-Replayed header when feature is off
        assert "idempotency-replayed" not in resp.headers

    def test_idempotency_replay_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2: Duplicate key within 24h returns original response."""
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        cached_body = {
            "status": "saved",
            "key": "k",
            "tier": "pattern",
            "confidence": 0.8,
            "memory_group": None,
        }
        ikey = "550e8400-e29b-41d4-a716-446655440000"
        client, _ = self._setup(dsn="postgresql://fake/db")
        with patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.check.return_value = (200, cached_body)
            mock_inst.close = MagicMock()
            mock_cls.return_value = mock_inst
            with client:
                resp = client.post(
                    "/v1/remember",
                    headers={"x-project-id": "proj", "x-idempotency-key": ikey},
                    json={"key": "k", "value": "v"},
                )
        assert resp.status_code == 200
        assert resp.json() == cached_body
        assert resp.headers.get("idempotency-replayed") == "true"

    def test_idempotency_store_called_on_miss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-1: On first call, response is stored in idempotency_keys.

        TAP-548: ``IdempotencyStore`` is now a process-wide singleton
        built once in the lifespan startup hook, so ``check`` and
        ``save`` both land on the same instance (previously each route
        call built a fresh store).
        """
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        saved_result = {
            "status": "saved",
            "key": "k",
            "tier": "pattern",
            "confidence": 0.8,
            "memory_group": None,
        }
        ikey = "test-uuid-1234"
        client, _ = self._setup(dsn="postgresql://fake/db")
        inst = MagicMock()
        inst.check.return_value = None  # cache miss → real write runs
        inst.save = MagicMock()
        inst.close = MagicMock()
        with (
            patch("tapps_brain.services.memory_service.memory_save", return_value=saved_result),
            patch(
                "tapps_brain.idempotency.IdempotencyStore",
                return_value=inst,
            ) as mock_cls,
            client,
        ):
            resp = client.post(
                "/v1/remember",
                headers={"x-project-id": "proj", "x-idempotency-key": ikey},
                json={"key": "k", "value": "v"},
            )
        assert resp.status_code == 200
        # Singleton: constructed once for the whole lifespan.
        assert mock_cls.call_count == 1
        # ``save`` was routed through the same instance as ``check``.
        inst.save.assert_called_once_with("proj", ikey, 200, saved_result)

    def test_empty_body_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/remember",
                headers={"x-project-id": "proj"},
                content=b"",
            )
        assert resp.status_code == 400

    def test_invalid_json_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post(
                "/v1/remember",
                headers={"x-project-id": "proj"},
                content=b"not-json",
            )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# AC-1: POST /v1/reinforce accepts X-Idempotency-Key
# ---------------------------------------------------------------------------


class TestV1ReinforceRoute:
    """HTTP POST /v1/reinforce (STORY-070.5)."""

    def _setup(self, *, dsn: str | None = None) -> tuple[TestClient, MagicMock]:
        store = _make_store()
        settings = _make_settings(dsn=dsn, store=store)
        return _client_with_store(settings), store

    def test_missing_project_id_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/reinforce", json={"key": "k"})
        assert resp.status_code == 400

    def test_missing_key_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/reinforce", headers={"x-project-id": "proj"}, json={})
        assert resp.status_code == 400

    def test_successful_reinforce(self) -> None:
        client, _ = self._setup()
        result = {"status": "reinforced", "key": "k", "confidence": 0.9, "access_count": 3}
        with (
            patch("tapps_brain.services.memory_service.memory_reinforce", return_value=result),
            client,
        ):
            resp = client.post(
                "/v1/reinforce",
                headers={"x-project-id": "proj"},
                json={"key": "k", "confidence_boost": 0.1},
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "reinforced"

    def test_no_store_returns_503(self) -> None:
        settings = _make_settings(store=None)
        client = _client_with_store(settings)
        with client:
            resp = client.post(
                "/v1/reinforce",
                headers={"x-project-id": "proj"},
                json={"key": "k"},
            )
        assert resp.status_code == 503

    def test_idempotency_replay_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC-2: Duplicate key within 24h returns original response."""
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        cached_body = {"status": "reinforced", "key": "k", "confidence": 0.9, "access_count": 5}
        ikey = "reinforce-uuid-5678"
        client, _ = self._setup(dsn="postgresql://fake/db")
        with patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls:
            mock_inst = MagicMock()
            mock_inst.check.return_value = (200, cached_body)
            mock_inst.close = MagicMock()
            mock_cls.return_value = mock_inst
            with client:
                resp = client.post(
                    "/v1/reinforce",
                    headers={"x-project-id": "proj", "x-idempotency-key": ikey},
                    json={"key": "k"},
                )
        assert resp.status_code == 200
        assert resp.json() == cached_body
        assert resp.headers.get("idempotency-replayed") == "true"


# ---------------------------------------------------------------------------
# TAP-548: singleton IdempotencyStore lifecycle — build once, close on shutdown
# ---------------------------------------------------------------------------


class TestIdempotencySingletonLifecycle:
    """TAP-548 — the ``IdempotencyStore`` must be built once per adapter
    process and closed on shutdown.  Pre-fix every write route built a
    fresh store (and its backing ``PostgresConnectionManager`` pool) per
    request, which raced the hardened pool for ``max_connections``
    slots under load and was invisible to pool metrics.
    """

    def _setup_store(self) -> MagicMock:
        return _make_store()

    def test_store_constructed_once_across_many_writes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """100 sequential /v1/remember calls must hit the singleton, so the
        ``IdempotencyStore`` ctor fires exactly once.  (Concurrency is
        not needed to prove the invariant: if per-request construction
        came back, a purely sequential loop would fire the ctor 100
        times.)
        """
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        saved_result = {
            "status": "saved",
            "key": "k",
            "tier": "pattern",
            "confidence": 0.8,
            "memory_group": None,
        }
        inst = MagicMock()
        inst.check.return_value = None
        inst.save = MagicMock()
        inst.close = MagicMock()
        settings = _make_settings(dsn="postgresql://fake/db", store=self._setup_store())
        client = _client_with_store(settings)
        with (
            patch(
                "tapps_brain.services.memory_service.memory_save",
                return_value=saved_result,
            ),
            patch(
                "tapps_brain.idempotency.IdempotencyStore",
                return_value=inst,
            ) as mock_cls,
            client,
        ):
            for i in range(100):
                resp = client.post(
                    "/v1/remember",
                    headers={
                        "x-project-id": "proj",
                        "x-idempotency-key": f"uuid-{i:03d}",
                    },
                    json={"key": "k", "value": "v"},
                )
                assert resp.status_code == 200
        assert mock_cls.call_count == 1, (
            f"expected singleton; got {mock_cls.call_count} IdempotencyStore "
            f"constructions across 100 requests"
        )

    def test_singleton_closed_on_shutdown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Exiting the ``TestClient`` context runs FastAPI shutdown; the
        singleton's ``close()`` must be invoked so the Postgres pool
        releases cleanly.
        """
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        inst = MagicMock()
        inst.check.return_value = None
        inst.close = MagicMock()
        settings = _make_settings(dsn="postgresql://fake/db", store=self._setup_store())
        client = _client_with_store(settings)
        with patch(
            "tapps_brain.idempotency.IdempotencyStore",
            return_value=inst,
        ):
            with client:
                pass  # enter + exit = full startup+shutdown cycle
        inst.close.assert_called_once()
        assert settings.idempotency_store is None, (
            "shutdown must null out the singleton so a second lifespan "
            "(e.g. another TestClient on the same app) rebuilds it"
        )

    def test_no_singleton_when_feature_flag_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ``TAPPS_BRAIN_IDEMPOTENCY`` is unset, the lifespan must
        NOT construct an ``IdempotencyStore`` — even with a DSN — so
        disabled deployments pay no pool cost.
        """
        monkeypatch.delenv("TAPPS_BRAIN_IDEMPOTENCY", raising=False)
        settings = _make_settings(dsn="postgresql://fake/db", store=self._setup_store())
        client = _client_with_store(settings)
        with patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls:
            with client:
                pass
        assert mock_cls.call_count == 0
        assert settings.idempotency_store is None

    def test_no_singleton_when_dsn_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the adapter has no DSN, the singleton is not built (there
        is nothing to talk to) and write routes fall through as if
        idempotency were disabled.
        """
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        settings = _make_settings(dsn=None, store=self._setup_store())
        client = _client_with_store(settings)
        with patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls:
            with client:
                pass
        assert mock_cls.call_count == 0
        assert settings.idempotency_store is None

    def test_store_init_failure_is_logged_and_swallowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the ``IdempotencyStore`` ctor raises at startup (e.g. DSN is
        garbage), the adapter must still come up — the write routes
        fall through to the real write path without a cache.
        """
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        settings = _make_settings(dsn="postgresql://fake/db", store=self._setup_store())
        client = _client_with_store(settings)
        with patch(
            "tapps_brain.idempotency.IdempotencyStore",
            side_effect=RuntimeError("db unreachable at boot"),
        ):
            # Entering the client starts the lifespan; it must not raise.
            with client:
                pass
        assert settings.idempotency_store is None


# ---------------------------------------------------------------------------
# AC-3/4/5/6: Migration file exists with correct structure
# ---------------------------------------------------------------------------


class TestMigrationFile:
    """AC-3/4/5/6: Migration 010 exists and defines the correct table schema."""

    def test_migration_file_exists(self) -> None:
        from pathlib import Path

        migration = Path(__file__).parent.parent.parent / (
            "src/tapps_brain/migrations/private/010_idempotency_keys.sql"
        )
        assert migration.exists(), "010_idempotency_keys.sql migration file missing"

    def test_migration_contains_key_column(self) -> None:
        from pathlib import Path

        migration = (
            Path(__file__).parent.parent.parent
            / "src/tapps_brain/migrations/private/010_idempotency_keys.sql"
        )
        sql = migration.read_text()
        assert "key" in sql.lower()

    def test_migration_contains_project_id(self) -> None:
        from pathlib import Path

        migration = (
            Path(__file__).parent.parent.parent
            / "src/tapps_brain/migrations/private/010_idempotency_keys.sql"
        )
        sql = migration.read_text()
        assert "project_id" in sql.lower()

    def test_migration_contains_response_hash_equivalent(self) -> None:
        from pathlib import Path

        migration = (
            Path(__file__).parent.parent.parent
            / "src/tapps_brain/migrations/private/010_idempotency_keys.sql"
        )
        sql = migration.read_text()
        # We store response_json (the full response body) rather than just a hash,
        # which satisfies the intent of the story's response_hash requirement.
        assert "response_json" in sql.lower() or "response_hash" in sql.lower()

    def test_migration_contains_created_at(self) -> None:
        from pathlib import Path

        migration = (
            Path(__file__).parent.parent.parent
            / "src/tapps_brain/migrations/private/010_idempotency_keys.sql"
        )
        sql = migration.read_text()
        assert "created_at" in sql.lower()

    def test_migration_version_bumped_to_10(self) -> None:
        from pathlib import Path

        migration = (
            Path(__file__).parent.parent.parent
            / "src/tapps_brain/migrations/private/010_idempotency_keys.sql"
        )
        sql = migration.read_text()
        assert "(10," in sql or "VALUES (10" in sql


# ---------------------------------------------------------------------------
# AC-7: GC sweep integration via maintenance_service
# ---------------------------------------------------------------------------


class TestMaintenanceGcWithIdempotencySweep:
    """AC-7: TTL sweep runs as part of existing GC."""

    def test_gc_sweeps_idempotency_keys_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        monkeypatch.setenv("TAPPS_BRAIN_DATABASE_URL", "postgresql://fake/db")

        gc_result = MagicMock()
        gc_result.model_dump.return_value = {"archived_count": 0, "archived_keys": []}
        gc_result.archived_keys = []

        mock_store = MagicMock()
        mock_store.gc.return_value = gc_result

        with patch("tapps_brain.idempotency.sweep_expired_keys", return_value=3) as mock_sweep:
            from tapps_brain.services.maintenance_service import maintenance_gc

            result = maintenance_gc(mock_store, "proj", "agent", dry_run=False)

        mock_sweep.assert_called_once()
        assert result.get("idempotency_keys_swept") == 3

    def test_gc_does_not_sweep_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TAPPS_BRAIN_IDEMPOTENCY", raising=False)

        gc_result = MagicMock()
        gc_result.model_dump.return_value = {"archived_count": 0, "archived_keys": []}
        gc_result.archived_keys = []

        mock_store = MagicMock()
        mock_store.gc.return_value = gc_result

        with patch("tapps_brain.idempotency.sweep_expired_keys") as mock_sweep:
            from tapps_brain.services.maintenance_service import maintenance_gc

            result = maintenance_gc(mock_store, "proj", "agent", dry_run=False)

        mock_sweep.assert_not_called()
        assert "idempotency_keys_swept" not in result

    def test_gc_dry_run_does_not_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")

        gc_result = MagicMock()
        gc_result.model_dump.return_value = {"archived_count": 0, "archived_keys": []}
        gc_result.archived_keys = []

        mock_store = MagicMock()
        mock_store.gc.return_value = gc_result

        with patch("tapps_brain.idempotency.sweep_expired_keys") as mock_sweep:
            from tapps_brain.services.maintenance_service import maintenance_gc

            result = maintenance_gc(mock_store, "proj", "agent", dry_run=True)

        mock_sweep.assert_not_called()
        assert "idempotency_keys_swept" not in result


# ---------------------------------------------------------------------------
# TAP-629: concurrent same-key requests must execute the handler exactly once
# ---------------------------------------------------------------------------


class TestIdempotencyRaceFixed:
    """TAP-629 acceptance: N concurrent identical POSTs must execute the handler once.

    Before TAP-629 the check → execute → save sequence had a race window:
    two concurrent requests both saw a cache miss, both ran the handler
    (with all its side effects), then raced to save().  ON CONFLICT DO NOTHING
    only deduplicated the *stored response*, not the handler execution.

    After TAP-629 each idempotency key is guarded by a per-key asyncio.Lock
    acquired before the cache check and released after save().  Concurrent
    duplicates yield at ``await guard.acquire()``; when they wake up the
    cached response is already present and they short-circuit.
    """

    def test_concurrent_same_key_executes_handler_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N concurrent identical POSTs → handler runs exactly once.

        A threading-level simulation: N threads each call client.post() with
        the same idempotency key.  The mock ``check()`` / ``save()`` pair
        maintains an in-memory cache that matches real IdempotencyStore
        semantics.  A sleep in the mock handler widens the race window so
        the guard is essential (without it, all N threads would slip through
        to the handler before any of them stores the result).
        """
        import time

        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")

        call_count = 0
        saved_result: dict[str, Any] = {
            "status": "saved",
            "key": "k",
            "tier": "pattern",
            "confidence": 0.8,
            "memory_group": None,
        }
        # Simulated idempotency cache — mirrors real check/save semantics.
        cache: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        call_count_lock = threading.Lock()

        def fake_memory_save(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal call_count
            with call_count_lock:
                call_count += 1
            time.sleep(0.02)  # Widen race window to stress the guard
            return saved_result

        def check_side_effect(project_id: str, key: str) -> tuple[int, dict[str, Any]] | None:
            return cache.get((project_id, key))

        def save_side_effect(
            project_id: str, key: str, status: int, body: dict[str, Any]
        ) -> None:
            cache[(project_id, key)] = (status, body)

        N = 8
        ikey = "race-uuid-tap-629"

        store = _make_store()
        settings = _make_settings(dsn="postgresql://fake/db", store=store)
        client = _client_with_store(settings)

        responses: list[Any] = []
        errors: list[Exception] = []
        resp_lock = threading.Lock()

        def post_request() -> None:
            try:
                r = client.post(
                    "/v1/remember",
                    headers={"x-project-id": "proj", "x-idempotency-key": ikey},
                    json={"key": "k", "value": "v"},
                )
                with resp_lock:
                    responses.append(r)
            except Exception as exc:
                with resp_lock:
                    errors.append(exc)

        inst = MagicMock()
        inst.check = MagicMock(side_effect=check_side_effect)
        inst.save = MagicMock(side_effect=save_side_effect)
        inst.close = MagicMock()

        with (
            # Ensure get_settings() returns the test settings during request
            # handling so that require_data_plane_auth sees auth_token=None
            # and bypasses the bearer-token check (same settings isolation
            # used by _client_with_store during app construction).
            patch.object(_adapter_mod, "get_settings", return_value=settings),
            patch.object(_adapter_mod, "_settings", settings),
            patch(
                "tapps_brain.services.memory_service.memory_save",
                side_effect=fake_memory_save,
            ),
            patch("tapps_brain.idempotency.IdempotencyStore", return_value=inst),
            client,
        ):
            threads = [threading.Thread(target=post_request) for _ in range(N)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        assert not errors, f"Request errors: {errors}"
        assert len(responses) == N, f"Expected {N} responses, got {len(responses)}"
        # Handler must run exactly once.  The per-key asyncio.Lock serializes
        # concurrent coroutines: the first acquires the lock, executes, saves,
        # and releases; the rest wake up, see the cached response, and return
        # without calling fake_memory_save.
        assert call_count == 1, (
            f"TAP-629 regression: handler executed {call_count} times for {N} "
            f"concurrent requests with the same idempotency key — expected exactly 1"
        )
        # All responses must be 200.
        for resp in responses:
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
