# 📖 BACKTEST BIBLE — Single Source of Truth — 2026-09-26 Redo

> **Canonical file.** Mac `BACKTEST_BIBLE.md` is the only canonical copy.
> After every edit `tools/sync_backtest_bible.sh` publishes identical bytes to
> `S1:/home/niels/binance-sandbox/BACKTEST_BIBLE.md` and `S1:data/reports/BACKTEST_BIBLE.md`
> and verifies 4× SHA-256. If conflict, THIS file wins.
> Historical `backtest_bible_0901.md` (745K) is fallback only.

> **RULE 0 — NEVER REVERT.** Find better version in `backups/` on S1+Mac before git.
> Diff and patch current — never overwrite newer with older. Git is unreliable — check dates.

---

## 🔴 NO-LIES MANDATE — EVERY METRIC REAL

Lying Sharpe/gain/DD wiped out half the user's net worth in 4 months. Every metric on a human-facing surface MUST be real — forward and backward.

1. Every script emitting Sharpe/gain/DD to human surface MUST route through `metrics_guard.validate_and_format_sharpe()`.
2. Every CSV in `data/sweep_results/` or `data/autonomous/` MUST include `pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr, trades, max_dd_pct, n_syms, years`.
3. No annualization. No `sqrt(252)` / `sqrt(N)` — BANNED: `sharpe_annual, sharpe_y, sharpe_yearly, sharpe_w, pool_sharpe_proxy, sharpe_rough`.
4. No bare "Sharpe" label — always qualified: `pool_sharpe`, `sym_sharpe`, or `sharpe_per_trade`.
5. Sample floor: ≥48 crypto OR ≥100 stocks × >1yr × ≥30 trades/sym. Below → `[DIAGNOSTIC ONLY]`.
6. Source of truth = trade-return list. Sharpe without per-trade returns = `[UNVERIFIED]`.

`metrics_guard.write_sharpe_row()` is the ONLY sanctioned writer. It refuses violations.

---

## 1. INFRASTRUCTURE — GATEWAY + S1 ALWAYS STAY, S4/S5 ARE IMAGES OF S1

**Gateway + S1 never deleted, never wiped.**

- **Gateway** `157.90.168.35` (`gateway-internal` via `~/.ssh/config` ProxyJump, fallback `157.180.125.52:22` direct, `s1-sftp` ControlMaster `~/.ssh/cm-s1-int` on `127.0.0.1:2201`). Bootstrap: `ssh -fNT s1-sftp` then verify `ssh s1-int "hostname"` and `ssh s5 "hostname"`.
- **S1 Niels** `s1-int 127.0.0.1:2201 / s1-pub 157.180.125.52:22 / niels 10.0.0.3 / hel1 2a01:4f9:c013:fdf7::/64` — 16c 30 Gi 79 G free, `backtest_v8/indicators 473×31 G` local, `SPREADSHEETS/` + `data/reports/lifecycle_pilot/` source of truth.
- **S4 `65.108.49.184` (10.0.0.4), S5 `10.0.0.5`, future `S6…`** — `10.0.0.x via gateway ProxyJump`, ephemeral, **exact image of S1** (`rsync -az s1-int:~/binance-sandbox/ niels@10.0.0.x:~/binance-sandbox/`). Never provision from scratch; always clone S1.
- **Mac** `Darwin /opt/anaconda3/envs/binance_env/bin/python 134×10 G` — **LIVE trading + dashboard only, never backtests** except `--dry-run` / `--allow-mac`. Mac NPZ truncated → `0 trades DATA_ERROR`.

**Code sync:** Edit only on Mac (`/Users/niels/Documents/binance`), then `rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new"` `v15_pilot.py`, `SPREADSHEETS/TEMPLATE*.xlsx`, `tools/v15_local_herd.py` to `~/binance-sandbox/` + `~/binance/` on S1/S4/S5, `md5sum` verify. Never `push.py`.

**NPZ sync:** S1 is NPZ source (`backtest_v8/indicators/*.npz`). `tools/sync_indicators.sh` rsyncs to `10.0.0.4/5` every 60 s. If `ZECUSDC` missing `stdev_edge_15m`, run `backtest_v8_precompute.py --symbol ZECUSDC --mode crypto` on S1.

---

## 2. DATA — WHAT v15_pilot READS

