<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->
<!-- BEGIN: tapps-skill-asset orchestration-prompt/references/verification-routing.md v3.12.83 -->
# Verification routing and honest reporting

Ten rules promoted from a consuming project's local region, where they were working and reaching nobody else. `references/field-rules-and-rulings.md` is about whether a proof is sound; these are about who runs it, over what population, and how its result gets reported.

## Verification routing and honest reporting

Ten rules promoted from a consuming project's local region, where they were working and
reaching nobody else. The Field rules above are about *whether a proof is sound*; these
are about *who runs it, over what population, and how its result gets reported*.

1. **Route a verifier by the permission its proof needs — a third axis beside proof
   shape and blast radius.** An adversarial brief that says "break the code and count the
   failures" cannot run on `Explore`: it is read-only, so `git init`, `git worktree add`,
   a scratch commit and every temporary mutation are refused. The agent behaves correctly
   and fabricates nothing — it reports the write-requiring steps UNVERIFIED — so a whole
   verification round buys static analysis instead of the mutation evidence that was
   asked for. Mutation tests, negative controls and scratch-repo reproductions need
   `general-purpose`; `Explore` stays the default only for genuinely read-only proofs.
   This is the routing axis whose failure returns a *non-answer*, so state the proof's
   write needs in the dispatch alongside `agentType` and `model`.
2. **Dry-run every string a verifier will execute, on the target tree, before the
   verifier launches — an amendment is a proof command too.** A proof command that is
   wrong about reality (a path that does not exist in that worktree, a venv binary in a
   venv-less tree, a summary table the page never had) makes the verifier report RED
   honestly, which is the right failure mode and still costs a whole fresh-context round.
   A clause *appended* to an already-verified proof row is a new command and gets the same
   dry-run. Every numeric floor also names the artifact it is counted from. Three riders
   on Workflow spend: a cached resume replays results keyed on (prompt, opts) and is blind
   to repo state, so any stage reading mutable state is re-launched fresh rather than
   resumed; guard the cheap pre-stage of an expensive gate, or a pre-stage failing for
   environment reasons silently cancels the stage that was the point; and a *mechanical*
   merge gate needs no fresh context at all — `git range-diff` printing `=` proves a
   rebase patch-identical for a few hundred tokens where a two-agent verification round
   costs six figures. Reserve fresh contexts for reads that actually need independence.
3. **Scope verification to the artifact, not to the diff.** Every mechanism in a program
   scoped to *change* is structurally blind to a falsehood already on the main line: a
   claim that contradicts the record beside it can survive round after round of review,
   because every reviewer was scoped to the diff and nobody was ever asked *is what is
   already here true?* A clean identity read is evidence about what the reader looked at,
   never proof of absence. Attribute a defect with a content search over history (`git
   log -S` on the string), never from the most recent nearby merge.
4. **Give every cross-cutting claim exactly one owner.** Per-artifact ownership makes
   cross-artifact truth nobody's job — splitting findings per page and fixing each page
   against its own record produces a second round whose findings are almost entirely
   *between* the pages. Either one lane owns a **claim** across every artifact that makes
   it, or the shared fact moves into one record the artifacts derive from. Scope
   owner-facing lanes by **what the recipient actually opens** (the zip, the PDF inside
   it, the email), not by file ownership: enumerate the shipped manifest first and make
   it the lane's file list. And a lane whose evidence runs a tool it does not own
   *reports* the failing line — it does not edit the tool, or two lanes fix the same
   shared bug two different ways and the fold conflicts irreconcilably.
5. **"Disjoint files" is measured, not argued.** The derived-shared-state test (§3) is the
   sophisticated half of the independence question and it can be right while the trivial
   half was never checked at all — a plan can correctly serialise one lane behind a shared
   derived set and, in the same paragraph, call two others "disjoint files *and* disjoint
   derived sets" when both edit the same module and both append to the same test file.
   Intersect the intended file lists mechanically before fanning out and record the result
   in the Parallelization plan. An elaborate dependency argument is not evidence that
   anyone ran the simple check.
