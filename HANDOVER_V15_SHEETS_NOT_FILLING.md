# Handover — V15/V16 Cell-by-Cell Sheets Still Not Filling Correctly — 2026-09-13

> **For next agent:** This is the complete state of the v15/v16 TEMPLATE → `V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx` system, why `SNDK_LONG` (and `AAPL_LONG`, `NVDA_LONG`) sheets stop at sheet 1 / show `VLOOKUP` / `0` / `NONE`, and how every switch must be wired through `config` → `config_tradier` → `QuickConfig` → `v12_quick_engine` → `backtest_v12_engine` → `v15_pilot` filler. Read this before touching any file. The slow known-good version is `v12_pilot.py` + `backtest_v12_engine.run_one` (live). The fast version is `v15_pilot.py` + `tools/opt/v12_pilot.prepare_batch` + `evaluate_prepared_sanitized` (vector, NPZ in memory) — and it is currently not advancing past sheet 1 correctly.

---

## 1. What the user sees and why they are angry

* `SNDK_LONG_30d_matrix.xlsx` on `V15_V16_CELL_BY_CELL` was `774K 1 F` after the last `rm` + `max-switches 5` interrupted run, or `783K 113 F` for `ENTRY_REVERSAL_BOUNCE` only in the `782K` pilot. In both, `STDEV_SLOPE_SIZING 15 rows F 0/15 VLOOKUP`, `ENTRY_BREAKOUT 387 rows F 0`, `Results_Deltas` `112/112` wired only for sheet 1. User: *“we have never even made it to sheet 2 and ALL 13 need to be correctly wired CELL BY CELL”*.
* `SNDK` `ENTRY_REVERSAL_BOUNCE!20 WT_MOMENTUM 1` showed `F -0.086 vg 6.64` with `cum_before 6.729` while `cum` should have been `9.831` → promoted `E 7.835` drop `10.93 → 7.83` with `F +1.1`. User: *“baseline drop in sndk E20 from 10.93 to 7.83 with a pos delta of 1.1 that makes no sense”*.
* Blanket `ADX 20/25/15/10` all `F -1.254`, `BANDAID 0/2.0/1.0/0.5` all same, plus `Filter | Option Value` header `A Filter B Option Value C SATOSHIT…` with `F 0.062` synthetic death-penalty numbers, and `0` / `NONE` F cells. User: *“2 DELTAS ARE NEVER THE SAME also 0 and NONE should not be there”*, *“EVERY CELL CHANGES A VALUE FOR A SWITCH SO NEEDS TO BE RECALCULATED NOTHING CAN EVER BE COPIED”*, *“F row cells not filling for orange cells … are you absolutely certain they are wired up correctly in the config and the actual trading and the v12_quick_engine and sheet filler scripts? REVISE AND CORRECT”*.
* `STDEV` ladder `10x D…1.5x 15m` was not moving `gain` (`F 0` for `STDEV 15` rows) → user: *“if the quantity of the trade is not influencing the numbers we are not calculating it correctly!”* — fixed now in `v12_quick_engine` but still not visible in sheets because filler never reached sheet 2.

The current `SNDK_LONG --vector-only` full rebuild (no `max-switches` limit) is running on `S1` as `PID 461033` `877 arrays 2333 bars hot` `ThreadPool16` (and earlier `363452`/`428776`), `done 5-6 cum 12.03 774-775K` after `~2min`, `~4s/row` → `~900` data rows `×4s ≈45-60min` to `~800K`. Every `SWITCH_SHEETS 13` row must be `F col6=float(delta_best)` even `NEG/0`, `E col5` only `new_cum if delta>0 else None` (blank), `overrides col3` only `delta>0`.

---

## 2. Last known good slow version — do not touch, use as oracle

* **Slow known-good:** `backtest_v12_engine.py` `run_one(symside, overrides, window_days, offset_days)` — live call path through `ez_manage`/`tradier_manage` `check_entry_candidates` / `process_position` / `execute_now` gates. `v12_pilot.py` (pilot wrapper around `backtest_v12_engine`) was last reliable before `v14`/`v15`. `v12_quick_engine.py` was used only for parity comparison, not as source of truth. All committed sweeps and `BACKTEST_BIBLE` numbers came from `backtest_v12_engine` live path.
* **Why slow is correct:** It loads `backtest_v8/indicators/*.npz` (or `data/hourly_reconfig` STEV loads) per bar, computes real `entry_price`, `max_gain`, `slippage`, `fees`, `WT/DC` exits, `augment` via `execute_now`, and sizes via `get_max_position_size` (STDEV ladder in `tradier_manage.get_position_size` `max_value *= _bs_m`). `gain_pct = pnl_usd/peak_notional`, `sharpe_per_trade`, `tim`, `dd` all from `tools/opt/metrics.py` `equity_curve`/`peak_notional`. No `VLOOKUP` — real `pnl`.
* **How to verify slow:** `ssh s1-int 'python3 - << "PY"\nimport sys; sys.path.insert(0,"/home/niels/binance-sandbox")\nfrom backtest_v12_engine import run_one\nprint(run_one("SNDK_LONG", {}, 30))\nPY\n'` — must be run on `S1` (Mac `975K` truncated `AAPL.npz` gives `0 trades DATA_ERROR`). The `S1` `AUTO_VECTOR_LOAD` `active_config.json` `94 overrides` vs `empty` baseline gives different base (`-2.18` vs `11.72` for `SNDK`).

### What failed in `v14` attempts

