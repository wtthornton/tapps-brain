"""Profile service functions (EPIC-070 STORY-070.1)."""

from __future__ import annotations

import logging
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def profile_info(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    profile = store.profile
    if profile is None:
        return {"error": "no_profile", "message": "No profile loaded."}
    return {
        "name": profile.name,
        "description": profile.description,
        "version": profile.version,
        "layers": [
            {
                "name": la.name,
                "half_life_days": la.half_life_days,
                "decay_model": la.decay_model,
                "confidence_floor": la.confidence_floor,
            }
            for la in profile.layers
        ],
        "scoring": {
            "relevance": profile.scoring.relevance,
            "confidence": profile.scoring.confidence,
            "recency": profile.scoring.recency,
            "frequency": profile.scoring.frequency,
        },
    }


def memory_profile_onboarding(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    profile = store.profile
    if profile is None:
        return {"error": "no_profile", "message": "No profile loaded."}
    from tapps_brain.onboarding import render_agent_onboarding

    return {"format": "markdown", "content": render_agent_onboarding(profile)}


def profile_switch(store: Any, project_id: str, agent_id: str, *, name: str) -> dict[str, Any]:
    try:
        from tapps_brain.profile import get_builtin_profile

        profile = get_builtin_profile(name)
        store._profile = profile
        return {
            "switched": True,
            "profile": profile.name,
            "layer_count": len(profile.layers),
        }
    except FileNotFoundError:
        from tapps_brain.profile import list_builtin_profiles

        return {
            "error": "profile_not_found",
            "message": f"No built-in profile '{name}'.",
            "available": list_builtin_profiles(),
        }
    except Exception as exc:
        logger.exception("profile_switch_error", profile=name)
        return {"error": "profile_switch_error", "message": str(exc)}
