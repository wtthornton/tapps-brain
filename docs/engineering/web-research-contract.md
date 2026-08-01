# Web research BrainBridge contract (TAP-5364 / ADR-0030)

Authoritative request/response shapes for tapps-mcp **TAP-5365** to bind
`HttpBrainBridge` against brain MCP tools `web_research` and `research_fetch`.

Credentials (Exa / Tavily / Firecrawl) live **brain-side only**. Consumers call
these tools over HTTP MCP; they must never hold provider API keys.

## Tools

### `web_research`

| Arg | Type | Default | Notes |
|-----|------|---------|-------|
| `query` | string | required | Free-text search |
| `source` | string | `"auto"` | `auto` \| `exa` \| `tavily` \| `firecrawl` |
| `freshness` | string | `"volatile"` | `volatile` \| `evergreen` |
| `max_results` | int | `5` | 1–20 |

`source=auto` uses the first configured key in order **tavily → exa → firecrawl**.

### `research_fetch`

| Arg | Type | Default | Notes |
|-----|------|---------|-------|
| `url` | string | required | Single URL to scrape (Firecrawl) |
| `freshness` | string | `"evergreen"` | `volatile` \| `evergreen` |

## Success response

Returned as a JSON string from the MCP tool (parse before use):

```json
{
  "success": true,
  "query": "...",
  "url": null,
  "source": "cache|api|stale_fallback",
  "provider": "exa|tavily|firecrawl",
  "cache_hit": false,
  "freshness_tier": "volatile|evergreen",
  "results": [
    {"title": "...", "url": "...", "snippet": "...", "content": "..."}
  ],
  "response_time_ms": 12.3,
  "degraded": false,
  "warning": null
}
```

## Failure / degradation

Never treat an empty body as success. Structured errors:

```json
{
  "success": false,
  "error": "not_configured|provider_unavailable|ssrf_blocked|rag_safety_blocked|invalid_args|no_results",
  "detail": "...",
  "degraded": true,
  "retryable": true,
  "freshness_tier": "volatile",
  "stale_results": null
}
```

| Condition | Shape |
|-----------|--------|
| No provider keys, no cache | `success=false`, `error=not_configured` |
| Provider HTTP failure, no stale | `success=false`, `error=provider_unavailable`, `retryable=true` |
| Provider failure, expired cache present | `success=true`, `source=stale_fallback`, `degraded=true`, `warning=<reason>` |
| SSRF / hard RAG block | `success=false`, matching error; nothing written to cache |
| Soft RAG sanitize | `success=true`, sanitised `results`, optional `warning` |

## Brain-down (tapps-mcp responsibility)

When BrainBridge cannot reach the brain (transport / auth / unknown tool),
tapps-mcp must return a structured degraded payload in the spirit of memory’s
`_bridge_call_failed_response` (`success=false`, `degraded=true`,
`retryable=true`, remediation). Do **not** fall back to silent empty success
or local Exa/Firecrawl keys.

Suggested bridge methods (TAP-5365):

```python
async def web_research(self, query: str, *, source="auto", freshness="volatile",
                       max_results=5) -> dict[str, Any]: ...
async def research_fetch(self, url: str, *, freshness="evergreen") -> dict[str, Any]: ...
```

`InProcessBrainBridge` should raise `BrainBridgeUnavailable` (same as `docs_*`).

## Cache model (brain)

- Project / agent: `TAPPS_BRAIN_RESEARCH_PROJECT_ID` (default `web-research`) /
  `research-cache`
- Memory group: `web-research`
- Search key: `research:{provider}:{normalized_query}`
- Fetch key: `research:fetch:{sha256(url)}`
- TTL: `RESEARCH_CACHE_TTL_VOLATILE` (default 3600s),
  `RESEARCH_CACHE_TTL_EVERGREEN` (default 604800s)

Answer-level pattern-tier recall is **out of scope** here (TAP-5366).

## Safety

Before write-through:

1. `url_guard.validate_url` on result / fetch URLs
2. `safety.check_content_safety` on text fields

Env: `RESEARCH_ALLOW_HTTP`, `RESEARCH_ALLOW_PRIVATE_HOSTS`, `RESEARCH_MAX_BYTES`.

## Refs

- ADR-0030 (tapps-mcp)
- ADR-0014 (`docs_lookup` pattern)
- Linear: TAP-5364, TAP-5365, TAP-4419
