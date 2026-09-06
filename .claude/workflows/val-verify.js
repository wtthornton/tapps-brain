// upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
// BEGIN: tapps-skill-asset workflows/.claude/workflows/val-verify.js v3.12.83
export const meta = {
  name: 'val-verify',
  description: 'Independent adversarial VAL verification: per-VAL fresh-context refuters running BOTH a negative and a positive control, a standing-invariant pass not scoped to the diff, then one identity read of the human-facing artifacts bound to the shipping commit and gated on allGreen',
  whenToUse: 'Before authorizing a merge or rollout: pass the VAL rows the change claims, any standing invariants that are not scoped to this diff, the artifacts a human reads, and the commit sha that will actually ship',
  phases: [
    { title: 'Verify', detail: 'one fresh-context verifier per VAL, each running BOTH controls; deterministic -> haiku/low, comparative -> sonnet/medium, semantic -> opus/high' },
    { title: 'StandingInvariants', detail: 'invariants checked against the repo as it stands today, not scoped to this change' },
    { title: 'Identity', detail: 'artifact identity read, bound to args.shipSha, gated on allGreen' },
  ],
}

// args = {
//   worktree: string,                                   // read-only worktree the verifiers operate in
//   shipSha: string,                                     // the commit that will actually ship -- identity
//                                                         // reads THIS, not whatever HEAD drifted to mid-run
//   vals: [{ id, kind: 'deterministic'|'comparative'|'semantic', assertion, proofCommand,
//            negativeControl, positiveControl }],
//   standingInvariants: [{ id, kind, assertion, proofCommand, negativeControl, positiveControl }],
//                                                         // NOT scoped to the diff -- "is what is
//                                                         // already here true", independent of this change
//   identityArtifacts: string[],                          // human-facing artifacts to read at shipSha
// }
// `args` must be a real JSON object -- prose here yields `undefined` in every prompt.

const WORKTREE = args?.worktree
const SHIP_SHA = args?.shipSha
const vals = Array.isArray(args?.vals) ? args.vals : []
const standingInvariants = Array.isArray(args?.standingInvariants) ? args.standingInvariants : []
const artifacts = Array.isArray(args?.identityArtifacts) ? args.identityArtifacts : []

if (!WORKTREE) return { error: 'args.worktree missing -- the verifiers must read a specific tree, not guess one' }
if (!SHIP_SHA) {
  return {
    error:
      'args.shipSha missing -- the identity phase must be bound to the commit that will actually ' +
      'ship, not whatever HEAD happens to be when the read runs. On 2026-09-01 four identity reads ' +
      'ran and the last covered the wrong sha while a different one shipped -- a scheduling defect ' +
      'this argument exists to close.',
  }
}
if (!vals.length && !standingInvariants.length) {
  return { error: 'both args.vals and args.standingInvariants are empty -- nothing to verify' }
}
const uncontrolled = [...vals, ...standingInvariants].filter((v) => !v.negativeControl || !v.positiveControl)
if (uncontrolled.length) {
  return {
    error:
      `these checks are missing a control: ${uncontrolled.map((v) => v.id).join(', ')}. Every proof ` +
      `needs BOTH a negative control (must FAIL on a broken input) and a positive control (must PASS ` +
      `on a known-good one) -- a check that fires on everything is as useless as one that fires on ` +
      `nothing, and a selector matching zero targets prints what a clean result prints.`,
  }
}
if (budget.total && budget.remaining() < 60_000) {
  log(`insufficient budget (${budget.remaining()} left) -- aborting`)
  return { aborted: true, reason: 'budget' }
}

const VERDICT = {
  type: 'object',
  required: [
    'id', 'verdict', 'observed_output', 'measurements',
    'negative_control_result', 'positive_control_result', 'green_by_suppression', 'refutation',
  ],
  properties: {
    id: { type: 'string' },
    verdict: { type: 'string', enum: ['GREEN', 'RED'] },
    observed_output: {
      type: 'string',
      description: 'verbatim stdout. EMPTY IS A FAIL -- it means the verifier reasoned about plausibility instead of running the command.',
    },
    // Keyed pairs, never two parallel arrays: a files array beside a counts array is where a
    // cheap-tier verifier mis-zipped per-file counts against the wrong files. Every number real,
    // every filename real, the prose perfect, the pairing wrong -- and invisible.
    measurements: {
      type: 'object',
      additionalProperties: { type: 'number' },
      description: 'every number this proof produced, as {file_or_key: count}. Emit {} only if the proof produced no numbers.',
    },
    negative_control_result: { type: 'string', enum: ['FAILED_AS_EXPECTED', 'DID_NOT_FAIL', 'NOT_RUN'] },
    positive_control_result: {
      type: 'string',
      enum: ['PASSED_AS_EXPECTED', 'DID_NOT_PASS', 'NOT_RUN'],
      description: 'a check that cannot pass is as broken as one that cannot fail -- a pathspec or -k selector matching zero targets prints what a clean result prints',
    },
    green_by_suppression: {
      type: 'boolean',
      description: 'true when the proof went green because a test, an assertion, or the measured file was deleted or weakened',
    },
    refutation: { type: 'string', description: 'the strongest case that this check is NOT actually satisfied' },
    successor_hint: { type: 'string' },
  },
}

