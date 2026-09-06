<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->
<!-- BEGIN: tapps-skill-asset orchestration-prompt/references/guardrails-and-contracts.md v3.12.83 -->
# Guardrails and cargo contracts

The full Guardrails-every-prompt list, and the Autonomy / Failure-handling / Expected-fail-fix-loop / Engineering-discipline cargo text that rides along with it. Every `> **CARGO` marked section is text for the emitted prompt, addressed to its runner — not an instruction to the authoring session (see the Terminal contract in `SKILL.md`).

## Guardrails every emitted prompt must carry

> **CARGO — text for the emitted prompt, addressed to its runner.** Not an
> instruction to you, the authoring session (see Terminal contract).

- **Verifiable termination** — the Goal condition *and* a hard cap (max iterations
  or a token budget) so a stuck loop stops instead of burning quota.
- **Independent verification** — the sub-goal's proof is confirmed by a verifier that
  did not produce the work (method §5), handed the *proof command* rather than the
  claim, against ground truth. Its tier follows the **proof-shape table** (method §5)
  rather than a uniform frontier default, and its verdict schema carries
  `observed_output` (empty = FAIL) and `green_by_suppression`; cheap-tier verdicts are
  adjudicated on `observed_output`, never on the conclusion sentence.
- **Standing user constraints** — every one restated as a Guardrail *and* an Autonomy
  hard-stop (method §0b); no Done-when clause is satisfiable by violating one.
- **No green-by-deletion** — at least one Done-when clause is a count that must not
  shrink, so the goal cannot be met by removing what is measured (method §1).
- **Artifact identity, not just validity** — gates check form only (schema, exit code,
  geometry, provenance, signature) and will happily pass an artifact that is the wrong
  *thing* entirely. Every emitted prompt whose loop produces something a human or
  customer will look at needs one delegated step — named `agentType` + `model=opus`
  and tiered as open judgement rather than a closed check — that opens the artifact
  and answers *is this the thing that was asked for*, in words. Drop this guardrail
  only when the loop produces no artifact a human or customer will look at.
- **Execution-path proof before "this change takes effect"** — name the file, the
  checkout it resolves from, and the revision the consumer loads, then prove it with a
  marker check against that exact file — never a merge SHA or a branch name alone.
  Merging to a default branch is not the same as the consumer seeing it: a consumer
  can load a stale checkout, or one on a different branch, that never sees the merge.
  Forbid delegates from locating the tool by filesystem search — pin the path and
  hard-stop on mismatch. Drop this guardrail only when the change's producer and
  consumer are the same checkout.
- **Driver discipline — the orchestrator dispatches, it does not execute** (this is
  the Orchestrator-discipline guardrail; the emitted prompt carries it as the single
  required `## Driver discipline` section). The top session decides what to dispatch,
  dispatches, adjudicates verdicts, makes the gated or plugin-only calls a delegate
  cannot reach, and checkpoints. It edits no files, runs no builds, runs no probes,
  tails no logs, and gathers no per-iteration state. Every Plane-map row whose Owner is
  not `driver` is delegated, `orch-spend` stays under 15%, and the two detectors
  (method §3) have been run against the map.
- **Every dispatch carries a return schema** alongside `agentType` + `model` — a
  schema-less dispatch comes back as prose the driver must re-read, spending exactly
  the tokens the delegation was meant to save.
- **Test scope — no regression or full-suite run until the plan is complete.** Per-item
  proof runs **only the tests the change adds or touches**, with the command and its
  exit code pasted. A whole-suite run proves nothing that item owns, and on a large
  suite it approaches the wall-clock ceiling that kills a headless lane outright. One
  full **enumeration** per wave is enough to catch a collection error (a
  `--collect-only` count, not an execution), and exactly one regression run at program
  end, after the plan is complete — that run is the operator's call, not a per-item
  step.
- **Tier by question shape, not importance** — closed and evidence-checkable (line
  counts, string presence, exit codes) goes cheap *even at high stakes*; open judgement
  gating an irreversible step goes frontier *even when it looks small*. Defaulting
  everything to frontier is the expensive failure this rule exists to stop.
- **Dispatch each wave in full before polling it** — independent chunks grouped into a
  `### Parallel wave schedule`, with the constraint that actually binds stated (usually
  one working tree per repo). Serialising independent lanes buys no safety and costs
  wall-clock.
- **Every subagent dispatch names `agentType` + `model`** (and `effort` when it runs
  in a Workflow) — never "spawn an agent to…". Read-only work uses `Explore` so the
  tool boundary, not the prose, enforces it. No cheap-model verdict gates an
  irreversible step; load-bearing answers are re-derived from returned evidence.