6. **Prose is the unguarded surface — and a prose rule beside the code it governs does not
   stop the code.** The defects that survive their author's own review are overwhelmingly
   *prose*, and the code beside them is usually correct, which is exactly why nobody looks:
   a comment asserting that a dry-run previews what the real run does, when it compares
   pre-change state; a runbook naming a file that does not exist; a generator comment
   naming a failure mode precisely, a few hundred lines above the shipped instance of it.
   None is reachable by any test. Two consequences. Prose can assert a *consumer* that was
   never built, which makes an unshipped feature read as shipped and leaves every artifact
   agreeing about it — so grep for the reader, not just the writer. And where a preview and
   a real run must agree, **assert that they are equal**, never that both were "computed by
   the same logic": the latter is satisfiable by calling the right helper on the wrong
   state, which is the bug it was meant to exclude. Whenever you are about to add a standing
   constraint to a prompt, ask first whether the *dispatcher* could refuse the thing
   mechanically — an injected rule is still a reminder, and reminders lose to defaults.
7. **Never read tracker state as evidence that work happened.** An integration can write
   it: merging a PR whose title carried an issue id has auto-completed that issue seconds
   later, `completedAt` matching the merge, with most acceptance boxes unticked and no
   agent or human write behind it. Keep ids out of PR titles and branch names and put them
   in the body; make "is this PR attached to that issue?" a **pre-merge** check; re-read
   every issue that must stay open after every merge. The claim runs both ways — a
   prompt's own summary of tracker state is a handoff claim, not a fact, so a prompt that
   restates tracker state says so in the same breath. Close an issue by ticking each box
   with its evidence pointer, or leaving it unticked and saying in the body why:
   unticked-and-silent is the only version that is not honest.
8. **"Blocked" is a first-class lane outcome — say so, or lanes optimise for the number.**
   A lane that cannot clear a gate honestly, refuses to bypass it, and reports blocked with
   a diagnosis has usually located a real defect in the *gate*. A prompt silent on this
   reads as "return green", which is an instruction to suppress. State explicitly that
   blocked-with-a-diagnosis is a fully acceptable outcome, and that the diagnosis is the
   deliverable in that case.
9. **Read the spec adversarially before you read the code: could an implementation tick
   every box and leave the defect live?** Ask it of the *specification*, deliberately
   without reading the implementation. Reading the code finds one bug; reading the spec
   finds the generator of bugs. This is the emission-time twin of §1's must-not-shrink
   clause — both ask what a green run could look like while the goal is still unmet.
10. **Enforcement before remediation deadlocks; ship the ratchet instead.** An absolute
    per-file threshold fails any change touching a legacy file *including one that improves
    it*, so the only ways past are an override or an unrelated refactor — and a rule
    obeyable only by bypassing it enforces nothing. The ratchet is strictly harder to cheat
    than the flat bar: new files are never grandfathered, a passing file may never fall
    below the bar, only an already-under file gets the decrease-only test, and an
    unscoreable baseline falls back to absolute — unknown refuses, it never skips. Two
    riders: wire it into **every** enforcement point at once (landing it in CI but not the
    local hook just moves the deadlock one layer down), and **track the ratcheted
    population**, or the exemption becomes permanent.

**The identity read is a SEND gate, not a merge gate.** This amends the
artifact-identity guardrail below. An open-ended "would we ship this to the customer"
read re-reviews from scratch, so its bar moves every round and it never converges — it
can refuse a merge three rounds running, each time on real but *new* items, while
blocking strict improvements to something nobody sees until the outward step. **Merge**
on deterministic verification plus integration floors plus a post-merge live re-fetch;
run the expensive identity read **once**, immediately before the outward step it actually
protects. The two decisions have different blast radii and different convergence
properties, and conflating them turns an attempt cap into a wall. Note also what the
sibling gates cannot see: an integrity check proves the artifact was *not altered*, which
is exactly why it passes an artifact that is the wrong thing rendered faithfully.
Fidelity and identity answer different questions.
<!-- END: tapps-skill-asset -->
