// upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
// BEGIN: tapps-skill-asset workflows/.claude/workflows/linear-disposition-verify.js v3.12.83
export const meta = {
  name: 'linear-disposition-verify',
  description: 'Read-only evidence gathering + adversarial verify for Linear issue dispositions -- writes happen in the calling session, never here',
  whenToUse: 'Pass args.candidates = [{id, hypothesis}], args.team, args.project, and args.repoPath. Returns confirmed dispositions; the calling session applies them via the linear-issue skill.',
  phases: [
    { title: 'Evidence', detail: 'per-issue ground truth from repo + live Linear state' },
    { title: 'Verify', detail: 'adversarial confirm/refute of each disposition' },
  ],
}

// args = {
//   team: string,           // Linear team these issues belong to -- never assumed
//   project: string,        // Linear project these issues belong to
//   repoPath: string,       // repo whose commits/docs/probes decide the disposition
//   candidates: [{ id, hypothesis }],
// }

const DISPOSITION = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'current_state', 'verdict', 'evidence', 'rationale'],
  properties: {
    id: { type: 'string' },
    current_state: { type: 'string', description: 'live Linear state at read time' },
    verdict: {
      type: 'string',
      enum: ['close', 'cancel', 'dedupe', 'reparent', 'demote', 'reopen', 'update_state', 'keep', 'blocked'],
    },
    target: { type: 'string', description: 'target state / parent / canonical duplicate id, when applicable' },
    evidence: { type: 'array', items: { type: 'string' }, description: 'file:line, commit sha, PR #, probe output lines' },
    rationale: { type: 'string' },
    blocked_on: { type: 'string', description: 'when verdict=blocked: the check id or external condition this waits on' },
  },
}

const VERDICT = {
  type: 'object',
  additionalProperties: false,
  required: ['id', 'confirmed', 'reason'],
  properties: {
    id: { type: 'string' },
    confirmed: { type: 'boolean' },
    corrected_verdict: { type: 'string', description: 'when refuted: the verdict the evidence actually supports' },
    reason: { type: 'string' },
  },
}

// The Workflow harness may deliver args as a JSON-encoded string.
const parsedArgs = typeof args === 'string' ? JSON.parse(args) : args
const TEAM = parsedArgs?.team
const PROJECT = parsedArgs?.project
const REPO_PATH = parsedArgs?.repoPath
const candidates = (parsedArgs && parsedArgs.candidates) || []

if (!TEAM) throw new Error('Pass args.team -- the Linear team these issues belong to. Never assume; a wrong-team write is an agent-scope violation.')
if (!PROJECT) throw new Error('Pass args.project -- the Linear project these issues belong to.')
if (!REPO_PATH) throw new Error('Pass args.repoPath -- ground truth lives in the repo, not in Linear prose. Point this at the repo whose commits/docs decide the disposition.')
if (!candidates.length) {
  throw new Error('Pass args.candidates = [{id: "TAP-####", hypothesis: "..."}]')
}
if (candidates.length > 30) {
  throw new Error('Cap: <=30 candidates per invocation. Chunk the list.')
}
if (budget.total && budget.remaining() < 30_000) {
  log(`insufficient budget (${budget.remaining()} left) -- aborting`)
  return { aborted: true, reason: 'budget' }
}

const RULES = [
  'READ-ONLY: no file writes, no Linear writes, no state changes of any kind. You gather evidence; the calling session applies dispositions.',
  `Linear reads: load tools via ToolSearch ("select:mcp__tapps-mcp__tapps_linear_snapshot_get,mcp__plugin_linear_linear__get_issue,mcp__plugin_linear_linear__list_issues"). Single-issue lookups go straight to get_issue(id). Any multi-issue slice needs tapps_linear_snapshot_get(team="${TEAM}", project="${PROJECT}", state=...) FIRST.`,
  `Ground truth lives in ${REPO_PATH} (CHANGELOG.md, git log, docs/, prompts/, reports/) and, for live claims, read-only probes. Docs can be stale -- commits and live probes outrank prose.`,
  'Never print secret values. Cite every claim as file:line, commit sha, or pasted probe line.',
].join('\n')

phase('Evidence')
const results = await pipeline(
  candidates,
  (c) =>
    agent(
      [
        `Gather ground-truth evidence for Linear issue ${c.id} (team ${TEAM}, project ${PROJECT}).`,
        `Working hypothesis: ${c.hypothesis}`,
        '',
        'Do: (1) get_issue for live state/parent/children; (2) check the hypothesis against repo ' +
          'reality -- CHANGELOG.md, git log --oneline, the specific docs/prompts the issue names, and ' +
          'cheap read-only probes if the claim is about a live surface; (3) decide the disposition the ' +
          'EVIDENCE supports (which may contradict the hypothesis).',
        'Verdicts: close (work verifiably shipped) | cancel (premise invalid/superseded -- name the ' +
          'superseding ruling) | dedupe (name canonical id in target) | reparent (name new parent in ' +
          'target) | demote (In Progress but idle/blocked -- target state in target) | reopen | ' +
          'update_state (epic state to match children) | keep (state is accurate) | blocked (needs ' +
          'evidence this run has not produced yet -- name it in blocked_on).',
        'Rule: an issue is closeable ONLY on deterministic evidence (commit/PR/probe), never on a doc claim alone.',
        '',
        RULES,
      ].join('\n'),
      { label: `evidence:${c.id}`, phase: 'Evidence', schema: DISPOSITION, effort: 'medium' }
    ),
  (d, c) =>
    d &&
    agent(
      [
        `Adversarially verify this proposed Linear disposition. REFUTE it if the evidence does not hold.`,
        `Issue: ${d.id} (${PROJECT}). Current state: ${d.current_state}. Proposed: ${d.verdict}${d.target ? ' -> ' + d.target : ''}.`,
        `Rationale: ${d.rationale}`,
        `Evidence claimed: ${JSON.stringify(d.evidence)}`,
        '',
        'Independently re-check the load-bearing evidence yourself (open the file:line, git show the ' +
          'sha, re-run the probe). Default to confirmed=false on any doubt. A "close" verdict with ' +
          'only prose evidence is refuted. If refuted, state the verdict the evidence actually ' +
          'supports in corrected_verdict.',
        '',
        RULES,
      ].join('\n'),
      { label: `verify:${d.id}`, phase: 'Verify', schema: VERDICT, effort: 'high' }
    ).then((v) => ({ ...d, verify: v })),
)

const done = results.filter(Boolean)
const confirmed = done.filter((r) => r.verify && r.verify.confirmed)
const refuted = done.filter((r) => r.verify && !r.verify.confirmed)
const unverified = done.filter((r) => !r.verify)
log(`Dispositions: ${confirmed.length} confirmed, ${refuted.length} refuted, ${unverified.length} unverified, ${candidates.length - done.length} agent-failed`)
return { confirmed, refuted, unverified, failed_count: candidates.length - done.length }
// END: tapps-skill-asset
