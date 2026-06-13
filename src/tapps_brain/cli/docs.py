"""``docs`` sub-app: legacy cache import for brain-central doc RAG."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from tapps_brain.cli._common import JsonFlag, ProjectDir, _output, docs_app


@docs_app.command("import-dir")
def docs_import_dir_cmd(
    cache_dir: Annotated[
        Path,
        typer.Argument(help="Path to legacy .tapps-mcp-cache directory."),
    ],
    skip_existing: Annotated[
        bool,
        typer.Option(help="Skip library/topic keys already present in brain."),
    ] = True,
    project_dir: ProjectDir = None,
    as_json: JsonFlag = False,
) -> None:
    """Import legacy per-repo doc cache markdown + meta sidecars into brain."""
    from tapps_brain.docs_import import import_cache_dir
    from tapps_brain.docs_lookup import get_docs_store

    _ = project_dir  # library-docs store is global; caller project_dir ignored
    store = get_docs_store()
    report = import_cache_dir(store, cache_dir, skip_existing=skip_existing)
    _output(report.as_dict(), as_json=as_json)
