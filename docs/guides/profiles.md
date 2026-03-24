# Memory Profiles: Designing Custom Memory for Any AI Agent

tapps-brain ships with a configurable profile system that lets you define custom memory layers, decay models, scoring weights, promotion rules, and limits — turning it from a code-repo memory into a universal brain for any AI agent.

> **48% of documentation readers in 2026 are AI agents** ([Mintlify](https://mintlify.com)). This guide is written for both human integrators and AI agents designing memory systems.

## Table of Contents

- [What is a Profile?](#what-is-a-profile)
- [Quick Start](#quick-start)
- [Profile Resolution Order](#profile-resolution-order)
- [Layer Design](#layer-design)
- [Decay Models](#decay-models)
- [Scoring Weights](#scoring-weights)
- [Promotion & Demotion](#promotion--demotion)
- [Importance Tags](#importance-tags)
- [Source Confidence & Ceilings](#source-confidence--ceilings)
- [Garbage Collection](#garbage-collection)
- [Recall Configuration](#recall-configuration)
- [Limits](#limits)
- [Hive Configuration](#hive-configuration)
- [Inheritance](#inheritance)
- [Architecture Patterns](#architecture-patterns)
- [Anti-Patterns](#anti-patterns)
- [Reference: Full Schema](#reference-full-schema)

---

## What is a Profile?

A profile is a YAML file that configures **every tunable aspect** of tapps-brain's memory behavior:

- **Layers** (tiers) — how many, what they're called, how fast they decay
- **Scoring** — how search results are ranked (relevance vs. confidence vs. recency vs. frequency)
- **Promotion/demotion** — when memories move between layers based on usage
- **Decay** — exponential or power-law, per-layer half-lives, importance tag multipliers
- **GC** — when memories are archived, session expiry, contradiction thresholds
- **Limits** — max entries, key/value sizes, tag count
- **Hive** — which layers auto-propagate to the shared brain, which stay private

Without a profile, tapps-brain defaults to `repo-brain` — the 4-layer profile designed for code repositories. With a profile, you can model any domain: personal assistants, customer support, research, project management, home automation, or your own custom domain.

---

## Quick Start

### Use a built-in profile

```python
from pathlib import Path
from tapps_brain import MemoryStore

# Use the personal-assistant profile
store = MemoryStore(Path("."), profile_name="personal-assistant")
```

### Create a custom profile

Drop a YAML file at `{project}/.tapps-brain/profile.yaml`:

```yaml
profile:
  name: "my-agent"
  version: "1.0"
  description: "Memory for my custom agent"

  layers:
    - name: "core-knowledge"
      description: "Permanent domain facts"
      half_life_days: 365
      decay_model: "power_law"
      decay_exponent: 0.5

    - name: "learned-patterns"
      description: "Patterns observed across sessions"
      half_life_days: 60

    - name: "working-memory"
      description: "Current session context"
      half_life_days: 7
```

Everything not specified falls back to sensible defaults (exponential decay, standard scoring weights, 500 max entries).

### Minimal viable profile

Only `name` and `layers` (with `half_life_days`) are required:

```yaml
profile:
  name: "minimal"
  layers:
    - name: "important"
      half_life_days: 90
    - name: "temporary"
      half_life_days: 7
```

---

## Profile Resolution Order

tapps-brain checks three locations in order, using the first one found:

1. **Project-specific**: `{project_dir}/.tapps-brain/profile.yaml`
2. **User-global**: `~/.tapps-brain/profile.yaml`
3. **Built-in**: loaded by name from the `tapps_brain/profiles/` package directory

```python
# Explicit profile name (skips #1 and #2, goes straight to built-in)
store = MemoryStore(Path("."), profile_name="research-knowledge")

# Resolution order (checks project, then user, then falls back to "repo-brain")
store = MemoryStore(Path("."))
```

---

## Layer Design

Layers are the heart of a profile. Each layer defines a **category of memory** with its own decay characteristics.

### How many layers?

| Layers | When to use | Example |
|--------|-------------|---------|
| **2** | Simple use cases with clear "permanent vs. temporary" split | Chatbot with core knowledge + session context |
| **3** | Most agents — maps to cognitive short/long/permanent memory | Personal assistant, customer support |
| **4** | Standard for structured domains with distinct knowledge categories | Code repos (architectural/pattern/procedural/context) |
| **5-6** | Complex multi-signal systems with different temporal patterns | Home automation (5 layers), delivery pipelines |
| **7+** | Rarely needed — consider if some layers can be distinguished by tags instead | Over-engineering risk |

### Choosing half-lives

Match half-lives to the **natural lifetime of the information** in your domain:

| Information type | Recommended half-life | Example |
|-----------------|----------------------|---------|
| Identity / permanent facts | 365 days (power_law) | User name, core preferences, system architecture |
| Stable domain knowledge | 90-180 days | API patterns, product knowledge, expert reputation |
| Active patterns | 30-60 days | Coding conventions, workflow habits, routing heuristics |
| Recent context | 7-14 days | Active tasks, recent conversations, sprint goals |
| Ephemeral / signals | 1-3 days | Sensor readings, individual test results, chat state |

### Layer naming

Use descriptive, domain-specific names — not generic tier names:

```yaml
# Good — domain-specific
layers:
  - name: "product-knowledge"     # customer support
  - name: "established-facts"     # research
  - name: "household-profile"     # home automation
  - name: "platform-invariants"   # delivery pipeline

# Avoid — generic
layers:
  - name: "tier-1"
  - name: "tier-2"
```

### The 2026 consensus on memory tiers

Research in 2026 converges on three cognitive memory types as the baseline:

- **Episodic** — what happened (events, interactions, session traces)
- **Semantic** — what I know (facts, relationships, domain knowledge)
- **Procedural** — how to do it (workflows, patterns, learned skills)

Your layers don't need to map 1:1 to these types, but the best profiles tend to cover all three. The `repo-brain` profile maps them as: architectural/pattern (semantic), procedural (procedural), context (episodic).

---

## Decay Models

Each layer can use one of two decay models:

### Exponential decay (default)

```
confidence × 0.5^(days / half_life)
```

Drops by 50% every half-life period. Straightforward and predictable. Use for most layers.

```yaml
- name: "working-memory"
  half_life_days: 14
  decay_model: "exponential"  # default, can omit
```

### Power-law decay

```
confidence × (1 + days / (9 × half_life))^(-exponent)
```

Decays fast initially, then slows dramatically. Memories that survive the first period persist for a very long time. Use for identity, core knowledge, and permanent facts.

```yaml
- name: "identity"
  half_life_days: 365
  decay_model: "power_law"
  decay_exponent: 0.5   # lower = slower long-term decay
```

### Comparing decay curves at confidence 0.95

| Time | Exponential (180d) | Power-law (365d, exp=0.5) |
|------|-------------------|--------------------------|
| 30 days | 0.85 | 0.94 |
| 90 days | 0.67 | 0.93 |
| 180 days | 0.475 | 0.91 |
| 365 days | 0.24 | 0.89 |
| 730 days | 0.06 | 0.84 |

Power-law is dramatically more persistent. Reserve it for layers where forgetting is unacceptable.

### Confidence floor

Every layer has a `confidence_floor` (default 0.10). Confidence never decays below this value — it prevents total forgetting. Set to 0.0 for ephemeral layers where complete forgetting is desired.

---

## Scoring Weights

When you search or recall memories, results are ranked by a composite score:

```
score = w_relevance × relevance + w_confidence × confidence + w_recency × recency + w_frequency × frequency
```

**Weights must sum to ~1.0** (tolerance: 0.95–1.05).

### Weight selection guide

| Priority | Relevance | Confidence | Recency | Frequency | Best for |
|----------|-----------|------------|---------|-----------|----------|
| **Relevance-first** | 0.50 | 0.25 | 0.10 | 0.15 | Research, knowledge bases (keyword match matters most) |
| **Balanced** (default) | 0.40 | 0.30 | 0.15 | 0.15 | Code repos, general-purpose agents |
| **Recency-first** | 0.30 | 0.20 | 0.35 | 0.15 | Home automation, IoT, real-time agents |
| **Confidence-first** | 0.25 | 0.40 | 0.15 | 0.20 | QA, compliance, safety-critical systems |
| **Frequency-first** | 0.30 | 0.20 | 0.15 | 0.35 | Customer support (recurring patterns matter) |
| **Recency-relevance** | 0.30 | 0.20 | 0.30 | 0.20 | Personal assistants (recent + relevant) |

### Additional scoring parameters

```yaml
scoring:
  bm25_norm_k: 5.0       # BM25 normalization constant (score/(score+K))
  frequency_cap: 20       # Access count cap (prevents frequency from dominating)
```

`bm25_norm_k` controls how BM25 text relevance scores map to 0.0–1.0. A score of K maps to 0.5 normalized. Lower K = more aggressive normalization. Default 5.0 works well for most corpora.

---

## Promotion & Demotion

Layers can form a hierarchy where memories move up (promotion) or down (demotion) based on usage patterns.

### Promotion

Triggered on **reinforcement** (`store.reinforce(key)`). When all three criteria are met, the memory's tier changes:

```yaml
- name: "short-term"
  promotion_to: "long-term"
  promotion_threshold:
    min_access_count: 5    # accessed at least 5 times
    min_age_days: 3        # at least 3 days old
    min_confidence: 0.5    # effective confidence >= 0.5
```

**Desirable difficulty bonus**: Nearly-forgotten memories get larger reinforcement boosts when accessed. A memory at 0.2 confidence that gets reinforced receives a bigger boost than one at 0.8. This is based on the spacing effect from cognitive science (Roediger & Karpicke, 2006).

**Stability growth**: Repeatedly reinforced memories decay more slowly. Effective half-life grows with `base × (1 + log1p(reinforce_count) × 0.3)`. A memory reinforced 10 times has ~1.72x the base half-life.

### Setting promotion thresholds

| Promotion path | Recommended thresholds | Rationale |
|---------------|----------------------|-----------|
| Ephemeral → Short-term | 2 accesses, 1 day, 0.3 confidence | Low bar — if mentioned twice, it's worth keeping |
| Short-term → Long-term | 5 accesses, 3-7 days, 0.5 confidence | Must prove useful across multiple sessions |
| Long-term → Permanent | 15-25 accesses, 30-60 days, 0.7-0.8 confidence | High bar — only genuinely core information |

### Demotion

Defined in the profile but triggered during **garbage collection**. When effective confidence drops near the floor AND the memory hasn't been accessed within its half-life, it moves to a lower layer instead of being archived:

```yaml
- name: "long-term"
  demotion_to: "short-term"
```

Demotion criteria (all must be met):
- Layer defines `demotion_to` (not null)
- Effective confidence ≤ floor × 1.5
- No access within the layer's half-life period

> **Note**: Demotion is currently implemented in `PromotionEngine.check_demotion()` but is not automatically triggered at runtime. It is available for explicit use via the API. Promotion is fully automatic on reinforcement.

---

## Importance Tags

Tags on memory entries can multiply the effective half-life for decay calculations:

```yaml
- name: "household-profile"
  importance_tags:
    safety: 3.0      # 3x half-life for safety-tagged memories
    allergy: 3.0      # medical/allergy info persists much longer
    preference: 1.5   # preferences decay slower than defaults
    recurring: 1.3    # recurring patterns get a modest boost
```

**How it works**: When a memory has tags matching the layer's `importance_tags`, the highest multiplier is applied to the effective half-life. A "safety"-tagged memory in a 365-day layer effectively has a 1095-day half-life.

### When to use importance tags

- **Safety/compliance**: High multipliers (2.0-3.0) for information that must not be forgotten
- **Domain priorities**: Medium multipliers (1.3-1.5) for domain-relevant content
- **Frequency signals**: Use `recurring: 1.3` for patterns that prove their value over time

### Tags vs. layers

Use **layers** when information categories have fundamentally different lifetimes and scoring needs. Use **tags** when information within the same layer has variable importance.

---

## Source Confidence & Ceilings

Control how much you trust different information sources:

```yaml
source_confidence:       # Initial confidence by source type
  human: 0.95            # Human-provided information
  agent: 0.60            # Agent-generated (LLM output)
  inferred: 0.40         # Extracted/inferred from context
  system: 0.90           # System-generated (build tools, CI)

source_ceilings:         # Maximum confidence by source type
  human: 0.95            # Human info can reach 0.95
  agent: 0.85            # Agent info capped at 0.85
  inferred: 0.70         # Inferred info capped at 0.70
  system: 0.95           # System info can reach 0.95
```

### Tuning for agent-heavy systems

If your system is primarily agent-driven (e.g., a delivery pipeline where most memories come from automated agents), consider raising agent confidence:

```yaml
source_confidence:
  agent: 0.70            # Higher initial trust for structured agent output
source_ceilings:
  agent: 0.90            # Allow agent memories to reach near-human levels
```

---

## Garbage Collection

Controls when memories are archived (moved to `archive.jsonl`, never deleted):

```yaml
gc:
  floor_retention_days: 30    # Days at confidence floor before archival
  session_expiry_days: 7      # Days after session end for session-scoped memories
  contradicted_threshold: 0.2 # Confidence below which contradicted memories are archived
  stale_threshold: 0.3        # Confidence below which memories are flagged as stale
```

### Tuning GC for different workloads

| Workload | floor_retention | session_expiry | Rationale |
|----------|----------------|----------------|-----------|
| Low volume (personal assistant) | 30 days | 7 days | Keep memories around longer |
| Medium volume (code repo) | 30 days | 7 days | Default — balanced |
| High volume (delivery pipeline) | 14 days | 3 days | Aggressive cleanup to stay under limits |
| IoT / sensor data | 7 days | 1 day | Very fast cleanup; most data is transient |

---

## Recall Configuration

Controls how the recall orchestrator builds context for prompt injection:

```yaml
recall:
  default_token_budget: 3000   # Max tokens in recalled memory block
  default_engagement: "high"   # low/medium/high — controls how aggressively memories are injected
  min_score: 0.3               # Minimum composite score to include in recall
  min_confidence: 0.1          # Minimum effective confidence to include
```

Increase `default_token_budget` for domains with long-form content (research: 4000, personal assistants: 4000, code repos: 3000). See [Profile Limits Rationale](profile-limits-rationale.md) for benchmarks.

---

## Limits

```yaml
limits:
  max_entries: 5000        # Hard cap per store (lowest-confidence evicted)
  max_key_length: 128      # Max characters for memory keys
  max_value_length: 4096   # Max characters for memory values
  max_tags: 10             # Max tags per entry
```

### Sizing guidance

| Use case | max_entries | max_value_length | Rationale |
|----------|-------------|------------------|-----------|
| Code repository | 5,000 | 4096 | Default — well tested on Pi 5 through server |
| Personal assistant | 5,000 | 4096 | Identity + preferences accumulate over years |
| Research / knowledge base | 10,000 | 8192 | Knowledge accumulation is the purpose |
| IoT / home automation | 5,000 | 4096 | Sensor events + learned patterns across devices |
| Customer support | 5,000 | 4096 | Product knowledge + interaction history |
| Delivery pipeline | 10,000 | 8192 | High volume, intent specs can be verbose |

GC and auto-consolidation keep the active set lean — the limit is a safety net, not a target. For details on hardware performance at various entry counts, see [Profile Limits Rationale](profile-limits-rationale.md).

---

## Hive Configuration

The Hive enables cross-agent memory sharing. See the [Hive Guide](hive.md) for full details.

```yaml
hive:
  auto_propagate_tiers:      # Tiers that auto-propagate to the Hive
    - "platform-invariants"
    - "expert-knowledge"
  private_tiers:             # Tiers that NEVER propagate
    - "signals"
    - "session-context"
  conflict_policy: "confidence_max"  # How to resolve conflicting writes
  recall_weight: 0.8         # Weight multiplier for Hive results in recall
```

### Conflict policies

| Policy | Behavior | Best for |
|--------|----------|----------|
| `supersede` | Creates a new versioned key, invalidates old | Audit trails, compliance |
| `source_authority` | Rejects writes from non-authoritative agents | Domain-owned namespaces |
| `confidence_max` | Keeps the higher-confidence version | Multi-agent convergence |
| `last_write_wins` | Overwrites unconditionally | Simple systems, low contention |

---

## Inheritance

Profiles can extend other profiles using the `extends` field:

```yaml
profile:
  name: "my-variant"
  extends: "repo-brain"      # Inherit from built-in repo-brain

  layers:
    - name: "architectural"
      half_life_days: 365     # Override just this layer

  scoring:
    recency: 0.25             # Override scoring weights
    confidence: 0.20
    relevance: 0.40
    frequency: 0.15
```

### Inheritance rules

- **Layers**: Child layers with matching names replace parent layers. New child layers are appended.
- **Scalars** (scoring, gc, recall, limits): Child values override parent completely.
- **Dicts** (source_confidence, source_ceilings): Merged — child keys override matching parent keys, parent-only keys are preserved.
- **Depth limit**: Maximum 3 levels of inheritance.

### When to use inheritance

- Tweaking a built-in profile for a specific project (e.g., `extends: "repo-brain"` with a longer architectural half-life)
- Creating a family of related profiles (e.g., `thestudio-qa` extends `thestudio`)
- Overriding one section without duplicating the entire profile

---

## Architecture Patterns

### Pattern 1: Single profile + Hive namespaces (recommended for multi-agent)

All agents share one profile defining the universal layer schema. The Hive provides agent isolation via namespaces. Best when agents need to share a common vocabulary.

```
Agent A (namespace: "developer")  ─┐
Agent B (namespace: "qa")         ─┼── Hive (shared brain)
Agent C (namespace: "router")     ─┘    └── universal namespace (shared facts)
                                        └── developer namespace (code patterns)
                                        └── qa namespace (defect patterns)
All use profile: "thestudio"
```

### Pattern 2: Per-role profiles

Each agent type gets its own profile with tuned layers and scoring. Best when agents have fundamentally different memory needs (different decay rates, different scoring priorities).

```
Developer agent → profile: "thestudio-dev"   (recency-heavy scoring)
QA agent        → profile: "thestudio-qa"    (confidence-heavy scoring)
Router agent    → profile: "thestudio-router" (frequency-heavy scoring)
```

### Pattern 3: Shared base + role extensions (hybrid)

One base profile with per-role variants using `extends`. Best of both worlds — shared vocabulary with role-specific tuning.

```yaml
# thestudio-qa.yaml
profile:
  name: "thestudio-qa"
  extends: "thestudio"
  layers:
    - name: "defect-patterns"        # New layer, appended
      half_life_days: 90
      decay_model: "power_law"
      decay_exponent: 0.7
  scoring:
    confidence: 0.40                 # QA trusts proven patterns more
    relevance: 0.25
    recency: 0.20
    frequency: 0.15
```

### When to choose which pattern

| Condition | Recommended pattern |
|-----------|-------------------|
| Agents share the same pipeline/domain | Pattern 1 (single profile + namespaces) |
| Agents have different scoring needs | Pattern 3 (shared base + extensions) |
| Agents are truly independent systems | Pattern 2 (per-role profiles) |
| Starting a new integration | Pattern 1, split later if needed |

---

## Anti-Patterns

### Too many layers

**Problem**: 8+ layers with overlapping half-lives and unclear boundaries.
**Fix**: If two layers have similar half-lives (within 2x) and serve the same purpose, merge them and use tags to distinguish subtypes.

### Mismatched promotion chains

**Problem**: Promotion from a 7-day layer requires 30 days of age — memories decay to the floor before they can promote.
**Fix**: Ensure `min_age_days` in the promotion threshold is less than the layer's half-life.

### Over-relying on power-law decay

**Problem**: Using power-law for all layers because "we don't want to forget anything."
**Fix**: Power-law should be reserved for 1-2 top layers. Most layers should use exponential decay — if everything persists, the store fills up and GC can't clean effectively.

### Scoring weights that don't match the use case

**Problem**: Using default repo-brain weights (40% relevance) for a real-time agent where recency matters most.
**Fix**: Audit your scoring weights against the weight selection guide above. A personal assistant with 40% relevance and 15% recency will surface old memories over recent ones.

### Ignoring the Hive config

**Problem**: Multi-agent system where all tiers propagate, flooding the shared brain with ephemeral signals.
**Fix**: Always set `private_tiers` for your most volatile layers. Only `auto_propagate_tiers` for stable, high-value knowledge.

### Giant max_entries without matching GC

**Problem**: Setting `max_entries: 25000` but keeping 30-day floor retention. Store fills up, eviction kicks in based on confidence, and you lose memories unpredictably.
**Fix**: If you increase max_entries well above the default (5,000), decrease floor_retention_days proportionally, or ensure your layer design produces enough natural decay. At 25,000+ entries, enable vector search for sub-linear retrieval.

---

## Reference: Full Schema

```yaml
profile:
  name: "string"                    # Required. Profile identifier.
  version: "1.0"                    # Semver string.
  extends: "parent-profile-name"    # Optional. Built-in profile to inherit from.
  description: "string"             # Optional. Human-readable description.

  layers:                           # Required. At least 1 layer.
    - name: "string"                # Required. Unique within profile.
      description: "string"         # Optional.
      half_life_days: 90            # Required. >= 1.
      decay_model: "exponential"    # "exponential" (default) or "power_law".
      decay_exponent: 1.0           # 0.1–5.0. Only meaningful for power_law.
      confidence_floor: 0.10        # 0.0–1.0. Minimum decayed confidence.
      importance_tags:              # Optional. Tag → half-life multiplier.
        critical: 2.0
      promotion_to: "layer-name"    # Optional. Target layer for promotion.
      promotion_threshold:          # Required if promotion_to is set.
        min_access_count: 5         # >= 1
        min_age_days: 7             # >= 1
        min_confidence: 0.5         # 0.0–1.0
      demotion_to: "layer-name"     # Optional. Target layer for demotion.

  scoring:                          # Optional (defaults shown).
    relevance: 0.40                 # 0.0–1.0
    confidence: 0.30                # 0.0–1.0
    recency: 0.15                   # 0.0–1.0
    frequency: 0.15                 # 0.0–1.0
    bm25_norm_k: 5.0                # >= 0.1
    frequency_cap: 20               # >= 1
    # Weights must sum to ~1.0 (0.95–1.05 tolerance).

  source_confidence:                # Optional (defaults shown).
    human: 0.95
    agent: 0.60
    inferred: 0.40
    system: 0.90

  source_ceilings:                  # Optional (defaults shown).
    human: 0.95
    agent: 0.85
    inferred: 0.70
    system: 0.95

  gc:                               # Optional (defaults shown).
    floor_retention_days: 30        # >= 1
    session_expiry_days: 7          # >= 1
    contradicted_threshold: 0.2     # 0.0–1.0
    stale_threshold: 0.3            # 0.0–1.0

  recall:                           # Optional (defaults shown).
    default_token_budget: 3000      # >= 100
    default_engagement: "high"      # "low", "medium", "high"
    min_score: 0.3                  # 0.0–1.0
    min_confidence: 0.1             # 0.0–1.0

  limits:                           # Optional (defaults shown).
    max_entries: 5000               # >= 1
    max_key_length: 128             # >= 1
    max_value_length: 4096          # >= 1
    max_tags: 10                    # >= 1

  hive:                             # Optional (defaults: Hive disabled).
    auto_propagate_tiers: []        # Layer names that auto-propagate.
    private_tiers: []               # Layer names that never propagate.
    conflict_policy: "supersede"    # "supersede"|"source_authority"|"confidence_max"|"last_write_wins"
    recall_weight: 0.8              # 0.0–1.0. Weight for Hive results in recall.
```

---

## Further Reading

- [Hive Guide](hive.md) — Cross-agent memory sharing with namespaces
- [Profile Catalog](profile-catalog.md) — Built-in profiles explained
- [Federation Guide](federation.md) — Cross-project memory sharing
- [Auto-Recall Guide](auto-recall.md) — How profiles affect recall behavior
