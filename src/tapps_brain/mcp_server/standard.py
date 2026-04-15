"""Standard MCP server — safe for AGENT.md grants.

STORY-070.9: thin module that exposes only the standard (non-operator) server.
Operator tools are never registered, regardless of environment variables.

Entry point: ``tapps-brain-mcp`` → :func:`main`.
"""

from tapps_brain.mcp_server import create_server, main

__all__ = ["create_server", "main"]
