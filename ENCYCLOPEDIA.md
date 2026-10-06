# ENCYCLOPEDIA — what every function does, live and vectorized, and how to diagnose a sym_side

> Built 2026-10-06 from a read-only code census (8 chapters, ~10k lines) + fleet evidence (523 finished sheets,
> ~0.77M current-engine evals with trade counts). Hub document: start here, follow links into the chapters.
> **Line numbers drift — always grep the function/switch name.** Everything marked *unverified* in a chapter was not
> proven in code. Rules of the house still apply: BACKTEST_BIBLE.md (§14, §18–§21, §56, §58, §64, §65) wins on conflict.

| # | Chapter | Covers |
|---|---|---|
| 01 | [ez_manage part 1](docs/encyclopedia/01_ez_manage_part1_policy_execute.md) | crypto policy helpers, `_psym_get`, signal router, golden rule, `execute_trade_action` (~59 gates), `execute_now` (~74 gates) |
| 02 | [ez_manage part 2](docs/encyclopedia/02_ez_manage_part2_process_position.md) | `process_position` gate-by-gate (exit order = exit-reason ownership), reentry, augment, PPL, loops, overseers |
| 03 | [tradier_manage](docs/encyclopedia/03_tradier_manage.md) | stocks live: `process_position`, `evaluate_stop`, entry stack, reentry ladder, gap/MOC, `_cfg` precedence, 20 read-first facts |
| 04 | [live helpers + scalar engine](docs/encyclopedia/04_live_helpers_and_scalar_engine.md) | `ez_positions_quick` (HLR/SELL_TOP, quick exits), `mtf_live_evaluator`, reentry daemon, `backtest_v12_engine` parity modes |
| 05 | [v12_quick_engine](docs/encyclopedia/05_v12_quick_engine.md) | `QuickConfig`, `simulate_one` in execution order, every reason string → code path, metrics/validity, engine LEVER TABLE |
| 06 | [vec_decisions](docs/encyclopedia/06_vec_decisions.md) | 203 modules: 112 reachable / 91 never called, catalog by lifecycle, vec LEVER TABLE, fabrication suspects |
| 07 | [v15_pilot map](docs/encyclopedia/07_v15_pilot_map.md) | main()/spec-fill/DONE flow, env vars, eval primitives, progress JSON schema, publish gates, landmines |
| 08 | [results diagnosis](docs/encyclopedia/08_results_diagnosis.md) | where every metric lives, fleet fault census, diagnosis thresholds, data gaps |
| 09 | [lever evidence summary](docs/encyclopedia/09_lever_evidence_summary.md) | top-40 trade adders/removers per cat_side (empirical, all sym_sides) — raw: `data/encyclopedia/lever_evidence.csv`, fleet table: `data/encyclopedia/fleet_diagnosis.csv` |

Per-switch wiring (defaults ×4 cat_sides, live read sites, vec read sites, template rows, status) is NOT repeated here:
it lives in `SWITCH_BIBLE.md` / `data/SWITCH_BIBLE.json` (3582 switches). Caveat from chapter 06: ~35 switches marked
wired there are read only in vec modules that are never called, and ~25 are read-and-discarded — a SWITCH_BIBLE
"WIRED" is necessary, not sufficient.

---

## 1. The ten facts that explain most bad 30D results (read before diagnosing anything)

1. **Generic exits are labels, not closes.** `VEC_EXIT_SIG_IS_TRIGGER=False` (all 4 cat_sides): everything ORed into
   `exit_sig` (DELTA_EXIT, VEL_EXIT, MFI_FLIP, GR_HTF, lane exits, `ported_exit`, stdev/gap/formation exits, ~15 modules)
   closes **nothing** unless `TECHNICAL_DC_STOP_TF/TARGET_TF` (default OFF) or the short-side WT-final label fires.
   Many "dead exit switch" zeros are honest. Turning that one switch on (or a `TECHNICAL_DC_*_TF`) wakes them all. [05 §0, 06 F1]
