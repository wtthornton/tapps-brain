# Memory decay: power-law vs exponential

This guide explains the two decay models tapps-brain supports, how to choose between them, and the calibration math behind the `repo-brain` profile's migration to power-law (STORY-SC02 / TAP-558).

For the FSRS-style adaptive stability layer that sits on top of these curves, see [`memory-decay-and-fsrs.md`](memory-decay-and-fsrs.md).

## TL;DR

- **Exponential** (`decay_model: exponential`) is the historical default. Simple, and its half-life parameter has a crisp "half gone in H days" interpretation.
- **Power-law** (`decay_model: power_law`) matches Ebbinghaus / Wixted forgetting data and the FSRS spaced-repetition family. Same-ish near-term shape, **fatter tail** — memories are forgotten more gradually at long ages.
- The `repo-brain` built-in profile ships on **power-law** for all four tiers (architectural / pattern / procedural / context), calibrated so `R(t = half_life) = 0.5` — median retention at the half-life mark is unchanged from the prior exponential configuration.

## The two formulas

Decay is computed lazily on read and multiplied into the entry's base confidence, then clamped to `[layer_confidence_floor, source_ceiling]`.

### Exponential

```
R_exp(t) = 0.5 ^ (t / H)
```

- `H` — layer half-life in days.
- `R_exp(H) = 0.5` exactly, by construction.
- Tail is thin: `R_exp(3H) = 0.125`, `R_exp(6H) ≈ 0.016`.

### Power-law (FSRS family)

```
R_pl(t) = (1 + t / (k · H)) ^ (-β)
```

- `H` — layer half-life in days.
- `k` — scaling constant. Default `81/19 ≈ 4.263`, the FSRS-4 canonical value (FSRS expresses its curve as `R = (1 + F·t/S)^C` with `F = 19/81`, so `k = 1/F`). See the [FSRS algorithm wiki][fsrs-wiki].
- `β` — decay exponent. Tuning parameter; see calibration below.

### Why power-law

Power-law is the shape that actually fits human forgetting data from Ebbinghaus onward. Exponential is a simplification that happens to be easy to reason about and cheap to compute. For an agent-memory system:

- **Shape**: Wixted & Ebbesen (1997), Wixted & Carpenter (2007), and Murre & Dros (2015) all show power-law fits empirical retention data far better than exponential. Murre & Dros report r² = 0.97 re-fitting Ebbinghaus's own 1885 data with a power-law.
- **Tail**: power-law decays more slowly at long ages. For an agent, this is the direction you want — architectural decisions from 18 months ago shouldn't vanish as fast as `0.5^6 ≈ 1.5%` implies.
- **Lineage**: FSRS is the canonical spaced-repetition algorithm; using `k = 81/19` puts tapps-brain in the same family.

## Calibration: preserving `R(H) = 0.5`

When migrating an existing exponential profile to power-law, the usual ask is *keep the median retention the same at the half-life mark, but fatten the tail*. That requires solving for β given `k`:

```
R_pl(H) = (1 + 1/k) ^ (-β) = 0.5
⇒ β = ln(2) / ln(1 + 1/k)
```

With `k = 81/19`:

```
β = ln(2) / ln(1 + 19/81)
  = ln(2) / ln(100/81)
  ≈ 3.29
```

This is the `decay_exponent` value used by every tier in the `repo-brain` profile (see [`src/tapps_brain/profiles/repo-brain.yaml`](../../src/tapps_brain/profiles/repo-brain.yaml)). At `t = H` the two curves intersect exactly (by construction); before `H` the power-law decays slightly faster, after `H` it decays slower.

Numerical spot-check at pattern tier (`H = 60d`, `β = 3.29`, `k = 81/19`):

| t (days) | R_exp   | R_pl    | Δ (pl − exp) |
|---------:|--------:|--------:|-------------:|
| 0        | 1.000   | 1.000   | 0            |
| 15       | 0.841   | 0.817   | −0.024       |
| 30       | 0.707   | 0.672   | −0.035       |
| 60 (= H) | 0.500   | 0.500   | 0            |
| 120      | 0.250   | 0.290   | +0.040       |
| 240      | 0.063   | 0.111   | +0.048       |
| 480      | 0.004   | 0.037   | +0.033       |

Near-term and tail behavior intentionally diverge — the tail is fatter, which is the cog-sci-correct direction and matches the Ebbinghaus shape.

## Configuration

### Profile (YAML)

Each layer in a profile can independently pick its decay model, exponent, and scaling constant:

```yaml
layers:
  - name: "architectural"
    half_life_days: 180
    decay_model: "power_law"     # default: "exponential"
    decay_exponent: 3.29         # half-life-anchor for k = 81/19
    # decay_k: 4.263             # optional per-layer override; inherits global default
    confidence_floor: 0.10
```