| Source | Path | Content | Window |
|---|---|---|---|
| **NPZ indicators** | `backtest_v8/indicators/{SYM}.npz` (S1: `~/binance-sandbox/backtest_v8/indicators/`) | Per-bar arrays: `open/high/low/close/volume`, `dc_position_*`, `wt1/wt2_*`, `atr_*`, `sma_200_1h`, `adx_1h`, `stdev_edge_*`, `stdev_slope_*`, `timestamps` (unix ms) | Pilot slices **30 days** (`timestamps[-1] - 30d` crypto, `20 RTH sessions` stocks) via `prepare_batch(sym, 30)` |
| **TEMPLATE workbook** | `SPREADSHEETS/TEMPLATE*.xlsx` | 13 `SWITCH_SHEETS` + `*_BASELINE_METRICS` + `FILTER_DICTIONARY_V2` + `FILTERS_EXPLAINED` + `LEGEND_FILTERS` | Cloned per `sym_side` to `V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_30d_matrix.xlsx` |
| **Previous best overrides** | `SPREADSHEETS/` + `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` (`cumulative_overrides`, `hustler_overrides`) + `hustler_best.json` | Best `switch=cand` and `filter=opt` per exact `SYM_SIDE` | Ingested **before** baseline; best wins over `config.py`/`config_tradier.py` defaults — never `if k not in overrides` guard |
| **Defaults** | `config.py` / `config_tradier.py` | 851 `QuickConfig` fields | Sanitized via `sanitize_overrides()` |

**No per-row disk reload.** `ALL_PREPARED` + `V12_NPZ_CACHE=32` + `preload_prepared()` keep the 30-day sliced NPZ in RAM for the entire workbook. Per-row reload is forbidden — it turns 0.07 s/cell into >1 s/cell.

---

## 3. ENGINES — TWO, NOT ONE

| Engine | Real reads | s/eval | Role |
|---|---|---|---|
| `v12_quick_engine` (`QuickConfig`, 851 reads) | 851 | 0.07 s | **Vector sweep** — `V.simulate_one(npz,sym,is_long,cfg)` + `prepare_batch` / `evaluate_prepared_sanitized` (hot `ALL_PREPARED`, `V12_NPZ_CACHE=32`). What `v15_pilot` calls for every yellow cell and every `VECTOR_DELTA`. |
| `backtest_v12_engine` (266 + 1981 stubs) | 266 | ~30 s | **Scalar live-faithful verifier** — calls real `ez_manage.process_position` / `tradier_manage.process_position` bar-by-bar, guarded by `_assert_live_path`. What `v15_pilot` calls **once per workbook** (winning set) to fill `LIVE_DELTA`/`LIVE_SHARPE` and prove parity. |

Parity = `v12_quick_engine` vs `backtest_v12_engine` on **same frozen 30-day NPZ**. `backtest_v12_engine` carries no-vectorisation guard — do not defeat. Trade ratio must be 0.80–1.25 and gain mismatch <0.5 pp and <15%.

---

## 4. TEMPLATE SYSTEM — THE 12-TAB WORKBOOK

### 4.1 Source

`SPREADSHEETS/TEMPLATE*.xlsx` — four variants, selected per `sym_side` by `get_template_for_symside()`:

- `TEMPLATE_STOCKS_LONG.xlsx` / `TEMPLATE_STOCKS_SHORT.xlsx` → stocks via `tradier_manage` / `config_tradier.py`
- `TEMPLATE_CRYPTO_LONG.xlsx` / `TEMPLATE_CRYPTO_SHORT.xlsx` → crypto via `ez_manage` / `config.py`
- `TEMPLATE.xlsx` generic fallback (deprecated for new runs).

Each template has:

- **13 `SWITCH_SHEETS`** in fixed order: `STDEV_SLOPE_SIZING, ENTRY_REVERSAL_BOUNCE, ENTRY_BREAKOUT_CHANNEL, ENTRY_CONFIRMATION_GATES, EXIT_STRUCTURAL, EXIT_VELOCITY, REENTRY_WINDOWED, REENTRY_ADAPTIVE, AUGMENT_TREND, AUGMENT_RISK_SIZING, REDUCE_PROFIT_LOCK, REDUCE_SIGNAL_RATER, GLOBAL_RISK_GATES` — **`STDEV_SLOPE_SIZING` is SKIPPED until rewritten (see §4.2), so 12 tabs are active.**
- **`TEMPLATE_BASELINE_METRICS`** (renamed on clone to `{SYM}_{SIDE}_BASELINE_METRICS`)
- **`FILTER_DICTIONARY_V2`** — every filter's `Filter | Option Value | Sheets applicable | Switches exactly (gates) | Recommendation (SPECIFIC/GENERAL)` — the source of yellow-cell eligibility.
- **`LEGEND_FILTERS` / `FILTERS_EXPLAINED` / `INSTRUCTIONS_V2`** — human docs.

### 4.2 Column contract — read by row-2 headers, never by coordinates

Columns may be added, so `v15_pilot` resolves every column via `_hdr_col_map()` / `_resolve_cols()` on **row 2 header names**:

