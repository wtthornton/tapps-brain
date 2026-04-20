"""``visual`` sub-app commands: export JSON snapshot + capture PNG for
the brain-visual dashboard (see docs/planning/brain-visual-implementation-plan.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, cast

import typer

from tapps_brain.cli._common import ProjectDir, _get_store, visual_app


@visual_app.command("export")
def visual_export_cmd(
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output path for brain-visual.json (default: ./brain-visual.json).",
        ),
    ] = Path("brain-visual.json"),
    project_dir: ProjectDir = None,
    skip_diagnostics: Annotated[
        bool,
        typer.Option(
            "--skip-diagnostics",
            help="Skip store diagnostics (faster; omits circuit_state/composite_score).",
        ),
    ] = False,
    privacy: Annotated[
        str,
        typer.Option(
            "--privacy",
            help=(
                "standard (default) | strict (redact path/tampered keys) | "
                "local (tag + group names)."
            ),
        ),
    ] = "standard",
) -> None:
    """Write a versioned JSON snapshot for the static brain visual demo and dashboards."""
    from tapps_brain.visual_snapshot import PrivacyTier, build_visual_snapshot, snapshot_to_json

    if privacy not in {"standard", "strict", "local"}:
        typer.echo("Error: --privacy must be standard, strict, or local.", err=True)
        raise typer.Exit(code=1)
    tier = cast("PrivacyTier", privacy)
    store = _get_store(project_dir)
    try:
        snap = build_visual_snapshot(store, skip_diagnostics=skip_diagnostics, privacy=tier)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(snapshot_to_json(snap), encoding="utf-8")
    finally:
        store.close()
    typer.echo(f"Wrote {output.resolve()}")


@visual_app.command("capture")
def visual_capture_cmd(  # pragma: no cover
    json_path: Annotated[
        Path,
        typer.Option(
            "--json",
            "-j",
            help="Path to brain-visual.json snapshot (required).",
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Destination PNG path (default: brain-visual.png).",
        ),
    ] = Path("brain-visual.png"),
    html: Annotated[
        Path,
        typer.Option(
            "--html",
            help="Path to examples/brain-visual/index.html.",
        ),
    ] = Path("examples/brain-visual/index.html"),
    width: Annotated[
        int,
        typer.Option("--width", help="Viewport width in px (default 1280)."),
    ] = 1280,
    height: Annotated[
        int,
        typer.Option("--height", help="Viewport height in px (default 900)."),
    ] = 900,
    theme: Annotated[
        str,
        typer.Option("--theme", help="light (default) or dark."),
    ] = "light",
) -> None:
    """Capture a headless PNG of the brain-visual dashboard.

    Requires the [visual] optional extra:

        uv sync --extra visual
        playwright install chromium
    """
    from tapps_brain.visual_snapshot import capture_png

    if theme not in {"light", "dark"}:
        typer.echo("Error: --theme must be light or dark.", err=True)
        raise typer.Exit(code=1)
    try:
        capture_png(
            html_path=html,
            json_path=json_path,
            output=output,
            width=width,
            height=height,
            theme=theme,
        )
    except RuntimeError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Wrote {output.resolve()}")
