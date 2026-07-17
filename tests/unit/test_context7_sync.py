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


def test_resolve_library_handles_null_results() -> None:
    """API payloads with ``results: null`` must not raise TypeError."""
    from unittest.mock import MagicMock, patch

    from tapps_brain.context7_sync import SyncContext7Client

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"results": None}
    with patch("tapps_brain.context7_sync.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.get.return_value = mock_resp
        client = SyncContext7Client(api_key="k")
        assert client.resolve_library("fastapi") == []
