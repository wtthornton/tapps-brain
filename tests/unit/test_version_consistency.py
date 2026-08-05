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


# Every copy of the agent-facing skill.  ``.cursor`` was NOT pinned here until
# 2026-08-05 and had silently drifted three releases behind (3.26.0 while the
# package shipped 3.29.0) — an unpinned copy is an unmaintained copy.
SKILL_COPIES = (
    ".claude/skills/tapps-brain/SKILL.md",
    ".cursor/skills/tapps-brain/SKILL.md",
    "src/tapps_brain/_assets/tapps-brain-skill.md",
)


def _read_skill_frontmatter_version(rel_path: str) -> str:
    """Read the ``version:`` field from a skill copy's frontmatter.

    Pinning each copy to the package version forces its content to be reviewed
    and re-pinned on every release, so the agent-facing skill never drifts
    behind the deployed brain (the doc surface it describes).
    """
    skill = PROJECT_ROOT / rel_path
    match = re.search(
        r'^version:\s*"?([^"\n]+?)"?\s*$',
        skill.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"{rel_path} frontmatter is missing a 'version:' field"
    return match.group(1)


def _read_docker_env_example_brain_version() -> str:
    """Read BRAIN_VERSION from docker/.env.example (compose image pin template)."""
    path = PROJECT_ROOT / "docker" / ".env.example"
    match = re.search(
        r"^BRAIN_VERSION=(.+)$",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, "docker/.env.example is missing BRAIN_VERSION="
    return match.group(1).strip()


def test_all_versions_match() -> None:
    """All distribution files must declare the same version string."""
    pyproject_ver = _read_pyproject_version()

    versions = {
        "pyproject.toml": pyproject_ver,
        "server.json": _read_server_json_version(),
        "docker/.env.example BRAIN_VERSION": _read_docker_env_example_brain_version(),
    }
    for rel_path in SKILL_COPIES:
        versions[rel_path] = _read_skill_frontmatter_version(rel_path)

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


def test_skill_bodies_do_not_claim_a_stale_release() -> None:
    """A skill body must not name a release older than the one being shipped.

    The frontmatter pin above is satisfied by bumping a single number, which is
    exactly what happened in 3.28.3 and again in 3.29.0: all three copies
    declared the new version while their bodies still read "current at v3.24.0"
    and "current at v3.26.0", and none documented the save-response contract
    those releases changed.

    This asserts the weaker but mechanically checkable half — that no body
    advertises a stale version. It cannot verify that prose describes current
    behaviour; that still needs a human reading the body against the CHANGELOG.
    """
    pyproject_ver = _read_pyproject_version()
    stale: list[str] = []
    for rel_path in SKILL_COPIES:
        text = (PROJECT_ROOT / rel_path).read_text(encoding="utf-8")
        for claimed in re.findall(r"current at v(\d+\.\d+\.\d+)", text):
            if claimed != pyproject_ver:
                stale.append(f"{rel_path}: body says 'current at v{claimed}'")
    assert not stale, (
        "Skill body advertises a stale release while pyproject.toml is at "
        f"{pyproject_ver}:\n  " + "\n  ".join(stale)
    )
