# Ralph Feedback Report

**Project:** tapps-brain
**Ralph version:** v1.0+ (agent mode)
**Claude CLI version:** 2.1.80
**Report date:** 2026-03-21
**Observation window:** 2026-03-20 to 2026-03-21 (2 sessions, 17 total loops)

---

## Executive Summary

Ralph has been highly productive on this project, completing 11 epics and progressing well into EPIC-012. The core loop works: it reads the fix_plan, executes one task, commits, and moves on. However, several issues in stream parsing, temp file cleanup, the multi-result detection heuristic, a missing circuit breaker function, and response analysis field extraction are generating noise and could affect reliability at scale.

**Overall rating: Strong.** Ralph is delivering real value. The issues below are polish/reliability concerns, not blockers.

---

## 1. Productivity Metrics

### Session: 2026-03-21 (primary observation)

| Loop | Task | Duration (wall) | API Duration | Cost (USD) | Turns | Commit |
|------|------|-----------------|-------------|------------|-------|--------|
| 1 | 012-B: Daily note import | 5m 33s | 1m 45s | $0.61 | 19 | `8aa806e` |
| 2 | 012-C: Markdown import tests | 6m 35s | 2m 39s | $0.75 | 19 | `524397d` |
| 3 | 012-D: Plugin skeleton | 10m 25s | 2m 14s | $0.52 | 1* | `4c2430f` |
| 4 | 012-E: Bootstrap hook | 13m 58s | 3m 40s | $1.30 | 1* | `83f108a` |
| 5 | 012-F: Ingest hook | 7m 14s | 2m 34s | $0.77 | 1* | `ba79f5f` |
| 6 | 012-G: afterTurn hook | ~2m+ | ongoing | — | — | `6b83dcd` |

**Totals (loops 1-5):** ~44 min wall time, $3.95 USD, 6 tasks completed
**Average cost per task:** ~$0.79
**Average wall time per task:** ~7.3 min

