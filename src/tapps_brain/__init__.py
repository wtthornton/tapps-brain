"""tapps-brain: Persistent cross-session memory system for AI coding assistants."""

from __future__ import annotations

import importlib
import importlib.metadata
from typing import TYPE_CHECKING

try:
    __version__: str = importlib.metadata.version("tapps-brain")
except importlib.metadata.PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0-dev"

# ---------------------------------------------------------------------------
# TAP-1834: Lazy-import map — attribute name → (module_path, attr_in_module)
#
# This replaces 35+ eager top-level imports with a PEP 562 __getattr__ so
# `import tapps_brain.mcp_server` (and any other submodule import) does NOT
# pay the full package startup cost when only a lightweight tool-catalog
# probe is needed.  First access is identical to a direct import; subsequent
# accesses are O(1) dict lookups (cached in globals()).
# ---------------------------------------------------------------------------
_LAZY: dict[str, tuple[str, str]] = {
    # _protocols
    "AgentRegistryBackend": ("tapps_brain._protocols", "AgentRegistryBackend"),
    "FederationBackend": ("tapps_brain._protocols", "FederationBackend"),
    "HiveBackend": ("tapps_brain._protocols", "HiveBackend"),
    # agent_brain
    "AgentBrain": ("tapps_brain.agent_brain", "AgentBrain"),
    "BrainConfigError": ("tapps_brain.agent_brain", "BrainConfigError"),
    "BrainError": ("tapps_brain.agent_brain", "BrainError"),
    "BrainTransientError": ("tapps_brain.agent_brain", "BrainTransientError"),
    "BrainValidationError": ("tapps_brain.agent_brain", "BrainValidationError"),
    # aio
    "AsyncMemoryStore": ("tapps_brain.aio", "AsyncMemoryStore"),
    # async_postgres_private
    "AsyncPostgresPrivateBackend": (
        "tapps_brain.async_postgres_private",
        "AsyncPostgresPrivateBackend",
    ),
    # backends
    "FileAgentRegistryBackend": ("tapps_brain.backends", "FileAgentRegistryBackend"),
    "create_federation_backend": ("tapps_brain.backends", "create_federation_backend"),
    "create_hive_backend": ("tapps_brain.backends", "create_hive_backend"),
    "resolve_hive_backend_from_env": ("tapps_brain.backends", "resolve_hive_backend_from_env"),
    # bm25
    "BM25Scorer": ("tapps_brain.bm25", "BM25Scorer"),
    # client
    "AsyncTappsBrainClient": ("tapps_brain.client", "AsyncTappsBrainClient"),
    "BrainClientProtocol": ("tapps_brain.client", "BrainClientProtocol"),
    "TappsBrainClient": ("tapps_brain.client", "TappsBrainClient"),
    # consolidation
    "consolidate": ("tapps_brain.consolidation", "consolidate"),
    # decay
    "DecayConfig": ("tapps_brain.decay", "DecayConfig"),
    "calculate_decayed_confidence": ("tapps_brain.decay", "calculate_decayed_confidence"),
    "get_effective_confidence": ("tapps_brain.decay", "get_effective_confidence"),
    "is_stale": ("tapps_brain.decay", "is_stale"),
    # gc
    "GCResult": ("tapps_brain.gc", "GCResult"),
    "MemoryGarbageCollector": ("tapps_brain.gc", "MemoryGarbageCollector"),
    # injection
    "InjectionConfig": ("tapps_brain.injection", "InjectionConfig"),
    "inject_memories": ("tapps_brain.injection", "inject_memories"),
    # integrity
    "INTEGRITY_HASH_VERSION": ("tapps_brain.integrity", "INTEGRITY_HASH_VERSION"),
    "compute_integrity_hash": ("tapps_brain.integrity", "compute_integrity_hash"),
    "compute_integrity_hash_v1": ("tapps_brain.integrity", "compute_integrity_hash_v1"),
    "verify_integrity_hash": ("tapps_brain.integrity", "verify_integrity_hash"),
    # io
    "export_memories": ("tapps_brain.io", "export_memories"),
    "export_to_markdown": ("tapps_brain.io", "export_to_markdown"),
    "import_memories": ("tapps_brain.io", "import_memories"),
    # markdown_import
    "import_memory_md": ("tapps_brain.markdown_import", "import_memory_md"),
    "import_openclaw_workspace": ("tapps_brain.markdown_import", "import_openclaw_workspace"),
    # markdown_sync
    "get_sync_state": ("tapps_brain.markdown_sync", "get_sync_state"),
    "sync_from_markdown": ("tapps_brain.markdown_sync", "sync_from_markdown"),
    "sync_to_markdown": ("tapps_brain.markdown_sync", "sync_to_markdown"),
    # metrics
    "MetricsSnapshot": ("tapps_brain.metrics", "MetricsSnapshot"),
    "StoreHealthReport": ("tapps_brain.metrics", "StoreHealthReport"),
    # models
    "ConsolidatedEntry": ("tapps_brain.models", "ConsolidatedEntry"),
    "ConsolidationReason": ("tapps_brain.models", "ConsolidationReason"),
    "MemoryEntry": ("tapps_brain.models", "MemoryEntry"),
    "MemoryScope": ("tapps_brain.models", "MemoryScope"),
    "MemorySnapshot": ("tapps_brain.models", "MemorySnapshot"),
    "MemorySource": ("tapps_brain.models", "MemorySource"),
    "MemoryTier": ("tapps_brain.models", "MemoryTier"),
    "RecallResult": ("tapps_brain.models", "RecallResult"),
    # profile
    "MemoryProfile": ("tapps_brain.profile", "MemoryProfile"),
    "ScoringConfig": ("tapps_brain.profile", "ScoringConfig"),
    # project_registry
    "ProjectNotRegisteredError": ("tapps_brain.project_registry", "ProjectNotRegisteredError"),
    "ProjectRecord": ("tapps_brain.project_registry", "ProjectRecord"),
    "ProjectRegistry": ("tapps_brain.project_registry", "ProjectRegistry"),
    # project_resolver
    "DEFAULT_PROJECT_ID": ("tapps_brain.project_resolver", "DEFAULT_PROJECT_ID"),
    "InvalidProjectIdError": ("tapps_brain.project_resolver", "InvalidProjectIdError"),
    "resolve_project_id": ("tapps_brain.project_resolver", "resolve_project_id"),
    "validate_project_id": ("tapps_brain.project_resolver", "validate_project_id"),
    # rate_limiter
    "RateLimiterConfig": ("tapps_brain.rate_limiter", "RateLimiterConfig"),
    "SlidingWindowRateLimiter": ("tapps_brain.rate_limiter", "SlidingWindowRateLimiter"),
    # recall
    "RecallConfig": ("tapps_brain.recall", "RecallConfig"),
    "RecallOrchestrator": ("tapps_brain.recall", "RecallOrchestrator"),
    # relations
    "RelationEntry": ("tapps_brain.relations", "RelationEntry"),
    "extract_relations": ("tapps_brain.relations", "extract_relations"),
    # retrieval
    "MemoryRetriever": ("tapps_brain.retrieval", "MemoryRetriever"),
    "ScoredMemory": ("tapps_brain.retrieval", "ScoredMemory"),
    # safety
    "DEFAULT_SAFETY_RULESET_VERSION": ("tapps_brain.safety", "DEFAULT_SAFETY_RULESET_VERSION"),
    "SafetyCheckResult": ("tapps_brain.safety", "SafetyCheckResult"),
    "check_content_safety": ("tapps_brain.safety", "check_content_safety"),
    "resolve_safety_ruleset_version": ("tapps_brain.safety", "resolve_safety_ruleset_version"),
    # similarity
    "SimilarityResult": ("tapps_brain.similarity", "SimilarityResult"),
    "compute_similarity": ("tapps_brain.similarity", "compute_similarity"),
    "find_similar": ("tapps_brain.similarity", "find_similar"),
    # store
    "VALID_AGENT_SCOPES": ("tapps_brain.store", "VALID_AGENT_SCOPES"),
    "ConsolidationConfig": ("tapps_brain.store", "ConsolidationConfig"),
    "MemoryStore": ("tapps_brain.store", "MemoryStore"),
    "MemoryStoreLockTimeout": ("tapps_brain.store", "MemoryStoreLockTimeout"),
}


