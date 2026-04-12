"""Shared test fixtures for tapps-brain."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

# sentence-transformers is not installed in the test environment.
# MemoryStore() auto-detects this via get_embedding_provider() which returns
# None when sentence-transformers is unavailable. Tests that need embeddings
# pass their own provider explicitly.

if TYPE_CHECKING:
    from collections.abc import Iterator

_HAS_TYPER = importlib.util.find_spec("typer") is not None
_HAS_MCP = importlib.util.find_spec("mcp") is not None
_HAS_SENTENCE_TRANSFORMERS = importlib.util.find_spec("sentence_transformers") is not None


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked requires_cli / requires_mcp / requires_postgres when deps are missing."""
    import os

    skip_cli = pytest.mark.skip(reason="requires [cli] extra (typer)")
    skip_mcp = pytest.mark.skip(reason="requires [mcp] extra (mcp)")
    skip_pg = pytest.mark.skip(
        reason="requires live Postgres (set TAPPS_BRAIN_DATABASE_URL)"
    )
    _has_postgres = bool(os.environ.get("TAPPS_BRAIN_DATABASE_URL"))
    for item in items:
        if "requires_cli" in item.keywords and not _HAS_TYPER:
            item.add_marker(skip_cli)
        if "requires_mcp" in item.keywords and not _HAS_MCP:
            item.add_marker(skip_mcp)
        if "requires_postgres" in item.keywords and not _has_postgres:
            item.add_marker(skip_pg)


@pytest.fixture(scope="session", autouse=True)
def _cached_embedding_model():
    """Load the embedding model once for the entire test session.

    Without this, every MemoryStore() call invokes get_embedding_provider()
    which instantiates SentenceTransformerProvider and loads the model from
    disk (~8 seconds each). With 800+ store fixtures all function-scoped,
    that adds ~110 minutes of model loading to the suite.

    This fixture patches get_embedding_provider at the module level so all
    MemoryStore instances created during the session share one loaded model.
    Individual tests that need embedding_provider=None can still pass it
    explicitly to MemoryStore() and bypass this cached instance.
    """
    if not _HAS_SENTENCE_TRANSFORMERS:
        yield
        return

    import tapps_brain.embeddings as _emb

    _original = _emb.get_embedding_provider
    _provider = _emb.SentenceTransformerProvider()
    _emb.get_embedding_provider = lambda model=_emb._DEFAULT_MODEL: _provider  # noqa: SLF001
    yield
    _emb.get_embedding_provider = _original


# ---------------------------------------------------------------------------
# In-memory PrivateBackend for unit tests (ADR-007 stage 2)
# ---------------------------------------------------------------------------
#
# Production code is Postgres-only.  Unit tests previously relied on the
# now-deleted SQLite ``MemoryPersistence``; we provide an in-process dict-backed
# stand-in that satisfies the ``PrivateBackend`` protocol so the unit suite can
# run without spinning up Docker Postgres.  Integration tests that exercise
# real Postgres still set ``TAPPS_TEST_POSTGRES_DSN`` and bypass this fixture.


