"""Unit tests for STORY-066.11: Postgres backup and restore runbook.

Verifies all 7 acceptance criteria without requiring a live Postgres instance:

  AC1 — docs/guides/postgres-backup.md exists and covers logical / physical / PITR
  AC2 — schema-independent restore documented for private / hive / federation
  AC3 — Hive replica failover documented with sample config
  AC4 — crontab and pgBackRest examples included
  AC5 — docs/operations/postgres-backup-runbook.md exists for ops on-call
  AC6 — cross-links from hive-deployment.md and db-roles-runbook.md
  AC7 — internal markdown links in both new files resolve to existing files
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_BACKUP_DOC = _REPO_ROOT / "docs" / "guides" / "postgres-backup.md"
_OPS_RUNBOOK = _REPO_ROOT / "docs" / "operations" / "postgres-backup-runbook.md"
_HIVE_DEPLOYMENT = _REPO_ROOT / "docs" / "guides" / "hive-deployment.md"
_DB_ROLES = _REPO_ROOT / "docs" / "operations" / "db-roles-runbook.md"


def _relative_links(path: Path) -> list[tuple[str, Path]]:
    """Return (raw_link, resolved_path) for relative markdown links in *path*."""
    content = path.read_text()
    pattern = re.compile(r'\[.*?\]\(([^)]+)\)')
    results: list[tuple[str, Path]] = []
    for match in pattern.finditer(content):
        target = match.group(1).split("#")[0].strip()
        if not target or target.startswith("http"):
            continue
        results.append((target, (path.parent / target).resolve()))
    return results


# ---------------------------------------------------------------------------
# AC1 — backup doc exists and covers all three strategies
# ---------------------------------------------------------------------------


class TestAc1BackupDocContent:
    """docs/guides/postgres-backup.md covers logical / physical / PITR strategies."""

    def _doc(self) -> str:
        assert _BACKUP_DOC.exists(), f"Missing: {_BACKUP_DOC}"
        return _BACKUP_DOC.read_text()

    def test_ac1_file_exists(self) -> None:
        assert _BACKUP_DOC.exists()

    def test_ac1_logical_pg_dump_covered(self) -> None:
        doc = self._doc()
        assert "pg_dump" in doc

    def test_ac1_physical_wal_covered(self) -> None:
        """WAL archiving / base backup for PITR is covered."""
        doc = self._doc()
        assert "WAL" in doc or "wal" in doc.lower()
        assert "base backup" in doc.lower() or "pg_basebackup" in doc

    def test_ac1_pitr_covered(self) -> None:
        """Point-in-time recovery is explicitly covered."""
        doc = self._doc()
        assert "PITR" in doc or "point-in-time" in doc.lower()

    def test_ac1_pgbackrest_covered(self) -> None:
        """pgBackRest is documented as the recommended production strategy."""
        doc = self._doc()
        assert "pgBackRest" in doc or "pgbackrest" in doc.lower()


# ---------------------------------------------------------------------------
# AC2 — schema-independent restore for private / hive / federation
# ---------------------------------------------------------------------------


class TestAc2SchemaIndependentRestore:
    """Schema-independent restore documented for private / hive / federation."""

    def _doc(self) -> str:
        return _BACKUP_DOC.read_text()

    def test_ac2_private_schema_restore(self) -> None:
        doc = self._doc()
        assert "private" in doc.lower()

    def test_ac2_hive_schema_restore(self) -> None:
        doc = self._doc()
        assert "hive" in doc.lower()

    def test_ac2_federation_schema_restore(self) -> None:
        doc = self._doc()
        assert "federation" in doc.lower()

    def test_ac2_independent_restore_section(self) -> None:
        """Dedicated section on per-schema restore exists."""
        doc = self._doc()
        assert "Schema-independent" in doc or "schema-independent" in doc.lower() or (
            "independent" in doc.lower() and "restore" in doc.lower()
        )


# ---------------------------------------------------------------------------
# AC3 — Hive replica failover documented with sample config
# ---------------------------------------------------------------------------


class TestAc3HiveReplicaFailover:
    """Hive replica failover documented with sample config."""

    def _doc(self) -> str:
        return _BACKUP_DOC.read_text()

    def test_ac3_failover_section_exists(self) -> None:
        doc = self._doc()
        assert "failover" in doc.lower() or "replica" in doc.lower()

    def test_ac3_primary_conninfo_or_streaming(self) -> None:
        """Sample streaming replication config (primary_conninfo or pg_hba entry)."""
        doc = self._doc()
        assert "primary_conninfo" in doc or "streaming" in doc.lower()

    def test_ac3_promote_command_documented(self) -> None:
        """pg_ctl promote or pg_promote documented."""
        doc = self._doc()
        assert "promote" in doc.lower()


# ---------------------------------------------------------------------------
# AC4 — crontab and pgBackRest examples included
# ---------------------------------------------------------------------------


class TestAc4CrontabAndPgbackrest:
    """Crontab and pgBackRest examples are included in the backup doc."""

    def _doc(self) -> str:
        return _BACKUP_DOC.read_text()

    def test_ac4_crontab_example(self) -> None:
        doc = self._doc()
        assert "crontab" in doc.lower() or "cron" in doc.lower()

    def test_ac4_pgbackrest_stanza_config(self) -> None:
        """pgBackRest stanza configuration example is present."""
        doc = self._doc()
        assert "stanza" in doc.lower() or "pgbackrest.conf" in doc

    def test_ac4_pgbackrest_backup_command(self) -> None:
        """pgBackRest backup command example shown."""
        doc = self._doc()
        assert "pgbackrest backup" in doc or "pgbackrest --stanza" in doc


# ---------------------------------------------------------------------------
# AC5 — ops on-call runbook exists
# ---------------------------------------------------------------------------


class TestAc5OpsRunbook:
    """docs/operations/postgres-backup-runbook.md exists for ops on-call."""

    def test_ac5_ops_runbook_exists(self) -> None:
        assert _OPS_RUNBOOK.exists(), f"Missing: {_OPS_RUNBOOK}"

    def test_ac5_ops_runbook_non_empty(self) -> None:
        content = _OPS_RUNBOOK.read_text()
        assert len(content.strip()) > 200, "Ops runbook appears to be a stub"

    def test_ac5_ops_runbook_has_restore_steps(self) -> None:
        content = _OPS_RUNBOOK.read_text()
        assert "restore" in content.lower() or "recover" in content.lower()


# ---------------------------------------------------------------------------
# AC6 — cross-links from hive-deployment.md and db-roles-runbook.md
# ---------------------------------------------------------------------------


class TestAc6CrossLinks:
    """Cross-links from hive-deployment.md and db-roles-runbook.md."""

    def test_ac6_hive_deployment_links_backup(self) -> None:
        assert _HIVE_DEPLOYMENT.exists(), f"Missing: {_HIVE_DEPLOYMENT}"
        content = _HIVE_DEPLOYMENT.read_text()
        assert "postgres-backup" in content, (
            "hive-deployment.md does not cross-link to postgres-backup.md"
        )

    def test_ac6_db_roles_links_backup(self) -> None:
        assert _DB_ROLES.exists(), f"Missing: {_DB_ROLES}"
        content = _DB_ROLES.read_text()
        assert "postgres-backup" in content, (
            "db-roles-runbook.md does not cross-link to postgres-backup runbook"
        )


# ---------------------------------------------------------------------------
# AC7 — internal links in both new files resolve
# ---------------------------------------------------------------------------


class TestAc7InternalLinksResolve:
    """All relative markdown links in both new files resolve to existing files."""

    def _check_file(self, path: Path) -> list[str]:
        broken: list[str] = []
        for raw, resolved in _relative_links(path):
            if not resolved.exists():
                broken.append(f"{raw!r} → {resolved}")
        return broken

    def test_ac7_backup_doc_links_resolve(self) -> None:
        broken = self._check_file(_BACKUP_DOC)
        assert not broken, (
            f"Broken links in {_BACKUP_DOC.name}:\n" + "\n".join(broken)
        )

    def test_ac7_ops_runbook_links_resolve(self) -> None:
        broken = self._check_file(_OPS_RUNBOOK)
        assert not broken, (
            f"Broken links in {_OPS_RUNBOOK.name}:\n" + "\n".join(broken)
        )

    def test_ac7_backup_doc_has_links(self) -> None:
        """Sanity: backup doc has at least one internal cross-link."""
        assert len(_relative_links(_BACKUP_DOC)) >= 1
