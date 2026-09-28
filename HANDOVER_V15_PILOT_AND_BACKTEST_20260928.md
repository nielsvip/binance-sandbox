# 🧭 HANDOVER — v15_pilot + Backtest System — FULL BRIEFING FOR THE NEXT AGENT
**Date:** 2026-09-28 · **Author:** prior session (Claude) · **Audience:** the agent taking over the backtest/pilot work
**Status of this doc:** authoritative for what was changed this session; folds in `BACKTEST_BIBLE.md` (2026-09-26). Where this doc and older text conflict, **the NO-LIES mandate and `BACKTEST_BIBLE.md` win**, and this doc records the deltas since the bible.

---

## 0. READ THIS FIRST — THE TWO RULES THAT OVERRIDE EVERYTHING

1. **NO LIES. Every metric on any human-facing surface must be REAL** (from the trade-return ledger, forward and backward). Lying Sharpe/gain/DD numbers wiped out half the user's net worth in 4 months. Never fabricate a delta, never inject synthetic trades, never annualize (`sqrt(252)`/`sqrt(N)` are BANNED), never emit a bare "Sharpe" (always `pool_sharpe` / `sym_sharpe` / `sharpe_per_trade`). If a switch genuinely produces 0 delta, **report 0** — do not manufacture a number.
2. **NEVER REVERT / ROLLBACK live code.** Find a better version in `backups/` (on Mac AND S1) before ever touching git — git is unreliable, check dates. Diff and patch the current file; never overwrite a newer file with an older one. Locked files (`LOCKED_FILES.md`) may not be edited without an explicit "unlock <file>" in the user's message.

**Machines (CHANGED since the bible — critical):** Only **two** servers exist now: `ssh s1` (crypto backtests) and `ssh s2` (stocks backtests). **S4 and S5 are GONE** ("the rest is gone" — user, 2026-09-28). The bible's S4/S5 references are stale. **Mac has NO usable NPZ and never backtests** — its NPZ are truncated (`File is not a zip file` / `0 trades DATA_ERROR`); Mac is for editing code + live trading + dashboards only.

- `ssh s1` → python `~/miniforge3/bin/python` (3.12), 16 cores, 601 NPZ in `~/binance-sandbox/backtest_v8/indicators/`.
- `ssh s2` → python `~/binance-sandbox/.venv/bin/python` (3.10), 16 cores, 601 NPZ. **s2 goes via ProxyJump 10.0.0.4** — rsync to it needs `-e "ssh -S none -o StrictHostKeyChecking=accept-new"` or you get `Host key verification failed`.
- **"64/128/256 workers" is aspirational** — both boxes have **16 physical cores**. High thread counts only help because NumPy releases the GIL; the historical deadlock (`ThreadPool>28` on parallel `V.load_npz`) is about *parallel NPZ loads*, not per-cell vec evals on one already-loaded NPZ. One `sym_side` at a time with 64–128 internal workers has been stable.

---

## 0.5 🟨 THE CANONICAL SHEET-FILL RULES — VERBATIM FROM THE USER — OBEY EXACTLY

> The user has repeated these rules **thousands of times and they keep getting ignored.** They are the acceptance contract for `v15_pilot.py`. Read them, re-read them, and implement them EXACTLY. If your code does anything different, your code is wrong. Reproduced here verbatim (lightly formatted, wording preserved):

**Reading the sheet**
- Read by **row 2 (column headers), NOT by coordinates** — match by *switch name × column name*, because columns may be added and coordinates then break.

**The `Switch` column & bold/`override` column**
- Default values for the initial baseline calculation are in **BOLD** in the `default` column.
- If the sym_side being calculated has had **previous calculations**, `v15_pilot.py` MUST find those in `/SPREADSHEETS/` and apply those settings, putting **every non-default setting in BOLD in the `override` column**, and **NEVER changing the bold/regular of the `default` column.**
  - The `default` column's bold is changed **only by maintenance** when `V15_AVG_DELTAS.xls` recalculates the `AVG_DELTA` column, after which the row order changes (`worst_first`) **while keeping the entire column's content together** (the yellow cells for a row always stay with the same switch name when row order changes).
- **ORANGE (FILTER) fields** (below SWITCH in the same column) can **NEVER be above white (SWITCH) rows.**

**Step 0 — the FIRST thing on a sym_side**
- FIRST: **FIND the best default and override settings from previous tests in `/SPREADSHEETS`** and put them **BOLD in the `override` column.**
- THEN calculate the combination of **ALL these switches in `E3`** (this is the baseline: the v12_quick_engine value for all defaults + the previous-best overrides for that sym_side). The **first value calculated is the first switch name (which is ALWAYS a bold default).**
- This must be **SUPER FAST** — that is why the NPZ for the sym stays **in RAM until all values are calculated.** Never wait years.

**Yellow cells (per switch, per row)**
- The first (and every) row probably has **yellow cells in the `O`–`IO` columns** whose header is a **filter name.**
- If a cell is yellow, the filter in that column name **must be tested for THAT SWITCH AND ONLY THAT SWITCH** — never applied to any other override or default setting.
- The **delta of that calculation is ALWAYS written in the yellow cell** (pos, neg, or zero).
- If that yellow delta is **positive**: write the **filter (column header) name** into the `override` column **AND** the `PER_ROW_FILTERS` column (**ADD the header name — never overwrite existing content**), and **add its delta to `VECTOR_DELTA`** (sum if there is already a value there).
- When **all yellow values in the row have been calculated and the positive ones summed into the delta, the row is complete.**

**Row → row / row → tab navigation (the greedy chain)**
- If `VECTOR_DELTA` is **POSITIVE**: **move DOWN 1 row** to the next switch name, **add the delta to the previous baseline number and write it in the `baseline` column of that next row**, and repeat.
- If `VECTOR_DELTA` is **None, zero, or NEGATIVE**: **DO NOT move down the tab.** Move to the **first pending row in the NEXT tab**, write the baseline value in the `baseline` column there, and repeat.
  - If it then gives a **positive** delta in the next tab → stay on that tab, next row, add delta to baseline.
  - If none/0/neg → move on to the next tab.
- **Every row in every tab needs a delta value (pos or neg).** All rows are filled in order. If a tab is complete it may be skipped in remaining rounds. **Only if `hustle mode` is on may rows be filled in random order.**

**The 6 clarifications (do not violate any)**
1. **`baseline` (E) only gets something written after a POSITIVE delta — otherwise it stays BLANK normally.**
2. **`LIVE_DELTA` and `LIVE_SHARPE` contain formulas that screw up the sheet fill — clear them at open.**
3. After calculating the initial baseline with overrides from the last round, **start calculating ALL the YELLOW BOXES in the first (default-setting) row.**
4. `VECTOR_DELTA` = **sum of the positive yellow deltas of the row**, added into the baseline on the next row **if** there is a positive delta; if no positive delta, move to the **first unfilled row in the next tab.**
5. **NEG delta** in row's `F`/VECTOR after adding all pos yellow deltas → **skip to next TAB.**
6. **POS delta** in row's VECTOR after adding all pos yellow deltas → **add delta to baseline in next row**, then calculate the next delta / next yellow-filter columns, etc., **UNTIL THE COMPLETE WORKBOOK IS FINISHED.**

**Other hard rules**
- **STDEV_SLOPE_SIZING** has only **one switch (T/F): use the 1–5× multiplier or not.** **There are 13 tabs to fill.** ⚠️ *NOTE: `BACKTEST_BIBLE.md` §4.3 currently lists STDEV in `SKIP_SHEETS` (12 active tabs). The USER'S CURRENT INSTRUCTION OVERRIDES THAT: STDEV is a single T/F switch tab and ALL 13 TABS ARE FILLED.* If STDEV's `compute_regime_sizing_mult` still stalls, fix it to a constant-time T/F mask — do not silently skip the tab.
- `LIVE_DELTA` / `LIVE_SHARPE` are filled **only when the entire sheet is complete**, by running the winning set of defaults+overrides through `backtest_v12_engine` in the actual trading script (slow but necessary to prove parity).
- If **default bold font is lost**, the `is_default` column holds a written backup of what must be made bold again — restore from it.
- Defaults change **only** when `V15_AVG_DELTAS` is recalculated and reapplied into the `AVG_DELTA` and `POS_SYM` columns.
- **Stall guard:** if a script gets stuck on a cell **>10 s**, mark **that cell AND the tab RED**, write the reason for the stall **in the red cell**, then continue to the **next yellow cell** — or, if there are no yellow cells left in the row, continue to the **next TAB, not the next ROW.**

**COMMON VIOLATIONS THAT KEEP HAPPENING — DO NOT REPEAT ANY:**
- ❌ Writing to hardcoded column numbers instead of resolving by row-2 header name.
- ❌ Leaving `E2` as the string `BASELINE` but then failing to write numeric `E3`, or writing `BASELINE`/`#NUM!`/`0` into data rows.
- ❌ Filling `E` (baseline) on rows that did NOT get a positive delta (E must stay BLANK unless promoted).
- ❌ `if k not in overrides` guard when ingesting previous-best (best MUST win over defaults; overwrite).
- ❌ Overwriting `override`/`PER_ROW_FILTERS` content instead of ADDING the filter header name.
- ❌ Applying a yellow filter to any switch other than the exact one on that row.
- ❌ Testing filters that are not yellow for the row (wastes CPU, lies about provenance).
- ❌ Moving DOWN a tab on a neg/zero delta (must jump to next tab); or moving to next tab on a positive delta when there was a testable row below.
- ❌ Leaving `F`/`G` as `VLOOKUP`/`IF` formulas or `None` (every row gets a real float).
- ❌ Filling `LIVE_DELTA`/`LIVE_SHARPE` per-row or leaving their template formulas in (clear at open, fill once at end).
- ❌ Reloading NPZ from disk per row (keep in RAM until the whole workbook is done).
- ❌ Abandoning a sym_side halfway (run to the last tab even if every delta is negative).
- ❌ Injecting synthetic/`arange`/hash/proxy deltas to avoid a 0 (a real 0 is a real 0).

---

## 1. WHAT THIS SESSION CHANGED (so you don't re-discover or undo it)

All edits are on Mac (`/Users/niels/Documents/binance`) and synced to s1+s2 `~/binance-sandbox/` with md5 verification. Backups are in `backups/before_*_20260928*.py`.

