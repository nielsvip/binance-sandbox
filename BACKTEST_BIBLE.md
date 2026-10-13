# 📖 BACKTEST BIBLE — Single Source of Truth — 2026-09-26 Redo, updated 2026-10-07

> **Canonical file.** Mac `BACKTEST_BIBLE.md` is the only canonical copy.
> After every edit, publish identical bytes by hand (the old `tools/sync_backtest_bible.sh`
> no longer exists — retired 2026-10-07):
> `rsync -az BACKTEST_BIBLE.md s1:~/binance-sandbox/BACKTEST_BIBLE.md` plus
> `S1:data/reports/BACKTEST_BIBLE.md`, then `sha256sum` all copies incl. Mac.
> If conflict, THIS file wins.
> Historical `backtest_bible_0901.md` (745K) is fallback only.
> 2026-10-07 cut: 4 independent templates (§70), switch-add (§71), zero-formula skip (§72),
> possym sampling (§73), fleet/scheduler reality (§74); STDEV active; G = joint-first (§56.0).

> 🔴 **PARITY DEFINITION — READ §68 BEFORE ANY ENGINE, LIVE, TEMPLATE OR DEFAULTS WORK (USER 2026-10-06).**
> Vectorized == live BY DEFINITION. The vectorized engine is now the source of truth; live functions are
> adapted to the vectorized functions. There is NO "vec-only trading", NO vec-only switch, NO gate that
> "makes" live match. Defaults/per-sym settings change ONLY through the daily pre-market chain (§68.3).

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
- **S2 `10.0.0.4` (hostname `s2`), S5 `10.0.0.5` (hostname `s5`), future `S6…`** — `10.0.0.x via gateway ProxyJump`, ephemeral, **exact image of S1** (`rsync -az s1-int:~/binance-sandbox/ niels@10.0.0.x:~/binance-sandbox/`). Never provision from scratch; always clone S1. (Older docs' `S4` name is retired — the fleet observed 2026-10-07 is S1/S2/S5.)
- **Mac** `Darwin /opt/anaconda3/envs/binance_env/bin/python 134×10 G` — **LIVE trading + dashboard only, never backtests** except `--dry-run` / `--allow-mac`. Mac NPZ truncated → `0 trades DATA_ERROR`.

**Code sync:** Edit only on Mac (`/Users/niels/Documents/binance`), then `rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new"` the changed runtime files (`v15_pilot.py`, `SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_*.xlsx`, `tools/v15_row_guards.py`, `data/cell_evidence/*.json`, …) to `~/binance-sandbox/` on S1/S2/S5, `md5sum` verify. Never `push.py`. (`tools/v15_local_herd.py` is deleted — the scheduler launches pilots; see §74. Running pilots keep their loaded code; only new launches take the new bytes.)

**NPZ sync:** S1 is NPZ source (`backtest_v8/indicators/*.npz`). `tools/sync_indicators.sh` rsyncs to `10.0.0.4/5` every 60 s. If `ZECUSDC` missing `stdev_edge_15m`, run `backtest_v8_precompute.py --symbol ZECUSDC --mode crypto` on S1.

---

## 2. DATA — WHAT v15_pilot READS

| Source | Path | Content | Window |
|---|---|---|---|
| **NPZ indicators** | `backtest_v8/indicators/{SYM}.npz` (S1: `~/binance-sandbox/backtest_v8/indicators/`) | Per-bar arrays: `open/high/low/close/volume`, `dc_position_*`, `wt1/wt2_*`, `atr_*`, `sma_200_1h`, `adx_1h`, `stdev_edge_*`, `stdev_slope_*`, `timestamps` (unix **seconds**) | Pilot slices **30 days** (`timestamps[-1] - 30d` crypto, `20 RTH sessions` stocks) via `prepare_batch(sym, 30)` |
| **TEMPLATE workbook** | `SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx` — 4 independent files, never generic (§70) | 13 `SWITCH_SHEETS` + `*_BASELINE_METRICS` + `FILTER_DICTIONARY_V2` + `FILTERS_EXPLAINED` + `LEGEND_FILTERS` | Cloned per `sym_side` to `V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_30d_matrix.xlsx` |
| **Previous best overrides** | `SPREADSHEETS/` + `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` (`cumulative_overrides`, `hustler_overrides`) + `hustler_best.json` | Best `switch=cand` and `filter=opt` per exact `SYM_SIDE` | Ingested **before** baseline; best wins over `config.py`/`config_tradier.py` defaults — never `if k not in overrides` guard |
| **Defaults** | `config.py` / `config_tradier.py` | 851 `QuickConfig` fields | Sanitized via `sanitize_overrides()` |

**No per-row disk reload.** `ALL_PREPARED` + `V12_NPZ_CACHE=32` + `preload_prepared()` keep the 30-day sliced NPZ in RAM for the entire workbook. Per-row reload is forbidden — it turns 0.07 s/cell into >1 s/cell.

---

## 3. ENGINES — TWO, NOT ONE

| Engine | Real reads | s/eval | Role |
|---|---|---|---|
| `v12_quick_engine` (`QuickConfig`, ~851 reads, 15 384 lines) | ~851 | ~0.07 s unloaded / 0.09–0.17 s loaded (2026-10-07 fleet audit) | **Vector sweep** — `V.simulate_one(npz,sym,is_long,cfg)` + `prepare_batch` / `evaluate_prepared_sanitized` (hot `ALL_PREPARED`, `V12_NPZ_CACHE=32` pilot-forced). What `v15_pilot` calls for every yellow cell and every `VECTOR_DELTA`. |
| `backtest_v12_engine` | scalar | ~30 s+ | **Scalar live-faithful verifier** — calls real `ez_manage.process_position` / `tradier_manage.process_position` bar-by-bar, guarded by `_assert_live_path`. What `v15_pilot` calls **once per workbook** (winning set) to fill `LIVE_DELTA`/`LIVE_SHARPE` and prove parity — deferred under the fleet's `--vector-only` + `V15_SKIP_LIVE_AT_DONE=1` launches. |

Parity = `v12_quick_engine` vs `backtest_v12_engine` on **same frozen 30-day NPZ**. `backtest_v12_engine` carries no-vectorisation guard — do not defeat. Trade ratio must be 0.80–1.25 and gain mismatch <0.5 pp and <15%.

---

## 4. TEMPLATE SYSTEM — THE 13-TAB WORKBOOK × 4 INDEPENDENT FILES

### 4.1 Source

`SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_*.xlsx` — four INDEPENDENT files, selected per `sym_side` by `get_template_for_symside()` (exact file, no cross-side fallback — a missing file refuses the run). They diverge per cat_side by design (§70). The legacy generic `SPREADSHEETS/TEMPLATE_*.xlsx` set was archived 2026-10-07 (`backups/archive_generic_templates_20261007/`, never restored); `TEMPLATE.xlsx` no longer exists.

- `TEMPLATE_STOCKS_LONG.xlsx` / `TEMPLATE_STOCKS_SHORT.xlsx` → stocks via `tradier_manage` / `config_tradier.py`
- `TEMPLATE_CRYPTO_LONG.xlsx` / `TEMPLATE_CRYPTO_SHORT.xlsx` → crypto via `ez_manage` / `config.py`

Each template has:

- **13 `SWITCH_SHEETS`** in fixed order: `STDEV_SLOPE_SIZING, ENTRY_REVERSAL_BOUNCE, ENTRY_BREAKOUT_CHANNEL, ENTRY_CONFIRMATION_GATES, EXIT_STRUCTURAL, EXIT_VELOCITY, REENTRY_WINDOWED, REENTRY_ADAPTIVE, AUGMENT_TREND, AUGMENT_RISK_SIZING, REDUCE_PROFIT_LOCK, REDUCE_SIGNAL_RATER, GLOBAL_RISK_GATES` — **all 13 active** (`SKIP_SHEETS` is empty; `STDEV_SLOPE_SIZING` is a single T/F switch tab per §56 R20, filled like the rest).
- **`TEMPLATE_BASELINE_METRICS`** (renamed on clone to `{SYM}_{SIDE}_BASELINE_METRICS`)
- **`FILTER_DICTIONARY_V2`** — every filter's `Filter | Option Value | Sheets applicable | Switches exactly (gates) | Recommendation (SPECIFIC/GENERAL)` — the source of yellow-cell eligibility.
- **`LEGEND_FILTERS` / `FILTERS_EXPLAINED` / `INSTRUCTIONS_V2`** — human docs.

### 4.1b SWITCH BIBLE — every switch's wiring index (2026-10-01)

`SWITCH_BIBLE.md` (repo root, generated) + `data/SWITCH_BIBLE.json` list for EVERY template/vector-read switch and filter: defaults in `config.py` / `config_tradier.py` / `QuickConfig` /
`data/cat_side_defaults_4.json` / template bold, the exact live read sites (`ez_manage.py` crypto, `tradier_manage.py` stocks + imported modules, file:line) and vectorized read sites
(`v12_quick_engine.simulate_one`/`compute_exit_signals`, `vec_decisions/*` reachable from simulate_one), template rows per tab/side, and a wiring status
(WIRED_BOTH_PARITY_PROVEN / WIRED_BOTH_UNPROVEN / VEC_ONLY / LIVE_ONLY / DEAD / NOT_IN_CONFIG). Stubs (`_ = getattr(...)`), `and False` guards, `_batch*_template_*` farms and dead functions never count as consumers.
Rebuild: `python tools/build_switch_bible.py`. Guard (exit 1 on broken links): `python tools/verify_switch_bible.py` (`--since-md5` = what changed since the accepted baseline `data/SWITCH_BIBLE_baseline.json`).
The rules for adding/moving/renaming a switch (touch list of surfaces) are the first section of `SWITCH_BIBLE.md`. Mac cron (`tools/switch_bible_cycle.sh`, every 10 min, mkdir-lock) rebuilds + verifies after template / engine-deploy changes and logs one line to `data/wiring/LOG.md`; orange-filter placement evidence: `python tools/orange_placement_check.py` -> `data/wiring/orange_placement.csv`.

### 4.2 Column contract — read by row-2 headers, never by coordinates

Columns may be added, so `v15_pilot` resolves every column via `_hdr_col_map()` / `_resolve_cols()` on **row 2 header names**:

| Col | Header (row 2) | Meaning | Pilot writes | Rule |
|---|---|---|---|---|
| A | `Switch` | Switch name (e.g. `WT_15M_BOUNCE_ENABLED`) | never | White rows = switches under test. |
| B | `default` | Default value (bold = default) | never | Bold/regular in column B is sacred — only maintenance via `V15_AVG_DELTAS` recalculation changes it. `is_default` col L is backup. |
| C | `override` | Override for this row | `switch=cand [+ pos_yellow_headers]` | **Bold** if non-default, only when `VECTOR_DELTA > 0` for that row's positive yellows. Never overwrite — **add** header names. |
| D | `Family` | `SPECIFIC` vs `GENERAL` | never | `GENERAL` rows are orange per-sheet rollups, never yellow per-switch. |
| E | `BASELINE` | Cumulative baseline before this row | numeric float or blank | **E2 is always header `BASELINE` (string, never overwritten). E3 = `baseline_gain` (first numeric). All other E cells stay BLANK until a POS delta promotes the next row/tab (see §5.4).** |
| F | `HUSTLE_DELTA` | Row result vs the ORIGINAL (initial) baseline (§56.0) | **BLANK by default** (`V15_WRITE_HUSTLE=0` under worst2best; `=1` to write) | Never `VLOOKUP` — pilot decides. Fill `#4472C4` header style handled by `_auto_adjust_all_sheets`. |
| G | `VECTOR_DELTA` | Row COMPLETE delta vs `cumulative_before`: real JOINT eval (switch + all positive yellows together), else best single positive, else naked (§56.0 — never an arithmetic sum) | float pos/neg on evaluated rows; **BLANK (never 0.0) on skipped/invalid** (§41.1, RULE#2) | Pos green `006100` / neg red `FFC7CE`. |
| H | `LIVE_DELTA` | `live_gain - cumulative_before` | **BLANK until workbook complete** | Contains formulas in template — **pilot clears them at start** (`_spec_clear_live_formulas`). Filled once at the end via `backtest_v12_engine` on winning set. Never per-row. |
| I | `LIVE_SHARPE` | `live pool_sharpe` delta | **BLANK until workbook complete** | Same as H — cleared at start, filled once at end. |
| J | `REAL_COMPLETE` | — | — | — |
| K | `PER_ROW_FILTERS` | Comma-joined positive yellow headers for this row | `hdr1, hdr2` or blank | Per-row via `_write_per_row_HIK`. |
| L | `is_default (backup if bold lost)` | `YES` if row B should be bold | never | If bold lost, pilot restores B bold from `L=YES` at workbook open. |
| M:N | `AVG DELTA` / `POS_SYM` | Maintained by `V15_AVG_DELTAS` | never by pilot | Reordered `worst_first` while keeping entire column content together — yellow cells for a row always stay with same switch name when row order changes. |
| L:BI | Yellow headers `FILTER=OPT` (e.g. `ATR_TRAIL_FILTER_TF=OFF`) | Per-yellow delta vs `cumulative_before` | float delta per yellow cell | **Opportune yellows only** (see §5.3). Written before row advances. |
| — | `ORANGE (FILTER)` fields below SWITCH in same column C | Per-sheet GENERAL rollups | — | **Can NEVER be above white (SWITCH) rows.** White switches occupy `r=3..n_switch`, orange GENERAL rows follow below — never interleaved. |
| — | `WHAT SWITCH` | Sentinel ending yellow block | never | Header scan stops here. |

**Visual:** All cells `Arial 10 left`, row height 15, column width `max_len+2 cap 30` via `_auto_adjust_all_sheets` before every `_atomic_save`. `DC_BREAKOUT_SCORE` int `10`, TF `15m` string, `False/True` not `FALSE/TRUE`. Only switch column C may carry non-default bold; no blueish/orange outside C.

### 4.3 STDEV_SLOPE_SIZING — active single-switch tab

`STDEV_SLOPE_SIZING` is ONE switch (`True` = use the 1–5× regime multiplier, `False` = don't — §56 R20), filled like every other tab (`SKIP_SHEETS` is empty). Requires `stdev_edge_*`/`stdev_slope_*` in the NPZ; if genuinely missing, its row is marked with the reason and logged — never silently dropped. Effective workbook = **13 tabs, ~3400 rows** (CRYPTO_LONG 2026-10-07; counts drift as switches are added — see the floor test, never pin exact counts in prose).

---

## 5. WHAT v15_pilot IS SUPPOSED TO DO — EXACT FILL CONTRACT

`v15_pilot.py` is the **ONLY writer** of `V15_V16_CELL_BY_CELL/*_30d_matrix.xlsx`. Formulas never in data rows `r≥3` for `C/E/F/G/H/I/K` — pilot decides and clears any `VLOOKUP`/`IF` via `_clear_vlookup_formulas` (except documented `GLOBAL_RISK_GATES` waiver). The workbook is **never filled in parallel across tabs** — tabs are filled sequentially under pilot order (with `hustle` shuffle as the only exception).

### 5.1 Where it runs

**Fleet S1/S2/S5** (never Mac except `--dry-run`). The scheduler (`tools/v15_fleet_scheduler.py`, S1 cron `*/2`, config `tools/fleet_hosts_final.json` — workers/pairs/memory-guard per host) launches **one `v15_pilot` per `sym_side`** with `--seq-mode worst2best --window-days 30 --vector-only` (live verify deferred), `V12_NPZ_CACHE=32` (pilot-forced), `ALL_PREPARED` in RAM. (The old herd daemons are retired — see §74. Worker counts are tuned per host in the fleet config, not in this file.)

### 5.2 Step 0 — Clone + ingest best overrides + baseline

For each new `sym_side` (e.g. `AAPL_LONG`):

1. **Find best overrides for that exact `sym_side`:** Search `SPREADSHEETS/` + `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` (`cumulative_overrides` / `hustler_overrides`) + `hustler_best.json`. **Best wins over defaults — never `if k not in overrides` guard.** Every non-default from the winning set is written **bold in column C `override`** before any calculation — never changing bold/regular of column B `default` (column B only changes via maintenance when `V15_AVG_DELTAS.xls` recalculates `AVG_DELTA` and reorders `worst_first`).

2. **Clone:** `SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE{STOCKS|CRYPTO}_{LONG|SHORT}.xlsx` → `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_30d_matrix.xlsx` via `_atomic_save` (validates `ZipFile ≥10` entries before `os.replace` to avoid 225 KB truncation). Rename `TEMPLATE_BASELINE_METRICS` → `{SYM}_{SIDE}_BASELINE_METRICS`, fix `G` `VLOOKUP &"_"&`→`&"="&`, clear `#NUM!/#NAME?/0` in `E`.

3. **Fill ALL overrides in sheet before baseline:** `BEST-C-FILL` — pilot iterates every sheet row `3..max_row`; if `A=Switch` exists in `overrides`, set `C=override` (e.g. `C3=False` for `WT_15M_BOUNCE_OPEN_ENABLED`). This is how the first row calculates baseline **while** all overrides are already in the sheet.

4. **Baseline:** `evaluate_prepared_sanitized(prepared, overrides, 30)` (or `evaluate_sanitized` if no prepared) on the 30-day NPZ slice → `baseline_gain` / `bh` / `trades` / `pool_sharpe` written to `{SYM}_BASELINE_METRICS!B2` and **`STDEV_SLOPE_SIZING!E3` / first pending row's `E`** (with `E2` header `BASELINE` preserved). **Zero-trades still writes XLS then skips sweep — never `DIAGNOSTIC ONLY` with no XLS**; early `return` before `clone_template` is forbidden.

### 5.3 Step 1 — Per-row yellow evaluation

For each tab in `SWITCH_SHEETS` order (`STDEV_SLOPE_SIZING → ENTRY_REVERSAL_BOUNCE → ENTRY_BREAKOUT_CHANNEL → ENTRY_CONFIRMATION_GATES → EXIT_STRUCTURAL → EXIT_VELOCITY → REENTRY_WINDOWED → REENTRY_ADAPTIVE → AUGMENT_TREND → AUGMENT_RISK_SIZING → REDUCE_PROFIT_LOCK → REDUCE_SIGNAL_RATER → GLOBAL_RISK_GATES`), rows visited worst-first (`--seq-mode worst2best`) with `--nav-mode jump` on NEG/0 (next pending row of the next tab) — jump changes VISIT ORDER only; every row still fills (coverage-audited; a mode that drops rows violates §56.0):

- **Opportune yellows** for that exact `switch` — from `FILTER_DICTIONARY_V2` / `FILTERS_EXPLAINED`, filtered by the yellow headers `L:BI` (row 2) for that sheet. (Interim eligibility until the yellow-map rebuild: row switch name and column filter name share ≥2 `_`-tokens, or the cell is in the ever-yellow map, or the base is mandatory for the tab.) A yellow cell exists iff:
  - `Recommendation` is `SPECIFIC` (not `GENERAL` — GENERAL is the orange per-sheet rollup, never yellow per-switch),
  - `Sheets applicable` contains the sheet's lifecycle (`ENTRY`, `EXIT`, `REENTRY`, `AUGMENT`, `REDUCE`, `GLOBAL_CHECK`) or `ALL`,
  - `Switches exactly (gates)` token-overlaps the current `switch` (`_token_overlap` requires ≥2 strong tokens or exact switch containment — generic `FILTER` token alone is not enough),
  - and `FILTER=OPT` header exists in `L:BI` for that sheet.

  **If a cell is yellow, the filter in the column name MUST be tested for THAT SWITCH AND ONLY THAT SWITCH** — not applied to any other override or default. Never calculate random filters that are not yellow for that row (forbidden — wastes CPU, lies about provenance).

- **Candidates:** `[naked]` (switch=cand alone) `+ each yellow filter variant` (switch=cand **plus that one filter** = `FILTER=OPT`, vs `cumulative_before`). Each candidate evaluated via `v12_quick_engine` (fork pool `_n_proc = min(workers, cpu-1)`, ~0.07 s unloaded each, `V12_NPZ_CACHE=32` from RAM). Every yellow delta is written **into that yellow cell** (`L:BI`) before the next row — never batched to end. (Evidence-condemned never-positive rows/cells are skipped + booked instead of evaluated — §72; sampled rows/cells are deferred to the heal pass — §73.)

- **`VECTOR_DELTA` (G) = the row's COMPLETE delta** (§56.0 — never an arithmetic sum): real JOINT eval (switch + all positive yellows together) if `>1e-9`, else the best single positive (filter or naked), else the naked delta (which covers the no-yellows case). Negative/zero yellows are written but **never promoted**.

- **If positive:** add the **column header name** to `override` (C) and `PER_ROW_FILTERS` (K) — **add, never overwrite** existing content — and add its delta to `VECTOR_DELTA` (sum if already valued). When all yellows for the row are done the row is complete.

### 5.4 Step 2 — Baseline chaining and tab navigation

After all yellows for the row are evaluated:

| Condition | Meaning | Action |
|---|---|---|
| `VECTOR_DELTA > 1e-9` (complete delta — joint, best single, or naked) | Row is winner | Write `C` (switch + adopted filters), `G/K` + yellow `L:BI` cells (`F` stays blank unless `V15_WRITE_HUSTLE=1`). **Move down 1 row on SAME TAB.** `E` for the next pending row on this tab = `cumulative_before + VECTOR_DELTA` (numeric `E_next = E + G`). `cumulative_gain` and `cumulative_overrides` advance. |
| `VECTOR_DELTA` is zero or negative (nothing positive) | Row is loser | Write `G/K` + yellow `L:BI` deltas (negative red fill for G), leave `C` blank. **DO NOT MOVE DOWN the tab** (`--nav-mode jump`, the fleet default). Move to **first pending row in the next tab**, write `E = cumulative_before` there, and repeat on that tab. (`--nav-mode fill_tab` stays instead; jump only reorders visits — every row still fills.) |
| Row has **no yellow cells at all** | Naked-only row | `G` = the naked delta; same navigation as every row (**POS stays, NEG jumps**). (Older bible text claimed no-yellow rows always jump tabs even when POS — corrected 2026-10-07: neither the code nor §56 supports that; the naked switch itself is what gets exploited.) |

**Baseline invariant:** `E` (BASELINE) **only gets written after a positive delta** — otherwise it **stays BLANK normally**. The only `E` values that exist are `E3 = baseline_gain` and each `E` that was promoted by a preceding POS row on its tab (or the first pending row of a newly entered tab which inherits the current `cumulative_gain`). `_validate_e_chain_and_yellows` asserts `new_cum ≥ old_cum` and `delta == vg - cum`.

**Every row in every tab gets a verdict** (delta, settled skip, or pending→refilled) — jump reorders visits, it never drops rows. `None` (blank) G on an EVALUATED row is a bug; blank G on a skipped/invalid row is correct (§41.1).

### 5.5 Step 3 — LIVE verification (deferred)

`LIVE_DELTA` (H) and `LIVE_SHARPE` (I) **contain formulas that screw up sheet fill — they stay BLANK until the entire workbook is complete** (`_spec_clear_live_formulas` clears any formulas at open, `_write_per_row_HIK` writes only `K` per row and leaves `H/I = None`).

When the 13-tab workbook is fully filled, pilot runs the **winning set** (`cumulative_overrides`) through `backtest_v12_engine` (`live_evaluate`) and fills **every processed row's `H`/`I`** with the live parity result (POS rows get `live_delta`/`live_sharpe`, NEG rows get their own negative delta / `0.0` sharpe). Per-row parity fail marks flags but never aborts the sheet. Falls back to vector result on live timeout. (Fleet launches pass `--vector-only` + `V15_SKIP_LIVE_AT_DONE=1`, so live verify is deferred, not per-run.) Final charts and 365-day robustness rerun are handled after (§7).

### 5.6 Speed — NPZ in RAM

NPZ for the `sym_side` is preloaded **once** via `preload_prepared()` → `ALL_PREPARED[sym]` (pilot parents run 4–8 GB RSS under load — 2026-10-07 fleet audit) and kept for all row×yellow evaluations. `evaluate_prepared_sanitized()` uses RAM arrays only. Per-row disk reload is forbidden. The scheduler's memory guard (`mem_reserve_mb`, `oom_mb` per host in `tools/fleet_hosts_final.json`) stops new launches when tight and reaps the youngest pilot below the OOM floor; `V12_NPZ_CACHE=32` is pilot-forced (launcher env values are vestigial).

### 5.7 Stall guard — >10 s = RED and move on

If a yellow cell's `v12_quick_engine` evaluation stalls **>10 s** (`YELLOW_TIMEOUT = 10.0`, `per_cell_timeout_sec = 10.0`, `_per_cell_hard_limit = 10.0`):

- Mark **that single yellow cell** `RED` (`FF0000` fill, `FFFFFF` bold) and write `TIMEOUT 10s` reason in it.
- Mark the **tab color** `RED` (`ws.sheet_properties.tabColor = "FF0000"`).
- Append to `data/reports/v15_flags/{SYM}_{SIDE}_flags.md` via `_flag_to_md`.
- **Continue to the next yellow cell** in the same row. If no yellow cells in the row, **continue to next TAB, not next ROW** (never hang, never >1 h per workbook, per-sym ≤60 m never >20 m).

Never stall the entire workbook on one cell. Baseline evaluation has its own 60 s guard and falls back to empty baseline on timeout.

---

## 6. INCIDENT INVARIANTS (2026-09 post-mortem, compressed 2026-10-07 — all fixed in code, kept as law)

A week-long stall (every `sym_side` showing `E2=BASELINE`, `C` empty, `F/G/H/L:BI None`) traced to 7 defects. Every one is fixed; every fix is now an invariant — reintroducing any of them reintroduces the stall:

1. **Zero-trade baselines still clone + write an XLS**, then skip the sweep (never early-return before `clone_template`).
2. **Best wins over defaults — never `if k not in overrides`** when ingesting the winning set into `C`.
3. **Prev-XLS parser handles single-value `C`** (`False`, not just `K=V + …` with `F>0`).
4. **Every `_atomic_save` zip-validates** (`ZipFile ≥10` entries) before `os.replace` — truncated saves never land.
5. **All columns resolve via row-2 headers** (never hardcoded coordinates); yellow detection reads `L:BI` `=` headers.
6. **`H`/`I` formulas cleared at open** (data rows never carry `VLOOKUP`/`IF` in `H/I`).
7. **Every tab in `SWITCH_SHEETS` fills** (the old `SKIP_SHEETS={"STDEV…"}` workaround is retired — STDEV is rewritten and active, §4.3).

**Standing rule:** refill lost/truncated XLS from JSON truth via `tools/v15_refill_from_json.py` (validated save) so compute is never lost. **First priority at any moment:** if `C` is empty or `F/G` are `None` for a `sym_side` with `done>0`, stop the fleet, restore the invariants above, validate via `_validate_e_chain_and_yellows`, refill from JSON — before any other work.

---

## 7. v12_quick_engine — STALL FUNCTIONS THAT BLOCK THE ENTIRE WORKBOOK

`v12_quick_engine.py` (15 384 lines, ~851 real reads) is `v15_pilot`'s inner loop — every yellow cell does `evaluate_prepared_sanitized → simulate_one → compute_*_signals → vec_decisions.*`. Functions that stall >10 s on common NPZ block the whole workbook because the pilot's fork pool waits on them and the per-yellow `10 s` guard must fire to prevent hang.

### 7.1 Known stall-prone areas

- **`_batchN_template_wiring()` blocks — REMOVED 2026-09-29** (§26.6 cleanup; do not reintroduce batch-per-switch wiring — new wiring goes in `vec_decisions/` predicates + a single call site). The same law applies to any replacement: **constant-time mask ops only** — no per-bar Python loops, no `safe` fallback that recomputes `close` repeatedly.

- **`compute_regime_sizing_mult()` (STDEV).** Single-switch tab (§4.3) reading `stdev_edge_*`/`stdev_slope_*` per TF. Requires those keys in the NPZ — regenerate a symbol's NPZ (`backtest_v8_precompute.py --symbol X --mode crypto`) when genuinely missing; never silently substitute `close` and call the row calculated.

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
| F | `HUSTLE_DELTA` | `vec_gain - baseline_gain` (vs ORIGINAL baseline) | `Arial 10 left`, blue `#4472C4` header; **BLANK by default** (`V15_WRITE_HUSTLE=0` under worst2best), never `VLOOKUP` |
| G | `VECTOR_DELTA` | Row complete delta vs `cumulative_before`: real JOINT eval, else best single positive, else naked (§56.0) | Float pos/neg on evaluated rows, green `006100` / red `FFC7CE`; **BLANK (never 0.0) on skipped/invalid**, never `VLOOKUP` |
| H | `LIVE_DELTA` (col 8) | `live_gain - cumulative_before` (parity live) | **Per-workbook** written via `_write_per_row_HIK` after complete — blank per row until complete |
| I | `LIVE_SHARPE` (col 9) | `live pool_sharpe` per row | Per-workbook via `HIK` |
| K | `PER_ROW_FILTERS` (col 11) | Comma-joined pos yellows for that switch | Per-row via `HIK` |
| L:BI | Yellow headers row 2 (`col_by_header` lookup) | Per-yellow delta inside yellow cell vs `cumulative_before` | Positives feed the JOINT eval + `C/K` adoption; evaluated yellows always get a real delta; skipped cells stay BLANK (never 0.0) |

**Sequential fill:** 13 tabs, `--seq-mode worst2best` (worst-first visit order) with `--nav-mode jump` on NEG/0 (jump reorders visits only — every row still fills; `fill_tab` stays instead). `cycle` rotates deque on NEG delta. Hustle beam `width 64 depth 10` + exhaustive `top12` subsets finds max combination vs `baseline+cum`. **Never ditch a `sym_side` halfway** — every `sym_side` must run to the last sheet even if 13 sheets are NEG (`SHEET NEVER ABANDONED`).

**E-chain guard:** Baseline written to `E3` (float), `E2` header `BASELINE` preserved via `ws.cell(row=3,col5)=baseline` and self-monitor that checks `E3` numeric and restores header without overwriting numeric. `_validate_e_chain_and_yellows` asserts `delta==vg-cum` and `new_cum ≥ old`.

---

## 9. LAST-3-DAY INGEST, CHARTS, 365D, PARITY, SYNC

- **Last-3-day ingestion:** Before baseline, pilot searches `SPREADSHEETS/` + `SPREADSHEETS/V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` for BEST overrides for that `sym_side`. `cumulative_overrides` / `hustler_best.json` best wins over defaults (never `if k not in`). Done entries from `progress.json` repopulate yellows and are respected via `respect STDEV/shuffle max delta` logic; `_atomic_save` `zip≥10` valid prevents BadZip loss. S1 is writer, Mac mirror via `sync_s1_to_mac.sh` — progress json correlation via `_v14_progress.json` per `sym_side`.

- **Charts:** Per-sheet `write_zoomable_chart(symside, sheet, overrides, 30)` and per-complete `write_zoomable_chart(symside, None, cumulative_overrides, 30, suffix='30D_REAL_ZOOMABLE')` + `30D_BIGGEST_DELTA_ZOOMABLE`. Offline `file://` Chart.js 4.4.1 zoom/pan with `bh` and `gain` in title/filename (`{sym}_bh{gain}_30d_matrix.xlsx` and HTML `title: bh {bh:.2f}% gain {gain:.2f}%`). 365D chart via `suffix='365D_REAL_ZOOMABLE'` after 365D rerun.

- **365D robustness:** After 30D greedy+hustle, pilot prepares 365D NPZ via `prepare_batch(sym,365)`, evaluates best vs baseline via `evaluate_prepared_sanitized` and live parity, checks `365D delta <50% of 30D delta` warns overfit, `trades<30` diagnostic-only, `DD/sharpe` gates. Creates `*_365d_matrix.xlsx` with `bh/gain` in filename and `_BASELINE_METRICS` rows for 365D metrics. No promotion without pos gain unless 30D pos or >`bh`.

- **Parity:** Every pos delta verified via `live_evaluate(sym, overrides, 30)` (scalar bar-by-bar) vs `vector_evaluate_cached` (vector, `V12_NPZ_CACHE=32`, RAM via `preload_prepared`). `parity_ok` requires trade ratio `0.80..1.25` and gain mismatch `<0.5 pp` and `<15%`. Per-row parity fail marks red and logs `flags.md` without aborting sheet. Final switch-by-switch live verification on winning set.

- **Sync:** Edit only on Mac, then `rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new"` to `~/binance-sandbox/` on S1 (canonical) + S2 + S5, `md5sum` verify. S1 NPZ `473×31 G` (16c 30 Gi), Mac `134×10 G` never backtests except `--dry-run`. (Fleet live-verify runs deferred — see §3.)

---

## 10. METRICS — HONEST, NO LIES (STRESS-TEST RULES)

`tools/opt/metrics.py:compute(events)` → `gain_pct = sum(pnl_$)/peak_concurrent*100`, `pool_sharpe = mean(per_trade_returns)/stdev`, `TIM=held/window*100`, `DD=peak-to-trough/peak*100` capped 100%, gates `pool_sharpe>0.5 interim (>1.0 real)`, `gain/mo>20%`, `≥10× B&H`, `TIM 20-80`, `DD<30`, `30/mo` crypto. No annualization `sqrt(252)` — banned. Sample floor `≥48 crypto` or `≥100 stocks` `>1yr` `≥30 trades/sym` else `[DIAGNOSTIC ONLY]`.

Beat ideas to death: add friction (1.5–2× slippage, worst-case fills), seek **plateaus not peaks** (profitable across 50–150% param range, not at one spike), walk-forward out-of-sample, multiple regimes. Time allocation 20% ideas / 80% breaking. See `backtest-expert` skill `references/methodology.md`.

---

## 11. MACHINE ROLES & CONNECTION — BEFORE ANY SSH

`Mac` `Darwin` `/opt/anaconda3/envs/binance_env/bin/python` `134×10 G` — **LIVE trading + dashboard only, never backtests** (except `--dry-run`/`--allow-mac`).

`S1` `157.180.125.52` / `s1-int 127.0.0.1:2201` (`ssh -fNT s1-sftp` ControlMaster `~/.ssh/cm-s1-int`, fallback `157.90.168.35` gateway, `10.0.0.3`) `16c 30 Gi 79 G free` `473×31 G` — **LIVE trading stack + backtest pilots + fleet scheduler** (corrected 2026-10-07: S1 was never backtests-only; the live `ez_*`/`tradier_*` stack runs here).

`S2` `10.0.0.4` / `S5` `10.0.0.5` via gateway ProxyJump — backtest pilots (+ `v15_trade_parity.py` lane). All 16c/30 GB.

**Before any ssh:** `ssh -fNT s1-sftp` else `Connection refused 127.0.0.1:2201`. Try both `s1-int` and `s1-pub` before declaring S1 down. `~/binance-sandbox` canonical — never hardcode, use `Path(__file__).resolve().parents[1]`.

---

## 12. TEMPLATE MAINTENANCE — AVG-DELTA PIPELINE (ONE WRITER PER JOB)

Defaults (column B bold) only change through the avg-delta pipeline: `tools/v15_cell_evidence.py` + `data/avg_delta_pos_sym.json` feed `tools/v15_daily_template_update.py`, which recalculates `AVG DELTA` / `POS_SYM` and reorders rows `worst_first` while keeping **entire column content together** — yellow cells for a row always stay with same switch name when row order changes. Never change bold/regular of `default` column outside this maintenance. `is_default` (col L) is backup if bold lost. Writer allowlist for the 4 templates (§70): daily-update (avg/promotions), bookkeeper (books), switch-add (new rows), staged-apply (verified restructures) — nothing else.

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
- A **cell** is one evaluated quantity: each switch row spawns `1 naked + K yellow` candidate evaluations, and there are multiple candidate *values* per switch. Across 13 tabs × rows × candidate values × all `L:BI` yellows this is **>5000 cells**.
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

Per-row lines use `label: ROW_DONE` (`row_secs`, `n_evals`, `delta`, `promoted`, `cum_after`); post-spec stages log `FINAL_RECHECK`, `FINAL_DIAGNOSE`, `DIAG_365D`, `REPAIR365_SCREEN:*`.

This is the **audit source of truth**. Standard audits:

- **Fill count / zero rate:** count records; `delta==0` (`abs<1e-9`) vs non-zero; % non-zero per `label=="naked"` and overall.
- **Distinct outcomes:** `len(set(round(gain_pct,6)))` — how many distinct strategy states the sweep reached.
- **Duplicate deltas:** `collections.Counter(round(delta,6))` — a delta value repeated across unrelated switches often indicates a shared fallback path; investigate.
- **Timing:** `secs` distribution for eval lines, `row_secs` for `ROW_DONE`; count `>0.1s` (cached target ~0.07s unloaded) and `max` (see §22).
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

- With NPZ in RAM (`ALL_PREPARED`, `V12_NPZ_CACHE=32`, `evaluate_prepared_sanitized`), a single cached eval is **~0.07s unloaded**; a cold/first eval ~0.11s. Under fleet load (2026-10-07 audit, 3 fork workers, contended 16-core boxes): mean 0.09s / median 0.12s / p90 0.17s per eval — the 0.03–0.07s figure is per-EVAL (one candidate), while a row costs ~205 evals / workers (≈7s at 3 workers) and a symbol costs 3393 rows × ~2.7 refill passes + post-stages (see §74 for the full wall-time math).
- **Target: every cell fills in ≤0.1s.** A cell that takes materially longer is almost always doing a **per-row disk reload** or a per-bar Python loop — fix it (§7). Verified 2026-09-29 GDX_LONG: 26k evals, mean well under 0.1s, only ~1.7% exceeded 0.1s with a max of 0.23s (acceptable tail under CPU contention; investigate if the tail grows).
- Two distinct guards exist and serve different purposes: the **10s per-yellow hard stall guard** (`YELLOW_TIMEOUT`, marks the cell + tab RED and moves on — never hang; §5.7) and the **0.1s performance target** (a cell routinely over this is a perf regression to fix, not a stall to RED). Do not confuse them.
- The filler must **never get stuck on a cell**: on stall, RED the cell, log to `data/reports/v15_flags/{SYM}_{SIDE}_flags.md`, continue. Monitors sweep the flags every ~10 min so other agents can pick up RED cells for repair.

---

## 23. PROOF PROTOCOL — DEMONSTRATE ONE CLEAN SHEET PER SERVER

To prove the pipeline end-to-end without disturbing the running herd:

1. **Isolate:** launch with `V15_PROGRESS_DIR=/tmp/proof_{sym}` so the proof's progress JSON + delta-log do not collide with the scheduler fleet. Add `V15_SKIP_LIVE_AT_DONE=1` to defer the (slow, sometimes parent-killing) live-verify at DONE. Add `SWITCH_PARITY_REGISTER=0` so the proof's DONE stage does not register its set into the live per-sym books (§67 registrar).
2. **One symbol, max workers:** `v15_pilot.py --sym-side {SYM}_{SIDE} --template SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_{cat}_{side}.xlsx --seq-mode worst2best --window-days 30 --vector-only --workers 14` (16-core box → 14–15 workers). `_n_proc = min(workers, cpu_count-1)`.
3. **Server placement:** all three fleet hosts run crypto (stocks queue is empty); check `free -m` first — never add load that could kill a running filler (2026-09-29 note: s2 was ~1 GB free vs s1 ~22 GB; capacities swing — measure, don't assume).
4. **Verify the fill (from the delta-log + xlsx):** every row wrote G + all `L:BI` (F blank unless `V15_WRITE_HUSTLE=1`); no `None` on evaluated rows; timing ≤0.1s median unloaded; baseline correct (determinism + idempotency); greedy `E` chain monotonic; and the finished workbook is named with `bh` and `gain` and has a zoomable chart on the Mac (`tools/generate_zoomable_charts_mac.py`, output `SPREADSHEETS/charts/{stem}_zoom.html`).
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

1. **Default-variant row (`=OFF`):** naked flip to `OFF` = the current value → naked delta `0` (integrity; §14.1). The row's value comes from its yellows: evaluate `{...cum..., WT_LOWER_CROSS_EXIT_TF:OFF, <each yellow>}` vs `6.20`. Suppose two yellows are positive (+0.30, +0.12) and the rest ≤0. The pilot runs the real JOINT eval (switch + both yellows together); suppose it returns `+0.40`. `G = 0.40` (never the 0.42 sum — §56.0). Since `G>0`: write `C = "WT_LOWER_CROSS_EXIT_TF=OFF + WT_CROSS_EXIT_APPLIES_TO_WINNERS=0.5 + EMA_9_21_FILTER_FILTER_TF=4h"`, `G = 0.40`, `K = those two headers`, each `L:BI` yellow cell = its own delta (pos green, neg red). Move **down one row on this tab**; next row `E = 6.20 + 0.40 = 6.60`; `cumulative_overrides` gains those two filters.
2. **`=1h` row:** naked flip to `1h` vs `6.60` — suppose `+1.20` (real ledger change: fewer late exits). Then its yellows on top, then the JOINT eval; suppose it returns `+1.24 > 0`. Promote: `C = "WT_LOWER_CROSS_EXIT_TF=1h + <pos yellows>"`, `E_next = 6.60 + 1.24 = 7.84`, overrides gain `WT_LOWER_CROSS_EXIT_TF=1h`.
3. **`=4h` row:** naked flip to `4h` vs `7.84` — suppose `-0.30` and no positive yellows → `G = -0.30` (the naked delta). Not promoted: `C` stays blank, `G` written (red), `E_next` **stays blank** (baseline does not advance), `cumulative` unchanged. Under `--nav-mode jump` the next visit is the first pending row of the next tab (jump reorders visits only — every row still fills).
4. Pending rows of `EXIT_VELOCITY` are still all visited (worst-first across tabs); the tab's chain continues from the current `cumulative` (7.84).

Key invariants exercised: every evaluated row wrote `G` (pos or neg) and every eligible `L:BI`; `C` only for promoted rows; `E` only advanced on positive `G`; the default-variant row's value came from yellows, not from the (zero) naked flip. (`F` stays blank under worst2best unless `V15_WRITE_HUSTLE=1`.)

---

## 28. THE 13 ACTIVE TABS — WHAT EACH SWEEPS

Order is fixed (all 13 fill). Lifecycle tag drives yellow eligibility (`Sheets applicable`).

0. **STDEV_SLOPE_SIZING** (`SPECIFIC`, sizing) — ONE switch: use the 1–5× regime multiplier or don't (§4.3).

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
12. **GLOBAL_RISK_GATES** (`GLOBAL_CHECK`) — portfolio/rate gates: `OPEN_RATE_MAX`, blacklist, circuit gates, bear-market mode. (No VLOOKUP waiver is in force — data rows carry no formulas anywhere; the pilot's docstring allows a waiver only if explicitly documented, and none is.)

**Yellow eligibility recap (per §5.3):** a filter is a yellow for a switch iff `Recommendation=SPECIFIC` AND (`Sheets applicable` contains the tab's lifecycle OR `ALL`) AND `Switches exactly (gates)` token-overlaps the switch AND the `FILTER=OPT` header exists in that tab's `L:BI`. (Interim until the yellow-map rebuild: ≥2 shared `_`-tokens, ever-yellow map, or mandatory base.) Never evaluate a filter that is not yellow for the row.

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

After a workbook's 13 tabs are filled, the pilot's DONE stage publishes it (the standalone `v15_finisher.py --watch` daemon is retired — publish is in-pilot since the scheduler era, §74):

- **Re-anchor:** the headline `gain` is recomputed as a **fresh full-set evaluation** of the final `cumulative_overrides` under the pilot's own engine — never the chained running total (which could carry mixed-engine arithmetic across a cut). If the fresh eval and the chained total diverge beyond tolerance, the sheet is stamped `engine_mixed_chain`/`CONTAMINATED` and **held, not published**.
- **Name:** the published file carries `bh` and `gain` in the filename, e.g. `AXTI_SHORT_bhm1p03_gain11p29_30d_matrix.xlsx` (`m` = minus, `p` = decimal point).
- **Chart:** `tools/generate_zoomable_charts_mac.py` runs on the Mac over `V15_V16_CELL_BY_CELL(_FINAL)`, output `SPREADSHEETS/charts/{stem}_zoom.html` (offline `file://`, Chart.js zoom/pan, `bh`/`gain` in title). No auto-trigger — run it manually.
- **Publish-before-live:** publish happens inside the DONE stage *before* the optional live-verify, so a slow/failing live-verify never loses the published sheet. `V15_SKIP_LIVE_AT_DONE=1` defers live-verify entirely for showcase/proof runs (fleet default).
- **Scheduler requeue:** the scheduler tracks `done30` per cat_side from published finals and launches only unfinished pairs — complete-but-unpublished sheets get relaunched, never stranded.

---

## 33. CHARTS — WHAT MUST BE TRUE

- Single-file offline `file://` HTML, zoomable/pannable, height ~62vh per sheet (not 165vw).
- `bh` and `gain` in both the filename and the chart title.
- OPEN/AUGMENT/REDUCE/CLOSE markers must be sorted by `(bar, OPEN<AUGMENT<REDUCE<CLOSE)` before plotting so a same-bar reduce never renders before its buy (§25) — a reduce cannot precede its open in the sim; if the chart shows that, fix the marker sort, not the sim.
- Charts land on the Mac (pulled from S1 `V15_V16_CELL_BY_CELL_FINAL`); Mac-side files not in `_FINAL` may be pruned by the 60s `--delete` pull, so a sheet must satisfy publish criteria to persist on the Mac.

---

## 34. DAEMON LAYER — RETIRED 2026-10-06 (SCHEDULER ERA)

The herd/sentinel/finisher/assure/redflag daemons are **retired**: none run on the fleet (verified 2026-10-07 — absent from S1 crontab and process list), `tools/v15_local_herd.py` is deleted. Do not revive them; do not cite their logs/flags as live mechanisms.

What replaced each (see §74):

- **Launch/queue/reap** → `tools/v15_fleet_scheduler.py` (S1 cron `*/2`): slots per host, memory guard, OOM/hardcap/stall reaps, `done30` tracking. Log `/tmp/v15_fleet_sched.log` (JSONL per tick).
- **Publish** → in-pilot DONE stage (§32).
- **Refill** → `tools/v15_refill_from_json.py` on demand (JSON truth → XLS).
- **Idle/stuck pilots** → scheduler stall reap (`stall_min`) + OOM reap.

**Never kill an in-flight pilot to make room** — it loses that sheet's compute. Add load only where there is headroom; a RAM-starved box must not get another max-worker pilot.

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
| `v12 prepared ...` text in RED cells | type-incompatible candidate reached the engine (numeric←TF-word, dict←scalar, K=V pollution) | TYPE_MISMATCH grey-skip in pilot + `_coerce_override` reject in `evaluate_v12` (§41.1); re-run affected rows |
| Whole file all `0.0` with reason `all vectors invalid` | legacy fallback loop wrote 0.0 for invalid evals (pre-2026-10-02) | fake zeros — re-run the sym_side on the `_spec` path; never trust or average them |

---

## 36. GLOSSARY

- **Baseline** — strategy gain with all switches/filters at bold/default (+ promoted best-overrides for that `SYM_SIDE`). What is live.
- **Naked delta** — delta of flipping the switch alone (no yellow) vs `cumulative_before`.
- **Yellow (cell/filter)** — a `FILTER=OPT` candidate eligible for a specific switch's row (`L:BI`); its cell holds the delta of `switch + that one filter`.
- **`G` / VECTOR_DELTA** — the row's COMPLETE delta: real JOINT eval, else best single positive, else naked (§56.0 — never an arithmetic sum).
- **`F` / HUSTLE_DELTA** — the row result vs the ORIGINAL (initial) baseline; blank by default under worst2best (see column contract §4.2).
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

The exact control flow `v15_pilot` implements (all 13 tabs, STDEV first). This is normative — code that deviates is wrong.

```
cumulative_gain      = baseline_gain          # from *_BASELINE_METRICS!B2
cumulative_overrides = ingest_best(SYM_SIDE)  # prior-best wins over defaults (no "if k not in" guard)
write E3 = baseline_gain ; keep E2 = "BASELINE" (header string)

visit rows worst-first (--seq-mode worst2best); on NEG/0 jump to next tab's first pending row
(--nav-mode jump; jump reorders visits only — every row still fills)

for each visited row:                          # switch=cand
    switch, cand = row.A, row.candidate
    yellows = opportune_yellows(switch, tab)   # SPECIFIC + lifecycle/ALL + token-overlap + header exists
    cum_before = cumulative_gain

    naked = eval(cumulative_overrides + {switch:cand}) - cum_before      # ~0.07s, RAM
    per_yellow = { hdr: eval(cumulative_overrides + {switch:cand} + {filter(hdr)}) - cum_before
                   for hdr in yellows }                                   # each written to L:BI now
    pos = {hdr:d for hdr,d in per_yellow.items() if d > 1e-9}
    joint = eval(cumulative_overrides + {switch:cand} + {all pos filters}) - cum_before if pos else -inf
    if joint > 1e-9:      G, adopted = joint, pos            # JOINT first (§56.0 — never a sum)
    elif pos:             G, adopted = best_single(pos)       # best single positive
    else:                 G, adopted = naked, {}              # naked (covers no-yellow rows)
    write G (+F iff V15_WRITE_HUSTLE=1); write every L:BI cell (pos green / neg red)

    if G > 1e-9:
        write C = "switch=cand + " + join(adopted)           # bold if non-default
        write K = adopted headers
        cumulative_gain      += G
        cumulative_overrides += {switch:cand} + adopted
        # stay on THIS tab, next row's E = cumulative_gain
    else:
        leave C blank ; E of next row stays BLANK ; do not advance cumulative
        # jump: next visit is next tab's first pending row (fill_tab: stay instead)

# after all tabs: endgame cycle, then winning set through backtest_v12_engine -> H/I; DONE publishes (§§32,69)
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
6. **Parity** — add a faithful scalar twin path in `backtest_v12_engine` (or confirm the live `process_position` already does it) before promoting on vector numbers alone (§43; then 365D robustness, §44).
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

### 41.1 TYPE GATE (2026-10-02) — incompatible candidates are never evaluated

A template candidate whose type the engine cannot consume must never reach `simulate_one`. `v15_pilot._cand_compatible` (mirrored by `tools/opt/evaluate_v12._coerce_override`) rejects, before any eval:

- numeric field + non-numeric string (`WT_DC_DC_POS_THRESHOLD_SHORT=OFF`, `...=15m`, `MTF_GR_EXIT_MIN_TFS=D`) — the engine's `float(...)`/`int(...)` reads raise `ValueError`;
- dict/list field + scalar (`LR_BAND_LADDER_TF_BOTTOM=1`) — `vec_decisions` `.get(...)` reads raise `AttributeError: 'str' object has no attribute 'get'` (only when the owning family is enabled, e.g. `LR_BAND_LADDER_ENABLED=True`);
- any raw value containing `=` (`REENTRY_FILTER_MIN_PASS=2`, `HLR_REENTRY_MULT_D=1.875 + ...`) — column-C display text, never a real value. The one exception is the `X=X` dup typo (`DC_HARD_STOP_TF=D=D`, 271 rows), which collapses to `X`.

Consequences: such rows grey-skip with a `TYPE_MISMATCH` reason (never evaluated, never RED, never 0.0 — §56.0 grey rule already covered dict-valued groups); such yellow cells stay blank and are recorded as `type_skipped` in the progress JSON; ingested best-overrides are cleaned by `_clean_ingested_overrides`. Before this gate, these crashes surfaced as `v12 prepared ...` text (`reason[:30]`) in RED sheet cells. Invalid evals still never produce 0.0 (§60.4).

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
ssh s1-int 'cd ~/binance-sandbox && V15_PROGRESS_DIR=/tmp/proof_gdx V15_SKIP_LIVE_AT_DONE=1 SWITCH_PARITY_REGISTER=0 \
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

Whether a `SYM_SIDE` starts from defaults or from prior-best determines its baseline (§14). **Gating law: §64 currently FORBIDS starting from prior-best — all rounds run defaults-pure until every sym_side has full 30D coverage** (the run25 defaults-purity violation). This section describes the MECHANISM, which re-activates when §64's gate lifts:

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
- Flag sweeps run on a ~10-minute cycle (the old redflag/sentinel daemons are retired, §34 — the scheduler's stall reap + on-demand `v15_refill_from_json.py` cover the loop); a repair agent picks up RED cells, roots the cause (usually a slow/broken predicate or a missing NPZ key), fixes forward, and the refill path recomputes just those cells from JSON truth.
- **Never** leave a cell blank/None on stall, never erase a computed value to hide a stall, never let one cell push a workbook past ~20 min (hard target) / 60 min (absolute). (Observed 2026-10-07: full symbols run 1–12h — the 20/60min targets are NOT met; see §74 for the wall-time breakdown and the reduction plan.)
- BadZip protection: every write via `_atomic_save` (tmp+fsync+rename, `ZipFile≥10` validate, keep `.bak`); the JSON progress file is the source of truth so a corrupted xlsx is rebuilt, not lost.

---

## 51. WHAT "GOOD GAINS" MEANS — PROMOTION GATES

A sheet with "good gains, all deltas applied correctly" means:

- Headline `gain` = finisher's **fresh re-anchored full-set eval** (§32), not a chained total — reproducible to the last decimal by an independent `evaluate_sanitized` re-run.
- `gain > baseline` (greedy only climbs) and `gain` beats or reasonably trails B&H (`bh` in the name for context; a strategy can be worth keeping below B&H if it has far lower DD/exposure).
- Metric gates (three different gates — do not conflate): **operating gate** `pool_sharpe > 0.2` (+ `TIM 20–80`, `max_dd_pct ≤ 30`) for sweep promotion; **interim bar** `> 0.5`; **real-money bar** `> 1.0`. Trade count above the sample floor (`≥30/sym`, `≥48 crypto`/`≥100 stocks` universe) else `[DIAGNOSTIC ONLY]`.
- **DD semantics (USER 2026-10-03): `max_dd_pct` = realized peak-to-trough on peak-concurrent equity (the standard). `max_dd_mae_pct` (DDmtm on charts) = wick-adverse MTM on the SAME base = the max the strategy COULD have lost, always ≥ DD. Per-trade `pnl%` divides by THAT trade's deployed — never compare it against DD directly; chart `eq%` (= pnl/peak equity) is the reconciling column. Mean-deployed DD normalization stays BANNED (MU_SHORT 125.8% — exceeds capital ever at risk).**
- **Peak-concurrent fix (USER 2026-10-03): trade `deployed` sums fills and never subtracts REDUCEs — `_peak_concurrent` now replays the signed OPEN/AUGMENT/REDUCE/CLOSE event stream and reports max open, not gross churn (GALAUSDT_SHORT proof: $510.77 → $326.27, gain 3.21% → 5.02%, DD 1.91% → 2.96%, DDmtm 2.08% → 3.20%). ALL gain/DD %s fleet-wide rescale by each eval's own churn factor (~1.0–2.5x, augment-heavy symsides move most); deltas stay differences of honest %s. Engine `peak_capital` (locked) still reports fill-sum — honest surfaces use `E._peak_concurrent`, engine stays `*_RAW` diagnostic only.**
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
- **Metrics** use CLOSE rows only: `gain_pct = pnl_usd / capital *100` where `capital = peak_notional(events)` (peak-based per the §51 peak-concurrent fix — mean-deployed normalization is BANNED), `pool_sharpe = mean(per_trade_ret)/stdev`, win rate = `#(pnl_pct>0)/#CLOSE`.
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

## 56. OPERATOR FILL SPEC — VERBATIM & AUTHORITATIVE (2026-09-29, REVISED 2026-09-30 — see §56.0 FIRST)

This is the operator's own statement of how `TEMPLATE_*.xlsx` must be filled. **It is LAW and supersedes any conflicting phrasing above.** Every rule here is mandatory; the right-hand notes say where it is enforced.

### 56.0 REVISION 2026-09-30 — SEQUENTIAL FILL (SUPERSEDES R3, R7, R13, R15–R18 and the no-yellow "next TAB" part of R24)

Implemented in `v15_pilot.py` `_spec_fill_workbook` (md5 `820cb5ca`, deployed s1/s2/s5 2026-09-30) and verified on a live
fleet sheet (MSTR_LONG: 3165 rows vs its progress JSON, 0 violations) plus the isolated XRPUSDC_LONG proof.

**Template layout (every SWITCH_SHEETS tab, resolved by header):** `A Switch | B default | C override | D Family | E BASELINE |
F HUSTLE_DELTA | G VECTOR_DELTA | H LIVE_DELTA | I LIVE_SHARPE | J REAL_COMPLETE | K PER_ROW_FILTERS | L is_default |
M AVG_DELTA | N POS_SYM | O.. yellow "FILTER=opt" columns`. White switch rows always above orange filter rows.

**Defaults (bold) are LAW and the pilot relies on them:**
- Every (tab, switch) group and every yellow filter has EXACTLY ONE default: bold B cell + `is_default=YES` in L (every other
  row `NO`); a filter's default = the one bold `FILTER=opt` header carrying a `DEFAULT` note. 0 or 2 defaults = broken.
- Source of a default = the venue's live config (`config.Config` crypto, `config_tradier.TradierConfig` stocks), else
  QuickConfig (`apply_tradier_defaults()` for stocks), overlaid with `data/cat_side_promotions.json` (avg-delta promotions).
  per-sym_side overrides live in `data/hourly_reconfig/{trb,inf}/active_config.json` + `per_sym_active_config.json`.
- **FOUR defaults per switch (built 2026-09-30, USER-unlocked; Stage 5):** `data/cat_side_defaults_4.json` = one default set per
  CRYPTO_LONG / CRYPTO_SHORT / STOCKS_LONG / STOCKS_SHORT, built by `tools/build_cat_side_defaults_4.py` from the template
  bold/is_default rows (refuses on any 0/2-default violation; re-run automatically by `v15_avg_delta_apply.py --apply`).
  Single accessor `cat_side_defaults.py` (`get`, `get_for`, `cat_side_of`, `defaults`). Precedence EVERYWHERE:
  **per-sym override > cat_side default > the single global value** — live crypto `ez_manage._psym_get` (all no-override
  fallbacks via `_ezm_default`), live stocks `tradier_manage._cfg` (step 4 before the global value), sweep engine
  `evaluate_v12.prepare/build_cfg_npz` (stocks: full `apply_tradier_defaults()` then `QuickConfig.apply_cat_side_defaults`),
  pilot (bold passed explicitly). Kill switch `CAT_SIDE_DEFAULTS_ENABLED` in config.py / config_tradier.py / QuickConfig
  or env `CAT_SIDE_DEFAULTS_DISABLED=1`. At deploy the 4 sets equalled the live config (0 differences → no live change);
  sweep baselines moved to live parity (QuickConfig's own values had been running for many switches).
- Groups whose default is not among their options, with no config field, a dict value or no options are GREY (col-A font
  `FFBFBFBF`) and never calculated; they still keep exactly one is_default row.
- The pilot refuses to run (`[DEFAULTS-GATE]`) unless every non-grey group has exactly one YES = the bold row, and passes
  every bold default to the engine EXPLICITLY (`template_bold_defaults()`): the running default is the bold set, never
  QuickConfig's own values. Audit: `tools/v15_template_defaults_fix.py --audit` (report-only).

**No 30D sheet is ever disqualified — invalid baselines are REPAIRED (USER 2026-09-30, §58 rules, gates never loosened):**
`_credible_baseline` (up to `V15_ADAPT_MAX_STEPS` = 12 steps, each step stops at the first credible set) picks the fix by
what fails: **trades < floor** → soften bool FILTER/GATE/BLOCK/REQUIRE/CONFIRM/VETO/GUARD switches + open ENTRY_/REENTRY_
paths; **TIM > 80 or DD > 30** → rows of EXIT_STRUCTURAL / EXIT_VELOCITY / REDUCE_PROFIT_LOCK / REDUCE_SIGNAL_RATER /
REENTRY_WINDOWED / REENTRY_ADAPTIVE, exit blockers loosened (EXIT…BLOCK/VETO/GATE/FILTER/REQUIRE/GUARD/NOLOSS → False),
reentry filters on (REENTRY…FILTER → True); **ultra-negative** → greedy best row keeping validity. Rank = (credible, valid,
least TIM/DD excess, trades, gain). Proof 2026-09-30 (s2): AAPL_LONG TIM 90 → DAYTRADE_DC_TARGET_TF + STOP_TF = 15m,1h →
165 tr, TIM 77, +6.52, valid; MSTR_LONG TIM 81 → one exit → TIM 79, +7.65, valid. Still not credible after all steps → the
sheet is filled anyway (`baseline_not_credible` flag), only VALID rows promote.
**A sheet is FINISHED only when its final set complies** (valid: TIM ≤ 80, DD ≤ 30, trades ≥ floor — a NEGATIVE gain is NOT
a disqualifier). At DONE a non-compliant final set is repaired the same way (steps in the `COMPLIANCE_REPAIR` tab +
`compliance_repair` in the progress JSON); still non-compliant → `not_compliant` flag and **no bh/gain filename** (the pilot,
`v15_harvest_done.py` and `v15_finisher.py` all refuse). `v15_assure watch` is stopped on s1/s2 (old column semantics).

**Fill order:** tabs in `SWITCH_SHEETS` order (STDEV first), rows in order, white rows before orange rows, NO row skipped,
NO tab jumping (R16/R17 jump is gone). Hustle shuffle stays the only exception. Grey / dead-vector / empty-candidate rows are
recorded with their reason and never evaluated.

**Step 1 — running default + prior best:** running set = bold defaults + previous-best overrides for that sym_side (best JSON,
previous progress `cumulative_overrides`, previous sheet C). Every NON-default previous-best setting is written ONCE, bold,
as `SWITCH=value` into C of the row whose B equals that value (else the switch's first row).
**Step 2 — initial baseline:** the real `v12_quick_engine` gain of that complete set → **E3** of the first tab (chain start).
The bold row needs no calculation: it IS the baseline.

**Per row** (every eval = running set + this switch=cand [+ one filter], delta vs the LATEST baseline):
- Every yellow cell gets its own delta (pos or neg; `INVALID <reason>` if the engine marks it invalid; a yellow equal to the
  naked switch = non-binding, shows 0.0 grey, never counted as positive).
- **PER_ROW_FILTERS (K)** = every positive yellow as `FILTER=opt`.
- **VECTOR_DELTA (G)** = the row's COMPLETE delta vs the LATEST baseline: the switch + ALL positive filters evaluated together
  (a real joint eval — never an arithmetic sum of deltas that each already contain the switch's own effect). If the positive
  filters do not stack (joint ≤ 0), the best single REAL positive (filter or naked switch) is used. With no positive
  filter, G = the naked switch delta. G is BLANK only on the running-default (bold) row when none of its yellows is positive.
- **HUSTLE_DELTA (F)** = the same row result vs the ORIGINAL (initial) baseline (temporary column).
- **BASELINE (E)**: G > 0 → promoted: running set advances, the NEXT row's E = latest baseline + G (the value itself may be
  negative — "positive" refers to the delta vs the previous baseline). G ≤ 0 / blank → the next row's E stays EMPTY.
  E values only go up (`[E-MONOTONIC-FAIL]` refuses a lower one).
- **override (C)** on promotion: `SWITCH=cand` (unless it was already running) + every promoted `FILTER=opt`. A key lives in
  exactly ONE C cell: when a later promotion sets the same switch/filter to another value, the old part is ERASED from its
  cell (`[C-SUPERSEDE]` log) and the new setting is written in the promoting row's C.
- **Slow cell** (> `YELLOW_TIMEOUT` 10 s): logged, cell RED with the reason, fill CONTINUES with the next cell/row; every red
  cell is retried at the end of the workbook (`V15_RED_RETRY_S`, default 120 s) and gets its real delta vs the baseline it was
  measured against (never promoted after the fact — the chain has passed it). Still failing → stays RED for `v15_assure`.
- LIVE_DELTA / LIVE_SHARPE stay blank until the workbook is complete (R23 unchanged).

> **§56 tool-name map (2026-10-07 — the verbatim law below stands; only tool names moved):**
> `v15_avg_delta_apply` → `tools/v15_daily_template_update.py` (avg/promotions writer);
> `v15_avg_delta_rebuild` → `tools/v15_cell_evidence.py` + `data/avg_delta_pos_sym.json`;
> row adds → `tools/v15_switch_add.py` (the ONE add-switch exception, SWITCH_ADD_GUIDE.md);
> books → `tools/v15_template_bookkeeper.py` (S1 cron 12:30 UTC, sole book writer);
> bulk restructures → `tools/v15_template_staged_apply.py` (staged+verified; currently refuses on the
> pre-existing violation backlog). Legacy `SPREADSHEETS/TEMPLATE_*.xlsx` set archived
> (`backups/archive_generic_templates_20261007/`); the ONLY templates are the 4
> `SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_*.xlsx` (§70).
> `v15_add_orange_rows` / `v15_add_switches` stay refused (superseded, do not un-refuse).

**Templates are changed by ONE script only:** `tools/v15_avg_delta_apply.py` (after `tools/v15_avg_delta_rebuild.py`, stats
per `SWITCH=value` / `FILTER=opt`) writes AVG_DELTA/POS_SYM by header, promotes the highest POSITIVE avg-delta row/header of a
group to bold + `is_default=YES` (previous default regular + `NO`, ledger `data/cat_side_promotions.json`), and re-orders
groups worst_first (whole rows incl. every yellow cell move together; white above orange). It verifies before saving.
NO script adds or removes rows: `v15_add_orange_rows`, `v15_add_switches`, `v15_prune_orange` refuse; the pilot no longer
merges template rows into existing sheets; `v15_template_fix`, `v15_template_defaults_fix`, `verify_template_defaults` are
report-only. Yellow cells (interim, until the yellow-map agent's rebuild): `tools/v15_yellow_by_tokens.py` — a cell is yellow
iff the row's switch name and the column's filter name share ≥ 2 `_`-tokens.

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
- **R13 [SUPERSEDED by §56.0: C/K/G rules]** — Positive yellow → add header name (append, never overwrite) to `override` AND `PER_ROW_FILTERS`, and add its delta to `VECTOR_DELTA` (sum if a value already exists).** (§5.3, §37)
- **R14 — Row complete when all its yellow values are calculated and summed into the delta.** (§5.3)

### 56.4 Baseline chaining & tab navigation
- **R15 [SUPERSEDED by §56.0: G = real joint delta vs LATEST baseline]** — `VECTOR_DELTA` = sum of the row's POSITIVE yellow deltas. (§5.4, §14.2, item 4)
- **R16 [SUPERSEDED by §56.0: always the next row, E only after a positive row]** — POSITIVE `VECTOR_DELTA` → move DOWN one row (next switch) on the SAME tab; add the delta to the previous baseline and write it in the baseline column of the next row; repeat.** (§5.4, item 6)
- **R17 [SUPERSEDED by §56.0: NO tab jumping — rows filled in order]** — None / zero / negative `VECTOR_DELTA` → do NOT move down; go to the FIRST pending row in the NEXT tab, write the baseline value there, and repeat.** In the next tab: positive → stay and go down next row adding delta; none/0/neg → move to next tab again. (§5.4, item 5)
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
(~~0→1/10, 1→1/5, 2→1/3~~ — 2026-09-29 values, superseded 2026-10-06: current probabilities are 0→1/20, 1→1/10, 2→1/6, 3→½, >3→every — see §73) via a v15_pilot change, NOT row deletion (nothing is ever deleted).
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

## 59. FLEET RUN REGIME — RETIRED 2026-10-06 (SCHEDULER ERA; was USER 2026-09-29 PM)

The sweep-cron/full-sweep-driver regime below is **retired** (replaced by `tools/v15_fleet_scheduler.py`, §74). Kept as history; only the pipeline-order law survives:

**Pipeline (order is law):** 30D run #1 ALL sym_sides → recalc avg-delta → rebuild templates → 30D run #2 ALL sym_sides → 365D verification + adjustments (§58). No 365D run until run #2 completes.

**Guard-bypass law (survives):** the pilot's `ALREADY FINISHED (early) — MUST NOT RETOUCH` skip only fires when it finds a prior progress JSON in `V15_PROGRESS_DIR`. Every fresh pass MUST point `--progress-dir` at a NEW empty iso dir — that is what re-runs a finished side on the new template. Sheets still land in `SPREADSHEETS/V15_V16_CELL_BY_CELL/`.

(Retired detail: `v15_sweep_cron.sh`/`v15_full_sweep_driver.py` sharding, `v15_delta_health_monitor.py` Mac cron, the unconfirmed yellow-agent job, `reset_fleet_20260929.sh` — see git/backups history. Servers run backtests AND (S1) the live stack — §11.)

---

*End of the 2026-09-29 body — if a procedure above conflicts with older text, this wins. §59's pipeline-order law and §58 (negative 365D = faulty 30D sheet → repair loop, never a disqualification) are operator-authoritative. §14–§57 are the 2026-09-29 operator-corrected additions; §56 is the operator's verbatim fill spec and is the highest authority on how the templates are filled; §57 + DAILY_OPTIMIZATION_PLAN.md define the daily self-optimization loop; current run regime lives in §74.*


## 60. DEVELOPMENTS 2026-10-01 → 10-02 (ESTABLISHED FACTS — read before touching data, engine or scheduler)

**60.1 Look-ahead audit (crypto).** Crypto 1h/4h/D NPZ arrays leaked the full *current* HTF bar into every 15m row. `vec_decisions/htf_causal_align.py` shifts those arrays at load time, only for stores that lack the marker `htf_align='causal_v2'/'causal_v3'`; rebuilt NPZs carry the marker and skip the shift. `V12_HTF_LEAK_LEGACY=1` restores the old behaviour (diagnostics only). Weekly/monthly arrays were leaky AND stale; the 32 composite/daily keys are covered by deploy NPZW/001. The scalar engine applies the same align through `backtest_v8_harness.py`. Crypto results produced before the fix are quarantined (`contaminated_crypto_lookahead_20261001`) but still COUNT for `pos_sym` (user decision). Bollinger sigma auto-tunes from the last 500 bars (stocks moved ±0.06, crypto cumulative moves more).

**60.2 NPZ builder + guard.** Builder `backtest_v8_precompute.py` (md5 `3e5c1477`, identical on all hosts) has D→W/M synthesis, causal_v3 W/M mapping and the has_long_d waiver. `tools/npz_guard.py` refuses any overwrite that shortens an NPZ (keep ≥80 % of bars/span, never lose keys). A precompute run by another session once truncated 154 s1 stock NPZs (restored from backups; guard patched). **A full rebuild of a CRYPTO NPZ from `klines_cache` is impossible** (cache holds ~17 days of 15m; builder says `insufficient coverage … need 30000 bars`). Crypto NPZ freshness therefore = whatever the NPZB loop (`~/npzb`, cron */10) last installed; the scheduler's crypto staleness gate is 336 h for that reason.

**60.3 Stock data truth.** Tradier (5m + D, D goes back years) is the source of truth for every stock bar from July 2026; Massive is ONLY deep-history backfill (v2 merge rule: Tradier wins from its first bar). Tradier caches contained provisional volume-0 quote-snapshot rows from late September and a naive-ET-tagged-UTC bug in `tradier_klines.py` (4 h shift). Fix path = `tools/stkt/rebuild_tail.py` (installed on s1 as `tools/stkt_rebuild_tail.py`): fetches true-UTC time&sales, rebuilds the NPZ tail with the UNCHANGED builder, validates pre-cutoff bars identical + post-cutoff == Tradier, npz_guard, atomic install. Stock NPZs hold ~730 days (about 40 universe stocks have only 28–80 days: rebuilt by `tools/stkh/*`; they are `history < 32d` unready in the scheduler until long enough). Rolling cutoff env: `STKT_CUTOFF_EPOCH`, `STKT_OVERLAP_START`.

**60.4 Row semantics.** A WHITE row = a SWITCH that ADDS trades. An ORANGE / YELLOW cell = a FILTER/GATE that REDUCES trades. Trade reduction is never a physical cap. A switch/filter exists only if it is identically wired in live AND vector AND all configs. Anything that changes baselines is a switch default-OFF until tested. Every filter must yield a delta in every test; `0` only for an exact 0.00000, never for None/invalid.

**60.5 Evidence rules (pos_sym / sampling / 365D).** `pos_sym` = number of sym_sides in which the row/cell had a positive recorded delta, summed over a month; rows with `n_sym ≥ 20` and `pos_sym` 0/1/2/3 are recomputed only 1-in-20/10/6/3 passes (exact zeros count as evidence; flag `data/possym_sampling.flag`, pilot md5 `f2a2a469`+). **User rule 2026-10-02: any filter-on-a-switch or switch alone that never produced a value in the 365D row evidence is DEAD and goes to the 1-in-20 test.** Row-level 365D (`tools/v15_row365_filters.py`, filters on non-zero rows, top-50 |30D delta| rows per sym_side) is evidence for yellow/orange classification and pos_sym — it is NOT a promotion test and nothing from it goes live; the user capped it at ~5 long + 5 short sym_sides per cat and then 30D only. **SUSPENDED 2026-10-03 per user (hollow sheets: 44% rows skipped + 78% rows zero-yellows fleet-wide, no warnings): `data/possym_sampling.flag=0` and `data/tablevel.flag=0` (new, pilot md5 `b80f8f5c`+) restore FULL per-row + per-yellow calculation; policies stay in code, re-enable = echo 1. NOT_WIRED_VEC / TYPE_MISMATCH / ZERO_TRADES skips remain (correct). Every DONE now prints `[SKIP-ALARM]` coverage + writes `*_SKIP_ALERT.txt` when computed <50% or >85% rows zero-yellows.**

**60.6 Defaults rounds.** Defaults round id = `runN-json<md5>-tpl<md5>-eng<md5>` stamped into every progress JSON (`V15_DEFAULTS_ROUND`). NEVER average deltas across default rounds. Sweep defaults are read from `data/sweep_defaults/cat_side_defaults_4.json` via env `CAT_SIDE_DEFAULTS_PATH` (module `cat_side_defaults.py`); the LIVE file `data/cat_side_defaults_4.json` is hot-reloaded by live ez_manage and is NEVER written by the autopilot. Promotion into live is a human decision.

**60.7 Scheduler (tools/v15_fleet_scheduler.py, s1 cron */2).** Symbol-pair slots per host (`max_pairs`), memory and non-niced-CPU admission, per-run attempt reset, slot release, `V15_SCHED_NO_CHAIN=1` (no 365D verify/REPAIR chains), market-clock venue order (stocks first when the US market is closed), readiness gate per SYMBOL (a stale/short symbol is skipped and listed, it never blocks the venue — user: "you only need ONE symbol complete, not 16-0"). Stocks in `config_tradier.NON_SHORTABLE` (+ `data/non_shortable_mac.json`, Mac config is truth) get NO `_SHORT` compute. Each tick writes `data/autopilot/sched_snapshot.json`.

**60.8 Yellow paint applied 2026-10-02 (explicit user request).** `tools/v15_yellow_paint_from_proposal.py --apply` from `data/yellow_discovery/20261001`: BRIGHT FFFF00 = any evaluated non-zero effect; none = evaluated and always exactly zero/inert; LIGHT FFF2CC = UNKNOWN (never evaluated, never 0). Row fingerprints (all non-filter columns) verified identical before/after. Backups `backups/before_yellow_paint_202610020049_*`.

**60.9 Engine deploy protocol.** Engine files change only through `tools/engine_deploy.py --stage DIR` (atomic, md5, import check on all hosts, `data/engine_deploy/CURRENT.json`). Current engine md5 `ecacb3be`. Pilots import the engine at start: record md5 per result.

## 61. AUTOPILOT (USER 2026-10-02) — ON HOLD (`#BUILDER_HOLD` in S1 crontab, verified 2026-10-07). See AUTOPILOT_RUNBOOK.md.
`tools/v15_autopilot.py` (s1 cron */5, flock, resumable state `data/autopilot/state.json`) drives rounds: SWEEP (scheduler launches 30D pilots into the current progress dir) → COLLECT (per-host partials → merged workbook) → TEMPLATE (`v15_daily_template_update --apply`, NO `--sync-defaults`) → NORMALISE (`TEMPLATE_FINAL_NORM`) → DEFAULTS (sweep-only json) → SYNC (s2/s5, md5) → RESTART (new progress dir + defaults round id). `tools/v15_npz_keeper.py` (s1 cron */10) refreshes the NPZs of the next 10 pending STOCK symbols just before their turn (Tradier tail rebuild on s1, push to s2/s5 with md5, skipped for a host that is running the symbol). s1 is the template source of truth while the user is away (Mac template pushes are disabled, `#AUTOPILOT_OFF#` in the Mac crontab). Failure policy: a failing round-end stage is retried every tick; after 6 failures the round restarts on the PREVIOUS defaults so servers stay busy, and `STATUS.json` carries an `alert`.


## 62. MONDAY FINAL PHASE + GO-LIVE (USER 2026-10-02, AUTHORITATIVE) — see AUTOPILOT_RUNBOOK.md §8
Mon 2026-10-05 06:00 UTC: best (newest-round) 30D sheet of every sym_side → 365D verify → §58 repair loop until both windows are positive → live-faithful `backtest_v12_engine` parity run of the 30D set → qualifiers frozen 12:30 → `tools/golive_final.py` on the Mac writes the live books 12:50 (retry 13:10) before the 13:30 open. Only both-positive sym_sides trade; negative/unverified get `_NEG_BLOCK` (and their crypto 365D certification is removed) — they do not trade on old settings either. Qualification gates: `final_both_ok` (30D+365D valid, gain>0, TIM≤80, DD≤30, ≥10 trades/30D, ≥80/365D, 365D span ≥330 d), crypto certification via `confirm_365d.confirm_symside` on exactly the final recipe, parity not valid-negative. Parity itself is a RECORDED measurement: the scalar harness often ends without a PARITY line (UNAVAILABLE) — that is not a block; this is a deliberate deviation from "parity must pass" because strict parity currently passes almost nowhere (see `data/reports/parity/results_*.jsonl`). Live precedence facts: stocks live `_cfg` reads `data/hourly_reconfig/trb/active_config.json` FIRST, then the books, and `data/full_recipe_live_config.json` exact recipes override both — the go-live writer replaces/removes those entries for promoted sym_sides. Crypto accounts `fin/inf/flz/men` have their own overlay dirs under `data/hourly_reconfig/` that are NOT touched.

## 63. SELL_TOP TOP-CONFIRMATION + RECROSS-REENTRY LAW (USER 2026-10-02, MOVRUSDT_LONG autopsy — AUTHORITATIVE)

**Evidence.** `SPREADSHEETS/V15_V16_CELL_BY_CELL/MOVRUSDT_LONG_bh268p19_gain58p27_30d_matrix.xlsx` (+ `.html`): BH **+268.19%**, strategy **+48–58%** (Δ −216 pp), TIM 48–60%, 160 opens / 130 closes / 74 reduces. During the vertical rally (bars 2648→2718, 1.07→2.02, +89% in 70 bars) the ledger shows the two mandated fixes verbatim:

**MISTAKE 1 — SELL_TOP sells mid-rally.** `HLR_TOP_EXIT_SELL_TOP` fired **30×** (every +2–8% leg: 1.073, 1.180, 1.228, 1.281, 1.455, 1.576, 1.735, 1.951, 2.026 …), each a **FULL exit** (`frac = 1.0`, `v12_quick_engine.py:13233`). Mechanism: the fire predicate (`vec_decisions/quick_reduce_strong.py`, mirror of `ez_positions_quick.py:3706-3745`) counts **EXHAUST** (`vel > 0 & accel ≤ 0` = still rising, one-bar deceleration) as a top-confirming TF; with `HLR_TOP_MIN_TFS=1` (loosest — promoted into MOVR's set) + `HLR_TOP_MIN_GAIN_PCT=2.25` + `VEL_4H/VEL_D_THRESH=0.0` (any negative tick), any +2% position is sold on the first HTF deceleration bar. Same bar, `PARTIAL_PROFIT_LOCK` takes 50% first (`vec_decisions/reduce_profit_lock.py:55-60`, runs before the qr block at `v12:13101`) and SELL_TOP dumps the rest — a same-bar double-take. SELL_TOP also ignores `MIN_HOLD_BARS_BEFORE_EXIT` (no `min_hold` check at `v12:13229`, fires with `bars_held=1`) while every other exit honors it.

**MISTAKE 2 — no immediate reentry after a premature sell.** After 2685 @1.281 the recross (2698 @1.29) reentered only at 2700 @1.353 (+5.6% chase); after 2751 @1.7285 the recross (2754 @1.74) reentered only at 2764 @2.003 (**+16%** chase). `HARDCODED_RALLY_REENTRY` (close > exit, `v12:12571-12583`, cooldown bypassed) fires, but the unconditional `_open` choke gates (`v12:12660-12746`: KG-all-TFs / GR-all-TFs / STOP-level / DC4H blocks) veto it until price reclaims HTF EMAs. Contrast: TARGET-DC exits get `cd=0` + immediate recross fire (`v12:13212-13218`, `v12:12560-12569`, 2026-09-26 mandate) — SELL_TOP exits get full `cd=cooldown_bars` and no choke bypass.

**Why the sweep never fixed it (template gaps, all verified 2026-10-02):** (a) `HLR_TOP_MIN_TFS` lived in `ENTRY_REVERSAL_BOUNCE` (wrong lifecycle, fills first) with candidates `1/2/2` only — tightening to 3/4 was untestable; `MIN_TFS=1.0` promoted. (b) `HLR_TOP_VEL_4H/D_THRESH` were single-row (`0`, untestable); `HLR_TOP_MIN_GAIN_PCT` maxed at `3.0`. (c) HLR rows got **ZERO yellows** (no REDUCE header shared ≥2 `_`-tokens; dict had no REDUCE-applicable SPECIFIC HLR filter). (d) `HLR_TOP_EXIT_ENABLED` False/True rows were never evaluated on MOVR (blank F/G). (e) `FILTER_DICTIONARY_V2` MIN_TFS rows said default `0.0` (live truth is `2`) with the YES flag on the `0` row. (f) The avg-delta promotion layer had promoted `MIN_TFS=0` (CRYPTO_LONG) / `1` (CRYPTO_SHORT) — premature selling tests positive on average because holding runs into lossy `MULTI_TF_EXIT` closes (72 on MOVR, mostly red); tightening was never in the candidate set so the loosening loop could not be broken by evidence.

**Rule (law from 2026-10-02):** a SELL_TOP-family switch (`HLR_TOP_EXIT_ENABLED`, `HLR_TOP_MIN_GAIN_PCT`, `HLR_TOP_MIN_TFS`, `HLR_TOP_VEL_*_THRESH`, `HLR_TOP_EXIT_LIVE_SANCTIONED`, `HLR_TOP_RECROSS_BYPASS_ENABLED`, `HLR_RECROSS_BYPASS_BARS`) is only promotable together with its top-confirmation filters; a top exit without HTF rollover proof (velocity negative, not merely decelerating) is premature by definition. Every future SELL_TOP-family addition ships tightening candidates AND per-switch confirmation yellows in the same change. **Order law:** sanction rows precede the HLR confirmation rows in `REDUCE_SIGNAL_RATER`, so tightening is always evaluated with HLR alive — a sanction placed after the block would test confirmation against a dead exit (all-zero deltas) and silently skip the tightening it exists to enable.

**Template change applied 2026-10-02 (all 4 `TEMPLATE_*`, backups `backups/before_selltop_topconfirm_*`):** (1) moved `HLR_TOP_MIN_TFS` group `ENTRY_REVERSAL_BOUNCE` → `REDUCE_SIGNAL_RATER` (deduped double-`2`, kept YES); (2) naked candidates `MIN_TFS 3,4`, `MIN_GAIN 5,8`, `VEL_4H/VEL_D -1.0,-2.0` (L=NO, non-bold, blank M/N); (3) 6 per-switch yellow headers on both REDUCE tabs (`HLR_TOP_MIN_TFS=3,4`, `HLR_TOP_MIN_GAIN_PCT=5,8`, `HLR_TOP_VEL_4H_THRESH=-1.0`, `HLR_TOP_VEL_D_THRESH=-1.0`) + 4 bold `DEFAULT` headers (`=2,=1.5,=0.0,=0.0`); token rule (≥2 shared) yellows them onto every HLR row with no pilot change; (4) 4 SPECIFIC `FILTER_DICTIONARY_V2` rows (Sheets=`REDUCE`, gates=all six HLR knobs) + fixed MIN_TFS GENERAL rows (default `2`, opts `0–4`, YES on `2`). Audit `tools/v15_template_defaults_fix.py --audit`: zero new structural violations (stocks identical; crypto only the tolerated `filter default 2 != promotion 0/1` mismatch class, updater-owned).

**Locked-engine follow-ups — DONE 2026-10-02** (user `unlock all needed fix and relock`; backups `backups/before_selltop_engine_202610022247_*`): (i) SELL_TOP full exit gets TARGET-style `cd=0` (`v12:13281` `if _qr_fire: cd = 0`); (ii) recross fire extended to `SELL_TOP`/`HLR_TOP_EXIT` reasons (`v12:12572`) + sweep-gated choke bypass (`v12:12674-12701`, `HLR_TOP_RECROSS_BYPASS_ENABLED` default False, window `HLR_RECROSS_BYPASS_BARS=32` 15m bars) skipping ONLY the unconditional 2026-09-27 hard blocks (DC4H/STOP/KG/GR) — sweepable gates are never bypassed; (iii) qr honors `MIN_HOLD_BARS_BEFORE_EXIT` (`v12:13252` `_qr_hold_ok`); (iv) PPL double-take reviewed, NO code change: PPL-before-qr mirrors live tick order, and with sanction default OFF the double-take disappears unless the sweep promotes the pair — the sanction gate below is the binding fix.

**Core finding behind the fix — sanction design:** live `QUICK_REDUCE_TECHNICAL_ONLY` SUPPRESSES `HLR_TOP_EXIT` (`rec=STRONG_REDUCE` + reason without a named token → `_TRAP_SUPPRESSED`, `ez_positions_quick.py:14902-14909`) while vec modeled it as firing — every backtest of HLR was testing an exit live never takes. Fix: `HLR_TOP_EXIT_LIVE_SANCTIONED` (default False in `config.py:2620`, `config_tradier.py:3791`, `QuickConfig` + `AUTO_WIRED_PARAMS`): vec qr requires it (`v12:13251-13253`), the live trap carves it out (`ez_positions_quick.py:14906-14908`). The sweep promotes True per-sym where the exit wins; only then do vec and live both take it. Live twins: MIN_HOLD suppression (`ez_positions_quick.py:14914-14931`, 3m-bar idiom mirroring `ez_manage.py:46660`) and reentry-loop T1 inversion (`ez_positions_quick.py:16695-16704`): a sanctioned+bypassed SELL_TOP exit flips dip semantics to continuation-recross within the window, signaled by `_hlr_top_exit_registry` (pending_reentries reasons are generic and lose the HLR tag). Template rows added (backups `backups/before_selltop_switchrows_202610022333_*`): 7 naked rows before the HLR block in `REDUCE_SIGNAL_RATER` (sanction False YES/True; bypass False YES/True; bars 32 YES/16/64) + GENERAL dict IDs 115–117. Parity caveats (known, out of mandate): (a) vec `min_hold` counts 15m sim-bars vs live 3m bars (pre-existing, all exits); (b) stocks HLR live is stubs-only, sanction stays False; (c) the live registry is in-memory — a restart degrades to dip semantics (safe direction).

**Proof round `selltop_eng_20261002` (2026-10-03, new engine + new templates, isolated `--out`, all numbers fresh-verified):** mechanism A/B first — MOVR sanction gate 0→2 fires, recross reentry lag 1.0 bar (was +5.6%/+16% chase), strict confirmation → 0 fires, MIN_HOLD killed ~28 `bars_held=1` fires; bypass arm unexercised (no choke-bound recross in the 4 C-sets — promotion-gated, stated unverified). Pilots: (1) `1000PEPEUSDC_LONG` 30D **+27.93** (209tr TIM 58.2 DD 7.8, fresh 21.63 → §58 repair +2 steps → 27.93, filename matches) vs old +25.29 (**+2.64pp**), sanction True +1.18 joint + MIN_TFS 3.0; 365D −191 vomit → quarantined (old round also vomited, −211 — PEPE 30D-greedy overfit, not the sanction). (2) `AAPL_LONG` **+11.34** (fresh==chain, no repair) vs old +14.88 (fleet now holds +16.16) — sanction correctly rejected (0.0, stocks stubs-only); **365D +17.77 PASS** (1779tr) → fully qualified; old set revals +14.72 under the new engine, so the gap is greedy-path, not engine capability. (3) `MOVRUSDT_LONG` chain **+78.27** (230tr, fresh==chain) vs old +58.27 (**+20pp**, sanction rejected −6.63, HLR retired) → quarantined on `gain<BH 268.19` (2026-10-02 finish rule; old file predates the rule). (4) `1INCHUSDT_LONG` rows +12.52 → ADAPT +13.62 max (204tr) vs old +17.34 → quarantined on `gain<BH 15.76`; sanction True +0.43. Bypass rejected 0.0 on all 4 (honest zeros). Collateral: MOVR quarantine unlinked S1's prior MOVR artifacts (`_quarantine_impossible` uses bare `unlink`, no backup — Mac retains all htmls + gain55p84 xlsx; S1-only gain35p46/gain58p27 workbooks lost, charts + C-sets survive); 1INCH/AAPL live files safety-backed to `~/v15_selltop_eng/safety/` on S1. Lesson recorded: never run an ISO pilot on a sym whose BH it cannot beat — quarantine deletes shared-dir evidence; and quarantine should carry (not unlink) prior `bh*` files.

**NO-LIES rider (same autopsy):** MOVR filename gain (`58.27`) ≠ metrics-sheet gain (`51.86`) ≠ chart-header gain (`48.28`). Three numbers for one set is a headline violation: the finisher must assert `|filename_gain − final_set_reval_gain| < 0.5pp` at write time, else suffix the file `[UNVERIFIED]` and flag it for `v15_assure` instead of publishing the filename number.

## 64. CAT_DEFAULTS-BASELINE LAW (USER 2026-10-03, AUTHORITATIVE — comparability)

**Rule:** every pilot run baselines against the **latest cat_side defaults** (template bold/is_default=YES rows = `_tpl_defaults`, gated against venue truth + `cat_side_promotions.json`). Starting from a previous test — live recipes, `*_best.json` / `hustler_best.json`, previous progress `cumulative_overrides`, previous-xls C-fill, or an ADAPT-BASE that picks `previous_best` — is **completely forbidden until all 354×2 sym_sides have produced final sheets**. Rationale: chaining from a 5-minute-old test bakes that test's greedy path into every number and makes cross-sym sheets incomparable; the first full-coverage round must be defaults-pure.

**Enforcement (mechanism, verified in `v15_pilot.py`):** `V15_TEMPLATE_DEFAULTS=1` drops all ingested overrides → `overrides = dict(_tpl_defaults)` (:4658-4662); `V15_ADAPT_BASELINE=0` disables the ADAPT-BASE max-pick (default 1, and forced on under `V15_FRESH_RUN=1` at :4908); omit `V15_INGEST_BEST` (without it, FRESH_RUN falls back to recipe-only at :4630 — still previous-test-derived, so TEMPLATE_DEFAULTS=1 must accompany it). `V15_FRESH_RUN=1` + isolated `V15_PROGRESS_DIR` + isolated `--out` still required for ISO rounds. `CAT_SIDE_DEFAULTS_PATH` is consumed nowhere in the pilot/tools tree (dead env var — do not rely on it). Consequence, accepted: a defaults baseline that lands sub-floor yields a `[DIAGNOSTIC ONLY]` sheet with the sweep skipped — honest, per the rule — instead of a rescued number. Standing violation to clear: the run25 fleet launches with `V15_INGEST_BEST=1`; its rounds are previous-test-chained until the herd switches to this mode. **CLEARED 2026-10-06/07: current launcher env is `V15_TEMPLATE_DEFAULTS=1` + `V15_FRESH_RUN=1` + `V15_UNWIRED_SKIP=0` with NO `V15_INGEST_BEST` (verified on live fleet cmdlines) — rounds are defaults-pure.** Second enforcement (2026-10-03, `v15_pilot.py:2979` → `:3072` after the parallel manifest update, which preserved the gate; fleet md5 `402312f95ec7b674fac8c1e7f8a89079`): compliance-repair round 2 used to inject `prior_0/1` fleet sets via `_prior_final_sets()` — under `V15_TEMPLATE_DEFAULTS=1` priors are now forced to `[]`, so every compliance round stays defaults-pure. Third enforcement — the bold-type law: a template bold that cannot pass `_coerce_override` under its field type deterministically voids EVERY defaults-pure set (observed: bold `'0.5'` for bool `WT_EXHAUST_EXIT_REQUIRE_GAIN` rejected all PEPE/1INCH compliance rounds 0-1, forcing the §64-violating round-2 priors that produced the VOID first-launch ADAPT numbers +27.65/+12.41). Fix (2026-10-03): 6 phantom promotions voided from `data/cat_side_promotions.json` + SQL KV on Mac/S1 (S2/S5 have no SQL store, JSON fallback covers them) — `WT_EXHAUST_EXIT_REQUIRE_GAIN`/`WT_4H_VEL_EXIT_REQUIRE_PROFIT` (CRYPTO_LONG), `LEGACY_PROC_SINGLE_REENTRY`/`LEGACY_REENTRY_PSR_DC_BOUNCE` (STOCKS_LONG), `LOSS_TECHNICAL_EXIT_NO_STALE_BLOCK` (STOCKS_SHORT), `DC_BREAKOUT_SCORE` (CRYPTO_SHORT) — and template bolds moved to config-parity (`WT_EXHAUST`→`False` ×4, `DC_BREAKOUT_SCORE`→`15`, `LH_HL_FILTER_REQUIRE_BOTH`→native bool). Exact-loader audit (`template_bold_defaults` + coerce per key) now reports 0 poison keys and 0 loader violations on all 4 cat_sides. Note: the Oct-02 avg_delta batch that wrote the phantoms measured deltas for values the strict engine cannot evaluate — any future promoter must coerce-check values before recording them. Fourth enforcement — the ablation exemption (2026-10-03, entry-death outage): `ABLATION_DISABLE_*=True` in crypto template bolds deterministically kills ALL entries (fleet-wide 0-1 trades on every crypto sym_side, both directions; proven: cat_side set with 13 True → 1 trade, same set with all False → 203 trades PEPE / 186 1INCH, valid). Root cause chain: `config.py` ablation defaults flipped True on 2026-09-21 (live forward-proving regime) → template bolds followed to config-parity → every defaults-pure baseline died; prior-based runs survived only via per_sym overrides that lack ablation keys... until the Oct-02 builder run (`tools/build_cat_side_defaults_4.py`, 18:33Z) also wrote True into `cat_side_defaults_4.json`. Fix: bold+`is_default=YES` moved True→False for all 13 ablation switches in `TEMPLATE_CRYPTO_LONG/SHORT` + `TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_*` (102 bold flips + 102 YES moves, zero row add/delete; STOCKS untouched — working), JSON purged to match (md5 `d29bd72d`), deployed S1/S2. LAW: ablation switches are EXEMPT from config-parity — the live OFF-proving state must never leak into backtest defaults; any builder/promoter emitting `ABLATION_DISABLE_*=True` into a crypto default set is a P0 violation. Fifth enforcement — the per_sym-baseline skip: the Oct-02 "cat_side vs per_sym, best as E3" block (`v15_pilot.py:5077`) starts a sym from its own prior best = running against itself. Defaults-pure rounds must export `V15_SKIP_CAT_PERSYM_BASELINE=1` (baseline = template bolds only, no comparison). Verified 2026-10-03: PEPE 226 trades / 1INCH 222 trades, valid, zero priors. Sixth enforcement — the self-improvement loop (2026-10-03): defaults improve ONLY via rebuild→daily-update→builder, unfrozen this date (`TEMPLATES_FROZEN` lifted to backups/, user-ordered). Promoter gates now: coerce-check per field type (phantom class refused), ablation-True P0-refused, `--min-n 3` evidence floor, every refusal ledgered. Builder P0: crypto `ABLATION_DISABLE_*` forced False post-fill (template False bolds are dropped as "untrusted vs config truth" and refilled True = entry death; observed 9 re-emitted, forced back). Fleet runs `TEMPLATE_FINAL_NORM` (the ONLY templates since 2026-10-07 — the legacy `SPREADSHEETS/TEMPLATE_*.xlsx` set is archived; the updater runs on FINAL_NORM only). Zero-delta watchdog (`tools/v15_zero_delta_watchdog.py`): avg==0+median==0 over ≥8 syms on a non-default value = dead suspect → `--apply` appends to the pilot's skip list (KG never added); first run: 1 suspect row (`WT_15M_BOUNCE_REL_VOL_GT_1=True`), 0 disablable. Daily cron `V15_SELFIMPROVE` 05:00 UTC (`tools/v15_daily_selfimprove.sh`, dated-agg explicit — `*latest*` names are clobbered by S1→Mac pulls). Full-compute law: `V15_POSSYM_SAMPLING=0` fleet-wide (both launchers) — validity requires every row calculated. **SUSPENDED by later user order 2026-10-06 ("sampling ON again" — fleet now launches `V15_POSSYM_SAMPLING=1`; see §73). Sampling defers low-evidence rows/cells to a sample-free heal pass rather than deleting them, which is why the suspension is validity-safe.** Seventh enforcement — the complete-rounds law (USER 2026-10-03, round `run27_complete_20261003`): (1) CORPSE-CLEAR — rows stuck `is_running` are dead-run orphans (QBTS_LONG: 835 permanent blanks reading as done); the pilot drops uncalculated ones at startup (post-DEDUP, safe) and re-drives them. (2) UNWIRED RECALC — `V15_UNWIRED_SKIP=0` fleet-wide: audit keys calculate anyway, honest 0.0 rows tagged `UNWIRED_CALCULATED` (never blank, never fake). No switch is ignored until several complete rounds exist. (3) HOLLOW BOARDS REOPEN — `symside_status` no longer counts corpses, undecided blanks, `NOT_WIRED_VEC` or `SKIPPED_SAMPLING` rows as filled (QBTS_LONG 3084/3088 → 2280/3088); re-driving hollow boards is NOT a §65 retest (they never qualified). (4) ACTIVE-ONLY SCOPE — this round runs actively-traded symbols only (crypto `symbols_active.json` + TRB long/short; default ON, `V15_ACTIVE_ONLY=0` restores full queue; explicit `--order` bypasses): S1 150→100, s2 204→111. (5) CLAIM-ADOPT + DONE-CLEAR — a restarted herd adopts same-host dead-PID claims immediately instead of idling 90m on its own corpse (registry lives on S1; workers adopt via `_s1_run`; other hosts' claims untouched); `_only_complete` clears claim done-markers for reopened boards (a `done` file otherwise blocks the redrive forever — 29 stale markers found). (6) NO-SHADOW LAW — `import glob, json` inside `main()` made `json` function-local → UnboundLocalError swallowed by inner excepts (silent unfiltered order) / fatal outside try (herd death, S1 idle); fixed via `_jsu`/`_jsact` aliases; regression `test_herd_noshadow.py` (AST: main() binds no module-global name) fails pre-fix, passes post-fix. (7) TEMPLATE HYGIENE — 20 untestable rows deleted from all 8 templates (phantom switches `UNIVERSAL_NOLOSS_BYPASS_REASONS`/`CIRCUIT_BREAKER_THRESHOLD_PCT` in no config + unwired; `BB_BREAKOUT_ENTRY_TF=20` int-for-str) + `DC_BREAKOUT_SCORE` 0.75→0 (label==tested value); parser coerces bool-words/native-bools → 1.0/0.0 for float defaults (config_tradier 0.0/1.0 bool encoding: `HTF_GATE_D_MANDATORY`, `LH_HL_FILTER_REQUIRE_BOTH`). Fleet topology corrected: `s2-int` tunnels to S1 (same bytes); real workers are S1 `niels` + s2-box `10.0.0.4` (hostname s2, stocks) + s5 `10.0.0.5` (hostname s5) — all three herds restarted uniform this date.

## 65. NO-RETEST LAW (USER 2026-10-03, AUTHORITATIVE — compute-once)

**Rule:** a sym_side with a **qualifying final** is NEVER relaunched until **every** sym_side in the order has one **and** its NPZ was refreshed after the final. Qualifying = published `{SYM}_bh*_gain*_30d_matrix.xlsx` in `V15_V16_CELL_BY_CELL` with `gain>0` (parsed from the filename: `p`=decimal point, `m`=negative), **and** not a loser (`final_gain<0` or 365D `<0` in progress). USER 2026-10-03 pos-gain go-live: BH is recorded (filename + manifest `gain_vs_bh`) but NEVER gating — a moonshot BH month must not quarantine a sound positive set; the 365D verify (valid + gain>0 + ≥80 trades) remains the quality gate. Note: the 30D finish docstring historically claimed a DD≤30 check the code never implemented — no DD gate exists; do not assume one. Rationale: re-running a qualified sym against itself burns fleet hours for a second opinion nobody asked for; coverage first, refinement only on fresh data.

**Why the hole existed:** three retest paths ignored prior finals — (1) template-mtime staleness (`xlsx older than TEMPLATE_*` → re-run, so every template deploy retested the universe), (2) BEST-order freshness (BEST validation ignores global done and re-runs on latest template), (3) the `is_complete` requalify (a final whose progress JSON is missing locally reads as not-done). The pilot's DEDUP lock and the claim registry only stop *concurrent* doubles, never sequential reruns.

**Enforcement — HERD MECHANISM RETIRED 2026-10-06 with the herd; scheduler-era enforcement is PARTIAL (gap flagged 2026-10-07):** the retired mechanism (`tools/v15_local_herd.py` `_protected_qualified(order)`, NPZ-refresh release valve, ssh union of S1 finals) no longer runs. What the scheduler enforces today: `done30` tracking per cat_side from published finals — finished pairs are not relaunched. NOT yet ported: the NPZ-refresh release valve (fresh data does not currently release a qualified sym) and the ssh finals union (each host counts its own finals). The hollow-board exception below and the pilot-side midrun-swap refusal still hold (pilot code, launcher-independent).

**Hollow-board exception (USER 2026-10-03 complete-rounds, enforced in `_qualified_final_set`):** a qualifying final protects ONLY a complete board. A qual whose local progress/final exists on this host but `is_complete()` is False (hollow: corpses/blanks/NOT_WIRED/SKIPPED rows unfilled) is released to `todo` with a `[HOLLOW-REOPEN]` line — redrive is not retest (the board never finished). Remote-only quals (no local progress/final — finished elsewhere) stay protected (compute-once). Zero-trades boards report `is_complete` True (terminal, never redriven); unfillable redrives park via the herd STALLED guard. Fleet proof 2026-10-04: S1 64→1 protected/todo 51, s2 99→1/todo 110, s5 69→2/todo 46; claim slate 317→0 (zero done-markers existed); S1/s5 crypto queues split disjoint 76/74 by base (twins together, was identical 150-line files); herd md5 `405800a7`.

**Midrun-swap refusal (USER 2026-10-04 ALGO stale-751, enforced in the `v15_pilot.py` FINAL gate):** the run-start `npz_id` stamp (RULE#4) is re-compared at FINAL publish; a changed NPZ (regen swap under a measuring pilot) REFUSES the final — board archived (`.npzprev`), `needs_redo` scheduled, herd relaunch refills every row on the new generation — so no final ever mixes generations. Unchanged NPZ stamps `npz_id_final` as proof. Guard-exception fail-opens with an honest `npz_final_check` flag. Regression `test_npz_midrun_*` in `test_v15_row_guards.py` (2 passed; pre-existing `test_scan_board_tally` failure untouched — tally drift in another lane). Deployed 2026-10-04 (pilot md5 `077fc1bb`, all 6 targets verified); running pilots finish undisturbed, new launches carry the gate.

## 66. MONDAY-READINESS FIXES 2026-10-04 (resume-claude session — ESTABLISHED FACTS)

**66.1 Disk guard protected Monday evidence.** s1 raw disk hit 89% (~3 GB headroom, growth ~3.5 GB/day) with the old guard deleting every run dir except the 2 newest — it would have wiped run18–24 (the Monday `host_scan` sources) ~20 h before T0. `tools/v15_autopilot.py:disk_guard` (fleet md5 `780acd2a`) now NEVER deletes run dirs with N ≥ 18, the current progress dir, or `v15_final_*` dirs. Separately, the provably-unread 12 GB `SPREADSHEETS/archive_20250928_0730` (no code references on Mac or s1; 5160/5161 files untouched since Sept 28) was checksummed to s2+s5 (5162 files each incl. one pre-existing ACEUSDT file s1 lacked) and removed on s1 → raw 85.6% (~13 GB headroom, ~3.7 days). Runbook §4 disk row updated to match (never delete run18+).

**66.2 CURRENT.json resynced to e467 truth (manifest-only, no file deploys).** The Oct-04 parity_go engine cut (v12 `e467eb7b` on all 4 machines) never updated the manifest (still `98bd3e9e`, Oct 3; s2/s5 had no file). Manifest now `e467eb7b`, identical on Mac+s1+s2+s5 (md5 `71476c96`), audit record `data/engine_deploy/20261004-manifest-resync.json`. The Monday `golive_final` engine gate (s1-manifest == Mac-manifest) stays green. Known divergences left for post-Monday `engine_deploy`: `vec_unwired.json` Mac≠fleet, `data/cat_side_defaults_4.json` Mac≠fleet (s5 converged to s1 this session, §66.3), `config_tradier.py` s2/s5≠Mac/s1 (pre-LH/LL; inert-equivalent — fleet templates lack the LH/LL rows so no set references the missing knobs).

**66.3 s5 backtest baselines converged to s1 fleet truth.** s5's sandbox `data/cat_side_defaults_4.json` was pre-Oct-02 (no HLR knobs, `MIN_GAIN_TO_BUY` 3.0 vs 4.5, `WT_EXHAUST_REQUIRE_GAIN` True vs False, ~19 stale strategy values) — the Monday 365D chain reads the LIVE path on each host, so s5 verdicts would have used a stale unset-key baseline. Overwritten with s1's file (md5 `750274e0` = Mac minus the 7 inert LH/LL knobs), JSON-validated + atomic-mv; original preserved at `backups/before_csd_s1truth_20261004.json` (s5) and `backups/before_csd_s5_orig_202610040229.json` (Mac). s5 runs no live trading — backtest input only. s2 already equalled s1. s5's stale Oct-02 SWEEP file (`ea38a414`) deliberately untouched mid-round (post-Monday round-end `stage_sync` converges it).

**66.4 `host_scan` includes herd-era boards.** Herds (revived Oct-03, all 3 hosts) write to `data/reports/lifecycle_pilot/`, which the Oct-02 `host_scan` never read. `tools/v15_final_phase.py:host_scan` (fleet md5 `8859be0d`; same file also carries the §66.7 parity-timeout fix) now adds Oct-03+ lifecycle boards at the newest round number (older boards stay ranked by run dir only, so stale Sept boards can't outrank Oct run dirs). Verified live: s1 scan 253 syms, 2 lifecycle-sourced (BTCDOMUSDT_LONG, CRWD_LONG).

**66.5 T0 stops herds, TEND restarts them.** Oct-02 design assumed herds dead; live herds would relaunch 30D pilots into the old dir at T0 and starve the 365D chain (herd pilots run un-niced vs chain nice-10). `tools/v15_final_orch.py` (fleet md5 `85cabd8c`): `assemble` SIGTERMs exact herd PIDs first (SIGKILL after 8 s only after cmdline re-verify; ssh failure logged, not fatal) and saves exact cmdline+env+cwd+stdout per host in `fin["herd_cmd"]`; the TEND branch replays them via setsid+nohup (skips hosts already alive; no-spec hosts log a manual-restart line and continue on the scheduler alone). Dry runs skip both (verified: alive-branch lists only real herd PIDs — the `pgrep -f` pattern needed `[.]` brackets because the ssh wrapper embeds the pattern in its own cmdline and self-matched). No herd supervisor is currently active (Oct-03 note retired) — stopped herds stay stopped.

**66.6 Monday dry rehearsal GREEN (2026-10-04 ~02:40Z, zero side effects).** Real `assemble(dry)` + `drive(dry)` + `export` on s1: **275/286 sym_sides (96.2%)** have a finished 30D sheet, far above the 30% assemble bar; by-round run24:256/run23:8/run20:6/run21:3/run26:2; by-host s2:97/s5:105/s1:73; export records engine `e467eb7b`. Missing 11 (no finished sheet anywhere): HII_LONG, ZCSH_LONG/SHORT, COMP/JASMY/STX/QNTUSDT_LONG/SHORT. 365D verify cost measured: ~1–3 s/sym (AAPL/BTCDOM); repair sheet re-runs are the long pole — the 12:30 freeze degrades gracefully (unrepaired syms stay blocked, never promoted).

**66.7 Parity rehearsal + timeout fix + no herd supervisor.** Spot `host-parity` (CRWD_LONG, 661-key full set) under the old 420 s timeout reached 84% of the scalar 30D leg (349 s sim + ~60 s warmup) and was killed → UNAVAILABLE (recorded, correctly non-blocking; `host-collect` RUNNING→UNAVAILABLE parse verified). Timeout raised 420→900 s in `host_parity` (same deploy as §66.4). `confirm_365d` uses the vector engine (seconds) — its 900 s is ample. Supervisor hunt (Oct-03 "unknown supervisor" note): no cron/loop/systemd herd-reviver on any host (s1 `sleep 3600` = CUPS noise; s5's 02:45 herd restart was a concurrent peer) — the T0 SIGTERM holds; no herd fence needed.

**66.8 Shadow-stray decontamination + parity import fix (2026-10-04 ~03:00Z).** The s1 parity vec leg returned `None` ("no NPZ") while the identical direct call returned full metrics. Root cause: s1 `tools/` held 5 unreferenced strays placed Oct-04 01:12Z — `v12_quick_engine.py` (Oct-3 engine `98bd3e9e`), `config.py`/`config_tradier.py` (Oct-2 selltop), `ez_manage.py`/`tradier_manage.py` (Oct-2 bt-family) — plus Sept-20 `config.py`/`config_tradier.py` strays on s2/s5. `v15_parity_check.py` was the only tools-first importer, so on s1 it imported the stale engine (whose `BASE_PATH` resolves under `tools/`, finds no NPZs) and the stale managers/configs inside the scalar leg; s2/s5 scalar legs ran Sept-20 configs. Fix: (1) all strays moved to `backups/stray_tools_shadow_20261004/` per host (reversible; zero code references anywhere); (2) parity import order swapped to root-first (fleet `v15_parity_check.py` md5 `4616da3e`) — root modules now always win, future strays harmless. Verified: tools-first import resolves root files; CRWD re-run vec leg must read ≈+17.44. Mac-only note: root vs `tools/cat_side_defaults.py` are different modules (Sept-30 accessor vs Sept-28 fossil) — root-first keeps parity on the accessor. Fleet freeze requested of all 6 peer sessions until Mon 13:30Z (teal-ophiuchus parity-cut ACKed; their e467 cut is the rehearsed state).

**66.9 PAR/001 arc + strict qualify + T0 pin (2026-10-04 04:00–08:30Z, auditor amber-blazer).** Parity verdicts: CRWD_LONG FAIL (vec +10.1267/83cl vs live −3.7781/29cl, ov 661) + SMCI_LONG FAIL (+11.13/72 vs −6.79/21, ov 628) + ALGOUSDT_LONG FAIL (vec 0/0 invalid vs scalar 1589/−40.29, ov 751) — systemic shape; gate triple-fail short-circuit (validity→ratio→gain) proven correct. Vec counts CLOSES (83 OPEN + 83 CLOSE ledger pairs; trades_rows=166 is rows) — same units as scalar; the 0.80–1.25 ratio gate is valid. Mechanism (forensics): exit-timing PRIMARY (vec 22 DAYTRADE_TARGET vs scalar 0 — scalar bleeds via GAP_RISK 8 + PEAK_GIVEBACK 10 intrabar stops), entry SECONDARY (vec 56 HARDCODED_RALLY_REENTRY; scalar 12 open-bars rarely flat), holds vec-med-135min vs scalar-swing; debounce ELIMINATED (flagged rerun bit-identical); overrides enable daytrade on both paths (no routing gap) → evaluation-granularity race; costs secondary (vec fees ignored, `tools/opt/evaluate_v12.py:553`). Debounce flag DEAD 2/2 (stocks bit-identical + crypto scalar bit-identical −40.2907/1589) → dropped from launcher plans; crypto churn is set-expressed (stale loose sets), fixed by fresh sets. Prune-repair (`tools/v15_parity_repair.py`) FUTILE pre-cut#4: EXIT/WT_DC/REGIME removals move vec (12.54/11.15/10.93) but scalar frozen 29/29/29 → structural read-gap (engines read different keys); parked until cut#4 re-test (code proven-correct, 5/5 pytest, fleet v2 `779de134` prepared-batch). Corollary: the 661-key CRWD set is ~96% noise in vec (post-Monday set hygiene). STRICT qualify LIVE (user-ordered immediate fix): QUALIFIED requires parity PASS; FAIL→NEGATIVE; UNAVAILABLE/RUNNING/missing→UNVERIFIED (phase `00fd78b7`→`c7f1e717`, orch `8229c2df`→`3ffe1622`, 18/18 + 28/28 pytest incl ported probe cases); the UNAVAILABLE-"not a block" leniency is retired. T0 engine pin (no-freeze replacement for the calendar freeze): host-collect reports CURRENT; assemble pins on unanimous fleet engine (refuses split, retries next tick; down hosts excluded from assignment); drive excludes drifted hosts' verdicts/parity/confirmed (`fin["drift"]` + progress_report); export stamps pin engine + pin block (fallback live CURRENT when unpinned). Straddle-filtering simplified away (re-sweep obsoletes old results; Monday reads post-resweep only). Composite engine_md5 (`tools/engine_pin.py`, 2/2 tests: md5 over sorted path=md5 of v12 + `vec_decisions/*.py` excl test_* + 2 scorers) cuts over at Monday T0. Cuts (coordinator-verified + independently audited): cut#3 v12 `4a3ba49e` (CURRENT `66f173ec`) → B1 `6d467f87` (apple parity-b1-unswept, CURRENT `f3d17a84`) → B23 `9a4fa00b` (parity-b23-sync, CURRENT `6584b6a6`, unannounced — caught by health peek). Modules uniform (set-hash `e16f2d22` x3; watchdog `de736132`, wtx `13acac45`). Mac-ahead normal under no-freeze (v12 b0ae W2 work, configs `981ed64d`/`bca06331` w2-haiku +21/+23 — pin stays fleet-based; coordinator safety backups taken). SHORT veto DISSOLVED: naked-0 normal for some syms (NKE precedent; CIEN-132 independent match); C-set repro 0/0/1/0 + baselines 0/0 = stale-bests across cuts (laneF+WT moved baselines), not engine veto; cat_side (`d0660abc` x4) + ablation (8/8 zeros) exonerated. SHORT yield = re-sweep dependent → run27 rollover post-cut#4. Freeze LIFTED (user direct ~06:40Z): the §66.8 freeze request is void; cut discipline stays. Per-sym gate stays OFF until Monday (BEST-filename fallback reads August pre-parity sets; ruling: complementary, not subsumed). Venue timeouts: crypto 3600 (ALGO 63%@1458s pace ~2400s full) / stocks 1500 (SMCI 83%@884s); helper `parity_timeout_for` + 3/3 tests (caught raw-symside suffix bug pre-deploy). Result stamps LIVE (budding pilot `9a87489d` x4): START-capture + DONE writes memory values + start/done_utc; verified NKE_LONG keys venue-correct. Standing: awaiting teal cut#4 (~12:30Z ±1h, 3/9 twins green) → re-verify (`tools/parity_pack.py` runner ready) → fleet re-sweep (fresh sets every sym_side — old results decay under new bytes) → pre-verify → Monday ready. RULE: tag every verdict with engine era (ec61/4a/6d/9a/cut#4).

## 67. OBLIGATED SWITCH-VALUE SYMMETRY (USER 2026-10-06, AUTHORITATIVE — no surface exists in disparity)

**Rule:** no switch value may ever differ between `TEMPLATE_*` bold, `config.py` / `config_tradier.py`, `QuickConfig` (+`apply_tradier_defaults`), per-sym JSON and SQLite — for all four cat_sides. This is **obligated, not manual**: three automatic mechanisms, zero cron-job faith required.

**1. Workbook-end registrar (per-sym values).** `switch_parity.register_workbook_result()`, called by `v15_pilot._spec_fill_workbook` at DONE (`done` + `best-effort` returns): every workbook end with ≥1 VALID promoted positive fresh-evaluates the final set and, if it qualifies (valid, TIM 20-80, ≥10 trades, gain>0 — mirrors `_qualifies_30d`), atomically writes SQLite primary + per-sym JSON book + trb overlay (stocks) + `data/parity_promotions.jsonl` ledger, then post-verifies by re-read. Refuses ALL on: zero promotions, unqualified final set, secret keys, any type-coerce failure, empty cat_side snapshot. Existing `_NEG_BLOCK` is preserved unless 365D evidence lifts it (values register everywhere immediately; live-trading enablement keeps its 365D/parity gates). `SWITCH_PARITY_REGISTER=0` disables (proof runs must set it — proof protocol §23 updated).

**2. Promotion hook (default values).** `tools/v15_daily_template_update.py --apply` (the avg/promotions writer — one of the 4 allow-listed template writers in §70, not the sole writer) auto-syncs the just-promoted keys' venue globals + QuickConfig in the same run via `switch_parity.sync_default_surfaces(keys=...)` (targeted, guarded, backup+compile per file, failures loud in the report, never fatal to the update).

**3. Pilot startup gate.** `switch_parity.startup_gate()` runs at every pilot start after `[DEFAULTS-GATE]`: hard disparity (`bold-vs-global`, `bold-vs-quick`) REFUSES the run; template-lane backlog (`bold-vs-cat`, `missing_from_cat`, `template-default-violation`) warns loudly. Cached in `/tmp` by template+config md5 (≈0.1 s warm). `SWITCH_PARITY_GATE=off` escapes (loud). Cron form: `switch_parity.py gate --cat-side X` (exit 1 on hard disparity).

**Value-truth semantics (why globals are not overwritten per-sym):** a single global cannot equal two side bolds. Per-side truth lives in `cat_side_defaults_4` (`side-split-cat-truth`, informational — effective parity holds via the cat layer for all scoped reads). Globals/QuickConfig carry the template bold only when uniform across the venue (cross-venue raw guarded: a raw edit that would break another cat_side is refused as `needs_manual`). `fallback-split` (3+ distinct bolds, e.g. `REGIME_TRENDING_WT_REDUCE_FRAC_LOW` 0.2/0.15/0.1) is architecturally unrepresentable in raw+overlay — cat-side truth wins, documented. Exclusions: §17.3/§31 set + `MAX_AUGMENTS_PER_POSITION` (USER no-cap mandate) + ablation exemption (§64).

**Initial close (2026-10-06, user-unlocked):** hard 63→0 on MAIN templates (11 tool edits + 7 manual: 3 duplicate-field pairs in QuickConfig fixed on both lines, `REGIME_RANGING` raw + 5 `apply_tradier_defaults` appends). Backups `backups/before_parity_sync_20261006_*`. Behavior-neutral for scoped reads (cat_side already carried every synced key). Cat fill (2026-10-06, same mandate): `switch_parity.py sync-cat --apply` filled 36 keys (12 stale incl. 8 ledger-evidenced staged promotions + 4 newly-trusted after the global sync, 24 missing — all verified bold==global==quick before fill) into `data/cat_side_defaults_4.json` + kv dual-write, backup `backups/before_parity_catfill_20261006_042816.json`. The 4 fossil pairs (`MOM3_FILTER_TF`/`VIGILANCE_GUARD_ENABLED`/`WT_CROSSUNDER_FINAL_ENABLED` on SHORT sides) are HELD at builder P0-FOSSIL live-truth values — their template bolds are stale and only the template lane may move them. Residual backlog: 4 fossil `bold-vs-cat`, 8 structural template violations (block `build_cat_side_defaults_4.py` on Mac MAIN stocks templates). Fleet deploy = engine cut: rsync `switch_parity.py`, `v15_pilot.py`, `tools/v15_daily_template_update.py`, `config.py`, `config_tradier.py`, `v12_quick_engine.py` to S1/S2/S5 × sandbox+live, md5-verify, `import v12_quick_engine` per host; S1 baseline sanity (`GDX_LONG`/`AXTI_LONG`/`AXTI_SHORT` trades not collapsed) before herds relaunch. Tests: `tests/test_switch_parity.py` (16).

---

## 68. PARITY DEFINITION + DAILY PRE-MARKET CHAIN (USER 2026-10-06, AUTHORITATIVE — supersedes every earlier "parity" wording)

### 68.1 What parity means
- **Vectorized == live by definition.** That is what the backtest system was built for. Phase 1 (done): the vectorized
  engine (`v12_quick_engine` + `vec_decisions/`) was set up to reflect live. Phase 2 (now): the vectorized engine produces
  better results than live — partly through experimental functions that so far existed only in vector — so **live is
  adjusted to vector**. The vectorized function is the **source of truth** that drives the trades on the zoomable
  charts; the charts we choose to make live must trade live exactly as charted.
- **Parity = the daily chain (§68.3) works perfectly AND every live function is identical to its vectorized function**
  (same code or a proven twin, same switch, same effective value, same data). Same trade, same moment.
- A vectorized function that has proven itself (positive in the sheets / pos_sym) and does not exist live is
  **implemented live** (`ez_manage.py` / `ez_positions_quick.py` / `tradier_manage.py` + `config.py` / `config_tradier.py`)
  behind the SAME switch with the SAME default — it is never "vec-only".
- A live path with no vectorized function is either given a vectorized twin, or switched OFF by its OWN switch on both
  sides (same value in TEMPLATE / config / QuickConfig). Never by a gate.

### 68.2 Forbidden (agents keep doing these — stop)
1. **"Vec-only trading", `*_VEC_ONLY_*` switches/rows, or any construct that lets a function trade in one surface only.**
2. **Parity by gates**: blocking native live orders so that only vec-decided orders fill (e.g. `VEC_DRIVEN_NATIVE_ENTRY_BLOCK_ALL`,
   execute_now suppression lists, the VEC_DRIVEN intent bridge). These are emergency/transition tools only, never parity, and
   are removed as soon as the functions are identical.
3. **Flipping vectorized defaults back to old live values** (vector is the truth; live follows), and **ablation flags True
   outside a single investigative test** (§64).
4. **Writing template defaults outside the daily chain**: no pilot start, verifier, healer or agent rewrites `TEMPLATE_*`
   bold / `is_default` (the in-pilot `TEMPLATE-VERIFY` rewrite was removed 2026-10-06). The ONE writer is the daily update.
5. **Editing defaults/per-sym settings by hand** in config / SQLite / per-sym JSON outside the chain, or pushing older
   files over newer ones (templates and code go Mac→servers; results come servers→S1/Mac).
6. **Indicator data that differs from live**: the NPZ builder (`backtest_v8_precompute.py`) must compute every field with
   live's functions and parameters on live's frames, with no look-ahead (crypto WaveTrend on crypto params; stocks on RTH
   frames). Guard tests: `tests/test_parity_crypto_builder_vs_live.py`, `tests/test_parity_stock_builder_vs_live.py`.

### 68.3 The daily pre-market chain (EVERY day, before market open — the only path by which settings change)
1. **Recalculate all deltas** from the latest round: `v15_avg_delta` / `tools/v15_vector_delta.py` → per cat_side
   (CRYPTO_LONG/SHORT, STOCKS_LONG/SHORT) `average_delta` and `pos_sym` for every switch row and filter.
2. **Define the defaults in the templates**: the single template writer (`tools/v15_daily_template_update.py --apply`)
   sets `TEMPLATE_*` bold + `is_default`, and in the same run syncs the venue globals (`config.py`, `config_tradier.py`),
   `QuickConfig` and `data/cat_side_defaults_4.json` (`switch_parity.sync_default_surfaces`, §67).
3. **Apply the new per-sym settings to live** — per sym_side best sets that pass the gates (30D valid + positive, 365D
   confirmed, `switch_parity.register_workbook_result`) are written to ALL live surfaces together: the config files, the
   SQLite store (`per_sym_store`) and the per-sym JSON book (+ trb overlay for stocks), post-verified by re-read.
3b. **Refresh the inf account universe** (USER 2026-10-06, top 20 per USER 2026-10-08): `symbols_inf_long.json` /
   `symbols_inf_short.json` are rewritten with the **20 best LONG and 20 best SHORT crypto sym_sides of that day's backtest**
   (ranked on the fresh 30D result of the final per-sym set; only sym_sides that pass the same gates as step 3: valid,
   gain > 0, TIM 20-80, DD ≤ 30, ≥10 trades, 365D confirmed when available; only tradeable symbols; never a
   SIMPLE_PRICE_GT0 set). Symbols with an OPEN inf position stay listed until that position is closed (no stranding).
   A symbol eligible on BOTH sides is listed only on its better-gain side (a both-sides listing is silently dropped by
   the hedge-pair cleanup, so it would trade neither). Backup + atomic write; `tradeable_keys.json` `inf:` keys follow
   within minutes via the positions service cleanup, and live `ez_manage --account inf` picks both up with no restart
   (symbols reload every 3 min, tradeable every 180 s). Tool: `tools/v15_daily_inf_universe.py --apply --top 20`, run as
   step 5d of the Mac chain follow-up (`tools/v15_daily_chain_mac_apply.sh`, right after the per-sym go-live + size
   tiers, non-fatal, result in the DONE stamp) with the 15:05Z `V15_INF_UNIVERSE_APPLY` cron as backstop. Fetch unions
   the pointer dir + every `~/v15_*` campaign dir + the lifecycle_pilot dir (pointers rotate fleet-wide; the day boundary
   is the 36h evaluated filter, not the directory); `--apply` refuses on a thin fleet view (< `--min-records` 20 sym_sides);
   dedupe prefers a complete evaluation over a newer partial copy. Writer ownership: the chain OWNS `symbols_inf_*.json` —
   `ez_rankings.py` `INF_BEST_SAVE` is SUSPENDED since 2026-10-08 (USER order: short-term gainers had horrific results;
   `INF_BEST_SAVE_SUSPENDED=True`, resume = flip + rankings restart; verified live: SUSPENDED marker, zero writes, pilot
   books stick). The `tradier_rankings.py` `INF_HOOK` is OUT OF SCOPE per user ("tradier has nothing to do with this",
   2026-10-08) — left untouched; it only fires when tradier_rankings runs in market hours, so if stock twins ever appear
   in the books intraday, that hook is the source. News-scanner injections stay additive (TTL 48 h).
4. **Live trades those settings with functions identical to the vectorized ones** (§68.1). Precedence everywhere:
   per-sym > cat_side > global, identical in live and vector.
5. The fleet then sweeps on the new defaults (S1 coordinator; each sym_side baselined on its previous best) and the next
   pre-market run repeats 1-4. Nothing else moves defaults.

### 68.3a Sizing is NOT part of parity (USER 2026-10-06)
- Parity = the same trade DECISION at the same moment (open / augment / reduce / close, side, bar). **Quantities are live's
  job**: `execute_trade_action` / `execute_now` define live sizing (ratio boost/cut, ladders, structure multipliers, …) and
  that amplification is correct behaviour. Vec-decided orders go through the same live sizing as native ones.
- A parallel paper forward test executes paper orders of the exact vectorized size for comparison.
- Never strip, gate or "parity-fix" live sizing multipliers; never use quantity equality in a parity verdict.

### 68.4 How parity is proven
- **Trade-level replay** (`tools/v15_trade_parity.py`, `backtest_v12_engine.run_one`): the REAL live scripts
  (`process_position` → `execute_trade_action` → `execute_now`) on frozen NPZ vs `simulate_one` on the same NPZ and set:
  vec→live and live→vec trip match ≥0.95, exits agree. The harness must import only the code under test (fail closed on
  sandbox imports), apply only the sym_side's own set, inject no producers of its own, and call live loops at live cadence.
- **Data-level**: the builder guard tests above PASS (live indicator functions == NPZ fields on live frames).
- **Switch-level**: `switch_parity.py gate` (all cat_sides) and `switch_parity.py verify-live-switches --strict` report 0 hard.
- Status 2026-10-06: crypto 5/5 and stocks 11/11 sample sym_sides at 1.0 in the isolated replay (live executing the vec
  decision through the real order path); live-equal builder applied (user unlock); large replay (80 crypto + 101 stock
  sym_sides × 30D/90D) running; NPZ fleet rebuild on the live-equal builder in progress → all sheets re-measured after it.

## 69. ENDGAME FILTER CYCLE + SELF-IMPROVEMENT (USER 2026-10-07, AUTHORITATIVE — end of every workbook)
- **Order**: per-row fill (filter DEFAULTS from moment 1, filter ALTERNATIVES orange after their switch) → `_final_filter_recheck`
  → `_endgame_filter_cycle` → `_diagnose_repair` → compliance → DONE. `V15_ENDGAME=0` disables; never raises; herd-budgeted.
- **Cycle**: (1) REPAIR illegal values (int-truncate float counts, drop `ABLATION_DISABLE_*=True` P0) + fresh re-eval.
  (2) STRIP all promoted filters → naked switch-only base + fresh re-eval. (3) NAKED retest every promoted switch
  leave-one-out vs the stripped base: ACTUAL marginal deltas; honest zeros cause-tagged (`NON_BINDING_ZERO`/`OFFSET_ZERO`,
  §18 — never faked, §19). (4) REAPPLY filters one-by-one greedy toward gain > bh+10 (§14.2: ALL positives reapply, even
  past the target — the target is a reported goal, never forced). (5) ADOPT = fresh NO-LIES eval of exactly the adopted
  set; dropped filters `_c_drop`-ped so C == the final set (§56.0). (6) TIER-entry share reported, flag-only, no auto-action.
- **Proof**: every run prints `[ENDGAME-PROOF] lift ±x (entry → final)` with the running aggregate
  (`endgame_knowledge.json → workbooks`: n, mean_lift, P(lift>0), targets, adopted). Within-subject (same NPZ window, zero
  extra compute) + herd A/B split (`V15_ENDGAME_EV_ORDER=0/1`) for the ordering benefit. Proven = mean lift > 0 with
  sign consistency across workbooks — negatives reported honestly, never hidden.
- **Self-improvement**: NAKED/REAPPLY try-order follows cross-workbook EV (mean marginal delta, n≥3, else alphabetical).
  Order only — every candidate is still tried, greedy adoption unchanged (§14.2); under a binding eval budget best-first
  banks the best positives first. Each run appends `endgame_ledger.jsonl` (entry/strip/final, naked deltas, reapply
  sequence with step deltas, drops) and merges `endgame_knowledge.json`. No look-ahead: knowledge loads at cycle entry
  from PRIOR workbooks only; the current run appends at exit. Resume-keyed `(symside|set-hash)`, corrupt files fall back
  to alphabetical, never fatal. Guard tests: `tests/test_v15_endgame.py`.
- **ADOPT GUARD (USER 2026-10-13 — measured: run31/32 endgame ledger sum lift −1046.5pp over 551 boards on 4 hosts, 258
  negative boards ALL adopted; s2/s5/s6 logs since 10-11: ENDGAME-PROOF Σ −1209.1pp (364 negative), REPAIR int-truncation
  Σ −205.4pp, DIAG `status_up` accepts Σ −398.5pp on 84 boards; FOCUS +169.4, FINAL_RECHECK +835.6, COMPLIANCE 0 events,
  all never negative).** The STRIP base is DISCOVERY ONLY — never adopted. The reapplied set replaces the entry set only
  when a fresh eval of exactly that set (`ENDGAME_ADOPT`) is valid, passes the trades floor + TIM band (same escapes as
  `trades_guard_veto`/`tim_guard_veto`) and gain ≥ the fresh legal entry gain (`ENDGAME_ENTRY`, or the post-REPAIR gain
  when illegal values were repaired — REPAIR now also tries the ceil variant of int counts and keeps the better legal set).
  Otherwise the entry set stays unchanged: `[ENDGAME-REJECT] lift x`. Same guard on `_diagnose_repair` adoption
  (`[DIAG-REJECT]`), whose only exception is a status repair of an origin that fails `_qualifies_30d` (365D
  qualification alone never buys 30D gain — live before 365D). `progress["best_seen"]` holds the best fresh-verified
  guard-passing set of the board (same NPZ; FINAL_RECHECK / ENDGAME_ENTRY / ENDGAME_ADOPT / DIAG_BASE / DIAG_ADOPT); the
  BEST-SEEN publish gate (after diagnose, before DONE save) re-evaluates both fresh and publishes best_seen when it is
  strictly better and qualifies (`[BEST-SEEN-FALLBACK]`, C dropped for keys back at default, rest in `c_unplaced`).
  Kill switches: `V15_ENDGAME_ADOPT_GUARD`, `V15_DIAG_ADOPT_GUARD`, `V15_BEST_SEEN_GATE` (all default 1). Pure helpers
  `_endgame_adopt_verdict` / `_endgame_best_seen_note` are unit-tested.
- **FLEET KNOWLEDGE (USER 2026-10-13).** EV order reads `data/endgame/endgame_knowledge_fleet.json` first (override
  `V15_ENDGAME_KNOWLEDGE`; per key the cat_side stats when n≥3, else fleet-global — `_endgame_fleet_view`), falling back to the
  per-campaign `PROGRESS_DIR/endgame_knowledge.json` (still the local write target). Built by
  `tools/v15_endgame_knowledge_merge.py --pull --push` on S1 (chain step 4e + hourly cron `23 * * * *` `# V15_ENDGAME_MERGE`,
  flock `/tmp/v15_endgame_merge.lock`, nice): rsyncs every worker's `~/v15_run*/progress/endgame_ledger.jsonl` to
  `data/endgame/ledgers/<host>/`, dedupes by (key, ts), writes `workbooks`/`keys`/`by_cat_side`, pushes to every worker's
  `<root>/data/endgame/` md5-verified. Consumer API: `load_ev(cat_side) -> {key: (n, mean_delta, pos_rate)}`. Ledger rows now
  carry `cat_side, ref_gain, cand_gain, cand_lift, rejected`. Tests: `tests/test_v15_endgame_knowledge_merge.py`.

---

## 70. FOUR INDEPENDENT TEMPLATES — NEVER GENERIC (USER 2026-10-07, ARCHITECTURE LAW)

The fleet-live templates are the 4 `SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx`
files — one per venue×side, independent, diverging by design (CRYPTO_LONG carried 3393 SWITCH rows on
2026-10-07; counts differ per file and grow as switches are added — never pin exact counts in prose or
tests; pin floors). A SECOND set, `SPREADSHEETS/TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx` (the "main"
set, 3456 rows CRYPTO_LONG), also exists on S1+Mac and is still written daily by the chain (step 2
"main" run — the ONLY run that writes `cat_side_promotions` + round ledger); it is NOT retired
(corrected 2026-10-07 — an earlier draft of this section wrongly called it retired). `TEMPLATE.xlsx`
no longer exists. Consequences:

- **Resolution is exact:** `get_template_for_symside()` returns the run's own file or REFUSES
  (`SystemExit`) — there is NO cross-side fallback (a missing crypto file is never substituted with a
  stocks file). `--template` still overrides explicitly (fleet launches pass it).
- **No symmetry assumptions:** tabs, rows, defaults and yellows differ per file. Any code that copies
  rows across files must map per-file tabs/headers; any test that asserts equal shape across files is wrong.
- **Writer allowlist (LOCKED_FILES.md one-script rule):** ONLY these four writers touch the template
  files (both the main and the norm set) — `tools/v15_daily_template_update.py` (avg/promotions),
  `tools/v15_template_bookkeeper.py` (books, S1 cron 12:00 UTC — before the chain, §75), `tools/v15_switch_add.py` (new rows,
  §71), `tools/v15_switch_remove.py` (row removal of a switch whose config+QuickConfig fields are already
  gone — USER 2026-10-08, same guards as the adder, run on the source host), `tools/v15_template_staged_apply.py` (verified restructures; currently refuses on the
  pre-existing violation backlog). Pilots write ledgers/progress only. No hand edits, no other
  writers, no exceptions beyond §71.
- **Daily rewrite (USER 2026-10-07 — the whole point of the templates):** every day before market
  open the chain rewrites the 4 live files with new AVG_DELTA + POS_SYM, worst-first row reorder,
  and promoted defaults, then applies the day's promotions to `config.py`/`QuickConfig`/
  `config_tradier.py` (Mac follow-up, live processes pick it up at next restart — nothing is
  restarted), to the SQL kv store + JSON state (`per_sym_store`, SQL-primary readers), and to future
  tests (pos_sym sampling evidence + zero-skip cell maps). A day with no chain stamp = a day the
  fleet sweeps on stale defaults — the Mac follow-up FAILS LOUD at step 1 and the miss must be
  caught up the same day, never silently skipped (§68.3, §75).
- **Sync direction (USER 2026-10-07 — the S1 move is COMPLETE and verified):** templates flow
  S1→fleet+Mac ONLY. S1 chain pushes 8 files to s2/s5 (step 6, md5-verified); Mac pulls the stamped
  set via `v15_daily_chain_mac_apply.sh` (13:00Z, md5-verified against the stamp). The minutely
  `sync_s1_to_mac.sh` deliberately EXCLUDES templates (a 60s pull could grab a mid-chain partial
  set — only the stamped pull is atomic). Code still flows Mac→fleet (edits on Mac, rsync + md5,
  incl. Mac-apply step 4 pushing promoted config). Every old Mac→S1 template pusher
  (`template_push.sh`, `sync_mac_to_s1_30min.sh`, `v15_cleaned_norm_sync.sh`) is not just
  cron-disabled but a fail-closed REFUSER since 2026-10-07 — re-enabling one exits 1 without
  touching anything, so stale Mac bytes can never overwrite S1's fresh templates again.

## 71. ADDING A SWITCH — THE ONE ROW-ADD PATH (USER 2026-10-06/07)

Full procedure: [SWITCH_ADD_GUIDE.md](SWITCH_ADD_GUIDE.md) (any agent can follow it). Summary of the law:

1. **Wire code FIRST:** venue config field + `QuickConfig` field (+`apply_tradier_defaults` for stocks) +
   live function on the `process_position()` path + `vec_decisions/` predicate + SINGLE call site in
   `v12_quick_engine.py` + `cat_side_defaults_4` rebuild + behavioral test.
2. **Registry + index:** `data/wiring/vec_function_registry.json` entry (`suggested_home_tab` + ≥2
   `valid_options`), then `build_switch_bible.py` + `verify_switch_bible.py` green for the switch.
3. **Rows:** `tools/v15_switch_add.py --switch --tab --candidates(≥2) --default` — dry-run, review,
   `--apply` (whole-row insert after the last WHITE row, style mirrored, default first + bold +
   `is_default` YES, backup + row-guard + defaults-gate proof). Refuses unwired/duplicates/mismatches.
4. **Proofs:** `backtest_v12_engine` ledger-flip with the switch flipped vs default; pilot smoke;
   bible green; `--fleet` sync + md5.
- `tools/v15_add_switches.py` / `v15_add_orange_rows.py` stay refused (2026-09-30 class refusal,
  superseded — never un-refuse). Backlog signal: `verify_switch_bible` COVERAGE (wired but in no
  template — 53 entries on 2026-10-07 incl. 23 retired-generic-only switches).

- **Amendment 2026-10-13 (§94) — PERSISTENT ROW INTAKE, rows can never be lost again.** Rows written only on the Mac were
  overwritten by the Mac-apply template pull (ENTRY_LH_TRIGGER_* rows added 10-09 03:06, lost 10-09 13:49Z; replayed exactly by
  `v15_template_row_intake.py rescue-lost` on the 10-09 backups). The ONE row writer is still `tools/v15_switch_add.py`; it now also
  has `--queue data/template_row_queue.json [--apply]` (same validation/insert/defaults-gate/fingerprint guards, one atomic save per
  file, flock shared with the bookkeeper, idempotent — anything dropped is re-installed) and `--enqueue [--push s1-int]` (inbox entry,
  no template write). The S1 source host owns the queue; S1 chain step 2a installs it every day before the push; Mac-apply step 2c
  queues any switch a pull would drop. On the Mac never `--apply` alone — use `--enqueue --push s1-int`. Non-scalar defaults
  (tuples/None) are refused (work order WO-20261013-LIST_SWITCH_ENCODING). Venue rule: a venue gets rows only when its config has
  the field AND its own manager reads it live (no VEC_ONLY rows, §67).

## 72. ZERO-FORMULA SKIP — NEVER-POSITIVE ROWS/CELLS ARE SKIPPED + BOOKED (USER 2026-10-06)

Evidence-condemned rows (`pos_sym=0` in ≥15 syms, `V15_ZERO_MIN_N_ROW`) and yellow cells (`pos_sym=0`
in ≥10 syms, `V15_ZERO_MIN_N_CELL`, cell map from `tools/v15_cell_evidence.py` over progress-JSON
yellows) are SKIPPED — blank, never fake 0 — with settled verdict `ZERO_FORMULA_*` (a structural
prefix in `tools/v15_row_guards.py`: never refilled, exempt from RULE#3 hollow, stands across heal
passes). Chain-neutral by construction (only deltas `>1e-9` promote — §37). **Skipped ≠ obsolete:**
every skip is booked (`progress["zero_book"]` + `data/zero_formula_book/{CAT}/{SYM}.json` + the
`ZERO_FORMULA_BOOK` sheet the bookkeeper maintains) for formula-fix investigation; a formula fix +
evidence regen lifts the skip automatically. Never skips defaults or unevidenced rows (fail-open);
kill switch `V15_ZERO_FORMULA_SKIP=0`. Measured effect 2026-10-07: 69–77% of evaluated cell evals hit
condemned cells. Guard tests: `tests/test_v15_zero_formula.py`.

## 73. POSSYM SAMPLING — CURRENT PROBABILITIES + PASS STRUCTURE (USER 2026-10-06, AMENDED 2026-10-07)

Sampling is ON fleet-wide (`V15_POSSYM_SAMPLING=1`; the 2026-10-03 full-compute suspension in §64 is
itself suspended — sampling defers, never deletes). Per-round compute probabilities from
`data/avg_delta_pos_sym.json` evidence (`pos_sym`/`n_sym`, min evidence `V15_POSSYM_MIN_N=3`):
**None→1/20, 0→1/20, 1→1/10, 2→1/6, 3→½, ≥4→every** (deterministic draw per sym|tab|row|round —
same input re-skips identically within a round; 2026-10-07: rows with NO pos_sym at all are now
sampled 1/20 like pos_sym 0 — previously always computed). Pass 1 evaluates winners; skipped
rows/cells stay pending (`SKIPPED_SAMPLING`, blank); RULE#3 refuses publish while hollow, which
schedules a sample-free REDO heal pass that evaluates every hole (holes-only, chain carried).
Bold defaults and just-added (`new`) rows are never sampled. Yellow sampling uses the filter's
orange-row evidence as proxy (cell-level evidence now exists for the zero-skip, §72, but sampling
still uses the proxy) — EXCEPT unevidenced cells: a name-yellow cell with NO entry in
`data/cat_avg/priority_evidence_{cat}.json` (NONE: never numeric in any finalized sheet) is
must-calculate priority inside every computed row and is never sampled out (`none_priority`
bucket; fail-open when the JSONs are absent). Row sampling still gates the row; the priority
applies within computed rows. Full work order: `NONE_CELLS_PRIORITY_20261007.md`.
**Fleet enforcement (USER 2026-10-07):** two layers — scheduler launch env `V15_POSSYM_SAMPLING=1`
(env wins) plus `data/possym_sampling.flag=1` on every host (fallback for any launcher); verified
live on s1/s2/s5 + Mac. Evidence distribution: chain step 4 rebuilds + pushes
`data/avg_delta_pos_sym.json`, step 4b regens + pushes `data/avg_delta_pos_sym_cell.json` +
`data/cell_evidence/*.json` (multi progress-dir union, newest-wins per sym_side); Mac-apply pulls
both with per-file stamp-md5 checks. Guard tests: `tests/test_v15_possym_sampling.py` (exact prob
table, always-compute buckets, determinism, pos=0 rate band, env precedence, zero default-ON).

## 74. FLEET REALITY + WALL-TIME MATH (AUDITED 2026-10-07)

Launcher: `tools/v15_fleet_scheduler.py` (S1 cron `*/2`, `--once` per tick) with
`tools/fleet_hosts_final.json` (4 hosts since 2026-10-07: s1/s2/s5/s6; `max_pairs`, `workers_per_side`,
`mem_reserve_mb`, `oom_mb` per host; tuned there, never in prose). Launches `--seq-mode worst2best --window-days 30 --vector-only`
(`V15_TEMPLATE_DEFAULTS=1`, `V15_FRESH_RUN=1`, `V15_POSSYM_SAMPLING=1`, `V15_UNWIRED_SKIP=0`,
`V15_SKIP_LIVE_AT_DONE=1`, NO `V15_INGEST_BEST`). Reaps: 8h hardcap, 60min stall, OOM floor
(youngest pilot SIGKILLed — progress JSON persists, relaunch refills). No launches over cap or under
reserve. Observed 2026-10-07: S1 also runs the live stack + scheduler (load 20–40 pre-tune);
S2/S5 additionally run the `v15_trade_parity.py` lane (~3.5 cores each); S1 reboots interrupted runs
(daily ~12:55 plus ad-hoc — cause under investigation, needs sudo).
**Chain sequence (VERIFIED 2026-10-07 — currently OFF):** the scheduler codes the full per-symbol
chain (`need30` → `need365` → GS-heal/`needrepair` → terminal; pass 2 steals 365D/REPAIR-a1 onto idle
hosts; slot released only at terminal), BUT it is gated: `V15_SCHED_NO_CHAIN=1` (S1 cron env, USER
2026-10-01 order) + missing `data/autopilot/chain_mode.flag` → slots release after 30D. The 30D→365D→
30D-again loop runs ONLY when the flag file exists (one command: `touch` it on S1). Do not create it
without explicit user order — 365D chains multiply per-symbol compute several-fold.

**Wall-time math (why 0.07s becomes hours — all figures audited, none estimated):** 0.07s is per-EVAL
(one candidate; loaded 0.09–0.17s). Per-ROW ≈ 205 evals / workers (≈7s at 3 workers). Per-SYM ≈
3393 rows × ~1.5s avg (skipped + eval mix) × ~2.7 refill passes + FINAL_RECHECK (~3800 evals) +
DIAGNOSE/REPAIR365 (~20k evals) ≈ 5–8h at unloaded pace, 12h+ contended. Reduction levers in force:
fewer+faster pilots (cores are the bound), zero-formula skip (§72, ~70% of cell evals), possym
sampling (§73). Structural floor: full-fidelity (all yellows + all passes + post-stages) cannot go
sub-~50min even at 16 workers — sub-10min requires cutting evals (fewer yellows / single pass / no
post-stages), a science decision, not an ops tweak.

## 75. DAILY SCHEDULE — CLOCKS, ORDER, RETRIES (USER 2026-10-07)

All UTC. Market open 13:30Z (09:30 ET) — everything lands before it.

| UTC | What | Where | Note |
|---|---|---|---|
| 12:00 | bookkeeper (ZERO_FORMULA_BOOK sheets) | S1 cron | Runs BEFORE the chain: the chain writer preserves non-SWITCH tabs, so books ride through the rewrite and the stamp md5s include them. Moved from 12:30Z 2026-10-07 (it raced the chain's step-2 rewrite on the same files with a different lock). |
| 12:15 | daily chain (avg → templates → defaults → possym → push s2/s5) | S1 cron `V15_DAILY_CHAIN_S1` | ~45 min. Stamps `data/daily_chain/<date>.json`. Fail-stop (except watchdog/tradeable-check). |
| 13:00 | Mac follow-up (pull stamped set → kv sync → config sync → push code → gates) | Mac cron `V15_DAILY_CHAIN_MAC_APPLY` | Waits up to 55 min for the SAME-date stamp — the Mac is ~1h behind S1, never a day, when the chain lands. |
| 20:00 | Mac retry slot (same script, step-0 no-ops a DONE day) | Mac cron `V15_DAILY_CHAIN_MAC_RETRY` | Catches late chains (stamp after ~13:55Z). Added 2026-10-07. |
| 06:00 | Mac early slot (same script) | Mac cron `V15_DAILY_CHAIN_MAC_EARLY` | 2026-10-08 (USER tandem lane): the script now auto-picks the NEWEST S1 DONE stamp (today, else yesterday) that has no Mac DONE stamp, so a chain that landed overnight (2026-10-07 landed 00:04Z next day and was orphaned) is applied before open. |
| 13:05 + 13:25 | TANDEM GUARD `tools/v15_tandem_guard.py --notify` | Mac cron `V15_TANDEM_GUARD_1305/1325` | 2026-10-08 (USER: "everything in tandem before market open"): S1 stamp DONE + Mac stamp DONE same date; TEMPLATE bold == cat_side_defaults_4 == venue config == QuickConfig for EVERY key (hard classes bold-vs-quick/bold-vs-cat/bold-vs-global/not-in-configs fail; side-split-cat-truth, fallback-split, builder P0-FOSSIL pins, kept-dead rows informational); startup_gate hard==[] ×4 cat_sides; config.py/config_tradier.py/v12_quick_engine.py md5 identical Mac vs every fleet dir (sandbox+live, s1/s2/s5/s6). Writes `data/daily_chain/TANDEM_<date>.json`; FAIL → `ALERT_TANDEM_<date>.txt` + macOS notification + exit 1. |
| @reboot | chain catch-up (only if no stamp AND slot passed, ≥12:20Z) | S1 cron `V15_DAILY_CHAIN_REBOOT_CATCHUP` | Added 2026-10-07 after S1 rebooted through the 12:15Z slot and the whole day was silently skipped. |

Rules: a FAILED mac-apply is never the end of the story — the 20:00Z retry or a same-day manual
run closes it. A new cron slot that writes templates or config MUST reuse these scripts (never a new
writer — §70 allowlist) and MUST be idempotent (stamp-checked no-op). Cron edits are backed up to
`backups/before_*cron*` like any other edit.

## 76. WRITER SELF-HEALING + PERSISTER LAW (USER 2026-10-07)

**Default repair:** the daily writer establishes what it verifies. A group with no promotable
winner keeps its default — unless broken (0 or 2+ YES/bold), in which case the writer heals
it color-blind (broken switch groups sit in orange rows too — first version skipped orange and
repaired 0): single-bold half-write → YES completes it; else the row matching the venue live
default (`venue_values`: QuickConfig overlaid with `config.Config`/`TradierConfig`); else the first row.
Orange filter groups are not repaired (owned by tab-level appends; the gate still guards them).
`--no-promote` disables repair. Repairs land in `repaired_defaults`, NEVER in `ledger`/`promoted_keys`
(they align the template TO config — nothing syncs back, no floor-guard). First catch: 96 broken
groups on 2026-10-07 (STDEV_*/GOLDEN_RULE_*/VOL_SPIKE_*/REENTRY_TIER* — recent switches that landed
without defaults and bricked the chain + fail-closed pilot baselines). Guard tests:
`tests/test_v15_daily_template_update_repair.py`.
**Ancillary hygiene:** the writer captures the docs-inventory subprocess output (a traceback there once
failed the whole chain on the `^Traceback` grep AFTER all 4 templates had saved); the inventory
`--ensure` skips clean (exit 0) when `data/rowcoverage/latest.csv` is absent on its host instead of
tracebacking. Ancillary steps never fail the chain — only template/ledger/push steps do.
**Persister:** of the chain's two writer runs, only the MAIN run persists `cat_side_promotions` +
round ledger + `promoted_keys` (gate: `tdir == SPREADSHEETS`); the norm run writes xlsx only. The
stamp reads the main report. (A 2026-10-07 repoint briefly flipped the gate to norm — restored same
night; repoints must never flip behavioral gates.) OPEN DESIGN QUESTION (pre-existing, not changed):
fleet defaults JSON is built from MAIN bolds while pilots read NORM bolds — main/norm bold drift
would split sweep/live from pilot baselines; needs a director ruling, not a silent fix.

## 77. AUTOPSY-INERT ROWS — NAKED EVERY ROW, YELLOWS ONLY WHERE THE ENGINE CAN MOVE (USER 2026-10-08, approved #1 "only the real useless/dead rows skipped … revise them 1 in 25")

- A 30D board launched with `V15_START_OVERRIDES` whose base json carries `autopsy.effective_switches` (written by `tools/v15_autopsy_first.py`
  from `tools/v15_trade_autopsy_run.py`: switches whose flip touched ≥1 trade on this sym_side's template-bold base, plus every kept combo key)
  runs in AUTOPSY-INERT mode (`v15_pilot.py` spec-fill, `[AUTOPSY-INERT] <ss>: N effective switches …` in the log).
- EVERY row still gets its NAKED candidate evaluated by the real engine → `G` (col G) is a real delta on every row, every board. Nothing is a fake 0.
- A row whose switch is NOT effective skips its yellow (`L:BI`) evals — those cells stay BLANK (no value, no paint, no `yellows` entry in the
  progress row; `inert_filters` lists the headers) — unless:
  1. rotating sample: `(row + round_seq + tab_hash) % V15_INERT_SAMPLE_EVERY (25) == 0` → full yellow pass (`inert_sampled`, `SAMPLED` in `[spec-row]`), so
     every inert row is fully re-tested once every 25 rounds, a different 4% slice each round; or
  2. revive: the naked eval moved the ledger (`|G| > 1e-9`) → the full yellow pass runs on the spot (`[AUTOPSY-INERT-REVIVED]`, `inert_revived`).
- Blank inert yellow cells are NOT missing cells: `complete` is unaffected, `v15_assure`/refill must not "repair" them, AVG_DELTA sees no value (never a 0).
- `V15_INERT_PRUNE=0` disables (full board). Without `effective_switches` in the base (old bases) there is no pruning.
- Measured 2026-10-08 (ADAUSDC_SHORT, 70/1197 effective): 436 rows in a 15-min slice (fleet boards: ~1–2 rows/min), 350 pruned, 14 sampled, 10 revived, 7 promotions, ~3.8 GB total RSS.
- ENGINE ARTEFACTS ARE NEVER CANDIDATES OR EVIDENCE: `MODE`, `SIMPLE_PRICE_GT0_ENABLED`, `VENUE`, `IS_TRADIER`, `ABLATION_*` — denied in the autopsy
  (`AUTOPSY_DENY`), the priors (`PRIOR_DENY`), go-live (`ARTEFACT_KEYS`, stripped before the fresh eval) and the pilot (`ABLATION_NEVER_ON` row skip).
  2026-10-08: 137 stocks bases "rescued" by `MODE=crypto`, and 75 LIVE per-sym sets (73 with ablation ON) had to be quarantined/stripped.

## 78. OVERRIDE SETS ARE NOT KNOBS — RESTATED DEFAULTS NEVER FORCE MASTERS (2026-10-08)

- `tools/opt/evaluate_v12._expand_dependencies` forces a candidate knob's master switches ON (and OR-peers OFF) so a single evaluated row is
  binding. That rule applies to EVALUATED knobs only. A key whose override merely restates the prepared base value (`_restates_default`) is
  part of a SET (template bolds, autopsy base, cumulative set) and forces nothing.
- Why it is law: ABT_LONG engine defaults = 14 trades; the same 776 template keys passed explicitly at identical values = 0 trades (every
  master of every sub-knob forced ON at once). 83 STOCKS_LONG, 12 STOCKS_SHORT and 8 crypto autopsy bases were "dead" for this reason, and
  every dead-zero board of the last weeks that started from a full set is suspect.
- Consequence: baselines computed before 2026-10-08 20:00Z from full sets understate trades for stocks; bases are re-autopsied; the scheduler
  holds sym_sides whose template base still has 0 trades (`waiting_base`) instead of launching ZERO_TRADES boards.

## 79. STOCH-K ENTRY VETO IS A SWITCH, OFF — WT IS THE FILTER (USER 2026-10-08)

- `ENTRY_VET_COMBINED_STOCH_GATE_ENABLED` (default False, all four surfaces + template rows in ENTRY_CONFIRMATION_GATES) gates the
  `COMBINED_STOCH_GATE_TRADIER` (k>=60 long / k<=40 short) veto in `ez_manage.check_entry_vetting`, both `tradier_manage` k5m sites and the
  vec (`entry_sig & vec_stoch_gate_pass`, the earlier `extra_ok` copy never reached the opens).
- Why OFF: trade-level parity on ADAUSDC_LONG/SHORT + AAVEUSDC_SHORT (npz twin source) showed every live trip in vec with identical exits and
  the only gap = 153/35/44 vec opens vetoed live by this gate, vec P&L +9.29/-0.71/+1.12 — rallies happen at high k; vetoing them loses the rally.
- Parity procedure: harness forces `PARITY_VEC_EXACT_SOURCE=npz`; attribute vec-only opens via the live log (`[VEC_EXACT] … OPEN … -> <result>`)
  before touching engine code.

## 80. MISSED-TREND REVISION ROUND — BEST_EFFORT BOARDS RE-CHECK ENTRIES/EXITS/FILTERS (USER 2026-10-08, AGLDUSDT_SHORT autopsy)

- **Why:** `AGLDUSDT_SHORT_bhm12p30_gain2p_t17_30d_matrix` (s2, 2026-10-08) published gain +2.95% / TIM 1.94% / 17
  one-bar scalps (median hold 1, `EXIT_VELOCITY_WT` 12/17) while price slid **-15.97% from the 0.2292 top (bar 1757,
  2026-09-26 16:45) to 0.1926 (bar 2874)** with the first entry 201 bars (~50 h) late — plus three more 0%-covered
  slides (+13.00/+10.90/+10.47%). The sweep found **0 positive rows in all ENTRY/REENTRY/EXIT tabs**, compliance
  repair could not lift TIM with single-switch steps, the board published `BEST_EFFORT` — and the scheduler treats
  `BEST_EFFORT` as **TERMINAL** (`tools/v15_fleet_scheduler.py:_quar_terminal`), so no revision round ever re-checked
  entries, exits, filters or the missed move. Self-improvement existed only cross-sym (daily chain avg_delta); per-sym
  trend misses were frozen forever.
- **Tool:** `tools/v15_missed_trend.py` (NEW, zero locked-file edits). `scan` (Mac-safe, engine-free) parses a published
  chart `.html` (or progress+NPZ) into non-overlapping favorable legs over the FULL series with per-leg cover,
  `late_by_bars` and churn evidence; `revise` fires on TIM < 20 with a ≥8% leg covered <35%. `screen` (fleet only, full
  NPZ) evaluates every `ENTRY_*` + `REENTRY_*` row + premature-exit HOLD rows (`MIN_HOLD/MIN_TFS/COOLDOWN/TARGET_TF/
  STOP_TF/REQUIRE_PROFIT/REQUIRE_GAIN/MIN_GAIN/VELOCITY` in `EXIT_*`) + SOFTEN/ENTRY_PATH bools vs the published final
  set, attributes each to the missed legs via `v15_trade_autopsy.attribute`, then keeps an engine-verified surgical
  combo (valid AND gain up AND TIM not down — §19). A leg no switch captures becomes a `PROPOSED_NEW_SWITCH` spec
  (e.g. SHORT trigger on the first lower high — LH_HL rows today are FILTERS, not triggers) for the §71 pipeline.
- **Re-run includes the fix:** `screen` writes `{SS}_missed_trend_base.json` in the `V15_START_OVERRIDES` schema (same
  as `v15_autopsy_first`, incl. `effective_switches` for AUTOPSY-INERT + the §64 TEMPLATE_DEFAULTS exemption); the
  fixing switches ride IN the base, so sampling/zero-skip/inert can never hide them. `screen` also emits a priors
  partial in the exact `merge_partials` shape (same contract as `v15_autopsy_priors`) so the daily chain learns the
  verified deltas cross-sym. `requeue` (explicit, per-sym, reversible) archives the BEST_EFFORT final to
  `SPREADSHEETS/V15_V16_HISTORY/`, archives the stale progress file (rows measured for the old set), optionally
  installs the base into `~/v15_autopsy_first/` — the scheduler's existing launch env then starts a fresh `need30`
  revision round from the repaired set. Refuses QUALIFIED/IMPOSSIBLE without a human decision; never deletes.
- **Fleet procedure (S1/s2 shell):** `python tools/v15_missed_trend.py screen --symside SS --set-json <final-set> --out ~/v15_missed_trend --workers 6`
  then `python tools/v15_missed_trend.py requeue --symside SS --progress <round-pdir>/SS_v14_progress.json --base ~/v15_missed_trend/SS_missed_trend_base.json --autopsy-dir ~/v15_autopsy_first`
  (`--dry-run` first). Guard tests: `tests/test_v15_missed_trend.py` (incl. the AGLD bar-1757→2874 regression).
- **AGLD outcome (s2, 2026-10-08 23:4xZ):** screen of the 788-key published set: 994 candidates, 55 effective, 0 errors —
  `RESCUED` 2.95 (17t TIM 1.94) → **4.30 (21t TIM 2.50)** via `HTF_DIRECTION_GATE_ENABLED=False` (+1.04pp, 14 captured
  move-trades — the HTF gate vetoed entries into the slide, §17 entry-blocker class) + `MIN_HOLD_BARS_BEFORE_EXIT=32`
  (+0.30pp vs 1-bar churn). 9 REENTRY switches moved the ledger but captured nothing — no existing reentry re-enters
  this slide; entries still start ~200 bars after the top, so a first-lower-high SHORT trigger remains a
  `PROPOSED_NEW_SWITCH` for the §71 pipeline (needs user unlock: engine files are locked). Trend base beat the
  pre-board autopsy base (3.37/17t) on the current engine and was installed for the requeue. Tool lesson: the fork-pool
  worker MUST be module-level (`_screen_w` + `_SCREEN_PREP` global, autopsy_run pattern) — a `run_screen` closure fails
  all futures with `Can't get local object` (first run: 994/994 errors swallowed, then surfaced via `n_eval_errors`;
  pickle regression test pins it).

## 81. TIM_LOW_REPAIR — TIM<20 FINAL ROUND ADDS ENTRIES, LOOSENS FILTERS, FORCES REENTRIES (USER 2026-10-09)

- **Trigger:** `tools/v15_diagnose_repair.py:run()` phase `1b`, after SOFTEN, before the ADD beam — only when the
  post-worst2best state is valid with TIM<20 and budget left (`S.left()>0.30*work`). No pilot edit: the hook lives
  inside `run()`, the ADD beam builds on the repaired state.
- **Stages (each multi-pass to exhaustion, one verified apply per pass, stops at TIM≥20):** `TIM_LOW_ENTRY`
  (ENTRY_* rows — add entries), `TIM_LOW_FILTER` (filter/gate rows — loosen filters), `TIM_LOW_REENTRY` (REENTRY_*
  rows — mandatory: runs even when the other stages applied nothing). `TIM_LOW_PAIR`: one bounded round (top-8
  reentry × top-8 entry/filter singles, ≤64 evals) when no single reentry verifies — the greedy blind spot.
- **Acceptance (`_tim_accept`, NO-LIES verified, unit-tested):** validity-first, TIM-gap must strictly close
  (or, gap held, trades AND gain strictly climb as a stepping stone); DD≤30, trades≥10; a profitable base must
  stay profitable (dips allowed, red forbidden), a red base must strictly rise. Reverts/safety/forbidden never win.
- **Proof:** `tests/test_v15_diagnose_repair.py` (AGLD-shaped 17t/TIM-1.9 synthetic origin through `DR.run()`:
  all three stages fire, TIM 1.9→24.9, red-flip + safety never applied; pair synergy; skip when healthy).

## 82. KNOWLEDGE REFRESH — BIBLE CYCLE HARDENED + CENSUS EVIDENCE REBUILT (USER 2026-10-09)

- **Switch bible cycle:** the `md5: command not found` + killed-build log lines were STALE (Oct 6, pre-fix script;
  log untouched since). Verified healthy (cycles completing, latest full cycle 1m55s, verify at the known
  BROKEN≈74 backlog), then hardened: cron `PATH` export + fail-closed `md5` resolution, stale log truncated so
  new failures surface. `tools/switch_bible_cycle.sh` backup in `backups/before_bible_cycle_pathfix_*`.
- **Census evidence:** new `tools/v15_census_evidence.py` (tested, `tests/test_v15_census_evidence.py`) re-mines
  the CURRENT mirror: 443 progress files / 835k P_naked evals (93% baselines via the Oct 6 zero-delta-mode rule,
  both done-row schemas) → `data/encyclopedia/lever_evidence_20261009.csv` (12,549 levers: 537 ADD / 811 REMOVE /
  265 MIXED) + `data/encyclopedia/fleet_diagnosis_20261009.csv` (141 manifests, manifest-only — Mac progress is a
  different era, verdict join proven wrong) + `docs/encyclopedia/09b_lever_evidence_refresh.md` (top-40 tables).
  Oct 6 files untouched (their F/L sources not redone — compare within-refresh). Hub stamped; prose chapters stay
  Oct 6. The v2 graph (what the pilot reads) cycles healthily on S1 (*/30, last build 01:01Z); note its
  `n_fresh_levers` is crypto-only right now (STOCKS 0 — evidence follows the runs).

## 83. REDO START-SET PRECEDENCE — REPAIRED SET ALWAYS WINS (USER 2026-10-09, AGLDUSDT_SHORT proof)

- **Bug:** a REDO re-fill silently discarded its repaired start set twice: `needs_redo` was ignored when
  `V15_START_OVERRIDES` was set (stale autopsy base won), and dropped by `TEMPLATE_DEFAULTS` when it was not
  (AGLD REDO re-filled from template defaults at -5.80/97t instead of the repaired +1.12/121t/TIM-25.9 set).
  Every fleet REDO under the standard launcher env was affected.
- **Fix (`v15_pilot.py`, backup `backups/before_redo_precedence_*`):** `needs_redo` ingests unconditionally and
  sets `_nr_applied`; the TEMPLATE_DEFAULTS drop is skipped when set. Precedence: repaired set > start
  overrides > priors/template. Pins: `tests/test_v15_redo_precedence.py`; live proof: reseeded REDO logged
  baseline +1.0497/140t matching the pre-launch eval to the digit. Deployed S1+s2 (md5 `cfcacf11`).

## 84. INVENTION LOOP — OBSERVATIONS BECOME TESTED SWITCHES (USER 2026-10-09)

- **Gap closed:** the fleet produced invention observations (missed-trend specs, autopsy missing_functions,
  diagnose gaps, verify COVERAGE) with no collector, ranker, or tracker — §71 ran per-incident by hand.
- **Tool:** `tools/v15_invention_loop.py` (tested, `tests/test_v15_invention_loop.py`): `mine` scans all
  sources into `data/invention/backlog.json` (dedup by signature, score = uncaptured %/frequency; cron
  `V15_INVENT_MINE` every 30 min, reads + backlog write only); `spec <id>` renders a §71 Muse work order to
  `data/invention/orders/` (measured evidence + nearest wired anchors with file:lines from SWITCH_BIBLE.json +
  acceptance; the intelligence step is a Muse session — the script never touches locked code itself);
  `ingest` validates WIRED (bible status + all three configs) and TESTED (ledger-flip proof with real flip);
  `promote-check` moves TESTED→PROMOTED (pos_sym≥3, mean delta>0) or →RETIRED (≥10 syms, 0 pos) from done-row
  evidence. RETIRED records the verdict; nothing is ever deleted. Seed: 72 proposals, top = AGLD SHORT
  LH_TRIGGER (INV-0001, score 19.72).

- **Amendment 2026-10-13 (§94):** the tool runs on S1 (chain step 4e: `mine --write` + rows-check + `promote-check`; the Mac cron
  `V15_INVENT_MINE` is commented `#MOVED_TO_S1_CHAIN_4E_20261013#`, Mac-apply step 2d pulls `data/invention/`). `scan_autopsy` now reads
  the fields the autopsy really writes (`missing_entry_filters` / `missing_exit_confirmations`), groups by (at, cat_side, feature, op),
  counts DISTINCT sym_sides (≥3), score = |mean pp| × distinct syms; evidence is deduped by signature (INV-0001 223 copies → 1).
  First S1 run: 300 observations, 164 autopsy rules, 175 new proposals (all `[DIAGNOSTIC ONLY]`: single-feature in-sample thresholds
  that remove about as many good exits as bad). `rows-check` reports ROWS_INSTALLED only when the rows exist in the templates.
  Engine work orders: data/invention/orders/WO-20261013-*.md.

## 85. CAUSAL LEARNER — EVERY LOSER + NEG-DELTA BECOMES A RANKED FIX (USER 2026-10-09)

- **Tool:** `tools/v15_causal_learner.py` (tested, `tests/test_causal_learner.py` 9 tests): SQLite
  `data/causal_learner.db` lessons→causes→fixes→fix_outcomes, ranked by `fix_ranking` view.
  `ingest-progress` records every sweep cell delta (all deltas, not just negatives — positives are the
  auto-lift fuel); `ingest-live` records every live losing trade (net<0) attributed weakly (0.2x) to the
  sym's live override set (`per_sym_store.get_overrides`, NOT `get_full_config` — 28 switches, not 3518).
- **Key format law:** pilot progress keys are `TAB!<rownum>:SWITCH=cand` — switch AFTER the colon.
  `_parse_cell_key` validates `^[A-Z][A-Z0-9_]*$`; row numbers ('10','100') are rejected, never condemned.
- **Confirm bar (calibrated 2026-10-09 on 877k run-dir causes, 1134→13):** net ≤ −10 over ≥3 syms,
  ≥3 strictly-negative direct, ZERO positive direct, avg direct ≤ −0.5. ABLATION_* harness flags never
  confirm. Live evidence alone can only propose, never confirm.
- **Export guards:** `export` cross-checks the aggregate — any cell with pos_sym>0 vetoed, any switch
  with an aggregate-positive cell vetoed (saved LH_LL_TOP_EXIT_STRUCT_TF crypto both sides on first run).
  Stale confirms auto-demote; post-confirm positive sweep evidence auto-lifts (hurt→rejected with note).
- **Schedule:** S1 chain step 4c ingests `v15_run*/` + `lifecycle_pilot` (production campaigns only — never
  smoke/demo/autopsy/test dirs) and exports `data/causal_condemned.json` BEFORE 4b (same-day merge) and the
  step 6 push; Mac apply step 5e ingests live losers and union-merges (union-only, never shrinks the S1 pull).
  Consumers: step 5 watchdog disable path + `v15_cell_evidence` runtime merge. First S1 seed: 29,671 lessons,
  877,804 causes, 27 final condemns (3 cats × 13 unique minus 4 aggregate vetoes).
- **Amendment 2026-10-09 (USER: rescue round + name identity, never row numbers):** (1) NAME IDENTITY LAW —
  lesson key is canonical `TAB!SW=cand` (row number dropped) + campaign dir as measurement epoch
  (`progress/` subdir unwrapped to the run name); reorder-proof, verified by
  `test_rownum_reorder_same_lesson`. (2) Discovery fixed: run progress files live in `progress/`
  subdirs — both globs now scan top + `progress/` (sanctioned layout per avg-delta `LIST_CMD`); full
  seed is 273,569 lessons / 4.42M causes → 18 condemns + 84,901 cells + 40 rescue. (3) RESCUE: export
  carries a `rescue` shortlist (net ≤ −10, ≥3 syms, ≥3 neg, 1–2 pos — e.g. RSI2_XTREME_ENTRY_TF
  −6506/135 syms/845neg/2pos) for the ablation rerun instead of a kill. (4) SKIP ROTATION: condemned
  yellow cells were frozen forever (no resample); `v15_cell_evidence` now releases a deterministic
  1-in-25 (name-hash × day, `V15_SKIP_ROTATE*`) of runtime skips daily for retest — no pilot edit needed;
  audit in `data/cell_evidence/_rotated_release.json`; proof on run29+30: 1,810/44,818 released.
  Naked still runs every row; disabled switches are NO-SKIP log-only in the pilot. (5) NEVER-CONDEMN
  mirrors `AUTOPSY_DENY` + pilot `KG_NEVER_SKIP` (exact + substring) — MODE demoted on sight.

- **Amendment 2026-10-13 (§94) — rescue consumer + DB cap.** `tools/v15_causal_rescue.py run` turns the export's `rescue` list
  into real-engine ablations (LOO to default + naked candidate tries, `evaluate_prepared_sanitized` on rotating autopsy bases per
  cat_side) → `data/causal/rescue_outcomes.{json,md}`; `ingest-outcomes` writes `fixes(kind=rescue_ablation)` + `fix_outcomes`
  (verdicts `ablation_*`/`naked_*`, deliberately not `helped`/`hurt`, so confirm/auto-lift logic is unchanged). First run (s6, 26
  switches × 16 bases, 47 s): fix_outcomes 18 → 659; every rescue switch was already at its default and naked tries were mostly
  exactly 0 — the large negative "net" totals look like chained side effects, `[DIAGNOSTIC ONLY]`. `cap --apply` (dry-run default,
  `V15_CAUSAL_DB_MAX_GB` 2.0, integrity-check gated, protects confirmed/rejected causes, keeps lesson tombstones so ingest cannot
  re-add them, VACUUM): 4.99 GB → 2.10 GB on S1 (backup `backups/before_causal_cap_202610131644_causal_learner.db`). Weekly: chain step 8c.

## 86. SCHEDULER DONE-STALL — NESTED endgame.final_gain FROZE THE FLEET AT ~1 RESULT/2H (USER 2026-10-10)

- **Symptom:** 4 hosts at 93–100% CPU, 94 RULE#3 REDO-refusals vs ~0 publishes in 12h; needs_redo boards
  (625–758 hollow rows each) rotted 5–16h unrelaunched; `done_n` 88 with almost no real finals.
- **Root cause (two poisons, one regex):** the probe's flat `"final_gain"` regex matched the NESTED
  `endgame.final_gain` mid-run stage gain → (1) every endgame-reaching board marked DONE, so its REDO
  refill never relaunched; (2) the same phantom values harvested as tier gains, and M-tier deferral
  (1d window) then parked both sides as `terminal_deferred` — HYPEUSDT invisible 8h+ on an unpublished
  "gain 19.3". AXTI parked correctly (not in universe/allowlist).
- **Fix (`tools/v15_fleet_scheduler.py`, tests `tests/test_sched_done_marks.py` 4/4, S1 md5-verified):**
  (1) `_progress_marks()` — TOP-LEVEL `final_gain` (numeric, non-bool) + no `needs_redo` = done;
  top-level verdict = quarantine; probe cache versioned to v2 (v1 verdicts poisoned). (2) Tier deferral
  exempts already-started sides (chain continuations drain, only fresh boards defer — the docstring's
  own promise). (3) One-time purge of 88 phantom `gain_seen` entries (state backed up).
- **Proof:** next ticks: done 88→8, ENAUSDC REDO relaunched, HYPE requeued pending, running 6→15.
  Probes ship from S1 (`HOST_PY`), so the S1 file fixes all hosts. Watch: refills must converge to
  publish at depth 1–2 — if RULE#3 refuses again with the same holes, the heal (possym-only) doesn't
  cover zero-skip/inert holes and the gate needs a sampled-cells exemption or the refill must go
  fully sample-free.
- **Second killer found 01:50Z (same stall):** the OOM guard murdered ENAUSDC_SHORT's refill at 186 rows
  (MemAvailable 1884MB) as "least-progress victim" — refills ALWAYS lose that contest, and each murder
  burns 1 attempt (cap 3: ENA + BB LONG were already parked). S1 (31GB) carries live stack ~8GB + chain
  + refresh + 2 sweep pairs ≈ 12–15GB → structural OOM. Fixes: (1) `_rank_oom_victim` spares refills
  (`_progress_is_refill`: needs_redo/_redo_heal_run/redo_depth≥1 sorts last; tested); (2) s1
  `max_pairs` 3→1 in `fleet_hosts_final.json` (honest capacity beside live); (3) forgave the proven
  host-fault OOM burns (ENA SHORT 3→1, BB LONG 3→1, noted in state). Scheduler deployed fleet-wide
  md5 `e2fc6ac8` (reap imports it on every host).
- **Throughput push (USER 2026-10-10: s7 destroyed, need >10 sym_sides/hour on s1/s2/s5/s6):**
  (1) `V15_POSSYM_SAMPLING` 1→0 in the launch env (S1 md5 `ada54098`): sampled fills left ~700 holes and
  holes-only splice failed 4/4 observed (promoted holes → full wipe), so every board paid ~1.6 fills; one
  full fill publishes first try. Applies to new launches; running sampled boards publish via their
  sample-free REDO refill. (2) s7's autopsy-first backfill moved to S1 (detached, workers 4, nice 10):
  35 fresh sides were `waiting_base`-blocked; dead `V15_AF_PULL` cron disabled (crontab backed up).
  s6 left at 5 pairs (9.2GB avail, no OOM — CPU contention only; tune to 4 if rate still <10 after
  convergence). Monitor watches publish-count new highs. 02:30Z: S1 OOMed again (1045MB) on backfill
  (4 workers) + pair + live — backfill throttled to workers 2 / nice 15; several backfill sides ERROR
  (Nonet/missing NPZ → template-bold base only, may stay base-blocked until data exists). BB LONG
  forgiven 3→2 (last life before park).
- **10-MIN WORST_FIRST + 50-MIN PUSH (USER 2026-10-10: never stop mid-run work; make it faster):**
  Pushers resumed (SIGCONT + crons restored, verified computing). Bottleneck measured: 14% of rows
  evaluate 175–225 yellows each = 96% of all evals; sample-free fills run ~9 rows/min (6h/board).
  Fix (1) COARSE SWEEP (`v15_pilot.py`, tests `tests/test_coarse_trim.py` 3/3, canary s1+s2 md5
  `6528ef62`): `V15_COARSE_YELLOW_CAP=24` (kill-switch `V15_COARSE_SWEEP=0`) caps yellows/row to the
  top-K by fleet pos_sym priors; overflow recorded as `coarse_deferred_filters` (NOT sampled/hollow);
  new rows + REDO-heal passes exempt; promoted rows earn the full matrix next pass (`COARSE-EXPAND`);
  RULE#3 + `complete` forgive pure sampled-cells rows in coarse mode (`COARSE-FORGIVE`, healed only for
  genuine missing/policy holes). Naked still runs every row; chain stays conservative (coarse understates,
  never overstates). Fix (2) PUSHER BUDGET (`tools/v15_gain_pusher.py`, tests
  `tests/test_pusher_budget.py` 2/2, fleet md5 `71cf07ff`): `V15_PUSH_BUDGET_MIN=50` wall-clock deadline
  pierces round loop + screen_all + elim + ablation; early stops report partial moves + `budget_hit`.
- **Pusher starvation (USER 2026-10-10: 0 sym_sides in 2h, need 20/hour or s2/s5/s6 shut down):**
  `v15_gain_pusher` marathon polish jobs (5–7h old, round 10) consumed 59–69% of fleet CPU while
  pilots crawled at ~30 rows/min (loads 30–42). Fix: SIGSTOPed all 40 pushers on s2/s5/s6 (resumable,
  no state loss) + disabled the per-host `v15_pusher_supervisor` crons (backed up to
  `/tmp/crontab_before_pushstop_20261010.txt` each host). Loads collapsed to 8–16 within 3 min.
  Resume with `pkill -CONT -f 'v15_[g]ain_pusher.py'` + cron restore when board rate is safe.

## 87. PRIOR POLICY + TIERS + FOCUS ROUND + CADENCE (USER 2026-10-12 — "refactor based on all results so far")

**Evidence (real, mined, never estimated):** `tools/v15_switch_causal_profile.py` mines EVERY fleet delta log (bible §20)
into `data/causal/switch_causal_profile.json` + `.md` — 2026-10-12: 207 logs, 10.0M real engine evals, 171 sym_sides. Per
cat_side, per `SWITCH=cand` (naked) and per `FILTER=opt` (marginal vs the row's naked): `n_sym, pos_sym, neg_sym, invalid_sym,
mean/median delta, sum_pos/neg, mean_d_tim, mean_d_trades, promoted_sym, verdict, effect` tags. Verdicts: USEFUL (pos≥3,
pos_rate≥15%, mean>0) / SOMETIMES / NEUTRAL / INSUFFICIENT (n<3) / USELESS (n≥10, ≤1 pos, <5%) / DEAD (n≥10, never moved) /
HARMFUL (n≥10, 0 pos, ≥3 neg or invalid). d_tim/d_trades = engine differences vs the latest reproduced baseline (a valid
|delta|<1e-9 eval reproduces the running set, §14.1). **Measured: 84–93% of all fleet eval time sat on DEAD/USELESS/HARMFUL
keys** (CRYPTO_LONG 84%, CRYPTO_SHORT 88%, STOCKS_LONG 93%, STOCKS_SHORT 91%). DD/WR are not logged, so
`tools/v15_switch_flip_study.py` re-measures the flagged switches on real autopsy bases with the real engine
(`evaluate_prepared_sanitized`; 2026-10-12 s5: 4×30 bases × ~60 flips, 123 s, 0 errors) → `data/causal/switch_flip_study.json`
+ `.md` with `d_gain, d_tim, d_dd, d_wr, d_trades` per flip. Causal findings worth knowing: entry blockers
(`WT_DIV_ENTRY_GATE_ENABLED=False` +3–7pp TIM, +5–40 trades), churn cutters (`EXIT_VELOCITY_WT_MIN_TFS=2` +3pp TIM, −23 trades),
and the consistent killers (`MOM3_FILTER_TF=D`, `DC_BREAK_FILTER_TF=*`, `WICK_REJECT_ENTRY_ENABLED`, `STOCH_XTREME_ENTRY_ENABLED`,
`RSI2_XTREME_ENTRY_ENABLED`: −3 to −7pp gain, −8 to −27pp TIM, −20 to −48pp WR, dozens of trades lost; low `MAX_AUGMENTS` −4pp
on stocks). Re-run both tools after every round (S1: `python3 tools/v15_switch_causal_profile.py`; a worker:
`.venv/bin/python tools/v15_switch_flip_study.py --workers 8`).

**Pilot (`v15_pilot.py`, `prior_row_policy` / `symside_tier` / `_focus_round`, tests `tests/test_v15_prior_policy.py`):**
- **Law kept:** EVERY row still gets its NAKED eval (§77, real G, never a fake 0). Only the yellow matrix (96% of evals) is pruned.
- **PRIOR-SKIP:** a row whose `SWITCH=cand` verdict is DEAD/USELESS/HARMFUL on ≥ `V15_PRIOR_MIN_N` (10) sym_sides runs
  naked-only; inside kept rows, cells whose `FILTER=opt` verdict is DEAD/USELESS/HARMFUL are skipped. Skipped cells are BLANK,
  SETTLED (not hollow: RULE#3 never refills them), booked in `progress["prior_policy"]` + done-row `prior_skipped_filters` /
  `prior_mode`. Never pruned: sacred families (`SACRED_ZERO_EXEMPT_PREFIXES`), `ESSENTIAL_GATE_SWITCHES`, `new` rows, promoted
  rows earning the full matrix (`_coarse_expand`), unevidenced keys (fail-open). Rotating `1/V15_PRIOR_ROTATE_EVERY` (25) slice
  per round re-tests pruned rows fully (priors self-correct). **PRIOR-REVIVED:** a positive naked delta on THIS sym_side runs
  the skipped yellows on the spot. Order inside `_row_static`: autopsy-inert → prior → possym/coarse → zero-formula.
- **TIERS (`progress["tier"]`):** potential = max(baseline gain, best earlier final gain of this sym_side). WINNER = potential ≥
  max(bh, `V15_TIER_WIN_GAIN` 10) with ≥10 trades, or a BIG MOVER (|bh| ≥ `V15_TIER_BIG_MOVER` 25 — the opportunity exists both
  ways); LOST_CAUSE = |bh| < `V15_TIER_BH_LOST` 2 and potential < `V15_TIER_LOST_GAIN` 5 (flat month: a couple of points at best);
  LOSER = potential ≤ 0 or below the trade floor; else MEDIOCRE. LOSER/LOST_CAUSE run STRICT (yellows only on USEFUL/SOMETIMES
  rows); LOST_CAUSE skips endgame + diagnose (lightning board). `V15_TIER_FORCE=<tier>` overrides.
- **FOCUS ROUND** (after the main pass + sacred final repass, fork pool still hot; budget `V15_FOCUS_S_WINNER` 900 s /
  `MEDIOCRE` 300 / `LOSER` 120 / `LOST_CAUSE` 0, inside the 90-min herd cap; `V15_FOCUS=0` disables): on the rows that moved
  THIS sym_side (promoted or positive naked, ranked by |movement|, `V15_FOCUS_TOP_K` 12): (1) the full yellow matrix where it
  was prior-skipped / coarse-deferred / inert, joint promote; (2) a finer numeric grid ×0.5…×2 around every promoted numeric
  value inside the template candidate span; (3) pairwise joint evals of the top non-running switches (first synergy wins).
  Same gates as the sacred repass (valid, trade floor, TIM/trades guards, `promotion_block_reason`), greedy vs the LATEST
  cumulative (§14.2), honest deltas (§19), resume-safe (`focus_done.result_key`), `progress["focus"]` + `FOCUS_ROUND` tab,
  done-keys `FOCUS!<n>:<label>` carry `cumulative_before/after` so the E-chain validator holds.
- Kill switches: `V15_PRIOR_POLICY=0` (full board), `V15_TIER_STRICT=0`, `V15_TIER_SKIP_POST=0`, `V15_FOCUS=0`.
- `V15_SLOW_CELL_S` default restored to 2.0 (the 2026-10-10 hotfix had been lost: 0.2 s cells were all going RED + 120 s retry).

**Cadence (2-server regime, `tools/v15_symside_ledger.py` + `tools/v15_fleet_scheduler.py`, tests
`tests/test_v15_sched_cadence.py`):** the ledger (S1 cron `V15_SYMSIDE_LEDGER` :07/:37 → `data/v15_symside_ledger.json` + `.md`)
reads the newest progress per sym_side (final gain, bh, trades, tim, dd, wr, verdict, pilot tier, 365D) and assigns
`sched_tier`: **W = DAILY** (top `V15_DAILY_CAP` 48 by potential, ≥10 trades, >0, ranked with half of B&H as the bar),
**M = 5 d**, **L = 10 d** (losers, lost causes, IMPOSSIBLE/NO_TRADES) → "the rest weekly on average". The scheduler's
`_gain_tiers(gains, ledger=…)` lets the ledger WIN over its gain-only rank (stale > 3 d → fail-open), caps W per pool
(`V15_TIER_W_MAX_POOL` 12 → ≤48 daily boards), keeps the 24 h W refresh, exempts W from the 4-day fresh skip, defers M/L by
`_defer_windows()` (5/10 d). The scheduler tick log prints `[sched] cadence ledger: …`.

**Venue phase + load division (USER 2026-10-12 PM: "stocks only until all tradeable stocks have passed, then crypto
(+ best stock performers adjustment every day); the S1 orchestrator divides the load between s1/s2/s5/s6"):**
S1 cron `V15_SCHED_VENUE_ONLY=auto` → `_venue_phase(chain_state)`: phase **stocks** while any tradeable stock sym_side is
still launchable (`need30`; running / waiting_base / deferred / fresh never hold the phase), else **any** (crypto flows).
W-tier stocks come back daily through the 24 h refresh → `need30` → the phase is stocks for that tick, they launch first,
crypto resumes the next tick. `_venue_allowed(sym, phase=)` at all three gate sites; tick log `venue_phase` + `[sched] venue
phase=…`. Hosts are admitted in `_host_order` (most free capacity first, then name) instead of config order (s1 = live host
no longer fills first). `tools/v15_director.py` leads the priority file with the ledger W syms (stocks first in the stocks
phase) and prints phase + DAILY list in `DIRECTOR_STATUS.md`. `tools/v15_daily_chain_s1.sh` step 4d regenerates the causal
profile + ledger daily (ancillary, never fails the chain) and step 6 pushes `data/causal/switch_causal_profile.json` to the
workers. Tests `tests/test_v15_sched_venue_phase.py`.

## 88. FIXED_STOP — NON-NEGOTIABLE FROZEN-AT-ENTRY STOP (USER 2026-10-12/13 — ang ALGOUSDT_LONG −46% with no stop anywhere)

**Rule (simple fact, not a switch):** at first entry, freeze LONG `dc_low_15m × 0.9975` (SHORT `dc_high_15m × 1.0025`); entry-price fallback when the channel is missing. Evaluated on EVERY vec bar, EVERY live bar, and the live backtest (same `vec_decisions/fixed_stop.py` core — zero config/QuickConfig/template surface, so §17 parity is vacuous and §14 greedy/delta mechanics are unchanged in shape). NO veto, NO min-hold, NO mode gate.

**Two legs:** (1) software leg exits the regular (maker-first) way at the frozen level, reason `FIXED_STOP`; (2) exchange leg presets a STOP_MARKET backstop 0.05% beyond (`×0.9995` LONG / `×1.0005` SHORT, `closePosition`, `MARK_PRICE`) so a dead system still exits taker. Crypto wires via Binance **algo-conditional** orders (`-4120` on `/fapi/v1/order`); stocks via Tradier GTC stop. Live loops self-heal (adopt/peer-dedupe, cancel-on-close, orphan sweep); `positions_truth` bypasses PLACE/CANCEL (no position change expected); parity gates treat `FIXED_STOP` as emergency.

**Baseline honesty (§15/§19):** the stop is a REAL ledger change — baselines and finals recomputed under it WILL differ from pre-stop sheets. That is the fix working, not drift. Never present pre-stop gains as current; the hold-up batch (`data/reports/fixed_stop_holdup/`) recomputes baseline+final per sym_side under the stop (HOLDS = recomputed final ≥ recomputed baseline). Vec models the software leg only (perfect execution); the 0.05% is a live-only placement offset.

**Freeze timing (parity note):** vec freezes at the exact entry bar. Live and the scalar backtest freeze lazily on the first `process_position`/fetch cycle after open (same bar live within seconds; next 1m step in the scalar engine since entry scan runs after `process_position` in the bar loop) — the breach check can never fire before a freeze. Residual vec↔scalar skew is ≤1 bar of 15m-DC drift (the 15-bar rolling min rarely moves bar-to-bar); live↔backtest agree exactly since both use the lazy path. No OPEN-path freeze was added: `execute_now` is the only order gate and the skew is negligible.

**Provenance:** engine md5 `ce6cf73c` (v12; `27489154` was a mid-task intermediate — fleet is ce6cf73c on Mac+S1+s2/s5/s6, verified 2026-10-13), tests `tests/test_fixed_stop.py` (14 green Mac+S1), A/B old-vs-new on S1 NPZs (FIXED_STOP binds with exact frozen math), live algo backstops verified on the exchange per account. Hold-up round 1 (`data/reports/fixed_stop_holdup/holdup_202610130144*.json`): 487 unique symsides, 296 evaluable on current NPZs → 96 HOLDS / 8 MARGINAL / 23 BROKEN among override stacks with ≥30 trades (75.6% hold, median edge retains ~72% of claimed); 187 unverifiable (174 missing NPZ + 13 stale, stock rebuild in flight). Round-2a interim (+5 resolved, same engine): merged 98 HOLDS / 8 / 23 (76.0%), 301 evaluable, 182 still missing; full round-2 waiter pending on S1.


## 89. ILLEGAL RE-STAMP + JIT NPZ DISPATCH + THROUGHPUT (USER 2026-10-13)

**(1) The illegal re-stamp — STOPPED.** `tools/v15_red_fixer.py` (S1 cron `*/10`) and `tools/red_cell_monitor_and_fixer.py`
re-opened PUBLISHED `SPREADSHEETS/V15_V16_CELL_BY_CELL/*_30d_matrix.xlsx` and re-saved them, giving OLD results (crypto
included) a fresh mtime with no recomputation. Measured 2026-10-13 00:45Z: **38 sheets re-stamped in 2 h while only 2
boards actually finished** (`lsof` caught the writer mid-save on `DOGEUSDC_SHORT_30d_matrix.xlsx.<pid>.tmp`). Beyond the
provenance lie it corrupted scheduling: `_fresh_computed_sides` (the 4-day skip) and the cadence ledger both read mtime,
so re-stamped boards looked freshly computed and were skipped. **Both crons are disabled** (`#RESTAMP_STOPPED_20261013#`,
crontab backup `backups/crontab_s1_before_stop_restamp_*`). **Law: a published sheet is immutable provenance — nothing but
a real recomputation may change its mtime.** Any tool that must touch one restores the original mtime (`os.utime`).
`tools/v15_symside_ledger.py` is now re-stamp-proof: it takes the board's own `result_done_utc` as the cadence clock
(`mtime_src`, `file_mtime` kept for audit) and only falls back to file mtime when the board recorded none.

**(2) JIT NPZ travels WITH the dispatch (USER: "the npz goes WITH THE PUSH of the sym_side, it gets calculated on s1
before dispatching").** `npz_prep()` in `tools/v15_fleet_scheduler.py` used to run `tools/v15_npz_prep.py` ON THE TARGET
host and only for stocks. It now runs on S1 (the scheduler host = the NPZ source) and **pushes** the refreshed NPZ to the
target with the tool's own md5 verify (`--push <ip>`; a local target needs no push), for **every venue**
(`V15_SCHED_NPZ_REFRESH` defaults to 1). `ok=False` holds the sym_side out of the dispatch instead of burning hours on a
stale tail. NOTE `tools/v15_npz_prep.py` is the Tradier/stocks gate (klines cache -> regen -> guard -> atomic install ->
push); crypto tails are owned by the Binance refresher, so for crypto the dispatcher verifies the tail age instead of
running the Tradier gate (which has no crypto cache and would falsely hold it).

**(3) Throughput — why the fleet produced ~1 board/hour on 4 hosts.** Measured on PEP_SHORT (3 h, zero output):
`V15_POSSYM_SAMPLING=1` in the launch env skipped **2412 of 3383 rows entirely**, leaving 568 hollow rows ->
`[COMPLETENESS-GATE] RULE#3 refusing publish` -> `[RUN-FAILED-KEPT-BEST]`. §86 had already established this
("one full fill publishes first try") and the flag had been set back to 1. **The launch env now carries
`V15_POSSYM_SAMPLING=0`** — the §87 prior policy does the pruning and its skips are SETTLED (never hollow), so speed no
longer depends on sampling. Post-stages are capped in the launch env (`V15_DIAG_REPAIR_S=300`, `V15_ENDGAME_S=240`,
`V15_FOCUS_S_WINNER=300`, `V15_FOCUS_S_MEDIOCRE=150`) after DIAG spent 816 s / 4313 evals on PEP_SHORT and changed
nothing. Queue depth: cadence deferral is env-tuned for a push (`V15_TIER_DEFER_M_D=1 V15_TIER_DEFER_L_D=2`) and the
non-winner quota is now tunable (`V15_NONW_QUOTA_DIV`, 1 = no quota) so a free paid host is never starved while
winners still sort first. Measured after the fix: fills run 15-30 min (EGLDUSDT_SHORT 1200 rows / 345 s).

**(4) The fleet scheduler is the ONLY organizer (USER 2026-10-13).** It owns dispatch + JIT NPZ + the chain; the daily
chain (§87 step 4d) owns avg-delta, template rewrites, the causal profile and the ledger. A second cron that merely
"keeps N tests running" is forbidden — one was added during this push and removed the same hour. Admission no longer
drops a candidate silently: `skipped_no_actions` and `skipped_pair_gate` join `skipped_unready` / `skipped_venue_only` /
`skipped_quota` in the tick log, so an idling fleet always names its own blocker.
`tools/v15_push_dispatch.sh` + `tools/v15_push_topup.sh` remain as MANUAL operator tools (NPZ-verified push, OOM/load
throttled from `fleet_hosts_final.json`); they are never crons.

## 90. TEMPLATE SUPREMACY + LIVE CHAIN-ROUTE (USER 2026-10-13 — "config decides NOTHING")

**Doctrine (absolute):** `TEMPLATE_{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx` bold is the ONLY source of defaults. It is written ONLY by the S1 daily chain (12:15Z cron) (avg-delta rebuild → template writer: pos_sym + avg_delta per cat/side → new bolds, demote rest). It feeds, in order: `per_sym_settings` store (SQL kv_json primary, JSON backup — verified identical, 0 diffs) → per-sym overrides → `config.py` / `config_tradier.py` / `QuickConfig` literals (last-resort fallbacks, never the decision). No function executes a value not calculated as a positive delta in the templates.

**Reads:** live resolves EVERY store-carried key via the chain — per-sym override > cat_side default > global literal (`ez_manage._psym_get`, `tradier_manage._cfg/_cfg_auto`, `ez_positions_service._svc_psym_get`, vec `QuickConfig.apply_cat_side_defaults→apply_per_sym_store`). Direct `getattr(config…)` reads of diverged keys were live bugs (live ran the literal, vec ran TEMPLATE): fixed 2026-10-13 (15 ez sites + KG gate, parabolic +7 tradier sites, 2 service sites). Regression gate: `tests/test_per_sym_chain_parity.py` (7 green Mac, 6+S1) — fails on ANY new direct read of a diverged key (4 documented allowlist entries: dead code, fallback default, tick-global cap, signature-compat default).

**Builder trust (NOT damage — verified working as designed):** `build_cat_side_defaults_4` transcribes a divergent bold ONLY with a promotion record (`per_sym_store.get_cat_side_promotions`, 89/106/110/86 per side); unpromoted divergent bolds (73) are held at config values as placeholders — that IS the no-LIES rule. The 2 promoted-but-untranscribed keys are P0-FOSSIL pins (§64, entry-killers, user-sanctioned). `STOCH_CROSS_ENTRY_TRADIER` bold=True is unpromoted → correctly held False (no-k-gate mandate + builder agree); the sheet bold is stale, behavior is right. KG gates are TEMPLATE-proven on all 4 sides (crypto False, stocks True) — the "config violation" claim was wrong; only the per-sym routing was fixed (ETHUSDC_SHORT E921=True was ignored at one call site).

**Live behavior changes from the route (TEMPLATE values now enforced):** MOM5_LONG_THRESHOLD −1.0→−0.5; FG_SIZING CS off→on; ATR_ADAPTIVE_STOP_TF CS 1h→4h / SS 1h→OFF; EMA_DIST_SIZING_MULT 2.0→4.0; PARABOLIC SS on→off. (MAX_ORDER_VALUE CL and MAX_AUGMENTS SHORTS were INITIALLY routed to stale store values 90/0 — user-caught same turn: 90 was a stale 10-09 promotion predating the 10-11 mandate 180=4×45 gainer headroom, and 0 contradicted the 10-10 no-cap mandate via a "0.5" guard bypass + vec-inert-knob noise. Both voided as phantoms, bolds restored to 180/999999 in all 8 template files, stores rebuilt, 104 per-sym poison keys stripped Mac+S1. The routing stands — it now serves the corrected values, identical to pre-route live behavior for these two keys.) Files take effect at next live-loop restart (loops NOT restarted by the fixing agent). NOTE: the store rebuild itself took effect WITHOUT restart (live hot-reloads the store): the pre-existing chained short AUGMENT_AT_LOSS gate (`ez_manage` ~41482) was blocked by the phantom 0 since 10-12 and is unblocked now.

**Mandate hardening (same turn):** pilot `MAXAUG_NEVER_ZERO` guard extended to fractional <1 (`_maxaug_is_zero`); daily writer refuses MAX_AUGMENTS<1 promotions (`refused_mandate` ledger); `v15_persym_golive` strips per-sym MAX_AUGMENTS<1. Tests `tests/test_maxaug_never_zero.py` (7 green Mac incl. 94-row grey-assert; S1 4/7 in-suite, remainder proven by byte-identical template hashes + direct store queries): predicate, writer refusal, golive source, no-poison DB scan, grey-assert, store MAX_AUG≥1, MAX_ORDER≥4×START structural invariant.

**Fishnet doctrine (USER 2026-10-13 — "MAX_AUGMENTS is only a catch... these are just fishnets, nothing that moves deltas"):** safety catches are NOT TEMPLATE switches — no rows calculated, no deltas, no promotions, ever. `MAX_AUGMENTS_PER_POSITION` (vec never reads it — its ±deltas were non-causal noise) + `MAX_ORDER_VALUE` (vec reads it only at sim scale, §17.3-excluded — live-scale deltas non-transferable; value derived 4×START, never tuned) greyed out (`FFBFBFBF`, 94 rows) in all 8 template files → skipped eternally by pilot cells, bold reader, and daily writer. Grey keys fall to config fill in the store (or live!=quick conflict-omit with identical literal fallback — same values; chain-parity test mirrors the exact contract). 4th void: CRYPTO_SHORT MAX_ORDER=90 record (divergent from bold 180). Per-sym `MAX_ORDER_VALUE=0.0` poison stripped (FCN both hosts); golive extended to refuse ≤0 caps. Non-zero per-sym caps (Mac 66 syms) LEFT for user verdict — may encode deliberate risk sizing. All other cap-like keys (MEN/FIN/BTC variants, MAX_POSITION_SIZE*, GOLDEN_PULLBACK, BROKER_SYNC) verified rowless already. Grey-assert test pins all 94 rows (14/14 green Mac).

## 91. NPZ IS JIT-ONLY + SETTINGS HISTORY DB IS ORCHESTRATOR-OWNED (USER 2026-10-13)

**(1) NPZ LAW — rebuild ONLY within ~15 min of a server loading it, otherwise NEVER.** "We need backtest results; NPZs
are only good for that and 15 min later they are obsolete again." Blanket/background NPZ rebuilding is forbidden. What
was found and STOPPED on s1 (it was pinning the host at load 46 while boards starved):
- 3 orphaned `tools/npz_freshness_gate.py --refresh` drivers (2.9-3.1 h old, in NO cron) and the **34**
  `backtest_v8_precompute.py` workers they had spawned;
- the perpetual `tools/npz_bar_refresh.py --loop --mode both --workers 8` cron (`*/5` + its `@reboot` twin) and
  `V15_AF_FORCE_REFRESH` (`*/10`) — commented `#NPZ_JIT_ONLY_20261013#` (crontab backup
  `backups/crontab_s1_before_npz_jit_only_*`);
- **`binance-npz-refresh.service`** — a systemd unit that silently resurrected the loop after every kill
  (`systemctl stop` + `disable`; this is why earlier kills "didn't stick").
Result: s1 load 46 -> 10, +52 GB free, and s1 finally computes boards. The ONLY rebuild path left is the dispatch
path: `npz_prep()` runs on s1, rebuilds that one symbol, and PUSHES it md5-verified with the job (§89.2).
- **Single-flight:** every rebuild is wrapped in `flock /tmp/v15_npz_prep.lock` (`V15_NPZ_LOCK_WAIT_S`, 600 s). One
  rebuild at a time, fleet-wide — concurrent dispatches used to stampede s1 with 6 precompute workers each.
- **Prefetch is completion-gated:** the scheduler pre-builds the next job's NPZ only for a host with NO free slot whose
  oldest running board is at least `V15_SCHED_NPZ_PREFETCH_MIN_AGE_MIN` (15) minutes old — i.e. "just before the server
  finishes the previous calc". Budget `V15_SCHED_NPZ_PREFETCH_BUDGET` (4), skipped entirely while s1 is above
  `V15_SCHED_NPZ_PREFETCH_MAXLOAD` (1.6x cores) so NPZ work can never eat the compute s1 needs for its own boards.
- **Venue-aware staleness:** the wall-clock tail gate applies to CRYPTO only. A STOCK tail is hours behind whenever the
  US market is closed, which is normal; stock freshness is judged by `tools/v15_npz_prep.py` against the Tradier klines
  cache. Gating stocks on wall clock falsely held every overnight stock board (ZCSH 142 min, AXON 262 min, MSTR 157 min).
- **Every server computes SPREADSHEETS results.** s1 is NOT a live trader (it trades only if the MacBook dies) and must
  keep ~95% of itself free for its own boards; it generates NPZs per job, nothing else.

**(2) DISK — the eval memo cache was strangling the fleet.** `data/eval_cache_v1.db` had grown to **55 GB per host**
(+5.6 GB WAL): s5 99% full (the scheduler's own disk guard was refusing its launches) and s2, a survivor, 98%. It is a
regenerable cross-run memo cache with a documented fail-open path and kill switch (`V15_EVAL_CACHE=0`), so it was cleared
fleet-wide and capped: cron `V15_EVALCACHE_CAP` (every 20 min, every host) drops it past 12 GB and logs to
`~/logs/evalcache_cap.log`. Note: deleting it frees space only as the running boards release the inode.

**(3) SETTINGS HISTORY — the orchestrator writes it, on every publish.** `per_sym_history` in
`data/hourly_reconfig/per_sym_store.db` is the day-by-day trail of each sym_side's calculated settings. It was only
written at the daily Mac go-live, so 160 freshly calculated sets were missing (DB 6-11 h stale while 15 boards published
in 90 min). NEW `tools/v15_settings_history.py`: scans every host's round progress JSONs, and for each board with a
top-level numeric `final_gain` and no `needs_redo` appends ONE row (same columns `per_sym_store.upsert()` writes) with
the full `overrides_json` (~27 KB), gain/baseline/delta/bh/trades/tim/dd, `defaults_round`, NPZ id and
`meta_json.source=fleet_orchestrator_publish`. Idempotent per (sym_side, gain, day); refuses to write unless
`PRAGMA integrity_check` is `ok`. **HISTORY ONLY — it never touches `per_sym_active`:** a fresh 30D set is evidence, not
a promotion, and live per-sym settings stay behind the go-live gate (365D verification, artefact stripping, blocklists).
The fleet scheduler fires it detached (`flock /tmp/v15_settings_history.lock`) on any tick that sees a sym_side publish
since the last tick (`st["hist_seen"]`, `log["settings_history"]`) — no extra cron, the orchestrator owns it.

## 92. APPLY LIVE BEFORE 365D + 2-LOSS ESCALATION (USER 2026-10-13, AUTHORITATIVE — supersedes every "365D before live" gate)

**The rule, verbatim intent:** "We now apply live BEFORE 365D and backtest_v12_live verification, which does not make
them obsolete — they need to run also, but meanwhile the newest settings trade immediately, and as soon as 2 consecutive
losing trades occur with the new settings, the 365D gets prioritized and optimized until new settings replace the
current ones."

**(1) Live application no longer waits for 365D.** `tools/v15_persym_golive.py`: `gate_365d()` still MEASURES the verdict
and records it in the row (`gate_365d`, plus `applied_before_365d: true`), but it never refuses. Restore the old hard
gate with `V15_GOLIVE_REQUIRE_365D=1`. The live entry gate in `ez_manage._confirm_365d_allows`
(`CONFIRM_365D_GATE_ENABLED`) already defaults OFF since the 2026-10-02 user order, so live opens are not gated either —
consistent with this rule, nothing to change there.
**Every INTEGRITY refusal stays** — they protect against junk, not against being unverified: the go-live blocklist
(`data/golive_blocklist.json`), `ARTEFACT_KEYS` + `ABLATION_*` stripping (§77), zero-trade sets, engine-rejected sets,
secret/type gates, `_NEG_BLOCK`. A set that the engine cannot reproduce is still refused.

**(2) Verification is NOT obsolete — it still runs.** The fleet chain keeps `need365` -> 365D -> repair/GS exactly as
before. What changed is only the ORDER relative to live application.

**(3) 2 consecutive losing trades -> prioritized 365D.** NEW `tools/v15_loss_streak_watch.py` (Mac cron
`V15_LOSS_STREAK`, every 5 min, `--push s1-pub`; tests `tests/test_v15_loss_streak.py` 8/8):
- the anchor is **when the current settings went live** — `per_sym_active.updated_at` in `per_sym_store.db`;
- trades come from the live ledgers `data/per_trade_returns/{acct}_{YYYYMMDD}.jsonl`, `net = pnl_pct - fees_pct`
  (identical to `v15_causal_learner.ingest_live`, so a fee-eaten "win" counts as the loss it is);
- only trades NEWER than the anchor count, so "with the new settings" is exact and **"until new settings replace the
  current ones" is automatic**: a new apply moves the anchor and the counter restarts at zero;
- the TRAILING run is what matters — any win resets it (`trailing_loss_streak`, pure + unit-tested, order-independent);
- `streak >= V15_LOSS_STREAK_N` (2) -> `data/v15_365_priority.json` with the offending trades, their net %, exit reason
  and the `applied_at`/`winning_tag` anchor.
- `tools/v15_director.py` puts those symbols **FIRST** in `~/v15_priority_syms.txt` (ahead of the ledger W/DAILY tier)
  and reports them under "2-LOSS ESCALATION" in `DIRECTOR_STATUS.md/.json`; the scheduler reads that file as
  `V15_SCHED_PRIORITY_SYMS`, so their 365D verification/optimization takes the next free slots.
- The watcher is strictly read-only with respect to trading: it never blocks, closes or sizes anything — it only ranks
  verification work. First run (2026-10-13 03:58Z): 145 sym_sides with live trades, 86 traded since their apply,
  **12 flagged** (WLDUSDC_LONG/SHORT, SANDUSDT_LONG, QTUMUSDT_LONG, THETAUSDT_LONG, AIAUSDT_LONG, BNBUSDC_LONG,
  IOTXUSDT_LONG, KSMUSDT_LONG/SHORT, SOLUSDC_LONG, XLMUSDT_SHORT).

**(4) OPEN ITEM — the cadence of live application.** Go-live still runs on the Mac daily chain (step 5b). "Trade
immediately" is satisfied in the sense that 365D no longer gates it, but a set calculated at 03:00Z still waits for the
next go-live slot. Making it apply per publish means applying unverified settings to real money many times a day; that
is a live-money cadence decision for the operator, not a silent change. To enable, run
`tools/v15_persym_golive.py --all-finished` on a tighter Mac cron (it is idempotent and diff-only).
## 93. ASYNC JIT DISPATCH + RESUME-FROM-CELL (2026-10-13)

**Incident (measured).** Zero pilots fleet-wide 13:44Z–16:38Z. The S1 cron tick (`timeout 110`) ran `npz_prep()` synchronously;
a stock NPZ build is ~20 single-core minutes once RTH is open (stock forming-values pass in `backtest_v8_precompute`), so every
tick died before launching and never saved state. The async patch deployed at 16:13Z was LOST again by 16:27Z (cause not found —
the Mac + S1 file reverted to the pre-async bytes); it was re-applied from the session record. On top of that: `results_watchdog`
killed every new S1 pilot each 10 min ("progress silent 224m" = age of the newest file from before the idle), `cpu_target_pct`
was 50 in `fleet_hosts_final.json` (s1 at 69% busy admitted nothing), and all crypto 30D pilots refuse at start-up on a
PARITY-GATE hard disparity (`DC_EDGE_SIZING_MAX_MULT` bold 6 / global 3, `EMA_BLANKET_FILTER_FILTER_TF` OFF vs quick 15m —
locked engine/config files, needs `unlock`), each refusal burning a resume slice.

**Dispatch (tick returns in ~30 s).**
- `tools/v15_fleet_scheduler.py` `_dispatch_async()` hands NPZ prep + md5-verified push + pilot launch to the detached
  `tools/v15_dispatch_job.py`. Kill switch `V15_SCHED_ASYNC_DISPATCH=0` (old synchronous path).
- In-flight dispatches HOLD their slot (`st["dispatch_q"]` key→ts + `st["dispatch_host"]` sym→host, held status `dispatching`);
  the grace (`V15_DISPATCH_GRACE_S` 2400) is released as soon as the job process is gone (`_prune_finished_dispatches`).
- Job: one live job per tag (flock dedup), per-symbol single flight, `V15_NPZ_PREP_SLOTS` (default 8) concurrent builds instead
  of one global lock, `V15_NPZ_PREP_TIMEOUT_S` 2400. Prep `ok=False` = skip; `ok=None` (crash/timeout/guard-blocked) = launch
  with what the host has. Log `/tmp/v15_dispatch.log`.
- `tools/v15_npz_prep.py`: builds a missing NPZ (RIO "no live NPZ" was refused forever; sanity: ≥500 bars, ≥30d span); pushes
  S1's copy to the target whenever usable — also when the tail was already current (it used to exit before the push) — and
  verifies md5 before the `mv`.
- Worker "npz missing" no longer idles a fresh pair: the job builds/pushes it (`npz_missing_jit` in the tick log).
- Speculative NPZ prefetch stays OFF (`V15_SCHED_NPZ_PREFETCH=1` to enable). The pilot's own start-up refresh hook
  (`tools.v15_npz_ensure`, `main()` in v15_pilot.py) is opt-in `V15_PILOT_NPZ_ENSURE=1` and never runs on a resume (the module
  was never deployed, so it was inert; it is now safe if it ever is).
- Parity preflight: `_parity_gate_ok()` runs `switch_parity.startup_gate` once per cat_side per tick; a refusing cat_side gets
  no 30D launch (`skipped_parity_gate` names the disparity). `V15_SCHED_PARITY_PREFLIGHT=0` disables.
- Capacity: `cpu_target_pct` floor 90 via `V15_SCHED_CPU_TARGET_MIN` (survives the hosts-file self-heal); slot cap is
  mem-derived (`max(max_pairs, used + proj_mem // est_pair)`, ≤12; `V15_SCHED_MEM_CAP=0` disables).

**Resume from the cell it left off.**
- RULE#4 freeze registry `data/npz_frozen_syms.json` (`tools/v15_npz_freeze.py`), written every tick: symbols with an unfinished
  board (progress without final_gain, or needs_redo) or a running pilot. Honoured by `v15_npz_prep` (no rebuild, no push →
  `action=frozen`), `tools/sync_indicators.sh` (excluded from the S1→worker mirror), `npz_fundoi/install_fundoi_npz.py`,
  `npz_sentiment_force.py`. Why: 44 boards (up to 3546 done rows) were archived by NPZ-CHANGED-REFILL in the 24h before the fix,
  every one because the NPZ tail moved between two slices. `V15_NPZ_FREEZE=0` disables.
- Order: `_place_order` ranks operator pins (0) → unfinished boards incl. needs_redo (1) → fresh pairs (2); every unfinished
  board a host owns is admitted before any fresh pair in the same tick (`own_unfinished` per host in the tick log).
- "Done" = the NEWEST progress (any host) is final; an older final elsewhere no longer hides a newer half-filled board.
- Slice budget (20) restarts when the board's progress json advanced since the previous launch (`_slice_progress_reset`,
  probe field `started_mt`) — a board that writes rows is never exhausted; crash loops still stop.
- Never kill a pilot that writes rows: reaper `hardcap` additionally needs ≥20 min idle; `tools/v15_results_watchdog.sh`
  stuck-side needs pilot age ≥ SYM_STALL_MIN AND progress/log/delta-log all silent; host stall needs EVERY pilot older than
  PROGRESS_STALL_MIN.
- Operator queue `data/v15_sched_pinned_sides.json` {"sides", "hosts"}: bypass deferral/fresh-skip/base-wait/venue phase/quota/
  allowlist, own budget `_PIN_SLICE_CAP` 40, `pin_status` per side in every tick log.
- Drain `data/fleet_drain_hosts.json` {"hosts"} or `V15_SCHED_DRAIN_HOSTS` (outside the self-healed hosts file): no new launches,
  running pilots finish, boards re-home. Currently EMPTY (USER 17:13Z: s5/s6 run the full round).
- Inventory: `tools/v15_unfinished_inventory.py` → `data/v15_unfinished_<date>.json` (newest progress per side across every
  `~/v15_run*/progress` + `~/v15_import_*/progress` on S1 and s2; xlsx mtimes ignored). Boards from an OLDER campaign whose NPZ no
  longer exists cannot resume under RULE#4 — they relaunch as fresh boards.

Tests: `tests/test_v15_sched_resume.py` (+ all `tests/test_v15_sched*.py`, `tests/test_sched_done_marks.py`).

## 94. SELF-IMPROVEMENT LOOP CLOSED (2026-10-13)

**USER:** "the self improving part is not working correctly so you will need to add all code that it should have generated in
the self improvement rounds and all the documentation". Audit (2026-10-13 ~16:15Z): every learner had a writer but no reader,
or a reader of the wrong field, or no schedule. Each component below is now writer → reader → consumer → proof.

| Component | Writer | Reader / consumer | Proof (2026-10-13) |
|---|---|---|---|
| **Template rows** (§71) | `data/template_row_queue.json` (60 entries, 8 explicit exclusions) + inbox `data/template_row_queue_inbox/` (`v15_switch_add.py --enqueue`, Mac-apply 2c `rescue-lost`) | S1 chain **2a**: `v15_template_row_intake.py merge-inbox` → `v15_switch_add.py --queue … --apply` (the ONE row writer) → step 6 push → Mac pull | 266 switch installs on S1: FINAL_NORM CL 34 sw/94 rows, CS 34/94, SL 38/105, SS 38/106; main CL 26/74, CS 26/74, SL 35/97, SS 35/98. Re-run plans 0 (304 present). md5-identical S1/s2/s5/s6/Mac. `v15_pilot.scan_template_cands` lists all 8 ENTRY_LH_TRIGGER candidates on every cat_side. Replay of the 10-09 loss: `rescue-lost` flags exactly the 3 LH switches |
| **Invention loop** (§84) | S1 chain **4e** `v15_invention_loop.py mine --write` (real autopsy fields, distinct-sym score, dedup) | `promote-check` (TESTED→PROMOTED/RETIRED), `rows-check`, work orders `data/invention/orders/` | 300 obs, 164 autopsy rules, 175 added; 2nd run added=0; INV-0001 ROWS_INSTALLED |
| **Causal rescue** (§85) | `v15_causal_learner.py export` → `rescue` list | **8c weekly**: `v15_causal_rescue.py run` (idlest host, nice 15, 2 workers) → `ingest-outcomes` → `fix_outcomes`; `cap --apply` | fix_outcomes 18 → 659; DB 4.99 → 2.10 GB |
| **Autopsy-first bases** (§78) | NEW `tools/v15_autopsy_jit.py`, S1 cron `V15_AF_JIT` */15 (flock, nice 15, ≤4 sides, load1 < 0.75×cores) | scheduler `V15_SCHED_REQUIRE_BASE` / `V15_START_OVERRIDES`; `V15_AF_PUSH` → workers | picks only pending sides (scheduler snapshot × director priority) lacking an accepted base; first dry-run: 12 candidates, 39 without NPZ, deferred on load. Sides with no NPZ get ONE build through the dispatch builder (`flock /tmp/v15_npz_prep.lock v15_npz_prep.py`) right before the autopsy loads it — §91-compliant (load within minutes), ≤2 per run |
| **Missed-trend round** (§80/§81) | NEW `tools/v15_missed_trend_round.py`, chain **8a** (≤2 boards/day) | real `v15_missed_trend.py screen` on BEST_EFFORT & TIM<20 boards; verified base (gain up AND TIM not down) installed as `~/v15_autopsy_first/{SS}_autopsy_base.json` (prev kept) for the side's next cadence round; published sheets untouched unless `V15_MT_REQUEUE=1` | first pick on S1: ETHUSDC_SHORT (6 such boards in the ledger) |
| **Priority evidence** (pilot `none_priority_evidence`) | `v15_cat_avg_matrix.py`, chain **8b** daily (stale since 10-07) | pushed md5-verified to the workers in 8b and with tomorrow's step 6 | — |
| **Switch flip study** (§87) | `v15_switch_flip_study.py`, chain **8c** weekly on the idlest host | `data/causal/switch_flip_study.json` pushed in step 6 | — |

**Chain hardening (`tools/v15_daily_chain_s1.sh`):** `trap on_exit EXIT` writes `<date>.FAILED.json` whenever a run ends without
DONE (TERM/INT/HUP trapped; bash runs the trap when the current step returns); `<date>.RUNNING` (pid + start) — a run killed by
SIGKILL/OOM/reboot is converted to FAILED at the next start, and the Mac apply stops waiting when the RUNNING pid is dead.
`V15_DAILY_CHAIN=1` is exported; `tools/oom_guard.sh` never STOPs (and resumes) a process carrying it. Fact: the guard's STOP patterns
(`baseline_backtest`, `sweep_every`) never matched chain processes, so the 4 silent deaths (10-10 ×2, 10-12, 10-13 12:51Z right
after the 4c ingest) are NOT explained by the guard; the cause stays unproven — from now on every death leaves a FAILED stamp.
Steps 8a–8c run AFTER the DONE stamp and can never change or fail the day.

**Not closed here (owned by other lanes or locked):** endgame ADOPT "not worse than entry" guard + shared endgame knowledge
(`v15_pilot.py`, audit B1/B7); engine work orders WO-20261013-EXIT_HOLD_WT_VEL, -EXIT_HOLD_STRETCH, -STOCKS_OPENING_BUFFER_ENTRY_ENABLED,
-LIST_SWITCH_ENCODING (need `unlock` of v12_quick_engine/ez_manage/ez_positions_quick/tradier_manage/config/config_tradier).
Tests: `tests/test_v15_template_row_queue.py`, `tests/test_v15_selfimprove_loop.py`, `tests/test_v15_invention_loop.py`,
`tests/test_v15_causal_rescue.py`, `tests/test_v15_switch_add.py`, `tests/test_causal_learner.py`.

## 95. V17 ROUNDS — FILTER PROBE → ABBREVIATED WORST_FIRST → PINPOINT (2026-10-13)

**User goal (verbatim):** "speed up the v15_pilot.py we need 4-12 sym_sides to be completely resolved per hour per server
24/7/365 where the worst_first round needs to be abbreviated and the pinpoint round needs to be enlarged … (by filter ablation
first then testing all the entry and exit paths that did not have a chance in previous rounds because filters had disabled
them)". Ablation route clarified by the user the same day: strip a category's filters (a discovery base, worse by design),
find positive entry/augment/reentry/exit switches on it, pinpoint the filter that kills each one, exempt the switch from that
filter, put every filter back and adopt only above the previous best.

**Evidence that shaped it (run32, 198 boards, phase report):** main pass 68% of wall / 0.43 pp per 1k evals; focus ~3.7 pp per
1k evals but <1 min per board; 112 REDOs from RULE#3 hollow rows; 197/245 filter switches never promoted yet 38% of yellow
evals; endgame strip-all −15.8 pp median (blanket strip refuted); 286 naked≤0 rows won via a filter change; 137/227 sacred
promotions were rows ≤0 earlier.

**Board (`v15_pilot.py`, `_spec_fill_workbook`, kill switch `V15_V17_ROUNDS=0` = pre-V17 board exactly; default OFF since the A/B below — opt in with `V15_V17_ROUNDS=1`):**
- **P0** baseline unchanged. A legacy board resumed mid-fill keeps pre-V17 semantics (no mixed chain); `progress["v17"]["on"]`.
- **P1 FILTER PROBE** (`_v17_filter_probe`): every option of the top-K (`V15_V17_PROBE_K` 15) filter switches of the cat_side
  (ranked from `data/causal/switch_causal_profile.json` + `data/causal/v17_priors.json` (larger n wins) + fleet endgame
  knowledge `tools/v15_endgame_knowledge_merge.load_ev`, cold-start list `V17_FILTER_FALLBACK`), both directions, one parallel
  batch vs the start set; greedy: best positive promoted (its own eval = the fresh exact eval), remaining positives
  re-evaluated vs the new chain. Re-run as `P3_PROBE_<i>` at the end of every pinpoint iteration.
- **P2 ABBREVIATED WORST_FIRST**: naked on every live row (§77); tab order = measured lift per eval-second of the cat_side
  (`v17_tab_order`, persisted in `progress["v17"]["tab_order"]`); NO hustle eval — F = this row's result vs the ORIGINAL
  baseline, the §56 wording ("the same row result vs the ORIGINAL baseline"), so a row costs 1 eval instead of 2.
  **Yellow frequency (USER rule):** "dont switch off yellow cells, just reduce their frequency of calculation from every time
  (highest pos_sym values) to once in 200 (0 pos_sym)": every yellow cell (incl. prior-skipped / inert) is evaluated with
  `p = max(1/200, min(1, pos_sym / pos_top))` (`v17_cell_p`; pos_top = 97th percentile pos_sym of the cat_side's evidenced
  cells, `V15_V17_P_TOP_PCT`; unevidenced n<`V15_PRIOR_MIN_N` → p=1), deterministic draw `md5(cat_side|tab|switch=cand|cell|round)`
  (`v17_cell_keep`) so the rotation covers every cell. Skipped cells = coarse-deferred = SETTLED (blank, `sampled_skip_p` on the
  row, never hollow, no RULE#3/REDO, no refill). A POSITIVE NAKED row revives its yellows fully (COARSE-INLINE); a row that
  only moved through a kept yellow inlines the top `V15_V17_INLINE_CAP` (32) prior-ranked cells. `[YELLOW-SAMPLE]` +
  `progress["v17"]["yellow_sample"]`. Kill switch `V15_POSSYM_P_SAMPLING=0` (then cap `V15_V17_P2_YELLOWS` 8).
  `V15_V17_P2_NAKED_ONLY=1` = pure naked pass with every yellow `DEFERRED_V17` (measured worse, see below).
  Tab-boundary sacred repass KEPT (`V15_V17_SACRED_REPASS`): canary RLC old arm got 21.1 pp from it (GOLDEN_RULE_BASE_USD=0.0).
- **P3 PINPOINT** (`_v17_pinpoint`, budget `V15_V17_P3_S_<TIER>` 600/300/120/30 s capped so the board ends inside
  `V15_V17_BOARD_S` 900 s with `V15_V17_TAIL_S` 210 s ahead), iterations until the budget is spent or an iteration adds nothing:
  FOCUS (yellows of the moved rows submitted to the pool as one batch, numeric grid ×0.5..×2, pairs) → **ABLATION ROUTE**
  per category entry/reentry/augment/exit (`ABL_STRIP` → `ABL_DISCOVER` (≤400 path switches by EV, every AUGMENT_* in augment) →
  `ABL_RESTORE1` (one filter restored at a time; only filters that bind on the strip base) + `ABL_ATTR` → `v17_killers`
  (restoring the filter removes >50% of the discovered delta) → exemption from `data/v17_filter_exemptions.json`
  (engine key `FILTER_EXEMPTIONS`, union-merged with the chain's value, exact engine dict form) or proxy = killer filter OFF
  (`[ABLATION-NO-EXEMPT]`) → `ABL_RESTORE` / `ABL_EXEMPT` / `ABL_PROXY` on the full previous-best chain, greedy) → moved-chain
  RETEST of every non-promoted ENTRY/REENTRY/AUGMENT/EXIT row + sacred families whose chain moved since their eval, then
  near-misses × ever-promoted filters → filter re-probe. Then **TIM-FIX** (a positive-delta candidate whose fresh eval lands
  TIM inside [20,80]; prevents the compliance-repair full REDO) and **ESSENTIAL proof** (own-row then cross-row deferred
  cells of every FAIL protection filter until one moves). Same gates everywhere: valid, trade floor, TIM/trades guards,
  `promotion_block_reason`, MAXAUG/ABLATION never.
- **P4** final_filter_recheck only on filters touched in P1/P3 (`V15_V17_FFR_TOUCHED`) and under the TIM/trades guards
  (canary RLC: unguarded FFR pushed TIM to 9.3 → compliance REDO); `P4_TIMFIX`; endgame/diagnose budgets capped by env at call
  time (`V15_V17_POST_S` 120 total).
- Done keys: `V17_PROBE!n:F=opt`, `V17_RETEST!n:…`, `V17_TIMFIX!n:…`, `ABL!<cat>:<switch>=<cand>[+exempt|+proxy]`,
  `FOCUS!<iter>.<n>:…` — all carry cumulative_before/after (E-chain validator holds), `complete: True`, plus tab/row/switch.
  Resume: phase markers in `progress["v17"]["phases"]` (`P1_PROBE`, `P2_WORST_FIRST`, `P3_*_<i>`, `P4_*`); rows are never wiped.
- Logs: one `[V17-PHASE] name evals= wall= lift=` line per phase, `[V17-PROMOTE]`, `V17_ROUNDS` sheet (phases, promotions,
  ablation records: strip gain, discovery delta, killers, exemption used, final delta, adopted).

**Measured 2026-10-13 (same frozen NPZ md5 on every host, identical start set; isolated V15_PROGRESS_DIR/V15_OUT_DIR,
SWITCH_PARITY_REGISTER=0; crypto canaries needed SWITCH_PARITY_GATE=off because the fleet parity gate refuses every crypto
board: DC_EDGE_SIZING_MAX_MULT bold 6.0 vs config 3.0, EMA_BLANKET_FILTER_FILTER_TF quick 15m vs OFF — engine/config lane):**
see `data/reports/v17_ab_*.md` for the formal A/B. Canary history: naked-only P2 (variant B) was fast (10.8–17.3 min) but lost
most of the joint lift (RLC 30.5 vs old 66.3 after focus) → yellows restored with the frequency rule; first variant with
yellows + ablation: COIN_LONG 12.4 min, 19.69 published, 365D PASS, no REDO (old pilot same start: 20.9 min, then 365D REDO);
v3: COIN_LONG 14.8 min 29.86 (old 20.x), VALE_SHORT 12.3 min 21.18 (old 26.x, run32 21.00), RLCUSDT_LONG 26.2 min 25.5
(old ≥66.3: crypto P2 with many evidenced cells is the open gap). Fleet s2 after the V17 deploy: 9 boards in 30 min
(10.5–19 min each) vs 20–35 min per board before.

**Formal A/B 2026-10-13 18:33–19:11Z (`data/reports/v17_ab_202610131911.md/.json`, `tools/v17_ab_report.py`; frozen pilot
old=bab5e0ce vs new=V17, same frozen NPZ md5 / templates / start sets / priors / exemptions on S1 and s2, arms swapped across
hosts; S1 ran at load ~28-31 on 16 cores (fleet + live), s2 ~12):** final gain new vs old — COIN_LONG 29.86 vs 20.56 (8.8 vs 33.4
min), RLCUSDT_LONG 25.53 vs 64.29 (old still running at report time), UNIUSDC_SHORT 18.62 vs 29.92, VALE_SHORT 21.18 vs 25.43.
V17 wins 1 of 4 on gain → **per the user acceptance rule V17 is default OFF** (`V15_V17_ROUNDS` default "0", deployed s1+s2
md5 d14a97a7); opt in per launch with `V15_V17_ROUNDS=1`. Root cause measured in the phase lines: on 3 of 4 boards P2 used
868–1582 s, so the 900 s board budget left P3 (focus/ablation/retest) no time at all — the abbreviated pass is not yet short
enough on crypto/slow stocks and the pinpoint round never ran there. Next: guarantee P3 a minimum budget independent of P2,
shrink P2 further (sampling pos_top, inline revives), then re-run this A/B.

## 96. TYPED SWITCH VALUES — ONE VALIDATOR, FAIL-CLOSED (2026-10-13)

**Trigger (USER):** "'0.0' on a true/false switch — that is TERRIBLE but an easy fix, just make sure it can never happen again."
A go-live audit found ~215 template rows (FINAL_NORM set; 348 refused + ~7.4k non-canonical cells over all 8 files) whose value
did not match its field type: `'0.0'`/`'0.5'`/`'2.0'` text on bools (multiplier artefacts), `False` on float thresholds and TF
fields, `20/50/80` on TF fields, `'0.5'` on int counts/modes (the pilot silently truncated them to 0 — the `MAX_AUGMENTS` phantom
of §90), numeric text on enum fields, and refused BOLD values (`BALANCE_FLOOR_USD='D'`, `STALE_HOLD_ENABLED='0.10'`).
Root cause: every writer typed against `QuickConfig` annotations, which declare most fields `float` (lax).

**The one validator — `tools/switch_types.py`:** `coerce(field, value, venue) -> (ok, value, reason)`. Kind comes from the LIVE
venue default (`config.Config` crypto / `config_tradier.TradierConfig` stocks), then the other venue, then QuickConfig (never lets
a float annotation override a live bool/int/str). Kinds: bool (0/1, '0'/'1'/'0.0'/'1.0', 'True'/'False', yes/no only — '2.0',
'0.5', 'OFF', 30 refused), bool01 (bool on one venue, 0.0/1.0 float on the other), int (integral only; non-integral refused —
`allow_quick_float=True` keeps the registrar's documented float-kept path), float (bools refused), tf (tokens in
OFF/1m..12h/D/W/M, combos with , + _, plus BOTH/ALL/ANY/EITHER; False -> 'OFF'), str/enum (numeric or bool-word text refused unless
the default is one), dict (FILTER_EXEMPTIONS rules), list, grey (live dict/list + vec scalar: only the QuickConfig default),
unknown (accepted 'untyped'; `strict_unknown` refuses). 'K=V' text refused; 'X=X' dup typos collapse. `canonical()` = the cell
already holds the native type; `to_cell()` = what a template cell must hold.

**Call sites (fail-closed — refuse/flag, never silently write):** `tools/v15_switch_add.py` (`validate_switch`: every candidate +
default, both venues; `parse_opt` int no longer truncates), `--queue` (via validate_switch), NEW `--fix-types`;
`tools/v15_template_row_intake.py` (`merge_entries` refuses mistyped entries, `rescue-lost` never re-queues refused values);
`tools/v15_daily_template_update.py` (`_coercible` -> promotions, `add_tab_level_rows` -> typed cells, refused options reported);
`tools/v15_avg_delta_apply.py` (promotion gate, after the MAXAUG mandate); `switch_parity.register_workbook_result` (every key,
before any surface; validator missing = refuse). `v15_pilot.py` (owned by another lane): hook spec in the 2026-10-13 report —
`_cand_compatible` already rejects bool-field garbage but MISSES int truncation, bool->float and anything->TF/str.

**Repair (`python3 tools/v15_switch_add.py --fix-types [--apply]`, S1 source host, both dirs):** unambiguous coercions written in
place; ambiguous candidate rows dropped (never guessed) and listed; a refused BOLD row is realigned to the cat_side default (= the
value live + the store already run, §90 builder placeholders): bold moves onto the row carrying it, else the cell is rewritten; a
typed row that duplicates a kept row (same value AND all other cells identical) is dropped. Guards: bookkeeper flock, backup per
file (`backups/before_fix_types_*`), template_row_guard fingerprints (lost == dropped, gained == 0), one-default rule, post-save
validator rescan (any leftover = file untouched), zip-validated atomic save. Idempotent (2nd run: 0 changes).
2026-10-13 run: per file ~850-970 cells coerced, 46-70 rows dropped, 2-4 bolds realigned, 0-2 bolds moved, 0 unresolved; daily
writer layout verify 0 violations on the result. Report: `data/reports/v15_fix_types_20261013.json` (S1).

**Never again — chain step 2b** (`tools/v15_daily_chain_s1.sh`, after the row intake): `switch_types.py --scan` over all 8
templates; any refused or non-canonical value = step FAILED loudly (`data/alerts/SWITCH_TYPE_ALERT.json`, `[chain] step2b
FAILED`, steps_ok `2b!TYPE_ALERT`); the chain continues (writers/registrar are fail-closed on their own); the alert file is removed
on a clean scan. Tests: `tests/test_switch_types.py` (17: bool/int/float/TF/enum/dict/grey cases, the '0.0'-on-bool regression,
fix-types on a fixture workbook, switch_add / intake / daily-writer / registrar refusals).

**V17 iteration 2 (2026-10-13 19:2x–20:xxZ, USER: pinpoint = a per-board SURGEON + "understand market changes since the
previous test and anticipate what is ahead"):**
- **Budget:** a long main pass can no longer starve the pinpoint round. `v17_p3_budget` guarantees board target
  (`V15_V17_BOARD_S` 1500 s = 25 min) × tier share (WINNER 50% / MEDIOCRE 40% / LOSER 25% / LOST_CAUSE 10%), more when the
  board is ahead, hard cap `V15_V17_P3_MAX_S` 900 s; the loop extends while an iteration lifts ≥ `V15_V17_MIN_LIFT_PM` 0.3 pp/min.
  Main-pass yellow work stops after `V15_V17_P2_YELLOW_S` (35% of the target): later rows run naked (+ full revive on a
  positive naked), their cells coarse-deferred (settled) for the pinpoint round.
- **SURGEON** (`_v17_diagnose` / `_v17_surgeon_paths` / `_v17_surgeon`, first step of every pinpoint iteration, 60% of it):
  (1) diagnose this board's own result — one real ledger eval of the current set on its own NPZ slice, classified with
  `tools/v15_trade_autopsy` (LOSER, PREMATURE_EXIT, GIVEBACK, MISSED_AUGMENT, missed favourable moves, churn = 1-bar trades)
  plus TIM low/high and gain below bh; (2) map every symptom to PATHS (missed moves / TIM low / below bh → entry + reentry
  switches, their loosened filters and the path WITH its exemption from `tools/v17_priors.exemptions_for` or
  `data/v17_filter_exemptions.json`; premature exits / giveback / churn → exit-path switches, ranked up by token overlap with
  the offending trades' exit reasons; losers / TIM high → entry filters tightened + exits on the losers' path; missed augment →
  AUGMENT paths), ranked by the symptom's size on THIS board (priors only break ties); (3) greedy on top of the current best
  set (fresh evals, same guards, ≤3 adoptions per round); (4) re-diagnose and repeat while it lifts. Book
  `progress["v17"]["surgeon"]` + `SURGEON` tab (per round: symptoms with size, paths tried / positive / best path with
  delta-TIM-DD-trades, adopted).
- **REGIME** (`_v17_regime`): segments of this window before vs since the previous board's window end (net side-adjusted
  move, bar volatility, chop, range → TREND_UP / TREND_DOWN / RANGE (+HIGH_VOL / +SQUEEZE)); the previous final set and the
  current set are evaluated on the since-previous slice (`prepare_batch(sym_side, N days)` = last N days up to the last closed
  bar, no look-ahead) — if the old set did better there, every key where it differs becomes an `old_set_recent` surgeon path.
  ANTICIPATE (`_v17_regime_pick`): among the top 5 positive paths on the full 30D, the surgeon adopts the first whose recent-slice
  delta ≥ −`V15_V17_RECENT_TOL` 0.25; 30D-only winners are flagged `OLD_REGIME_ONLY` (`progress["v17"]["regime_flags"]`).
  `progress["v17"]["regime"]` + REGIME row on the SURGEON tab. `V15_V17_ANTICIPATE=0` disables the recent check.
- **Types law** (template-types lane): `_switch_type_violation` (non-composite) and `_cand_compatible` call
  `tools/switch_types.coerce(field, value, venue)` first — not ok → `TYPE_MISMATCH`, validator unavailable → TYPE_MISMATCH
  (fail closed); `_parse_opt_value` int branch keeps a non-integer value raw (no silent '0.5' → 0).
- **Go-live hooks:** `sanitize_overrides` json-parses a string FILTER_EXEMPTIONS into the engine dict and drops
  `GLOBAL_ONLY_KEYS` (`AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED`); template rows of global-only keys are skipped. Proven on S1:
  a set with FILTER_EXEMPTIONS evaluates bit-identically before/after a JSON round trip.
- **Window boards** published 17:27–19:15Z with V17 default ON: `data/v17_window_requeue.json` (best-wins kept the higher
  same-window active, e.g. BWXT_SHORT 28.68 next to V17 23.71; 6 requeue candidates + the full window list).
- **A/B #2 (same host s2, both arms at the same time, load 30–50; `data/reports/v17_ab_202610132049.md`; new = V17 with the
  surgeon v1, no regime/365 step yet):** 30D final gain new vs old — ALGOUSDT_SHORT 19.05 vs 17.13, COIN_LONG 29.63 vs 13.97,
  IOTXUSDT_SHORT 39.12 vs 18.68, RLCUSDT_LONG 46.82 vs 71.44, UNIUSDC_SHORT 17.86 vs 29.92, VALE_SHORT 18.17 vs 22.84 → 3/6;
  wall: V17 slower on every finished pair (30–53 vs 27–46 min — the guaranteed pinpoint minimum is added on top of a main pass
  that is only ~25% shorter). 365D of the final sets: V17 better on ALGO/IOTX, worse on COIN/RLC/UNI/VALE. → V17 stays
  default OFF. The regime-aware surgeon canary (COIN_LONG) reached 33.62. Next: make the pinpoint budget = the time the
  abbreviated pass saves (not extra), surgeon before generic steps, re-run the A/B with 1 old + 1 new board at a time
  (resource rule: ≤2 A/B boards, nice 10, s2 only).
- **Loser forensics (A/B #2, `data/reports/v17_loser_forensics_20261013.txt`, `tools/v17_ab_forensics.py`):** every promotion
  of the old arm on RLC / UNI / VALE traced to the V17 arm. Lost pp by cause — RLC (−24.6): old sacred-repass win
  GOLDEN_RULE_BASE_USD=0.0 +19.8 (V17 chain had diverged, row naked −2.76), rows tested at a very different chain (naked ≤0,
  10.5), joint filter cells sampled out / capped (8.8), joint cells on rows already running after the pre-P2 probe never
  evaluated (7.1), old focus (6.3); UNI (−12.1): running-row joint cells (5.3: MOM3=OFF + CIRCUIT_SHARPE/DC_MOMENTUM cells),
  UNIVERSAL_NOLOSS_GATE at a different chain (3.8); VALE (−4.7): different chain (3.7), sampled-out COUNTER_TREND / MTF_DC
  cells (3.2). Fixes (v6, ce1937e7): the main-pass yellow time cap is OFF by default (it dropped exactly those cells under
  load), every cell of a top-30 / ever-promoted filter is always evaluated (p = 1), the filter probe runs after P2 (no early
  chain divergence; `V15_V17_PROBE_FIRST=1` restores), skipped cells record their p (`sampled_skip_p`), and the pinpoint
  budget = the time the abbreviated pass saved at this board's P2 seconds/eval (tier floor 180/120/60/30 s, extension while
  it lifts) so V17 is not slower than the old board. A/B #3 runs one pair at a time on s2 (nice 10).
- **Best-start floor (USER ABSOLUTE "a worse result is PHYSICALLY IMPOSSIBLE"):** the coordinator's `[BEST-START]` block picks
  the start set (best of last round vs template defaults); `_spec_fill_workbook` now notes that start set as `best_seen`
  ("START", fresh eval) before E3-DEFAULT-BASE or any phase can lower the chain, so the BEST-SEEN publish gate falls back to
  it for the old board and for V17 (test `test_final_never_below_best_start_both_paths`). Remaining exception owned by the
  endgame lane's gate: a higher start set that does not qualify (TIM band) does not replace a lower qualifying final.
- **V17 P2 = legacy (v8, c11a14e1, 21:5xZ):** the first A/B #3 pair (RLC, v6) showed V17's own main pass slower than the old
  one (2593 s for 2800 rows vs 1092 s) and on a lower chain (29.3 vs 58.7) — tab reorder + sampling of the whole cell pool
  (incl. prior-skipped cells the old board never evaluates) diverged and enlarged it. `V15_V17_P2_MODE=legacy` (default): the
  V17 main pass is the old main pass exactly (same rows, yellow policy, tab order, sacred repasses → same chain), only the
  hustle eval is dropped; everything V17 adds afterwards (probe, surgeon, regime/365D, ablation, retest, focus) is greedy on
  top, so V17 ≥ the old main pass by construction and its extra time = the hustle time saved (+ tier floor). A/B #3 restarted
  with v8.

## 97. V17 COMBO ENGINE — LEARNED + EXPLORED PATHS (2026-10-13)

**User goal (verbatim):** "NOW START ON v17 so it LEARNS FROM PAST DATA AND STARTS IDENTIFYING AND EMPLOYING TENS OF THOUSANDS
OF CUSTOM SWITCH/FILTER/TF COMBINATIONS THAT WILL FINALLY GET GAINS UP". The pilot lane owns `v15_pilot.py` (V17 SURGEON);
this lane built the engine the surgeon calls: `tools/v17_combo_engine.py` (+ `data/v17_combo_library.sqlite`,
`tests/test_v17_combo_engine.py` 12 tests, handoff `docs/v17_combo_engine.md`). Deployed s2 + S1, md5 `dc76fa82` (tool),
`6a778e76` (library), `f3f8e7bd` (`data/v17_tf_semantics.json`).

**API:** `search(prepared, sym_side, base_overrides, symptoms=None, regime=None, budget_s=300, workers=4, stats_out=,
evaluator=, windows=(30,)|(30,365), prepared_365=, accept=)` -> ranked verified combos `{overrides_delta, gain, base_gain, delta,
trades, tim, dd, wr, recent_segment_gain, recent_delta, n_evals, origin, why, flags, tf, w365, regime_attr_365, steps}`;
item 0 `kind:"joint"` only when the greedy-combined set beats the ORIGINAL base in a fresh **cache-off** re-evaluation that
matches the greedy number exactly and passes the pilot's own gates (imported from v15_pilot: sanitized floor, TIM ≤ 80,
DD ≤ 30, trades ≥ 10, `tim_guard_veto`, `trades_guard_veto`, `promotion_block_reason`, MAXAUG never 0, ABLATION never).

**Combo = path:** 1–3 switch=value changes (ENTRY/REENTRY/AUGMENT/EXIT/SIZING/GLOBAL) + filter(s) at a TF (yellow headers)
applied/removed + `FILTER_EXEMPTIONS` (exemption map / priors blocks) + numeric ×0.5…×2 inside the template span +
AUGMENT_INTO_REENTRY family. Values only from the four TEMPLATE_* candidate sets / config field types, each through
`switch_types.coerce(strict_unknown=True)`; refused/blocked/identity combos never evaluated. ~38–39k typed combos per board.

**TF coherence (TF study lane, `docs/v17_tf_semantics.md`, `data/v17_tf_semantics.json`, WO-20261013-V17TF-001..004):**
the engine has NO validity window or consumption memory for HTF signals — a D/4h STATE trigger re-opens on every flat 15m bar
while true (COOLDOWN_BARS 0; measured RLCUSDT_LONG `BB_BREAKOUT_ENTRY_TF=D` 33 opens in 6 D bars, up to 11 per instance;
94 re-opens inside one instance across the isolated runs, 61 without a fresh 15m cross), an HTF EVENT trigger lives one 15m
bar (`BB_BOUNCE_ENTRY_TF`, which is also a global entry AND filter: opens 133 -> 0). Engine rules: C4 + C9 rejected before
evaluation, C5 window ≤ arming-TF span (stocks D = 26 bars); C1/C2 combos down-ranked and re-offered as `tf_coherent`
variants with a consumption rule (cooldown ≤ span) and/or an explicit 15m confirm. TF structure (htf, ltf_confirm,
window_bars, rearm) is stored per combo in the library.

**Library (learned from past data):** per-host `build-library` (progress done rows: naked + every yellow with delta > 0;
delta-log positives of non-row phases incl. SURGEON symptom labels; v17_priors synergy + block->exemption; fleet endgame
knowledge; regime features from each board's own NPZ window, no look-ahead) then `merge`. S1 (117 s) + s2 (377 s) merged:
2,493 boards / 75,961 combos / 798,534 evidence rows; after the proofs 2,508 boards / 85,897 combos / 842,844 rows
(44,514 engine evals appended, 514 regime-evidence rows), 169 MB. Generators: same-sym history, k=30 regime neighbours
(z-scored side-adjusted trend, bar vol, log chop, range, bh, TIM, log trades), symptom fixes, regime fixes (dual runs),
synergy, block->exemption, symptom paths, filter paths, exemption paths, numeric, AIR, pairs, tf_coherent, 15% exploration.

**Search:** rounds until the budget is used — stage 1 screens the next slice of the quota-interleaved order on the full 30D
(the gate window) in batched fork-pool waves; stage 2 = top ~5% to the recent 7D segment (+365D with ledgers in dual mode);
stage 3 greedy-combines non-conflicting winners on the CURRENT best set (re-evaluated after every adoption;
OLD_REGIME_ONLY = recent delta < −0.25 is not adopted, pilot ANTICIPATE parity); the next round starts from the improved set.
FEW_TRADES flag = ≤3 changed trades or one trade > 70% of the change (ledger diff per adoption step).

**Recent-slice law (finding):** `evaluate_v12._exact_30d_slice` honours only window 1 and 7 (crypto calendar days, stocks
sessions); `prepare_batch(ss, 8)` (and any 3..15 other than 7) evaluates the FULL 30D. The engine uses 7. The pilot's
`_v17_recent_eval` / `_v17_regime_pick` use `rd` in 3..15 (default 8) -> its "recent" check is the 30D window except rd=7
(handed to the pilot lane).

**Proof (s2 only, one board at a time, 4 workers, nice 19, start = newest progress JSON `cumulative_overrides` =
published final, same NPZ for base and combos, `V12_ALLOW_STALE_NPZ=1` as the fleet launches; all numbers real engine evals,
joint verified cache-off; pool_sharpe single-board = [DIAGNOSTIC ONLY]):**

| sym_side | published (its NPZ) | fresh base, current NPZ | joint 30D (verified) | lift | screened | evals | ev/s | adopted | 7D base -> joint |
|---|---|---|---|---|---|---|---|---|---|
| RLCUSDT_LONG | 30.11 (same NPZ) | 30.11 | 64.55 (110 t, TIM 23.4, DD 4.1) | +34.44 | 5,280 | 6,085 | 25.3 | 12 | 9.58 -> 25.36 |
| UNIUSDC_SHORT | 50.43 (same NPZ) | 50.43 | 56.04 (170 t, TIM 43.3, DD 7.7) | +5.61 | 3,600 | 3,628 | 13.5 | 4 | 5.05 -> 22.28 |
| VALE_SHORT | 21.00 (older NPZ) | 16.87 | 23.75 (19 t, TIM 63.1, DD 1.1) | +6.88 | 5,472 | 5,839 | 21.8 | 7 | 0.85 -> 1.19 |
| COIN_LONG | 6.12 (older NPZ) | 6.40 | 21.56 (63 t, TIM 24.0, DD 3.2) | +15.16 | 7,080 | 7,844 | 33.8 | 12 | 1.42 -> 2.59 |

These final runs were warm (the library already held the earlier engine runs of the same board); first-contact runs:
VALE +6.73 (120 s smoke), RLC +16.79, UNI +4.21, COIN +15.67 (3,168 screened). Throughput at s2 load 8–13: 13.5–33.8
evals/s per host-run = 3.4–8.4 evals/s/core (eval-cache hits included; CPU 0.08–0.21 s/eval), 800–1,830 combos screened
per minute; at load 30–40: 7–12 evals/s. Generation 170 s -> 2 s after the coherence fix (early runs lost ~100 s to it).

**Dual 30D+365D (240 s):** VALE_SHORT 30D +5.47, 365D 8.26 -> 11.08, DD 13.99 -> 13.71 (TREND_DOWN +1.36, TREND_UP +0.72,
RANGE −0.05 pp); COIN_LONG 30D 6.40 -> 18.66, 365D −4.67 (DD 36.8 invalid) -> +7.01 (DD 29.8 valid); RLCUSDT_LONG 30D +1.87,
365D −2.42 -> −1.75. **Overfitting caught:** RLC's largest 30D combo `GOLDEN_RULE_BASE_USD=0 + REENTRY_TIER1_SIZE_MULT=0.5`
(+9.49 on 30D, bh +243%) gives 365D −23.23 with DD 38.6% (invalid) -> rejected; the 30D-only RLC lift (+34.44) must not go
live without the 365D step. Many adoption steps carry FEW_TRADES (2–6 changed trades); COIN's top steps have recent delta 0.0
(30D-only evidence). Promotion stays with the pilot (its gates + 365D policy §92).

## 98. LIVE TRADES THE VECTOR — EXITS NEVER LOST, LIVE-ONLY PATHS OFF, GUARANTEED STOPS, LATEST BOARD LIVE (USER 2026-10-13)

**User rules (verbatim intent):** "NOTHING can ever be live only — live trades EXACTLY what the vectorized best outcome switches
dictate, nothing more nothing less, 0 tolerance; only the paper traders can diverge." "GUARANTEED STOP existing as soon as any
position opens, stock or crypto, so that when any system is down it will always get rid of a position." "NO exit can EVER be
skipped AT ALL for ANY REASON."

**Evidence that live was NOT trading the vector (2026-10-13):**
- Binance 7d (income API): ang +5.24, fin −2.04, flz −2.35, inf −11.15, men +13.52 realized / −24.48 uPnL. The internal ledger
  `data/per_trade_returns` claimed every account profitable (men ≈ +$409; rows with `entry_price 0.0`) — it is NOT truth.
- 63 crypto sym_sides whose live effective settings == their latest board: vec last-7d positive on 48/63 (+$1,268 sim), Binance
  sign agreed on only 30/60. Same settings, different trades.
- NEARUSDC_LONG ang: vec OPEN 10-11 23:45 == live BUY 23:43; vec CLOSE 10-12 00:32 -> `POSITIONS_SNAPSHOT_LOCK_BUSY` returned an
  hours-old pre-OPEN snapshot -> `POSITIONS_BROKER_MISMATCH local=4 broker=0` -> exit skipped -> `BLOCKED_DISPATCH_TIMEOUT (act
  consumed, no auto-retry)` -> live held 25h, sold 5.231 (−10.8%). No FIXED_STOP existed (first ang FIXED_STOP line 10-13 01:39).
- Fleet log tallies (5 crypto accounts): ~1/3 of vec OPENs expired unconsumed (`BLOCKED_SKIPPED_STALE`, 4-bar catch-up), plus 24
  `ULTIMATE_DC_4h` hard stops and 38 WT exits expired; `CANCELLED_DISPATCH` 819, `BLOCKED_DISPATCH_TIMEOUT` 175. Root: loop
  latency (one order 80 s+: redis lock wedge 17 s, 2×20 s price timeouts, busy positions lock). Latency itself NOT yet fixed.
- 14.6% of closes came from live-only paths: VEC_STALE_FLATTEN 207, MARGIN_FREE_FOR 142, SUBSTITUTION_FOR 115,
  VEC_CONVERGE_TO_FLAT 45, CB_HTF_EXHAUST 42.
- 67/130 crypto boards published on S1 were not live: go-live selection (`data/avg_delta_selection.json`) pointed at old
  campaign files (DOGEUSDC_LONG -> destroyed s6 run30) and a manual stalefix run registered ~400 old sets incl. negative
  `_NEG_BLOCK` ones (DOGE live −3.16% vs board +50.18%, md5 5be34dd3).
- Stocks: 36/37 Tradier positions had NO broker stop — the stop wire call was refused `ORDER_DEDUPE_BLOCK:NOT_VIA_EXECUTE_NOW`.

**Fixes (2026-10-13 21:3x–22:0xZ):**
1. `positions_truth.BrokerPositions.snapshot`: busy single-flight returns ONLY a fresh snapshot, else None (unknown).
   `ez_pre_order_check`: a stale snapshot can never veto an exit (`SKIP_BROKER_FLAT` only on fresh broker truth).
2. Exit-owed is the law: a vec full exit (CLOSE / REDUCE-to-flat) is owed from hand-out until a FORCED broker read shows flat.
   Local flat never clears it (`vec_exact.exit_owed_due`); local-flat-with-owed -> forced read: holds -> CLOSE re-sent now,
   flat -> cleared, unknown -> kept. A vec CLOSE met with local flat is owed. EXPIRED vec full exits are owed
   (`pop_expired_exits`); opened_at unknown -> owed; only exits decided before this live entry are logged
   `[VEC_EXACT_EXIT_PRIOR_LIFECYCLE]` (they belong to an earlier vec position live never held).
3. Live-only closes OFF by their own switches, mirrored in QuickConfig (§68.1): `SUBSTITUTION_CLOSE_ENABLED`,
   `MARGIN_FREE_CLOSE_ENABLED`, `CB_HTF_EXHAUST_EXIT_ENABLED` = False. `verify_live_order_switches` n_hard 0
   (`FH_MOMENTUM_FILTER_TF` now read via `_psym_get`).
4. GUARANTEED STOP: crypto FIXED_STOP exchange order is never cancelled on local flat (sweep, hourly reconcile, ensure all need a
   forced broker read; unknown keeps it). Stocks: stop PLACE/CANCEL inside `execute_now_context("tradier")`, own protective stop
   never blocks exits (dedupe guard), cancel-before-exit handoff + re-ensure, broker-confirmed-flat before any cancel, registry
   save merges per account, entry-price fallback level. Audit tools (read-only): scratch `stop_audit.py` / `tradier_stop_audit.py`
   pattern — every open position must list a broker STOP.
5. GO-LIVE = latest published board per sym_side (`tools/v15_published_selection.py`, `v15_persym_golive.py --published
   --pin-all`, chain step 5b); `_NEG_BLOCK` never appended/kept; perf tiers size negative gainers minimal, never block.

**Still open (not fixed in this pass):** loop latency (the cause of expiries/cancellations); remaining live gates that can refuse a
vec-decided OPEN/AUGMENT outside the 10 `_vec_exact_reason_ok` exemptions (e.g. HARD_SIZE_GATE); live sizing multipliers
(BREAKOUT_SIZE_LADDER ×3, LS_REBALANCE) still apply per §68.3a; partial vec REDUCEs skipped while in flight are not owed;
`data/per_trade_returns` ledger must be rebuilt from Binance fills; Mac v12_quick_engine mirror fields not yet pushed to S1.
- **TIM publish law (USER ABSOLUTE):** the best-seen publish floor records every engine-valid set above the trade floor (no TIM
  veto); the publish gate takes a higher best-seen set even when it is outside [20,80] (`progress["tim_out_of_band"]` + value);
  a TIM-only miss at the DONE-stage 30D gate no longer runs the compliance repair / REDO (`[TIM-OUT-OF-BAND]`,
  `V15_TIM_PUBLISH_LAW=0` restores). TIM still guards promotions during the fill. Test `test_higher_out_of_band_set_publishes_flagged`.
- **Combo engine wired (v10, bb3eb5d1):** `_v17_combo_step` = first pinpoint step of iteration 1 with a fixed share
  (`V15_V17_COMBO_SHARE` 0.35 of the pinpoint budget): `tools/v17_combo_engine.search(... windows=(30,365), prepared_365,
  recent_prepared=7D)` through `_V17PilotEval` (30D evals on the pilot's pre-forked pool, 7D/365D/ledger evals in the main
  process — no second fork pool); its joint set + singles are adopted greedily with the pilot's gates and ANTICIPATE; the
  engine records every evaluation in `data/v17_combo_library.sqlite` itself (`record=True`). Fail-open (absent module / error
  = skipped, `[V17-WARN]`). Recent slice = 7 days (the engine slicer honours 1/7/30/365 only).
- **A/B #3 first pair (legacy-P2 V17, same host, nice 10):** RLCUSDT_LONG old 72.11 published in 20:14 min; V17 main pass ended
  at 67.15 (16 min elapsed) and the pinpoint round took it to 119.76 (surgeon +31.04 in 109 s, retest +11.31, ablation
  +1.55, probe +4.77; extended 3x while lifting). Final/365D numbers: `data/reports/v17_ab_*` when the pair completes.
- **v12 (2e10f436, 22:4xZ):** per-tier pinpoint cap (USER "extend time cap for winners"): `v17_p3_cap` —
  `V15_V17_P3_MAX_S_WINNER` 1500 s / `_MEDIOCRE` 540 / `_LOSER` 300 / `_LOST_CAUSE` 120 (an explicitly set `V15_V17_P3_MAX_S`
  overrides all tiers); the round still only extends while it lifts ≥ `V15_V17_MIN_LIFT_PM`. Pool guard: a failed pool submit
  (broken / SIGTERM-ed workers) is a missing verdict, never a KeyError that aborts the pinpoint round (production AMZN_LONG
  lost its round + failed the ESSENTIAL gate that way); the ESSENTIAL proof now also runs after any pinpoint error.
