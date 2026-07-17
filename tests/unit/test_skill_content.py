"""Unit tests for bundled tapps-brain skill loader (TAP-2981)."""

from __future__ import annotations

from tapps_brain import __version__
from tapps_brain.skill_content import load_tapps_brain_skill


def test_load_tapps_brain_skill_contract() -> None:
    load_tapps_brain_skill.cache_clear()
    payload = load_tapps_brain_skill()
    assert payload["name"] == "tapps-brain"
    assert payload["version"] == __version__
    assert "/tapps-brain" in payload["body"]


def test_load_tapps_brain_skill_returns_fresh_dict() -> None:
    """Mutating a returned dict must not poison the cache for later callers."""
    load_tapps_brain_skill.cache_clear()
    first = load_tapps_brain_skill()
    original_body = first["body"]
    first["body"] = "POISON"
    second = load_tapps_brain_skill()
    assert second["body"] == original_body
    assert first is not second
