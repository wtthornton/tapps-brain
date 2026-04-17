"""Integration tests for configurable memory profiles (EPIC-010).

Uses REAL MemoryStore + SQLite (no mocks) to verify that profile wiring
works end-to-end: default profile resolution, custom tier names, decay
with profile half-lives, power-law decay, backward compatibility, persist
across restart, and GC with profile thresholds.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from tapps_brain.decay import DecayConfig, calculate_decayed_confidence, decay_config_from_profile
from tapps_brain.gc import MemoryGarbageCollector
from tapps_brain.models import MemoryEntry
from tapps_brain.profile import (
    GCConfig,
    LayerDefinition,
    MemoryProfile,
    get_builtin_profile,
)
from tapps_brain.store import MemoryStore


@pytest.fixture()
def tmp_project(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _personal_assistant_profile() -> MemoryProfile:
    """Load the built-in personal-assistant profile."""
    return get_builtin_profile("personal-assistant")


def _repo_brain_profile() -> MemoryProfile:
    """Load the built-in repo-brain profile."""
    return get_builtin_profile("repo-brain")


# ---------------------------------------------------------------------------
# 1. Store loads default repo-brain profile
# ---------------------------------------------------------------------------


class TestDefaultProfileLoading:
    """Verify that a store created with no profile arg gets repo-brain."""

    def test_store_loads_default_repo_brain_profile(self, tmp_project):
        store = MemoryStore(tmp_project)
        try:
            assert store.profile is not None, "Profile should not be None"
            assert store.profile.name == "repo-brain"
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 2. Store with explicit profile — custom tier names
# ---------------------------------------------------------------------------


class TestExplicitProfileCustomTiers:
    """Create a personal-assistant profile and verify custom tier names work."""

    def test_save_and_retrieve_custom_tier_entries(self, tmp_project):
        profile = _personal_assistant_profile()
        store = MemoryStore(tmp_project, profile=profile)
        try:
            custom_tiers = {
                "identity": "User prefers dark mode in all applications",
                "long-term": "User works at Acme Corp as a software engineer",
                "short-term": "Currently working on the Q4 report",
                "ephemeral": "Just asked about the weather in NYC",
            }

            # Save entries with custom tier names
            for tier_name, value in custom_tiers.items():
                key = f"test-{tier_name}"
                result = store.save(
                    key=key,
                    value=value,
                    tier=tier_name,
                    source="human",
                    confidence=0.9,
                )
                assert isinstance(result, MemoryEntry), (
                    f"Save should return MemoryEntry, got {type(result)}"
                )

            # Verify all entries persist and can be retrieved
            for tier_name in custom_tiers:
                key = f"test-{tier_name}"
                entry = store.get(key)
                assert entry is not None, f"Entry '{key}' should be retrievable"
                assert str(entry.tier) == tier_name
                assert entry.value == custom_tiers[tier_name]
        finally:
            store.close()

    def test_list_all_filters_by_custom_tier(self, tmp_project):
        profile = _personal_assistant_profile()
        store = MemoryStore(tmp_project, profile=profile)
        try:
            store.save(key="id-1", value="User name is Alice", tier="identity", source="human")
            store.save(key="lt-1", value="Likes coffee", tier="long-term", source="human")
            store.save(key="id-2", value="Born in 1990", tier="identity", source="human")

            identity_entries = store.list_all(tier="identity")
            assert len(identity_entries) == 2
            keys = {e.key for e in identity_entries}
            assert keys == {"id-1", "id-2"}
        finally:
            store.close()


# ---------------------------------------------------------------------------
# 3. Decay uses profile half-lives
# ---------------------------------------------------------------------------


class TestDecayUsesProfileHalfLives:
    """With personal-assistant profile, identity (365d) vs ephemeral (1d)."""

    def test_identity_decays_slower_than_ephemeral(self):
        profile = _personal_assistant_profile()
        config = decay_config_from_profile(profile)

        now = datetime.now(tz=UTC)
        ten_days_ago = (now - timedelta(days=10)).isoformat()

        identity_entry = MemoryEntry(
            key="test-identity",
            value="User prefers dark mode",
            tier="identity",
            confidence=0.9,
            source="human",
            created_at=ten_days_ago,
            updated_at=ten_days_ago,
        )

        ephemeral_entry = MemoryEntry(
            key="test-ephemeral",
            value="Asked about weather",
            tier="ephemeral",
            confidence=0.9,
            source="human",
            created_at=ten_days_ago,
            updated_at=ten_days_ago,
        )

        identity_conf = calculate_decayed_confidence(identity_entry, config, now=now)
        ephemeral_conf = calculate_decayed_confidence(ephemeral_entry, config, now=now)

        # Identity (365-day half-life) should retain much more confidence
        # than ephemeral (1-day half-life) after 10 days.
        assert identity_conf > ephemeral_conf, (
            f"Identity ({identity_conf:.4f}) should have higher confidence "
            f"than ephemeral ({ephemeral_conf:.4f}) after 10 days"
        )

        # Identity should still be very high (365d half-life, only 10d elapsed)
        assert identity_conf > 0.8, (
            f"Identity confidence should remain high after 10 days, got {identity_conf:.4f}"
        )

        # Ephemeral should be extremely low (1-day half-life, 10 days elapsed)
        # 0.9 * 0.5^10 ~ 0.000879, but floor may apply
        assert ephemeral_conf < 0.1, (
            f"Ephemeral confidence should be very low after 10 days, got {ephemeral_conf:.4f}"
        )


# ---------------------------------------------------------------------------
# 4. Power-law decay on identity tier
# ---------------------------------------------------------------------------


class TestPowerLawDecay:
    """Personal-assistant identity tier uses power_law decay.

    Power-law has a longer tail than exponential, so after 100 days
    the confidence should be higher than exponential would give.
    """

    def test_power_law_higher_than_exponential_at_100_days(self):
        profile = _personal_assistant_profile()
        config = decay_config_from_profile(profile)

        now = datetime.now(tz=UTC)
        hundred_days_ago = (now - timedelta(days=100)).isoformat()

        identity_entry = MemoryEntry(
            key="test-power-law",
            value="Core identity preference",
            tier="identity",
            confidence=0.9,
            source="human",
            created_at=hundred_days_ago,
            updated_at=hundred_days_ago,
        )

        # Actual decayed confidence using profile (power_law)
        actual_conf = calculate_decayed_confidence(identity_entry, config, now=now)

        # What exponential decay would give at same half-life (365 days):
        # C0 * 0.5^(days / half_life) = 0.9 * 0.5^(100/365)
        exponential_conf = 0.9 * math.pow(0.5, 100.0 / 365.0)

        assert actual_conf > exponential_conf, (
            f"Power-law ({actual_conf:.6f}) should be higher than "
            f"exponential ({exponential_conf:.6f}) at 100 days"
        )

        # Verify the decay model is indeed power_law for identity tier
        assert config.layer_decay_models.get("identity") == "power_law"
        assert config.layer_decay_exponents.get("identity") == 0.5

    def test_power_law_formula_matches_expected(self):
        """Verify the power-law formula: C0 * (1 + t/(k*H))^(-beta)."""
        profile = _personal_assistant_profile()
        config = decay_config_from_profile(profile)

        now = datetime.now(tz=UTC)
        days = 100
        timestamp = (now - timedelta(days=days)).isoformat()

        entry = MemoryEntry(
            key="test-formula",
            value="Test power law formula",
            tier="identity",
            confidence=0.9,
            source="human",
            created_at=timestamp,
            updated_at=timestamp,
        )

        actual = calculate_decayed_confidence(entry, config, now=now)

        # Manual calculation: C0 * (1 + t / (k * H))^(-beta)
        # k = 81/19 (FSRS-canonical default, STORY-SC02), H=365, beta=0.5
        k = 81.0 / 19.0
        half_life = 365
        beta = 0.5
        expected = 0.9 * math.pow(1.0 + days / (k * half_life), -beta)

        assert abs(actual - expected) < 1e-6, (
            f"Power-law result {actual:.8f} should match manual calc {expected:.8f}"
        )


# ---------------------------------------------------------------------------
# 5. repo-brain backward compatibility
# ---------------------------------------------------------------------------


class TestRepoBrainBackwardCompat:
    """repo-brain profile should behave identically to no-profile defaults."""

    def test_repo_brain_decay_matches_default_config(self):
        profile = _repo_brain_profile()
        profile_config = decay_config_from_profile(profile)
        default_config = DecayConfig()

        # All four legacy half-lives should match
        assert (
            profile_config.architectural_half_life_days
            == default_config.architectural_half_life_days
        )
        assert profile_config.pattern_half_life_days == default_config.pattern_half_life_days
        assert profile_config.procedural_half_life_days == default_config.procedural_half_life_days
        assert profile_config.context_half_life_days == default_config.context_half_life_days

    def test_repo_brain_standard_tiers_save_and_retrieve(self, tmp_project):
        profile = _repo_brain_profile()
        store = MemoryStore(tmp_project, profile=profile)
        try:
            standard_tiers = ["architectural", "pattern", "procedural", "context"]
            for tier_name in standard_tiers:
                key = f"rb-{tier_name}"
                result = store.save(
                    key=key,
                    value=f"Test entry for {tier_name}",
                    tier=tier_name,
                    source="agent",
                )
                assert isinstance(result, MemoryEntry), (
                    f"Save with tier '{tier_name}' should succeed"
                )

            for tier_name in standard_tiers:
                key = f"rb-{tier_name}"
                entry = store.get(key)
                assert entry is not None
                assert str(entry.tier) == tier_name
        finally:
            store.close()

    def test_repo_brain_preserves_median_retention_at_half_life(self):
        """STORY-SC02 (TAP-558): repo-brain migrated from exponential to power_law
        with the half-life-anchor exponent β = ln(2)/ln(1+1/k). For each tier,
        ``calculate_decayed_confidence`` at ``days = half_life_days`` must equal
        ``confidence × 0.5`` within 1 % tolerance — preserving the median
        retention behavior of the prior exponential configuration even though
        near-term and tail behavior intentionally diverge.
        """
        profile = _repo_brain_profile()
        profile_config = decay_config_from_profile(profile)

        now = datetime.now(tz=UTC)
        c0 = 0.9

        tier_half_lives = {
            "architectural": 180,
            "pattern": 60,
            "procedural": 30,
            "context": 14,
        }

        for tier_name, half_life in tier_half_lives.items():
            ref = (now - timedelta(days=half_life)).isoformat()
            entry = MemoryEntry(
                key=f"anchor-{tier_name}",
                value=f"Half-life anchor for {tier_name}",
                tier=tier_name,
                confidence=c0,
                source="human",
                created_at=ref,
                updated_at=ref,
            )
            decayed = calculate_decayed_confidence(entry, profile_config, now=now)
            target = c0 * 0.5
            tolerance = 0.01 * c0  # 1 %
            assert abs(decayed - target) < tolerance, (
                f"Tier '{tier_name}': R(t=H) should be ≈ {target:.4f}, got {decayed:.6f}"
            )

    def test_repo_brain_uses_power_law_for_all_tiers(self):
        """STORY-SC02 (TAP-558): assert the migration actually happened."""
        profile = _repo_brain_profile()
        config = decay_config_from_profile(profile)
        for tier_name in ["architectural", "pattern", "procedural", "context"]:
            assert config.layer_decay_models.get(tier_name) == "power_law", (
                f"Tier '{tier_name}' should use power_law decay after STORY-SC02 migration"
            )


# ---------------------------------------------------------------------------
# 6. Custom tier names persist across store restart
# ---------------------------------------------------------------------------


class TestCustomTierPersistAcrossRestart:
    """Close and reopen the store; custom tier entries survive."""

    def test_identity_tier_survives_restart(self, tmp_project):
        profile = _personal_assistant_profile()

        # Phase 1: create store, save entry, close
        store1 = MemoryStore(tmp_project, profile=profile)
        result = store1.save(
            key="persist-identity",
            value="User is left-handed",
            tier="identity",
            source="human",
            confidence=0.9,
        )
        assert isinstance(result, MemoryEntry)
        store1.close()

        # Phase 2: reopen with same profile, verify entry survives
        store2 = MemoryStore(tmp_project, profile=profile)
        try:
            entry = store2.get("persist-identity")
            assert entry is not None, "Entry should survive store restart"
            assert str(entry.tier) == "identity"
            assert entry.value == "User is left-handed"
            assert entry.confidence == 0.9  # explicit confidence preserved
        finally:
            store2.close()

    def test_multiple_custom_tiers_survive_restart(self, tmp_project):
        profile = _personal_assistant_profile()

        # Phase 1: save entries across multiple custom tiers
        store1 = MemoryStore(tmp_project, profile=profile)
        tiers_data = {
            "identity": "User prefers vim",
            "long-term": "Works in fintech",
            "short-term": "Debugging auth module",
            "ephemeral": "Current file is main.py",
        }
        for tier_name, value in tiers_data.items():
            store1.save(
                key=f"multi-{tier_name}",
                value=value,
                tier=tier_name,
                source="human",
            )
        store1.close()

        # Phase 2: reopen and verify all entries
        store2 = MemoryStore(tmp_project, profile=profile)
        try:
            for tier_name, value in tiers_data.items():
                key = f"multi-{tier_name}"
                entry = store2.get(key)
                assert entry is not None, f"Entry '{key}' should survive restart"
                assert str(entry.tier) == tier_name
                assert entry.value == value
        finally:
            store2.close()


# ---------------------------------------------------------------------------
# 7. GC uses profile thresholds
# ---------------------------------------------------------------------------


class TestGCUsesProfileThresholds:
    """GC should use profile-driven session_expiry_days."""

    def test_gc_archives_session_entry_with_profile_threshold(self):
        """A profile with session_expiry_days=1 should archive after 1 day."""
        # Create a profile with aggressive GC (1-day session expiry)
        profile = MemoryProfile(
            name="aggressive-gc",
            layers=[
                LayerDefinition(
                    name="context",
                    half_life_days=14,
                    confidence_floor=0.05,
                ),
            ],
            gc=GCConfig(session_expiry_days=1),
        )

        config = decay_config_from_profile(profile)

        # Create a session-scoped entry from 2 days ago
        now = datetime.now(tz=UTC)
        two_days_ago = (now - timedelta(days=2)).isoformat()

        session_entry = MemoryEntry(
            key="session-old",
            value="Temporary session data",
            tier="context",
            scope="session",
            confidence=0.9,
            source="agent",
            created_at=two_days_ago,
            updated_at=two_days_ago,
        )

        # GC with profile threshold (session_expiry_days=1)
        gc = MemoryGarbageCollector(
            config=config,
            session_expiry_days=profile.gc.session_expiry_days,
        )
        candidates = gc.identify_candidates([session_entry], now=now)

        assert len(candidates) == 1, (
            "Session entry older than 1 day should be archived with profile threshold"
        )
        assert candidates[0].key == "session-old"

    def test_default_gc_does_not_archive_2day_old_session(self):
        """With default session_expiry_days=7, a 2-day-old session should NOT be archived."""
        config = DecayConfig()

        now = datetime.now(tz=UTC)
        two_days_ago = (now - timedelta(days=2)).isoformat()

        session_entry = MemoryEntry(
            key="session-recent",
            value="Recent session data",
            tier="context",
            scope="session",
            confidence=0.9,
            source="agent",
            created_at=two_days_ago,
            updated_at=two_days_ago,
        )

        # GC with default threshold (session_expiry_days=7)
        gc = MemoryGarbageCollector(config=config)
        candidates = gc.identify_candidates([session_entry], now=now)

        assert len(candidates) == 0, (
            "2-day-old session entry should NOT be archived with default 7-day threshold"
        )

    def test_gc_profile_vs_default_threshold_contrast(self):
        """Same entry: archived by profile GC (1d), kept by default GC (7d)."""
        profile = MemoryProfile(
            name="fast-gc",
            layers=[
                LayerDefinition(name="context", half_life_days=14, confidence_floor=0.05),
            ],
            gc=GCConfig(session_expiry_days=1),
        )

        config = decay_config_from_profile(profile)
        now = datetime.now(tz=UTC)
        three_days_ago = (now - timedelta(days=3)).isoformat()

        entry = MemoryEntry(
            key="gc-contrast",
            value="Session entry for contrast test",
            tier="context",
            scope="session",
            confidence=0.9,
            source="agent",
            created_at=three_days_ago,
            updated_at=three_days_ago,
        )

        # Profile GC: 1-day expiry -> should archive
        profile_gc = MemoryGarbageCollector(
            config=config,
            session_expiry_days=profile.gc.session_expiry_days,
        )
        profile_candidates = profile_gc.identify_candidates([entry], now=now)
        assert len(profile_candidates) == 1

        # Default GC: 7-day expiry -> should NOT archive
        default_gc = MemoryGarbageCollector(config=DecayConfig())
        default_candidates = default_gc.identify_candidates([entry], now=now)
        assert len(default_candidates) == 0
