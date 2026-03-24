"""Tests for the tapps-brain CLI tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.requires_cli

from typer.testing import CliRunner

from tapps_brain.cli import app
from tapps_brain.store import MemoryStore

runner = CliRunner()


@pytest.fixture()
def store(tmp_path: Path):
    """Create a MemoryStore with some test data."""
    s = MemoryStore(tmp_path)
    s.save(key="tech-stack", value="We use PostgreSQL and Python", tier="architectural")
    s.save(key="deploy-process", value="We deploy via GitHub Actions", tier="procedural")
    s.save(key="api-pattern", value="All APIs use REST with JSON", tier="pattern")
    return s


@pytest.fixture()
def project_dir(store, tmp_path: Path):
    """Return the project dir path string for CLI --project-dir."""
    store.close()
    return str(tmp_path)


# ===================================================================
# Version / Help
# ===================================================================


class TestVersionHelp:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "tapps-brain" in result.stdout

    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "store" in result.stdout
        assert "memory" in result.stdout
        assert "federation" in result.stdout
        assert "maintenance" in result.stdout

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # typer returns exit code 0 or 2 depending on version for no_args_is_help
        assert "Usage" in result.stdout

    def test_subgroup_help(self):
        """All CLI subgroups respond to --help."""
        for subgroup in [
            "store",
            "memory",
            "federation",
            "maintenance",
            "profile",
            "hive",
            "agent",
            "openclaw",
            "feedback",
            "diagnostics",
        ]:
            result = runner.invoke(app, [subgroup, "--help"])
            assert result.exit_code == 0, f"{subgroup} --help failed"
            assert "Usage" in result.stdout, f"{subgroup} --help missing Usage"


# ===================================================================
# Store commands
# ===================================================================


class TestStoreCommands:
    def test_stats(self, project_dir):
        result = runner.invoke(app, ["store", "stats", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Entries: 3 / 500" in result.stdout
        assert "Schema: v11" in result.stdout

    def test_stats_json(self, project_dir):
        result = runner.invoke(app, ["store", "stats", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_entries"] == 3
        assert data["schema_version"] == 11

    def test_list(self, project_dir):
        result = runner.invoke(app, ["store", "list", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "tech-stack" in result.stdout
        assert "deploy-process" in result.stdout
        assert "api-pattern" in result.stdout
        assert "3 entries" in result.stdout

    def test_list_tier_filter(self, project_dir):
        result = runner.invoke(
            app, ["store", "list", "--project-dir", project_dir, "--tier", "architectural"]
        )
        assert result.exit_code == 0
        assert "tech-stack" in result.stdout
        assert "deploy-process" not in result.stdout

    def test_list_json(self, project_dir):
        result = runner.invoke(app, ["store", "list", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 3

    def test_list_scope_filter(self, project_dir):
        result = runner.invoke(
            app, ["store", "list", "--project-dir", project_dir, "--scope", "project"]
        )
        assert result.exit_code == 0
        # All entries are project-scoped by default, so all 3 should appear
        assert "3 entries" in result.stdout

    def test_search(self, project_dir):
        result = runner.invoke(app, ["store", "search", "PostgreSQL", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "tech-stack" in result.stdout

    def test_search_json(self, project_dir):
        result = runner.invoke(
            app, ["store", "search", "PostgreSQL", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_search_no_results(self, project_dir):
        result = runner.invoke(
            app,
            ["store", "search", "xyzzy-nonexistent-term-99", "--project-dir", project_dir],
        )
        assert result.exit_code == 0
        assert "0 results" in result.stdout


# ===================================================================
# Memory commands
# ===================================================================


class TestMemoryCommands:
    def test_show(self, project_dir):
        result = runner.invoke(app, ["memory", "show", "tech-stack", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "tech-stack" in result.stdout
        assert "PostgreSQL" in result.stdout
        assert "architectural" in result.stdout

    def test_show_json(self, project_dir):
        result = runner.invoke(
            app, ["memory", "show", "tech-stack", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["key"] == "tech-stack"
        assert "PostgreSQL" in data["value"]

    def test_show_not_found(self, project_dir):
        result = runner.invoke(app, ["memory", "show", "nonexistent", "--project-dir", project_dir])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_history(self, tmp_path: Path):
        s = MemoryStore(tmp_path)
        s.save(key="pricing", value="$297/mo", tier="context")
        s.supersede("pricing", "$397/mo")
        s.close()

        result = runner.invoke(
            app, ["memory", "history", "pricing", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "2 versions" in result.stdout
        assert "superseded" in result.stdout

    def test_history_json(self, tmp_path: Path):
        s = MemoryStore(tmp_path)
        s.save(key="pricing", value="$297/mo", tier="context")
        s.supersede("pricing", "$397/mo")
        s.close()

        result = runner.invoke(
            app, ["memory", "history", "pricing", "--project-dir", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 2

    def test_search(self, project_dir):
        result = runner.invoke(app, ["memory", "search", "deploy", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "results" in result.stdout

    def test_search_json(self, project_dir):
        result = runner.invoke(
            app, ["memory", "search", "deploy", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)


# ===================================================================
# Import/Export commands
# ===================================================================


class TestImportExport:
    def test_export_json_stdout(self, project_dir):
        result = runner.invoke(app, ["export", "--project-dir", project_dir, "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 3

    def test_export_json_file(self, project_dir, tmp_path: Path):
        out = tmp_path / "export.json"
        result = runner.invoke(
            app,
            ["export", "--project-dir", project_dir, "--format", "json", "--output", str(out)],
        )
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert len(data) == 3

    def test_export_markdown(self, project_dir):
        result = runner.invoke(
            app, ["export", "--project-dir", project_dir, "--format", "markdown"]
        )
        assert result.exit_code == 0
        assert "#" in result.stdout  # Markdown headers

    def test_export_tier_filter(self, project_dir):
        result = runner.invoke(
            app,
            ["export", "--project-dir", project_dir, "--format", "json", "--tier", "architectural"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["key"] == "tech-stack"

    def test_import_json(self, tmp_path: Path):
        # Create source store and export
        src = MemoryStore(tmp_path / "src")
        src.save(key="imported-fact", value="This was imported", tier="pattern")
        src.close()

        # Export to file
        export_file = tmp_path / "data.json"
        result = runner.invoke(
            app,
            ["export", "-d", str(tmp_path / "src"), "-f", "json", "-o", str(export_file)],
        )
        assert result.exit_code == 0

        # Import into new store
        dest = tmp_path / "dest"
        dest.mkdir()
        result = runner.invoke(
            app,
            ["import", str(export_file), "--project-dir", str(dest)],
        )
        assert result.exit_code == 0
        assert "Imported 1" in result.stdout

    def test_import_dry_run(self, tmp_path: Path):
        export_file = tmp_path / "data.json"
        export_file.write_text(json.dumps([{"key": "a", "value": "b"}]))

        result = runner.invoke(
            app,
            ["import", str(export_file), "--project-dir", str(tmp_path), "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Would import 1" in result.stdout

    def test_import_not_found(self, tmp_path: Path):
        result = runner.invoke(
            app,
            ["import", str(tmp_path / "nope.json"), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_import_invalid_json(self, tmp_path: Path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not valid json {{{{", encoding="utf-8")
        result = runner.invoke(
            app,
            ["import", str(bad_file), "--project-dir", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert "invalid json" in result.output.lower()

    def test_import_skip_duplicates(self, project_dir, tmp_path: Path):
        export_file = tmp_path / "data.json"
        export_file.write_text(
            json.dumps(
                [
                    {"key": "tech-stack", "value": "duplicate"},
                    {"key": "new-thing", "value": "new entry"},
                ]
            )
        )

        result = runner.invoke(
            app,
            ["import", str(export_file), "--project-dir", project_dir],
        )
        assert result.exit_code == 0
        assert "Imported 1" in result.stdout
        assert "skipped 1" in result.stdout

    def test_import_json_output(self, tmp_path: Path):
        export_file = tmp_path / "data.json"
        export_file.write_text(json.dumps([{"key": "a", "value": "b"}]))

        result = runner.invoke(
            app,
            ["import", str(export_file), "--project-dir", str(tmp_path), "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["imported"] == 1


# ===================================================================
# Federation commands
# ===================================================================


class TestFederationCommands:
    def test_status(self):
        result = runner.invoke(app, ["federation", "status"])
        assert result.exit_code == 0
        assert "Hub:" in result.stdout

    def test_status_json(self):
        result = runner.invoke(app, ["federation", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "hub_path" in data
        assert "projects" in data

    def test_list(self):
        result = runner.invoke(app, ["federation", "list"])
        assert result.exit_code == 0

    def test_list_json(self):
        result = runner.invoke(app, ["federation", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestFederationSubscribeCommand:
    """Tests for federation subscribe command (016-A)."""

    def test_subscribe_happy_path(self, tmp_path: Path) -> None:
        """Subscribe to a project returns success message."""
        mock_config = MagicMock()
        mock_config.subscriptions = [MagicMock()]  # 1 subscription

        with patch("tapps_brain.federation.add_subscription", return_value=mock_config):
            result = runner.invoke(
                app,
                ["federation", "subscribe", "source-project", "--project-dir", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "Subscribed" in result.stdout

    def test_subscribe_json_output(self, tmp_path: Path) -> None:
        """Subscribe --json returns expected keys."""
        mock_config = MagicMock()
        mock_config.subscriptions = [MagicMock()]

        with patch("tapps_brain.federation.add_subscription", return_value=mock_config):
            result = runner.invoke(
                app,
                [
                    "federation",
                    "subscribe",
                    "source-project",
                    "--project-dir",
                    str(tmp_path),
                    "--json",
                ],
            )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "subscriber" in data
        assert "source" in data
        assert data["source"] == "source-project"
        assert data["subscriptions"] == 1

    def test_subscribe_nonexistent_project_error(self, tmp_path: Path) -> None:
        """Subscribe raises ValueError for an unregistered subscriber — exits non-zero."""
        with patch(
            "tapps_brain.federation.add_subscription",
            side_effect=ValueError("Subscriber 'xxx' is not registered in the federation hub"),
        ):
            result = runner.invoke(
                app,
                [
                    "federation",
                    "subscribe",
                    "nonexistent-project",
                    "--project-dir",
                    str(tmp_path),
                ],
            )
        assert result.exit_code != 0


class TestFederationUnsubscribeCommand:
    """Tests for federation unsubscribe command (016-A)."""

    def test_unsubscribe_happy_path(self, tmp_path: Path) -> None:
        """Unsubscribe removes a matching subscription."""
        from tapps_brain.federation import FederationConfig, FederationSubscription

        project_name = tmp_path.name
        config = FederationConfig(
            subscriptions=[
                FederationSubscription(subscriber=project_name, sources=["source-project"])
            ]
        )

        with (
            patch("tapps_brain.federation.load_federation_config", return_value=config),
            patch("tapps_brain.federation.save_federation_config"),
        ):
            result = runner.invoke(
                app,
                [
                    "federation",
                    "unsubscribe",
                    "source-project",
                    "--project-dir",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        assert "Unsubscribed" in result.stdout

    def test_unsubscribe_unknown_project(self, tmp_path: Path) -> None:
        """Unsubscribe with no existing subscription shows informative message."""
        from tapps_brain.federation import FederationConfig

        config = FederationConfig(subscriptions=[])

        with (
            patch("tapps_brain.federation.load_federation_config", return_value=config),
            patch("tapps_brain.federation.save_federation_config"),
        ):
            result = runner.invoke(
                app,
                [
                    "federation",
                    "unsubscribe",
                    "no-such-project",
                    "--project-dir",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        assert "No subscription found" in result.stdout

    def test_unsubscribe_json_output(self, tmp_path: Path) -> None:
        """Unsubscribe --json returns removed count."""
        from tapps_brain.federation import FederationConfig, FederationSubscription

        project_name = tmp_path.name
        config = FederationConfig(
            subscriptions=[
                FederationSubscription(subscriber=project_name, sources=["source-project"])
            ]
        )

        with (
            patch("tapps_brain.federation.load_federation_config", return_value=config),
            patch("tapps_brain.federation.save_federation_config"),
        ):
            result = runner.invoke(
                app,
                [
                    "federation",
                    "unsubscribe",
                    "source-project",
                    "--project-dir",
                    str(tmp_path),
                    "--json",
                ],
            )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "removed" in data
        assert data["removed"] == 1


class TestFederationPublishCommand:
    """Tests for federation publish command (016-A)."""

    def test_publish_happy_path(self, project_dir: str) -> None:
        """Publish syncs entries to hub and reports published count."""
        mock_hub = MagicMock()
        mock_hub.close = MagicMock()

        with (
            patch("tapps_brain.federation.register_project"),
            patch("tapps_brain.federation.FederatedStore", return_value=mock_hub),
            patch(
                "tapps_brain.federation.sync_to_hub",
                return_value={"published": 3, "skipped": 0},
            ),
        ):
            result = runner.invoke(
                app,
                ["federation", "publish", "--project-dir", project_dir],
            )
        assert result.exit_code == 0
        assert "Published" in result.stdout
        assert "3" in result.stdout

    def test_publish_json_output(self, project_dir: str) -> None:
        """Publish --json returns published/skipped counts."""
        mock_hub = MagicMock()
        mock_hub.close = MagicMock()

        with (
            patch("tapps_brain.federation.register_project"),
            patch("tapps_brain.federation.FederatedStore", return_value=mock_hub),
            patch(
                "tapps_brain.federation.sync_to_hub",
                return_value={"published": 2, "skipped": 1},
            ),
        ):
            result = runner.invoke(
                app,
                ["federation", "publish", "--project-dir", project_dir, "--json"],
            )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["published"] == 2
        assert data["skipped"] == 1


# ===================================================================
# Maintenance commands
# ===================================================================


class TestMaintenanceCommands:
    def test_consolidate(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "consolidate", "--project-dir", project_dir, "--force"]
        )
        assert result.exit_code == 0

    def test_consolidate_json(self, project_dir):
        result = runner.invoke(
            app,
            ["maintenance", "consolidate", "--project-dir", project_dir, "--force", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "scanned" in data

    def test_gc_dry_run(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "gc", "--project-dir", project_dir, "--dry-run"]
        )
        assert result.exit_code == 0
        assert "Would archive" in result.stdout

    def test_gc(self, project_dir):
        result = runner.invoke(app, ["maintenance", "gc", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Archived" in result.stdout

    def test_gc_json(self, project_dir):
        result = runner.invoke(app, ["maintenance", "gc", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "archived" in data

    def test_gc_archives_stale_entries(self, tmp_path: Path) -> None:
        """016-B: GC non-dry-run archives session-scoped entries older than 7 days."""
        import sqlite3
        from datetime import UTC, datetime, timedelta

        # Create store and save a session-scoped entry
        s = MemoryStore(tmp_path)
        s.save(key="old-session-fact", value="temporary session info", scope="session")
        s.close()

        # Back-date updated_at to 8 days ago so GC identifies it as stale
        db_path = tmp_path / ".tapps-brain" / "memory" / "memory.db"
        old_ts = (datetime.now(tz=UTC) - timedelta(days=8)).isoformat()
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "UPDATE memories SET updated_at = ? WHERE key = ?",
            (old_ts, "old-session-fact"),
        )
        conn.commit()
        conn.close()

        # Run gc non-dry-run
        result = runner.invoke(app, ["maintenance", "gc", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0
        # Verify at least 1 entry was archived
        assert "Archived 1" in result.stdout

        # Verify archive file was written
        archive_path = tmp_path / ".tapps-brain" / "memory" / "archive.jsonl"
        assert archive_path.exists()
        archive_content = archive_path.read_text()
        assert "old-session-fact" in archive_content

    def test_migrate(self, project_dir):
        result = runner.invoke(app, ["maintenance", "migrate", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "v11" in result.stdout

    def test_migrate_dry_run(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "migrate", "--project-dir", project_dir, "--dry-run"]
        )
        assert result.exit_code == 0
        assert "v11" in result.stdout

    def test_migrate_json(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "migrate", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["schema_version"] == 11


class TestFlywheelCli:
    """EPIC-031 flywheel commands."""

    def test_flywheel_process_json(self, project_dir: str) -> None:
        result = runner.invoke(app, ["flywheel", "process", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "processed_events" in data

    def test_flywheel_process_text(self, project_dir: str) -> None:
        result = runner.invoke(app, ["flywheel", "process", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "processed_events" in result.stdout

    def test_flywheel_gaps_json(self, project_dir: str) -> None:
        result = runner.invoke(
            app, ["flywheel", "gaps", "--project-dir", project_dir, "--json", "--limit", "5"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "gaps" in data

    def test_flywheel_gaps_table(self, project_dir: str) -> None:
        result = runner.invoke(
            app, ["flywheel", "gaps", "--project-dir", project_dir, "--limit", "3"]
        )
        assert result.exit_code == 0

    def test_flywheel_gaps_empty(self, tmp_path: Path) -> None:
        MemoryStore(tmp_path).close()
        result = runner.invoke(app, ["flywheel", "gaps", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "No clustered gaps" in result.stdout

    def test_flywheel_report_markdown(self, project_dir: str) -> None:
        result = runner.invoke(app, ["flywheel", "report", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Quality report" in result.stdout

    def test_flywheel_report_json(self, project_dir: str) -> None:
        result = runner.invoke(
            app,
            [
                "flywheel",
                "report",
                "--project-dir",
                project_dir,
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "rendered_text" in data

    def test_flywheel_evaluate_table(self, project_dir: str) -> None:
        suite = Path(__file__).resolve().parents[1] / "eval"
        result = runner.invoke(
            app,
            [
                "flywheel",
                "evaluate",
                str(suite),
                "--project-dir",
                project_dir,
                "--format",
                "table",
            ],
        )
        assert result.exit_code == 0
        assert "MRR=" in result.stdout

    def test_flywheel_hive_feedback_json(self, project_dir: str) -> None:
        result = runner.invoke(
            app, ["flywheel", "hive-feedback", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["process"]["skipped"] is True

    def test_flywheel_hive_feedback_text(self, project_dir: str) -> None:
        result = runner.invoke(app, ["flywheel", "hive-feedback", "--project-dir", project_dir])
        assert result.exit_code == 0

    def test_flywheel_evaluate_bad_suffix(self, project_dir: str, tmp_path: Path) -> None:
        bad = tmp_path / "suite.txt"
        bad.write_text("x", encoding="utf-8")
        result = runner.invoke(
            app,
            ["flywheel", "evaluate", str(bad), "--project-dir", project_dir],
        )
        assert result.exit_code == 1

    def test_flywheel_evaluate_missing_path(self, project_dir: str) -> None:
        result = runner.invoke(
            app,
            [
                "flywheel",
                "evaluate",
                "/nonexistent/beir",
                "--project-dir",
                project_dir,
            ],
        )
        assert result.exit_code == 1

    def test_flywheel_evaluate_json(self, project_dir: str) -> None:
        suite = Path(__file__).resolve().parents[1] / "eval"
        result = runner.invoke(
            app,
            [
                "flywheel",
                "evaluate",
                str(suite),
                "--project-dir",
                project_dir,
                "--format",
                "json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "mrr" in data


class TestMaintenanceGcConfigCommand:
    """Tests for maintenance gc-config CLI command."""

    def test_gc_config_show(self, project_dir):
        result = runner.invoke(app, ["maintenance", "gc-config", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "floor_retention_days" in result.stdout
        assert "session_expiry_days" in result.stdout
        assert "contradicted_threshold" in result.stdout

    def test_gc_config_json(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "gc-config", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "floor_retention_days" in data
        assert "session_expiry_days" in data
        assert "contradicted_threshold" in data

    def test_gc_config_set_floor(self, project_dir):
        result = runner.invoke(
            app,
            [
                "maintenance",
                "gc-config",
                "--project-dir",
                project_dir,
                "--floor-retention-days",
                "60",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["floor_retention_days"] == 60
        assert data["status"] == "updated"


class TestMaintenanceConsolidationConfigCommand:
    """Tests for maintenance consolidation-config CLI command."""

    def test_consolidation_config_show(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "consolidation-config", "--project-dir", project_dir]
        )
        assert result.exit_code == 0
        assert "enabled" in result.stdout
        assert "threshold" in result.stdout

    def test_consolidation_config_json(self, project_dir):
        result = runner.invoke(
            app,
            ["maintenance", "consolidation-config", "--project-dir", project_dir, "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "enabled" in data
        assert "threshold" in data
        assert "min_entries" in data

    def test_consolidation_config_set_threshold(self, project_dir):
        result = runner.invoke(
            app,
            [
                "maintenance",
                "consolidation-config",
                "--project-dir",
                project_dir,
                "--threshold",
                "0.85",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["threshold"] == 0.85
        assert data["status"] == "updated"


# ===================================================================
# Recall command
# ===================================================================


class TestRecallCommand:
    def test_recall(self, project_dir):
        result = runner.invoke(
            app, ["recall", "what database do we use", "--project-dir", project_dir]
        )
        assert result.exit_code == 0
        assert "Recalled" in result.stdout

    def test_recall_json(self, project_dir):
        result = runner.invoke(
            app,
            ["recall", "what database do we use", "--project-dir", project_dir, "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "memory_count" in data
        assert "token_count" in data
        assert "recall_time_ms" in data

    def test_recall_with_token_budget(self, project_dir):
        result = runner.invoke(
            app,
            ["recall", "deploy process", "--project-dir", project_dir, "--max-tokens", "500"],
        )
        assert result.exit_code == 0


# ===================================================================
# Helpers
# ===================================================================


class TestHelpers:
    def test_resolve_project_dir_default(self):
        from tapps_brain.cli import _resolve_project_dir

        result = _resolve_project_dir(None)
        assert result == Path.cwd().resolve()

    def test_resolve_project_dir_explicit(self, tmp_path: Path):
        from tapps_brain.cli import _resolve_project_dir

        result = _resolve_project_dir(tmp_path)
        assert result == tmp_path.resolve()


# ===================================================================
# Health command (EPIC-007)
# ===================================================================


class TestHealthCommand:
    def test_health(self, project_dir):
        result = runner.invoke(app, ["maintenance", "health", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Entries:" in result.stdout
        assert "Schema:" in result.stdout
        assert "Federation:" in result.stdout

    def test_health_json(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "health", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "entry_count" in data
        assert "schema_version" in data
        assert "tier_distribution" in data
        assert data["entry_count"] == 3


# ===================================================================
# Metrics command (EPIC-007)
# ===================================================================


class TestMetricsCommand:
    def test_metrics(self, project_dir):
        result = runner.invoke(app, ["store", "metrics", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Counters:" in result.stdout

    def test_metrics_json(self, project_dir):
        result = runner.invoke(app, ["store", "metrics", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "counters" in data
        assert "histograms" in data


# ===================================================================
# Profile commands (EPIC-010)
# ===================================================================


class TestProfileCommands:
    """Tests for profile show, list, set, and layers commands."""

    def test_profile_show(self, project_dir):
        result = runner.invoke(app, ["profile", "show", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Profile" in result.stdout

    def test_profile_show_json(self, project_dir):
        result = runner.invoke(app, ["profile", "show", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "name" in data
        assert "layers" in data
        assert isinstance(data["layers"], list)

    def test_profile_list(self):
        result = runner.invoke(app, ["profile", "list"])
        assert result.exit_code == 0
        assert "repo-brain" in result.stdout

    def test_profile_list_json(self):
        result = runner.invoke(app, ["profile", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        names = [p["name"] for p in data]
        assert "repo-brain" in names

    def test_profile_set(self, project_dir):
        result = runner.invoke(app, ["profile", "set", "repo-brain", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "repo-brain" in result.stdout

    def test_profile_layers(self, project_dir):
        result = runner.invoke(app, ["profile", "layers", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Profile" in result.stdout

    def test_profile_layers_json(self, project_dir):
        result = runner.invoke(app, ["profile", "layers", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "name" in data[0]
        assert "half_life_days" in data[0]


# ===================================================================
# Hive commands (EPIC-011)
# ===================================================================


class TestHiveCommands:
    """Tests for hive status and search CLI commands."""

    def test_hive_status(self):
        result = runner.invoke(app, ["hive", "status"])
        assert result.exit_code == 0
        # Either shows entry count or empty state
        assert "Hive" in result.stdout or "entries" in result.stdout

    def test_hive_status_json(self):
        result = runner.invoke(app, ["hive", "status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "namespaces" in data
        assert "total_entries" in data
        assert "agents" in data

    def test_hive_search_no_results(self):
        result = runner.invoke(app, ["hive", "search", "xyzzy-nonexistent-query-12345"])
        assert result.exit_code == 0
        assert "No results" in result.stdout or result.stdout == ""

    def test_hive_search_json(self):
        result = runner.invoke(app, ["hive", "search", "test-query", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "results" in data
        assert "count" in data


# ===================================================================
# Agent commands (EPIC-011)
# ===================================================================


class TestAgentCommands:
    """Tests for agent register and list CLI commands."""

    def test_agent_register(self):
        result = runner.invoke(
            app,
            ["agent", "register", "test-cli-agent", "--profile", "repo-brain", "--skills", ""],
        )
        assert result.exit_code == 0
        assert "test-cli-agent" in result.stdout

    def test_agent_list(self):
        result = runner.invoke(app, ["agent", "list"])
        # Either shows agents or "No registered agents."
        assert result.exit_code == 0

    def test_agent_list_json(self):
        result = runner.invoke(app, ["agent", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert "agents" in data
        assert "count" in data


class TestAgentCreateCommand:
    """Tests for agent create CLI command (014-B)."""

    def test_agent_create_valid_profile(self):
        result = runner.invoke(
            app,
            ["agent", "create", "test-create-agent", "--profile", "repo-brain"],
        )
        assert result.exit_code == 0
        assert "test-create-agent" in result.stdout
        assert "repo-brain" in result.stdout

    def test_agent_create_json(self):
        result = runner.invoke(
            app,
            ["agent", "create", "test-create-json-agent", "--profile", "repo-brain", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["created"] is True
        assert data["agent_id"] == "test-create-json-agent"
        assert data["profile"] == "repo-brain"
        assert "namespace" in data
        assert "profile_summary" in data
        ps = data["profile_summary"]
        assert "name" in ps
        assert "version" in ps
        assert "layers" in ps

    def test_agent_create_with_skills(self):
        result = runner.invoke(
            app,
            [
                "agent",
                "create",
                "test-skilled-agent",
                "--profile",
                "repo-brain",
                "--skills",
                "coding,review",
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["skills"] == ["coding", "review"]

    def test_agent_create_invalid_profile(self):
        # Click 8.2+ Typer CliRunner keeps stdout/stderr separate by default (no mix_stderr kwarg).
        r = CliRunner()
        result = r.invoke(
            app,
            ["agent", "create", "bad-agent", "--profile", "nonexistent-profile-xyz"],
        )
        assert result.exit_code == 1
        combined = f"{result.stdout}\n{result.stderr}"
        assert "not found" in combined
        # 016-B: error message must list available profiles
        assert "Available profiles" in combined

    def test_agent_create_invalid_profile_json(self):
        result = runner.invoke(
            app,
            ["agent", "create", "bad-agent", "--profile", "nonexistent-profile-xyz", "--json"],
        )
        # exit code may be 1; JSON output goes to stdout
        data = json.loads(result.stdout)
        assert data["error"] == "invalid_profile"
        assert "available_profiles" in data

    def test_agent_delete_existing_agent(self):
        """agent delete removes a registered agent successfully."""
        # First register the agent, then delete it
        runner.invoke(
            app,
            ["agent", "register", "del-cli-agent", "--profile", "repo-brain"],
        )
        result = runner.invoke(app, ["agent", "delete", "del-cli-agent"])
        assert result.exit_code == 0
        assert "Deleted" in result.stdout

    def test_agent_delete_missing_agent_exits_nonzero(self):
        """agent delete exits with code 1 for an agent that doesn't exist."""
        result = runner.invoke(app, ["agent", "delete", "no-such-agent-xyz-99999"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    def test_agent_delete_json_output(self):
        """agent delete --json returns deleted flag."""
        runner.invoke(
            app,
            ["agent", "register", "del-json-cli-agent", "--profile", "repo-brain"],
        )
        result = runner.invoke(app, ["agent", "delete", "del-json-cli-agent", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["deleted"] is True
        assert data["agent_id"] == "del-json-cli-agent"


# ===================================================================
# Memory knowledge-graph commands (015-B)
# ===================================================================


@pytest.fixture()
def kg_project_dir(tmp_path: Path):
    """Create a MemoryStore with entries that produce knowledge-graph relations."""
    s = MemoryStore(tmp_path)
    # "uses" pattern → triggers extract_relations → subject=Python, predicate=uses, obj=Django
    s.save(key="python-stack", value="Python uses Django for web apps", tier="architectural")
    # "manages" pattern → subject=TeamAlpha, predicate=manages, obj=database
    s.save(key="team-ownership", value="TeamAlpha manages database cluster", tier="pattern")
    s.close()
    return str(tmp_path)


class TestMemoryRelationsCommand:
    def test_relations_no_results(self, project_dir):
        """Entry with no extractable relations returns friendly message."""
        result = runner.invoke(
            app, ["memory", "relations", "tech-stack", "--project-dir", project_dir]
        )
        assert result.exit_code == 0
        assert "No relations found" in result.stdout

    def test_relations_table_output(self, kg_project_dir):
        result = runner.invoke(
            app, ["memory", "relations", "python-stack", "--project-dir", kg_project_dir]
        )
        assert result.exit_code == 0
        # Should contain relation details
        assert "Python" in result.stdout or "python" in result.stdout.lower()
        assert "uses" in result.stdout.lower()
        assert "relations" in result.stdout

    def test_relations_json_output(self, kg_project_dir):
        result = runner.invoke(
            app,
            ["memory", "relations", "python-stack", "--project-dir", kg_project_dir, "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # At least one relation extracted
        assert len(data) >= 1
        rel = data[0]
        assert "subject" in rel
        assert "predicate" in rel
        assert "object_entity" in rel
        assert "confidence" in rel

    def test_relations_key_not_found(self, project_dir):
        """Non-existent key should return empty relations (not error)."""
        result = runner.invoke(
            app, ["memory", "relations", "nonexistent-key", "--project-dir", project_dir]
        )
        assert result.exit_code == 0
        assert "No relations found" in result.stdout


class TestMemoryRelatedCommand:
    def test_related_no_results(self, project_dir):
        """Entry with no graph neighbors returns friendly message."""
        result = runner.invoke(
            app, ["memory", "related", "tech-stack", "--project-dir", project_dir]
        )
        assert result.exit_code == 0
        assert "No related entries found" in result.stdout

    def test_related_table_output(self, kg_project_dir):
        result = runner.invoke(
            app, ["memory", "related", "python-stack", "--project-dir", kg_project_dir]
        )
        assert result.exit_code == 0

    def test_related_json_output(self, kg_project_dir):
        result = runner.invoke(
            app,
            ["memory", "related", "python-stack", "--project-dir", kg_project_dir, "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # Each result has key and hops
        for item in data:
            assert "key" in item
            assert "hops" in item

    def test_related_hops_option(self, kg_project_dir):
        result = runner.invoke(
            app,
            [
                "memory",
                "related",
                "python-stack",
                "--hops",
                "1",
                "--project-dir",
                kg_project_dir,
            ],
        )
        assert result.exit_code == 0

    def test_related_key_not_found(self, project_dir):
        result = runner.invoke(
            app, ["memory", "related", "nonexistent-key", "--project-dir", project_dir]
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()


# ===================================================================
# Memory audit command (015-D)
# ===================================================================


@pytest.fixture()
def audit_project_dir(tmp_path: Path):
    """Create a MemoryStore with some saved entries to generate audit events."""
    s = MemoryStore(tmp_path)
    s.save(key="audit-key-one", value="First entry for audit testing", tier="architectural")
    s.save(key="audit-key-two", value="Second entry for audit testing", tier="pattern")
    s.save(key="audit-key-one", value="Updated first entry", tier="architectural")  # re-save
    s.close()
    return str(tmp_path)


class TestMemoryAuditCommand:
    def test_audit_no_events_empty_store(self, tmp_path):
        """Empty store with no audit log returns friendly message."""
        result = runner.invoke(app, ["memory", "audit", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "no audit events found" in result.stdout

    def test_audit_all_events_table(self, audit_project_dir):
        """Without filters, returns all events in table format."""
        result = runner.invoke(app, ["memory", "audit", "--project-dir", audit_project_dir])
        assert result.exit_code == 0
        # Should have header columns
        assert "TIMESTAMP" in result.stdout
        assert "EVENT_TYPE" in result.stdout
        assert "KEY" in result.stdout
        assert "events" in result.stdout

    def test_audit_filter_by_key(self, audit_project_dir):
        """Filtering by key returns only events for that key."""
        result = runner.invoke(
            app,
            ["memory", "audit", "audit-key-one", "--project-dir", audit_project_dir],
        )
        assert result.exit_code == 0
        assert "audit-key-one" in result.stdout
        # The other key should not appear
        assert "audit-key-two" not in result.stdout

    def test_audit_filter_by_type(self, audit_project_dir):
        """Filtering by event type returns only matching events."""
        result = runner.invoke(
            app,
            ["memory", "audit", "--type", "save", "--project-dir", audit_project_dir],
        )
        assert result.exit_code == 0
        assert "save" in result.stdout

    def test_audit_json_output(self, audit_project_dir):
        """JSON output returns list of event dicts."""
        result = runner.invoke(
            app,
            ["memory", "audit", "--project-dir", audit_project_dir, "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) >= 1
        event = data[0]
        assert "timestamp" in event
        assert "event_type" in event
        assert "key" in event

    def test_audit_json_filter_by_key(self, audit_project_dir):
        """JSON output with key filter returns only events for that key."""
        result = runner.invoke(
            app,
            [
                "memory",
                "audit",
                "audit-key-two",
                "--project-dir",
                audit_project_dir,
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # All returned events should be for the filtered key
        for event in data:
            assert event["key"] == "audit-key-two"

    def test_audit_limit_option(self, audit_project_dir):
        """Limit option caps the number of returned events."""
        result = runner.invoke(
            app,
            ["memory", "audit", "--limit", "1", "--project-dir", audit_project_dir, "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) <= 1

    def test_audit_since_filter(self, audit_project_dir):
        """Since filter with future date returns no events."""
        result = runner.invoke(
            app,
            [
                "memory",
                "audit",
                "--since",
                "2099-01-01",
                "--project-dir",
                audit_project_dir,
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data == []

    def test_audit_until_filter(self, audit_project_dir):
        """Until filter with past date returns no events."""
        result = runner.invoke(
            app,
            [
                "memory",
                "audit",
                "--until",
                "2000-01-01",
                "--project-dir",
                audit_project_dir,
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data == []


# ---------------------------------------------------------------------------
# Tag management CLI commands (015-F)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tags_project_dir(tmp_path: Path):
    """Create a MemoryStore with tagged entries."""
    s = MemoryStore(tmp_path)
    s.save(key="alpha", value="First entry", tier="architectural", tags=["python", "core"])
    s.save(key="beta", value="Second entry", tier="pattern", tags=["python", "api"])
    s.save(key="gamma", value="Third entry", tier="procedural", tags=["core"])
    s.close()
    return str(tmp_path)


class TestMemoryTagsCommand:
    def test_tags_empty_store(self, tmp_path):
        """Empty store shows friendly message."""
        result = runner.invoke(app, ["memory", "tags", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "no tags found" in result.stdout

    def test_tags_table_output(self, tags_project_dir):
        """Table output shows TAG and COUNT columns."""
        result = runner.invoke(app, ["memory", "tags", "--project-dir", tags_project_dir])
        assert result.exit_code == 0
        assert "TAG" in result.stdout
        assert "COUNT" in result.stdout
        assert "python" in result.stdout
        assert "core" in result.stdout
        assert "tags" in result.stdout

    def test_tags_json_output(self, tags_project_dir):
        """JSON output is a list of {tag, count} dicts."""
        result = runner.invoke(app, ["memory", "tags", "--project-dir", tags_project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        tags_map = {item["tag"]: item["count"] for item in data}
        assert tags_map["python"] == 2
        assert tags_map["core"] == 2
        assert tags_map["api"] == 1

    def test_tags_sorted_alphabetically(self, tags_project_dir):
        """Tags are returned in alphabetical order."""
        result = runner.invoke(app, ["memory", "tags", "--project-dir", tags_project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        tag_names = [item["tag"] for item in data]
        assert tag_names == sorted(tag_names)


class TestMemoryTagCommand:
    def test_tag_add(self, tags_project_dir):
        """Adding a tag updates the entry."""
        result = runner.invoke(
            app,
            [
                "memory",
                "tag",
                "alpha",
                "--add",
                "new-tag",
                "--project-dir",
                tags_project_dir,
            ],
        )
        assert result.exit_code == 0
        assert "new-tag" in result.stdout

    def test_tag_remove(self, tags_project_dir):
        """Removing a tag updates the entry."""
        result = runner.invoke(
            app,
            [
                "memory",
                "tag",
                "alpha",
                "--remove",
                "python",
                "--project-dir",
                tags_project_dir,
            ],
        )
        assert result.exit_code == 0
        assert "python" not in result.stdout or "Updated" in result.stdout

    def test_tag_add_and_remove(self, tags_project_dir):
        """Simultaneous add and remove updates correctly."""
        result = runner.invoke(
            app,
            [
                "memory",
                "tag",
                "alpha",
                "--add",
                "fresh",
                "--remove",
                "python",
                "--project-dir",
                tags_project_dir,
            ],
        )
        assert result.exit_code == 0
        assert "fresh" in result.stdout

    def test_tag_json_output(self, tags_project_dir):
        """JSON output returns key and updated tags list."""
        result = runner.invoke(
            app,
            [
                "memory",
                "tag",
                "beta",
                "--add",
                "new",
                "--project-dir",
                tags_project_dir,
                "--json",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["key"] == "beta"
        assert "new" in data["tags"]

    def test_tag_not_found(self, tmp_path):
        """Non-existent key returns error exit code."""
        result = runner.invoke(
            app,
            ["memory", "tag", "no-such-key", "--add", "x", "--project-dir", str(tmp_path)],
        )
        assert result.exit_code != 0

    def test_tag_not_found_json(self, tmp_path):
        """Non-existent key with --json returns error dict."""
        result = runner.invoke(
            app,
            [
                "memory",
                "tag",
                "no-such-key",
                "--add",
                "x",
                "--project-dir",
                str(tmp_path),
                "--json",
            ],
        )
        assert result.exit_code != 0
        data = json.loads(result.stdout)
        assert data.get("error") == "not_found"


# ===================================================================
# Diagnostics commands (EPIC-030)
# ===================================================================


class TestDiagnosticsCommands:
    def test_diagnostics_report_json(self, project_dir):
        r = runner.invoke(
            app,
            ["diagnostics", "report", "--json", "--project-dir", project_dir],
        )
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert "composite_score" in data
        assert "circuit_state" in data
        assert "dimensions" in data

    def test_diagnostics_report_human(self, project_dir):
        r = runner.invoke(app, ["diagnostics", "report", "--project-dir", project_dir])
        assert r.exit_code == 0
        assert "Operational" in r.stdout or "Degraded" in r.stdout
        assert "Composite score:" in r.stdout

    def test_diagnostics_history_after_report(self, project_dir):
        r1 = runner.invoke(
            app,
            ["diagnostics", "report", "--project-dir", project_dir],
        )
        assert r1.exit_code == 0
        r2 = runner.invoke(
            app,
            ["diagnostics", "history", "--json", "--project-dir", project_dir, "--limit", "5"],
        )
        assert r2.exit_code == 0
        hist = json.loads(r2.stdout)
        assert isinstance(hist, list)
        assert len(hist) >= 1
        assert "composite_score" in hist[0]


# ===================================================================
# Feedback commands (EPIC-029)
# ===================================================================


class TestFeedbackCommands:
    def test_feedback_rate_gap_issue_record_list(self, project_dir):
        r1 = runner.invoke(
            app,
            ["feedback", "rate", "tech-stack", "--rating", "helpful", "--project-dir", project_dir],
        )
        assert r1.exit_code == 0
        assert "recall_rated" in r1.stdout

        r2 = runner.invoke(
            app,
            ["feedback", "gap", "how do we deploy?", "--project-dir", project_dir],
        )
        assert r2.exit_code == 0
        assert "gap_reported" in r2.stdout

        r3 = runner.invoke(
            app,
            ["feedback", "issue", "tech-stack", "outdated stack", "--project-dir", project_dir],
        )
        assert r3.exit_code == 0
        assert "issue_flagged" in r3.stdout

        r4 = runner.invoke(
            app,
            [
                "feedback",
                "record",
                "pr_merged",
                "--entry-key",
                "api-pattern",
                "--utility-score",
                "0.25",
                "--project-dir",
                project_dir,
            ],
        )
        assert r4.exit_code == 0
        assert "pr_merged" in r4.stdout

        lst = runner.invoke(
            app,
            ["feedback", "list", "--event-type", "gap_reported", "--project-dir", project_dir],
        )
        assert lst.exit_code == 0
        assert "gap_reported" in lst.stdout
        assert "1 event" in lst.stdout

    def test_feedback_json_output(self, project_dir):
        r = runner.invoke(
            app,
            ["feedback", "rate", "tech-stack", "--json", "--project-dir", project_dir],
        )
        assert r.exit_code == 0
        data = json.loads(r.stdout)
        assert data["status"] == "recorded"
        assert data["event"]["event_type"] == "recall_rated"

        lst = runner.invoke(app, ["feedback", "list", "--json", "--project-dir", project_dir])
        assert lst.exit_code == 0
        rows = json.loads(lst.stdout)
        assert isinstance(rows, list)
        assert len(rows) >= 1

    def test_feedback_invalid_rating_exit_code(self, project_dir):
        r = runner.invoke(
            app,
            ["feedback", "rate", "tech-stack", "--rating", "nope", "--project-dir", project_dir],
        )
        assert r.exit_code == 1

    def test_feedback_invalid_event_type_exit_code(self, project_dir):
        r = runner.invoke(
            app,
            ["feedback", "record", "bad", "--project-dir", project_dir],
        )
        assert r.exit_code == 1

    def test_feedback_invalid_details_json_exit_code(self, project_dir):
        r = runner.invoke(
            app,
            [
                "feedback",
                "rate",
                "tech-stack",
                "--details-json",
                "not-json",
                "--project-dir",
                project_dir,
            ],
        )
        assert r.exit_code == 1
