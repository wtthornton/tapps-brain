---
id: EPIC-018
title: "Code Review — Retrieval & Scoring"
status: planned
priority: medium
created: 2026-03-22
target_date: 2026-04-30
tags: [review, retrieval, scoring, bm25, quality]
---

# EPIC-018: Code Review — Retrieval & Scoring

## Context

Full code review of all retrieval, scoring, ranking, and search files. The retrieval layer is where the source_trust regression (BUG-002) was found — extra scrutiny warranted.

## Success Criteria

- [ ] `retrieval.py` reviewed (composite scoring engine, source trust)
- [ ] `recall.py` reviewed (orchestration, Hive merging)
- [ ] `bm25.py` + `fusion.py` reviewed (text scoring)
- [ ] `similarity.py` reviewed (Jaccard + TF-IDF)
- [ ] `embeddings.py` + `reranker.py` reviewed (optional ML components)
- [ ] All issues found are fixed with tests

## Stories

See `.ralph/fix_plan.md` tasks 018-A through 018-E.
