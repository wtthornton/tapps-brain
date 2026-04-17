# LoCoMo benchmark

> Adapter landed via STORY-SC01 (TAP-557). Headline numbers are
> **placeholders** until a full run is executed with LLM answer/judge.
> Re-open this file after the run to fill in the results tables.

## Source

- **Paper:** [Maharana et al. 2024, arXiv:2402.17753](https://arxiv.org/abs/2402.17753)
- **Dataset:** [snap-research/locomo · data/locomo10.json](https://github.com/snap-research/locomo)
- **Shape:** 10 long multi-session conversations; 1,986 QA pairs across
  five categories. Core headline accuracy is typically reported over
  categories 1–4 (single-hop / multi-hop / temporal / open-domain);
  category 5 (adversarial, ~446 items) is conventionally excluded from
  the top-line.

Category counts cited in third-party summaries: single-hop 841,
multi-hop 321, temporal 96, open-domain 282, adversarial 446. Verify by
loading the JSON before publishing.

## Methodology

1. **Dataset load.** Clone `snap-research/locomo`, point the CLI at
   `data/locomo10.json`. Record the commit hash of the checkout.
2. **Seed.** For each QA item, wipe the store and load the full
   conversation history as memory (one entry per dialogue turn, keyed by
   `session_N:dia_id`, tier `context`).
3. **Retrieve.** Call `AgentBrain.recall(question, max_results=k)` with
   `k=5` by default.
4. **Answer.** LLM produces an answer of ≤6 words given the retrieved
   context (mem0's short-answer constraint — LoCoMo answers are
   typically terse entity/date spans).
5. **Grade.** LLM-as-judge returns `CORRECT`/`WRONG`. Adapted from mem0's
   `metrics/llm_judge.py` `ACCURACY_PROMPT`. See
   [`_common.py:JUDGE_PROMPT`](../../src/tapps_brain/benchmarks/_common.py).
6. **Aggregate.** Overall accuracy + per-category accuracy.

## Configuration

| Setting | Default |
|---|---|
| Retrieval k | 5 |
| Excluded categories | 5 (adversarial) |
| Answer model | `claude-haiku-4-5-20251001` or `gpt-4o-mini` |
| Judge model | `claude-haiku-4-5-20251001` for dev, `gpt-4o` for the headline number |
| Store | `live` (AgentBrain + Postgres) for headline; `in-memory` for smoke |
| Profile | `repo-brain` |

## Results (pending run)

Fill in once executed. Keep a dated copy in `benchmarks/runs/` and cite
the commit hash below.

| Metric | Value |
|---|---|
| Overall accuracy (core cats 1–4) | *TBD* |
| Single-hop accuracy | *TBD* |
| Multi-hop accuracy | *TBD* |
| Temporal accuracy | *TBD* |
| Open-domain accuracy | *TBD* |
| Wall time (minutes) | *TBD* |
| Judge token cost (USD) | *TBD* |

## Comparison

| System | LoCoMo accuracy | Source |
|---|---|---|
| mem0 | **91.6** | arXiv:2504.19413 |
| Memori | **81.95** | MemoriLabs/Memori README |
| tapps-brain | *TBD* | this doc |

## Reproducer

```bash
python scripts/run_benchmark.py locomo \
    --dataset /path/to/snap-research-locomo/data/locomo10.json \
    --answer-model anthropic \
    --judge anthropic \
    --store live \
    --k 5 \
    --output benchmarks/runs/locomo-$(date +%Y%m%d).json
```

Set `ANTHROPIC_API_KEY` (or switch to `openai` and set `OPENAI_API_KEY`).

## Changelog

- *YYYY-MM-DD* — initial run against commit `<dataset-hash>`, answer
  model `<model>`, judge `<model>`. Accuracy `<x.x>`.
