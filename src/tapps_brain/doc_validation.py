"""Context7-assisted memory validation and enrichment (Epic 62).

Validates memory entries against authoritative documentation retrieved
via the knowledge lookup engine. Extracts library/framework claims from
memory values, scores them against retrieved docs using TF-IDF cosine
similarity, and adjusts confidence accordingly. All detection is
deterministic -- no LLM calls.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from tapps_brain.bm25 import preprocess
from tapps_brain.similarity import _term_frequency, cosine_similarity

if TYPE_CHECKING:
    from tapps_brain._protocols import LookupEngineLike
    from tapps_brain.models import MemoryEntry
    from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum library name length to avoid false positives on short words
_MIN_LIB_NAME_LENGTH = 3

# Max characters to include in matched snippet
_MAX_SNIPPET_LENGTH = 500

# Confidence adjustment bounds
_MAX_BOOST = 0.2
_MAX_PENALTY = 0.3
_CONFIDENCE_FLOOR = 0.1

# Similarity thresholds for alignment classification
_CONFIRMED_THRESHOLD = 0.6
_CONTRADICTED_THRESHOLD = 0.3

# Min chunk length for doc splitting
_MIN_CHUNK_LENGTH = 50

# Max doc-ref tags to keep per entry
_MAX_DOC_REF_TAGS = 2

# Tag used to track validation date (avoids re-validation within interval)
_VALIDATION_TAG_PREFIX = "doc-validated:"
_CONTRADICTION_TAG_PREFIX = "doc-contradicted:"
_CHECKED_TAG_PREFIX = "doc-checked:"
_DOC_REF_TAG_PREFIX = "doc-ref:"

# Max doc tags before we start evicting old ones
_MAX_DOC_TAGS = 3

# Patterns for extracting library claims from memory values
_IMPORT_PATTERNS = [
    r"(?:from|import)\s+(\w[\w.]*)",  # Python imports
    r"require\(['\"](\w[\w./-]*?)['\"]\)",  # JS require
    r"import\s+['\"](\w[\w./-]*?)['\"]",  # JS/TS import
]

_USAGE_PATTERNS = [
    r"(?:we\s+)?use[sd]?\s+(\w[\w.-]*)",
    r"using\s+(\w[\w.-]*)",
    r"built\s+(?:with|on)\s+(\w[\w.-]*)",
    r"(\w[\w.-]*)\.\w+\(",  # X.method() calls
]

_VERSION_PATTERNS = [
    r"(\w[\w.-]*)\s*[><=!~]+\s*[\d.]+",  # X>=1.0
    r"(\w[\w.-]*)\s+v\d+",  # X v2
    r"(\w[\w.-]*)\s+version\s+[\d.]+",  # X version 3.x
]

# Deprecation markers that indicate a doc contradiction
_DEPRECATION_MARKERS = [
    "deprecated",
    "removed in",
    "no longer supported",
    "will be removed",
    "use instead",
    "replaced by",
    "obsolete",
]

# Security anti-patterns that docs may flag
_SECURITY_ANTIPATTERNS = [
    r"verify\s*=\s*False",
    r"eval\s*\(",
    r"exec\s*\(",
    r"shell\s*=\s*True",
    r"password\s*=\s*['\"]",
    r"SECRET.*=.*['\"]",
]

# Well-known Python/JS/Go/Rust packages (for normalisation)
_PACKAGE_ALIASES: dict[str, str] = {
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "sqlalchemy": "sqlalchemy",
    "requests": "requests",
    "httpx": "httpx",
    "pydantic": "pydantic",
    "pytest": "pytest",
    "numpy": "numpy",
    "pandas": "pandas",
    "react": "react",
    "next": "nextjs",
    "express": "express",
    "gin": "gin-gonic",
    "tokio": "tokio",
    "serde": "serde",
    "axum": "axum",
}


# ---------------------------------------------------------------------------
# Enums and models
# ---------------------------------------------------------------------------


class ClaimType(StrEnum):
    """Type of library claim extracted from a memory value."""

    api_usage = "api_usage"
    version = "version"
    config = "config"
    pattern = "pattern"
    deprecation = "deprecation"


class AlignmentLevel(StrEnum):
    """How well a memory claim aligns with retrieved documentation."""

    confirmed = "confirmed"
    contradicted = "contradicted"
    inconclusive = "inconclusive"
    no_docs = "no_docs"


class ValidationStatus(StrEnum):
    """Overall validation status of a memory entry."""

    validated = "validated"
    flagged = "flagged"
    inconclusive = "inconclusive"
    skipped = "skipped"


@dataclass
class LibraryClaim:
    """A library/framework reference extracted from a memory value."""

    library: str
    topic: str
    claim_text: str
    claim_type: ClaimType


@dataclass
class DocAlignment:
    """Result of scoring a memory claim against documentation."""

    similarity_score: float
    alignment: AlignmentLevel
    matched_snippet: str
    doc_source: str
    confidence_delta: float


@dataclass
class EntryValidation:
    """Validation result for a single memory entry."""

    entry_key: str
    claims: list[LibraryClaim] = field(default_factory=list)
    alignments: list[DocAlignment] = field(default_factory=list)
    overall_status: ValidationStatus = ValidationStatus.skipped
    confidence_adjustment: float = 0.0
    doc_references: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class ValidationReport:
    """Aggregated validation results for a batch of entries."""

    validated: int = 0
    flagged: int = 0
    inconclusive: int = 0
    skipped: int = 0
    no_docs: int = 0
    entries: list[EntryValidation] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class ApplyResult:
    """Result of applying validation back to the memory store."""

    boosted: int = 0
    penalised: int = 0
    unchanged: int = 0
    tags_added: int = 0
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Story 62.1 — Claim Extractor
# ---------------------------------------------------------------------------


class ClaimExtractor:
    """Extract library/framework references from memory entry values.

    Uses five strategies in priority order:
    1. Tag-based (library-related tags)
    2. Import patterns (Python/JS/TS imports)
    3. Usage patterns ("we use X", "X.method()")
    4. Seeded memory shortcut (key-based for auto-seeded entries)
    5. Version claim patterns (X>=1.0, X v2)
    """

    def extract_claims(self, entry: MemoryEntry) -> list[LibraryClaim]:
        """Extract all library claims from a memory entry.

        Returns an empty list (not an error) for entries with no detectable
        library references.
        """
        claims: list[LibraryClaim] = []
        seen_libs: set[str] = set()

        # Strategy 1: Tag-based
        claims.extend(self._from_tags(entry, seen_libs))

        # Strategy 2: Import patterns
        claims.extend(self._from_imports(entry.value, seen_libs))

        # Strategy 3: Usage patterns
        claims.extend(self._from_usage(entry.value, seen_libs))

        # Strategy 4: Seeded memory key
        claims.extend(self._from_seeded_key(entry, seen_libs))

        # Strategy 5: Version claims
        claims.extend(self._from_versions(entry.value, seen_libs))

        return claims

    def _normalise_lib(self, name: str) -> str:
        """Normalise a library name to lowercase with alias resolution."""
        clean = name.lower().strip().replace("_", "-")
        return _PACKAGE_ALIASES.get(clean, clean)

    def _add_if_valid(
        self,
        name: str,
        claim_text: str,
        claim_type: ClaimType,
        topic: str,
        seen: set[str],
        out: list[LibraryClaim],
    ) -> None:
        """Add a claim if the library name is valid and not yet seen."""
        lib = self._normalise_lib(name)
        if len(lib) < _MIN_LIB_NAME_LENGTH:
            return
        if lib in seen:
            return
        # Filter out common English words that look like library names
        if lib in {"the", "that", "this", "with", "from", "into", "have", "been"}:
            return
        seen.add(lib)
        out.append(
            LibraryClaim(
                library=lib,
                topic=topic,
                claim_text=claim_text[:200],
                claim_type=claim_type,
            )
        )

    def _from_tags(self, entry: MemoryEntry, seen: set[str]) -> list[LibraryClaim]:
        """Strategy 1: Extract library names from tags."""
        claims: list[LibraryClaim] = []
        # Tags that are themselves library names (not meta-tags)
        meta_tags = {
            "library",
            "framework",
            "database",
            "orm",
            "dependency",
            "auto-seeded",
            "language",
            "test",
            "testing",
            "test-framework",
            "package-manager",
            "build-tool",
            "tooling",
            "file",
            "path",
            "module",
            "branch",
            "feature-branch",
            "architecture",
            "pattern",
            "context",
            "security",
            "config",
            "api",
        }
        for tag in entry.tags:
            tag_lower = tag.lower()
            if tag_lower not in meta_tags:
                self._add_if_valid(
                    tag_lower,
                    f"Tagged with '{tag}'",
                    ClaimType.pattern,
                    _infer_topic(entry.value),
                    seen,
                    claims,
                )
        return claims

    def _from_imports(self, value: str, seen: set[str]) -> list[LibraryClaim]:
        """Strategy 2: Extract library names from import statements."""
        claims: list[LibraryClaim] = []
        for pattern in _IMPORT_PATTERNS:
            for match in re.finditer(pattern, value):
                raw = match.group(1)
                # Take the top-level package name (e.g., "fastapi" from "fastapi.routing")
                top_level = raw.split(".")[0].split("/")[0]
                self._add_if_valid(
                    top_level,
                    match.group(0)[:200],
                    ClaimType.api_usage,
                    "api",
                    seen,
                    claims,
                )
        return claims

    def _from_usage(self, value: str, seen: set[str]) -> list[LibraryClaim]:
        """Strategy 3: Extract library names from usage patterns."""
        claims: list[LibraryClaim] = []
        for pattern in _USAGE_PATTERNS:
            for match in re.finditer(pattern, value, re.IGNORECASE):
                raw = match.group(1)
                topic = _infer_topic(value)
                self._add_if_valid(
                    raw,
                    match.group(0)[:200],
                    ClaimType.pattern,
                    topic,
                    seen,
                    claims,
                )
        return claims

    def _from_seeded_key(self, entry: MemoryEntry, seen: set[str]) -> list[LibraryClaim]:
        """Strategy 4: Extract library name from seeded memory keys."""
        claims: list[LibraryClaim] = []
        if entry.seeded_from != "project_profile":
            return claims

        # Keys like "framework-fastapi", "library-sqlalchemy"
        _expected_parts = 2
        key_parts = entry.key.split("-", 1)
        if len(key_parts) == _expected_parts and key_parts[0] in (
            "framework",
            "library",
            "database",
            "orm",
        ):
            self._add_if_valid(
                key_parts[1],
                f"Seeded from project profile: {entry.key}",
                ClaimType.pattern,
                "overview",
                seen,
                claims,
            )
        return claims

    def _from_versions(self, value: str, seen: set[str]) -> list[LibraryClaim]:
        """Strategy 5: Extract library names from version claims."""
        claims: list[LibraryClaim] = []
        for pattern in _VERSION_PATTERNS:
            for match in re.finditer(pattern, value, re.IGNORECASE):
                raw = match.group(1)
                self._add_if_valid(
                    raw,
                    match.group(0)[:200],
                    ClaimType.version,
                    "overview",
                    seen,
                    claims,
                )
        return claims


# ---------------------------------------------------------------------------
# Story 62.2 — Documentation Similarity Scorer
# ---------------------------------------------------------------------------


class DocSimilarityScorer:
    """Score how well a memory claim aligns with retrieved documentation.

    Uses TF-IDF cosine similarity with chunk-level matching against
    ##-delimited doc sections, plus heuristic checks for deprecation,
    version mismatch, and security anti-patterns.
    """

    def score_claim(self, claim: LibraryClaim, doc_content: str) -> DocAlignment:
        """Score a memory claim against retrieved documentation.

        Args:
            claim: The library claim to validate.
            doc_content: Raw documentation content (markdown).

        Returns:
            DocAlignment with similarity score, alignment level, and
            confidence delta.
        """
        if not doc_content.strip():
            return DocAlignment(
                similarity_score=0.0,
                alignment=AlignmentLevel.no_docs,
                matched_snippet="",
                doc_source="empty",
                confidence_delta=0.0,
            )

        # Split docs into chunks on ## headers
        chunks = self._split_into_chunks(doc_content)
        if not chunks:
            chunks = [doc_content[:2000]]

        # Find best matching chunk via TF-IDF cosine similarity
        best_score = 0.0
        best_chunk = ""
        claim_terms = preprocess(claim.claim_text)
        claim_tf = _term_frequency(claim_terms)

        for chunk in chunks:
            chunk_terms = preprocess(chunk)
            chunk_tf = _term_frequency(chunk_terms)
            score = cosine_similarity(claim_tf, chunk_tf)
            if score > best_score:
                best_score = score
                best_chunk = chunk

        # Check for heuristic signals
        deprecation_hit = self._check_deprecation(claim.claim_text, doc_content)
        security_hit = self._check_security_antipattern(claim.claim_text)

        # Determine alignment and confidence delta
        alignment, delta = self._classify(
            best_score,
            deprecation_hit,
            security_hit,
        )

        snippet = best_chunk[:_MAX_SNIPPET_LENGTH] if best_chunk else ""

        return DocAlignment(
            similarity_score=round(best_score, 4),
            alignment=alignment,
            matched_snippet=snippet,
            doc_source="lookup",
            confidence_delta=round(delta, 3),
        )

    def _split_into_chunks(self, content: str) -> list[str]:
        """Split markdown content into sections on ## headers."""
        chunks: list[str] = []
        current: list[str] = []
        for line in content.splitlines():
            if line.startswith("## ") and current:
                chunks.append("\n".join(current))
                current = []
            current.append(line)
        if current:
            chunks.append("\n".join(current))
        # Filter out very short chunks
        return [c for c in chunks if len(c.strip()) > _MIN_CHUNK_LENGTH]

    def _check_deprecation(self, claim_text: str, doc_content: str) -> bool:
        """Check if docs indicate the claimed feature is deprecated."""
        claim_lower = claim_text.lower()
        doc_lower = doc_content.lower()

        # Look for deprecation markers near the claim terms
        claim_terms = set(preprocess(claim_lower))
        for marker in _DEPRECATION_MARKERS:
            if marker in doc_lower:
                # Check if deprecation is near any claim terms
                marker_pos = doc_lower.find(marker)
                nearby = doc_lower[max(0, marker_pos - 200) : marker_pos + 200]
                nearby_terms = set(preprocess(nearby))
                if claim_terms & nearby_terms:
                    return True
        return False

    def _check_security_antipattern(self, claim_text: str) -> bool:
        """Check if the memory claim recommends a known security anti-pattern."""
        for pattern in _SECURITY_ANTIPATTERNS:
            if re.search(pattern, claim_text, re.IGNORECASE):
                return True
        return False

    def _classify(
        self,
        similarity: float,
        deprecation_hit: bool,
        security_hit: bool,
    ) -> tuple[AlignmentLevel, float]:
        """Classify alignment and compute confidence delta.

        Returns:
            Tuple of (alignment_level, confidence_delta).
        """
        # Heuristic overrides
        if deprecation_hit:
            return AlignmentLevel.contradicted, -0.2
        if security_hit:
            return AlignmentLevel.contradicted, -0.3

        # Similarity-based classification
        if similarity >= _CONFIRMED_THRESHOLD:
            # Scale boost: 0.1 at threshold, 0.2 at 1.0
            boost = 0.1 + (similarity - _CONFIRMED_THRESHOLD) * (0.1 / (1.0 - _CONFIRMED_THRESHOLD))
            return AlignmentLevel.confirmed, min(boost, _MAX_BOOST)

        if similarity < _CONTRADICTED_THRESHOLD:
            # Low vocabulary overlap means the docs are likely irrelevant to the claim,
            # not that they contradict it. Returning inconclusive avoids false penalties
            # when docs for a different topic happen to be retrieved.
            return AlignmentLevel.inconclusive, 0.0

        return AlignmentLevel.inconclusive, 0.0


