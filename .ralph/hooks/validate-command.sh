#!/bin/bash
# .ralph/hooks/validate-command.sh
# PreToolUse hook for Bash commands.
# Reads command from stdin JSON, blocks destructive operations.
# Exit 0 = allow, Exit 2 = block.
#
# TAP-624 rewrite: tokenize argv, scan every token (not just prefix), and
# anchor .ralph/ / .claude/ protection to the *target path* of any write-
# capable tool — not just shell redirects.

set -euo pipefail

# TAP-2344: anchor the project's .ralph/ to an absolute prefix so the
# `*/.ralph/*` glob below only matches the project-scoped install, not
# the global `~/.ralph/`. Bash redirect patterns at the end of this file
# also branch on this prefix.
_proj_dir="${CLAUDE_PROJECT_DIR:-$PWD}"
RALPH_DIR="$_proj_dir/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')
[[ -z "$COMMAND" ]] && exit 0

block() {
    echo "BLOCKED: $1: $COMMAND" >&2
    exit 2
}

# Normalize: collapse all whitespace (tabs, repeats) to single spaces so a
# `rm  -rf` double-space attack can't slip past a pattern expecting one space.
NORM=$(printf '%s' "$COMMAND" | tr '\t' ' ' | tr -s ' ')

# Split on unquoted whitespace. This is a best-effort parse — a shell with
# eval() can always construct tokens dynamically, but for a PreToolUse guard
# on direct Bash tool commands this covers the observed attack surface.
# shellcheck disable=SC2206
read -r -a ARGV <<< "$NORM"
CMD0="${ARGV[0]:-}"

# Skip leading `env VAR=val` wrappers and `sudo`: re-anchor CMD0 to the real
# command.
while [[ "$CMD0" == "env" || "$CMD0" == "sudo" || "$CMD0" == *"="* ]] && (( ${#ARGV[@]} > 1 )); do
    ARGV=("${ARGV[@]:1}")
    CMD0="${ARGV[0]:-}"
done

# Skip `uv run`, `pipx run`, `poetry run` wrappers so `uv run python -c` is
# detected as a python invocation, not a uv invocation. The runner command
# may take its own flags before the interpreter; we conservatively skip the
# common shape `<runner> run <interp> [-c ...]` only.
if [[ "$CMD0" == "uv" || "$CMD0" == "pipx" || "$CMD0" == "poetry" ]] \
   && [[ "${ARGV[1]:-}" == "run" ]] && (( ${#ARGV[@]} > 2 )); then
    ARGV=("${ARGV[@]:2}")
    CMD0="${ARGV[0]:-}"
fi

# Normalize CMD0 to its basename so /usr/bin/python3 and python3 match the
# same case branches below. Strip everything up to the last `/`.
CMD0="${CMD0##*/}"

# ---- 1. Destructive git ------------------------------------------------------

if [[ "$CMD0" == "git" ]]; then
    # Rebuild rest-of-command for substring scans
    REST=" ${ARGV[*]:1} "

    # Block --no-verify / --no-gpg-sign anywhere in args
    case "$REST" in
        *" --no-verify "*|*" --no-verify="*) block "--no-verify not allowed" ;;
        *" --no-gpg-sign "*|*" --no-gpg-sign="*) block "--no-gpg-sign not allowed" ;;
    esac

    # `git commit -n` short form
    if [[ "${ARGV[1]:-}" == "commit" ]]; then
        for arg in "${ARGV[@]:2}"; do
            [[ "$arg" == "-n" || "$arg" == "--amend" || "$arg" == --fixup=* || "$arg" == "--fixup" ]] && \
                block "destructive git commit flag ($arg)"
        done
    fi

    # `git push` with force (anywhere in argv), unless --force-with-lease
    if [[ "${ARGV[1]:-}" == "push" ]]; then
        has_force=0
        has_lease=0
        for arg in "${ARGV[@]:2}"; do
            case "$arg" in
                -f|--force|--force-if-includes) has_force=1 ;;
                --force-with-lease|--force-with-lease=*) has_lease=1 ;;
            esac
        done
        (( has_force == 1 && has_lease == 0 )) && block "destructive git push"

        # Harness-side R0 enforcement: refuse direct pushes to main. The
        # ralph-workflow skill mandates feature branches + squash-merge; this
        # is the hook that catches the case Claude's prose discipline misses.
        # AgentForge 2026-05-21 campaign shipped 13/20 commits direct to main
        # because R1's commit-on-main check accepted them as valid. R0 (in
        # the skill) added prose discipline; this is the harness-side net.
        #
        # Detect:
        #   git push origin main
        #   git push origin HEAD:main
        #   git push origin <branch>:main
        #   git push --tags origin main      (flags ignored; only refspec matters)
        # Allow:
        #   git push -u origin <branch>      (feature branches)
        #   git push origin --delete <branch>
        #   git push origin <branch>:<branch>  (same-name on both sides, non-main)
        #
        # Bypass via RALPH_ALLOW_PUSH_MAIN=1 (logged to .ralph/.bypass-log.jsonl
        # in a future iteration; for now, the env var is the escape hatch).
        if [[ "${RALPH_ALLOW_PUSH_MAIN:-}" != "1" ]]; then
            for arg in "${ARGV[@]:2}"; do
                case "$arg" in
                    main|master) block "direct push to ${arg} forbidden (R0). Use a feature branch + gh pr merge --squash" ;;
                    *:main|*:master|HEAD:main|HEAD:master|*:refs/heads/main|*:refs/heads/master) block "direct push to refs/heads/${arg##*:} forbidden (R0). Use a feature branch + gh pr merge --squash" ;;
                esac
            done
        fi
    fi

    # `git reset --hard`, `git clean -f*`, `git rm`. Anchor to the subcommand
    # position (ARGV[1]) — the prior substring scan over the whole command
    # matched these words inside commit messages (e.g.
    # `git commit -m "refactor: clean up"` was wrongly blocked).
    case "${ARGV[1]:-}" in
        clean|rm) block "destructive git subcommand (${ARGV[1]})" ;;
        reset)
            for arg in "${ARGV[@]:2}"; do
                [[ "$arg" == "--hard" ]] && block "destructive git reset --hard"
            done
            ;;
    esac
