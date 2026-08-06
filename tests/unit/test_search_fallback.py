"""TAP-5677: staged search fallback — lexical OR tsquery + vector KNN.

Covers the pure OR-token builder / SQL variant in ``_postgres_private_sql``
and the store-level KNN fallback gating in ``QueryMixin.search``.  The
Postgres round-trip for the OR retry lives in
``tests/integration/test_postgres_private_backend.py`` (requires_postgres).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tapps_brain._postgres_private_sql import build_or_tsquery, build_search_sql
from tapps_brain.store import MemoryStore
from tests.unit.test_private_backend import _make_backend

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


class TestBuildOrTsquery:
    """Sanitized ``" | "``-joined token string for the to_tsquery OR retry."""

    def test_multiword_query_joins_tokens(self) -> None:
        assert build_or_tsquery("postgres hive retrieval") == "postgres | hive | retrieval"

    def test_single_token_returns_none(self) -> None:
        assert build_or_tsquery("postgres") is None

    def test_empty_query_returns_none(self) -> None:
        assert build_or_tsquery("   ") is None

    def test_punctuation_stripped_and_lowercased(self) -> None:
        assert build_or_tsquery("ADR-007, Postgres!") == "adr | 007 | postgres"

    def test_duplicate_tokens_removed_order_preserved(self) -> None:
        assert build_or_tsquery("cat dog cat bird dog") == "cat | dog | bird"

    def test_token_count_capped_at_16(self) -> None:
        query = " ".join(f"tok{i}" for i in range(30))
        result = build_or_tsquery(query)
        assert result is not None
        assert len(result.split(" | ")) == 16

    def test_duplicate_only_pair_returns_none(self) -> None:
        # Two occurrences of one distinct token: OR would equal AND.
        assert build_or_tsquery("postgres postgres") is None


class TestBuildSearchSqlMatchAny:
    """``match_any=True`` swaps plainto_tsquery for to_tsquery, params unchanged."""

    @staticmethod
    def _build(**kwargs: Any) -> tuple[str, list[Any]]:
        return build_search_sql(
            memory_group=None,
            since=None,
            until=None,
            time_field="created_at",
            memory_class=None,
            as_of=None,
            **kwargs,
        )

    def test_default_uses_plainto_tsquery(self) -> None:
        sql, _ = self._build()
        assert "plainto_tsquery" in sql
        assert "to_tsquery('english'" in sql  # plainto_tsquery contains the substring

    def test_match_any_uses_to_tsquery_only(self) -> None:
        sql, _ = self._build(match_any=True)
        assert "plainto_tsquery" not in sql
        assert sql.count("to_tsquery('english', %s)") == 2  # rank + WHERE positions

    def test_match_any_extra_params_identical(self) -> None:
        _, params_and = self._build()
        _, params_or = self._build(match_any=True)
        assert params_and == params_or


class TestPostgresOrRetry:
    """Stage-2 OR retry against a mocked cursor (PostgresPrivateBackend)."""

    def test_empty_and_pass_retries_with_or_tsquery(self) -> None:
        backend, cur = _make_backend(rows=[], col_names=[])
        backend.search("test query")
        retry_sql, retry_params = cur.execute.call_args_list[1][0]
        assert "plainto_tsquery" not in retry_sql
        assert "to_tsquery" in retry_sql
        assert retry_params[0] == "test | query"

    def test_single_token_empty_does_not_retry(self) -> None:
        # One distinct token: OR equals AND, so no second execute.
        backend, cur = _make_backend(rows=[], col_names=[])
        backend.search("solitary")
        assert cur.execute.call_count == 1


# Matches the private_memories embedding column (vector(384), migration 002)
# so these tests also pass when TAPPS_BRAIN_DATABASE_URL routes them at a
# real Postgres backend (the CI matrix does).
_EMBED_DIM = 384


class _FakeEmbeddingProvider:
    """Deterministic stand-in for SentenceTransformerProvider."""

    model_id = "fake-model"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fail:
            msg = "embed failed"
            raise RuntimeError(msg)
        return [0.1] * _EMBED_DIM


@pytest.fixture()
def store(tmp_path: Path) -> Generator[MemoryStore, None, None]:
    """Store with a fake embedding provider (in-memory backend from conftest)."""
    s = MemoryStore(tmp_path, embedding_provider=_FakeEmbeddingProvider())  # type: ignore[arg-type]
    yield s
    s.close()


class TestKnnFallback:
    """Stage-3 semantic fallback fires only when lexical stages return empty."""

    def test_lexical_hit_never_calls_knn(self, store: MemoryStore) -> None:
        store.save(key="k-alpha", value="alpha bravo content")

        def _knn_boom(*args: Any, **kwargs: Any) -> list[tuple[str, float]]:
            msg = "knn_search must not be called on a lexical hit"
            raise AssertionError(msg)

        store._persistence.knn_search = _knn_boom  # type: ignore[attr-defined]
        results = store.search("alpha")
        assert [e.key for e in results] == ["k-alpha"]

    def test_knn_fallback_on_lexical_miss_orders_by_distance(self, store: MemoryStore) -> None:
        store.save(key="k-near", value="alpha content")
        store.save(key="k-far", value="bravo content")

        def _knn(embedding: list[float], k: int, **kwargs: Any) -> list[tuple[str, float]]:
            assert embedding == [0.1] * _EMBED_DIM
            return [("k-near", 0.05), ("k-far", 0.4)]

        store._persistence.knn_search = _knn  # type: ignore[attr-defined]
        results = store.search("zzzunknown qqqmissing")
        assert [e.key for e in results] == ["k-near", "k-far"]

    def test_no_provider_returns_empty(self, tmp_path: Path) -> None:
        s = MemoryStore(tmp_path, embedding_provider=None)
        try:
            s.save(key="k1", value="alpha content")
            s._persistence.knn_search = (  # type: ignore[attr-defined]
                lambda *a, **kw: [("k1", 0.1)]
            )
            assert s.search("zzzunknown qqqmissing") == []
        finally:
            s.close()

    def test_backend_without_knn_returns_empty(self, store: MemoryStore) -> None:
        # Force-absent knn_search: works for both the in-memory backend (no
        # such attribute) and a DSN-routed Postgres backend (real method).
        store.save(key="k1", value="alpha content")
        store._persistence.knn_search = None  # type: ignore[attr-defined]
        assert store.search("zzzunknown qqqmissing") == []

    def test_embed_failure_returns_empty_without_raising(self, tmp_path: Path) -> None:
        provider = _FakeEmbeddingProvider(fail=True)
        s = MemoryStore(tmp_path, embedding_provider=provider)  # type: ignore[arg-type]
        try:
            s.save(key="k1", value="alpha content")
            s._persistence.knn_search = (  # type: ignore[attr-defined]
                lambda *a, **kw: [("k1", 0.1)]
            )
            assert s.search("zzzunknown qqqmissing") == []
            assert provider.calls  # embed was attempted
        finally:
            s.close()

    def test_knn_error_returns_empty_without_raising(self, store: MemoryStore) -> None:
        store.save(key="k1", value="alpha content")

        def _knn_fail(*args: Any, **kwargs: Any) -> list[tuple[str, float]]:
            msg = "hnsw unavailable"
            raise RuntimeError(msg)

        store._persistence.knn_search = _knn_fail  # type: ignore[attr-defined]
        assert store.search("zzzunknown qqqmissing") == []

    def test_knn_results_respect_tier_filter(self, store: MemoryStore) -> None:
        store.save(key="k-ctx", value="alpha content", tier="context")

        store._persistence.knn_search = (  # type: ignore[attr-defined]
            lambda *a, **kw: [("k-ctx", 0.1)]
        )
        assert store.search("zzzunknown qqqmissing", tier="architectural") == []
        results = store.search("zzzunknown qqqmissing", tier="context")
        assert [e.key for e in results] == ["k-ctx"]

    def test_no_fallback_when_since_filter_set(self, store: MemoryStore) -> None:
        """Temporal windows live in FTS SQL — the fallback must stand down."""
        store.save(key="k1", value="alpha content")

        def _knn_boom(*args: Any, **kwargs: Any) -> list[tuple[str, float]]:
            msg = "knn_search must not run for since/until queries"
            raise AssertionError(msg)

        store._persistence.knn_search = _knn_boom  # type: ignore[attr-defined]
        assert store.search("zzzunknown qqqmissing", since="2099-01-01T00:00:00+00:00") == []
        assert store.search("zzzunknown qqqmissing", until="2000-01-01T00:00:00+00:00") == []

    def test_no_fallback_when_memory_class_set(self, store: MemoryStore) -> None:
        """memory_class is SQL-level only — the fallback must stand down."""
        store.save(key="k1", value="alpha content")

        def _knn_boom(*args: Any, **kwargs: Any) -> list[tuple[str, float]]:
            msg = "knn_search must not run for memory_class queries"
            raise AssertionError(msg)

        store._persistence.knn_search = _knn_boom  # type: ignore[attr-defined]
        assert store.search("zzzunknown qqqmissing", memory_class="decision") == []
