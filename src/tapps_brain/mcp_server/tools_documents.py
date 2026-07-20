"""Document plane MCP tool registrations (TAP-4998 / TAP-5003).

Thin wrappers around :mod:`tapps_brain.services.document_service` —
durable documents beside vector RAG: store original bytes, chunk +
embed deterministically, hybrid tsvector + pgvector search with RRF.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tapps_brain.services import document_service

if TYPE_CHECKING:
    from tapps_brain.mcp_server.context import ToolContext


def register_document_tools(mcp: Any, ctx: ToolContext) -> None:  # noqa: ANN401
    """Register ``document_*`` tools on *mcp*."""
    _server_aid = ctx.server_agent_id
    _resolve = ctx.resolve_store_for_call
    _pid = ctx.pid
    _rpc = ctx.resolve_per_call_agent_id

    @mcp.tool()  # type: ignore[untyped-decorator]
    def document_put(
        title: str,
        content: str = "",
        content_base64: str = "",
        content_type: str = "text/plain",
        tags: list[str] | None = None,
        index: bool = True,
        retention: str = "project",
        agent_id: str = "",
    ) -> str:
        """Store a document durably (text or base64 bytes) with optional hybrid indexing.

        ``index=True`` chunks + embeds the content deterministically so
        ``document_search`` can find it.  ``retention`` is ``"project"``
        (keep until deleted) or ``"days:<n>"``.
        """
        return json.dumps(
            document_service.document_put(
                _resolve(agent_id),
                _pid(),
                _rpc(agent_id, default=_server_aid),
                title=title,
                content=content,
                content_base64=content_base64,
                content_type=content_type,
                tags=tags,
                index=index,
                retention=retention,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def document_get(
        doc_id: str,
        meta_only: bool = False,
        agent_id: str = "",
    ) -> str:
        """Fetch a stored document's metadata and content by doc_id."""
        return json.dumps(
            document_service.document_get(
                _resolve(agent_id),
                _pid(),
                _rpc(agent_id, default=_server_aid),
                doc_id=doc_id,
                meta_only=meta_only,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def document_search(
        query: str,
        limit: int = 10,
        agent_id: str = "",
    ) -> str:
        """Hybrid search (tsvector + pgvector, RRF-fused) over indexed document chunks."""
        return json.dumps(
            document_service.document_search(
                _resolve(agent_id),
                _pid(),
                _rpc(agent_id, default=_server_aid),
                query=query,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def document_list(
        tag: str = "",
        limit: int = 100,
        agent_id: str = "",
    ) -> str:
        """List stored documents (metadata only), optionally filtered by tag."""
        return json.dumps(
            document_service.document_list(
                _resolve(agent_id),
                _pid(),
                _rpc(agent_id, default=_server_aid),
                tag=tag,
                limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def document_delete(
        doc_id: str,
        agent_id: str = "",
    ) -> str:
        """Delete a stored document and its chunks."""
        return json.dumps(
            document_service.document_delete(
                _resolve(agent_id),
                _pid(),
                _rpc(agent_id, default=_server_aid),
                doc_id=doc_id,
            )
        )
