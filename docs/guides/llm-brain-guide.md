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

| Tier | Use for | Typical lifespan |
|------|---------|-----------------|
| `architectural` | Tech stack, framework choices, API contracts | Long-lived |
| `pattern` | Naming conventions, code style, file organization | Long-lived |
| `procedural` | How-to knowledge, build steps, deploy procedures | Medium |
| `context` | Current task state, recent decisions | Session-length |
| `ephemeral` | Scratch notes, intermediate reasoning | Very short |

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
    "task_description": "Implemented responsive sidebar with Tailwind",
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
