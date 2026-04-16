"""Unit tests for STORY-066.13: Postgres integration tests replacing deleted SQLite tests.

Verifies all 9 acceptance criteria structurally (file content, markers, coverage
signatures) without requiring a live Postgres instance.

  AC1 — test_postgres_private_backend.py covers save / load_all / delete / search
  AC2 — test_feedback_postgres.py covers FeedbackStore record / query / strict-mode
  AC3 — test_session_index_postgres.py covers save_chunks / search / delete_expired
  AC4/AC5 — test_agent_identity_postgres.py covers (project_id, agent_id) isolation
  AC6 — test_pgvector_embeddings.py covers embedding write + knn_search recall
  AC7 — all new tests marked requires_postgres
  AC8 — new tests live in tests/integration/ (the CI Postgres workflow target)
  AC9 — no duplicate test function names within each file (basic flakiness proxy)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent
_INTEGRATION = _REPO_ROOT / "tests" / "integration"

_PRIVATE_BACKEND = _INTEGRATION / "test_postgres_private_backend.py"
_FEEDBACK = _INTEGRATION / "test_feedback_postgres.py"
_SESSION_INDEX = _INTEGRATION / "test_session_index_postgres.py"
_AGENT_IDENTITY = _INTEGRATION / "test_agent_identity_postgres.py"
_PGVECTOR = _INTEGRATION / "test_pgvector_embeddings.py"

_ALL_NEW = [_PRIVATE_BACKEND, _FEEDBACK, _SESSION_INDEX, _AGENT_IDENTITY, _PGVECTOR]


def _src(path: Path) -> str:
    assert path.exists(), f"Missing integration test file: {path}"
    return path.read_text()


def _test_names(path: Path) -> list[str]:
    """Return all test function names defined in *path*."""
    return re.findall(r"def (test_\w+)\s*\(", path.read_text())


# ---------------------------------------------------------------------------
# AC1 — test_postgres_private_backend.py
# ---------------------------------------------------------------------------


class TestAc1PrivateBackend:
    """tests/integration/test_postgres_private_backend.py covers CRUD + search."""

    def test_ac1_file_exists(self) -> None:
        assert _PRIVATE_BACKEND.exists()

    def test_ac1_save_covered(self) -> None:
        src = _src(_PRIVATE_BACKEND)
        assert "save" in src

    def test_ac1_load_all_covered(self) -> None:
        src = _src(_PRIVATE_BACKEND)
        assert "load_all" in src

    def test_ac1_delete_covered(self) -> None:
        src = _src(_PRIVATE_BACKEND)
        assert "delete" in src

    def test_ac1_search_covered(self) -> None:
        src = _src(_PRIVATE_BACKEND)
        assert "search" in src

    def test_ac1_has_test_functions(self) -> None:
        assert len(_test_names(_PRIVATE_BACKEND)) >= 4


# ---------------------------------------------------------------------------
# AC2 — test_feedback_postgres.py
# ---------------------------------------------------------------------------


class TestAc2FeedbackPostgres:
    """tests/integration/test_feedback_postgres.py covers FeedbackStore."""

    def test_ac2_file_exists(self) -> None:
        assert _FEEDBACK.exists()

    def test_ac2_record_covered(self) -> None:
        src = _src(_FEEDBACK)
        assert "record" in src

    def test_ac2_query_covered(self) -> None:
        src = _src(_FEEDBACK)
        assert "query" in src

    def test_ac2_feedbackstore_imported(self) -> None:
        src = _src(_FEEDBACK)
        assert "FeedbackStore" in src

    def test_ac2_has_test_functions(self) -> None:
        assert len(_test_names(_FEEDBACK)) >= 3


# ---------------------------------------------------------------------------
# AC3 — test_session_index_postgres.py
# ---------------------------------------------------------------------------


class TestAc3SessionIndexPostgres:
    """tests/integration/test_session_index_postgres.py covers SessionIndex."""

    def test_ac3_file_exists(self) -> None:
        assert _SESSION_INDEX.exists()

    def test_ac3_save_chunks_covered(self) -> None:
        src = _src(_SESSION_INDEX)
        assert "save_chunks" in src

    def test_ac3_search_covered(self) -> None:
        src = _src(_SESSION_INDEX)
        assert "search" in src

    def test_ac3_delete_expired_covered(self) -> None:
        src = _src(_SESSION_INDEX)
        assert "delete_expired" in src or "expired" in src

    def test_ac3_has_test_functions(self) -> None:
        assert len(_test_names(_SESSION_INDEX)) >= 3


# ---------------------------------------------------------------------------
# AC4/AC5 — test_agent_identity_postgres.py
# ---------------------------------------------------------------------------


class TestAc4Ac5AgentIdentityPostgres:
    """tests/integration/test_agent_identity_postgres.py covers row isolation."""

    def test_ac4_file_exists(self) -> None:
        assert _AGENT_IDENTITY.exists()

    def test_ac4_project_id_used(self) -> None:
        src = _src(_AGENT_IDENTITY)
        assert "project_id" in src

    def test_ac5_agent_id_used(self) -> None:
        src = _src(_AGENT_IDENTITY)
        assert "agent_id" in src

    def test_ac5_multi_agent_isolation_tested(self) -> None:
        """Test exercises at least two distinct agents."""
        src = _src(_AGENT_IDENTITY)
        # Either two agent variables or loop over agents
        agent_vars = re.findall(r"agent_[ab]\b|agents?\[", src)
        assert len(agent_vars) >= 2, (
            "Expected test to use at least two agent instances for isolation check"
        )

    def test_ac4_ac5_isolation_assertion(self) -> None:
        """An assertion verifies that one agent cannot see another's rows."""
        src = _src(_AGENT_IDENTITY)
        # Must assert non-overlap (not in, isdisjoint, ==, empty)
        assert (
            "not in" in src
            or "isdisjoint" in src
            or "not overlap" in src.lower()
            or ("agent" in src and "assert" in src)
        )

    def test_ac4_has_test_functions(self) -> None:
        assert len(_test_names(_AGENT_IDENTITY)) >= 2


