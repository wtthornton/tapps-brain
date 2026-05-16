"""CI guard: every forward migration must have a paired *.down.sql file.

TAP-1818 — postgres_migrations.py: no downgrade path for any migration.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_MIGRATIONS_ROOT = Path(__file__).parent.parent.parent / "src" / "tapps_brain" / "migrations"

_FWD_RE = re.compile(r"^(\d+)_.+(?<!\.down)\.sql$")
_DOWN_RE = re.compile(r"^(\d+)_.+\.down\.sql$")

# Migration planes that must have paired down files.
_PLANES = ("private", "hive", "federation")


def _forward_migrations(plane: str) -> list[tuple[int, str]]:
    """Return (version, stem) for all forward migrations in *plane*."""
    plane_dir = _MIGRATIONS_ROOT / plane
    results = []
    for p in sorted(plane_dir.iterdir()):
        if _FWD_RE.match(p.name):
            version = int(re.match(r"^(\d+)", p.name).group(1))  # type: ignore[union-attr]
            stem = p.stem  # e.g. "001_initial"
            results.append((version, stem))
    return results


def _down_stems(plane: str) -> set[str]:
    """Return the set of *stem* values for discovered down migrations.

    For a file named ``001_initial.down.sql`` the stem returned is
    ``001_initial`` (i.e. the part before ``.down.sql``).
    """
    plane_dir = _MIGRATIONS_ROOT / plane
    stems = set()
    for p in sorted(plane_dir.iterdir()):
        if _DOWN_RE.match(p.name):
            # Strip the trailing ".down" from the stem.
            stem = p.stem  # gives "001_initial.down"
            if stem.endswith(".down"):
                stem = stem[: -len(".down")]
            stems.add(stem)
    return stems


@pytest.mark.parametrize("plane", _PLANES)
def test_every_forward_migration_has_a_down_file(plane: str) -> None:
    """Assert that every NNN_*.sql file has a matching NNN_*.down.sql sibling."""
    forward = _forward_migrations(plane)
    assert forward, f"No forward migrations found for plane '{plane}' — check path."

    down_stems = _down_stems(plane)
    missing = [stem for _v, stem in forward if stem not in down_stems]

    assert not missing, (
        f"Migration plane '{plane}': the following forward migrations have no "
        f"paired *.down.sql file:\n"
        + "\n".join(f"  {s}.sql" for s in missing)
    )


@pytest.mark.parametrize("plane", _PLANES)
def test_down_files_have_no_orphans(plane: str) -> None:
    """Assert that every *.down.sql file has a corresponding forward migration."""
    forward_stems = {stem for _v, stem in _forward_migrations(plane)}
    down_stems = _down_stems(plane)

    orphans = down_stems - forward_stems
    assert not orphans, (
        f"Migration plane '{plane}': the following *.down.sql files have no "
        f"corresponding forward migration:\n"
        + "\n".join(f"  {s}.down.sql" for s in sorted(orphans))
    )