def __getattr__(name: str) -> object:
    """PEP 562 lazy loader — imports are deferred until first attribute access.

    Subsequent accesses are O(1) because the resolved value is cached in the
    module's ``globals()`` dict after the first call.
    """
    if name in _LAZY:
        module_path, attr = _LAZY[name]
        mod = importlib.import_module(module_path)
        val = getattr(mod, attr)
        # Cache in module globals so __getattr__ is not called again.
        globals()[name] = val
        return val
    raise AttributeError(f"module 'tapps_brain' has no attribute {name!r}")


if TYPE_CHECKING:
    # Kept under TYPE_CHECKING so static analysers understand the public API
    # without triggering the runtime import cost.
    from tapps_brain._protocols import AgentRegistryBackend as AgentRegistryBackend
    from tapps_brain._protocols import FederationBackend as FederationBackend
    from tapps_brain._protocols import HiveBackend as HiveBackend
    from tapps_brain.agent_brain import AgentBrain as AgentBrain
    from tapps_brain.agent_brain import BrainConfigError as BrainConfigError
    from tapps_brain.agent_brain import BrainError as BrainError
    from tapps_brain.agent_brain import BrainTransientError as BrainTransientError
    from tapps_brain.agent_brain import BrainValidationError as BrainValidationError
    from tapps_brain.aio import AsyncMemoryStore as AsyncMemoryStore
    from tapps_brain.async_postgres_private import (
        AsyncPostgresPrivateBackend as AsyncPostgresPrivateBackend,
    )
    from tapps_brain.backends import FileAgentRegistryBackend as FileAgentRegistryBackend
    from tapps_brain.backends import create_federation_backend as create_federation_backend
    from tapps_brain.backends import create_hive_backend as create_hive_backend
    from tapps_brain.backends import resolve_hive_backend_from_env as resolve_hive_backend_from_env
    from tapps_brain.bm25 import BM25Scorer as BM25Scorer
    from tapps_brain.client import AsyncTappsBrainClient as AsyncTappsBrainClient
    from tapps_brain.client import BrainClientProtocol as BrainClientProtocol
    from tapps_brain.client import TappsBrainClient as TappsBrainClient
    from tapps_brain.consolidation import consolidate as consolidate
    from tapps_brain.decay import DecayConfig as DecayConfig
    from tapps_brain.decay import calculate_decayed_confidence as calculate_decayed_confidence
    from tapps_brain.decay import get_effective_confidence as get_effective_confidence
    from tapps_brain.decay import is_stale as is_stale
    from tapps_brain.gc import GCResult as GCResult
    from tapps_brain.gc import MemoryGarbageCollector as MemoryGarbageCollector
    from tapps_brain.injection import InjectionConfig as InjectionConfig
    from tapps_brain.injection import inject_memories as inject_memories
    from tapps_brain.integrity import INTEGRITY_HASH_VERSION as INTEGRITY_HASH_VERSION
    from tapps_brain.integrity import compute_integrity_hash as compute_integrity_hash
    from tapps_brain.integrity import compute_integrity_hash_v1 as compute_integrity_hash_v1
    from tapps_brain.integrity import verify_integrity_hash as verify_integrity_hash
    from tapps_brain.io import export_memories as export_memories
    from tapps_brain.io import export_to_markdown as export_to_markdown
    from tapps_brain.io import import_memories as import_memories
    from tapps_brain.markdown_import import import_memory_md as import_memory_md
    from tapps_brain.markdown_import import import_openclaw_workspace as import_openclaw_workspace
    from tapps_brain.markdown_sync import get_sync_state as get_sync_state
    from tapps_brain.markdown_sync import sync_from_markdown as sync_from_markdown
    from tapps_brain.markdown_sync import sync_to_markdown as sync_to_markdown
    from tapps_brain.metrics import MetricsSnapshot as MetricsSnapshot
    from tapps_brain.metrics import StoreHealthReport as StoreHealthReport
    from tapps_brain.models import ConsolidatedEntry as ConsolidatedEntry
    from tapps_brain.models import ConsolidationReason as ConsolidationReason
    from tapps_brain.models import MemoryEntry as MemoryEntry
    from tapps_brain.models import MemoryScope as MemoryScope
    from tapps_brain.models import MemorySnapshot as MemorySnapshot
    from tapps_brain.models import MemorySource as MemorySource
    from tapps_brain.models import MemoryTier as MemoryTier
    from tapps_brain.models import RecallResult as RecallResult
    from tapps_brain.profile import MemoryProfile as MemoryProfile
    from tapps_brain.profile import ScoringConfig as ScoringConfig
    from tapps_brain.project_registry import (
        ProjectNotRegisteredError as ProjectNotRegisteredError,
    )
    from tapps_brain.project_registry import ProjectRecord as ProjectRecord
    from tapps_brain.project_registry import ProjectRegistry as ProjectRegistry
    from tapps_brain.project_resolver import DEFAULT_PROJECT_ID as DEFAULT_PROJECT_ID
    from tapps_brain.project_resolver import InvalidProjectIdError as InvalidProjectIdError
    from tapps_brain.project_resolver import resolve_project_id as resolve_project_id
    from tapps_brain.project_resolver import validate_project_id as validate_project_id
    from tapps_brain.rate_limiter import RateLimiterConfig as RateLimiterConfig
    from tapps_brain.rate_limiter import SlidingWindowRateLimiter as SlidingWindowRateLimiter
    from tapps_brain.recall import RecallConfig as RecallConfig
    from tapps_brain.recall import RecallOrchestrator as RecallOrchestrator
    from tapps_brain.relations import RelationEntry as RelationEntry
    from tapps_brain.relations import extract_relations as extract_relations
    from tapps_brain.retrieval import MemoryRetriever as MemoryRetriever
    from tapps_brain.retrieval import ScoredMemory as ScoredMemory
    from tapps_brain.safety import DEFAULT_SAFETY_RULESET_VERSION as DEFAULT_SAFETY_RULESET_VERSION
    from tapps_brain.safety import SafetyCheckResult as SafetyCheckResult
    from tapps_brain.safety import check_content_safety as check_content_safety
    from tapps_brain.safety import resolve_safety_ruleset_version as resolve_safety_ruleset_version
    from tapps_brain.similarity import SimilarityResult as SimilarityResult
    from tapps_brain.similarity import compute_similarity as compute_similarity
    from tapps_brain.similarity import find_similar as find_similar
    from tapps_brain.store import VALID_AGENT_SCOPES as VALID_AGENT_SCOPES
    from tapps_brain.store import ConsolidationConfig as ConsolidationConfig
    from tapps_brain.store import MemoryStore as MemoryStore
    from tapps_brain.store import MemoryStoreLockTimeout as MemoryStoreLockTimeout


