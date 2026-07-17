"""Synchronous Context7 HTTP client for brain-central doc lookup."""

from __future__ import annotations

from typing import Any

import httpx

CONTEXT7_BASE_URL = "https://context7.com"


class Context7Error(Exception):
    """Raised when the Context7 API returns an error."""


def snippet_parts(snippet: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    title = snippet.get("codeTitle") or snippet.get("title")
    if isinstance(title, str) and title.strip():
        parts.append(f"### {title.strip()}")
    desc = snippet.get("codeDescription") or snippet.get("content")
    if isinstance(desc, str) and desc.strip():
        parts.append(desc.strip())
    code_list = snippet.get("codeList") or []
    if isinstance(code_list, list):
        for code in code_list:
            if isinstance(code, str) and code.strip():
                parts.append(f"```\n{code.strip()}\n```")
            elif isinstance(code, dict):
                lang = code.get("language", "")
                code_text = code.get("code", "")
                if isinstance(code_text, str) and code_text.strip():
                    parts.append(f"```{lang}\n{code_text.strip()}\n```")
    return parts


def extract_context7_content(data: Any) -> str:
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("content"), str):
        content: str = data["content"]
        stripped = content.strip()
        if stripped:
            return stripped
    snippets = data.get("snippets") or data.get("codeSnippets") or []
    if not isinstance(snippets, list):
        return ""
    parts: list[str] = []
    for snippet in snippets:
        if isinstance(snippet, dict):
            parts.extend(snippet_parts(snippet))
    return "\n\n".join(parts).strip()


class SyncContext7Client:
    """Minimal synchronous Context7 client for the brain HTTP/MCP surface."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = CONTEXT7_BASE_URL,
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "tapps-brain/docs", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def resolve_library(self, query: str) -> list[dict[str, str]]:
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            try:
                resp = client.get("/api/v2/search", params={"query": query})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise Context7Error(f"Context7 resolve failed: {exc}") from exc
            data = resp.json()
        items = data if isinstance(data, list) else data.get("results", [])
        if not isinstance(items, list):
            items = []
        results: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            results.append(
                {
                    "id": str(item.get("id", "")),
                    "title": str(item.get("title", item.get("name", ""))),
                }
            )
        return results

    def fetch_docs(
        self,
        library_id: str,
        *,
        topic: str = "overview",
        mode: str = "code",
        max_tokens: int = 5000,
    ) -> str:
        lib_path = library_id.strip("/")
        params: dict[str, str | int] = {
            "type": "json",
            "topic": topic,
            "tokens": max_tokens,
        }
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
            follow_redirects=True,
        ) as client:
            try:
                resp = client.get(f"/api/v2/docs/{mode}/{lib_path}", params=params)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise Context7Error(f"Context7 fetch failed: {exc}") from exc
            data = resp.json()
        content = extract_context7_content(data)
        if not content:
            raise Context7Error(f"No documentation for {library_id} topic={topic}")
        return content
