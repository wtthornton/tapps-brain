"""tapps-brain: Persistent cross-session memory system for AI coding assistants."""

from __future__ import annotations

__version__ = "1.0.0"

# Core types
# BM25
from tapps_brain.bm25 import BM25Scorer as BM25Scorer
from tapps_brain.consolidation import consolidate as consolidate

# Decay
from tapps_brain.decay import DecayConfig as DecayConfig
from tapps_brain.decay import calculate_decayed_confidence as calculate_decayed_confidence
from tapps_brain.decay import get_effective_confidence as get_effective_confidence
from tapps_brain.decay import is_stale as is_stale

# Federation
from tapps_brain.federation import FederatedStore as FederatedStore
from tapps_brain.federation import FederationConfig as FederationConfig

# GC
from tapps_brain.gc import GCResult as GCResult
from tapps_brain.gc import MemoryGarbageCollector as MemoryGarbageCollector

# Injection
from tapps_brain.injection import InjectionConfig as InjectionConfig
from tapps_brain.injection import inject_memories as inject_memories

# I/O
from tapps_brain.io import export_memories as export_memories
from tapps_brain.io import export_to_markdown as export_to_markdown
from tapps_brain.io import import_memories as import_memories
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

# Relations
from tapps_brain.relations import RelationEntry as RelationEntry
from tapps_brain.relations import extract_relations as extract_relations

# Retrieval
from tapps_brain.retrieval import MemoryRetriever as MemoryRetriever
from tapps_brain.retrieval import ScoredMemory as ScoredMemory

# Safety
from tapps_brain.safety import SafetyCheckResult as SafetyCheckResult
from tapps_brain.safety import check_content_safety as check_content_safety

# Similarity & Consolidation
from tapps_brain.similarity import SimilarityResult as SimilarityResult
from tapps_brain.similarity import compute_similarity as compute_similarity
from tapps_brain.similarity import find_similar as find_similar

# Store
from tapps_brain.store import ConsolidationConfig as ConsolidationConfig
from tapps_brain.store import MemoryStore as MemoryStore
