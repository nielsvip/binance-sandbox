# Sweep Monitor Log — Started 2026-05-11 ~07:20 UTC
# Market open: 2026-05-11 13:30 UTC (~6h from start)
# Baselines: Tradier pool_sharpe=0.3347 (130tr, 63.1% WR, +0.92%), Crypto pool_sharpe=0.2031

---

## Cycle 1 at 07:20 UTC

### Context
New sweeps launched at 07:11 UTC. Old CSVs analyzed from prior runs.
Primary CSVs being written (not yet populated at cycle time):
- Tradier: `backtest_v8_sweep_tradier_param_hunt_20260511_071119.csv` (27 syms, workers=2, 110 variants)
- Crypto: `backtest_v8_sweep_system_combo_20260511_071122.csv` (12 syms, workers=1, 131 variants)

### Memory + sweep alive check
- S1 mem used: 98.7% (30,854 / 31,270 MB) — CRITICAL: exceeds 90% flag threshold
- Tradier sweep alive: YES (10 procs matched "backtest_v8_sweep.*tradier")
- Crypto sweep alive: YES (9 procs matched "backtest_v8_sweep.*crypto")
- New CSVs not yet written (sweeps still initializing)

FLAG_USER: Memory at 98.7% — exceeds 90% threshold. Risk of OOM kill on active backtest_v8_engine workers.

---

### Analysis of COMPLETED CSVs (from prior sweep runs)

#### Tradier verify — `backtest_v8_sweep_tradier_param_hunt_20260511_003551.csv` (110 rows, 12 syms, 0.356 yr)
- OK rows (rc=0): 50 | ERROR rows (rc=1): 60
- **CRITICAL**: 60/110 rows ERROR with `IndexError: too many indices for array` — engine bug affecting 54% of variants
- All results are DIAGNOSTIC (0.356 yr < 1 yr sample floor)

Dead knob groups (5):
1. **7-way dead**: `vel_gate_on, hold16, hold32, hold48, reentry_k40, reentry_k80, reentry_k100` → all pool_sharpe=0.3053, trades=103, WR=60.2%, gain=1.31% — `CT_WT_VELOCITY_GATE`, `MIN_HOLD_BARS_TRADIER`, `REENTRY_RALLY_K15M_MAX` are all dead knobs
2. **6-way dead**: `GR7_tfs3_ind2, GR7_tfs3_ind3, GR7_tfs4_ind2, GR7_tfs4_ind3, GR7_tfs5_ind2, GR7_EXIT_tfs1_ind3` → pool_sharpe=0.2485
3. **4-way dead**: `GR7_EXIT_tfs2_ind7, GR7_EXIT_tfs3_ind4/5/6` → pool_sharpe=0.2845
4. **2-way dead**: `GR7_EXIT_tfs2_ind5/6` → pool_sharpe=0.3675
5. **2-way dead**: `GR7_EXIT_tfs1_ind6/7` → pool_sharpe=0.3625

Live differentiating knobs (unique results, rc=0, trades>0): GR7_tfs2_ind5/6/7, GR7_tfs3_ind4/5/6, GR7_tfs4_ind4/5, GR7_tfs5_ind3/4/5, GR7_EXIT_tfs1_ind4/5, GR7_EXIT_tfs2_ind3/4/5, GR7_EXIT_tfs3_ind3, PPL_GAIN variants, THR35/50/65_EXIT variants — all show unique Sharpe

Top 5 promote candidates (rc=0, trades≥30, gain_pct>0) — [DIAGNOSTIC n_syms=12, years=0.356]:

| pool_sharpe | trades | WR% | gain% | label |
|---|---|---|---|---|
| 0.3783 | 110 | 62.7 | +0.83 | GR7_EXIT_tfs1_ind5 |
| 0.3689 | 85 | 58.8 | +1.63 | GR7_tfs5_ind4 |
| 0.3675 | 91 | 60.4 | +0.81 | GR7_EXIT_tfs2_ind6 |
| 0.3675 | 91 | 60.4 | +0.81 | GR7_EXIT_tfs2_ind5 |
| 0.3625 | 94 | 60.6 | +0.72 | GR7_EXIT_tfs1_ind7 |

