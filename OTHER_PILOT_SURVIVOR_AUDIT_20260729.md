# Non-MU pilot survivor audit — 2026-07-29

## Outcome

No non-MU pilot is promotable. The bounded, leak-safe rescreen produced **zero
stable discovery survivors**, so the third chronological fold remained sealed
for every key and neither ordinary-engine nor live-state parity was run.
Nothing was written to live configuration or the switch matrix.

**TTD_SHORT is nearest**, but it is not close enough to advance: its best
predeclared profile earned +15.246 percentage points over its side-aware
benchmark in discovery fold 1, then lost 25.852 points to the benchmark in fold
2. This is regime instability, not a data or capacity failure.

## Contract

The campaign
`tools/run_other_pilot_survivor_audit.py` screened `TTD_SHORT`, `ACN_SHORT`,
`NVDA_LONG`, `VT_LONG`, and `LAC_SHORT` under:

- frozen S1 NPZ hash, row count, last-bar, and synthetic-parent-clock checks;
- completed-parent availability and next-strictly-later execution;
- separate LONG and SHORT ledgers with adverse side-aware fills;
- six coherent meta profiles per valid key: capacity $4,000/$8,000 crossed with
  completed-4h Donchian exits N20/N30/N45;
- 164 coherent ladder curves per profile (84 deterministic, 80 seeded random);
- discovery folds 1 and 2 only; fold 3 is never computed for a losing profile;
- 65–80% exposure-weighted time in market on every discovery fold;
- fill ratio at least 0.75, solvency, hard entry capacity, zero forgotten
  reclaim bars, and zero future-HTF observations;
- ranking on deployed-capital alpha, not the levered $2,000-label return.

The deployed denominator is pre-cost committed/requested dollar-time averaged
over the complete comparison window. It is not marked notional: marked
notional falls automatically for a winning SHORT and rises for a winning LONG,
which previously created a side-dependent denominator bias.

The fixed comparison floor is
`max(side-aware B&H return, 0% cash)`. A B&H ratio is publishable only when the
side-aware B&H return is positive and at least +20 percentage points. In
particular, negative strategy divided by negative B&H is not a positive result.

## Frozen source audit

| key | rows | last bar UTC | parent clock | source verdict |
|---|---:|---|---|---|
| NVDA_LONG | 115,904 | 2026-07-27 23:55 | yes | PASS |
| TTD_SHORT | 93,922 | 2026-07-24 23:50 | yes | PASS |
| ACN_SHORT | 60,206 | 2026-07-24 23:40 | yes | PASS |
| LAC_SHORT | 98,047 | 2026-07-24 23:55 | yes | PASS |
| VT_LONG | 41,824 | 2026-07-24 22:00 | **no** | BLOCKED: synthetic parent clock missing |

The S1 rolling and frozen hashes matched for the four valid keys. VT's rolling
source had already drifted from the frozen file, and the frozen file still
lacks `synthetic_5m_parent_close_ts`; it was not rescreened.

## Nearest profile per key

Values are return per deployed capital relative to the better of side-aware
B&H and cash. `PASS` requires both the alpha and exposure gates.

| key | profile | fold 1 alpha / TIM | fold 2 alpha / TIM | fills | clamps | discovery |
|---|---|---:|---:|---:|---:|---|
| **TTD_SHORT** | $8k, E02 N30 | **+15.246pp / 70.22%** | **−25.852pp / 77.61%** | 52 | 0 | fail fold 2 |
| ACN_SHORT | $8k, E02 N20 | −14.417pp / 66.71% | −7.373pp / 78.22% | 70 | 0 | fail both |
| NVDA_LONG | $4k, E02 N20 | +4.087pp / 82.50% | −1.441pp / 77.74% | 44 | 0 | fail both |
| LAC_SHORT | $8k, E02 N20 | +0.315pp / 84.24% | **−84.328pp / 75.72%** | 50 | 0 | fail both |
| VT_LONG | — | — | — | — | — | source blocked |

All valid-key rows had fill ratio 1.0, zero clamps, zero future HTF sources,
and no entry-capacity breach. The failure is therefore not an inability to
execute the requested ladder size:

- TTD changes sign across regimes.
- ACN stays in-band but trails B&H on deployed capital in both folds.
- NVDA has a small first-fold edge only by exceeding the exposure ceiling,
  then trails in fold 2.
- LAC nearly ties its positive short B&H in fold 1 at excessive exposure, then
  loses 84.328% per deployed dollar against the cash floor in fold 2. Its
  side-aware B&H was −61.888%; the former negative/negative `1.36×` ratio was
  invalid and is now suppressed.

## What this invalidates

The old headline rows cannot be promotion evidence:

- TTD_SHORT `+747.74% vs +141.54% (5.28×)` had aggregate fill ratio 0.201 and
  used the levered $2,000-label comparison.
- ACN_SHORT `+407.15% vs +69.84% (5.83×)` did not satisfy the current
  deployed-capital/fold-stability contract.
- NVDA_LONG `+209.5% vs +43.7% (4.79×)` was a private vector schedule on an
  earlier file and a levered ratio, not ordinary-engine/live parity.
- VT_LONG was already the negative control and now also fails the required
  frozen parent-clock source contract.
- LAC_SHORT's old aggregate was insolvent; the corrected screen again exposes
  catastrophic regime risk.

The old `v8_exact_ladder_replay` receipts replayed a private schedule through an
engine hook. They establish schedule/accounting agreement only. They do not
exercise ordinary
`check_entry_candidates_for_account()` /
`check_exit_candidates_for_account()` decisions and cannot establish live
parity.

## Nearest blocker and next safe search

TTD_SHORT has valid, sufficiently deep, current-contract data. Its remaining
blocker is **strategy instability**, not NPZ regeneration: the N30 correction
book works in the first discovery regime and gives back the edge in the
second. The next bounded test should therefore split correction-short and
confirmed-bear continuation states before sizing, while keeping the same
deployed-alpha/cash-floor score. Repeating more N values or increasing capacity
would only resample a family that already changed sign.

Even if that search creates a stable vector survivor, a separate code blocker
remains: the ordinary backtest/live decision path does not implement this
three-timeframe ladder plus completed-4h exit and stored reclaim state machine.
That path must be connected and shown to match the vector actions before any
per-symbol setting is eligible for a canary.

## Evidence and rollback

- Frozen manifest:
  `data/reports/vec_research/OTHER_PILOT_FROZEN_MANIFEST_20260729.json`
- Compact fleet receipt:
  `data/reports/vec_research/OTHER_PILOT_SURVIVOR_RECEIPT_20260729.json`
- Full S1 artifact:
  `data/reports/vec_research/other_pilot_survivor_20260729T160821Z/result.json`
- Tests:
  `test_run_other_pilot_survivor_audit.py`,
  `test_vec_band_ladder_walkforward.py`

Rollback is limited to the runner, the deployed-capital telemetry/scorer in
`tools/vec_band_ladder_walkforward.py`, its compiled parity scanner, digest
rendering, tests, and these append-only reports. No live position, symbol list,
active config, matrix cell, or canonical NPZ changed.
