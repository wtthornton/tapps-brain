"""BM25 scoring engine for memory retrieval.

Implements Okapi BM25 with basic text preprocessing (lowercasing,
stop-word removal, suffix stripping).  Pure Python, no external deps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tapps_brain.lexical import tokenize_lexical

# ---------------------------------------------------------------------------
# Stop words (~50 common English words)
# ---------------------------------------------------------------------------

_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "do",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "they",
        "this",
        "to",
        "up",
        "us",
        "was",
        "we",
        "what",
        "when",
        "which",
        "who",
        "will",
        "with",
        "you",
        "your",
    }
)

# ---------------------------------------------------------------------------
# Stemming (basic suffix stripping)
# ---------------------------------------------------------------------------

_SUFFIX_ORDER: tuple[str, ...] = (
    "tion",
    "ment",
    "ness",
    "ing",
    "est",
    "er",
    "ed",
    "ly",
    "s",
)

_MIN_STEM_LENGTH = 3


def stem(word: str) -> str:
    """Apply basic suffix stripping to *word*.

    Strips the first matching suffix from ``_SUFFIX_ORDER`` provided
    the remaining stem has at least ``_MIN_STEM_LENGTH`` characters.
    """
    for suffix in _SUFFIX_ORDER:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM_LENGTH:
            return word[: -len(suffix)]
    return word


# ---------------------------------------------------------------------------
# Preprocessing
# ---------------------------------------------------------------------------


def preprocess(
    text: str,
    *,
    apply_stem: bool = True,
    ascii_fold: bool = False,
    camel_case_tokenization: bool = True,
) -> list[str]:
    """Lowercase, tokenise, remove stop words, and optionally stem.

    Tokenization follows :func:`tapps_brain.lexical.tokenize_lexical` (camelCase
    boundaries, punctuation separators). Use ``camel_case_tokenization=False``
    for legacy whitespace-style runs only.
    """
    tokens = tokenize_lexical(
        text,
        ascii_fold=ascii_fold,
        camel_case_tokenization=camel_case_tokenization,
    )
    out: list[str] = []
    for t in tokens:
        if t in _STOP_WORDS:
            continue
        out.append(stem(t) if apply_stem else t)
    return out


def preprocess_similarity(text: str) -> list[str]:
    """Tokenize for TF-IDF / overlap heuristics (stable product names like ``FastAPI``).

    Retrieval uses :func:`preprocess` with camelCase boundaries; similarity-style
    callers keep ``camel_case_tokenization=False`` so acronyms stay single tokens.
    """
    return preprocess(text, camel_case_tokenization=False)


# ---------------------------------------------------------------------------
# BM25 scorer
# ---------------------------------------------------------------------------

_DEFAULT_K1 = 1.2
_DEFAULT_B = 0.75


@dataclass
class BM25Scorer:
    """Okapi BM25 ranking function over a pre-built document index.

    Usage::

        scorer = BM25Scorer()
        scorer.build_index(["doc one text", "doc two text"])
        scores = scorer.score("query text")
    """

    k1: float = _DEFAULT_K1
    b: float = _DEFAULT_B
    delta: float = 1.0
    apply_stem: bool = True
    ascii_fold: bool = False
    camel_case_tokenization: bool = True

    # Internal state (populated by build_index)
    _doc_count: int = field(default=0, init=False, repr=False)
    _avgdl: float = field(default=0.0, init=False, repr=False)
    _doc_lens: list[int] = field(default_factory=list, init=False, repr=False)
    _doc_terms: list[list[str]] = field(default_factory=list, init=False, repr=False)
    _tf_maps: list[dict[str, int]] = field(default_factory=list, init=False, repr=False)
    _df: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _idf: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def _preprocess_doc(self, text: str) -> list[str]:
        return preprocess(
            text,
            apply_stem=self.apply_stem,
            ascii_fold=self.ascii_fold,
            camel_case_tokenization=self.camel_case_tokenization,
        )

    def build_index(self, documents: list[str]) -> None:
        """Preprocess *documents* and compute IDF values."""
        self._doc_terms = [self._preprocess_doc(doc) for doc in documents]
        self._doc_count = len(self._doc_terms)
        self._doc_lens = [len(terms) for terms in self._doc_terms]
        total_len = sum(self._doc_lens)
        self._avgdl = total_len / self._doc_count if self._doc_count else 0.0

        # Pre-compute per-document term-frequency maps (avoids recomputation per query)
        self._tf_maps = []
        for terms in self._doc_terms:
            tf_map: dict[str, int] = {}
            for t in terms:
                tf_map[t] = tf_map.get(t, 0) + 1
            self._tf_maps.append(tf_map)

        # Document frequency
        self._df = {}
        for terms in self._doc_terms:
            for term in set(terms):
                self._df[term] = self._df.get(term, 0) + 1

        # IDF: log((N - df + 0.5) / (df + 0.5) + 1)
        self._idf = {}
        n = self._doc_count
        for term, df in self._df.items():
            self._idf[term] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, query: str) -> list[float]:
        """Score every indexed document against *query*.

        Returns a list of BM25 scores (one per document) in index order.
        Returns an empty list for an empty corpus and a zero-score list
        when the corpus has only empty/whitespace-only documents.
        """
        query_terms = self._preprocess_doc(query)
        if not self._doc_count or self._avgdl == 0.0 or not query_terms:
            return [0.0] * self._doc_count

        scores: list[float] = []
        for idx in range(self._doc_count):
            scores.append(self._score_doc(query_terms, idx))
        return scores

    def score_batch(self, queries: list[str]) -> list[list[float]]:
        """Score every indexed document against each query in *queries*.

        Returns a list of score-lists, one per query.
        """
        return [self.score(q) for q in queries]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _score_doc(self, query_terms: list[str], doc_idx: int) -> float:
        """BM25 score for a single document.

        Returns 0.0 immediately when the average document length is zero
        (empty corpus or all-empty documents) to avoid ZeroDivisionError
        in the length-normalisation term ``b * dl / _avgdl``.
        """
        if self._avgdl == 0.0:
            return 0.0
        dl = self._doc_lens[doc_idx]
        tf_map = self._tf_maps[doc_idx]

        total = 0.0
        for qt in query_terms:
            idf = self._idf.get(qt, 0.0)
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            # TF saturation
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * dl / self._avgdl)
            total += idf * (numerator / denominator + self.delta)
        return total
