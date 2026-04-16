# tapps-brain — Documentation Index

**215 documents** across **8 categories**

## Overview

| Category | Count |
|---|---|
| API Reference | 3 |
| Architecture | 14 |
| Getting Started | 7 |
| Guides | 37 |
| Operations | 5 |
| Other | 3 |
| Planning | 145 |
| Release | 1 |

## API Reference

- [tapps-brain — Documentation Index](docs/DOCUMENTATION_INDEX.md) — **121 documents** across **7 categories** *(updated 2026-04-14)*
- [Data Stores and Schema Reference](docs/engineering/data-stores-and-schema.md) — All durable stores use **PostgreSQL** ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)). No SQLite fall... *(updated 2026-04-13)*
- [Kubernetes Liveness and Readiness Probes](docs/operations/k8s-probes.md) — tapps-brain's HTTP adapter exposes two dedicated probe endpoints that map *(updated 2026-04-11)*
## Architecture

- [Industry features and technologies (implementation map)](docs/engineering/features-and-technologies.md) — **Audience:** Architecture and product review — what capability areas we cover, which libraries/patterns we use, and ... *(updated 2026-04-13)*
- [System Architecture (Implementation-Aligned)](docs/engineering/system-architecture.md) — tapps-brain is designed for **many concurrent agents** without shared-DB bottlenecks: *(updated 2026-04-12)*
- [Configurable Memory Profiles — Design Document](docs/planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md) *(updated 2026-04-15)*
- [ADR-001: Retrieval stack — embedded SQLite-first (defer learned sparse, ColBERT, managed vector DB)](docs/planning/adr/ADR-001-retrieval-stack.md) — **Status:** Accepted *(updated 2026-04-08)*
- [ADR-002: Freshness — lazy decay + operator GC (defer wall-clock TTL jobs)](docs/planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-003: Correctness — heuristic conflicts + offline review (defer ontology and in-product review queue)](docs/planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-004: Scale — single-node SQLite posture (defer published QPS SLO and service extraction)](docs/planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md) — **Status:** Accepted *(updated 2026-04-12)*
- [ADR-005: SQLCipher operations — passphrase runbook + backup verification (defer KMS product integration)](docs/planning/adr/ADR-005-sqlcipher-key-backup-operations.md) — **Status:** Accepted *(updated 2026-04-12)*
- [ADR-006: Save-path observability — phase histograms + health summary (defer deeper metrics unless trigger (a))](docs/planning/adr/ADR-006-save-path-observability.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-007: PostgreSQL-only persistence plane (SQLite fully removed)](docs/planning/adr/ADR-007-postgres-only-no-sqlite.md) — Accepted (2026-04-10) *(updated 2026-04-12)*
- [ADR-008: No new public HTTP routes without MCP + library parity](docs/planning/adr/ADR-008-no-http-without-mcp-library-parity.md) — Accepted (2026-04-10) *(updated 2026-04-11)*
- [ADR-009: Row Level Security on hive_memories — Ship in GA vs Defer](docs/planning/adr/ADR-009-rls-ship-vs-defer.md) — Accepted (2026-04-11) *(updated 2026-04-14)*
- [ADR-010: Multi-tenant project identification and profile registration over MCP](docs/planning/adr/ADR-010-multi-tenant-project-registration.md) — Proposed (2026-04-14) *(updated 2026-04-14)*
- [Design note: multi-scope memory (#49)](docs/planning/design-issue-49-multi-scope-memory.md) — Epic **#49** (multi-group memory scopes: Hive, named groups, personal) needs a clear *(updated 2026-04-15)*
## Getting Started

- [GitHub Setup Guide](docs/GITHUB_SETUP_GUIDE.md) — <!-- tapps-generated: v2.4.0 --> *(updated 2026-04-12)*
- [Ralph Setup Guide (Windows + WSL)](docs/RALPH_SETUP_GUIDE.md) — Step-by-step guide for setting up Ralph on a new project. Covers the common pitfalls. *(updated 2026-04-05)*
- [NLT Labs — Brand Style Sheet & Logo Pack Audit](docs/design/nlt-brand/README.md) — | Item | Location | *(updated 2026-04-11)*
- [Engineering Documentation Baseline](docs/engineering/README.md) — This folder is the code-aligned engineering reference for tapps-brain runtime behavior. *(updated 2026-04-12)*
- [Optional Features and Runtime Toggle Matrix](docs/engineering/optional-features-matrix.md) — This matrix documents behavior changes from extras, feature checks, and profile-driven toggles. *(updated 2026-04-12)*
- [Getting Started with tapps-brain](docs/guides/getting-started.md) — tapps-brain ships three first-class interfaces to the same memory engine. Choose the one that fits your workflow. *(updated 2026-04-14)*
- [Install and upgrade tapps-brain for OpenClaw from GitHub (no PyPI)](docs/guides/openclaw-install-from-git.md) — Use this guide when you want the **Python** package and **`tapps-brain-mcp`** installed or upgraded from the Git repo... *(updated 2026-04-05)*
## Guides

- [Agent integration guide](docs/guides/agent-integration.md) — This page is the **operator contract** for AI agents using tapps-brain: the *(updated 2026-04-14)*
- [Agent.md Wiring Guide](docs/guides/agent-md-wiring.md) — This guide explains how to grant tapps-brain MCP access in an `AGENT.md` file *(updated 2026-04-15)*
- [AgentForge Integration Guide (v3)](docs/guides/agentforge-integration.md) — How any agent host — AgentForge, custom orchestrators, or bare Python scripts — *(updated 2026-04-13)*
- [Auto-Recall: Pre-Prompt Memory Injection](docs/guides/auto-recall.md) — Auto-recall automatically searches the memory store for relevant context before an agent processes a user message, an... *(updated 2026-04-13)*
- [ClawHub Submission Guide](docs/guides/clawhub-submission.md) — How to submit `tapps-brain-memory` to the ClawHub skill directory. *(updated 2026-04-05)*
- [TappsBrainClient — official Python client](docs/guides/client.md) — `TappsBrainClient` (sync) and `AsyncTappsBrainClient` (async) let you consume a *(updated 2026-04-15)*
- [tapps-brain Deployment Guide](docs/guides/deployment.md) — This guide covers deploying tapps-brain as a **shared networked service** — *(updated 2026-04-15)*
- [Pluggable lookup engine for doc validation](docs/guides/doc-validation-lookup-engine.md) — `tapps-brain` validates memory entries against authoritative documentation using a *(updated 2026-04-09)*
- [Embedding model card (default semantic search)](docs/guides/embedding-model-card.md) — This page documents the **default** dense embedding stack for built-in vector / hybrid retrieval (**EPIC-042** STORY-... *(updated 2026-04-13)*
- [Error Taxonomy and Retry Semantics](docs/guides/errors.md) — tapps-brain uses a **stable error code vocabulary** so client circuit-breakers and retry policies can be written once... *(updated 2026-04-14)*
- [Federation Guide](docs/guides/federation.md) — Cross-project memory sharing via a central hub store. *(updated 2026-04-13)*
- [Hive Deployment Guide](docs/guides/hive-deployment.md) — This guide covers deploying the tapps-brain Hive (shared Postgres brain) in *(updated 2026-04-13)*
- [Hive Operations Guide](docs/guides/hive-operations.md) — Day-to-day operational procedures for the tapps-brain Hive Postgres backend. *(updated 2026-04-08)*
- [TLS for the Hive Stack (EPIC-067 STORY-067.4)](docs/guides/hive-tls.md) — This guide covers adding HTTPS to the `tapps-visual` dashboard endpoint. *(updated 2026-04-13)*
- [Hive vs federation — when to use which](docs/guides/hive-vs-federation.md) — Both features move memories across boundaries, but the **boundary** and **mechanics** differ. Use this page first, th... *(updated 2026-04-15)*
- [Hive Guide: Cross-Agent Memory Sharing](docs/guides/hive.md) — The Hive is tapps-brain's multi-agent shared brain. It enables agents to share knowledge through a central PostgreSQL... *(updated 2026-04-13)*
- [HTTP Adapter](docs/guides/http-adapter.md) — The tapps-brain HTTP adapter exposes `/health`, `/ready`, `/metrics`, and `/snapshot` endpoints. It is enabled separa... *(updated 2026-04-12)*
- [Idempotency Keys for Write Operations](docs/guides/idempotency.md) — **Feature flag:** `TAPPS_BRAIN_IDEMPOTENCY=1` (default OFF) *(updated 2026-04-14)*
- [LLM Brain Guide](docs/guides/llm-brain-guide.md) — Instructions for LLMs and AI agents using the tapps-brain simplified MCP tools. *(updated 2026-04-08)*
- [MCP Server: Using tapps-brain with AI Assistants](docs/guides/mcp.md) — tapps-brain exposes its full API via the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP), making per... *(updated 2026-04-14)*
- [Memory decay and FSRS-style stability](docs/guides/memory-decay-and-fsrs.md) — This document is the **product decision** for **EPIC-042** STORY-042.8: how tapps-brain combines time-based decay wit... *(updated 2026-04-15)*
- [Memory relay (sub-agent → primary)](docs/guides/memory-relay.md) — Cross-node setups: only the primary host runs tapps-brain. Sub-agents build a **relay** JSON envelope so the primary ... *(updated 2026-04-05)*
- [Memory scopes: project group vs Hive vs profile (GitHub #49)](docs/guides/memory-scopes.md) — Three concepts are easy to confuse. They are **separate** in tapps-brain. *(updated 2026-04-15)*
- [Migration Guide: tapps-brain 3.5.x → 3.6](docs/guides/migration-3.5-to-3.6.md) — This guide covers the breaking changes and migration steps required when *(updated 2026-04-15)*
- [Observability](docs/guides/observability.md) — tapps-brain exposes structured **metrics**, **health**, **audit**, **diagnostics**, and **feedback** surfaces through... *(updated 2026-04-12)*
- [OpenClaw Install and Upgrade Runbook (Canonical)](docs/guides/openclaw-runbook.md) — This is the source-of-truth runbook for installing and upgrading tapps-brain in OpenClaw. *(updated 2026-04-12)*
- [tapps-brain for OpenClaw](docs/guides/openclaw.md) — Persistent cross-session memory for your OpenClaw agents. MCP tool and resource *(updated 2026-04-15)*
- [Postgres Backup and Restore — tapps-brain](docs/guides/postgres-backup.md) — tapps-brain stores **all durable state** in PostgreSQL (ADR-007): private memories, *(updated 2026-04-12)*
- [Environment Variable Reference](docs/guides/postgres-dsn.md) — This is the **canonical environment variable contract** for tapps-brain v3. *(updated 2026-04-12)*
- [pg_tde Operator Runbook](docs/guides/postgres-tde.md) — **Applies to:** Percona Distribution for PostgreSQL 17 + pg_tde 2.1.2 (released 2026-03-02) *(updated 2026-04-13)*
- [Profile Catalog](docs/guides/profile-catalog.md) — tapps-brain ships with 6 built-in profiles covering common AI agent use cases. Each profile can be used directly, ext... *(updated 2026-04-06)*
- [Profile Limits: Research and Rationale](docs/guides/profile-limits-rationale.md) — This document explains the evidence behind tapps-brain's built-in profile defaults. *(updated 2026-04-13)*
- [Memory Profiles: Designing Custom Memory for Any AI Agent](docs/guides/profiles.md) — tapps-brain ships with a configurable profile system that lets you define custom memory layers, decay models, scoring... *(updated 2026-04-10)*
- [Remote MCP integration (Streamable HTTP)](docs/guides/remote-mcp-integration.md) — This guide describes how remote agents (AgentForge, OpenClaw, etc.) connect to *(updated 2026-04-14)*
- [Save conflicts: offline review and NLI backlog](docs/guides/save-conflict-nli-offline.md) — Save-time conflict detection uses deterministic text similarity (`detect_save_conflicts` in `contradictions.py`) when... *(updated 2026-04-05)*
- [Scope Audit: agent_scope / Group / Hive — Allowed Namespaces and Operations](docs/guides/scope-audit.md) *(updated 2026-04-12)*
- [Visual snapshot (`brain-visual.json`)](docs/guides/visual-snapshot.md) — Export a **versioned JSON snapshot** of store health, tier mix, and related signals for static dashboards and the bra... *(updated 2026-04-09)*
## Operations

- [DB Roles Runbook — tapps-brain](docs/operations/db-roles-runbook.md) — **Covers EPIC-063 STORY-063.1 + STORY-063.2: least-privilege Postgres roles.** *(updated 2026-04-12)*
- [Operator Runbook — tapps-brain Observability](docs/operations/observability-runbook.md) — See [`k8s-probes.md`](k8s-probes.md) for full probe spec. *(updated 2026-04-12)*
- [Postgres Backup Runbook — tapps-brain (Ops On-Call)](docs/operations/postgres-backup-runbook.md) — **Audience:** On-call engineers and SREs. *(updated 2026-04-13)*
- [Deploying tapps-brain to OpenClaw](docs/planning/DEPLOY-OPENCLAW.md) — There are **two complementary deployment paths** for getting tapps-brain into OpenClaw: *(updated 2026-04-15)*
- [Story 70.15 -- Docker + docs — one binary, both transports](docs/stories/STORY-070.15-docker-unified.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
## Other

- [Core Call Flows](docs/engineering/call-flows.md) — This document maps the dominant runtime call paths as implemented now. *(updated 2026-04-12)*
- [tapps-brain v3 Threat Model](docs/engineering/threat-model.md) — **Scope:** tapps-brain v3.0 — Postgres-only persistence plane with private agent memory, *(updated 2026-04-12)*
- [Ralph Bug: Live Mode JSONL Crash in Response Analyzer](docs/ralph-jsonl-crash-bug.md) — **Severity:** Critical (silent loop termination, no error logged) *(updated 2026-04-05)*
## Planning

- [v3 Behavioral Parity — What Changed vs v2](docs/engineering/v3-behavioral-parity.md) — **Epic:** [EPIC-059](../planning/epics/EPIC-059.md) — Greenfield v3 Postgres-Only Persistence Plane *(updated 2026-04-12)*
- [Epic 70: AgentForge Integration — Remote-First Brain as a Shared Service](docs/epics/EPIC-070-agentforge-integration.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-14)*
- [Epic Validation — Regression Runbook](docs/operations/epic-validation-regression.md) — This runbook verifies that the **Epic Validation** CI job (`.github/workflows/epic-validation.yml`) *(updated 2026-04-11)*
- [Telemetry Policy — Allowed and Forbidden Attributes](docs/operations/telemetry-policy.md) — The following attributes may be set on OTel spans.  All values are **bounded enums *(updated 2026-04-11)*
- [Agent Feature Governance](docs/planning/AGENT_FEATURE_GOVERNANCE.md) — Last updated: 2026-03-27 *(updated 2026-04-05)*
- [Feature Feasibility Criteria (Agent Standard)](docs/planning/FEATURE_FEASIBILITY_CRITERIA.md) — Last updated: 2026-03-27 (web-calibrated pass) *(updated 2026-04-15)*
- [Issue triage — saved searches and board setup](docs/planning/ISSUE_TRIAGE_VIEWS.md) — Last updated: 2026-03-27 *(updated 2026-04-05)*
- [Planning Conventions](docs/planning/PLANNING.md) — This document defines how epics, stories, and tasks are structured in this project so that both humans and AI coding ... *(updated 2026-04-11)*
- [Project status snapshot](docs/planning/STATUS.md) — **Last updated:** 2026-04-14 (America/Chicago) — **v3.5.1** — EPIC-069 / ADR-010: multi-tenant `project_id` on the wi... *(updated 2026-04-15)*
- [SQLite to Postgres - Meeting Notes](docs/planning/archive/sqlite-to-postgres-meeting-notes.md) — Status: active discussion *(updated 2026-04-05)*
- [Implementation plan: dynamic tapps-brain visual identity](docs/planning/brain-visual-implementation-plan.md) — Track progress here for a **modern, per-instance-unique** visual representation of tapps-brain (dashboard, marketing,... *(updated 2026-04-15)*
- [Phase 2 — Follow-up issues (ready to file)](docs/planning/engineering-doc-phase2-follow-up-issues.md) — **Purpose:** Concrete, prioritized cleanup items derived from `docs/engineering/code-inventory-and-doc-gaps.md` and f... *(updated 2026-04-05)*
- [Engineering Documentation Task - System Ground Truth](docs/planning/engineering-documentation-task.md) — Status: in_progress *(updated 2026-04-05)*
- [Epic #49 — actionable child issues (multi-scope memory)](docs/planning/epic-49-tasks.md) — **Parent:** GitHub **[#49](https://github.com/wtthornton/tapps-brain/issues/49)** — **closed** (v1 complete 2026-03-29). *(updated 2026-04-05)*
- [EPIC-001: Test Suite Quality — Raise to A+](docs/planning/epics/EPIC-001.md) — The test suite currently sits at 79% line coverage (521 tests, 13s runtime). A detailed review identified structural ... *(updated 2026-04-05)*
- [EPIC-002: Integration Wiring — Connect Standalone Modules to the Runtime](docs/planning/epics/EPIC-002.md) — Epic 1 raised test coverage to 96.59% across 792 tests — the codebase is well-tested at the unit level. However, seve... *(updated 2026-04-05)*
- [EPIC-003: Auto-Recall — Pre-Prompt Memory Injection Hook](docs/planning/epics/EPIC-003.md) — tapps-brain has a complete retrieval engine (BM25 + FTS5 + composite scoring + optional vector search + RRF fusion) a... *(updated 2026-04-05)*
- [EPIC-004: Bi-Temporal Fact Versioning with Validity Windows](docs/planning/epics/EPIC-004.md) — tapps-brain currently tracks three timestamps per memory: `created_at` (when stored), `updated_at` (when last modifie... *(updated 2026-04-05)*
- [EPIC-005: CLI Tool for Memory Management and Operations](docs/planning/epics/EPIC-005.md) — tapps-brain is currently a Python library only. Managing a memory store requires writing code — there's no way to ins... *(updated 2026-04-05)*
- [EPIC-006: Persistent Knowledge Graph and Semantic Queries](docs/planning/epics/EPIC-006.md) — tapps-brain has a `relations.py` module that extracts entity relations (subject-predicate-object triples) and a `retr... *(updated 2026-04-05)*
- [EPIC-007: Observability — Metrics, Audit Trail Queries, and Health Checks](docs/planning/epics/EPIC-007.md) — Partial implementation exists and is covered by tests: *(updated 2026-04-05)*
- [EPIC-008: MCP Server — Expose tapps-brain via Model Context Protocol](docs/planning/epics/EPIC-008.md) — - `src/tapps_brain/mcp_server.py` — FastMCP server, tools (CRUD, search, list, recall, reinforce, ingest, supersede, ... *(updated 2026-04-05)*
- [EPIC-009: Multi-Interface Distribution — Library, CLI, and MCP Packaging](docs/planning/epics/EPIC-009.md) — tapps-brain is becoming a three-interface project: a Python library (`import tapps_brain`), a CLI (`tapps-brain`), an... *(updated 2026-04-11)*
- [EPIC-010: Configurable Memory Profiles — Pluggable Layers and Scoring](docs/planning/epics/EPIC-010.md) — tapps-brain's memory tiers (architectural/pattern/procedural/context), half-lives (180/60/30/14 days), and scoring we... *(updated 2026-04-05)*
- [EPIC-011: Hive — Multi-Agent Shared Brain with Domain Namespaces](docs/planning/epics/EPIC-011.md) — tapps-brain currently serves one agent per project. But AI agent setups increasingly involve multiple specialized age... *(updated 2026-04-05)*
- [EPIC-012: OpenClaw Integration — ContextEngine Plugin and ClawHub Skill](docs/planning/epics/EPIC-012.md) — tapps-brain can already serve OpenClaw as an MCP server (documented in `docs/guides/openclaw.md`). But this is a side... *(updated 2026-04-05)*
- [EPIC-013: Hive-Aware MCP Surface — Agent Identity, Scope Propagation, and OpenClaw Multi-Agent Wiring](docs/planning/epics/EPIC-013.md) — EPIC-011 built the Hive core (HiveStore, AgentRegistry, PropagationEngine, conflict resolution, namespace isolation) ... *(updated 2026-04-05)*
- [EPIC-014: Hardening — Input Validation, Interface Parity, Resilience, and Onboarding Docs](docs/planning/epics/EPIC-014.md) — EPICs 001–013 built a complete memory system with profiles, Hive multi-agent sharing, MCP server, OpenClaw integratio... *(updated 2026-04-05)*
- [EPIC-015: Analytics & Operational Surface](docs/planning/epics/EPIC-015.md) — The tapps-brain library layer has ~36 public methods on `MemoryStore`, but 7 are not exposed via MCP or CLI. The know... *(updated 2026-04-05)*
- [EPIC-016: Test Suite Hardening](docs/planning/epics/EPIC-016.md) — A coverage and quality audit of the 1641-test suite (95.54% coverage) revealed four categories of gaps: *(updated 2026-04-05)*
- [EPIC-017: Code Review — Storage & Data Model](docs/planning/epics/EPIC-017.md) — With all 16 feature epics complete and BUG-001/BUG-002 fixes queued, the codebase is ready for systematic code review... *(updated 2026-04-05)*
- [EPIC-018: Code Review — Retrieval & Scoring](docs/planning/epics/EPIC-018.md) — Full code review of all retrieval, scoring, ranking, and search files. The retrieval layer is where the source_trust ... *(updated 2026-04-05)*
- [EPIC-019: Code Review — Memory Lifecycle](docs/planning/epics/EPIC-019.md) — Full code review of memory lifecycle management: decay, consolidation, GC, promotion, reinforcement. The consolidatio... *(updated 2026-04-05)*
- [EPIC-020: Code Review — Safety & Validation](docs/planning/epics/EPIC-020.md) — Full code review of safety, injection detection, validation, and contradiction handling. These are security-critical ... *(updated 2026-04-05)*
- [EPIC-021: Code Review — Federation, Hive & Relations](docs/planning/epics/EPIC-021.md) — Full code review of cross-project and cross-agent sharing systems. The HiveStore connection leak (BUG-001-C) and exce... *(updated 2026-04-05)*
- [EPIC-022: Code Review — Interfaces (MCP, CLI, IO)](docs/planning/epics/EPIC-022.md) — Full code review of all user-facing interfaces: MCP server (54 tools), CLI (41 commands), IO, and markdown import. *(updated 2026-04-05)*
- [EPIC-023: Code Review — Config, Profiles & Observability](docs/planning/epics/EPIC-023.md) — Full code review of configuration, profiles, metrics, and observability. *(updated 2026-04-05)*
- [EPIC-024: Code Review — Unit Tests (Part 1)](docs/planning/epics/EPIC-024.md) — Review all unit test files for: test quality, missing edge cases, flaky test patterns, proper isolation, assertion co... *(updated 2026-04-05)*
- [EPIC-025: Code Review — Integration Tests, Benchmarks & TypeScript](docs/planning/epics/EPIC-025.md) — Review all integration tests, benchmarks, test infrastructure, TypeScript plugin code, and configuration files. Final... *(updated 2026-04-05)*
- [EPIC-026: OpenClaw Memory Replacement — Replace memory-core with tapps-brain](docs/planning/epics/EPIC-026.md) — EPIC-012 delivered a ContextEngine plugin that adds auto-recall and auto-capture hooks. *(updated 2026-04-05)*
- [EPIC-027: OpenClaw Full Feature Surface — Expose All 41 MCP Tools as Native Tools](docs/planning/epics/EPIC-027.md) — **Note:** This epic was written against a **41-tool** MCP surface; tapps-brain exposes **54** tools as of v1.3.1. Cou... *(updated 2026-04-05)*
- [EPIC-028: OpenClaw Plugin Hardening — Stability, Tests, and Compatibility](docs/planning/epics/EPIC-028.md) — The ContextEngine plugin (EPIC-012) and memory replacement (EPIC-026) provide the *(updated 2026-04-05)*
- [EPIC-029: Feedback Collection — LLM and Project Quality Signals](docs/planning/epics/EPIC-029.md) — tapps-brain has strong observability (EPIC-007: metrics, audit trail, health checks) but no mechanism for consumers —... *(updated 2026-04-05)*
- [EPIC-030: Diagnostics & Self-Monitoring — Quality Scorecard and Anomaly Detection](docs/planning/epics/EPIC-030.md) — EPIC-007 gave tapps-brain operational observability: metrics (counters, histograms), audit trail, and health checks. ... *(updated 2026-04-05)*
- [EPIC-031: Continuous Improvement Flywheel — Feedback-Driven Quality Loop](docs/planning/epics/EPIC-031.md) — EPIC-029 collects feedback signals. EPIC-030 assesses quality and detects anomalies. This epic closes the loop: it tu... *(updated 2026-04-05)*
- [EPIC-032: OTel GenAI Semantic Conventions — Standardized Telemetry Export](docs/planning/epics/EPIC-032.md) — Upgrade tapps-brain's optional OpenTelemetry exporter to comply with the OpenTelemetry GenAI and MCP semantic convent... *(updated 2026-04-15)*
- [EPIC-033: OpenClaw Plugin SDK Alignment — Fix API Type Drift and Runtime Bugs](docs/planning/epics/EPIC-033.md) — The OpenClaw plugin (`openclaw-plugin/src/index.ts`) defines a custom `OpenClawPluginApi` interface (lines 184-202) i... *(updated 2026-04-05)*
- [EPIC-034: Production Readiness QA Remediation - Lint, Format, Typing, Test Stability](docs/planning/epics/EPIC-034.md) — The production-readiness review found hard blockers: failing Ruff checks, formatting drift, and unstable plugin test ... *(updated 2026-04-05)*
- [EPIC-035: OpenClaw Install and Upgrade UX Consistency](docs/planning/epics/EPIC-035.md) — The readiness review found documentation and command inconsistencies that can cause failed installs, failed upgrades,... *(updated 2026-04-05)*
- [EPIC-036: Release Gate Hardening for Production-Ready OpenClaw Distribution](docs/planning/epics/EPIC-036.md) — Readiness is currently assessed manually and can regress between releases. To keep production readiness durable, the ... *(updated 2026-04-05)*
- [EPIC-037: OpenClaw Plugin SDK Realignment — Fix API Contract to Match Real SDK](docs/planning/epics/EPIC-037.md) — The OpenClaw plugin ships a hand-written `openclaw-sdk.d.ts` (ambient type declarations) that diverges from the real ... *(updated 2026-04-05)*
- [EPIC-038: OpenClaw Plugin Simplification — Remove Dead Compat Layers and Streamline](docs/planning/epics/EPIC-038.md) — After EPIC-037 aligns the plugin with the real OpenClaw SDK, a significant amount of dead weight remains in the codeb... *(updated 2026-04-05)*
- [EPIC-039: Replace Custom MCP Client with Official @modelcontextprotocol/sdk](docs/planning/epics/EPIC-039.md) — The OpenClaw plugin's `mcp_client.ts` is a hand-rolled JSON-RPC 2.0 client (~466 lines) that implements Content-Lengt... *(updated 2026-04-05)*
- [EPIC-040: tapps-brain v2.0 — Research-Driven Upgrades](docs/planning/epics/EPIC-040.md) — Per-story checkboxes, phases, and GitHub issue mapping (**#24–#44**) live in **`.ralph/fix_plan.md`** under `## EPIC-... *(updated 2026-04-09)*
- [EPIC-041: Federation hub + Hive groups + operator docs](docs/planning/epics/EPIC-041.md) — Post–**#49** (v1 project-local `memory_group`) work queued on GitHub and in [`open-issues-roadmap.md`](../open-issues... *(updated 2026-04-05)*
- [Improvement program: `features-and-technologies.md` (index)](docs/planning/epics/EPIC-042-feature-tech-index.md) — **Source map:** [`docs/engineering/features-and-technologies.md`](../../engineering/features-and-technologies.md) *(updated 2026-04-09)*
- [EPIC-042: Retrieval and ranking (RAG-style memory)](docs/planning/epics/EPIC-042.md) — Maps to **§1** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Core product valu... *(updated 2026-04-12)*
- [EPIC-043: Storage, persistence, and schema](docs/planning/epics/EPIC-043.md) — Maps to **§2** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Ground truth: `pe... *(updated 2026-04-05)*
- [EPIC-044: Ingestion, deduplication, and lifecycle](docs/planning/epics/EPIC-044.md) — Maps to **§3** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). *(updated 2026-04-09)*
- [EPIC-045: Multi-tenant, sharing, and sync models](docs/planning/epics/EPIC-045.md) — Maps to **§4** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Overlaps shipped ... *(updated 2026-04-05)*
- [EPIC-046: Agent / tool integration](docs/planning/epics/EPIC-046.md) — Maps to **§5** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). *(updated 2026-04-05)*
- [EPIC-047: Quality loop, observability, and ops](docs/planning/epics/EPIC-047.md) — Maps to **§6** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Relates to **EPIC... *(updated 2026-04-05)*
- [EPIC-048: Optional / auxiliary capabilities](docs/planning/epics/EPIC-048.md) — Maps to **§7** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). *(updated 2026-04-09)*
- [EPIC-049: Dependency extras (install surface)](docs/planning/epics/EPIC-049.md) — Maps to **§8** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) and `pyproject.tom... *(updated 2026-04-07)*
- [EPIC-050: Concurrency and runtime model](docs/planning/epics/EPIC-050.md) — Maps to **§9** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) and roadmap backlo... *(updated 2026-04-12)*
- [EPIC-051: Cross-cutting review (§10 checklist)](docs/planning/epics/EPIC-051.md) — Maps to **§10** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) — meta items that... *(updated 2026-04-12)*
- [EPIC-052: Full Codebase Code Review — 2026-Q2 Sweep](docs/planning/epics/EPIC-052.md) — The last full code-review sweep completed on 2026-03-23 across **EPIC-017 → EPIC-025**. Since then, substantial code ... *(updated 2026-04-05)*
- [EPIC-053: Per-Agent Brain Identity](docs/planning/epics/EPIC-053.md) — Today `agent_id` defaults to `"unknown"` across all of tapps-brain. The MCP server accepts `--agent-id` but most call... *(updated 2026-04-09)*
- [EPIC-054: Hive Backend Abstraction Layer](docs/planning/epics/EPIC-054.md) — `HiveStore` and `FederatedStore` are tightly coupled to SQLite. Every method directly executes SQL against a local `.... *(updated 2026-04-09)*
- [EPIC-055: PostgreSQL Hive & Federation Backend](docs/planning/epics/EPIC-055.md) — EPIC-054 introduced backend protocols for `HiveBackend` and `FederationBackend` with SQLite adapters. This epic imple... *(updated 2026-04-09)*
- [EPIC-056: Declarative Group Membership & Expert Publishing](docs/planning/epics/EPIC-056.md) — Today Hive groups exist (`create_group()`, `add_group_member()`) but are **imperatively managed** — callers must expl... *(updated 2026-04-09)*
- [EPIC-057: Unified Agent API — Hide the Complexity](docs/planning/epics/EPIC-057.md) — After EPIC-053 through EPIC-056, tapps-brain has per-agent brains, backend abstraction, Postgres backends, declarativ... *(updated 2026-04-09)*
- [EPIC-058: Docker & Deployment Support](docs/planning/epics/EPIC-058.md) — tapps-brain is currently distributed as a Python package with no Docker artifacts. The target architecture requires: *(updated 2026-04-09)*
- [EPIC-059: Greenfield v3 — Postgres-Only Persistence Plane](docs/planning/epics/EPIC-059.md) — Ship a v3 persistence layer where **PostgreSQL is the only supported engine** for all durable data (private agent mem... *(updated 2026-04-15)*
- [EPIC-060: Greenfield v3 — Agent-First Core & Minimal Runtime API](docs/planning/epics/EPIC-060.md) — Center all product documentation and integrations on an **agent-first** Python API; expose **minimal** HTTP (or gRPC)... *(updated 2026-04-15)*
- [EPIC-061: Greenfield v3 — Observability-First Product (Simple & Complete)](docs/planning/epics/EPIC-061.md) — Make **OpenTelemetry** traces and metrics the default observability path for save/recall/hive operations, with health... *(updated 2026-04-15)*
- [EPIC-062: Greenfield v3 — MCP-Primary Integration & Environment Contract](docs/planning/epics/EPIC-062.md) — Make **MCP** the primary IDE/agent integration, wiring the MCP server to the **same Postgres-backed** Hive and config... *(updated 2026-04-15)*
- [EPIC-063: Greenfield v3 — Trust Boundaries & Postgres Enforcement](docs/planning/epics/EPIC-063.md) — Enforce **least-privilege Postgres roles**, document an **RLS vs app-layer** decision, and publish a **threat model**... *(updated 2026-04-15)*
- [EPIC-064: Product surface — narrative motion, deep insight, NLT brand fidelity](docs/planning/epics/EPIC-064.md) — The **greenfield v3** epics created the same day ([EPIC-059](EPIC-059.md)–[EPIC-063](EPIC-063.md)) are infrastructure... *(updated 2026-04-15)*
- [Epic 65: Live Always-On Dashboard — Real-Time tapps-brain and Hive Monitoring](docs/planning/epics/EPIC-065.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [Epic 66: Postgres-Only Persistence Plane — Production Readiness](docs/planning/epics/EPIC-066.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [Epic 67: Docker Hive Stack — Production Completeness](docs/planning/epics/EPIC-067.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [Epic 68: Multi-page brain-visual dashboard — hash-routed navigation](docs/planning/epics/EPIC-068.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [EPIC-069 — next-session resumption prompt](docs/planning/epics/EPIC-069-next-session-prompt.md) — Drop this into a fresh Claude Code session to pick up where 2026-04-14 left off. *(updated 2026-04-14)*
- [Epic 69: Multi-tenant project registration and profile delivery over MCP](docs/planning/epics/EPIC-069.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-14)*
- [Epic 70: HTTP/MCP transport parity — Streamable HTTP + service-layer refactor](docs/planning/epics/EPIC-070.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [EPIC-071: TappsBrainClient & AsyncTappsBrainClient — SDK Hardening and Documentation](docs/planning/epics/EPIC-071.md) — Harden the `TappsBrainClient` and `AsyncTappsBrainClient` HTTP clients shipped in v3.6.0 with proper error classifica... *(updated 2026-04-15)*
- [EPIC-072: Async-Native Postgres Core — psycopg3 AsyncConnection Upgrade](docs/planning/epics/EPIC-072.md) — Replace the `asyncio.to_thread()` shim in `AsyncMemoryStore` with native `psycopg3` async connections (`psycopg.Async... *(updated 2026-04-15)*
- [Story 65.1 -- GET /snapshot live endpoint on HttpAdapter](docs/planning/epics/stories/STORY-065.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.2 -- Dashboard live polling mode](docs/planning/epics/stories/STORY-065.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 65.3 -- Purge stale and privacy-gated components](docs/planning/epics/stories/STORY-065.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 65.4 -- Hive hub deep monitoring panel](docs/planning/epics/stories/STORY-065.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.5 -- Agent registry live table](docs/planning/epics/stories/STORY-065.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.6 -- Memory velocity panel](docs/planning/epics/stories/STORY-065.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.7 -- Retrieval pipeline live metrics panel](docs/planning/epics/stories/STORY-065.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 66.1 -- Consolidation merge audit emission](docs/planning/epics/stories/STORY-066.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.10 -- pg_tde operator runbook](docs/planning/epics/stories/STORY-066.10.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.11 -- Postgres backup and restore runbook](docs/planning/epics/stories/STORY-066.11.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.12 -- Engineering docs drift sweep](docs/planning/epics/stories/STORY-066.12.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.13 -- Postgres integration tests replacing deleted SQLite-coupled tests](docs/planning/epics/stories/STORY-066.13.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.14 -- Final test failure sweep — 90 to zero](docs/planning/epics/stories/STORY-066.14.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.2 -- Bi-temporal as_of filter on PostgresPrivateBackend.search](docs/planning/epics/stories/STORY-066.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.3 -- GC archive Postgres table (migration 006)](docs/planning/epics/stories/STORY-066.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.4 -- MCP tool registration audit and fix](docs/planning/epics/stories/STORY-066.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.5 -- Version consistency unblock for openclaw-skill](docs/planning/epics/stories/STORY-066.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.6 -- CI workflow with ephemeral Postgres service container](docs/planning/epics/stories/STORY-066.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.7 -- Connection pool tuning and health JSON pool fields](docs/planning/epics/stories/STORY-066.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.8 -- Auto-migrate on startup gate](docs/planning/epics/stories/STORY-066.8.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.9 -- Behavioural parity doc and load smoke benchmark](docs/planning/epics/stories/STORY-066.9.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 67.1 -- Add Dockerfile.http and tapps-brain-http compose service](docs/planning/epics/stories/STORY-067.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 67.2 -- Fix tapps-visual nginx upstream and validate /snapshot end-to-end](docs/planning/epics/stories/STORY-067.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 67.3 -- Default-credential guard in make hive-deploy](docs/planning/epics/stories/STORY-067.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 67.4 -- TLS documentation and nginx SSL config for the visual endpoint](docs/planning/epics/stories/STORY-067.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 67.5 -- make hive-smoke end-to-end stack smoke test](docs/planning/epics/stories/STORY-067.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 68.1 -- Hash router and persistent side-nav shell](docs/planning/epics/stories/STORY-068.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.2 -- Overview page — decision strip and health summary](docs/planning/epics/stories/STORY-068.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.3 -- Health page — scorecard with filter bar and issue workflow](docs/planning/epics/stories/STORY-068.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.4 -- Memory page — pulse, groups, tags, histograms](docs/planning/epics/stories/STORY-068.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.5 -- Retrieval page — mode, latency histogram, vector stats](docs/planning/epics/stories/STORY-068.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.6 -- Agents and Hive page — SVG topology diagram and registry](docs/planning/epics/stories/STORY-068.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.7 -- Integrity and Export page — checks, privacy tiers, export workflow](docs/planning/epics/stories/STORY-068.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.8 -- Quality sweep — docs-mcp, tapps-mcp, Lighthouse, accessibility audit](docs/planning/epics/stories/STORY-068.8.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.1 -- Extract pure service layer from MCP tool bodies](docs/planning/epics/stories/STORY-070.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.2 -- Adopt FastMCP and Streamable HTTP transport](docs/planning/epics/stories/STORY-070.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.3 -- Replace stdlib http_adapter with FastAPI app](docs/planning/epics/stories/STORY-070.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.4 -- Mount FastMCP Streamable HTTP at /mcp with tenant middleware](docs/planning/epics/stories/STORY-070.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.5 -- Parity test — MCP tool registry versus HTTP route manifest](docs/planning/epics/stories/STORY-070.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.6 -- Update Docker image and compose for unified HTTP/MCP surface](docs/planning/epics/stories/STORY-070.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.7 -- AgentForge integration spike and remote-MCP migration guide](docs/planning/epics/stories/STORY-070.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Next session — agent handoff prompt](docs/planning/next-session-prompt.md) — Copy everything below the line into a new chat (or Ralph task) as the **user message**. *(updated 2026-04-15)*
- [Open Issues Roadmap](docs/planning/open-issues-roadmap.md) — Last updated: 2026-04-09 — **v3.2.0** — EPIC-048 complete (all 6 stories); default embedding → `BAAI/bge-small-en-v1.... *(updated 2026-04-12)*
- [Story 70.1 -- Streamable-HTTP MCP transport](docs/stories/STORY-070.1-streamable-http-mcp-transport.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.10 -- Native async parity](docs/stories/STORY-070.10-async-parity.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.11 -- Official TappsBrainClient (sync + async)](docs/stories/STORY-070.11-tapps-brain-client.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.12 -- OTel + Prometheus label enrichment](docs/stories/STORY-070.12-otel-prom-labels.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.13 -- AgentForge BrainBridge port — reference implementation](docs/stories/STORY-070.13-agentforge-bridge-example.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.14 -- Compatibility test suite](docs/stories/STORY-070.14-compat-suite.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.2 -- Transport-agnostic service layer](docs/stories/STORY-070.2-service-layer.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.3 -- Memory CRUD on HttpAdapter](docs/stories/STORY-070.3-memory-crud-http.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.4 -- Error taxonomy + retry-ability semantics](docs/stories/STORY-070.4-error-taxonomy.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.5 -- Idempotency keys for writes](docs/stories/STORY-070.5-idempotency-keys.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.6 -- Bulk operations](docs/stories/STORY-070.6-bulk-operations.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.7 -- Per-call identity (agent_id / scope / group)](docs/stories/STORY-070.7-per-call-identity.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.8 -- Per-tenant auth tokens](docs/stories/STORY-070.8-per-tenant-auth.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.9 -- Operator-tool separation](docs/stories/STORY-070.9-operator-tools-split.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
## Release

- [Code Inventory and Documentation Gaps](docs/engineering/code-inventory-and-doc-gaps.md) — - **Core orchestration** *(updated 2026-04-12)*
