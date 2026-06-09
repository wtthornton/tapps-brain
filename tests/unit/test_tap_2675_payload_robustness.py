"""Regression tests for brain_record_events_batch payload robustness (TAP-2675).

Two production-payload bugs, reproduced with the verbatim shapes from the
brain tracebacks:

1. ``EntitySpec`` 500'd with "2 validation errors ... entity_type / canonical_name
   Field required" when callers posted entities as ``{'key': '<string>'}``.
2. ``store.save`` raised ``AttributeError: 'dict' object has no attribute 'strip'``
   in ``check_content_safety`` when ``/v1/remember`` posted a non-str ``value``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tapps_brain.experience import EntitySpec
from tapps_brain.store import MemoryStore, _ensure_str_value

if TYPE_CHECKING:
    from pathlib import Path


class TestEntitySpecKeyShorthand:
    def test_key_only_payload_is_accepted(self) -> None:
        # Verbatim shape from the production traceback (input_value).
        spec = EntitySpec(**{"key": "agent:bambustudio-xyz tapps-brain memory (summary)"})
        assert spec.canonical_name == "agent:bambustudio-xyz tapps-brain memory (summary)"
        assert spec.entity_type == "concept"

    def test_full_payload_is_unchanged(self) -> None:
        spec = EntitySpec(entity_type="module", canonical_name="store.py")
        assert spec.entity_type == "module"
        assert spec.canonical_name == "store.py"

    def test_explicit_canonical_name_wins_over_key(self) -> None:
        spec = EntitySpec(**{"key": "k", "canonical_name": "real-name"})
        assert spec.canonical_name == "real-name"
        assert spec.entity_type == "concept"

    def test_type_id_shorthand_from_tapps_mcp(self) -> None:
        spec = EntitySpec(**{"type": "file", "id": "packages/tapps-mcp/checklist.py"})
        assert spec.entity_type == "file"
        assert spec.canonical_name == "packages/tapps-mcp/checklist.py"

    def test_explicit_entity_type_wins_over_type_shorthand(self) -> None:
        spec = EntitySpec(**{"type": "file", "entity_type": "module", "id": "store.py"})
        assert spec.entity_type == "module"
        assert spec.canonical_name == "store.py"

    def test_empty_payload_still_rejected(self) -> None:
        # No key and no canonical_name → nothing to name the entity → still invalid.
        with pytest.raises(ValueError, match="canonical_name"):
            EntitySpec(entity_type="module")


class TestEnsureStrValue:
    def test_str_unchanged(self) -> None:
        assert _ensure_str_value("hello") == "hello"

    def test_dict_json_encoded(self) -> None:
        assert _ensure_str_value({"a": 1}) == '{"a": 1}'

    def test_list_json_encoded(self) -> None:
        assert _ensure_str_value([1, "two"]) == '[1, "two"]'

    def test_int_str_coerced(self) -> None:
        assert _ensure_str_value(42) == "42"

    def test_dict_value_does_not_raise_on_strip(self) -> None:
        # The crash was `content.strip()` on a dict in check_content_safety;
        # after coercion the value is a str and `.strip()` is safe.
        coerced = _ensure_str_value({"nested": {"k": "v"}})
        assert coerced.strip() == coerced


class TestStoreSaveDictValue:
    def test_save_dict_value_persists_json_string(self, tmp_path: Path) -> None:
        # Before the fix this 500'd in check_content_safety with
        # AttributeError: 'dict' object has no attribute 'strip'.
        store = MemoryStore(tmp_path, embedding_provider=None)
        store.save("k", cast("str", {"a": 1}), tier="pattern")
        loaded = store.get("k")
        assert loaded is not None
        assert loaded.value == '{"a": 1}'
        store.close()