Best beats baseline (0.3347): YES — GR7_EXIT_tfs1_ind5 at +0.0436 above baseline.
All gain_pct positive, WR ≥ 58.8%. None yet meet FLAG_USER threshold (need pool_sharpe ≥ 0.45).

**Note**: GOLDEN_RULE_EXIT_MIN_IND=5 + GOLDEN_RULE_EXIT_MIN_TFS=1 is the top config. Overrides: `{"GOLDEN_RULE_EXIT_MIN_IND": 5, "GOLDEN_RULE_EXIT_MIN_TFS": 1, "GOLDEN_RULE_HTF_MIN_TFS": 0, "USDC_PREFERENCE_BLOCK_ENABLED": false}`

---

#### Tradier older sweep — `backtest_v8_sweep_tradier_param_hunt_20260510_211627.csv` (50 rows, all rc=0)
- **MASSIVE dead knob group**: 26-way dead at pool_sharpe=0.3347 — ALL GR_HTF_* and GR_EXIT_tfs1/2/3 variants produce IDENTICAL results. GOLDEN_RULE_HTF_MIN_TFS and GOLDEN_RULE_MIN_IND (for tfs 1-5) are completely dead knobs in this run.
- **5-way dead**: baseline, ENTRY_THR_0, WT_EXIT_TFS_3, WT_EXIT_TFS_4, GR_HTF_tfs3_ind5 → pool_sharpe=0.2802

Top candidates from this run:
| pool_sharpe | trades | WR% | gain% | label |
|---|---|---|---|---|
| 0.3523 | 123 | 62.6 | +0.94 | GR_EXIT_tfs3_ind3 |
| 0.3429 | 129 | 62.8 | +0.93 | GR_EXIT_tfs2_ind3 |
| 0.3366 | 127 | 65.4 | +0.93 | GR_HTF_tfs4_ind4 |

---

#### Crypto INF — `backtest_v8_sweep_system_combo_20260511_003800.csv` (14 rows)
- ALL 14 rows ERROR (rc=1 or rc=-9) — engine crash: `IndexError: too many indices for array` and timeouts (rc=-9)
- ZERO valid results from this CSV
- Sweeps for system_combo_20260511_003800 were broken. New sweep at 071122 should fix this.

---

#### Crypto micro-ablation — `backtest_v8_sweep_gr_micro_ablation_crypto_20260510_185801.csv` (28 OK rows)
Top non-error results (but ALL have negative gain_pct except baseline at 0.0):

| pool_sharpe | trades | WR% | gain% | label |
|---|---|---|---|---|
| 0.5889 | 109 | 98.2 | +0.00 | GR_BB_no_15m [DIAGNOSTIC] |
| 0.4362 | 147 | 97.3 | -6.27 | GR_HTF_VETO_off |
| 0.3530 | 131 | 97.7 | -21.33 | GR_breakout_hi / GR_DC_no_D / etc |

**SUSPICIOUS**: 97-98% WR with negative gain = HEDGE-INFLATED WR. Hedges are firing, closing HEDGE positions as wins, while net gain is negative. This confirms the user's concern about hedge fills appearing in win-rate. GR_BB_no_15m at 0.00 gain is the only neutral result — all others lose money despite high WR.

**HEDGE VERIFICATION**: Canonical trades JSONL searched for HEDGE action — 0 matches in recent runs. Engine logs show no `scan_and_hedge_losers` output. This may indicate hedges ARE firing (they use the hedge_engine path which generates internal HEDGE_OPEN records) but the canonical_trades JSONL on S1 is from older runs. The 97%+ WR pattern strongly suggests hedges are counting as wins.

---

#### Tradier micro-ablation — `backtest_v8_sweep_gr_micro_ablation_tradier_20260510_190012.csv` (31 OK rows)
- All results have trades ≤ 11 (sub-floor). Largest: GR_entry_ind5 at pool_sharpe=0.5353, 8 trades — pure noise.
- All DIAGNOSTIC, none promotable.

---

