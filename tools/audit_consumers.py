"""CLI for ``brain_audit_consumers`` — declared-but-silent agent audit (TAP-2093).

Joins :class:`tapps_brain.backends.AgentRegistry` (YAML, persistent) with the
in-process per-tool call counter from
:func:`tapps_brain.otel_tracer.get_tool_call_counts_snapshot` and prints a
human-readable table.

Because the call counter is **in-process**, running the CLI as a separate
short-lived process will always see an empty counter (every agent reports
``silent``). The CLI is therefore mainly useful in:

- **CI** — seeded fixtures that exercise the brain in-process, then dump the
  audit before exit.
- **Operator workflows** — invocation from the same Python process / container
  as the running tapps-brain (e.g. ``docker exec`` into ``tapps-brain-http``).

For ad-hoc operator queries against a deployed server, prefer the MCP tool
``brain_audit_consumers`` via the standard tapps-brain MCP endpoint.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _format_table(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"project_id          : {report.get('project_id', '')}")
    lines.append(f"as_of               : {report.get('as_of', '')}")
    lines.append(f"window_effective    : {report.get('window_effective', '')}")
    lines.append(f"since_requested     : {report.get('since_requested', '') or '(none)'}")
    lines.append("")
    silent = report.get("declared_silent", [])
    lines.append(f"declared_silent ({len(silent)}):")
    for aid in silent:
        lines.append(f"  - {aid}")
    if not silent:
        lines.append("  (none)")
    lines.append("")
    active = report.get("active", [])
    lines.append(f"active ({len(active)}):")
    if active:
        lines.append(f"  {'agent_id':<40s}  total  tools")
        for row in active:
            tool_summary = ", ".join(f"{k}={v}" for k, v in row.get("tools", {}).items())
            lines.append(
                f"  {row.get('agent_id', ''):<40s}  {row.get('total_calls', 0):>5}  {tool_summary}"
            )
    else:
        lines.append("  (none)")
    lines.append("")
    orphans = report.get("unregistered_active", [])
    lines.append(f"unregistered_active ({len(orphans)}):")
    for aid in orphans:
        lines.append(f"  - {aid}")
    if not orphans:
        lines.append("  (none)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audit_consumers",
        description=(
            "Surface declared-but-silent tapps-brain consumer agents. "
            "Reads the in-process call counter — see module docstring for caveats."
        ),
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Project id to audit (e.g. 'tapps-brain').",
    )
    parser.add_argument(
        "--since",
        default="",
        help="Optional ISO-8601 timestamp (informational; counter is cumulative).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of the human-readable table.",
    )
    args = parser.parse_args(argv)

    from tapps_brain.services import memory_service

    report = memory_service.audit_consumers(
        store=None,
        project_id=args.project_id,
        agent_id="cli",
        target_project_id=args.project_id,
        since=args.since,
    )

    if report.get("error"):
        sys.stderr.write(f"error: {report['error']}: {report.get('message', '')}\n")
        return 2

    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, default=str) + "\n")
    else:
        sys.stdout.write(_format_table(report) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
