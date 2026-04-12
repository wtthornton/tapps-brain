#!/bin/bash
# .ralph/hooks/on-task-completed.sh
# TaskCompleted hook. Fires when a task is marked complete.
#
# Exit 0 = allow completion
# Exit 2 = prevent completion (e.g., validation failed)

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"
[[ -d "$RALPH_DIR" ]] || exit 0

INPUT=$(cat)
task_description=$(echo "$INPUT" | jq -r '.task_description // "unknown"' 2>/dev/null || echo "unknown")

# Log the completion
echo "[$(date '+%H:%M:%S')] TASK COMPLETED: $task_description" \
  >> "$RALPH_DIR/live.log"

# PLANOPT: Incremental import graph invalidation for modified source files
# Source import_graph.sh from Ralph installation for invalidate_file function
if [[ -f "$RALPH_DIR/.import_graph.json" && -f "$RALPH_DIR/.files_modified_this_loop" ]]; then
  _ig_lib=""
  for _lib_dir in "$HOME/.ralph/lib" "${RALPH_INSTALL_DIR:-/nonexistent}/lib"; do
    [[ -f "$_lib_dir/import_graph.sh" ]] && _ig_lib="$_lib_dir/import_graph.sh" && break
  done

  if [[ -n "$_ig_lib" ]]; then
    source "$_ig_lib"
    _ig_count=0
    while IFS= read -r _modified_file; do
      [[ -z "$_modified_file" ]] && continue
      # Only invalidate source files (not config, docs, etc.)
      if echo "$_modified_file" | grep -qE '\.(py|ts|tsx|js|jsx|sh)$'; then
        if import_graph_invalidate_file "$_modified_file" "$RALPH_DIR/.import_graph.json" 2>/dev/null; then
          _ig_count=$((_ig_count + 1))
        fi
      fi
    done < "$RALPH_DIR/.files_modified_this_loop"
    # Log import graph invalidation to ralph.log
    if [[ $_ig_count -gt 0 ]]; then
      _ralph_log="$RALPH_DIR/logs/ralph.log"
      [[ -f "$_ralph_log" ]] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] on-task-completed: Invalidated $_ig_count file(s) in import graph" >> "$_ralph_log"
    fi
  fi
fi

# Allow completion
exit 0
