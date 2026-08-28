"""``maintenance`` sub-app commands.

Includes: consolidate, consolidation-threshold-sweep, save-conflict-candidates,
consolidation-merge-undo, consolidation-diff, stale, gc, gc-config, decay-refresh,
demote-contradicted, consolidation-config, migrate, health, verify-integrity,
migrate-hive, hive-schema-status, backup-hive, restore-hive.
"""

from __future__ import annotations

import os
import sys
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


@maintenance_app.command("save-conflict-undo")
def maintenance_save_conflict_undo(
    keys: Annotated[
        list[str],
        typer.Argument(help="Key(s) of the conflict-invalidated row(s) to restore."),
    ],
    project_dir: ProjectDir = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview which keys would be restored, without changes."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt (also: TAPPS_BRAIN_CONFIRM_YES=1 env var).",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Restore entries hidden by save-time conflict detection (TAP-5782).

    Save-time conflict detection marks an entry ``contradicted=true``, which drops
    it from recall. A false positive was previously unrecoverable: this command is
    the counterpart to ``consolidation-merge-undo`` for that mechanism.

    Clears ``contradicted`` and ``contradiction_reason`` only — value, tier, tags,
    confidence and ``updated_at`` are untouched — and appends ``save_conflict_undo``
    to the audit log. Refuses any contradiction not written by save-time conflict
    detection, naming the reason it found instead.

    Scope note: private memory is keyed by ``(project_id, agent_id, key)``. Set
    ``TAPPS_BRAIN_PROJECT`` and ``TAPPS_BRAIN_AGENT_ID`` to target the right
    partition, or the command will report ``not_found`` for rows that plainly exist.

    Pass ``--dry-run`` to preview. Pass ``--yes`` (or ``TAPPS_BRAIN_CONFIRM_YES=1``)
    for non-interactive scripts.
    """
    auto_yes = yes or os.environ.get("TAPPS_BRAIN_CONFIRM_YES") == "1"

    store = _get_store(project_dir)
    try:
        if dry_run:
            previews = [store.undo_save_conflict(k, dry_run=True) for k in keys]
            eligible = [r for r in previews if r["ok"]]
            if as_json:
                _output({"dry_run": True, "results": previews}, as_json=True)
            else:
                typer.echo("Dry run — no changes made.")
                typer.echo(f"  would restore {len(eligible)} of {len(keys)} key(s):")
                for r in previews:
                    mark = "<-" if r["ok"] else "skip"
                    typer.echo(f"    {mark} {r['key']}  ({r['reason']})")
            return

        if not auto_yes:
            if sys.stdin.isatty():
                typer.confirm(
                    f"Clear the save-time conflict flag on {len(keys)} entr(y/ies)?",
                    abort=True,
                    default=False,
                )
            else:
                typer.echo(
                    "Non-interactive mode: pass --yes or set TAPPS_BRAIN_CONFIRM_YES=1 "
                    "to confirm this operation.",
                    err=True,
                )
                raise typer.Exit(code=1)

        results = [store.undo_save_conflict(k) for k in keys]
        restored = [r for r in results if r["restored"]]
        if as_json:
            _output({"restored": len(restored), "results": results}, as_json=True)
        else:
            typer.echo(f"Restored {len(restored)} of {len(keys)} key(s).")
            for r in results:
                mark = "<-" if r["restored"] else "skip"
                typer.echo(f"  {mark} {r['key']}  ({r['reason']})")
        # Partial success is still a non-zero exit so a script notices.
        if len(restored) != len(keys):
            raise typer.Exit(code=1)
    finally:
        store.close()


@maintenance_app.command("consolidation-merge-undo")
def maintenance_consolidation_merge_undo(
    consolidated_key: Annotated[str, typer.Argument(help="Key of the consolidated row to undo.")],
    project_dir: ProjectDir = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview what would be removed/restored without changes."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt (also: TAPPS_BRAIN_CONFIRM_YES=1 env var).",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Revert the last auto-consolidation merge for this key (EPIC-044 STORY-044.4).

    Restores superseded source rows, deletes the consolidated row, and appends
    ``consolidation_merge_undo`` to ``memory_log.jsonl``. Requires matching audit
    and unchanged supersede metadata on sources.

    Pass ``--dry-run`` to preview what would be removed/restored without making changes.
    Pass ``--yes`` (or set ``TAPPS_BRAIN_CONFIRM_YES=1``) to skip the TTY confirmation
    prompt in non-interactive scripts.
    """
    from tapps_brain.auto_consolidation import find_last_consolidation_merge_audit

    auto_yes = yes or os.environ.get("TAPPS_BRAIN_CONFIRM_YES") == "1"

    store = _get_store(project_dir)
    try:
        if dry_run:
            merge_rec = find_last_consolidation_merge_audit(
                store._persistence.audit_path,
                consolidated_key,
                persistence=store._persistence,
            )
            if merge_rec is None:
                typer.echo(
                    f"No consolidation merge audit found for key: {consolidated_key} "
                    f"(nothing to undo)."
                )
                return
            source_keys = list(merge_rec.get("source_keys") or [])
            if as_json:
                _output(
                    {
                        "dry_run": True,
                        "consolidated_key": consolidated_key,
                        "would_remove": [consolidated_key],
                        "would_restore": source_keys,
                    },
                    as_json=True,
                )
            else:
                typer.echo("Dry run — no changes made.")
                typer.echo(f"  would remove 1 consolidated row: {consolidated_key}")
                typer.echo(f"  would restore {len(source_keys)} source row(s):")
                for sk in source_keys:
                    typer.echo(f"    <- {sk}")
            return

        if not auto_yes:
            if sys.stdin.isatty():
                merge_rec = find_last_consolidation_merge_audit(
                    store._persistence.audit_path,
                    consolidated_key,
                    persistence=store._persistence,
                )
                source_keys = (merge_rec.get("source_keys") or []) if merge_rec else []
                n_src = len(source_keys)
                typer.confirm(
                    f"Restore {n_src} source row(s) and remove 1 consolidated row?",
                    abort=True,
                    default=False,
                )
            else:
                typer.echo(
                    "Non-interactive mode: pass --yes or set TAPPS_BRAIN_CONFIRM_YES=1 "
                    "to confirm this destructive operation.",
                    err=True,
                )
                raise typer.Exit(code=1)

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


@maintenance_app.command("decay-refresh")
def maintenance_decay_refresh(
    project_dir: ProjectDir = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing (default).")
    ] = True,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Write the closures/archivals. Never point this at production without a backup.",
        ),
    ] = False,
    report: Annotated[
        str | None,
        typer.Option("--report", help="Write the JSON result to this path."),
    ] = None,
    sample_size: Annotated[
        int, typer.Option("--sample-size", min=0, help="Keys to list per bucket.")
    ] = 10,
    as_json: JsonFlag = False,
) -> None:
    """Scheduled decay refresh: close decayed rows, archive floor-crossers (ADR-014).

    Lazy read-path decay is unchanged; this persists what it computes. Rows below
    ``stale_threshold`` get ``invalid_at`` + ``status='stale'`` via
    ``close_validity``; rows at the tier confidence floor are archived to
    ``gc_archive`` with ``archive_reason='age'`` and removed from the live table.

    ``--dry-run`` is the default: ``--apply`` must be asked for explicitly, and
    the report it writes names the ``gc_archive`` rows that back an undo.
    """
    import json as _json

    effective_dry_run = not apply if apply else dry_run
    store = _get_store(project_dir)
    try:
        data = store.refresh_decay(dry_run=effective_dry_run, sample_size=sample_size)
        if effective_dry_run:
            # Name the undo path in the same breath as the numbers, so an
            # operator reading a dry run already knows what --apply is
            # recoverable from (SC-6: every destructive pass is undoable).
            data["undo_path"] = (
                "gc_archive rows with payload->>'archive_reason' = 'age' "
                "(maintenance stale/list-archive); closed rows keep their payload "
                "in private_memories and revive on a later save"
            )
        if report:
            with open(report, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2, sort_keys=True)
        if as_json:
            _output(data, as_json=True)
        elif effective_dry_run:
            typer.echo(
                f"would_close={data['would_close']} would_archive={data['would_archive']} "
                f"rows_before={data['rows_before']}"
            )
            for key in data["close_sample"]:
                typer.echo(f"  close   - {key}")
            for key in data["archive_sample"]:
                typer.echo(f"  archive - {key}")
        else:
            typer.echo(
                f"closed={data['closed']} archived={data['archived']} "
                f"rows_before={data['rows_before']} rows_after={data['rows_after']} "
                f"archived_delta={data['archived_delta']}"
            )
        if report:
            typer.echo(f"report written to {report}")
    finally:
        store.close()