| Col | Header (row 2) | Meaning | Pilot writes | Rule |
|---|---|---|---|---|
| A | `Switch` | Switch name (e.g. `WT_15M_BOUNCE_ENABLED`) | never | White rows = switches under test. |
| B | `default` | Default value (bold = default) | never | Bold/regular in column B is sacred — only maintenance via `V15_AVG_DELTAS` recalculation changes it. `is_default` col L is backup. |
| C | `override` | Override for this row | `switch=cand [+ pos_yellow_headers]` | **Bold** if non-default, only when `VECTOR_DELTA > 0` for that row's positive yellows. Never overwrite — **add** header names. |
| D | `Family` | `SPECIFIC` vs `GENERAL` | never | `GENERAL` rows are orange per-sheet rollups, never yellow per-switch. |
| E | `BASELINE` | Cumulative baseline before this row | numeric float or blank | **E2 is always header `BASELINE` (string, never overwritten). E3 = `baseline_gain` (first numeric). All other E cells stay BLANK until a POS delta promotes the next row/tab (see §5.4).** |
| F | `HUSTLE_DELTA` | Alias of `VECTOR_DELTA` vs `cumulative_before` | float pos/neg | Never leave as `VLOOKUP` — pilot decides. Fill `#4472C4` header style handled by `_auto_adjust_all_sheets`. |
| G | `VECTOR_DELTA` | `sum(pos yellow deltas)` vs `cumulative_before` | float pos/neg | Every row gets a value — pos green `006100` or neg red `FFC7CE`. Never `None`. |
| H | `LIVE_DELTA` | `live_gain - cumulative_before` | **BLANK until workbook complete** | Contains formulas in template — **pilot clears them at start** (`_spec_clear_live_formulas`). Filled once at the end via `backtest_v12_engine` on winning set. Never per-row. |
| I | `LIVE_SHARPE` | `live pool_sharpe` delta | **BLANK until workbook complete** | Same as H — cleared at start, filled once at end. |
| J | `REAL_COMPLETE` | — | — | — |
| K | `PER_ROW_FILTERS` | Comma-joined positive yellow headers for this row | `hdr1, hdr2` or blank | Per-row via `_write_per_row_HIK`. |
| L | `is_default (backup if bold lost)` | `YES` if row B should be bold | never | If bold lost, pilot restores B bold from `L=YES` at workbook open. |
| M:N | `AVG DELTA` / `POS_SYM` | Maintained by `V15_AVG_DELTAS` | never by pilot | Reordered `worst_first` while keeping entire column content together — yellow cells for a row always stay with same switch name when row order changes. |
| O:BI | Yellow headers `FILTER=OPT` (e.g. `ATR_TRAIL_FILTER_TF=OFF`) | Per-yellow delta vs `cumulative_before` | float delta per yellow cell | **Opportune yellows only** (see §5.3). Written before row advances. |
| — | `ORANGE (FILTER)` fields below SWITCH in same column C | Per-sheet GENERAL rollups | — | **Can NEVER be above white (SWITCH) rows.** White switches occupy `r=3..n_switch`, orange GENERAL rows follow below — never interleaved. |
| — | `WHAT SWITCH` | Sentinel ending yellow block | never | Header scan stops here. |

**Visual:** All cells `Arial 10 left`, row height 15, column width `max_len+2 cap 30` via `_auto_adjust_all_sheets` before every `_atomic_save`. `DC_BREAKOUT_SCORE` int `10`, TF `15m` string, `False/True` not `FALSE/TRUE`. Only switch column C may carry non-default bold; no blueish/orange outside C.

### 4.3 STDEV_SLOPE_SIZING — skipped

34 rows implementing `compute_regime_sizing_mult()` (`BAND_SLOPE_SIZING_V2`, `stdev_edge_*`, `stdev_slope_*` per TF `D/4h/1h/15m`). Sheet stays in TEMPLATE but `SKIP_SHEETS = {"STDEV_SLOPE_SIZING"}` — never calculated until rewritten. Effective workbook = **12 tabs, ~3800 rows** (was 4801 with STDEV).

---

## 5. WHAT v15_pilot IS SUPPOSED TO DO — EXACT FILL CONTRACT

`v15_pilot.py` is the **ONLY writer** of `V15_V16_CELL_BY_CELL/*_30d_matrix.xlsx`. Formulas never in data rows `r≥3` for `C/E/F/G/H/I/K` — pilot decides and clears any `VLOOKUP`/`IF` via `_clear_vlookup_formulas` (except documented `GLOBAL_RISK_GATES` waiver). The workbook is **never filled in parallel across tabs** — tabs are filled sequentially under pilot order (with `hustle` shuffle as the only exception).

### 5.1 Where it runs

**S1 only** (`s1-int 127.0.0.1:2201` via `s1-sftp` ControlMaster). The herd (`tools/v15_local_herd.py`, `tools/v15_overnight_herd.py`, `tools/v15_cpu80_watchdog.py`) launches **one `v15_pilot` per `sym_side`** with `worst2best` / `cycle` / `shuffle` ordering, `workers 56` (S1) / `28` (S5), `V12_NPZ_CACHE=32`, `ALL_PREPARED` in RAM. Mac never runs sweeps (except `--dry-run`), S4/S5 are clones.