# ---------------------------------------------------------------------------
# Story 62.3 — Validation Engine
# ---------------------------------------------------------------------------


class MemoryDocValidator:
    """Orchestrate claim extraction, doc lookup, and similarity scoring.

    Validates memory entries against authoritative documentation and
    produces validation reports with per-entry results.
    """

    def __init__(
        self,
        lookup_engine: LookupEngineLike,
        *,
        revalidation_interval_days: int = 7,
    ) -> None:
        self._lookup = lookup_engine
        self._extractor = ClaimExtractor()
        self._scorer = DocSimilarityScorer()
        self._revalidation_interval_days = revalidation_interval_days
        self._doc_cache: dict[str, str | None] = {}

    async def validate_entry(self, entry: MemoryEntry) -> EntryValidation:
        """Validate a single memory entry against documentation."""
        result = EntryValidation(entry_key=entry.key)

        # Skip if recently validated
        if self._recently_validated(entry):
            result.overall_status = ValidationStatus.skipped
            result.reason = "Recently validated"
            return result

        # Extract claims
        claims = self._extractor.extract_claims(entry)
        result.claims = claims

        if not claims:
            result.overall_status = ValidationStatus.skipped
            result.reason = "No library claims found"
            return result

        # Validate each claim against docs
        has_confirmed = False
        has_contradicted = False
        has_no_docs = False
        total_delta = 0.0

        for claim in claims:
            doc_content = await self._lookup_doc(claim.library, claim.topic)

            if doc_content is None:
                alignment = DocAlignment(
                    similarity_score=0.0,
                    alignment=AlignmentLevel.no_docs,
                    matched_snippet="",
                    doc_source="lookup_failed",
                    confidence_delta=0.0,
                )
                has_no_docs = True
            else:
                alignment = self._scorer.score_claim(claim, doc_content)
                if alignment.alignment == AlignmentLevel.confirmed:
                    has_confirmed = True
                    result.doc_references.append(f"{claim.library}/{claim.topic}")
                elif alignment.alignment == AlignmentLevel.contradicted:
                    has_contradicted = True

            result.alignments.append(alignment)
            total_delta += alignment.confidence_delta

        # Determine overall status
        if has_contradicted:
            result.overall_status = ValidationStatus.flagged
            result.reason = "Documentation contradicts one or more claims"
        elif has_confirmed:
            result.overall_status = ValidationStatus.validated
            result.reason = "Documentation confirms claims"
        elif has_no_docs and not has_confirmed:
            result.overall_status = ValidationStatus.inconclusive
            result.reason = "No documentation found for claims"
        else:
            result.overall_status = ValidationStatus.inconclusive
            result.reason = "Similarity scores inconclusive"

        # Average the delta across claims to avoid outsized adjustments
        if claims:
            result.confidence_adjustment = round(total_delta / len(claims), 3)

        return result

    async def validate_batch(
        self,
        entries: list[MemoryEntry],
        *,
        max_lookups: int = 20,
    ) -> ValidationReport:
        """Validate multiple entries with rate-limited doc lookups.

        Args:
            entries: Memory entries to validate.
            max_lookups: Maximum unique library+topic doc lookups.

        Returns:
            ValidationReport with counts and per-entry details.
        """
        start = time.monotonic()
        report = ValidationReport()
        self._doc_cache.clear()
        lookup_count = 0

        for entry in entries:
            # Fast-path: skip recently validated entries without consuming lookup budget.
            # validate_entry() would skip them too, but only after budget accounting.
            if self._recently_validated(entry):
                ev = EntryValidation(
                    entry_key=entry.key,
                    overall_status=ValidationStatus.skipped,
                    reason="Recently validated",
                )
                report.entries.append(ev)
                report.skipped += 1
                continue

            # Check lookup budget
            claims = self._extractor.extract_claims(entry)
            new_lookups = sum(1 for c in claims if f"{c.library}:{c.topic}" not in self._doc_cache)
            if lookup_count + new_lookups > max_lookups:
                ev = EntryValidation(
                    entry_key=entry.key,
                    overall_status=ValidationStatus.skipped,
                    reason="Lookup budget exhausted",
                )
                report.entries.append(ev)
                report.skipped += 1
                continue

            ev = await self.validate_entry(entry)
            lookup_count += new_lookups
            report.entries.append(ev)

            if ev.overall_status == ValidationStatus.validated:
                report.validated += 1
            elif ev.overall_status == ValidationStatus.flagged:
                report.flagged += 1
            elif ev.overall_status == ValidationStatus.inconclusive:
                report.inconclusive += 1
            else:
                report.skipped += 1

        report.elapsed_ms = round((time.monotonic() - start) * 1000, 1)
        return report

    async def validate_stale(
        self,
        entries: list[MemoryEntry],
        *,
        confidence_threshold: float = 0.5,
        max_entries: int = 10,
    ) -> ValidationReport:
        """Validate entries whose effective confidence is below threshold.

        Entries are sorted by confidence ascending (stalest first) and
        capped at max_entries.
        """
        from tapps_brain.decay import DecayConfig, calculate_decayed_confidence

        config = DecayConfig()
        stale = [
            e
            for e in entries
            if calculate_decayed_confidence(e, config) < confidence_threshold and not e.contradicted
        ]
        stale.sort(key=lambda e: calculate_decayed_confidence(e, config))
        return await self.validate_batch(stale[:max_entries])

    async def apply_results(
        self,
        report: ValidationReport,
        store: MemoryStore,
        *,
        dry_run: bool = False,
    ) -> ApplyResult:
        """Apply validation results back to the memory store.

        Args:
            report: Validation report to apply.
            store: Memory store to update.
            dry_run: If True, return what would change without mutating.

        Returns:
            ApplyResult with counts of changes made.
        """
        result = ApplyResult(dry_run=dry_run)
        now_tag = datetime.now(tz=UTC).strftime("%Y-%m-%d")

        for ev in report.entries:
            if ev.overall_status == ValidationStatus.skipped:
                result.unchanged += 1
                continue

            entry = store.get(ev.entry_key)
            if entry is None:
                result.unchanged += 1
                continue

            updates: dict[str, object] = {}
            new_tags = list(entry.tags)

            if ev.overall_status == ValidationStatus.validated:
                # Boost confidence
                new_conf = min(
                    entry.confidence + ev.confidence_adjustment,
                    _source_ceiling(entry.source),
                )
                new_conf = max(new_conf, _CONFIDENCE_FLOOR)
                updates["confidence"] = round(new_conf, 3)

                # Add doc-validated tag
                _manage_doc_tags(new_tags, f"{_VALIDATION_TAG_PREFIX}{now_tag}")

                # Add doc-ref tags
                for ref in ev.doc_references[:_MAX_DOC_REF_TAGS]:
                    _manage_doc_tags(new_tags, f"{_DOC_REF_TAG_PREFIX}{ref}")

                # Clear contradiction if previously set by validation
                reason = entry.contradiction_reason or ""
                if entry.contradicted and "docs" in reason.lower():
                    updates["contradicted"] = False
                    updates["contradiction_reason"] = None

                result.boosted += 1

            elif ev.overall_status == ValidationStatus.flagged:
                # Reduce confidence
                new_conf = max(
                    entry.confidence + ev.confidence_adjustment,  # delta is negative
                    _CONFIDENCE_FLOOR,
                )
                updates["confidence"] = round(new_conf, 3)
                updates["contradicted"] = True

                # Build reason from alignments
                snippets = [
                    a.matched_snippet[:100]
                    for a in ev.alignments
                    if a.alignment == AlignmentLevel.contradicted and a.matched_snippet
                ]
                snippet_text = snippets[0] if snippets else "content mismatch"
                updates["contradiction_reason"] = f"Conflicts with docs: {snippet_text}"

                _manage_doc_tags(new_tags, f"{_CONTRADICTION_TAG_PREFIX}{now_tag}")
                result.penalised += 1

            elif ev.overall_status == ValidationStatus.inconclusive:
                _manage_doc_tags(new_tags, f"{_CHECKED_TAG_PREFIX}{now_tag}")
                result.unchanged += 1
            else:
                # Unknown status — defensive guard; do not update store.
                result.unchanged += 1
                continue  # skip the tag/field update below

            # Count new tags
            old_tag_count = len(entry.tags)
            updates["tags"] = new_tags
            result.tags_added += max(0, len(new_tags) - old_tag_count)

            if not dry_run:
                store.update_fields(ev.entry_key, **updates)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _lookup_doc(self, library: str, topic: str) -> str | None:
        """Look up documentation with caching across the batch."""
        cache_key = f"{library}:{topic}"
        if cache_key in self._doc_cache:
            return self._doc_cache[cache_key]

        try:
            result = await self._lookup.lookup(library, topic)
            content = result.content if result.success else None
        except Exception:
            logger.warning("doc_validation_lookup_failed", library=library, topic=topic, exc_info=True)
            content = None

        self._doc_cache[cache_key] = content
        return content

    def _recently_validated(self, entry: MemoryEntry) -> bool:
        """Check if entry was validated within the revalidation interval."""
        for tag in entry.tags:
            for prefix in (_VALIDATION_TAG_PREFIX, _CONTRADICTION_TAG_PREFIX, _CHECKED_TAG_PREFIX):
                if tag.startswith(prefix):
                    date_str = tag[len(prefix) :]
                    try:
                        validated_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                        days_since = (datetime.now(tz=UTC) - validated_date).days
                        if days_since < self._revalidation_interval_days:
                            return True
                    except ValueError:
                        continue
        return False


