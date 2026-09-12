---
name: tapps-research
user-invocable: true
description: >-
  Look up library documentation and run open-ended / latest web research
  for the technologies used in this project. Use when writing code that uses
  an external library, when you need API reference, or when the question is
  time-sensitive / not covered by Context7 docs.
allowed-tools: >-
  mcp__nlt-build__tapps_research
  mcp__nlt-build__tapps_lookup_docs
argument-hint: "[library|query] [topic]"
context: fork
model: claude-sonnet-5
---
<!-- BEGIN: tapps-skill tapps-research v3.12.83 -->
<!-- upgrade-policy: managed-block. Edits made inside this BEGIN/END block are regenerated and lost on the next tapps_upgrade — put project-specific customizations below the END marker instead, where they survive every upgrade untouched. -->

Research using TappsMCP's unified front door (ADR-0030):

1. Prefer `mcp__nlt-build__tapps_research`:
   - Library/API: pass `library=` (and optional `topic=`) or `route="docs"`
   - Open-ended / latest: pass `query=` (auto-routes to brain `web_research`)
   - Single URL scrape: pass `url=` (brain `research_fetch`)
2. For a known library name only, `mcp__nlt-build__tapps_lookup_docs` is fine (doc-only).
3. If the brain path returns `degraded=true` / `success=false`, report the structured error — do not invent Exa/Firecrawl keys locally.
4. Synthesize findings into a clear, actionable answer with code examples when docs content is present.
5. Suggest follow-up lookups if additional coverage is needed
<!-- END: tapps-skill -->

<!-- tapps-skill-project-customizations: preserved from the pre-marker version — review and trim any content the managed block above now covers -->
<!-- flagged: 100% of this region's lines duplicate the managed block above — review and trim -->

<!-- upgrade-policy: overwrite. tapps_upgrade replaces this file wholesale on every run and local edits are lost (tapps_init leaves an existing copy alone; upgrade does not). Fold the change upstream into the platform template, or pin the whole directory with an upgrade_skip_files token. -->

Research using TappsMCP's unified front door (ADR-0030):

1. Prefer `mcp__nlt-build__tapps_research`:
   - Library/API: pass `library=` (and optional `topic=`) or `route="docs"`
   - Open-ended / latest: pass `query=` (auto-routes to brain `web_research`)
   - Single URL scrape: pass `url=` (brain `research_fetch`)
2. For a known library name only, `mcp__nlt-build__tapps_lookup_docs` is fine (doc-only).
3. If the brain path returns `degraded=true` / `success=false`, report the structured error — do not invent Exa/Firecrawl keys locally.
4. Synthesize findings into a clear, actionable answer with code examples when docs content is present.
5. Suggest follow-up lookups if additional coverage is needed
