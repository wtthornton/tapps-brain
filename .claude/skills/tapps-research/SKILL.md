---
name: tapps-research
user-invocable: true
description: >-
  Look up library documentation and research best practices
  for the technologies used in this project.
allowed-tools: mcp__tapps-mcp__tapps_lookup_docs
argument-hint: "[library] [topic]"
context: fork
model: claude-sonnet-4-6
---

Look up library documentation using TappsMCP:

1. Call `mcp__tapps-mcp__tapps_lookup_docs` with the library name and topic
2. If coverage is incomplete, call `mcp__tapps-mcp__tapps_lookup_docs` with a more specific topic
3. Synthesize findings into a clear, actionable answer with code examples
4. Include API signatures and usage patterns from the documentation
5. Suggest follow-up lookups if additional coverage is needed
