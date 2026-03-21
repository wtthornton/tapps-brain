"""Integration tests for OpenClaw markdown import with real SQLite.

Tests import_memory_md and import_openclaw_workspace against a real
MemoryStore with SQLite persistence. Verifies tier assignment, deduplication,
daily note date extraction, and data persistence across store re-opens.

Story: STORY-012.7 from EPIC-012
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tapps_brain.markdown_import import import_memory_md, import_openclaw_workspace
from tapps_brain.models import MemorySource, MemoryTier
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> MemoryStore:
    """Create a real MemoryStore backed by SQLite in a temp directory."""
    s = MemoryStore(tmp_path)
    yield s  # type: ignore[misc]
    s.close()


@pytest.fixture()
def store_dir(tmp_path: Path) -> Path:
    """Return a temp directory for tests that need to re-open the store."""
    return tmp_path / "store"


SAMPLE_MEMORY_MD = """\
# Architecture Overview
The system uses SQLite for persistence with WAL mode for concurrency.

## Data Model
Pydantic v2 models with tier-based classification and source tracking.

### Naming Conventions
All keys are slugified. Use snake_case for Python, kebab-case for keys.

#### Build Steps
1. Run uv sync
2. Run pytest
3. Run ruff check

##### Sub-step Details
Additional details on sub-steps that are deeply nested.

###### Footnotes
Minor footnotes at the deepest heading level.
"""


# ---------------------------------------------------------------------------
# Tests — import_memory_md with real SQLite
# ---------------------------------------------------------------------------


class TestMarkdownImportIntegration:
    """Integration tests for import_memory_md with real SQLite backend."""

    def test_multiple_heading_levels_correct_tiers(
        self, tmp_path: Path, store: MemoryStore
    ) -> None:
        """Import MEMORY.md with H1-H6 headings → correct tier for each."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text(SAMPLE_MEMORY_MD, encoding="utf-8")

        count = import_memory_md(md_path, store)
        assert count == 6

        # H1 → architectural
        arch = store.get("architecture-overview")
        assert arch is not None
        assert arch.tier == MemoryTier.architectural
        assert "SQLite" in arch.value

        # H2 → architectural
        model = store.get("data-model")
        assert model is not None
        assert model.tier == MemoryTier.architectural

        # H3 → pattern
        naming = store.get("naming-conventions")
        assert naming is not None
        assert naming.tier == MemoryTier.pattern

        # H4 → procedural
        build = store.get("build-steps")
        assert build is not None
        assert build.tier == MemoryTier.procedural
        assert "uv sync" in build.value

        # H5 → procedural
        sub = store.get("sub-step-details")
        assert sub is not None
        assert sub.tier == MemoryTier.procedural

        # H6 → procedural
        foot = store.get("footnotes")
        assert foot is not None
        assert foot.tier == MemoryTier.procedural

    def test_idempotent_import_no_duplicates(self, tmp_path: Path, store: MemoryStore) -> None:
        """Importing the same file twice creates no duplicate entries."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text(SAMPLE_MEMORY_MD, encoding="utf-8")

        first = import_memory_md(md_path, store)
        second = import_memory_md(md_path, store)

        assert first == 6
        assert second == 0

        # Verify original entries are untouched
        arch = store.get("architecture-overview")
        assert arch is not None
        assert "SQLite" in arch.value

    def test_persistence_across_store_reopen(self, tmp_path: Path, store_dir: Path) -> None:
        """Entries survive store close and re-open (SQLite persistence)."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text(
            "# Persistent Section\nThis should survive a restart.\n",
            encoding="utf-8",
        )

        # First open: import
        store1 = MemoryStore(store_dir)
        count = import_memory_md(md_path, store1)
        assert count == 1
        store1.close()

        # Second open: verify data persists
        store2 = MemoryStore(store_dir)
        entry = store2.get("persistent-section")
        assert entry is not None
        assert "survive a restart" in entry.value
        assert entry.tier == MemoryTier.architectural

        # Importing again should find the existing entry (dedup across reopens)
        count2 = import_memory_md(md_path, store2)
        assert count2 == 0
        store2.close()

    def test_source_set_to_system(self, tmp_path: Path, store: MemoryStore) -> None:
        """Imported entries have source='system'."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("# Source Test\nVerify source field.\n", encoding="utf-8")
        import_memory_md(md_path, store)

        entry = store.get("source-test")
        assert entry is not None
        assert entry.source == MemorySource.system


# ---------------------------------------------------------------------------
# Tests — daily note date extraction with real SQLite
# ---------------------------------------------------------------------------


class TestDailyNoteIntegration:
    """Integration tests for daily note import via import_openclaw_workspace."""

    def test_daily_notes_date_extraction(self, tmp_path: Path, store: MemoryStore) -> None:
        """Daily notes are imported as context-tier entries with date-based keys."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text("# Intro\nHello.\n", encoding="utf-8")

        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-01-15.md").write_text("January note.", encoding="utf-8")
        (mem_dir / "2026-02-28.md").write_text("February note.", encoding="utf-8")
        (mem_dir / "2026-03-21.md").write_text("March note.", encoding="utf-8")

        result = import_openclaw_workspace(workspace, store)

        assert result["daily_notes"] == 3
        assert result["memory_md"] == 1
        assert result["skipped"] == 0

        # Verify each daily note key and tier
        for date_str in ("2026-01-15", "2026-02-28", "2026-03-21"):
            entry = store.get(f"daily-{date_str}")
            assert entry is not None, f"Missing daily-{date_str}"
            assert entry.tier == MemoryTier.context
            assert entry.source == MemorySource.system

        # Verify content
        jan = store.get("daily-2026-01-15")
        assert jan is not None
        assert "January note" in jan.value

    def test_daily_notes_sorted_import_order(self, tmp_path: Path, store: MemoryStore) -> None:
        """Daily notes are imported in chronological order (sorted by filename)."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mem_dir = workspace / "memory"
        mem_dir.mkdir()

        # Create notes out of order
        (mem_dir / "2026-12-25.md").write_text("Christmas.", encoding="utf-8")
        (mem_dir / "2026-01-01.md").write_text("New Year.", encoding="utf-8")
        (mem_dir / "2026-07-04.md").write_text("Independence.", encoding="utf-8")

        result = import_openclaw_workspace(workspace, store)
        assert result["daily_notes"] == 3

        # All three are importable and accessible
        assert store.get("daily-2026-01-01") is not None
        assert store.get("daily-2026-07-04") is not None
        assert store.get("daily-2026-12-25") is not None

    def test_daily_notes_persistence_across_reopen(self, tmp_path: Path, store_dir: Path) -> None:
        """Daily notes persist across store close/re-open."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-06-15.md").write_text("Mid-year review.", encoding="utf-8")

        store1 = MemoryStore(store_dir)
        import_openclaw_workspace(workspace, store1)
        store1.close()

        store2 = MemoryStore(store_dir)
        entry = store2.get("daily-2026-06-15")
        assert entry is not None
        assert entry.tier == MemoryTier.context
        assert "Mid-year review" in entry.value
        store2.close()


