---
name: orchestration-prompt
user-invocable: true
model: claude-sonnet-5
description: >-
  Generate a ready-to-run orchestration PROMPT: a verifiable Goal, a bounded loop,
  and an independent creator-verifier pass. Refuses foggy Goals — redirects to
  /tapps-wayfind. Use whenever the user wants to orchestrate multi-step, multi-repo,
  autonomous, or recurring work — "create a prompt to…", "orchestrate…", "make a
  goal for…", "work the backlog", "loop until X" — even if they don't say
  "orchestrate".
argument-hint: "[free-form objective]"
---
<!-- BEGIN: tapps-skill orchestration-prompt v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

# orchestration-prompt

You produce **prompts, not actions**. The output is a self-contained orchestration
prompt (a markdown file under `prompts/`) that the user — or a Routine, or a `/goal`
run — executes later. You write the *loop*; you do not run it.

## Terminal contract (hard stop — read before anything below)

**This skill AUTHORS a prompt. It never implements the work the prompt describes.** A
run terminates at exactly two things: a markdown file under `prompts/` and one fenced
launch block printed to the user (Output step 9). Branches, edits, dispatches, commits,
PRs and tracker writes that belong to the objective are the *runner's* job. Producing
any of them means this skill failed, however good the work itself was.

The input is always work-order-shaped — "orchestrate the burndown", "work the backlog",
"ship the epic" — so the shape of the sentence is never authorization to do the work. A
project autonomy rule that says to treat the request as standing authorization for every
step authorizes you to **write the prompt without asking**; it does not widen the scope
from authoring to implementing. Autonomy is about not pausing, not about scope.

**The only writes you may perform** are `prompts/<slug>.md`, the optional companion
`.claude/workflows/<slug>.js`, and `learnings.md`. Any other file touched on disk is a
defect, and Output step 7 checks for exactly that.

**Cargo convention.** Much of what follows is *cargo*: second-person text destined for
the emitted prompt and addressed to **its runner**, not to you. Every cargo section
opens with a `> **CARGO` marker line. When a cargo sentence says "decide and act on
every reversible, in-scope step", it is telling the runner to do that. Unmarked text is
method — addressed to you, the authoring session. If a second-person instruction is not
under a `> **CARGO` marker, it is for you; if it is, it is freight.

## Why this exists

The leverage is in the loop's shape — goal, termination, verification, model tier
per step — not in phrasing. A well-shaped harness lets a cheaper model match a
frontier one on verification-friendly work. Every prompt rests on nine load-bearing
parts; miss one and the loop never terminates, terminates without finishing, trusts
self-report, invents a Goal under fog, or can't be cold-started.

## The method

Nine load-bearing parts, each independently verifiable — miss one and the loop
never terminates, terminates without finishing, trusts self-report, invents a
Goal under fog, or can't be cold-started. This is the index; the full
elaboration of every part below, the derived-state coupling test, the
context-lifecycle recycle cycle, and the cold-start preflight checklists live
in `references/method-detail.md` — read it before drafting a Goal or a Loop.

0. **Wayfind fog preflight.** Refuse to invent a Goal while the route is
   foggy — redirect to `/tapps-wayfind`. Decide / map / research-to-decide
   chunks are fog; execute / verify / fix / research-to-execute chunks are
   this skill's.
0b. **Harvest standing constraints** before shaping the goal — each becomes a
    Guardrail *and* an Autonomy hard-stop, or the goal is satisfiable by
    violating it.
0c. **Research preflight** before design choices — `tapps_lookup_docs` then
    `tapps_research` then raw web, dispatched to subagents and never read
    directly into the authoring context.
1. **Pin the Goal** to a verifiable, demonstrable done-condition, anchored to
   ground truth, with at least one clause where a count must not shrink.
2. **Decompose** a large goal into sequential sub-goals; a validation
   contract precedes execution sub-goals whenever the goal changes software
   behavior.
