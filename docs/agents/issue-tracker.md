# Issue tracker

This repo's tracker is **Linear**, not GitHub Issues. `/to-spec`, `/to-tickets`,
`/triage` and `/implement` read this file to decide where work goes; the Vara OS
Product bot follows the same rules, so hand-driven and bot-driven work produce one
board rather than two.

**GitHub Issues are not used here.** Never run `gh issue create`. Never write issues
into `.scratch/`.

## Where issues live

- **Team:** Waranoe, key `VEN`.
- **Project:** `Sqzdots` (one Linear project per venture).
- **Repo:** `waranoe177/squeeze-scanner`.
- **How:** through the Linear connector (`save_issue`, `list_issues`, `save_comment`).
  If the connector is unavailable, stop and say so rather than falling back to another
  tracker.

## Statuses

`Triage` -> `Backlog` -> `Plan Review` -> `Ready` -> `In Progress` -> `In Review` ->
`Done`, plus `Canceled`, `Duplicate` and `Todo`.

| Status | Means |
|---|---|
| **Triage** | Anything newly arrived, from any source. Unsized, unjudged |
| **Backlog** | Worth doing, not for the current checkpoint. Carries a one-line reason |
| **Plan Review** | Size M or L, plan written, waiting on Wara's approval |
| **Ready** | Approved and agent-ready. **Only Wara moves an issue here** |
| **Todo** | A human has to do this one. Not for an agent |
| **In Progress / In Review** | An agent is on it / a PR is open |
| **Done** | Closed by Linear's GitHub automation when the PR merges |
| **Canceled** | Decided against, with the reason on the issue |

## Labels

| Group | Values |
|---|---|
| Source | `src/you` `src/product` `src/builder` `src/engineer` `src/review` `src/customer` |
| Size (group `size`, single-select) | `S` `M` `L` |
| Kind | `Bug` `Improvement` `Feature` |

Every issue carries a source label. Size drives the plan reviews: **S** needs none,
**M** needs engineering plus design if it has a UI, **L** needs engineering, design
and developer experience.

## Issue format

```
## What to build
The end-to-end behaviour this ticket makes work, from the user's
perspective. Never layer-by-layer implementation.

## Acceptance criteria
- [ ] Criterion 1
- [ ] Criterion 2

## Blocked by
- VEN-12, or "None (can start immediately)".
```

For size M or L, the reviewed plan goes in the issue **description**, ending with a
`## Approved plan` section left unticked until Wara approves it. An agent refuses any
M or L issue without one.

## What makes a ticket valid

1. **Tracer bullets only.** A thin vertical slice through every layer, demoable the
   moment it lands. Never one layer at a time.
2. **Every acceptance criterion must fail at the starting commit.** If it already
   passes, it is a description of today, not a ticket. Rewrite it.
3. At least one criterion per ticket.
4. Small enough for one agent session and one pull request.
5. The first ticket of a set is the walking skeleton.
6. Blocking edges are explicit, by issue ID.

## Who owns which field

| Field | Owner |
|---|---|
| Title, body, acceptance criteria, blocked-by | `vara-os/work/sqzdots/tickets.yml` |
| Status, assignee, comments | Linear |
| Criteria ticked | the agent, and only where its evidence shows a pass |
| Issue closed | Linear's GitHub automation, on merge |
| Milestones | Linear project milestones; `docs/ROADMAP.md` links to them |

**Whoever slices a feature owns its tickets.** Issues Wara sliced by hand are imported
into `tickets.yml` with a key and otherwise left alone.

## Closing work

- The branch name carries the issue ID; the PR body says `Closes VEN-12`.
- The PR carries an **Evidence** table: for each acceptance criterion, a before
  showing it failing on `main` and an after showing it passing on the preview.
- Wara merges. Linear moves the issue to Done by itself.

## PRs as a request surface

**Off.** Pull requests opened here are work in flight, not incoming requests, and do
not enter the triage queue.
