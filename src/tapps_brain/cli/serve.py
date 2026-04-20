"""``serve`` top-level command plus ``project`` sub-app (EPIC-067, EPIC-069)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Annotated, Any

import structlog
import typer

from tapps_brain.cli._common import app, project_app


@app.command("serve")
def cmd_serve(  # noqa: PLR0915  # orchestrator: many independent startup steps
    host: Annotated[
        str,
        typer.Option("--host", envvar="TAPPS_BRAIN_HTTP_HOST", help="Bind address."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", envvar="TAPPS_BRAIN_HTTP_PORT", help="HTTP data-plane TCP port."),
    ] = 8080,
    dsn: Annotated[
        str | None,
        typer.Option(
            "--dsn",
            envvar="TAPPS_BRAIN_HIVE_DSN",
            help="Postgres DSN for /ready probe (falls back to TAPPS_BRAIN_DATABASE_URL).",
        ),
    ] = None,
    mcp_host: Annotated[
        str,
        typer.Option(
            "--mcp-host",
            envvar="TAPPS_BRAIN_MCP_HOST",
            help="Bind address for the Streamable-HTTP MCP transport.",
        ),
    ] = "127.0.0.1",
    mcp_port: Annotated[
        int,
        typer.Option(
            "--mcp-port",
            envvar="TAPPS_BRAIN_MCP_HTTP_PORT",
            help=(
                "TCP port for the operator Streamable-HTTP MCP transport. "
                "Set to 0 (default) to disable the MCP transport and run "
                "HTTP-only (legacy behaviour). "
                "STORY-070.15: set to 8090 in Docker for the unified binary."
            ),
        ),
    ] = 0,
) -> None:
    """Start the HTTP adapter (liveness, readiness, metrics, /snapshot).

    STORY-070.15: when --mcp-port > 0 (or TAPPS_BRAIN_MCP_HTTP_PORT is set),
    the operator Streamable-HTTP MCP server is also started in the same process
    on the given port, making a single container sufficient for both transports.

    Reads TAPPS_BRAIN_HTTP_AUTH_TOKEN and TAPPS_BRAIN_HIVE_DSN from the
    environment automatically; the --dsn flag overrides the env var.

    Blocks until interrupted (SIGINT / SIGTERM).  Graceful shutdown stops
    both transports before exiting.
    """
    import os
    import signal

    from tapps_brain.http_adapter import HttpAdapter

    store = None
    if os.environ.get("TAPPS_BRAIN_DATABASE_URL"):
        from tapps_brain.backends import resolve_hive_backend_from_env
        from tapps_brain.store import MemoryStore

        project_root = Path(os.environ.get("TAPPS_BRAIN_SERVE_ROOT", "/var/lib/tapps-brain"))
        project_root.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(
            project_root,
            agent_id="http-adapter",
            hive_store=resolve_hive_backend_from_env(),
            hive_agent_id="http-adapter",
        )

    # ---- Security: warn when binding to all interfaces without auth ------
    # Mirror _Settings._resolve_auth_token so _FILE variants (Docker Secrets)
    # are also recognised as "auth configured".
    _auth_configured = bool(
        os.environ.get("TAPPS_BRAIN_AUTH_TOKEN")
        or os.environ.get("TAPPS_BRAIN_AUTH_TOKEN_FILE")
        or os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN")
        or os.environ.get("TAPPS_BRAIN_HTTP_AUTH_TOKEN_FILE")
        or os.environ.get("TAPPS_BRAIN_PER_TENANT_AUTH") == "1"
    )
    if host == "0.0.0.0" and not _auth_configured:
        structlog.get_logger(__name__).warning(
            "http_adapter.bind_all_interfaces_unauthenticated",
            host=host,
            port=port,
            advice=(
                "Set TAPPS_BRAIN_AUTH_TOKEN (or TAPPS_BRAIN_AUTH_TOKEN_FILE) "
                "or TAPPS_BRAIN_PER_TENANT_AUTH=1 when binding to 0.0.0.0, "
                "or restrict to 127.0.0.1."
            ),
        )
    if mcp_port > 0 and mcp_host == "0.0.0.0" and not _auth_configured:
        structlog.get_logger(__name__).warning(
            "http_adapter.mcp_bind_all_interfaces_unauthenticated",
            mcp_host=mcp_host,
            mcp_port=mcp_port,
            advice=(
                "Set TAPPS_BRAIN_AUTH_TOKEN (or TAPPS_BRAIN_AUTH_TOKEN_FILE) "
                "or TAPPS_BRAIN_PER_TENANT_AUTH=1 when binding to 0.0.0.0, "
                "or restrict --mcp-host to 127.0.0.1."
            ),
        )

    # ---- HTTP data-plane ------------------------------------------------
    adapter = HttpAdapter(host=host, port=port, dsn=dsn, store=store)
    adapter.start()
    typer.echo(f"tapps-brain HTTP adapter listening on {host}:{port}")

    # ---- Streamable-HTTP MCP transport (STORY-070.15) -------------------
    # Bearer token auth: reuses TAPPS_BRAIN_ADMIN_TOKEN (the operator-plane
    # credential already established in EPIC-069).  When unset the transport
    # refuses to start — unauthenticated operator access is not permitted on
    # a multi-agent host.
    _operator_token: str | None = os.environ.get("TAPPS_BRAIN_ADMIN_TOKEN")

    mcp_thread: threading.Thread | None = None
    mcp_server_obj: object = None
    if mcp_port > 0:
        if not _operator_token:
            typer.echo(
                "ERROR: TAPPS_BRAIN_ADMIN_TOKEN must be set to enable the operator MCP "
                "transport on port {mcp_port}. Set the token or disable MCP with --mcp-port 0.",
                err=True,
            )
            raise typer.Exit(1)
        try:
            from tapps_brain.mcp_server import create_operator_server

            mcp_server_obj = create_operator_server(
                None,  # project_dir resolved from env inside create_operator_server
                enable_hive=True,
                agent_id=os.environ.get("TAPPS_BRAIN_AGENT_ID", "serve-operator"),
            )

            # Capture locals for the thread closure.
            _mcp_host = mcp_host
            _mcp_port = mcp_port
            _tok = _operator_token

            def _run_mcp() -> None:
                import contextlib as _contextlib

                import uvicorn as _uvicorn

                # Obtain the FastMCP Streamable HTTP ASGI app.
                _asgi: object = None
                for _attr in ("streamable_http_app", "streamable_http"):
                    _fn = getattr(mcp_server_obj, _attr, None)
                    if callable(_fn):
                        with _contextlib.suppress(TypeError):
                            _asgi = _fn()
                        if _asgi is not None:
                            break

                if _asgi is None:
                    # FastMCP version too old to expose the ASGI app directly;
                    # fall back to its own runner (no auth middleware possible).
                    mcp_server_obj.run(transport="streamable-http")  # type: ignore[attr-defined]
                    return

                # Wrap with a minimal bearer-token ASGI middleware.
                _bearer_prefix = "bearer "
                _inner = _asgi

                async def _authed_app(scope: object, receive: object, send: object) -> None:
                    _scope = scope
                    if isinstance(_scope, dict) and _scope.get("type") == "http":
                        _hdrs: dict[bytes, bytes] = {
                            k.lower(): v for k, v in _scope.get("headers", [])
                        }
                        _auth = _hdrs.get(b"authorization", b"").decode()
                        if not _auth.lower().startswith(_bearer_prefix) or _auth[7:] != _tok:
                            await send(  # type: ignore[operator]
                                {
                                    "type": "http.response.start",
                                    "status": 401,
                                    "headers": [(b"content-type", b"application/json")],
                                }
                            )
                            await send(  # type: ignore[operator]
                                {
                                    "type": "http.response.body",
                                    "body": b'{"error":"unauthorized",'
                                    b'"detail":"Bearer token required for operator MCP."}',
                                }
                            )
                            return
                    await _inner(scope, receive, send)  # type: ignore[operator]

                _uvicorn.run(
                    _authed_app,
                    host=_mcp_host,
                    port=_mcp_port,
                    log_level="warning",
                )

            mcp_thread = threading.Thread(target=_run_mcp, daemon=True, name="tapps-brain-mcp")
            mcp_thread.start()
            typer.echo(
                f"tapps-brain operator MCP (streamable-http, auth=on) "
                f"listening on {mcp_host}:{mcp_port}"
            )
        except ImportError:
            typer.echo(
                "WARNING: mcp extra not installed — operator MCP transport disabled. "
                "Install with: pip install 'tapps-brain[mcp]'",
                err=True,
            )

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    stop_event.wait()

    # ---- Graceful shutdown ----------------------------------------------
    typer.echo("tapps-brain: shutting down…")
    adapter.stop()
    # MCP daemon thread exits automatically when the process terminates;
    # FastMCP does not expose a public stop() API, so we just join briefly.
    if mcp_thread is not None and mcp_thread.is_alive():
        mcp_thread.join(timeout=5.0)
    typer.echo("tapps-brain: stopped.")


# ---------------------------------------------------------------------------
# EPIC-069: project registry commands
# ---------------------------------------------------------------------------


def _open_project_registry() -> tuple[Any, Any]:
    """Build a :class:`ProjectRegistry` against the env DSN.

    Returns ``(registry, connection_manager)`` so the caller can close
    the pool when it's done.
    """
    import os

    from tapps_brain.postgres_connection import PostgresConnectionManager
    from tapps_brain.project_registry import ProjectRegistry

    dsn = (os.environ.get("TAPPS_BRAIN_DATABASE_URL") or "").strip()
    if not dsn:
        typer.echo(
            "error: TAPPS_BRAIN_DATABASE_URL must be set (postgres:// or postgresql:// DSN).",
            err=True,
        )
        raise typer.Exit(code=2)
    cm = PostgresConnectionManager(dsn)
    return ProjectRegistry(cm), cm


@project_app.command("register")
def project_register(
    project_id: str = typer.Argument(..., help="Project slug (e.g. 'alpaca')."),
    profile_path: Path = typer.Option(
        ...,
        "--profile",
        "-p",
        exists=True,
        readable=True,
        help="Path to a profile.yaml seed document.",
    ),
    approved: bool = typer.Option(
        True, "--approved/--pending", help="Mark registered row approved."
    ),
    source: str = typer.Option("admin", "--source", help="admin|auto|import"),
    notes: str = typer.Option("", "--notes", help="Optional notes for admin audit."),
) -> None:
    """Register (or overwrite) a project profile from a YAML seed file."""
    from tapps_brain.profile import load_profile

    profile = load_profile(profile_path)
    registry, cm = _open_project_registry()
    try:
        record = registry.register(
            project_id,
            profile,
            source=source,
            approved=approved,
            notes=notes,
        )
    finally:
        cm.close()
    typer.echo(
        f"Registered project '{record.project_id}' "
        f"(profile={record.profile.name}, approved={record.approved}, "
        f"source={record.source})"
    )


@project_app.command("list")
def project_list(
    approved_only: bool = typer.Option(False, "--approved-only", help="Only show approved rows."),
    pending_only: bool = typer.Option(
        False, "--pending-only", help="Only show pending (unapproved) rows."
    ),
) -> None:
    """List registered projects."""
    registry, cm = _open_project_registry()
    approved_filter: bool | None = None
    if approved_only and pending_only:
        typer.echo("error: pass at most one of --approved-only / --pending-only", err=True)
        raise typer.Exit(code=2)
    if approved_only:
        approved_filter = True
    elif pending_only:
        approved_filter = False
    try:
        rows = registry.list_all(approved=approved_filter)
    finally:
        cm.close()
    if not rows:
        typer.echo("(no projects registered)")
        return
    for r in rows:
        badge = "OK" if r.approved else "PENDING"
        typer.echo(
            f"[{badge:7s}] {r.project_id:<32s} profile={r.profile.name:<24s} source={r.source}"
        )


@project_app.command("show")
def project_show(
    project_id: str = typer.Argument(..., help="Project slug to inspect."),
) -> None:
    """Show a registered project's profile summary."""
    registry, cm = _open_project_registry()
    try:
        record = registry.get(project_id)
    finally:
        cm.close()
    if record is None:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"project_id:  {record.project_id}")
    typer.echo(f"approved:    {record.approved}")
    typer.echo(f"source:      {record.source}")
    typer.echo(f"notes:       {record.notes or '(none)'}")
    typer.echo(f"profile:     {record.profile.name} (v{record.profile.version})")
    typer.echo(f"layers:      {[la.name for la in record.profile.layers]}")
    typer.echo(f"max_entries: {record.profile.limits.max_entries}")


