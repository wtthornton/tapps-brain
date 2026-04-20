"""Shared helpers, sub-app Typer instances, type aliases, and constants.

Every command module in the ``tapps_brain.cli`` package imports from this
module so the sub-app instances and helper functions are shared singletons.

The ``typer`` import guard lives here; downstream modules can import typer
normally because this module is always imported first by ``__init__``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

import structlog

try:
    import typer
except ImportError:
    _msg = (
        "The 'typer' package is required for the tapps-brain CLI.\n"
        "Install it with: uv sync --extra cli  (or --extra all)\n"
    )
    raise SystemExit(_msg) from None

from tapps_brain import __version__
from tapps_brain.agent_scope import normalize_agent_scope

# Route structlog output to stderr so it doesn't pollute CLI stdout/JSON
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING),
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PREVIEW_LEN = 80  # Max chars shown for a memory value preview in table output

# Diagnostics CLI letter-grade cutoffs (composite dimension score 0-1).
_DIAG_GRADE_A_MIN = 0.85
_DIAG_GRADE_B_MIN = 0.70
_DIAG_GRADE_C_MIN = 0.55
_DIAG_GRADE_D_MIN = 0.40

# Top-level ``stats`` command: effective confidence below this counts as near-expiry.
_STATS_NEAR_EXPIRY_THRESHOLD = 0.35

# ---------------------------------------------------------------------------
# App setup — sub-app Typer instances shared across all command modules.
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="tapps-brain",
    help=(
        "Persistent cross-session memory for AI assistants — BM25, decay, "
        "Hive. Sub-apps: store, memory, feedback, diagnostics, flywheel, "
        "hive, openclaw, …"
    ),
    no_args_is_help=True,
)
store_app = typer.Typer(help="Inspect store contents and statistics.", no_args_is_help=True)
memory_app = typer.Typer(
    help="Query, inspect, and save individual memories (MCP parity for save).",
    no_args_is_help=True,
)
maintenance_app = typer.Typer(help="Run store maintenance operations.", no_args_is_help=True)

profile_app = typer.Typer(help="Manage memory profiles.", no_args_is_help=True)
hive_app = typer.Typer(help="Manage the Hive shared brain.", no_args_is_help=True)
agent_app = typer.Typer(help="Manage Hive agent registrations.", no_args_is_help=True)

openclaw_app = typer.Typer(help="OpenClaw integration tools.", no_args_is_help=True)
feedback_app = typer.Typer(help="Record and query feedback events.", no_args_is_help=True)
diagnostics_app = typer.Typer(
    help="Quality diagnostics scorecard (EPIC-030).",
    no_args_is_help=True,
)
flywheel_app = typer.Typer(
    help="Continuous improvement flywheel (EPIC-031).",
    no_args_is_help=True,
)
visual_app = typer.Typer(
    help=(
        "Export JSON snapshots for brain visual surfaces "
        "(see docs/planning/brain-visual-implementation-plan.md)."
    ),
    no_args_is_help=True,
)

app.add_typer(store_app, name="store")
app.add_typer(memory_app, name="memory")
app.add_typer(maintenance_app, name="maintenance")
app.add_typer(profile_app, name="profile")
app.add_typer(hive_app, name="hive")
app.add_typer(agent_app, name="agent")
app.add_typer(openclaw_app, name="openclaw")
app.add_typer(feedback_app, name="feedback")
app.add_typer(diagnostics_app, name="diagnostics")
app.add_typer(flywheel_app, name="flywheel")
app.add_typer(visual_app, name="visual")

session_app = typer.Typer(help="Session lifecycle commands.", no_args_is_help=True)
app.add_typer(session_app, name="session")

relay_app = typer.Typer(
    help="Cross-node memory relay — import payloads from sub-agents (GitHub #19).",
    no_args_is_help=True,
)
app.add_typer(relay_app, name="relay")

# EPIC-069: per-project profile registry for multi-tenant deployments.
project_app = typer.Typer(
    help="Register and manage per-project profiles (EPIC-069, ADR-010).",
    no_args_is_help=True,
)
app.add_typer(project_app, name="project")

# ---------------------------------------------------------------------------
# Global options (type aliases reused across command modules)
# ---------------------------------------------------------------------------

ProjectDir = Annotated[
    Path | None,
    typer.Option(
        "--project-dir",
        "-d",
        help="Project root directory (defaults to cwd).",
        envvar="TAPPS_BRAIN_PROJECT_DIR",
    ),
]
JsonFlag = Annotated[
    bool,
    typer.Option("--json", "-j", help="Output as JSON."),
]


# ---------------------------------------------------------------------------
# Global state for agent-id (STORY-053.4)
#
# The CLI ``--agent-id`` flag (and its env var) is set by the root callback
# before any sub-command runs.  Because commands live in several modules we
# expose a module-level dict as the shared mutable store; ``_main_callback``
# (in ``cli.__init__``) writes to it, helper ``_get_store`` reads from it.
# ---------------------------------------------------------------------------

_state: dict[str, str | None] = {"cli_agent_id": None}


def get_cli_agent_id() -> str | None:
    """Return the currently-active ``--agent-id`` (None = default)."""
    return _state["cli_agent_id"]


def set_cli_agent_id(value: str | None) -> None:
    """Record the ``--agent-id`` from the root callback for later ``_get_store`` use."""
    _state["cli_agent_id"] = value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_project_dir(project_dir: Path | None) -> Path:
    """Resolve project directory, defaulting to cwd."""
    return (project_dir or Path.cwd()).resolve()


def _get_store(project_dir: Path | None) -> Any:  # noqa: ANN401
    """Open a MemoryStore from the resolved project dir."""
    from tapps_brain.backends import resolve_hive_backend_from_env
    from tapps_brain.store import MemoryStore

    root = _resolve_project_dir(project_dir)
    agent_id = get_cli_agent_id()
    return MemoryStore(
        root,
        agent_id=agent_id,
        hive_store=resolve_hive_backend_from_env(),
        hive_agent_id=agent_id or "cli",
    )


def _open_hive_backend_for_cli() -> Any:  # noqa: ANN401
    """Return Postgres Hive backend or exit with an error (ADR-007)."""
    from tapps_brain.backends import resolve_hive_backend_from_env

    hb = resolve_hive_backend_from_env()
    if hb is None:
        typer.echo(
            "Error: Hive requires TAPPS_BRAIN_HIVE_DSN=postgresql://... "
            "(SQLite Hive removed — ADR-007).",
            err=True,
        )
        raise typer.Exit(code=1)
    return hb


def _output(data: Any, as_json: bool) -> None:  # noqa: ANN401
    """Print data as JSON or formatted text."""
    if as_json:
        typer.echo(json.dumps(data, indent=2, default=str))
    elif isinstance(data, dict):
        for k, v in data.items():
            typer.echo(f"  {k}: {v}")
    elif isinstance(data, list):
        for item in data:
            typer.echo(item)
    else:
        typer.echo(str(data))


def _entry_to_row(entry: Any) -> dict[str, Any]:  # noqa: ANN401
    """Convert a MemoryEntry to a display-friendly dict."""
    from tapps_brain.decay import DecayConfig, get_effective_confidence

    eff_conf, _ = get_effective_confidence(entry, DecayConfig())
    row: dict[str, Any] = {
        "key": entry.key,
        "tier": str(entry.tier),
        "confidence": f"{entry.confidence:.2f}",
        "effective": f"{eff_conf:.2f}",
        "scope": entry.scope.value,
        "group": entry.memory_group or "",
        "tags": ", ".join(entry.tags) if entry.tags else "",
        "created": entry.created_at[:10],
    }
    if entry.valid_at:
        row["valid_at"] = entry.valid_at[:10]
    if entry.invalid_at:
        row["invalid_at"] = entry.invalid_at[:10]
    if entry.superseded_by:
        row["superseded_by"] = entry.superseded_by
    return row


def _print_table(rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    """Print a list of dicts as a simple aligned table."""
    if not rows:
        typer.echo("  (no results)")
        return

    if columns is None:
        columns = list(rows[0].keys())

    widths = {col: len(col) for col in columns}
    for row in rows:
        for col in columns:
            val = str(row.get(col, ""))
            widths[col] = max(widths[col], len(val))

    header = "  ".join(col.upper().ljust(widths[col]) for col in columns)
    typer.echo(header)
    typer.echo("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        line = "  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        typer.echo(line)


# ---------------------------------------------------------------------------
# Version callback (top-level --version flag)
# ---------------------------------------------------------------------------


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"tapps-brain {__version__}")
        raise typer.Exit


# Re-export the agent_scope helper so sub-modules can pull it from here.
__all__ = [
    "_DIAG_GRADE_A_MIN",
    "_DIAG_GRADE_B_MIN",
    "_DIAG_GRADE_C_MIN",
    "_DIAG_GRADE_D_MIN",
    "_PREVIEW_LEN",
    "_STATS_NEAR_EXPIRY_THRESHOLD",
    "Annotated",
    "Any",
    "JsonFlag",
    "Path",
    "ProjectDir",
    "_entry_to_row",
    "_get_store",
    "_open_hive_backend_for_cli",
    "_output",
    "_print_table",
    "_resolve_project_dir",
    "_version_callback",
    "agent_app",
    "app",
    "diagnostics_app",
    "feedback_app",
    "flywheel_app",
    "get_cli_agent_id",
    "hive_app",
    "json",
    "maintenance_app",
    "memory_app",
    "normalize_agent_scope",
    "openclaw_app",
    "profile_app",
    "project_app",
    "relay_app",
    "session_app",
    "set_cli_agent_id",
    "store_app",
    "structlog",
    "typer",
    "visual_app",
]
