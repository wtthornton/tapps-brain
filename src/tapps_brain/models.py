"""Pydantic v2 models for the shared memory subsystem."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    ephemeral = "ephemeral"  # very fast decay - momentary context (default: 1 day)
    session = "session"  # ephemeral, current session only (default: 1 day)


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
    ephemeral = "ephemeral"  # very fast decay - momentary context (profile: 1 day)
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
MAX_TAG_LENGTH: int = 64


def tier_str(tier: MemoryTier | str) -> str:
    """Return the string value of a tier, handling both MemoryTier enum and str."""
    return tier.value if isinstance(tier, MemoryTier) else str(tier)


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
    # STORY-042.2: which model produced ``embedding`` (nullable for legacy rows).
    embedding_model_id: str | None = Field(
        default=None,
        description="Dense model id when embedding was computed (e.g. BAAI/bge-small-en-v1.5).",
    )

    # Hive agent scope (EPIC-011 + GitHub #52 group:<name>)
    agent_scope: str = Field(
        default="private",
        description=(
            "Hive propagation: 'private' | 'domain' | 'hive' | 'group:<name>' "
            "(cross-agent Hive group namespace; requires membership)."
        ),
    )

    # Project-local partition (GitHub #49); not Hive namespace or profile tier.
    memory_group: str | None = Field(
        default=None,
        description="Optional project-local group for retrieval filters (e.g. team-a, feature-x).",
    )

    # Bi-temporal versioning (EPIC-004)
    valid_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC: when this fact became true in the real world.",
    )
    invalid_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC: when this fact stopped being true.",
    )
    superseded_by: str | None = Field(
        default=None,
        description="Key of the entry that replaced this one.",
    )

    # Temporal validity window (GitHub #29, task 040.3)
    # Alias fields that mirror valid_at/invalid_at but use human-friendly ISO-8601 strings.
    valid_from: str = Field(
        default="",
        description=(
            "ISO-8601 UTC: when this fact begins to be valid (inclusive). Empty means 'always'."
        ),
    )
    valid_until: str = Field(
        default="",
        description=(
            "ISO-8601 UTC: when this fact stops being valid (exclusive). Empty means 'forever'."
        ),
    )

    # Integrity hash for tamper detection (H4a)
    integrity_hash: str | None = Field(
        default=None,
        description="HMAC-SHA256 hex digest computed over key|value|tier|source.",
    )

    # Provenance metadata (GitHub #38): track WHERE each memory came from.
    source_session_id: str = Field(default="", description="Session ID that triggered this memory.")
    source_channel: str = Field(
        default="",
        description="Channel/surface (e.g. 'webchat', 'discord').",
    )
    source_message_id: str = Field(default="", description="Message ID that triggered this memory.")
    triggered_by: str = Field(default="", description="Event or action that triggered this memory.")

    # Adaptive stability and difficulty for FSRS-style decay (GitHub #28, task 040.5)
    stability: float = Field(
        default=0.0,
        description=(
            "FSRS-style memory stability in days. 0.0 means use tier half-life. "
            "See docs/guides/memory-decay-and-fsrs.md."
        ),
    )
    difficulty: float = Field(
        default=0.0,
        description="Memory difficulty (1-10). 0.0 means auto from tier.",
    )

    # Bayesian confidence update counters (GitHub #35, task 040.6)
    useful_access_count: int = Field(
        default=0,
        description="Times this memory was retrieved and proved useful.",
    )
    total_access_count: int = Field(
        default=0,
        description="Total times this memory was retrieved.",
    )

    # Flywheel feedback tallies (EPIC-031); floats allow fractional implicit signals.
    positive_feedback_count: float = Field(
        default=0.0,
        ge=0.0,
        description="Tally of positive feedback signals applied to this entry.",
    )
    negative_feedback_count: float = Field(
        default=0.0,
        ge=0.0,
        description="Tally of negative feedback signals applied to this entry.",
    )

    @field_validator("tier", mode="before")
    @classmethod
    def _normalize_tier(cls, v: object) -> MemoryTier:
        """Coerce string tier values to MemoryTier; reject unknown values."""
        if isinstance(v, MemoryTier):
            return v
        try:
            return MemoryTier(v)
        except ValueError as exc:
            valid = [t.value for t in MemoryTier]
            msg = f"tier must be one of {valid}, got {v!r}"
            raise ValueError(msg) from exc

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
        for tag in v:
            if not tag.strip():
                msg = "Tags must not be empty or whitespace-only."
                raise ValueError(msg)
            if len(tag) > MAX_TAG_LENGTH:
                msg = f"Tag exceeds max length ({len(tag)} > {MAX_TAG_LENGTH}): {tag!r}"
                raise ValueError(msg)
        return v

    @field_validator("agent_scope")
    @classmethod
    def _validate_agent_scope(cls, v: str) -> str:
        from tapps_brain.agent_scope import normalize_agent_scope

        try:
            return normalize_agent_scope(v)
        except ValueError as exc:
            msg = str(exc)
            raise ValueError(msg) from exc

    @field_validator("memory_group", mode="before")
    @classmethod
    def _validate_memory_group(cls, v: object) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            msg = f"memory_group must be a string or None. Got: {type(v).__name__}"
            raise ValueError(msg)
        from tapps_brain.memory_group import normalize_memory_group

        return normalize_memory_group(v)

    @model_validator(mode="after")
    def _apply_defaults_and_validate(self) -> MemoryEntry:
        # Apply source-based confidence default
        if self.confidence < 0:
            self.confidence = _SOURCE_CONFIDENCE_DEFAULTS.get(self.source, 0.5)

        # Branch required when scope=branch
        if self.scope == MemoryScope.branch and not self.branch:
            msg = "Branch name is required when scope is 'branch'."
            raise ValueError(msg)

        # Temporal validation: invalid_at must be after valid_at
        if (
            self.valid_at is not None
            and self.invalid_at is not None
            and self.invalid_at <= self.valid_at
        ):
            msg = "invalid_at must be after valid_at."
            raise ValueError(msg)

        return self

    def is_temporally_valid(self, as_of: str | None = None) -> bool:
        """Check whether this entry is valid at the given point in time.

        If *as_of* is ``None``, uses the current UTC time. An entry with
        both ``valid_at`` and ``invalid_at`` set to ``None`` is always valid.

        The window is ``[valid_at, invalid_at)`` — inclusive start, exclusive end.
        Also checks ``valid_from`` / ``valid_until`` (GitHub #29, task 040.3).
        """
        ts = as_of or _utc_now_iso()
        # Check valid_at / invalid_at (bi-temporal EPIC-004 fields)
        if self.valid_at is not None and ts < self.valid_at:
            return False
        if self.invalid_at is not None and ts >= self.invalid_at:
            return False
        # Check valid_from / valid_until (human-friendly alias fields, GitHub #29)
        if self.valid_from and ts < self.valid_from:
            return False
        return not (self.valid_until and ts >= self.valid_until)

    @property
    def is_superseded(self) -> bool:
        """Return ``True`` if this entry has been superseded (invalid_at in the past)."""
        if self.invalid_at is None:
            return False
        return _utc_now_iso() >= self.invalid_at


# ---------------------------------------------------------------------------
# Auto-recall models (Epic 003)
# ---------------------------------------------------------------------------


class RecallDiagnostics(BaseModel):
    """Machine-readable context when recall returns few or no memories."""

    empty_reason: str | None = Field(
        default=None,
        description="Set when nothing was injected; null when memories are present.",
    )
    retriever_hits: int = Field(
        default=0,
        ge=0,
        description="Rows returned by retriever before composite score cutoff.",
    )
    visible_entries: int | None = Field(
        default=None,
        description="Entry count visible for this query (e.g. memory_group scope).",
    )


class RecallResult(BaseModel):
    """Result of an auto-recall operation.

    Returned by ``RecallOrchestrator.recall()`` and the ``RecallHookLike``
    protocol. Contains the formatted memory section ready for injection,
    metadata about which memories were selected, and timing information.
    """

    memory_section: str = Field(
        default="",
        description="Formatted markdown section for injection into the prompt.",
    )
    memories: list[dict[str, Any]] = Field(
        default_factory=list,
        description=("Injected memory summaries: key, value, confidence, tier, score, stale."),
    )
    token_count: int = Field(
        default=0,
        ge=0,
        description="Estimated token count of the injected memory section.",
    )
    recall_time_ms: float = Field(
        default=0.0,
        ge=0.0,
        description="Time taken for the recall operation in milliseconds.",
    )
    truncated: bool = Field(
        default=False,
        description="Whether the results were truncated due to token budget.",
    )
    memory_count: int = Field(
        default=0,
        ge=0,
        description="Number of memories injected.",
    )
    hive_memory_count: int = Field(
        default=0,
        ge=0,
        description="Number of memories from the Hive (EPIC-011).",
    )
    quality_warning: str | None = Field(
        default=None,
        description="Set when diagnostics circuit breaker is not CLOSED (EPIC-030).",
    )
    recall_diagnostics: RecallDiagnostics | None = Field(
        default=None,
        description="Why recall was empty or pipeline stats (agent observability).",
    )


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
    is_consolidated: Literal[True] = Field(
        default=True,
        description="Always True for ConsolidatedEntry.",
    )


# ---------------------------------------------------------------------------
# Agent Registration (Hive, EPIC-011)
# ---------------------------------------------------------------------------


class AgentRegistration(BaseModel):
    """A registered agent in the Hive.

    Moved from ``hive.py`` during STORY-059.2 (SQLite shared-store removal).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique agent identifier (slug).")
    name: str = Field(default="", description="Human-readable agent name.")
    profile: str = Field(
        default="repo-brain",
        description="Memory profile name (determines domain namespace).",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Skills this agent provides (e.g. ['code-review', 'testing']).",
    )
    project_root: str | None = Field(
        default=None,
        description="Absolute path to the agent's project root (if project-scoped).",
    )