class InMemoryPrivateBackend:
    """Dict-backed PrivateBackend used by unit tests only — never in prod."""

    def __init__(self, project_id: str = "test", agent_id: str = "test") -> None:
        from tapps_brain.models import MemoryEntry  # noqa: F401  (typing only)

        self._project_id = project_id
        self._agent_id = agent_id
        self._entries: dict[str, Any] = {}
        self._relations: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._db_path = Path("/dev/null")
        self._store_dir = Path("/dev/null").parent
        # Use a real temp directory + JSONL file so append_audit / find_last_consolidation_merge_audit
        # work in unit tests without a Postgres connection.  The temp dir is cleaned up on close().
        self._tmp_audit_dir: str = tempfile.mkdtemp(prefix="tapps_test_audit_")
        self._audit_path = Path(self._tmp_audit_dir) / "audit.jsonl"
        self._audit_path.touch()
        # Sentinel attributes that store.py / FeedbackStore / DiagnosticsHistoryStore
        # introspect to reach the underlying connection manager. Tests that need
        # FeedbackStore must inject a real backend instead.
        self._cm = None

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def audit_path(self) -> Path:
        return self._audit_path

    @property
    def encryption_key(self) -> str | None:
        return None

    def save(self, entry: Any) -> None:
        with self._lock:
            self._entries[entry.key] = entry

    def load_all(self) -> list[Any]:
        with self._lock:
            return list(self._entries.values())

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._entries.pop(key, None) is not None

    def search(self, query: str, **_kwargs: Any) -> list[Any]:
        """Word-level FTS approximation: return entries where ANY query word appears
        in the value or key.  This mimics plainto_tsquery token matching so unit
        tests that use multi-word queries work correctly without a real tsvector."""
        if not query.strip():
            return []
        q_words = set(query.lower().split())
        with self._lock:
            return [
                e
                for e in self._entries.values()
                if q_words & set(e.value.lower().split())
                or q_words & set(e.key.lower().replace("-", " ").split())
            ]

    def list_relations(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._relations)

    def count_relations(self) -> int:
        with self._lock:
            return len(self._relations)

    def save_relations(self, key: str, relations: list[Any]) -> int:
        with self._lock:
            for rel in relations:
                self._relations.append(
                    {
                        "subject": getattr(rel, "subject", ""),
                        "predicate": getattr(rel, "predicate", ""),
                        "object_entity": getattr(rel, "object_entity", ""),
                        "source_entry_keys": list(
                            dict.fromkeys([*getattr(rel, "source_entry_keys", []), key])
                        ),
                        "confidence": float(getattr(rel, "confidence", 0.8)),
                        "created_at": "1970-01-01T00:00:00+00:00",
                    }
                )
            return len(relations)

    def load_relations(self, key: str) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self._relations if key in r["source_entry_keys"]]

    def delete_relations(self, key: str) -> int:
        """Remove all relations whose ``source_entry_keys`` contains *key*."""
        with self._lock:
            before = len(self._relations)
            self._relations = [
                r for r in self._relations if key not in r.get("source_entry_keys", [])
            ]
            return before - len(self._relations)

    def get_schema_version(self) -> int:
        return 1

    def knn_search(self, query_embedding: list[float], k: int) -> list[tuple[str, float]]:
        return []  # tests that exercise vector recall must use a real backend

    def vector_row_count(self) -> int:
        return 0

    def append_audit(
        self,
        action: str,
        key: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Write a JSONL audit record to the temp audit file (unit-test only).

        Each line contains at least ``action`` and ``key``; ``extra`` fields are
        merged into the top level so callers can read them as ``rec["field"]``.
        """
        record: dict[str, Any] = {"action": action, "key": key}
        if extra:
            record.update(extra)
        with self._lock:
            try:
                with open(self._audit_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            except OSError:
                pass  # best-effort — must not raise on hot path

    def close(self) -> None:
        shutil.rmtree(self._tmp_audit_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _inject_in_memory_private_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Inject :class:`InMemoryPrivateBackend` whenever ``MemoryStore`` is built
    without an explicit ``private_backend`` and no Postgres DSN is set.

    Production code raises ``ValueError`` in that case (ADR-007).  This fixture
    only intercepts construction during the unit-test session and does **not**
    touch tests that explicitly pass a backend or set ``TAPPS_BRAIN_DATABASE_URL``.
    """
    import os

    from tapps_brain import store as _store_mod

    _original_init = _store_mod.MemoryStore.__init__

    # Tests that want to verify the Postgres-only hard-fail set
    # ``TAPPS_BRAIN_TEST_NO_INMEMORY_BACKEND=1`` to disable this fixture and
    # let MemoryStore raise ValueError naturally.
    def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        if (
            kwargs.get("private_backend") is None
            and not os.environ.get("TAPPS_BRAIN_DATABASE_URL")
            and not os.environ.get("TAPPS_BRAIN_HIVE_DSN")
            and not os.environ.get("TAPPS_BRAIN_TEST_NO_INMEMORY_BACKEND")
        ):
            kwargs["private_backend"] = InMemoryPrivateBackend()
        _original_init(self, *args, **kwargs)

    monkeypatch.setattr(_store_mod.MemoryStore, "__init__", _patched_init)
    yield


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Provide a temporary project root directory."""
    return tmp_path


@pytest.fixture()
def tmp_project_with_git(tmp_path: Path) -> Path:
    """Provide a temporary project root with a git repo.

    Skips the test if ``git`` is not available on the system.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git not available on this system")

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path
