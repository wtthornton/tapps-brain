"""``maintenance`` sub-app commands.

Includes: consolidate, consolidation-threshold-sweep, save-conflict-candidates,
consolidation-merge-undo, consolidation-diff, stale, gc, gc-config,
consolidation-config, migrate, health, verify-integrity, migrate-hive,
hive-schema-status, backup-hive, restore-hive.
"""

from __future__ import annotations

from typing import Annotated

import typer

from tapps_brain.cli._common import (
    JsonFlag,
    ProjectDir,
    _get_store,
    _output,
    _resolve_project_dir,
    maintenance_app,
)


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


def _strip_dsn_password(dsn: str) -> tuple[str, str | None]:
    """Parse a Postgres DSN and extract the password for safe subprocess use.

    Returns ``(safe_dsn, password_or_None)`` where *safe_dsn* has the password
    component removed so it is safe to pass on the command line (visible via
    ``ps``).  The caller should set ``PGPASSWORD`` in the subprocess environment
    when *password_or_None* is not ``None``.

    Supports both URL format (``postgres://user:pass@host/db``) and
    keyword=value format (``host=h user=u password=p dbname=db``).
    """
    import re
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(dsn)
    if parsed.scheme in ("postgres", "postgresql") and parsed.password:
        password = parsed.password
        # Reconstruct netloc without the password component.
        host_part = parsed.hostname or ""
        if parsed.port:
            host_part = f"{host_part}:{parsed.port}"
        netloc = f"{parsed.username}@{host_part}" if parsed.username else host_part
        safe_dsn = urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
        return safe_dsn, password

    # Keyword=value format: handle both password='...' and password=word
    match = re.search(r"\bpassword\s*=\s*'([^']*)'|\bpassword\s*=\s*(\S+)", dsn)
    if match:
        password = match.group(1) if match.group(1) is not None else match.group(2)
        safe_dsn = re.sub(r"\s*\bpassword\s*=\s*'[^']*'|\s*\bpassword\s*=\s*\S+", "", dsn).strip()
        return safe_dsn, password

    return dsn, None


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

    safe_dsn, pgpassword = _strip_dsn_password(effective_dsn)
    proc_env = os.environ.copy()
    if pgpassword:
        proc_env["PGPASSWORD"] = pgpassword

    fmt_flag = "--format=custom" if format == "custom" else "--format=plain"
    cmd = ["pg_dump", safe_dsn, fmt_flag, f"--file={output}"]  # nosec B603 — password passed via PGPASSWORD env var, not argv

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, env=proc_env)  # nosec B603
        typer.echo(f"Backup written to {output}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        if pgpassword:
            stderr = stderr.replace(pgpassword, "***")
        typer.echo(f"Error: {stderr}", err=True)
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

    safe_dsn, pgpassword = _strip_dsn_password(effective_dsn)
    proc_env = os.environ.copy()
    if pgpassword:
        proc_env["PGPASSWORD"] = pgpassword

    # Detect format
    if input.endswith(".sql"):
        cmd = ["psql", safe_dsn, "-f", input]  # nosec B603 — password passed via PGPASSWORD env var, not argv
    else:
        cmd = ["pg_restore", "--dbname", safe_dsn, "--clean", "--if-exists", input]  # nosec B603

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, env=proc_env)  # nosec B603
        typer.echo(f"Restored from {input}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        if pgpassword:
            stderr = stderr.replace(pgpassword, "***")
        typer.echo(f"Error: {stderr}", err=True)
        raise typer.Exit(1) from e
