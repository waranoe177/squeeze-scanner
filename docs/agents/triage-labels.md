# Triage labels

This tracker has no `needs-triage` style labels. Linear **statuses** carry those
roles instead, so `/triage` should move issues between statuses rather than apply
labels. This file is the mapping.

| Canonical role | In this tracker |
|---|---|
| `needs-triage` | Status **Triage** |
| `needs-info` | Status **Triage**, plus a comment naming exactly what is missing. If only Wara can answer it, it is an owed answer: it goes in the Vara OS Decision Log and gets asked in the brief, never chased here |
| `ready-for-agent` | Status **Ready**. **Only Wara moves an issue to Ready.** An agent must never set this itself |
| `ready-for-human` | Status **Todo** |
| `wontfix` | Status **Canceled**, with the reason written on the issue |

Two rules that override the defaults:

- **Never create labels or statuses to match this table.** The mapping exists so that
  the vocabulary already in Linear is used as-is.
- **Never promote an issue to Ready.** That is Wara's approval, and it is the one gate
  between a plan and an agent writing code.
