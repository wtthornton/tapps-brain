# OpenClaw Install and Upgrade Runbook (Canonical)

This is the source-of-truth runbook for installing and upgrading tapps-brain in OpenClaw.
Use this file for operator workflows and copy-paste commands.

## Compatibility

- OpenClaw `v2026.3.7+`: full ContextEngine lifecycle (`bootstrap`, `ingest`, `assemble`, `compact`, `dispose`)
- OpenClaw `v2026.3.1-3.6`: hook-only mode (session-start injection only)
- OpenClaw `< v2026.3.1`: tools-only fallback
- Python `3.12+`, Node `18+`

## Path A: PyPI install/upgrade (recommended)

### 1) Install Python package

```bash
pip install tapps-brain[mcp]
```

### 2) Build and install OpenClaw plugin

```bash
git clone https://github.com/wtthornton/tapps-brain.git
cd tapps-brain/openclaw-plugin
npm install
npm run build
openclaw plugin install .
```

### 3) Configure OpenClaw

```yaml
plugins:
  slots:
    contextEngine: tapps-brain-memory
  entries:
    tapps-brain-memory:
      enabled: true
      config:
        mcpCommand: tapps-brain-mcp
        tokenBudget: 2000
        captureRateLimit: 3
```

### 4) Restart gateway

```bash
openclaw gateway restart
```

### 5) Smoke checks

```bash
openclaw --version
tapps-brain --version
tapps-brain-mcp --version
tapps-brain search "memory" --project-dir /path/to/project
```

## Path B: Git-only install/upgrade (no PyPI)

### 1) Install from Git

```bash
pip install "git+https://github.com/wtthornton/tapps-brain.git@main#egg=tapps-brain[mcp]"
```

Pinning a release tag is also supported:

```bash
pip install "git+https://github.com/wtthornton/tapps-brain.git@v1.3.0#egg=tapps-brain[mcp]"
```

### 2) Build/install plugin, configure, restart

Use the same plugin/config/restart steps from Path A.

### 3) Smoke checks

Use the same smoke checks from Path A.

## Upgrade workflow

### Python package

- PyPI path:

```bash
pip install --upgrade tapps-brain[mcp]
```

- Git path:

```bash
pip install --upgrade --force-reinstall "git+https://github.com/wtthornton/tapps-brain.git@main#egg=tapps-brain[mcp]"
```

### Plugin rebuild/reinstall

```bash
cd tapps-brain/openclaw-plugin
npm install
npm run build
openclaw plugin install .
```

### Mandatory restart

```bash
openclaw gateway restart
```

### Post-upgrade validation

```bash
tapps-brain --version
tapps-brain-mcp --version
openclaw --version
```

Quick runtime sanity:
- Ask the agent to remember a fact.
- Ask the agent to recall that fact in a second prompt.
- If needed, verify directly with `tapps-brain search`.

## Maintainers (pre-release)

From a clean checkout on Linux, macOS, or WSL:

```bash
bash scripts/release-ready.sh
```

That gate includes OpenClaw doc consistency checks, packaging build, tests, lint, types, and the `openclaw-plugin` npm build/test. See `scripts/publish-checklist.md` and `docs/planning/STATUS.md`.
