"""Agent registry service functions (EPIC-070 STORY-070.1)."""

from __future__ import annotations

import logging
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def agent_register(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    new_agent_id: str,
    profile: str = "repo-brain",
    skills: str = "",
) -> dict[str, Any]:
    if not new_agent_id or not new_agent_id.strip():
        return {"error": "invalid_agent_id", "message": "agent_id must not be empty"}
    try:
        from tapps_brain.backends import AgentRegistry
        from tapps_brain.models import AgentRegistration

        registry = AgentRegistry()
        skill_list = [s.strip() for s in skills.split(",") if s.strip()]
        agent = AgentRegistration(id=new_agent_id, profile=profile, skills=skill_list)
        registry.register(agent)
        return {
            "registered": True,
            "agent_id": new_agent_id,
            "profile": profile,
            "skills": skill_list,
        }
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_register")
        return {"error": "registry_error", "message": str(exc)}


def agent_create(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    new_agent_id: str,
    profile: str = "repo-brain",
    skills: str = "",
) -> dict[str, Any]:
    if not new_agent_id or not new_agent_id.strip():
        return {"error": "invalid_agent_id", "message": "agent_id must not be empty"}
    try:
        from tapps_brain.backends import AgentRegistry
        from tapps_brain.models import AgentRegistration
        from tapps_brain.profile import get_builtin_profile, list_builtin_profiles

        try:
            prof = get_builtin_profile(profile)
        except FileNotFoundError:
            available = list_builtin_profiles()
            return {
                "error": "invalid_profile",
                "message": f"Profile '{profile}' not found.",
                "available_profiles": available,
            }

        skill_list = [s.strip() for s in skills.split(",") if s.strip()]
        agent = AgentRegistration(id=new_agent_id, profile=profile, skills=skill_list)
        registry = AgentRegistry()
        registry.register(agent)

        namespace = profile

        layer_names = [layer.name for layer in prof.layers]
        profile_summary = {
            "name": prof.name,
            "version": prof.version,
            "layers": layer_names,
            "description": prof.description,
        }

        return {
            "created": True,
            "agent_id": new_agent_id,
            "profile": profile,
            "namespace": namespace,
            "skills": skill_list,
            "profile_summary": profile_summary,
        }
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_create")
        return {"error": "agent_create_error", "message": str(exc)}


def agent_list(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    try:
        from tapps_brain.backends import AgentRegistry

        registry = AgentRegistry()
        agents = [a.model_dump(mode="json") for a in registry.list_agents()]
        return {"agents": agents, "count": len(agents)}
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_list")
        return {"error": "registry_error", "message": str(exc)}


def agent_delete(
    store: Any, project_id: str, agent_id: str, *, target_agent_id: str
) -> dict[str, Any]:
    try:
        from tapps_brain.backends import AgentRegistry

        registry = AgentRegistry()
        removed = registry.unregister(target_agent_id)
        if removed:
            return {"deleted": True, "agent_id": target_agent_id}
        return {
            "deleted": False,
            "agent_id": target_agent_id,
            "message": f"Agent '{target_agent_id}' not found.",
        }
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_delete")
        return {"error": "registry_error", "message": str(exc)}