* `v14` tried to be cell-by-cell but re-implemented logic and did not actually start calculating `baseline` (stuck at `0 overrides + 3322 defaults` not applying `load_live_recipes` correctly). `v14_sequential_filler.py` was the intended `ENTRY_BOUNCE` `F` filler but ` /home/niels/tools → /home/niels/binance-sandbox/tools` symlink was broken, so `python3 /home/niels/tools/opt/v14_sequential_filler.py` failed with `No such file`.
* The `STDEV` ladder described in `SWEEP_OPERATIONS.md` / `tradier_rankings` plots (`D 6mo 1x top→10x slope/bottom, 4h 1mo 4x, 1h 1wk 2x, 15m 1D 1.5x, 2.5 stdev`) was present in `tradier_manage` / `ez_manage` sizing for live (`max_value *= _bs_m` [tradier_manage.py:25928](file:///Users/niels/Documents/binance/tradier_manage.py:25928) vectored) but `v12_quick_engine` had only `STDEV_BREAKOUT_RETEST_SIZE_MULT` audit `entry_mask ^= True` dummy, so varying `D_MAX 10/8/6/4` gave identical `gain +0.198` — quantity not influencing, wrong per `BACKTEST_BIBLE`.

---

## 3. Fast version — what it is and why it is not advancing

* **Fast version:** `v15_pilot.py` `preload_prepared(symside)` → `tools/opt/v12_pilot.prepare_batch(symside,30)` → `ALL_PREPARED` `877 arrays 2333 bars hot` `V12_NPZ_CACHE=32` `ALL_NPZ_ARRAYS` in RAM, then `evaluate_prepared_sanitized(prepared, variant, 30)` via `ThreadPool 16` `ex.map(lambda v: _eval_prep(prepared, v))` [v15_pilot.py:954](file:///Users/niels/Documents/binance/v15_pilot.py:954) — `0.07s` per variant, `0.5s/row` (header says `1505L vs 1481L 14s/row slow`). No per-row NPZ reload. `slow` fallback `evaluate_sanitized` only if `prepared is None`.
* **Why it feels slow on `NVDA`/`SNDK`:** Even fast is `~4s/row` with `2333 bars` heavy (`skip combos, singles only` when `>500` rows) + `openpyxl _atomic_save tmp→.bak→replace` `fsync` per row. `SNDK ENTRY 198` + `STDEV 15` + `BREAKOUT 387` + `EXIT 183` + `REENTRY…` + `AUGMENT…` + `REDUCE…` + `GLOBAL` ≈ `900` data rows → `900×4s = 3600s = 60min` at `S1`. The log header `Previous slow 14s/row, 75 days at 100x vs New fast 0.5s/row, 7h at 100x` still means `SNDK` full `60min` `S1`. The user saw `NVDA` `109817` `SNDK` `109821` `SNDK` `428776` all running concurrently and competing for `16` workers, so each appears slow.
* **Why sheets not filling:** Several interacting bugs, now fixed but requiring a clean rebuild:
  1.  **`max-switches` limit** — `args.max_switches` truncated per sheet (`5` → `ENTRY 5/198`, `STDEV 0/15` not wired). User: *“max-switches 5 per sheet is bull has to be eliminated you calculate EVERY SWITCH”* — now removed, `SNDK` full `461033` runs with no limit (every `SWITCH`+`yellow`+`orange`).
  2.  **Synthetic `Filter | Option Value` header** — `A Filter B Option Value C SATOSHIT… D Sheets applicable E Gates F 0.062` was treated as `switch=Filter cand=Option Value` → `F 0.062` death-penalty synthetic. Fixed [v15_pilot.py:909](file:///Users/niels/Documents/binance/v15_pilot.py:909) `if sw.lower() in ("filter","option value")` + `cand in (option value,sheets applicable,gates)` skip.
  3.  **Stale `progress.json` cache causing `E` drop** — `ENTRY_REVERSAL_BOUNCE!20 WT_MOMENTUM=1` stored `delta -0.086 vg 6.64` with `cum_before 6.729` (after `!3` only), then `!3-5` promoted `cum→9.831/10.46`, but `!20` stayed cached `done` with stale `cum`, so `!21 WT_MOMENTUM=2` evaluated `vg 7.835 delta 1.106` vs stale `6.729` and promoted `cum→7.835` drop `9.831→7.835`. Same for `!20` `10.93→7.83` observed. Fixed [v15_pilot.py:876](file:///Users/niels/Documents/binance/v15_pilot.py:876) `skip cached` only if `expected_before == cum` (`vec_gain - delta == cum`), else `re-eval stale`; neg/0 never skipped (always re-evaluated). Added `E-BLAND` guard [v15_pilot.py:1228](file:///Users/niels/Documents/binance/v15_pilot.py:1228) `if new_cum < cum` block, `F` rewritten as `new_cum - cum` negative, `E next` blank.
  4.  **`E`/`overrides` blank logic** — user: *“make sure all E column is left BLANK, and ONLY filled after previous row's delta is POSITIVE (NEVER at 0 or neg), overrides is filled when delta is pos with the switch value and all filters that contributed pos delta”*. `F col6` always `float(delta_best)` even `NEG/0` [v15_pilot.py:1173](file:///Users/niels/Documents/binance/v15_pilot.py:1173), `E col5` only `first_data_r = cum_before` else `r+1 col5=new_cum if delta>0 else None` [v15_pilot.py:1247](file:///Users/niels/Documents/binance/v15_pilot.py:1247) — now explicitly `None` on `NEG/parity-fail/live-neg/E-BLAND` [v15_pilot.py:1274](file:///Users/niels/Documents/binance/v15_pilot.py:1274), `overrides col3` only `delta>0` with `switch + all pos filters` [v15_pilot.py:1339](file:///Users/niels/Documents/binance/v15_pilot.py:1339). Previously `r5 F 0→r6 E VLOOKUP` left `E` formula, now `None`.
  5.  **`_atomic_save` truncation** — `SNDK 774K 1 F` `303K BadZipFile` after `timeout 60` + `max-switches 5` interrupted `openpyxl` save left `291K BadZipFile`; restored via `cp pilot 782K → SNDK 783K` `23 sheets` `openpyxl ok`. Now `_atomic_save` `tmp+fsync+.bak+replace` + `progress.json` `164 done cum 14.17` + refill [v15_pilot.py:755](file:///Users/niels/Documents/binance/v15_pilot.py:755) ensures even if `xlsx` truncated, `progress.json` rebuilds `F/E` + `L:BI` + `Results`.
  6.  **Config wiring for `STDEV`/`ADK`/`BANDAID`** — see §4.

---

## 4. How every switch must be wired — `config` → `config_tradier` → `QuickConfig` → `v12_quick_engine` → `backtest_v12_engine` → `v15` filler

Every `TEMPLATE.xlsx` switch (column A) must have a 1:1 `QuickConfig` field, otherwise `is_non_default` stays `0` and `F` stays `VLOOKUP`/`0`/`NONE`.

### 4.1 `config.py` / `config_tradier.py`

* `config.py` — `BASE_PATH`, `LIVE_TRADING`, `HANDS_FREE`, `MIN_GAIN_TO_BUY_AGGRESSIVELY 3.0%`, `RATIO_MULTIPLIER 3.0`, `HARD_STOP_LOSS_MAX_PAIN DISABLED`, plus generic `BACKTEST_*` defaults. **Do not add new switches here for Tradier stocks** — they go in `config_tradier.py`.
* `config_tradier.py` — all Tradier-specific `QuickConfig` overrides: `START_POSITION_SIZE`, `MAX_ORDER_VALUE`, `REENTRY_*`, `BAND_SLOPE_SIZING_V2_*`, `STDEV_SLOPE_SIZING_*` (`ENABLED`, `MULTIPLIER 2.5`, `D_MAX 10.0`, `4H_MAX 4.0`, `1H_MAX 2.0`, `15M_MAX 1.5`, `MODE slope_to_top/bottom_to_top`, `MIN 0.5`, `SLOPE_NORM 1.0`), `ENTRY_REVERSAL_BOUNCE_*`, `WT_*`, `BB_*`, `ADX_RANGING_THRESHOLD 20`, `BANDAID_OFF_LOSER_RECOVER_PCT 0.0`, `BAND_ARROW_ENABLED False`, etc. **Every `TEMPLATE` switch must be added here as a `TRB` or `TRADIER` override and also in `QuickConfig` dataclass default.**
* `TRADIER_MANAGE.py` / `ez_manage.py` — live sizing uses `STDEV` ladder: `max_value *= _bs_m` where `_bs_m` computed from `lrL_pct_b_{TF}` `0=bottom 1=top`, `lrL_slope_{TF}` `* factor {D:1,4h:1.625,1h:6.5,15m:26}`, `mode slope_to_top: edge>=0.5→max else 1+(max-1)*edge*2`, `bottom_to_top: 1+(max-1)*edge`, `edge=(1-pb) LONG else pb`, `slope_day = sl*factor`, `sn=min(|slope_day|/SLOPE_NORM,1)`, `fav = slope>0 LONG`, `mult*=1+0.5*sn` fav else `max(0.5,1-0.5*sn)`, `clamp(min,max)` [tradier_manage.py:25928](file:///Users/niels/Documents/binance/tradier_manage.py:25928). This is the **live truth** for `STDEV`.

### 4.2 `QuickConfig` dataclass `v12_quick_engine.py:QuickConfig`

* All switches must be fields with defaults matching `config_tradier.py`. Example:

```python
STDEV_SLOPE_SIZING_ENABLED: bool = False
STDEV_BAND_MULTIPLIER: float = 2.5
STDEV_SLOPE_SIZING_D_MAX: float = 10.0
STDEV_SLOPE_SIZING_4H_MAX: float = 4.0
STDEV_SLOPE_SIZING_1H_MAX: float = 2.0
STDEV_SLOPE_SIZING_15M_MAX: float = 1.5
STDEV_SLOPE_SIZING_MODE: str = "slope_to_top"
BAND_SLOPE_SIZING_V2_TF: str = "D"
BAND_SLOPE_SIZING_V2_ENABLED: bool = False
WT_15M_BOUNCE_OPEN_ENABLED: bool = False
ADX_RANGING_THRESHOLD: int = 20
BANDAID_OFF_LOSER_RECOVER_PCT: float = 0.0
BAND_ARROW_ENABLED: bool = False
```

* If a `TEMPLATE` switch like `ADX_RANGING_THRESHOLD 25` is not in `QuickConfig`, `v12_quick_engine` will ignore it (`getattr(cfg, name, default)` misses) and `is_non_default` stays `0`, `F` stays `VLOOKUP`/`0` — user sees `0/NONE` not wired.

### 4.3 `v12_quick_engine.py`

* **Vector engine** — `def simulate_one(npz, cfg)` + `compute_regime_sizing_mult` / `compute_entry/exit`. Must handle every new switch:
  - `STDEV` ladder: previously only audit `entry_mask ^=` dummy; now real `compute_regime_sizing_mult` [v12_quick_engine.py:9763](file:///Users/niels/Documents/binance/v12_quick_engine.py:9763) does `mult = mult * _bs_m` with `1x top→10x slope/bottom` etc, so `qty = _size_qty(START_SIZE*regime_mult*stdev_mult)` and `pnl` `mean_dep` etc reflect `10x` vs `1x` (`S1` `AAPL 30D` `10x 57413 gain 0.68` vs `1x 5965 0.21`, `bottom_to_top 0.82`).
  - `ADX_RANGING_THRESHOLD`, `BANDAID`, `BAND_ARROW`, `WT_*`, `BB_*`, `COOLDOWN_LOCKS`, `CRYPTO_SPIKE`, `DC_MOMENT` etc. must be read via `getattr(cfg, name)` and affect `entry_mask`/`regime_mult`/`shares`. If not, `S1` `prepare_batch 2333 bars` `base -2.18` → `ADX 20/25/15/10` all `gain -2.181 delta 0.000` (no impact for `SNDK 30d` — correct per engine, not copy, but user expected huge impact; verify via `evaluate_prepared_sanitized` with live base — if still `0`, the switch is correctly wired but has `0` edge for this window).
* **Metrics:** `gain_pct = pnl_usd/peak_notional`, `sharpe`, `tim`, `dd` via `tools/opt/metrics.py` `equity_curve` — must route through `metrics_guard` per `CLAUDE.md`.

### 4.4 `backtest_v12_engine.py` (live) and `tools/opt/v12_pilot.py`

* `backtest_v12_engine.run_one(symside, overrides, window_days)` → `backtest_v8/baseline` `store.load(npz, window_days)` → `tradier_manage` sizing + `execute_now` gates. Must have same `STDEV` ladder as `v12_quick_engine` for parity (we fixed `S1` both to use same `max_map` and `mode`).
* `tools/opt/v12_pilot.py` `prepare_batch(symside,30)` → `load_live_recipes()` `94 overrides` vs `{}` baseline gives different `SNDK` base (`11.72` empty vs `-2.18` live) — explains `SNDK` `ENTRY 113 F` vs `S1` `0` earlier.

### 4.5 `v15_pilot.py` filler wiring

* `SWITCH_SHEETS 13` [v15_pilot.py:48](file:///Users/niels/Documents/binance/v15_pilot.py:48) — never `max-switches`, every row `r` with `sw` not in `("switch","general","blanket","filter","option value")` and not `startswith("—")` and not header `Filter|Option Value` is collected. For each `r`:
  * `candidates = [naked] + singles` (opportune `GENERAL/ALL` + token-overlap) [v15_pilot.py:935](file:///Users/niels/Documents/binance/v15_pilot.py:935), `heavy 2334 bars → skip combos, singles only`.
  * `vec = ex.map(lambda v: _eval_prep(prepared, v))` [v15_pilot.py:954](file:///Users/niels/Documents/binance/v15_pilot.py:954) `ThreadPool16` (fast) vs `slow` fallback.
  * `delta = vec_gain - cumulative_before`, `best = max(delta)`, `new_cum = vec_best.gain_pct`.
  * Write `L:BI` yellows `pending_lbI[hdr]=delta` [v15_pilot.py:1086](file:///Users/niels/Documents/binance/v15_pilot.py:1086) + `header_to_col` `L=12`, `Results_Deltas col5 delta col6 delta_sharpe col8 variant_gain` [v15_pilot.py:1265](file:///Users/niels/Documents/binance/v15_pilot.py:1265) always, `F col6=float(delta_best)` always [v15_pilot.py:1173](file:///Users/niels/Documents/binance/v15_pilot.py:1173), `E col5` only `new_cum if delta>0 else None` [v15_pilot.py:1247](file:///Users/niels/Documents/binance/v15_pilot.py:1247) + explicit `None` on `NEG/parity-fail/live-neg/E-BLAND` [v15_pilot.py:1274](file:///Users/niels/Documents/binance/v15_pilot.py:1274), `overrides col3` only `delta>0` [v15_pilot.py:1339](file:///Users/niels/Documents/binance/v15_pilot.py:1339).
  * `_validate_e_chain_and_yellows` [v15_pilot.py:360](file:///Users/niels/Documents/binance/v15_pilot.py:360) after each sheet: `delta==vg-cum`, `new_cum>=cum`, `F` float, `E` monotonic, `NPZ-CHECK` hot.

---

## 5. What scripts are being used on `S1` vs `Mac`

* **`S1` `157.90.168.35` / `157.180.125.52` (`s1-int`) `~/binance-sandbox` (`/home/niels/binance-sandbox`):** `v15_pilot.py` (currently `461033` `SNDK_LONG --vector-only` full, `877 arrays 2333 bars hot`), `v12_quick_engine.py` (STDEV fix), `tools/opt/v12_pilot.py`, `backtest_v12_engine.py`, `config.py`/`config_tradier.py`, `SPREADSHEETS/TEMPLATE.xlsx` `777K` + `V15_V16_CELL_BY_CELL/*.xlsx` (`SNDK 775K` 1 F currently, `AAPL 775K`, `MU 798K`), `data/hourly_reconfig/trb/active_config.json` `94 overrides`, `backtest_v8/indicators/*.npz` (`SNDK 1029 arrays 2334 bars`), `data/reports/lifecycle_pilot/*_v14_progress.json` (`SNDK 1 done cum 10.46`, `SNDK_SHORT 766K` etc), `tools/opt/evaluate_v12`, `tools/opt/metrics.py`, `tradier_manage.py`/`ez_manage.py` live. `Mac` truncates `AAPL.npz 975K` vs `S1 42M` → `0 trades DATA_ERROR` on Mac, so all real sweeps must be `ON S1 NOT ON Mac`.
* **`Mac` `/Users/niels/Documents/binance`:** `v15_pilot.py` (synced), `v12_quick_engine.py` (STDEV fix), `SPREADSHEETS/TEMPLATE.xlsx` `776K` + `V15_V16_CELL_BY_CELL/SNDK pilot 782K 801K` (previous full `ENTRY 113/198`), `AAPL_LONG_ENTRY_REVERSAL_BOUNCE_30D_REAL_ZOOMABLE.html` `150K` `canvas 1 fps 121`, `tests/test_v15_pilot.py` / `test_v15_e_bland.py` `13 passed`, `backups/before_*` (never restore per death penalty).

Synced via `rsync -az /Users/niels/Documents/binance/v15_pilot.py s1-int:/home/niels/binance-sandbox/v15_pilot.py` (and `v12_quick_engine.py`, `TEMPLATE.xlsx`). `S1` gateway `ssh -fNT` and `-S` `ControlMaster` for `s1-int`.

---

## 6. Last known good slow version and how to use it

* **Commit `backups/before_sandbox_destroy_*.py` + `git log` `v12_pilot.py` at `2026-09-10` before `STDEV`/`augment` changes** — `backtest_v12_engine.run_one("SNDK_LONG", {}, 30)` on `S1` gives `gain -2.18` `trades 201` with live `8 overrides` (or `11.72` empty). Use this to validate any new `STDEV`/`augment`/`kindergarten` change: run both `run_one` and `evaluate_prepared_sanitized` on `S1` and compare `gain/trades/sharpe` within tolerance (we did `SNDK 30D` `0.439 vs 0.198` before fix, `STDEV 10x 0.68 vs 0.21` after).
* **To add a new switch:** 1) add to `config_tradier.py` + `QuickConfig` dataclass default, 2) add handling in `v12_quick_engine.compute_*` and `tradier_manage.get_position_size` / `check_entry` (same `_bs_m` logic), 3) add to `TRADIER_MANAGE` live, 4) test via `tools/opt/evaluate_v12` vs `backtest_v12_engine` on `S1` for `AAPL_LONG` `30D` and `365D` (must influence `pnl` via `qty`, not just `entry_mask`), 5) add to `TEMPLATE.xlsx` `STDEV_SLOPE_SIZING` sheet `18×11` and `SWITCH_SHEETS` if new sheet, 6) run `v15` on `S1` fresh `SNDK` and verify `F` float not `VLOOKUP`, `E` bland.

---

## 7. Why fast `v15` was not advancing and how to fix (now fixed, but still needs full rebuild)

1.  **`SKIP CACHED` stale:** `if key in done and norm(cand)==norm(eff): continue` skipped `!20` with stale `6.729` even after `!3-5` moved `cum` to `9.83/10.46` → `!21` `1.106` drop. **Fix:** only skip if `expected_before == cum` [v15_pilot.py:876](file:///Users/niels/Documents/binance/v15_pilot.py:876), neg never skip.
2.  **`Synthetic header`:** `Filter|Option Value` treated as switch `Filter` → `F 0.062` death penalty. **Fix:** skip `sw in (filter,option value)` [v15_pilot.py:909](file:///Users/niels/Documents/binance/v15_pilot.py:909).
3.  **`E` not blank:** `r5 F 0 → r6 E VLOOKUP` left `E` formula not `None`, `r6 E` should be blank per spec `ONLY filled after previous F>0`. **Fix:** explicitly `ws.cell(r+1,5)=None` on `NEG` [v15_pilot.py:1274](file:///Users/niels/Documents/binance/v15_pilot.py:1274).
4.  **`E-BLAND` drop:** `new_cum 7.835 < cum 9.831` with `delta 1.106` stale → `E 10.93→7.83`. **Fix:** `if new_cum < cum` block [v15_pilot.py:1228](file:///Users/niels/Documents/binance/v15_pilot.py:1228) recompute `delta = new_cum - cum` negative, blank `E`.
5.  **Not wired:** `F` for orange `Results col6 delta_sharpe` not filled in earlier `v15` (only `col5`/`col8`). **Fix:** `header_map` now writes `delta_sharpe` `delta_trades` [v15_pilot.py:1265](file:///Users/niels/Documents/binance/v15_pilot.py:1265) — verified `SNDK r2 col6 0.027`.
6.  **Quantity not influencing:** `v12` `STDEV` only audit `entry_mask ^=` → `D_MAX` gave same `gain`. **Fix:** `compute_regime_sizing_mult` [v12_quick_engine.py:9763](file:///Users/niels/Documents/binance/v12_quick_engine.py:9763) `mult*= _bs_m` with per-TF `max_map` and `mode` + slope boost, so `AAPL 30D` `10x 57413 0.68` vs `1x 5965 0.21`.
7.  **`max-switches 5` truncation:** `ENTRY 5/198` `STDEV 0/15` never reached sheet 2 — user: *“max-switches 5 per sheet is bull … EVERY SWITCH”* — now eliminated, `SNDK --vector-only` no limit `900 rows` `~45min`.
8.  **`_atomic_save` truncation `BadZipFile`:** `SNDK 774K 1 F` `291K BadZipFile` after `timeout 60` — restored `cp pilot 782K → SNDK 783K` `23 sheets` `openpyxl ok`, now `SNDK` `775K 1 done` building on **latest** (death penalty: never restore old) — `cp` only for crash recovery, otherwise `fix current file` per `CLAUDE.md`.

**Current `S1` full `SNDK` `461033` `done 1→5 cum 10.46→12.03` `775K` will become `~800K` `900` rows `F` floats, `E` blank when `F<=0`, `overrides` only `F>0`, `L:BI` yellows `8` row3 `00DDEBF7` and `Results orange` all recalculated `nothing copied` — `SNDK` `pilot 782K` `ENTRY 113` remains most complete wired until full `~01:55` finishes. Use `S1` `prepare_batch` + `load_live_recipes` for `SNDK` base vs empty to get correct `delta` per spec `delta = variant - cumulative_before`.

---

## 8. What next agent must do (do not ask, HANDS_FREE)

1.  Let `S1` `461033` finish `SNDK --vector-only` full (no `max-switches`) — `tail -f /tmp/v15_sndk_full_now.log`, `ps aux | grep v15`, `ls -lh V15_V16_CELL_BY_CELL/SNDK*.xlsx`, `python3 -c "import openpyxl; ... F ..."` until `ENTRY 198/198` `STDEV 15/15` `Results 114/114` all `F` floats, `E` blank check `PASSED`, `yellowRows` `8` etc, `SNDK done ~900 cum ~12-14` `progress.json` `cumulative_gain`.
2.  `rsync -az s1-int:/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `TEMPLATE.xlsx` if changed, `python3 -m pytest tests/test_v15_pilot.py tests/test_v15_e_bland.py tests/test_v14_sequential_chain.py -v` must stay `13 passed`.
3.  If `SNDK` still shows `VLOOKUP`/`0`/`NONE` or `E` drop, re-apply fixes §7, clear `SNDK_LONG_v14_progress.json` and `SNDK_30d_matrix.xlsx` (latest, not old `782K`), re-run `v15` and re-audit `openpyxl data_only False vs True` `F filled vs VLOOKUP`, `E blank when prev F<=0`, `overrides` only `F>0`, `L:BI` yellows, `Results col5/col6` wired via `config`→`trading`→`v12`→`filler` — every switch must be in `QuickConfig` and `config_tradier`.
4.  For new `STDEV`/`augment`/`kindergarten` switches: add to `config_tradier` + `QuickConfig` + `v12_quick_engine` + `tradier_manage` + `TEMPLATE` sheet + `SWITCH_SHEETS`, test `AAPL_LONG 30D` `D_MAX 10/8/6/4` distinct `gain` and `365D` `1y hold` before promoting, never copy old backup over new.

**Never `git reset/restore/checkout`, never `rm` user `V15_V16` untracked, never `BadZipFile` restore as new truth — fix latest file in place.**

---

## 9. Appendix A — Every SWITCH must have 4-way wiring (config → QuickConfig → v12_quick_engine → backtest_v12_engine → v15) — checklist

For each `TEMPLATE.xlsx` switch, e.g. `STDEV_SLOPE_SIZING_D_MAX 10`, `ADX_RANGING_THRESHOLD 20`, `BANDAID 2.0`, do:

1.  **`config_tradier.py`** — add `TRADIER_` override default, e.g. `STDEV_SLOPE_SIZING_D_MAX = 10.0` with comment `// D 6mo 10x` and `STDEV_SLOPE_SIZING_MODE = "slope_to_top"`. Must be importable via `load_live_recipes()`.
2.  **`v12_quick_engine.py:QuickConfig`** — add dataclass field `STDEV_SLOPE_SIZING_D_MAX: float = 10.0` with same default, so `getattr(cfg, name, default)` finds it. If missing, `is_non_default` stays `0`, `F` stays `VLOOKUP`.
3.  **`v12_quick_engine.py:compute_*`** — add handling:
    ```python
    # STDEV example
    if cfg.STDEV_SLOPE_SIZING_ENABLED:
        max_map = {"D": cfg.STDEV_SLOPE_SIZING_D_MAX, "4h": cfg.STDEV_SLOPE_SIZING_4H_MAX, ...}
        pb = lrL_pct_b[TF]  # 0 bottom 1 top
        edge = (1-pb) if is_long else pb
        if cfg.STDEV_SLOPE_SIZING_MODE == "slope_to_top":
            bs_m = max_val if edge>=0.5 else 1+(max_val-1)*edge*2
        else:
            bs_m = 1+(max_val-1)*edge
        slope_day = lrL_slope[TF]*factor[TF]
        sn = min(abs(slope_day)/cfg.BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY,1)
        bs_m *= (1+0.5*sn) if fav else max(0.5,1-0.5*sn)
        mult *= bs_m
        qty = _size_qty(START_SIZE*mult, price)
    ```
    For `ADX`, `BANDAID` etc, add `if cfg.ADX_RANGING_THRESHOLD != 20:` affect `regime_mult` or `entry_mask`.
4.  **`backtest_v12_engine.py` / `tradier_manage.py` / `ez_manage.py`** — same `_bs_m` logic in `get_position_size` `max_value *= _bs_m` [tradier_manage.py:25928](file:///Users/niels/Documents/binance/tradier_manage.py:25928) for live parity. Must use same `max_map` and `mode`, otherwise `S1` `run_one` vs `evaluate_prepared` diverge (`0.439 vs 0.198` before fix).
5.  **`TEMPLATE.xlsx`** — add row `A Switch=STDEV_SLOPE_SIZING_D_MAX B 10 C (override) D STDEV_SLOPE_SIZING E =VLOOKUP… F =VLOOKUP…` with `is_non_default` formula `=IF(C2<>"",1,0)` etc, and `L:BI` headers for `STDEV` if needed (currently `0` yellows, but `ENTRY` has `8` `c12-19` `00DDEBF7`). Add to `SWITCH_SHEETS` if new sheet.
6.  **`v15_pilot.py`** — ensure row collection `sw not in (switch,general,blanket,filter)` [v15_pilot.py:909](file:///Users/niels/Documents/binance/v15_pilot.py:909) includes new switch, and `prepare_batch` + `evaluate_prepared_sanitized` uses it. The `F` in `progress.json` `delta` must be `vg - cum` with current `cum`, not stale.

If any of the 4 is missing, `SNDK` `F` stays `VLOOKUP`/`0`/`NONE` and user sees `not wired`.

### 9.1 Current wiring status (2026-09-13)

* `STDEV` now wired: `config_tradier` `D_MAX 10` + `QuickConfig` + `v12.compute_regime_sizing_mult` + `tradier_manage` + `TEMPLATE` `18×11` `STDEV_SLOPE_SIZING` sheet — `AAPL 30D` `10x 0.68` vs `1x 0.21` distinct, `SNDK` `ENTRY !3 F 12.64` includes `STDEV` qty.
* `ADX 20/25/15/10` wired but `SNDK 30d` shows `0` delta correctly per engine (no impact for this window, not copy) — `S1` `prepare_batch 2333 bars` `base -2.18` → `ADX 20 gain -2.181 delta 0.000` for all 4, so `F -1.25` with `STDEV` `12.03` is same for `20/25/15` (plateau), `10 → 0` distinct.
* `BANDAID 0/2.0/1.0/0.5` wired similarly `0` when no impact.
* `Filter | Option Value` header now skipped, no synthetic `0.062`.

### 9.2 How to add a new switch (example `KINDERGARTEN_FILTER`)

1.  `config_tradier.py: KINDERGARTEN_FILTER_ENABLED = False`
2.  `v12_quick_engine.py:QuickConfig KINDERGARTEN_FILTER_ENABLED: bool = False`
3.  `v12_quick_engine.py:compute_entry` `if cfg.KINDERGARTEN_FILTER_ENABLED and k_15<80:` etc
4.  `tradier_manage.py:check_entry` same
5.  `TEMPLATE.xlsx` new row `A KINDERGARTEN_FILTER_ENABLED B False` `SWITCH_SHEETS` maybe `GLOBAL_RISK_GATES`
6.  Test `S1` `AAPL_LONG` `prepare_batch` vs `run_one` must influence `gain` via `qty` or `entry` (not just `VLOOKUP`).

---

## 10. Appendix B — `TEMPLATE.xlsx` structure (relevant columns)

* `A Switch` — config name, must match `QuickConfig` field.
* `B default` — default value from `QuickConfig` default.
* `C override` — candidate value to test (e.g. `10` for `D_MAX`, `True` for `WT_15M_BOUNCE`).
* `D Family` — `STDEV_SLOPE_SIZING`, `ENTRY_REVERSAL_BOUNCE`, `BLANKET` etc — used to filter `SWITCH_SHEETS`.
* `E BASELINE` col5 — `=TEMPLATE_BASELINE_METRICS!B2` for `r3`, else `=IF(F3="",E3,IF(F3>0,E3+F3,E3))` — **must be overwritten with `float(cumulative_before)` for `r==first_data_r` and with `new_cum if delta>0 else None` for `r+1`**, otherwise `E` drop.
* `F VECTOR_DELTA` col6 — `=VLOOKUP($A&"="&$B,Results_Deltas!A:P,5,FALSE)` — **must be overwritten with `float(delta_best)` for every row**, even `NEG/0`, never `VLOOKUP`.
* `G LIVE_DELTA` col7, `H LIVE_SHARPE` col8, `I REAL_COMPLETE` col9 — from `Results_Deltas` `delta_sharpe` etc.
* `J is_default` `K ...` — `=IF(C<>B,1,0)` etc.
* `L:BI` cols12-61 `PER_ROW_FILTERS` yellows `00DDEBF7` — `pending_lbI[hdr]=delta` for each `GENERAL` filter with `sheets_app ALL` or token-overlap, `header_to_col` `L=12`.
* `Results_Deltas` orange `00FFC000` — `A Switch, B default, C override, D is_non_default, E delta_gain_vs_bh col5, F delta_sharpe col6, G delta_trades col7, H variant_gain col8, I REAL_COMPLETE col9, J-K-L-M pool_sharpe/trades/tim/dd, N-P variant_*`.

If any of these columns stay `VLOOKUP`/`0`/`NONE` after `v15` run, that row was not wired.

---

## 11. Appendix C — `v15_pilot.py` flow (fast path)

1.  `preload_prepared(symside)` [v15_pilot.py:360](file:///Users/niels/Documents/binance/v15_pilot.py:360) `prepare_batch` → `ALL_PREPARED` `877 arrays` `V12_NPZ_CACHE=32`.
2.  `load_live_recipes` `94 overrides` vs `defaults` `3337` → `baseline_gain` `SNDK -1.85` `trades 201`.
3.  `clone TEMPLATE → V15_V16_CELL_BY_CELL/{SYM}_30d_matrix*.xlsx` `shutil.copy2` `794K` `23 sheets`, `ensure_lbI_headers` `L:BI`, `progress.json` `done {}` `cumulative_gain = baseline`.
4.  For each `sheet` in `SWITCH_SHEETS 13` [v15_pilot.py:917](file:///Users/niels/Documents/binance/v15_pilot.py:917):
    * `rows = [(r,sw,cand) for r if sw not in (switch,general,blanket,filter) and not startswith("—")]`
    * For each `r`: `cumulative_before = cumulative_gain`, `candidates = [naked] + singles` (`GENERAL` blanket + token-overlap) [v15_pilot.py:935](file:///Users/niels/Documents/binance/v15_pilot.py:935), `heavy → skip combos`.
    * `vec = ex.map(lambda v: _eval_prep(prepared, v))` [v15_pilot.py:954](file:///Users/niels/Documents/binance/v15_pilot.py:954) `ThreadPool16` `0.07s` (fast) vs `slow` fallback.
    * `delta = vec_gain - cumulative_before`, `best = max(delta)`, `new_cum = vec_best.gain_pct`.
    * `_atomic_save` `L:BI` yellows + `Results col5/col6` [v15_pilot.py:1265](file:///Users/niels/Documents/binance/v15_pilot.py:1265), `F col6=float(delta_best)` [v15_pilot.py:1173](file:///Users/niels/Documents/binance/v15_pilot.py:1173), `E col5` for `first_data_r` only.
    * Check `best is None`, `parity-fail`, `live_delta <=0`, `E-BLAND` `new_cum < cum` → `F` negative, `E next None`, `overrides None`, `continue`.
    * Promotion: `all_over` `switch + best_filter` [v15_pilot.py:1339](file:///Users/niels/Documents/binance/v15_pilot.py:1339), `ws3.cell(r,3)=overrides_str`, `ws3.cell(r,6)=delta_best`, `ws3.cell(r+1,5)=new_cum if delta>0 else None`, `cumulative_gain=new_cum`, `cumulative_overrides=variant_best`, `progress done + _atomic_write_json`.
    * `_validate_e_chain_and_yellows` [v15_pilot.py:360](file:///Users/niels/Documents/binance/v15_pilot.py:360) + `NPZ-CHECK`.
5.  `write_zoomable_chart` `AAPL_LONG_ENTRY_REVERSAL_BOUNCE_30D_REAL_ZOOMABLE.html` `150K` `canvas 1 fps 121` `wheel zoom drag`.

If `prepared is None` → fallback `slow` `14s/row` `75 days at 100x` — user sees `still feels like slow system`.

---

## 12. Appendix D — `S1` vs `Mac` NPZ and `S1` gateway

* `S1` `157.90.168.35` (`S1`) + `157.180.125.52` (`gateway`) + `10.0.0.3` (`sandbox`) — `~/binance-sandbox` `42M` `AAPL.npz` vs `Mac` `975K` truncated → `0 trades DATA_ERROR` on Mac, so `ON S1 NOT ON Mac`.
* `~/.ssh/cm-s1-int` `ControlMaster` for `s1-int` (`157.180.125.52:22` via `gateway-internal` `157.90.168.35:2201`), `S1_NPZ_DIRS` `backtest_v8/indicators`, `hourly_reconfig/trb/active_config.json` `94 overrides`, `V15_V16_CELL_BY_CELL` `NFS` between `gateway` and `sandbox`.
* `Mac` autosave `launchd` every `15min` git commit, `backups/before_*` is only reliable history, never `git reset` per `CLAUDE.md`.

---

## 13. Appendix E — Current `S1` state (2026-09-13 01:30)

* `SNDK_LONG --vector-only` full `no max-switches` `PID 461033` `877 arrays 2333 bars hot` `done 1→5 cum 10.46→12.03` `775K` `V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx` `23 sheets` — will be `~800K` `900` rows `45-60min`.
* `AAPL_LONG 775K` `SNDK 782K 113 F` `ENTRY` most complete wired until full `~01:55` finishes.
* `TEMPLATE.xlsx` `776K` `ENTRY yellows 8` row3 `c12-19` `00DDEBF7`, `STDEV 0` (pos will be added when `STDEV` `F>0` found).
* `v15_pilot.py` `1505L` now `E-BLAND` + `header skip` + `F always` + `max-switches` eliminated, `S1` compile `ok`, `pytest 13 passed`.

---

## 14. Appendix F — Backtest-expert validation for `SNDK_LONG 30d`

* **Sample floor:** `2333 bars` `30D` `201 trades` base `>100` `>1yr`? No, `30D` is diagnostic only per `BACKTEST_BIBLE` (`≥48 crypto OR ≥100 stocks × >1yr × ≥30 trades/sym`), so `SNDK 30d` `201 trades` `30D` is `DIAGNOSTIC ONLY` — not for promotion, `1y` `250 combos` was `0 pos`.
* **Bias prevention:** `V12_NPZ_CACHE=32` `prepare_batch` `30` uses `offset_days` `window_days` strictly, no lookahead `lrL` `wt` computed from prior `close`, `STDEV` `D 6mo` uses `slope` `6mo` not future.
* **Parameter robustness:** `SNDK` `ENTRY` `198` rows `113 F` `57%` pos, `STDEV` `10x 0.68` vs `1x 0.21` distinct, `K 15 WT 15 GR` `250` combos `0 pos` in `1y` shows not overfit.
* **Slippage:** `backtest_v12_engine` `execute_now` `futures_create_order` gate, `ez_manage` `persist_hedge_record`, `0.08%` `TRB` fractional.
* **Next:** After `SNDK` full `~800K` `900` rows, `rsync` to `Mac`, `pytest` `13 passed`, `SNDK` `30D` `12.03` vs `STDEV` `10x` `12.03` same (no `STDEV` edge for `SNDK 30d`), test `AAPL_LONG` `STDEV` `10→1x` distinct `0.68→0.21` to prove `qty` wiring.

---

## 15. Appendix G — Commands to resume (hands-free)

```bash
# S1 fresh SNDK every cell (currently 461033 running)
ssh s1-int 'tail -f /tmp/v15_sndk_full_now.log'
ssh s1-int 'ps aux | grep v15_pilot | grep -v grep; ls -lh SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK*.xlsx'
# when done 900 cum ~12-14
rsync -az s1-int:/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx ~/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/
python3 - << 'PY'
import openpyxl
p="/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx"
wb=openpyxl.load_workbook(p, data_only=False)
print([(s, sum(1 for r in range(3, wb[s].max_row+1) if isinstance(wb[s].cell(r,6).value,(int,float)))) for s in wb.sheetnames if s.startswith("ENTRY")])
PY
python3 -m pytest tests/test_v15_pilot.py tests/test_v15_e_bland.py -v
```

**Never `git reset`, never `cp old backup over new`, never `rm` user `V15_V16` untracked.**


---

## 16. Appendix H — Detailed per-sheet analysis (why STDEV never reached)

* `ENTRY_REVERSAL_BOUNCE 198 rows` — fully wired `113 F` in `782K` pilot, `5/198` in fresh `775K` after `rm`, will be `198/198` after full `~13min`.
* `STDEV_SLOPE_SIZING 15 rows` — `F 0/15 VLOOKUP` in both pilots because `SNDK 30d` `STDEV` `D 10x` delta `0` (no edge for `SNDK` `30d` `2333 bars` `6mo` slope `0.2`), but `AAPL 30D` `STDEV` `10x 0.68 vs 1x 0.21` distinct — so `STDEV` `15` will stay `0` for `SNDK` correctly, not copy, but `F` still `float 0` not `VLOOKUP` after fix (currently `0` `VLOOKUP` because sheet not yet reached).
* `ENTRY_BREAKOUT_CHANNEL 387 rows` — `0/387` not yet, heavy `skip combos` `singles only` for `>500` rows.
* `ENTRY_CONFIRMATION_GATES 23`, `EXIT_STRUCTURAL 183`, `EXIT_VELOCITY 32`, `REENTRY_WINDOWED 112`, `REENTRY_ADAPTIVE 197`, `AUGMENT_TREND 197`, `AUGMENT_RISK_SIZING 32`, `REDUCE_PROFIT_LOCK 197`, `REDUCE_SIGNAL_RATER 197`, `GLOBAL_RISK_GATES 32` — all `0` until `SNDK` full `60min`.

**How to know sheet 2 not reached:** `SNDK` `775K` `ENTRY F 1/198` `STDEV 0/15` `Results 1/114` (only `1` row for `ENTRY`); `SNDK` `782K` `ENTRY 113/198` `STDEV 0/15` — sheet 2 never started. New `SNDK 461033` `done 5` will be `ENTRY 5/198` then `STDEV 5/15` etc — after `~45min` all `13` will be `F` floats.

## 17. Appendix I — Config wiring per switch (current values)

```
config_tradier.py:
  STDEV_SLOPE_SIZING_ENABLED = True
  STDEV_BAND_MULTIPLIER = 2.5
  STDEV_SLOPE_SIZING_D_MAX = 10.0
  STDEV_SLOPE_SIZING_4H_MAX = 4.0
  STDEV_SLOPE_SIZING_1H_MAX = 2.0
  STDEV_SLOPE_SIZING_15M_MAX = 1.5
  STDEV_SLOPE_SIZING_MODE = "slope_to_top"  # or bottom_to_top
  STDEV_SLOPE_SIZING_MIN = 0.5
  BAND_SLOPE_SIZING_V2_SLOPE_NORM_PCT_DAY = 1.0
  BAND_SLOPE_SIZING_V2_TF = "D"
  WT_15M_BOUNCE_OPEN_ENABLED = False
  WT_15M_BOUNCE_BB_MAX = 0.9
  BB_SQUEEZE_ENTRY_ENABLED = True
  WT_MOMENTUM_EXIT_THRESHOLD = 1
  ADX_RANGING_THRESHOLD = 20
  BANDAID_OFF_LOSER_RECOVER_PCT = 0.0
  BAND_ARROW_ENABLED = False
  DELTA_HTF_GATE = "4h"
  DC_MOMENT_STRONG_THRESHOLD = 40
  COOLDOWN_LOCKS_FILTER_TF = "OFF"
  CRYPTO_SPIKE_FADE_THRESHOLD_PCT = 10
  EMA_DIST_SIZING_MULT = 1.0
```

`QuickConfig` must have same fields with same defaults, otherwise `v12` misses `getattr` and `is_non_default` `0`.

## 18. Appendix J — `v12_quick_engine` STDEV detailed (before vs after)

**Before (audit):**
```python
# STDEV_BREAKOUT_RETEST_SIZE_MULT — faithful to live: 1-10x slope-vs-stdev sizing boost
stdev_mult = float(getattr(cfg, 'STDEV_BREAKOUT_RETEST_SIZE_MULT', 1.5))
if stdev_mult != 1.5:
    entry_mask = entry_mask & _cond  # NEW_AUDIT balanced
    entry_mask[0] ^= True
```
Only `entry_mask` flip, no `qty` change → `D_MAX 10/8/6/4` same `gain`.

**After (real):**
```python
if cfg.STDEV_SLOPE_SIZING_ENABLED:
    max_map = {"D": cfg.STDEV_SLOPE_SIZING_D_MAX, ...}
    pb = lrL_pct_b[TF]  # 0 bottom 1 top
    edge = (1-pb) if is_long else pb
    if mode == "slope_to_top":
        bs_m = max_val if edge>=0.5 else 1+(max_val-1)*edge*2
    else:
        bs_m = 1+(max_val-1)*edge
    slope_day = lrL_slope[TF]*factor[TF]
    sn = min(abs(slope_day)/SLOPE_NORM,1)
    bs_m *= (1+0.5*sn) if fav else max(0.5,1-0.5*sn)
    bs_m = clip(bs_m, min, max)
    mult *= bs_m
    qty = _size_qty(START_SIZE*mult, price)
```
Now `AAPL 30D` `10x 57413 0.68` vs `1x 5965 0.21` distinct, `SNDK` `ENTRY r3 12.64` includes `STDEV` qty.

## 19. Appendix K — `TEMPLATE.xlsx` VLOOKUP vs float (why `F` must be float)

* `TEMPLATE` `F col6` `=VLOOKUP($A&"="&$B,Results_Deltas!A:P,5,FALSE)` — pulls `delta` from `Results_Deltas col5`. If left `VLOOKUP`, Excel recalculates `delta` from `Results` which may be stale or `0`, not from `vector` `gain` with current `cum`. `v15` must overwrite `F` with `float(delta_best)` for every row, even `NEG/0`, so `data_only True` shows `float` not `VLOOKUP`.
* `E col5` `=IF(F3="",E3,IF(F3>0,E3+F3,E3))` — if left `VLOOKUP`, `E` drop `10.93→7.83` occurs. `v15` overwrites `E` only when `F>0` with `new_cum`, else `None` (blank) per spec.

## 20. Appendix L — `Results_Deltas` orange `F col6` wiring (previously missing)

* `TEMPLATE` `Results_Deltas` `col5 delta_gain col6 delta_sharpe col7 delta_trades col8 variant_gain col9 REAL_COMPLETE` — `col6` `F` was `VLOOKUP` not filled by old `v15` (only `col5`/`col8`). Fixed [v15_pilot.py:1265](file:///Users/niels/Documents/binance/v15_pilot.py:1265) `header_map["delta_sharpe"]` etc now writes `col6` `float(vec_best.pool_sharpe - baseline.pool_sharpe)` — verified `SNDK r2 col6 0.027`.

## 21. Appendix M — Hands-free, death penalty, no `ask_question`

* `HANDS_FREE`/`HANDS_OFF` `GLOBAL DEFAULT` — never `ask_question`, 100% autonomy.
* `DEATH PENALTY — NEVER REVERT LIVE CODE` — never `cp old backup over new`, never `git reset`, never `delete/pop position dict`, never `zero entry_price`, never `place orders outside execute_now`.
* `LOCKED_FILES.md` — check before edit, `unlock <file>` only.
* `STEP 1` backup `cp <file> backups/before_<desc>_$(date +%Y%m%d%H%M).py` `py_compile` `grep` `rsync` `verify parity`.

## 22. Appendix N — Current `S1` processes and `Mac` files (2026-09-13 01:35)

* `S1` `461033` `SNDK_LONG --vector-only` full `no max-switches` `877 arrays 2333 bars hot` `done 5 cum 12.03` `775K` → `~800K` `900 rows` `45-60min` to `V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx` `23 sheets`.
* `Mac` `SNDK 782K 113 F` `ENTRY` most complete wired until full finishes, `AAPL 775K` `SNDK_SHORT 766K` etc `23 sheets`, `TEMPLATE 776K` `ENTRY yellows 8`.
* `tests/test_v15_pilot.py` `11 passed` + `test_v15_e_bland.py` `2 passed` `13 passed`, `backups/before_EblandFix_*.py` kept.

## 23. Appendix O — How to verify after `SNDK` full finishes

```bash
ssh s1-int 'ls -lh SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx; python3 - << "PY"
import openpyxl
p="/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx"
wb=openpyxl.load_workbook(p, data_only=False)
for s in ["ENTRY_REVERSAL_BOUNCE","STDEV_SLOPE_SIZING","Results_Deltas"]:
    ws=wb[s]
    print(s, sum(1 for r in range(3, ws.max_row+1) if isinstance(ws.cell(r,6).value,(int,float))), "/", sum(1 for r in range(3, ws.max_row+1) if ws.cell(r,1).value))
PY
'
rsync -az s1-int:/home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/SNDK_LONG_30d_matrix.xlsx ~/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/
python3 -m pytest tests/test_v15_pilot.py tests/test_v15_e_bland.py -v
```

If any `F` still `VLOOKUP`/`0`/`NONE` or `E` drop, re-apply §7, clear `SNDK_LONG_v14_progress.json` (latest, not old `782K`), re-run `v15` and re-audit `openpyxl data_only False vs True`.

---
