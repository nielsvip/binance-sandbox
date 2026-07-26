# Same-Entry WT Exit Adapter — 2026-07-26

## Outcome

`tools/vec_same_entry_exit_adapter.py` now lets alternative EXIT paths consume
the already-frozen band-ladder entry request schedule from each path-fleet
artifact. It does not reselect an entry curve while testing an exit.

The first completed cohort screen covered all ten accepted top-LONG controls:
SNDK, MRVL, ARM, MU, INTC, AMD, PBF, MPC, DINO, and VLO. It tested 120 coherent
multi-timeframe WaveTrend blocks and 27 structural lower-low → lower-top
WaveTrend blocks per symbol.

There is **no exact-replay survivor**. Several latest-fold WT settings looked
better than E02, but none survived the chronological freeze, identical-entry
E02 comparison, and 70–80% weighted exposure gate together. No result was
written to `SWITCH_MATRIX_TRB`, `PARAM_BASELINE_STOCKS`, or live configuration.

Authoritative S1 artifact:

`data/reports/vec_research/same_entry_exit_top10_nested_exp70_80_20260726T1758Z`

## Contract

Every candidate uses:

- the curve selected in the source ladder artifact before the exit screen;
- a SHA-256 of the exact entry request timestamps and multipliers for each
  validation fold;
- the frozen `target`/`add` semantics and next-RTH fill rule;
- $2,000 B&H denominator and $16,000 hard strategy capacity;
- 5 bps commission plus 2 bps slippage per side;
- LONG-only, side-keyed accounting for this cohort;
- completed HTF source timestamps with a fail on future information;
- persistent zero-buffer resting reclaim on the first OHLC touch or adverse
  gap open, plus the lower-ladder reentry route;
- a 70–80% weighted time-in-market survivor gate.

`dc_low4` is not present in the profit-exit implementation. A lower Donchian
break remains an entry-quality diagnostic only.

The same adapter reconstructs the 4h N=30 E02 control. This is intentional:
the old path-fleet artifact used a close-cross reclaim approximation, whereas
the candidate and reconstructed control both use the corrected resting-touch
reclaim. The source artifact control is retained separately in every result.

## Latest-fold discovery warning

These five WT results beat both B&H and the reconstructed identical-entry E02
control on the latest fold, but they were **discovery observations**, not
promotion evidence:

| symbol | WT alpha vs E02 | weighted TIM | reason not promotable |
|---|---:|---:|---|
| ARM | +42.60 pp | 81.44% | outside 70–80%; settings saw this fold |
| INTC | +98.35 pp | 84.72% | outside 70–80%; settings saw this fold |
| MPC | +52.02 pp | 23.33% | far below exposure target |
| DINO | +90.86 pp | 90.53% | far above exposure target |
| VLO | +93.27 pp | 80.09% | marginally above target; settings saw this fold |

This is exactly why “beats B&H” alone is not sufficient. The E02 control and
exposure contract prevent a low-exposure or reselected exit from being
mistaken for an improvement.

## Chronological freeze

For each symbol and exit family, the grid was ranked on the earlier folds
without using the final fold. One setting was frozen per family, then scored
on the untouched final fold.

### Frozen WT settings

| symbol | discovery alpha vs E02 | validation alpha vs E02 | validation TIM | verdict |
|---|---:|---:|---:|---|
| SNDK | -1,641.79 pp | -756.93 pp | 91.44% | gray |
| MRVL | +157.10 pp | -866.13 pp | 85.39% | gray |
| ARM | +76.43 pp | -286.06 pp | 92.10% | gray |
| MU | -249.25 pp | -822.25 pp | 51.62% | gray |
| INTC | -235.80 pp | -143.90 pp | 84.20% | gray |
| AMD | +130.34 pp | -405.49 pp | 95.64% | gray |
| PBF | +34.27 pp | -40.80 pp | 86.93% | gray |
| MPC | +28.95 pp | +27.85 pp | 20.00% | gray: exposure failure |
| DINO | +29.23 pp | -27.29 pp | 74.62% | gray |
| VLO | +88.27 pp | +22.64 pp | 67.58% | gray: exposure failure |

