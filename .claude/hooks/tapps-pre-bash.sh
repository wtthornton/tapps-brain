#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.83
# tapps-mcp-hook-content-sha: da7d038c
# TappsMCP PreToolUse hook (Bash) - destructive command guard (opt-in)
# Blocks commands containing rm -rf, format c:, etc. Exit 2 = block, 0 = allow.
# TAP-6889: also blocks backgrounding, leaving the project dir, and a few
# suppression markers, but only when ORCHESTRATOR_GOAL_DISPATCH=1 (dispatched
# lanes) so interactive sessions are never affected.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PYBIN" ]; then
  # TAP-1785: enforcement gate fails closed when python is unavailable.
ROOT="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$ROOT" ]; then
  _common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$_common" ]; then
    ROOT="$(cd "$_common/.." && pwd)"
  else
    ROOT="$PWD"
  fi
fi
  mkdir -p "$ROOT/.tapps-mcp" 2>/dev/null
  echo "{\"ts\":\"$(date -u +%FT%TZ)\",\"hook\":\"tapps-pre-bash\",\"reason\":\"no_python\"}" \
    >> "$ROOT/.tapps-mcp/.bypass-log.jsonl" 2>/dev/null
  echo "TappsMCP: Blocked — no python interpreter available to evaluate destructive-command guard." >&2
  exit 2
