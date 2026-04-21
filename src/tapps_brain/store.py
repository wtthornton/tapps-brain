"""In-memory cache backed by Postgres for the shared memory subsystem.

Provides fast reads from an in-memory dict with write-through to the
``PostgresPrivateBackend`` (ADR-007 — Postgres-only persistence plane).
RAG safety checks on save prevent prompt injection in stored content.
Auto-consolidation triggers on save when enabled (EPIC-058).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

from tapps_brain.agent_scope import agent_scope_valid_values_for_errors, normalize_agent_scope
from tapps_brain.memory_group import MEMORY_GROUP_UNSET, normalize_memory_group
from tapps_brain.models import (
    MemoryEntry,
    MemoryScope,
    MemorySnapshot,
    MemorySource,
    MemoryTier,
    _utc_now_iso,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tapps_brain._protocols import HiveBackend, PrivateBackend
    from tapps_brain.auto_consolidation import ConsolidationUndoResult
    from tapps_brain.embeddings import SentenceTransformerProvider
    from tapps_brain.feedback import FeedbackEvent, FeedbackStore, InMemoryFeedbackStore
    from tapps_brain.write_policy import DeterministicWritePolicy, LLMWritePolicy

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
    GEN_AI_OPERATION_EXECUTE_TOOL,
    SPAN_DELETE,
    SPAN_HIVE_PROPAGATE,
    SPAN_HIVE_SEARCH,
    SPAN_RECALL,
    SPAN_REINFORCE,
    SPAN_REMEMBER,
    SPAN_SEARCH,
    record_retrieval_document_events,
    rm_add_recall_latency_ms,
    rm_increment_recall_total,
    start_span,
)
from tapps_brain.rate_limiter import RateLimiterConfig, SlidingWindowRateLimiter
from tapps_brain.relations import RelationEntry, extract_relations
from tapps_brain.safety import check_content_safety
from tapps_brain.tier_normalize import normalize_save_tier

logger = structlog.get_logger(__name__)

# Maximum number of memories per project.  TAP-513 — operators can override
# this via the TAPPS_BRAIN_MAX_ENTRIES env var without code changes;
# YAML profile (``limits.max_entries``) still wins when set.  Precedence:
# YAML > env > default.
_MAX_ENTRIES_DEFAULT = 5000


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


_UNSET_EMBEDDING: Any = object()  # sentinel — distinguishes "not passed" from explicit None


class MemoryStore:
    """In-memory cache with SQLite write-through persistence.

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
        self._rate_limiter = SlidingWindowRateLimiter(rate_limiter_config)
        # Profile before persistence so lexical FTS/BM25 settings apply at open.
        self._profile = self._resolve_profile(project_root, profile)
        _lexical = getattr(self._profile, "lexical", None) if self._profile is not None else None

        # STORY-066.8: Auto-migrate private schema on startup when
        # TAPPS_BRAIN_AUTO_MIGRATE=1.  Runs before the backend is constructed
        # so the schema is up-to-date before the first connection.
        _auto_migrate_dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
        if _auto_migrate_dsn and _auto_migrate_dsn.startswith(("postgres://", "postgresql://")):
            from tapps_brain.postgres_migrations import maybe_auto_migrate_private

            maybe_auto_migrate_private(_auto_migrate_dsn)

        # ADR-007: Postgres-only persistence plane. A PrivateBackend is required.
        # When the caller does not pass one, resolve it from
        # TAPPS_BRAIN_DATABASE_URL via backends.resolve_private_backend_from_env.
        # If the env var is also missing we hard-fail — there is no SQLite fallback.
        if private_backend is None:
            from tapps_brain.backends import (
                derive_project_id,
                resolve_private_backend_from_env,
            )

            _resolved_agent_id = agent_id or "default"
            # EPIC-069: honor TAPPS_BRAIN_PROJECT (human-readable slug) before
            # falling back to the legacy path-hash.  The env var is how MCP
            # clients connecting over stdio declare project identity — see
            # ADR-010 and project_resolver.resolve_project_id.
            _env_project = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
            if _env_project:
                from tapps_brain.project_resolver import validate_project_id

                _project_id = validate_project_id(_env_project)
            else:
                _project_id = derive_project_id(project_root)
            private_backend = resolve_private_backend_from_env(_project_id, _resolved_agent_id)
            if private_backend is None:
                msg = (
                    "MemoryStore requires a Postgres private_backend (ADR-007). "
                    "Set TAPPS_BRAIN_DATABASE_URL to a postgres:// or postgresql:// "
                    "DSN, or pass an explicit private_backend constructed via "
                    "tapps_brain.backends.create_private_backend(dsn, ...). "
                    "SQLite is no longer supported."
                )
                raise ValueError(msg)
        # store_dir / encryption_key / lexical_config are legacy SQLite knobs —
        # kept in the signature for API compatibility but ignored on Postgres.
        _ = (store_dir, encryption_key, _lexical)
        self._persistence: PrivateBackend = private_backend
        # STORY-069.7: stash resolved project_id so instance methods can bind
        # it into structured logs without reaching into the backend each time.
        # Falls back to None for backends (e.g. InMemoryPrivateBackend) that
        # don't carry a project_id.
        self._project_id: str | None = getattr(private_backend, "_project_id", None)
        self._lock = threading.Lock()
        if lock_timeout_seconds is not None:
            self._lock_timeout_sec = (
                float(lock_timeout_seconds) if lock_timeout_seconds > 0 else None
            )
        else:
            self._lock_timeout_sec = _env_lock_timeout_seconds()
        if consolidation_config is not None:
            self._consolidation_config = consolidation_config
        elif self._profile is not None and hasattr(self._profile, "consolidation"):
            _pc = self._profile.consolidation
            self._consolidation_config = ConsolidationConfig(
                enabled=_pc.enabled,
                threshold=_pc.threshold,
                min_entries=_pc.min_entries,
            )
        else:
            self._consolidation_config = ConsolidationConfig()
        if embedding_provider is _UNSET_EMBEDDING:
            from tapps_brain.embeddings import get_embedding_provider

            self._embedding_provider = get_embedding_provider()
        else:
            self._embedding_provider = embedding_provider
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
        from tapps_brain.gc import GCConfig as _GCConfig

        self._gc_config = _GCConfig()
        self._metrics = MetricsCollector()
        self._hive_store = hive_store
        self._hive_agent_id = hive_agent_id

        # Cold-start: load all entries into memory.
        # Pass the effective max-entries cap so backends that support early-cutoff
        # (e.g. PostgresPrivateBackend with ORDER BY updated_at DESC) can stop
        # streaming once we have the most-recent entries up to the limit.
        self._entries: dict[str, MemoryEntry] = {}
        for entry in self._persistence.load_all(limit=self._max_entries):
            self._entries[entry.key] = entry

        # TAP-655: startup sanity check — warn if expected HNSW index is absent.
        _verify = getattr(self._persistence, "verify_expected_indexes", None)
        if callable(_verify):
            _verify()

        # Bloom filter for write-path deduplication (GitHub #31)
        self._bloom = BloomFilter()
        for _entry in self._entries.values():
            self._bloom.add(normalize_for_dedup(_entry.value))

        # Entity index for graph centrality scoring (TAP-734).
        # Maps BM25 token → set of entry keys that contain it.
        # Derived state only — never persisted; rebuilt from _entries at startup.
        self._entity_index: dict[str, set[str]] = {}
        for _entry in self._entries.values():
            self._index_entry_entities(_entry.key, _entry.value)

        # Cold-start: load all relations into memory, indexed by entry key
        self._relations: dict[str, list[dict[str, Any]]] = {}
        all_relations = self._persistence.list_relations()
        for rel in all_relations:
            for src_key in rel["source_entry_keys"]:
                self._relations.setdefault(src_key, []).append(rel)

        # EPIC-029: Lazy-initialized feedback store.
        self._feedback_store_instance: FeedbackStore | InMemoryFeedbackStore | None = None

        # EPIC-029 story 029.3: In-memory session tracking for implicit feedback.
        # Maps session_id → list of (entry_key, monotonic_time) for recalled entries.
        # Maps session_id → set of entry_keys that were reinforced in the session.
        # All access must run under ``_serialized()`` (same underlying lock).
        self._session_recall_log: dict[str, list[tuple[str, float]]] = {}
        self._session_reinforced: dict[str, set[str]] = {}

        # EPIC-029 story 029-4b: In-memory tracking for reformulation + correction.
        # _session_query_log: session_id → list of (query_text, recalled_keys, mono_time)
        #   Used to detect when a new query is a reformulation of a recent one.
        # _session_recalled_values: session_id → list of (entry_key, entry_value, mono_time)
        #   Used to detect when a save() corrects a recently recalled entry.
        self._session_query_log: dict[str, list[tuple[str, list[str], float]]] = {}
        self._session_recalled_values: dict[str, list[tuple[str, str, float]]] = {}

        # EPIC-029 story 029-7: session → hive memory key → namespace for feedback propagation.
        self._hive_feedback_key_index: dict[str, dict[str, str]] = {}

        # EPIC-030: diagnostics circuit breaker + history (lazy SQLite).
        from tapps_brain.diagnostics import AnomalyDetector, CircuitBreaker

        self._circuit_breaker = CircuitBreaker()
        self._anomaly_detector = AnomalyDetector()
        self._diagnostics_history_store: Any = None
        self._hive_recall_weight_multiplier: float = 1.0

        # EPIC-031: weak gap signals from empty recall (bounded in-memory buffer).
        self._zero_result_queries: deque[tuple[str, str]] = deque(maxlen=2000)
        self._latest_quality_report: dict[str, Any] | None = None

        # STORY-032.6: Last-known candidate counts for tapps_brain.* gauges.
        # Updated when health() or gc() is called; stale between runs — that is fine
        # because computing them requires a full-entry scan.
        self._last_consolidation_candidates: int = 0
        self._last_gc_candidates: int = 0

        # Auto-register agent in Hive registry (STORY-053.3)
        if auto_register and self._agent_id is not None and self._hive_store is not None:
            self._auto_register_agent()

        # Auto-join declared groups (STORY-056.1)
        if self._groups and self._hive_store is not None:
            self._setup_group_memberships()

        logger.info(
            "memory_store_initialized",
            project_root=str(project_root),
            entry_count=len(self._entries),
            relation_count=len(all_relations),
            auto_consolidation=self._consolidation_config.enabled,
        )

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
        """Auto-create and join declared groups in the Hive (STORY-056.1)."""
        if self._hive_store is None or not self._agent_id:
            return
        for group_name in self._groups:
            try:
                self._hive_store.create_group(group_name)
                self._hive_store.add_group_member(group_name, self._agent_id)
            except Exception:
                logger.warning(
                    "group_auto_join_failed",
                    group_name=group_name,
                    agent_id=self._agent_id,
                    exc_info=True,
                )

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
    def agent_id(self) -> str | None:
        """Return the agent identity used for storage isolation, or ``None``."""
        return self._agent_id

    @property
    def groups(self) -> list[str]:
        """Return declared group memberships (EPIC-056)."""
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
        if not project_id or not dsn.startswith(("postgres://", "postgresql://")):
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
        """Instantiate the best available LLM judge (lazy, no hard dependency)."""
        try:
            from tapps_brain.evaluation import AnthropicJudge

            return AnthropicJudge(model=model)
        except ImportError:
            pass  # anthropic optional dependency not installed
        except Exception:
            logger.warning("anthropic_judge_init_failed", exc_info=True)
        try:
            from tapps_brain.evaluation import OpenAIJudge

            return OpenAIJudge()
        except ImportError:
            pass  # openai optional dependency not installed
        except Exception:
            logger.warning("openai_judge_init_failed", exc_info=True)
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

    def save(  # noqa: PLR0911
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

        Args:
            key: Unique identifier for the memory.
            value: Memory content.
            tier: Memory tier (architectural, pattern, context).
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
        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            return {
                "error": "invalid_agent_scope",
                "message": str(exc),
                "valid_values": agent_scope_valid_values_for_errors(),
            }

        # STORY-056.3: Validate group membership for group-scoped saves
        if agent_scope.startswith("group:") and agent_scope != "group":
            group_name = agent_scope[6:]
            if group_name not in self._groups:
                return {
                    "error": "invalid_agent_scope",
                    "message": f"Agent not a member of group '{group_name}'",
                }

        # Auto-fill source_agent from store identity (STORY-053.2)
        if source_agent == "unknown" and self._agent_id is not None:
            source_agent = self._agent_id

        tier = normalize_save_tier(tier, self._profile)

        _mg_explicit: str | None | object = MEMORY_GROUP_UNSET
        if memory_group is not MEMORY_GROUP_UNSET:
            if memory_group is None:
                _mg_explicit = None
            else:
                try:
                    _mg_explicit = normalize_memory_group(str(memory_group))
                except ValueError as exc:
                    return {"error": "invalid_memory_group", "message": str(exc)}

        # Write rules validation (Epic 65.17)
        wr_error = _validate_write_rules(key, value, self._write_rules)
        if wr_error is not None:
            return {
                "error": "write_rules_violation",
                "message": wr_error,
            }

        # Rate limit check (H6a) — warn-only, never blocks.
        # Exemption is read from the batch_exempt_scope() contextvar only.
        rate_result = self._rate_limiter.check()
        if rate_result.minute_exceeded or rate_result.lifetime_exceeded:
            logger.warning(
                "memory_save_rate_warning",
                key=key,
                minute_count=rate_result.current_minute_count,
                lifetime_count=rate_result.current_lifetime_count,
            )

        # RAG safety check on value (ruleset + metrics: EPIC-044 STORY-044.1)
        _rs_ver: str | None = None
        if self._profile is not None:
            _safety_cfg = getattr(self._profile, "safety", None)
            if _safety_cfg is not None:
                _rs_ver = getattr(_safety_cfg, "ruleset_version", None)
        safety = check_content_safety(
            value,
            ruleset_version=_rs_ver,
            metrics=self._metrics,
        )
        if not safety.safe:
            logger.warning(
                "memory_save_blocked",
                key=key,
                match_count=safety.match_count,
                patterns=safety.flagged_patterns,
                ruleset_version=safety.ruleset_version,
            )
            self._metrics.increment("store.save.errors")
            self._metrics.increment("store.save.errors.content_blocked")
            return {
                "error": "content_blocked",
                "message": "Memory value blocked by RAG safety filter.",
                "flagged_patterns": safety.flagged_patterns,
            }

        if safety.sanitised_content is not None:
            value = safety.sanitised_content

        # Write-path policy decision (TAP-560/STORY-SC04).
        # Called after safety check so the policy always receives sanitised content.
        # DeterministicWritePolicy returns ADD unconditionally (zero overhead).
        # LLMWritePolicy consults an LLM and may return NOOP, DELETE, or UPDATE.
        if self._write_policy is not None:
            from tapps_brain.write_policy import WriteDecision

            with self._serialized():
                _wp_candidates = list(self._entries.values())
            _wp_result = self._write_policy.decide(key, value, _wp_candidates)
            if _wp_result.decision == WriteDecision.NOOP:
                # The policy determined this entry is already captured.
                self._metrics.increment("store.save.write_policy.noop")
                logger.info(
                    "memory_save_write_policy_noop",
                    key=key,
                    reasoning=_wp_result.reasoning,
                )
                with self._serialized():
                    _existing_noop = self._entries.get(key)
                if _existing_noop is not None:
                    return _existing_noop
                return {"write_policy": "noop", "key": key, "reasoning": _wp_result.reasoning}
            elif _wp_result.decision == WriteDecision.DELETE and _wp_result.target_key:
                # The policy wants to remove an existing entry and discard the new one.
                self._metrics.increment("store.save.write_policy.delete")
                logger.info(
                    "memory_save_write_policy_delete",
                    key=key,
                    target_key=_wp_result.target_key,
                    reasoning=_wp_result.reasoning,
                )
                self.delete(_wp_result.target_key)
                return {
                    "write_policy": "delete",
                    "deleted_key": _wp_result.target_key,
                    "reasoning": _wp_result.reasoning,
                }
            # WriteDecision.ADD and WriteDecision.UPDATE fall through to the
            # standard save path.  For UPDATE with a different target_key, the
            # caller may redirect; for now we save the incoming entry as-is.
            if _wp_result.decision != WriteDecision.ADD:
                logger.debug(
                    "memory_save_write_policy_passthrough",
                    key=key,
                    decision=_wp_result.decision.value,
                )

        # Bloom filter dedup fast-path (GitHub #31)
        if dedup:
            normalized = normalize_for_dedup(value)
            if self._bloom.might_contain(normalized):
                _dup_key: str | None = None
                with self._serialized():
                    for _existing in self._entries.values():
                        if normalize_for_dedup(_existing.value) == normalized:
                            _dup_key = _existing.key
                            break
                if _dup_key is not None:
                    logger.debug(
                        "memory_dedup_bloom_hit",
                        key=key,
                        existing_key=_dup_key,
                    )
                    self._metrics.increment("store.save.dedup_skip")
                    try:
                        return self.reinforce(_dup_key)
                    except KeyError:
                        pass  # Entry was deleted between check and reinforce; proceed with save
            self._bloom.add(normalized)

        # Conflict detection (GitHub #44, task 040.16) — opt-in only.
        _conflict_valid_at: str | None = None
        if conflict_check:
            from tapps_brain.contradictions import (
                detect_save_conflicts,
                format_save_conflict_reason,
            )

            with self._serialized():
                _all_entries = list(self._entries.values())
            _cc = (
                getattr(self._profile, "conflict_check", None)
                if self._profile is not None
                else None
            )
            if _cc is not None:
                _sim_threshold = _cc.effective_similarity_threshold()
            else:
                from tapps_brain.profile import ConflictCheckConfig

                _sim_threshold = ConflictCheckConfig().effective_similarity_threshold()
            _conflicts = detect_save_conflicts(
                value,
                tier,
                _all_entries,
                _sim_threshold,
                exclude_key=key,
            )
            if _conflicts:
                _conflict_keys = [h.entry.key for h in _conflicts]
                _tier_display = tier
                _conflict_audit = [
                    {
                        "key": h.entry.key,
                        "similarity": round(h.similarity, 4),
                        "tier": (
                            h.entry.tier.value
                            if hasattr(h.entry.tier, "value")
                            else str(h.entry.tier)
                        ),
                    }
                    for h in _conflicts
                ]
                logger.warning(
                    "memory_save_conflicts_detected",
                    key=key,
                    conflicting_keys=_conflict_keys,
                    similarity_threshold=_sim_threshold,
                    conflicts=_conflict_audit,
                )
                # Mark conflicting entries as superseded (set invalid_at = now)
                _now_conflict = _utc_now_iso()
                _conflict_valid_at = _now_conflict
                for _hit in _conflicts:
                    _conflict_entry = _hit.entry
                    if _conflict_entry.invalid_at is None:
                        _invalidated: MemoryEntry | None = None
                        _reason = format_save_conflict_reason(
                            incoming_key=key,
                            tier=_tier_display,
                            similarity=_hit.similarity,
                        )
                        with self._serialized():
                            _current = self._entries.get(_conflict_entry.key)
                            if _current is not None and _current.invalid_at is None:
                                _invalidated = _current.model_copy(
                                    update={
                                        "invalid_at": _now_conflict,
                                        "updated_at": _now_conflict,
                                        "contradicted": True,
                                        "contradiction_reason": _reason,
                                    }
                                )
                                self._entries[_conflict_entry.key] = _invalidated
                        if _invalidated is not None:
                            try:
                                self._persistence.save(_invalidated)
                            except Exception:
                                logger.warning(
                                    "conflict_invalidate_persist_failed",
                                    conflict_key=_conflict_entry.key,
                                    exc_info=True,
                                )

        self._metrics.increment("store.save")
        with (
            start_span(
                SPAN_REMEMBER,
                {
                    "memory.tier": tier,
                    "memory.scope": scope,
                    "memory.agent_scope": agent_scope,
                    "gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL,
                },
            ),
            MetricsTimer(self._metrics, "store.save_ms"),
        ):
            now = _utc_now_iso()
            with (
                MetricsTimer(self._metrics, "store.save.phase.lock_build_ms"),
                self._serialized(),
            ):
                existing = self._entries.get(key)

                if memory_group is MEMORY_GROUP_UNSET:
                    mg_for_entry: str | None = (
                        existing.memory_group if existing is not None else None
                    )
                else:
                    mg_for_entry = cast("str | None", _mg_explicit)

                # EPIC-010: Accept profile layer names as tier values.
                # Try MemoryTier enum first; if it fails, accept the raw
                # string when the active profile defines a layer with that name.
                try:
                    tier_val: MemoryTier | str = MemoryTier(tier)
                except ValueError:
                    if self._profile is not None and tier in self._profile.layer_names:
                        tier_val = tier
                    else:
                        tier_val = MemoryTier(tier)  # Raise original error

                entry = MemoryEntry(
                    key=key,
                    value=value,
                    tier=tier_val,
                    confidence=confidence,
                    source=MemorySource(source),
                    source_agent=source_agent,
                    scope=MemoryScope(scope),
                    agent_scope=agent_scope,
                    tags=tags or [],
                    created_at=existing.created_at if existing else now,
                    updated_at=now,
                    last_accessed=now,
                    access_count=existing.access_count if existing else 1,
                    branch=branch,
                    # Preserve reserved fields on update
                    last_reinforced=existing.last_reinforced if existing else None,
                    reinforce_count=existing.reinforce_count if existing else 0,
                    contradicted=existing.contradicted if existing else False,
                    contradiction_reason=(existing.contradiction_reason if existing else None),
                    seeded_from=existing.seeded_from if existing else None,
                    # Preserve temporal fields on update (EPIC-004);
                    # override valid_at when conflicts were resolved (040.16)
                    valid_at=_conflict_valid_at
                    if _conflict_valid_at
                    else (existing.valid_at if existing else None),
                    invalid_at=existing.invalid_at if existing else None,
                    superseded_by=existing.superseded_by if existing else None,
                    # Provenance metadata (GitHub #38)
                    source_session_id=source_session_id,
                    source_channel=source_channel,
                    source_message_id=source_message_id,
                    triggered_by=triggered_by,
                    memory_group=mg_for_entry,
                    # TAP-735: per-entry decay velocity override.
                    # When the caller passes None (the default), the existing value is
                    # preserved on update.  Passing an explicit "high"/"medium"/"low"
                    # replaces the stored value.  The MCP tool uses "" as the absent
                    # sentinel and converts it to None before calling save(), so there
                    # is currently no public API path to clear an existing setting back
                    # to None — that can be added when needed via a dedicated clear param.
                    temporal_sensitivity=temporal_sensitivity
                    if temporal_sensitivity is not None
                    else (existing.temporal_sensitivity if existing else None),
                    failed_approaches=failed_approaches
                    if failed_approaches is not None
                    else (existing.failed_approaches if existing else []),
                )

                # Compute integrity hash (H4a) — always v2 (JSON encoding, TAP-710)
                from tapps_brain.integrity import (
                    INTEGRITY_HASH_VERSION as _HASH_V,
                    compute_integrity_hash as _compute_hash,
                )

                _tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                _source_str = (
                    entry.source.value if hasattr(entry.source, "value") else str(entry.source)
                )
                _hash = _compute_hash(entry.key, entry.value, _tier_str, _source_str)
                entry = entry.model_copy(
                    update={"integrity_hash": _hash, "integrity_hash_v": _HASH_V}
                )

                # Per-group then global max entry enforcement (EPIC-044 STORY-044.7)
                self._enforce_entry_caps_before_assign(
                    key=key,
                    new_group=entry.memory_group,
                    existing=existing,
                )

                self._entries[key] = entry

            # Compute embedding when semantic search is enabled (Epic 65.7)
            if self._embedding_provider is not None:
                with MetricsTimer(self._metrics, "store.save.phase.embed_ms"):
                    try:
                        emb = self._embedding_provider.embed(value)
                        _mid_raw = getattr(self._embedding_provider, "model_id", None)
                        _mid: str | None = (
                            _mid_raw.strip()
                            if isinstance(_mid_raw, str) and _mid_raw.strip()
                            else None
                        )
                        _embed_update: dict[str, object] = {
                            "embedding": emb,
                            "embedding_model_id": _mid,
                        }
                        entry = entry.model_copy(update=_embed_update)
                        with self._serialized():
                            # Re-read current entry to avoid overwriting concurrent
                            # updates (e.g. another save/update_fields in between).
                            current = self._entries.get(key)
                            if current is not None and current.key == entry.key:
                                entry = current.model_copy(update=_embed_update)
                            self._entries[key] = entry
                    except Exception:
                        logger.warning("embedding_compute_failed", key=key, exc_info=True)

            # Persist to Postgres — rollback in-memory cache on failure to
            # maintain write-through consistency.
            try:
                with MetricsTimer(self._metrics, "store.save.phase.persist_ms"):
                    self._persistence.save(entry)
            except Exception:
                with self._serialized():
                    if existing is not None:
                        self._entries[key] = existing
                    else:
                        self._entries.pop(key, None)
                    # TAP-644: The bloom filter was mutated before the persist
                    # attempt and does not support item removal.  Rebuild it
                    # from the now-rolled-back _entries so might_contain()
                    # returns accurate results.  This is O(N) but persist
                    # failures are exceptional.
                    if dedup:
                        self._bloom = BloomFilter()
                        for _e in self._entries.values():
                            self._bloom.add(normalize_for_dedup(_e.value))
                raise

            # Audit (best-effort — append_audit swallows its own exceptions).
            self._persistence.append_audit(
                action="save",
                key=key,
                extra={
                    "tier": str(entry.tier),
                    "value_len": len(entry.value),
                    "is_update": existing is not None,
                },
            )

            # Entity index update for graph centrality (TAP-734).
            # Remove old tokens first (for updates), then add new ones.
            if existing is not None:
                self._remove_entry_entities(key)
            self._index_entry_entities(key, entry.value)

            # Hive propagation (EPIC-011)
            if self._hive_store is not None:
                with MetricsTimer(self._metrics, "store.save.phase.hive_ms"):
                    self._propagate_to_hive(entry)

            # STORY-056.3: Group-scoped save routing
            if self._hive_store is not None and self._groups:
                _tier_str_056 = (
                    entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                )
                _source_str_056 = (
                    entry.source.value if hasattr(entry.source, "value") else str(entry.source)
                )
                if agent_scope == "group" and self._groups:
                    # Propagate to ALL declared groups
                    for _gn in self._groups:
                        try:
                            self._hive_store.save(
                                namespace=f"group:{_gn}",
                                key=entry.key,
                                value=entry.value,
                                tier=_tier_str_056,
                                confidence=entry.confidence,
                                source=_source_str_056,
                                source_agent=entry.source_agent,
                                tags=entry.tags,
                            )
                        except Exception:
                            logger.warning(
                                "group_save_propagation_failed",
                                group=_gn,
                                key=entry.key,
                                exc_info=True,
                            )
                elif agent_scope.startswith("group:"):
                    # Propagate to specific group (already validated above)
                    _target_group = agent_scope[6:]
                    try:
                        self._hive_store.save(
                            namespace=f"group:{_target_group}",
                            key=entry.key,
                            value=entry.value,
                            tier=_tier_str_056,
                            confidence=entry.confidence,
                            source=_source_str_056,
                            source_agent=entry.source_agent,
                            tags=entry.tags,
                        )
                    except Exception:
                        logger.warning(
                            "group_save_propagation_failed",
                            group=_target_group,
                            key=entry.key,
                            exc_info=True,
                        )

            # STORY-056.2: Expert domain auto-publishing
            if (
                auto_publish
                and self._expert_domains
                and self._hive_store is not None
                and tier in ("architectural", "pattern")
                and agent_scope == "private"
            ):
                _tier_str_exp = (
                    entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                )
                _source_str_exp = (
                    entry.source.value if hasattr(entry.source, "value") else str(entry.source)
                )
                expert_tags = [f"expert:{d}" for d in self._expert_domains]
                all_tags = list(entry.tags or []) + expert_tags
                try:
                    self._hive_store.save(
                        namespace="universal",
                        key=entry.key,
                        value=entry.value,
                        tier=_tier_str_exp,
                        confidence=entry.confidence,
                        source=_source_str_exp,
                        source_agent=entry.source_agent,
                        tags=all_tags,
                    )
                except Exception:
                    logger.warning(
                        "expert_auto_publish_failed",
                        key=entry.key,
                        exc_info=True,
                    )

            # Extract and persist relations (EPIC-006)
            with MetricsTimer(self._metrics, "store.save.phase.relations_ms"):
                relations = extract_relations(key, value)
                if relations:
                    from tapps_brain.relations import (
                        RelationEntry,
                        detect_relation_cycles,
                    )

                    # Warn on detected cycles (self-loops / direct reversals).
                    cycles = detect_relation_cycles(relations)
                    if cycles:
                        logger.warning(
                            "relations.cycles_detected",
                            entry_key=key,
                            cycle_count=len(cycles),
                            cycles=[
                                {"subject": s, "predicate": p, "object": o} for s, p, o in cycles
                            ],
                        )

                    # Cap total edges per key to MAX_EDGES_PER_KEY.
                    existing_count = len(self._relations.get(key, []))
                    budget = RelationEntry.MAX_EDGES_PER_KEY - existing_count
                    if budget <= 0:
                        logger.debug(
                            "relations.max_edges_reached",
                            entry_key=key,
                            limit=RelationEntry.MAX_EDGES_PER_KEY,
                        )
                    else:
                        relations_to_save = relations[:budget]
                        self._persistence.save_relations(key, relations_to_save)
                        # Reload from persistence to keep timestamps consistent
                        with self._serialized():
                            self._relations[key] = self._persistence.load_relations(key)

            # Auto-consolidation check (Epic 58)
            if (
                self._consolidation_config.enabled
                and not skip_consolidation
                and not self._consolidation_in_progress
            ):
                with MetricsTimer(self._metrics, "store.save.phase.consolidate_ms"):
                    self._maybe_consolidate(entry)

        # EPIC-029 story 029-4b: recall-then-store correction detection.
        # If this save follows a recent recall (within the feedback window) and the
        # saved value has > 40% token overlap with a recalled entry's value, it is
        # treated as a correction: emit implicit_correction (utility_score=-0.3) for
        # those recalled entries.
        if session_id is not None:
            _correction_targets: list[tuple[str, float]] = []
            _now_corr = time.monotonic()
            with self._serialized():
                _correction_targets = self._detect_correction(session_id, entry.value, _now_corr)
            for _ck, _overlap in _correction_targets:
                self._emit_implicit_feedback(
                    "implicit_correction",
                    _ck,
                    session_id,
                    -0.3,
                    details={"type": "correction", "token_overlap": round(_overlap, 4)},
                )

        return entry

    def _maybe_consolidate(self, entry: MemoryEntry) -> None:
        """Check if the saved entry should trigger consolidation.

        Runs consolidation in a non-reentrant manner to prevent infinite
        loops when consolidation saves new entries.
        """
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
            logger.warning("auto_consolidation_check_failed", exc_info=True)
        finally:
            self._consolidation_in_progress = False

    def _index_entry_entities(self, key: str, value: str) -> None:
        """Add *key* to the entity index for all BM25 tokens in *value* (TAP-734).

        Tokens shorter than 3 characters are excluded (post-stemming length).
        May be called with or without the store lock held; callers that need
        strict consistency should operate under ``_serialized()``.  Thread
        safety relies on CPython's GIL for dict mutations.
        """
        tokens = [t for t in _bm25_preprocess(value) if len(t) >= 3]
        for token in tokens:
            self._entity_index.setdefault(token, set()).add(key)

    def _remove_entry_entities(self, key: str) -> None:
        """Remove *key* from all entity index token sets (TAP-734).

        Empty token sets are pruned to keep memory bounded.
        """
        empty_tokens: list[str] = []
        for token, keys in self._entity_index.items():
            keys.discard(key)
            if not keys:
                empty_tokens.append(token)
        for token in empty_tokens:
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
                )
        except Exception:
            logger.warning("hive_propagation_failed", key=entry.key, exc_info=True)

    def get(
        self,
        key: str,
        scope: str | None = None,
        branch: str | None = None,
    ) -> MemoryEntry | None:
        """Retrieve a memory entry by key.

        When *scope* and *branch* are provided, applies scope resolution:
        session > branch > project (most specific wins).

        Updates ``last_accessed`` and ``access_count`` on read.
        """
        self._metrics.increment("store.get")
        with MetricsTimer(self._metrics, "store.get_ms"):
            with self._serialized():
                if scope is not None and branch is not None:
                    entry = self._resolve_scope(key, scope, branch)
                else:
                    entry = self._entries.get(key)

                if entry is None:
                    self._metrics.increment("store.get.miss")
                    return None

                # Update access metadata
                now = _utc_now_iso()
                updated = entry.model_copy(
                    update={
                        "last_accessed": now,
                        "access_count": entry.access_count + 1,
                    }
                )
                self._entries[updated.key] = updated

            self._metrics.increment("store.get.hit")
            # Persist access metadata — rollback on failure.
            try:
                self._persistence.save(updated)
            except Exception:
                with self._serialized():
                    self._entries[updated.key] = entry
                raise
            return updated

    def list_all(
        self,
        tier: str | None = None,
        scope: str | None = None,
        tags: list[str] | None = None,
        memory_group: str | None = None,
        include_superseded: bool = True,
    ) -> list[MemoryEntry]:
        """List entries with optional filters.

        Args:
            tier: Filter by tier.
            scope: Filter by scope.
            tags: Filter by tags.
            memory_group: When set, only entries in this project-local group
                (``None`` on entry means ungrouped; omit this arg to list all).
            include_superseded: When ``False``, exclude temporally invalid
                (superseded/expired) entries. Default ``True`` for backward
                compatibility.
        """
        with self._serialized():
            entries = list(self._entries.values())

        if tier is not None:
            entries = [e for e in entries if e.tier == tier]
        if scope is not None:
            entries = [e for e in entries if e.scope == scope]
        if memory_group is not None:
            entries = [e for e in entries if e.memory_group == memory_group]
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set.intersection(e.tags)]
        if not include_superseded:
            entries = [e for e in entries if e.is_temporally_valid()]

        return entries

    def list_memory_groups(self) -> list[str]:
        """Return sorted distinct project-local ``memory_group`` values (GitHub #49)."""
        with self._serialized():
            names = {e.memory_group for e in self._entries.values() if e.memory_group}
        return sorted(names)

    def delete(self, key: str) -> bool:
        """Delete a memory entry by key. Returns True if deleted."""
        with start_span(SPAN_DELETE, {"gen_ai.operation.name": GEN_AI_OPERATION_EXECUTE_TOOL}):
            with self._serialized():
                if key not in self._entries:
                    return False
                removed = self._entries.pop(key)

            # Persist deletion — rollback in-memory cache on failure to
            # maintain write-through consistency.
            try:
                self._persistence.delete(key)
            except Exception:
                with self._serialized():
                    self._entries[key] = removed
                raise

            # Remove from entity index (TAP-734).
            self._remove_entry_entities(key)

            # Audit (best-effort).
            self._persistence.append_audit(
                action="delete",
                key=key,
                extra={"tier": str(removed.tier)},
            )
            return True

    @staticmethod
    def _parse_relative_time(value: str) -> str:
        """Expand a relative time shorthand to an ISO-8601 UTC string.

        Accepts shorthands of the form ``Nd`` (days), ``Nw`` (weeks), or
        ``Nm`` (months, approximated as 30 days each).  Any other string is
        returned unchanged so that callers can pass ISO-8601 strings through
        transparently.

        Examples::

            "7d"  -> ISO string 7 days ago
            "2w"  -> ISO string 14 days ago
            "1m"  -> ISO string 30 days ago
            "2026-01-01T00:00:00Z" -> "2026-01-01T00:00:00Z" (passthrough)
        """
        import re
        from datetime import UTC, datetime, timedelta

        m = re.fullmatch(r"(\d+)([dwm])", value.strip())
        if m is None:
            return value
        n, unit = int(m.group(1)), m.group(2)
        days = n if unit == "d" else n * 7 if unit == "w" else n * 30
        return (datetime.now(UTC) - timedelta(days=days)).isoformat()

    def search(
        self,
        query: str,
        tags: list[str] | None = None,
        tier: str | None = None,
        scope: str | None = None,
        memory_group: str | None = None,
        as_of: str | None = None,
        include_historical: bool = False,
        since: str | None = None,
        until: str | None = None,
        time_field: str = "created_at",
        include_group_memories: bool = False,
        max_group_results: int = 20,
    ) -> list[MemoryEntry]:
        """Search via FTS5, with optional post-filters.

        Args:
            query: Search query string.
            tags: Filter by tags.
            tier: Filter by tier.
            scope: Filter by scope.
            memory_group: When set, restrict to this project-local group (GitHub #49).
            as_of: ISO-8601 timestamp for point-in-time temporal filtering.
                When set, only entries valid at that time are returned.
                When ``None`` (default), temporally invalid entries are excluded
                using the current time.
            include_historical: When True, include expired/superseded entries
                (GitHub #29, task 040.3). When False (default), entries whose
                ``invalid_at`` or ``valid_until`` is in the past are excluded.
            since: ISO-8601 UTC lower bound (inclusive) on *time_field* (Issue #70).
            until: ISO-8601 UTC upper bound (exclusive) on *time_field* (Issue #70).
            time_field: Column to filter on — ``created_at``, ``updated_at``,
                or ``last_accessed``. Defaults to ``created_at``.
            include_group_memories: When True and the store has declared groups,
                also search group namespaces in the Hive (STORY-056.5).
            max_group_results: Maximum results per group namespace (STORY-056.5).
        """
        self._metrics.increment("store.search")
        rm_increment_recall_total()
        _search_t0 = time.monotonic()
        with (
            start_span(SPAN_SEARCH) as _search_span,
            MetricsTimer(self._metrics, "store.search_ms"),
        ):
            # Expand relative shorthands ("7d", "2w", "1m") to ISO-8601 strings.
            if since is not None:
                since = self._parse_relative_time(since)
            if until is not None:
                until = self._parse_relative_time(until)
            results = self._persistence.search(
                query,
                memory_group=memory_group,
                since=since,
                until=until,
                time_field=time_field,
                as_of=as_of,
            )

            if tier is not None:
                results = [r for r in results if r.tier == tier]
            if scope is not None:
                results = [r for r in results if r.scope == scope]
            if memory_group is not None:
                results = [r for r in results if r.memory_group == memory_group]
            if tags:
                tag_set = set(tags)
                results = [r for r in results if tag_set.intersection(r.tags)]

            # Temporal filtering (EPIC-004 + GitHub #29)
            if not include_historical:
                results = [r for r in results if r.is_temporally_valid(as_of)]

            # STORY-056.5: Group-aware recall — search group namespaces in Hive
            if include_group_memories and self._groups and self._hive_store is not None:
                _seen_keys = {r.key for r in results}
                for _gn in self._groups:
                    try:
                        with start_span(
                            SPAN_HIVE_SEARCH,
                            {"hive.group": _gn, "hive.namespace": f"group:{_gn}"},
                        ):
                            group_results = self._hive_store.search(
                                query,
                                namespaces=[f"group:{_gn}"],
                                limit=max_group_results,
                            )
                        for _gr in group_results:
                            _gk = _gr.get("key", "")
                            if _gk and _gk not in _seen_keys:
                                _seen_keys.add(_gk)
                                # Convert hive dict to MemoryEntry for uniform return
                                try:
                                    _ge = MemoryEntry(
                                        key=_gk,
                                        value=_gr.get("value", ""),
                                        tier=_gr.get("tier", "pattern"),
                                        confidence=_gr.get("confidence", 0.5),
                                        source=_gr.get("source", "agent"),
                                        source_agent=_gr.get("source_agent", "unknown"),
                                        tags=_gr.get("tags", [])
                                        if isinstance(_gr.get("tags"), list)
                                        else [],
                                        agent_scope=f"group:{_gn}",
                                    )
                                    results.append(_ge)
                                except Exception:
                                    logger.warning(
                                        "group_search_entry_convert_failed",
                                        group=_gn,
                                        key=_gk,
                                        exc_info=True,
                                    )
                    except Exception:
                        logger.warning(
                            "group_search_failed",
                            group=_gn,
                            exc_info=True,
                        )

            self._metrics.increment("store.search.results", len(results))
            _search_elapsed_ms = (time.monotonic() - _search_t0) * 1000.0
            if _search_span is not None:
                _search_span.set_attribute("search.result_count", len(results))
                # STORY-070.12: standardised per-operation attributes
                _search_span.set_attribute(ATTR_ROWS_RETURNED, len(results))
                _search_span.set_attribute(ATTR_LATENCY_MS, _search_elapsed_ms)
            rm_add_recall_latency_ms(_search_elapsed_ms)
            return results

    def update_fields(self, key: str, **fields: Any) -> MemoryEntry | None:  # noqa: ANN401
        """Partial update of specific fields on an existing entry.

        Preserves immutable fields like ``created_at``. Used by Epic 24
        decay/contradiction/reinforcement systems.
        """
        with self._serialized():
            entry = self._entries.get(key)
            if entry is None:
                return None

            fields["updated_at"] = _utc_now_iso()
            updated = entry.model_copy(update=fields)
            self._entries[key] = updated

        # Persist — rollback in-memory cache on failure.
        try:
            self._persistence.save(updated)
        except Exception:
            with self._serialized():
                self._entries[key] = entry
            raise
        return updated

    def undo_consolidation_merge(self, consolidated_key: str) -> ConsolidationUndoResult:
        """Revert one auto-consolidation merge (EPIC-044 STORY-044.4).

        See :func:`tapps_brain.auto_consolidation.undo_consolidation_merge`.
        """
        from tapps_brain.auto_consolidation import undo_consolidation_merge as _undo_merge

        return _undo_merge(self, consolidated_key)

    def count(self) -> int:
        """Return the total number of memory entries."""
        with self._serialized():
            return len(self._entries)

    def snapshot(self) -> MemorySnapshot:
        """Return a serializable snapshot of the full memory state."""
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

    def knn_search(self, query_embedding: list[float], k: int) -> list[tuple[str, float]]:
        """Approximate-nearest-neighbour search via pgvector HNSW."""
        return self._persistence.knn_search(query_embedding, k)

    @property
    def vector_index_enabled(self) -> bool:
        """Always True under the Postgres backend (pgvector is a hard dependency)."""
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
            with self._serialized():
                entry = self._entries.get(key)
                if entry is None:
                    raise KeyError(key)

                updates = dict(_reinforce(entry, decay_cfg, confidence_boost=confidence_boost))
                # EPIC-042.8: FSRS-lite stability on explicit reinforce (was_useful=True),
                # using pre-reinforce timestamps for retrievability — same flag as record_access.
                if self._profile is not None:
                    tier_name = (
                        entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
                    )
                    layer = self._profile.get_layer(tier_name)
                    if layer is not None and layer.adaptive_stability:
                        try:
                            from tapps_brain.decay import update_stability

                            new_stab, new_diff = update_stability(entry, decay_cfg, True)
                            updates["stability"] = new_stab
                            updates["difficulty"] = new_diff
                        except Exception:
                            logger.warning(
                                "reinforce_stability_update_failed", key=key, exc_info=True
                            )

                updated = entry.model_copy(update=updates)
                self._entries[key] = updated

            # Persist reinforcement — rollback in-memory cache on failure to
            # maintain write-through consistency (matches get() / update_fields()).
            try:
                self._persistence.save(updated)
            except Exception:
                with self._serialized():
                    self._entries[key] = entry
                raise

            # EPIC-010: Check promotion after reinforcement
            final: MemoryEntry = updated
            if self._profile is not None:
                try:
                    from tapps_brain.promotion import PromotionEngine

                    engine = PromotionEngine(decay_cfg)
                    target_tier = engine.check_promotion(updated, self._profile)
                    if target_tier is not None:
                        old_tier = str(updated.tier)
                        promoted = updated.model_copy(
                            update={"tier": target_tier, "updated_at": _utc_now_iso()}
                        )
                        with self._serialized():
                            self._entries[key] = promoted
                        self._persistence.save(promoted)
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
                        logger.info(
                            "memory_promoted",
                            key=key,
                            from_tier=old_tier,
                            to_tier=target_tier,
                        )
                        final = promoted
                except Exception:
                    logger.warning("promotion_check_failed", key=key, exc_info=True)

            # EPIC-029 story 029.3: implicit positive feedback
            if session_id is not None:
                _should_emit = False
                with self._serialized():
                    _should_emit = self._check_and_mark_reinforced(session_id, key)
                if _should_emit:
                    self._emit_implicit_feedback("implicit_positive", key, session_id, 1.0)

            return final

    def record_access(self, key: str, was_useful: bool) -> None:
        """Record whether a retrieved memory was useful. Updates Bayesian confidence.

        Increments total_access_count always; increments useful_access_count when
        was_useful=True. Applies a Bayesian update to confidence:

            new_confidence = old_confidence * (useful + 1) / (total + 2)

        If adaptive_stability is enabled on the entry's tier, also calls
        update_stability() from decay.py.

        Args:
            key: The memory entry key.
            was_useful: Whether this retrieval was useful to the caller.
        """
        with self._serialized():
            entry = self._entries.get(key)
            if entry is None:
                return

            new_total = entry.total_access_count + 1
            new_useful = entry.useful_access_count + (1 if was_useful else 0)

            # Bayesian update: confidence * (useful + 1) / (total + 2)
            new_confidence = entry.confidence * (new_useful + 1) / (new_total + 2)
            # Clamp to [0.0, 1.0]
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
        try:
            self._persistence.save(updated)
        except Exception:
            with self._serialized():
                self._entries[key] = entry
            raise
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
            # Skip if already exists
            with self._serialized():
                if key in self._entries:
                    continue

            result = self.save(
                key=key,
                value=fact["value"],
                tier=fact["tier"],
                source=source,
                agent_scope=agent_scope,
            )
            if isinstance(result, MemoryEntry):
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
        """
        from tapps_brain.session_index import index_session as _index_session

        try:
            return _index_session(self._project_root, session_id, chunks)
        except Exception:
            logger.warning("session_index_failed", session_id=session_id, exc_info=True)
            return 0

    def search_sessions(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Search session index by query.

        Returns list of dicts with keys: session_id, chunk_index, content, created_at.
        """
        from tapps_brain.session_index import search_session_index

        try:
            return search_session_index(self._project_root, query, limit=limit)
        except Exception:
            logger.warning("session_search_failed", query=query, exc_info=True)
            return []

    def cleanup_sessions(self, *, ttl_days: int = 90) -> int:
        """Delete session chunks older than ttl_days.

        Returns:
            Count of deleted chunks.
        """
        try:
            from tapps_brain.session_index import delete_expired_sessions

            return delete_expired_sessions(self._project_root, ttl_days)
        except Exception:
            logger.warning("session_cleanup_failed", exc_info=True)
            return 0

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

        # Collect entries to validate
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

        report = asyncio.run(_run_validation())

        return report

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

        with self._serialized():
            old_entry = self._entries.get(old_key)
            if old_entry is None:
                raise KeyError(old_key)

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
        self._persistence.save(invalidated)

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

        new_entry = self.save(key=new_key, value=new_value, **new_kwargs)
        if isinstance(new_entry, dict):
            msg = f"Failed to create superseding entry: {new_entry.get('message', '')}"
            raise ValueError(msg)

        # Set valid_at on the new entry
        with self._serialized():
            updated_new = new_entry.model_copy(update={"valid_at": now})
            self._entries[new_key] = updated_new
        self._persistence.save(updated_new)

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
        with self._serialized():
            if key not in self._entries:
                raise KeyError(key)

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

            # Walk forward from root collecting the chain.
            # Track visited keys to guard against corrupted cyclic superseded_by chains.
            chain: list[MemoryEntry] = []
            chain_visited: set[str] = set()
            current: str | None = root
            while current is not None:
                if current in chain_visited:
                    logger.warning("history_cycle_detected", key=key, cycle_key=current)
                    break
                entry = self._entries.get(current)
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
        from tapps_brain.recall import RecallOrchestrator

        log = logger.bind(project_id=self._project_id, op="recall")
        log.debug("store.recall.begin")
        # EPIC-029 story 029.3: extract session_id before forwarding kwargs.
        _raw_sid = kwargs.pop("session_id", None)
        session_id: str | None = str(_raw_sid) if _raw_sid is not None else None

        self._metrics.increment("store.recall")
        rm_increment_recall_total()
        _recall_t0 = time.monotonic()
        with self._serialized():
            if not hasattr(self, "_recall_orchestrator"):
                # Wire Hive store and profile for hive-aware recall (EPIC-011)
                hive_weight = 0.8
                agent_profile = "repo-brain"
                if self._profile is not None:
                    hive_cfg = getattr(self._profile, "hive", None)
                    if hive_cfg is not None:
                        hive_weight = hive_cfg.recall_weight
                    agent_profile = getattr(self._profile, "name", "repo-brain")
                self._recall_orchestrator = RecallOrchestrator(
                    self,
                    decay_config=self._get_decay_config(),
                    hive_store=self._hive_store,
                    hive_recall_weight=hive_weight,
                    hive_agent_profile=agent_profile,
                    hive_agent_id=self._hive_agent_id,
                )

        _recall_t0 = time.monotonic()
        with (
            start_span(SPAN_RECALL) as _recall_span,
            MetricsTimer(self._metrics, "store.recall_ms"),
        ):
            result = self._recall_orchestrator.recall(message, **kwargs)
            if _recall_span is not None:
                _recall_span.set_attribute(
                    "recall.hive_count", getattr(result, "hive_memory_count", 0)
                )
                # STORY-032.3: add one structured event per retrieved document
                record_retrieval_document_events(_recall_span, getattr(result, "memories", []))
                # STORY-070.12: standardised per-operation attributes
                _recall_memories = getattr(result, "memories", [])
                _recall_span.set_attribute(ATTR_ROWS_RETURNED, len(_recall_memories))
                _recall_span.set_attribute(
                    ATTR_LATENCY_MS, (time.monotonic() - _recall_t0) * 1000.0
                )

        # EPIC-029 story 029.3 + 029-4b: implicit feedback tracking
        if session_id is not None:
            # Flush entries whose window has expired (lazy negative detection)
            _expired: list[str] = []
            with self._serialized():
                _expired = self._consume_expired_recalls(session_id)
            for _k in _expired:
                self._emit_implicit_feedback("implicit_negative", _k, session_id, -0.1)

            # Build list of recalled entry keys from this result
            _memories: list[Any] = getattr(result, "memories", [])
            _recalled_keys: list[str] = [
                str(m.get("key", "")) for m in _memories if isinstance(m, dict) and m.get("key")
            ]

            # EPIC-029 story 029-7: remember which keys came from Hive (per session).
            with self._serialized():
                _hive_idx = self._hive_feedback_key_index.setdefault(session_id, {})
                for _m in _memories:
                    if not isinstance(_m, dict):
                        continue
                    if str(_m.get("source", "")) != "hive":
                        continue
                    _hk = str(_m.get("key", ""))
                    if not _hk:
                        continue
                    _hive_idx[_hk] = str(_m.get("namespace", "universal"))

            # EPIC-029 story 029-4b: reformulation detection.
            # Compare the current query against recent queries in the session log.
            # If Jaccard similarity > 0.5 within 60s, emit implicit_correction for
            # the entry keys recalled by the similar past query.
            _reform_targets: list[tuple[str, float]] = []
            _now_track = time.monotonic()
            with self._serialized():
                _reform_targets = self._detect_reformulation(session_id, message, _now_track)
                # Update query log with current query + recalled keys (after detection
                # so we don't match the current query against itself)
                _q_log = self._session_query_log.setdefault(session_id, [])
                _q_log.append((message, list(_recalled_keys), _now_track))
                # TAP-645: cap per-session query log to prevent unbounded growth in
                # long-lived sessions.  Keep the most-recent entries (trim oldest).
                if len(_q_log) > _SESSION_LOG_PER_SESSION_CAP:
                    del _q_log[:-_SESSION_LOG_PER_SESSION_CAP]
                # Record recalled keys + values for positive/negative/correction tracking
                if _recalled_keys:
                    _r_log = self._session_recall_log.setdefault(session_id, [])
                    _val_log = self._session_recalled_values.setdefault(session_id, [])
                    for _k in _recalled_keys:
                        _r_log.append((_k, _now_track))
                        _entry_val = self._entries.get(_k)
                        if _entry_val is not None:
                            _val_log.append((_k, _entry_val.value, _now_track))
                    # TAP-645: cap recall log and values log per-session.
                    if len(_r_log) > _SESSION_LOG_PER_SESSION_CAP:
                        del _r_log[:-_SESSION_LOG_PER_SESSION_CAP]
                    if len(_val_log) > _SESSION_LOG_PER_SESSION_CAP:
                        del _val_log[:-_SESSION_LOG_PER_SESSION_CAP]

            for _k, _sim in _reform_targets:
                self._emit_implicit_feedback(
                    "implicit_correction",
                    _k,
                    session_id,
                    -0.5,
                    details={"type": "reformulation", "jaccard_similarity": round(_sim, 4)},
                )

        from tapps_brain.diagnostics import CircuitState

        qw: str | None = None
        st = self._circuit_breaker.state
        if st == CircuitState.DEGRADED:
            qw = "Memory quality degraded — results may be reduced in quality."
        elif st == CircuitState.OPEN:
            qw = "Memory quality critical — Hive recall limited until recovery."
        elif st == CircuitState.HALF_OPEN:
            qw = "Memory quality recovering — diagnostic probes in progress."
        if qw is not None:
            result = result.model_copy(update={"quality_warning": qw})

        if not getattr(result, "memory_count", 0) and message.strip():
            with self._serialized():
                self._zero_result_queries.append((message.strip(), _utc_now_iso()))

        rm_add_recall_latency_ms((time.monotonic() - _recall_t0) * 1000.0)
        return result

    def health(self) -> StoreHealthReport:
        """Return a structured health report for this store."""
        from datetime import UTC, datetime

        from tapps_brain.gc import MemoryGarbageCollector
        from tapps_brain.similarity import find_consolidation_groups

        with self._serialized():
            entries = list(self._entries.values())

        tier_counts: dict[str, int] = {}
        for entry in entries:
            tier_val = entry.tier.value if isinstance(entry.tier, MemoryTier) else str(entry.tier)
            tier_counts[tier_val] = tier_counts.get(tier_val, 0) + 1

        schema_ver = self._persistence.get_schema_version()

        oldest_age = 0.0
        now = datetime.now(tz=UTC)
        for entry in entries:
            try:
                raw = entry.created_at.replace("Z", "+00:00")
                created = datetime.fromisoformat(raw)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                days = (now - created).total_seconds() / 86400.0
                oldest_age = max(oldest_age, days)
            except (ValueError, TypeError, AttributeError):
                continue

        gc = MemoryGarbageCollector(
            config=self._get_decay_config(),
            gc_config=self._gc_config,
        )
        gc_candidates = gc.identify_candidates(entries)

        groups = find_consolidation_groups(
            entries,
            threshold=self._consolidation_config.threshold,
        )
        consolidation_candidates = sum(len(g) for g in groups)
        # Update tapps_brain.consolidation.candidates gauge (STORY-032.6).
        self._last_consolidation_candidates = consolidation_candidates

        # Federation config removed (STORY-059.2 — SQLite federation deleted).
        # Federation is now Postgres-only; project count not available from local config.
        federation_project_count = 0

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

        prof = getattr(self, "_profile", None)
        prof_name: str | None = getattr(prof, "name", None) if prof is not None else None
        seed_ver: str | None = None
        if prof is not None:
            _seed = getattr(prof, "seeding", None)
            if _seed is not None:
                seed_ver = getattr(_seed, "seed_version", None)

        from tapps_brain.safety import resolve_safety_ruleset_version

        _rs_pin: str | None = None
        if prof is not None:
            _sfc = getattr(prof, "safety", None)
            if _sfc is not None:
                _rs_pin = getattr(_sfc, "ruleset_version", None)
        eff_ruleset = resolve_safety_ruleset_version(_rs_pin)

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
        )

    def gc(self, *, dry_run: bool = False) -> Any:  # noqa: ANN401
        """Run garbage collection on the store.

        Archives stale rows to the ``gc_archive`` Postgres table (migration 006,
        STORY-066.3).  Counters: ``store.gc`` (invocations),
        ``store.gc.archived`` (rows), ``store.gc.archive_bytes`` (bytes written).

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
        with self._serialized():
            entries = list(self._entries.values())
        now = datetime.now(tz=UTC)
        candidates = gc_collector.identify_candidates(entries, now=now)
        # Update tapps_brain.gc.candidates gauge with the current candidate count
        # (STORY-032.6) — recorded once per gc() call so get_metrics() stays cheap.
        self._last_gc_candidates = len(candidates)
        details = gc_collector.stale_candidate_details(entries, now=now)
        reason_counts = aggregate_gc_reason_counts(details)
        candidate_keys = [c.key for c in candidates]
        now_iso = now.isoformat()
        est_bytes = archive_entries_jsonl_utf8_bytes(candidates, archived_at_iso=now_iso)

        if dry_run:
            return GCResult(
                archived_count=0,
                remaining_count=len(entries),
                archived_keys=candidate_keys,
                dry_run=True,
                reason_counts=reason_counts,
                estimated_archive_bytes=est_bytes,
            )

        # Archive to Postgres gc_archive table (STORY-066.3) and delete from store.
        # archive_entry() is best-effort — returns 0 on failure so we accumulate
        # only successfully written bytes.
        appended = 0
        for entry in candidates:
            appended += self._persistence.archive_entry(entry)
        for key in candidate_keys:
            self.delete(key)

        self._metrics.increment("store.gc.archived", len(candidate_keys))
        if appended:
            self._metrics.increment("store.gc.archive_bytes", appended)

        # Prune session index (FTS5) rows aligned with GC retention policy.
        session_chunks_deleted = self.cleanup_sessions(
            ttl_days=self._gc_config.session_index_ttl_days
        )
        if session_chunks_deleted:
            self._metrics.increment("store.gc.session_chunks_deleted", session_chunks_deleted)

        # TAP-549: sweep the in-memory session-state helper dicts so
        # ``session_id`` rotation by long-lived clients cannot slow-burn
        # OOM the adapter.  Runs unconditionally on live GC (dry_run was
        # returned earlier) because it only drops process-local state —
        # there's nothing to preview.
        self._sweep_stale_sessions()

        return GCResult(
            archived_count=len(candidate_keys),
            remaining_count=len(entries) - len(candidate_keys),
            archived_keys=candidate_keys,
            dry_run=False,
            reason_counts=reason_counts,
            archive_bytes=appended,
            session_chunks_deleted=session_chunks_deleted,
        )

    def list_gc_stale_details(self, *, now: Any = None) -> list[Any]:  # noqa: ANN401
        """Return GC stale candidates with reasons (GitHub #21)."""
        from datetime import UTC, datetime

        from tapps_brain.gc import MemoryGarbageCollector

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
            List of ``AuditEntry`` objects matching the filters.
        """
        from tapps_brain.audit import AuditReader

        reader = AuditReader(self._persistence)
        return reader.query(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
        )

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
        from tapps_brain.diagnostics import (
            CircuitState,
            DiagnosticsConfig,
            hive_recall_multiplier,
            maybe_remediate,
            run_diagnostics,
        )

        self._ensure_diagnostics_history()
        hist_rows: list[dict[str, Any]] = []
        if self._diagnostics_history_store is not None:
            hist_rows = self._diagnostics_history_store.history(limit=500)
        dcfg = DiagnosticsConfig()
        if self._profile is not None and getattr(self._profile, "diagnostics", None) is not None:
            dcfg = DiagnosticsConfig.model_validate(self._profile.diagnostics.model_dump())
        report = run_diagnostics(self, config=dcfg, history_for_correlation=hist_rows)
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
        report = report.model_copy(
            update={"anomalies": alerts, "circuit_state": st.value},
        )
        if record_history and self._diagnostics_history_store is not None:
            self._diagnostics_history_store.record(report, circuit_state=st.value)
            self._diagnostics_history_store.prune_older_than(dcfg.retention_days)
        try:
            self._persistence.append_audit(
                "diagnostics_record",
                "",
                {
                    "composite_score": report.composite_score,
                    "circuit_state": st.value,
                },
            )
        except Exception:
            logger.warning("diagnostics_audit_failed", exc_info=True)
        self._metrics.increment("store.diagnostics")
        return report

    def diagnostics_history(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent diagnostics snapshots from SQLite (EPIC-030).

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
    # Relations (EPIC-006)
    # ------------------------------------------------------------------

    def count_relations(self) -> int:
        """Return the total number of stored relation triples."""
        return self._persistence.count_relations()

    def save_relations(self, key: str, relations: list[RelationEntry]) -> None:
        """Persist *relations* for *key* and refresh the in-memory cache.

        Public wrapper for ``_persistence.save_relations`` (TAP-510) so
        callers (auto-consolidation, future graph rebuilders) don't have
        to reach into ``_persistence`` / ``_lock`` / ``_relations``
        directly.

        Accepts a list of :class:`~tapps_brain.relations.RelationEntry`
        — the same type produced by extraction and merging.  The
        in-memory cache is rebuilt from the persistence layer under
        ``_lock`` so concurrent readers see the old or new set, never a
        partial write.
        """
        self._persistence.save_relations(key, relations)
        with self._lock:
            self._relations[key] = self._persistence.load_relations(key)

    def load_relations(self, key: str) -> list[dict[str, Any]]:
        """Reload relations for *key* from persistence and refresh the cache.

        Public wrapper for ``_persistence.load_relations`` (TAP-510).
        Use after an external writer has mutated the underlying store and
        the in-memory cache may be stale.  Returns the freshly loaded
        list of relation dicts (same shape as :meth:`get_relations`).
        """
        loaded = self._persistence.load_relations(key)
        with self._lock:
            self._relations[key] = list(loaded)
        return list(loaded)

    def get_relations(self, key: str) -> list[dict[str, Any]]:
        """Return all relations associated with a memory entry key.

        Args:
            key: The memory entry key.

        Returns:
            List of relation dicts with subject, predicate, object_entity,
            source_entry_keys, confidence, and created_at.
        """
        return list(self._relations.get(key, []))

    def get_relations_batch(self, keys: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Return relations for multiple keys in one call (STORY-048.2).

        Args:
            keys: Memory entry keys to look up.

        Returns:
            Dict mapping each requested key to its list of relation dicts.
            Keys with no relations map to an empty list.
        """
        return {key: list(self._relations.get(key, [])) for key in keys}

    def find_related(
        self,
        key: str,
        *,
        max_hops: int = 2,
    ) -> list[tuple[str, int]]:
        """Find entries related to *key* via BFS traversal of the relation graph.

        Two entries are considered connected when they share an entity
        (subject or object_entity) in their extracted relations.

        Args:
            key: Starting entry key.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            List of ``(entry_key, hop_distance)`` tuples, ordered by hop
            distance (ascending) then key name.  The starting key is
            **not** included in the results.

        Raises:
            KeyError: If *key* does not exist in the store.
        """
        with self._serialized():
            if key not in self._entries:
                raise KeyError(key)

            # Build entity -> set[entry_key] index from all relations
            entity_to_keys: dict[str, set[str]] = {}
            for entry_key, rels in self._relations.items():
                for rel in rels:
                    for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                        entity_to_keys.setdefault(entity, set()).add(entry_key)

            # BFS
            visited: set[str] = {key}
            result: list[tuple[str, int]] = []
            frontier: set[str] = {key}

            for hop in range(1, max_hops + 1):
                next_frontier: set[str] = set()
                for current_key in frontier:
                    # Collect entities from current_key's relations
                    for rel in self._relations.get(current_key, []):
                        for entity in (rel["subject"].lower(), rel["object_entity"].lower()):
                            for neighbor_key in entity_to_keys.get(entity, set()):
                                if neighbor_key not in visited:
                                    visited.add(neighbor_key)
                                    result.append((neighbor_key, hop))
                                    next_frontier.add(neighbor_key)
                frontier = next_frontier

        # Sort by hop distance, then key name for determinism
        result.sort(key=lambda t: (t[1], t[0]))
        return result

    def query_relations(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        object_entity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Filter relations by subject, predicate, and/or object_entity.

        All filters use case-insensitive matching.  When multiple filters are
        provided they are combined with AND logic.  Passing no filters returns
        all relations.

        Args:
            subject: Filter by subject entity.
            predicate: Filter by predicate/relationship type.
            object_entity: Filter by object entity.

        Returns:
            List of matching relation dicts.
        """
        with self._serialized():
            matches: list[dict[str, Any]] = []
            for rels in self._relations.values():
                for rel in rels:
                    if subject is not None and rel["subject"].lower() != subject.lower():
                        continue
                    if predicate is not None and rel["predicate"].lower() != predicate.lower():
                        continue
                    if (
                        object_entity is not None
                        and rel["object_entity"].lower() != object_entity.lower()
                    ):
                        continue
                    matches.append(dict(rel))
            # Deduplicate by (subject, predicate, object_entity) triple
            seen: set[tuple[str, str, str]] = set()
            deduped: list[dict[str, Any]] = []
            for m in matches:
                triple = (m["subject"].lower(), m["predicate"].lower(), m["object_entity"].lower())
                if triple not in seen:
                    seen.add(triple)
                    deduped.append(m)
        return deduped

    # ------------------------------------------------------------------
    # Tag management (EPIC-015)
    # ------------------------------------------------------------------

    def list_tags(self) -> dict[str, int]:
        """Return all unique tags across all entries with their usage counts.

        Returns:
            Dict mapping tag → count of entries that carry that tag.
        """
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
            self._entries[key] = updated

        self._persistence.save(updated)
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

    # ------------------------------------------------------------------
    # Integrity verification (H4b)
    # ------------------------------------------------------------------

    def verify_integrity(self) -> dict[str, Any]:
        """Scan all entries and verify their HMAC integrity hashes.

        For each entry that has a stored ``integrity_hash``, recomputes the
        HMAC-SHA256 and checks for a match. Entries without a stored hash
        (pre-v8 or NULL) are reported separately.

        Returns:
            Dict with ``total``, ``verified``, ``tampered``, ``no_hash``,
            ``tampered_keys``, ``missing_hash_keys``, ``tampered_details``.
        """
        from tapps_brain.integrity import (
            compute_integrity_hash,
            compute_integrity_hash_v1,
            verify_integrity_hash,
        )

        self._metrics.increment("store.verify_integrity")

        with self._serialized():
            entries = list(self._entries.values())

        total = len(entries)
        verified = 0
        tampered: list[str] = []
        tampered_details: list[dict[str, str]] = []
        missing_hash_keys: list[str] = []

        for entry in entries:
            stored_hash = getattr(entry, "integrity_hash", None)
            if not stored_hash:
                missing_hash_keys.append(entry.key)
                continue

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)
            hash_version = getattr(entry, "integrity_hash_v", 1)

            # Use the version-appropriate verifier so legacy v1 rows don't
            # spuriously show as tampered when the process uses the v2 scheme.
            if hash_version == 1:
                # v1: legacy pipe-joined canonical form
                v1_expected = compute_integrity_hash_v1(
                    entry.key, entry.value, tier_str, source_str
                )
                import hmac as _hmac

                if _hmac.compare_digest(v1_expected, stored_hash):
                    verified += 1
                    continue
            else:
                if verify_integrity_hash(entry.key, entry.value, tier_str, source_str, stored_hash):
                    verified += 1
                    continue

            tampered.append(entry.key)
            expected = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
            tampered_details.append(
                {
                    "key": entry.key,
                    "stored_hash": stored_hash,
                    "expected_hash": expected,
                    "hash_version": str(hash_version),
                }
            )
            logger.warning(
                "integrity_verification_failed",
                key=entry.key,
                tier=tier_str,
                hash_version=hash_version,
            )

        return {
            "total": total,
            "verified": verified,
            "tampered": len(tampered),
            "no_hash": len(missing_hash_keys),
            "tampered_keys": tampered,
            "missing_hash_keys": missing_hash_keys,
            "tampered_details": tampered_details,
        }

    def rehash_integrity_v1(self) -> dict[str, int]:
        """Recompute integrity hashes for legacy v1 (pipe-joined) entries.

        Scans all in-memory entries whose ``integrity_hash_v == 1`` (written
        before TAP-710 was fixed), verifies each against the old v1 canonical
        form, and — if the stored hash is still valid — replaces it with a
        fresh v2 (JSON) hash.  Entries whose v1 hash no longer matches (i.e.
        already tampered) are left unchanged and counted in ``tampered``.
        Entries with no hash are skipped and counted in ``skipped_no_hash``.

        This method is the application-layer migration shim for upgrading from
        ``integrity_hash_v = 1`` to ``integrity_hash_v = 2``.  After running
        it, :meth:`verify_integrity` will validate all entries under the v2
        scheme.  The shim is safe to run multiple times — v2 entries are a
        no-op.

        Returns:
            Dict with ``upgraded``, ``tampered``, ``skipped_no_hash``,
            ``already_v2`` counts.
        """
        from tapps_brain.integrity import (
            INTEGRITY_HASH_VERSION as _HASH_V,
            compute_integrity_hash,
            compute_integrity_hash_v1,
        )

        import hmac as _hmac

        upgraded = 0
        tampered = 0
        skipped_no_hash = 0
        already_v2 = 0

        with self._serialized():
            keys = list(self._entries.keys())

        for key in keys:
            with self._serialized():
                entry = self._entries.get(key)
            if entry is None:
                continue

            stored_hash = getattr(entry, "integrity_hash", None)
            if not stored_hash:
                skipped_no_hash += 1
                continue

            hash_version = getattr(entry, "integrity_hash_v", 1)
            if hash_version >= 2:
                already_v2 += 1
                continue

            tier_str = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            source_str = entry.source.value if hasattr(entry.source, "value") else str(entry.source)

            # Verify that the stored v1 hash is still intact before upgrading.
            v1_expected = compute_integrity_hash_v1(entry.key, entry.value, tier_str, source_str)
            if not _hmac.compare_digest(v1_expected, stored_hash):
                tampered += 1
                logger.warning(
                    "rehash_integrity_v1.tampered_skipped",
                    key=key,
                    hint="v1 hash mismatch — entry may be tampered; not upgraded",
                )
                continue

            # v1 hash is intact — upgrade to v2.
            new_hash = compute_integrity_hash(entry.key, entry.value, tier_str, source_str)
            upgraded_entry = entry.model_copy(
                update={"integrity_hash": new_hash, "integrity_hash_v": _HASH_V}
            )
            with self._lock:
                self._entries[key] = upgraded_entry

            if self._hive_store is not None:
                try:
                    self._hive_store.save(upgraded_entry)
                except Exception:  # noqa: BLE001
                    pass

            if self._backend is not None:
                try:
                    self._backend.save(upgraded_entry)
                except Exception:  # noqa: BLE001
                    pass

            upgraded += 1
            logger.debug("rehash_integrity_v1.upgraded", key=key)

        logger.info(
            "rehash_integrity_v1.complete",
            upgraded=upgraded,
            tampered=tampered,
            skipped_no_hash=skipped_no_hash,
            already_v2=already_v2,
        )
        return {
            "upgraded": upgraded,
            "tampered": tampered,
            "skipped_no_hash": skipped_no_hash,
            "already_v2": already_v2,
        }

    # ------------------------------------------------------------------
    # Flywheel (EPIC-031)
    # ------------------------------------------------------------------

    def zero_result_gap_signals(self) -> list[tuple[str, str]]:
        """Return (query, timestamp) pairs for recalls that returned no memories."""
        with self._serialized():
            return list(self._zero_result_queries)

    def process_feedback(
        self,
        *,
        since: str | None = None,
        config: Any = None,  # noqa: ANN401
    ) -> dict[str, Any]:
        """Apply queued feedback events to entry confidence (Bayesian update)."""
        from tapps_brain.flywheel import FeedbackProcessor, FlywheelConfig

        cfg = config if config is not None else FlywheelConfig()
        return FeedbackProcessor(cfg).process_feedback(self, since=since)

    def knowledge_gaps(
        self,
        limit: int = 10,
        *,
        semantic: bool = False,
    ) -> list[Any]:
        """Ranked knowledge gaps (explicit reports + zero-result recall)."""
        from tapps_brain.flywheel import GapTracker

        gaps = GapTracker().analyze_gaps(self, use_semantic_clustering=semantic)
        return gaps[:limit]

    def generate_report(self, **kwargs: Any) -> Any:  # noqa: ANN401
        """Build markdown + structured quality report (flywheel)."""
        from tapps_brain.flywheel import generate_report as flywheel_generate_report

        qr = flywheel_generate_report(self, **kwargs)
        with self._serialized():
            self._latest_quality_report = qr.model_dump(mode="json")
        return qr

    def latest_quality_report(self) -> dict[str, Any] | None:
        """Last report from ``generate_report`` (None if never run)."""
        with self._serialized():
            return self._latest_quality_report

    # ------------------------------------------------------------------
    # Feedback API (EPIC-029)
    # ------------------------------------------------------------------

    def _get_feedback_store(self) -> FeedbackStore | InMemoryFeedbackStore:
        """Return the lazily-initialized feedback store.

        Returns a :class:`~tapps_brain.feedback.FeedbackStore` when the active
        backend is a :class:`~tapps_brain.postgres_private.PostgresPrivateBackend`
        with a connection manager.  Falls back to an
        :class:`~tapps_brain.feedback.InMemoryFeedbackStore` when the backend
        has no Postgres connection (e.g. the unit-test ``InMemoryPrivateBackend``).
        The in-memory store persists events for the lifetime of the
        :class:`MemoryStore` instance only — it is not durable.
        """
        if self._feedback_store_instance is None:
            from tapps_brain.feedback import FeedbackConfig, FeedbackStore, InMemoryFeedbackStore

            cm = getattr(self._persistence, "_cm", None)
            project_id = getattr(self._persistence, "_project_id", None)
            agent_id = getattr(self._persistence, "_agent_id", None)

            if cm is None or project_id is None or agent_id is None:
                # No Postgres connection — fall back to in-memory store.
                # Use backend._feedback_events if available so all MemoryStore
                # instances sharing the same InMemoryPrivateBackend (same
                # project_root in tests) see the same feedback data.
                config: FeedbackConfig | None = None
                if self._profile is not None:
                    config = getattr(self._profile, "feedback", None)
                shared = getattr(self._persistence, "_feedback_events", None)
                self._feedback_store_instance = InMemoryFeedbackStore(
                    config=config, shared_events=shared
                )
            else:
                config = None
                if self._profile is not None:
                    config = getattr(self._profile, "feedback", None)
                self._feedback_store_instance = FeedbackStore(
                    cm,
                    project_id=project_id,
                    agent_id=agent_id,
                    config=config,
                )
        return self._feedback_store_instance

    def _propagate_feedback_to_hive(self, event: FeedbackEvent, session_id: str | None) -> None:
        """Mirror feedback to the Hive when the entry was Hive-sourced (STORY-029.7).

        Resolves namespace from the per-session hive recall index, or from
        ``event.details[\"hive_namespace\"]`` when set explicitly.

        Failure-tolerant: Hive write errors are logged and do not affect local
        feedback persistence.
        """
        if self._hive_store is None:
            return
        ek = event.entry_key
        if not ek:
            return
        ns: str | None = None
        if session_id:
            with self._serialized():
                ns = self._hive_feedback_key_index.get(session_id, {}).get(ek)
        if ns is None:
            d = event.details if isinstance(event.details, dict) else {}
            hn = d.get("hive_namespace")
            if isinstance(hn, str) and hn.strip():
                ns = hn.strip()
        if ns is None:
            return
        try:
            details_out: dict[str, Any] = (
                dict(event.details) if isinstance(event.details, dict) else {}
            )
            self._hive_store.record_feedback_event(
                event_id=event.id,
                namespace=ns,
                entry_key=ek,
                event_type=event.event_type,
                session_id=event.session_id,
                utility_score=event.utility_score,
                details=details_out,
                timestamp=event.timestamp,
                source_project=str(self._project_root.resolve()),
            )
        except Exception:
            logger.warning(
                "hive_feedback_propagate_failed",
                entry_key=ek,
                namespace=ns,
                exc_info=True,
            )

    def rate_recall(
        self,
        entry_key: str,
        *,
        rating: str = "helpful",
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Record a user rating for a recalled memory entry.

        Convenience wrapper that creates a ``recall_rated`` feedback event.

        Args:
            entry_key: The memory entry key that was recalled.
            rating: Quality rating — ``"helpful"`` (1.0), ``"partial"`` (0.5),
                ``"irrelevant"`` (0.0), or ``"outdated"`` (0.0).
            session_id: Optional calling session identifier.
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.

        Raises:
            ValueError: If *rating* is not a recognised value.
        """
        from tapps_brain.feedback import FeedbackEvent

        _RATING_SCORES: dict[str, float] = {
            "helpful": 1.0,
            "partial": 0.5,
            "irrelevant": 0.0,
            "outdated": 0.0,
        }
        if rating not in _RATING_SCORES:
            raise ValueError(f"Unknown rating {rating!r}. Valid values: {sorted(_RATING_SCORES)}")

        log = logger.bind(project_id=self._project_id, op="feedback", event_type="recall_rated")
        log.debug("store.feedback.recall_rated")
        event = FeedbackEvent(
            event_type="recall_rated",
            entry_key=entry_key,
            session_id=session_id,
            utility_score=_RATING_SCORES[rating],
            details={"rating": rating, **(details or {})},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.recall_rated")
        self._propagate_feedback_to_hive(event, session_id)
        return event

    def report_gap(
        self,
        query: str,
        *,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Report a knowledge gap — a query that returned insufficient results.

        Creates a ``gap_reported`` feedback event.  The *query* string is
        stored in ``details["query"]`` for later clustering and analysis.

        Args:
            query: The query or topic that was not well served.
            session_id: Optional calling session identifier.
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.
        """
        from tapps_brain.feedback import FeedbackEvent

        log = logger.bind(project_id=self._project_id, op="feedback", event_type="gap_reported")
        log.debug("store.feedback.gap_reported")
        event = FeedbackEvent(
            event_type="gap_reported",
            session_id=session_id,
            details={"query": query, **(details or {})},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.gap_reported")
        return event

    def report_issue(
        self,
        entry_key: str,
        issue: str,
        *,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Flag a quality issue with a specific memory entry.

        Creates an ``issue_flagged`` feedback event.  The *issue* description
        is stored in ``details["issue"]``.

        Args:
            entry_key: The memory entry key that has the quality issue.
            issue: Human-readable description of the issue.
            session_id: Optional calling session identifier.
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.
        """
        from tapps_brain.feedback import FeedbackEvent

        log = logger.bind(project_id=self._project_id, op="feedback", event_type="issue_flagged")
        log.debug("store.feedback.issue_flagged")
        event = FeedbackEvent(
            event_type="issue_flagged",
            entry_key=entry_key,
            session_id=session_id,
            details={"issue": issue, **(details or {})},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.issue_flagged")
        self._propagate_feedback_to_hive(event, session_id)
        return event

    def record_feedback(
        self,
        event_type: str,
        *,
        entry_key: str | None = None,
        session_id: str | None = None,
        utility_score: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> FeedbackEvent:
        """Record a generic feedback event (built-in or custom event type).

        This is the low-level API that accepts any valid Object-Action
        snake_case ``event_type``.  Use the typed convenience methods
        (``rate_recall``, ``report_gap``, ``report_issue``) for standard
        events, and this method for custom event types registered via
        ``FeedbackConfig.custom_event_types``.

        Args:
            event_type: Object-Action snake_case event name (open enum).
            entry_key: Optional memory entry key this event relates to.
            session_id: Optional calling session identifier.
            utility_score: Numeric utility signal in [-1.0, 1.0].
            details: Optional additional metadata.

        Returns:
            The persisted ``FeedbackEvent``.

        Raises:
            ValueError: If *event_type* fails pattern validation, or if
                strict event types are enabled and the type is unknown.
        """
        from tapps_brain.feedback import FeedbackEvent

        log = logger.bind(project_id=self._project_id, op="feedback", event_type=event_type)
        log.debug("store.feedback.recorded")
        event = FeedbackEvent(
            event_type=event_type,
            entry_key=entry_key,
            session_id=session_id,
            utility_score=utility_score,
            details=details or {},
            project_id=self._project_id,
        )
        self._get_feedback_store().record(event)
        self._metrics.increment("store.feedback.recorded")
        self._propagate_feedback_to_hive(event, session_id)
        return event

    def query_feedback(
        self,
        *,
        event_type: str | None = None,
        entry_key: str | None = None,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[FeedbackEvent]:
        """Query recorded feedback events with optional filters.

        Convenience wrapper around ``FeedbackStore.query()``.

        Args:
            event_type: Filter by exact event type (or None for all).
            entry_key: Filter by related memory entry key.
            session_id: Filter by session identifier.
            since: ISO-8601 lower bound (inclusive) on timestamp.
            until: ISO-8601 upper bound (inclusive) on timestamp.
            limit: Maximum number of results (default 100).

        Returns:
            Matching ``FeedbackEvent`` objects ordered by timestamp ascending.
        """
        return self._get_feedback_store().query(
            event_type=event_type,
            entry_key=entry_key,
            session_id=session_id,
            since=since,
            until=until,
            limit=limit,
        )

    def close(self) -> None:
        """Close the underlying persistence layer."""
        if self._feedback_store_instance is not None:
            self._feedback_store_instance.close()
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
                for key in past_keys:
                    targets.append((key, sim))
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
        return sum(1 for e in self._entries.values() if e.memory_group == memory_group)

    def _evict_lowest_confidence_in_group(self, memory_group: str | None) -> None:
        """Evict lowest-confidence row within one ``memory_group`` bucket."""
        candidates = [k for k, e in self._entries.items() if e.memory_group == memory_group]
        if not candidates:
            return
        lowest_key = min(candidates, key=lambda k: self._entries[k].confidence)
        del self._entries[lowest_key]
        self._persistence.delete(lowest_key)
        logger.info(
            "memory_evicted",
            key=lowest_key,
            reason="max_entries_per_group",
            memory_group=memory_group,
        )

    def _evict_lowest_confidence_prefer_group(self, memory_group: str | None) -> None:
        """Global cap: prefer evicting from the same bucket as the incoming save."""
        if self._max_entries_per_group is not None:
            in_group = [k for k, e in self._entries.items() if e.memory_group == memory_group]
            if in_group:
                lowest_key = min(in_group, key=lambda k: self._entries[k].confidence)
                del self._entries[lowest_key]
                self._persistence.delete(lowest_key)
                logger.info(
                    "memory_evicted",
                    key=lowest_key,
                    reason="max_entries_fair",
                    memory_group=memory_group,
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
        del self._entries[lowest_key]
        self._persistence.delete(lowest_key)
        logger.info("memory_evicted", key=lowest_key, reason="max_entries")

    def _resolve_scope(self, key: str, scope: str, branch: str) -> MemoryEntry | None:
        """Resolve scope precedence: session > branch > project.

        Must be called while holding the store serialization lock (inside ``_serialized()``).
        """
        # Try most specific first
        for try_scope in [MemoryScope.session, MemoryScope.branch, MemoryScope.project]:
            if try_scope.value == scope or _scope_rank(try_scope) >= _scope_rank(
                MemoryScope(scope)
            ):
                for entry in self._entries.values():
                    if entry.key == key and entry.scope == try_scope:
                        if try_scope == MemoryScope.branch and entry.branch != branch:
                            continue
                        return entry
        return None


def _scope_rank(scope: MemoryScope) -> int:
    """Return numeric rank for scope precedence (higher = more specific)."""
    return {
        MemoryScope.project: 0,
        MemoryScope.branch: 1,
        MemoryScope.session: 2,
    }.get(scope, 0)
