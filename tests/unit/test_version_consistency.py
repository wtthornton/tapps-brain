"""Test that version strings are consistent across all distribution files.

Commit: test(story-012.6): version consistency check
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

# Project root is two levels up from tests/unit/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _read_pyproject_version() -> str:
    """Read version from pyproject.toml."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    version: str = data["project"]["version"]
    return version


def _read_server_json_version() -> str:
    """Read version from server.json (MCP server manifest)."""
    server = PROJECT_ROOT / "server.json"
    data = json.loads(server.read_text(encoding="utf-8"))
    version: str = data["version"]
    return version


def _read_skill_version() -> str:
    """Read the ``version:`` field from the tapps-brain SKILL.md frontmatter.

    Pinning the skill's version to the package version forces the SKILL.md
    content to be reviewed and re-pinned on every release, so the agent-facing
    skill never drifts behind the deployed brain (the doc surface it describes).
    """
    skill = PROJECT_ROOT / ".claude/skills/tapps-brain/SKILL.md"
    match = re.search(
        r'^version:\s*"?([^"\n]+?)"?\s*$',
        skill.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, "SKILL.md frontmatter is missing a 'version:' field"
    return match.group(1)


def test_all_versions_match() -> None:
    """All distribution files must declare the same version string."""
    pyproject_ver = _read_pyproject_version()
    server_json_ver = _read_server_json_version()
    skill_ver = _read_skill_version()

    versions = {
        "pyproject.toml": pyproject_ver,
        "server.json": server_json_ver,
        ".claude/skills/tapps-brain/SKILL.md": skill_ver,
    }

    # All must be non-empty
    for name, ver in versions.items():
        assert ver, f"{name} has empty version"

    # All must match pyproject.toml (the canonical source)
    for name, ver in versions.items():
        assert ver == pyproject_ver, (
            f"Version mismatch: {name} has '{ver}' but pyproject.toml has '{pyproject_ver}'"
        )


def test_version_is_valid_semver() -> None:
    """The canonical version must be valid semver (MAJOR.MINOR.PATCH)."""
    version = _read_pyproject_version()
    assert re.match(r"^\d+\.\d+\.\d+([a-zA-Z0-9.+-]*)?$", version), (
        f"Version '{version}' is not valid semver"
    )
