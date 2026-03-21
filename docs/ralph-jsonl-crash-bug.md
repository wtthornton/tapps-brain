# Ralph Bug: Live Mode JSONL Crash in Response Analyzer

**Severity:** Critical (silent loop termination, no error logged)
**Affected component:** `~/.ralph/lib/response_analyzer.sh` (`parse_json_response`) and
`~/.ralph/ralph_loop.sh` (stream extraction, lines ~1341-1382)
**Versions tested:** Ralph with Claude CLI 2.1.80, WSL2 on Windows 11

---

## Summary

In live mode (`--live`), Ralph's response analysis crashes silently when the
stream-to-JSON extraction step fails to run. The output file remains as raw
JSONL (one JSON object per line), but `parse_json_response` only handles single
objects or arrays. Every `jq` field extraction returns N lines instead of 1,
corrupting all downstream variables and causing bash arithmetic errors that
terminate the loop without any logged error.

## Reproduction

1. Run `ralph --live` on any project with tasks in `fix_plan.md`
2. Wait for Claude to complete a task successfully
3. Observe: Ralph logs "Analyzing Claude Code response..." then exits to shell
4. No "Completed Loop #N" entry appears in `ralph.log`
5. No error message is logged

The bug is intermittent -- it depends on whether the stream extraction step
at lines 1341-1382 in `ralph_loop.sh` successfully runs.

## Root Cause Analysis

### Two-layer failure

**Layer 1: Stream extraction silently skips (ralph_loop.sh ~1341)**

In live mode, Claude CLI runs with `--output-format stream-json`, producing
JSONL output (one JSON object per line per event). After the pipeline finishes,
extraction code is supposed to:

1. Back up the full stream to `_stream.log`
2. Extract the single `"type":"result"` line via `grep`
3. Overwrite the output file with just that one JSON object

```bash
# ralph_loop.sh line ~1341
if [[ -f "$output_file" ]]; then
    local stream_output_file="${output_file%.log}_stream.log"
    cp "$output_file" "$stream_output_file"
    local result_line=$(grep -E '"type"[[:space:]]*:[[:space:]]*"result"' "$output_file" | tail -1)
    if [[ -n "$result_line" ]]; then
        if echo "$result_line" | jq -e . >/dev/null 2>&1; then
            echo "$result_line" > "$output_file"
            log_status "INFO" "Extracted and validated session data from stream output"
        fi
    fi
fi
```

In the failing session, neither "Extracted and validated..." nor any fallback
warning appeared in `ralph.log`. The `_stream.log` backup file was never
created. Yet the output file exists at 585KB (1466 JSON objects). This means
`[[ -f "$output_file" ]]` returned false despite the file existing.

**Suspected cause:** On WSL2 with NTFS mounts (`/mnt/c/...`), file visibility
can lag behind pipeline completion. The `tee` command in the pipeline writes
through NTFS, and the `-f` test may race against filesystem flush. The
`stdbuf -oL` mitigates this for data integrity but not for metadata (inode)
visibility on cross-filesystem mounts.

**Layer 2: parse_json_response crashes on JSONL input (response_analyzer.sh)**

When the output file is still raw JSONL, `parse_json_response` processes it
incorrectly:

```
Input:  1466 JSON objects, one per line (JSONL/stream-json format)
Expected: Single JSON object or JSON array
```

**Step-by-step crash trace:**

| Line | Code | Result on JSONL |
|------|------|-----------------|
| 100 | `jq empty "$output_file"` | PASSES -- jq validates each object |
| 107 | `jq -e 'type == "array"'` | FALSE -- each object is type "object" |
| 146 | `jq -r '.status // "UNKNOWN"'` | Returns 1466 lines of values |
| 154 | `jq -r '.exit_signal // false'` | Returns 1466 lines of "false" |
| 192 | `jq -r '.files_modified // 0'` | Returns 1466 lines of "0" |
| 199 | `jq -r '.error_count // 0'` | Returns 1466 lines of "0" |
| 215 | `jq -r '.confidence // 0'` | Returns 1466 lines of "0" |

