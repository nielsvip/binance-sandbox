# SUSPICIOUS_INVESTIGATION_AGENT.md — Revised 2026-09-15 (V15 baseline: 3005 / 228 unique / 274 files / medians / ~1.7 MB)

**Status:** REVISED FROM SCRATCH — synthesizes compact evidence only (prior results 1–6). Supersedes L1-69 taxonomy/checklist. Do not use DOUBLE build.

**Baseline truth (intended V15):** `V15_AVG_DELTAS.xlsx` = 3005 distinct switches × 228 unique sym_sides × 274 files recursive, 16 sheets (SUMMARY, AVG_SWITCHES 3005, DEFAULT_APPLIED 56, AVG_FILTERS 241, VALIDATION 55, AVG_SWITCH_FILTER_COMBO 1001, YELLOW_SYSTEM_ANALYSIS, CATEGORY_RECOMMENDATIONS 89, SWITCHES_* 101 each, FILTERS_* 101 each), headers contain `median`/`median_delta` + `avg_delta`/`avg_delta_gain` (lowercase, case-insensitive PASS) + `median` in AVG_FILTERS, file ~1.7 MB expected (backup proxy `V15_AVG_DELTAS_backup_20260915064219.xlsx` 520 KB, 16 sheets, avg 41.7 occurrences/switch, total_rows 125377, total_yellows 739644, avg 5.9/row median 2). Verified via `openpyxl.load_workbook` `max_row 3006` (3005 data) PASS.

**Current file is DOUBLE and must NOT be used:** `SPREADSHEETS/V15_AVG_DELTAS.xlsx` 3,322,177 bytes (3.17 MB) mtime 2026-09-15T21:13:48, 22 sheets, `AVG_SWITCHES max_row 4714 → 4713` FAIL vs 3005, all `SWITCHES_*` 4713 FAIL, `PER_SYM_DETAILS max_row 244 → 243 data` distinct 243 vs stated 244 (SUMMARY title says 244 syms expanded vs prior 228, row2 says recursive 243 unique from 1055 files — contradiction), `YELLOW_SYSTEM_ANALYSIS` 4713 distinct switches avg 77.7 total_rows 366400 total_yellows_nonzero 1171985 vs backup 3005/41.7/125377, `AVG_SWITCHES median 113 n≥100 2474` aggregate claim false per-category (CRYPTO_LONG n0 1690, CRYPTO_SHORT n0 1708, STOCKS_SHORT n0 1691, STOCKS_LONG n0 30 n1 1661), `SUSPICIOUS_SWITCHES_FILTERS max_row 5121 → 5120` FAIL vs expected 6092 short 972, `AVG_SWITCH_FILTER_COMBO max_row 25001 → 25000` FAIL vs expected `combos[:1000]` with `len>=5` → max 1001 (rebuild_all_bestgain.py L425) — 25× larger and contains 6420 rows with `n<5` violating filter. `CATEGORY_RECOMMENDATIONS` only 2 KEEP switches vs backup many KEEP filters (GR_FILTER_VEC marginal 2.37 etc.). DEEP sheets require ≥100 trades & walk-forward but `PER_SYM_DETAILS trades_est` mean 90.3 median 93 ≥100 only 84/193, done ≥2800 only 64/244 mean 1507, 73% degraded (e.g., 1000BONKUSDC_LONG done 2382 gain 0 quarantined). Treat DOUBLE as forensic artifact, not baseline.

---

## 1. Scope & taxonomy (replaces NOT_CALCULATED/LOW_SAMPLE/SUSPICIOUS_ZERO/PLACEHOLDER)

| Tag | Definition | Count source |
|---|---|---|
| **NOT_CALCULATED** | Cell formula missing or E/G pointed at header (G4 bug) — never computed | Dead §2 E=G4 vs G199, G=$A4 |
| **PLACEHOLDER** | `n=0 never tested for that category` — switch/filter exists globally but zero yellow evaluations in this category's SWITCHES_*/FILTERS_* sheet (category-scoped) | `SUSPICIOUS_SWITCHES_FILTERS` header `n=0 never tested for that category (now 244 syms, expanded)` — 5120 rows, 1720 unique switches (~36% of 4713×4 combos) |
| **LOW_SAMPLE** | `n < 5` (AVG_SWITCH_FILTER_COMBO) or `n < 30` per backtest-expert (worst-first stress) — not actionable | Combo n<5 rows 6420 violate filter; backup 93k combos only 13,630 (14.6%) n≥10 |
| **SUSPICIOUS_ZERO** | `avg==0 && median==0 && std==0 && pos==0 && neg==0` — indistinguishable between true zero delta (n=19 avg0 std0 pos0, e.g., SWITCHES_CRYPTO_LONG row4) and placeholder n=0 avg0 (verified: all SWITCHES_* n0 rows have avg0, no n0 non-zero) | Critic: no placeholder flag in workbook (rebuild_all_bestgain.py L132-134 excludes zero yellows from avg but loses flag; AVG_SWITCHES zero_count first 20 zeros all 0) |
| **BADLY_WIRED** | Filter/switch has no live vector — code path is dummy, hash-block, synthetic early-return, or template column never yellow | See §3 checklist branch B |

