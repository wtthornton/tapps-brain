"""Regression tests for TAP-747.

When a client posts an invalid slug key (e.g. starts with `_`), the HTTP adapter
must return HTTP 400 with a structured error body — not HTTP 500.

Affected routes:
  - POST /v1/remember
  - POST /v1/remember:batch (per-item error, not whole-request 500)
  - POST /v1/reinforce  (not affected — reinforce never constructs MemoryEntry from user key)
  - POST /v1/reinforce:batch  (same; included for completeness)
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

# ---------------------------------------------------------------------------
# Helpers — mirrored from test_bulk_operations.py
# ---------------------------------------------------------------------------


def _make_profile() -> MagicMock:
    profile = MagicMock()
    profile.layer_names = ["architectural", "pattern", "procedural", "context"]
    return profile


def _make_store() -> MagicMock:
    store = MagicMock()
    store.profile = _make_profile()
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
def _patched_client(store: Any):  # type: ignore[return]
    """Context manager that yields a TestClient with auth disabled.

    ``get_settings()`` is called at *request time* (after ``create_app`` returns),
    so we must keep the auth_token patch alive for the entire duration of the test
    using a persistent ``patch.object`` on the module-level ``_settings`` singleton.
    """
    settings = _make_settings(store=store)
    mcp_dummy = MagicMock()
    mcp_dummy.session_manager = None
    with (
        patch.object(_adapter_mod, "_settings", settings),
        patch.object(_adapter_mod, "get_settings", return_value=settings),
    ):
        app = create_app(store=store, mcp_server=mcp_dummy)
        client = TestClient(app, raise_server_exceptions=False)
        with client:
            yield client


# ---------------------------------------------------------------------------
# Service-layer unit test
# ---------------------------------------------------------------------------


class TestMemorySaveSlugValidation:
    """memory_save() returns a structured dict instead of raising pydantic error."""

    def test_invalid_key_returns_bad_request_dict(self) -> None:
        """TAP-747: store.save raising ValidationError is caught; returns error dict."""

        from tapps_brain.services.memory_service import memory_save

        store = MagicMock()
        store.profile = _make_profile()

        # Simulate pydantic raising ValidationError inside store.save()
        from tapps_brain.models import MemoryEntry

        def _raise_validation_error(**kwargs: Any) -> None:
            # Trigger real pydantic validation by constructing MemoryEntry with bad key
            MemoryEntry(key="_leading_underscore", value="v")

        store.save.side_effect = _raise_validation_error

        result = memory_save(store, "proj", "agent", key="_leading_underscore", value="v")

        assert result.get("error") == "bad_request", f"expected bad_request, got: {result}"
        assert "message" in result
        assert isinstance(result["message"], str)
        assert len(result["message"]) > 0
        # The pydantic error message should include the key name or slug constraint hint
        assert "_leading_underscore" in result["message"] or "slug" in result["message"].lower()

    def test_valid_key_passes_through(self) -> None:
        """TAP-747: valid slug key does not trigger error path."""
        from tapps_brain.services.memory_service import memory_save

        store = MagicMock()
        store.profile = _make_profile()
        mock_entry = MagicMock(
            key="valid-key",
            tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8,
            memory_group=None,
        )
        store.save.return_value = mock_entry

        result = memory_save(store, "proj", "agent", key="valid-key", value="some value")

        assert result.get("status") == "saved"
        assert result.get("key") == "valid-key"


# ---------------------------------------------------------------------------
# HTTP-layer integration tests
# ---------------------------------------------------------------------------


class TestRememberRoute400OnBadKey:
    """POST /v1/remember returns 400 (not 500) for invalid slug key."""

    def test_leading_underscore_key_returns_400(self) -> None:
        """TAP-747: _leading_underscore key must return 400, not 500."""
        from tapps_brain.models import MemoryEntry

        store = _make_store()

        def _raise(**kwargs: Any) -> None:
            MemoryEntry(key="_leading_underscore", value="x")

        store.save.side_effect = _raise

        with _patched_client(store) as client:
            resp = client.post(
                "/v1/remember",
                json={"key": "_leading_underscore", "value": "x"},
                headers={"X-Project-Id": "test-proj"},
            )

        assert resp.status_code == 400, f"expected 400, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "error" in body

    def test_valid_key_returns_200(self) -> None:
        """TAP-747: valid slug key is unaffected by the fix."""
        store = _make_store()
        mock_entry = MagicMock(
            key="good-key",
            tier=MagicMock(__str__=lambda s: "pattern"),
            confidence=0.8,
            memory_group=None,
        )
        store.save.return_value = mock_entry

        with _patched_client(store) as client:
            resp = client.post(
                "/v1/remember",
                json={"key": "good-key", "value": "some value"},
                headers={"X-Project-Id": "test-proj"},
            )

        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"


class TestRememberBatchRoute400OnBadKey:
    """POST /v1/remember:batch surfaces per-item 400 for invalid slug keys."""

    def test_bad_key_in_batch_is_per_item_error_not_500(self) -> None:
        """TAP-747: invalid slug key in a batch entry must appear as per-item error."""
        from tapps_brain.models import MemoryEntry

        store = _make_store()

        def _side_effect(**kwargs: Any) -> Any:
            key = kwargs.get("key", "")
            if key.startswith("_"):
                MemoryEntry(key=key, value="x")  # triggers ValidationError
            return MagicMock(
                key=key,
                tier=MagicMock(__str__=lambda s: "pattern"),
                confidence=0.8,
                memory_group=None,
            )

        store.save.side_effect = _side_effect

        with _patched_client(store) as client:
            resp = client.post(
                "/v1/remember:batch",
                json={
                    "entries": [
                        {"key": "good-key", "value": "v1"},
                        {"key": "_bad-key", "value": "v2"},
                    ]
                },
                headers={"X-Project-Id": "test-proj"},
            )

        # The request should succeed (200) with the batch summary
        assert resp.status_code == 200, f"unexpected status: {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["saved_count"] == 1
        assert body["error_count"] == 1
        results = body["results"]
        # First entry should have saved successfully
        assert results[0].get("status") == "saved"
        # Second entry should surface the validation error (service-layer key: "message")
        assert results[1].get("error") == "bad_request"
        assert "message" in results[1]
