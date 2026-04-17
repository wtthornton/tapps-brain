# Linear automation via a dedicated Claude Agent user

**Status:** PLANNED — key not yet generated, poller not yet built. This
guide describes the intended setup so it's captured before we execute.
Flip sections from "planned" to "current" as each step lands.

**Audience:** a human operator wiring a Linear workspace so Claude-driven
automation (commenting, status updates, triage responses) appears under a
dedicated bot identity instead of the operator's own account.

## Goal

Let a human comment `@Claude Agent` on a Linear issue and have a
scheduled process pick it up, reply with the requested
research/update/status change, and never double-reply.

Non-goal: the bot does **not** implement the underlying engineering
work. It only comments, updates fields, and triages. Implementation work
still runs through the normal Claude Code / Ralph paths on the operator's
workstation.

## Linear user

- **Email:** `tapp.thornton+claude@gmail.com` (Gmail plus-alias —
  delivers to `tapp.thornton@gmail.com`; filterable on `+claude`).
- **Full name:** `Claude Agent` (two words — makes it obvious in
  activity feeds that this is a non-human actor).
- **Username:** `claude` (short `@claude` mentions still work).
- **Role:** Member. Not Admin. The bot should never need workspace-level
  permissions.
- **Avatar:** distinct colour/initials so comments are visually
  separable from human ones.

## Auth strategy — two separate paths

Linear exposes two auth surfaces, and we use them for different purposes.

### Path A — Linear plugin in Claude Code (OAuth)

The `plugin:linear:linear` MCP inside Claude Code uses OAuth, not API
keys. It authenticates **per Claude Code user** against Linear.

- **Authed as:** the human operator (Bill Thornton).
- **Why:** this is the plugin the operator uses interactively during a
  session ("check TAP-560", "list my open issues"). Actions posted
  through it show up attributed to Bill.
- **Do not** re-auth this plugin as Claude Agent. Doing so would mean
  every in-session Linear action — whoever triggered it — shows as the
  bot, which confuses audit and removes the operator's ability to
  comment interactively from Claude Code.

### Path B — Scheduled poller (personal API key)

All automated Claude Agent activity runs through a separate process
authed with a Personal API key generated **while logged in as Claude
Agent**. This decouples the automation from the interactive session.

- **Authed as:** Claude Agent (`tapp.thornton+claude@gmail.com`).
- **Why:** clean attribution in Linear, separate failure domain from the
  interactive plugin, easier to revoke without disturbing the operator.

## Key storage

Personal API keys never get committed, never get pasted into chat
transcripts, and never go into the repo's `.env` (which is shared with
other tooling).

```bash
mkdir -p ~/.config/claude-agent
umask 077
printf 'LINEAR_API_KEY=lin_api_YOUR_KEY_HERE\n' > ~/.config/claude-agent/linear.env
chmod 600 ~/.config/claude-agent/linear.env
```

Path is user-scoped (`~/.config/…`), not repo-scoped — the key belongs
to the operator, not to any one codebase. A single poller can monitor
multiple Linear workspaces/projects from one credential file.

### Sanity check

```bash
source ~/.config/claude-agent/linear.env
curl -s \
  -H "Authorization: $LINEAR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ viewer { id name email } }"}' \
  https://api.linear.app/graphql
```

Expected: `"name":"Claude Agent"`. If it returns the operator's name,
the key was generated on the wrong account — revoke it and redo from
the Claude Agent login.

### Revocation and rotation

- If a key is ever pasted into a chat transcript, terminal scrollback,
  shared screen recording, issue description, or commit, treat it as
  compromised. Revoke in Linear → Settings → API → Personal API keys.
- Rotate on a schedule appropriate to blast radius (workspace size,
  external exposure). For a solo workspace, annual rotation is fine.

## Planned poller architecture

Not built yet — this is the design we're committing to before
implementation.

### Trigger

