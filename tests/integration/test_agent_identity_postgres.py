"""Postgres integration tests for (project_id, agent_id) row isolation.

STORY-066.13: Replaces deleted SQLite-coupled test_agent_identity test file
with a Postgres-backed equivalent.

Verifies that the composite key ``(project_id, agent_id)`` enforces per-agent
per-project isolation — no agent can read another agent's private memories.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` environment variable (skipped otherwise).
Mark: ``requires_postgres``
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_backend(project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    cm = PostgresConnectionManager(_PG_DSN)
    return PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


def _make_entry(key: str, value: str) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


# ---------------------------------------------------------------------------
# Same-project, different-agent isolation
# ---------------------------------------------------------------------------


class TestSameProjectDifferentAgent:
    """Two agents sharing a project_id must have disjoint views."""

    def test_load_all_scoped_per_agent(self) -> None:
        project_id = _unique_project()
        agent_a = _make_backend(project_id, "agent-a")
        agent_b = _make_backend(project_id, "agent-b")
        try:
            agent_a.save(_make_entry("key-a", "agent-a private memory"))
            agent_b.save(_make_entry("key-b", "agent-b private memory"))

            a_keys = {e.key for e in agent_a.load_all()}
            b_keys = {e.key for e in agent_b.load_all()}

            # Each agent sees only its own entries
            assert "key-a" in a_keys
            assert "key-b" not in a_keys
            assert "key-b" in b_keys
            assert "key-a" not in b_keys
        finally:
            agent_a.close()
            agent_b.close()

    def test_search_scoped_per_agent(self) -> None:
        project_id = _unique_project()
        agent_a = _make_backend(project_id, "search-agent-a")
        agent_b = _make_backend(project_id, "search-agent-b")
        try:
            agent_a.save(_make_entry("canary-a", "canary phrase for agent-a only"))
            agent_b.save(_make_entry("other-b", "different content for agent-b"))

            # Agent B's search must not find Agent A's canary
            b_results = agent_b.search("canary")
            assert not any(e.key == "canary-a" for e in b_results)

            # Agent A's search finds its own canary
            a_results = agent_a.search("canary")
            assert any(e.key == "canary-a" for e in a_results)
        finally:
            agent_a.close()
            agent_b.close()

    def test_delete_scoped_per_agent(self) -> None:
        """Deleting a key for agent A must not affect agent B's entry with the same key."""
        project_id = _unique_project()
        agent_a = _make_backend(project_id, "del-agent-a")
        agent_b = _make_backend(project_id, "del-agent-b")
        try:
            # Both agents save under the same key name
            agent_a.save(_make_entry("shared-key", "agent-a version"))
            agent_b.save(_make_entry("shared-key", "agent-b version"))

            agent_a.delete("shared-key")

            # Agent A's entry is gone
            assert agent_a.load_all() == []
            # Agent B's entry survives
            b_entries = agent_b.load_all()
            assert len(b_entries) == 1
            assert b_entries[0].value == "agent-b version"
        finally:
            agent_a.close()
            agent_b.close()

    def test_many_agents_isolated(self) -> None:
        """N agents in the same project each see only their own entry."""
        project_id = _unique_project()
        n = 5
        backends = [_make_backend(project_id, f"multi-agent-{i}") for i in range(n)]
        try:
            for i, b in enumerate(backends):
                b.save(_make_entry(f"key-{i}", f"memory for agent {i}"))

            for i, b in enumerate(backends):
                entries = b.load_all()
                assert len(entries) == 1, f"agent {i} should see exactly 1 entry"
                assert entries[0].key == f"key-{i}"
        finally:
            for b in backends:
                b.close()


# ---------------------------------------------------------------------------
# Different-project isolation (same agent_id)
# ---------------------------------------------------------------------------


class TestDifferentProjectIsolation:
    """Same agent_id in different projects must be completely isolated."""

    def test_different_projects_no_cross_visibility(self) -> None:
        project_x = _unique_project()
        project_y = _unique_project()
        backend_x = _make_backend(project_x, "same-agent")
        backend_y = _make_backend(project_y, "same-agent")
        try:
            backend_x.save(_make_entry("proj-x-key", "project x memory"))
            backend_y.save(_make_entry("proj-y-key", "project y memory"))

            x_keys = {e.key for e in backend_x.load_all()}
            y_keys = {e.key for e in backend_y.load_all()}

            assert "proj-x-key" in x_keys
            assert "proj-y-key" not in x_keys
            assert "proj-y-key" in y_keys
            assert "proj-x-key" not in y_keys
        finally:
            backend_x.close()
            backend_y.close()

    def test_different_projects_search_isolated(self) -> None:
        project_x = _unique_project()
        project_y = _unique_project()
        backend_x = _make_backend(project_x, "shared-agent-search")
        backend_y = _make_backend(project_y, "shared-agent-search")
        try:
            backend_x.save(_make_entry("xk", "canary for project-x only"))
            backend_y.save(_make_entry("yk", "unrelated content for project-y"))

            results_y = backend_y.search("canary")
            assert results_y == []

            results_x = backend_x.search("canary")
            assert len(results_x) >= 1
        finally:
            backend_x.close()
            backend_y.close()


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_schema_version_positive_int(self) -> None:
        project_id = _unique_project()
        b = _make_backend(project_id, _unique_agent())
        try:
            version = b.get_schema_version()
            assert isinstance(version, int)
            assert version >= 1
        finally:
            b.close()