2. **The exits that really close at defaults:** `MULTI_TF_EXIT` (no master switch) > `DAYTRADE_TARGET dc_15m_high −0.10%`
   (not profit-gated, ignores MIN_HOLD) > `MTF_BB_REJECT` / `MTF_GR_WT_EXIT` (crypto compound) > `PPL_SL_CLOSE`
   (crypto: 50% off at +1.5%, stop near entry) > `ALL_TF_AGAINST_CLOSE` (no min-hold, no NOLOSS) > `ULTIMATE_DC_4h`
   (vetoed ~90% of bars by `bottom_exit_veto`) > `GAP_MOC_EXIT` (stocks) > crypto `live_exit_chain`. [05 §0, 06 F5]
3. **Reentry chasing is baseline behaviour.** A TARGET-DC exit sets `cd=0`; `HARDCODED_RALLY_REENTRY` (ON,
   `BYPASS_COOLDOWN=True`, `REQUIRE_WT=False`) re-enters on any bar past the last exit price; two of four anti-chase
   blocks compare against the same-bar channel and are inert. Stocks also run cooldown 0 / min-hold 2 bars because
   `WT_15M_BOUNCE_OPEN_ENABLED=True`. Reentry produces most trades fleet-wide (`ABLATION_DISABLE_REENTRY=True` removes ~99%). [05 §0, 08 §3]
4. **Most entry gates inside `compute_entry_signals` are bypassed** — trades come from HARDCODED_RALLY, block re-ORs
   (`B_WT_DC_LIVE`, `B_BBSQUEEZE`), simulate_one-level ORs. The gates that bind sit at the **open choke**:
   `_strict_open_block` (HTF direction, OI confirm, funding), `_ec_choke_block` (crypto: entry_vet, **RSI-T55**, stdev macro,
   WT-div), stocks GR consensus / DC4 block / counter-trend. Flipping a pre-bypass gate gives an honest 0. [06 F2–F3]
5. **2026-10-06 00:02 in-flight change (Mac, uncommitted at census time):** `vec_decisions/entry_vet_rsi_t55.py` ORed
   into the crypto open choke, no switch: LONG blocked when `rsi_D > RSI_ENTRY_LONG_TRADIER` (40), SHORT when
   `< RSI_ENTRY_SHORT_TRADIER` (58). Probe GALAUSDT_LONG 134 trades → 0. **First suspect for any crypto trade collapse
   after that time; results before/after are not comparable.** [05 §0.6, 06 F4]
6. **`haiku_augment` is the crypto gain AND DD amplifier** (GALA_LONG: off → gain +13.3 → +1.8, DD 5.1 → 0.9, same trades).
   Live augments are largely dead (crypto ablation True in config.py vs False in QuickConfig; stocks `BB4H_BREAKOUT_LADDER`
   elif swallows `evaluate_augment`) → **vec gain in trends is an upper bound**. [06 F6, 02 §4.4, 03 §4]
