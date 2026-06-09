#!/bin/bash
# Ralph test runner — runs Ralph and then shows results
set -e

cd "$(dirname "$0")"

echo "=== Starting Ralph test run ==="
echo "Task: Add docstring to __init__.py"
echo "Limits: 5 calls/hour, 5 min timeout"
echo ""

# Run Ralph with live output
ralph --live

echo ""
echo "=== Ralph finished ==="
echo ""

echo "--- Git diff ---"
git diff

echo ""
echo "--- Recent commits ---"
git log --oneline -5

echo ""
echo "--- Circuit breaker status ---"
ralph --circuit-status

echo ""
echo "--- Tests still passing? ---"
pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95
