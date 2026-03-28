"""Tests for sub-agent memory relay (GitHub #19)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tapps_brain.memory_relay import (
    RelayImportResult,
    build_relay_json,
    import_relay_to_store,
    normalize_relay_tier,
    parse_relay_document,
    resolve_relay_scopes,
)
from tapps_brain.store import MemoryStore


def test_relay_import_result_to_dict() -> None:
    r = RelayImportResult(imported=2, skipped=1, warnings=["a"])
    assert r.to_dict() == {"imported": 2, "skipped": 1, "warnings": ["a"]}


def test_normalize_tier_aliases() -> None:
    assert normalize_relay_tier("long-term") == "architectural"
    assert normalize_relay_tier("SHORT-TERM") == "pattern"
    assert normalize_relay_tier("identity") == "architectural"
    assert normalize_relay_tier(None) == "pattern"


def test_resolve_scope_hive_legacy() -> None:
    assert resolve_relay_scopes({"scope": "hive"}) == ("project", "hive")


def test_resolve_explicit_visibility_and_agent() -> None:
    assert resolve_relay_scopes({"visibility": "session", "agent_scope": "domain"}) == (
        "session",
        "domain",
    )


def test_resolve_invalid_scope() -> None:
    assert resolve_relay_scopes({"scope": "not-a-scope"}) is None


def test_resolve_invalid_visibility() -> None:
    assert resolve_relay_scopes({"visibility": "nope", "scope": "project"}) is None


def test_resolve_invalid_agent_scope_field() -> None:
    assert resolve_relay_scopes({"agent_scope": "everyone", "scope": "project"}) is None


def test_parse_valid() -> None:
    raw = json.dumps(
        {
            "relay_version": "1.0",
            "source_agent": "a1",
            "items": [{"key": "k", "value": "v"}],
        }
    )
    payload, err = parse_relay_document(raw)
    assert err is None
    assert payload is not None
    assert payload["source_agent"] == "a1"


def test_parse_bad_version() -> None:
    payload, err = parse_relay_document('{"relay_version":"9","source_agent":"x","items":[]}')
    assert payload is None
    assert err is not None


def test_parse_missing_source_agent() -> None:
    payload, _err = parse_relay_document('{"relay_version":"1.0","items":[]}')
    assert payload is None


def test_parse_invalid_json() -> None:
    payload, err = parse_relay_document("{")
    assert payload is None
    assert err is not None
    assert "invalid_json" in err


def test_parse_root_not_object() -> None:
    payload, err = parse_relay_document("[]")
    assert payload is None
    assert err is not None


def test_parse_items_not_list() -> None:
    payload, err = parse_relay_document('{"relay_version":"1.0","source_agent":"x","items":{}}')
    assert payload is None
    assert err is not None


def test_import_items_not_list_warns(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(store, {"items": "nope", "source_agent": "a"})
        assert r.imported == 0
        assert "not a list" in r.warnings[0].lower()
    finally:
        store.close()


def test_import_default_agent_non_string(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "items": [{"key": "k.agent", "value": "v", "scope": "project"}],
                "source_agent": 123,
            },
        )
        assert r.imported == 1
        ent = store.get("k.agent")
        assert ent is not None
        assert ent.source_agent == "unknown"
    finally:
        store.close()


def test_import_skips_non_object_row(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "relay_version": "1.0",
                "source_agent": "s",
                "items": [1, {"key": "ok.row", "value": "x", "scope": "project"}],
            },
        )
        assert r.skipped >= 1
        assert r.imported >= 1
    finally:
        store.close()


def test_coerce_invalid_value_type_skipped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [{"key": "bad.val", "value": 99, "scope": "project"}],
            },
        )
        assert r.imported == 0
        assert r.skipped == 1
    finally:
        store.close()


def test_coerce_bad_key_slug_skipped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [{"key": "BadKey", "value": "v", "scope": "project"}],
            },
        )
        assert r.skipped == 1
    finally:
        store.close()


def test_coerce_bad_tags_type_skipped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [{"key": "tag.bad", "value": "v", "scope": "project", "tags": "nope"}],
            },
        )
        assert r.skipped == 1
    finally:
        store.close()


def test_import_invalid_scope_row_skipped(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [{"key": "scope.bad", "value": "v", "scope": "galaxy-wide"}],
            },
        )
        assert r.skipped == 1
    finally:
        store.close()


def test_coerce_unknown_tier_normalized_on_import(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [
                    {
                        "key": "tier.bad",
                        "value": "v",
                        "scope": "project",
                        "tier": "not-a-valid-tier-xyz",
                    }
                ],
            },
        )
        assert r.imported == 1
        ent = store.get("tier.bad")
        assert ent is not None
        assert str(ent.tier) == "pattern"
    finally:
        store.close()


def test_confidence_bool_treated_as_default(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [
                    {
                        "key": "conf.bool",
                        "value": "v",
                        "scope": "project",
                        "confidence": True,
                    }
                ],
            },
        )
        assert r.imported == 1
        ent = store.get("conf.bool")
        assert ent is not None
        # -1.0 in relay → store applies MemorySource.agent default (0.6).
        assert ent.confidence == 0.6
    finally:
        store.close()


def test_branch_persisted(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [
                    {
                        "key": "branch.row",
                        "value": "v",
                        "scope": "branch",
                        "visibility": "branch",
                        "branch": "main",
                    }
                ],
            },
        )
        assert r.imported == 1
        assert store.get("branch.row").branch == "main"
    finally:
        store.close()


def test_confidence_non_numeric_defaults(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [
                    {
                        "key": "conf.str",
                        "value": "v",
                        "scope": "project",
                        "confidence": "nope",
                    }
                ],
            },
        )
        assert r.imported == 1
        assert store.get("conf.str") is not None
    finally:
        store.close()


def test_source_non_string_defaults_to_agent(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(
            store,
            {
                "source_agent": "s",
                "items": [{"key": "src.row", "value": "v", "scope": "project", "source": 1}],
            },
        )
        assert r.imported == 1
        assert store.get("src.row").source.value == "agent"
    finally:
        store.close()


def test_save_blocked_dict_counts_skip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        real_save = store.save

        def _fake_save(*args: object, **kwargs: object) -> object:
            key = kwargs.get("key")
            if key == "block.a":
                return {"error": "content_blocked"}
            return real_save(*args, **kwargs)

        with patch.object(store, "save", _fake_save):
            r = import_relay_to_store(
                store,
                {
                    "source_agent": "s",
                    "items": [
                        {"key": "block.a", "value": "v", "scope": "project"},
                        {"key": "block.b", "value": "v2", "scope": "project"},
                    ],
                },
            )
        assert r.imported == 1
        assert r.skipped == 1
    finally:
        store.close()


def test_save_raises_value_error_skip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        with patch.object(store, "save", side_effect=ValueError("boom")):
            r = import_relay_to_store(
                store,
                {
                    "source_agent": "s",
                    "items": [{"key": "err.row", "value": "v", "scope": "project"}],
                },
            )
        assert r.skipped == 1
        assert r.imported == 0
    finally:
        store.close()


def test_import_skips_bad_key_and_imports_good(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        payload = {
            "relay_version": "1.0",
            "source_agent": "sub-1",
            "items": [
                {"key": "", "value": "x"},
                {"key": "valid.relay.key", "value": "hello", "tier": "pattern", "scope": "project"},
            ],
        }
        r = import_relay_to_store(store, payload)
        assert r.imported == 1
        assert r.skipped == 1
        assert store.get("valid.relay.key") is not None
        assert store.get("valid.relay.key").value == "hello"
    finally:
        store.close()


def test_import_tier_alias_and_hive_scope(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    try:
        payload = {
            "relay_version": "1.0",
            "source_agent": "b",
            "items": [
                {
                    "key": "relay.hive.item",
                    "value": "hive fact",
                    "tier": "long-term",
                    "scope": "hive",
                    "tags": ["t1"],
                }
            ],
        }
        r = import_relay_to_store(store, payload)
        assert r.imported == 1
        ent = store.get("relay.hive.item")
        assert ent is not None
        assert str(ent.tier) == "architectural"
        assert ent.agent_scope == "hive"
    finally:
        store.close()


def test_build_relay_json_roundtrip(tmp_path: Path) -> None:
    items = [{"key": "r.key", "value": "body", "scope": "project"}]
    s = build_relay_json(source_agent="agent-x", items=items)
    data, err = parse_relay_document(s)
    assert err is None
    assert data is not None
    store = MemoryStore(tmp_path)
    try:
        r = import_relay_to_store(store, data)
        assert r.imported == 1
    finally:
        store.close()
