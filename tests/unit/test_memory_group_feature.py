"""Project-local memory_group (GitHub #49)."""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.requires_mcp

from tapps_brain.memory_group import (
    MAX_MEMORY_GROUP_LENGTH,
    MEMORY_GROUP_UNSET,
    normalize_memory_group,
)
from tapps_brain.models import MemoryEntry
from tapps_brain.store import MemoryStore


def _tool_fn(mcp_server: object, name: str):
    for tool in mcp_server._tool_manager.list_tools():  # type: ignore[attr-defined]
        if tool.name == name:
            return tool.fn
    msg = f"tool not found: {name}"
    raise KeyError(msg)


@pytest.fixture
def mcp_server(tmp_path):
    from tapps_brain.mcp_server import create_server

    server = create_server(tmp_path)
    yield server
    if hasattr(server, "_tapps_store"):
        server._tapps_store.close()


def test_normalize_memory_group() -> None:
    assert normalize_memory_group(None) is None
    assert normalize_memory_group("") is None
    assert normalize_memory_group("  team-a  ") == "team-a"
    with pytest.raises(ValueError, match="exceeds max length"):
        normalize_memory_group("x" * (MAX_MEMORY_GROUP_LENGTH + 1))
    with pytest.raises(ValueError, match="control"):
        normalize_memory_group("a\nb")


def test_save_roundtrip_and_list_groups(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        e1 = store.save(key="g1", value="alpha", tier="pattern", memory_group="team-a")
        assert e1.memory_group == "team-a"
        e2 = store.save(key="g2", value="beta", tier="pattern")
        assert e2.memory_group is None

        assert set(store.list_memory_groups()) == {"team-a"}

        listed = store.list_all(memory_group="team-a")
        assert len(listed) == 1 and listed[0].key == "g1"

        hits = store.search("alpha", memory_group="team-a")
        assert len(hits) == 1 and hits[0].key == "g1"
        assert not store.search("alpha", memory_group="other")
    finally:
        store.close()


def test_save_preserves_group_when_unset(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        store.save(key="k", value="v1", tier="pattern", memory_group="g1")
        updated = store.save(key="k", value="v2", tier="pattern", memory_group=MEMORY_GROUP_UNSET)
        assert isinstance(updated, MemoryEntry)
        assert updated.memory_group == "g1"
    finally:
        store.close()


def test_save_explicit_none_clears_group(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    try:
        store.save(key="k", value="v1", tier="pattern", memory_group="g1")
        cleared = store.save(key="k", value="v2", tier="pattern", memory_group=None)
        assert isinstance(cleared, MemoryEntry)
        assert cleared.memory_group is None
    finally:
        store.close()


def test_retriever_respects_memory_group(tmp_path) -> None:
    from tapps_brain.retrieval import MemoryRetriever

    store = MemoryStore(tmp_path)
    try:
        store.save(key="a", value="python asyncio patterns", tier="pattern", memory_group="g1")
        store.save(key="b", value="python asyncio guide", tier="pattern", memory_group="g2")
        r = MemoryRetriever()
        g1 = r.search("python asyncio", store, memory_group="g1", limit=10)
        keys = {s.entry.key for s in g1}
        assert keys == {"a"}
    finally:
        store.close()


def test_mcp_memory_list_groups_tool(mcp_server) -> None:
    store = mcp_server._tapps_store
    store.save(key="mg-mcp", value="x", tier="pattern", memory_group="zeta")
    fn = _tool_fn(mcp_server, "memory_list_groups")
    raw = fn()
    names = json.loads(raw)
    assert "zeta" in names
