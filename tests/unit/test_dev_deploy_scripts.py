"""Tests for fast local Docker deploy scripts (dev-deploy loop)."""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = _REPO_ROOT / "scripts" / name
    assert script.exists(), f"Missing {script}"
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


class TestMakefileDevDeployTargets:
    def test_makefile_declares_fast_deploy_targets(self) -> None:
        makefile = (_REPO_ROOT / "Makefile").read_text()
        for target in (
            "hive-wheel:",
            "hive-reload-http:",
            "hive-reload:",
            "dev-deploy:",
        ):
            assert target in makefile, f"Makefile missing {target}"


class TestMigrationsChangedScript:
    def test_stamp_then_unchanged_exits_nonzero(self) -> None:
        _run_script("migrations-changed.sh", "--stamp")
        result = _run_script("migrations-changed.sh")
        assert result.returncode == 1, result.stdout + result.stderr

    def test_no_stamp_exits_zero(self, tmp_path: Path, monkeypatch: object) -> None:
        stamp = _REPO_ROOT / ".docker-last-migrate-sha"
        backup = None
        if stamp.exists():
            backup = stamp.read_text()
            stamp.unlink()
        try:
            result = _run_script("migrations-changed.sh")
            assert result.returncode == 0
        finally:
            if backup is not None:
                stamp.write_text(backup)
            elif stamp.exists():
                stamp.unlink()

    def test_dockerfiles_use_buildkit_cache_mount(self) -> None:
        for name in ("Dockerfile.http", "Dockerfile.migrate"):
            content = (_REPO_ROOT / "docker" / name).read_text()
            assert "# syntax=docker/dockerfile:1" in content
            assert "mount=type=cache,target=/root/.cache/pip" in content
