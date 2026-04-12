# tapps-brain — Documentation Index

**121 documents** across **7 categories**

## Overview

| Category | Count |
|---|---|
| API Reference | 1 |
| Architecture | 12 |
| Getting Started | 6 |
| Guides | 27 |
| Operations | 1 |
| Other | 3 |
| Planning | 71 |

## API Reference

- [Data Stores and Schema Reference](engineering/data-stores-and-schema.md) — | Store | Backend | Location | *(updated 2026-04-09)*

## Architecture

- [Industry features and technologies (implementation map)](engineering/features-and-technologies.md) — **Audience:** Architecture and product review — what capability areas we cover, which libraries/patterns we use, and ... *(updated 2026-04-09)*
- [System Architecture (Implementation-Aligned)](engineering/system-architecture.md) — tapps-brain is designed for **many concurrent agents** without shared-DB bottlenecks: *(updated 2026-04-09)*
- [Configurable Memory Profiles — Design Document](planning/DESIGN-CONFIGURABLE-MEMORY-PROFILES.md) — | Framework | Memory Types / Layers | Storage | Scoring | Key Innovation | *(updated 2026-04-10)*
- [ADR-001: Retrieval stack — embedded SQLite-first (defer learned sparse, ColBERT, managed vector DB)](planning/adr/ADR-001-retrieval-stack.md) — **Status:** Accepted *(updated 2026-04-08)*
- [ADR-002: Freshness — lazy decay + operator GC (defer wall-clock TTL jobs)](planning/adr/ADR-002-freshness-lazy-decay-vs-ttl.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-003: Correctness — heuristic conflicts + offline review (defer ontology and in-product review queue)](planning/adr/ADR-003-correctness-heuristics-vs-ontology-review-queue.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-004: Scale — single-node SQLite posture (defer published QPS SLO and service extraction)](planning/adr/ADR-004-scale-single-node-sqlite-defer-service-extraction.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-005: SQLCipher operations — passphrase runbook + backup verification (defer KMS product integration)](planning/adr/ADR-005-sqlcipher-key-backup-operations.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-006: Save-path observability — phase histograms + health summary (defer deeper metrics unless trigger (a))](planning/adr/ADR-006-save-path-observability.md) — **Status:** Accepted *(updated 2026-04-05)*
- [ADR-007: Postgres-only backends — no SQLite for Hive or Federation (v3 greenfield)](planning/adr/ADR-007-postgres-only-no-sqlite.md) — **Status:** Accepted *(updated 2026-04-10)*
- [ADR-008: No new public HTTP routes without MCP + library parity](planning/adr/ADR-008-no-http-without-mcp-library-parity.md) — **Status:** Accepted *(updated 2026-04-10)*
- [ADR-009: Row Level Security on hive_memories — Ship in GA vs Defer](planning/adr/ADR-009-rls-ship-vs-defer.md) — **Status:** Accepted *(updated 2026-04-11)*
- [Design note: multi-scope memory (#49)](planning/design-issue-49-multi-scope-memory.md) — Epic **#49** (multi-group memory scopes: Hive, named groups, personal) needs a clear *(updated 2026-04-05)*

## Getting Started

- [Ralph Setup Guide (Windows + WSL)](RALPH_SETUP_GUIDE.md) — Step-by-step guide for setting up Ralph on a new project. Covers the common pitfalls. *(updated 2026-04-05)*
- [GitHub Setup Guide](GITHUB_SETUP_GUIDE.md) — GitHub configurations that cannot be set via repository files — required API or UI setup steps for new contributors. *(updated 2026-04-05)*
- [Engineering Documentation Baseline](engineering/README.md) — This folder is the code-aligned engineering reference for tapps-brain runtime behavior. *(updated 2026-04-05)*
- [Optional Features and Runtime Toggle Matrix](engineering/optional-features-matrix.md) — This matrix documents behavior changes from extras, feature checks, and profile-driven toggles. *(updated 2026-04-09)*
- [Getting Started with tapps-brain](guides/getting-started.md) — tapps-brain ships three first-class interfaces to the same memory engine. Choose the one that fits your workflow. *(updated 2026-04-08)*
- [Install and upgrade tapps-brain for OpenClaw from GitHub (no PyPI)](guides/openclaw-install-from-git.md) — Use this guide when you want the **Python** package and **`tapps-brain-mcp`** installed or upgraded from the Git repo... *(updated 2026-04-05)*

## Guides

- [Agent integration guide (MCP, OpenClaw, custom clients)](guides/agent-integration.md) — This page is the **operator contract** for AI agents using tapps-brain: how to write memory, how recall behaves when ... *(updated 2026-04-08)*
- [AgentForge Integration Guide](guides/agentforge-integration.md) — How any project connects to the running AgentForge stack — invoke agents, stream tasks, share Hive memory, and add pr... *(updated 2026-04-09)*
- [Auto-Recall: Pre-Prompt Memory Injection](guides/auto-recall.md) — Auto-recall automatically searches the memory store for relevant context before an agent processes a user message, an... *(updated 2026-04-05)*
- [ClawHub Submission Guide](guides/clawhub-submission.md) — How to submit `tapps-brain-memory` to the ClawHub skill directory. *(updated 2026-04-05)*
- [Pluggable lookup engine for doc validation](guides/doc-validation-lookup-engine.md) — `tapps-brain` validates memory entries against authoritative documentation using a *(updated 2026-04-09)*
- [Embedding model card (default semantic search)](guides/embedding-model-card.md) — This page documents the **default** dense embedding stack for built-in vector / hybrid retrieval (**EPIC-042** STORY-... *(updated 2026-04-08)*
- [Federation Guide](guides/federation.md) — Cross-project memory sharing via a central hub store. *(updated 2026-04-05)*
- [Hive Deployment Guide](guides/hive-deployment.md) — This guide covers deploying the tapps-brain Hive (shared Postgres brain) in *(updated 2026-04-08)*
- [Hive Operations Guide](guides/hive-operations.md) — Day-to-day operational procedures for the tapps-brain Hive Postgres backend. *(updated 2026-04-08)*
- [Hive vs federation — when to use which](guides/hive-vs-federation.md) — Both features move memories across boundaries, but the **boundary** and **mechanics** differ. Use this page first, th... *(updated 2026-04-05)*
- [Hive Guide: Cross-Agent Memory Sharing](guides/hive.md) — The Hive is tapps-brain's multi-agent shared brain. It enables agents on the same machine to share knowledge through ... *(updated 2026-04-10)*
- [LLM Brain Guide](guides/llm-brain-guide.md) — Instructions for LLMs and AI agents using the tapps-brain simplified MCP tools. *(updated 2026-04-08)*
- [MCP Server: Using tapps-brain with AI Assistants](guides/mcp.md) — tapps-brain exposes its full API via the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP), making per... *(updated 2026-04-05)*
- [Memory decay and FSRS-style stability](guides/memory-decay-and-fsrs.md) — This document is the **product decision** for **EPIC-042** STORY-042.8: how tapps-brain combines time-based decay wit... *(updated 2026-04-05)*
- [Memory relay (sub-agent → primary)](guides/memory-relay.md) — Cross-node setups: only the primary host runs tapps-brain. Sub-agents build a **relay** JSON envelope so the primary ... *(updated 2026-04-05)*
- [Memory scopes: project group vs Hive vs profile (GitHub #49)](guides/memory-scopes.md) — Three concepts are easy to confuse. They are **separate** in tapps-brain. *(updated 2026-04-05)*
- [Observability](guides/observability.md) — tapps-brain exposes structured **metrics**, **health**, **audit**, **diagnostics**, and **feedback** surfaces through... *(updated 2026-04-05)*
- [OpenClaw Install and Upgrade Runbook (Canonical)](guides/openclaw-runbook.md) — This is the source-of-truth runbook for installing and upgrading tapps-brain in OpenClaw. *(updated 2026-04-05)*
- [tapps-brain for OpenClaw](guides/openclaw.md) — Persistent cross-session memory for your OpenClaw agents. MCP tool and resource *(updated 2026-04-05)*
- [Profile Catalog](guides/profile-catalog.md) — tapps-brain ships with 6 built-in profiles covering common AI agent use cases. Each profile can be used directly, ext... *(updated 2026-04-06)*
- [Profile Limits: Research and Rationale](guides/profile-limits-rationale.md) — This document explains the evidence behind tapps-brain's built-in profile defaults. *(updated 2026-04-05)*
- [Memory Profiles: Designing Custom Memory for Any AI Agent](guides/profiles.md) — tapps-brain ships with a configurable profile system that lets you define custom memory layers, decay models, scoring... *(updated 2026-04-10)*
- [Save conflicts: offline review and NLI backlog](guides/save-conflict-nli-offline.md) — Save-time conflict detection uses deterministic text similarity (`detect_save_conflicts` in `contradictions.py`) when... *(updated 2026-04-05)*
- [Visual snapshot (`brain-visual.json`)](guides/visual-snapshot.md) — Export a **versioned JSON snapshot** of store health, tier mix, and related signals for static dashboards and the bra... *(updated 2026-04-09)*
- [Brain-visual dashboard](../examples/brain-visual/README.md) — Load a real or demo snapshot in the static dashboard; motion test checklist; brand and NLT Labs design language notes. *(updated 2026-04-11)*

## Operations

- [Deploying tapps-brain to OpenClaw](planning/DEPLOY-OPENCLAW.md) — There are **two complementary deployment paths** for getting tapps-brain into OpenClaw: *(updated 2026-04-05)*
- [Epic Validation — Regression Runbook](operations/epic-validation-regression.md) — Step-by-step regression checklist for validating completed epics against acceptance criteria. *(updated 2026-04-11)*

## Other

- [Core Call Flows](engineering/call-flows.md) — This document maps the dominant runtime call paths as implemented now. *(updated 2026-04-05)*
- [Code Inventory and Documentation Gaps](engineering/code-inventory-and-doc-gaps.md) — - **Core orchestration** *(updated 2026-04-05)*
- [Ralph Bug: Live Mode JSONL Crash in Response Analyzer](ralph-jsonl-crash-bug.md) — **Severity:** Critical (silent loop termination, no error logged) *(updated 2026-04-05)*

## Planning

- [Agent Feature Governance](planning/AGENT_FEATURE_GOVERNANCE.md) — Last updated: 2026-03-27 *(updated 2026-04-05)*
- [Feature Feasibility Criteria (Agent Standard)](planning/FEATURE_FEASIBILITY_CRITERIA.md) — Last updated: 2026-03-27 (web-calibrated pass) *(updated 2026-04-05)*
- [Issue triage — saved searches and board setup](planning/ISSUE_TRIAGE_VIEWS.md) — Last updated: 2026-03-27 *(updated 2026-04-05)*
- [Planning Conventions](planning/PLANNING.md) — This document defines how epics, stories, and tasks are structured in this project so that both humans and AI coding ... *(updated 2026-04-05)*
- [Project status snapshot](planning/STATUS.md) — **Last updated:** 2026-04-09 (America/Chicago) — **v3.2.0** — EPIC-048 complete (all 6 stories done); default embeddi... *(updated 2026-04-09)*
- [Implementation plan: dynamic tapps-brain visual identity](planning/brain-visual-implementation-plan.md) — Track progress here for a **modern, per-instance-unique** visual representation of tapps-brain (dashboard, marketing,... *(updated 2026-04-05)*
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
- [EPIC-009: Multi-Interface Distribution — Library, CLI, and MCP Packaging](planning/epics/EPIC-009.md) — tapps-brain is becoming a three-interface project: a Python library (`import tapps_brain`), a CLI (`tapps-brain`), an... *(updated 2026-04-05)*
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
- [EPIC-032: OTel GenAI Semantic Conventions — Standardized Telemetry Export](planning/epics/EPIC-032.md) — EPIC-007 added an optional OpenTelemetry exporter (`otel_exporter.py`) that converts tapps-brain's internal metrics t... *(updated 2026-04-05)*
- [EPIC-033: OpenClaw Plugin SDK Alignment — Fix API Type Drift and Runtime Bugs](planning/epics/EPIC-033.md) — The OpenClaw plugin (`openclaw-plugin/src/index.ts`) defines a custom `OpenClawPluginApi` interface (lines 184-202) i... *(updated 2026-04-05)*
- [EPIC-034: Production Readiness QA Remediation - Lint, Format, Typing, Test Stability](planning/epics/EPIC-034.md) — The production-readiness review found hard blockers: failing Ruff checks, formatting drift, and unstable plugin test ... *(updated 2026-04-05)*
- [EPIC-035: OpenClaw Install and Upgrade UX Consistency](planning/epics/EPIC-035.md) — The readiness review found documentation and command inconsistencies that can cause failed installs, failed upgrades,... *(updated 2026-04-05)*
- [EPIC-036: Release Gate Hardening for Production-Ready OpenClaw Distribution](planning/epics/EPIC-036.md) — Readiness is currently assessed manually and can regress between releases. To keep production readiness durable, the ... *(updated 2026-04-05)*
- [EPIC-037: OpenClaw Plugin SDK Realignment — Fix API Contract to Match Real SDK](planning/epics/EPIC-037.md) — The OpenClaw plugin ships a hand-written `openclaw-sdk.d.ts` (ambient type declarations) that diverges from the real ... *(updated 2026-04-05)*
- [EPIC-038: OpenClaw Plugin Simplification — Remove Dead Compat Layers and Streamline](planning/epics/EPIC-038.md) — After EPIC-037 aligns the plugin with the real OpenClaw SDK, a significant amount of dead weight remains in the codeb... *(updated 2026-04-05)*
- [EPIC-039: Replace Custom MCP Client with Official @modelcontextprotocol/sdk](planning/epics/EPIC-039.md) — The OpenClaw plugin's `mcp_client.ts` is a hand-rolled JSON-RPC 2.0 client (~466 lines) that implements Content-Lengt... *(updated 2026-04-05)*
- [EPIC-040: tapps-brain v2.0 — Research-Driven Upgrades](planning/epics/EPIC-040.md) — Per-story checkboxes, phases, and GitHub issue mapping (**#24–#44**) live in **`.ralph/fix_plan.md`** under `## EPIC-... *(updated 2026-04-09)*
- [EPIC-041: Federation hub + Hive groups + operator docs](planning/epics/EPIC-041.md) — Post–**#49** (v1 project-local `memory_group`) work queued on GitHub and in `planning/open-issues-roadmap.md`. *(updated 2026-04-05)*
- [Improvement program: `features-and-technologies.md` (index)](planning/epics/EPIC-042-feature-tech-index.md) — **Source map:** [features-and-technologies.md](engineering/features-and-technologies.md) *(updated 2026-04-09)*
- [EPIC-042: Retrieval and ranking (RAG-style memory)](planning/epics/EPIC-042.md) — Maps to **§1** of [`features-and-technologies.md`](engineering/features-and-technologies.md). Core product valu... *(updated 2026-04-09)*
- [EPIC-043: Storage, persistence, and schema](planning/epics/EPIC-043.md) — Maps to **§2** of [`features-and-technologies.md`](engineering/features-and-technologies.md). Ground truth: `pe... *(updated 2026-04-05)*
- [EPIC-044: Ingestion, deduplication, and lifecycle](planning/epics/EPIC-044.md) — Maps to **§3** of [`features-and-technologies.md`](engineering/features-and-technologies.md). *(updated 2026-04-09)*
- [EPIC-045: Multi-tenant, sharing, and sync models](planning/epics/EPIC-045.md) — Maps to **§4** of [`features-and-technologies.md`](engineering/features-and-technologies.md). Overlaps shipped ... *(updated 2026-04-05)*
- [EPIC-046: Agent / tool integration](planning/epics/EPIC-046.md) — Maps to **§5** of [`features-and-technologies.md`](engineering/features-and-technologies.md). *(updated 2026-04-05)*
- [EPIC-047: Quality loop, observability, and ops](planning/epics/EPIC-047.md) — Maps to **§6** of [`features-and-technologies.md`](engineering/features-and-technologies.md). Relates to **EPIC... *(updated 2026-04-05)*
- [EPIC-048: Optional / auxiliary capabilities](planning/epics/EPIC-048.md) — Maps to **§7** of [`features-and-technologies.md`](engineering/features-and-technologies.md). *(updated 2026-04-09)*
- [EPIC-049: Dependency extras (install surface)](planning/epics/EPIC-049.md) — Maps to **§8** of [`features-and-technologies.md`](engineering/features-and-technologies.md) and `pyproject.tom... *(updated 2026-04-07)*
- [EPIC-050: Concurrency and runtime model](planning/epics/EPIC-050.md) — Maps to **§9** of [`features-and-technologies.md`](engineering/features-and-technologies.md) and roadmap backlo... *(updated 2026-04-09)*
- [EPIC-051: Cross-cutting review (§10 checklist)](planning/epics/EPIC-051.md) — Maps to **§10** of [`features-and-technologies.md`](engineering/features-and-technologies.md) — meta items that... *(updated 2026-04-05)*
- [EPIC-052: Full Codebase Code Review — 2026-Q2 Sweep](planning/epics/EPIC-052.md) — The last full code-review sweep completed on 2026-03-23 across **EPIC-017 → EPIC-025**. Since then, substantial code ... *(updated 2026-04-05)*
- [EPIC-053: Per-Agent Brain Identity](planning/epics/EPIC-053.md) — Today `agent_id` defaults to `"unknown"` across all of tapps-brain. The MCP server accepts `--agent-id` but most call... *(updated 2026-04-09)*
- [EPIC-054: Hive Backend Abstraction Layer](planning/epics/EPIC-054.md) — `HiveStore` and `FederatedStore` are tightly coupled to SQLite. Every method directly executes SQL against a local `.... *(updated 2026-04-09)*
- [EPIC-055: PostgreSQL Hive & Federation Backend](planning/epics/EPIC-055.md) — EPIC-054 introduced backend protocols for `HiveBackend` and `FederationBackend` with SQLite adapters. This epic imple... *(updated 2026-04-09)*
- [EPIC-056: Declarative Group Membership & Expert Publishing](planning/epics/EPIC-056.md) — Today Hive groups exist (`create_group()`, `add_group_member()`) but are **imperatively managed** — callers must expl... *(updated 2026-04-09)*
- [EPIC-057: Unified Agent API — Hide the Complexity](planning/epics/EPIC-057.md) — After EPIC-053 through EPIC-056, tapps-brain has per-agent brains, backend abstraction, Postgres backends, declarativ... *(updated 2026-04-09)*
- [EPIC-058: Docker & Deployment Support](planning/epics/EPIC-058.md) — tapps-brain is currently distributed as a Python package with no Docker artifacts. The target architecture requires: *(updated 2026-04-09)*
- [EPIC-065: Live Always-On Dashboard](planning/epics/EPIC-065.md) — Replace static snapshot-file model with live polling dashboard backed by GET /snapshot on HttpAdapter; add Hive and agent-registry panels. *(updated 2026-04-12)*
- [EPIC-066: Postgres-Only Persistence Plane — Production Readiness](planning/epics/EPIC-066.md) — Closes out EPIC-059 stage 2: green CI suite against ephemeral Postgres, operator runbooks for TDE/backup, pool health, auto-migrate, and behavioural parity. *(updated 2026-04-12)*
- [Next session — agent handoff prompt](planning/next-session-prompt.md) — Copy everything below the line into a new chat (or Ralph task) as the **user message**. *(updated 2026-04-09)*
- [Open Issues Roadmap](planning/open-issues-roadmap.md) — Last updated: 2026-04-09 — **v3.2.0** — EPIC-048 complete (all 6 stories); default embedding → `BAAI/bge-small-en-v1.... *(updated 2026-04-09)*
