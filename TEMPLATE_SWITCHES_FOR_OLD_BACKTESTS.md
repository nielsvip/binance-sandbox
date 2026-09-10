# TEMPLATE Switches → Old Backtests: Categorized Port List

**Date:** 2026-09-09 · **Sources:** `SPREADSHEETS/TEMPLATE.xlsx` (26 sheets), `TEMPLATE_audit_final_report.md`, live `data/hourly_reconfig/*/active_config.json`, `per_sym_*` engines on disk.

**Thesis:** Stop chasing the TEMPLATE/v12 wiring treadmill (198/357 switches not even `getattr`-wired, 335 novel switches vs old winners, only 22 overlap). The old `per_sym` engines already produced verified gains with ~185 knobs. Cherry-pick TEMPLATE ideas **by category** into the old engines instead of fixing all 198 wirings for a never-true number.

---

## Part A — Scripts That Actually Calculated per_sym Settings (many cryptos & stocks)

These are the **working** engines — they swept real NPZ, used `metrics_guard` honest sharpe, and promoted winners to `data/hourly_reconfig/*/active_config.json`. Use them as the port target.

| Script | Scope | What it swept | Output | Verified? |
|---|---|---|---|---|
| `per_sym_crypto_profiler.py` | 245 cryptos (USDT/USDC) | BB_LEN/STD, WT_CHAN/AVG, DC_PERIOD per TF, ENTRY_MODE, BB_LONG/SHORT thresholds, MIN_HOLD, COOLDOWN, WT_CROSS_LB, BB_EXTREME/SQUEEZE — marginal sweep + TPD 5-15 gate | `data/hourly_reconfig/per_sym_active_config.json` (245 symsides) + `plots/*.png` | Yes |
| `per_sym_crypto_profiles.py` | All crypto NPZ minus FLZ8 | Full mutation grid (std crypto params, no BTC_DEDICATED) | same `per_sym_active_config.json` | Yes |
| `per_sym_vec_engine_crypto.py` | Variant-axis vectorized | 10 high-impact knobs, chunked 10k variants, numpy-broadcast entry/exit masks + `sweep_variants(sym, 1M variants)` | arrays → caller writes CSV via `metrics_guard` | Yes (v1: 10 knobs, out-of-scope: RZ cascade/wt_dc hierarchy/pyramid) |
| `per_sym_engine_crypto.py` | Scalar engine reused by stocks | `SymParams` dataclass + `simulate()`/`simulate_dual()` + `walk_trades` | same | Yes — stocks layer imports it |
| `per_sym_tradier_profiles.py` | Stocks (TRB, `symbols_trb_long/short.json`) | LONG_ONLY/SHORT_ONLY/BOTH + WA_MIN_GAIN_PCT, MIN_HOLD_BARS, ENTRY_SCORE_THRESHOLD, WT_EXIT_MIN_TFS, COOLDOWN_BARS, D_TREND_REQUIRED | `_candidates/{sym}_{side}_winner.json` → `tradier_hourly_reconfig.py` hourly | Yes |
| `per_sym_engine_stocks.py` | Stocks — thin wrapper over crypto engine | Same infra, stocks deltas: 5m LTF, 0.04% RT commission, DECISION_TFS=(15m,1h,4h,D), TF_BARS_5M, BARS_PER_DAY=78 (RTH) | same | Yes |
| `per_sym_vec_engine_stocks.py` | Vectorized stocks | Same as crypto vectorized, stocks-specific | same | Yes |
| `per_sym_20d_agent_stocks.py` / `per_sym_7d_agent.py` | Lifecycle pilots | Windowed pilots that fed the above | `data/hourly_reconfig/trb/active_config.json` 160 symsides, `trc` 199, `inf` 101 | Yes |
| `per_sym_*_profiles.py` (ang/fin/men/flz8) | Account-sliced | Same as `per_sym_crypto_profiles` per account | partitioned `per_sym_active_config` | Yes |
| `backtest_scorer_5cond.py` | Single-scorer audit | `wt_dc_exit_scorer.score_exit()` 5-cond strict gate vs 2-cond, via `score_exit()` on NPZ | `data/scorer_5cond_bt/` | Yes (indicator audit) |

**Promoted winners today (evidence it still works):**

- `data/hourly_reconfig/trb/active_config.json` — **160 symsides** (top: AAPL_LONG 14.77% / 211 trades)
- `data/hourly_reconfig/trc/active_config.json` — **199 symsides** (top: AGI_LONG 296%, AU_LONG 288%)
- `data/hourly_reconfig/inf/active_config.json` — **101 symsides** (top: ACEUSDT_LONG 151%)
- `data/hourly_reconfig/per_sym_active_config.json` — **245 symsides** (crypto universe, includes `_meta`)

