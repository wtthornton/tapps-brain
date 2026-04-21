"""``diagnostics`` and ``flywheel`` sub-app commands (EPIC-030 / EPIC-031)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from tapps_brain.cli._common import (
    _DIAG_GRADE_A_MIN,
    _DIAG_GRADE_B_MIN,
    _DIAG_GRADE_C_MIN,
    _DIAG_GRADE_D_MIN,
    JsonFlag,
    ProjectDir,
    _get_store,
    _output,
    _print_table,
    diagnostics_app,
    flywheel_app,
)


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
