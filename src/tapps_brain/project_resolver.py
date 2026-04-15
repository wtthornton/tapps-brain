"""Transport-layer ``project_id`` resolution.

Implements EPIC-069 STORY-069.3.  See
``docs/planning/adr/ADR-010-multi-tenant-project-registration.md``.

Precedence (first non-empty value wins):

1. Per-call override — MCP tool call's ``_meta.project_id`` or an HTTP
   request body field.
2. HTTP header ``X-Tapps-Project`` (Streamable HTTP / SSE transport).
3. Env var ``TAPPS_BRAIN_PROJECT`` (stdio transport — the MCP client
   sets it in ``.mcp.json`` ``env``).
4. The literal ``"default"``.  Strict-mode deployments reject this at
   the registry layer via :class:`ProjectNotRegisteredError`.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = structlog.get_logger(__name__)

DEFAULT_PROJECT_ID = "default"
HEADER_NAME = "X-Tapps-Project"
ENV_VAR = "TAPPS_BRAIN_PROJECT"

# Mirrors the project_profiles_id_shape CHECK constraint (migration 008):
# lowercase alnum + dash/underscore, 1-64 chars, must start with alnum.
_VALID_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class InvalidProjectIdError(ValueError):
    """Raised when a resolved project_id fails the slug shape check."""


def validate_project_id(project_id: str) -> str:
    """Return *project_id* if it matches the slug shape, else raise."""
    if not _VALID_ID.match(project_id):
        msg = (
            f"Invalid project_id '{project_id}': must match "
            f"^[a-z0-9][a-z0-9_-]{{0,63}}$ (lowercase alnum, dash, underscore)."
        )
        raise InvalidProjectIdError(msg)
    return project_id


def resolve_project_id(
    *,
    meta_override: str | None = None,
    headers: Mapping[str, str] | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Resolve the effective project_id for a request.

    Args:
        meta_override: Value from MCP ``_meta.project_id`` or a per-call
            override in an HTTP body.  Takes precedence over everything.
        headers: HTTP request headers (case-insensitive lookup).  May be
            ``None`` for stdio transport.
        env: Process environment (injected for testability).  Defaults to
            :data:`os.environ` when ``None``.

    Returns:
        A validated project_id slug.

    Raises:
        InvalidProjectIdError: if the resolved value fails the slug check.
    """
    candidate = (meta_override or "").strip()
    source = "meta"
    if not candidate and headers is not None:
        candidate = _lookup_header(headers, HEADER_NAME)
        source = "header"
    if not candidate:
        env_map = env if env is not None else os.environ
        candidate = (env_map.get(ENV_VAR) or "").strip()
        source = "env"
    if not candidate:
        candidate = DEFAULT_PROJECT_ID
        source = "default"

    # Only validate non-default values — 'default' is the documented sentinel
    # and we want it to pass the shape check trivially.
    if candidate != DEFAULT_PROJECT_ID:
        validate_project_id(candidate)

    logger.debug(
        "project_resolver.resolved",
        project_id=candidate,
        source=source,
    )
    return candidate


def _lookup_header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup that tolerates both ``dict`` and
    Starlette/Werkzeug-style multi-dict headers (which are already
    case-insensitive but expose a plain ``Mapping`` interface)."""
    lowered = name.lower()
    # Fast path — exact key match.
    value = headers.get(name) or headers.get(lowered)
    if value:
        return value.strip()
    # Slow path — iterate for case-insensitive match.
    for key, val in headers.items():
        if key.lower() == lowered:
            return (val or "").strip()
    return ""
