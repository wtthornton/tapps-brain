"""CLI for ``brain_export`` — one-shot Managed Agents-layout exporter (TAP-2099).

Snapshots the top-N memories per tier into a folder shaped for Anthropic
Managed Agents (``<dir>/<tier>/<key>.md`` plus ``manifest.json``).  Companion
to the ``brain_export`` MCP tool; provides an operator entrypoint for runs
that do not have an MCP client wired up.

Per the TAP-2095 feasibility report (``docs/research/file-backed-memory-mirror.md``):
this is a **snapshot, not a live mirror**.  Re-run the CLI when you need a
fresh export — there is no continuous sync.

Construction requires ``TAPPS_BRAIN_DATABASE_URL`` (per ADR-007 — Postgres
is the source of truth).  The CLI exits non-zero on validation errors and
writes JSON to stdout on success.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain_export",
        description=(
            "Export the top-N memories per tier into a Managed Agents-shaped "
            "folder layout. One-shot snapshot, NOT a live mirror — re-run for "
            "fresh data."
        ),
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Destination directory. Refuses to write into a non-empty path.",
    )
    parser.add_argument(
        "--project-id",
        default="",
        help="Project to export. Defaults to TAPPS_BRAIN_PROJECT_ID or the store default.",
    )
    parser.add_argument(
        "--layout",
        default="managed-agents",
        choices=["managed-agents", "okf"],
        help="Output layout: managed-agents (default) or okf (Open Knowledge Format v0.1 bundle).",
    )
    parser.add_argument(
        "--top-n-per-tier",
        type=int,
        default=500,
        help="Maximum entries per tier (default 500).",
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="Disable AWS/GH/JWT/email redaction. Entries tagged 'secret' are still skipped.",
    )
    args = parser.parse_args(argv)

    if not os.environ.get("TAPPS_BRAIN_DATABASE_URL"):
        sys.stderr.write(
            "error: TAPPS_BRAIN_DATABASE_URL not set. brain_export needs a "
            "MemoryStore backed by Postgres (ADR-007).\n"
        )
        return 2

    from tapps_brain.services import memory_service
    from tapps_brain.store import MemoryStore

    store = MemoryStore()
    project_id = args.project_id or os.environ.get("TAPPS_BRAIN_PROJECT_ID", "")
    agent_id = os.environ.get("TAPPS_BRAIN_AGENT_ID", "cli")

    report = memory_service.brain_export(
        store=store,
        project_id=project_id,
        agent_id=agent_id,
        output_dir=args.output_dir,
        layout=args.layout,
        redact=not args.no_redact,
        top_n_per_tier=args.top_n_per_tier,
        target_project_id=project_id,
    )

    if report.get("error"):
        sys.stderr.write(f"error: {report['error']}: {report.get('message', '')}\n")
        return 2

    sys.stdout.write(json.dumps(report, indent=2, default=str) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
