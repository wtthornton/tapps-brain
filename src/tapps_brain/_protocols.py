"""Protocol types for optional integrations.

These protocols allow the brain to work with external systems
(project profiling, doc lookup, path validation) without depending
on specific implementations. Consumers pass concrete objects that
satisfy these protocols.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


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
