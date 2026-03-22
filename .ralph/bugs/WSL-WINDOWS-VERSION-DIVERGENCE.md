# Bug: WSL and Windows Ralph installs diverge after update — causes silent loop crash

**Discovered:** 2026-03-21
**Resolved:** 2026-03-21 (Ralph v1.1.0)
**Severity:** Critical — Ralph silently stops after 1 task with no error in ralph.log
**Affects:** Any user running Ralph from WSL on a Windows host
**Status:** RESOLVED — Fixed in Ralph v1.1.0 with `check_version_divergence()` startup check, on-stop.sh text fallback, and both WSL/Windows installs synced to v1.1.0.

## Symptoms

- Ralph completes one task successfully, logs `✅ Claude Code execution completed successfully`, then stops
- No error message in `ralph.log` — log ends abruptly after the success line
- `status.json` shows `status: "UNKNOWN"` (hook parsing failed)
- No `.response_analysis` file created
- No crash code file (process exits 0)
- No additional `claude_output_*.log` files — Loop 2 never starts

## Root Cause

Two independent copies of `ralph_loop.sh` exist and diverge silently:

| Location | Version | Analysis Method |
|----------|---------|-----------------|
| `C:\Users\<user>\.ralph\ralph_loop.sh` | New (2544 lines) | `on-stop.sh` hook → `status.json` |
| `~/.ralph/ralph_loop.sh` (WSL) | Old (2174 lines) | `lib/response_analyzer.sh` → `.response_analysis` |

Ralph runs from **WSL** (`~/.ralph/ralph_loop.sh`). When the Windows copy was upgraded to hook-based analysis, the WSL copy was left behind with the old `response_analyzer.sh` approach.

### Failure chain

1. Claude Code runs with `--output-format stream-json` (live mode), producing a JSONL stream file (thousands of JSON lines)
2. Old `analyze_response()` calls `detect_output_format()` → `jq empty "$output_file"` on the JSONL file
3. **JSONL is not valid JSON** — `jq empty` fails, function returns `"text"` format
4. Text-mode grep parsing on raw JSONL produces garbage or empty results
5. `analyze_response()` crashes or returns non-zero; `.response_analysis` never created
6. `ralph_loop.sh` dies during analysis — the next `log_status` call never executes
7. Cleanup trap fires with exit code 0 (subprocess failure, not main process), no crash logged

Meanwhile, the `.claude/settings.json` Stop hook (`on-stop.sh`) fires independently but the old `ralph_loop.sh` never reads `status.json` — it only reads `.response_analysis`.

### Secondary issue: on-stop.sh hook parsing

The `on-stop.sh` hook also fails to extract response text. It tries JSON paths (`.result`, `.content`, `.result.text`, `.message.content`) on the Stop event stdin payload, but Claude Code agent mode sends data in a format that doesn't match any of these paths. Result: `status: "UNKNOWN"` in `status.json`.

## Workaround

Sync the Windows Ralph install to WSL manually:

```bash
# Backup
cp ~/.ralph/ralph_loop.sh ~/.ralph/ralph_loop.sh.bak

# Copy from Windows
cp /mnt/c/Users/<user>/.ralph/ralph_loop.sh ~/.ralph/ralph_loop.sh
chmod +x ~/.ralph/ralph_loop.sh

# Fix CRLF
sed -i 's/\r$//' ~/.ralph/ralph_loop.sh

# Also fix hook CRLF if needed
find /mnt/c/<project>/.ralph/hooks/ -name '*.sh' -exec sed -i 's/\r$//' {} \;
```

## Suggested Fix (for Ralph project)

1. **Version stamp**: Add a `RALPH_LOOP_VERSION=` line to `ralph_loop.sh`. At startup, compare WSL and Windows versions; warn if they differ.

2. **JSONL-safe detection**: In `detect_output_format()`, check for JSONL before calling `jq empty`:
   ```bash
   # Count lines — JSONL has many top-level JSON objects
   local line_count=$(wc -l < "$output_file")
   if [[ $line_count -gt 10 ]]; then
     echo "jsonl"
     return
   fi
   ```

3. **Sync on update**: When Ralph updates `ralph_loop.sh` on one platform, detect the other install and offer to sync.

4. **Hook fallback**: In `on-stop.sh`, fall back to treating raw stdin as text if no JSON path matches (already applied locally in this fix).