7. **Metric semantics:** `gain_pct = Σ realised pnl$ / peak concurrent notional` (BIBLE §53's mean_deployed text is wrong);
   `FINAL_MTM` (open position at the last bar, no exit fee) counts as a trade; DD = realised trades in exit order;
   validity: trades ≥10 (30D) / ≥30 (365D), TIM ≤80, DD ≤30, spike_frac ≤2%. There is **no lower TIM gate** in the
   evaluator — only the pilot's `_qualifies_30d` (TIM 20–80, gain > 0). [05 §6]
8. **"3m" is 15m in the vector engine.** `clamp_config` disables every `*_3M*/*_5M*_ENABLED` override and clamps 3m/5m
   strings to 15m; live exits needing 1m/3m data (PARABOLIC, STRUCTURAL_RANGE_SHIFT, DC4_3M, K1M, SATOSHIT 3m) are
   inert in vec. [04, 05 §10.11]
9. **Live-vs-vec structural gaps** (why a vec winner may not trade live): crypto `ABLATION_DISABLE_QUICK_EXIT/ENTRY=True`
   switches off most of `ez_positions_quick` live while `live_exit_chain` still models those exits in vec;
   `PARITY_DISABLE_NON_VECTORIZABLE=True` forces ~30 live-only switches False at import; `STRICT_VEC_PARITY_MODE`
   blocks live entries whose reason lacks a vec token; live crypto reentry daemon reads Redis keys nobody writes; the
   scalar `backtest_v12_engine` replaces `execute_now` with a harness (its gates never run). Stocks: NOLOSS hold +
   240-min min-hold + `HTF_TREND_VETO_ON_REDUCE` hold losers live that vec closes; disaster guard / NON_SHORTABLE /
   RED_ZONE / sector L/S have no vec twin → **treat vec SHORT results as an upper bound**. [01, 02, 03 §5, 04 §0]
10. **Every family call is wrapped in `try/except: pass`.** An exception, missing NPZ key or type mismatch silently
    no-ops a whole family. Call the family function directly to see the error before calling a switch "dead". [06 F9]

---

## 2. Where the numbers are (per sym_side)

| Need | Read it from | Notes |
|---|---|---|
| Final 30D gain | progress JSON `final_gain_fresh_vec` (else `cumulative_gain`), manifest `metrics.gain_pct` | filename gain is integer-truncated since ~2026-10-03 (`gain6p_t66` = 6%, 66 trades) |
| Final trades / TIM / DD / WR | **re-evaluate** `cumulative_overrides` (missing for 464/523 finished sheets) — or the new `diagnose_repair.after` / `DIAGNOSE_REPAIR` tab | `{SS}_BASELINE_METRICS` is the **start** set, not the final |
| B&H | progress `bh` (baseline eval), every eval's `bh_pct` (= `prepared["bh"]`) | side-adjusted |
| 365D | progress `final_365d`, `repair_365d`; `data/confirmed_365d.json` | 21/31 365D invalids fail on **DD**, not TIM |
| Exit/entry reason mix, holds | ledger: `evaluate_prepared_sanitized(prepared, ov, 30, include_ledger=True)` CLOSE rows (`exit_reason`, `entry_reason`, `bars_held`, `pnl_pct`) — or the new `DIAGNOSE_REPAIR` tab EXIT MIX | HTML charts: 42/97 disagree with the final set by >0.5pp — do not diagnose from charts |
| Per-row deltas | progress `done[{sheet}!{row}:{sw}={cand}]`, delta log `{progress_dir}/v15_delta_log/{SS}_*.jsonl` (has `tim`, never `dd`) | done rows store naked `trades` only |
| Filters on the final set | `FINAL_FILTER_RECHECK` tab / `progress.final_filter_recheck` | trades yes, TIM/DD no |
| This sym_side's lever map | **new**: `DIAGNOSE_REPAIR` tab "LEVER MAP" + `{progress_dir}/v15_diag_repair/{SS}.json` (`lever_map`: Δgain/Δtrades/ΔTIM/ΔDD of every candidate vs the final set) | the most direct "what moves this symbol" table |

Reason strings → code path → switches: chapter 05 §5 (engine) and chapter 02 Part E (live `process_position` order).

---

## 3. SYMPTOM → cause → levers (cross-venue playbook)

Thresholds (fleet-grounded, chapter 08 §4; implemented in `tools/v15_diagnose_repair.diagnose`):

| Fault | Rule | Fleet hits (523 finished) |
|---|---|---|
| TOO_FEW_TRADES | trades < 10 / 30D (hard floor) | 8 |
| FEW_TRADES | 10 ≤ trades < 30 | 82 (crypto short 32) |
| TOO_MANY_TRADES | trades ≥ cat p90 (CL 213 / CS 171 / SL 188 / SS 201) **and** gain/trade < 0.10pp, or > 300, or median hold < 4 bars with > 100 trades | 43 |
| LOW_EDGE | trades ≥ 30 and gain/trade < 0.05pp | 82 |
| TIM_HIGH / TIM_LOW | TIM > 80 / < 20 | final unknown for most; 65 **start** sets < 20 |
| DD_HIGH / DD_WARN | DD > 30 / > 15 | 365D: 19/31 fail on DD |
| GAIN_NEG | gain ≤ 0 | 29 |
| BELOW_BH_MATERIAL | gain < bh − 5pp | 48 (crypto long 34) |
| LOW_WR | WR < 35% with ≥ 10 trades | — |
| LOSING_EXIT:\<reason\> / LOSING_ENTRY:\<reason\> | one reason family ≥ 25% of closes with negative mean pnl | from ledger |

| Symptom | Look at (ledger) | Likely cause (vec) | First levers (direction) | Live differs? |
|---|---|---|---|---|
| **Too few trades** | few fresh OPENs, entry_reason mostly reentry | open-choke vetoes (RSI-T55 crypto, funding, OI, HTF direction, WT-div), STRENGTH score ≥5, CT velocity ≥9, HTF alignment, EMA50 15m, zone k_1h>80; stocks WT_DC stack (threshold 45), KG_STOCKS_LIVE_GATE (vec-only) | `HTF_DIRECTION_GATE_ENABLED=False`, `WT_DIV_ENTRY_GATE_ENABLED=False`, `STRENGTH_FILTER_ENABLED=False`/lower `STRENGTH_MIN_SCORE`, `CT_WT_VELOCITY_GATE_ENABLED=False`, `HTF_ALIGNMENT_ENABLED=False`, `EMA50_15M_ENTRY_FILTER_ENABLED=False`, lower `WT_DC_ENTRY_THRESHOLD`; audit QuickConfig↔config parity first (BIBLE §17.5) | live has MORE blockers (execute_now ~74 gates, leaderboard, MIN_GAIN early return) |
| **Too many trades / churn** | `bars_held` 1–3; `DAYTRADE_TARGET` followed 1–2 bars later by `HARDCODED_RALLY_REENTRY` | DAYTRADE target 15m (not profit-gated) + TARGET cd=0 + HARDCODED_RALLY cooldown bypass; `MULTI_TF_EXIT`; crypto MTF compound legs; stocks cooldown 0 | `TARGET_DC_IMMEDIATE_REENTRY_ENABLED=False`, `HARDCODED_RALLY_REENTRY_REQUIRE_WT=True` or `_BYPASS_COOLDOWN=False`, `DAYTRADE_DC_TARGET_TF=1h`, stocks `WT_15M_BOUNCE_OPEN_ENABLED=False`, `MTF_GR_EXIT_MIN_TFS`↑, `ALL_TF_AGAINST_CLOSE_MIN_TFS=5` | live dedupes queue actions (180 s) → vec over-counts |
| **TIM too high / hold-forever** | few closes, FINAL_MTM-dominated | no closing exit in a trend (exit_sig label-only), ULTIMATE_DC vetoed | `WT_LOWER_CROSS_EXIT_TF=1h/15m`, `TECHNICAL_DC_STOP_TF=1h`, `VEC_EXIT_SIG_IS_TRIGGER=True`, `DAYTRADE_DC_TARGET_TF=15m,1h`, `MI_EXIT_ENABLED=True`, `DELTA_MAX_HOLD_BARS`, `MTF_ATR_TRAIL_ENABLED=True` | stocks live holds losers longer (NOLOSS, 240-min, HTF veto on reduce) |
| **TIM too low / tiny wins** | many small + exits | DAYTRADE target 15m, PPL BE stop (crypto), MOMENTUM_TP, WT_CROSS_EXIT_1h, MULTI_TF_EXIT | `DAYTRADE_DC_TARGET_TF=1h/4h`, `PARTIAL_PROFIT_LOCK_ENABLED=False` or higher `_GAIN_PCT`, `MOMENTUM_TP_ENABLED=False`, `MIN_EXIT_GAIN_PCT>0` | live `WINNER_MOM_HOLD` (hard-coded) holds winners vec trims |
| **DD high** | large losers from `ULTIMATE_DC_4h_HARD_STOP`; augment-heavy positions | wide 20×4h stop, `bottom_exit_veto`, HAIKU pyramids, STDEV/DC_EDGE 3–5× sizing | `DAYTRADE_DC_STOP_TF=15m/1h`, `VIGILANCE_GUARD_ENABLED=True`, `NEWBORN_LOSS_KILL_ENABLED=True`, `BOTTOM_EXIT_HTF_WT_VETO_ENABLED=False`, `HAIKU_WINNER_ENABLED=False`, `DC_HARD_STOP_TF=4h` | live size amplifiers (BREAKOUT ladder 3×, index mult 2.5×) have no vec twin → $ DD larger live |
| **Gain negative** | LOSING_EXIT/LOSING_ENTRY families | wrong-trend entries, losing exit family | HTF/trend entry gates (TREND/HTF/EMA/ADX/REGIME/SMA200 filters True), disable or replace the losing exit family, `REENTRY_ENTRY_FILTER_ENABLED=True` | — |
| **Below B&H (trend)** | exits at every new 15m high then late reentry higher | DAYTRADE target, SELL_TOP (if sanctioned), PPL stop, MTF_BB_REJECT | slower `DAYTRADE_DC_TARGET_TF`, `MTF_BB_REJECT_EXIT_ENABLED=False`, keep `HLR_TOP_EXIT_LIVE_SANCTIONED=False` unless with §63 confirmation, `HLR_TOP_RECROSS_BYPASS_ENABLED=True` for SELL_TOP cases | vec augments in trends live doesn't (upper bound) |
| **Reentry chases** | re-buys above previous exit, small losers after winners | HARDCODED_RALLY (close > exit only), TARGET/SELL_TOP recross; stocks PRICE_CROSS_BACK / TIER2_FORCED | `HARDCODED_RALLY_REENTRY_REQUIRE_WT=True`, `REENTRY_ENTRY_FILTER_ENABLED=True` + `REENTRY_FILTER_MIN_PASS=2`, `REENTRY_APPLY_ENTRY_GATES_ENABLED=True`, `PRICE_CROSS_BACK_BAND_PCT`↓ | crypto live PRICE_CROSS_BACK has no vec twin |
| **Exactly 0 delta for a switch** | — | label-only exit; clamped `_3M/_5M`; pre-bypass entry gate; master forced off by `_expand_dependencies`; swallowed exception; WT_DC TF key-case bug (`wt1_1H`); candidate == effective baseline | BIBLE §18 protocol; call the family directly; check chapter 06 §8 (unreachable) | — |

Empirical cross-check (chapter 09, all sym_sides): adding trades on a finished set almost always costs gain (sets are
greedy-optimised) — pick adders by **gain cost per added trade** (least-cost adder tables in chapter 09). Robust
trade-adders: `WRONG_SIDE_WT_TFS_REQUIRED=2/3`, `E_3_USE_WT_STRUCTURE_EXIT_MODE=2.0`, `ALL_TF_AGAINST_CLOSE_MIN_TFS`↓,
`VIGILANCE_GUARD_ENABLED=True`, `REENTRY_TIER2_MAX_MINUTES`↓, `WT_LOWER_CROSS_EXIT_TF=15m`, `MI_EXIT_ENABLED=True`,
`HTF_DIRECTION_GATE_ENABLED=False`, `DAYTRADE_DC_TARGET_TF=15m,1h`, `WT_DIV_ENTRY_GATE_ENABLED=False`.

---

## 4. Lifecycle map — live function ↔ vectorized twin (summary; full entries in the chapters)

| Lifecycle | Crypto live (ez_manage / helpers) | Stocks live (tradier_manage) | Vectorized (v12 / vec_decisions) | Parity state |
|---|---|---|---|---|
| Fresh entry | signal router → `execute_trade_action` → `execute_now`; `check_entry_alignment` (needs 3/3 LTF — never passes at defaults), `check_entry_vetting` (RSI_D ≤40 long / ≥58 short) | WT_DC entry stack, `StockStrategy`, `evaluate_stop` pre-gates, disaster guard | `compute_entry_signals` (mostly bypassed) + block re-ORs + open choke (`_strict_open_block`, `_ec_choke_block`) | vec RSI-T55 now mirrors `check_entry_vetting` (crypto); many live gates no twin [01 §5–6, 03 §3, 06 §2–3] |
| Reentry | `evaluate_reentry` (PRICE_CROSS_BACK, HARDCODED_RALLY), reentry daemon (dead: Redis keys never written), queue consumer 1.5× | `evaluate_reentry` ladder (~2 h), `reentry_monitor_loop` (bypasses every order gate), gap morning rebuy | inline HARDCODED_RALLY, `HTF_WT_CHURN_REENTRY`, `reentry_pathways`, `stocks_reentry_sources`, TARGET/SELL_TOP recross | vec reentry > live crypto (daemon dead) [02 Part D, 03, 06 §5] |
| Exit | `process_position` first-fire-wins chain (order = exit-reason ownership); MTF compound, ALL_TF_AGAINST, GR exit, ULTIMATE_DC (vetoed), DAYTRADE DC, hard-coded profit exits | `evaluate_stop` (NOLOSS hold, 240-min min hold, HTF veto on reduce), DAYTRADE DC, EXIT_VELOCITY_WT (no switch, no twin), HYBRID_STRUCT, GAP_MOC | in-loop closers (DAYTRADE DC, MULTI_TF_EXIT, PPL, ALL_TF_AGAINST, ULTIMATE_DC, compound, live_exit_chain, GAP_MOC); exit_sig label-only | MULTI_TF_EXIT vec-only for live stocks; many live exits vec cannot show [02 E1/E2, 03 §4, 05 §3.4] |
| Augment | `HaikuOverseer.manage_winners` (+10% above +3%, no switch), high-gain augment (periodic_tasks crashes) — ablation True | `evaluate_augment` swallowed by BB4H elif | `haiku_augment`, `gain_ladder_augment`, lane augment-at-loss | vec augments ≫ live [02 Part F, 03, 06 §6] |
| Reduce | PPL, CYCLE_TP tiers, quick reduce (ablated), ratio rebalance (no working off switch) | CYCLE_TP via PROFIT_TARGET_PCT, R2 | `reduce_profit_lock.ppl_step`, `quick_reduce_strong` (SELL_TOP needs `HLR_TOP_EXIT_LIVE_SANCTIONED`) | PPL not WINNER_MOM-gated in vec [02 §4.4, 04, BIBLE §63] |
| Sizing | MAX_ORDER_VALUE 180 live vs 2500 QC, BREAKOUT_TF ×3–4, perf tier, index mult | heavy artillery ×2/×3, ladder ×1.5–3, VIX regime | STDEV/DC_EDGE multipliers, H1 reentry size mult | most live multipliers no twin (% metrics unaffected, $ DD is) [01 §4, 03 §5] |
| Config | `_psym_get` (full chain incl. cat_side) vs plain `getattr` in background loops (global only) | `_cfg`: `per_sym_store.db` FIRST, then hourly_reconfig/trb, books, full_recipe, cat_side, global; `_cfg_auto` side-key bug in execute paths | `QuickConfig` + `apply_tradier_defaults` + `apply_cat_side_defaults` + template bolds; 131 duplicate field names (last wins) | BIBLE §62 precedence text ≠ code [01 §3, 03 §0, 05 §2] |

---

## 5. DIAGNOSE + REPAIR phase in v15_pilot (2026-10-06)

`tools/v15_diagnose_repair.py` (pure logic, unit-tested in `tests/test_v15_diagnose_repair.py`), called from
`v15_pilot._spec_fill_workbook` → `_diagnose_repair()` right after `_final_filter_recheck()` — all cells are
calculated, the 30D prepared NPZ and the fork pool are still hot, nothing has been published yet. Downstream
(fresh-final, compliance, gates, filename, manifest, 365D gate) automatically sees the repaired set.

Why: the sequential greedy fill only keeps positive steps, one row at a time. It can never pass through a temporary
loss — so it cannot loosen a filter that starves a later switch of entries, and on a trending window it never keeps
the exits that 365D needs (BIBLE §58). The phase searches non-sequentially:

| Step | What | Objective |
|---|---|---|
| 0 DIAGNOSE | fresh eval + ledger of the final set → faults (§3 thresholds), exit/entry reason mix; **screen 0** = every evaluable template row (white + orange) + every yellow `filter=opt`, applied singly to the final set → per-sym_side **lever map** (Δgain/Δtrades/ΔTIM/ΔDD) + liveness (how many ENTRY/REENTRY/AUGMENT candidates move the ledger) | measure |
| 1 SOFTEN | one change per round: the candidate (filter/gate, or ENTRY/REENTRY/AUGMENT tab) that adds the most trades; stops at trades ≥ max(60, 3×base) (cap 300), no adder left, or 45% of budget; gain may drop on purpose; DD capped at max(45, current); TIM may rise | trades + liveness |
| 2 ADD | beam (width 3) over all candidates + reverts | quality key |
| 3 TIGHTEN | beam over filters/gates + reverts of every change | quality key |
| 4 POLISH | hill-climb over everything | quality key |
| 5 VERIFY | top-3 distinct compliant finalists + the original set on 365D (`evaluate_sanitized_with_timeout`, same path as the DONE gate) | (30D compliant, 365D qualified, adj. gain, fewer changes) |

Quality key = (30D compliant [valid, ≥10 trades, TIM 20–80, gain > 0], valid, −TIM/DD excess, floor gap,
gain + 0.1·max(0, gain − B&H) − 0.05·max(0, 30 − trades), −#changes). **B&H is a stretch bonus, never a gate** (§65).
Acceptance: the winner replaces the final set only if it beats the original on (30D compliant, 365D qualified) status,
or on 30D gain by ≥ 0.5pp without losing status; then a fresh real eval of exactly that set must be compliant.

Safety (never applied, always measured): promotion-blocked switches (DEAD_VEC / LIVE_ONLY / LIVE_DEAD_KEY /
SIZING_FALSE_ALPHA), any `ABLATION_DISABLE_*=True` (§64 P0), bool safety gates flipped True→False (tokens
GUARD/BLOCK/HARD/STOP/KILL/HEDGE/STDEV_REJECT/RISK/NOLOSS — §58 final_safe), `NEVER_APPLY` fabrication-class switches
(`SIMPLE_PRICE_GT0_ENABLED`). Every eval goes through the pilot's `_get` (one delta-log line each, sheet
`DIAGNOSE_REPAIR`, keeps the cell sentinel alive) and `_EVAL_CACHE`.

Outputs: workbook tab `DIAGNOSE_REPAIR` (metrics before/after, liveness, faults before/after, exit mix, every step,
finalists 30D+365D, GAPS, LEVER MAP); full report `{progress_dir}/v15_diag_repair/{SS}.json`; compact
`progress["diagnose_repair"]`; on adoption, column C rewritten via `_set_override` so C = published set
(`c_unplaced` lists composite keys without a row). **GAPS** = remaining faults where no existing switch/filter moves
the metric the right way (`MISSING_LEVER`) or only at a cost (`LEVER_EXISTS_BUT_COSTLY`) — this is the worklist for
new switches/filters.

Env: `V15_DIAG_REPAIR=0` off · `V15_DIAG_REPAIR_MODE=publish|report` (report = measure + tab, set unchanged) ·
`V15_DIAG_REPAIR_S` budget (default 900 s, clipped to keep 15 min inside the herd's 90-min hardcap) · skipped under
`--fast-switches` and when the same final set was already repaired (resume).

Known trade-off: on adoption the published set differs from the set the sequential rows measured. The
`DIAGNOSE_REPAIR` tab holds the measurements of every applied change; the 2026-10-03 row/set-coherence law
(`repair_needs_redo`, used by COMPLIANCE) instead re-fills the sheet. Use `V15_DIAG_REPAIR_MODE=report` to keep strict
coherence.

---

## 6. How an agent diagnoses a sym_side (procedure)

1. Open the newest workbook / progress JSON for the sym_side. If it has a `DIAGNOSE_REPAIR` tab, start there: faults
   before/after, finalists' 365D, GAPS, lever map.
2. Otherwise re-evaluate the final set with `include_ledger=True` (BIBLE §45 cheatsheet) — never diagnose from the
   start-set BASELINE_METRICS or the charts (§2).
3. Classify faults with §3 thresholds; read the exit/entry reason mix against chapter 05 §5 (reason → code path).
4. Check §1 facts first (RSI-T55 era, label-only exits, augment amplifier, choke vs pre-bypass gates).
5. Pick levers from §3, then confirm on this symbol with the lever map (or single evals). Remember the §1.9 live gaps
   before promoting anything that only vec can do.
6. Zeros: BIBLE §18 order (parity → cand == effective baseline → binding → genuine gap), never "unwired" by default.

---

## 7. Open items surfaced by the census (not fixed — locked files / user-gated)

- RSI-T55 crypto open veto added without a switch (engine, 2026-10-06 00:02) — comparability break.
- WT_DC TF-expanded gates read uppercase keys (`wt1_1H`) that do not exist → inert except 'D'. [05 §10.2]
- `_ty_fire` (MTF_DC_REJECT / BB_FROZEN_STOP yellow exits) computed, never closes. [05 §10.3]
- `ROUND_TRIP_COST_PCT` (0.05) ≠ what simulate_one charges (crypto 0.04, stocks 0). [05 §10.12]
- Crypto: `periodic_tasks` UnboundLocalError and `crypto_fh_momentum_loop` TypeError (code-read). [02 §4.2]
- Stocks: `reentry_monitor_loop` bypasses execute_now; `_cfg_auto` side-key bug; BB4H elif swallows augments. [03 §6]
- `ULTIMATE_DC` per-sym `DC_HARD_STOP_TF` ignored live; `UNIVERSAL_NOLOSS_GATE` off at defaults. [02]
- SWITCH_BIBLE counts ~35 switches as wired whose only vec reads are in never-called modules. [06 §8–9]
