# Observability

tapps-brain exposes structured **metrics**, **health**, **audit**, **diagnostics**, and **feedback** surfaces through `MemoryStore` APIs, CLI commands, and MCP tools/resources. See `docs/engineering/call-flows.md` for where these run in recall and maintenance paths.

## OpenTelemetry (`otel_exporter`)

The optional `[otel]` extra installs types and helpers in `src/tapps_brain/otel_exporter.py` (`create_exporter`, `OTelExporter`).

**Status:** the exporter module is **not** initialized from `MemoryStore`, the Typer CLI, or `mcp_server.py`. Nothing turns OTel “on” at process start today; only unit tests exercise the module.

**Operators:** installing `tapps-brain[otel]` alone does not attach spans to store operations. Product wiring (CLI flag, env gate, or store hooks) is tracked under **EPIC-032** (`docs/planning/epics/EPIC-032.md`).

**Developers:** see `tests/unit/test_otel_exporter.py` for intended usage patterns once wired.
