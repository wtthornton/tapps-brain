"""Pydantic v2 models for the shared memory subsystem."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator, model_validator


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

_KEY_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class MemoryTier(StrEnum):
    """Decay classification for memory entries."""

    architectural = "architectural"  # slow decay - project structure, key decisions
    pattern = "pattern"  # medium decay - coding patterns, conventions
    procedural = "procedural"  # medium decay - how to do (workflows, steps); Epic 65.11
    context = "context"  # fast decay - session-specific context


class MemorySource(StrEnum):
    """Origin of a memory entry."""

    human = "human"  # explicitly set by a developer
    agent = "agent"  # created by an AI agent
    inferred = "inferred"  # derived from analysis
    system = "system"  # created by TappsMCP internals


class MemoryScope(StrEnum):
    """Visibility scope of a memory entry."""

    project = "project"  # visible across the entire project
    branch = "branch"  # scoped to a git branch
    session = "session"  # ephemeral, current session only
    shared = "shared"  # eligible for cross-project federation


# ---------------------------------------------------------------------------
# Source-based confidence defaults
# ---------------------------------------------------------------------------

_SOURCE_CONFIDENCE_DEFAULTS: dict[MemorySource, float] = {
    MemorySource.human: 0.95,
    MemorySource.agent: 0.6,
    MemorySource.inferred: 0.4,
    MemorySource.system: 0.9,
}

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_KEY_LENGTH: int = 128
MAX_VALUE_LENGTH: int = 4096
MAX_TAGS: int = 10


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class MemoryEntry(BaseModel):
    """A single memory entry in the shared memory store."""

    key: str = Field(description="Unique slug identifier (max 128 chars).")
    value: str = Field(description="Memory content (max 4096 chars).")
    tier: MemoryTier = Field(default=MemoryTier.pattern, description="Decay classification.")
    confidence: float = Field(
        default=-1.0,
        ge=-1.0,
        le=1.0,
        description="Confidence score 0.0-1.0. -1.0 means use source default.",
    )
    source: MemorySource = Field(default=MemorySource.agent, description="Who created this memory.")
    source_agent: str = Field(
        default="unknown", description="Agent identifier (e.g. 'claude-code')."
    )
    scope: MemoryScope = Field(default=MemoryScope.project, description="Visibility scope.")
    tags: list[str] = Field(default_factory=list, description="Free-form tags for search (max 10).")
    created_at: str = Field(default_factory=_utc_now_iso, description="ISO-8601 UTC creation time.")
    updated_at: str = Field(
        default_factory=_utc_now_iso, description="ISO-8601 UTC last update time."
    )
    last_accessed: str = Field(
        default_factory=_utc_now_iso, description="ISO-8601 UTC last access time."
    )
    access_count: int = Field(default=0, ge=0, description="Read access count.")
    branch: str | None = Field(default=None, description="Git branch (required when scope=branch).")

    # Reserved for Epic 24 (Memory Intelligence)
    last_reinforced: str | None = Field(
        default=None, description="ISO-8601 UTC, set by reinforce action."
    )
    reinforce_count: int = Field(default=0, ge=0, description="Total reinforcements.")
    contradicted: bool = Field(default=False, description="Set by contradiction detection.")
    contradiction_reason: str | None = Field(default=None, description="Reason for contradiction.")

    # Reserved for Epic 25 (Memory Retrieval & Integration)
    seeded_from: str | None = Field(default=None, description="Populated by profile seeding.")

    # Optional embedding for semantic search (Epic 65.7)
    embedding: list[float] | None = Field(
        default=None,
        description="Vector embedding for semantic search when enabled.",
    )

    # Class-level constants (not serialised)
    _KEY_PATTERN: ClassVar[re.Pattern[str]] = _KEY_SLUG_PATTERN

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not _KEY_SLUG_PATTERN.match(v):
            msg = (
                f"Key must be a lowercase slug (letters, digits, dots, hyphens, "
                f"underscores), 1-{MAX_KEY_LENGTH} chars, starting with alphanumeric. "
                f"Got: {v!r}"
            )
            raise ValueError(msg)
        return v

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: str) -> str:
        if len(v) > MAX_VALUE_LENGTH:
            msg = f"Value exceeds max length ({len(v)} > {MAX_VALUE_LENGTH})."
            raise ValueError(msg)
        if not v.strip():
            msg = "Value must not be empty or whitespace-only."
            raise ValueError(msg)
        return v

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        if len(v) > MAX_TAGS:
            msg = f"Too many tags ({len(v)} > {MAX_TAGS})."
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _apply_defaults_and_validate(self) -> MemoryEntry:
        # Apply source-based confidence default
        if self.confidence < 0:
            object.__setattr__(
                self,
                "confidence",
                _SOURCE_CONFIDENCE_DEFAULTS.get(self.source, 0.5),
            )

        # Branch required when scope=branch
        if self.scope == MemoryScope.branch and not self.branch:
            msg = "Branch name is required when scope is 'branch'."
            raise ValueError(msg)

        return self


class MemorySnapshot(BaseModel):
    """Full-state snapshot of the memory store for export/serialization."""

    project_root: str = Field(description="Project root path.")
    entries: list[MemoryEntry] = Field(default_factory=list, description="All memory entries.")
    total_count: int = Field(default=0, ge=0, description="Total entry count.")
    tier_counts: dict[str, int] = Field(default_factory=dict, description="Count per tier.")
    exported_at: str = Field(default_factory=_utc_now_iso, description="ISO-8601 UTC export time.")


# ---------------------------------------------------------------------------
# Consolidation models (Epic 58)
# ---------------------------------------------------------------------------


class ConsolidationReason(StrEnum):
    """Why entries were consolidated."""

    similarity = "similarity"  # Jaccard + TF-IDF similarity above threshold
    same_topic = "same_topic"  # Same tier + overlapping tags
    supersession = "supersession"  # Newer entry references older entry
    manual = "manual"  # User-triggered consolidation


class ConsolidatedEntry(MemoryEntry):
    """A memory entry that consolidates multiple source entries.

    Inherits all MemoryEntry fields and adds provenance tracking.
    Source entries are marked as `consolidated: true` but retained.
    """

    source_ids: list[str] = Field(
        default_factory=list,
        description="Keys of entries that were consolidated into this one.",
    )
    consolidated_at: str = Field(
        default_factory=_utc_now_iso,
        description="ISO-8601 UTC timestamp when consolidation occurred.",
    )
    consolidation_reason: ConsolidationReason = Field(
        default=ConsolidationReason.similarity,
        description="Why the entries were consolidated.",
    )
    is_consolidated: bool = Field(
        default=True,
        description="Always True for ConsolidatedEntry.",
    )
