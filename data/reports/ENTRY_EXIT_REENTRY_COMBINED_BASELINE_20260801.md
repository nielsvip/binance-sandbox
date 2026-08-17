# Entry / exit / reentry combined baseline — 2026-08-01

> This is a scorecard, not a claim that every row is production-safe. Exact ENGINE PASS rows are the only rows with live-grade evidence. Vector/deadline and retired-history rows retain their provenance and show `—` where DD, WR, or Sharpe was not recorded.

## Combined exact baseline

A one-close trace is not a B&H comparison. Rows below one real close per elapsed month remain diagnostic only. The activity-qualified baseline below includes only rows that clear the hard monthly floor; rows below the four-closes/month weekly target are still marked as research-only.

- Activity-qualified rows: **1**; weekly-target rows: **0**; trade-level WR: **N/A** (ledger unavailable).
- Mean gain/mo: **2.4248%**; mean B&H/mo: **2.0770%**; mean delta: **+0.3478 pp/mo**.
- Mean max DD: **24.1677%**; mean pool Sharpe: **0.2914**.
- Excluded as `B&H_COMPARISON_INVALID_ACTIVITY`: **6** row(s); their stored gains/deltas are diagnostic only.

| key | entry/exit/reentry cell | real closes | months | closes/month | activity | B&H validity | promotion basis | DD % | B&H DD % | WR % | Sharpe | gain/mo % | B&H/mo % | Δ vs B&H pp | trace | evidence |
|---|---|---:|---:|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| MU_LONG | `STRUCTURAL_RANGE_SHIFT_TF="dc_4h"; default entry/reentry` | 1 | 24 | 0.04 | `B&H_COMPARISON_INVALID_ACTIVITY` | INVALID — one/month floor | `NO_VALID_BH_OR_DD_EXCEPTION` | 7.1005 | — | — | 0.9909 | 55.0843 | N/A | N/A | 2 | EXACT PASS stocks_repaired_20260725_c2/full |
| NVDA_LONG | `STRUCTURAL_RANGE_SHIFT_EXIT=true; default entry/reentry` | 1 | 24 | 0.04 | `B&H_COMPARISON_INVALID_ACTIVITY` | INVALID — one/month floor | `NO_VALID_BH_OR_DD_EXCEPTION` | 4.0615 | — | — | 0.9694 | 9.1966 | N/A | N/A | 2 | EXACT PASS stocks_repaired_20260725_c2/full |
| ACN_SHORT | `STRUCTURAL_RANGE_SHIFT_EXIT=true; default entry/reentry` | 1 | 12 | 0.08 | `B&H_COMPARISON_INVALID_ACTIVITY` | INVALID — one/month floor | `NO_VALID_BH_OR_DD_EXCEPTION` | 0.0221 | — | — | 0.9995 | 7.0653 | N/A | N/A | 2 | EXACT PASS stocks_repaired_20260730_c5_1yr/1yr |
| VT_LONG | `STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS=50.0; default entry/reentry` | 2 | 24 | 0.08 | `B&H_COMPARISON_INVALID_ACTIVITY` | INVALID — one/month floor | `NO_VALID_BH_OR_DD_EXCEPTION` | 3.3479 | — | — | 0.6559 | 2.6565 | N/A | N/A | 3 | EXACT PASS stocks_repaired_20260725_c2/full |
| GM_LONG | `STRUCTURAL_RANGE_SHIFT_EXIT=true; default entry/reentry` | 1 | 12 | 0.08 | `B&H_COMPARISON_INVALID_ACTIVITY` | INVALID — one/month floor | `NO_VALID_BH_OR_DD_EXCEPTION` | 0.0000 | — | — | 1.0045 | 5.7087 | N/A | N/A | 2 | EXACT PASS stocks_repaired_20260730_c5_1yr/1yr |
| PLTR_SHORT | `STRUCTURAL_RANGE_SHIFT_EXIT=true; default entry/reentry` | 1 | 12 | 0.08 | `B&H_COMPARISON_INVALID_ACTIVITY` | INVALID — one/month floor | `NO_VALID_BH_OR_DD_EXCEPTION` | 8.5585 | — | — | 0.6697 | 2.8275 | N/A | N/A | 2 | EXACT PASS stocks_repaired_20260730_c5_1yr/1yr |
| LAC_SHORT | `DELTA_EXIT_ENABLED=true; default entry/reentry` | 56 | 24 | 2.33 | `BELOW_WEEKLY_ACTIVITY_TARGET` | VALID | `POSITIVE_BH_DELTA` | 24.1677 | — | — | 0.2914 | 2.4248 | 2.0770 | +0.3478 | 57 | EXACT PASS stocks_repaired_20260725_c2/full |

## Deadline/vector candidates (provisional; no fabricated risk metrics)

These are the 20 user-authorized deadline candidates currently staged in `active_config.json`. Their source rows provide gain/mo, B&H/mo, delta, and a cell, but not a trustworthy DD/WR/Sharpe receipt; those columns therefore remain `—`.

