#!/bin/bash
# .ralph/hooks/on-session-start.sh
# Replaces: build_loop_context() in ralph_loop.sh
#
# SessionStart hook. Reads loop state and emits context for Claude's system prompt.
# PLANOPT: Also runs plan optimization (section-level, async graph, batch hints).
# Exit 0 = allow session. stderr = inject into context.

set -euo pipefail

RALPH_DIR="${CLAUDE_PROJECT_DIR:-.}/.ralph"

# Guard: only run if this is a Ralph-managed project
if [[ ! -d "$RALPH_DIR" ]]; then
  exit 0
fi

# --- Source Ralph libraries ---
# Libraries live in the Ralph installation, not the project
RALPH_LIB=""
for _lib_dir in "$HOME/.ralph/lib" "${RALPH_INSTALL_DIR:-/nonexistent}/lib"; do
  if [[ -d "$_lib_dir" ]]; then
    RALPH_LIB="$_lib_dir"
    break
  fi
done

# --- Read loop state ---
loop_count=0
last_status=""
if [[ -f "$RALPH_DIR/status.json" ]]; then
  loop_count=$(jq -r '.loop_count // 0' "$RALPH_DIR/status.json" 2>/dev/null || echo "0")
  last_status=$(jq -r '.status // ""' "$RALPH_DIR/status.json" 2>/dev/null || echo "")
fi

# --- Read fix_plan completion status ---
total_tasks=0
done_tasks=0
FIX_PLAN="$RALPH_DIR/fix_plan.md"
if [[ -f "$FIX_PLAN" ]]; then
  total_tasks=$(grep -c '^\- \[' "$FIX_PLAN" 2>/dev/null) || total_tasks=0
  done_tasks=$(grep -c '^\- \[x\]' "$FIX_PLAN" 2>/dev/null) || done_tasks=0
fi
remaining=$((total_tasks - done_tasks))

# --- Read circuit breaker state ---
cb_state="CLOSED"
if [[ -f "$RALPH_DIR/.circuit_breaker_state" ]]; then
  cb_state=$(jq -r '.state // "CLOSED"' "$RALPH_DIR/.circuit_breaker_state" 2>/dev/null || echo "CLOSED")
fi

# --- PLANOPT: Plan optimization ---
NO_OPTIMIZE="${RALPH_NO_OPTIMIZE:-false}"
DRY_RUN="${DRY_RUN:-false}"
plan_optimized=false
batch_annotation=""