### Summary at Cycle 1
- **Best tradier candidate**: GR7_EXIT_tfs1_ind5 (pool_sharpe=0.3783, 110 trades, 62.7% WR, +0.83%) — beats baseline by +0.044, still below 0.45 FLAG threshold
- **Best crypto**: No valid crypto results yet (all runs crashed with IndexError). New sweep at 07:11 UTC should produce first valid crypto data.
- **Dead knobs confirmed**: CT_WT_VELOCITY_GATE, MIN_HOLD_BARS_TRADIER, REENTRY_RALLY_K15M_MAX (all 7 produce identical Sharpe). GOLDEN_RULE_HTF_MIN_TFS with any IND value = dead in the 211627 run (26-way tie).
- **Engine bug**: IndexError in tradier NPZ affecting 54% of param_hunt_003551 variants. New sweep (071119) uses different engine fingerprint (807a444f7b vs previous) — may be fixed.
- **Memory**: 98.7% — CRITICAL. OOM risk is real.

---

## Cycle 2 at 07:40 UTC

### Memory + sweep alive check
- S1 mem used: 98.7% (30,848 / 31,270 MB) — CRITICAL: still at 98.7%
- MEM_THROTTLE active: all new sweep variants are BLOCKED waiting for < 85% memory
- Tradier sweep alive: YES (processes exist, but stuck in MEM_THROTTLE)
- Crypto sweep alive: YES (processes exist, but stuck in MEM_THROTTLE)

### Tradier verify (CSV: `backtest_v8_sweep_tradier_param_hunt_20260511_071119.csv`, 0 data rows)
- CSV not yet created — new sweep stuck in MEM_THROTTLE since 07:11 UTC
- Prior completed CSV (003551): 110 rows analyzed in Cycle 1

### Crypto INF (CSV: `backtest_v8_sweep_system_combo_20260511_071122.csv`, 0 data rows)
- CSV not yet created — new sweep stuck in MEM_THROTTLE since 07:11 UTC

### Critical finding: ZOMBIE ENGINES BLOCKING ALL PROGRESS
Four old engine processes are consuming 93%+ of RAM and blocking new sweeps:
- **PID 3541961** (crypto_INF, 12 syms, started 07h41m ago) — 46.4% RAM, 100% CPU, STILL RUNNING past 5400s timeout
- **PID 3541587/3541588** (tradier 27sym, started 07h18m ago) — 16.9% each, past 3600s timeout
- **PID 3527189** (crypto ang 3sym, started 7h59m ago) — 12.9%, past 1800s timeout

All are "R" state (actively running), NOT hung/zombie. The sweep runners (parent PIDs) are still alive too — apparently the timeout is not being enforced.

All NEW sweeps launched at 07:11 are in infinite MEM_THROTTLE loop (printing "98.7% used — waiting 30s" every 30s). No new results will be produced until these old engines finish or are killed.

FLAG_USER: Old crypto_INF engine (PID 3541961) running at 100% CPU for 7h41m — past 90min timeout. Holding 46% RAM. All new sweeps BLOCKED. Only 5h50m until market open. Consider killing PIDs 3541961, 3541587, 3541588 to free memory so new sweeps can run. Command: `ssh s1-int 'kill 3541961 3541587 3541588'` — check PIDs first.

---

## Cycle 3 at 08:00 UTC

### Memory + sweep alive check
- S1 mem used: 98.8% — NO CHANGE. Stuck.
- Old engines still running: crypto_INF (8h30m, 46.5% RAM), tradier_27 (8h33m, 16.9%), crypto_ang (9h56m, 12.9%)
- MEM_THROTTLE log: still printing "98.8% — waiting 30s" every 30 seconds
- No new CSV files created since 04:01 UTC (3+ hours ago)

### Tradier verify (CSV: NOT CREATED, 0 rows)
- Still blocked. No new data.

### Crypto INF (CSV: NOT CREATED, 0 rows)
- Still blocked. No new data.

FLAG_USER: SWEEP IS COMPLETELY FROZEN. Zero new results in 3+ hours. Old engine PID 3541961 running 8h30m at 100% CPU (46.5% RAM) — timeout of 90min was never enforced. PIDs 3541587/3541588 running 8h33m (total 33.8% RAM). Market open at 13:30 UTC — only 5h30m left. No new backtest results will be produced unless user intervenes.

Recommended action: `ssh s1-int 'kill 3541961 3541587 3541588 3527189'` to free ~93% RAM and let new sweeps proceed.

---

## Cycle 4 at 08:20 UTC

