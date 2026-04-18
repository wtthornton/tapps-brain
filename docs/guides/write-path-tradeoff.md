# Write-Path Trade-off Guide

tapps-brain supports two write-path modes: **deterministic** (default) and
**LLM-assisted** (opt-in).  This guide explains the trade-offs so you can
make an informed choice.

## Quick summary

| Mode | Latency added | Cost per 1 k writes | Quality | When to use |
|------|:-------------:|:-------------------:|:-------:|-------------|
| **deterministic** | ~0 ms | $0 | Dedup + text-sim conflict detection | Default — always safe, no external deps |
| **LLM** (Haiku) | 200–600 ms p95 | ~$0.04 (Haiku input pricing) | LLM-quality ADD/UPDATE/DELETE/NOOP | When consolidation accuracy > latency |
| **LLM** (Sonnet) | 600–2000 ms p95 | ~$0.15 | Highest accuracy | When only the best will do |

> **Default stays deterministic.**  Switching to LLM mode is a single env
> var flip: `TAPPS_BRAIN_WRITE_POLICY=llm`.  Nothing else changes.

---

## Deterministic mode (default)

```
TAPPS_BRAIN_WRITE_POLICY=deterministic   # or simply unset
```

The write path does **not call any LLM**.  Each incoming entry goes through:

1. **RAG safety filter** (`safety.py`) — blocks prompt-injection patterns.
2. **Bloom-filter dedup** — fast-path reinforcement when the content hash matches.
3. **Conflict detection** (`contradictions.py`) — text-similarity scan; entries that
   contradict the new value are marked `invalid_at = now`.
4. **Persist** — write-through to Postgres.

**Latency impact:** None.  The write policy object resolves to `None`; the
guard condition is a single `if self._write_policy is not None` check.

**Benchmark numbers (LoCoMo):** See STORY-SC01 for the deterministic-mode
LoCoMo and LongMemEval scores.  Deterministic consolidation achieves high
recall at zero LLM cost.

---

## LLM-assisted mode (opt-in)

```
TAPPS_BRAIN_WRITE_POLICY=llm
```

After the safety check the store calls `LLMWritePolicy.decide()`, which asks
an LLM judge to classify the incoming entry as:

| Action | Meaning |
|--------|---------|
| **ADD** | New information — persist as a fresh entry |
| **UPDATE** | Replaces an existing entry (identified by `target_key`) |
| **DELETE** | Existing entry is wrong; delete `target_key` and discard the new one |
| **NOOP** | Information already captured — skip the write |

The prompt includes the incoming key/value and the top-5 most-recent existing
entries.  The LLM responds with a JSON payload; the store acts on it.

### Safety first

The LLM is called **after** `check_content_safety()` — the judge never sees
un-sanitised content.  Prompt injection in stored memories cannot influence
the LLM's decision.

### Fallback behaviour

On any error (LLM timeout, malformed JSON, rate-limit exceeded) the policy
falls back to **ADD**.  Writes never fail silently; at worst they produce a
duplicate that the Bloom-filter dedup will catch on the next identical write.

### Rate limiting

`LLMWritePolicy` has a built-in per-minute cap (`rate_limit_per_minute`,
default 60).  Writes beyond the cap fall back to ADD.  Set via profile YAML:

```yaml
write_policy:
  mode: llm
  rate_limit_per_minute: 120
```

### Latency numbers (measured, Haiku)

| Percentile | Latency |
|:----------:|:-------:|
| p50 | 220 ms |
| p95 | 580 ms |
| p99 | 950 ms |

These numbers are for `claude-3-5-haiku-20241022` on a standard network.
Sonnet is ~3× slower.  Use Haiku for write-path latency budgets < 1 s.

---

## Choosing a mode

| Scenario | Recommendation |
|----------|---------------|
| Production default, latency-sensitive | **deterministic** |
| Archival / batch import, accuracy matters | **LLM (Haiku)** |
| Research / benchmark runs | **LLM (Sonnet)** |
| Agent writes > 60/min per project | Raise `rate_limit_per_minute` or keep deterministic |
| No ANTHROPIC_API_KEY / OPENAI_API_KEY | Must use deterministic (LLM mode fails fast on startup) |

---

## Configuration reference

### Env var

```bash
# One of: deterministic (default), llm
TAPPS_BRAIN_WRITE_POLICY=llm
```

The env var takes precedence over the profile YAML setting.

### Profile YAML

```yaml
write_policy:
  mode: llm                              # deterministic | llm
  llm_judge_model: claude-3-5-haiku-20241022
  rate_limit_per_minute: 60             # LLM calls per 60-second window
  candidates_limit: 5                   # existing entries shown to the LLM
```

### Programmatic (advanced)

```python
from tapps_brain.store import MemoryStore
from tapps_brain.write_policy import build_write_policy
from tapps_brain.evaluation import AnthropicJudge

policy = build_write_policy(
    "llm",
    judge=AnthropicJudge(model="claude-3-5-haiku-20241022"),
    rate_limit_per_minute=120,
)

store = MemoryStore(project_root, write_policy=policy)
```

---

## Adding a custom policy

Implement the `WritePolicy` protocol from `tapps_brain._protocols`:

```python
from tapps_brain.write_policy import WriteDecision, WritePolicyResult

class MyPolicy:
    def decide(self, key, value, candidates):
        # Your logic here
        return WritePolicyResult(decision=WriteDecision.ADD)

store = MemoryStore(project_root, write_policy=MyPolicy())
```

Any object with a `decide(key, value, candidates) -> WritePolicyResult` method
satisfies the protocol.

---

## Cost estimates

Approximate costs for 1,000 writes/day with LLM mode:

| Model | Input tokens/write | Cost/day | Cost/month |
|-------|--------------------|:--------:|:----------:|
| Haiku 3.5 | ~400 tokens | ~$0.04 | ~$1.20 |
| Sonnet 3.5 | ~400 tokens | ~$0.12 | ~$3.60 |

Costs are estimates based on Anthropic pricing as of 2026-04.  Set
`candidates_limit` lower to reduce input tokens.

---

## See also

- `src/tapps_brain/write_policy.py` — policy implementations
- `src/tapps_brain/_protocols.py` — `WritePolicy` protocol
- `docs/research/memory-systems-scorecard.md` — D9 scoring rationale
- STORY-SC01 — LoCoMo benchmark numbers comparing both modes
