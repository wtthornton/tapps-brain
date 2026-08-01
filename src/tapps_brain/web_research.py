"""Brain-central web research fetch with write-through cache (TAP-5364 / ADR-0030).

Stores raw research results under a dedicated project
(``TAPPS_BRAIN_RESEARCH_PROJECT_ID``, default ``web-research``) with
``memory_group=web-research``.  On cache miss, calls Exa / Tavily / Firecrawl
when the corresponding API key is set on the brain service.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
import structlog

from tapps_brain.backends import resolve_hive_backend_from_env, resolve_private_backend_from_env
from tapps_brain.exa_sync import ExaError, SyncExaClient
from tapps_brain.firecrawl_sync import FirecrawlError, SyncFirecrawlClient
from tapps_brain.memory_group import normalize_memory_group
from tapps_brain.safety import check_content_safety
from tapps_brain.services import memory_service
from tapps_brain.store import MemoryStore
from tapps_brain.tavily_sync import SyncTavilyClient, TavilyError
from tapps_brain.url_guard import UrlGuardConfig, UrlGuardError, validate_url

logger = structlog.get_logger(__name__)

DEFAULT_VOLATILE_TTL_SECONDS = 3_600.0
DEFAULT_EVERGREEN_TTL_SECONDS = 604_800.0
RESEARCH_MEMORY_GROUP = "web-research"
RESEARCH_AGENT_ID = "research-cache"
RESEARCH_PROJECT_ENV = "TAPPS_BRAIN_RESEARCH_PROJECT_ID"
RESEARCH_AGENT_ENV = "TAPPS_BRAIN_RESEARCH_AGENT_ID"
VOLATILE_TTL_ENV = "RESEARCH_CACHE_TTL_VOLATILE"
EVERGREEN_TTL_ENV = "RESEARCH_CACHE_TTL_EVERGREEN"
EXA_KEY_ENV = "EXA_API_KEY"
TAVILY_KEY_ENV = "TAVILY_API_KEY"
FIRECRAWL_KEY_ENV = "FIRECRAWL_API_KEY"

FreshnessTier = Literal["volatile", "evergreen"]
ProviderName = Literal["exa", "tavily", "firecrawl"]
AUTO_PROVIDER_ORDER: tuple[ProviderName, ...] = ("tavily", "exa", "firecrawl")

_QUERY_SANITIZE_RE = re.compile(r"[^a-z0-9_\-.\s]+")


class ResearchProviderError(Exception):
    """Raised when a research provider call fails."""


class ResearchProvider(Protocol):
    """Minimal search provider surface."""

    def search(self, query: str, *, max_results: int = 5) -> list[dict[str, str]]: ...


@dataclass(frozen=True)
class ResearchConfig:
    """Runtime configuration for brain web research cache."""

    project_id: str
    agent_id: str
    volatile_ttl_seconds: float
    evergreen_ttl_seconds: float
    exa_api_key: str | None
    tavily_api_key: str | None
    firecrawl_api_key: str | None
    url_guard: UrlGuardConfig

    @classmethod
    def from_env(cls) -> ResearchConfig:
        return cls(
            project_id=(
                os.environ.get(RESEARCH_PROJECT_ENV, "web-research").strip() or "web-research"
            ),
            agent_id=(
                os.environ.get(RESEARCH_AGENT_ENV, RESEARCH_AGENT_ID).strip() or RESEARCH_AGENT_ID
            ),
            volatile_ttl_seconds=_parse_ttl(
                VOLATILE_TTL_ENV,
                DEFAULT_VOLATILE_TTL_SECONDS,
            ),
            evergreen_ttl_seconds=_parse_ttl(
                EVERGREEN_TTL_ENV,
                DEFAULT_EVERGREEN_TTL_SECONDS,
            ),
            exa_api_key=os.environ.get(EXA_KEY_ENV, "").strip() or None,
            tavily_api_key=os.environ.get(TAVILY_KEY_ENV, "").strip() or None,
            firecrawl_api_key=os.environ.get(FIRECRAWL_KEY_ENV, "").strip() or None,
            url_guard=UrlGuardConfig.from_env(),
        )

    def ttl_for(self, freshness: FreshnessTier) -> float:
        if freshness == "evergreen":
            return self.evergreen_ttl_seconds
        return self.volatile_ttl_seconds

    def configured_providers(self) -> list[ProviderName]:
        found: list[ProviderName] = []
        if self.tavily_api_key:
            found.append("tavily")
        if self.exa_api_key:
            found.append("exa")
        if self.firecrawl_api_key:
            found.append("firecrawl")
        return found


def _parse_ttl(env_name: str, default: float) -> float:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning("research_config.invalid_ttl", env=env_name, raw=raw, fallback=default)
        return default
    if math.isfinite(parsed) and parsed >= 0.0:
        return parsed
    logger.warning("research_config.invalid_ttl", env=env_name, raw=raw, fallback=default)
    return default


_research_store_cache: dict[tuple[str, str], MemoryStore] = {}
_research_store_lock = threading.Lock()


def open_research_store(
    cfg: ResearchConfig | None = None,
    *,
    project_root: Path | None = None,
) -> MemoryStore:
    """Open a ``MemoryStore`` for the shared web-research project."""
    resolved = cfg or ResearchConfig.from_env()
    backend = resolve_private_backend_from_env(resolved.project_id, resolved.agent_id)
    if backend is None:
        msg = "Research store requires TAPPS_BRAIN_DATABASE_URL (postgres:// or postgresql:// DSN)."
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


def get_research_store(cfg: ResearchConfig | None = None) -> MemoryStore:
    """Return a cached research ``MemoryStore`` (one per ``(project_id, agent_id)``)."""
    resolved = cfg or ResearchConfig.from_env()
    key = (resolved.project_id, resolved.agent_id)
    with _research_store_lock:
        cached = _research_store_cache.get(key)
        if cached is not None:
            return cached
        store = open_research_store(resolved)
        _research_store_cache[key] = store
        return store


def normalize_query(query: str) -> str:
    """Normalize a free-text query for cache keying."""
    cleaned = _QUERY_SANITIZE_RE.sub(" ", query.strip().lower())
    return " ".join(cleaned.split())


def research_search_key(provider: str, query: str) -> str:
    """Stable memory key for a provider + query pair."""
    norm = normalize_query(query).replace(":", "_").replace("/", "_").replace("\\", "_")
    return f"research:{provider}:{norm}"


def research_fetch_key(url: str) -> str:
    """Stable memory key for a fetched URL."""
    digest = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
    return f"research:fetch:{digest}"


def _encode_payload(
    *,
    results: list[dict[str, str]],
    provider: str,
    freshness_tier: FreshnessTier,
    query: str | None = None,
    url: str | None = None,
) -> str:
    payload = {
        "results": results,
        "provider": provider,
        "freshness_tier": freshness_tier,
        "query": query,
        "url": url,
        "cached_at": datetime.now(UTC).isoformat(),
    }
    return json.dumps(payload, ensure_ascii=False)


def decode_research_value(raw: str) -> dict[str, Any] | None:
    """Parse a stored research entry value."""
    text = raw.strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    results = parsed.get("results")
    if not isinstance(results, list):
        return None
    return parsed


def _parse_cached_at(payload: dict[str, Any]) -> datetime | None:
    raw = payload.get("cached_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _is_fresh(payload: dict[str, Any], ttl_seconds: float) -> bool:
    raw_cached_at = payload.get("cached_at")
    cached_at = _parse_cached_at(payload)
    if cached_at is None:
        return not (isinstance(raw_cached_at, str) and raw_cached_at.strip())
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - cached_at < timedelta(seconds=ttl_seconds)


def _read_cached_entry(
    store: Any,
    cfg: ResearchConfig,
    key: str,
) -> dict[str, Any] | None:
    row = memory_service.memory_get(store, cfg.project_id, cfg.agent_id, key=key)
    if not isinstance(row, dict) or row.get("error") or not row.get("value"):
        return None
    payload = decode_research_value(str(row["value"]))
    if payload is None:
        return None
    payload["cache_hits"] = int(row.get("access_count") or 0)
    return payload


def _persist_entry(
    store: Any,
    cfg: ResearchConfig,
    *,
    key: str,
    results: list[dict[str, str]],
    provider: str,
    freshness_tier: FreshnessTier,
    query: str | None = None,
    url: str | None = None,
) -> None:
    value = _encode_payload(
        results=results,
        provider=provider,
        freshness_tier=freshness_tier,
        query=query,
        url=url,
    )
    tags = [f"source:{provider}", "web-research", f"freshness:{freshness_tier}"]
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
        confidence=0.9,
        agent_scope="hive",
        group=normalize_memory_group(RESEARCH_MEMORY_GROUP),
    )


def _response(
    *,
    success: bool,
    start: float,
    query: str | None = None,
    url: str | None = None,
    source: str | None = None,
    provider: str | None = None,
    cache_hit: bool = False,
    freshness_tier: FreshnessTier = "volatile",
    results: list[dict[str, str]] | None = None,
    error: str | None = None,
    detail: str | None = None,
    degraded: bool = False,
    retryable: bool = False,
    warning: str | None = None,
    stale_results: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    payload: dict[str, Any] = {
        "success": success,
        "query": query,
        "url": url,
        "cache_hit": cache_hit,
        "freshness_tier": freshness_tier,
        "response_time_ms": elapsed_ms,
        "degraded": degraded,
    }
    if source is not None:
        payload["source"] = source
    if provider is not None:
        payload["provider"] = provider
    if results is not None:
        payload["results"] = results
    if error is not None:
        payload["error"] = error
    if detail is not None:
        payload["detail"] = detail
    if retryable:
        payload["retryable"] = True
    elif not success:
        payload["retryable"] = False
    if warning is not None:
        payload["warning"] = warning
    if stale_results is not None:
        payload["stale_results"] = stale_results
    return payload


def _normalize_freshness(raw: str) -> FreshnessTier | None:
    value = raw.strip().lower()
    if value in {"volatile", "evergreen"}:
        return value  # type: ignore[return-value]
    return None


def _provider_client(name: ProviderName, cfg: ResearchConfig) -> ResearchProvider:
    if name == "tavily":
        return SyncTavilyClient(cfg.tavily_api_key)
    if name == "exa":
        return SyncExaClient(cfg.exa_api_key)
    return SyncFirecrawlClient(cfg.firecrawl_api_key)


def _resolve_search_provider(
    source: str,
    cfg: ResearchConfig,
) -> tuple[ProviderName | None, str | None]:
    normalized = source.strip().lower() or "auto"
    if normalized == "auto":
        configured = cfg.configured_providers()
        if not configured:
            return None, "not_configured"
        return configured[0], None
    if normalized not in {"exa", "tavily", "firecrawl"}:
        return None, "invalid_args"
    name: ProviderName = normalized  # type: ignore[assignment]
    key_map = {
        "exa": cfg.exa_api_key,
        "tavily": cfg.tavily_api_key,
        "firecrawl": cfg.firecrawl_api_key,
    }
    if not key_map[name]:
        return None, "not_configured"
    return name, None


def _result_text(hit: dict[str, str]) -> str:
    parts = [hit.get("title") or "", hit.get("snippet") or "", hit.get("content") or ""]
    return "\n".join(p for p in parts if p)


def _sanitize_results(
    results: list[dict[str, str]],
    cfg: ResearchConfig,
    *,
    fail_on_ssrf: bool,
) -> tuple[list[dict[str, str]], str | None, str | None]:
    """Apply SSRF + RAG safety. Returns (safe_results, warning, hard_error)."""
    safe: list[dict[str, str]] = []
    warnings: list[str] = []
    for hit in results:
        url = (hit.get("url") or "").strip()
        if url:
            try:
                validate_url(url, cfg.url_guard)
            except UrlGuardError as exc:
                if fail_on_ssrf and len(results) == 1:
                    return [], None, f"ssrf_blocked:{exc}"
                logger.warning("research.ssrf_skip", url=url, detail=str(exc))
                continue
        text = _result_text(hit)
        safety = check_content_safety(text)
        if not safety.safe:
            if fail_on_ssrf and len(results) == 1:
                return [], None, "rag_safety_blocked"
            logger.warning(
                "research.rag_blocked_skip",
                url=url,
                patterns=safety.flagged_patterns,
            )
            continue
        cleaned = dict(hit)
        if safety.sanitised_content is not None:
            cleaned["content"] = safety.sanitised_content
            if cleaned.get("snippet"):
                cleaned["snippet"] = safety.sanitised_content[:400]
            if safety.warning:
                warnings.append(safety.warning)
        safe.append(cleaned)
    warning = "; ".join(warnings) if warnings else None
    return safe, warning, None


def _stale_results(cached: dict[str, Any] | None) -> list[dict[str, str]] | None:
    if not cached:
        return None
    raw = cached.get("results")
    if not isinstance(raw, list) or not raw:
        return None
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("url"):
            out.append(
                {
                    "title": str(item.get("title") or ""),
                    "url": str(item.get("url") or ""),
                    "snippet": str(item.get("snippet") or ""),
                    "content": str(item.get("content") or ""),
                }
            )
    return out or None


def web_research(
    store: Any,
    *,
    query: str,
    source: str = "auto",
    freshness: str = "volatile",
    max_results: int = 5,
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """Search the web; cache raw results in Postgres, fetch on miss."""
    start = time.perf_counter()
    cfg = config or ResearchConfig.from_env()
    query_clean = query.strip()
    tier = _normalize_freshness(freshness)
    if not query_clean:
        return _response(
            success=False,
            start=start,
            query=query_clean,
            error="invalid_args",
            detail="query is required",
            degraded=True,
        )
    if tier is None:
        return _response(
            success=False,
            start=start,
            query=query_clean,
            error="invalid_args",
            detail="freshness must be 'volatile' or 'evergreen'",
            degraded=True,
        )
    if max_results < 1 or max_results > 20:
        return _response(
            success=False,
            start=start,
            query=query_clean,
            freshness_tier=tier,
            error="invalid_args",
            detail="max_results must be between 1 and 20",
            degraded=True,
        )

    provider, cfg_err = _resolve_search_provider(source, cfg)
    if cfg_err == "invalid_args":
        return _response(
            success=False,
            start=start,
            query=query_clean,
            freshness_tier=tier,
            error="invalid_args",
            detail="source must be 'auto', 'exa', 'tavily', or 'firecrawl'",
            degraded=True,
        )

    # For auto, try cache under each configured provider key (prefer first hit).
    cached: dict[str, Any] | None = None
    cache_provider = provider
    if provider is not None:
        key = research_search_key(provider, query_clean)
        cached = _read_cached_entry(store, cfg, key)
    elif source.strip().lower() in {"", "auto"}:
        for name in AUTO_PROVIDER_ORDER:
            candidate = _read_cached_entry(store, cfg, research_search_key(name, query_clean))
            if candidate and candidate.get("results"):
                cached = candidate
                cache_provider = name
                break

    ttl = cfg.ttl_for(tier)
    if cached and cached.get("results") and _is_fresh(cached, ttl):
        return _response(
            success=True,
            start=start,
            query=query_clean,
            source="cache",
            provider=str(cached.get("provider") or cache_provider or "cache"),
            cache_hit=True,
            freshness_tier=tier,
            results=_stale_results(cached) or [],
        )

    stale = _stale_results(cached)

    if provider is None:
        if stale:
            return _response(
                success=True,
                start=start,
                query=query_clean,
                source="stale_fallback",
                provider=str(cached.get("provider") if cached else cache_provider or "cache"),
                cache_hit=True,
                freshness_tier=tier,
                results=stale,
                degraded=True,
                warning="No research provider API keys configured; returning stale cache",
            )
        return _response(
            success=False,
            start=start,
            query=query_clean,
            freshness_tier=tier,
            error="not_configured",
            detail=(
                "No research provider API keys configured "
                "(TAVILY_API_KEY / EXA_API_KEY / FIRECRAWL_API_KEY)"
            ),
            degraded=True,
            retryable=False,
            stale_results=None,
        )

    try:
        client = _provider_client(provider, cfg)
        raw_results = client.search(query_clean, max_results=max_results)
    except (ExaError, TavilyError, FirecrawlError, ResearchProviderError, httpx.HTTPError) as exc:
        if stale:
            return _response(
                success=True,
                start=start,
                query=query_clean,
                source="stale_fallback",
                provider=provider,
                cache_hit=True,
                freshness_tier=tier,
                results=stale,
                degraded=True,
                warning=str(exc),
            )
        return _response(
            success=False,
            start=start,
            query=query_clean,
            provider=provider,
            freshness_tier=tier,
            error="provider_unavailable",
            detail=str(exc),
            degraded=True,
            retryable=True,
        )

    if not raw_results:
        if stale:
            return _response(
                success=True,
                start=start,
                query=query_clean,
                source="stale_fallback",
                provider=provider,
                cache_hit=True,
                freshness_tier=tier,
                results=stale,
                degraded=True,
                warning="Provider returned no results; returning stale cache",
            )
        return _response(
            success=False,
            start=start,
            query=query_clean,
            provider=provider,
            freshness_tier=tier,
            error="no_results",
            detail="Provider returned no results",
            degraded=True,
            retryable=True,
        )

    safe_results, warning, hard_err = _sanitize_results(
        raw_results,
        cfg,
        fail_on_ssrf=False,
    )
    if hard_err and hard_err.startswith("ssrf_blocked"):
        return _response(
            success=False,
            start=start,
            query=query_clean,
            provider=provider,
            freshness_tier=tier,
            error="ssrf_blocked",
            detail=hard_err.split(":", 1)[-1],
            degraded=True,
        )
    if hard_err == "rag_safety_blocked":
        return _response(
            success=False,
            start=start,
            query=query_clean,
            provider=provider,
            freshness_tier=tier,
            error="rag_safety_blocked",
            detail="All results blocked by RAG safety",
            degraded=True,
        )
    if not safe_results:
        return _response(
            success=False,
            start=start,
            query=query_clean,
            provider=provider,
            freshness_tier=tier,
            error="rag_safety_blocked",
            detail="All results blocked by SSRF or RAG safety",
            degraded=True,
        )

    _persist_entry(
        store,
        cfg,
        key=research_search_key(provider, query_clean),
        results=safe_results,
        provider=provider,
        freshness_tier=tier,
        query=normalize_query(query_clean),
    )
    return _response(
        success=True,
        start=start,
        query=query_clean,
        source="api",
        provider=provider,
        freshness_tier=tier,
        results=safe_results,
        warning=warning,
    )


def research_fetch(
    store: Any,
    *,
    url: str,
    freshness: str = "evergreen",
    config: ResearchConfig | None = None,
) -> dict[str, Any]:
    """Fetch a single URL via Firecrawl; cache raw result, fetch on miss."""
    start = time.perf_counter()
    cfg = config or ResearchConfig.from_env()
    url_clean = url.strip()
    tier = _normalize_freshness(freshness)
    if not url_clean:
        return _response(
            success=False,
            start=start,
            url=url_clean,
            error="invalid_args",
            detail="url is required",
            degraded=True,
        )
    if tier is None:
        return _response(
            success=False,
            start=start,
            url=url_clean,
            error="invalid_args",
            detail="freshness must be 'volatile' or 'evergreen'",
            degraded=True,
        )

    try:
        validate_url(url_clean, cfg.url_guard)
    except UrlGuardError as exc:
        return _response(
            success=False,
            start=start,
            url=url_clean,
            freshness_tier=tier,
            error="ssrf_blocked",
            detail=str(exc),
            degraded=True,
        )

    key = research_fetch_key(url_clean)
    cached = _read_cached_entry(store, cfg, key)
    ttl = cfg.ttl_for(tier)
    if cached and cached.get("results") and _is_fresh(cached, ttl):
        return _response(
            success=True,
            start=start,
            url=url_clean,
            source="cache",
            provider=str(cached.get("provider") or "firecrawl"),
            cache_hit=True,
            freshness_tier=tier,
            results=_stale_results(cached) or [],
        )

    stale = _stale_results(cached)

    if not cfg.firecrawl_api_key:
        if stale:
            return _response(
                success=True,
                start=start,
                url=url_clean,
                source="stale_fallback",
                provider="firecrawl",
                cache_hit=True,
                freshness_tier=tier,
                results=stale,
                degraded=True,
                warning="FIRECRAWL_API_KEY not set; returning stale cache",
            )
        return _response(
            success=False,
            start=start,
            url=url_clean,
            freshness_tier=tier,
            error="not_configured",
            detail="FIRECRAWL_API_KEY not configured and no cached entry",
            degraded=True,
            retryable=False,
        )

    try:
        client = SyncFirecrawlClient(cfg.firecrawl_api_key)
        hit = client.scrape(url_clean)
    except (FirecrawlError, httpx.HTTPError) as exc:
        if stale:
            return _response(
                success=True,
                start=start,
                url=url_clean,
                source="stale_fallback",
                provider="firecrawl",
                cache_hit=True,
                freshness_tier=tier,
                results=stale,
                degraded=True,
                warning=str(exc),
            )
        return _response(
            success=False,
            start=start,
            url=url_clean,
            provider="firecrawl",
            freshness_tier=tier,
            error="provider_unavailable",
            detail=str(exc),
            degraded=True,
            retryable=True,
        )

    safe_results, warning, hard_err = _sanitize_results([hit], cfg, fail_on_ssrf=True)
    if hard_err and hard_err.startswith("ssrf_blocked"):
        return _response(
            success=False,
            start=start,
            url=url_clean,
            provider="firecrawl",
            freshness_tier=tier,
            error="ssrf_blocked",
            detail=hard_err.split(":", 1)[-1],
            degraded=True,
        )
    if hard_err == "rag_safety_blocked" or not safe_results:
        return _response(
            success=False,
            start=start,
            url=url_clean,
            provider="firecrawl",
            freshness_tier=tier,
            error="rag_safety_blocked",
            detail="Fetched content blocked by RAG safety",
            degraded=True,
        )

    _persist_entry(
        store,
        cfg,
        key=key,
        results=safe_results,
        provider="firecrawl",
        freshness_tier=tier,
        url=url_clean,
    )
    return _response(
        success=True,
        start=start,
        url=url_clean,
        source="api",
        provider="firecrawl",
        freshness_tier=tier,
        results=safe_results,
        warning=warning,
    )
