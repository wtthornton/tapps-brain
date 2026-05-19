"""Minimal AsyncTappsBrainClient quickstart (STORY-071.7).

Runs a remember -> recall -> forget round trip against a local Docker brain
to demonstrate the v3.7+ async SDK surface: ``async with`` lifecycle, the
semantic exception taxonomy from STORY-071.1, and opt-in retry from
STORY-071.2.

Run it against a locally deployed brain::

    # 1. Start the unified stack (Postgres + migrate + brain + dashboard).
    make hive-deploy        # from the repo root; reads docker/.env

    # 2. Export the bearer token (same value as docker/.env's
    #    TAPPS_BRAIN_AUTH_TOKEN -- same file the container reads).
    export TAPPS_BRAIN_AUTH_TOKEN="$(grep ^TAPPS_BRAIN_AUTH_TOKEN= docker/.env | cut -d= -f2)"
    export TAPPS_BRAIN_PROJECT="tapps-brain"
    export TAPPS_BRAIN_AGENT_ID="quickstart-example"

    # 3. Run this script.
    python examples/client_quickstart.py

See ``docs/guides/client.md`` for the full integrator guide.
"""

from __future__ import annotations

import asyncio
import os
import sys

from tapps_brain.client import AsyncTappsBrainClient, RetryConfig
from tapps_brain.exceptions import (
    TappsBrainAuthError,
    TappsBrainTransientError,
    TappsBrainTransportError,
)


async def main() -> int:
    url = os.environ.get("TAPPS_BRAIN_URL", "http://127.0.0.1:8080")
    auth_token = os.environ.get("TAPPS_BRAIN_AUTH_TOKEN", "")
    project_id = os.environ.get("TAPPS_BRAIN_PROJECT", "tapps-brain")
    agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "quickstart-example")

    # Retries off by default; opt in with RetryConfig() so the round trip
    # survives a single transient blip from the brain (3 attempts, 0.5s base,
    # +-20% jitter, 30s cap -- see docs/guides/client.md#retry).
    async with AsyncTappsBrainClient(
        url,
        project_id=project_id,
        agent_id=agent_id,
        auth_token=auth_token,
        retry_config=RetryConfig(),
    ) as brain:
        try:
            key = await brain.remember(
                "Use ruff for linting in tapps-brain.",
                tier="procedural",
            )
            print(f"remember -> key={key}")

            results = await brain.recall("ruff linting", max_results=3)
            print(f"recall   -> {len(results)} hit(s)")
            for hit in results:
                print(f"  - {hit.get('key', '?')}: {hit.get('value', '')[:80]}")

            forgotten = await brain.forget(key)
            print(f"forget   -> {forgotten}")
        except TappsBrainAuthError as exc:
            print(f"auth error: {exc}. Check TAPPS_BRAIN_AUTH_TOKEN and project_id.")
            return 2
        except TappsBrainTransientError as exc:
            # The retry layer already burned through RetryConfig's budget.
            # In a real agent: alert and back off; here we just exit non-zero.
            print(f"brain transient failure after retries: {exc}")
            return 3
        except TappsBrainTransportError as exc:
            print(f"brain unreachable: {exc}. Is the docker stack up on {url}?")
            return 4

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
