"""Tests for memory import/export (Epic 25, Story 25.4)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tapps_brain.io import (
    export_memories,
    export_to_markdown,
    import_memories,
)
from tapps_brain.models import (
    MemoryEntry,
    MemorySnapshot,
    MemoryTier,
)
from tests.factories import make_entry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_entry(key: str = "test-key", value: str = "test value") -> MemoryEntry:
    return make_entry(
        key=key,
        value=value,
        source_agent="test-agent",
        tags=["test"],
    )


def _make_store(entries: list[MemoryEntry] | None = None) -> MagicMock:
    """Create a mock MemoryStore."""
    store = MagicMock()
    store.project_root = Path("/test/project")

    entries = entries or []
    store.snapshot.return_value = MemorySnapshot(
        project_root="/test/project",
        entries=entries,
        total_count=len(entries),
    )

    # get() returns None by default (no existing key)
    store.get.return_value = None
    store.count.return_value = len(entries)
    return store


def _make_validator(tmp_path: Path) -> MagicMock:
    """Create a mock PathValidator that returns real paths."""
    validator = MagicMock()
    # validate_path returns the path it's given, resolved
    validator.validate_path.side_effect = lambda p, **kwargs: Path(p).resolve()
    return validator


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------


class TestExport:
    def test_export_creates_valid_json(self, tmp_path: Path) -> None:
        entries = [_make_entry("key-1", "value 1"), _make_entry("key-2", "value 2")]
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.json"

        result = export_memories(store, output, validator)

        assert result["exported_count"] == 2
        assert output.exists()

        data = json.loads(output.read_text())
        assert "memories" in data
        assert len(data["memories"]) == 2
        assert "exported_at" in data
        assert "source_project" in data
        assert "tapps_version" in data

    def test_export_with_tier_filter(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("arch-key", "arch value"),
            _make_entry("ctx-key", "ctx value"),
        ]
        # Manually set tiers
        entries[0] = entries[0].model_copy(update={"tier": MemoryTier.architectural})
        entries[1] = entries[1].model_copy(update={"tier": MemoryTier.context})
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.json"

        result = export_memories(store, output, validator, tier="architectural")

        assert result["exported_count"] == 1
        data = json.loads(output.read_text())
        assert data["memories"][0]["tier"] == "architectural"

    def test_export_with_min_confidence_filter(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("high-conf", "high"),
            _make_entry("low-conf", "low"),
        ]
        entries[0] = entries[0].model_copy(update={"confidence": 0.9})
        entries[1] = entries[1].model_copy(update={"confidence": 0.2})
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.json"

        result = export_memories(store, output, validator, min_confidence=0.5)

        assert result["exported_count"] == 1

    def test_export_empty_store(self, tmp_path: Path) -> None:
        store = _make_store([])
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.json"

        result = export_memories(store, output, validator)

        assert result["exported_count"] == 0
        data = json.loads(output.read_text())
        assert data["memories"] == []


# ---------------------------------------------------------------------------
# Markdown export tests (Epic 65.2)
# ---------------------------------------------------------------------------


class TestExportMarkdown:
    def test_export_format_markdown_creates_valid_markdown(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("key-1", "Memory content one"),
            _make_entry("key-2", "Memory content two"),
        ]
        entries[0] = entries[0].model_copy(update={"tier": MemoryTier.pattern})
        entries[1] = entries[1].model_copy(update={"tier": MemoryTier.architectural})
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        result = export_memories(
            store,
            output,
            validator,
            export_format="markdown",
        )

        assert result["exported_count"] == 2
        assert result["format"] == "markdown"
        assert output.exists()
        text = output.read_text()
        assert "# TappsMCP Memory Export" in text
        assert "key-1" in text
        assert "key-2" in text
        assert "Memory content one" in text
        assert "Memory content two" in text

    def test_export_markdown_grouped_by_tier(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("arch-key", "Arch content"),
            _make_entry("pattern-key", "Pattern content"),
        ]
        entries[0] = entries[0].model_copy(update={"tier": MemoryTier.architectural})
        entries[1] = entries[1].model_copy(update={"tier": MemoryTier.pattern})
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        export_memories(
            store,
            output,
            validator,
            export_format="markdown",
            group_by="tier",
        )

        text = output.read_text()
        assert "# Architectural" in text
        assert "# Pattern" in text

    def test_export_markdown_with_frontmatter(self, tmp_path: Path) -> None:
        entries = [_make_entry("key-1", "Content")]
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        export_memories(
            store,
            output,
            validator,
            export_format="markdown",
            include_frontmatter=True,
        )

        text = output.read_text()
        assert "---" in text
        assert "tags:" in text
        assert "created_at:" in text
        assert "confidence:" in text
        assert "tier:" in text

    def test_export_markdown_without_frontmatter(self, tmp_path: Path) -> None:
        entries = [_make_entry("key-1", "Content")]
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        export_memories(
            store,
            output,
            validator,
            export_format="markdown",
            include_frontmatter=False,
        )

        text = output.read_text()
        assert "---" not in text or text.count("---") < 2
        assert "## key-1" in text
        assert "Content" in text

    def test_export_markdown_group_by_none(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("key-a", "A content"),
            _make_entry("key-b", "B content"),
        ]
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        export_memories(
            store,
            output,
            validator,
            export_format="markdown",
            group_by="none",
        )

        text = output.read_text()
        assert "## key-a" in text
        assert "## key-b" in text

    def test_export_to_markdown_empty_returns_placeholder(self) -> None:
        result = export_to_markdown([])
        assert "# TappsMCP Memory Export" in result
        assert "*No memories.*" in result

    def test_export_to_markdown_include_metadata(self, tmp_path: Path) -> None:
        entries = [_make_entry("key-1", "Content")]
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        export_memories(
            store,
            output,
            validator,
            export_format="markdown",
            include_metadata=True,
        )

        text = output.read_text()
        assert "created:" in text
        assert "confidence" in text


# ---------------------------------------------------------------------------
# Import tests
# ---------------------------------------------------------------------------


class TestImport:
    def test_import_loads_entries(self, tmp_path: Path) -> None:
        entry = _make_entry("imported-key", "imported value")
        payload = {"memories": [entry.model_dump(mode="json")]}

        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps(payload))

        store = _make_store()
        validator = _make_validator(tmp_path)

        result = import_memories(store, input_file, validator)

        assert result["imported_count"] == 1
        assert result["skipped_count"] == 0
        assert result["error_count"] == 0
        store.save.assert_called_once()

    def test_import_skips_existing_keys(self, tmp_path: Path) -> None:
        entry = _make_entry("existing-key", "new value")
        payload = {"memories": [entry.model_dump(mode="json")]}

        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps(payload))

        store = _make_store()
        # Simulate existing key
        store.get.return_value = _make_entry("existing-key", "old value")
        validator = _make_validator(tmp_path)

        result = import_memories(store, input_file, validator, overwrite=False)

        assert result["imported_count"] == 0
        assert result["skipped_count"] == 1
        store.save.assert_not_called()

    def test_import_with_overwrite(self, tmp_path: Path) -> None:
        entry = _make_entry("existing-key", "new value")
        payload = {"memories": [entry.model_dump(mode="json")]}

        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps(payload))

        store = _make_store()
        store.get.return_value = _make_entry("existing-key", "old value")
        validator = _make_validator(tmp_path)

        result = import_memories(store, input_file, validator, overwrite=True)

        assert result["imported_count"] == 1
        store.save.assert_called_once()

    def test_import_marks_source_agent_as_imported(self, tmp_path: Path) -> None:
        entry = _make_entry("key-1", "val")
        payload = {"memories": [entry.model_dump(mode="json")]}

        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps(payload))

        store = _make_store()
        validator = _make_validator(tmp_path)

        import_memories(store, input_file, validator)

        call_kwargs = store.save.call_args
        assert "(imported)" in call_kwargs.kwargs["source_agent"]

    def test_import_rejects_invalid_entries(self, tmp_path: Path) -> None:
        payload = {
            "memories": [
                {"key": "valid-key", "value": "valid"},
                {"key": "!!invalid!!", "value": "bad key"},  # invalid slug
            ]
        }

        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps(payload))

        store = _make_store()
        validator = _make_validator(tmp_path)

        result = import_memories(store, input_file, validator)

        assert result["imported_count"] == 1
        assert result["error_count"] == 1

    def test_import_validates_payload_structure(self, tmp_path: Path) -> None:
        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps({"not_memories": []}))

        store = _make_store()
        validator = _make_validator(tmp_path)

        with pytest.raises(ValueError, match="'memories' list"):
            import_memories(store, input_file, validator)

    def test_import_path_validation_enforced(self, tmp_path: Path) -> None:
        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps({"memories": []}))

        store = _make_store()
        validator = _make_validator(tmp_path)

        import_memories(store, input_file, validator)

        validator.validate_path.assert_called_once()

    def test_import_malformed_json_raises_value_error(self, tmp_path: Path) -> None:
        input_file = tmp_path / "bad.json"
        input_file.write_text("not valid json {{{")

        store = _make_store()
        validator = _make_validator(tmp_path)

        with pytest.raises(ValueError, match="not valid JSON"):
            import_memories(store, input_file, validator)

    def test_import_non_dict_items_are_dropped(self, tmp_path: Path) -> None:
        entry = _make_entry("valid-key", "valid value")
        payload = {"memories": [entry.model_dump(mode="json"), "not-a-dict", 42]}

        input_file = tmp_path / "import.json"
        input_file.write_text(json.dumps(payload))

        store = _make_store()
        validator = _make_validator(tmp_path)

        result = import_memories(store, input_file, validator)

        # Valid dict entry is imported; non-dict items are silently dropped
        assert result["imported_count"] == 1
        assert result["error_count"] == 0


class TestExportMarkdownProcedural:
    """Regression tests for the procedural tier data-loss bug."""

    def test_export_markdown_includes_procedural_tier(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("proc-key", "Procedural content"),
        ]
        entries[0] = entries[0].model_copy(update={"tier": MemoryTier.procedural})
        store = _make_store(entries)
        validator = _make_validator(tmp_path)
        output = tmp_path / "export.md"

        export_memories(store, output, validator, export_format="markdown", group_by="tier")

        text = output.read_text()
        assert "# Procedural" in text
        assert "proc-key" in text
        assert "Procedural content" in text

    def test_export_to_markdown_all_four_tiers_rendered(self) -> None:
        entries = [
            _make_entry("arch-key", "arch content"),
            _make_entry("pattern-key", "pattern content"),
            _make_entry("proc-key", "procedural content"),
            _make_entry("ctx-key", "context content"),
        ]
        entries[0] = entries[0].model_copy(update={"tier": MemoryTier.architectural})
        entries[1] = entries[1].model_copy(update={"tier": MemoryTier.pattern})
        entries[2] = entries[2].model_copy(update={"tier": MemoryTier.procedural})
        entries[3] = entries[3].model_copy(update={"tier": MemoryTier.context})

        result = export_to_markdown(entries, group_by="tier")

        assert "# Architectural" in result
        assert "# Pattern" in result
        assert "# Procedural" in result
        assert "# Context" in result
        assert "arch-key" in result
        assert "pattern-key" in result
        assert "proc-key" in result
        assert "ctx-key" in result
