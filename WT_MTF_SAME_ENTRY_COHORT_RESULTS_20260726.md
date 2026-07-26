# Same-entry multi-timeframe WT exit cohort — 2026-07-26

## Outcome

`EXIT_WT_MTF` completed its bounded screen over the frozen top-10 LONG and
bottom-10 SHORT cohorts. Each symbol/side evaluated exactly 256 settings:

- completed timeframes: 15m, 1h, 4h, D and W;
- minimum timeframes against the held side: 1, 2, 3 or 4;
- WT exhaustion level: 45, 55, 65 or 75;
- minimum adverse WT velocity: 0, 0.25, 0.5 or 1;
- open-position profit gate: 0%, 0.25%, 0.5% or 1%.

The 64 immutable multi-timeframe signal books are vectorized once per key and
reused for all four profit gates. This produced 5,120 side-isolated candidate
rows with zero worker errors. Path-fleet job 40 is `SCREENED`; all 20 frozen
discovery winners are gray and the exact-replay queue is empty.

No candidate passed the complete chronological contract: positive discovery
and untouched-validation alpha versus both side-specific B&H and the
identical-entry E02 control, 70–80% weighted time in market in both partitions,
at least one actual WT exit, and zero future-HTF, insolvency, capacity or
reclaim violation. No matrix, live config or promotion write was made.

Authoritative S1 artifacts:

- cohort:
  `data/reports/vec_research/same_entry_wt_mtf_topbottom_nested_exp70_80_20260726T1838Z`;
- path-fleet job: `data/reports/path_fleet/job_40_EXIT_WT_MTF`;
- ledger: `data/reports/path_fleet/PROGRESS.md`;
- digest: `data/reports/SWITCH_MATRIX_TRB_DIGEST.md`.

## Fixed comparison contract

Every arm uses the entry request timestamps, multipliers, `target`/`add`
semantics and next-RTH fills selected before this exit screen. LONG and SHORT
use separate cash/P&L ledgers. Capacity is $16,000, B&H uses $2,000, and every
fill pays 5 bps commission plus 2 bps adverse slippage. Lower ladder entries
and persistent resting reclaim remain mandatory after an exit.

Only completed higher-timeframe values are consumed. The W source uses the
same completed-bar mapping as 1h/4h/D; future source count is zero in every
selected row. The path does not use `dc_low4_5m`, an interpolated 5m exit, or a
first-break stop.

An arm with zero technical exits remains in the evidence but cannot establish
exit value. Its final result is tagged `zero_exit_mtm` and rejected.

## Frozen LONG winners

Settings are `minimum TFs / exhaustion / velocity / profit gate`. `disc TIM`
and `val TIM` are weighted time in market in discovery and untouched final
folds. Returns and benchmark deltas are from the untouched final fold.

| key | setting | disc TIM | strategy | B&H | Δ control | val TIM | exits | clamps | decisive rejection |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| AMD_LONG | 1/75/0/1 | 62.73% | +710.33% | +134.45% | -470.48pp | 98.19% | 20 | 251 | exposure; validation control |
| ARM_LONG | 1/75/0/1 | 91.33% | +788.35% | +126.60% | -294.09pp | 87.27% | 28 | 305 | exposure; validation control |
| DINO_LONG | 2/45/0/0.5 | 45.00% | +476.46% | +90.70% | -8.78pp | 83.96% | 27 | 85 | exposure; validation control |
| INTC_LONG | 1/65/0/0 | 79.89% | +744.17% | +139.23% | -219.84pp | 89.70% | 13 | 275 | control alpha; validation exposure |
| MPC_LONG | 1/55/0/0 | 53.92% | +156.61% | +88.48% | +39.15pp | 23.83% | 32 | 38 | exposure; discovery-fold instability |
| MRVL_LONG | 2/45/0/1 | 96.18% | +695.39% | +123.58% | -977.33pp | 76.75% | 25 | 241 | discovery exposure; validation control |
| MU_LONG | 4/65/0/0 | 67.65% | +1,294.20% | +204.90% | -82.03pp | 77.79% | 0 | 180 | zero-exit MTM; discovery exposure/control |
| PBF_LONG | 1/75/0.25/0 | 21.15% | +555.69% | +121.26% | -151.58pp | 81.44% | 25 | 67 | exposure; validation control |
| SNDK_LONG | 4/75/0/0 | 99.00% | +2,855.99% | +467.09% | +530.35pp | 99.68% | 2 | 363 | leveraged-hold exposure |
| VLO_LONG | 2/65/0/0 | 49.88% | +407.16% | +84.91% | +19.86pp | 70.63% | 44 | 28 | discovery exposure only |

