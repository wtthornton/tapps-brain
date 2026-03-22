"""CLI tool for tapps-brain memory management and operations.

Provides commands for inspecting, searching, importing/exporting,
federating, and maintaining memory stores from the command line.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

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

# Route structlog output to stderr so it doesn't pollute CLI stdout/JSON
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="tapps-brain",
    help="Persistent cross-session memory system for AI coding assistants.",
    no_args_is_help=True,
)
store_app = typer.Typer(help="Inspect store contents and statistics.", no_args_is_help=True)
memory_app = typer.Typer(help="Query and inspect individual memories.", no_args_is_help=True)
federation_app = typer.Typer(help="Manage cross-project federation.", no_args_is_help=True)
maintenance_app = typer.Typer(help="Run store maintenance operations.", no_args_is_help=True)

profile_app = typer.Typer(help="Manage memory profiles.", no_args_is_help=True)
hive_app = typer.Typer(help="Manage the Hive shared brain.", no_args_is_help=True)
agent_app = typer.Typer(help="Manage Hive agent registrations.", no_args_is_help=True)

app.add_typer(store_app, name="store")
app.add_typer(memory_app, name="memory")
app.add_typer(federation_app, name="federation")
app.add_typer(maintenance_app, name="maintenance")
app.add_typer(profile_app, name="profile")
app.add_typer(hive_app, name="hive")
app.add_typer(agent_app, name="agent")

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
# Helpers
# ---------------------------------------------------------------------------


def _resolve_project_dir(project_dir: Path | None) -> Path:
    """Resolve project directory, defaulting to cwd."""
    return (project_dir or Path.cwd()).resolve()


def _get_store(project_dir: Path | None) -> Any:  # noqa: ANN401
    """Open a MemoryStore from the resolved project dir."""
    from tapps_brain.store import MemoryStore

    root = _resolve_project_dir(project_dir)
    return MemoryStore(root)


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
            "max_entries": 500,
            "schema_version": schema_ver,
            "tier_distribution": snap.tier_counts,
            "exported_at": snap.exported_at,
        }
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(f"Store: {snap.project_root}")
            typer.echo(f"Entries: {snap.total_count} / 500")
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
        )
        if as_json:
            _output([_entry_to_row(e) for e in entries], as_json=True)
        else:
            rows = [_entry_to_row(e) for e in entries]
            base_cols = ["key", "tier", "confidence", "effective", "scope", "created"]
            _print_table(rows, columns=base_cols)
            typer.echo(f"\n{len(entries)} entries")
    finally:
        store.close()


@store_app.command("search")
def store_search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    project_dir: ProjectDir = None,
    tier: Annotated[str | None, typer.Option(help="Filter by tier.")] = None,
    scope: Annotated[str | None, typer.Option(help="Filter by scope.")] = None,
    as_of: Annotated[str | None, typer.Option(help="Point-in-time query (ISO-8601).")] = None,
    as_json: JsonFlag = False,
) -> None:
    """Search memory entries by query text."""
    store = _get_store(project_dir)
    try:
        results = store.search(query, tier=tier, scope=scope, as_of=as_of)
        if as_json:
            _output([_entry_to_row(e) for e in results], as_json=True)
        else:
            rows = [_entry_to_row(e) for e in results]
            _print_table(rows, columns=["key", "tier", "confidence", "effective", "scope"])
            typer.echo(f"\n{len(results)} results")
    finally:
        store.close()


# ===================================================================
# MEMORY COMMANDS
# ===================================================================


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
                typer.echo(f"      Value: {entry.value[:80]}...")
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
    limit: Annotated[int, typer.Option(help="Maximum results.")] = 20,
    as_json: JsonFlag = False,
) -> None:
    """Search memories by query text with ranked results."""
    store = _get_store(project_dir)
    try:
        from tapps_brain.retrieval import MemoryRetriever

        retriever = MemoryRetriever()
        scored = retriever.search(query, store, limit=limit, as_of=as_of)
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
        str | None, typer.Option("--type", help="Filter by event type (save, delete, etc.).")
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
            typer.echo(f"Updated tags for '{key}': {', '.join(result.tags) if result.tags else '(none)'}")
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
        data = json.loads(raw)
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
# FEDERATION COMMANDS
# ===================================================================


@federation_app.command("status")
def federation_status(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Show federation hub status."""
    from tapps_brain.federation import load_federation_config

    config = load_federation_config()
    data = {
        "hub_path": config.hub_path,
        "projects": len(config.projects),
        "subscriptions": len(config.subscriptions),
        "project_list": [p.project_id for p in config.projects],
    }
    if as_json:
        _output(data, as_json=True)
    else:
        typer.echo(f"Hub: {config.hub_path}")
        typer.echo(f"Projects: {len(config.projects)}")
        for p in config.projects:
            typer.echo(f"  - {p.project_id} ({p.project_root})")
        typer.echo(f"Subscriptions: {len(config.subscriptions)}")
        for s in config.subscriptions:
            typer.echo(f"  - {s.subscriber} <- {', '.join(s.sources)}")


