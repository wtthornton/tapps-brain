"""Session indexing for searchable past sessions (Epic 65.10).

Stores session chunks (summaries or key facts) in a separate table with FTS5.
Trade-off: more coverage, more noise. Flush prompt quality becomes critical.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from tapps_brain.persistence import MemoryPersistence

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

# Limits per Epic 65.10
_MAX_CHUNKS_DEFAULT = 50
_MAX_CHARS_DEFAULT = 500


def index_session(
    project_root: Path,
    session_id: str,
    chunks: list[str],
    *,
    max_chunks: int = _MAX_CHUNKS_DEFAULT,
    max_chars_per_chunk: int = _MAX_CHARS_DEFAULT,
) -> int:
    """Index session chunks for later search.

    Args:
        project_root: Project root path.
        session_id: Session identifier (e.g. from platform).
        chunks: List of text chunks (summaries or key facts per turn/day).
        max_chunks: Maximum chunks to store per session (default: 50).
        max_chars_per_chunk: Maximum characters per chunk (default: 500).

    Returns:
        Number of chunks stored.
    """
    if not session_id or not session_id.strip():
        logger.warning("session_index_skip_empty_id")
        return 0
    persistence = MemoryPersistence(project_root)
    try:
        count = persistence.save_session_chunks(
            session_id,
            chunks,
            max_chunks=max_chunks,
            max_chars_per_chunk=max_chars_per_chunk,
        )
        logger.debug(
            "session_indexed",
            session_id=session_id,
            chunks_stored=count,
            chunks_input=len(chunks),
        )
        return count
    finally:
        persistence.close()


def search_session_index(
    project_root: Path,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search session index by query.

    Returns list of dicts with keys: session_id, chunk_index, content, created_at.
    """
    if not query or not query.strip():
        return []
    persistence = MemoryPersistence(project_root)
    try:
        return persistence.search_session_index(query, limit=limit)
    finally:
        persistence.close()


def delete_expired_sessions(
    project_root: Path,
    ttl_days: int,
) -> int:
    """Delete session chunks older than ttl_days. Returns count deleted."""
    if ttl_days < 1:
        return 0
    persistence = MemoryPersistence(project_root)
    try:
        return persistence.delete_expired_session_chunks(ttl_days)
    finally:
        persistence.close()
