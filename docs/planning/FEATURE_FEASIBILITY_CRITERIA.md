# Feature Feasibility Criteria (Agent Standard)

Last updated: 2026-03-27 (web-calibrated pass)  
Applies to: all agents proposing `feat` work in this repository

## Purpose

This document defines the mandatory criteria for proposing new feature work so we minimize roadmap noise, avoid duplicate efforts, and prioritize high-value delivery.

Every new feature idea must pass this framework **before** issue creation or implementation planning.

## Non-Negotiable Policy

- No feature issue should be opened without a completed criteria assessment.
- If required information is missing, the feature remains `proposal-incomplete`.
- If the feature fails hard gates, do **not** open as `feat`; either:
  - close/reject,
  - defer to backlog with rationale, or
  - re-scope into a smaller, safer increment.

## Core Principles (2026 Best Practice)

- **Outcome-first, not output-first:** define measurable value before writing code.
- **Platform reliability over feature volume:** prefer fewer high-confidence features.
- **Backward-compatible by default:** additive/optional/reversible changes first.
- **Low operational burden:** avoid introducing always-on complexity without strong ROI.
- **Evidence over intuition:** use metrics, incidents, and user pain to justify work.
- **No duplicate surfaces:** verify existing capabilities before proposing new APIs/commands.
- **Reliability before acceleration:** AI-assisted velocity should not increase change-failure risk.

## Project-Specific Architecture Constraints (Must Respect)

Any accepted feature must remain aligned with repository constraints in `CLAUDE.md` and workspace rules:

- Core implementation remains deterministic (no LLM calls in core memory logic).
- Core remains synchronous by design (no async/await architecture drift in core modules).
- Storage remains SQLite write-through with WAL-aware behavior and migration safety.
- Changes are backward-compatible by default and avoid forced migrations unless explicitly approved.
- Feature proposals must not modify Ralph control files (`.ralph/`, `.ralphrc`) as part of feature work.
- Avoid introducing always-on infra dependencies that conflict with local-first/offline expectations.

If a proposed feature requires violating one of these constraints, mark `reject` or `approve_with_rescope`.

## Feature Triage Criteria

Score each criterion from `0` to `5` (definitions below).  
Use evidence (usage data, logs, incidents, user reports, architecture constraints).

### 1) User Value Density

How much real user pain does this solve, and how often?

- `0`: no clear user pain
- `1`: nice-to-have edge case
- `3`: useful for a meaningful segment
- `5`: frequent high-severity pain in core workflows

### 2) Strategic Fit

How strongly does this improve tapps-brain's core value proposition?

- `0`: unrelated to core product
- `2`: adjacent convenience
- `4`: strengthens core reliability/retrieval/adoption
- `5`: major moat reinforcement

### 3) Dependency Unlock Value

Does this unlock other high-value work?

- `0`: isolated feature
- `2`: minor downstream impact
- `4`: unlocks multiple roadmap items
- `5`: foundational prerequisite

### 4) Delivery Confidence

Can we implement correctly with current architecture/team confidence?

- `0`: highly uncertain feasibility
- `2`: substantial unknowns
- `4`: known path, manageable risk
- `5`: clear implementation path, proven patterns

### 5) Build Complexity (inverse)

Engineering complexity and cross-module blast radius.  
Higher score = higher complexity (worse for priority).

- `0`: tiny isolated change
- `2`: medium scope
- `4`: multi-subsystem change
- `5`: broad architectural impact

### 6) Operational Burden (inverse)

Expected long-term maintenance/on-call/observability overhead.  
Higher score = higher burden (worse for priority).

- `0`: near-zero ongoing ops cost
- `2`: occasional maintenance
- `4`: recurring operational tasks
- `5`: always-on complexity (subscriptions, retries, fan-out, etc.)

### 7) Risk Surface (inverse)

Security, migration, data integrity, portability, and regression risk.  
Higher score = higher risk (worse for priority).

- `0`: minimal risk
- `2`: bounded risk with easy rollback
- `4`: significant risk, careful rollout required
- `5`: high-probability severe failure modes

### 8) Overlap / Duplication Risk (inverse)

Likelihood this feature duplicates existing behavior or creates parallel UX/API.

- `0`: no overlap
- `2`: partial overlap but justified
- `4`: substantial overlap, unclear differentiation
- `5`: duplicates existing capability

### 9) Measurability

Can we objectively determine if this feature succeeded?

- `0`: no measurable outcome
- `2`: weak/proxy metrics only
- `4`: clear metric + baseline + target
- `5`: strong KPI + reliable instrumentation path

### 10) Compatibility & Migration Safety

How safely can we ship this without breaking users?

- `0`: breaking/forced migration
- `2`: risky migration path
- `4`: additive + optional with migration tooling
- `5`: fully backward-compatible and reversible

## Scoring Model

Compute:

`priority_score = (value_density + strategic_fit + unlock_value + delivery_confidence + measurability + compatibility) - (build_complexity + operational_burden + risk_surface + overlap_risk)`

### Interpreting Score

- `>= 10`: Strong candidate, plan for near-term execution
- `5 to 9`: Candidate, but likely needs re-scope or sequencing
- `0 to 4`: Weak candidate, defer unless urgent dependency exists
- `< 0`: Do not start; close or heavily reframe

## Hard Gates (Fail Any = No Feature Issue)

1. **Problem clarity gate**  
   Must specify concrete user pain and affected workflow.

