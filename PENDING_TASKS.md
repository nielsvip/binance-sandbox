# PENDING TASKS — Trading System

_Generated 2026-05-14. Source: path inventory session + user directives._

---

## Priority Legend
- **P0** — Blocks sweep accuracy / live parity; fix before next promote cycle
- **P1** — High-value signal gaps with measured live event counts
- **P2** — Structural improvements; test before wiring live
- **P3** — Low-frequency or speculative; queue after P1/P2 clear

---

## P0 — Sweep Accuracy Blockers

### P0-A · RIDICULOUS_HOLD time-variant inline (backtest_v8_engine.py)
**What**: Engine DISC-7 only fires on loss floor (`gain <= RIDICULOUS_LOSS_PCT = -15%`). Live additionally fires on `age > RIDICULOUS_HOLD_HOURS` (720h) AND `gain >= 0` via `REQUIRE_GAIN_NONNEG=True` branch. The age-based path is never reached in backtest — engine only calls the loss path.  
**Impact**: 234 live events/30d (P1 gap). Backtest holds profitable stalled positions indefinitely → upward PnL bias.  
**Fix**: Add age-based branch to DISC-7 inline block: read `RIDICULOUS_HOLD_HOURS` and `REQUIRE_GAIN_NONNEG` from config; if `bar_age_hours > threshold AND gain >= 0` (when REQUIRE_GAIN_NONNEG=True) → fire close with reason `RIDICULOUS_HOLD_GUARD`.  
**Files**: `backtest_v8_engine.py` ~L3338 (DISC-7 block).

---

### P0-B · VEC_STRATEGY_GATES wired into engine entry path
**What**: 4 boolean entry gates (MR5_L, MOM5+TRENDER_L, MR3S_S, MOM4S_S) exist in `ez_manage.py:13467` but are NOT wired into `backtest_v8_engine.py`. Default `VEC_GATES_LOG_ONLY=True` means shadow-log only in live. Engine ignores completely.  
**Impact**: Sweep results for any config testing VEC_STRATEGY_GATES accuracy are garbage — engine silently skips the gate.  
**Fix tasks**:
1. Set `VEC_GATES_LOG_ONLY=False` in a sweep config to actually block/allow entries.
2. Wire gate into `backtest_v8_engine.py` execute_now path: import `vec_paths/vec_strategy_gates.py`, call gate check before `check_entry_candidates_for_account()`, skip entry if gate returns False.
3. Run sweep with gate ON vs gate OFF on full 48-sym crypto pool to get reliable Sharpe delta.  
**Files**: `backtest_v8_engine.py` (entry loop), `vec_paths/vec_strategy_gates.py` (must exist or create), `config.py` (`VEC_GATES_LOG_ONLY`).

---

## P1 — High-Event-Count Signal Gaps (190+ events/30d each)

### P1-A · STALL_SUB vec module (190 live events/30d)
**What**: `STALL_SUB` fires from `_stall_close_scanner()` — a portfolio-level periodic scanner not called from `process_position` or `check_exit`. Closes profitable positions that have stalled (price range < threshold for N bars).  
**Why missing from engine**: `_stall_close_scanner()` is an async task loop separate from per-position processing. Engine runs per-position, not portfolio-wide.  
**Fix**: Create `vec_paths/stall_sub.py` — vectorized implementation:
- Input: per-symbol OHLCV bars, `STALL_MAX_CLOSES_ENABLED`, `STALL_RANGE_PCT`, `STALL_BARS_MIN`
- Output: boolean array per bar — "stall condition met"
- Wire as exit signal in `v8_vec_sweep.py` and `backtest_v8_engine.py` DISC block.
- Future NPZ regen: add `stall_range_N` precomputed fields.  
**Files**: new `vec_paths/stall_sub.py`, `vec_paths/PATH_AUDIT_v2.md` (update coverage).

---

