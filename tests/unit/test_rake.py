"""Tests for RAKE keyword extraction."""

from __future__ import annotations

from tapps_brain.rake import extract_keywords, generate_key


def test_extract_keywords_basic():
    text = (
        "We decided to use SQLite for the memory database. "
        "The architecture choice was driven by portability."
    )
    keywords = extract_keywords(text, top_n=3)
    assert len(keywords) > 0
    assert all(isinstance(k, tuple) and len(k) == 2 for k in keywords)


def test_extract_keywords_empty():
    assert extract_keywords("") == []
    assert extract_keywords("   ") == []


def test_generate_key():
    text = "Bill prefers dark mode for all applications"
    key = generate_key(text)
    assert len(key) > 0
    assert len(key) <= 64
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in key)


def test_generate_key_empty():
    assert generate_key("") == "memory"
