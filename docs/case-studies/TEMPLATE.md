# Case study: [Adopter name / project]

> **Status:** draft | under review | published
> **Published:** YYYY-MM-DD
> **Contact:** [adopter contact for follow-up questions, if they consent to listing]

---

## Background

*Who you are, what you build, why you needed persistent agent memory.*

Example: "We run a coding-assistant fleet that helps our engineers review
PRs and write migration scripts. Each agent needs to remember project
conventions across sessions without us hard-coding everything into the
system prompt."

---

## Deployment shape

| Parameter | Value |
|-----------|-------|
| tapps-brain version | v3.x.x |
| Deployment mode | Docker Compose / K8s / embedded library |
| Postgres | self-hosted pg17 / RDS / Cloud SQL / … |
| Brain containers | e.g. 1 sidecar shared by N project containers |
| Agent containers | e.g. N per project, M projects |
| Dashboard | yes / no |
| Hive | yes / no |

---

## Memory volume

| Metric | Value (approximate) |
|--------|---------------------|
| Entries per agent at steady-state | ~X |
| Peak total entries across all projects | ~Y |
| Growth rate | ~Z entries / day |
| Typical recall token budget | 1,500–2,500 tokens |

---

## Agent topology

*Describe the agents and how they use memory.*

- **Agent type(s):** e.g. coding assistant, support bot, data-ingestion script
- **Number of agents:** e.g. 5–20 concurrently
- **Profile used:** e.g. `repo-brain`, custom YAML based on `repo-brain`
- **Hive usage:** e.g. `domain` scope for sharing conventions across agents
  on the same project; `private` for per-agent task state

---

## Multi-tenancy setup

- **`project_id` scheme:** e.g. one per GitHub org, or one per customer tenant
- **Profile per project:** yes / no — brief description
- **RLS confirmed:** yes / no (run `tapps-brain maintenance health` to check)
- **Token auth:** yes / no

---

## Measured outcome

*One concrete before/after metric, or a qualitative change that your team
noticed.*

Examples:
- "Reduced system-prompt size by 40% — conventions now recalled from
  memory on-demand rather than injected wholesale."
- "Agents stopped asking the same setup questions in each new session after
  ~2 weeks of memory accumulation."
- "Cut time-to-first-correct-suggestion from ~3 turns to ~1 turn for
  repeat tasks."

**Outcome:** _your answer here_

---

## What we'd do differently

*Optional. Anything that was rougher than expected, or a config you wish
you'd set from the start.*

---

## Quotes

> "…" — name, role (if they consent to attribution)

---

## References

- tapps-brain docs used: [link], [link]
- Related guides: [Fleet topology](../guides/fleet-topology.md), [Hive guide](../guides/hive.md)