fi

# ---- 2. Destructive rm -------------------------------------------------------

if [[ "$CMD0" == "rm" ]]; then
    for arg in "${ARGV[@]:1}"; do
        case "$arg" in
            --recursive|--recursive=*) block "rm --recursive not allowed" ;;
            -r|-R|-rf|-fr|-Rf|-fR|-rR|-Rr) block "recursive rm not allowed" ;;
            --*) ;;
            -*)
                # Short-flag cluster — scan for r/R anywhere in the cluster
                if [[ "$arg" == *r* || "$arg" == *R* ]]; then
                    block "recursive rm (short-flag cluster $arg) not allowed"
                fi
                ;;
        esac
    done
fi

# ---- 3. find ... -delete -----------------------------------------------------

if [[ "$CMD0" == "find" ]]; then
    for arg in "${ARGV[@]:1}"; do
        [[ "$arg" == "-delete" ]] && block "find -delete not allowed"
    done
fi

# ---- 4. Interpreter -c / -e escape hatches -----------------------------------
# TAP-1876: include a one-line remediation pointing at file-based execution,
# which IS permitted. Without this, every loop where Claude tries
# `python3 -c "import x"` burns a tool call AND the next loop tries the
# same shape because the denial message gives no out.

_interpreter_snippet_path() {
    # Suggest an idiomatic extension per interpreter so the remediation
    # reads naturally for whichever language Claude reached for.
    case "$1" in
        python|python3) echo "/tmp/snippet.py" ;;
        perl)           echo "/tmp/snippet.pl" ;;
        ruby)           echo "/tmp/snippet.rb" ;;
        node)           echo "/tmp/snippet.js" ;;
        bash|sh|zsh)    echo "/tmp/snippet.sh" ;;
        *)              echo "/tmp/snippet"    ;;
    esac
}

# Versioned binaries (python3.12, python3.11, pypy3.10) reduce to the base
# interpreter family for the snippet-path lookup. We don't need to be exact
# about the version — only about the language.
_interp_family() {
    case "$1" in
        python|python3|python3.*|python2|python2.*|pypy|pypy3|pypy3.*) echo "python" ;;
        perl|perl5|perl5.*)     echo "perl" ;;
        ruby|ruby[0-9]*)        echo "ruby" ;;
        node|nodejs|node[0-9]*) echo "node" ;;
        bash|sh|zsh|dash|ksh)   echo "$1" ;;
        *) echo "" ;;
    esac
}

_family=$(_interp_family "$CMD0")
if [[ -n "$_family" ]]; then
    for arg in "${ARGV[@]:1}"; do
        case "$arg" in
            -c|-e)
                _snippet=$(_interpreter_snippet_path "$_family")
                echo "BLOCKED: $CMD0 $arg script-execution not allowed. Write the snippet to $_snippet (or similar) and run \"$CMD0 $_snippet\" instead: $COMMAND" >&2
                exit 2
                ;;
        esac
    done
fi

# ---- 5. Write-capable tools hitting protected paths --------------------------

