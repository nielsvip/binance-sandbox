# DC Sweep Retrospective — 2026-09-26

## Request
- Start from `SPREADSHEETS/BEST/` global + per-`sym_side` cumulative overrides as baseline, with `DAYTRADE_STOP/DAYTRADE_TARGET/TECHNICAL_EXIT` DC levels `dc_3/5m_low/high` `→3m` / `dc_15m_low/high` / `dc_1h_low/high` `-0.25%` stop / `-0.10%` target, using latest 30d NPZs on S1.
- Test **every** DC entry/exit option on **every** of 354 `sym_side`, keep **only best entry TF + best exit TF per `sym_side`**, define new defaults per `cat_side` (`CRYPTO_LONG/SHORT`, `STOCKS_LONG/SHORT`) for new templates.
- Charts: Chart.js zoomable like `AAPL_SHORT_30d_matrix_30D_REAL_ZOOMABLE.html` (hammer + zoom, dark). XLSX: like `BEST/CRYPTO_SHORT/XRPUSDC_SHORT_bhm31p24_gainm23p17_30d_matrix.xlsx` — fully filled `F/G/H` + `L:BI` yellows, `bh/gain/delta` in filename. Live on S1 `~/binance` + `~/binance-sandbox` and Mac `~/Documents/binance` + `~/binance`.

## What Was Built
- `v12_quick_engine.py` `QuickConfig` `DAYTRADE_DC_STOP_TF/TARGET_TF` + `TECHNICAL_DC_STOP_TF/TARGET_TF` `OFF/3m/15m/1h` (`5m→3m`) with `0.25`/`0.10` buffers and `DC_DAYTRADE_*` legacy aliases (`dc_low/high_15m` vectorizable).
- `tools/v15_quick_dc_sweep.py` 6-variant probe vs `cumulative_before` (naked + yellows) → `data/reports/v15_quick_dc_4x16.json` (XLM `+3.89pp`, 1000BONK `+47pp`).
- `BEST_DC_NEW` generation via `v15_pilot_v01.write_zoomable_chart` (Chart.js) + xlsx copy-patch; `per_sym_active_config*.json` promoted `209` live; S1 disk `100%→6.2G` via `klines_cache` symlink.

## What Went Wrong
1. **Scope too narrow, not per-sym:** Quick 6-variant used a single `cumulative_before` (BEST `GLOBAL_RISK_GATES` final `C`) instead of testing **every** `OFF/3m/15m/1h` on **every** `sym_side` and keeping best per `sym_side`. Result: one global delta, not 354 bests.
2. **TFs not in NPZ:** Tested `3m/5m` (`5m→3m`) but `backtest_v8/indicators/*.npz` has no `dc_low_3m/5m` (only `15m/1h/4h/D`). Those variants silently `OFF` (synthetic `3m` from `15m`) and wasted `81→256` combos. Correct set per your last correction: **`OFF/15m/1h/4h` without `1/3/5m`** (`3 TFs → 4 TFs`).
3. **Baseline wrong:** Used generic `QuickConfig` (`WT_15M_BOUNCE_OPEN`) instead of **afternoon's best per `sym_side` overrides** from `SPREADSHEETS/BEST/` + `lifecycle_pilot/*_v14_progress.json` + `per_sym_active_config.json`. Takes `~30s` per `sym_side` to load that baseline — we skipped it, so deltas vs `cumulative_before` were off.
4. **XLSX empty:** `BEST_DC_NEW` was `shutil.copy2(TEMPLATE) + E2=30.04` (`1.9M` `24` sheets, `STDEV max_row 3` `F3 None`) vs real `BEST/XRPUSDC_SHORT` `901K` `23` sheets `max_row 214` with `F/G/H` + `L:BI` per-row. We did not run `v15_pilot` cell-by-cell; user saw `EMPTY` with no numbers.
5. **Exit reason not specific:** `DAYTRADE_STOP`/`TECHNICAL_EXIT` were generic (`TECHNICAL_EXIT` at `22263`, `DAYTRADE_DC_STOP_15m` at `22180`) not `DAYTRADE_STOP dc_15m_low / TECHNICAL_EXIT dc_15m_low` as requested for best-TF provenance.
6. **Infra:** S1 `100%` `No space left` truncated `225KB` BadZip via `_atomic_save`; S2 `niels@10.0.0.4` lacked `openpyxl`/`numpy` (`python3.10` vs `3.12`) and needed `~/.local` sync; `s2-int` alias missing (is `10.0.0.4`); `stale_npz_48h.txt` corrupted with `S3` variants (`265` real stale).
7. **354 handling:** `4×16` `4 sym_sides at a time 16 workers` OOM guard killed pilots; `ZECUSDC` debate (`S2` except ZEC vs include) and `XRP` vs `354` split; sync via `tools/sync_s1_to_mac.sh` missing (excludes `TEMPLATE`) so `BEST_DC_NEW` never landed in `SPREADSHEETS/` on Mac.

