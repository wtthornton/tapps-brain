"""Tests for profile-driven onboarding copy (GitHub #45)."""

from __future__ import annotations

from tapps_brain.onboarding import render_agent_onboarding
from tapps_brain.profile import get_builtin_profile


def test_render_agent_onboarding_repo_brain() -> None:
    p = get_builtin_profile("repo-brain")
    text = render_agent_onboarding(p)
    assert "repo-brain" in text
    assert "Layers (tiers)" in text
    assert "architectural" in text
    assert "Retrieval scoring" in text
    assert "Hive (shared memory)" in text


def test_render_agent_onboarding_extended_scoring_weights() -> None:
    from tapps_brain.profile import ScoringConfig

    base = get_builtin_profile("repo-brain")
    sc = ScoringConfig(
        relevance=0.2,
        confidence=0.2,
        recency=0.15,
        frequency=0.15,
        graph_centrality=0.15,
        provenance_trust=0.15,
    )
    p = base.model_copy(update={"scoring": sc})
    text = render_agent_onboarding(p)
    assert "Extended" in text
