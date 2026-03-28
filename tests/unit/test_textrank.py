"""Tests for TextRank summarization."""

from tapps_brain.textrank import summarize, summarize_messages


def test_summarize_basic():
    text = (
        "We decided to use SQLite for the database. "
        "The team discussed PostgreSQL but rejected it. "
        "SQLite runs everywhere including Raspberry Pi. "
        "Performance benchmarks showed sub-5ms queries. "
        "The architecture decision was unanimous. "
        "Bill approved the final design. "
        "Implementation will start next week."
    )
    result = summarize(text, top_n=3)
    assert len(result) > 0
    assert len(result) < len(text)


def test_summarize_short_text():
    text = "Just one sentence."
    assert summarize(text) == text.strip()


def test_summarize_empty():
    assert summarize("") == ""
    assert summarize("   ") == ""


def test_summarize_messages():
    msgs = [
        "We need to pick a database for the memory system.",
        "SQLite is portable and runs on Pi.",
        "PostgreSQL needs a server process.",
        "Let's go with SQLite for now.",
    ]
    result = summarize_messages(msgs, top_n=2)
    assert len(result) > 0
