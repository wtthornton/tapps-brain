"""Tenancy migration for the S3 population (TAP-7279, decision on TAP-7260).

**S3 population** — rows that arrived under a throwaway or misattributed
tenant identity rather than a real project/agent:

    private_memories  WHERE project_id IN ('default','api','main','repo-brain')
                          OR agent_id IN ('default','unknown')
    private_relations WHERE <the same predicate on its tenant columns>

Each row is classified by provenance, first rule to fire wins:

- **R1** ``audit_log`` — the earliest ``save``/``remember`` audit event for the
  same ``key`` names a REGISTERED, APPROVED project (``project_profiles.approved
  = true``) -> re-home there.  ``private_relations`` has no ``key`` column to
  join ``audit_log`` on, so R1 never fires for it (see module note below).
- **R2** ``source_agent`` — names a registered approved project, or a known
  agent of one, via a caller-supplied agent->project map (absent map -> rule
  skipped, stated in the plan's ``rule_config``).  ``private_relations`` has no
  ``source_agent`` column, so R2 never fires for it either.
- **R3** ingest key shape — ``mem-<slug>-<hash>`` keys written by ``/ingest``
  for ``nlt-ideas-scout`` (TAP-7232), ``source_agent='unknown'``, inside a
  caller-supplied window measured on ``created_at`` -> keep project, agent_id
  -> ``'ingest'``.  Absent window -> rule skipped, stated.  Structurally
  inapplicable to ``private_relations`` (no ``key``/``source_agent``).
- **R4** legacy unattributed — row is under a REAL project (project_id not one
  of the four S3 placeholders) with ``agent_id`` in ``('default','unknown')``
  and no rule above fired -> keep project, agent_id -> ``'legacy-unattributed'``
  (the decision on TAP-7260).  Applies to both tables.
- **R5** everything else -> archive.  Applies to both tables.

Because ``private_relations`` carries no ``source_agent``/``key`` columns, R1-R3
are structurally inapplicable to it; only R4/R5 ever fire for that table, and
its primary-key identity for collision purposes is the
``(subject, predicate, object_entity)`` triple (it has no ``updated_at``, so
collisions there are broken by ``created_at`` instead — see :func:`apply_plan`).

**R1/R2 change project_id only** — the brief does not direct them to also
rewrite ``agent_id``, so a row re-homed by R1/R2 can still carry
``agent_id in ('default', 'unknown')`` after the move, and would still match
the S3 *predicate* on that clause alone.  That is exactly why every mutation in
this module is driven by the row identities captured *before* any write, never
by re-evaluating the boolean predicate against already-mutated rows — see the
identity-tuple design note on :func:`apply_plan`.  A second run of this tool
against the same database will pick such a row up again and this time land it
on R4 (project_id is no longer an S3 placeholder), which is an intentional,
idempotent multi-pass property, not a bug.

**Deferred tables** (never touched, only counted): ``audit_log``,
``experience_events``, ``feedback_events``, ``kg_entities``, ``kg_edges``,
``kg_evidence``, ``kg_aliases``, ``kg_predicates``, ``gc_archive``,
``hive_memories`` (scope ruling S3; a successor story owns them, see
``deferred_tables`` in the plan).

CLI::

    python -m tapps_brain.maintenance.tenancy_migrate \\
        --dsn postgresql://... --dry-run [--plan-out path.json] \\
        [--agent-project-map path.json] \\
        [--ingest-window-start 2026-06-01T00:00:00+00:00 \\
         --ingest-window-end 2026-06-30T23:59:59+00:00]

    python -m tapps_brain.maintenance.tenancy_migrate \\
        --dsn postgresql://... --apply --plan plan.json --archive-table tenancy_migration_2026_09_09

Connects directly with ``psycopg`` (no ``PrivateBackend``/RLS session scoping)
because ``private_memories`` has FORCE ROW LEVEL SECURITY with **no**
admin-bypass policy (``migrations/private/012_rls_force.sql``) — this tool is
a maintenance script in the same class as the migration runner, and must be
pointed at a DSN that authenticates as a role with ``BYPASSRLS`` (or the table
owner without FORCE), never the application's ``tapps_runtime`` role.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Sequence

    import psycopg
    from psycopg.sql import Identifier

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# S3 predicate
# ---------------------------------------------------------------------------

#: Placeholder/throwaway project ids that are never a real tenant.
S3_PROJECT_IDS: frozenset[str] = frozenset({"default", "api", "main", "repo-brain"})

#: Placeholder/unattributed agent ids.
S3_AGENT_IDS: frozenset[str] = frozenset({"default", "unknown"})

#: Tables this tool re-homes/archives. Both carry (project_id, agent_id) tenant columns.
MIGRATED_TABLES: tuple[str, ...] = ("private_memories", "private_relations")

#: Tables that carry rows matching the S3 predicate but are out of scope for this
#: story (scope ruling S3) — counted in the plan's ``deferred_tables``, never written.
DEFERRED_TABLES: tuple[str, ...] = (
    "audit_log",
    "experience_events",
    "feedback_events",
    "kg_entities",
    "kg_edges",
    "kg_evidence",
    "kg_aliases",
    "kg_predicates",
    "gc_archive",
    "hive_memories",
)

#: ``mem-<slug>-<hash>`` key shape written by ``/ingest`` for nlt-ideas-scout (TAP-7232).
_INGEST_KEY_RE = re.compile(r"^mem-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?-[0-9a-f]{6,}$")

#: Archive table name shape (used for both the caller-supplied base name and its
#: ``_relations``-suffixed sibling) — mirrors ``project_profiles_id_shape``'s
#: slug guard (migrations/private/008_project_profiles.sql) so a hostile
#: ``--archive-table`` value cannot be used for SQL injection via table naming.
_ARCHIVE_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class TenancyMigrationError(RuntimeError):
    """Base class for tenancy-migration refusals."""


class EmptyEnumerationError(TenancyMigrationError):
    """Raised when the S3 predicate matches zero rows across every table."""


class StalePlanError(TenancyMigrationError):
    """Raised when ``--apply``'s live counts no longer match the plan's."""


class ApplyIntegrityError(TenancyMigrationError):
    """Raised when post-apply verification (live + archive == pre-apply total) fails."""


class InvalidArchiveTableNameError(TenancyMigrationError):
    """Raised when ``--archive-table`` is not a safe SQL identifier."""


# ---------------------------------------------------------------------------
# Row identity
# ---------------------------------------------------------------------------

#: A row's identity within its table: ``(key,)`` for private_memories,
#: ``(subject, predicate, object_entity)`` for private_relations.
RowId = tuple[str, ...]


@dataclass(frozen=True)
class MemoryRow:
    """One enumerated ``private_memories`` row (the columns this tool needs)."""

    project_id: str
    agent_id: str
    key: str
    created_at: str
    updated_at: str
    source_agent: str
    tags: list[str] = field(default_factory=list)

    @property
    def row_id(self) -> RowId:
        return (self.key,)


@dataclass(frozen=True)
class RelationRow:
    """One enumerated ``private_relations`` row."""

    project_id: str
    agent_id: str
    subject: str
    predicate: str
    object_entity: str
    created_at: str

    @property
    def row_id(self) -> RowId:
        return (self.subject, self.predicate, self.object_entity)


@dataclass(frozen=True)
class Classification:
    """The outcome of classifying one row."""

    rule: str  # "R1".."R5"
    action: str  # "re_home" | "archive"
    target_project: str | None = None
    target_agent: str | None = None


# ---------------------------------------------------------------------------
# Rule functions — each named, pure, and independently unit-tested.
# ---------------------------------------------------------------------------


def classify_r1_audit_log(
    key: str,
    *,
    audit_earliest_project: dict[str, str],
    approved_projects: frozenset[str],
) -> str | None:
    """R1: earliest save/remember audit event for *key* names an approved project.

    ``audit_earliest_project`` maps key -> the project_id of the earliest
    ``save``/``remember`` ``audit_log`` event for that key (already the
    earliest, regardless of project — see :func:`_load_audit_earliest_project`).
    Returns that project only when it is registered and approved; otherwise
    the rule does not fire (falls through to R2+), even if some *later* event
    for the same key named an approved project.
    """
    project = audit_earliest_project.get(key)
    if project is not None and project in approved_projects:
        return project
    return None


def classify_r2_source_agent(
    source_agent: str,
    *,
    agent_project_map: dict[str, str] | None,
    approved_projects: frozenset[str],
) -> str | None:
    """R2: ``source_agent`` names a registered approved project or a known agent of one.

    ``agent_project_map`` is supplied by the caller (the driver, from
    AgentForge's projects/agents tables) — absent map means the rule is
    structurally skipped for every row, not silently no-op per row.
    """
    if not agent_project_map:
        return None
    project = agent_project_map.get(source_agent)
    if project is not None and project in approved_projects:
        return project
    return None


def classify_r3_ingest_window(
    row: MemoryRow,
    *,
    ingest_window: tuple[str, str] | None,
) -> bool:
    """R3: ``mem-<slug>-<hash>`` /ingest keys for nlt-ideas-scout inside the measured window.

    ``created_at`` is the attribution column here (never ``updated_at`` —
    learning transitions bump ``updated_at`` on old rows and would misdate
    an entry that was actually written outside TAP-7232's ingest run).
    """
    if ingest_window is None:
        return False
    if row.project_id != "nlt-ideas-scout":
        return False
    if row.source_agent != "unknown":
        return False
    if not _INGEST_KEY_RE.match(row.key):
        return False
    start, end = ingest_window
    return start <= row.created_at <= end


def classify_r4_legacy_unattributed(project_id: str, agent_id: str) -> bool:
    """R4: real project (not an S3 placeholder) with an unattributed agent_id."""
    return project_id not in S3_PROJECT_IDS and agent_id in S3_AGENT_IDS


def classify_memory_row(
    row: MemoryRow,
    *,
    approved_projects: frozenset[str],
    audit_earliest_project: dict[str, str],
    agent_project_map: dict[str, str] | None,
    ingest_window: tuple[str, str] | None,
) -> Classification:
    """Apply R1-R5, in order, to one ``private_memories`` row."""
    target = classify_r1_audit_log(
        row.key, audit_earliest_project=audit_earliest_project, approved_projects=approved_projects
    )
    if target is not None:
        return Classification(
            rule="R1", action="re_home", target_project=target, target_agent=row.agent_id
        )

    target = classify_r2_source_agent(
        row.source_agent, agent_project_map=agent_project_map, approved_projects=approved_projects
    )
    if target is not None:
        return Classification(
            rule="R2", action="re_home", target_project=target, target_agent=row.agent_id
        )

    if classify_r3_ingest_window(row, ingest_window=ingest_window):
        return Classification(
            rule="R3", action="re_home", target_project=row.project_id, target_agent="ingest"
        )

    if classify_r4_legacy_unattributed(row.project_id, row.agent_id):
        return Classification(
            rule="R4",
            action="re_home",
            target_project=row.project_id,
            target_agent="legacy-unattributed",
        )

    return Classification(rule="R5", action="archive")


def classify_relation_row(row: RelationRow) -> Classification:
    """Apply R4/R5 to one ``private_relations`` row (R1-R3 are structurally inapplicable)."""
    if classify_r4_legacy_unattributed(row.project_id, row.agent_id):
        return Classification(
            rule="R4",
            action="re_home",
            target_project=row.project_id,
            target_agent="legacy-unattributed",
        )
    return Classification(rule="R5", action="archive")


# ---------------------------------------------------------------------------
# Plan / apply-result schema
# ---------------------------------------------------------------------------


class PlanGroup(BaseModel):
    """One (old tenant, rule, outcome) bucket within a table's plan."""

    old_project: str
    old_agent: str
    rule: str
    action: str
    target_project: str | None = None
    target_agent: str | None = None
    count: int
    row_ids: list[list[str]] = Field(default_factory=list)


class CollisionRecord(BaseModel):
    """A predicted (or, post-apply, resolved) primary-key collision."""

    old_project: str
    old_agent: str
    target_project: str
    target_agent: str
    row_id: list[str]
    source_timestamp: str
    existing_timestamp: str
    winner: str  # "source" | "existing"


class TablePlan(BaseModel):
    predicate: str
    live_count: int
    groups: list[PlanGroup] = Field(default_factory=list)
    collisions: list[CollisionRecord] = Field(default_factory=list)
    total_re_home: int = 0
    total_archive: int = 0


class DeferredTableCount(BaseModel):
    table: str
    count: int | None = None
    note: str = ""


class MigrationPlan(BaseModel):
    generated_at: str
    tables: dict[str, TablePlan]
    deferred_tables: list[DeferredTableCount] = Field(default_factory=list)
    rule_config: dict[str, Any] = Field(default_factory=dict)


class ApplyTableResult(BaseModel):
    pre_apply_total: int
    re_homed_count: int
    #: Rows captured in the pre-apply archive-table snapshot — always equals
    #: ``pre_apply_total`` by construction. A "source wins" collision archives
    #: one *additional* physical row (the losing pre-existing target row, which
    #: was never part of the S3 population) into the same archive table; that
    #: extra row is counted in ``collisions``, not here — see
    #: :func:`apply_plan`'s docstring.
    archived_count: int
    collisions: int
    remaining_after: int


class ApplyResult(BaseModel):
    applied_at: str
    archive_table: str
    tables: dict[str, ApplyTableResult]


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

_MEMORIES_PREDICATE_SQL = "(project_id = ANY(%(project_ids)s) OR agent_id = ANY(%(agent_ids)s))"
_RELATIONS_PREDICATE_SQL = _MEMORIES_PREDICATE_SQL

_PREDICATE_PARAMS: dict[str, Any] = {
    "project_ids": sorted(S3_PROJECT_IDS),
    "agent_ids": sorted(S3_AGENT_IDS),
}


def _predicate_params() -> dict[str, Any]:
    return dict(_PREDICATE_PARAMS)


def _scalar_count(cur: Any) -> int:
    """``int(cur.fetchone()[0])``, tolerant of a driver returning no row at all."""
    row = cur.fetchone()
    return int(row[0]) if row else 0


def table_exists(conn: psycopg.Connection, table: str) -> bool:
    """Whether *table* exists in the ``public`` schema (read-only).

    ``private_relations`` is created lazily on first use
    (``PostgresPrivateBackend._ensure_relations_table``), not by a versioned
    migration — a genuinely untouched database has ``private_memories`` (from
    migration 001) but no ``private_relations`` at all. Every caller that
    might touch ``private_relations`` checks this first instead of letting a
    bare ``SELECT``/``CREATE TABLE AS SELECT`` fail with ``UndefinedTable``.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        return cur.fetchone() is not None


def live_predicate_count(conn: psycopg.Connection, table: str) -> int:
    """``SELECT count(*)`` of the S3 predicate against *table* (read-only).

    Returns 0 without querying when *table* does not exist yet.
    """
    if not table_exists(conn, table):
        return 0
    predicate = _MEMORIES_PREDICATE_SQL if table == "private_memories" else _RELATIONS_PREDICATE_SQL
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {table} WHERE {predicate}",  # nosec B608 — table is a module constant, never user input
            _predicate_params(),
        )
        return _scalar_count(cur)


def enumerate_memory_rows(conn: psycopg.Connection) -> list[MemoryRow]:
    """Enumerate every S3-predicate row in ``private_memories`` (read-only)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT project_id, agent_id, key, created_at, updated_at, source_agent, tags "
            f"FROM private_memories WHERE {_MEMORIES_PREDICATE_SQL} "
            "ORDER BY project_id, agent_id, key",
            _predicate_params(),
        )
        rows = cur.fetchall()
    out: list[MemoryRow] = []
    for project_id, agent_id, key, created_at, updated_at, source_agent, tags in rows:
        out.append(
            MemoryRow(
                project_id=project_id,
                agent_id=agent_id,
                key=key,
                created_at=_iso(created_at),
                updated_at=_iso(updated_at),
                source_agent=source_agent,
                tags=_parse_jsonb_list(tags),
            )
        )
    return out


def _parse_jsonb_list(raw: Any) -> list[str]:
    """Parse a JSONB column value into a ``list[str]``.

    Mirrors ``postgres_private._parse_jsonb_list``: psycopg normally returns
    an already-parsed list for a jsonb column, but a TEXT-column fallback or
    a NULL must not crash enumeration.
    """
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    return []


def enumerate_relation_rows(conn: psycopg.Connection) -> list[RelationRow]:
    """Enumerate every S3-predicate row in ``private_relations`` (read-only).

    Returns ``[]`` without querying when the table does not exist yet (see
    :func:`table_exists`).
    """
    if not table_exists(conn, "private_relations"):
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT project_id, agent_id, subject, predicate, object_entity, created_at "
            f"FROM private_relations WHERE {_RELATIONS_PREDICATE_SQL} "
            "ORDER BY project_id, agent_id, subject, predicate, object_entity",
            _predicate_params(),
        )
        rows = cur.fetchall()
    out: list[RelationRow] = []
    for project_id, agent_id, subject, predicate, object_entity, created_at in rows:
        out.append(
            RelationRow(
                project_id=project_id,
                agent_id=agent_id,
                subject=subject,
                predicate=predicate,
                object_entity=object_entity,
                created_at=_iso(created_at),
            )
        )
    return out


