# tapps-brain — Documentation Index

**265 documents** across **9 categories**

## Overview

| Category | Count |
|---|---|
| API Reference | 6 |
| Architecture | 17 |
| Configuration | 1 |
| Getting Started | 20 |
| Guides | 47 |
| Operations | 9 |
| Other | 11 |
| Planning | 150 |
| Release | 4 |

## API Reference

- [TappsMCP - instructions for AI assistants](../AGENTS.md) — <!-- tapps-agents-version: 3.3.0 --> *(updated 2026-04-24)*
- [— Documentation Index](DOCUMENTATION_INDEX.md) — **265 documents** across **9 categories** *(updated 2026-04-28)*
- [Code Inventory and Documentation Gaps](engineering/code-inventory-and-doc-gaps.md) — All source modules live in `src/tapps_brain/`. 80+ files organized into 9 layers. *(updated 2026-04-21)*
- [Data Stores and Schema Reference](engineering/data-stores-and-schema.md) — All durable stores use **PostgreSQL** ([ADR-007](../planning/adr/ADR-007-postgres-only-no-sqlite.md)). No SQLite fall... *(updated 2026-04-13)*
- [tapps-brain — Documentation Index](index.md) — **215 documents** across **8 categories** *(updated 2026-04-16)*
- [Kubernetes Liveness and Readiness Probes](operations/k8s-probes.md) — tapps-brain's HTTP adapter exposes two dedicated probe endpoints that map *(updated 2026-04-11)*
## Architecture

