"""Unit tests for brain-central doc lookup and import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.docs_import import import_cache_dir
from tapps_brain.context7_sync import extract_context7_content
from tapps_brain.docs_lookup import (
    DocsConfig,
    decode_doc_value,
    doc_memory_key,
    docs_lookup,
    docs_warm,
)


class _MemStore:
    """Minimal in-memory stand-in for MemoryStore + memory_service backend."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}


def _install_memory_service_fake(monkeypatch: pytest.MonkeyPatch, store: _MemStore) -> None:
    from tapps_brain.services import memory_service

    def _get(store_obj: Any, project_id: str, agent_id: str, *, key: str) -> dict[str, Any]:
        row = store.rows.get((project_id, agent_id, key))
        if row is None:
            return {"error": "not_found", "key": key}
        return row

    def _save(
        store_obj: Any,
        project_id: str,
        agent_id: str,
        *,
        key: str,
        value: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        store.rows[(project_id, agent_id, key)] = {"key": key, "value": value, "access_count": 0}
        return {"ok": True, "key": key}

    monkeypatch.setattr(memory_service, "memory_get", _get)
    monkeypatch.setattr(memory_service, "memory_save", _save)


def test_decode_doc_value_json_payload() -> None:
    raw = json.dumps({"content": "body", "provider_source": "context7", "context7_id": "/x"})
    payload = decode_doc_value(raw)
    assert payload["content"] == "body"
    assert payload["context7_id"] == "/x"


def test_extract_context7_content_snippets() -> None:
    data = {
        "snippets": [
            {
                "title": "Example",
                "content": "Description",
                "codeList": ["print('hi')", {"language": "python", "code": "pass"}],
            }
        ]
    }
    text = extract_context7_content(data)
    assert "### Example" in text
    assert "Description" in text
    assert "print('hi')" in text
    assert "```python" in text


def test_docs_lookup_missing_library() -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key="key",
    )
    result = docs_lookup(store, library="   ", config=cfg)
    assert result["success"] is False
    assert "required" in result["error"]


def test_docs_lookup_stale_fallback_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=1,
        context7_api_key=None,
    )
    key = doc_memory_key("httpx", "overview")
    from tapps_brain.docs_lookup import _encode_doc_value

    store.rows[(cfg.project_id, cfg.agent_id, key)] = {
        "key": key,
        "value": _encode_doc_value(
            content="stale body",
            library="httpx",
            topic="overview",
            mode="code",
            context7_id="/httpx",
            provider_source="context7",
        ),
        "access_count": 1,
    }
    _install_memory_service_fake(monkeypatch, store)
    monkeypatch.setattr(
        "tapps_brain.docs_lookup._is_fresh",
        lambda _payload, _ttl: False,
    )
    result = docs_lookup(store, library="httpx", topic="overview", config=cfg)
    assert result["success"] is True
    assert result["source"] == "stale_fallback"
    assert result["content"] == "stale body"
    assert "CONTEXT7_API_KEY" in result["warning"]


def test_docs_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAPPS_BRAIN_DOCS_PROJECT_ID", "custom-docs")
    monkeypatch.setenv("DOCS_CACHE_TTL", "7200")
    monkeypatch.setenv("CONTEXT7_API_KEY", "secret")
    cfg = DocsConfig.from_env()
    assert cfg.project_id == "custom-docs"
    assert cfg.cache_ttl_seconds == 7200.0
    assert cfg.context7_api_key == "secret"


def test_doc_memory_key_normalizes() -> None:
    assert doc_memory_key("FastAPI", "Routing") == "docs:fastapi:routing"


def test_decode_doc_value_legacy_markdown() -> None:
    payload = decode_doc_value("# Hello")
    assert payload["content"] == "# Hello"


def test_docs_lookup_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
    )
    key = doc_memory_key("pytest", "fixtures")
    from tapps_brain.docs_lookup import _encode_doc_value

    store.rows[(cfg.project_id, cfg.agent_id, key)] = {
        "key": key,
        "value": _encode_doc_value(
            content="cached body",
            library="pytest",
            topic="fixtures",
            mode="code",
            context7_id="/pytest/docs",
            provider_source="context7",
        ),
        "access_count": 2,
    }
    _install_memory_service_fake(monkeypatch, store)
    result = docs_lookup(store, library="pytest", topic="fixtures", config=cfg)
    assert result["success"] is True
    assert result["cache_hit"] is True
    assert result["content"] == "cached body"
    assert result["context7_id"] == "/pytest/docs"


@patch("tapps_brain.docs_lookup.SyncContext7Client")
def test_docs_lookup_fetches_context7(
    mock_client_cls: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key="test-key",
    )
    _install_memory_service_fake(monkeypatch, store)
    client = mock_client_cls.return_value
    client.resolve_library.return_value = [{"id": "/fastapi/fastapi", "title": "FastAPI"}]
    client.fetch_docs.return_value = "# FastAPI routing"

    result = docs_lookup(store, library="fastapi", topic="routing", config=cfg)
    assert result["success"] is True
    assert result["cache_hit"] is False
    assert result["source"] == "api"
    assert "fastapi" in result["content"].lower() or result["content"] == "# FastAPI routing"
    key = doc_memory_key("fastapi", "routing")
    assert (cfg.project_id, cfg.agent_id, key) in store.rows


def test_docs_warm_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
    )
    _install_memory_service_fake(monkeypatch, store)

    def _fake_lookup(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "no key", "cache_hit": False}

    monkeypatch.setattr("tapps_brain.docs_lookup.docs_lookup", _fake_lookup)
    report = docs_warm(store, ["httpx", "pytest"], config=cfg)
    assert report["count"] == 0
    assert len(report["failed"]) == 2


def test_import_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
    )
    _install_memory_service_fake(monkeypatch, store)
    lib_dir = tmp_path / "httpx"
    lib_dir.mkdir()
    (lib_dir / "overview.md").write_text("# httpx overview", encoding="utf-8")
    meta = {
        "library": "httpx",
        "topic": "overview",
        "context7_id": "/encode/httpx",
        "provider_source": "context7",
    }
    (lib_dir / "overview.meta.json").write_text(json.dumps(meta), encoding="utf-8")

    report = import_cache_dir(store, tmp_path, config=cfg)
    assert report.imported == 1
    assert report.failed == 0
    key = doc_memory_key("httpx", "overview")
    assert (cfg.project_id, cfg.agent_id, key) in store.rows

    report2 = import_cache_dir(store, tmp_path, config=cfg, skip_existing=True)
    assert report2.skipped == 1
    assert report2.imported == 0
