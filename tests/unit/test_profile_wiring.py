"""Pin profile blocks that are wired into MemoryStore behavior (run 4 audit).

These blocks were previously parsed-but-dead configuration:
``source_confidence``, ``limits.max_key_length/max_value_length/max_tags``,
``recall``, and ``hive.groups`` / ``hive.expert_domains``.
"""

from __future__ import annotations

import pytest

from tapps_brain.profile import (
    HiveConfig,
    LayerDefinition,
    LimitsConfig,
    MemoryProfile,
    RecallProfileConfig,
)
from tapps_brain.store import MemoryStore


def _profile(**overrides) -> MemoryProfile:
    base = {
        "name": "test-wiring",
        "layers": [
            LayerDefinition(name="architectural", half_life_days=180),
            LayerDefinition(name="pattern", half_life_days=60),
        ],
    }
    base.update(overrides)
    return MemoryProfile(**base)


class TestSourceConfidenceWiring:
    def test_profile_source_default_applied(self, tmp_path):
        profile = _profile(
            source_confidence={"agent": 0.77, "human": 0.95, "inferred": 0.4, "system": 0.9}
        )
        store = MemoryStore(tmp_path, profile=profile)
        try:
            entry = store.save("wiring-conf", "some value", tier="pattern", source="agent")
            assert entry.confidence == pytest.approx(0.77)
        finally:
            store.close()

    def test_explicit_confidence_wins_over_profile(self, tmp_path):
        profile = _profile(
            source_confidence={"agent": 0.77, "human": 0.95, "inferred": 0.4, "system": 0.9}
        )
        store = MemoryStore(tmp_path, profile=profile)
        try:
            entry = store.save(
                "wiring-conf-explicit", "v", tier="pattern", source="agent", confidence=0.5
            )
            assert entry.confidence == pytest.approx(0.5)
        finally:
            store.close()


class TestProfileLimitsWiring:
    def test_value_over_profile_limit_rejected(self, tmp_path):
        profile = _profile(limits=LimitsConfig(max_value_length=50))
        store = MemoryStore(tmp_path, profile=profile)
        try:
            with pytest.raises(ValueError, match="profile limit"):
                store.save("wiring-too-long", "x" * 60, tier="pattern", source="agent")
        finally:
            store.close()

    def test_tags_over_profile_limit_rejected(self, tmp_path):
        profile = _profile(limits=LimitsConfig(max_tags=2))
        store = MemoryStore(tmp_path, profile=profile)
        try:
            with pytest.raises(ValueError, match="profile limit"):
                store.save("wiring-tags", "v", tier="pattern", source="agent", tags=["a", "b", "c"])
        finally:
            store.close()

    def test_within_profile_limits_accepted(self, tmp_path):
        profile = _profile(limits=LimitsConfig(max_value_length=50, max_tags=2))
        store = MemoryStore(tmp_path, profile=profile)
        try:
            entry = store.save(
                "wiring-ok", "short value", tier="pattern", source="agent", tags=["a", "b"]
            )
            assert entry.key == "wiring-ok"
        finally:
            store.close()


class TestRecallConfigWiring:
    def test_profile_recall_block_threaded_into_orchestrator(self, tmp_path):
        profile = _profile(recall=RecallProfileConfig(min_score=0.42, default_token_budget=1234))
        store = MemoryStore(tmp_path, profile=profile)
        try:
            orch = store._recall_get_orchestrator()
            assert orch._config.min_score == pytest.approx(0.42)
            assert orch._config.max_tokens == 1234
        finally:
            store.close()


class TestHiveMembershipWiring:
    def test_profile_groups_used_as_fallback(self, tmp_path):
        profile = _profile(hive=HiveConfig(groups=["team-a"], expert_domains=["css"]))
        store = MemoryStore(tmp_path, profile=profile)
        try:
            assert store._groups == ["team-a"]
            assert store._expert_domains == ["css"]
        finally:
            store.close()

    def test_constructor_args_win_over_profile(self, tmp_path):
        profile = _profile(hive=HiveConfig(groups=["team-a"], expert_domains=["css"]))
        store = MemoryStore(
            tmp_path, profile=profile, groups=["ctor-group"], expert_domains=["react"]
        )
        try:
            assert store._groups == ["ctor-group"]
            assert store._expert_domains == ["react"]
        finally:
            store.close()
