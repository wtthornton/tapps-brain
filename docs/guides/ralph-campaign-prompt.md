# Ralph campaign prompt (run + monitor + self-correct)

A reusable, copy-paste prompt for driving a **Ralph campaign** on tapps-brain
from a fresh Claude Code session: start the loop, monitor it live, and run a
self-correction guard that catches Ralph spinning and fixes it the supported
way (re-running the Ralph CLIs, never hand-editing protected files).

It leans on the **`ralph-runner` skill**, which already encodes the proven
startup, monitor-filter, kill-safety, and subagent-delegation patterns — so the
orchestrating agent keeps its own context window small.

Related: [`ralph-setup.md`](ralph-setup.md) (one-time setup + credentials),
[`ralph-claude-agent.md`](ralph-claude-agent.md) (Linear automation user).

## How to use

1. Open a fresh Claude Code session at the repo root.
2. Paste the prompt below verbatim.
3. The agent runs preflight, starts Ralph in a detached tmux session, and
   reports status after each loop. If it detects trouble it STOPS and tells you
   rather than pushing through.

> **Task source.** This repo runs in **Linear mode**
> (`RALPH_TASK_SOURCE=linear`); the queue is the
> [tapps-brain Linear project](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db).
> If you instead want a `fix_plan.md`-driven campaign, change step 3 of the
> prompt to point at `.ralph/fix_plan.md`.

## The prompt

```text
Run a Ralph campaign on tapps-brain with live monitoring and a self-correction loop. Use the ralph-runner skill.

SETUP / PREFLIGHT (do this before starting the loop):
1. cd to the repo root (the folder with pyproject.toml). Abort if not found.
2. Confirm Ralph is healthy first: run `ralph --version` and `ralph-doctor`. If ralph-doctor shows any WARN/FAIL, STOP and report it — fix only by re-running `ralph-upgrade-project --yes .`, never by hand-editing protected files (.ralph/hooks/, .ralphrc, .claude/settings.json, agents).
3. Confirm the task source: this repo runs in Linear mode (RALPH_TASK_SOURCE=linear), queue = the tapps-brain Linear project. Do NOT reorder or invent tasks. Pull the next priority issue from Linear, not from a stale fix_plan.md.
4. Check nothing is already running: look for .ralph/.ralph.lock and any tmux session named ralph-loop. If one exists, report it and ask before starting a second.

RUN + MONITOR:
5. Start Ralph in a detached tmux session so it survives this chat (use --live or --monitor). Print the log path.
6. Tail the loop log and report concise status after each iteration: current task/issue, loop count, pass/fail, token + rate-limit state, and circuit-breaker status. Keep your own context small — delegate bulk log analysis and Linear writes to subagents; don't paste raw logs back to me.

SELF-IMPROVEMENT / CORRECTNESS GUARD (the important part):
7. After each loop, verify Ralph is actually making progress, not spinning:
   - Confirm the loop produced a real commit (git log) tied to the issue it claimed.
   - Watch for the "no plan" recovery branch, repeated identical edits, or the no-progress circuit breaker opening — these mean Ralph is stuck.
   - If the circuit breaker opens or 2 consecutive loops make no committed progress: STOP the loop, diagnose root cause from the logs, and report. Common fixes: ralph-doctor drift (re-run ralph-upgrade-project --yes .), wrong task source, or a failing QA gate at an epic boundary.
8. Capture friction patterns you observe (recurring failures, flaky steps, missing context) and file them as follow-up Linear issues via the linear-issue skill — assigned to the agent user, never me.

GUARDRAILS:
- Only touch THIS repository. No cross-project writes.
- Never use `git push --force`, `git reset --hard`, or delete branches without asking.
- Do NOT edit .ralph/, .ralphrc, or .claude config by hand — those are Ralph's control files; the only supported change path is the ralph CLIs.
- QA (pytest/ruff/mypy) is deferred to epic boundaries — don't run it mid-epic.

Report a short status summary now and after every loop. If anything looks wrong, stop and tell me rather than pushing through.
```

## What each section does

- **Preflight** — refuses to start on a drifted install (`ralph-doctor` WARN/FAIL),
  which is the exact failure mode behind TAP-1681 ("no plan" recovery from a
  Linear-mode/​file-mode template mismatch). Drift is fixed only via
  `ralph-upgrade-project --yes .`.
- **Run + monitor** — detached tmux so the loop survives the chat; status is
  summarized, not raw-dumped, and bulk analysis is delegated to subagents.
- **Self-correction guard** — the loop is only "progress" if it produced a
  committed change tied to the claimed issue. Two no-progress loops or an open
  circuit breaker halts the campaign for diagnosis instead of burning rate limit.
- **Friction capture** — recurring problems become follow-up Linear issues
  (via the `linear-issue` skill, assigned to the agent user), closing the
  improvement loop.
- **Guardrails** — repo-scoped, no destructive git ops without asking, no
  hand-edits to Ralph control files, QA deferred to epic boundaries.
