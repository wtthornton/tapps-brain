"""Release-artifact freshness checks.

These differ from ``test_version_consistency.py`` (which asserts declared
version *strings* match): they assert the generated release *artifacts* —
the per-version OpenAPI contract snapshot and the ``llms.txt`` pair — are
present and regenerated for the current package version.

Both classes of drift have shipped before: 3.22.4 was tagged with its
``docs/contracts/openapi-3.22.4.json`` snapshot missing, and ``llms.txt``
advertised 3.20.1 for two releases because it was regenerated from a stale
checkout. Pinning these to the package version makes the test fail until the
artifact is regenerated, the same forcing function the SKILL.md version pin
provides.
"""

from __future__ import annotations

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


def _read_llms_version(filename: str) -> str:
    """Read the ``- Version: X.Y.Z`` line from an llms.txt-style file."""
    path = PROJECT_ROOT / filename
    match = re.search(
        r"^- Version:\s*(\S+)\s*$",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, f"{filename} is missing a '- Version:' line"
    return match.group(1)


def test_openapi_snapshot_for_current_version_exists() -> None:
    """A per-version OpenAPI snapshot must exist for the current release.

    Forces ``docs/contracts/openapi-<version>.json`` to be regenerated and
    committed each release (the convention the other 25 snapshots establish),
    so the contract history is never missing the shipped version.
    """
    version = _read_pyproject_version()
    snapshot = PROJECT_ROOT / "docs" / "contracts" / f"openapi-{version}.json"
    assert snapshot.is_file(), (
        f"missing OpenAPI contract snapshot {snapshot.relative_to(PROJECT_ROOT)} "
        f"for version {version} — regenerate the contract and commit it before tagging"
    )


def test_llms_txt_version_matches_pyproject() -> None:
    """llms.txt and llms-full.txt must advertise the current package version.

    These are generated files that must be regenerated each release; a stale
    version here means an AI consumer reads an out-of-date project summary.
    """
    expected = _read_pyproject_version()
    for filename in ("llms.txt", "llms-full.txt"):
        actual = _read_llms_version(filename)
        assert actual == expected, (
            f"{filename} declares version {actual!r} but pyproject is {expected!r} "
            f"— regenerate with docs_generate_llms_txt before tagging"
        )
