"""Tests for TAP-1834: lazy imports in tapps_brain.mcp_server.

Verifies that importing ``tapps_brain.mcp_server`` does NOT eagerly load
heavy dependencies (psycopg, sentence_transformers, torch) and completes in
under 200 ms on any reasonable system.
"""

from __future__ import annotations

import subprocess
import sys


def _import_in_fresh_process(module: str) -> tuple[float, set[str]]:
    """Import *module* in a subprocess and return (elapsed_ms, sys_modules_keys)."""
    code = (
        "import sys, time, json; "
        f"t0 = time.time(); import {module}; "
        "elapsed = (time.time() - t0) * 1000; "
        "print(json.dumps({'elapsed_ms': elapsed, 'modules': list(sys.modules.keys())}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Import failed: {result.stderr}"
    import json

    data = json.loads(result.stdout.strip())
    return data["elapsed_ms"], set(data["modules"])


#: How many cold-import samples to take before judging the import budget.
#: Five keeps the test under a second while making a spurious failure require
#: every one of five independent samples to be slow.
_TIMING_SAMPLES = 5


def _fastest_import_ms(module: str, *, samples: int = _TIMING_SAMPLES) -> tuple[float, list[float]]:
    """Return ``(fastest_ms, all_samples_ms)`` for a cold import of *module*.

    Judged on the **minimum**, not a single sample or a mean, because CPU
    contention can only ever *add* time to a measurement — it never makes an
    import look faster than it is. The fastest of several samples is therefore
    the closest estimate of the true cost, and the one that does not turn a
    busy machine into a red build. This is the same reasoning behind
    ``timeit.repeat()``'s guidance to take the min rather than the average.

    The wall-clock budget exists to catch a newly-added *eager* import, which
    costs tens of milliseconds every time and so shows up in every sample. A
    regression that real cannot hide behind the minimum.
    """
    timings = [_import_in_fresh_process(module)[0] for _ in range(samples)]
    return min(timings), timings


class TestMcpServerLazyImport:
    """TAP-1834 acceptance criteria: import tapps_brain.mcp_server is fast and lean."""

    def test_import_time_under_200ms(self) -> None:
        """importing tapps_brain.mcp_server should complete in < 200 ms.

        Uses the fastest of several samples so a loaded machine does not fail
        the build: this test previously took a single wall-clock reading and
        went red under parallel pytest while the real import cost was ~50 ms,
        i.e. it reported the machine's load rather than the code's behaviour.
        """
        fastest, samples = _fastest_import_ms("tapps_brain.mcp_server")
        assert fastest < 200, (
            f"tapps_brain.mcp_server import took {fastest:.0f} ms at best "
            f"(samples: {', '.join(f'{t:.0f}' for t in samples)} ms), expected < 200 ms. "
            "Every sample was slow, so this is an eager-import regression rather than "
            "machine load — check for newly-added module-level imports and move them "
            "inside function bodies."
        )

    def test_psycopg_not_imported_at_load_time(self) -> None:
        """psycopg must not be in sys.modules after import tapps_brain.mcp_server."""
        _, modules = _import_in_fresh_process("tapps_brain.mcp_server")
        psycopg_mods = [m for m in modules if m == "psycopg" or m.startswith("psycopg.")]
        assert not psycopg_mods, (
            f"psycopg was imported at tapps_brain.mcp_server load time: {psycopg_mods}. "
            "Move psycopg imports inside function bodies."
        )

    def test_sentence_transformers_not_imported_at_load_time(self) -> None:
        """sentence_transformers must not be in sys.modules after import."""
        _, modules = _import_in_fresh_process("tapps_brain.mcp_server")
        st_mods = [m for m in modules if m.startswith("sentence_transformers")]
        assert not st_mods, (
            f"sentence_transformers was imported at load time: {st_mods}. "
            "Move sentence_transformers imports inside function bodies."
        )

    def test_torch_not_imported_at_load_time(self) -> None:
        """torch must not be in sys.modules after import tapps_brain.mcp_server."""
        _, modules = _import_in_fresh_process("tapps_brain.mcp_server")
        torch_mods = [m for m in modules if m == "torch" or m.startswith("torch.")]
        assert not torch_mods, (
            f"torch was imported at load time: {torch_mods}. "
            "Move torch imports inside function bodies."
        )

    def test_structlog_not_imported_at_load_time(self) -> None:
        """structlog must not be in sys.modules after import tapps_brain.mcp_server.

        server.py configures structlog on first create_server() call, not at
        module import time (TAP-1834).
        """
        _, modules = _import_in_fresh_process("tapps_brain.mcp_server")
        sl_mods = [m for m in modules if m == "structlog" or m.startswith("structlog.")]
        assert not sl_mods, (
            f"structlog was imported at module load time: {sl_mods}. "
            "structlog.configure() must be called inside create_server(), not at module top-level."
        )

    def test_public_api_still_accessible_after_lazy_load(self) -> None:
        """All names in tapps_brain.__all__ must resolve after first access."""
        # Run in-process since we need the module already loaded
        import tapps_brain

        missing = []
        for name in tapps_brain.__all__:
            try:
                val = getattr(tapps_brain, name)
                if val is None and name != "__version__":
                    missing.append(f"{name}=None")
            except AttributeError:
                missing.append(name)

        assert not missing, f"tapps_brain.__all__ members not accessible: {missing}"

    def test_mcp_server_public_exports_accessible(self) -> None:
        """create_server, main, ToolContext etc. must be importable from mcp_server."""
        import tapps_brain.mcp_server as ms

        assert callable(ms.create_server)
        assert callable(ms.create_operator_server)
        assert callable(ms.main)
        assert callable(ms.main_operator)
        assert ms.ToolContext is not None
