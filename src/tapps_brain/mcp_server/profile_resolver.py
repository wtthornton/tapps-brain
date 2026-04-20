"""Per-request MCP profile resolver — EPIC-073 STORY-073.2.

Resolution precedence (cheapest check first):

1. ``X-Brain-Profile`` HTTP header — explicit caller override.  Validated
   against :class:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry`
   before acceptance; unknown values yield a 400 upstream.
2. Agent-registry lookup — ``(project_id, agent_id)`` → ``profile`` column
   from the ``agent_registry`` Postgres table via an injected callable.
   Results are cached in-process with a configurable TTL (default 60 s) to
   avoid a round-trip on every request.
3. Server-level default — ``TAPPS_BRAIN_DEFAULT_PROFILE`` env var or the
   literal ``"full"`` when the var is unset, preserving current behaviour.

Public API
----------
ProfileResolver(registry, agent_profile_getter=None, default_profile=None,
               cache_ttl=60.0)
    Build a resolver.  *agent_profile_getter* is an optional callable
    ``(project_id, agent_id) -> str | None`` so callers can inject any
    backend without coupling this module to Postgres.
ProfileResolver.resolve(*, project_id, agent_id, header_profile) -> str
    Return the resolved profile name (never raises).
ProfileResolver.invalidate(project_id, agent_id) -> None
    Evict a single cache entry (call after agent-registry writes).
ProfileResolver.cache_stats() -> dict[str, int | float]
    Return hit/miss counters and derived hit-rate for the STORY-073.4
    metric surface.
"""

from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from tapps_brain.mcp_server.profile_registry import ProfileRegistry


class ProfileResolver:
    """Resolve the active MCP profile for a single request.

    The resolver is designed to be a **process-wide singleton** — build it
    once at adapter startup and share it across all requests.  Thread-safety
    for the cache is provided by an internal :class:`threading.Lock`.

    Parameters
    ----------
    registry:
        A :class:`~tapps_brain.mcp_server.profile_registry.ProfileRegistry`
        instance used to validate header-supplied profile names.
    agent_profile_getter:
        Optional callable ``(project_id: str, agent_id: str) -> str | None``
        that performs the agent-registry DB lookup.  When *None* the
        registry-lookup step is skipped entirely.
    default_profile:
        Profile name to fall back to when neither the header nor the agent
        registry provides one.  Defaults to the ``TAPPS_BRAIN_DEFAULT_PROFILE``
        environment variable, or ``"full"`` when the variable is unset.
    cache_ttl:
        Seconds that a cached agent → profile mapping stays valid.
    """

    def __init__(
        self,
        registry: ProfileRegistry,
        agent_profile_getter: Callable[[str, str], str | None] | None = None,
        default_profile: str | None = None,
        cache_ttl: float = 60.0,
    ) -> None:
        self._registry = registry
        self._getter = agent_profile_getter
        self._default = default_profile or os.environ.get("TAPPS_BRAIN_DEFAULT_PROFILE", "full")
        self._cache_ttl = cache_ttl
        # (project_id, agent_id) -> (profile | None, expires_monotonic)
        self._cache: dict[tuple[str, str], tuple[str | None, float]] = {}
        self._lock = threading.Lock()
        self._cache_hits = 0
        self._cache_misses = 0
        # STORY-073.4: resolution-source counters (source ∈ {header, agent_registry, default})
        self._resolution_source_counts: dict[str, int] = {}
        # STORY-073.4: invalidation counter (for mcp_profile_cache_events_total{invalidated})
        self._cache_invalidations = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        *,
        project_id: str,
        agent_id: str,
        header_profile: str | None,
    ) -> str:
        """Return the resolved profile name for this request.

        The caller is responsible for validating *header_profile* against the
        registry and returning a 400 before calling ``resolve`` — this method
        trusts that *header_profile* (when not ``None``) is already known-good.

        Parameters
        ----------
        project_id:
            Tenant project identifier (from ``X-Project-Id`` header).
        agent_id:
            Agent identifier (from ``X-Agent-Id`` / ``X-Tapps-Agent`` header).
        header_profile:
            Stripped, lower-cased value of ``X-Brain-Profile``, or ``None``
            when the header was absent.
        """
        # 1. Header override — highest precedence, zero overhead.
        if header_profile is not None:
            with self._lock:
                self._resolution_source_counts["header"] = (
                    self._resolution_source_counts.get("header", 0) + 1
                )
            return header_profile

        # 2. Agent-registry lookup (cached).
        if self._getter is not None:
            registered = self._lookup_cached(project_id, agent_id)
            if registered:
                with self._lock:
                    self._resolution_source_counts["agent_registry"] = (
                        self._resolution_source_counts.get("agent_registry", 0) + 1
                    )
                return registered

        # 3. Server-level default.
        with self._lock:
            self._resolution_source_counts["default"] = (
                self._resolution_source_counts.get("default", 0) + 1
            )
        return self._default

    def invalidate(self, project_id: str, agent_id: str) -> None:
        """Evict a single ``(project_id, agent_id)`` cache entry.

        Call this after any agent-registry write to ensure the next request
        for that agent picks up the new profile instead of using a stale TTL.
        """
        with self._lock:
            self._cache.pop((project_id, agent_id), None)
            self._cache_invalidations += 1

    def cache_stats(self) -> dict[str, int | float]:
        """Return hit/miss/invalidation counters and derived hit-rate.

        Used by STORY-073.4 to expose the cache metrics on ``/metrics``.

        Returns
        -------
        dict with keys ``hits``, ``misses``, ``invalidated``, ``hit_rate``.
        """
        with self._lock:
            hits = self._cache_hits
            misses = self._cache_misses
            invalidated = self._cache_invalidations
        total = hits + misses
        return {
            "hits": hits,
            "misses": misses,
            "invalidated": invalidated,
            "hit_rate": hits / total if total > 0 else 0.0,
        }

    def resolution_stats(self) -> dict[str, int]:
        """Return resolution-source counters.

        Used by STORY-073.4 to expose ``mcp_profile_resolution_source_total``
        on ``/metrics``.

        Returns
        -------
        dict mapping source names (``header``, ``agent_registry``, ``default``)
        to their cumulative call counts.
        """
        with self._lock:
            return dict(self._resolution_source_counts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lookup_cached(self, project_id: str, agent_id: str) -> str | None:
        """Return the cached profile for ``(project_id, agent_id)``.

        Performs the DB fetch on cache miss.  The fetch itself runs outside
        the lock so a slow DB call does not block other requests.
        """
        now = time.monotonic()
        key = (project_id, agent_id)

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and now < cached[1]:
                self._cache_hits += 1
                return cached[0]
            self._cache_misses += 1

        # Fetch outside the lock — concurrent misses for the same key do a
        # redundant DB call rather than serialise; that's fine for a 60 s TTL.
        result = self._getter(project_id, agent_id)  # type: ignore[misc]

        with self._lock:
            self._cache[key] = (result, now + self._cache_ttl)
        return result
