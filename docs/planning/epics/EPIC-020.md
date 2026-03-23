---
id: EPIC-020
title: "Code Review — Safety & Validation"
status: planned
priority: high
created: 2026-03-22
target_date: 2026-04-30
tags: [review, security, safety, validation, quality]
---

# EPIC-020: Code Review — Safety & Validation

## Context

Full code review of safety, injection detection, validation, and contradiction handling. These are security-critical — extra scrutiny required. Prompt injection defense and rate limiting are key trust boundaries.

## Success Criteria

- [ ] `safety.py` + `injection.py` reviewed (prompt injection defense)
- [ ] `doc_validation.py` reviewed (document validation)
- [ ] `contradictions.py` reviewed (contradiction detection)
- [ ] `seeding.py` reviewed (initial memory bootstrap)
- [ ] `rate_limiter.py` reviewed (rate limiting)
- [ ] All issues found are fixed with tests

## Stories

See `.ralph/fix_plan.md` tasks 020-A through 020-E.
