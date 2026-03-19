"""Shared test fixtures for tapps-brain."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Provide a temporary project root directory."""
    return tmp_path


@pytest.fixture()
def tmp_project_with_git(tmp_path: Path) -> Path:
    """Provide a temporary project root with a git repo."""
    import subprocess

    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    return tmp_path
