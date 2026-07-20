"""Unit tests for the document plane core module (TAP-4998 / TAP-5002/5004).

Covers deterministic chunking, retention parsing, RRF key helpers, and the
DB-free validation paths of :class:`PostgresDocumentStore`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.documents import (
    DocumentMeta,
    PostgresDocumentStore,
    chunk_text,
    make_chunk_key,
    parse_retention,
    split_chunk_key,
)


class TestChunkText:
    def test_is_deterministic(self) -> None:
        text = "\n\n".join(f"Paragraph {i} with some words about topic {i}." for i in range(30))
        assert chunk_text(text) == chunk_text(text)

    def test_short_text_is_single_chunk(self) -> None:
        chunks = chunk_text("just one short paragraph")
        assert chunks == ["just one short paragraph"]

    def test_packs_paragraphs_up_to_chunk_chars(self) -> None:
        paras = [f"para {i} " + "x" * 40 for i in range(10)]
        chunks = chunk_text("\n\n".join(paras), chunk_chars=120)
        assert len(chunks) > 1
        assert all(len(c) <= 120 for c in chunks)
        # No content lost: every paragraph appears in exactly one chunk.
        joined = "\n\n".join(chunks)
        for p in paras:
            assert p in joined

    def test_oversize_paragraph_splits_on_whitespace(self) -> None:
        text = "word " * 500  # single 2500-char paragraph
        chunks = chunk_text(text, chunk_chars=100)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 100
            assert not c.startswith(" ")
            # Splitting prefers word boundaries — no mid-word cuts of "word".
            assert all(tok == "word" for tok in c.split())

    def test_oversize_paragraph_without_whitespace_hard_splits(self) -> None:
        text = "x" * 950
        chunks = chunk_text(text, chunk_chars=300)
        assert "".join(chunks) == text
        assert all(len(c) <= 300 for c in chunks)

    def test_max_chunks_caps_output(self) -> None:
        text = "\n\n".join(f"unique paragraph {i} " + "y" * 90 for i in range(50))
        chunks = chunk_text(text, chunk_chars=100, max_chunks=5)
        assert len(chunks) == 5

    def test_blank_input_returns_empty(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\n  \n  ") == []

    def test_invalid_limits_return_empty(self) -> None:
        assert chunk_text("content", chunk_chars=0) == []
        assert chunk_text("content", max_chunks=0) == []

    def test_normalizes_crlf(self) -> None:
        assert chunk_text("a\r\n\r\nb") == chunk_text("a\n\nb")


class TestParseRetention:
    def test_project_means_no_expiry(self) -> None:
        assert parse_retention("project") is None

    def test_days_spec_sets_future_expiry(self) -> None:
        before = datetime.now(tz=UTC)
        expires = parse_retention("days:30")
        assert expires is not None
        assert before + timedelta(days=29) < expires < before + timedelta(days=31)

    @pytest.mark.parametrize(
        "spec",
        ["", "forever", "days:0", "days:-1", "days:99999", "days:", "30", "ttl:30"],
    )
    def test_invalid_specs_raise(self, spec: str) -> None:
        with pytest.raises(ValueError, match="invalid retention|expected"):
            parse_retention(spec)


class TestChunkKey:
    def test_roundtrip(self) -> None:
        key = make_chunk_key("abc123", 42)
        assert split_chunk_key(key) == ("abc123", 42)


class TestDocumentMeta:
    def test_defaults(self) -> None:
        meta = DocumentMeta(doc_id="d1", title="t", size_bytes=10, sha256="s")
        assert meta.content_type == "text/plain"
        assert meta.index_status == "none"
        assert meta.retention == "project"
        assert meta.chunk_count == 0


class TestPostgresDocumentStoreValidation:
    """DB-free guard paths — no connection is opened before these raise."""

    @pytest.fixture
    def store(self) -> PostgresDocumentStore:
        class _ExplodingCM:
            def get_connection(self) -> None:
                raise AssertionError("must not connect")

        return PostgresDocumentStore(_ExplodingCM(), project_id="p", agent_id="a")  # type: ignore[arg-type]

    def test_put_rejects_invalid_index_status(self, store: PostgresDocumentStore) -> None:
        with pytest.raises(ValueError, match="invalid index_status"):
            store.put(
                doc_id="d",
                title="t",
                content=b"x",
                content_type="text/plain",
                sha256="s",
                tags=[],
                index_status="bogus",
                index_error=None,
                retention="project",
                expires_at=None,
            )

    def test_set_index_status_rejects_invalid_status(self, store: PostgresDocumentStore) -> None:
        with pytest.raises(ValueError, match="invalid index_status"):
            store.set_index_status("d", "bogus")

    def test_save_chunks_rejects_length_mismatch(self, store: PostgresDocumentStore) -> None:
        with pytest.raises(ValueError, match="equal length"):
            store.save_chunks("d", ["one"], [])

    def test_save_chunks_empty_returns_zero(self, store: PostgresDocumentStore) -> None:
        assert store.save_chunks("d", [], []) == 0

    def test_list_documents_nonpositive_limit_returns_empty(
        self, store: PostgresDocumentStore
    ) -> None:
        assert store.list_documents(limit=0) == []

    def test_lexical_search_blank_query_returns_empty(
        self, store: PostgresDocumentStore
    ) -> None:
        assert store.lexical_search("") == []
        assert store.lexical_search("   ") == []
        assert store.lexical_search("q", limit=0) == []

    def test_semantic_search_empty_vector_returns_empty(
        self, store: PostgresDocumentStore
    ) -> None:
        assert store.semantic_search([]) == []
        assert store.semantic_search([0.1], limit=0) == []
