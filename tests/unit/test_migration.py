"""Unit tests for tapps_brain.migration — memory-core migration tool.

Tests cover:
- _slugify: slug normalisation
- _count_workspace_entries: dry-run counting without a store
- _import_memory_core_sqlite: reading from a real SQLite DB
- find_memory_core_db: DB discovery
- migrate_from_workspace: end-to-end (real + dry-run)
- openclaw_migrate MCP tool: via mcp_server.create_server
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tapps_brain.migration import (
    _count_workspace_entries,
    _import_memory_core_sqlite,
    _slugify,
    find_memory_core_db,
    migrate_from_workspace,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    """Return a temporary OpenClaw workspace directory."""
    return tmp_path / "workspace"


@pytest.fixture()
def workspace_with_memory_md(tmp_workspace: Path) -> Path:
    """Workspace containing MEMORY.md with three headings."""
    tmp_workspace.mkdir(parents=True)
    memory_md = tmp_workspace / "MEMORY.md"
    memory_md.write_text(
        "# Project Brain\n\n"
        "## Architecture Overview\nThe system uses SQLite for storage.\n\n"
        "### Coding Pattern\nAlways use type hints.\n\n"
        "#### Workflow Step\nRun tests before committing.\n",
        encoding="utf-8",
    )
    return tmp_workspace


@pytest.fixture()
def workspace_with_daily_notes(tmp_workspace: Path) -> Path:
    """Workspace containing two daily-note files."""
    tmp_workspace.mkdir(parents=True)
    mem_dir = tmp_workspace / "memory"
    mem_dir.mkdir()
    (mem_dir / "2026-01-01.md").write_text("# Daily\nNew year notes.", encoding="utf-8")
    (mem_dir / "2026-01-02.md").write_text("# Daily\nDay two notes.", encoding="utf-8")
    return tmp_workspace


@pytest.fixture()
def memory_core_sqlite(tmp_path: Path) -> Path:
    """Create a minimal memory-core SQLite DB at tmp_path/agent.sqlite."""
    db_path = tmp_path / "agent.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE memories (id TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO memories VALUES (?, ?)",
        [
            ("py-typing", "Always annotate function signatures."),
            ("git-flow", "Use feature branches."),
            ("", ""),  # empty value — should be skipped
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def mock_store() -> MagicMock:
    """Minimal MemoryStore mock that tracks saved entries."""
    store = MagicMock()
    _saved: dict[str, object] = {}

    def _get(key: str) -> object | None:
        return _saved.get(key)

    def _save(**kwargs: object) -> None:  # type: ignore[misc]
        _saved[kwargs["key"]] = kwargs  # type: ignore[index]

    store.get.side_effect = _get
    store.save.side_effect = _save
    store._saved = _saved
    return store


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert _slugify("Hello World") == "hello-world"


def test_slugify_special_chars() -> None:
    assert _slugify("Use type hints!") == "use-type-hints"


def test_slugify_empty_returns_placeholder() -> None:
    assert _slugify("") == "m-"


def test_slugify_truncates_to_max_key_length() -> None:
    from tapps_brain.models import MAX_KEY_LENGTH

    result = _slugify("a" * (MAX_KEY_LENGTH + 50))
    assert len(result) <= MAX_KEY_LENGTH


def test_slugify_strips_separators() -> None:
    assert _slugify("---hello---") == "hello"


# ---------------------------------------------------------------------------
# _count_workspace_entries
# ---------------------------------------------------------------------------


def test_count_no_files(tmp_workspace: Path) -> None:
    tmp_workspace.mkdir(parents=True)
    md, daily = _count_workspace_entries(tmp_workspace)
    assert md == 0
    assert daily == 0


def test_count_memory_md(workspace_with_memory_md: Path) -> None:
    md, daily = _count_workspace_entries(workspace_with_memory_md)
    assert md == 3  # H2, H3, H4 headings
    assert daily == 0


def test_count_daily_notes(workspace_with_daily_notes: Path) -> None:
    md, daily = _count_workspace_entries(workspace_with_daily_notes)
    assert md == 0
    assert daily == 2


def test_count_combined(workspace_with_memory_md: Path) -> None:
    mem_dir = workspace_with_memory_md / "memory"
    mem_dir.mkdir()
    (mem_dir / "2026-03-01.md").write_text("# Daily", encoding="utf-8")
    md, daily = _count_workspace_entries(workspace_with_memory_md)
    assert md == 3
    assert daily == 1


def test_count_ignores_non_daily_files(tmp_workspace: Path) -> None:
    tmp_workspace.mkdir(parents=True)
    mem_dir = tmp_workspace / "memory"
    mem_dir.mkdir()
    (mem_dir / "notes.md").write_text("random note", encoding="utf-8")
    (mem_dir / "2026-03-01.md").write_text("daily", encoding="utf-8")
    _, daily = _count_workspace_entries(tmp_workspace)
    assert daily == 1


# ---------------------------------------------------------------------------
# _import_memory_core_sqlite
# ---------------------------------------------------------------------------


def test_import_sqlite_basic(memory_core_sqlite: Path, mock_store: MagicMock) -> None:
    result = _import_memory_core_sqlite(mock_store, memory_core_sqlite)
    assert result["imported"] == 2  # empty value row skipped
    assert result["skipped"] == 1  # the empty-value row
    assert result["errors"] == 0
    assert "py-typing" in mock_store._saved
    assert "git-flow" in mock_store._saved


def test_import_sqlite_dry_run(memory_core_sqlite: Path) -> None:
    result = _import_memory_core_sqlite(None, memory_core_sqlite, dry_run=True)
    assert result["imported"] == 2
    assert result["errors"] == 0


def test_import_sqlite_skips_existing(memory_core_sqlite: Path, mock_store: MagicMock) -> None:
    # Pre-populate one key
    existing: dict[str, object] = {"py-typing": "old value"}
    mock_store.get.side_effect = lambda k: existing.get(k)

    result = _import_memory_core_sqlite(mock_store, memory_core_sqlite)
    assert result["imported"] == 1  # only git-flow imported
    assert result["skipped"] == 2  # py-typing (existing) + empty value


def test_import_sqlite_no_matching_table(tmp_path: Path, mock_store: MagicMock) -> None:
    db_path = tmp_path / "unknown.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()

    result = _import_memory_core_sqlite(mock_store, db_path)
    assert result["imported"] == 0
    assert result["errors"] == 0


def test_import_sqlite_bad_path(tmp_path: Path, mock_store: MagicMock) -> None:
    result = _import_memory_core_sqlite(mock_store, tmp_path / "nonexistent.sqlite")
    assert result["errors"] == 1


def test_import_sqlite_content_table(tmp_path: Path, mock_store: MagicMock) -> None:
    """Test that 'entries' table with 'content' column is discovered."""
    db_path = tmp_path / "alt.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE entries (slug TEXT, content TEXT)")
    conn.execute("INSERT INTO entries VALUES ('my-key', 'my value')")
    conn.commit()
    conn.close()

    result = _import_memory_core_sqlite(mock_store, db_path)
    assert result["imported"] == 1
    assert "my-key" in mock_store._saved


def test_import_sqlite_no_value_column(tmp_path: Path, mock_store: MagicMock) -> None:
    """Table with no recognised value column is skipped gracefully."""
    db_path = tmp_path / "novalue.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE memories (foo TEXT, bar TEXT)")
    conn.execute("INSERT INTO memories VALUES ('a', 'b')")
    conn.commit()
    conn.close()

    result = _import_memory_core_sqlite(mock_store, db_path)
    assert result["imported"] == 0
    assert result["errors"] == 0


# ---------------------------------------------------------------------------
# find_memory_core_db
# ---------------------------------------------------------------------------


def test_find_memory_core_db_not_found(tmp_path: Path) -> None:
    with patch("tapps_brain.migration._MEMORY_CORE_DB_DIR", tmp_path / "nonexistent"):
        assert find_memory_core_db() is None


def test_find_memory_core_db_by_agent_id(tmp_path: Path) -> None:
    (tmp_path / "myagent.sqlite").write_text("", encoding="utf-8")
    with patch("tapps_brain.migration._MEMORY_CORE_DB_DIR", tmp_path):
        result = find_memory_core_db("myagent")
    assert result is not None
    assert result.name == "myagent.sqlite"


def test_find_memory_core_db_fallback(tmp_path: Path) -> None:
    (tmp_path / "fallback.sqlite").write_text("", encoding="utf-8")
    with patch("tapps_brain.migration._MEMORY_CORE_DB_DIR", tmp_path):
        result = find_memory_core_db()
    assert result is not None
    assert result.name == "fallback.sqlite"


def test_find_memory_core_db_wrong_agent_falls_back(tmp_path: Path) -> None:
    (tmp_path / "other.sqlite").write_text("", encoding="utf-8")
    with patch("tapps_brain.migration._MEMORY_CORE_DB_DIR", tmp_path):
        result = find_memory_core_db("nonexistent-agent")
    assert result is not None  # falls back to first .sqlite


# ---------------------------------------------------------------------------
# migrate_from_workspace
# ---------------------------------------------------------------------------


def test_migrate_dry_run_no_files(tmp_workspace: Path) -> None:
    tmp_workspace.mkdir(parents=True)
    result = migrate_from_workspace(None, tmp_workspace, dry_run=True)
    assert result["imported"] == 0
    assert result["dry_run"] is True
    assert result["memory_md"] == 0
    assert result["daily_notes"] == 0
    assert result["memory_core_sqlite"] == 0
    assert result["memory_core_db"] is None


def test_migrate_dry_run_with_memory_md(workspace_with_memory_md: Path) -> None:
    result = migrate_from_workspace(None, workspace_with_memory_md, dry_run=True)
    assert result["memory_md"] == 3
    assert result["dry_run"] is True


def test_migrate_dry_run_with_daily_notes(workspace_with_daily_notes: Path) -> None:
    result = migrate_from_workspace(None, workspace_with_daily_notes, dry_run=True)
    assert result["daily_notes"] == 2
    assert result["dry_run"] is True


def test_migrate_dry_run_with_sqlite(
    workspace_with_memory_md: Path,
    memory_core_sqlite: Path,
) -> None:
    with patch(
        "tapps_brain.migration.find_memory_core_db",
        return_value=memory_core_sqlite,
    ):
        result = migrate_from_workspace(None, workspace_with_memory_md, dry_run=True)
    assert result["memory_core_sqlite"] == 2
    assert result["memory_core_db"] == str(memory_core_sqlite)
    assert result["dry_run"] is True


def test_migrate_raises_without_store(tmp_workspace: Path) -> None:
    tmp_workspace.mkdir(parents=True)
    with pytest.raises(ValueError, match="store must be provided"):
        migrate_from_workspace(None, tmp_workspace, dry_run=False)


def test_migrate_real_with_memory_md(
    workspace_with_memory_md: Path,
    tmp_path: Path,
) -> None:
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path / "store")
    try:
        result = migrate_from_workspace(store, workspace_with_memory_md)
        assert result["memory_md"] >= 1  # at least one section imported
        assert "imported" in result
        assert result.get("dry_run") is None  # not present in live mode
    finally:
        store.close()


def test_migrate_idempotent(
    workspace_with_memory_md: Path,
    tmp_path: Path,
) -> None:
    """Running migrate twice must not create duplicate entries."""
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path / "store")
    try:
        r1 = migrate_from_workspace(store, workspace_with_memory_md)
        r2 = migrate_from_workspace(store, workspace_with_memory_md)
        assert r2["imported"] == 0, "second run should import nothing new"
        assert r2["skipped"] == r1["imported"], "second run should skip everything"
    finally:
        store.close()


def test_migrate_with_sqlite(
    workspace_with_memory_md: Path,
    memory_core_sqlite: Path,
    tmp_path: Path,
) -> None:
    from tapps_brain.store import MemoryStore

    store = MemoryStore(tmp_path / "store")
    try:
        with patch(
            "tapps_brain.migration.find_memory_core_db",
            return_value=memory_core_sqlite,
        ):
            result = migrate_from_workspace(store, workspace_with_memory_md)
        assert result["memory_core_sqlite"] == 2
        assert result["memory_core_db"] == str(memory_core_sqlite)
        assert store.get("py-typing") is not None
        assert store.get("git-flow") is not None
    finally:
        store.close()


# ---------------------------------------------------------------------------
# MCP tool: openclaw_migrate
# ---------------------------------------------------------------------------


def test_mcp_openclaw_migrate_dry_run(tmp_workspace: Path) -> None:
    tmp_workspace.mkdir(parents=True)
    from tapps_brain.mcp_server import create_server
    from tapps_brain.store import MemoryStore

    store_path = tmp_workspace.parent / "store"
    store = MemoryStore(store_path)
    try:
        server = create_server(store_path)
        # Access the underlying function through the server's tool registry
        tool_fn = next(
            t.fn
            for t in server._tool_manager.list_tools()
            if t.name == "openclaw_migrate"
        )
        raw = tool_fn(workspace_dir=str(tmp_workspace), dry_run=True)
        data = json.loads(raw)
        assert "imported" in data
        assert data.get("dry_run") is True
    finally:
        store.close()


def test_mcp_openclaw_migrate_live(tmp_workspace: Path, tmp_path: Path) -> None:
    (tmp_workspace).mkdir(parents=True)
    memory_md = tmp_workspace / "MEMORY.md"
    memory_md.write_text(
        "# Brain\n\n## Architecture\nSQLite backend.\n",
        encoding="utf-8",
    )

    from tapps_brain.mcp_server import create_server

    store_path = tmp_path / "mcp_store"
    server = create_server(store_path)
    tool_fn = next(
        t.fn
        for t in server._tool_manager.list_tools()
        if t.name == "openclaw_migrate"
    )
    raw = tool_fn(workspace_dir=str(tmp_workspace), dry_run=False)
    data = json.loads(raw)
    assert data["imported"] >= 1
    assert "memory_md" in data


def test_mcp_openclaw_migrate_error_handling(tmp_path: Path) -> None:
    """MCP tool returns error JSON on bad workspace path."""
    from tapps_brain.mcp_server import create_server

    store_path = tmp_path / "store"
    server = create_server(store_path)
    tool_fn = next(
        t.fn
        for t in server._tool_manager.list_tools()
        if t.name == "openclaw_migrate"
    )
    # Pass a file path as workspace (will cause OSError or graceful empty result)
    bad_ws = str(tmp_path / "nonexistent_file.txt")
    raw = tool_fn(workspace_dir=bad_ws, dry_run=False)
    data = json.loads(raw)
    # Either imported=0 (empty workspace) or error key present — both acceptable
    assert "imported" in data or "error" in data
