"""Backend factory for Hive and Federation storage (PostgreSQL only).

Callers use the factory functions to get a backend instance. SQLite backends
were removed in ADR-007 — ``create_hive_backend`` / ``create_federation_backend``
require a ``postgres://`` or ``postgresql://`` DSN.

Also hosts the YAML-backed :class:`AgentRegistry`, :class:`PropagationEngine`,
and batch-push helpers that were originally in ``hive.py`` (moved during
STORY-059.2 — SQLite shared-store removal).

EPIC-055 — pluggable storage backends.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tapps_brain._protocols import (
        AgentRegistryBackend,
        FederationBackend,
        HiveBackend,
        PrivateBackend,
    )
    from tapps_brain.store import MemoryStore

from tapps_brain.agent_scope import hive_group_name_from_scope
from tapps_brain.models import AgentRegistration

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_DEFAULT_HIVE_DIR = Path.home() / ".tapps-brain" / "hive"


# ---------------------------------------------------------------------------
# YAML-backed Agent Registry (EPIC-011, moved from hive.py STORY-059.2)
# ---------------------------------------------------------------------------


class AgentRegistry:
    """YAML-backed registry of agents participating in the Hive.

    Persisted at ``~/.tapps-brain/hive/agents.yaml``.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self._path = registry_path or (_DEFAULT_HIVE_DIR / "agents.yaml")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._agents: dict[str, AgentRegistration] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "agents" not in raw:
            return
        for agent_data in raw["agents"]:
            try:
                agent = AgentRegistration(**agent_data)
                self._agents[agent.id] = agent
            except (ValidationError, TypeError) as exc:
                logger.warning(
                    "hive.agent_registry.load_skipped",
                    agent_data=agent_data,
                    error=str(exc),
                )

    def _save(self) -> None:
        data = {"agents": [a.model_dump(mode="json") for a in self._agents.values()]}
        self._path.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    def register(self, agent: AgentRegistration) -> None:
        """Add or update an agent registration."""
        self._agents[agent.id] = agent
        self._save()
        logger.info("hive.agent_registered", agent_id=agent.id, profile=agent.profile)

    def unregister(self, agent_id: str) -> bool:
        """Remove an agent. Returns True if it existed."""
        if agent_id not in self._agents:
            return False
        del self._agents[agent_id]
        self._save()
        logger.info("hive.agent_unregistered", agent_id=agent_id)
        return True

    def get(self, agent_id: str) -> AgentRegistration | None:
        """Look up an agent by ID."""
        return self._agents.get(agent_id)

    def list_agents(self) -> list[AgentRegistration]:
        """Return all registered agents."""
        return list(self._agents.values())

    def agents_for_domain(self, domain_name: str) -> list[AgentRegistration]:
        """Return agents whose profile matches the given domain name."""
        return [a for a in self._agents.values() if a.profile == domain_name]


# ---------------------------------------------------------------------------
# File-backed (YAML) Agent Registry Backend
# ---------------------------------------------------------------------------


class FileAgentRegistryBackend:
    """File-backed :class:`AgentRegistryBackend` (``agents.yaml``).

    The registry is a YAML file at ``~/.tapps-brain/hive/agents.yaml``.  Prefer
    :func:`create_agent_registry_backend` for selection.
    """

    def __init__(self, registry_path: Path | None = None) -> None:
        self._registry = AgentRegistry(registry_path=registry_path)

    def register(self, agent: AgentRegistration) -> None:
        self._registry.register(agent)

    def unregister(self, agent_id: str) -> bool:
        return self._registry.unregister(agent_id)

    def get(self, agent_id: str) -> AgentRegistration | None:
        return self._registry.get(agent_id)

    def list_agents(self) -> list[Any]:
        return self._registry.list_agents()

    def agents_for_domain(self, domain_name: str) -> list[Any]:
        return self._registry.agents_for_domain(domain_name)


# ---------------------------------------------------------------------------
# PropagationEngine (EPIC-011, moved from hive.py STORY-059.2)
# ---------------------------------------------------------------------------