### P1-B · QUICK_BREAKEVEN_GAIN_EROSION_STOP vec module (177 live events/30d)
**What**: Fires when position has reached a gain peak then eroded back near breakeven. Separate from STALL_SUB — time/velocity based, not range-based.  
**Why missing**: Inline in `ez_manage.py` near PPL block; not replicated in engine or vec.  
**Fix**: Create `vec_paths/breakeven_gain_erosion.py`:
- Input: per-bar gain array, peak-gain running max, `QUICK_BREAKEVEN_*` config params
- Output: boolean array — "eroded from peak back to breakeven band"
- Wire as exit gate in engine DISC block (before process_position).
- Future NPZ regen: no new fields needed — computable from price + entry_price bars.  
**Files**: new `vec_paths/breakeven_gain_erosion.py`, `backtest_v8_engine.py` DISC block.

---

## P2 — New Signal Tests (vectorized sweep first, no live change until proven)

### P2-A · BB_RECOVERY_ENTRY — test in v8 vectorized
**What**: Currently only `BB_RECOVERY_EXIT` exists (tradier_manage.py:9470) — fires when price re-enters BB after being pushed outside, allowing NOLOSS bypass close. An entry signal on BB_RECOVERY is the inverse: open/augment when price re-enters BB after an overshoot (mean-reversion setup, expected to be additive).  
**Hypothesis**: Should improve avg_gain_trade by entering on confirmed pullback rather than breakout chasing.  
**Test plan**:
1. Add `BB_RECOVERY_ENTRY_ENABLED` flag (default OFF) to `config_tradier.py` and `config.py`.
2. Create `vec_paths/bb_recovery_entry.py` — detect bar where price crosses back inside bb_upper/bb_lower from outside.
3. Run full tradier sweep (114 syms × 4yr) with flag ON vs OFF via `backtest_v8_sweep.py --mode tradier`.
4. **Consider commissions + slippage**: use `SLIPPAGE_PCT=0.05` and `COMMISSION_PCT=0.01` in sweep config — these are mean-reversion entries, slippage matters.
5. Promote only if `pool_sharpe` delta ≥ +0.05 on sample-floor compliant run.  
**Files**: new `vec_paths/bb_recovery_entry.py`, `config_tradier.py`, `config.py`.

---

