"""Session summarization — episodic memory capture (Issue #17).

Provides a Python API for saving end-of-session summaries as short-term
episodic memory entries. Corresponds to the CoALA episodic memory layer.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tapps_brain.store import MemoryStore

_PREFERRED_TIERS = ("short-term", "context", "pattern")
"""Ordered list of tier candidates for episodic memory entries.

We try ``short-term`` first (personal-assistant profile), then fall
back to ``context`` and ``pattern`` which exist in all built-in profiles.
"""


def _pick_tier(store: MemoryStore, preferred: str) -> str:
    """Return *preferred* if valid for the active profile, else the first fallback."""
    profile = getattr(store, "profile", None)
    layer_names: list[str] = list(profile.layer_names) if profile is not None else []

    # Collect all valid tiers: profile layer names + base MemoryTier values
    from tapps_brain.models import MemoryTier

    valid: set[str] = {t.value for t in MemoryTier} | set(layer_names)

    if preferred in valid:
        return preferred
    for fallback in _PREFERRED_TIERS:
        if fallback in valid:
            return fallback
    return "pattern"  # always valid


def session_summary_save(
    summary: str,
    *,
    tags: list[str] | None = None,
    project_dir: Path | None = None,
    workspace_dir: Path | None = None,
    daily_note: bool = False,
    tier: str = "short-term",
    scope: str = "project",
    source_agent: str = "agent",
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Save an end-of-session episodic memory entry.

    Creates a timestamped episodic memory entry tagged with ``date``,
    ``session``, and ``episodic`` in addition to any caller-supplied
    tags. The tier defaults to ``short-term`` (personal-assistant
    profile) and falls back to ``context`` then ``pattern`` when the
    active profile does not define ``short-term``. Optionally appends a
    formatted summary to ``memory/YYYY-MM-DD.md`` in the workspace
    directory.

    Args:
        summary: Human-readable summary of what happened in the session.
        tags: Additional tags to attach to the entry (beyond the default
            ``date``, ``session``, ``episodic`` tags).
        project_dir: Path to the tapps-brain project root.  Defaults to
            ``cwd``.
        workspace_dir: Workspace root used for daily note output.
            Defaults to ``project_dir``.
        daily_note: When ``True``, append the summary to today's daily
            note file at ``workspace_dir/memory/YYYY-MM-DD.md``.
        tier: Preferred memory tier (default: ``short-term``).  Auto-
            resolved to a valid tier for the active profile if the
            preferred value is not available.
        scope: Visibility scope (default: ``project``).
        source_agent: Agent identifier saved with the entry.
        max_chars: Optional character budget for the summary text.  When
            set and ``summary`` exceeds this length, the text is
            truncated at the last whitespace boundary before the limit
            and ``" …"`` is appended.  ``None`` (default) disables
            truncation.

    Returns:
        A dict with ``key``, ``status``, ``tags``, ``tier``, and
        ``scope`` fields on success, or an ``error`` field on failure.
        A ``truncated`` key is present (and ``True``) when the summary
        was shortened by the budget.
    """
    from tapps_brain.store import MemoryStore

    root = Path(project_dir).resolve() if project_dir else Path.cwd().resolve()

    # Apply token/character budget before persisting.
    truncated = False
    if max_chars is not None and len(summary) > max_chars:
        head = summary[:max_chars]
        cut = head.rsplit(None, 1)[0] if " " in head else head
        summary = cut + " …"
        truncated = True

    today = datetime.date.today().isoformat()
    now_ts = datetime.datetime.now(tz=datetime.UTC).strftime("%H%M%S")
    key = f"session.{today}.{now_ts}"

    base_tags = ["date", "session", "episodic"]
    all_tags = base_tags + [t for t in (tags or []) if t not in base_tags]

    store = MemoryStore(root)
    resolved_tier = _pick_tier(store, tier)
    try:
        result = store.save(
            key=key,
            value=summary,
            tier=resolved_tier,
            source="agent",
            source_agent=source_agent,
            scope=scope,
            tags=all_tags,
            agent_scope="private",
        )
    finally:
        store.close()

    if isinstance(result, dict) and result.get("error"):
        return result

    # Optionally write daily note
    if daily_note:
        ws = Path(workspace_dir).resolve() if workspace_dir else root
        _append_daily_note(ws, today, summary)

    out: dict[str, Any] = {
        "key": key,
        "status": "saved",
        "tags": all_tags,
        "tier": resolved_tier,
        "scope": scope,
    }
    if truncated:
        out["truncated"] = True
    return out


def _append_daily_note(workspace: Path, today: str, summary: str) -> None:
    """Append a formatted session summary block to today's daily note."""
    import datetime

    note_dir = workspace / "memory"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{today}.md"

    timestamp = datetime.datetime.now(tz=datetime.UTC).strftime("%H:%M UTC")
    block = f"\n## Session End — {timestamp}\n\n{summary}\n"

    with open(note_path, "a") as f:
        f.write(block)
