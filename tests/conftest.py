"""Shared test fixtures for tapps-brain."""

from __future__ import annotations

import importlib.util
import os
from typing import TYPE_CHECKING

import pytest

# Disable auto-embedding in tests by default — avoids loading the heavy
# sentence-transformers model for every MemoryStore() call.  Tests that
# explicitly need embeddings can pass their own provider.
os.environ.setdefault("TAPPS_SEMANTIC_SEARCH", "0")

if TYPE_CHECKING:
    from pathlib import Path

_HAS_TYPER = importlib.util.find_spec("typer") is not None
_HAS_MCP = importlib.util.find_spec("mcp") is not None


def pytest_collection_modifyitems(config, items):
    """Auto-skip tests marked requires_cli / requires_mcp when extras are missing."""
    skip_cli = pytest.mark.skip(reason="requires [cli] extra (typer)")
    skip_mcp = pytest.mark.skip(reason="requires [mcp] extra (mcp)")
    for item in items:
        if "requires_cli" in item.keywords and not _HAS_TYPER:
            item.add_marker(skip_cli)
        if "requires_mcp" in item.keywords and not _HAS_MCP:
            item.add_marker(skip_mcp)


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