Old freq leaders (what actually won): `REENTRY_MANDATORY` (87), `WIN_TRAIL_EROSION_PCT` (56), `REENTRY_TIER1_SIZE_MULT_TRADIER`/`TRADIER_DC_*`/`COOLDOWN_BARS`/`ENTRY_SCORE_THRESHOLD`/`GOLDEN_RULE_*` (35 each), `BOUNCE_AUGMENT_K_D_THRESHOLD`/`WT_EXIT_MIN_TFS` (27-31). These are the signal; TEMPLATE's 335 novel switches are mostly untested FILTER_TF gates.

**How to add a new switch to an old engine (one pattern):**

```python
# 1) Add field to SymParams (per_sym_engine_crypto.py) or QuickConfig if using v8_quick_engine
# 2) Thread it through _build_signals() / simulate() as a bool or threshold gate
# 3) Add its VALUES list to per_sym_crypto_profiler.py (e.g. WT_15M_BOUNCE_VALUES = [True, False])
# 4) Marginal sweep: test it alone vs current best, trade-rate check 5-15 tpd, promote if pool_sharpe + dd + tpd pass
# 5) All sharpe writes still via metrics_guard.validate_and_format_sharpe() — NO annualization
```

---

## Part B — TEMPLATE.xlsx Full Inventory

**File:** `SPREADSHEETS/TEMPLATE.xlsx` — 26 sheets (1 INSTRUCTIONS + 1 FILTER_DICTIONARY_V8 + 20 switch sheets + Results_30d_Deltas/TEMPLATE_BASELINE_METRICS/_BLANKET_INVENTORY/12SYM_PARITY).

| Sheet | Rows | Unique switches | Value shape |
|---|---|---|---|
| ENTRY_PULLBACK_BOUNCE | 315 | 117 | bool/numeric/TF sweeps per row |
| ENTRY_BREAKOUT | 324 | 120 | same |
| ENTRY_NEUTRAL | 381 | 148 | same |
| ENTRY_FULL_FILTERED | 941 | 267 | global gate dumping ground (806 GLOBAL_CHECK rows) |
| EXIT_PULLBACK_BOUNCE | 301 | 111 | same |
| EXIT_BREAKOUT | 6 | 3 | tiny (LR ladder + stdev fail) |
| EXIT_NEUTRAL | 441 | 179 | largest exit sheet |
| EXIT_FULL_FILTERED | 146 | 71 | bool exits |
| REENTRY_PULLBACK_BOUNCE | 12 | 6 | BOUNCE_* only |
| REENTRY_BREAKOUT | 4 | 2 | LEASH only |
| REENTRY_NEUTRAL | 126 | 62 | GUARANTEED_* heavy |
| REENTRY_FULL_FILTERED | 142 | 70 | same + LEASH variants |
| AUGMENT_PULLBACK_BOUNCE | 319 | 114 | AUGMENT_* + filters |
| AUGMENT_BREAKOUT | 28 | 8 | FALLBACK/AUGMENT_MIN only |
| AUGMENT_NEUTRAL | 341 | 125 | largest augment |
| AUGMENT_FULL_FILTERED | 52 | 20 | stripped |
| REDUCE_PULLBACK_BOUNCE | 4 | 2 | 2 switches |
| REDUCE_BREAKOUT | 4 | 2 | 2 switches |
| REDUCE_NEUTRAL | 318 | 119 | large |
| REDUCE_FULL_FILTERED | 24 | 12 | small |
| **20 sheets total** | **4,229** | **357 distinct** | ~2-5 value options per switch |
| FILTER_DICTIONARY_V8 | 427 | 119 distinct filters | OFF/D/4h/1h/15m TF sweeps or threshold ladders |

**FILTER_DICTIONARY_V8 (119 filters, 427 rows):**

| Live location | Count | Meaning |
|---|---|---|
| GLOBAL_CHECK | 238 | generic AND-mask gates (80% mask in v12) |
| ENTRY, GLOBAL_CHECK, STOCKS_EXIT | 77 | entry+exit |
| GLOBAL_CHECK, STOCKS_EXIT | 49 | exit-leaning |
| ENTRY_PULLBACK_BOUNCE | 12 | pullback-specific |
| UNIVERSAL (ex-BTC) — ENTRY/EXIT/REENTRY | 11 | universal |
| AUGMENT/EXIT/REDUCE combos | 10-16 | position-management |
| DEAD | 15 | known dead code |

Each filter row is `Filter | Option Value | Semantics | Live location | Sheets applicable | Switches it gates | Recommendation`. 50+ are `*_FILTER_TF` TF selectors (OFF/D/4h/1h/15m) that gate a single switch's `getattr(config,"*_FILTER_TF")` check in `_breakout_15m_vol_ok` etc.

---

## Part C — Wireability & Overlap Audit (why not to fix v12)