- **Research grant** — every emitted prompt states that the loop has web access,
  `tapps_research` and `tapps_lookup_docs` (Context7-backed, local-cache-first, so
  effectively free to repeat), and **names the specific lookups required before the
  first line of code touching an external API**. A loop that writes against a
  versioned external surface from recalled syntax will hallucinate a schema that lints
  clean and fails at runtime. Research-to-*execute* is in scope; research-to-*decide*
  still goes to `/tapps-wayfind`.
- **Caps must not fire on *correct* behavior** — for every required-fail cap, ask "is
  there a legitimate correct run where this still fires?" Separate *broken* from
  *correct-empty* (the gate rightly held everything) or a correct negative scores red.
- **Terminal lessons-learned pass** — every emitted prompt ends with a REQUIRED final
  sub-goal that mines the run and appends to `learnings.md`, plus a Done-when clause
  gating on it. Without a clause in Done-when it is advisory, and an autonomous loop
  drops advisory work the moment the real goal goes green — which is exactly when the
  lessons are freshest. It is the one sub-goal that survives trimming. Point it at what
  an independent verifier *refuted* first: that is the run's densest source of
  transferable lesson, because each item is something the loop believed and got wrong.
- **No fan-out of coupled coding** — parallel agents editing related code cascade
  errors; keep code edits sequential, per repo.
- **Parallel where independent, serial where coupled** — lanes that share no derived
  state fan out and dispatch to the background at iteration 1; the moment one lane reads
  a set another lane writes, they serialise and the emitted prompt names that set in the
  Parallelization plan's `order-forced-by` field. Disjoint file lists are not evidence of
  independence (method §3) — the coupling that fails silently is the one where each half
  is internally consistent.
- **Concurrent writers — a running loop is never the only writer.** Shared scripts, git
  config, and temp directories may change under a running loop — another session,
  another lane, or an operator can edit `scripts/`, rewrite `.git/config`, or clean
  `/tmp` while this loop is mid-run. Record the **version of any shared tool actually
  used** (its printed `--version`, a content hash, a resolved path) rather than
  inferring it from documentation that may already be stale for this run. Every lane
  copies its own log out of the temp directory on completion, before the directory can
  be reused or cleaned by something else. **Gate any corrective git command on a
  re-observation, never on a single status snapshot** — a snapshot taken before a
  concurrent writer's edit is stale by the time the correction runs. The triage order
  before any corrective git action: (1) confirm the files still on disk match what the
  snapshot claimed, (2) confirm HEAD is still the commit the snapshot named intact, (3)
  confirm nothing was pushed out from under this check, (4) confirm the recovery is a
  single command — then **observe again immediately before acting**, because the
  triage itself takes wall-clock time a concurrent writer can fill.
- **Context hygiene** — prune stale reads each iteration; targeted grep over full
  re-Read (method §4).
- **Context lifecycle** — a long loop recycles instead of growing: at each sub-goal
  boundary (or ~50% context, whichever first) `/tapps-handoff-session` → **re-verify** →
  a real clear (subagent / next `claude -p` / operator `/clear`) → `/tapps-continue-session`
  (method §7). Never clear on an unverified handoff — check sha vs `git log -1`, re-read
  named PR/issue state from the tracker, re-read metrics from their newest artifact. One
  runner per handoff file. The handoff carries **cumulative** attempt-count,
  budget-spent, and refuted strategies, or the clear silently resets the caps and the
  loop repeats what already failed. Name the sub-goals where the boundary is skipped and
  why.
- **Autonomy, not checkpoints** — act on every reversible in-scope step; for an
  outward/irreversible step produce a reversible precursor (draft PR, staged diff)
  and keep going.
- **Fog gate** — never invent a Goal while decide work remains; redirect to
  `/tapps-wayfind` (method §0).
- **Scope** — name the exact repos/paths; reads can be fleet-wide, writes go through
  the owning repo's channel. **The session's workspace directory list is the scope
  fence — a fleet-registry row is not an in-scope target by itself**; a manifest can
  list far more repos than this session actually has open. Naming a repo in the
  prompt is inert: the boundary is crossed only when a tool call's *path argument*
  points outside the workspace. Audit by grepping the transcript for path
  **arguments**, never for repo names — a mention proves nothing either way. Every
  fan-out brief names the permitted paths and the dispatched agent's return schema
  reports the paths it actually read, so the fence stays auditable after the fact.
  Out-of-scope work discovered mid-run is a hard-stop to surface immediately, never a
  silent skip.
