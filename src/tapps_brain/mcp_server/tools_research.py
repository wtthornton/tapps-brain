"""Web research MCP tools (TAP-5364 / ADR-0030)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tapps_brain.web_research import research_fetch as run_research_fetch
from tapps_brain.web_research import web_research as run_web_research

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext


def register_research_tools(mcp: Any, _ctx: ToolContext) -> None:  # noqa: ANN401
    """Register ``web_research`` and ``research_fetch`` on *mcp*."""
    from tapps_brain.web_research import get_research_store

    @mcp.tool()  # type: ignore[untyped-decorator]
    def web_research(
        query: str,
        source: str = "auto",
        freshness: str = "volatile",
        max_results: int = 5,
    ) -> str:
        """Search the web via brain-held providers with write-through cache.

        Credentials (Exa / Tavily / Firecrawl) stay on the brain service.
        Results are cached under project ``web-research`` /
        ``memory_group=web-research`` keyed by normalized ``(query, source)``.
        """
        store = get_research_store()
        result = run_web_research(
            store,
            query=query,
            source=source,
            freshness=freshness,
            max_results=max_results,
        )
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def research_fetch(
        url: str,
        freshness: str = "evergreen",
    ) -> str:
        """Fetch a single URL via Firecrawl with write-through cache.

        Applies SSRF/url_guard and RAG safety before cache write.
        """
        store = get_research_store()
        result = run_research_fetch(store, url=url, freshness=freshness)
        return json.dumps(result, default=str)
