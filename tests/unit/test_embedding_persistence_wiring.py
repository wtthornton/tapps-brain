"""Unit tests for embedding persistence wiring (TAP-2672).

Covers the non-DB pieces of the embedding write-path fix:

* ``embedding_to_pgvector`` literal formatting
* ``embedding_startup_status`` summary
* ``build_save_params`` carries the embedding literal as its last element
* ``MemoryStore`` fail-loud when ``TAPPS_BRAIN_EMBEDDING_REQUIRED=1`` but no
  provider is available
* ``health().embeddings_enabled`` reflects provider presence

The "save actually writes the embedding column" proof lives in the Postgres
integration tests (the in-memory unit backend cannot exercise the SQL).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain._postgres_private_sql import build_save_params, embedding_to_pgvector
from tapps_brain.embeddings import embedding_startup_status
from tapps_brain.models import MemoryEntry
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


class _StubProvider:
    """Minimal embedding provider stub (no model download)."""

    def __init__(self, dimension: int = 384, *, model_id: str | None = "stub@v1") -> None:
        self._dimension = dimension
        self._model_id = model_id

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_id(self) -> str | None:
        return self._model_id

    def embed(self, text: str) -> list[float]:
        return [0.1] * self._dimension

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dimension for _ in texts]


class TestEmbeddingToPgvector:
    def test_none_returns_none(self) -> None:
        assert embedding_to_pgvector(None) is None

    def test_empty_returns_none(self) -> None:
        assert embedding_to_pgvector([]) is None

    def test_values_render_bracketed_literal(self) -> None:
        assert embedding_to_pgvector([1.0, -0.5, 0.25]) == "[1.0,-0.5,0.25]"


class TestEmbeddingStartupStatus:
    def test_none_provider_disabled(self) -> None:
        assert embedding_startup_status(None) == {
            "enabled": False,
            "model_id": None,
            "dimension": None,
        }

    def test_provider_reports_identity(self) -> None:
        status = embedding_startup_status(_StubProvider(dimension=384, model_id="m@1"))
        assert status == {"enabled": True, "model_id": "m@1", "dimension": 384}


class TestBuildSaveParamsEmbedding:
    def test_embedding_literal_is_last_param(self) -> None:
        entry = MemoryEntry(key="k", value="v", embedding=[1.0, 2.0])
        params = build_save_params(entry=entry, project_id="p", agent_id="a")
        assert params[-1] == "[1.0,2.0]"

    def test_missing_embedding_yields_null_param(self) -> None:
        entry = MemoryEntry(key="k", value="v")
        params = build_save_params(entry=entry, project_id="p", agent_id="a")
        assert params[-1] is None


class TestMemoryStoreEmbeddingRequired:
    def test_required_but_absent_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_EMBEDDING_REQUIRED", "1")
        with pytest.raises(RuntimeError, match="EMBEDDING_REQUIRED"):
            MemoryStore(tmp_path, embedding_provider=None)

    def test_not_required_absent_is_allowed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_EMBEDDING_REQUIRED", "0")
        store = MemoryStore(tmp_path, embedding_provider=None)
        assert store.health().embeddings_enabled is False
        store.close()

    def test_required_with_provider_starts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_EMBEDDING_REQUIRED", "1")
        store = MemoryStore(tmp_path, embedding_provider=_StubProvider())
        assert store.health().embeddings_enabled is True
        store.close()
