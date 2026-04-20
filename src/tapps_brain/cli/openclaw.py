"""``openclaw`` sub-app commands: init and upgrade workspace scaffolding."""

from __future__ import annotations

import typer

from tapps_brain.cli._common import get_cli_agent_id, openclaw_app


@openclaw_app.command("init")
def openclaw_init(
    project_dir: str = typer.Option(".", help="Project directory"),
) -> None:
    """Initialize a workspace with correct tapps-brain memory hierarchy."""
    from pathlib import Path

    root = Path(project_dir)

    # Create .tapps-brain dir if needed
    tb_dir = root / ".tapps-brain"
    tb_dir.mkdir(parents=True, exist_ok=True)

    # Write default profile if not exists
    profile_path = tb_dir / "profile.yaml"
    if not profile_path.exists():
        profile_path.write_text(
            "profile:\n  extends: personal-assistant\n  layers: []\n  name: personal-assistant\n",
            encoding="utf-8",
        )
        typer.echo(f"Created {profile_path}")

    # Create memory dir
    mem_dir = tb_dir / "memory"
    mem_dir.mkdir(exist_ok=True)

    typer.echo("✅ Workspace initialized for tapps-brain")


@openclaw_app.command("upgrade")
def openclaw_upgrade(
    project_dir: str = typer.Option(".", help="Project directory"),
) -> None:
    """Upgrade workspace — export MEMORY.md from tapps-brain entries."""
    from pathlib import Path

    from tapps_brain.store import MemoryStore

    root = Path(project_dir)
    store = MemoryStore(root, agent_id=get_cli_agent_id())
    entries = store.list_all()

    # Export identity + long-term entries to MEMORY.md
    memory_md = root / "MEMORY.md"
    lines = ["# MEMORY.md — Auto-generated from tapps-brain\n\n"]
    lines.append(f"*Exported {len(entries)} total entries*\n\n")

    for entry in sorted(entries, key=lambda e: e.tier):
        tier = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
        if tier in ("identity", "long-term"):
            lines.append(f"## {entry.key}\n")
            lines.append(f"**Tier:** {tier} | **Confidence:** {entry.confidence:.2f}\n\n")
            lines.append(f"{entry.value}\n\n---\n\n")

    memory_md.write_text("".join(lines), encoding="utf-8")
    typer.echo(f"✅ Exported {len(entries)} entries to {memory_md}")
