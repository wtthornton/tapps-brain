"""``feedback`` sub-app commands: rate, gap, issue, record, list (EPIC-029)."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from tapps_brain.cli._common import (
    JsonFlag,
    ProjectDir,
    _get_store,
    _output,
    _print_table,
    feedback_app,
)


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
