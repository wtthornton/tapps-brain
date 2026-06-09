# tools_kg.py: brain_get_neighbors entity_refs

## What

Optional P2 convenience: auto-resolve canonical names before get_neighbors so tapps-mcp need not pre-call brain_resolve_entity.

## Where

- `src/tapps_brain/services/kg_service.py:527-588`
- `src/tapps_brain/mcp_server/tools_kg.py:425-500`

## Acceptance

- [ ] - [ ] brain_get_neighbors accepts optional entity_refs_json array of {entity_type
- [ ] canonical_name}
- [ ] Each ref resolved via resolve_entity before neighbourhood query; bad refs return structured error
- [ ] Existing entity_ids_json path unchanged; entity_refs is convenience not replacement for UUIDv5
- [ ] Unit test proves same file path resolves to stable UUID across two resolve calls

## Refs

docs/planning/epics/EPIC-074.md
