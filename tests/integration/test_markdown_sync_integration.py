"""Integration tests for bidirectional MEMORY.md sync (EPIC-026, story-026.4).

Tests the round-trip: save entries to store → sync_to_markdown → edit file →
sync_from_markdown → verify store state.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from tapps_brain.markdown_sync import (
    MEMORY_MD_SCHEMA_VERSION,
    _parse_memory_md_sections,
    get_sync_state,
    sync_from_markdown,
    sync_to_markdown,
)
from tapps_brain.store import MemoryStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_store(tmp_path: Path) -> MemoryStore:
    """A clean in-memory MemoryStore (no Postgres backend required)."""
    store = MemoryStore(tmp_path / "store")
    return store


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A clean workspace directory (simulates an OpenClaw workspace)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


# ---------------------------------------------------------------------------
# sync_to_markdown tests
# ---------------------------------------------------------------------------


class TestSyncToMarkdown:
    def test_empty_store_creates_empty_memory_md(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        result = sync_to_markdown(tmp_store, workspace)
        assert result["exported"] == 0
        memory_md = workspace / "MEMORY.md"
        assert memory_md.is_file()
        content = memory_md.read_text(encoding="utf-8")
        # File should exist but contain only the header
        assert "# Memory" in content

    def test_exports_entries_by_tier(self, tmp_store: MemoryStore, workspace: Path) -> None:
        tmp_store.save("arch-key", "Architecture decision.", tier="architectural")
        tmp_store.save("pat-key", "Coding pattern.", tier="pattern")
        tmp_store.save("proc-key", "Step-by-step procedure.", tier="procedural")
        tmp_store.save("ctx-key", "Session context.", tier="context")

        result = sync_to_markdown(tmp_store, workspace)
        assert result["exported"] == 4

        content = (workspace / "MEMORY.md").read_text(encoding="utf-8")
        # Tier heading prefixes
        assert "## arch-key" in content
        assert "### pat-key" in content
        assert "#### proc-key" in content
        assert "##### ctx-key" in content
        # Values
        assert "Architecture decision." in content
        assert "Coding pattern." in content
        assert "Step-by-step procedure." in content
        assert "Session context." in content

    def test_tier_order_is_arch_pattern_procedural_context(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        tmp_store.save("ctx", "ctx value", tier="context")
        tmp_store.save("arch", "arch value", tier="architectural")

        sync_to_markdown(tmp_store, workspace)
        content = (workspace / "MEMORY.md").read_text(encoding="utf-8")

        arch_pos = content.index("## arch")
        ctx_pos = content.index("##### ctx")
        assert arch_pos < ctx_pos, "architectural should appear before context"

    def test_entries_sorted_by_key_within_tier(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        tmp_store.save("z-last", "last", tier="pattern")
        tmp_store.save("a-first", "first", tier="pattern")
        tmp_store.save("m-middle", "middle", tier="pattern")

        sync_to_markdown(tmp_store, workspace)
        content = (workspace / "MEMORY.md").read_text(encoding="utf-8")

        pos_a = content.index("### a-first")
        pos_m = content.index("### m-middle")
        pos_z = content.index("### z-last")
        assert pos_a < pos_m < pos_z

    def test_updates_sync_state_last_sync_to(self, tmp_store: MemoryStore, workspace: Path) -> None:
        tmp_store.save("k", "v", tier="pattern")
        sync_to_markdown(tmp_store, workspace)

        state = get_sync_state(workspace)
        assert "last_sync_to" in state
        # Should be a valid ISO-8601 timestamp
        from datetime import datetime

        dt = datetime.fromisoformat(state["last_sync_to"])
        assert dt.tzinfo is not None

    def test_returns_path_to_memory_md(self, tmp_store: MemoryStore, workspace: Path) -> None:
        result = sync_to_markdown(tmp_store, workspace)
        expected = str(workspace / "MEMORY.md")
        assert result["path"] == expected

    def test_memory_md_contains_schema_version_front_matter(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Exported MEMORY.md starts with YAML front matter containing schema_version."""
        tmp_store.save("k", "v", tier="pattern")
        sync_to_markdown(tmp_store, workspace)
        content = (workspace / "MEMORY.md").read_text(encoding="utf-8")
        assert content.startswith("---\n"), "MEMORY.md must start with YAML front matter"
        assert f"schema_version: {MEMORY_MD_SCHEMA_VERSION}" in content

    def test_superseded_entries_excluded(self, tmp_store: MemoryStore, workspace: Path) -> None:
        tmp_store.save("active", "active value", tier="pattern")
        # Save entry then supersede it
        tmp_store.save("old-entry", "old value", tier="pattern")
        # Manually supersede by saving with same key (update)
        tmp_store.save("new-entry", "new value", tier="pattern")
        # Supersede old-entry
        entry = tmp_store.get("old-entry")
        if entry is not None:
            tmp_store.update_fields("old-entry", superseded_by="new-entry")

        sync_to_markdown(tmp_store, workspace)
        # superseded entries excluded from export
        content = (workspace / "MEMORY.md").read_text(encoding="utf-8")
        assert "### active" in content


