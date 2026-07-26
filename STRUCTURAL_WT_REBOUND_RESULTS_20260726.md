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
