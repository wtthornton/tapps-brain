"""Tier integrity: no unpriceable tier is written, and none crashes a batch pass.

TAP-6698 / VAL-09.  ``private_memories.tier`` is free text: a
:class:`~tapps_brain.models.MemoryTier` member and an EPIC-010 profile layer
name are both legal values, and nothing in the row records *which profile*
defines a layer name.  A row written under an in-process profile that was never
persisted therefore carries a tier the reading process cannot price, and that
single fact produced both halves of the live defect:

* ``decay._get_half_life`` raised ``ValueError`` uncaught through
  ``calculate_decayed_confidence`` → ``identify_decay_refresh`` →
  ``refresh_decay``, aborting the scheduled decay pass for the whole tenant.
* ``retention_slo``'s SLO 1 inner-joined the tier half-life table, so those
  same rows could never appear as a violation at any age.

The maintenance pass crashed on exactly the rows its own health check could not
see.  These tests pin both directions: unpriceable tiers cannot be *written*
through the paths that bypass ``MemoryStore.save``, and a scope that already
contains one is *reported*, not crashed on and not silently dropped.
"""

from __future__ import annotations

import pytest

from tapps_brain.decay import DecayConfig, decay_config_from_profile, tier_is_resolvable
from tapps_brain.experience import MemorySpec
from tapps_brain.models import MemoryTier
from tapps_brain.profile import get_builtin_profile

#: The three tiers found on the deployed brain that no live profile defines.
#: They are ``personal-assistant.yaml`` layer names (:7, :19, and the
#: short-term layer), emitted by ``extraction._PA_PATTERNS``.
LIVE_UNPRICEABLE_TIERS = ("identity", "long-term", "short-term")


class TestTierIsResolvable:
    """The predicate must agree exactly with ``_get_half_life``'s behaviour."""

    @pytest.mark.parametrize("tier", [t.value for t in MemoryTier])
    def test_every_enum_member_is_resolvable(self, tier: str) -> None:
        assert tier_is_resolvable(tier, DecayConfig()) is True

    @pytest.mark.parametrize("tier", LIVE_UNPRICEABLE_TIERS)
    def test_live_bad_tiers_are_not_resolvable_under_the_default_config(self, tier: str) -> None:
        assert tier_is_resolvable(tier, DecayConfig()) is False

    @pytest.mark.parametrize("tier", LIVE_UNPRICEABLE_TIERS)
    def test_same_tiers_are_resolvable_under_the_profile_that_defines_them(self, tier: str) -> None:
        """Not a bad *value* — a value whose meaning lives in a profile.

        This is the whole shape of the defect: the tier is perfectly valid
        under ``personal-assistant`` and meaningless without it, and the row
        does not carry which one applies.
        """
        config = decay_config_from_profile(get_builtin_profile("personal-assistant"))
        assert tier_is_resolvable(tier, config) is True

    def test_agrees_with_get_half_life_on_resolvable_tiers(self) -> None:
        from tapps_brain.decay import _get_half_life

        config = decay_config_from_profile(get_builtin_profile("personal-assistant"))
        for tier in LIVE_UNPRICEABLE_TIERS:
            assert tier_is_resolvable(tier, config) is True
            assert _get_half_life(tier, config) > 0

    def test_agrees_with_get_half_life_on_unresolvable_tiers(self) -> None:
        """``_get_half_life`` keeps its strict contract — the predicate is the
        way to ask without paying an exception."""
        from tapps_brain.decay import _get_half_life

        config = DecayConfig()
        for tier in LIVE_UNPRICEABLE_TIERS:
            assert tier_is_resolvable(tier, config) is False
            with pytest.raises(ValueError, match="Unknown tier"):
                _get_half_life(tier, config)


