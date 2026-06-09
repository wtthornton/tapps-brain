# mcp_profiles.yaml: register profile data tools

## What

Wire profile data tools into capability profiles, OpenAPI contract, and integration tests.

## Where

- `src/tapps_brain/mcp_server/mcp_profiles.yaml:80-95`
- `docs/contracts/openapi.json` — `/v1/profile/data:set` and `/v1/profile/data:get`
- `tests/integration/test_profile_data_tools.py:1-80`

## Acceptance

- [x] `brain_profile_set` and `brain_profile_get` in `full` and `operator` profiles (`defer_loading: true`)
- [x] OpenAPI spec updated for `/v1/profile/data:*` routes
- [x] Integration test set-then-get round-trip for `domain_weights` key
- [x] Profile fixture tool set files updated (`full.txt`, `operator.txt`)

## Refs

docs/planning/epics/EPIC-075.md
