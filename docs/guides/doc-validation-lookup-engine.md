# Pluggable lookup engine for doc validation

`tapps-brain` validates memory entries against authoritative documentation using a
**pluggable lookup engine** — any object that satisfies the `LookupEngineLike` protocol.
This lets you wire in any documentation source: a local file cache, an MCP tool, an
HTTP API, or your own retrieval pipeline.

## The protocol

```python
# tapps_brain._protocols

class LookupResult(Protocol):
    @property
    def success(self) -> bool: ...  # True if docs were found

    @property
    def content(self) -> str: ...   # Raw documentation text (markdown)


class LookupEngineLike(Protocol):
    async def lookup(self, library: str, topic: str) -> LookupResult: ...
```

Your engine receives two strings — `library` (e.g. `"fastapi"`) and `topic`
(e.g. `"configuration"`) — and returns a result object with `success` and `content`.

---

## Minimal stub (testing / CI)

Use this as a starting point or in tests where you want deterministic behaviour:

```python
from dataclasses import dataclass


@dataclass
class StubLookupResult:
    success: bool
    content: str


class StubLookupEngine:
    """Returns canned documentation keyed by library name."""

    def __init__(self, docs: dict[str, str]) -> None:
        self._docs = docs

    async def lookup(self, library: str, topic: str) -> StubLookupResult:
        content = self._docs.get(library, "")
        return StubLookupResult(success=bool(content), content=content)
```

---

## HTTP-based engine example

Wire in any documentation HTTP API that returns markdown content:

```python
import httpx
from dataclasses import dataclass


@dataclass
class HttpLookupResult:
    success: bool
    content: str


class HttpLookupEngine:
    """Fetch documentation from an HTTP endpoint.

    The endpoint is expected to accept GET requests with `library` and `topic`
    query parameters and return markdown text.
    """

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def lookup(self, library: str, topic: str) -> HttpLookupResult:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.get(
                    f"{self._base_url}/docs",
                    params={"library": library, "topic": topic},
                )
                resp.raise_for_status()
                return HttpLookupResult(success=True, content=resp.text)
            except (httpx.HTTPError, httpx.TimeoutException):
                return HttpLookupResult(success=False, content="")
```

---

## Wiring the engine into MemoryStore

Pass the engine at construction time:

```python
from pathlib import Path
from tapps_brain.store import MemoryStore

engine = HttpLookupEngine(base_url="https://docs.example.com")
store = MemoryStore(project_root=Path(".tapps-brain"), lookup_engine=engine)

# Validate all entries and apply confidence adjustments back to the store
report = store.validate_entries()
print(f"validated={report.validated}  flagged={report.flagged}")
```

---

## Strict mode for CI

Pass `strict=True` to raise `StrictValidationError` if any entries are
doc-contradicted. Use this in CI pipelines for markdown repos where a flagged
entry should be a hard failure:

```python
from tapps_brain.doc_validation import StrictValidationError

try:
    store.validate_entries(strict=True)
except StrictValidationError as exc:
    print(f"Strict mode: {exc}")
    for ev in exc.report.entries:
        if ev.overall_status == "flagged":
            print(f"  flagged: {ev.entry_key} — {ev.reason}")
    raise SystemExit(1)
```

See `scripts/run_doc_validation.py` for a ready-made CI script.

---

## Caching and rate limiting

`MemoryDocValidator` caches lookup results within a single `validate_batch()` call
(keyed by `library:topic`) so the same documentation is never fetched twice per run.
The `max_lookups` parameter limits total unique lookups per batch (default `20`).

```python
from tapps_brain.doc_validation import MemoryDocValidator

validator = MemoryDocValidator(engine, revalidation_interval_days=7)
report = await validator.validate_batch(entries, max_lookups=50)
```

Entries with a `doc-validated:YYYY-MM-DD` or `doc-checked:YYYY-MM-DD` tag added
within the `revalidation_interval_days` window are skipped automatically and do not
consume lookup budget.
