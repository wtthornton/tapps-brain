"""Centralized library documentation lookup and cache (ADR-0014).

Stores doc snippets in Postgres via ``MemoryStore`` under a dedicated docs
project (``TAPPS_BRAIN_DOCS_PROJECT_ID``, default ``library-docs``) with
``memory_group=library-docs``.  On cache miss, fetches from Context7 when
``CONTEXT7_API_KEY`` is set.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.context7_sync import Context7Error, SyncContext7Client
from tapps_brain.llms_txt_sync import LlmsTxtError, SyncLlmsTxtClient
from tapps_brain.memory_group import normalize_memory_group
from tapps_brain.services import memory_service

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

DEFAULT_DOCS_CACHE_TTL_SECONDS = 86_400.0
DOCS_MEMORY_GROUP = "library-docs"
DOCS_AGENT_ID = "docs-cache"
DOCS_PROJECT_ENV = "TAPPS_BRAIN_DOCS_PROJECT_ID"
DOCS_AGENT_ENV = "TAPPS_BRAIN_DOCS_AGENT_ID"
DOCS_TTL_ENV = "DOCS_CACHE_TTL"
CONTEXT7_KEY_ENV = "CONTEXT7_API_KEY"
DOCS_LLMS_FALLBACK_ENV = "DOCS_LLMS_TXT_FALLBACK"


@dataclass(frozen=True)
class DocsConfig:
    """Runtime configuration for brain-central doc RAG."""

    project_id: str
    agent_id: str
    cache_ttl_seconds: float
    context7_api_key: str | None
    llms_txt_fallback: bool

    @classmethod
    def from_env(cls) -> DocsConfig:
        ttl_raw = os.environ.get(DOCS_TTL_ENV, "").strip()
        ttl = float(ttl_raw) if ttl_raw else DEFAULT_DOCS_CACHE_TTL_SECONDS
        key = os.environ.get(CONTEXT7_KEY_ENV, "").strip() or None
        fallback_raw = os.environ.get(DOCS_LLMS_FALLBACK_ENV, "1").strip().lower()
        llms_fallback = fallback_raw not in {"0", "false", "no", "off"}
        return cls(
            project_id=os.environ.get(DOCS_PROJECT_ENV, "library-docs").strip() or "library-docs",
            agent_id=os.environ.get(DOCS_AGENT_ENV, DOCS_AGENT_ID).strip() or DOCS_AGENT_ID,
            cache_ttl_seconds=ttl,
            context7_api_key=key,
            llms_txt_fallback=llms_fallback,
        )


_docs_store_cache: dict[tuple[str, str], MemoryStore] = {}
_docs_store_lock = threading.Lock()


def open_docs_store(
    cfg: DocsConfig | None = None,
    *,
    project_root: Path | None = None,
) -> MemoryStore:
    """Open a ``MemoryStore`` for the shared library-docs project (ADR-0014).

    Doc cache rows live under ``(project_id, agent_id)`` from :class:`DocsConfig`,
    not the MCP caller's project.  Callers must ``close()`` when done unless using
    :func:`get_docs_store` (process-wide cache for MCP tools).
    """
    from tapps_brain.backends import resolve_hive_backend_from_env, resolve_private_backend_from_env
    from tapps_brain.store import MemoryStore

    resolved = cfg or DocsConfig.from_env()
    backend = resolve_private_backend_from_env(resolved.project_id, resolved.agent_id)
    if backend is None:
        msg = "Docs store requires TAPPS_BRAIN_DATABASE_URL (postgres:// or postgresql:// DSN)."
        raise ValueError(msg)
    hive_store = resolve_hive_backend_from_env()
    root = (project_root or Path.cwd()).resolve()
    return MemoryStore(
        root,
        agent_id=resolved.agent_id,
        private_backend=backend,
        auto_register=False,
        hive_store=hive_store,
        hive_agent_id=resolved.agent_id,
    )


def get_docs_store(cfg: DocsConfig | None = None) -> MemoryStore:
    """Return a cached docs ``MemoryStore`` (one per ``(project_id, agent_id)``)."""
    resolved = cfg or DocsConfig.from_env()
    key = (resolved.project_id, resolved.agent_id)
    with _docs_store_lock:
        cached = _docs_store_cache.get(key)
        if cached is not None:
            return cached
        store = open_docs_store(resolved)
        _docs_store_cache[key] = store
        return store


def doc_memory_key(library: str, topic: str) -> str:
    """Stable memory key for a library/topic pair."""
    lib = library.strip().lower().replace("/", "_").replace("\\", "_").replace(":", "_")
    top = (topic.strip().lower() or "overview").replace("/", "_").replace("\\", "_")
    return f"docs:{lib}:{top}"


def _encode_doc_value(
    *,
    content: str,
    library: str,
    topic: str,
    mode: str,
    context7_id: str | None,
    provider_source: str,
) -> str:
    payload = {
        "content": content,
        "library": library,
        "topic": topic,
        "mode": mode,
        "context7_id": context7_id,
        "provider_source": provider_source,
        "cached_at": datetime.now(UTC).isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False)


def decode_doc_value(raw: str) -> dict[str, Any]:
    """Parse a stored doc entry value; plain markdown is treated as legacy."""
    text = raw.strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get("content"), str):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"content": raw, "provider_source": "import", "context7_id": None}


def _parse_cached_at(payload: dict[str, Any]) -> datetime | None:
    raw = payload.get("cached_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_fresh(payload: dict[str, Any], ttl_seconds: float) -> bool:
    cached_at = _parse_cached_at(payload)
    if cached_at is None:
        return True
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - cached_at < timedelta(seconds=ttl_seconds)


def _read_cached_entry(
    store: Any,
    cfg: DocsConfig,
    library: str,
    topic: str,
) -> dict[str, Any] | None:
    key = doc_memory_key(library, topic)
    row = memory_service.memory_get(store, cfg.project_id, cfg.agent_id, key=key)
    if not isinstance(row, dict) or row.get("error") or not row.get("value"):
        return None
    payload = decode_doc_value(str(row["value"]))
    payload["cache_hits"] = int(row.get("access_count") or 0)
    return payload


def _persist_doc_entry(
    store: Any,
    cfg: DocsConfig,
    *,
    library: str,
    topic: str,
    mode: str,
    content: str,
    context7_id: str | None,
    provider_source: str,
) -> None:
    key = doc_memory_key(library, topic)
    value = _encode_doc_value(
        content=content,
        library=library,
        topic=topic,
        mode=mode,
        context7_id=context7_id,
        provider_source=provider_source,
    )
    tags = [f"source:{provider_source}", "library-docs"]
    if context7_id:
        tags.append(f"context7_id:{context7_id}")
    memory_service.memory_save(
        store,
        cfg.project_id,
        cfg.agent_id,
        key=key,
        value=value,
        tier="pattern",
        source="system",
        tags=tags,
        scope="project",
        confidence=0.95,
        agent_scope="hive",
        group=normalize_memory_group(DOCS_MEMORY_GROUP),
    )


def _lookup_response(
    *,
    success: bool,
    start: float,
    library: str,
    topic: str,
    content: str | None = None,
    source: str = "cache",
    context7_id: str | None = None,
    cache_hit: bool = False,
    error: str | None = None,
    warning: str | None = None,
    provider_source: str | None = None,
) -> dict[str, Any]:
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    payload: dict[str, Any] = {
        "success": success,
        "library": library,
        "topic": topic,
        "cache_hit": cache_hit,
        "response_time_ms": elapsed_ms,
    }
    if content is not None:
        payload["content"] = content
    if source:
        payload["source"] = source
    if context7_id is not None:
        payload["context7_id"] = context7_id
    if provider_source is not None:
        payload["provider_source"] = provider_source
    if error is not None:
        payload["error"] = error
    if warning is not None:
        payload["warning"] = warning
    return payload


def _fetch_from_context7(
    cfg: DocsConfig,
    library: str,
    topic: str,
    mode: str,
) -> tuple[str | None, str, str]:
    client = SyncContext7Client(cfg.context7_api_key)
    matches = client.resolve_library(library)
    if not matches or not matches[0].get("id"):
        raise Context7Error(f"No Context7 match for {library}")
    context7_id = matches[0]["id"]
    content = client.fetch_docs(context7_id, topic=topic, mode=mode)
    return context7_id, content, "context7"


def _fetch_from_llms_txt(library: str, topic: str) -> tuple[str | None, str, str]:
    client = SyncLlmsTxtClient()
    source_url, content = client.fetch(library, topic=topic)
    return source_url, content, "llmstxt"


def _fetch_remote_docs(
    cfg: DocsConfig,
    library: str,
    topic: str,
    mode: str,
) -> tuple[str | None, str, str]:
    """Fetch doc body from Context7 with optional llms.txt fallback."""
    errors: list[str] = []
    if cfg.context7_api_key:
        try:
            return _fetch_from_context7(cfg, library, topic, mode)
        except Context7Error as exc:
            errors.append(str(exc))
            if not cfg.llms_txt_fallback:
                raise
    if cfg.llms_txt_fallback:
        try:
            return _fetch_from_llms_txt(library, topic)
        except LlmsTxtError as exc:
            errors.append(str(exc))
    if errors:
        raise Context7Error("; ".join(errors))
    msg = "CONTEXT7_API_KEY not configured and llms.txt fallback disabled"
    raise Context7Error(msg)


def _stale_snapshot(cached: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not cached or not cached.get("content"):
        return None, None
    stale_id = cached.get("context7_id")
    return str(cached["content"]), stale_id if isinstance(stale_id, str) else None


def _stale_fallback_response(
    *,
    start: float,
    library: str,
    topic: str,
    stale_content: str,
    stale_id: str | None,
    warning: str,
) -> dict[str, Any]:
    return _lookup_response(
        success=True,
        start=start,
        library=library,
        topic=topic,
        content=stale_content,
        source="stale_fallback",
        context7_id=stale_id,
        cache_hit=True,
        warning=warning,
    )


def docs_lookup(
    store: Any,
    *,
    library: str,
    topic: str = "overview",
    mode: str = "code",
    config: DocsConfig | None = None,
) -> dict[str, Any]:
    """Look up library documentation; cache in Postgres, fetch on miss."""
    start = time.perf_counter()
    cfg = config or DocsConfig.from_env()
    lib_clean = library.strip().lower()
    topic_clean = topic.strip() or "overview"
    if not lib_clean:
        return _lookup_response(
            success=False,
            start=start,
            library=lib_clean,
            topic=topic_clean,
            error="library is required",
        )

    cached = _read_cached_entry(store, cfg, lib_clean, topic_clean)
    if cached and cached.get("content") and _is_fresh(cached, cfg.cache_ttl_seconds):
        return _lookup_response(
            success=True,
            start=start,
            library=lib_clean,
            topic=topic_clean,
            content=str(cached["content"]),
            source="cache",
            context7_id=cached.get("context7_id"),
            cache_hit=True,
            provider_source=cached.get("provider_source"),
        )

    stale_content, stale_id = _stale_snapshot(cached)

    if not cfg.context7_api_key and not cfg.llms_txt_fallback:
        if stale_content:
            return _stale_fallback_response(
                start=start,
                library=lib_clean,
                topic=topic_clean,
                stale_content=stale_content,
                stale_id=stale_id,
                warning="CONTEXT7_API_KEY not set; returning stale cache",
            )
        return _lookup_response(
            success=False,
            start=start,
            library=lib_clean,
            topic=topic_clean,
            error="CONTEXT7_API_KEY not configured and no cached entry",
        )

    try:
        external_id, content, provider_source = _fetch_remote_docs(
            cfg,
            lib_clean,
            topic_clean,
            mode,
        )
    except Context7Error as exc:
        if stale_content:
            return _stale_fallback_response(
                start=start,
                library=lib_clean,
                topic=topic_clean,
                stale_content=stale_content,
                stale_id=stale_id,
                warning=str(exc),
            )
        return _lookup_response(
            success=False,
            start=start,
            library=lib_clean,
            topic=topic_clean,
            error=str(exc),
        )

    _persist_doc_entry(
        store,
        cfg,
        library=lib_clean,
        topic=topic_clean,
        mode=mode,
        content=content,
        context7_id=external_id,
        provider_source=provider_source,
    )
    return _lookup_response(
        success=True,
        start=start,
        library=lib_clean,
        topic=topic_clean,
        content=content,
        source="api",
        context7_id=external_id,
        provider_source=provider_source,
    )


def docs_warm(
    store: Any,
    libraries: list[str],
    *,
    topic: str = "overview",
    mode: str = "code",
    config: DocsConfig | None = None,
) -> dict[str, Any]:
    """Pre-fetch documentation for a batch of libraries."""
    warmed: list[str] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []
    for lib in libraries:
        name = lib.strip()
        if not name:
            continue
        result = docs_lookup(store, library=name, topic=topic, mode=mode, config=config)
        if result.get("success"):
            warmed.append(name)
        elif result.get("cache_hit"):
            skipped.append(name)
        else:
            failed.append({"library": name, "error": str(result.get("error", "unknown"))})
    return {
        "warmed": warmed,
        "skipped": skipped,
        "failed": failed,
        "count": len(warmed),
    }