class PropagationEngine:
    """Routes memory entries to the Hive based on ``agent_scope``.

    - ``private`` -> stays local (no propagation)
    - ``domain`` -> saved to Hive namespace matching the agent's profile name
    - ``hive`` -> saved to the ``universal`` namespace
    - ``group:<name>`` -> saved to Hive namespace *name* when *agent_id* is a
      member of that group (see ``HiveBackend.create_group`` / ``add_group_member``)

    Auto-propagation: if the entry's tier is in the profile's
    ``hive.auto_propagate_tiers``, scope is upgraded to ``domain``.
    If the tier is in ``hive.private_tiers``, scope is forced to ``private``.
    """

    @staticmethod
    def propagate(
        *,
        key: str,
        value: str,
        agent_scope: str,
        agent_id: str,
        agent_profile: str,
        tier: str,
        confidence: float,
        source: str,
        tags: list[str] | None,
        hive_store: HiveBackend,
        auto_propagate_tiers: list[str] | None = None,
        private_tiers: list[str] | None = None,
        bypass_profile_hive_rules: bool = False,
        dry_run: bool = False,
        memory_group: str | None = None,
    ) -> dict[str, Any] | None:
        """Propagate a memory entry to the Hive if appropriate.

        Returns the saved Hive entry dict, or None if the entry stayed private.
        When *dry_run* is True, does not write; returns a minimal dict
        with ``namespace`` and ``key`` if propagation would occur.

        *bypass_profile_hive_rules*: when True, ignore *private_tiers* and
        *auto_propagate_tiers* so explicit *agent_scope* from the caller wins
        (used for CLI/MCP batch push — GitHub #18).
        """
        effective_scope = agent_scope

        if not bypass_profile_hive_rules:
            # Private tiers override everything
            if private_tiers and tier in private_tiers:
                effective_scope = "private"
            # Auto-propagation for configured tiers
            elif (
                auto_propagate_tiers
                and tier in auto_propagate_tiers
                and effective_scope == "private"
            ):
                effective_scope = "domain"

        if effective_scope == "private":
            return None

        group_ns = hive_group_name_from_scope(effective_scope)
        if group_ns is not None:
            if not hive_store.agent_is_group_member(group_ns, agent_id):
                logger.warning(
                    "hive.propagate.group_denied",
                    group_name=group_ns,
                    agent_id=agent_id,
                    key=key,
                    reason="not_a_member",
                )
                return None
            namespace = group_ns
        elif effective_scope == "hive":
            namespace = "universal"
        elif effective_scope == "domain":
            namespace = agent_profile
        else:
            logger.warning(
                "hive.propagate.unknown_scope",
                effective_scope=effective_scope,
                agent_id=agent_id,
                key=key,
                fallback="domain",
            )
            namespace = agent_profile

        if dry_run:
            logger.debug(
                "hive.propagate_dry_run",
                key=key,
                namespace=namespace,
                agent_id=agent_id,
            )
            return {"namespace": namespace, "key": key, "dry_run": True}

        result = hive_store.save(
            key=key,
            value=value,
            namespace=namespace,
            source_agent=agent_id,
            tier=tier,
            confidence=confidence,
            source=source,
            tags=tags,
            memory_group=memory_group,
        )

        logger.info(
            "hive.propagated",
            key=key,
            scope=effective_scope,
            namespace=namespace,
            agent_id=agent_id,
        )

        return result


# ---------------------------------------------------------------------------
# Batch push helpers (GitHub #18, moved from hive.py STORY-059.2)
# ---------------------------------------------------------------------------


def select_local_entries_for_hive_push(
    store: MemoryStore,
    *,
    push_all: bool = False,
    tags: list[str] | None = None,
    tier: str | None = None,
    keys: list[str] | None = None,
    include_superseded: bool = False,
) -> list[Any]:
    """Select project memories for batch Hive push (GitHub #18).

    *keys*: explicit entry keys (highest priority). *push_all*: all entries,
    optionally narrowed by *tier* and/or *tags*. Otherwise require at least one
    of *tier* or *tags*.

    Raises:
        ValueError: When selection criteria are empty or *tier* is invalid.
    """
    from tapps_brain.models import MemoryTier

    if keys:
        resolved: list[Any] = []
        for k in keys:
            entry = store.get(k)
            if entry is not None:
                resolved.append(entry)
        return resolved

    tier_enum: MemoryTier | None = None
    if tier is not None:
        try:
            tier_enum = MemoryTier(tier)
        except ValueError as exc:
            msg = f"Unknown tier '{tier}'"
            raise ValueError(msg) from exc

    if push_all:
        return store.list_all(
            tier=tier_enum,
            tags=tags,
            include_superseded=include_superseded,
        )

    if tier_enum is None and not tags:
        msg = "Specify keys, push_all=True, and/or tier/tags filters"
        raise ValueError(msg)

    return store.list_all(
        tier=tier_enum,
        tags=tags,
        include_superseded=include_superseded,
    )