def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def load_approved_projects(conn: psycopg.Connection) -> frozenset[str]:
    """Registered, approved project ids (``project_profiles.approved = true``)."""
    with conn.cursor() as cur:
        cur.execute("SELECT project_id FROM project_profiles WHERE approved = true")
        return frozenset(row[0] for row in cur.fetchall())


def _load_audit_earliest_project(conn: psycopg.Connection, keys: list[str]) -> dict[str, str]:
    """The earliest ``save``/``remember`` audit event's project_id, per key.

    Read-only. Empty ``keys`` short-circuits without a query.
    """
    if not keys:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (key) key, project_id "
            "FROM audit_log "
            "WHERE key = ANY(%(keys)s) AND event_type IN ('save', 'remember') "
            "ORDER BY key, timestamp ASC",
            {"keys": keys},
        )
        return dict(cur.fetchall())


def _table_has_columns(conn: psycopg.Connection, table: str, columns: tuple[str, ...]) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s AND column_name = ANY(%s)",
            (table, list(columns)),
        )
        present = {row[0] for row in cur.fetchall()}
    return present == set(columns)


def deferred_table_counts(conn: psycopg.Connection) -> list[DeferredTableCount]:
    """S3-predicate counts for the tables this tool never writes (read-only).

    A table without both ``project_id`` and ``agent_id`` columns cannot carry
    the S3 predicate at all and is reported with ``count=None`` rather than
    silently omitted or mis-queried.
    """
    out: list[DeferredTableCount] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(DEFERRED_TABLES),),
        )
        existing = {row[0] for row in cur.fetchall()}
    for table in DEFERRED_TABLES:
        if table not in existing:
            out.append(DeferredTableCount(table=table, count=None, note="table not present"))
            continue
        if not _table_has_columns(conn, table, ("project_id", "agent_id")):
            out.append(
                DeferredTableCount(
                    table=table,
                    count=None,
                    note="no project_id/agent_id columns; S3 predicate inapplicable",
                )
            )
            continue
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE {_MEMORIES_PREDICATE_SQL}",  # nosec B608 — table from a fixed allow-list, never user input
                _predicate_params(),
            )
            row = cur.fetchone()
        out.append(DeferredTableCount(table=table, count=int(row[0]) if row else 0))
    return out