**Do not use `avg==0` alone to label SUSPICIOUS_ZERO** — must join `n`, `median`, `std`, `pos_rate` per gap G/H (marginal vs naked). Default-applied filters (112 IsDefault=YES, usually 15m) lift `cumulative_before` by +2–2.5; testing same 15m vs elevated cum gives ~0, testing D/4h/OFF gives –0.3…–1.6 — looks negative but is actually good default. Use `marginal_avg = yellow - naked_per_row` and `marginal_pos_rate`, not raw avg.

---

## 2. Per-row checklist (category-scoped vs badly-wired vs no live vector) — every SUSPICIOUS row must be classified into exactly one branch

Run for each row in `SUSPICIOUS_SWITCHES_FILTERS` (5120 in DOUBLE; 6092 expected in true 3005 baseline) + each `SWITCHES_*`/`FILTERS_*` `n=0` row. Record evidence row-by-row, not bulk.

### Step 0 — File-health gate (gaps A/C/I — absent from playbook L66-68)

- [ ] `V15_AVG_DELTAS.xlsx` passes: `max_row 3006` (3005), 16 sheets, SUMMARY syms 228 (not 243/244), size ~0.5–1.7 MB (not 3.17 MB), `AVG_SWITCHES` header contains `avg_delta`/`median` (avg_delta_gain/median_delta in backup) — if FAIL, STOP and restore backup `V15_AVG_DELTAS_backup_20260915064219.xlsx` before any yellow work.
- [ ] Template health: `TEMPLATE.xlsx` LIVE 819K 860–884K range, `ENTRY_REVERSAL_BOUNCE!199` `E199==IF(G199=""` + `G199==IFERROR(VLOOKUP($A199&"_"&$B199…` (not G4/E3/A4) — if `G4` bug (Dead §2 L30-31, §5 re.sub A/G/E rewrite L78) present, redo row-by-row fix before scoring yellows (gap F: E-BLAND 0 != vg 9.1155 poison).
- [ ] No `File is not a zip file` / BadZipFile / MergedCells / `@` quarantine / ENOSPC (Dead §7 1.1M vs 819K bloat, STOCKS_LONG 863K corrupt) — `xattr -c` + atomic move required; check `openpyxl.load_workbook` succeeds and `conditional_formatting 0 rules` (no hidden 4-col bands).
- [ ] Yellows `L:BI` (cols 12–61) fidelity: `ENTRY_REVERSAL_BOUNCE!WT_15M_BOUNCE_OPEN_ENABLED True` must have **0** yellows (WT is disabled, not a filter) — if 7 yellows (STOCKS_LONG r199 had 7) or 49 bloat (CRYPTO_* 1.1M), colors did not move with switch (gap B L32/75-79) — redo per-row L:BI copy.

### Step 1 — Is it SUSPICIOUS at all? (gap F + H: generic delta==vec_gain-cumulative without G4 branch, median 0 without marginal)

- [ ] Validate row's delta source: `E-chain F=variant_gain - cumulative_before (col G)` and `G==IFERROR(VLOOKUP($A&B,Results_Deltas!$A$2:$P$15000,5,FALSE),"")` with correct row number. If G4/A4, mark NOT_CALCULATED, not SUSPICIOUS_ZERO.
- [ ] For `avg==0` row, fetch `n`, `median`, `std`, `pos`, `neg`, `zero_count`. If `n==0` and `zero_count==0` and no placeholder flag → PLACEHOLDER branch (cannot claim SUSPICIOUS_ZERO). If `n>0` and `median==0` and `pos==0` → check `marginal_avg`/`marginal_pos_rate` (Reboot §2 L39-40 DEFAULT_APPLIED L40) before SUSPICIOUS_ZERO.

### Step 2 — Branch the row (the required 3-way)

**A. CATEGORY-SCOPED (`n==0` in this category, `n>0` globally / marginal>0 elsewhere)**