| Metric | Value |
|---|---|
| TEMPLATE distinct NAMEs | 357 |
| TEMPLATE rows | 4,229 (prior audit counted 1,309 on older 23-sheet TEMPLATE — now 26 sheets) |
| Distinct in old winners (`trb`+`per_sym_active` combined) | 185 |
| Overlap TEMPLATE ∩ old winners | **22** |
| Novel in TEMPLATE (not in any old winner) | **335** |
| In old winners but NOT in TEMPLATE | **163** |
| Wired in `v12_quick_engine.py` (`getattr(cfg,"NAME")`) | 1,043 unique |
| TEMPLATE switches **NOT wired** anywhere in v12 | **198 / 357 (55%)** |
| Of not-wired, `*_FILTER_TF` gates | 30 |
| Hooked at scale (prior TEMPLATE_audit_final_report) | 45/351 distinct had been hooked at time of audit; current file larger, gap bigger |

Conclusion: the v12/TEMPLATE system needs ~198 new `QuickConfig` fields + ~198 new `getattr` wirings + matching `vec_decisions/*.py` shims + live parity — for switches where most have zero backtested edge. The old engines are 1-file changes per switch.

---

## Part D — Categorized Port List: What to Add to Old Backtests Instead

Add by **functional category** into `per_sym_engine_crypto.py` / `per_sym_crypto_profiler.py` (crypto) and `per_sym_engine_stocks.py` (stocks). Each row = one switch; `Values` = what TEMPLATE tests (port as sweep grid); `Effort` = Lo/Med/Hi.

### A. MTF Filter Gates — 53 `*_FILTER_TF` selectors (LOW signal individually, batch as one gate)

All have values `OFF / D / 4h / 1h / 15m` (bypass vs require HTF confirmation). Port as a single `FILTER_TF` vector, not 53 separate knobs.

| Filter | Sheets | Live location | Port? |
|---|---|---|---|
| `ATR_TRAIL_FILTER_TF`, `BAR_PATTERNS_FILTER_TF`, `BB_PULLBACK_GATE_FILTER_TF`, `BB_RECOVERY_ENTRY_FILTER_TF`, `BB_RECOVERY_FILTER_TF`, `BREAKEVEN_GAIN_EROSION_FILTER_TF`, `BREAKOUT_RETEST_FILTER_TF`, `BTC_DEDICATED_FILTER_TF`, `BT_WT_CROSS_LADDER_FILTER_TF`, `CANDLE_PATTERN_STOPS_FILTER_TF`, `CIRCUIT_SHARPE_GATES_FILTER_TF`, `COOLDOWN_LOCKS_FILTER_TF`, `DC_BREACH_REDUCE_FILTER_TF`, `DC_BREAK_FILTER_TF`, `DC_MOMENTUM_BOTA_SCORER_FILTER_TF`, `DELTA_ENGINE_FILTER_TF`, `DUP_GUARD_FILTER_TF`, `E2E_REPLAY_VALIDATOR_FILTER_TF`, `EMA_9_21_FILTER_FILTER_TF`, `EMA_BLANKET_FILTER_FILTER_TF`, `EMERGENCY_BRAKE_FILTER_TF`, `EXHAUSTION_EXIT_FILTER_TF`, `EXIT_R1_R2_FILTER_TF`, `EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF`, `EXIT_TOP_FADE_FILTER_TF`, `EXIT_TO_REDUCE_ADAPTER_FILTER_TF`, `FAST_RISER_FILTER_TF`, `FH_MOMENTUM_FILTER_TF`, `FIRST_OPEN_THROTTLE_FILTER_TF`, `FROZEN_STOP_FILTER_TF`, `FUNDING_GATE_FILTER_TF`, `GOLDEN_RULE_ENFORCE_FILTER_TF`, `GOLDEN_RULE_HTF_VOTE_FILTER_TF`, `GR_FILTER_VEC_FILTER_TF`, `GR_V5_STATE_FILTER_TF`, `HAIKU_WINNER_FILTER_TF`, `KILLER_KNOB_FINDER_FILTER_TF`, `LIVE_ENTRY_ENGINE_FILTER_TF`, `LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF`, `MOM3_FILTER_TF`, `MOMENTUM_BREAKOUT_FILTER_TF`, `MTF_ARMED_ENTRIES_FILTER_TF`, `MTF_ATR_TRAIL_FILTER_TF`, `MTF_DC_REJECT_FILTER_TF`, `NEWBORN_LOSS_KILL_FILTER_TF`, `NEWBORN_PROTECT_FILTER_TF`, `NOLOSS_BYPASS_WT5OF5_FILTER_TF`, `OPEN_INTENT_SIZE_GATES_FILTER_TF`, `PARTIAL_PROFIT_LOCK_V2_FILTER_TF`, `PEAK_GIVEBACK_BE_EROSION_FILTER_TF`, `HA_WICK_QUALITY_TF` | 9 sheets avg | GLOBAL_CHECK | **Batch port** — one shared MTF gate vector; do not add 53 knobs individually |

**Recommendation:** Do not port individually. Old engines already have `MIN_TFS_AGREE` + `REQUIRE_D/W_TREND`. If you want TEMPLATE's MTF idea, add **one** shared `MTF_FILTER_TF` (OFF/15m/1h/4h/D) that gates the whole entry scorer — not 53.