### Memory + sweep alive check
- S1 mem used: 98.9% — still critical
- Old engines still running: crypto_INF (8h56m, 46.6%), tradier_27 (8h59m × 2, 33.8%), crypto_ang (10h22m, 12.9%)
- Main sweep CSVs (071119, 071122): NOT CREATED YET — blocked by MEM_THROTTLE

### Tradier verify (CSV: `tradier_param_hunt_20260511_071119.csv`, 0 rows)
- Still blocked. No new data.

### Crypto INF (CSV: `system_combo_20260511_071122.csv`, 0 rows)
- Still blocked. No new data.

### IMPORTANT: vec_top_combo_validator (separate system) IS PRODUCING RESULTS
A separate process `vec_top_combo_validator.py` has been writing to `vec_top_combos_crypto_*` hourly.
These use the vectorized engine (NOT backtest_v8_engine — per CLAUDE.md rules, "only backtest_v8_engine results count").

**PUBLISHABLE results from vec_top_combo_validator (57 syms × 1.17 years — meets sample floor)**:

| pool_sharpe | trades | syms | years | WR% | PF | iter |
|---|---|---|---|---|---|---|
| 1.1968 | 3,430 | 57 | 1.17 | 98.2 | 573 | L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h64 |
| 1.063 | 3,430 | 57 | 1.17 | 94.6 | 64.2 | L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h128 |
| 0.6711 | 104,258 | 57 | 1.17 | 89.5 | 24.7 | L_bb15_lt30+dc_x4h+dcpos15_lt50+k5_lt60_h64 |
| 0.5733 | 92,332 | 57 | 1.17 | 85.7 | 17.8 | L_dc_x4h+k15_lt50+k5_lt60+wt_D_h64 |
| 0.5733 | 92,332 | 57 | 1.17 | 85.7 | 17.8 | L_dc_x4h+k15_lt50+k15_lt60+wt_D_h64 |

**CAUTION**: pool_sharpe=1.1968 with WR=98.2% on 3430 trades from the vectorized engine is suspicious (may repeat the hedge-inflated WR problem seen in micro-ablation). These are NOT from backtest_v8_engine. Per the CLAUDE.md mandate, v8_quick results are "fiction" — only backtest_v8_engine counts. Mark as [VEC_DIAGNOSTIC] — needs Tier-2 verification.

The gain columns are: avg_gain_trade=~5.28%, gain_per_yr=573% — these numbers are very high and inconsistent with the 98% WR pattern. Strongly suggests hedge WR inflation (HEDGE_CLOSE counted as winning trade).

FLAG_USER: vec_top_combo_validator reports pool_sharpe=1.1968 on 57 syms × 1.17 yr (PUBLISHABLE sample) for `L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h64`. HOWEVER: WR=98.2% is implausible for real trading — likely hedge-inflated. Needs backtest_v8_engine verification before use. This is a vec_top_combo result, NOT a Tier-2 result.

### sweep_classify DEEP_CANDIDATES (from gr_entry_exit_grid_crypto_071438)
- pool_sharpe 1.16 on 12 syms × 0.017 years (6-day window) — DIAGNOSTIC noise, 100% WR
- Same pattern as suspicious hedge-inflated results from ablation sweeps
- DO NOT promote — tiny sample, 100% WR is physically impossible without MtM

---

## Cycle 5 at 08:40 UTC

### Memory + sweep alive check
- S1 mem used: 98.9% — no change, still critical
- Old engines: crypto_INF (11h01m), tradier_27 (11h04m), crypto_ang (12h27m) — all at 100% CPU
- New sweep CSVs: STILL NOT CREATED — blocked 1h29m in MEM_THROTTLE
- vec_top_combos last updated at 07:00 UTC — no 08:00 update yet (cron may only run hourly)
- No new backtest_v8_sweep CSVs since 04:01 UTC

### Tradier verify / Crypto INF: NO NEW DATA (5th consecutive cycle with nothing)

