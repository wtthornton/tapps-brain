"""CLI tool for tapps-brain memory management and operations.

Provides commands for inspecting, searching, importing/exporting,
federating, and maintaining memory stores from the command line.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Annotated, Any, cast

import structlog

try:
    import typer
except ImportError:
    _msg = (
        "The 'typer' package is required for the tapps-brain CLI.\n"
        "Install it with: uv sync --extra cli  (or --extra all)\n"
    )
    raise SystemExit(_msg) from None

from tapps_brain import __version__
from tapps_brain.agent_scope import normalize_agent_scope

# Route structlog output to stderr so it doesn't pollute CLI stdout/JSON
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PREVIEW_LEN = 80  # Max chars shown for a memory value preview in table output

# Diagnostics CLI letter-grade cutoffs (composite dimension score 0-1).
_DIAG_GRADE_A_MIN = 0.85
_DIAG_GRADE_B_MIN = 0.70
_DIAG_GRADE_C_MIN = 0.55
_DIAG_GRADE_D_MIN = 0.40

# Top-level ``stats`` command: effective confidence below this counts as near-expiry.
_STATS_NEAR_EXPIRY_THRESHOLD = 0.35

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="tapps-brain",
    help=(
        "Persistent cross-session memory for AI assistants — BM25, decay, "
        "Hive. Sub-apps: store, memory, feedback, diagnostics, flywheel, "
        "hive, openclaw, …"
    ),
    no_args_is_help=True,
)
store_app = typer.Typer(help="Inspect store contents and statistics.", no_args_is_help=True)
memory_app = typer.Typer(
    help="Query, inspect, and save individual memories (MCP parity for save).",
    no_args_is_help=True,
)
maintenance_app = typer.Typer(help="Run store maintenance operations.", no_args_is_help=True)

profile_app = typer.Typer(help="Manage memory profiles.", no_args_is_help=True)
hive_app = typer.Typer(help="Manage the Hive shared brain.", no_args_is_help=True)
agent_app = typer.Typer(help="Manage Hive agent registrations.", no_args_is_help=True)

openclaw_app = typer.Typer(help="OpenClaw integration tools.", no_args_is_help=True)
feedback_app = typer.Typer(help="Record and query feedback events.", no_args_is_help=True)
diagnostics_app = typer.Typer(
    help="Quality diagnostics scorecard (EPIC-030).",
    no_args_is_help=True,
)
flywheel_app = typer.Typer(
    help="Continuous improvement flywheel (EPIC-031).",
    no_args_is_help=True,
)
visual_app = typer.Typer(
    help=(
        "Export JSON snapshots for brain visual surfaces "
        "(see docs/planning/brain-visual-implementation-plan.md)."
    ),
    no_args_is_help=True,
)

app.add_typer(store_app, name="store")
app.add_typer(memory_app, name="memory")
app.add_typer(maintenance_app, name="maintenance")
app.add_typer(profile_app, name="profile")
app.add_typer(hive_app, name="hive")
app.add_typer(agent_app, name="agent")
app.add_typer(openclaw_app, name="openclaw")
app.add_typer(feedback_app, name="feedback")
app.add_typer(diagnostics_app, name="diagnostics")
app.add_typer(flywheel_app, name="flywheel")
app.add_typer(visual_app, name="visual")

session_app = typer.Typer(help="Session lifecycle commands.", no_args_is_help=True)
app.add_typer(session_app, name="session")

relay_app = typer.Typer(
    help="Cross-node memory relay — import payloads from sub-agents (GitHub #19).",
    no_args_is_help=True,
)
app.add_typer(relay_app, name="relay")

# EPIC-069: per-project profile registry for multi-tenant deployments.
project_app = typer.Typer(
    help="Register and manage per-project profiles (EPIC-069, ADR-010).",
    no_args_is_help=True,
)
app.add_typer(project_app, name="project")

# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------

ProjectDir = Annotated[
    Path | None,
    typer.Option(
        "--project-dir",
        "-d",
        help="Project root directory (defaults to cwd).",
        envvar="TAPPS_BRAIN_PROJECT_DIR",
    ),
]
JsonFlag = Annotated[
    bool,
    typer.Option("--json", "-j", help="Output as JSON."),
]


# ---------------------------------------------------------------------------
# Global state for agent-id (STORY-053.4)
# ---------------------------------------------------------------------------

_cli_agent_id: str | None = None


@app.callback()
def _main_callback(
    agent_id: Annotated[
        str | None,
        typer.Option(
            "--agent-id",
            envvar="TAPPS_BRAIN_AGENT_ID",
            help="Agent identifier for per-agent storage isolation.",
        ),
    ] = None,
) -> None:
    """Global options applied before any sub-command."""
    global _cli_agent_id
    _cli_agent_id = agent_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_project_dir(project_dir: Path | None) -> Path:
    """Resolve project directory, defaulting to cwd."""
    return (project_dir or Path.cwd()).resolve()


def _get_store(project_dir: Path | None) -> Any:  # noqa: ANN401
    """Open a MemoryStore from the resolved project dir."""
    from tapps_brain.backends import resolve_hive_backend_from_env
    from tapps_brain.store import MemoryStore

    root = _resolve_project_dir(project_dir)
    return MemoryStore(
        root,
        agent_id=_cli_agent_id,
        hive_store=resolve_hive_backend_from_env(),
        hive_agent_id=_cli_agent_id or "cli",
    )


def _open_hive_backend_for_cli() -> Any:  # noqa: ANN401
    """Return Postgres Hive backend or exit with an error (ADR-007)."""
    from tapps_brain.backends import resolve_hive_backend_from_env

    hb = resolve_hive_backend_from_env()
    if hb is None:
        typer.echo(
            "Error: Hive requires TAPPS_BRAIN_HIVE_DSN=postgresql://... "
            "(SQLite Hive removed — ADR-007).",
            err=True,
        )
        raise typer.Exit(code=1)
    return hb


def _output(data: Any, as_json: bool) -> None:  # noqa: ANN401
    """Print data as JSON or formatted text."""
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
    elif isinstance(data, dict):
        for k, v in data.items():
            typer.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            typer.echo(item)
    else:
        typer.echo(str(data))


def _entry_to_row(entry: Any) -> dict[str, Any]:  # noqa: ANN401
    """Convert a MemoryEntry to a display-friendly dict."""
    from tapps_brain.decay import DecayConfig, get_effective_confidence

    eff_conf, _ = get_effective_confidence(entry, DecayConfig())
    row: dict[str, Any] = {
        "key": entry.key,
        "tier": str(entry.tier),
        "confidence": f"{entry.confidence:.2f}",
        "effective": f"{eff_conf:.2f}",
        "scope": entry.scope.value,
        "group": entry.memory_group or "",
        "tags": ", ".join(entry.tags) if entry.tags else "",
        "created": entry.created_at[:10],
    }
    if entry.valid_at:
        row["valid_at"] = entry.valid_at[:10]
    if entry.invalid_at:
        row["invalid_at"] = entry.invalid_at[:10]
    if entry.superseded_by:
        row["superseded_by"] = entry.superseded_by
    return row


def _print_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    """Print a list of dicts as a simple aligned table."""
    if not rows:
        typer.echo("  (no results)")
        return

    if columns is None:
        columns = list(rows[0].keys())

    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            val = str(row.get(col, ""))
            widths[col] = max(widths[col], len(val))

    header = "  ".join(col.upper().ljust(widths[col]) for col in columns)
    typer.echo(header)
    typer.echo("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        line = "  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        typer.echo(line)


# ---------------------------------------------------------------------------
# Version callback
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"tapps-brain {__version__}")
        raise typer.Exit


@app.callback()
def main(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            "-v",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """tapps-brain: Persistent cross-session memory system for AI coding assistants."""


# ===================================================================
# STORE COMMANDS
# ===================================================================


@store_app.command("stats")
def store_stats(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show store statistics: entry count, tier distribution, schema version."""
    store = _get_store(project_dir)
    try:
        snap = store.snapshot()
        schema_ver = store._persistence.get_schema_version()
        data = {
            "project_root": str(snap.project_root),
            "total_entries": snap.total_count,
            "max_entries": store._max_entries,
            "max_entries_per_group": store._max_entries_per_group,
            "schema_version": schema_ver,
            "tier_distribution": snap.tier_counts,
            "exported_at": snap.exported_at,
        }
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(f"Store: {snap.project_root}")
            typer.echo(f"Entries: {snap.total_count} / {store._max_entries}")
            cap_g = store._max_entries_per_group
            if cap_g is not None:
                typer.echo(f"Per memory_group cap: {cap_g}")
            typer.echo(f"Schema: v{schema_ver}")
            typer.echo("Tiers:")
            for tier, count in snap.tier_counts.items():
                typer.echo(f"  {tier}: {count}")
    finally:
        store.close()


