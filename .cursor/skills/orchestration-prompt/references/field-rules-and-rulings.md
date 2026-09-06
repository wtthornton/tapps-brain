<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->
<!-- BEGIN: tapps-skill-asset orchestration-prompt/references/field-rules-and-rulings.md v3.12.83 -->
# Field rules and rulings

Read while filling Guardrails, the Validation contract, or the Plane map. Twelve field rules distilled from postmortems of this skill's own emitted prompts, followed by eight rulings that pin edge cases the proof-shape table (`references/method-detail.md` §5) does not spell out on its own.

## Field rules

Twelve rules distilled from postmortems of this skill's own emitted prompts. Follow
each — they are not optional flavor text.

1. **Validate the instrument on a known-bad and a known-positive before trusting its
   verdict.** Method §6 preflights that a *mechanism* executes; nothing preflights
   that a *judgement instrument* — a verifier, linter, or scorer — actually
   discriminates. Before trusting a verdict, run the instrument once against a
   known-bad input and once against a known-good input and confirm it tells them
   apart. An instrument that passes everything (or fails everything) is a silent
   rubber stamp, not a check.
2. **Green-by-citation is distinct from green-by-suppression.** A cited source ("per
   the docs…") can be just as unearned as a deleted test if the citation is not tied
   to the claim it is supposed to establish. Every citation is quoted beside one
   sentence naming the exact proposition it establishes — a citation with no adjacent
   claim is decoration, not evidence.
3. **The verifier's control is the pre-change tree, not the fix's own tests.** A fix's
   own test suite is not a control group — it was written by the same actor with the
   same blind spots. Run the fix's proof against the unpatched tree and confirm it
   fails there; a proof that never ran against a failing baseline proves nothing about
   whether the fix did anything.
4. **A merge-gating verifier reports the PR's own CI by name and state, and re-runs
   the CI job's own command.** When a verdict gates a merge, name the actual CI
   check(s) on that PR and their actual state — not a locally-run proxy — and re-run
   the CI job's own command rather than an invented equivalent. A local pass that
   diverges from the CI command is not evidence the gate will pass.
5. **A measured number is a floor until the instrument is proven able to express it.**
   A wrapper script or CLI flag can silently discard the value it claims to report (a
   `--json` flag ignored, a count capped by a page size). Treat every measured number
   as a floor, not a fact, until the instrument is confirmed to express the true
   value — "0 failures" can mean "zero were counted", not "zero exist".
6. **Prove freshness per deployed layer and diff config per key hash; treat every
   deployment fact as point-in-time.** A multi-layer deploy (image, config map,
   running container, edge cache) can have one stale layer while the others are
   current — freshness is proven per layer, never once for the whole stack.
   Configuration is diffed by hashing each key, not by eyeballing a diff. No
   deployment fact survives past the moment it was checked.
7. **Run a blast-radius preflight before any state-touching verify step.** A command
   that reads as inert ("just checking the count") can still mutate or destroy state —
   a dry-run flag that is not actually a no-op, a script with a side-effecting import.
   Before running a verify step against live state, name what it could destroy and
   confirm the command is inert, rather than assuming from its name.
8. **A return schema separates queried-and-got-zero from the-query-failed;
   identifiers are resolved live at Sub-goal 0.** "Zero results" and "the query
   errored" are distinguishable fields in a return schema, never collapsed into one
   falsy value — a caller that cannot tell them apart treats a broken query as a
   clean negative. Identifiers (issue ids, repo paths, image tags) are resolved live
   at Sub-goal 0, never hardcoded from a stale prior run.
9. **Round-2 fix prompts gate on the delta and also sweep siblings by symbol.** A
   second-round fix sub-goal proves the specific delta the verifier flagged, and
   separately greps for other call sites of the same symbol or pattern — a bug fixed
   at one call site and left in three siblings is how a round-2 verify still turns up
   a fresh, different failure.
10. **A successor to a partially-failed program needs a disposition disjunction with
    a numeric floor and an anti-escape guard.** When a prior run stopped short, the
    next prompt's Done-when states an explicit disjunction of acceptable dispositions
    (e.g. "fixed OR cancelled with a written reason"), each with a numeric floor (N of
    M resolved), plus a guard against the trivial escape of cancelling everything to
    make the count balance.
11. **Agreement among artifacts is not corroboration — read the component with
    authority.** Two documents, dashboards, or logs that agree can both be downstream
    copies of the same stale source rather than independent confirmations. When a
    claim matters, read the component that actually has authority over it (the
    running config, the source serializer, the database row), not the artifact that
    merely displays it.
12. **A dispatched headless lane's structural limits are the author's problem,
    including that it dies when it returns.** A `claude -p` lane or a subagent that
    has returned cannot be polled, resumed, or asked a follow-up — and it cannot
    background work across its own return without losing it. Design the dispatch so
    the lane's own return is the last useful signal it gives; never assume a lane can
    pick back up after the dispatching call returns.

## Rulings

Verifier-tier guidance (method §5) is authoritative — see above. These eight rulings
resolve cases the proof-shape table does not spell out on its own.

1. A refuter may author a narrow fix and stay on as re-verifier while it owns the live
   repro, without weakening creator ≠ verifier before merge — the point of the rule is
   a fresh, adversarial perspective, not a fresh identity, and the agent already
   holding the live reproduction is best placed to confirm a scoped fix without
   re-establishing context from zero.
2. No-silent-scope-creep carries a carve-out naming exactly two exception categories, data-loss and security — a delegate may step outside its named scope only to stop in-flight
   data loss or a live security defect, and the carve-out is void the moment it is
   silent: acting outside scope is legitimate only if it is surfaced loudly in the same
   evidence block, never filed and walked past, never discovered later in a diff. An
   ordinary adjacent problem that is neither data-loss nor security still routes to a
   separate item, with no change in behaviour; the carve-out names these two categories
   and stops there — it is not a general licence to widen the diff. This carve-out is
   lane-level and in-flight only: a filed finding's admission into the current run
   (Urgent-or-High, driver-announced) is a separate mechanism, below.
3. Shared quota is a coupling the independence test (method §3) must see. Two lanes
   with disjoint file lists can still contend for the same rate limit, API quota, or
   worker pool — that is a derived-state coupling exactly like an env-var set, and it
   forces the same `order-forced-by` treatment: a fan-out and the lanes beside it may
   need sequencing, not just disjoint paths.
4. Billing topology — which account or budget a dispatch's spend lands against — is
   frequently unresolved. Probe it at Sub-goal 0, as a live check, never cite it in a
   prompt as a known fact until it has been probed for that run.
5. Content-diff freshness (a built artifact's content hash vs source) is necessary but
   not sufficient — see method §6's stale/divergent distinction. It is repeated per
   deployed layer, never asserted once for a whole stack, and it expires: a freshness
   check from an hour ago is not evidence for the current run.
6. Cheap-tier transcription (method §5's `haiku`/`low` row) is reliable only when the
   return schema carries keyed pairs — `{name: value}` — never two parallel lists
   (`names: […]`, `values: […]`) the reader must zip back together by position. A
   cheap model transcribing two lists can silently misalign them; a keyed schema makes
   that structurally impossible.
7. On visual/UI work, one named artifact handover to the operator — a screenshot, a
   rendered page, a design-canvas link — is allowed before the verification tail
   spends its budget, so a human sees the actual visual result once early rather than
   only after several rounds of automated verify already ran. This is a single named
   handover, not a standing checkpoint.
8. The word "plane" is reserved for the coordination-versus-execution distinction
   (method §3). Do not reuse it for the build-time-versus-runtime distinction — use
   "surface" there instead ("build surface" vs "runtime surface"), so a reader can
   rely on "plane" meaning one specific thing throughout an emitted prompt.

## Rulings folded from a consuming project's local region

Five rulings nlt-orchestrator carried in its own local region below this skill's
managed block — folded here (TAP-7078 box 5) so an upgrade absorbs them instead of
leaving them to silently re-diverge every time the block refreshes.

9. A driver that merges, deploys, installs, or scopes a fix from a RED verdict is above
   the `sonnet`+`medium` floor by construction. The floor is for read/triage-only
   drivers; a driver-row that merges, deploys, installs, or scopes a fix runs at
   `opus`+`high`, and a driver-row contesting identity (whose session actually sent a
   message) runs `fable`/`opus` at `high`-`xhigh`.
10. Input is an existing PLAN with an evidence file → §0c is already done; cite it, don't
    redo it. When the request names a `reports/<program>/PLAN*.md` backed by a review or
    STATE file: derive `## Unverified assumptions` from that file's stated non-verified
    claims, cite the evidence file by path, and run the `tapps_lookup_docs` calls the
    lanes will need into a `/tmp` docs file the briefs may read (lanes have no MCP) — or
    state in the Research grant that no external library API is written against.
11. After a `/clear`, every unattributed artifact in the tree is possibly your own —
    and `ListAgents` absence is not authorship. Before naming an author, compare the
    `from=` socket path on your own incoming and outgoing messages with the session
    you are about to name; one socket is one process regardless of what the context
    remembers.
12. Two effort knobs. The Plane map's `effort` column is Workflow `opts.effort`; a
    lane's effort is `dispatch-lane.sh`'s fourth argument; an Agent-tool subagent has
    neither. Say which a cell means. A prompt that does not name its brief files has
    lanes nobody can dispatch, and the shape check requires the `## Lane briefs`
    table.
13. `learnings.md` is read by an extractor, not in full; its ceiling is a check. The
    managed "Read `learnings.md` before drafting" contradicts the delegation
    doctrine at this file's size. Dispatch `Explore` + `sonnet` with the program's
    shape and a 40-bullet cap; fold the struct. `node scripts/check-learnings-size.js`
    owns the ceilings (bullets, bytes, bytes-per-bullet, and the trailing-date house
    style).
<!-- END: tapps-skill-asset -->