2. **No-duplication gate**  
   Must include proof the capability does not already exist (CLI/API/MCP/docs/code).

3. **Success metric gate**  
   Must define at least one measurable target and baseline.

4. **Compatibility gate**  
   Must describe backward compatibility and rollback strategy.

5. **Operational gate**  
   For always-on/evented features, must provide reliability model and failure behavior.

6. **Security/data gate**  
   Must assess data safety, secret handling, and migration impact.

7. **Architecture invariants gate (project-specific)**  
   Must explicitly state compliance with deterministic core, synchronous core, and SQLite write-through design.

8. **Operational reliability gate**  
   Must include impact on change-failure risk and a safe rollout strategy (phased or reversible).

9. **Source quality gate**  
   Claims must cite at least one authoritative source for critical risk/security/reliability assumptions.

## Required Proposal Sections (Before Opening `feat`)

Every proposal must include all sections below:

1. Problem statement (who is affected, how often, severity)
2. Current workaround and its cost
3. Proposed scope (v1 only, explicitly out-of-scope items)
4. Criteria scorecard (all 10 criteria with 0-5 score + evidence)
5. Hard gate checklist (pass/fail with rationale)
6. Success metrics (baseline, target, measurement method)
7. Dependency map (blocked-by / unlocks)
8. Compatibility + migration + rollback plan
9. Operational impact (runbooks, monitoring, failure modes)
10. Testing plan (unit/integration/e2e as applicable)

## Anti-Noise Rules

- Do not open `feat` for:
  - duplicate functionality already shipped,
  - vague requests without measurable outcomes,
  - broad “platform redesign” ideas without phased v1,
  - high-burden low-value operational complexity.

- Convert to:
  - `docs` task if issue is guidance/documentation only,
  - `housekeeping` if validation/closure work,
  - `spike` if feasibility is unknown.

## Evidence Standard (Required for "Detailed Research")

When proposals claim "best practice", include evidence quality labels:

- `Tier A` (authoritative): standards bodies, primary project docs, official vendor docs.
- `Tier B` (industry research): recognized engineering research groups, benchmark studies.
- `Tier C` (secondary): blogs, opinion posts, summaries.

Rules:

- Critical security/reliability decisions require at least one `Tier A` source.
- At least 70% of cited evidence should be `Tier A` + `Tier B`.
- `Tier C` sources can inform ideas but cannot be the sole justification for hard decisions.

## Rollout and Validation Standard (2026 AI/Agent Practice)

Every non-trivial feature must define:

1. **Offline validation:** deterministic tests and baseline comparisons.
2. **Progressive rollout:** feature flag, staged enablement, or constrained initial scope.
3. **Production observability:** metrics, alerts, and failure-mode logging.
4. **Rollback trigger:** explicit threshold for reverting or disabling.

For retrieval-quality features, include:

- quality metric(s) (for example precision@k, recall@k, task success proxy),
- latency budget impact,
- regression dataset or scenario set.

## Proposal Template (Copy/Paste)

```md
## Feature Intake

### 1) Problem
- User pain:
- Affected workflows:
- Frequency/severity:

### 2) Current Workaround + Cost
- Workaround:
- Cost/risk:

### 3) Proposed v1 Scope
- In scope:
- Out of scope:

### 4) Criteria Scorecard (0-5)
- Value density:
- Strategic fit:
- Dependency unlock value:
- Delivery confidence:
- Build complexity (inverse):
- Operational burden (inverse):
- Risk surface (inverse):
- Overlap risk (inverse):
- Measurability:
- Compatibility/migration safety:

Computed priority_score:

### 5) Hard Gates
- Problem clarity gate: pass/fail
- No-duplication gate: pass/fail
- Success metric gate: pass/fail
- Compatibility gate: pass/fail
- Operational gate: pass/fail
- Security/data gate: pass/fail
- Architecture invariants gate: pass/fail
- Operational reliability gate: pass/fail
- Source quality gate: pass/fail

### 6) Success Metrics
- Baseline:
- Target:
- Measurement method:

### 7) Dependencies
- Blocked by:
- Unlocks:

### 8) Compatibility, Migration, Rollback
- Compatibility approach:
- Migration approach:
- Rollback plan:

### 9) Operational Impact
- Monitoring:
- Runbook updates:
- Failure handling:

### 10) Test Plan
- Unit:
- Integration:
- End-to-end:
```

## Decision Outcomes

After scoring and gate checks, use one:

- `approve`: ready for roadmap sequencing
- `approve_with_rescope`: proceed only with narrowed v1
- `defer`: not now; revisit with trigger conditions
- `reject`: insufficient value or excessive risk/noise

## Governance

- This criteria file is the default intake standard for all new feature proposals.
- If an agent skips this process, proposal quality is considered invalid.
- Update this document when triage quality degrades or project priorities shift.

## Research Calibration Notes (2026)

This standard is aligned to:

- NIST AI RMF + GenAI Profile (NIST AI 600-1) for govern/map/measure/manage risk framing.
- OWASP LLM Top 10 project guidance for prompt injection, output handling, excessive agency, and data exposure classes.
- DORA 2025 findings emphasizing AI as an amplifier and the need to protect reliability while increasing velocity.
- SQLite WAL official guidance, including checkpoint behavior, same-host constraints, WAL file handling, and current WAL bugfix awareness.

These references are used as guardrails, then specialized to this repository's deterministic, synchronous, SQLite-centered architecture.