fi
CMD=$(echo "$INPUT" | "$PYBIN" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    ti = d.get('tool_input', {}) or {}
    cmd = ti.get('command', '') or ti.get('cmd', '')
    if not cmd and isinstance(ti.get('args'), list):
        cmd = ' '.join(str(a) for a in ti['args'])
    print(cmd if isinstance(cmd, str) else '')
except Exception:
    print('')
" 2>/dev/null)
# Blocklist (substring match, case-insensitive for format/del).
# Fork-bomb signature ":(){"  is matched as a QUOTED literal substring
# because bare ( / ) terminate case alternatives early and cause a bash
# syntax error. The substring ":(){" is distinctive enough on its own.
BLOCK=0
case "$CMD" in
  *rm\ -rf*|*rm\ -fr*|*rm\ -r\ -f*|*rm\ -rf\ /*) BLOCK=1 ;;
  *format\ c:*|*format\ c:/*|*format\ C:*|*format\ C:/*) BLOCK=1 ;;
  *del\ /f\ /s\ /q*|*del\ /s\ /q*|*rd\ /s\ /q*) BLOCK=1 ;;
  *":(){"*) BLOCK=1 ;;
esac
if [ "$BLOCK" = 1 ]; then
  echo "TappsMCP: Blocked potentially destructive command." >&2
  exit 2
fi
# TAP-6889: lane guard, gated on ORCHESTRATOR_GOAL_DISPATCH=1 so it only
# fires for dispatched lanes, never interactive sessions. Re-parses $INPUT
# independently of the $CMD extraction above (rather than sharing it) since
# a command can contain literal newlines and splitting combined stdout by
# line would corrupt it.
# TAP-6908: the "&" check used to be a trailing-suffix string test, which
# missed a background operator anywhere else in the command ("cmd & echo",
# a subshell "( cmd & )") and never looked inside a `bash -c '...'` payload.
# Both gaps are closed below.
if [ "$ORCHESTRATOR_GOAL_DISPATCH" = "1" ]; then
  LANE_CHECK=$(echo "$INPUT" | "$PYBIN" -c "
import json, os, shlex, sys

BACKSLASH = chr(92)
SHELL_OPERATOR_CHARS = '();<>|&'
INTERPRETERS = ('bash', 'sh', 'zsh', 'dash')
SUPPRESSION_MARKERS = ('# noqa', '# type: ignore', '@pytest.mark.skip', 'xfail')


def _is_operator_token(tok):
    return bool(tok) and all(c in SHELL_OPERATOR_CHARS for c in tok)


def _tokenize(cmd):
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ''
        return list(lex)
    except ValueError:
        return []


def _has_background_amp(cmd):
    # Character-level scan rather than another shlex pass: shlex's
    # punctuation-run grouping merges an unquoted '&' with an immediately
    # adjacent non-punctuation character into one token (e.g. 'cmd&;next'
    # tokenizes as ['cmd', '&;', 'next'], and 'echo \$(pytest &)' produces
    # a token '&)'), so a token == '&' equality check would silently miss
    # both a mid-compound '&' glued to the next word and one inside a
    # subshell / command substitution. This scan special-cases only '&&'
    # (AND-list) and '>&' / '&>' (fd-merge redirects); every other
    # unquoted bare '&' is treated as a background operator. Quote and
    # escape tracking is a simplified approximation (backslash is a
    # universal escape outside single quotes) -- deliberately not a full
    # shell parser, per the smallest-robust-version constraint.
    in_single = False
    in_double = False
    escaped = False
    length = len(cmd)
    i = 0
    while i < length:
        c = cmd[i]
        if escaped:
            escaped = False
        elif c == BACKSLASH and not in_single:
            escaped = True
        elif c == chr(39) and not in_double:
            in_single = not in_single
        elif c == chr(34) and not in_single:
            in_double = not in_double
        elif c == '&' and not in_single and not in_double:
            prev = cmd[i - 1] if i > 0 else ''
            nxt = cmd[i + 1] if i + 1 < length else ''
            if nxt == '&':
                i += 2
                continue
            if prev in ('&', '>') or nxt == '>':
                i += 1
                continue
            return True
        i += 1
    return False


def _cd_escapes_project(tokens, project_dir, project_real):
    for idx, tok in enumerate(tokens):
        if tok != 'cd':
            continue
        if idx + 1 >= len(tokens) or _is_operator_token(tokens[idx + 1]):
            continue
        target = tokens[idx + 1]
        if target in ('-', '~'):
            continue
        if target.startswith('~/'):
            target = os.path.expanduser('~') + target[1:]
        if not os.path.isabs(target):
            target = os.path.join(project_dir, target)
        target_real = os.path.realpath(target)
        if target_real != project_real and not target_real.startswith(project_real + os.sep):
            return True
    return False


def _check(cmd, project_dir, project_real, depth):
    if _has_background_amp(cmd):
        return 'background operator (&)'
    tokens = _tokenize(cmd)
    for word in ('nohup', 'disown', 'setsid'):
        if word in tokens:
            return word + ' command word'
    if _cd_escapes_project(tokens, project_dir, project_real):
        return 'cd outside project directory'
    for marker in SUPPRESSION_MARKERS:
        if marker in cmd:
            return 'suppression marker'
    # TAP-6908: recurse into a literal bash/sh/zsh/dash -c '...' payload so
    # the same checks apply to a nested command string -- a quoted -c
    # payload is no longer a blind spot. This only follows a literal
    # string argument resolved by shlex: it does NOT expand shell
    # variables (e.g. bash -c with \$CMD), command substitution, eval, or
    # indirection through e.g. xargs -I{} bash -c. That remains an
    # accepted, tested gap rather than a silent one -- see
    # test_bash_c_variable_indirection_not_recursively_checked.
    if depth < 4:
        for idx, tok in enumerate(tokens):
            if os.path.basename(tok) not in INTERPRETERS:
                continue
            for j in range(idx + 1, len(tokens)):
                if tokens[j] == '-c':
                    if j + 1 < len(tokens):
                        nested = _check(tokens[j + 1], project_dir, project_real, depth + 1)
                        if nested:
                            return nested + ' (nested in ' + tok + ' -c)'
                    break
    return None


try:
    d = json.load(sys.stdin)
except Exception:
    print('ALLOW')
    sys.exit(0)
ti = d.get('tool_input', {}) or {}
cmd = ti.get('command', '') or ti.get('cmd', '')
if not cmd and isinstance(ti.get('args'), list):
    cmd = ' '.join(str(a) for a in ti['args'])
if not isinstance(cmd, str):
    cmd = ''
if ti.get('run_in_background') is True:
    print('BLOCK:run_in_background tool_input flag')
    sys.exit(0)
project_dir = os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd()
project_real = os.path.realpath(project_dir)
reason = _check(cmd, project_dir, project_real, 0)
if reason:
    print('BLOCK:' + reason)
else:
    print('ALLOW')
" 2>/dev/null)
  case "$LANE_CHECK" in
    BLOCK:*)
      echo "TappsMCP: Blocked by lane guard - ${LANE_CHECK#BLOCK:} (ORCHESTRATOR_GOAL_DISPATCH=1)." >&2
      exit 2
      ;;
  esac
fi
exit 0
