"""Unit tests for synchronous Context7 client helpers."""

from __future__ import annotations

from tapps_brain.context7_sync import extract_context7_content, snippet_parts


def test_snippet_parts_formats_code_blocks() -> None:
    parts = snippet_parts(
        {
            "title": "Demo",
            "content": "Explain",
            "codeList": ["x = 1", {"language": "py", "code": "y = 2"}],
        }
    )
    assert parts[0] == "### Demo"
    assert "Explain" in parts
    assert "x = 1" in parts[-2]
    assert "```py" in parts[-1]


def test_extract_context7_content_plain_string() -> None:
    assert extract_context7_content("  hello  ") == "hello"


def test_extract_context7_content_top_level_content_key() -> None:
    assert extract_context7_content({"content": " docs "}) == "docs"