@federation_app.command("list")
def federation_list(
    as_json: JsonFlag = False,
) -> None:
    """List all federated projects."""
    from tapps_brain.federation import load_federation_config

    config = load_federation_config()
    if as_json:
        _output(
            [
                {
                    "project_id": p.project_id,
                    "project_root": p.project_root,
                    "registered_at": p.registered_at,
                    "tags": p.tags,
                }
                for p in config.projects
            ],
            as_json=True,
        )
    else:
        if not config.projects:
            typer.echo("No federated projects.")
            return
        rows = [
            {
                "project_id": p.project_id,
                "root": p.project_root,
                "registered": p.registered_at[:10],
                "tags": ", ".join(p.tags),
            }
            for p in config.projects
        ]
        _print_table(rows)


@federation_app.command("subscribe")
def federation_subscribe(
    project_name: Annotated[str, typer.Argument(help="Project to subscribe to.")],
    project_dir: ProjectDir = None,
    min_confidence: Annotated[
        float, typer.Option(help="Minimum confidence for synced entries.")
    ] = 0.5,
    as_json: JsonFlag = False,
) -> None:
    """Subscribe to another project's memories."""
    from tapps_brain.federation import add_subscription

    root = _resolve_project_dir(project_dir)
    subscriber = root.name
    config = add_subscription(
        subscriber=subscriber,
        sources=[project_name],
        min_confidence=min_confidence,
    )
    sub_count = len(config.subscriptions)
    result = {"subscriber": subscriber, "source": project_name, "subscriptions": sub_count}
    if as_json:
        _output(result, as_json=True)
    else:
        typer.echo(f"Subscribed '{subscriber}' to '{project_name}'")


