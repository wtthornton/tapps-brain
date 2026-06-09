#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.16
# TappsMCP Stop hook — TAP-1326 / TAP-1327 (+ completion-gate warn-mode for non-Ralph)
# Phase 1 (always when transcript exists): scan tool calls, write loop-metrics.jsonl
#   + write .tapps-mcp/.completion-gate-violations.jsonl when files were edited
#   without tapps_validate_changed / tapps_quality_gate / tapps_checklist (warn-mode telemetry).
# Phase 2 (Ralph only): override status.json EXIT_SIGNAL=true → false when gates skipped,
#   log to .ralph/logs/on-stop.log, append note to .ralph/PROMPT.md.
# Phase 3 (always): conditional reminder to stderr — only fires when violations detected.
# IMPORTANT: Must check stop_hook_active to prevent infinite loops.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
PARSED=$(echo "$INPUT" | "$PYBIN" -c   "import sys,json
try:
    d=json.load(sys.stdin)
    print(d.get('stop_hook_active','false'))
    print(d.get('transcript_path',''))
except Exception:
    print('false'); print('')" 2>/dev/null)
ACTIVE=$(echo "$PARSED" | sed -n '1p')
TRANSCRIPT=$(echo "$PARSED" | sed -n '2p')
if [ "$ACTIVE" = "True" ] || [ "$ACTIVE" = "true" ]; then
  exit 0
fi
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
STATUS_JSON="$PROJECT_DIR/.ralph/status.json"
EDITS_FILE="$PROJECT_DIR/.ralph/.edits_this_loop"
LOG_DIR="$PROJECT_DIR/.ralph/logs"
PROMPT_FILE="$PROJECT_DIR/.ralph/PROMPT.md"
RALPH_MODE="false"
EXIT_SIGNAL="false"
if [ -f "$STATUS_JSON" ]; then
  RALPH_MODE="true"
  EXIT_SIGNAL=$("$PYBIN" -c "
import json
try:
    d=json.load(open('$STATUS_JSON'))
    print('true' if d.get('EXIT_SIGNAL') is True or str(d.get('EXIT_SIGNAL','')).lower()=='true' else 'false')
except Exception:
    print('false')" 2>/dev/null)
fi
# Phase 1: ALWAYS scan transcript (warn-mode telemetry + loop-metrics, for Ralph and non-Ralph).
GATE_REPORT=""
if [ -n "$TRANSCRIPT" ] && [ -f "$TRANSCRIPT" ]; then
  GATE_REPORT=$("$PYBIN" - <<PYEOF 2>/dev/null
import json,os,time
transcript='$TRANSCRIPT'
edits_file='$EDITS_FILE'
project_dir='$PROJECT_DIR'
ralph_mode = '$RALPH_MODE' == 'true'
gate_tools={'tapps_quick_check','tapps_validate_changed','tapps_quality_gate',
            'mcp__tapps-mcp__tapps_quick_check','mcp__tapps-mcp__tapps_validate_changed',
            'mcp__tapps-mcp__tapps_quality_gate','mcp__tapps-quality__tapps_quick_check',
            'mcp__tapps-quality__tapps_validate_changed','mcp__tapps-quality__tapps_quality_gate'}
checklist_tools={'tapps_checklist','mcp__tapps-mcp__tapps_checklist','mcp__tapps-quality__tapps_checklist'}
lookup_tools={'tapps_lookup_docs','mcp__tapps-mcp__tapps_lookup_docs','mcp__tapps-quality__tapps_lookup_docs'}
edit_tools={'Edit','Write','MultiEdit','NotebookEdit'}
mcp_calls=0
gate_called=False
checklist_called=False
lookup_called=False
tools_used=set()
edited_from_transcript=[]
try:
    with open(transcript) as fh:
        for line in fh:
            try: row=json.loads(line)
            except Exception: continue
            msg=row.get('message') or {}
            for blk in (msg.get('content') or []):
                if not isinstance(blk,dict): continue
                if blk.get('type')!='tool_use': continue
                name=blk.get('name','')
                tools_used.add(name)
                if name.startswith('mcp__'): mcp_calls+=1
                if name in gate_tools: gate_called=True
                if name in checklist_tools: checklist_called=True
                if name in lookup_tools: lookup_called=True
                if name in edit_tools:
                    fp=(blk.get('input') or {}).get('file_path','')
                    if fp: edited_from_transcript.append(fp)
except Exception:
    pass
edits=[]
if ralph_mode and os.path.exists(edits_file):
    with open(edits_file) as fh:
        edits=[ln.strip() for ln in fh if ln.strip()]
else:
    seen=set(); edits=[p for p in edited_from_transcript if not (p in seen or seen.add(p))]
needs_gate=any(p.endswith(('.py','.pyi','.ts','.tsx','.js','.jsx','.go','.rs')) for p in edits)
miss=[]
gate_skipped=[]
if needs_gate and not gate_called:
    miss.append('QUALITY_GATE_SKIP:'+','.join(edits[:8]))
    gate_skipped=edits
# CHECKLIST_MISSING fires only when files were edited (was unconditional pre-uplift).
if needs_gate and not checklist_called:
    miss.append('CHECKLIST_MISSING')
# TAP-1333: append per-loop telemetry (rotates at 10 MB). ALWAYS write.
metrics_dir=os.path.join(project_dir,'.tapps-mcp')
try:
    os.makedirs(metrics_dir,exist_ok=True)
    metrics_path=os.path.join(metrics_dir,'loop-metrics.jsonl')
    if os.path.exists(metrics_path) and os.path.getsize(metrics_path) > 10*1024*1024:
        os.replace(metrics_path, metrics_path + '.1')
    with open(metrics_path,'a') as fh:
        fh.write(json.dumps({
            'ts': int(time.time()),
            'files_edited': edits,
            'mcp_calls': mcp_calls,
            'gate_skipped_files': gate_skipped,
            'lookup_docs_called': lookup_called,
            'checklist_called': checklist_called,
            'ralph_mode': ralph_mode,
            'tools_used': sorted(tools_used)[:50],
        }) + '\n')
except Exception:
    pass
# Warn-mode completion-gate violation log (only on miss). Mirrors .cache-gate-violations.jsonl.
if miss:
    try:
        violations_path=os.path.join(metrics_dir,'.completion-gate-violations.jsonl')
        if os.path.exists(violations_path) and os.path.getsize(violations_path) > 10*1024*1024:
            os.replace(violations_path, violations_path + '.1')
        with open(violations_path,'a') as fh:
            fh.write(json.dumps({
                'ts': int(time.time()),
                'mode': 'warn',
                'reasons': miss,
                'files_edited': edits[:16],
                'ralph_mode': ralph_mode,
            }) + '\n')
    except Exception:
        pass
print('|'.join(miss))
PYEOF
)
fi
# Phase 2: Ralph-only EXIT_SIGNAL override when violations detected.
if [ "$RALPH_MODE" = "true" ] && [ "$EXIT_SIGNAL" = "true" ] && [ -n "$GATE_REPORT" ]; then
  mkdir -p "$LOG_DIR" 2>/dev/null
  TS=$(date -u +%FT%TZ)
  echo "{\"ts\":\"$TS\",\"override\":\"EXIT_SIGNAL\",\"reasons\":\"$GATE_REPORT\"}" \
    >> "$LOG_DIR/on-stop.log" 2>/dev/null
  "$PYBIN" - <<PYEOF 2>/dev/null
import json
try:
    p='$STATUS_JSON'
    d=json.load(open(p))
    d['EXIT_SIGNAL']=False
    d['exit_signal_override']='$GATE_REPORT'
    json.dump(d,open(p,'w'),indent=2)
except Exception:
    pass
PYEOF
  if [ -f "$PROMPT_FILE" ]; then
    {
      echo ""
      echo "<!-- TappsMCP override $TS: EXIT_SIGNAL forced false. Run missing gates next loop: $GATE_REPORT -->"
    } >> "$PROMPT_FILE" 2>/dev/null
  fi
  cat >&2 <<MSG
TappsMCP: EXIT_SIGNAL=true overridden to false — required gates skipped:
  $GATE_REPORT
Run tapps_quick_check / tapps_validate_changed and tapps_checklist before retrying.
See .claude/rules/tapps-pipeline.md.
MSG
fi
# Phase 2.5: Ralph cleanup of edits_this_loop (independent of EXIT_SIGNAL).
if [ "$RALPH_MODE" = "true" ]; then
  : > "$EDITS_FILE" 2>/dev/null
fi
PROGRESS="$PROJECT_DIR/.tapps-mcp/.validation-progress.json"
if [ -f "$PROGRESS" ]; then
  SUMMARY=$("$PYBIN" -c "
import json
try:
    d=json.load(open('$PROGRESS'))
    if d.get('status')=='completed':
        t=d.get('total',0);p=sum(1 for r in d.get('results',[]) if r.get('gate_passed'))
        f=t-p;gp='all passed' if d.get('all_gates_passed') else f'{f} failed'
        print(f'Last validation: {t} files, {gp}')
except Exception:
    pass
" 2>/dev/null)
  if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY" >&2
    exit 0
  fi
fi
REPORT_PROGRESS="$PROJECT_DIR/.tapps-mcp/.report-progress.json"
if [ -f "$REPORT_PROGRESS" ]; then
  REPORT_SUMMARY=$("$PYBIN" -c "
import json
try:
    d=json.load(open('$REPORT_PROGRESS'))
    if d.get('status')=='completed':
        results=d.get('results',[])
        if results:
            avg=sum(r.get('score',0) for r in results)/len(results)
            print(f'Last report: {len(results)} files, avg {avg:.1f}/100')
except Exception:
    pass
" 2>/dev/null)
  [ -n "$REPORT_SUMMARY" ] && echo "$REPORT_SUMMARY"
fi
# Phase 3: conditional reminder — only when this turn's scan flagged violations.
if [ -n "$GATE_REPORT" ]; then
  echo "TappsMCP completion-gate (warn): $GATE_REPORT" >&2
  echo "Reminder: run /tapps-finish-task (or tapps_validate_changed + tapps_checklist manually) before declaring complete." >&2
fi
exit 0