# ---------------------------------------------------------------------------
# Plan construction (dry run) — read-only.
# ---------------------------------------------------------------------------


def build_plan(
    conn: psycopg.Connection,
    *,
    agent_project_map: dict[str, str] | None = None,
    ingest_window: tuple[str, str] | None = None,
) -> MigrationPlan:
    """Enumerate, classify, and group the S3 population. Issues only SELECTs.

    Raises :class:`EmptyEnumerationError` when both tables enumerate zero rows
    — a migration that finds nothing is a wrong DSN or a wrong predicate, never
    a clean result.
    """
    approved_projects = load_approved_projects(conn)

    memory_rows = enumerate_memory_rows(conn)
    relation_rows = enumerate_relation_rows(conn)

    if not memory_rows and not relation_rows:
        raise EmptyEnumerationError(
            "S3 predicate matched zero rows in private_memories and private_relations. "
            "This is a wrong --dsn or a wrong predicate, never a clean result."
        )

    memories_live_count = live_predicate_count(conn, "private_memories")
    if memories_live_count != len(memory_rows):
        raise TenancyMigrationError(
            f"private_memories: enumerated {len(memory_rows)} rows but live SELECT count(*) "
            f"reports {memories_live_count} — rows changed mid-enumeration; refusing to plan."
        )
    relations_live_count = live_predicate_count(conn, "private_relations")
    if relations_live_count != len(relation_rows):
        raise TenancyMigrationError(
            f"private_relations: enumerated {len(relation_rows)} rows but live SELECT count(*) "
            f"reports {relations_live_count} — rows changed mid-enumeration; refusing to plan."
        )

    audit_earliest_project = _load_audit_earliest_project(conn, [row.key for row in memory_rows])

    memory_classified = [
        (
            row,
            classify_memory_row(
                row,
                approved_projects=approved_projects,
                audit_earliest_project=audit_earliest_project,
                agent_project_map=agent_project_map,
                ingest_window=ingest_window,
            ),
        )
        for row in memory_rows
    ]
    relation_classified = [(row, classify_relation_row(row)) for row in relation_rows]

    memories_table_plan = _build_table_plan(
        conn,
        table="private_memories",
        live_count=memories_live_count,
        classified=memory_classified,
    )
    relations_table_plan = _build_table_plan(
        conn,
        table="private_relations",
        live_count=relations_live_count,
        classified=relation_classified,
    )

    return MigrationPlan(
        generated_at=datetime.now(tz=UTC).isoformat(),
        tables={
            "private_memories": memories_table_plan,
            "private_relations": relations_table_plan,
        },
        deferred_tables=deferred_table_counts(conn),
        rule_config={
            "approved_projects": sorted(approved_projects),
            "s3_project_ids": sorted(S3_PROJECT_IDS),
            "s3_agent_ids": sorted(S3_AGENT_IDS),
            "agent_project_map_provided": bool(agent_project_map),
            "ingest_window": list(ingest_window) if ingest_window else None,
            "r2_skipped": not agent_project_map,
            "r3_skipped": ingest_window is None,
        },
    )


