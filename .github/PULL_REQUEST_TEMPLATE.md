## Summary

<!-- Brief description of what this PR changes and why. -->

## Type of change

- [ ] Bug fix
- [ ] New feature / story
- [ ] Refactor / cleanup
- [ ] Docs only
- [ ] CI / tooling

## Related issues / stories

<!-- e.g. Closes #123, feat(story-061.6) -->

## Test plan

<!-- Describe how you tested this change. -->

---

### Epic frontmatter review (required when editing docs/planning/epics/EPIC-059–063)

> Complete this section only when your PR adds or modifies planning epic files
> (`docs/planning/epics/EPIC-059*.md`–`EPIC-063*.md`).  The **Epic Validation**
> CI job will run automatically and block merge on errors.  Delete this section
> for unrelated changes.  See [epic-validation-regression.md](docs/operations/epic-validation-regression.md)
> for how to verify the gate is working.

- [ ] `id` matches the filename stem (e.g. `id: EPIC-062` in `EPIC-062.md`)
- [ ] `status` is one of: `planned`, `in-progress`, `complete`
- [ ] `priority` is one of: `critical`, `high`, `medium`, `low`
- [ ] `created` is a valid `YYYY-MM-DD` date
- [ ] All required fields present: `id`, `title`, `status`, `priority`, `created`, `tags`, `depends_on`, `blocks`
- [ ] Ran `python3 scripts/validate_epics.py docs/planning/epics/EPIC-0XX.md` locally and it printed `OK`

---

### Telemetry review (required when touching observability code)

> Complete this section only when your PR adds or modifies spans, metrics, log
> statements, or diagnostic output.  Delete this section for doc-only or unrelated
> changes.  See [`docs/operations/telemetry-policy.md`](docs/operations/telemetry-policy.md).

- [ ] No raw memory content (`entry.content`, query strings) in span attributes
- [ ] No raw memory content in metric label values
- [ ] No raw memory content in structured log field values
- [ ] All new span names added to `SPAN_*` constants in `otel_tracer.py` and
      referenced in `docs/engineering/system-architecture.md`
- [ ] All new metric label keys are from the bounded allow-list in `otel_exporter.py`
- [ ] No new unbounded string labels (entry keys, session IDs, agent IDs, query text)
- [ ] DSN / secrets never appear in any telemetry path
- [ ] OTel Views registered if a new high-cardinality instrument was added

Files that require this section:
`otel_tracer.py`, `otel_exporter.py`, `metrics.py`, `http_adapter.py`,
`store.py` (span calls), `agent_brain.py` (span calls), any new module calling
`start_span()`.
