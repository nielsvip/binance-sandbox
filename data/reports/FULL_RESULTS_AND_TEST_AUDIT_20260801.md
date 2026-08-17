# FULL RESULTS AND TEST AUDIT — 2026-08-01

> Exact ENGINE status is sourced from authoritative S1 (`157.180.125.52`), observed `2026-08-01T19:24:28Z`. S1 has **2,082** current repaired-contract ENGINE rows, **93,639** total parameter cells, and **1,194** baselines. Amber/vector rows are reported separately and never receive exact credit.

## Headline counts
- Deadline receipt: **20** historical candidates; vector overrides applied **19**; exact already live **1**; currently in TRB side allowlists **10**.
- Only records with a complete raw-P&L → average-deployed → **$2,000** normalization receipt are comparable to B&H. Vector/capture rows without that receipt are quarantined and never ranked or promoted.
- Full overlay: **432,176** rows; status counts `{"DATA_UNAVAILABLE": 420073, "INERT": 12103}`; exact credit **0**.
- Current c5 exact credit: **0**; current-contract pilot differential coverage is reported below. Historical/quarantined ENGINE rows remain preserved: **10,022/10,034**.
- Capture source timestamp: `2026-08-01T22:06:44Z`; SHA256 `b2a620267e4fd8123917aed37907e88d08f8789c0f7b05a1ebf915c6088e05b4`.

## S1 current-contract pilot coverage
| key | exact differential | amber provisional | provisional total | plateau cross-key | strategy tests |
|---|---:|---:|---:|---:|---:|
| MU_LONG | 0/824 | 824 | 824/824 | 1/785 | 1 |
| NVDA_LONG | 8/824 | 576 | 584/824 | 2/785 | 15 |
| VT_LONG | 115/824 | 493 | 608/824 | 782/785 | 1,861 |
| TTD_SHORT | 1/874 | 633 | 634/874 | 25/726 | 81 |
| ACN_SHORT | 0/874 | 634 | 634/874 | 37/726 | 110 |
| LAC_SHORT | 3/874 | 631 | 634/874 | 9/726 | 14 |

## $2,000-normalized deadline candidates
| key | gain/mo | B&H/mo | delta | tested cell | campaign | allowlist |
|---|---:|---:|---:|---|---|:---:|

## Exact active-config rows: $2,000-normalized, above B&H, >10 trades and >2%/month
| key | gain/mo | B&H/mo | delta | campaign/window | closes | tag |
|---|---:|---:|---:|---|---:|---|
| VT_LONG | 2.8412 | 1.5014 | +1.3398 | stocks_repaired_20260730_c5_1yr / 1yr | 38 | `MATRIX_C5_1YR_WT_DC_EXIT_ENABLED_true` |
| LAC_SHORT | 2.4248 | 2.0770 | +0.3478 | stocks_repaired_20260725_c2 / full | 56 | `MATRIX_HIST_C4_VALIDATED_FULL_DELTA_EXIT_ENABLED_true` |

## Capture best-cell rows above B&H and >2%/month
| key | gain/mo | B&H/mo | delta | cell | campaign | trades |
|---|---:|---:|---:|---|---|---:|

## Capture baseline rows above B&H and >2%/month
| key | gain/mo | B&H/mo | delta | campaign | trades | best cell |
|---|---:|---:|---:|---|---:|---|

## Local VEC_APPROX scalar ledgers: best positive key result
| key | gain/mo | B&H/mo | delta | parameter | proxy | trades |
|---|---:|---:|---:|---|---|---:|

## Tests run
- `python -m py_compile config_tradier.py tradier_manage.py` — PASS.
- `pytest -q test_tradier_entry_contract.py test_options_safety.py` — **11 passed**.
- `pytest -q test_results_digest_matrix_campaigns.py test_switch_matrix_digest.py test_vector_scalar_gap_reporting.py test_repaired_matrix_contract.py` — **46 passed**.
- `python tools/matrix_guard.py` — completed using the S1 current-contract snapshot for this report.
- `python tools/run_full_matrix_vec_approx.py --out-dir data/reports/full_trb_matrix_vec_approx_20260801T1430Z` — completed; 432,176 rows written (12,103 INERT; 420,073 DATA_UNAVAILABLE).
- Deadline receipt/config assertions — PASS: 20 keys present, strict inequalities hold, active hash matches receipt.

## Report synchronization
- S1 authority receipt: `data/reports/S1_ENGINE_SNAPSHOT_20260801.json`.
- Core S1 report artifacts pass timestamp/size synchronization; see `data/reports/S1_REPORT_SYNC_RECEIPT_20260801.json`.

## Interpretation
S1 is reachable and is the authority for exact ENGINE status. The current repaired-contract store is active and populated, but exact differential coverage is still sparse for most pilots. Amber/vector rows are useful provisional research and fill monitoring only; they are not exact V8 proof and do not receive exact credit. The 20 deadline rows remain provisional unless their exact activity, B&H, and risk receipts pass the promotion contract.
