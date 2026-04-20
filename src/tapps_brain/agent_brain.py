"""Unified Agent API — a simple facade hiding all tapps-brain complexity.

Usage::

    with AgentBrain(agent_id="frontend-dev", project_dir="/app") as brain:
        brain.remember("Use Tailwind for styling")
        results = brain.recall("how to style components?")
        brain.learn_from_success("Styled the sidebar component")

EPIC-057 — Unified Agent API.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.backends import create_hive_backend
from tapps_brain.models import MemoryTier
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from tapps_brain._protocols import HiveBackend

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Typed exception hierarchy (STORY-060.2)
# ---------------------------------------------------------------------------


class BrainError(Exception):
    """Base class for all tapps-brain exceptions raised through the public API.

    Catch this to handle any tapps-brain error generically.
    """


class BrainConfigError(BrainError):
    """Raised when the agent cannot be started due to a configuration problem.

    These errors occur at construction time or on first use and will not
    resolve without an operator change.  Examples:

    * Missing or malformed ``TAPPS_BRAIN_DATABASE_URL`` or
      ``TAPPS_BRAIN_HIVE_DSN`` (non-Postgres scheme, e.g. ``sqlite://``).
    * ``TAPPS_BRAIN_STRICT=1`` is set and a required DSN is absent.
    * An unknown / unsupported profile name is passed to the constructor.

    Recovery: fix the environment variable or constructor argument and restart.
    """


class BrainTransientError(BrainError):
    """Raised when an operation fails due to a transient infrastructure problem.

    The failure *may* resolve on retry.  Examples:

    * Postgres connection refused or timed out during ``remember`` / ``recall``.
    * Pool exhaustion under high concurrency.
    * A network hiccup during Hive propagation.

    Recovery: retry with exponential back-off; alert if failures persist.
    """


class BrainValidationError(BrainError, ValueError):
    """Raised when a caller-supplied value fails validation.

    These errors will not resolve without a code change.  Examples:

    * ``tier`` passed to ``remember()`` is not one of the canonical values
      (``"architectural"``, ``"pattern"``, ``"procedural"``, ``"context"``).
    * A ``share_with`` value is an empty string.
    * ``max_results`` is non-positive.

    ``BrainValidationError`` also inherits from :class:`ValueError` so
    existing code that catches ``ValueError`` continues to work.
    """


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
    slug = "-".join(w[:12] for w in words if w.isalnum() or w.replace("-", "").isalnum())[:60]
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
        _project_dir = project_dir or os.environ.get("TAPPS_BRAIN_PROJECT_DIR") or str(Path.cwd())
        self._project_dir = Path(_project_dir).resolve()
        _hive_dsn = hive_dsn or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
        _groups = groups or _parse_csv_env("TAPPS_BRAIN_GROUPS")
        _expert_domains = expert_domains or _parse_csv_env("TAPPS_BRAIN_EXPERT_DOMAINS")

        # Postgres Hive only (ADR-007); no SQLite fallback.
        self._hive = None
        if _hive_dsn:
            try:
                self._hive = create_hive_backend(_hive_dsn, encryption_key=encryption_key)
            except Exception:
                logger.warning("agent_brain.hive_init_failed", exc_info=True)

        # STORY-066.8: Auto-migrate private schema if TAPPS_BRAIN_AUTO_MIGRATE=1.
        # MemoryStore.__init__ also performs this check, but we call it here so
        # AgentBrain users see the error before the backend is constructed.
        _auto_migrate_dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
        if _auto_migrate_dsn and _auto_migrate_dsn.startswith(("postgres://", "postgresql://")):
            try:
                from tapps_brain.postgres_migrations import maybe_auto_migrate_private

                maybe_auto_migrate_private(_auto_migrate_dsn)
            except Exception:
                # MigrationDowngradeError and ImportError propagate; other
                # transient errors are logged and deferred to the store.
                raise

        # ADR-007: resolve the Postgres private backend from
        # TAPPS_BRAIN_DATABASE_URL.  No SQLite fallback — when the env var is
        # unset, MemoryStore.__init__ raises ValueError.
        _private_backend = None
        _effective_agent_id = self._agent_id or "unknown"
        try:
            from tapps_brain.backends import derive_project_id, resolve_private_backend_from_env

            # EPIC-069 / ADR-010: honor TAPPS_BRAIN_PROJECT (human-readable
            # slug) before the legacy path-hash.  Matches MemoryStore.__init__
            # so library-path users hit the project registry by slug, not by
            # per-directory hash.
            _env_project = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
            if _env_project:
                from tapps_brain.project_resolver import validate_project_id

                _project_id = validate_project_id(_env_project)
            else:
                _project_id = derive_project_id(self._project_dir)
            _private_backend = resolve_private_backend_from_env(_project_id, _effective_agent_id)
        except Exception:
            logger.warning("agent_brain.private_backend_init_failed", exc_info=True)
            _private_backend = None

        # Create MemoryStore
        self._store = MemoryStore(
            self._project_dir,
            agent_id=self._agent_id,
            hive_store=self._hive,
            hive_agent_id=_effective_agent_id,
            groups=_groups,
            expert_domains=_expert_domains,
            encryption_key=encryption_key,
            private_backend=_private_backend,
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
    def hive(self) -> HiveBackend | None:
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

    def __exit__(self, *args: object) -> None:
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
        try:
            MemoryTier(tier)
        except ValueError as exc:
            valid = [t.value for t in MemoryTier]
            raise BrainValidationError(f"Invalid tier {tier!r}: must be one of {valid}") from exc
        if isinstance(share_with, str) and not share_with.strip():
            raise BrainValidationError(
                "share_with must be a non-empty group name or 'hive'"
            )

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
            if not share_with or any(not g.strip() for g in share_with):
                raise BrainValidationError(
                    "share_with list must not be empty and must not contain blank group names"
                )
            # Save to each specified group
            for group in share_with:
                self._store.save(key=key, value=fact, tier=tier, agent_scope=f"group:{group}")
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
        if max_results <= 0:
            raise BrainValidationError("max_results must be a positive integer")
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

    def set_task_context(self, task_id: str, session_id: str | None = None) -> None:
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
                with contextlib.suppress(KeyError):
                    self._store.reinforce(recalled_key, confidence_boost=boost)

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
