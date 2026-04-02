"""Unit tests for lexical tokenization and FTS query helpers (EPIC-042)."""

from __future__ import annotations

from tapps_brain.lexical import (
    LexicalRetrievalConfig,
    ascii_fold_text,
    build_fts_match_query,
    fts_query_terms,
    tokenize_lexical,
)


class TestTokenizeLexical:
    def test_camel_case_splits(self) -> None:
        assert tokenize_lexical("getUserId", camel_case_tokenization=True) == [
            "get",
            "user",
            "id",
        ]

    def test_camel_case_off_keeps_run(self) -> None:
        assert tokenize_lexical("getUserId", camel_case_tokenization=False) == ["getuserid"]

    def test_path_like_separators(self) -> None:
        assert tokenize_lexical("src/models/User.ts") == ["src", "models", "user", "ts"]

    def test_ascii_fold_optional(self) -> None:
        raw = "Café"
        assert tokenize_lexical(raw, ascii_fold=False, camel_case_tokenization=False) == ["caf"]
        folded = tokenize_lexical(raw, ascii_fold=True, camel_case_tokenization=False)
        assert folded == ["cafe"]


class TestAsciiFoldText:
    def test_strips_combining_marks(self) -> None:
        assert ascii_fold_text("café") == "cafe"


class TestFtsQueryTerms:
    def test_whitespace_and_slashes(self) -> None:
        assert fts_query_terms("a/b c", fts_path_splits=True) == ["a", "b", "c"]

    def test_empty_segments_from_repeated_slashes(self) -> None:
        assert fts_query_terms("a//b", fts_path_splits=True) == ["a", "b"]

    def test_leading_delimiter_drops_empty_chunk(self) -> None:
        assert fts_query_terms("/hello", fts_path_splits=True) == ["hello"]

    def test_dots_in_segment(self) -> None:
        assert fts_query_terms("foo.py", fts_path_splits=True) == ["foo", "py"]


class TestBuildFtsMatchQuery:
    def test_two_terms(self) -> None:
        assert build_fts_match_query("hello world", fts_path_splits=True) == '"hello" "world"'

    def test_empty(self) -> None:
        assert build_fts_match_query("", fts_path_splits=True) == ""
        assert build_fts_match_query("   ", fts_path_splits=True) == ""


class TestLexicalRetrievalConfig:
    def test_defaults(self) -> None:
        c = LexicalRetrievalConfig()
        assert c.camel_case_tokenization is True
        assert c.ascii_fold is False
        assert c.apply_stem is True
        assert c.fts_path_splits is True