def _build_table_plan(
    conn: psycopg.Connection,
    *,
    table: str,
    live_count: int,
    classified: Sequence[tuple[MemoryRow | RelationRow, Classification]],
) -> TablePlan:
    groups: dict[tuple[str, str, str, str, str | None, str | None], list[RowId]] = {}
    for row, cls in classified:
        group_key = (
            row.project_id,
            row.agent_id,
            cls.rule,
            cls.action,
            cls.target_project,
            cls.target_agent,
        )
        groups.setdefault(group_key, []).append(row.row_id)

    plan_groups: list[PlanGroup] = []
    total_re_home = 0
    total_archive = 0
    for (
        old_project,
        old_agent,
        rule,
        action,
        target_project,
        target_agent,
    ), row_ids in groups.items():
        plan_groups.append(
            PlanGroup(
                old_project=old_project,
                old_agent=old_agent,
                rule=rule,
                action=action,
                target_project=target_project,
                target_agent=target_agent,
                count=len(row_ids),
                row_ids=[list(r) for r in row_ids],
            )
        )
        if action == "re_home":
            total_re_home += len(row_ids)
        else:
            total_archive += len(row_ids)

    collisions = _detect_collisions(conn, table=table, groups=plan_groups)

    return TablePlan(
        predicate=_MEMORIES_PREDICATE_SQL,
        live_count=live_count,
        groups=plan_groups,
        collisions=collisions,
        total_re_home=total_re_home,
        total_archive=total_archive,
    )


