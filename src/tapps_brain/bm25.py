"""BM25 scoring engine for memory retrieval.

Implements Okapi BM25 with basic text preprocessing (lowercasing,
stop-word removal, suffix stripping).  Pure Python, no external deps.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

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

_WORD_RE = re.compile(r"[a-z0-9]+")


def preprocess(text: str) -> list[str]:
    """Lowercase, tokenise, remove stop words, and stem."""
    tokens = _WORD_RE.findall(text.lower())
    return [stem(t) for t in tokens if t not in _STOP_WORDS]


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

    # Internal state (populated by build_index)
    _doc_count: int = field(default=0, init=False, repr=False)
    _avgdl: float = field(default=0.0, init=False, repr=False)
    _doc_lens: list[int] = field(default_factory=list, init=False, repr=False)
    _doc_terms: list[list[str]] = field(default_factory=list, init=False, repr=False)
    _df: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _idf: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    # ------------------------------------------------------------------
    # Index building
    # ------------------------------------------------------------------

    def build_index(self, documents: list[str]) -> None:
        """Preprocess *documents* and compute IDF values."""
        self._doc_terms = [preprocess(doc) for doc in documents]
        self._doc_count = len(self._doc_terms)
        self._doc_lens = [len(terms) for terms in self._doc_terms]
        total_len = sum(self._doc_lens)
        self._avgdl = total_len / self._doc_count if self._doc_count else 0.0

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
        """
        query_terms = preprocess(query)
        if not self._doc_count or not query_terms:
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
        """BM25 score for a single document."""
        doc_terms = self._doc_terms[doc_idx]
        dl = self._doc_lens[doc_idx]

        # Term frequencies in this document
        tf_map: dict[str, int] = {}
        for t in doc_terms:
            tf_map[t] = tf_map.get(t, 0) + 1

        total = 0.0
        for qt in query_terms:
            idf = self._idf.get(qt, 0.0)
            tf = tf_map.get(qt, 0)
            if tf == 0:
                continue
            # TF saturation
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * dl / self._avgdl)
            total += idf * numerator / denominator
        return total
