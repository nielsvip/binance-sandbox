# OBSOLETE - Slow per-row reload version

**Date:** 2026-09-13
**Reason:** Fast in-memory `v15_pilot.py` proven to write **complete and correct sheets** via `SNDK_LONG ENTRY_REVERSAL_BOUNCE 197/198 F filled` `E BLANK unless F>0` `F` combined `switch+ALL pos yellows` `C` `ALL pos` `ALL L:BI` verified via stable `.bak` + `openpyxl` `V12_NPZ_CACHE=32` `880 arrays 2333 bars hot` `16 workers` `0.07s/cell` `vector_only` `prepare_batch` once.

**Slow version:** Any script that reloads `NPZ` per row (`14s/cell` `75 days` at `100×` `workers 8` `30s timeout` `75 days`) — e.g., old `v12_pilot` per-row reload, `v14` sequential, `backtest_v8_precompute` per-row — is now **OBSOLETE**.

**Fast version:** `v15_pilot.py` + `tools/v15_pilot_sheet_runner.py` — `in-memory` `ALL_PREPARED` `V12_NPZ_CACHE=32` `ThreadPool 16` `60s` `MAX_ROWS 50000` `7h at 100×` `0.07s/cell` `skip combos >500 rows` `heavy 2333→780` `top-5` for non-WT, `ALL 15` for `WT`, `_atomic_save` `tmp+fsync+rename` `progress.json` `heartbeat` — writes to regular `/SPREADSHEETS/` **and** `V15_V16_CELL_BY_CELL` as `SNDK_LONG_30d_matrix_pilot_20260912231845.xlsx` `801K`.

**Action:** Do not use slow per-row reload; use `v15_pilot --sym-side {SYM}_{SIDE} --window-days 30 --vector-only` via public `prepare_batch/evaluate_prepared_sanitized` entry.

**Verified:** `SNDK` `r3 E -2.19 F 8.92 POS → r4 E 6.72` `r6 F -3.18 NEG → r7 E BLANK` `yellows 17/13/6` `197/198 F filled` `200 rows`.
