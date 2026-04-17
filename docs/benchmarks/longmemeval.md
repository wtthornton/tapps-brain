# LongMemEval benchmark

> Adapter landed via STORY-SC01 (TAP-557). Headline numbers are
> **placeholders** until a full run is executed with LLM answer/judge.

## Source

- **Paper:** [Xiao et al. 2024, arXiv:2410.10813](https://arxiv.org/abs/2410.10813) (ICLR 2025)
- **Dataset:** [xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval)
  or the HF cleaned mirror `xiaowu0162/longmemeval-cleaned`.
- **License:** MIT.
- **Shape:** 500 questions across seven types
  (single-session-user/assistant/preference, multi-session,
  knowledge-update, temporal-reasoning, abstention (30 items)).
- **Splits:**
  - `longmemeval_oracle` — evidence-only, for upper-bound sanity.
  - `longmemeval_s` — ~115k tokens of haystack history per question
    (~40 sessions). This is our smoke-to-headline size.
  - `longmemeval_m` — ~500 sessions per question. Full size.

> The HF viewer currently errors on the `answer` column (mixed
> string/array types). The adapter normalises array answers to
> `" | "`-joined strings; prefer raw-JSON download for reproducibility.

## Methodology

1. **Dataset load.** Download `longmemeval_s.json` (or `_m.json`). Record
   repo commit hash.
2. **Seed.** For each question, wipe the store and load every turn from
   every session in `haystack_sessions` as memory, keyed by
   `session_id:turn_index`, tier `context`. Optional: pass the session
   date through as a `created_at` hint.
3. **Retrieve.** `AgentBrain.recall(question, max_results=k)`, `k=5`.
4. **Answer.** LLM answers given the retrieved context.
5. **Grade.** LLM-as-judge returns `CORRECT`/`WRONG`. The paper notes
   softer grading for `temporal-reasoning` (off-by-one days allowed) and
   `knowledge-update` (partial-credit for acknowledging older state);
   the default judge prompt is intentionally lenient ("same meaning =
   correct") to match.
6. **Aggregate.** Overall accuracy + per-`question_type` accuracy.

## Configuration

| Setting | Default |
|---|---|
| Split | `longmemeval_s` |
| Retrieval k | 5 |
| Answer model | `claude-haiku-4-5-20251001` or `gpt-4o-mini` |
| Judge model | `claude-haiku-4-5-20251001` dev / `gpt-4o-2024-08-06` paper-default headline |
| Store | `live` for headline; `in-memory` for smoke |

## Results (pending run)

| Metric | Value |
|---|---|
| Overall accuracy | *TBD* |
| single-session-user | *TBD* |
| single-session-assistant | *TBD* |
| single-session-preference | *TBD* |
| multi-session | *TBD* |
| knowledge-update | *TBD* |
| temporal-reasoning | *TBD* |
| abstention | *TBD* |
| Wall time (minutes) | *TBD* |
| Judge token cost (USD) | *TBD* |

## Comparison

| System | LongMemEval | Source |
|---|---|---|
| Graphiti (Zep OSS core) + GPT-4o | **63.8** | arXiv:2501.13956 |
| tapps-brain | *TBD* | this doc |

## Reproducer

```bash
python scripts/run_benchmark.py longmemeval \
    --dataset /path/to/longmemeval/data/longmemeval_s.json \
    --answer-model anthropic \
    --judge anthropic \
    --store live \
    --k 5 \
    --output benchmarks/runs/longmemeval-s-$(date +%Y%m%d).json
```

## Changelog

- *YYYY-MM-DD* — initial run against `longmemeval_s` commit
  `<dataset-hash>`. Accuracy `<x.x>`.