def _detect_collisions(
    conn: psycopg.Connection, *, table: str, groups: list[PlanGroup]
) -> list[CollisionRecord]:
    """Join each re-home group's target identity against the live table (read-only)."""
    collisions: list[CollisionRecord] = []
    for group in groups:
        if group.action != "re_home" or group.target_project is None or group.target_agent is None:
            continue
        for row_id in group.row_ids:
            existing = _fetch_row_timestamp(
                conn,
                table=table,
                project_id=group.target_project,
                agent_id=group.target_agent,
                row_id=row_id,
            )
            if existing is None:
                continue
            source_ts = _fetch_row_timestamp(
                conn,
                table=table,
                project_id=group.old_project,
                agent_id=group.old_agent,
                row_id=row_id,
            )
            source_ts = source_ts or ""
            winner = "source" if source_ts >= existing else "existing"
            collisions.append(
                CollisionRecord(
                    old_project=group.old_project,
                    old_agent=group.old_agent,
                    target_project=group.target_project,
                    target_agent=group.target_agent,
                    row_id=list(row_id),
                    source_timestamp=source_ts,
                    existing_timestamp=existing,
                    winner=winner,
                )
            )
    return collisions


def _fetch_row_timestamp(
    conn: psycopg.Connection, *, table: str, project_id: str, agent_id: str, row_id: list[str]
) -> str | None:
    timestamp_col = "updated_at" if table == "private_memories" else "created_at"
    if table == "private_memories":
        (key,) = row_id
        sql_text = (
            f"SELECT {timestamp_col} FROM private_memories "  # nosec B608 — table/column are fixed constants
            "WHERE project_id = %s AND agent_id = %s AND key = %s"
        )
        params: tuple[str, ...] = (project_id, agent_id, key)
    else:
        subject, predicate, object_entity = row_id
        sql_text = (
            f"SELECT {timestamp_col} FROM private_relations "  # nosec B608
            "WHERE project_id = %s AND agent_id = %s AND subject = %s "
            "AND predicate = %s AND object_entity = %s"
        )
        params = (project_id, agent_id, subject, predicate, object_entity)
    with conn.cursor() as cur:
        cur.execute(sql_text, params)
        row = cur.fetchone()
    if row is None:
        return None
    return _iso(row[0])


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _validate_archive_table_name(name: str) -> None:
    if not _ARCHIVE_TABLE_NAME_RE.match(name):
        raise InvalidArchiveTableNameError(
            f"--archive-table {name!r} is not a safe identifier "
            f"(must match {_ARCHIVE_TABLE_NAME_RE.pattern!r})"
        )


