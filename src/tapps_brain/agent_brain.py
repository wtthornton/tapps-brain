"""Unified Agent API — a simple facade hiding all tapps-brain complexity.

Usage::

    with AgentBrain(agent_id="frontend-dev", project_dir="/app") as brain:
        brain.remember("Use Tailwind for styling")
        results = brain.recall("how to style components?")
        brain.learn_from_success("Styled the sidebar component")

EPIC-057 — Unified Agent API.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import structlog

from tapps_brain.backends import create_hive_backend
from tapps_brain.store import MemoryStore

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_csv_env(var: str) -> list[str]:
    """Parse a comma-separated environment variable into a list of strings."""
    raw = os.environ.get(var, "")
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else []


def _content_key(content: str) -> str:
    """Generate a deterministic key from content."""
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    # Create a slug from first few words
    words = content.lower().split()[:4]
    slug = "-".join(
        w[:12] for w in words if w.isalnum() or w.replace("-", "").isalnum()
    )[:60]
    return f"{slug}-{h}" if slug else h


# ---------------------------------------------------------------------------
# AgentBrain facade
# ---------------------------------------------------------------------------


class AgentBrain:
    """Simple, agent-facing API that wraps MemoryStore + HiveBackend.

    Agents and LLMs use this class.  They never think about backends,
    scopes, propagation, or conflict policies.
    """

    def __init__(
        self,
        agent_id: str | None = None,
        project_dir: str | Path | None = None,
        *,
        groups: list[str] | None = None,
        expert_domains: list[str] | None = None,
        profile: str = "repo-brain",
        hive_dsn: str | None = None,
        encryption_key: str | None = None,
    ) -> None:
        # Resolve from env vars if not provided
        self._agent_id = agent_id or os.environ.get("TAPPS_BRAIN_AGENT_ID") or None
        _project_dir = (
            project_dir or os.environ.get("TAPPS_BRAIN_PROJECT_DIR") or str(Path.cwd())
        )
        self._project_dir = Path(_project_dir).resolve()
        _hive_dsn = hive_dsn or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
        _groups = groups or _parse_csv_env("TAPPS_BRAIN_GROUPS")
        _expert_domains = expert_domains or _parse_csv_env("TAPPS_BRAIN_EXPERT_DOMAINS")

        # Create HiveBackend if DSN available or default SQLite
        self._hive = None
        try:
            self._hive = create_hive_backend(_hive_dsn, encryption_key=encryption_key)
        except Exception:
            logger.warning("agent_brain.hive_init_failed", exc_info=True)

        # Create MemoryStore
        self._store = MemoryStore(
            self._project_dir,
            agent_id=self._agent_id,
            hive_store=self._hive,
            hive_agent_id=self._agent_id or "unknown",
            groups=_groups,
            expert_domains=_expert_domains,
            encryption_key=encryption_key,
        )

        # Internal recall tracking for learn_from_success
        self._last_recalled_keys: list[str] = []
        self._task_id: str | None = None
        self._session_id: str | None = None
        self._closed = False

    # --- Properties -----------------------------------------------------------

    @property
    def agent_id(self) -> str | None:
        """Return the agent identity, or ``None``."""
        return self._agent_id

    @property
    def store(self) -> MemoryStore:
        """Return the underlying ``MemoryStore``."""
        return self._store

    @property
    def hive(self) -> Any:
        """Return the ``HiveBackend``, or ``None``."""
        return self._hive

    @property
    def groups(self) -> list[str]:
        """Return declared group memberships."""
        return self._store.groups

    @property
    def expert_domains(self) -> list[str]:
        """Return declared expert domains."""
        return self._store.expert_domains

    # --- Context Manager (STORY-057.7) ----------------------------------------

    def __enter__(self) -> AgentBrain:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying store and Hive backend."""
        if self._closed:
            return
        self._closed = True
        if hasattr(self._store, "close"):
            self._store.close()
        if self._hive is not None and hasattr(self._hive, "close"):
            self._hive.close()

    # --- Core methods (STORY-057.2) -------------------------------------------

    def remember(
        self,
        fact: str,
        *,
        tier: str = "procedural",
        share: bool = False,
        share_with: str | list[str] | None = None,
    ) -> str:
        """Save a memory.  Returns the generated key."""
        key = _content_key(fact)

        # Determine agent_scope
        agent_scope = "private"
        if share:
            agent_scope = "group"  # share with all declared groups
        elif share_with == "hive":
            agent_scope = "hive"
        elif isinstance(share_with, str):
            agent_scope = f"group:{share_with}"
        elif isinstance(share_with, list):
            # Save to each specified group
            for group in share_with:
                self._store.save(
                    key=key, value=fact, tier=tier, agent_scope=f"group:{group}"
                )
            return key

        self._store.save(key=key, value=fact, tier=tier, agent_scope=agent_scope)
        return key

    def recall(
        self,
        query: str,
        *,
        max_results: int = 5,
        scope: str = "all",
    ) -> list[dict[str, Any]]:
        """Recall memories matching *query*.  Returns list of result dicts."""
        entries = self._store.search(query)

        # Convert MemoryEntry objects to dicts and limit results
        results: list[dict[str, Any]] = []
        for entry in entries[:max_results]:
            if isinstance(entry, dict):
                results.append(entry)
            else:
                results.append(
                    {
                        "key": entry.key,
                        "value": entry.value,
                        "tier": str(entry.tier),
                        "confidence": entry.confidence,
                        "tags": list(entry.tags) if entry.tags else [],
                    }
                )

        # Track for learn_from_success
        self._last_recalled_keys = [r.get("key", "") for r in results]

        return results

    def forget(self, key: str) -> bool:
        """Archive a memory.  Returns ``True`` if found."""
        entry = self._store.get(key)
        if entry is None:
            return False
        self._store.delete(key)
        return True

    # --- Learning methods (STORY-057.3) ---------------------------------------

    def set_task_context(
        self, task_id: str, session_id: str | None = None
    ) -> None:
        """Set the current task context for subsequent learn calls."""
        self._task_id = task_id
        self._session_id = session_id

    def learn_from_success(
        self,
        task_description: str,
        *,
        task_id: str | None = None,
        boost: float = 0.1,
    ) -> None:
        """Record a successful task outcome.

        Saves the experience and reinforces any recently recalled memories.
        """
        tid = task_id or self._task_id
        key = _content_key(f"success-{task_description}")
        tags = ["success"]
        if tid:
            tags.append(f"task:{tid}")
        self._store.save(key=key, value=task_description, tier="procedural", tags=tags)

        # Reinforce recalled memories
        for recalled_key in self._last_recalled_keys:
            entry = self._store.get(recalled_key)
            if entry is not None:
                try:
                    self._store.reinforce(recalled_key, confidence_boost=boost)
                except KeyError:
                    pass

    def learn_from_failure(
        self,
        description: str,
        *,
        task_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Record a failed task outcome to avoid repeating mistakes."""
        tid = task_id or self._task_id
        key = _content_key(f"failure-{description}")
        value = description
        if error:
            value = f"{description}\n\nError: {error}"
        tags = ["failure"]
        if tid:
            tags.append(f"task:{tid}")
        self._store.save(key=key, value=value, tier="procedural", tags=tags)