3. **Map each chunk** to a plane, a mechanism, and a model tier. The top
   session dispatches, adjudicates verifier verdicts, and checkpoints — it
   does not do the work. Target under 15% of run tokens for the orchestrator.
   Full intent → mechanism → model-tier tables: `references/claude-feature-map.md`.
   **Surface is a separate axis from plane** — authoring surface (a template or
   generator constant, shipped by regenerating) versus runtime surface (a live
   loop or process, shipped by restarting it) — never reuse "plane" for it; name
   each sub-goal's surface and deploy channel, and treat a substrate shared
   across surfaces as additive-only until every consuming path is verified.
   Full elaboration: `references/method-detail.md`.
4. **Write the loop** with termination + guardrails: state → decide →
   execute → verify → record → repeat or stop.
5. **Add an independent verification pass** (creator ≠ verifier), tiered by
   proof shape — never uniformly frontier.
6. **Make it cold-start runnable** — a brand-new session runs it with zero
   hand-holding; Sub-goal 0 self-heals every precondition, never a "set this
   up first" note for the user.
7. **Context lifecycle** — recycle at every sub-goal boundary: handoff →
   re-verify → clear → continue, never growing one context to the finish.

## Field rules, rulings, and verification routing

Postmortem-derived rules that govern whether a *proof* is sound live in
`references/field-rules-and-rulings.md` (twelve field rules plus eight
rulings — including a no-silent-scope-creep carve-out naming exactly two exception categories, data-loss and security, reported loudly in the evidence block rather than filed and walked past — that pin edge cases the proof-shape table doesn't spell out on its
own). Rules governing *who* runs verification, over what population, and how
its result gets reported — as distinct from whether the proof itself is
sound — live in `references/verification-routing.md`. Read both before
writing a Guardrails or Loop section for an emitted prompt.

## Guardrails, contracts, and cargo text