def apply_plan(conn: psycopg.Connection, plan: MigrationPlan, *, archive_table: str) -> ApplyResult:
    """Apply *plan* in one transaction. Refuses on a stale plan or a failed verify.

    Issues no explicit ``BEGIN``/``COMMIT``/``ROLLBACK`` itself: *conn* must be a
    non-autocommit connection (psycopg3's default), so every statement below
    shares the one implicit transaction psycopg opens on the first execute.
    The caller is responsible for committing on success and rolling back on
    a raised :class:`TenancyMigrationError` — using *conn* as a context
    manager (``with psycopg.connect(dsn) as conn:``) does exactly that, which
    is what :func:`main` does.

    **Row identity, not predicate re-evaluation.** Every write and every delete
    in this function addresses rows by the ``(project_id, agent_id, row_id)``
    identity captured in the plan — never by re-running the S3 boolean
    predicate against the table mid-apply. R1/R2 re-homed rows can still
    satisfy the S3 predicate's ``agent_id`` clause after their ``project_id``
    changes (see the module docstring); a delete step that re-evaluated the
    predicate post-mutation would wrongly delete a row this same apply just
    correctly re-homed. Addressing by identity sidesteps that entirely: a
    winning re-homed row's *old* identity no longer exists in the table (the
    UPDATE moved it), so a delete keyed on old identities is a no-op for it.

    **Collision policy.** For each re-home target identity that already has a
    live row, the newer of (source row, existing target row) wins — ties go to
    the source row. ``private_memories`` breaks ties on ``updated_at``;
    ``private_relations`` has no ``updated_at`` column, so it uses
    ``created_at`` instead (documented deviation from the brief's literal
    ``updated_at`` wording, forced by that table's schema). The loser is
    archived and removed; the winner ends up at the target identity. Every
    collision increments ``collisions`` in the result, counted separately from
    ``re_homed_count``/``archived_count``.
    """
    _validate_archive_table_name(archive_table)
    relations_archive_table = f"{archive_table}_relations"
    _validate_archive_table_name(relations_archive_table)

    from psycopg import sql

    results: dict[str, ApplyTableResult] = {}

    results["private_memories"] = _apply_table(
        conn,
        table="private_memories",
        table_plan=plan.tables["private_memories"],
        archive_table_ident=sql.Identifier(archive_table),
    )
    results["private_relations"] = _apply_table(
        conn,
        table="private_relations",
        table_plan=plan.tables["private_relations"],
        archive_table_ident=sql.Identifier(relations_archive_table),
    )

    return ApplyResult(
        applied_at=datetime.now(tz=UTC).isoformat(),
        archive_table=archive_table,
        tables=results,
    )