@store_app.command("list")
def store_list(
    project_dir: ProjectDir = None,
    tier: Annotated[str | None, typer.Option(help="Filter by tier.")] = None,
    scope: Annotated[str | None, typer.Option(help="Filter by scope.")] = None,
    group: Annotated[
        str | None,
        typer.Option(help="Filter by project-local memory group (GitHub #49)."),
    ] = None,
    include_superseded: Annotated[
        bool, typer.Option("--include-superseded", help="Include superseded entries.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """List all memory entries in the store."""
    store = _get_store(project_dir)
    try:
        entries = store.list_all(
            tier=tier,
            scope=scope,
            include_superseded=include_superseded,
            memory_group=group,
        )
        if as_json:
            _output([_entry_to_row(e) for e in entries], as_json=True)
        else:
            rows = [_entry_to_row(e) for e in entries]
            base_cols = ["key", "tier", "confidence", "effective", "scope", "group", "created"]
            _print_table(rows, columns=base_cols)
            typer.echo(f"\n{len(entries)} entries")
    finally:
        store.close()


@store_app.command("groups")
def store_groups(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """List distinct project-local memory group names (GitHub #49)."""
    store = _get_store(project_dir)
    try:
        names = store.list_memory_groups()
        if as_json:
            _output(names, as_json=True)
        else:
            if not names:
                typer.echo("(no groups — all entries are ungrouped)")
            else:
                for n in names:
                    typer.echo(n)
            typer.echo(f"\n{len(names)} group(s)")
    finally:
        store.close()


@store_app.command("search")
def store_search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    project_dir: ProjectDir = None,
    tier: Annotated[str | None, typer.Option(help="Filter by tier.")] = None,
    scope: Annotated[str | None, typer.Option(help="Filter by scope.")] = None,
    group: Annotated[
        str | None,
        typer.Option(help="Filter by project-local memory group (GitHub #49)."),
    ] = None,
    as_of: Annotated[str | None, typer.Option(help="Point-in-time query (ISO-8601).")] = None,
    as_json: JsonFlag = False,
) -> None:
    """Search memory entries by query text."""
    store = _get_store(project_dir)
    try:
        results = store.search(query, tier=tier, scope=scope, as_of=as_of, memory_group=group)
        if as_json:
            _output([_entry_to_row(e) for e in results], as_json=True)
        else:
            rows = [_entry_to_row(e) for e in results]
            _print_table(rows, columns=["key", "tier", "confidence", "effective", "scope", "group"])
            typer.echo(f"\n{len(results)} results")
    finally:
        store.close()


# ===================================================================
# MEMORY COMMANDS
# ===================================================================


@memory_app.command("save")
def memory_save_cmd(
    key: Annotated[str, typer.Argument(help="Memory key (slug; same rules as MCP memory_save).")],
    value: Annotated[str, typer.Argument(help="Memory body text.")],
    tier: Annotated[str, typer.Option(help="Tier or active profile layer name.")] = "pattern",
    source: Annotated[str, typer.Option(help="human | agent | inferred | system")] = "agent",
    scope: Annotated[str, typer.Option(help="project | branch | session")] = "project",
    branch: Annotated[str | None, typer.Option(help="Required when scope is branch.")] = None,
    agent_scope: Annotated[
        str,
        typer.Option(help="Hive propagation: private | domain | hive | group:<name>."),
    ] = "private",
    confidence: Annotated[float, typer.Option(help="-1 uses source default confidence.")] = -1.0,
    source_agent: Annotated[str, typer.Option(help="Attributing agent id (optional).")] = "",
    tag: Annotated[
        list[str] | None,
        typer.Option("--tag", help="Tag (repeatable).", show_default=False),
    ] = None,
    group: Annotated[
        str | None, typer.Option("--group", help="Project-local memory group (GitHub #49).")
    ] = None,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Save or update one memory (same core semantics as MCP ``memory_save``).

    Example:

        tapps-brain memory save auth.plan "Use device code flow" --tier pattern --tag security
    """
    from tapps_brain.memory_group import MEMORY_GROUP_UNSET
    from tapps_brain.models import MemoryTier
    from tapps_brain.tier_normalize import normalize_save_tier

    store = _get_store(project_dir)
    try:
        try:
            agent_scope = normalize_agent_scope(agent_scope)
        except ValueError as exc:
            err = {"error": "invalid_agent_scope", "message": str(exc)}
            if as_json:
                _output(err, as_json=True)
            else:
                typer.echo(err["message"], err=True)
            raise typer.Exit(code=1) from None

        norm_tier = normalize_save_tier(tier, store.profile)
        valid_tiers = (
            frozenset(store.profile.layer_names)
            if store.profile is not None
            else frozenset(m.value for m in MemoryTier)
        )
        if norm_tier not in valid_tiers:
            ordered = sorted(valid_tiers)
            tier_err: dict[str, Any] = {
                "error": "invalid_tier",
                "message": f"Invalid tier {norm_tier!r}. Valid: {ordered}",
                "valid_values": ordered,
            }
            if as_json:
                _output(tier_err, as_json=True)
            else:
                typer.echo(tier_err["message"], err=True)
            raise typer.Exit(code=1)

        src_ok = ("human", "agent", "inferred", "system")
        if source not in src_ok:
            err = {
                "error": "invalid_source",
                "message": f"Invalid source {source!r}. Valid: {list(src_ok)}",
            }
            if as_json:
                _output(err, as_json=True)
            else:
                typer.echo(err["message"], err=True)
            raise typer.Exit(code=1)

        mg: str | None | object = MEMORY_GROUP_UNSET if group is None else group
        resolved_agent = source_agent.strip() or "cli"
        out = store.save(
            key=key.strip(),
            value=value,
            tier=norm_tier,
            source=source,
            tags=tag or [],
            scope=scope,
            branch=branch,
            confidence=confidence,
            agent_scope=agent_scope,
            source_agent=resolved_agent,
            memory_group=mg,
        )
        if isinstance(out, dict):
            if as_json:
                _output(out, as_json=True)
            else:
                typer.echo(
                    f"Save failed ({out.get('error', 'unknown')}): {out.get('message', '')}",
                    err=True,
                )
            raise typer.Exit(code=1)
        if as_json:
            _output(
                {
                    "status": "saved",
                    "key": out.key,
                    "tier": str(out.tier),
                    "confidence": out.confidence,
                    "memory_group": out.memory_group,
                },
                as_json=True,
            )
        else:
            grp = out.memory_group if out.memory_group else "(ungrouped)"
            typer.echo(f"Saved memory '{out.key}' (tier={out.tier!s}, group={grp})")
    finally:
        store.close()


@memory_app.command("show")
def memory_show(
    key: Annotated[str, typer.Argument(help="Memory entry key.")],
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show full details of a single memory entry."""
    store = _get_store(project_dir)
    try:
        entry = store.get(key)
        if entry is None:
            typer.echo(f"Entry not found: {key}", err=True)
            raise typer.Exit(code=1)

        if as_json:
            _output(entry.model_dump(mode="json"), as_json=True)
        else:
            from tapps_brain.decay import DecayConfig, get_effective_confidence

            eff_conf, _ = get_effective_confidence(entry, DecayConfig())
            typer.echo(f"Key:           {entry.key}")
            typer.echo(f"Value:         {entry.value}")
            typer.echo(f"Tier:          {entry.tier!s}")
            typer.echo(f"Confidence:    {entry.confidence:.2f}")
            typer.echo(f"Effective:     {eff_conf:.2f}")
            typer.echo(f"Source:        {entry.source.value}")
            typer.echo(f"Source Agent:  {entry.source_agent}")
            typer.echo(f"Scope:         {entry.scope.value}")
            typer.echo(
                f"Group:         {entry.memory_group if entry.memory_group else '(ungrouped)'}"
            )
            typer.echo(f"Tags:          {', '.join(entry.tags) if entry.tags else '(none)'}")
            typer.echo(f"Created:       {entry.created_at}")
            typer.echo(f"Updated:       {entry.updated_at}")
            typer.echo(f"Accessed:      {entry.last_accessed}")
            typer.echo(f"Access Count:  {entry.access_count}")
            if entry.branch:
                typer.echo(f"Branch:        {entry.branch}")
            if entry.valid_at:
                typer.echo(f"Valid At:      {entry.valid_at}")
            if entry.invalid_at:
                typer.echo(f"Invalid At:    {entry.invalid_at}")
            if entry.superseded_by:
                typer.echo(f"Superseded By: {entry.superseded_by}")
            if entry.contradicted:
                typer.echo(f"Contradicted:  {entry.contradiction_reason}")
            typer.echo(f"Reinforced:    {entry.reinforce_count}x")
    finally:
        store.close()


@memory_app.command("history")
def memory_history(
    key: Annotated[str, typer.Argument(help="Memory entry key.")],
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show the temporal version chain for a key."""
    store = _get_store(project_dir)
    try:
        chain = store.history(key)
        if as_json:
            _output([_entry_to_row(e) for e in chain], as_json=True)
        else:
            for i, entry in enumerate(chain):
                marker = " (current)" if not entry.superseded_by else " (superseded)"
                typer.echo(f"  [{i + 1}] {entry.key}{marker}")
                val_preview = entry.value[:_PREVIEW_LEN] + (
                    "..." if len(entry.value) > _PREVIEW_LEN else ""
                )
                typer.echo(f"      Value: {val_preview}")
                if entry.valid_at:
                    typer.echo(f"      Valid: {entry.valid_at}")
                if entry.invalid_at:
                    typer.echo(f"      Invalid: {entry.invalid_at}")
            typer.echo(f"\n{len(chain)} versions")
    finally:
        store.close()


@memory_app.command("relations")
def memory_relations(
    key: Annotated[str, typer.Argument(help="Memory entry key.")],
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show all knowledge-graph relations for a memory entry."""
    store = _get_store(project_dir)
    try:
        rels = store.get_relations(key)
        if as_json:
            _output(rels, as_json=True)
        else:
            if not rels:
                typer.echo(f"No relations found for key: {key}")
                return
            rows = [
                {
                    "subject": r.get("subject", ""),
                    "predicate": r.get("predicate", ""),
                    "object": r.get("object_entity", ""),
                    "confidence": f"{r.get('confidence', 0.0):.2f}",
                }
                for r in rels
            ]
            _print_table(rows, columns=["subject", "predicate", "object", "confidence"])
            typer.echo(f"\n{len(rels)} relations")
    finally:
        store.close()


@memory_app.command("related")
def memory_related(
    key: Annotated[str, typer.Argument(help="Memory entry key.")],
    hops: Annotated[int, typer.Option("--hops", help="Maximum traversal hops.")] = 2,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Find entries related to a key via knowledge-graph traversal."""
    store = _get_store(project_dir)
    try:
        try:
            results = store.find_related(key, max_hops=hops)
        except KeyError:
            typer.echo(f"Entry not found: {key}", err=True)
            raise typer.Exit(code=1) from None
        if as_json:
            _output([{"key": k, "hops": h} for k, h in results], as_json=True)
        else:
            if not results:
                typer.echo(f"No related entries found for key: {key}")
                return
            rows = [{"key": k, "hops": str(h)} for k, h in results]
            _print_table(rows, columns=["key", "hops"])
            typer.echo(f"\n{len(results)} related entries")
    finally:
        store.close()


@memory_app.command("search")
def memory_search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    project_dir: ProjectDir = None,
    as_of: Annotated[str | None, typer.Option(help="Point-in-time query (ISO-8601).")] = None,
    group: Annotated[
        str | None,
        typer.Option(help="Restrict to project-local memory group (GitHub #49)."),
    ] = None,
    limit: Annotated[int, typer.Option(help="Maximum results.")] = 20,
    as_json: JsonFlag = False,
) -> None:
    """Search memories by query text with ranked results."""
    store = _get_store(project_dir)
    try:
        from tapps_brain.retrieval import MemoryRetriever

        _prof = getattr(store, "profile", None)
        _lex = getattr(_prof, "lexical", None) if _prof is not None else None
        retriever = MemoryRetriever(lexical_config=_lex)
        scored = retriever.search(query, store, limit=limit, as_of=as_of, memory_group=group)
        if as_json:
            _output(
                [
                    {"key": s.entry.key, "score": round(s.score, 4), **_entry_to_row(s.entry)}
                    for s in scored
                ],
                as_json=True,
            )
        else:
            rows = [{"score": f"{s.score:.3f}", **_entry_to_row(s.entry)} for s in scored]
            _print_table(rows, columns=["score", "key", "tier", "confidence", "effective"])
            typer.echo(f"\n{len(scored)} results")
    finally:
        store.close()


@memory_app.command("audit")
def memory_audit(
    key: Annotated[str | None, typer.Argument(help="Filter by memory entry key.")] = None,
    project_dir: ProjectDir = None,
    event_type: Annotated[
        str | None,
        typer.Option(
            "--type",
            help=(
                "Filter by action (save, delete, consolidation_merge, "
                "consolidation_source, consolidation_merge_undo, …)."
            ),
        ),
    ] = None,
    since: Annotated[
        str | None, typer.Option("--since", help="Lower bound timestamp (ISO-8601, inclusive).")
    ] = None,
    until: Annotated[
        str | None, typer.Option("--until", help="Upper bound timestamp (ISO-8601, inclusive).")
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of events to return.")] = 50,
    as_json: JsonFlag = False,
) -> None:
    """Query the audit trail for memory events.

    Filter by key, event type, or time range. Example:

        memory audit my-key --type save --since 2026-01-01 --limit 20
    """
    store = _get_store(project_dir)
    try:
        entries = store.audit(
            key=key,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
        )
        if as_json:
            _output(
                [
                    {
                        "timestamp": e.timestamp,
                        "event_type": e.event_type,
                        "key": e.key,
                        **e.details,
                    }
                    for e in entries
                ],
                as_json=True,
            )
        else:
            if not entries:
                typer.echo("  (no audit events found)")
                return
            rows = [
                {
                    "timestamp": e.timestamp[:19].replace("T", " "),
                    "event_type": e.event_type,
                    "key": e.key,
                }
                for e in entries
            ]
            _print_table(rows, columns=["timestamp", "event_type", "key"])
            typer.echo(f"\n{len(entries)} events")
    finally:
        store.close()


@memory_app.command("tags")
def memory_tags(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """List all tags with their usage counts across the store."""
    store = _get_store(project_dir)
    try:
        counts = store.list_tags()
        if as_json:
            _output(
                [{"tag": tag, "count": cnt} for tag, cnt in sorted(counts.items())],
                as_json=True,
            )
        else:
            if not counts:
                typer.echo("  (no tags found)")
                return
            rows = [{"tag": tag, "count": str(cnt)} for tag, cnt in sorted(counts.items())]
            _print_table(rows, columns=["tag", "count"])
            typer.echo(f"\n{len(counts)} tags")
    finally:
        store.close()


@memory_app.command("tag")
def memory_tag(
    key: Annotated[str, typer.Argument(help="Memory entry key.")],
    project_dir: ProjectDir = None,
    add: Annotated[
        list[str] | None,
        typer.Option("--add", help="Tags to add (repeatable).", show_default=False),
    ] = None,
    remove: Annotated[
        list[str] | None,
        typer.Option("--remove", help="Tags to remove (repeatable).", show_default=False),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Add or remove tags on a memory entry.

    Example:

        memory tag my-key --add important --add python --remove old-tag
    """
    store = _get_store(project_dir)
    try:
        result = store.update_tags(key, add=add or [], remove=remove or [])
        if isinstance(result, dict):
            # Error dict returned by store
            if as_json:
                _output(result, as_json=True)
            else:
                typer.echo(f"Error ({result.get('error')}): {result.get('message')}", err=True)
            raise typer.Exit(code=1)
        if as_json:
            _output({"key": result.key, "tags": list(result.tags)}, as_json=True)
        else:
            tag_str = ", ".join(result.tags) if result.tags else "(none)"
            typer.echo(f"Updated tags for '{key}': {tag_str}")
    finally:
        store.close()


# ===================================================================
# INIT COMMAND
# ===================================================================


@app.command("init")
def init_cmd(
    target_dir: Annotated[
        Path | None,
        typer.Argument(help="Directory to scaffold into (defaults to cwd)."),
    ] = None,
    project_id: Annotated[
        str | None,
        typer.Option(
            "--project-id",
            help="Stable project slug used in .mcp.json and profile.yaml. "
            "Defaults to the target directory name.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option("--force/--no-force", help="Overwrite existing scaffold files if present."),
    ] = False,
) -> None:
    """Scaffold a new coding project to connect to a deployed tapps-brain hub.

    Writes the following into ``target_dir`` (a copy of ``examples/coding-project-init/``
    with ``{{PROJECT_ID}}`` substituted and ``.mcp.json.template`` renamed to ``.mcp.json``):

      - ``.mcp.json``       — MCP server entry for Claude Code / Cursor
      - ``brain_init.py``   — runtime AgentBrain factory
      - ``.env.example``    — environment variable reference
      - ``profile.yaml``    — per-project memory profile
      - ``README.md``       — scaffold-specific quickstart

    Nothing runs, nothing connects — this just drops files into your project.
    Open and read them before committing.
    """
    import importlib.resources

    dest = (target_dir or Path.cwd()).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    pid = project_id or dest.name

    # Locate the packaged scaffold. We prefer the installed package resource so
    # the command works from a wheel; fall back to the repo path for dev.
    scaffold_src: Path | None = None
    try:
        with importlib.resources.as_file(
            importlib.resources.files("tapps_brain") / "_scaffolds" / "coding-project-init"
        ) as p:
            if p.exists():
                scaffold_src = p
    except (ModuleNotFoundError, FileNotFoundError):
        scaffold_src = None
    if scaffold_src is None:
        # Dev fallback: examples/ relative to the source tree.
        repo_example = Path(__file__).resolve().parents[2] / "examples" / "coding-project-init"
        if repo_example.exists():
            scaffold_src = repo_example
    if scaffold_src is None:
        typer.echo(
            "Error: scaffold sources not found in the installed package or repo.",
            err=True,
        )
        raise typer.Exit(code=1)

    rename_map = {".mcp.json.template": ".mcp.json"}
    written: list[Path] = []
    skipped: list[Path] = []
    for src in sorted(scaffold_src.iterdir()):
        if not src.is_file():
            continue
        out_name = rename_map.get(src.name, src.name)
        out_path = dest / out_name
        if out_path.exists() and not force:
            skipped.append(out_path)
            continue
        content = src.read_text(encoding="utf-8")
        content = content.replace("{{PROJECT_ID}}", pid)
        out_path.write_text(content, encoding="utf-8")
        written.append(out_path)
        if out_name == "brain_init.py":
            out_path.chmod(0o644)

    for p in written:
        typer.echo(f"  wrote  {p.relative_to(dest)}")
    for p in skipped:
        typer.echo(f"  skip   {p.relative_to(dest)} (exists; pass --force to overwrite)")

    typer.echo(
        f"\nScaffold installed into {dest} (project_id={pid!r}). "
        "Read README.md next; nothing is wired until you commit the files and open your editor."
    )


# ===================================================================
# STATS COMMAND
# ===================================================================


@app.command("stats")
def stats_cmd(  # noqa: PLR0915
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show memory health stats: tiers, recent entries, expiry risk, top accessed, db size."""
    from collections import defaultdict
    from datetime import UTC, datetime, timedelta

    store = _get_store(project_dir)
    try:
        from tapps_brain.decay import get_effective_confidence

        decay_cfg = store._get_decay_config()
        entries = store.list_all()

        now = datetime.now(UTC)
        week_ago = now - timedelta(days=7)

        # Stats by tier
        tier_counts: dict[str, int] = defaultdict(int)
        tier_conf_sum: dict[str, float] = defaultdict(float)
        recent_count = 0
        near_expiry: list[tuple[str, str, float]] = []
        top_accessed: list[tuple[str, str, int, str]] = []

        for e in entries:
            tier_key = e.tier.value if hasattr(e.tier, "value") else str(e.tier)
            eff_conf, _ = get_effective_confidence(e, decay_cfg)
            tier_counts[tier_key] += 1
            tier_conf_sum[tier_key] += eff_conf

            # Recent entries (created in last 7 days)
            try:
                created = datetime.fromisoformat(e.created_at.replace("Z", "+00:00"))
                if created >= week_ago:
                    recent_count += 1
            except Exception:
                pass

            # Near expiry
            if eff_conf < _STATS_NEAR_EXPIRY_THRESHOLD:
                near_expiry.append((e.key, tier_key, round(eff_conf, 3)))

            preview = e.value[:60] if e.value else ""
            top_accessed.append((e.key, tier_key, e.access_count, preview))

        # Top 5 by access_count
        top_accessed.sort(key=lambda x: x[2], reverse=True)
        top5 = top_accessed[:5]

        # DB size is no longer reported under the Postgres backend (ADR-007).
        db_size_kb = 0.0

        tier_avg = {
            t: round(tier_conf_sum[t] / tier_counts[t], 3) if tier_counts[t] else 0.0
            for t in tier_counts
        }

        if as_json:
            tiers_out = {
                t: {"count": tier_counts[t], "avg_confidence": tier_avg[t]} for t in tier_counts
            }
            near_rows = [{"key": k, "tier": t, "confidence": c} for k, t, c in near_expiry[:10]]
            top_rows = [{"key": k, "tier": t, "access_count": a} for k, t, a, _ in top5]
            _output(
                {
                    "total_entries": len(entries),
                    "tiers": tiers_out,
                    "recent_7d": recent_count,
                    "near_expiry_count": len(near_expiry),
                    "near_expiry": near_rows,
                    "top_accessed": top_rows,
                    "db_size_kb": db_size_kb,
                },
                as_json=True,
            )
        else:
            typer.echo("\n📊 Memory Health Stats")
            typer.echo(f"  Total entries: {len(entries)}")
            typer.echo(f"  DB size: {db_size_kb} KB")
            typer.echo("\n🗂  Entries by tier:")
            for tier_name in sorted(tier_counts):
                cnt = tier_counts[tier_name]
                ac = tier_avg[tier_name]
                typer.echo(f"  {tier_name:15s} {cnt:4d} entries  avg_conf={ac:.3f}")
            typer.echo(f"\n🕐 Added in last 7 days: {recent_count}")
            thr = _STATS_NEAR_EXPIRY_THRESHOLD
            typer.echo(f"\n⚠️  Near expiry (conf < {thr}): {len(near_expiry)}")
            for key, tier_name, conf in near_expiry[:10]:
                typer.echo(f"  [{tier_name}] {key[:60]}  conf={conf}")
            typer.echo("\n🔝 Top 5 most accessed:")
            for key, tier_name, acc, preview in top5:
                typer.echo(f"  [{tier_name}] ({acc}x) {key[:50]}  — {preview}")
    finally:
        store.close()


# ===================================================================
# IMPORT / EXPORT COMMANDS
# ===================================================================


@app.command("export")
def export_cmd(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Output file path (default: stdout)."),
    ] = None,
    fmt: Annotated[str, typer.Option("--format", "-f", help="Export format.")] = "json",
    tier: Annotated[str | None, typer.Option(help="Filter by tier.")] = None,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Export memory entries to JSON or Markdown."""
    store = _get_store(project_dir)
    try:
        entries = store.list_all(tier=tier)

        if fmt == "markdown":
            from tapps_brain.io import export_to_markdown

            content = export_to_markdown(entries)
        else:
            data = [e.model_dump(mode="json") for e in entries]
            content = json.dumps(data, indent=2, default=str)

        if output:
            output.write_text(content, encoding="utf-8")
            if as_json:
                _output({"exported": len(entries), "file": str(output), "format": fmt}, True)
            else:
                typer.echo(f"Exported {len(entries)} entries to {output}")
        else:
            typer.echo(content)
    finally:
        store.close()


@app.command("import")
def import_cmd(
    input_file: Annotated[Path, typer.Argument(help="File to import (JSON or Markdown).")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without writing.")] = False,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Import memory entries from a JSON file."""
    if not input_file.exists():
        typer.echo(f"File not found: {input_file}", err=True)
        raise typer.Exit(code=1)

    store = _get_store(project_dir)
    try:
        raw = input_file.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            typer.echo(f"Invalid JSON in {input_file}: {exc}", err=True)
            raise typer.Exit(code=1) from None
        if not isinstance(data, list):
            typer.echo("Expected a JSON array of entries.", err=True)
            raise typer.Exit(code=1)

        if dry_run:
            result = {"would_import": len(data), "file": str(input_file)}
            if as_json:
                _output(result, as_json=True)
            else:
                typer.echo(f"Would import {len(data)} entries from {input_file}")
            return

        imported = 0
        skipped = 0
        for item in data:
            key = item.get("key", "")
            if not key:
                skipped += 1
                continue
            existing = store.get(key)
            if existing is not None:
                skipped += 1
                continue
            store.save(
                key=key,
                value=item.get("value", ""),
                tier=item.get("tier", "pattern"),
                source=item.get("source", "system"),
                tags=item.get("tags"),
                confidence=item.get("confidence", -1.0),
            )
            imported += 1

        result = {"imported": imported, "skipped": skipped, "file": str(input_file)}
        if as_json:
            _output(result, as_json=True)
        else:
            typer.echo(f"Imported {imported} entries, skipped {skipped}")
    finally:
        store.close()


# ===================================================================
# MAINTENANCE COMMANDS
# ===================================================================


@maintenance_app.command("consolidate")
def maintenance_consolidate(
    project_dir: ProjectDir = None,
    threshold: Annotated[float, typer.Option(help="Similarity threshold for merging.")] = 0.7,
    force: Annotated[
        bool, typer.Option("--force", help="Run even if interval hasn't elapsed.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Trigger auto-consolidation scan."""
    from tapps_brain.auto_consolidation import run_periodic_consolidation_scan

    root = _resolve_project_dir(project_dir)
    store = _get_store(project_dir)
    try:
        result = run_periodic_consolidation_scan(
            store,
            root,
            threshold=threshold,
            force=force,
        )
        data = result.to_dict()
        if as_json:
            _output(data, as_json=True)
        elif result.scanned:
            typer.echo(
                f"Scanned: {result.groups_found} groups found, "
                f"{result.entries_consolidated} entries consolidated"
            )
            if result.consolidated_entries:
                for key in result.consolidated_entries:
                    typer.echo(f"  -> {key}")
        else:
            typer.echo(f"Skipped: {result.skipped_reason}")
    finally:
        store.close()


@maintenance_app.command("consolidation-threshold-sweep")
def maintenance_consolidation_threshold_sweep(
    project_dir: ProjectDir = None,
    min_group_size: Annotated[
        int,
        typer.Option(
            "--min-group-size",
            min=2,
            help="Minimum entries per consolidation group (matches periodic scan default).",
        ),
    ] = 3,
    tag_weight: Annotated[
        float | None,
        typer.Option(help="Tag vs text blend; default matches similarity module."),
    ] = None,
    text_weight: Annotated[
        float | None,
        typer.Option(help="Tag vs text blend; default matches similarity module."),
    ] = None,
    include_contradicted: Annotated[
        bool,
        typer.Option(
            "--include-contradicted",
            help="Include contradicted rows and consolidated subclasses (default: active-only).",
        ),
    ] = False,
    thresholds: Annotated[
        str | None,
        typer.Option(
            "--thresholds",
            help="Comma-separated similarity cutoffs (default: 0.40-0.90 step 0.05).",
        ),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Report consolidation group counts across thresholds (read-only; no store mutations)."""
    from tapps_brain.evaluation import run_consolidation_threshold_sweep

    store = _get_store(project_dir)
    try:
        entries = store.list_all()
        thr_list: list[float] | None = None
        if thresholds is not None and thresholds.strip():
            thr_list = []
            for part in thresholds.split(","):
                p = part.strip()
                if p:
                    thr_list.append(float(p))
        report = run_consolidation_threshold_sweep(
            entries,
            thresholds=thr_list,
            min_group_size=min_group_size,
            tag_weight=tag_weight,
            text_weight=text_weight,
            active_only=not include_contradicted,
        )
        data = report.model_dump(mode="json")
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(
                f"Analyzed {report.analyzed_entry_count} entries "
                f"({report.source_entry_count} total in store); "
                f"min_group_size={report.min_group_size} "
                f"active_only={report.active_only} "
                f"tag_weight={report.tag_weight} text_weight={report.text_weight}"
            )
            typer.echo("threshold  groups  entries_in_groups  largest_group")
            for row in report.rows:
                typer.echo(
                    f"{row.threshold:>8.4f}  {row.group_count:>6}  "
                    f"{row.entries_in_groups:>17}  {row.largest_group_size:>14}"
                )
    finally:
        store.close()


@maintenance_app.command("save-conflict-candidates")
def maintenance_save_conflict_candidates(
    project_dir: ProjectDir = None,
    threshold: Annotated[
        float | None,
        typer.Option(
            "--threshold",
            help=(
                "Similarity cutoff for detect_save_conflicts "
                "(default: profile conflict_check or built-in medium tier)."
            ),
        ),
    ] = None,
    include_contradicted: Annotated[
        bool,
        typer.Option(
            "--include-contradicted",
            help="Also treat contradicted / consolidated rows as hypothetical saves.",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Export save-time conflict pairs for offline review (read-only; EPIC-044 STORY-044.3).

    Does not run NLI or any LLM — output is JSON or a short table for external tooling.
    """
    from tapps_brain.evaluation import run_save_conflict_candidate_report
    from tapps_brain.profile import ConflictCheckConfig

    store = _get_store(project_dir)
    try:
        prof = store.profile
        _cc = getattr(prof, "conflict_check", None) if prof is not None else None
        if threshold is not None:
            sim_thr = float(threshold)
        elif _cc is not None:
            sim_thr = _cc.effective_similarity_threshold()
        else:
            sim_thr = ConflictCheckConfig().effective_similarity_threshold()
        entries = store.list_all()
        report = run_save_conflict_candidate_report(
            entries,
            sim_thr,
            active_only=not include_contradicted,
        )
        data = report.model_dump(mode="json")
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(
                f"Save conflict candidates: {len(report.rows)} pair(s); "
                f"threshold={report.similarity_threshold} "
                f"analyzed_incoming={report.analyzed_entry_count} "
                f"source_entries={report.source_entry_count} "
                f"active_only={report.active_only}"
            )
            preview_len = 72
            trim_to = 69
            for row in report.rows:
                inc_v = row.hypothetical_incoming_value.replace("\n", " ")
                con_v = row.conflicting_value.replace("\n", " ")
                if len(inc_v) > preview_len:
                    inc_v = inc_v[:trim_to] + "..."
                if len(con_v) > preview_len:
                    con_v = con_v[:trim_to] + "..."
                typer.echo(
                    f"  {row.hypothetical_incoming_key} -> {row.conflicting_key} "
                    f"tier={row.tier} sim={row.similarity:.4f}"
                )
                typer.echo(f"    incoming: {inc_v}")
                typer.echo(f"    conflict: {con_v}")
    finally:
        store.close()


@maintenance_app.command("consolidation-merge-undo")
def maintenance_consolidation_merge_undo(
    consolidated_key: Annotated[str, typer.Argument(help="Key of the consolidated row to undo.")],
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Revert the last auto-consolidation merge for this key (EPIC-044 STORY-044.4).

    Restores superseded source rows, deletes the consolidated row, and appends
    ``consolidation_merge_undo`` to ``memory_log.jsonl``. Requires matching audit
    and unchanged supersede metadata on sources.
    """
    store = _get_store(project_dir)
    try:
        result = store.undo_consolidation_merge(consolidated_key)
        data = result.to_dict()
        if as_json:
            _output(data, as_json=True)
        elif result.ok:
            typer.echo(
                f"Undo OK: removed {result.consolidated_key}; "
                f"restored {len(result.source_keys)} source(s)."
            )
            for sk in result.source_keys:
                typer.echo(f"  <- {sk}")
        else:
            typer.echo(f"Undo failed: {result.reason}", err=True)
            raise typer.Exit(code=1)
    finally:
        store.close()


@maintenance_app.command("consolidation-diff")
def maintenance_consolidation_diff(
    merge_id: Annotated[str, typer.Argument(help="Consolidated key (merge ID) to inspect.")],
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Inspect details of a past auto-consolidation merge (STORY-SC03 / TAP-559).

    Reads the audit log for the given consolidated key and displays source keys,
    trigger, threshold, similarity score, merge rule (embedding_cosine vs
    text_similarity), and consolidation reason.  Read-only — no store mutations.
    """
    from tapps_brain.auto_consolidation import find_last_consolidation_merge_audit

    store = _get_store(project_dir)
    try:
        audit_path = store._persistence.audit_path
        record = find_last_consolidation_merge_audit(
            audit_path, merge_id, persistence=store._persistence
        )
        if record is None:
            typer.echo(f"No consolidation merge audit found for key: {merge_id}", err=True)
            raise typer.Exit(code=1)
        data = dict(record)
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(f"Merge ID:        {merge_id}")
            typer.echo(f"  Source keys:   {record.get('source_keys', [])}")
            typer.echo(f"  Trigger:       {record.get('trigger', 'unknown')}")
            typer.echo(f"  Threshold:     {record.get('threshold', 'unknown')}")
            typer.echo(f"  Merge rule:    {record.get('merge_rule', 'text_similarity')}")
            sim = record.get("similarity_score")
            typer.echo(f"  Similarity:    {round(sim, 4) if sim is not None else 'n/a'}")
            typer.echo(f"  Reason:        {record.get('consolidation_reason', 'unknown')}")
    finally:
        store.close()


@maintenance_app.command("stale")
def maintenance_stale(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """List GC stale candidates with reasons (read-only; GitHub #21)."""
    store = _get_store(project_dir)
    try:
        details = store.list_gc_stale_details()
        data = {
            "count": len(details),
            "entries": [d.model_dump(mode="json") for d in details],
        }
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(f"Stale (GC) candidates: {len(details)}")
            for d in details:
                reasons = ",".join(d.reasons)
                typer.echo(
                    f"  - {d.key} [{d.tier}] effective={d.effective_confidence:.3f} "
                    f"reasons={reasons}"
                )
    finally:
        store.close()


@maintenance_app.command("gc")
def maintenance_gc(
    project_dir: ProjectDir = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without archiving.")] = False,
    as_json: JsonFlag = False,
) -> None:
    """Run garbage collection to archive stale entries."""
    store = _get_store(project_dir)
    try:
        result = store.gc(dry_run=dry_run)
        data = result.model_dump(mode="json")
        if as_json:
            _output(data, as_json=True)
        elif dry_run:
            n = len(result.archived_keys)
            typer.echo(
                f"Would archive {n} entries (~{result.estimated_archive_bytes} UTF-8 bytes JSONL):"
            )
            if result.reason_counts:
                typer.echo(f"  reasons: {result.reason_counts}")
            for key in result.archived_keys:
                typer.echo(f"  - {key}")
        else:
            typer.echo(
                f"Archived {result.archived_count} entries "
                f"({result.archive_bytes} bytes to archive), "
                f"{result.remaining_count} remaining"
            )
    finally:
        store.close()


@maintenance_app.command("gc-config")
def maintenance_gc_config(
    project_dir: ProjectDir = None,
    set_floor: Annotated[
        int | None,
        typer.Option("--floor-retention-days", help="Days at floor confidence before archival."),
    ] = None,
    set_session: Annotated[
        int | None,
        typer.Option("--session-expiry-days", help="Days after session end before archival."),
    ] = None,
    set_threshold: Annotated[
        float | None,
        typer.Option(
            "--contradicted-threshold", help="Confidence threshold for contradicted archival."
        ),
    ] = None,
    set_session_index_ttl: Annotated[
        int | None,
        typer.Option(
            "--session-index-ttl-days",
            help="Days before session index (FTS5) rows are pruned during gc().",
        ),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Show or update garbage collection configuration."""
    from tapps_brain.gc import GCConfig

    store = _get_store(project_dir)
    try:
        current: GCConfig = store.get_gc_config()

        _any_set = (
            set_floor is not None
            or set_session is not None
            or set_threshold is not None
            or set_session_index_ttl is not None
        )
        # If any --set flags given, update and save back to store
        if _any_set:
            new_cfg = GCConfig(
                floor_retention_days=(
                    set_floor if set_floor is not None else current.floor_retention_days
                ),
                session_expiry_days=(
                    set_session if set_session is not None else current.session_expiry_days
                ),
                contradicted_threshold=(
                    set_threshold if set_threshold is not None else current.contradicted_threshold
                ),
                session_index_ttl_days=(
                    set_session_index_ttl
                    if set_session_index_ttl is not None
                    else current.session_index_ttl_days
                ),
            )
            store.set_gc_config(new_cfg)
            data: dict[str, object] = {"status": "updated", **new_cfg.to_dict()}
        else:
            data = current.to_dict()

        if as_json:
            _output(data, as_json=True)
        else:
            cfg = store.get_gc_config()
            typer.echo(f"floor_retention_days:    {cfg.floor_retention_days}")
            typer.echo(f"session_expiry_days:     {cfg.session_expiry_days}")
            typer.echo(f"contradicted_threshold:  {cfg.contradicted_threshold}")
            typer.echo(f"session_index_ttl_days:  {cfg.session_index_ttl_days}")
            if _any_set:
                typer.echo("Configuration updated.")
    finally:
        store.close()


@maintenance_app.command("consolidation-config")
def maintenance_consolidation_config(
    project_dir: ProjectDir = None,
    set_enabled: Annotated[
        bool | None,
        typer.Option("--enabled/--disabled", help="Enable or disable auto-consolidation."),
    ] = None,
    set_threshold: Annotated[
        float | None,
        typer.Option("--threshold", help="Similarity threshold for merging entries."),
    ] = None,
    set_min_entries: Annotated[
        int | None,
        typer.Option("--min-entries", help="Minimum entries required before consolidation."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Show or update auto-consolidation configuration."""
    from tapps_brain.store import ConsolidationConfig

    store = _get_store(project_dir)
    try:
        current: ConsolidationConfig = store.get_consolidation_config()

        if set_enabled is not None or set_threshold is not None or set_min_entries is not None:
            new_cfg = ConsolidationConfig(
                enabled=set_enabled if set_enabled is not None else current.enabled,
                threshold=set_threshold if set_threshold is not None else current.threshold,
                min_entries=set_min_entries if set_min_entries is not None else current.min_entries,
            )
            store.set_consolidation_config(new_cfg)
            data: dict[str, object] = {"status": "updated", **new_cfg.to_dict()}
        else:
            data = current.to_dict()

        if as_json:
            _output(data, as_json=True)
        else:
            cfg = store.get_consolidation_config()
            typer.echo(f"enabled:     {cfg.enabled}")
            typer.echo(f"threshold:   {cfg.threshold}")
            typer.echo(f"min_entries: {cfg.min_entries}")
            if set_enabled is not None or set_threshold is not None or set_min_entries is not None:
                typer.echo("Configuration updated.")
    finally:
        store.close()


@maintenance_app.command("migrate")
def maintenance_migrate(
    project_dir: ProjectDir = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show current schema version only.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Run schema migrations."""
    store = _get_store(project_dir)
    try:
        version = store.get_schema_version()
        if dry_run:
            data = {"current_version": version}
            if as_json:
                _output(data, as_json=True)
            else:
                typer.echo(f"Current schema version: v{version}")
        else:
            # Migrations run automatically on store open
            data = {"schema_version": version, "status": "up-to-date"}
            if as_json:
                _output(data, as_json=True)
            else:
                typer.echo(f"Schema version: v{version} (up-to-date)")
    finally:
        store.close()


@maintenance_app.command("health")
def maintenance_health(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show store health: counts, schema, profile, optional seed version, GC/consolidation hints."""
    store = _get_store(project_dir)
    try:
        report = store.health()
        if as_json:
            _output(report.model_dump(mode="json"), as_json=True)
        else:
            typer.echo(f"Store: {report.store_path}")
            typer.echo(f"Entries: {report.entry_count} / {report.max_entries}")
            typer.echo(f"Schema: v{report.schema_version}")
            if report.package_version:
                typer.echo(f"Package: {report.package_version}")
            if report.profile_name:
                typer.echo(f"Profile: {report.profile_name}")
            if report.profile_seed_version:
                typer.echo(f"Profile seed version: {report.profile_seed_version}")
            typer.echo("Tiers:")
            for tier, count in report.tier_distribution.items():
                typer.echo(f"  {tier}: {count}")
            if report.oldest_entry_age_days > 0:
                typer.echo(f"Oldest entry: {report.oldest_entry_age_days:.1f} days")
            typer.echo(f"Consolidation candidates: {report.consolidation_candidates}")
            typer.echo(f"GC candidates: {report.gc_candidates}")
            typer.echo(
                f"Federation: {'enabled' if report.federation_enabled else 'disabled'}"
                f" ({report.federation_project_count} projects)"
            )
    finally:
        store.close()


@maintenance_app.command("verify-integrity")
def maintenance_verify_integrity(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Verify HMAC-SHA256 integrity hashes for all memory entries.

    Scans every entry in the store, recomputes its integrity hash, and
    reports any tampered or missing-hash entries.  Exit code 0 when all
    entries verify, 1 when tampered entries are found.
    """
    store = _get_store(project_dir)
    try:
        result = store.verify_integrity()
        if as_json:
            _output(result, as_json=True)
        else:
            typer.echo(f"Total entries: {result['total']}")
            typer.echo(f"Verified:      {result['verified']}")
            typer.echo(f"Tampered:      {result['tampered']}")
            typer.echo(f"No hash:       {result['no_hash']}")
            if result["tampered_keys"]:
                typer.echo("\nTampered keys:")
                for key in result["tampered_keys"]:
                    typer.echo(f"  - {key}")
            if result["missing_hash_keys"]:
                typer.echo("\nMissing hash keys:")
                for key in result["missing_hash_keys"]:
                    typer.echo(f"  - {key}")
        if result["tampered"] > 0:
            raise typer.Exit(code=1)
    finally:
        store.close()


# NOTE: the legacy `maintenance split-by-agent` command was removed in
# ADR-007 — it operated on per-agent SQLite memory.db files.  Under the
# Postgres-only persistence plane every agent already has an isolated
# (project_id, agent_id) scope inside ``private_memories``; there is
# nothing to split.


# ---------------------------------------------------------------------------
# PostgreSQL Hive migration commands (EPIC-055)
# ---------------------------------------------------------------------------

_PG_DSN_OPT = Annotated[
    str,
    typer.Option(
        "--dsn",
        envvar="TAPPS_BRAIN_HIVE_POSTGRES_DSN",
        help="PostgreSQL connection string (or set TAPPS_BRAIN_HIVE_POSTGRES_DSN).",
    ),
]


@maintenance_app.command("migrate-hive")
def maintenance_migrate_hive(
    dsn: _PG_DSN_OPT = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show pending migrations without applying.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Apply pending PostgreSQL Hive schema migrations."""
    if not dsn:
        typer.echo("Error: --dsn or TAPPS_BRAIN_HIVE_POSTGRES_DSN is required.", err=True)
        raise typer.Exit(code=1)

    from tapps_brain.postgres_migrations import apply_hive_migrations

    applied = apply_hive_migrations(dsn, dry_run=dry_run)
    data = {
        "applied_versions": applied,
        "dry_run": dry_run,
        "status": "dry-run" if dry_run else ("up-to-date" if not applied else "applied"),
    }
    if as_json:
        _output(data, as_json=True)
    elif not applied:
        typer.echo("Hive schema is up-to-date.")
    else:
        action = "Would apply" if dry_run else "Applied"
        typer.echo(f"{action} migrations: {applied}")


@maintenance_app.command("hive-schema-status")
def maintenance_hive_schema_status(
    dsn: _PG_DSN_OPT = "",
    as_json: JsonFlag = False,
) -> None:
    """Show current PostgreSQL Hive schema version and pending migrations."""
    if not dsn:
        typer.echo("Error: --dsn or TAPPS_BRAIN_HIVE_POSTGRES_DSN is required.", err=True)
        raise typer.Exit(code=1)

    from tapps_brain.postgres_migrations import get_hive_schema_status

    status = get_hive_schema_status(dsn)
    data = {
        "current_version": status.current_version,
        "applied_versions": status.applied_versions,
        "pending_migrations": [{"version": v, "filename": f} for v, f in status.pending_migrations],
    }
    if as_json:
        _output(data, as_json=True)
    else:
        typer.echo(f"Current version: {status.current_version}")
        if status.pending_migrations:
            typer.echo("Pending migrations:")
            for v, fname in status.pending_migrations:
                typer.echo(f"  v{v}: {fname}")
        else:
            typer.echo("No pending migrations.")


# ---------------------------------------------------------------------------
# Hive backup / restore commands (EPIC-058 STORY-058.4)
# ---------------------------------------------------------------------------


@maintenance_app.command("backup-hive")
def maintenance_backup_hive(
    output: Annotated[str | None, typer.Option(help="Output file path.")] = None,
    dsn: Annotated[str | None, typer.Option(help="Hive Postgres DSN.")] = None,
    format: Annotated[str, typer.Option(help="Backup format: sql or custom.")] = "sql",  # noqa: A002
) -> None:
    """Backup Hive Postgres data using pg_dump."""
    import os
    import subprocess
    from datetime import UTC, datetime

    effective_dsn = dsn or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
    if not effective_dsn:
        typer.echo("Error: --dsn or TAPPS_BRAIN_HIVE_DSN required.", err=True)
        raise typer.Exit(1)

    if output is None:
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%S")
        output = f"hive-backup-{ts}.sql"

    fmt_flag = "--format=custom" if format == "custom" else "--format=plain"
    cmd = ["pg_dump", effective_dsn, fmt_flag, f"--file={output}"]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        typer.echo(f"Backup written to {output}")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Error: {e.stderr}", err=True)
        raise typer.Exit(1) from e
    except FileNotFoundError as e:
        typer.echo("Error: pg_dump not found. Install PostgreSQL client tools.", err=True)
        raise typer.Exit(1) from e


@maintenance_app.command("restore-hive")
def maintenance_restore_hive(
    input: Annotated[str, typer.Argument(help="Backup file to restore.")],  # noqa: A002
    dsn: Annotated[str | None, typer.Option(help="Hive Postgres DSN.")] = None,
) -> None:
    """Restore Hive Postgres data from a backup."""
    import os
    import subprocess

    effective_dsn = dsn or os.environ.get("TAPPS_BRAIN_HIVE_DSN")
    if not effective_dsn:
        typer.echo("Error: --dsn or TAPPS_BRAIN_HIVE_DSN required.", err=True)
        raise typer.Exit(1)

    # Detect format
    if input.endswith(".sql"):
        cmd = ["psql", effective_dsn, "-f", input]
    else:
        cmd = ["pg_restore", "--dbname", effective_dsn, "--clean", "--if-exists", input]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        typer.echo(f"Restored from {input}")
    except subprocess.CalledProcessError as e:
        typer.echo(f"Error: {e.stderr}", err=True)
        raise typer.Exit(1) from e


@store_app.command("metrics")
def store_metrics(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show store operation metrics (counters and latency histograms)."""
    store = _get_store(project_dir)
    try:
        snapshot = store.get_metrics()
        if as_json:
            _output(snapshot.to_dict(), as_json=True)
        else:
            typer.echo("Counters:")
            if not snapshot.counters:
                typer.echo("  (no counters recorded)")
            for name, value in sorted(snapshot.counters.items()):
                typer.echo(f"  {name}: {value}")
            typer.echo("\nHistograms:")
            if not snapshot.histograms:
                typer.echo("  (no histograms recorded)")
            for name, stats in sorted(snapshot.histograms.items()):
                typer.echo(
                    f"  {name}: count={stats.count} "
                    f"p50={stats.p50:.2f}ms p95={stats.p95:.2f}ms p99={stats.p99:.2f}ms"
                )
    finally:
        store.close()


# ===================================================================
# RECALL COMMAND (top-level)
# ===================================================================


@app.command("recall")
def recall_cmd(
    message: Annotated[str, typer.Argument(help="Message to search memories for.")],
    project_dir: ProjectDir = None,
    max_tokens: Annotated[int, typer.Option(help="Token budget.")] = 2000,
    group: Annotated[
        str | None,
        typer.Option(help="Restrict local retrieval to this memory group (GitHub #49)."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Test auto-recall from the terminal."""
    store = _get_store(project_dir)
    try:
        result = store.recall(message, max_tokens=max_tokens, memory_group=group)
        if as_json:
            _output(
                {
                    "memory_count": result.memory_count,
                    "token_count": result.token_count,
                    "recall_time_ms": round(result.recall_time_ms, 2),
                    "truncated": result.truncated,
                    "memories": result.memories,
                },
                as_json=True,
            )
        else:
            typer.echo(
                f"Recalled {result.memory_count} memories "
                f"({result.token_count} tokens, {result.recall_time_ms:.1f}ms)"
            )
            if result.memory_section:
                typer.echo("")
                typer.echo(result.memory_section)
    finally:
        store.close()


# ===================================================================
# SIMPLIFIED AGENT BRAIN COMMANDS (EPIC-057)
# ===================================================================


@app.command("remember")
def cli_remember(
    fact: Annotated[str, typer.Argument(help="The memory to save.")],
    tier: Annotated[str, typer.Option(help="Memory tier.")] = "procedural",
    share: Annotated[bool, typer.Option("--share", help="Share with all groups.")] = False,
    project_dir: ProjectDir = None,
) -> None:
    """Save a memory to the agent's brain."""
    from tapps_brain.agent_brain import _content_key

    store = _get_store(project_dir)
    try:
        key = _content_key(fact)
        agent_scope = "group" if share else "private"
        store.save(key=key, value=fact, tier=tier, agent_scope=agent_scope)
        typer.echo(f"Remembered: {key}")
    finally:
        store.close()


@app.command("brain-recall")
def cli_brain_recall(
    query: Annotated[str, typer.Argument(help="What to search for.")],
    max_results: Annotated[int, typer.Option(help="Max results.")] = 5,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Recall memories matching a query (simplified)."""
    store = _get_store(project_dir)
    try:
        entries = store.search(query)
        results = []
        for entry in entries[:max_results]:
            results.append(
                {
                    "key": entry.key,
                    "value": entry.value[:120],
                    "tier": str(entry.tier),
                    "confidence": f"{entry.confidence:.2f}",
                }
            )
        if as_json:
            _output(results, as_json=True)
        elif not results:
            typer.echo("  (no results)")
        else:
            _print_table(results)
    finally:
        store.close()


@app.command("forget")
def cli_forget(
    key: Annotated[str, typer.Argument(help="Memory key to archive.")],
    project_dir: ProjectDir = None,
) -> None:
    """Archive a memory."""
    store = _get_store(project_dir)
    try:
        entry = store.get(key)
        if entry is None:
            typer.echo(f"Not found: {key}")
            raise typer.Exit(code=1)
        store.delete(key)
        typer.echo(f"Forgotten: {key}")
    finally:
        store.close()


@app.command("status")
def cli_status(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show agent identity, groups, and store stats."""
    store = _get_store(project_dir)
    try:
        status = {
            "agent_id": getattr(store, "agent_id", None),
            "groups": getattr(store, "groups", []),
            "expert_domains": getattr(store, "expert_domains", []),
            "memory_count": len(store.list_all()),
            "hive_connected": store._hive_store is not None,
        }
        if as_json:
            _output(status, as_json=True)
        else:
            for k, v in status.items():
                typer.echo(f"  {k}: {v}")
    finally:
        store.close()


@app.command("who-am-i")
def cli_who_am_i(
    project_dir: ProjectDir = None,
) -> None:
    """Show current agent identity and configuration."""
    store = _get_store(project_dir)
    try:
        typer.echo(f"  agent_id: {getattr(store, 'agent_id', None)}")
        typer.echo(f"  project_root: {store.project_root}")
        typer.echo(f"  groups: {getattr(store, 'groups', [])}")
        typer.echo(f"  expert_domains: {getattr(store, 'expert_domains', [])}")
        profile = getattr(store, "profile", None)
        typer.echo(f"  profile: {getattr(profile, 'name', 'default')}")
        typer.echo(f"  hive_connected: {store._hive_store is not None}")
    finally:
        store.close()


# ===================================================================
# PROFILE COMMANDS (EPIC-010)
# ===================================================================


@profile_app.command("show")
def profile_show(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show the active memory profile."""
    from tapps_brain.profile import resolve_profile

    root = _resolve_project_dir(project_dir)
    profile = resolve_profile(root)

    data = {
        "name": profile.name,
        "description": profile.description,
        "version": profile.version,
        "layer_count": len(profile.layers),
        "layers": [
            {
                "name": la.name,
                "half_life_days": la.half_life_days,
                "decay_model": la.decay_model,
            }
            for la in profile.layers
        ],
    }
    if as_json:
        _output(data, as_json=True)
    else:
        typer.echo(f"Profile:       {profile.name}")
        typer.echo(f"Description:   {profile.description}")
        typer.echo(f"Version:       {profile.version}")
        typer.echo(f"Layers ({len(profile.layers)}):")
        for la in profile.layers:
            typer.echo(f"  - {la.name}: {la.half_life_days}d ({la.decay_model})")


@profile_app.command("list")
def profile_list(
    as_json: JsonFlag = False,
) -> None:
    """List available built-in profiles."""
    from tapps_brain.profile import get_builtin_profile, list_builtin_profiles

    names = list_builtin_profiles()
    profiles = []
    for name in names:
        p = get_builtin_profile(name)
        profiles.append(
            {
                "name": p.name,
                "description": p.description,
                "layers": len(p.layers),
            }
        )

    if as_json:
        _output(profiles, as_json=True)
    else:
        typer.echo("Available profiles:")
        for item in profiles:
            typer.echo(f"  {item['name']:25s} ({item['layers']} layers) — {item['description']}")


@profile_app.command("set")
def profile_set(
    name: str,
    project_dir: ProjectDir = None,
) -> None:
    """Set the active profile for this project."""
    import yaml

    from tapps_brain.profile import get_builtin_profile

    # Verify profile exists
    profile = get_builtin_profile(name)

    root = _resolve_project_dir(project_dir)
    profile_dir = root / ".tapps-brain"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_path = profile_dir / "profile.yaml"

    # Write a minimal profile YAML that references the built-in
    data = {"profile": {"name": name, "extends": name, "layers": []}}
    profile_path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")

    typer.echo(f"Profile set to '{profile.name}' at {profile_path}")


@profile_app.command("onboard")
def profile_onboard(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Print structured onboarding guidance for the active memory profile (GitHub #45)."""
    from tapps_brain.onboarding import render_agent_onboarding
    from tapps_brain.profile import resolve_profile

    root = _resolve_project_dir(project_dir)
    profile = resolve_profile(root)
    text = render_agent_onboarding(profile)
    if as_json:
        _output({"format": "markdown", "content": text}, as_json=True)
    else:
        typer.echo(text)


@profile_app.command("layers")
def profile_layers(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show detailed layer information for the active profile."""
    from tapps_brain.profile import resolve_profile

    root = _resolve_project_dir(project_dir)
    profile = resolve_profile(root)

    layers_data = []
    for la in profile.layers:
        layer_info: dict[str, Any] = {
            "name": la.name,
            "description": la.description,
            "half_life_days": la.half_life_days,
            "decay_model": la.decay_model,
            "confidence_floor": la.confidence_floor,
        }
        if la.promotion_to:
            layer_info["promotion_to"] = la.promotion_to
        if la.promotion_threshold:
            layer_info["promotion_threshold"] = {
                "min_access_count": la.promotion_threshold.min_access_count,
                "min_age_days": la.promotion_threshold.min_age_days,
                "min_confidence": la.promotion_threshold.min_confidence,
            }
        if la.demotion_to:
            layer_info["demotion_to"] = la.demotion_to
        if la.importance_tags:
            layer_info["importance_tags"] = la.importance_tags
        layers_data.append(layer_info)

    if as_json:
        _output(layers_data, as_json=True)
    else:
        typer.echo(f"Profile: {profile.name}")
        typer.echo(f"Layers ({len(profile.layers)}):")
        for la_info in layers_data:
            typer.echo(f"\n  [{la_info['name']}]")
            typer.echo(f"    Description:      {la_info.get('description', '')}")
            typer.echo(f"    Half-life:        {la_info['half_life_days']} days")
            typer.echo(f"    Decay model:      {la_info['decay_model']}")
            typer.echo(f"    Confidence floor: {la_info['confidence_floor']}")
            if "promotion_to" in la_info:
                pt = la_info.get("promotion_threshold", {})
                typer.echo(
                    f"    Promotion:        → {la_info['promotion_to']} "
                    f"(access≥{pt.get('min_access_count', '?')}, "
                    f"age≥{pt.get('min_age_days', '?')}d, "
                    f"conf≥{pt.get('min_confidence', '?')})"
                )
            if "demotion_to" in la_info:
                typer.echo(f"    Demotion:         → {la_info['demotion_to']}")
            if "importance_tags" in la_info:
                tags_str = ", ".join(f"{k}={v}x" for k, v in la_info["importance_tags"].items())
                typer.echo(f"    Importance tags:  {tags_str}")


# ---------------------------------------------------------------------------
# Hive commands (EPIC-011)
# ---------------------------------------------------------------------------


@hive_app.command("status")
def hive_status(as_json: JsonFlag = False) -> None:
    """Show Hive status: namespaces, entry counts, registered agents."""
    from tapps_brain.backends import AgentRegistry

    hive = _open_hive_backend_for_cli()
    try:
        ns_counts = hive.count_by_namespace()
        agent_counts = hive.count_by_agent()
        total = sum(ns_counts.values())
    finally:
        hive.close()

    registry = AgentRegistry()
    agents = [
        {
            "id": a.id,
            "profile": a.profile,
            "skills": a.skills,
            # Count entries contributed by this agent (across all namespaces).
            # Previously used ns_counts.get(a.profile, 0) which always returned 0
            # because entries go to "universal" or a domain namespace, not a
            # namespace named after the agent ID. Fix for issue #22.
            "entries_contributed": agent_counts.get(a.id, 0),
        }
        for a in registry.list_agents()
    ]

    data: dict[str, Any] = {
        "namespaces": ns_counts,
        "total_entries": total,
        "agents": agents,
    }
    if as_json:
        _output(data, as_json=True)
    else:
        typer.echo(f"Hive: {total} entries across {len(ns_counts)} namespaces")
        for ns, count in ns_counts.items():
            typer.echo(f"  {ns}: {count}")
        if agents:
            typer.echo(f"\nAgents ({len(agents)}):")
            for a in agents:
                typer.echo(
                    f"  {a['id']} (profile={a['profile']}, "
                    f"entries_contributed={a['entries_contributed']})"
                )
        else:
            typer.echo("\nNo registered agents.")


@hive_app.command("search")
def hive_search(
    query: str,
    namespace: Annotated[str | None, typer.Option(help="Namespace filter.")] = None,
    as_json: JsonFlag = False,
) -> None:
    """Search the Hive shared brain."""
    hive = _open_hive_backend_for_cli()
    try:
        ns_list = [namespace] if namespace else None
        results = hive.search(query, namespaces=ns_list, limit=20)
    finally:
        hive.close()

    if as_json:
        _output({"results": results, "count": len(results)}, as_json=True)
    else:
        if not results:
            typer.echo("No results found.")
            return
        for r in results:
            typer.echo(
                f"[{r.get('namespace', '?')}] {r['key']} "
                f"(conf={r.get('confidence', 0):.2f}): "
                f"{r['value'][:80]}"
            )


@hive_app.command("watch")
def hive_watch(
    since: Annotated[
        int | None,
        typer.Option(help="Only emit when revision is greater than this (default: current)."),
    ] = None,
    poll_ms: Annotated[int, typer.Option(help="Poll interval in milliseconds.")] = 500,
    timeout: Annotated[
        float | None,
        typer.Option(help="With --once, max seconds to wait (default: 3600)."),
    ] = None,
    once: Annotated[
        bool,
        typer.Option(help="Wait for the next write then exit."),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Poll Hive write revision — lightweight pub-sub for new shared memories (GitHub #12).

    Also updates ``~/.tapps-brain/hive/.hive_write_notify`` on each Hive write for file watchers.
    """
    hive = _open_hive_backend_for_cli()
    try:
        state0 = hive.get_write_notify_state()
        baseline = state0["revision"] if since is None else since
        interval = max(50, poll_ms) / 1000.0

        if once:
            cap = 3600.0 if timeout is None else max(0.0, float(timeout))
            result = hive.wait_for_write_notify(
                since_revision=baseline,
                timeout_sec=cap,
                poll_interval_sec=interval,
            )
            if as_json:
                _output(result, as_json=True)
            elif result.get("changed"):
                typer.echo(
                    f"Hive write revision {result['revision']} at {result.get('updated_at', '')}"
                )
            else:
                typer.echo(
                    f"No new writes (revision still {result['revision']}, timed_out="
                    f"{result.get('timed_out', True)})",
                    err=True,
                )
                raise typer.Exit(code=1)
            return

        last = baseline
        typer.echo(
            f"Watching Hive writes (revision >= {last + 1}, poll={poll_ms}ms). Ctrl+C to stop.",
            err=True,
        )
        while True:
            state = hive.get_write_notify_state()
            rev = state["revision"]
            if rev > last:
                payload = {"revision": rev, "updated_at": state.get("updated_at", "")}
                if as_json:
                    typer.echo(json.dumps(payload))
                else:
                    typer.echo(f"revision {rev} ({payload['updated_at']})")
                last = rev
            time.sleep(interval)
    except KeyboardInterrupt:
        typer.echo("Stopped.", err=True)
    finally:
        hive.close()


def _run_hive_push_from_store(
    *,
    agent_scope: str,
    push_all: bool,
    tags_csv: str,
    tier: str | None,
    keys_csv: str,
    dry_run: bool,
    force: bool,
    project_dir: Path | None,
    as_json: bool,
) -> None:
    """Batch-promote local memories to Hive (GitHub #18)."""
    from tapps_brain.backends import (
        push_memory_entries_to_hive,
        select_local_entries_for_hive_push,
    )

    try:
        agent_scope = normalize_agent_scope(agent_scope)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    if agent_scope == "private":
        typer.echo(
            "Error: agent_scope cannot be 'private' for hive push; "
            "use domain, hive, or group:<name>.",
            err=True,
        )
        raise typer.Exit(code=1)

    store = _get_store(project_dir)
    key_list = [k.strip() for k in keys_csv.split(",") if k.strip()]
    tag_list = [t.strip() for t in tags_csv.split(",") if t.strip()] or None
    try:
        try:
            entries = select_local_entries_for_hive_push(
                store,
                push_all=push_all,
                tags=tag_list,
                tier=tier,
                keys=key_list or None,
                include_superseded=False,
            )
        except ValueError as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from None

        shared = getattr(store, "_hive_store", None)
        _should_close = shared is None
        hive = shared if shared is not None else _open_hive_backend_for_cli()
        agent_id = getattr(store, "_hive_agent_id", "cli")
        profile_name = "repo-brain"
        auto_propagate: list[str] | None = None
        private_tiers: list[str] | None = None
        prof = getattr(store, "profile", None)
        if prof is not None:
            profile_name = getattr(prof, "name", "repo-brain")
            hc = getattr(prof, "hive", None)
            if hc is not None:
                auto_propagate = hc.auto_propagate_tiers
                private_tiers = hc.private_tiers

        try:
            report = push_memory_entries_to_hive(
                entries,
                hive_store=hive,
                agent_id=agent_id,
                agent_profile=profile_name,
                agent_scope=agent_scope,
                auto_propagate_tiers=auto_propagate,
                private_tiers=private_tiers,
                bypass_profile_hive_rules=force,
                dry_run=dry_run,
            )
        finally:
            if _should_close:
                hive.close()

        if as_json:
            _output(report, as_json=True)
        else:
            mode = "Dry-run" if dry_run else "Done"
            typer.echo(
                f"{mode}: selected {report['count_selected']}, "
                f"pushed {report['count_pushed']}, "
                f"skipped {report['count_skipped']}, "
                f"failed {report['count_failed']} "
                f"(scope={agent_scope})"
            )
            if report["failed"]:
                for row in report["failed"]:
                    typer.echo(f"  failed: {row['key']}: {row['error']}", err=True)
    finally:
        store.close()


@hive_app.command("push")
def hive_push_cmd(
    scope: Annotated[
        str,
        typer.Option("--scope", help="Hive target: domain | hive | group:<name>."),
    ] = "hive",
    push_all: Annotated[
        bool,
        typer.Option("--all", help="Include all entries; combine with --tags / --tier to narrow."),
    ] = False,
    tags: Annotated[
        str, typer.Option("--tags", help="Comma-separated tags (entry matches any).")
    ] = "",
    tier: Annotated[str | None, typer.Option(help="Filter by memory tier.")] = None,
    keys: Annotated[
        str, typer.Option("--keys", help="Comma-separated local keys (exclusive).")
    ] = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing to Hive.")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            help="Ignore profile private_tiers / auto_propagate; use agent_scope as given.",
        ),
    ] = False,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Push project memories to the Hive in batch (GitHub #18).

    Use ``--keys`` for explicit keys, or ``--all`` and/or ``--tags`` / ``--tier``.
    See also ``hive push-tagged``.
    """
    _run_hive_push_from_store(
        agent_scope=scope,
        push_all=push_all,
        tags_csv=tags,
        tier=tier,
        keys_csv=keys,
        dry_run=dry_run,
        force=force,
        project_dir=project_dir,
        as_json=as_json,
    )


@hive_app.command("push-tagged")
def hive_push_tagged_cmd(
    tag: Annotated[list[str], typer.Argument(help="One or more tags (entry matches any).")],
    scope: Annotated[
        str,
        typer.Option("--scope", help="Hive target: domain | hive | group:<name>."),
    ] = "hive",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing to Hive.")
    ] = False,
    force: Annotated[bool, typer.Option(help="Ignore profile hive tier rules.")] = False,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Push all local memories that carry any of the given tags (GitHub #18)."""
    tags_csv = ",".join(t.strip() for t in tag if t.strip())
    if not tags_csv:
        typer.echo("Error: provide at least one tag.", err=True)
        raise typer.Exit(code=1)
    _run_hive_push_from_store(
        agent_scope=scope,
        push_all=False,
        tags_csv=tags_csv,
        tier=None,
        keys_csv="",
        dry_run=dry_run,
        force=force,
        project_dir=project_dir,
        as_json=as_json,
    )


@agent_app.command("create")
def agent_create(
    agent_id: str,
    profile: Annotated[str, typer.Option(help="Memory profile name.")] = "repo-brain",
    skills: Annotated[str, typer.Option(help="Comma-separated skills.")] = "",
    as_json: JsonFlag = False,
) -> None:
    """Create an agent with profile validation and print namespace and profile summary."""
    from tapps_brain.backends import AgentRegistry
    from tapps_brain.models import AgentRegistration
    from tapps_brain.profile import get_builtin_profile, list_builtin_profiles

    # Validate profile
    try:
        prof = get_builtin_profile(profile)
    except FileNotFoundError:
        available = list_builtin_profiles()
        if as_json:
            _output(
                {
                    "error": "invalid_profile",
                    "message": f"Profile '{profile}' not found.",
                    "available_profiles": available,
                },
                as_json=True,
            )
        else:
            typer.echo(f"Error: Profile '{profile}' not found.", err=True)
            typer.echo(f"Available profiles: {', '.join(available)}", err=True)
        raise typer.Exit(code=1) from None

    # Register agent
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    agent = AgentRegistration(id=agent_id, profile=profile, skills=skill_list)
    registry = AgentRegistry()
    registry.register(agent)

    # Namespace = profile name (same as PropagationEngine)
    namespace = profile
    layer_names = [layer.name for layer in prof.layers]
    profile_summary = {
        "name": prof.name,
        "version": prof.version,
        "layers": layer_names,
        "description": prof.description,
    }

    if as_json:
        _output(
            {
                "created": True,
                "agent_id": agent_id,
                "profile": profile,
                "namespace": namespace,
                "skills": skill_list,
                "profile_summary": profile_summary,
            },
            as_json=True,
        )
    else:
        typer.echo(f"Created agent '{agent_id}'.")
        typer.echo(f"  Profile:   {profile}")
        typer.echo(f"  Namespace: {namespace}")
        typer.echo(f"  Layers:    {', '.join(layer_names)}")
        if prof.description:
            typer.echo(f"  Desc:      {prof.description}")
        if skill_list:
            typer.echo(f"  Skills:    {', '.join(skill_list)}")


@agent_app.command("register")
def agent_register(
    agent_id: str,
    profile: Annotated[str, typer.Option(help="Memory profile name.")] = "repo-brain",
    skills: Annotated[str, typer.Option(help="Comma-separated skills.")] = "",
) -> None:
    """Register an agent in the Hive."""
    from tapps_brain.backends import AgentRegistry
    from tapps_brain.models import AgentRegistration

    registry = AgentRegistry()
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    agent = AgentRegistration(id=agent_id, profile=profile, skills=skill_list)
    registry.register(agent)
    typer.echo(f"Registered agent '{agent_id}' with profile '{profile}'.")


@agent_app.command("list")
def agent_list(as_json: JsonFlag = False) -> None:
    """List all registered agents in the Hive."""
    from tapps_brain.backends import AgentRegistry

    registry = AgentRegistry()
    agents = registry.list_agents()

    if as_json:
        _output(
            {"agents": [a.model_dump(mode="json") for a in agents], "count": len(agents)},
            as_json=True,
        )
    else:
        if not agents:
            typer.echo("No registered agents.")
            return
        for a in agents:
            skills = ", ".join(a.skills) if a.skills else "none"
            typer.echo(f"  {a.id} (profile={a.profile}, skills={skills})")


@agent_app.command("delete")
def agent_delete(
    agent_id: str,
    as_json: JsonFlag = False,
) -> None:
    """Delete a registered agent from the Hive."""
    from tapps_brain.backends import AgentRegistry

    registry = AgentRegistry()
    removed = registry.unregister(agent_id)

    if as_json:
        _output({"deleted": removed, "agent_id": agent_id}, as_json=True)
    elif removed:
        typer.echo(f"Deleted agent '{agent_id}'.")
    else:
        typer.echo(f"Agent '{agent_id}' not found.", err=True)
        raise typer.Exit(code=1)


# ===================================================================
# OPENCLAW COMMANDS
# ===================================================================


@openclaw_app.command("init")
def openclaw_init(
    project_dir: str = typer.Option(".", help="Project directory"),
) -> None:
    """Initialize a workspace with correct tapps-brain memory hierarchy."""
    from pathlib import Path

    root = Path(project_dir)

    # Create .tapps-brain dir if needed
    tb_dir = root / ".tapps-brain"
    tb_dir.mkdir(parents=True, exist_ok=True)

    # Write default profile if not exists
    profile_path = tb_dir / "profile.yaml"
    if not profile_path.exists():
        profile_path.write_text(
            "profile:\n  extends: personal-assistant\n  layers: []\n  name: personal-assistant\n",
            encoding="utf-8",
        )
        typer.echo(f"Created {profile_path}")

    # Create memory dir
    mem_dir = tb_dir / "memory"
    mem_dir.mkdir(exist_ok=True)

    typer.echo("✅ Workspace initialized for tapps-brain")


@openclaw_app.command("upgrade")
def openclaw_upgrade(
    project_dir: str = typer.Option(".", help="Project directory"),
) -> None:
    """Upgrade workspace — export MEMORY.md from tapps-brain entries."""
    from pathlib import Path

    from tapps_brain.store import MemoryStore

    root = Path(project_dir)
    store = MemoryStore(root, agent_id=_cli_agent_id)
    entries = store.list_all()

    # Export identity + long-term entries to MEMORY.md
    memory_md = root / "MEMORY.md"
    lines = ["# MEMORY.md — Auto-generated from tapps-brain\n\n"]
    lines.append(f"*Exported {len(entries)} total entries*\n\n")

    for entry in sorted(entries, key=lambda e: e.tier):
        tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        if tier in ("identity", "long-term"):
            lines.append(f"## {entry.key}\n")
            lines.append(f"**Tier:** {tier} | **Confidence:** {entry.confidence:.2f}\n\n")
            lines.append(f"{entry.value}\n\n---\n\n")

    memory_md.write_text("".join(lines), encoding="utf-8")
    typer.echo(f"✅ Exported {len(entries)} entries to {memory_md}")


# ===================================================================
# FEEDBACK COMMANDS (EPIC-029 / STORY-029.5)
# ===================================================================


def _feedback_parse_details_option(details_json: str | None) -> dict[str, Any] | None:
    """Parse ``--details-json`` or return ``None`` when unset/empty."""
    if details_json is None or not details_json.strip():
        return None
    try:
        data = json.loads(details_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON in --details-json: {exc}", err=True)
        raise typer.Exit(code=1) from None
    if not isinstance(data, dict):
        typer.echo("--details-json must be a JSON object.", err=True)
        raise typer.Exit(code=1)
    return data


def _feedback_event_to_row(ev: Any) -> dict[str, Any]:  # noqa: ANN401
    """Table row for a ``FeedbackEvent``."""
    score = "" if ev.utility_score is None else f"{ev.utility_score:.3f}"
    return {
        "event_type": ev.event_type,
        "entry_key": ev.entry_key or "",
        "session_id": ev.session_id or "",
        "utility": score,
        "timestamp": ev.timestamp,
        "id": ev.id,
    }


def _emit_feedback_event(ev: Any, *, as_json: bool) -> None:  # noqa: ANN401
    payload = {"status": "recorded", "event": ev.model_dump(mode="json")}
    if as_json:
        _output(payload, as_json=True)
    else:
        typer.echo(f"Recorded {ev.event_type} (id={ev.id})")


@feedback_app.command("rate")
def feedback_rate_cmd(
    entry_key: Annotated[str, typer.Argument(help="Memory key that was recalled.")],
    project_dir: ProjectDir = None,
    rating: Annotated[
        str,
        typer.Option(help="One of: helpful, partial, irrelevant, outdated."),
    ] = "helpful",
    session_id: Annotated[str | None, typer.Option(help="Calling session id.")] = None,
    details_json: Annotated[
        str | None,
        typer.Option("--details-json", help="JSON object merged into event details."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Record a recall quality rating (``recall_rated``)."""
    details = _feedback_parse_details_option(details_json)
    store = _get_store(project_dir)
    try:
        try:
            ev = store.rate_recall(
                entry_key,
                rating=rating,
                session_id=session_id,
                details=details,
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        _emit_feedback_event(ev, as_json=as_json)
    finally:
        store.close()


@feedback_app.command("gap")
def feedback_gap_cmd(
    query: Annotated[str, typer.Argument(help="Query or topic that was not well served.")],
    project_dir: ProjectDir = None,
    session_id: Annotated[str | None, typer.Option(help="Calling session id.")] = None,
    details_json: Annotated[
        str | None,
        typer.Option("--details-json", help="JSON object merged into event details."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Report a knowledge gap (``gap_reported``)."""
    details = _feedback_parse_details_option(details_json)
    store = _get_store(project_dir)
    try:
        ev = store.report_gap(query, session_id=session_id, details=details)
        _emit_feedback_event(ev, as_json=as_json)
    finally:
        store.close()


@feedback_app.command("issue")
def feedback_issue_cmd(
    entry_key: Annotated[str, typer.Argument(help="Memory entry key.")],
    issue: Annotated[str, typer.Argument(help="Description of the quality issue.")],
    project_dir: ProjectDir = None,
    session_id: Annotated[str | None, typer.Option(help="Calling session id.")] = None,
    details_json: Annotated[
        str | None,
        typer.Option("--details-json", help="JSON object merged into event details."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Flag an issue with a memory entry (``issue_flagged``)."""
    details = _feedback_parse_details_option(details_json)
    store = _get_store(project_dir)
    try:
        ev = store.report_issue(entry_key, issue, session_id=session_id, details=details)
        _emit_feedback_event(ev, as_json=as_json)
    finally:
        store.close()


@feedback_app.command("record")
def feedback_record_cmd(
    event_type: Annotated[str, typer.Argument(help="Object-Action snake_case event name.")],
    project_dir: ProjectDir = None,
    entry_key: Annotated[str | None, typer.Option(help="Related memory key.")] = None,
    session_id: Annotated[str | None, typer.Option(help="Calling session id.")] = None,
    utility_score: Annotated[
        float | None,
        typer.Option(help="Numeric signal in [-1.0, 1.0]."),
    ] = None,
    details_json: Annotated[
        str | None,
        typer.Option("--details-json", help="JSON object event details."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Record a generic feedback event (custom or built-in type)."""
    details = _feedback_parse_details_option(details_json)
    store = _get_store(project_dir)
    try:
        try:
            ev = store.record_feedback(
                event_type,
                entry_key=entry_key,
                session_id=session_id,
                utility_score=utility_score,
                details=details,
            )
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from None
        _emit_feedback_event(ev, as_json=as_json)
    finally:
        store.close()


@feedback_app.command("list")
def feedback_list_cmd(
    project_dir: ProjectDir = None,
    event_type: Annotated[str | None, typer.Option(help="Filter by event type.")] = None,
    entry_key: Annotated[str | None, typer.Option(help="Filter by memory key.")] = None,
    session_id: Annotated[str | None, typer.Option(help="Filter by session.")] = None,
    since: Annotated[str | None, typer.Option(help="ISO-8601 lower bound (inclusive).")] = None,
    until: Annotated[str | None, typer.Option(help="ISO-8601 upper bound (inclusive).")] = None,
    limit: Annotated[int, typer.Option(help="Max events.", min=1, max=10_000)] = 100,
    as_json: JsonFlag = False,
) -> None:
    """List feedback events with optional filters."""
    store = _get_store(project_dir)
    try:
        events = store.query_feedback(
            event_type=event_type,
            entry_key=entry_key,
            session_id=session_id,
            since=since,
            until=until,
            limit=limit,
        )
        if as_json:
            _output([e.model_dump(mode="json") for e in events], as_json=True)
        else:
            rows = [_feedback_event_to_row(e) for e in events]
            _print_table(
                rows,
                columns=["event_type", "entry_key", "session_id", "utility", "timestamp", "id"],
            )
            typer.echo(f"\n{len(events)} event(s)")
    finally:
        store.close()


def _diagnostics_sre_status(circuit_state: str) -> str:
    return {
        "closed": "Operational",
        "degraded": "Degraded",
        "half_open": "Partial Outage",
        "open": "Major Outage",
    }.get(circuit_state, circuit_state)


def _dimension_grade_letter(score: float) -> str:
    if score >= _DIAG_GRADE_A_MIN:
        return "A"
    if score >= _DIAG_GRADE_B_MIN:
        return "B"
    if score >= _DIAG_GRADE_C_MIN:
        return "C"
    if score >= _DIAG_GRADE_D_MIN:
        return "D"
    return "F"


def _circuit_status_color(circuit_state: str) -> str:
    if circuit_state == "closed":
        return typer.colors.GREEN
    if circuit_state in ("degraded", "half_open"):
        return typer.colors.YELLOW
    return typer.colors.RED


@diagnostics_app.command("health")
def diagnostics_health_cmd(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
    no_hive: Annotated[
        bool,
        typer.Option("--no-hive", help="Skip Hive connectivity check."),
    ] = False,
) -> None:
    """Run a native health check: store, hive, integrity status in one call.

    Exit codes: 0 = all green, 1 = warnings, 2 = errors.
    """
    from tapps_brain.health_check import run_health_check

    root = Path(project_dir) if project_dir else None
    report = run_health_check(project_root=root, check_hive=not no_hive)

    if as_json:
        _output(report.model_dump(mode="json"), as_json=True)
    else:
        status_icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(report.status, "?")
        typer.echo(f"{status_icon} tapps-brain health: {report.status.upper()}")
        typer.echo(f"   Generated: {report.generated_at}  ({report.elapsed_ms:.0f}ms)")
        typer.echo("")

        # Store
        s = report.store
        s_icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(s.status, "?")
        sz_kb = s.size_bytes // 1024
        typer.echo(
            f"Store {s_icon}: {s.entries}/{s.max_entries} entries  "
            f"schema={s.schema_version}  size={sz_kb}KB"
        )
        typer.echo(f"  pgvector HNSW: on  ({s.vector_index_rows} vectors indexed)")
        if getattr(s, "retrieval_effective_mode", "unknown") != "unknown":
            typer.echo(f"  Retrieval: {s.retrieval_effective_mode}")
            if s.retrieval_summary:
                typer.echo(f"    {s.retrieval_summary}")
        if getattr(s, "save_phase_summary", ""):
            typer.echo(f"  Save phases: {s.save_phase_summary}")
        if s.tiers:
            typer.echo(f"  Tiers: {', '.join(f'{k}={v}' for k, v in sorted(s.tiers.items()))}")

        # Hive
        h = report.hive
        h_icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(h.status, "?")
        if h.connected:
            ns = ", ".join(h.namespaces)
            typer.echo(f"Hive  {h_icon}: {h.entries} entries  {h.agents} agents  ns={ns}")
        else:
            typer.echo(f"Hive  {h_icon}: not connected")

        # Integrity
        i = report.integrity
        i_icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}.get(i.status, "?")
        typer.echo(
            f"Integ {i_icon}: corrupted={i.corrupted_entries}  "
            f"orphaned={i.orphaned_relations}  expired={i.expired_entries}"
        )

        if report.errors:
            typer.echo("")
            typer.echo("Errors:")
            for err in report.errors:
                typer.echo(f"  ❌ {err}")
        if report.warnings:
            typer.echo("")
            typer.echo("Warnings:")
            for warn in report.warnings:
                typer.echo(f"  ⚠️  {warn}")

    raise typer.Exit(code=report.exit_code())


@diagnostics_app.command("report")
def diagnostics_report_cmd(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
    record_history: Annotated[
        bool,
        typer.Option(help="Persist this snapshot to diagnostics_history."),
    ] = True,
) -> None:
    """Run quality diagnostics: composite score, dimensions, circuit breaker state."""
    store = _get_store(project_dir)
    try:
        rep = store.diagnostics(record_history=record_history)
        if as_json:
            _output(rep.model_dump(mode="json"), as_json=True)
            return
        status = _diagnostics_sre_status(rep.circuit_state)
        typer.secho(
            f"Status: {status} (circuit={rep.circuit_state})",
            fg=_circuit_status_color(rep.circuit_state),
        )
        typer.echo(f"Composite score: {rep.composite_score:.4f}")
        if rep.hive_composite_score is not None:
            typer.echo(f"Hive composite (worst-of): {rep.hive_composite_score:.4f}")
        typer.echo(f"Gap signals (feedback): {rep.gap_count}")
        typer.echo(f"Correlation-adjusted weights: {rep.correlation_adjusted}")
        typer.echo("")
        typer.echo("Dimensions (grade):")
        rows: list[dict[str, Any]] = []
        for name, ds in sorted(rep.dimensions.items()):
            rows.append(
                {
                    "dimension": name,
                    "score": f"{ds.score:.4f}",
                    "grade": _dimension_grade_letter(ds.score),
                },
            )
        if rows:
            _print_table(rows, columns=["dimension", "score", "grade"])
        if rep.recommendations:
            typer.echo("")
            typer.echo("Recommendations:")
            for line in rep.recommendations:
                typer.echo(f"  - {line}")
        if rep.anomalies:
            typer.echo("")
            typer.echo("Anomalies:")
            for a in rep.anomalies:
                typer.echo(f"  - {a}")
    finally:
        store.close()


@diagnostics_app.command("history")
def diagnostics_history_cmd(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
    limit: Annotated[int, typer.Option(help="Max rows.", min=1, max=500)] = 50,
) -> None:
    """List recent persisted diagnostics snapshots."""
    store = _get_store(project_dir)
    try:
        hist = store.diagnostics_history(limit=limit)
        if as_json:
            _output(hist, as_json=True)
            return
        if not hist:
            typer.echo("No diagnostics history yet.")
            return
        slim = [
            {
                "recorded_at": r.get("recorded_at", ""),
                "composite": f"{float(r.get('composite_score', 0.0)):.4f}",
                "circuit": r.get("circuit_state", ""),
                "id": str(r.get("id", ""))[:8],
            }
            for r in hist
        ]
        _print_table(slim, columns=["recorded_at", "composite", "circuit", "id"])
        typer.echo(f"\n{len(hist)} row(s)")
    finally:
        store.close()


@flywheel_app.command("process")
def flywheel_process_cmd(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
    since: Annotated[str, typer.Option(help="ISO-8601 lower bound for applying events.")] = "",
) -> None:
    """Apply feedback events to memory confidence (Bayesian update)."""
    store = _get_store(project_dir)
    try:
        res = store.process_feedback(since=since.strip() or None)
        _output(res, as_json=as_json)
    finally:
        store.close()


@flywheel_app.command("gaps")
def flywheel_gaps_cmd(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 10,
    semantic: Annotated[
        bool, typer.Option(help="Use optional embedding+HDBSCAN clustering.")
    ] = False,
) -> None:
    """List prioritized knowledge gaps."""
    store = _get_store(project_dir)
    try:
        gaps = store.knowledge_gaps(limit=limit, semantic=semantic)
        rows = [g.model_dump(mode="json") for g in gaps]
        if as_json:
            _output({"gaps": rows, "count": len(rows)}, as_json=True)
            return
        if not rows:
            typer.echo("No clustered gaps.")
            return
        slim = [
            {
                "pattern": r.get("query_pattern", "")[:60],
                "priority": f"{float(r.get('priority_score', 0.0)):.4f}",
                "count": str(r.get("count", "")),
            }
            for r in rows
        ]
        _print_table(slim, columns=["pattern", "priority", "count"])
    finally:
        store.close()


@flywheel_app.command("report")
def flywheel_report_cmd(
    project_dir: ProjectDir = None,
    period: Annotated[int, typer.Option("--period", help="Days of history window.")] = 7,
    output_format: Annotated[str, typer.Option("--format", help="json or markdown.")] = "markdown",
) -> None:
    """Generate a quality self-report."""
    store = _get_store(project_dir)
    try:
        rep = store.generate_report(period_days=period)
        fmt = output_format.strip().lower()
        if fmt == "json":
            _output(rep.model_dump(mode="json"), as_json=True)
        else:
            typer.echo(rep.rendered_text)
    finally:
        store.close()


@flywheel_app.command("evaluate")
def flywheel_evaluate_cmd(
    suite_path: Annotated[str, typer.Argument(help="BEIR directory or YAML suite file.")],
    project_dir: ProjectDir = None,
    k: Annotated[int, typer.Option(min=1, max=50)] = 5,
    output_format: Annotated[str, typer.Option("--format", help="json or table.")] = "json",
) -> None:
    """Run offline retrieval evaluation against the store."""
    from tapps_brain.evaluation import EvalSuite, evaluate

    store = _get_store(project_dir)
    try:
        p = Path(suite_path).expanduser().resolve()
        if not p.exists():
            typer.secho(f"Not found: {p}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        if p.is_dir():
            suite = EvalSuite.load_beir_dir(p)
        elif p.suffix.lower() in (".yaml", ".yml"):
            suite = EvalSuite.load_yaml(p)
        else:
            typer.secho("Expected a BEIR directory or .yaml suite.", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        report = evaluate(store, suite, k=k)
        fmt = output_format.strip().lower()
        if fmt == "table":
            typer.echo(f"Suite: {report.suite_name}  k={report.k}  passed={report.passed}")
            typer.echo(f"MRR={report.mrr:.4f}  NDCG@k={report.mean_ndcg_at_k:.4f}")
            for row in report.per_query:
                typer.echo(
                    f"  {row.query_id}: P@k={row.precision_at_k:.4f} "
                    f"R@k={row.recall_at_k:.4f} RR={row.reciprocal_rank:.4f} "
                    f"nDCG={row.ndcg_at_k:.4f}"
                )
        else:
            _output(report.model_dump(mode="json"), as_json=True)
    finally:
        store.close()


@flywheel_app.command("hive-feedback")
def flywheel_hive_feedback_cmd(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
    threshold: Annotated[int, typer.Option(min=1, help="Min projects with negative signal.")] = 3,
) -> None:
    """Aggregate Hive feedback and apply cross-project confidence penalties."""
    from tapps_brain.flywheel import aggregate_hive_feedback, process_hive_feedback

    store = _get_store(project_dir)
    try:
        hs = getattr(store, "_hive_store", None)
        agg = aggregate_hive_feedback(hs)
        proc = process_hive_feedback(hs, threshold=threshold)
        out = {"aggregate": None if agg is None else agg.model_dump(mode="json"), "process": proc}
        _output(out, as_json=as_json)
    finally:
        store.close()


# ===================================================================
# SESSION COMMANDS (Issue #17)
# ===================================================================


@session_app.command("end")
def session_end_cmd(
    summary: Annotated[str, typer.Argument(help="Session summary text.")],
    project_dir: ProjectDir = None,
    tags: Annotated[
        list[str],
        typer.Option("--tag", "-t", help="Additional tags (can be repeated)."),
    ] = [],  # noqa: B006
    daily_note: Annotated[
        bool,
        typer.Option(
            "--daily-note",
            help="Append formatted summary to memory/YYYY-MM-DD.md.",
        ),
    ] = False,
    workspace_dir: Annotated[
        Path | None,
        typer.Option(
            "--workspace-dir",
            "-w",
            help="Workspace root for --daily-note (defaults to project-dir or cwd).",
        ),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Record an end-of-session episodic memory entry.

    Creates a short-term episodic memory tagged with 'date', 'session',
    and 'episodic'. Optionally appends a formatted summary to today's
    daily note file (memory/YYYY-MM-DD.md).
    """
    from tapps_brain.session_summary import session_summary_save

    root = _resolve_project_dir(project_dir)
    ws = workspace_dir.resolve() if workspace_dir else root

    all_tags = list(tags)
    result = session_summary_save(summary, tags=all_tags, project_dir=root)

    if daily_note:
        _write_daily_note(ws, summary)

    if as_json:
        _output(result, as_json=True)
    else:
        key = result.get("key", "")
        typer.secho(f"✓ Session memory saved: {key}", fg=typer.colors.GREEN)
        if daily_note:
            typer.echo(f"  Daily note updated in {ws / 'memory'}")


def _write_daily_note(workspace: Path, summary: str) -> None:
    """Append a formatted session summary to today's daily note file."""
    import datetime

    today = datetime.date.today().isoformat()
    note_dir = workspace / "memory"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{today}.md"

    from datetime import UTC
    from datetime import datetime as dt

    timestamp = dt.now(tz=UTC).strftime("%H:%M UTC")
    block = f"\n## Session End — {timestamp}\n\n{summary}\n"

    with open(note_path, "a") as f:
        f.write(block)


# ===================================================================
# RELAY (GitHub #19)
# ===================================================================


@relay_app.command("import")
def relay_import_cmd(
    file: Annotated[
        Path | None,
        typer.Argument(help="Relay JSON file (omit when using --stdin)."),
    ] = None,
    stdin: Annotated[
        bool,
        typer.Option("--stdin", help="Read relay JSON from stdin."),
    ] = False,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Import memories from a structured relay JSON file or stdin.

    See docs/guides/memory-relay.md for the relay_version 1.0 schema.
    Invalid items are skipped with warnings; the command exits 0 unless the
    envelope JSON is unusable.
    """
    import sys as _sys

    from tapps_brain.memory_relay import import_relay_to_store, parse_relay_document

    if stdin and file is not None:
        typer.echo("Use either a FILE or --stdin, not both.", err=True)
        raise typer.Exit(code=1)
    if not stdin:
        if file is None:
            typer.echo("Provide a FILE or use --stdin.", err=True)
            raise typer.Exit(code=1)
        if not file.is_file():
            typer.echo(f"File not found: {file}", err=True)
            raise typer.Exit(code=1)
        raw = file.read_text(encoding="utf-8")
    else:
        raw = _sys.stdin.read()

    payload, err = parse_relay_document(raw)
    if payload is None:
        typer.echo(f"Invalid relay document: {err}", err=True)
        raise typer.Exit(code=1)

    store = _get_store(project_dir)
    try:
        outcome = import_relay_to_store(store, payload)
        if as_json:
            _output(outcome.to_dict(), as_json=True)
        else:
            typer.echo(f"Imported {outcome.imported} entr(y/ies); skipped {outcome.skipped}.")
            for w in outcome.warnings:
                typer.secho(f"  ⚠ {w}", fg=typer.colors.YELLOW)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Visual snapshot (brain-visual.json)
# ---------------------------------------------------------------------------


@visual_app.command("export")
def visual_export_cmd(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output path for brain-visual.json (default: ./brain-visual.json).",
        ),
    ] = Path("brain-visual.json"),
    project_dir: ProjectDir = None,
    skip_diagnostics: Annotated[
        bool,
        typer.Option(
            "--skip-diagnostics",
            help="Skip store diagnostics (faster; omits circuit_state/composite_score).",
        ),
    ] = False,
    privacy: Annotated[
        str,
        typer.Option(
            "--privacy",
            help=(
                "standard (default) | strict (redact path/tampered keys) | "
                "local (tag + group names)."
            ),
        ),
    ] = "standard",
) -> None:
    """Write a versioned JSON snapshot for the static brain visual demo and dashboards."""
    from tapps_brain.visual_snapshot import PrivacyTier, build_visual_snapshot, snapshot_to_json

    if privacy not in {"standard", "strict", "local"}:
        typer.echo("Error: --privacy must be standard, strict, or local.", err=True)
        raise typer.Exit(code=1)
    tier = cast("PrivacyTier", privacy)
    store = _get_store(project_dir)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=skip_diagnostics, privacy=tier)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(snapshot_to_json(snap), encoding="utf-8")
    finally:
        store.close()
    typer.echo(f"Wrote {output.resolve()}")


@visual_app.command("capture")
def visual_capture_cmd(  # pragma: no cover
    json_path: Annotated[
        Path,
        typer.Option(
            "--json",
            "-j",
            help="Path to brain-visual.json snapshot (required).",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Destination PNG path (default: brain-visual.png).",
        ),
    ] = Path("brain-visual.png"),
    html: Annotated[
        Path,
        typer.Option(
            "--html",
            help="Path to examples/brain-visual/index.html.",
        ),
    ] = Path("examples/brain-visual/index.html"),
    width: Annotated[
        int,
        typer.Option("--width", help="Viewport width in px (default 1280)."),
    ] = 1280,
    height: Annotated[
        int,
        typer.Option("--height", help="Viewport height in px (default 900)."),
    ] = 900,
    theme: Annotated[
        str,
        typer.Option("--theme", help="light (default) or dark."),
    ] = "light",
) -> None:
    """Capture a headless PNG of the brain-visual dashboard.

    Requires the [visual] optional extra:

        uv sync --extra visual
        playwright install chromium
    """
    from tapps_brain.visual_snapshot import capture_png

    if theme not in {"light", "dark"}:
        typer.echo("Error: --theme must be light or dark.", err=True)
        raise typer.Exit(code=1)
    try:
        capture_png(
            html_path=html,
            json_path=json_path,
            output=output,
            width=width,
            height=height,
            theme=theme,
        )
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {output.resolve()}")


# ---------------------------------------------------------------------------
# HTTP adapter server (EPIC-067 STORY-067.1)
# ---------------------------------------------------------------------------


@app.command("serve")
def cmd_serve(  # noqa: PLR0915  # orchestrator: many independent startup steps
    host: Annotated[
        str,
        typer.Option("--host", envvar="TAPPS_BRAIN_HTTP_HOST", help="Bind address."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", envvar="TAPPS_BRAIN_HTTP_PORT", help="HTTP data-plane TCP port."),
    ] = 8080,
    dsn: Annotated[
        str | None,
        typer.Option(
            "--dsn",
            envvar="TAPPS_BRAIN_HIVE_DSN",
            help="Postgres DSN for /ready probe (falls back to TAPPS_BRAIN_DATABASE_URL).",
        ),
    ] = None,
    mcp_host: Annotated[
        str,
        typer.Option(
            "--mcp-host",
            envvar="TAPPS_BRAIN_MCP_HOST",
            help="Bind address for the Streamable-HTTP MCP transport.",
        ),
    ] = "127.0.0.1",
    mcp_port: Annotated[
        int,
        typer.Option(
            "--mcp-port",
            envvar="TAPPS_BRAIN_MCP_HTTP_PORT",
            help=(
                "TCP port for the operator Streamable-HTTP MCP transport. "
                "Set to 0 (default) to disable the MCP transport and run "
                "HTTP-only (legacy behaviour). "
                "STORY-070.15: set to 8090 in Docker for the unified binary."
            ),
        ),
    ] = 0,
) -> None:
    """Start the HTTP adapter (liveness, readiness, metrics, /snapshot).

    STORY-070.15: when --mcp-port > 0 (or TAPPS_BRAIN_MCP_HTTP_PORT is set),
    the operator Streamable-HTTP MCP server is also started in the same process
    on the given port, making a single container sufficient for both transports.

    Reads TAPPS_BRAIN_HTTP_AUTH_TOKEN and TAPPS_BRAIN_HIVE_DSN from the
    environment automatically; the --dsn flag overrides the env var.

    Blocks until interrupted (SIGINT / SIGTERM).  Graceful shutdown stops
    both transports before exiting.
    """
    import os
    import signal

    from tapps_brain.http_adapter import HttpAdapter

    store = None
    if os.environ.get("TAPPS_BRAIN_DATABASE_URL"):
        from tapps_brain.backends import resolve_hive_backend_from_env
        from tapps_brain.store import MemoryStore

        project_root = Path(os.environ.get("TAPPS_BRAIN_SERVE_ROOT", "/var/lib/tapps-brain"))
        project_root.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(
            project_root,
            agent_id="http-adapter",
            hive_store=resolve_hive_backend_from_env(),
            hive_agent_id="http-adapter",
        )

    # ---- Security: warn when binding to all interfaces without auth ------
    # Mirror _Settings._resolve_auth_token so _FILE variants (Docker Secrets)
    # are also recognised as "auth configured".
    _auth_configured = bool(
        os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN_FILE")
        or os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN")
        or os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE")
        or os.environ.get("TAPPS_BRAIN_PER_TENANT_AUTH") == "1"
    )
    if host == "0.0.0.0" and not _auth_configured:
        structlog.get_logger(__name__).warning(
            "http_adapter.bind_all_interfaces_unauthenticated",
            host=host,
            port=port,
            advice=(
                "Set TAPPS_BRAIN_AUTH_TOKEN (or TAPPS_BRAIN_AUTH_TOKEN_FILE) "
                "or TAPPS_BRAIN_PER_TENANT_AUTH=1 when binding to 0.0.0.0, "
                "or restrict to 127.0.0.1."
            ),
        )
    if mcp_port > 0 and mcp_host == "0.0.0.0" and not _auth_configured:
        structlog.get_logger(__name__).warning(
            "http_adapter.mcp_bind_all_interfaces_unauthenticated",
            mcp_host=mcp_host,
            mcp_port=mcp_port,
            advice=(
                "Set TAPPS_BRAIN_AUTH_TOKEN (or TAPPS_BRAIN_AUTH_TOKEN_FILE) "
                "or TAPPS_BRAIN_PER_TENANT_AUTH=1 when binding to 0.0.0.0, "
                "or restrict --mcp-host to 127.0.0.1."
            ),
        )

    # ---- HTTP data-plane ------------------------------------------------
    adapter = HttpAdapter(host=host, port=port, dsn=dsn, store=store)
    adapter.start()
    typer.echo(f"tapps-brain HTTP adapter listening on {host}:{port}")

    # ---- Streamable-HTTP MCP transport (STORY-070.15) -------------------
    # Bearer token auth: reuses TAPPS_BRAIN_ADMIN_TOKEN (the operator-plane
    # credential already established in EPIC-069).  When unset the transport
    # refuses to start — unauthenticated operator access is not permitted on
    # a multi-agent host.
    _operator_token: str | None = os.environ.get("TAPPS_BRAIN_ADMIN_TOKEN")

    mcp_thread: threading.Thread | None = None
    mcp_server_obj: object = None
    if mcp_port > 0:
        if not _operator_token:
            typer.echo(
                "ERROR: TAPPS_BRAIN_ADMIN_TOKEN must be set to enable the operator MCP "
                "transport on port {mcp_port}. Set the token or disable MCP with --mcp-port 0.",
                err=True,
            )
            raise typer.Exit(1)
        try:
            from tapps_brain.mcp_server import create_operator_server

            mcp_server_obj = create_operator_server(
                None,  # project_dir resolved from env inside create_operator_server
                enable_hive=True,
                agent_id=os.environ.get("TAPPS_BRAIN_AGENT_ID", "serve-operator"),
            )

            # Capture locals for the thread closure.
            _mcp_host = mcp_host
            _mcp_port = mcp_port
            _tok = _operator_token

            def _run_mcp() -> None:
                import contextlib as _contextlib

                import uvicorn as _uvicorn

                # Obtain the FastMCP Streamable HTTP ASGI app.
                _asgi: object = None
                for _attr in ("streamable_http_app", "streamable_http"):
                    _fn = getattr(mcp_server_obj, _attr, None)
                    if callable(_fn):
                        with _contextlib.suppress(TypeError):
                            _asgi = _fn()
                        if _asgi is not None:
                            break

                if _asgi is None:
                    # FastMCP version too old to expose the ASGI app directly;
                    # fall back to its own runner (no auth middleware possible).
                    mcp_server_obj.run(transport="streamable-http")  # type: ignore[attr-defined]
                    return

                # Wrap with a minimal bearer-token ASGI middleware.
                _bearer_prefix = "bearer "
                _inner = _asgi

                async def _authed_app(scope: object, receive: object, send: object) -> None:
                    _scope = scope
                    if isinstance(_scope, dict) and _scope.get("type") == "http":
                        _hdrs: dict[bytes, bytes] = {
                            k.lower(): v for k, v in _scope.get("headers", [])
                        }
                        _auth = _hdrs.get(b"authorization", b"").decode()
                        if not _auth.lower().startswith(_bearer_prefix) or _auth[7:] != _tok:
                            await send(  # type: ignore[operator]
                                {
                                    "type": "http.response.start",
                                    "status": 401,
                                    "headers": [(b"content-type", b"application/json")],
                                }
                            )
                            await send(  # type: ignore[operator]
                                {
                                    "type": "http.response.body",
                                    "body": b'{"error":"unauthorized",'
                                    b'"detail":"Bearer token required for operator MCP."}',
                                }
                            )
                            return
                    await _inner(scope, receive, send)  # type: ignore[operator]

                _uvicorn.run(
                    _authed_app,
                    host=_mcp_host,
                    port=_mcp_port,
                    log_level="warning",
                )

            mcp_thread = threading.Thread(target=_run_mcp, daemon=True, name="tapps-brain-mcp")
            mcp_thread.start()
            typer.echo(
                f"tapps-brain operator MCP (streamable-http, auth=on) "
                f"listening on {mcp_host}:{mcp_port}"
            )
        except ImportError:
            typer.echo(
                "WARNING: mcp extra not installed — operator MCP transport disabled. "
                "Install with: pip install 'tapps-brain[mcp]'",
                err=True,
            )

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stop_event.wait()

    # ---- Graceful shutdown ----------------------------------------------
    typer.echo("tapps-brain: shutting down…")
    adapter.stop()
    # MCP daemon thread exits automatically when the process terminates;
    # FastMCP does not expose a public stop() API, so we just join briefly.
    if mcp_thread is not None and mcp_thread.is_alive():
        mcp_thread.join(timeout=5.0)
    typer.echo("tapps-brain: stopped.")


# ---------------------------------------------------------------------------
# EPIC-069: project registry commands
# ---------------------------------------------------------------------------


def _open_project_registry() -> tuple[Any, Any]:
    """Build a :class:`ProjectRegistry` against the env DSN.

    Returns ``(registry, connection_manager)`` so the caller can close
    the pool when it's done.
    """
    import os

    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.project_registry import ProjectRegistry

    dsn = (os.environ.get("TAPPS_BRAIN_DATABASE_URL") or "").strip()
    if not dsn:
        typer.echo(
            "error: TAPPS_BRAIN_DATABASE_URL must be set (postgres:// or postgresql:// DSN).",
            err=True,
        )
        raise typer.Exit(code=2)
    cm = PostgresConnectionManager(dsn)
    return ProjectRegistry(cm), cm


@project_app.command("register")
def project_register(
    project_id: str = typer.Argument(..., help="Project slug (e.g. 'alpaca')."),
    profile_path: Path = typer.Option(
        ...,
        "--profile",
        "-p",
        exists=True,
        readable=True,
        help="Path to a profile.yaml seed document.",
    ),
    approved: bool = typer.Option(
        True, "--approved/--pending", help="Mark registered row approved."
    ),
    source: str = typer.Option("admin", "--source", help="admin|auto|import"),
    notes: str = typer.Option("", "--notes", help="Optional notes for admin audit."),
) -> None:
    """Register (or overwrite) a project profile from a YAML seed file."""
    from tapps_brain.profile import load_profile

    profile = load_profile(profile_path)
    registry, cm = _open_project_registry()
    try:
        record = registry.register(
            project_id,
            profile,
            source=source,
            approved=approved,
            notes=notes,
        )
    finally:
        cm.close()
    typer.echo(
        f"Registered project '{record.project_id}' "
        f"(profile={record.profile.name}, approved={record.approved}, "
        f"source={record.source})"
    )


@project_app.command("list")
def project_list(
    approved_only: bool = typer.Option(False, "--approved-only", help="Only show approved rows."),
    pending_only: bool = typer.Option(
        False, "--pending-only", help="Only show pending (unapproved) rows."
    ),
) -> None:
    """List registered projects."""
    registry, cm = _open_project_registry()
    approved_filter: bool | None = None
    if approved_only and pending_only:
        typer.echo("error: pass at most one of --approved-only / --pending-only", err=True)
        raise typer.Exit(code=2)
    if approved_only:
        approved_filter = True
    elif pending_only:
        approved_filter = False
    try:
        rows = registry.list_all(approved=approved_filter)
    finally:
        cm.close()
    if not rows:
        typer.echo("(no projects registered)")
        return
    for r in rows:
        badge = "OK" if r.approved else "PENDING"
        typer.echo(
            f"[{badge:7s}] {r.project_id:<32s} profile={r.profile.name:<24s} source={r.source}"
        )


@project_app.command("show")
def project_show(
    project_id: str = typer.Argument(..., help="Project slug to inspect."),
) -> None:
    """Show a registered project's profile summary."""
    registry, cm = _open_project_registry()
    try:
        record = registry.get(project_id)
    finally:
        cm.close()
    if record is None:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"project_id:  {record.project_id}")
    typer.echo(f"approved:    {record.approved}")
    typer.echo(f"source:      {record.source}")
    typer.echo(f"notes:       {record.notes or '(none)'}")
    typer.echo(f"profile:     {record.profile.name} (v{record.profile.version})")
    typer.echo(f"layers:      {[la.name for la in record.profile.layers]}")
    typer.echo(f"max_entries: {record.profile.limits.max_entries}")


@project_app.command("approve")
def project_approve(
    project_id: str = typer.Argument(..., help="Project to approve."),
) -> None:
    """Flip ``approved=true`` on an existing row."""
    registry, cm = _open_project_registry()
    try:
        updated = registry.approve(project_id)
    finally:
        cm.close()
    if not updated:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Approved project '{project_id}'")


@project_app.command("delete")
def project_delete(
    project_id: str = typer.Argument(..., help="Project to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove a project's profile row.

    Does **not** delete the project's memory rows from ``private_memories``.
    """
    if not yes:
        typer.confirm(
            f"Delete profile for project '{project_id}'? (memory rows are NOT deleted)",
            abort=True,
        )
    registry, cm = _open_project_registry()
    try:
        deleted = registry.delete(project_id)
    finally:
        cm.close()
    if not deleted:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Deleted project '{project_id}'")


@project_app.command("rotate-token")
def project_rotate_token(
    project_id: str = typer.Argument(..., help="Project slug to issue a token for."),
) -> None:
    """Issue (or replace) the per-tenant bearer token for a project.

    Prints the **plaintext token once** — store it securely; it is never
    retrievable again.  Requires ``argon2-cffi`` (installed with the
    ``tapps-brain[http]`` extra).

    Enable per-tenant auth with ``TAPPS_BRAIN_PER_TENANT_AUTH=1``.
    """
    registry, cm = _open_project_registry()
    try:
        try:
            plaintext = registry.rotate_token(project_id)
        except LookupError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        except ImportError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
    finally:
        cm.close()
    typer.echo(f"Token for project '{project_id}':")
    typer.echo(plaintext)
    typer.echo("(store this token — it will not be shown again)", err=True)


@project_app.command("revoke-token")
def project_revoke_token(
    project_id: str = typer.Argument(..., help="Project slug to revoke the token for."),
) -> None:
    """Revoke (clear) the per-tenant bearer token for a project.

    After revocation the project falls back to the global
    ``TAPPS_BRAIN_AUTH_TOKEN`` check (or no auth if that is unset).
    """
    registry, cm = _open_project_registry()
    try:
        revoked = registry.revoke_token(project_id)
    finally:
        cm.close()
    if not revoked:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Token revoked for project '{project_id}'")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