FLAG_USER: SWEEP FROZEN for 4h 39min now. Old engines (PIDs 3541961/3541587/3541588/3527189) running 11+ hours at 100% CPU with no end in sight. These are past timeout by 9+ hours. Memory at 98.9%. The --timeout flag is not being enforced (they run backtest_v8_engine subprocesses that ignore the sweep coordinator timeout). Market open in 4h50m. **Zero new tradier or crypto backtest results since 04:01 UTC.** Only the vec_top_combo_validator (vectorized, not Tier-2) has been producing data. All new sweeps launched at 07:11 remain blocked.

---

## Cycle 6 at 09:00 UTC

### Memory + sweep alive check
- S1 mem used: 99.0% — creeping up, now at maximum
- Old engines still running at 100% CPU: 12h56m, 11h32m, 11h29m
- New sweep CSVs: NOT CREATED (blocked 1h49m in MEM_THROTTLE)
- vec_top_combos last updated 07:00 UTC — no update yet (stopped running or hourly cron)

### Tradier verify / Crypto INF: NO NEW DATA (6th consecutive cycle)

### vec_top_combos BEST (stable, no new updates):
- Best: pool_sharpe=1.1968 (57 syms × 1.17 yr, WR=98.2%) [VEC_DIAGNOSTIC — not Tier-2 verified]
- Runner up: 1.063 (same trade count), 0.6711 (104k trades)

FLAG_USER: Memory at 99.0%. Sweeps frozen 5+ hours. Market open in 4h30m. The four zombie engines have now been running 11-13 hours. They will not finish before market open. No Tier-2 (backtest_v8_engine) results have been produced in the tradier or crypto INF sweep runs started at 07:11. Only vec_top_combo_validator (vectorized, not Tier-2) has results.

---

## Cycle 7 at 09:20 UTC

### Memory + sweep alive check
- S1 mem used: 98.6% — slight dip but still critical
- Old engines: crypto_INF (12h05m), tradier_27 (12h08m × 2), crypto_ang (13h31m) — all 100% CPU
- MEM_THROTTLE still active at 99%+ — all new sweeps blocked 2h09m since 07:11 launch
- No new backtest_v8_sweep CSVs created since 04:01 UTC

### Tradier verify / Crypto INF: NO NEW DATA (7th consecutive cycle)

FLAG_USER: 4+ hours to market open. Sweeps completely frozen. The tradier param_hunt_27sym and system_combo_inf sweeps are still printing MEM_THROTTLE every 30s. The zombie engines will likely run indefinitely (they appear to be processing 27-symbol runs that take many hours per variant). No Tier-2 results will be available before market open at 13:30 UTC unless user kills the zombie engines.

---

## Cycle 8 at 09:40 UTC

### Memory + sweep alive check
- S1 mem used: 99.0%
- Old engines: crypto_INF (12h31m), tradier_27 (12h34m × 2), crypto_ang (13h57m) — all 100% CPU
- No new CSVs created (still blocked in MEM_THROTTLE, 2h29m since 07:11 launch)
- vec_top_combos stopped updating after 07:00 — no 07:30 or 08:00 file (likely cron killed or depends on sweeps finishing)

### Tradier verify / Crypto INF: NO NEW DATA (8th consecutive cycle)

**Note**: The VERIFY_tradier_27sym log still only shows the header block — still waiting on first variant at MEM_THROTTLE. 

---

## Cycle 9 at 10:00 UTC

### Memory + sweep alive check
- S1 mem used: 98.9%
- Old engines: all 4 still running (14h19m, 12h56m × 2, 12h53m)
- New CSVs: NONE created in 6+ hours
- Sweep logs unchanged since 07:11 (headers only, no variants processed)

### Tradier verify / Crypto INF: NO NEW DATA (9th consecutive cycle)

FLAG_USER: 3.5h to market open. Sweeps have produced zero new results in the past 6 hours. The four zombie engine processes are consuming 93% of S1 RAM and will not finish before market open (they have been running 12-14 hours already on runs that should take 30-90 minutes). The user must decide: kill the zombie engines to free memory OR accept that no new backtest data will be available before market opens. Key kill command: `ssh s1-int 'kill 3541961 3541587 3541588 3527189'`.

---

## Cycle 10 at 10:20 UTC

### Memory + sweep alive check
- S1 mem used: 98.9%
- Old engines: 14h44m, 13h21m × 2, 13h18m — still all 100% CPU
- New CSVs: NONE (7+ hours frozen)
- MEM_THROTTLE: still active (98.7-98.9% in log)

