# Contributing to tapps-brain

Thank you for your interest in contributing to tapps-brain! This guide will help you get started.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally:

```bash
git clone https://github.com/wtthornton/tapps-brain
cd tapps-brain
```

## Development Setup

```bash
# Install with uv
uv sync --all-packages
```

## Coding Standards

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check .
ruff format --check .
```

Type checking is enforced with mypy:

```bash
mypy .
```

## Testing

Please ensure all tests pass before submitting a pull request.

```bash
pytest -v
```

When adding new features, please include appropriate tests.

CI runs automatically on pull requests (workflows: ci, epic-validation, hive-smoke).

## Documentation Quality

Documentation health is tracked via [docs-mcp](https://github.com/anthropics/docs-mcp). The canonical drift-ignore manifest lives in [`.docsmcp.yaml`](.docsmcp.yaml) at the repo root — keep it in sync when adding or removing internal-only constants that the scanner flags but that don't belong in user docs.

Running drift cleanly (zero false positives):

```bash
# Build the comma-joined argument from .docsmcp.yaml, then invoke docs_check_drift
ARGS=$(python -c "import yaml; print(','.join(yaml.safe_load(open('.docsmcp.yaml'))['drift_ignore_patterns']))")
# Then call docs_check_drift via your MCP client with ignore_patterns=$ARGS
```

A `drift_score` above 0 after applying the manifest is real signal — investigate before merging.

## Submitting Changes

1. Create a feature branch from `main`:

   ```bash
   git checkout -b feature/my-feature
   ```

2. Make your changes and commit with a descriptive message:

   ```bash
   git add .
   git commit -m "feat: add my new feature"
   ```

3. Push your branch to your fork:

   ```bash
   git push origin feature/my-feature
   ```

4. Open a Pull Request against the `main` branch
5. Describe your changes and link any related issues
6. Wait for review and address any feedback

## Reporting Issues

When reporting issues, please include:

- A clear and descriptive title
- Steps to reproduce the problem
- Expected behavior vs actual behavior
- Your environment (OS, language version, etc.)

Please use the provided [issue templates](.github/ISSUE_TEMPLATE/) when applicable.
