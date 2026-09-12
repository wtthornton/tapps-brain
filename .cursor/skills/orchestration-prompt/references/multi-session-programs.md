<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->
<!-- BEGIN: tapps-skill-asset orchestration-prompt/references/multi-session-programs.md v3.12.83 -->
# Multi-session programs

## Multi-session programs

Everything above assumes **one driver session**. A program run by two or more interactive
sessions has a different failure surface than a single-driver loop — see
`.claude/rules/agent-to-agent.md` for the transport, identity/authority caveat, coordination
protocol, epistemic discipline, and the N-party scaling analysis (§7) this section builds on.
Restating that protocol here, instead of pointing at it, is the exact drift this repo exists
to remove.

### When to emit a multi-session program

Emit one **only** when the work has an irreducible need for a second interactive driver:

- **A second reader for claims** — a second session earns its keep on *claims in prose*, never
  by re-running measurements (`.claude/rules/agent-to-agent.md` §5, where the assertions-vs-
  second-reader split was measured).
- **A hard contract edge** where two drivers must hold opposite sides.

Do **not** add a session to parallelise dispatch — one driver fans out lanes perfectly well, and
a second driver doubles the operator's authorisation load (below). More sessions buy review
coverage, never separation of powers (agent-to-agent.md §2, §7 — same account, same credential,
same blast radius at any N).

### What the prompt MUST carry when there is more than one driver

Add these to the nine load-bearing parts; a multi-session prompt missing them is incomplete —
each is governed in full by `.claude/rules/agent-to-agent.md`, referenced here rather than
restated:

10. **Partition** — which paths each session owns, as a table (agent-to-agent.md §4). An
    unassigned path is unassigned, not free.
11. **Integrator** — the single session that merges; everyone else opens PRs (agent-to-agent.md
    §4, §7.3).
12. **Review ring** — each session adversarially reads exactly one other's *conclusions*
    (agent-to-agent.md §5, §7.4 — a ring covers every claim once, all-pairs does not scale).
13. **Authorisation clause** — a peer relaying an operator decision tells you a decision EXISTS,
    not that it applies to you; confirm it in your own window (agent-to-agent.md §3, §7.5).
14. **Session roster with worktrees** — one worktree per session (agent-to-agent.md §7.1: the
    single highest-value change, and cheap).

### How it gets kicked off

`dispatch-lane.sh` is the kickoff for one lane. **`scripts/start-program.sh` is the kickoff for
one program**, and it is what turns the items above from prose into state:

```
scripts/start-program.sh <slug> <driver-prompt> <integrator> <session>...
```

It measures how many live sessions share the working tree, cuts a worktree per session, writes
`reports/programs/<slug>/partition.md` (committed, so it binds sessions that were not in the
room), assigns the ring, and prints the exact text to paste into each session. It deliberately
does not message anyone: authorisation is per-session and a script must not appear to grant it.

So the full chain is:

```
/orchestration-prompt  ->  prompts/<slug>.md          (the program prompt; no action)
scripts/start-program.sh  ->  worktrees + partition   (only if >1 driver)
  human pastes kickoff text into each session
each driver  ->  prompts/<slug>-lane-*.md
scripts/dispatch-lane.sh  ->  claude -p in a worktree ->  PR
  integrator verifies independently  ->  merge
```

When emitting a multi-session prompt, include the literal `start-program.sh` invocation in the
prompt's kickoff section, and point every driver at `.claude/rules/agent-to-agent.md` — the
transport, the identity/authority caveat, the epistemic discipline, and the N-party scaling
analysis live there, and restating them in the prompt is the drift shape this repo exists to
remove.

### Cost discipline

The 2026-09-01 CEG program produced 59 commits, 53 lane prompts and ~20 long peer messages in a
day. It was correct — it caught three false claims on one client-facing page — and it was far more
expensive than it needed to be. The waste was concentrated and it was mechanical, not intellectual:

| Sink | What it cost | The fix, now available |
|---|---|---|
| Hand-rolled probes | 9 wrong results; 2–5 calls each to diagnose and redo; one measurement took 8 calls | `scripts/measure.py` — mandatory known-positive assertion, prints the denominator, diagnoses a miss instead of returning empty |
| Re-derived git facts | two-dot vs three-dot diffs, stale HEAD searches, "is this branch really unmerged" | `scripts/gitfacts.sh adds\|landed\|content\|stale\|sessions` |
| Peer status prose | ~20 messages, much of it status | `status/<session>.md` in the program dir; peers **read** state |
| Operator interrupts | ~6 separate asks across two windows | `decisions.md` — one table answered at kickoff |

**Emit these into every multi-session prompt:**

- Point at `measure.py` / `gitfacts.sh` by name and forbid hand-rolled equivalents. An ad-hoc
  one-liner used as evidence never gets the validation a test would get.
- Require a **denominator** with every count. "16 lines", "16 shown heroes" and "89 candidate
  records" are three different answers to what sounds like one question, and conflating two of
  them while holding a green assertion is how a wrong finding reaches a peer's queue.
- Put the **decision budget** in the prompt's kickoff, not in the loop. Authorisation cannot be
  relayed between sessions (`agent-to-agent.md` §3), so every un-batched decision costs one
  operator interrupt *per session*.
- Say explicitly that a second session reviews **conclusions, not measurements**. Re-running a
  peer's greps is the lowest-value work a second session can do, and it is the default thing an
  idle one will reach for.

**The single highest-leverage change is not a rule, it is that the checks became commands.** Nine
probe failures in one day were nine defaults being wrong; a prose rule saying "validate your probe"
was already in force and did not prevent any of them. `measure.py` refuses to emit results at all
unless a known-positive assertion passes — the constraint that replaces the reminder.
<!-- END: tapps-skill-asset -->