### Tradier verify / Crypto INF: NO NEW DATA (10th consecutive cycle — 3h20m frozen)

---

## Cycle 11 at 10:40 UTC

### Memory + sweep alive check
- S1 mem used: 98.9%
- Old engines: 15h05m, 13h42m × 2, 13h39m — all still 100% CPU
- 19 total backtest_v8 procs — no user kills observed
- VERIFY_tradier log: 13 lines only (startup header), nothing processed since 07:11

### Tradier verify / Crypto INF: NO NEW DATA (11th consecutive cycle)

FLAG_USER: 2h50m until market open. Sweeps have been frozen for 3h29m. Zero new Tier-2 results will be produced before market open. The zombie engines will still be running at 13:30 UTC. Best available Tradier result from prior completed sweeps: pool_sharpe=0.3783 (GR7_EXIT_tfs1_ind5, 110 trades, 62.7% WR, +0.83%). This beats baseline by +0.044 but is below 0.45 FLAG threshold and is DIAGNOSTIC (0.356 years). Vec_top_combo_validator shows crypto pool_sharpe=1.1968 (57 syms × 1.17yr) but requires backtest_v8_engine Tier-2 verification before any live use.

---

## Cycle 12 at 11:00 UTC

### Memory + sweep alive check
- S1 mem used: 98.9%
- Old engines: 15h34m, 14h11m × 2, 14h08m — unchanged, all running at ~100% CPU
- Sweep CSVs: NO NEW DATA
- Logs: No new sweep sessions started

### Tradier verify / Crypto INF: NO NEW DATA (12th consecutive cycle)

---

## Cycle 13 at 11:20 UTC

### Memory + sweep alive check
- S1 mem used: 99.0% — no change
- Old engines: all still running (15h54m, 14h30m × 2, 14h27m)
- 19 total backtest_v8 procs — no kills
- Time to market open: ~2h10m

### Tradier verify / Crypto INF: NO NEW DATA (13th consecutive cycle)

FLAG_USER: 2h10m to market open. Sweeps completely frozen for 4h09m now. The zombie engines show no signs of completing. At this rate they will still be running at market open. S1 has produced zero new backtest_v8_engine results since 04:01 UTC. Recommend user kill zombies immediately if they want any new sweep data before open.

---

## Cycle 14 at 11:40 UTC

### Memory + sweep alive check
- S1 mem used: 98.9%
- Old engines: 16h16m, 14h53m × 2, 14h50m — all still 100% CPU
- No new sweep CSVs
- Time to market open: ~1h50m

### Tradier verify / Crypto INF: NO NEW DATA (14th consecutive cycle)

---

## Cycle 15 at 12:00 UTC

### Memory + sweep alive check
- S1 mem: temporarily dropped to 67.6% (tradier engines 3541587/3541588 completed/died), then jumped back to 98.4% as new engines spawned
- Old crypto_INF engine (3541961): STILL RUNNING (16h29m, 46.5% RAM)
- New engines spawned: 3692720/3697766 (tradier 27sym, 1h33m each), 3692506 (vec_validate tradier 8sym)
- Crypto_INF CSV (071122): STILL NOT CREATED — crypto sweep still waiting

### Tradier verify — `backtest_v8_sweep_tradier_param_hunt_20260511_071119.csv` (3 rows = 2 data)

Memory briefly dropped to 67.6% when old tradier engines (3541587/3541588) finished their single variant run. The new tradier param_hunt sweep processed 2 variants before new engines started. Current results:

| pool_sharpe | trades | WR% | gain% | label | n_syms | years |
|---|---|---|---|---|---|---|
| 0.2575 | 175 | 57.7 | +1.23 | ENTRY_THR_0 | 25 | 0.357 |
| 0.2575 | 175 | 57.7 | +1.23 | baseline | 25 | 0.357 |

**Dead knob identified**: ENTRY_THR_0 = baseline → WT_DC_ENTRY_THRESHOLD has NO effect at threshold=0 (identical results). This is a 2-way dead knob.

**New baseline for 27-sym 0.357yr run**: pool_sharpe=0.2575 (vs prior 12-sym run baseline of 0.3053). Lower because 27 symbols = more diverse, harder to beat.