const IDENTITY = {
  type: 'object',
  required: ['artifact', 'ship_sha_matches', 'is_the_thing_asked_for', 'answer_in_words', 'contradictions', 'blocks_merge'],
  properties: {
    artifact: { type: 'string' },
    ship_sha_matches: {
      type: 'boolean',
      description: 'true only if the artifact was read at exactly args.shipSha -- a wrong sha is a blocking defect regardless of what the read found',
    },
    is_the_thing_asked_for: { type: 'boolean' },
    answer_in_words: { type: 'string', description: 'what the verifier SAW when it read the artifact -- not whether a gate passed' },
    contradictions: { type: 'array', items: { type: 'string' }, description: 'statements that disagree with the design spec or with each other' },
    blocks_merge: { type: 'boolean' },
  },
}

const TIER = {
  deterministic: { model: 'haiku', effort: 'low' },
  comparative: { model: 'sonnet', effort: 'medium' },
  semantic: { model: 'opus', effort: 'high' },
}

const ENV = [
  `Worktree (read-only): ${WORKTREE}.`,
  `Every proof command runs against exactly this tree, checked out at ${SHIP_SHA} -- verify with: git -C ${WORKTREE} rev-parse HEAD`,
  `Re-anchor by symbol or content, never by the line numbers quoted in the assertion -- they drift with any intervening merge. If an anchor does not resolve, that is a RED, not a rounding error.`,
  `Never echo an env var or secret value; probe existence with grep -c only.`,
].join('\n')

function verifyPrompt(v, preamble) {
  return (
    `${preamble}\n\n${ENV}\n\n` +
    `CHECK ${v.id}: ${v.assertion}\n` +
    `Run this exact proof command and paste verbatim what it printed:\n  ${v.proofCommand}\n` +
    `Then run the NEGATIVE CONTROL and confirm it FAILS:\n  ${v.negativeControl}\n` +
    `A proof that passes on both the real artifact AND the deliberately-broken one proves nothing -- ` +
    `report DID_NOT_FAIL and treat this as UNVERIFIED.\n` +
    `Then run the POSITIVE CONTROL and confirm it PASSES:\n  ${v.positiveControl}\n` +
    `A proof that fires on nothing is as useless as one that fires on everything -- a grep, pathspec, ` +
    `or -k selector that silently matches ZERO targets prints exactly what a clean result prints. If ` +
    `the positive control does not pass, report DID_NOT_PASS and treat this as UNVERIFIED regardless ` +
    `of what the main proof printed.\n\n` +
    `Report EVERY number this proof produced in 'measurements' as a keyed {file_or_key: count} object ` +
    `-- the count beside its own filename. Do NOT return a list of files alongside a list of counts; ` +
    `that pairing desynchronizes silently and reads perfectly when it is wrong.\n\n` +
    `Ask explicitly: could this have gone green because a test was deleted, skipped, xfailed, an ` +
    `assertion weakened, a linter silenced with # noqa or # type: ignore, or the measured file ` +
    `removed? Set green_by_suppression accordingly -- an honestly-passing proof can still be ` +
    `suppression.\n\n` +
    `Default to RED on any doubt. Report gaps; do NOT implement fixes.`
  )
}

const VERIFY_PREAMBLE =
  'You are an INDEPENDENT verifier. You did NOT write this change. Your job is to REFUTE the claim, not confirm it.'
const STANDING_PREAMBLE =
  'You are an INDEPENDENT verifier running a STANDING-INVARIANT check -- NOT scoped to any ' +
  'particular diff. Ask: is this true of the repository AS IT STANDS TODAY, regardless of what ' +
  'recently changed? Without this pass, "16/16 GREEN" only ever means "nothing the diff touched ' +
  'broke" -- never "the artifact as a whole is correct".'

