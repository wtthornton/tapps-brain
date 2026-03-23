"""Unit tests for tapps_brain.markdown_import.

Covers: heading-level → tier mapping, slugification, deduplication,
daily note date extraction, edge cases (empty files, malformed markdown,
missing MEMORY.md), and workspace import.

Part of EPIC-012, story 012.1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tapps_brain.markdown_import import (
    _import_daily_note,
    _parse_sections,
    _slugify,
    _tier_from_level,
    import_memory_md,
    import_openclaw_workspace,
)
from tapps_brain.models import MAX_KEY_LENGTH, MAX_VALUE_LENGTH, MemoryTier
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_characters_replaced(self):
        assert _slugify("API Keys & Tokens!") == "api-keys-tokens"

    def test_collapses_separators(self):
        assert _slugify("foo---bar___baz") == "foo-bar-baz"

    def test_strips_leading_trailing_separators(self):
        assert _slugify("--hello--") == "hello"

    def test_non_alphanumeric_start_prefix(self):
        # Dots are stripped as separators, leaving "only-dots" (alphanumeric start)
        assert _slugify("...only dots") == "only-dots"

    def test_empty_string_gets_prefix(self):
        # Empty input cannot start with alphanumeric → "m-" prefix
        assert _slugify("") == "m-"

    def test_truncation(self):
        long_text = "a" * 300
        result = _slugify(long_text)
        assert len(result) <= MAX_KEY_LENGTH


# ---------------------------------------------------------------------------
# _tier_from_level
# ---------------------------------------------------------------------------


class TestTierFromLevel:
    def test_h1_architectural(self):
        assert _tier_from_level(1) == MemoryTier.architectural

    def test_h2_architectural(self):
        assert _tier_from_level(2) == MemoryTier.architectural

    def test_h3_pattern(self):
        assert _tier_from_level(3) == MemoryTier.pattern

    def test_h4_procedural(self):
        assert _tier_from_level(4) == MemoryTier.procedural

    def test_h5_procedural(self):
        assert _tier_from_level(5) == MemoryTier.procedural

    def test_h6_procedural(self):
        assert _tier_from_level(6) == MemoryTier.procedural


# ---------------------------------------------------------------------------
# _parse_sections
# ---------------------------------------------------------------------------


class TestParseSections:
    def test_multiple_heading_levels(self):
        md = (
            "# Architecture\n"
            "Top-level design.\n\n"
            "## Modules\n"
            "Module overview.\n\n"
            "### Patterns\n"
            "Common patterns.\n\n"
            "#### Steps\n"
            "Step-by-step guide.\n"
        )
        sections = _parse_sections(md)
        assert len(sections) == 4

        keys = [s[0] for s in sections]
        tiers = [s[2] for s in sections]

        assert keys == ["architecture", "modules", "patterns", "steps"]
        assert tiers == [
            MemoryTier.architectural,
            MemoryTier.architectural,
            MemoryTier.pattern,
            MemoryTier.procedural,
        ]

    def test_heading_without_body_skipped(self):
        md = "# Empty Section\n\n# Has Body\nSome content.\n"
        sections = _parse_sections(md)
        assert len(sections) == 1
        assert sections[0][0] == "has-body"

    def test_empty_text(self):
        assert _parse_sections("") == []

    def test_no_headings(self):
        assert _parse_sections("Just some text\nwith no headings.") == []

    def test_body_multiline(self):
        md = "# Title\nLine one.\nLine two.\nLine three.\n"
        sections = _parse_sections(md)
        assert len(sections) == 1
        assert "Line one." in sections[0][1]
        assert "Line three." in sections[0][1]


# ---------------------------------------------------------------------------
# import_memory_md
# ---------------------------------------------------------------------------


class TestImportMemoryMd:
    def test_import_headings_correct_tiers(self, tmp_path: Path):
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text(
            "# Architecture\nSystem design.\n\n"
            "## Components\nComponent list.\n\n"
            "### Naming Patterns\nUse snake_case.\n\n"
            "#### Build Steps\nRun make.\n",
            encoding="utf-8",
        )
        store = MemoryStore(tmp_path / "store")
        count = import_memory_md(md_path, store)
        assert count == 4

        arch = store.get("architecture")
        assert arch is not None
        assert arch.tier == MemoryTier.architectural

        comp = store.get("components")
        assert comp is not None
        assert comp.tier == MemoryTier.architectural

        pat = store.get("naming-patterns")
        assert pat is not None
        assert pat.tier == MemoryTier.pattern

        steps = store.get("build-steps")
        assert steps is not None
        assert steps.tier == MemoryTier.procedural

    def test_import_twice_no_duplicates(self, tmp_path: Path):
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text("# Intro\nHello.\n", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")

        first = import_memory_md(md_path, store)
        second = import_memory_md(md_path, store)
        assert first == 1
        assert second == 0

    def test_missing_file_returns_zero(self, tmp_path: Path):
        store = MemoryStore(tmp_path / "store")
        count = import_memory_md(tmp_path / "nonexistent.md", store)
        assert count == 0

    def test_empty_file(self, tmp_path: Path):
        md_path = tmp_path / "empty.md"
        md_path.write_text("", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")
        count = import_memory_md(md_path, store)
        assert count == 0

    def test_value_truncation(self, tmp_path: Path):
        long_body = "x" * (MAX_VALUE_LENGTH + 500)
        md_path = tmp_path / "MEMORY.md"
        md_path.write_text(f"# Big Section\n{long_body}\n", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")
        count = import_memory_md(md_path, store)
        assert count == 1
        entry = store.get("big-section")
        assert entry is not None
        assert len(entry.value) <= MAX_VALUE_LENGTH


# ---------------------------------------------------------------------------
# _import_daily_note
# ---------------------------------------------------------------------------


class TestImportDailyNote:
    def test_import_valid_daily_note(self, tmp_path: Path):
        note = tmp_path / "2026-03-15.md"
        note.write_text("Today I learned about BM25.", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")

        result = _import_daily_note(note, store)
        assert result is True
        entry = store.get("daily-2026-03-15")
        assert entry is not None
        assert entry.tier == MemoryTier.context
        assert "BM25" in entry.value

    def test_duplicate_daily_note_skipped(self, tmp_path: Path):
        note = tmp_path / "2026-01-01.md"
        note.write_text("New year.", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")

        assert _import_daily_note(note, store) is True
        assert _import_daily_note(note, store) is False

    def test_empty_daily_note_skipped(self, tmp_path: Path):
        note = tmp_path / "2026-06-01.md"
        note.write_text("", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")
        assert _import_daily_note(note, store) is False

    def test_bad_filename_skipped(self, tmp_path: Path):
        note = tmp_path / "not-a-date.md"
        note.write_text("Some text.", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")
        assert _import_daily_note(note, store) is False


# ---------------------------------------------------------------------------
# import_openclaw_workspace
# ---------------------------------------------------------------------------


class TestImportOpenclawWorkspace:
    def test_full_workspace(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        # MEMORY.md with two sections
        (workspace / "MEMORY.md").write_text(
            "# Project\nOverview.\n\n### Conventions\nUse ruff.\n",
            encoding="utf-8",
        )

        # Daily notes
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-03-01.md").write_text("March notes.", encoding="utf-8")
        (mem_dir / "2026-03-02.md").write_text("More notes.", encoding="utf-8")

        store = MemoryStore(tmp_path / "store")
        result = import_openclaw_workspace(workspace, store)

        assert result["memory_md"] == 2
        assert result["daily_notes"] == 2
        assert result["skipped"] == 0

    def test_missing_memory_md(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        store = MemoryStore(tmp_path / "store")
        result = import_openclaw_workspace(workspace, store)
        assert result["memory_md"] == 0

    def test_missing_memory_dir(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text("# Intro\nHello.\n", encoding="utf-8")
        store = MemoryStore(tmp_path / "store")
        result = import_openclaw_workspace(workspace, store)
        assert result["memory_md"] == 1
        assert result["daily_notes"] == 0

    def test_idempotent_workspace_import(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text("# Intro\nHello.\n", encoding="utf-8")
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-01-01.md").write_text("Note.", encoding="utf-8")

        store = MemoryStore(tmp_path / "store")

        first = import_openclaw_workspace(workspace, store)
        second = import_openclaw_workspace(workspace, store)

        assert first["memory_md"] == 1
        assert first["daily_notes"] == 1
        assert first["skipped"] == 0

        assert second["memory_md"] == 0
        assert second["daily_notes"] == 0
        assert second["skipped"] == 2  # both skipped as duplicates

    def test_malformed_markdown(self, tmp_path: Path):
        """Malformed markdown (no proper headings) imports zero entries."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "MEMORY.md").write_text(
            "No headings here.\nJust plain text.\n",
            encoding="utf-8",
        )
        store = MemoryStore(tmp_path / "store")
        result = import_openclaw_workspace(workspace, store)
        assert result["memory_md"] == 0

    def test_encoding_error_memory_md(self, tmp_path: Path):
        """Non-UTF-8 MEMORY.md is skipped gracefully, returning zero."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        # Write Latin-1 bytes that are invalid UTF-8
        (workspace / "MEMORY.md").write_bytes(b"# Title\n\xc0\xc1 invalid utf-8\n")
        store = MemoryStore(tmp_path / "store")
        result = import_openclaw_workspace(workspace, store)
        assert result["memory_md"] == 0

    def test_encoding_error_daily_note(self, tmp_path: Path):
        """Non-UTF-8 daily note is skipped gracefully."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-03-10.md").write_bytes(b"\xc0\xc1 invalid utf-8")
        store = MemoryStore(tmp_path / "store")
        result = import_openclaw_workspace(workspace, store)
        assert result["daily_notes"] == 0


# ---------------------------------------------------------------------------
# Additional edge case tests
# ---------------------------------------------------------------------------


class TestImportMemoryMdEncodingError:
    def test_non_utf8_file_returns_zero(self, tmp_path: Path):
        """import_memory_md returns 0 if file has non-UTF-8 encoding."""
        md_path = tmp_path / "MEMORY.md"
        md_path.write_bytes(b"# Title\n\xc0\xc1 bad bytes\n")
        store = MemoryStore(tmp_path / "store")
        count = import_memory_md(md_path, store)
        assert count == 0


class TestImportDailyNoteEncodingError:
    def test_non_utf8_daily_note_returns_false(self, tmp_path: Path):
        """_import_daily_note returns False if file has non-UTF-8 encoding."""
        note = tmp_path / "2026-03-15.md"
        note.write_bytes(b"\xc0\xc1 invalid utf-8")
        store = MemoryStore(tmp_path / "store")
        result = _import_daily_note(note, store)
        assert result is False