- [ ] Check same switch/filter in other `SWITCHES_*`/`FILTERS_*` category sheets + `AVG_SWITCHES`/`AVG_FILTERS` master + `AVG_SWITCH_FILTER_COMBO` (backup 1001, not DOUBLE 25000). If `n≥5` and `marginal_avg>0.5` elsewhere (e.g., `WT_15M_BOUNCE_OPEN_ENABLED=True` STOCKS_LONG n38 7.07, CRYPTO_LONG WT False 2.42 vs STOCKS_SHORT –0.54), verdict **CATEGORY-SCOPED** — keep for categories where `avg>1 n≥5`, skip where `avg<-5` (Reboot §3: STOCKS_LONG +0.23 keep, AUGMENT –12.95…–15.9 skip). Do not delete globally.
- [ ] Evidence to log: per-category `n`, `avg_delta`, `median`, `pos_rate`, `marginal_pos`, plus `CATEGORY_RECOMMENDATIONS` header (CRYPTO_LONG 7 syms keep 1 WT +2.42/5 AUGMENT –15.9; STOCKS_LONG 19 syms keep WT +6.06/skip AUGMENT 1.25 –14.6). If `n<5` due to degraded done<2800 (mean 1507, only 64/244 ≥2800), note LOW_SAMPLE, not category effect.

**B. BADLY-WIRED (no live vector — filter/switch never reaches live code)**

- [ ] Check live wiring (primary-1 evidence):
  - `config.py:2870 ATR_ADAPTIVE_SIZING_TARGET_PCT 2.0` (BACKTEST_CHANGE_135 crypto default 2.0) vs `config_tradier.py:2346 1.5` (Phase 8 winner tradier 1.5 != crypto) — divergent, not mis-wired.
  - `v12_quick_engine.py:4692 QuickConfig ATR_ADAPTIVE_SIZING_TARGET_PCT 2.0` (3346 fields, 1249 exhaustive) — `9820-9816 compute_regime_sizing_mult` real vector `clip(target/atr_pct,0.25,4.0)` using `atr_1h/close`; `_apply_625_sizing_mult 13624-13629` synthetic hash-scale **disabled by early return `mult.copy()`** (only stdev ladder is real). Mark 1.015 synthetic as BADLY-WIRED (dead).
  - `backtest_v12_engine.py:12743-12744` dummy audit `if getattr(...ATR_ADAPTIVE_SIZING_TARGET_PCT,None) is not None: _=1` — no real sizing, not parity with `ez_manage`.
  - `ez_manage.py:40382-40397` **real live sizing** `if ATR_ADAPTIVE_SIZING_ENABLED: atr_1h → atr_pct → target 2.0 → mult=min(2.0,max(0.5,target/atr_pct)) → sizing_score+=(mult-1)*50` (crypto path, gated, TF 1h default) — wired.
  - `tradier_manage.py:14339-14342` **hash-block** `_v=get _cfg(ATR_ADAPTIVE_SIZING_TARGET_PCT,0); if is_entry and float!=0 and hash(...)%7==0 and V8_FORCE_REAL!='1': return True BLOCK (1/7)` — not real sizing; `_wire_625_live_reads 7469` includes knob but no sizing like ez_manage; real tradier ATR at 30855-30868 is `DT_TARGET_ATR_ENABLED (2*atr/price)` different knob — mark `ATR_ADAPTIVE_SIZING_TARGET_PCT` for tradier as N_A_VENUE (may be crypto-only per SWITCH_CAUSAL queue — unresolved).
  - Template yellows: `data/reports/lifecycle_pilot/*_progress.json` yellows `{}` empty, `vec None` for 1000BONKUSDC_LONG (delta 0 all vectors invalid, yellows 0) and AVAXUSDC_LONG (delta –0.19 valid but yellows 0); `SPREADSHEETS/TEMPLATE.xlsx ENTRY_BREAKOUT_CHANNEL` max 387 rows, ATR rows 1.0/1.5/1.5/2.5/3.0 (no 2.0 row because 2.0 is tautological default), col247 Correct Options, yellows headers `L:BI` row2 e.g., `ATR_TRAIL_FILTER_TF=OFF`; `Filter=Option Value` not found in any progress yellows (only HYPE log DEBUG for ENTRY_REVERSAL_BOUNCE!25 combo prep failure) — if `Filter=Option Value` never appears as header, mark combo prep as BADLY-WIRED for that sym_side.
  - `config.py` 3198 keys (6027 lines) vs `config_tradier.py` 1466 keys (2465 lines) — 1871 crypto-only, 174 stocks-only, 1292 shared. `SCALP_V3_*` 142 keys crypto-only (194-355, 198 K_1M_MAX etc.) except one leak `SCALP_V3_ENABLED=False` at tradier 2463; `TRENDER_*` 8 keys crypto-only (304 LIN_MIN 0.65, 315 MAD 2.0 etc.) zero in tradier; `BREAKOUT_MULTI_LUNG_*` 7 keys **shared** (not crypto-only — correction); `STDEV_SLOPE_SIZING_*` 10-key block shared (MODE, 15M/1H/4H/D_MAX, LOOKBACK) + 40 STDEV namespace shared (not stocks-only); `AUGMENT_*` 13 shared, TRADIER-suffixed leaks only (`AUGMENT_PYRAMID_TRADIER 4347`, `AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER 1423` etc.).
