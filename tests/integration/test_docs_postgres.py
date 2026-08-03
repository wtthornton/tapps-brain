"""Postgres integration tests for brain-central doc lookup (TAP-3865/3866).

Verifies ``docs_lookup`` / ``docs_warm`` and ``import_cache_dir`` persist and
read through a real ``MemoryStore`` + ``PostgresPrivateBackend``.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (``@pytest.mark.requires_postgres``).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _docs_config(*, with_api_key: bool = True) -> Any:
    from tapps_brain.docs_lookup import DocsConfig

    suffix = uuid.uuid4().hex[:8]
    return DocsConfig(
        project_id=f"library-docs-test-{suffix}",
        agent_id="docs-cache",
        cache_ttl_seconds=3600,
        context7_api_key="test-key" if with_api_key else None,
        llms_txt_fallback=False,
    )


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


@pytest.fixture
def docs_cfg() -> Any:
    return _docs_config()


@pytest.fixture
def docs_store(docs_cfg: Any) -> Any:
    from tapps_brain.docs_lookup import open_docs_store

    store = open_docs_store(docs_cfg)
    yield store
    store.close()


class TestDocsLookupPostgres:
    """``docs_lookup`` write-through cache against live Postgres."""

    @patch("tapps_brain.docs_lookup.SyncContext7Client")
    def test_persist_and_cache_hit(
        self,
        mock_cls: MagicMock,
        docs_store: Any,
        docs_cfg: Any,
    ) -> None:
        from tapps_brain.docs_lookup import docs_lookup

        client = mock_cls.return_value
        client.resolve_library.return_value = [{"id": "/pytest/docs", "title": "pytest"}]
        client.fetch_docs.return_value = "# pytest docs body"

        first = docs_lookup(docs_store, library="pytest", topic="overview", config=docs_cfg)
        assert first["success"] is True
        assert first["cache_hit"] is False
        assert first["source"] == "api"
        assert first["context7_id"] == "/pytest/docs"

        second = docs_lookup(docs_store, library="pytest", topic="overview", config=docs_cfg)
        assert second["success"] is True
        assert second["cache_hit"] is True
        assert second["source"] == "cache"
        assert second["content"] == "# pytest docs body"
        mock_cls.assert_called_once()

    @patch("tapps_brain.docs_lookup.SyncContext7Client")
    def test_docs_warm_batch(
        self,
        mock_cls: MagicMock,
        docs_store: Any,
        docs_cfg: Any,
    ) -> None:
        from tapps_brain.docs_lookup import docs_warm

        client = mock_cls.return_value
        client.resolve_library.return_value = [{"id": "/httpx/docs", "title": "httpx"}]
        client.fetch_docs.return_value = "# httpx overview"

        report = docs_warm(docs_store, ["httpx", "pytest"], topic="overview", config=docs_cfg)
        assert report["count"] == 2
        assert "httpx" in report["warmed"]
        assert "pytest" in report["warmed"]


class TestDocsImportPostgres:
    """``docs import-dir`` migrator against live Postgres."""

    def test_import_dir_then_lookup_cache_hit(self, tmp_path: Path) -> None:
        from tapps_brain.docs_import import import_cache_dir
        from tapps_brain.docs_lookup import docs_lookup, open_docs_store

        cfg = _docs_config(with_api_key=False)
        store = open_docs_store(cfg)
        try:
            lib_dir = tmp_path / "httpx"
            lib_dir.mkdir()
            (lib_dir / "overview.md").write_text("# httpx from disk", encoding="utf-8")
            meta = {
                "library": "httpx",
                "topic": "overview",
                "context7_id": "/encode/httpx",
                "provider_source": "import",
            }
            (lib_dir / "overview.meta.json").write_text(json.dumps(meta), encoding="utf-8")

            report = import_cache_dir(store, tmp_path, config=cfg)
            assert report.imported == 1
            assert report.failed == 0

            result = docs_lookup(store, library="httpx", topic="overview", config=cfg)
            assert result["success"] is True
            assert result["cache_hit"] is True
            assert "httpx from disk" in result["content"]
            assert result["context7_id"] == "/encode/httpx"

            report2 = import_cache_dir(store, tmp_path, config=cfg, skip_existing=True)
            assert report2.skipped == 1
            assert report2.imported == 0
        finally:
            store.close()


class TestDocsStoreIsolation:
    """Docs cache uses library-docs project, not the MCP caller project."""

    @patch("tapps_brain.docs_lookup.SyncContext7Client")
    def test_shared_library_docs_project(
        self,
        mock_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        from tapps_brain.docs_lookup import docs_lookup, open_docs_store
        from tapps_brain.store import MemoryStore

        cfg = _docs_config()
        docs_store = open_docs_store(cfg)
        caller_store = MemoryStore(tmp_path)
        client = mock_cls.return_value
        client.resolve_library.return_value = [{"id": "/ruff/docs", "title": "ruff"}]
        client.fetch_docs.return_value = "# ruff lint rules"
        try:
            docs_lookup(docs_store, library="ruff", topic="overview", config=cfg)
            assert caller_store.get("docs.ruff.overview") is None
            second = docs_lookup(docs_store, library="ruff", topic="overview", config=cfg)
            assert second["cache_hit"] is True
        finally:
            docs_store.close()
            caller_store.close()
