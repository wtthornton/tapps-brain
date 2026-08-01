"""Synchronous Firecrawl client for brain web research (TAP-5364)."""

from __future__ import annotations

from typing import Any

import httpx

FIRECRAWL_BASE_URL = "https://api.firecrawl.dev"
_SNIPPET_MAX = 400


class FirecrawlError(Exception):
    """Raised when the Firecrawl API returns an error."""


def _hit_from_search_item(item: dict[str, Any]) -> dict[str, str]:
    title = item.get("title") or ""
    url = item.get("url") or ""
    description = item.get("description") or item.get("markdown") or ""
    content = item.get("markdown") or description or ""
    snippet = description if isinstance(description, str) else ""
    if len(snippet) > _SNIPPET_MAX:
        snippet = snippet[:_SNIPPET_MAX]
    return {
        "title": str(title).strip(),
        "url": str(url).strip(),
        "snippet": str(snippet).strip(),
        "content": str(content).strip() if isinstance(content, str) else "",
    }


class SyncFirecrawlClient:
    """Minimal synchronous Firecrawl search + scrape client."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = FIRECRAWL_BASE_URL,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY not configured")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "tapps-brain/research",
            "Accept": "application/json",
        }

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
        payload = {
            "query": query,
            "limit": max(1, min(max_results, 20)),
        }
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:
            try:
                resp = client.post("/v1/search", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise FirecrawlError(f"Firecrawl search failed: {exc}") from exc
            data = resp.json()
        items = None
        if isinstance(data, dict):
            nested = data.get("data")
            if isinstance(nested, dict):
                items = nested.get("web") or nested.get("results")
            if items is None:
                items = data.get("data") if isinstance(data.get("data"), list) else None
            if items is None:
                items = data.get("results")
        if not isinstance(items, list):
            raise FirecrawlError("Firecrawl search returned no results list")
        hits: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, dict):
                hit = _hit_from_search_item(item)
                if hit["url"]:
                    hits.append(hit)
        return hits

    def scrape(self, url: str) -> dict[str, str]:
        payload = {"url": url, "formats": ["markdown"]}
        with httpx.Client(
            base_url=self._base_url,
            headers=self._headers(),
            timeout=self._timeout,
            follow_redirects=False,
        ) as client:
            try:
                resp = client.post("/v1/scrape", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise FirecrawlError(f"Firecrawl scrape failed: {exc}") from exc
            data = resp.json()
        body: dict[str, Any] = {}
        if isinstance(data, dict):
            nested = data.get("data")
            body = nested if isinstance(nested, dict) else data
        markdown = body.get("markdown") or body.get("content") or ""
        metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        title = metadata.get("title") if isinstance(metadata, dict) else ""
        content = str(markdown).strip() if isinstance(markdown, str) else ""
        if not content:
            raise FirecrawlError(f"Firecrawl scrape returned empty content for {url}")
        snippet = content[:_SNIPPET_MAX]
        return {
            "title": str(title or "").strip(),
            "url": url,
            "snippet": snippet,
            "content": content,
        }
