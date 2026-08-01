"""Synchronous Tavily search client for brain web research (TAP-5364)."""

from __future__ import annotations

from typing import Any

import httpx

TAVILY_BASE_URL = "https://api.tavily.com"
_SNIPPET_MAX = 400


class TavilyError(Exception):
    """Raised when the Tavily API returns an error."""


def _hit_from_item(item: dict[str, Any]) -> dict[str, str]:
    title = item.get("title") or ""
    url = item.get("url") or ""
    content = item.get("content") or item.get("raw_content") or ""
    snippet = content if isinstance(content, str) else ""
    if len(snippet) > _SNIPPET_MAX:
        snippet = snippet[:_SNIPPET_MAX]
    return {
        "title": str(title).strip(),
        "url": str(url).strip(),
        "snippet": str(snippet).strip(),
        "content": str(content).strip() if isinstance(content, str) else "",
    }


class SyncTavilyClient:
    """Minimal synchronous Tavily search client."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = TAVILY_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
        if not self._api_key:
            raise TavilyError("TAVILY_API_KEY not configured")
        payload = {
            "api_key": self._api_key,
            "query": query,
            "max_results": max(1, min(max_results, 20)),
            "include_raw_content": False,
        }
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "tapps-brain/research",
            "Accept": "application/json",
        }
        with httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:
            try:
                resp = client.post("/search", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise TavilyError(f"Tavily search failed: {exc}") from exc
            data = resp.json()
        items = data.get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise TavilyError("Tavily search returned no results list")
        hits: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, dict):
                hit = _hit_from_item(item)
                if hit["url"]:
                    hits.append(hit)
        return hits