| key | entry/exit/reentry cell | DD % | WR % | Sharpe | gain/mo % | B&H/mo % | Δ vs B&H pp | source trades | evidence |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| MU_LONG | `WT_DC_EXIT_THRESHOLD=45` | — | — | — | 89.7580 | 55.1500 | +34.6080 | 1 | EXACT_ALREADY_LIVE |
| AXTI_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 70.8266 | 38.5320 | +32.2946 | 8 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| HAO_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 57.0836 | 30.5730 | +26.5106 | 4 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| CIEN_SHORT | `BB_BREAKOUT_ENABLED=True` | — | — | — | 33.3696 | 20.9660 | +12.4036 | 10 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| MNTS_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 51.5810 | 43.5520 | +8.0290 | 4 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| MP_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 14.9453 | 8.3610 | +6.5843 | 43 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| AG_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 13.1839 | 7.1110 | +6.0729 | 35 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| LSCC_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 15.0695 | 10.3700 | +4.6995 | 2 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| MSTR_SHORT | `BB_PULLBACK_GATE_ENABLED=False` | — | — | — | 12.1663 | 7.8320 | +4.3343 | 9 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| TTD_SHORT | `BB_PULLBACK_GATE_ENABLED=False` | — | — | — | 9.3428 | 6.4780 | +2.8648 | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| HL_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 11.1707 | 8.5180 | +2.6527 | 45 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| MRVL_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 15.2387 | 12.9940 | +2.2447 | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| AMD_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 8.9953 | 6.7900 | +2.2053 | 28 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| DELL_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 18.8298 | 16.9180 | +1.9118 | — | VEC_APPROX_OR_HISTORICAL_VECTOR |
| CDW_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 11.6131 | 10.1090 | +1.5041 | 2 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| PAAS_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 8.6310 | 7.1720 | +1.4590 | 41 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| A_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | — | — | — | 7.0590 | 5.6650 | +1.3940 | 8 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| NEM_LONG | `BB_PULLBACK_GATE_ENABLED=False` | — | — | — | 6.5936 | 5.9150 | +0.6786 | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| AU_LONG | `BB_PULLBACK_GATE_ENABLED=False` | — | — | — | 9.3750 | 9.0010 | +0.3740 | 28 | VEC_APPROX_OR_HISTORICAL_VECTOR |
| COPX_LONG | `BB_PULLBACK_GATE_ENABLED=False` | — | — | — | 6.5180 | 6.2900 | +0.2280 | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR |

## Research combination leaders (not promotion evidence)

| combination | gain/mo | DD % | WR % | Sharpe | Δ vs B&H | B&H multiple | disposition |
|---|---:|---:|---:|---:|---:|---:|---|
| MU structural-WT nested frozen candidate | — | 23.6636 | — | — | +22.6535 pp | 2.9741× | research candidate; ladder/control checks prevent promotion |
| MU daily deep Pareto holdout final | — | 35.8534 | — | — | +965.4485 pp | 5.7037× | not robust-stable across folds |
| MU ladder exposure retune | — | 38.5057 | — | — | — | 5.3995× | fails exposure target / vector survivor gate |
| MU exact ladder replay | — | — | — | — | — | 6.4117× | replay evidence only; no matrix write/promotion receipt |

## Retired S4H history integration rule

`SWITCH_MATRIX_TRB_ENGINE_HIST_STOCKS_BASELINE_V2_S4H` is delta-only legacy evidence (retired 2026-07-25). It can seed a cell's priority and preserve its raw source/hash, but it cannot supply DD, WR, Sharpe, gain/mo, or an exact PASS. The largest retained delta signals include MU_LONG +914.03, SNDK_SHORT +184.41, SNDK_LONG +128.51, PLTR_LONG +80.49, ASTS_SHORT +70.00, MNTS_LONG +53.28, HAO_SHORT +46.14, AXTI_LONG +36.77, NVDA_LONG +35.60, and ARM_LONG +25.21. These must remain labelled `LEGACY_S4H_DELTA_ONLY` until replayed under the current capital and receipt contract.

## Reading this scorecard

- DD is max account drawdown percentage; WR is intentionally N/A until the co-located trade ledger is available; Sharpe is the stored pool Sharpe. These are not interchangeable with vector proxy metrics.
- The repeated 50% values were an artifact of counting the final engine MTM row alongside one real close in two-element traces, not a certified trade win rate. They have been removed.
- A stored B&H/delta number on an activity-invalid row is diagnostic source data only; it is intentionally rendered as N/A in the validity columns and excluded from aggregates.
- The hard floor is at least one real close/month; the preferred target is at least four real closes/month (weekly average). TIM remains 50–80% for ranks 1–10 per side and 20–60% for all other keys.
- No missing metric has been backfilled from a different campaign, side, or obsolete history file.
