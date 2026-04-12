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
pip install "git+https://github.com/wtthornton/tapps-brain.git@v1.3.1#egg=tapps-brain[mcp]"
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

## Troubleshooting

### Repeated provenance warning in `openclaw logs`

You may see `openclaw logs` emit repeated lines like:

```text
[plugins] tapps-brain-memory: loaded without install/load-path provenance; treat as untracked local code and pin trust via plugins.allow or install records (~/.openclaw/extensions/tapps-brain-memory/dist/index.js)
```

**Cause:** OpenClaw loaded the plugin directory directly from `~/.openclaw/extensions/tapps-brain-memory/` without a matching install record. This typically happens when the directory was created manually (copied, `git clone`'d, or built in place) instead of being registered through `openclaw plugin install`.

**Fix — reinstall through OpenClaw so an install record is written:**

```bash
cd tapps-brain/openclaw-plugin
npm install
npm run build
openclaw plugin install .
openclaw gateway restart
```

`openclaw plugin install .` records install provenance so subsequent loads are trusted and the warning goes away.

**Alternative — pin trust explicitly** (useful for CI or vendored checkouts where you intentionally load from a known path):

Add an allow entry to your OpenClaw config so the untracked load path is trusted without reinstalling:

```yaml
plugins:
  allow:
    - id: tapps-brain-memory
      path: ~/.openclaw/extensions/tapps-brain-memory/dist/index.js
```

Restart the gateway after editing the config.

Tracked in [GitHub #65](https://github.com/wtthornton/tapps-brain/issues/65).

## Long-lived MCP and SQLite WAL

The memory MCP server stays up with the gateway. As of ADR-007, tapps-brain uses PostgreSQL — there is no SQLite WAL file to manage. For Postgres backup guidance before maintenance windows, see [`docs/operations/postgres-backup-restore.md`](../operations/postgres-backup-runbook.md) *(SQLite WAL guide removed — SQLite retired in ADR-007)*.

## Maintainers (pre-release)

From a clean checkout on Linux, macOS, or WSL:

```bash
bash scripts/release-ready.sh
```

That gate includes OpenClaw doc consistency checks, packaging build, tests, lint, types, and the `openclaw-plugin` npm build/test. See `scripts/publish-checklist.md` and `docs/planning/STATUS.md`.