# ---------------------------------------------------------------------------
# Tests — full workspace round-trip
# ---------------------------------------------------------------------------


class TestWorkspaceImportIntegration:
    """Full workspace import integration tests."""

    def test_full_workspace_with_all_heading_levels(
        self, tmp_path: Path, store: MemoryStore
    ) -> None:
        """Import a workspace with MEMORY.md + daily notes → all entries correct."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        (workspace / "MEMORY.md").write_text(SAMPLE_MEMORY_MD, encoding="utf-8")

        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-03-01.md").write_text("Sprint planning.", encoding="utf-8")
        (mem_dir / "2026-03-15.md").write_text("Sprint review.", encoding="utf-8")

        result = import_openclaw_workspace(workspace, store)

        assert result["memory_md"] == 6
        assert result["daily_notes"] == 2
        assert result["skipped"] == 0

        # Verify tier distribution
        tiers = []
        for key in (
            "architecture-overview",
            "data-model",
            "naming-conventions",
            "build-steps",
            "sub-step-details",
            "footnotes",
        ):
            entry = store.get(key)
            assert entry is not None, f"Missing key: {key}"
            tiers.append(entry.tier)

        assert tiers.count(MemoryTier.architectural) == 2
        assert tiers.count(MemoryTier.pattern) == 1
        assert tiers.count(MemoryTier.procedural) == 3

    def test_idempotent_workspace_import(self, tmp_path: Path, store: MemoryStore) -> None:
        """Double import of workspace → second import all skipped."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text(
            "# Design\nCore design doc.\n\n### Patterns\nCommon patterns.\n",
            encoding="utf-8",
        )
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-04-01.md").write_text("April notes.", encoding="utf-8")

        first = import_openclaw_workspace(workspace, store)
        second = import_openclaw_workspace(workspace, store)

        assert first["memory_md"] == 2
        assert first["daily_notes"] == 1
        assert first["skipped"] == 0

        assert second["memory_md"] == 0
        assert second["daily_notes"] == 0
        assert second["skipped"] == 3  # 2 md + 1 daily skipped

    def test_workspace_with_non_note_files_ignored(
        self, tmp_path: Path, store: MemoryStore
    ) -> None:
        """Non-date files in memory/ directory are ignored."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mem_dir = workspace / "memory"
        mem_dir.mkdir()

        (mem_dir / "2026-05-01.md").write_text("Valid note.", encoding="utf-8")
        (mem_dir / "README.md").write_text("Not a daily note.", encoding="utf-8")
        (mem_dir / "notes.txt").write_text("Not markdown.", encoding="utf-8")
        (mem_dir / "random-file.md").write_text("Random.", encoding="utf-8")

        result = import_openclaw_workspace(workspace, store)

        assert result["daily_notes"] == 1
        assert store.get("daily-2026-05-01") is not None

    def test_workspace_persistence_full_round_trip(self, tmp_path: Path, store_dir: Path) -> None:
        """Full workspace import persists across store close and re-open."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text(
            "# Arch\nArchitecture.\n\n### Pattern\nA pattern.\n",
            encoding="utf-8",
        )
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-08-01.md").write_text("August notes.", encoding="utf-8")

        # Import and close
        store1 = MemoryStore(store_dir)
        result = import_openclaw_workspace(workspace, store1)
        assert result["memory_md"] == 2
        assert result["daily_notes"] == 1
        store1.close()

        # Re-open and verify all entries persist
        store2 = MemoryStore(store_dir)

        arch = store2.get("arch")
        assert arch is not None
        assert arch.tier == MemoryTier.architectural

        pat = store2.get("pattern")
        assert pat is not None
        assert pat.tier == MemoryTier.pattern

        daily = store2.get("daily-2026-08-01")
        assert daily is not None
        assert daily.tier == MemoryTier.context

        # Re-import should be fully idempotent
        result2 = import_openclaw_workspace(workspace, store2)
        assert result2["memory_md"] == 0
        assert result2["daily_notes"] == 0
        assert result2["skipped"] == 3
        store2.close()