### 5.2 Step 0 — Clone + ingest best overrides + baseline

For each new `sym_side` (e.g. `AAPL_LONG`):

1. **Find best overrides for that exact `sym_side`:** Search `SPREADSHEETS/` + `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` (`cumulative_overrides` / `hustler_overrides`) + `hustler_best.json`. **Best wins over defaults — never `if k not in overrides` guard.** Every non-default from the winning set is written **bold in column C `override`** before any calculation — never changing bold/regular of column B `default` (column B only changes via maintenance when `V15_AVG_DELTAS.xls` recalculates `AVG_DELTA` and reorders `worst_first`).

2. **Clone:** `TEMPLATE{STOCKS|CRYPTO}_{LONG|SHORT}.xlsx` → `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_30d_matrix.xlsx` via `_atomic_save` (validates `ZipFile ≥10` entries before `os.replace` to avoid 225 KB truncation). Rename `TEMPLATE_BASELINE_METRICS` → `{SYM}_{SIDE}_BASELINE_METRICS`, fix `G` `VLOOKUP &"_"&`→`&"="&`, clear `#NUM!/#NAME?/0` in `E`.

3. **Fill ALL overrides in sheet before baseline:** `BEST-C-FILL` — pilot iterates every sheet row `3..max_row`; if `A=Switch` exists in `overrides`, set `C=override` (e.g. `C3=False` for `WT_15M_BOUNCE_OPEN_ENABLED`). This is how the first row calculates baseline **while** all overrides are already in the sheet.

4. **Baseline:** `evaluate_prepared_sanitized(prepared, overrides, 30)` (or `evaluate_sanitized` if no prepared) on the 30-day NPZ slice → `baseline_gain` / `bh` / `trades` / `pool_sharpe` written to `{SYM}_BASELINE_METRICS!B2` and **`STDEV_SLOPE_SIZING!E3` / first pending row's `E`** (with `E2` header `BASELINE` preserved). **Zero-trades still writes XLS then skips sweep — never `DIAGNOSTIC ONLY` with no XLS**; early `return` before `clone_template` is forbidden.

### 5.3 Step 1 — Per-row yellow evaluation

For each tab **sequentially** (`ENTRY_REVERSAL_BOUNCE → ENTRY_BREAKOUT_CHANNEL → ENTRY_CONFIRMATION_GATES → EXIT_STRUCTURAL → EXIT_VELOCITY → REENTRY_WINDOWED → REENTRY_ADAPTIVE → AUGMENT_TREND → AUGMENT_RISK_SIZING → REDUCE_PROFIT_LOCK → REDUCE_SIGNAL_RATER → GLOBAL_RISK_GATES`, STDEV skipped), for each `switch=cand` row `3..max_row` **in order**:

- **Opportune yellows** for that exact `switch` — from `FILTER_DICTIONARY_V2` / `FILTERS_EXPLAINED`, filtered by the yellow headers `O:BI` (row 2) for that sheet. A yellow cell exists iff:
  - `Recommendation` is `SPECIFIC` (not `GENERAL` — GENERAL is the orange per-sheet rollup, never yellow per-switch),
  - `Sheets applicable` contains the sheet's lifecycle (`ENTRY`, `EXIT`, `REENTRY`, `AUGMENT`, `REDUCE`, `GLOBAL_CHECK`) or `ALL`,
  - `Switches exactly (gates)` token-overlaps the current `switch` (`_token_overlap` requires ≥2 strong tokens or exact switch containment — generic `FILTER` token alone is not enough),
  - and `FILTER=OPT` header exists in `O:BI` for that sheet.

  **If a cell is yellow, the filter in the column name MUST be tested for THAT SWITCH AND ONLY THAT SWITCH** — not applied to any other override or default. Never calculate random filters that are not yellow for that row (forbidden — wastes CPU, lies about provenance).

- **Candidates:** `[naked]` (switch=cand alone) `+ each yellow filter variant` (switch=cand **plus that one filter** = `FILTER=OPT`, vs `cumulative_before`). Each candidate evaluated via `v12_quick_engine` (`ThreadPool 16`, `0.07 s` each, `V12_NPZ_CACHE=32` from RAM). Every yellow delta is written **into that yellow cell** (`L:BI`) before the next row — never batched to end.

- **`VECTOR_DELTA` (G) = sum of positive yellow deltas for that row** (`>1e-9` summed via `_per_yellow_sum` into `delta_best`). Naked delta is only used when there are **no yellows** for that row (see §5.4 — no-yellow rule). Negative/zero yellows are written but **not added** to `G`.

- **If positive:** add the **column header name** to `override` (C) and `PER_ROW_FILTERS` (K) — **add, never overwrite** existing content — and add its delta to `VECTOR_DELTA` (sum if already valued). When all yellows for the row are done the row is complete.

