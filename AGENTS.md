# AGENTS.md

Agent instructions for the `waranoe177/squeeze-scanner` repo. This repo is part of **Vara OS**: its
issues live in Linear, and the machinery that plans and files them lives in the
`vara-os` repo.

## Agent skills

### Issue tracker

Issues live in Linear (team Waranoe `VEN`, project `Sqzdots`), not GitHub Issues.
See `docs/agents/issue-tracker.md`.

### Triage labels

Linear statuses carry the canonical triage roles; there are no triage labels.
See `docs/agents/triage-labels.md`.

### Domain docs

Single-context. See `docs/agents/domain.md`.

## Standing rules

- **Never merge.** Open a pull request and let Wara merge it.
- **Never deploy to production, spend money, or write to anyone else's system.**
- **Only Wara moves an issue to Ready.**
- **Two failed attempts, then stop** and hand the issue back with what you tried.
- Anything you find on the way that is not this ticket goes to **Triage**, never
  into the same pull request.

The full build workflow (isolate, build, prove, ship, with before-and-after evidence
in every PR) arrives with the Vara OS week 3 setup pass and will be added here then.
