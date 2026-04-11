# Ralph Improvement Recommendations

> Generated: 2026-04-10 after session startup  
> Observed: 5 loops, 4 tasks completed (059.1–059.4), ~40 minutes runtime

---

## Status: Healthy

Ralph is running correctly in agent mode with MCP tools enabled. 4 of 47 tasks completed.  
No blocking issues. The items below are optimizations worth applying.

---

## 1. Disable the failing Claude CLI auto-update

**Observation:** Every loop startup prints:
```
[WARN] Claude CLI update available: 2.1.92 → 2.1.101. Attempting auto-update...
[WARN] Claude CLI update failed — version unchanged at 2.1.92
```
This wastes ~6 seconds per loop and adds noise. `npm update -g` is failing because of write permissions on this machine.

**Fix:** In `.ralphrc`, change:
```bash
CLAUDE_AUTO_UPDATE=true
```
to:
```bash
CLAUDE_AUTO_UPDATE=false
```
Then update Claude manually when needed:
```bash
npm install -g @anthropic-ai/claude-code@latest
# or
~/.local/bin approach: see CLAUDE.md § WSL upgrade
```

---

## 2. Large tasks are spawning too many tool calls

**Observation:** Loop 3 (task 059.2 — SQLite removal) used **469 tool calls** and took **22 minutes**, with **20 system errors**. Loop 4 (task 059.3) used 148 tool calls with 6 errors. Loop 5 (task 059.4) was 42 tool calls with 0 errors — clean.

The 20 errors in loop 3 came from the `ralph-explorer` sub-agent probing for files that don't exist (e.g., searching for `.ralph` inside `src/tapps_brain/`). This wastes tokens and inflates loop time.

**Fix:** Add to `.claude/agents/ralph.md` in the "Sub-agents" section:
```
- ralph-explorer: constrain searches to src/tapps_brain/, tests/, docs/ — never probe .ralph/ or other control dirs
```
Also, the three remaining `[LARGE]` tasks in fix_plan (059.5, 059.8, 060.2) could each benefit from a note like:
```
Sub-agent turns capped at 30 for explorer — do targeted reads, not broad sweeps.
```

---

## 3. `status.json` work_type shows UNKNOWN on some loops

**Observation:** After some loops, the Response Analysis panel shows:
```
Work Type:  UNKNOWN
Summary:    false
```
even though progress was detected (`tasks=1 files=N`). This happens when Claude's final status JSON doesn't match Ralph's expected schema — typically when the task was complex and the response was long.

**Impact:** Low — the `on-stop` hook correctly detects progress regardless, so the circuit breaker stays healthy. But `UNKNOWN` loops do not increment the adaptive timeout calibration.

**Fix (workaround):** In `.ralph/PROMPT.md`, reinforce the status JSON output format. Add to the "REPORTING" section:
```
IMPORTANT: Always output the status JSON as the LAST thing in your response, on its own line, with no trailing text. Ralph's parser requires it to be the final JSON object.
```

---

## 4. Explorer sub-agent hitting scope errors

**Observation:** 4 "scope" errors in loop 4 from `ralph-explorer` attempting to read paths outside the project (e.g., probing sibling dirs or absolute paths outside the workspace). These do not block progress but waste tokens.

**Fix:** Add to `.claude/agents/ralph-explorer.md` disallowedTools or add a note in the system prompt:
```
Only read files within the workspace root /home/wtthornton/code/tapps-brain/. Do not probe paths outside this directory.
```

---

## 5. Adaptive timeout will calibrate after loop 5

**Observation:** Debug logs show:
```
[DEBUG] Adaptive timeout: only 4 samples (need 5) — using static 60m
```
This is self-resolving — Ralph needs 5 completed loops to calibrate a dynamic timeout. Loop 5 is in progress now. No action needed.

---

## 6. Hook permissions (already fixed)

**Observation:** On first startup all `.ralph/hooks/*.sh` were not executable.  
**Status:** Fixed — `chmod +x` applied to all hooks on 2026-04-10. No longer appearing in logs.

---

## Summary Table

| # | Issue | Severity | Fix Effort | Action |
|---|-------|----------|------------|--------|
| 1 | Auto-update noise | Low | 1 line | Set `CLAUDE_AUTO_UPDATE=false` in `.ralphrc` |
| 2 | LARGE task tool explosion | Medium | 2-3 lines | Add explorer scope guidance in `ralph.md` |
| 3 | UNKNOWN work_type in status | Low | 2 lines | Reinforce JSON output rule in `PROMPT.md` |
| 4 | Explorer scope errors | Low | 2 lines | Constrain explorer in `ralph-explorer.md` |
| 5 | Adaptive timeout calibrating | None | None | Self-resolves after loop 5 |
| 6 | Hook permissions | None | Done | Already fixed |
