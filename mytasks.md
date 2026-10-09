# My tasks — Sqzdots

## Multi-timeframe table (feat/mtf-table)

- [ ] Say "merge and push" when ready. The daily GitHub Actions run picks it up from `main`.
      Nothing is pushed until you say so.
- [ ] Parity check, the first evening after it goes live: compare the Telegram table cell by
      cell with the TOS watchlist (`Sqzdots_Col_V5_RevGate_compact.ts` at WK / 3D / 2D / 1D).
      All four columns must match. Report any mismatch with the ticker and timeframe.
- Note: futures in the table are Yahoo continuous contracts (`/ES` = `ES=F`). TOS shows the front
  month (`/ES[Z26]`). Small differences near a roll date are expected and are not a parity failure.
