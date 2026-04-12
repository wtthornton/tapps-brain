# HTTP Adapter

> This guide is a stub. For full HTTP adapter documentation see the OpenAPI spec at `docs/generated/openapi.yaml` and the source at `src/tapps_brain/http_adapter.py`.

The tapps-brain HTTP adapter exposes `/health`, `/ready`, `/metrics`, and `/snapshot` endpoints. It is enabled separately from the MCP server and requires `TAPPS_BRAIN_DATABASE_URL` to be set.

See [agentforge-integration.md](agentforge-integration.md) for wiring examples.