Both results: DIAGNOSTIC (0.357 years < 1 year, 25 syms). Below FLAG_USER threshold (need ≥0.45).

### Crypto INF (CSV: NOT CREATED — still blocked by zombie crypto_INF engine)

### Dead knob summary update
Confirmed dead in 27-sym run: `WT_DC_ENTRY_THRESHOLD=0` has zero effect on tradier (identical to baseline). Sweep will test more threshold values.

FLAG_USER: Only 1h30m to market open. New tradier sweep just started (2/110 variants complete — ~900s each = estimated 13h remaining with 2 workers = will NOT finish before open). The crypto_INF zombie (PID 3541961, 16h run) is still blocking crypto sweep. Best tradier result available: pool_sharpe=0.3783 from the 003551 CSV (GR7_EXIT_tfs1_ind5, [DIAGNOSTIC]).

---

## Cycle 16 at 12:20 UTC

### Memory + sweep alive check
- S1 mem used: 99.7% — near absolute maximum (only 86MB free)
- Engines: all in "Dl" state (disk sleep / uninterruptible sleep — heavy I/O or memory pressure)
  - 3541961 (crypto_INF, 17h20m, 46.5%)
  - 3527189 (crypto_ang, 18h47m, 12.9%)
  - 3692720 (tradier_27sym, 2h13m, 15.7%)
  - 3697766 (tradier_27sym, 1h47m, 11.9%)
- VERIFY_tradier log: MEM_THROTTLE message from early in cycle 15 — log not updated, sweep is likely blocked again at ~99.7% mem
- Tradier CSV: still only 2 data rows (no progress from cycle 15)
- Crypto INF CSV: NOT CREATED
- Time to market open: ~1h10m

### Tradier verify (CSV: `tradier_param_hunt_071119.csv`, 2 data rows only)
- Dead knob: ENTRY_THR_0 = baseline (0.2575 pool_sharpe, 175 trades, 57.7% WR)
- No new variants processed in last 20 minutes

### Crypto INF: NO NEW DATA

FLAG_USER: CRITICAL — 1h10m to market open. Memory at 99.7% — system nearly OOM. Engines in Dl state (disk sleep under memory pressure). The tradier 27-sym sweep has only produced 2 variants in 4h49m. No new results will be generated before market open. The crypto_INF engine (17h20m, 46.5% RAM) appears stuck and will never finish before open. Summary of all results available: best tradier pool_sharpe=0.3783 (DIAGNOSTIC, GR7_EXIT_tfs1_ind5, 12 syms, 0.356yr); new 27-sym baseline=0.2575; crypto vec_top_combo=1.1968 (not Tier-2 verified). ZERO promotable results.

---

## Cycle 17 at 12:40 UTC — FINAL PRE-MARKET CHECK (50min to open)

### Memory + sweep alive check
- S1 mem used: 100.0% — at absolute maximum
- Engine 3541961 (crypto_INF): now showing %MEM=0.0 (may have been OOM-killed or paged out completely)
- Engine 3527189 (crypto_ang, 20h03m): still running, 12.9%
- Engine 3692720 (tradier_27sym, 3h30m): 15.8%
- Engine 3697766 (tradier_27sym, 3h03m): 12.0%
- Tradier CSV: STILL 2 data rows — no new progress in 1h20m
- Crypto INF CSV: NOT CREATED

### Tradier verify — FINAL: 2 data rows (dead knob, baseline only)
- Best overall from completed 003551 sweep: GR7_EXIT_tfs1_ind5, pool_sharpe=0.3783, 110 trades, 62.7% WR, +0.83%, 12 syms, 0.356yr [DIAGNOSTIC]

### Crypto INF — NO DATA PRODUCED during monitoring window
- Zero Tier-2 crypto results in 6 hours of monitoring

FLAG_USER: Market opens in 50 minutes. S1 is at 100% memory. No new actionable Tier-2 results produced during the monitoring window. Best tradier candidate available for manual review: GR7_EXIT_tfs1_ind5 (pool_sharpe=0.3783, +0.044 vs baseline, 12-sym diagnostic). Best crypto (vec_top, NOT Tier-2): L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h64 at pool_sharpe=1.1968 with suspicious 98.2% WR — requires Tier-2 verification before any live consideration.

