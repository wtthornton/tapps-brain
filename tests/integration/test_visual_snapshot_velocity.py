"""STORY-078.12 — integration tests for memory velocity in visual snapshots.

Verifies ``_collect_velocity`` and ``build_visual_snapshot`` return non-zero
counts when Postgres has recent write/recall activity.

Requires: ``TAPPS_BRAIN_DATABASE_URL`` (``requires_postgres`` marker).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.requires_postgres

_PG_DSN = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")


def _apply_migrations() -> None:
    from tapps_brain.postgres_migrations import apply_private_migrations

    apply_private_migrations(_PG_DSN)


def _make_store(tmp_path: Path, project_id: str, agent_id: str) -> Any:
    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.postgres_private import PostgresPrivateBackend
    from tapps_brain.store import MemoryStore

    cm = PostgresConnectionManager(_PG_DSN)
    backend = PostgresPrivateBackend(cm, project_id=project_id, agent_id=agent_id)
    return MemoryStore(tmp_path, private_backend=backend)


def _unique_project() -> str:
    return f"test-proj-{uuid.uuid4().hex[:8]}"


def _unique_agent() -> str:
    return f"test-agent-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module", autouse=True)
def _migrate() -> None:
    _apply_migrations()


class TestVisualSnapshotVelocityPostgres:
    def test_collect_velocity_non_zero_after_recent_writes(
        self, tmp_path: Path
    ) -> None:
        """Recent saves produce non-zero writes_1h and writes_24h from Postgres."""
        from tapps_brain.visual_snapshot import _collect_velocity

        store = _make_store(tmp_path, _unique_project(), _unique_agent())
        try:
            for i in range(3):
                store.save(f"vel-write-{i}", f"payload {i}")
            vel = _collect_velocity(store)
        finally:
            store.close()

        assert vel.writes_1h == 3
        assert vel.writes_24h == 3
        assert vel.recalls_1h == 0
        assert vel.recalls_24h == 0

    def test_build_visual_snapshot_velocity_matches_collect(
        self, tmp_path: Path
    ) -> None:
        """build_visual_snapshot velocity block matches _collect_velocity on Postgres."""
        from tapps_brain.visual_snapshot import _collect_velocity, build_visual_snapshot

        store = _make_store(tmp_path, _unique_project(), _unique_agent())
        try:
            store.save("vel-snap", "snapshot velocity check")
            store.get("vel-snap")
            snap = build_visual_snapshot(store, skip_diagnostics=True)
            direct = _collect_velocity(store)
        finally:
            store.close()

        assert snap.velocity.writes_1h == direct.writes_1h == 1
        assert snap.velocity.writes_24h == direct.writes_24h == 1
        assert snap.velocity.recalls_1h == direct.recalls_1h >= 1
        assert snap.velocity.recalls_24h == direct.recalls_24h >= 1

    def test_collect_velocity_zero_on_empty_store(self, tmp_path: Path) -> None:
        """Empty Postgres store returns all-zero velocity without error."""
        from tapps_brain.visual_snapshot import MemoryVelocity, _collect_velocity

        store = _make_store(tmp_path, _unique_project(), _unique_agent())
        try:
            vel = _collect_velocity(store)
        finally:
            store.close()

        assert vel == MemoryVelocity()
