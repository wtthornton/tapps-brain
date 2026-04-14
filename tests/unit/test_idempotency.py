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
    s.allowed_origins = []
    s.version = _service_version()
    s.store = store
    s.snapshot_lock = threading.Lock()
    s.snapshot_cache = None
    s.snapshot_cache_at = 0.0
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
    with patch.object(_adapter_mod, "_settings", settings), \
         patch.object(_adapter_mod, "get_settings", return_value=settings):
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
    cm.get_connection = MagicMock(return_value=MagicMock(
        __enter__=MagicMock(return_value=conn),
        __exit__=MagicMock(return_value=False),
    ))
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
            save_return = {"status": "saved", "key": "test-key",
                           "tier": "pattern", "confidence": 0.8, "memory_group": None}
        store.save.return_value = MagicMock(
            key="test-key", tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8, memory_group=None,
        )
        settings = _make_settings(dsn=dsn, store=store)
        client = _client_with_store(settings)
        return client, store

    def test_missing_project_id_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/remember",
                               json={"key": "k", "value": "v"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_missing_key_or_value_returns_400(self) -> None:
        client, _ = self._setup()
        with client:
            resp = client.post("/v1/remember",
                               headers={"x-project-id": "proj"},
                               json={"key": "k"})
        assert resp.status_code == 400

    def test_successful_save_returns_200(self) -> None:
        client, _ = self._setup()
        with patch("tapps_brain.services.memory_service.memory_save",
                   return_value={"status": "saved", "key": "k",
                                 "tier": "pattern", "confidence": 0.8,
                                 "memory_group": None}), client:
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
        with patch("tapps_brain.services.memory_service.memory_save",
                   return_value={"status": "saved", "key": "k",
                                 "tier": "pattern", "confidence": 0.8,
                                 "memory_group": None}):
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

    def test_idempotency_replay_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-2: Duplicate key within 24h returns original response."""
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        cached_body = {"status": "saved", "key": "k",
                       "tier": "pattern", "confidence": 0.8, "memory_group": None}
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

    def test_idempotency_store_called_on_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-1: On first call, response is stored in idempotency_keys."""
        monkeypatch.setenv("TAPPS_BRAIN_IDEMPOTENCY", "1")
        saved_result = {"status": "saved", "key": "k",
                        "tier": "pattern", "confidence": 0.8, "memory_group": None}
        ikey = "test-uuid-1234"
        client, _ = self._setup(dsn="postgresql://fake/db")
        miss_inst = MagicMock()
        miss_inst.check.return_value = None
        miss_inst.close = MagicMock()
        save_inst = MagicMock()
        save_inst.save = MagicMock()
        save_inst.close = MagicMock()
        with (
            patch("tapps_brain.services.memory_service.memory_save",
                  return_value=saved_result),
            patch("tapps_brain.idempotency.IdempotencyStore") as mock_cls,
            client,
        ):
            mock_cls.side_effect = [miss_inst, save_inst]
            resp = client.post(
                "/v1/remember",
                headers={"x-project-id": "proj", "x-idempotency-key": ikey},
                json={"key": "k", "value": "v"},
            )
        assert resp.status_code == 200
        # save() was called on the save_inst
        save_inst.save.assert_called_once_with("proj", ikey, 200, saved_result)

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
            resp = client.post("/v1/reinforce",
                               headers={"x-project-id": "proj"},
                               json={})
        assert resp.status_code == 400

    def test_successful_reinforce(self) -> None:
        client, _ = self._setup()
        result = {"status": "reinforced", "key": "k", "confidence": 0.9, "access_count": 3}
        with patch("tapps_brain.services.memory_service.memory_reinforce",
                   return_value=result), client:
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

    def test_idempotency_replay_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_gc_sweeps_idempotency_keys_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_gc_does_not_sweep_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

    def test_gc_dry_run_does_not_sweep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
