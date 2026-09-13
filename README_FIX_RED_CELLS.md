# V15 Red Cell Fix Guide — Dedicated Agent

**Purpose:** Every red-flagged `F col6` cell in `SPREADSHEETS/V15_V16_CELL_BY_CELL/*_matrix.xlsx` is a blocking cell that did not let the `E col5` cumulative sequence move on. The workbook keeps filling — red + MD never stops production. A dedicated agent fixes reds by making the live function have a fast vectorized equivalent within the per-cell time limit.

## Time Limits (hard, never wait)

- `7d` (and `1d` proxy): **0.5s per cell** — entire `900-row` workbook `<10min`
- `30d`: **1.0s per cell** — entire workbook `<15min`

`v15_pilot.py` enforces `per_cell_timeout_sec = 0.5 if window in (1,7) else 1.0` and on timeout flags `FF0000` red + `FFFFFF` white bold + writes MD and **moves on** — never sheet wait.

## MDs for Agent (no interruption)

- Per-run: `data/reports/v15_flags/{SYM}_{window}d_flags.md` — auto-created at run start (old `.bak` kept). Each flagged cell appends one row:
  ```
  | Sheet | Row | Switch | Cand | Reason | Delta | VecGain | CumBefore | LiveFunc | VectorPath | Budget |
  ```
- This file: `README_FIX_RED_CELLS.md` — how to fix.

## How to Fix One Red Row

1. **Read MD row:** e.g. `| ENTRY_REVERSAL_BOUNCE | 5 | BB_SQUEEZE_ENTRY_ENABLED | True | NEG delta<=0 blocks | -1.49 | 2.18 | 3.67 |`
2. **Find live function:** `grep -rn "BB_SQUEEZE" tradier_manage.py backtest_v12_engine.py` — live path is `tradier_manage.check_entry_candidates()` → `backtest_v12_engine.process_position()` → `v12_quick_engine.QuickConfig.{SWITCH}`
3. **Find vectorized equivalent:** `grep -rn "BB_SQUEEZE" v12_quick_engine.py tools/opt/evaluate_v12.py` — vector path is `v12_quick_engine.QuickConfig` + `compute_regime_*` / `evaluate_prepared_sanitized` called via `tools/opt/v12_pilot.prepare_batch` + `evaluate_prepared_sanitized` in `v15_pilot` (workers 16).
4. **Ensure same result within budget:**
   - Vector must return same `gain_pct/trades/sharpe` as live `backtest_v12_engine` for that `window` (7d 486 bars / 30d 2333 bars) — use `tools/opt/evaluate_v12.prepare` + `evaluate_prepared` vs `backtest_v12_engine` replay.
   - Must be `<0.5s` (7d) / `<1.0s` (30d) per cell: `candidates = 1 + 2 yellows (3 cands)` for fast window (limit 2), `workers 16`, `V12_NPZ_CACHE=32` hot, no per-row `load_workbook` (only `wb_keep` open per sheet).
   - If live has no vector (e.g. new `STDEV_SLOPE_SIZING` qty), add to `v12_quick_engine.QuickConfig` field, wire `config_tradier.TradierConfig` default, `get_defaults_for_symside()`, and `compute_regime_sizing_mult` — then `sanitize_overrides` will map.
5. **Verify:** `python3 -m pytest tests/test_v15_pilot.py -k vector` or `tools/opt/evaluate_v12.prepare` for `AAPL_LONG`/`SNDK_LONG` 7d/30d — must be `valid True` and `delta` matches `vg - cum`.

## Live → Vector Mapping (current)

- `tradier_manage.py:check_entry_candidates` + `check_exit_candidates` + `process_position` (SACRED) ↔ `backtest_v12_engine.py` (parity) ↔ `v12_quick_engine.py:QuickConfig` (3337 fields) ↔ `tools/opt/evaluate_v12.py:prepare` + `tools/opt/v12_pilot:evaluate_prepared_sanitized` (vector, `ThreadPool 16`)
- `config.py` / `config_tradier.py:TradierConfig` → `v15_pilot.get_defaults_for_symside()` → `QuickConfig`

## Example Fix

Red: `STDEV_SLOPE_SIZING 3 ENABLED True delta -0.5 NEG`
Live: `v12_quick_engine.compute_regime_sizing_mult` was `1.0` dummy → Vector: make it `max_map * edge * slope * fav * clamp` with `START_SIZE` → `qty` → `gain` distinct per `D_MAX`, and `<0.5s`.

## Production

- Workbooks: `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_{window}d_matrix.xlsx` — `F` in memory per sheet, `_atomic_save` per sheet flush, red `FF0000` on block, never per-row wait.
- Progress: `data/reports/lifecycle_pilot/{SYM}_{window}d_progress.json` `done` + `cumulative_gain` — source of truth, never lose.
- Flags: `data/reports/v15_flags/*.md` — agent fixes reds, production keeps moving to `sheet 13` + `write_zoomable_chart` even with reds.

**Agent loop:** `while read MD row; do fix live↔vector; pytest; done` — never `pkill -f v15_pilot` for fix, only dedicated agent touches flagged switches.