- [ ] If any wiring check is dummy/hash/early-return/off-by-default tautology, verdict **BADLY-WIRED** — add to `FILTER_DICTIONARY_V2` Status `DEAD-REMOVE` (15 rows) or Recommendation `SPECIFIC 251` → skip, do not test further. Log exact code line.

**C. NO LIVE VECTOR (wired but never yields a vector in current templates/progress)**

- [ ] Check `YELLOW_SYSTEM_ANALYSIS` in backup: 739644 yellows avg 5.9/row median 2 max 50, 93,373 combos only 13,630 n≥10 (14.6%) — if filter never in yellows (0 rows with non-zero yellow), mark **NO LIVE VECTOR** for that template.
- [ ] Check `lifecycle_pilot` progress: unique yellows 0 for BONK set, 0 for ATR 2.0 tautology — if `yellows {}` empty for ≥100-trade syms, vector is NONE (not just filtered).
- [ ] Check template `TEMPLATE_STOCKS_LONG.xlsx` 2001530 bytes 23 sheets: `M=AVG_DELTA` only `ENTRY_REVERSAL_BOUNCE col13='avg_delta_M (V15_AVG redone 2026-09-15) 2.449/7.071' header FF1F4E78` (1/13), other 11 sheets `col13='ATR_TRAIL_FILTER_TF=OFF'` (not M), `STDEV_SLOPE_SIZING max_col 12` no col13 — confirms `1/13 != 13/13` via row2 substring search. Color pattern: header FF1F4E78 dark blue, HUSTLE_DELTA FFD9E1F2, is_default FF548235 green, data 00000000 transparent 1-12, FFDDEBF7 light blue 210 cells cols13+, FFFFFF00 yellow sporadic 22 cells — no `blank-white-blank-orange` 4-col band, `conditional_formatting 0 rules`. If filter lives only in `FILTER_DICTIONARY_V2 A1:Q442` 120 distinct filters (Recommendation SPECIFIC 251 GENERAL 138, Type TF selector 255 param64 threshold56 etc., Status LIVE-wired 388 DEAD-REMOVE 15, SheetsApplicable 11 distinct GLOBAL_CHECK 228 etc., Gates 106 distinct) but never in `L:BI` yellows, verdict **NO LIVE VECTOR** (correct to remove).

### Step 3 — Record per-row verdict

- [ ] Write row to `CATEGORY_RECOMMENDATIONS` style line: `Category | Syms | Type | Name | n | avg/marginal | marginal_pos/yellow_pos | Verdict | Reason` — include `n`, `median`, `marginal_avg`, `marginal_pos_rate` (not just avg). For DOUBLE rows, annotate `DOUBLE→REBASE to 3005` and re-check in backup.

### Step 4 — Gate before any delete

- [ ] Require `n≥100` + `median` + walk-forward per critic robustness (FILTERS sheets require ≥100 trades & WF) — backup `PER_SYM_DETAILS trades_est` mean 90.3 median 93 ≥100 only 84/193, done ≥2800 only 64/244 → 73% degraded, so `n≥100` claim false per-category. Do not delete on `n<5` (combo) or `n1=1679` (AVG_SWITCHES) without trades check. Mark `LOW_SAMPLE` until ≥100 trades.

---

## 3. Reconnection of useful winning multi-filter combos

**DOUBLE suppressed all winners:** `AVG_SWITCH_FILTER_COMBO` 25000 rows `avg_comb pos 2 neg 24998 max 0.52 min –16.4` `avg_marg pos 0 neg 24991 max 0 min –2.78` zero winners — contradicts backup where `marginal 4.9` (`GLOBAL_RISK_GATES+EXIT_TOP_FADE marginal 4.95`, `AVG_SWITCH_FILTER_COMBO 1001 n≥5 filtered, pos winners`). `CATEGORY_RECOMMENDATIONS` master only 2 KEEP switches (CRYPTO_LONG WT False n20 2.45, STOCKS_LONG WT True n38 7.07) all filters SKIP (FH_MOMENTUM –5.7 etc.) vs backup many KEEP filters marginal>0.5 (GR_FILTER_VEC 2.37, NOLOSS 2.06 etc.) — DOUBLE keeps negative tails.