VLO must not be mistaken for a missed survivor. Its final fold is genuinely
strong: +407.16% versus +84.91% B&H and +387.31% identical-entry control,
70.63% time in market, and 44 actual exits. It also has positive aggregate
discovery alpha (+130.06pp versus B&H, +25.86pp versus control), no insolvency,
no future source and no reclaim breach. It is gray because discovery exposure
was only 49.88%, outside the frozen 70–80% comparison band. Its individual
discovery folds were 21.56% and 77.16%, so this is temporal instability, not a
clerical rejection. It is suitable for a separately preregistered
regime/structure interaction, but not exact replay under this job's rules.

MU's +1,294.20% is not WT-exit evidence: the selected arm made zero exits in
both discovery and validation. SNDK's two final-fold exits and 99.68% exposure
are likewise hold-like. Both remain gray.

## Frozen SHORT winners

| key | setting | disc TIM | strategy | B&H | Δ control | val TIM | exits | insolvent folds | decisive rejection |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| ACN_SHORT | 2/55/0.5/0.5 | 51.63% | +367.29% | +44.50% | -33.68pp | 88.00% | 20 | 0 | exposure; validation control |
| ALB_SHORT | 2/65/0/0 | 88.14% | +45.46% | +19.41% | -40.26pp | 42.60% | 10 | 1 | discovery B&H, validation control, insolvency |
| ASTS_SHORT | 2/45/0.25/1 | 76.33% | +89.47% | +22.27% | +22.89pp | 21.16% | 16 | 2 | negative discovery alpha; exposure; insolvency |
| EGO_SHORT | 2/55/0/0.5 | 31.41% | +54.06% | +23.03% | +6.05pp | 18.96% | 13 | 0 | discovery B&H; exposure |
| HL_SHORT | 2/65/0.25/0.25 | 86.73% | +51.53% | +21.95% | +1.27pp | 17.26% | 14 | 1 | discovery B&H; exposure; insolvency |
| LAC_SHORT | 3/45/0/0.5 | 91.56% | +95.30% | +36.98% | -14.03pp | 19.34% | 5 | 1 | B&H/control; exposure; insolvency |
| LDOS_SHORT | 1/65/1/0 | 43.58% | +60.03% | +37.50% | -7.73pp | 13.24% | 45 | 0 | B&H/control; exposure |
| TTD_SHORT | 3/45/1/0.25 | 56.14% | +532.42% | +54.33% | +50.53pp | 93.70% | 18 | 0 | exposure; discovery-fold instability |
| UEC_SHORT | 3/65/0.5/0 | 72.41% | +72.61% | +22.65% | +7.56pp | 21.81% | 6 | 0 | validation exposure; discovery-fold instability |
| UUUU_SHORT | 2/75/1/1 | 71.70% | +47.91% | +26.20% | -20.32pp | 21.63% | 6 | 1 | B&H/control; exposure; insolvency |

No SHORT selected setting has validation exposure in the 70–80% band. ACN and
TTD obtain large final-fold returns while remaining 88–94% exposed. UEC is the
closest discovery match but collapses to 21.81% in validation. Four selected
SHORT books include an insolvent fold and are independently ineligible.

## What this establishes

1. The WT path is connected and does fire: 19 selected arms made real exits.
2. One universal threshold is not stable across time or side. LONG winners
   tend toward permissive one-TF exits or hold-like four-TF settings; SHORT
   winners mostly exit far too much in the latest fold.
3. Profit gating alone does not solve the cross-fold exposure/control problem.
4. VLO's 2-of-5, 65, zero-velocity, zero-profit-gate recipe is the only selected
   row that passes both final-fold alpha comparisons, final exposure, actual
   exits and all safety ledgers. Its weak early fold blocks promotion.
5. The next WT experiment should add preregistered price-structure confirmation
   or partial sizing. Widening this grid would optimize hold duration, not fix
   the observed temporal instability.

## Reproduction

- `tools/vec_same_entry_exit_adapter.py`
- `tools/path_fleet_wt_mtf_worker.py`
- `tools/path_fleet_e02_worker.py`
- `tools/path_fleet_campaign.py`
- `test_vec_same_entry_exit_adapter.py`
- `test_path_fleet_wt_mtf_worker.py`

```bash
python3 tools/path_fleet_wt_mtf_worker.py \
  --root data/reports/path_fleet \
  --npz-dir backtest_v8/indicators \
  --long-summary data/reports/path_fleet/job_33_ENTRY_LADDER_GREEN/summary.json \
  --short-summary data/reports/path_fleet/job_33_ENTRY_LADDER_GREEN/summary.short.json \
  --output-root data/reports/vec_research/same_entry_wt_mtf_topbottom_nested_exp70_80_20260726T1838Z \
  --workers 4
```

Focused adapter, worker, accounting, causality and campaign tests pass locally.
S1's pytest environment lacks `pluggy`; the focused functions were also
executed directly there and passed.