# ---------------------------------------------------------------------------
# sync_from_markdown tests
# ---------------------------------------------------------------------------


class TestSyncFromMarkdown:
    def test_no_memory_md_returns_zeros(self, tmp_store: MemoryStore, workspace: Path) -> None:
        result = sync_from_markdown(tmp_store, workspace)
        assert result["imported"] == 0
        assert result["skipped"] == 0

    def test_imports_new_entries_from_memory_md(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n## my-arch\n\nArchitectural fact.\n\n### my-pattern\n\nPattern note.\n",
            encoding="utf-8",
        )

        result = sync_from_markdown(tmp_store, workspace)
        assert result["imported"] == 2
        assert result["memory_md"] == 2

        arch_entry = tmp_store.get("my-arch")
        assert arch_entry is not None
        assert "Architectural fact." in arch_entry.value
        assert str(arch_entry.tier) == "architectural"

        pat_entry = tmp_store.get("my-pattern")
        assert pat_entry is not None
        assert str(pat_entry.tier) == "pattern"

    def test_skips_existing_entries_tapps_brain_wins(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        tmp_store.save("existing-key", "ORIGINAL value", tier="pattern")

        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n### existing-key\n\nOVERWRITTEN value.\n",
            encoding="utf-8",
        )

        result = sync_from_markdown(tmp_store, workspace)
        assert result["skipped"] == 1
        assert result["imported"] == 0

        entry = tmp_store.get("existing-key")
        assert entry is not None
        assert entry.value == "ORIGINAL value"  # tapps-brain won

    def test_updates_sync_state_last_sync_from(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n### k\n\nsome value.\n",
            encoding="utf-8",
        )
        sync_from_markdown(tmp_store, workspace)

        state = get_sync_state(workspace)
        assert "last_sync_from" in state

    def test_imports_daily_notes(self, tmp_store: MemoryStore, workspace: Path) -> None:
        memory_dir = workspace / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-03-01.md").write_text(
            "Daily note content for March 1.", encoding="utf-8"
        )
        (memory_dir / "2026-03-02.md").write_text(
            "Daily note content for March 2.", encoding="utf-8"
        )

        result = sync_from_markdown(tmp_store, workspace)
        assert result["daily_notes"] == 2
        assert result["imported"] == 2

        entry = tmp_store.get("daily-2026-03-01")
        assert entry is not None
        assert "March 1" in entry.value
        assert str(entry.tier) == "context"

    def test_skips_existing_daily_notes(self, tmp_store: MemoryStore, workspace: Path) -> None:
        tmp_store.save("daily-2026-03-01", "original note", tier="context")

        memory_dir = workspace / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-03-01.md").write_text("overwritten note", encoding="utf-8")

        result = sync_from_markdown(tmp_store, workspace)
        assert result["skipped"] == 1
        assert result["daily_notes"] == 0

        entry = tmp_store.get("daily-2026-03-01")
        assert entry is not None
        assert entry.value == "original note"  # tapps-brain won

    def test_ignores_non_date_files_in_memory_dir(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        memory_dir = workspace / "memory"
        memory_dir.mkdir()
        (memory_dir / "notes.md").write_text("not a daily note", encoding="utf-8")
        (memory_dir / "README.txt").write_text("readme", encoding="utf-8")
        (memory_dir / "2026-03-01.md").write_text("valid daily note", encoding="utf-8")

        result = sync_from_markdown(tmp_store, workspace)
        assert result["daily_notes"] == 1
        assert tmp_store.get("daily-2026-03-01") is not None
        assert tmp_store.get("notes") is None

    def test_html_comment_lines_excluded_from_values(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        (workspace / "MEMORY.md").write_text(
            "# Memory\n\n"
            "<!-- Generated by tapps-brain sync. Edit freely; tapps-brain wins. -->\n\n"
            "### pat-key\n\nReal value here.\n",
            encoding="utf-8",
        )

        sync_from_markdown(tmp_store, workspace)
        entry = tmp_store.get("pat-key")
        assert entry is not None
        assert "<!--" not in entry.value
        assert "Real value here." in entry.value

    def test_encoding_error_memory_md_skipped_gracefully(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        # Write a file with invalid UTF-8 bytes
        (workspace / "MEMORY.md").write_bytes(b"# Memory\n\n### key\n\n\xff\xfe bad\n")

        result = sync_from_markdown(tmp_store, workspace)
        # Should not raise; returns zero imports
        assert result["imported"] == 0


# ---------------------------------------------------------------------------
# Round-trip test (the core integration scenario)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_save_export_edit_import_round_trip(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Full round-trip: save → export → edit → import."""
        # Step 1: populate the store
        tmp_store.save("arch-decision", "Use SQLite WAL mode.", tier="architectural")
        tmp_store.save("coding-pattern", "Use type hints everywhere.", tier="pattern")

        # Step 2: export to MEMORY.md
        export_result = sync_to_markdown(tmp_store, workspace)
        assert export_result["exported"] == 2
        memory_md = workspace / "MEMORY.md"
        assert memory_md.is_file()

        # Step 3: a human edits MEMORY.md, adding a new entry
        content = memory_md.read_text(encoding="utf-8")
        content += "\n### new-pattern\n\nAlways write tests first.\n"
        memory_md.write_text(content, encoding="utf-8")

        # Step 4: import — new entry should be added; existing ones skipped
        import_result = sync_from_markdown(tmp_store, workspace)
        assert import_result["memory_md"] == 1  # only the new one
        assert import_result["skipped"] == 2  # the two existing ones

        new_entry = tmp_store.get("new-pattern")
        assert new_entry is not None
        assert "Always write tests first." in new_entry.value
        assert str(new_entry.tier) == "pattern"

        # Original entries untouched
        arch = tmp_store.get("arch-decision")
        assert arch is not None
        assert arch.value == "Use SQLite WAL mode."

    def test_round_trip_preserves_tier_for_all_tiers(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Tier round-trips correctly for all four tiers."""
        tmp_store.save("k-arch", "arch value", tier="architectural")
        tmp_store.save("k-pat", "pattern value", tier="pattern")
        tmp_store.save("k-proc", "procedural value", tier="procedural")
        tmp_store.save("k-ctx", "context value", tier="context")

        # Export
        sync_to_markdown(tmp_store, workspace)

        # Create a fresh store and import
        store2 = MemoryStore(workspace / "store2")
        sync_from_markdown(store2, workspace)

        assert str(store2.get("k-arch").tier) == "architectural"  # type: ignore[union-attr]
        assert str(store2.get("k-pat").tier) == "pattern"  # type: ignore[union-attr]
        assert str(store2.get("k-proc").tier) == "procedural"  # type: ignore[union-attr]
        assert str(store2.get("k-ctx").tier) == "context"  # type: ignore[union-attr]

    def test_repeated_export_is_idempotent(self, tmp_store: MemoryStore, workspace: Path) -> None:
        """Exporting twice does not duplicate entries on re-import."""
        tmp_store.save("idempotent-key", "stable value", tier="pattern")

        sync_to_markdown(tmp_store, workspace)
        sync_to_markdown(tmp_store, workspace)

        store2 = MemoryStore(workspace / "store2")
        result = sync_from_markdown(store2, workspace)
        assert result["imported"] == 1  # Not 2

    def test_lossless_round_trip_key_value_tier(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Round-trip is lossless for the (key, value, tier) subset of fields."""
        entries = [
            ("rt-arch", "Architectural constant.", "architectural"),
            ("rt-pat", "Pattern note.", "pattern"),
            ("rt-proc", "Procedural step.", "procedural"),
            ("rt-ctx", "Session context.", "context"),
        ]
        for key, value, tier in entries:
            tmp_store.save(key, value, tier=tier)

        sync_to_markdown(tmp_store, workspace)

        store2 = MemoryStore(workspace / "store2")
        sync_from_markdown(store2, workspace)

        for key, value, tier in entries:
            entry = store2.get(key)
            assert entry is not None, f"key {key!r} missing after round-trip"
            assert entry.value == value, f"value mismatch for {key!r}"
            assert str(entry.tier) == tier, f"tier mismatch for {key!r}"

    def test_state_file_tracks_both_timestamps(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Sync state JSON records both last_sync_to and last_sync_from."""
        tmp_store.save("k", "v", tier="pattern")
        sync_to_markdown(tmp_store, workspace)
        sync_from_markdown(tmp_store, workspace)

        state_path = workspace / ".tapps-brain" / "sync_state.json"
        assert state_path.is_file()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_sync_to" in state
        assert "last_sync_from" in state
        assert state.get("version") == 1


# ---------------------------------------------------------------------------
# _parse_memory_md_sections unit tests
# ---------------------------------------------------------------------------


class TestParseMemoryMdSections:
    def test_h1_title_skipped(self) -> None:
        text = "# Memory\n\n## arch-key\n\nArch value.\n"
        sections = _parse_memory_md_sections(text)
        keys = [s[0] for s in sections]
        assert "memory" not in keys
        assert "arch-key" in keys

    def test_heading_levels_map_to_correct_tiers(self) -> None:
        text = (
            "## arch\n\narch value\n"
            "### pat\n\npat value\n"
            "#### proc\n\nproc value\n"
            "##### ctx\n\nctx value\n"
        )
        sections = _parse_memory_md_sections(text)
        tier_map = {key: tier for key, _, tier in sections}
        assert tier_map["arch"] == "architectural"
        assert tier_map["pat"] == "pattern"
        assert tier_map["proc"] == "procedural"
        assert tier_map["ctx"] == "context"

    def test_html_comments_excluded_from_body(self) -> None:
        text = "<!-- sync metadata -->\n## k\n\n<!-- another comment -->\nReal content.\n"
        sections = _parse_memory_md_sections(text)
        assert len(sections) == 1
        key, value, _ = sections[0]
        assert key == "k"
        assert "<!--" not in value
        assert "Real content." in value

    def test_empty_body_section_excluded(self) -> None:
        text = "## k\n\n## k2\n\nhas content\n"
        sections = _parse_memory_md_sections(text)
        keys = [s[0] for s in sections]
        assert "k" not in keys
        assert "k2" in keys

    def test_multiline_body_preserved(self) -> None:
        text = "## k\n\nLine one.\nLine two.\n\nLine three.\n"
        sections = _parse_memory_md_sections(text)
        assert len(sections) == 1
        _, value, _ = sections[0]
        assert "Line one." in value
        assert "Line two." in value
        assert "Line three." in value

    def test_slugify_applied_to_heading_text(self) -> None:
        text = "## My Arch Key!\n\nsome value\n"
        sections = _parse_memory_md_sections(text)
        assert len(sections) == 1
        key, _, _ = sections[0]
        assert key == "my-arch-key"

    def test_yaml_front_matter_skipped(self) -> None:
        text = "---\nschema_version: 1\n---\n\n## arch-key\n\nArch value.\n"
        sections = _parse_memory_md_sections(text)
        keys = [s[0] for s in sections]
        assert "arch-key" in keys
        # Front matter fields must not leak into sections
        assert "schema-version" not in keys
        assert "schema_version" not in [s[0] for s in sections]

    def test_yaml_front_matter_content_excluded_from_values(self) -> None:
        text = "---\nschema_version: 1\ngenerated_by: tapps-brain\n---\n\n## k\n\nreal value\n"
        sections = _parse_memory_md_sections(text)
        assert len(sections) == 1
        _, value, _ = sections[0]
        assert "schema_version" not in value
        assert "real value" in value


# ---------------------------------------------------------------------------
# Slug collision tests (TAP-718)
# ---------------------------------------------------------------------------


class TestSlugCollision:
    """_parse_memory_md_sections must survive slug collisions without silently
    dropping entries.  Two headings that slugify to the same key (e.g. "Foo Bar"
    and "foo-bar") must both appear in the parsed output with distinct keys, and
    a warning must be emitted so the user can fix the MEMORY.md."""

    def _expected_disambiguated_key(self, base_slug: str, heading_text: str) -> str:
        """Reproduce the deterministic suffix used by _resolve_slug_collision."""
        suffix = hashlib.sha256(heading_text.encode()).hexdigest()[:6]
        max_base = 121  # MAX_KEY_LENGTH(128) - 7
        return f"{base_slug[:max_base]}-{suffix}"

    def test_both_sections_survive(self) -> None:
        """Two headings that collapse to the same slug both produce entries."""
        # "Foo Bar" and "foo-bar" both slugify to "foo-bar"
        text = "## Foo Bar\n\nFirst section.\n\n## foo-bar\n\nSecond section.\n"
        sections = _parse_memory_md_sections(text)
        assert len(sections) == 2, (
            "Both sections must survive; the second must not be silently dropped."
        )

    def test_first_section_keeps_original_slug(self) -> None:
        """The first occurrence retains the unmodified slug."""
        text = "## Foo Bar\n\nFirst.\n\n## foo-bar\n\nSecond.\n"
        sections = _parse_memory_md_sections(text)
        keys = [s[0] for s in sections]
        assert "foo-bar" in keys, "First occurrence must keep its original slug."

    def test_second_section_gets_disambiguated_key(self) -> None:
        """The second occurrence receives a deterministic -<hash> suffix."""
        text = "## Foo Bar\n\nFirst.\n\n## foo-bar\n\nSecond.\n"
        sections = _parse_memory_md_sections(text)
        keys = [s[0] for s in sections]
        # The second heading text is "foo-bar"; its slug is also "foo-bar".
        expected_key = self._expected_disambiguated_key("foo-bar", "foo-bar")
        assert expected_key in keys, (
            f"Second occurrence should have key '{expected_key}' but got {keys!r}."
        )

    def test_values_are_preserved_after_disambiguation(self) -> None:
        """Each section's body text is preserved unchanged after disambiguation."""
        text = "## Foo Bar\n\nFirst section body.\n\n## foo-bar\n\nSecond section body.\n"
        sections = _parse_memory_md_sections(text)
        values = {k: v for k, v, _ in sections}
        assert "First section body." in next(
            v for k, v in values.items() if k == "foo-bar"
        )
        second_key = self._expected_disambiguated_key("foo-bar", "foo-bar")
        assert "Second section body." in values[second_key]

    def test_warning_logged_on_collision(self) -> None:
        """A structlog warning is emitted when a slug collision is detected."""
        import structlog
        from structlog.testing import capture_logs

        saved = structlog.get_config()
        structlog.reset_defaults()
        try:
            text = "## Foo Bar\n\nFirst.\n\n## foo-bar\n\nSecond.\n"
            with capture_logs() as log_entries:
                _parse_memory_md_sections(text)
            assert any(
                entry.get("event") == "markdown_sync.slug_collision"
                and entry.get("log_level") == "warning"
                for entry in log_entries
            ), f"Expected a slug_collision warning but got: {log_entries!r}"
        finally:
            structlog.configure(**saved)

    def test_no_collision_no_warning(self) -> None:
        """No warning is emitted when all headings produce distinct slugs."""
        import structlog
        from structlog.testing import capture_logs

        saved = structlog.get_config()
        structlog.reset_defaults()
        try:
            text = "## Alpha\n\nalpha value\n\n## Beta\n\nbeta value\n"
            with capture_logs() as log_entries:
                _parse_memory_md_sections(text)
            assert not any(
                entry.get("event") == "markdown_sync.slug_collision"
                for entry in log_entries
            ), f"Unexpected slug_collision warning: {log_entries!r}"
        finally:
            structlog.configure(**saved)

    def test_three_way_collision_all_survive(self) -> None:
        """Three headings colliding on the same slug all produce distinct keys."""
        text = (
            "## Foo Bar\n\nBody 1.\n\n"
            "## foo-bar\n\nBody 2.\n\n"
            "## FOO BAR\n\nBody 3.\n"
        )
        sections = _parse_memory_md_sections(text)
        assert len(sections) == 3, "All three colliding sections must survive."
        keys = [s[0] for s in sections]
        # All keys must be distinct
        assert len(set(keys)) == 3, f"Keys must be distinct but got {keys!r}."


class TestSlugCollisionRoundTrip:
    """Round-trip tests: save N entries → sync_to_markdown → sync_from_markdown
    into an empty store → assert all N entries survive with the same keys and
    values (TAP-718 acceptance criterion)."""

    def test_round_trip_preserves_all_entries(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Full lossless round-trip: N entries survive export + fresh import."""
        entries = [
            ("alpha-key", "Alpha value.", "architectural"),
            ("beta-key", "Beta value.", "pattern"),
            ("gamma-key", "Gamma value.", "procedural"),
            ("delta-key", "Delta value.", "context"),
            ("epsilon-key", "Epsilon value.", "pattern"),
        ]
        for key, value, tier in entries:
            tmp_store.save(key, value, tier=tier)

        sync_to_markdown(tmp_store, workspace)

        store2 = MemoryStore(workspace / "store2")
        result = sync_from_markdown(store2, workspace)

        assert result["imported"] == len(entries), (
            f"Expected {len(entries)} imports but got {result['imported']}."
        )
        for key, value, tier in entries:
            entry = store2.get(key)
            assert entry is not None, f"Entry '{key}' missing after round-trip."
            assert entry.value == value, f"Value mismatch for '{key}'."
            assert str(entry.tier) == tier, f"Tier mismatch for '{key}'."

    def test_round_trip_entry_count_after_manual_collision_in_md(
        self, tmp_store: MemoryStore, workspace: Path
    ) -> None:
        """Hand-edited MEMORY.md with a slug collision: both entries are imported."""
        # Write a MEMORY.md manually with two headings that collide
        (workspace / "MEMORY.md").write_text(
            "---\nschema_version: 1\n---\n\n"
            "# Memory\n\n"
            "## Foo Bar\n\nFirst entry body.\n\n"
            "## foo-bar\n\nSecond entry body.\n",
            encoding="utf-8",
        )

        result = sync_from_markdown(tmp_store, workspace)

        # Both entries must be imported (not 1 silently dropped)
        assert result["imported"] == 2, (
            f"Expected 2 imports from colliding headings but got {result['imported']}."
        )

        # The first heading's slug survives as "foo-bar"
        first = tmp_store.get("foo-bar")
        assert first is not None, "First collision entry must be importable."
        assert "First entry body." in first.value

        # The second heading gets a deterministic suffix
        suffix = hashlib.sha256("foo-bar".encode()).hexdigest()[:6]
        second_key = f"foo-bar-{suffix}"
        second = tmp_store.get(second_key)
        assert second is not None, f"Second collision entry must be importable as '{second_key}'."
        assert "Second entry body." in second.value


# ---------------------------------------------------------------------------
# get_sync_state tests
# ---------------------------------------------------------------------------


class TestGetSyncState:
    def test_returns_default_when_no_state_file(self, workspace: Path) -> None:
        state = get_sync_state(workspace)
        assert state == {"version": 1}

    def test_returns_state_after_sync(self, tmp_store: MemoryStore, workspace: Path) -> None:
        tmp_store.save("k", "v", tier="pattern")
        sync_to_markdown(tmp_store, workspace)

        state = get_sync_state(workspace)
        assert "last_sync_to" in state

    def test_handles_corrupt_state_file_gracefully(self, workspace: Path) -> None:
        state_dir = workspace / ".tapps-brain"
        state_dir.mkdir()
        (state_dir / "sync_state.json").write_text("NOT JSON", encoding="utf-8")

        state = get_sync_state(workspace)
        assert state == {"version": 1}
