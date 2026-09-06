#!/usr/bin/env bash
# upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched.
# BEGIN: tapps-skill-asset gitfacts-script/scripts/gitfacts.sh v3.12.83
# The five git questions an orchestrator asks constantly, answered correctly once.
#
# Usage:
#   scripts/gitfacts.sh adds    <repo> <ref>        what this ref ADDS vs origin/main (three-dot)
#   scripts/gitfacts.sh landed  <repo> <ref>        is this ref's content already on origin/main?
#   scripts/gitfacts.sh content <repo> <string>     when did <string> enter/leave origin/main?
#   scripts/gitfacts.sh stale   <repo>              is this checkout behind origin/main?
#   scripts/gitfacts.sh sessions <repo>             live sessions sharing this working tree
#     (exits 0 whenever it successfully counts sessions, regardless of the count --
#      a caller doing `if gitfacts.sh sessions .; then` must see 0 in BOTH the safe
#      case (0 or 1 sessions) and the hazard case (2+); only a failure to determine
#      the count at all should exit non-zero)
#
# Every one of these was hand-rolled during the 2026-09-01 program and every one was
# got wrong at least once. The errors were not carelessness, they were the commands
# being subtly wrong by default:
#
#   * `git diff origin/main <ref>` (TWO dots) shows main's newer work as deletions when
#     the ref is behind. It reported 1298 files / 58k deletions for a branch that added
#     five. Three-dot is almost always what you meant.
#   * `git log -S` without an explicit ref searches the CHECKOUT's HEAD, which is routinely
#     stale. It returned "never on main" for content that was merged and then purged.
#   * A branch that is `ahead 1` may be unmerged work, OR work that landed by another
#     route, OR work that landed and was deliberately reverted. `rev-list --count` says 1
#     for all three. Only content comparison tells them apart -- and the third case is a
#     compliance hazard that presents as routine cleanup.
set -euo pipefail

usage() { sed -n '/^# Usage:/,/^# *$/p' "${BASH_SOURCE[0]}" >&2; exit 2; }

CMD=${1:-}; REPO=${2:-}
[ -n "$CMD" ] && [ -n "$REPO" ] || usage
[ -d "$REPO/.git" ] || git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1 || {
  echo "not a git checkout: $REPO" >&2; exit 1; }

g() { git -C "$REPO" "$@"; }
g fetch origin --quiet 2>/dev/null || echo "warning: fetch failed; results may be stale" >&2

case "$CMD" in
  adds)
    REF=${3:?usage: adds <repo> <ref>}
    echo "# what $REF adds vs origin/main (three-dot; two-dot would show main's work as deletions)"
    g diff --stat "origin/main...$REF"
    ;;

  landed)
    REF=${3:?usage: landed <repo> <ref>}
    files=$(g diff --name-only "origin/main...$REF")
    [ -n "$files" ] || { echo "VERDICT: ref adds nothing vs origin/main"; exit 0; }
    missing=0; total=0
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      total=$((total + 1))
      if g cat-file -e "origin/main:$f" 2>/dev/null &&
         g diff --quiet "origin/main" "$REF" -- "$f" 2>/dev/null; then
        printf '  identical on main : %s\n' "$f"
      else
        printf '  DIFFERS or absent : %s\n' "$f"; missing=$((missing + 1))
      fi
    done <<< "$files"
    echo
    if [ "$missing" -eq 0 ]; then
      echo "VERDICT: all $total file(s) already on origin/main -- ref is SUPERSEDED."
      echo "  Deleting it loses nothing. 'ahead 1' here was the squash-merge illusion."
    else
      echo "VERDICT: $missing of $total file(s) differ from origin/main."
      echo "  Do NOT assume this is unmerged work. Run:  gitfacts.sh content $REPO '<a distinctive string>'"
      echo "  If it landed and was later REVERTED, merging forward re-introduces what someone removed."
    fi
    ;;

  content)
    S=${3:?usage: content <repo> <string>}
    echo "# history of '$S' on origin/main (explicit ref: a bare -S searches the stale checkout HEAD)"
    out=$(g log origin/main --format='%h %ad %s' --date=short -S "$S" || true)
    if [ -z "$out" ]; then
      echo "  no commit on origin/main added or removed this string"
      echo "  NOTE: absence here is only meaningful if the string is spelled as the producer spells it."
      echo "        An exact-identifier search across a naming seam is a false-negative machine."
    else
      echo "$out"
      echo
      echo "  Read this newest-first. If the string was ADDED and later REMOVED, the removal is"
      echo "  probably deliberate and re-introducing it reverts someone's decision."
    fi
    ;;

  stale)
    local_head=$(g rev-parse --short HEAD)
    remote_head=$(g rev-parse --short origin/main)
    behind=$(g rev-list --count "HEAD..origin/main")
    ahead=$(g rev-list --count "origin/main..HEAD")
    echo "HEAD=$local_head  origin/main=$remote_head  ahead=$ahead  behind=$behind"
    flagged=$(g ls-files -v | grep '^[a-z]' || true)
    if [ -n "$flagged" ]; then
      echo "ASSUME-UNCHANGED FILES PRESENT -- 'git status' is blind to these:"
      echo "$flagged" | sed 's/^/  /'
    fi
    [ "$behind" -eq 0 ] && echo "VERDICT: current." || {
      echo "VERDICT: STALE by $behind commit(s). Any -S / grep / read here answers about old code."; }
    ;;

  sessions)
    n=0
    for p in $(pgrep -f 'native-binary/claude' 2>/dev/null || true); do
      d=$(readlink "/proc/$p/cwd" 2>/dev/null || true)
      [ "$d" = "$(cd "$REPO" && pwd)" ] && { echo "  pid $p"; n=$((n + 1)); }
    done
    echo "VERDICT: $n live session(s) share this working tree and its single git index."
    if [ "$n" -gt 1 ]; then
      echo "  Any 'git add -A' by any of them stages the others' work. Use per-session worktrees."
    fi
    ;;

  *) usage ;;
esac
# END: tapps-skill-asset
