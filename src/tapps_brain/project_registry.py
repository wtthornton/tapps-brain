"""Project profile registry — per-project :class:`MemoryProfile` storage.

Implements EPIC-069 STORY-069.2.  See
``docs/planning/adr/ADR-010-multi-tenant-project-registration.md`` for the
surrounding design.  The registry replaces filesystem-based profile
discovery for deployed/shared brains: one row per ``project_id`` in the
``project_profiles`` table holds the authoritative profile JSON.

Resolution order used by :class:`~tapps_brain.store.MemoryStore` once this
module is wired in (STORY-069.2 continued):

1. Registered row in ``project_profiles`` (any ``approved`` value)
2. Built-in ``repo-brain`` default

Strict mode (``TAPPS_BRAIN_STRICT_PROJECTS=1``) additionally rejects
unknown ``project_id`` values before a ``MemoryStore`` is constructed.
Lax mode auto-creates a row cloned from the built-in default with
``approved=false, source='auto'`` for later admin review.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import structlog

from tapps_brain.postgres_connection import PostgresConnectionManager
from tapps_brain.profile import MemoryProfile, get_builtin_profile

logger = structlog.get_logger(__name__)

_DEFAULT_SEED_PROFILE = "repo-brain"


class ProjectNotRegisteredError(LookupError):
    """Raised in strict mode when ``project_id`` has no registered profile."""

    def __init__(self, project_id: str) -> None:
        super().__init__(
            f"project_id '{project_id}' is not registered. "
            f"Register with: tapps-brain project register {project_id} --profile <file>"
        )
        self.project_id = project_id


@dataclass(frozen=True)
class ProjectRecord:
    """One row of the ``project_profiles`` table."""

    project_id: str
    profile: MemoryProfile
    approved: bool
    source: str  # 'admin' | 'auto' | 'import'
    notes: str


class ProjectRegistry:
    """Read/write access to ``project_profiles``.

    Thin wrapper around a :class:`PostgresConnectionManager` — holds no
    state of its own so it is safe to construct per-request or cache for
    the life of the process.
    """

    def __init__(self, connection_manager: PostgresConnectionManager) -> None:
        self._cm = connection_manager

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, project_id: str) -> ProjectRecord | None:
        """Return the stored record for *project_id*, or ``None`` if absent."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT project_id, profile, approved, source, notes "
                "FROM project_profiles WHERE project_id = %s",
                (project_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        pid, profile_json, approved, source, notes = row
        return ProjectRecord(
            project_id=pid,
            profile=MemoryProfile.model_validate(
                profile_json if isinstance(profile_json, dict) else json.loads(profile_json)
            ),
            approved=bool(approved),
            source=source,
            notes=notes or "",
        )

    def list_all(self, *, approved: bool | None = None) -> list[ProjectRecord]:
        """Return every row, optionally filtered by approval status."""
        sql = (
            "SELECT project_id, profile, approved, source, notes "
            "FROM project_profiles"
        )
        params: tuple[Any, ...] = ()
        if approved is not None:
            sql += " WHERE approved = %s"
            params = (approved,)
        sql += " ORDER BY project_id"
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            ProjectRecord(
                project_id=pid,
                profile=MemoryProfile.model_validate(
                    pj if isinstance(pj, dict) else json.loads(pj)
                ),
                approved=bool(ap),
                source=src,
                notes=nt or "",
            )
            for pid, pj, ap, src, nt in rows
        ]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def register(
        self,
        project_id: str,
        profile: MemoryProfile,
        *,
        source: str = "admin",
        approved: bool = True,
        notes: str = "",
    ) -> ProjectRecord:
        """Insert or overwrite a project's profile.

        Admin-authored registration defaults to ``source='admin',
        approved=True``.  Callers with less trust (auto-registration) must
        pass their own values.
        """
        if source not in {"admin", "auto", "import"}:
            msg = f"Invalid source '{source}' — must be admin|auto|import"
            raise ValueError(msg)

        profile_json = profile.model_dump(mode="json")
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO project_profiles
                    (project_id, profile, approved, source, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (project_id) DO UPDATE SET
                    profile  = EXCLUDED.profile,
                    approved = EXCLUDED.approved,
                    source   = EXCLUDED.source,
                    notes    = EXCLUDED.notes
                """,
                (project_id, json.dumps(profile_json), approved, source, notes),
            )
            conn.commit()
        logger.info(
            "project_registry.registered",
            project_id=project_id,
            source=source,
            approved=approved,
            profile_name=profile.name,
        )
        return ProjectRecord(
            project_id=project_id,
            profile=profile,
            approved=approved,
            source=source,
            notes=notes,
        )

    def approve(self, project_id: str) -> bool:
        """Flip ``approved=true`` on an existing row.  Returns ``True`` if
        a row was updated, ``False`` if the ID was unknown."""
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE project_profiles SET approved = TRUE "
                "WHERE project_id = %s",
                (project_id,),
            )
            updated = cur.rowcount
            conn.commit()
        if updated:
            logger.info("project_registry.approved", project_id=project_id)
        return bool(updated)

    def delete(self, project_id: str) -> bool:
        """Remove a row.  Does **not** cascade to ``private_memories`` —
        callers that want a full purge must delete memory rows explicitly.
        Returns ``True`` if a row was deleted.
        """
        with self._cm.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM project_profiles WHERE project_id = %s",
                (project_id,),
            )
            deleted = cur.rowcount
            conn.commit()
        if deleted:
            logger.warning("project_registry.deleted", project_id=project_id)
        return bool(deleted)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, project_id: str) -> MemoryProfile:
        """Return the profile to use for *project_id*.

        In strict mode (``TAPPS_BRAIN_STRICT_PROJECTS=1``), an unknown
        project raises :class:`ProjectNotRegisteredError`.  In lax mode
        an unknown project is auto-registered with the built-in
        ``repo-brain`` profile and ``approved=false``.
        """
        record = self.get(project_id)
        if record is not None:
            return record.profile

        if _strict_mode_enabled():
            raise ProjectNotRegisteredError(project_id)

        seed = get_builtin_profile(_DEFAULT_SEED_PROFILE)
        logger.info(
            "project.auto_registered",
            project_id=project_id,
            seed=_DEFAULT_SEED_PROFILE,
        )
        self.register(
            project_id,
            seed,
            source="auto",
            approved=False,
            notes="Auto-registered on first connection (lax mode).",
        )
        return seed


def _strict_mode_enabled() -> bool:
    """Read ``TAPPS_BRAIN_STRICT_PROJECTS`` at call time (not import time)
    so tests and admin tools can toggle it per-process."""
    return os.environ.get("TAPPS_BRAIN_STRICT_PROJECTS", "0") == "1"
