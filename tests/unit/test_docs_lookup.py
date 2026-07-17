"""Unit tests for brain-central doc lookup and import."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.context7_sync import Context7Error, extract_context7_content
from tapps_brain.docs_import import import_cache_dir
from tapps_brain.docs_lookup import (
    DEFAULT_DOCS_CACHE_TTL_SECONDS,
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
        llms_txt_fallback=False,
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
        llms_txt_fallback=False,
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
    monkeypatch.setenv("DOCS_LLMS_TXT_FALLBACK", "0")
    cfg = DocsConfig.from_env()
    assert cfg.project_id == "custom-docs"
    assert cfg.cache_ttl_seconds == 7200.0
    assert cfg.context7_api_key == "secret"
    assert cfg.llms_txt_fallback is False


def test_open_docs_store_requires_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from tapps_brain.docs_lookup import open_docs_store

    monkeypatch.delenv("TAPPS_BRAIN_DATABASE_URL", raising=False)
    monkeypatch.delenv("TAPPS_BRAIN_HIVE_DSN", raising=False)
    with pytest.raises(ValueError, match="TAPPS_BRAIN_DATABASE_URL"):
        open_docs_store()


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
        llms_txt_fallback=False,
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


def test_docs_lookup_invalid_cached_at_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key="test-key",
        llms_txt_fallback=False,
    )
    key = doc_memory_key("pytest", "fixtures")
    payload = {
        "content": "stale body",
        "library": "pytest",
        "topic": "fixtures",
        "mode": "code",
        "context7_id": "/pytest/docs",
        "provider_source": "context7",
        "cached_at": "not-a-timestamp",
    }
    store.rows[(cfg.project_id, cfg.agent_id, key)] = {
        "key": key,
        "value": json.dumps(payload),
        "access_count": 2,
    }
    _install_memory_service_fake(monkeypatch, store)
    monkeypatch.setattr(
        "tapps_brain.docs_lookup._fetch_remote_docs",
        lambda *_args, **_kwargs: ("/pytest/docs", "fresh body", "context7"),
    )

    result = docs_lookup(store, library="pytest", topic="fixtures", config=cfg)

    assert result["success"] is True
    assert result["cache_hit"] is False
    assert result["content"] == "fresh body"


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
        llms_txt_fallback=False,
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
        llms_txt_fallback=False,
    )
    _install_memory_service_fake(monkeypatch, store)

    def _fake_lookup(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "no key", "cache_hit": False}

    monkeypatch.setattr("tapps_brain.docs_lookup.docs_lookup", _fake_lookup)
    report = docs_warm(store, ["httpx", "pytest"], config=cfg)
    assert report["count"] == 0
    assert len(report["failed"]) == 2


def test_docs_warm_cache_hits_count_as_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
        llms_txt_fallback=False,
    )
    _install_memory_service_fake(monkeypatch, store)

    def _fake_lookup(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"success": True, "cache_hit": True, "content": "cached"}

    monkeypatch.setattr("tapps_brain.docs_lookup.docs_lookup", _fake_lookup)
    report = docs_warm(store, ["httpx", "pytest"], config=cfg)

    assert report["count"] == 0
    assert report["warmed"] == []
    assert report["skipped"] == ["httpx", "pytest"]


def test_import_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
        llms_txt_fallback=False,
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


@patch("tapps_brain.docs_lookup._fetch_from_llms_txt")
@patch("tapps_brain.docs_lookup._fetch_from_context7")
def test_docs_lookup_falls_back_to_llms_txt(
    mock_context7: MagicMock,
    mock_llms: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key="test-key",
        llms_txt_fallback=True,
    )
    _install_memory_service_fake(monkeypatch, store)
    mock_context7.side_effect = Context7Error("Context7 down")
    mock_llms.return_value = ("https://docs.pytest.org/llms.txt", "# pytest", "llmstxt")

    result = docs_lookup(store, library="pytest", topic="fixtures", config=cfg)
    assert result["success"] is True
    assert result["provider_source"] == "llmstxt"
    assert result["source"] == "api"
    mock_llms.assert_called_once()


def test_persist_doc_entry_uses_hive_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
        llms_txt_fallback=False,
    )
    captured: dict[str, Any] = {}

    def _save(
        store_obj: Any,
        project_id: str,
        agent_id: str,
        *,
        key: str,
        value: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        captured.update(kwargs)
        store.rows[(project_id, agent_id, key)] = {"key": key, "value": value, "access_count": 0}
        return {"ok": True, "key": key}

    from tapps_brain.services import memory_service

    monkeypatch.setattr(memory_service, "memory_save", _save)
    from tapps_brain.docs_lookup import _persist_doc_entry

    _persist_doc_entry(
        store,
        cfg,
        library="pytest",
        topic="overview",
        mode="code",
        content="body",
        context7_id="/pytest",
        provider_source="context7",
    )
    assert captured.get("agent_scope") == "hive"
    assert captured.get("group") == "library-docs"


def test_import_cache_dir_meta_library_remap_does_not_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A meta.json library remap must not apply to sibling files in the same dir."""
    store = _MemStore()
    cfg = DocsConfig(
        project_id="library-docs",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key=None,
        llms_txt_fallback=False,
    )
    _install_memory_service_fake(monkeypatch, store)
    lib_dir = tmp_path / "fastapi"
    lib_dir.mkdir()
    (lib_dir / "overview.md").write_text("overview body", encoding="utf-8")
    (lib_dir / "routing.md").write_text("routing body", encoding="utf-8")
    (lib_dir / "overview.meta.json").write_text(
        json.dumps({"library": "other-lib", "topic": "overview"}),
        encoding="utf-8",
    )

    report = import_cache_dir(store, tmp_path, config=cfg, skip_existing=False)
    assert report.imported == 2
    assert report.failed == 0
    assert (cfg.project_id, cfg.agent_id, doc_memory_key("other-lib", "overview")) in store.rows
    assert (cfg.project_id, cfg.agent_id, doc_memory_key("fastapi", "routing")) in store.rows


def test_docs_config_invalid_ttl_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCS_CACHE_TTL", "not-a-number")
    cfg = DocsConfig.from_env()
    assert cfg.cache_ttl_seconds == DEFAULT_DOCS_CACHE_TTL_SECONDS


def test_docs_config_negative_ttl_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCS_CACHE_TTL", "-10")
    cfg = DocsConfig.from_env()
    assert cfg.cache_ttl_seconds == DEFAULT_DOCS_CACHE_TTL_SECONDS


def test_doc_memory_key_sanitizes_topic_colons() -> None:
    assert doc_memory_key("lib", "a:b") == "docs:lib:a_b"
