"""In-memory cache backed by Postgres for the shared memory subsystem.

Provides fast reads from an in-memory dict with write-through to the
``PostgresPrivateBackend`` (ADR-007 — Postgres-only persistence plane).
RAG safety checks on save prevent prompt injection in stored content.
Auto-consolidation triggers on save when enabled (EPIC-058).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

from tapps_brain.memory_group import MEMORY_GROUP_UNSET
from tapps_brain.models import (
    MemoryEntry,
    MemoryScope,
    MemorySnapshot,
    MemorySource,
    MemoryStatus,
    MemoryTier,
    _utc_now_iso,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from datetime import datetime
    from pathlib import Path

    from tapps_brain._protocols import HiveBackend, PrivateBackend
    from tapps_brain.auto_consolidation import ConsolidationUndoResult
    from tapps_brain.embeddings import SentenceTransformerProvider
    from tapps_brain.feedback import FeedbackStore, InMemoryFeedbackStore
    from tapps_brain.write_policy import DeterministicWritePolicy, LLMWritePolicy

from tapps_brain._save_conflict import (
    ConflictPlan,
    plan_conflicts,
    resolve_similarity_threshold,
)
from tapps_brain._save_pipeline import (
    apply_safety_check,
    validate_scope_and_group,
)
from tapps_brain._save_propagation import (
    propagate_group_save,
    publish_to_experts,
)
from tapps_brain._store_feedback import FeedbackMixin
from tapps_brain._store_integrity import IntegrityMixin
from tapps_brain._store_query import QueryMixin
from tapps_brain._store_relations import RelationsMixin
from tapps_brain.bloom import BloomFilter, normalize_for_dedup
from tapps_brain.bm25 import preprocess as _bm25_preprocess
from tapps_brain.metrics import (
    MetricsCollector,
    MetricsSnapshot,
    MetricsTimer,
    StoreHealthReport,
    compact_save_phase_summary,
)
from tapps_brain.otel_tracer import (
    ATTR_LATENCY_MS,
    ATTR_ROWS_RETURNED,
    GEN_AI_DATA_SOURCE_ID,
    GEN_AI_OPERATION_EXECUTE_TOOL,
    SPAN_HIVE_PROPAGATE,
    SPAN_RECALL,
    SPAN_REINFORCE,
    SPAN_REMEMBER,
    record_retrieval_document_events,
    rm_add_recall_latency_ms,
    rm_increment_recall_total,
    start_span,
)
from tapps_brain.rate_limiter import RateLimiterConfig, SlidingWindowRateLimiter
from tapps_brain.relations import RelationEntry, extract_relations
from tapps_brain.tier_normalize import normalize_save_tier

logger = structlog.get_logger(__name__)


def _ensure_str_value(value: object) -> str:
    """Normalise a memory ``value`` to ``str`` (TAP-2675).

    Callers at the HTTP/MCP boundary occasionally pass a non-str ``value`` (e.g.
    ``/v1/remember`` with a JSON object body).  The ``value`` text column and the
    content-safety scan both require a ``str``; without this a dict raised
    ``AttributeError: 'dict' object has no attribute 'strip'`` deep in
    ``check_content_safety`` — a 500.  We normalise rather than reject: JSON-encode
    dict/list so structured payloads survive as their JSON text, ``str()`` anything
    else, and leave a real ``str`` untouched.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# Maximum number of memories per project.  TAP-513 — operators can override
# this via the TAPPS_BRAIN_MAX_ENTRIES env var without code changes;
# YAML profile (``limits.max_entries``) still wins when set.  Precedence:
# YAML > env > default.
_MAX_ENTRIES_DEFAULT = 5000


def _resolve_status(
    explicit: str | None,
    existing: MemoryEntry | None,
) -> MemoryStatus:
    """Return the status to write on a save() call.

    Priority:
    1. Caller-provided non-empty string → coerce to MemoryStatus (fallback active).
    2. Existing entry's status → preserve on update.
    3. Default active.
    """
    if explicit is not None and explicit != "":
        try:
            return MemoryStatus(explicit)
        except ValueError:
            return MemoryStatus.active
    if existing is not None:
        return existing.status
    return MemoryStatus.active


def _max_entries_from_env() -> int:
    """Return the env-var override for ``_MAX_ENTRIES``, or the default.

    Invalid (non-int / <= 0) values fall back to the default with a
    warning log so a typo can't silently disable the cap.
    """
    raw = os.environ.get("TAPPS_BRAIN_MAX_ENTRIES", "").strip()
    if not raw:
        return _MAX_ENTRIES_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "store.max_entries_env_invalid",
            raw=raw,
            detail="TAPPS_BRAIN_MAX_ENTRIES must be a positive integer; using default.",
            default=_MAX_ENTRIES_DEFAULT,
        )
        return _MAX_ENTRIES_DEFAULT
    if value <= 0:
        logger.warning(
            "store.max_entries_env_invalid",
            raw=raw,
            detail="TAPPS_BRAIN_MAX_ENTRIES must be > 0; using default.",
            default=_MAX_ENTRIES_DEFAULT,
        )
        return _MAX_ENTRIES_DEFAULT
    return value


# Built-in Hive propagation primitives (``group:<name>`` is also valid; see ``agent_scope``).
VALID_AGENT_SCOPES: tuple[str, ...] = ("private", "domain", "hive")


def _validate_write_rules(
    key: str,
    value: str,
    write_rules: Any,  # noqa: ANN401
) -> str | None:
    """Validate memory save against write rules (Epic 65.17).

    Returns None if valid, or an error message string if invalid.
    """
    if write_rules is None:
        return None

    enforced = getattr(write_rules, "enforced", False)
    if not enforced:
        return None

    # Check blocked keywords
    blocked = getattr(write_rules, "block_sensitive_keywords", [])
    combined = f"{key} {value}".lower()
    for kw in blocked:
        if kw.lower() in combined:
            return f"Blocked by write rule: contains sensitive keyword '{kw}'"

    # Check min length
    min_len = getattr(write_rules, "min_value_length", 0)
    if min_len > 0 and len(value) < min_len:
        return f"Value too short ({len(value)} < {min_len} chars)"

    # Check max length
    max_len = getattr(write_rules, "max_value_length", 4096)
    if len(value) > max_len:
        return f"Value too long ({len(value)} > {max_len} chars)"

    return None


# Reformulation detection window in seconds (STORY-029-4b).
# Queries issued within this window with Jaccard similarity > 0.5 are
# treated as reformulations of each other.
_REFORMULATION_WINDOW = 60

# TAP-549: hard cap on the number of distinct session_ids tracked across
# the session-keyed helper dicts.  Past the cap, LRU eviction drops the
# least-recently-touched sessions so a misbehaving client that rotates
# session_id on every call cannot slow-burn OOM the adapter.  The cap is
# far above realistic concurrent-session counts (deployment model runs
# ~20 agents per box) but cheap enough that sweeps stay ~O(ms).
_SESSION_STATE_HARD_CAP = 10_000

# TAP-645: per-session entry cap for the session log lists.  A single
# long-lived session that issues thousands of recall calls would otherwise
# append without bound.  100 entries covers the reformulation/correction
# detection window (60 s / ~1 recall/s) with generous headroom.
_SESSION_LOG_PER_SESSION_CAP = 100


class MemoryStoreLockTimeout(RuntimeError):  # noqa: N818 — Timeout reads better for operators
    """Raised when the store lock is not acquired within the configured timeout (EPIC-050.2)."""


