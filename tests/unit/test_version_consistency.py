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


def _read_skill_md_version() -> str:
    """Read version from openclaw-skill/SKILL.md YAML frontmatter."""
    skill_md = PROJECT_ROOT / "openclaw-skill" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    # YAML frontmatter is between --- delimiters; \r? handles CRLF line endings
    match = re.search(r"^---\s*\r?\n(.*?)\r?\n---", text, re.DOTALL)
    assert match, "SKILL.md must have YAML frontmatter"
    frontmatter = match.group(1)
    ver_match = re.search(r'^version:\s*["\']?([^"\'"\r\n]+)["\']?', frontmatter, re.MULTILINE)
    assert ver_match, "SKILL.md frontmatter must contain a version field"
    return ver_match.group(1).strip()


def _read_package_json_version() -> str:
    """Read version from openclaw-plugin/package.json."""
    pkg = PROJECT_ROOT / "openclaw-plugin" / "package.json"
    data = json.loads(pkg.read_text(encoding="utf-8"))
    version: str = data["version"]
    return version


def _read_plugin_json_version() -> str:
    """Read version from openclaw-skill/openclaw.plugin.json."""
    plugin = PROJECT_ROOT / "openclaw-skill" / "openclaw.plugin.json"
    data = json.loads(plugin.read_text(encoding="utf-8"))
    version: str = data["version"]
    return version


def _read_server_json_version() -> str:
    """Read version from server.json (MCP server manifest)."""
    server = PROJECT_ROOT / "server.json"
    data = json.loads(server.read_text(encoding="utf-8"))
    version: str = data["version"]
    return version


def test_all_versions_match() -> None:
    """All distribution files must declare the same version string."""
    pyproject_ver = _read_pyproject_version()
    skill_md_ver = _read_skill_md_version()
    package_json_ver = _read_package_json_version()
    plugin_json_ver = _read_plugin_json_version()
    server_json_ver = _read_server_json_version()

    versions = {
        "pyproject.toml": pyproject_ver,
        "openclaw-skill/SKILL.md": skill_md_ver,
        "openclaw-plugin/package.json": package_json_ver,
        "openclaw-skill/openclaw.plugin.json": plugin_json_ver,
        "server.json": server_json_ver,
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