---

### B. Bollinger / Squeeze / Pullback — 7 switches (HIGH signal — port first)

| Switch | Values in TEMPLATE | Sheets | Effort |
|---|---|---|---|
| `BB_PULLBACK_GATE_TF` | D, 1h, 4h, 15m | ENTRY_PULLBACK_BOUNCE | Lo |
| `BB_BREAKOUT_SCORE` | 20, 40 | ENTRY_BREAKOUT, ENTRY_FULL_FILTERED | Lo |
| `BB_SQUEEZE_ENTRY_ENABLED` | False, True | ENTRY_PULLBACK_BOUNCE, ENTRY_FULL_FILTERED | Lo |
| `BB_SQUEEZE_EXIT_ENABLED` | False, True | EXIT_NEUTRAL, EXIT_FULL_FILTERED | Lo |
| `BB_SQUEEZE_WIDTH_PERCENTILE` | 0.1, 0.15, 0.2, 0.25 | ENTRY_PULLBACK_BOUNCE, ENTRY_FULL_FILTERED | Lo |
| `DELTA_GATE_BB_SQUEEZE` | False, True | ENTRY_PULLBACK_BOUNCE, ENTRY_FULL_FILTERED | Lo |
| `RZ_BOT_BB_THRESHOLD` | 0.375 | ENTRY_FULL_FILTERED | Lo |

**Port order:** SQUEEZE trio (entry/exit/width) → BB_PULLBACK_GATE → DELTA_GATE_BB_SQUEEZE. These were top ideas in `per_sym_crypto_profiler` BB_LEN/STD sweeps.

### C. WaveTrend (WT) — 50 switches (HIGH signal — port next)

Core WT gates; list truncated to highest-signal. All bool unless noted.

| Switch | Values | Sheets | Notes |
|---|---|---|---|
| `WT_15M_BOUNCE_OPEN_ENABLED` + `WT_15M_BOUNCE_*` (BB_MIN/MAX, HIGH/LOW_GT_PREV, REL_VOL_GT_1) | True/False, 0.05-0.95 | ENTRY_PULLBACK_BOUNCE | TEMPLATE says this is the single most broken gate (`v12:5894 MAX_BARS_AGO=100, cooldown 0`) — **port first** |
| `AUGMENT_WT_4H_BOUNCE_ENABLED` | True/False | ENTRY_FULL_FILTERED | augment-side WT 4h |
| `MANDATORY_REENTRY_WT_FILTER_*` (5 variants: MIN_TFS 1/2/3, MIN_VELOCITY, REQUIRE_FLIP, TF_MODE, VELOCITY_RATIO) | mixed | all 9 aug/entry/exit sheets | reentry WT velocity gate |
| `WT_DC_HTF_GATE`, `WT_PERCENTILE_ENTRY_GATE_ENABLED`, `WT_CROSS_EXIT_*`, `WT_REDUCE_FRAC_*` | mixed | EXIT/REDUCE/AUGMENT | WT↔DC coupling |
| `BTC_RZ_WT_DC_MULTIFACTOR`, `BTC_TECH_EXIT_WT_MIN_TFS` | True/False | ENTRY_NEUTRAL/FULL | crypto WT multifactor |
| `DAEMON_REENTRY_SHORT_WT_XUNDER_GATE_ENABLED`, `E_1_WT_EXIT_USE_DELTA_ENABLED`, `E_3_USE_WT_STRUCTURE_EXIT_MODE` | True/False | REENTRY/EXIT | short/structural WT |

Full 50 count in inventory; port WT_15M_BOUNCE cluster first, then DC-coupled WT, then daemon/structural WT.

### D. Donchian Channel (DC) — 13 switches (HIGH)

| Switch | Values | Sheets |
|---|---|---|
| `DC_BREAKOUT_SCORE` | 15, True/False | ENTRY_BREAKOUT/FULL |
| `DC_BREAKOUT_TF` | OFF/D/4h/1h/15m | same |
| `DC_HOPELESS_EXIT_ENABLED` / `DC_HOPELESS_EXIT_MIN_AGE_S` | True/False / 0/0.5/1/2 | EXIT_NEUTRAL/FULL, ENTRY_FULL |
| `DC_MOMENT_STRONG_THRESHOLD` | 40/48 | all 9 sheets |
| `DELTA_EXIT_DC_FLOOR`, `EXIT_SCORER_DC_EXTREME`, `BREAKEVEN_DC_FIELD_MODE` | mixed | ENTRY_FULL/EXIT |
| `DC_WIDTH_CAP_MULT` etc. (remaining 5) | — | — |

### E. Reentry / Bounce / Guaranteed — 76 switches (MED-HIGH — large but worthwhile)