- [ ] **Restore backup combo:** Use `V15_AVG_DELTAS_backup_20260915064219.xlsx` `AVG_SWITCH_FILTER_COMBO 1001` sorted by `marginal` — top marginal winners are meaningful multi-filter gates:
  - `GLOBAL_RISK_GATES:CRYPTO_SPIKE_FADE_THRESHOLD_PCT=10` + `EXIT_TOP_FADE_FILTER_TF=15m` n38 avg_comb –3.03 marginal **+4.95** 52% marginal_pos
  - `REENTRY_ADAPTIVE:CRYPTO_SPIKE_FADE_THRESHOLD_PCT=10` + same 15m n39 –3.52 → **+4.82**
  - `REDUCE_SIGNAL_RATER:CRYPTO_SPIKE_FADE_THRESHOLD_PCT=10` + same **+4.79**
  - `GLOBAL_RISK_GATES:CRYPTO_SPIKE_FADE_THRESHOLD_PCT=5` + same **+4.78**
  - `AUGMENT_RISK_SIZING:SCALP_V3_AUG_BE_STOP_PCT=0` + `FROZEN_STOP_FILTER_TF=15m` n39 –2.36 → **+4.76** 69% pos — etc. (full 1000 list in backup, max marginal 50.4)
- [ ] **Reconnect, don't re-test blindly:** For each combo with `marginal_avg ≥ 1.0` and `marginal_pos ≥ 30%` and `n ≥ 10` (Reboot §2 `ABSOLUTELY KEEP 15 marginal≥1.0 mpos≥30% n≥100`), force-gate both switch and filter yellows in template `L:BI` for all per-switch rows where token-overlap would gate either — per-switch gating avg 5.9/row median 2, not per-tab (would be 50/row 8.5×) nor per-workbook (240/row 40×). Keep `50` cap.
- [ ] **Exact DOUBLE build script not located** (`tools/v15_avg/rebuild_double.py` missing; only `rebuild_all_bestgain.py` exists) — suppression vs expansion unconfirmed beyond row count + n<5 violation. Do not reverse-engineer DOUBLE's 25k logic; rebase to backup 1001.

---

## 4. Removal of useless (what to actually delete/skip)

Use backup thresholds, not DOUBLE's zero-winner tail:

- [ ] **Filters SKIP if `marginal_avg < –1` AND `yellow_pos < 1%` AND `marginal_pos < 5%` AND `n ≥ 50`** — per Reboot §2 `DISCARD 25 marginal<0 mpos<5% n≥50` and §4 table (Co 105 pos0 n>500 worst –9…–11.95 are correctly `False/OFF` in config.py: `COOLDOWN_LOCKS D –11.95 n1098 pos0`, `COOLDOWN_LOCKS 1h –11.78 n773`, `BANDAID 0.5 –11.23 n1154`, `BAND_ARROW True –11.21`, `CRYPTO_SPIKE_FADE 12.5 –11.11`, `FROZEN_STOP 1h –9.09 n2817` etc.). Freeze `OFF`, exclude from 84 combos. See `FILTERS_*` 101 per category — `FROZEN_STOP D –2.18 n1227 ypos0` SKIP saves 1227 yellows, etc.
- [ ] **Switches SKIP if `avg < –5`** (or `<–3` for crypto_short small sample n=3) — backup `AUGMENT_TREND:BOUNCE_MIN_GAIN_PCT=0.625 –15.97` 0% pos etc. — skip testing to save 30% rows.
- [ ] **Do not delete defaults:** 112 IsDefault=YES (usually 15m) — `GR_FILTER_VEC +2.46 48% n2773`, `GOLDEN_RULE +2.30 63% n1875`, `NOLOSS +2.24 70% n4035`, `BT_WT_CROSS +1.78 30% n4488`, `MTF_ARMED +1.54 50% n1303` — keep absolute (Reboot §4). For bases with divergence (`BB_PULLBACK_GATE 15m +0.87 vs 1h –0.54`, etc.) verdict `USE OTHER SETTINGS 96` — keep only best TF variant (usually 15m), reduce `240→~90` yellows ~60% saving (YELLOW_SYSTEM_ANALYSIS recommendation 2). `FILTER_DICTIONARY_V2` `DEAD-REMOVE 15` are analog to PLACEHOLDER.
- [ ] **Per-row check:** If filter's `avg_marg pos0` already in DOUBLE's 2 pos vs 24998 neg, re-check in backup before deleting — DOUBLE's negatives are poisoned by degraded progress (only 64/244 ≥2800).

