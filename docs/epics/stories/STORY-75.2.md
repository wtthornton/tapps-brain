# profile_service.py: brain_profile_set and get

## What

Service layer and MCP/REST tools for profile-scoped learned data replacing tapps-mcp domain_weights.yaml.

## Where

- `src/tapps_brain/services/profile_data_service.py:1-120`
- `src/tapps_brain/mcp_server/tools_profile.py:1-80`
- `src/tapps_brain/http_adapter.py` — `POST /v1/profile/data:set` and `POST /v1/profile/data:get`

## Acceptance

- [x] `profile_data_service.set/get` upserts and reads `value_json` scoped to `project_id` and `profile_name`
- [x] `brain_profile_set(profile, key, value_json)` returns `{ok: true}`; get returns `{ok: true, value_json}` or `{ok: false}`
- [x] REST routes mirror MCP with profile gate on `full` / `operator` profiles
- [x] Invalid JSON in `value_json` returns `bad_json` envelope consistent with KG tools

## Refs

docs/planning/epics/EPIC-075.md