If `decay_k` is omitted on a layer, the FSRS-canonical default (`81/19`) is used. Set it only when you want a layer to decay differently from the rest of the profile — e.g. a `session` layer with `decay_k: 1.0` to get a steeper near-term drop.

### `DecayConfig` (code)

```python
from tapps_brain.decay import DecayConfig

config = DecayConfig(
    decay_model="power_law",
    decay_exponent=3.29,
    decay_k=81 / 19,
    # Per-layer overrides (all optional):
    layer_decay_models={"architectural": "power_law", "context": "exponential"},
    layer_decay_exponents={"architectural": 3.29},
    layer_decay_k={"architectural": 4.263},
)
```

Per-layer overrides always take precedence over the global `decay_model` / `decay_exponent` / `decay_k`.

### Standalone functions

For testing or ad-hoc analysis:

```python
from tapps_brain.decay import exponential_decay, power_law_decay

exponential_decay(days=30, half_life=60)              # ≈ 0.707
power_law_decay(days=30, half_life=60, decay_exponent=3.29)  # ≈ 0.672
```

Both return a multiplier in `(0, 1]`. `0.0` days returns `1.0` exactly; negative days are clamped to `0`.

## Tuning guide

Three questions drive most tuning:

1. **Do I want the exponential semantic (crisp half-life) or the empirical shape (fat tail)?**
   - Crisp operational semantics, short-lived tiers, or mixing with external exponential models → **exponential**.
   - Agent memory, long-lived tiers, matching human forgetting data → **power-law**.

2. **If power-law, do I want `R(H) = 0.5` to hold?**
   - Yes → use `β = ln(2) / ln(1 + 1/k)`. With `k = 81/19`, that's `β ≈ 3.29`.
   - No, I want FSRS-at-90 %-retrievability semantics (`H` means *stability*, not half-life) → use `β = 0.5` with `k = 81/19`. `R(H) ≈ 0.90` in that regime. This is what `personal-assistant.yaml` uses for its `identity` tier.

3. **Should this layer decay differently at long ages from the rest of the profile?**
   - Yes → set `decay_k` per layer. Smaller `k` → steeper near-term, thinner tail. Larger `k` → flatter near-term, fatter tail.

### Bounds

- `decay_exponent ∈ [0.1, 10.0]` — upper bound raised from the original 5.0 in STORY-SC02 to permit the anchor β (≈ 3.29) and future fits.
- `decay_k > 0` — no upper bound, but values above ~20 start behaving like linear decay over the time horizons we care about.
- `half_life_days ≥ 1` — enforced by `Field(ge=1)` on `LayerDefinition`.

## Design non-goals

- **Background decay worker.** Decay stays lazy-on-read. No thread, no cron, no LISTEN/NOTIFY trigger.
- **Observable equivalence at every point.** Exponential and power-law intersect at exactly one point; they cannot be observably identical everywhere. The `repo-brain` migration preserves median retention at `R(H) = 0.5` only. Near-term and tail behavior diverge by design.
- **Global `decay.mode` flag.** Per-layer `decay_model` is strictly more flexible and already shipped. Profiles that pin a layer to `exponential` keep working unchanged.
- **Learned decay (fit β from feedback).** Deliberately deferred — non-trivial overlap with the existing FSRS-lite `update_stability()` path documented in [`memory-decay-and-fsrs.md`](memory-decay-and-fsrs.md). Scope a follow-up story before building this.

## Performance

Power-law adds ~4–5 % CPU over exponential at 10k-entry scale — see `tests/benchmarks/test_decay_perf.py`. Both primitives are single-call `math.pow`; the cost difference is one more `+ 1` and one divide. Not a hot-path concern for any realistic workload.

## References

- [FSRS algorithm wiki][fsrs-wiki] — canonical `R = (1 + F·t/S)^C`, `F = 19/81`.
- [Expertium — FSRS technical explanation](https://expertium.github.io/Algorithm.html) — `k = 81/19` derivation.
- [Murre & Dros 2015 — *Replication and Analysis of Ebbinghaus' Forgetting Curve*](https://pmc.ncbi.nlm.nih.gov/articles/PMC4492928/) — power-law fits Ebbinghaus's own data with r² = 0.97.
- [Wixted & Carpenter 2007 — *The Wickelgren Power Law and the Ebbinghaus Savings Function*](http://wixtedlab.ucsd.edu/publications/wixted/Wixted_and_Carpenter_(2007).pdf).
- [Wixted & Ebbesen 1997 — *Genuine power curves in forgetting*](http://wixtedlab.ucsd.edu/publications/wixted/Wixted_and_Ebbesen_(1997).pdf).
- [Kahana & Adler — *Note on the Power Law of Forgetting*](https://memory.psych.upenn.edu/files/pubs/KahaAdle02.pdf).
- Companion research: [`docs/research/memory-systems-2026.md`](../research/memory-systems-2026.md) §1.3.

[fsrs-wiki]: https://github.com/open-spaced-repetition/fsrs4anki/wiki/The-Algorithm