def _env_lock_timeout_seconds() -> float | None:
    raw = os.environ.get("TAPPS_STORE_LOCK_TIMEOUT_S", "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def _jaccard_similarity(a: str, b: str) -> float:
    """Jaccard similarity between token sets of two strings.

    Tokens are whitespace-split lowercased words.  Returns 1.0 for two
    empty strings, 0.0 if only one is empty.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return intersection / union


def _token_overlap_ratio(a: str, b: str) -> float:
    """Token overlap ratio between two strings.

    Returns |A ∩ B| / min(|A|, |B|).  0.0 if either string is empty.
    Tokens are whitespace-split lowercased words.
    """
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    return intersection / min(len(tokens_a), len(tokens_b))


def _preserved_fields_for_update(
    existing: MemoryEntry | None,
    now: str,
) -> dict[str, Any]:
    """Return the ``MemoryEntry`` fields that must be preserved on update.

    Extracted from ``_construct_memory_entry`` (TAP-602) so the entry builder
    stays linear.  When ``existing`` is ``None`` (a fresh insert) every field
    returns its insert-time default; otherwise we carry the stored value
    forward so reinforcement / contradiction / temporal state is not clobbered
    by a routine save().
    """
    if existing is None:
        return {
            "created_at": now,
            "access_count": 1,
            "last_reinforced": None,
            "reinforce_count": 0,
            "contradicted": False,
            "contradiction_reason": None,
            "seeded_from": None,
            "valid_at": None,
            "invalid_at": None,
            "superseded_by": None,
            "temporal_sensitivity": None,
            "failed_approaches": [],
            "useful_access_count": 0,
            "total_access_count": 0,
            "positive_feedback_count": 0.0,
            "negative_feedback_count": 0.0,
            "stability": 0.0,
            "difficulty": 0.0,
            "embedding": None,
            "embedding_model_id": None,
        }
    return {
        "created_at": existing.created_at,
        "access_count": existing.access_count,
        "last_reinforced": existing.last_reinforced,
        "reinforce_count": existing.reinforce_count,
        "contradicted": existing.contradicted,
        "contradiction_reason": existing.contradiction_reason,
        "seeded_from": existing.seeded_from,
        "valid_at": existing.valid_at,
        "invalid_at": existing.invalid_at,
        "superseded_by": existing.superseded_by,
        "temporal_sensitivity": existing.temporal_sensitivity,
        "failed_approaches": existing.failed_approaches,
        "useful_access_count": existing.useful_access_count,
        "total_access_count": existing.total_access_count,
        "positive_feedback_count": existing.positive_feedback_count,
        "negative_feedback_count": existing.negative_feedback_count,
        "stability": existing.stability,
        "difficulty": existing.difficulty,
        "embedding": existing.embedding,
        "embedding_model_id": existing.embedding_model_id,
    }


@dataclass
class ConsolidationConfig:
    """Configuration for auto-consolidation on save."""

    enabled: bool = True
    threshold: float = 0.7
    min_entries: int = 3

    def to_dict(self) -> dict[str, object]:
        """Return config as a plain dict."""
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "min_entries": self.min_entries,
        }


@dataclass(frozen=True)
class _SavePrep:
    """Validated, pre-persist save state shared by :meth:`MemoryStore.save` and
    :meth:`MemoryStore.save_many` (TAP-2800).

    Carries the fields that phases 1-5 (scope/tier validation, safety, write
    policy, dedup, conflict detection) transform, so the build step is identical
    whether a single entry or a batch is being persisted.
    """

    value: str
    agent_scope: str
    tier: str
    source_agent: str
    mg_explicit: str | None | object
    conflict_valid_at: str | None
    effective_key: str | None = None
    """When write-policy UPDATE remaps onto an existing candidate key."""


_UNSET_EMBEDDING: Any = object()  # sentinel — distinguishes "not passed" from explicit None


class MemoryStore(RelationsMixin, IntegrityMixin, FeedbackMixin, QueryMixin):
    """In-memory cache with Postgres write-through persistence (ADR-007).

    Thread-safe: one ``threading.Lock`` serializes orchestration and cache access.
    Optional ``lock_timeout_seconds`` or env ``TAPPS_STORE_LOCK_TIMEOUT_S`` (>0) makes
    contended acquires fail fast with :exc:`MemoryStoreLockTimeout` instead of blocking.
    Write-through: every mutation updates both the in-memory dict and Postgres synchronously.
    Auto-consolidation triggers on save when enabled (EPIC-058).

    Semantic search via pgvector HNSW is always available under the
    Postgres-only persistence plane (ADR-007).  Pass ``embedding_provider=None``
    to disable embedding computation entirely.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        store_dir: str = ".tapps-brain",
        agent_id: str | None = None,
        groups: list[str] | None = None,
        expert_domains: list[str] | None = None,
        consolidation_config: ConsolidationConfig | None = None,
        embedding_provider: SentenceTransformerProvider | None = _UNSET_EMBEDDING,
        write_rules: Any = None,  # noqa: ANN401
        lookup_engine: Any = None,  # noqa: ANN401
        profile: Any = None,  # noqa: ANN401  # MemoryProfile | None (EPIC-010)
        hive_store: HiveBackend | None = None,
        hive_agent_id: str = "unknown",
        rate_limiter_config: RateLimiterConfig | None = None,
        encryption_key: str | None = None,
        lock_timeout_seconds: float | None = None,
        auto_register: bool = True,
        private_backend: PrivateBackend | None = None,
        write_policy: DeterministicWritePolicy | LLMWritePolicy | None = None,
    ) -> None:
        self._project_root = project_root
        self._agent_id = agent_id
        self._groups = groups or []
        self._expert_domains = expert_domains or []
        if rate_limiter_config is None:
            enforce = os.environ.get("TAPPS_BRAIN_RATE_LIMIT_ENFORCE", "").strip() in (
                "1",
                "true",
                "TRUE",
                "yes",
                "YES",
            )
            rate_limiter_config = RateLimiterConfig(enforce=enforce)
        self._rate_limiter = SlidingWindowRateLimiter(rate_limiter_config)
        # Profile before persistence so lexical FTS/BM25 settings apply at open.
        self._profile = self._resolve_profile(project_root, profile)
        _lexical = getattr(self._profile, "lexical", None) if self._profile is not None else None
        # Declarative hive memberships (EPIC-056): constructor args / env win,
        # profile.hive.groups / expert_domains are the fallback — without this
        # the profile fields are dead configuration.
        _hive_cfg = getattr(self._profile, "hive", None) if self._profile is not None else None
        if not self._groups and _hive_cfg is not None and _hive_cfg.groups:
            self._groups = list(_hive_cfg.groups)
        if not self._expert_domains and _hive_cfg is not None and _hive_cfg.expert_domains:
            self._expert_domains = list(_hive_cfg.expert_domains)

        self._maybe_auto_migrate_private_schema()

        # ADR-007: Postgres-only persistence plane. A PrivateBackend is required.
        private_backend = self._resolve_private_backend(project_root, agent_id, private_backend)
        # store_dir / encryption_key / lexical_config are legacy SQLite knobs —
        # kept in the signature for API compatibility but ignored on Postgres.
        _ = (store_dir, encryption_key, _lexical)
        # ``_persistence`` is a property backed by ``_persistence_backend``
        # plus a per-thread override (see ``_scoped_persistence``) so the
        # async wrapper can capture writes without mutating shared state.
        self._persistence_local = threading.local()
        self._persistence = private_backend
        # STORY-069.7: stash resolved project_id so instance methods can bind it
        # into structured logs.  Falls back to None for backends (e.g.
        # InMemoryPrivateBackend) that don't carry a project_id.
        self._project_id: str | None = getattr(private_backend, "_project_id", None)

        self._lock = threading.Lock()
        self._lock_timeout_sec = self._resolve_lock_timeout(lock_timeout_seconds)
        self._consolidation_config = self._resolve_consolidation_config(consolidation_config)
        self._init_embedding_provider(embedding_provider)

        self._write_rules = write_rules
        self._lookup_engine = lookup_engine
        self._consolidation_in_progress = False
        # Write-path policy (TAP-560/STORY-SC04). Resolve in precedence order:
        # 1. Explicit constructor argument.
        # 2. TAPPS_BRAIN_WRITE_POLICY env var.
        # 3. Profile write_policy.mode.
        # 4. Default → DeterministicWritePolicy (zero-cost, current behaviour).
        self._write_policy: DeterministicWritePolicy | LLMWritePolicy | None = (
            self._resolve_write_policy(write_policy)
        )
        self._gc_config = self._resolve_gc_config()
        self._metrics = MetricsCollector()
        self._hive_store = hive_store
        self._hive_agent_id = hive_agent_id

        # Cold-start: load entries + relations and rebuild derived indexes.
        relation_count = self._load_cold_start_state()
        # In-memory feedback / session / diagnostics / gap-signal state.
        self._init_in_memory_tracking_state()

        # Auto-register agent in Hive registry + join declared groups
        # (STORY-053.3 / STORY-056.1).
        if auto_register and self._agent_id is not None and self._hive_store is not None:
            self._auto_register_agent()
        if self._groups and self._hive_store is not None:
            self._setup_group_memberships()

        logger.info(
            "memory_store_initialized",
            project_root=str(project_root),
            entry_count=len(self._entries),
            relation_count=relation_count,
            auto_consolidation=self._consolidation_config.enabled,
        )

    @staticmethod
    def _maybe_auto_migrate_private_schema() -> None:
        """STORY-066.8: auto-migrate the private schema before the backend opens.

        Runs only when ``TAPPS_BRAIN_DATABASE_URL`` names a Postgres DSN and
        ``TAPPS_BRAIN_AUTO_MIGRATE=1`` (the latter checked inside
        ``maybe_auto_migrate_private``), so the schema is current before the
        first connection.
        """
        _auto_migrate_dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
        if _auto_migrate_dsn:
            from tapps_brain.postgres_connection import is_postgres_dsn

            if is_postgres_dsn(_auto_migrate_dsn):
                from tapps_brain.postgres_migrations import maybe_auto_migrate_private

                maybe_auto_migrate_private(_auto_migrate_dsn)

    @staticmethod
    def _resolve_private_backend(
        project_root: Path,
        agent_id: str | None,
        private_backend: PrivateBackend | None,
    ) -> PrivateBackend:
        """Return the caller's backend, or resolve one from the environment.

        ADR-007: when no backend is passed, resolve it from
        ``TAPPS_BRAIN_DATABASE_URL``.  Hard-fails when the env var is also
        missing — there is no SQLite fallback.
        """
        if private_backend is not None:
            return private_backend
        from tapps_brain.backends import (
            derive_project_id,
            resolve_private_backend_from_env,
        )

        _resolved_agent_id = agent_id or "default"
        # EPIC-069: honor TAPPS_BRAIN_PROJECT (human-readable slug) before
        # falling back to the legacy path-hash.  The env var is how MCP clients
        # connecting over stdio declare project identity — see ADR-010 and
        # project_resolver.resolve_project_id.
        _env_project = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
        if _env_project:
            from tapps_brain.project_resolver import validate_project_id

            _project_id = validate_project_id(_env_project)
        else:
            _project_id = derive_project_id(project_root)
        resolved = resolve_private_backend_from_env(_project_id, _resolved_agent_id)
        if resolved is None:
            msg = (
                "MemoryStore requires a Postgres private_backend (ADR-007). "
                "Set TAPPS_BRAIN_DATABASE_URL to a postgres:// or postgresql:// "
                "DSN, or pass an explicit private_backend constructed via "
                "tapps_brain.backends.create_private_backend(dsn, ...). "
                "SQLite is no longer supported."
            )
            raise ValueError(msg)
        return resolved

    @staticmethod
    def _resolve_lock_timeout(lock_timeout_seconds: float | None) -> float | None:
        """Resolve the store-lock timeout: explicit arg (>0) or the env default."""
        if lock_timeout_seconds is not None:
            return float(lock_timeout_seconds) if lock_timeout_seconds > 0 else None
        return _env_lock_timeout_seconds()

    def _resolve_consolidation_config(
        self,
        consolidation_config: ConsolidationConfig | None,
    ) -> ConsolidationConfig:
        """Resolve consolidation config: explicit arg, then profile, then default."""
        if consolidation_config is not None:
            return consolidation_config
        if self._profile is not None and hasattr(self._profile, "consolidation"):
            _pc = self._profile.consolidation
            return ConsolidationConfig(
                enabled=_pc.enabled,
                threshold=_pc.threshold,
                min_entries=_pc.min_entries,
            )
        return ConsolidationConfig()

    def _resolve_gc_config(self) -> Any:  # noqa: ANN401  # gc.GCConfig
        """Seed GC thresholds from the active profile's ``gc:`` block.

        Profiles ship materially different retention windows (e.g.
        home-automation: ``floor_retention_days: 7``); without this the
        profile settings were silently ignored and every store ran with
        the dataclass defaults. ``session_index_ttl_days`` has no profile
        field and keeps its default. Runtime overrides via
        :meth:`set_gc_config` still apply on top.
        """
        from tapps_brain.gc import GCConfig as _GCConfig

        prof_gc = getattr(self._profile, "gc", None) if self._profile is not None else None
        if prof_gc is None:
            return _GCConfig()
        return _GCConfig(
            floor_retention_days=prof_gc.floor_retention_days,
            session_expiry_days=prof_gc.session_expiry_days,
            contradicted_threshold=prof_gc.contradicted_threshold,
        )

    def _init_embedding_provider(
        self,
        embedding_provider: SentenceTransformerProvider | None,
    ) -> None:
        """Resolve + validate the embedding provider (TAP-2672 fail-loud).

        When ``TAPPS_BRAIN_EMBEDDING_REQUIRED=1`` and no provider could be
        loaded, raise rather than silently degrading semantic recall to
        BM25-only while health still reports ``db_ok=true``.
        """
        if embedding_provider is _UNSET_EMBEDDING:
            from tapps_brain.embeddings import get_embedding_provider

            self._embedding_provider = get_embedding_provider()
        else:
            self._embedding_provider = embedding_provider
        if (
            os.environ.get("TAPPS_BRAIN_EMBEDDING_REQUIRED", "0") == "1"
            and self._embedding_provider is None
        ):
            msg = (
                "TAPPS_BRAIN_EMBEDDING_REQUIRED=1 but no embedding provider could be "
                "loaded (sentence-transformers missing or model load failed). Semantic "
                "recall would silently degrade to BM25-only. Install tapps-brain[all], "
                "set HF_TOKEN / warm the model cache, or set "
                "TAPPS_BRAIN_EMBEDDING_REQUIRED=0 to allow lexical-only mode."
            )
            raise RuntimeError(msg)
        if self._embedding_provider is not None:
            from tapps_brain.embeddings import embedding_startup_status

            logger.info(
                "embedding_provider_loaded",
                **embedding_startup_status(self._embedding_provider),
            )

    def _load_cold_start_state(self) -> int:
        """Cold-start: load entries + relations, rebuild bloom + entity indexes.

        Returns the number of relations loaded (for the init log line).  Passes
        the effective max-entries cap so backends that support early-cutoff
        (e.g. PostgresPrivateBackend ``ORDER BY updated_at DESC``) can stop
        streaming once the most-recent entries up to the limit are collected.
        """
        self._entries: dict[str, MemoryEntry] = {}
        for entry in self._persistence.load_all(limit=self._max_entries):
            self._entries[entry.key] = entry

        # Removal tombstones for merge-resurrection protection (see
        # _merge_durable_entries / _note_removed_locked).
        self._removal_epoch: int = 0
        self._removed_at: dict[str, int] = {}

        # TAP-655: startup sanity check — warn if expected HNSW index is absent.
        _verify = getattr(self._persistence, "verify_expected_indexes", None)
        if callable(_verify):
            _verify()

        # Bloom filter for write-path deduplication (GitHub #31).
        self._bloom = BloomFilter()
        for _entry in self._entries.values():
            self._bloom.add(normalize_for_dedup(_entry.value))

        # Entity index for graph centrality scoring (TAP-734).  Maps BM25 token
        # → set of entry keys; derived state only, rebuilt from _entries here.
        self._entity_index: dict[str, set[str]] = {}
        for _entry in self._entries.values():
            self._index_entry_entities(_entry.key, _entry.value)

        # Cold-start: load all relations into memory, indexed by entry key.
        self._relations: dict[str, list[dict[str, Any]]] = {}
        all_relations = self._persistence.list_relations()
        for rel in all_relations:
            for src_key in rel["source_entry_keys"]:
                self._relations.setdefault(src_key, []).append(rel)
        return len(all_relations)

    def _init_in_memory_tracking_state(self) -> None:
        """Initialise the lazy feedback store + in-memory session / diagnostics /
        gap-signal state (EPIC-029 / 030 / 031).

        All session dicts must be accessed under ``_serialized()``.
        """
        # EPIC-029: Lazy-initialized feedback store.
        self._feedback_store_instance: FeedbackStore | InMemoryFeedbackStore | None = None

        # EPIC-029 story 029.3: in-memory session tracking for implicit feedback.
        # session_id → list of (entry_key, monotonic_time) recalled; and
        # session_id → set of entry_keys reinforced in the session.
        self._session_recall_log: dict[str, list[tuple[str, float]]] = {}
        self._session_reinforced: dict[str, set[str]] = {}

        # EPIC-029 story 029-4b: in-memory tracking for reformulation + correction.
        # _session_query_log: session_id → list of (query_text, recalled_keys, mono_time).
        # _session_recalled_values: session_id → list of (entry_key, entry_value, mono_time).
        self._session_query_log: dict[str, list[tuple[str, list[str], float]]] = {}
        self._session_recalled_values: dict[str, list[tuple[str, str, float]]] = {}

        # EPIC-029 story 029-7: session → hive memory key → namespace for feedback.
        self._hive_feedback_key_index: dict[str, dict[str, str]] = {}

        # EPIC-030: diagnostics circuit breaker + history (lazy).
        from tapps_brain.diagnostics import AnomalyDetector, CircuitBreaker

        self._circuit_breaker = CircuitBreaker()
        self._anomaly_detector = AnomalyDetector()
        self._diagnostics_history_store: Any = None
        self._hive_recall_weight_multiplier: float = 1.0

        # EPIC-031: weak gap signals from empty recall (bounded in-memory buffer).
        self._zero_result_queries: deque[tuple[str, str]] = deque(maxlen=2000)
        self._latest_quality_report: dict[str, Any] | None = None

        # STORY-032.6: last-known candidate counts for tapps_brain.* gauges.
        # Updated when health() or gc() is called; stale between runs is fine.
        self._last_consolidation_candidates: int = 0
        self._last_gc_candidates: int = 0

    def _auto_register_agent(self) -> None:
        """Register this agent in the Hive registry if not already present."""
        if self._hive_store is None or self._agent_id is None:
            return
        # _db_path is /dev/null for Postgres hive backends (sentinel value).
        # Only derive a local registry path for file-based backends.
        import pathlib

        from tapps_brain.backends import AgentRegistry
        from tapps_brain.models import AgentRegistration

        _db_path = getattr(self._hive_store, "_db_path", None)
        registry_path = (
            _db_path.parent / "agents.yaml"
            if _db_path is not None and _db_path != pathlib.Path("/dev/null")
            else None
        )
        registry = AgentRegistry(registry_path=registry_path)
        if registry.get(self._agent_id) is not None:
            return  # already registered
        profile_name = ""
        if self._profile is not None:
            profile_name = getattr(self._profile, "name", "")
        agent = AgentRegistration(
            id=self._agent_id,
            name=self._agent_id,
            profile=profile_name or "repo-brain",
            project_root=str(self._project_root),
        )
        registry.register(agent)

    def _setup_group_memberships(self) -> None:
        """Auto-create and join declared groups in the Hive (STORY-056.1).

        Fail-closed: groups that cannot be joined are dropped from
        ``self._groups`` so later scope checks do not assume membership.
        """
        if self._hive_store is None or not self._agent_id:
            return
        joined: list[str] = []
        for group_name in self._groups:
            try:
                self._hive_store.create_group(group_name)
                self._hive_store.add_group_member(group_name, self._agent_id)
                joined.append(group_name)
            except Exception:
                logger.warning(
                    "group_auto_join_failed",
                    group_name=group_name,
                    agent_id=self._agent_id,
                    exc_info=True,
                )
        if len(joined) != len(self._groups):
            self._groups = joined

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        """Serialize access to in-memory state and save-path critical sections (EPIC-050.2)."""
        lock = self._lock
        timeout = self._lock_timeout_sec
        if timeout is None:
            lock.acquire()
            try:
                yield
            finally:
                lock.release()
        else:
            if not lock.acquire(timeout=timeout):
                raise MemoryStoreLockTimeout(
                    f"MemoryStore lock not acquired within {timeout}s — another thread holds it. "
                    "Reduce concurrent load on this process or unset TAPPS_STORE_LOCK_TIMEOUT_S. "
                    "See docs/engineering/system-architecture.md § Concurrency model."
                )
            try:
                yield
            finally:
                lock.release()

    @property
    def _persistence(self) -> PrivateBackend:
        """The active persistence backend for the *calling thread*.

        Returns the per-thread override installed by
        :meth:`_scoped_persistence` when present, else the shared backend.
        The async wrapper (:mod:`tapps_brain.aio`) uses the override to
        capture writes for its native flush path — a thread-local keeps the
        capture invisible to concurrent sync-path writers on other threads
        (mutating the shared attribute used to let a sync save land in
        another request's capture backend and get lost on flush failure).
        """
        override = getattr(self._persistence_local, "override", None)
        if override is not None:
            return override  # type: ignore[no-any-return]
        return self._persistence_backend

    @_persistence.setter
    def _persistence(self, value: PrivateBackend) -> None:
        self._persistence_backend = value

    @contextmanager
    def _scoped_persistence(self, backend: PrivateBackend) -> Iterator[None]:
        """Route this thread's persistence calls through *backend*.

        Thread-local: other threads (and re-entrant calls after exit) keep
        using the shared backend.  Used by :mod:`tapps_brain.aio` to capture
        writes inside ``asyncio.to_thread`` workers.
        """
        self._persistence_local.override = backend
        try:
            yield
        finally:
            self._persistence_local.override = None

    @property
    def agent_id(self) -> str | None:
        """Return the agent identity used for storage isolation, or ``None``."""
        return self._agent_id

    def _postgres_session_index(self) -> Any:  # noqa: ANN401
        """Return a Postgres :class:`SessionIndex` when the private backend exposes one.

        Falls back to ``None`` for non-Postgres backends / missing tenant ids so
        callers can use the in-process session helpers.
        """
        cm = getattr(self._persistence, "_cm", None)
        project_id = self._project_id
        if cm is None or not project_id:
            return None
        from tapps_brain.session_index import SessionIndex

        # Prefer the private backend's agent_id so session chunks share the
        # same tenant key as private_memories / FeedbackStore.
        backend_agent = getattr(self._persistence, "_agent_id", None)
        agent_id = (
            str(backend_agent)
            if backend_agent is not None and str(backend_agent)
            else (self._agent_id or "unknown")
        )
        return SessionIndex(
            cm,
            project_id=str(project_id),
            agent_id=agent_id,
        )

    def document_store(self) -> Any:  # noqa: ANN401
        """Return a :class:`PostgresDocumentStore` for this tenant, or ``None``.

        Mirrors :meth:`_postgres_session_index` — requires the private
        backend's connection manager and a project id (TAP-4998).  Reads are
        project-scoped; ``agent_id`` records the writer.
        """
        cm = getattr(self._persistence, "_cm", None)
        project_id = self._project_id
        if cm is None or not project_id:
            return None
        from tapps_brain.documents import PostgresDocumentStore

        backend_agent = getattr(self._persistence, "_agent_id", None)
        agent_id = (
            str(backend_agent)
            if backend_agent is not None and str(backend_agent)
            else (self._agent_id or "unknown")
        )
        return PostgresDocumentStore(
            cm,
            project_id=str(project_id),
            agent_id=agent_id,
        )

    def _merge_durable_entries(self, *, limit: int | None = None) -> None:
        """Fill cache misses from ``load_all`` (durable overflow beyond cold-start).

        Never overwrites keys already present in ``_entries`` so in-memory
        mutations (and write-through newer cache state) are preserved.

        The ``load_all`` snapshot is taken outside the store lock, so a key
        may be evicted/deleted (cache pop + durable delete) while the snapshot
        is in flight — naively merging would resurrect it in the cache and
        push the store past ``max_entries``.  The removal-epoch check below
        skips any key removed after the snapshot began.
        """
        with self._serialized():
            snapshot_epoch = self._removal_epoch
        durable = self._persistence.load_all(limit=limit)
        with self._serialized():
            for entry in durable:
                if entry.key in self._entries:
                    continue
                if self._removed_at.get(entry.key, 0) > snapshot_epoch:
                    continue
                self._entries[entry.key] = entry
                # Keep derived indexes consistent with the cache: without the
                # bloom add, a later save() of an identical value skips dedup
                # ("bloom says absent") and creates a duplicate; without the
                # entity index, graph-centrality scoring never sees the entry.
                self._bloom.add(normalize_for_dedup(entry.value))
                self._index_entry_entities(entry.key, entry.value)

    def _note_removed_locked(self, key: str) -> None:
        """Record a cache+durable removal for merge-resurrection protection.

        Must be called while holding the store serialization lock, after the
        durable row was deleted.  Bounded: oldest half is pruned past 4096
        tombstones (deletes are rare; the tombstone only needs to outlive
        concurrent ``load_all`` snapshots).
        """
        self._removal_epoch += 1
        self._removed_at[key] = self._removal_epoch
        if len(self._removed_at) > 4096:
            cutoff = sorted(self._removed_at.values())[len(self._removed_at) // 2]
            self._removed_at = {k: e for k, e in self._removed_at.items() if e > cutoff}

    @property
    def groups(self) -> list[str]:
        """Return declared group memberships (EPIC-056)."""
        return list(self._groups)

    def refresh_group_membership(self) -> list[str]:
        """Reload group membership from ``TAPPS_BRAIN_GROUPS`` (scope-audit G-2).

        ``_groups`` is captured at construction; call this after Hive group
        membership changes so orphaned namespace writes are less likely.
        Returns the refreshed membership list.
        """
        raw = os.environ.get("TAPPS_BRAIN_GROUPS", "")
        refreshed = [g.strip() for g in raw.split(",") if g.strip()] if raw else []
        self._groups = refreshed
        return list(self._groups)

    @property
    def expert_domains(self) -> list[str]:
        """Return declared expert domains (EPIC-056)."""
        return list(self._expert_domains)

    @property
    def project_root(self) -> Path:
        """Return the project root path."""
        return self._project_root

    def get_consolidation_config(self) -> ConsolidationConfig:
        """Return the active consolidation configuration."""
        return self._consolidation_config

    def set_consolidation_config(self, config: ConsolidationConfig) -> None:
        """Update the consolidation configuration."""
        self._consolidation_config = config

    def get_gc_config(self) -> Any:  # noqa: ANN401
        """Return the active GCConfig instance."""
        return self._gc_config

    def set_gc_config(self, config: Any) -> None:  # noqa: ANN401
        """Update the GC configuration at runtime."""
        self._gc_config = config

    @property
    def rate_limiter(self) -> SlidingWindowRateLimiter:
        """Return the rate limiter instance for stats/config access."""
        return self._rate_limiter

    @staticmethod
    def _resolve_profile(project_root: Path, profile: Any) -> Any:  # noqa: ANN401
        """Resolve the active memory profile (EPIC-010, amended by EPIC-069).

        Order of precedence:

        1. Explicit ``profile=`` argument (any ``MemoryProfile``).
        2. **Project registry** when ``TAPPS_BRAIN_PROJECT`` and
           ``TAPPS_BRAIN_DATABASE_URL`` are both set — see ADR-010.
           Strict mode (``TAPPS_BRAIN_STRICT_PROJECTS=1``) will raise
           :class:`ProjectNotRegisteredError` for unknown IDs.
        3. Filesystem / built-in defaults from
           :func:`tapps_brain.profile.resolve_profile` (legacy path).

        Falls back gracefully to ``None`` if none of the above apply.
        """
        if profile is not None:
            return profile

        registry_profile = MemoryStore._resolve_profile_from_registry()
        if registry_profile is not None:
            return registry_profile

        try:
            from tapps_brain.profile import resolve_profile as _resolve

            return _resolve(project_root)
        except Exception:
            # Fall back to profile=None (code-default scoring/limits), but
            # never silently: a broken profile.yaml otherwise degrades the
            # store with no operator-visible signal, defeating
            # ProfileValidationError's fail-loud contract.
            logger.warning(
                "store.profile_resolve_failed",
                project_root=str(project_root),
                exc_info=True,
            )
            return None

    @staticmethod
    def _resolve_profile_from_registry() -> Any:  # noqa: ANN401
        """Hit the ``project_profiles`` registry when env is configured.

        Returns ``None`` when either env var is missing (preserving the
        single-tenant code path).  Strict-mode errors propagate so
        misconfigured clients fail loudly.
        """
        project_id = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
        dsn = (os.environ.get("TAPPS_BRAIN_DATABASE_URL") or "").strip()
        from tapps_brain.postgres_connection import is_postgres_dsn

        if not project_id or not is_postgres_dsn(dsn):
            return None
        try:
            from tapps_brain.postgres_connection import PostgresConnectionManager
            from tapps_brain.project_registry import (
                ProjectNotRegisteredError,
                ProjectRegistry,
            )
            from tapps_brain.project_resolver import validate_project_id
        except ImportError:
            return None

        validate_project_id(project_id)
        cm = PostgresConnectionManager(dsn)
        try:
            registry = ProjectRegistry(cm)
            # resolve() raises ProjectNotRegisteredError in strict mode.
            return registry.resolve(project_id)
        except ProjectNotRegisteredError:
            raise
        except Exception:
            # Any transport-level hiccup falls back to legacy resolution;
            # strict mode still surfaces the structured error above.
            logger.warning(
                "profile_registry_resolve_failed",
                project_id=project_id,
                exc_info=True,
            )
            return None
        finally:
            cm.close()

    def _resolve_write_policy(
        self,
        explicit: DeterministicWritePolicy | LLMWritePolicy | None,
    ) -> DeterministicWritePolicy | LLMWritePolicy | None:
        """Resolve the active write policy (TAP-560/STORY-SC04).

        Precedence:
        1. Explicit ``write_policy=`` constructor arg.
        2. ``TAPPS_BRAIN_WRITE_POLICY`` env var (``deterministic`` or ``llm``).
        3. Profile ``write_policy.mode`` (when a profile is active).
        4. ``None`` → store uses the built-in ADD path (equivalent to deterministic).
        """
        if explicit is not None:
            return explicit

        from tapps_brain.write_policy import build_write_policy

        # Env var takes precedence over profile.
        env_mode = os.environ.get("TAPPS_BRAIN_WRITE_POLICY", "").strip().lower()
        profile_mode = ""
        profile_judge_model = "claude-3-5-haiku-20241022"
        profile_rate_limit = 60
        profile_candidates = 5
        if self._profile is not None:
            _wp_cfg = getattr(self._profile, "write_policy", None)
            if _wp_cfg is not None:
                profile_mode = getattr(_wp_cfg, "mode", "deterministic").strip().lower()
                profile_judge_model = getattr(_wp_cfg, "llm_judge_model", profile_judge_model)
                profile_rate_limit = getattr(_wp_cfg, "rate_limit_per_minute", profile_rate_limit)
                profile_candidates = getattr(_wp_cfg, "candidates_limit", profile_candidates)

        mode = env_mode or profile_mode
        if not mode or mode == "deterministic":
            return None  # None → store uses fast ADD path; no extra overhead.

        if mode == "llm":
            judge = self._build_llm_judge(profile_judge_model)
            if judge is None:
                logger.warning(
                    "write_policy.llm.no_judge",
                    detail=(
                        "TAPPS_BRAIN_WRITE_POLICY=llm but no LLM SDK is available. "
                        "Falling back to deterministic mode. "
                        "Install anthropic or openai to enable LLM-assisted writes."
                    ),
                )
                return None
            try:
                return build_write_policy(
                    "llm",
                    judge=judge,
                    candidates_limit=profile_candidates,
                    rate_limit_per_minute=profile_rate_limit,
                )
            except ValueError:
                logger.warning(
                    "write_policy.build_failed",
                    mode=mode,
                    exc_info=True,
                )
                return None

        logger.warning("write_policy.unknown_mode", mode=mode)
        return None

    @staticmethod
    def _build_llm_judge(model: str) -> Any:  # noqa: ANN401
        """Instantiate the best available LLM judge (lazy, no hard dependency).

        Distinguishes between two failure modes:
        - ``ImportError``: the optional dependency (anthropic/openai) is not installed
          — silent graceful degradation.
        - Any other exception (e.g. bad API key, network error): a real configuration
          problem — logged at WARNING so operators can act on it.
        """
        anthropic_available = False
        openai_available = False

        try:
            from tapps_brain.evaluation import AnthropicJudge
        except ImportError:
            pass  # anthropic optional dependency not installed
        else:
            anthropic_available = True
            try:
                return AnthropicJudge(model=model)
            except Exception as exc:
                logger.warning(
                    "store.llm_judge.anthropic_init_failed",
                    error=str(exc),
                    model=model,
                    exc_info=True,
                )

        try:
            from tapps_brain.evaluation import OpenAIJudge
        except ImportError:
            pass  # openai optional dependency not installed
        else:
            openai_available = True
            try:
                return OpenAIJudge()
            except Exception as exc:
                logger.warning(
                    "store.llm_judge.openai_init_failed",
                    error=str(exc),
                    exc_info=True,
                )

        if not (anthropic_available or openai_available):
            logger.info(
                "store.llm_judge.extras_not_installed",
                hint="Install the 'anthropic' or 'openai' extra to enable LLM flywheel scoring.",
            )

        return None

    @property
    def profile(self) -> Any:  # noqa: ANN401
        """Return the active ``MemoryProfile``, or ``None``."""
        return self._profile

    def _get_decay_config(self) -> Any:  # noqa: ANN401
        """Return a ``DecayConfig`` derived from the active profile (EPIC-010)."""
        if self._profile is not None:
            try:
                from tapps_brain.decay import decay_config_from_profile

                return decay_config_from_profile(self._profile)
            except Exception:
                logger.warning("decay_config_from_profile_failed", exc_info=True)
        from tapps_brain.decay import DecayConfig

        return DecayConfig()

    @property
    def _max_entries(self) -> int:
        """Return the max-entries limit.

        Precedence (TAP-513): YAML profile (``limits.max_entries``) >
        ``TAPPS_BRAIN_MAX_ENTRIES`` env var > module default ``5000``.
        Env var resolution is per-call so deployed brains can be retuned
        without restart (env reads are cheap).
        """
        if self._profile is not None:
            try:
                return int(self._profile.limits.max_entries)
            except (AttributeError, TypeError, ValueError):
                pass  # profile.limits.max_entries absent or non-numeric; fall through to env
        return _max_entries_from_env()

    @property
    def _max_entries_per_group(self) -> int | None:
        """Return optional per-``memory_group`` cap, or None when disabled."""
        if self._profile is not None:
            try:
                raw = self._profile.limits.max_entries_per_group
            except AttributeError:
                return None  # profile.limits.max_entries_per_group not set
            if raw is None:
                return None
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None  # non-numeric cap value; disable the limit
        return None

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def save(
        self,
        key: str,
        value: str,
        tier: str = "pattern",
        source: str = "agent",
        source_agent: str = "unknown",
        scope: str = "project",
        tags: list[str] | None = None,
        branch: str | None = None,
        confidence: float = -1.0,
        agent_scope: str = "private",
        source_session_id: str = "",
        source_channel: str = "",
        source_message_id: str = "",
        triggered_by: str = "",
        memory_group: str | None | object = MEMORY_GROUP_UNSET,
        temporal_sensitivity: Literal["high", "medium", "low"] | None = None,
        failed_approaches: list[str] | None = None,
        status: str | None = None,
        stale_reason: str | None = None,
        stale_date: str | None = None,
        superseded_by: str | None = None,
        valid_at: str | None = None,
        *,
        skip_consolidation: bool = False,
        session_id: str | None = None,
        dedup: bool = True,
        conflict_check: bool = True,
        auto_publish: bool = True,
    ) -> MemoryEntry | dict[str, Any]:
        """Save or update a memory entry.

        Returns the saved ``MemoryEntry``, or an error dict if RAG safety
        blocks the content.

        Rate-limit exemption is granted by wrapping trusted internal bulk
        operations with :func:`~tapps_brain.rate_limiter.batch_exempt_scope`.
        Never accept a caller-supplied exemption string from HTTP/MCP/CLI.
        The save path is decomposed across helper modules
        (:mod:`tapps_brain._save_pipeline`, :mod:`tapps_brain._save_conflict`,
        :mod:`tapps_brain._save_propagation`) and focused private methods
        below (TAP-602).  Public behaviour, error dict shapes, and metric /
        log names are unchanged.

        Args:
            key: Unique identifier for the memory.
            value: Memory content.
            tier: Memory tier (architectural, pattern, procedural, context).
            source: Source of the memory (human, agent, inferred, system).
            source_agent: Identifier of the agent saving the memory.
            scope: Visibility scope (project, branch, session).
            agent_scope: Hive propagation scope (private, domain, hive, group:<name>).
            source_session_id: Session ID that triggered this memory (GitHub #38).
            source_channel: Channel/surface where memory originated (GitHub #38).
            source_message_id: Message ID that triggered this memory (GitHub #38).
            triggered_by: Event or action that triggered this memory (GitHub #38).
            memory_group: Optional project-local partition (GitHub #49). Use
                :data:`~tapps_brain.memory_group.MEMORY_GROUP_UNSET` to preserve
                the existing value on update; pass ``None`` or ``\"\"`` after
                normalize to clear; pass a non-empty string to set.
            tags: Tags for categorization.
            branch: Git branch name (required when scope=branch).
            confidence: Confidence score (-1.0 for auto from source).
            skip_consolidation: If True, skip auto-consolidation check.
            session_id: Optional session identifier for implicit feedback tracking
                (STORY-029.3).  Used by 029-4b correction detection.
            conflict_check: When True, check for entries that may conflict with
                the new value before saving.  Conflicting entries (same tier,
                high similarity, different content) are logged as warnings,
                marked ``contradicted`` with a ``contradiction_reason``, and
                their ``invalid_at`` field is set to now (GitHub #44, task 040.16).
                Similarity cutoff comes from ``profile.conflict_check`` when a
                profile is loaded. Defaults to True for safer writes.
        """
        log = logger.bind(project_id=self._project_id, op="save", key=key)
        log.debug("store.save.begin")

        # Validate enum-typed inputs before any side-effectful phase: dedup
        # can reinforce another entry and conflict-check can mark entries
        # contradicted before _construct_memory_entry would reject a bad
        # scope/source — mutations that would survive the failed save.
        MemorySource(source)
        MemoryScope(scope)

        # TAP-2675: normalise non-str values *before* the profile length
        # check below — len() on a dict counts keys (undercounting the JSON
        # text that actually persists) and raises TypeError on int/float.
        value = _ensure_str_value(value)

        limit_error = self._profile_limit_error(key, value, tags)
        if limit_error is not None:
            raise ValueError(limit_error)

        # Phases 1-5 — validate, safety, write-policy, dedup, conflict (TAP-2800
        # extracted these into the shared prepare step used by save_many too).
        prep = self._prepare_save(
            key=key,
            value=value,
            tier=tier,
            source_agent=source_agent,
            agent_scope=agent_scope,
            memory_group=memory_group,
            dedup=dedup,
            conflict_check=conflict_check,
        )
        if not isinstance(prep, _SavePrep):
            # Short-circuit: an error dict, or a MemoryEntry from a dedup hit /
            # write-policy decision — return it unchanged.
            return prep

        # Phase 6 — build, persist, propagate under span + timer.
        self._metrics.increment("store.save")
        with (
            start_span(
                SPAN_REMEMBER,
                {
                    "memory.tier": prep.tier,
                    "memory.scope": scope,
                    "memory.agent_scope": prep.agent_scope,
                    "gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL,
                },
            ),
            MetricsTimer(self._metrics, "store.save_ms"),
        ):
            entry, existing = self._build_and_assign_entry(
                key=prep.effective_key or key,
                value=prep.value,
                tier=prep.tier,
                source=source,
                source_agent=prep.source_agent,
                scope=scope,
                tags=tags,
                branch=branch,
                confidence=confidence,
                agent_scope=prep.agent_scope,
                source_session_id=source_session_id,
                source_channel=source_channel,
                source_message_id=source_message_id,
                triggered_by=triggered_by,
                memory_group=memory_group,
                mg_explicit=prep.mg_explicit,
                temporal_sensitivity=temporal_sensitivity,
                failed_approaches=failed_approaches,
                conflict_valid_at=valid_at or prep.conflict_valid_at,
                status=status,
                stale_reason=stale_reason,
                stale_date=stale_date,
                superseded_by=superseded_by,
            )

            persist_key = prep.effective_key or key
            entry = self._embed_entry(persist_key, prep.value, entry)
            self._persist_entry_or_rollback(persist_key, entry, existing=existing, dedup=dedup)
            self._postprocess_saved_entry(
                persist_key,
                entry,
                existing,
                value=prep.value,
                agent_scope=prep.agent_scope,
                tier=prep.tier,
                auto_publish=auto_publish,
                skip_consolidation=skip_consolidation,
            )

        # Phase 7 — recall-then-store correction detection (outside the span).
        if session_id is not None:
            self._emit_correction_feedback(session_id, entry.value)

        return entry

    def _profile_limit_error(self, key: str, value: str, tags: list[str] | None) -> str | None:
        """Return an error message when profile-tightened limits reject the row.

        The MemoryEntry model enforces the global constants (128/4096/10), but
        a profile may set *stricter* caps — without this check those three
        fields are dead configuration and the onboarding doc lies about them.
        Only limits stricter than the model constants are enforced here; at or
        above them Pydantic raises its own ValidationError, which callers pin
        on.  Shared by :meth:`save` (raises) and :meth:`_prepare_batch_entry`
        (returns a per-row error dict).
        """
        if self._profile is None:
            return None
        lim = getattr(self._profile, "limits", None)
        if lim is None:
            return None
        from tapps_brain.models import MAX_KEY_LENGTH, MAX_TAGS, MAX_VALUE_LENGTH

        if lim.max_key_length < MAX_KEY_LENGTH and len(key) > lim.max_key_length:
            return f"Key exceeds profile limit of {lim.max_key_length} characters."
        if lim.max_value_length < MAX_VALUE_LENGTH and len(value) > lim.max_value_length:
            return f"Value exceeds profile limit of {lim.max_value_length} characters."
        if lim.max_tags < MAX_TAGS and tags is not None and len(tags) > lim.max_tags:
            return f"Too many tags (max {lim.max_tags} per profile limit)."
        return None

    def _prepare_save(
        self,
        *,
        key: str,
        value: str,
        tier: str,
        source_agent: str,
        agent_scope: str,
        memory_group: str | None | object,
        dedup: bool,
        conflict_check: bool,
    ) -> _SavePrep | MemoryEntry | dict[str, Any]:
        """Run the pre-persist save phases shared by :meth:`save` and
        :meth:`save_many` (TAP-2800).

        Returns a :class:`_SavePrep` when the caller should proceed to build +
        persist, or a short-circuit result the caller returns as-is: an error
        ``dict`` (scope / write-rules / safety failure) or a :class:`MemoryEntry`
        (dedup hit, or a write-policy NOOP/DELETE decision).
        """
        # TAP-2675: normalise non-str values (e.g. a JSON object posted to
        # /v1/remember) before the safety scan + text-column persistence, which
        # both assume a str.  See _ensure_str_value.
        value = _ensure_str_value(value)

        # Phase 1 — scope + memory_group validation (pure).
        scope_result = validate_scope_and_group(
            agent_scope=agent_scope,
            memory_group=memory_group,
            groups=self._groups,
        )
        if isinstance(scope_result, dict):
            return scope_result
        agent_scope = scope_result.agent_scope
        mg_explicit = scope_result.mg_explicit

        # Auto-fill source_agent from store identity (STORY-053.2)
        if source_agent == "unknown" and self._agent_id is not None:
            source_agent = self._agent_id
        tier = normalize_save_tier(tier, self._profile)

        # Phase 2 — write-rules + rate limiter + safety.
        wr_error = _validate_write_rules(key, value, self._write_rules)
        if wr_error is not None:
            return {"error": "write_rules_violation", "message": wr_error}

        self._check_rate_limit(key)

        safety_outcome = apply_safety_check(
            key=key,
            value=value,
            profile=self._profile,
            metrics=self._metrics,
        )
        if isinstance(safety_outcome, dict):
            return safety_outcome
        value = safety_outcome.value

        # Phase 3 — optional write-policy gate (may short-circuit).
        wp_short = self._apply_write_policy(key, value)
        update_key: str | None = None
        if isinstance(wp_short, dict) and wp_short.get("write_policy") == "update":
            update_key = str(wp_short["target_key"])
        elif wp_short is not None:
            return wp_short

        # Phases 4-5 run *durable* side effects: dedup can reinforce another
        # entry and conflict-check marks similar entries contradicted.  Skip
        # both when the key or value is going to fail model validation anyway
        # — _construct_memory_entry still raises the same ValidationError, but
        # no side effects survive the failed save.
        from tapps_brain.models import _KEY_SLUG_PATTERN, MAX_VALUE_LENGTH

        persist_key = key if update_key is None else update_key
        will_fail_validation = (
            not _KEY_SLUG_PATTERN.fullmatch(persist_key) or len(value) > MAX_VALUE_LENGTH
        )

        conflict_valid_at: str | None = None
        if not will_fail_validation:
            # Phase 4 — dedup fast-path.
            dedup_short = self._handle_dedup(persist_key, value, dedup)
            if dedup_short is not None:
                return dedup_short

            # Phase 5 — conflict detection (opt-in) marks superseded entries.
            conflict_valid_at = self._handle_conflicts(persist_key, value, tier, conflict_check)

        return _SavePrep(
            value=value,
            agent_scope=agent_scope,
            tier=tier,
            source_agent=source_agent,
            mg_explicit=mg_explicit,
            conflict_valid_at=conflict_valid_at,
            effective_key=update_key,
        )

    def _postprocess_saved_entry(
        self,
        key: str,
        entry: MemoryEntry,
        existing: MemoryEntry | None,
        *,
        value: str,
        agent_scope: str,
        tier: str,
        auto_publish: bool,
        skip_consolidation: bool,
    ) -> None:
        """Post-persist fan-out shared by :meth:`save` and :meth:`save_many`.

        Audit log, entity-index refresh, Hive/group/expert propagation, relation
        persistence, and the optional consolidation pass.  Must run *after* the
        entry is durably persisted.  Callers invoke this inside the
        ``SPAN_REMEMBER`` span so the child timers nest correctly.
        """
        self._emit_save_audit(key, entry, existing=existing)
        self._refresh_entity_index(key, entry, existing_present=existing is not None)

        # Hive + group + expert fan-out (best-effort).
        if self._hive_store is not None:
            with MetricsTimer(self._metrics, "store.save.phase.hive_ms"):
                self._propagate_to_hive(entry)
        propagate_group_save(
            entry=entry,
            agent_scope=agent_scope,
            groups=self._groups,
            hive_store=self._hive_store,
        )
        _hive_prof = getattr(self._profile, "hive", None) if self._profile is not None else None
        publish_to_experts(
            entry=entry,
            tier=tier,
            agent_scope=agent_scope,
            expert_domains=self._expert_domains,
            hive_store=self._hive_store,
            auto_publish=auto_publish,
            publish_tiers=(
                tuple(_hive_prof.auto_publish_tiers)
                if _hive_prof is not None
                else ("architectural", "pattern")
            ),
        )

        self._persist_relations(key, value, created_at=entry.created_at)

        if (
            self._consolidation_config.enabled
            and not skip_consolidation
            and not self._consolidation_in_progress
        ):
            with MetricsTimer(self._metrics, "store.save.phase.consolidate_ms"):
                self._maybe_consolidate(entry)

    def save_many(
        self,
        items: list[dict[str, Any]],
    ) -> list[MemoryEntry | dict[str, Any]]:
        """Persist many entries with a single batched DB round-trip (TAP-2800).

        Each *item* is a kwargs mapping mirroring :meth:`save` (``key`` and
        ``value`` required; the rest optional).  The per-row pre-persist pipeline
        (validation, dedup, conflict detection) runs in memory, then ONE batched
        ``save_many`` persists every valid row, then the per-row post-persist
        fan-out runs.  This replaces the old N-independent-INSERT loop where each
        entry issued its own write-through round-trip.

        Returns a list aligned 1:1 with *items*: each element is the saved
        :class:`MemoryEntry`, or the short-circuit result :meth:`save` would have
        returned for that row (an error ``dict``, a dedup-hit entry, …).  A row
        that fails validation does **not** abort the batch.  Falls back to a
        per-row :meth:`save` persist loop when the backend exposes no
        ``save_many`` primitive.
        """
        results: list[MemoryEntry | dict[str, Any] | None] = [None] * len(items)
        pending: list[tuple[int, MemoryEntry, MemoryEntry | None]] = []
        backend_save_many = getattr(self._persistence, "save_many", None)

        self._metrics.increment("store.save_many")
        with (
            start_span(
                SPAN_REMEMBER,
                {
                    "memory.batch_size": len(items),
                    "gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL,
                },
            ),
            MetricsTimer(self._metrics, "store.save_many_ms"),
        ):
            try:
                for idx, item in enumerate(items):
                    built = self._prepare_batch_entry(item)
                    if isinstance(built, tuple):
                        pending.append((idx, built[0], built[1]))
                    else:
                        results[idx] = built  # short-circuit (error dict / dedup entry)
            except Exception:
                # A row raised past the per-row handler (rate limit, lock
                # timeout, …).  Earlier valid rows were already assigned into
                # the cache but never persisted — roll them back so the cache
                # doesn't advertise rows Postgres never saw.
                if pending:
                    with self._serialized():
                        for _, entry, existing in pending:
                            if existing is not None:
                                self._entries[entry.key] = existing
                            else:
                                self._entries.pop(entry.key, None)
                        self._bloom = BloomFilter()
                        for _e in self._entries.values():
                            self._bloom.add(normalize_for_dedup(_e.value))
                raise

            # Single batched persist for all valid rows (TAP-2800).
            if pending:
                self._persist_many_or_rollback(
                    [(entry, existing) for _, entry, existing in pending],
                    backend_save_many=backend_save_many,
                )
                # Same evict/persist resurrection guard as single save(): a
                # concurrent delete between cache assignment and the batched
                # persist must not leave a zombie durable row.
                for _, entry, _existing in pending:
                    self._drop_if_concurrently_removed(entry.key)

            # Per-row post-persist fan-out + result assembly.
            for idx, entry, existing in pending:
                item = items[idx]
                tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                self._postprocess_saved_entry(
                    entry.key,
                    entry,
                    existing,
                    value=entry.value,
                    agent_scope=entry.agent_scope,
                    tier=tier_str,
                    auto_publish=item.get("auto_publish", True),
                    skip_consolidation=item.get("skip_consolidation", False),
                )
                results[idx] = entry
                sid = item.get("session_id")
                if sid is not None:
                    self._emit_correction_feedback(sid, entry.value)

        return cast("list[MemoryEntry | dict[str, Any]]", results)

    def _prepare_batch_entry(
        self, item: dict[str, Any]
    ) -> tuple[MemoryEntry, MemoryEntry | None] | MemoryEntry | dict[str, Any]:
        """Run the pre-persist pipeline for one ``save_many`` row (TAP-2800).

        Returns ``(entry, existing)`` when the row should be persisted, or a
        short-circuit result (an error ``dict`` or a dedup-hit / write-policy
        result) that the caller stores verbatim — mirroring :meth:`save`'s
        per-row semantics so one bad row never aborts the batch.
        """
        from pydantic import ValidationError as _PydanticValidationError

        key = item.get("key", "")

        # Mirror save()'s up-front guards per row: enum conversion inside
        # _construct_memory_entry raises a bare ValueError (not a Pydantic
        # ValidationError), which previously escaped the batch and stranded
        # earlier cache-assigned rows; profile-tightened limits were not
        # enforced at all on the batch path.
        try:
            MemorySource(item.get("source", "agent"))
            MemoryScope(item.get("scope", "project"))
        except ValueError as exc:
            # "detail" is the canonical envelope key (openapi_contract.py);
            # "message" is kept as a legacy alias for older consumers.
            return {"error": "bad_request", "detail": str(exc), "message": str(exc)}
        limit_error = self._profile_limit_error(
            key, _ensure_str_value(item.get("value", "")), item.get("tags")
        )
        if limit_error is not None:
            return {"error": "bad_request", "detail": limit_error, "message": limit_error}

        prep = self._prepare_save(
            key=key,
            value=item.get("value", ""),
            tier=item.get("tier", "pattern"),
            source_agent=item.get("source_agent", "unknown"),
            agent_scope=item.get("agent_scope", "private"),
            memory_group=item.get("memory_group", MEMORY_GROUP_UNSET),
            dedup=item.get("dedup", True),
            conflict_check=item.get("conflict_check", True),
        )
        if not isinstance(prep, _SavePrep):
            return prep
        persist_key = prep.effective_key or key
        try:
            entry, existing = self._build_and_assign_entry(
                key=persist_key,
                value=prep.value,
                tier=prep.tier,
                source=item.get("source", "agent"),
                source_agent=prep.source_agent,
                scope=item.get("scope", "project"),
                tags=item.get("tags"),
                branch=item.get("branch"),
                confidence=item.get("confidence", -1.0),
                agent_scope=prep.agent_scope,
                source_session_id=item.get("source_session_id", ""),
                source_channel=item.get("source_channel", ""),
                source_message_id=item.get("source_message_id", ""),
                triggered_by=item.get("triggered_by", ""),
                memory_group=item.get("memory_group", MEMORY_GROUP_UNSET),
                mg_explicit=prep.mg_explicit,
                temporal_sensitivity=item.get("temporal_sensitivity"),
                failed_approaches=item.get("failed_approaches"),
                conflict_valid_at=item.get("valid_at") or prep.conflict_valid_at,
                status=item.get("status"),
                stale_reason=item.get("stale_reason"),
                stale_date=item.get("stale_date"),
                superseded_by=item.get("superseded_by"),
            )
            entry = self._embed_entry(persist_key, prep.value, entry)
        except _PydanticValidationError as exc:
            # Mirror memory_save's TAP-747 handling per row so one bad row
            # surfaces a structured error without aborting the batch.
            errs = exc.errors()
            msg = errs[0].get("msg", str(exc)) if errs else str(exc)
            return {"error": "bad_request", "detail": msg, "message": msg}
        except ValueError as exc:
            # Bare ValueError from enum conversion or similar — surface as a
            # per-row error instead of aborting the batch (ValidationError is
            # a ValueError subclass, so this arm must come second).
            return {"error": "bad_request", "detail": str(exc), "message": str(exc)}
        return entry, existing

    def _persist_many_or_rollback(
        self,
        entries: list[tuple[MemoryEntry, MemoryEntry | None]],
        *,
        backend_save_many: Callable[[list[MemoryEntry]], None] | None,
    ) -> None:
        """Persist a batch of already-built entries in one round-trip, rolling the
        in-memory cache back for the whole batch on failure (TAP-2800).

        *entries* is a list of ``(new_entry, existing_or_None)`` — *existing* is
        used to restore the prior cache value on rollback.  When the backend has
        no ``save_many`` primitive, falls back to a per-entry ``save`` loop so the
        write still completes (just without the batching win).
        """
        to_save = [entry for entry, _ in entries]
        persisted_keys: set[str] = set()
        try:
            with MetricsTimer(self._metrics, "store.save.phase.persist_ms"):
                if backend_save_many is not None:
                    backend_save_many(to_save)
                else:
                    for entry in to_save:
                        self._persistence.save(entry)
                        persisted_keys.add(entry.key)
        except Exception:
            with self._serialized():
                # Batch path: the backend save_many is transactional, so no row
                # is durable — roll back everything.  Fallback per-row path:
                # rows persisted before the failure ARE durable; rolling their
                # cache slots back would make the cache disagree with Postgres
                # (and the next hydration would resurrect the new value anyway).
                for entry, existing in entries:
                    if entry.key in persisted_keys:
                        continue
                    if existing is not None:
                        self._entries[entry.key] = existing
                    else:
                        self._entries.pop(entry.key, None)
                # A row may have used dedup → rebuild the bloom filter from the
                # restored cache; the filter has no item-remove (TAP-644).
                self._bloom = BloomFilter()
                for _e in self._entries.values():
                    self._bloom.add(normalize_for_dedup(_e.value))
            raise

    # ------------------------------------------------------------------
    # save() helpers — see TAP-602 decomposition for design rationale.
    # ------------------------------------------------------------------

    def _check_rate_limit(self, key: str) -> None:
        """Emit a rate-limit log; raise when the limiter is in enforce mode."""
        rate_result = self._rate_limiter.check()
        if rate_result.minute_exceeded or rate_result.lifetime_exceeded:
            logger.warning(
                "memory_save_rate_warning",
                key=key,
                minute_count=rate_result.current_minute_count,
                lifetime_count=rate_result.current_lifetime_count,
                allowed=rate_result.allowed,
            )
        if not rate_result.allowed:
            from tapps_brain.errors import BrainRateLimitedError

            raise BrainRateLimitedError(
                rate_result.message
                or (
                    f"Rate limit exceeded: {rate_result.current_minute_count} writes/min "
                    f"(limit: {self._rate_limiter.config.writes_per_minute})"
                )
            )

    def _apply_write_policy(  # noqa: PLR0911
        self,
        key: str,
        value: str,
    ) -> MemoryEntry | dict[str, Any] | None:
        """Consult ``self._write_policy`` (TAP-560/STORY-SC04).

        Returns ``None`` when the caller should continue with the normal save
        path (the default DeterministicWritePolicy result, or ADD/UPDATE from
        an LLM policy).  Returns a ``MemoryEntry`` or error dict when the
        policy short-circuits with NOOP or DELETE.
        """
        if self._write_policy is None:
            return None

        from tapps_brain.write_policy import DeterministicWritePolicy, WriteDecision

        # DeterministicWritePolicy is documented as a zero-cost no-op that
        # returns ADD unconditionally — skip the full-table durable merge and
        # candidate assembly it would never look at.
        if type(self._write_policy) is DeterministicWritePolicy:
            return None

        # Refresh from durable store so policy decisions are not cache-only.
        self._merge_durable_entries()
        with self._serialized():
            candidates = list(self._entries.values())
        result = self._write_policy.decide(key, value, candidates)

        if result.decision == WriteDecision.NOOP:
            self._metrics.increment("store.save.write_policy.noop")
            logger.info(
                "memory_save_write_policy_noop",
                key=key,
                reasoning=result.reasoning,
            )
            # A NOOP usually means an equivalent memory already exists — often
            # under a *different* key (result.target_key).  Prefer returning
            # that entry so callers get the duplicate instead of an error dict.
            with self._serialized():
                existing_noop = self._entries.get(key)
                if existing_noop is None and result.target_key:
                    existing_noop = self._entries.get(result.target_key)
            if existing_noop is not None:
                return existing_noop
            return {"write_policy": "noop", "key": key, "reasoning": result.reasoning}

        if result.decision == WriteDecision.DELETE and result.target_key:
            candidate_keys = {c.key for c in candidates}
            if result.target_key not in candidate_keys:
                logger.warning(
                    "memory_save_write_policy_delete_unscoped",
                    key=key,
                    target_key=result.target_key,
                    reasoning=result.reasoning,
                )
                # Fall through to ADD rather than deleting an arbitrary key.
                return None
            self._metrics.increment("store.save.write_policy.delete")
            logger.info(
                "memory_save_write_policy_delete",
                key=key,
                target_key=result.target_key,
                reasoning=result.reasoning,
            )
            self.delete(result.target_key)
            return {
                "write_policy": "delete",
                "deleted_key": result.target_key,
                "reasoning": result.reasoning,
            }

        if result.decision == WriteDecision.UPDATE and result.target_key:
            candidate_keys = {c.key for c in candidates}
            if result.target_key not in candidate_keys:
                logger.warning(
                    "memory_save_write_policy_update_unscoped",
                    key=key,
                    target_key=result.target_key,
                    reasoning=result.reasoning,
                )
                return None  # fall through to ADD under the incoming key
            self._metrics.increment("store.save.write_policy.update")
            logger.info(
                "memory_save_write_policy_update",
                key=key,
                target_key=result.target_key,
                reasoning=result.reasoning,
            )
            return {
                "write_policy": "update",
                "target_key": result.target_key,
                "reasoning": result.reasoning,
            }

        # ADD / unexpected → fall through.
        if result.decision != WriteDecision.ADD:
            logger.debug(
                "memory_save_write_policy_passthrough",
                key=key,
                decision=result.decision.value,
            )
        return None

    def _handle_dedup(
        self,
        key: str,
        value: str,
        dedup: bool,
    ) -> MemoryEntry | None:
        """Bloom-filter dedup fast-path (GitHub #31).

        Returns the reinforced existing entry when a duplicate is found;
        ``None`` otherwise.  The bloom filter is always updated so later
        saves see this value.
        """
        if not dedup:
            return None
        normalized = normalize_for_dedup(value)
        if self._bloom.might_contain(normalized):
            # Bloom positives are confirmed against durable rows when possible.
            self._merge_durable_entries()
            dup_key: str | None = None
            with self._serialized():
                for existing in self._entries.values():
                    if normalize_for_dedup(existing.value) == normalized:
                        dup_key = existing.key
                        break
            if dup_key is not None:
                logger.debug("memory_dedup_bloom_hit", key=key, existing_key=dup_key)
                self._metrics.increment("store.save.dedup_skip")
                try:
                    return self.reinforce(dup_key)
                except KeyError:
                    pass  # Entry was deleted between check and reinforce; proceed with save.
        # Under the lock: gc() holds the lock for its whole bloom rebuild and
        # BloomFilter.add can auto-resize (reallocate _bits, reset _count) —
        # an unlocked add racing that rebuild loses this entry's bits or
        # interleaves the clear/resize non-atomically.
        with self._serialized():
            self._bloom.add(normalized)
        return None

    def _handle_conflicts(
        self,
        key: str,
        value: str,
        tier: str,
        conflict_check: bool,
    ) -> str | None:
        """Detect and invalidate conflicting entries; return shared valid_at.

        Wraps :func:`tapps_brain._save_conflict.plan_conflicts`.  The returned
        timestamp should be used as ``valid_at`` on the new entry to keep the
        temporal chain (EPIC-004) coherent.
        """
        if not conflict_check:
            return None

        # Conflict detection must see durable rows, not only the cold-start cap.
        self._merge_durable_entries()
        with self._serialized():
            entries_snapshot = list(self._entries.values())
        similarity_threshold = resolve_similarity_threshold(self._profile, tier)
        plan: ConflictPlan | None = plan_conflicts(
            key=key,
            value=value,
            tier=tier,
            entries_snapshot=entries_snapshot,
            similarity_threshold=similarity_threshold,
            now=_utc_now_iso(),
        )
        if plan is None:
            return None

        logger.warning(
            "memory_save_conflicts_detected",
            key=key,
            conflicting_keys=plan.conflict_keys,
            similarity_threshold=plan.similarity_threshold,
            conflicts=plan.audit,
        )

        for conflict_key, reason in plan.invalidations:
            invalidated: MemoryEntry | None = None
            previous: MemoryEntry | None = None
            with self._serialized():
                current = self._entries.get(conflict_key)
                if current is not None and current.invalid_at is None:
                    previous = current
                    invalidated = current.model_copy(
                        update={
                            "invalid_at": plan.now,
                            "updated_at": plan.now,
                            "contradicted": True,
                            "contradiction_reason": reason,
                        }
                    )
                    self._entries[conflict_key] = invalidated
            if invalidated is not None and previous is not None:
                try:
                    self._persistence.save(invalidated)
                    self._drop_if_concurrently_removed(conflict_key)
                except Exception:
                    with self._serialized():
                        if self._entries.get(conflict_key) is invalidated:
                            self._entries[conflict_key] = previous
                    logger.warning(
                        "conflict_invalidate_persist_failed",
                        conflict_key=conflict_key,
                        exc_info=True,
                    )
                    # Do not advertise a shared conflict timestamp when any
                    # invalidation failed to persist — abort the conflict plan.
                    return None

        return plan.now

    def _build_and_assign_entry(
        self,
        *,
        key: str,
        value: str,
        tier: str,
        source: str,
        source_agent: str,
        scope: str,
        tags: list[str] | None,
        branch: str | None,
        confidence: float,
        agent_scope: str,
        source_session_id: str,
        source_channel: str,
        source_message_id: str,
        triggered_by: str,
        memory_group: str | None | object,
        mg_explicit: str | None | object,
        temporal_sensitivity: Literal["high", "medium", "low"] | None,
        failed_approaches: list[str] | None,
        conflict_valid_at: str | None,
        status: str | None = None,
        stale_reason: str | None = None,
        stale_date: str | None = None,
        superseded_by: str | None = None,
    ) -> tuple[MemoryEntry, MemoryEntry | None]:
        """Build the new :class:`MemoryEntry` and atomically assign it.

        Runs under the store lock with the ``store.save.phase.lock_build_ms``
        timer.  Returns ``(new_entry, existing_entry_or_None)`` so the caller
        can still distinguish inserts from updates for audit + propagation.
        """
        # Hydrate durable-only rows before treating this as an insert.
        self._ensure_entry_cached(key)
        with (
            MetricsTimer(self._metrics, "store.save.phase.lock_build_ms"),
            self._serialized(),
        ):
            existing = self._entries.get(key)

            if memory_group is MEMORY_GROUP_UNSET:
                mg_for_entry: str | None = existing.memory_group if existing is not None else None
            else:
                mg_for_entry = cast("str | None", mg_explicit)

            tier_val = self._resolve_tier_value(tier)
            now = _utc_now_iso()
            entry = self._construct_memory_entry(
                key=key,
                value=value,
                tier_val=tier_val,
                source=source,
                source_agent=source_agent,
                scope=scope,
                tags=tags,
                branch=branch,
                confidence=confidence,
                agent_scope=agent_scope,
                source_session_id=source_session_id,
                source_channel=source_channel,
                source_message_id=source_message_id,
                triggered_by=triggered_by,
                mg_for_entry=mg_for_entry,
                temporal_sensitivity=temporal_sensitivity,
                failed_approaches=failed_approaches,
                conflict_valid_at=conflict_valid_at,
                status=status,
                stale_reason=stale_reason,
                stale_date=stale_date,
                superseded_by=superseded_by,
                existing=existing,
                now=now,
            )
            entry = self._stamp_integrity_hash(entry)
            self._enforce_entry_caps_before_assign(
                key=key,
                new_group=entry.memory_group,
                existing=existing,
            )
            self._entries[key] = entry
        return entry, existing

    def _resolve_tier_value(self, tier: str) -> MemoryTier | str:
        """Resolve ``tier`` to a :class:`MemoryTier` or profile layer name (EPIC-010)."""
        try:
            return MemoryTier(tier)
        except ValueError:
            if self._profile is not None and tier in self._profile.layer_names:
                return tier
            raise

    def _construct_memory_entry(
        self,
        *,
        key: str,
        value: str,
        tier_val: MemoryTier | str,
        source: str,
        source_agent: str,
        scope: str,
        tags: list[str] | None,
        branch: str | None,
        confidence: float,
        agent_scope: str,
        source_session_id: str,
        source_channel: str,
        source_message_id: str,
        triggered_by: str,
        mg_for_entry: str | None,
        temporal_sensitivity: Literal["high", "medium", "low"] | None,
        failed_approaches: list[str] | None,
        conflict_valid_at: str | None,
        status: str | None,
        stale_reason: str | None,
        stale_date: str | None,
        superseded_by: str | None,
        existing: MemoryEntry | None,
        now: str,
    ) -> MemoryEntry:
        """Allocate a new :class:`MemoryEntry`, preserving reserved fields on update."""
        preserved = _preserved_fields_for_update(existing, now)
        # Preserve learned confidence on routine updates: -1.0 means "caller
        # did not specify".  Falling through to the static source default
        # would discard what record_access / reinforce / the feedback
        # flywheel accumulated while keeping the counters they derived it
        # from (mutually inconsistent state).
        if confidence == -1.0 and existing is not None:
            confidence = existing.confidence
        # Profile-tuned source defaults (profile.source_confidence) take
        # precedence over the static map in models.py — without this the
        # profile block is dead configuration.
        if confidence == -1.0 and self._profile is not None:
            prof_default = (getattr(self._profile, "source_confidence", None) or {}).get(source)
            if prof_default is not None:
                confidence = float(prof_default)
        effective_valid_at = conflict_valid_at or preserved["valid_at"]
        effective_temporal = (
            temporal_sensitivity
            if temporal_sensitivity is not None
            else preserved["temporal_sensitivity"]
        )
        effective_failed = (
            failed_approaches if failed_approaches is not None else preserved["failed_approaches"]
        )
        return MemoryEntry(
            key=key,
            value=value,
            tier=tier_val,
            confidence=confidence,
            source=MemorySource(source),
            source_agent=source_agent,
            scope=MemoryScope(scope),
            agent_scope=agent_scope,
            tags=tags or [],
            created_at=preserved["created_at"],
            updated_at=now,
            last_accessed=now,
            access_count=preserved["access_count"],
            useful_access_count=preserved["useful_access_count"],
            total_access_count=preserved["total_access_count"],
            positive_feedback_count=preserved["positive_feedback_count"],
            negative_feedback_count=preserved["negative_feedback_count"],
            stability=preserved["stability"],
            difficulty=preserved["difficulty"],
            embedding=preserved["embedding"],
            embedding_model_id=preserved["embedding_model_id"],
            branch=branch,
            last_reinforced=preserved["last_reinforced"],
            reinforce_count=preserved["reinforce_count"],
            contradicted=preserved["contradicted"],
            contradiction_reason=preserved["contradiction_reason"],
            seeded_from=preserved["seeded_from"],
            valid_at=effective_valid_at,
            invalid_at=preserved["invalid_at"],
            superseded_by=superseded_by
            if superseded_by is not None
            else preserved["superseded_by"],
            source_session_id=source_session_id,
            source_channel=source_channel,
            source_message_id=source_message_id,
            triggered_by=triggered_by,
            memory_group=mg_for_entry,
            temporal_sensitivity=effective_temporal,
            failed_approaches=effective_failed,
            status=_resolve_status(status, existing),
            stale_reason=stale_reason
            if stale_reason is not None
            else (existing.stale_reason if existing else None),
            stale_date=stale_date
            if stale_date is not None
            else (existing.stale_date if existing else None),
        )

    @staticmethod
    def _stamp_integrity_hash(entry: MemoryEntry) -> MemoryEntry:
        """Compute + attach the H4a integrity hash on ``entry``."""
        from tapps_brain.integrity import compute_integrity_hash as _compute_hash

        tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
        h = _compute_hash(entry.key, entry.value, tier_str, source_str)
        return entry.model_copy(update={"integrity_hash": h, "integrity_hash_v": 2})

    def _embed_entry(self, key: str, value: str, entry: MemoryEntry) -> MemoryEntry:
        """Compute + attach the embedding when a provider is configured (Epic 65.7).

        When no provider is configured or embed fails, keep any preserved
        embedding from the prior row so a re-save does not NULL the vector.
        """
        if self._embedding_provider is None:
            return entry
        with MetricsTimer(self._metrics, "store.save.phase.embed_ms"):
            try:
                emb = self._embedding_provider.embed(value)
                mid_raw = getattr(self._embedding_provider, "model_id", None)
                mid: str | None = (
                    mid_raw.strip() if isinstance(mid_raw, str) and mid_raw.strip() else None
                )
                embed_update: dict[str, object] = {
                    "embedding": emb,
                    "embedding_model_id": mid,
                }
                entry = entry.model_copy(update=embed_update)
                with self._serialized():
                    # Re-read to avoid overwriting concurrent update_fields.
                    # Skip the cache write when the key was deleted in the
                    # meantime — re-inserting would resurrect a deleted entry.
                    current = self._entries.get(key)
                    if current is not None:
                        entry = current.model_copy(update=embed_update)
                        self._entries[key] = entry
            except Exception:
                logger.warning("embedding_compute_failed", key=key, exc_info=True)
        return entry

    def backfill_embeddings(self) -> dict[str, int]:
        """Compute + persist embeddings for cached entries that have none.

        Operator remediation for rows written before an embedding provider was
        active (``vector_index_rows == 0`` on the dashboard — "pgvector HNSW
        ready but no embedded rows").  For each cached entry lacking an
        ``embedding``, compute it with the configured provider and write it
        through to Postgres (and Hive when configured).  Only ``embedding`` and
        ``embedding_model_id`` change — timestamps and integrity hashes are
        preserved, so a backfill never invalidates integrity verification.

        Returns:
            Dict with ``backfilled``, ``skipped_existing`` and ``failed``
            counts.  All zero when no embedding provider is configured.
        """
        if self._embedding_provider is None:
            logger.warning(
                "backfill_embeddings.no_provider",
                hint="no embedding provider configured; nothing to backfill",
            )
            return {"backfilled": 0, "skipped_existing": 0, "failed": 0}

        mid_raw = getattr(self._embedding_provider, "model_id", None)
        model_id: str | None = (
            mid_raw.strip() if isinstance(mid_raw, str) and mid_raw.strip() else None
        )

        # Include durable rows beyond the cold-start cache cap.
        self._merge_durable_entries()
        with self._serialized():
            keys = list(self._entries.keys())

        backfilled = 0
        skipped_existing = 0
        failed = 0

        # Ask the durable store which rows actually lack an embedding when it
        # can tell us: load_all()/load_one() never hydrate the embedding
        # column, so the in-memory field is None for every hydrated row — the
        # cache-only check would re-embed and rewrite the entire store after
        # any restart (and report skipped_existing=0).
        missing: set[str] | None = None
        keys_missing = getattr(self._persistence, "keys_missing_embedding", None)
        if callable(keys_missing):
            try:
                missing = set(keys_missing())
            except Exception:
                logger.warning("backfill_embeddings.missing_query_failed", exc_info=True)

        for key in keys:
            with self._serialized():
                entry = self._entries.get(key)
            if entry is None:
                continue
            if missing is not None:
                if key not in missing:
                    skipped_existing += 1
                    continue
            elif getattr(entry, "embedding", None):
                skipped_existing += 1
                continue

            try:
                emb = self._embedding_provider.embed(entry.value)
            except Exception:
                failed += 1
                logger.warning("backfill_embeddings.embed_failed", key=key, exc_info=True)
                continue

            update: dict[str, object] = {"embedding": emb, "embedding_model_id": model_id}
            with self._serialized():
                current = self._entries.get(key)
                if current is None:
                    continue
                previous = current
                updated = current.model_copy(update=update)
                self._entries[key] = updated

            try:
                self._persistence.save(updated)
            except Exception:
                failed += 1
                with self._serialized():
                    if self._entries.get(key) is updated:
                        self._entries[key] = previous
                logger.warning("backfill_embeddings.persist_failed", key=key, exc_info=True)
                continue
            self._drop_if_concurrently_removed(key)

            backfilled += 1
            # Sync embedding to Hive only after private persist succeeds (scoped rules).
            if self._hive_store is not None:
                self._propagate_to_hive(updated)

        logger.info(
            "backfill_embeddings.complete",
            backfilled=backfilled,
            skipped_existing=skipped_existing,
            failed=failed,
        )
        return {"backfilled": backfilled, "skipped_existing": skipped_existing, "failed": failed}

    def load_embeddings(self) -> dict[str, dict[str, Any]]:
        """Load durable embedding vectors for lossless export (TAP-5030).

        Returns ``{key: {"vector": [...], "embedding_model_id": str|None}}``.
        Empty when the backend does not support ``load_embeddings``.
        """
        loader = getattr(self._persistence, "load_embeddings", None)
        if not callable(loader):
            return {}
        try:
            loaded = loader()
        except Exception:
            logger.warning("store.load_embeddings_failed", exc_info=True)
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def set_embeddings(
        self,
        vectors: dict[str, list[float]],
        *,
        model_id: str,
    ) -> dict[str, int]:
        """Write precomputed embeddings without re-running the embedder (TAP-5030).

        Only updates ``embedding`` / ``embedding_model_id`` on existing keys.
        Returns counts: ``restored``, ``missing_keys``, ``failed``.
        """
        restored = 0
        missing_keys = 0
        failed = 0
        for key, vector in vectors.items():
            with self._serialized():
                current = self._entries.get(key)
            if current is None:
                # Try durable peek
                peek = self._ensure_entry_cached(key)
                if peek is None:
                    missing_keys += 1
                    continue
                current = peek
            update: dict[str, object] = {
                "embedding": list(vector),
                "embedding_model_id": model_id,
            }
            with self._serialized():
                latest = self._entries.get(key) or current
                previous = latest
                updated = latest.model_copy(update=update)
                self._entries[key] = updated
            try:
                self._persistence.save(updated)
            except Exception:
                failed += 1
                with self._serialized():
                    if self._entries.get(key) is updated:
                        self._entries[key] = previous
                logger.warning("store.set_embeddings_failed", key=key, exc_info=True)
                continue
            restored += 1
        return {"restored": restored, "missing_keys": missing_keys, "failed": failed}

    def _persist_entry_or_rollback(
        self,
        key: str,
        entry: MemoryEntry,
        *,
        existing: MemoryEntry | None,
        dedup: bool,
    ) -> None:
        """Persist the entry and roll the cache back on failure.

        Maintains write-through consistency: if the Postgres write raises,
        the in-memory ``_entries`` is restored and (when dedup was used) the
        bloom filter is rebuilt from the current cache — the filter has no
        item-remove operation (TAP-644).
        """
        try:
            with MetricsTimer(self._metrics, "store.save.phase.persist_ms"):
                self._persistence.save(entry)
        except Exception:
            with self._serialized():
                if existing is not None:
                    self._entries[key] = existing
                else:
                    self._entries.pop(key, None)
                if dedup:
                    self._bloom = BloomFilter()
                    for _e in self._entries.values():
                        self._bloom.add(normalize_for_dedup(_e.value))
            raise
        self._drop_if_concurrently_removed(key)

    def _drop_if_concurrently_removed(self, key: str) -> None:
        """Close the evict/persist resurrection race after a durable save.

        Write-through persists run outside the store lock, so a concurrent
        eviction or delete may have removed this key (cache + durable row)
        between cache assignment and persist — the save would then silently
        resurrect the row in Postgres while the cache stays capped, and
        ``_merge_durable_entries`` would push the store over ``max_entries``.
        Re-check under the lock and drop the orphaned row.  Applies to every
        persist-outside-lock site (save, update_fields, reinforce,
        record_access, get, supersede, backfill_embeddings, conflict marking).
        """
        with self._serialized():
            if key not in self._entries:
                try:
                    self._persistence.delete(key)
                except Exception:
                    logger.warning("save_resurrect_cleanup_failed", key=key, exc_info=True)

    def _emit_save_audit(
        self,
        key: str,
        entry: MemoryEntry,
        *,
        existing: MemoryEntry | None,
    ) -> None:
        """Append the save to the audit log (best-effort)."""
        self._persistence.append_audit(
            action="save",
            key=key,
            extra={
                "tier": str(entry.tier),
                "value_len": len(entry.value),
                "is_update": existing is not None,
            },
        )

    def _refresh_entity_index(
        self,
        key: str,
        entry: MemoryEntry,
        *,
        existing_present: bool,
    ) -> None:
        """Refresh the entity index for graph centrality (TAP-734).

        Takes the store lock: this runs in the post-persist fan-out (outside
        any lock), and unlocked index mutation races other saves/deletes —
        dict insert during ``_remove_entry_entities``'s iteration raises
        ``RuntimeError: dictionary changed size during iteration``.
        """
        with self._serialized():
            if existing_present:
                self._remove_entry_entities(key)
            self._index_entry_entities(key, entry.value)

    def _persist_relations(self, key: str, value: str, *, created_at: str | None = None) -> None:
        """Extract + persist relations and warn on simple cycles (EPIC-006)."""
        with MetricsTimer(self._metrics, "store.save.phase.relations_ms"):
            relations = extract_relations(key, value, created_at=created_at)
            if not relations:
                return

            from tapps_brain.relations import RelationEntry, detect_relation_cycles

            # Include cached relations touching the new triples' entities so
            # cross-entry direct cycles (k1: "A manages B", later k2:
            # "B manages A") warn too — detection over only the fresh
            # extractions could never see the first edge.
            new_entities = {r.subject.lower() for r in relations} | {
                r.object_entity.lower() for r in relations
            }

            def _as_relation(cached: object) -> RelationEntry | None:
                # The cache holds RelationEntry objects on the extract path
                # but raw dict rows when hydrated from the durable backend.
                if isinstance(cached, RelationEntry):
                    return cached
                if isinstance(cached, dict):
                    try:
                        return RelationEntry.model_validate(cached)
                    except Exception:  # malformed cached row; skip
                        return None
                return None

            with self._serialized():
                cached_flat = [
                    cached
                    for cached_key, cached_rels in self._relations.items()
                    if cached_key != key
                    for cached in cached_rels
                ]
            neighborhood = [
                rel
                for rel in (_as_relation(c) for c in cached_flat)
                if rel is not None
                and (
                    rel.subject.lower() in new_entities or rel.object_entity.lower() in new_entities
                )
            ]

            # Neighborhood first: a direct cycle then reports the *new* edge.
            # Filter to new triples so pre-existing self-loops/cycles in the
            # neighborhood don't re-warn on every unrelated save.
            new_triples = {
                (r.subject.lower(), r.predicate.lower(), r.object_entity.lower()) for r in relations
            }
            cycles = [
                c
                for c in detect_relation_cycles(neighborhood + relations)
                if (c[0].lower(), c[1].lower(), c[2].lower()) in new_triples
            ]
            if cycles:
                logger.warning(
                    "relations.cycles_detected",
                    entry_key=key,
                    cycle_count=len(cycles),
                    cycles=[{"subject": s, "predicate": p, "object": o} for s, p, o in cycles],
                )

            existing_count = len(self._relations.get(key, []))
            budget = RelationEntry.MAX_EDGES_PER_KEY - existing_count
            if budget <= 0:
                logger.debug(
                    "relations.max_edges_reached",
                    entry_key=key,
                    limit=RelationEntry.MAX_EDGES_PER_KEY,
                )
                return

            relations_to_save = relations[:budget]
            self._persistence.save_relations(key, relations_to_save)
            with self._serialized():
                self._relations[key] = self._persistence.load_relations(key)

    def _emit_correction_feedback(self, session_id: str, entry_value: str) -> None:
        """Emit ``implicit_correction`` events when a save corrects recent recalls.

        EPIC-029 story 029-4b.  >40% token overlap between the save value and
        a recent recalled entry's value within the feedback window triggers
        the event with ``utility_score=-0.3``.
        """
        now = time.monotonic()
        with self._serialized():
            targets = self._detect_correction(session_id, entry_value, now)
        for ck, overlap in targets:
            self._emit_implicit_feedback(
                "implicit_correction",
                ck,
                session_id,
                -0.3,
                details={"type": "correction", "token_overlap": round(overlap, 4)},
            )

    def _maybe_consolidate(self, entry: MemoryEntry) -> None:
        """Check if the saved entry should trigger consolidation.

        Runs consolidation in a non-reentrant manner to prevent infinite
        loops when consolidation saves new entries.
        """
        # Compare-and-set under the store lock: an unlocked check-then-set
        # let two concurrent saves both pass the guard and run overlapping
        # merges, where the second merge's superseded_by marks clobbered the
        # first's linkage (breaking its undo).
        with self._serialized():
            if self._consolidation_in_progress:
                return
            self._consolidation_in_progress = True
        try:
            from tapps_brain.auto_consolidation import check_consolidation_on_save

            result = check_consolidation_on_save(
                entry,
                self,
                threshold=self._consolidation_config.threshold,
                min_entries=self._consolidation_config.min_entries,
            )

            if result.triggered:
                self._metrics.increment("store.consolidate")
                self._metrics.increment("store.consolidate.merged", len(result.source_keys))
                logger.info(
                    "auto_consolidation_on_save",
                    entry_key=entry.key,
                    consolidated_key=result.consolidated_entry.key
                    if result.consolidated_entry
                    else None,
                    source_keys=result.source_keys,
                )
        except Exception:
            # Best-effort like the other post-persist steps (Hive propagation,
            # group/expert publish): the entry is already durably saved, so a
            # consolidation failure must not fail the save() that triggered it.
            logger.warning("auto_consolidation_check_failed", exc_info=True)
        finally:
            self._consolidation_in_progress = False

    def _index_entry_entities(self, key: str, value: str) -> None:
        """Add *key* to the entity index for all BM25 tokens in *value* (TAP-734).

        Tokens shorter than 3 characters are excluded (post-stemming length).
        Must be called while holding the store lock.  Token sets are replaced
        (copy-on-write), never mutated in place, so unlocked readers (recall's
        graph-centrality scoring) can safely iterate a set snapshot they
        obtained via ``dict.get`` without racing a concurrent mutation.
        """
        tokens = [t for t in _bm25_preprocess(value) if len(t) >= 3]
        for token in tokens:
            existing = self._entity_index.get(token)
            self._entity_index[token] = {key} if existing is None else existing | {key}

    def _remove_entry_entities(self, key: str) -> None:
        """Remove *key* from all entity index token sets (TAP-734).

        Empty token sets are pruned to keep memory bounded.  Must be called
        while holding the store lock; sets are replaced, not mutated in place
        (see :meth:`_index_entry_entities`).
        """
        for token, keys in list(self._entity_index.items()):
            if key not in keys:
                continue
            remaining = keys - {key}
            if remaining:
                self._entity_index[token] = remaining
            else:
                self._entity_index.pop(token, None)

    def _propagate_to_hive(self, entry: MemoryEntry) -> None:
        """Propagate a saved entry to the Hive if appropriate (EPIC-011)."""
        if self._hive_store is None:
            return
        try:
            from tapps_brain.backends import PropagationEngine

            # Read Hive config from profile if available
            auto_propagate: list[str] | None = None
            private: list[str] | None = None
            agent_profile = "repo-brain"
            if self._profile is not None:
                hive_cfg = getattr(self._profile, "hive", None)
                if hive_cfg is not None:
                    auto_propagate = hive_cfg.auto_propagate_tiers
                    private = hive_cfg.private_tiers
                agent_profile = getattr(self._profile, "name", "repo-brain")

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)

            with start_span(
                SPAN_HIVE_PROPAGATE,
                {
                    "hive.agent_scope": entry.agent_scope,
                    "hive.tier": tier_str,
                    "hive.agent_id": self._hive_agent_id,
                },
            ):
                PropagationEngine.propagate(
                    key=entry.key,
                    value=entry.value,
                    agent_scope=entry.agent_scope,
                    agent_id=self._hive_agent_id,
                    agent_profile=agent_profile,
                    tier=tier_str,
                    confidence=entry.confidence,
                    source=entry.source.value
                    if hasattr(entry.source, "value")
                    else str(entry.source),
                    tags=entry.tags,
                    hive_store=self._hive_store,
                    auto_propagate_tiers=auto_propagate,
                    private_tiers=private,
                    memory_group=entry.memory_group,
                    embedding=entry.embedding,
                )
        except Exception:
            logger.warning("hive_propagation_failed", key=entry.key, exc_info=True)
            # Private row is already durable by design; surface the split via metrics
            # so operators can alert without rolling back local memory.
            self._metrics.increment("store.hive_propagation_failed")
            if entry.agent_scope and entry.agent_scope != "private":
                self._metrics.increment("store.hive_propagation_failed_visible")

    def update_fields(self, key: str, **fields: Any) -> MemoryEntry | None:  # noqa: ANN401
        """Partial update of specific fields on an existing entry.

        Preserves immutable fields like ``created_at``. Used by Epic 24
        decay/contradiction/reinforcement systems.

        ``updated_at`` defaults to now but honors an explicit caller value —
        maintenance passes (e.g. flywheel confidence updates) preserve the
        entry's own timestamp so a metadata write does not inflate the
        recency ranking signal of negatively rated entries.
        """
        with self._serialized():
            entry = self._entries.get(key)
            if entry is None:
                load_one = getattr(self._persistence, "load_one", None)
                if callable(load_one):
                    entry = load_one(key)
                    if entry is not None:
                        self._entries[key] = entry
            if entry is None:
                return None

            fields.setdefault("updated_at", _utc_now_iso())
            updated = entry.model_copy(update=fields)
            self._entries[key] = updated

        # Persist — rollback in-memory cache on failure.  Identity-guarded so
        # a concurrent writer that replaced the slot in the failure window is
        # not clobbered with our stale pre-image.
        try:
            self._persistence.save(updated)
        except Exception:
            with self._serialized():
                if self._entries.get(key) is updated:
                    self._entries[key] = entry
            raise
        self._drop_if_concurrently_removed(key)
        return updated

    def undo_consolidation_merge(self, consolidated_key: str) -> ConsolidationUndoResult:
        """Revert one auto-consolidation merge (EPIC-044 STORY-044.4).

        See :func:`tapps_brain.auto_consolidation.undo_consolidation_merge`.
        """
        from tapps_brain.auto_consolidation import undo_consolidation_merge as _undo_merge

        return _undo_merge(self, consolidated_key)

    def count(self) -> int:
        """Return the total number of memory entries."""
        self._merge_durable_entries()
        with self._serialized():
            return len(self._entries)

    def snapshot(self) -> MemorySnapshot:
        """Return a serializable snapshot of the full memory state."""
        self._merge_durable_entries()
        with self._serialized():
            entries = list(self._entries.values())

        tier_counts: dict[str, int] = {}
        for entry in entries:
            tier_val = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1

        return MemorySnapshot(
            project_root=str(self._project_root),
            entries=entries,
            total_count=len(entries),
            tier_counts=tier_counts,
        )

    def get_schema_version(self) -> int:
        """Return the current private-memory schema version."""
        return self._persistence.get_schema_version()

    def knn_search(
        self,
        query_embedding: list[float],
        k: int,
        *,
        include_expired: bool = False,
        as_of: str | None = None,
    ) -> list[tuple[str, float]]:
        """Approximate-nearest-neighbour search via pgvector HNSW.

        TAP-4586: *include_expired* (default ``False``) pushes the live-row
        predicate into recall SQL so expired/superseded rows do not consume a
        top-K slot.  Pass ``True`` only when historical rows are wanted.

        *as_of* applies the FTS-equivalent bi-temporal window and stands the
        live-row predicate down for point-in-time hybrid recall.
        """
        return self._persistence.knn_search(
            query_embedding, k, include_expired=include_expired, as_of=as_of
        )

    @property
    def vector_index_enabled(self) -> bool:
        """True when pgvector KNN is usable for this store.

        False when expected indexes are missing or the last knn_search marked
        the backend as degraded.
        """
        persistence = self._persistence
        if getattr(persistence, "knn_search_degraded", False):
            return False
        if getattr(persistence, "index_verify_unknown", False):
            return False
        verify = getattr(persistence, "verify_expected_indexes", None)
        if callable(verify):
            try:
                missing = verify()
            except Exception:
                return False
            if missing:
                return False
        return True

    @property
    def vector_row_count(self) -> int:
        """Number of private_memories rows with a non-NULL embedding vector."""
        return self._persistence.vector_row_count()

    # ------------------------------------------------------------------
    # Reinforcement (Story 002.2)
    # ------------------------------------------------------------------

    def reinforce(
        self, key: str, *, confidence_boost: float = 0.0, session_id: str | None = None
    ) -> MemoryEntry:
        """Reinforce a memory entry, resetting its decay clock atomically.

        Args:
            key: The memory entry key to reinforce.
            confidence_boost: Optional confidence increase (0.0-0.2).
            session_id: Optional session identifier for implicit feedback tracking
                (STORY-029.3).  When provided and the entry was recalled in the same
                session within the feedback window, an ``implicit_positive`` event
                (utility_score=1.0) is emitted.

        Returns:
            The updated ``MemoryEntry``.

        Raises:
            KeyError: If the entry does not exist.
        """
        from tapps_brain.reinforcement import reinforce as _reinforce

        self._metrics.increment("store.reinforce")
        decay_cfg = self._get_decay_config()

        with start_span(SPAN_REINFORCE, {"gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL}):
            entry = self._ensure_entry_cached(key)
            if entry is None:
                raise KeyError(key)

            with self._serialized():
                # Re-read under lock in case another writer raced after hydrate.
                entry = self._entries.get(key) or entry
                updates = dict(_reinforce(entry, decay_cfg, confidence_boost=confidence_boost))
                updates.update(self._reinforce_stability_updates(entry, decay_cfg))
                updated = entry.model_copy(update=updates)
                self._entries[key] = updated

            # Persist reinforcement — rollback in-memory cache on failure to
            # maintain write-through consistency (matches get() / update_fields()).
            # Identity-guarded so a concurrent writer is not clobbered.
            try:
                self._persistence.save(updated)
            except Exception:
                with self._serialized():
                    if self._entries.get(key) is updated:
                        self._entries[key] = entry
                raise
            self._drop_if_concurrently_removed(key)

            final = self._maybe_promote_after_reinforce(key, updated, decay_cfg)

            # EPIC-029 story 029.3: implicit positive feedback
            if session_id is not None:
                _should_emit = False
                with self._serialized():
                    _should_emit = self._check_and_mark_reinforced(session_id, key)
                if _should_emit:
                    self._emit_implicit_feedback("implicit_positive", key, session_id, 1.0)

            return final

    def _reinforce_stability_updates(
        self,
        entry: MemoryEntry,
        decay_cfg: Any,  # noqa: ANN401 — DecayConfig
    ) -> dict[str, Any]:
        """FSRS-lite stability/difficulty updates for an explicit reinforce (EPIC-042.8).

        Mirrors ``record_access``'s ``was_useful=True`` path using pre-reinforce
        timestamps for retrievability.  Returns ``{}`` when the active profile's
        layer does not enable adaptive stability, or on any failure (best-effort).
        """
        if self._profile is None:
            return {}
        tier_name = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        layer = self._profile.get_layer(tier_name)
        if layer is None or not layer.adaptive_stability:
            return {}
        try:
            from tapps_brain.decay import update_stability

            new_stab, new_diff = update_stability(entry, decay_cfg, True)
        except Exception:
            logger.warning("reinforce_stability_update_failed", key=entry.key, exc_info=True)
            return {}
        return {"stability": new_stab, "difficulty": new_diff}

    def _maybe_promote_after_reinforce(
        self,
        key: str,
        updated: MemoryEntry,
        decay_cfg: Any,  # noqa: ANN401 — DecayConfig
    ) -> MemoryEntry:
        """Promote *updated* to a higher tier if the profile's rules are met (EPIC-010).

        Persists + audits the promotion and returns the promoted entry; returns
        *updated* unchanged when no profile is set, no promotion is warranted, or
        the check fails (best-effort — promotion never breaks a reinforce).
        """
        if self._profile is None:
            return updated
        try:
            from tapps_brain.promotion import PromotionEngine

            engine = PromotionEngine(decay_cfg)
            target_tier = engine.check_promotion(updated, self._profile)
            if target_tier is None:
                return updated
            old_tier = str(updated.tier)
            promoted = updated.model_copy(
                update={"tier": target_tier, "updated_at": _utc_now_iso()}
            )
            with self._serialized():
                self._entries[key] = promoted
            try:
                self._persistence.save(promoted)
            except Exception:
                with self._serialized():
                    if self._entries.get(key) is promoted:
                        self._entries[key] = updated
                raise
            self._drop_if_concurrently_removed(key)
            self._persistence.append_audit(
                action="promote",
                key=key,
                extra={
                    "from_tier": old_tier,
                    "to_tier": target_tier,
                    "access_count": updated.access_count,
                    "reinforce_count": updated.reinforce_count,
                },
            )
            logger.info("memory_promoted", key=key, from_tier=old_tier, to_tier=target_tier)
        except Exception:
            logger.warning("promotion_check_failed", key=key, exc_info=True)
            return updated
        return promoted

    def record_access(self, key: str, was_useful: bool) -> None:
        """Record whether a retrieved memory was useful. Updates Bayesian confidence.

        Increments total_access_count always; increments useful_access_count when
        was_useful=True. Blends confidence toward the Laplace usefulness estimate:

            target = (useful + 1) / (total + 2)
            new_confidence = old_confidence + 0.2 * (target - old_confidence)

        Useful accesses on a mostly-useful memory pull confidence up; non-useful
        accesses pull it down. (The previous formula *multiplied* old confidence
        by the Laplace estimate, which monotonically decreased confidence even
        for a perfect usefulness record — the opposite of positive feedback.)

        If adaptive_stability is enabled on the entry's tier, also calls
        update_stability() from decay.py.

        Args:
            key: The memory entry key.
            was_useful: Whether this retrieval was useful to the caller.
        """
        if self._ensure_entry_cached(key) is None:
            return

        with self._serialized():
            entry = self._entries.get(key)
            if entry is None:
                return

            new_total = entry.total_access_count + 1
            new_useful = entry.useful_access_count + (1 if was_useful else 0)

            # Blend toward the Laplace usefulness estimate (see docstring).
            laplace_target = (new_useful + 1) / (new_total + 2)
            new_confidence = entry.confidence + 0.2 * (laplace_target - entry.confidence)
            # Cap at the source ceiling (same contract as reinforce) so agent
            # memories cannot drift above agent_confidence_ceiling via useful
            # access alone. Never *reduce* an already-over-ceiling value.
            from tapps_brain.decay import _get_ceiling

            ceiling = _get_ceiling(entry.source, self._get_decay_config())
            new_confidence = min(new_confidence, max(ceiling, entry.confidence))
            new_confidence = max(0.0, min(1.0, new_confidence))

            updates: dict[str, object] = {
                "total_access_count": new_total,
                "useful_access_count": new_useful,
                "confidence": new_confidence,
            }

            # Adaptive stability (040.5): update if enabled on this tier
            if self._profile is not None:
                tier_name = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                layer = self._profile.get_layer(tier_name)
                if layer is not None and layer.adaptive_stability:
                    try:
                        from tapps_brain.decay import update_stability

                        decay_cfg = self._get_decay_config()
                        new_stab, new_diff = update_stability(entry, decay_cfg, was_useful)
                        updates["stability"] = new_stab
                        updates["difficulty"] = new_diff
                    except Exception:
                        logger.warning(
                            "record_access_stability_update_failed", key=key, exc_info=True
                        )

            updated = entry.model_copy(update=updates)
            self._entries[key] = updated

        # Persist access metadata — rollback in-memory cache on failure to
        # maintain write-through consistency (matches get() / update_fields()).
        # Identity-guarded so a concurrent writer is not clobbered.
        try:
            self._persistence.save(updated)
        except Exception:
            with self._serialized():
                if self._entries.get(key) is updated:
                    self._entries[key] = entry
            raise
        self._drop_if_concurrently_removed(key)
        logger.debug(
            "memory_access_recorded",
            key=key,
            was_useful=was_useful,
            new_confidence=new_confidence,
            total_access_count=new_total,
            useful_access_count=new_useful,
        )

    # ------------------------------------------------------------------
    # Extraction ingestion (Story 002.3)
    # ------------------------------------------------------------------

    def ingest_context(
        self,
        context: str,
        *,
        source: str = "agent",
        capture_prompt: str = "",
        agent_scope: str = "private",
    ) -> list[str]:
        """Extract durable facts from context and save new entries.

        Uses rule-based pattern matching to find decision-like statements
        and saves them as memory entries. Existing keys are skipped.

        Args:
            context: Raw session/transcript text to scan.
            source: Source attribution for created entries.
            capture_prompt: Optional guidance for extraction.
            agent_scope: Hive propagation scope for captured facts —
                ``'private'`` (default), ``'domain'``, ``'hive'``, or ``'group:<name>'``.

        Returns:
            List of keys for newly created entries.
        """
        from tapps_brain.extraction import extract_durable_facts

        _profile_name = getattr(self._profile, "name", None) if self._profile else None
        facts = extract_durable_facts(context, capture_prompt, profile=_profile_name)
        created_keys: list[str] = []

        for fact in facts:
            key = fact["key"]
            # Skip if already exists — hydrate from the durable store first so
            # rows written out-of-band (experience path, other processes) or
            # beyond the cold-start cap are not silently overwritten.
            if self._ensure_entry_cached(key) is not None:
                continue

            result = self.save(
                key=key,
                value=fact["value"],
                tier=fact["tier"],
                source=source,
                agent_scope=agent_scope,
            )
            # Only report keys that were actually created: a dedup hit returns
            # the reinforced *existing* entry under a different key, and error
            # dicts are not creations.
            if isinstance(result, MemoryEntry) and result.key == key:
                created_keys.append(key)

        return created_keys

    # ------------------------------------------------------------------
    # Session indexing (Story 002.4)
    # ------------------------------------------------------------------

    def index_session(self, session_id: str, chunks: list[str]) -> int:
        """Index session chunks for later search.

        Args:
            session_id: Session identifier.
            chunks: List of text chunks to index.

        Returns:
            Number of chunks stored.

        Raises:
            Exception: Propagates backend failures (no silent ``0`` swallow).
        """
        pg_index = self._postgres_session_index()
        if pg_index is not None:
            try:
                return int(pg_index.save_chunks(session_id, chunks))
            except Exception:
                logger.warning("session_index_failed", session_id=session_id, exc_info=True)
                raise

        from tapps_brain.session_index import index_session as _index_session

        try:
            return _index_session(
                self._project_root,
                session_id,
                chunks,
                agent_id=self._agent_id or "",
            )
        except Exception:
            logger.warning("session_index_failed", session_id=session_id, exc_info=True)
            raise

    def search_sessions(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search session index by query.

        Returns list of dicts with keys: session_id, chunk_index, content, created_at.

        Raises:
            Exception: Propagates backend failures (no silent empty swallow).
        """
        pg_index = self._postgres_session_index()
        if pg_index is not None:
            try:
                return list(pg_index.search(query, limit=limit))
            except Exception:
                logger.warning("session_search_failed", query=query, exc_info=True)
                raise

        from tapps_brain.session_index import search_session_index

        try:
            return search_session_index(
                self._project_root,
                query,
                limit=limit,
                agent_id=self._agent_id or "",
            )
        except Exception:
            logger.warning("session_search_failed", query=query, exc_info=True)
            raise

    def cleanup_sessions(self, *, ttl_days: int = 90) -> int:
        """Delete session chunks older than ttl_days.

        Returns:
            Count of deleted chunks.

        Raises:
            Exception: Propagates backend failures (no silent ``0`` swallow).
        """
        pg_index = self._postgres_session_index()
        if pg_index is not None:
            try:
                return int(pg_index.delete_expired(ttl_days))
            except Exception:
                logger.warning("session_cleanup_failed", exc_info=True)
                raise

        try:
            from tapps_brain.session_index import delete_expired_sessions

            return delete_expired_sessions(
                self._project_root,
                ttl_days,
                agent_id=self._agent_id or "",
            )
        except Exception:
            logger.warning("session_cleanup_failed", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Doc validation (Story 002.1)
    # ------------------------------------------------------------------

    def validate_entries(
        self,
        *,
        keys: list[str] | None = None,
        strict: bool = False,
    ) -> Any:  # noqa: ANN401
        """Validate memory entries against authoritative documentation.

        Requires a lookup engine to be configured at construction time.
        When no lookup engine is set, returns an empty ``ValidationReport``.

        Args:
            keys: Optional list of entry keys to validate. If None,
                validates all entries.
            strict: If ``True``, raise
                :class:`~tapps_brain.doc_validation.StrictValidationError`
                when any entries are flagged as doc-contradicted.  Intended
                for CI pipelines on markdown repos that must fail on
                contradictions.

        Returns:
            A ``ValidationReport`` with per-entry results. Changes are
            applied back to the store automatically.

        Raises:
            StrictValidationError: When ``strict=True`` and flagged > 0.
        """
        import asyncio

        from tapps_brain.doc_validation import MemoryDocValidator, ValidationReport

        if self._lookup_engine is None:
            return ValidationReport()

        validator = MemoryDocValidator(self._lookup_engine)

        # Collect entries to validate (hydrate so durable overflow is included).
        if keys is not None:
            for k in keys:
                self._ensure_entry_cached(k)
        else:
            self._merge_durable_entries()

        with self._serialized():
            if keys is not None:
                entries = [self._entries[k] for k in keys if k in self._entries]
            else:
                entries = list(self._entries.values())

        # Run async validation and result application in a single event loop
        # (store is synchronous by design — two asyncio.run() calls would create
        # two separate event loops, which is unnecessary overhead).
        async def _run_validation() -> Any:  # noqa: ANN401
            rep = await validator.validate_batch(entries, strict=strict)
            await validator.apply_results(rep, self)
            return rep

        return asyncio.run(_run_validation())

    # ------------------------------------------------------------------
    # Bi-temporal versioning (EPIC-004)
    # ------------------------------------------------------------------

    def supersede(self, old_key: str, new_value: str, **kwargs: Any) -> MemoryEntry:  # noqa: ANN401
        """Atomically supersede an existing entry with a new one.

        Sets ``invalid_at`` and ``superseded_by`` on the old entry and
        creates a new entry with ``valid_at`` set to now.

        Args:
            old_key: Key of the entry to supersede.
            new_value: Value for the replacement entry.
            **kwargs: Additional fields for the new entry (tier, tags, etc.).

        Returns:
            The newly created ``MemoryEntry``.

        Raises:
            KeyError: If *old_key* does not exist.
            ValueError: If *old_key* is already superseded.
        """
        self._metrics.increment("store.supersede")
        now = _utc_now_iso()

        old_entry = self._ensure_entry_cached(old_key)
        if old_entry is None:
            raise KeyError(old_key)

        with self._serialized():
            old_entry = self._entries.get(old_key) or old_entry
            if old_entry.invalid_at is not None:
                msg = (
                    f"Entry '{old_key}' is already superseded (invalid_at={old_entry.invalid_at})."
                )
                raise ValueError(msg)

            # Derive new key from old key or kwargs
            new_key = kwargs.pop("key", f"{old_key}.v{self._version_count(old_key) + 1}")

            # Invalidate the old entry
            invalidated = old_entry.model_copy(
                update={
                    "invalid_at": now,
                    "superseded_by": new_key,
                    "updated_at": now,
                }
            )
            self._entries[old_key] = invalidated

        # Persist the invalidated entry
        try:
            self._persistence.save(invalidated)
        except Exception:
            with self._serialized():
                if self._entries.get(old_key) is invalidated:
                    self._entries[old_key] = old_entry
            raise
        self._drop_if_concurrently_removed(old_key)

        # Create the new entry
        new_kwargs: dict[str, Any] = {
            "tier": str(old_entry.tier),
            "source": old_entry.source.value,
            "source_agent": old_entry.source_agent,
            "scope": old_entry.scope.value,
            "tags": list(old_entry.tags),
            "branch": old_entry.branch,
            "confidence": old_entry.confidence,
        }
        new_kwargs.update(kwargs)

        # dedup/conflict_check must be off: a dedup hit would return the
        # reinforced *other* entry (key != new_key), corrupting the cache
        # slot and pointing superseded_by at a phantom key; a conflict
        # check could invalidate unrelated entries mid-supersede.
        new_kwargs.setdefault("dedup", False)
        new_kwargs.setdefault("conflict_check", False)
        try:
            new_entry = self.save(key=new_key, value=new_value, **new_kwargs)
        except Exception:
            with self._serialized():
                self._entries[old_key] = old_entry
            try:
                self._persistence.save(old_entry)
            except Exception:
                logger.warning("supersede_rollback_failed", old_key=old_key, exc_info=True)
            raise
        if isinstance(new_entry, MemoryEntry) and new_entry.key != new_key:
            # Defensive: the save was redirected (write policy etc.) — roll
            # back rather than stamp valid_at onto the wrong durable row.
            with self._serialized():
                self._entries[old_key] = old_entry
            try:
                self._persistence.save(old_entry)
            except Exception:
                logger.warning("supersede_rollback_failed", old_key=old_key, exc_info=True)
            msg = (
                f"supersede: save() returned entry '{new_entry.key}' instead of "
                f"'{new_key}' (redirected write); superseding aborted"
            )
            raise ValueError(msg)
        if isinstance(new_entry, dict):
            with self._serialized():
                self._entries[old_key] = old_entry
            try:
                self._persistence.save(old_entry)
            except Exception:
                logger.warning("supersede_rollback_failed", old_key=old_key, exc_info=True)
            msg = f"Failed to create superseding entry: {new_entry.get('message', '')}"
            raise ValueError(msg)

        # Set valid_at on the new entry
        with self._serialized():
            previous_new = new_entry
            updated_new = new_entry.model_copy(update={"valid_at": now})
            self._entries[new_key] = updated_new
        try:
            self._persistence.save(updated_new)
        except Exception:
            with self._serialized():
                if self._entries.get(new_key) is updated_new:
                    self._entries[new_key] = previous_new
            raise
        self._drop_if_concurrently_removed(new_key)

        # Transfer relations from old entry to new entry
        old_relations = self.get_relations(old_key)
        if old_relations:
            transferred = [
                RelationEntry(
                    subject=r["subject"],
                    predicate=r["predicate"],
                    object_entity=r["object_entity"],
                    source_entry_keys=[new_key],
                    confidence=float(r.get("confidence", 0.8)),
                )
                for r in old_relations
            ]
            self._persistence.save_relations(new_key, transferred)
            with self._serialized():
                self._relations[new_key] = self._persistence.load_relations(new_key)
            try:
                self._persistence.delete_relations(old_key)
            except Exception:
                logger.warning(
                    "supersede_delete_old_relations_failed",
                    old_key=old_key,
                    exc_info=True,
                )
            with self._serialized():
                self._relations.pop(old_key, None)

        return updated_new

    def history(self, key: str) -> list[MemoryEntry]:
        """Return the full temporal chain for a key, ordered by ``valid_at``.

        Follows the ``superseded_by`` chain forward from the given key
        to find all successors, and backward to find all predecessors.

        Args:
            key: Any key in the version chain.

        Returns:
            All entries in the chain, ordered by ``valid_at`` ascending
            (entries without ``valid_at`` sort first).

        Raises:
            KeyError: If *key* does not exist.
        """
        if self._ensure_entry_cached(key) is None:
            raise KeyError(key)

        # Predecessors may live only in durable store beyond the cold-start cap.
        self._merge_durable_entries()

        with self._serialized():
            # Build reverse index: superseded_by -> source key
            reverse: dict[str, str] = {}
            for e in self._entries.values():
                if e.superseded_by:
                    reverse[e.superseded_by] = e.key

            # Walk backward to the root.
            # Guard against corrupted cyclic chains (e.g. A→B→A).
            root = key
            backward_visited: set[str] = {root}
            while root in reverse:
                root = reverse[root]
                if root in backward_visited:
                    logger.warning("history_backward_cycle_detected", key=key, cycle_key=root)
                    break
                backward_visited.add(root)

        # Walk forward from root, hydrating each hop from durable store.
        chain: list[MemoryEntry] = []
        chain_visited: set[str] = set()
        current: str | None = root
        while current is not None:
            if current in chain_visited:
                logger.warning("history_cycle_detected", key=key, cycle_key=current)
                break
            entry = self._ensure_entry_cached(current)
            if entry is None:
                break
            chain_visited.add(current)
            chain.append(entry)
            current = entry.superseded_by

        # Sort by valid_at (None sorts first)
        chain.sort(key=lambda e: e.valid_at or "")
        return chain

    def _version_count(self, key: str) -> int:
        """Count how many versions of a key exist (for generating version suffixes).

        Must be called while holding the store serialization lock (inside ``_serialized()``).
        """
        count = 0
        for k in self._entries:
            if k == key or k.startswith(f"{key}.v"):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Auto-recall (EPIC-003)
    # ------------------------------------------------------------------

    def recall(self, message: str, **kwargs: Any) -> Any:  # noqa: ANN401
        """Search for relevant memories and return injection-ready context.

        Convenience wrapper around ``RecallOrchestrator.recall()``. The
        orchestrator is created lazily on first call and reused after.

        Args:
            message: The user's incoming message to match against.
            **kwargs: Override ``RecallConfig`` fields for this call.
                ``session_id`` (str | None) is extracted here for implicit
                feedback tracking (STORY-029.3) and is NOT forwarded to
                ``RecallOrchestrator``.

        Returns:
            ``RecallResult`` with formatted memory section, metadata,
            and timing information.
        """
        log = logger.bind(project_id=self._project_id, op="recall")
        log.debug("store.recall.begin")
        # EPIC-029 story 029.3: extract session_id before forwarding kwargs.
        _raw_sid = kwargs.pop("session_id", None)
        session_id: str | None = str(_raw_sid) if _raw_sid is not None else None

        self._metrics.increment("store.recall")
        rm_increment_recall_total()
        orchestrator = self._recall_get_orchestrator()

        _recall_t0 = time.monotonic()
        with (
            start_span(SPAN_RECALL) as _recall_span,
            MetricsTimer(self._metrics, "store.recall_ms"),
        ):
            result = orchestrator.recall(message, **kwargs)
            if _recall_span is not None:
                self._annotate_recall_span(_recall_span, result, _recall_t0)

        # EPIC-029 story 029.3 + 029-4b: implicit feedback tracking.
        if session_id is not None:
            self._track_recall_feedback(session_id, message, result)

        qw = self._recall_quality_warning()
        if qw is not None:
            existing_qw = getattr(result, "quality_warning", None)
            merged = qw if not existing_qw else f"{existing_qw}; {qw}"
            result = result.model_copy(update={"quality_warning": merged})

        if not getattr(result, "memory_count", 0) and message.strip():
            with self._serialized():
                self._zero_result_queries.append((message.strip(), _utc_now_iso()))

        rm_add_recall_latency_ms((time.monotonic() - _recall_t0) * 1000.0)
        return result

    def _recall_get_orchestrator(self) -> Any:  # noqa: ANN401 — RecallOrchestrator
        """Lazily create + cache the :class:`RecallOrchestrator` (EPIC-011).

        Wires the Hive store and profile so the orchestrator can do hive-aware
        recall.  Created once under the store lock and reused thereafter.
        """
        from tapps_brain.recall import RecallConfig, RecallOrchestrator

        with self._serialized():
            if not hasattr(self, "_recall_orchestrator"):
                agent_profile = "repo-brain"
                recall_config: RecallConfig | None = None
                if self._profile is not None:
                    agent_profile = getattr(self._profile, "name", "repo-brain")
                    # Thread profile recall: block into the orchestrator config —
                    # without this the profile's recall settings are dead
                    # configuration and the RecallConfig dataclass defaults
                    # always win.
                    recall_prof = getattr(self._profile, "recall", None)
                    if recall_prof is not None:
                        recall_config = RecallConfig(
                            engagement_level=recall_prof.default_engagement,
                            max_tokens=recall_prof.default_token_budget,
                            min_score=recall_prof.min_score,
                            min_confidence=recall_prof.min_confidence,
                        )
                # hive_recall_weight deliberately NOT passed: an explicit
                # constructor weight would freeze the profile value at first
                # recall AND bypass get_hive_recall_weight(), whose diagnostics
                # circuit multiplier (EPIC-030: 0.5 when DEGRADED, 0.0 when
                # OPEN) must apply per-search.
                self._recall_orchestrator = RecallOrchestrator(
                    self,
                    config=recall_config,
                    decay_config=self._get_decay_config(),
                    hive_store=self._hive_store,
                    hive_agent_profile=agent_profile,
                    hive_agent_id=self._hive_agent_id,
                )
        return self._recall_orchestrator

    def _annotate_recall_span(self, span: Any, result: Any, t0: float) -> None:  # noqa: ANN401
        """Set OTel recall-span attributes + per-document retrieval events."""
        span.set_attribute("recall.hive_count", getattr(result, "hive_memory_count", 0))
        # STORY-032.3: add one structured event per retrieved document.
        record_retrieval_document_events(span, getattr(result, "memories", []))
        # STORY-070.12: standardised per-operation attributes.
        _recall_memories = getattr(result, "memories", [])
        span.set_attribute(ATTR_ROWS_RETURNED, len(_recall_memories))
        span.set_attribute(ATTR_LATENCY_MS, (time.monotonic() - t0) * 1000.0)
        # TAP-2170: GenAI semconv v1.40.0 data source identity.
        span.set_attribute("gen_ai.data_source.id", GEN_AI_DATA_SOURCE_ID)

    def _track_recall_feedback(self, session_id: str, message: str, result: Any) -> None:  # noqa: ANN401
        """EPIC-029: implicit-feedback bookkeeping for a recall in *session_id*.

        Flushes expired recall windows (lazy negatives), records which keys came
        from Hive, runs reformulation detection, and updates the per-session
        query / recall / value logs.
        """
        # Flush entries whose window has expired (lazy negative detection).
        with self._serialized():
            _expired = self._consume_expired_recalls(session_id)
        for _k in _expired:
            self._emit_implicit_feedback("implicit_negative", _k, session_id, -0.1)

        _memories: list[Any] = getattr(result, "memories", [])
        _recalled_keys: list[str] = [
            str(m.get("key", "")) for m in _memories if isinstance(m, dict) and m.get("key")
        ]

        # EPIC-029 story 029-7: remember which keys came from Hive (per session).
        with self._serialized():
            _hive_idx = self._hive_feedback_key_index.setdefault(session_id, {})
            for _m in _memories:
                if isinstance(_m, dict) and str(_m.get("source", "")) == "hive":
                    _hk = str(_m.get("key", ""))
                    if _hk:
                        _hive_idx[_hk] = str(_m.get("namespace", "universal"))

        # EPIC-029 story 029-4b: reformulation detection.  If the current query
        # is Jaccard-similar (>0.5 within 60s) to a recent one, emit
        # implicit_correction for the keys that earlier query recalled.
        _now_track = time.monotonic()
        with self._serialized():
            _reform_targets = self._detect_reformulation(session_id, message, _now_track)
            self._update_session_recall_logs(session_id, message, _recalled_keys, _now_track)

        for _k, _sim in _reform_targets:
            self._emit_implicit_feedback(
                "implicit_correction",
                _k,
                session_id,
                -0.5,
                details={"type": "reformulation", "jaccard_similarity": round(_sim, 4)},
            )

    def _update_session_recall_logs(
        self,
        session_id: str,
        message: str,
        recalled_keys: list[str],
        now_track: float,
    ) -> None:
        """Append to the per-session query / recall / value logs (caller holds lock).

        TAP-645: each log is capped at ``_SESSION_LOG_PER_SESSION_CAP`` entries
        (oldest trimmed) to bound memory in long-lived sessions.
        """
        # Update query log AFTER reformulation detection so the current query is
        # not matched against itself.
        _q_log = self._session_query_log.setdefault(session_id, [])
        _q_log.append((message, list(recalled_keys), now_track))
        if len(_q_log) > _SESSION_LOG_PER_SESSION_CAP:
            del _q_log[:-_SESSION_LOG_PER_SESSION_CAP]
        if not recalled_keys:
            return
        _r_log = self._session_recall_log.setdefault(session_id, [])
        _val_log = self._session_recalled_values.setdefault(session_id, [])
        for _k in recalled_keys:
            _r_log.append((_k, now_track))
            _entry_val = self._entries.get(_k)
            if _entry_val is not None:
                _val_log.append((_k, _entry_val.value, now_track))
        if len(_r_log) > _SESSION_LOG_PER_SESSION_CAP:
            del _r_log[:-_SESSION_LOG_PER_SESSION_CAP]
        if len(_val_log) > _SESSION_LOG_PER_SESSION_CAP:
            del _val_log[:-_SESSION_LOG_PER_SESSION_CAP]

    def _recall_quality_warning(self) -> str | None:
        """Map the diagnostics circuit-breaker state to a recall quality warning."""
        from tapps_brain.diagnostics import CircuitState

        st = self._circuit_breaker.state
        if st == CircuitState.DEGRADED:
            return "Memory quality degraded — results may be reduced in quality."
        if st == CircuitState.OPEN:
            return "Memory quality critical — Hive recall limited until recovery."
        if st == CircuitState.HALF_OPEN:
            return "Memory quality recovering — diagnostic probes in progress."
        return None

    def health(
        self,
        *,
        skip_consolidation_scan: bool = False,
        consolidation_scan_max_entries: int | None = None,
    ) -> StoreHealthReport:
        """Return a structured health report for this store.

        Parameters
        ----------
        skip_consolidation_scan:
            When ``True``, skip the O(n^2) ``find_consolidation_groups`` similarity
            scan and reuse the last cached candidate count instead.  Used by the
            visual ``/snapshot`` builder (EPIC-078), where an exact, freshly
            computed consolidation gauge is not worth a multi-minute, request-time
            scan over thousands of entries.  Defaults to ``False`` so all other
            callers keep the exact behaviour.
        consolidation_scan_max_entries:
            TAP-4332 size guard.  When set, run the live consolidation scan only
            if the store holds at most this many entries; above the cap, reuse the
            cached gauge (same as ``skip_consolidation_scan``).  Lets the snapshot
            keep a fresh consolidation gauge on normal-sized stores while never
            stalling the request path on pathologically large ones.  Ignored when
            ``skip_consolidation_scan`` is ``True``.
        """
        from datetime import UTC, datetime

        from tapps_brain.gc import MemoryGarbageCollector
        from tapps_brain.similarity import find_consolidation_groups

        # Include durable overflow so health counts match verify_integrity.
        self._merge_durable_entries()
        with self._serialized():
            entries = list(self._entries.values())

        now = datetime.now(tz=UTC)
        tier_counts = self._health_tier_distribution(entries)
        oldest_age = self._health_oldest_age_days(entries, now)
        schema_ver = self._persistence.get_schema_version()

        gc = MemoryGarbageCollector(
            config=self._get_decay_config(),
            gc_config=self._gc_config,
        )
        gc_candidates = gc.identify_candidates(entries)

        _over_scan_cap = (
            consolidation_scan_max_entries is not None
            and len(entries) > consolidation_scan_max_entries
        )
        if skip_consolidation_scan or _over_scan_cap:
            consolidation_candidates = self._last_consolidation_candidates
        else:
            # Mirror the periodic scanner's semantics — active rows only,
            # partitioned by memory_group, scanner's min group size —
            # otherwise the gauge counts merges that can never happen
            # (contradicted sources stay similar to their merge blob forever,
            # permanently inflating the metric after any merge).
            _scan_pool = [
                e
                for e in entries
                if not e.contradicted
                and e.superseded_by is None
                and str(getattr(e, "status", "active")) == "active"
            ]
            _by_group: dict[str | None, list[MemoryEntry]] = {}
            for e in _scan_pool:
                _by_group.setdefault(e.memory_group, []).append(e)
            consolidation_candidates = 0
            for _group_entries in _by_group.values():
                groups = find_consolidation_groups(
                    _group_entries,
                    threshold=self._consolidation_config.threshold,
                    min_group_size=self._consolidation_config.min_entries,
                )
                consolidation_candidates += sum(len(g) for g in groups)
            # Update tapps_brain.consolidation.candidates gauge (STORY-032.6).
            self._last_consolidation_candidates = consolidation_candidates

        # Federation config removed (STORY-059.2 — SQLite federation deleted).
        # Federation is now Postgres-only; project count not available from local config.
        federation_project_count = 0

        # Update tapps_brain.gc.candidates gauge — the init comment promises
        # this is refreshed by health() as well as gc().
        self._last_gc_candidates = len(gc_candidates)

        # Integrity verification (H4c)
        integrity = self.verify_integrity()

        # Rate limiter anomaly counts (H6c)
        rl_stats = self._rate_limiter.stats

        pkg_ver = ""
        try:
            import importlib.metadata

            pkg_ver = importlib.metadata.version("tapps-brain")
        except importlib.metadata.PackageNotFoundError:
            pkg_ver = ""

        prof_name, seed_ver, eff_ruleset = self._resolve_health_profile_metadata()

        # Document plane stats (TAP-5005) — best-effort; a missing table or
        # connection hiccup must not fail the whole health probe.
        document_count = 0
        document_total_bytes = 0
        doc_store = self.document_store()
        if doc_store is not None:
            try:
                document_count = doc_store.count()
                document_total_bytes = doc_store.total_bytes()
            except Exception:
                logger.warning("health.document_stats_failed", exc_info=True)

        _snap = self._metrics.snapshot()
        save_phases = compact_save_phase_summary(_snap)
        _ctr = _snap.counters

        return StoreHealthReport(
            store_path=str(self._project_root),
            entry_count=len(entries),
            max_entries=self._max_entries,
            max_entries_per_group=self._max_entries_per_group,
            schema_version=schema_ver,
            package_version=pkg_ver,
            profile_name=prof_name,
            profile_seed_version=seed_ver,
            tier_distribution=tier_counts,
            oldest_entry_age_days=oldest_age,
            consolidation_candidates=consolidation_candidates,
            gc_candidates=len(gc_candidates),
            federation_enabled=federation_project_count > 0,
            federation_project_count=federation_project_count,
            integrity_verified=integrity["verified"],
            integrity_tampered=integrity["tampered"],
            integrity_no_hash=integrity["no_hash"],
            integrity_tampered_keys=integrity["tampered_keys"][:20],
            integrity_likely_key_mismatch=bool(integrity.get("likely_key_mismatch", False)),
            rate_limit_minute_anomalies=rl_stats.minute_anomalies,
            rate_limit_lifetime_anomalies=rl_stats.lifetime_anomalies,
            rate_limit_total_writes=rl_stats.total_writes,
            rate_limit_exempt_writes=rl_stats.exempt_writes,
            relation_count=self.count_relations(),
            save_phase_summary=save_phases,
            rag_safety_ruleset_version=eff_ruleset,
            rag_safety_blocked_count=int(_ctr.get("rag_safety.blocked", 0)),
            rag_safety_sanitized_count=int(_ctr.get("rag_safety.sanitized", 0)),
            gc_runs_total=int(_ctr.get("store.gc", 0)),
            gc_archived_rows_total=int(_ctr.get("store.gc.archived", 0)),
            # Read total archive bytes from the Postgres gc_archive table so the
            # value survives process restarts (STORY-066.3).
            gc_archive_bytes_total=self._persistence.total_archive_bytes(),
            # TAP-549: session-state cardinality for /metrics alerting.
            active_session_count=self.active_session_count(),
            bloom_saturation=self._bloom.approximate_false_positive_rate(),
            embeddings_enabled=self._embedding_provider is not None,
            document_count=document_count,
            document_total_bytes=document_total_bytes,
        )

    @staticmethod
    def _health_tier_distribution(entries: list[MemoryEntry]) -> dict[str, int]:
        """Count entries per tier for the health report."""
        tier_counts: dict[str, int] = {}
        for entry in entries:
            tier_val = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1
        return tier_counts

    @staticmethod
    def _health_oldest_age_days(entries: list[MemoryEntry], now: datetime) -> float:
        """Age (in days) of the oldest entry, robust to mixed tz representations."""
        from datetime import UTC
        from datetime import datetime as _datetime

        oldest_age = 0.0
        for entry in entries:
            try:
                raw = entry.created_at.replace("Z", "+00:00")
                created = _datetime.fromisoformat(raw)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                days = (now - created).total_seconds() / 86400.0
                oldest_age = max(oldest_age, days)
            except (ValueError, TypeError, AttributeError):
                continue
        return oldest_age

    def _resolve_health_profile_metadata(self) -> tuple[str | None, str | None, str]:
        """Resolve (profile_name, seed_version, effective_safety_ruleset) for health."""
        from tapps_brain.safety import resolve_safety_ruleset_version

        prof = getattr(self, "_profile", None)
        prof_name: str | None = getattr(prof, "name", None) if prof is not None else None
        seed_ver: str | None = None
        _rs_pin: str | None = None
        if prof is not None:
            _seed = getattr(prof, "seeding", None)
            if _seed is not None:
                seed_ver = getattr(_seed, "seed_version", None)
            _sfc = getattr(prof, "safety", None)
            if _sfc is not None:
                _rs_pin = getattr(_sfc, "ruleset_version", None)
        return prof_name, seed_ver, resolve_safety_ruleset_version(_rs_pin)

    def gc(self, *, dry_run: bool = False) -> Any:  # noqa: ANN401
        """Run garbage collection on the store.

        Archives stale rows to the ``gc_archive`` Postgres table (migration 006,
        STORY-066.3).  Floor-retention candidates whose layer defines
        ``demotion_to`` in the active profile are demoted a tier instead of
        archived (EPIC-010).  Counters: ``store.gc`` (invocations),
        ``store.gc.archived`` (rows), ``store.gc.archive_bytes`` (bytes
        written), ``store.gc.demoted`` (demotions).

        Args:
            dry_run: If True, only identify candidates without archiving.

        Returns:
            ``GCResult`` with keys, ``reason_counts``, and byte fields
            (``estimated_archive_bytes`` when dry-run, ``archive_bytes`` when live).
        """
        from datetime import UTC, datetime

        from tapps_brain.gc import (
            GCResult,
            MemoryGarbageCollector,
            aggregate_gc_reason_counts,
            archive_entries_jsonl_utf8_bytes,
        )

        self._metrics.increment("store.gc")
        gc_collector = MemoryGarbageCollector(
            config=self._get_decay_config(),
            gc_config=self._gc_config,
        )
        # Include durable overflow beyond the cold-start cache cap.
        self._merge_durable_entries()
        with self._serialized():
            entries = list(self._entries.values())
        now = datetime.now(tz=UTC)
        # Single candidate scan: derive the archival set from the detail records
        # rather than running identify_candidates() (same _archive_reasons pass)
        # a second time over every entry.
        details = gc_collector.stale_candidate_details(entries, now=now)
        detail_keys = {d.key for d in details}
        candidates = [e for e in entries if e.key in detail_keys]
        # Update tapps_brain.gc.candidates gauge with the current candidate count
        # (STORY-032.6) — recorded once per gc() call so get_metrics() stays cheap.
        self._last_gc_candidates = len(candidates)

        # EPIC-010: demote-instead-of-archive. When the profile's layer for a
        # floor-retention candidate defines ``demotion_to``, the entry moves
        # down a tier rather than into gc_archive. Contradicted and
        # session-expired candidates archive regardless.
        detail_by_key = {d.key: d for d in details}
        demoted_keys: list[str] = []
        if self._profile is not None:
            from tapps_brain.promotion import PromotionEngine

            engine = PromotionEngine(self._get_decay_config())
            to_archive: list[MemoryEntry] = []
            for entry in candidates:
                target: str | None = None
                if detail_by_key[entry.key].reasons == ["floor_retention"]:
                    try:
                        target = engine.check_demotion(entry, self._profile, now=now)
                    except Exception:
                        logger.warning("gc.demotion_check_failed", key=entry.key, exc_info=True)
                if target is None:
                    to_archive.append(entry)
                elif dry_run or self._gc_demote_entry(entry, target):
                    demoted_keys.append(entry.key)
                else:
                    to_archive.append(entry)
            candidates = to_archive
            details = [detail_by_key[e.key] for e in candidates]
            if demoted_keys and not dry_run:
                self._metrics.increment("store.gc.demoted", len(demoted_keys))

        reason_counts = aggregate_gc_reason_counts(details)
        candidate_keys = [c.key for c in candidates]

        if dry_run:
            return GCResult(
                archived_count=0,
                remaining_count=len(entries),
                archived_keys=candidate_keys,
                dry_run=True,
                reason_counts=reason_counts,
                estimated_archive_bytes=archive_entries_jsonl_utf8_bytes(candidates),
                demoted_count=len(demoted_keys),
                demoted_keys=demoted_keys,
            )

        # Archive to Postgres gc_archive table (STORY-066.3) and delete from store.
        # Only delete keys that were successfully archived — archive_entry() returns
        # 0 on failure; deleting those would silently destroy data.
        archived_keys: list[str] = []
        archive_bytes = 0
        for entry in candidates:
            # Re-read the current row: a save()/reinforce() that landed after
            # the candidate snapshot must not be destroyed by archiving the
            # stale payload and then deleting the fresh row.
            with self._serialized():
                current = self._entries.get(entry.key)
            if current is None:
                continue
            if current.updated_at != entry.updated_at:
                logger.info(
                    "gc.skip_concurrently_updated",
                    key=entry.key,
                    hint="entry changed since candidate scan; left in store",
                )
                continue
            nbytes = self._persistence.archive_entry(current)
            if nbytes > 0:
                archived_keys.append(entry.key)
                archive_bytes += nbytes
            else:
                logger.warning(
                    "gc.archive_failed_skip_delete",
                    key=entry.key,
                    hint="entry left in store until archive succeeds",
                )

        for key in archived_keys:
            self.delete(key)

        self._metrics.increment("store.gc.archived", len(archived_keys))
        if archive_bytes:
            self._metrics.increment("store.gc.archive_bytes", archive_bytes)

        # Prune session index rows aligned with GC retention policy.
        session_chunks_deleted = self.cleanup_sessions(
            ttl_days=self._gc_config.session_index_ttl_days
        )
        if session_chunks_deleted:
            self._metrics.increment("store.gc.session_chunks_deleted", session_chunks_deleted)

        # Document plane retention sweep (TAP-5005): remove documents whose
        # expires_at has passed; chunks cascade via the FK.
        documents_expired = 0
        doc_store = self.document_store()
        if doc_store is not None:
            try:
                documents_expired = doc_store.delete_expired()
            except Exception:
                logger.warning("gc.documents_expiry_sweep_failed", exc_info=True)
        if documents_expired:
            self._metrics.increment("store.gc.documents_expired", documents_expired)

        # TAP-549: sweep the in-memory session-state helper dicts so
        # ``session_id`` rotation by long-lived clients cannot slow-burn
        # OOM the adapter.  Runs unconditionally on live GC (dry_run was
        # returned earlier) because it only drops process-local state —
        # there's nothing to preview.
        self._sweep_stale_sessions()

        # TAP-726: rebuild the Bloom filter from the surviving entries so
        # stale bits from archived items are removed.  Without this, the
        # filter accumulates bits for every entry that ever existed and
        # eventually saturates (FP rate → 1.0), making the dedup fast-path
        # useless.  O(k*n) where k = hash_count and n = surviving entries.
        # The lock is held for the entire rebuild so concurrent save() threads
        # cannot race on _bloom._bits / _count during the clear+re-add cycle.
        with self._serialized():
            surviving_values = [normalize_for_dedup(e.value) for e in self._entries.values()]
            self._bloom.rebuild(surviving_values)

        return GCResult(
            archived_count=len(archived_keys),
            remaining_count=len(self._entries),
            archived_keys=archived_keys,
            dry_run=False,
            reason_counts=reason_counts,
            archive_bytes=archive_bytes,
            session_chunks_deleted=session_chunks_deleted,
            documents_expired=documents_expired,
            demoted_count=len(demoted_keys),
            demoted_keys=demoted_keys,
        )

    def _gc_demote_entry(self, entry: MemoryEntry, target_tier: str) -> bool:
        """Apply a GC demotion: persist the tier change + audit. Returns success.

        Best-effort — a failed demotion leaves the entry unchanged so the GC
        pass falls back to archiving it.
        """
        old_tier = str(entry.tier)
        try:
            with self._serialized():
                # Demote the *current* row, not the candidate-scan snapshot —
                # assigning a snapshot-era copy would silently revert any
                # concurrent update.  A changed row is no longer the entry the
                # candidate scan judged; fall back to the archive path (which
                # re-checks updated_at itself).
                current = self._entries.get(entry.key)
                if current is None or current.updated_at != entry.updated_at:
                    return False
                demoted = current.model_copy(
                    update={"tier": target_tier, "updated_at": _utc_now_iso()}
                )
                self._entries[entry.key] = demoted
            try:
                self._persistence.save(demoted)
            except Exception:
                with self._serialized():
                    if self._entries.get(entry.key) is demoted:
                        self._entries[entry.key] = current
                raise
            self._drop_if_concurrently_removed(entry.key)
            self._persistence.append_audit(
                action="demote",
                key=entry.key,
                extra={"from_tier": old_tier, "to_tier": target_tier, "trigger": "gc"},
            )
        except Exception:
            logger.warning(
                "gc.demotion_apply_failed",
                key=entry.key,
                to_tier=target_tier,
                exc_info=True,
            )
            return False
        return True

    def list_gc_stale_details(self, *, now: Any = None) -> list[Any]:  # noqa: ANN401
        """Return GC stale candidates with reasons (GitHub #21)."""
        from datetime import UTC, datetime

        from tapps_brain.gc import MemoryGarbageCollector

        # Same durable merge as gc() so overflow rows beyond the cold-start
        # cache limit appear in operator previews (CLI/HTTP stale).
        self._merge_durable_entries()
        gc_collector = MemoryGarbageCollector(
            config=self._get_decay_config(),
            gc_config=self._gc_config,
        )
        with self._serialized():
            entries = list(self._entries.values())
        _now = now if now is not None else datetime.now(tz=UTC)
        details = gc_collector.stale_candidate_details(entries, now=_now)
        return list(details)

    def audit(
        self,
        *,
        key: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[Any]:
        """Query the audit trail (Postgres ``audit_log`` table — migration 005).

        Convenience wrapper around :class:`~tapps_brain.audit.AuditReader`.

        Args:
            key: Filter by memory entry key.
            event_type: Filter by event type (save, delete, etc.).
            since: ISO-8601 lower bound (inclusive).
            until: ISO-8601 upper bound (inclusive).
            limit: Maximum number of entries to return.

        Returns:
            The *most recent* ``limit`` matching ``AuditEntry`` objects, in
            chronological (oldest-first) order.  Previously the limit was
            applied to an ascending scan, so once the log outgrew ``limit``
            this method could only ever return the first events recorded.
        """
        from tapps_brain.audit import AuditReader

        reader = AuditReader(self._persistence)
        entries = reader.query(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
            newest_first=True,
        )
        return list(reversed(entries))

    def get_metrics(self) -> MetricsSnapshot:
        """Return a snapshot of in-process operation metrics.

        Pool stats (when a Hive backend is configured) are included as gauges:

        - ``pool.hive.connections_in_use`` — active connections (pool_size - pool_available)
        - ``pool.hive.pool_size`` — total open connections
        - ``pool.hive.saturation`` — fraction of max_size in use (0.0-1.0)

        Custom ``tapps_brain.*`` gauges (STORY-032.6) are also included:

        - ``tapps_brain.entries.count`` — current memory entry count (updated on every call)
        - ``tapps_brain.consolidation.candidates`` — last known consolidation candidate count
          (updated by :meth:`health` or :meth:`gc`; stale between runs)
        - ``tapps_brain.gc.candidates`` — last known GC candidate count
          (updated by :meth:`gc`; stale between runs)
        - ``tapps_brain.session_query_log.entries`` — total entries across all active
          session query logs (TAP-645); capped at ``_SESSION_LOG_PER_SESSION_CAP``
          entries per session

        .. note::
            **Cardinality rule:** these gauges must **never** carry ``entry_key``,
            ``query``, ``session_id``, or any user-controlled string as an OTel
            attribute.  Only bounded enum values from ``ALLOWED_METRIC_DIMENSIONS``
            are safe as metric labels.
        """
        if self._hive_store is not None:
            _pool_fn = getattr(self._hive_store, "get_pool_stats", None)
            if callable(_pool_fn):
                try:
                    _ps = _pool_fn()
                    _size = float(_ps.get("pool_size", 0))
                    _avail = float(_ps.get("pool_available", 0))
                    _saturation = float(_ps.get("pool_saturation", 0.0))
                    self._metrics.set_gauge(
                        "pool.hive.connections_in_use", max(0.0, _size - _avail)
                    )
                    self._metrics.set_gauge("pool.hive.pool_size", _size)
                    self._metrics.set_gauge("pool.hive.saturation", _saturation)
                except (AttributeError, TypeError, KeyError):
                    pass  # hive pool stats unavailable; best-effort metrics skip

        # tapps_brain.* gauges — STORY-032.6
        self._merge_durable_entries()
        with self._serialized():
            _entry_count = len(self._entries)
            # TAP-645: expose per-session log size so growth is visible in metrics.
            _session_log_entries = sum(len(v) for v in self._session_query_log.values())
        self._metrics.set_gauge("tapps_brain.entries.count", float(_entry_count))
        self._metrics.set_gauge(
            "tapps_brain.session_query_log.entries",
            float(_session_log_entries),
        )
        self._metrics.set_gauge(
            "tapps_brain.consolidation.candidates",
            float(self._last_consolidation_candidates),
        )
        self._metrics.set_gauge(
            "tapps_brain.gc.candidates",
            float(self._last_gc_candidates),
        )

        return self._metrics.snapshot()

    def get_hive_recall_weight(self) -> float:
        """Effective Hive recall weight including diagnostics circuit multiplier."""
        base = 0.8
        if self._profile is not None:
            hc = getattr(self._profile, "hive", None)
            if hc is not None:
                base = float(getattr(hc, "recall_weight", base))
        return max(0.0, min(1.0, base * float(self._hive_recall_weight_multiplier)))

    def _ensure_diagnostics_history(self) -> None:
        if self._diagnostics_history_store is not None:
            return
        from tapps_brain.diagnostics import DiagnosticsHistoryStore

        cm = getattr(self._persistence, "_cm", None)
        project_id = getattr(self._persistence, "_project_id", None)
        agent_id = getattr(self._persistence, "_agent_id", None)
        if cm is None:
            # No Postgres connection manager (e.g. InMemoryPrivateBackend in tests).
            # Fall back to an in-memory store so diagnostics history still works.
            from tapps_brain.diagnostics import InMemoryDiagnosticsHistoryStore

            self._diagnostics_history_store = InMemoryDiagnosticsHistoryStore()
            return
        if project_id is None or agent_id is None:
            logger.debug("diagnostics_history.skipped_no_project_or_agent")
            return
        self._diagnostics_history_store = DiagnosticsHistoryStore(
            cm,
            project_id=project_id,
            agent_id=agent_id,
        )
        self._anomaly_detector.reset_from_history(
            self._diagnostics_history_store.history(limit=500)
        )

    def diagnostics(
        self,
        *,
        record_history: bool = True,
        run_remediation: bool = True,
    ) -> Any:  # noqa: ANN401
        """Run quality diagnostics, update circuit breaker, optional history (EPIC-030)."""
        from tapps_brain.diagnostics import DiagnosticsConfig, run_diagnostics

        self._ensure_diagnostics_history()
        hist_rows: list[dict[str, Any]] = []
        if self._diagnostics_history_store is not None:
            hist_rows = self._diagnostics_history_store.history(limit=500)
        dcfg = DiagnosticsConfig()
        if self._profile is not None and getattr(self._profile, "diagnostics", None) is not None:
            dcfg = DiagnosticsConfig.model_validate(self._profile.diagnostics.model_dump())
        report = run_diagnostics(self, config=dcfg, history_for_correlation=hist_rows)

        st, alerts = self._apply_diagnostics_circuit(report, run_remediation=run_remediation)
        report = report.model_copy(
            update={"anomalies": alerts, "circuit_state": st.value},
        )
        if record_history and self._diagnostics_history_store is not None:
            self._diagnostics_history_store.record(report, circuit_state=st.value)
            self._diagnostics_history_store.prune_older_than(dcfg.retention_days)
        self._audit_diagnostics(report.composite_score, st.value)
        self._metrics.increment("store.diagnostics")
        return report

    def _apply_diagnostics_circuit(
        self,
        report: Any,  # noqa: ANN401 — diagnostics report model
        *,
        run_remediation: bool,
    ) -> tuple[Any, list[Any]]:
        """Advance the diagnostics circuit breaker for *report* (EPIC-030).

        Records a half-open probe, transitions on the composite score, runs
        remediation + half-open cooldown when OPEN, refreshes the Hive recall
        multiplier, and returns ``(circuit_state, anomaly_alerts)``.
        """
        from tapps_brain.diagnostics import CircuitState, hive_recall_multiplier, maybe_remediate

        if self._circuit_breaker.state == CircuitState.HALF_OPEN:
            self._circuit_breaker.record_probe(report.composite_score)
        st = self._circuit_breaker.transition(report.composite_score)
        nowm = time.monotonic()
        if st == CircuitState.OPEN and run_remediation:
            maybe_remediate(self, report, self._circuit_breaker, now_mono=nowm)
        if st == CircuitState.OPEN:
            self._circuit_breaker.enter_half_open_if_cooled(nowm)
            st = self._circuit_breaker.state
        alerts = self._anomaly_detector.detect(report)
        self._hive_recall_weight_multiplier = hive_recall_multiplier(st)
        return st, alerts

    def _audit_diagnostics(self, composite_score: float, circuit_state: str) -> None:
        """Best-effort audit-log append for a diagnostics run (never raises)."""
        try:
            self._persistence.append_audit(
                "diagnostics_record",
                "",
                {"composite_score": composite_score, "circuit_state": circuit_state},
            )
        except Exception:
            logger.warning("diagnostics_audit_failed", exc_info=True)

    def diagnostics_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent diagnostics snapshots from Postgres (EPIC-030).

        STORY-069.7: each returned row carries ``project_id`` (or ``None``
        for legacy single-tenant backends) so downstream filters and the
        ``/snapshot?project=`` query can scope rows by tenant.
        """
        self._ensure_diagnostics_history()
        if self._diagnostics_history_store is None:
            return []
        rows = cast(
            "list[dict[str, Any]]",
            self._diagnostics_history_store.history(limit=limit),
        )
        for row in rows:
            row.setdefault("project_id", self._project_id)
        return rows

    # ------------------------------------------------------------------
    # Tag management (EPIC-015)
    # ------------------------------------------------------------------

    def list_tags(self) -> dict[str, int]:
        """Return all unique tags across all entries with their usage counts.

        Returns:
            Dict mapping tag → count of entries that carry that tag.
        """
        self._merge_durable_entries()
        with self._serialized():
            entries = list(self._entries.values())
        counts: dict[str, int] = {}
        for entry in entries:
            for tag in entry.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def update_tags(
        self,
        key: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> MemoryEntry | dict[str, str]:
        """Atomically add and/or remove tags on an existing entry.

        Args:
            key: The memory entry key.
            add: Tags to add (ignored when already present).
            remove: Tags to remove (ignored when not present).

        Returns:
            The updated ``MemoryEntry`` on success, or a dict with
            ``"error"`` and ``"message"`` keys on failure.
        """
        from tapps_brain.models import MAX_TAGS

        add_set = set(add or [])
        remove_set = set(remove or [])

        if self._ensure_entry_cached(key) is None:
            return {"error": "not_found", "message": f"Entry '{key}' not found."}

        with self._serialized():
            entry = self._entries.get(key)
            if entry is None:
                return {"error": "not_found", "message": f"Entry '{key}' not found."}

            current = list(entry.tags)
            # Remove first, then add (preserves existing order)
            updated_tags = [t for t in current if t not in remove_set]
            for tag in add_set:
                if tag not in updated_tags:
                    updated_tags.append(tag)

            if len(updated_tags) > MAX_TAGS:
                return {
                    "error": "too_many_tags",
                    "message": (
                        f"Cannot have more than {MAX_TAGS} tags ({len(updated_tags)} would result)."
                    ),
                }

            from tapps_brain.models import _utc_now_iso

            updated = entry.model_copy(update={"tags": updated_tags, "updated_at": _utc_now_iso()})
            previous = entry
            self._entries[key] = updated

        try:
            self._persistence.save(updated)
        except Exception:
            with self._serialized():
                if self._entries.get(key) is updated:
                    self._entries[key] = previous
            raise
        self._drop_if_concurrently_removed(key)
        return updated

    def entries_by_tag(
        self,
        tag: str,
        *,
        tier: str | None = None,
    ) -> list[MemoryEntry]:
        """Return all entries that carry a specific tag.

        Args:
            tag: The tag to filter by.
            tier: Optional tier filter.

        Returns:
            List of matching ``MemoryEntry`` objects.
        """
        return self.list_all(tags=[tag], tier=tier)

    def close(self) -> None:
        """Close the underlying persistence layer."""
        # Swap under the store lock so a concurrent _get_feedback_store()
        # (double-checked init) cannot observe a half-closed instance.
        with self._serialized():
            fb, self._feedback_store_instance = self._feedback_store_instance, None
        if fb is not None:
            fb.close()
        if self._diagnostics_history_store is not None:
            self._diagnostics_history_store.close()
            self._diagnostics_history_store = None
        self._persistence.close()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # ---- Implicit feedback helpers (EPIC-029 story 029.3) ----

    def _get_implicit_feedback_window(self) -> int:
        """Return the implicit feedback window in seconds from FeedbackConfig."""
        if self._profile is not None:
            cfg = getattr(self._profile, "feedback", None)
            if cfg is not None:
                return int(getattr(cfg, "implicit_feedback_window_seconds", 300))
        return 300

    def _consume_expired_recalls(self, session_id: str) -> list[str]:
        """Return keys of expired unreinforced recalls and remove them from the log.

        Must be called while holding the store serialization lock (inside ``_serialized()``).
        Expired = recall_time < now - window AND not yet reinforced.
        """
        window = self._get_implicit_feedback_window()
        now_mono = time.monotonic()
        log = self._session_recall_log.get(session_id, [])
        reinforced = self._session_reinforced.get(session_id, set())
        expired: list[str] = []
        remaining: list[tuple[str, float]] = []
        for entry_key, recall_time in log:
            if now_mono - recall_time > window:
                if entry_key not in reinforced:
                    expired.append(entry_key)
                # Expired entries are removed regardless of reinforced state
            else:
                remaining.append((entry_key, recall_time))
        if len(remaining) != len(log):
            self._session_recall_log[session_id] = remaining
        return expired

    def _check_and_mark_reinforced(self, session_id: str, key: str) -> bool:
        """Check if *key* was recalled in *session_id* within the feedback window.

        If so, marks it as reinforced and returns True.
        Must be called while holding the store serialization lock (inside ``_serialized()``).
        """
        window = self._get_implicit_feedback_window()
        now_mono = time.monotonic()
        log = self._session_recall_log.get(session_id, [])
        for recall_key, recall_time in log:
            if recall_key == key and now_mono - recall_time <= window:
                self._session_reinforced.setdefault(session_id, set()).add(key)
                return True
        return False

    def _emit_implicit_feedback(
        self,
        event_type: str,
        entry_key: str,
        session_id: str | None,
        utility_score: float,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Emit an implicit feedback event (best-effort, never raises).

        Called outside ``_serialized()`` to avoid holding the lock during I/O.
        Optional *details* dict is forwarded to ``FeedbackEvent.details``.
        """
        try:
            from tapps_brain.feedback import FeedbackEvent

            event = FeedbackEvent(
                event_type=event_type,
                entry_key=entry_key,
                session_id=session_id,
                utility_score=utility_score,
                details=details or {},
            )
            self._get_feedback_store().record(event)
            self._metrics.increment(f"store.feedback.{event_type}")
            self._propagate_feedback_to_hive(event, session_id)
        except Exception:
            logger.warning(
                "implicit_feedback_emit_failed",
                event_type=event_type,
                entry_key=entry_key,
            )

    # ---- Implicit feedback: reformulation + correction (EPIC-029 story 029-4b) ----

    def _detect_reformulation(
        self, session_id: str, current_query: str, now_mono: float
    ) -> list[tuple[str, float]]:
        """Detect query reformulations and return (entry_key, jaccard_sim) pairs.

        Must be called while holding the store serialization lock (inside ``_serialized()``).

        Compares *current_query* against recent queries in ``_session_query_log``
        for *session_id*.  Any past query within ``_REFORMULATION_WINDOW`` seconds
        whose Jaccard similarity to *current_query* exceeds 0.5 is treated as a
        reformulation: the entry keys recalled by that past query are returned as
        targets for an ``implicit_correction`` event (utility_score=-0.5).

        Old entries (> ``_REFORMULATION_WINDOW`` seconds) are pruned lazily.
        """
        q_log = self._session_query_log.get(session_id, [])
        targets: list[tuple[str, float]] = []
        remaining: list[tuple[str, list[str], float]] = []
        for past_query, past_keys, past_time in q_log:
            age = now_mono - past_time
            if age > _REFORMULATION_WINDOW:
                continue  # prune expired, do not keep
            remaining.append((past_query, past_keys, past_time))
            sim = _jaccard_similarity(current_query, past_query)
            if sim > 0.5:
                targets.extend((key, sim) for key in past_keys)
        self._session_query_log[session_id] = remaining
        return targets

    def _detect_correction(
        self, session_id: str, saved_value: str, now_mono: float
    ) -> list[tuple[str, float]]:
        """Detect recall-then-store corrections; return (entry_key, overlap) pairs.

        Must be called while holding the store serialization lock (inside ``_serialized()``).

        For each recently recalled entry in ``_session_recalled_values`` for
        *session_id* that is still within the implicit feedback window and whose
        value has > 40% token overlap with *saved_value*, an
        ``implicit_correction`` event (utility_score=-0.3) is warranted.

        Matched entries are consumed (removed) to prevent double-emission.
        Expired entries are pruned lazily.
        """
        window = self._get_implicit_feedback_window()
        val_log = self._session_recalled_values.get(session_id, [])
        targets: list[tuple[str, float]] = []
        remaining: list[tuple[str, str, float]] = []
        for key, recalled_value, recall_time in val_log:
            age = now_mono - recall_time
            if age > window:
                continue  # expired: prune
            overlap = _token_overlap_ratio(saved_value, recalled_value)
            if overlap > 0.4:
                targets.append((key, overlap))
                # Consumed: don't re-add to remaining
            else:
                remaining.append((key, recalled_value, recall_time))
        self._session_recalled_values[session_id] = remaining
        return targets

    # ---- End implicit feedback helpers ----

    # ---- Session-state sweeper (TAP-549) ----

    def _session_state_session_ids(self) -> set[str]:
        """Union of session_ids present in any session-keyed helper dict.

        Caller must hold the store serialization lock.
        """
        return (
            self._session_recall_log.keys()
            | self._session_reinforced.keys()
            | self._session_query_log.keys()
            | self._session_recalled_values.keys()
            | self._hive_feedback_key_index.keys()
        )

    def _session_last_touch_map(self) -> dict[str, float]:
        """Compute per-session last-activity monotonic time by walking the logs.

        Sessions that appear only in the timestamp-less dicts
        (``_session_reinforced``, ``_hive_feedback_key_index``) have no
        recoverable activity time, so they're stamped with ``now`` — they
        can only be evicted by the LRU hard-cap, never aged out by the
        stale-session sweep.  That's deliberate: the timestamp-less dicts
        are only written immediately after a matching entry in the
        timestamped dicts, so a session seen only in them is about to
        grow a timestamped entry anyway.

        Caller must hold the store serialization lock.
        """
        last_touch: dict[str, float] = {}

        def _bump(sid: str, t: float) -> None:
            prev = last_touch.get(sid)
            if prev is None or t > prev:
                last_touch[sid] = t

        for sid, recall_items in self._session_recall_log.items():
            for _k, t in recall_items:
                _bump(sid, t)
        for sid, query_items in self._session_query_log.items():
            for _q, _ks, t in query_items:
                _bump(sid, t)
        for sid, value_items in self._session_recalled_values.items():
            for _k, _v, t in value_items:
                _bump(sid, t)

        now_mono = time.monotonic()
        for sid in self._session_state_session_ids():
            if sid not in last_touch:
                last_touch[sid] = now_mono
        return last_touch

    def _drop_session_state(self, session_id: str) -> None:
        """Remove ``session_id`` from every session-keyed helper dict.

        Caller must hold the store serialization lock.
        """
        self._session_recall_log.pop(session_id, None)
        self._session_reinforced.pop(session_id, None)
        self._session_query_log.pop(session_id, None)
        self._session_recalled_values.pop(session_id, None)
        self._hive_feedback_key_index.pop(session_id, None)

    def _sweep_stale_sessions(self) -> dict[str, int]:
        """Drop session_ids with no recent activity; LRU-evict above the cap.

        Acceptance target for TAP-549 — called from :meth:`gc` so existing
        GC cadence handles both memory-entry retention and session-state
        bounds in one pass.

        Returns a dict with per-reason counts:

        * ``stale_removed`` — sessions idle > ``implicit_feedback_window * 2``.
        * ``lru_evicted``   — sessions evicted above the LRU hard cap.
        """
        window = self._get_implicit_feedback_window()
        cutoff = time.monotonic() - 2 * window
        with self._serialized():
            last_touch = self._session_last_touch_map()

            stale_ids = [sid for sid, t in last_touch.items() if t < cutoff]
            for sid in stale_ids:
                self._drop_session_state(sid)
                last_touch.pop(sid, None)

            evicted = 0
            overflow = len(last_touch) - _SESSION_STATE_HARD_CAP
            if overflow > 0:
                # Sort oldest-first; evict just enough to reach the cap.
                victims = sorted(last_touch.items(), key=lambda kv: kv[1])[:overflow]
                for sid, _t in victims:
                    self._drop_session_state(sid)
                    evicted += 1

        if stale_ids:
            self._metrics.increment("store.session_state_stale_removed", len(stale_ids))
        if evicted:
            self._metrics.increment("store.session_state_evicted", evicted)
        return {"stale_removed": len(stale_ids), "lru_evicted": evicted}

    def active_session_count(self) -> int:
        """Return the number of distinct session_ids tracked in the helper dicts.

        Exposed in ``StoreHealthReport`` and the ``/metrics`` gauge
        ``tapps_brain_store_active_sessions`` so operators can alert on
        unbounded growth (TAP-549).
        """
        with self._serialized():
            return len(self._session_state_session_ids())

    # ---- End session-state sweeper ----

    def _count_entries_in_memory_group(self, memory_group: str | None) -> int:
        """Count live rows whose ``memory_group`` matches (``None`` = ungrouped)."""
        # Caller holds the store lock; merge durable overflow so caps see
        # experience/out-of-band rows that never entered the cold-start cache.
        snapshot_epoch = self._removal_epoch
        durable = self._persistence.load_all()
        for entry in durable:
            if entry.key not in self._entries:
                # Same contract as _merge_durable_entries, *including* the
                # removal-tombstone guard: a delete whose durable phase is
                # still in flight must not be resurrected into the cache.
                if self._removed_at.get(entry.key, 0) > snapshot_epoch:
                    continue
                self._entries[entry.key] = entry
                # Keep derived indexes consistent (same contract as
                # _merge_durable_entries): dedup and graph centrality must see
                # merged rows.
                self._bloom.add(normalize_for_dedup(entry.value))
                self._index_entry_entities(entry.key, entry.value)
        return sum(1 for e in self._entries.values() if e.memory_group == memory_group)

    def _evict_entry_key(self, key: str, *, reason: str, memory_group: str | None = None) -> None:
        """Remove one key from cache + durable store with full cleanup parity.

        Mirrors :meth:`QueryMixin.delete` ordering: durable row first, then
        relations, with cache restore on any persistence failure so a
        mid-eviction error cannot leave relations deleted while the row remains
        (or a cache miss for a still-durable row).
        """
        entry = self._entries.pop(key, None)
        try:
            self._persistence.delete(key)
        except Exception:
            if entry is not None:
                self._entries[key] = entry
            raise
        try:
            self._persistence.delete_relations(key)
        except Exception:
            if entry is not None:
                self._entries[key] = entry
                try:
                    self._persistence.save(entry)
                except Exception:
                    logger.warning("evict_rollback_failed", key=key, exc_info=True)
            raise
        self._relations.pop(key, None)
        self._remove_entry_entities(key)
        self._note_removed_locked(key)
        logger.info(
            "memory_evicted",
            key=key,
            reason=reason,
            memory_group=memory_group,
        )

    def _evict_lowest_confidence_in_group(self, memory_group: str | None) -> None:
        """Evict lowest-confidence row within one ``memory_group`` bucket."""
        candidates = [k for k, e in self._entries.items() if e.memory_group == memory_group]
        if not candidates:
            return
        lowest_key = min(candidates, key=lambda k: self._entries[k].confidence)
        self._evict_entry_key(lowest_key, reason="max_entries_per_group", memory_group=memory_group)

    def _evict_lowest_confidence_prefer_group(self, memory_group: str | None) -> None:
        """Global cap: prefer evicting from the same bucket as the incoming save."""
        if self._max_entries_per_group is not None:
            in_group = [k for k, e in self._entries.items() if e.memory_group == memory_group]
            if in_group:
                lowest_key = min(in_group, key=lambda k: self._entries[k].confidence)
                self._evict_entry_key(
                    lowest_key, reason="max_entries_fair", memory_group=memory_group
                )
                return
        self._evict_lowest_confidence()

    def _enforce_entry_caps_before_assign(
        self,
        *,
        key: str,
        new_group: str | None,
        existing: MemoryEntry | None,
    ) -> None:
        """Evict if needed so assigning ``key`` into ``new_group`` respects caps.

        Must be called while holding the store serialization lock (inside ``_serialized()``).
        """
        cap_g = self._max_entries_per_group
        if cap_g is not None:
            if existing is None:
                if self._count_entries_in_memory_group(new_group) >= cap_g:
                    self._evict_lowest_confidence_in_group(new_group)
            else:
                old_g = existing.memory_group
                if old_g != new_group:
                    n_in_new = self._count_entries_in_memory_group(new_group)
                    if n_in_new + 1 > cap_g:
                        self._evict_lowest_confidence_in_group(new_group)

        if existing is None and len(self._entries) >= self._max_entries:
            self._evict_lowest_confidence_prefer_group(new_group)

    def _evict_lowest_confidence(self) -> None:
        """Evict the entry with the lowest confidence to make room.

        Must be called while holding the store serialization lock (inside ``_serialized()``).
        """
        if not self._entries:
            return

        lowest_key = min(self._entries, key=lambda k: self._entries[k].confidence)
        self._evict_entry_key(lowest_key, reason="max_entries")

    def _resolve_scope(self, key: str, scope: str, branch: str) -> MemoryEntry | None:
        """Return the entry when its scope is at least as specific as *scope*.

        ``_entries`` holds at most one entry per key, so there is nothing to
        rank between — the old probe loop over [session, branch, project]
        also silently hid ``ephemeral``/``shared``-scoped entries from scoped
        ``get()`` because those scopes were never probed.

        Must be called while holding the store serialization lock (inside
        ``_serialized()``).
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.scope != scope and _scope_rank(entry.scope) < _scope_rank(MemoryScope(scope)):
            return None
        if entry.scope == MemoryScope.branch and entry.branch != branch:
            return None
        return entry


def _scope_rank(scope: MemoryScope) -> int:
    """Return numeric rank for scope precedence (higher = more specific)."""
    return {
        MemoryScope.project: 0,
        MemoryScope.branch: 1,
        MemoryScope.session: 2,
    }.get(scope, 0)