Scheduled agent (via the `/schedule` skill or a cron job on the
operator's host), firing every 15 minutes. Dynamic self-pacing
(`/loop` without an interval) is not appropriate — we want the same
cadence regardless of whether a session is active.

### Scope of work per run

1. Fetch issues whose `updatedAt` is after the last-run watermark
   (typically <20 issues per 15-min window).
2. For each, pull comments created after the watermark and filter for
   `@Claude Agent` mentions.
3. For each qualifying comment, run the dedup checks (below); if they
   pass, call the configured handler (research, status update, etc.)
   and post a reply as Claude Agent.
4. Advance the watermark **only after** all replies for that window
   have succeeded or been explicitly skipped — partial-failure safe.

### De-duplication — three layers

No single layer is sufficient. Use all three.

1. **Timestamp watermark** stored in tapps-brain memory under key
   `linear.poller.last_seen` (tier `procedural`, TTL
   profile-default). Primary filter — cheap, narrows the query to
   recent comments only.
2. **Reply-in-thread check.** Before replying, `list_comments` on the
   issue and look for any comment *after* the `@Claude Agent` mention
   authored by the Claude Agent user ID. If present, skip. This is the
   safety net if the watermark is lost or rewound.
3. **Hidden marker in Claude Agent replies.** Each reply starts with
   `<!-- claude-reply:<mention-comment-id> -->`. Before replying, the
   poller greps the thread for `claude-reply:<id>`; if found, skip.
   Cheapest possible idempotency — survives watermark loss and
   reply-check false negatives.

### Trigger convention

- **Reply to a mention:** comment `@Claude Agent <instructions>` on
  any issue.
- **Force re-process:** new comment with `@Claude Agent please retry`
  — new comment ID passes all three dedup checks.
- **Non-comment triggers:** not supported initially. Assignment to
  Claude Agent does not on its own start work — require an explicit
  `@Claude Agent` comment on the issue. Ambiguity in human-written
  issue bodies is not worth the risk of spam-replying.

### Failure modes and handling

| Scenario | Handling |
|---|---|
| Linear API 5xx / timeout | Do not advance watermark; next run retries. |
| Handler raises | Reply with an error comment, advance watermark (don't retry the same comment forever), log for operator. |
| Watermark in tapps-brain lost | Layers 2 and 3 prevent double-replies; poller catches up gradually. |
| Operator edits the original `@Claude Agent` comment | Reply-in-thread check still catches the prior reply — no double-reply. |
| Operator revokes the API key | Poller fails at auth, no writes occur; operator fixes the env file and restarts. |

## Separation from interactive use

To keep the two paths from tangling:

- **In-session** work through the Claude Code Linear plugin → attributed
  to the operator. Good for ad-hoc queries, manual triage.
- **Scheduled / automated** work through the poller → attributed to
  Claude Agent. Good for unattended response to mentions.

If you want an in-session Claude action attributed to Claude Agent
instead of the operator, invoke the poller's handler directly rather
than using the plugin — keeps the attribution model simple.

## Open questions (resolve before implementation)

- **Handler dispatch.** How does the poller know *what* to do with a
  given `@Claude Agent` comment? Options: (a) convention in the comment
  body ("research: …", "status: done"), (b) a label on the issue
  driving behaviour, (c) a single handler that reads the whole comment
  and decides. Leaning (a) — explicit is easier to audit.
- **Rate limiting.** Linear's API has per-user limits. Unlikely to
  matter at 15-min cadence on a small workspace, but the poller should
  back off on 429.
- **Multi-workspace.** Out of scope for v1. One key, one workspace.
- **Observability.** Where do poller logs live? Proposal: append to
  `~/.config/claude-agent/logs/linear-poller.log` with rotation;
  surface errors via whatever the operator's existing notification
  channel is.

## Implementation checkpoints

Flip these to `[x]` as we land each piece.

- [ ] Claude Agent Linear user created and invited (`tapp.thornton+claude@gmail.com`).
- [ ] Personal API key generated as Claude Agent, stored at
  `~/.config/claude-agent/linear.env` (chmod 600).
- [ ] GraphQL `viewer` sanity check returns `Claude Agent`.
- [ ] Poller script written (language / location TBD during impl).
- [ ] Watermark read/write wired to tapps-brain memory.
- [ ] All three dedup layers implemented and unit-tested.
- [ ] Scheduled via `/schedule` or host cron, verified it fires.
- [ ] End-to-end test: human comments `@Claude Agent hello`, poller
  replies within one cycle, does not reply again on the next cycle.
- [ ] Failure-mode tests: key revoked, Linear down, watermark lost.

Once all checkpoints are complete, re-title this guide "Linear
automation via a dedicated Claude Agent user" (strip the PLANNED
banner) and move the "Open questions" section to a short "Decisions"
appendix.