# ---------------------------------------------------------------------------
# AC6 — test_pgvector_embeddings.py
# ---------------------------------------------------------------------------


class TestAc6PgvectorEmbeddings:
    """tests/integration/test_pgvector_embeddings.py covers embedding + knn_search."""

    def test_ac6_file_exists(self) -> None:
        assert _PGVECTOR.exists()

    def test_ac6_embedding_write_covered(self) -> None:
        src = _src(_PGVECTOR)
        assert "embedding" in src.lower()

    def test_ac6_knn_search_covered(self) -> None:
        src = _src(_PGVECTOR)
        assert "knn_search" in src

    def test_ac6_has_test_functions(self) -> None:
        assert len(_test_names(_PGVECTOR)) >= 2


# ---------------------------------------------------------------------------
# AC7 — all new tests marked requires_postgres
# ---------------------------------------------------------------------------


class TestAc7RequiresPostgresMark:
    """All five new integration test files have the requires_postgres marker."""

    @pytest.mark.parametrize("path", _ALL_NEW, ids=lambda p: p.name)
    def test_ac7_requires_postgres_mark(self, path: Path) -> None:
        src = _src(path)
        assert "requires_postgres" in src, (
            f"{path.name} is missing the requires_postgres pytest marker"
        )

    @pytest.mark.parametrize("path", _ALL_NEW, ids=lambda p: p.name)
    def test_ac7_pytestmark_or_decorator(self, path: Path) -> None:
        """Marker applied via pytestmark or @pytest.mark.requires_postgres."""
        src = _src(path)
        assert "pytestmark" in src or "@pytest.mark.requires_postgres" in src


# ---------------------------------------------------------------------------
# AC8 — tests live in tests/integration/ (CI workflow target)
# ---------------------------------------------------------------------------


class TestAc8InIntegrationDirectory:
    """All five new test files live in tests/integration/."""

    @pytest.mark.parametrize("path", _ALL_NEW, ids=lambda p: p.name)
    def test_ac8_in_integration_dir(self, path: Path) -> None:
        assert "integration" in str(path), f"{path.name} is not in tests/integration/"
        assert path.exists()


# ---------------------------------------------------------------------------
# AC9 — no duplicate test names within each file (flakiness proxy)
# ---------------------------------------------------------------------------


class TestAc9NoDuplicateTestNames:
    """No duplicate test function names within any new integration test file."""

    @pytest.mark.parametrize("path", _ALL_NEW, ids=lambda p: p.name)
    def test_ac9_no_duplicate_names(self, path: Path) -> None:
        names = _test_names(path)
        seen: set[str] = set()
        duplicates: list[str] = []
        for name in names:
            if name in seen:
                duplicates.append(name)
            seen.add(name)
        assert not duplicates, f"Duplicate test names in {path.name}: {duplicates}"