---

## 5. M-refill hold (do not refill column M until suspicious cleared)

- [ ] **Hold M at 1/13:** Current `TEMPLATE_STOCKS_LONG.xlsx` 2,021,270 bytes 23 sheets: `ENTRY_REVERSAL_BOUNCE col13='AVG_DELTA_STOCKS_LONG'` max_col 248 201 rows, `STDEV_SLOPE_SIZING max_col 12` no col13, other 11 param sheets `col13='ATR_TRAIL_FILTER_TF=OFF'` (verified `TEMPLATE.xlsx` LIVE: ENTRY_REVERSAL_BOUNCE `AVG DELTA` 201 rows max_col 248, STDEV `None` max_col 13 max_row 30, others `ATR_TRAIL_FILTER_TF=OFF` max_col 247, `TEMPLATE_CRYPTO_LONG`/`STOCKS_SHORT` same pattern via openpyxl). `FILTER_DICTIONARY_V2` 442×17 `20.0` etc. is not data. **Claim `13/13 sheets match M=AVG_DELTA` is FALSE — actual 1/13** via row2 substring search. Do not treat as global M.
- [ ] **Do not refill `M` (`AVG_DELTA` / `avg_delta_M`)** for any sheet until:
  1. File-health gate (G4→G199 per-row `re.sub` A/G/E rewrite, L:BI exact copy, `xattr -c` + atomic move, sheet order `LEGEND_FILTERS, INSTRUCTIONS` first) passes (Dead §5 L78, §2 E/G formula G4 bug L30-31, yellows 7 vs 0 L32/75-79, sheet order/quarantine/MergedCells/BadZipFile/ENOSPC L33-40/83-84 — gaps A/C absent from playbook Steps 1-2).
  2. Per-row checklist (§2) completes for that sheet's `SUSPICIOUS` rows — category-scoped rows kept in other cats, badly-wired/NO LIVE VECTOR removed, winning combos reconnected.
  3. `V15_AVG_DELTAS` rebased to 3005/228/274 (not DOUBLE 4713/244) and medians verified — then refill M from `SWITCHES_*`/`FILTERS_*` `median` + `avg_delta` (lowercase) + `avg_marg` per sheet, with `STDEV_SLOPE_SIZING` as separate (currently no col13 — intentional, not missing).
- [ ] **Color hold:** Keep current fills (header FF1F4E78, HUSTLE FFD9E1F2, is_default FF548235, data 00000000, cols13+ FFDDEBF7 210 cells, yellow FFFFFF00 22 cells sporadic) — no `blank-white-blank-orange` 4-col repeating band nor `conditional_formatting` rules to restore. `FILTER_DICTIONARY` fills FFE2EFDA/FFDDEBF7 are not template fills. Do not invent orange band (unresolved — may be rendered view or older variant).
- [ ] **TEMPLATE_V15_* phrase matches no file** (glob 0 hits); `V15_TEMPLATE_FIX_SMART.md` confirms 13 trading sheets inside `TEMPLATE_STOCKS_LONG` not separate V15 files — do not wait for `TEMPLATE_V15_*`.

---

## 6. Execution (what to run — replaces 6092-row generic)

- [ ] **Resolve count mismatch:** Expected 6092 rows (3005×? per prior) vs DOUBLE 5120 (short 972, 1720 unique switches 36% of combos) — after rebasing to 3005, recompute `SUSPICIOUS_SWITCHES_FILTERS` as `n==0 never tested for that category` per-category (not global), header `SUSPICIOUS: n=0… (now 228 syms, not 244 expanded)` — 244 is DOUBLE expansion artifact from 1055 files.
- [ ] **Worst-first + cycle-on-neg preserved:** Reboot §3 worst-first global `STDEV –4.49 → REDUCE_PROFIT –2.30` + within-sheet `AUGMENT –12.95 first → WT True +0.15 last` (per-cat STOCKS_LONG `EXIT_STRUCTURAL –0.33 → ENTRY_REVERSAL_BOUNCE +0.23`, STOCKS_SHORT `ENTRY_CONFIRMATION –1.47 → ENTRY_REVERSAL_BOUNCE –0.54`, CRYPTO keeps global for small n) and `deque per sheet cur%len round_robin 3012/3012 cycles424` (v15_pilot_0914.py:1179 `first10=[ENTRY_BOUNCE False, STDEV True…]`) — keep, but do not run until M hold lifts and templates pass health (Dead §5 2-min row-by-row: `re.sub` formula rewrite + fill L:BI 12–61 + `xattr -c` + atomic move, ~200 rows×13 sheets×60 cols =156K cells/file ~15s/file). Playbook L34 `invalid_yellows` presence check insufficient — require exact L:BI column fidelity (gap B) and G4 root-cause branch (gap F).
- [ ] **Stress/OOS (gap J):** Reboot §5 stress `30D→365D slippage 1.5–2× param ±50% shuffle vs WF cycle-on-neg 3012/3012 cycles424` + `marginal +2.47 vs shipped –4.02` — retain as decision gate `pool_sharpe + VECTOR_DELTA (F) + marginal_avg vs naked` vs `REV2 sequential + filterless + best-first` control (should lose). Require `worst-first + cycle > sequential` on all 4 cats and `marginal_pos` stable.
- [ ] **Infra (gap I):** Reboot `INFRA ssh s1-int jump L91-95 / DEAD L53 psutil/72 workers/ControlMaster/ENOSPC L3` — playbook Safety L67 `do not run on Mac` insufficient; require `ssh s1-int` (`127.0.0.1:2201`) jump, `psutil 7.2.2`, `72 workers`, not `14`. Check `s1/s2/s3/s5` union for `274 files` (not 1055 DOUBLE) — currently unreachable from sandbox (unresolved).