_is_protected_path() {
    # Any .ralph/ or .ralphrc path is blocked at the shell layer; the
    # Edit-tool hook (protect-ralph-files.sh) carves out fix_plan.md /
    # status.json from there, but Bash gets no such grace because we
    # can't prove a `>` is a narrow update vs. a clobber.
    #
    # TAP-2344: .ralph/ and .ralphrc are anchored to the project root so the
    # global `~/.ralph/` install is allowed to be edited from inside a Ralph
    # project (the hotfix workflow).
    #
    # TAP-2345 (F4): bring .claude/ carve-outs to parity with the Edit-side
    # hook so `cat > .claude/rules/foo.md` and Bash-side skill installs
    # stop being dead-ends that force a tool pivot. Block only the
    # subdirs that are genuinely dangerous to clobber (settings.json,
    # agents/, hooks/) — rules/, skills/, commands/ are routine agent
    # surface area.
    local p="$1"
    p="${p%\"}"; p="${p#\"}"; p="${p%\'}"; p="${p#\'}"
    case "$p" in
        # Carve-out FIRST (TAP-2599): Ralph self-manages its transient
        # task-selection caches under .ralph/. The ralph-workflow skill
        # (step 0) tells the agent to `rm -f .ralph/.linear_next_issue`
        # after honoring a locality hint, and the optimizer rewrites it
        # every session. Blanket-protecting it forced a cancelled tool
        # call every selection loop. These are ephemeral hints, never
        # durable state — durable files (status.json, fix_plan.md,
        # .circuit_breaker_state, .harness_halt_reason) stay protected by
        # the blanket arm below.
        "$RALPH_DIR"/.linear_next*|.ralph/.linear_next*|./.ralph/.linear_next*) return 1 ;;
        "$RALPH_DIR"|"$RALPH_DIR"/*) return 0 ;;
        .ralph|.ralph/*|./.ralph/*) return 0 ;;
        "$_proj_dir"/.ralphrc) return 0 ;;
        .ralphrc|./.ralphrc) return 0 ;;
        # Carve-outs FIRST: rules/, skills/, commands/ are explicitly
        # allowed before the .claude/* blanket block fires.
        *.claude/rules|*.claude/rules/*) return 1 ;;
        *.claude/skills|*.claude/skills/*) return 1 ;;
        *.claude/commands|*.claude/commands/*) return 1 ;;
        .claude/rules|.claude/rules/*) return 1 ;;
        .claude/skills|.claude/skills/*) return 1 ;;
        .claude/commands|.claude/commands/*) return 1 ;;
        ./.claude/rules|./.claude/rules/*) return 1 ;;
        ./.claude/skills|./.claude/skills/*) return 1 ;;
        ./.claude/commands|./.claude/commands/*) return 1 ;;
        # Remaining .claude/ (settings.json, agents/, hooks/) — blocked.
        .claude|.claude/*|./.claude/*|*/.claude/*) return 0 ;;
    esac
    return 1
}

_walk_args_for_protected_path() {
    for arg in "${ARGV[@]:1}"; do
        if _is_protected_path "$arg"; then
            block "write to protected path ($arg)"
        fi
    done
}

case "$CMD0" in
    rm|mv|cp|tee|truncate|chmod|chown|ln|dd|install|rsync|sed)
        _walk_args_for_protected_path
        ;;
esac

# ---- 6. Shell redirects into protected paths ---------------------------------
# Even if the leading command is benign (echo, cat, printf), a redirect can
# clobber a protected file.
#
# TAP-2344: anchor .ralph/ redirects to the project prefix so the global
# `~/.ralph/` install isn't caught. .claude/ remains globally blocked
# (see _is_protected_path above for the rationale).
#
# Canonicalize every redirect operator (`>`, `>>`, `>|`, fd-numbered `1>`,
# `&>`, and no-space forms like `x>.ralph/y`) to a single ` > ` token so the
# space-anchored patterns below can't be bypassed. Without this, `echo x
# >.ralph/status.json` (no space) and `1>`/`&>`/`>|` variants slipped past.
RNORM=$(printf '%s' "$NORM" | sed -E 's/[0-9]*&?>{1,2}[|]?/ > /g' | tr -s ' ')
case "$RNORM" in
    *" > $RALPH_DIR/"*) block "redirect into project .ralph/" ;;
    *" > .ralph/"*|*" > ./.ralph/"*)
        block "redirect into .ralph/" ;;
esac
# TAP-2345 (F4): allow redirects into .claude/rules/, .claude/skills/,
# .claude/commands/ — those are routine agent surface area and the
# Edit-side hook already allows them. Settings, agents/, hooks/ stay
# blocked. The carve-out cases must precede the catch-all so they win.
case "$RNORM" in
    *" > .claude/rules/"*|*" > ./.claude/rules/"*) ;;
    *" > .claude/skills/"*|*" > ./.claude/skills/"*) ;;
    *" > .claude/commands/"*|*" > ./.claude/commands/"*) ;;
    *" > .claude/"*|*" > ./.claude/"*)
        block "redirect into .claude/" ;;
esac
case "$RNORM" in
    *" > $_proj_dir/.ralphrc"*) block "redirect into project .ralphrc" ;;
    *" > .ralphrc"*) block "redirect into .ralphrc" ;;
esac

exit 0
