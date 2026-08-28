"""Disposable local Postgres fixture for retention-SLO tests (TAP-6698).

Lane policy for ``tests/test_retention_slo.py`` and ``tests/test_maintenance_cycle.py``
is skip-free: unlike the rest of the suite (which skips ``requires_postgres``
tests when ``TAPPS_BRAIN_DATABASE_URL`` is unset — see ``tests/conftest.py``),
these tests must run against a real Postgres one way or another. CI already
provisions one as a compose service (``.github/workflows/ci.yml``); locally,
this module starts a throwaway ``pgvector/pgvector:pg17`` container distinct
from the deployed ``tapps-brain-db`` and tears it down after the session.

Never points at ``tapps-brain-db`` — SC-6/guardrails forbid apply-mode writes
against the live deployed brain from this lane, and these tests write rows.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time

import pytest

_CONTAINER_NAME = "tapps-brain-retention-slo-fixture"
_FIXTURE_PASSWORD = "tapps-fixture-only"  # local disposable container, never real credentials
_FIXTURE_DB = "tapps_brain_fixture"
_FIXTURE_USER = "postgres"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _wait_for_postgres(dsn: str, *, timeout_s: float = 30.0) -> None:
    import psycopg

    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
            return
        except Exception as exc:  # retry loop: any connect failure just waits and retries
            last_exc = exc
            time.sleep(0.5)
    raise RuntimeError(f"fixture Postgres never became ready: {last_exc}")


def _start_fixture_container() -> str:
    port = _free_port()
    subprocess.run(["docker", "rm", "-f", _CONTAINER_NAME], capture_output=True, check=False)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            _CONTAINER_NAME,
            "-e",
            f"POSTGRES_PASSWORD={_FIXTURE_PASSWORD}",
            "-e",
            f"POSTGRES_DB={_FIXTURE_DB}",
            "-p",
            f"127.0.0.1:{port}:5432",
            "pgvector/pgvector:pg17",
        ],
        check=True,
        capture_output=True,
    )
    dsn = f"postgresql://{_FIXTURE_USER}:{_FIXTURE_PASSWORD}@127.0.0.1:{port}/{_FIXTURE_DB}"
    _wait_for_postgres(dsn)
    from tapps_brain.postgres_migrations import apply_hive_migrations, apply_private_migrations

    apply_private_migrations(dsn)
    apply_hive_migrations(dsn)  # hive_memories — namespace_reaper reads it
    return dsn


def resolve_fixture_dsn() -> str:
    """A live Postgres DSN, migrated, for retention-SLO tests. Never skips.

    Prefers an already-provisioned DSN (CI's compose service); falls back to
    a throwaway local container. Fails loudly (not a skip) if neither is
    available — SC-5 / lane policy forbid going green by omission here.

    Not itself a pytest fixture — callers wrap it in a session-scoped fixture
    (see ``tests/test_retention_slo.py`` / ``tests/test_maintenance_cycle.py``)
    so this stays a plain, directly-testable function.
    """
    dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL") or os.environ.get("TAPPS_TEST_POSTGRES_DSN")
    if dsn:
        return dsn
    if not _docker_available():
        pytest.fail(
            "TAPPS_BRAIN_DATABASE_URL/TAPPS_TEST_POSTGRES_DSN are unset and docker is "
            "unavailable to start a disposable fixture Postgres. This suite is skip-free "
            "by lane policy (TAP-6698) — provide a DSN or docker, not a skip."
        )
    started_dsn = _start_fixture_container()

    def _teardown() -> None:
        subprocess.run(["docker", "rm", "-f", _CONTAINER_NAME], capture_output=True, check=False)

    import atexit

    atexit.register(_teardown)
    return started_dsn
