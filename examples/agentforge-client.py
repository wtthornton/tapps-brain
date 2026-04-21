"""AgentForge -> tapps-brain Streamable HTTP example client (EPIC-070 STORY-070.7).

Demonstrates a remote AgentForge-style agent talking to a docker-compose'd
tapps-brain HTTP adapter via the MCP Streamable HTTP transport.

Run it against a locally deployed brain:

    # 1. Start the unified brain stack (Postgres + migrate + brain + dashboard).
    make hive-deploy       # from the repo root; reads docker/.env

    # 2. Export the bearer token (same value as docker/.env's
    #    TAPPS_BRAIN_AUTH_TOKEN — same file the container reads).
    export TAPPS_BRAIN_AUTH_TOKEN="$(grep ^TAPPS_BRAIN_AUTH_TOKEN= docker/.env | cut -d= -f2)"

    # 3. Run this script.
    python examples/agentforge-client.py

The script performs a full session round-trip:
``initialize -> memory_save -> memory_search -> memory_recall -> close``
and prints each intermediate result.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BRAIN_URL = os.environ.get("TAPPS_BRAIN_URL", "http://localhost:8080/mcp")
AUTH_TOKEN = os.environ.get("TAPPS_BRAIN_AUTH_TOKEN", "")
PROJECT_ID = os.environ.get("TAPPS_BRAIN_PROJECT_ID", "demo-project")
AGENT_ID = os.environ.get("TAPPS_BRAIN_AGENT_ID", "agentforge-example")


def _headers() -> dict[str, str]:
    if not AUTH_TOKEN:
        print(
            "error: TAPPS_BRAIN_AUTH_TOKEN is empty; set it to the bearer token "
            "the brain expects (same value as docker/.env's TAPPS_BRAIN_AUTH_TOKEN).",
            file=sys.stderr,
        )
        sys.exit(2)
    return {
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "X-Project-Id": PROJECT_ID,
        "X-Agent-Id": AGENT_ID,
    }


def _dump(label: str, value: Any) -> None:
    print(f"\n--- {label} ---")
    print(value)


async def main() -> None:
    headers = _headers()
    print(f"connecting to {BRAIN_URL} as project={PROJECT_ID} agent={AGENT_ID}")

    async with streamablehttp_client(BRAIN_URL, headers=headers) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            _dump("initialize", init_result)

            tools = await session.list_tools()
            _dump("tools", [t.name for t in tools.tools])

            save_result = await session.call_tool(
                "memory_save",
                {
                    "content": (
                        "AgentForge example: Streamable HTTP transport is the supported "
                        "remote path for tapps-brain as of EPIC-070."
                    ),
                    "tags": ["agentforge", "epic-070", "example"],
                },
            )
            _dump("memory_save", save_result)

            search_result = await session.call_tool(
                "memory_search",
                {"query": "Streamable HTTP transport", "limit": 3},
            )
            _dump("memory_search", search_result)

            recall_result = await session.call_tool(
                "memory_recall",
                {"query": "EPIC-070 transport", "limit": 3},
            )
            _dump("memory_recall", recall_result)

    print("\nsession closed cleanly")


if __name__ == "__main__":
    asyncio.run(main())
