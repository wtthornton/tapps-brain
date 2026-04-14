"""MCP server exposing tapps-brain via Model Context Protocol.

Uses FastMCP to expose MemoryStore operations as MCP tools, resources,
and prompts over stdio transport. Requires the ``mcp`` optional extra.

Key public API: :func:`create_server` returns a configured ``FastMCP``
instance; call ``mcp.run()`` to start the stdio server.

Entry point: ``tapps-brain-mcp`` (see pyproject.toml).

EPIC-070 STORY-070.1: tool bodies have been extracted to
``tapps_brain.services.*``. Each ``@mcp.tool()`` here is a thin wrapper
that resolves the per-call store, delegates to the service function, and
serialises the result to JSON.
"""

from __future__ import annotations

import argparse
import contextvars
import importlib.metadata
import json
import logging
import os
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

# STORY-070.4: transport-neutral contextvars set by the FastAPI tenant
# middleware (HTTP) or left unset for stdio (falls back to env/argv).
REQUEST_PROJECT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_project_id", default=None
)
REQUEST_AGENT_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tapps_brain_request_agent_id", default=None
)

import structlog

from tapps_brain.services import (
    agents_service,
    diagnostics_service,
    feedback_service,
    flywheel_service,
    hive_service,
    maintenance_service,
    memory_service,
    profile_service,
    relay_service,
)
from tapps_brain.services._common import parse_details_json as _mcp_parse_details_json  # noqa: F401

# Silence structlog for server mode — MCP uses stdin/stdout for protocol.
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

logger = structlog.get_logger(__name__)


