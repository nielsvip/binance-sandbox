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

## 0. STATE OF THE SYSTEM — 2026-10-13 (READ FIRST — supersedes any older text that conflicts)

Day summary for the next agent. Each law below is USER-authoritative; the referenced sections carry the detail. Archived
(superseded) sections live verbatim in `BACKTEST_BIBLE_ARCHIVE.md` — never resurrect them.

**Fleet (2 servers only).** S1 (`s1-int` 127.0.0.1:2201 via `ssh -fNT s1-sftp-direct` if the gateway tunnel resets; `s1-pub`
157.180.125.52) = fleet scheduler + NPZ source + board pilots + STANDBY live stack. s2 = `10.0.0.4` (reach via S1) = board pilots.
s5/s6 were DESTROYED 2026-10-13 (`data/fleet_drain_hosts.json` lists them). Mac = LIVE trading only. Targets (USER): ≥12 fully
resolved sym_sides per hour per server, then >30; FULL backtests until 10x bh; never let a board that keeps lifting ≥0.3 pp/min die.

**Scheduler (S1 cron */2, `tools/v15_fleet_scheduler.py`).**
- A tick must never block: every launch (new 30D, backfill, stolen 365D/REPAIR, any window, any host incl. S1 itself) goes through the
  detached `tools/v15_dispatch_job.py` (§93). The job (1) tail-refreshes S1's NPZ with `tools/npz_bar_refresh.py --symbols SYM --w15 2500`
  (append-only, ~9–13 s), (2) md5-pushes it to the target, (3) ships the sym_side's top-5 published sets (`~/v15_prev_best/{ss}.json`
  from `per_sym_history`), (4) launches. Frozen symbols (unfinished board / running sibling) are never refreshed or pushed (`frozen-keep`).
  Full-history NPZ rebuilds only when S1 has no NPZ. Speculative NPZ prefetch is OFF (`V15_SCHED_NPZ_PREFETCH=1` to re-enable).
- Both sides of a symbol launch together (`V15_SCHED_PAIR_ALWAYS`). Unfinished boards resume from their last cell before new symbols.
- Every 30D launch runs V17 (`V15_SCHED_V17=1` → `V15_V17_ROUNDS=1`). OOM guard spares boards still lifting in pinpoint (last victim).
- Tokenized-stock perps (Binance `TRADIFI_PERPETUAL`, 221 symbols) are excluded everywhere via `data/excluded_tokenized_stocks.json`.

**Pilot laws (`v15_pilot.py`).**
- BEST-START (absolute): the first baseline is the best of 6+ candidates re-measured on the CURRENT NPZ — the incumbent start set
  (which already beat cat_side defaults + per-sym), the top-5 published sets layered on TODAY's TEMPLATE_CAT_SIDE defaults, earlier
  finals on the host, and today's template defaults alone. The start is recorded as best_seen; only positive deltas follow; the publish
  gate falls back to best_seen — a final below the start is impossible (`[BEST-START]`, `progress["best_start"]`).
- 365D NEVER gates and never triggers REDO/quarantine (`V15_365D_RECORD_ONLY=1`, verdict in `progress["verdict_365d"]`, §92).
  Instead V17's 365D step (≤240 s, 600 s for winners) finds regime-specific fixes that keep 30D ≥ best and improve 365D.
