# Open Issues Roadmap — retired

**Status:** retired 2026-04-21. This file is no longer the canonical delivery queue.
**Canonical queue:** the [tapps-brain project in Linear](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db).
Owner: @wtthornton

## Why this was retired

This roadmap drifted faster than we reconciled it. Epics and stories own their own `status:` frontmatter in [`epics/`](epics/); mirroring that into a separate markdown queue produced a shadow copy that was stale within days of every refresh. Linear now tracks status; [`epics/`](epics/) still owns the *design* (acceptance criteria, story breakdowns, ADR links).

## How to use the queue now

- **What's next / priority?** → [Linear: `tapps-brain` project](https://linear.app/tappscodingagents/project/tapps-brain-e5604347c7db) (filter by priority, status, assignee).
- **What does an epic mean / what are its acceptance criteria?** → the epic file under [`epics/`](epics/). Each Linear parent issue links back to its epic spec.
- **Ralph loop?** → [`.ralph/fix_plan.md`](../../.ralph/fix_plan.md) stays as Ralph's loop driver. It's independent from Linear and from this file (and was never the product-delivery queue — see [`PLANNING.md` § *Open issues roadmap vs Ralph tooling*](PLANNING.md#open-issues-roadmap-vs-ralph-tooling)).

## Issue seeding (2026-04-21)

Bootstrapped Linear from the in-progress + planned epic files:

| Linear parent | Epic | Priority | Children |
|---|---|---|---|
| [TAP-801](https://linear.app/tappscodingagents/issue/TAP-801) | EPIC-060 Agent-First Core — remaining stories | Urgent | TAP-808, TAP-809 |
| [TAP-802](https://linear.app/tappscodingagents/issue/TAP-802) | EPIC-061 Observability-First — operator runbook | Urgent | TAP-810 |
| [TAP-803](https://linear.app/tappscodingagents/issue/TAP-803) | EPIC-062 MCP-Primary Integration — close-out audit | High | — |
| [TAP-804](https://linear.app/tappscodingagents/issue/TAP-804) | EPIC-065 Live dashboard `/snapshot` + panels | High | TAP-811 → TAP-815 |
| [TAP-805](https://linear.app/tappscodingagents/issue/TAP-805) | EPIC-071 TappsBrainClient SDK hardening | High | TAP-816 → TAP-821 |
| [TAP-806](https://linear.app/tappscodingagents/issue/TAP-806) | EPIC-072 Async-native Postgres core | Medium | TAP-822 → TAP-827 |
| [TAP-807](https://linear.app/tappscodingagents/issue/TAP-807) | EPIC-032 OTel GenAI semantic conventions | Low | — |

Epics that were *done* at seeding time (not filed): EPIC-059, EPIC-063, EPIC-064, EPIC-066, EPIC-067, EPIC-068, EPIC-069, EPIC-070. See each epic file's frontmatter for completion dates.

## Historical change log

The pre-retirement history of this file (weekly updates from 2026-03-27 through 2026-04-20, shipped issues #12–#72, EPIC-040 through EPIC-058 tracking rows) is preserved in git — run `git log --follow docs/planning/open-issues-roadmap.md` to walk it.
