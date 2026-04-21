"""``store`` sub-app commands: stats, list, groups, search, metrics."""

from __future__ import annotations

from typing import Annotated

import typer

from tapps_brain.cli._common import (
    JsonFlag,
    ProjectDir,
    _entry_to_row,
    _get_store,
    _output,
    _print_table,
    store_app,
)


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