def _lazy_import_mcp() -> Any:  # noqa: ANN401
    """Import ``mcp`` lazily so the module can be imported without the extra."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.stderr.write(
            "ERROR: The 'mcp' package is required for the MCP server.\n"
            "Install it with: uv sync --extra mcp  (or --extra all)\n"
        )
        sys.exit(1)
    return FastMCP


def _resolve_project_dir(project_dir: str | None) -> Path:
    """Resolve project directory, defaulting to cwd."""
    return Path(project_dir).resolve() if project_dir else Path.cwd().resolve()


def _get_store(
    project_dir: Path,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:  # noqa: ANN401
    """Open a MemoryStore for the given project directory.

    When *enable_hive* is ``True``, a Postgres :class:`HiveBackend` is
    wired in when ``TAPPS_BRAIN_HIVE_DSN`` is set (ADR-007 — no SQLite
    Hive).

    **Strict mode** (``TAPPS_BRAIN_STRICT=1``): When this env var is set,
    startup **fails immediately** if ``TAPPS_BRAIN_HIVE_DSN`` is not
    configured.  This prevents silent degradation in production where a
    missing DSN would quietly disable Hive tools.
    """
    from tapps_brain.backends import resolve_hive_backend_from_env
    from tapps_brain.store import MemoryStore

    strict = os.environ.get("TAPPS_BRAIN_STRICT", "") == "1"

    hive_store = None
    if enable_hive:
        hive_store = resolve_hive_backend_from_env()
        if strict and hive_store is None:
            raise RuntimeError(
                "TAPPS_BRAIN_STRICT=1 requires TAPPS_BRAIN_HIVE_DSN to be set (postgresql://...)"
            )

    agent_id_for_store = agent_id if agent_id != "unknown" else None
    return MemoryStore(
        project_dir,
        agent_id=agent_id_for_store,
        hive_store=hive_store,
        hive_agent_id=agent_id,
    )


# --------------------------------------------------------------------------
# STORY-069.3: Per-call project_id dispatch with a bounded LRU store cache.
# --------------------------------------------------------------------------

_DEFAULT_STORE_CACHE_SIZE = 16


class _StoreCache:
    """Bounded LRU cache of ``MemoryStore`` instances keyed by project_id."""

    def __init__(self, *, maxsize: int | None = None) -> None:
        if maxsize is None:
            try:
                maxsize = int(
                    os.environ.get("TAPPS_BRAIN_STORE_CACHE_SIZE", "")
                    or _DEFAULT_STORE_CACHE_SIZE
                )
            except ValueError:
                maxsize = _DEFAULT_STORE_CACHE_SIZE
        self._maxsize = max(1, maxsize)
        self._entries: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def get_or_create(self, project_id: str, factory: Any) -> Any:
        with self._lock:
            if project_id in self._entries:
                self._entries.move_to_end(project_id)
                return self._entries[project_id]

        store = factory()

        evicted: list[Any] = []
        with self._lock:
            existing = self._entries.get(project_id)
            if existing is not None:
                self._entries.move_to_end(project_id)
                evicted.append(store)
                result = existing
            else:
                self._entries[project_id] = store
                self._entries.move_to_end(project_id)
                while len(self._entries) > self._maxsize:
                    _, old = self._entries.popitem(last=False)
                    evicted.append(old)
                result = store

        for victim in evicted:
            _safe_close_store(victim)
        return result

    def clear(self) -> None:
        with self._lock:
            victims = list(self._entries.values())
            self._entries.clear()
        for victim in victims:
            _safe_close_store(victim)

    def __contains__(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def _safe_close_store(store: Any) -> None:
    close = getattr(store, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001
        logger.debug("store_cache.close_failed", exc_info=True)


_STORE_CACHE = _StoreCache()


def _resolve_project_dir_for_id(project_id: str) -> Path:
    return Path.cwd().resolve()


def _get_store_for_project(
    project_id: str | None,
    *,
    default_store: Any,
    enable_hive: bool = True,
    agent_id: str = "unknown",
) -> Any:  # noqa: ANN401
    if not project_id:
        return default_store
    default_pid = getattr(default_store, "_tapps_project_id", None)
    if default_pid and project_id == default_pid:
        return default_store

    def _factory() -> Any:
        prev = os.environ.get("TAPPS_BRAIN_PROJECT")
        os.environ["TAPPS_BRAIN_PROJECT"] = project_id
        try:
            return _get_store(
                _resolve_project_dir_for_id(project_id),
                enable_hive=enable_hive,
                agent_id=agent_id,
            )
        finally:
            if prev is None:
                os.environ.pop("TAPPS_BRAIN_PROJECT", None)
            else:
                os.environ["TAPPS_BRAIN_PROJECT"] = prev

    return _STORE_CACHE.get_or_create(project_id, _factory)


def _current_request_project_id() -> str | None:
    """Resolve the per-request project_id across transports.

    Precedence:
      1. ``REQUEST_PROJECT_ID`` contextvar (set by the FastAPI tenant
         middleware for Streamable HTTP — STORY-070.4).
      2. MCP JSON-RPC ``_meta.project_id`` on the active request context
         (legacy per-call override; also works over stdio).
      3. ``TAPPS_BRAIN_PROJECT`` environment variable (stdio transport
         set by the client's ``.mcp.json`` ``env``).
    """
    pid = REQUEST_PROJECT_ID.get()
    if pid:
        return str(pid).strip() or None
    try:
        from mcp.server.lowlevel.server import request_ctx
    except Exception:  # noqa: BLE001
        request_ctx = None  # type: ignore[assignment]
    if request_ctx is not None:
        try:
            rc = request_ctx.get()
        except LookupError:
            rc = None
        if rc is not None:
            meta = getattr(rc, "meta", None)
            if meta is not None:
                mpid = getattr(meta, "project_id", None)
                if mpid is None:
                    extra = getattr(meta, "model_extra", None) or {}
                    mpid = extra.get("project_id")
                if mpid:
                    return str(mpid).strip() or None
    env_pid = (os.environ.get("TAPPS_BRAIN_PROJECT") or "").strip()
    return env_pid or None


def _current_request_agent_id() -> str | None:
    """Return the ``X-Agent-Id`` header value captured by HTTP middleware."""
    agent = REQUEST_AGENT_ID.get()
    if not agent:
        return None
    return str(agent).strip() or None


def _raise_project_not_registered(project_id: str | None) -> None:
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData

    raise McpError(
        ErrorData(
            code=-32002,
            message="project_not_registered",
            data={"project_id": project_id},
        )
    )


class _StoreProxy:
    """Per-call dispatch shim that looks like a ``MemoryStore``."""

    __slots__ = ("_default_store", "_enable_hive", "_agent_id")

    def __init__(
        self,
        default_store: Any,
        *,
        enable_hive: bool,
        agent_id: str,
    ) -> None:
        object.__setattr__(self, "_default_store", default_store)
        object.__setattr__(self, "_enable_hive", enable_hive)
        object.__setattr__(self, "_agent_id", agent_id)

    def _resolve(self) -> Any:
        pid = _current_request_project_id()
        try:
            return _get_store_for_project(
                pid,
                default_store=self._default_store,
                enable_hive=self._enable_hive,
                agent_id=self._agent_id,
            )
        except Exception as exc:  # noqa: BLE001
            from tapps_brain.project_registry import ProjectNotRegisteredError

            if isinstance(exc, ProjectNotRegisteredError):
                _raise_project_not_registered(exc.project_id)
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._resolve(), name, value)

    @property  # type: ignore[override]
    def __class__(self) -> type:  # type: ignore[override]
        try:
            return self._resolve().__class__
        except Exception:  # noqa: BLE001
            return _StoreProxy


_MCP_INSTRUCTIONS = (
    "tapps-brain is a persistent cross-session memory system. "
    "Use memory tools to save, retrieve, search, and manage "
    "knowledge across coding sessions.\n\n"
    "## Hive (multi-agent memory sharing)\n\n"
    "When Hive is enabled, memories can be shared across agents "
    "using the `agent_scope` parameter on `memory_save`:\n\n"
    "- **private** (default): Only visible to the saving agent.\n"
    "- **domain**: Visible to all agents sharing the same profile.\n"
    "- **hive**: Visible to ALL agents in the Hive.\n"
    "- **group:<name>**: Hive namespace *name* for members of that group.\n\n"
    "Recall automatically merges local and Hive results."
)


def create_server(  # noqa: PLR0915
    project_dir: Path | None = None,
    *,
    enable_hive: bool = True,
    agent_id: str = "unknown",
    enable_operator_tools: bool = False,
) -> Any:  # noqa: ANN401
    """Create and configure a FastMCP server instance.

    See module docstring for behaviour notes; tool bodies live in
    ``tapps_brain.services.*`` (EPIC-070 STORY-070.1).
    """
    fastmcp_cls = _lazy_import_mcp()

    resolved_dir = _resolve_project_dir(str(project_dir) if project_dir else None)
    try:
        default_store = _get_store(
            resolved_dir, enable_hive=enable_hive, agent_id=agent_id
        )
    except Exception as exc:
        from tapps_brain.project_registry import ProjectNotRegisteredError

        if isinstance(exc, ProjectNotRegisteredError):
            from mcp.shared.exceptions import McpError
            from mcp.types import ErrorData

            raise McpError(
                ErrorData(
                    code=-32002,
                    message="project_not_registered",
                    data={"project_id": exc.project_id},
                )
            ) from exc
        raise

    store = _StoreProxy(
        default_store,
        enable_hive=enable_hive,
        agent_id=agent_id,
    )

    def _hive_for_tools() -> tuple[Any, bool]:
        """Return ``(hive_backend, should_close)`` for Hive MCP tools (ADR-007)."""
        shared = getattr(store, "_hive_store", None)
        if shared is not None:
            return shared, False
        from tapps_brain.backends import resolve_hive_backend_from_env

        extra = resolve_hive_backend_from_env()
        if extra is None:
            raise RuntimeError(
                "Hive tools require TAPPS_BRAIN_HIVE_DSN (postgresql://...) "
                "or MemoryStore configured with a Hive backend (ADR-007)."
            )
        return extra, True

    def _pid() -> str:
        return _current_request_project_id() or ""

    # STORY-070.2: Modern MCP 2025-03-26 Streamable HTTP transport.
    # - stateless_http=True  → no Mcp-Session-Id bookkeeping between calls;
    #   each POST to /mcp is self-contained.  Required for horizontal scaling.
    # - json_response=True   → return plain application/json bodies instead
    #   of SSE streams so ordinary HTTP clients (curl, requests, httpx) work.
    mcp_kwargs: dict[str, Any] = {"instructions": _MCP_INSTRUCTIONS}
    try:
        mcp = fastmcp_cls(
            "tapps-brain",
            stateless_http=True,
            json_response=True,
            **mcp_kwargs,
        )
    except TypeError:
        # Older FastMCP builds (< 1.25 / < 3.2) that lack these kwargs.
        # Stdio path still works; Streamable HTTP mount will degrade to
        # whatever the installed mcp package supports.
        mcp = fastmcp_cls("tapps-brain", **mcp_kwargs)

    # The original ``instructions=`` block kept below is unused now that
    # mcp_kwargs carries it; left as a no-op assignment for diff hygiene.
    _unused = (  # noqa: F841
            "tapps-brain is a persistent cross-session memory system. "
            "Use memory tools to save, retrieve, search, and manage "
            "knowledge across coding sessions.\n\n"
            "## Hive (multi-agent memory sharing)\n\n"
            "When Hive is enabled, memories can be shared across agents "
            "using the `agent_scope` parameter on `memory_save`:\n\n"
            "- **private** (default): Only visible to the saving agent. "
            "Use for scratch notes, intermediate reasoning, and "
            "agent-specific context.\n"
            "- **domain**: Visible to all agents sharing the same memory "
            "profile (e.g., all 'repo-brain' agents). Use for conventions, "
            "patterns, and role-specific knowledge.\n"
            "- **hive**: Visible to ALL agents in the Hive regardless of "
            "profile. Use for cross-cutting facts: tech stack decisions, "
            "project architecture, API contracts, and team agreements.\n"
            "- **group:<name>**: Hive namespace *name* for members of that group "
            "(distinct from project-local `group` on saves). Requires Hive group "
            "membership.\n\n"
            "Recall automatically merges local and Hive results. Use "
            "`hive_status` to see registered agents and namespaces, "
            "`hive_search` to query the shared store directly, "
            "`hive_propagate` to manually share an existing local memory, "
            "and `hive_write_revision` / `hive_wait_write` to poll for new "
            "Hive memory writes (lightweight pub-sub)."
    )

    # ------------------------------------------------------------------
    # Simplified Agent Brain tools (EPIC-057)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_remember(
        fact: str,
        tier: str = "procedural",
        share: bool = False,
        share_with: str = "",
    ) -> str:
        """Save a memory to the agent's brain.

        Use tier='architectural' for lasting decisions, 'pattern' for conventions,
        'procedural' for how-to knowledge. Set share=True to share with all groups,
        or share_with='hive' for org-wide.
        """
        return json.dumps(
            memory_service.brain_remember(
                store, _pid(), agent_id,
                fact=fact, tier=tier, share=share, share_with=share_with,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_recall(query: str, max_results: int = 5) -> str:
        """Recall memories matching a query."""
        return json.dumps(
            memory_service.brain_recall(
                store, _pid(), agent_id, query=query, max_results=max_results,
            ),
            default=str,
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_forget(key: str) -> str:
        """Archive a memory by key. The memory is not permanently deleted."""
        return json.dumps(memory_service.brain_forget(store, _pid(), agent_id, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_success(task_description: str, task_id: str = "") -> str:
        """Record a successful task outcome."""
        return json.dumps(
            memory_service.brain_learn_success(
                store, _pid(), agent_id,
                task_description=task_description, task_id=task_id,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_learn_failure(description: str, task_id: str = "", error: str = "") -> str:
        """Record a failed task outcome to avoid repeating mistakes."""
        return json.dumps(
            memory_service.brain_learn_failure(
                store, _pid(), agent_id,
                description=description, task_id=task_id, error=error,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def brain_status() -> str:
        """Show agent identity, group memberships, store stats, and Hive connectivity."""
        return json.dumps(memory_service.brain_status(store, _pid(), agent_id), default=str)

    # ------------------------------------------------------------------
    # Tools — model-controlled operations
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_save(
        key: str,
        value: str,
        tier: str = "pattern",
        source: str = "agent",
        tags: list[str] | None = None,
        scope: str = "project",
        confidence: float = -1.0,
        agent_scope: str = "private",
        source_agent: str = "",
        group: str | None = None,
    ) -> str:
        """Save or update a memory entry."""
        return json.dumps(
            memory_service.memory_save(
                store, _pid(), agent_id,
                key=key, value=value, tier=tier, source=source, tags=tags,
                scope=scope, confidence=confidence, agent_scope=agent_scope,
                source_agent=source_agent, group=group,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_get(key: str) -> str:
        """Retrieve a single memory entry by key."""
        return json.dumps(memory_service.memory_get(store, _pid(), agent_id, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_delete(key: str) -> str:
        """Delete a memory entry by key."""
        return json.dumps(memory_service.memory_delete(store, _pid(), agent_id, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search(
        query: str,
        tier: str | None = None,
        scope: str | None = None,
        as_of: str | None = None,
        group: str | None = None,
        since: str = "",
        until: str = "",
        time_field: str = "created_at",
    ) -> str:
        """Search memory entries using full-text search."""
        return json.dumps(
            memory_service.memory_search(
                store, _pid(), agent_id,
                query=query, tier=tier, scope=scope, as_of=as_of, group=group,
                since=since, until=until, time_field=time_field,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list(
        tier: str | None = None,
        scope: str | None = None,
        include_superseded: bool = False,
        group: str | None = None,
    ) -> str:
        """List memory entries with optional filters."""
        return json.dumps(
            memory_service.memory_list(
                store, _pid(), agent_id,
                tier=tier, scope=scope, include_superseded=include_superseded, group=group,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_groups() -> str:
        """List distinct project-local memory group names (GitHub #49)."""
        return json.dumps(memory_service.memory_list_groups(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_recall(message: str, group: str | None = None) -> str:
        """Run auto-recall for a message and return ranked memories."""
        return json.dumps(
            memory_service.memory_recall(store, _pid(), agent_id, message=message, group=group)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_reinforce(key: str, confidence_boost: float = 0.0) -> str:
        """Reinforce a memory entry, boosting its confidence and resetting decay."""
        return json.dumps(
            memory_service.memory_reinforce(
                store, _pid(), agent_id, key=key, confidence_boost=confidence_boost,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_ingest(
        context: str,
        source: str = "agent",
        agent_scope: str = "private",
    ) -> str:
        """Extract and store durable facts from conversation context."""
        return json.dumps(
            memory_service.memory_ingest(
                store, _pid(), agent_id,
                context=context, source=source, agent_scope=agent_scope,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_supersede(
        old_key: str,
        new_value: str,
        key: str | None = None,
        tier: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Create a new version of a memory, superseding the old one."""
        return json.dumps(
            memory_service.memory_supersede(
                store, _pid(), agent_id,
                old_key=old_key, new_value=new_value, key=key, tier=tier, tags=tags,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_history(key: str) -> str:
        """Show the full version chain for a memory key."""
        return json.dumps(memory_service.memory_history(store, _pid(), agent_id, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_index_session(
        session_id: str,
        chunks: list[str],
    ) -> str:
        """Index session chunks for future search."""
        return json.dumps(
            memory_service.memory_index_session(
                store, _pid(), agent_id, session_id=session_id, chunks=chunks,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_search_sessions(
        query: str,
        limit: int = 10,
    ) -> str:
        """Search past session summaries."""
        return json.dumps(
            memory_service.memory_search_sessions(
                store, _pid(), agent_id, query=query, limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_capture(
        response: str,
        source: str = "agent",
        agent_scope: str = "private",
    ) -> str:
        """Extract and persist new facts from an agent response."""
        return json.dumps(
            memory_service.memory_capture(
                store, _pid(), agent_id,
                response=response, source=source, agent_scope=agent_scope,
            )
        )

    # ------------------------------------------------------------------
    # Feedback tools (EPIC-029 / STORY-029.4)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_rate(
        entry_key: str,
        rating: str = "helpful",
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Rate a recalled memory entry (creates ``recall_rated`` event)."""
        return json.dumps(
            feedback_service.feedback_rate(
                store, _pid(), agent_id,
                entry_key=entry_key, rating=rating,
                session_id=session_id, details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_gap(
        query: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Report a knowledge gap (``gap_reported`` event)."""
        return json.dumps(
            feedback_service.feedback_gap(
                store, _pid(), agent_id,
                query=query, session_id=session_id, details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_issue(
        entry_key: str,
        issue: str,
        session_id: str = "",
        details_json: str = "",
    ) -> str:
        """Flag a quality issue with a memory entry (``issue_flagged``)."""
        return json.dumps(
            feedback_service.feedback_issue(
                store, _pid(), agent_id,
                entry_key=entry_key, issue=issue,
                session_id=session_id, details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_record(
        event_type: str,
        entry_key: str = "",
        session_id: str = "",
        utility_score: float | None = None,
        details_json: str = "",
    ) -> str:
        """Record a generic feedback event (built-in or custom type)."""
        return json.dumps(
            feedback_service.feedback_record(
                store, _pid(), agent_id,
                event_type=event_type, entry_key=entry_key, session_id=session_id,
                utility_score=utility_score, details_json=details_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def feedback_query(
        event_type: str = "",
        entry_key: str = "",
        session_id: str = "",
        since: str = "",
        until: str = "",
        limit: int = 100,
    ) -> str:
        """Query recorded feedback events with optional filters."""
        return json.dumps(
            feedback_service.feedback_query(
                store, _pid(), agent_id,
                event_type=event_type, entry_key=entry_key, session_id=session_id,
                since=since, until=until, limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_report(
        record_history: bool = True,
    ) -> str:
        """Run quality diagnostics (EPIC-030): composite score, dimensions, circuit state."""
        return json.dumps(
            diagnostics_service.diagnostics_report(
                store, _pid(), agent_id, record_history=record_history,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def diagnostics_history(
        limit: int = 50,
    ) -> str:
        """Return recent persisted diagnostics snapshots."""
        return json.dumps(
            diagnostics_service.diagnostics_history(
                store, _pid(), agent_id, limit=limit,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_process(since: str = "") -> str:
        """Run feedback → confidence pipeline (EPIC-031)."""
        return json.dumps(
            flywheel_service.flywheel_process(store, _pid(), agent_id, since=since)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_gaps(limit: int = 10, semantic: bool = False) -> str:
        """Return top knowledge gaps as JSON."""
        return json.dumps(
            flywheel_service.flywheel_gaps(
                store, _pid(), agent_id, limit=limit, semantic=semantic,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_report(period_days: int = 7) -> str:
        """Generate quality report (markdown + structured summary)."""
        return json.dumps(
            flywheel_service.flywheel_report(
                store, _pid(), agent_id, period_days=period_days,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_evaluate(suite_path: str, k: int = 5) -> str:
        """Run BEIR-format directory or YAML suite evaluation."""
        return json.dumps(
            flywheel_service.flywheel_evaluate(
                store, _pid(), agent_id, suite_path=suite_path, k=k,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def flywheel_hive_feedback(threshold: int = 3) -> str:
        """Aggregate / apply Hive cross-project feedback penalties."""
        return json.dumps(
            flywheel_service.flywheel_hive_feedback(
                store, _pid(), agent_id, threshold=threshold,
            )
        )

    # ------------------------------------------------------------------
    # Resources — read-only store views
    # ------------------------------------------------------------------

    @mcp.resource("memory://stats")  # type: ignore[untyped-decorator]
    def stats_resource() -> str:
        """Store stats: counts, tiers, schema, package/profile, optional profile_seed_version."""
        snap = store.snapshot()
        schema_ver = store.get_schema_version()
        h = store.health()
        return json.dumps(
            {
                "project_root": str(snap.project_root),
                "total_entries": snap.total_count,
                "max_entries": store._max_entries,
                "max_entries_per_group": store._max_entries_per_group,
                "schema_version": schema_ver,
                "package_version": h.package_version,
                "profile_name": h.profile_name,
                "profile_seed_version": h.profile_seed_version,
                "tier_distribution": snap.tier_counts,
            }
        )

    @mcp.resource("memory://agent-contract")  # type: ignore[untyped-decorator]
    def agent_contract_resource() -> str:
        """Agent integration snapshot: versions, profile, tiers, recall empty codes."""
        from tapps_brain.models import MemoryTier
        from tapps_brain.recall_diagnostics import (
            RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
            RECALL_EMPTY_ENGAGEMENT_LOW,
            RECALL_EMPTY_GROUP_EMPTY,
            RECALL_EMPTY_NO_RANKED_MATCHES,
            RECALL_EMPTY_POST_FILTER,
            RECALL_EMPTY_RAG_BLOCKED,
            RECALL_EMPTY_SEARCH_FAILED,
            RECALL_EMPTY_STORE_EMPTY,
        )

        h = store.health()
        prof = store.profile
        layers = list(prof.layer_names) if prof is not None else []
        return json.dumps(
            {
                "package_version": h.package_version,
                "schema_version": h.schema_version,
                "profile_name": h.profile_name,
                "profile_layer_names": layers,
                "canonical_memory_tiers": [m.value for m in MemoryTier],
                "recall_empty_reason_codes": sorted(
                    {
                        RECALL_EMPTY_ENGAGEMENT_LOW,
                        RECALL_EMPTY_SEARCH_FAILED,
                        RECALL_EMPTY_STORE_EMPTY,
                        RECALL_EMPTY_GROUP_EMPTY,
                        RECALL_EMPTY_NO_RANKED_MATCHES,
                        RECALL_EMPTY_BELOW_SCORE_THRESHOLD,
                        RECALL_EMPTY_RAG_BLOCKED,
                        RECALL_EMPTY_POST_FILTER,
                    }
                ),
                "write_path_mcp": "memory_save",
                "write_path_cli": "tapps-brain memory save KEY VALUE [options]",
                "read_paths_mcp": ["memory_search", "memory_recall", "memory_list", "memory_get"],
                "operator_docs": "https://github.com/wtthornton/tapps-brain/tree/main/docs/guides",
            },
            indent=2,
        )

    @mcp.resource("memory://health")  # type: ignore[untyped-decorator]
    def health_resource() -> str:
        """Store health report."""
        report = store.health()
        return json.dumps(report.model_dump(mode="json"))

    @mcp.resource("memory://entries/{key}")  # type: ignore[untyped-decorator]
    def entry_resource(key: str) -> str:
        """Full detail view of a single memory entry."""
        entry = store.get(key)
        if entry is None:
            return json.dumps({"error": "not_found", "key": key})
        return json.dumps(entry.model_dump(mode="json"))

    @mcp.resource("memory://metrics")  # type: ignore[untyped-decorator]
    def metrics_resource() -> str:
        """Operation metrics: counters and latency histograms."""
        snapshot = store.get_metrics()
        return json.dumps(snapshot.to_dict())

    @mcp.resource("memory://feedback")  # type: ignore[untyped-decorator]
    def feedback_resource() -> str:
        """Recent feedback events (up to 500), newest-friendly order by query default."""
        events = store.query_feedback(limit=500)
        return json.dumps(
            {
                "events": [e.model_dump(mode="json") for e in events],
                "count": len(events),
            }
        )

    @mcp.resource("memory://diagnostics")  # type: ignore[untyped-decorator]
    def diagnostics_resource() -> str:
        """Latest diagnostics report (does not append history by default)."""
        rep = store.diagnostics(record_history=False)
        return json.dumps(rep.model_dump(mode="json"))

    @mcp.resource("memory://report")  # type: ignore[untyped-decorator]
    def report_resource() -> str:
        """Latest flywheel quality report summary (from last ``generate_report``)."""
        latest = store.latest_quality_report()
        if latest is None:
            rep = store.generate_report(period_days=7)
            payload = rep.structured_data
        else:
            payload = latest.get("structured_data", latest)
        return json.dumps(payload)

    # ------------------------------------------------------------------
    # Prompts — user-invoked workflow templates (STORY-008.6)
    # ------------------------------------------------------------------

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def recall(topic: str) -> list[dict[str, str]]:
        """What do you remember about a topic?"""
        result = store.recall(topic)
        if result.memory_count == 0:
            body = f"No memories found about: {topic}"
        else:
            body = (
                f'Here are {result.memory_count} memories about "{topic}":\n\n'
                f"{result.memory_section}"
            )
        return [{"role": "user", "content": body}]

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def store_summary() -> list[dict[str, str]]:
        """Generate a summary of what's in the memory store."""
        snap = store.snapshot()
        schema_ver = store.get_schema_version()
        entries = store.list_all()

        lines = [
            f"Memory store summary for: {snap.project_root}",
            f"Total entries: {snap.total_count} / 500",
            f"Schema version: {schema_ver}",
            f"Tier distribution: {json.dumps(snap.tier_counts)}",
            "",
        ]
        preview_len = 80
        if entries:
            lines.append("Recent entries (up to 10):")
            for entry in entries[:10]:
                truncated = entry.value[:preview_len]
                suffix = "…" if len(entry.value) > preview_len else ""
                lines.append(f"  - [{entry.tier!s}] {entry.key}: {truncated}{suffix}")
        else:
            lines.append("The store is empty.")

        return [{"role": "user", "content": "\n".join(lines)}]

    @mcp.prompt()  # type: ignore[untyped-decorator]
    def remember(fact: str) -> list[dict[str, str]]:
        """Remember a fact by saving it to the memory store."""
        body = (
            f"The user wants you to remember the following:\n\n"
            f'"{fact}"\n\n'
            "Please save this to the memory store using the memory_save tool. "
            "Choose an appropriate:\n"
            "- **key**: a short, descriptive kebab-case identifier\n"
            "- **tier**: one of architectural (system-level decisions), "
            "pattern (coding patterns/conventions), "
            "procedural (workflows/processes), "
            "or context (session-specific facts)\n"
            "- **tags**: relevant category tags\n"
            "- **confidence**: 0.7-0.9 for stated facts, 0.5-0.7 for inferences\n\n"
            "Confirm what you saved back to the user."
        )
        return [{"role": "user", "content": body}]

    # ------------------------------------------------------------------
    # Maintenance tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_consolidate(
        threshold: float = 0.7,
        min_group_size: int = 3,
        force: bool = True,
    ) -> str:
        """Trigger memory consolidation to merge similar entries."""
        return json.dumps(
            maintenance_service.maintenance_consolidate(
                store, _pid(), agent_id,
                project_root=resolved_dir, threshold=threshold,
                min_group_size=min_group_size, force=force,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_gc(dry_run: bool = False) -> str:
        """Run garbage collection to archive stale memories."""
        return json.dumps(
            maintenance_service.maintenance_gc(store, _pid(), agent_id, dry_run=dry_run)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def maintenance_stale() -> str:
        """List GC stale memory candidates with reasons (read-only; GitHub #21)."""
        return json.dumps(maintenance_service.maintenance_stale(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_health(check_hive: bool = True) -> str:
        """Return a structured health report for tapps-brain (issue #15)."""
        return json.dumps(
            diagnostics_service.tapps_brain_health(
                store, _pid(), agent_id, check_hive=check_hive,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config() -> str:
        """Return the current garbage collection configuration."""
        return json.dumps(memory_service.memory_gc_config(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_gc_config_set(
        floor_retention_days: int | None = None,
        session_expiry_days: int | None = None,
        contradicted_threshold: float | None = None,
    ) -> str:
        """Update garbage collection configuration thresholds."""
        return json.dumps(
            memory_service.memory_gc_config_set(
                store, _pid(), agent_id,
                floor_retention_days=floor_retention_days,
                session_expiry_days=session_expiry_days,
                contradicted_threshold=contradicted_threshold,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config() -> str:
        """Return the current auto-consolidation configuration."""
        return json.dumps(memory_service.memory_consolidation_config(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_consolidation_config_set(
        enabled: bool | None = None,
        threshold: float | None = None,
        min_entries: int | None = None,
    ) -> str:
        """Update auto-consolidation configuration."""
        return json.dumps(
            memory_service.memory_consolidation_config_set(
                store, _pid(), agent_id,
                enabled=enabled, threshold=threshold, min_entries=min_entries,
            )
        )

    # ------------------------------------------------------------------
    # Export / Import tools (STORY-008.5)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_export(
        tier: str | None = None,
        scope: str | None = None,
        min_confidence: float | None = None,
    ) -> str:
        """Export memory entries as JSON."""
        return json.dumps(
            memory_service.memory_export(
                store, _pid(), agent_id,
                project_root=str(resolved_dir),
                tier=tier, scope=scope, min_confidence=min_confidence,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_import(
        memories_json: str,
        overwrite: bool = False,
    ) -> str:
        """Import memory entries from a JSON string."""
        return json.dumps(
            memory_service.memory_import(
                store, _pid(), agent_id,
                memories_json=memories_json, overwrite=overwrite,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_relay_export(source_agent: str, items_json: str) -> str:
        """Build a memory relay JSON payload for cross-node handoff (GitHub #19)."""
        return json.dumps(
            relay_service.tapps_brain_relay_export(
                store, _pid(), agent_id,
                source_agent=source_agent, items_json=items_json,
            )
        )

    # ------------------------------------------------------------------
    # Profile tools (EPIC-010)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_info() -> str:
        """Return the active profile name, layers, and scoring config."""
        return json.dumps(profile_service.profile_info(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_profile_onboarding() -> str:
        """Return Markdown onboarding guidance for the active memory profile (GitHub #45)."""
        return json.dumps(
            profile_service.memory_profile_onboarding(store, _pid(), agent_id)
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def profile_switch(name: str) -> str:
        """Switch to a different built-in memory profile."""
        return json.dumps(profile_service.profile_switch(store, _pid(), agent_id, name=name))

    # ------------------------------------------------------------------
    # Hive tools (EPIC-011)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_status() -> str:
        """Return Hive status: namespaces, entry counts, and registered agents."""
        return json.dumps(
            hive_service.hive_status(
                store, _pid(), agent_id, hive_resolver=_hive_for_tools,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_search(query: str, namespace: str | None = None) -> str:
        """Search the shared Hive for memories from other agents."""
        return json.dumps(
            hive_service.hive_search(
                store, _pid(), agent_id,
                hive_resolver=_hive_for_tools, query=query, namespace=namespace,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_propagate(
        key: str,
        agent_scope: str = "hive",
        force: bool = False,
        dry_run: bool = False,
    ) -> str:
        """Manually propagate a local memory to the Hive shared store."""
        return json.dumps(
            hive_service.hive_propagate(
                store, _pid(), agent_id,
                hive_resolver=_hive_for_tools,
                key=key, agent_scope=agent_scope, force=force, dry_run=dry_run,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_push(
        agent_scope: str = "hive",
        push_all: bool = False,
        tags: str = "",
        tier: str | None = None,
        keys: str = "",
        dry_run: bool = False,
        force: bool = False,
    ) -> str:
        """Batch-promote local project memories to the Hive (GitHub #18)."""
        return json.dumps(
            hive_service.hive_push(
                store, _pid(), agent_id,
                hive_resolver=_hive_for_tools,
                agent_scope=agent_scope, push_all=push_all, tags=tags, tier=tier,
                keys=keys, dry_run=dry_run, force=force,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_write_revision() -> str:
        """Return the Hive write notification revision (GitHub #12)."""
        return json.dumps(
            hive_service.hive_write_revision(
                store, _pid(), agent_id, hive_resolver=_hive_for_tools,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def hive_wait_write(since_revision: int = 0, timeout_seconds: float = 10.0) -> str:
        """Wait until the Hive write revision exceeds *since_revision* or timeout."""
        return json.dumps(
            hive_service.hive_wait_write(
                store, _pid(), agent_id,
                hive_resolver=_hive_for_tools,
                since_revision=since_revision, timeout_seconds=timeout_seconds,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_register(
        agent_id: str,  # noqa: ARG001 — shadows outer agent_id intentionally (was the original signature)
        profile: str = "repo-brain",
        skills: str = "",
    ) -> str:
        """Register an agent in the Hive."""
        return json.dumps(
            agents_service.agent_register(
                store, _pid(), agent_id,
                new_agent_id=agent_id, profile=profile, skills=skills,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_create(
        agent_id: str,  # noqa: ARG001
        profile: str = "repo-brain",
        skills: str = "",
    ) -> str:
        """Create an agent: register in the Hive with a validated profile."""
        return json.dumps(
            agents_service.agent_create(
                store, _pid(), agent_id,
                new_agent_id=agent_id, profile=profile, skills=skills,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_list() -> str:
        """List all registered agents in the Hive."""
        return json.dumps(agents_service.agent_list(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def agent_delete(agent_id: str) -> str:  # noqa: ARG001
        """Delete a registered agent from the Hive."""
        return json.dumps(
            agents_service.agent_delete(
                store, _pid(), agent_id, target_agent_id=agent_id,
            )
        )

    # ------------------------------------------------------------------
    # Knowledge graph tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations(key: str) -> str:
        """Return all relations associated with a memory entry key."""
        return json.dumps(memory_service.memory_relations(store, _pid(), agent_id, key=key))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_relations_get_batch(keys_json: str) -> str:
        """Return relations for multiple memory keys in one call (STORY-048.2)."""
        return json.dumps(
            memory_service.memory_relations_get_batch(
                store, _pid(), agent_id, keys_json=keys_json,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_find_related(key: str, max_hops: int = 2) -> str:
        """Find entries related to a key via BFS traversal of the relation graph."""
        return json.dumps(
            memory_service.memory_find_related(
                store, _pid(), agent_id, key=key, max_hops=max_hops,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_query_relations(
        subject: str = "",
        predicate: str = "",
        object_entity: str = "",
    ) -> str:
        """Filter relations by subject, predicate, and/or object_entity."""
        return json.dumps(
            memory_service.memory_query_relations(
                store, _pid(), agent_id,
                subject=subject, predicate=predicate, object_entity=object_entity,
            )
        )

    # ------------------------------------------------------------------
    # Audit trail tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_audit(
        key: str = "",
        event_type: str = "",
        since: str = "",
        until: str = "",
        limit: int = 50,
    ) -> str:
        """Query the audit trail for memory events."""
        return json.dumps(
            memory_service.memory_audit(
                store, _pid(), agent_id,
                key=key, event_type=event_type, since=since, until=until, limit=limit,
            )
        )

    # ------------------------------------------------------------------
    # Tag management tools (EPIC-015)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_list_tags() -> str:
        """List all tags used in the memory store with their usage counts."""
        return json.dumps(memory_service.memory_list_tags(store, _pid(), agent_id))

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_update_tags(
        key: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> str:
        """Atomically add and/or remove tags on an existing memory entry."""
        return json.dumps(
            memory_service.memory_update_tags(
                store, _pid(), agent_id, key=key, add=add, remove=remove,
            )
        )

    @mcp.tool()  # type: ignore[untyped-decorator]
    def memory_entries_by_tag(
        tag: str,
        tier: str = "",
    ) -> str:
        """Return all memory entries that carry a specific tag."""
        return json.dumps(
            memory_service.memory_entries_by_tag(
                store, _pid(), agent_id, tag=tag, tier=tier,
            )
        )

    # ------------------------------------------------------------------
    # Session end tool (Issue #17 — episodic memory capture)
    # ------------------------------------------------------------------

    @mcp.tool()  # type: ignore[untyped-decorator]
    def tapps_brain_session_end(
        summary: str,
        tags: list[str] | None = None,
        daily_note: bool = False,
    ) -> str:
        """Record an end-of-session episodic memory entry."""
        return json.dumps(
            maintenance_service.tapps_brain_session_end(
                store, _pid(), agent_id,
                project_root=resolved_dir, summary=summary,
                tags=tags, daily_note=daily_note,
            ),
            default=str,
        )

    # ------------------------------------------------------------------
    # Operator tool gate
    # ------------------------------------------------------------------

    _OPERATOR_TOOL_NAMES: frozenset[str] = frozenset(
        {
            "maintenance_consolidate",
            "maintenance_gc",
            "maintenance_stale",
            "tapps_brain_health",
            "memory_gc_config",
            "memory_gc_config_set",
            "memory_consolidation_config",
            "memory_consolidation_config_set",
            "memory_export",
            "memory_import",
            "tapps_brain_relay_export",
            "flywheel_evaluate",
            "flywheel_hive_feedback",
        }
    )

    if not enable_operator_tools:
        for _op_tool in _OPERATOR_TOOL_NAMES:
            try:
                mcp._tool_manager.remove_tool(_op_tool)
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Attach store and Hive metadata to server for testing / tool access
    # ------------------------------------------------------------------
    mcp._tapps_store = store
    mcp._tapps_default_store = default_store
    mcp._tapps_agent_id = agent_id
    mcp._tapps_hive_enabled = enable_hive
    mcp._tapps_operator_tools_enabled = enable_operator_tools
    mcp._tapps_hive_store = getattr(default_store, "_hive_store", None)

    return mcp


def main() -> None:
    """Entry point for ``tapps-brain-mcp`` command."""
    try:
        pkg_ver = importlib.metadata.version("tapps-brain")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover
        pkg_ver = "0.0.0-dev"
    parser = argparse.ArgumentParser(
        prog="tapps-brain-mcp",
        description=(
            "Run the tapps-brain MCP server (stdio transport). "
            "Version matches the installed tapps-brain package."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {pkg_ver}",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        default=None,
        help="Project root directory (defaults to cwd).",
    )
    parser.add_argument(
        "--agent-id",
        type=str,
        default="unknown",
        help="Agent identifier for Hive propagation (default: 'unknown').",
    )
    parser.add_argument(
        "--enable-hive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable Hive multi-agent shared brain (default: enabled).",
    )
    parser.add_argument(
        "--enable-operator-tools",
        action="store_true",
        default=False,
        help=(
            "Register advanced/maintenance tools (consolidation, GC, export/import, "
            "relay, eval harness). Not intended for regular agent sessions. "
            "Also enabled by TAPPS_BRAIN_OPERATOR_TOOLS=1 env var."
        ),
    )
    args = parser.parse_args()

    effective_agent_id = args.agent_id
    if effective_agent_id == "unknown":
        effective_agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "unknown")

    enable_operator_tools: bool = (
        args.enable_operator_tools
        or os.environ.get("TAPPS_BRAIN_OPERATOR_TOOLS", "") == "1"
    )

    project_dir = Path(args.project_dir) if args.project_dir else None
    try:
        server = create_server(
            project_dir,
            enable_hive=args.enable_hive,
            agent_id=effective_agent_id,
            enable_operator_tools=enable_operator_tools,
        )
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        sys.exit(1)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
