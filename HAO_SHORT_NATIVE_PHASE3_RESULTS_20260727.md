# HAO_SHORT native-entry phase 3 — 2026-07-27

## Outcome

The corrected phase-3 campaign found **zero discovery-strict candidates** and
therefore ran no exact v3 replay. HAO remains isolated and quarantined. No
canonical indicator, Tradier setting, switch-matrix cell, path-fleet row, or
live state was changed.

This is a useful rejection rather than a zero-trade failure. Twelve of 24 E02
entry-filter candidates and twelve of 24 E05 candidates beat the positive
$2,000 SHORT B&H opportunity in both discovery folds. The failure was
stability:

- no candidate held 70–80% exposure-weighted time in market in both discovery
  folds;
- no E05 candidate beat its identical-filter E02 control in both discovery
  folds; and
- twelve E05 candidates became insolvent in at least one discovery fold after
  their changed exit/reentry sequence, despite starting from ladder profiles
  that were solvent in the §15.26 control.

## Data, clock, and accounting boundary

The only input was the isolated HAO recovery NPZ, unchanged before and after:

`17907495bd909a1ea7ebcd6c7846134a7e70ae966b0fe7e79c3006107bb36332`

The frozen ladder source result was also hash-bound:

`dff84d48cb54e5d796aade003d9e1888b22b13ebda7e3126f9b57992a261e3da`

The run used `SYNTHETIC_PARENT_CLOSE_AVAILABILITY_V1`: 27,092 ordered RTH
observations, 7,641 synthetic rows, and 5,487 duplicate parent-close
availability rows. Entries and covers filled only at the first strictly later
availability batch. The completed 1h/4h/D audit made 4,904 source checks and
found zero future sources. SHORT entries used bid-adverse slippage; covers and
terminal liquidation used ask-adverse slippage. Commission was charged each
way.

Each fold kept a $10,000 solvency ledger, $2,000 comparison unit, and $16,000
hard notional capacity. The opportunity gate was
`strategy > max(side-specific short B&H, cash=0)`. HAO's short B&H was positive
in both discovery folds: +67.3870% and +20.6375%, so neither comparison relies
on a negative-denominator “multiple.” The final fold remained sealed because
discovery had no strict survivor.

## Preregistered screen

The campaign fixed three all-fold-solvent ladder reductions from §15.26:

- exact frozen ladder, scale 1.0, cap 6x;
- D at 75%, scale 1.0, cap 6x; and
- exact frozen ladder, scale 0.875, cap 6x.

Each was crossed with eight SHORT-native filters and two covers, making 48
discovery candidates. Every filter first required a completed daily and 4h
bear state: close below EMA20 and WaveTrend 1 below WaveTrend 2 on both
timeframes. The distinct 1h confirmations were regime state, lower-high plus
lower-low breakdown, WT rollover, their union, downside ATR acceleration,
failed bullish EMA reclaim, structure plus acceleration, and the union of all
confirmed-bear events. A bounded 72–120 hour causal arm let later ladder
requests use the confirmation without backfilling earlier signals.

The two covers were:

- E02: completed 4h Donchian N30; and
- fixed E05: completed prior-only 4h divergence, later structural break,
  ATR rebound, then later rollover. The first break itself never covered.

Filtered E02 versus the ungated ladder/E02 baseline measures the entry
contribution. E05 versus the identical filtered-entry E02 result measures the
exit contribution.

Discovery folds 1–2 were fully serialized, then
`DISCOVERY_FREEZE.json` was written. Because its strict count was zero, fold 3
was never simulated or serialized. File contents and the runner's control flow
enforce that boundary.

## Ungated ladder attribution

The strict parent-close simulator gives the following controls:

