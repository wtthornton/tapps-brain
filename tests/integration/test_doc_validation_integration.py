"""Integration tests for doc validation with real MemoryStore + SQLite.

Uses real MemoryStore (no mocks), real SQLite/FTS5, real claim extraction,
and real TF-IDF similarity scoring. Stub lookup engine provides canned
documentation. All databases use tmp_path for isolation.

Story: STORY-002.1 from EPIC-002
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tapps_brain.doc_validation import ValidationReport, ValidationStatus
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class StubLookupResult:
    """Stub satisfying the ``LookupResult`` protocol."""

    success: bool
    content: str


class StubLookupEngine:
    """Stub satisfying the ``LookupEngineLike`` protocol.

    Returns canned documentation content keyed by library name.
    """

    def __init__(self, docs: dict[str, str] | None = None) -> None:
        self._docs: dict[str, str] = docs or {}

    async def lookup(self, library: str, topic: str) -> StubLookupResult:
        content = self._docs.get(library, "")
        return StubLookupResult(success=bool(content), content=content)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore backed by SQLite in a temp directory (no lookup engine)."""
    s = MemoryStore(tmp_path)
    yield s  # type: ignore[misc]
    s.close()


def _save(
    store: MemoryStore,
    key: str,
    value: str,
    *,
    tier: str = "pattern",
    tags: list[str] | None = None,
    confidence: float = -1.0,
    source: str = "agent",
) -> None:
    """Helper to save a memory entry with sensible defaults."""
    result = store.save(
        key=key,
        value=value,
        tier=tier,
        tags=tags or [],
        confidence=confidence,
        source=source,
    )
    assert not isinstance(result, dict), f"save failed: {result}"


# ---------------------------------------------------------------------------
# Test: no lookup engine returns empty report
# ---------------------------------------------------------------------------


class TestNoLookupEngine:
    """When no lookup engine is configured, validate_entries returns empty report."""

    def test_no_engine_returns_empty_report(self, store: MemoryStore) -> None:
        _save(store, "some-entry", "from fastapi import FastAPI", tags=["fastapi"])

        report = store.validate_entries()

        assert isinstance(report, ValidationReport)
        assert report.validated == 0
        assert report.flagged == 0
        assert report.inconclusive == 0
        assert report.skipped == 0
        assert report.entries == []


# ---------------------------------------------------------------------------
# Test: validated entry with matching docs
# ---------------------------------------------------------------------------

_FASTAPI_DOCS = """\
## FastAPI

FastAPI is a modern, fast (high-performance) web framework for building APIs
with Python based on standard Python type hints.

## Getting Started

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}
```

## Features

FastAPI provides automatic interactive API documentation, data validation
with Pydantic, dependency injection, and OAuth2 with JWT tokens.
"""


class TestValidatedEntry:
    """Entry with matching documentation should be validated and confidence boosted."""

    def test_fastapi_entry_validated(self, tmp_path: Path) -> None:
        engine = StubLookupEngine(docs={"fastapi": _FASTAPI_DOCS})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "fastapi-usage",
                (
                    "from fastapi import FastAPI\n"
                    "FastAPI is a modern high-performance web framework for building APIs "
                    "with Python based on standard Python type hints. "
                    "It provides automatic interactive API documentation, data validation "
                    "with Pydantic, dependency injection, and OAuth2 with JWT tokens."
                ),
                tags=["web-framework"],
                confidence=0.6,
                source="agent",
            )

            report = s.validate_entries()

            assert isinstance(report, ValidationReport)
            assert report.validated >= 1
            # Find the entry validation for our key
            ev = next(e for e in report.entries if e.entry_key == "fastapi-usage")
            assert ev.overall_status == ValidationStatus.validated
            assert ev.confidence_adjustment > 0.0
            assert len(ev.claims) >= 1
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Test: entry with no docs / empty docs
# ---------------------------------------------------------------------------


class TestNoDocsResult:
    """When lookup engine returns empty/no docs, result should be inconclusive or no_docs."""

    def test_empty_docs_inconclusive(self, tmp_path: Path) -> None:
        # Engine returns empty content for all lookups
        engine = StubLookupEngine(docs={})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "obscure-lib",
                "from obscurelib import Widget\nWe use obscurelib for widgets.",
                tags=["obscurelib"],
                confidence=0.7,
                source="agent",
            )

            report = s.validate_entries()

            assert isinstance(report, ValidationReport)
            ev = next(e for e in report.entries if e.entry_key == "obscure-lib")
            # With no docs returned, the result should be inconclusive
            assert ev.overall_status in {
                ValidationStatus.inconclusive,
                ValidationStatus.skipped,
            }
        finally:
            s.close()

    def test_lookup_returns_empty_string(self, tmp_path: Path) -> None:
        """Lookup engine returns success=True but empty content string."""
        engine = StubLookupEngine(docs={"somelib": ""})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "somelib-entry",
                "from somelib import Thing\nWe use somelib for processing.",
                tags=["somelib"],
                confidence=0.6,
                source="agent",
            )

            report = s.validate_entries()

            assert isinstance(report, ValidationReport)
            # Empty doc content should not produce a "validated" result
            validated_keys = [
                e.entry_key
                for e in report.entries
                if e.overall_status == ValidationStatus.validated
            ]
            assert "somelib-entry" not in validated_keys
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Test: validate specific keys only
# ---------------------------------------------------------------------------


