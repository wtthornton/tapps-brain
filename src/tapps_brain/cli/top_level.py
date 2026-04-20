"""Top-level CLI commands + the ``profile`` sub-app.

Includes: init, stats, export, import, recall, remember, brain-recall,
forget, status, who-am-i, and ``profile {show,list,set,onboard,layers}``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from tapps_brain.cli._common import (
    _STATS_NEAR_EXPIRY_THRESHOLD,
    JsonFlag,
    ProjectDir,
    _get_store,
    _output,
    _print_table,
    _resolve_project_dir,
    app,
    profile_app,
)

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
        repo_example = Path(__file__).resolve().parents[3] / "examples" / "coding-project-init"
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
            except (ValueError, AttributeError, TypeError):
                pass  # malformed or missing created_at; skip this entry in recency count

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