@maintenance_app.command("demote-contradicted")
def maintenance_demote_contradicted(
    project_dir: ProjectDir = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview without writing (default).")
    ] = True,
    apply: Annotated[bool, typer.Option("--apply", help="Write the demotions.")] = False,
    report: Annotated[
        str | None,
        typer.Option("--report", help="Write the JSON result to this path."),
    ] = None,
    as_json: JsonFlag = False,
) -> None:
    """Demote learnings whose promotion no longer holds (TAP-5547 rules).

    A thin wrapper over ``MemoryStore.decay_learnings`` — the three demotion
    rules (contradicted at any ``learning_status``, candidate below the
    confidence floor, candidate unvalidated past ``candidate_stale_days``)
    already live in ``decay.identify_learning_demotions`` and are exercised by the MCP
    ``maintenance_decay_learnings`` tool. This adds the CLI surface only; it
    deliberately contains no demotion logic of its own.

    Demotion never touches the trust-axis CHECK constraints: ``promotion_signal``
    stays ``eval``/``human`` only, and nothing here can move a row *into*
    ``approved``.
    """
    import json as _json

    effective_dry_run = not apply if apply else dry_run
    store = _get_store(project_dir)
    try:
        data = store.decay_learnings(dry_run=effective_dry_run)
        if report:
            with open(report, "w", encoding="utf-8") as fh:
                _json.dump(data, fh, indent=2, sort_keys=True)
        if as_json:
            _output(data, as_json=True)
        elif not data["enabled"]:
            typer.echo("learning decay disabled by profile (learning_decay.enabled=false)")
        elif effective_dry_run:
            typer.echo(f"would_demote={len(data['demoted_keys'])} reasons={data['reason_counts']}")
            for key in data["demoted_keys"]:
                typer.echo(f"  - {key}")
        else:
            typer.echo(f"demoted={data['demoted_count']} reasons={data['reason_counts']}")
        if report:
            typer.echo(f"report written to {report}")
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
            help="Days before session index rows are pruned during gc().",
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
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            envvar="TAPPS_BRAIN_DATABASE_URL",
            help="PostgreSQL DSN (or set TAPPS_BRAIN_DATABASE_URL).",
        ),
    ] = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show pending migrations without applying.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Apply pending private-memory schema migrations."""
    import os

    from tapps_brain.postgres_migrations import (
        apply_private_migrations,
        get_private_schema_status,
    )

    _ = project_dir  # retained for CLI signature compatibility
    resolved = (dsn or os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")).strip()
    if not resolved:
        typer.echo("Error: --dsn or TAPPS_BRAIN_DATABASE_URL is required.", err=True)
        raise typer.Exit(code=1)

    status = get_private_schema_status(resolved)
    if dry_run:
        pending = [{"version": v, "filename": f} for v, f in status.pending_migrations]
        data = {
            "current_version": status.current_version,
            "pending_migrations": pending,
            "dry_run": True,
        }
        if as_json:
            _output(data, as_json=True)
        elif not pending:
            typer.echo(f"Current schema version: v{status.current_version} (up-to-date)")
        else:
            typer.echo(f"Current schema version: v{status.current_version}")
            typer.echo(f"Pending: {[p['version'] for p in pending]}")
        return

    applied = apply_private_migrations(resolved)
    after = get_private_schema_status(resolved).current_version
    data = {
        "schema_version": after,
        "applied_versions": applied,
        "status": "up-to-date" if not applied else "applied",
    }
    if as_json:
        _output(data, as_json=True)
    elif not applied:
        typer.echo(f"Schema version: v{after} (up-to-date)")
    else:
        typer.echo(f"Applied migrations: {applied} (now v{after})")


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


@maintenance_app.command("resign-integrity")
def maintenance_resign_integrity(
    project_dir: ProjectDir = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt (also: TAPPS_BRAIN_CONFIRM_YES=1 env var).",
        ),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Re-sign all entries' integrity hashes under the CURRENT signing key (TAP-4331).

    Remediation for a signing-key mismatch — e.g. a database restored under a host
    whose ``~/.tapps-brain/integrity.key`` differs from the key that wrote the rows,
    so ``verify-integrity`` reports every row as tampered.

    WARNING: this assumes the stored content is authentic and overwrites every
    ``integrity_hash``, destroying the prior tamper-audit trail. Only run when the
    data is trusted. Pass ``--yes`` (or set TAPPS_BRAIN_CONFIRM_YES=1) in scripts.
    """
    auto_yes = yes or os.environ.get("TAPPS_BRAIN_CONFIRM_YES") == "1"

    store = _get_store(project_dir)
    try:
        if not auto_yes:
            if sys.stdin.isatty():
                report = store.verify_integrity()
                typer.confirm(
                    f"Re-sign {report['tampered']} of {report['total']} entries under the "
                    f"current key? This overwrites integrity hashes and cannot be undone.",
                    abort=True,
                    default=False,
                )
            else:
                typer.echo(
                    "Non-interactive mode: pass --yes or set TAPPS_BRAIN_CONFIRM_YES=1 "
                    "to confirm this destructive operation.",
                    err=True,
                )
                raise typer.Exit(code=1)

        result = store.resign_integrity()
        if as_json:
            _output(result, as_json=True)
        else:
            typer.echo(
                f"Re-signed {result['resigned']} entries "
                f"({result['skipped_no_change']} already current)."
            )
    finally:
        store.close()