def _apply_table(
    conn: psycopg.Connection,
    *,
    table: str,
    table_plan: TablePlan,
    archive_table_ident: Identifier,
) -> ApplyTableResult:
    from psycopg import sql

    source_exists = table_exists(conn, table)
    pre_apply_total = live_predicate_count(conn, table) if source_exists else 0
    if pre_apply_total != table_plan.live_count:
        raise StalePlanError(
            f"{table}: plan recorded live_count={table_plan.live_count} but a fresh "
            f"SELECT count(*) reports {pre_apply_total} — rows changed since the dry "
            "run; refusing to apply. Re-run --dry-run and review the new plan."
        )

    if not source_exists:
        # private_relations can legitimately not exist yet (see table_exists's
        # docstring). live_count == 0 was just confirmed above, so there is no
        # data to snapshot, re-home, or archive — nothing to do.
        return ApplyTableResult(
            pre_apply_total=0, re_homed_count=0, archived_count=0, collisions=0, remaining_after=0
        )

    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE TABLE {} AS SELECT * FROM {} WHERE " + _MEMORIES_PREDICATE_SQL).format(
                archive_table_ident, sql.Identifier(table)
            ),
            _predicate_params(),
        )
    with conn.cursor() as cur:
        # Row count of the snapshot alone, before any collision-loser row (which
        # can be a non-S3 row, e.g. a pre-existing target-identity row) is added
        # to the same archive table below — see the docstring's "archived_count"
        # note. By construction (same transaction, no intervening write) this
        # always equals pre_apply_total; the comparison after the loop is a
        # belt-and-suspenders sanity check, not an expected failure mode.
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(archive_table_ident))
        snapshot_count = _scalar_count(cur)

    re_homed_count = 0
    collisions = 0
    for group in table_plan.groups:
        if group.action != "re_home" or group.target_project is None or group.target_agent is None:
            continue
        for row_id in group.row_ids:
            won = _apply_one_rehome(
                conn,
                table=table,
                archive_table_ident=archive_table_ident,
                old_project=group.old_project,
                old_agent=group.old_agent,
                target_project=group.target_project,
                target_agent=group.target_agent,
                row_id=row_id,
            )
            if won == "collision":
                collisions += 1
                re_homed_count += 1
            elif won == "source":
                re_homed_count += 1
            elif won == "existing":
                collisions += 1

    _bulk_delete_by_identity(conn, table=table, groups=table_plan.groups)

    remaining_after = _count_by_identity(conn, table=table, groups=table_plan.groups)

    if remaining_after != 0:
        raise ApplyIntegrityError(
            f"{table}: {remaining_after} row(s) with an original S3 identity are still "
            "live after apply — expected 0. Rolling back."
        )
    if snapshot_count != pre_apply_total:
        raise ApplyIntegrityError(
            f"{table}: archive snapshot holds {snapshot_count} rows but the pre-apply total "
            f"was {pre_apply_total} — live + archive != pre-apply total. Rolling back."
        )

    return ApplyTableResult(
        pre_apply_total=pre_apply_total,
        re_homed_count=re_homed_count,
        archived_count=snapshot_count,
        collisions=collisions,
        remaining_after=remaining_after,
    )


def _apply_one_rehome(
    conn: psycopg.Connection,
    *,
    table: str,
    archive_table_ident: Identifier,
    old_project: str,
    old_agent: str,
    target_project: str,
    target_agent: str,
    row_id: list[str],
) -> str:
    """Move one row to its target identity. Returns "source", "existing", or "collision".

    "collision" is returned (in addition to "source"/"existing" internally
    being folded into the caller's bookkeeping) only via the two-return-value
    contract documented on the caller — see :func:`_apply_table`.
    """

    existing_ts = _fetch_row_timestamp(
        conn, table=table, project_id=target_project, agent_id=target_agent, row_id=row_id
    )
    source_ts = _fetch_row_timestamp(
        conn, table=table, project_id=old_project, agent_id=old_agent, row_id=row_id
    )
    if source_ts is None:
        # Already moved or archived by an earlier collision in this same apply — nothing to do.
        return "existing"

    if existing_ts is None:
        _move_row(
            conn,
            table=table,
            old_project=old_project,
            old_agent=old_agent,
            target_project=target_project,
            target_agent=target_agent,
            row_id=row_id,
        )
        return "source"

    if source_ts >= existing_ts:
        # Source wins: archive the existing target row, delete it, then move source in.
        _archive_and_delete_one(
            conn,
            table=table,
            archive_table_ident=archive_table_ident,
            project_id=target_project,
            agent_id=target_agent,
            row_id=row_id,
        )
        _move_row(
            conn,
            table=table,
            old_project=old_project,
            old_agent=old_agent,
            target_project=target_project,
            target_agent=target_agent,
            row_id=row_id,
        )
        return "collision"

    # Existing wins: leave the source row in place — it is already captured in the
    # initial archive-table snapshot and will be removed by the bulk delete below.
    return "existing"


def _move_row(
    conn: psycopg.Connection,
    *,
    table: str,
    old_project: str,
    old_agent: str,
    target_project: str,
    target_agent: str,
    row_id: list[str],
) -> None:
    tag = f"migrated_from:{old_project}/{old_agent}"
    if table == "private_memories":
        (key,) = row_id
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE private_memories "
                "SET project_id = %s, agent_id = %s, tags = tags || %s::jsonb "
                "WHERE project_id = %s AND agent_id = %s AND key = %s",
                (target_project, target_agent, json.dumps([tag]), old_project, old_agent, key),
            )
    else:
        subject, predicate, object_entity = row_id
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE private_relations "
                "SET project_id = %s, agent_id = %s "
                "WHERE project_id = %s AND agent_id = %s AND subject = %s "
                "AND predicate = %s AND object_entity = %s",
                (
                    target_project,
                    target_agent,
                    old_project,
                    old_agent,
                    subject,
                    predicate,
                    object_entity,
                ),
            )


