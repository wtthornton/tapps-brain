"""TextRank — extractive summarization without LLM (Mihalcea & Tarau 2004).

Pure Python, no external dependencies. Uses PageRank on a sentence
similarity graph to extract the most important sentences.
"""

from __future__ import annotations

import math
import re

_MIN_SENTENCE_LEN = 10


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text.strip())
    return [s.strip() for s in sentences if s.strip() and len(s.strip()) > _MIN_SENTENCE_LEN]


def _tokenize(sentence: str) -> set[str]:
    """Tokenize and lowercase a sentence, removing stop words."""
    words = re.findall(r"[a-z0-9]+", sentence.lower())
    # Minimal stop words for similarity computation
    stops = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        "again",
        "further",
        "then",
        "once",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "if",
        "than",
        "that",
        "this",
        "it",
        "its",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "my",
        "your",
        "his",
        "her",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
    }
    return {w for w in words if w not in stops and len(w) > 1}


def _sentence_similarity(s1: str, s2: str) -> float:
    """Word overlap similarity: |intersection| / (log|s1| + log|s2|)."""
    words1 = _tokenize(s1)
    words2 = _tokenize(s2)
    if not words1 or not words2:
        return 0.0
    overlap = len(words1 & words2)
    if overlap == 0:
        return 0.0
    denom = math.log(max(len(words1), 1) + 1) + math.log(max(len(words2), 1) + 1)
    return overlap / denom if denom > 0 else 0.0


def _pagerank(
    matrix: list[list[float]],
    d: float = 0.85,
    iterations: int = 30,
    tol: float = 1e-6,
) -> list[float]:
    """Run PageRank on a weighted adjacency matrix."""
    n = len(matrix)
    if n == 0:
        return []
    scores = [1.0 / n] * n
    for _ in range(iterations):
        new_scores = [0.0] * n
        for i in range(n):
            rank_sum = 0.0
            for j in range(n):
                if i == j:
                    continue
                out_sum = sum(matrix[j][k] for k in range(n) if k != j)
                if out_sum > 0:
                    rank_sum += matrix[j][i] * scores[j] / out_sum
            new_scores[i] = (1 - d) / n + d * rank_sum
        # Check convergence
        diff = sum(abs(new_scores[i] - scores[i]) for i in range(n))
        scores = new_scores
        if diff < tol:
            break
    return scores


def summarize(text: str, top_n: int = 5, min_sentences: int = 2) -> str:
    """Extract top-N most important sentences from text using TextRank.

    Returns sentences in their original order.
    """
    if not text or not text.strip():
        return ""
    sentences = _split_sentences(text)
    if len(sentences) <= min_sentences:
        return text.strip()

    # Build similarity matrix
    n = len(sentences)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            sim = _sentence_similarity(sentences[i], sentences[j])
            matrix[i][j] = sim
            matrix[j][i] = sim

    # Run PageRank
    scores = _pagerank(matrix)

    # Get top-N indices, return in original order
    top_n = min(top_n, len(sentences))
    ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)[:top_n]
    ranked_in_order = sorted(ranked)

    return " ".join(sentences[i] for i in ranked_in_order)


def summarize_messages(messages: list[str], top_n: int = 5) -> str:
    """Summarize a list of conversation messages using TextRank."""
    combined = "\n".join(messages)
    return summarize(combined, top_n=top_n)
