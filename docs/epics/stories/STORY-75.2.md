# profile_service.py: brain_profile_set and get

## What

Service layer and MCP/REST tools for profile-scoped learned data replacing tapps-mcp domain_weights.yaml.

## Where

- `src/tapps_brain/services/profile_data_service.py:1-120`
- `src/tapps_brain/mcp_server/tools_profile.py:1-80`

## Acceptance

- [ ] - [ ] profile_data_service.set/get upserts and reads value_json scoped to project_id and profile_name
- [ ] brain_profile_set(profile
- [ ] key
- [ ] value_json) returns {ok:true}; get returns {ok:true
- [ ] value_json} or {ok:false}
- [ ] POST /v1/profile/data set and get REST routes with profile gate
- [ ] Invalid JSON in value_json returns bad_json envelope consistent with KG tools

## Refs

docs/planning/epics/EPIC-075.md