### 5.4 Step 2 — Baseline chaining and tab navigation

After all yellows for the row are evaluated:

| Condition | Meaning | Action |
|---|---|---|
| `VECTOR_DELTA > 1e-9` (positive sum of pos yellows) | Row is winner | Write `C` (switch + pos yellows), `F/G/K` + yellow `L:BI` cells. **Move down 1 row on SAME TAB.** `E` for the next pending row on this tab = `cumulative_before + VECTOR_DELTA` (numeric `E_next = E + G`). `cumulative_gain` and `cumulative_overrides` advance (add switch=cand + each pos yellow filter). |
| `VECTOR_DELTA` is `None`, zero, or negative (no pos yellows) | Row is loser | Write `F/G/K` + yellow `L:BI` deltas (with negative red fill for G), leave `C` blank. **DO NOT MOVE DOWN the tab.** Move to **first pending row in the next tab**, write `E = cumulative_before` there, and repeat process on that new tab. |
| Row has **no yellow cells at all** | Untestable switch alone | Evaluate naked switch vs `cumulative_before`, write `F/G` as that delta, then **always continue to next TAB (not next ROW)** per spec — even if POS — because there is nothing to exploit on this row. |

**Baseline invariant:** `E` (BASELINE) **only gets written after a positive delta** — otherwise it **stays BLANK normally**. The only `E` values that exist are `E3 = baseline_gain` and each `E` that was promoted by a preceding POS row on its tab (or the first pending row of a newly entered tab which inherits the current `cumulative_gain`). `_validate_e_chain_and_yellows` asserts `new_cum ≥ old_cum` and `delta == vg - cum`.

**Every row in every tab needs a delta value pos or neg.** All rows are filled in order; if a tab is complete it is skipped in remaining rounds. Only `hustle`/`shuffle` mode fills rows in random order (shuffle via `_rnd.shuffle(rows)` per tab).

### 5.5 Step 3 — LIVE verification (deferred)

`LIVE_DELTA` (H) and `LIVE_SHARPE` (I) **contain formulas that screw up sheet fill — they stay BLANK until the entire workbook is complete** (`_spec_clear_live_formulas` clears any formulas at open, `_write_per_row_HIK` writes only `K` per row and leaves `H/I = None`).

When the 12-tab workbook is fully filled, pilot runs the **winning set** (`cumulative_overrides`) through `backtest_v12_engine` (`live_evaluate`, 30 s timeout) and fills **every processed row's `H`/`I`** with the live parity result (POS rows get `live_delta`/`live_sharpe`, NEG rows get their own negative delta / `0.0` sharpe). Per-row parity fail marks flags but never aborts the sheet. Falls back to vector result on 30 s live timeout. Final charts and 365-day robustness rerun are handled after (§7).

### 5.6 Speed — NPZ in RAM

NPZ for the `sym_side` is preloaded **once** via `preload_prepared()` → `ALL_PREPARED[sym]` (0.85–1.5 G) and kept for all ~3800 row×yellow evaluations. `evaluate_prepared_sanitized()` uses RAM arrays only. Per-row disk reload is forbidden. The herd keeps workers at **max 80% RAM** (`avail > 1500` guard, `V12_NPZ_CACHE=32`).

### 5.7 Stall guard — >10 s = RED and move on

If a yellow cell's `v12_quick_engine` evaluation stalls **>10 s** (`YELLOW_TIMEOUT = 10.0`, `per_cell_timeout_sec = 10.0`, `_per_cell_hard_limit = 10.0`):

- Mark **that single yellow cell** `RED` (`FF0000` fill, `FFFFFF` bold) and write `TIMEOUT 10s` reason in it.
- Mark the **tab color** `RED` (`ws.sheet_properties.tabColor = "FF0000"`).
- Append to `data/reports/v15_flags/{SYM}_{SIDE}_flags.md` via `_flag_to_md`.
- **Continue to the next yellow cell** in the same row. If no yellow cells in the row, **continue to next TAB, not next ROW** (never hang, never >1 h per workbook, per-sym ≤60 m never >20 m).

Never stall the entire workbook on one cell. Baseline evaluation has its own 60 s guard and falls back to empty baseline on timeout.

---

## 6. WHY THE TEMPLATE SYSTEM IS NOW STUCK — OVER A WEEK WITHOUT A SINGLE TAB

Despite having a working last-good workbook (`SPREADSHEETS/V15_V16_CELL_BY_CELL/UNIUSDC_LONG_bh57p81_gain32p89_30d_matrix.xlsx` — did copy `BEST→C`, baseline via `v12_quick_engine`, per-row `F/G` + `L:BI` yellows correctly — `E2` numeric, `C` populated, `231` yellows), the current pilot produces for **every `sym_side` with `*_v14_progress.json done>0`: `E2=BASELINE` string, `C` empty, `F/G/H/L:BI None`, sheets showing `BASELINE #NUM! 0`** and not a single tab completes. Spend is `~$0.40/hr ×3` with zero output.

