"""Agent registry service functions (EPIC-070 STORY-070.1).

Exposes agent registration, creation, listing, and deletion via the MCP
``agent_*`` tools and the HTTP adapter. Delegates to ``AgentRegistry`` and
``AgentRegistration`` from ``tapps_brain.backends`` / ``tapps_brain.models``.
"""

from __future__ import annotations

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
    """Register a new agent with the given profile and skills in the AgentRegistry."""
    if not new_agent_id or not new_agent_id.strip():
        return {"error": "invalid_agent_id", "message": "agent_id must not be empty"}
    try:
        from tapps_brain.backends import resolve_agent_registry
        from tapps_brain.models import AgentRegistration

        registry = resolve_agent_registry(getattr(store, "_hive_store", None))
        skill_list = [s.strip() for s in skills.split(",") if s.strip()]
        agent = AgentRegistration(id=new_agent_id, profile=profile, skills=skill_list)
        registry.register(agent)
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_register")
        return {"error": "registry_error", "message": str(exc)}
    return {
        "registered": True,
        "agent_id": new_agent_id,
        "profile": profile,
        "skills": skill_list,
    }


def agent_create(
    store: Any,
    project_id: str,
    agent_id: str,
    *,
    new_agent_id: str,
    profile: str = "repo-brain",
    skills: str = "",
) -> dict[str, Any]:
    """Create and register a new agent, validating the profile exists before registration."""
    if not new_agent_id or not new_agent_id.strip():
        return {"error": "invalid_agent_id", "message": "agent_id must not be empty"}
    try:
        from tapps_brain.backends import resolve_agent_registry
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
        registry = resolve_agent_registry(getattr(store, "_hive_store", None))
        registry.register(agent)

        namespace = profile

        layer_names = [layer.name for layer in prof.layers]
        profile_summary = {
            "name": prof.name,
            "version": prof.version,
            "layers": layer_names,
            "description": prof.description,
        }

    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_create")
        return {"error": "agent_create_error", "message": str(exc)}
    return {
        "created": True,
        "agent_id": new_agent_id,
        "profile": profile,
        "namespace": namespace,
        "skills": skill_list,
        "profile_summary": profile_summary,
    }


def agent_list(store: Any, project_id: str, agent_id: str) -> dict[str, Any]:
    """Return all registered agents with their profile and skill metadata."""
    try:
        from tapps_brain.backends import resolve_agent_registry

        registry = resolve_agent_registry(getattr(store, "_hive_store", None))
        agents = [a.model_dump(mode="json") for a in registry.list_agents()]
        return {"agents": agents, "count": len(agents)}
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_list")
        return {"error": "registry_error", "message": str(exc)}


def agent_delete(
    store: Any, project_id: str, agent_id: str, *, target_agent_id: str
) -> dict[str, Any]:
    """Unregister an agent by ID; returns deleted=False if the agent was not found."""
    try:
        from tapps_brain.backends import resolve_agent_registry

        registry = resolve_agent_registry(getattr(store, "_hive_store", None))
        removed = registry.unregister(target_agent_id)
    except Exception as exc:
        logger.exception("hive_tool_error", tool="agent_delete")
        return {"error": "registry_error", "message": str(exc)}
    if removed:
        return {"deleted": True, "agent_id": target_agent_id}
    return {
        "deleted": False,
        "agent_id": target_agent_id,
        "message": f"Agent '{target_agent_id}' not found.",
    }
