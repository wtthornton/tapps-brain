"""tapps-brain: Persistent cross-session memory system for AI coding assistants."""

from __future__ import annotations

import importlib.metadata

try:
    __version__: str = importlib.metadata.version("tapps-brain")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0-dev"

# Agent Brain facade (EPIC-057)
# Backend protocols & adapters (EPIC-055)
from tapps_brain._protocols import AgentRegistryBackend as AgentRegistryBackend
from tapps_brain._protocols import FederationBackend as FederationBackend
from tapps_brain._protocols import HiveBackend as HiveBackend
from tapps_brain.agent_brain import AgentBrain as AgentBrain
from tapps_brain.agent_brain import BrainConfigError as BrainConfigError
from tapps_brain.agent_brain import BrainError as BrainError
from tapps_brain.agent_brain import BrainTransientError as BrainTransientError
from tapps_brain.agent_brain import BrainValidationError as BrainValidationError

# Async (Issue #66)
from tapps_brain.aio import AsyncMemoryStore as AsyncMemoryStore
from tapps_brain.backends import SqliteAgentRegistryBackend as SqliteAgentRegistryBackend
from tapps_brain.backends import create_federation_backend as create_federation_backend
from tapps_brain.backends import create_hive_backend as create_hive_backend
from tapps_brain.backends import resolve_hive_backend_from_env as resolve_hive_backend_from_env

# BM25
from tapps_brain.bm25 import BM25Scorer as BM25Scorer
from tapps_brain.consolidation import consolidate as consolidate

# Decay
from tapps_brain.decay import DecayConfig as DecayConfig
from tapps_brain.decay import calculate_decayed_confidence as calculate_decayed_confidence
from tapps_brain.decay import get_effective_confidence as get_effective_confidence
from tapps_brain.decay import is_stale as is_stale

# GC
from tapps_brain.gc import GCResult as GCResult
from tapps_brain.gc import MemoryGarbageCollector as MemoryGarbageCollector

# Injection
from tapps_brain.injection import InjectionConfig as InjectionConfig
from tapps_brain.injection import inject_memories as inject_memories

# Integrity (H4a)
from tapps_brain.integrity import compute_integrity_hash as compute_integrity_hash
from tapps_brain.integrity import verify_integrity_hash as verify_integrity_hash

# I/O
from tapps_brain.io import export_memories as export_memories
from tapps_brain.io import export_to_markdown as export_to_markdown
from tapps_brain.io import import_memories as import_memories

# Markdown Import (EPIC-012)
from tapps_brain.markdown_import import import_memory_md as import_memory_md
from tapps_brain.markdown_import import import_openclaw_workspace as import_openclaw_workspace

# Markdown Sync — bidirectional MEMORY.md sync (EPIC-026)
from tapps_brain.markdown_sync import get_sync_state as get_sync_state
from tapps_brain.markdown_sync import sync_from_markdown as sync_from_markdown
from tapps_brain.markdown_sync import sync_to_markdown as sync_to_markdown

# Metrics / observability (return types for store.health() and store.get_metrics())
from tapps_brain.metrics import MetricsSnapshot as MetricsSnapshot
from tapps_brain.metrics import StoreHealthReport as StoreHealthReport

# Models
from tapps_brain.models import (
    ConsolidatedEntry as ConsolidatedEntry,
)
from tapps_brain.models import (
    ConsolidationReason as ConsolidationReason,
)
from tapps_brain.models import (
    MemoryEntry as MemoryEntry,
)
from tapps_brain.models import (
    MemoryScope as MemoryScope,
)
from tapps_brain.models import (
    MemorySnapshot as MemorySnapshot,
)
from tapps_brain.models import (
    MemorySource as MemorySource,
)
from tapps_brain.models import (
    MemoryTier as MemoryTier,
)
from tapps_brain.models import (
    RecallResult as RecallResult,
)

# Profile (EPIC-010)
from tapps_brain.profile import MemoryProfile as MemoryProfile
from tapps_brain.profile import ScoringConfig as ScoringConfig

# Rate Limiting (H6a)
from tapps_brain.rate_limiter import RateLimiterConfig as RateLimiterConfig
from tapps_brain.rate_limiter import SlidingWindowRateLimiter as SlidingWindowRateLimiter

# Recall (EPIC-003)
from tapps_brain.recall import RecallConfig as RecallConfig
from tapps_brain.recall import RecallOrchestrator as RecallOrchestrator

# Relations
from tapps_brain.relations import RelationEntry as RelationEntry
from tapps_brain.relations import extract_relations as extract_relations

# Retrieval
from tapps_brain.retrieval import MemoryRetriever as MemoryRetriever
from tapps_brain.retrieval import ScoredMemory as ScoredMemory

# Safety
from tapps_brain.safety import DEFAULT_SAFETY_RULESET_VERSION as DEFAULT_SAFETY_RULESET_VERSION
from tapps_brain.safety import SafetyCheckResult as SafetyCheckResult
from tapps_brain.safety import check_content_safety as check_content_safety
from tapps_brain.safety import resolve_safety_ruleset_version as resolve_safety_ruleset_version

# Similarity & Consolidation
from tapps_brain.similarity import SimilarityResult as SimilarityResult
from tapps_brain.similarity import compute_similarity as compute_similarity
from tapps_brain.similarity import find_similar as find_similar

# Store
from tapps_brain.store import VALID_AGENT_SCOPES as VALID_AGENT_SCOPES
from tapps_brain.store import ConsolidationConfig as ConsolidationConfig
from tapps_brain.store import MemoryStore as MemoryStore
from tapps_brain.store import MemoryStoreLockTimeout as MemoryStoreLockTimeout

__all__ = [
    "DEFAULT_SAFETY_RULESET_VERSION",
    "VALID_AGENT_SCOPES",
    "AgentBrain",
    "AgentRegistryBackend",
    "BrainConfigError",
    "BrainError",
    "BrainTransientError",
    "BrainValidationError",
    "AsyncMemoryStore",
    "BM25Scorer",
    "ConsolidatedEntry",
    "ConsolidationConfig",
    "ConsolidationReason",
    "DecayConfig",
    "FederationBackend",
    "GCResult",
    "HiveBackend",
    "InjectionConfig",
    "MemoryEntry",
    "MemoryGarbageCollector",
    "MemoryProfile",
    "MemoryRetriever",
    "MemoryScope",
    "MemorySnapshot",
    "MemorySource",
    "MemoryStore",
    "MemoryStoreLockTimeout",
    "MemoryTier",
    "MetricsSnapshot",
    "RateLimiterConfig",
    "RecallConfig",
    "RecallOrchestrator",
    "RecallResult",
    "RelationEntry",
    "SafetyCheckResult",
    "ScoredMemory",
    "ScoringConfig",
    "SimilarityResult",
    "SlidingWindowRateLimiter",
    "SqliteAgentRegistryBackend",
    "StoreHealthReport",
    "__version__",
    "calculate_decayed_confidence",
    "check_content_safety",
    "compute_integrity_hash",
    "compute_similarity",
    "consolidate",
    "create_federation_backend",
    "create_hive_backend",
    "export_memories",
    "export_to_markdown",
    "extract_relations",
    "find_similar",
    "get_effective_confidence",
    "get_sync_state",
    "import_memories",
    "import_memory_md",
    "import_openclaw_workspace",
    "inject_memories",
    "is_stale",
    "resolve_hive_backend_from_env",
    "resolve_safety_ruleset_version",
    "sync_from_markdown",
    "sync_to_markdown",
    "verify_integrity_hash",
]