### 6.1 Root causes — template instructions were never correctly implemented

1. **`0-trades` early return before `clone_template` → no XLS at all for `0 trades` (`DATA_ERROR`).** Pilot returned before cloning the workbook, so valid zero-trade baselines (which must still write an XLS and then skip sweep) produced no file — downstream logic found no file and stalled.

2. **`if k not in overrides` guard → `C` empty.** When ingesting best overrides, the guard skipped any `switch` already present in `cumulative_overrides` from defaults, so the winning set never overwrote the template defaults and column C stayed empty. Spec requires **best wins over defaults — never `if k not in overrides`**.

3. **XLS prev-parser only handled `C="K=V + …"` with `F>0`, missed single-value `C` (`False`) baseline overrides.** Single-value overrides (e.g. `WT_15M_BOUNCE_ENABLED=False`) were not parsed from previous XLS, so the ingested best set was incomplete and baseline was computed from wrong defaults.

4. **`_atomic_save` without zip-validate → `225 KB` BadZip (`CLF_LONG done 2243` unreadable).** Truncated saves (OOM/pkill) were not validated (`ZipFile ≥10` entries) before `os.replace`, producing unreadable workbooks that looked `done` in JSON but had no fill.

5. **Column-C coordinate write vs header lookup.** Pilot wrote to hardcoded `col 3` without resolving row-2 headers, so any inserted column broke the fill. Columns `O:BI` (yellow) header detection missed `"="` headers after column inserts, so _opportune filters_ returned empty → every row followed the no-yellow path (single naked eval then jump to next tab without ever filling yellows).

6. **`LIVE_DELTA`/`LIVE_SHARPE` formulas left in template screws up fill.** `H`/`I` not cleared at open left `VLOOKUP`/`IF` formulas in data rows, which interfered with `_hdr_col_map` and caused `E2` to be detected as numeric instead of header string, collapsing the `E2/E3` distinction.

7. **`STDEV_SLOPE_SIZING` still in `SWITCH_SHEETS` without being skipped → 13-tab loop stall.** `STDEV` wiring is half-finished (`compute_regime_sizing_mult` with per-TF maps and NPZ `stdev_edge_*`/`stdev_slope_*` not on all hosts) and its 34 rows stall; until removed via `SKIP_SHEETS` the workbook never advanced past the first tab.

### 6.2 What the spec demands to unstick it

- Restore `BEST→C` ingestion (no guard), fix parser for single-value `C`, clear `H/I` formulas at open, resolve all columns via row-2 headers, zip-validate every `_atomic_save`, keep `STDEV` in `SKIP_SHEETS`, and keep NPZ in RAM. Refill lost XLS from JSON truth via `tools/v15_refill_from_json.py` (`_atomic_save` validated) so compute is never lost.

**First priority at any moment:** If `E2=BASELINE/0`, `C` empty, or `F/G None` for a `sym_side` with `done>0`, **stop herd**, fix `v15_pilot.py` to restore the spec above, validate `E2` numeric via `_validate_e_chain_and_yellows`, and refill XLS from JSON — before any other work.

---

## 7. v12_quick_engine — STALL FUNCTIONS THAT BLOCK THE ENTIRE WORKBOOK

`v12_quick_engine.py` (22 472 lines, 851 real reads) is `v15_pilot`'s inner loop — every yellow cell does `evaluate_prepared_sanitized → simulate_one → compute_*_signals → _batch*_template_wiring`. Functions that stall >10 s on common NPZ block the whole workbook because `v15_pilot`'s `ThreadPool 16` waits on them and the per-yellow `10 s` guard must fire to prevent hang.

### 7.1 Known stall-prone areas

- **`_batch1.._batchN_template_wiring()` (wiring blocks for 600+ switches).** Each batch tests `abs(thr-def)>1e-9` or `bool(getattr(cfg, "X_ENABLED"))` then touches NPZ arrays (`_safe(npz, "wt1_3m", n)`, `atr_1h`, `adx_1h`, etc.). When a switch's NPZ key is missing and fallback is `close`/`zeros`, the mask logic still branches through `&`/`|` over `n=10k` arrays — cheap per call but multiplied by **all batches for every candidate** (2–5 batches × 3800 rows × ~5 yellows = ~75k batch invocations). Any batch that does `np.arange(n) % 20 == 0` or multi-TF loops without early-exit stalls proportionally. Keep each batch to **constant-time mask ops only** — no per-bar Python loops, no `safe` fallback that recomputes `close` repeatedly.

- **`compute_regime_sizing_mult()` (STDEV ladder).** Per-TF `_stdev_max_map {'D':10,'4h':4,'1h':2,'15m':1.5} × edge × slope_mult × lookback × band`, clipped `[MIN,MAX]`. Requires `stdev_edge_*`/`stdev_slope_*` in NPZ (243 files on S1, missing on `ZECUSDC` until `backtest_v8_precompute.py --symbol ZECUSDC --mode crypto`). When missing, fallback recomputes from `close` has been the widest stall.