---

## 7. Safety — merged with gaps A–J (previous Safety L66-68 had no file-health/xattr/MergedCell check)

- [ ] **Gap A:** Dead §2 E/G formula G4 bug L30-31 + §5 `re.sub` A/G/E rewrite L78 — add to Steps 1–2 (playbook search `G4/G199/formula` 0 hits).
- [ ] **Gap B:** Dead yellows `L:BI` exact-column fidelity L32/75-79 and 49 vs 0 bloat L123-129 — add to Step 2 (playbook L34 only presence check).
- [ ] **Gap C:** Dead sheet order/quarantine/MergedCells/BadZipFile/ENOSPC L33-40/83-84 — add to Safety (playbook L66-68 none).
- [ ] **Gap D/E:** Reboot worst-first L46-57 + cycle-on-neg deque L5/57/79/98 — add to Execution (no worst-first/cycle/sheet-order param).
- [ ] **Gap F:** Dead E-BLAND 0 != vg 9.1155 poison L43/Reboot L5 — add to Step 1 generic `delta==vec_gain-cumulative` without G4 branch (playbook L35).
- [ ] **Gap G/H:** Reboot marginal vs naked L39-40/71-72 `DEFAULT_APPLIED` L40 — add to Step 1 median 0/avg 0 L13 fix with `marginal` correction → false SUSPICIOUS_ZERO on defaults.
- [ ] **Gap I:** Reboot infra ssh s1-int jump L91-95/DEAD L53 — add to Safety.
- [ ] **Gap J:** Reboot §5 stress OOS `pool_sharpe`/`VECTOR_DELTA`/shuffle L77-88 — add to Decision/Execution L42-49/53-56.

---

## 8. Evidence map (inspected, not pointers)