| Subgroup | Switches | Values |
|---|---|---|
| BOUNCE | `BOUNCE_REENTRY_ENABLED`, `BOUNCE_REENTRY_K_RESET_LONG/SHORT`, `BOUNCE_REENTRY_K_RESET*` | True/False, 0/0.5/1/2 |
| BREAKOUT LEASH | `BREAKOUT_LEASH_REENTRY_MULT` | 0.75/1.125/1.5/1.875 |
| GUARANTEED (6) | `GUARANTEED_REENTRY_DELTA_GATE_ENABLED, _K_FAVORABLE_HIGH/LOW, _K_HIGH/LOW_BLOCK, _STRICT_CONFIRMATION` | True/False |
| BTC GUARANTEED | `BTC_GUARANTEED_REENTRY_ENABLED/MAX_AGE_BARS/MIN_GAP_BARS` | True/False, 480, 5 |
| DAEMON/CHANNEL | `CHANNEL_REENTRY_STOP_ENABLED`, `DAEMON_REENTRY_*`, `DIRECTION_FAVORABLE_REENTRY_ENABLED`, `REENTRY2_*`, `HLR_REENTRY_MULT_*` (4), etc. | True/False |
| Other | `REENTRY_TIER*`, `VEC_REENTRY_DC4_EXITPRICE_ENABLED`, `LEGACY_REENTRY_*`, `REENTRY_B16_SMA200_PULLBACK_ENABLED`, etc. | — |

**Port priority:** GUARANTEED 6 + BOUNCE 3 + LEASH — these are the old winners' `REENTRY_MANDATORY`/`REENTRY_TIER1` cousins.

### F. Exit / Stop / Hold / Erosion — 60 switches (MED-HIGH)

| Switch | Values | Sheets |
|---|---|---|
| `MIN_HOLD_BARS_BEFORE_EXIT` / `MIN_HOLD_MINUTES_TRADIER` | already in old winners | — (already ported) |
| `BREAKEVEN_GAIN_EROSION_ENABLED/MIN_GAIN/REQUIRE_PROFIT` | True/False, 0/0.5/1/2 | EXIT_NEUTRAL/FULL, ENTRY_FULL |
| `BREAKEVEN_DC_FIELD_MODE` | DC4/0/0.5/1 | same |
| `DC_HOPELESS_EXIT_*` | see D | — |
| `CRYPTO_SPIKE_FADE_THRESHOLD_PCT` | 10/12 | all 9 sheets |
| `ATR_ADAPTIVE_STOP_MULT`, `ENTRY_SCORE_THRESHOLD` | 2, 18 | ENTRY_FULL |
| `ADX_RANGING_THRESHOLD` | 10/15/20/25 | all 9 sheets |
| `STOCH_CROSS_EXIT_*`, `WT_EXIT_*`, `TREND_EXIT_*`, etc. | — | EXIT/REDUCE |

Many of these GATE exits — test one exit family at a time (BREAKEVEN → HOPELESS → SPIKE_FADE → RANGING).

### G. Augment / Pyramid / Sizing — 18 switches (MED)

| Switch | Values | Sheets |
|---|---|---|
| `AUGMENT_AT_LOSS_ENABLED`, `AUGMENT_ONLY_WHEN_PROFITABLE` | True/False | AUGMENT_FULL/NEUTRAL |
| `AUGMENT_BOUNCE_MIN_GAIN_PCT` | 0/0.5/1/1.5 | AUGMENT_* (4 sheets) |
| `AUGMENT_BREAKOUT_MIN_GAIN_PCT` | 1/1.5/2/3 | same |
| `AUGMENT_FALLBACK_GAIN_PCT` / `AUGMENT_FALLBACK_REDUCE_*` | 0.5/1/1.5/2 / True/False / 0.25/0.5/1 | AUGMENT_*, REDUCE_* |
| `AUGMENT_MIN_GAIN_PCT` | 0.5/1/1.5/2/3/5 | AUGMENT_BREAKOUT/PULLBACK |
| `AUGMENTED_POSITIONS_GUARD_FLOOR_MULT`, `DELTA_PYRAMID_*`, `DYNAMIC_SCORE_AUGMENT_ENABLED`, `MAX_AUGMENTS_PER_POSITION` | 0/0.5/1/2 / True/False | AUGMENT_FULL/NEUTRAL |
| `ATR_ADAPTIVE_SIZING_TARGET_PCT/STOP_MULT` | 1.5 / 2 | ENTRY_FULL |

### H. Reduce / Profit-Lock / Giveback — 3 switches (MED — small but causal)

| Switch | Values | Sheets |
|---|---|---|
| `PARTIAL_PROFIT_LOCK_FRAC` | 0/0.5/1/2 | ENTRY_FULL, REDUCE_NEUTRAL/FULL |
| `PEAK_GIVEBACK_DROP_TRIGGER_ENABLED` | True/False | REDUCE_NEUTRAL/FULL |
| `QUICK_REDUCE_TECHNICAL_ONLY` | True/False | REDUCE_NEUTRAL/FULL |
| (+ `AUGMENT_FALLBACK_REDUCE_*` counted in G, `BOUNCE_AUGMENT_MIN_LOSS_PCT` etc. in K) | — | — |

