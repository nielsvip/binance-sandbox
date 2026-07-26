# Structural-break → WT1 lower-top exit research

**Date:** 2026-07-26  
**Status:** research only; no live or matrix promotion  
**Artifact:** `data/reports/vec_research/structural_wt_rebound_20260726T062654Z`

## Question

Does a completed 4h structural break followed by a completed 1h lower rebound
top in both price and WT1 improve on immediate `dc_low4_5m` exits and on the
no-exit/side-and-hold floor?

All signals fill at the next RTH open with costs and adverse slippage. LONG and
SHORT are isolated. Reentry uses the lower-price E11 path plus a zero-buffer E10
reclaim obligation that remains latched until filled.

## First result

| Path | Valid folds | Median alpha vs side-and-hold | Mean RTH exposure | Exits | Losing exits | Verdict |
|---|---:|---:|---:|---:|---:|---|
| No-exit runner | 6 | 0.00 pp | 100.0% | 0 | 0 | Control |
| Frozen `dc_low4_5m` | 6 | -1.12 pp | 99.6% | 25 | 25 | Entry-failure diagnostic; discard as profit exit |
| Structural + WT1 lower rebound top | 6 | -4.70 pp | 77.9% | 50 | 30 | Correct exposure shape, but still realizes too many failed reentries |

The valid MU_LONG and VT_LONG folds all remained below their no-exit controls.
The proposed path therefore does **not** fill the switch matrix and is not
promotion eligible.

HAO_SHORT produced `+20.18 pp` alpha with one winning exit, but HAO's current
NPZ contract is invalid. The result is quarantined and cannot be used to select
the path or its parameters.

## Interpretation

The DC comparator confirms the user's diagnosis: every valid-fold DC exit was a
losing leg. It tells us the entry or reentry was wrong; executing it as a normal
exit only adds churn.

The structural sequence is directionally better as a design because it waits
for the rebound and reduces time in market. It still fails economically because
it closes losing post-reentry legs. The next experiment must separate:

1. **Profit harvesting:** execute the confirmed lower-top exit only when the
   current leg is net profitable or previously achieved a sufficient MFE.
2. **Entry failure:** when the same signal occurs on a losing leg, attribute it
   to the entry family and keep it as diagnostic evidence instead of pretending
   it is a top exit.
3. **Reentry quality:** preserve the reclaim obligation, but sweep lower-price
   reentry confirmation so the system does not repeatedly reopen near the exit
   top and pay another round trip.

The next small vector grid covers rebound size, pre-break lookback, wait length,
profit threshold, and MFE activation. Selection must be frozen before scoring a
subsequent fold.

## Profit/MFE grid result

**Artifact:** `data/reports/vec_research/structural_wt_profit_grid_20260726T063646Z`

The grid evaluated 189 fixed candidates. One universal specification was chosen
using only MU+VT 2025Q4, then frozen before later folds:

- rebound: `1 ATR`
- pre-break lookback: `10`
- maximum wait: `30` completed 1h bars
- gate: current leg must exceed round-trip costs by `0.25%`

The gate repaired the losing-exit defect: discovery and validation produced
`9/9` winning executed exits and `0` losing exits. It still did not solve alpha:
only `1/4` valid LONG validation folds beat side-and-hold and median validation
alpha was `-1.04 pp`.

| Validation fold | Strategy | Side-and-hold | Alpha |
|---|---:|---:|---:|
| MU Jan–Feb | +40.32% | +36.29% | +4.03 pp |
| MU recent | -6.85% | +2.60% | -9.44 pp |
| VT Jan–Feb | +3.24% | +3.78% | -0.54 pp |
| VT recent | -0.72% | +0.83% | -1.55 pp |

Seventeen losing lower-top signals were rejected and preserved as entry-failure
diagnostics. The remaining defect is now localized to E10 reclaim execution:
the recent MU reclaim filled `10.01%` worse than its exit and VT filled `1.46%`
worse. The current vector model waits for a close beyond the stored reclaim
level and then buys/sells at the next RTH open. That violates the practical
intent of “do not let price outrun the exit/top without reopening.”

The next comparison keeps the selected exit fixed and models E10 as a persistent
resting stop order:

- LONG: buy stop at `max(exit fill, stored rebound top)`;
- SHORT: mirrored sell stop;
- if a bar gaps beyond the stop, fill at the adverse open plus slippage;
- otherwise, if the executable intrabar range touches the stop, fill at the
  stop plus slippage;
- E11 may still reopen lower before the reclaim stop fires.

This is research execution semantics only. No live order behavior changes until
the vector result and exact-engine parity audit both pass.

## Resting reclaim comparison

**Artifact:** `data/reports/vec_research/resting_reclaim_compare_20260726T064428Z`

The exit specification remained frozen from the prior discovery grid. Only E10
reclaim execution changed from close-cross/next-open to persistent touch/gap
semantics.

| Key/fold | B&H | Delayed E10 | Resting E10 | Improvement |
|---|---:|---:|---:|---:|
| MU Jan–Feb | +36.29% | +40.32% | +40.69% | +0.37 pp |
| MU recent | +2.60% | -6.85% | +13.59% | +20.43 pp |
| VT Jan–Feb | +3.78% | +3.24% | +3.25% | +0.01 pp |
| VT recent | +0.83% | -0.72% | -1.35% | -0.63 pp |

Across the four valid frozen LONG folds:

- median alpha improved from `-1.04 pp` to `+1.94 pp`;
- positive-alpha fold rate became `2/4`;
- mean reclaim overshoot fell from `0.80%` to `0.02%`;
- mean missed move fell from `3.68%` to `2.52%`;
- independent Python/C parity passed for every comparison.

This validates the reclaim execution defect, not a universal strategy. MU recent
improved dramatically; VT did not. Symbol-specific exit/reentry parameters and
untouched validation remain necessary.

## MU ladder discovery probe

**Artifact:** `data/reports/vec_research/struct_wt_resting_reclaim_probe_20260726T070000Z_MU_LONG`

A research grid combined the accepted MU ladder curve with structural/WT1 exits
and resting reclaim. Its in-window best candidate used `rebound=1 ATR`,
`lookback=4` (6 tied), and `max_wait=20h`:

- strategy return: `+409.929%`;
- B&H capital return: `+204.905%`;
- multiple: `2.00058×`;
- weighted exposure: `52.323%`;
- max account drawdown: `23.664%`;
- 18 signals/fills, 15 lower reentries, 2 reclaim reentries;
- future HTF inputs: `0`.

This reaches the requested 2× threshold but is **discovery only** because the
exit parameters were selected in the reported window. It cannot fill the matrix
until parameters are frozen on an earlier slice and pass a later untouched
slice (plus exact-engine parity).
