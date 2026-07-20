"""Document plane service functions (TAP-4998, stories TAP-5003/5004/5005).

Shared by the MCP ``document_*`` tools and the ``/v1/documents`` HTTP
routes.  Functions take ``(store, project_id, agent_id, **typed_args)``
and return JSON-serialisable dicts; error envelopes use stable taxonomy
codes (``document_too_large``, ``document_limit_exceeded``, ``not_found``,
``bad_request``, ``documents_unavailable``).

Pipeline for ``document_put(index=True)``: safety scan → deterministic
chunking (:func:`tapps_brain.documents.chunk_text`) → embedding
(``embeddings.get_embedding_provider``) → hybrid-searchable chunks.
``index_status`` transitions ``pending → indexed`` (or ``error`` with a
diagnostic).  No LLM anywhere in the pipeline.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — runtime use in dataclass annotations
from typing import Any

import structlog

from tapps_brain.documents import (
    DocumentMeta,
    DocumentSearchHit,
    chunk_text,
    make_chunk_key,
    parse_retention,
    split_chunk_key,
)
from tapps_brain.fusion import (
    hybrid_rrf_weights_for_query,
    reciprocal_rank_fusion_weighted,
)

logger = structlog.get_logger(__name__)

_SNIPPET_CHARS = 240
_MAX_TITLE_CHARS = 512
_MAX_TAGS = 32
_MAX_SEARCH_LIMIT = 50
# Per-channel candidate pool multiplier for RRF fusion.
_POOL_MULTIPLIER = 5
_MIN_POOL = 20


def _bad_request(detail: str) -> dict[str, Any]:
    return {"error": "bad_request", "detail": detail, "message": detail}


def _document_store(store: Any) -> Any:
    """Resolve the tenant's :class:`PostgresDocumentStore`, or ``None``."""
    getter = getattr(store, "document_store", None)
    if getter is None:
        return None
    return getter()


def _documents_config(store: Any) -> Any:
    """Return the effective :class:`DocumentsConfig` (profile or defaults)."""
    from tapps_brain.profile import DocumentsConfig

    profile = getattr(store, "_profile", None)
    cfg = getattr(profile, "documents", None) if profile is not None else None
    return cfg if cfg is not None else DocumentsConfig()


def _resolve_embedder(store: Any) -> Any:
    """Prefer the store's provider; fall back to the process singleton."""
    provider = getattr(store, "_embedding_provider", None)
    if provider is not None:
        return provider
    from tapps_brain.embeddings import get_embedding_provider

    return get_embedding_provider()


def _unavailable() -> dict[str, Any]:
    detail = "Document plane requires a Postgres-backed store."
    return {"error": "documents_unavailable", "detail": detail, "message": detail}


def _meta_payload(meta: DocumentMeta) -> dict[str, Any]:
    return meta.model_dump(mode="json")