- **Budget** — every loop carries *both* an iteration cap and a token budget; set a
  Workflow `budget` to a token ceiling (≈ the autonomy cost gate) so it self-aborts.
- **Memory** — recall at the start, record the outcome (incl. failures) at each
  checkpoint, so learning survives the session.
- **Harness compatibility** — every tool call the loop makes that is gated by a
  project hook has its unlock/refresh step in the prompt, and every MCP standing
  nudge is explicitly adopted or overridden (method §6).

## Autonomy contract (every emitted prompt carries this)

> **CARGO — text for the emitted prompt, addressed to its runner.** Not an
> instruction to you, the authoring session (see Terminal contract).

Run like an operator, not an intern. Decide and act on every reversible, in-scope
step — never insert "should I proceed?" checkpoints. For an irreversible/outward step,
produce the *reversible precursor* (draft PR, staged diff, written proposal) and
continue; the human reviews async. A draft PR is not a stop.

Hard-stop and ask **once** (batched, with a recommendation) only when: the step is
irreversible/outward with no reversible precursor (merge to main, force-push, delete
un-recreatable data, external message, cross-project write); **or** the projected
cost of the next step exceeds the configured ceiling (default ≈ USD 20; honor any higher
pre-authorization); **or** a genuinely ambiguous decision where a wrong guess is
expensive and unrecoverable. Enforce the cost gate mechanically via the Workflow
`budget` so the run aborts itself instead of asking.

## Failure handling (diagnose, don't repeat)

> **CARGO — text for the emitted prompt, addressed to its runner.** Not an
> instruction to you, the authoring session (see Terminal contract).

On a failed verify, do **not** re-run the same action. Diagnose first: read the
actual error, inspect state/files, recall prior failures from the brain, research the
cause. Form a specific hypothesis, apply a fix, retry with *something changed*. Bound
it: max **3 distinct strategies** per sub-goal, then escalate once (more capable
model / different approach), then **stop and surface a concise diagnosis**. Repeating
the same action on the same error is forbidden.

## Expected-fail fix loop (Missions-inspired)

> **CARGO — text for the emitted prompt, addressed to its runner.** Not an
> instruction to you, the authoring session (see Terminal contract).

Independent verification **almost never passes on the first attempt** for non-trivial
work. Treat that as the design, not a crisis:

1. **Record a structured handoff** before fixing: what completed, what is undone,
   commands run + exit codes, issues found, whether procedures were followed.
2. **Scope a narrow fix sub-goal** targeting the verifier's actionable gaps — do not
   reopen the whole feature or weaken the validation contract to go green.
3. **Re-execute → re-verify** (fresh verifier context again).
4. **Attempt cap (default 3 validation rounds per sub-goal)** — override explicitly
   in the emitted prompt when needed. After the cap: escalate once, then stop with
   a diagnosis. If the *contract* itself is wrong, stop and ask the human — do not
   silently rewrite Done-when to match the broken implementation.

Infinite fix spirals and "green by suppression" are forbidden.

## Engineering discipline (emit in every prompt's guardrails)

> **CARGO — text for the emitted prompt, addressed to its runner.** Not an
> instruction to you, the authoring session (see Terminal contract).

Produce *solutions*, not band-aids: root-cause not workarounds; **no
green-by-suppression** (never skip/disable a check to pass); **right-sized** (the
simplest thing that fully solves it); durable over expedient; match repo conventions;
no silent scope creep — carve-out for in-flight data-loss and security only, reported
loudly; everything else filed, admission is the driver's announced call.

**Two mechanisms, two actors — do not conflate them.**

- **In-flight carve-out (LANE, immediate).** A lane may step outside its named scope
  ONLY to stop in-flight data loss or a live security defect — the
  data-loss and security pair, and nothing wider — and must report doing so loudly
  in its own evidence block the moment it acts. Everything else it finds, it FILES; it
  does not fix it in flight.
- **Scope admission (DRIVER, announced).** The driver may admit a filed finding into
  the current run as a new lane or VAL only if it is triaged **Urgent or High**, says
  so out loud in the same report that discovers it, and adds it to the SCORE
  denominator so `pct` tells the truth about the larger population rather than
  quietly shrinking its own target. The lane never self-admits.

An adjacent Urgent defect that is neither data-loss nor security is FILED by the
lane and may be ADMITTED by the driver — the lane does not fix it in flight. Everything
below High is filed and left for the operator. What stays forbidden in both mechanisms
is the *silent* version: work that appears in the diff and nowhere in the report.
<!-- END: tapps-skill-asset -->
