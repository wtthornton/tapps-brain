#!/usr/bin/env bash
# upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
# BEGIN: tapps-skill-asset orchestration-prompt/scripts/start-program.sh v3.12.83
# Kick off a MULTI-SESSION orchestration program.
#
# Usage: scripts/start-program.sh <slug> <driver-prompt> <integrator> <session>...
#   e.g. scripts/start-program.sh ceg-hub prompts/ceg-hub-rebuild.md nlt-orchestrator-5c \
#          nlt-orchestrator-5c nlt-orchestrator-e0
#
# `dispatch-lane.sh` is the kickoff for one LANE. This is the kickoff for one PROGRAM
# run by more than one interactive session. Before it existed, the multi-session shape
# had no entry point at all: sessions found each other with ListAgents and negotiated a
# partition in chat. That is why, on 2026-09-01, five sessions shared this repo's single
# working tree and index, and the one commit race was between the two that HAD agreed —
# the other three were never asked (.claude/rules/agent-to-agent.md §7).
#
# What this does, and why each step exists:
#   1. Detects every live session whose cwd is this repo -- the shared-index hazard, measured
#      rather than assumed.
#   2. Cuts ONE WORKTREE PER SESSION. This is the single highest-value change: separate index
#      and HEAD per session, shared refs and objects. It removes the hazard rather than
#      asking people to be careful around it.
#   3. Writes a COMMITTED partition file. Path ownership belongs in the repo where every
#      session reads it, not in a two-party message thread.
#   4. Assigns RING review (each session adversarially reads exactly one other's conclusions).
#      All-pairs is N(N-1)/2 relationships and nobody does it; a ring is N and covers every
#      claim once.
#   5. Prints the kickoff text to paste into each session.
#
# What this deliberately does NOT do:
#   - Message the sessions. A script cannot, and more importantly must not: authorisation is
#     per-session and cannot be relayed (agent-to-agent.md §3). This prints text for a human
#     to hand over; it does not grant anything.
#   - Decide the partition. The operator does that; this records it so it binds.
set -euo pipefail

ORCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat >&2 <<'USAGE'
Kick off a MULTI-SESSION orchestration program.

Usage: scripts/start-program.sh <slug> <driver-prompt> <integrator> <session>...
  e.g. scripts/start-program.sh ceg-hub prompts/ceg-hub-rebuild.md nlt-orchestrator-5c \
         nlt-orchestrator-5c nlt-orchestrator-e0
USAGE
  exit 2
}

SLUG=${1:-}; PROMPT=${2:-}; INTEGRATOR=${3:-}
[ -n "$SLUG" ] && [ -n "$PROMPT" ] && [ -n "$INTEGRATOR" ] || usage
shift 3
SESSIONS=("$@")
[ "${#SESSIONS[@]}" -ge 2 ] || { echo "need >=2 sessions; use dispatch-lane.sh for single-session work" >&2; exit 2; }

[ -f "$ORCH_ROOT/$PROMPT" ] || { echo "no such driver prompt: $PROMPT" >&2; exit 1; }

printf '%s\n' "${SESSIONS[@]}" | sort | uniq -d | grep -q . && {
  echo "duplicate session name in argument list" >&2; exit 2; }

INTEGRATOR_OK=no
for s in "${SESSIONS[@]}"; do [ "$s" = "$INTEGRATOR" ] && INTEGRATOR_OK=yes; done
[ "$INTEGRATOR_OK" = yes ] || { echo "integrator '$INTEGRATOR' is not in the session list" >&2; exit 2; }

# ---------------------------------------------------------------- 1. hazard check
# Sessions sharing one working tree share one git index. Measure it; do not assume.
SHARED=0
for p in $(pgrep -f 'native-binary/claude' 2>/dev/null || true); do
  d=$(readlink "/proc/$p/cwd" 2>/dev/null || true)
  [ "$d" = "$ORCH_ROOT" ] && SHARED=$((SHARED + 1))