### I. HTF / Golden Rule / GR / HLR / MTS — 23 switches (MED)

| Switch | Values |
|---|---|
| `ALL_TF_AGAINST_CLOSE_MIN_TFS/COOLDOWN_SEC/ENABLED` | OFF/D/4h/1h/15m etc. |
| `GOLDEN_RULE_BASE_USD`, `GR_FILTER_ALL_ENTRIES/VEC_ENABLED/MIN_TFS` | 0/5/6 etc., True/False |
| `EMA_9_21_FILTER_MIN_TFS`, `EMA_BLANKET_FILTER_MIN_TFS/ENABLED` | 0/False/True |
| `HLR_REENTRY_*`, `MTS_GATE_*`, `GR_V5_STATE_*`, `DELTA_HTF_GATE` | True/False, 4h etc. |
| `HTF_*`, `HLR_*` remaining | — |

Old winners already heavily use `GOLDEN_RULE_*`/`WT_DC_HTF_GATE` — TEMPLATE's extra GR knobs are fine-tuning.

### J. Other Indicators — 9 switches (LO-MED)

| Switch | Values | Sheets |
|---|---|---|
| `ATR_LONG_WINDOW` | 100/150 | ENTRY_FULL |
| `ATR_TRAIL_SWEEP_ENABLED` | True/False | EXIT_NEUTRAL/FULL |
| `EMA_BLANKET_FILTER_ENABLED` | False/True | all 9 |
| `HA_WICK_QUALITY_ENABLED/SCORE/TF` | False/True, 15/16, OFF/D/4h/1h/15m | all 9 |
| `LR_BAND_LADDER_STOCH_EXTREME/TF_*` | 30/36, 0/1 | all 9 |
| `STOCH_CROSS_ENTRY_TRADIER` | True | ENTRY_FULL |

### K. Other / Structural / Guard — 45 switches (LO-MED — guardrails, not alpha)

| Switch | Values | Note |
|---|---|---|
| `BANDAID_OFF_LOSER_RECOVER_PCT` | 0 / -0.25 / -0.3 | loser recovery |
| `BAND_ARROW_ENABLED/SLOPE_DEADBAND` | True/False | arrow slope |
| `BTC_*` (ACCEL_RAMP, HARD_BLOCK, FIB, RZ_*, RESTRICTED_DC, etc.) | mixed | 163 old-only BTC switches already cover this better |
| `BTC_BREAKOUT_ENTRY_ENABLED`, `BTC_DIVERGENCE_EXIT_AGAINST` | True/False | breakout/divergence |
| `STDEV_BREAKOUT_*`, `LR_BAND_LADDER_*`, `INTRADAY_SESSION_FORCE_EXIT_UTC` | mixed | stdev/LR/session |
| remaining 37 | — | structural guards — port last |

---

## Part E — FILTER_DICTIONARY (119 filters) — What They Gate

Each filter row gates a specific scorer path (e.g. `ATR_TRAIL_FILTER_TF` gates `_breakout_15m_vol_ok`). Adding the switch without its filter is half the idea. For old backtests, either:

- **(A) Ignore filters** — test switch alone (simplest, matches old marginal-sweep style), or
- **(B) Pair switch+filter** — test `switch=True` with `filter=4h` vs `OFF` (more faithful, 2D grid).

| Filter pattern | Count | Sheets applicable | How to port |
|---|---|---|---|
| `*_FILTER_TF` | 53 | GLOBAL_CHECK / ENTRY / STOCKS_EXIT | One shared `FILTER_TF` (Section A) |
| Threshold ladders (`ADX_RANGING 10/15/20/25`, `CRYPTO_SPIKE 5/7.5/10/12.5`, `FG_FEAR 12.5-31.25`, etc.) | ~12 | ENTRY/GLOBAL_CHECK | Already-style sweep grid — add VALUES to profiler |
| Bool gates (`GR_FILTER_VEC_ENABLED`, `MARKET_QUALITY_SCORE_ENABLED`, `OI_CONFIRM_ENABLED`, etc.) | ~35 | mixed | True/False toggle |
| MTF min-count (`EMA_9_21_FILTER_MIN_TFS 0/1/2`, `HLR_TOP_MIN_TFS`, `GR_FILTER_VEC_MIN_TFS`, `LR_BAND_LADDER_TF_*`) | ~8 | GLOBAL_CHECK | 0/1/2 ladder |
| Other (`DELTA_HTF_GATE 4h`, `HA_WICK_QUALITY_SCORE 15`, `BANDAID_OFF_LOSER_RECOVER_PCT 0/0.5/1/2`) | ~11 | mixed | numeric ladder |

**Full 119 list (for grepping into config):**

