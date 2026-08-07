"""Escape hatches for auto-consolidation on the agent-facing MCP surface.

``skip_consolidation`` (write side) and ``include_sources`` (read side) both
existed one layer down but were unreachable from ``brain_remember`` /
``brain_recall`` — so an agent whose long entry had been merged into a
truncated summary had no way to opt out, and no way to read the originals
back. These tests exercise the registered tool functions, not the service
layer, because reachability is the thing under test.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tapps_brain.mcp_server.tools_brain import register_brain_tools
from tapps_brain.store import ConsolidationConfig, MemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


class _CapturingMCP:
    """Minimal stand-in for FastMCP that records the decorated tool functions."""

    def __init__(self) -> None:
        self.tools: dict[str, Callable[..., Any]] = {}

    def tool(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _Ctx:
    """Minimal ToolContext stand-in bound to a single store."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self.server_agent_id = "test-agent"

    def resolve_store_for_call(self, _agent_id: str = "") -> MemoryStore:
        return self._store

    def pid(self) -> str:
        return "test-project"

    def resolve_per_call_agent_id(self, agent_id: str = "", *, default: str = "") -> str:
        return agent_id or default


@pytest.fixture
def brain_tools(tmp_path: Path) -> Generator[tuple[dict[str, Any], MemoryStore], None, None]:
    """Registered brain_* tool functions bound to a live store."""
    # min_entries=2 / threshold=0.1 makes consolidation eager, so a test that
    # asserts "no merge happened" is asserting the flag, not a lucky miss.
    store = MemoryStore(
        tmp_path,
        consolidation_config=ConsolidationConfig(
            enabled=True, threshold=0.1, min_entries=2, exempt_tiers=()
        ),
    )
    mcp = _CapturingMCP()
    register_brain_tools(mcp, _Ctx(store))
    try:
        yield mcp.tools, store
    finally:
        store.close()


class TestBrainRememberSkipConsolidation:
    """VAL-07 (write side)."""

    def test_skip_consolidation_creates_entry_and_triggers_no_merge(
        self, brain_tools: tuple[dict[str, Any], MemoryStore]
    ) -> None:
        tools, store = brain_tools
        remember = tools["brain_remember"]

        facts = [
            "Deployment runbook: run the migration sidecar before restarting http.",
            "Deployment runbook: run the migration sidecar, then restart http, then smoke test.",
            "Deployment runbook: migration sidecar first, http restart second, smoke test third.",
        ]
        keys = []
        for fact in facts:
            payload = json.loads(remember(fact=fact, tier="procedural", skip_consolidation=True))
            assert payload.get("saved") is True, payload
            keys.append(payload["key"])

        # Every entry survives with its own body and no merge row was created.
        # (``contradicted`` is not asserted: save-path conflict detection sets
        # it for near-duplicates independently of consolidation.)
        assert store.count() == len(facts)
        for key, fact in zip(keys, facts, strict=True):
            entry = store.get(key)
            assert entry is not None
            assert entry.value == fact
            assert entry.superseded_by is None
            assert "consolidated into" not in (entry.contradiction_reason or "")

    def test_default_call_still_allows_consolidation(
        self, brain_tools: tuple[dict[str, Any], MemoryStore]
    ) -> None:
        """The hatch must be opt-in — omitting it must not disable merging.

        Without this the previous test would pass even if ``skip_consolidation``
        were ignored and consolidation were simply never reachable here.
        """
        tools, store = brain_tools
        remember = tools["brain_remember"]

        # A short older entry plus a long newer one: the merged value carries
        # the newest verbatim *plus* the older's unique sentence, so it clears
        # the content-preservation floor and the merge is allowed to proceed.
        for fact in (
            "Retry backoff caps at thirty seconds.",
            "Retry policy uses exponential backoff with jitter on transient errors, and the "
            "client surfaces a structured error envelope after the final attempt so callers "
            "can tell exhaustion apart from an immediate failure.",
        ):
            json.loads(remember(fact=fact, tier="procedural"))

        superseded = [e for e in store.list_all() if e.superseded_by is not None]
        assert superseded, (
            "expected the default (no skip_consolidation) path to merge these near-duplicates; "
            "if it does not, the skip_consolidation test above proves nothing"
        )


class TestBrainRecallIncludeSources:
    """VAL-07 (read side)."""

    def _seed_merged_pair(self, store: MemoryStore) -> tuple[str, str]:
        """Persist a merged entry plus one consolidation source, as a merge leaves them."""
        store.save(
            key="jwt-signing-original",
            value="Use RS256 for JWT signing keys and rotate them quarterly.",
            tier="pattern",
            tags=["jwt"],
            skip_consolidation=True,
        )
        store.save(
            key="jwt-signing-merged",
            value="Use RS256 for JWT signing keys.",
            tier="pattern",
            tags=["jwt"],
            skip_consolidation=True,
            dedup=False,
            conflict_check=False,
        )
        store.update_fields(
            "jwt-signing-original",
            contradicted=True,
            contradiction_reason="consolidated into jwt-signing-merged",
            invalid_at="2020-01-01T00:00:00+00:00",
            superseded_by="jwt-signing-merged",
        )
        return "jwt-signing-original", "jwt-signing-merged"

    def test_include_sources_returns_what_the_default_call_omits(
        self, brain_tools: tuple[dict[str, Any], MemoryStore]
    ) -> None:
        tools, store = brain_tools
        recall = tools["brain_recall"]
        source_key, merged_key = self._seed_merged_pair(store)

        default_keys = [r["key"] for r in json.loads(recall(query="JWT signing", max_results=10))]
        assert merged_key in default_keys
        assert source_key not in default_keys

        with_sources = [
            r["key"]
            for r in json.loads(recall(query="JWT signing", max_results=10, include_sources=True))
        ]
        assert merged_key in with_sources
        assert source_key in with_sources

    def test_include_sources_returns_the_untruncated_original(
        self, brain_tools: tuple[dict[str, Any], MemoryStore]
    ) -> None:
        tools, store = brain_tools
        recall = tools["brain_recall"]
        source_key, _ = self._seed_merged_pair(store)

        results = json.loads(recall(query="JWT signing", max_results=10, include_sources=True))
        source = next(r for r in results if r["key"] == source_key)
        assert source["value"] == ("Use RS256 for JWT signing keys and rotate them quarterly.")

    def test_include_sources_does_not_resurrect_unrelated_expired_entries(
        self, brain_tools: tuple[dict[str, Any], MemoryStore]
    ) -> None:
        """The flag is about consolidation sources, not about historical rows at large."""
        tools, store = brain_tools
        recall = tools["brain_recall"]
        self._seed_merged_pair(store)
        store.save(
            key="jwt-signing-expired",
            value="Legacy JWT signing guidance that expired on its own.",
            tier="pattern",
            tags=["jwt"],
            skip_consolidation=True,
            dedup=False,
            conflict_check=False,
        )
        store.update_fields("jwt-signing-expired", invalid_at="2020-01-01T00:00:00+00:00")

        keys = [
            r["key"]
            for r in json.loads(recall(query="JWT signing", max_results=10, include_sources=True))
        ]
        assert "jwt-signing-expired" not in keys
