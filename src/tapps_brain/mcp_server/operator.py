"""Operator MCP server — always exposes maintenance/destructive tools.

STORY-070.9: thin module that exposes the operator server. GC, consolidation,
import/export, migration, and relay tools are always registered.

Do NOT grant this server in a normal agent's AGENT.md — use
``tapps-brain-mcp`` (the standard server) instead.

Entry point: ``tapps-brain-operator-mcp`` → :func:`main_operator`.
"""

from tapps_brain.mcp_server import create_operator_server, main_operator

__all__ = ["create_operator_server", "main_operator"]