- Primary-0: `SPREADSHEETS/V15_AVG_DELTAS.xlsx` 3322177 bytes 22 sheets exists, `SWITCHES_*` 4713 vs expected 3005 FAIL, backup 3006 PASS, `SUSPICIOUS max_row 5121→5120` vs 6092 FAIL, `PER_SYM_DETAILS 244→243` distinct 243 vs 244, SUMMARY `244 syms expanded` vs prior 228, headers `avg_delta` + `median` present, `AVG_FILTERS avg_comb+median`, `YELLOW distinct 4713/245` vs backup 3005.
- Critic: master 4713 vs 3005, `n` stats `mean 77.26 median 113 n1 1679 n≥100 2474` aggregate false per-category (CRYPTO_LONG n0 1690 etc.), `PER_SYM_DETAILS 84/193 ≥100 trades` `64/244 ≥2800` 73% degraded, `SUSPICIOUS 5120 short 972`, `AVG_SWITCH_FILTER_COMBO 25000` 6420 `n<5` vs 1001, `CATEGORY_RECOMMENDATIONS` 2 KEEP vs backup many, `n=0` placeholder vs true zero indistinguishable (all n0 avg0).
- Primary-1: `config.py:2870 2.0` / `config_tradier.py:2346 1.5`, `v12_quick_engine.py:4692 2.0 1249 exhaustive 9812-9816 real clip 0.25–4.0 13624-13629 synthetic early return dead`, `backtest_v12_engine.py:12743 dummy`, `ez_manage.py:40382 real mult 0.5–2.0 (mult-1)*50`, `tradier_manage.py:14339 hash-block 1/7 7469 _wire_625`, `lifecycle_pilot` BONK delta 0 yellows {} vec None, AVAX –0.19 yellows 0, `TEMPLATE.xlsx` ENTRY_BREAKOUT_CHANNEL max 387 ATR 1.0/1.5/1.5/2.5/3.0 no 2.0, col247 Correct, yellows L:BI row2 `ATR_TRAIL_FILTER_TF=OFF`.
- Primary-2: `TEMPLATE_STOCKS_LONG.xlsx` 2001530 bytes 23 sheets 13 param verified, `M=AVG_DELTA` only 1/13 `ENTRY_REVERSAL_BOUNCE col13 avg_delta_M 2.449/7.071 FF1F4E78`, `STDEV max_col 12` no col13, colors FF1F4E78/FFD9E1F2/FF548235/00000000/FFDDEBF7 210 cells/FFFF00 22 cells no 4-col band 0 conditional_formatting, `FILTER_DICTIONARY_V2 A1:Q442` 120 distinct SPECIFIC 251 GENERAL 138 etc. LIVE 388 DEAD 15, `TEMPLATE_V15_*` 0 hits, `V15_AVG_DELTAS 3322177` exists, `V15_TEMPLATE_FIX_SMART.md` 13 sheets inside.
- Primary-3: `SUSPICIOUS_INVESTIGATION_AGENT.md L1-69` taxonomy Steps 0-4 Execution 6092 Safety 3 bullets, `V15_0915_DEAD_HANDOFF.md L1-131` §2 corruption table E=G4 G=$A4 yellows 7 vs 0 etc. §5 2-min `re.sub` fix L78 §7 update 00:45/01:07 49 yellows bloat 1.1M vs 819K, `V15_0914_REBOOT_HANDOFF.md L1-129` UPDATE 20:09 S1/S3/S5 health psutil ENOSPC, §2 V15 16-sheet avg –2.91 vs marginal +2.47, §3 worst-first orders, §5 stress OOS — gaps A–J inspected line-complete.
- Primary-6: `config.py` 3198 / `config_tradier.py` 1466 keys, 1871 crypto-only 174 stocks-only 1292 shared, `BASE_TF 3m` vs `MTS_WEIGHT 5m 2.0` etc., 40 STDEV shared, 13 AUGMENT shared, 142 SCALP_V3 crypto-only (1 leak), 8 TRENDER crypto-only, 7 BREAKOUT_MULTI_LUNG shared (correction), `STDEV_SLOPE_SIZING` 10 shared (not stocks-only), TRADIER-suffixed leaks only.

## 9. Unresolved (explicit)

- `Filter=Option Value` exact identity not found as header; only HYPEUSDT_SHORT_v15_28.log DEBUG for combo prep failure — no lifecycle_pilot progress contains yellows key `Option Value` (unique yellows 0 for BONK/ATR 2.0 tautology).
- `tradier_manage.py` real sizing for `ATR_ADAPTIVE_SIZING_TARGET_PCT` not located beyond hash-block/DT_TARGET_ATR — may be crypto-only per SWITCH_CAUSAL N_A_VENUE, unconfirmed beyond grep.
- `v12_quick_engine _apply_625` synthetic 1.015 dead via early return — whether intended dead unconfirmed without 2026-08-09 commit note.
- `blank-white-blank-orange` band not found in fills/conditional_formatting — may be rendered view or older variant.
- `PLACEHOLDER` literal absent from Recommendation/Type; `FILTER_DICTIONARY_V8` sheet/file not found — DEAD 15 are analog.
- `TEMPLATE_V15_*` 0 hits; `13/13 M=AVG_DELTA` inaccurate (1/13).
- `V15_V16_CELL_BY_CELL` current byte-size/formula health (STOCKS_LONG 863K vs 819K) secondary verification recommended per Dead §7 1/4 rescued state — out of scope.
- Exact DOUBLE build script that produced 25001 combos not located (`tools/v15_avg/rebuild_double.py` missing; only `rebuild_all_bestgain.py` exists) — suppression vs expansion unconfirmed beyond row count + n<5 violation.
- 1055 vs 339 vs 274 file count for dedup law 244 vs 243 vs 228 requires S1/S2/S3/S5 union — SUMMARY marks degraded unreachable from sandbox.
- Placeholder vs true zero at yellow vector level not verifiable locally: sampled lifecycle_pilot progress files have yellows 0 vectors None (AVAX etc.) so per-switch yellows distinction untestable on Mac.

---
*Generated from compact evidence only. Rebase to backup before any template work. Do not refill M until per-row checklist clears.*
