"""Protocol types for optional integrations.

These protocols allow the brain to work with external systems
(project profiling, doc lookup, path validation) without depending
on specific implementations. Consumers pass concrete objects that
satisfy these protocols.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from tapps_brain.models import RecallResult


@runtime_checkable
class HealthDimension(Protocol):
    """Pluggable diagnostics dimension (EPIC-030).

    Implementations expose a stable ``name``, a ``default_weight`` for
    composite scoring, and a ``check`` that inspects the store.
    """

    @property
    def name(self) -> str: ...

    @property
    def default_weight(self) -> float: ...

    def check(self, store: Any) -> Any: ...


@runtime_checkable
class ProjectProfileLike(Protocol):
    """Minimal interface for project profile data.

    Used by contradiction detection and memory seeding.
    """

    @property
    def project_type(self) -> str: ...

    @property
    def project_type_confidence(self) -> float: ...

    @property
    def tech_stack(self) -> Any: ...

    @property
    def test_frameworks(self) -> list[str]: ...

    @property
    def package_managers(self) -> list[str]: ...

    @property
    def ci_systems(self) -> list[str]: ...

    @property
    def has_docker(self) -> bool: ...


@runtime_checkable
class PathValidatorLike(Protocol):
    """Minimal interface for path validation / sandboxing."""

    def validate_path(
        self,
        file_path: str | Path,
        *,
        must_exist: bool = True,
        max_file_size: int | None = None,
    ) -> Path: ...


class LookupResult(Protocol):
    """Minimal interface for a doc lookup result."""

    @property
    def success(self) -> bool: ...

    @property
    def content(self) -> str: ...


@runtime_checkable
class LookupEngineLike(Protocol):
    """Minimal interface for documentation lookup.

    Used by doc_validation.py for Context7-style doc validation.
    """

    async def lookup(self, library: str, topic: str) -> LookupResult: ...


# ---------------------------------------------------------------------------
# Auto-recall protocols (Epic 003)
# ---------------------------------------------------------------------------


@runtime_checkable
class RecallHookLike(Protocol):
    """Interface for auto-recall hooks.

    Host agents implement this to integrate automatic memory recall
    before processing a user message. tapps-brain provides a default
    implementation via ``RecallOrchestrator``.
    """

    def recall(self, message: str, **kwargs: object) -> RecallResult: ...


@runtime_checkable
class CaptureHookLike(Protocol):
    """Interface for auto-capture hooks.

    Host agents implement this to capture new facts from agent
    responses and persist them back to the memory store.
    """

    def capture(self, response: str, **kwargs: object) -> list[str]: ...


@runtime_checkable
class ReportSection(Protocol):
    """Pluggable quality report section (EPIC-031)."""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...

    def should_include(self, data: Any) -> bool: ...

    def render(self, data: Any) -> str: ...


# ---------------------------------------------------------------------------
# Backend protocols for Hive and Federation storage (EPIC-055)
# ---------------------------------------------------------------------------


@runtime_checkable
class HiveBackend(Protocol):
    """Backend protocol for Hive storage.

    Decouples callers from the concrete SQLite ``HiveStore`` implementation,
    enabling alternative backends (e.g., PostgreSQL) via EPIC-055.
    """

    _db_path: Path

    def save(
        self,
        *,
        key: str,
        value: str,
        namespace: str = "universal",
        source_agent: str = "unknown",
        tier: str = "pattern",
        confidence: float = 0.6,
        source: str = "agent",
        tags: list[str] | None = None,
        valid_at: str | None = None,
        invalid_at: str | None = None,
        superseded_by: str | None = None,
        conflict_policy: Any = "supersede",
        memory_group: str | None = None,
    ) -> dict[str, Any] | None: ...

    def get(self, key: str, namespace: str = "universal") -> dict[str, Any] | None: ...

    def search(
        self,
        query: str,
        namespaces: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict[str, Any]]: ...

    def patch_confidence(
        self,
        *,
        namespace: str,
        key: str,
        confidence: float,
    ) -> bool: ...

    def get_confidence(self, *, namespace: str, key: str) -> float | None: ...

    def create_group(self, name: str, description: str = "") -> dict[str, Any]: ...

    def add_group_member(
        self, group_name: str, agent_id: str, role: str = "member"
    ) -> bool: ...

    def remove_group_member(self, group_name: str, agent_id: str) -> bool: ...

    def list_groups(self) -> list[dict[str, Any]]: ...

    def get_group_members(self, group_name: str) -> list[dict[str, Any]]: ...

    def get_agent_groups(self, agent_id: str) -> list[str]: ...

    def agent_is_group_member(self, group_name: str, agent_id: str) -> bool: ...

    def search_with_groups(
        self,
        query: str,
        agent_id: str,
        agent_namespace: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]: ...

    def record_feedback_event(
        self,
        *,
        event_id: str,
        namespace: str,
        entry_key: str | None,
        event_type: str,
        session_id: str | None,
        utility_score: float | None,
        details: dict[str, Any],
        timestamp: str,
        source_project: str | None = None,
    ) -> None: ...

    def query_feedback_events(
        self,
        *,
        namespace: str | None = None,
        entry_key: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def list_namespaces(self) -> list[str]: ...

    def count_by_namespace(self) -> dict[str, int]: ...

    def count_by_agent(self) -> dict[str, int]: ...

    def get_write_notify_state(self) -> dict[str, Any]: ...

    def wait_for_write_notify(
        self,
        *,
        since_revision: int,
        timeout_sec: float,
        poll_interval_sec: float = 0.25,
    ) -> dict[str, Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class FederationBackend(Protocol):
    """Backend protocol for Federation storage.

    Decouples callers from the concrete SQLite ``FederatedStore`` implementation,
    enabling alternative backends (e.g., PostgreSQL) via EPIC-055.
    """

    def publish(
        self,
        project_id: str,
        entries: list[Any],
        project_root: str = "",
    ) -> int: ...

    def unpublish(
        self, project_id: str, keys: list[str] | None = None
    ) -> int: ...

    def search(
        self,
        query: str,
        project_ids: list[str] | None = None,
        tags: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
        memory_group: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_project_entries(
        self,
        project_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def get_stats(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


@runtime_checkable
class AgentRegistryBackend(Protocol):
    """Backend protocol for Agent Registry.

    Decouples callers from the concrete YAML-backed ``AgentRegistry``
    implementation, enabling alternative backends via EPIC-055.
    """

    def register(self, agent: Any) -> None: ...

    def unregister(self, agent_id: str) -> bool: ...

    def get(self, agent_id: str) -> Any | None: ...

    def list_agents(self) -> list[Any]: ...

    def agents_for_domain(self, domain_name: str) -> list[Any]: ...
