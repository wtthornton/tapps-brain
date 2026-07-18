"""Lexical tokenization for BM25 and FTS query building (EPIC-042 STORY-042.1).

Deterministic, locale-agnostic helpers: optional ASCII folding (NFKD), camelCase
boundaries for code-like text, path-style splitting for FTS queries, and basic
suffix stemming in :mod:`tapps_brain.bm25`.
"""

from __future__ import annotations

import re
import unicodedata

from pydantic import BaseModel, Field


class LexicalRetrievalConfig(BaseModel):
    """Profile-tunable lexical indexing and search (BM25 + FTS query terms)."""

    camel_case_tokenization: bool = Field(
        default=True,
        description=(
            "Split camelCase / PascalCase and similar boundaries before BM25 "
            "tokenization (e.g. getUser → get, user)."
        ),
    )
    ascii_fold: bool = Field(
        default=False,
        description=(
            "Apply NFKD normalization and strip combining characters before "
            "tokenization (deterministic Western-style folding)."
        ),
    )
    apply_stem: bool = Field(
        default=True,
        description="Apply basic suffix stripping in BM25 preprocessing (bm25.stem).",
    )
    fts_path_splits: bool = Field(
        default=True,
        description=(
            "When building FTS match strings, also split on / and \\\\ between "
            "whitespace-separated chunks (path-friendly literals)."
        ),
    )


_CAMEL_BOUNDARY_1 = re.compile(r"([a-z0-9])([A-Z])")
_CAMEL_BOUNDARY_2 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_TOKEN_RUN = re.compile(r"[a-z0-9]+")


def ascii_fold_text(text: str) -> str:
    """NFKD normalize and remove combining marks (deterministic)."""
    nk = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nk if not unicodedata.combining(c))


def insert_camel_boundaries(text: str) -> str:
    """Insert spaces at camelCase / acronym boundaries, then lowercase."""
    s = _CAMEL_BOUNDARY_1.sub(r"\1 \2", text)
    s = _CAMEL_BOUNDARY_2.sub(r"\1 \2", s)
    return s.lower()


def tokenize_lexical(
    text: str,
    *,
    ascii_fold: bool = False,
    camel_case_tokenization: bool = True,
) -> list[str]:
    """Extract lowercase alphanumeric tokens for BM25 / analysis.

    When *camel_case_tokenization* is True, boundaries are inserted so
    ``fooBar`` yields ``foo`` and ``bar``. Underscores and punctuation act as
    separators (``foo_bar`` → ``foo``, ``bar``).
    """
    if not text:
        return []
    raw = ascii_fold_text(text) if ascii_fold else text
    raw = insert_camel_boundaries(raw) if camel_case_tokenization else raw.lower()
    return _TOKEN_RUN.findall(raw)


def fts_query_terms(
    query: str,
    *,
    fts_path_splits: bool,
    ascii_fold: bool = False,
) -> list[str]:
    """Terms for FTS literal phrases (AND-combined in :func:`build_fts_match_query`).

    Splits on whitespace; when *fts_path_splits* is True, also splits segments
    on ``/`` and ``\\`` so ``src/models`` becomes ``src``, ``models``. Each
    segment is further split into alphanumeric runs (``foo.py`` → ``foo``, ``py``).
    Does **not** apply camelCase splitting (avoids over-constraining AND queries).
    When *ascii_fold* is True, applies the same NFKD folding as BM25 so accented
    queries (e.g. ``café`` → ``cafe``) match folded index terms.
    """
    s = query.strip()
    if not s:
        return []
    if ascii_fold:
        s = ascii_fold_text(s)
    chunks = re.split(r"[\s/\\]+", s) if fts_path_splits else s.split()
    terms: list[str] = []
    for ch in chunks:
        if not ch:
            continue
        terms.extend(_TOKEN_RUN.findall(ch.lower()))
    return terms


def build_fts_match_query(
    query: str,
    *,
    fts_path_splits: bool,
    ascii_fold: bool = False,
) -> str:
    """Build a safe FTS match string: AND of double-quoted literals.

    Legacy SQLite FTS5 helper kept for API compatibility — the Postgres
    backends (ADR-007) query via ``plainto_tsquery`` and do not use it.
    """
    tokens = fts_query_terms(query, fts_path_splits=fts_path_splits, ascii_fold=ascii_fold)
    if not tokens:
        return ""
    return " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens)
