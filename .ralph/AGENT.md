# Ralph Agent Configuration

## Build Instructions

```bash
# Install dependencies
uv sync --extra dev

# Install with optional vector search support
uv sync --extra dev --extra vector
```

## Test Instructions

```bash
# Run all tests with coverage
pytest tests/ -v --tb=short --cov=tapps_brain --cov-report=term-missing --cov-fail-under=95

# Run a single test file
pytest tests/unit/test_memory_store.py -v

# Run a single test
pytest tests/unit/test_memory_store.py::test_function_name -v
```

## Lint & Format

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

## Notes
- Reference stories in commits: `feat(story-001.3): description`
- See CLAUDE.md at project root for full architecture details
- See docs/planning/epics/ for planned work
