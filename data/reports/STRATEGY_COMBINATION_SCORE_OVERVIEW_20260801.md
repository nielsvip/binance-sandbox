# Entry / exit / reentry combination score overview — 2026-08-01

> This report uses the deadline promotion receipt. It does not invent drawdown, win-rate, or Sharpe: those fields are `N/A` where the source receipt did not provide them. Vector-only rows are explicitly marked and are not exact V8 evidence.

## Best scored combinations

| rank | key | path/value | gain/mo | B&H/mo | Δ vs B&H | DD | WR | Sharpe | trades | evidence | campaign |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | MU_LONG | `WT_DC_EXIT_THRESHOLD=45` | 89.7580 | 55.1500 | 34.6080 | N/A | N/A | N/A | 1 | EXACT_ALREADY_LIVE | stocks_repaired_20260730_c5_1yr |
| 2 | AXTI_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | 70.8266 | 38.5320 | 32.2946 | N/A | N/A | N/A | 8 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 3 | MU_LONG | `STRUCTURAL_RANGE_SHIFT_TF="dc_4h"` | 55.0843 | 23.8051 | 31.2792 | 7.1005 | N/A | 0.9909 | 1 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260725_c2 |
| 4 | HAO_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | 57.0836 | 30.5730 | 26.5106 | N/A | N/A | N/A | 4 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v1 |
| 5 | CIEN_SHORT | `BB_BREAKOUT_ENABLED=True` | 33.3696 | 20.9660 | 12.4036 | N/A | N/A | N/A | 10 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 6 | MNTS_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | 51.5810 | 43.5520 | 8.0290 | N/A | N/A | N/A | 4 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 7 | MP_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 14.9453 | 8.3610 | 6.5843 | N/A | N/A | N/A | 43 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 8 | AG_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 13.1839 | 7.1110 | 6.0729 | N/A | N/A | N/A | 35 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 9 | NVDA_LONG | `STRUCTURAL_RANGE_SHIFT_EXIT=true` | 9.1966 | 4.1631 | 5.0335 | 4.0615 | N/A | 0.9694 | 1 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260725_c2 |
| 10 | LSCC_SHORT | `MTF_ARMED_ENTRY_ENABLED=True` | 15.0695 | 10.3700 | 4.6995 | N/A | N/A | N/A | 2 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 11 | MSTR_SHORT | `BB_PULLBACK_GATE_ENABLED=False` | 12.1663 | 7.8320 | 4.3343 | N/A | N/A | N/A | 9 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v1 |
| 12 | ACN_SHORT | `STRUCTURAL_RANGE_SHIFT_EXIT=true` | 7.0653 | 3.9698 | 3.0955 | 0.0221 | N/A | 0.9995 | 1 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260730_c5_1yr |
| 13 | TTD_SHORT | `BB_PULLBACK_GATE_ENABLED=False` | 9.3428 | 6.4780 | 2.8648 | N/A | N/A | N/A | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_repaired_20260730_c5_1yr |
| 14 | HL_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 11.1707 | 8.5180 | 2.6527 | N/A | N/A | N/A | 45 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 15 | MRVL_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 15.2387 | 12.9940 | 2.2447 | N/A | N/A | N/A | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_repaired_20260730_c5_1yr |
| 16 | AMD_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 8.9953 | 6.7900 | 2.2053 | N/A | N/A | N/A | 28 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h |
| 17 | DELL_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 18.8298 | 16.9180 | 1.9118 | N/A | N/A | N/A | 0 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v1 |
| 18 | CDW_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 11.6131 | 10.1090 | 1.5041 | N/A | N/A | N/A | 2 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 19 | PAAS_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 8.6310 | 7.1720 | 1.4590 | N/A | N/A | N/A | 41 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 20 | A_LONG | `MTF_ARMED_ENTRY_ENABLED=True` | 7.0590 | 5.6650 | 1.3940 | N/A | N/A | N/A | 8 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h |
| 21 | VT_LONG | `STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS=50.0` | 2.6565 | 1.3723 | 1.2842 | 3.3479 | N/A | 0.6559 | 2 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260725_c2 |
| 22 | GM_LONG | `STRUCTURAL_RANGE_SHIFT_EXIT=true` | 5.7087 | 4.4619 | 1.2468 | 0.0000 | N/A | 1.0045 | 1 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260730_c5_1yr |
| 23 | PLTR_SHORT | `STRUCTURAL_RANGE_SHIFT_EXIT=true` | 2.8275 | 1.6466 | 1.1809 | 8.5585 | N/A | 0.6697 | 1 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260730_c5_1yr |
| 24 | NEM_LONG | `BB_PULLBACK_GATE_ENABLED=False` | 6.5936 | 5.9150 | 0.6786 | N/A | N/A | N/A | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v1 |
| 25 | AU_LONG | `BB_PULLBACK_GATE_ENABLED=False` | 9.3750 | 9.0010 | 0.3740 | N/A | N/A | N/A | 28 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_baseline_v2_s4h__vec |
| 26 | LAC_SHORT | `DELTA_EXIT_ENABLED=true` | 2.4248 | 2.0770 | 0.3478 | 24.1677 | N/A | 0.2914 | 56 | ACTIVE_CONFIG_PASS_RECEIPT_REMOTE_UNVERIFIED | stocks_repaired_20260725_c2 |
| 27 | COPX_LONG | `BB_PULLBACK_GATE_ENABLED=False` | 6.5180 | 6.2900 | 0.2280 | N/A | N/A | N/A | 1 | VEC_APPROX_OR_HISTORICAL_VECTOR | stocks_repaired_20260730_c5_1yr |

## Combined side-aware baseline

- Candidates with complete gain/B&H/delta: **27**.
- Mean gain/month: **20.6042%**; mean side-aware B&H/month: **13.3257%**; mean delta: **+7.2786 pp/month**.
- Sum of reported gain/month values: **556.3145%**; sum B&H/month: **359.7928%**; sum delta: **+196.5217 pp/month**.
- This is a combined descriptive baseline, not a portfolio backtest: no cross-symbol capital allocation, correlation, aggregate drawdown, win-rate, or portfolio Sharpe was supplied by the receipt.

## Metric availability and next action

- DD/WR/Sharpe must be filled from co-located exact/vector ledgers before claiming those metrics; deadline receipt only carried gain/month, B&H/month, delta, and a trade-count proxy.
- Historical BASELINE_V2 rows are in `SWITCH_MATRIX_TRB_HISTORICAL_INTEGRATED.jsonl` with `exact_completion_credit=false`; they are useful path clues but are not current winners.
- Re-run the same selected cells with the repaired V8 engine and ledger audit to populate DD, WR, Sharpe, and combined portfolio baseline.
