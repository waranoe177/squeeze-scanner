# Domain docs

**Layout: single-context.** One repo, one domain, no per-package contexts.

| Thing | Where |
|---|---|
| Orientation | `README.md` at the repo root, plus `WISHLIST.md` for what is wanted but unbuilt. |
| Architectural decisions | `docs/adr/` does not exist yet. The first architectural decision written up
  creates it, starting at `0001`. |

## Consumer rules

This repo's decisions live in its docs and commit history rather than ADRs. When a
decision is big enough to outlive a session, write it as `docs/adr/0001-...md` and
start the sequence.

Read the orientation document before any design work, and the ADRs whose subject
your change touches. Do not re-derive a decision that is already recorded; if you
think a recorded decision is wrong, say so and propose superseding it, rather than
quietly doing something else.

**Research honesty rules apply.** Write rules down before testing them, test on a
window that was not tuned on, and tag new signals separately from old ones. The edge
verdict of Sep 2026 stands: the mechanical signal has no demonstrated edge, and the
product is a watchlist and educational tool, not a performance claim.
