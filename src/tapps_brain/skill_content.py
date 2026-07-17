"""Load the version-pinned tapps-brain agent skill from package data (TAP-2981)."""

from __future__ import annotations

import re
from functools import lru_cache
from importlib.resources import files

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@lru_cache(maxsize=1)
def _load_tapps_brain_skill_cached() -> tuple[str, str, str]:
    """Cached ``(name, version, body)`` tuple — immutable so callers cannot poison the cache."""
    raw = (files("tapps_brain") / "_assets" / "tapps-brain-skill.md").read_text(encoding="utf-8")
    name = "tapps-brain"
    version = ""
    body = raw
    match = _FRONTMATTER_RE.match(raw)
    if match:
        body = raw[match.end() :]
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key == "name":
                name = val
            elif key == "version":
                version = val
    if not version:
        from tapps_brain import __version__

        version = __version__
    return (name, version, body)


def load_tapps_brain_skill() -> dict[str, str]:
    """Return ``{name, version, body}`` for the bundled SKILL.md artifact.

    Each call returns a **fresh** dict so callers cannot mutate the cached
    skill content for subsequent callers.
    """
    name, version, body = _load_tapps_brain_skill_cached()
    return {"name": name, "version": version, "body": body}


# Preserve ``.cache_clear()`` for tests / hot-reload without exposing the tuple cache.
load_tapps_brain_skill.cache_clear = _load_tapps_brain_skill_cached.cache_clear  # type: ignore[attr-defined]