- [CLAUDE.md](../CLAUDE.md) — This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. *(updated 2026-04-24)*
- [Industry features and technologies (implementation map)](engineering/features-and-technologies.md) — **Audience:** Architecture and product review — what capability areas we cover, which libraries/patterns we use, and ... *(updated 2026-04-21)*
- [System Architecture (Implementation-Aligned)](engineering/system-architecture.md) — tapps-brain is designed for **many concurrent agents** without shared-DB bottlenecks: *(updated 2026-04-21)*
- [Configurable Memory Profiles — Design Document](planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md) *(updated 2026-04-15)*
- [ADR-001: Retrieval stack — embedded SQLite-first (defer learned sparse, ColBERT, managed vector DB)](planning/adr/ADR-001-retrieval-stack.md) — **Status:** Accepted *(updated 2026-04-08)*
- [ADR-002: Freshness — lazy decay + operator GC (defer wall-clock TTL jobs)](planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-003: Correctness — heuristic conflicts + offline review (defer ontology and in-product review queue)](planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-004: Scale — single-node SQLite posture (defer published QPS SLO and service extraction)](planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md) — **Status:** Superseded by [ADR-007](./ADR-007-postgres-only-no-sqlite.md) (2026-04-11) — Postgres-backed private memo... *(updated 2026-04-21)*
- [ADR-005: SQLCipher operations — passphrase runbook + backup verification (defer KMS product integration)](planning/adr/ADR-005-sqlcipher-key-backup-operations.md) — **Status:** Superseded by [ADR-007](./ADR-007-postgres-only-no-sqlite.md) (2026-04-11) — SQLCipher and the `[encrypti... *(updated 2026-04-21)*
- [ADR-006: Save-path observability — phase histograms + health summary (defer deeper metrics unless trigger (a))](planning/adr/ADR-006-save-path-observability.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-007: PostgreSQL-only persistence plane (SQLite fully removed)](planning/adr/ADR-007-postgres-only-no-sqlite.md) — Accepted (2026-04-10) *(updated 2026-04-12)*
- [ADR-008: No new public HTTP routes without MCP + library parity](planning/adr/ADR-008-no-http-without-mcp-library-parity.md) — Accepted (2026-04-10) *(updated 2026-04-11)*
- [ADR-009: Row Level Security on hive_memories — Ship in GA vs Defer](planning/adr/ADR-009-rls-ship-vs-defer.md) — Accepted (2026-04-11) *(updated 2026-04-14)*
- [ADR-010: Multi-tenant project identification and profile registration over MCP](planning/adr/ADR-010-multi-tenant-project-registration.md) — Proposed (2026-04-14) *(updated 2026-04-14)*
- [Design note: multi-scope memory (#49)](planning/design-issue-49-multi-scope-memory.md) — Epic **#49** (multi-group memory scopes: Hive, named groups, personal) needs a clear *(updated 2026-04-15)*
- [Agent memory systems — 2026 knowledge base](research/memory-systems-2026.md) — The agent-memory field in 2025–2026 has split along three axes that were open *(updated 2026-04-17)*
- [Session prompt — tapps-brain review fixes (TAP-508 → TAP-514)](../prompts/SESSION-tapps-brain-review-fixes.md) — You are working in `/home/wtthornton/code/tapps-brain`. This is a no-LLM Python brain service (uv, pytest, mypy --str... *(updated 2026-04-16)*
## Configuration

- [Skill](../openclaw-skill/SKILL.md) *(updated 2026-04-28)*
## Getting Started

- [tapps-brain](../README.md) — AI agents forget everything between sessions. **tapps-brain** gives them persistent, ranked memory that decays natura... *(updated 2026-04-21)*
- [Docker Artifacts for tapps-brain](../docker/README.md) — Quick reference for the Docker deployment of tapps-brain. The stack is a **unified** tapps-brain-http container (serv... *(updated 2026-04-21)*
- [GitHub Setup Guide](GITHUB_SETUP_GUIDE.md) — <!-- tapps-generated: v3.3.0 --> *(updated 2026-04-28)*
- [Ralph Setup Guide (Windows + WSL)](RALPH_SETUP_GUIDE.md) — Step-by-step guide for setting up Ralph on a new project. Covers the common pitfalls. *(updated 2026-04-05)*
- [tapps-brain benchmarks](benchmarks/README.md) — End-to-end QA benchmarks for tapps-brain. These are **answer-based** *(updated 2026-04-17)*
- [tapps-brain case studies](case-studies/README.md) — Production adopter case studies — how real teams run tapps-brain in their *(updated 2026-04-20)*
- [NLT Labs — Brand Style Sheet & Logo Pack Audit](design/nlt-brand/README.md) — | Item | Location | *(updated 2026-04-17)*
- [Engineering Documentation Baseline](engineering/README.md) — This folder is the code-aligned engineering reference for tapps-brain runtime behavior. *(updated 2026-04-12)*
- [Optional Features and Runtime Toggle Matrix](engineering/optional-features-matrix.md) — This matrix documents behavior changes from extras, feature checks, and profile-driven toggles. *(updated 2026-04-21)*
- [Getting Started with tapps-brain](guides/getting-started.md) — tapps-brain ships three first-class interfaces to the same memory engine. Choose the one that fits your workflow. *(updated 2026-04-14)*
- [Connecting a repo to the deployed tapps-brain via MCP](guides/mcp-client-repo-setup.md) — **Audience:** a human developer wiring Claude Code (or another MCP client) *(updated 2026-04-19)*
- [Install and upgrade tapps-brain for OpenClaw from GitHub (no PyPI)](guides/openclaw-install-from-git.md) — Use this guide when you want the **Python** package and **`tapps-brain-mcp`** installed or upgraded from the Git repo... *(updated 2026-04-05)*
- [AgentForge BrainBridge — Reference Port (STORY-070.13)](../examples/agentforge_bridge/README.md) — This directory contains a **documentation artefact** — a reference port of *(updated 2026-04-15)*
- [Brain visual (live dashboard)](../examples/brain-visual/README.md) — A static HTML/JS dashboard that polls the live tapps-brain `/snapshot` endpoint. There is no file-load or demo fallba... *(updated 2026-04-15)*
- [Coding project init — connect your project to tapps-brain](../examples/coding-project-init/README.md) — This scaffold wires a new project to a **deployed** tapps-brain hub in two dimensions: *(updated 2026-04-14)*
- [tapps-brain OpenClaw Plugin](../openclaw-plugin/README.md) — **Plugin version 2.0.3** (tracks the [tapps-brain](https://github.com/wtthornton/tapps-brain) Python release). *(updated 2026-04-13)*
- [tapps-brain-memory](../openclaw-skill/README.md) — **Persistent cross-session memory for OpenClaw agents.** *(updated 2026-04-13)*
- [@tapps-brain/langgraph](../packages/langgraph/README.md) — LangGraph `BaseStore` adapter backed by [tapps-brain](https://github.com/wtthornton/tapps-brain) persistent agent mem... *(updated 2026-04-18)*
- [@tapps-brain/sdk](../packages/sdk/README.md) — TypeScript SDK for [tapps-brain](https://github.com/wtthornton/tapps-brain) — a Postgres-backed persistent memory sys... *(updated 2026-04-18)*
- [tapps-brain — Database Migrations](../src/tapps_brain/migrations/README.md) — This folder contains **forward-only** SQL migrations for all tapps-brain Postgres backends. *(updated 2026-04-11)*
## Guides

- [Contributing to tapps-brain](../CONTRIBUTING.md) — Thanks for helping improve tapps-brain. This project uses **uv** for environments, **pytest** with a **≥95% coverage*... *(updated 2026-04-15)*
- [Agent integration guide](guides/agent-integration.md) — This page is the **operator contract** for AI agents using tapps-brain: the *(updated 2026-04-14)*
- [Agent.md Wiring Guide](guides/agent-md-wiring.md) — This guide explains how to grant tapps-brain MCP access in an `AGENT.md` file *(updated 2026-04-15)*
- [AgentForge Integration Guide (v3)](guides/agentforge-integration.md) — How any agent host — AgentForge, custom orchestrators, or bare Python scripts — *(updated 2026-04-21)*
- [Auto-Recall: Pre-Prompt Memory Injection](guides/auto-recall.md) — Auto-recall automatically searches the memory store for relevant context before an agent processes a user message, an... *(updated 2026-04-13)*
- [Claude Code hooks for tapps-brain](guides/claude-code-hooks.md) — **Audience:** a human developer wiring Claude Code in a repo that talks to *(updated 2026-04-17)*
- [ClawHub Submission Guide](guides/clawhub-submission.md) — How to submit `tapps-brain-memory` to the ClawHub skill directory. *(updated 2026-04-05)*
- [TappsBrainClient — official Python client](guides/client.md) — `TappsBrainClient` (sync) and `AsyncTappsBrainClient` (async) let you consume a *(updated 2026-04-16)*
- [Memory decay: power-law vs exponential](guides/decay.md) — This guide explains the two decay models tapps-brain supports, how to choose between them, and the calibration math b... *(updated 2026-04-17)*
- [tapps-brain Deployment Guide](guides/deployment.md) — This guide covers deploying tapps-brain as a **shared networked service** — *(updated 2026-04-21)*
- [Pluggable lookup engine for doc validation](guides/doc-validation-lookup-engine.md) — `tapps-brain` validates memory entries against authoritative documentation using a *(updated 2026-04-09)*
- [Embedding model card (default semantic search)](guides/embedding-model-card.md) — This page documents the **default** dense embedding stack for built-in vector / hybrid retrieval (**EPIC-042** STORY-... *(updated 2026-04-21)*
- [Error Taxonomy and Retry Semantics](guides/errors.md) — tapps-brain uses a **stable error code vocabulary** so client circuit-breakers and retry policies can be written once... *(updated 2026-04-14)*
- [Federation Guide](guides/federation.md) — Cross-project memory sharing via a central hub store. *(updated 2026-04-13)*
- [Fleet Topology: N FastAPI Containers + 1 Brain Sidecar](guides/fleet-topology.md) *(updated 2026-04-18)*
- [Hive Deployment Guide](guides/hive-deployment.md) — One Postgres, one brain container, one API. Hive rides along automatically because the brain falls back to `TAPPS_BRA... *(updated 2026-04-28)*
- [Hive Operations Guide](guides/hive-operations.md) — Day-to-day operational procedures for the tapps-brain Hive Postgres backend. *(updated 2026-04-08)*
- [TLS for the Hive Stack (EPIC-067 STORY-067.4)](guides/hive-tls.md) — This guide covers adding HTTPS to the `tapps-visual` dashboard endpoint. *(updated 2026-04-13)*
- [Hive vs federation — when to use which](guides/hive-vs-federation.md) — Both features move memories across boundaries, but the **boundary** and **mechanics** differ. Use this page first, th... *(updated 2026-04-15)*
- [Hive Guide: Cross-Agent Memory Sharing](guides/hive.md) — The Hive is tapps-brain's cross-agent memory layer. Agents share knowledge through Hive namespaces (`universal`, per-... *(updated 2026-04-21)*
- [HTTP Adapter](guides/http-adapter.md) — The tapps-brain HTTP adapter is the language-neutral entrypoint to the brain. It runs alongside the MCP server (or st... *(updated 2026-04-27)*
- [Idempotency Keys for Write Operations](guides/idempotency.md) — **Feature flag:** `TAPPS_BRAIN_IDEMPOTENCY=1` (default OFF) *(updated 2026-04-14)*
- [LangGraph Store Adapter (`@tapps-brain/langgraph`)](guides/langgraph-adapter.md) — **Package:** `@tapps-brain/langgraph` *(updated 2026-04-18)*
- [Linear automation via a dedicated Claude Agent user](guides/linear-claude-agent.md) — **Status:** PLANNED — key not yet generated, poller not yet built. This *(updated 2026-04-18)*
- [LLM Brain Guide](guides/llm-brain-guide.md) — Instructions for LLMs and AI agents using the tapps-brain simplified MCP tools. *(updated 2026-04-08)*
- [MCP tools for repo-embedded agents](guides/mcp-tools-for-agents.md) — Complete reference for every tool the deployed tapps-brain exposes over MCP (55 tools, verified live against `tapps-b... *(updated 2026-04-19)*
- [MCP Server: Using tapps-brain with AI Assistants](guides/mcp.md) — tapps-brain exposes its full API via the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP), making per... *(updated 2026-04-14)*
- [Memory decay and FSRS-style stability](guides/memory-decay-and-fsrs.md) — This document is the **product decision** for **EPIC-042** STORY-042.8: how tapps-brain combines time-based decay wit... *(updated 2026-04-15)*
- [Memory relay (sub-agent → primary)](guides/memory-relay.md) — Cross-node setups: only the primary host runs tapps-brain. Sub-agents build a **relay** JSON envelope so the primary ... *(updated 2026-04-05)*
- [Memory scopes: project group vs Hive vs profile (GitHub #49)](guides/memory-scopes.md) — Three concepts are easy to confuse. They are **separate** in tapps-brain. *(updated 2026-04-15)*
- [Migration Guide: tapps-brain 3.5.x → 3.6](guides/migration-3.5-to-3.6.md) — This guide covers the breaking changes and migration steps required when *(updated 2026-04-15)*
- [Migration 3.6 → 3.7](guides/migration-3.6-to-3.7.md) — Upgrading an existing v3.6.x deployment to v3.7.x (including v3.7.2). Three concrete changes; the rest is backwards c... *(updated 2026-04-16)*
- [Observability](guides/observability.md) — tapps-brain exposes structured **metrics**, **health**, **audit**, **diagnostics**, and **feedback** surfaces through... *(updated 2026-04-21)*
- [OpenClaw Install and Upgrade Runbook (Canonical)](guides/openclaw-runbook.md) — This is the source-of-truth runbook for installing and upgrading tapps-brain in OpenClaw. *(updated 2026-04-12)*
- [tapps-brain for OpenClaw](guides/openclaw.md) — Persistent cross-session memory for your OpenClaw agents. MCP tool and resource *(updated 2026-04-15)*
- [Postgres Backup and Restore — tapps-brain](guides/postgres-backup.md) — tapps-brain stores **all durable state** in PostgreSQL (ADR-007): private memories, *(updated 2026-04-12)*
- [Environment Variable Reference](guides/postgres-dsn.md) — This is the **canonical environment variable contract** for tapps-brain v3. *(updated 2026-04-21)*
- [pg_tde Operator Runbook](guides/postgres-tde.md) — **Applies to:** Percona Distribution for PostgreSQL 17 + pg_tde 2.1.2 (released 2026-03-02) *(updated 2026-04-13)*
- [Profile Catalog](guides/profile-catalog.md) — tapps-brain ships with 6 built-in profiles covering common AI agent use cases. Each profile can be used directly, ext... *(updated 2026-04-06)*
- [Profile Limits: Research and Rationale](guides/profile-limits-rationale.md) — This document explains the evidence behind tapps-brain's built-in profile defaults. *(updated 2026-04-13)*
- [Memory Profiles: Designing Custom Memory for Any AI Agent](guides/profiles.md) — tapps-brain ships with a configurable profile system that lets you define custom memory layers, decay models, scoring... *(updated 2026-04-10)*
- [Remote MCP integration (Streamable HTTP)](guides/remote-mcp-integration.md) — This guide describes how remote agents (AgentForge, OpenClaw, etc.) connect to *(updated 2026-04-17)*
- [Save conflicts: offline review and NLI backlog](guides/save-conflict-nli-offline.md) — Save-time conflict detection uses deterministic text similarity (`detect_save_conflicts` in `contradictions.py`) when... *(updated 2026-04-05)*
- [Scope Audit: agent_scope / Group / Hive — Allowed Namespaces and Operations](guides/scope-audit.md) *(updated 2026-04-16)*
- [TypeScript SDK (`@tapps-brain/sdk`)](guides/typescript-sdk.md) — **Package:** `@tapps-brain/sdk` *(updated 2026-04-18)*
- [Visual snapshot (`brain-visual.json`)](guides/visual-snapshot.md) — Export a **versioned JSON snapshot** of store health, tier mix, and related signals for static dashboards and the bra... *(updated 2026-04-09)*
- [Write-Path Trade-off Guide](guides/write-path-tradeoff.md) — tapps-brain supports two write-path modes: **deterministic** (default) and *(updated 2026-04-18)*
## Operations

- [Tapps Hive Password](../docker/secrets/tapps_hive_password.txt) — tapps *(updated 2026-04-16)*
- [Tapps Http Auth Token](../docker/secrets/tapps_http_auth_token.txt) — debug-token *(updated 2026-04-16)*
- [DB Roles Runbook — tapps-brain](operations/db-roles-runbook.md) — **Covers EPIC-063 STORY-063.1 + STORY-063.2: least-privilege Postgres roles.** *(updated 2026-04-12)*
- [Operator Runbook — tapps-brain Observability](operations/observability-runbook.md) — See [`k8s-probes.md`](k8s-probes.md) for full probe spec. *(updated 2026-04-12)*
- [Postgres Backup Runbook — tapps-brain (Ops On-Call)](operations/postgres-backup-runbook.md) — **Audience:** On-call engineers and SREs. *(updated 2026-04-13)*
- [Deploying tapps-brain to OpenClaw](planning/DEPLOY-OPENCLAW.md) — There are **two complementary deployment paths** for getting tapps-brain into OpenClaw: *(updated 2026-04-15)*
- [Agent memory systems — comparative scorecard (2026-04-17)](research/memory-systems-scorecard.md) *(updated 2026-04-20)*
- [Story 70.15 -- Docker + docs — one binary, both transports](stories/STORY-070.15-docker-unified.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Ralph Feedback Report](../ralph-feedback-report.md) — **Project:** tapps-brain *(updated 2026-04-05)*
## Other

- [Tech Stack](../TECH_STACK.md) — - **Type:** library *(updated 2026-04-24)*
- [Case study: [Adopter name / project]](case-studies/TEMPLATE.md) — | Parameter | Value | *(updated 2026-04-20)*
- [Core Call Flows](engineering/call-flows.md) — This document maps the dominant runtime call paths as implemented now. *(updated 2026-04-21)*
- [tapps-brain v3 Threat Model](engineering/threat-model.md) — **Scope:** tapps-brain v3.0 — Postgres-only persistence plane with private agent memory, *(updated 2026-04-12)*
- [Ralph Bug: Live Mode JSONL Crash in Response Analyzer](ralph-jsonl-crash-bug.md) — **Severity:** Critical (silent loop termination, no error logged) *(updated 2026-04-05)*
- [tapps-brain](../llms.txt) — - Version: 3.14.2 *(updated 2026-04-28)*
- [Coder](../tests/fixtures/profile_tool_sets/coder.txt) — brain_forget *(updated 2026-04-20)*
- [Full](../tests/fixtures/profile_tool_sets/full.txt) — agent_create *(updated 2026-04-20)*
- [Operator](../tests/fixtures/profile_tool_sets/operator.txt) — agent_create *(updated 2026-04-20)*
- [Reviewer](../tests/fixtures/profile_tool_sets/reviewer.txt) — brain_recall *(updated 2026-04-20)*
- [Seeder](../tests/fixtures/profile_tool_sets/seeder.txt) — brain_status *(updated 2026-04-20)*
## Planning

- [tapps-brain Cleanup & Simplification Plan](../CLEANUP-PLAN.md) — **Date:** 2026-04-08 *(updated 2026-04-15)*
- [LoCoMo benchmark](benchmarks/locomo.md) — - **Paper:** [Maharana et al. 2024, arXiv:2402.17753](https://arxiv.org/abs/2402.17753) *(updated 2026-04-17)*
- [LongMemEval benchmark](benchmarks/longmemeval.md) — - **Paper:** [Xiao et al. 2024, arXiv:2410.10813](https://arxiv.org/abs/2410.10813) (ICLR 2025) *(updated 2026-04-17)*
- [v3 Behavioral Parity — What Changed vs v2](engineering/v3-behavioral-parity.md) — **Epic:** [EPIC-059](../planning/epics/EPIC-059.md) — Greenfield v3 Postgres-Only Persistence Plane *(updated 2026-04-12)*
- [Epic 70: AgentForge Integration — Remote-First Brain as a Shared Service](epics/EPIC-070-agentforge-integration.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-14)*
- [Epic Validation — Regression Runbook](operations/epic-validation-regression.md) — This runbook verifies that the **Epic Validation** CI job (`.github/workflows/epic-validation.yml`) *(updated 2026-04-11)*
- [Telemetry Policy — Allowed and Forbidden Attributes](operations/telemetry-policy.md) — The following attributes may be set on OTel spans.  All values are **bounded enums *(updated 2026-04-11)*
- [Agent Feature Governance](planning/AGENT_FEATURE_GOVERNANCE.md) — Last updated: 2026-03-27 *(updated 2026-04-05)*
- [Feature Feasibility Criteria (Agent Standard)](planning/FEATURE_FEASIBILITY_CRITERIA.md) — Last updated: 2026-03-27 (web-calibrated pass) *(updated 2026-04-15)*
- [Issue triage — saved searches and board setup](planning/ISSUE_TRIAGE_VIEWS.md) — Last updated: 2026-03-27 *(updated 2026-04-05)*
- [Planning Conventions](planning/PLANNING.md) — This document defines how epics, stories, and tasks are structured in this project so that both humans and AI coding ... *(updated 2026-04-21)*
- [Project status snapshot](planning/STATUS.md) — **Last updated:** 2026-04-20 (America/Los_Angeles) — **v3.10.0** — Security batch (TAP-626–655): per-tenant auth bypa... *(updated 2026-04-21)*
- [SQLite to Postgres - Meeting Notes](planning/archive/sqlite-to-postgres-meeting-notes.md) — Status: active discussion *(updated 2026-04-05)*
- [Implementation plan: dynamic tapps-brain visual identity](planning/brain-visual-implementation-plan.md) — Track progress here for a **modern, per-instance-unique** visual representation of tapps-brain (dashboard, marketing,... *(updated 2026-04-15)*
- [Phase 2 — Follow-up issues (ready to file)](planning/engineering-doc-phase2-follow-up-issues.md) — **Purpose:** Concrete, prioritized cleanup items derived from `docs/engineering/code-inventory-and-doc-gaps.md` and f... *(updated 2026-04-05)*
- [Engineering Documentation Task - System Ground Truth](planning/engineering-documentation-task.md) — Status: in_progress *(updated 2026-04-05)*
- [Epic #49 — actionable child issues (multi-scope memory)](planning/epic-49-tasks.md) — **Parent:** GitHub **[#49](https://github.com/wtthornton/tapps-brain/issues/49)** — **closed** (v1 complete 2026-03-29). *(updated 2026-04-05)*
- [EPIC-001: Test Suite Quality — Raise to A+](planning/epics/EPIC-001.md) — The test suite currently sits at 79% line coverage (521 tests, 13s runtime). A detailed review identified structural ... *(updated 2026-04-05)*
- [EPIC-002: Integration Wiring — Connect Standalone Modules to the Runtime](planning/epics/EPIC-002.md) — Epic 1 raised test coverage to 96.59% across 792 tests — the codebase is well-tested at the unit level. However, seve... *(updated 2026-04-05)*
- [EPIC-003: Auto-Recall — Pre-Prompt Memory Injection Hook](planning/epics/EPIC-003.md) — tapps-brain has a complete retrieval engine (BM25 + FTS5 + composite scoring + optional vector search + RRF fusion) a... *(updated 2026-04-05)*
- [EPIC-004: Bi-Temporal Fact Versioning with Validity Windows](planning/epics/EPIC-004.md) — tapps-brain currently tracks three timestamps per memory: `created_at` (when stored), `updated_at` (when last modifie... *(updated 2026-04-05)*
- [EPIC-005: CLI Tool for Memory Management and Operations](planning/epics/EPIC-005.md) — tapps-brain is currently a Python library only. Managing a memory store requires writing code — there's no way to ins... *(updated 2026-04-05)*
- [EPIC-006: Persistent Knowledge Graph and Semantic Queries](planning/epics/EPIC-006.md) — tapps-brain has a `relations.py` module that extracts entity relations (subject-predicate-object triples) and a `retr... *(updated 2026-04-05)*
- [EPIC-007: Observability — Metrics, Audit Trail Queries, and Health Checks](planning/epics/EPIC-007.md) — Partial implementation exists and is covered by tests: *(updated 2026-04-05)*
- [EPIC-008: MCP Server — Expose tapps-brain via Model Context Protocol](planning/epics/EPIC-008.md) — - `src/tapps_brain/mcp_server.py` — FastMCP server, tools (CRUD, search, list, recall, reinforce, ingest, supersede, ... *(updated 2026-04-05)*
- [EPIC-009: Multi-Interface Distribution — Library, CLI, and MCP Packaging](planning/epics/EPIC-009.md) — tapps-brain is becoming a three-interface project: a Python library (`import tapps_brain`), a CLI (`tapps-brain`), an... *(updated 2026-04-11)*
- [EPIC-010: Configurable Memory Profiles — Pluggable Layers and Scoring](planning/epics/EPIC-010.md) — tapps-brain's memory tiers (architectural/pattern/procedural/context), half-lives (180/60/30/14 days), and scoring we... *(updated 2026-04-05)*
- [EPIC-011: Hive — Multi-Agent Shared Brain with Domain Namespaces](planning/epics/EPIC-011.md) — tapps-brain currently serves one agent per project. But AI agent setups increasingly involve multiple specialized age... *(updated 2026-04-05)*
- [EPIC-012: OpenClaw Integration — ContextEngine Plugin and ClawHub Skill](planning/epics/EPIC-012.md) — tapps-brain can already serve OpenClaw as an MCP server (documented in `docs/guides/openclaw.md`). But this is a side... *(updated 2026-04-05)*
- [EPIC-013: Hive-Aware MCP Surface — Agent Identity, Scope Propagation, and OpenClaw Multi-Agent Wiring](planning/epics/EPIC-013.md) — EPIC-011 built the Hive core (HiveStore, AgentRegistry, PropagationEngine, conflict resolution, namespace isolation) ... *(updated 2026-04-05)*
- [EPIC-014: Hardening — Input Validation, Interface Parity, Resilience, and Onboarding Docs](planning/epics/EPIC-014.md) — EPICs 001–013 built a complete memory system with profiles, Hive multi-agent sharing, MCP server, OpenClaw integratio... *(updated 2026-04-05)*
- [EPIC-015: Analytics & Operational Surface](planning/epics/EPIC-015.md) — The tapps-brain library layer has ~36 public methods on `MemoryStore`, but 7 are not exposed via MCP or CLI. The know... *(updated 2026-04-05)*
- [EPIC-016: Test Suite Hardening](planning/epics/EPIC-016.md) — A coverage and quality audit of the 1641-test suite (95.54% coverage) revealed four categories of gaps: *(updated 2026-04-05)*
- [EPIC-017: Code Review — Storage & Data Model](planning/epics/EPIC-017.md) — With all 16 feature epics complete and BUG-001/BUG-002 fixes queued, the codebase is ready for systematic code review... *(updated 2026-04-05)*
- [EPIC-018: Code Review — Retrieval & Scoring](planning/epics/EPIC-018.md) — Full code review of all retrieval, scoring, ranking, and search files. The retrieval layer is where the source_trust ... *(updated 2026-04-05)*
- [EPIC-019: Code Review — Memory Lifecycle](planning/epics/EPIC-019.md) — Full code review of memory lifecycle management: decay, consolidation, GC, promotion, reinforcement. The consolidatio... *(updated 2026-04-05)*
- [EPIC-020: Code Review — Safety & Validation](planning/epics/EPIC-020.md) — Full code review of safety, injection detection, validation, and contradiction handling. These are security-critical ... *(updated 2026-04-05)*
- [EPIC-021: Code Review — Federation, Hive & Relations](planning/epics/EPIC-021.md) — Full code review of cross-project and cross-agent sharing systems. The HiveStore connection leak (BUG-001-C) and exce... *(updated 2026-04-05)*
- [EPIC-022: Code Review — Interfaces (MCP, CLI, IO)](planning/epics/EPIC-022.md) — Full code review of all user-facing interfaces: MCP server (54 tools), CLI (41 commands), IO, and markdown import. *(updated 2026-04-05)*
- [EPIC-023: Code Review — Config, Profiles & Observability](planning/epics/EPIC-023.md) — Full code review of configuration, profiles, metrics, and observability. *(updated 2026-04-05)*
- [EPIC-024: Code Review — Unit Tests (Part 1)](planning/epics/EPIC-024.md) — Review all unit test files for: test quality, missing edge cases, flaky test patterns, proper isolation, assertion co... *(updated 2026-04-05)*
- [EPIC-025: Code Review — Integration Tests, Benchmarks & TypeScript](planning/epics/EPIC-025.md) — Review all integration tests, benchmarks, test infrastructure, TypeScript plugin code, and configuration files. Final... *(updated 2026-04-05)*
- [EPIC-026: OpenClaw Memory Replacement — Replace memory-core with tapps-brain](planning/epics/EPIC-026.md) — EPIC-012 delivered a ContextEngine plugin that adds auto-recall and auto-capture hooks. *(updated 2026-04-05)*
- [EPIC-027: OpenClaw Full Feature Surface — Expose All 41 MCP Tools as Native Tools](planning/epics/EPIC-027.md) — **Note:** This epic was written against a **41-tool** MCP surface; tapps-brain exposes **54** tools as of v1.3.1. Cou... *(updated 2026-04-05)*
- [EPIC-028: OpenClaw Plugin Hardening — Stability, Tests, and Compatibility](planning/epics/EPIC-028.md) — The ContextEngine plugin (EPIC-012) and memory replacement (EPIC-026) provide the *(updated 2026-04-05)*
- [EPIC-029: Feedback Collection — LLM and Project Quality Signals](planning/epics/EPIC-029.md) — tapps-brain has strong observability (EPIC-007: metrics, audit trail, health checks) but no mechanism for consumers —... *(updated 2026-04-05)*
- [EPIC-030: Diagnostics & Self-Monitoring — Quality Scorecard and Anomaly Detection](planning/epics/EPIC-030.md) — EPIC-007 gave tapps-brain operational observability: metrics (counters, histograms), audit trail, and health checks. ... *(updated 2026-04-05)*
- [EPIC-031: Continuous Improvement Flywheel — Feedback-Driven Quality Loop](planning/epics/EPIC-031.md) — EPIC-029 collects feedback signals. EPIC-030 assesses quality and detects anomalies. This epic closes the loop: it tu... *(updated 2026-04-05)*
- [EPIC-032: OTel GenAI Semantic Conventions — Standardized Telemetry Export](planning/epics/EPIC-032.md) — Upgrade tapps-brain's optional OpenTelemetry exporter to comply with the OpenTelemetry GenAI and MCP semantic convent... *(updated 2026-04-15)*
- [EPIC-033: OpenClaw Plugin SDK Alignment — Fix API Type Drift and Runtime Bugs](planning/epics/EPIC-033.md) — The OpenClaw plugin (`openclaw-plugin/src/index.ts`) defines a custom `OpenClawPluginApi` interface (lines 184-202) i... *(updated 2026-04-05)*
- [EPIC-034: Production Readiness QA Remediation - Lint, Format, Typing, Test Stability](planning/epics/EPIC-034.md) — The production-readiness review found hard blockers: failing Ruff checks, formatting drift, and unstable plugin test ... *(updated 2026-04-05)*
- [EPIC-035: OpenClaw Install and Upgrade UX Consistency](planning/epics/EPIC-035.md) — The readiness review found documentation and command inconsistencies that can cause failed installs, failed upgrades,... *(updated 2026-04-05)*
- [EPIC-036: Release Gate Hardening for Production-Ready OpenClaw Distribution](planning/epics/EPIC-036.md) — Readiness is currently assessed manually and can regress between releases. To keep production readiness durable, the ... *(updated 2026-04-05)*
- [EPIC-037: OpenClaw Plugin SDK Realignment — Fix API Contract to Match Real SDK](planning/epics/EPIC-037.md) — The OpenClaw plugin ships a hand-written `openclaw-sdk.d.ts` (ambient type declarations) that diverges from the real ... *(updated 2026-04-05)*
- [EPIC-038: OpenClaw Plugin Simplification — Remove Dead Compat Layers and Streamline](planning/epics/EPIC-038.md) — After EPIC-037 aligns the plugin with the real OpenClaw SDK, a significant amount of dead weight remains in the codeb... *(updated 2026-04-05)*
- [EPIC-039: Replace Custom MCP Client with Official @modelcontextprotocol/sdk](planning/epics/EPIC-039.md) — The OpenClaw plugin's `mcp_client.ts` is a hand-rolled JSON-RPC 2.0 client (~466 lines) that implements Content-Lengt... *(updated 2026-04-05)*
- [EPIC-040: tapps-brain v2.0 — Research-Driven Upgrades](planning/epics/EPIC-040.md) — Per-story checkboxes, phases, and GitHub issue mapping (**#24–#44**) live in **`.ralph/fix_plan.md`** under `## EPIC-... *(updated 2026-04-09)*
- [EPIC-041: Federation hub + Hive groups + operator docs](planning/epics/EPIC-041.md) — Post–**#49** (v1 project-local `memory_group`) work queued on GitHub and in [`open-issues-roadmap.md`](../open-issues... *(updated 2026-04-05)*
- [Improvement program: `features-and-technologies.md` (index)](planning/epics/EPIC-042-feature-tech-index.md) — **Source map:** [`docs/engineering/features-and-technologies.md`](../../engineering/features-and-technologies.md) *(updated 2026-04-09)*
- [EPIC-042: Retrieval and ranking (RAG-style memory)](planning/epics/EPIC-042.md) — Maps to **§1** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Core product valu... *(updated 2026-04-12)*
- [EPIC-043: Storage, persistence, and schema](planning/epics/EPIC-043.md) — Maps to **§2** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Ground truth: `pe... *(updated 2026-04-05)*
- [EPIC-044: Ingestion, deduplication, and lifecycle](planning/epics/EPIC-044.md) — Maps to **§3** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). *(updated 2026-04-09)*
- [EPIC-045: Multi-tenant, sharing, and sync models](planning/epics/EPIC-045.md) — Maps to **§4** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Overlaps shipped ... *(updated 2026-04-05)*
- [EPIC-046: Agent / tool integration](planning/epics/EPIC-046.md) — Maps to **§5** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). *(updated 2026-04-05)*
- [EPIC-047: Quality loop, observability, and ops](planning/epics/EPIC-047.md) — Maps to **§6** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). Relates to **EPIC... *(updated 2026-04-05)*
- [EPIC-048: Optional / auxiliary capabilities](planning/epics/EPIC-048.md) — Maps to **§7** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md). *(updated 2026-04-09)*
- [EPIC-049: Dependency extras (install surface)](planning/epics/EPIC-049.md) — Maps to **§8** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) and `pyproject.tom... *(updated 2026-04-07)*
- [EPIC-050: Concurrency and runtime model](planning/epics/EPIC-050.md) — Maps to **§9** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) and roadmap backlo... *(updated 2026-04-12)*
- [EPIC-051: Cross-cutting review (§10 checklist)](planning/epics/EPIC-051.md) — Maps to **§10** of [`features-and-technologies.md`](../../engineering/features-and-technologies.md) — meta items that... *(updated 2026-04-12)*
- [EPIC-052: Full Codebase Code Review — 2026-Q2 Sweep](planning/epics/EPIC-052.md) — The last full code-review sweep completed on 2026-03-23 across **EPIC-017 → EPIC-025**. Since then, substantial code ... *(updated 2026-04-05)*
- [EPIC-053: Per-Agent Brain Identity](planning/epics/EPIC-053.md) — Today `agent_id` defaults to `"unknown"` across all of tapps-brain. The MCP server accepts `--agent-id` but most call... *(updated 2026-04-09)*
- [EPIC-054: Hive Backend Abstraction Layer](planning/epics/EPIC-054.md) — `HiveStore` and `FederatedStore` are tightly coupled to SQLite. Every method directly executes SQL against a local `.... *(updated 2026-04-09)*
- [EPIC-055: PostgreSQL Hive & Federation Backend](planning/epics/EPIC-055.md) — EPIC-054 introduced backend protocols for `HiveBackend` and `FederationBackend` with SQLite adapters. This epic imple... *(updated 2026-04-09)*
- [EPIC-056: Declarative Group Membership & Expert Publishing](planning/epics/EPIC-056.md) — Today Hive groups exist (`create_group()`, `add_group_member()`) but are **imperatively managed** — callers must expl... *(updated 2026-04-09)*
- [EPIC-057: Unified Agent API — Hide the Complexity](planning/epics/EPIC-057.md) — After EPIC-053 through EPIC-056, tapps-brain has per-agent brains, backend abstraction, Postgres backends, declarativ... *(updated 2026-04-09)*
- [EPIC-058: Docker & Deployment Support](planning/epics/EPIC-058.md) — tapps-brain is currently distributed as a Python package with no Docker artifacts. The target architecture requires: *(updated 2026-04-09)*
- [EPIC-059: Greenfield v3 — Postgres-Only Persistence Plane](planning/epics/EPIC-059.md) — Ship a v3 persistence layer where **PostgreSQL is the only supported engine** for all durable data (private agent mem... *(updated 2026-04-15)*
- [EPIC-060: Greenfield v3 — Agent-First Core & Minimal Runtime API](planning/epics/EPIC-060.md) — Center all product documentation and integrations on an **agent-first** Python API; expose **minimal** HTTP (or gRPC)... *(updated 2026-04-27)*
- [EPIC-061: Greenfield v3 — Observability-First Product (Simple & Complete)](planning/epics/EPIC-061.md) — Make **OpenTelemetry** traces and metrics the default observability path for save/recall/hive operations, with health... *(updated 2026-04-27)*
- [EPIC-062: Greenfield v3 — MCP-Primary Integration & Environment Contract](planning/epics/EPIC-062.md) — Make **MCP** the primary IDE/agent integration, wiring the MCP server to the **same Postgres-backed** Hive and config... *(updated 2026-04-27)*
- [EPIC-063: Greenfield v3 — Trust Boundaries & Postgres Enforcement](planning/epics/EPIC-063.md) — Enforce **least-privilege Postgres roles**, document an **RLS vs app-layer** decision, and publish a **threat model**... *(updated 2026-04-15)*
- [EPIC-064: Product surface — narrative motion, deep insight, NLT brand fidelity](planning/epics/EPIC-064.md) — The **greenfield v3** epics created the same day ([EPIC-059](EPIC-059.md)–[EPIC-063](EPIC-063.md)) are infrastructure... *(updated 2026-04-15)*
- [Epic 65: Live Always-On Dashboard — Real-Time tapps-brain and Hive Monitoring](planning/epics/EPIC-065.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [Epic 66: Postgres-Only Persistence Plane — Production Readiness](planning/epics/EPIC-066.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [Epic 67: Docker Hive Stack — Production Completeness](planning/epics/EPIC-067.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [Epic 68: Multi-page brain-visual dashboard — hash-routed navigation](planning/epics/EPIC-068.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [EPIC-069 — next-session resumption prompt](planning/epics/EPIC-069-next-session-prompt.md) — Drop this into a fresh Claude Code session to pick up where 2026-04-14 left off. *(updated 2026-04-14)*
- [Epic 69: Multi-tenant project registration and profile delivery over MCP](planning/epics/EPIC-069.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-14)*
- [Epic 70: HTTP/MCP transport parity — Streamable HTTP + service-layer refactor](planning/epics/EPIC-070.md) — <!-- docsmcp:start:metadata --> *(updated 2026-04-15)*
- [EPIC-071: TappsBrainClient & AsyncTappsBrainClient — SDK Hardening and Documentation](planning/epics/EPIC-071.md) — Harden the `TappsBrainClient` and `AsyncTappsBrainClient` HTTP clients shipped in v3.6.0 with proper error classifica... *(updated 2026-04-15)*
- [EPIC-072: Async-Native Postgres Core — psycopg3 AsyncConnection Upgrade](planning/epics/EPIC-072.md) — Replace the `asyncio.to_thread()` shim in `AsyncMemoryStore` with native `psycopg3` async connections (`psycopg.Async... *(updated 2026-04-15)*
- [EPIC-073: Per-profile MCP tool filtering](planning/epics/EPIC-073.md) — Today tapps-brain exposes **55 MCP tools** on `tapps-brain-mcp` and 68 on *(updated 2026-04-20)*
- [Story 65.1 -- GET /snapshot live endpoint on HttpAdapter](planning/epics/stories/STORY-065.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.2 -- Dashboard live polling mode](planning/epics/stories/STORY-065.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 65.3 -- Purge stale and privacy-gated components](planning/epics/stories/STORY-065.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 65.4 -- Hive hub deep monitoring panel](planning/epics/stories/STORY-065.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.5 -- Agent registry live table](planning/epics/stories/STORY-065.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.6 -- Memory velocity panel](planning/epics/stories/STORY-065.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 65.7 -- Retrieval pipeline live metrics panel](planning/epics/stories/STORY-065.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-11)*
- [Story 66.1 -- Consolidation merge audit emission](planning/epics/stories/STORY-066.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.10 -- pg_tde operator runbook](planning/epics/stories/STORY-066.10.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.11 -- Postgres backup and restore runbook](planning/epics/stories/STORY-066.11.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.12 -- Engineering docs drift sweep](planning/epics/stories/STORY-066.12.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.13 -- Postgres integration tests replacing deleted SQLite-coupled tests](planning/epics/stories/STORY-066.13.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.14 -- Final test failure sweep — 90 to zero](planning/epics/stories/STORY-066.14.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.2 -- Bi-temporal as_of filter on PostgresPrivateBackend.search](planning/epics/stories/STORY-066.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.3 -- GC archive Postgres table (migration 006)](planning/epics/stories/STORY-066.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.4 -- MCP tool registration audit and fix](planning/epics/stories/STORY-066.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.5 -- Version consistency unblock for openclaw-skill](planning/epics/stories/STORY-066.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.6 -- CI workflow with ephemeral Postgres service container](planning/epics/stories/STORY-066.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.7 -- Connection pool tuning and health JSON pool fields](planning/epics/stories/STORY-066.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.8 -- Auto-migrate on startup gate](planning/epics/stories/STORY-066.8.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 66.9 -- Behavioural parity doc and load smoke benchmark](planning/epics/stories/STORY-066.9.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 67.1 -- Add Dockerfile.http and tapps-brain-http compose service](planning/epics/stories/STORY-067.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 67.2 -- Fix tapps-visual nginx upstream and validate /snapshot end-to-end](planning/epics/stories/STORY-067.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 67.3 -- Default-credential guard in make hive-deploy](planning/epics/stories/STORY-067.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 67.4 -- TLS documentation and nginx SSL config for the visual endpoint](planning/epics/stories/STORY-067.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 67.5 -- make hive-smoke end-to-end stack smoke test](planning/epics/stories/STORY-067.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-13)*
- [Story 68.1 -- Hash router and persistent side-nav shell](planning/epics/stories/STORY-068.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.2 -- Overview page — decision strip and health summary](planning/epics/stories/STORY-068.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.3 -- Health page — scorecard with filter bar and issue workflow](planning/epics/stories/STORY-068.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.4 -- Memory page — pulse, groups, tags, histograms](planning/epics/stories/STORY-068.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.5 -- Retrieval page — mode, latency histogram, vector stats](planning/epics/stories/STORY-068.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.6 -- Agents and Hive page — SVG topology diagram and registry](planning/epics/stories/STORY-068.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.7 -- Integrity and Export page — checks, privacy tiers, export workflow](planning/epics/stories/STORY-068.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 68.8 -- Quality sweep — docs-mcp, tapps-mcp, Lighthouse, accessibility audit](planning/epics/stories/STORY-068.8.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.1 -- Extract pure service layer from MCP tool bodies](planning/epics/stories/STORY-070.1.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.2 -- Adopt FastMCP and Streamable HTTP transport](planning/epics/stories/STORY-070.2.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.3 -- Replace stdlib http_adapter with FastAPI app](planning/epics/stories/STORY-070.3.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.4 -- Mount FastMCP Streamable HTTP at /mcp with tenant middleware](planning/epics/stories/STORY-070.4.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.5 -- Parity test — MCP tool registry versus HTTP route manifest](planning/epics/stories/STORY-070.5.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.6 -- Update Docker image and compose for unified HTTP/MCP surface](planning/epics/stories/STORY-070.6.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Story 70.7 -- AgentForge integration spike and remote-MCP migration guide](planning/epics/stories/STORY-070.7.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-15)*
- [Next session — agent handoff prompt](planning/next-session-prompt.md) — Copy everything below the line into a new chat (or Ralph task) as the **user message**. *(updated 2026-04-15)*
- [Open Issues Roadmap — retired](planning/open-issues-roadmap.md) — **Status:** retired 2026-04-21. This file is no longer the canonical delivery queue. *(updated 2026-04-21)*
- [Story 70.1 -- Streamable-HTTP MCP transport](stories/STORY-070.1-streamable-http-mcp-transport.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.10 -- Native async parity](stories/STORY-070.10-async-parity.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.11 -- Official TappsBrainClient (sync + async)](stories/STORY-070.11-tapps-brain-client.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.12 -- OTel + Prometheus label enrichment](stories/STORY-070.12-otel-prom-labels.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.13 -- AgentForge BrainBridge port — reference implementation](stories/STORY-070.13-agentforge-bridge-example.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.14 -- Compatibility test suite](stories/STORY-070.14-compat-suite.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.2 -- Transport-agnostic service layer](stories/STORY-070.2-service-layer.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.3 -- Memory CRUD on HttpAdapter](stories/STORY-070.3-memory-crud-http.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.4 -- Error taxonomy + retry-ability semantics](stories/STORY-070.4-error-taxonomy.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.5 -- Idempotency keys for writes](stories/STORY-070.5-idempotency-keys.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.6 -- Bulk operations](stories/STORY-070.6-bulk-operations.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.7 -- Per-call identity (agent_id / scope / group)](stories/STORY-070.7-per-call-identity.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.8 -- Per-tenant auth tokens](stories/STORY-070.8-per-tenant-auth.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Story 70.9 -- Operator-tool separation](stories/STORY-070.9-operator-tools-split.md) — <!-- docsmcp:start:user-story --> *(updated 2026-04-14)*
- [Fix Plan — EPIC-070 AgentForge Integration (Remote-First Brain as a Shared Service)](../fix_plan.md) — All work is tracked in [EPIC-070](docs/epics/EPIC-070-agentforge-integration.md). Stories reference files in `docs/st... *(updated 2026-04-15)*
## Release

- [Changelog](../CHANGELOG.md) — All notable changes to this project will be documented in this file. *(updated 2026-04-28)*
- [Security Policy](../SECURITY.md) — | Version | Supported | *(updated 2026-04-05)*
- [Upgrading the tapps-brain OpenClaw Plugin](../openclaw-plugin/UPGRADING.md) — - Bumps plugin `package.json`, `openclaw.plugin.json`, `ContextEngineInfo` / client identity strings, and manifests t... *(updated 2026-04-10)*
- [Release Checklist — tapps-brain](../scripts/publish-checklist.md) — Distribution channel for `tapps-brain` (TAP-992).  Default path is automated: *(updated 2026-04-27)*