Every emitted prompt must carry a fixed set of guardrails — termination,
independent verification, standing constraints, no-green-by-deletion,
artifact identity, execution-path proof, driver discipline, tiering by
question shape, context lifecycle, scope, memory, and a required
lessons-learned pass — plus the Autonomy contract, Failure-handling
protocol, Expected-fail fix loop, and Engineering-discipline text that ride
along with them. The full list and cargo text (each marked `> **CARGO`, for
the emitted prompt's runner, not for you) is
`references/guardrails-and-contracts.md`. Fill Output step 4's template
from that list; do not freehand a shorter one. Test scope is part of that
list: a per-sub-goal verifier's charge sheet is scoped to the diff audit, the
sub-goal's proof artifact, its new or changed test files, and a
`--collect-only` enumeration — bulk suite re-runs are excluded there and
reserved for the single end-of-program regression proof.

## Output

1. **Fog preflight (method §0).** If foggy, refuse and point at `/tapps-wayfind` —
   do not emit a prompt. If clear, recall `memory_group=wayfind` resume when present.
2. Read `references/host-feature-map.md` when the runner host is Cursor or when Run-as / checkpoint lanes differ by host. **Refuse to emit a prompt whose Run-as names only one execution home.** Every emitted Run-as names both the in-session runner (this session edits directly) and the orchestrator-driven dispatch-lane home (a `claude -p` lane in its own worktree that ends in a `LINEAR EVIDENCE` block and a PR, with verify/merge/tracker-write retained by the dispatching orchestrator) — a single-home Run-as silently picks a default the runner never chose.
3. Read the workspace manifest (e.g. `fleet.md`) for the repos / Linear projects /
   brain ids involved, if the project has one. **The manifest is a registry, not a
   scope grant** — it can list far more repos than this session's actual workspace
   directory list has open. Treat a manifest row as a candidate to confirm against the
   open workspace, never as authorization by itself.
4. Fill `assets/prompt-template.md` — keep only the sections the task needs. Always
   keep **Prerequisites / Wayfind gate**, the **"How to run (cold start)"** block,
   **`## Driver discipline`** with its Owner-column Plane map and
   **`### Parallel wave schedule`**, the **`## Parallelization plan`** that says which
   lanes are serial and why, a
   **Sub-goal 0** for self-healing preconditions (checklists:
   `references/cold-start-and-verify.md`), the **Verify** step wired to an
   independent verifier, the **Lessons learned** section with its REQUIRED final
   sub-goal *and* its Done-when clause, and — when changing software behavior — a
   **Validation contract** filled *before* execution sub-goals plus an
   **expected-fail fix loop** with attempt cap. Drop the template's
   **artifact-identity** Guardrails bullet only when the loop produces no artifact a
   human or customer will look at; drop the **execution-path proof** Guardrails
   bullet only when the change's producer and consumer are the same checkout.
5. If any chunk is multi-stage parallel work, also write the companion
   `.claude/workflows/<slug>.js` (schema + `budget` + per-stage `model`/`effort`) and
   point Run-as at it. A single coupled item (N=1) is a `/goal` drive, not a Workflow.
6. Save the prompt to `prompts/<short-slug>.md`.
7. **Completeness self-check** — walk the **Guardrails** list above and confirm the
   emitted prompt satisfies every line; then run the **cold-start test** (a fresh
   session with nothing loaded can run it). Fix anything weak before saving. Run
   `node scripts/check-prompt-shape.js prompts/<slug>.md` and, when the program
   carries a `learnings.md`, `node scripts/check-learnings-size.js learnings.md` —
   fix whatever either names before saving.
   **Context lifecycle is checked explicitly**, because nothing else catches its
   absence: confirm the prompt names a context boundary per sub-goal (or says which
   sub-goals skip it and why), that the boundary carries the re-verify gate, and that
   the autonomous run shape is named as one `claude -p` per sub-goal rather than a
   `/clear` the loop cannot invoke. A template supplies the boundary by default, so a
   prompt that quietly drops it looks finished — this is the one guardrail whose failure
   mode is silence.
   **Then assert no files were written outside `prompts/<slug>.md`, the optional
   `.claude/workflows/<slug>.js`, and `learnings.md`.** A stray branch, edit or commit
   means the terminal contract was broken and the run is a failure whatever the prompt
   scored.
8. Tell the user exactly how to run it — the `/goal` line, the `/loop` cadence, the
   Routine schedule, or "invoke the Workflow tool `<script>`" — and from which
   session.
9. **Launch block — REQUIRED, and the last thing the run produces.** Print exactly one
   fenced block and nothing after it. It carries a concrete `/model` and a concrete
   `/effort` — real values, never placeholders, because the runner otherwise inherits
   whatever the pasting session happened to be set to — and a line that reads the prompt
   file *before* looping, since `/goal "<condition>"` does not load the file's body:

   ```text
   /model sonnet
   /effort medium
   Read prompts/<slug>.md in full, then execute it as a goal loop from <cwd>: run the
   Loop section once per iteration, print the SCORE line every iteration, establish
   your own preconditions per Sub-goal 0, and stop only when Done-when holds or an
   Autonomy hard-stop fires.
   ```

   Then stop. Do not create a branch, dispatch a lane, or start Sub-goal 0 yourself —
   that is the terminal contract, and this block is where the skill ends.

## Learn as you go, and multi-session programs

Two more references round out the method. The `learnings.md` protocol — what
to mine, when to write it (twice: at generation time and at the end of every
run), and how to keep the file readable (the byte ceiling is the binding one;
bullet count alone is misleading, since a handful of long bullets can blow the
byte budget while staying under the bullet ceiling) — is
`references/learnings-protocol.md`. Programs run by more than one
interactive driver session — partition, integrator, review ring, the
authorisation clause, the
`scripts/start-program.sh` kickoff, and the 2026-09-01 cost-discipline
findings — are `references/multi-session-programs.md`; read it only when the
work has an irreducible need for a second driver
(`.claude/rules/agent-to-agent.md`).
<!-- END: tapps-skill -->