def push_memory_entries_to_hive(
    entries: Sequence[Any],
    *,
    hive_store: HiveBackend,
    agent_id: str,
    agent_profile: str,
    agent_scope: str,
    auto_propagate_tiers: list[str] | None = None,
    private_tiers: list[str] | None = None,
    bypass_profile_hive_rules: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Push local *entries* to the Hive using :meth:`PropagationEngine.propagate`.

    Returns a JSON-serializable report: ``pushed``, ``skipped``, ``failed``,
    each a list of per-key records. *skipped* means propagation returned
    ``None`` (would stay private under current rules).
    """
    from tapps_brain.models import MemorySource, tier_str

    pushed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for entry in entries:
        tier_val = tier_str(entry.tier)
        src = entry.source
        source_val = src.value if isinstance(src, MemorySource) else str(src)
        try:
            result = PropagationEngine.propagate(
                key=entry.key,
                value=entry.value,
                agent_scope=agent_scope,
                agent_id=agent_id,
                agent_profile=agent_profile,
                tier=tier_val,
                confidence=float(entry.confidence),
                source=source_val,
                tags=entry.tags,
                hive_store=hive_store,
                auto_propagate_tiers=auto_propagate_tiers,
                private_tiers=private_tiers,
                bypass_profile_hive_rules=bypass_profile_hive_rules,
                dry_run=dry_run,
                memory_group=entry.memory_group,
            )
        except Exception as exc:
            logger.warning(
                "hive.push_entry_failed",
                key=entry.key,
                error=str(exc),
                exc_info=True,
            )
            failed.append({"key": entry.key, "error": str(exc)})
            continue
        if result is None:
            skipped.append({"key": entry.key, "reason": "not_propagated_private_rules"})
        else:
            ns = str(result.get("namespace", ""))
            pushed.append({"key": entry.key, "namespace": ns})

    return {
        "dry_run": dry_run,
        "agent_scope": agent_scope,
        "count_selected": len(entries),
        "count_pushed": len(pushed),
        "count_skipped": len(skipped),
        "count_failed": len(failed),
        "pushed": pushed,
        "skipped": skipped,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def create_hive_backend(
    dsn_or_path: str | None = None,
    *,
    encryption_key: str | None = None,
) -> HiveBackend:
    """Create a :class:`PostgresHiveBackend`.

    A **PostgreSQL** DSN is required (ADR-007).

    ``encryption_key`` is accepted for API compatibility with older callers but
    is ignored — at-rest encryption is delegated to the storage layer
    (e.g. ``pg_tde``), not the application.
    """
    _ = encryption_key  # legacy SQLCipher knob — Postgres uses pg_tde instead
    if dsn_or_path is None or not str(dsn_or_path).strip():
        msg = (
            "create_hive_backend() requires TAPPS_BRAIN_HIVE_DSN "
            "(postgres:// or postgresql://). SQLite backends are removed (ADR-007)."
        )
        raise ValueError(msg)
    if not str(dsn_or_path).startswith(("postgres://", "postgresql://")):
        msg = "Hive backend requires a PostgreSQL DSN. SQLite backends are removed (ADR-007)."
        raise ValueError(msg)
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_hive import PostgresHiveBackend

    cm = PostgresConnectionManager(dsn_or_path)
    return PostgresHiveBackend(cm)


def create_federation_backend(dsn_or_path: str | None = None) -> FederationBackend:
    """Create a :class:`PostgresFederationBackend`.

    A **PostgreSQL** DSN is required. SQLite Federation backends were removed (ADR-007).
    """
    if dsn_or_path is None or not str(dsn_or_path).strip():
        msg = (
            "create_federation_backend() requires TAPPS_BRAIN_FEDERATION_DSN "
            "(postgres:// or postgresql://). SQLite backends are removed (ADR-007)."
        )
        raise ValueError(msg)
    if not str(dsn_or_path).startswith(("postgres://", "postgresql://")):
        msg = "Federation backend requires a PostgreSQL DSN. SQLite backends are removed (ADR-007)."
        raise ValueError(msg)
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_federation import PostgresFederationBackend

    cm = PostgresConnectionManager(dsn_or_path)
    return PostgresFederationBackend(cm)


def resolve_hive_backend_from_env(
    *,
    encryption_key: str | None = None,
) -> HiveBackend | None:
    """Return a Postgres :class:`HiveBackend` from ``TAPPS_BRAIN_HIVE_DSN``, or ``None``.

    Falls back to ``TAPPS_BRAIN_DATABASE_URL`` (v3 unified DSN) when
    ``TAPPS_BRAIN_HIVE_DSN`` is not set.  When neither var is set, returns
    ``None`` (no Hive). Never opens a SQLite Hive (ADR-007).
    """
    import os

    dsn = (
        os.environ.get("TAPPS_BRAIN_HIVE_DSN") or os.environ.get("TAPPS_BRAIN_DATABASE_URL") or ""
    ).strip()
    if not dsn:
        return None
    return create_hive_backend(dsn, encryption_key=encryption_key)


def create_private_backend(
    dsn: str,
    *,
    project_id: str,
    agent_id: str,
) -> PrivateBackend:
    """Create a :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`.

    A **PostgreSQL** DSN is required. SQLite private-memory backends are not
    supported in v3 (ADR-007).

    Args:
        dsn: PostgreSQL DSN (``postgres://`` or ``postgresql://``).
        project_id: Canonical project identifier (e.g. a hash of the project root
            path).  All operations for this backend are scoped to this project.
        agent_id: Agent identifier (e.g. ``'claude-code'``).

    Raises:
        ValueError: When *dsn* is empty or does not use a Postgres scheme.
    """
    if not dsn or not dsn.strip():
        msg = (
            "create_private_backend() requires a PostgreSQL DSN "
            "(postgres:// or postgresql://). SQLite private backends are not "
            "supported in v3 (ADR-007)."
        )
        raise ValueError(msg)
    if not dsn.startswith(("postgres://", "postgresql://")):
        msg = (
            "Private memory backend requires a PostgreSQL DSN. "
            "SQLite backends are not supported in v3 (ADR-007)."
        )
        raise ValueError(msg)

    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm = PostgresConnectionManager(dsn)
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def resolve_private_backend_from_env(
    project_id: str,
    agent_id: str,
) -> PrivateBackend | None:
    """Return a :class:`~tapps_brain.postgres_private.PostgresPrivateBackend` from env.

    Reads ``TAPPS_BRAIN_DATABASE_URL`` (v3 unified DSN) and falls back to
    ``TAPPS_BRAIN_HIVE_DSN`` for backward compatibility.  Returns ``None`` when
    neither env var is set; :class:`~tapps_brain.store.MemoryStore` then raises
    ``ValueError`` because v3 is Postgres-only (ADR-007).

    Args:
        project_id: Canonical project identifier (see :func:`derive_project_id`).
        agent_id: Agent identifier string.
    """
    import os

    dsn = (
        os.environ.get("TAPPS_BRAIN_DATABASE_URL") or os.environ.get("TAPPS_BRAIN_HIVE_DSN") or ""
    ).strip()
    if not dsn:
        return None
    try:
        return create_private_backend(dsn, project_id=project_id, agent_id=agent_id)
    except ValueError:
        logger.warning(
            "private_backend.resolve_from_env_failed",
            dsn_prefix=dsn[:20] if dsn else "",
        )
        return None


def derive_project_id(project_root: Path | str) -> str:
    """Derive a stable ``project_id`` string from a filesystem path.

    Uses the first 16 hex chars of SHA-256(absolute-path) to keep IDs short
    and path-agnostic.  Stable across sessions for the same root.

    Example::

        >>> derive_project_id("/home/user/myrepo")
        'a3f1c9e207b4d852'
    """
    import hashlib

    return hashlib.sha256(str(Path(project_root).resolve()).encode()).hexdigest()[:16]


def create_agent_registry_backend(
    registry_path: str | None = None,
) -> AgentRegistryBackend:
    """Create an :class:`AgentRegistryBackend`.

    - ``None`` or a file path -> :class:`FileAgentRegistryBackend` (YAML file)
    - ``postgres://...`` or ``postgresql://...`` -> :class:`PostgresAgentRegistry` (EPIC-055)
    """
    if registry_path is not None and registry_path.startswith(("postgres://", "postgresql://")):
        from tapps_brain.postgres_connection import PostgresConnectionManager
        from tapps_brain.postgres_hive import PostgresAgentRegistry

        cm = PostgresConnectionManager(registry_path)
        return PostgresAgentRegistry(cm)
    path = Path(registry_path) if registry_path else None
    return FileAgentRegistryBackend(registry_path=path)
