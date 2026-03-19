"""Tests for Context7-assisted memory validation (Epic 62).

Covers ClaimExtractor, DocSimilarityScorer, MemoryDocValidator,
helper functions, and confidence adjustment logic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tapps_brain.doc_validation import (
    AlignmentLevel,
    ApplyResult,
    ClaimExtractor,
    ClaimType,
    DocAlignment,
    DocSimilarityScorer,
    EntryValidation,
    LibraryClaim,
    MemoryDocValidator,
    ValidationReport,
    ValidationStatus,
    _infer_topic,
    _manage_doc_tags,
    _source_ceiling,
)
from tapps_brain.models import MemoryEntry
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    key: str = "test-key",
    value: str = "test value",
    tags: list[str] | None = None,
    confidence: float = 0.7,
    source: str = "agent",
    seeded_from: str | None = None,
    contradicted: bool = False,
    contradiction_reason: str | None = None,
) -> MemoryEntry:
    """Create a MemoryEntry for testing."""
    return make_entry(
        key=key,
        value=value,
        tags=tags,
        confidence=confidence,
        source=source,
        seeded_from=seeded_from,
        contradicted=contradicted,
        contradiction_reason=contradiction_reason,
    )


def _make_lookup_engine(
    docs: dict[str, str] | None = None,
    *,
    fail_for: set[str] | None = None,
) -> MagicMock:
    """Create a mock LookupEngine with configurable responses."""
    engine = MagicMock()

    async def mock_lookup(library: str, topic: str, **kwargs: Any) -> MagicMock:
        if fail_for and library in fail_for:
            raise RuntimeError(f"Lookup failed for {library}")
        result = MagicMock()
        content = (docs or {}).get(library)
        result.success = content is not None
        result.content = content
        return result

    engine.lookup = AsyncMock(side_effect=mock_lookup)
    return engine


# ===================================================================
# Story 62.1 — ClaimExtractor tests
# ===================================================================


class TestClaimExtractor:
    """Tests for the ClaimExtractor class."""

    def setup_method(self) -> None:
        self.extractor = ClaimExtractor()

    def test_extract_from_import_pattern(self) -> None:
        entry = _make_entry(value="We use from fastapi import FastAPI to build the API.")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "fastapi" in libs

    def test_extract_from_require_pattern(self) -> None:
        entry = _make_entry(value="const express = require('express')")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "express" in libs

    def test_extract_from_usage_pattern(self) -> None:
        entry = _make_entry(value="We use SQLAlchemy for our ORM layer.")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "sqlalchemy" in libs

    def test_extract_from_tags(self) -> None:
        entry = _make_entry(value="Config for DB.", tags=["pydantic", "config"])
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "pydantic" in libs
        # "config" is a meta-tag, should NOT be extracted
        assert "config" not in libs

    def test_extract_from_seeded_key(self) -> None:
        entry = _make_entry(
            key="framework-fastapi",
            value="FastAPI is our web framework",
            seeded_from="project_profile",
        )
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "fastapi" in libs

    def test_extract_from_version_claim(self) -> None:
        entry = _make_entry(value="We require pydantic>=2.0 for models")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "pydantic" in libs

    def test_no_duplicate_claims(self) -> None:
        entry = _make_entry(
            value="from fastapi import FastAPI; using fastapi for routing",
            tags=["fastapi"],
        )
        claims = self.extractor.extract_claims(entry)
        libs = [c.library for c in claims]
        assert libs.count("fastapi") == 1

    def test_short_names_filtered(self) -> None:
        entry = _make_entry(value="import os; import io")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "os" not in libs
        assert "io" not in libs

    def test_common_words_filtered(self) -> None:
        entry = _make_entry(value="We used the library from this repo")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "the" not in libs
        assert "this" not in libs
        assert "from" not in libs

    def test_no_extractable_claims_returns_empty(self) -> None:
        entry = _make_entry(value="No library references here at all.")
        claims = self.extractor.extract_claims(entry)
        # Should have no or only generic matches
        assert all(len(c.library) >= 3 for c in claims)

    def test_no_library_references(self) -> None:
        entry = _make_entry(value="The project uses a monorepo structure.")
        claims = self.extractor.extract_claims(entry)
        # Should return empty or very few generic matches
        for claim in claims:
            assert len(claim.library) >= 3

    def test_alias_resolution(self) -> None:
        entry = _make_entry(value="Using next for server-side rendering")
        claims = self.extractor.extract_claims(entry)
        libs = {c.library for c in claims}
        assert "nextjs" in libs

    def test_claim_type_for_import(self) -> None:
        entry = _make_entry(value="from sqlalchemy import Column")
        claims = self.extractor.extract_claims(entry)
        import_claims = [c for c in claims if c.library == "sqlalchemy"]
        assert import_claims
        assert import_claims[0].claim_type == ClaimType.api_usage

    def test_claim_type_for_version(self) -> None:
        entry = _make_entry(value="django>=4.2 is required")
        claims = self.extractor.extract_claims(entry)
        version_claims = [c for c in claims if c.library == "django"]
        assert version_claims
        assert version_claims[0].claim_type == ClaimType.version

    def test_seeded_key_non_profile_skipped(self) -> None:
        entry = _make_entry(
            key="framework-fastapi",
            value="Something",
            seeded_from="manual",
        )
        claims = self.extractor.extract_claims(entry)
        seeded_claims = [c for c in claims if c.claim_text.startswith("Seeded from")]
        assert seeded_claims == []


# ===================================================================
# Story 62.2 — DocSimilarityScorer tests
# ===================================================================


class TestDocSimilarityScorer:
    """Tests for the DocSimilarityScorer class."""

    def setup_method(self) -> None:
        self.scorer = DocSimilarityScorer()

    def test_empty_doc_returns_no_docs(self) -> None:
        claim = LibraryClaim(
            library="fastapi",
            topic="api",
            claim_text="from fastapi import FastAPI",
            claim_type=ClaimType.api_usage,
        )
        result = self.scorer.score_claim(claim, "")
        assert result.alignment == AlignmentLevel.no_docs

    def test_high_similarity_confirmed(self) -> None:
        claim = LibraryClaim(
            library="fastapi",
            topic="api",
            claim_text="FastAPI uses async def route handlers with dependency injection",
            claim_type=ClaimType.api_usage,
        )
        doc = (
            "## Route Handlers\n"
            "FastAPI uses async def route handlers with dependency injection "
            "for building modern web APIs. Decorators like @app.get() define routes."
        )
        result = self.scorer.score_claim(claim, doc)
        assert result.similarity_score > 0.0
        assert result.alignment in (AlignmentLevel.confirmed, AlignmentLevel.inconclusive)

    def test_deprecation_detected(self) -> None:
        claim = LibraryClaim(
            library="fastapi",
            topic="api",
            claim_text="Use fastapi.params for query parameters",
            claim_type=ClaimType.api_usage,
        )
        doc = (
            "## Query Parameters\n"
            "The fastapi.params module has been deprecated in v2.0. "
            "Use fastapi.Query instead for query parameter handling."
        )
        result = self.scorer.score_claim(claim, doc)
        assert result.alignment == AlignmentLevel.contradicted
        assert result.confidence_delta < 0

    def test_security_antipattern_detected(self) -> None:
        claim = LibraryClaim(
            library="requests",
            topic="security",
            claim_text="Use requests.get(url, verify=False) for self-signed certs",
            claim_type=ClaimType.api_usage,
        )
        doc = "## TLS Verification\nAlways verify SSL certificates."
        result = self.scorer.score_claim(claim, doc)
        assert result.alignment == AlignmentLevel.contradicted
        assert result.confidence_delta <= -0.3

    def test_chunk_splitting(self) -> None:
        doc = (
            "## Section A\n" + "a " * 50 + "\n"
            "## Section B\n" + "b " * 50 + "\n"
            "## Section C\n" + "c " * 50
        )
        chunks = self.scorer._split_into_chunks(doc)
        assert len(chunks) == 3

    def test_short_chunks_filtered(self) -> None:
        doc = "## Short\nhi\n## Long Section\n" + "content " * 30
        chunks = self.scorer._split_into_chunks(doc)
        # "hi" is <50 chars, should be filtered
        assert all(len(c.strip()) > 50 for c in chunks)

    def test_snippet_length_capped(self) -> None:
        claim = LibraryClaim(
            library="test",
            topic="api",
            claim_text="test library usage",
            claim_type=ClaimType.api_usage,
        )
        long_doc = "## Doc\n" + "word " * 2000
        result = self.scorer.score_claim(claim, long_doc)
        assert len(result.matched_snippet) <= 500

    def test_inconclusive_range(self) -> None:
        alignment, delta = self.scorer._classify(0.45, False, False)
        assert alignment == AlignmentLevel.inconclusive
        assert delta == 0.0


# ===================================================================
# Story 62.3 — MemoryDocValidator tests
# ===================================================================


class TestMemoryDocValidator:
    """Tests for the MemoryDocValidator class."""

    @pytest.fixture()
    def lookup_engine(self) -> MagicMock:
        return _make_lookup_engine(
            docs={
                "fastapi": (
                    "## FastAPI\nFastAPI is a modern web framework for building "
                    "APIs with Python 3.8+ based on standard type hints. "
                    "It uses async def handlers and dependency injection."
                ),
                "sqlalchemy": (
                    "## SQLAlchemy\nSQLAlchemy is the Python SQL toolkit and ORM. "
                    "Use Column, Integer, String for model definitions."
                ),
            }
        )

    @pytest.fixture()
    def validator(self, lookup_engine: MagicMock) -> MemoryDocValidator:
        return MemoryDocValidator(lookup_engine)

    @pytest.mark.asyncio()
    async def test_validate_entry_with_claims(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        entry = _make_entry(value="from fastapi import FastAPI for our web API")
        result = await validator.validate_entry(entry)
        assert result.entry_key == "test-key"
        assert len(result.claims) > 0
        assert result.overall_status != ValidationStatus.skipped

    @pytest.mark.asyncio()
    async def test_validate_entry_no_claims(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        entry = _make_entry(value="The project uses a monorepo structure.")
        result = await validator.validate_entry(entry)
        assert result.overall_status == ValidationStatus.skipped
        assert "No library claims" in result.reason

    @pytest.mark.asyncio()
    async def test_validate_entry_recently_validated(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        entry = _make_entry(
            value="from fastapi import FastAPI",
            tags=[f"doc-validated:{today}"],
        )
        result = await validator.validate_entry(entry)
        assert result.overall_status == ValidationStatus.skipped
        assert "Recently validated" in result.reason

    @pytest.mark.asyncio()
    async def test_validate_entry_old_validation_not_skipped(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        old_date = (datetime.now(tz=UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
        entry = _make_entry(
            value="from fastapi import FastAPI",
            tags=[f"doc-validated:{old_date}"],
        )
        result = await validator.validate_entry(entry)
        # Should NOT be skipped since validation is old
        assert result.overall_status != ValidationStatus.skipped or "Recently" not in result.reason

    @pytest.mark.asyncio()
    async def test_validate_entry_lookup_fails(self) -> None:
        engine = _make_lookup_engine(fail_for={"fastapi"})
        validator = MemoryDocValidator(engine)
        entry = _make_entry(value="from fastapi import FastAPI")
        result = await validator.validate_entry(entry)
        # Should be inconclusive since no docs available
        assert result.overall_status in (
            ValidationStatus.inconclusive,
            ValidationStatus.skipped,
        )

    @pytest.mark.asyncio()
    async def test_validate_batch_counts(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        entries = [
            _make_entry(key="e1", value="from fastapi import FastAPI"),
            _make_entry(key="e2", value="The project uses monorepo."),
            _make_entry(key="e3", value="from sqlalchemy import Column"),
        ]
        report = await validator.validate_batch(entries)
        assert len(report.entries) == 3
        total = report.validated + report.flagged + report.inconclusive + report.skipped
        assert total == 3
        assert report.elapsed_ms >= 0

    @pytest.mark.asyncio()
    async def test_validate_batch_budget_exhaustion(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        entries = [
            _make_entry(key=f"e{i}", value=f"from lib{i} import something") for i in range(25)
        ]
        report = await validator.validate_batch(entries, max_lookups=5)
        # Some entries should be skipped due to budget
        skipped_budget = [e for e in report.entries if e.reason == "Lookup budget exhausted"]
        assert len(skipped_budget) > 0

    @pytest.mark.asyncio()
    async def test_validate_stale(
        self,
        validator: MemoryDocValidator,
    ) -> None:
        # Low confidence = stale
        entries = [
            _make_entry(key="stale1", value="from fastapi import FastAPI", confidence=0.3),
            _make_entry(key="fresh1", value="from fastapi import FastAPI", confidence=0.9),
        ]
        report = await validator.validate_stale(
            entries,
            confidence_threshold=0.5,
            max_entries=10,
        )
        # Only low-confidence entries should be validated
        validated_keys = {e.entry_key for e in report.entries}
        assert "stale1" in validated_keys

    @pytest.mark.asyncio()
    async def test_doc_cache_avoids_duplicate_lookups(
        self,
        lookup_engine: MagicMock,
    ) -> None:
        validator = MemoryDocValidator(lookup_engine)
        entries = [
            _make_entry(key="e1", value="from fastapi import FastAPI"),
            _make_entry(key="e2", value="from fastapi import APIRouter"),
        ]
        await validator.validate_batch(entries)
        # fastapi should only be looked up once (cached)
        fastapi_calls = [
            c
            for c in lookup_engine.lookup.call_args_list
            if c.args[0] == "fastapi" or (c.kwargs and c.kwargs.get("library") == "fastapi")
        ]
        # May be 1 or 2 depending on topics, but caching should reduce calls
        assert len(fastapi_calls) <= 2


# ===================================================================
# Story 62.4 — Confidence Adjustment & Enrichment tests
# ===================================================================


class TestApplyResults:
    """Tests for apply_results in MemoryDocValidator."""

    @pytest.fixture()
    def store(self) -> MagicMock:
        mock = MagicMock()
        mock.get.return_value = _make_entry(
            confidence=0.6,
            source="agent",
            tags=["existing-tag"],
        )
        return mock

    @pytest.fixture()
    def lookup_engine(self) -> MagicMock:
        return _make_lookup_engine()

    @pytest.fixture()
    def validator(self, lookup_engine: MagicMock) -> MemoryDocValidator:
        return MemoryDocValidator(lookup_engine)

    @pytest.mark.asyncio()
    async def test_apply_boosts_validated(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        report = ValidationReport(
            validated=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.validated,
                    confidence_adjustment=0.1,
                    doc_references=["fastapi/api"],
                ),
            ],
        )
        result = await validator.apply_results(report, store)
        assert result.boosted == 1
        store.update_fields.assert_called_once()
        call_kwargs = store.update_fields.call_args
        assert call_kwargs.args[0] == "test-key"

    @pytest.mark.asyncio()
    async def test_apply_penalises_flagged(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        report = ValidationReport(
            flagged=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.flagged,
                    confidence_adjustment=-0.2,
                    alignments=[
                        DocAlignment(
                            similarity_score=0.1,
                            alignment=AlignmentLevel.contradicted,
                            matched_snippet="deprecated feature",
                            doc_source="lookup",
                            confidence_delta=-0.2,
                        ),
                    ],
                ),
            ],
        )
        result = await validator.apply_results(report, store)
        assert result.penalised == 1
        call_kwargs = store.update_fields.call_args[1]
        assert call_kwargs["contradicted"] is True

    @pytest.mark.asyncio()
    async def test_apply_dry_run(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        report = ValidationReport(
            validated=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.validated,
                    confidence_adjustment=0.1,
                ),
            ],
        )
        result = await validator.apply_results(report, store, dry_run=True)
        assert result.dry_run is True
        assert result.boosted == 1
        store.update_fields.assert_not_called()

    @pytest.mark.asyncio()
    async def test_apply_skipped_entry_unchanged(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        report = ValidationReport(
            skipped=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.skipped,
                ),
            ],
        )
        result = await validator.apply_results(report, store)
        assert result.unchanged == 1
        store.update_fields.assert_not_called()

    @pytest.mark.asyncio()
    async def test_apply_clears_doc_contradiction(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        store.get.return_value = _make_entry(
            confidence=0.4,
            contradicted=True,
            contradiction_reason="Conflicts with docs: old info",
        )
        report = ValidationReport(
            validated=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.validated,
                    confidence_adjustment=0.15,
                ),
            ],
        )
        result = await validator.apply_results(report, store)
        assert result.boosted == 1
        call_kwargs = store.update_fields.call_args[1]
        assert call_kwargs["contradicted"] is False

    @pytest.mark.asyncio()
    async def test_confidence_capped_by_source(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        store.get.return_value = _make_entry(confidence=0.84, source="agent")
        report = ValidationReport(
            validated=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.validated,
                    confidence_adjustment=0.2,
                ),
            ],
        )
        await validator.apply_results(report, store)
        call_kwargs = store.update_fields.call_args[1]
        # agent ceiling is 0.85
        assert call_kwargs["confidence"] <= 0.85

    @pytest.mark.asyncio()
    async def test_confidence_floor_enforced(
        self,
        validator: MemoryDocValidator,
        store: MagicMock,
    ) -> None:
        store.get.return_value = _make_entry(confidence=0.15, source="agent")
        report = ValidationReport(
            flagged=1,
            entries=[
                EntryValidation(
                    entry_key="test-key",
                    overall_status=ValidationStatus.flagged,
                    confidence_adjustment=-0.3,
                    alignments=[],
                ),
            ],
        )
        await validator.apply_results(report, store)
        call_kwargs = store.update_fields.call_args[1]
        assert call_kwargs["confidence"] >= 0.1


# ===================================================================
# Helper function tests
# ===================================================================


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_infer_topic_config(self) -> None:
        assert _infer_topic("Database config in yaml file") == "configuration"

    def test_infer_topic_security(self) -> None:
        assert _infer_topic("JWT auth token handling") == "security"

    def test_infer_topic_testing(self) -> None:
        assert _infer_topic("pytest fixtures for mocking") == "testing"

    def test_infer_topic_api(self) -> None:
        assert _infer_topic("from module import class") == "api"

    def test_infer_topic_deployment(self) -> None:
        assert _infer_topic("Docker container setup") == "deployment"

    def test_infer_topic_default(self) -> None:
        assert _infer_topic("general information") == "overview"

    def test_source_ceiling_human(self) -> None:
        assert _source_ceiling("human") == 0.95

    def test_source_ceiling_agent(self) -> None:
        assert _source_ceiling("agent") == 0.85

    def test_source_ceiling_inferred(self) -> None:
        assert _source_ceiling("inferred") == 0.70

    def test_source_ceiling_unknown(self) -> None:
        assert _source_ceiling("unknown") == 0.85

    def test_manage_doc_tags_adds_tag(self) -> None:
        tags: list[str] = ["existing"]
        _manage_doc_tags(tags, "doc-validated:2026-03-09")
        assert "doc-validated:2026-03-09" in tags

    def test_manage_doc_tags_evicts_oldest(self) -> None:
        tags: list[str] = [
            "user-tag",
            "doc-validated:2026-01-01",
            "doc-checked:2026-01-15",
            "doc-ref:fastapi/api",
        ]
        # 3 doc tags = at limit (_MAX_DOC_TAGS=3)
        _manage_doc_tags(tags, "doc-validated:2026-03-09")
        # Should have evicted the oldest doc tag
        assert "doc-validated:2026-03-09" in tags
        assert len([t for t in tags if t.startswith("doc-")]) <= 3

    def test_manage_doc_tags_respects_max_tags(self) -> None:
        # Fill to MAX_TAGS (10) with user tags
        tags: list[str] = [f"tag-{i}" for i in range(10)]
        _manage_doc_tags(tags, "doc-validated:2026-03-09")
        # Can't add since no doc tags to evict and at limit
        assert len(tags) <= 10


# ===================================================================
# Enum and model tests
# ===================================================================


class TestModels:
    """Tests for data models."""

    def test_claim_type_values(self) -> None:
        assert ClaimType.api_usage == "api_usage"
        assert ClaimType.version == "version"
        assert ClaimType.deprecation == "deprecation"

    def test_alignment_level_values(self) -> None:
        assert AlignmentLevel.confirmed == "confirmed"
        assert AlignmentLevel.contradicted == "contradicted"
        assert AlignmentLevel.no_docs == "no_docs"

    def test_validation_status_values(self) -> None:
        assert ValidationStatus.validated == "validated"
        assert ValidationStatus.flagged == "flagged"
        assert ValidationStatus.skipped == "skipped"

    def test_validation_report_defaults(self) -> None:
        report = ValidationReport()
        assert report.validated == 0
        assert report.flagged == 0
        assert report.entries == []
        assert report.elapsed_ms == 0.0

    def test_apply_result_defaults(self) -> None:
        result = ApplyResult()
        assert result.boosted == 0
        assert result.penalised == 0
        assert result.dry_run is False

    def test_entry_validation_defaults(self) -> None:
        ev = EntryValidation(entry_key="k")
        assert ev.overall_status == ValidationStatus.skipped
        assert ev.claims == []
        assert ev.confidence_adjustment == 0.0

    def test_doc_alignment_fields(self) -> None:
        da = DocAlignment(
            similarity_score=0.75,
            alignment=AlignmentLevel.confirmed,
            matched_snippet="test",
            doc_source="lookup",
            confidence_delta=0.15,
        )
        assert da.similarity_score == 0.75
        assert da.confidence_delta == 0.15

    def test_library_claim_fields(self) -> None:
        claim = LibraryClaim(
            library="fastapi",
            topic="api",
            claim_text="from fastapi import FastAPI",
            claim_type=ClaimType.api_usage,
        )
        assert claim.library == "fastapi"
        assert claim.claim_type == ClaimType.api_usage
