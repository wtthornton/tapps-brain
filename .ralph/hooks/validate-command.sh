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

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
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
    fi

    # `git reset --hard`, `git clean -f*`, `git rm`
    case "$REST" in
        *" clean "*|*" rm "*|*" reset --hard"*|*" reset --hard "*)
            block "destructive git subcommand" ;;
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

case "$CMD0" in
    python|python3|perl|ruby|node|bash|sh|zsh)
        for arg in "${ARGV[@]:1}"; do
            case "$arg" in
                -c|-e) block "$CMD0 $arg script-execution not allowed" ;;
            esac
        done
        ;;
esac

# ---- 5. Write-capable tools hitting protected paths --------------------------

_is_protected_path() {
    # Any .ralph/ or .claude/ or .ralphrc path, no exemptions — at the shell
    # layer we can't prove the write is a narrow checkbox update the way the
    # Edit-tool hook (protect-ralph-files.sh) can. If Ralph wants to tick off
    # fix_plan.md it must go through the Edit tool.
    local p="$1"
    p="${p%\"}"; p="${p#\"}"; p="${p%\'}"; p="${p#\'}"
    case "$p" in
        .ralph|.ralph/*|./.ralph/*|*/.ralph/*) return 0 ;;
        .ralphrc|./.ralphrc|*/.ralphrc) return 0 ;;
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
case "$NORM" in
    *" > .ralph/"*|*" >> .ralph/"*|*" > ./.ralph/"*|*" >> ./.ralph/"*)
        block "redirect into .ralph/" ;;
esac
case "$NORM" in
    *" > .claude/"*|*" >> .claude/"*|*" > ./.claude/"*|*" >> ./.claude/"*)
        block "redirect into .claude/" ;;
esac
case "$NORM" in
    *" > .ralphrc"*|*" >> .ralphrc"*) block "redirect into .ralphrc" ;;
esac

exit 0
