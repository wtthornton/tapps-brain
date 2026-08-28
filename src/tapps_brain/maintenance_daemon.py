"""``tapps-brain-maintenance`` compose service entrypoint (TAP-6698, KB-3.4).

A small FastAPI app whose only job is a background loop: every
``TAPPS_BRAIN_MAINTENANCE_INTERVAL_MINUTES`` (default 60) it runs one
:func:`~tapps_brain.services.maintenance_cycle.run_maintenance_cycle` pass
and records the outcome for ``/health`` and ``/metrics``. Reference shape is
the one-shot ``tapps-brain-migrate`` service's build/depends_on/env style
(Ruling 14) — this differs only in that it loops instead of running once.

Run via ``python -m tapps_brain.maintenance_daemon`` (the compose service's
``command:`` override on the ``tapps-brain-http`` image).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_DEFAULT_INTERVAL_MINUTES = 60
_DEFAULT_PARTITION_MONTHS_AHEAD = 3
_DEFAULT_SINGLE_ENTRY_AGE_DAYS = 30


class _State:
    """Mutable status shared between the background loop and the HTTP routes."""

    def __init__(self) -> None:
        self.cycle_count = 0
        self.last_cycle_at: float | None = None
        self.last_cycle_ok: bool | None = None
        self.last_cycle_error: str | None = None
        self.last_cycle_duration_s: float | None = None
        self.loop_alive = False


def _settings_from_env() -> dict[str, Any]:
    interval_minutes = int(
        os.environ.get("TAPPS_BRAIN_MAINTENANCE_INTERVAL_MINUTES", str(_DEFAULT_INTERVAL_MINUTES))
    )
    dry_run = os.environ.get("TAPPS_BRAIN_MAINTENANCE_DRY_RUN", "") == "1"
    retention_env = os.environ.get("TAPPS_BRAIN_EVENTS_RETENTION_MONTHS", "")
    dsn = os.environ.get("TAPPS_BRAIN_DATABASE_URL", "")
    project_root = Path(os.environ.get("TAPPS_BRAIN_SERVE_ROOT", "/var/lib/tapps-brain"))
    return {
        "interval_minutes": interval_minutes,
        "dry_run": dry_run,
        "retention_env": retention_env,
        "dsn": dsn,
        "project_root": project_root,
    }


async def _maintenance_loop(state: _State) -> None:
    from tapps_brain.services.maintenance_cycle import run_maintenance_cycle

    settings = _settings_from_env()
    state.loop_alive = True
    try:
        while True:
            start = time.monotonic()
            try:
                await asyncio.to_thread(
                    run_maintenance_cycle,
                    project_root=settings["project_root"],
                    dsn=settings["dsn"],
                    dry_run=settings["dry_run"],
                    partition_months_ahead=_DEFAULT_PARTITION_MONTHS_AHEAD,
                    retention_env=settings["retention_env"],
                    single_entry_age_days=_DEFAULT_SINGLE_ENTRY_AGE_DAYS,
                )
                state.last_cycle_ok = True
                state.last_cycle_error = None
            except Exception as exc:  # one bad cycle must not kill the loop
                state.last_cycle_ok = False
                state.last_cycle_error = str(exc)
                logger.warning("maintenance_daemon.cycle_failed", error=str(exc))
            state.cycle_count += 1
            state.last_cycle_at = time.time()
            state.last_cycle_duration_s = time.monotonic() - start
            await asyncio.sleep(max(60, settings["interval_minutes"] * 60))
    finally:
        state.loop_alive = False


def create_app() -> FastAPI:
    state = _State()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_maintenance_loop(state))
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=_lifespan)
    app.state.maintenance_status = state

    @app.get("/health")
    async def _health() -> JSONResponse:
        settings = _settings_from_env()
        body = {
            "ok": state.loop_alive,
            "loop_alive": state.loop_alive,
            "cycle_count": state.cycle_count,
            "last_cycle_at": state.last_cycle_at,
            "last_cycle_ok": state.last_cycle_ok,
            "last_cycle_error": state.last_cycle_error,
            "dry_run": settings["dry_run"],
            "interval_minutes": settings["interval_minutes"],
        }
        return JSONResponse(status_code=200 if state.loop_alive else 503, content=body)

    @app.get("/metrics")
    async def _metrics() -> PlainTextResponse:
        lines = [
            "# HELP tapps_brain_maintenance_cycle_count Total maintenance cycles run.",
            "# TYPE tapps_brain_maintenance_cycle_count counter",
            f"tapps_brain_maintenance_cycle_count {state.cycle_count}",
            "# HELP tapps_brain_maintenance_last_cycle_ok 1 if the last cycle succeeded.",
            "# TYPE tapps_brain_maintenance_last_cycle_ok gauge",
            f"tapps_brain_maintenance_last_cycle_ok {1 if state.last_cycle_ok else 0}",
            "# HELP tapps_brain_maintenance_last_cycle_duration_seconds Last cycle duration.",
            "# TYPE tapps_brain_maintenance_last_cycle_duration_seconds gauge",
            f"tapps_brain_maintenance_last_cycle_duration_seconds "
            f"{state.last_cycle_duration_s or 0.0}",
        ]
        return PlainTextResponse("\n".join(lines) + "\n")

    return app


app = create_app()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("TAPPS_BRAIN_MAINTENANCE_PORT", "8095"))
    # Container-internal bind, matches http_adapter's own uvicorn invocation.
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
