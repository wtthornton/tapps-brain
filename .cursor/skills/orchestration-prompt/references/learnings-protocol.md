<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->
<!-- BEGIN: tapps-skill-asset orchestration-prompt/references/learnings-protocol.md v3.12.83 -->
# Learn as you go — the learnings.md protocol

## Learn as you go (measured evolution)

`learnings.md` (project-scoped) is written on **two** occasions. Both are required —
the second is the one that gets forgotten, and it is the richer of the two.

**1. At generation time (you, writing the prompt).** Read `learnings.md` before
drafting and fold in relevant lessons. When a generation teaches a better pattern — or
the user edits your output before running it — append a one-line lesson.

**2. At the end of every RUN of an emitted prompt.** The prompt itself must carry the
terminal lessons-learned sub-goal and the Done-when clause that gates on it (see
Guardrails and `assets/prompt-template.md`). Generation-time lessons capture what you
learned *planning*; run-time lessons capture what the work actually cost — and those
are the ones a fresh session cannot rediscover. If a run finished without them, the
harness paid for the mistake and kept none of the value.

Keep lessons **project-scoped**; never bleed them across repos.

**What a lesson must be.** Transferable to a *different* task, concrete enough to
falsify later, and where possible carrying the cheap command that detects the trap.
Mine what an independent verifier **refuted** before anything else — a refuted claim
is by construction something the loop believed and got wrong, which is the densest
lesson available. Then what cost the most retries, then any premise that turned out
false, then evidence that did not prove what it appeared to.

**What a lesson is not.** A narration of the run (that is the handoff). A one-off
project fact — a ticket id, a port, a service quirk — which belongs in brain or a
project memory file. A near-duplicate of an existing bullet: read the file first and
*sharpen the existing line* instead. And never filler — **zero lessons is a legitimate
outcome**, stated in one line. A manufactured lesson corrupts this file the same way
an invented error corrupts a correction.

**Keep it readable.** This file is read in full before every generation, so every
stale bullet taxes every future run. The byte ceiling (40 KB) is the binding one —
bullet count alone is misleading, since a handful of long bullets can blow the byte
budget while staying under 120, and 120 short bullets can stay well under 40 KB. Past
either ceiling, merge overlapping lines and delete ones overtaken by a fixed tool or a
changed codebase. Pruning is part of the loop, not cleanup deferred forever.

Treat this as a *measured* loop, not a scratchpad: the harness improves by observing
its own runs. When a golden set (`evals/evals.json`) and a gated improvement loop
(`SELF_IMPROVEMENT.md`) exist, promote a template change only when it shows measured
lift against the evals — don't hand-tune blind.
<!-- END: tapps-skill-asset -->