# ---------------------------------------------------------------------------
# Topic inference helper (shared by ClaimExtractor)
# ---------------------------------------------------------------------------


def _infer_topic(value: str) -> str:
    """Infer a documentation topic from memory value text."""
    lower = value.lower()
    if any(kw in lower for kw in ("config", "setting", "environment", "env", "yaml", "toml")):
        return "configuration"
    if any(kw in lower for kw in ("security", "auth", "token", "jwt", "oauth", "ssl", "tls")):
        return "security"
    if any(kw in lower for kw in ("test", "mock", "fixture", "assert", "pytest", "jest")):
        return "testing"
    if any(kw in lower for kw in ("import", "from", "require", "module")):
        return "api"
    if any(kw in lower for kw in ("deploy", "docker", "kubernetes", "ci", "cd")):
        return "deployment"
    return "overview"


# ---------------------------------------------------------------------------
# Tag management helpers (Story 62.4)
# ---------------------------------------------------------------------------


def _manage_doc_tags(tags: list[str], new_tag: str) -> None:
    """Add a doc-* tag, evicting the oldest doc tag if at limit.

    Ensures total tags never exceed MAX_TAGS (10) and doc tags
    never exceed _MAX_DOC_TAGS. Only evicts doc-* tags, never
    user-set tags. Idempotent — no-op if the tag is already present.
    """
    from tapps_brain.models import MAX_TAGS

    doc_prefixes = (
        _VALIDATION_TAG_PREFIX,
        _CONTRADICTION_TAG_PREFIX,
        _CHECKED_TAG_PREFIX,
        _DOC_REF_TAG_PREFIX,
    )

    # Idempotency: if the exact tag is already present, nothing to do
    if new_tag in tags:
        return

    # Count current doc tags
    doc_tags = [t for t in tags if any(t.startswith(p) for p in doc_prefixes)]

    # Evict oldest doc tags if over limit
    while len(doc_tags) >= _MAX_DOC_TAGS:
        oldest = doc_tags[0]
        tags.remove(oldest)
        doc_tags.pop(0)

    # Respect overall tag limit
    while len(tags) >= MAX_TAGS:
        # Try to evict a doc tag first
        for t in tags:
            if any(t.startswith(p) for p in doc_prefixes):
                tags.remove(t)
                break
        else:
            break  # No doc tags to evict, can't add

    if len(tags) < MAX_TAGS:
        tags.append(new_tag)


def _source_ceiling(source: str) -> float:
    """Get the confidence ceiling for a given source."""
    ceilings = {
        "human": 0.95,
        "agent": 0.85,
        "inferred": 0.70,
        "system": 0.95,
    }
    return ceilings.get(source, 0.85)
