# HANDOFF 3971 FULL FILL — 2026-09-27 22:00

## Goal Tonight
- FULL 4864 F + L:BI yellows for 20 sheets (5 per cat_side) — EVERY number
- `CRYPTO_LONG` BTC/ETH/SOL/ZEC/AAVE, `CRYPTO_SHORT` same 5, `STOCKS_LONG` AAPL/TSLA/NVDA/MSFT/GOOGL, `STOCKS_SHORT` same 5
- THEN new baselines, discard rows

## Current State
- `ZECUSDC_LONG` 433/4864 F 894/4864 — MAC 96673 timeout 600 — NPZ 945 arrays 1797 bars HOT — YELLOW_TIMEOUT 1.0 no -1 — S1 OK3 5.2G free
- Template 4864 rows (285+527+248+... ) — pilot was exiting at 308 due to max_loops 36k + done<1000 fallback missing — fixed 108k + F_filled check
- HERDS FORBIDDEN — only ZEC until 4864, S1 herds killed (888634 etc) — S1 empty

## Next
1. ZEC complete xlsx + charts (browser.mjs file:// offline 62vh Chart.js 4.4.1 + hammer)
2. 4 parallel S1 pilots (BTC/ETH x LONG/SHORT) + Mac ZEC = 5 concurrent FULL 4864
3. 365D + live + slippage 1.5x before new baselines

## If Fail
- Progress `data/reports/lifecycle_pilot/ZECUSDC_LONG_v14_progress.json` done 433 cum 5.54 — resume, never delete
- Workbook `/SPREADSHEETS/V15_V16_CELL_BY_CELL/ZECUSDC_LONG_30d_matrix.xlsx` 2.3M 21 sheets — _atomic_save pid+tid tmp
- Logs `/tmp/v15_*.log` — spec-loop guard 500
- Re-run `timeout 3600 .venv/bin/python -u v15_pilot.py --sym-side ZECUSDC_LONG --window-days 30 --vector-only --allow-mac`