__all__ = [
    "DEFAULT_PROJECT_ID",
    "DEFAULT_SAFETY_RULESET_VERSION",
    "INTEGRITY_HASH_VERSION",
    "VALID_AGENT_SCOPES",
    "AgentBrain",
    "AgentRegistryBackend",
    "AsyncMemoryStore",
    "AsyncPostgresPrivateBackend",
    "AsyncTappsBrainClient",
    "BM25Scorer",
    "BrainClientProtocol",
    "BrainConfigError",
    "BrainError",
    "BrainTransientError",
    "BrainValidationError",
    "ConsolidatedEntry",
    "ConsolidationConfig",
    "ConsolidationReason",
    "DecayConfig",
    "FederationBackend",
    "FileAgentRegistryBackend",
    "GCResult",
    "HiveBackend",
    "InjectionConfig",
    "InvalidProjectIdError",
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
    "ProjectNotRegisteredError",
    "ProjectRecord",
    "ProjectRegistry",
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
    "StoreHealthReport",
    "TappsBrainClient",
    "__version__",
    "calculate_decayed_confidence",
    "check_content_safety",
    "compute_integrity_hash",
    "compute_integrity_hash_v1",
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
    "resolve_project_id",
    "resolve_safety_ruleset_version",
    "sync_from_markdown",
    "sync_to_markdown",
    "validate_project_id",
    "verify_integrity_hash",
]
