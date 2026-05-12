# LLM Brain Guide

Instructions for LLMs and AI agents using the tapps-brain simplified MCP tools.

## When to remember

- After learning a user preference or project convention
- After a successful task outcome (use `brain_learn_success`)
- When discovering architectural decisions (tier: `architectural`)
- When identifying a reusable pattern (tier: `pattern`)
- When learning a how-to procedure (tier: `procedural`)

## When to recall

- Before starting any task, to check for relevant context
- When you need to know project conventions or preferences
- When you are unsure about a decision that may have been made before
- After receiving an error, to check if a similar failure was recorded

## When to share

- **share=True** (group scope): Share with all agents in your declared groups.
  Use for team conventions, shared patterns, and group decisions.
- **share_with="hive"**: Share org-wide. Use for cross-cutting facts like
  tech stack decisions, API contracts, and team agreements.
- **share_with="group-name"**: Share with a specific group only.

## When NOT to remember

- Ephemeral information (timestamps, temporary file paths)
- PII unless the user explicitly requests it
- Information that changes every session
- Raw error output (summarize instead)

## Tier guide

The four tiers below are the **default `repo-brain` profile** — every coding-agent install ships with these. Half-lives are exponential-decay parameters set in [`profile-catalog.md`](profile-catalog.md).

| Tier | Use for | Half-life (repo-brain) |
|------|---------|-----------------------|
| `architectural` | Tech stack, framework choices, API contracts, ADR-level decisions | 180 days |
| `pattern` | Naming conventions, code style, file organisation, reusable design patterns | 60 days |
| `procedural` | How-to knowledge, build steps, deploy procedures, runbooks | 30 days |
| `context` | Current task state, recent session-scoped decisions | 14 days |

Custom profiles can add additional tiers (`ephemeral`, `personal`, etc.) — call `profile_info` or `memory_profile_onboarding` to see the active profile's layer stack. `MemoryTier` accepts `ephemeral` as an enum value, but the default `repo-brain` profile does not define an `ephemeral` layer, so its decay behaviour falls back to `context`-tier defaults.

## MCP tool examples

### Save a memory

```json
{
  "tool": "brain_remember",
  "arguments": {
    "fact": "This project uses Tailwind CSS for all styling",
    "tier": "architectural"
  }
}
```

### Save and share with group

```json
{
  "tool": "brain_remember",
  "arguments": {
    "fact": "API responses must include a `request_id` header",
    "tier": "pattern",
    "share": true
  }
}
```

### Search memories

```json
{
  "tool": "brain_recall",
  "arguments": {
    "query": "how to style components",
    "max_results": 5
  }
}
```

### Record a success

```json
{
  "tool": "brain_learn_success",
  "arguments": {
    "description": "Implemented responsive sidebar with Tailwind",
    "task_id": "TASK-42"
  }
}
```

### Record a failure

```json
{
  "tool": "brain_learn_failure",
  "arguments": {
    "description": "CSS grid layout broke on Safari mobile",
    "error": "Grid items overflow container on iOS Safari 16",
    "task_id": "TASK-43"
  }
}
```

### Forget a memory

```json
{
  "tool": "brain_forget",
  "arguments": {
    "key": "use-tailwind-for-abc123def456"
  }
}
```

### Check status

```json
{
  "tool": "brain_status",
  "arguments": {}
}
```

### Share with the whole org

```json
{
  "tool": "brain_remember",
  "arguments": {
    "fact": "All services must use structured JSON logging",
    "tier": "architectural",
    "share_with": "hive"
  }
}
```

### Save a procedural memory (default tier)

```json
{
  "tool": "brain_remember",
  "arguments": {
    "fact": "Run `npm run build:css` before `npm test` to regenerate styles"
  }
}
```

### Search for failure patterns

```json
{
  "tool": "brain_recall",
  "arguments": {
    "query": "Safari mobile layout failures"
  }
}
```
