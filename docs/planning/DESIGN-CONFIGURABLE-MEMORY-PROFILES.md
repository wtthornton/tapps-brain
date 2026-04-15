# Configurable Memory Profiles — Design Document

> **ARCHIVED — pre-ADR-007 (SQLite era).** This document was written before 2026-04-11 when
> tapps-brain still used SQLite/FTS5/WAL. ADR-007 (2026-04-11) removed SQLite entirely; all
> storage is now PostgreSQL (pgvector + tsvector). References to `hive.db`, `federated.db`,
> SQLite WAL, FTS5, and `sqlite-vec` in this document reflect the design thinking at the time
> and are **not accurate descriptions of the current system**. The profile scoring logic and
> tier configuration described here was implemented and is still accurate; only the storage
> layer references are stale.

> **Goal:** Make tapps-brain's memory layer system (tiers, half-lives, scoring weights, promotion rules) fully configurable via profile files, ship 5–6 out-of-the-box profiles, and position tapps-brain as a universal brain for any AI agent — not just code repos.

---

## Table of Contents

1. [Research: 2026 Memory Landscape](#1-research-2026-memory-landscape)
2. [Cognitive Science Foundations](#2-cognitive-science-foundations)
3. [Decay Mathematics](#3-decay-mathematics)
4. [Architecture: Configurable Layer System](#4-architecture-configurable-layer-system)
5. [Profile Schema](#5-profile-schema)
6. [Layer Interaction Patterns](#6-layer-interaction-patterns)
7. [Out-of-the-Box Profiles](#7-out-of-the-box-profiles)
8. [Hive Architecture — Multi-Agent Shared Brain](#8-hive-architecture-multi-agent-shared-brain)
9. [Implementation Plan](#9-implementation-plan)
10. [Migration & Backward Compatibility](#10-migration-backward-compatibility)
11. [Sources](#11-sources)

---

## 1. Research: 2026 Memory Landscape

### 1.1 How the major frameworks model memory

| Framework | Memory Types / Layers | Storage | Scoring | Key Innovation |
|-----------|----------------------|---------|---------|----------------|
| **Mem0** | Episodic, Semantic, Procedural, Associative | Postgres + vector DB + graph (Neo4j/Neptune) | relevance × recency × type_weight (0.6/0.3/0.1) | Hybrid vector+graph; 26% accuracy gain over plain vector |
| **Letta (MemGPT)** | Core (RAM), Recall (searchable history), Archival (long-term) | Tiered: context window → DB → archive | OS-style page faults; agent controls own memory via function calls | Agent-managed memory with virtual paging |
| **Zep (Graphiti)** | Episode → Semantic Entity → Community (3-tier subgraph) | Neo4j temporal knowledge graph | Hybrid: vector + BM25 + graph traversal | Bi-temporal modeling (ingestion time + fact validity) |
| **Cognee** | Graph + Vector + Relational (3 storage layers) | Kuzu/Neo4j + LanceDB + SQLite | 14 retrieval modes; Memify post-processing strengthens frequent connections | Graph-vector hybrid with 6-stage pipeline |
| **LangMem** | Semantic, Episodic, Procedural | Pluggable via LangGraph namespaces | Namespace-scoped; user_id segmentation | Procedural = updated prompt instructions |
| **AWS AgentCore** | Semantic, User Preference, Summary (3 strategies) | Managed cloud service | Parallel strategy extraction; ~200ms retrieval | Streaming notifications via Kinesis |
| **OpenClaw** | Markdown files (MEMORY.md + daily notes) | SQLite via sqlite-vec + FTS5 | 70% vector + 30% BM25 | Pre-compaction memory flush; ContextEngine plugin slot |
| **tapps-brain (current)** | Architectural, Pattern, Procedural, Context | SQLite WAL + FTS5 + in-memory cache | 40% relevance + 30% confidence + 15% recency + 15% frequency | Exponential decay with tier half-lives; deterministic (no LLM) |

### 1.2 Emerging consensus (2026)

The 2026 ecosystem converges on several principles:

1. **Three cognitive memory types are standard** — episodic (what happened), semantic (what I know), procedural (how to do it). Nearly every framework maps to these.

2. **Hybrid retrieval is mandatory** — Pure vector search is insufficient. The top systems combine vector + keyword (BM25) + graph traversal. Mem0 reports 26% accuracy gains; Zep achieves 18.5% improvement over baselines.

3. **Temporal awareness matters** — Zep's bi-temporal model and tapps-brain's bi-temporal versioning are converging on the same insight: facts have validity windows, not just creation timestamps.

4. **Memory should be agent-managed** — Letta's key insight: the agent should control its own memory via tools, not rely on external heuristics. This aligns with tapps-brain's MCP tool approach.

5. **Configurable layers are the frontier** — AWS AgentCore ships pluggable "strategies," OpenClaw's ContextEngine is a replaceable slot, Microsoft Agent Framework offers YAML-defined agents with pluggable memory. No one has shipped fully user-configurable layer definitions yet.

6. **Graph + vector + relational is the storage trifecta** — Cognee, Zep, and Mem0 all converge on this. tapps-brain has relational (SQLite) and keyword (FTS5/BM25), with optional vector (FAISS). Graph is the gap.

### 1.3 Where tapps-brain is ahead

- **Zero LLM dependency** — Every other framework (Mem0, Zep, Cognee, LangMem) requires LLM calls for extraction, consolidation, or classification. tapps-brain is fully deterministic.
- **Bi-temporal versioning** — Only Zep matches this. Mem0, Letta, Cognee don't track fact validity windows.
- **Composite scoring with 4 signals** — Mem0 uses 3 (relevance, recency, type_weight). Most others use 2 (vector similarity + recency). tapps-brain's 4-signal system (relevance, confidence, recency, frequency) is richer.
- **Consolidation without LLM** — Jaccard + TF-IDF merging is unique.

### 1.4 Where tapps-brain needs to grow

- **Configurable layers** — Hardcoded `MemoryTier` enum limits use cases beyond code repos.
- **Promotion/demotion between layers** — No mechanism for information to flow between tiers based on usage patterns.
- **Emotional valence / importance scoring** — No concept of memory "weight" beyond confidence.
- **Pre-compaction hooks** — OpenClaw's auto-flush before context loss has no equivalent.
- **Graph-based retrieval** — Relations module exists but isn't a first-class retrieval path.

---

## 2. Cognitive Science Foundations

The design of configurable memory profiles should be grounded in established cognitive science models, adapted for AI agent use cases.

### 2.1 Atkinson-Shiffrin Multi-Store Model (1968)

The foundational model of human memory describes three stores:

```
Sensory Register  →  Short-Term Store (STM)  →  Long-Term Store (LTM)
   (~250ms)            (~20-30 seconds)           (unlimited duration)
      ↓                     ↓                          ↓
  Attention            Rehearsal                  Retrieval
  (filter)          (maintenance)               (reconstruction)
```

**Key mechanisms:**
- **Attention** gates what enters STM from sensory input
- **Rehearsal** (repetition) promotes STM → LTM
- **Decay** removes unrehearsed STM items within ~20 seconds
- **Retrieval failure** (not erasure) explains most LTM "forgetting"

**Mapping to AI agents:**
| Cognitive Store | AI Agent Analog | tapps-brain Equivalent |
|----------------|-----------------|----------------------|
| Sensory Register | Current context window / message buffer | Not modeled (host agent's job) |
| Short-Term Memory | Active session context, working set | `context` tier (14-day half-life) |
| Long-Term Memory | Persistent store across sessions | `architectural`/`pattern`/`procedural` tiers |

### 2.2 Baddeley's Working Memory Model (2000)

Extends Atkinson-Shiffrin with a **central executive** that coordinates:
- **Phonological loop** — verbal/acoustic information
- **Visuospatial sketchpad** — spatial/visual information
- **Episodic buffer** — integrates information from multiple sources

**Mapping to AI agents:** The episodic buffer maps to the recall orchestrator's job — pulling memories from different tiers/sources and assembling them into a coherent context for the current task.

### 2.3 Levels of Processing (Craik & Lockhart, 1972)

Deeper processing leads to stronger memory traces:
- **Structural** (shallow) — surface features → fast decay
- **Phonemic** (intermediate) — sound/pattern → moderate decay
- **Semantic** (deep) — meaning/relationships → slow decay

**Mapping to profiles:** Memories formed from deep analysis (architectural decisions, patterns) should have longer half-lives than surface observations (context notes). This validates the existing tier→half-life mapping and extends it: memories with more connections/relationships should decay more slowly.

### 2.4 Emotional Enhancement of Memory

Research shows emotional valence directly affects memory retention:

- **Arousal-mediated consolidation** — The amygdala modulates hippocampal memory consolidation. Emotionally arousing events are remembered better and for longer (PMC5438110).
- **Valence asymmetry** — Negative emotional memories are consolidated more strongly than positive ones ("NEVER forget" effect — PMC6613951). Negative events are processed slowly initially, then increasingly faster.
- **Importance × emotion interaction** — High-importance + high-emotion = strongest retention.

**Mapping to AI agents:** Memories tagged as "critical," "blocking," "failure," or "incident" should have boosted retention. Memories about things that went wrong (bugs, outages, bad decisions) should decay more slowly than routine observations. This can be modeled as an **importance multiplier** on half-life.

### 2.5 Consolidation and Promotion

In biological memory, consolidation is the process by which labile STM traces become stable LTM traces:

- **Synaptic consolidation** (minutes to hours) — molecular changes at synapses
- **Systems consolidation** (weeks to years) — hippocampus → neocortex transfer
- **Reconsolidation** — retrieved memories become labile again and must re-stabilize

**Mapping to AI agents:** This suggests a promotion mechanism:
1. New memories start in a "working" layer (fast decay)
2. If accessed/reinforced N times within a window, they promote to a more durable layer
3. When retrieved and updated (supersede), they undergo "reconsolidation" — the new version starts fresh in the working layer

---

## 3. Decay Mathematics

### 3.1 Current model: Exponential decay

tapps-brain currently uses the Ebbinghaus exponential decay formula:

```
R(t) = C₀ × 0.5^(t / H)
```

Where:
- `R(t)` = retrievability (effective confidence) at time `t`
- `C₀` = initial confidence at time of last reinforcement
- `t` = days since last reinforcement
- `H` = half-life in days (tier-specific)

This is clamped to `[confidence_floor, source_ceiling]`.

**Current constants:**

| Parameter | Value |
|-----------|-------|
| Architectural half-life | 180 days |
| Pattern half-life | 60 days |
| Procedural half-life | 30 days |
| Context half-life | 14 days |
| Confidence floor | 0.1 |
| Human source ceiling | 0.95 |
| Agent source ceiling | 0.85 |
| Inferred source ceiling | 0.70 |
| System source ceiling | 0.95 |

### 3.2 Alternative: Power-law decay (Wickelgren)

Wickelgren (1974) proposed that forgetting follows a power law rather than exponential:

```
R(t) = C₀ × (1 + t/τ)^(-β)
```

Where:
- `τ` = time scaling factor
- `β` = decay exponent

**Key difference:** Power-law decay is initially faster than exponential but has a longer tail — old memories fade more slowly than exponential predicts. This matches empirical data better for long-term retention (Wixted & Carpenter, 2007).

**Practical implication:** At the aggregate level, individual exponential decays produce power-law-like behavior (Kahana, 2002). Since tapps-brain operates at the aggregate level (many memories), the exponential model is a reasonable approximation, but a power-law option would benefit long-lived memories (architectural decisions that should "almost never" vanish).

### 3.3 FSRS model: Difficulty × Stability × Retrievability

The Free Spaced Repetition Scheduler (FSRS) — now the default in Anki — uses a three-variable model:

```
R(t) = (1 + t / (9 × S))^(-1)     [power-law form]

S' = S × e^(w × (R − 0.9))          [stability update after review]

D' = D − w₁ × (grade − 3)          [difficulty update]
```

Where:
- `R` = retrievability (probability of recall)
- `S` = stability (days for R to drop from 1.0 to 0.9)
- `D` = difficulty (0-10, how hard to increase stability)
- `w` = learned weight parameters (21 total in FSRS-6)

**Key insights for tapps-brain:**
1. **Stability increases with successful recall** — Each reinforcement doesn't just reset the clock; it makes the memory more durable. The interval between reinforcements should grow exponentially.
2. **Difficulty models inherent memorability** — Some facts are harder to retain than others. This maps to memory tier, but could also be per-entry.
3. **21 parameters, all learnable** — FSRS learns optimal parameters from review history. tapps-brain could similarly tune decay parameters from access patterns.

### 3.4 Proposed enhanced decay model

Combine the best elements:

```
R(t) = C₀ × (1 + t / (k × S))^(-β)
```

Where:
- `S` = stability (replaces fixed half-life; grows with reinforcement)
- `k` = scaling constant (default 9, from FSRS)
- `β` = decay exponent (default 1.0; configurable per profile)
- `C₀` = initial confidence

**Stability update on reinforcement:**
```
S' = S × (1 + α × (access_count / frequency_cap))
```

Where `α` is a growth factor (default 0.5). More accesses = more stable.

**Importance multiplier:**
```
S_effective = S × importance_multiplier
```

Where `importance_multiplier` is set by tags (e.g., "critical" → 2.0, "blocking" → 1.5, "routine" → 1.0).

**Backward compatibility:** When `β = 1.0` and `k × S ≈ H / ln(2)`, this reduces to approximately the current exponential model. The default profile can use these values.

### 3.5 Composite scoring — configurable weights

Current fixed weights:

```python
_W_RELEVANCE  = 0.40
_W_CONFIDENCE = 0.30
_W_RECENCY    = 0.15
_W_FREQUENCY  = 0.15
```

The profile should allow overriding these. Different use cases need different balances:

| Use Case | Relevance | Confidence | Recency | Frequency |
|----------|-----------|------------|---------|-----------|
| Code repo (current) | 0.40 | 0.30 | 0.15 | 0.15 |
| Personal assistant | 0.30 | 0.20 | 0.30 | 0.20 |
| Customer support | 0.35 | 0.25 | 0.15 | 0.25 |
| Research agent | 0.50 | 0.25 | 0.10 | 0.15 |

---

## 4. Architecture: Configurable Layer System

### 4.1 Core concept: Profiles replace hardcoded enums

Today, `MemoryTier` is a `StrEnum` with 4 fixed values. The new design:

```
Profile file (YAML)
    ↓
MemoryProfile (Pydantic model loaded at store init)
    ↓
    ├── defines N layers (name, half_life, decay_model, ...)
    ├── defines scoring weights
    ├── defines promotion/demotion rules
    ├── defines importance multipliers
    └── defines GC thresholds
```

### 4.2 What stays the same

- **`MemoryEntry` model** — The `tier` field becomes a `str` (already is, since `StrEnum` serializes to string). Validation moves from enum membership to profile layer lookup.
- **SQLite schema** — `tier` column is already `TEXT`. No migration needed.
- **Decay engine** — Same math, but half-life comes from profile instead of hardcoded constants.
- **Retrieval** — Same composite scoring, but weights come from profile.
- **BM25, FTS5, safety, federation** — Unchanged.
- **MCP server API** — `tier` parameter remains a string. Valid values come from the active profile.

### 4.3 What changes

| Component | Current | New |
|-----------|---------|-----|
| `MemoryTier` enum | 4 hardcoded values | Kept as default; profile layers override |
| `DecayConfig` | 4 hardcoded half-lives | Reads from profile layer definitions |
| `retrieval.py` weights | Module-level constants | Reads from profile scoring config |
| `gc.py` thresholds | Module-level constants | Reads from profile GC config |
| `store.py` init | No profile loading | Loads profile from `{project_dir}/.tapps-brain/profile.yaml` or built-in |
| Validation | `MemoryTier` enum check | Profile layer name check |
| **New: promotion engine** | Does not exist | Evaluates promotion/demotion rules on reinforcement |

### 4.4 Profile resolution order

```
1. {project_dir}/.tapps-brain/profile.yaml       (project-specific override)
2. ~/.tapps-brain/profile.yaml                     (user-global default)
3. Built-in profile (selected by name, e.g. "repo-brain")
4. Hardcoded default (backward-compatible with current behavior)
```

### 4.5 Profile inheritance

Profiles can extend a base profile via the `extends` field. This follows the pattern used by Kubernetes (StorageClasses), Terraform (provider configs), and game engines (ScriptableObject presets):

```yaml
profile:
  name: "my-custom-profile"
  extends: "repo-brain"               # Inherit all settings from repo-brain
  description: "Repo brain with longer context retention"

  layers:
    - name: "context"                  # Override only the context layer
      half_life_days: 30               # Doubled from default 14
```

**Resolution rules:**
- The child profile's `layers` list **replaces** layers with matching names, **appends** new layers
- Scalar fields (`scoring`, `gc`, `limits`) are **merged** — child overrides parent
- `extends` can chain: A extends B extends C (max depth 3 to prevent cycles)

### 4.6 Third-party layer plugins (future)

Python's `importlib.metadata.entry_points()` enables third-party packages to register custom layers:

```toml
# In a third-party package's pyproject.toml
[project.entry-points."tapps_brain.layers"]
redis_cache = "tapps_brain_redis:RedisMemoryLayer"
pinecone_vector = "tapps_brain_pinecone:PineconeLayer"
```

tapps-brain discovers these at startup and makes them available in profiles:

```python
from importlib.metadata import entry_points

def discover_layers() -> dict[str, type]:
    return {ep.name: ep.load() for ep in entry_points(group="tapps_brain.layers")}
```

This follows the pattern used by pytest (plugins), Flask (extensions), and Babel (locale data). **Not needed for MVP** — reserve the entry point group name and document the Protocol interface. Build the registry when there's demand.

---

## 5. Profile Schema

### 5.1 Full YAML schema

```yaml
# .tapps-brain/profile.yaml
profile:
  name: "repo-brain"                    # Profile identifier
  version: "1.0"                        # Schema version
  extends: null                         # Optional: inherit from another profile name
  description: "Memory profile for code repositories"

  # ----- Layer Definitions -----
  layers:
    - name: "architectural"
      description: "System decisions, tech stack, infrastructure"
      half_life_days: 180
      decay_model: "exponential"        # "exponential" | "power_law" | "fsrs"
      decay_exponent: 1.0               # β for power-law; ignored for exponential
      confidence_floor: 0.10
      importance_tags:                   # Tags that boost half-life
        critical: 2.0                   # tag_name: multiplier
        blocking: 1.5
      promotion_to: null                # No promotion (top tier)
      demotion_to: "pattern"            # Demotes here if decayed below threshold

    - name: "pattern"
      description: "Coding conventions, API patterns"
      half_life_days: 60
      decay_model: "exponential"
      decay_exponent: 1.0
      confidence_floor: 0.10
      importance_tags:
        critical: 2.0
      promotion_to: "architectural"
      promotion_threshold:
        min_access_count: 10            # Accessed 10+ times
        min_age_days: 30                # At least 30 days old
        min_confidence: 0.7             # Still high confidence
      demotion_to: "context"

    - name: "procedural"
      description: "Workflows, deployment steps, processes"
      half_life_days: 30
      decay_model: "exponential"
      decay_exponent: 1.0
      confidence_floor: 0.10
      promotion_to: "pattern"
      promotion_threshold:
        min_access_count: 8
        min_age_days: 14
        min_confidence: 0.6
      demotion_to: "context"

    - name: "context"
      description: "Session-specific facts, current task details"
      half_life_days: 14
      decay_model: "exponential"
      decay_exponent: 1.0
      confidence_floor: 0.05
      promotion_to: "procedural"
      promotion_threshold:
        min_access_count: 5
        min_age_days: 7
        min_confidence: 0.5
      demotion_to: null                 # Archived by GC instead

  # ----- Scoring Weights -----
  scoring:
    relevance: 0.40
    confidence: 0.30
    recency: 0.15
    frequency: 0.15
    bm25_norm_k: 5.0                    # BM25 sigmoid normalization constant
    frequency_cap: 20                   # Max access_count for frequency scoring

  # ----- Source Confidence -----
  source_confidence:
    human: 0.95
    agent: 0.60
    inferred: 0.40
    system: 0.90

  # ----- Source Ceilings -----
  source_ceilings:
    human: 0.95
    agent: 0.85
    inferred: 0.70
    system: 0.95

  # ----- Garbage Collection -----
  gc:
    floor_retention_days: 30            # Days at floor before archival
    session_expiry_days: 7              # Days before session memories expire
    contradicted_threshold: 0.2         # Confidence threshold for contradicted archival
    stale_threshold: 0.3                # Below this = "stale"

  # ----- Recall / Injection -----
  recall:
    default_token_budget: 2000
    default_engagement: "high"          # "low" | "medium" | "high"
    min_score: 0.3
    min_confidence: 0.1

  # ----- Limits -----
  limits:
    max_entries: 500
    max_key_length: 128
    max_value_length: 4096
    max_tags: 10
```

### 5.2 Layer definition model (Pydantic)

```python
class LayerDefinition(BaseModel):
    name: str                                    # e.g. "architectural"
    description: str = ""
    half_life_days: int = Field(ge=1)
    decay_model: Literal["exponential", "power_law", "fsrs"] = "exponential"
    decay_exponent: float = Field(default=1.0, ge=0.1, le=5.0)
    confidence_floor: float = Field(default=0.1, ge=0.0, le=1.0)
    importance_tags: dict[str, float] = Field(default_factory=dict)
    promotion_to: str | None = None
    promotion_threshold: PromotionThreshold | None = None
    demotion_to: str | None = None

class PromotionThreshold(BaseModel):
    min_access_count: int = Field(default=5, ge=1)
    min_age_days: int = Field(default=7, ge=1)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)

class ScoringConfig(BaseModel):
    relevance: float = Field(default=0.40, ge=0.0, le=1.0)
    confidence: float = Field(default=0.30, ge=0.0, le=1.0)
    recency: float = Field(default=0.15, ge=0.0, le=1.0)
    frequency: float = Field(default=0.15, ge=0.0, le=1.0)
    bm25_norm_k: float = Field(default=5.0, ge=0.1)
    frequency_cap: int = Field(default=20, ge=1)

class MemoryProfile(BaseModel):
    name: str
    version: str = "1.0"
    extends: str | None = None          # Inherit from another profile
    description: str = ""
    layers: list[LayerDefinition]
    scoring: ScoringConfig = ScoringConfig()
    source_confidence: dict[str, float] = {...}
    source_ceilings: dict[str, float] = {...}
    gc: GCConfig = GCConfig()
    recall: RecallProfileConfig = RecallProfileConfig()
    limits: LimitsConfig = LimitsConfig()
```

---

## 6. Layer Interaction Patterns

### 6.1 Promotion (bottom-up)

When a memory is reinforced, the promotion engine checks if it qualifies to move to a higher tier:

```
Context  →(promote)→  Procedural  →(promote)→  Pattern  →(promote)→  Architectural
  14d                    30d                      60d                    180d
```

**Trigger:** `store.reinforce(key)` — after updating access_count and last_reinforced:

```python
def _check_promotion(entry, profile):
    layer = profile.get_layer(entry.tier)
    if layer.promotion_to is None:
        return  # Already at top tier

    threshold = layer.promotion_threshold
    if threshold is None:
        return  # No promotion rules defined

    if (entry.access_count >= threshold.min_access_count
        and age_days(entry) >= threshold.min_age_days
        and effective_confidence(entry) >= threshold.min_confidence):

        entry.tier = layer.promotion_to
        # Reset decay reference but keep accumulated confidence
        entry.last_reinforced = now()
```

**Design choice:** Promotion is checked on reinforcement, not on read. This keeps reads fast and makes promotion an explicit signal of value.

### 6.1.1 Desirable difficulty bonus (Jost's Law)

Cognitive science research (Roediger & Karpicke 2006, FSRS algorithm) shows that retrieving a nearly-forgotten memory strengthens it more than retrieving a fresh one. Currently, tapps-brain gives the same confidence boost regardless of how decayed the memory was at reinforcement time.

**Enhancement:** Scale the reinforcement boost by how far the memory had decayed:

```python
def reinforce_with_difficulty_bonus(entry, boost, config):
    decayed_R = calculate_decayed_confidence(entry, config)
    difficulty_bonus = 1.0 - decayed_R   # Higher bonus when more forgotten
    effective_boost = boost * (1.0 + difficulty_bonus)
    new_confidence = min(entry.confidence + effective_boost, ceiling)
```

**Jost's Law extension:** Reinforcement should also grow the effective stability (half-life), not just reset the decay clock. A memory reinforced 10 times is fundamentally more durable than one reinforced once:

```python
def effective_half_life(entry, layer):
    base = layer.half_life_days
    # Logarithmic growth — diminishing returns on repeated reinforcement
    stability_growth = 1.0 + math.log1p(entry.reinforce_count) * 0.3
    return base * stability_growth
```

A memory with `reinforce_count=10` gets `base × 1.72` effective half-life. This captures Jost's First Law: "Of two equally strong memory traces, the older (more reinforced) decays more slowly."

### 6.2 Demotion (top-down)

When a memory's effective confidence drops below a threshold AND it hasn't been accessed in a while, it can be demoted to a lower tier:

```
Architectural  →(demote)→  Pattern  →(demote)→  Context  →(archive)→  JSONL
    180d                     60d                    14d                  gone
```

**Trigger:** Checked during GC runs (not on every read):

```python
def _check_demotion(entry, profile):
    layer = profile.get_layer(entry.tier)
    if layer.demotion_to is None:
        return  # Already at bottom tier; GC handles archival

    effective_conf = calculate_decayed_confidence(entry, ...)
    if effective_conf <= layer.confidence_floor * 1.5:  # Near floor
        if age_days_since_last_access(entry) > layer.half_life_days:
            entry.tier = layer.demotion_to
            # Keep current confidence; new tier's half-life takes over
```

### 6.3 Consolidation across layers

The existing consolidation engine (Jaccard + TF-IDF similarity) operates within a tier. With configurable layers, consolidation should:

1. **Prefer same-layer merges** — Two `pattern` memories consolidate into one `pattern` memory
2. **Cross-layer merge promotes** — If a `context` memory and a `pattern` memory are similar, the consolidated result goes to `pattern` (the higher tier)
3. **Merged confidence** — `max(conf_a, conf_b) + 0.05` (existing logic, kept)

### 6.4 Importance multiplier

Tags can boost a memory's effective half-life:

```python
def effective_half_life(entry, layer):
    base = layer.half_life_days
    multiplier = 1.0
    for tag in entry.tags:
        if tag in layer.importance_tags:
            multiplier = max(multiplier, layer.importance_tags[tag])
    return base * multiplier
```

Example: An `architectural` memory tagged `critical` gets `180 × 2.0 = 360 days` effective half-life.

### 6.5 Inter-layer search

Retrieval always searches across ALL layers. The composite scoring naturally handles cross-layer ranking because:
- Higher-tier memories tend to have higher confidence (slower decay)
- The relevance signal is tier-agnostic (BM25 doesn't care about tier)
- Frequency and recency are per-entry, not per-tier

No special cross-layer logic is needed in retrieval.

---

## 7. Out-of-the-Box Profiles

### 7.1 `repo-brain` (default — backward compatible)

**Use case:** Code repositories, AI coding assistants (Claude Code, Cursor, OpenClaw dev agents).

```yaml
profile:
  name: "repo-brain"
  description: "Memory for code repositories and AI coding assistants"

  layers:
    - name: "architectural"
      description: "System decisions, tech stack, infrastructure"
      half_life_days: 180
      decay_model: "exponential"
      importance_tags: { critical: 2.0, security: 1.5 }
      promotion_to: null
      demotion_to: "pattern"

    - name: "pattern"
      description: "Coding conventions, API patterns, design patterns"
      half_life_days: 60
      decay_model: "exponential"
      importance_tags: { critical: 1.5 }
      promotion_to: "architectural"
      promotion_threshold: { min_access_count: 10, min_age_days: 30, min_confidence: 0.7 }
      demotion_to: "context"

    - name: "procedural"
      description: "Workflows, deployment steps, build commands"
      half_life_days: 30
      decay_model: "exponential"
      promotion_to: "pattern"
      promotion_threshold: { min_access_count: 8, min_age_days: 14, min_confidence: 0.6 }
      demotion_to: "context"

    - name: "context"
      description: "Session-specific facts, current task details"
      half_life_days: 14
      decay_model: "exponential"
      confidence_floor: 0.05
      promotion_to: "procedural"
      promotion_threshold: { min_access_count: 5, min_age_days: 7, min_confidence: 0.5 }
      demotion_to: null

  scoring:
    relevance: 0.40
    confidence: 0.30
    recency: 0.15
    frequency: 0.15
```

### 7.2 `personal-assistant`

**Use case:** OpenClaw personal agents, general-purpose AI assistants managing daily life, preferences, schedules.

```yaml
profile:
  name: "personal-assistant"
  description: "Memory for personal AI assistants (OpenClaw, general agents)"

  layers:
    - name: "identity"
      description: "User identity, core preferences, long-term goals"
      half_life_days: 365
      decay_model: "power_law"
      decay_exponent: 0.5              # Very slow tail — identity almost never forgotten
      importance_tags: { preference: 1.5, identity: 2.0 }
      promotion_to: null
      demotion_to: "long-term"

    - name: "long-term"
      description: "Durable facts, relationships, learned preferences"
      half_life_days: 90
      decay_model: "exponential"
      importance_tags: { important: 1.5, recurring: 1.3 }
      promotion_to: "identity"
      promotion_threshold: { min_access_count: 20, min_age_days: 60, min_confidence: 0.8 }
      demotion_to: "short-term"

    - name: "short-term"
      description: "Recent conversations, active tasks, temporary notes"
      half_life_days: 7
      decay_model: "exponential"
      promotion_to: "long-term"
      promotion_threshold: { min_access_count: 5, min_age_days: 3, min_confidence: 0.5 }
      demotion_to: "ephemeral"

    - name: "ephemeral"
      description: "Momentary context, current conversation state"
      half_life_days: 1
      decay_model: "exponential"
      confidence_floor: 0.0            # Can fully decay
      promotion_to: "short-term"
      promotion_threshold: { min_access_count: 2, min_age_days: 1, min_confidence: 0.3 }
      demotion_to: null

  scoring:
    relevance: 0.30
    confidence: 0.20
    recency: 0.30                      # Recency matters more for personal use
    frequency: 0.20

  recall:
    default_token_budget: 3000         # Larger budget for personal context
```

### 7.3 `customer-support`

**Use case:** Support agents that need to remember customer history, tickets, product knowledge, and escalation patterns.

```yaml
profile:
  name: "customer-support"
  description: "Memory for customer support agents"

  layers:
    - name: "product-knowledge"
      description: "Product features, pricing, policies, known issues"
      half_life_days: 120
      decay_model: "exponential"
      importance_tags: { policy: 2.0, pricing: 1.5, known-issue: 1.5 }
      promotion_to: null
      demotion_to: "customer-patterns"

    - name: "customer-patterns"
      description: "Common issues, resolution playbooks, escalation triggers"
      half_life_days: 60
      decay_model: "exponential"
      importance_tags: { escalation: 1.5, recurring: 1.3 }
      promotion_to: "product-knowledge"
      promotion_threshold: { min_access_count: 15, min_age_days: 30, min_confidence: 0.7 }
      demotion_to: "interaction-history"

    - name: "interaction-history"
      description: "Recent customer interactions, open tickets, pending actions"
      half_life_days: 14
      decay_model: "exponential"
      importance_tags: { vip: 2.0, escalated: 1.5 }
      promotion_to: "customer-patterns"
      promotion_threshold: { min_access_count: 5, min_age_days: 7, min_confidence: 0.5 }
      demotion_to: "session-context"

    - name: "session-context"
      description: "Current conversation state, active ticket details"
      half_life_days: 3
      decay_model: "exponential"
      confidence_floor: 0.05
      promotion_to: "interaction-history"
      promotion_threshold: { min_access_count: 3, min_age_days: 1, min_confidence: 0.4 }
      demotion_to: null

  scoring:
    relevance: 0.35
    confidence: 0.25
    recency: 0.15
    frequency: 0.25                    # Frequent issues should surface fast
```

### 7.4 `research-knowledge`

**Use case:** Research agents, knowledge management, literature review, long-running investigations.

```yaml
profile:
  name: "research-knowledge"
  description: "Memory for research and knowledge management agents"

  layers:
    - name: "established-facts"
      description: "Verified findings, published results, confirmed hypotheses"
      half_life_days: 365
      decay_model: "power_law"
      decay_exponent: 0.7              # Slow tail for established knowledge
      importance_tags: { verified: 2.0, published: 1.5, replicated: 2.0 }
      promotion_to: null
      demotion_to: "working-knowledge"

    - name: "working-knowledge"
      description: "Current hypotheses, draft findings, literature notes"
      half_life_days: 60
      decay_model: "exponential"
      importance_tags: { promising: 1.3, cited: 1.5 }
      promotion_to: "established-facts"
      promotion_threshold: { min_access_count: 12, min_age_days: 30, min_confidence: 0.8 }
      demotion_to: "observations"

    - name: "observations"
      description: "Raw observations, experiment notes, data points"
      half_life_days: 21
      decay_model: "exponential"
      promotion_to: "working-knowledge"
      promotion_threshold: { min_access_count: 5, min_age_days: 7, min_confidence: 0.5 }
      demotion_to: "scratch"

    - name: "scratch"
      description: "Quick notes, brainstorm ideas, tentative thoughts"
      half_life_days: 3
      decay_model: "exponential"
      confidence_floor: 0.0
      promotion_to: "observations"
      promotion_threshold: { min_access_count: 3, min_age_days: 1, min_confidence: 0.3 }
      demotion_to: null

  scoring:
    relevance: 0.50                    # Research is query-driven
    confidence: 0.25
    recency: 0.10
    frequency: 0.15

  limits:
    max_entries: 1000                  # Research generates more entries
    max_value_length: 8192             # Longer notes
```

### 7.5 `project-management`

**Use case:** PM agents tracking tasks, decisions, stakeholders, timelines, risks.

```yaml
profile:
  name: "project-management"
  description: "Memory for project management and coordination agents"

  layers:
    - name: "decisions"
      description: "Approved decisions, strategic direction, stakeholder commitments"
      half_life_days: 180
      decay_model: "exponential"
      importance_tags: { approved: 2.0, stakeholder: 1.5, budget: 1.5 }
      promotion_to: null
      demotion_to: "plans"

    - name: "plans"
      description: "Active plans, milestones, dependencies, timelines"
      half_life_days: 45
      decay_model: "exponential"
      importance_tags: { deadline: 1.5, blocker: 2.0, risk: 1.5 }
      promotion_to: "decisions"
      promotion_threshold: { min_access_count: 10, min_age_days: 30, min_confidence: 0.7 }
      demotion_to: "activity"

    - name: "activity"
      description: "Recent updates, status changes, meeting notes, action items"
      half_life_days: 14
      decay_model: "exponential"
      importance_tags: { action-item: 1.3, blocker: 1.5 }
      promotion_to: "plans"
      promotion_threshold: { min_access_count: 5, min_age_days: 7, min_confidence: 0.5 }
      demotion_to: "noise"

    - name: "noise"
      description: "FYI updates, casual observations, transient context"
      half_life_days: 5
      decay_model: "exponential"
      confidence_floor: 0.05
      promotion_to: "activity"
      promotion_threshold: { min_access_count: 3, min_age_days: 2, min_confidence: 0.4 }
      demotion_to: null

  scoring:
    relevance: 0.35
    confidence: 0.25
    recency: 0.25                      # PM needs fresh information
    frequency: 0.15
```

### 7.6 `home-automation`

**Use case:** Home automation agents (HomeIQ-style), managing routines, device state, preferences, future events.

```yaml
profile:
  name: "home-automation"
  description: "Memory for home automation and IoT agents"

  layers:
    - name: "household-profile"
      description: "Residents, preferences, allergies, routines, permanent config"
      half_life_days: 365
      decay_model: "power_law"
      decay_exponent: 0.5
      importance_tags: { safety: 3.0, allergy: 3.0, medical: 3.0 }
      promotion_to: null
      demotion_to: "learned-patterns"

    - name: "learned-patterns"
      description: "Behavioral patterns, energy usage, schedule regularities"
      half_life_days: 60
      decay_model: "exponential"
      importance_tags: { energy: 1.3, comfort: 1.2 }
      promotion_to: "household-profile"
      promotion_threshold: { min_access_count: 20, min_age_days: 30, min_confidence: 0.8 }
      demotion_to: "recent-events"

    - name: "recent-events"
      description: "Recent device events, anomalies, guest visits, weather"
      half_life_days: 7
      decay_model: "exponential"
      importance_tags: { anomaly: 2.0, maintenance: 1.5, guest: 1.3 }
      promotion_to: "learned-patterns"
      promotion_threshold: { min_access_count: 5, min_age_days: 3, min_confidence: 0.5 }
      demotion_to: "transient"

    - name: "future-events"
      description: "Scheduled events, reminders, maintenance due dates"
      half_life_days: 90
      decay_model: "exponential"
      importance_tags: { maintenance: 1.5, recurring: 1.3 }
      promotion_to: "learned-patterns"
      promotion_threshold: { min_access_count: 3, min_age_days: 14, min_confidence: 0.5 }
      demotion_to: "transient"

    - name: "transient"
      description: "Momentary sensor readings, door open/close, motion events"
      half_life_days: 1
      decay_model: "exponential"
      confidence_floor: 0.0
      promotion_to: "recent-events"
      promotion_threshold: { min_access_count: 3, min_age_days: 1, min_confidence: 0.3 }
      demotion_to: null

  scoring:
    relevance: 0.30
    confidence: 0.20
    recency: 0.35                      # Home automation is very recency-sensitive
    frequency: 0.15

  gc:
    session_expiry_days: 1             # Transient data expires fast

  limits:
    max_entries: 750                   # More entries for device state
```

---

## 8. Hive Architecture — Multi-Agent Shared Brain

### 8.1 The problem

Today's multi-agent setups suffer from memory isolation. OpenClaw's own GitHub tracks issues where Agent A recalls Agent B's memories (no isolation) or where agents can't share knowledge at all (too much isolation). The 2026 research consensus is clear: most real systems need **local working memory with selectively shared artifacts** — neither full isolation nor full sharing.

tapps-brain already has federation (cross-project pub/sub via a hub DB). The Hive extends this to **cross-agent** sharing within and across projects, with agent-level profiles controlling what each agent sees and contributes.

### 8.2 Core concepts

```
┌─────────────────────────────────────────────────────────┐
│                     HIVE (shared)                       │
│                                                         │
│  ~/.tapps-brain/hive/hive.db                           │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │  Universal   │  │  Domain      │  │  User         │ │
│  │  Knowledge   │  │  Knowledge   │  │  Profile      │ │
│  │             │  │  (per-skill) │  │  (identity)   │ │
│  └──────┬──────┘  └──────┬───────┘  └──────┬────────┘ │
│         │                │                  │          │
│         └────────────────┼──────────────────┘          │
│                          │                              │
└──────────────────────────┼──────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌─────▼──────┐  ┌─────▼──────┐
   │  Dev Agent   │  │ Calendar   │  │  HomeIQ    │
   │  (work)      │  │  Agent     │  │  Agent     │
   │              │  │            │  │            │
   │  Profile:    │  │  Profile:  │  │  Profile:  │
   │  repo-brain  │  │  personal  │  │  home-auto │
   │              │  │  assistant │  │  mation    │
   │  Private:    │  │            │  │            │
   │  code memory │  │  Private:  │  │  Private:  │
   │  patterns    │  │  schedules │  │  devices   │
   └─────────────┘  └────────────┘  └────────────┘
```

### 8.3 Three-level memory hierarchy

| Level | Location | Scope | Examples |
|-------|----------|-------|----------|
| **Hive** | `~/.tapps-brain/hive/hive.db` | All agents | User identity, preferences, timezone, relationships, common facts |
| **Domain** | Hive DB, skill-namespaced | Agents sharing a skill | "repo-brain" domain: tech stack decisions, coding patterns. "home-automation" domain: device registry |
| **Agent-private** | `{project}/.tapps-brain/memory.db` | Single agent only | Session context, in-progress work, agent-specific observations |

### 8.4 Scoping model

Every memory has two scope dimensions:

```yaml
# Existing scope (visibility within a project)
scope: "project" | "branch" | "session" | "shared"

# New: agent scope (visibility across agents)
agent_scope: "private" | "domain" | "hive"
```

| Agent Scope | Who can read | Who can write | Sync direction |
|-------------|-------------|---------------|----------------|
| `private` | This agent only | This agent only | Never leaves agent store |
| `domain` | Agents with the same skill/profile | This agent (propagates up) | Agent → Hive domain namespace |
| `hive` | All agents | Any agent (with conflict resolution) | Agent ↔ Hive bidirectional |

### 8.5 Agent registration

Each agent registers with the Hive, declaring its identity and skills:

```yaml
# ~/.tapps-brain/hive/agents.yaml
agents:
  - id: "work"
    name: "Dev Agent"
    profile: "repo-brain"
    skills: ["coding", "git", "architecture"]
    project_root: "C:\\cursor\\tapps-brain"

  - id: "homeiq"
    name: "HomeIQ Agent"
    profile: "home-automation"
    skills: ["iot", "scheduling", "energy"]
    project_root: null                       # Not project-bound

  - id: "main"
    name: "Personal Assistant"
    profile: "personal-assistant"
    skills: ["calendar", "communication", "research"]
    project_root: null
```

### 8.6 Knowledge flow patterns

**Bottom-up propagation (Agent → Hive):**

When an agent saves a memory, the propagation engine decides where it goes:

```python
def propagate(entry, agent, hive):
    if entry.agent_scope == "private":
        return  # Stays in agent store only

    if entry.agent_scope == "domain":
        # Publish to the agent's skill domain in the Hive
        hive.save(entry, namespace=agent.profile)

    if entry.agent_scope == "hive":
        # Publish to the universal namespace
        hive.save(entry, namespace="universal")
```

**Top-down injection (Hive → Agent):**

When an agent runs `recall()`, it queries both its private store AND the Hive:

```python
def recall(message, agent, hive):
    # 1. Search agent's private store
    private_results = agent.store.recall(message)

    # 2. Search the Hive — universal knowledge
    hive_universal = hive.search(message, namespace="universal")

    # 3. Search the Hive — domain knowledge for this agent's skills
    hive_domain = hive.search(message, namespace=agent.profile)

    # 4. Merge, deduplicate, rank
    return merge_and_rank(private_results, hive_universal, hive_domain)
```

**Lateral sharing (Agent A → Hive → Agent B):**

Agent A (Dev Agent) discovers "We use PostgreSQL 16" and saves it with `agent_scope: "hive"`. Agent B (Calendar Agent) can now recall this fact when asked about database schedules, without Agent A explicitly sharing it.

### 8.7 Conflict resolution

When multiple agents write conflicting facts to the Hive:

| Strategy | When to use | How it works |
|----------|-------------|-------------|
| **Last-write-wins** | Low-stakes facts | Timestamp-based; most recent write takes precedence |
| **Source-authority** | Domain-specific facts | The agent whose profile matches the domain wins. A dev agent's opinion about code architecture beats a calendar agent's |
| **Confidence-max** | General knowledge | Highest confidence version wins |
| **Supersede chain** | Versioned facts | Uses existing bi-temporal versioning — both versions kept with validity windows |

```python
class ConflictPolicy(StrEnum):
    last_write_wins = "last_write_wins"
    source_authority = "source_authority"
    confidence_max = "confidence_max"
    supersede = "supersede"
```

The default is `supersede` — it preserves history and lets the retrieval engine rank versions by recency and confidence.

### 8.8 Domain namespaces

Domain namespaces group knowledge by skill area. Agents with matching skills can read/write to the domain:

```
hive.db
├── universal/          # All agents: user identity, preferences
├── repo-brain/         # Dev agents: code patterns, architecture
├── personal-assistant/ # Personal agents: schedules, contacts
├── home-automation/    # HomeIQ agents: device state, routines
└── customer-support/   # Support agents: product knowledge, playbooks
```

**Cross-domain queries:** An agent can read from any domain but only writes to its own. This prevents a calendar agent from accidentally overwriting code architecture facts.

**Domain subscription:** Similar to existing federation subscriptions:

```yaml
# Agent "main" subscribes to domain knowledge from all skills
subscriptions:
  - domain: "repo-brain"
    min_confidence: 0.7       # Only high-confidence code facts
    tag_filter: ["architecture", "tech-stack"]
  - domain: "home-automation"
    min_confidence: 0.5
```

### 8.9 Hive store implementation

The Hive store extends the existing `FederatedStore` concept:

```
~/.tapps-brain/
├── hive/
│   ├── hive.db              # Shared SQLite (WAL mode)
│   ├── agents.yaml          # Agent registry
│   └── domains/
│       ├── universal.yaml   # Universal domain config
│       ├── repo-brain.yaml  # Dev domain config
│       └── ...
├── memory/
│   └── federated.db         # Existing federation hub (kept for cross-machine)
└── profile.yaml             # User-global default profile
```

**Key design decisions:**
- **Single SQLite DB** for the Hive (not one per domain) — keeps queries fast and transactional
- **Namespace column** in the memories table: `namespace TEXT DEFAULT 'universal'`
- **Agent column** tracks provenance: `source_agent TEXT`
- **WAL mode** for concurrent reads from multiple agents
- **The Hive is local** — it's the shared brain for agents on this machine. Cross-machine sharing uses the existing federation hub

### 8.10 Profile integration

The profile YAML gains a `hive` section:

```yaml
profile:
  name: "repo-brain"

  hive:
    enabled: true
    agent_id: "work"                    # This agent's ID in the registry
    write_to_hive: true                 # Can this agent write to the Hive?
    default_agent_scope: "domain"       # Default scope for new memories
    auto_propagate_tiers:               # Which tiers auto-propagate up?
      - "architectural"                 # Always share arch decisions
      - "pattern"                       # Share patterns with domain peers
    private_tiers:                      # These never leave the agent
      - "context"                       # Session context stays private
      - "ephemeral"
    conflict_policy: "supersede"        # How to handle Hive conflicts
    recall_from_hive: true              # Include Hive results in recall?
    hive_recall_weight: 0.8             # Hive results scored at 80% of local

  layers:
    # ... layer definitions ...
```

### 8.11 Recall with Hive integration

The recall orchestrator gains Hive awareness:

```python
def recall(message, config, agent_store, hive_store):
    # Local recall (existing behavior)
    local = agent_store.recall(message)

    if not config.hive.enabled or not config.hive.recall_from_hive:
        return local

    # Hive recall
    hive_results = hive_store.search(
        message,
        namespaces=["universal", config.name],  # Universal + agent's domain
        min_confidence=config.hive.min_confidence,
    )

    # Score Hive results at configured weight
    for result in hive_results:
        result.score *= config.hive.hive_recall_weight

    # Merge, deduplicate (same key = keep highest score)
    merged = deduplicate_by_key(local.memories + hive_results)
    merged.sort(key=lambda m: m.score, reverse=True)

    # Apply token budget
    return apply_token_budget(merged, config.recall.default_token_budget)
```

### 8.12 OpenClaw mapping

For the user's existing OpenClaw setup with agents `main`, `work`, and `homeiq`:

| OpenClaw Agent | tapps-brain Profile | Hive Role | Domains |
|----------------|--------------------|-----------|---------|
| `main` | `personal-assistant` | Reads all domains, writes to `universal` + `personal-assistant` | Universal consumer |
| `work` | `repo-brain` | Reads/writes `repo-brain` domain + reads `universal` | Code knowledge authority |
| `homeiq` | `home-automation` | Reads/writes `home-automation` domain + reads `universal` | IoT knowledge authority |

**What flows through the Hive:**
- User preferences (timezone, name, communication style) → `universal` → all agents see it
- "We use PostgreSQL 16" → `repo-brain` domain → `work` agent authority, `main` can read
- "Living room lights prefer warm white" → `home-automation` domain → `homeiq` authority
- Session-specific debug context → `private` → never leaves the agent

### 8.13 Relationship to existing federation

| Feature | Federation (existing) | Hive (new) |
|---------|----------------------|------------|
| Scope | Cross-project | Cross-agent (same machine) |
| Storage | `~/.tapps-brain/memory/federated.db` | `~/.tapps-brain/hive/hive.db` |
| Sync model | Explicit publish/subscribe | Automatic propagation by agent_scope |
| Conflict resolution | Last-write-wins | Configurable (supersede/authority/confidence) |
| Use case | Share code patterns between repos | Share knowledge between agents |

**They coexist:** Federation shares knowledge across machines/repos. The Hive shares knowledge across agents on one machine. A memory can flow: Agent → Hive → Federation Hub → Remote Hive → Remote Agent.

---

## 9. Implementation Plan

### Phase 1: Profile loading (S — no behavior change)

1. Create `src/tapps_brain/profile.py` with `MemoryProfile`, `LayerDefinition`, `ScoringConfig`, etc.
2. Add `_load_profile()` to `MemoryStore.__init__()` — reads YAML or falls back to built-in `repo-brain`
3. Ship 6 built-in profiles as YAML files in `src/tapps_brain/profiles/`
4. Expose `store.profile` property for introspection
5. `DecayConfig` reads half-lives from profile layers instead of hardcoded defaults
6. **Zero behavior change** with default profile — existing tests pass unchanged

### Phase 2: Configurable scoring (S — retrieval change)

1. `MemoryRetriever` reads weights from `profile.scoring` instead of module-level constants
2. BM25 normalization K and frequency cap from profile
3. `RecallConfig` defaults from `profile.recall`

### Phase 3: Flexible tier validation (S — model change)

1. `MemoryEntry` tier validation checks against `profile.layers` names instead of `MemoryTier` enum
2. Keep `MemoryTier` enum as a convenience alias for the `repo-brain` profile
3. MCP `memory_save` validates tier against active profile

### Phase 4: Promotion/demotion engine (M — new feature)

1. Create `src/tapps_brain/promotion.py` with `PromotionEngine`
2. Hook into `store.reinforce()` — check promotion after reinforcement
3. Hook into GC — check demotion during candidate identification
4. Log promotions/demotions to audit JSONL

### Phase 5: Enhanced decay models (M — math change)

1. Add power-law decay: `C₀ × (1 + t/τ)^(-β)` as an option
2. Add FSRS-inspired stability growth on reinforcement
3. Add importance multiplier via tags
4. All models configurable per-layer in the profile

### Phase 6: Profile CLI & MCP tools (S — interface change)

1. `tapps-brain profile show` — display active profile
2. `tapps-brain profile list` — list built-in profiles
3. `tapps-brain profile set <name>` — switch profile
4. MCP tool: `profile_info()` — return active profile details

### Phase 7: Hive store (L — new subsystem)

1. Create `src/tapps_brain/hive.py` with `HiveStore` — extends existing `FederatedStore` patterns
2. Hive SQLite schema: `namespace TEXT`, `source_agent TEXT`, existing `memories` columns
3. Agent registry: `~/.tapps-brain/hive/agents.yaml` with Pydantic models
4. `agent_scope` field added to `MemoryEntry` (default `"private"` — backward compatible)
5. Propagation engine: on `store.save()`, check `agent_scope` and propagate to Hive if needed
6. Conflict resolution via `ConflictPolicy` enum (default `supersede`)

### Phase 8: Hive-aware recall (M — retrieval change)

1. `RecallOrchestrator` queries both agent store and Hive store
2. Hive results scored at configurable weight (`hive_recall_weight`)
3. Deduplication by key across stores (highest score wins)
4. Domain namespace filtering based on agent's skills/profile
5. MCP tools: `hive_status()`, `hive_search()`, `hive_propagate()`

### Phase 9: Agent registration & domain management (S — config)

1. Agent registration CLI: `tapps-brain agent register --id work --profile repo-brain`
2. Domain subscription config in profile YAML
3. Cross-domain read access with write restriction (agents write only to their own domain)
4. Audit logging for Hive writes with source agent attribution

---

## 10. Migration & Backward Compatibility

### Existing data

- **No SQLite migration needed** — `tier` is already stored as `TEXT`
- Entries with `tier="architectural"` remain valid in any profile that defines an `architectural` layer
- If a profile does not define a layer matching an existing entry's tier, the entry uses the profile's **lowest half-life layer** as fallback (graceful degradation)

### Existing code

- `MemoryTier` enum is **kept** — it becomes a convenience alias for the 4 `repo-brain` layers
- All existing tests pass with the default `repo-brain` profile
- `DecayConfig` becomes a derived view of the profile (not the source of truth)
- The `tier` parameter in MCP tools / Python API remains a `str` — no breaking change

### New stores

- New stores get a `profile.yaml` written on first init with the user's chosen profile (or `repo-brain` default)
- `store.set_profile(name_or_path)` allows switching at runtime (writes new YAML, reloads)

---

## 11. Sources

### 2026 AI Memory Frameworks
- [Mem0 Memory Types](https://docs.mem0.ai/core-concepts/memory-types) — Episodic, semantic, procedural, associative memory API
- [Mem0 arXiv Paper](https://arxiv.org/html/2504.19413v1) — "Building Production-Ready AI Agents with Scalable Long-Term Memory"
- [Letta (MemGPT) Docs](https://docs.letta.com/concepts/memgpt/) — Core, Recall, Archival tiered memory
- [Letta Memory Management](https://docs.letta.com/advanced/memory-management/) — Agent-controlled memory via function calls
- [Zep arXiv Paper](https://arxiv.org/abs/2501.13956) — "A Temporal Knowledge Graph Architecture for Agent Memory"
- [Cognee Architecture](https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory) — Graph + Vector + Relational 3-layer engine
- [LangMem Conceptual Guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) — Semantic, episodic, procedural with namespaces
- [AWS AgentCore Memory Deep Dive](https://aws.amazon.com/blogs/machine-learning/building-smarter-ai-agents-agentcore-long-term-memory-deep-dive/) — Pluggable memory strategies
- [OpenClaw Memory Docs](https://docs.openclaw.ai/concepts/memory) — MEMORY.md + sqlite-vec hybrid search
- [OpenClaw ContextEngine Deep Dive](https://openclaws.io/blog/openclaw-contextengine-deep-dive) — 7 lifecycle hooks, slot-based plugin architecture
- [6 Best AI Agent Memory Frameworks 2026](https://machinelearningmastery.com/the-6-best-ai-agent-memory-frameworks-you-should-try-in-2026/)
- [AI Agent Memory Types & Best Practices](https://47billion.com/blog/ai-agent-memory-types-implementation-best-practices/)

### Cognitive Science & Decay Mathematics
- [Ebbinghaus Forgetting Curve (Wikipedia)](https://en.wikipedia.org/wiki/Forgetting_curve) — R = e^(-t/S) formula and history
- [Wickelgren Power Law](https://www.researchgate.net/publication/51389724_The_Wickelgren_Power_Law_and_the_Ebbinghaus_Savings_Function) — Power-law decay: P(recall) = m(1 + ht)^(-f)
- [FSRS Algorithm Wiki](https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm) — DSR model, 21 parameters, power-law retrievability
- [FSRS ABC Guide](https://github.com/open-spaced-repetition/fsrs4anki/wiki/abc-of-fsrs) — Difficulty, Stability, Retrievability definitions
- [FSRS Technical Explanation](https://expertium.github.io/Algorithm.html) — Detailed math and parameter optimization
- [Atkinson-Shiffrin Model (Wikipedia)](https://en.wikipedia.org/wiki/Atkinson%E2%80%93Shiffrin_memory_model) — Sensory → STM → LTM multi-store model
- [Emotional Modulation of Memory (PMC5438110)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5438110/) — Amygdala-mediated consolidation enhancement
- [NEVER Forget: Negative Emotional Valence (PMC6613951)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6613951/) — Negative events consolidate more strongly
- [Influences of Emotion on Learning (PMC5573739)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5573739/) — Valence and arousal effects on retention

### Configurable Architecture Patterns
- [Microsoft Agent Framework](https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework-the-open-source-engine-for-agentic-ai-apps/) — YAML-defined agents with pluggable memory
- [PersonaNexus](https://github.com/PersonaNexus/personanexus) — YAML personality/behavioral profiles for AI agents
- [Neo4j Agent Memory](https://github.com/neo4j-labs/agent-memory) — Graph-native memory with configurable entity schemas
- [AI Agents Stack 2026 Edition](https://medium.com/data-science-collective/the-ai-agents-stack-2026-edition-37fa32db7a56)

### Multi-Agent / Hive Architecture
- [Multi-Agent Memory from a Computer Architecture Perspective](https://arxiv.org/html/2603.10062) — Shared vs distributed memory, coherence protocols, consistency models
- [Multi-Agent Shared Graph Memory (Neo4j)](https://neo4j.com/nodes-ai/agenda/multi-agent-shared-graph-memory-building-collective-knowledge-for-agents/) — Building collective knowledge for agents
- [Ruflo Hive Mind Architecture](https://github.com/ruvnet/ruflo) — Queen-led hierarchical coordination with shared memory bus
- [Memory in Multi-Agent Systems](https://medium.com/@cauri/memory-in-multi-agent-systems-technical-implementations-770494c0eca7) — Conflict resolution, CRDTs, synchronization protocols
- [OpenClaw Sub-Agents Docs](https://docs.openclaw.ai/tools/subagents) — Per-agent memory isolation via AsyncLocalStorage
- [OpenClaw Memory Isolation Issue #15325](https://github.com/openclaw/openclaw/issues/15325) — Cross-agent memory bleed in multi-agent setups
- [Mem0 Multi-Agent Collaboration](https://docs.mem0.ai/cookbooks/frameworks/llamaindex-multiagent) — user_id/agent_id scoping for shared memory
- [Accenture: Hive Mind for AI Agents](https://www.accenture.com/in-en/insights/data-ai/hive-mind-harnessing-power-ai-agents) — Collective intelligence beyond individual agent instantiations
