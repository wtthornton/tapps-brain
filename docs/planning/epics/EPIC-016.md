---
id: EPIC-016
title: "Test Suite Hardening — CLI gaps, concurrency, resource cleanup, edge cases"
status: done
priority: high
created: 2026-03-22
target_date: 2026-04-15
tags: [testing, coverage, concurrency, cli, hardening]
---

# EPIC-016: Test Suite Hardening

## Context

A coverage and quality audit of the 1641-test suite (95.54% coverage) revealed four categories of gaps:

1. **CLI federation commands have zero test coverage** — `federation_subscribe()`, `federation_unsubscribe()`, and `federation_publish()` are user-facing features with no tests at all. The `maintenance_gc` archive path and `agent_create` error path are also untested.
2. **Thread safety is claimed but never verified** — `MemoryStore`, `HiveStore`, `metrics`, and `recall` all use `threading.Lock` and document thread safety, but no concurrent tests exist.
3. **15 ResourceWarning: unclosed database** warnings during the test run — some test paths skip `store.close()` or MCP server teardown is incomplete.
4. **Unicode and boundary values** — FTS special chars are tested but emoji, CJK, RTL, and MAX_KEY_LENGTH/MAX_VALUE_LENGTH boundaries are not.

### Why Now

All 15 feature epics are complete. The suite is healthy but these gaps represent real risk: untested CLI commands can regress silently, thread-safety bugs can corrupt production data, and resource leaks accumulate under load.

## Success Criteria

- [ ] CLI federation commands (subscribe, unsubscribe, publish) have unit tests
- [ ] CLI maintenance gc archive path tested
- [ ] CLI agent_create error path (invalid profile) tested
- [ ] Concurrent save/recall/GC stress tests pass under ThreadPoolExecutor
- [ ] Zero ResourceWarning during test run (unclosed SQLite connections fixed)
- [ ] Unicode edge cases tested (emoji, CJK, RTL text in keys and values)
- [ ] Boundary value tests for MAX_KEY_LENGTH and MAX_VALUE_LENGTH
- [ ] 95% coverage maintained

## Stories

### STORY-016.1: CLI federation command tests

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/cli.py:743-819`, `tests/unit/test_cli.py`
**Verification:** `pytest tests/unit/test_cli.py -v -k federation`

#### Why

Three user-facing CLI commands — `federation subscribe`, `federation unsubscribe`, and `federation publish` — have zero test coverage. These are core federation features that can break silently on any refactor.

#### Acceptance Criteria

- [ ] `federation subscribe` happy path: subscribe to a project dir, verify output
- [ ] `federation unsubscribe` happy path: unsubscribe, verify subscription removed
- [ ] `federation publish` happy path: publish to hub, verify output
- [ ] Error paths: subscribe to non-existent dir, unsubscribe from unknown project

### STORY-016.2: CLI untested command paths

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `src/tapps_brain/cli.py:896-900`, `src/tapps_brain/cli.py:1395-1407`, `tests/unit/test_cli.py`
**Verification:** `pytest tests/unit/test_cli.py -v -k "gc or agent_create"`

#### Why

The `maintenance gc` archive path (lines 896-900) exercises the actual deletion logic, and `agent create` with an invalid profile (lines 1395-1407) is an important validation boundary. Both are untested.

#### Acceptance Criteria

- [ ] `maintenance gc` test: create stale entries, run gc (non-dry-run), verify entries archived
- [ ] `agent create` with invalid profile: verify error message lists available profiles
- [ ] `agent create` happy path already covered — confirm no regression

### STORY-016.3: Concurrent save stress test

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/hive.py`
**Verification:** `pytest tests/unit/test_concurrent.py -v`

#### Why

`MemoryStore` and `HiveStore` both claim thread safety via `threading.Lock`, but this is never verified. Concurrent saves from multiple threads could expose lock ordering bugs, data corruption, or deadlocks.

#### Acceptance Criteria

- [ ] New `tests/unit/test_concurrent.py` file
- [ ] Test: 10 threads saving 50 entries each concurrently — all 500 entries persisted, no corruption
- [ ] Test: 5 threads saving while 5 threads recalling concurrently — no exceptions, consistent results
- [ ] Test: concurrent save at max capacity (500) — eviction works correctly under contention
- [ ] All tests complete within 30 seconds (no deadlocks)

