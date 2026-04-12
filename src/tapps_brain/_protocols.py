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

    from tapps_brain.models import MemoryEntry, RecallResult
    from tapps_brain.relations import RelationEntry


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

    Decouples callers from the concrete backend implementation.
    The only supported backend in v3 is ``PostgresHiveBackend`` (ADR-007).
    Created via :func:`tapps_brain.backends.create_hive_backend`.
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

    def add_group_member(self, group_name: str, agent_id: str, role: str = "member") -> bool: ...

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

    Decouples callers from the concrete backend implementation.
    The only supported backend in v3 is ``PostgresFederationBackend`` (ADR-007).
    Created via :func:`tapps_brain.backends.create_federation_backend`.
    """

    def publish(
        self,
        project_id: str,
        entries: list[Any],
        project_root: str = "",
    ) -> int: ...

    def unpublish(self, project_id: str, keys: list[str] | None = None) -> int: ...

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


# ---------------------------------------------------------------------------
# PrivateBackend protocol for per-agent private memory (EPIC-059 STORY-059.5)
# ---------------------------------------------------------------------------


@runtime_checkable
class PrivateBackend(Protocol):
    """Backend protocol for private agent memory storage.

    The only supported implementation in v3 is
    :class:`~tapps_brain.postgres_private.PostgresPrivateBackend` (ADR-007).
    All operations are implicitly scoped to a single ``(project_id, agent_id)``
    pair — implementations supply tenant isolation at construction time.

    Path sentinels
    --------------
    ``db_path``, ``store_dir``, and ``audit_path`` must return :class:`Path`
    objects but point at ``Path("/dev/null")`` under the Postgres backend.
    They exist for legacy diagnostics call sites and are not written to.
    """

    # -- Properties required by MemoryStore callers -------------------------

    @property
    def store_dir(self) -> Path: ...

    @property
    def db_path(self) -> Path: ...

    @property
    def audit_path(self) -> Path: ...

    @property
    def encryption_key(self) -> str | None: ...

    # -- Core CRUD -----------------------------------------------------------

    def save(self, entry: MemoryEntry) -> None: ...

    def load_all(self) -> list[MemoryEntry]: ...

    def delete(self, key: str) -> bool: ...

    def search(
        self,
        query: str,
        *,
        memory_group: str | None = None,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        as_of: str | None = None,
    ) -> list[MemoryEntry]:
        """Search entries using full-text matching.

        Args:
            query: Plain-text search query.
            memory_group: Restrict results to a project-local group.
            since: ISO-8601 lower bound (inclusive) on *time_field*.
            until: ISO-8601 upper bound (exclusive) on *time_field*.
            time_field: Column to filter on (``created_at``, ``updated_at``,
                ``last_accessed``).
            as_of: ISO-8601 timestamp for bi-temporal point-in-time filtering.
                When set, the SQL query adds::

                    (valid_at IS NULL OR valid_at <= as_of::timestamptz)
                    AND (invalid_at IS NULL OR invalid_at > as_of::timestamptz)

                These predicates map to the ``valid_at`` and ``invalid_at`` columns
                introduced in migration 001 (``migrations/private/001_initial.sql``).
                ``NULL`` in either column means "unbounded", so entries without
                temporal bounds are always visible.  When ``as_of`` is ``None``
                the backend returns all FTS-matching rows; the store layer applies
                its own in-memory ``is_temporally_valid`` filter.
        """
        ...

    # -- Relations -----------------------------------------------------------

    def list_relations(self) -> list[dict[str, Any]]: ...

    def count_relations(self) -> int: ...

    def save_relations(self, key: str, relations: list[RelationEntry]) -> int: ...

    def load_relations(self, key: str) -> list[dict[str, Any]]: ...

    # -- Schema / vector / audit ---------------------------------------------

    def get_schema_version(self) -> int: ...

    def knn_search(
        self, query_embedding: list[float], k: int
    ) -> list[tuple[str, float]]: ...

    def vector_row_count(self) -> int: ...

    def append_audit(
        self,
        action: str,
        key: str,
        extra: dict[str, Any] | None = None,
    ) -> None: ...

    def close(self) -> None: ...
