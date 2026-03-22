# Ralph Agent Configuration

## Build Instructions

```bash
# Install dependencies
uv sync --extra dev

# Install with optional vector search support
uv sync --extra dev --extra vector
```

## Test Instructions

> **EPIC-BOUNDARY ONLY:** Do NOT run these commands mid-epic. Only run at epic boundaries
> (last `- [ ]` in a `##` section of fix_plan.md) or before EXIT_SIGNAL: true.
> Mid-epic: set `TESTS_STATUS: DEFERRED` and move on.

```bash
# Run all tests with coverage (EPIC BOUNDARY ONLY)
pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

# Run a single test file (only if writing NEW tests for this task)
pytest tests/unit/test_memory_store.py -v

# Run a single test
pytest tests/unit/test_memory_store.py::test_function_name -v
```

## Lint & Format

> **EPIC-BOUNDARY ONLY:** Same rule — defer lint/type checks to epic boundaries.

```bash
# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/

# Auto-fix lint/format
ruff check --fix src/ tests/
ruff format src/ tests/

# Type check (strict mode)
mypy --strict src/tapps_brain/
```

## Code Quality Requirements
- Python 3.12+, strict mypy, ruff
- Line length: 100 chars
- Coverage minimum: 95%
- LF line endings enforced
- No async/await in core code (synchronous by design)
- **QA runs at epic boundaries only — not after every task**

## Notes
- Reference stories in commits: `feat(story-001.3): description`
- See CLAUDE.md at project root for full architecture details
- See docs/planning/epics/ for planned work
