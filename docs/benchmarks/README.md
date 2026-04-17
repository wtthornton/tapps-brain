# tapps-brain benchmarks

End-to-end QA benchmarks for tapps-brain. These are **answer-based**
evaluations — seed a memory store with a conversation history, ask a
question, produce an answer, grade the answer with an LLM-as-judge — and
are distinct from the retrieval-IR (BEIR) metrics in
`src/tapps_brain/evaluation.py`.

| Benchmark | Paper | Shape | Status |
|---|---|---|---|
| [LoCoMo](locomo.md) | [arXiv:2402.17753](https://arxiv.org/abs/2402.17753) | Long multi-session conversational memory | Adapter landed (STORY-SC01); published numbers **pending run** |
| [LongMemEval](longmemeval.md) | [arXiv:2410.10813](https://arxiv.org/abs/2410.10813) | Long-history multi-session QA | Adapter landed (STORY-SC01); published numbers **pending run** |

## Why answer-based and not retrieval-based?

The field is evaluating memory systems on whether the *answer* is correct
after memory retrieval, not whether retrieval alone returns the right
document. mem0 publishes LoCoMo accuracy this way; Graphiti publishes
LongMemEval accuracy this way; the Supermemory and Memori leaderboards
use the same judge-based setup. Retrieval IR metrics (precision@k,
recall@k, NDCG@k) live separately in `src/tapps_brain/evaluation.py` and
remain our regression harness for retrieval-layer changes.

## Library

Adapters live in [`src/tapps_brain/benchmarks/`](../../src/tapps_brain/benchmarks/):

- `_common.py` — `AnswerModel` / `AnswerJudge` Protocols, the shared
  `run_benchmark` aggregator, and deterministic stand-ins for smoke tests.
- `locomo.py` — `load_locomo()` + `run_locomo()`.
- `longmemeval.py` — `load_longmemeval()` + `run_longmemeval()`.

Both loaders accept a path to the dataset JSON and return a list of
typed items; both runners accept adapter callbacks (`seed_history`,
`retrieve_context`) so the library stays store-agnostic — tests plug a
pure-Python token-overlap retriever, real runs plug `AgentBrain`.

## Reproducer CLI

[`scripts/run_benchmark.py`](../../scripts/run_benchmark.py) is a thin CLI:

```bash
# Smoke run — no API keys, no Postgres, 5 items only
python scripts/run_benchmark.py locomo \
    --dataset data/locomo10.json \
    --limit 5 \
    --answer-model deterministic \
    --judge deterministic \
    --output benchmarks/runs/locomo-smoke.json

# Full run — requires ANTHROPIC_API_KEY or OPENAI_API_KEY + live Postgres
python scripts/run_benchmark.py longmemeval \
    --dataset data/longmemeval_s.json \
    --answer-model anthropic \
    --judge anthropic \
    --store live \
    --output benchmarks/runs/longmemeval-s.json
```

Output is a JSON `BenchmarkReport` with overall accuracy, per-category
accuracy, per-item results (question, reference, candidate, judge
label), wall-time, and metadata (answer model, judge, store, k, seed).

## Cost

Full runs are not free. Rough envelope (confirm before running):

| Benchmark | Items | Judge | Est. judge-token cost |
|---|---|---|---|
| LoCoMo core (excl. adversarial) | ~1,540 QA | Haiku 4.5 | low tens of USD |
| LoCoMo core | ~1,540 QA | GPT-4o | low hundreds of USD |
| LongMemEval-S | 500 | Haiku 4.5 | single-digit USD |
| LongMemEval-S | 500 | GPT-4o | low tens of USD |

`docs/research/memory-systems-scorecard.md` §Limitations flags that
"publishing a score is not free … hundreds of dollars per run." Budget
accordingly and document judge choice in the published result doc.

## Reproducibility

- `--seed` is threaded through the runner (reserved for future
  retrieval tie-breaking; dataset ordering is already deterministic).
- Dataset files are not vendored — record the dataset commit hash in the
  published methodology doc.
- Judge-model name and version are stamped into `report.metadata` on
  every run.

## Interpreting results

- Top-line: **overall accuracy** (correct / total).
- LoCoMo: report per-category accuracy (single_hop, multi_hop, temporal,
  open_domain); adversarial is excluded from the headline by default.
- LongMemEval: report per `question_type` accuracy — the paper's
  rubric notes softer grading for temporal and knowledge-update types.

## Scorecard impact

This work is tracked by TAP-557 (STORY-SC01) under EPIC TAP-556. The
scorecard's D2 "Retrieval quality" dimension moves from **2/5** →
**4/5** once numbers are published (any published benchmark with
methodology qualifies for 4; beating mem0's 91.6 LoCoMo would qualify
for 5). See [`docs/research/memory-systems-scorecard.md`](../research/memory-systems-scorecard.md).
