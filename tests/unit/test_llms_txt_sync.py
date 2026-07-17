"""Unit tests for synchronous llms.txt provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tapps_brain.llms_txt_sync import (
    LlmsTxtError,
    SyncLlmsTxtClient,
    extract_topic_section,
)


def test_extract_topic_section() -> None:
    content = "# Overview\nintro\n\n# Fixtures\nfixture docs\n\n# Other\nx"
    section = extract_topic_section(content, "fixtures")
    assert "fixture docs" in section
    assert "Overview" not in section.split("\n")[0] or "# Fixtures" in section


def test_resolve_url_known_library() -> None:
    client = SyncLlmsTxtClient()
    assert client.resolve_url("pytest") == "https://docs.pytest.org/llms.txt"


@patch("tapps_brain.llms_txt_sync.httpx.Client")
def test_resolve_url_falls_back_to_get_when_head_blocked(mock_client_cls: MagicMock) -> None:
    head_resp = MagicMock()
    head_resp.status_code = 405
    get_resp = MagicMock()
    get_resp.status_code = 200
    client_ctx = mock_client_cls.return_value.__enter__.return_value
    client_ctx.head.return_value = head_resp
    client_ctx.get.return_value = get_resp

    client = SyncLlmsTxtClient()
    assert client.resolve_url("custom-lib") == "https://docs.custom-lib.dev/llms.txt"


@patch("tapps_brain.llms_txt_sync.httpx.Client")
def test_fetch_returns_content(mock_client_cls: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "# Pytest\nDocs here"
    client_ctx = mock_client_cls.return_value.__enter__.return_value
    client_ctx.get.return_value = mock_resp

    client = SyncLlmsTxtClient()
    url, content = client.fetch("pytest", topic="overview")
    assert "pytest" in url
    assert "Docs here" in content


@patch("tapps_brain.llms_txt_sync.httpx.Client")
def test_fetch_raises_when_empty(mock_client_cls: MagicMock) -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "   "
    client_ctx = mock_client_cls.return_value.__enter__.return_value
    client_ctx.get.return_value = mock_resp

    client = SyncLlmsTxtClient()
    try:
        client.fetch("unknown-lib-xyz")
    except LlmsTxtError:
        return
    raise AssertionError("expected LlmsTxtError")
