"""Postgres integration test for tenant-purge helpers (TAP-4465).

Verifies that ``purge_projects`` deletes the rows a test created under its
unique project_id, leaving no residue — the prevention mechanism for the leak
documented in TAP-4465.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (skipped otherwise). Mark: requires_postgres.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_entry(key: str, value: str) -> Any:
    from tapps_brain.models import MemoryEntry

    return MemoryEntry(key=key, value=value)


def test_purge_projects_removes_created_rows() -> None:
    from tapps_brain.maintenance_purge import purge_projects
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    project_id = f"test-purge-{uuid.uuid4().hex[:8]}"
    agent_id = f"test-agent-{uuid.uuid4().hex[:8]}"
    cm = PostgresConnectionManager(_PG_DSN)
    try:
        backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
        backend.save(_make_entry("purge-key-1", "row that should be deleted"))
        backend.save(_make_entry("purge-key-2", "another row to delete"))
        assert len(backend.load_all()) == 2

        deleted = purge_projects(cm, [project_id])
        assert deleted.get("private_memories", 0) >= 2

        # A fresh backend on the same tenant sees no residue.
        backend_after = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
        assert backend_after.load_all() == []
    finally:
        cm.close()


def test_purge_by_prefix_only_targets_reserved_tenants() -> None:
    from tapps_brain.maintenance_purge import purge_by_prefix
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend

    reserved = f"test-prefixpurge-{uuid.uuid4().hex[:8]}"
    cm = PostgresConnectionManager(_PG_DSN)
    try:
        backend = PostgresPrivateBackend(cm, project_id=reserved, agent_id="a")
        backend.save(_make_entry("k", "reserved-prefix row"))
        assert len(backend.load_all()) == 1

        purge_by_prefix(cm, ("test-prefixpurge-",))

        after = PostgresPrivateBackend(cm, project_id=reserved, agent_id="a")
        assert after.load_all() == []
    finally:
        cm.close()
