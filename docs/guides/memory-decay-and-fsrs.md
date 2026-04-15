# Memory decay and FSRS-style stability

This document is the **product decision** for **EPIC-042** STORY-042.8: how tapps-brain combines time-based decay with optional adaptive stability.

## Decision: hybrid (tier baseline + FSRS-lite stability)

| Approach | Verdict |
| -------- | ------- |
| **Full FSRS** (interval scheduling, review queues, full parameter set) | **Out of scope** for the synchronous Python core. No background scheduler; recall is on-demand. |
| **Tier half-life only** | **Default path.** `DecayConfig` / profile `half_life_days` drive exponential (or power-law) decay. `MemoryEntry.stability == 0` means “use tier half-life as effective half-life.” |
| **Hybrid** | **Shipped model.** Optional **adaptive stability** adjusts `stability` (days) using deterministic `update_stability()` in `decay.py`. When `stability > 0`, `calculate_decayed_confidence()` uses it as the **effective** half-life (still clamped by source ceilings and tier floors). |

## Where stability updates run

| Path | When | `was_useful` |
| ---- | ---- | ------------ |
| **`MemoryStore.record_access(key, was_useful)`** | After retrieval feedback | Caller-supplied |
| **`MemoryStore.reinforce(key, …)`** | Explicit human/agent reinforce | Treated as **`True`** (strong positive signal) |

Both paths apply **only** if the entry’s profile layer has **`adaptive_stability: true`**. Default layers leave it `false` so behavior matches historical tier-only decay.

Reinforce still sets `last_reinforced` and bumps confidence via `reinforcement.reinforce()`. Stability is computed **before** merging those updates, using the entry’s timestamps **as they were prior to reinforce**, so retrievability reflects how “due” the memory was when it was reinforced.

## Composite ranking vs decay (no double-counting)

- **Decay** (`calculate_decayed_confidence`, `get_effective_confidence`) shrinks **stored confidence** over time (and optional stability stretches the effective half-life).
- **Retrieval** (`retrieval.py`) blends **relevance, confidence, recency, frequency** into a composite score. The **recency** term uses access/recency signals, not the same formula as exponential decay.

They compose: decay lowers effective confidence used in scoring; recency is an additional signal. Tuning either is intentional; see profile `scoring` and `decay` / layer blocks separately.

## Profile: per-tier half-lives

Profile **`layers[].half_life_days`** (≥ 1) map to `DecayConfig.layer_half_lives` via `decay_config_from_profile()`. Unknown tier strings fall back to `context_half_life_days` with a warning. Teams should align layer names with saved entry tiers (including custom profile layers).

## Related code

- `src/tapps_brain/decay.py` — `calculate_decayed_confidence`, `update_stability`, `decay_config_from_profile`
- `src/tapps_brain/store.py` — `record_access`, `reinforce`
- `src/tapps_brain/reinforcement.py` — clock reset + confidence boost (no stability logic)
- `src/tapps_brain/profile.py` — `LayerDefinition.adaptive_stability`