def _archive_and_delete_one(
    conn: psycopg.Connection,
    *,
    table: str,
    archive_table_ident: Identifier,
    project_id: str,
    agent_id: str,
    row_id: list[str],
) -> None:
    from psycopg import sql

    if table == "private_memories":
        (key,) = row_id
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "INSERT INTO {} SELECT * FROM private_memories "
                    "WHERE project_id = %s AND agent_id = %s AND key = %s"
                ).format(archive_table_ident),
                (project_id, agent_id, key),
            )
            cur.execute(
                "DELETE FROM private_memories WHERE project_id = %s AND agent_id = %s AND key = %s",
                (project_id, agent_id, key),
            )
    else:
        subject, predicate, object_entity = row_id
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "INSERT INTO {} SELECT * FROM private_relations "
                    "WHERE project_id = %s AND agent_id = %s AND subject = %s "
                    "AND predicate = %s AND object_entity = %s"
                ).format(archive_table_ident),
                (project_id, agent_id, subject, predicate, object_entity),
            )
            cur.execute(
                "DELETE FROM private_relations WHERE project_id = %s AND agent_id = %s "
                "AND subject = %s AND predicate = %s AND object_entity = %s",
                (project_id, agent_id, subject, predicate, object_entity),
            )


def _bulk_delete_by_identity(
    conn: psycopg.Connection, *, table: str, groups: list[PlanGroup]
) -> None:
    """Delete every group's rows, addressed by their *original* (old) identity.

    A no-op for rows already moved by :func:`_move_row` (their old identity is
    gone) or already archived+deleted by a collision (ditto) — see the
    identity-vs-predicate note on :func:`apply_plan`.
    """
    if table == "private_memories":
        for group in groups:
            for row_id in group.row_ids:
                (key,) = row_id
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM private_memories "
                        "WHERE project_id = %s AND agent_id = %s AND key = %s",
                        (group.old_project, group.old_agent, key),
                    )
    else:
        for group in groups:
            for row_id in group.row_ids:
                subject, predicate, object_entity = row_id
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM private_relations WHERE project_id = %s AND agent_id = %s "
                        "AND subject = %s AND predicate = %s AND object_entity = %s",
                        (group.old_project, group.old_agent, subject, predicate, object_entity),
                    )


def _count_by_identity(conn: psycopg.Connection, *, table: str, groups: list[PlanGroup]) -> int:
    total = 0
    if table == "private_memories":
        for group in groups:
            for row_id in group.row_ids:
                (key,) = row_id
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM private_memories "
                        "WHERE project_id = %s AND agent_id = %s AND key = %s",
                        (group.old_project, group.old_agent, key),
                    )
                    total += _scalar_count(cur)
    else:
        for group in groups:
            for row_id in group.row_ids:
                subject, predicate, object_entity = row_id
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM private_relations "
                        "WHERE project_id = %s AND agent_id = %s "
                        "AND subject = %s AND predicate = %s AND object_entity = %s",
                        (group.old_project, group.old_agent, subject, predicate, object_entity),
                    )
                    total += _scalar_count(cur)
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tapps_brain.maintenance.tenancy_migrate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dsn", required=True, help="PostgreSQL DSN (postgres:// or postgresql://)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="enumerate, classify, and write a plan"
    )
    mode.add_argument("--apply", action="store_true", help="apply a previously written plan")
    parser.add_argument(
        "--plan-out",
        type=Path,
        default=None,
        help="--dry-run: write plan JSON here (default: stdout)",
    )
    parser.add_argument("--plan", type=Path, default=None, help="--apply: read plan JSON from here")
    parser.add_argument(
        "--archive-table", default=None, help="--apply: base name for the archive tables"
    )
    parser.add_argument(
        "--agent-project-map",
        type=Path,
        default=None,
        help=(
            "--dry-run: JSON file {source_agent: project_id} for R2 "
            "(from AgentForge's projects/agents tables)"
        ),
    )
    parser.add_argument(
        "--ingest-window-start",
        default=None,
        help="--dry-run: R3 window start (created_at, ISO-8601)",
    )
    parser.add_argument(
        "--ingest-window-end", default=None, help="--dry-run: R3 window end (created_at, ISO-8601)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.apply and (args.plan is None or args.archive_table is None):
        parser.error("--apply requires --plan and --archive-table")
    if (args.ingest_window_start is None) != (args.ingest_window_end is None):
        parser.error("--ingest-window-start and --ingest-window-end must be given together")

    import psycopg

    if args.dry_run:
        agent_project_map: dict[str, str] | None = None
        if args.agent_project_map is not None:
            agent_project_map = json.loads(args.agent_project_map.read_text())
        ingest_window: tuple[str, str] | None = None
        if args.ingest_window_start is not None:
            ingest_window = (args.ingest_window_start, args.ingest_window_end)

        try:
            with psycopg.connect(args.dsn) as conn:
                plan = build_plan(
                    conn, agent_project_map=agent_project_map, ingest_window=ingest_window
                )
        except EmptyEnumerationError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        text = plan.model_dump_json(indent=2)
        if args.plan_out is not None:
            args.plan_out.write_text(text + "\n")
        else:
            print(text)
        return 0

    plan = MigrationPlan.model_validate_json(args.plan.read_text())
    with psycopg.connect(args.dsn) as conn:
        result = apply_plan(conn, plan, archive_table=args.archive_table)
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
