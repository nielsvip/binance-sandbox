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

## 14. GREEDY DELTA + BASELINE SEMANTICS — THE LAW OF THE SHEET (2026-09-29)

This is the single most misunderstood part of the system. Read it before touching `v15_pilot.py` delta/baseline logic or the templates.

### 14.1 Bold = default = LIVE = the baseline

- Every switch (col A) and every yellow filter (`L:BI` headers) has a **default** value. In the template that default is the **bold** cell (col B for switches; the bold option inside each yellow group).
- **The bold/default values ARE what is running live right now.** The baseline of the sheet is the strategy evaluated with all switches/filters at their bold/default values (plus any promoted best-overrides from a previous run for that exact `SYM_SIDE`).
- Therefore: **setting a switch/filter to its own default value MUST produce a delta of exactly 0.** You are just reproducing the baseline. This is an integrity invariant, not a bug.
- **If a bold/default flip produces a non-zero delta, the system is broken** — it means the baseline computation and the override-application path disagree (a determinism/idempotency fault). Treat any such case as a P0 (see §21).

### 14.2 What the sweep actually tests

- The sweep tests **flipping a switch/filter to a NON-default value** to discover whether that change improves the strategy.
- A non-default flip is *expected* to change the trade ledger and therefore produce a **non-zero** delta (positive or negative).
- **Negative deltas are normal and fine.** A negative delta simply means "that change is worse" — it is **NOT summed into the baseline**, and the next row's `E` (BASELINE) stays blank. The candidate is not promoted; `C` is not filled with it.
- **Positive deltas are promoted greedily:** `E_next = E + G`, `C` gets the switch (+ its positive yellow headers), and `cumulative_overrides` advances.
- Consequence of the greedy rule: **a finished sheet's gain can only climb from the baseline.** `final_gain = baseline + Σ(positive promoted deltas)`. A finished sheet whose headline gain is *below* its baseline is impossible if the accumulation is correct — if you ever see that, the accumulation (not the baseline sign) is the bug.

### 14.3 When a NON-default flip legitimately reads 0 — investigate, do not assume "unwired"

A non-default flip that returns exactly 0 is a signal to **investigate**, in this order (never jump to "the function is unwired"):

1. **Candidate == effective baseline value.** If the running baseline (prior-best overrides for this `SYM_SIDE`) already holds that switch at that value, re-stating it is a no-op → honest 0. Check `cumulative_overrides` for the current value before concluding anything.
2. **An upstream gate/filter is suppressing the trades the switch would touch.** If a filter in the baseline blocks the entries/exits that this switch governs, flipping the switch changes nothing because those trades never exist. This is the **entry-blocker class** and is the FIRST thing to check (see §17 — a QuickConfig↔config parity gap can silently enable an entry-gate in the sweep baseline that live does not run).
3. **The switch does not bind on THIS symbol's price path in THIS 30-day window.** e.g. an HTF direction gate whose mask happens to already include every entry bar for this symbol → no change → honest 0 for this symbol, real delta on another symbol/window.
4. **Candidate equals default** (the bold-variant row) — expected 0 by design; that row draws its value from its **yellow filters**, not from the naked switch (see §5.4 no-yellow / yellow rules).

Only after 1–4 are excluded is "the engine path for this switch needs work" a valid conclusion — and the fix is genuine signal logic in `vec_decisions/` + one call site in `v12_quick_engine`, never a `getattr` no-op (see §19).

### 14.4 The units — "cells" vs "switches"

- A **switch** is one name in col A (e.g. `WT_LOWER_CROSS_EXIT_TF`). There are on the order of ~200 switch names across the 12 active tabs.
- A **cell** is one evaluated quantity: each switch row spawns `1 naked + K yellow` candidate evaluations, and there are multiple candidate *values* per switch. Across 12 tabs × rows × candidate values × all `L:BI` yellows this is **>5000 cells**.
- When the operator says "5000+ cell fills," that is the cell count (F/G/E + every `L:BI` yellow across all rows of all sheets), not the switch-name count. Always answer in the operator's unit.

---

## 15. THE CORRECT BASELINE STRATEGY — DC-CHANNEL EXITS (2026-09-29)

The intended baseline strategy (what bold/default should encode) as stated by the operator:

- **Daytrade flag stays ON.** It was **not** turned off. What changed (days ago) is the daytrade *rules*: fixed-% exits were **deleted** and replaced with DC-channel rules. Do not "fix" this by disabling daytrade.
- **The only baseline exits are:**
  1. **LOSS exit** — price breaches `dc_low_{TF} − 0.25%` (long) / `dc_high_{TF} + 0.25%` (short). This is the worst-case stop.
  2. **GAIN exit** — price reaches `0.1% below dc_high_{TF}` (long) / `0.1% above dc_low_{TF}` (short). Take-profit near the channel edge.
  3. **`wt1_15m` lower cross** — WT turning against the position on 15m (long: wt crosses/closes lower than previous; short: vv).
  - `{TF}` is one of `15m / 1h / 4h` (which TF, and combinations, is exactly what the sweep tests).
- **No fixed-% stop/target.** Fixed `DC_DAYTRADE_STOP_PCT` / `DC_DAYTRADE_TARGET_PCT` are eliminated when a DC-channel TF list is active. Do not sweep the fixed-% params as if they matter under the DC-channel baseline — they are overridden.
- **Any other exit** (MTF trails, gap MOC, breakeven erosion, spike-fade, etc.) is **OFF in the baseline** and is only ever adopted if the sweep proves a large positive delta for it. The operator's expectation is that most will not beat the DC-channel baseline.
- The engine implements the DC-channel daytrade path via `DAYTRADE_DC_TARGET_TF` / `DAYTRADE_DC_STOP_TF` (+ `DAYTRADE_DC_TARGET_BUFFER_PCT` 0.10 / `DAYTRADE_DC_STOP_BUFFER_PCT` 0.25) and the technical channel exit via `TECHNICAL_DC_TARGET_TF` / `TECHNICAL_DC_STOP_TF`; the WT-cross exit via `WT_LOWER_CROSS_EXIT_TF`. These are the switches that genuinely move a DC-baseline symbol.

---

## 16. THE SIMPLE SYSTEM vs THE BIG SYSTEM — SAME ENGINE

There are two sweep systems running in parallel; they share **one engine** and must not be confused.

- **Big system** = `v15_pilot.py` + `TEMPLATE_{STOCKS,CRYPTO}_{LONG,SHORT}.xlsx` — the full 12-tab, >5000-cell workbook per `SYM_SIDE`. This is the one this bible mostly describes.
- **Simple system** = `tools/dc_simple_8_sweep.py` — a small, curated sweep of only the switches that dominate a DC-channel strategy: `TECHNICAL_DC_STOP_TF`/`TECHNICAL_DC_TARGET_TF` (the technical channel EXIT), `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` (the daytrade ENTRY channel), `WT_LOWER_CROSS_EXIT_TF`, and `EMA_9_21_FILTER`. It sweeps ~64 EXIT×ENTRY combos + WT + EMA. `STOP_BUF=0.25`, `TARGET_BUF=0.10`.
- **Both call the identical engine:** `dc_simple_8_sweep.eval_gain()` builds a `v12_quick_engine.QuickConfig` (with `apply_tradier_defaults()` for stocks), applies overrides, sets `cfg.MODE`, and calls `V.simulate_one(sliced, sym, is_long, cfg)`. `v15_pilot` funnels through `evaluate_prepared_sanitized → simulate_one`. **Same rules, same math.**
- **Implication:** any rule proven in the simple system is *already in the engine* the big system uses. Divergence between the two is almost always **which switches the template selects to sweep** and **whether the big-system baseline is in config-parity** (§17), not a separate rule codebase.
- **Coordination:** the simple system is owned by a separate operator/agent and produces the missing `SYM_SIDE` results before market open. Do not touch its files or its queue. The big system (this bible) is a separate lane. `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` exist and are wired in the engine (`v12_quick_engine` ~L5025, ~L9041-9064) — if the big template does not sweep them, that is a template-coverage gap to add, not an engine gap.

---

## 17. QUICKCONFIG ↔ CONFIG / CONFIG_TRADIER PARITY — MANDATORY, BUT CURATED

The sweep baseline is only trustworthy if `QuickConfig` reproduces the live configuration. This is a hard requirement and a recurring failure mode.

### 17.1 The three configs

| Config | Class | Fields | Used for |
|---|---|---|---|
| `config.py` | `Config` | ~3269 | LIVE crypto (`ez_manage`) |
| `config_tradier.py` | `TradierConfig` | ~1611 | LIVE stocks (`tradier_manage`) |
| `v12_quick_engine.py` | `QuickConfig` (+`apply_tradier_defaults()`) | ~3420 | BACKTEST both venues |

- Crypto sweep baseline = `QuickConfig()` should match `Config()` on shared strategy fields.
- Stock sweep baseline = `QuickConfig()` + `apply_tradier_defaults()` should match `TradierConfig()` on shared strategy fields.

### 17.2 How to audit parity (read-only, safe)

```python
import dataclasses as dc, v12_quick_engine as V, config as C, config_tradier as CT
qf={f.name:getattr(V.QuickConfig(),f.name) for f in dc.fields(V.QuickConfig())}
qt=V.QuickConfig(); qt.apply_tradier_defaults(); qtf={f.name:getattr(qt,f.name) for f in dc.fields(qt)}
cf={f.name:getattr(C.Config(),f.name) for f in dc.fields(C.Config())}
ct={f.name:getattr(CT.TradierConfig(),f.name) for f in dc.fields(CT.TradierConfig())}
# compare shared keys; round floats to 9 dp; report mismatches
```

As of 2026-09-29 this showed **~652 crypto and ~365 tradier strategy-field mismatches** (after excluding ~73/23 infra/path fields). This is the parity gap that must be closed for the sweep baseline to equal live.

### 17.3 CRITICAL — a blind full-sync BREAKS the backtest

**Do NOT copy every live value into QuickConfig.** Verified 2026-09-29: setting all 365 tradier strategy fields to their `TradierConfig` values produced **0 trades / gain 0** on GDX_LONG. Reason: live config enables **live-only, non-vectorizable entry engines** that cannot run in the vector backtest and therefore block all entries when forced on. Known live-only fields to **exclude** from any sync (grow this list as found):

- `LIVE_ENTRY_ENGINE_ENABLED`, `LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED`, `LIVE_5m_trading_ENABLED`, `MTF_ARMED_ENTRY_ENABLED`, `REENTRY_LIVE_MONITOR_DC_BREAK_ENABLED` (live monitors / live entry engines)
- All `ABLATION_DISABLE_*` (research toggles — `Config` may hold them True; they are NOT the live strategy and must not be copied into the backtest baseline)
- Infra: any `*_PATH/FILE/DIR/CACHE/TOKEN/KEY/URL/HOST/PORT`, and `BASE_PATH`, `BASE_TF`, capital-normalization bases (`ATR_PARITY_EQUITY_BASE_USD`, `START_POSITION_SIZE`) that legitimately differ for backtest normalization.

### 17.4 Curated sync procedure (the ONLY safe way)

