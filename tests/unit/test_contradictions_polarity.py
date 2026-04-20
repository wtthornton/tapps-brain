"""Tests for pairwise polarity & numeric-divergence contradiction detection (TAP-559).

Covers :func:`detect_keyword_polarity`, :func:`detect_numeric_divergence`,
:func:`detect_boolean_polarity`, and :func:`detect_pairwise_contradictions`.
"""

from __future__ import annotations

from tapps_brain.contradictions import (
    PolarityContradiction,
    detect_boolean_polarity,
    detect_keyword_polarity,
    detect_numeric_divergence,
    detect_pairwise_contradictions,
)
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Keyword polarity
# ---------------------------------------------------------------------------


class TestDetectKeywordPolarity:
    def test_detects_conflicting_technology_claims(self) -> None:
        """'uses Postgres' vs 'uses SQLite' → keyword_polarity."""
        ea = make_entry(key="db-config", value="We use Postgres for all persistence.")
        eb = make_entry(key="db-choice", value="We use SQLite as our storage backend.")
        hit = detect_keyword_polarity(ea, eb)
        assert hit is not None
        assert hit.contradiction_type == "keyword_polarity"
        assert hit.entry_a_key == "db-config"
        assert hit.entry_b_key == "db-choice"
        assert "postgres" in hit.reason.lower() or "sqlite" in hit.reason.lower()

    def test_no_contradiction_when_no_use_claims(self) -> None:
        ea = make_entry(key="a", value="Memory is important for agents.")
        eb = make_entry(key="b", value="Agents need persistent state.")
        assert detect_keyword_polarity(ea, eb) is None

    def test_no_contradiction_when_same_technology(self) -> None:
        ea = make_entry(key="a", value="We use Postgres for writes.")
        eb = make_entry(key="b", value="Using Postgres ensures ACID compliance.")
        assert detect_keyword_polarity(ea, eb) is None

    def test_detects_migrated_to_conflict(self) -> None:
        ea = make_entry(key="a", value="We migrated to Redis for caching.")
        eb = make_entry(key="b", value="We migrated to Memcached for the cache layer.")
        hit = detect_keyword_polarity(ea, eb)
        assert hit is not None
        assert hit.contradiction_type == "keyword_polarity"

    def test_detects_built_with_conflict(self) -> None:
        ea = make_entry(key="a", value="Built with FastAPI for the REST layer.")
        eb = make_entry(key="b", value="Built with Django for the web layer.")
        hit = detect_keyword_polarity(ea, eb)
        assert hit is not None

    def test_no_contradiction_one_side_no_claim(self) -> None:
        ea = make_entry(key="a", value="We use PostgreSQL for storage.")
        eb = make_entry(key="b", value="The architecture is event-driven.")
        assert detect_keyword_polarity(ea, eb) is None

    def test_returns_polarity_contradiction_type(self) -> None:
        ea = make_entry(key="a", value="Using npm for package management.")
        eb = make_entry(key="b", value="Using yarn for package management.")
        hit = detect_keyword_polarity(ea, eb)
        assert hit is not None
        assert isinstance(hit, PolarityContradiction)
        assert hit.detected_at  # auto-populated


# ---------------------------------------------------------------------------
# Numeric divergence
# ---------------------------------------------------------------------------


class TestDetectNumericDivergence:
    def test_detects_threshold_divergence(self) -> None:
        """Same label 'threshold', values 0.7 vs 0.9 → numeric_divergence."""
        ea = make_entry(key="a", value="The consolidation threshold is 0.7 for merging.")
        eb = make_entry(key="b", value="The consolidation threshold is 0.9 for merging.")
        hit = detect_numeric_divergence(ea, eb)
        assert hit is not None
        assert hit.contradiction_type == "numeric_divergence"
        assert "threshold" in hit.reason.lower()
        assert "0.7" in hit.reason and "0.9" in hit.reason

    def test_no_contradiction_when_values_close(self) -> None:
        """Values within the 15% relative threshold are not flagged."""
        ea = make_entry(key="a", value="timeout is 30 seconds.")
        eb = make_entry(key="b", value="timeout is 31 seconds.")
        assert detect_numeric_divergence(ea, eb) is None

    def test_no_contradiction_when_different_labels(self) -> None:
        ea = make_entry(key="a", value="max_entries is 5000 for the store.")
        eb = make_entry(key="b", value="timeout is 30 seconds.")
        assert detect_numeric_divergence(ea, eb) is None

    def test_detects_large_absolute_divergence(self) -> None:
        ea = make_entry(key="a", value="max entries is 500.")
        eb = make_entry(key="b", value="max entries is 5000.")
        hit = detect_numeric_divergence(ea, eb)
        assert hit is not None
        assert hit.contradiction_type == "numeric_divergence"

    def test_custom_divergence_threshold(self) -> None:
        ea = make_entry(key="a", value="retention is 30.")
        eb = make_entry(key="b", value="retention is 35.")
        # Default threshold (15%) would not flag 30 vs 35 (16.7% > 15%).
        # But at 20% threshold they should NOT be flagged.
        assert detect_numeric_divergence(ea, eb, divergence_threshold=0.20) is None

    def test_no_contradiction_when_no_numerics(self) -> None:
        ea = make_entry(key="a", value="The system uses Postgres.")
        eb = make_entry(key="b", value="Storage is backed by a database.")
        assert detect_numeric_divergence(ea, eb) is None

    def test_result_has_keys(self) -> None:
        ea = make_entry(key="cfg-a", value="threshold is 0.5.")
        eb = make_entry(key="cfg-b", value="threshold is 0.95.")
        hit = detect_numeric_divergence(ea, eb)
        assert hit is not None
        assert hit.entry_a_key == "cfg-a"
        assert hit.entry_b_key == "cfg-b"


