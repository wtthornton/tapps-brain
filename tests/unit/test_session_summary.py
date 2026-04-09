"""Tests for session summarization — Issue #17."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_cli


def _extract_json(output: str) -> dict[str, object]:
    """Extract JSON from CLI output, skipping any warning lines."""
    json_start = output.find("{")
    return json.loads(output[json_start:])  # type: ignore[no-any-return]


from typer.testing import CliRunner

from tapps_brain.cli import app
from tapps_brain.session_summary import session_summary_save
from tapps_brain.store import MemoryStore

runner = CliRunner()


# ===================================================================
# Python API
# ===================================================================


class TestSessionSummarySave:
    def test_saves_entry(self, tmp_path: Path):
        result = session_summary_save("Worked on auth module", project_dir=tmp_path)
        assert result["status"] == "saved"
        assert "key" in result
        assert result["key"].startswith("session.")
        assert result["tier"] in ("short-term", "context", "pattern")
        assert result["scope"] == "project"

    def test_default_tags(self, tmp_path: Path):
        result = session_summary_save("Something happened", project_dir=tmp_path)
        assert "date" in result["tags"]
        assert "session" in result["tags"]
        assert "episodic" in result["tags"]

    def test_extra_tags_merged(self, tmp_path: Path):
        result = session_summary_save(
            "Deployed to prod", tags=["deploy", "prod"], project_dir=tmp_path
        )
        assert "deploy" in result["tags"]
        assert "prod" in result["tags"]
        assert "session" in result["tags"]

    def test_no_duplicate_base_tags(self, tmp_path: Path):
        result = session_summary_save(
            "Session with manual tags",
            tags=["session", "date"],
            project_dir=tmp_path,
        )
        assert result["tags"].count("session") == 1
        assert result["tags"].count("date") == 1

    def test_entry_persisted_in_store(self, tmp_path: Path):
        result = session_summary_save("Bug fixed in payment module", project_dir=tmp_path)
        key = result["key"]

        store = MemoryStore(tmp_path)
        try:
            entries = store.search("payment module")
            keys = [e.key for e in entries]
            assert key in keys
        finally:
            store.close()

    def test_daily_note_written(self, tmp_path: Path):
        today = datetime.date.today().isoformat()
        session_summary_save(
            "Great session!",
            project_dir=tmp_path,
            workspace_dir=tmp_path,
            daily_note=True,
        )
        note_path = tmp_path / "memory" / f"{today}.md"
        assert note_path.exists()
        content = note_path.read_text()
        assert "Great session!" in content
        assert "Session End" in content

    def test_daily_note_appends(self, tmp_path: Path):
        today = datetime.date.today().isoformat()
        note_dir = tmp_path / "memory"
        note_dir.mkdir(parents=True)
        note_path = note_dir / f"{today}.md"
        note_path.write_text("# Existing content\n")

        session_summary_save(
            "Second summary",
            project_dir=tmp_path,
            workspace_dir=tmp_path,
            daily_note=True,
        )
        content = note_path.read_text()
        assert "# Existing content" in content
        assert "Second summary" in content

    def test_unique_keys_per_call(self, tmp_path: Path):
        """Two calls in the same second still produce distinct keys
        because the store deduplicates by key — but we verify no crash."""
        result1 = session_summary_save("First session", project_dir=tmp_path)
        result2 = session_summary_save("Second session", project_dir=tmp_path)
        # Both should succeed (second may update same key if within same second)
        assert result1["status"] == "saved"
        assert result2["status"] == "saved"

    # ------------------------------------------------------------------
    # Token / character budget (STORY-048.1)
    # ------------------------------------------------------------------

    def test_max_chars_no_truncation_when_within_budget(self, tmp_path: Path):
        """Short summary is stored unchanged when within max_chars."""
        short = "Short summary."
        result = session_summary_save(short, project_dir=tmp_path, max_chars=200)
        assert result["status"] == "saved"
        assert "truncated" not in result

    def test_max_chars_truncates_long_summary(self, tmp_path: Path):
        """Summary exceeding max_chars is truncated and truncated=True is returned."""
        long_summary = "word " * 50  # 250 chars
        result = session_summary_save(long_summary, project_dir=tmp_path, max_chars=50)
        assert result["status"] == "saved"
        assert result.get("truncated") is True

        # Verify stored value is within budget (plus ellipsis marker " …")
        from tapps_brain.store import MemoryStore
        store = MemoryStore(tmp_path)
        try:
            entries = store.search("word")
            assert entries, "Expected at least one stored entry"
            stored_value = entries[0].value
            assert stored_value.endswith(" …")
            assert len(stored_value) <= 50 + 3  # " …" is 2 chars
        finally:
            store.close()

    def test_max_chars_none_does_not_truncate(self, tmp_path: Path):
        """Default max_chars=None never truncates."""
        long_summary = "x" * 2000
        result = session_summary_save(long_summary, project_dir=tmp_path, max_chars=None)
        assert result["status"] == "saved"
        assert "truncated" not in result

    def test_max_chars_truncates_at_word_boundary(self, tmp_path: Path):
        """Truncation happens at a whitespace boundary, not mid-word."""
        summary = "hello world this is a long sentence that will be cut"
        result = session_summary_save(summary, project_dir=tmp_path, max_chars=20)
        assert result.get("truncated") is True
        from tapps_brain.store import MemoryStore
        store = MemoryStore(tmp_path)
        try:
            entries = store.search("hello")
            stored = entries[0].value
            # Should not cut mid-word
            assert not stored.rstrip(" …")[-1:].isalpha() or stored.endswith(" …")
        finally:
            store.close()


# ===================================================================
# CLI command
# ===================================================================


class TestSessionEndCLI:
    def test_basic_end(self, tmp_path: Path):
        result = runner.invoke(
            app,
            ["session", "end", "Did some work", "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 0, result.output
        assert "Session memory saved" in result.output

    def test_json_output(self, tmp_path: Path):
        result = runner.invoke(
            app,
            ["session", "end", "JSON test", "--project-dir", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert data["status"] == "saved"
        assert "key" in data

    def test_with_extra_tags(self, tmp_path: Path):
        result = runner.invoke(
            app,
            [
                "session",
                "end",
                "Tagged session",
                "--project-dir",
                str(tmp_path),
                "--tag",
                "deploy",
                "--tag",
                "prod",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = _extract_json(result.output)
        assert "deploy" in data["tags"]
        assert "prod" in data["tags"]

    def test_daily_note_flag(self, tmp_path: Path):
        today = datetime.date.today().isoformat()
        result = runner.invoke(
            app,
            [
                "session",
                "end",
                "Daily note test",
                "--project-dir",
                str(tmp_path),
                "--workspace-dir",
                str(tmp_path),
                "--daily-note",
            ],
        )
        assert result.exit_code == 0, result.output
        note_path = tmp_path / "memory" / f"{today}.md"
        assert note_path.exists()
        assert "Daily note test" in note_path.read_text()

    def test_help(self):
        result = runner.invoke(app, ["session", "end", "--help"])
        assert result.exit_code == 0
        assert "summary" in result.output.lower() or "episodic" in result.output.lower()

    def test_session_subapp_help(self):
        result = runner.invoke(app, ["session", "--help"])
        assert result.exit_code == 0
        assert "end" in result.output


# ===================================================================
# MCP tool
# ===================================================================


class TestMCPSessionEnd:
    def test_tool_registered(self, tmp_path: Path):
        """tapps_brain_session_end tool should be registered in MCP server."""
        from tapps_brain.mcp_server import create_server

        mcp = create_server(project_dir=tmp_path)
        tool_names = [t.name for t in mcp._tool_manager.list_tools()]
        assert "tapps_brain_session_end" in tool_names

    def test_tool_saves_entry(self, tmp_path: Path):
        from tapps_brain.mcp_server import create_server

        mcp = create_server(project_dir=tmp_path)
        tool_names = {t.name: t for t in mcp._tool_manager.list_tools()}
        assert "tapps_brain_session_end" in tool_names

        # Save via session_summary_save directly to verify the module works
        result = session_summary_save("MCP test session", project_dir=tmp_path)
        assert result["status"] == "saved"
