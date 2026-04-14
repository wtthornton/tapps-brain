"""Pure, transport-agnostic service functions for tapps-brain (EPIC-070 STORY-070.1).

Each service module exposes functions that take ``(store, project_id, agent_id,
**typed_args)`` and return JSON-serialisable Python objects. Transport adapters
(MCP, HTTP, CLI) are responsible for serialisation and request resolution.
"""