All variables now contain multi-line strings (1466 lines each).

**First crash point -- bash arithmetic (line 223):**

```bash
permission_denial_count=$((permission_denial_count + 0))
```

With `permission_denial_count` containing `"0\n0\n0\n..."` (1466 lines):

```
bash: 0
0
0: syntax error in expression (error token is "
0
0")
```

Without `set -e`, bash prints to stderr and continues with a corrupted value.
Additional arithmetic operations crash at lines 259, 265, 268, 278, 281.

**Second crash point -- jq --argjson (lines 286-320):**

```bash
jq -n \
    --argjson exit_signal "$exit_signal" \
    --argjson is_test_only "$is_test_only" \
    ...
    '{ ... }' > "$result_file"
```

`--argjson` requires valid JSON. A 1466-line string is not valid JSON:

```
jq: invalid JSON text passed to --argjson
```

The `jq -n` command fails, writing nothing to `$result_file`. But the function
ends with `return 0` regardless, so the caller thinks it succeeded.

**Back in analyze_response (line ~360):**

```bash
if parse_json_response "$output_file" "$RALPH_DIR/.json_parse_result" 2>/dev/null; then
    # Enters this block because return 0
    json_confidence=$(jq -r '.confidence' $RALPH_DIR/.json_parse_result 2>/dev/null || echo "0")
    confidence_score=$((json_confidence + 50))  # May crash if .json_parse_result is corrupt
```

The cascade continues until the bash process accumulates enough corrupted state
to terminate silently.

## Evidence from Logs

**Successful session (2026-03-20 16:46):**
```
[16:46:40] [INFO] Extracted and validated session data from stream output
[16:46:40] [SUCCESS] Claude Code execution completed successfully
[16:46:40] [INFO] Analyzing Claude Code response...
[16:46:46] [LOOP] === Completed Loop #7 ===
[16:46:46] [INFO] Loop #8 - calling init_call_tracking...
```

Stream backup exists: `claude_output_2026-03-20_16-46-47_stream.log` (1028KB)

**Failing session (2026-03-21 07:58):**
```
[08:05:00] [SUCCESS] Claude Code execution completed successfully
[08:05:00] [INFO] Analyzing Claude Code response...
[08:08:54] [INFO] Loaded configuration from .ralphrc    <-- NEW session, user restarted
```

No "Extracted and validated" message. No `_stream.log` backup.
No "Completed Loop" entry. No error logged. Script silently died.

**Output file analysis:**
```
File: claude_output_2026-03-21_07-58-40.log
Size: 585,030 bytes
Lines: 1,466 (JSONL, not extracted to single object)
Types: stream_event (1385), assistant (47), user (31), system (1), result (1), rate_limit_event (1)
```

## Proposed Fix

### Fix 1: Add JSONL detection to parse_json_response (critical)

Before field extraction, detect JSONL and extract only the result object:

```bash
# response_analyzer.sh -- add after line 107 (array check), before field extractions

# Check if file is JSONL (multiple JSON objects, one per line)
# jq processes each object; if line count > 1, it's JSONL (stream-json format)
local line_count=$(wc -l < "$output_file" 2>/dev/null || echo "1")
if [[ $line_count -gt 1 ]]; then
    # JSONL detected -- extract the "result" type message for analysis
    normalized_file=$(mktemp)

    local result_obj
    result_obj=$(jq -c 'select(.type == "result")' "$output_file" 2>/dev/null | tail -1)

    if [[ -n "$result_obj" ]]; then
        echo "$result_obj" > "$normalized_file"
        output_file="$normalized_file"
        [[ "${VERBOSE_PROGRESS:-}" == "true" ]] && \
            echo "DEBUG: JSONL detected ($line_count lines), extracted result object" >&2
    else
        # No result message found -- try to build a minimal object from what we have
        echo '{}' > "$normalized_file"
        output_file="$normalized_file"
        log_status "WARN" "JSONL detected but no result object found"
    fi
fi
```

