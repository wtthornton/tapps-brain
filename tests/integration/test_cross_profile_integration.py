"""Cross-profile integration tests for EPIC-010 (STORY-010.8).

Validates the full round-trip: profile loading -> custom layers -> decay ->
scoring -> promotion -> recall, using real MemoryStore + SQLite.
"""

from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.decay import DecayConfig, calculate_decayed_confidence, decay_config_from_profile
from tapps_brain.models import MemoryEntry
from tapps_brain.profile import MemoryProfile, get_builtin_profile
from tapps_brain.promotion import PromotionEngine
from tapps_brain.retrieval import MemoryRetriever
from tapps_brain.store import MemoryStore


@pytest.fixture()
def tmp_project(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_brain_profile() -> MemoryProfile:
    """Load the built-in repo-brain profile."""
    return get_builtin_profile("repo-brain")


def _personal_assistant_profile() -> MemoryProfile:
    """Load the built-in personal-assistant profile."""
    return get_builtin_profile("personal-assistant")


def _make_entry(
    key: str,
    value: str,
    tier: str = "context",
    confidence: float = 0.9,
    source: str = "human",
    tags: list[str] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    last_accessed: str | None = None,
    access_count: int = 0,
    reinforce_count: int = 0,
    last_reinforced: str | None = None,
) -> MemoryEntry:
    """Build a MemoryEntry with sensible defaults for testing."""
    now_iso = datetime(2025, 1, 1, tzinfo=UTC).isoformat()
    return MemoryEntry(
        key=key,
        value=value,
        tier=tier,
        confidence=confidence,
        source=source,
        tags=tags or [],
        created_at=created_at or now_iso,
        updated_at=updated_at or now_iso,
        last_accessed=last_accessed or now_iso,
        access_count=access_count,
        reinforce_count=reinforce_count,
        last_reinforced=last_reinforced,
    )


# ---------------------------------------------------------------------------
# 1. TestPromotionIntegration
# ---------------------------------------------------------------------------


class TestPromotionIntegration:
    """Verify that reinforcement triggers tier promotion via the store."""

    def test_promotion_triggers_after_reinforcements(self, tmp_project):
        """Context entry promoted to procedural after meeting threshold criteria.

        repo-brain context layer: promotion_to=procedural,
        promotion_threshold: min_access_count=5, min_age_days=7, min_confidence=0.5
        """
        profile = _repo_brain_profile()
        store = MemoryStore(tmp_project, profile=profile)
        try:
            # Save a context-tier entry
            result = store.save(
                key="ctx-pattern-01",
                value="Always run linting before committing code changes",
                tier="context",
                source="human",
                confidence=0.9,
            )
            assert isinstance(result, MemoryEntry)

            # Backdate created_at to 10 days ago so min_age_days=7 is met
            ten_days_ago = (datetime.now(tz=UTC) - timedelta(days=10)).isoformat()
            store.update_fields("ctx-pattern-01", created_at=ten_days_ago)

            # Reinforce enough times to meet min_access_count=5.
            # Each reinforce increments access_count by 1 (starts at 1 from save).
            # We need access_count >= 5, so reinforce at least 4 times.
            # Reinforce 5 times to be sure (access_count will be 6 after).
            promoted_entry = None
            for _ in range(5):
                promoted_entry = store.reinforce("ctx-pattern-01", confidence_boost=0.0)

            assert promoted_entry is not None

            # After reinforcement, the store checks promotion.
            # Verify the entry was promoted to "procedural".
            final_entry = store.get("ctx-pattern-01")
            assert final_entry is not None
            assert str(final_entry.tier) == "procedural", (
                f"Expected tier 'procedural' after promotion, got '{final_entry.tier}'"
            )
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 2. TestDemotionIntegration
# ---------------------------------------------------------------------------


class TestDemotionIntegration:
    """Verify demotion check returns the correct target tier for stale entries."""

    def test_demotion_on_stale_entry(self, tmp_project):
        """A long-term entry in personal-assistant with very low confidence
        and no recent access should be demoted to short-term."""
        profile = _personal_assistant_profile()
        config = decay_config_from_profile(profile)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        # 200+ days ago: well past the 90-day half-life of long-term tier
        old_timestamp = (now - timedelta(days=210)).isoformat()

        entry = _make_entry(
            key="lt-stale-fact",
            value="User once visited Paris for a conference",
            tier="long-term",
            confidence=0.15,
            source="agent",
            created_at=old_timestamp,
            updated_at=old_timestamp,
            last_accessed=old_timestamp,
            access_count=1,
        )

        engine = PromotionEngine(config)
        demotion_target = engine.check_demotion(entry, profile, now=now)

        # personal-assistant long-term layer has demotion_to="procedural" (Issue #68)
        assert demotion_target == "procedural", (
            f"Expected demotion to 'procedural', got '{demotion_target}'"
        )


# ---------------------------------------------------------------------------
# 3. TestPowerLawVsExponential
# ---------------------------------------------------------------------------


class TestPowerLawVsExponential:
    """Power-law decay retains more confidence at long time horizons."""

    def test_power_law_retains_more_at_365_days(self):
        """At 365 days, power_law (identity tier in personal-assistant)
        should retain more confidence than exponential with the same half-life."""
        profile = _personal_assistant_profile()
        config = decay_config_from_profile(profile)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        one_year_ago = (now - timedelta(days=365)).isoformat()

        # Entry using power_law (identity tier, half_life=365, exponent=0.5)
        entry = _make_entry(
            key="identity-pref",
            value="User prefers dark mode everywhere",
            tier="identity",
            confidence=0.9,
            source="human",
            created_at=one_year_ago,
            updated_at=one_year_ago,
        )

        power_law_conf = calculate_decayed_confidence(entry, config, now=now)

        # Hypothetical exponential decay with same half-life (365 days):
        # C0 * 0.5^(365 / 365) = 0.9 * 0.5 = 0.45
        exponential_conf = 0.9 * math.pow(0.5, 365.0 / 365.0)

        assert power_law_conf > exponential_conf, (
            f"Power-law ({power_law_conf:.6f}) should retain more confidence "
            f"than exponential ({exponential_conf:.6f}) at 365 days"
        )


# ---------------------------------------------------------------------------
# 4. TestImportanceTagsIntegration
# ---------------------------------------------------------------------------


class TestImportanceTagsIntegration:
    """importance_tags on a layer multiply the effective half-life for decay."""

    def test_critical_tag_doubles_half_life(self):
        """repo-brain architectural layer has importance_tags: {critical: 2.0}.
        An entry tagged 'critical' should have significantly higher confidence
        after 180 days (= base half-life) than one without the tag."""
        profile = _repo_brain_profile()
        config = decay_config_from_profile(profile)

        now = datetime(2025, 1, 1, tzinfo=UTC)
        half_life_ago = (now - timedelta(days=180)).isoformat()

        # Entry WITH "critical" tag -> effective half-life = 180 * 2.0 = 360 days
        critical_entry = _make_entry(
            key="arch-critical",
            value="Database schema uses UUID primary keys everywhere",
            tier="architectural",
            confidence=0.9,
            source="human",
            tags=["critical"],
            created_at=half_life_ago,
            updated_at=half_life_ago,
        )

        # Entry WITHOUT "critical" tag -> effective half-life = 180 days
        normal_entry = _make_entry(
            key="arch-normal",
            value="API responses include request-id headers",
            tier="architectural",
            confidence=0.9,
            source="human",
            tags=["api"],
            created_at=half_life_ago,
            updated_at=half_life_ago,
        )

        critical_conf = calculate_decayed_confidence(critical_entry, config, now=now)
        normal_conf = calculate_decayed_confidence(normal_entry, config, now=now)

        # At 180 days (= base half-life):
        # - normal: 0.9 * 0.5^(180/180) = 0.45
        # - critical: 0.9 * 0.5^(180/360) ~ 0.636
        # So critical should be significantly higher.
        assert critical_conf > normal_conf, (
            f"Critical-tagged entry ({critical_conf:.4f}) should have higher "
            f"confidence than normal entry ({normal_conf:.4f}) after 180 days"
        )

        # The gap should be meaningful (at least 0.1 difference)
        gap = critical_conf - normal_conf
        assert gap > 0.1, (
            f"Confidence gap ({gap:.4f}) should be substantial (> 0.1) "
            f"showing the critical tag's effect"
        )


# ---------------------------------------------------------------------------
# 5. TestCustomScoringWeights
# ---------------------------------------------------------------------------


class TestCustomScoringWeights:
    """Profile scoring weights change how retrieval ranks entries."""

    def test_personal_assistant_recency_ranking(self, tmp_project):
        """personal-assistant has recency=0.30 vs repo-brain's 0.15.
        The personal-assistant retriever should produce a bigger score gap
        between a recent and an old entry than the repo-brain retriever."""
        pa_profile = _personal_assistant_profile()
        rb_profile = _repo_brain_profile()

        pa_config = decay_config_from_profile(pa_profile)
        rb_config = decay_config_from_profile(rb_profile)

        now = datetime.now(tz=UTC)
        thirty_days_ago = (now - timedelta(days=30)).isoformat()

        # Create a store, save both entries, then close.
        store = MemoryStore(tmp_project, profile=rb_profile)
        try:
            store.save(
                key="deploy-recent",
                value="Deploy application using docker compose up command",
                tier="context",
                source="human",
                confidence=0.8,
                tags=["deploy", "docker"],
            )
            store.save(
                key="deploy-old",
                value="Deploy application using kubernetes helm charts",
                tier="context",
                source="human",
                confidence=0.8,
                tags=["deploy", "kubernetes"],
            )
        finally:
            store.close()

        # Backdate the entry directly through the Postgres private backend
        # so the freshly-opened store loads the aged row on cold start.
        from tapps_brain.backends import (
            create_private_backend,
            derive_project_id,
        )

        dsn = os.environ.get("TAPPS_TEST_POSTGRES_DSN") or os.environ.get(
            "TAPPS_BRAIN_DATABASE_URL"
        )
        if not dsn:
            pytest.skip("requires TAPPS_TEST_POSTGRES_DSN / TAPPS_BRAIN_DATABASE_URL")
        backend = create_private_backend(
            dsn, project_id=derive_project_id(tmp_project), agent_id="default"
        )
        old_entry = next(e for e in backend.load_all() if e.key == "deploy-old")
        backdated = old_entry.model_copy(
            update={
                "created_at": thirty_days_ago,
                "updated_at": thirty_days_ago,
                "last_accessed": thirty_days_ago,
            }
        )
        backend.save(backdated)
        backend.close()

        # Re-open the store so it loads the backdated entry from Postgres.
        store = MemoryStore(tmp_project, profile=rb_profile)
        try:
            # Retriever with personal-assistant scoring (recency=0.30)
            pa_retriever = MemoryRetriever(
                config=pa_config,
                scoring_config=pa_profile.scoring,
            )
            pa_results = pa_retriever.search("deploy application", store, limit=10)

            # Retriever with repo-brain scoring (recency=0.15)
            rb_retriever = MemoryRetriever(
                config=rb_config,
                scoring_config=rb_profile.scoring,
            )
            rb_results = rb_retriever.search("deploy application", store, limit=10)

            # Both should find both entries
            assert len(pa_results) >= 2, "PA retriever should find both entries"
            assert len(rb_results) >= 2, "RB retriever should find both entries"

            # Calculate score gaps: |score_recent - score_old|
            pa_scores = {r.entry.key: r.score for r in pa_results}
            rb_scores = {r.entry.key: r.score for r in rb_results}

            pa_gap = abs(pa_scores.get("deploy-recent", 0) - pa_scores.get("deploy-old", 0))
            rb_gap = abs(rb_scores.get("deploy-recent", 0) - rb_scores.get("deploy-old", 0))

            # personal-assistant (recency=0.30) should produce a bigger gap
            # than repo-brain (recency=0.15), because recency weight is doubled.
            assert pa_gap > rb_gap, (
                f"Personal-assistant recency gap ({pa_gap:.4f}) should exceed "
                f"repo-brain gap ({rb_gap:.4f}) due to higher recency weight"
            )
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 6. TestRepoBrainBackwardCompat
# ---------------------------------------------------------------------------


class TestRepoBrainBackwardCompat:
    """repo-brain profile should produce identical recall as no-profile defaults."""

    def test_recall_identical_with_and_without_profile(self, tmp_project):
        """Two stores (one with explicit repo-brain, one without profile)
        should produce identical search results for the same entries and query."""
        profile = _repo_brain_profile()

        # Two separate directories for two stores
        dir_with_profile = tmp_project / "with-profile"
        dir_with_profile.mkdir()
        dir_no_profile = tmp_project / "no-profile"
        dir_no_profile.mkdir()

        store_with = MemoryStore(dir_with_profile, profile=profile)
        # For no-profile store, the default resolution will also pick repo-brain.
        # We pass profile=None to ensure no explicit profile is set, but the
        # store's _resolve_profile will still resolve to repo-brain since it's
        # the default. This is the intended backward-compat behavior.
        store_without = MemoryStore(dir_no_profile)

        try:
            # Save identical entries to both stores
            entries_data = [
                ("arch-decision", "Use PostgreSQL as the primary database", "architectural"),
                ("pattern-naming", "Use snake_case for all Python functions", "pattern"),
                ("proc-deploy", "Run pytest before every deployment", "procedural"),
                ("ctx-current", "Working on authentication module refactor", "context"),
            ]

            for key, value, tier in entries_data:
                for s in (store_with, store_without):
                    s.save(
                        key=key,
                        value=value,
                        tier=tier,
                        source="human",
                        confidence=0.85,
                        tags=["test"],
                    )

            # Build retrievers with matching configs
            config_with = decay_config_from_profile(profile)
            config_without = DecayConfig()

            retriever_with = MemoryRetriever(config=config_with, scoring_config=profile.scoring)
            retriever_without = MemoryRetriever(config=config_without)

            # Search with same query
            query = "database deployment"
            results_with = retriever_with.search(query, store_with, limit=10)
            results_without = retriever_without.search(query, store_without, limit=10)

            # Both should return the same keys in the same order
            keys_with = [r.entry.key for r in results_with]
            keys_without = [r.entry.key for r in results_without]

            assert keys_with == keys_without, (
                f"Results order should match. With profile: {keys_with}, without: {keys_without}"
            )

            # Scores should be identical (or within floating-point tolerance)
            for rw, rwo in zip(results_with, results_without, strict=True):
                assert abs(rw.score - rwo.score) < 0.01, (
                    f"Score mismatch for '{rw.entry.key}': "
                    f"with={rw.score:.4f}, without={rwo.score:.4f}"
                )
        finally:
            store_with.close()
            store_without.close()