`ADX_RANGING_THRESHOLD, ATR_TRAIL_FILTER_TF, BANDAID_OFF_LOSER_RECOVER_PCT, BAND_ARROW_ENABLED, BAR_PATTERNS_FILTER_TF, BB_PULLBACK_GATE_FILTER_TF, BB_RECOVERY_ENTRY_FILTER_TF, BB_RECOVERY_FILTER_TF, BREAKEVEN_GAIN_EROSION_FILTER_TF, BREAKOUT_RETEST_FILTER_TF, BTC_ACCEL_RAMP_REQUIRE_POSITIVE, BTC_DEDICATED_FILTER_TF, BTC_HARD_BLOCK_OTHER_ACCOUNTS, BTC_ROUND_BANDS_EACH_SIDE, BT_WT_CROSS_LADDER_FILTER_TF, CANDLE_PATTERN_STOPS_FILTER_TF, CIRCUIT_SHARPE_GATES_FILTER_TF, COOLDOWN_LOCKS_FILTER_TF, CRYPTO_SPIKE_FADE_THRESHOLD_PCT, DC_BREACH_REDUCE_FILTER_TF, DC_BREAK_FILTER_TF, DC_MOMENTUM_BOTA_SCORER_FILTER_TF, DC_MOMENT_STRONG_THRESHOLD, DELTA_ENGINE_FILTER_TF, DELTA_HTF_GATE, DELTA_REENTRY_FILTER_ENABLED, DUP_GUARD_FILTER_TF, E2E_REPLAY_VALIDATOR_FILTER_TF, EMA_9_21_FILTER_FILTER_TF, EMA_9_21_FILTER_MIN_TFS, EMA_BLANKET_FILTER_ENABLED, EMA_BLANKET_FILTER_FILTER_TF, EMA_BLANKET_FILTER_MIN_TFS, EMERGENCY_BRAKE_FILTER_TF, EXECUTE_NOW_SINGLE_GATE_ENFORCE, EXHAUSTION_EXIT_FILTER_TF, EXIT_R1_R2_FILTER_TF, EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF, EXIT_TOP_FADE_FILTER_TF, EXIT_TO_REDUCE_ADAPTER_FILTER_TF, EZ_MANAGE_THROTTLER_RATE, FAST_RISER_FILTER_TF, FG_FEAR_THRESHOLD, FG_GREED_THRESHOLD, FH_MOMENTUM_FILTER_TF, FIRST_OPEN_THROTTLE_FILTER_TF, FROZEN_STOP_FILTER_TF, FUNDING_GATE_FILTER_TF, FUNDING_GATE_LONG_MAX, FUNDING_GATE_MTF_REQUIRED, FUNDING_GATE_SHORT_MIN, GOLDEN_RULE_BASE_USD, GOLDEN_RULE_ENFORCE_FILTER_TF, GOLDEN_RULE_HTF_VOTE_FILTER_TF, GR_FILTER_ALL_ENTRIES, GR_FILTER_VEC_ENABLED, GR_FILTER_VEC_FILTER_TF, GR_FILTER_VEC_MIN_TFS, GR_V5_STATE_FILTER_TF, HAIKU_WINNER_FILTER_TF, HA_WICK_QUALITY_ENABLED, HA_WICK_QUALITY_SCORE, HA_WICK_QUALITY_TF, HLR_SMA_BAND_PCT, HLR_TOP_MIN_TFS, HTF4_CONF, HTF_DIRECTION_GATE_ENABLED, HTF_GATE_BYPASS_RZ, HTF_GATE_D_MANDATORY, HTF_GATE_MIN_CONFIRMATIONS, HTF_TREND_VETO_BYPASS_ENABLED, HTF_TREND_VETO_BYPASS_REASONS, KILLER_KNOB_FINDER_FILTER_TF, LEADERBOARD_FILTER, LH_HL_FILTER_ENABLED, LH_HL_FILTER_MODE, LH_HL_FILTER_REQUIRE_BOTH, LH_HL_FILTER_TF_REQ, LIVE_ENTRY_ENGINE_FILTER_TF, LIVE_ONLY_SIGNALS_BATCH5_FILTER_TF, LIVE_VEC_EMERGENCY_BRAKE_ENABLED, LR_BAND_LADDER_STOCH_EXTREME, LR_BAND_LADDER_TF_BOTTOM, LR_BAND_LADDER_TF_TOP, MANDATORY_REENTRY_WT_FILTER_MIN_TFS, MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY, MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP, MANDATORY_REENTRY_WT_FILTER_TF_MODE, MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO, MARKET_QUALITY_SCORE_ENABLED, MI_TF_AGREE_MIN, MOM3_FILTER_TF, MOMENTUM_BREAKOUT_FILTER_TF, MOVER_THRESHOLD, MTF_ARMED_ENTRIES_FILTER_TF, MTF_ATR_TRAIL_FILTER_TF, MTF_DC_REJECT_FILTER_TF, MTF_FILTER_STRONG_BUY_QUICK_BYPASS, MTF_GR_MIN_IND, MTS_BOTTOM_BONUS_THRESHOLD, MTS_BOTTOM_STRONG_THRESHOLD, MTS_GATE_ENABLED, NEWBORN_LOSS_KILL_FILTER_TF, NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT, NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST, NEWBORN_PROTECT_FILTER_TF, NEW_POSITION_MAX_LOSS_THRESHOLD, NOLOSS_BYPASS_WT5OF5_FILTER_TF, OI_CONFIRM_ENABLED, OI_CONFIRM_MIN_CHANGE_PCT, OI_CONFIRM_MIN_PRICE_PCT, OPEN_INTENT_SIZE_GATES_FILTER_TF, PARTIAL_PROFIT_LOCK_V2_FILTER_TF, PEAK_GIVEBACK_BE_EROSION_FILTER_TF, WT_15M_BOUNCE_BB_MAX, WT_15M_BOUNCE_BB_MIN, WT_15M_BOUNCE_HIGH_1H_GT_PREV, WT_15M_BOUNCE_LOW_1H_GT_PREV, WT_15M_BOUNCE_REL_VOL_GT_1`