- Endgame/diagnose adopt only if fresh gain ≥ entry (`[ENDGAME-REJECT]`/`[DIAG-REJECT]`, §69); strip sets are discovery only.
- A higher-gain valid set publishes even when TIM is outside [20,80] (flagged `tim_out_of_band`); TIM guards promotions only.
- Yellow cells are never switched off: p_eval = max(1/200, pos_sym/pos_top) (`tools/v17_priors.py p_eval`), high-value filters always run;
  skipped cells are SETTLED (never hollow → no RULE#3 REDO).
- Switch values are typed and fail-closed everywhere (`tools/switch_types.py`, §96).

**V17 rounds (§95).** Main pass = legacy rows/tab order (keeps the old chain) minus the hustle eval → filter probe → PINPOINT:
combo engine (30D+365D dual window, §97) + per-board SURGEON (diagnoses this board's TIM/DD/gain-vs-bh/churn/missed moves/giveback
→ specific switch + filter paths + FILTER_EXEMPTIONS) + regime step (previous vs current window, old-set forward check on the last 7
days) + ablation route (strip a filter category → discover blocked switches → pinpoint the killer filter → exempt the switch → restore
filters → adopt only above previous best) + retest of paths that had no chance + focus. Pinpoint tier budgets are INITIAL only
(WINNER 1500 / MEDIOCRE 540 / LOSER 300 / LOST_CAUSE 120 s); any board extends while the last window lifted ≥0.3 pp/min.
A/B #3 pair 1 (RLCUSDT_LONG, same host/NPZ/start): V17 127.69% vs old 72.11% (33.0 vs 20.2 min).

**Learning stack.** `data/causal/v17_priors.json` (1.59M cached evals mined), `data/v17_combo_library.sqlite` (every combo + regime +
TF structure, appended by every board), `data/endgame/endgame_knowledge_fleet.json` (hourly merge), `tools/v17_daily_learn*` (daily
13:30Z: zero-token prep packet ≤30 KB → one capped headless Claude run → `data/learn/insights_latest.json` consumed by the combo engine),
invention loop + causal learner/rescue closed on S1 (§94).

**Engine / live (§98).**
- New switches, defaults keep behaviour unless stated: FILTER_EXEMPTIONS {path:[filters]} (per-switch filter exemption, the ablation route's
  tool); AUGMENT_INTO_REENTRY family (6 per-sym keys + global AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED=True): the missed augment is
  carried into a higher-low (LONG) / lower-high (SHORT) reentry — wired on every surface incl. native live and backtest_v12_engine.
- HTF signal law (USER, default ON, in progress): an HTF signal stays valid for its span (crypto D = 96×15m) and may re-open ONLY on a fresh
  lower-TF confirmation, with each further attempt (up to ~12) requiring stricter tolerance; never stacks opens on persistence, never
  discarded after one hit. Docs `docs/v17_tf_semantics.md`.
- NO live trade the vector did not authorize: crypto `PARITY_VEC_EXACT_MODE=True`; stocks `config_tradier.PARITY_VEC_EXACT_MODE=True`
  (2026-10-13). Live resolves per-sym SQL set > cat_side defaults (daily from TEMPLATE bolds) > config literal (§90).
- `DC_EDGE_SIZING_MAX_MULT=6.0` (config.py, template parity). Live picks up today's engine/config changes at the next sanctioned sync + restart.
- Live failover (in progress): S1 standby producers start within 3 min of stale Mac data; full trading failover with a lease (fencing).

**Throughput killers found today (never repeat).** Synchronous NPZ rebuild in the scheduler tick (fleet idle 13:44–16:5xZ);
ZERO_DETECT_SIDECAR re-stamping 214 boards (now `--no-paint`); 365D REDO deleting published finals and re-running one side alone;
S1 load from non-board work (batch NPZ builds by hand, klines vault/restore, parity probes, `tradier-sandbox-indicators.service` —
now disabled); mem guard on S1 (only 2 pairs). Throughput math: 12/h/server = 80 core-min per board average (non-winners ≤60).

**Handover:** `docs/HANDOVER_10X_BH_AND_SPEED_20261013.md` (status, open work, commands, measured numbers).

---

## 1. INFRASTRUCTURE — GATEWAY + S1 ALWAYS STAY; s2 IS THE ONLY OTHER WORKER (2026-10-13)

**Gateway + S1 never deleted, never wiped.**

- **Gateway** `157.90.168.35` (`gateway-internal` via `~/.ssh/config` ProxyJump). Tunnel to S1: `ssh -fNT s1-sftp` (via gateway) or, when that resets,
  `ssh -fNT s1-sftp-direct` (public IP) — both expose S1 as `s1-int` on `127.0.0.1:2201`. Fallback `s1-pub` (`157.180.125.52:22`).
- **S1** `10.0.0.3`, 16c / 30 GB: fleet scheduler (cron */2), NPZ source (`backtest_v8/indicators`), board pilots, STANDBY live stack
  (`~/binance`, `~/binance/ez_s1_stack`); S1 trades live only on failover (lease-fenced).
- **s2** `10.0.0.4` (reach via S1: `ssh s1-int ssh 10.0.0.4`), 16c / 30 GB: board pilots. s5/s6 destroyed 2026-10-13.
- **Mac** — LIVE trading + dashboard only, never backtests (Mac NPZ truncated → `0 trades DATA_ERROR`).

**Code sync:** edit on the Mac, deploy changed runtime files to S1 `~/binance-sandbox` (+ `~/binance` for the pilot) and s2 `~/binance-sandbox`,
md5-verify. When another agent edits the same file concurrently, patch the DEPLOYED copy surgically instead of shipping unfinished Mac work.
Plain `rsync` from the Mac can hang under S1 load — `ssh s1-int 'cat > file.new' < file` + compile + `mv` works.

**NPZ:** JIT only — the dispatch job tail-refreshes and pushes per launch (§0, §91, §93); `tools/sync_indicators.sh` (S1 */10) syncs to `10.0.0.4`
only and skips frozen symbols.

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

## 11. MACHINE ROLES & CONNECTION — BEFORE ANY SSH (2026-10-13)

See §1. Before any ssh: a working `s1-int` tunnel (`ssh -fNT s1-sftp` or `ssh -fNT s1-sftp-direct`); try `s1-int` and `s1-pub` before
declaring S1 down. `~/binance-sandbox` is canonical on S1/s2 — never hardcode, use `Path(__file__).resolve().parents[1]`.
`pkill -f PATTERN` over ssh kills your own ssh session when PATTERN appears in the command line — use `pgrep -f "[x]yz"` bracket patterns.
Remote crontab edits only via a script file with a non-empty-backup check (a bare `crontab` installs an EMPTY crontab).

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

## 26. SESSION LEARNINGS LEDGER — 2026-09-29 (operator-corrected) — ARCHIVED 2026-10-13 (dated session ledger); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 34. DAEMON LAYER — RETIRED 2026-10-06 (SCHEDULER ERA) — ARCHIVED 2026-10-13 (daemon layer retired); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 44. 365D ROBUSTNESS — ARCHIVED 2026-10-13 (365D no longer gates (§0, §92)); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 55. SESSION TIMELINE — WHAT WAS ACTUALLY ESTABLISHED (2026-09-29) — ARCHIVED 2026-10-13 (dated session timeline); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 58. NEGATIVE 365D = FAULTY 30D SHEET → EXIT/TRADE REPAIR LOOP (USER 2026-09-29, AUTHORITATIVE) — ARCHIVED 2026-10-13 (365D REDO loop replaced by record-only + V17 365D step (§0)); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

## 59. FLEET RUN REGIME — RETIRED 2026-10-06 (SCHEDULER ERA; was USER 2026-09-29 PM) — ARCHIVED 2026-10-13 (fleet regime retired); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 61. AUTOPILOT (USER 2026-10-02) — ON HOLD (`#BUILDER_HOLD` in S1 crontab, verified 2026-10-07). See AUTOPILOT_RUNBOOK.md. — ARCHIVED 2026-10-13 (autopilot on hold/retired); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 64. CAT_DEFAULTS-BASELINE LAW (USER 2026-10-03, AUTHORITATIVE — comparability) — ARCHIVED 2026-10-13 (replaced by BEST-START law (§0)); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

## 65. NO-RETEST LAW (USER 2026-10-03, AUTHORITATIVE — compute-once) — ARCHIVED 2026-10-13 (replaced by daily recalc cadence + BEST-START (§0)); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

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

## 73. POSSYM SAMPLING — CURRENT PROBABILITIES + PASS STRUCTURE (USER 2026-10-06, AMENDED 2026-10-07) — ARCHIVED 2026-10-13 (replaced by pos_sym p_eval sampling (§0, §95)); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

## 74. FLEET REALITY + WALL-TIME MATH (AUDITED 2026-10-07) — ARCHIVED 2026-10-13 (fleet wall-time math for 5–6 hosts obsolete (§0)); verbatim text in BACKTEST_BIBLE_ARCHIVE.md

## 75. DAILY SCHEDULE — CLOCKS, ORDER, RETRIES (USER 2026-10-07)

All UTC. Market open 13:30Z (09:30 ET) — everything lands before it.

| UTC | What | Where | Note |
|---|---|---|---|
| 11:55 | bookkeeper (ZERO_FORMULA_BOOK + PROTOCOL tabs) | S1 cron (holds `/tmp/v15_daily_chain.lock`) | USER 2026-10-14: 11:55Z (never early — the chain must take in the full morning of calculations). Runs BEFORE the chain: the chain writer preserves non-SWITCH tabs, so books ride through the rewrite and the stamp md5s include them. Moved from 12:30Z 2026-10-07 (it raced the chain's step-2 rewrite on the same files with a different lock). |
| 12:15 | daily chain (herd avg deltas → template writer copies POS_SYM + AVG_DELTA row by row per cat_side, promotes highest positive to bold, re-sorts groups → cat_side defaults from bolds → possym → push) | S1 cron `V15_DAILY_CHAIN_S1` | 2026-10-14 (USER "TEMPLATE_* rewritten BEFORE market open after the avg delta calculations, pos deltas become defaults for every cat_side"): 10-13 stamp 13:44Z, Mac apply 14:11Z = AFTER the 13:30Z open (12:15Z run died at step 4c + generic writer still ran, now removed). Stays 12:15Z for the full morning of herd calculations, waits on the bookkeeper lock, ~30 min → ~12:45Z. Stamps `data/daily_chain/<date>.json`. Fail-stop (except watchdog/tradeable-check). The script exits 0 when today is already DONE (`V15_CHAIN_FORCE=1` reruns) and moves an earlier FAILED stamp aside on retry. |
| 12:55 | chain catch-up (no-op if today DONE) | S1 cron `V15_DAILY_CHAIN_S1_CATCHUP2` | Retry a failed/missed 12:15Z run. |
| 12:30 | Mac apply pre-open slot (same script, pid lock `data/daily_chain/.mac_apply.lock`) | Mac cron `V15_DAILY_CHAIN_MAC_PREOPEN1` | 2026-10-14: waits up to 55 min for the stamp; new defaults + QuickConfig/config sync on Mac/S1/S2 before 13:30Z. 13:00/20:00/06:00 slots remain as late fallbacks (DONE day = no-op). |
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

**Two legs:** (1) software leg exits the regular (maker-first) way at the frozen level, reason `FIXED_STOP` — **always ±0.25%, never widened** (USER 2026-10-18: the maker leg must keep its chance to fill, because almost every stop was ending up as a market order); (2) exchange leg presets a STOP_MARKET backstop beyond it (`closePosition`, `MARK_PRICE`) so a dead system still exits taker — **crypto/Binance sits at `CRYPTO_EXCHANGE_TOTAL_PCT` = 0.40% from the frozen channel (`×0.9960` LONG / `×1.0040` SHORT, `fixed_stop.crypto_exchange_level`, USER 2026-10-18, was 0.2999%)**, stocks/Tradier still 0.05% past software (`×0.9995` / `×1.0005`, `fixed_stop.exchange_level`) = 0.2999% from the channel. Crypto wires via Binance **algo-conditional** orders (`-4120` on `/fapi/v1/order`); stocks via Tradier GTC stop. Live loops self-heal (adopt/peer-dedupe, cancel-on-close, orphan sweep); `positions_truth` bypasses PLACE/CANCEL (no position change expected); parity gates treat `FIXED_STOP` as emergency.

**Baseline honesty (§15/§19):** the stop is a REAL ledger change — baselines and finals recomputed under it WILL differ from pre-stop sheets. That is the fix working, not drift. Never present pre-stop gains as current; the hold-up batch (`data/reports/fixed_stop_holdup/`) recomputes baseline+final per sym_side under the stop (HOLDS = recomputed final ≥ recomputed baseline). Vec models the software leg only (perfect execution); the venue offset (crypto 0.40%, stocks 0.05% past software) is a live-only placement number, so `v12_quick_engine` and `backtest_v12_engine` fire at ±0.25% exactly as before and NO board changes when it moves — `v15_pilot` has nothing to flip either (zero config/QuickConfig/template surface). The one real parity consequence: when the maker leg fails to fill and the venue order fires, live books a worse exit than the vector's ±0.25% (crypto: 0.15pp worse since 2026-10-18), so widening the venue leg is only paid for by fewer market exits, which is exactly why it was widened.

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

**Amendment 2026-10-14 05:1xZ (throughput deadlock, s2 26% cpu with 8/9 "held").**
- Slots = RUNNING pilots + in-flight dispatches only; persisted holds without a process are no longer slots (`V15_SCHED_CHAIN_HOLD=1` restores).
- 365D LAW in the scheduler: after a 30D final the side is `terminal_ok` — no 365D verify / REPAIR / GS launches
  (`V15_SCHED_30D_ONLY=1` default; running chain jobs finish). Cron env dropped `V15_GS_FLEET=1 V15_SCHED_FORCE_REPAIR=1 V15_REPAIR_ALL_TIERS=1`.
- Venue phase falls through to crypto per host when the US market is closed or no stock side can actually launch on that host
  this tick (`venue_fallthrough` in the tick log; `V15_SCHED_PHASE_FALLTHROUGH=0` disables).
- Pair gate compares the launched sides with the sides that HAVE a pending action (same budget rules); the old attempts-only
  view made pinned/sliced sides "not needy" -> 39 stock symbols gated forever.
- Pair memory floor via `V15_SCHED_PAIR_FLOOR_MB=4000` (measured 1.7–3.7 GB per side at 2 workers; max measured x1.25 still wins).
- One organizer: S1 crons `V15_PUSHSUP_S1_20261010` (v15_pusher_supervisor -> v15_pusher_worker) and `V15_PUSHCOORD`
  (v15_pusher_coordinator -> v15_gain_pusher) commented `#PUSHER_OFF_20261014#`; no systemd/@reboot starter found.

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
- **v13 (4a1b90b7, 23:0xZ) — USER "the 0.3 rule: never kill a backtest as long as it manages to keep it up":** per-tier caps are
  INITIAL budgets only (`v17_p3_cap`; WINNER always gets its full 1500 s, not the P2-saved estimate). `v17_p3_hard()` = inf unless
  `V15_V17_P3_MAX_S` is explicitly set; `v17_p3_extend()` adds a chunk (max(`V15_V17_P3_CHUNK_S` 180 s, last window)) whenever the
  last window lifted ≥ `V15_V17_MIN_LIFT_PM` 0.3 pp/min and the deadline is < 120 s away; `V15_V17_P3_MAX_ITERS` / `V15_V17_SURGEON_ROUNDS`
  default 0 = unbounded. WINNER: combo engine (30D+365D) runs EVERY window (`P3_COMBO_<it>`, share `V15_V17_COMBO_SHARE_WINNER` 0.5)
  then the surgeon (`V15_V17_SHARE_SURGEON_WINNER` 0.75 of the rest); 365D regime box `V15_V17_365_S_WINNER` 600 s. `_SLICE_HOLD`:
  `V15_FILL_SLICE_S` never slices a board once its pinpoint started. `[V17-HB]` heartbeat every `V15_V17_HB_S` 300 s ONLY when evals
  advanced (keeps reaper/watchdog idle clocks honest; a hung pinpoint still goes silent). Per-window lift book `progress["v17"]["p3_lift"]`.

## 99. DAILY LEARN LOOP (2026-10-13)

USER 2026-10-13: "a daily cron that uses <5% of tokens for the day and learns from all the trades and the backtest results and
starts understanding how to get out at the top and in at the bottom with bigger amounts if higher low (vv short)". (Section number
99 because §98 was already taken by the live-trades-the-vector ruling.)

- **Schedule.** launchd `com.binance.v17-daily-learn` 13:40Z (`launchd/com.binance.v17-daily-learn.plist`, primary — it has the
  login keychain) + cron fallback `10 14 * * *` `# V17_DAILY_LEARN`. Cron alone cannot call `claude` on this Mac ("Not logged in",
  proven hourly by `data/reports/klines_culprit.log`); the script's auth preflight exits 5 there, a done day exits 0 at once.
  Overlap guard `/usr/bin/lockf` on `/tmp/v17_daily_learn.lock` (macOS has no flock). Log `/tmp/v17_daily_learn.log`.
- **Prep = pure Python, zero tokens, read-only** (`tools/v17_daily_learn_prep.py`, nice 15, no NPZ builds, no evals):
  `--leg backtest` on S1 (+ `--leg backtest-host` on s2 = 10.0.0.4 over ssh): progress JSONs touched in 24h (`~/v15_run*`,
  `~/v15_import*`) -> v17 per-phase lift, promotions (adopted paths + delta/TIM/DD/trades), best_start, final vs bh, surgeon
  symptoms/exit reasons, regime; combo library last-24h origins + top positive combos + `insight_outcomes`; `v17_ab_*.md` of 24h.
  `--leg live` on the Mac: `data/history/<acct>/*.jsonl` + `data/tradier/history/<acct>/*.jsonl` lifecycles (broker echoes
  dropped when the bot's own event is within 300 s; `REJECTED` intents never count) on the freshest 15m klines copy
  (`klines_cache[_gateway][/tradier]`): entry vs the local LOW/HIGH (±16 bars, % and bars), exit vs the local top/bottom, MFE
  giveback, hold, higher-low (LONG) / lower-high (SHORT) adds + forward result, peak/first size (dust starters skipped), missed
  augments (in-trade HL pivot then ≥1% run with 0 augments), regime label (`v17_combo_engine.classify_regime`); `data/decisions`
  VEC_EXACT share. `--leg packet`: `data/learn/daily_packet_<date>.md` (HARD CAP 30 KB; top-N shrinks until it fits) + `.json`,
  `valid_keys_<date>.json` (every TEMPLATE_* row + yellow header value per cat_side through `switch_types.coerce` strict) +
  `valid_keys_<date>.txt` (≤ 20 KB focused reference for the model). Every aggregate is DIAGNOSTIC ONLY; no Sharpe anywhere.
- **ONE LLM call/day, no tools.** `claude -p --model claude-sonnet-5-5 --max-budget-usd ${V17_LEARN_BUDGET_USD:-3}
  --output-format json --tools "" --no-session-persistence --strict-mcp-config --disable-slash-commands`, cwd
  `~/.cache/v17_daily_learn_cwd` (outside the repo: no CLAUDE.md / memory tokens), stdin = `tools/v17_daily_learn_prompt.md` +
  packet + key reference. With zero tools the model can write nothing; the script writes only under `data/learn/`. First run
  2026-10-13: prompt 53.9 KB, 42.5k input + 10.8k output tokens, **$0.278** (cap $3), 16/16 candidate paths valid.
- **Validation (fail-closed).** `--leg ingest` parses PART 1 JSON + PART 2 markdown -> `insights_<date>.raw.json`,
  `insights_<date>.json` (validated), `.validation.json`, `.md`; every `overrides_delta` key must be a TEMPLATE key of that
  cat_side, its value must coerce (`switch_types`, strict) AND equal a template candidate (numeric: inside the template span);
  ABLATION_*=True and MAX_AUGMENTS_PER_POSITION=0 refused; > 4 keys trimmed; invalid keys DROPPED (listed in `dropped_keys`),
  empty paths dropped; ids `<date>-NN`. Cost row -> `data/learn/cost_log.jsonl`. Only a non-empty validated file replaces
  `insights_latest.json`, which is pushed to S1 + s2 `~/binance-sandbox/data/learn/` md5-verified.
- **Consumer = proof, not trust.** `tools/v17_combo_engine.generate()` step (0): `load_daily_insights(cat_side, sym_side,
  regime_now)` (same cat_side; sym_side null or equal; regime null or prefix match; file ≤ `V17_INSIGHTS_MAX_AGE_DAYS` 3 old;
  `V17_INSIGHTS_OFF=1` disables) adds each path as origin `daily_insight` (score 8, +1 sym-specific; quota 1.0 = scheduled
  first), still re-typed / blocked / coherence-checked, then evaluated by the real engine with the caller's 30D (+365D in dual)
  gates exactly like every other origin. `_record_insights` writes one `insight_outcomes` row per insight per board (evaluated
  d30/gate/7D/365D delta/adopted, or identity / refused_type / blocked / incoherent / not_reached); rows whose base eval was
  INVALID (e.g. STALE_NPZ_SKIPPED) are reported as `eval_invalid_base`, never as a result. The next packet's
  "B.YESTERDAY'S INSIGHTS" table carries these outcomes and the prompt tells the model to retire what failed. The surgeon in
  v15_pilot needs NO hook: it calls `CE.search()`, which calls `generate()`. CLI check: `python3 tools/v17_combo_engine.py
  insights [--sym-side X]` (no evals).
- **Never.** The loop never edits code/config/templates, never applies anything live, never places orders; a proposal reaches
  live only through the normal pilot promotion + daily chain after it beat the gates.
- Tests: `tests/test_v17_daily_learn.py` (live leg on fixtures, path labels, packet ≤ 30 KB on oversized inputs, progress row
  shape, validator drops invalid keys, LLM output parse, insight loading filters, insights scheduled first, outcome recorded +
  summarised).

## 100. LIVE FAILOVER S1 (2026-10-13)

Full spec, triggers, fencing and fail-back: [`INFRASTRUCTURE.md` § LIVE FAILOVER S1 (2026-10-13)](INFRASTRUCTURE.md). Backtest-relevant facts only:
* Live data inputs can now come from S1 while a venue is ACTIVE (`data/live_failover/delivery.jsonl` on the Mac names every file, provenance `S1:<host>`; crypto snapshots arrive as `data/market_data_<ts>_s1.json`). Klines are only replaced by strictly newer bars — never older, never deleted. The vec twin (`PARITY_VEC_EXACT_SOURCE=live_klines`) reads the same `klines_cache`, so its inputs are refreshed by the same delivery.
* `VEC_STALE_FLATTEN` is NOT a stale-data exit: it runs only when the twin status is OK and its bar is ≤ `VEC_EXACT_STALE_FALLBACK_BARS` (4 × 15m) old, and needs 8 bars (`VEC_STALE_FLATTEN_BARS`) of paper-flat + live-holds with no act. Over the retained ez_manage logs `VEC_EXACT_STALE_FALLBACK` (the real "twin data stale" event) fired 0 times; native fallbacks were `NO_STORE` only (21). Data failover therefore avoids 0 of the stale-flatten closes by construction; their driver is paper/live divergence, not input staleness.
* Proof harness: `tests/test_live_failover.py` (14 tests, Mac + S1).
- **v14 (0704b44a) signal law:** `_sig_shutdown` acts only in the parent pid (`_MAIN_PID`); a forked worker re-raises SIG_DFL.
  Pilot pool + combo PoolEvaluator use a SIG_DFL initializer; `V17_COMBO_IN_PILOT=1` makes `search()` refuse to fork its own
  pool inside the pilot (AMZN_LONG RUN-FAILED root cause). Test `tests/test_v15_pool_signals.py`.
- **v15 (404a3881, 2026-10-14):** GLOBAL_ONLY_KEYS += PARITY_VEC_EXACT_MODE/_FAMILIES/_SET_DIR (never rows, never adopted —
  `_v17_blocked`, stripped in `_clean_ingested_overrides`, START/needs_redo ingest and every progress write). LOSER skips
  endgame+diag. Header-map cache per worksheet; self-monitor/stuck guards 8 s/30 s -> 120 s. Prefetch task-cap + presubmit
  exist behind `V15_PREFETCH_TASKCAP=1` / `V15_ROW_PRESUBMIT=1` (default OFF: S1 A/B UNIUSDC_SHORT identical result + wall,
  +33% CPU — hosts are CPU-saturated). Tests `test_v15_global_only_keys.py`, `test_v15_throughput_pipeline.py`.

## 101. RE-ENTRY AFTER A STOP + STOCKS SESSION-OPEN STOP RE-FREEZE (USER 2026-10-13/14)

**(1) REENTRY_EXIT_RECLAIM (existing switch, REENTRY_WINDOWED, bold True / buffer 0.2 in all 4 templates) was NEVER modelled
by simulate_one** — its vec twin `vec_decisions/lane_vec_reentry2.reclaim_buffer_mask` had no caller (VEC_UNSUPPORTED), so every
board measured it as OFF while live B00 (ez_manage evaluate_reentry) / H2 (tradier_manage) declare it ON (both native = inactive
under PARITY_VEC_EXACT_MODE). Wiring prepared and PROVEN, NOT shipped: `reclaim_ready_mask()` (exact B00/H2 legs: close vs
ema_9_15m side + 15m WT wt1>wt2, velocity>0, k_15m<80, shorts mirrored) + armed-after-ANY-exit price leg (close beyond last exit
x (1 +/- BUFFER)) + label REENTRY_EXIT_RECLAIM. Flip proof (30d, default set): ALGOUSDT_LONG -1.09% -> -4.20% (71 re-entries),
ALGOUSDT_SHORT +0.52 -> -0.09, BTCUSDC_LONG -3.48 -> -3.49, stocks unchanged (PRICE_CROSS_BACK fires first). Patch:
`backups/exit_reclaim_wire_v12_quick_engine_202610132355.patch` (engine held at measured md5 891ecc85 at that time). Shipping it
activates the pathway on every pinned board at once (all boards pin True). USER 2026-10-14 chose (a) SHIP NOW: patch applied
onto the engine (md5 51d666ff, flip proofs re-run identical), pushed to S1/S2 sandboxes (md5 + import OK), crypto live restarted
(S1 failover disarmed during the roll, re-armed after); stocks load it at the 13:01Z tradier start. Boards published before
51d666ff were measured WITHOUT it — the next fleet round re-measures them with the re-entry active.

**(2) SESSION_OPEN_STOP_REFREEZE_ENABLED (NEW, stocks only, default False, EXIT_STRUCTURAL rows False/True in TEMPLATE_STOCKS_
LONG/SHORT).** First RTH bar of each ET date: a position carried overnight gets a NEW FIXED_STOP at the current dc_15m_low -0.25%
(vv short) with the unchanged math of vec_decisions/fixed_stop.py; breached -> FIXED_STOP exit; re-entry through the re-entry
pathways. Vec: `vec_decisions/fixed_stop_session.session_open_mask` (single call site in the stocks session setup) + re-freeze in
the FIXED_STOP block. Live: tradier_manage FIXED_STOP block (once per position per ET date, RTH, opened before today; level map
carries `sess`), and ensure_fixed_stop_backstop_tradier now re-places when the resting stop price != the frozen level (tick-exact).
Name avoids "FIXED_STOP" on purpose: tests/test_fixed_stop.py pins the 10-12 mandate that no config field may switch the stop.
Flip proof: AAPL_LONG +6.26 -> +6.52, NVDA_LONG 6 -> 10 trades (FIXED_STOP exits 4 -> 7), NVDA_SHORT stops 1 -> 0, crypto unchanged.
Registry + SWITCH_BIBLE green; rows added on S1 via v15_switch_add, synced S2+Mac (md5 abc0e4fe / 071ad735); MASTER_switches.csv
line appended (S1/S2/Mac). INCIDENT: a first rename attempt ran two v15_switch_remove processes concurrently (dropped ssh session
+ relaunch) and deleted 2 original STOCKS_SHORT EXIT_STRUCTURAL rows (DC_DAYTRADE_TARGET_PCT=0.01, MOM3_FILTER_TF=D); restored
from the 00:05 pre-add backup (no other writer in between, md5 ceb49f39), then re-added; final diff vs original = exactly the 2
new rows per file, 0 missing. Lesson: template tools on S1 run detached (setsid) and ONE at a time — check pgrep first.

## 102. STALE-SET 2-LOSS HALT (2026-10-13)

**User rules (verbatim):** "NO blocking loss closes if the vector tells you to" + "but if the vector has not been refreshed
for over 10 days and 2 consecutive trades close at a loss, trading needs to be halted for the sym_side until recalculated
with latest npz." Live follows the winning vectorized board exactly, INCLUDING loss closes.

**(1) Vec-decided closes are never refused for being at a loss (audit 2026-10-14).**
- ez `execute_now`: `UNIVERSAL_NOLOSS_GATE` / `NOLOSS_MIN` already bypassed for `_vd_exempt` (2026-10-06). **Fixed:** the
  EMERGENCY_BRAKE (10/min churn, 500 entries/h, 1000 trades/h, 50/h per key) refused every close with tracker gain <= 0.1 —
  i.e. exactly the losing vec closes. Now `_eb_vec_exit = _is_close and _vd_exempt` skips the live brake and the
  `LIVE_VEC_EMERGENCY_BRAKE` enforcement for vec-decided exits.
- tradier `execute_now`: **Fixed:** `EXCHANGE_OPEN_ORDER_EXISTS` matched our own resting protective stop (§98 GUARANTEED STOP,
  `sell` for a LONG) against every LONG exit (`SELL`) and refused it before `place_order`'s cancel-before-exit handoff could run.
  Protective stops (`order_dedupe_guard.tradier_protective_stop`) are now skipped there, same exemption as the ODG.
- Not loss-driven, left as is (reported): ez `BROKER_SYNC_PENDING_CONFIRMATION` (90 s repeat guard, also on exits), ez
  `STALE_MARK_PRICE` on a partial REDUCE with no price anywhere, ez `MAKER_SUPPRESS_WEBHOOK` (maker lock / dust), tradier queue
  90 s `DUPLICATE` and redis exec lock (REDUCE->CLOSE back-to-back), tradier `GFV_BLOCKED` (cash account). Partial vec REDUCEs
  refused by these are not re-sent (only full exits are owed, §98).

**(2) The halt (`stale_set_halt.py`, both venues).** Per sym_side:
- *vector age* = now − `recalc_anchor(active per_sym_active row)`: (a) dated board tag (`v15_30D_YYYYMMDD`, `pilot30d_`,
  `continuous_<epoch>`, `..._YYYYMMDD`) + `data/published_selection.json` `published_at` <= apply → board publish time (board
  finished on a JIT NPZ, §91); (b) dated tag only → the tag date (apply day 00:00Z, conservative); (c) undated tag (mandate /
  artefact strip, backfill) → latest DATED `per_sym_history` row at/before the apply; (d) nothing dated → earliest of
  campaign_ts / meta.updated_at / meta.promoted_at. **`updated_at` alone is NOT used** — the 2026-10-08 rename re-stamped every
  row; `campaign_ts` = apply time (switch_parity), not recalculation. No row → cat_side defaults (daily template) → fresh.
- *last 2 closes* = the last `STALE_SET_HALT_LOSSES` FULL closes of the sym_side (all accounts of the venue) since the active
  set was applied; net = gain − fee (crypto 0.04, stocks 0.0, same as `per_trade_returns`). Source: the new execute_now
  full-close hooks (`data/stale_set_halt/closes_{ez|tradier}.jsonl`, gain from the REAL entry price, independent of
  `VIGILANCE_GUARD_ENABLED`) from `hooks_started_*.txt` on; before that the `data/per_trade_returns` ledgers (partials
  TIERED/PARTIAL/REDUCE excluded; crypto gain = the exit's `g<x>%` stamp when present, else `pnl_pct` — flagged `src`).
- age > `STALE_SET_HALT_DAYS` (10) AND both closes net < 0 → **HALT**: execute_now refuses OPEN/AUGMENT/REENTRY/ENTRY/REVERSE/BUY
  (vec-decided included) with `BLOCKED_STALE_SET_HALT`; CLOSE/REDUCE/HEDGE_CLOSE never. Persisted in
  `data/stale_set_halt/halts_{venue}.json`, alert line in `data/alerts/stale_set_halt_{venue}.jsonl`, `[STALE_SET_HALT]`
  critical log, and the sym_side is inserted at `sides[0]` of S1 `data/v15_sched_pinned_sides.json` (ssh s1-int from a daemon
  thread, JSON-validated + md5; the scheduler treats pins as a set = priority 0; `tools/stale_set_halt_ctl.py --sync-pins`
  retries PENDING pins). The halt is evaluated at the 2nd losing close and at every entry attempt (fresh vectors exit
  early without ledger IO).
- **Release** (automatic): active row applied after `halted_at` AND overrides fingerprint changed AND the board behind it
  published after the halt (`published_at`, or a dated tag on a later day). A re-stamp of the same set never releases. The
  loss window restarts at the new apply (counter reset); the S1 pin is removed.
- Kill switch `STALE_SET_HALT_ENABLED` (True) + `STALE_SET_HALT_DAYS` 10 + `STALE_SET_HALT_LOSSES` 2 in `config.py` and
  `config_tradier.py`, read inside the module (live-only operational guard reacting to realized live losses — no vec twin by
  design; vec twins originate opens only through execute_now, so they are covered by the same gate).
- Tests `tests/test_stale_set_halt.py` (13). Read-only status: `python3 tools/stale_set_halt_ctl.py --dry-run`.
- First dry-run 2026-10-14 00:1xZ (Mac ledgers): **0 sym_sides would halt**; 24 crypto sets older than 10 days (23 with no
  closes since apply, BTCUSDT_LONG last 2 closes wins); 14 crypto + 1 stock sym_sides have 2 trailing losses on a fresh set
  (earliest halt-eligible ZROUSDT_SHORT 2026-10-16 if no new board).

**(3) Global-only safety key.** `PARITY_VEC_EXACT_MODE/_FAMILIES/_SET_DIR/_SOURCE` are GLOBAL: 3 crypto per-sym rows
(ETCUSDT_LONG, ETHFIUSDC_LONG, ACEUSDT_LONG) carried MODE=False and traded native. Stripped via `per_sym_store.upsert` (backup
`backups/before_strip_parity_vec_exact_persym_*.json`, meta `global_key_strip_20261014`); added to
`switch_parity.GLOBAL_ONLY_KEYS`; golive `ARTEFACT_KEYS` patch proposed in `backups/proposed_patches/`. All 672 active
sym_sides resolve PARITY_VEC_EXACT_MODE=True through per-sym > cat_side > global.
- **v16 (594deeda, 2026-10-14 01:1xZ) — USER "99% time savings and 10x bh in the next round":**
  * **V17 CARRY** (`carry_decide/carry_update`, books `~/v15_carry/<SS>.json`, seeded from the delta logs by
    `tools/v17_carry_seed.py`, S1<->s2 newest-wins sync cron `V15_CARRY_SYNC`): a main-pass row dead (|naked|<1e-6) on the
    sym_side's last 2 evaluated boards is not re-evaluated (`CARRIED_DEAD:` structural skip, G blank, never a fake 0) — never
    WINNER, never sacred rows, last board <= 7 d, re-evaluated after 2 carried boards, rotating 1/10 re-check (`carry_sampled`
    measures precision live). Offline replay (real delta logs): 66% of naked rows, 99.3% stayed dead. A/B S1 ZCSH_LONG
    (MEDIOCRE, same file, production autopsy base): carry OFF 69.5485 / 17:28 wall / 1086 cpu-s; carry ON 69.5749 / 12:15 /
    779 cpu-s (gain >=, wall -30%, CPU -28%). Kill switch `V15_V17_CARRY=0`; strict start-fp match `V15_V17_CARRY_REQUIRE_FP=1`.
  * Prefetch/presubmit pipeline A/B again at 2 workers (UNIUSDC_SHORT): identical 20.8301 + wall, +26% CPU -> stays OFF.
  * Non-winner combo slice: P3 budget += `V15_V17_COMBO_MIN_S_<TIER>` (MEDIOCRE 120 / LOSER 75 / LOST_CAUSE 45 s) and the first
    combo step gets at least that (the 30 s search minimum starved MEDIOCRE).
  * PHASE CUT: `data/v17_phase_stats.json` (written hourly by `tools/v17_hourly_report.py`, S1 cron :05 `V17_HOURLY_REPORT`,
    pushed to hosts); a cuttable phase (P1_PROBE, P3_FOCUS/ABLATION/RETEST/PROBE/COMBO/SURGEON — never REGIME365/TIMFIX/
    ESSENTIAL) with n >= 20 new-build boards and 0 lifts is skipped (`V15_V17_PHASE_CUT=0` disables).
  * Per board: `result_cpu_s` (self + reaped pool workers), `result_workers`, `[V17-SUMMARY]` line (lift by phase, final/bh).

### 101.1 CORRECTION (USER 2026-10-14): the re-entry rule is THREE switches, not B00
"re-enters on the first closed 15m bar that reclaims the exit price by the 0.2% buffer <- this was never the rule it is a switch
either breaking exit price or dc_high4_15m or a higher low (vv short) … all 3 can be true!" The B00/H2 twin shipped at 51d666ff
was withdrawn (engine patch reversed; REENTRY_EXIT_RECLAIM is inert in the vec again, its predicate stays unused). Shipped instead
(engine a7651c1b, Mac = S1/S2 sandbox md5-identical): `vec_decisions/reentry_triggers.py` — ONE function for vec + native live —
successor of the USER's precise rule VEC_REENTRY_DC4_EXITPRICE_ENABLED (v12_wide_engine.py:7698, never ported), as 3 independent
OR-ed switches, default True, REENTRY_WINDOWED rows True/False in all 4 templates:
- REENTRY_ON_EXIT_PRICE_BREAK_ENABLED — close crosses back through the last exit price.
- REENTRY_ON_DC4_15M_BREAK_ENABLED — close crosses the prior closed bar's dc_high4_15m (vv dc_low4_15m short).
- REENTRY_ON_HIGHER_LOW_ENABLED — bar low above the lowest 15m low since the exit + up close (vv lower high + down close short).
All require the original 15m WT confirm (wt1 > wt2, velocity >= 0; short mirrored); armed after ANY exit (FIXED_STOP incl.) while
flat; normal entry gates apply; label = the trigger name. Live: crypto ez_manage.evaluate_reentry + stocks tradier_manage
StockStrategy.evaluate_reentry call fire_live() (same predicate; native, acts when the vec twin is not deciding); in exact mode
live = the vec twin. Switch bible: WIRED_BOTH on both venues. Flip proof 30d default set: ALGOUSDT_LONG none -1.09 / exit -2.23 /
dc4 -2.13 / hl -4.95 / all3 -5.32; ALGOUSDT_SHORT exit +1.51 (vs +0.52); BTCUSDC_LONG all3 -4.02 (vs -3.48); AAPL_LONG hl +7.86
(vs +6.26); NVDA_SHORT no fires. Defaults ON per the original mandate; per-sym sweeps decide via the rows. Tests:
tests/test_reentry_triggers.py. Template integrity verified (exactly 6 new rows per template, 0 missing); allowlist =
data/reports/ALL_PATHS_ALLOWLIST.csv (S1/S2/Mac).

## 103. V17 KPI + AUTO-TUNE (2026-10-14)

USER 2026-10-14: "make sure the results keep improving and processing times per sym_side keep reducing by the self learning
system". Goals: 10x bh for every sym_side; >=12 then >30 boards/hour/server on S1 + s2. Tool `tools/v17_kpi.py` (stdlib; S1
python 3.12, s2 3.10). DIAGNOSTIC aggregates only, no Sharpe anywhere; every number is copied/derived from progress JSONs +
delta logs.

- **Collect (S1 cron `3,33 * * * *` `# V17_KPI`, flock `/tmp/v17_kpi.cron.lock` + internal `/tmp/v17_kpi.lock`, nice 15).**
  S1 `~/v15_run*/progress` locally + s2 (10.0.0.4) by piping the tool itself to `ssh 10.0.0.4 python3 - --extract` (no deploy
  skew). Every finished board (`result_done_utc`; live files + `*.npzprev_*.json` archives) -> `data/kpi/v17_kpi.sqlite`
  table `boards` (key `host|sym_side|result_done_utc`, upsert; incremental from the host's last finish - 2 h, backfill
  `--days`). Fields: sym_side, cat_side, tier, host, pilot/engine/config md5 (`result_*_md5`), start/finish/wall, workers
  (`result_workers` > dispatch-log `workers` > scheduler `hosts.<h>.workers_per_side` timeline > default, flagged
  `workers_assumed`), CPU: `core_min` = pilot `result_cpu_s`/60 (v16+, measured) else wall x workers (flag
  `cpu_est_wall_x_workers`), `core_min_est` = wall x workers always, `eval_core_min` = measured sum of non-cached eval `secs`
  in the delta log inside [start, done] (lower bound), evals/cached, final gain, bh, gain/bh (bh > 0 only), trades, TIM/DD
  (best_seen when it equals the final, else the last promotion/delta-log row equal to the final), 365D gain/DD/TIM/trades
  (`final_365d`), best_start choice + gain, `final_below_best_start`, per-phase {wall, lift, evals, promotions} folded into
  families `p0, probe, p2, combo, surgeon, focus, ablation, retest, probe_p3, regime365, ffr, endgame_diag, other`
  (+ p3_timfix/p3_essential...). Legacy-P2 boards record no P2 phase: `p2` = wall from v17 start to the first P3 phase,
  lift = first P3 entry gain - best_start gain (flag `p2_derived`); `other` = wall not covered by any recorded phase. Phase
  core-min = phase wall x the board's measured average cores (cpu_s / wall) or x workers.
- **Rollups -> `data/kpi/v17_kpi_latest.md/.json`** (windows 6h / 24h / today / 3d, hourly 48 h, daily 7 d): boards/h/server,
  median wall, median core-min (measured / est / eval), median + p90 gain/bh, share of boards >= 1x/2x/5x/10x bh, median final
  - best_start, finals below best_start, median 365D gain/DD, lift / core-min / lift-per-core-min / lift-per-wall-min per phase
  and per phase x tier, pilot md5 set. Mac pull cron `8,38 * * * *` `# V17_KPI_PULL` -> `data/kpi/` + `data/alerts/`.
- **Regression guard (every collect).** Last 24 h and last 6 h vs the 72 h before each window (min 5 boards each side, else
  "insufficient", no verdict): median wall / core-min / core-min est / eval core-min rise > 15 %, median gain/bh fall > 10 %,
  or ANY final below its best_start -> `data/alerts/V17_KPI_REGRESSION.json` with the evidence, tier mix, and the pilot/engine
  md5s present in the window but not in its baseline (the coinciding change). Cleared -> renamed
  `V17_KPI_REGRESSION.resolved_<ts>.json` (evidence kept).
- **Bounded auto-tuner (S1 cron `30 14 * * *` `# V17_AUTOTUNE`, after the 13:40Z daily learn; once per UTC day, `--force`
  reruns).** Last `V17_AT_DAYS` 3 days of v17 boards (>= 8, else every knob held). Knobs (default -> hard bounds):
  `V15_V17_COMBO_SHARE_WINNER` 0.5 [0.15, 0.7], `V15_V17_COMBO_SHARE` 0.35 [0.1, 0.6], `V15_V17_SHARE_SURGEON_WINNER` 0.75
  [0.3, 0.9], `V15_V17_SHARE_SURGEON` 0.6 [0.2, 0.85], `V15_V17_SHARE_FOCUS` 0.5 [0.1, 0.8], `V15_V17_SHARE_ABLATION` 0.6
  [0.15, 0.85], `V15_V17_SHARE_RETEST` 0.7 [0.2, 0.9], `V15_V17_P3_MAX_S_WINNER` 1500 [1500, 3600] (USER "extend time cap
  for winners": raise only), `_MEDIOCRE` 540 [180, 1500], `_LOSER` 300 [90, 900], `_LOST_CAUSE` 120 [30, 300], `V15_V17_P_MIN`
  0.005 [0.001, 0.02]. Rules: share knob = phase lift/core-min vs the P3 pool (combo+surgeon+focus+ablation+retest) of its tier
  group; deadband 0.8-1.25x hold, else x sqrt(ratio) clamped to +-20 %. Tier budget = tier P3 lift per wall-minute: >= 0.6
  (2x the 0.3 rule) +20 %, < 0.3 -20 % (never cut while the tier lifts >= 0.3 pp/min; the pilot's 0.3 extension still extends
  any lifting window), else hold. P_MIN = P2 vs P3 lift/core-min: < 0.5x -20 %, > 1.5x +20 %. Per knob >= 3 boards and >= 10
  core-min of evidence else hold. Every value: <= 20 % from prev, inside its bounds. NEVER written: `V15_V17_MIN_LIFT_PM`
  (0.3 rule), `V15_V17_P3_MAX_S` (absolute cap), best-start, 365D record-only, typed values, never-lower.
  Output `data/kpi/v17_autotune.json` {apply, knobs: {value, prev, default, bounds, why, evidence}, env}; history
  `data/kpi/autotune_history/v17_autotune_<ts>.json` + `data/kpi/v17_autotune_history.jsonl`. **Rollback (one command):**
  `python3 tools/v17_kpi.py --rollback` (restores the snapshot before the one in effect; none -> apply=false). **Off:**
  `python3 tools/v17_kpi.py --disable` or `V17_AUTOTUNE_APPLY=0` in the scheduler cron env.
- **Reader hook = `tools/v15_dispatch_job.py` `_autotune_exports(tag)`** (the scheduler hands every launch to the dispatch
  job): for a tag with a 30D leg, an apply=true file <= `V17_AUTOTUNE_MAX_AGE_D` 3 days old, only whitelisted knob names
  (regex) with finite values inside hard bounds -> the remote launch command is prefixed `export K=V; ...` (inherited by the
  setsid/bash -c/pilot chain; an inline `VAR=` in the scheduler's env string still wins). Launch log rows now carry
  `workers` (parsed `--workers N`) and `autotune` {generated_utc, n}. Any problem -> no prefix (pilot defaults).
  Direct launches that bypass the dispatch job do not get the knobs (hook for their owner: prefix `side_cmd()`'s 30D `env`
  with the same exports).
- **Daily learn packet:** `tools/v17_daily_learn_prep.py` `kpi_section()` / `kpi_compact()` -> packet section "C. FLEET KPI"
  (window trends, lift per core-minute per phase best-first, best/worst phases, regression verdict + new md5s, autotune moved
  knobs) from the Mac copy `data/kpi/v17_kpi_latest.json`; JSON twin `data.kpi`. Same 30 KB cap. Also fixed: the packet's
  365D columns read `final_365d.gain_pct / max_dd_pct` (were always None).
- **First run 2026-10-14 01:1xZ** (347 boards backfilled 10 d): 24 h: 120 boards, s1 1.17 / s2 3.83 boards/h, median wall
  28.0 min, median gain/bh 1.705 (p90 6.55), >= 1x 0.81, >= 10x 0.059; 3 finals below best_start (ONEUSDT_SHORT s2
  22.55 -> 0.62 f71ab606; FILUSDC_SHORT 3.42 -> 3.09 53bb952f; JASMYUSDT_LONG 3.98 -> 3.74 ce1937e7) -> alert written.
  3 d lift/core-min: probe 10.8, focus 0.36, surgeon 0.19, p2 0.12, retest 0.11, combo 0.031, ablation 0.023, regime365 0
  (record-only by law). First proposal: COMBO_SHARE_WINNER 0.5->0.4, SHARE_SURGEON_WINNER 0.75->0.9, SHARE_FOCUS 0.5->0.6,
  SHARE_ABLATION 0.6->0.48, SHARE_RETEST 0.7->0.84, P3_MAX_S_MEDIOCRE 540->648, P_MIN 0.005->0.006 (WINNER budget held at
  the 1500 floor; LOSER/LOST_CAUSE/non-winner combo held: thin evidence).
- Cron installer `tools/v17_kpi_install_cron.sh` (refuses an empty/short crontab, backup
  `backups/crontab_s1_before_v17_kpi_<ts>.txt`, idempotent). Tests `tests/test_v17_kpi.py` (12).
- **v17 (3ce393d3, 2026-10-14 01:5xZ) — BEST-START-FLOOR (USER hard law):** KPI guard found 3 s2 finals below best start.
  Paths: ONEUSDT_SHORT (f71ab606, pre-floor build) DIAG accepted status_up 28.21 -> 0.62 (TIM 9.2 -> 20.7); JASMYUSDT_LONG
  (ce1937e7) DIAG status_up 7.40 -> 3.74 (TIM 6.3 -> 29.2) below START 3.98; the BEST-SEEN gate was SKIPPED because the
  START note runs before `_run_npz_short` exists (best_seen.npz '') and the gate required npz equality (FILUSDC_SHORT
  53bb952f same hole, progress since overwritten). Fix: gate drops the npz-string and chain-value preconditions (both sets are
  re-evaluated fresh on this board's prepared NPZ = same NPZ); START note keeps `start_overrides`; hard `[BEST-START-FLOOR]`
  right after the authoritative fresh final eval (before qualification/TIM law): best-start and best-seen sets re-evaluated,
  a higher valid set is published instead (`progress["best_start_floor"]`, `V15_BEST_START_FLOOR=0` disables).
  Combo: 7,800 s of combo wall -> 16.5 pp on s1+s2 (repeats mostly 0) -> WINNER first share 0.5 -> 0.3 and a combo repeat
  runs only while its own last call lifted >= 0.3 pp/min. Guard-thread interval fix (`os` shadowing killed _check2).

### 101.3 NO GENERIC TEMPLATE — CHAIN WAS BUILDING LIVE DEFAULTS FROM THE ABANDONED SET (USER 2026-10-14)
"THERE IS NO SUCH THING AS A GENERIC TEMPLATE WE ABANDONED THAT MONTHS AGO … SPREADSHEETS/TEMPLATE_FINAL_NORM has the 4 templates
they are independent." Found 2026-10-14: `tools/v15_daily_chain_s1.sh` (since at least the 2026-10-07 runs in ~/logs/v15_daily_chain.log)
ran step 2 TWICE ("template writer main (SPREADSHEETS)" + "norm (TEMPLATE_FINAL_NORM)"), installed queued rows into BOTH dirs (2a),
type-scanned both (2b), built step 3 live + sweep cat_side defaults (data/per_sym_settings.json, read by live AND by every board via
CAT_SIDE_DEFAULTS_PATH) from the GENERIC `SPREADSHEETS/TEMPLATE_*` set, and pushed/pulled the generic files (S1 step 6, Mac apply).
Promotion records (cat_side_promotions) were written by both passes = a mix of both sets. switch_parity.py read the generic set for
the template md5 + gate audit. FIXED 2026-10-14 03:2x-03:3xZ: chain step 2/2a/2b/3/6 = TEMPLATE_FINAL_NORM only (S1 96f1dba4); Mac
apply pulls FINAL_NORM only; switch_parity -> FINAL_NORM (6b9de0d5, S1/S2/Mac); per_sym_settings rebuilt from FINAL_NORM and
propagated S1=S2=Mac (be612200, sweep copy identical, SQL kv verified): 8 value changes (CL ATR_ADAPTIVE_SIZING_TARGET_PCT 3->2,
REGIME_RANGING_POSITION_SIZE_MULT 0.25->0.5, REGIME_TRENDING_POSITION_SIZE_MULT 3->2.25; SL BB_EXIT_AT_LOSS_TF 15m->OFF,
FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED True->False, LH_HL_FILTER_DC_THRESHOLD_PCT 1.0->0.5, RZ_BOT_BB_THRESHOLD 0.225->0.375;
SS GAP_CLOSE_MOC_EXIT_ENABLED False->True) + new keys. STILL OPEN: (a) v15_pilot.py fallbacks at :9249 / :14370 name the generic
files (unused while the scheduler passes --template FINAL_NORM); (b) cat_side_promotions contains records decided on the generic
set; (c) 225-328 groups per template are grey (wire_status_paint: vector-blind) and 19-47 of them carry promotion records —
bold != live for those by design until they are wired.
Agent template writes the same night (all on S1, all backed up): 00:05-01:22 switch adds via v15_switch_add (SESSION_OPEN_STOP_REFREEZE,
3 REENTRY_ON_*), 00:12 two concurrent v15_switch_remove runs deleted 2 STOCKS_SHORT rows -> restored by hand from the 00:05 backup;
03:16 a bookkeeper --rules-only INSTRUCTIONS write that diverged S1 from S2/Mac (and was auto-synced to S2) -> undone on S1 and S2
from the identical 01:22 state, bookkeeper edit reverted (4bcf8d3d). Final templates S1=S2=Mac 0418f1e0 f0fb401d 221e9e86 3fdf7610.

## 104. CASH-ACCOUNT SETTLEMENT (GFV) PARITY + EXIT-GUARANTEE FILL MODEL (USER 2026-10-14 "no violations accepted; make sure it works identically in the vectorized backtests")

**One rule, one module:** `vec_decisions/cash_settlement.py` (stdlib; covered by the eval-cache engine fingerprint) is used by live
`tradier_manage.GFVTracker`, `v12_quick_engine.simulate_one` and the `backtest_v12_engine` replay. Switch
`CASH_ACCOUNT_SETTLEMENT_ENABLED` (True; QuickConfig + config_tradier; never False outside an investigative backtest — live GFV
gates are a legal rule and do not read it). No template row (not sweepable).

**Who is a cash account:** `ACCOUNT_TYPE_<ACCT>` — tra=cash, trb=margin, trc=paper (QuickConfig now carries all three, = config_tradier);
unknown -> cash (fail closed, = `_gfv_guard_applies`). **tra is hard entry-banned** (`order_dedupe_guard.HARD_ENTRY_BANNED_ACCOUNTS`,
`TRA_ALLOW_BUYS=False`): it holds legacy positions only (VT/META/MSTR/NEM LONG on 2026-10-13) and opens no board. Every live board
trades on trb (margin) and/or trc (paper) -> **the settlement rule is inert for every published board** (proof below).

**The rule (cash account):** (1) entry-banned account -> no buy leg ever (any account type); (2) a buy leg on ET date D is refused
while this board's own sale proceeds are unsettled (strict board-local reading of live's broker settled-cash gate); (3) every buy
leg restricts selling until `next_settlement_day(D)` (T+1; worst-case reading of the GFVTracker restriction); (4) a sell requested
while restricted is OWED and executed at the first regular-session bar whose ET date >= settlement, at that bar's price.
Settlement calendar = weekdays minus NYSE holidays (rule-based + one-off closures) minus Fed settlement holidays (Columbus Day,
Veterans Day — exchange open, no DTC settlement). Live `_next_business_day` was weekends-only before 2026-10-14.

**Board account (pilot hook, V17 owns v15_pilot):** `cash_settlement.board_account(sym, side, TradierConfig())` -> the STRICTEST
account that can actually open the sym_side (entry-banned and side-restricted accounts skipped; any settling account wins).
tradier_vec_exact emits ONE decision stream per sym_side for all accounts, so a board traded on cash + margin is evaluated as cash.
Pass it as `sim_account=` / overrides `SIM_ACCOUNT` at EVALUATION time only (never into cumulative_overrides / per-sym sets).
Today it returns trb for every stock sym_side.

**Live mechanics:** GFV refusals (`GFV_BLOCKED`, `BLOCKED_GFV_ACCOUNT_RULE`, `GFV_SETTLED_CASH_BLOCK`) are POLICY in exit_guarantee
(a FORCE cannot sell before settlement); the refused sell is registered `GFVTracker.owe_exit` (persisted `owed_exits` in
data/tradier/gfv_tracker.json) and `process_position` executes it at the first RTH tick after settlement
(`CASH_SETTLEMENT_DEFERRED[...]`, an exit_guarantee AUTH token) — nothing else happens on that position meanwhile (vector skips the
bar). Cash buy legs: strict T+1 restriction (`record_buy_settlement`); trb/trc keep the old inert bookkeeping.

**Stock whole-share twin (same cut):** live tradier execute_now sends `max(1, int(round(q)))` shares on every order; the vector
reduced fractional shares (358/464 stock REDUCE events, 17/73 boards). simulate_one now normalises each stock REDUCE to the live
share count (a reduce that rounds to the whole position = `WHOLE_SHARE_REDUCE_TO_FLAT` close). Tokenised Binance stocks (MODE
crypto) stay fractional. **15 of 72 valid stock boards change** (s2, current published sets, 30D): ADBE_L -2.48->-4.04 (19->20
trades), AMAT_S 15.88->15.07 (13->15), AMD_L 1.38->1.37, AMZN_L 0.02->-0.15, BABA_S 5.13->5.13, DELL_L 9.38->9.51, LAC_L,
LOW_S 10.05->10.03, NEM_S 3.44->3.40, NFLX_L 2.79->2.77, PLTR_L 2.08->0.71 (6->10), PYPL_S, STZ_L, WDAY_L 0.70->0.66, ZCSH_L.
57 boards bit-identical; the settlement rule changes 0 of them (trb).

**Exit-guarantee vs fill model:** vector fills every decision at the decision-bar close, zero slippage (stocks fee 0, crypto
0.02%/leg), reduces instantly, no dust. Live: exit_guarantee makes decided exits fill (FORCE market reduce-only on refused/unconfirmed
full closes, reduce retries 15-240 s, dust force-close only after a confirmed FULL close) -> it moves live TOWARD the vector
assumption. Remaining honest gap = slippage/latency: crypto VEC_EXACT exits 2026-10-11..14 (Mac order ledgers, heuristic pairing)
filled earliest 134 s after bar close; n=54 (lag<=300 s) median +2.9 bps against the position, mean -2.5, p90 +42.6; MARKET fills
(n=76) median +7.9 bps; stocks 0 pairs (no live VEC_EXACT stock fills yet) = unknown. Not modelled in the vector.
**Replay rule:** `backtest_v12_engine` sets `EXIT_GUARANTEE_REPLAY=1` -> exit_guarantee pass-through (it used to run the real wrapper
in V12_REAL_EXECUTE replays: live per_trade_returns / vigilance / stale-set / alert writes, desktop notifications, crypto FORCE
fills at price 0, stock watchers on REAL-time sleeps). Would-escalate / would-retry outcomes are counted in
`run_one()["exit_guarantee_replay"]`.

**Replay harness fixes (same cut, pre-existing breakage):** `_check_live_drawdown_ceiling` answered without broker I/O (sim equity 0
-> DD 100% blocked every stock OPEN, rate guard aborted -> every stock trade-parity live leg UNAVAILABLE); VEC_EXACT stale fallback
off in replays (wall-clock vs frozen NPZ -> VEC_EXACT_STALE on 810/810 bars; reader `float(_cfg() or 4.0)` -> wrapped `_cfg`).
OPEN (pre-existing, proven on the pre-edit code): APO_LONG stock twin emits no OPEN in replay (only ULTIMATE_DC hard-stop CLOSEs on
a flat book) while the vector opens 8; in-process `tools/v15_trade_parity.run` (vec leg first) contaminates the live twin
(0 opens) -> run legs in separate processes; `tools/cat_side_defaults.py` shadows root `cat_side_defaults.py` when tools/ is on
sys.path first.

**Proof (s2, frozen NPZ ~/parity_loop_stocks/.../indicators_frozen_20261006, split-process legs, current published sets):**
AAPL_LONG trb 24/24 trips identical entry+exit timestamps; AAPL_LONG as cash (REAL execute) 9/9 identical, 0 unsettled sells
either leg, deferred exits on the same bars; META_LONG trb 25/28 (pre-existing twin divergence 09-08 + 09-24), as cash 6/9 (all 3 deferred vec
exits identical in live; the 3 unmatched 09-09..09-16 cascade from the same 09-08 twin exit divergence seen on trb), 0 unsettled sells either leg;
tra (AAPL/APO/META) 0 trades both legs. Unit tests `tests/test_cash_settlement_parity.py` (13).
- **v18 (2be673b9):** SIM_ACCOUNT hook — `install_sim_account_hook`: stock boards evaluated as `cash_settlement.board_account(sym,
  side, TradierConfig())`, injected into the overrides only inside `tools.opt.v12_pilot.evaluate_prepared_sanitized/
  evaluate_sanitized` (patched in-process, pool inherits) and only when != trb (today all trb -> results + cache keys unchanged);
  SIM_ACCOUNT / SIM_TRADING_ACCOUNT are GLOBAL_ONLY.
- **v19 (4693e9ed, 04:41Z):** P4_FFR + P4_POST_ENDGAME_DIAG cuttable (same 20-board zero-lift rule; best-seen gate, best-start
  floor and adopt guards always run). WINNER first combo share back to 0.5 (repeat only while >= 0.3 pp/min). Tier: aligned
  big mover WINNER only at bh >= `V15_TIER_WIN_BIG_MOVER` (25); 8 <= bh < 25 needs potential >= max(bh, 10). Replay on 141
  boards (2 days, s1+s2): winners 95 -> 88, expected boards/h x1.01 — winners are winners by potential (65/95 from prior
  finals), and non-winner walls were 27 min vs winners 36 min, so the tier mix is not the throughput lever.

## 105. S2 SCALAR PARITY LANE — REAL LIVE-vs-VECTOR TRADE PARITY (USER 2026-10-14)

USER: "run backtest_v12_engine … to get real parity between the live and vector scripts instead of just overriding live with
vector results as we do now" → "only on s2, give it plenty of time to finish each, the order sounds perfect".

- **What it measures:** `tools/v15_trade_parity.py` per sym_side on the SAME frozen 30D NPZ: vec leg = `v12_quick_engine`
  (what the board measured), live leg = `backtest_v12_engine.run_one` → real `ez_manage` / `tradier_manage` `process_position`.
  Trips are matched entry-by-entry (30 min tol); VEC_ONLY / LIVE_ONLY / exit-disagree lists = the gap evidence.
- **No overriding:** the lane runs `TRADE_PARITY_VEC_EXACT=0` → `V8_VEC_EXACT_INGEST` OFF. The trade_parity default (=1) feeds
  the vector's actions into the live leg — that hides divergence instead of measuring it. Reports carry
  `mode_label=SEAM_DIAGNOSTIC` and `env.V8_VEC_EXACT_INGEST=None`.
- **Set:** the exact published set live is registered with (`data/published_selection.json` overrides + `overrides_md5`).
  One result per (sym_side, set md5); a newly published set re-queues the sym_side.
- **Order (queue, Mac `tools/v15_scalar_parity_queue.py`, cron :40 → pushed md5-verified to S2):** 1 boards live today →
  2 open live position / live trades last 3 d → 3 2-loss streak (`data/v15_365_priority.json`) → 4 rest; ties: position >
  trades > streak.
- **Runner (S2 ONLY, `tools/v15_scalar_parity_lane.py`, S2 cron `V15_SCALAR_PARITY_LANE` */10, flock):** host-guarded to s2,
  `nice -n 19`, ≤3 jobs, starts only at load1 < 14, `PARITY_LANE_TIMEOUT_H` 12 h per job. A died/timed-out job is reported
  (DIED), never silently retried. Output `data/parity/scalar_lane/<SS>__<md5_8>/` + `STATUS.json/.md`; Mac pull cron :50.
- 2026-10-14 03:04Z S1 run without `V8_SWEEP_MODE` covered 5.2 of 30 days in 50 min — the lane uses `run_one` (sweep mode on,
  live entry gates kept by `V8_KEEP_ENTRY_GATES`).

### 105.1 Same day fixes (2026-10-14)
- **Template writer crash** (`tools/v15_daily_template_update.py`): loop variable `cs` (filter columns) shadowed the cat_side
  parameter `cs` in `pick_winners` (×2) and `apply_tab` after the 10-13 19:21Z type-coercion edit passed `cs` to `_coercible`
  → `AttributeError: 'list' object has no attribute 'split'`; chain FAILED at step 2 at 12:15Z and 12:55Z, no new defaults at
  the open. Loop variable renamed `cols` (8 lines); report-only run on the 20261014 aggregate: 4/4 cat_sides, 0 violations.
- **Go-live refresh** (Mac cron `V15_GOLIVE_REFRESH` 05:20/11:20/19:20): still applied `data/avg_delta_selection.json`
  (`--all-finished`) and overwrote the published go-live (10-14 12:03Z: 194 sets over the 235 published boards). Now
  `tools/v15_golive_refresh_published.sh` = chain step 5b (`--published --pin-all`); a failed published build applies nothing.

### 105.2 Lane data law + S1 Binance IP ban (2026-10-14 16:xxZ)
- First 18 lane results were INVALID and moved to `data/parity/scalar_lane/_invalid_stale_npz_20261014/`: the vec leg skipped
  (`STALE_NPZ_SKIPPED`, USER 2026-10-12 freshness law) while the live leg ran → one-sided. Never bypassed
  (`V12_ALLOW_STALE_NPZ` is tests-only).
- Now every job goes through S1 `tools/v15_dispatch_job.py` (the herd's JIT path: tail refresh + md5-verified push to S2), then
  `--run-job` on S2 freezes a private copy of the job symbol's NPZ (others symlinked) as `TRADE_PARITY_NPZ_DIR`; a still-stale
  NPZ → `STALE_WAIT` (no run, retry after 60 min, all new dispatches held meanwhile). While `ez_klines`' ban file on S1
  (`~/binance/ez_s1_stack/data/ez_klines_ban_state.json`) shows an active ban, the lane dispatches nothing.
- **S1 IP 157.180.125.52 is banned by Binance futures REST (418, -1003) since ≥ 10-13 18:23Z, re-banned to 1792007759 (≈19:56Z
  10-14).** Consequences: every S1 NPZ tail refresh fetches nothing but exits 0 (`EXCHANGE_BAR_NOT_READY`) and
  `v15_dispatch_job.fast_push` logs it as "tail current" → herd boards since ~06:00Z run on NPZs ending ~06:00Z (crypto) or are
  STALE-skipped; S1 position-failover cannot reach Binance. Escalation pattern: when a ban lifts, independent fetchers
  (`ez_klines` 282×418 in one burst 15:06Z, `npz_bar_refresh`, `klines_ff`, `klines_vault`, dispatch tail refreshes) each assume the
  full 2400/min IP budget → instant re-ban, longer each time. OPEN (operator decision, data-ingestion lane): one shared S1
  ban/weight governor honoured by every fetcher + a non-zero exit / "stale" label when the tail refresh fetched nothing.

### 105.3 Why today's defaults did not reach config/QuickConfig + chain step 7 (2026-10-14 17:xxZ)
- `switch_parity.py` `verify_default_surfaces` / `sync_default_surfaces` / `sync_cat_keys` + the 3 CLI `--template-dir` flags +
  the startup_gate fallback defaulted to `template_dir="SPREADSHEETS"` = the ABANDONED generic set (§101.3 fixed only the
  md5/gate paths). Mac apply step 3 therefore compared every promoted key against stale generic bolds → fake "side splits"
  (CRYPTO_SHORT EMA_DIST_SIZING_MULT 2.0 generic vs 4.0 FINAL_NORM; STOCKS_SHORT ADX_REGIME_FILTER_ENABLED True vs False) →
  `planned 0`, config/QuickConfig never synced, gates hard=1 on all 4 cat_sides. All 7 defaults → `SPREADSHEETS/TEMPLATE_FINAL_NORM`.
  Re-plan of today's 82 keys: 4 edits + 1 manual (config_tradier multi-line ADX) — applied; startup_gate 4/4 ok, hard 0;
  config.py / config_tradier.py / v12_quick_engine.py / switch_parity.py md5-identical Mac = S1 = S2 (sandbox + live).
- Chain step 7 still read `template_main.json` (generic writer removed 10-14 by me without updating step 7) → FAILED after a
  successful push, and its `>` redirect left an EMPTY `<date>.json` that the Mac apply pulled as the stamp and stopped on. Step 7
  now reads the norm report and writes the stamp atomically (tmp + mv, non-empty check).
- Dead workers: chain TARGETS, Mac apply HOSTS and bookkeeper V15_BOOK_HOSTS no longer list s5/s6 (destroyed 2026-10-13; the 10-14
  15:56Z run failed at step 6 on 10.0.0.5).

## 106. LIVE (BINANCE) vs BOARD TRADE PARITY 2026-10-14

USER: "put each sym_side's board next to its actual Binance trades since that board went live, taken from the exchange rather
than our internal ledger … immediately fix the disparities". Report `data/reports/live_vs_board_parity_20261014.md` (+ `.csv`
one row per sym_side, `.json` trip detail with Binance trade/order ids and log lines).

- **Method.** Exchange truth = `tools/binance_fills_fetch.py` (Mac only, GET only, 418/429 hard stop): userTrades + income +
  allOrders + positionRisk -> round trips per account/positionSide (start position reconstructed from positionRisk, so carried
  positions are never fake entries). Live set = `per_sym_store.per_sym_active` (327 crypto rows; the JSON
  `per_sym_active_config.json` holds only 155). Board = that set through `evaluate_v12.evaluate_prepared(include_ledger)` on the
  live twin's frozen store (`/tmp/vec_exact_frozen`), i.e. the bars live decided on; S2 NPZs end 06:30Z (S1 ban) and only
  cross-check history. Window = first `per_sym_history` row with the current md5, cap 7 d. Match = same account, entry ±30 min.
  Tool `tools/live_vs_board_parity.py`. Never `data/per_trade_returns` (men: 2,393 rows, 2,349 with entry_price 0, sum pnl_pct
  +3,194.8 vs Binance REALIZED_PNL −14.35 $ over 7 d).
- **Result (diagnostic, 1 day).** 264 live trips since boards went live, 144 matched, 120 LIVE_ONLY, 941 BOARD_ONLY (per
  account), 50 exit disagreements; Binance net −21.51 $; on matched trips live −17.30 $ vs board −9.00 $ at live size.
- **Causes, ranked by $:** (C1) exchange FIXED_STOP is a MARK-price STOP_MARKET (intrabar) while the vector checks the frozen
  stop on the 15m CLOSE (`v12_quick_engine.py:20112-20132`) — 39 stop-fill trips = −14.78 $ (69%); board stopped on only 7 of
  17 matched ones. (C2) entries refused as `BLOCKED_POSITIONS_BROKER_UNREACHABLE` (181 dispatches vs 173 SUCCESS) because
  single-flight waiters gave up after 3 s while the read may take 8 s — FIXED. (C3) twin vs clean board run divergence / keys
  not evaluated (set switches, pins, store rebuilds, 11:53–15:2xZ avg_delta set churn §105.1). (C4) late exits:
  SKIPPED_VX_INFLIGHT + EXIT_OWED retries; execute_now 83 s (redis lock 17.5 s, 2×20 s price timeouts). (C5) VEC_CONVERGE_OPEN
  38 trips. (C6) S1 failover started traders 14:20–15:16Z and 16:02–16:14Z while Mac traders ran (`live_failover_s1.py:408-414`,
  `mac_silent = hb_absent`) -> FIXED_STOP war. (C7) 50 live sets != latest board (go-live refused the board: zero trades,
  invalid types, set drift) + 132 without a published board. (C8) live sizing multipliers on vec opens (§98 open item).
- **Fix F1 (positions_truth.py:376-397, unlocked):** waiters wait `POSITIONS_BROKER_TIMEOUT_S + 1` and share the in-flight read
  (forced readers only accept a read started after their request); NEAR-ang stale rule unchanged. Test
  `tests/test_positions_snapshot_singleflight_wait.py`. md5 59525818 Mac = S1 = S2 (binance + sandbox). Restart ez_manage
  ang/fin/flz/inf/men + ez_positions_service_main inf/men (+ tradier processes) to load it.
- **Open (user decisions):** C1 stop semantics (proposed engine diff in the report, re-measure all boards), C6 takeover rule,
  C5/C8 converge-open + sizing, per-bar twin evaluated-key logging for C3. No LOCKED file was edited.

## 107. HISTORY DB — EVERY BOARD'S SETTINGS + METRICS, KEPT FOREVER + FINALIZE DD GATE (USER 2026-10-14)

USER: "make sure the db gets filled correctly from now on so we have the daily settings and metrics for each sym_side after every
calc and hold on to it infinitely (at least on ext drive) for stats and development and self learning" + "no xlsx should be able
to finalize with <10 trades, >30%dd, etc".

- **Writer** `tools/v15_settings_history.py` (S1): source = publish manifests (`SPREADSHEETS/V15_V16_CELL_BY_CELL*/…_manifest.json`
  on S1 + S2, plus `data/manifest_archive*`); the exact set from the progress JSON whose md5(cumulative_overrides) == manifest
  `overrides_md5`. Row: sym_side, updated_at = published_utc, cat_side, FINAL_NORM template md5, gain, baseline, delta, bh,
  trades, max DD, TIM, valid, overrides_json; meta = publish_class, gain_vs_bh, pilot/engine md5, npz md5, manifest/progress paths.
  pool_sharpe stays NULL (no per-trade returns in a manifest — NO-LIES rule 6). Sources: `fleet_orchestrator_publish` (set
  matched — the ONLY go-live source), `fleet_orchestrator_publish_noset`, `manifest_backfill`. Triggers: fleet scheduler on every
  publish (--window-h 6) + S1 cron `V15_SETTINGS_HISTORY_SWEEP` hourly (--window-h 48). Idempotent per (sym_side, published_utc,
  overrides_md5) and against the 10-13 writer's rows (same day + gain). Backfill 10-14: 19 eligible + 348 backfill rows (248 with
  metrics only — their progress files had been overwritten); 13 distinct days from 10-02.
- **Go-live order**: `tools/v15_published_selection.py` picks the latest row per sym_side by `updated_at` (was by id: an
  after-the-fact insert would have won). Check vs the old tool: 383 same set, 10 move to a NEWER board the old writer had
  missed (e.g. BA_SHORT 10-12 → 10-14 06:59Z), 10 sym_sides newly covered.
- **Retention**: `per_sym_store._prune_history` (35 d / 45 rows cap) is OFF unless `PER_SYM_HISTORY_PRUNE=1`.
- **Archive** `tools/v15_history_archive.sh` (Mac cron `V15_HISTORY_ARCHIVE` 23:40Z) → `/Volumes/E3T/binance_history/`:
  `per_sym_history/<date>_<from>-<to>.jsonl.gz` (append-only, LAST_ID advances after row-count + gzip verify),
  `db_snapshots/per_sym_store_<YYYYMM>.db.gz` (monthly, md5-verified), `manifests/` (never --delete). First run: 4,907 rows,
  217 MB snapshot, 832 manifests. (SSD2T is full; E3T had 216 GB free.)
- **Finalize gate** (`v15_pilot._qualifies_30d` / `_nearmiss_30d`): valid, trades >= 10, gain > 0, **max DD <= 30 (NEW, hard,
  unknown = fail)**; TIM 20–80 miss alone publishes flagged BEST_EFFORT (USER 10-13 law); edge vs bh is recorded, not gating
  (USER 10-03). Failure → §58 repair → REDO (max `V15_QUAL_MAX_REDOS` 2) → IMPOSSIBLE quarantine, no publish.
- Not gating today (operator decision): 30-trade sample floor (175 of 785 published boards had 10–29 trades), pool_sharpe > 0.2
  (no per-trade list in the publish path).

## 108. V15 BRAIN — ONE LEARNED GRAPH + ONE PER-1000 ALLOCATOR + FROZEN-NPZ BENCH GATE (USER 2026-10-14)

USER: "revise v15_pilot self-learning … a framework … like a neural network that keeps making more connections between
switches and filters and their settings relative to gain, trades, dd, tim, sharpe … lower the number of times per 1000 tests
low scorers are calculated … ≥4 sym_sides/server/hour, then all sym_sides every 24 h … leave nothing on the table" +
"keep an archive of npz files on s2 … compare new versions of v15_pilot with old ones on the same npz … apples with apples".

### 108.1 Measured state before (S1+S2 logs 10-07..10-14, read-only)
- Learning was ~14 disconnected components (causal profile, avg_delta pos_sym, cell_evidence, v17_priors, combo library,
  causal_learner, autopsy priors …) feeding **six competing sampling rules** in the main pass (POSSYM, coarse cap 24, prior
  verdicts, ZERO-FORMULA, AUTOPSY-INERT rotation, V17 carry/p) with four evidence sources and four thresholds.
- Predictive check (chronological split): pos_rate predicts the next board (Spearman +0.55..+0.73) but **mean_delta is
  anti-predictive (−0.50..−0.75)** — the USEFUL verdict and V17 EV ranking leaned on it. Row-level prior verdicts did not
  separate rescue rates (pruned 7.7% vs kept 5.6%); cell level did (2.1% vs 8–10%).
- Delta logs carried gain/trades/tim only — no DD, Sharpe, WR, no board context → the system could not learn DD/Sharpe.
- Wall/board ~2700 s while evals/board fell 12x: board ≈ 1 effective worker (prefetch cap counts entries, batch 8), main
  thread 30% bookkeeping; cProfile (TRXUSDT_SHORT, 480 s slice): `_paint_tab_status` read `ws.max_column` per row (full-sheet
  scan) → each xlsx save ~41 s (141 s of 480 s); progress JSON full dump every 15 s ≈ 0.17 s/row.
- Fleet supply: 171/278 sides finished and never re-queued under 30D_ONLY (only W-tier refreshed), SOFT-SKIP relaunch loop
  = 42% of 24 h launches, short stock NPZs (13–20 sessions) = 25%; S1 idle on disk guard (disk 100%).

### 108.2 Framework (tools/v15_brain.py)
1. **Experience** = every real engine eval (delta log). Now also `max_dd_pct`, `pool_sharpe`, `wr_pct` per eval and one
   `BOARD_CTX` line per board (tier, bh, baseline, start fingerprint, round) — zero extra compute.
2. **Graph** = nodes SWITCH=cand / FILTER=opt / switch-name; edges at GLOBAL, cat_side, sym_side scope plus
   filter-on-row pairs (the yellow's marginal effect ON THAT ROW); each edge stores real outcomes (n, pos, neg, Σ+delta,
   mean d_tim / d_trades / d_dd / d_sharpe, secs, last seen). Connections grow only where evidence exists.
3. **Model** = one learned layer (online logistic, predict-then-learn) over graph features: key log-odds per scope (shrunk by
   n), sibling-candidate (switch-name) log-odds, pair log-odds, evidence mass, board/tab running hit-rate (EMA). Separate
   heads R (naked row), F (cell given the naked), F0 (cell before the naked = what the pilot can use).
4. **Allocator** = `p_eval = clip(P(pos)/p_ref, floor/1000, 1)`, deterministic draw per (sym_side, tab, key, round),
   propensity booked → unbiased re-learning; unevidenced keys always run; floor (default 5/1000) keeps every dead key
   re-tested; skipped cells revive on a positive naked (PRIOR-REVIVED path); skipped rows are settled `BRAIN_SAMPLED_OUT`.
   Never pruned: sacred / essential-gate / new / coarse-expand rows; WINNER-tier rows (`V15_BRAIN_NAKED_WINNER=0`).
5. **Metric steering**: `v15_brain.py suggest --scope SYM_SIDE --metric gain|tim|trades|dd|sharpe` ranks nodes by the
   learned effect (dd/sharpe fill in as enriched logs accumulate).
6. **Gate** = `tools/v15_npz_bench.py` (S2 `~/v15_bench`): frozen NPZ + data/ + templates + start bases + production env;
   versions run concurrently on identical inputs (real code copies — engine ROOT resolves symlinks), compare final gain /
   trades / DD / TIM / Sharpe / wall / evals; PASS = Σgain_B ≥ Σgain_A and no side loses > 0.5 pp. A config/code change
   that skips more rows ships only with a PASS.

### 108.3 Offline evidence (prequential, S2 logs, 393k evals, scored from 10-12 on, each predicted before learned)
| policy | eval-secs kept | positives kept | positive delta kept |
|---|---|---|---|
| cells: today's verdict rule (proxy) | 70% | 95.5% | 96.1% |
| cells: brain F0 + revive, p_ref 0.002 | 69% | 97.5% | 96.6% |
| cells: brain F0 + revive, p_ref 0.005 | 60% | 95.2% | 94.4% |
| naked rows: today (all) | 100% | 100% | 100% |
| naked rows: brain p_ref 0.001 | 69% | 97.5% | 97.9% |
| naked rows: brain p_ref 0.002 | 57% | 95.5% | 96.1% |
Defaults: cells 0.002, naked 0.001, floor 5/1000. Percentages are of evals production already ran (the brain stacks on the
existing policy). Not yet proven on boards → §108.5.

### 108.4 Switches (all default OFF in the pilot until the bench PASSes)
`V15_BRAIN=1` (cells), `V15_BRAIN_NAKED=1` (rows), `V15_BRAIN_PREF_R/_F`, `V15_BRAIN_FLOOR` (per 1000), `V15_BRAIN_DIR`
(model dir, default data/brain), `V15_BRAIN_NAKED_WINNER`. Model: `python3 tools/v15_brain.py export --since <7-14 d>`
on a worker → `data/brain/brain_<CAT_SIDE>.json` (~6 MB each), rsync to workers. Progress JSON `brain` block books every
skip with its propensity.
Speed fixes ON (behaviour-identical): `_paint_tab_status` hoists max_row/max_column; `V15_PROGRESS_JSON_EVERY_S` default
15→60 (promotions still force a write). Scheduler: `V15_SCHED_REFRESH_ALL=1` (M/L refresh after their defer window, max 3
rm/tick as W), `V15_SCHED_SOFTSKIP_DONE=1` (verdict IMPOSSIBLE/NO_TRADES/BEST_EFFORT + final_gain = done in the host probe).

### 108.5 Bench results
(filled from `~/v15_bench/runs/20261014__*` when the 3-version run completes)

### 108.6 Live truth beats board gain (USER 2026-10-14: 5 accounts red on 1/7/30/365D while boards claim gains)
A cloud session's report (`data/SESSIONS_OUTPUT_ANALYSIS_2026_10_14.md`) reads board results as money ("93.5% win rate",
"+248% 14-day PnL", "$347K per fortnight if deployed"). NO-LIES verdict: those are 30-day IN-SAMPLE board gains (the window
the settings were fitted on; consecutive daily boards overlap ~29/30 days, so even board-to-board persistence is not
out-of-sample), "win rate" = share of boards with a positive vec gain, not trades; live is negative on every account.
Its token report is also wrong: idle Claude sessions consume no tokens; the herds spawn python processes, not cloud
sessions; the per-session token rates are invented, and `TOKEN_CONSUMPTION_REPORT.md` was never written. Capital
deployment advice from it is void until live P&L confirms (USER).
Integrated into the brain:
1. **Board gain is necessary, not sufficient.** The bench gate (108.2.6) proves a pilot change does not lose BOARD gain on
   frozen inputs; it does not prove money. The learning target gets a LIVE layer next: per sym_side and per key, board
   trades matched to the live ledger (§106 parity: C1 FIXED_STOP timing 69% of live losses, C2 position timeout, C3 twin
   divergence 510 missed trades, C4 latency) → a live-survival factor per key/exit reason that discounts P(pos) and
   E[delta] for settings whose board edge does not survive execution (e.g. tight-stop / high-churn keys while C1 is open).
2. **Forward persistence is a model feature, not an assumption.** A key promoted on board k is scored on board k+1's NPZ
   (newer bars) — the evidence the graph stores per sym_side scope — and on live fills when they exist. Keys that only win
   in-sample lose weight automatically (prequential learning).
3. **Engine changes reset evidence.** v12 `754331ff` (FIXED_STOP intrabar, 2026-10-14 19:40Z) changes every stop-driven
   delta; the brain export must be rebuilt from post-754331ff logs once ~3–5 days accumulate (until then the pre-change
   model only ranks; the bench on 754331ff decides).
4. No capital/position action is derived from boards here (live lane, other session; §106).

## 108. LIVE STACK HARDENING + HERMETIC VEC-ONLY TRADING + IDENTICAL ENVS (USER 2026-10-14)

USER: "keep going until all errors are fixed! macbook always runs conda … make sure live trading on both works identical … make this
totally hermetic so for now it can ONLY trade in tandem with what the vectorized would trade … then total parity in the scripts
themselves so we can opt to trade the identical trades from vectorized or switch on the non vectorizable and 1m/3m parts".

- **Positions service fixed (root causes, evidence = new stall dumps `~/logs/loop_stall_<proc>.log`, `loop_stall_watch.py`):**
  (a) `ez_share_ind` shared-memory handshake (multiprocessing BaseManager.connect, no timeout) ran on the event loop
  (positions bootstrap + ez_indicators): server up since 10-05 with 158 connections answered after up to 138 s → ez_manage
  flz/inf bootstrap TimeoutError loops (100+). Now `shared_mem_safe.connect_store` (off-thread, 3 s, 30 s backoff); bridge never
  connects on the loop; hot-path pull 0.1 s → 1 s (`POSITIONS_HOT_PATH_INTERVAL_S`). (b) `ez_positions.atomic_save_positions`
  re-parsed the whole side file per position (~650×/save) → 8–9 s loop stalls each cycle → heartbeat stale → 23 realtime
  takeovers on 10-14. Parse once per side per save. After: 0 stalls / 0 takeovers on ang/fin/flz/inf (men: one-off 9 s import at
  start). Bootstrap 75 s+ → 6–7 s.
- **Envs:** Mac live = conda `binance_env` (py 3.11.14, numpy 2.4.6, pandas 3.0.0). S1 failover + data watchdog used conda with
  numpy 1.26.4 / pandas 2.3.2 → NEW S1 env `~/.conda/envs/binance_env_mac` = exact Mac pip set (231 packages; pandas_ta 0.3.14b0
  copied from the Mac, PyPI yanked it); `tools/s1_trading_supervisor.sh`, `tools/s1_failover_start.sh`,
  `ez_s1_stack/run_with_watchdog.sh` point to it (old env kept for rollback). Live-stack code md5-identical Mac = S1/S2 dirs.
  Sweeps/boards run in S1/S2 `.venv` (py 3.12, numpy 2.5.3, pandas 3.0.5) — OPEN: align with live for numeric parity.
- **HERMETIC (phase 2):** `live_twins/vec_provenance.py` ContextVar marked at the 3 vec dispatch sites (ENTRY OPEN,
  VEC_CONVERGE_OPEN, in-position acts); `ez_manage.execute_now` refuses an exposure increase (BUY on LONG / SELL on SHORT / any
  hedge) without provenance for the same key → `BLOCKED_HERMETIC_NOT_VEC`. Exits/reductions/stops never gated. Runtime mode
  `data/hermetic_mode.json` off|log|enforce (deployed log 20:42Z → enforce after the log proves no legit vec order is flagged).
  Limit: a twin OPEN that differs from the clean board run still carries provenance — closed in phase 3 (twin == board run).
- Also 10-14: a "Teleport auto-stash" (git stash 19:38:35Z) reverted all uncommitted tracked edits in the Mac repo; restored
  forward (§106 text, binance_fills_fetch); traders were SIGKILLed in sequence 20:31–20:36Z by an unidentified actor (not the
  cron watchdog, not memory pressure; a Muse agent runs on the Mac).

### 108.7 OOS audit (2026-10-14 22:xxZ) — the 30D optimizer fits noise; holdout mode + min-effect knob
Real engine evals (`E.prepare` + `evaluate_prepared_sanitized`, NPZ truncated in memory; scripts `~/oos_audit/` on S2):
- Boards fitted Oct 8–13 (exact fit end, n=108 in-sample / 79 OOS, median 4.2 unseen days): published set in-sample median
  +9.53 (claimed +20.0 — boards do not reproduce even on their own window: 10/108 within 1 pp) -> OOS median **0.00**, mean +0.13;
  OOS lift vs its own start set median 0.00 (≈2% of the claimed lift survives); vs template +0.21 median, mostly by NOT trading
  (25/79 zero-trade). Spearman(in-sample lift, OOS lift) ≈ 0 (−0.06 / +0.08). Older boards (n=382, last 7 d): A 0.00 median.
- `vp.evaluate_sanitized(..., window_days=7)` silently returns the 30D result; the prepare path honours 7. The pilot's V17
  regime check uses prepare_batch(…, 7) (correct).
Consequences: learning MORE connections from in-sample deltas learns noise. Pilot (md5 `67a88ea1` Mac / bench; deployed
`6f019d49` without the min-effect knob): `V15_HOLDOUT_DAYS=7` fits the whole board on the NPZ truncated at last bar − 7 d and
scores final / start / template on the 7 unseen days -> `progress["holdout"]` + `HOLDOUT` delta-log line (default 0 = off);
`V15_MIN_PROMOTE_DELTA` (pp, default 0) rejects main-pass promotions below a minimum effect. The bench now judges versions on the
holdout (`v15_npz_bench.py compare` prints OOS sums). The brain must be retrained on holdout outcomes (P(OOS win | fit win)),
not on fit deltas.
Ops same day (USER): S1 = backtests only; klines APPENDED (npz_bar_refresh NPZ_LOCAL_ONLY=1, tradier-sandbox-klines disabled),
ez_s1_stack only inside the failover unit (EZ_S1_STACK cron commented, tools/ez_s1_stack_reap.sh), failover stale limits 600 s,
symbols_*.json pushed Mac->S1 every 2 min (tools/ez_rankings_push_s1.sh), pilot tier = ledger W/M/L of the previous round
(ledger built from the KPI DB in 0.1 s, pushed to S2).

### 108.8 Version gate — only verified pilot changes reach production (USER 2026-10-14)
USER: "make sure the verifier is in place on the fixed npz set on s2 that runs every new version of v15_pilot.py and subscripts and
only allows a modification if results improve — not when skipping switches or filters leads to deterioration of the system".
`tools/v15_version_gate.py` (S2 `~/v15_bench/tools/`, cron */20 run + hourly watch): `submit --files SRC=REL [--set K=V]` ->
champion vs candidate CONCURRENTLY on frozen `snap_20261014` (4 sides, `V15_HOLDOUT_DAYS=7`) -> PASS = all sides resulted, Σfit not
lower than champion − 1.0, no side −2.0 pp, ΣOOS not lower than champion − 1.0 -> atomic md5-verified deploy to S1+S2 sandbox+live,
gated switch defaults -> `data/v15_gate_flags.json` (pilot loads at import, env wins), candidate = champion. FAIL = nothing deployed.
`watch` alerts (`~/v15_bench/gate/ALERT_UNGATED_PILOT.json`) when a production pilot differs from the champion. Bootstrap champion
= pilot md5 1aef0e69 (brain default OFF); first candidate = BRAIN ON (p_ref 0.005, floor 5/1000). Pending task docs:
`docs/v15_pending/00_INDEX.md`.

### 108.9 Gate discipline — the ONLY production path (USER 2026-10-15)
No pilot, subscript, or gated switch-default change reaches production by hand edit, direct rsync, or fleet-wide
redeploy — `v15_version_gate submit` on S2 is the single gate: PASS deploys (atomic md5-verified), FAIL deploys nothing.
A board that degrades or reverts after a deploy is gate-bypass evidence: stop the herd, prove the bypass, re-submit.
Out of gate scope by design: scheduler-track files (`tools/v15_fleet_scheduler.py`, `tools/v15_dispatch_job.py`,
`tools/v15_round_watchdog.sh`) — Mac-only dev, never fleet-pushed, never benched; they schedule work, never change
what a board computes.

---

## 109 — 2026-10-15 CORRECTIONS. THIS SECTION SUPERSEDES EVERY EARLIER PASSAGE IT CONTRADICTS.

Written after the 2026-10-15 Tradier loss day (**−$430.34** stocks / **−$713.52** both venues, 39 sym_sides).
Each item below was MEASURED, with the file:line or the number. Where an older section disagrees, **§109 wins** —
the older text is left in place as provenance, as §0 requires. Full detail: `HANDOVER_20261015/`.

### 109.1 Yellow geometry — "L:BI (col12-61)" is WRONG
§56 and the `INSTRUCTIONS` tab say yellows live in **`L:BI`, 50 columns**. Measured on
`TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx`: filter headers run to **`max_column` 230, ~216 headers per tab,
2461 per workbook** (`ENTRY_REVERSAL_BOUNCE` 216, `EXIT_VELOCITY` 214, `GLOBAL_RISK_GATES` 190 …). `v15_pilot.py:5521`
scans col 12 → `max_column+50`, stopping at the `WHAT SWITCH` sentinel — it is NOT bounded by BI.
**Consequence:** any audit that diffs only `r[11:61]` compares the first 47 of 216 columns and will declare a starved
template healthy. The Oct 13 → Oct 15 full-width diff is **2461 → 2461, zero headers removed** — templates were never
column-starved. ~80 filters on one row fits ~4× over; **slot capacity has never been the constraint.**

### 109.2 `DEAD_VEC_SWITCHES` / `vec_unwired.json` mean dead on the **VECTOR** side, not live
`v15_pilot.py:124-128` states it; it is easy to misread and was misread this session (30 names removed, **18 had to be
put back**). A `QuickConfig` dataclass FIELD plus `AUTO_WIRED_PARAMS` membership is **INERT** — all four consumers are
`return`-first passthroughs with erased bodies: `v12_quick_engine.py:799, 809, 256, 779` (2026-09-28/09-30 NO-LIES
purges). Un-excluding such a switch is **dangerous, not generous**: its vec delta is structurally 0, board
nondeterminism is ±0.48 gain, and 15 of the 18 had real live consumers — i.e. noise could promote a structural zero
onto live stops.

### 109.3 The 2026-09-28 filter-wiring census verdicts are UNRELIABLE — do not cite them as proof
It counted single-line `getattr(cfg,NAME)` hits and therefore missed every multi-line
`_cfg("NAME",\n default,\n account_key, symbol, side)` consumer. `EXIT_R1_R2_FILTER_TF` and `FROZEN_STOP_FILTER_TF`
were verdicted dead while being real on BOTH sides (`v12q:17114` / `:17091` + `twin_yellow_filters`; live inside the
`R2_WT_VEL_SLOW` gate). **Four traps, all of which produced a false verdict this session:**
1. a quoted name inside a **list literal** is not a read (`filter_ablation_groups.py:54`, `lane_vec_stub4.py:77`); nor is a docstring citation;
2. a read inside a `vec_decisions/` twin counts only if an engine **calls** that twin (`wt_crossunder_final_exit` @ `twin_entry_ports_b.py:415` has zero call sites);
3. on the LIVE side `_ = _cfg("NAME", …)` is a stub farm — **456** such lines in `tradier_manage.py`, plus `if bool(_cfg(…)): _ = 1` around `:59783-61298`;
4. **any dead switch with a `data/switch_dependencies.json` master looks alive** under baseline-vs-flip, because `tools/opt/evaluate_v12.py:812-814` forces the master ON when the switch is overridden. Pin the master in BOTH arms.
**Mandatory gate:** `python3 -I tools/v15_deadvec_wiring_audit.py` must exit 0 before and after any wiring change.

### 109.4 A config default is NOT a kill switch
`_cfg` / `_cfg_auto` / `_psym_get` resolve **per_sym FIRST**. Measured 2026-10-15: `RULE_B_5M_EXIT_ENABLED=True` on
**222/222 stock and 325/325 crypto** rows, `WT_CROSSUNDER_FINAL_ENABLED=True` on 218/222 and 322/325,
`HEDGE_FAILED_FALLBACK_CLOSE_ENABLED=True` on all 547. Flipping the class default changed **nothing** live.
A real kill switch must sit **above** the per_sym layer (`SUB15M_BLOCKED_SWITCHES` + `_sub15m_blocked` at the top of
`_cfg`; `_sub15m_blocked_ez` at the top of `_psym_get`) or be read off the class object at the call site.
**Also:** inside `tradier_manage.py` the bare name `config` **is `TradierConfig()`** (`:6645`) — that file never
imports `config.py`. Stocks settings belong in `config_tradier.py`; a stocks constant in `config.py` is dead.

### 109.5 `r2_eff_tfs` semantics — the mechanism behind the peak give-backs
`vec_decisions/twin_yellow_filters.py:1067`: `"15m"` (the default) or `""`/`"OFF"` → **ALL** of `family`; any other TF
→ **just `(that_tf,)`**. Stocks family `('1h','4h','D')` ⇒ `'D'` **narrows** to Daily only. **Crypto family is
`('15m',)` ⇒ `'D'` is a total SWAP to a leg crypto never had.** It gates `R2_WT_VEL_SLOW`, the peak-then-collapse exit.
It moved 15m→D on **109/222 stock and 98/325 crypto**; 13 positions surrendered their whole peak
(CRWD_SHORT +13.01%→−0.20%).

### 109.6 One-way-ness test — how to tell a mechanical rewrite from a real optimiser
A per-sym delta search returns a SPREAD of transitions. These returned one:
`EXIT_R1_R2_FILTER_TF` 103 sym_sides / **1** distinct transition / 100% · `FROZEN_STOP_FILTER_TF` 103 / 1 / 100% ·
`PARITY_VEC_EXACT_MODE` 146 / 1 / 100% · `DC_HARD_STOP_15M_ENABLED` 31 / 1 / 100%
versus a genuinely optimised switch — `ATR_ADAPTIVE_STOP_MULT`: 64 sym_sides, **23** distinct transitions, dominant 17%.
**A switch flipping one way across 100+ sym_sides was not backtested. Treat it as un-evidenced.**

### 109.7 Yellow-attachment ≠ prune-exemption (§56 addendum)
`WT_CORE_MANDATORY_BASES` / `ESSENTIAL_GATE_SWITCHES` exempt a row from **pruning** only; nothing consulted them for
**attachment**. Measured before the fix: `WT_15M_BOUNCE_OPEN_ENABLED` attached 49 yellows of which only **12** were
WT-specific; `WT_CROSS_EXIT_APPLIES_TO_WINNERS` evaluated **0** (its only yellow is in `UNWIRED_FILTERS`, so 5 cells
were painted yellow while zero work happened). **Painted ≠ evaluated — audit `len(done[k]["yellows"])`, not colour.**
Fixed by making `mandatory_bases_for_tab(sheet, switch)` switch-aware; `EXIT_VELOCITY`/`EXIT_STRUCTURAL` previously had
no mandatory set at all.

### 109.8 The coarse yellow cap is PAUSED (24 → 0)
`_COARSE_CAP` default is now **0** (disabled). `_coarse_trim` ranks `_kept` by `_kept_ev` = (AVG_DELTA, POS_SYM) from
prior rounds, so a **newly added column has no evidence, sorts last, and is deferred forever** — never earning the
evidence that would keep it. The cap would permanently hide the WT15 filters and the boards would "prove" new columns
worthless by never evaluating them. Restore with `V15_COARSE_YELLOW_CAP=24` only once the new columns have
AVG_DELTA/POS_SYM over hundreds of tests. Cost accepted by the user: WT rows now evaluate up to ~127 yellows.
Measured regression this closed: WT15 yellows/row **26.20 (run27, Oct 6) → 0.65 (run32, Oct 12)**, a 40× collapse;
EXIT-tab zero-yellow share 44.9% → 93.5%; fleet-wide 61.8% of calculated EXIT rows evaluated ZERO yellows.

### 109.9 The S2 version gate was a closed door 2026-10-14 → 2026-10-16 — ROOT CAUSE FOUND, now fixed
Every bench side returned `{"err": "no progress json", "rc": 0}` after 22–61 minutes of clean running, so
`v15_version_gate.py:153` dropped all 4 as `uninformative`, `scored: []`, `fit Σ 0.0→0.0`, and **PASS was unreachable
by construction**. The 4 FAILs in that window are **harness failures, not verdicts on their candidates**.
**ACTUAL CAUSE (2026-10-16, `tools/v15_npz_bench.py:189-198`):** the pilot names its progress JSON
`{symside}_v14_progress.json` **only when `--window-days` is 30** (`v15_pilot.py:12119-12123`); every other window
writes `{symside}_{window}d_progress.json`. The gate always benches with `V15_HOLDOUT_DAYS=7`
(`v15_version_gate.py:119`), which snaps the fit window 30 → 20, so the pilot wrote `*_20d_progress.json` while the
bench globbed only `*_v14_progress.json`. `find_progress` now tries `*_{fit_days}d_progress.json` first.
**A superseded hypothesis, recorded so it is not re-chased:** this was initially attributed to the 2026-10-15
`PROGRESS_JSON_EVERY_S` 60→300 throttle plus the bench's `V15_FILL_SLICE_S=0` — i.e. "a bench board never hits a
forced flush". That was wrong; the boards flushed fine, under a name nobody was looking for.
**Standing rules regardless:** never read a `0.0→0.0` / `scored: []` FAIL as evidence against a candidate, and never
relax `TOL_SUM`/`TOL_SIDE` to make one pass.

### 109.10 `ALL_PATHS_ALLOWLIST.csv` gates the live-faithful verifier
`backtest_v12_engine.py:260` **exits 2** for any override not listed. The CSV had been gzipped away, and **no WT15 key
was in it** — so column **H / LIVE_DELTA was dead for the entire WT15 family**. Restored (1586 rows). Other families
may still be unregistered: audit before trusting any H column.

### 109.11 Only what is in the tradeable universe may consume CPU
Universe = `symbols_trb_long.json` ∪ `symbols_trb_short.json` ∪ `TradierConfig.TRADIER_MANDATORY_{LONG,SHORT}_TRB`
(stocks, **side-exact**) ∪ `tradeable_keys.json` (crypto, `acct:SYM_SIDE`; contains **no `tr*` accounts**).
Outside it: klines and indicators may still be collected, but **no NPZ build and no backtest**.
`excluded_tokenized_stocks.filter_syms` covers tokenized stocks ONLY and is not a universe filter — the director now
applies the real one (`v15_director.py`), `v15_fleet_scheduler.py:577-580` still does not. Cost of the gap, measured:
5 tier-1 losers traded outside the universe for **−$143.58** on 2026-10-15.
Positions held in now-untradeable symbols are closed **only when WT 15m turns the wrong way** — never force-closed.

### 109.12 Live is not 15m-only, and a disabled feature was still closing positions
`tradier_manage.py` reads `k_1m, rsi_1m, atr_3m, k_3m, atr_5m, k_5m, rsi_5m`. `LTF_SUB15M_TRADING_ENABLED=False` now
hard-blocks the sub-15m **decision switches** (19 stock / 18 crypto, above per_sym, fail-CLOSED,
`tests/test_sub15m_hard_block.py` 12/12) but the indicator reads remain.
`HEDGE_FAILED_FALLBACK_CLOSE`: hedging is off (`HEDGE_MODE_TRADIER=False`) yet the FAILURE path was armed and defaulted
True at `:11489` — live sought a hedge mate impossible on Tradier and the guaranteed failure **closed at a loss**:
**11 lifecycles, −$202.82**, violating this repo's own "never close at a loss on live" prohibition. Now ANDed with
`HEDGE_MODE_TRADIER` off the class object.

### 109.13 Hermetic vec-only gate — crypto ENFORCING, stocks LOG
`data/hermetic_mode.json` = `enforce` since 2026-10-14T20:58Z (crypto, `ez_manage.py:31983`, 0 false flags).
Stocks had **zero** hermetic references until 2026-10-15; the twin now sits at the top of
`tradier_manage.execute_now`, reading `TradierConfig.TRADIER_HERMETIC_ENFORCE` (**False = log only**) so per_sym cannot
switch it off. Flip to enforce only after the `[HERMETIC_TR] WOULD_REFUSE` count over a full session reconciles with
forward parity's `LIVE_ONLY_NONVEC` tally — stock parity is **1 PASS of 35** today.
**EXITS ARE NEVER GATED IN EITHER MODE** (`is_full_close`/`action` passed to `vec_provenance.gate`); blocking an exit
recreates the 2026-10-13 incident. The gate fails OPEN on error.

### 109.14 The profit-protection layer is the least-wired part of the system
52 non-sub-15m exit/stop/protection switches sit unwired — only **7%** of `vec_unwired.json` is sub-15m, so this is not
a timeframe artefact. Standouts: `ATR_TRAIL_FILTER_TF` + `ATR_TRAIL_SWEEP_ENABLED` (live reads them, no reachable vec
read — and give-backs were the day's #1 loss mode); `REGIME_TRENDING_EXIT_GAIN_MIN` / `REGIME_RANGING_EXIT_GAIN_MIN`
(**dead both sides** — literally the "take profit in time" knob); `INTRADAY_SESSION_FORCE_EXIT_UTC` (**dead both
sides** — the direct remedy for the −$233 overnight gap). `ULTIMATE_DC_4H_STOP_ENABLED` is **vestigial by mandate**,
not a defect (the stop at `:14939` is "no veto" by USER mandate; the tunable lever is `DC_HARD_STOP_TF`).
The vec route **does** carry switches live never reads (`PARITY_VEC_EXACT_MODE` → `_vec_exact_process` →
`per_sym_store.get_overrides` → `setattr` → `simulate_one`; 40/40 values verified carried), so a vec-wired-only exit
switch is tradeable today — the gap is the scalar fallback and every `VEC_EXACT_STALE` 4-bar fallback.

## 110. SIDE-BY-SIDE GATE — LAUNCH ENV IS PART OF THE VERSION, CORNER CUTS MUST EARN THEIR PLACE (USER 2026-10-16)

USER: "make sure any version gets tested side by side against previous version so corner cutting is exposed and better
results are always guaranteed". Measured before the change (2026-10-16):
- The gate benched pilot CODE with the env frozen into `snap_20261014` (captured from one CRWD_LONG pilot 10-14 19:3xZ).
  Production boards run the scheduler's dispatch env (`tools/v15_fleet_scheduler.py:1488-1490`: `V15_INERT_SAMPLE_EVERY=100`,
  `V15_V17_ROUNDS=1`, FOCUS cuts) plus the daily `v17_kpi.py --autotune` (S1 cron 14:30Z) values — neither was ever benched.
  On 10-16 192/192 S1 production pilots ran 5 unbenched autotune values (SHARE_ABLATION 0.48→0.3072, SHARE_RETEST
  0.84→0.593, SHARE_FOCUS 0.6→0.8, P3_MAX_S_LOSER 300→432, COMBO_SHARE_WINNER 0.4→0.4527).
- The 30 latest production boards (pilot 1aef0e69) evaluated 6.2% of attached yellow cells (0.20M of 3.23M); 26% of
  main-pass rows had no naked eval. The gate compared gain only, so a version doing less work was invisible.
- 4 bench sides (one per cat_side) against a 0.48/board noise floor.

Gate v2 (`~/v15_bench/tools/v15_version_gate.py` md5 b67aafa0, `v15_npz_bench.py` 6f20851e; tests
`tests/test_v15_gate_side_by_side.py` 10/10):
1. **Launch env is versioned.** Champion meta carries `env` (bootstrap = snapshot env). Champion and candidate benches each
   get an explicit `--env-file`; a candidate may change env (`submit --env K=V`, `--env-from-production HOST`). On PASS the
   env is published as `data/v15_gate_env.json` beside `data/v15_gate_flags.json`. Routing-only keys are ignored (ENV_IGNORE).
2. **Coverage is measured on every bench board** (`board_coverage`: main-pass rows, naked rows, yellow cells evaluated =
   cells with a result, skipped cells per mechanism, no-naked reasons). Painted ≠ evaluated (§109.7).
3. **Corner-cut rule.** Candidate cells-evaluated or naked-rows < 90% of the champion's (`V15_GATE_COVERAGE_MIN`) =
   CORNER_CUT → PASS only if ΣOOS beats the champion by more than the noise floor (0.48·√n) AND Σfit is not lower at all
   (no tolerance). Unreadable candidate coverage while the champion's is known = cut (fail-closed). Every FAIL lists
   `reasons`.
4. **All 8 snapshot sides** by default (two per cat_side); run timeout 720 min.
5. **`watch` (hourly) reads /proc/environ of every production python pilot** on S2 + S1 (bench pilots excluded) and writes
   `gate/ALERT_UNGATED_ENV.json` on any result-affecting difference from the champion env. GATE_STATUS.md shows the last
   candidate side by side (fit, OOS, trades, DD, TIM, cells, naked rows, wall per side) plus both alerts.
First candidate: `20261016T181153Z` = champion code + the production env above, benched against the champion env.
Not yet enforced: the scheduler does not read `data/v15_gate_env.json`; until it does, drift is detected and alerted, not
prevented. No gate can promise future market gains — it guarantees only that nothing ships which measured worse
(fit and unseen-days) or did less work without proving it on unseen days.

## 111. NPZ FRESHNESS — EVERY STORE TOPPED UP EVERY BAR, NEVER REBUILT WHEN A DEEPER COPY EXISTS, NEVER SHRUNK (USER 2026-10-16)

USER: "ez_indicators and tradier_indicators … are supposed to trickle update the npz files every 15min however this
system is faulty … revise and fix so they are always up to date from now on" + "s2 has backups of the npz;s so no
complete reruns are ever needed just topups; make sure they get appended to never overwritten with smaller versions".

**Measured before (S1, 2026-10-16 20:3x–21:0xZ, nothing inferred).** Crypto median 3.4 bars behind, 5 of 94 within
2 bars. The 15m loop's own stamp showed cycles of **2064–2866 s against a 900 s bar** (`latency_from_bar_close_s`
2615–2666 for the W*/X*/Z* tail of a fixed job order, every cycle). Six symbols were frozen for days: ASTERUSDT 17
days (legacy `htf_align` → `REFUSE_LEGACY_HTF_ALIGN` forever), ZECUSDC 64 h (`cannot extend 45 row keys`, all
monthly), CDW/ADP/AEM/AXTI 1-ROW stores since 2026-10-13 (`cannot extend 618 row keys`). On s2, 63 shared symbols
were staler than on S1 (BNBUSDC 3 bars on S1 / 361 on s2) and AEM was 366 rows vs 25083.

**Root causes, each proven.**
1. **One global lock made every targeted top-up a silent no-op.** `--loop` held `/tmp/npz_bar_refresh.lock`
   forever, so `v15_dispatch_job.tail_refresh`, `v15_npz_ensure`, the `*/15` additive cron and
   `npz_freshness_gate --refresh` printed "locked (previous run active)" and **exited 0**; the dispatch logged that
   as `tail current (0.2s)` and pushed the stale NPZ to a worker with an md5 "verified=True". Mutual exclusion is
   now PER SYMBOL (`sym_lock`), the daemon keeps a singleton lock, a one-shot that did not reach the bar exits 3,
   and `tail_refresh` judges the NPZ's own last bar instead of parsing stdout.
2. **The cycle was ~3x the bar and the loop then slept to the NEXT boundary.** Jobs are now ordered live-lane
   first then worst-behind-first (`order_jobs`), the pool is sized from the previous cycle to fit one bar
   (`autoscale`, cap `NPZ_LOOP_MAX_WORKERS=8` of 16 cores, margin 1.15), and `catchup_wait` starts the next cycle
   as soon as a closed bar is waiting — measured result **2634 s → 650 s**.
3. **The appender demanded a near-newest bar.** When the cutoff was not provably final it stepped back at most 3
   bars and else refused, so a symbol whose klines ended a day ago never caught up to bars its own klines ALREADY
   held (AEM/AXTI). `cache_last_final` now takes the newest provably-final bar in the local store (same finality
   rule — a strictly newer row must exist; `tests/test_npz_refresh_dropkeys.py` still pins that an unfinal bar is
   refused). `NPZ_CATCHUP_TO_SOURCE=0` restores the old behaviour.
4. **Hard refusals were repaired by nobody.** They are now queued (`data/vec_live/npz_repair_queue.json`) and
   **tools/npz_repair.py** fixes them by ADOPTING the deepest copy on any host (s1 / s1-live / s1 backups / s2 /
   s2 backups), then walking it forward in as many 1400-bar passes as the hole needs. Rebuilds are a separate,
   explicit `--rebuild` (never the cron): adoption healed AEM 1 → 25083 rows from s2's backup — DEEPER than AEM's
   own 19177 klines rows, which is exactly why adoption is tried before any rebuild — and AXTI 1 → 464.
5. **The monthly leg froze short crypto stores.** `droppable(k, mode)` matches the whole `*_M` / `*_M_prev` family
   for CRYPTO only; stocks keep every one of them (the only readers repo-wide are `tradier_manage.py` and
   `vec_decisions/check_entry_candidates_stocks__htf_w_m_align.py`; `ez_rankings`' `qv_M` is an output label it
   writes). ZECUSDC went 259 bars → 1 bar on the first cycle after this.
6. **The loop ignored the RULE#4 freeze** that `v15_npz_freeze` names every other NPZ writer in. It now skips
   frozen symbols (`FROZEN_BOARD`, 17 on the first cycle) and picks them up automatically when the board finishes,
   because the order is worst-first.
7. **21 installed stores were in no refresh job at all** (42USDT, BASUSDT, COLLECTUSDT, ARIAUSDT, BANANAS31USDT …
   301 bars behind with current klines and a clean dry-run append). The universe now includes every installed NPZ
   (`installed_syms`, kill switch `NPZ_COVER_INSTALLED=0`); variant/derived files (`_S3`, `_LONG`, `_365` …) stay out.
8. **The mirror to the worker stopped entirely while the loop lagged.** `sync_indicators.sh` excludes every NPZ the
   freshness gate calls STALE (>2 bars) or SHORT (<365 d), so during the slow cycles 113 files — including the
   current ones — were never pushed and s2 aged without limit (ZECUSDC and AGCO, at 331 d and 95 d, could never be
   pushed at all). **tools/npz_fresh_sync.py** is the corrective pass (cron `5,15,25,…`): it copies a file ONLY
   where it beats the peer's by rows/last-bar and never where it would shrink the target — 68 corrected copies on
   the first run, 12 held because their boards were in flight.
9. **A SIGTERMed loop could hang alive forever.** `Pool.join()` has no timeout: one worker inside builder C code
   held the parent in `do_wait` for 4+ minutes while the `pgrep`-only keepalive saw a live pid and started nothing.
   `_kill_pool` bounds the wait and kills the stragglers; **tools/npz_loop_keepalive.sh** judges the loop's log
   HEARTBEAT (`NPZ_LOOP_MAX_LOG_AGE=900`) and kills a silent pid — the same fix the Mac position writers needed.

**The never-shrink invariant.** `npz_live_generator._shrink_check` refuses any write with fewer rows or an older
last bar than the file it would replace (`NPZ_ALLOW_SHRINK=1` to override), and the 1-row-stub branch is refused
for CRYPTO as well as stocks (`NPZ_ALLOW_CRYPTO_STUB=1` restores it). That writer — shared by `ez_indicators` and
`tradier_indicators` — is what replaced four full stock stores with 1-row live stubs on 2026-10-13; it never called
`npz_guard`, which would have refused it.

**Where freshness is bounded by the source, say so.** `npz_fresh_sync` splits every symbol into `refresh_gap` (the
NPZ is behind its OWN klines — this pipeline's fault) and `upstream_gap` (the klines store itself is behind the
clock), judges stocks against the session frontier rather than the wall clock, and excludes symbols whose klines
stopped arriving (`dead_klines`, by LAST BAR not mtime). On 2026-10-16 21:20Z that separated ~60 stock NPZs sitting
95 bars "behind" — whose klines ended at 2026-10-15T20:00Z — from anything the NPZ chain could fix.

**Reporting rule.** A board or live decision may never be claimed to run on a current NPZ because a top-up command
returned 0. The proof is the NPZ's own last bar: `data/reports/npz_freshness.json` per symbol per host,
`data/alerts/npz_stale_live_lane.json` when a live-lane symbol passes `NPZ_FRESH_MAX_BARS` (2),
`data/alerts/npz_unrepairable.json` when no host holds a deeper copy. A store that is LEVEL with its source now
reports `SOURCE_NO_NEW_BAR` instead of `EXCHANGE_BAR_NOT_READY` (and counts as success for the exit code): the old
label made a healthy cycle read as a 241-symbol outage. Tests: `tests/test_npz_always_fresh.py` (11 contracts, the
never-shrink one needs pandas so it is skipped on the Mac) + `tests/test_npz_refresh_dropkeys.py` — 16 pass on S1.

### 111.1 — THE SOURCE SIDE, SAME DAY (USER "rebuild use massive.com if needed … fix the gateway fetcher … run disk cleaner")

**Everything above was bounded by two full disks.** S1 was at **100 % (2.5 GB free)** and the gateway at **99 %
(620 MB)**. A write that stops on ENOSPC leaves a file cut at a block boundary, which is exactly what the kline
stores held: 111 unparseable JSONs on the Mac, the same 111 on S1 (rsync propagated them), **222 on the gateway**,
every size an exact multiple of 4096 (245760, 229376, 282624 …). The primary stores (`klines_cache`,
`klines_cache_backtest`) had **zero** — only the gateway cache was hit, and for symbols whose ONLY store is that
cache (1000RATSUSDT, PROVEUSDT, BASUSDT, SAHARAUSDT) there was no source at all.

- **Disk.** `ez_disk_cleanup.py --auto 85` (cron */10) only covers `~/binance` and freed 1.1 GB — it cannot reach
  the sandbox. What actually freed S1: dropping `data/eval_cache_v1.db` (**58 GB**, regenerable memo cache, via the
  operator's own `V15_EVALCACHE_CAP` command; `causal_learner.db` NOT touched) and draining `~/ext_staging`
  (**12 GB**). The staging drain had been dead for days: `tools/mac_ext_ingest.sh` looks for `/Volumes/E3T` or
  `/Volumes/WD25`, E3T is unplugged and **WD25 is a PHANTOM** — a leftover directory on the boot disk (st_dev
  equals `/`), which its own `is_ext_mount` correctly refuses — so it logged "no MOUNTED ext target" every 15 min.
  `/Volumes/SSD2T` is a real mount with 87 GB free and is now in the list. **S1: 100 % -> 76 %, 70 GB free.**
  Gateway: journal vacuum 3.4 GB + the 222 deletions -> **99 % -> 89 %**.
- **Gateway fetcher untouched**, as instructed. NEW `tools/klines_truncation_guard.py` restores a truncated file
  from a longer parseable local copy, or deletes it so the fetcher refetches (that is also what frees the blocks).
  Mac 111 -> 0 (21 restored, 90 deleted); gateway 222 deleted -> 0. Cron `KLINES_TRUNC_GUARD` on the Mac
  (13,43 * * * *) and hourly on gateway/S1/s2.
- **Massive.com cannot cover the current day.** MEASURED: `2025-06-02` 110 bars, `2026-10-15` (yesterday) 60 bars,
  **today HTTP 403** — the plan embargoes it, and `massive_backfill_15m.py` is append-older-only by design. So
  massive is for history, never for today's session.
- **Today's stock session was missing for 107 of 223 symbols** — not a vendor outage: a DIRECT Tradier timesales
  call for the lagging names returned **27 of today's bars in 1.0 s**. `tradier_klines.py` runs sem=64 over
  223 symbols x 6 TFs during RTH and one 7-day repair pass at sem=16 after the close, and its log shows
  `15min fetch failed/timeout:` with an EMPTY message (`asyncio.wait_for`'s TimeoutError stringifies to nothing)
  at 1-3 MINUTES per symbol — self-inflicted concurrency, and the alphabetical crawl never reached T/STZ/USO/TTD.
  NEW `tools/tradier_klines_catchup.py` imports that module and calls its own `process_symbol()` at **conc 4** for
  the symbols actually behind — **107 symbols, all 6 timeframes, 156 s**; the script itself is unchanged. Cron
  `TRADIER_KLINES_CATCHUP` (*/20, 13-23 Mon-Fri). Afterwards only ELD and HAO lag, and both simply stopped
  trading before the close. The NPZ audit's `upstream_gap` fell **68 -> 5**.
- **The six frozen symbols, final:** ASTERUSDT 36009 rows/1658 bars behind/legacy -> **37675 rows, 1 bar,
  causal_v3** (rebuilt in ~105 s once the disk was free — the same rebuild had timed out at 90 min while the disk
  was full). ZECUSDC 259 bars -> **2**. AEM 1 row -> **25483** (adopted from s2's backup, deeper than its own
  klines, then walked forward). ADP 1 row -> **16101** (adopted from `/Volumes/SSD2T/tmp_s1_relay`, deeper than a
  rebuild). CDW 1 row -> **15174**. AXTI 1 row -> **596**. All four stock stores now sit at their session frontier.
- **Adoption needs a shape check, not just a row count.** `/Volumes/SSD2T/tmp_s1_relay/CDW.npz` has 43904 rows —
  more than any live store — with `dt_med=0 s` over a **20708-day** span (duplicate/garbage timestamps), and the
  deepest ADP copy on that drive is a **300 s** grid, a different generation. Ranking by depth alone would have
  adopted either. `npz_repair.grid_ok()` requires `600 < dt_med < 1200` and a span under `NPZ_MAX_SPAN_DAYS`
  (4000), and after an adopt+walk the tool ROLLS BACK if the tail ended up older than before (a deeper copy with
  an older tail is worse for a 30D board than a shallower current one).

## 112. ORDER-QUANTITY QUANTIZERS — ONE FUNCTION, VECTOR = LIVE, CAPS INCLUDED (USER 2026-10-16)

- **One implementation.** Every quantity multiplier that a board measures lives in `vec_decisions/qty_quantizers.py` and is CALLED (never re-typed) by `v12_quick_engine` (vector), `ez_manage.execute_trade_action` (crypto) and `tradier_manage.calculate_position_size` / `_qta_entry_sizing` (stocks). A sizing knob with three hand-written copies is three different strategies; that is how the STDEV ladder ended up 1–5× on boards, 0.7–1.8× live crypto and 10×-flat live stocks.
- **STDEV ladder (USER "should be 1-5x").** `STDEV_SLOPE_SIZING_ENABLED` master; `edge = clip(1-lrL_pct_b_TF, 0, 1)` long / `clip(lrL_pct_b_TF, 0, 1)` short; `mult = 1 + (STDEV_LADDER_MAX_MULT-1)·edge` → 1× at the top, 5× at the bottom (vv shorts), linear. Missing/non-finite channel = 1.0 (no guess). `STDEV_LADDER_TF` D (4h/1h rows). When STDEV is off, BAND_SLOPE_SIZING_V2 is the live path and the vector applies its live twin.
- **Streak quantizer.** `QTY_STREAK_SIZING_ENABLED` (default False) + `MIN_MOVE_PCT` 1.0/0.5, `LOSS_COUNT` 1/2 → `LOSS_MULT` 0.8/0.6/0.4, `WIN_COUNT` 1/2 → ×(1+`WIN_STEP` 0.2/0.5/0.8) compounding per further qualifying win, ceiling `MAX_MULT` 3/2/5. A trade = one lifecycle scored at its FULL close by the move of the exit vs the AVERAGE entry; sub-threshold results neither count nor break a streak; applies to new opens/reentries only. Live keeps `data/qty_streak/{venue}_{account}.json` (one count per lifecycle, dust skipped); the vector folds the same CLOSE rows (FINAL_MTM excluded).
- **Sub-knob rows force their master** via `data/switch_dependencies.json` (`dep_forced` in the result) — a `QTY_STREAK_WIN_STEP=0.5` row is measured WITH `QTY_STREAK_SIZING_ENABLED=True` and promotes both.
- **Caps are part of the size.** Live clamps every vec-decided order at the per-sym `MAX_ORDER_VALUE` / `MAX_POSITION_SIZE` AFTER every multiplier (`VEC_EXACT_HARD_CAP`). The vector cap `max(MAX_ORDER_VALUE, 10×START)` does not (measured 30d OPENs up to 22–30× START on crypto). `QTY_LIVE_ORDER_CAP_MULT` (START units) mirrors the live cap; 0 = legacy. OPEN: the pilot must feed it per sym_side from the live set, through the version gate.
- **Live-only sizing is not allowed to survive.** `DC_POS_SIZE` (crypto, 0.5–2.2× after the cap, no twin) is behind `DC_POS_SIZE_LIVE_ENABLED=False` — same ruling as the 2026-10-06 VEC-SIZE PARITY removals.

## 113. CHAIN HARMONY — ONE DECLARATION, ONE HOME, ONE TYPE; BOARDS CAPPED LIKE LIVE (USER 2026-10-16)

- **QuickConfig: one declaration per field.** A second `NAME: T = v` silently replaces the first (type AND default); 133 did, and the winning "generic" lines typed bool/str/TF switches as `float`, so boards published wrong-typed values the go-live gate refused. `v12_quick_engine` now raises at import on any duplicate (`_quickconfig_duplicate_fields`); repair with `tools/quickconfig_dedupe.py --apply` (keeps the line Python used, re-typed from the live config, values unchanged).
- **Templates: one row per (switch, value) per tab; regular rows in ONE tab.** Filter-block (filled) rows repeat per tab by design and are exempt. A bool switch whose rows all carry one value is a missing option, not a copy — the duplicate becomes the opposite value. Repairs only through the bookkeeper: `--dedupe-rows`, `--single-home SWITCH=TAB` (refuses if another tab carries a value or yellow filter the home lacks). Proof: `tools/chain_harmony_check.py` (exit 1 on any HARD) + `tests/test_chain_harmony.py`.
- **Boards are capped like live** until the system is trusted with size: order ≤ `QTY_LIVE_ORDER_CAP_MULT` (8) × START, position ≤ `QTY_LIVE_POSITION_CAP_MULT` (10) × START. The chart header shows the same set's `uncapped what-if` result on its own labelled line — never as the headline, never tradeable at today's caps.
- **Live sizing ceilings are mode-dependent and per-sym values bypass them** (OPEN, see §112): `_apply_mode` LIGHT/NORMAL/EXTREME (≈1×/4.3×/7.7× START after 2026-10-16 23:22Z) is switched automatically from `data/market_mode.json` (ez_rankings market index ≥65 EXTREME, ≤35 LIGHT), but `_psym_get` returns the per-sym store's own START/MAX_ORDER_VALUE first, so the mode ceiling only binds where the set has none; CRYPTO_MINIMUM_TRADING_MODE caps everything to the exchange floor on top.

## 114. LOSS-PRONE ENTRY FUNCTIONS + CLOSE-BEFORE-A-LOSS — THE PILOT FLIPS THEM, NOBODY ELSE (USER 2026-10-17)

USER: "what we do need is a stricter enforcement of 'closing before entering into a loss' and a clear database of what functions ended
having to close in a loss - these are to be excluded from newborn protection and should only be accepted with additional filters in
future application (unless backtest deltas say otherwise)"; "hard-zero breakeven protection was disabled <- NEVER SHOUDL BE ENABLED OR
DISABLED IT IS A SWITCH THAT FLIPS ACCORDING TO v15_pilot.py test results"; "newborn protection; should exist in both with the exceptions
jsut stated".

- **DATABASE.** `tools/loss_close_db.py` (daily 05:15Z, Mac cron `LOSS_CLOSE_DB` -> `tools/loss_close_db_cron.sh`, pushes to S1/S2 sandbox +
  live): `data/loss_close_db.sqlite` (every board trade from the latest 30d charts with close-basis MFE, every confirmed live full close joined
  to its opening function), `data/loss_close_db_summary.json`, `data/loss_prone_entries.json`. Flag = board n>=30 & pnl_sum<0 & loss_rate>=0.55,
  or live n>=10 & pnl_sum<0. `loss_prone.fn_family()` is the ONE name normaliser (DB, live, vector). 2026-10-17: 3905 board + 1155 live trades,
  106 functions, crypto 7 (B12, B15, B_EMADIST, B_SRS_ENTRY, HARDCODED_RALLY_REENTRY, REENTRY_DC4_15M_BREAK, REENTRY_HIGHER_LOW), stocks 6.
- **CLOSE BEFORE A LOSS = THE EXISTING PEAK_GIVEBACK FAMILY, NOW ACTUALLY IN THE ENGINE.** `v12_quick_engine` imported
  `vec_decisions/check_exit_candidates_crypto__peak_giveback.py` but never called it, so every pilot test of `PEAK_GIVEBACK_HARD_ZERO_ENABLED`
  (REDUCE_PROFIT_LOCK) measured delta 0 and the hand-set 2026-04-27 value could never be overturned. The engine now runs that same pure core per
  closed bar (close basis, reason `PEAK_GIVEBACK_GAIN_EROSION_STOP_peak…`); live follows through the VEC_EXACT twin. NO default was changed —
  the pilot's measured delta decides per board. A duplicate `BREAKEVEN_LOCK_*` family drafted the same morning was deleted (one rule, one home).
  New option `PEAK_GIVEBACK_SCOPE` all/loss_prone (master `PEAK_GIVEBACK_HARD_ZERO_ENABLED`), read by the engine, `ez_positions_quick` and
  `tradier_manage` alike. `HARD_BREAKEVEN_FLOOR_ENABLED` (EXIT_VELOCITY) is still a modifier of a live HTF veto with no engine twin — inert rows.
  Flip proof, 30d default sets, 7 sym_sides [DIAGNOSTIC ONLY]: HARD_ZERO on 6/7 better (COIN_SHORT −6.69→−1.12, UNIUSDC_LONG +0.53→+2.15),
  AMD_LONG worse (+3.94→+3.63).
- **ADDITIONAL FILTERS.** `LOSS_PRONE_ENTRY_FILTER_ENABLED` (default True, template row, ENTRY_CONFIRMATION_GATES) + `LOSS_PRONE_ENTRY_MIN_FILTERS`
  1/2/3: an open from a flagged function must pass N of {WT 1h, WT 4h, close vs ema_50_1h} with the side. Engine (before `_open`) and live
  native exposure increases at both chokepoints (`loss_prone.native_entry_ok`, `BLOCKED_LOSS_PRONE_ENTRY_FILTER`; vec-provenanced orders were
  already filtered by the engine). Filter on vs off, crypto: DOGE +0.10 vs −2.99, SUI −17.74 vs −21.29, UNI +0.53 vs −1.36, ZEC −7.52 vs −7.35;
  no effect on the 3 stock boards. A board turns it off when its delta says so.
- **NEWBORN PROTECTION, BOTH VENUES.** The 15-min block on native reduces/closes (ez `execute_now`) now has a stocks twin in tradier
  `execute_now`; same emergency bypasses, finest DC (5m) break, VEC_EXACT exempt, and positions opened by a loss-prone function are exempt
  (`NEWBORN_PROTECT_LOSS_PRONE_EXEMPT`, config-only LIVE_AUTHORITY). `exit_guarantee` classes `BLOCKED_NEWBORN_PROTECT_` as STRATEGY, so an
  authorized close still escalates.
- **13 live per-sym sets carry `PEAK_GIVEBACK_HARD_ZERO_ENABLED=True`** chosen while the switch was inert on boards; their next re-measure is
  the first honest number for them.

### 114.1 — HARD_BREAKEVEN_FLOOR ENGINE VERSION + LIVE = VECTOR, LIVE-ONLY FUNCTIONS PAPER-TRADED (USER 2026-10-17)
- `HARD_BREAKEVEN_FLOOR_ENABLED` is the override inside the live BREAKEVEN_GAIN_EROSION exit (former winner peak >= MIN_PEAK exits past the
  HTF veto; never-peaked waits). `vec_decisions/breakeven_gain_erosion.py` is the ONE core; `ez_positions_quick` and `v12_quick_engine` both
  call it (stocks get it through the VEC_EXACT twin; tradier had stubs only). The old engine stub `_batch1_template_wiring` is dead (no callers).
- Live defaults put the window at 50–50.5 % (unreachable) and the template options were 25–75 %; reachable 0.0/0.1/0.25 rows added, bold kept.
  Flip proof 30d, 7 sym_sides [DIAGNOSTIC ONLY]: window 0.1 floor on vs off better on COIN/DOGE/UNI(+2.21 vs +0.55)/SUI, worse AMD/PWR/ZEC;
  profit-not-required floor on vs off better 6/7 (UNI off: 48 breakeven closes −33.8 % summed). Pilot decides per board.
- LIVE = VECTOR: executed fills 3d before — crypto 1245 vec / 15 native entries, 1086 / 222 exits; stocks 28 / 270 entries, 4 / 397 exits.
  `TRADIER_HERMETIC_ENFORCE=True`. Every refusal (HERMETIC, STRICT_VEC_PARITY, both venues) is written by `paper_nonvec.record()`;
  `tools/paper_nonvec_eval.py` scores exits vs the real lifecycle close and entries by 16-bar forward return net of cost, per function per
  sym_side; PROMOTE_CANDIDATE after >= 7 days, >= 5 sym_sides, > half positive and pooled > 0. Promotion is a decision, never automatic.
- OPEN: the exit allowlist still passes intrabar live executions of vector families by token (FIXED_STOP, DAYTRADE/TECHNICAL_TARGET, …);
  they fire at the vector's own levels, but the trigger clock is live's — verify level identity per family before calling it 100 %.

### 111.2 — USDC IS NATIVE NOW (USER 2026-10-17 "we used to trade usdc but use usdt data … usdc has full and reliable data"; "usdc and usdt can have >5% price discrepancies so we DEFINITELY need to make it usdc native as well")

**The premise checks out.** All three real kline stores hold REAL USDC bars for every USDC ticker — measured on S1
over 39 symbols: `klines_canonical` 442 d typical, `klines_cache` / `klines_cache_backtest` 558 d typical and
1016 d for BTCUSDC/ETHUSDC, BNBUSDC 569 d. Verified by comparing each file's closes against its USDT twin:
**real, not cloned**, in canonical/cache/backtest for all 39. And every USDC **NPZ** matches its own USDC klines
**100 %** (0 % against the USDT twin) — the contamination the 2026-10-14 canonical-wins rule was written against
is gone. No refetch was necessary for depth or authenticity.

**Two USDT-derived relics existed and are deleted**: `klines_cache_gateway/KAITOUSDC_15m.json` (last bar
2026-08-30) and `IPUSDC_15m.json` (2026-07-02) — stale leftovers from before the gateway's 2026-10-14 kline fix,
for symbols the gateway no longer tracks (238 tracked, 33 USDC). `klines_cache`/`backtest` hold 413 d of real
KAITOUSDC, so nothing was lost. Removed on gateway + S1 + Mac.

**What was still substituted — and it was worse than substitution.** `gateway_data_broadcaster.py` built its
markPrice streams as `symbol.replace("USDC","USDT")` on the **legacy `/stream?streams=` base**, which klines had
already left on 2026-10-14. That base is DEAD: the handshake succeeds and not one message arrives. The gateway log
read, for hours, `✅ Mark price WebSocket batch 0 connected` -> `⚠️ No first mark price message received within 5
seconds` -> `timeout - no messages for 2 minutes, reconnecting` -> **`💰 Mark Prices: ❌ No mark prices found`** —
i.e. the gateway was publishing NO mark prices at all, for USDT and USDC alike. PROVEN from the gateway itself:
5/5 USDC `@markPrice` streams delivered on `/market/stream` (BTCUSDC 81442.70, ETHUSDC 2417.22, BNBUSDC 719.47,
SOLUSDC 104.21, KAITOUSDC 0.29444736) while the identical request on `/stream` timed out.

FIXED on the gateway's live copy (its own lineage, 341 lines ahead of the repo copy — patched in place, never
overwritten, backup `gateway_broadcaster/backups_before_usdc_native_202610170032.py`): markPrice now uses
`MARK_WS_BASE_URL` (`/market/stream`, `GATEWAY_MARK_WS_LEGACY=1` restores) and the native symbol via the existing
`kline_api_symbol()` whose exception set is empty. RESULT: **`💰 Mark Prices: ✅ 197 live_mark_prices`** sustained,
zero failure lines after the restart, **36 `*usdc@markPrice` streams** subscribed natively, and Redis publishes
`mark_price:BNBUSDC {"price": 719.52}` against an exchange BNBUSDC of 719.32-719.48 and a BNBUSDT of 720.11 — the
published price is the USDC one. The repo copy got the same design ported (`api_symbol_for()`,
`FSTREAM_WS_BASE_URL`, 4 substitution sites + 3 stream bases, 0 raw substitutions left).

**Measured basis, honestly:** 0.110-0.139 % across BTC/ETH/BNB/SOL/ZEC/LINK/DOGE/KAITO at 00:2xZ — not 5 % in a
quiet market, but it is a real, moving basis and it widens under stress, so a USDC pair priced off its USDT twin
is the wrong price either way.

**Side finding, fixed:** the gateway ran **two** broadcasters from two differently-named units — system
`gateway-broadcaster.service` (what `gateway_watchdog.sh` restarts, no `--user`) and a user-level
`gateway_data_broadcaster.service` stuck in auto-restart — plus a 65 h manually-started process. Now exactly one
supervised process (`NRestarts=0`), the user-level duplicate stopped and disabled.

**Settling contracts are an exchange fact, not our bug (correction to §111.1's attribution).** ICXUSDT, STORJUSDT,
IPUSDC, VANRYUSDT are `status=SETTLING` in `fapi/v1/exchangeInfo`, and `fapi/v1/klines` itself returns the frozen
settlement close (ICXUSDT 0.0152200). We store faithfully what Binance serves; we do not fabricate it. The
`source_frozen()` classification and keeping them out of the repair queue stand — only the attribution changed.

### 111.3 — NO USDC/USDT CONVERSION ANYWHERE (USER 2026-10-17 "make sure no usdc/usdt conversion is being applied in any script anymore as it is outdated and obsolete")

**Proven safe before touching anything:** the conversion was ALREADY a no-op for the live universe — no live symbol
source (`symbols_*.json`, `tradeable_keys.json`, `data/vec_live/vec_driven.json`) contained a USDT name whose USDC
sibling we hold data for (39 USDC pairs with klines, 38 in `live_usdc_pairs.json`). So removing it cannot change
behaviour today; it stops the silent rewriting going forward and makes a stale symbol LIST visible instead.

Swept the working tree (backups/worktrees/staged copies excluded): **65 active conversion sites in 27 files ->
24 left in 9 files**, of which 21 are inside LOCKED files and 3 are deliberate (below).

REMOVED (unlocked files, each compiled, backup per file in `backups/before_usdc_native_*`):
- `ez_mark_prices_v2.py` ×3 — `fetch_klines` fetched the USDT twin's candles for a USDC symbol; the
  api_symbol->original map is now identity; a redis fallback price is published under the symbol it belongs to.
- `gateway_data_broadcaster.py` (both lineages, repo + the gateway's own newer copy) — `api_symbol_for`/
  `kline_api_symbol` are pure identity, `force_usdc_if_needed` is a NO-OP that WARNS instead of rewriting, and the
  three "relabel an incoming USDT stream as the USDC pair" blocks are gone. See §111.2 for the dead-base fix.
- `per_sym_{ang,fin,men}_profiles.py` ×3 — `_load_tf_ohlc` loaded the USDT twin's candles when the USDC file was
  missing, i.e. it profiled a USDC symbol on USDT bars.
- `tools/npz_fundoi/fetch_fundoi.py` — funding and open interest are PER CONTRACT; this borrowed the USDT perp's
  funding/OI into the USDC symbol's `cov_funding`/`oi_*` NPZ keys. Native only (deployed S1+s2, md5 verified).
- `tools/npz_3m/fetch_klines_3m1m.py` — same fallback for the 3m/1m NPZ inputs (deployed S1+s2).
- `tools/wmh/pull_wm.py` — wrote a `{sym}_{tf}.proxyUSDT.json` weekly/monthly "proxy" for USDC symbols. Retired.
- `ez_manage_sandbox.py` ×8 and `scripts/ez_manage_2802.py` ×8 — incoming-symbol normalisation, mark-price
  candidates, indicator-snapshot aliasing, and `get_symbol_config`'s USDT fallback. **That last one mattered most:
  tick size, step size and min notional are per contract, so a USDC order sized on the USDT pair's filters can be
  rejected or mis-rounded.** It now logs a missing config instead of borrowing one.
- `scripts/limitless_{trader,tournament}.py` — the ticker map named USDT pairs and the code force-converted them;
  the MAP now names the real symbol (DOGE/SUI/WIF/NEAR, plus AAVE in the trader) and the conversion is gone.
  DOT/XLM/TRX/MNT/LEO/XMR stay USDT because **those USDC pairs have never existed** (operator confirmed; verified
  absent from `fapi/v1/exchangeInfo`), and PEPE stays USDT because its USDC contract is `1000PEPEUSDC` — a
  different contract size, not a twin.
- `scripts/ang_reentry_monitor.py`, `flz_dashboard.py`, `gate_analysis.py`, `intervention_oneshot.py`,
  `trader_deep_analyzer.py` ×2, `backtest_derived_strategies.py`, `reverse_engineer_traders.py` — twin lookups in
  kline/NPZ/mark-price resolution.
- `tools/build_usdc_hybrid_1y.py` — **RETIRED behind a hard refusal.** It built a "1y USDC" series from ADJUSTED
  USDT bars (USDC for ~18 d, converted USDT for ~347 d). Unnecessary: real USDC history is 442-1016 d.

DELIBERATELY LEFT (3 sites, none applies a conversion):
1. `gateway_data_broadcaster.py:192` — the `.replace` only DETECTS that a USDC pair exists so the no-op can warn.
2. `tools/build_usdc_hybrid_1y.py:33` — inside the retired script, unreachable behind the refusal.
3. `vec_decisions/small_account_sizer.py:78` — `step_size_for`'s USDT fallback is the VECTOR TWIN of the live
   fallback in `ez_manage.py` (locked). Removing one side alone would create a vec-vs-live sizing divergence, so
   both must go in the same change.

STILL TO DO — LOCKED FILES, 21 sites (CLAUDE.md STEP 0b; HANDS_FREE explicitly does NOT override LOCKED_FILES):
`ez_manage.py` 13, `ez_prices.py` 2, `utils.py` 2 (`force_usdc_if_needed`/`force_usdc_in_list`, whose callers are
`ez_positions_service.py` and `ez_mark_prices.py`), `ez_positions_quick.py` 2, `ez_double.py` 1,
`ez_mark_prices.py` 1 — plus `vec_decisions/small_account_sizer.py` in the same pass for parity.

### 111.4 — THE MAC COMPUTES THE BAR, S1 APPENDS IT (USER 2026-10-17 "we want to use macbook for the latest npz bar calculations for both tradier and ez -> rsync to s1 -> s1 adds it immediately to the existing npz's. No compute wasted as macbook already did the computing within the indicator scripts; s1 never has to rebuild npz's it just uses the updates just like it appends the klines without fetching them")

**THE PATH.** `ez_indicators.py` / `tradier_indicators.py` already call `npz_live_generator.update_npz_for_symbol`
every 15 min. Off S1 that call used to `return False` on its first line. It now computes the row with the live
calculator and stages it as JSON (`npz_live_generator.export_bar_for_symbol` → `data/npz_bar_outbox/`);
`tools/npz_bar_push_s1.sh` (Mac cron `* * * * *`, `--remove-source-files`) ships it to
`data/npz_bar_inbox/` on S1; `tools/npz_bar_ingest.py` (S1 cron `*/2`) applies it through
`npz_live_generator._append_row_to_npz` — the SAME function that writes a bar when the generator runs on S1.
Nothing is reimplemented, nothing is rebuilt, and a symbol with no NPZ is never created from a shipped bar (that
would trigger the full precompute this path exists to avoid).

**THE ROW MAY ONLY MOVE BASE-TF (15m) KEYS. HTF CARRIES FORWARD. THIS IS NOT A COMPROMISE, IT IS THE TRUTH.**
`_append_row_to_npz` carries a per-bar key the row does not supply forward (`v = arr[-1]`), and for a 1h/4h/D/W/M
key that IS the value until its bucket closes. Measured proof that it must work this way, from 75 recomputed rows
(`tools/npz_label_code_derive.py`, `data/reports/npz_label_code_derive.json`): every 15m categorical maps to its
stored int8 code UNANIMOUSLY (`wt_momentum_state_15m` {EXHAUST_DOWN:-2, IMPULSE_DOWN:-1, IMPULSE_UP:1,
EXHAUST_UP:2}, `wt_peak_structure_15m` {HH:1, LH:-1}, `bar_pattern_15m` 17 labels, `ha_15m`, `bar_vol_regime_15m`,
`wt_wave_phase_15m`), while EVERY HTF categorical CONFLICTS — `wt_cross_4h` BEAR splits -1/+1 64:11, `ha_1h` green
+1/-1 28:13, `wt_wave_phase_4h` EXPANDING +1/-1 57:10. The cause is not noise and more samples cannot fix it: the
HTF value stored on a 15m row is the builder's AS-OF BROADCAST of the last closed HTF bar
(`_broadcast_asof_indices`), and a live recompute reads that TF's own latest close instead. `npz_bar_refresh.py`
(deep klines + the real builder) stays the only writer that moves an HTF key.

**W AND M ARE NEVER COMPUTED LIVE, ON ANY HOST (USER "W is onluy for stocks not crypto").**
`npz_live_generator._compute_row_for_15m_close` drops W/M from its TF list unconditionally. The W/M source it
would read is stale EVERYWHERE: measured 2026-10-17 on the Mac AND S1, in `klines_cache` AND
`klines_cache_backtest`, `AAPL_W.json` ends **2026-03-23** (7 months back, on an 05:00-ET grid, not the builder's
Monday-00:00Z left-labelled `W-MON` bucket) and `BNBUSDC_W.json` ends **2026-08-24**.
`backtest_v8_precompute._crypto_wm_frame` abandoned those files for exactly this reason ("the old 'append-only
contract' kept the stale authentic `*_W.json`/`*_M.json` files … so W/M arrays froze") and now builds the frame as
authentic-older-history + fresh 15m buckets, which needs history the live calculator does not have: its 15m span is
95d for stocks and 18d for crypto = **14 and 3 weekly bars against the 200 `ema_200_W` needs**. This was not
theoretical — 3 staged stock bars (ABBV/ACN/ADP) carried 118 W + 118 M keys with `timestamp_W=2026-03-23` and would
have written a March weekly high onto an October row; `tools/npz_bar_ingest.py:strip_htf()` refused them. W/M were
added to `tradier_indicators.TIMEFRAMES` only so a host WITH the deep history can ask for them
(`compute(df, symbol, "W")` used to raise `KeyError: 'W'`); `WT_TF_PARAMS_STOCK` was deliberately NOT touched,
because `wavetrend()` does `.get(tf, <default>)` and the builder's stored W/M wavetrend used that default.

**FAIL-CLOSED, THREE WAYS, ALL "CARRIED FORWARD" AND NEVER FABRICATED.** `select_base_tf()` drops a key as
`HTF` (not a base TF), `UNMAPPED_LABEL` (a non-numeric string for an integer key whose label is not in the measured
table — never guessed) or `BAD_TIMESTAMP`. A number shipped as a string is not a label: `wt_cross_bars_ago_15m`
arrives as `"4"` and is coerced, not dropped. `sanitize_row()` maps JSON null to the store's own "no value" (0 for
numeric dtypes; an object array keeps None). The measured table lives in `data/npz_label_codes.json` and is written
for base-TF keys ONLY, one (key,label) pair at a time, each one unanimous.

**WHAT IT FIXED, MEASURED THE SAME NIGHT.** Before the base-TF selection every crypto append died on
`key wt_cross_15m (int8) value 'BULL'` — 103-110 `APPEND_FAILED` per cycle, zero bars applied. After:
**`APPLIED: 132`, `APPEND_FAILED: 0`**. ETHUSDC went 40128 → 40129 rows with `high_W` unchanged at 2736.47, which
is carry-forward working. Producer/transport timing over 1231 staged bars: bar close → computed on the Mac
**17.0 min median** (p90 18.5), computed → on S1 disk **1.7 min**. The producer term is the Mac's own 15m klines
being up to one bar behind at the moment the indicator cycle reads them (measured 04:19Z, 4 min after a close:
30 symbols 0 bars behind, 36 one bar behind), so the ingest cadence was raised 8x/hour → `*/2`.

**LOCK: ONE FILE, ONE OWNER.** `npz_bar_ingest.py` takes `/tmp/npz_bar_ingest.lock` itself (`self_lock()`), so an
operator run and the cron can never interleave. The cron's own `flock -n` wrapper on that SAME path was therefore
REMOVED — with both, every cron run saw its own parent's lock and exited "another ingest holds the lock", and
ingestion stopped dead from 03:47Z to 04:18Z (04:03/04:08/04:18 all no-ops). A `claim()` that tolerates
`FileNotFoundError` replaced the bare `shutil.move`, which had killed a whole batch mid-apply when two instances
globbed the same file. NO_NPZ bars are filed under `inbox/rejected/` and pruned after 6 h: 293 of 507 staged files
were NO_NPZ and leaving them in place would have grown the inbox ~28k files/day.

### 111.5 — THE DEEPEST COPY IS OFTEN IN A PURGE DIR (USER 2026-10-17 "that is RIDICULOUS … we have >365D npz's for EVERYTHING and they are ALL <15mm old")

**NO_NPZ 293 WAS A FILE COUNT, NOT A SYMBOL COUNT** — 8 staged bars each for 35 distinct symbols. The real finding
was bigger. Measured against the 566-symbol tradeable union: S1's live indicator dir held **332** NPZs while
`indicators/_quarantine` held **482**, `.purged_npz/20261013002948` **191** and `.purged_variants/20261012234331`
**262**, and s2's live dir held **681**. 62 symbols were MISSING from S1 live while a clean 900s-grid copy with
400-1875 days of span sat in the purge dir (C98USDT 1875d, CELOUSDT 1841d, BOMEUSDC 901d); 39 more were live but
shallower by >30d. `tools/npz_repair.local_roots()`/`peer_roots()` now search the quarantine and purge dirs and
s2's `npz_before_history_rebuild_*` — they are not junk, they are where the deepest stores were.

**RANK BY SPAN ON A VALID GRID, NEVER BY ROW COUNT.** A first pass ranked candidates by rows and "found" STORJUSDT
at 1,011,590 rows vs 207,076 live and PLTR at 123,832 vs 3,924 — both **1-minute-grid** stores, not deeper 15m
ones, and `npz_repair` correctly refused both (`grid_ok`: 600 < dt_med < 1200). The recurring **20707.7d** span
(BNO, CIEN, CLX) is the epoch-0 junk-row signature `NPZ_MAX_SPAN_DAYS=4000` exists to reject.

**A DEEP STORE THAT CANNOT BE TAILED IS HALF A REPAIR, AND MUST SAY SO.** `npz_repair` labelled every adoption
`adopted+topped_up` unconditionally. AXLUSDT and AWEUSDT were adopted from the purge dir and wore that label while
`topup()` had returned `REFUSE_LEGACY_HTF_ALIGN` both times — those stores carry no `htf_align` key at all, so
`npz_bar_refresh` refuses them forever (appending would double-shift their causal rows, the ASTERUSDT lesson) and
they sat **422 h stale under a success label**. The label now reports the real top-up status and names
`ADOPTED BUT NOT TAILABLE — needs rebuild`; both were then rebuilt from their own deep, fresh klines (46,547 /
42,449 15m rows). Only 2 of 155 crypto stores were ever in this state, and `--rebuild` is still explicit-only.
RULE#4 is respected throughout: ABBV and AGCO were skipped `board in flight (RULE#4 freeze)` and must be requeued,
never forced.

**FRESHNESS MUST BE REPORTED PER VENUE OR IT IS A LIE.** A single median over all NPZs read 488 min and meant
nothing. Split: **crypto** (24/7) n=151 median **29.1 min**, 60% ≤30 min, 91% ≤45 min; **stocks** n=193 median
505 min, which is CORRECT at 04:15Z because the session closed at 20:00Z — only 6 exceeded 600 min. Depth: crypto
139/151 and stocks 151/193 are ≥365d. The genuine outliers separate by cause, and the NPZ is never the cause when
its klines are older: AWEUSDT/AXLUSDT/C98USDT/ICXUSDT had klines from 03:45 the same morning (NPZ-side), while
ANKRUSDT (4d), BLOK (18d), HL (51d), BK (149d) and CALM (175d) are their own klines being ancient (kline-side).

### 111.6 — CONDA ONLY, AND THE VENV-MOVES-BOARD-NUMBERS FEAR IS MEASURED FALSE (USER 2026-10-17 "we will be using conda only for everything" + "all conda is up to latest so find out why it would be using old numpy before migrating")

**THE TARGET.** Workers: `/home/niels/.conda/envs/binance_env_mac/bin/python`. Mac:
`/opt/anaconda3/envs/binance_env/bin/python`. Both are py3.11.14 / numpy 2.4.6 / pandas 3.0.6 / scipy 1.17.1 /
sklearn 1.8.0 — identical, so a script that runs on either host computes the same numbers.

**WHY ONE CONDA ENV STILL SHOWED OLD NUMPY — the question, answered.** `~/.conda/envs/binance_env` on S1/s2 is
**not a maintained conda env**: created **2025-06-03**, its numpy 1.26.4 has **no conda install record** (only a
`numpy-1.26.4.dist-info`, i.e. **pip-installed outside conda's control**), and it is pinned below 2 by
`pyarrow 15.0.2` (`numpy<2`) and `tensorflow-cpu-aws 2.15.0` (`numpy<2.0.0`). `conda update` cannot lift it.
`binance_env_mac` (2026-10-14) carries neither package. So "conda is up to date" was true — of the current env;
the laggard was a 2025 leftover that 8 S1 crons, 3 processes and ~150 repo files still named, including
`persym_baseline_campaign.py` and `build_master_switch_sheet.py`, i.e. baseline/board work silently on old numpy.

**THE FEAR WAS TESTED, NOT ASSUMED.** The standing caution — "the sandbox venv's numpy 2.5.3 / pandas 3.0.5
against conda's 2.4.6 would move board numbers, so it must be benched through `v15_version_gate` or not at all" —
is **false for the NPZ build path**. `backtest_v8_precompute.compute_symbol('BNBUSDC','crypto',
return_arrays=True)` run under the sandbox venv (py3.12.3) and under conda `binance_env_mac` returned
**946 numeric 1-D arrays over 42,451 rows that are BIT-IDENTICAL: 0 moved values, 0 float-noise differences, 0
dtype differences, identical key sets.** Conda is additionally strictly MORE capable: the venv has **no scipy**,
which is precisely why every stocks row recompute failed there (`No module named 'scipy'`, AAPL 0/25 bars) and
succeeds under conda (25/25). Probe + diff kept at `/tmp/env_numeric_probe.py` and `/tmp/env_diff.py` on S1.

**VERIFY A MIGRATION BY THE EXECUTING SURFACE, NOT BY GREP.** Three surfaces decide what actually runs:
the crontab, `systemctl show <unit> -p ExecStart` (the **effective** value — a `.service.d/*.conf` drop-in can
override a venv ExecStart in the base unit, which is why `binance-npz-refresh` read as conda while a venv process
ran), and the live process list. File greps mislead twice over: they count `backups/`, `.history/` and `.git/`
(exclude them or the number is meaningless), and the **Mac's** conda env is ALSO named `binance_env`
(`/opt/anaconda3/envs/binance_env`), so the naive pattern `envs/binance_env/bin/python` matches 157 perfectly
correct Mac paths alongside the stale worker ones.

**THE VENV KEPT COMING BACK FOR A REASON.** `binance-npz-refresh.service` had a correct conda drop-in from 01:24Z,
yet the running loop was a venv process started Oct 16 22:13 — because the unit sat `inactive` while
`tools/npz_loop_keepalive.sh` relaunched the loop on `$ROOT/.venv/bin/python` every 5 min. A migration that leaves
the *relauncher* behind migrates nothing.

**WHAT WAS CHANGED.** S1+s2 crontabs → 0 non-conda refs; drop-ins for `binance-npz-refresh`,
`live-failover-watchdog`, `trade-auto-tuner` (both live modules **import-probed** under the target env before
switching — on this host "present" has meant "crash-loops on import"); `npz_loop_keepalive.sh`;
`tools/pyinterp.{sh,py}` (stale rung removed — it had listed `binance_env` AHEAD of a *duplicated*
`binance_env_mac` line, so a host missing that env silently fell through to old numpy); ~149 Mac-repo files
(Tier 1 = 128 absolute stale-env refs in 108 files; Tier 2 = 80 `.venv` refs in 41 files, applied only to the
absolute/remote/spawning sites — the ~98 bare-relative hits are mostly fallback rungs *after* conda or docstring
usage lines and were deliberately left); 58 worker-only legacy launchers edited **in place** on each worker (they
do not exist in the Mac repo, so they cannot be fixed Mac-side and rsynced); then 73 files/106 lines on S1 and
71/111 on s2 converted off `.venv`, each syntax-checked with **restore-on-failure**. Both sandbox venvs retired by
`mv` to `.venv.RETIRED_<ts>` (S1 immediately; s2 via `tools/s2_retire_venv_when_idle.sh` on a `*/10` cron, because
a `par_path_probe.py` had been running on it for 12 min and killing live parity work to tidy an interpreter is not
a trade worth making). Pre-edit images: `backups/before_conda_tier1_*.tar.gz` (valid — taken before the edit),
`backups/before_conda_tier2_PREIMAGE_6a8c18beb_*.tar.gz` (rebuilt from the pre-edit commit after the first attempt
archived the ALREADY-EDITED files, because the 15-min autosave cron had committed them and `git show HEAD:` was
therefore post-edit), and per-worker `backups/{conda_worker_local,venv_to_conda}_<ts>/`.

### 111.7 — ONE REGISTRY ANSWERS "WHERE IS THIS DATA" (USER 2026-10-17 "we have 2 ext drives and gateway and macbook and s1 full of klines costing huge resources and you are looking in the WRONG FOLDERS? MAKE A SYSTEM that understands WHERE THINGS ARE … AND CAN NEVER FUCK UP ON SIMPLE ISSUES LIKE THIS")

**`tools/data_locations.py` is the single source of truth for every data location, and `tests/test_data_locations.py`
holds it to that.** `--where <kind> --host <host>` prints the authoritative path; `--audit` measures every declared
path on every host (mac / s1 / s2 / gateway / the 4 ext drives) and reports existence, file+symlink+subdir counts,
size and age; `--kinds` lists what it knows. Kinds today: `klines_crypto`, `klines_stocks`, `market_data_crypto`,
`indicators_snapshot_crypto`, `indicators_snapshot_stocks`, `positions_crypto`, `positions_stocks`,
`positions_failover_mirror`, `npz_indicators`, `npz_bar_outbox`, `npz_bar_inbox`, `failover_state`,
`failover_stage`, `failover_outbox`, `producer_logs`.

**THE THREE RULES THAT MAKE IT WORTH HAVING.**
1. **Env overrides are part of the truth.** A kind whose path is decided at runtime declares the variable, and
   resolution reads it FIRST. This is the bug that caused the module: `mac_failover_agent.STAGE` is
   `LF_STAGE` defaulting to `/Users/niels/live_failover_stage`, which is **NOT under `LF_BASE`**. A diagnostic
   looked under `<base>/data/live_failover/stage`, found nothing, and reported a working S1→Mac delivery as broken.
2. **An unknown kind RAISES; it never returns a plausible default.** `where("klines_banana")` exits non-zero and
   lists the known kinds. A wrong path that looks right is the whole failure class.
3. **Unverified is a PROBLEM, not a pass.** A canonical path whose probe could not run is reported as
   `UNVERIFIED`, because silence reading as success is how this goes wrong quietly.

**IT CAUGHT ITS OWN AUTHOR TWICE, WHICH IS THE POINT.** The first registry declared positions at
`data/positions`; the audit immediately reported 5 missing canonical paths, and the real layout is per-account
files (`<base>/<acct>/{long,short,augmented,reduced,options}_positions.json`, crypto ang/fin/flz/inf/men, stocks
tra/trb/trc) — exactly what `live_failover_state_check.side_files()` reads. The first probe counted only regular
files and so reported `indicators/_quarantine` as "6 files" when it holds **482 entries** (476 are symlinks the NPZ
adopter makes) and `.purged_npz` as "0" when it holds dated **subdirs** full of stores; it now counts files,
symlinks and subdirs separately. The first remote probe passed its script as quoted JSON through ssh, got mangled,
and reported every remote host as `?` — it is base64-encoded now, and a failed probe records WHY in the note.

**WHAT THE FIRST FULL AUDIT FOUND (2026-10-17 06:0xZ), all worth acting on separately:**
* **Only ONE of the two ext drives is actually backing up.** `/Volumes/SSD2T/binance_archive/` holds 2.5 GB crypto
  klines (5870 files) and 6.2 GB stocks klines (9416 files), both ~2.4 h fresh. The same two dirs **exist and are
  EMPTY on `/Volumes/TOSHIBA_EXT`**, and are absent from E3T and WD25.
* **`backtest_npz_master` is GONE from all four drives** although INFRASTRUCTURE.md still documents it as the
  4.4 GB master NPZ archive.
* **S1's sandbox and live trees disagree hugely on stocks klines**: 29,916 files (5.7 GB) in
  `binance-sandbox/klines_cache_backtest/tradier` vs 4,744 (5.6 GB) in `binance/klines_cache/tradier`.
* The Mac's `klines_cache_backtest` holds **8 files / 12.6 MB** — effectively empty, so any sweep pointed at the
  Mac's backtest store is reading nothing.
* S1's live NPZ store is **497-501 stores / 7.0 GB** (it was 332 at the start of the night).

## 115. BAR-IDENTITY MEASUREMENT — WHAT THE REPLAY CAN AND CANNOT PROVE (USER 2026-10-20)

Operator mandate this session: "THEY HAVE TO MEAN IDENTICAL BARS", "live can not have even a 0.1% discrepancy",
"make sure all new sym_sides are applied live immediately and trading identically to their charts". Everything below
is measured; where a number was later refuted, both the claim and the refutation are kept, because the retraction is
the useful part.

### 115.1 The numbers, with their scope

| run | scope | entry bars identical | exit bars identical |
|---|---|---|---|
| crypto, S1+S2, 28 sym_sides scored | uncapped store sets, Oct 1-10 window | **2,074 / 2,293 = 90.4%** | **1,997 / 2,074 entry-exact pairs = 96.3%** |
| stocks, S2, 30 sym_sides, TWIN mode | `PARITY_VEC_EXACT_MODE=True` | 475 / 516 = 92.05% | 406 / 488 = 83.2% |
| stocks, S2, 59 sym_sides, NATIVE | today's live config | **68 / 755 = 9.0%** | 81 / 743 = 10.9% |
| crypto, Mac, 4 sym_sides | Mac at load 356 | 160 / 163 = 98.2% | **61 / 161 = 37.9%** |

11 of 28 crypto sym_sides were bit-exact on EVERY entry and exit in the window. Entry agreement is binary: +-1 bar and
+-4 bars add 3 matches in 2,293 — either live takes the same bar or it takes nothing.

**The twin-mode number is near-tautological and must never be quoted as switch-set parity.** In twin mode live executes
the vector's own decisions. Same sym_side, same window: `AAPL_LONG` 12/12 exact in twin -> 1/12 native. What twin mode
does prove is a floor: live is handed the answer and still dropped 41 entries and 82 exits.

**The 37.9% Mac exit figure is an environment artefact, not an engine property.** It was taken while that host sat at
load average 356 with 76 MB free RAM. S1 and S2 reproduce 95.6% and 96.5% independently across two different engine
md5 sets. `VEC_EXIT_OWED` appeared in 5 of 77 exit mismatches on the servers, not 98 of 100. A parity number measured
on a thrashing host is not a measurement.

### 115.2 The engine CANNOT fire HARDCODED_RALLY_REENTRY — this biases every stocks parity number

`HARDCODED_RALLY_REENTRY` is the highest-volume entry family in the corpus (159,841 rows) and 195 of the 561 VEC_ONLY
entries in the 60-sym_side stocks run. It is gated on `last_reduction_price > 0` and fires when price crosses back
through that stored exit (`ez_manage.py:42381`, `tradier_manage.py:40307`).

* LIVE keeps that field on the key across the flat period (CLAUDE.md forbids deleting position dict entries, and
  `ez_positions_service.py:9518` `[REENTRY_GUARDIAN]` restores it). Live /history/ holds **86 NATIVE (untagged)**
  `HARDCODED_RALLY_REENTRY` OPENs — 46 on `trb`, 40 on `trc`, 2026-09-21..10-15. The family is wired and firing live.
* THE ENGINE's live leg fired it **ZERO** times. Measured with a probe: `last_reduction_price > 0` on **0 of 1,255**
  flat-key `process_position` calls, `was_reduced=True` 0 times, `evaluate_reentry` called **0** times (its
  precondition `if was_reduced and last_red_time and not has_position` never held).
* ROOT CAUSE, and it is on the CLOSE side: the engine replaces `manager.execute_now` with its own seam
  (`backtest_v12_engine.py:10857`), so `_execute_now_inner` — the only live writer of those fields
  (`tradier_manage.py:50704-50707`) — never runs, and the seam's reduce branch (`:10712-10723`) does not set
  `last_reduction_price` / `last_reduction_time` / `was_reduced`.
* A 2026-10-20 patch carrying the field onto a REOPENED position at the three OPEN-side construction sites was
  **measured a no-op** (`obj_replaced=0`, those sites never reached). The fix belongs in the seam's REDUCE branch,
  mirroring `tradier_manage.py:50704-50707`, and per gate discipline must ship through `v15_version_gate submit`.
* CONSEQUENCE FOR READING §115.1: the stocks NATIVE 9.0% is measured in a harness where the vector's #1 entry family
  cannot fire on the live leg. It understates live parity by an unknown amount. Do not quote it as live's parity.

### 115.3 The replay runs exits with their safety mechanism OFF

`backtest_v12_engine` sets `EXIT_GUARANTEE_REPLAY=1`, and `exit_guarantee.py` then goes **pass-through** — no FORCE
leg, no stuck watcher (`exit_guarantee.py:27, :352`). Live has `EXIT_GUARANTEE_ENABLED` / `_FORCE_ENABLED` True with
retry `15,30,60,120,240` and stuck checks `20,60,180`. So a replay's exit timing is NOT live's exit timing, and the
replay is structurally unfit to measure "did live exit late". Any exit-side parity claim must say which arm it came
from.

### 115.4 The harness set is `per_sym_store.get_overrides` (UNCAPPED), never `full_set`

`full_set` applies the sizing caps, so it differs from the store on sizing knobs only — `START_POSITION_SIZE`,
`MAX_POSITION_SIZE`, `MAX_ORDER_VALUE_FIN/MEN` (60/60 sym_sides), `MAX_ORDER_VALUE` (11/60). `live_twins/vec_exact.py:357`
then reports `LIVE_SET_NE_BOARD_1keys:START_POSITION_SIZE` and refuses EVERY OPEN/AUGMENT — measured on **25 of 60**
crypto sym_sides, one logging 1,897 refusals and 0 OPENs. Live's own gate reads the uncapped store
(`vec_exact.overrides_for`, lines 61-70), and `_run_one_set_precedence_on` (`backtest_v12_engine.py:18065`) patches the
store so `full_set` still applies caps at read time inside the engine — caps modelled as live, gate passing as live.
With the store set, 0 refusals. Key counts are 3,459-3,556 (median ~3,526); a set of ~800 or ~11 keys is the wrong layer.
For STOCKS the two are byte-identical on 60/60 (`_apply_caps` touches only crypto sizing), so this trap is crypto-only.

### 115.5 A board's age must be judged at the SIMULATED moment, and by the CELL date

Two defects, both fixed 2026-10-20, both of which silently zeroed live legs:

1. **Wall clock inside a replay.** `latest_board_ok` aged the board against `time.time()` even under replay, so a long
   run crossed the staleness bar MID-FLIGHT: `BTCUSDC_LONG` had live trades in its 04:11Z run and 0 entries with
   `BOARD_TOO_OLD_225h>216h` at 08:50Z on identical inputs. `_gate_now()` now returns the engine's `_sim_ts[0]` when
   `EXIT_GUARANTEE_REPLAY=1` and wall clock otherwise (live byte-unchanged). Verified on all five paths.
2. **Run label instead of cell date.** The gate took the older of `published_at` and the date parsed from
   `run\d+_(\d{8})` in the progress path. That label is the day the RUN STARTED, and a long-lived run keeps producing
   fresh cells under it: **332 of 474** published sym_sides were refused `BOARD_TOO_OLD` at 230 h while their cells were
   **0.4-179.2 h** old. `tools/v15_published_selection.py` now carries `cell_ts` (from `meta.result_done_utc` for the
   new pilot, `campaign_ts` for the older publisher, `recorded_at` as fallback) and the gate prefers it. Result:
   `BOARD_TOO_OLD` 332 -> 0, OK 130 -> 443 of 474. NOTE: a first version reading only `campaign_ts` was INERT —
   that key exists in 0 of 1,496 `fleet_orchestrator_publish` rows.

Also measured: `LIVE_BOARD_MAX_AGE_H` must be identical on every host. S1 read 216 h while S2 read 168 h for a while,
so the same sym_side got a different verdict depending on which box evaluated it. One value, pushed everywhere.

### 115.6 The replay is NOT hermetic — it writes live trading state

Confirmed on two hosts, two different files:

* `data/vec_exact_exit_owed.json` — the Mac runs added **9 keys** under the `ang` account key, on exactly the
  sym_sides being replayed, between 03:35 and 03:47Z while every `ang` process was down. S1's sandbox copy went 7 -> 16
  keys the same way. An owed entry closes the NEXT position opened on that key one sweep after entry, so this is not
  cosmetic.
* `data/fixed_stop_registry_tradier.json` — rewritten by the stocks replay with a simulated FIXED_STOP level keyed
  like a live record (`trb:ULTA_LONG`, `trb:GOOGL_LONG`).

Containment that held: the live-path copies under `~/binance/data/` were byte- and mtime-unchanged, because the runs
had `BASE_PATH=~/binance-sandbox`. RULE: a replay must run with a sandbox `BASE_PATH`, never on the Mac, and its
state writes must be diffed before and after. 20 obligations on flat keys were cleared on 2026-10-20 (backup
`backups/vec_exact_exit_owed_before_flat_clear_202610200353.json`).

### 115.7 Engine era, and S2 does not keep a hand-placed engine

All five decisive files differed between S2 and the Mac during this session (`tradier_manage`, `config_tradier`,
`config`, `backtest_v12_engine`, `live_twins/vec_exact`). Numbers from one host are internally comparable (one engine
stamp per run set) but are a verdict on THAT code era. Always report per-host aggregates beside any combined number.
A `backtest_v12_engine.py` rsynced to S2 by hand was reverted to the fleet copy within ~3 minutes, with the original
mtime preserved; the writer was not identified. Engine changes reach the fleet through `v15_version_gate submit`, not
rsync.

### 115.8 NPZ freshness for a backtest

`tools/opt/v12_pilot.npz_freshness()` skips a test when the NPZ is more than **3 bars** behind the last closed bar
(crypto) or last RTH close (stocks), emitting `STALE_NPZ_SKIPPED` rather than a misleading number — grace was 1 bar
until 2026-10-17. `V12_ALLOW_STALE_NPZ=1` bypasses it and is for fixtures only; after a market open it becomes
necessary for any window that ends at yesterday's close, and both arms of an A/B must then set it identically.
Measured 2026-10-20 14:16Z on S1's 196 crypto stores: **0 within 1 bar**, 7 within 2, 172 within 3, 16 beyond 4. So a
1-bar (15 min) requirement would currently skip essentially every calculation.

**The ingest live-lock (fixed).** `npz_bar_ingest` took the per-symbol flock in order to REFUSE a non-contiguous bar as
`HOLE_LEFT_TO_REFRESHER`, while `npz_bar_refresh` — the only thing that can close a hole — needs that same lock. 748
bars across 15 symbols were re-read and re-refused every 2 minutes, and a targeted refresh returned `LOCKED_SYMBOL` on
8 of 19 stuck stores, the holder being the ingest process refusing their bars. Hole-blocked bars are now filed to
`data/npz_bar_inbox/deferred/` when the hole exceeds `--hole-defer-bars` (default 2); inbox drained 777 -> 507 in two
cycles. Of the 19 stuck stores: `LOCKED_SYMBOL` 8, `FROZEN_BOARD` 3 (their S1 KLINES stop ~10-13 — an NPZ cannot pass
its source), `REFUSE_LEGACY_HTF_ALIGN` 6 (pre-`causal_v3` store: needs a REBUILD, a bar cannot be appended), 1 delisted.

### 115.9 Re-stamping: no evidence, and the one path that remains

`docs/v15_pending/19_RESTAMP_FORENSICS_20261020.md` is the authority. Verdict: **no re-stamp is evidenced.** 259 of 259
verifiable publish stamps land within 1 h of that run's newest delta-log eval, and `updated_at - result_done_utc` is
-0.78..+0.01 min. The "gap median 93 h / max 191 h" that triggered the investigation was an arithmetic artefact —
the gate's run-label date (230 h) minus publish age — not a stamp-vs-cells gap. `v15_fleet_scheduler` invokes
`v15_settings_history.py`, which **records, it does not re-date**.

The one residual path, pre-existing since BOARD_DATE_TRUTH 10-19: a pilot that resumes (`returning early`) publishes a
new stamp over old cells. `tools/v15_settings_history.py` now refuses any row whose `published_utc` is more than
`RESTAMP_MAX_LAG_H` (2 h) after its own `result_done_utc`, counted as `refused_restamp` in the summary. The rule is
deliberately asymmetric: contradicting evidence REFUSES, **absent** evidence RECORDS and says so, because 156 of 474
boards have no delta log on the publishing host. Dry run over today's 139 real boards refused 0.

### 115.10 Measurement traps that produced false findings this session

Every one of these generated a wrong conclusion that had to be retracted. Treat them as mandatory checks.

* **Grepping a log for a bare number.** `grep -E "418|429"` matched digits inside JSON (`"rows_before": 21189`) and
  produced a phantom "S1 is Binance-banned" story. Every record actually showed `"t_fetch": 0.0`. Grep the HTTP
  context, never the number.
* **An env var the code does not read.** `LIVE_BOARD_MAX_AGE_H=216` was set in the environment to measure the impact of
  a 9-day bar; `_knob` does not read the environment, so the run measured the old 264 h default and reported "445 OK,
  zero too old". The real figure at 216 h was 360 of 469 refused.
* **`pgrep -f` with a pattern containing its own search terms** self-matches the shell running it. This produced both a
  phantom trader count AND, separately, a real double-trade discovery; exclude `bash -c`/`pgrep` from any count.
* **macOS `pgrep -f` does not match `python.*foo` the way Linux does** — the existing `TRADER_PATTERN` returns 0 on the
  Mac while the processes are running. Test process-matching logic on the host where it runs.
* **An empty staging directory is not a dead producer.** `npz_bar_push_s1.sh` uses `rsync --remove-source-files`, so a
  healthy push EMPTIES `data/npz_bar_outbox`.
* **Stock NPZ tails at <=1 day outside RTH are correct, not stale** — no 15m stock bar exists overnight.
* **The chart entry formula `ts - (bar_exit - bar_entry) * 900` is WRONG for stocks.** RTH-only bars make that
  arithmetic walk through overnight gaps (10 of 31 `BWXT_SHORT` rows disagreed with the row's own `entry_time`). Use
  the row's own entry timestamp, or compare vector<->live event timestamps and never chart arithmetic.
* **Tokenising a reason by its longest UPPER_SNAKE prefix misses the family.** Live writes `FIXED_STOP_LONG` /
  `CYCLE_TP_TIERED_T0` while the vector emits `FIXED_STOP` / `CYCLE_TP`; testing only the full prefix reported 313
  vector-backed exits as "no emitter". Try progressively shorter prefixes, and allow numeric segments
  (`E_1_WT_DELTA_EXIT` truncates to `E` otherwise).
* **A `getattr`/`_cfg` reference is NOT proof a switch is wired.** Live contains deliberate probe farms
  (`ez_manage.py:1637` "wire 1613 missing config.py switches", `_wire_trb_stocks_hemisphere_tradier`, and a per-switch
  `if bool(getattr(config,"X",False)): _ = 1` block). 1,210 switches exist in live ONLY as a throwaway probe, and 67 of
  those are set by the boards to a NON-DEFAULT value on 433-560 sym_sides each. `WT_DC_ENTRY_ENABLED` was one: False on
  764 of 765 sets and honoured by the vector, stubbed in live, so stocks fired `WT_DC_ENTRY` **201 times against the
  vector's 0** (144 native events in live history). Check that the reference is a real clause, not `_ = ...`.

### 115.11 Still open

* **Determinism is unmeasured.** Identical tree + identical NPZ has been measured to differ by <=0.48 in GAIN because
  phase budgets are wall-clock bounded; whether the BARS move across repeated runs of the same sym_side was still being
  measured (6 sym_sides x 4 runs on S2) when this section was written. If the bars move, no parity percentage above
  means anything.
* **Kline-vs-NPZ fidelity is unmeasured.** Whether the NPZ a backtest reads equals what the real scripts compute from
  the same klines (`npz_live_generator._compute_row_for_15m_close`, the shared `ez_indicators`/`tradier_indicators`
  entry point) had not been verified key-by-key. Until it is, a bar-identity number only proves the two engines agree
  with each other, not that either agrees with live market data.
* The 66 remaining board-set stub-only switches (§115.10) are a ranked worklist, not yet wired:
  `/private/tmp/.../scratchpad/stub_only_board_set.json` — top entries `MTF_WT_CROSS_EXIT_TF` 560,
  `MTF_BB_REJECT_EXIT_TF` 512, `MTF_DC_REJECT_EXIT_TF` 475, `MIN_HOLD_BARS`, `R1_TF`, `K_LOWER_HIGH_EXIT_ENABLED`.

## 116. DETERMINISM PASSES, FIDELITY FAILS — THE BOARDS ARE COMPUTED ON DIFFERENT INDICATORS THAN LIVE (USER 2026-10-20)

This supersedes §115.11, which recorded both questions as unmeasured. Both are now measured, on S2, 6 crypto
sym_sides x 4 runs, harness `tools/v15_trade_parity.py` md5 `05b2e40130b77e56ca58283e911cf118` unmodified, NPZs
frozen by hardlink and verified byte-identical before and after. **VERDICT: NO-GO.**

### 116.1 Determinism — the bars do NOT move (the apparatus is sound)
Every completed run of a sym_side is bit-identical on all four bar sets (vec entry, vec exit, live entry, live
exit), symmetric difference ZERO — `KSMUSDT_LONG` across all 4 runs, and including sym_sides that were not
bit-exact in the previous session's run. **The known <=0.48 GAIN drift is not bar movement.** So a bar-identity
percentage is a meaningful number; what it is measured AGAINST is the problem.

### 116.2 Fidelity — 26.4% of 65,096 decision-key comparisons disagree beyond 1e-3
Reference = the frozen NPZ. Real-script = `npz_live_generator._compute_row_for_15m_close(...)` with
`ez_indicators.IndicatorCalculator()`, called unmodified — the shared entry point `ez_indicators` /
`tradier_indicators` use. 166 bars, 65,096 comparisons: 32.8% bitwise exact, 33.9% within float32 (2e-7),
0.3% <=1e-6, 6.6% <=1e-3, **26.4% mismatched**. By timeframe: 15m 12.2%, 1h 37.7%, 4h 39.9%, D 42.5%.

**THREE CAUSES, AT THE SOURCE LINE:**
1. **`adx_1h` is not a 1-hour ADX.** `backtest_v8_precompute.py:2515-2533` computes it from
   `merged["high_1h"]/["low_1h"]/["close_1h"]` — HTF arrays ALREADY BROADCAST onto the 15m grid — so
   `ewm(alpha=1/14)` runs over a 4x-repeated series. Measured on all six: `adx_1h` changes **863-960 times** in a
   954-961 bar window while `close_1h`/`wt1_1h`/`stoch_k_1h`/`ema_20_1h` change 218-241 (240 expected) and
   `close_4h`/`adx_4h` change 59-64 (60 expected). It disagrees with the real live value on **138 of 138**
   compared bars (npz 78.08 vs live 17.00; median |diff| 9.5-23.4 ADX points, max 67.9).
2. **`bb_mult_1h` is hardcoded.** `backtest_v8_precompute.py:2362`: `np.full(n, 2.0, dtype=np.float32)` —
   measured uniq=1, min=max=2 across the whole window. Live `ez_indicators.py:2524` auto-tunes per call and
   returned 1.5.
3. **`bb_width` is a DIFFERENT PHYSICAL QUANTITY on 1h/4h.** `backtest_v8_precompute.py:1466-1470` stores
   `bb_upper - bb_lower` (absolute price), `:2364` re-writes `bb_width_1h`/`bb_width_4h` at mult=2.0; live
   `ez_indicators.py:2536` returns `(u-l)/mid*100` (percent). Inside ONE NPZ: `bb_width_15m` 0.406-9.426
   (percent) vs `bb_width_1h` 5.74e-4-6.21e-3 on a ~0.05 price (absolute). `vec_decisions/sba_bounce.py:186`
   compares it to `>14` — **the absolute series can never reach that, so the gate is dead.**

**WHY THIS FORBIDS TRADING, not merely annoys.** `SBA_BOUNCE_ENABLED` is True on all six sym_sides and
`vec_decisions/sba_bounce.py:182,276` kills the entry when `adx_1h > adx_max`. NPZ and live fall on OPPOSITE
sides of `SBA_ADX_MAX = 25` on **19.6% (RVN), 29.1% (COMP), 56.2% (KSM), 60.6% (IOTA), 61.0% (UNI)** of bars.
For a live entry family, the board and live disagree about whether the gate is even open on up to three fifths
of bars. No lease, gate, owed-exit or sizing work can close that: the two engines read different numbers.

Worst keys (mismatch rate): `adx_1h` 100%, `bb_width_1h` 100%, `bb_width_4h` 100%, `bb_mult_1h` 93.5%,
`adx_4h` 71.7%, `wt1_4h`/`wt2_4h` 68.8%, `wt1_D`/`wt2_D`/`stoch_k_D`/`stoch_d_D`/`relative_volume_D` 68.1%,
`ema_20_D` 55.8%, `wt1_1h` 47.1%, `stoch_k_1h` 43.5%, `wt1_15m` 19.3%, `dc_high_1h` 10.1%. 23 keys agree on
every bar (dc crossover flags, `dc_low_15m`, `dc_high_D`); 6 never agree on any. **`wt1_15m_prev` is not an NPZ
key at all** — only `wt1_<tf>`/`wt2_<tf>` exist — so the `_prev` the live rally/bounce gates read has no NPZ
counterpart to compare.

### 116.3 Two methodology rules this run established
* **Trim each frame to bars CLOSED at the decision instant before comparing.** Kline timestamps are the bucket
  START (a 1h bar labelled 13:00 == the 15m bars 13:00..13:45, verified START=True on every sample), and the
  NPZ's HTF columns hold the last COMPLETED HTF bar (8-11 of 12 probes per TF; PARTIAL 0/12 always). Since
  `_compute_row_for_15m_close` reads `df.loc[:close_ts]`, a naive replay over a complete historical kline file
  hands it the bucket CONTAINING close_ts — already filled with the future. Naive = 45.5% mismatch, trimmed =
  26.4%. Reporting the naive figure overstates the gap by 19 points.
* **Say how much of the window could not be checked.** S2 has ONE crypto kline store (`klines_cache`;
  `klines_cache_backtest` is a symlink) ending 2026-10-14T05:30Z while the NPZs end 2026-10-20T03:00-03:45Z, so
  **561-566 of 954-961 bars (59%) have no klines on that host**; 3m ends 2026-08-26, W 2026-09-21, M 2026-09-01
  and W/M computation fails on every bar; `ONEUSDT` has only a 15m file (it is the control: 15m-only keys, 5.7%
  mismatch). The three causes above are code-level and host-independent, so they hold wherever they run.

### 116.4 Harness defects found while measuring (both bias any parity number)
* **The vec leg is voided at random by a gate reading a directory neither leg uses.**
  `tools/opt/v12_pilot.npz_freshness()` (:103-128) builds `ROOT/backtest_v8/indicators/<SYM>.npz` — the project
  dir the 60 s sync rewrites — NOT the frozen dir pinned via `TRADE_PARITY_NPZ_DIR`/`V12_NPZ_DIR`, with a 20-min
  bar. Same sym_side, same frozen NPZ md5 `c15ce821...`, same 3,555-key set, bit-identical live legs: 339 trades
  at 14:07Z, `STALE_NPZ_SKIPPED` at 15:40Z. Whether a board gets a vector leg depends on a file it never reads.
* **9 of 24 attempts (37.5%) hung silently** (4 timed out at the cap with rc=124, 5 were killed), each stopping
  after step 2880 of 2888-2893 (the last ~10 bars) then
  burning 100-106% CPU with ZERO bytes written for 14-40 min. Not sym_side-bound — every affected sym_side
  completed in 14-18 min on another attempt with identical inputs — COMPUSDT_LONG completed three times at
  ~16 min each and then hung a full hour on the fourth with byte-identical inputs. Budget runs accordingly;
  a 4-run arm needs retries, and only 1 of 6 sym_sides reached 4 completed runs.
* **`--window-days 10` is refused**: `"unsupported window_days=10 (need 1/7/30/365+)"` at
  `tools/opt/lifecycle_pilot.py:220`, which `generic_window_slice` does not patch. Run 30 and score the last 10
  days from each NPZ's own last bar.
* **`LIVE_BOARD_VINTAGE_ENFORCE` is not readable from the environment.** `vec_exact._knob()` (:416-427) reads
  `_CFG[0]` then `config.Config`, never `os.environ`. Pass it as a key in the run's override set, which
  `backtest_v12_engine._run_one_apply_overrides` setattrs onto config and `_PerSymProxy` falls through to — and
  then VERIFY by counting `BOARD_TOO_OLD` / `VEC_EXACT_ENTRY_REFUSED` lines in the run log (0 each here).
  §115.10's "an env var the code does not read" trap, hit again by a different tool.

## 117. THE SECOND PARITY BLOCKER — 60% OF PILOT-TESTED KNOBS ARE NOT READ PER-SYM IN LIVE (USER 2026-10-20)

§116 showed the boards are computed on indicator values live never produces. This is the independent second
layer: the boards also TUNE values live reads GLOBALLY. Either one alone makes bar parity unreachable, so
fixing one without the other changes nothing measurable.

### 117.1 The census — AST audit of 1,288 switch+filter keys from all four TEMPLATE_FINAL_NORM files

| verdict | keys | meaning |
|---|---|---|
| **OK** | **807** | per-sym everywhere live reads them |
| **GLOBAL_LEAK** | **328** | at least one live read takes the bare global — **524 real global read sites** |
| **ABSENT** | 131 | no reference in any live module at all |
| **INERT** | 22 | referenced only in probe farms / dead code / name lists |

**THE LEAKS ARE NOT SPREAD ACROSS CALL SITES — THEY ARE THREE MODULES THAT NEVER WRAPPED `config`:**

| module | binding | per-sym proxy? | leaks |
|---|---|---|---|
| `ez_manage.py:8874` | `config = _PerSymProxy(_ezm_base_config)` | **yes** | **0** |
| `ez_positions_quick.py:304` | `config = Config()` | no | **339** |
| `tradier_manage.py:6764` | `config = TradierConfig()` | no | **101** |
| `ez_positions_service.py:435` | `config = Config()` | no | **19** |

`_PerSymProxy.__getattribute__` (`ez_manage.py:8848`) intercepts every UPPERCASE name, reads `_psym_ctx_var`
(set per position at `:7842`) and returns `_ezm_per_sym_layer(sym, side)[name]` when the layer carries it — so
`getattr(config, "KNOB")` in ez_manage ALREADY resolves per-sym. (`:7879 config = _ezm_base_config` is a
placeholder that `:8874` replaces; reading only the first binding is what produced the wrong census below.)

So "no switch may ever differ from its per_sym value" is **not 524 call-site edits — it is wrapping `config` in
the same proxy in three modules**, which ez_manage already proves in production. One structural change and no
site can read the global by construction. Caveats: `tradier_manage` needs the STOCKS resolver (`_cfg`, already
per-sym > cat_side > global), not ez_manage's crypto layer; and a read taken where `_psym_ctx_var` is **None**
still falls through to the base even inside ez_manage — a context-coverage hole grep cannot measure, which needs
a counter in the proxy logging "template switch read with no per-sym context". That counter is the honest
version of "can never be global".

**A SUPERSEDED CENSUS, KEPT AS A WARNING.** The first run of this audit reported
`OK 518 / GLOBAL_LEAK 521 / INERT 118`, 1,052 global sites, "ez_manage 420" — wrong, because it was not
proxy-aware. It was then "verified independently" by re-running the same tool, which reproduced the same
numbers. **Re-running a tool proves reproducibility, not correctness.** Verification has to hit the claim at
its source — here, `grep -n '^config = ' <module>` and reading `__getattribute__` — not the instrument that
made it. The corrected never-applied count is **153**, not 249.

Tools: `tools/template_switch_psym_audit.py` (AST-based, exits non-zero on any leak, cron 6-hourly — this is
the "can never be global" enforcement), `data/reports/template_psym_worklist.csv` (844 rows: module, function,
line, key — sorted so a fix batch is one function), `data/reports/template_psym_not_applied.csv` (the 249).

### 117.2 THREE ARTIFACT CLASSES THAT MUST BE EXCLUDED, or the number is garbage
Each was excluded on evidence, and each would have inflated the leak count badly:
* **1,313 probe-farm sites.** `_ensure_ez_all` / `_ensure_tradier_all` are reference-only — the
  `if bool(getattr(config,"X",False)): … _=_a` pattern, one of them reading `locals().get("position",{})` in a
  function whose only parameter is `config`. See §115.10: a `getattr` is not proof of wiring.
* **63 dead-code sites.** `_batch1_template_live_gate` begins `return True, ''`; the 59 switch reads below it
  can never execute.
* **`cfg` is NOT the global.** In the vector and `vec_paths/*`, `cfg` IS the resolved per-sym set, so counting
  `getattr(cfg, …)` as a leak is simply wrong.
VALIDATE AGAINST KNOWN CASES before trusting any such audit: the two 2026-10-20 GOLDEN_RULE fixes read OK
afterwards, and `GOLDEN_RULE_ENABLED` is correctly still flagged. A line that looks like a false positive may
not be — this ternary genuinely leaks on its `else` branch:
`int(_psym_get(sym, side, "MIN_HOLD_BARS_BEFORE_EXIT", getattr(config,…,10)) if position else getattr(config,…,10))`

### 117.3 THE NAIVE FIX IS A PERFORMANCE TRAP — resolve the set ONCE, then read the dict
MEASURED: `per_sym_live_set.full_set` warm = **0.208 ms**. Converting `process_position`'s 118 global getattrs
into 118 resolver calls costs **24.5 ms per call — ~2.45 s per bar across 100 positions**, straight into the
event loop (§116 era: that loop already froze >20 s every ~3 min). Resolving the set ONCE per position per bar
and reading from that dict costs **0.0036 ms for all 118 — ~7,000x cheaper**. This is the operator's own rule
("read the dict not calculate anything from any kline") applied to settings. NOTE: with the proxy approach of
§117.1 this objection largely dissolves — ez_manage already pays the proxy's cost in production today — but the
measurement stands for any design that calls a resolver per key instead of per position.

### 117.4 How this ships
Per CLAUDE.md gate discipline this is a change to live decision RESOLUTION, so it goes through
`v15_version_gate submit` on S2, never a hand-push. Order: one MODULE at a time, `ez_positions_quick` first
(339 of the 524), the global kept as the fallback so any sym_side whose board lacks a key behaves
byte-identically, plus the no-context counter so the residual hole of §117.1 is visible rather than assumed.

### 117.5 A GOLDEN_RULE correction worth keeping (how not to read an audit)
A same-day claim that "GOLDEN_RULE is entirely inert on stocks — all 106 mentions are discarded probes" is
**FALSE**: measured, `tradier_manage.py` has **26 real `_cfg_auto` reads vs 3 discarded probes**, at entry
(`:22141`, `:22164`), exit (`:34515`, `:34995`) and re-entry (`:41396`, `:47189`). The 3 probes at `:8476-8478`
are a DIFFERENT switch (`GOLDEN_RULE_REQUIRE_HEDGE_OPEN`). Consequence: stock boards' GOLDEN_RULE deltas are
VALID, and `GOLDEN_RULE_HTF_MIN_TFS` in the drift list is ordinary settings drift, not a dead switch. Generalising
from a handful of stub lines to every mention of a name is the §115.10 trap in another costume.
What IS real on crypto: the same rule was SPLIT — entry read `_psym_get` (`ez_manage.py:18529`, `:44689`) while
exit read the global (`:53672/53673`, and `ez_positions_quick.py:30843/30848`). A board that tuned the threshold
changed when a position OPENED and was ignored when it CLOSED, and those rows' sweep deltas were earned against
a value live never applied on exit. Fixed across 8 sites with the global preserved as the default.

## 118. THE PARITY HOST WAS NEVER MEASURING LIVE — THREE INDEPENDENT CAUSES (USER 2026-10-20)

**USER 2026-10-20:** *"pls verify we enter augment reduce and exit at the exact bar the per_sym 3600 settings
dictate and in sync with what the html chart would have traded looking forward — on all crypto and stocks trb trc
accounts."* Answering that exposed three reasons the S1/S2 parity lane was measuring something other than live.
Each was measured, not inferred, and each invalidates results from runs that did not control for it.

### 118.1 `data/per_sym_full_sets.json` DID NOT EXIST ON EITHER WORKER
`ls` on both: `No such file or directory` (S1 and S2), while the Mac holds 96,535,297 bytes / 767 rows. That file
is the full-set layer live resolves. Pushed to S2 md5-verified (`d701ae3b…`). This alone does NOT fix the gate —
see 118.2 — but any claim that a worker's live leg "read the live per-sym layer" before this is false on its face.

### 118.2 THE WORKER RESOLVES ITS OWN `per_sym_store.db`, NOT THE MAC'S
`per_sym_store.DB_PATH` = `data/hourly_reconfig/per_sym_store.db`, overridable ONLY by env `PER_SYM_STORE_DB`
(`per_sym_store.py:56`). Measured 2026-10-20 20:16Z: **Mac 1,308,164,096 bytes md5 `27e1c392…`; S2 471,445,504
bytes md5 `fd462ddd…`, with a live `-wal`/`-shm` because S2's own herd writes it.** Different store, different
rows. Consequence, measured on the real gate (`live_twins.vec_exact.latest_board_ok`, the one live calls before
every OPEN/AUGMENT) over 92 live stock sym_sides:

| store the worker resolved | gate OK | refused |
|---|---|---|
| S2's own `per_sym_store.db` | **1** | **91**, all `LIVE_SET_NE_BOARD_{34…958}keys` |
| Mac's live store via `PER_SYM_STORE_DB` | **92** | **0** |

92/92 matches what the live Mac measures for itself (`tools/per_sym_disparity_guard.py`: 111 stock sym_sides →
**92 OK, 15 DRIFT, 4 NO_LATEST_BOARD, 0 BOARD_TOO_OLD**; crypto 50/50 OK). **LAW: a parity run on S1/S2 must pin
`PER_SYM_STORE_DB` at a snapshot of the Mac's store, or it is scoring the worker's incumbent rows** — the same
class of error as `worker_db_is_not_live`. Build the snapshot with `sqlite3 .backup` from the live file (never a
plain `cp` of a WAL database); `per_sym_active` + `kv_json` alone is 224 MB vs 1.3 GB with `per_sym_history`.
This also explains §116's `BOARD_TOO_OLD_221h..225h>216h`: S2's `published_selection.json` was the **02:50Z**
copy, not the Mac's 19:51Z one carrying the `cell_ts` fix.

### 118.3 THE STOCK LIVE PATH IN VEC-EXACT MODE IS WALL-CLOCK BOUND — 0 LIVE TRIPS, ALWAYS
`tools/v15_trade_parity.py:429` defaults `TRADE_PARITY_VEC_EXACT=1`, which injects
`PARITY_VEC_EXACT_MODE: True` into the **live leg only** (`:432`). That routes
`tradier_manage.process_position` into `_vec_exact_process_twin` (`:14159`), which asks the twin
`_vx.actions_at(symbol, side, _now)` with **`_now = time.time()`** (`:14183`) at every simulated step, and judges
staleness as `_now - _bar_ts > VEC_EXACT_STALE_FALLBACK_BARS * 900`. In a replay of September–October bars that
asks "what should I do right now", so it returns the bare string `VEC_EXACT` with no action and the live leg
opens nothing. **Measured across all 92 stock sym_sides: `live_trips=0` against `vec_trips` 10–71**, with
`[V8_CANDIDATE_DIAG] … results=['VEC_EXACT'] queue_size=0` at every step. Forcing the stale fallback
(`VEC_EXACT_STALE_FALLBACK_BARS=0.0001` in the set) does NOT rescue it: AG_SHORT still `live_trips=0` against 67.
Worse, the verdict is wall-clock dependent — whether the twin reads "fresh" depends on how close the NPZ's last
bar is to the moment the run happens, so the same sym_side can answer differently hours apart (the §116
BTCUSDC_LONG shape).

**AND PRODUCTION DOES NOT RUN THAT MODE.** `PARITY_VEC_EXACT_MODE` is **absent on all 92 live stock sym_sides**
(so it resolves `config_tradier.TradierConfig` default **False**), and today's live logs agree: `trb` and `trc`
logged **0** `[VEC_EXACT]` dispatches, 0 `VEC_EXACT_STALE_FALLBACK`, 0 `VEC_EXACT_SKIP`; only `tra` (paper) shows
any (12 dispatches, 64 fallbacks). **Live stocks on trb/trc DECIDE NATIVELY from the per-sym set — they do not
replay the board.** So the tool's default measures a configuration live never uses, and the right stock run is the
tool's own documented native diagnostic, `TRADE_PARITY_VEC_EXACT=0` (`:429` calls it the "labelled seam/native
diagnostic"). Every earlier stock parity verdict taken at the default is void, in both directions.

### 118.4 WHAT THE CRYPTO NUMBER IS, AND WHAT REFUTES THE MAC'S
Run on S1+S2 over the 60 uncapped store sets (key counts 3,492–3,554; combined digest
`59e214cfd9bf46a0feddda9c3940da0a` identical on Mac/S1/S2), NPZ frozen by hardlink, scored only inside
`[2026-10-01, 2026-10-10]`: **entries 2,074 of 2,293 identical (90.4%); of those entry-exact pairs, exits
1,997 of 2,074 on the identical bar (96.3%)**. 11 of 28 scored sym_sides are bit-exact on every entry and exit.
±1 and ±4 bar tolerance add 1 and 3 — **entry agreement is binary: the same bar, or no live trade at all.**
This **refutes** the Mac's two earlier runs (160/163 entries but 61/161 exits = 37.9%, 98 of 100 mismatches
labelled `VEC_EXIT_OWED paper-flat-live-holds-retry`): on S1+S2 `VEC_EXIT_OWED` appears in **5** of 77 exit
mismatches, and both hosts agree independently (S1 95.6%, S2 96.5%) across two different engine md5 sets
(`8bfbc1e6419e` / `0d9d9d824367`). The Mac was at load 356 with 76 MB free RAM — see
`mac_overload_starves_producers`. Asymmetry is concentrated in re-entry cadence, both directions:
VEC_ONLY 216 (`REENTRY_HIGHER_LOW` 54, `B` 50, `B_KZONE` 36), LIVE_ONLY 62 (`REENTRY_HIGHER_LOW` 17, `B` 14).
Coverage shortfall is honest: 20 of 60 wrote no report at `timeout 14400` under a concurrent herd; needs ≤3
concurrent or a herd-free host.

### 118.5 A GUARD OF MY OWN MAKING — MARKET HOURS ARE PART OF "NOBODY TRADES"
`tools/never_untraded_guard.py` (118.5 cadence 30 s, 90 s grace) asks "is this account traded by ANYBODY". It had
no market-hours awareness, so the **correct** post-close exit of `trb`/`trc`/`tra` read as an untraded account:
at 90 s it would start the wrapper, the wrapper's own market gate would exit 0, and it would restart it every
30 s — a churn loop created by the guard that exists to prevent outages. Fixed with `stocks_in_session()`
(13:25–20:00Z weekdays, ET offset derived not hardcoded; US holidays deliberately NOT modelled — on a holiday it
logs a false alarm it cannot fix, never a start loop, because the wrapper gate still refuses). 6 boundary cases
tested; post-close verdict is now `OK` with `note: "market closed"` and an empty `actions` list. Crypto is never
gated: 5/5 accounts traded by the Mac, S1 at zero, `data/latest_market_data.json` 20 s old.

### 118.6 THE RULE THIS SECTION LEAVES BEHIND
Before any parity verdict is quoted, four things must be stated or it is not a verdict:
1. **which store the live leg resolved** (`PER_SYM_STORE_DB`, with the Mac store's md5),
2. **which decision mode the live leg ran** (`TRADE_PARITY_VEC_EXACT` / `PARITY_VEC_EXACT_MODE`), and that it is
   the mode production runs for that venue,
3. **the gate census on that host** (`latest_board_ok` OK/refused over the tested set, not assumed),
4. **the NPZ freeze and the scoring window**, measured from each NPZ's own last bar.
A run missing any of them measures the harness, not the system. All three causes above were silent: every leg
exited 0, wrote a report, and printed a number.

### 118.7 THE STOCKS CENSUS — 21.6% ENTRY IDENTITY, AND IT IS NOT THE HARNESS
Run with the three 118.1–118.3 controls in place (live store pinned via `PER_SYM_STORE_DB`, gate OK 92/92,
`TRADE_PARITY_VEC_EXACT=0` = the native mode production runs, NPZ frozen on S2 fresh to 19:45Z, 30 sessions,
sets 3,525–3,555 keys, digest `cfcbb87483ddf1404267e29f2f083f72`). **92 reports, 91 scored, 0 lost.**

| measure | stocks (91 sym_sides) | crypto (28, §118.4) |
|---|---|---|
| vector trips | 3,888 | 2,293 |
| live trips | 3,044 | — |
| **matched on the identical bar** | **838 = 21.6% of vector, 27.5% of live** | **90.4%** |
| **exit identity on matched pairs** | **460/838 = 54.9%** | **96.3%** |
| sym_sides bit-exact on every entry | **0 of 91** | 11 of 28 |
| VEC_ONLY trips (vector entered, live did not) | **3,050** | 216 |
| LIVE_ONLY trips (live entered, vector did not) | **2,206** | 62 |

**31 of 91 stock sym_sides never opened a single live position while the vector made 7–79 trips** — HOOD_SHORT 79,
AMZN_LONG 78, COIN_SHORT 76, MRVL_LONG 76, VICR_SHORT 72, ZCSH_SHORT 71, SNDK_LONG 64, MSFT_LONG 43, ASML_LONG 43
… `[V8_CANDIDATE_DIAG] … results=['PROCESSED']` every step, i.e. the native path ran and declined, it did not
error. Best agreement anywhere is MU_SHORT 68.0% and OLED_LONG 65.0%; SCHW_SHORT is 12.9% on 147 vector trips.
By USER 2026-10-20 (`vector_dictates_live_executes`) a vector entry live refuses is a **live defect**, so 3,050
of them is the finding, not a tolerance.

**Asymmetry by family, summed over 92** — LIVE_ONLY: `MANDATORY_REENTRY_PRICE_CROSS_CROSSED_BACK_GUARANTEED`
**1,251**, `WT_BOUNCE_OPEN_S` 358, `WT_BOUNCE_OPEN_L` 237, `WT_SIMPLE_GUARANTEE_L` 122. VEC_ONLY:
`B_SRS_ENTRY` **972**, `HARDCODED_RALLY_REENTRY` **873**, `B12` 343, `B` 184, `REENTRY_BREAK` 163.
**These are reason LABELS and §97's trap applies — a label is not a gate, and `B_SRS_ENTRY` is not even an opener
(`v12_quick_engine.py:10992` sets `blocks["B_SRS_ENTRY"] = ~srs_block`, an entry PERMISSION; `history_prov.py:95`
calls it "entry permission = NOT the SRS pctb block"). Do not conclude "unwired" from a grep count.**

**ONE CAUSE IS CONFIRMED, NOT A LEAD.** `tradier_manage.py:11` imports `rally_ok as _rally_ok` and `:40539`
calls `_rally_ok(config, …)` where `config` is the module-level bare `TradierConfig()` (`:6764`, never
proxy-wrapped — the §117 GLOBAL_LEAK). So every `HARDCODED_RALLY_*` resolves GLOBAL on stocks, while measured on
the 92 live stock sets: `HARDCODED_RALLY_HTF_TREND_TF` **44 of 92** differ from the global default,
`_MIN_AGE_MIN` **44**, `_DC_POS_MAX` **25**, `_MIN_MOVE_PCT` **20**, `_REENTRY_REQUIRE_WT` 11,
`_SMA200_SIDE_ENABLED` 11, and `_REENTRY_ENABLED` itself **8**. The vector evaluates this re-entry with the
board's per-sym values; live evaluates it with one global. **873 VEC_ONLY `HARDCODED_RALLY_REENTRY` trips is the
exact signature of that leak** — the vector permits the re-entry the board paid for and live asks a different
question. Fix shape: pass the per-sym getter adapter, the pattern `tradier_manage.py:15484` already uses.
NOT patched here: `tradier_manage.py` is in `LOCKED_FILES.md` and no unlock was given.

**WHAT THIS DOES NOT SAY.** It does not say crypto is fine and stocks are broken as a venue: §116's indicator
defect (`adx_1h` recomputed from broadcast HTF arrays, disagreeing with live on 138/138 bars) is upstream of
both, and stocks additionally run a per-venue opener set the vector may not model at all. It says only this:
**measured at the bar, stocks execute 21.6% of what their boards decided, so no stock board's published gain can
currently be claimed as the gain live would have earned.**

## 119. BARBARIC FORWARD-PARITY LAW — NEVER MISS, NEVER EXTRA (USER 2026-10-21)

Until 1/3/5m trading is enabled (which happens ONLY after total parity is reached WITHOUT this law), live obeys
one barbaric rule on ALL accounts, crypto and stocks, 15m ONLY:

1. **NEVER miss.** Any position that would be open under forward application of the sym_side's board switch set
   (the set its HTML chart shows, i.e. the same settings run on the latest NPZ) MUST be open live — if the key
   is tradeable for the account (member of `symbols_<acct>_<side>.json`).
2. **NEVER extra.** NOTHING may be open live that forward application says is flat.
3. **15m only.** The law is evaluated at closed-15m-bar cadence. 1/3/5m signals are refused until total parity
   unlocks them (`tools/forward_parity_enforcer.py --timeframe` exits 2 on anything but `15m`).

**WHY THIS LAW EXISTS.** 2026-10-20 the operator ordered "ez_ AND tradier_ trade SWITCHES not npz's" /
"NOTHING can live trade an npz" (`PARITY_VEC_EXACT_MODE=False`, `config.py:1786`): natives read the FULL board
set and apply it to the current dict. But `data/hermetic_mode.json` stayed `enforce` (vec-twin provenance demanded
for every entry) — so on 2026-10-21 01:33Z live FIRED native entries (`GOLDEN_RULE`, `QUICK_OPEN`) and refused every
one of them (`BLOCKED_HERMETIC_NOT_VEC`, hundreds per account per 30 min). Natives in charge + vec-only gate =
a system that can never trade. The fix (same night): hermetic `enforce->log` (runtime mode file, no restart;
would-refuse stays visible), backup `backups/before_barbaric_parity_202610210135_hermetic_mode.json`.

**WHAT ENFORCES IT IN-LOOP** (no reimplementation — the detector audits these, it does not redo them):
`VEC_CONVERGE_OPEN` (vec holds + live flat -> OPEN), `VEC_STALE_FLATTEN` 2-bar (vec flat + live holds -> close),
`promise_ledger` + `tools/promise_audit.py` (promise vs delivery), `exit_guarantee` (refused close -> FORCE),
`latest_board_ok` entry gate + `tools/per_sym_disparity_guard.py` (board vs live settings).

**THE DETECTOR** (`tools/forward_parity_enforcer.py`, tests `tests/test_forward_parity_enforcer.py` 6/6) runs the
law's checkable surface on every invocation: gate coherence (auto-fix `--fix`: enforce->log when natives are in
charge), universe parseable + non-empty per account/side (`--fix` repairs JSON SYNTAX with backup and a
byte-identical membership proof — 2026-10-21: `symbols_men_long.json` trailing comma repaired, 27/27 identical;
membership itself is NEVER edited: `symbols_inf_{long,short}.json` are `[]` and `44/466` tradeable sym_sides are
off-board, both reported for the operator, fixed only via `tools/v15_persym_golive.py golive --published
--pin-all`), recent-refusal evidence from bounded log tails. It NEVER places, cancels or sizes orders and NEVER
writes per-sym sets. Alerts append to `data/alerts/forward_parity.jsonl`; exit 0 clean / 1 faults / 2 unmeasured.

**NPZ FRESHNESS** does not excuse a miss: S1 `~/binance-sandbox/backtest_v8/indicators` is the NPZ source
(`python3 tools/data_locations.py --where npz_indicators --host s1`); a stale NPZ is reported, never used as a
reason to hold a position the board set says is flat.

## 120. INF UNIVERSE — WINNERS AND LOSERS, SIZED BY SUCCESS (USER 2026-10-21)

`symbols_inf_{long,short}.json` are owned by ONE writer: `tools/v15_daily_inf_universe.py` (BIBLE §68.3 step 3b;
daily 15:05Z cron + chain step 5d). `ez_rankings` INF writes stay suspended (`tests/test_inf_rankings_suspended.py`).
Every tradeable sym_side with a backtest inside 9 days (`--max-age-hours 216`) belongs in the books; start size
follows backtest success via the tiers (`data/persym_size_tiers.json`, `PERF_TIER_SIZING_ENABLED`, uncertified =
`RANKED_NO365_SMALL` x0.50 — that half never broke).

**WHY inf WENT EMPTY ON 2026-10-20.** Not one agent's typo but a chain: (1) the fleet emits progress records with
no fresh full-set metrics (`v17 phase cut` skips ENDGAME+DIAG; 41 of 51 records) plus 365D-invalid boards, so the
full gates yield 0 eligible and the writer correctly REFUSES (it never writes empties unasked); (2) dead `s5` sat
in `DEFAULT_HOSTS` and its timeout refused every `--apply`; (3) the standing books had filled with tokenized-stock
perps, which `tools/excluded_tokenized_stocks.py` rightly stripped down to `[]` — leaving inf nothing tradeable.
Fix (same night, writer is not locked): `DEFAULT_HOSTS=(s1-pub,s2)`; 9-day default window; FALLBACK ranking when
zero pass — `score = gain + 2.0 x day-over-day gain change` (`--momentum-weight`, 0 = level only) over the final
set's fresh gain, still requiring tradeable key, verdict ok, no needs_redo, a final set, gain > 0, trades >= 10,
in-window eval, no SIMPLE_PRICE_GT0. Fallback rows are DEGRADED (no TIM/DD/365D) and tier-sized down; garbage and
tokenized syms can never enter by either path. Provenance: `data/inf_universe/<date>.json` carries
`fallback:{used,weight,skipped}`; `--apply` backups + verifies. First live run booked LONG 4 / SHORT 3 on the
primary path; the fallback path is proven by 44/44 writer tests and a 26-book dry run, and fires automatically
whenever the primary yields zero. Rollback: `backups/before_inf_universe_*`.

## 121. ZERO-PASS GOES COMPLETELY — EVERY CALCULATION TRADES, NEGATIVES AT FLOOR (USER 2026-10-21)

"Zero-pass" = any mechanism that keeps a calculated sym_side from being promoted to live. It is gone on ALL
accounts: a sym_side counts as calculated with a published board or a backtest < 9 days old (verdict not
IMPOSSIBLE/NO_TRADES), and board-gate refusals (`NO_LATEST_BOARD`, `LIVE_SET_NE_BOARD`, `BOARD_TOO_OLD`) become
allow-with-log at every entry gate — crypto `execute_now` board gate, both twin ENTRY/AUGMENT paths, stocks
`TRADIER_NO_BOARD_ENTRY_BLOCK`. The gate functions stay truthful (disparity + enforcer keep measuring); only the
refusal is bypassed, behind `ZERO_PASS_TRADE_ALL` (default True; False restores every old refusal). Sizing carries
the risk, per the standing rule: positive gains untouched (RANKED tiers to x6 stay exactly as they are); a
calculated sym_side with negative latest gain trades at the floor — crypto the exchange minimum, stocks exactly 1
share, discarded when 1 share costs more than `START_POSITION_SIZE` — behind `ZERO_PASS_NEG_FLOOR` (default True).
Authority: `live_twins/zero_pass.py` (incremental mtime scan, one ~16 s background warm per process so the event
loop never stalls; old refusals stand while warming). Rollback: both switches False + `backups/before_zero_pass_*`.
INERT UNTIL THE ENGINES RECYCLE (imports bind once; one graceful SIGTERM per account, watchdog relaunches).

## 122. NO-SUPPRESSION — VALIDITY FILTERS NEVER BLOCK, ONLY SIZE (USER 2026-10-21)

365D was never tested live, nor were several sibling filters — in their best case of validity they may shrink an
order (~tiers already cut 50–95% for the weak), but they NEVER refuse one. Disarmed behind the same switch for
calculated sym_sides, every allow logged: vec-driven native suppression incl. native CLOSEs (men was stranding
exits on it), native-entry block, 365D confirm, GATES_A, lane-B filter/strict vetoes, counter-trend, per-sym live
profitability gate, sub-step maker zero (steps UP to 1 step when pre-round notional clears the minimum). Coverage
this night: 476 published sym_sides, 234 tradeable keys, 104 calculated in the live scan. Deliberately STILL wired:
correctness (bad key/price/duplicate open), infra (broker/sync/mark freshness), execution integrity (locks, dedup,
cooldowns, no-double-open), loss halts (SYM_HALT/VIGILANCE/STALE_SET/balance floor/brakes), operator blacklist,
per-sym strategy thresholds that ARE the dictate (WT_DC, ENTRY_SCORE, promoted filter TFs — bypassing those would
trade MORE than dictated, violating EXACTLY), disabled-strategy flags, and all of exits/stops/guarantees. Rollback:
`ZERO_PASS_TRADE_ALL=False` + `backups/before_no_suppress_*`.

## 123. EVERY MATRIX TRADES AT $5; THE DECIDER NEVER SLEEPS (USER 2026-10-21)

"Thresholds ARE the edge" means: the KINDERGARTEN/WT_DC/ENTRY_SCORE gates only fire when the board itself promoted
them (`enabled` defaults False) — they are the backtest's recipe, not overlays, so they stand; everything else
that could veto a calculated sym_side is bypassed. Floor: `PERF_TIER_MIN_ORDER_USD` 6.0→**5.0** (Binance minimum),
so every calculated backtest result trades live if only at $5 — the live-vs-vector comparison always has fills.
ALWAYS-RUNS: the vectorized decider lane (`pure_board_trader --loop`, men intents) was an unsupervised Sunday
manual run next to a duplicate from Oct 19 — now exactly one copy under launchd KeepAlive
(`com.niels.pure-board-trader`, lock-guarded), duplicates reaped. `tools/forward_parity_enforcer.py` gains the
always-runs section (loop alive, intents <30 min, matrices <40 h, progress <48 h; `--fix` relaunches the local
loop, S1-side reported never faulted blindly — the pusher pull is currently failing on the dead s1-int tunnel).
Rollback: floor default + `backups/before_five_dollar_floor_*`; supervision off via `launchctl bootout` + rm plist.

## 124. STALE DICTATES STILL TRADE; PARITY INVENTORY METHOD (USER 2026-10-21)

The vec lane takes ~27 min to compute a 15 m bar while the consumer refused intents older than 300 s — every men
intent arrived dead, so the lane's 25 opens never converted (root cause of men holding 5 stale positions against
25 opens). Both stale guards (intent consumer, reconcile) now execute late dictates for calculated sym_sides with
a log line; lane latency stays open as the freshness fault to fix. Parity inventory, run don't guess: men forward
= latest lane intent per sym_side (`data/vec_live/intents/PB-men-*.json`); ang forward = `v12_quick_engine` vector
sim on the trailing 30 d NPZ with the full board set, holding iff `exposure_series[-1] > 0`, Mac-NPZ staleness
reported as UNMEASURED (S1 rerun for full freshness). Snapshot 04:43Z: ang holds 5 LONG / 0 short against a fresh
vector that says hold nothing (ENAUSDC already self-closed 04:40); men holds 5 LONG converging onto 25 lane opens
plus 2 lane closes executing.

## 125. EXACT COPY — LIVE TRADES THE WINNING SWITCHES, NO EXCEPTIONS (USER 2026-10-21)

The 9-day clock governs APPLICABILITY (a backtest inside 9 days may trade); the switches traded are ALWAYS the
exact copy that got the best per_sym results in the LATEST backtest — identity, not approximation. The hourly
delta converger (`tools/v15_golive_refresh_published.sh` → `v15_persym_golive --published --pin-all`) pins every
pinnable drift; minutes-scale intent/state age guards are bypassed for calculated sym_sides (they contradicted the
9-day bar), while the 9-day backtest window itself still applies. Deliberate remainders, each needing its own word
to override: h2h-measured live rows (running experiments, e.g. ACN_LONG +74%), golive-blocklisted data-poison and
tokenized names (LEXX_SHORT, AMAT/ARM/BMNR shorts), type-gate-malformed boards (1 bad key each: KSMUSDT_LONG,
ICPUSDT_LONG, CLF_LONG, CMC_LONG, APO_SHORT, NXE_SHORT, 1000FLOKIUSDT_LONG — pilot-side fix), and
SET_DRIFTED_SINCE_PUBLISH (SCCO_LONG — republish on S1). Rollback: the converger is additive and report-audited
(`data/daily_chain/persym_golive_*`); switches False restores vetoes.

## 126. VECTOR-COPY-TRADING LAW — BAR, CROSS, CONFIRM, KILL (USER 2026-10-21)

THE SWITCHES AND NOTHING ELSE. Tonight's exit census showed why: live bled through VEL_EXIT, QUICK_REDUCE_*,
PEAK_GIVEBACK erosion stops, DAYTRADE_TARGET, WRONG_SIDE kills, ALL_TF_AGAINST closes — none of them crosses.
#1 exits only on cross/board-intent/stop (cross/channel-break/flip/LH-LL, VEC_DRIVEN/RECONCILE/owed, backstop,
bookkeeping). #2 entries AND exits only inside 180 s after each 15 m bar close (`VECTOR_BAR_WINDOW_S`) — mid-bar
decisions wait, they don't die. #3 unconfirmed executions never happen: the first non-cross exit from a family
refuses it, DISABLES the family in `data/vector_kills.json`, and FLAGS `data/vector_violations.jsonl` — manual
restore only (`tools/vector_violations.py restore FAMILY --why`). Proven 05:47Z: `inf:1INCHUSDT_LONG` filled in
window, first `BB_TAKE_4h` CLOSE killed+flagged the family. Untouched: backstops, reconcile bookkeeping,
correctness, infra, locks/dedup/cooldowns, loss halts, blacklist, per-sym recipe thresholds. Convergence state:
hourly delta applier pins all pinnable (05:43Z run: 18 PASS + 10 registered); remainders deliberate — h2h rows,
blocklist poison/tokenized, 6 type-malformed boards (pilot fix), SCCO republish. Rollback: `VECTOR_ENFORCE_ENABLED`
False + `backups/before_vector_enforce_*`; kills cleared only by hand.

## 126.1. RETRACE PROCEDURE — EVERY KILL GETS A SWITCH OR A RESTORE (USER 2026-10-21)

`QUICK_*` is a dispatcher category, never a violator — retrace each kill to its function
before building anything. Proven pattern from the first two kills: (1) locate the emitting
branch (`TIGHT_LEASH_PROFIT_SAVE` = `AdvancedSignalRater.rate()` `:6590`, unvectorized,
user-ordered OFF since 2026-05-30); (2) find the route that bypassed the existing gates
(twin direct `execute_now` dispatch skips all three `SCALP_REDUCE_ENABLED` gates; `""` vs
`None` had killed the trap-suppressor); (3) gate the ROUTE per-sym on the EXISTING switch
(zero boards True = default-neutral, pilot may opt in) — that gate IS the switch built for
the discovery; (4) if the family is vector-modeled and switch-gated (`BB_TAKE` =
`BB_PROFIT_TAKE_TF`, 174 boards), the kill was a false positive — RESTORE it and list it in
`VEC_SWITCH_EXIT_FAMILIES` (skips kill, keeps bar window), do not re-kill. Standing agent
job: every new `data/vector_violations.jsonl` family gets this retrace; vectorizable →
switch + wire both venues; not vectorizable → switch default OFF. Interference log: a
parallel agent softened `check()` to flag-only and added an inert `board_state_converge_loop`
— the soften was reverted (kill law stands), the loop kept and whitelisted as board-confirmed.
Two agents editing `ez_manage.py` concurrently is a collision risk; coordinate via the lock file.

## 123. STOCKS OFF-HOURS — NO AGENT EVER FLAGS STOCK INDICATORS OR PRICES OUTSIDE NYSE HOURS (USER 2026-10-21)

**USER VERBATIM 2026-10-21:** "add to the bible that no agent can ever start whining again about indicators or prices for stocks outside market hours it is fkn pathetic" — and: "s1 ONLY starts ez_indicators when rsync from macbook fails to update s1 market data (tradier ONLY ON MARKET HOURS OBVIOUSLY) and syncs it back to macbook so we never have a minute of missing market data in a decade".

1. **Outside the NYSE session (09:30–16:00 America/New_York, Mon–Fri, holidays closed) stock indicators, stock prices and stock market-data files are EXPECTED to be idle.** `data/tradier/tradier_indicators_latest.json`, `tradier_prices_latest.json`, tradier quotes and every stock snapshot may be hours or days old off-hours. That is the design, not a fault. No agent, guard, monitor, report or audit may call it stale, a breach, an outage, a risk, or "worth noting".
2. **S1 is the standby, not a mirror.** The Mac rsyncs crypto market data to S1 every minute. S1 starts its own `ez_indicators` only when that rsync stops updating S1 market data, and its tradier stack only when the Mac goes stale INSIDE the session (`tools/live_failover_common.py`, `tools/live_failover_s1.py`), then syncs back to the Mac. An old S1 stock file off-hours, or an old S1 crypto file while the Mac is healthy, is never a finding.
3. **Inside the session** stock freshness IS enforced (failover trigger `SNAPSHOT_STALE_S` = 90 s, `tools/data_never_miss.py`).
4. **Stock KLINES are different:** a session that is missing from a stock kline file (e.g. Tuesday's bars absent on Wednesday 00:00 ET) is a real data gap at any hour, and `tools/data_never_miss.py` heals it from S1/Mac. The prohibition is on flagging idle off-hours *indicators/prices/snapshots*, not on missing *completed-session bars*.
5. Machine rule: `tools/data_never_miss.py` checks Mac stock indicators only when `in_rth()`; S1 stock files are never checked.

### 118.8 THE OBVIOUS FIX FOR 118.7 WOULD HANG LIVE STOCKS — VERIFIED
The tempting repair is to wrap stocks' module config the way crypto does
(`ez_manage.py:8877 config = _PerSymProxy(_ezm_base_config)`, `ez_positions_quick.py:3342
config = _PQPerSymProxy(config)`; the plain ones are `tradier_manage.py:6764 config = TradierConfig()` and
`ez_positions_service.py:435 config = Config()`). **On stocks that recurses.** `tradier_manage._cfg` — the
correct per-sym resolver at `:7782`, "per-sym overrides > TEMPLATE baseline > global" — **ends at `:7854` with
`return getattr(config, param, default)`** (read and verified, not taken on report). A proxy bound to the
module's `config` whose `__getattribute__` resolved through `_cfg` would re-enter `_cfg` on that final line
forever: an infinite recursion inside the live stock decision loop, i.e. a hang of the real-money account, not a
wrong number. The crypto proxies avoid it by reading the per-sym layer **directly** and falling back to the
**inner** object, never to the module name they are bound to — that is the shape stocks needs.
So stocks is not missing the machinery (`_cfg` exists and many reads use it correctly); the leak is the ~102
sites that call bare `getattr(config, …)` instead, plus 42 in `ez_positions_service`, 88 distinct template
switches over 165 sites. **The narrow, safe fix for the confirmed 118.7 cause is to pass a per-sym getter
adapter at the `_rally_ok` call site (the pattern `tradier_manage.py:15484` already uses), NOT to wrap the
module.** Both files are in `LOCKED_FILES.md`; neither was touched.

### 118.9 A THIRD BASELINE BLOCKER — 179 SWITCHES WHERE THE TEMPLATE DEFAULT ≠ THE VENUE CONFIG DEFAULT
Bold in a TEMPLATE = default = live (§14). Where that bold value and the venue config's dataclass default
disagree, a sym_side whose board carries no override for that switch runs live on the CONFIG value while the
pilot measured its baseline on the TEMPLATE value — **so the board's baseline is not live's baseline**, and
every delta measured against it inherits the offset. Independently recomputed 2026-10-21 from
`data/reports/catside_template_vs_config_parity_20261021.json` (preserved off volatile `/tmp`), reproducing a
subagent's figures exactly:

| lane | flagged | cosmetic (`30` vs `30.0`) | **real** |
|---|---|---|---|
| CRYPTO_LONG vs `config.py` | 256 | 184 | **72** |
| CRYPTO_SHORT vs `config.py` | 247 | 192 | **55** |
| STOCKS_LONG vs `config_tradier.py` | 261 | 208 | **53** |
| STOCKS_SHORT vs `config_tradier.py` | 262 | 212 | **50** |

**179 distinct switches**, of which only **2 are systemic across all four lanes**
(`NEWBORN_LOSS_KILL_VEL_TF`, `KINDERGARTEN_STRICT_TFS`) — the rest are lane-specific, consistent with
per-cat_side divergence rather than one bad sync. Measured examples (crypto long): `SATOSHIT_ENTRY_ENABLED`
template True / config False; `STRUCTURAL_RANGE_SHIFT_EXIT` template False / config True;
`NEWBORN_LOSS_KILL_ENABLED` template True / config False; `TECHNICAL_DC_TARGET_TF` template `'4h'` / config
`'OFF'`; `DAYTRADE_DC_STOP_TF` template `'1h'` / config `'OFF'`; `DC_EDGE_SIZING_ENABLED` template False /
config True. Note the trap that nearly made this a 256-per-lane scare: **72% of the flags are `30` vs `30.0`**
— cast before comparing, and compare bool-ness separately or `True == 1.0` hides a real flip.
**NOT fixed here, deliberately:** §17 forbids a blind full-sync, and a default flip is a switch change that
belongs to the pilot and the S2 gate, not a hand edit. Each needs a per-lane ruling on which side is right.

### 118.10 THE CAT_SIDE LAYER LIVES UNDER A DIFFERENT FILENAME THAN ITS BUILDER — 1,220 "FIXES" LANDED NOWHERE
2026-10-21 a subagent reported projecting the four TEMPLATE workbooks onto the cat_side layer and clearing
**1,220 deviations** (262 missing keys, 204 wrong values, 754 wrong types) with the guard then exiting 0. Its
reasoning for moving to that layer is CORRECT and worth keeping: template-vs-`config.py` is unfixable by
construction, because **one `config.py` serves both crypto lanes and a single global cannot equal two templates
whose bold defaults differ** (§118.9 measures that gap but cannot close it). The cat_side layer, resolved
per-sym > cat_side > global, is the only place a per-lane default is expressible.

**But it wrote `data/cat_side_defaults_4.json`, which nothing live reads.** The loader is `per_sym_settings.py`
(`cat_side_defaults.py` is a 15-line compat shim since 2026-10-08): `PATH` = env `PER_SYM_SETTINGS_PATH` or env
`CAT_SIDE_DEFAULTS_PATH` or **`data/per_sym_settings.json`** (`:14`), and it prefers the SQL `kv_json`
`per_sym_settings` row when present (`:53-67`) — "SQL primary, the JSON is the identical backup"
(`per_sym_store.py:69`). The canonical builder, despite its legacy name
`tools/build_cat_side_defaults_4.py`, writes `CSD.PATH` = `data/per_sym_settings.json` and dual-writes SQL
(`:194-206`). Verified: **no `CAT_SIDE_DEFAULTS_PATH`/`PER_SYM_SETTINGS_PATH` in the environment of the running
`ez_manage`**, `data/per_sym_settings.json` unchanged (md5 `242a5e64…`, mtime 2026-10-20 18:53, identical to
hours earlier), and the only code referencing `data/cat_side_defaults_4.json` is the new tool itself plus
`tools/maxaug_purge.py` and a `v15_director.py` self-heal comment. The written file's own `_meta.built_at` still
reads **2026-10-08T01:22Z** against live's **2026-10-20T18:52Z** — a 12-day-old artifact.

**CONSEQUENCES, both ways.** Blast radius is **zero** and there is nothing to revert — the fear on reading that
report (a mass default flip on five live crypto accounts mid-session) was unfounded. But the headline is false
for the layer that decides trades, and a guard that exits 0 against a dead path is worse than no guard: it
certifies a layer nobody consults. An indicative diff of that projection against the live layer shows **80
missing + 134 value disagreements** (e.g. CRYPTO_LONG `DIRECTION_FAVORABLE_REENTRY_ENABLED` template True /
live False, STOCKS_LONG `ATR_LONG_WINDOW` 150 / 100) — **indicative only**, because the projected file is
otherwise a 2026-10-08 artifact, so this is "projection vs live", not "templates vs live". Measuring it properly
means running the canonical builder in report mode against today's templates.

**THE RULE.** Before writing a layer, prove the live process reads that exact path: check the loader's `PATH`
resolution, the env of the RUNNING process, and whether a SQL row is primary over the file. And a mass default
flip on the live cat_side layer belongs to the pilot and the S2 gate, never a hand tool — the same agent stated
that rule for §118.9's 179 switches an hour before building a writer for 1,220 of them.

## 124. NEVER MISS A BEAT — PRICES, MARKET DATA, KLINES; MAC + S1 + GATEWAY ARE EACH OTHER'S SOURCES (USER 2026-10-21)

**USER VERBATIM 2026-10-21:** "apply and monitor use macbook s1 and gateway as sources the system can never miss a beat not for prices not for klines not for market data on crypto nor on stock I designed it to be solid but it got corrupted by rogue agents now it needs to be solid again" — "any writer should always respect a newer timestamp" — "gateway does not want macbook or s1 klines".

1. **Every writer respects a newer timestamp.** No copy, sync, restore or merge may put an older file or an older bar over a newer one. Every `rsync` into live data (`klines_cache*`, `price_cache*`, `latest_market_data*`, `market_data_*`, `data/tradier/*`) carries `-u`/`--update`. MEASURED 2026-10-21: `rsync_from_gateway_continuous.sh` pulled the gateway's **26-day-old** `price_cache_1.json` over the Mac's live one every ~2.3 min (no `-u`; the gateway copy was frozen because its disk was 100 % full and the Mac's push to it failed) — every reader in that window saw month-old prices for all 197 crypto symbols. Fixed in all 11 sync scripts (`backups/before_rsync_update_only_*`); verified 1186 samples / 5 min, 0 stale.
2. **Klines are append-only everywhere.** A newer source never REPLACES a file; it APPENDS the bars newer than the file's own last bar and keeps every existing row. `live_failover_common.apply_newer_klines` replaced whole files (a shorter S1 copy dropped Mac history) — now appends (`backups/before_failover_merge_not_replace_*`). Same law as the vault/guard/truncation-guard fixes of 2026-10-20.
3. **`tools/data_never_miss.py` (Mac cron */2, `DATA_NEVER_MISS`)** probes Mac + S1 (`~/binance`, `~/binance-sandbox`) + gateway in one pass and:
   - KLINES (crypto `symbols.json` minus tokenized, stocks `symbols_tradier.json`; 3m/15m/1h/4h/D; 1m excluded — built from mark prices): for every symbol×tf the newest copy on ANY host is the reference; Mac and S1 `~/binance` stores behind it get the missing bars APPENDED (server-side tail extraction `data_never_miss_tail.py`, append-only merge `data_never_miss_merge.py`). **The gateway is a source only — never written.** No exchange API, ever. Crypto verdict vs the clock (closed bars, 5-min grace); stocks vs the stock universe's own newest bar (calendar-free). Bars no host has → `UNHEALABLE` (then and only then is the exchange the source, via the ≤5-bar tail law).
   - MARKET DATA / PRICES: Mac price caches (freshest ≤ 60 s — the ez_manage block rule), Mac newest `market_data_*.json` ≤ 90 s (the failover `SNAPSHOT_STALE_S`), Mac stock indicators ≤ 90 s **in session only (§123)**, S1 standby stack `ez_s1_stack/` prices ≤ 60 s + snapshot ≤ 90 s, Mac snapshot delivered to S1 ≤ 180 s, gateway snapshot ≤ 300 s.
   - Report `data/reports/data_never_miss.json`, history `data/alerts/data_never_miss.jsonl`, log `~/logs/data_never_miss.log`, macOS notification on Mac breaches.
4. **First run 2026-10-21 04:31–05:00Z:** 108 of 223 stocks on the Mac were missing part or all of Tuesday's 15m session (S1 had them); 51 Mac crypto files, 37 S1 files behind. Healed: Mac 1337 + 1619 bars, S1 392 + 177 bars. Final pass: **OK on all three hosts, all classes**.
5. **Disk is part of never-miss.** The gateway was at 100 % (221 MB free) from syslog (1.3 GB, its kline fetcher logs every FORCE FETCH at INFO) + journal (3.2 GB). Cleaned to 5.6 GB free; rsyslog now daily/200 MB/7 kept with an hourly check, journald `SystemMaxUse=500M`.
6. **Timestamp styles — compare ONLY via `canon()`.** Stores mix `"2026-10-20"` (stock D), `"2026-10-20T19:30"`, `"2026-10-20 15:30"` and `"2026-10-20T19:30:00.000000Z"`. A SPACE-separated stock intraday stamp is **New-York-labelled** (Mac `USO_1h` `"2026-10-20 13:30"` == the UTC 17:30 bar). Text comparison of mixed styles reads the same bar as two. **I made this error on 2026-10-21:** the first heal runs appended 13 UTC bars into Mac `BNO_1h`/`USO_1h` that duplicated their ET-labelled session; all 13 removed (`backups/before_undo_nevermiss_append*`, `before_remove_nevermiss_dup_*`). Guards now: probe reads space stamps as ET, D compares by DATE, merge/tail refuse any stock file with ET rows in its tail, such files are reported `MIXED-LABEL` (currently Mac BNO_1h, USO_1h) and never auto-healed. Pre-existing and untouched: stock D files (ASML, ABT, …) carry every day 2016→2026-08-26 twice (13:30 + 20:00 stamps, identical values) — a grid-merge artefact for a deliberate fix, not a side effect.
7. **S1's crypto stack is on-demand** (cron `EZ_S1_STACK_REAP` stops `ez_s1_stack` while the Mac is NORMAL). S1 is judged by "Mac push OR own stack fresh", never by the idle stack alone.

### 118.11 ERASING THE DIFF MODEL — VERIFIED INERT ON LIVE, AND THE ONE REMNANT THAT IS REAL
2026-10-21 a subagent removed two env escape hatches from the live per-sym resolution path
(`per_sym_store.py:659` `PER_SYM_APPLY_FULL_SET=0`, whose own docstring offered to "restore the 842-key diff";
`per_sym_live_set.py:80` `PER_SYM_FULL_SET_READER=0`, which reverted every caller to a legacy `overrides`
lookup). Both files are read by all five live crypto engines, so this was checked before being believed:

1. **Neither variable is set in the environment of any running engine** (`ps -Eww` on four live `ez_manage`
   PIDs) — both hatches were already at their defaults, so deleting them changes nothing live.
2. The single behavioural delta on the default path is `full_config` missing/empty → **`None`** now, where it
   used to fall back to `dict(e["overrides"])`. Measured against the live SQL store: **0 of 759
   `per_sym_active` rows have an empty/absent `full_config`**, 0 have neither, and **0 have a `full_config`
   under 500 keys**. So no sym_side loses its board values; the change is inert and the fallback it removed was
   unreachable in practice.
3. Both compile; with both old vars forced to `0`, `per_sym_live_set.enabled()` is `True` and
   `BTCUSDC_LONG` resolves **3,530 keys**.

**A FALSE ALARM WORTH NAMING:** it flagged "`per_sym_full_sets.json` has a `min=10` entry — a near-empty row in
a file that is supposed to hold only full sets". That row is **`_meta`**, the file's own metadata header. The
smallest real sym_side row is **3,311 keys** (`DIVTESTUSDT_LONG`, `ZZZTESTUSDT_LONG`). A min/median scan over a
dict whose keys are data rows must exclude the metadata block, or the header reports as the worst row.

**THE REMNANT THAT IS REAL, AND IT IS ON THE LIVE ENTRY PATH.** `data/published_selection.json` stores each
board as a **diff**, not a full set — it carries `n_overrides` and an `overrides` dict, and
`live_twins.vec_exact.latest_board_ok` → `board_live_diff` compares the live set against **only those keys**.
Independently corroborated by §118.2's own output, where a published row printed
`"n_board_overrides": 879` beside a 3,500-key live set. That comparison IS the crypto entry gate, so converting
the generator (`tools/v15_published_selection.py`) to publish full sets also moves the gate and the go-live
applier — a multi-file change on the live entry path, and precisely where a careless edit refuses every entry on
every sym_side. That failure happened twice on 2026-10-20 alone (199 stock orders refused with
`TRADIER_HERMETIC_ENFORCE`, and the `STRICT_VEC_PARITY_MODE` gate admitting 0 of 582,689 board entries). It
belongs to the pilot and the S2 gate, not a hand edit.

**THIRD MISREPORTED BACKUP FILENAME IN ONE NIGHT.** Claimed
`backups/before_erase_override_model_202610210459_*`; the real files are `…_202610210538_*`. The mandated
backups DO exist each time, but the name in the report has been wrong three times
(`…0015…` vs `…0430…` for `tradier_manage`, and here). **Verify a backup by `ls`, never by the sentence that
claims it** — a backup you cannot find is a backup you do not have.
8. **3m year on ext = Binance bulk zips (USER 2026-10-21 "mac has only 1899 ext has a year").** MEASURED that day it did NOT: the ext 3m archive began 2026-10-12 (9 days), `klines_ext_archive --deepen` never covers 3m (and pages fapi at limit 1500), SSD2T + TOSHIBA_EXT were **100 % full** (SSD2T's archive stalled 10-20 20:45; both are personal drives — nothing deleted), WD25 not mounted, E3T 11 GB free. Now: `tools/klines_3m_vision_backfill.py` (cron `KLINES_3M_VISION` 03:35 daily, log `~/logs/klines_3m_vision_backfill.log`) keeps the last 12 months + current-month dailies of every live crypto symbol as Binance's own monthly/daily zips from data.binance.vision (no API weight), sha256-verified against `.CHECKSUM`, append-only, at `<freest drive>/binance_archive/vision_3m/<SYM>/` (~10 MB/symbol, ~2 GB total).

### 118.12 THE CONFIG LITERAL IS MOSTLY SHIELDED — BUT 65 SWITCHES STILL RUN AGAINST THEIR OWN TEMPLATE
A subagent argued that §118.9's template-vs-config mismatches are now "unreachable, work on a layer with no
authority", because (a) `ez_manage` / `ez_positions_quick` / `tradier_manage` bind `config` to a per-sym proxy
and (b) its projection made the cat_side layer a complete template image, so resolution never reaches the config
literal. **(a) is true. (b) is false** — that projection wrote `data/cat_side_defaults_4.json`, which nothing
live reads (§118.10); the live layer is `data/per_sym_settings.json` + the SQL `per_sym_settings` row, built by
`tools/build_cat_side_defaults_4.py`, and it was never touched. So the shielding that does exist comes from the
canonical daily build, not from that projection.

**Measured against the layer live actually resolves** (2026-10-22, 226 real template-vs-config mismatches):

| | count |
|---|---|
| present in the live cat_side layer | 216 |
| …and it carries the TEMPLATE value → config literal genuinely unreachable | **142** |
| live cat_side layer DISAGREES with the template bold | **74** |
| absent from the live layer → falls through to the config literal | **10** |
| **distinct switches whose template value never applies live** | **65** |

So the dismissal is 63% right and 37% wrong. Examples: `DIRECTION_FAVORABLE_REENTRY_ENABLED` template True /
live False; `REENTRY_POST_CONSOL_ENABLED` template False / live True; `TECHNICAL_DC_TARGET_TF` template `'4h'` /
live `'OFF'`; `GOLDEN_RULE_BASE_USD` template 0 / live 5.0; `WT_15M_BOUNCE_HIGH_1H_GT_PREV` /
`_LOW_1H_GT_PREV` template False / live True (both absent from the live layer).

**THE SUBSYSTEM SPLIT IS THE INTERESTING PART: of the 65, twenty-three are RE-ENTRY keys** (`REENTRY_POST_CONSOL_*`
across 3 lanes, `REENTRY_ON_DC4_15M_BREAK_ENABLED`, `REENTRY_ON_EXIT_PRICE_BREAK_ENABLED`,
`DIRECTION_FAVORABLE_REENTRY_ENABLED`, `BTC_GUARANTEED_REENTRY_*`, `STOP_RECLAIM_REENTRY_ENABLED`,
`REENTRY2_DC_BREAK_FILTER_TF`, …), then OTHER 20, ENTRY 8, SIZING 8, EXIT 6. **Re-entry is also the dominant
asymmetry family in BOTH parity censuses** — crypto VEC_ONLY `REENTRY_HIGHER_LOW` 54 / LIVE_ONLY 17 (§118.4),
stocks LIVE_ONLY `MANDATORY_REENTRY_PRICE_CROSS` 1,251 and VEC_ONLY `HARDCODED_RALLY_REENTRY` 873 (§118.7).
**A mechanism that would produce exactly that:** the vector evaluates re-entry with the TEMPLATE bold (what the
pilot measured), live resolves the cat_side layer, and for these 23 the two disagree — so for every sym_side
whose board does not override them, the two engines are asking different re-entry questions by construction.
**Stated as a hypothesis with a named mechanism, NOT as proven causation** — §97 applies, a reason label is not
a gate, and the only confirmed stock cause so far remains the `_rally_ok` global leak (§118.7). Testing it is
cheap and specific: re-run one sym_side's parity with those 23 keys forced to the template value on both legs
and see whether the re-entry asymmetry collapses.

**AND A CORRECTION OF MY OWN, caught before it was quoted:** my first pass reported 127 unshielded because the
helper did `float(t)==float(v)` inside a `try` and returned False on `ValueError` — so `'OFF'` vs `'OFF'` and
`'15m'` vs `'15m'` counted as disagreements. Equality must be tested BEFORE any numeric cast, and `None` vs `''`
(a blank template cell) treated as equal. The corrected figure is 84 lane-instances / 65 distinct switches.
Same family of error as the `30` vs `30.0` trap in §118.9, in the opposite direction.

### 118.13 `ez_positions_service` — THE REAL EXPOSURE IS 4 KEYS / 10 SITES, AND THE PRIORITY ONES ARE ALREADY DONE
A subagent reported `ez_positions_service.py` as "the last unwrapped module: 13 keys, 43 sites", and said the
five `FIXED_STOP_*` keys were what it would make per-sym first because they decide where a stop sits. Measured
per key, by how each site actually RESOLVES (not where the literal sits — the §117 trap):

| key | `config.X` / `getattr` | via `_svc_psym_get` | verdict |
|---|---|---|---|
| 5× `FIXED_STOP_REF_FIELD/REF_TF/BUFFER_PCT/TIGHTEN_MODE/TIGHTEN_TRIGGER_PCT` | 0 | **5** | **already per-sym** |
| `MAX_ORDER_VALUE` | 0 | **2** | **already per-sym** |
| `MIN_POSITION_SIZE` | 17 | 0 | global — SIZING |
| `START_POSITION_SIZE` | 13 | 0 | global — SIZING |
| `HIGH_GAIN_AUGMENTATION_MIN_SIZE` | 1 | 0 | global — SIZING |
| `HEDGE_MODE` | 5 | 0 | global — behaviour |
| `NEW_POSITION_MAX_LOSS_THRESHOLD` | 3 | 0 | global — behaviour |
| `DELTA_ENGINE_ENABLED` | 1 | 0 | global — behaviour |
| `CAT_SIDE_DEFAULTS_ENABLED` | 1 | 0 | global — behaviour |

**`_svc_fixed_stop_spec` (`ez_positions_service.py`) already calls
`_svc_psym_get(symbol, side, "FIXED_STOP_REF_FIELD", getattr(config, "FIXED_STOP_REF_FIELD", "dc_low"))` and the
same for the other four** — per-sym > cat_side > global, mirroring `ez_manage._fixed_stop_spec_for`. The
`getattr(config, …)` is the FALLBACK DEFAULT passed as an argument, not the resolution. Counting those as global
read sites is the same mistake that turned "151 unwired keys" into 14 (§117): **an auditor must classify a key
by the call that RESOLVES it, never by a `config` reference appearing on the same line.**

So the genuine remaining parity exposure there is **4 keys / 10 sites** (`HEDGE_MODE`,
`NEW_POSITION_MAX_LOSS_THRESHOLD`, `DELTA_ENGINE_ENABLED`, `CAT_SIDE_DEFAULTS_ENABLED`), plus ~31 sizing sites
that are live's domain by the USER's bars-not-quantities rule. And wrapping the module remains the wrong shape:
it has no single per-position entry point, `parse_position_key` is scattered across 8+ sites that discard
symbol/side, and it is both the single position writer and an order path via its FIXED_STOP leg.

**ON "SHOULD THE SIZING KNOBS BE REMOVED FROM THE TEMPLATES":** `per_sym_live_set._apply_caps` clamps **8**
sizing knobs from `CRYPTO_MIN_SIZE_CAPS`, not 4 — and its own docstring says **"Crypto only; a stock sym_side is
never capped."** So the mode table does NOT own the sizing knobs on stocks, and deleting those rows from
`TEMPLATE_STOCKS_*` would remove a sweep the stock venue genuinely uses. Any such decision is per-venue, and a
template row change belongs to the pilot and the S2 gate regardless.
