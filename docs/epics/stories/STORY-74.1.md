# kg_service.py: brain_query_events MCP and REST

## What

Implement query_events in kg_service querying experience_events with RLS.

## Where

- `src/tapps_brain/services/kg_service.py:590-640`
- `src/tapps_brain/mcp_server/tools_kg.py:1-120`
- `src/tapps_brain/http_adapter.py:3029-3100`

## Acceptance

- [ ] - [ ] kg_service.query_events filters experience_events by event_type (required)
- [ ] since
- [ ] until
- [ ] entity_id (payload file_path or subject_key)
- [ ] limit capped at 500
- [ ] brain_query_events MCP tool returns {events:[{event_id
- [ ] event_type
- [ ] payload
- [ ] ts
- [ ] agent_id
- [ ] session_id?}]
- [ ] count}
- [ ] POST /v1/experience:query REST route mirrors MCP and is profile-gated
- [ ] Tool registered defer_loading in full
- [ ] operator
- [ ] reviewer mcp_profiles.yaml

## Refs

docs/planning/epics/EPIC-074.md