- **`compute_entry_signals` / `compute_exit_signals` / `compute_augment_signals` / `compute_reduce_signals` / `compute_reentry_blocks`.** Each calls dozens of `vec_decisions.*` gating predicates. `vec_decisions` that consult `dc_position_15m`/`wt_velocity_1h` with TF string mismatch branch (`if _tf != '15m'`) and per-bar `np.arange` modulate exit density — must remain vector masks.

- **`simulate_one` ledger loop.** Vector masks are legit; per-trade Python loops are not. Any new switch that introduces per-position state (`entry_price`, `opened_at`, `max_gain` dicts) must stay in NumPy — dict-mutating paths stall and desync from `backtest_v12_engine`.

### 7.2 Contract for any new wiring

- Wire via `vec_decisions/` predicate + single call in `v12_quick_engine` — never re-implement inline in both engines.
- Guard with **10 s per-yellow timeout** in `v15_pilot` (`YELLOW_TIMEOUT`, `_per_cell_hard_limit`, `per_cell_timeout_sec`). On timeout: mark cell + tab `RED`, write reason, continue to next yellow or next TAB (see §5.7). Never erase, never hang.
- `preload_prepared()` must succeed for the `sym_side` before the workbook starts; on preload fail retry once, else skip the `sym_side` but log — never fall back to per-row disk reload in loop.

---

## 8. WORKBOOK FILL — SUMMARY TABLE (pilot, not formula)

| Column | Header (row 2) | Per-row value | Rule |
|---|---|---|---|
| C | `override` | `switch + options + yellow-filter settings` **ONLY IF** pos delta inside yellow box (`>1e-9`) | Pos-only, bold if non-default, orange never above white |
| E | `BASELINE` (row 2 header preserved, `E3 = baseline_gain`) | `cumulative_before` (previous winning gain) | Self-monitor checks `E3` numeric, restores `E2` header if corrupted, aborts only after 3 fails. Blank unless POS promoted |
| F | `HUSTLE_DELTA` | `vec_gain - baseline_gain` (vs baseline) | `Arial 10 left`, blue `#4472C4` header, white bold per row, `auto width+2 cap30 height15`, every row float not `VLOOKUP` |
| G | `VECTOR_DELTA` | `delta_best = vec_gain - cumulative_before` (vs cum, per-yellow `sum_pos`) | Float, green `006100` pos / red `FFC7CE` neg, every row filled pos or neg, never leave `VLOOKUP` |
| H | `LIVE_DELTA` (col 8) | `live_gain - cumulative_before` (parity live) | **Per-workbook** written via `_write_per_row_HIK` after complete — blank per row until complete |
| I | `LIVE_SHARPE` (col 9) | `live pool_sharpe` per row | Per-workbook via `HIK` |
| K | `PER_ROW_FILTERS` (col 11) | Comma-joined pos yellows for that switch | Per-row via `HIK` |
| L:BI | Yellow headers row 2 (`col_by_header` lookup) | Per-yellow delta inside yellow cell vs `cumulative_before` | Pos-only added to `C/K/G` via `sum_pos>1e-9`, all yellows for row calculated (no cap), `0.0` backstop for empty |

**Sequential fill:** 12 tabs (`STDEV_SLOPE_SIZING` in `SKIP_SHEETS`, never calculated, sheet stays but skipped). `worst_first` / `cycle` / `shuffle` modes supported; `cycle` rotates deque on NEG delta. Every row in every tab filled in order pos or neg before next tab. Hustle beam `width 64 depth 10` + exhaustive `top12` subsets finds max combination vs `baseline+cum`. **Never ditch a `sym_side` halfway** — every `sym_side` must run to the last sheet even if 12 sheets are NEG (`SHEET NEVER ABANDONED`).

**E-chain guard:** Baseline written to `E3` (float), `E2` header `BASELINE` preserved via `ws.cell(row=3,col5)=baseline` and self-monitor that checks `E3` numeric and restores header without overwriting numeric. `_validate_e_chain_and_yellows` asserts `delta==vg-cum` and `new_cum ≥ old`.

---

## 9. LAST-3-DAY INGEST, CHARTS, 365D, PARITY, SYNC

- **Last-3-day ingestion:** Before baseline, pilot searches `SPREADSHEETS/` + `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` for BEST overrides for that `sym_side`. `cumulative_overrides` / `hustler_best.json` best wins over defaults (never `if k not in`). Done entries from `progress.json` repopulate yellows and are respected via `respect STDEV/shuffle max delta` logic; `_atomic_save` `zip≥10` valid prevents BadZip loss. S1 is writer, Mac mirror via `sync_s1_to_mac.sh` — progress json correlation via `_v14_progress.json` per `sym_side`.

