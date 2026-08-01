"""Synchronous Exa search client for brain web research (TAP-5364)."""

from __future__ import annotations

from typing import Any

import httpx

EXA_BASE_URL = "https://api.exa.ai"
_SNIPPET_MAX = 400


class ExaError(Exception):
    """Raised when the Exa API returns an error."""


def _hit_from_item(item: dict[str, Any]) -> dict[str, str]:
    title = item.get("title") or item.get("name") or ""
    url = item.get("url") or item.get("id") or ""
    text = item.get("text") or ""
    snippet = item.get("snippet") or item.get("summary") or ""
    if not snippet and isinstance(text, str):
        snippet = text[:_SNIPPET_MAX]
    content = text if isinstance(text, str) else ""
    if not content and isinstance(snippet, str):
        content = snippet
    return {
        "title": str(title).strip(),
        "url": str(url).strip(),
        "snippet": str(snippet).strip(),
        "content": str(content).strip(),
    }


class SyncExaClient:
    """Minimal synchronous Exa search client."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = EXA_BASE_URL,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
        if not self._api_key:
            raise ExaError("EXA_API_KEY not configured")
        payload = {
            "query": query,
            "numResults": max(1, min(max_results, 20)),
            "contents": {"text": True},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
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
                raise ExaError(f"Exa search failed: {exc}") from exc
            data = resp.json()
        items = data.get("results") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ExaError("Exa search returned no results list")
        hits: list[dict[str, str]] = []
        for item in items:
            if isinstance(item, dict):
                hit = _hit_from_item(item)
                if hit["url"]:
                    hits.append(hit)
        return hits