### STORY-016.4: Concurrent GC and Hive stress tests

**Status:** planned
**Effort:** M
**Depends on:** STORY-016.3
**Context refs:** `src/tapps_brain/gc.py`, `src/tapps_brain/hive.py`, `tests/unit/test_concurrent.py`
**Verification:** `pytest tests/unit/test_concurrent.py -v -k "gc or hive"`

#### Why

GC running concurrently with saves is a realistic production scenario. Hive propagation from multiple agents simultaneously is the core multi-agent use case. Neither is tested.

#### Acceptance Criteria

- [ ] Test: GC running while saves happen concurrently — no exceptions, archived entries consistent
- [ ] Test: multiple agents propagating to HiveStore concurrently — all entries arrive, no corruption
- [ ] Test: concurrent recall from Hive while propagation in progress — no exceptions
- [ ] No deadlocks (30-second timeout)

### STORY-016.5: Fix unclosed SQLite connections

**Status:** planned
**Effort:** M
**Depends on:** none
**Context refs:** `tests/unit/test_mcp_server.py`, `tests/unit/test_hive.py`, `tests/conftest.py`
**Verification:** `pytest tests/ -W error::ResourceWarning -q 2>&1 | tail -5`

#### Why

15 `ResourceWarning: unclosed database` warnings during the test run indicate leaked SQLite connections. In production under load, this leads to file descriptor exhaustion and "too many open files" errors.

#### Acceptance Criteria

- [ ] Identify all test paths producing ResourceWarning (likely MCP server tests and Hive tests)
- [ ] Add proper teardown (store.close(), connection.close()) to affected fixtures/tests
- [ ] Zero ResourceWarning when running full suite with `-W error::ResourceWarning`
- [ ] No new test failures introduced by tighter cleanup

### STORY-016.6: Unicode and boundary value tests

**Status:** planned
**Effort:** S
**Depends on:** none
**Context refs:** `src/tapps_brain/store.py`, `src/tapps_brain/models.py`, `tests/unit/test_memory_store.py`
**Verification:** `pytest tests/unit/test_edge_cases.py -v`

#### Why

FTS5 special characters are tested, but real-world memory values contain emoji, CJK, RTL text, and very long strings. The model has MAX_KEY_LENGTH and MAX_VALUE_LENGTH validators that are never exercised at the boundary.

#### Acceptance Criteria

- [ ] New `tests/unit/test_edge_cases.py` file
- [ ] Test: save and recall entries with emoji in key and value
- [ ] Test: save and recall entries with CJK characters (Chinese, Japanese, Korean)
- [ ] Test: save and recall entries with mixed RTL/LTR text (Arabic + English)
- [ ] Test: key at exactly MAX_KEY_LENGTH — accepted
- [ ] Test: key at MAX_KEY_LENGTH + 1 — rejected with validation error
- [ ] Test: value at exactly MAX_VALUE_LENGTH — accepted
- [ ] Test: value at MAX_VALUE_LENGTH + 1 — rejected with validation error
- [ ] FTS5 search works correctly with unicode content

### STORY-016.7: Final validation and status update

**Status:** planned
**Effort:** S
**Depends on:** STORY-016.1, STORY-016.2, STORY-016.3, STORY-016.4, STORY-016.5, STORY-016.6
**Context refs:** `docs/planning/STATUS.md`, `.ralph/fix_plan.md`
**Verification:** `pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95`

#### Why

Final gate to confirm all gaps are closed, coverage is maintained, and project status is updated.

#### Acceptance Criteria

- [ ] Full test suite passes with 95%+ coverage
- [ ] Zero ResourceWarning in test output
- [ ] Lint and type checks pass
- [ ] EPIC-016 status set to done
- [ ] STATUS.md updated with new test count and EPIC-016 completion

## Priority Order

| Order | Story | Rationale |
|-------|-------|-----------|
| 1 | 016.1 | Highest risk — zero coverage on user-facing commands |
| 2 | 016.2 | Quick win — two small CLI paths |
| 3 | 016.3 | Foundation for concurrency tests |
| 4 | 016.4 | Builds on 016.3 infrastructure |
| 5 | 016.5 | Resource cleanup — independent |
| 6 | 016.6 | Edge cases — independent |
| 7 | 016.7 | Final gate — depends on all above |
