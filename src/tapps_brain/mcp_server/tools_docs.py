"""Library documentation MCP tools (ADR-0014)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tapps_brain.docs_lookup import docs_lookup as run_docs_lookup
from tapps_brain.docs_lookup import docs_warm as run_docs_warm

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext


def register_docs_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401
    """Register ``docs_lookup`` and ``docs_warm`` on *mcp*."""
    _resolve = ctx.resolve_store_for_call

    @mcp.tool()  # type: ignore[untyped-decorator]
    def docs_lookup(
        library: str,
        topic: str = "overview",
        mode: str = "code",
    ) -> str:
        """Fetch library documentation from the brain-central doc cache.

        On cache miss calls Context7 when ``CONTEXT7_API_KEY`` is configured on
        the brain service.  Storage uses project ``library-docs`` /
        ``memory_group=library-docs`` (company-wide index).
        """
        if mode not in {"code", "info"}:
            return json.dumps(
                {"error": "invalid_mode", "detail": "mode must be 'code' or 'info'"},
            )
        store = _resolve("")
        result = run_docs_lookup(store, library=library, topic=topic, mode=mode)
        return json.dumps(result, default=str)

    @mcp.tool()  # type: ignore[untyped-decorator]
    def docs_warm(libraries: list[str], topic: str = "overview", mode: str = "code") -> str:
        """Batch pre-fetch library docs into the brain-central cache."""
        if mode not in {"code", "info"}:
            return json.dumps(
                {"error": "invalid_mode", "detail": "mode must be 'code' or 'info'"},
            )
        store = _resolve("")
        result = run_docs_warm(store, libraries, topic=topic, mode=mode)
        return json.dumps(result, default=str)