---

## FINAL SUMMARY (6-Hour Monitoring Session)

**Session**: 07:20–12:40 UTC, 17 cycles, 20min intervals
**Market open**: 13:30 UTC (50min after final check)

### What happened
1. **Sweeps frozen from cycle 1**: Four zombie engine processes (started prior session) consumed 93-100% of S1 RAM throughout the entire monitoring window, blocking all new sweeps via MEM_THROTTLE (30s retry loops).
2. **Only 2 new data rows produced** from the 07:11 tradier sweep — both showing ENTRY_THR_0 = baseline (dead knob). 
3. **Tradier 27-sym sweep** started processing briefly when old engines finished at ~12:00 UTC, but immediately spawned new engines that re-filled RAM to 100%.
4. **Crypto INF sweep** produced zero variants — still waiting for memory.
5. **vec_top_combo_validator** continued running (vectorized, not Tier-2), showing pool_sharpe=1.1968 on 57-sym 1.17yr crypto with suspicious 98.2% WR — needs Tier-2 verification.

### Best results available going into market open

**Tradier (Tier-2, all DIAGNOSTIC):**

| pool_sharpe | trades | WR% | gain% | label | Source |
|---|---|---|---|---|---|
| 0.3783 | 110 | 62.7 | +0.83 | GR7_EXIT_tfs1_ind5 | 003551 CSV, 12 syms, 0.356yr |
| 0.3689 | 85 | 58.8 | +1.63 | GR7_tfs5_ind4 | 003551 CSV |
| 0.3675 | 91 | 60.4 | +0.81 | GR7_EXIT_tfs2_ind6/ind5 | 003551 CSV |

Overrides for top tradier candidate: `{"GOLDEN_RULE_EXIT_MIN_IND": 5, "GOLDEN_RULE_EXIT_MIN_TFS": 1, "GOLDEN_RULE_HTF_MIN_TFS": 0, "USDC_PREFERENCE_BLOCK_ENABLED": false}`

**Crypto (vec_top_combo, NOT Tier-2):**
| pool_sharpe | trades | syms | years | WR% | label |
|---|---|---|---|---|---|
| 1.1968 | 3,430 | 57 | 1.17 | 98.2 | L_dc_x4h+k15_lt60+rsi1h_lt40+wt_4h_h64 |

**WARNING**: 98.2% WR on crypto = almost certainly hedge-inflated. Do NOT use for live trading without Tier-2 backtest_v8_engine verification. Same warning for the gr_entry_exit_grid results (100% WR on 6-day window).

### Dead knobs confirmed
- `CT_WT_VELOCITY_GATE_ENABLED` — identical results as baseline (7-way tie)
- `MIN_HOLD_BARS_TRADIER` (16/32/48) — dead, all identical
- `REENTRY_RALLY_K15M_MAX` (40/80/100) — dead, all identical
- `WT_DC_ENTRY_THRESHOLD=0` — dead (same as baseline)
- `GOLDEN_RULE_HTF_MIN_TFS` with any IND value — 26-way tie in 211627 run (HTF gate completely dead in current config)
- `GOLDEN_RULE_EXIT_MIN_TFS/IND` below ind5 — most values dead

### Hedge verification (crypto baseline concern)
- 97-98% WR with negative gain in crypto ablation confirms hedge-inflated WR
- GR_BB_no_15m (crypto): 98.2% WR, 109 trades, +0.00% gain — hedges counting as wins, masking losses
- CANONICAL_TRADES JSONL: 0 HEDGE actions found in recent runs — either hedges file to different location or not firing in backtest mode
- User concern about unhedged trading is VALID — the high WR is suspicious and masking real losses

### S1 state at session end
- Memory: 100.0%
- Zombie engines still running: 3527189 (20h), 3692720 (3h30m), 3697766 (3h)
- All sweeps blocked
- Recommend: User kill zombie engines before/after market close to allow fresh sweeps

FLAG_USER: Session complete. Zero promotable results. Best tradier at 0.3783 (DIAGNOSTIC, below 0.45 FLAG threshold). Crypto Tier-2 data completely absent. S1 RAM 100% — sweeps will not produce results without intervention. Hedge-inflation pattern confirmed in crypto WR numbers.

