| ladder | fold | E02 return | E05 return | E05 − E02 | E02 weighted TIM | E05 weighted TIM |
|---|---:|---:|---:|---:|---:|---:|
| exact 1.0 / 6x | D1 | 348.94% | 310.77% | -38.17pp | 56.72% | 56.47% |
| exact 1.0 / 6x | D2 | 36.73% | 103.82% | +67.08pp | 75.67% | 76.59% |
| D75 1.0 / 6x | D1 | 348.94% | 310.77% | -38.17pp | 56.72% | 56.47% |
| D75 1.0 / 6x | D2 | 36.73% | 103.82% | +67.08pp | 75.67% | 76.59% |
| exact .875 / 6x | D1 | 349.96% | 313.66% | -36.30pp | 52.39% | 52.43% |
| exact .875 / 6x | D2 | 46.75% | 106.51% | +59.76pp | 72.86% | 73.43% |

This isolates the exit problem clearly. E05 helps substantially in discovery
fold 2, but loses 36–38pp to E02 in discovery fold 1. No final E05 metric was
computed because that inconsistency already fails the discovery contract.

## Frozen gray diagnostics

With no discovery-strict row, the protocol froze one best gray diagnostic per
exit family. Both use the lower-risk exact .875/6x ladder and the broad
completed bear-state filter.

| path | fold | return | short B&H | same-entry E02 | entry contribution | exit contribution | weighted TIM | min equity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| filtered E02 | D1 | 152.38% | 67.39% | 152.38% | -197.58pp | — | 37.26% | $8,825.75 |
| filtered E02 | D2 | 134.11% | 20.64% | 134.11% | +87.36pp | — | 58.69% | $2,605.78 |
| filtered E05 | D1 | 111.88% | 67.39% | 152.38% | -238.09pp | -40.50pp | 57.00% | $4,674.59 |
| filtered E05 | D2 | 176.29% | 20.64% | 134.11% | +129.54pp | +42.18pp | 58.22% | $2,605.78 |

Binary occupancy can look superficially acceptable—the frozen filtered E02
was open 86.43% and 76.52% of discovery bars—but weighted exposure was only
37.26% and 58.69%. The filter left an undersized short open instead of
deploying the ladder's intended capital. That distinction explains why merely
loosening an arm window is unlikely to solve the result.

The sharper 1h filters were less stable. LH/LL, WT rollover, their union and
the broad event union were insolvent in discovery fold 1 under E05. Downside
acceleration and failed bullish reclaim stayed solvent more often and beat
B&H in both discovery folds for some ladder/exit pairs, but their best
fold-1 weighted exposures were only about 18–20%. They are useful event
features, not viable standalone gates.

## Reentry correction and invalidated attempts

Three preliminary artifacts were preserved and explicitly invalidated:

1. V1 simulated an unused final baseline before persisting the discovery
   freeze. Its ranking was not affected, but it violated the structural
   sealed-final rule.
2. V2 remembered a reclaim touch that occurred while the entry filter vetoed
   reentry, then could submit the short later after price had recovered above
   the stored level. V3 keeps the obligation but enters only when the current
   bar is still at or through the stored short-reclaim level.
3. V3 fixed that state bug but still evaluated the final fold for gray
   diagnostics despite zero discovery-strict candidates. V4 leaves every
   final array and baseline unobserved unless discovery first passes.

These invalidations are recorded in
`data/reports/vec_research/HAO_SHORT_NATIVE_PHASE3_INVALIDATIONS.json`.
Focused tests cover the reclaim-veto sequence, adverse fill direction,
final-blind freeze, actual-exit gate, clock/source constraints, bounded grid,
solvency, capacity, TIM, and benchmark rules.

## Decision and rollback

The corrected V4 result is gray:

- 48 discovery candidates;
- 0 discovery-strict;
- 2 frozen diagnostics;
- final fold sealed and not evaluated;
- exact v3 not run because the discovery contract failed.

The machine artifact is:

`data/npz_recovery/hao_recovery_20260727T0010Z/controls/short_native_phase3/hao_short_native_phase3_20260727T041500Z/`

The result SHA-256 is
`daa59b46e8229e8bce145c5eeef3a403435ae1388dd10e78ad379e7e2aaf62ca`.
The compact repository receipt is
`data/reports/vec_research/HAO_SHORT_NATIVE_PHASE3_RECEIPT_20260727.json`.

Rollback is deletion of the isolated phase-3 artifact directories and this
research documentation. No canonical, matrix, fleet, config, or live rollback
is required because none was written.