### 1.1 Removed fabrication from `v12_quick_engine.py` (NO-LIES purge)
The engine was riddled with scaffolding that manufactured non-zero deltas so every switch "looked wired." **All of the following were neutralized to early-return passthrough (bodies preserved for audit, matching the codebase's own `_apply_625_entry_gates` disable pattern):**

- `_batch1_template_wiring` — OR'd trivially-true `_cond` (`_c>0`/`_atr>0`) and `arange(n)%N==0` into masks.
- `_wire_07_exit_stops_tranche` — 44 "exit" switches all OR'd `close>0`/`arange%N` into `exit_mask`.
- `_apply_auto_wired_params` — hashed the config and flipped mask bits at `arange`-seeded indices.
- `_apply_universal_distinctness_fallback` — docstring claimed "DISABLED" but STILL RAN; flipped entry/exit bits at `(arange(n)*9973+h)%13==0`.
- **`_apply_new_audit_causal`** (found by the audit agent, ~4,700 lines) — applied synthetic proxies (`rsi_1h>55`, `adx_1h>15`, `bb 0.1–0.9`, `wt1>wt2`) to ~1,000 switches whose real meaning is unrelated. Called in `simulate_one` at line ~22025.
- **`_apply_batch2_entry_gates`** / **`_apply_batch2_exit_gates`** — same synthetic-proxy pattern (`wt_velocity_1h>0`, `atr>thr*0.8`).
- Also confirmed already-dead (return passthrough): `_apply_625_entry_gates`, `_apply_625_exit_gates`, `_apply_625_sizing_mult`, `_apply_625_generic_gates`. And `_apply_PZ_causal` / `_apply_batch2_augment_gates` are **not called** anywhere (dead).
- The BB and WT_DC entry masks previously OR'd `arange(n)%2`/`arange(n)%4` synthetic trades — **removed**; now real signal only.

**Consequence you must internalize:** with fabrication gone, **most entry/filter switches produce ~0 delta on a baseline that is ~always in-market (high TIM, few trades).** That is the TRUTH, not a bug. Only switches that change the *trade set* (exits like DC stop/target, WT-lower-cross; and entries evaluated against a lower-TIM baseline) move gain. Do not "fix" a 0 by re-introducing fabrication.

### 1.2 WT_DC entry rewritten to the slowdown/accel scorer (user-requested)
- Live `wt_dc_entry_scorer.score_entry()` used to just call the simple 5-component `score_entry_multitf` (25/25/30/10/10). The detailed `_score_long`/`_score_short` (lines 176–693, the slowdown/accel logic: `wt_bull/bear_alignment`, `wt_velocity_up/down_count`, `wt_structure_4h/D`, `wt_composite_bias`, `wt_wave_phase`, `wt_cross_bull/bear_*`, `wt_cross_value/rising/prev`, `wt_momentum_state`, `wt_divergence`, DCBB, volume, EMA/HA context; 6 categories × `CATEGORY_WEIGHTS` htf .75/ltf 1.25/mom .75/dcbb 2.25/vol .5/ctx .75; threshold 43) was **dead fallback.**
- Added an **opt-in** `detailed=` param to `score_entry` (default False → **live UNCHANGED / safe**).
- Vectorized `_score_long`/`_score_short` faithfully as `wt_dc_entry_scorer_vec.score_entry_detailed_vec` — **parity 0.000000 vs scalar** over 3000 bars both sides (`test_wt_dc_detailed_scorer_vec.py`, 2 passed).
- Wired into `v12_quick_engine.compute_entry_signals` behind switch **`WT_DC_DETAILED_SCORER_ENABLED`** (default off; threshold 43 when on). Produces real deltas where it fires distinct entries (verified LONG Δ−5.19, +12 trades on 1000000MOGUSDT).
- NPZ has 33/34 required fields (only `wt_cross_bull_5m` missing → defaults 0, same as scalar).

### 1.3 BB squeeze — `alignment` wired
- Live BB squeeze breakout (`ez_positions_quick.detect_bb_squeeze_breakout` + gate `alignment>=10` at `ez_positions_quick.py:16141-16155`) is a **dead gate in live** because the bare `alignment` key is never populated anywhere in the codebase.
- Faithful vectorized detector + gate added to the (unlocked) shared module `vec_decisions/check_entry_candidates_crypto__bb_squeeze_gate.py`: `detect_bb_squeeze_breakout_vec` (stateful rolling-percentile port), `compute_alignment_vec` (user-approved wiring: 0–24 side-aware from real NPZ ema/stoch/vol/sma200/wt fields), `bb_squeeze_entry_mask_vec` (detector AND gate), TF-parametric via `BB_SQUEEZE_ENTRY_TF`.
- **Open item:** to make BB actually fire in LIVE (not just backtest), `alignment` must also be wired into `ez_indicators.py` / `tradier_indicators.py` — that is a NEW-STRATEGY change (needs sweep proof + paper days before flipping live).

### 1.4 Old fast sweep driver
- `tools/dc_simple_8_sweep.py` — `run_one(sym_side, per_sym_map, 30)` computes the 102-variant ledger (64 DC exit×entry + 8 exit + 8 entry + 4 WT-lower-cross + 4 EMA + 4 BB + 4 WT_DC + 6 AF) via the (now honest) engine. Edited this session: BB variant sets `BB_SQUEEZE_ENTRY_TF`; WT_DC variant sets `WT_DC_DETAILED_SCORER_ENABLED=True` + `WT_DC_TF_ENTRY`. TF lists are **15m/1h/4h only — 3m/5m/1m are excluded** (per user; those are tested forward in live).
- `tools/dc64_proof_sheets.py` (NEW this session) — wraps `run_one` to write the classic `{SYM}_{SIDE}_bh{..}_gain{..}_delta{..}_30d_matrix.xlsx` ledger + a `REAL_ZOOMABLE.html` Chart.js trade chart (CLOSE + entry/win/loss markers, wheel/drag zoom). `--sym X_LONG` or `--all --venue crypto|stocks --workers N`.

**KNOWN LIMITATION of the old sweep (why the user rejected it):** it tests each switch as an **independent variant vs the same baseline**. The baseline uses per_sym BEST, which for many symbols holds the position ~forever (2–7 trades, ~98% TIM). So entry/filter switches show 0 and the chart shows 2 trades = "crap." The **v15 greedy method is the correct system** (below) because it chains exits first, lowering TIM so later switches have room to matter, and enforces a 30-trade floor.

---

## 2. NO-LIES MANDATE (verbatim intent — obey it)

1. Every script emitting Sharpe/gain/DD to a human surface MUST route through `metrics_guard.validate_and_format_sharpe()`. `metrics_guard.write_sharpe_row()` is the ONLY sanctioned writer; it refuses violations.
2. Every CSV in `data/sweep_results/` or `data/autonomous/` MUST include `pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr, trades, max_dd_pct, n_syms, years`.
3. No annualization. BANNED tokens: `sharpe_annual, sharpe_y, sharpe_yearly, sharpe_w, pool_sharpe_proxy, sharpe_rough`.
4. No bare "Sharpe" — always qualified.
5. Sample floor: ≥48 crypto OR ≥100 stocks × >1yr × ≥30 trades/sym; below → `[DIAGNOSTIC ONLY]`, never for promotion.
6. Source of truth = trade-return list. Sharpe without per-trade returns = `[UNVERIFIED]`.
7. **New corollary from this session:** a switch that does not move the trade set produces an honest 0. NEVER inject `arange`/hash/proxy trades to make it non-zero. If the user expects non-zero, the fix is a *lower-TIM baseline* or a *real signal*, not fabrication.

---

## 3. INFRASTRUCTURE — CURRENT REALITY (supersedes bible §1/§11)

| Machine | Address | Python | Role | NPZ |
|---|---|---|---|---|
| **Mac** | local | `/opt/anaconda3/envs/binance_env/bin/python` | edit code, live trading, dashboards. **NEVER backtests.** | truncated — unusable |
| **s1** | `ssh s1` (gateway ProxyJump / `s1-int 127.0.0.1:2201`) | `~/miniforge3/bin/python` (3.12) | **crypto backtests** | 601 in `~/binance-sandbox/backtest_v8/indicators/` |
| **s2** | `ssh s2` (ProxyJump 10.0.0.4) | `~/binance-sandbox/.venv/bin/python` (3.10) | **stocks backtests** | 601 |

- **S4/S5 no longer exist.** Do not try to provision or sync to them.
- **Disk warning (s1):** root was 100% full this session. The hog is `~/binance-git-backup` (**145 GB** — autosave git history bloated by committed data files). Clearing sandbox caches freed a few GB. If runs fail with `No space left on device`, this is why; do NOT delete the 145 GB backup without the user's OK.
- **Code sync pattern:** edit on Mac → `rsync -az -e "ssh -S none -o BatchMode=yes -o StrictHostKeyChecking=accept-new" <file> s1:~/binance-sandbox/<file>` → `md5sum` verify on both. Sync to both `~/binance-sandbox/` (herds run here) and `~/binance/` if present. **Never `push.py`** (it halts live for cutover).
- **Detached runs:** `ssh` sessions drop mid-command. Launch server jobs with `setsid nohup <py> -u <script> ... > /tmp/log 2>&1 < /dev/null & disown`. A plain `nohup ... &` inside a backgrounded ssh has been killed on session teardown — use `setsid` + `disown` and then verify the log grows.
- **Waiting:** don't hold one ssh open to block on a run — it dies. Poll with fresh short ssh connections, or watch for the progress JSON / process exit.

---

## 4. DATA — WHAT THE PILOT READS (bible §2)

| Source | Path | Content | Window |
|---|---|---|---|
| NPZ indicators | `backtest_v8/indicators/{SYM}.npz` | per-bar arrays: `open/high/low/close/volume`, `dc_position_*`, `dc_low/high_*`, `wt1/wt2_*`, `wt_*` (alignment/velocity/structure/cross/divergence), `atr_*`, `sma_200_*`, `ema_*`, `bb_*`, `stoch_*`, `mfi_*`, `relative_volume_*`, `squeeze_*`, `stdev_edge/slope_*`, `timestamps` (unix ms) | slice **30 days** (`timestamps[-1]-30d` crypto ≈ 14,400 3m bars; **20 RTH sessions** stocks ≈ 2,881–3,233 5m bars) |
| TEMPLATE workbook | `SPREADSHEETS/TEMPLATE{CRYPTO,STOCKS}_{LONG,SHORT}.xlsx` | 13 SWITCH_SHEETS + `*_BASELINE_METRICS` + `FILTER_DICTIONARY_V2` + `FILTERS_EXPLAINED` + `LEGEND_FILTERS` | cloned per sym_side |
| Previous best | `SPREADSHEETS/` + `V15_V16_CELL_BY_CELL/` + `data/reports/lifecycle_pilot/*_v14_progress.json` (`cumulative_overrides`/`hustler_overrides`) + `hustler_best.json` | best `switch=cand` + `filter=opt` per exact SYM_SIDE | ingested BEFORE baseline; best wins — never `if k not in overrides` |
| Defaults | `config.py` (crypto) / `config_tradier.py` (stocks) | QuickConfig fields | `sanitize_overrides()` |
| per_sym live config | `data/hourly_reconfig/per_sym_active_config.json` (253 crypto) + `..._stocks.json` (248) | live per-symbol overrides (the "BEST") | loaded by `dc_simple_8_sweep.load_per_sym_maps()` |

**RAM discipline:** `ALL_PREPARED` + `V12_NPZ_CACHE=32` + `preload_prepared()` keep the 30-day slice in RAM for the whole workbook. **Per-row disk reload is forbidden** (turns 0.07 s/cell into >1 s/cell). `3m/5m/1m` TFs are NOT in the NPZ and must never be run in a backtest — they are validated forward in live.

---

## 5. ENGINES — TWO, NOT INTERCHANGEABLE (bible §3)

| Engine | s/eval | Role |
|---|---|---|
| `v12_quick_engine.py` (`QuickConfig`, ~22.9k lines) | ~0.07 s vectorized | **Vector sweep.** `V.simulate_one(npz,sym,is_long,cfg)` + `prepare_batch`/`evaluate_prepared_sanitized` on hot `ALL_PREPARED`. What the pilot calls for every yellow cell + `VECTOR_DELTA`. Entry logic in `compute_entry_signals` (line ~9027); real switch families wired there. |
| `backtest_v12_engine.py` (~30 s) | ~30 s scalar | **Live-faithful verifier.** Calls real `ez_manage.process_position` / `tradier_manage.process_position` bar-by-bar, guarded by `_assert_live_path` (do NOT defeat the no-vectorization guard). Pilot calls it ONCE per workbook (winning set) to fill LIVE_DELTA/LIVE_SHARPE and prove parity. |

**Parity gate:** same frozen 30-day NPZ; trade ratio 0.80–1.25, gain mismatch <0.5 pp AND <15%. Per-row parity fail → mark red + log `flags.md`, never abort the sheet.

**Genuinely-wired, live-faithful switches in the vector engine (audit-verified this session — trust these):** the DC family (`ENTRY_DC_TF`, `TECHNICAL_DC_STOP/TARGET_TF/_BUFFER_PCT` at ~22149–22179, `DAYTRADE_DC_*` at ~22108–22147 building `dc_low/high_{tf}` from NPZ), `WT_LOWER_CROSS_EXIT_TF` (~22421), `WT_DC_ENABLED`/`_DETAILED_SCORER_ENABLED`/`_TF_ENTRY`/`_DC_TF` (~9434–9447), KINDERGARTEN/EMA_9_21 cross block (~9574–9644), `WT_15M_BOUNCE_OPEN_ENABLED` (~9697, ~22075), `BB_SQUEEZE_ENTRY_ENABLED` (~8747, faithful port), `STDEV_SLOPE_SIZING_ENABLED` (~10333), `DC_DAYTRADE_ENABLED`/`TRADIER_DC_DAYTRADE_ENABLED` (~22081). These are exactly the switches `dc_simple_8_sweep` sweeps, so the old sweep measures real signal.

---

## 6. THE v15 TEMPLATE SYSTEM — HOW IT SHOULD WORK (bible §4–§9, expanded)

This is the correct, modern system. `v15_pilot.py` is the **ONLY writer** of `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_{SIDE}_30d_matrix.xlsx`. It runs **one sym_side per invocation**, sequentially over 12 active tabs (STDEV skipped), greedy, worst_first.

### 6.1 The workbook (per template, selected by `get_template_for_symside()`)
- **13 SWITCH_SHEETS** in fixed order: `STDEV_SLOPE_SIZING, ENTRY_REVERSAL_BOUNCE, ENTRY_BREAKOUT_CHANNEL, ENTRY_CONFIRMATION_GATES, EXIT_STRUCTURAL, EXIT_VELOCITY, REENTRY_WINDOWED, REENTRY_ADAPTIVE, AUGMENT_TREND, AUGMENT_RISK_SIZING, REDUCE_PROFIT_LOCK, REDUCE_SIGNAL_RATER, GLOBAL_RISK_GATES`. **`STDEV_SLOPE_SIZING` is in `SKIP_SHEETS`** (half-finished `compute_regime_sizing_mult`, stalls) → 12 active tabs, ~3,800 rows.
- `TEMPLATE_BASELINE_METRICS` (renamed `{SYM}_{SIDE}_BASELINE_METRICS` on clone).
- `FILTER_DICTIONARY_V2` — `Filter | Option Value | Sheets applicable | Switches exactly (gates) | Recommendation (SPECIFIC/GENERAL)` — source of yellow-cell eligibility.
- `LEGEND_FILTERS` / `FILTERS_EXPLAINED` / `INSTRUCTIONS_V2` — human docs.

### 6.2 Column contract — resolve by ROW-2 HEADER NAME, never by coordinate
| Col | Header | Meaning | Pilot writes |
|---|---|---|---|
| A | `Switch` | switch name | never |
| B | `default` | default (bold = default) | never (only `V15_AVG_DELTAS` maintenance) |
| C | `override` | override for this row | `switch=cand [+ positive yellow headers]`, bold if non-default, **only when VECTOR_DELTA>0**; ADD names, never overwrite |
| D | `Family` | SPECIFIC vs GENERAL | never (GENERAL = orange per-sheet rollup) |
| E | `BASELINE` | cumulative baseline before this row | numeric or blank; **E2 stays header string `BASELINE`; E3 = first `baseline_gain`; other E blank until a POS delta promotes the next row/tab** |
| F | `HUSTLE_DELTA` | `vec_gain - baseline_gain` | float every row |
| G | `VECTOR_DELTA` | `sum(positive yellow deltas)` vs `cumulative_before` | float every row, green `006100` pos / red `FFC7CE` neg, **never None** |
| H | `LIVE_DELTA` | `live_gain - cumulative_before` | **BLANK until workbook complete** (clear template formulas at open) |
| I | `LIVE_SHARPE` | live pool_sharpe delta | **BLANK until complete** |
| K | `PER_ROW_FILTERS` | comma-joined positive yellow headers | per row |
| L | `is_default` backup | `YES` if B should be bold | never |
| M:N | `AVG DELTA`/`POS_SYM` | maintenance only | never by pilot |
| O:BI | yellow headers `FILTER=OPT` | per-yellow delta vs `cumulative_before` | float per yellow cell, opportune-only |

Visual: Arial 10 left, row height 15, width `max_len+2` cap 30 via `_auto_adjust_all_sheets` before every `_atomic_save`. `False/True` not `FALSE/TRUE`. Only column C may carry non-default bold.

### 6.3 The fill contract (the exact algorithm)
**Step 0 — clone + ingest best + baseline (per sym_side):**
1. Find best overrides for the exact sym_side (progress JSON `cumulative_overrides`/`hustler_overrides`, `hustler_best.json`, prior XLS). **Best wins over defaults — never `if k not in overrides`.** Write every non-default **bold in column C** before any calc.
2. Clone `TEMPLATE...xlsx` → `{SYM}_{SIDE}_30d_matrix.xlsx` via `_atomic_save` (validate `ZipFile>=10` entries before `os.replace` — prevents 225 KB BadZip truncation). Rename baseline sheet, fix VLOOKUP, clear `#NUM!/#NAME?/0` in E.
3. `BEST-C-FILL`: for every sheet row 3..max, if `A=Switch` in overrides, set `C=override`.
4. Baseline: `evaluate_prepared_sanitized(prepared, overrides, 30)` → `baseline_gain/bh/trades/pool_sharpe` → `{SYM}_BASELINE_METRICS!B2` and first pending `E3` (E2 header preserved). Zero-trades STILL writes the XLS then skips sweep — **early return before clone is FORBIDDEN.**

**Step 1 — per-row yellow evaluation (each tab sequentially, each row in order):**
- **Opportune yellows** for THAT switch only: a yellow cell `FILTER=OPT` exists iff `Recommendation=SPECIFIC`, `Sheets applicable` matches the tab lifecycle or `ALL`, `Switches exactly (gates)` token-overlaps the switch (≥2 strong tokens or exact containment; generic `FILTER` token alone is not enough), and the `FILTER=OPT` header exists in `O:BI`. **Test only that switch's yellows — never random filters.**
- **Candidates:** `[naked]` (switch=cand alone) `+ each yellow` (switch=cand + that one filter) vs `cumulative_before`. Each via `v12_quick_engine` (ThreadPool workers, 0.07 s, RAM). Write each yellow delta into its `L:BI` cell BEFORE the next row.
- **`VECTOR_DELTA` (G) = sum of POSITIVE yellow deltas** (`>1e-9`). Naked delta used only when the row has no yellows. Negative/zero yellows written but not summed.

**Step 2 — baseline chaining / tab navigation:**
| Condition | Action |
|---|---|
| `G > 1e-9` (winner) | write C (switch+pos yellows), F/G/K + yellow cells; **move DOWN 1 row, SAME tab**; `E_next = E + G`; advance `cumulative_gain`/`cumulative_overrides`. |
| `G` None/0/negative (loser) | write F/G/K + yellow deltas (G red), leave C blank; **go to first pending row of NEXT tab**, write `E = cumulative_before`. |
| row has NO yellows | eval naked vs cum, write F/G, then **always go to NEXT tab** (nothing to exploit). |

Invariant: `E` only written after a positive delta (else blank). `_validate_e_chain_and_yellows` asserts `new_cum >= old_cum` and `delta == vg - cum`. **Every row in every tab gets a delta (pos or neg). Never abandon a sym_side halfway — run to the last sheet even if all 12 are NEG.**

**Step 3 — LIVE verification (deferred, once):** when the 12-tab workbook is complete, run the winning set (`cumulative_overrides`) through `backtest_v12_engine` (30 s timeout) and fill every processed row's H/I. Falls back to vector on timeout.

**Charts / 365D / parity / sync:** per-complete `write_zoomable_chart(..., suffix='30D_REAL_ZOOMABLE')` + `30D_BIGGEST_DELTA`; 365D rerun via `prepare_batch(sym,365)` (`365D delta < 50% of 30D` warns overfit); parity per pos delta; S1 canonical, mirror to Mac via `sync_s1_to_mac.sh`.

**Stall guard:** any yellow cell eval >10 s (`YELLOW_TIMEOUT=10`) → mark that cell RED (`FF0000`), mark tab RED, log `data/reports/v15_flags/{SYM}_{SIDE}_flags.md`, continue to next yellow (or next tab if none). Never hang the workbook; per-sym ≤60 m, target ≤20 m.

### 6.4 WHY IT'S BEEN STUCK (bible §6 — the bugs to fix)
The pilot currently produces, for every sym_side with `done>0`: **`E2=BASELINE` string, C empty, F/G/H/L:BI None, sheets showing `BASELINE #NUM! 0`** — not a single tab completes. Root causes (each must be fixed):
1. `0-trades` early return before `clone_template` → no XLS. (Fix: always clone+write, then skip sweep.)
2. `if k not in overrides` guard → C empty. (Fix: best wins, no guard.)
3. XLS prev-parser only handled `C="K=V + …"` with `F>0`, missed single-value `C` (`False`). (Fix: parse single-value overrides.)
4. `_atomic_save` no zip-validate → 225 KB BadZip. (Fix: validate `ZipFile>=10`.)
5. Column-C coordinate write vs header lookup; `O:BI` `"="` header detection missed after column inserts → every row took the no-yellow path. (Fix: resolve all columns by row-2 header.)
6. `LIVE_DELTA`/`LIVE_SHARPE` formulas left in template broke `_hdr_col_map` and made E2 look numeric. (Fix: `_spec_clear_live_formulas` at open.)
7. `STDEV_SLOPE_SIZING` not skipped → 13-tab loop stall. (Fix: keep in `SKIP_SHEETS`.)

**Observed this session:** the pilot's *compute* actually works — a fresh AAVEUSDC_LONG run produced a complete, honest `*_v14_progress.json` (3,020 real cells with varied deltas, valid:True). But the **XLSX writer left E2="BASELINE" and F/G=None.** `tools/v15_refill_from_json.py --sym {SYM}_{SIDE}` refills the XLSX from the JSON truth (E2 numeric, cells filled) and validates the zip. **The JSON is the source of truth; the inline XLSX writer is the broken part to fix.** Until it's fixed, run pilot → refill.

---

## 7. THE AUDIT FINDINGS (switch ↔ engine ↔ config ↔ live) — ACT ON THESE

A full audit (435 distinct TEMPLATE switches; 54 deep-audited) found:

### 7.1 Fabrication (partly fixed this session, verify)
- Neutralized this session: `_apply_new_audit_causal`, `_apply_batch2_entry_gates`, `_apply_batch2_exit_gates` (plus the 4 from §1.1). **Re-verify they are still passthrough** before trusting any v15 sheet — a generator rebuild could reintroduce them.
- Switches whose ONLY handling was inside those functions are now **UNWIRED in vec** (live still runs them). Examples flagged: `BOUNCE_REENTRY_ENABLED`, `DC_HOPELESS_EXIT_ENABLED`, `EMA_BLANKET_FILTER_ENABLED`, `DELTA_REENTRY_FILTER_ENABLED`, `ALL_TF_AGAINST_CLOSE_ENABLED`, `ATR_TRAIL_SWEEP_ENABLED`, `DC_BREAKOUT_TF/SCORE`, `WT_DC_TF_COMBO`. These need REAL `vec_decisions/` predicates if they are to appear in sweeps — otherwise they must be marked "live-only, not backtestable" and excluded from the TEMPLATE sweep (do not let them show a fake delta).

### 7.2 Default mismatches (config.py vs config_tradier.py vs QuickConfig) — fix deliberately
| Switch | config.py | config_tradier | QuickConfig | Note |
|---|---|---|---|---|
| COOLDOWN_BARS | 3 | 3 | 0 | vec uses 0 (intentional user override) |
| KINDERGARTEN_EMA_GATE_ENABLED | False | True | True | crypto default disagreement |
| DELTA_HTF_GATE | "hh_hl_4h" | "4h" | 0.0 (float!) | **type mistyped** (float for a TF string) |
| AUGMENT_ONLY_WHEN_PROFITABLE | True (bool) | absent | 0.0 (float!) | type mismatch |
| ADX_RANGING_THRESHOLD | 10.0 | 20.0 | 20.0 | crypto disagreement |
| DC_BREAKOUT_TF/SCORE | 1h/15 | absent | 15m/20 | moot (unwired) |
- **QuickConfig has 129 fields declared TWICE** (second silently overrides first, sometimes with different type/value) — a structural hazard; dedupe carefully.
- Core DC sweep switches (`ENTRY_DC_TF`, `TECHNICAL_DC_*`, `DAYTRADE_DC_*`, `WT_LOWER_CROSS_EXIT_TF`, `WT_DC_ENABLED`) are **absent from BOTH config files** — their only default is QuickConfig `"OFF"`; live reads them from `per_sym_active_config*.json`. This is expected, not a bug.

### 7.3 TEMPLATE column-B corruption (per cat_side) — fix before trusting worst_first order
- 220 of 435 switches have disagreeing column-B defaults across the 4 cat_sides. Many are **data corruption**, not intent: boolean switches carrying numeric `0.5 / 1.0 / 2.0` defaults (leaked TF-slot encodings), e.g. `AUGMENT_ONLY_WHEN_PROFITABLE` CL=True/CS=1.0/SS=0.5. Pattern = bad template-clone/column-fill, not per-side tuning.
- Coverage differs: CRYPTO_LONG=426, CRYPTO_SHORT=418, STOCKS_LONG=418, **STOCKS_SHORT=340** (69 switches present in the other three but missing from STOCKS_SHORT, e.g. `KINDERGARTEN_EMA_GATE_ENABLED`, `WT_DC_TF_ENTRY`).
- Genuinely-intentional per-side defaults that look correct: `KINDERGARTEN_EMA_GATE_ENABLED` (CL False, others True — matches config), `BB_SQUEEZE_EXIT_ENABLED` (CL True, others False), `BEAR_MARKET_MODE` vs `BEAR_MARKET_MODE_TRADIER`.

**Action for the next agent:** rebuild the 4 TEMPLATE column-B defaults from the authoritative source (config.py for crypto, config_tradier.py for stocks, per_sym for the DC family), fix the bool-vs-numeric corruption, and make coverage consistent across cat_sides. Only after that is `worst_first` ordering meaningful.

---

## 8. THE 30-TRADE-FLOOR / BASELINE ISSUE (the user's core complaint)

The user's mandate: **the baseline (BEST or TEMPLATE defaults) must give 30–300 trades, and no row may fall below 30 trades.** Then apply each switch/filter worst_first, sum only positive deltas — the gain should MULTIPLY, never collapse to a 0/1/2-trade chart.

Reality found this session:
- Some symbols' per_sym BEST holds ~forever: e.g. `AAVEUSDC_LONG` baseline = **7 trades** (96% TIM) → useless. `AAVEUSDC_SHORT` (no per_sym → TEMPLATE defaults) = **143 trades** → good.
- Entry/filter switches produce 0 delta against a ~always-in-market baseline (nothing to add). Only exits (DC stop/target, WT-lower-cross) lower TIM and create trades.

**The correct baseline for the sweep must include trade-generating exits** so trades land in 30–300. Options the next agent should implement:
1. Prefer the config that yields 30–300 trades: if per_sym BEST < 30 trades, fall back to TEMPLATE defaults (which carry the DC/WT exits).
2. Enforce a **30-trade floor gate**: if a promoted set drops below 30 trades, do not promote it (`trades<30 soften`), keep the prior set.
3. Because the v15 method chains **exits first** (EXIT_STRUCTURAL/EXIT_VELOCITY tabs) via greedy, TIM drops early and later entry switches finally have room to show real deltas — this is exactly why the greedy method beats the old independent-variant sweep.

---

## 9. ETA (measured this session)

- **Old fast sweep (`dc64_proof_sheets --all`, 102-variant tab + chart):** ~**1.76 sym_sides/min/server** (12 workers, ~6 min warmup). Crypto 254 on s1 ≈ **2.5 h**; stocks 197 on s2 ≈ **2 h**; parallel → **~2.5 h for all ~450** → before market open. (But see §1.4 limitation — low-trade baselines make many charts useless.)
- **Full v15 (12 tabs, ~3,800 rows × yellows ≈ 5,000 cells, per-workbook live-parity):** ~**9 min/sym_side** (single, 8 workers). 354 ÷ 2 servers × 9 ≈ **~26 h** — NOT feasible in one night.
- **Smart plan the user wants:** tonight compute only a SUBSET of switches per v15 sheet (the high-value exits + a few entries → 30–300 trades, real multiplied gain), get all sym_sides done fast; **tomorrow add the remaining switches/filters onto the SAME sheets** to get per-row avg deltas, then eliminate low-value switches consciously and speed up. Speed levers: (a) skip switches the first pass shows 0 everywhere, (b) drop per-cell live re-sim (vec is now honest), (c) fill remaining tabs incrementally, (d) 128 internal workers per sym_side.

---

## 10. OUTSTANDING TASKS FOR THE NEXT AGENT (priority order)

1. **Fix the v15_pilot XLSX writer** (bible §6.4 items 1–7): so a run writes E2 numeric, C populated, F/G/H/L:BI real — no refill needed. Until then, run pilot → `tools/v15_refill_from_json.py`. This is the "First priority" whenever a sym_side has `done>0` but the XLS shows `E2=BASELINE`/`C` empty/`F/G` None.
2. **Baseline 30-trade floor** (§8): make the baseline use the config that yields 30–300 trades; gate promotions below 30 trades.
3. **Finish neutralizing / re-wiring fabricated switches** (§7.1): either give UNWIRED switches real `vec_decisions/` predicates, or exclude them from the sweep and mark "live-only." Never let a switch show a synthetic delta.
4. **Rebuild the 4 TEMPLATE column-B defaults** from config/config_tradier/per_sym (§7.3); fix bool-vs-numeric corruption; equalize cat_side coverage; then `V15_AVG_DELTAS` worst_first ordering is trustworthy.
5. **Fix config default mismatches + QuickConfig 129 duplicate fields** (§7.2) — deliberately, per cat_side, matching live.
6. **BB `alignment` live wiring** (§1.3) — NEW STRATEGY: present entry/exit/data/risk, sweep-prove, paper-days before flipping live. Backtest wiring already done behind `WT_DC_DETAILED_SCORER_ENABLED`-style opt-in.
7. **Deliver a finished, filled 30D sheet + REAL_ZOOMABLE chart for each of the 4 cat_sides** (CRYPTO_LONG/SHORT via s1, STOCKS_LONG/SHORT via s2), 30–300 trades, no None, honest deltas. This is the acceptance test the user keeps asking for.
8. **Run the before-market-open pass** (§9 smart plan) once #1/#2 are solid.

---

## 11. COMMAND CRIB (copy/paste, adjust sym_side)

```bash
# --- connectivity ---
ssh s1 'hostname; nproc; ls ~/binance-sandbox/backtest_v8/indicators/*.npz | wc -l'
ssh s2 'hostname; nproc'   # s2 via ProxyJump 10.0.0.4

# --- sync a file Mac -> s1 & s2 (md5 verify) ---
for h in s1 s2; do
  rsync -az -e "ssh -S none -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
    v12_quick_engine.py "$h:~/binance-sandbox/v12_quick_engine.py"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $h 'md5sum ~/binance-sandbox/v12_quick_engine.py'
done
md5 -q v12_quick_engine.py   # compare

# --- v15 pilot, ONE sym_side, greedy worst_first, all cells (skip-list moved aside) ---
ssh s1 'cd ~/binance-sandbox
  mv data/reports/lifecycle_pilot/AAVEUSDC_SHORT_v14_progress.json ~/proof_stale_backup_20260928/ 2>/dev/null
  setsid nohup ~/miniforge3/bin/python -u v15_pilot.py --sym-side AAVEUSDC_SHORT \
    --seq-mode worst_first --workers 128 --window-days 30 > /tmp/v15_aave_short.log 2>&1 < /dev/null & disown
  sleep 20; tail -15 /tmp/v15_aave_short.log'
# then refill XLS from JSON + verify:
ssh s1 'cd ~/binance-sandbox && ~/miniforge3/bin/python tools/v15_refill_from_json.py --sym AAVEUSDC_SHORT'

# --- old fast sweep (1 ledger tab + chart) ---
ssh s1 'cd ~/binance-sandbox && ~/miniforge3/bin/python tools/dc64_proof_sheets.py --sym AAVEUSDC_SHORT'          # one
ssh s2 'cd ~/binance-sandbox && .venv/bin/python  tools/dc64_proof_sheets.py --all --venue stocks --workers 12'   # all stocks

# --- pull results to Mac (host-key flag required for s2) ---
rsync -az -e "ssh -S none -o BatchMode=yes -o StrictHostKeyChecking=accept-new" \
  "s1:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/AAVEUSDC_SHORT_*" SPREADSHEETS/V15_V16_CELL_BY_CELL/

# --- verify an XLS is truly filled (no None) ---
ssh s1 'cd ~/binance-sandbox && ~/miniforge3/bin/python - <<PY
import openpyxl
wb=openpyxl.load_workbook("SPREADSHEETS/V15_V16_CELL_BY_CELL/AAVEUSDC_SHORT_30d_matrix.xlsx")
print("E2=",wb["STDEV_SLOPE_SIZING"].cell(2,5).value)
PY'
```

---

## 12. STRESS-TEST / PROMOTION RULES (bible §10, §13) — before any real-money enable

`tools/opt/metrics.py:compute(events)` → `gain_pct = sum(pnl_$)/peak_concurrent*100`, `pool_sharpe = mean(per_trade_returns)/stdev`, `TIM=held/window*100`, `DD=peak/trough` capped 100%. Gates: `pool_sharpe>0.5` interim (`>1.0` real), `gain/mo>20%`, `≥10× B&H`, `TIM 20–80`, `DD<30`, `≥30/mo` crypto. No annualization.

Beat ideas to death: 1.5–2× slippage/worst-case fills, seek **plateaus not peaks** (profitable across 50–150% param range), walk-forward OOS ≥50% of in-sample, multiple regimes, year-by-year, ≥100 trades ideal. 20% ideas / 80% breaking. See the `backtest-expert` skill `references/methodology.md`. No strategy goes live without full sweep proof + paper days + per-trade parity + stress tests + kill switch.

---

## 13. FILE MAP (what to touch, what not)

| File | Role | Locked? |
|---|---|---|
| `v12_quick_engine.py` | vector engine; `compute_entry_signals` ~9027; QuickConfig ~4546 | LOCKED (row 314) — unlocked by user this session for the BB/WT_DC/fabrication fixes |
| `wt_dc_entry_scorer.py` | live scalar scorer (`score_entry`, `_score_long/_short`) | not locked |
| `wt_dc_entry_scorer_vec.py` | vec scorers (`score_entry_multitf_vec`, `score_entry_detailed_vec`) | not locked |
| `vec_decisions/check_entry_candidates_crypto__bb_squeeze_gate.py` | BB detector+gate+alignment (vec) | not locked ("shared") |
| `v15_pilot.py` | the ONLY writer of V15 workbooks | not locked; **has the writer bug to fix** |
| `tools/dc_simple_8_sweep.py` | old 102-variant sweep (`run_one`) | not locked |
| `tools/dc64_proof_sheets.py` | old-sweep xlsx+chart wrapper (new this session) | not locked |
| `tools/v15_refill_from_json.py` | refill XLS from progress JSON (the reliable path today) | not locked |
| `ez_manage.py`, `ez_positions_quick.py`, `ez_indicators.py` | live crypto | LOCKED — read only unless unlocked |
| `tradier_manage.py`, `tradier_indicators.py`, `config.py`, `config_tradier.py` | live stocks / config | LOCKED — read only unless unlocked |
| `LOCKED_FILES.md`, `BACKTEST_BIBLE.md`, `ABSOLUTE_PROHIBITIONS.md` | governance | read every session |

Backups from this session: `backups/before_bb_wtdc_vec_fix_*`, `before_bb_detector_extract_*`, `before_detailed_scorer_optin_*`, `before_detailed_vec_*`, `before_purge2_fabrication_*`, `before_bb_tf_wire_*`.

---

## 14. TL;DR FOR THE NEW AGENT
- Backtests run on **s1 (crypto) / s2 (stocks) only**; Mac never backtests; S4/S5 gone.
- The engine is now **honest** (fabrication neutralized) — so **0 deltas are real**; the job is to make baselines trade 30–300 (real signal) not to manufacture deltas.
- The **v15 greedy worst_first system is correct**; its **XLSX writer is broken** (fix per §6.4) — until then run pilot → `v15_refill_from_json.py`.
- WT_DC detailed slowdown/accel scorer and BB alignment are wired (opt-in switches, parity-proven); TEMPLATE defaults + config are inconsistent (§7) — fix before trusting worst_first.
- Deliverable the user wants: **one FILLED 30D sheet + chart per cat_side, 30–300 trades, no None, honest deltas** — then the full run per the smart plan (§9).

---

# APPENDIX A — THE 13 SWITCH_SHEETS: WHAT EACH ONE IS FOR

The workbook groups switches by trade-lifecycle stage. `STDEV_SLOPE_SIZING` is tab 1 but **SKIPPED**; the 12 active tabs are filled in this exact order (greedy: exits chained early lower TIM so later entries have room to matter). Row 3..n are white SPECIFIC switches; orange GENERAL rollups follow below the white block, never interleaved.

1. **STDEV_SLOPE_SIZING** *(SKIPPED — `SKIP_SHEETS`)* — 34 rows for `compute_regime_sizing_mult()` (`BAND_SLOPE_SIZING_V2`, `stdev_edge_*`, `stdev_slope_*` per TF D/4h/1h/15m). Half-finished, stalls, and requires `stdev_edge_*`/`stdev_slope_*` in NPZ (243 files on S1, missing on some). Keep skipped until rewritten as a `vec_decisions/` predicate with constant-time masks.
2. **ENTRY_REVERSAL_BOUNCE** — mean-reversion / bounce entries: WT-15m bounce (`WT_15M_BOUNCE_OPEN_ENABLED`), BB pullback/squeeze entries, SBA bounce, RZ/red-zone entries, oversold reversals. Yellows: FILTER_TFs (FIRST_OPEN_THROTTLE, OPEN_INTENT_SIZE_GATES, LIVE_ENTRY_ENGINE, BB_PULLBACK_GATE) + per-switch thresholds (BB_MIN/MAX, LOW/HIGH_1H_GT_PREV, REL_VOL_GT_1).
3. **ENTRY_BREAKOUT_CHANNEL** — momentum/breakout entries: DC breakout (`ENTRY_DC_TF`, `DC_BREAKOUT_*`), channel breakouts, WT_DC entries (`WT_DC_ENABLED`/`_DETAILED_SCORER_ENABLED`), Clenow/momentum, stdev breakout. Largest sheet (~152 distinct switches).
4. **ENTRY_CONFIRMATION_GATES** — gates that must agree before an entry fires: golden-rule HTF consensus, MTF-armed, KINDERGARTEN/EMA_9_21 cross, HTF trend veto, alignment/stoch confirmation. These GATE (AND) rather than trigger (OR).
5. **EXIT_STRUCTURAL** — structural exits: DC stop/target (`TECHNICAL_DC_STOP/TARGET_TF/_BUFFER_PCT`), dc_low_4h hard stop, structural range shift, ALL_TF_AGAINST_CLOSE. **These lower TIM → create trades → are where real deltas come from.**
6. **EXIT_VELOCITY** — momentum-decay exits: WT lower-cross (`WT_LOWER_CROSS_EXIT_TF`), WT momentum exit, velocity/slowdown exits, exhaustion, parabolic protection, peak-giveback.
7. **REENTRY_WINDOWED** — time/price-windowed re-entry after an exit (guaranteed price-cross reentry, windowed cooldowns, rally reentry).
8. **REENTRY_ADAPTIVE** — adaptive re-entry (bounce-after-correction, delta-favorable reentry, breakout retest).
9. **AUGMENT_TREND** — pyramiding into a winning trend (sentiment pyramid, trend augment, delta pyramid).
10. **AUGMENT_RISK_SIZING** — size of augments (band/slope sizing, risk-scaled adds, `AUGMENT_ONLY_WHEN_PROFITABLE`).
11. **REDUCE_PROFIT_LOCK** — partial profit-taking / break-even locks (partial profit lock v2, breakeven gain erosion, gain-erosion trims).
12. **REDUCE_SIGNAL_RATER** — signal-quality-driven reduces (signal rater, DG/disaster-guard reduces).
13. **GLOBAL_RISK_GATES** — account-level gates (overtrade guard, GR filter, hedge gates, circuit sharpe gates, cooldown locks). Has a documented `VLOOKUP` waiver.

**Lifecycle → tab mapping used by yellow eligibility** (`Sheets applicable` in FILTER_DICTIONARY_V2): `ENTRY` → sheets 2–4; `EXIT` → 5–6; `REENTRY` → 7–8; `AUGMENT` → 9–10; `REDUCE` → 11–12; `GLOBAL_CHECK` → 13; `ALL` → every sheet.

---

# APPENDIX B — FULL SWITCH AUDIT REPORT (2026-09-28)

Scope: 435 distinct switches across the 4 cat_side templates; 54 deep-audited. Read-only; nothing modified by the audit. Extracted maps at (subagent scratch, regenerate if needed) `switches.json` / `persheet.json`.

## B.1 Fabrication status in v12_quick_engine.py
**Neutralized (return passthrough at top — confirmed):**
- `_batch1_template_wiring` (~:173), `_wire_07_exit_stops_tranche` (~:651), `_apply_auto_wired_params` (~:1036), `_apply_universal_distinctness_fallback` (~:1138), `_apply_625_entry_gates` (~:11601), `_apply_625_exit_gates` (~:13183), `_apply_625_sizing_mult` (~:14121), `_apply_625_generic_gates` (~:14647).
- **This session additionally neutralized:** `_apply_new_audit_causal` (~:17089, called ~:22025), `_apply_batch2_entry_gates` (~:11388, called ~:9565), `_apply_batch2_exit_gates` (~:11558, called ~:10080).
- Dead / not called (no action needed): `_apply_PZ_causal` (~:16054), `_apply_batch2_augment_gates` (~:11572).
- Pure touch blocks (defeat grep-based wiring audits, harmless but misleading): `_batch3_template_wiring` (~:900–1025), "BATCH 4" (~:8683–8742, line ~:8742 literally `_ = _b4_all  # prove vector read`).

## B.2 UNWIRED in vec (real logic removed with neutralized funcs; LIVE still runs them)
`BOUNCE_REENTRY_ENABLED` (ez×8, tradier×15), `DC_HOPELESS_EXIT_ENABLED` (ez×8, tradier×7), `EMA_BLANKET_FILTER_ENABLED` (ez×4, tradier×5) + `EMA_BLANKET_FILTER_MIN_TFS`, `BTC_BREAKOUT_ENTRY_ENABLED` (ez×8), `BTC_GUARANTEED_REENTRY_ENABLED`, `BREAKEVEN_GAIN_EROSION_ENABLED`, `BTC_DIVERGENCE_EXIT_AGAINST`, `DELTA_REENTRY_FILTER_ENABLED` (ez×9, tradier×8), `ALL_TF_AGAINST_CLOSE_ENABLED` (ez×7, tradier×5, a "FIX vs B&H" exit), `ATR_TRAIL_SWEEP_ENABLED` (ez×5, tradier×5), `DC_BREAKOUT_TF`/`DC_BREAKOUT_SCORE`, `BB_SQUEEZE_EXIT_ENABLED`, `BB_SQUEEZE_ENTRY_TF` (0 occurrences in v12 before this session's wiring), `WT_DC_TF_COMBO` (QuickConfig default only), `KINDERGARTEN_CUMULATIVE_MODE` (default only), `BEAR_MARKET_MODE_TRADIER`.
→ **Action:** each needs a real `vec_decisions/` predicate to be backtestable, OR must be excluded from the TEMPLATE sweep and labeled "live-only." Never show a synthetic delta for them.

## B.3 FAKE-ONLY before this session's purge (synthetic proxy) — now neutralized, verify
`ADX_RANGING_THRESHOLD` (`_apply_batch2_entry_gates`), `GR_FILTER_VEC_ENABLED` (`_apply_new_audit_causal` `rsi>55`), `DYNAMIC_SCORE_AUGMENT_ENABLED` (`_apply_batch2` `adx>20/25`), `HTF_AGAINST_FORCE_CLOSE_ENABLED` (new_audit), `HARD_BREAKEVEN_FLOOR_ENABLED` (new_audit), `PARABOLIC_PROTECTION_ENABLED` (`_apply_PZ_causal` — dead), `DELTA_HTF_GATE` (`_apply_batch2`, also mistyped), `AUGMENT_ONLY_WHEN_PROFITABLE` (`_apply_batch2`).
→ After neutralization these now correctly produce 0 in vec (honest) unless given a real predicate. **Re-verify passthrough survived any generator rebuild before trusting sheets.**

## B.4 Default mismatches (config.py / config_tradier.py / QuickConfig)
| Switch | config.py | config_tradier | QuickConfig | Concern |
|---|---|---|---|---|
| COOLDOWN_BARS | 3 | 3 | 0 | vec 0-bar cooldown (intentional user override) |
| KINDERGARTEN_EMA_GATE_ENABLED | False | True | True | crypto default disagreement |
| DC_BREAKOUT_TF | "1h" | absent | "15m" | moot (unwired) |
| DC_BREAKOUT_SCORE | 15 | absent | 20 | moot (unwired) |
| ADX_RANGING_THRESHOLD | 10.0 | 20.0 | 20.0 | crypto 10 ≠ 20 |
| BOUNCE_REENTRY_ENABLED | True | absent | False | live True, vec unwired |
| DYNAMIC_SCORE_AUGMENT_ENABLED | True | absent | False | live True |
| DELTA_HTF_GATE | "hh_hl_4h" | "4h" | 0.0 (float) | 3-way + **type mistyped** |
| AUGMENT_ONLY_WHEN_PROFITABLE | True (bool) | absent | 0.0 (float) | type mismatch |
| EMA_9_21_FILTER_MIN_TFS | 3 (int) | absent | 3.0 (float) | type drift |

`QuickConfig` has **129 duplicate field declarations** (v12 ~:4566–8254) — second silently overrides first, sometimes different type/value; e.g. `ADX_RANGING_THRESHOLD`, `DC_HOPELESS_EXIT_ENABLED`, `DELTA_HTF_GATE`, `DELTA_REENTRY_FILTER_ENABLED`, `ALL_TF_AGAINST_CLOSE_ENABLED`. Core DC sweep switches (`ENTRY_DC_TF`, `TECHNICAL_DC_*`, `DAYTRADE_DC_*`, `WT_LOWER_CROSS_EXIT_TF`, `WT_DC_ENABLED`) are absent from both config files (QuickConfig `"OFF"` only; live reads per_sym JSON).

## B.5 Vec vs live divergences
| Switch | vec | live | concern |
|---|---|---|---|
| BOUNCE_REENTRY_ENABLED | no-op | implemented | reentry absent from backtest |
| ALL_TF_AGAINST_CLOSE_ENABLED | no-op | active exit | "FIX vs B&H" exit not in vec |
| HTF_AGAINST_FORCE_CLOSE_ENABLED | was `rsi>55` proxy → now 0 | active exit | needs real predicate |
| DC_HOPELESS_EXIT_ENABLED | no-op | active | exit missing in vec |
| EMA_BLANKET_FILTER_ENABLED | no-op | counter-trend block | filter missing |
| GR_FILTER_VEC_ENABLED | was `rsi>55` → 0 | GR filter | needs real predicate |
| DYNAMIC_SCORE_AUGMENT_ENABLED | was `adx>20` → 0 | augment sizing | needs real predicate |
| DELTA_HTF_GATE | proxy + wrong default | HTF gate string | condition + default wrong |

## B.6 Per cat_side template-B issues
220/435 switches have disagreeing column-B defaults across cat_sides; many are **data corruption** (bool switches with numeric `0.5/1.0/2.0` = leaked TF-slot encodings), e.g. `AUGMENT_ONLY_WHEN_PROFITABLE` CL=True/CS=1.0/SL=1.0/SS=0.5; `BTC_GUARANTEED_REENTRY_MAX_AGE_BARS` CL=480/CS=2.0/SS=0.5; `ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC` CL=30/others=2.0. Coverage: CL=426, CS=418, SL=418, **SS=340** (69 present in CL+CS+SL, absent in STOCKS_SHORT). Intentional-and-correct: `KINDERGARTEN_EMA_GATE_ENABLED` (CL False/others True), `BB_SQUEEZE_EXIT_ENABLED` (CL True/others False), `BEAR_MARKET_MODE` vs `_TRADIER`.

## B.7 Verified genuinely-real (of 54): ~26
DC family (`ENTRY_DC_TF`, `TECHNICAL_DC_STOP/TARGET_TF/_BUFFER_PCT`, `DAYTRADE_DC_*`), `WT_LOWER_CROSS_EXIT_TF`, `WT_DC_ENABLED`/`_DETAILED_SCORER_ENABLED`/`_TF_ENTRY`/`_DC_TF`, KINDERGARTEN/EMA_9_21 cross, `WT_15M_BOUNCE_OPEN_ENABLED`, `BB_SQUEEZE_ENTRY_ENABLED`, `STDEV_SLOPE_SIZING_ENABLED`, `DC_DAYTRADE_ENABLED`/`TRADIER_DC_DAYTRADE_ENABLED`. ~18 UNWIRED, ~9 FAKE-ONLY (now neutralized), 1 intentional drift (COOLDOWN_BARS). 381 not individually traced (deep-dive capped at 54) — expected mostly UNWIRED/FAKE given the fabrication footprint.

---

# APPENDIX C — dc_simple_8_sweep.run_one: THE 102 VARIANTS (exact)

Baseline = `_get_template_baseline(is_crypto,is_long)` + per_sym BEST overrides + forced `KINDERGARTEN_EMA_GATE_ENABLED=True/FILTER_TF=4h`, `EMA_9_21_FILTER_ENABLED=True/FILTER_TF=4h`, any `"3m"` → `"OFF"` (3m ignored in vec). `STOP_BUF=0.25`, `TARGET_BUF=0.10`.

- **64 EXIT×ENTRY (`kind=EXIT_ENTRY`):** `TFS_EXIT × TFS_ENTRY`, each `TFS = [OFF,15m,1h,4h,15m,1h,15m,4h,1h,4h,15m,1h,4h]` (8 each). Sets `TECHNICAL_DC_STOP_TF`/`TARGET_TF`+bufs and `ENTRY_DC_TF`+buf. Label `DC64_{exit}x{entry}`.
- **8 EXIT (`kind=EXIT`):** `TECHNICAL_DC_STOP_TF/TARGET_TF=tf`. `TECH_EXIT_{tf}`.
- **8 ENTRY (`kind=ENTRY`):** `ENTRY_DC_TF=tf` (ABOVE low / BELOW high). `ENTRY_ABOVE_{tf}`.
- **4 WT (`kind=WT`):** `WT_LOWER_CROSS_EXIT_TF ∈ {OFF,15m,1h,4h}`. `WT_LOWER_CROSS_{tf}`. *(This is the biggest real delta driver on most symbols.)*
- **4 EMA (`kind=EMA`):** `EMA_9_21_FILTER_ENABLED/FILTER_TF ∈ {OFF,1h,4h,1h,4h}`. `EMA9_21_{tf}`.
- **4 BB (`kind=BB`):** `BB_SQUEEZE_ENTRY_ENABLED` + `BB_SQUEEZE_ENTRY_TF ∈ {OFF,15m,1h,4h}` + `BB_SQUEEZE_EXIT_ENABLED`. `BB_SQUEEZE_{tf}`.
- **4 WT_DC (`kind=WTDC`):** `WT_DC_ENABLED` + `WT_DC_DETAILED_SCORER_ENABLED` + `WT_DC_TF_ENTRY/DC_TF ∈ {OFF,15m,1h,4h}`. `WT_DC_{tf}`.
- **6 AF (`kind=AF`):** afternoon DC stop/target/combo variants at 15m/1h.

Each variant → `eval_gain` → `{variant,kind,tf,gain,delta=gain-base,trades,tim,sharpe_per_trade}`. `best_overall = max(delta)`; `combo` = best_exit+best_entry if both pos. **Limitation:** independent vs same baseline → entry/filter switches show 0 if baseline holds forever. Use v15 greedy for real entry deltas.

---

# APPENDIX D — NPZ FIELD REFERENCE (what's present, what's not)

Present (used by real logic): `open/high/low/close/volume`, `close_{tf}`, `dc_position_{tf}`, `dc_low/high_{tf}` (15m/1h/4h/D...), `wt1/wt2_{tf}`, `wt_velocity_{tf}`, `wt_bull/bear_alignment`, `wt_velocity_up/down_count`, `wt_structure_4h/D`, `wt_composite_bias`, `wt_wave_phase_4h/D`, `wt_cross_bull/bear_{1h,15m}`, `wt_cross_value/rising/prev_value_1h`, `wt_momentum_state_1h`, `wt_divergence_1h`, `wt_divergence_strength_1h`, `atr_{tf}`, `adx_{tf}`, `rsi_{tf}`, `mfi_{tf}`, `sma_200_{tf}`, `ema_9/14/20/50/200_{tf}`, `bb_upper/lower/pct_b_{tf}`, `bb_width_1h/4h`, `squeeze_on_{tf}`, `stoch_k/d_{tf}`, `stoch_crossover/crossunder_15m`, `relative_volume_{tf}`, `ha_{tf}`, `macd_hist_{tf}`, `stdev_edge_{tf}`, `stdev_slope_{tf}` (S1 mostly), `timestamps`.

**MISSING (never in NPZ — do NOT build backtests on them):** `alignment` (bare — dead in live too; wire only as new strategy), `k_3m`/`stoch_k_3m` present but `stoch_k_5m` missing, `wt_cross_bull_5m` missing, and **all 3m/5m/1m TF variants must be excluded from backtests** (validate forward in live). A symbol missing `stdev_edge_15m` → run `backtest_v8_precompute.py --symbol {SYM} --mode crypto` on S1.

---

# APPENDIX E — TROUBLESHOOTING RUNBOOK

| Symptom | Cause | Fix |
|---|---|---|
| `E2=BASELINE` string, C empty, F/G None in XLS but progress JSON has cells | v15_pilot inline writer broken (bible §6.4) | `tools/v15_refill_from_json.py --sym {SYM}_{SIDE}`; then fix the writer |
| chart shows 0/1/2 trades | baseline holds forever (per_sym BEST, high TIM) | use TEMPLATE-default baseline / enforce 30-trade floor (§8) |
| every switch delta 0 | fabrication removed AND baseline always-in-market | expected — need lower-TIM baseline (chain exits first) or real signal |
| `No space left on device` on s1 | root 100% full (`~/binance-git-backup` 145 G) | clear sandbox caches; ask user before touching the 145 G backup |
| `Host key verification failed` (rsync to s2) | `ssh -S none` fresh conn to 10.0.0.4 | add `-o StrictHostKeyChecking=accept-new` |
| `No such file or directory: python` | wrong interpreter | s1 `~/miniforge3/bin/python`, s2 `~/binance-sandbox/.venv/bin/python` |
| run "RUNNING" but log empty / no progress | nohup killed on ssh teardown | relaunch with `setsid nohup ... < /dev/null & disown`, verify log grows |
| `ALREADY FINISHED early ... MUST NOT RETOUCH` | progress-guard skip | move `data/reports/lifecycle_pilot/{SS}_v14_progress.json` aside to force fresh |
| `File is not a zip file` (Mac NPZ) | Mac NPZ truncated | never backtest on Mac; use s1/s2 |
| 1356 rows None after refill | never-positive skip-list | move `disabled_switches_never_pos_per_category.json` aside to compute all cells |
| pilot hangs on one cell | slow vec eval | `YELLOW_TIMEOUT=10` marks cell/tab RED and continues; ensure guard active |
| parity fail (trade ratio / gain mismatch) | vec vs live divergence | mark red + log flags.md, don't abort; investigate the switch's vec predicate |

---

# APPENDIX F — v15_pilot.py FLAGS & KEY FUNCTIONS

CLI: `--sym-side X_LONG`, `--template PATH`, `--out PATH`, `--window-days 30`, `--sheet NAME`, `--workers N`, `--vector-only`, `--no-lbI`, `--allow-mac`, `--seq-mode {sequential,cycle,round_robin,worst2best,worst_first,shuffle}`, `--baseline-json`, `--disable-switches-file`, `--cycle-on-neg`, `--sheet-order CSV`, `--batch-syms A_LONG,A_SHORT,...` (4-NPZ batch, keep all hot).

Key internals to know when fixing the writer: `_hdr_col_map()`/`_resolve_cols()` (row-2 header resolution — USE THESE, never hardcode columns), `_atomic_save` (must validate `ZipFile>=10`), `_spec_clear_live_formulas` (clear H/I at open), `_clear_vlookup_formulas`, `_write_per_row_HIK` (writes K per row; H/I only at end), `_per_yellow_sum` (sum pos yellows → G), `_validate_e_chain_and_yellows` (assert `new_cum>=old_cum`, `delta==vg-cum`), `preload_prepared()`/`ALL_PREPARED` (RAM slice), `write_zoomable_chart()`, `SKIP_SHEETS={"STDEV_SLOPE_SIZING"}`, `YELLOW_TIMEOUT=10.0`.

The herd (`tools/v15_local_herd.py`, `v15_overnight_herd.py`, `v15_cpu80_watchdog.py`) launches one pilot per sym_side with a seq-mode, `push_to_s1()` rsyncs `SPREADSHEETS/` + `data/reports/lifecycle_pilot/` back. Keep workers ≤ RAM 80% (`avail>1500`, `V12_NPZ_CACHE=32`).

---

# APPENDIX G — GLOSSARY

- **cat_side** — one of CRYPTO_LONG, CRYPTO_SHORT, STOCKS_LONG, STOCKS_SHORT (the 4 template families).
- **sym_side** — a specific symbol+side, e.g. `AAVEUSDC_SHORT`, `AAPL_LONG`.
- **yellow cell** — an `O:BI` cell headed `FILTER=OPT` that is *opportune* for a given switch row; holds that filter's delta vs cumulative_before.
- **naked delta** — switch=cand alone (no filter) vs cumulative_before; used only when a row has no yellows.
- **cumulative_before (E)** — the running best gain going into a row; advances only on a positive VECTOR_DELTA.
- **VECTOR_DELTA (G)** — sum of positive yellow deltas for a row (real vector engine).
- **HUSTLE_DELTA (F)** — `vec_gain - baseline_gain` (vs the original baseline, not the running cum).
- **LIVE_DELTA/LIVE_SHARPE (H/I)** — filled ONCE at workbook end via `backtest_v12_engine` on the winning set.
- **worst_first** — sheet/row order by worst avg delta first (so improvements compound); maintained by `V15_AVG_DELTAS`.
- **BH** — buy-and-hold `(c[-1]-c[0])/c[0]*100` (side-signed); pure, no config.
- **TIM** — time-in-market %, `held_bars/window_bars*100`.
- **pool_sharpe / sym_sharpe / sharpe_per_trade** — the only allowed Sharpe labels; per-trade returns based, never annualized.
- **DC** — Donchian channel; `dc_low/high_{tf}`, `dc_position_{tf}` (0=low,1=high).
- **WT** — WaveTrend oscillator (`wt1/wt2_{tf}`, cross = wt1 crossing wt2).
- **prepared / ALL_PREPARED** — the 30-day NPZ slice preloaded into RAM for fast per-cell evals.
- **per_sym BEST** — the live per-symbol override set in `data/hourly_reconfig/per_sym_active_config*.json`.

---

# APPENDIX H — WHAT "DONE RIGHT" LOOKS LIKE (acceptance test)

A correct filled 30D sheet for a sym_side has ALL of:
1. `{SYM}_BASELINE_METRICS!B2` = real `baseline_gain/bh/trades/pool_sharpe`; baseline **trades in 30–300**.
2. `STDEV_SLOPE_SIZING` skipped; the 12 active tabs each have every white row 3..n filled with F and G (float, never None), green/red by sign.
3. `E2` = header string `BASELINE`; `E3` = numeric baseline_gain; other E cells numeric only where a POS delta promoted them; `new_cum >= old_cum` monotonic.
4. Column C shows `switch=cand + positive yellow headers` (bold) only on winning rows; K lists the same yellows; L:BI yellow cells hold per-filter deltas.
5. No row promotes a set that drops trades below 30.
6. H/I blank until the end, then filled once from `backtest_v12_engine` (parity within ratio 0.80–1.25, gain <0.5 pp & <15%).
7. A `..._30D_REAL_ZOOMABLE.html` chart with CLOSE + all trades (entry/win/loss markers), `bh`/`gain` in title+filename, and the same in the xlsx filename (`{SYM}_{SIDE}_bh{..}_gain{..}_30d_matrix.xlsx`).
8. Gain of the final winning set **≥ baseline** (greedy never underperforms — positive-only promotion) and ideally multiplied via chained exits+entries.

If any of these is missing → it's not done; fix the writer/baseline, don't ship it.

---

# APPENDIX I — REFERENCE PSEUDOCODE FOR THE FILL LOOP (implement this shape)

This is the canonical algorithm from §0.5, as runnable pseudocode. Match this behavior exactly.

```python
SHEET_ORDER = [  # 13 tabs — STDEV is a single T/F switch tab, NOT skipped (user override 2026-09-28)
  "STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL",
  "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY",
  "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
  "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES",
]

def fill_sym_side(sym_side, template_path):
    is_long   = sym_side.endswith("_LONG")
    is_crypto = symbol_is_crypto(sym_side)
    # --- Step 0: clone + ingest previous best + baseline ---
    wb = clone_template(template_path, out_path(sym_side))     # _atomic_save validates ZipFile>=10
    clear_live_formulas(wb)                                    # H/I formulas removed at open (rule 2)
    cols = {sheet: header_col_map(wb[sheet], row=2) for sheet in SHEET_ORDER}  # resolve by header, never coord
    best = find_previous_best(sym_side)   # /SPREADSHEETS + V15_V16_CELL_BY_CELL + *_v14_progress.json + hustler_best.json
    overrides = merge(defaults_for(is_crypto, is_long), best)  # BEST WINS — no `if k not in overrides`
    for sheet in SHEET_ORDER:                                  # BEST-C-FILL: write bold override in C for every matching switch
        for r in switch_rows(wb[sheet]):
            sw = cell(wb[sheet], r, cols[sheet]["Switch"])
            if sw in overrides and overrides[sw] != default_of(sw):
                set_bold(cell(wb[sheet], r, cols[sheet]["override"]), fmt(overrides[sw]))
    prepared = preload_prepared(sym_side, days=30)             # NPZ -> RAM, kept for whole workbook
    base = evaluate_prepared_sanitized(prepared, overrides, 30)   # E3 baseline (all defaults+overrides)
    baseline_gain = base.gain
    write(BASELINE_METRICS!B2, base)                           # gain/bh/trades/pool_sharpe
    if base.trades == 0:  # zero-trades still writes XLS then skips sweep (never early-return-before-clone)
        atomic_save(wb); return
    cum = baseline_gain                                        # cumulative_before
    write_numeric(first_pending_E_cell(), cum)                # E3 = baseline_gain (E2 header 'BASELINE' preserved)
    cum_overrides = dict(overrides)

    # --- Step 1+2: greedy worst_first fill ---
    tab_i = 0
    while tab_i < len(SHEET_ORDER):
        sheet = SHEET_ORDER[tab_i]
        r = first_pending_row(sheet)
        advanced_on_this_tab = False
        while r is not None:
            switch = cell(sheet, r, cols[sheet]["Switch"])
            cand   = candidate_value(switch)                  # the value under test for this row
            yellows = opportune_yellows(sheet, switch, cols[sheet])   # see Appendix I.2
            vector_delta = 0.0
            pos_headers  = []
            if not yellows:
                naked = eval(cum_overrides | {switch: cand}, prepared) .gain - cum   # naked delta
                write_float(sheet, r, "HUSTLE_DELTA", naked_vs_baseline(switch,cand))
                write_float(sheet, r, "VECTOR_DELTA", naked)
                # no-yellow rule: ALWAYS go to next TAB regardless of sign
                if naked > 1e-9:
                    promote(switch, cand); cum += naked; cum_overrides[switch]=cand
                break_to_next_tab = True
            else:
                for hdr in yellows:                            # hdr == "FILTER=OPT"
                    filt, opt = hdr.split("=")
                    d = timed_eval(cum_overrides | {switch:cand, filt:opt}, prepared, timeout=10).gain - cum
                    if d is TIMEOUT:  mark_red(sheet, r, hdr, "TIMEOUT 10s"); mark_tab_red(sheet); continue
                    write_float_yellow(sheet, r, hdr, d)       # ALWAYS write the yellow delta
                    if d > 1e-9:
                        vector_delta += d
                        pos_headers.append(hdr)
                write_float(sheet, r, "HUSTLE_DELTA", (eval(cum_overrides|{switch:cand},prepared).gain - baseline_gain))
                write_float(sheet, r, "VECTOR_DELTA", vector_delta)   # green if >0 else red; never None
                break_to_next_tab = (vector_delta <= 1e-9)
            # navigation (rules 5/6):
            if yellows and vector_delta > 1e-9:
                add_to_override_and_perrow(sheet, r, switch, cand, pos_headers)   # ADD names, never overwrite
                for h in pos_headers: cum_overrides[apply(h)]                     # advance cum set
                cum += vector_delta
                r = next_row_same_tab(sheet, r)                # POS -> move DOWN 1 row, same tab
                write_numeric(sheet, r, "BASELINE", cum) if r else None   # E of next row = cum
                advanced_on_this_tab = True
                assert cum >= (cum - vector_delta)             # _validate_e_chain: monotonic
            else:
                r = None                                       # NEG/0/no-yellow -> jump to next tab
        tab_i += 1
        if r_is_first_of_next_tab_after_neg():
            write_numeric(next_tab_first_pending_E(), cum)     # baseline carried into next tab

    # --- Step 3: LIVE verification once, at the end ---
    live = backtest_v12_engine.live_evaluate(sym_side, cum_overrides, 30, timeout=30)
    for processed_row in all_processed_rows:
        write(processed_row, "LIVE_DELTA",  live.gain - baseline_gain_at(processed_row))
        write(processed_row, "LIVE_SHARPE", live.pool_sharpe)
    write_zoomable_chart(sym_side, None, cum_overrides, 30, suffix="30D_REAL_ZOOMABLE")
    atomic_save(wb)
```

## I.2 opportune_yellows(sheet, switch, cols) — yellow eligibility
```python
def opportune_yellows(sheet, switch, cols):
    lifecycle = lifecycle_of(sheet)   # ENTRY / EXIT / REENTRY / AUGMENT / REDUCE / GLOBAL_CHECK
    out = []
    for hdr in yellow_headers_O_to_BI(sheet):          # row-2 headers containing "="
        filt, opt = hdr.split("=")
        row = FILTER_DICTIONARY_V2[filt]
        if row.Recommendation != "SPECIFIC":  continue  # GENERAL = orange rollup, never yellow
        if lifecycle not in row.Sheets_applicable and "ALL" not in row.Sheets_applicable: continue
        if not token_overlap(row.Switches_exactly_gates, switch): continue  # >=2 strong tokens or exact containment
        out.append(hdr)
    return out
```
Rule reminders baked in above: yellow tested for THAT switch ONLY; delta ALWAYS written to the yellow cell; positive → ADD header to `override`+`PER_ROW_FILTERS` and sum into `VECTOR_DELTA`; `E` written only on promotion; H/I only at the end; NPZ stays in RAM; >10 s → red cell+tab, continue.

---

# APPENDIX J — WORKED EXAMPLE (one tab, concrete)

sym_side `AAVEUSDC_SHORT`, baseline (all defaults + previous best) → `E3 = -10.08` (gain), 136 trades. Tab `EXIT_VELOCITY`, first switch row = `WT_MOMENTUM_EXIT_THRESHOLD` (bold default).

Row r (switch `WT_MOMENTUM_EXIT_THRESHOLD=<cand>`), yellows in O:BI: `WT_MOM_EXIT_FILTER_TF=OFF`, `=D`, `=4h`, `=1h`, `=15m`.
- eval cum+{switch, FILTER_TF=OFF} → gain −10.83 → delta vs cum(−10.08) = **−0.75** → write −0.75 in that yellow cell, not summed (neg).
- `=15m` → gain −9.30 → delta **+0.78** → write +0.78, sum into VECTOR_DELTA, add `WT_MOM_EXIT_FILTER_TF=15m` to `override`+`PER_ROW_FILTERS`.
- other TFs neg → written, not summed.
- `VECTOR_DELTA (G) = +0.78` (green). `HUSTLE_DELTA (F) = vec_gain − baseline_gain`.
- POS → **move down 1 row**, `E(next) = -10.08 + 0.78 = -9.30`, `cum = -9.30`, `cum_overrides` now includes `WT_MOMENTUM_EXIT_THRESHOLD=cand` + `WT_MOM_EXIT_FILTER_TF=15m`.

Next row switch `WT_SLOWDOWN_EXIT_ENABLED`, all yellow deltas ≤ 0 → `VECTOR_DELTA = 0` (red), `C` blank, **do NOT move down** → jump to first pending row of next tab `REENTRY_WINDOWED`, write `E = -9.30` there, continue.

At the end: 12–13 tabs processed, `cum` is the final winning gain (≥ baseline because only positive promotions), `cum_overrides` is the winning set → run once through `backtest_v12_engine` to fill H/I and prove parity; write the chart. The xlsx filename encodes `bh`/`gain`.

**What a WRONG (current-bug) sheet looks like:** `E2="BASELINE"` (string) with no numeric `E3`; `C` empty; `F/G` = None across all rows; `#NUM!/0` in E. If you see that, the compute may still be fine in `*_v14_progress.json` — refill via `tools/v15_refill_from_json.py` and then FIX the writer.

---

# APPENDIX K — vec_decisions MODULE INVENTORY (shared scalar+vec predicates)

`v12_quick_engine` imports these at top-level (never inline-duplicate the logic). Live and vec call the SAME predicate. Present modules include:
`breakout_opener`, `check_entry_candidates_crypto__bb_squeeze_gate` (BB detector+gate+alignment, edited this session), `..._compression_boost`, `..._dc_breakout_tiered`, `..._pullback_augment`, `..._sba_gate`, `..._signal_entry_threshold`, `..._strict_stoch_gate`, `..._trend_entry_gate`, `..._vol_spike_reversal`, `..._wr_lr_pullback`, `check_entry_candidates_stocks__ema_alignment_trend_htf_gates`, `..._htf_dc_breakout`, `..._htf_w_m_align`, `..._lh_hl_filter`, `..._rsi_sma_mfi_gates`, `..._rvol_lrpctb_gates`, `..._stdev_breakout_bounce`, `check_exit_candidates_stocks__noloss_bb1h`, `..._stdev_bb_rz_exit`, `bb_pullback_gate`, `counter_trend`, `sba_bounce`, `filter_tf_gate`, `guaranteed_price_cross_reentry`, `reentry_15m_bb_htf`, `reentry_bounce_after_correction`, `reentry_breakout`, `process_position_stocks__wtdc_entry_gates`. Each has a `test_*` file. **Contract for new wiring (bible §7.2):** add a `vec_decisions/` predicate + a single call in `v12_quick_engine`; guard with the 10 s per-yellow timeout; `preload_prepared()` must succeed before the workbook starts; constant-time NumPy masks only (no per-bar Python loops, no dict-mutating per-position state — those stall and desync from `backtest_v12_engine`).

---

# APPENDIX L — METRICS DEFINITIONS (`tools/opt/metrics.py:compute`)

- `gain_pct = sum(pnl_dollars) / peak_concurrent_deployed * 100` (the `avg-trade-deployed-2000-v1` convention; `gain_pct_2000norm` in `simulate_one`).
- `pool_sharpe = mean(per_trade_returns) / stdev(per_trade_returns)` — per-trade, NEVER annualized.
- `sym_sharpe` = per-symbol variant; `sharpe_per_trade` = single-trade-normalized. Always one of these three labels.
- `TIM = held_bars / window_bars * 100`.
- `max_dd_pct = peak-to-trough / peak * 100`, capped 100%.
- `bh = (close[-1]-close[0])/close[0]*100`, side-signed (SHORT flips).
- Promotion gates: `pool_sharpe > 0.5` interim (`>1.0` real), `gain/mo > 20%`, `≥10× B&H`, `TIM 20–80`, `DD < 30`, `≥30 trades/mo` crypto. Sample floor ≥48 crypto / ≥100 stocks × >1yr × ≥30 trades/sym else `[DIAGNOSTIC ONLY]`.
- Commission/slippage: `CRYPTO_ROUND_TRIP_COMMISSION_PCT` + `0.08` slippage round-trip modeled in `simulate_one`.

---

# APPENDIX M — CHART SPEC (REAL_ZOOMABLE.html)

Offline `file://` Chart.js 4.4.1 + hammerjs 2.0.8 + chartjs-plugin-zoom 2.0.1. Data: CLOSE line over the 30-day slice (downsampled to ≤~4000 points), plus scatter overlays from the trade ledger (`simulate_one(...)["ledger"]`): entry = green circle at `(bar_entry, entry_price)`, win exit = green square, loss exit = red diamond at `(bar_exit, exit_price)`; tooltip shows pnl% + exit_reason. Title + filename carry `bh {bh:.2f}% gain {gain:.2f}%`. Interactions: wheel/pinch zoom, drag pan, dblclick reset. The chart must reflect the **winning config's** ledger (not a bare baseline) so it shows all real trades. Never emit a "sin-wave"/synthetic or `ledger 0 trades` chart — that is the fabrication trap.

---

# APPENDIX N — SESSION ARTIFACTS & STATE AT HANDOVER

- **Engine (honest) synced to s1+s2:** `v12_quick_engine.py` md5 `312f12a07dfc3b03f401ad639142f104` (fabrication neutralized incl. `_apply_new_audit_causal`/`_apply_batch2_*`). Re-verify md5 before trusting.
- **New/edited files:** `wt_dc_entry_scorer.py` (opt-in `detailed=`), `wt_dc_entry_scorer_vec.py` (`score_entry_detailed_vec`, parity 0.0), `vec_decisions/check_entry_candidates_crypto__bb_squeeze_gate.py` (detector+alignment), `tools/dc_simple_8_sweep.py` (BB_TF + WT_DC detailed wiring), `tools/dc64_proof_sheets.py` (NEW), `test_wt_dc_detailed_scorer_vec.py` (NEW, 2 passed).
- **Runs:** old `dc64_proof` crypto/stocks runs were killed and their `DC64_NEW_HONEST` outputs deleted (user: "farce"/"waste of space"). A v15 `AAVEUSDC_SHORT` greedy worst_first run (136-trade baseline, 128 workers) was in progress on s1 — check `data/reports/lifecycle_pilot/AAVEUSDC_SHORT_v14_progress.json`; if present, refill + verify + pull.
- **Skip-list moved aside on s1:** `disabled_switches_never_pos_per_category.json` → `.aside` so ALL cells compute (restore later if you want the never-positive fast-skip).
- **Stale/fabricated progress backed up on s1:** `~/proof_stale_backup_20260928/`.
- **Memory (Claude auto-memory):** `v12_quick_engine_synthetic_distinctness`, `bb_squeeze_alignment_dead`, `binance_server_topology_2026_09`.

---

# APPENDIX O — OLD SYSTEM vs NEW SYSTEM (when to use which)

| Dimension | OLD (`dc_simple_8_sweep`/`dc64_proof_sheets`) | NEW (`v15_pilot` + TEMPLATE) |
|---|---|---|
| Output | 1 ledger tab (102 variants) + chart | 13-tab greedy workbook (~5,000 cells) + charts |
| Baseline | per_sym BEST (often holds forever → 2–7 trades) | per_sym BEST / TEMPLATE defaults; **must be 30–300 trades** |
| Method | each switch tested INDEPENDENTLY vs same baseline | GREEDY worst_first — exits chained first → TIM drops → entries matter |
| Positive-delta handling | reports each variant's delta | promotes positive, sums, `E` chains forward |
| Entry-switch deltas | ~0 (baseline in-market) → "crap" | real (baseline has room after exits) |
| Speed (measured) | ~1.76 sym_sides/min/server; ~2.5 h all | ~9 min/sym_side; ~26 h all (needs subsetting) |
| Use it for | fast baseline+exit-delta pass, quick chart | the REAL avg-delta-per-row analysis + elimination |
| Trap | low-trade baselines → useless charts | broken XLSX writer (use refill until fixed) |

**Bottom line:** the OLD system is a fast baseline+exit scan; the NEW system is the real per-row-delta engine. The user wants the NEW system to WORK (writer fixed, 30-trade floor, honest deltas) for the deep analysis, and a fast pass for tonight's baselines.

---

# APPENDIX P — CONNECTION, PRECOMPUTE, HERD

## P.1 SSH / bootstrap
- `ssh s1` and `ssh s2` are configured in `~/.ssh/config` (gateway ProxyJump `157.90.168.35`; s1 ControlMaster `~/.ssh/cm-s1-int` on `127.0.0.1:2201`; s2 via `10.0.0.4`).
- If `Connection refused 127.0.0.1:2201`: `ssh -fNT s1-sftp` first (bootstrap the tunnel), then retry. Try both `s1-int` and `s1-pub` (`157.180.125.52:22`) before declaring s1 down.
- For `rsync` with `ssh -S none` (no ControlMaster), ALWAYS add `-o StrictHostKeyChecking=accept-new` (especially s2/10.0.0.4) or you get `Host key verification failed`.
- The user can run an interactive login themselves by typing `! <command>` in the session prompt (e.g. `! ssh s1`), which puts the output into the conversation.

## P.2 NPZ precompute
- NPZ live in `~/binance-sandbox/backtest_v8/indicators/{SYM}.npz` (601 on s1, mirrored to s2). Built by `backtest_v8_precompute.py`.
- If a symbol is missing a field (e.g. `ZECUSDC` missing `stdev_edge_15m`): `~/miniforge3/bin/python backtest_v8_precompute.py --symbol ZECUSDC --mode crypto` on s1, then re-sync.
- **Only 15m/1h/4h/D/W TFs are usable in backtests.** 3m exists in NPZ but is IGNORED by the vec system (no 15m fallback — set any `"3m"` override to `"OFF"`). **5m/1m are NOT in NPZ** — never backtest them; validate forward in live.

## P.3 The herd (batch driver for the NEW system)
- `tools/v15_local_herd.py` (+ `v15_overnight_herd.py`, `v15_cpu80_watchdog.py`) launch ONE `v15_pilot` per sym_side with a seq-mode (`worst2best`/`cycle`/`shuffle`), keep workers ≤80% RAM (`avail>1500`, `V12_NPZ_CACHE=32`), and `push_to_s1()` rsyncs `SPREADSHEETS/` + `data/reports/lifecycle_pilot/` back to canonical then Mac.
- The herd must never run two heavy sym_sides that each load a fresh NPZ in parallel with high thread counts — that was the historical `ThreadPool>28` `V.load_npz` deadlock. One sym_side at a time per server, with internal per-cell workers, is safe.

## P.4 per_sym config structure
`data/hourly_reconfig/per_sym_active_config.json` (crypto, 253 keys) + `..._stocks.json` (248): `{ "SYM_SIDE": {SWITCH: value, ...}, "_meta": {...} }`. `_meta` is filtered out by `load_per_sym_maps()`. These are the live "BEST" overrides and the source of the DC-family switch values (which are absent from config.py/config_tradier.py).

---

# APPENDIX Q — PARITY & backtest_v12_engine

- Vector (`v12_quick_engine`) must match live-faithful scalar (`backtest_v12_engine`) on the SAME frozen 30-day NPZ. `backtest_v12_engine` calls the REAL `ez_manage.process_position` / `tradier_manage.process_position` bar-by-bar and carries `_assert_live_path` + a warning if a vectorized engine is imported in-process (fine for comparison, but the scalar's numbers must come from the live call path). **Do not defeat these guards.**
- Parity thresholds: trade ratio `0.80–1.25`, gain mismatch `<0.5 pp` AND `<15%`. A per-row parity fail → mark red + append `data/reports/v15_flags/{SYM}_{SIDE}_flags.md`, never abort the sheet.
- `backtest_v12_engine` is ~30 s per evaluation → run it ONCE per workbook (winning set) to fill H/I. Never per-cell.
- The vec `simulate_one` returns a full `ledger` (per-trade `entry_price/exit_price/bar_entry/bar_exit/pnl_pct/exit_reason/qty/deployed`) — that ledger drives the chart AND is the source of truth for `pool_sharpe` (per-trade returns). No ledger → `[UNVERIFIED]`.

---

# APPENDIX R — 365D ROBUSTNESS & STRESS-TEST (before promotion)

## R.1 365D rerun
After the 30D greedy+hustle completes, prepare 365D NPZ via `prepare_batch(sym,365)`, evaluate the winning set vs baseline (`evaluate_prepared_sanitized` + live parity), write `*_365d_matrix.xlsx` (bh/gain in filename) with `_BASELINE_METRICS` for 365D. Overfit warning if `365D delta < 50% of 30D delta`. `trades<30` → `[DIAGNOSTIC ONLY]`. No promotion without positive gain unless 30D positive or beats `bh`.

## R.2 Beat ideas to death (bible §13 / `backtest-expert` skill)
No strategy goes to real money without: full sweep proof + paper-trading days + per-trade parity + stress tests:
- **Parameter sensitivity:** profitable across 50/75/100/125/150% of each key param — seek a **plateau, not a peak.**
- **Execution friction:** re-run with 1.5–2× slippage and worst-case fills; if it dies, it wasn't real.
- **Time robustness:** year-by-year, multiple regimes; out-of-sample must be ≥50% of in-sample or abandon.
- **Sample size:** ≥100 trades ideal; below the sample floor → `[DIAGNOSTIC ONLY]`, never promote.
- Time budget: 20% generating ideas, 80% trying to break them. Rubric → `reports/backtest_eval_*.json`.
- Every strategy needs a **kill switch** wired before any live enable.

---

# APPENDIX S — THIS SESSION'S CHANGELOG (chronological, for provenance)

1. Confirmed `v12_quick_engine.py` LOCKED (row 314); user unlocked it for this work.
2. Found + removed the `arange%2` (BB) and `arange%4` (WT_DC) synthetic trade injections and the exit-config mutations in the entry blocks.
3. Extracted the live BB squeeze detector + gate to `vec_decisions/check_entry_candidates_crypto__bb_squeeze_gate.py`; found the `alignment` key is never populated (dead in live); later wired `compute_alignment_vec` (user-approved) + TF-parametric detector.
4. Neutralized fabrication transforms `_batch1_template_wiring`, `_wire_07_exit_stops_tranche`, `_apply_auto_wired_params`, `_apply_universal_distinctness_fallback`.
5. Verified WT_DC vec change is real (entry mask 1830→8154 True bars) but discovered entry switches are absorbed by high-TIM baselines (honest ~0).
6. Built `tools/dc64_proof_sheets.py` → produced real xlsx+chart per sym_side; delivered crypto+stocks cat_side proofs; deleted later per user.
7. Switch-audit agent found MORE active fabrication (`_apply_new_audit_causal`, `_apply_batch2_entry/exit_gates`) — neutralized those too; catalogued config/QuickConfig/TEMPLATE inconsistencies.
8. Rewrote WT_DC to the detailed slowdown/accel scorer (`_score_long`/`_score_short`) as opt-in `detailed=` (live safe) + vectorized `score_entry_detailed_vec` (parity 0.000000) behind `WT_DC_DETAILED_SCORER_ENABLED`.
9. Killed the old-system runs + deleted crap outputs per user; launched v15 greedy `AAVEUSDC_SHORT` (136-trade baseline, worst_first, 128 workers).
10. Wrote this handover; embedded the canonical fill rules verbatim (§0.5).

---

# APPENDIX T — MASTER "DO NOT" LIST (pin this)

- DO NOT backtest on Mac (no usable NPZ). DO NOT reference S4/S5 (gone).
- DO NOT fabricate any metric — no `arange`/hash/proxy deltas, no annualization, no bare "Sharpe".
- DO NOT revert/rollback/overwrite-newer-with-older any script; find better in `backups/` first; ask before touching the 145 G `binance-git-backup`.
- DO NOT edit LOCKED files without an explicit same-message "unlock <file>".
- DO NOT write sheet cells by coordinate — resolve by row-2 header name.
- DO NOT fill `E` (baseline) on non-promoted rows; DO NOT leave `E2` as a numeric; DO NOT leave `F/G` as None/formula.
- DO NOT use `if k not in overrides` when ingesting previous best; best wins.
- DO NOT apply a yellow filter to any switch but the one on its row; DO NOT test non-yellow filters.
- DO NOT move down a tab on neg/0; DO NOT move to next tab on a positive with rows left below.
- DO NOT fill `LIVE_DELTA`/`LIVE_SHARPE` per-row or leave their formulas in; fill once at the end.
- DO NOT reload NPZ per row; keep in RAM for the whole workbook.
- DO NOT abandon a sym_side halfway; run to the last tab.
- DO NOT enable any strategy on real money without sweep proof + paper days + parity + stress + kill switch.
- DO NOT change `default`-column bold outside `V15_AVG_DELTAS` maintenance.
- DO NOT skip STDEV now — it is a single T/F switch tab (13 tabs total) per the user's current instruction; fix it to constant-time if it stalls.

*End of handover. Obey NO-LIES and NEVER-REVERT above all.*
