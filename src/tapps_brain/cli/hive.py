"""``hive`` and ``agent`` sub-apps — Hive status, search, watch, push, and
agent registry management (EPIC-011, EPIC-054).
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from pathlib import Path

import typer

from tapps_brain.agent_scope import normalize_agent_scope
from tapps_brain.cli._common import (
    JsonFlag,
    ProjectDir,
    _get_store,
    _open_hive_backend_for_cli,
    _output,
    agent_app,
    hive_app,
)


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