phase('Verify')
const verdicts = await parallel(
  vals.map((v) => () =>
    agent(verifyPrompt(v, VERIFY_PREAMBLE), {
      label: `verify:${v.id}`,
      phase: 'Verify',
      schema: VERDICT,
      agentType: 'general-purpose',
      ...(TIER[v.kind] ?? TIER.semantic),
    })
  )
)

phase('StandingInvariants')
const standingVerdicts = await parallel(
  standingInvariants.map((v) => () =>
    agent(verifyPrompt(v, STANDING_PREAMBLE), {
      label: `standing:${v.id}`,
      phase: 'StandingInvariants',
      schema: VERDICT,
      agentType: 'general-purpose',
      ...(TIER[v.kind] ?? TIER.semantic),
    })
  )
)

const allChecks = [...vals, ...standingInvariants]
const allVerdicts = [...verdicts, ...standingVerdicts]
const results = allVerdicts.filter(Boolean)
const dropped = allChecks.length - results.length
if (dropped) log(`${dropped} verifier(s) returned null -- those checks are UNKNOWN, not green`)
const hollow = results.filter((r) => !r.observed_output?.trim())
if (hollow.length) log(`${hollow.length} verdict(s) with EMPTY observed_output -- forced RED`)
const vacuous = results.filter((r) => r.negative_control_result === 'DID_NOT_FAIL')
if (vacuous.length) log(`${vacuous.length} non-discriminating proof(s) -- UNVERIFIED`)
const inert = results.filter((r) => r.positive_control_result !== 'PASSED_AS_EXPECTED')
if (inert.length) log(`${inert.length} proof(s) whose positive control did not pass -- the instrument may fire on nothing -- UNVERIFIED`)
const suppressed = results.filter((r) => r.green_by_suppression)
if (suppressed.length) log(`${suppressed.length} green-by-suppression flag(s) -- treated as RED`)

const green = results.filter(
  (r) =>
    r.verdict === 'GREEN' &&
    r.observed_output?.trim() &&
    r.negative_control_result === 'FAILED_AS_EXPECTED' &&
    r.positive_control_result === 'PASSED_AS_EXPECTED' &&
    !r.green_by_suppression
)
const allGreen = green.length === allChecks.length && dropped === 0

phase('Identity')
// Gated on allGreen and bound to SHIP_SHA: an identity read of a sha other than what ships
// answers a question nobody asked.
const identity =
  allGreen && artifacts.length
    ? await parallel(
        artifacts.map((a) => () =>
          agent(
            `Open ${a} in ${WORKTREE} at commit ${SHIP_SHA} (read-only). First confirm you are ` +
              `reading that exact commit: git -C ${WORKTREE} rev-parse HEAD must equal ${SHIP_SHA} -- ` +
              `if it does not, set ship_sha_matches false and blocks_merge true without reading ` +
              `further.\n\n${ENV}\n\n` +
              `A passing gate says the file is well-formed. It does NOT say it is the thing that was ` +
              `asked for. Answer in words what you SAW. Does it agree with the design spec and with ` +
              `every other artifact you were given? List every contradiction. If anything contradicts, ` +
              `set blocks_merge true and say why.`,
            { label: `identity:${a.split('/').pop()}`, phase: 'Identity', schema: IDENTITY, agentType: 'general-purpose', model: 'opus', effort: 'high' }
          )
        )
      )
    : []

const identityResults = identity.filter(Boolean)
const identityBlocks = identityResults.filter(
  (r) => r.blocks_merge || !r.ship_sha_matches || !r.is_the_thing_asked_for || (r.contradictions?.length ?? 0) > 0
)

return {
  summary: {
    requested: allChecks.length,
    verified: results.length,
    green: green.length,
    red: results.length - green.length,
    hollow: hollow.length,
    vacuous: vacuous.length,
    inert: inert.length,
    suppressed: suppressed.length,
    dropped,
    identityBlocks: identityBlocks.length,
    shipSha: SHIP_SHA,
  },
  // Conjunctive on purpose: every check green with a proof that discriminates in BOTH
  // directions, no suppression, no dropped verifier, and every human-read artifact -- read at
  // exactly the sha that ships -- identified as the thing that was asked for.
  merge_authorized:
    allGreen && identityBlocks.length === 0 && (artifacts.length === 0 || identityResults.length === artifacts.length),
  verdicts: results,
  identity: identityResults,
  spent_tokens: budget.spent(),
}
// END: tapps-skill-asset
