"""``session`` and ``relay`` sub-app commands (Issue #17, GitHub #19)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from tapps_brain.cli._common import (
    JsonFlag,
    ProjectDir,
    _get_store,
    _output,
    _resolve_project_dir,
    relay_app,
    session_app,
)


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
