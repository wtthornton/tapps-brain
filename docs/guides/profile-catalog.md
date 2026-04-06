# Profile Catalog

tapps-brain ships with 6 built-in profiles covering common AI agent use cases. Each profile can be used directly, extended via `extends`, or used as a reference for designing your own.

## Quick Reference

| Profile | Layers | Longest half-life | Decay model | Max entries | Token budget | Best for |
|---------|--------|-------------------|-------------|-------------|-------------|----------|
| [repo-brain](#repo-brain) | 4 | 180d | exponential | 5,000 | 3,000 | Code repos, coding assistants |
| [personal-assistant](#personal-assistant) | 5 | 365d | power_law | 5,000 | 4,000 | Personal AI assistants |
| [customer-support](#customer-support) | 4 | 120d | exponential | 5,000 | 3,000 | Support agents, ticketing |
| [research-knowledge](#research-knowledge) | 4 | 365d | power_law | 10,000 | 4,000 | Research, knowledge management |
| [project-management](#project-management) | 4 | 180d | exponential | 5,000 | 3,000 | PM tools, sprint planning |
| [home-automation](#home-automation) | 5 | 365d | power_law | 5,000 | 2,000 | IoT, smart home agents |

## Choosing a Profile

```
Is your agent a coding assistant?
  └─ Yes → repo-brain (default)

Does your agent need to remember user identity/preferences long-term?
  └─ Yes → personal-assistant

Does your agent handle customer interactions/tickets?
  └─ Yes → customer-support

Does your agent manage research or accumulate knowledge over months?
  └─ Yes → research-knowledge

Does your agent plan sprints, track decisions, or manage backlogs?
  └─ Yes → project-management

Does your agent manage devices, sensors, or household routines?
  └─ Yes → home-automation

None of the above?
  └─ Start with repo-brain and customize, or create your own
     (see the Profile Design Guide)
```

---

## repo-brain

**Default profile.** Memory for code repositories and AI coding assistants.

### Layers

| Layer | Half-life | Description | Promotes to | Demotes to |
|-------|-----------|-------------|-------------|------------|
| `architectural` | 180 days | System decisions, tech stack, infrastructure | — | `pattern` |
| `pattern` | 60 days | Coding conventions, API/design patterns | `architectural` | `context` |
| `procedural` | 30 days | Workflows, deployment steps, build commands | `pattern` | `context` |
| `context` | 14 days | Session-specific facts, current task details | `procedural` | — |

### Scoring

| Relevance | Confidence | Recency | Frequency |
|-----------|------------|---------|-----------|
| **40%** | **30%** | 15% | 15% |

Relevance-heavy — keyword match is the strongest signal. Good for searching technical terms, API names, and command patterns.

### When to use

- AI coding assistants (Claude Code, Cursor, Copilot)
- Dev tools that need to remember repo conventions
- CI/CD agents that track build patterns

### When NOT to use

- Personal assistants (use `personal-assistant` — identity layer with power-law decay)
- Agents processing high-volume events (use `home-automation` or custom with shorter half-lives)

---

## personal-assistant

Memory for personal AI assistants, general-purpose agents, and OpenClaw.

### Layers

| Layer | Half-life | Decay model | Description | Promotes to | Demotes to |
|-------|-----------|-------------|-------------|-------------|------------|
| `identity` | 365 days | **power_law (0.5)** | User identity, core preferences, goals | — | `long-term` |
| `long-term` | 90 days | exponential | Durable facts, relationships, learned preferences | `identity` | `procedural` |
| `procedural` | 30 days | exponential | How-to knowledge, routines, workflows | `long-term` | `short-term` |
| `short-term` | 7 days | exponential | Recent conversations, active tasks | `procedural` | `ephemeral` |
| `ephemeral` | 1 day | exponential | Current conversation state | `short-term` | — |

### Scoring

| Relevance | Confidence | Recency | Frequency |
|-----------|------------|---------|-----------|
| 30% | 20% | **30%** | **20%** |

Recency-heavy — what the user said recently matters as much as keyword relevance. Frequency is boosted to surface recurring topics.

### Key design decisions

- **Power-law on `identity`**: Core user preferences (name, role, allergies, communication style) decay negligibly. After 2 years, confidence drops only ~11% vs. 75% with exponential.
- **Procedural tier bridges the 7d–90d gap**: Routines, how-to knowledge, and workflows persist for 30 days without requiring the 20-access bar for long-term promotion.
- **Highest token budget (4000)**: Personal assistants need more context for nuanced responses.
- **Low promotion bar for `ephemeral` → `short-term`**: Just 2 accesses and 1 day. If you mention something twice, it persists.
- **High promotion bar for `long-term` → `identity`**: 20 accesses, 60 days, 0.8 confidence. Only truly core information reaches identity.
- **Conservative consolidation threshold (0.65)**: Lower than the default 0.7 to reduce false merges on semantically varied personal data.

### Importance tags

| Tag | Multiplier | Effect |
|-----|-----------|--------|
| `identity` | 2.0x | 730-day effective half-life in identity layer |
| `preference` | 1.5x | 547-day effective half-life in identity layer |
| `important` | 1.5x | 135-day effective half-life in long-term layer |
| `recurring` | 1.3x | 117-day effective half-life in long-term layer |
| `routine` | 1.5x | 45-day effective half-life in procedural layer |
| `how-to` | 1.3x | 39-day effective half-life in procedural layer |
| `workflow` | 1.3x | 39-day effective half-life in procedural layer |

---

## customer-support

Memory for customer support agents and ticketing systems.

### Layers

| Layer | Half-life | Description | Promotes to | Demotes to |
|-------|-----------|-------------|-------------|------------|
| `product-knowledge` | 120 days | Product features, pricing, policies, known issues | — | `customer-patterns` |
| `customer-patterns` | 60 days | Common issues, playbooks, escalation triggers | `product-knowledge` | `interaction-history` |
| `interaction-history` | 14 days | Recent interactions, open tickets | `customer-patterns` | `session-context` |
| `session-context` | 3 days | Current conversation, active ticket | `interaction-history` | — |

### Scoring

| Relevance | Confidence | Recency | Frequency |
|-----------|------------|---------|-----------|
| **35%** | 25% | 15% | **25%** |

Frequency is high (25%) — recurring issues should surface aggressively. If 50 customers hit the same bug, that pattern should dominate recall.

### Importance tags

| Tag | Multiplier | Effect |
|-----|-----------|--------|
| `policy` | 2.0x | Policies persist 2x longer |
| `vip` | 2.0x | VIP customer context persists 2x longer |
| `pricing` | 1.5x | Pricing info decays slower |
| `known-issue` | 1.5x | Known issues stay relevant longer |
| `escalated` | 1.5x | Escalated ticket context persists |

---

## research-knowledge

Memory for research agents, knowledge management, and long-form content curation.

### Layers

| Layer | Half-life | Decay model | Description | Promotes to | Demotes to |
|-------|-----------|-------------|-------------|-------------|------------|
| `established-facts` | 365 days | **power_law (0.7)** | Verified findings, published results | — | `working-knowledge` |
| `working-knowledge` | 60 days | exponential | Hypotheses, draft findings, literature notes | `established-facts` | `observations` |
| `observations` | 21 days | exponential | Raw observations, experiment notes | `working-knowledge` | `scratch` |
| `scratch` | 3 days | exponential | Quick notes, brainstorm ideas | `observations` | — |

### Scoring

| Relevance | Confidence | Recency | Frequency |
|-----------|------------|---------|-----------|
| **50%** | 25% | 10% | 15% |

Relevance-dominant (50%) — in research, finding the right content by keyword/topic is more important than how recent it is. Recency is minimized (10%) because established research doesn't lose value with age.

### Key design decisions

- **Largest limits**: 10,000 max entries, 8192 max value length. Research corpora are larger and individual entries are longer than other domains.
- **Power-law on `established-facts`**: Verified research should persist indefinitely. Exponent 0.7 (vs. 0.5 for personal-assistant identity) means slightly faster initial decay but still very persistent.
- **High promotion threshold to `established-facts`**: 12 accesses, 30 days, 0.8 confidence. Only well-validated findings get promoted.

### Importance tags

| Tag | Multiplier | Effect |
|-----|-----------|--------|
| `verified` | 2.0x | Verified findings persist twice as long |
| `replicated` | 2.0x | Replicated results persist twice as long |
| `published` | 1.5x | Published work decays slower |
| `cited` | 1.5x | Cited work decays slower |
| `promising` | 1.3x | Promising hypotheses get a modest boost |

---

## project-management

Memory for project management, sprint planning, and coordination agents.

### Layers

| Layer | Half-life | Description | Promotes to | Demotes to |
|-------|-----------|-------------|-------------|------------|
| `decisions` | 180 days | Approved decisions, strategic direction, commitments | — | `plans` |
| `plans` | 45 days | Active plans, milestones, dependencies, timelines | `decisions` | `activity` |
| `activity` | 14 days | Status changes, meeting notes, action items | `plans` | `noise` |
| `noise` | 5 days | FYI updates, casual observations, transient context | `activity` | — |

### Scoring

| Relevance | Confidence | Recency | Frequency |
|-----------|------------|---------|-----------|
| **35%** | 25% | **25%** | 15% |

Balanced with recency boost — project management needs recent status (what happened this sprint) ranked alongside keyword relevance.

### Importance tags

| Tag | Multiplier | Effect |
|-----|-----------|--------|
| `approved` | 2.0x | Approved decisions persist much longer |
| `blocker` | 2.0x | Blockers stay prominent |
| `budget` | 1.5x | Budget decisions decay slower |
| `deadline` | 1.5x | Deadline-related context persists |
| `risk` | 1.5x | Risk flags stay relevant |
| `action-item` | 1.3x | Action items get a modest boost |

---

## home-automation

Memory for home automation, IoT agents, and smart home systems.

### Layers

| Layer | Half-life | Decay model | Description | Promotes to | Demotes to |
|-------|-----------|-------------|-------------|-------------|------------|
| `household-profile` | 365 days | **power_law (0.5)** | Residents, preferences, allergies, routines | — | `learned-patterns` |
| `learned-patterns` | 60 days | exponential | Behavioral patterns, energy usage, schedules | `household-profile` | `recent-events` |
| `recent-events` | 7 days | exponential | Device events, anomalies, guest visits | `learned-patterns` | `transient` |
| `future-events` | 90 days | exponential | Scheduled events, reminders, maintenance | `learned-patterns` | `transient` |
| `transient` | 1 day | exponential | Sensor readings, motion events, door open/close | `recent-events` | — |

### Scoring

| Relevance | Confidence | Recency | Frequency |
|-----------|------------|---------|-----------|
| 30% | 20% | **35%** | 15% |

Recency-dominant (35%) — "the front door opened 2 minutes ago" must outrank "the front door opened last week" regardless of keyword relevance.

### Key design decisions

- **5 layers** (unique among built-in profiles): The `future-events` layer has a 90-day half-life for scheduled maintenance, upcoming events, and reminders. It doesn't fit into the standard past-focused hierarchy.
- **Highest importance multipliers**: `safety: 3.0`, `allergy: 3.0`, `medical: 3.0`. Forgetting someone's peanut allergy is dangerous. These tags give a 3x half-life multiplier.
- **1-day session expiry**: IoT generates many session-scoped events; aggressive cleanup prevents store bloat.
- **7-day GC floor retention**: Transient sensor data clears aggressively; household-profile and learned-patterns persist via their long half-lives and importance tags.

### Importance tags

| Tag | Multiplier | Effect |
|-----|-----------|--------|
| `safety` | **3.0x** | Safety-critical info persists 3x longer |
| `allergy` | **3.0x** | Allergy info persists 3x longer |
| `medical` | **3.0x** | Medical info persists 3x longer |
| `anomaly` | 2.0x | Anomalies persist longer for pattern detection |
| `maintenance` | 1.5x | Maintenance schedules decay slower |
| `guest` | 1.3x | Guest visit context gets a modest boost |
| `energy` | 1.3x | Energy patterns get a modest boost |
| `comfort` | 1.2x | Comfort preferences get a small boost |

---

## Creating Your Own Profile

See the [Profile Design Guide](profiles.md) for comprehensive guidance on:
- Choosing the right number of layers
- Setting half-lives for your domain
- Tuning scoring weights
- Configuring promotion/demotion
- Setting up Hive integration
- Avoiding common anti-patterns

### Starting from scratch vs. extending

| Approach | When to use |
|----------|-------------|
| **Start from scratch** | Your domain is fundamentally different from all built-in profiles |
| **Extend a built-in** | Your domain is similar but needs tuning (e.g., `extends: "repo-brain"` with different half-lives) |
| **Copy and modify** | You want full control but want a starting point |

### Contributing profiles

If you design a profile for a common use case (e.g., healthcare, legal, education, gaming), consider contributing it back. Place the YAML file in `src/tapps_brain/profiles/` and add an entry to this catalog.

---

## Further Reading

- [Profile Design Guide](profiles.md) — Full schema reference and design guidance
- [Hive Guide](hive.md) — Cross-agent memory sharing
- [Federation Guide](federation.md) — Cross-project memory sharing