### Fix 2: Make parse_json_response return code reflect actual success (important)

Change the hardcoded `return 0` to reflect whether the jq construction succeeded:

```bash
# response_analyzer.sh -- replace lines ~286-327

# Write normalized result using jq for safe JSON construction
jq -n \
    --arg status "$status" \
    --argjson exit_signal "$exit_signal" \
    ... \
    '{ ... }' > "$result_file"

# Check if write succeeded
if [[ $? -ne 0 || ! -s "$result_file" ]]; then
    [[ -n "$normalized_file" && -f "$normalized_file" ]] && rm -f "$normalized_file"
    return 1
fi

# Cleanup temporary normalized file if created
[[ -n "$normalized_file" && -f "$normalized_file" ]] && rm -f "$normalized_file"

return 0
```

### Fix 3: Add filesystem sync before extraction check (defensive)

Mitigate the WSL/NTFS race condition in the stream extraction code:

```bash
# ralph_loop.sh -- add before line ~1341 (the if [[ -f "$output_file" ]] check)

# Ensure filesystem has flushed the tee output (WSL/NTFS mount race condition)
sync 2>/dev/null || true
sleep 0.2  # Brief pause for NTFS metadata propagation
```

### Fix 4: Add fallback JSONL handling in stream extraction (belt and suspenders)

If the extraction step fails to convert the file, add a second pass before
analysis:

```bash
# ralph_loop.sh -- add after line ~1380, before the common success path

# Safety check: if output_file is still JSONL after extraction, fix it now
if [[ -f "$output_file" ]]; then
    local file_lines
    file_lines=$(wc -l < "$output_file" 2>/dev/null || echo "1")
    if [[ $file_lines -gt 5 ]]; then
        # Still JSONL -- extract result line as last resort
        local emergency_result
        emergency_result=$(grep -E '"type"[[:space:]]*:[[:space:]]*"result"' "$output_file" 2>/dev/null | tail -1)
        if [[ -n "$emergency_result" ]] && echo "$emergency_result" | jq -e . >/dev/null 2>&1; then
            # Back up stream first
            local backup="${output_file%.log}_stream.log"
            [[ ! -f "$backup" ]] && cp "$output_file" "$backup"
            echo "$emergency_result" > "$output_file"
            log_status "WARN" "Emergency JSONL extraction: converted $file_lines-line stream to single result object"
        fi
    fi
fi
```

## Priority

| Fix | Severity | Effort | Impact |
|-----|----------|--------|--------|
| Fix 1 (JSONL detection in parser) | Critical | Small | Prevents crash entirely |
| Fix 2 (return code accuracy) | Important | Trivial | Enables proper error handling |
| Fix 3 (filesystem sync) | Defensive | Trivial | Reduces WSL race window |
| Fix 4 (fallback extraction) | Defensive | Small | Catches edge cases Fix 3 misses |

Fix 1 alone resolves the crash. Fixes 2-4 are defense-in-depth.

## Test Plan

1. Create a mock JSONL file with ~100 stream events + 1 result object
2. Run `parse_json_response` against it -- verify it extracts only the result
3. Run `analyze_response` against it -- verify correct exit_signal, status, etc.
4. Run Ralph `--live` for 3+ consecutive loops -- verify no silent termination
5. Verify `_stream.log` backups are always created in live mode
6. Test on both native Linux and WSL2 with NTFS mount

## References

- Ralph source: `~/.ralph/ralph_loop.sh` (stream extraction lines 1296-1382)
- Ralph source: `~/.ralph/lib/response_analyzer.sh` (parser lines 82-330)
- Related issues: #134 (is_error:true), #141 (file change detection), #190 (stderr corruption), #198 (timeout fallback)