## Fixes Applied
- `v12_quick_engine.py:5096` `DAYTRAD E/TECHNICAL_DC_*_TF` `OFF/15m/1h/4h` (removed `3m/5m`), `22180/22195` now `f'DAYTRADE_STOP dc_{tf}_low/high'` / `f'DAYTRADE_TARGET dc_{tf}_high/low'`, `22006` added `_tech_*_dc` mirrors, `22272` `TECHNICAL_EXIT dc_{tf}_low/high` specific; compiled `py_compile` Mac+S1; `backups/before_exit_reason_specific_20260926.py`; `tests/test_exit_reason_dc_specific.py` `3 passed`.
- `per_sym_active_config*.json` now stores `DAYTRADE_DC_*` + `TECHNICAL_DC_*` per `sym_side` with `dc_best_gain/delta`; `data/reports/new_template_defaults*.json` aggregates most-common best per `cat_side` for new `TEMPLATE_CRYPTO/STOCKS_LONG/SHORT.xlsx`.
- One verified pair `1000BONKUSDC_LONG_bh15p46_gain30p04_30d` (`208K` html Chart.js `hammer+zoom` `2881` bars `151` trades, `1.9M` xlsx) shown via headless `browser.mjs` `200` `canvas true` `1.0M` screenshot — proves `write_zoomable_chart` path.

## Current State
- Running on `s1-int` `~/binance-sandbox`: `dc_sweep_per_sym.py` → `dc_afternoon_sweep.py` (afternoon best baseline, `OFF/15m/1h/4h` `256` combos per `sym_side` `16` workers, `~0.07s` each, `~30s` baseline load per `sym_side`). Previous `gen_all` (`354` `4` workers `1000BONK` `2/354`) killed; disk `12G/97%` (S2 `256G/12%`); live `254` crypto + `249` stocks already synced to `s1-int:~/binance/` and `Mac ~/Documents/binance/data/hourly_reconfig/` + `~/binance/`.
- Pending: finish `354` per-`sym_side` `OFF/15m/1h/4h` sweep (`~60min` on S1) → `data/reports/dc_afternoon_sweep.json` → `per_sym_active_config` live → new `TEMPLATE_*` defaults per `cat_side`; then `v15_pilot.py --sym-side` full `13`-sheet fill for `BEST_DC_NEW` (`E2` numeric, `F/G/H` + `L:BI` yellows) and `rsync -az s1-int:~/binance-sandbox/SPREADSHEETS/BEST_DC_NEW/` → `Mac SPREADSHEETS/BEST_DC_NEW/`.

## Risks
- `30d` single-regime `+40pp` deltas overfit without `1y` walk-forward + slippage `1.5-2×` plateau check; `dc_3m` synthetic may bias; S1 `8-12G` still tight for `362` `710-990K` html.

## Files
- `v12_quick_engine.py:5096,22006,22180,22272` `tests/test_exit_reason_dc_specific.py` `tools/v15_quick_dc_sweep.py` `SPREADSHEETS/BEST_DC_NEW/CRYPTO_LONG/1000BONKUSDC_LONG_bh15p46_gain30p04_30d_30D_REAL_ZOOMABLE.html` `SPREADSHEETS/BEST/CRYPTO_SHORT/XRPUSDC_SHORT_bhm31p24_gainm23p17_30d_matrix.xlsx`