MPC and VLO retained positive WT alpha versus E02 on the final fold, but their
frozen settings did not satisfy the exposure contract. They are useful search
seeds, not exact-replay candidates.

### Structural lower-low → lower-top WT

The structural path also produced no survivor. Its strongest apparent
exception was MPC (+79.04 pp discovery, +23.73 pp validation versus E02), but
validation weighted exposure was only 21.19%. VLO was +69.61 pp in discovery
and -26.01 pp in validation. Every other symbol either failed validation
against E02, missed the exposure band, or both.

The structural rows remain gray. They are retained as tested evidence so the
same settings are not repeatedly rediscovered, but they are not a replacement
for E02.

## Causality and reentry audit

Across the completed top-ten runs:

- future HTF source count: zero;
- bars flat beyond the mandatory reclaim after the fill bar: zero;
- LONG and SHORT P&L pooled: false;
- `dc_low4` used as a profit exit: false;
- exact replay launched: false, because the survivor queue is empty.

## Reproduction

```bash
python3 tools/run_same_entry_exit_cohort.py \
  --control-summary data/reports/path_fleet/job_33_ENTRY_LADDER_GREEN/summary.json \
  --npz-dir backtest_v8/indicators \
  --output-root data/reports/vec_research/same_entry_exit_top10_nested_exp70_80_<STAMP> \
  --families WT_MTF,STRUCTURAL_WT \
  --fold-mode nested \
  --workers 3 \
  --exposure-min-pct 70 \
  --exposure-max-pct 80
```

Unit coverage is in `test_vec_same_entry_exit_adapter.py`; the combined local
adapter/structural/ladder suite passed 17 tests.

## Next exit blocks

The adapter is generic enough for the remaining alternative exits to use the
same frozen request schedule. The next useful blocks are E01 Chandelier,
E05 divergence/retest, and partial-runner candidates. They must retain the
same nested freeze, resting reclaim, 70–80% exposure, B&H, and identical-entry
E02 gates. Relaxing the E02 comparison because a candidate beats B&H would
repeat the error this adapter was built to prevent.

## MPC/VLO partial-WT follow-up

The closest full-exit WT near-misses were tested again as bounded clips while
retaining E02 for the runner:

- clip fractions: 15%, 25%, 33%, and 50%;
- slower/stricter completed-TF blocks through 4h/D;
- WT extremes 65/75/85, adverse velocity 0.5/1/2, recent-extreme windows 8/16;
- majority or unanimous TF votes, with and without fast price structure;
- every clip owns an immediate resting reclaim at its exit/top level;
- E02 still owns the full runner exit and full-position reclaim.

S1 artifacts:

- `data/reports/vec_research/same_entry_partial_wt_nested_exp70_80_20260726T1815Z_MPC_LONG`
- `data/reports/vec_research/same_entry_partial_wt_nested_exp70_80_20260726T1817Z_VLO_LONG`

Neither symbol produced a candidate with positive alpha versus E02 on both
discovery and untouched validation.

For MPC, the discovery-selected partial block was a 50% clip using 15m+1h,
WT extreme 75, velocity 1, 16-bar recent-extreme memory, fast structure, and a
0.5% profit gate. It made +60.29 pp versus E02 in discovery but lost
41.23 pp versus E02 in validation; validation return was 76.23% versus 88.48%
B&H and weighted exposure was only 25.26%. Even the best frozen 15% clip changed
from +15.40 pp discovery to -14.98 pp validation. The underlying E02 control
itself had only 48.06% aggregate weighted exposure, so an exit-only change
cannot lift this symbol into the top-performer 70–80% band. MPC needs its
frozen ladder entry curve corrected before more exit tuning.

For VLO, no clip fraction had positive discovery alpha versus E02. The
exposure-gated frozen 25% block lost 59.30 pp in discovery and 38.51 pp in
validation. Its validation exposure was 86.22%. The best validation exposure
movement among the requested clip sizes still remained above 80% or destroyed
E02 alpha. VLO's earlier full-WT +22.64 pp validation observation therefore
did not survive the prior-fold freeze.

The partial implementation recorded clip exits and clip reclaims separately.
Future HTF count and flat-beyond-reclaim count remained zero. The result queue
is empty, so no exact replay was launched and all partial rows remain gray.