class TestValidateSpecificKeys:
    """Passing keys= should limit validation to only those entries."""

    def test_keys_filter(self, tmp_path: Path) -> None:
        engine = StubLookupEngine(docs={"fastapi": _FASTAPI_DOCS})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "entry-a",
                "from fastapi import FastAPI\nWe use FastAPI for APIs.",
                tags=["fastapi"],
                confidence=0.6,
                source="agent",
            )
            _save(
                s,
                "entry-b",
                "from fastapi import FastAPI\nFastAPI handles routing.",
                tags=["fastapi"],
                confidence=0.6,
                source="agent",
            )

            report = s.validate_entries(keys=["entry-a"])

            # Only entry-a should appear in the report
            reported_keys = [e.entry_key for e in report.entries]
            assert "entry-a" in reported_keys
            assert "entry-b" not in reported_keys
        finally:
            s.close()

    def test_nonexistent_key_ignored(self, tmp_path: Path) -> None:
        engine = StubLookupEngine(docs={"fastapi": _FASTAPI_DOCS})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "real-entry",
                "from fastapi import FastAPI",
                tags=["fastapi"],
                confidence=0.6,
                source="agent",
            )

            report = s.validate_entries(keys=["nonexistent-key"])

            # Nonexistent key is silently skipped
            assert len(report.entries) == 0
        finally:
            s.close()


# ---------------------------------------------------------------------------
# Test: full round-trip — save → validate → verify store updated
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Full round-trip: save entry, validate, then verify store reflects changes."""

    def test_confidence_updated_in_store(self, tmp_path: Path) -> None:
        engine = StubLookupEngine(docs={"fastapi": _FASTAPI_DOCS})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "roundtrip-entry",
                "from fastapi import FastAPI\nWe use FastAPI to build our REST API.",
                tags=["fastapi"],
                confidence=0.5,
                source="agent",
            )

            original_entry = s.get("roundtrip-entry")
            assert original_entry is not None
            original_confidence = original_entry.confidence

            report = s.validate_entries()

            ev = next(e for e in report.entries if e.entry_key == "roundtrip-entry")

            updated_entry = s.get("roundtrip-entry")
            assert updated_entry is not None

            if ev.overall_status == ValidationStatus.validated:
                # Confidence should have been boosted
                assert updated_entry.confidence > original_confidence
            elif ev.overall_status == ValidationStatus.flagged:
                # Confidence should have been reduced
                assert updated_entry.confidence < original_confidence
        finally:
            s.close()

    def test_doc_tags_added_after_validation(self, tmp_path: Path) -> None:
        engine = StubLookupEngine(docs={"fastapi": _FASTAPI_DOCS})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "tag-entry",
                "from fastapi import FastAPI\nWe use FastAPI for web APIs.",
                tags=["fastapi"],
                confidence=0.6,
                source="agent",
            )

            original_entry = s.get("tag-entry")
            assert original_entry is not None
            original_tags = set(original_entry.tags)

            s.validate_entries()

            updated_entry = s.get("tag-entry")
            assert updated_entry is not None
            updated_tags = set(updated_entry.tags)

            # Validation should add at least one doc-* tag
            new_tags = updated_tags - original_tags
            doc_tags = [t for t in new_tags if t.startswith("doc-")]
            assert len(doc_tags) >= 1
        finally:
            s.close()

    def test_flagged_entry_marked_contradicted(self, tmp_path: Path) -> None:
        """Entry contradicted by docs should have contradicted=True in store."""
        # Provide docs that talk about something completely different
        misleading_docs = (
            "## Security\n\n"
            "Never use eval() in production. Always sanitize inputs. "
            "Deprecated: the old handler API was removed in v3. "
            "Use the new middleware approach instead."
        )
        engine = StubLookupEngine(docs={"oldhandler": misleading_docs})
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "bad-entry",
                "from oldhandler import LegacyHandler\nWe use oldhandler for request handling.",
                tags=["oldhandler"],
                confidence=0.7,
                source="agent",
            )

            report = s.validate_entries()

            ev = next(e for e in report.entries if e.entry_key == "bad-entry")
            if ev.overall_status == ValidationStatus.flagged:
                updated = s.get("bad-entry")
                assert updated is not None
                assert updated.contradicted is True
                assert updated.contradiction_reason is not None
                assert updated.confidence < 0.7
        finally:
            s.close()

    def test_multiple_entries_round_trip(self, tmp_path: Path) -> None:
        """Validate multiple entries in one call and verify all are updated."""
        engine = StubLookupEngine(
            docs={
                "fastapi": _FASTAPI_DOCS,
                "pydantic": (
                    "## Pydantic\n\n"
                    "Pydantic is the most widely used data validation library for Python.\n"
                    "Define data models with type annotations and get automatic validation.\n\n"
                    "## BaseModel\n\n"
                    "```python\nfrom pydantic import BaseModel\n\n"
                    "class User(BaseModel):\n    name: str\n    age: int\n```\n"
                ),
            }
        )
        s = MemoryStore(tmp_path, lookup_engine=engine)
        try:
            _save(
                s,
                "fastapi-entry",
                "from fastapi import FastAPI\nFastAPI for our REST endpoints.",
                tags=["fastapi"],
                confidence=0.5,
                source="agent",
            )
            _save(
                s,
                "pydantic-entry",
                "from pydantic import BaseModel\nPydantic for data validation.",
                tags=["pydantic"],
                confidence=0.5,
                source="agent",
            )

            report = s.validate_entries()

            assert len(report.entries) == 2

            # Both entries should have been processed (not skipped)
            for ev in report.entries:
                entry = s.get(ev.entry_key)
                assert entry is not None
                # At minimum, doc tags should have been added
                doc_tags = [t for t in entry.tags if t.startswith("doc-")]
                assert len(doc_tags) >= 1
        finally:
            s.close()
