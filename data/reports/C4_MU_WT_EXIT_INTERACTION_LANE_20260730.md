# MU_LONG WT-force × exit exact-c4 interaction lane

Purpose: reduce MU_LONG from the exact all-exits-off ~99.97% TIM baseline into
its current runtime-ranked 20–60% TIM band without changing the entry schedule,
while retaining more than 2× the side-specific B&H result and beating an
identical-entry HOLD control on every frozen chronological fold.

The runner is `tools/run_exact_mu_wt_exit_interactions.py`. It is research-only:
it writes exact receipts under
`data/reports/exact_mu_wt_exit_interactions_20260730/`, never writes live
configuration, and never synthesizes an OFAT matrix cell.

## Frozen contract

- exact c4 fingerprint:
  `tradier-matrix-exec-c4-20260729:a2519f796e73284d35986b7dee6de0221b861c13ee629e9c0f8857d9764cfd08`
- canonical campaign/NPZ: `stocks_repaired_20260725_c2/MU.npz`; its SHA-256 is
  recorded in the manifest and every reusable receipt
- one side only: `MU_LONG`
- `$10,000` accounting capital, `$2,000` B&H deployment, `$16,000` maximum
  strategy notional (8× the B&H deployment)
- exactly `0.05%` stock round-trip friction; a missing or different trade cost
  fails the leg
- runtime TIM gate: 20–60% because MU_LONG is not currently top 10 in its side
- three preregistered, non-overlapping chronological folds; an aggregate can
  summarize but cannot override a failed fold

## Common entry floor

Every variant is built from the repaired all-exits-off floor and changes only
the declared exit allowlist:

- `WT_3M_FORCE_OPEN_ENABLED=true`
- `WT_3M_FORCE_OPEN_BUILD_TO_TARGET=true`
- `WT_3M_FORCE_OPEN_TARGET_USD=16000`
- `WT_3M_FORCE_OPEN_USE_SMA200=true`

The same override hash, engine fingerprint, NPZ hash, side isolation, real
closes, reentry violations, max notional, requested multiplier, and fill ratio
are retained in the receipts.

The current DB already contains the identical full-window control override
(SHA-256 `0de9b2db815460681eb6a773103d961f30c8faf97a44990b41ddff4b9e89d98d`):
WT-force true returned 31.6721%/month versus 4.7610%/month B&H (6.652×),
TIM 99.9722%; its false entry ablation returned 4.6736%/month. This is bound as
entry-floor provenance and prevents a redundant full-window HOLD run. It does
not replace the same-fold controls required for chronological exit comparison.
The true row declared a `$16k` capacity and an 8× ladder, but its maximum filled
notional was only `$5,199.99`; raw requests reached 25.2995× and were clamped
(requested/fill ratio 0.0105). Those facts are retained rather than describing
the control as fully invested at `$16k`.

## Bounded exit set and dependencies

1. `control`: identical WT-force entry, all exits off.
2. `dc_1h_n5`: compound master + MTF DC reject, 1h, lookback 5.
3. `wt_15m_gr3`: compound master + MTF WT cross at 15m + GR gate requiring
   three opposing timeframes.
4. `srs_bb1h`: exact structural range shift using BB 1h, K 80/20, 100 bps
   proximity.
5. `dc_wt`: safe compound combination of the DC and gated-WT children.
6. `dc_srs`: DC compound child plus the independent SRS evaluator.

There is no `GR-only` result. In `tradier_manage.py`, GR is only a gate inside
the MTF WT child branch. Turning on the gate while WT remains off is a duplicate
of HOLD, not a unique trading path.

The earlier causal vector factorial may rank DC above SRS, but used a different
frozen ladder request schedule. It cannot be relabeled as exact WT-force
evidence. WT/GR is stateful and has no faithful vector shortcut, so only this
small finalist set is exact-run.

## Strict decision

A candidate passes only if every fold:

- exceeds 2× side B&H when side B&H is positive; when side B&H is negative,
  exceeds cash (0%) instead;
- beats its same-fold, same-entry control;
- has 20–60% TIM and at least one real close;
- has zero reentry violations and no synthetic `_FINAL_V8` close;
- respects the filled `$16k` capacity / 8× ladder limit (oversized raw
  requests may be clamped and their fill ratio remains disclosed);
- contains no SHORT P&L row and uses exactly 0.05% stock round-trip cost.

Failures remain gray research evidence. Nothing is promoted automatically.

## Rollback

Delete `data/reports/exact_mu_wt_exit_interactions_20260730/` to remove all lane
artifacts. No live overlay, `tradier_config`, matrix DB, or
`SWITCH_MATRIX_TRB` cell is modified.