@project_app.command("approve")
def project_approve(
    project_id: str = typer.Argument(..., help="Project to approve."),
) -> None:
    """Flip ``approved=true`` on an existing row."""
    registry, cm = _open_project_registry()
    try:
        updated = registry.approve(project_id)
    finally:
        cm.close()
    if not updated:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Approved project '{project_id}'")


@project_app.command("delete")
def project_delete(
    project_id: str = typer.Argument(..., help="Project to delete."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove a project's profile row.

    Does **not** delete the project's memory rows from ``private_memories``.
    """
    if not yes:
        typer.confirm(
            f"Delete profile for project '{project_id}'? (memory rows are NOT deleted)",
            abort=True,
        )
    registry, cm = _open_project_registry()
    try:
        deleted = registry.delete(project_id)
    finally:
        cm.close()
    if not deleted:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Deleted project '{project_id}'")


@project_app.command("rotate-token")
def project_rotate_token(
    project_id: str = typer.Argument(..., help="Project slug to issue a token for."),
) -> None:
    """Issue (or replace) the per-tenant bearer token for a project.

    Prints the **plaintext token once** — store it securely; it is never
    retrievable again.  Requires ``argon2-cffi`` (installed with the
    ``tapps-brain[http]`` extra).

    Enable per-tenant auth with ``TAPPS_BRAIN_PER_TENANT_AUTH=1``.
    """
    registry, cm = _open_project_registry()
    try:
        try:
            plaintext = registry.rotate_token(project_id)
        except LookupError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
        except ImportError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
    finally:
        cm.close()
    typer.echo(f"Token for project '{project_id}':")
    typer.echo(plaintext)
    typer.echo("(store this token — it will not be shown again)", err=True)


@project_app.command("revoke-token")
def project_revoke_token(
    project_id: str = typer.Argument(..., help="Project slug to revoke the token for."),
) -> None:
    """Revoke (clear) the per-tenant bearer token for a project.

    After revocation the project falls back to the global
    ``TAPPS_BRAIN_AUTH_TOKEN`` check (or no auth if that is unset).
    """
    registry, cm = _open_project_registry()
    try:
        revoked = registry.revoke_token(project_id)
    finally:
        cm.close()
    if not revoked:
        typer.echo(f"no project '{project_id}' registered", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Token revoked for project '{project_id}'")