if [[ -f "$FIX_PLAN" && -n "$RALPH_LIB" && "$NO_OPTIMIZE" != "true" && "$DRY_RUN" != "true" && $remaining -gt 1 ]]; then
  PLAN_OPTIMIZER="$RALPH_LIB/plan_optimizer.sh"
  IMPORT_GRAPH_LIB="$RALPH_LIB/import_graph.sh"
  HASH_FILE="$RALPH_DIR/.plan_section_hashes"

  if [[ -f "$PLAN_OPTIMIZER" ]]; then
    source "$PLAN_OPTIMIZER"

    # Ensure import graph is available (async rebuild if stale)
    if [[ -f "$IMPORT_GRAPH_LIB" ]]; then
      source "$IMPORT_GRAPH_LIB"
      if import_graph_is_stale "${CLAUDE_PROJECT_DIR:-.}" "$RALPH_DIR/.import_graph.json" 2>/dev/null; then
        # Async rebuild — use stale cache for this loop
        local _ralph_log_ig="$RALPH_DIR/logs/ralph.log"
        [[ -f "$_ralph_log_ig" ]] && echo "[$(date '+%Y-%m-%d %H:%M:%S')] [INFO] PLANOPT: Import graph stale — triggering async rebuild" >> "$_ralph_log_ig"
        import_graph_build_async "${CLAUDE_PROJECT_DIR:-.}" "$RALPH_DIR/.import_graph.json" 2>/dev/null || true
      fi
    fi

    # Check which sections changed (section-level hashing of unchecked lines only)
    changed_sections=""
    if declare -f plan_changed_sections &>/dev/null; then
      changed_sections=$(plan_changed_sections "$FIX_PLAN" "$HASH_FILE" 2>/dev/null || echo "")
    fi

    if [[ -n "$changed_sections" ]]; then
      # Log optimization start to Ralph log (visible in terminal)
      local _ralph_log="$RALPH_DIR/logs/ralph.log"
      local _ts
      _ts=$(date '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "")
      [[ -f "$_ralph_log" ]] && echo "[$_ts] [INFO] PLANOPT: Optimizing fix_plan.md (changed sections detected)" >> "$_ralph_log"

      # Run optimizer (non-fatal — never block the loop)
      if plan_optimize_section "$FIX_PLAN" "${CLAUDE_PROJECT_DIR:-.}" "$RALPH_DIR/.import_graph.json" 2>/dev/null; then
        plan_optimized=true
        [[ -f "$_ralph_log" ]] && echo "[$_ts] [INFO] PLANOPT: Fix plan reordered (dependency order + module locality)" >> "$_ralph_log"
        # Update section hashes to reflect optimized plan
        if declare -f plan_section_hashes &>/dev/null; then
          plan_section_hashes "$FIX_PLAN" > "$HASH_FILE" 2>/dev/null || true
        fi
      else
        [[ -f "$_ralph_log" ]] && echo "[$_ts] [WARN] PLANOPT: Optimization failed (non-fatal, continuing)" >> "$_ralph_log"
      fi
    fi

    # Generate batch annotation for context injection
    if declare -f plan_annotate_batches &>/dev/null && declare -f plan_parse_tasks &>/dev/null; then
      _tasks_json=$(plan_parse_tasks "$FIX_PLAN" 2>/dev/null || echo "[]")
      batch_annotation=$(plan_annotate_batches "$_tasks_json" 2>/dev/null || echo "")
    fi
  fi
fi

# --- Clear per-loop file tracking ---
: > "$RALPH_DIR/.files_modified_this_loop" 2>/dev/null || true

# --- Emit context to stderr (injected into Claude's system prompt) ---
if [[ $total_tasks -gt 0 && $remaining -eq 0 ]]; then
  cat >&2 <<EOF
Ralph loop #$((loop_count + 1)). Tasks: $done_tasks/$total_tasks complete, 0 remaining.
Circuit breaker: $cb_state.$([ -n "$last_status" ] && echo " Last loop: $last_status.")
ALL TASKS COMPLETE. Do NOT run tests, lint, or any verification. Emit your RALPH_STATUS block with STATUS: COMPLETE, TASKS_COMPLETED_THIS_LOOP: 0, TESTS_STATUS: PASSING, EXIT_SIGNAL: true, and STOP immediately.
EOF
else
  # PLANOPT: Progress re-grounding (Reflexion pattern)
  last_completed=""
  if [[ -f "$FIX_PLAN" ]]; then
    last_completed=$(grep -E '^\- \[x\]' "$FIX_PLAN" | tail -1 | sed 's/^- \[x\] //' | head -c 80)
  fi

  cat >&2 <<EOF
Ralph loop #$((loop_count + 1)). Tasks: $done_tasks/$total_tasks complete, $remaining remaining.
Circuit breaker: $cb_state.$([ -n "$last_status" ] && echo " Last loop: $last_status.")$([ -n "$last_completed" ] && echo " Last completed: $last_completed.")$([ "$plan_optimized" = "true" ] && echo " Fix plan re-optimized (tasks reordered for dependency order and module locality).")$([ -n "$batch_annotation" ] && echo " Batch hint: $batch_annotation")
Read .ralph/fix_plan.md and do the FIRST unchecked item. IMPORTANT: Only run tests at epic boundaries (last task in a ## section). Otherwise set TESTS_STATUS: DEFERRED — do NOT run any test commands.
EOF

  # CTXMGMT-2: Check if current task needs decomposition
  if [[ "${RALPH_PROGRESSIVE_CONTEXT:-false}" == "true" ]]; then
    _ctx_lib_loaded=false
    for _lib_path in "$HOME/.ralph/lib/context_management.sh" \
                     "${RALPH_INSTALL_DIR:-/nonexistent}/lib/context_management.sh"; do
      if [[ -f "$_lib_path" ]]; then
        source "$_lib_path"
        _ctx_lib_loaded=true
        break
      fi
    done

    if [[ "$_ctx_lib_loaded" == "true" ]] && declare -f ralph_detect_decomposition_needed &>/dev/null; then
      current_task=$(grep -m1 -E '^\s*- \[ \]' "$RALPH_DIR/fix_plan.md" 2>/dev/null || echo "")
      if [[ -n "$current_task" ]]; then
        decomp_result=$(ralph_detect_decomposition_needed "$current_task" "${loop_count:-0}" 2>/dev/null || echo '{"decompose":false}')
        if echo "$decomp_result" | jq -r '.decompose' 2>/dev/null | grep -q "true"; then
          reasons=$(echo "$decomp_result" | jq -r '.reasons // ""' 2>/dev/null)
          echo "" >&2
          echo "DECOMPOSITION SIGNAL: Current task may be too large ($reasons). Consider breaking into sub-tasks." >&2
        fi
      fi
    fi
  fi
fi

exit 0
