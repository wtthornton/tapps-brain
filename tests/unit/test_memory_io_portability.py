"""Tests for TAP-5027 memory import/export portability."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tapps_brain.adapters.letta_af import letta_af_to_memory_dicts, looks_like_letta_af
from tapps_brain.adapters.mem0 import looks_like_mem0, mem0_to_memory_dicts
from tapps_brain.io import (
    NATIVE_FORMAT,
    NATIVE_FORMAT_VERSION,
    build_embeddings_sidecar,
    build_jsonl_export,
    build_mif_document,
    build_native_envelope,
    entry_to_mif_unit,
    export_memories,
    import_memories,
    import_memory_dicts,
    parse_import_payload,
    parse_jsonl_payload,
    resolve_max_import_entries,
    restore_embeddings,
)
from tapps_brain.markdown_import import (
    import_frontmatter_markdown_text,
    looks_like_frontmatter_export,
    parse_frontmatter_entries,
)
from tapps_brain.models import MemorySnapshot, MemoryTier
from tapps_brain.relations import RelationEntry
from tests.factories import make_entry


def _make_store(entries: list | None = None) -> MagicMock:
    store = MagicMock()
    store.project_root = Path("/test/project")
    entries = entries or []
    store.snapshot.return_value = MemorySnapshot(
        project_root="/test/project",
        entries=entries,
        total_count=len(entries),
    )
    store.list_all.return_value = entries
    store._ensure_entry_cached.return_value = None
    store.get.return_value = None
    store._embedding_provider = None
    store._persistence = MagicMock()
    store._persistence.list_relations.return_value = []
    store.query_relations.return_value = []
    return store


def _make_validator(tmp_path: Path) -> MagicMock:
    validator = MagicMock()
    validator.validate_path.side_effect = lambda p, **kwargs: Path(p).resolve()
    return validator


class TestNativeEnvelope:
    def test_build_native_envelope_fields(self) -> None:
        entry = make_entry(key="k1", value="v1")
        payload = build_native_envelope([entry.model_dump(mode="json")], source_project="/p")
        assert payload["format"] == NATIVE_FORMAT
        assert payload["format_version"] == NATIVE_FORMAT_VERSION
        assert payload["entry_count"] == 1
        assert payload["memories"][0]["key"] == "k1"

    def test_export_writes_envelope(self, tmp_path: Path) -> None:
        entries = [make_entry(key="a", value="1")]
        store = _make_store(entries)
        out = tmp_path / "e.json"
        result = export_memories(store, out, _make_validator(tmp_path))
        assert result["exported_count"] == 1
        data = json.loads(out.read_text())
        assert data["format"] == NATIVE_FORMAT
        assert data["format_version"] == NATIVE_FORMAT_VERSION
        assert len(data["memories"]) == 1

    def test_import_accepts_bare_array(self, tmp_path: Path) -> None:
        entry = make_entry(key="bare-key", value="bare")
        path = tmp_path / "bare.json"
        path.write_text(json.dumps([entry.model_dump(mode="json")]))
        store = _make_store()
        result = import_memories(store, path, _make_validator(tmp_path))
        assert result["imported_count"] == 1
        assert result["detected_format"] == "bare-array"

    def test_import_accepts_envelope(self, tmp_path: Path) -> None:
        entry = make_entry(key="env-key", value="env")
        payload = build_native_envelope([entry.model_dump(mode="json")])
        path = tmp_path / "env.json"
        path.write_text(json.dumps(payload))
        store = _make_store()
        result = import_memories(store, path, _make_validator(tmp_path))
        assert result["imported_count"] == 1
        assert result["detected_format"] == NATIVE_FORMAT

    def test_parse_rejects_missing_memories(self) -> None:
        with pytest.raises(ValueError, match="memories"):
            parse_import_payload({"foo": []})


class TestRelationsBundle:
    def test_export_includes_relations(self, tmp_path: Path) -> None:
        entries = [make_entry(key="src", value="A uses B")]
        store = _make_store(entries)
        rel = {
            "subject": "a",
            "predicate": "uses",
            "object_entity": "b",
            "source_entry_keys": ["src"],
            "confidence": 0.9,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
        store._persistence.list_relations.return_value = [rel]
        out = tmp_path / "bundle.json"
        export_memories(store, out, _make_validator(tmp_path), include_relations=True)
        data = json.loads(out.read_text())
        assert data["relations"] == [rel]
        assert data["relation_count"] == 1

    def test_import_restores_relations(self) -> None:
        store = _make_store()
        memories = [make_entry(key="src", value="A uses B").model_dump(mode="json")]
        relations = [
            {
                "subject": "a",
                "predicate": "uses",
                "object_entity": "b",
                "source_entry_keys": ["src"],
                "confidence": 0.85,
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ]
        result = import_memory_dicts(store, memories, relations=relations)
        assert result["imported_count"] == 1
        assert result["relations_restored"] == 1
        store.save_relations.assert_called()
        args = store.save_relations.call_args
        assert args[0][0] == "src"
        assert isinstance(args[0][1][0], RelationEntry)


class TestEmbeddingsSidecar:
    def test_skip_on_model_mismatch(self) -> None:
        store = _make_store()
        provider = MagicMock()
        provider.model_id = "BAAI/bge-small-en-v1.5"
        store._embedding_provider = provider
        store.set_embeddings = MagicMock(return_value={"restored": 1})
        sidecar = build_embeddings_sidecar(
            {"k": [0.1, 0.2]},
            embedding_model_id="other-model",
        )
        stats = restore_embeddings(store, sidecar)
        assert stats["skipped_mismatch"] == 1
        assert stats["restored"] == 0
        store.set_embeddings.assert_not_called()

    def test_restore_on_match(self) -> None:
        store = _make_store()
        provider = MagicMock()
        provider.model_id = "BAAI/bge-small-en-v1.5"
        store._embedding_provider = provider
        store.set_embeddings = MagicMock(return_value={"restored": 1})
        sidecar = build_embeddings_sidecar(
            {"k": [0.1, 0.2]},
            embedding_model_id="BAAI/bge-small-en-v1.5",
        )
        stats = restore_embeddings(store, sidecar)
        assert stats["restored"] == 1
        store.set_embeddings.assert_called_once()


class TestMif:
    def test_mif_round_trip_extensions(self) -> None:
        entry = make_entry(
            key="mif-key",
            value="portable fact",
            tier=MemoryTier.architectural,
        )
        entry = entry.model_copy(
            update={"agent_scope": "domain", "memory_group": "guild", "confidence": 0.91}
        )
        unit = entry_to_mif_unit(entry)
        assert unit["memoryType"] == "semantic"
        assert unit["content"] == "portable fact"
        assert "created" in unit
        tapps = unit["extensions"]["tapps"]
        assert tapps["key"] == "mif-key"
        assert tapps["tier"] == "architectural"
        assert tapps["agent_scope"] == "domain"
        assert tapps["memory_group"] == "guild"

        doc = build_mif_document([entry])
        parsed = parse_import_payload(doc)
        assert parsed["detected_format"] == "mif"
        mem = parsed["memories"][0]
        assert mem["key"] == "mif-key"
        assert mem["tier"] == "architectural"
        assert mem["agent_scope"] == "domain"
        assert mem["memory_group"] == "guild"
        assert mem["confidence"] == pytest.approx(0.91)


class TestJsonlAndLimits:
    def test_jsonl_round_trip(self) -> None:
        entries = [
            make_entry(key="j1", value="one"),
            make_entry(key="j2", value="two"),
        ]
        text = build_jsonl_export(entries, source_project="/p")
        parsed = parse_jsonl_payload(text)
        assert parsed["detected_format"] == "jsonl"
        assert len(parsed["memories"]) == 2
        assert {m["key"] for m in parsed["memories"]} == {"j1", "j2"}

    def test_import_limit_error_message(self) -> None:
        with pytest.raises(ValueError, match=r"3 > 2"):
            parse_import_payload(
                [
                    {"key": "a", "value": "1"},
                    {"key": "b", "value": "2"},
                    {"key": "c", "value": "3"},
                ],
                max_entries=2,
            )

    def test_resolve_max_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TAPPS_BRAIN_MAX_IMPORT_ENTRIES", "123")
        assert resolve_max_import_entries() == 123
        assert resolve_max_import_entries(9) == 9


class TestFrontmatterMarkdown:
    def test_round_trip_preserves_keys(self) -> None:
        from tapps_brain.io import export_to_markdown

        entries = [
            make_entry(key="keep-me", value="Body one", tier=MemoryTier.pattern),
            make_entry(key="also-keep", value="Body two", tier=MemoryTier.architectural),
        ]
        md = export_to_markdown(entries, group_by="none")
        assert looks_like_frontmatter_export(md)
        assert "key: 'keep-me'" in md or 'key: "keep-me"' in md or "key: 'keep-me'" in md
        parsed = parse_frontmatter_entries(md)
        keys = {e["key"] for e in parsed}
        assert keys == {"keep-me", "also-keep"}

        store = _make_store()
        imported = import_frontmatter_markdown_text(md, store)
        assert imported == 2
        assert store.save.call_count == 2


class TestAdapters:
    def test_mem0_preserve(self) -> None:
        payload = [
            {
                "id": "abc-123",
                "memory": "User likes dark mode",
                "categories": ["prefs"],
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        assert looks_like_mem0(payload)
        entries = mem0_to_memory_dicts(payload)
        assert len(entries) == 1
        assert entries[0]["key"] == "abc-123"
        assert entries[0]["value"] == "User likes dark mode"
        assert "prefs" in entries[0]["tags"]

    def test_letta_af_core_blocks_skip_archival(self) -> None:
        payload = {
            "type": "agent",
            "memory": {
                "persona": "I am helpful.",
                "human": "The user is Bill.",
            },
            "archival_memory": [{"text": "old passage"}],
        }
        assert looks_like_letta_af(payload)
        entries, warnings = letta_af_to_memory_dicts(payload)
        assert len(entries) == 2
        assert any("archival" in w for w in warnings)
        keys = {e["key"] for e in entries}
        assert "letta-persona" in keys
        assert "letta-human" in keys
