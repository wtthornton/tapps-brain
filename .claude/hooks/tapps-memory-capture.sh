#!/usr/bin/env bash
# tapps-mcp-hook-version: 3.12.42
# tapps-mcp-hook-content-sha: c69d5615
# TappsMCP Stop hook - Session Quality Tracker (TAP-1999)
# Session episodic memory migrated to brain-native memory_index_session.
# Hook retained only for the stop_hook_active guard (prevents infinite loops).
# IMPORTANT: Must check stop_hook_active to prevent infinite loops.
INPUT=$(cat)
PYBIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null)
ACTIVE=$(echo "$INPUT" | "$PYBIN" -c   "import sys,json; d=json.load(sys.stdin); print(d.get('stop_hook_active','false'))"   2>/dev/null)
if [ "$ACTIVE" = "True" ] || [ "$ACTIVE" = "true" ]; then
  exit 0
fi
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-.}"
CAPTURE_DIR="$PROJECT_DIR/.tapps-mcp"
MARKER="$CAPTURE_DIR/.validation-marker"
if [ -f "$MARKER" ]; then
  : # validation occurred; tapps_session_start handles brain indexing via memory_index_session
fi
# Session capture now handled by brain-native memory_index_session (TAP-1999).
exit 0