# ---------------------------------------------------------------------------
# Boolean polarity
# ---------------------------------------------------------------------------


class TestDetectBooleanPolarity:
    def test_detects_enabled_disabled(self) -> None:
        """'auto_migrate is enabled' vs 'auto_migrate is disabled'."""
        ea = make_entry(key="a", value="auto migrate is enabled by default.")
        eb = make_entry(key="b", value="auto migrate is disabled in production.")
        hit = detect_boolean_polarity(ea, eb)
        assert hit is not None
        assert hit.contradiction_type == "boolean_polarity"
        assert "auto migrate" in hit.reason.lower()
        assert "enabled" in hit.reason.lower()
        assert "disabled" in hit.reason.lower()

    def test_detects_true_false(self) -> None:
        ea = make_entry(key="a", value="debug logging is true.")
        eb = make_entry(key="b", value="debug logging is false.")
        hit = detect_boolean_polarity(ea, eb)
        assert hit is not None
        assert hit.contradiction_type == "boolean_polarity"

    def test_detects_on_off(self) -> None:
        ea = make_entry(key="a", value="feature flag is on.")
        eb = make_entry(key="b", value="feature flag is off.")
        hit = detect_boolean_polarity(ea, eb)
        assert hit is not None

    def test_no_contradiction_when_same_state(self) -> None:
        ea = make_entry(key="a", value="caching is enabled for all queries.")
        eb = make_entry(key="b", value="caching is enabled by default.")
        assert detect_boolean_polarity(ea, eb) is None

    def test_no_contradiction_different_labels(self) -> None:
        ea = make_entry(key="a", value="caching is enabled.")
        eb = make_entry(key="b", value="logging is disabled.")
        assert detect_boolean_polarity(ea, eb) is None

    def test_no_contradiction_no_booleans(self) -> None:
        ea = make_entry(key="a", value="Uses PostgreSQL.")
        eb = make_entry(key="b", value="Built with FastAPI.")
        assert detect_boolean_polarity(ea, eb) is None


# ---------------------------------------------------------------------------
# Pairwise scan
# ---------------------------------------------------------------------------


class TestDetectPairwiseContradictions:
    def test_empty_returns_empty(self) -> None:
        assert detect_pairwise_contradictions([]) == []

    def test_single_entry_returns_empty(self) -> None:
        e = make_entry(key="a", value="We use Postgres.")
        assert detect_pairwise_contradictions([e]) == []

    def test_detects_keyword_polarity_in_scan(self) -> None:
        entries = [
            make_entry(key="a", value="We use Postgres for all data."),
            make_entry(key="b", value="We use SQLite as storage."),
            make_entry(key="c", value="Agents read and write memories."),
        ]
        hits = detect_pairwise_contradictions(entries)
        assert len(hits) == 1
        assert hits[0].contradiction_type == "keyword_polarity"

    def test_detects_numeric_divergence_in_scan(self) -> None:
        entries = [
            make_entry(key="cfg-1", value="max entries is 1000."),
            make_entry(key="cfg-2", value="max entries is 9000."),
        ]
        hits = detect_pairwise_contradictions(entries)
        assert len(hits) == 1
        assert hits[0].contradiction_type == "numeric_divergence"

    def test_skips_contradicted_entries(self) -> None:
        entries = [
            make_entry(key="a", value="We use Postgres.", contradicted=True),
            make_entry(key="b", value="We use SQLite."),
            make_entry(key="c", value="Agents store memories."),
        ]
        hits = detect_pairwise_contradictions(entries)
        # Entry "a" is contradicted so pair (a, b) is not checked.
        assert hits == []

    def test_multiple_contradiction_types_detected(self) -> None:
        entries = [
            make_entry(key="a", value="We use Postgres. threshold is 0.5."),
            make_entry(key="b", value="We use SQLite. threshold is 0.9."),
        ]
        hits = detect_pairwise_contradictions(entries)
        # Only first hit per pair is reported (keyword_polarity or numeric_divergence).
        assert len(hits) == 1

    def test_deterministic_order(self) -> None:
        """Results have consistent entry_key order across repeated calls."""
        entries = [
            make_entry(key="z-entry", value="We use Postgres."),
            make_entry(key="a-entry", value="We use SQLite."),
        ]
        hits1 = detect_pairwise_contradictions(entries)
        hits2 = detect_pairwise_contradictions(entries)

        # Compare deterministic fields; detected_at timestamps may differ between calls.
        def _key_fields(h: PolarityContradiction) -> tuple[str, str, str]:
            return (h.entry_a_key, h.entry_b_key, h.contradiction_type)

        assert [_key_fields(h) for h in hits1] == [_key_fields(h) for h in hits2]

    def test_result_has_detected_at(self) -> None:
        ea = make_entry(key="a", value="debug is enabled.")
        eb = make_entry(key="b", value="debug is disabled.")
        hits = detect_pairwise_contradictions([ea, eb])
        assert len(hits) == 1
        assert hits[0].detected_at  # auto-populated ISO timestamp