---

## Part F — Recommended Port Order (pragmatic)

Do not port alphabetically. Port by **expected edge / wiring cost**.

| Priority | Category | Why | Effort | Old engine file to edit |
|---|---|---|---|---|
| **P0** | B+C+D — BB Squeeze + WT_15M_BOUNCE cluster + DC Breakout/Hopeless | TEMPLATE INSTRUCTIONS calls WT_15M_BOUNCE the most broken/most causal gate; old BB/DC sweeps already hot | Lo (1 field + 1 branch each) | `per_sym_engine_crypto.py` + profiler VALUES |
| **P1** | E — GUARANTEED_REENTRY 6 + BOUNCE 3 + LEASH | Direct cousins of old winners `REENTRY_MANDATORY`/`REENTRY_TIER1` (87 hits) — proven reentry edge | Lo-Med | same + `per_sym_tradier_profiles.py` for stocks |
| **P1** | F — BREAKEVEN_GAIN_EROSION + HOPELESS + SPIKE_FADE + RANGING | Exit gate family — old winners use `WIN_TRAIL_EROSION_PCT` (56 hits); this is the missing complement | Med | same |
| **P2** | G+H — Augment/Pyramid + Reduce/Profit-Lock | Sizing edge — old has `AUGMENTATION_COOLDOWN_SECONDS` (35 hits) but not the per-augment gain thresholds | Med | same |
| **P2** | I — HTF/GR fine-tuning (beyond already-promoted GOLDEN_RULE) | Diminishing returns — old already has core GR | Med | same |
| **P3** | J+K — Other indicators / Structural guards | Guardrails, not alpha — port last or not at all | Lo | same |
| **P3** | A — 53 FILTER_TF gates | Batch as one shared MTF gate; 53 individual knobs = combinatorial hell for marginal gain | Hi if individually, Lo as one | shared gate only |
| **Skip** | 15 DEAD filters, BTC_* that duplicates old BTC 163 switches | Known dead or redundant | — | — |

**Concrete next command (S1, one switch at a time):**

```bash
# Add one P0 switch to old engine, test on one symbol, honest metrics
# Example: BB_SQUEEZE_ENTRY_ENABLED
# 1) edit per_sym_engine_crypto.py: add field + branch
# 2) add to per_sym_crypto_profiler.py: BB_SQUEEZE_ENTRY_ENABLED_VALUES = [False, True]
# 3) run marginal sweep against current best for 1-2 symbols on S1
ssh s1-int "cd ~/binance-sandbox && python3 per_sym_crypto_profiler.py --sym BTCUSDC --mutate-all"
# 4) check data/sweep_results/*.csv via metrics_guard — pool_sharpe > 0.5, gain/mo > 20%, dd < 30, tpd 5-15
# 5) if wins, it appears in per_sym_active_config.json overrides for those syms — promote via hourly reconfig
# Repeat per switch — stop when delta_vs_BH ≤ 0 for a whole category (NO POSITIVE DELTA → STOP per Bible)
```

---

## Appendix — Raw Counts for Verification

```
TEMPLATE distinct switches: 357 (4,229 rows, 20 sheets) — SPREADSHEETS/TEMPLATE.xlsx
FILTER_DICTIONARY distinct filters: 119 (427 rows)
Old winners distinct: 185 (trb 160 + inf 101 + per_sym 245 — union)
Overlap: 22 · Novel: 335 · Old-only: 163
Wired in v12_quick_engine: 1,043 getattr · Not wired: 198 (55%) · FILTER_TF not wired: 30
Old engine sweep grids: BB_LEN [10,14,20,30,50,80] BB_STD [1.5..3.0] WT_CHAN [6..25] WT_AVG [12..50] DC_PERIOD [10..120] etc.
```

All numbers reproduced via `openpyxl` + `re.findall(r'getattr\(cfg,"[A-Z0-9_]+"', ...)` + `json.load(active_config.json)` — run the same one-liners in Part C to re-verify.

