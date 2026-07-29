# Recent Tradier coherent-bundle campaign — 2026-07-29

## Frozen actual-trade universe

The authoritative broker audit covers the 30 UTC calendar dates
`[2026-06-30T00:00:00Z, 2026-07-30T00:00:00Z)`. Tradier gain/loss closed lots
and current positions establish side by signed quantity: positive is LONG,
negative is SHORT. Options and hedge path families are excluded. Account
history is a symbol-level execution cross-check, not a source for guessing side.

The result is **65 symbol-side keys across 54 equities: 33 LONG and 32 SHORT**.

- LONG: A, ABT, BG, CAT, CF, CLX, COPX, CRWV, EXEL, FIVN, GOOGL, INTC, LLY,
  LRCX, LSCC, MPC, MRVL, MU, NEM, NVDA, OLED, PBF, ROBO, ROKU, RRC, SNDK, STZ,
  TRGP, TSM, UAN, USAR, VLO, VT.
- SHORT: ADBE, AGCO, AGI, ALB, ASTS, CHRD, CLX, CRWV, EGO, GDX, GM, GOOGL,
  IBIT, LDOS, MOS, MU, NEM, NUE, NVDA, OLED, PLTR, RBLX, RGLD, RIO, RRC, SCCO,
  SNDK, STZ, USAR, UUUU, WDAY, XLE.

HAO, TTD, ACN, and ACH were not actually traded in this window. HAO remains
data-quarantined and is excluded. `TTD_SHORT` and `ACN_SHORT` are admitted only
in the separately labeled `USER_PILOT_EXCEPTION_NOT_RECENT` bucket. `ACH` is
unresolved/absent and is never invented.

## Scheduler contract

`tools/recent_tradier_bundle_scheduler.py` screens coherent entry + exit +
reentry configurations instead of changing one field and invalidating every
dependent result:

1. A deterministic block contains nine priority claims followed by one
   exploration claim when both pools have work.
2. Existing path-fleet evidence seeds priority. A handle with three consecutive
   non-positive/inert attempts is demoted to the 10% exploration lane.
3. Every NPZ is frozen into 35% discovery-1, 35% discovery-2, and 30% untouched
   validation. Discovery failure stops the handle early.
4. The same fold is also run with the vector control. A bundle must have at
   least three trades, beat side-aware B&H-or-cash, beat that control, keep
   binary TIM in the user-updated **65–80%** band, and keep drawdown at or below
   40% on every fold.
   Return is reconstructed from the fill events on pre-cost committed
   fill-notional integrated over the complete fold. The denominator is never
   marked notional. `v8_vec_sweep.acc_gain_pct` is retained only as a diagnostic
   because it is a sum of per-trade percentages and is not comparable to B&H.
5. Per-handle wall-clock budgets prevent one symbol consuming the campaign.
   Data quarantines are terminal until their input changes; vector rejects may
   retry at most three times and then consume only exploration capacity.
6. SQLite leases, immutable JSON receipts, NPZ/fold hashes, and stale-lease
   recovery make the campaign resumable.
7. Only every-fold strict survivors become `EXACT_PENDING`. The scheduler never
   invokes exact replay, edits a live config, or fills a canonical ENGINE cell.

The finite canary is deliberately `nice -n 19` and is not installed as a
watchdog or cron. The existing matrix fleet manifest remains the only
authoritative exact-worker manifest.

## Retired ladder-grind claim

Bible §15.40's “24/7 grind is live” sentence was historical and is superseded
by `MATRIX_ONLY_MANDATE_20260728.md`. On S1 there is no current grind process or
watchdog cron. The last receipt was:

```text
2026-07-28T20:19:56Z total=461 due=78 ok=0 failed=56
skipped_no_npz=22 queued=0 status=pass_complete
```

The terminal failures were principally missing NPZ, missing required
`lrL_pct_b_4h`, `lrL_slope_4h`, `lrL_pct_b_D`, `lrL_slope_D`, thin 1h/4h/D
coverage, and split/corrupt data such as HAO/CRWD. This is a shutdown audit, not
permission to restart the discarded standalone scheduler.

## Promotion and rollback

Research receipts live under `data/reports/recent_tradier_90_10/`. Bundle rows
are copied into the separate path-fleet research ledger for the digest, colored
gray unless strict, and never used to color exact matrix cells. Rollback is
therefore deletion/archival of this campaign directory and its `BUNDLE_*`
research rows; live trading configuration is unaffected.
