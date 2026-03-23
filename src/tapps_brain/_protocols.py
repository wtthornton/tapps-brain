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
    def tech_stack(self) -> Any: ...  # noqa: ANN401

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
