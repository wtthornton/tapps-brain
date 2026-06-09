# mcp_profiles.yaml: register profile data tools

## What

Wire profile data tools into capability profiles, OpenAPI contract, and integration tests.

## Where

- `src/tapps_brain/mcp_server/mcp_profiles.yaml:80-95`
- `docs/contracts/openapi.json:1-50`
- `tests/integration/test_profile_data_tools.py:1-80`

## Acceptance

- [ ] - [ ] brain_profile_set and brain_profile_get in full and operator profiles defer_loading true
- [ ] OpenAPI spec updated for /v1/profile/data routes
- [ ] Integration test set then get round-trip for domain_weights key
- [ ] Profile fixture tool set files updated if project tests enumerate tools

## Refs

docs/planning/epics/EPIC-075.md