- **Charts:** Per-sheet `write_zoomable_chart(symside, sheet, overrides, 30)` and per-complete `write_zoomable_chart(symside, None, cumulative_overrides, 30, suffix='30D_REAL_ZOOMABLE')` + `30D_BIGGEST_DELTA_ZOOMABLE`. Offline `file://` Chart.js 4.4.1 zoom/pan with `bh` and `gain` in title/filename (`{sym}_bh{gain}_30d_matrix.xlsx` and HTML `title: bh {bh:.2f}% gain {gain:.2f}%`). 365D chart via `suffix='365D_REAL_ZOOMABLE'` after 365D rerun.

- **365D robustness:** After 30D greedy+hustle, pilot prepares 365D NPZ via `prepare_batch(sym,365)`, evaluates best vs baseline via `evaluate_prepared_sanitized` and live parity, checks `365D delta <50% of 30D delta` warns overfit, `trades<30` diagnostic-only, `DD/sharpe` gates. Creates `*_365d_matrix.xlsx` with `bh/gain` in filename and `_BASELINE_METRICS` rows for 365D metrics. No promotion without pos gain unless 30D pos or >`bh`.

- **Parity:** Every pos delta verified via `live_evaluate(sym, overrides, 30)` (scalar bar-by-bar) vs `vector_evaluate_cached` (vector, `V12_NPZ_CACHE=32`, RAM via `preload_prepared`). `parity_ok` requires trade ratio `0.80..1.25` and gain mismatch `<0.5 pp` and `<15%`. Per-row parity fail marks red and logs `flags.md` without aborting sheet. Final switch-by-switch live verification on winning set.

- **Sync:** Edit only on Mac, then `rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new"` to `~/binance-sandbox/` on S1 (canonical) + S5, `md5sum` verify. S1 NPZ `473×31 G` (16c 30 Gi), Mac `134×10 G` never backtests except `--dry-run`.

---

## 10. METRICS — HONEST, NO LIES (STRESS-TEST RULES)

`tools/opt/metrics.py:compute(events)` → `gain_pct = sum(pnl_$)/peak_concurrent*100`, `pool_sharpe = mean(per_trade_returns)/stdev`, `TIM=held/window*100`, `DD=peak-to-trough/peak*100` capped 100%, gates `pool_sharpe>0.5 interim (>1.0 real)`, `gain/mo>20%`, `≥10× B&H`, `TIM 20-80`, `DD<30`, `30/mo` crypto. No annualization `sqrt(252)` — banned. Sample floor `≥48 crypto` or `≥100 stocks` `>1yr` `≥30 trades/sym` else `[DIAGNOSTIC ONLY]`.

Beat ideas to death: add friction (1.5–2× slippage, worst-case fills), seek **plateaus not peaks** (profitable across 50–150% param range, not at one spike), walk-forward out-of-sample, multiple regimes. Time allocation 20% ideas / 80% breaking. See `backtest-expert` skill `references/methodology.md`.

---

## 11. MACHINE ROLES & CONNECTION — BEFORE ANY SSH

`Mac` `Darwin` `/opt/anaconda3/envs/binance_env/bin/python` `134×10 G` — **LIVE trading + dashboard only, never backtests** (except `--dry-run`/`--allow-mac`).

`S1` `157.180.125.52` / `s1-int 127.0.0.1:2201` (`ssh -fNT s1-sftp` ControlMaster `~/.ssh/cm-s1-int`, fallback `157.90.168.35` gateway, `10.0.0.3`) `16c 30 Gi 79 G free` `473×31 G` — **BACKTESTS ONLY**.

**Before any ssh:** `ssh -fNT s1-sftp` else `Connection refused 127.0.0.1:2201`. Try both `s1-int` and `s1-pub` before declaring S1 down. `~/binance-sandbox` canonical — never hardcode, use `Path(__file__).resolve().parents[1]`.

---

## 12. TEMPLATE MAINTENANCE — V15_AVG_DELTAS

Defaults (column B bold) only change when `V15_AVG_DELTAS.xls` recalculates `AVG DELTA` / `POS_SYM` and reorders rows `worst_first` while keeping **entire column content together** — yellow cells for a row always stay with same switch name when row order changes. Never change bold/regular of `default` column outside this maintenance. `is_default` (col L) is backup if bold lost.

---

## 13. BEATING IDEAS TO DEATH — STRESS TEST BEFORE PROMOTION

No strategy is enabled on real money without: full sweep proof + paper days + per-trade parity + stress tests (param sensitivity 50/75/100/125/150%, execution friction 1.5–2× slippage, time robustness year-by-year, sample ≥100 trades ideal). Out-of-sample must be ≥50% of in-sample or abandon. See `backtest-expert` skill for evaluation rubric (`reports/backtest_eval_*.json`).

---

*End of bible — if a procedure above conflicts with older text, this wins.*