*Loops 3-5 show `num_turns: 1` in the final result object because the multi-result stream only captured the tail end (see Issue #2 below). Actual turn counts were higher.*

### Cumulative Progress

- **Epics completed:** 11 of 12 (001-011)
- **EPIC-012 tasks completed:** 7 of 17 (012-A through 012-G)
- **Lines added this session:** 1,244 across 10 files
- **All commits:** Clean, well-formatted, referencing story IDs per convention

---

## 2. Issues Found

### Issue #1: Multi-Task Loop Violation False Positives (Medium)

**What happens:** Ralph's stream parser detects multiple `"type":"result"` objects in the JSON stream and logs:
```
[WARN] Stream contains N result objects (expected 1). Multi-task loop violation detected.
```

**Frequency:** 3 of 6 loops on 2026-03-21 (loops 3, 4, 5).

**Root cause hypothesis:** When Claude Code uses background agents (subagents like `ralph-tester`, `ralph-explorer`), each agent's completion emits its own result object into the stream. The stream parser counts all result objects, not just the top-level one. This is not a true multi-task violation — Ralph correctly did one task per loop and produced one commit per loop.

**Evidence:** Every loop's `RALPH_STATUS` block reports `TASKS_COMPLETED_THIS_LOOP: 1`. The `modelUsage` in those loops shows Haiku and Sonnet usage alongside Opus, confirming subagent delegation (e.g., loop 4 used Opus + Haiku, loop 5 used Opus + Haiku + Sonnet).

**Impact:** Currently cosmetic — the warning is logged but doesn't trigger circuit breaker or halt. However, if future versions act on this signal (e.g., penalizing the agent or resetting sessions), it would cause false punishments for correct behavior.

**Recommendation:** Filter result objects by `parent_tool_use_id`. Only count top-level results (where `parent_tool_use_id` is `null`) toward the multi-task threshold. Subagent results should be ignored or tracked separately.

---

### Issue #2: Emergency JSONL Extraction on Every Loop (Medium)

**What happens:** Every loop triggers:
```
[WARN] Emergency JSONL extraction: converted multi-value stream to single result object
```

**Frequency:** 5 of 5 completed loops (100%).

**Root cause hypothesis:** The `--output-format json` stream from Claude CLI v2.1.80 emits structured JSONL with `stream_event`, `assistant`, `user`, and `result` message types. Ralph's parser expects a single clean result object, but the modern CLI always streams interleaved events. The "emergency" fallback is actually the normal path now.

**Impact:** The fallback works correctly — all loops completed and status was extracted. But labeling the normal code path as "emergency" creates log noise and masks genuine parsing failures. If a real extraction error occurs, it would be indistinguishable from this routine warning.

**Recommendation:**
1. Rename the fallback to something like "stream extraction" (not "emergency").
2. Consider making JSONL stream parsing the primary path for CLI v2.1+, with the single-object parser as the legacy fallback.
3. Add a structured log field (e.g., `extraction_method: "stream"` vs `"direct"`) rather than a warning.

---

### Issue #3: Orphaned `status.json` Temp Files (Low)

**What happens:** Temp files accumulate in `.ralph/`:
```
status.json.8ZT8Jh
status.json.F4RkH5
status.json.aONMS8
status.json.q6lUV1
status.json.uoZJsO
(+ 4 more)
```

**Frequency:** 9 orphaned temp files observed (5 untracked in git).

**Root cause hypothesis:** Ralph uses atomic writes (write to temp file, then rename). On Windows/WSL, the rename may succeed but the cleanup of the temp file may not — possibly due to cross-filesystem moves between WSL and NTFS, or file locking. The temp files are created with `mktemp` suffixes but never deleted on the success path.

**Impact:** Low — just disk clutter. But they show up in `git status` as untracked files, which is noisy for the developer.

**Recommendation:**
1. Add cleanup logic after successful rename: `rm -f "$tmpfile" 2>/dev/null`.
2. Consider adding a `.gitignore` entry for `status.json.*` (glob pattern) in the `.ralph/` directory.
3. On loop startup, clean any stale `status.json.*` files older than 1 hour.

---

### Issue #4: Previous Session Crash Recovery (Low)

**What happens:** The 2026-03-21 session started with:
```
[WARN] Previous Ralph invocation crashed (exit code: 130)
```

And the earlier crash at 15:42:
```
[ERROR] Ralph loop crashed (exit code: 130)
[WARN] Failed to write stream output to log file (exit code 130)
[WARN] jq filter had issues parsing some stream events (exit code 130)
```

Exit code 130 = SIGINT (user Ctrl+C or WSL session termination).

**What went right:** Ralph correctly detected the crash, reset the circuit breaker, and resumed cleanly on the next invocation. No data was lost. The circuit breaker moved to HALF_OPEN but allowed work to proceed.

**What could improve:**
1. The crash left behind partial state (the stale temp files from Issue #3).
2. The log shows the `jq` filter failure alongside the crash — `jq` processes should be terminated gracefully when the parent receives SIGINT, not left to fail with their own exit code 130.

**Recommendation:** Add a `trap` handler for SIGINT/SIGTERM that kills child processes (`jq`, `tee`, stream watchers) before exiting. This would produce a cleaner shutdown log.

---

### Issue #5: Permission Denial Halt (2026-03-20 Session) (Medium)

**What happened:** On 2026-03-20, loop #9 was halted:
```
[WARN] Permission denied for 1 command(s): Bash(uv run python -c ")
[ERROR] Permission denied - halting loop
```

**Root cause:** `.ralphrc` allows `Bash(uv *)` and `Bash(python *)` but Claude tried `Bash(uv run python -c "...")` which the glob pattern `Bash(uv *)` should have matched. The log shows a truncated command (ending with `")`), suggesting the permission check may have been tripped by special characters (quotes, parentheses) in the command string.

**Impact:** Ralph halted the entire loop and reset the session. The developer had to intervene. This is the correct safety behavior, but the root cause appears to be a parsing bug in the permission matcher, not an actually dangerous command.

**Recommendation:**
1. Investigate whether the permission glob matcher handles quoted arguments and special characters correctly.
2. Consider logging the full command (not truncated) when a permission denial occurs, so the developer can diagnose the mismatch.
3. A `Bash(uv run *)` explicit entry in `ALLOWED_TOOLS` would work around this specific case.

---

### Issue #6: `cb_is_open: command not found` (Medium)

**What happens:** Between loops, the log shows:
```
/home/tappt/.ralph/ralph_loop.sh: line 1939: cb_is_open: command not found
```

**When observed:** Between Loop #6 completion and Loop #7 startup (2026-03-21 ~16:40).

**Root cause hypothesis:** The `cb_is_open` function is called in `ralph_loop.sh` at line 1939 but is either not defined, not sourced, or conditionally defined in a code path that wasn't executed. This is a missing function in the circuit breaker subsystem.

**Impact:** Medium — the circuit breaker health check silently fails. If the circuit breaker *should* be open (e.g., after repeated failures), Ralph would proceed with the next loop anyway because the gate check errors out rather than returning true. This undermines the circuit breaker's purpose as a safety mechanism.

**Recommendation:**
1. Verify `cb_is_open` is defined and exported/sourced before line 1939. Check if it was renamed (e.g., to `circuit_breaker_is_open`) in a refactor without updating all call sites.
2. Add `set -u` or at minimum a guard (`type cb_is_open &>/dev/null || ...`) so missing functions fail loudly instead of silently continuing.
3. Add a unit/integration test that exercises the circuit breaker open → half-open → closed lifecycle to catch regressions like this.

---

### Issue #7: Response Analysis Returns UNKNOWN / Empty Fields (Medium)

**What happens:** After Loop #6, the Response Analysis summary shows:
```
Exit Signal:    false
Files Changed:  1
Work Type:      UNKNOWN
Summary:        (empty)
```

This is despite the Claude output containing a well-formed `---RALPH_STATUS---` block with `WORK_TYPE: IMPLEMENTATION` and a full recommendation string.

**Frequency:** Observed on Loop #6 (task 012-G). Earlier loops appeared to parse correctly.

**Root cause hypothesis:** The response analysis reads from `status.json`, which is populated by the stream parser (see Issue #2). When the "emergency JSONL extraction" fallback runs, it may extract the result text but fail to parse the structured `RALPH_STATUS` fields from it. The `Work Type: UNKNOWN` default suggests the regex/parser that extracts `WORK_TYPE` from the status block didn't match, possibly due to the status block being embedded in a larger `assistant` message JSON object rather than appearing as plain text.

**Impact:** Medium — Ralph continues to the next loop regardless (the `Exit Signal: false` was parsed correctly), but the loss of `Work Type` and `Summary` means:
- The circuit breaker can't distinguish productive loops from idle ones based on work type
- The developer monitoring Ralph gets no summary of what was accomplished
- Any logic that gates on work type (e.g., skipping test-only loops for rate limiting) would fail

**Recommendation:**
1. After extracting the result text from the JSONL stream, apply the `RALPH_STATUS` regex parser to the extracted text, not the raw JSON.
2. Add a fallback: if `WORK_TYPE` is `UNKNOWN` but `FILES_MODIFIED > 0`, infer `IMPLEMENTATION` as a sensible default.
3. Log the raw extracted result text at DEBUG level so parsing failures can be diagnosed without re-reading the full stream log.

---

### Issue #8: Coverage Drift Below Threshold (Informational)

**Observation:** Loop 5 (task 012-F) noted in its status:
> "The coverage shortfall (92% vs 95%) and lint issues are pre-existing and not related to my TypeScript-only change."

The project requires 95% coverage (`--cov-fail-under=95`). Ralph correctly identified this as pre-existing and not caused by its changes, but it also didn't flag it for human attention or attempt to fix it.

**Recommendation:** This is not a Ralph tooling issue — it's a project state issue. However, Ralph could optionally emit a `COVERAGE_BELOW_THRESHOLD` field in its status block when it detects coverage below the configured minimum, so the loop harness can surface it.

---

## 3. What Ralph Does Well

1. **One task per loop discipline.** Despite the false-positive multi-task warnings, Ralph actually completed exactly one task per loop across all 6 observed iterations. Commits are clean and atomic.

2. **Commit message quality.** Every commit follows the `feat(story-NNN.N): description` convention consistently. Messages are descriptive and match the fix_plan task names.

3. **Self-verification.** Ralph runs tests and type checks before committing. When it delegates verification to background agents (Haiku/Sonnet), it waits for confirmation before reporting success.

4. **Crash resilience.** After a SIGINT crash, Ralph recovered cleanly on the next invocation without human intervention or data loss.

5. **Cost efficiency.** At ~$0.79 per task with Opus as the primary model, Ralph is using cache effectively (cache read tokens greatly exceed fresh input tokens in every loop).

6. **Fix plan maintenance.** Ralph updates `fix_plan.md` checkboxes after each task, keeping the single source of truth accurate.

---

## 4. Configuration Notes

The `.ralphrc` used in this observation:

| Setting | Value | Notes |
|---------|-------|-------|
| `MAX_CALLS_PER_HOUR` | 100 | Generous; only 4-6 calls used per hour |
| `CLAUDE_TIMEOUT_MINUTES` | 30 | Adequate; longest loop was ~14 min |
| `SESSION_CONTINUITY` | false | Fresh context per loop; avoids context drift |
| `CB_NO_PROGRESS_THRESHOLD` | 3 | Conservative; appropriate for this project |
| `RALPH_USE_AGENT` | true | Agent mode working well with CLI v2.1+ |
| `RALPH_ENABLE_TEAMS` | false | Subagents still used via Claude Code's native Agent tool |
| `RALPH_BG_TESTING` | false | Despite this being false, Claude Code internally used background test agents |

**Note on `RALPH_BG_TESTING`:** This is set to `false` in `.ralphrc`, but Claude Code's own agent system (via the `ralph-tester` agent definition) runs tests in background regardless. This is a distinction between Ralph's team parallelism feature and Claude Code's native subagent capability. Consider documenting this distinction.

---

## 5. Environment-Specific Observations (Windows/WSL)

- **Cross-filesystem issues:** Temp file cleanup failures (Issue #3) are likely NTFS/WSL interaction issues. The project lives on `/mnt/c/` (NTFS) accessed from WSL.
- **jq dependency:** jq parsing failures on crash (exit code 5, exit code 130) suggest jq is being used for stream processing. On WSL, jq may behave differently with large streaming inputs piped from Windows-hosted processes.
- **Session crash pattern:** Exit code 130 (SIGINT) occurred when the WSL session was likely terminated from Windows. Ralph's `trap` handling could be more robust for this scenario.

---

## 6. Summary of Recommendations

| # | Issue | Severity | Effort | Recommendation |
|---|-------|----------|--------|----------------|
| 1 | Multi-task false positives | Medium | Low | Filter by `parent_tool_use_id`; only count top-level results |
| 2 | Emergency JSONL on every loop | Medium | Medium | Make stream parsing the primary path for CLI v2.1+ |
| 3 | Orphaned temp files | Low | Low | Add cleanup after rename; add `.gitignore` pattern |
| 4 | Crash cleanup | Low | Low | Add `trap` handler to kill child processes on SIGINT |
| 5 | Permission denial parsing | Medium | Medium | Fix glob matcher for quoted/special-char arguments; log full command |
| 6 | `cb_is_open` not found | Medium | Low | Define/source missing function; add guard or `set -u` |
| 7 | Response analysis returns UNKNOWN | Medium | Medium | Parse `RALPH_STATUS` from extracted text, not raw JSON |
| 8 | Coverage threshold surfacing | Info | Low | Add optional `COVERAGE_BELOW_THRESHOLD` status field |

---

*Report generated from analysis of `.ralph/logs/ralph.log`, loop output files, `git log`, and `.ralphrc` configuration.*