### P2-B · ATR_TRAIL_EXIT — re-examine with vectorized sweep
**What**: `ATR_TRAIL_ENABLED_TRADIER=False` (DEAD_CONFIRMED). Was disabled because it was the #1 stock PnL destroyer (-2557% cumulative, BACKTEST_CHANGE_T58). However, that test used a naive ATR multiplier. Hypothesis: a wider multiplier (2.5×–4×) applied only after position reaches ≥1% gain might perform differently vs the original always-on 1.5× version.  
**Caution**: DO NOT re-enable on live without full sweep proof. This flag has a documented disaster history.  
**Test plan**:
1. Create `vec_paths/atr_trail.py` — trailing stop at `entry ± N × ATR_14` where N is a sweep parameter.
2. Sweep `ATR_MULT` ∈ {1.5, 2.0, 2.5, 3.0, 4.0} × `ARM_GAIN_PCT` ∈ {0.5%, 1.0%, 1.5%} (arm = don't activate until this gain reached).
3. Run on 114-sym tradier pool × 4yr with commissions+slippage.
4. Compare pool_sharpe AND max_dd_pct vs baseline — ATR trail must not increase dd.
5. If ANY config beats baseline by ≥0.05 Sharpe with dd ≤ baseline: flag for Tier-2 confirmation before promoting.  
**Files**: new `vec_paths/atr_trail.py`, sweep config entry in `backtest_v8_sweep.py`.

---

### P2-C · DC_BREAK_HIGH / DC_BREAK_LOW as GR multiplier inputs (tradier)
**What**: `StockBreakoutScalper` in `tradier_manage.py:14878` generates `DC_BREAK_HIGH_<TF>` and `DC_BREAK_LOW_<TF>` signals as standalone async loop entries. These are NOT connected to the GOLDEN_RULE multiplier cascade (1× / 1.5× / 2× / 3× by HTF level). DC breaks are structurally identical to the "breakout" event that GR Phase 1 MULT_BREAKOUT=0.1 is meant to size small — then GR Phase 2 MULT_RETEST=5.0 sizes big on retest at dc_basis.  
**Fix**: Wire DC_BREAK entries into GR sizing logic:
1. When `StockBreakoutScalper` fires `DC_BREAK_HIGH/LOW`, apply `MULT_BREAKOUT` (tiny) — mark position as `dc_break_phase=1`.
2. On retest at dc_basis + WT bounce, apply `MULT_RETEST` (large) — treat as GR Phase 2.
3. In `backtest_v8_engine.py`: add `DC_BREAK_PHASE` state to per-position dict; check dc_basis retest condition in check_entry loop.  
**Prerequisite**: `StockBreakoutScalper` code path must be replicated in engine (currently: not in engine at all). Create `vec_paths/dc_break_scalp.py` first.  
**Files**: `tradier_manage.py` (StockBreakoutScalper, ~L14878), `backtest_v8_engine.py` (GR block), new `vec_paths/dc_break_scalp.py`.

---

### P2-D · REENTRY_BREAKOUT — wire into tradier engine with breakout-price exit
**What**: `REENTRY` events appear in tradier `/history/` (229 events/30d) but a `REENTRY_BREAKOUT` path specifically targeting re-entries after DC break / breakout price is not in the engine. The concept: after a position is closed, re-enter when price retests the breakout level (dc_high or bb_upper for LONG; dc_low or bb_lower for SHORT). Exit when price falls back below that same breakout level.  
**Exit rule**: Close REENTRY_BREAKOUT position if price falls back through the breakout price that triggered re-entry (fallback-to-breakout-price stop). This is a tight structural stop, NOT a percentage stop.  
**Implementation plan**:
1. Add `REENTRY_BREAKOUT_ENABLED` (default OFF) to `config_tradier.py`.
2. In `evaluate_reentry_2()` block in `backtest_v8_engine.py` (~L2420): after standard reentry conditions, add DC break retest branch.
3. Tag re-entry with `reason='REENTRY_BREAKOUT_<TF>'` and store `reentry_breakout_level` in position state.
4. In `process_position` exit check: if `reentry_breakout_level` set AND price crosses back through it → close (bypasses NOLOSS — this is a structural stop like R1/R2).
5. Add `REENTRY_BREAKOUT` to `UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS` in `config_tradier.py`.  
**Files**: `backtest_v8_engine.py` (reentry block + exit block), `tradier_manage.py` (evaluate_reentry_2 equivalent), `config_tradier.py`.

---

### P2-E · SENTIMENT_FADE vec module (118 live events/30d)
**What**: `SENTIMENT_FADE` is the dominant tradier REDUCE reason and a top mismatch cause in E2E replay (0.4–1.0% match rate). Lives in `sentiment_boost.py`. Currently NOT covered by any vec module.  
**NPZ note**: Not worth rebuilding NPZ files now — `sentiment_score` is not precomputed and is context-dependent (inter-day). **Include `sentiment_score` as a required field in the next NPZ regen spec.**  
**Interim plan**:
1. Create `vec_paths/sentiment_fade.py` — proxy implementation using available NPZ fields:
   - Use `rsi_14_5m` divergence from 50 as sentiment proxy (RSI < 40 LONG = negative sentiment drift; RSI > 60 SHORT = positive sentiment drift)
   - Or use `macd_crossunder_5m` as fade trigger
   - Label explicitly: `# PROXY — real sentiment_score not in NPZ; replace on next NPZ regen`
2. Document required NPZ field: `sentiment_score` (daily, per-symbol, from market cap weighted sector momentum).
3. Add to NPZ regen checklist in `backtest_v8_precompute.py` header comment.  
**Future**: After next NPZ regen includes `sentiment_score`, replace proxy with real field.  
**Files**: new `vec_paths/sentiment_fade.py`, `backtest_v8_precompute.py` (add field comment), `vec_paths/PATH_AUDIT_v2.md` (update gap table).

---

### P2-F · L/S ratio estimation from NPZ position sizes
**What**: Live L/S ratio rebalancing (`RATIO_REDUCE / RATIO_CLOSE / RATIO_EMERGENCY_EXIT`) fires 0 times in engine because it requires full portfolio L/S count — unavailable in per-position simulation. These are real live events that cause divergence.  
**Approach**: Estimate L/S ratio from NPZ simulation state across the multi-symbol batch:
1. In `backtest_v8_engine.py` outer loop (iterates symbols), maintain `_pool_long_count` and `_pool_short_count` running totals.
2. After each `process_position()` call, update counts based on position side.
3. Before entry decisions: compute `_ratio = _pool_long_count / max(_pool_short_count, 1)`. If `ratio > RATIO_MAX_LONG` and new entry is LONG → block (RATIO_REDUCE equivalent).
4. Wire `RATIO_REBALANCE_ENABLED` config flag around this block.  
**Caveat**: This is a pool-level approximation — live uses account-level ratio. Will reduce upward PnL bias from missing ratio closes.  
**Files**: `backtest_v8_engine.py` (outer symbol loop), `config.py` (`RATIO_REBALANCE_ENABLED`, `RATIO_MAX_LONG`).

---

## P2-G · PARTIAL_PROFIT_LOCK threshold sweep (0.1% close trigger)
**What**: The current 3-step PPL: Step 1 close 50% at +0.3% (`PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER=0.3`, was 0.5%), arm stop at +0.5% (`ARM_GAIN_PCT=0.5`). The question: should the 0.5% activation level (or the 0.3% close) be loosened to 1% / 1.5% / 2% / 3%?  
**Historical data point** (from config_tradier.py:528 comment, 2026-04-25 sweep, 114-sym × 4.3yr):
- 0.5% close: pool_sharpe=2.832, gain=114%
- 0.9375% close: 2.435 Sharpe, 171% gain
- 1.5%: 1.784 / 206%
- 2.0%: 1.507 / 226%
- 3.0%: 1.253 / 244%
- 5.0%: 1.092 / 262%
- disabled: 1.077 / 247%

**The tradeoff is clear**: Sharpe falls as threshold rises, raw gain increases. Current 0.3%/0.5% maximizes Sharpe.  
**New sweep needed — tighter range + commissions**:
1. Test close thresholds: {0.1%, 0.2%, 0.3%, 0.5%, 0.75%} × arm thresholds: {0.3%, 0.5%, 0.75%, 1.0%}.
2. **MANDATORY**: include `SLIPPAGE_PCT=0.05%` and `COMMISSION_PCT=0.01%` per leg (2-sided = 0.12% round-trip). At 0.1% close trigger, commission drag will likely flip the trade negative.
3. Run on full 114-sym tradier pool via `backtest_v8_sweep.py --mode tradier`.
4. Report `pool_sharpe` AND `avg_gain_trade` AND `gain_per_yr` — all three required. Commission-adjusted only.  
**Files**: `backtest_v8_sweep.py` (add PPL sweep config), `config_tradier.py` (PPL params).

---

### P2-H · GR multiplier hedge exit at score ≥ 15 (even in profit)
**What**: Test whether positions with `golden_rule_htf.score_entry_htf() >= 15` (the highest GR tier, currently 3× multiplier) should be closed/reduced when GR score turns AGAINST the position, even if currently in profit.  
**Hypothesis**: A GR=15 score flip against position = very strong structural reversal signal. Taking profit here instead of waiting for WT exit may improve Sharpe.  
**Test plan**:
1. Add config flag `GR_SCORE_FLIP_EXIT_ENABLED` (default OFF), `GR_SCORE_FLIP_EXIT_MIN_SCORE=15`.
2. In `process_position` exit block: if `gr_score_against >= GR_SCORE_FLIP_EXIT_MIN_SCORE` AND `gain > 0` → fire REDUCE/CLOSE with reason `GR_SCORE_FLIP_EXIT_score15`.
3. Run sweep on both crypto (48 syms) and tradier (114 syms) separately — **never assume result transfers**.
4. Metric focus: does `avg_gain_trade` increase (exit at better price)? Does `trades` count drop (fewer re-entries after premature exits)?  
**Note**: This is an exit-quality test, not a new strategy. Does not require NEW_STRATEGY approval gate.  
**Files**: `backtest_v8_engine.py` (exit block), `config.py` + `config_tradier.py` (new flag), `vec_paths/golden_rule_htf.py` (must expose score array).

---

## P3 — Structural Gaps (missing paths from PATH_AUDIT_FINAL.md)

These 14 paths from PATH_AUDIT_FINAL.md are confirmed missing from the engine. Low individual event counts or require architectural work. Queue after P1/P2 are clear.

| # | Path | Live count/30d | Why blocked | Estimated effort |
|---|------|----------------|-------------|-----------------|
| 1 | RATIO_REDUCE / RATIO_CLOSE | ~50 est | Portfolio loop; use P2-F estimation | M |
| 2 | RATIO_EMERGENCY_EXIT | ~10 est | Same as above | S (extends P2-F) |
| 3 | STALL_MAX_CLOSES | 190 (covered by P1-A) | Async scanner loop | M |
| 4 | SmartCircuitBreaker (CB_HTF_EXHAUST) | <20 | trade_manager mock missing `circuit_breaker` attr | L |
| 5 | MARKET_SPIKE_REDUCE | <10 | No market_sentiment_score in NPZ | L (needs NPZ field) |
| 6 | HAIKU_OVERSEER | <5 | Separate agent process; not calleable from engine | XL |
| 7 | DC_BREACH_REDUCE | ~30 est | Hedge lifecycle monitor sub-path; hedge engine wrapper | M |
| 8 | DC_BREACH_REDUCE_UNHEDGED | ~15 est | Same code path as above | S (extends #7) |
| 9 | VEC_STRATEGY_GATES (entry block) | shadow only | Covered by P0-B | S |
| 10 | FAST_RISER_REDUCE | ~40 est | Not in engine inline; fires from price velocity scanner | M |
| 11 | DC_DAYTRADE entries | ~20 est | Not wired in engine (note in CLAUDE.md: BANNED without V8 proof) | M |
| 12 | StockBreakoutScalper (DC_BREAK_HIGH/LOW entries) | 20 events | Standalone async loop, not process_position | L (covered by P2-C) |
| 13 | StockScalpStrategy (SCALP_LONG/SHORT entries) | ~30 est | Same standalone loop | L (extends P2-C) |
| 14 | NOLOSS_BYPASS_WT_5OF5 | 0 (default OFF) | Not wired in ez_manage (per-path edit needed) | S |

---

## Notes

- **Commission + slippage mandatory for P2-G and P2-A/B**: round-trip cost ~0.12% makes sub-0.3% gain targets economically borderline.
- **Never test crypto and tradier findings interchangeably** — params are opposite (see CLAUDE.md crypto vs stock table).
- **NPZ regen trigger**: tasks P1-A (stall_range_N), P2-E (sentiment_score), P2-F (no new field) — accumulate field list, regen once. `sentiment_score` is the only new field worth waiting for.
- **V8_USE_VEC_ALL=1** must be set for all sweep runs testing new vec modules.
- **Sample floor**: all P2 sweep results below 48 crypto or 114 tradier syms = `[DIAGNOSTIC ONLY]`. Do not promote.
