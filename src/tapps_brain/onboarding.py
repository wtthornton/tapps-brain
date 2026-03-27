"""Profile-driven onboarding text for coding agents (GitHub #45).

Produces a compact Markdown guide from the active :class:`MemoryProfile`
so agents know how tiers, scoring, recall, and limits apply to this project.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tapps_brain.profile import MemoryProfile


def render_agent_onboarding(profile: MemoryProfile) -> str:
    """Return Markdown guidance for using tapps-brain with *profile*."""
    lines: list[str] = [
        f"# tapps-brain — profile `{profile.name}` (v{profile.version})",
        "",
    ]
    if profile.description.strip():
        lines.extend([profile.description.strip(), ""])

    lines.extend(
        [
            "## How to use memory here",
            "",
            "- Prefer **recall** / **search** before inventing facts; cite memory keys when "
            "relevant.",
            "- Assign **tiers** intentionally: long-lived decisions vs session-only context.",
            "- Keep **values** concise; use **tags** for cross-cutting topics.",
            "- Respect **scope** (project vs session) and **agent_scope** if Hive is on.",
            "",
            "## Layers (tiers)",
            "",
            "| Layer | Half-life (days) | Notes |",
            "|-------|------------------|-------|",
        ]
    )
    for layer in profile.layers:
        desc = (layer.description or "").replace("|", "\\|")[:120]
        lines.append(f"| `{layer.name}` | {layer.half_life_days} | {desc} |")
    lines.append("")

    sc = profile.scoring
    lines.extend(
        [
            "## Retrieval scoring (composite)",
            "",
            f"- Relevance **{sc.relevance:.2f}**, confidence **{sc.confidence:.2f}**, "
            f"recency **{sc.recency:.2f}**, frequency **{sc.frequency:.2f}**.",
            "",
        ]
    )
    if sc.graph_centrality > 0.0 or sc.provenance_trust > 0.0:
        lines.append(
            f"- Extended: graph_centrality **{sc.graph_centrality:.2f}**, "
            f"provenance_trust **{sc.provenance_trust:.2f}**."
        )
        lines.append("")

    rc = profile.recall
    lines.extend(
        [
            "## Recall defaults",
            "",
            f"- Token budget ≈ **{rc.default_token_budget}**, "
            f"engagement **{rc.default_engagement}**.",
            f"- Minimum score **{rc.min_score:.2f}**, "
            f"minimum confidence **{rc.min_confidence:.2f}**.",
            "",
        ]
    )

    lim = profile.limits
    lines.extend(
        [
            "## Limits",
            "",
            f"- Max **{lim.max_entries}** entries; key ≤ **{lim.max_key_length}** chars; "
            f"value ≤ **{lim.max_value_length}** chars; ≤ **{lim.max_tags}** tags per entry.",
            "",
        ]
    )

    gc = profile.gc
    lines.extend(
        [
            "## Garbage collection",
            "",
            f"- Floor retention **{gc.floor_retention_days}** d, "
            f"session expiry **{gc.session_expiry_days}** d.",
            "",
        ]
    )

    hive = profile.hive
    lines.extend(
        [
            "## Hive (shared memory)",
            "",
            f"- Recall blend weight **{hive.recall_weight:.2f}**; "
            f"conflict policy `{hive.conflict_policy}`.",
            f"- Auto-propagate tiers: {', '.join(hive.auto_propagate_tiers) or '—'}; "
            f"private tiers: {', '.join(hive.private_tiers) or '—'}.",
            "",
        ]
    )

    lines.extend(
        [
            "---",
            "",
            "_Generated from the active YAML profile. Use `tapps-brain profile show` "
            "or the `memory_profile_onboarding` MCP tool to refresh._",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"
