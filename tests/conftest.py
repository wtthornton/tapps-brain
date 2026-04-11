"""Shared test fixtures for tapps-brain."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING

import pytest

# sentence-transformers is not installed in the test environment.
# MemoryStore() auto-detects this via get_embedding_provider() which returns
# None when sentence-transformers is unavailable. Tests that need embeddings
# pass their own provider explicitly.

if TYPE_CHECKING:
    from pathlib import Path

_HAS_TYPER = importlib.util.find_spec("typer") is not None
_HAS_MCP = importlib.util.find_spec("mcp") is not None
_HAS_SENTENCE_TRANSFORMERS = importlib.util.find_spec("sentence_transformers") is not None


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked requires_cli / requires_mcp when extras are missing."""
    skip_cli = pytest.mark.skip(reason="requires [cli] extra (typer)")
    skip_mcp = pytest.mark.skip(reason="requires [mcp] extra (mcp)")
    for item in items:
        if "requires_cli" in item.keywords and not _HAS_TYPER:
            item.add_marker(skip_cli)
        if "requires_mcp" in item.keywords and not _HAS_MCP:
            item.add_marker(skip_mcp)


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
