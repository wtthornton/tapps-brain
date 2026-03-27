# Issue triage — saved searches and board setup

Last updated: 2026-03-27

Use these **bookmarkable** GitHub issue filters for the `triage:*` labels. They work in the browser and in `gh` (see CLI section).

Repository: [wtthornton/tapps-brain](https://github.com/wtthornton/tapps-brain)

## One-click saved searches (by decision)

| Decision | Label | Open issues |
|----------|--------|-------------|
| Approved | `triage:approved` | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Aapproved%22) |
| Approved with rescope | `triage:rescope` | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Arescope%22) |
| Defer / blocked | `triage:defer` | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Adefer%22) |
| Verify then close | `triage:close-candidate` | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Aclose-candidate%22) |

## Combined views

| View | Description | Link |
|------|-------------|------|
| **Implementation queue** | Approved only (default build queue) | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Aapproved%22) |
| **Needs scope work first** | Rescope items | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Arescope%22) |
| **Not now** | Defer / dependency | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Adefer%22) |
| **Any triage label** | Open issues with any `triage:*` label | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+label%3A%22triage%3Aapproved%22+OR+label%3A%22triage%3Arescope%22+OR+label%3A%22triage%3Adefer%22+OR+label%3A%22triage%3Aclose-candidate%22) |
| **Build candidates** | Approved or rescope (planning + build) | [View](https://github.com/wtthornton/tapps-brain/issues?q=is%3Aopen+is%3Aissue+%28label%3A%22triage%3Aapproved%22+OR+label%3A%22triage%3Arescope%22%29) |

## CLI (`gh`)

```bash
# Approved
gh issue list --label "triage:approved" --state open

# Rescope
gh issue list --label "triage:rescope" --state open

# Defer
gh issue list --label "triage:defer" --state open

# Close candidate
gh issue list --label "triage:close-candidate" --state open
```

## GitHub Projects board (optional)

GitHub does not store “saved searches” in the repo; use **Projects** for a persistent board.

1. Open [Projects](https://github.com/wtthornton/tapps-brain/projects) for the repo (or org) and create a project.
2. Add the repository `wtthornton/tapps-brain` as the source.
3. Use **board** or **table** layout.
4. Add **views** filtered by label:
   - View “Approved”: filter `label:triage:approved`
   - View “Rescope”: filter `label:triage:rescope`
   - View “Defer”: filter `label:triage:defer`
   - View “Close candidate”: filter `label:triage:close-candidate`
5. Optional: add a **Status** field (Todo / In progress / Done) for execution tracking separate from triage decision.

## Related

- Intake criteria: [`FEATURE_FEASIBILITY_CRITERIA.md`](./FEATURE_FEASIBILITY_CRITERIA.md)
- Roadmap: [`open-issues-roadmap.md`](./open-issues-roadmap.md)
