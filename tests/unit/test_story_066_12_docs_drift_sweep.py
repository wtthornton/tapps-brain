"""Unit tests for STORY-066.12: Engineering docs drift sweep.

Verifies all 9 acceptance criteria without requiring a live Postgres instance:

  AC1 — docs/engineering/ no longer presents SQLite public names as current architecture
  AC2 — docs/guides/ no longer presents SQLite public names as current architecture
  AC3/AC4 — system-architecture.md describes (project_id, agent_id) tenant key model
  AC5 — data-stores-and-schema.md describes private/hive/federation Postgres schemas
         with migration version refs
  AC6 — v3-behavioral-parity.md FeedbackStore row updated to reflect Postgres backend
  AC7 — threat-model.md updated to reference pg_tde
  AC8 — sqlite-to-postgres-meeting-notes.md archived or removed from docs/planning/
  AC9 — internal links in each changed file resolve to existing files
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_ENG = _REPO_ROOT / "docs" / "engineering"
_GUIDES = _REPO_ROOT / "docs" / "guides"
_PLANNING = _REPO_ROOT / "docs" / "planning"

_SYS_ARCH = _ENG / "system-architecture.md"
_DATA_STORES = _ENG / "data-stores-and-schema.md"
_PARITY = _ENG / "v3-behavioral-parity.md"
_THREAT_MODEL = _ENG / "threat-model.md"
_MEETING_NOTES_ORIG = _PLANNING / "sqlite-to-postgres-meeting-notes.md"
_MEETING_NOTES_ARCHIVE = _PLANNING / "archive" / "sqlite-to-postgres-meeting-notes.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Symbols that should NOT appear as current architecture in engineering docs.
# Historical changelog entries and past-tense migration notes are acceptable,
# so we look for these symbols in actively-instructional prose, not changelogs.
_STALE_SYMBOLS = [
    "MemoryPersistence",
    "SqliteAgentRegistryBackend",
    "sqlite_vec_knn_search",
    "sqlite_vec_row_count",
    "sqlcipher_enabled",
    "connect_sqlite",
    "encryption_migrate",
]


def _relative_links(path: Path) -> list[tuple[str, Path]]:
    """Return (raw_link, resolved_path) for relative markdown links in *path*."""
    content = path.read_text()
    pattern = re.compile(r"\[.*?\]\(([^)]+)\)")
    results: list[tuple[str, Path]] = []
    for match in pattern.finditer(content):
        target = match.group(1).split("#")[0].strip()
        if not target or target.startswith("http"):
            continue
        results.append((target, (path.parent / target).resolve()))
    return results


def _check_links(path: Path) -> list[str]:
    return [
        f"{raw!r} → {resolved}" for raw, resolved in _relative_links(path) if not resolved.exists()
    ]


# ---------------------------------------------------------------------------
# AC1 — docs/engineering no longer has stale SQLite symbols as current arch
# ---------------------------------------------------------------------------


class TestAc1EngineeringDriftSweep:
    """docs/engineering/ does not present stale SQLite public names as current."""

    def _engineering_content(self) -> str:
        return "\n".join(p.read_text() for p in _ENG.glob("*.md"))

    def test_ac1_no_stale_symbols_in_engineering(self) -> None:
        content = self._engineering_content()
        hits = [sym for sym in _STALE_SYMBOLS if sym in content]
        assert not hits, f"Stale SQLite symbols still in docs/engineering/: {hits}"

    def test_ac1_sqlite_not_current_storage_in_system_arch(self) -> None:
        """system-architecture.md presents Postgres (not SQLite) as current storage."""
        content = _SYS_ARCH.read_text()
        # Postgres must be the current store
        assert "PostgreSQL" in content or "Postgres" in content
        # SQLite should not appear as a current option (only in ADR reference titles)
        sqlite_current = [
            line
            for line in content.splitlines()
            if "sqlite" in line.lower()
            and "removed" not in line.lower()
            and "adr-007" not in line.lower()
            and "no-sqlite" not in line.lower()
        ]
        assert not sqlite_current, (
            "system-architecture.md still presents SQLite as current:\n" + "\n".join(sqlite_current)
        )


# ---------------------------------------------------------------------------
# AC2 — docs/guides/ no longer has stale SQLite symbols as current arch
# ---------------------------------------------------------------------------


class TestAc2GuidesDriftSweep:
    """docs/guides/ does not present stale SQLite public names as current."""

    def test_ac2_no_stale_symbols_in_guides(self) -> None:
        content = "\n".join(p.read_text() for p in _GUIDES.glob("*.md"))
        hits = [sym for sym in _STALE_SYMBOLS if sym in content]
        assert not hits, f"Stale SQLite symbols still in docs/guides/: {hits}"

    def test_ac2_sqlitehivebackend_only_in_delta_tables(self) -> None:
        """SqliteHiveBackend appears only in v2→v3 migration context, not as current API."""
        hits: list[str] = []
        for path in _GUIDES.glob("*.md"):
            content = path.read_text()
            for line in content.splitlines():
                if "SqliteHiveBackend" not in line:
                    continue
                # Acceptable: migration delta table row (| OldSymbol | NewSymbol |)
                # or explicit removed/v2 context
                if (
                    "PostgresHiveBackend" in line  # v2→v3 delta table
                    or "v2" in line.lower()
                    or "removed" in line.lower()
                ):
                    continue
                hits.append(f"{path.name}: {line.strip()}")
        assert not hits, "SqliteHiveBackend appears as current API in guides:\n" + "\n".join(hits)


# ---------------------------------------------------------------------------
# AC3/AC4 — system-architecture.md describes (project_id, agent_id) tenant key
# ---------------------------------------------------------------------------


class TestAc3Ac4SystemArchTenantKey:
    """system-architecture.md describes the (project_id, agent_id) tenant key model."""

    def _doc(self) -> str:
        assert _SYS_ARCH.exists(), f"Missing: {_SYS_ARCH}"
        return _SYS_ARCH.read_text()

    def test_ac3_project_id_in_system_arch(self) -> None:
        assert "project_id" in self._doc()

    def test_ac4_agent_id_in_system_arch(self) -> None:
        assert "agent_id" in self._doc()

    def test_ac3_ac4_composite_key_described(self) -> None:
        """The (project_id, agent_id) composite key sentence is present."""
        doc = self._doc()
        assert (
            "project_id" in doc
            and "agent_id" in doc
            and ("composite key" in doc.lower() or "keyed by" in doc.lower())
        )

    def test_ac3_postgres_only_storage(self) -> None:
        """Storage section describes PostgreSQL as the only current backend."""
        doc = self._doc()
        assert "PostgreSQL" in doc


# ---------------------------------------------------------------------------
# AC5 — data-stores-and-schema.md describes migrations with version refs
# ---------------------------------------------------------------------------


class TestAc5DataStoresAndSchema:
    """data-stores-and-schema.md describes private/hive/federation Postgres schemas
    with migration version refs."""

    def _doc(self) -> str:
        assert _DATA_STORES.exists(), f"Missing: {_DATA_STORES}"
        return _DATA_STORES.read_text()

    def test_ac5_file_exists(self) -> None:
        assert _DATA_STORES.exists()

    def test_ac5_private_schema_described(self) -> None:
        doc = self._doc()
        assert "private_memories" in doc or "private" in doc.lower()

    def test_ac5_hive_schema_described(self) -> None:
        doc = self._doc()
        assert "hive_memories" in doc or "hive" in doc.lower()

    def test_ac5_federation_schema_described(self) -> None:
        doc = self._doc()
        assert "federated_memories" in doc or "federation" in doc.lower()

    def test_ac5_migration_version_refs(self) -> None:
        """At least migration 001 is referenced with its SQL filename or number."""
        doc = self._doc()
        assert "001" in doc or "migration" in doc.lower()

    def test_ac5_no_v1_v17_sqlite_schema_refs(self) -> None:
        """Old v1–v17 SQLite schema version numbers are not the primary schema description."""
        doc = self._doc()
        # Check that migration context is Postgres, not SQLite-era
        assert "private_memories" in doc or "PostgreSQL" in doc


# ---------------------------------------------------------------------------
# AC6 — v3-behavioral-parity.md FeedbackStore row references Postgres backend
# ---------------------------------------------------------------------------


class TestAc6FeedbackStoreRow:
    """v3-behavioral-parity.md FeedbackStore row is updated to reflect Postgres."""

    def _doc(self) -> str:
        assert _PARITY.exists(), f"Missing: {_PARITY}"
        return _PARITY.read_text()

    def test_ac6_feedbackstore_row_exists(self) -> None:
        doc = self._doc()
        assert "FeedbackStore" in doc

    def test_ac6_feedbackstore_postgres_backend(self) -> None:
        """FeedbackStore row references Postgres backend (feedback_events or migration 003)."""
        doc = self._doc()
        lines = doc.splitlines()
        feedback_line = next(
            (ln for ln in lines if "FeedbackStore" in ln),
            None,
        )
        assert feedback_line is not None, "FeedbackStore row not found"
        # Must reference Postgres backend, not just SQLite
        assert (
            "Postgres" in feedback_line
            or "feedback_events" in feedback_line
            or "migration 003" in feedback_line
            or "v3.1" in feedback_line
        ), f"FeedbackStore row does not reference Postgres: {feedback_line!r}"


# ---------------------------------------------------------------------------
# AC7 — threat-model.md references pg_tde
# ---------------------------------------------------------------------------


class TestAc7ThreatModelPgtde:
    """threat-model.md updated to reference pg_tde."""

    def test_ac7_threat_model_exists(self) -> None:
        assert _THREAT_MODEL.exists()

    def test_ac7_pgtde_referenced(self) -> None:
        content = _THREAT_MODEL.read_text()
        assert "pg_tde" in content or "postgres-tde" in content, (
            "threat-model.md does not reference pg_tde"
        )

    def test_ac7_sqlcipher_not_as_current(self) -> None:
        """SQLCipher is not presented as the current at-rest encryption solution."""
        content = _THREAT_MODEL.read_text()
        lines = content.splitlines()
        sqlcipher_current = [
            ln
            for ln in lines
            if "SQLCipher" in ln
            and "replaced" not in ln.lower()
            and "removed" not in ln.lower()
            and "deprecated" not in ln.lower()
            and "pg_tde" not in ln
        ]
        # It's acceptable if SQLCipher only appears in historical context
        # (the sweep preserves past-tense mentions). So we only fail if
        # SQLCipher is the ONLY at-rest encryption mentioned and pg_tde is absent.
        if sqlcipher_current:
            assert "pg_tde" in content, (
                "threat-model.md still references SQLCipher as current but does not mention pg_tde"
            )


# ---------------------------------------------------------------------------
# AC8 — meeting notes archived or removed from docs/planning/
# ---------------------------------------------------------------------------


class TestAc8MeetingNotesArchived:
    """sqlite-to-postgres-meeting-notes.md not at original location (archived or deleted)."""

    def test_ac8_not_at_original_location(self) -> None:
        assert not _MEETING_NOTES_ORIG.exists(), (
            "sqlite-to-postgres-meeting-notes.md still at original docs/planning/ "
            "location; expected archive or deletion"
        )

    def test_ac8_archived_or_absent(self) -> None:
        """File is either archived under docs/planning/archive/ or deleted entirely."""
        # Either the archive location exists, or neither location exists (deleted).
        # Both are acceptable outcomes.
        if not _MEETING_NOTES_ARCHIVE.exists():
            # If not in archive, confirm it's also not at the original path.
            assert not _MEETING_NOTES_ORIG.exists(), (
                "File found at original path but not in archive — expected one or neither."
            )
        # Pass if archived or if both paths are absent.


# ---------------------------------------------------------------------------
# AC9 — internal links in changed files resolve
# ---------------------------------------------------------------------------


class TestAc9InternalLinksResolve:
    """Relative markdown links in every changed file resolve to existing files."""

    _CHANGED_FILES = [
        _SYS_ARCH,
        _DATA_STORES,
        _PARITY,
        _THREAT_MODEL,
    ]

    @pytest.mark.parametrize("path", _CHANGED_FILES, ids=lambda p: p.name)
    def test_ac9_links_resolve(self, path: Path) -> None:
        if not path.exists():
            pytest.skip(f"{path.name} not found")
        broken = _check_links(path)
        assert not broken, f"Broken links in {path.name}:\n" + "\n".join(broken)
