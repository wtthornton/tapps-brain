"""Tests for the tapps-brain CLI tool."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_cli

from typer.testing import CliRunner  # noqa: E402

from tapps_brain.cli import app  # noqa: E402
from tapps_brain.store import MemoryStore  # noqa: E402

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


# ===================================================================
# Store commands
# ===================================================================


class TestStoreCommands:
    def test_stats(self, project_dir):
        result = runner.invoke(app, ["store", "stats", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "Entries: 3 / 500" in result.stdout
        assert "Schema: v7" in result.stdout

    def test_stats_json(self, project_dir):
        result = runner.invoke(app, ["store", "stats", "--project-dir", project_dir, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["total_entries"] == 3
        assert data["schema_version"] == 7

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

    def test_migrate(self, project_dir):
        result = runner.invoke(app, ["maintenance", "migrate", "--project-dir", project_dir])
        assert result.exit_code == 0
        assert "v7" in result.stdout

    def test_migrate_dry_run(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "migrate", "--project-dir", project_dir, "--dry-run"]
        )
        assert result.exit_code == 0
        assert "v7" in result.stdout

    def test_migrate_json(self, project_dir):
        result = runner.invoke(
            app, ["maintenance", "migrate", "--project-dir", project_dir, "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["schema_version"] == 7


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
        result = runner.invoke(
            app,
            ["agent", "create", "bad-agent", "--profile", "nonexistent-profile-xyz"],
        )
        assert result.exit_code == 1
        assert "not found" in result.stderr or "not found" in result.output

    def test_agent_create_invalid_profile_json(self):
        result = runner.invoke(
            app,
            ["agent", "create", "bad-agent", "--profile", "nonexistent-profile-xyz", "--json"],
        )
        # exit code may be 1; JSON output goes to stdout
        data = json.loads(result.stdout)
        assert data["error"] == "invalid_profile"
        assert "available_profiles" in data


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
        # No error — exits cleanly
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
        result = runner.invoke(
            app, ["memory", "audit", "--project-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "no audit events found" in result.stdout

    def test_audit_all_events_table(self, audit_project_dir):
        """Without filters, returns all events in table format."""
        result = runner.invoke(
            app, ["memory", "audit", "--project-dir", audit_project_dir]
        )
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