def _decode_content(
    content: str, content_base64: str
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Resolve the raw document bytes from exactly one content field."""
    has_text = bool(content)
    has_b64 = bool(content_base64.strip())
    if has_text == has_b64:
        return None, _bad_request("Provide exactly one of content or content_base64.")
    if has_text:
        return content.encode("utf-8"), None
    try:
        return base64.b64decode(content_base64.strip(), validate=True), None
    except (binascii.Error, ValueError):
        return None, _bad_request("content_base64 is not valid base64.")


def _index_document(
    doc_store: Any,
    store: Any,
    *,
    doc_id: str,
    text: str,
    max_chunks: int,
) -> tuple[str, str | None, int]:
    """Chunk + embed + persist; returns ``(index_status, diagnostic, chunk_count)``."""
    chunks = chunk_text(text, max_chunks=max_chunks)
    if not chunks:
        return "error", "no indexable text after chunking", 0

    embedder = _resolve_embedder(store)
    if embedder is None:
        return "error", "embedding provider unavailable", 0
    try:
        embeddings = embedder.embed_batch(chunks)
    except Exception as exc:
        logger.warning("documents.embed_failed", doc_id=doc_id, exc_info=True)
        return "error", f"embedding failed: {exc}", 0

    doc_store.save_chunks(doc_id, chunks, list(embeddings))
    return "indexed", None, len(chunks)


@dataclass
class _PutRequest:
    """Validated ``document_put`` inputs (see :func:`_validate_put`)."""

    title: str
    tags: list[str]
    raw: bytes
    text: str | None
    expires_at: datetime | None
    cfg: Any


def _validate_put(  # noqa: PLR0911 — one early return per validation rule
    store: Any,
    doc_store: Any,
    *,
    title: str,
    content: str,
    content_base64: str,
    tags: list[str] | None,
    retention: str,
) -> _PutRequest | dict[str, Any]:
    """Validate put inputs against limits; returns an error envelope on failure."""
    clean_title = (title or "").strip()
    if not clean_title:
        return _bad_request("title is required.")
    if len(clean_title) > _MAX_TITLE_CHARS:
        return _bad_request(f"title exceeds {_MAX_TITLE_CHARS} characters.")
    tag_list = [t.strip() for t in (tags or []) if t and t.strip()]
    if len(tag_list) > _MAX_TAGS:
        return _bad_request(f"at most {_MAX_TAGS} tags allowed.")

    raw, err = _decode_content(content, content_base64)
    if err is not None or raw is None:
        return err if err is not None else _bad_request("content is empty.")
    if not raw.strip():
        return _bad_request("content is empty.")

    cfg = _documents_config(store)
    if len(raw) > cfg.max_doc_bytes:
        detail = f"Document is {len(raw)} bytes; max is {cfg.max_doc_bytes}."
        return {
            "error": "document_too_large",
            "detail": detail,
            "message": detail,
            "max_doc_bytes": cfg.max_doc_bytes,
        }
    if doc_store.count() >= cfg.max_docs_per_project:
        detail = f"Project already holds {cfg.max_docs_per_project} documents."
        return {
            "error": "document_limit_exceeded",
            "detail": detail,
            "message": detail,
            "max_docs_per_project": cfg.max_docs_per_project,
        }

    try:
        expires_at = parse_retention(retention)
    except ValueError as exc:
        return _bad_request(str(exc))

    try:
        text: str | None = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = None

    return _PutRequest(
        title=clean_title,
        tags=tag_list,
        raw=raw,
        text=text,
        expires_at=expires_at,
        cfg=cfg,
    )


def _put_result(
    doc_id: str,
    req: _PutRequest,
    sha256: str,
    *,
    index_status: str,
    index_error: str | None = None,
    chunk_count: int = 0,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "stored",
        "doc_id": doc_id,
        "size_bytes": len(req.raw),
        "sha256": sha256,
        "index_status": index_status,
        "chunk_count": chunk_count,
        "expires_at": req.expires_at.isoformat() if req.expires_at else None,
    }
    if index_error:
        result["index_error"] = index_error
    return result


def document_put(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    title: str,
    content: str = "",
    content_base64: str = "",
    content_type: str = "text/plain",
    tags: list[str] | None = None,
    index: bool = True,
    retention: str = "project",
) -> dict[str, Any]:
    """Store a document durably; optionally chunk + embed it for hybrid search.

    Content above ``documents.max_doc_bytes`` is rejected with
    ``document_too_large``; a project at ``documents.max_docs_per_project``
    rejects with ``document_limit_exceeded``.  Content failing the safety
    scan stores with ``index_status='error'`` (retrievable by ID, never
    injected into RAG context).
    """
    doc_store = _document_store(store)
    if doc_store is None:
        return _unavailable()

    validated = _validate_put(
        store,
        doc_store,
        title=title,
        content=content,
        content_base64=content_base64,
        tags=tags,
        retention=retention,
    )
    if isinstance(validated, dict):
        return validated
    req = validated

    doc_id = uuid.uuid4().hex
    sha256 = hashlib.sha256(req.raw).hexdigest()
    metrics = getattr(store, "_metrics", None)

    def _store_doc(index_status: str, index_error: str | None) -> None:
        doc_store.put(
            doc_id=doc_id,
            title=req.title,
            content=req.raw,
            content_type=content_type or "text/plain",
            sha256=sha256,
            tags=req.tags,
            index_status=index_status,
            index_error=index_error,
            retention=retention,
            expires_at=req.expires_at,
        )
        if metrics is not None:
            metrics.increment("documents.put")

    warning: str | None = None
    if req.text is not None:
        from tapps_brain.safety import check_content_safety

        safety = check_content_safety(req.text, metrics=metrics)
        if not safety.safe:
            # Safety-flagged content stores with index_status='error' —
            # retrievable by ID, never chunked into RAG context.
            diagnostic = safety.warning or "content failed safety scan"
            _store_doc("error", diagnostic)
            if metrics is not None:
                metrics.increment("documents.safety_blocked")
            return _put_result(doc_id, req, sha256, index_status="error", index_error=diagnostic)
        warning = safety.warning

    do_index = index and req.text is not None
    _store_doc("pending" if do_index else "none", None)

    index_status = "pending" if do_index else "none"
    index_error: str | None = None
    chunk_count = 0
    if do_index and req.text is not None:
        index_status, index_error, chunk_count = _index_document(
            doc_store,
            store,
            doc_id=doc_id,
            text=req.text,
            max_chunks=req.cfg.max_chunks_per_doc,
        )
        doc_store.set_index_status(doc_id, index_status, error=index_error)
        if metrics is not None and index_status == "indexed":
            metrics.increment("documents.indexed")
            metrics.increment("documents.chunks_indexed", chunk_count)

    result = _put_result(
        doc_id,
        req,
        sha256,
        index_status=index_status,
        index_error=index_error,
        chunk_count=chunk_count,
    )
    if warning:
        result["warning"] = warning
    if index and req.text is None:
        result["index_skipped"] = "content is not UTF-8 text; caller-side extraction required"
    return result


def document_get(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    doc_id: str,
    meta_only: bool = False,
) -> dict[str, Any]:
    """Fetch one document's metadata and (unless *meta_only*) its content."""
    doc_store = _document_store(store)
    if doc_store is None:
        return _unavailable()
    clean_id = (doc_id or "").strip()
    if not clean_id:
        return _bad_request("doc_id is required.")

    found = doc_store.get(clean_id, include_content=not meta_only)
    if found is None:
        detail = f"Document {clean_id!r} not found."
        return {"error": "not_found", "detail": detail, "message": detail}
    meta, raw = found

    result: dict[str, Any] = {"document": _meta_payload(meta)}
    if not meta_only and raw is not None:
        try:
            result["content"] = raw.decode("utf-8")
        except UnicodeDecodeError:
            result["content_base64"] = base64.b64encode(raw).decode("ascii")
    return result


def document_list(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    tag: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """List document metadata for the project, optionally filtered by tag."""
    doc_store = _document_store(store)
    if doc_store is None:
        return _unavailable()
    metas = doc_store.list_documents(tag=tag.strip() or None, limit=max(1, min(limit, 500)))
    return {
        "documents": [_meta_payload(m) for m in metas],
        "count": len(metas),
    }


def document_delete(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    doc_id: str,
) -> dict[str, Any]:
    """Delete a document; its chunks cascade."""
    doc_store = _document_store(store)
    if doc_store is None:
        return _unavailable()
    clean_id = (doc_id or "").strip()
    if not clean_id:
        return _bad_request("doc_id is required.")
    if not doc_store.delete(clean_id):
        detail = f"Document {clean_id!r} not found."
        return {"error": "not_found", "detail": detail, "message": detail}
    metrics = getattr(store, "_metrics", None)
    if metrics is not None:
        metrics.increment("documents.deleted")
    return {"status": "deleted", "doc_id": clean_id}


def document_search(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Hybrid search over document chunks: tsvector + pgvector fused with RRF.

    Falls back to lexical-only when no embedding provider is available.
    Documents with ``index_status='error'`` are never returned.
    """
    doc_store = _document_store(store)
    if doc_store is None:
        return _unavailable()
    clean_query = (query or "").strip()
    if not clean_query:
        return _bad_request("query is required.")
    limit = max(1, min(limit, _MAX_SEARCH_LIMIT))
    pool = max(limit * _POOL_MULTIPLIER, _MIN_POOL)

    lexical = doc_store.lexical_search(clean_query, limit=pool)

    semantic: list[tuple[str, int, str, str]] = []
    embedder = _resolve_embedder(store)
    if embedder is not None:
        try:
            query_vec = embedder.embed(clean_query)
        except Exception:
            logger.warning("documents.query_embed_failed", exc_info=True)
            query_vec = []
        if query_vec:
            semantic = doc_store.semantic_search(query_vec, limit=pool)

    by_key: dict[str, tuple[str, str]] = {}
    for doc_id, chunk_no, chunk_content, title in (*lexical, *semantic):
        by_key.setdefault(make_chunk_key(doc_id, chunk_no), (chunk_content, title))

    bm25_w, vec_w = hybrid_rrf_weights_for_query(clean_query)
    fused = reciprocal_rank_fusion_weighted(
        [make_chunk_key(d, n) for d, n, _, _ in lexical],
        [make_chunk_key(d, n) for d, n, _, _ in semantic],
        bm25_weight=bm25_w,
        vector_weight=vec_w,
    )

    hits: list[DocumentSearchHit] = []
    for key, score in fused[:limit]:
        doc_id, chunk_no = split_chunk_key(key)
        chunk_content, title = by_key[key]
        hits.append(
            DocumentSearchHit(
                doc_id=doc_id,
                chunk_no=chunk_no,
                title=title,
                snippet=chunk_content[:_SNIPPET_CHARS],
                score=round(score, 6),
            )
        )

    metrics = getattr(store, "_metrics", None)
    if metrics is not None:
        metrics.increment("documents.search")

    return {
        "results": [h.model_dump(mode="json") for h in hits],
        "count": len(hits),
        "channels": {"lexical": len(lexical), "semantic": len(semantic)},
    }
