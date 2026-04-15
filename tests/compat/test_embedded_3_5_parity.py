"""Embedded AgentBrain v3.5 API parity suite (STORY-070.14).

These tests **pin** the public-method behavior of the embedded Python library as
it existed in v3.5.x.  They exist to detect behavioral drift introduced by the
remote-first refactor (EPIC-070) or any future work.

Contract enforced:
  - Return-type shapes (recall → list[dict] with canonical keys)
  - Error types (BrainValidationError, BrainConfigError)
  - Confidence-score range (float in [0.0, 1.0])
  - Recall rank order stability (BM25 + confidence score; more-relevant entries
    must rank before less-relevant ones)
  - ``forget`` idempotency (returns False on second call for same key)
  - ``learn_from_failure`` stores value that includes the error string

pytest markers:
  - ``requires_postgres`` — skipped unless ``TAPPS_BRAIN_DATABASE_URL`` is set.

**Policy (STORY-070.14):** Any PR that causes a test in this file to fail *must*
include an ADR note in ``docs/planning/adr/``.  See
``docs/planning/adr/ADR-008-no-http-without-mcp-library-parity.md`` for the
precedent.  Do not update golden assertions without documenting the behavioral
change in an ADR.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HAS_POSTGRES = bool(os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip())

_RECALL_REQUIRED_KEYS = frozenset({"key", "value", "tier", "confidence", "tags"})


def _brain(tmp_path: Path, agent_id: str = "compat-3-5") -> Any:
    from tapps_brain.agent_brain import AgentBrain

    return AgentBrain(agent_id=agent_id, project_dir=tmp_path)


# ---------------------------------------------------------------------------
# Return-shape contracts
# ---------------------------------------------------------------------------


class TestReturnShapes:
    """v3.5 return shapes must not change without an ADR."""

    def test_remember_returns_nonempty_str(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            key = brain.remember("Use ruff for linting", tier="procedural")
        assert isinstance(key, str), "remember() must return str"
        assert len(key) > 0, "remember() must return a non-empty key"

    def test_recall_returns_list(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("tapps-brain uses ruff for Python linting")
            results = brain.recall("ruff linting")
        assert isinstance(results, list), "recall() must return list"

    def test_recall_entries_have_canonical_keys(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("ruff is the linter for tapps-brain")
            results = brain.recall("ruff linting")

        assert len(results) > 0, "recall() must return at least one entry"
        for entry in results:
            missing = _RECALL_REQUIRED_KEYS - entry.keys()
            assert not missing, (
                f"recall() entry is missing canonical key(s): {missing!r}.  "
                "Update this assertion AND write an ADR if the schema changes."
            )

    def test_recall_confidence_is_float_in_range(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("ruff is the linter for tapps-brain")
            results = brain.recall("ruff linting")

        for entry in results:
            conf = entry["confidence"]
            assert isinstance(conf, float), (
                f"recall() confidence must be float, got {type(conf).__name__!r}"
            )
            assert 0.0 <= conf <= 1.0, (
                f"recall() confidence {conf} is outside [0.0, 1.0]"
            )

    def test_recall_tier_is_str(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("procedural memory for compat test", tier="procedural")
            results = brain.recall("procedural memory compat")

        for entry in results:
            assert isinstance(entry["tier"], str), (
                "recall() tier must be str"
            )

    def test_recall_tags_is_list(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("tagged memory entry", tier="pattern")
            results = brain.recall("tagged memory")

        for entry in results:
            assert isinstance(entry["tags"], list), "recall() tags must be list"

    def test_forget_returns_true_on_hit(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            key = brain.remember("transient compat fact")
            result = brain.forget(key)
        assert result is True, "forget() must return True when key exists"

    def test_forget_returns_false_on_miss(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            result = brain.forget("nonexistent-key-xyz-parity-test")
        assert result is False, "forget() must return False for unknown key"

    def test_forget_idempotent(self, tmp_path: Path) -> None:
        """Second forget on same key must return False (not raise)."""
        with _brain(tmp_path) as brain:
            key = brain.remember("idempotent test fact")
            brain.forget(key)
            second = brain.forget(key)
        assert second is False, "forget() called twice must return False on second call"


# ---------------------------------------------------------------------------
# Error-type contracts
# ---------------------------------------------------------------------------


class TestErrorTypes:
    """v3.5 error types must remain stable."""

    def test_invalid_tier_raises_validation_error(self, tmp_path: Path) -> None:
        from tapps_brain.agent_brain import BrainValidationError

        with _brain(tmp_path) as brain:
            with pytest.raises(BrainValidationError):
                brain.remember("fact with bad tier", tier="not-a-real-tier")

    def test_brain_validation_error_is_brain_error(self) -> None:
        from tapps_brain.agent_brain import BrainError, BrainValidationError

        assert issubclass(BrainValidationError, BrainError), (
            "BrainValidationError must inherit from BrainError"
        )

    def test_brain_config_error_is_brain_error(self) -> None:
        from tapps_brain.agent_brain import BrainConfigError, BrainError

        assert issubclass(BrainConfigError, BrainError), (
            "BrainConfigError must inherit from BrainError"
        )

    def test_brain_transient_error_is_brain_error(self) -> None:
        from tapps_brain.agent_brain import BrainError, BrainTransientError

        assert issubclass(BrainTransientError, BrainError), (
            "BrainTransientError must inherit from BrainError"
        )

    def test_brain_validation_error_is_also_value_error(self) -> None:
        """BrainValidationError inherits ValueError for backward compat."""
        from tapps_brain.agent_brain import BrainValidationError

        assert issubclass(BrainValidationError, ValueError), (
            "BrainValidationError must inherit from ValueError for v3.5 compat"
        )


# ---------------------------------------------------------------------------
# Confidence-scoring stability
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    """Confidence scores must be in range and increase after reinforcement."""

    def test_default_confidence_in_range(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("confidence compat test entry", tier="pattern")
            results = brain.recall("confidence compat test")

        assert len(results) > 0
        assert 0.0 <= results[0]["confidence"] <= 1.0

    def test_learn_from_success_does_not_raise(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.remember("ruff linting pattern")
            brain.recall("ruff")  # populate _last_recalled_keys
            brain.learn_from_success("fixed all linting errors")

    def test_learn_from_failure_stores_error_text(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            brain.learn_from_failure(
                "database migration failed",
                error="connection refused",
            )
            results = brain.recall("database migration failed")

        assert len(results) > 0
        # The stored value must contain the error string
        combined_values = " ".join(r["value"] for r in results)
        assert "connection refused" in combined_values, (
            "learn_from_failure() must persist the error string in the stored value"
        )


# ---------------------------------------------------------------------------
# Recall rank-order stability
# ---------------------------------------------------------------------------


class TestRankOrder:
    """Recall must return higher-relevance entries before lower-relevance ones."""

    def test_exact_match_ranks_first(self, tmp_path: Path) -> None:
        """An entry whose value is the query string must rank first."""
        with _brain(tmp_path) as brain:
            # Highly relevant entry
            brain.remember(
                "ruff is the linter; run ruff check src/ before committing",
                tier="procedural",
            )
            # Marginally relevant entry
            brain.remember(
                "use type hints everywhere in Python files",
                tier="pattern",
            )
            results = brain.recall("ruff linter")

        assert len(results) >= 1
        top = results[0]
        assert "ruff" in top["value"].lower(), (
            "The most ruff-relevant entry must rank first.  "
            "If BM25 ranking changed, write an ADR."
        )

    def test_max_results_cap(self, tmp_path: Path) -> None:
        """recall(max_results=N) must return ≤ N entries."""
        with _brain(tmp_path) as brain:
            for i in range(10):
                brain.remember(f"compat rank test fact number {i}", tier="context")
            results = brain.recall("compat rank test fact", max_results=3)

        assert len(results) <= 3, (
            "recall(max_results=3) must not return more than 3 entries"
        )


# ---------------------------------------------------------------------------
# AgentBrain properties
# ---------------------------------------------------------------------------


class TestAgentBrainProperties:
    """Core properties must exist and return the correct types."""

    def test_agent_id_property(self, tmp_path: Path) -> None:
        with _brain(tmp_path, agent_id="compat-id-check") as brain:
            assert brain.agent_id == "compat-id-check"

    def test_groups_is_list(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            assert isinstance(brain.groups, list)

    def test_expert_domains_is_list(self, tmp_path: Path) -> None:
        with _brain(tmp_path) as brain:
            assert isinstance(brain.expert_domains, list)

    def test_context_manager_enter_returns_brain(self, tmp_path: Path) -> None:
        b = _brain(tmp_path)
        with b as entered:
            assert entered is b

    def test_double_close_does_not_raise(self, tmp_path: Path) -> None:
        b = _brain(tmp_path)
        b.close()
        b.close()  # must not raise


# ---------------------------------------------------------------------------
# Postgres-backed parity (requires_postgres)
# ---------------------------------------------------------------------------


@pytest.mark.requires_postgres
class TestPostgresParity:
    """Runs the same core contracts against a live Postgres backend.

    Skipped when ``TAPPS_BRAIN_DATABASE_URL`` is unset (unit CI).  The
    GitHub Actions ``compat`` job always sets this variable so the full
    contract is verified before a PR can merge.
    """

    def test_remember_recall_roundtrip(self, tmp_path: Path) -> None:
        with _brain(tmp_path, agent_id="pg-compat") as brain:
            key = brain.remember("postgres parity: use pgvector for embeddings")
            assert isinstance(key, str) and key

            results = brain.recall("pgvector embeddings")
            assert isinstance(results, list)

    def test_recall_entry_shapes_on_postgres(self, tmp_path: Path) -> None:
        with _brain(tmp_path, agent_id="pg-shape") as brain:
            brain.remember("postgres shape check: ruff formatting")
            results = brain.recall("ruff formatting")

        for entry in results:
            missing = _RECALL_REQUIRED_KEYS - entry.keys()
            assert not missing, (
                f"Postgres recall() entry missing keys: {missing!r}"
            )

    def test_confidence_range_on_postgres(self, tmp_path: Path) -> None:
        with _brain(tmp_path, agent_id="pg-conf") as brain:
            brain.remember("postgres confidence check", tier="architectural")
            results = brain.recall("postgres confidence")

        for entry in results:
            assert 0.0 <= entry["confidence"] <= 1.0

    def test_forget_on_postgres(self, tmp_path: Path) -> None:
        with _brain(tmp_path, agent_id="pg-forget") as brain:
            key = brain.remember("postgres forget test fact")
            assert brain.forget(key) is True
            assert brain.forget(key) is False
