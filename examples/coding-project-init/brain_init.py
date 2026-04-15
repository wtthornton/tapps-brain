"""Runtime AgentBrain factory for a consuming project.

Copy this file into your project and import ``get_brain`` from your agent loop.
Reads configuration from environment variables (see ``.env.example``). This is
intentionally minimal — it is an example, not a framework.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from tapps_brain import AgentBrain


@contextmanager
def get_brain() -> Iterator[AgentBrain]:
    """Yield an initialized AgentBrain bound to the consuming project.

    Environment variables honored:

    - ``TAPPS_BRAIN_AGENT_ID``      — identity for private-memory scoping and Hive attribution.
    - ``TAPPS_BRAIN_PROJECT_DIR``   — project root (defaults to cwd). Derives the stable project_id.
    - ``TAPPS_BRAIN_PROJECT``       — explicit project_id override (multi-tenant deployments).
    - ``TAPPS_BRAIN_HIVE_DSN``      — Postgres DSN of the Hive hub. Omit to disable Hive propagation.
    - ``TAPPS_BRAIN_GROUPS``        — comma-separated group memberships for Hive group scope.
    """
    brain = AgentBrain(
        agent_id=os.environ.get("TAPPS_BRAIN_AGENT_ID") or "my-app-agent",
        project_dir=os.environ.get("TAPPS_BRAIN_PROJECT_DIR"),
        groups=_csv(os.environ.get("TAPPS_BRAIN_GROUPS")),
        hive_dsn=os.environ.get("TAPPS_BRAIN_HIVE_DSN"),
    )
    try:
        yield brain
    finally:
        brain.close()


def _csv(val: str | None) -> list[str] | None:
    if not val:
        return None
    return [s.strip() for s in val.split(",") if s.strip()]


if __name__ == "__main__":
    # Tiny smoke demo — run directly to verify connectivity.
    with get_brain() as brain:
        brain.remember("tapps-brain scaffold smoke-test", tier="scratch")
        results = brain.recall("smoke-test")
        print(results.memory_section or "(no memories found)")