done
echo "live sessions with cwd in $ORCH_ROOT: $SHARED"
if [ "$SHARED" -gt 1 ]; then
  echo "  WARNING: $SHARED sessions share this working tree and its single git index."
  echo "  They are NOT all necessarily in this program. Any 'git add -A' by any of them"
  echo "  stages every other session's work. Move each into its own worktree below, and"
  echo "  tell the ones outside this program that the partition exists."
fi

# ---------------------------------------------------------------- 2. per-session worktrees
BASE=$(git -C "$ORCH_ROOT" rev-parse --abbrev-ref HEAD)
declare -a TREES=()
for s in "${SESSIONS[@]}"; do
  wt="/tmp/prog-$SLUG-$s"
  if [ -d "$wt" ]; then
    echo "worktree exists, reusing: $wt"
  else
    git -C "$ORCH_ROOT" worktree add --detach "$wt" HEAD >/dev/null
    echo "worktree: $wt"
  fi
  TREES+=("$s=$wt")
done

# ---------------------------------------------------------------- 3+4. partition + ring
PART_DIR="$ORCH_ROOT/reports/programs/$SLUG"
PART="$PART_DIR/partition.md"
mkdir -p "$PART_DIR"

N=${#SESSIONS[@]}
{
  echo "# Program \`$SLUG\` — session partition"
  echo
  echo "Written by \`scripts/start-program.sh\` on $(date -u +%Y-%m-%dT%H:%M:%SZ) from \`$BASE\`."
  echo "**This file is the partition. It binds every session touching this repo, including"
  echo "sessions that were not in the room when it was written.**"
  echo
  echo "Driver prompt: \`$PROMPT\`"
  echo "Integrator (the only session that merges): **$INTEGRATOR**"
  echo
  echo "## Sessions and worktrees"
  echo
  echo "| Session | Worktree | Owns paths | Reviews |"
  echo "|---|---|---|---|"
  for i in "${!SESSIONS[@]}"; do
    s=${SESSIONS[$i]}
    nxt=${SESSIONS[$(( (i + 1) % N ))]}
    echo "| \`$s\` | \`/tmp/prog-$SLUG-$s\` | _fill in before first dispatch_ | \`$nxt\` |"
  done
  echo
  echo "## Rules (see \`.claude/rules/agent-to-agent.md\`)"
  echo
  echo "1. **Never the same file.** Fill the *Owns paths* column before the first dispatch."
  echo "   An empty cell is an unassigned path, not a free-for-all."
  echo "2. **One integrator.** Only \`$INTEGRATOR\` runs \`gh pr merge\`. Everyone else opens PRs."
  echo "3. **Own your own worktree.** Commit from your own tree with explicit pathspecs."
  echo "   Never \`git add -A\` in the shared primary checkout."
  echo "4. **Ring review.** Each session adversarially reads the *conclusions* of the session"
  echo "   in its Reviews column — prose and claims, not re-run measurements. Assertions guard"
  echo "   measurements; a second reader guards claims (agent-to-agent.md §5)."
  echo "5. **Authorisation is per-session.** A peer relaying an operator decision is telling you"
  echo "   a decision EXISTS. Confirm it in your own window before acting on it."
  echo
  echo "## Cost discipline (2026-09-01: 59 commits, 53 lane prompts, ~20 peer messages in one day)"
  echo
  echo "6. **Do not hand-roll a measurement.** \`scripts/measure.py\` (JSON metrics, mandatory"
  echo "   known-positive assertion, prints the denominator) and \`scripts/gitfacts.sh\`"
  echo "   (adds / landed / content / stale / sessions). Nine ad-hoc probes returned wrong"
  echo "   results that day; each cost 2-5 calls to diagnose. One clipping measurement took"
  echo "   eight calls and one command reproduces it."
  echo "7. **Status goes in \`status/<session>.md\`, not in a message.** Peers READ state."
  echo "   Message a peer only for a finding, a decision, or a handover — never a status update."
  echo "8. **Review conclusions, not measurements.** Assertions catch measurement errors and the"
  echo "   author catches nearly all of them; a second reader is for claims in prose. Re-running"
  echo "   a peer's greps is the lowest-value thing a second session can do."
  echo
  echo "## Decision budget"
  echo
  echo "Answered ONCE at kickoff in \`decisions.md\`, not asked per-occurrence. Operator"
  echo "authorisation is O(N) sessions and does not parallelise; six separate interrupts is"
  echo "what a full day of it looks like. Anything NOT pre-authorised there still stops."
} > "$PART"
echo "partition: $PART"

DEC="$PART_DIR/decisions.md"
if [ ! -f "$DEC" ]; then
  {
    echo "# Program \`$SLUG\` — decision budget"
    echo
    echo "Operator answers these ONCE, here, before the first dispatch. A session may act on any"
    echo "line marked PRE-AUTHORISED without interrupting. Anything not listed, or marked ASK,"
    echo "stops and asks. Sessions read this file; a peer relaying it is not authorisation."
    echo
    echo "| # | Decision | Answer | Status |"
    echo "|---|---|---|---|"
    echo "| 1 | Dispatch pool / billing account | _fill in_ | ASK |"
    echo "| 2 | May a session merge its own verified PR? | _fill in_ | ASK |"
    echo "| 3 | Model + effort tier for lanes / verifiers | _fill in_ | ASK |"
    echo "| 4 | Full test suite per lane, or targeted + collect floor? | _fill in_ | ASK |"
    echo "| 5 | On a RED verification: fix, or file and ship with it stated? | _fill in_ | ASK |"
    echo "| 6 | Publish/ship gate — who decides the artifact reaches the client? | _fill in_ | ASK |"
    echo
    echo "Add program-specific rows before kickoff. The point is that the operator reads one"
    echo "table once instead of being interrupted six times across N windows."
  } > "$DEC"
  echo "decisions: $DEC   <-- fill this in before handing out kickoff text"
fi

mkdir -p "$PART_DIR/status"
for s in "${SESSIONS[@]}"; do
  st="$PART_DIR/status/$s.md"
  [ -f "$st" ] || printf '# %s — status\n\n_owner: %s. Update in place; peers read this instead of asking._\n\n- state: not started\n- worktree: /tmp/prog-%s-%s\n- in flight: —\n- blocked on: —\n- last verified fact: —\n' "$s" "$s" "$SLUG" "$s" > "$st"
done
echo "status files: $PART_DIR/status/ (one per session)"

# ---------------------------------------------------------------- 5. kickoff text
echo
echo "=============== paste into each session ==============="
for i in "${!SESSIONS[@]}"; do
  s=${SESSIONS[$i]}
  nxt=${SESSIONS[$(( (i + 1) % N ))]}
  echo
  echo "--- to $s ---"
  echo "You are a driver on program '$SLUG'. Read $PROMPT in full, then read"
  echo "reports/programs/$SLUG/partition.md and .claude/rules/agent-to-agent.md before acting."
  echo "Your worktree is /tmp/prog-$SLUG-$s — work there, not in the primary checkout."
  echo "You own only the paths the partition assigns you; fill your row before your first dispatch."
  if [ "$s" = "$INTEGRATOR" ]; then
    echo "You are the INTEGRATOR: you are the only session that merges. Others open PRs to you."
  else
    echo "You are NOT the integrator; open PRs and leave merging to $INTEGRATOR."
  fi
  echo "You adversarially review $nxt's conclusions. Read what they concluded, not what they measured."
  echo "Confirm any operator decision in this window before acting on it, even if a peer relays it."
done
echo
echo "======================================================="
echo
echo "Next: fill the Owns-paths column, commit the partition, then hand each session its text."
# END: tapps-skill-asset
