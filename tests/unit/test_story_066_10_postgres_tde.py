"""Unit tests for STORY-066.10: pg_tde operator runbook.

Verifies all 9 acceptance criteria without requiring a live Postgres instance:

  AC1 — docs/guides/postgres-tde.md exists and covers install / key provider /
         rotation / troubleshooting / cloud fallback
  AC2 — ADR-007 cross-links to the runbook
  AC3 — docs/engineering/threat-model.md cross-links to the runbook
  AC4 — docs/guides/hive-deployment.md cross-links to the runbook
  AC5 — runbook validated against Percona pg_tde 2.1.2 release notes
  AC6 — cloud fallback table covers AWS RDS
  AC7 — cloud fallback table covers Google CloudSQL
  AC8 — cloud fallback table covers Azure Database for PostgreSQL
  AC9 — all internal markdown links in the new file resolve to existing files
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_TDE_DOC = _REPO_ROOT / "docs" / "guides" / "postgres-tde.md"
_ADR_007 = _REPO_ROOT / "docs" / "planning" / "adr" / "ADR-007-postgres-only-no-sqlite.md"
_THREAT_MODEL = _REPO_ROOT / "docs" / "engineering" / "threat-model.md"
_HIVE_DEPLOYMENT = _REPO_ROOT / "docs" / "guides" / "hive-deployment.md"


# ---------------------------------------------------------------------------
# AC1 — runbook exists and covers all required sections
# ---------------------------------------------------------------------------


class TestAc1RunbookContent:
    """docs/guides/postgres-tde.md exists and covers install / key provider /
    rotation / troubleshooting / cloud fallback."""

    def _doc(self) -> str:
        assert _TDE_DOC.exists(), f"Missing: {_TDE_DOC}"
        return _TDE_DOC.read_text()

    def test_ac1_file_exists(self) -> None:
        assert _TDE_DOC.exists()

    def test_ac1_install_section(self) -> None:
        doc = self._doc()
        assert "Install" in doc or "install" in doc

    def test_ac1_key_provider_section(self) -> None:
        doc = self._doc()
        assert "Key Provider" in doc or "key provider" in doc.lower()

    def test_ac1_rotation_section(self) -> None:
        doc = self._doc()
        assert "Rotation" in doc or "rotation" in doc.lower()

    def test_ac1_troubleshooting_section(self) -> None:
        doc = self._doc()
        assert "Troubleshooting" in doc or "troubleshooting" in doc.lower()

    def test_ac1_cloud_fallback_section(self) -> None:
        doc = self._doc()
        assert "Cloud" in doc and ("Fallback" in doc or "fallback" in doc.lower())

    def test_ac1_vault_documented(self) -> None:
        """Vault key provider configuration is documented."""
        doc = self._doc()
        assert "Vault" in doc

    def test_ac1_openbao_documented(self) -> None:
        """OpenBao key provider configuration is documented."""
        doc = self._doc()
        assert "OpenBao" in doc


# ---------------------------------------------------------------------------
# AC2 — ADR-007 cross-links to the runbook
# ---------------------------------------------------------------------------


class TestAc2Adr007CrossLink:
    """ADR-007 cross-links to docs/guides/postgres-tde.md."""

    def test_ac2_adr007_exists(self) -> None:
        assert _ADR_007.exists()

    def test_ac2_adr007_links_tde(self) -> None:
        content = _ADR_007.read_text()
        assert "postgres-tde" in content, "ADR-007 does not cross-link to postgres-tde.md"


# ---------------------------------------------------------------------------
# AC3 — threat-model.md cross-links to the runbook
# ---------------------------------------------------------------------------


class TestAc3ThreatModelCrossLink:
    """docs/engineering/threat-model.md cross-links to docs/guides/postgres-tde.md."""

    def test_ac3_threat_model_exists(self) -> None:
        assert _THREAT_MODEL.exists(), f"Missing: {_THREAT_MODEL}"

    def test_ac3_threat_model_links_tde(self) -> None:
        content = _THREAT_MODEL.read_text()
        assert "postgres-tde" in content, "threat-model.md does not cross-link to postgres-tde.md"


# ---------------------------------------------------------------------------
# AC4 — hive-deployment.md cross-links to the runbook
# ---------------------------------------------------------------------------


class TestAc4HiveDeploymentCrossLink:
    """docs/guides/hive-deployment.md cross-links to docs/guides/postgres-tde.md."""

    def test_ac4_hive_deployment_exists(self) -> None:
        assert _HIVE_DEPLOYMENT.exists(), f"Missing: {_HIVE_DEPLOYMENT}"

    def test_ac4_hive_deployment_links_tde(self) -> None:
        content = _HIVE_DEPLOYMENT.read_text()
        assert "postgres-tde" in content, (
            "hive-deployment.md does not cross-link to postgres-tde.md"
        )


# ---------------------------------------------------------------------------
# AC5 — runbook references pg_tde 2.1.2
# ---------------------------------------------------------------------------


class TestAc5PgtdeVersion:
    """Runbook references Percona pg_tde 2.1.2."""

    def test_ac5_version_2_1_2_mentioned(self) -> None:
        doc = _TDE_DOC.read_text()
        assert "2.1.2" in doc, "pg_tde version 2.1.2 not mentioned in runbook"

    def test_ac5_percona_distribution_mentioned(self) -> None:
        doc = _TDE_DOC.read_text()
        assert "Percona" in doc

    def test_ac5_pg17_mentioned(self) -> None:
        """Runbook specifies Postgres 17 (required by pg_tde 2.1.2)."""
        doc = _TDE_DOC.read_text()
        assert "17" in doc and ("PostgreSQL 17" in doc or "pg17" in doc or "Postgres 17" in doc)


# ---------------------------------------------------------------------------
# AC6 — cloud fallback covers AWS RDS
# ---------------------------------------------------------------------------


class TestAc6AwsRds:
    """Cloud fallback table covers AWS RDS."""

    def test_ac6_rds_in_runbook(self) -> None:
        doc = _TDE_DOC.read_text()
        assert "RDS" in doc, "AWS RDS not covered in cloud fallback section"

    def test_ac6_rds_in_cloud_section(self) -> None:
        """RDS appears in the cloud/fallback portion of the doc."""
        doc = _TDE_DOC.read_text()
        lines = doc.splitlines()
        in_cloud = False
        for line in lines:
            if "Cloud" in line and ("Fallback" in line or "Provider" in line):
                in_cloud = True
            if in_cloud and "RDS" in line:
                return
        pytest.fail("RDS not found in cloud fallback section of postgres-tde.md")


# ---------------------------------------------------------------------------
# AC7 — cloud fallback covers Google CloudSQL
# ---------------------------------------------------------------------------


class TestAc7GoogleCloudsql:
    """Cloud fallback table covers Google CloudSQL."""

    def test_ac7_cloudsql_in_runbook(self) -> None:
        doc = _TDE_DOC.read_text()
        assert "CloudSQL" in doc or "Cloud SQL" in doc, (
            "Google CloudSQL not covered in cloud fallback section"
        )


# ---------------------------------------------------------------------------
# AC8 — cloud fallback covers Azure Database for PostgreSQL
# ---------------------------------------------------------------------------


class TestAc8AzurePostgres:
    """Cloud fallback table covers Azure Database for PostgreSQL."""

    def test_ac8_azure_in_runbook(self) -> None:
        doc = _TDE_DOC.read_text()
        assert "Azure" in doc, "Azure Database for PostgreSQL not covered in cloud fallback section"


# ---------------------------------------------------------------------------
# AC9 — internal markdown links in the new file resolve
# ---------------------------------------------------------------------------


class TestAc9InternalLinksResolve:
    """All relative markdown links in postgres-tde.md resolve to existing files."""

    def _collect_relative_links(self) -> list[tuple[str, Path]]:
        """Return (raw_link, resolved_path) for every relative link in the doc."""
        doc = _TDE_DOC.read_text()
        # Match [text](target) where target does not start with http/https/#
        pattern = re.compile(r"\[.*?\]\(([^)]+)\)")
        results: list[tuple[str, Path]] = []
        guide_dir = _TDE_DOC.parent
        for match in pattern.finditer(doc):
            target = match.group(1).split("#")[0].strip()  # strip anchors
            if not target or target.startswith("http"):
                continue
            resolved = (guide_dir / target).resolve()
            results.append((target, resolved))
        return results

    def test_ac9_relative_links_resolve(self) -> None:
        broken: list[str] = []
        for raw, resolved in self._collect_relative_links():
            if not resolved.exists():
                broken.append(f"{raw!r} → {resolved}")
        assert not broken, "Broken internal links in postgres-tde.md:\n" + "\n".join(broken)

    def test_ac9_at_least_one_internal_link(self) -> None:
        """Sanity: doc has at least one relative link (ADR-007 or similar)."""
        links = self._collect_relative_links()
        assert len(links) >= 1, "postgres-tde.md has no internal cross-links"
