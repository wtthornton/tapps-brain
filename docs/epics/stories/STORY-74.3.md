# experience.py: EntitySpec type/id shorthand

## What

Add type/id coercion to EntitySpec matching tapps-mcp quality_metric entity payloads so KG side-effects are not silently skipped.

## Where

- `src/tapps_brain/experience.py:68-100`
- `tests/unit/test_tap_2675_payload_robustness.py:1-50`

## Acceptance

- [ ] - [ ] EntitySpec validator maps type->entity_type and id->canonical_name when canonical fields absent
- [ ] {"type":"file"
- [ ] "id":"path"} upserts entity same as explicit entity_type/canonical_name
- [ ] Unit tests cover type/id
- [ ] key shorthand
- [ ] and explicit fields precedence
- [ ] brain_record_event with tapps-mcp entity shape returns entity_ids not warnings

## Refs

docs/planning/epics/EPIC-074.md
