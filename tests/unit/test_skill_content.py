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
