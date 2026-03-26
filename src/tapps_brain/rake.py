"""RAKE — Rapid Automatic Keyword Extraction (Rose et al. 2010).

Pure Python, no external dependencies. Extracts key phrases from text
by splitting at stop words and scoring by word co-occurrence.
"""
from __future__ import annotations

import re

from tapps_brain.bm25 import _STOP_WORDS

_MIN_PHRASE_LEN = 2


def _tokenize_sentences(text: str) -> list[str]:
    """Split text into sentences."""
    return re.split(r'[.!?\n]+', text)


def _extract_candidate_phrases(sentence: str, stop_words: frozenset[str]) -> list[str]:
    """Split sentence at stop words and punctuation to get candidate phrases."""
    # Split on stop words and punctuation
    pattern = r'[\s,;:()\[\]{}"\'"]+'
    words = re.split(pattern, sentence.lower().strip())
    phrases = []
    current = []
    for w in words:
        w = w.strip()
        if not w or w in stop_words:
            if current:
                phrases.append(' '.join(current))
                current = []
        else:
            current.append(w)
    if current:
        phrases.append(' '.join(current))
    return [p for p in phrases if len(p) > _MIN_PHRASE_LEN]


def _score_phrases(phrases: list[str]) -> dict[str, float]:
    """Score phrases using RAKE: word_score = degree(word) / frequency(word)."""
    word_freq: dict[str, int] = {}
    word_degree: dict[str, int] = {}
    for phrase in phrases:
        words = phrase.split()
        degree = len(words) - 1
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
            word_degree[w] = word_degree.get(w, 0) + degree
    # word score = (degree + freq) / freq
    word_score = {w: (word_degree[w] + word_freq[w]) / word_freq[w] for w in word_freq}
    # phrase score = sum of word scores
    phrase_scores: dict[str, float] = {}
    for phrase in set(phrases):
        score = sum(word_score.get(w, 0) for w in phrase.split())
        phrase_scores[phrase] = score
    return phrase_scores


def extract_keywords(text: str, top_n: int = 5) -> list[tuple[str, float]]:
    """Extract top-N keywords/phrases from text using RAKE.

    Returns list of (phrase, score) sorted by score descending.
    """
    if not text or not text.strip():
        return []
    sentences = _tokenize_sentences(text)
    all_phrases: list[str] = []
    for sentence in sentences:
        all_phrases.extend(_extract_candidate_phrases(sentence, _STOP_WORDS))
    if not all_phrases:
        return []
    scored = _score_phrases(all_phrases)
    sorted_phrases = sorted(scored.items(), key=lambda x: -x[1])
    return sorted_phrases[:top_n]


def generate_key(text: str, max_length: int = 64) -> str:
    """Generate a memory key from text using RAKE keyword extraction."""
    keywords = extract_keywords(text, top_n=1)
    if not keywords:
        return "memory"
    phrase = keywords[0][0]
    # Slugify
    slug = re.sub(r'[^a-z0-9]+', '-', phrase.lower()).strip('-')
    return slug[:max_length] if slug else "memory"
