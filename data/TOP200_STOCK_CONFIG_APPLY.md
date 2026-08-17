# TOP200 Stock Config — Apply List

Generated 2026-04-15 from merged TOP1000_SWEEP_RESULTS.xlsx after S1 archive ingest (256 tradier CSVs, 87,337 raw rows, 5,336 w/ trades>0 kept, 315 S1 rows in final TOP1000).

## Overview

Two distinct regimes exist in TOP1000:

1. **Pre-existing Local "tradier_t2 fast" (Rank 1–685)** — Sharpe 17.3, PnL $70, **only 111 trades over 8 symbols**. Small-N; likely pre-variance-fix. NOT applied.
2. **S1 real sweeps (Rank 686+)** — lower Sharpe but statistically robust. Rank 686–689 all agree: Sharpe 4.23, PnL $177.70, **20,605 trades** over 8 symbols. APPLY-CANDIDATE.

## #1 Recommended Config (S1 top, robust)

From `v8_sweep_tradier_t4_8sym_20260408_0115.csv`, runs 00000/00001 (Sh 4.23, 20605 trades):

| Key | Value | Live value (config_tradier.py) | Action |
|-----|-------|-------------------------------|--------|
| `TRADIER_DC_POSITION_ENTRY_THRESHOLD` | **0.15** | 0.25 (L828) | UPDATE |
| `TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER` | **35** | 80 (L831) | UPDATE |
| `TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER` | **65** | 20 (L832) | UPDATE |
| `TRADIER_WT_EXIT_TFS_TRADIER` | **5m+15m+1h+4h+D** | "5m+15m+1h+4h+D" (L858) | UNCHANGED |
| `TRADIER_WT_EXIT_MIN_TFS_TRADIER` | **4** | 4 (L859) | UNCHANGED |
| `TRADIER_WT_COMPOSITE_SCORING_ENABLED_TRADIER` | True | True (L857) | UNCHANGED |
| `TRADIER_DC_DAYTRADE_ENABLED` | True | True (L823) | UNCHANGED |
| `TRADIER_MI_EXIT_ENABLED_TRADIER` | True (1) | False (L819) | UPDATE |
| `TRADIER_MI_ENTRY_ENABLED_TRADIER` | False (0) for run_00001 / True for run_00000 | False (L818) | UNCHANGED (sweep tie) |

Also mirrored in non-TRADIER-prefixed counterparts (same file):
- `DC_POSITION_ENTRY_THRESHOLD` L535: 0.25 → **0.15**
- `K_ZONE_LONG_THRESHOLD_TRADIER` L406: 80 → **35**
- `K_ZONE_SHORT_THRESHOLD_TRADIER` L407: 20 → **65**
- `MI_EXIT_ENABLED_TRADIER` L631: False → **True**
- `WT_EXIT_MIN_TFS_TRADIER` L746: 5 → **4**

## Safety holds (NOT applied per CLAUDE.md)

Values that sweep might "want" but CLAUDE.md forbids changing:
- `HARD_STOP_LOSS_MAX_PAIN` — must stay DISABLED
- `STALE_DATA_PROFIT_SHIELD`, `STALE_DATA_HARD_STOP`, `STALE_DATA_GAIN_EROSION` — DISABLED
- `ATR_TRAIL_ENABLED` — False (stock #1 PnL destroyer)
- Any `gain < -X` stop-loss path in tradier_manage.py
- `MIN_GAIN_TO_BUY_AGGRESSIVELY` — 3.0% floor

## tradier_manage.py switches

No entry/exit-evaluate-function switches in the top S1 config require flipping beyond what's already on. The recommended config's behavior changes come entirely from `config_tradier.py` values above (gates in the existing evaluate functions). **No tradier_manage.py edits needed for this apply.**

## Top 10 (overall, with caveats)

| Rank | Sharpe | PnL | Trades | Tier | Notes |
|------|--------|-----|--------|------|-------|
| 1-9  | 17.309 | $70.18 | 111 | tradier_t2 fast | Pre-existing Local, thin 111 trades — NOT used |
| 10   | 17.309 | $70.18 | 111 | tradier_t2 fast | same |
| ...  | ...    | ...   | ...    | ... | ... |
| 686  | 4.230  | $177.70 | 20605 | S1 t4 | **APPLIED** |
| 687  | 4.230  | $177.70 | 20605 | S1 t4 | same config, MI_ENT toggled |
| 690  | 3.892  | $153.60 | 20833 | S1 t4 | same but WT_COMP=False |
| 694  | 1.593  | $50.69 | 9824 | S1 t4 | K_L=50/K_S=50 |

## Source files

- Merged xlsx: `/Users/niels/Documents/binance/data/TOP1000_SWEEP_RESULTS.xlsx`
- S1 archive incoming: `/Users/niels/Documents/binance/data/sweep_results_incoming_s1_tradier/`
- Backup pre-merge: `/Users/niels/Documents/binance/backups/TOP1000_SWEEP_RESULTS_before_s1_merge_202604152153.xlsx`
