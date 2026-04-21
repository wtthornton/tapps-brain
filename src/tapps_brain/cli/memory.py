"""``memory`` sub-app commands: save, show, history, relations, related, search,
audit, tags, tag.

Parity with MCP ``memory_save`` is preserved by ``memory save``.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from tapps_brain.agent_scope import normalize_agent_scope
from tapps_brain.cli._common import (
    _PREVIEW_LEN,
    JsonFlag,
    ProjectDir,
    _entry_to_row,
    _get_store,
    _output,
    _print_table,
    memory_app,
)


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