@federation_app.command("unsubscribe")
def federation_unsubscribe(
    project_name: Annotated[str, typer.Argument(help="Project to unsubscribe from.")],
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Unsubscribe from a project's memories."""
    from tapps_brain.federation import load_federation_config, save_federation_config

    root = _resolve_project_dir(project_dir)
    subscriber = root.name
    config = load_federation_config()
    original_count = len(config.subscriptions)
    config.subscriptions = [
        s
        for s in config.subscriptions
        if not (s.subscriber == subscriber and project_name in s.sources)
    ]
    save_federation_config(config)
    removed = original_count - len(config.subscriptions)
    result = {"subscriber": subscriber, "source": project_name, "removed": removed}
    if as_json:
        _output(result, as_json=True)
    elif removed > 0:
        typer.echo(f"Unsubscribed '{subscriber}' from '{project_name}'")
    else:
        typer.echo(f"No subscription found for '{subscriber}' from '{project_name}'")


@federation_app.command("publish")
def federation_publish(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Publish current project's memories to the federation hub."""
    from tapps_brain.federation import (
        FederatedStore,
        register_project,
        sync_to_hub,
    )

    root = _resolve_project_dir(project_dir)
    store = _get_store(project_dir)
    try:
        project_id = root.name
        register_project(project_id, str(root))
        hub = FederatedStore()
        try:
            result = sync_to_hub(store, hub, project_id, project_root=str(root))
            if as_json:
                _output(result, as_json=True)
            else:
                typer.echo(
                    f"Published {result.get('published', 0)} entries "
                    f"(skipped {result.get('skipped', 0)})"
                )
        finally:
            hub.close()
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


@maintenance_app.command("gc")
def maintenance_gc(
    project_dir: ProjectDir = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview without archiving.")] = False,
    as_json: JsonFlag = False,
) -> None:
    """Run garbage collection to archive stale entries."""
    from tapps_brain.gc import MemoryGarbageCollector

    store = _get_store(project_dir)
    try:
        entries = store.list_all(include_superseded=True)
        gc = MemoryGarbageCollector()
        candidates = gc.identify_candidates(entries)

        if dry_run:
            data = {
                "would_archive": len(candidates),
                "keys": [e.key for e in candidates],
            }
            if as_json:
                _output(data, as_json=True)
            else:
                typer.echo(f"Would archive {len(candidates)} entries:")
                for e in candidates:
                    typer.echo(f"  - {e.key} (confidence={e.confidence:.2f})")
            return

        archived = 0
        root = _resolve_project_dir(project_dir)
        if candidates:
            archive_path = root / ".tapps-brain" / "memory" / "archive.jsonl"
            gc.append_to_archive(candidates, archive_path)
            for entry in candidates:
                store.delete(entry.key)
                archived += 1

        data = {"archived": archived, "remaining": store.count()}
        if as_json:
            _output(data, as_json=True)
        else:
            typer.echo(f"Archived {archived} entries, {store.count()} remaining")
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
    as_json: JsonFlag = False,
) -> None:
    """Show or update garbage collection configuration."""
    from tapps_brain.gc import GCConfig

    store = _get_store(project_dir)
    try:
        current: GCConfig = store.get_gc_config()

        # If any --set flags given, update and save back to store
        if set_floor is not None or set_session is not None or set_threshold is not None:
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
            )
            store.set_gc_config(new_cfg)
            data: dict[str, object] = {"status": "updated", **new_cfg.to_dict()}
        else:
            data = current.to_dict()

        if as_json:
            _output(data, as_json=True)
        else:
            cfg = store.get_gc_config()
            typer.echo(f"floor_retention_days:   {cfg.floor_retention_days}")
            typer.echo(f"session_expiry_days:    {cfg.session_expiry_days}")
            typer.echo(f"contradicted_threshold: {cfg.contradicted_threshold}")
            if set_floor is not None or set_session is not None or set_threshold is not None:
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
        version = store._persistence.get_schema_version()
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
    """Show store health report: entry counts, schema, federation, GC status."""
    store = _get_store(project_dir)
    try:
        report = store.health()
        if as_json:
            _output(report.model_dump(mode="json"), as_json=True)
        else:
            typer.echo(f"Store: {report.store_path}")
            typer.echo(f"Entries: {report.entry_count} / {report.max_entries}")
            typer.echo(f"Schema: v{report.schema_version}")
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
    as_json: JsonFlag = False,
) -> None:
    """Test auto-recall from the terminal."""
    store = _get_store(project_dir)
    try:
        result = store.recall(message, max_tokens=max_tokens)
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
    from tapps_brain.hive import AgentRegistry, HiveStore

    hive = HiveStore()
    namespaces = hive.list_namespaces()
    ns_counts: dict[str, int] = {}
    for ns in namespaces:
        rows = hive._conn.execute(
            "SELECT COUNT(*) FROM hive_memories WHERE namespace = ?",
            (ns,),
        ).fetchone()
        ns_counts[ns] = rows[0] if rows else 0
    total = sum(ns_counts.values())

    registry = AgentRegistry()
    agents = [
        {"id": a.id, "profile": a.profile, "skills": a.skills} for a in registry.list_agents()
    ]
    hive.close()

    data: dict[str, Any] = {
        "namespaces": ns_counts,
        "total_entries": total,
        "agents": agents,
    }
    if as_json:
        _output(data, as_json=True)
    else:
        typer.echo(f"Hive: {total} entries across {len(namespaces)} namespaces")
        for ns, count in ns_counts.items():
            typer.echo(f"  {ns}: {count}")
        if agents:
            typer.echo(f"\nAgents ({len(agents)}):")
            for a in agents:
                typer.echo(f"  {a['id']} (profile={a['profile']})")
        else:
            typer.echo("\nNo registered agents.")


@hive_app.command("search")
def hive_search(
    query: str,
    namespace: Annotated[str | None, typer.Option(help="Namespace filter.")] = None,
    as_json: JsonFlag = False,
) -> None:
    """Search the Hive shared brain."""
    from tapps_brain.hive import HiveStore

    hive = HiveStore()
    ns_list = [namespace] if namespace else None
    results = hive.search(query, namespaces=ns_list, limit=20)
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


@agent_app.command("create")
def agent_create(
    agent_id: str,
    profile: Annotated[str, typer.Option(help="Memory profile name.")] = "repo-brain",
    skills: Annotated[str, typer.Option(help="Comma-separated skills.")] = "",
    as_json: JsonFlag = False,
) -> None:
    """Create an agent with profile validation and print namespace and profile summary."""
    from tapps_brain.hive import AgentRegistration, AgentRegistry
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
    from tapps_brain.hive import AgentRegistration, AgentRegistry

    registry = AgentRegistry()
    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    agent = AgentRegistration(id=agent_id, profile=profile, skills=skill_list)
    registry.register(agent)
    typer.echo(f"Registered agent '{agent_id}' with profile '{profile}'.")


@agent_app.command("list")
def agent_list(as_json: JsonFlag = False) -> None:
    """List all registered agents in the Hive."""
    from tapps_brain.hive import AgentRegistry

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