@maintenance_app.command("backfill-embeddings")
def maintenance_backfill_embeddings(
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Compute + persist embeddings for entries that have none (EPIC-078).

    Remediation for ``vector_index_rows == 0`` on the dashboard ("pgvector HNSW
    ready but no embedded rows") — rows written before an embedding provider was
    active.  Non-destructive: only ``embedding`` / ``embedding_model_id`` are
    written; timestamps and integrity hashes are preserved.  Requires an
    embedding provider (install the ``[all]`` extra); without one nothing is
    backfilled.
    """
    store = _get_store(project_dir)
    try:
        result = store.backfill_embeddings()
        if as_json:
            _output(result, as_json=True)
        else:
            typer.echo(
                f"Backfilled {result['backfilled']} entries "
                f"({result['skipped_existing']} already embedded, "
                f"{result['failed']} failed)."
            )
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
        help=(
            "PostgreSQL connection string. Falls back to TAPPS_BRAIN_HIVE_DSN, "
            "TAPPS_BRAIN_HIVE_POSTGRES_DSN, then TAPPS_BRAIN_DATABASE_URL."
        ),
    ),
]


def _resolve_hive_cli_dsn(dsn: str) -> str:
    """Resolve Hive DSN from --dsn or documented env vars."""
    import os

    return (
        (dsn or "").strip()
        or os.environ.get("TAPPS_BRAIN_HIVE_DSN", "").strip()
        or os.environ.get("TAPPS_BRAIN_HIVE_POSTGRES_DSN", "").strip()
        or os.environ.get("TAPPS_BRAIN_DATABASE_URL", "").strip()
    )


@maintenance_app.command("migrate-hive")
def maintenance_migrate_hive(
    dsn: _PG_DSN_OPT = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show pending migrations without applying.")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Apply pending PostgreSQL Hive schema migrations."""
    resolved = _resolve_hive_cli_dsn(dsn)
    if not resolved:
        typer.echo(
            "Error: --dsn or TAPPS_BRAIN_HIVE_DSN / TAPPS_BRAIN_DATABASE_URL is required.",
            err=True,
        )
        raise typer.Exit(code=1)

    from tapps_brain.postgres_migrations import apply_hive_migrations

    applied = apply_hive_migrations(resolved, dry_run=dry_run)
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
    resolved = _resolve_hive_cli_dsn(dsn)
    if not resolved:
        typer.echo(
            "Error: --dsn or TAPPS_BRAIN_HIVE_DSN / TAPPS_BRAIN_DATABASE_URL is required.",
            err=True,
        )
        raise typer.Exit(code=1)

    from tapps_brain.postgres_migrations import get_hive_schema_status

    status = get_hive_schema_status(resolved)
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


@maintenance_app.command("hnsw-reindex")
def maintenance_hnsw_reindex(
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            envvar="TAPPS_BRAIN_DATABASE_URL",
            help="PostgreSQL DSN (or set TAPPS_BRAIN_DATABASE_URL).",
        ),
    ] = "",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would run without issuing SQL."),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Reindex HNSW embedding indexes and VACUUM (ANALYZE) their tables (TAP-2729).

    Runs ``REINDEX INDEX CONCURRENTLY`` on every known HNSW embedding index
    (idx_priv_embedding_hnsw, idx_hive_embedding_hnsw, idx_fed_embedding_hnsw)
    then ``VACUUM (ANALYZE)`` on each affected table.  Both operations run in
    autocommit mode, so live queries are unaffected.

    Recommended cadence: weekly, during a low-traffic window.

    Tip: set maintenance_work_mem to 1-2 GB on the database session before
    running to speed up the HNSW rebuild step:

    \\b
        SET maintenance_work_mem = '1GB';
    """
    if not dsn:
        typer.echo("Error: --dsn or TAPPS_BRAIN_DATABASE_URL is required.", err=True)
        raise typer.Exit(code=1)

    from tapps_brain.gc import run_hnsw_maintenance

    result = run_hnsw_maintenance(dsn, dry_run=dry_run)
    data = result.model_dump(mode="json")

    if as_json:
        _output(data, as_json=True)
    elif dry_run:
        typer.echo("Dry run — no SQL issued.")
        typer.echo(f"Would reindex: {', '.join(result.reindexed_indexes) or 'none'}")
        typer.echo(f"Would vacuum:  {', '.join(result.vacuumed_tables) or 'none'}")
        if result.skipped_indexes:
            typer.echo(f"Skipped (not found): {', '.join(result.skipped_indexes)}")
    elif result.error:
        typer.echo(f"Error: {result.error}", err=True)
        raise typer.Exit(code=1)
    else:
        typer.echo(f"Reindexed: {', '.join(result.reindexed_indexes) or 'none'}")
        if result.skipped_indexes:
            typer.echo(f"Skipped (not found): {', '.join(result.skipped_indexes)}")
        typer.echo(f"Vacuumed:  {', '.join(result.vacuumed_tables) or 'none'}")
        typer.echo(f"Duration:  {result.duration_seconds:.1f}s")


@maintenance_app.command("migrations-rollback")
def maintenance_migrations_rollback(
    target_version: Annotated[
        int,
        typer.Argument(help="Roll back all migrations above this version number."),
    ],
    schema: Annotated[
        str,
        typer.Option(
            "--schema",
            help="Schema plane to roll back: private, hive, or federation.",
        ),
    ] = "private",
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            envvar="TAPPS_BRAIN_DATABASE_URL",
            help="PostgreSQL DSN (or set TAPPS_BRAIN_DATABASE_URL).",
        ),
    ] = "",
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview down-migrations without applying them."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Skip the interactive confirmation prompt."),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Roll back schema migrations to TARGET_VERSION.

    Executes the paired *.down.sql files for all versions above TARGET_VERSION
    in reverse order (newest first).  Each down file undoes the schema changes
    of its corresponding forward migration.

    Requires --yes (or confirmation on a TTY) unless --dry-run is set.

    Examples:

      # Preview: show what would be rolled back
      tapps-brain maintenance migrations-rollback --schema private 14 --dry-run

      # Roll back private schema to version 14
      tapps-brain maintenance migrations-rollback --schema private 14 --yes

      # Roll back Hive schema to version 1 using a custom DSN
      tapps-brain maintenance migrations-rollback --schema hive 1 \\
          --dsn postgresql://user:pass@host/db --yes
    """
    from tapps_brain.postgres_migrations import (
        rollback_federation_migrations,
        rollback_hive_migrations,
        rollback_private_migrations,
    )

    _valid_schemas = {"private", "hive", "federation"}
    if schema not in _valid_schemas:
        choices = ", ".join(sorted(_valid_schemas))
        typer.echo(f"Error: unknown --schema '{schema}'. Choose one of: {choices}.", err=True)
        raise typer.Exit(code=1)

    # Resolve DSN: --dsn flag / env var for each plane.
    if not dsn:
        if schema == "hive":
            dsn = os.environ.get("TAPPS_BRAIN_HIVE_DSN", "") or os.environ.get(
                "TAPPS_BRAIN_DATABASE_URL", ""
            )
        elif schema == "federation":
            dsn = os.environ.get("TAPPS_BRAIN_FEDERATION_DSN", "") or os.environ.get(
                "TAPPS_BRAIN_DATABASE_URL", ""
            )
    if not dsn:
        typer.echo(
            f"Error: --dsn or TAPPS_BRAIN_DATABASE_URL is required for --schema={schema}.",
            err=True,
        )
        raise typer.Exit(code=1)

    rollback_fn = {
        "private": rollback_private_migrations,
        "hive": rollback_hive_migrations,
        "federation": rollback_federation_migrations,
    }[schema]

    if not dry_run and not yes:
        typer.echo(f"This will roll back the '{schema}' schema to version {target_version}.")
        typer.echo("Run with --dry-run to preview, or --yes to confirm.")
        confirmed = typer.confirm("Proceed with rollback?", default=False)
        if not confirmed:
            typer.echo("Aborted.")
            raise typer.Exit(code=0)

    rolled_back = rollback_fn(dsn, target_version=target_version, dry_run=dry_run)

    data = {
        "schema": schema,
        "target_version": target_version,
        "rolled_back": rolled_back,
        "dry_run": dry_run,
        "status": (
            "dry-run" if dry_run else ("rolled-back" if rolled_back else "nothing-to-rollback")
        ),
    }
    if as_json:
        _output(data, as_json=True)
    elif not rolled_back:
        typer.echo(
            f"Nothing to roll back: no '{schema}' down-migrations above version {target_version}."
        )
    else:
        action = "Would roll back" if dry_run else "Rolled back"
        typer.echo(f"{action} '{schema}' migrations: {rolled_back}")


@maintenance_app.command("purge-test-tenants")
def maintenance_purge_test_tenants(
    prefix: Annotated[
        list[str] | None,
        typer.Option(
            "--prefix",
            help="project_id prefix to purge (repeatable). Defaults to the reserved "
            "test/load prefixes (smoke-, test-).",
        ),
    ] = None,
    dsn: Annotated[
        str,
        typer.Option(
            "--dsn",
            envvar="TAPPS_BRAIN_DATABASE_URL",
            help="PostgreSQL DSN (or set TAPPS_BRAIN_DATABASE_URL).",
        ),
    ] = "",
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually delete. Without this flag, only counts (dry-run)."),
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Delete leaked test/load tenant rows by project_id prefix (TAP-4465).

    Dry-run by default: reports how many rows match each reserved prefix without
    deleting. Pass ``--apply`` to remove them. Use this to mop up rows left by
    test or load harnesses that ran against a persistent/shared Postgres.
    """
    if not dsn:
        typer.echo("Error: --dsn or TAPPS_BRAIN_DATABASE_URL is required.", err=True)
        raise typer.Exit(code=1)

    from tapps_brain.maintenance_purge import (
        RESERVED_TEST_PROJECT_PREFIXES,
        discover_tenant_tables,
        purge_by_prefix,
    )
    from tapps_brain.postgres_connection import PostgresConnectionManager

    prefixes = tuple(prefix) if prefix else RESERVED_TEST_PROJECT_PREFIXES
    cm = PostgresConnectionManager(dsn)
    try:
        if apply:
            deleted = purge_by_prefix(cm, prefixes)
            status = "purged"
        else:
            deleted = _count_prefix_rows(cm, prefixes, discover_tenant_tables)
            status = "dry-run"
    finally:
        cm.close()

    data = {
        "prefixes": list(prefixes),
        "status": status,
        "rows_by_table": deleted,
        "total_rows": sum(deleted.values()),
    }
    if as_json:
        _output(data, as_json=True)
    else:
        verb = "Deleted" if apply else "Would delete"
        typer.echo(f"{verb} {data['total_rows']} rows for prefixes {list(prefixes)}:")
        for table, count in sorted(deleted.items()):
            typer.echo(f"  {table}: {count}")
        if not apply:
            typer.echo("(dry-run — pass --apply to delete)")


def _count_prefix_rows(
    cm: object,
    prefixes: tuple[str, ...],
    discover: object,
) -> dict[str, int]:
    """Count rows matching reserved prefixes per tenant table (read-only)."""
    from psycopg import sql as pgsql

    patterns = [f"{p}%" for p in prefixes if p]
    if not patterns:
        return {}
    clause = " OR ".join("project_id LIKE %s" for _ in patterns)
    counts: dict[str, int] = {}
    with cm.get_connection() as conn:  # type: ignore[attr-defined]
        for table in discover(cm):  # type: ignore[operator]
            stmt = pgsql.SQL("SELECT count(*) FROM {table} WHERE {where}").format(
                table=pgsql.Identifier(table),
                where=pgsql.SQL(clause),
            )
            with conn.cursor() as cur:
                cur.execute(stmt, tuple(patterns))
                row = cur.fetchone()
                n = int(row[0]) if row else 0
                if n > 0:
                    counts[table] = n
    return counts
