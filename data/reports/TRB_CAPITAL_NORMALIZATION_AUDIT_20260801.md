# TRB $2,000 Capital-Normalization Audit — 2026-08-01

## Live matrix result

Only **LAC_SHORT** and **VT_LONG** remain as live matrix entries after the deployed-capital and activity gates:

| key | strategy gain/mo ($2k normalized) | B&H gain/mo ($2k) | delta | trades | real closes | average deployed | strategy DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| LAC_SHORT | 2.4248% | 2.0770% | +0.3478 pp | 57 | 56 | $8,361.28 | 24.1677% |
| VT_LONG | 2.8412% | 1.5014% | +1.3398 pp | 39 | 38 | $4,699.14 | 5.0670% |

The receipt proves `capital_normalization_factor = 2000 / average_deployed` and `normalized_pnl = raw_pnl * factor`.

## Revoked matrix promotions

The following had only 1–3 trades/closes and were removed from `data/hourly_reconfig/trb/active_config.json` with a timestamped backup:

`MU_LONG`, `GM_LONG`, `ACN_SHORT`, `NVDA_LONG`, `PLTR_SHORT`.

## Promotion policy now enforced

- Strategy results must carry a complete raw-P&L → average-deployed → $2,000 receipt.
- Strategy gain/month and delta versus B&H are read only from that normalized receipt.
- Promotion requires strictly more than 10 trades and 10 real closes.
- A vector/capture result without deployed-capital telemetry is research-only and cannot fill a live slot.
- Deadline promotion fails closed unless 60 independently eligible symbol/sides exist.

## Canonical workbook status

`SWITCH_MATRIX_TRB.csv.gz` / `.xlsx` are currently quarantined for decision-making: the workbook audit reports 2,193 unexplained numeric CSV cells and 8,552 historical workbook values. This coding session's SSH sandbox cannot run the S1 pull, but the normal Mac launch agent has a transactional S1 report-sync path. Do not use stale cells to rank or promote; use the live receipt results above until the next S1-backed rebuild passes the workbook audit.
