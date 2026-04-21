"""CLI tool for tapps-brain memory management and operations.

Provides commands for inspecting, searching, importing/exporting,
federating, and maintaining memory stores from the command line.

The CLI is organised as a ``typer`` package split across command-group
modules (store, memory, maintenance, hive, …).  Import side effects
register every command on the shared ``app`` singleton exported from
``tapps_brain.cli._common``.
"""

from __future__ import annotations

from typing import Annotated

# Import the _common module first — this creates the shared ``app`` + sub-app
# Typer instances and helper functions that every command module depends on.
from tapps_brain.cli._common import (
    JsonFlag,
    ProjectDir,
    _entry_to_row,
    _get_store,
    _open_hive_backend_for_cli,
    _output,
    _print_table,
    _resolve_project_dir,
    _version_callback,
    agent_app,
    app,
    diagnostics_app,
    feedback_app,
    flywheel_app,
    get_cli_agent_id,
    hive_app,
    maintenance_app,
    memory_app,
    openclaw_app,
    profile_app,
    project_app,
    relay_app,
    session_app,
    set_cli_agent_id,
    store_app,
    visual_app,
)
from tapps_brain.cli._common import (
    typer as _typer,
)

# ---------------------------------------------------------------------------
# Root callback — combines the legacy ``_main_callback`` (--agent-id) and the
# ``main`` version callback into a single ``@app.callback()`` because typer
# only honours one callback per ``Typer`` instance.
# ---------------------------------------------------------------------------


@app.callback()
def _main_callback(
    agent_id: Annotated[
        str | None,
        _typer.Option(
            "--agent-id",
            envvar="TAPPS_BRAIN_AGENT_ID",
            help="Agent identifier for per-agent storage isolation.",
        ),
    ] = None,
    _version: Annotated[
        bool,
        _typer.Option(
            "--version",
            "-v",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """tapps-brain: Persistent cross-session memory system for AI coding assistants."""
    # Make the --agent-id flag visible to every sub-command via the shared
    # ``_common._state`` dict (modules read it through ``get_cli_agent_id()``).
    set_cli_agent_id(agent_id)


# Backwards-compat alias: the original module exposed ``main``.
main = _main_callback


# ---------------------------------------------------------------------------
# Register every command module by import (side-effect).
#
# Each module decorates its commands with ``@<sub_app>.command(...)`` on the
# sub-app singletons declared in ``_common``.  The top-level ``app`` is the
# parent of all sub-apps thanks to ``app.add_typer(...)`` calls in _common.
# ---------------------------------------------------------------------------

from . import (  # noqa: E402, F401
    diagnostics,
    feedback,
    hive,
    maintenance,
    memory,
    openclaw,
    serve,
    session,
    store,
    top_level,
    visual,
)

# Re-export symbols that tests and the ``[project.scripts]`` entry point
# reference directly from ``tapps_brain.cli``.
from .diagnostics import (  # noqa: E402
    _circuit_status_color,
    _diagnostics_sre_status,
    _dimension_grade_letter,
)
from .feedback import _feedback_parse_details_option  # noqa: E402
from .maintenance import _strip_dsn_password  # noqa: E402
from .serve import cmd_serve  # noqa: E402

__all__ = [
    "JsonFlag",
    "ProjectDir",
    "_circuit_status_color",
    "_diagnostics_sre_status",
    "_dimension_grade_letter",
    "_entry_to_row",
    "_feedback_parse_details_option",
    "_get_store",
    "_main_callback",
    "_open_hive_backend_for_cli",
    "_output",
    "_print_table",
    "_resolve_project_dir",
    "_strip_dsn_password",
    "_version_callback",
    "agent_app",
    "app",
    "cmd_serve",
    "diagnostics_app",
    "feedback_app",
    "flywheel_app",
    "get_cli_agent_id",
    "hive_app",
    "main",
    "maintenance_app",
    "memory_app",
    "openclaw_app",
    "profile_app",
    "project_app",
    "relay_app",
    "session_app",
    "set_cli_agent_id",
    "store_app",
    "visual_app",
]


if __name__ == "__main__":
    app()
