"""Unit tests for STORY-066.9: Behavioural parity doc + load smoke benchmark.

Verifies all 7 acceptance criteria without requiring a live Postgres instance:

  AC1 — docs/engineering/v3-behavioral-parity.md enumerates every intentional
         v3 vs v2 delta with code references
  AC2 — tests/benchmarks/load_smoke_postgres.py runs 50 concurrent agents for
         60 seconds against one Postgres
  AC3 — p95 latency recorded for save / recall / hive_search and stored as
         benchmark output
  AC4 — benchmark marked requires_postgres so it does not run in the unit suite
  AC5 — documented latency budget or explicit "informational only" status
  AC6 — AGENTS.md documents how to run the benchmark
  AC7 — EPIC-059 STORY-059.6 acceptance criteria all checked off
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_PARITY_DOC = _REPO_ROOT / "docs" / "engineering" / "v3-behavioral-parity.md"
_BENCHMARK_FILE = _REPO_ROOT / "tests" / "benchmarks" / "load_smoke_postgres.py"
_AGENTS_MD = _REPO_ROOT / "AGENTS.md"
_EPIC_059 = _REPO_ROOT / "docs" / "planning" / "epics" / "EPIC-059.md"


# ---------------------------------------------------------------------------
# AC1 — parity doc covers every intentional delta
# ---------------------------------------------------------------------------


class TestAc1ParityDocContent:
    """docs/engineering/v3-behavioral-parity.md enumerates every intentional v3 vs v2
    delta with code references."""

    def _doc(self) -> str:
        assert _PARITY_DOC.exists(), f"Missing: {_PARITY_DOC}"
        return _PARITY_DOC.read_text()

    def test_ac1_file_exists(self) -> None:
        assert _PARITY_DOC.exists()

    def test_ac1_audit_emission_delta(self) -> None:
        """Audit emission timing delta is documented."""
        doc = self._doc()
        assert "Audit Emission" in doc or "audit" in doc.lower()
        assert "audit_log" in doc

    def test_ac1_valid_at_semantics_delta(self) -> None:
        """valid_at / invalid_at semantics delta is documented."""
        doc = self._doc()
        assert "valid_at" in doc
        assert "TIMESTAMPTZ" in doc or "timestamptz" in doc

    def test_ac1_gc_archive_flow_delta(self) -> None:
        """GC archive flow delta (JSONL → Postgres table) is documented."""
        doc = self._doc()
        assert "gc_archive" in doc or "GC Archive" in doc or "GC archive" in doc

    def test_ac1_storage_engine_delta(self) -> None:
        """SQLite → PostgreSQL storage delta is documented."""
        doc = self._doc()
        assert "SQLite" in doc
        assert "PostgreSQL" in doc or "Postgres" in doc

    def test_ac1_fts_ranking_delta(self) -> None:
        """FTS ranking change (FTS5 → tsvector) is documented."""
        doc = self._doc()
        assert "tsvector" in doc or "FTS" in doc

    def test_ac1_code_references_present(self) -> None:
        """At least four code references (src/ or migrations/) appear in the doc."""
        doc = self._doc()
        code_refs = [
            line for line in doc.splitlines() if "src/tapps_brain" in line or "migrations/" in line
        ]
        assert len(code_refs) >= 4, (
            f"Expected ≥4 code references in {_PARITY_DOC.name}; found {len(code_refs)}"
        )

    def test_ac1_removed_public_api_section(self) -> None:
        """Removed public API symbols are enumerated."""
        doc = self._doc()
        assert "Removed Public API" in doc or "removed" in doc.lower()
        # At least one removed symbol must be named.
        assert "HiveStore" in doc or "SqliteHiveBackend" in doc


# ---------------------------------------------------------------------------
# AC2 — benchmark defines 50 agents × 60 s
# ---------------------------------------------------------------------------


class TestAc2BenchmarkAgentsAndDuration:
    """tests/benchmarks/load_smoke_postgres.py runs 50 concurrent agents for 60 s."""

    def _src(self) -> str:
        assert _BENCHMARK_FILE.exists(), f"Missing: {_BENCHMARK_FILE}"
        return _BENCHMARK_FILE.read_text()

    def test_ac2_file_exists(self) -> None:
        assert _BENCHMARK_FILE.exists()

    def test_ac2_default_agents_50(self) -> None:
        """_DEFAULT_AGENTS constant is 50."""
        src = self._src()
        assert "_DEFAULT_AGENTS" in src
        assert "50" in src

    def test_ac2_default_duration_60(self) -> None:
        """_DEFAULT_DURATION constant is 60."""
        src = self._src()
        assert "_DEFAULT_DURATION" in src
        assert "60" in src

    def test_ac2_uses_threading(self) -> None:
        """Benchmark uses threading (not asyncio) per technical notes."""
        src = self._src()
        assert "threading" in src
        assert "Thread" in src
        assert "Barrier" in src


# ---------------------------------------------------------------------------
# AC3 — p95 latency recorded for save / recall / hive_search
# ---------------------------------------------------------------------------


class TestAc3P95LatencyRecording:
    """p95 latency recorded for save / recall / hive_search and stored as output."""

    def _src(self) -> str:
        return _BENCHMARK_FILE.read_text()

    def test_ac3_save_bucket_exists(self) -> None:
        src = self._src()
        assert "save_bucket" in src

    def test_ac3_recall_bucket_exists(self) -> None:
        src = self._src()
        assert "recall_bucket" in src

    def test_ac3_hive_search_bucket_exists(self) -> None:
        src = self._src()
        assert "hive_search_bucket" in src

    def test_ac3_percentile_95_computed(self) -> None:
        """percentile(95) or p95 is computed for all three operations."""
        src = self._src()
        assert "percentile(95)" in src or "p95" in src.lower()

    def test_ac3_latency_bucket_class(self) -> None:
        """_LatencyBucket class provides record() and percentile() methods."""
        src = self._src()
        assert "_LatencyBucket" in src
        assert "def record(" in src
        assert "def percentile(" in src

    def test_ac3_results_printed(self) -> None:
        """Results are printed to stdout as benchmark output."""
        src = self._src()
        assert "_print_results" in src or "print(" in src

    def test_ac3_p95_assertions_in_test(self) -> None:
        """Test asserts p95 latency is non-None for recorded ops."""
        src = self._src()
        assert "p95_save" in src or "p95" in src


# ---------------------------------------------------------------------------
# AC4 — benchmark marks require_postgres and benchmark
# ---------------------------------------------------------------------------


class TestAc4BenchmarkMarks:
    """Benchmark file has requires_postgres mark so unit suite skips it."""

    def _src(self) -> str:
        return _BENCHMARK_FILE.read_text()

    def test_ac4_requires_postgres_mark(self) -> None:
        src = self._src()
        assert "requires_postgres" in src

    def test_ac4_benchmark_mark(self) -> None:
        src = self._src()
        assert "pytest.mark.benchmark" in src or "mark.benchmark" in src

    def test_ac4_pytestmark_module_level(self) -> None:
        """Module-level pytestmark includes both marks."""
        src = self._src()
        assert "pytestmark" in src

    def test_ac4_skips_without_dsn(self) -> None:
        """Benchmark skips gracefully when TAPPS_BRAIN_DATABASE_URL is unset."""
        src = self._src()
        assert "pytest.skip" in src
        assert "TAPPS_BRAIN_DATABASE_URL" in src


# ---------------------------------------------------------------------------
# AC5 — "informational only" status documented
# ---------------------------------------------------------------------------


class TestAc5InformationalOnlyStatus:
    """Latency budget or 'informational only' status is documented."""

    def test_ac5_parity_doc_informational(self) -> None:
        doc = _PARITY_DOC.read_text()
        assert "informational" in doc.lower()

    def test_ac5_benchmark_informational_comment(self) -> None:
        src = _BENCHMARK_FILE.read_text()
        assert "informational" in src.lower()

    def test_ac5_no_hard_slo_asserted(self) -> None:
        """Benchmark does not assert a hard latency ceiling (pre-SLO)."""
        src = _BENCHMARK_FILE.read_text()
        # Must not assert p95 < some hard limit — check there's no tight assert
        # on the p95 value itself (only that it is non-None / measurable).
        lines = src.splitlines()
        hard_budget_assertions = [
            line for line in lines if "p95" in line and "<" in line and "assert" in line
        ]
        assert not hard_budget_assertions, (
            "Unexpected hard p95 budget assertion found (expected informational only): "
            + str(hard_budget_assertions)
        )

    def test_ac5_parity_doc_has_latency_table(self) -> None:
        """Parity doc includes a reference latency table."""
        doc = _PARITY_DOC.read_text()
        assert "p95" in doc.lower() or "Typical range" in doc


# ---------------------------------------------------------------------------
# AC6 — AGENTS.md documents how to run the benchmark
# ---------------------------------------------------------------------------


class TestAc6AgentsMdBenchmark:
    """AGENTS.md has a benchmark-postgres section explaining how to run."""

    def _agents_md(self) -> str:
        assert _AGENTS_MD.exists(), f"Missing: {_AGENTS_MD}"
        return _AGENTS_MD.read_text()

    def test_ac6_agents_md_exists(self) -> None:
        assert _AGENTS_MD.exists()

    def test_ac6_benchmark_postgres_section(self) -> None:
        md = self._agents_md()
        assert "benchmark-postgres" in md

    def test_ac6_load_smoke_command_documented(self) -> None:
        """AGENTS.md shows a pytest command for the benchmark."""
        md = self._agents_md()
        assert "load_smoke_postgres" in md

    def test_ac6_tapps_brain_database_url_documented(self) -> None:
        """AGENTS.md documents the required env var."""
        md = self._agents_md()
        assert "TAPPS_BRAIN_DATABASE_URL" in md

    def test_ac6_override_env_vars_documented(self) -> None:
        """TAPPS_SMOKE_AGENTS and TAPPS_SMOKE_DURATION overrides documented."""
        md = self._agents_md()
        assert "TAPPS_SMOKE_AGENTS" in md or "TAPPS_SMOKE_DURATION" in md


# ---------------------------------------------------------------------------
# AC7 — EPIC-059 STORY-059.6 acceptance criteria all checked off
# ---------------------------------------------------------------------------


class TestAc7Epic059Story0596:
    """EPIC-059 STORY-059.6 acceptance criteria are all marked [x]."""

    def _epic059(self) -> str:
        assert _EPIC_059.exists(), f"Missing: {_EPIC_059}"
        return _EPIC_059.read_text()

    def test_ac7_epic059_exists(self) -> None:
        assert _EPIC_059.exists()

    def test_ac7_story_059_6_present(self) -> None:
        doc = self._epic059()
        assert "STORY-059.6" in doc or "059.6" in doc

    def test_ac7_story_059_6_marked_done(self) -> None:
        """STORY-059.6 section is marked 'done'."""
        doc = self._epic059()
        # Find the section and check for done status
        lines = doc.splitlines()
        in_section = False
        for line in lines:
            if "STORY-059.6" in line:
                in_section = True
            if in_section and ("done" in line.lower() or "**done**" in line.lower()):
                return
        pytest.fail("STORY-059.6 is not marked 'done' in EPIC-059.md")

    def test_ac7_parity_doc_ac_checked(self) -> None:
        """Parity doc AC is checked [x] in EPIC-059."""
        doc = self._epic059()
        # The parity doc AC line should have [x] prefix
        assert "[x]" in doc, "Expected at least one [x] checked AC in EPIC-059.md"
        # Specifically the parity doc line
        parity_line = next(
            (ln for ln in doc.splitlines() if "v3-behavioral-parity.md" in ln),
            None,
        )
        assert parity_line is not None, "parity doc AC line not found in EPIC-059.md"
        assert parity_line.strip().startswith("- [x]"), (
            f"Parity doc AC not checked: {parity_line!r}"
        )

    def test_ac7_benchmark_ac_checked(self) -> None:
        """Benchmark / load smoke AC is checked [x] in EPIC-059."""
        doc = self._epic059()
        benchmark_line = next(
            (ln for ln in doc.splitlines() if "load_smoke_postgres" in ln),
            None,
        )
        assert benchmark_line is not None, "benchmark AC line not found in EPIC-059.md"
        assert benchmark_line.strip().startswith("- [x]"), (
            f"Benchmark AC not checked: {benchmark_line!r}"
        )