class TestExperienceMemorySpecClosesTheRawInsertIngress:
    """``MemorySpec`` feeds a raw INSERT that skips ``normalize_save_tier``.

    Before TAP-6698 it validated only through ``MemoryEntry``, which passes
    unrecognised strings through as possible profile layer names — correct in
    the store (which knows its profile), wrong here (this path has none), so
    the row landed unpriceable by construction.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("identity", "architectural"),
            ("long-term", "architectural"),
            ("long_term", "architectural"),
            ("short-term", "pattern"),
            ("short_term", "pattern"),
        ],
    )
    def test_live_bad_tiers_resolve_through_the_shared_alias_table(
        self, raw: str, expected: str
    ) -> None:
        assert MemorySpec(key="k", value="v", tier=raw).tier == expected

    def test_a_genuinely_unknown_tier_falls_back_to_pattern(self) -> None:
        assert MemorySpec(key="k", value="v", tier="not-a-tier-anywhere").tier == "pattern"

    def test_enum_tiers_are_untouched(self) -> None:
        for tier in MemoryTier:
            assert MemorySpec(key="k", value="v", tier=tier.value).tier == tier.value

    def test_default_tier_is_unchanged(self) -> None:
        """Regression guard on the pre-existing default (``pattern``)."""
        assert MemorySpec(key="k", value="v").tier == "pattern"

    @pytest.mark.parametrize("raw", [*LIVE_UNPRICEABLE_TIERS, "not-a-tier-anywhere", "", "  "])
    def test_the_spec_can_never_emit_an_unpriceable_tier(self, raw: str) -> None:
        """The actual invariant: whatever goes in, what comes out is priceable.

        Enumerating aliases proves the mapping; this proves the *closure* —
        there is no input for which the raw INSERT can write a tier the decay
        engine cannot price with a bare ``DecayConfig()``.
        """
        spec = MemorySpec(key="k", value="v", tier=raw)
        assert tier_is_resolvable(spec.tier, DecayConfig()) is True

    def test_non_string_tier_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="tier must be a string"):
            MemorySpec(key="k", value="v", tier=123)  # type: ignore[arg-type]


class TestRefreshDecaySurvivesAnUnpriceableTier:
    """The pass must complete and *report*, not crash and not silently drop.

    The scope is built the way the live one was: written by a store bound to
    ``personal-assistant`` (so the layer tier is legal and is persisted
    verbatim), then read back by a store on the default profile — the
    maintenance service's situation exactly, since the profile was never
    written to ``project_profiles``.
    """

    @staticmethod
    def _seed_pa_scope(project_root):
        from tapps_brain.store import MemoryStore

        store = MemoryStore(project_root, profile=get_builtin_profile("personal-assistant"))
        try:
            store.save(key="pa-identity", value="User is a software engineer", tier="identity")
            store.save(key="pa-long-term", value="User prefers dark mode", tier="long-term")
            store.save(key="pa-normal", value="A perfectly ordinary note", tier="pattern")
            assert str(store.get("pa-identity").tier) == "identity", (
                "fixture invariant: the layer tier must persist verbatim, "
                "otherwise this test is not reproducing the live shape"
            )
        finally:
            store.close()

    def test_refresh_decay_completes_and_reports_the_unpriceable_rows(self, tmp_path) -> None:
        from tapps_brain.store import MemoryStore

        self._seed_pa_scope(tmp_path)
        # Same project root, default (repo-brain) profile — the reader has no
        # idea 'identity' ever meant anything.
        reader = MemoryStore(tmp_path)
        try:
            assert tier_is_resolvable("identity", reader._get_decay_config()) is False, (
                "fixture invariant: the reader must NOT be able to price the tier"
            )
            result = reader.refresh_decay(dry_run=True)
        finally:
            reader.close()

        assert result["unresolved_tier_rows"] == 2
        assert sorted(r["key"] for r in result["unresolved_tier_sample"]) == [
            "pa-identity",
            "pa-long-term",
        ]
        assert {r["tier"] for r in result["unresolved_tier_sample"]} == {"identity", "long-term"}

    def test_the_pass_used_to_raise_and_now_does_not(self, tmp_path) -> None:
        """Direct pin on the crash: ``calculate_decayed_confidence`` on one of
        these rows still raises, and ``refresh_decay`` no longer propagates it."""
        from tapps_brain.decay import calculate_decayed_confidence
        from tapps_brain.store import MemoryStore

        self._seed_pa_scope(tmp_path)
        reader = MemoryStore(tmp_path)
        try:
            entry = reader.get("pa-identity")
            with pytest.raises(ValueError, match="Unknown tier"):
                calculate_decayed_confidence(entry, reader._get_decay_config())
            reader.refresh_decay(dry_run=True)  # must not raise
        finally:
            reader.close()

    def test_priceable_rows_are_still_judged_normally(self, tmp_path) -> None:
        """Correct-negative: the skip must not swallow the rest of the scope."""
        from tapps_brain.store import MemoryStore

        self._seed_pa_scope(tmp_path)
        reader = MemoryStore(tmp_path)
        try:
            result = reader.refresh_decay(dry_run=True)
        finally:
            reader.close()
        # Three rows seeded, two unpriceable; the pass still saw all three and
        # returned a verdict shape for the remaining one.
        assert result["rows_before"] == 3
        assert result["unresolved_tier_rows"] == 2
        assert result["would_close"] == 0
        assert result["would_archive"] == 0


class TestLiveDsnGuard:
    """The ingress: a test run pointed at the deployed brain writes to it."""

    def test_deployed_database_name_is_refused(self) -> None:
        from tests._live_dsn_guard import live_dsn_refusal

        refusal = live_dsn_refusal(
            "postgresql://tapps:pw@tapps-brain-db:5432/tapps_brain",
            source="TAPPS_BRAIN_DATABASE_URL",
        )
        assert refusal is not None
        assert "tapps_brain" in refusal

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://tapps:pw@localhost:5432/tapps_brain_dev",  # CI's matrix DB
            "postgresql://postgres:pw@127.0.0.1:55432/tapps_brain_fixture",  # local fixture
            "",
        ],
    )
    def test_disposable_databases_pass(self, dsn: str) -> None:
        from tests._live_dsn_guard import live_dsn_refusal

        assert live_dsn_refusal(dsn, source="TAPPS_BRAIN_DATABASE_URL") is None

    def test_the_libpq_keyword_form_is_not_a_bypass(self) -> None:
        """psycopg accepts both DSN shapes; a guard that read only URLs would
        be sidestepped by ``dbname=tapps_brain host=...``."""
        from tests._live_dsn_guard import live_dsn_refusal

        refusal = live_dsn_refusal(
            "dbname=tapps_brain host=tapps-brain-db port=5432 user=tapps",
            source="TAPPS_TEST_POSTGRES_DSN",
        )
        assert refusal is not None

    def test_explicit_override_is_honoured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests._live_dsn_guard import ALLOW_ENV_VAR, live_dsn_refusal

        monkeypatch.setenv(ALLOW_ENV_VAR, "1")
        assert (
            live_dsn_refusal(
                "postgresql://tapps:pw@tapps-brain-db:5432/tapps_brain",
                source="TAPPS_BRAIN_DATABASE_URL",
            )
            is None
        )