1. Snapshot the mismatch set (§17.2) to JSON.
2. Partition into: **strategy params** (thresholds, TFs, sizing multipliers, gate enables that are vectorizable) vs **exclusions** (§17.3).
3. Sync strategy params only, **live value → QuickConfig** (live is truth). For fields where `Config` and `TradierConfig` differ, the raw `QuickConfig` default matches `Config` (crypto) and `apply_tradier_defaults()` sets the `TradierConfig` value (tradier overlay).
4. **Verify after every batch:** (a) `python -c "import py_compile; py_compile.compile('v12_quick_engine.py', doraise=True)"`, (b) re-run the mismatch audit → strategy mismatch count drops toward 0, (c) run `evaluate_sanitized("GDX_LONG",{},30)` and `AXTI_LONG`/`AXTI_SHORT` — **baseline trades must stay sane (not collapse to 0)**. If any field zeroes the backtest, move it to the exclusion list.
5. Deploying the synced engine to S1/S2 is an **engine cut** — see §25 (coordinate, archive pre-cut chains, never mid-sweep silently).

### 17.5 The entry-blocker class (operator's theory, confirmed real as a category)

Config-parity gaps can leave **entry-gating filters ON in the sweep baseline that live runs OFF** (e.g. `ADX_REGIME_FILTER_ENABLED`, `TOP_OF_RANGE_BLOCK_ENABLED`, `COUNTER_TREND_ADD_BLOCK_ENABLED`, `OPEN_RATE_BREAKER_ENABLED`, `GOLDEN_RULE_HTF_VETO_ENABLED`, the `TR_*4H_GATE_ENABLED` trio, `WT_DC_LONG/SHORT_ENABLED`). When such a gate suppresses entries in the baseline, **every downstream switch that acts on those entries reads 0 delta** — not because it is unwired, but because the trades it would touch never open. Whenever a broad swath of switches reads 0, **audit config parity for spurious entry gates FIRST** (§17.2). (Note: turning a gate ON in QuickConfig only bites if the gate's mask actually binds on the symbol; verify empirically — a mismatch in a flag that does not bind is a parity cleanup, not the cause of a specific 0.)

---

## 18. INVESTIGATING A ROW OF ZEROS — PROTOCOL (NEVER GUESS "UNWIRED")

When a `SYM_SIDE` sheet shows many 0-delta cells:

1. **Read the delta-log** for that `SYM_SIDE` (§20) — get the exact per-cell gain_pct and delta, and the baseline (`cum_before`).
2. **Confirm the baseline is right:** determinism + idempotency (§21). If a default flip is non-zero → P0 baseline bug, stop.
3. **Config parity (§17):** are entry gates ON in the sweep baseline that live has OFF? Fix parity (curated), re-run.
4. **Candidate vs effective baseline:** for each 0-delta non-default flip, is the candidate equal to the value already in `cumulative_overrides`? If yes, it is a no-op (honest 0) — the template should not sweep the current value.
5. **Binding on this symbol:** does the switch's mask/predicate actually change any real trade on this symbol's 30-day window? Test on 2–3 other symbols; if it binds elsewhere, the 0 is symbol-specific and honest.
6. Only if 2–5 are clean and the switch changes nothing on any symbol is genuine wiring work indicated — and it is done in `vec_decisions/` + one engine call site, proven by a **changed trade ledger** (§19), never by re-adding read-only scaffolding.

Do not report "N switches are dead/unwired" as a conclusion — it conflates no-op candidates, parity-suppressed entries, symbol-non-binding, and genuine gaps, and it has been wrong. Report per-cause counts from the delta-log instead.

---

## 19. NO FABRICATED DELTAS — THE NO-LIES RULE FOR THE SWEEP

- **A non-zero delta is credible ONLY when the trade ledger changed.** Identical yellows across a row, or a delta with no corresponding change in opens/closes/reduces, is a filter no-op and must read 0 — honestly.
- **FORBIDDEN:** any construct whose purpose is to make a switch *appear* to do something without changing trades. Specifically:
  - `_ = getattr(cfg, "X"); _ = cfg.X` read-only "wiring" (audit-defeating; a `_batchN_template_wiring`-style function of pure attribute reads was removed 2026-09-28 precisely because it faked "used" status while changing no trades — do not reintroduce it).
  - `entry_mask[0] ^= True # guarantee ledger distinct`, `np.arange(n)%k`, hash-of-param bit-flips, or any synthetic perturbation added to force a non-zero/distinct delta. These fabricate deltas and are the exact class of lie that the NO-LIES mandate exists to prevent.
- An **honest 0** (no-op candidate, non-binding switch, parity-suppressed entries) is correct and must be preserved. Making the sheet "show no zeros" by fabrication is a NO-LIES violation, not a fix.
- To make a switch genuinely produce deltas, implement its **real signal logic** (a `vec_decisions/` predicate returning a real NumPy mask that gates entries/exits/augments/reduces), call it once in `v12_quick_engine`, and prove the delta corresponds to a changed ledger (opens/closes differ).

---

## 20. THE DELTA LOG — GROUND TRUTH FOR EVERY EVAL

`v15_pilot` writes one JSON line per evaluation to `{PROGRESS_DIR}/v15_delta_log/{SYM}_{SIDE}_jump.jsonl` (default `PROGRESS_DIR = data/reports/lifecycle_pilot/`; override with `V15_PROGRESS_DIR` for isolated proofs). Each record:

```json
{"ts":"...Z","sym_side":"GDX_LONG","sheet":"EXIT_STRUCTURAL","row":27,"switch":"...","cand":"...",
 "label":"naked|<yellow header>","fn":"tools.opt.v12_pilot.evaluate_prepared_sanitized",
 "gain_pct":..., "trades":..., "valid":true, "cum_before":..., "delta": gain_pct-cum_before,
 "secs":0.07, "cached":false, "err":""}
```

This is the **audit source of truth**. Standard audits:

- **Fill count / zero rate:** count records; `delta==0` (`abs<1e-9`) vs non-zero; % non-zero per `label=="naked"` and overall.
- **Distinct outcomes:** `len(set(round(gain_pct,6)))` — how many distinct strategy states the sweep reached.
- **Duplicate deltas:** `collections.Counter(round(delta,6))` — a delta value repeated across unrelated switches often indicates a shared fallback path; investigate.
- **Timing:** `secs` distribution; count `>0.1s` (cached target ~0.07s) and `max` (see §22).
- **Which switches move the ledger:** `Counter(switch for r in records if label=='naked' and abs(delta)>=1e-9)`.

**Verified baseline (2026-09-29, GDX_LONG):** the 16:05 real run and a 00:15 isolated re-run were statistically identical — ~1117 naked flips, ~2% non-zero, the same ~11 switches producing deltas, ~17 distinct gains. This is the steady-state profile for this symbol under the current baseline; it is not a regression. "Cells filled" means every cell was *written* (E/F/G/K + all `L:BI`), which the pilot still does — the delta *content* being mostly 0 for a given symbol is a baseline/coverage property, audited via this log.

---

## 21. INTEGRITY CHECKS — RUN BEFORE TRUSTING ANY SWEEP

Two invariants must hold; both are quick and read-only. If either fails, stop and fix before sweeping.

1. **Determinism:** the same overrides evaluated twice must give byte-identical `gain_pct`.
   ```python
   from tools.opt.v12_pilot import evaluate_sanitized as ES
   assert ES("GDX_LONG",{},30)["gain_pct"] == ES("GDX_LONG",{},30)["gain_pct"]
   ```
2. **Idempotency (baseline reproduction):** setting any switch to its own default value must give delta 0 vs the no-override baseline.
   ```python
   b=ES("GDX_LONG",{},30)["gain_pct"]
   for sw,dv in {"WT_15M_BOUNCE_OPEN_ENABLED":False,"STDEV_SLOPE_SIZING_ENABLED":True}.items():
       assert abs(ES("GDX_LONG",{sw:dv},30)["gain_pct"]-b) < 1e-9  # else P0 (§14.1)
   ```

Verified 2026-09-29: both PASS on the current engine (determinism exact; default re-application = exactly 0). When comparing a running sheet's deltas against "cand==default," compare `cand` to the **effective baseline** (`cumulative_overrides` for that `SYM_SIDE`), NOT to a fresh `QuickConfig` default — a `SYM_SIDE` that was tested before starts from prior-best overrides, so a value that differs from the fresh default may equal the effective baseline (honest 0), and a fresh-default comparison will produce false "integrity violation" reports.

---

## 22. TIMING — 0.07s CACHED, 0.1s TARGET, 10s HARD GUARD

- With NPZ in RAM (`ALL_PREPARED`, `V12_NPZ_CACHE=32`, `evaluate_prepared_sanitized`), a single cached eval is **~0.07s**; a cold/first eval ~0.11s.
- **Target: every cell fills in ≤0.1s.** A cell that takes materially longer is almost always doing a **per-row disk reload** or a per-bar Python loop — fix it (§7). Verified 2026-09-29 GDX_LONG: 26k evals, mean well under 0.1s, only ~1.7% exceeded 0.1s with a max of 0.23s (acceptable tail under CPU contention; investigate if the tail grows).
- Two distinct guards exist and serve different purposes: the **10s per-yellow hard stall guard** (`YELLOW_TIMEOUT`, marks the cell + tab RED and moves on — never hang; §5.7) and the **0.1s performance target** (a cell routinely over this is a perf regression to fix, not a stall to RED). Do not confuse them.
- The filler must **never get stuck on a cell**: on stall, RED the cell, log to `data/reports/v15_flags/{SYM}_{SIDE}_flags.md`, continue. Monitors sweep the flags every ~10 min so other agents can pick up RED cells for repair.

---

## 23. PROOF PROTOCOL — DEMONSTRATE ONE CLEAN SHEET PER SERVER

To prove the pipeline end-to-end without disturbing the running herd:

1. **Isolate:** launch with `V15_PROGRESS_DIR=/tmp/proof_{sym}` so the proof's progress JSON + delta-log do not collide with the herd/cron sync. Add `V15_SKIP_LIVE_AT_DONE=1` to defer the (slow, sometimes parent-killing) live-verify at DONE.
2. **One symbol, max workers:** `v15_pilot.py --sym-side {SYM}_{SIDE} --template SPREADSHEETS/TEMPLATE_{cat}_{side}.xlsx --seq-mode worst2best --window-days 30 --vector-only --workers 14` (16-core box → 14–15 workers). `_n_proc = min(workers, cpu_count-1)`.
3. **Server placement:** s1 = crypto, s2 = stocks by convention; but s1 has the stock NPZs too and (2026-09-29) far more free RAM than s2 (s2 was ~1 GB free vs s1 ~22 GB). If s2 is RAM-starved, run a stock proof on s1 rather than risk OOM-killing s2's in-flight pilots — never add load that could kill a running filler.
4. **Verify the fill (from the delta-log + xlsx):** every row wrote F/G + all `L:BI`; no None; timing ≤0.1s median; baseline correct (determinism + idempotency); greedy `E` chain monotonic; and the finished workbook is named with `bh` and `gain` and has a zoomable chart on the Mac (`tools/generate_zoomable_charts_mac.py`, output `SPREADSHEETS/charts/{stem}_zoom.html`).
5. **"Good gains":** the headline `gain` is the finisher's fresh re-anchored full-set evaluation under the pilot's own engine — never a chained/mixed-engine number.

---

## 24. FILE & EDIT DISCIPLINE — NEVER REVERT, EDIT ON MAC, DEPLOY IS A CUT

- **RULE 0 (repeat): never revert.** Fix forward. A backup copy in `backups/` is a safety net, not a rollback target — never overwrite a newer engine/pilot with an older one. If a past version was better, diff it and patch the *current* file forward.
- **Backup before every edit:** `cp <file> backups/before_<desc>_$(date +%Y%m%d%H%M).<ext>`.
- **Edit only on Mac** (`/Users/niels/Documents/binance`). S1/S2 copies are deployed via `rsync`, never edited in place.
- **Deploying `v12_quick_engine.py` to a box running the herd is an ENGINE CUT.** In-flight pilots keep their loaded engine; new launches take the new one → a chain that resumes across the cut mixes engine arithmetic (`engine_mixed_chain`). Never resume a chain across a cut — archive the pre-cut progress JSONs and let chains re-baseline on the new engine. Announce engine batches to peer sessions before pushing; push modules/config/templates first, engine last; `md5sum` + `import v12_quick_engine` on every box before pilots launch.
- **`vec_decisions/` is largely untracked by git** (only a handful of files tracked). Do not rely on git history for it — check file mtimes and the census scoreboard.
- **Do not run `tools/sync_backtest_bible.sh` while another agent is editing on the servers** — it rsyncs to S1 and can collide. Sync the bible only when the server side is quiescent.

---

## 25. ENGINE ANATOMY — WHERE THINGS LIVE (v12_quick_engine.py)

For future wiring/parity work, the load-bearing structures (line numbers drift — grep, do not trust exact lines):

- `class QuickConfig` — the dataclass of all backtest config fields; `apply_tradier_defaults(self)` overlays stock-venue values (sets `MODE="tradier"`, `BASE_TF="5m"`, enables the stock entry stack, `DC_DAYTRADE_ENABLED=True`, etc.).
- `simulate_one(npz, sym, is_long, cfg, force_initial_seed=False)` — the vector ledger loop. Builds `entry_sig / exit_sig / augment_sig / reduce_sig` from `compute_*_signals`, then walks bars: `_open()` appends an `OPEN` event; augments append `AUGMENT` events; reduces append `REDUCE` (to both `events` and, as zero-qty rows, `trades`); closes append `CLOSE` to `trades`. **OPEN/AUGMENT live in `events`; CLOSE/REDUCE live in `trades`.** Trade metrics use CLOSE rows only.
  - Note on charts: OPEN (events) and CLOSE/REDUCE (trades) are separate lists; when merged for a chart they must be sorted by `(bar, then OPEN<AUGMENT<REDUCE<CLOSE)` or a same-bar reduce can render *before* its buy ("reduce before buy" is a chart-merge ordering artifact, not a sim bug — the sim only reduces an existing position).
- Gate families are applied in `simulate_one` via `vec_decisions.*` calls, each wrapped in `try/except: pass` (a broken import in a family silently no-ops the whole family — check by calling the functions directly with exceptions exposed; verified 2026-09-29 that `filter_tf_gates`, `wave4_families`, `generic_filter_tf` execute and return real masks, so there is no swallowed-exception cascade at present).
- `DAYTRADE_DC_*` / `TECHNICAL_DC_*` / `ENTRY_DC_*` / `WT_LOWER_CROSS_EXIT_TF` are the DC-baseline levers (§15). Fixed-% daytrade params are inert when a DC-channel TF list is active.

---

## 26. SESSION LEARNINGS LEDGER — 2026-09-29 (operator-corrected)

Facts established this session, kept so they are not re-derived:

1. Baseline delta+baseline accumulation math is **correct**; integrity (determinism + idempotency) **passes**. The greedy rule is as in §14.
2. The correct baseline is **daytrade-on with DC-channel exits** (§15); daytrade was **not** to be turned off.
3. Simple and big systems share **one engine** (§16); `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` are wired in the engine and are a candidate template-coverage add for the big system.
4. `QuickConfig` was **out of parity** with live config (~652 crypto / ~365 tradier strategy-field mismatches); a **blind full-sync zeroes the backtest** (live-only entry engines) — sync must be curated (§17). (QuickConfig parity remediation was assigned to a separate agent on 2026-09-29; do not double-edit.)
5. The GDX_LONG delta profile at 16:05 == the profile at 00:15 (§20) — **no regression** across the handoff window; "cells filled" = cells written, delta content mostly 0 for that symbol under the current baseline.
6. Read-only `getattr` "wiring" scaffolding (`_batchN_template_wiring`) was removed as fake-audit; it must **not** be reintroduced (§19).
7. Timing is within budget (~0.07s cached; ~1.7% of evals >0.1s, max 0.23s) — no per-cell perf regression at present (§22).
8. When many cells read 0, the investigation order is parity → candidate==effective-baseline → symbol-binding → genuine gap — reported per-cause from the delta-log, never as a blanket "unwired" count (§18).

---

## 27. WORKED EXAMPLE — ONE ROW, END TO END

Concrete walkthrough of a single switch row so the contract is unambiguous. Assume `AXTI_SHORT`, tab `EXIT_VELOCITY`, current `cumulative_before = 6.20` (baseline + everything promoted so far), and the row under test is `switch = WT_LOWER_CROSS_EXIT_TF`, default (bold) `OFF`.

Rows in the template for this switch (candidate values):

- `WT_LOWER_CROSS_EXIT_TF = OFF` (the bold/default variant)
- `WT_LOWER_CROSS_EXIT_TF = 15m`
- `WT_LOWER_CROSS_EXIT_TF = 1h`
- `WT_LOWER_CROSS_EXIT_TF = 4h`

Yellow headers eligible for this switch (from `FILTER_DICTIONARY_V2`, `SPECIFIC`, overlapping token `WT`/`EXIT`), e.g. `WT_CROSS_EXIT_APPLIES_TO_WINNERS=0.5`, `EMA_9_21_FILTER_FILTER_TF=4h`, etc.

Processing:

1. **Default-variant row (`=OFF`):** naked flip to `OFF` = the current value → naked delta `0` (integrity; §14.1). The row's value comes from its yellows: evaluate `{...cum..., WT_LOWER_CROSS_EXIT_TF:OFF, <each yellow>}` vs `6.20`. Suppose two yellows are positive (+0.30, +0.12) and the rest ≤0. `G = sum_pos = 0.42`. Since `G>0`: write `C = "WT_LOWER_CROSS_EXIT_TF=OFF + WT_CROSS_EXIT_APPLIES_TO_WINNERS=0.5 + EMA_9_21_FILTER_FILTER_TF=4h"`, `F/G = 0.42`, `K = those two headers`, each `L:BI` yellow cell = its own delta (pos green, neg red). Move **down one row on this tab**; next row `E = 6.20 + 0.42 = 6.62`; `cumulative_overrides` gains those two filters.
2. **`=1h` row:** naked flip to `1h` vs `6.62` — suppose `+1.20` (real ledger change: fewer late exits). Then its yellows on top. Suppose `G = 1.20 + (pos yellows 0.05) = 1.25 > 0`. Promote: `C = "WT_LOWER_CROSS_EXIT_TF=1h + <pos yellow>"`, `E_next = 6.62 + 1.25 = 7.87`, overrides gain `WT_LOWER_CROSS_EXIT_TF=1h`.
3. **`=4h` row:** naked flip to `4h` vs `7.87` — suppose `-0.30` and no positive yellows → `G = 0` (or the negative naked if no yellows). Not promoted: `C` stays blank, `F/G` written (red), `E_next` **stays blank** (baseline does not advance), `cumulative` unchanged. Because this row had yellows evaluated and none positive, we still **move to the next tab** only when the tab's rows are exhausted; within a tab we continue down its remaining rows.
4. When `EXIT_VELOCITY`'s rows are exhausted, move to `REENTRY_WINDOWED` with the current `cumulative` (7.87) written to its first pending row's `E`.

Key invariants exercised: every row wrote `F/G` (pos or neg) and every eligible `L:BI`; `C` only for promoted rows; `E` only advanced on positive `G`; the default-variant row's value came from yellows, not from the (zero) naked flip.

---

## 28. THE 12 ACTIVE TABS — WHAT EACH SWEEPS

Order is fixed (`STDEV_SLOPE_SIZING` present but in `SKIP_SHEETS`). Lifecycle tag drives yellow eligibility (`Sheets applicable`).

1. **ENTRY_REVERSAL_BOUNCE** (`ENTRY`) — bounce/reversal openers: `BB_SQUEEZE_ENTRY_ENABLED`, `BB_PULLBACK_GATE_TF`, `WT_15M_BOUNCE_OPEN_ENABLED`, BB recovery entries. Yellows: BB/WT confirmation filters.
2. **ENTRY_BREAKOUT_CHANNEL** (`ENTRY`) — breakout/channel openers: `WT_DC_DETAILED_TF`, `DC_BREAKOUT_TF`, `DC_BREAKOUT_SCORE`, breakout retest. Largest tab (~54 base switches → ~210 rows).
3. **ENTRY_CONFIRMATION_GATES** (`ENTRY`) — HTF/alignment gates on entries: `WT_DC_HTF_GATE`, alignment gates, HTF direction confirmation.
4. **EXIT_STRUCTURAL** (`EXIT`) — structural channel exits: `TECHNICAL_DC_STOP_TF`, `TECHNICAL_DC_TARGET_TF`, DC-hard-stop TF, hopeless-exit. **This is where the DC-channel LOSS/GAIN exits (§15) are swept.**
5. **EXIT_VELOCITY** (`EXIT`) — velocity/WT exits: `WT_LOWER_CROSS_EXIT_TF`, WT-4h velocity exit, exhaustion exit, top-fade.
6. **REENTRY_WINDOWED** (`REENTRY`) — windowed reentry after exit: mandatory-reentry gates, K-not-extreme, DC-break reentry.
7. **REENTRY_ADAPTIVE** (`REENTRY`) — adaptive reentry: HLR reentry mult, breakout-leash reentry, bounce reentry.
8. **AUGMENT_TREND** (`AUGMENT`) — trend-following adds: HTF-gate-apply-to-augment, WT-4h-bounce augment, bounce-augment min-loss.
9. **AUGMENT_RISK_SIZING** (`AUGMENT`) — add sizing: partial-recovery size mult, pyramid, augment gain gate.
10. **REDUCE_PROFIT_LOCK** (`REDUCE`) — profit locks: partial-profit-lock v2, breakeven-gain-erosion, HTF-gate-apply-to-open.
11. **REDUCE_SIGNAL_RATER** (`REDUCE`) — signal-rated reduces: MI entry-struct bonus, quick-reduce-technical, signal rater.
12. **GLOBAL_RISK_GATES** (`GLOBAL_CHECK`) — portfolio/rate gates: `OPEN_RATE_MAX`, blacklist, circuit gates, bear-market mode. (`GLOBAL_RISK_GATES` has a documented VLOOKUP waiver — see §4.2.)

`STDEV_SLOPE_SIZING` (`SPECIFIC`, sizing) — skipped until `compute_regime_sizing_mult` is fully wired and `stdev_edge_*`/`stdev_slope_*` exist on all hosts.

**Yellow eligibility recap (per §5.3):** a filter is a yellow for a switch iff `Recommendation=SPECIFIC` AND (`Sheets applicable` contains the tab's lifecycle OR `ALL`) AND `Switches exactly (gates)` token-overlaps the switch AND the `FILTER=OPT` header exists in that tab's `O:BI`. Never evaluate a filter that is not yellow for the row.

---

## 29. DC-CHANNEL EXIT MATH — HOW THE ENGINE COMPUTES IT

For a DC-channel baseline (`DAYTRADE_DC_*` / `TECHNICAL_DC_*` TF lists active), per bar `i`, long side:

- **Stop level:** `stop = dc_low_{TF}[i] * (1 - STOP_BUF/100)` with `STOP_BUF = DAYTRADE_DC_STOP_BUFFER_PCT` (default `0.25`). Exit if `close[i] <= stop`.
- **Target level:** `tgt = dc_high_{TF}[i] * (1 - TARGET_BUF/100)` with `TARGET_BUF = DAYTRADE_DC_TARGET_BUFFER_PCT` (default `0.10`) — i.e. 0.1% *below* the channel high. Exit if `close[i] >= tgt`.
- **WT cross exit:** if `WT_LOWER_CROSS_EXIT_TF={TF}`, exit if `wt1_{TF}[i]` crosses below `wt2_{TF}[i]` (long) — turning against the position.

Short side mirrors: stop `= dc_high_{TF}*(1+STOP_BUF/100)`, target `= dc_low_{TF}*(1+TARGET_BUF/100)`, WT upper cross.

- **Multi-TF lists** (`"15m,1h,4h"`): the exit fires if **ANY** listed TF's condition is met (OR across TFs). The TF alias `5m→3m` is applied by `_parse_tf_list`. `OFF` disables that family.
- **Fixed-% elimination:** when a DC TF list is active, the legacy fixed `daytrade_stop/target` (`*_PCT`) are **not used** — do not sweep them expecting a delta under a DC baseline.
- **`TECHNICAL_DC_*`** is the same channel machinery tagged as a technical (structural) exit with its own TF list and buffers; `DAYTRADE_DC_*` is the intraday-entry channel. The sweep tests both TF lists to find the best channel for the symbol.

This is why `DAYTRADE_DC_TARGET_TF`, `DAYTRADE_DC_*_BUFFER_PCT`, `TECHNICAL_DC_*_TF`, and `WT_LOWER_CROSS_EXIT_TF` are the switches that reliably move a DC-baseline symbol, while fixed-% and unrelated exit families read 0 against that baseline.

---

## 30. RUNNABLE AUDIT — ZERO/DUP/TIMING FROM THE DELTA LOG

Drop-in audit for any `SYM_SIDE` (read-only; run on S1):

```python
import json, collections
f="data/reports/lifecycle_pilot/v15_delta_log/GDX_LONG_jump.jsonl"   # or /tmp/proof_*/v15_delta_log/...
recs=[json.loads(l) for l in open(f) if l.strip()]
naked=[r for r in recs if r.get("label")=="naked" and r.get("delta") is not None]
alld=[r for r in recs if r.get("delta") is not None]
nz=lambda xs:[r for r in xs if abs(r["delta"])>=1e-9]
print("records",len(recs),"naked",len(naked),"naked_nonzero",len(nz(naked)),
      "all_nonzero_pct",round(100*len(nz(alld))/max(1,len(alld)),1))
print("distinct gains",len(set(round(r["gain_pct"],6) for r in recs if r.get("gain_pct") is not None)))
print("dup deltas",collections.Counter(round(r["delta"],6) for r in alld).most_common(6))
secs=[r["secs"] for r in recs if r.get("secs") is not None]
print("timing >0.1s",sum(1 for s in secs if s>0.1),"max",max(secs) if secs else None)
print("switches that move ledger",collections.Counter(r["switch"] for r in nz(naked)).most_common(20))
```

Interpretation guide:

- **High zero-rate** → run the §18 protocol (parity → cand==effective-baseline → binding → gap). Do NOT report "unwired."
- **Few distinct gains** relative to eval count → the baseline collapses variety (e.g. an entry gate suppressing most entries) — check §17 parity.
- **A single delta value repeated across unrelated switches** → shared fallback path; verify the ledger actually changed (§19).
- **Timing tail growing** → per-row disk reload crept in (§22); confirm `ALL_PREPARED`/`V12_NPZ_CACHE` still hot.

---

## 31. CURATED CONFIG-PARITY — FIELD CLASSIFICATION RULES

When closing the QuickConfig↔live gap (§17), classify each mismatched field:

**SYNC (live → QuickConfig):**
- Strategy thresholds (`*_THRESHOLD`, `*_MIN`, `*_MAX`, `*_PCT` that are not live-only), TF strings (`*_TF`), sizing multipliers (`*_MULT`, `*_SIZE`), lookbacks (`*_BARS`, `*_DAYS`), and gate enables that are vectorizable and bind on NPZ arrays.
- Direction: crypto raw `QuickConfig` default ← `Config`; tradier overlay in `apply_tradier_defaults()` ← `TradierConfig`.

**EXCLUDE (keep QuickConfig's own value):**
- Live-only entry engines / monitors: `LIVE_ENTRY_ENGINE_*`, `LIVE_5m_trading_ENABLED`, `MTF_ARMED_ENTRY_ENABLED`, `*_LIVE_MONITOR_*` (forcing these on → 0 trades in backtest).
- All `ABLATION_DISABLE_*` (research toggles, not the live strategy).
- Infra/paths: `*_PATH/FILE/DIR/CACHE/TOKEN/KEY/SECRET/URL/HOST/PORT/WEBHOOK/CHANNEL/EMAIL`, `BASE_PATH`.
- Backtest structural/normalization: `BASE_TF`, `MODE`, capital bases used for per-trade normalization (`ATR_PARITY_EQUITY_BASE_USD`, `START_POSITION_SIZE`, `MAX_ORDER_VALUE`) unless the operator says otherwise.

**VERIFY each batch:** compile-check; re-audit mismatch count (should drop); and re-run `evaluate_sanitized` on `GDX_LONG`, `AXTI_LONG`, `AXTI_SHORT` — **baseline trades must not collapse to 0**. Any field that zeroes the backtest moves to EXCLUDE. Only deploy via a coordinated engine cut (§24).

(As of 2026-09-29 this remediation is owned by a separate agent; this section documents the method, not an action to duplicate.)

---

## 32. THE FINISHER / RE-ANCHOR / PUBLISH PIPELINE

After a workbook's 12 tabs are filled, the finisher (`tools/v15_finisher.py --watch`) publishes it:

- **Re-anchor:** the headline `gain` is recomputed as a **fresh full-set evaluation** of the final `cumulative_overrides` under the pilot's own engine — never the chained running total (which could carry mixed-engine arithmetic across a cut). If the fresh eval and the chained total diverge beyond tolerance, the sheet is stamped `engine_mixed_chain`/`CONTAMINATED` and **held, not published**.
- **Name:** the published file carries `bh` and `gain` in the filename, e.g. `AXTI_SHORT_bhm1p03_gain11p29_30d_matrix.xlsx` (`m` = minus, `p` = decimal point).
- **Chart:** `tools/generate_zoomable_charts_mac.py` runs on the Mac over `V15_V16_CELL_BY_CELL(_FINAL)`, output `SPREADSHEETS/charts/{stem}_zoom.html` (offline `file://`, Chart.js zoom/pan, `bh`/`gain` in title). No auto-trigger — run it manually.
- **Publish-before-live:** publish happens inside the DONE stage *before* the optional live-verify, so a slow/failing live-verify never loses the published sheet. `V15_SKIP_LIVE_AT_DONE=1` defers live-verify entirely for showcase/proof runs.
- **Board `is_complete` = complete AND published** — drives herd requeue of unpublished-but-complete sheets.

---

## 33. CHARTS — WHAT MUST BE TRUE

- Single-file offline `file://` HTML, zoomable/pannable, height ~62vh per sheet (not 165vw).
- `bh` and `gain` in both the filename and the chart title.
- OPEN/AUGMENT/REDUCE/CLOSE markers must be sorted by `(bar, OPEN<AUGMENT<REDUCE<CLOSE)` before plotting so a same-bar reduce never renders before its buy (§25) — a reduce cannot precede its open in the sim; if the chart shows that, fix the marker sort, not the sim.
- Charts land on the Mac (pulled from S1 `V15_V16_CELL_BY_CELL_FINAL`); Mac-side files not in `_FINAL` may be pruned by the 60s `--delete` pull, so a sheet must satisfy publish criteria to persist on the Mac.

---

## 34. HERD / SENTINEL / WATCHDOG / FINISHER — THE DAEMONS

Per box (S1 crypto, S2 stocks), all `setsid` daemons:

- **herd** `tools/v15_local_herd.py` — queue of `SYM_SIDE`, launches **one pilot per sym_side** (twin-pair `2×8` workers to avoid oversubscribing 16 cores), `push_to_s1()` rsyncs `SPREADSHEETS/` + `data/reports/lifecycle_pilot/` back. Log `/tmp/v15_local_herd.log`. Reaps orphaned pilots (ppid==1) only when >30min old AND >15min idle.
- **sentinel** `tools/v15_cell_sentinel.py` — kills+relaunches pilots idle >12min. Log `/tmp/v15_sentinel.log`.
- **finisher** `tools/v15_finisher.py --watch` — re-anchor → bh/gain name → chart → state (§32). Log `/tmp/v15_finisher.log`.
- **assure** `tools/v15_assure.py watch` — refills JSON-ahead sheets from `*_v14_progress.json` truth (audit/refill/complete/bench/watch). Log `/tmp/v15_assure_watch*.log`.
- **redflag** `tools/v15_redflag.py` — echo/zero/stall/coverage detectors, run per monitor cycle.

**Never kill an in-flight pilot to make room** — it loses that sheet's compute. Add load only where there is headroom; a RAM-starved box (S2 was ~1 GB free 2026-09-29) must not get another max-worker pilot.

---

## 35. FAILURE SIGNATURES → FIXES (PLAYBOOK)

| Signature | Likely cause | Fix |
|---|---|---|
| Many cells `0` delta across a `SYM_SIDE` | config-parity entry gate ON in sweep baseline (§17.5); or cand==effective baseline; or symbol non-binding | §18 protocol; audit parity first; fix curated sync; do NOT re-stub |
| A **default/bold** flip shows non-zero | baseline↔override path disagree (idempotency fault) | P0 — stop; §21; trace override application |
| Finished sheet gain **below** baseline | greedy accumulation applying negatives, or chained/mixed-engine total | §14.2; re-anchor fresh eval (§32); check `E_next=E+G` only on `G>0` |
| `E2 = "BASELINE"` string vs numeric confusion | `H/I` live formulas left in rows corrupt header detection | clear live formulas at open (§5.5); resolve cols by row-2 header |
| Cell fill >0.1s routinely | per-row disk reload / per-bar Python loop | §22; confirm `ALL_PREPARED`+`V12_NPZ_CACHE`; vectorize the wiring |
| Sheet hangs on a cell | stalled eval | 10s guard RED the cell+tab, log flags, continue (§5.7) — never hang |
| `225 KB` BadZip workbook | truncated `_atomic_save` (OOM/pkill) | zip-validate (`ZipFile≥10`) before `os.replace`; refill from JSON (§32/assure) |
| "reduce before buy" on chart | marker merge ordering | sort `(bar, OPEN<AUGMENT<REDUCE<CLOSE)` (§33) — not a sim bug |
| New switch shows fabricated distinct delta | synthetic scaffolding (`entry_mask[0]^=True`, getattr no-op) | forbidden (§19); implement real `vec_decisions` mask |
| Backtest → 0 trades after a config change | live-only entry engine forced on | exclude from sync (§17.3/§31); revert that field only (not the file) |

---

## 36. GLOSSARY

- **Baseline** — strategy gain with all switches/filters at bold/default (+ promoted best-overrides for that `SYM_SIDE`). What is live.
- **Naked delta** — delta of flipping the switch alone (no yellow) vs `cumulative_before`.
- **Yellow (cell/filter)** — a `FILTER=OPT` candidate eligible for a specific switch's row (`L:BI`); its cell holds the delta of `switch + that one filter`.
- **`G` / VECTOR_DELTA** — sum of positive yellow deltas for the row (or the naked delta if the row has no yellows).
- **`F` / HUSTLE_DELTA** — the row's vector delta vs the baseline/all-settings-so-far (see column contract §4.2 and memory `v15_column_semantics_2026_09_28`).
- **`E` / BASELINE** — cumulative gain before the row; only advances (`E+G`) when `G>0`, else blank.
- **`K` / PER_ROW_FILTERS** — comma-joined positive yellow header names promoted on the row.
- **Promote** — write `C`, advance `E`, add to `cumulative_overrides` (only on `G>0`).
- **Effective baseline** — `cumulative_overrides` for the `SYM_SIDE` (prior-best + promotions so far); compare candidates against THIS, not fresh defaults.
- **Engine cut** — deploying a new `v12_quick_engine` to a box; chains must not resume across it.
- **DC-channel exit** — LOSS `dc_low−0.25%` / GAIN `dc_high−0.1%` (long; mirror for short) + `wt1_15m` cross; the baseline exits (§15).
- **Honest 0** — a true zero delta (no-op candidate / non-binding switch / parity-suppressed entries); correct, must be preserved.
- **Fabricated delta** — a non-zero produced without a changed ledger; forbidden (§19).

---

## 37. THE GREEDY STATE MACHINE — PSEUDOCODE

The exact control flow `v15_pilot` must implement per tab (STDEV skipped). This is normative — code that deviates is wrong.

```
cumulative_gain      = baseline_gain          # from *_BASELINE_METRICS!B2
cumulative_overrides = ingest_best(SYM_SIDE)  # prior-best wins over defaults (no "if k not in" guard)
write E3 = baseline_gain ; keep E2 = "BASELINE" (header string)

for tab in TABS_IN_ORDER:                      # ENTRY_REVERSAL_BOUNCE ... GLOBAL_RISK_GATES
    write E(first_pending_row(tab)) = cumulative_gain
    for row in rows(tab):                       # switch=cand rows, in order (or shuffled in hustle)
        switch, cand = row.A, row.candidate
        yellows = opportune_yellows(switch, tab) # SPECIFIC + lifecycle/ALL + token-overlap + header exists
        cum_before = cumulative_gain

        naked = eval(cumulative_overrides + {switch:cand}) - cum_before      # ~0.07s, RAM
        per_yellow = { hdr: eval(cumulative_overrides + {switch:cand} + {filter(hdr)}) - cum_before
                       for hdr in yellows }                                   # each written to L:BI now
        write F,G for the row ; write every L:BI cell (pos green / neg red)

        if yellows:
            G = sum(d for d in per_yellow.values() if d > 1e-9)   # positive yellows only
            if G > 1e-9:
                write C = "switch=cand + " + join(pos_yellow_headers)     # bold if non-default
                write K = pos_yellow_headers
                cumulative_gain      += G
                cumulative_overrides += {switch:cand} + {each pos yellow filter}
                # stay on THIS tab, next row's E = cumulative_gain
            else:
                leave C blank ; E of next row stays BLANK ; do not advance cumulative
                # continue down remaining rows of this tab
        else:   # no yellows for this row
            write F=G=naked
            # per spec, a no-yellow row cannot be exploited further -> move to NEXT TAB
            break

# after all tabs: run winning set through backtest_v12_engine -> fill H/I once; finisher re-anchors & publishes
```

Notes:
- **`E` never decreases and never sums a negative.** The only writes to `E` are `E3=baseline`, each promoted `E+=G`, and the first-pending-row inherit at tab entry.
- **`C` is only written on promotion**; it is additive (append headers), never overwritten.
- **Every row writes `F/G` and all its `L:BI`** before the next row — no batching to the end.
- **Live `H/I` stay blank** until the whole workbook is done; template formulas in `H/I` are cleared at open.

---

## 38. vec_decisions/ — PREDICATE MAP (WHERE REAL WIRING LIVES)

Genuine switch logic lives in `vec_decisions/` as pure NumPy predicates returning masks, called once from `v12_quick_engine.simulate_one` (never re-implemented inline in both engines). Representative modules (grep `import vec_decisions` in the engine for the current full list):

- `filter_tf_gates` — `mom3_entry_gate`, `momentum_breakout_gate`, `fast_riser_sig` (entry-family FILTER_TF gates).
- `wave4_families` — `oi_confirm_entry_gate`, `ema_blanket_entry_gate`, `htf_direction_gate`, `mi_exit_signal`.
- `generic_filter_tf` — `build_masks(...)` returns `{entry, reduce_confirm, erosion_confirm}` for generic FILTER_TF families.
- `gain_ladder_augment` — `gain_ladder_fire`, `cooldown_bars` (augment ladder).
- `reduce_profit_lock` — `ppl_step` (TP→BE→arm→SL), `dd_bounce_stop_fires`, `noloss_bypass_params`.
- `quick_reduce_strong` — `quick_reduce_gain_ok`.
- `mtf_atr_trail_exit`, `mtf_compound_exits` — MTF trail / dc / bb / wt compound exits.
- `process_position_crypto__*` / `process_position_stocks__*` — per-venue faithful exit twins.

Each is wrapped in `try/except: pass` at the call site — a broken import silently no-ops the **whole family** (verify by calling functions directly with exceptions exposed; §25). Returning `None` = inert (e.g. OI/EMA-blanket return None on stocks lacking that data) — that is an honest non-binding, not a bug.

---

## 39. HOW TO GENUINELY WIRE A SWITCH (RECIPE)

When §18 concludes a switch truly needs engine logic (rare), wire it honestly:

1. **Backup** the engine (`cp ... backups/before_wire_<SWITCH>_<ts>.py`).
2. **Write a predicate** in `vec_decisions/<family>.py`:
   ```python
   def my_gate(npz, n, is_long, cfg, close, _safe):
       if str(getattr(cfg, "MY_SWITCH_TF", "OFF")).upper() == "OFF":
           return None                      # inert when default -> honest 0 (integrity)
       arr = _safe(npz, f"wt1_{tf}", n, 0.0) # real NPZ array
       mask = (arr < _safe(npz, f"wt2_{tf}", n, 0.0)) if is_long else (...)
       return mask                          # real gating mask, changes which bars fire
   ```
3. **Call it once** in `simulate_one` (entry: `entry_sig &= mask`; exit: `exit_sig |= mask`; etc.), inside the family's try/except.
4. **Prove it** — the switch flip must produce a delta **with a changed trade ledger**: compare `include_ledger=True` results (opens/closes differ), not just gain. If gain moves but the ledger is identical, the delta is spurious — do not ship.
5. **Integrity** — setting the switch to its default (`OFF`) must give exactly 0 (predicate returns `None`).
6. **Parity** — add a faithful scalar twin path in `backtest_v12_engine` (or confirm the live `process_position` already does it) before promoting on vector numbers alone (§44).
7. **Never** satisfy an audit with `_ = getattr(cfg, "X")` reads (§19).

---

## 40. NPZ ARRAY INVENTORY (WHAT PREDICATES CAN READ)

Per-symbol `backtest_v8/indicators/{SYM}.npz` holds per-bar arrays keyed by TF suffix. Confirmed families (load with `allow_pickle=True`):

- **OHLCV:** `open/high/low/close/volume` (base), plus `*_D` daily (`open_D`, `close_D`, `close_D_prev`).
- **Donchian:** `dc_high_{tf}`, `dc_low_{tf}`, `dc_basis_{tf}`, `dc_width_{tf}`, `dc_position_{tf}`, and `dc_high4_{tf}`/`dc_low4_{tf}` (the "4" channel variant), plus `_prev`/`_ant` shifts. TFs: `15m,1h,4h,D` (and `3m/5m` where present).
- **WaveTrend:** `wt1_{tf}`, `wt2_{tf}`, `wt_velocity_{tf}`.
- **Momentum/vol:** `atr_{tf}`, `adx_1h`, `sma_200_1h`, `ema_*`, `bb_upper_{tf}`/`bb_lower_{tf}`, `k_3m`/`d_3m` (stoch), `stdev_edge_{tf}`, `stdev_slope_{tf}`.
- **Time:** `timestamps` (unix; used for the exact 30d/365d slice).

`_safe(npz, key, n, default)` returns the array iff present and length `n`, else a constant-`default` array of length `n` — so a missing key yields an inert predicate (honest non-binding), never a crash. `_exact_30d_slice(npz, is_crypto, 30)` produces the frozen window (crypto: `timestamps[-1]-30d`; stocks: ~20 RTH sessions). NPZ is 15m-based; ≥365D history (`~9490` bars stocks, `~35040` crypto); 30D uses a slice.

---

## 41. TYPE COERCION — sanitize_overrides / _parse_opt

Override values arrive as strings from the sheet and must be coerced to the field's type before the engine reads them:

- `_parse_opt(val, default)`: if `default` is `bool` → `"true"/"false"` (case-insensitive) → bool; if `int` (non-bool) → `int(float(val))`; if `float` → `float(val)`; else if val looks boolean → bool; else raw. Unknown types pass through.
- `sanitize_overrides(overrides, defaults)` normalizes the dict against `QuickConfig` field types and drops/《coerces》 malformed values; the pilot applies it before every eval so a sheet string like `"15m"`, `"False"`, `"0.10"`, `"38"` becomes the correct typed value.
- **Consequence for zero-deltas:** a candidate written as `"0.01"` when the field default float is `0.01` coerces to the identical value → honest 0. Confirm coercion when auditing (a `"1"` vs `1.0` mismatch is not a real change).

---

## 42. BASELINE_METRICS SHEET CONTRACT

`{SYM}_{SIDE}_BASELINE_METRICS` (renamed from `TEMPLATE_BASELINE_METRICS` on clone):

- `B2` = `baseline_gain` (the numeric baseline the sheets read into `E3`).
- Rows carry `bh`, `trades`, `pool_sharpe`, `gain_per_yr`, `avg_gain_trade`, `max_dd_pct`, `n_syms`, `years` (§NO-LIES CSV contract).
- Greedy cum formula fixed on clone: `=IF(G4="",E3,IF(G4>0,E3+G4,E3))`; `G` VLOOKUP key `&"_"&`→`&"="&`; clear `#NUM!/#NAME?/0` trash in `E`.
- Zero-trades still writes this sheet (baseline metrics + `E`) then skips the sweep — never `DIAGNOSTIC ONLY` with no XLS.

---

## 43. PARITY — v12_quick_engine vs backtest_v12_engine

- **Vector** (`v12_quick_engine`): fast masks, what fills every cell.
- **Scalar** (`backtest_v12_engine`): bar-by-bar, calls the real `ez_manage.process_position` / `tradier_manage.process_position`, guarded by `_assert_live_path` — the live-faithful truth.
- **Parity gate:** on the same frozen 30d NPZ, trade-count ratio `0.80–1.25` AND gain mismatch `<0.5 pp` AND `<15%`. Per-row parity fail → flag + red, never abort the sheet.
- **Rule:** never promote a switch on vector numbers alone if it lacks a faithful scalar twin — vector-only gains that live cannot reproduce are not real (see the WT_DC-detailed live≠vec history: vector counted trades that live gating blocked).
- Final winning set is verified through the scalar engine at DONE (`H`/`I`), publish-before-live so a slow scalar pass never loses the sheet.

---

## 44. 365D ROBUSTNESS

- After the 30D greedy+hustle, prepare 365D via `prepare_batch(sym, 365)` and evaluate the winning set vs baseline (`evaluate_prepared_sanitized`) + scalar parity.
- Overfit guards: `365D delta < 50% of 30D delta` → warn; `trades < 30` → diagnostic-only; DD/sharpe gates.
- Live crypto opens are additionally gated by `data/confirmed_365d.json` (365D, gain>0, ≥30 trades, ≤30d fresh) via `tools/confirm_365d.py` — never weaken this certifier.
- 365D artifacts: `*_365d_matrix.xlsx` with `bh/gain` in filename + `_BASELINE_METRICS` 365D rows + `365D_REAL_ZOOMABLE` chart. No promotion without positive 365D gain unless 30D positive or beats B&H.
- **A negative or invalid 365D result is NOT a disqualification — it is a diagnosis that the 30D sheet is faulty (usually: no exits kept). It triggers the §58 repair loop; the sym_side is only promoted once BOTH 30D and 365D are valid and positive.**

---

## 45. SESSION COMMAND CHEATSHEET (READ-ONLY DIAGNOSTICS)

Exact commands proven this session; all read-only unless noted.

```bash
# which engine is running (Mac == S1?)
md5 -q v12_quick_engine.py ; ssh s1-int 'cd ~/binance-sandbox && md5sum v12_quick_engine.py'

# baseline + ledger for a sym_side (see honest trades)
ssh s1-int 'cd ~/binance-sandbox && .venv/bin/python -c "
from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized as E
r=E(prepare_batch(\"GDX_LONG\",30),{},30,include_ledger=True)
print(r[\"gain_pct\"],r[\"trades\"]); led=r.get(\"ledger\") or []
print(sum(t[\"type\"]==\"OPEN\" for t in led),\"opens\",sum(t[\"type\"]==\"CLOSE\" for t in led),\"closes\")"'

# switch delta + timing (does a flip move the ledger? is it <0.1s?)
# ES=evaluate_sanitized applies MODE/tradier like the pilot
ssh s1-int 'cd ~/binance-sandbox && .venv/bin/python -c "
from tools.opt.v12_pilot import evaluate_sanitized as ES
b=ES(\"GDX_LONG\",{},30); print(\"base\",b[\"gain_pct\"],b[\"trades\"])
print(ES(\"GDX_LONG\",{\"WT_LOWER_CROSS_EXIT_TF\":\"1h\"},30)[\"gain_pct\"]-b[\"gain_pct\"])"'

# config parity audit (QuickConfig vs Config / TradierConfig) — see §17.2
# delta-log audit (zeros/dups/timing) — see §30

# isolated single-symbol max-worker PROOF (does not touch the herd)
ssh s1-int 'cd ~/binance-sandbox && V15_PROGRESS_DIR=/tmp/proof_gdx V15_SKIP_LIVE_AT_DONE=1 \
  setsid nohup .venv/bin/python -u v15_pilot.py --sym-side GDX_LONG \
  --template SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx --seq-mode worst2best \
  --window-days 30 --vector-only --workers 14 >> /tmp/proof_gdx.log 2>&1 < /dev/null &'
```

Connection: `ssh -fNT s1-sftp` first (else `Connection refused 127.0.0.1:2201`); try `s1-int` then `s1-pub`.

---

## 46. HARD DO / DON'T

**DO**
- Treat bold/default as live; a default flip = delta 0 (§14.1).
- Expect negative deltas; just don't sum them (§14.2).
- Audit config parity FIRST when many cells read 0 (§17).
- Prove every non-zero delta with a changed trade ledger (§19).
- Keep NPZ in RAM; every cell ≤0.1s (§22).
- Back up before any edit; edit on Mac only; deploy is a coordinated cut (§24).
- Report zero-delta causes per-category from the delta-log (§18/§30).

**DON'T**
- Don't revert to an older file (§24 / RULE 0).
- Don't full-sync live config into QuickConfig (zeroes the backtest, §17.3).
- Don't turn daytrade off — it stays on with DC-channel rules (§15).
- Don't reintroduce `getattr` no-op wiring or synthetic delta perturbations (§19).
- Don't report "N switches unwired" as a conclusion (§18).
- Don't kill an in-flight pilot to make room (§34).
- Don't confuse cells (>5000) with switch names (~200) (§14.4).

---

## 47. OPEN QUESTIONS FOR THE OPERATOR (KEEP CURRENT)

- Exact TF set the DC-channel LOSS/GAIN exits should default to (15m only, or 15m+1h+4h combined?) — the sweep tests combos; the *default* baseline TF should be confirmed.
- Whether `ENTRY_DC_TF`/`ENTRY_DC_BUFFER_PCT` (simple-system entry channel) should be added to the big template's swept switches (they are wired in the engine).
- Which fields, beyond the known live-only set (§17.3), must be excluded from the curated QuickConfig↔live sync.

---

## 48. BEST-OVERRIDE INGEST — HOW A SYM_SIDE STARTS

Whether a `SYM_SIDE` starts from defaults or from prior-best determines its baseline (§14):

- **Tested before** → start from the **best promoted overrides** of the last run for that exact `SYM_SIDE`. Sources searched, best wins over defaults (never `if k not in overrides`):
  - `data/reports/lifecycle_pilot/{SYM}_{SIDE}_v14_progress.json` → `cumulative_overrides` / `hustler_overrides`.
  - `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_*_30d_matrix.xlsx` → parse promoted `C` cells (both `"K=V + F1=v + F2=v"` multi and single-value `C="False"` forms — the parser MUST handle both).
  - `hustler_best.json` / `SPREADSHEETS/` prior artifacts.
- **Never tested** → start from the **cat_side template defaults** (bold col B), which should equal live config for that venue/side (§17).
- Every ingested non-default is written **bold in col C** *before* baseline is computed, so row 3 computes the baseline while the full override set is already in the sheet (`BEST-C-FILL`).
- **Corollary for audits (§21):** compare a row's candidate to the **effective baseline** (`cumulative_overrides` = ingested best + promotions so far), not to a fresh `QuickConfig` default — a "cand differs from fresh default" can still equal the effective baseline and be an honest 0.

---

## 49. COLUMN-C OVERRIDE STRING FORMATS

`C` is the promoted-override record for a row. Both forms must be produced and parsed:

- **Single value:** `WT_15M_BOUNCE_OPEN_ENABLED=False` (a lone switch flip promoted with no positive yellows because it had none, or a bold baseline override).
- **Switch + positive yellows:** `WT_LOWER_CROSS_EXIT_TF=1h + WT_CROSS_EXIT_APPLIES_TO_WINNERS=0.5 + EMA_9_21_FILTER_FILTER_TF=4h` (the switch plus each positive yellow header, ` + `-joined).
- **Bold** iff the value is non-default. Additive: appending yellow headers, never overwriting a prior `C`.
- Booleans render `True`/`False` (never `TRUE`/`FALSE`); ints bare (`10`), TFs quoted strings (`15m`). `_auto_adjust_all_sheets` sets `Arial 10 left`, width `len+2 cap 30`, height 15 before each `_atomic_save`.
- The prev-XLS parser (ingest, §48) must read both forms or the best-override set is silently incomplete → wrong baseline → the classic "C empty / E2 string" failure.

---

## 50. THE 10-MINUTE REPAIR LOOP (RED CELLS → OTHER AGENTS)

The filler must never block on a cell; instead it marks and moves, and a monitor reaps:

- On stall (>10s) or eval error: RED the single cell (`FF0000` fill, white bold), RED the tab (`tabColor="FF0000"`), write the reason into the cell, append `data/reports/v15_flags/{SYM}_{SIDE}_flags.md`, and continue to the next yellow (or next tab if no yellows).
- `tools/v15_redflag.py` + the sentinel/finisher sweep flags on a ~10-minute cycle; a repair agent picks up RED cells, roots the cause (usually a slow/broken predicate or a missing NPZ key), fixes forward, and the assure/refill path recomputes just those cells from JSON truth.
- **Never** leave a cell blank/None on stall, never erase a computed value to hide a stall, never let one cell push a workbook past ~20 min (hard target) / 60 min (absolute).
- BadZip protection: every write via `_atomic_save` (tmp+fsync+rename, `ZipFile≥10` validate, keep `.bak`); the JSON progress file is the source of truth so a corrupted xlsx is rebuilt, not lost.

---

## 51. WHAT "GOOD GAINS" MEANS — PROMOTION GATES

A sheet with "good gains, all deltas applied correctly" means:

- Headline `gain` = finisher's **fresh re-anchored full-set eval** (§32), not a chained total — reproducible to the last decimal by an independent `evaluate_sanitized` re-run.
- `gain > baseline` (greedy only climbs) and `gain` beats or reasonably trails B&H (`bh` in the name for context; a strategy can be worth keeping below B&H if it has far lower DD/exposure).
- Metric gates (interim → real): `pool_sharpe > 0.2` interim (`>0.5`/`>1.0` for promotion), `TIM 20–80`, `max_dd_pct ≤ 30`, trade count above the sample floor (`≥30/sym`, `≥48 crypto`/`≥100 stocks` universe) else `[DIAGNOSTIC ONLY]`.
- Every promoted delta traces to a real changed trade ledger (§19) and, for live promotion, passes scalar parity (§43) and 365D robustness (§44).
- Negative-winner reporting is forbidden (`RELATIVE_BEST_NEGATIVE_DELTA` kept for research only).

---

## 52. KNOWN-GOOD REFERENCE SHEETS

Use these as the "this is what a correct fill looks like" reference:

- `SPREADSHEETS/V15_V16_CELL_BY_CELL/UNIUSDC_LONG_bh57p81_gain32p89_30d_matrix.xlsx` — historical good fill: `BEST→C` copied, baseline via `v12_quick_engine`, per-row `F/G` + `L:BI` yellows correct, `E2` header preserved, `E3` numeric, ~231 yellows per applicable row, `C` populated on promotions.
- 2026-09-29 audited-clean examples (publish-before-live, re-anchored): `ADAUSDC_LONG_bh9p75_gain21p86` (24 trades), `SCCO_SHORT_bhm1p03_gain11p29` (171), `UNIUSDC_LONG_bh111p80_gain74p04` (106). Each: every row filled, `E2` header intact, `F/G/K` numeric per column semantics, `C` seeded+promotions, 0 error cells, no constant-delta echo, `final_gain == fresh_vec == independent re-run`.
- Audit any candidate sheet against these before showing it: header row intact, no None/blank `F/G`, no `#NUM!/#NAME?`, greedy `E` monotonic, promoted `C` matches the positive yellows in `L:BI`, and the finisher re-anchor log line agrees with the filename gain.

---

## 53. LEDGER EVENT / TRADE SCHEMA (simulate_one)

`simulate_one` builds two lists; `include_ledger=True` on `evaluate_prepared_sanitized` returns them merged as `ledger`.

- **`events`** (chart/parity only, never in trade metrics):
  - `OPEN`  → `{type:"OPEN", ts, price, qty, pos_deployed, bar, reason}` (reason e.g. `B12`, `B_SRS_ENTRY`, `HARDCODED_RALLY_REENTRY`, `SEED_BH`).
  - `AUGMENT` → `{type:"AUGMENT", ts, price, qty:add_qty, pos_deployed, bar, reason}` (e.g. `UAG_LADDER gain_since_add +6.18% >= 3.0%`).
  - `REDUCE` (partial) → `{type:"REDUCE", ts, price, qty:reduced_qty, pos_deployed, bar, reason}`.
- **`trades`** (metric source):
  - `CLOSE` → `{type:"CLOSE", pnl_dollars, pnl_pct, deployed, reason, exit_reason, ts, price, entry_price, exit_price, qty, bar_entry, bar_exit, bars_held, entry_reason}`.
  - `REDUCE` rows may also be appended to `trades` with `qty:0.0` (chart marker; not a metric close).
- **Metrics** use CLOSE rows only: `gain_pct = Σpnl_$ / mean_deployed *100` (avg-trade-deployed convention), `pool_sharpe = mean(per_trade_ret)/stdev`, win rate = `#(pnl_pct>0)/#CLOSE`.
- **Ordering invariant:** a position must `OPEN` before any `AUGMENT/REDUCE/CLOSE`; the sim enforces this (reduce/augment only run when `pos is not None`). Any "reduce before buy" seen downstream is a **merge/chart sort** artifact — sort by `(bar, OPEN<AUGMENT<REDUCE<CLOSE)` (§33/§25).
- **"Empty trades" clarification:** zero-qty `REDUCE` marker rows and zero-pnl `OPEN`/`AUGMENT` events are normal ledger entries for charting, not metric trades — do not mistake them for a defect; they are excluded from gain/sharpe.

Exit reasons you will see and what they mean:
- `DAYTRADE_TARGET dc_{tf}_high -0.10% TARGET` — DC-channel GAIN exit (§15/§29).
- `... dc_{tf}_low +0.10% TARGET` (short) — mirror.
- `TECHNICAL_DC ...` — structural channel exit (`TECHNICAL_DC_*_TF`).
- WT cross / velocity exits — `WT_LOWER_CROSS_EXIT_TF`, `WT_4H_VEL_EXIT_*`.
- `VIGILANCE_DC4_{tf}_STOP ... close+block` — vigilance DC4 breach (switchable, default off).
- `HLR_TOP_EXIT_SELL_TOP_g=...` / `REDUCE_TO_FLAT` — quick-reduce / reduce-to-flat.
- `PARTIAL_PROFIT_LOCK ...`, `DD_BOUNCE_STOP ...` — PPL / dd-bounce reduce legs.

---

## 54. CROSS-CHECK THE BIG SYSTEM AGAINST THE SIMPLE SYSTEM

Because both use one engine (§16), the simple system is a free oracle for the big system:

1. Run the simple sweep for a `SYM_SIDE`: `tools/dc_simple_8_sweep.py --sym {SYM} ...` — it reports `base_gain`, best `EXIT`/`ENTRY` TF combo, `WT`/`EMA` keeps.
2. The big-system baseline for that `SYM_SIDE` (DC-channel switches at their bold defaults) should reproduce the simple system's `base_gain` to the decimal — if not, the big template's DC-channel defaults are out of sync with the simple system's `TEMPLATE_DEFAULTS`/config (§17/§48).
3. The big system's promoted `TECHNICAL_DC_*` / `WT_LOWER_CROSS_EXIT_TF` picks should agree with the simple system's `keep_exit_tf` / `keep_wt_tf` (same engine, same data) — divergence means a big-template coverage or ordering issue, not an engine bug.
4. If the simple system finds positive deltas on switches the big system reads 0 for, the difference is baseline/parity (§17) or missing template coverage (e.g. `ENTRY_DC_TF` not swept, §16) — not an unwired engine function.

This turns "why is the big sheet full of zeros?" into a concrete, decidable comparison instead of speculation.

---

## 55. SESSION TIMELINE — WHAT WAS ACTUALLY ESTABLISHED (2026-09-29)

Kept so the next agent does not re-run the same 30 diagnostics:

- Mac and S1 ran identical engine `bd804da7` (Sep 28 20:14). Determinism + idempotency PASS (§21).
- GDX_LONG (tradier baseline): gain +5.28, 210 trades, ~71% win; AXTI_SHORT +1.62; the DC-channel daytrade path closes frequently on this data (short holds), which is the strategy, not a bug.
- Delta-log audit (§20/§30): GDX_LONG 16:05 real run and 00:15 isolated re-run were statistically identical (~2% naked non-zero, same ~11 mover switches, ~17 distinct gains). **No handoff-window regression.**
- The 20:04 engine autosave removed `_batch3_template_wiring` (pure `getattr` no-op scaffolding) + some `_b4_*` unused locals — correct fake-audit cleanup (§19); it changed no trade behavior (the reads were inert), only the appearance of "used" switches.
- Config parity gap measured (§17): ~652 crypto / ~365 tradier strategy mismatches; blind full-sync → 0 trades (live-only entry engines). Curated sync required; remediation assigned to a separate agent.
- vec_decisions gate families (`filter_tf_gates`, `wave4_families`, `generic_filter_tf`) execute and return real masks — no swallowed-exception cascade (§25).
- Timing within budget (~0.07s cached; ~1.7% >0.1s, max 0.23s) (§22).
- No engine/config file was edited by this session (one safety backup made; all diagnostics read-only; one isolated `/tmp/proof_gdx` proof run that did not touch the herd).

---

## 56. OPERATOR FILL SPEC — VERBATIM & AUTHORITATIVE (2026-09-29)

This is the operator's own statement of how `TEMPLATE_*.xlsx` must be filled. **It is LAW and supersedes any conflicting phrasing above.** Every rule here is mandatory; the right-hand notes say where it is enforced.

### 56.1 Reading & columns
- **R1 — Read by row-2 headers, never by coordinates.** Columns may be added, so resolve every column by its row-2 header name matched to the switch/filter name — never by fixed column index. (§4.2)
- **R2 — `Switch` column defaults are BOLD.** The bold value in each switch row is the default used for the initial baseline. (§4.2, §14.1)
- **R3 — Prior results → BOLD in `override`.** The FIRST action for a `sym_side` is to FIND its best default+override settings from previous tests in `/SPREADSHEETS/` and write every non-default setting **bold in the `override` column** — **NEVER** changing the bold/regular state of the `default` column. (§5.2, §48)
- **R4 — `default` column bold changes ONLY via maintenance.** Only `V15_AVG_DELTAS.xls` may change `default` bold, and only when it recalculates the `AVG_DELTA` column and reorders rows `worst_first` — keeping each row's entire content together (a row's yellow cells always travel with the same switch name when row order changes). (§12)
- **R5 — ORANGE (FILTER) rows never above WHITE (SWITCH) rows.** Orange GENERAL/filter rows (below a switch, same column) can never be positioned above white switch rows. (§4.2)
- **R6 — `is_default` backup.** If the default bold font is lost, the `is_default` column holds a written backup of what must be re-bolded. Defaults change only when `V15_AVG_DELTAS` recalculates `AVG_DELTA`/`POS_SYM`. (§4.2, §12)

### 56.2 First calculations
- **R7 — Ingest best FIRST, then baseline in E3.** Find best default+override settings from `/SPREADSHEETS/`, put them bold in `override`, then calculate the combination of ALL these switches → the initial baseline in **E3**. (This is repeatedly reported as *still not happening correctly* — verify it explicitly every run.) (§5.2, §48, §14)
- **R8 — First value = first switch (always a bold default).** The first computed value is the first switch row (always a bold default): the `v12_quick_engine` result for all defaults **plus** the previous-best overrides for that `sym_side`. (§5.2, §37)
- **R9 — Keep NPZ in RAM.** This must be SUPER FAST — never wait; the sym's NPZ stays in RAM until all values are calculated (no per-row disk reload). (§5.6, §22)

### 56.3 Yellow-cell calculation (per row)
- **R10 — Yellow cells hold filter names in the `O:IO` header columns.** The first row (and every row) has yellow cells in the `O..IO` columns whose header is a filter name. (§4.2, §5.3)
- **R11 — A yellow filter is tested for THAT SWITCH AND ONLY THAT SWITCH.** Never apply the yellow filter to any other override or default setting. (§5.3)
- **R12 — The delta of that calculation is ALWAYS written into the yellow cell.** Positive or negative, every yellow cell gets its delta. (§5.3, §8)
- **R13 — Positive yellow → add header name (append, never overwrite) to `override` AND `PER_ROW_FILTERS`, and add its delta to `VECTOR_DELTA` (sum if a value already exists).** (§5.3, §37)
- **R14 — Row complete when all its yellow values are calculated and summed into the delta.** (§5.3)

### 56.4 Baseline chaining & tab navigation
- **R15 — `VECTOR_DELTA` = sum of the row's POSITIVE yellow deltas.** (§5.4, §14.2, item 4)
- **R16 — POSITIVE `VECTOR_DELTA` → move DOWN one row (next switch) on the SAME tab; add the delta to the previous baseline and write it in the baseline column of the next row; repeat.** (§5.4, item 6)
- **R17 — None / zero / negative `VECTOR_DELTA` → do NOT move down; go to the FIRST pending row in the NEXT tab, write the baseline value there, and repeat.** In the next tab: positive → stay and go down next row adding delta; none/0/neg → move to next tab again. (§5.4, item 5)
- **R18 — BASELINE column stays BLANK by default; a baseline value is written ONLY after a positive delta.** (§5.4, §14.1, item 1)
- **R19 — Continue until the COMPLETE workbook is finished.** (§37, item 6)

### 56.5 Tabs, order, completeness
- **R20 — STDEV_SLOPE_SIZING is ONE switch: True/False (use / do not use the 1–5× multiplier).** It is a tab to fill like the others. **⚠️ SUPERSEDES earlier text:** the operator states **13 tabs to fill** with STDEV as a single T/F switch — do NOT skip STDEV as a blanket rule; fill its one T/F switch row (and its yellows if any). Where §4.3/§8 say "STDEV skipped / 12 active," that was a temporary workaround and is overridden here unless STDEV genuinely cannot evaluate (missing `stdev_edge_*`/`stdev_slope_*` NPZ), in which case its single row is marked and the reason logged — not silently dropped.
- **R21 — Every row in every tab needs a delta value (pos or neg).** All rows filled in order; a completed tab may be skipped in remaining rounds. (§5.4, §8)
- **R22 — Hustle mode is the ONLY exception to in-order filling** (rows may be filled in random order). (§5.4)

### 56.6 Live verification & stalls
- **R23 — `LIVE_DELTA` and `LIVE_SHARPE` are filled ONLY when the entire sheet is complete**, by testing the winning default+override set through `backtest_v12_engine` in the actual trading script (slow but necessary to prove parity). Their template formulas screw up the fill and must be cleared at open. (§5.5, §43, item 2)
- **R24 — Stall >10s on a cell → mark the CELL and the TAB RED, write the stall reason in the red cell, then continue to the next yellow cell; if the row has no yellow cells, continue to the next TAB (not the next ROW).** (§5.7, §50)

### 56.7 Compliance checklist (run against any produced sheet)
- [ ] All columns resolved by header name (R1); `E3` = combined baseline of defaults+prior-best (R7/R8).
- [ ] Prior-best written bold in `override`; `default` bold untouched (R3/R4); `is_default` backup intact (R6); orange never above white (R5).
- [ ] Every yellow cell in `O:IO` has its own delta (R12); each yellow tested for its switch only (R11).
- [ ] Positive yellows appended to `override`+`PER_ROW_FILTERS`; `VECTOR_DELTA` = Σ positive yellows (R13/R15).
- [ ] Baseline blank unless promoted; `E_next = E + VECTOR_DELTA` only on positive (R16/R18); neg/0/None → first pending row of next tab (R17).
- [ ] All 13 tabs addressed incl. STDEV single T/F (R20); every row has a pos/neg delta (R21).
- [ ] `LIVE_DELTA`/`LIVE_SHARPE` blank until complete, then filled via `backtest_v12_engine` (R23).
- [ ] No cell stalled the workbook; any >10s cell RED with reason, flow continued correctly (R24).
- [ ] NPZ stayed in RAM; per-cell ≤0.1s median (R9).

---

## 57. DAILY SELF-OPTIMIZATION LOOP (2026-09-29) — see DAILY_OPTIMIZATION_PLAN.md

The operator's daily self-optimization upgrade is captured in full in `DAILY_OPTIMIZATION_PLAN.md`
(repo root). Summary so it survives here: each day at market open, rebuild a simplified v15_avg_delta
(4 cat_side tabs; per switch+filter: pos_sym = count of positive-delta calcs, plus avg & mean delta) from
the latest round's real deltas → inject {avg,mean,pos_sym} into each TEMPLATE_{cat_side} → rearrange white
switch rows worst_first by avg delta (whole row + its yellow cells move together; orange rows below, same
order) → **promote** positive-avg-delta rows to bold defaults (the ONLY place defaults ever change), syncing
the same switch's default per-cat_side across config.py/config_tradier.py/QuickConfig (these must hold FOUR
defaults per switch, one per cat_side — a new architecture) → run the full 30D sweep on the new defaults
(each sym_side baselined on its previous best, using the template's worst_first order) → verify 365D, then
backtest_v12_engine if time → apply pos-30D-gain AND pos-365D winners to live per_sym before open →
recompute avg_delta (ADD pos_sym, REPLACE avg) and roll to the next day. Selective test frequency by pos_sym
(0→1/10, 1→1/5, 2→1/3, 3→½, >3→every) via a v15_pilot change, NOT row deletion (nothing is ever deleted).
JIT NPZ regen per symbol (N+1 while computing N); daily symbol-universe scan (add new, drop untraded).
Compute priority everywhere: pending 30D first, then 365D, then live-faithful rerun. s5 is a temporary
hybrid (all NPZ). INVARIANTS: NO-LIES real deltas only; defaults change only via promotion; rows move whole;
per-cat_side default sync across all 3 configs + template. See the plan doc for the OPEN QUESTIONS that must
be resolved before implementation — do not build past Stage 0 until they are answered.

---

## 58. NEGATIVE 365D = FAULTY 30D SHEET → EXIT/TRADE REPAIR LOOP (USER 2026-09-29, AUTHORITATIVE)

**Rule (operator, verbatim intent):** "−37.81%, in a position 96% of the time means it never tested any exits … few trades → add exits … if a sym_side is going up for 30D, exits will not give a pos delta but 365D will punish that — that is why we need it. Find best possible settings with more exits and more trades and rerun that on 365D — repeat until both are positive." A negative/invalid 365D is **never** a reason to discard a sym_side; it explains why its 30D sheet is wrong right now.

**Why it happens.** The greedy 30D sheet only keeps rows with a positive delta vs the current cumulative. On a 30D window that trends in the side's favour, every exit row cuts a winner short → negative 30D delta → never promoted. The winning set then holds one position (SOLUSDC_LONG: 30D +15%, 365D **−37.81% with 7 trades, TIM 96.4%**). 365D exposes it. The adaptive mandate (HANDOVER_SWEEP_20260929) says the same thing from the other side: *too few trades → add entries; too many trades / low win rate → add filters; TIM too high → add exits.*

**Hard gates in BOTH evaluators (fixed 2026-09-29).** `tools/opt/v12_pilot.evaluate_prepared_sanitized` (what every sheet row uses) now applies the same vomit gates as `lifecycle_pilot.evaluate_month` (the 365D verifier): **TIM > 80% or DD > 30% ⇒ `valid=False`** (`"TIM x% >80% (vomit)"`), plus the window floor (≥10 trades 30D, ≥30 trades 365D). Before this, sheets could promote hold-forever sets that only the 365D check rejected. A baseline that fails these gates is not credible → the pilot's credible-baseline stage (`_credible_baseline`, §56-adjacent; `[ADAPT-*]` log lines) adapts it before the sheet is filled.

**The loop — `tools/v15_365_repair.py --progress <SS>_v14_progress.json --template TEMPLATE_{CAT}_{SIDE}.xlsx`:**
1. Start from the sheet's 30D winning set (`cumulative_overrides`); prepare the 30D **and** the 365D NPZ slice once (forked workers share them).
2. Each step: evaluate every live-wired (non-grey, vector-wired, promotable, de-duplicated) row of `EXIT_STRUCTURAL, EXIT_VELOCITY, REDUCE_PROFIT_LOCK, REDUCE_SIGNAL_RATER, REENTRY_WINDOWED, REENTRY_ADAPTIVE` (+ `ENTRY_*` while trades < 30) on BOTH windows; apply the single row with the best joint score = (windows valid AND positive, windows valid, worst-window gain, 365D gain).
3. Stop when **30D and 365D are both valid (TIM ≤ 80, DD ≤ 30, trades ≥ floor) and positive**, or no row improves, or `--max-steps` (8).
4. Output `{SS}_365_repair.json` (start, every applied step with both windows' metrics, final set).
5. **Re-run the 30D sheet from the repaired set:** `V15_START_OVERRIDES=<SS>_365_repair.json V15_FRESH_RUN=1 v15_pilot.py --sym-side <SS> …` (the repaired set becomes the sheet baseline; the sheet can only climb from it).
6. Re-verify the new sheet's final set on 365D. If 365D is negative/invalid again → back to step 1 with the new sheet. **Repeat until both are positive.** Only then is the sym_side promotable (§51); until then it stays switched off in live (acc_gain_pct ≤ 0 / `_NEG_BLOCK`).

**Proof (2026-09-29, engine fd93da9a):** SOLUSDC_LONG start 30D +7.93 (11 tr) / 365D −37.81 (7 tr, TIM 96.4, invalid) → 5 steps, all exit/reentry rows: `MTF_EXIT_USE_COMPOUND=True`, `OI_CONFIRM_MIN_CHANGE_PCT=0`, `REENTRY_ENTRY_FILTER_ENABLED=True`, `REENTRY_FILTER_MIN_PASS=2`, `MIN_HOLD_BARS_BEFORE_EXIT=32` → **30D +9.36 (37 tr, TIM 40.4) / 365D +10.45 (714 tr, TIM 69.4), both valid**. The 30D sheet is being re-run from that set.

**Trade floors (USER 2026-09-29, hard):** every live sym_side must trade **≥ 10 trades per 30D AND ≥ 80 trades per 365D**. Too few trades (incl. 0) is repaired immediately — soften filters (bool FILTER/GATE/BLOCK/REQUIRE/CONFIRM/VETO/GUARD switches → False) and open entry + reentry paths (ENTRY_*/REENTRY_* rows, `*_ENABLED` entry switches → True) — never accepted. **Target ≥ 30 trades per 30D:** below that, `v15_365_repair.py` runs a BOOST phase (same candidates); a softer set is kept only if both windows stay valid + positive + floored and the worst-window gain does not drop. If no softer set keeps the results, go live with ≥ 10 trades per 30D. A 365D verdict is only real if the 365D slice covers ≥ 330 days of NPZ history — shorter = `UNVERIFIABLE` (wait for full-history NPZ), never a pass.

**Book replacement (USER 2026-09-29):** a verified both-positive set REPLACES the current live per-sym book unless the book itself passes both windows on the current engine + NPZ (valid, positive, floors met) AND is ≥ the new set on 365D. Old book gains measured on a different NPZ timespan are not evidence; a book with a big 365D but a negative/invalid 30D does not reproduce and is replaced.

**Start from the best previous settings (USER 2026-09-29):** a FRESH sheet always compares live recipe / previous best / template defaults on the current engine and starts from the best credible one (`v15_pilot` 77542378 `[ADAPT-BASE]`); a sheet that did not start from the best previous settings is re-run.

**Do not:** discard a sym_side for a negative 365D; promote a set that is only 30D-positive; loosen the TIM/DD/floor gates to make a window "valid"; count a window as fixed while it is invalid.

---

*End of bible — if a procedure above conflicts with older text, this wins. §58 (negative 365D = faulty 30D sheet → repair loop, never a disqualification) is operator-authoritative. §14–§57 are the 2026-09-29 operator-corrected additions; §56 is the operator's verbatim fill spec and is the highest authority on how `TEMPLATE_*.xlsx` is filled; §57 + DAILY_OPTIMIZATION_PLAN.md define the daily self-optimization loop.*
