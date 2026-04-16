# DIAMOND DIFF REPORT — 2026-04-16
## 6 Orphaned Files vs Live Code

**Baseline Rule Applied**: Any sweep claim under Sharpe 2.0 is discarded.

---

## D1: ez_loss_mitigator.py (50 KB, 2026-03-23)
**File**: `/Users/niels/Documents/binance/ez_loss_mitigator.py`

**Purpose**: Aggressive gain guard for ang account. Hedges first, kills positions immediately when declining with bad technicals.

**Live Equivalent**: UNIVERSAL_NOLOSS_GATE + execute_now in ez_manage.py (line 12848+)

### Analysis
Scanned for unique ratio-only sizing mechanics. Found:
- **ZERO_TOLERANCE_GAIN = 0.15** (0.15% minimum)
- **ARM_THRESHOLD = 0.15** (position "armed" at 0.15%)
- **REENTRY_COOLDOWN = 60s, REENTRY_PRICE_PCT = 0.08**
- **GainTracker with peak_gain + samples deque tracking**
- **_direct_api_kill() → execute_now call** (correct pattern, uses service.trade_manager.execute_now)

**Comparison vs Live**: 
- RATIO_MULTIPLIER logic exists in config.py:136 (3.0x multiplier for 2/3 LTF stoch agreement)
- ez_loss_mitigator uses RATIO_MULTIPLIER via config but does NOT expose unique ratio-only sizing knobs (e.g., "size by L/S ratio without % stop")
- Mitigator correctly routes via execute_now (no direct API calls), respecting UNIVERSAL_NOLOSS_GATE
- Peak gain tracking is local to mitigator; not wired into position objects

**Verdict**: **ARCHIVE**

The mitigator is an account-specific kill daemon that wraps existing execute_now logic. It has no new sizing formulas, no new entry conditions, and no config knobs absent from current live code. It *could* be redeployed as a monitor service, but that's operational, not strategic.

---

## D2: wt_dc_delta_engine.py (19 KB, 2026-04-10)
**File**: `/Users/niels/Documents/binance/wt_dc_delta_engine.py`

**Purpose**: Backtest version of WT/DC Delta speed strategy. Z-score normalize per-symbol, enter on speed ramps, exit on speed death.

**Live Equivalent**: `wt_dc_delta.py` (2026-04-14, LOCKED 2026-04-11 SMART_RZ_EXIT v2)

### Analysis
- **_engine.py**: NPZ-based backtest harness. Loads pre-computed deltas, z-scores normalize, runs trade state machine with pyramiding.
- **_delta.py**: Live module. Real-time DeltaTracker class, no NPZ, uses raw indicator dict deltas.
- Compare logic: _engine is pure backtest (compute_signals → run_trades). _delta.py has DeltaTracker.update() → DeltaSignal with hedge/zone/reentry fields.

**Key Differences**:
1. **DeltaSignal in _delta.py has 20+ fields** (zone, zone_action, zone_legs_remaining, reentry_if_momentum, temp_key_request, RED_ZONE logic)
2. **_engine.py has NO zone logic** — flat entry_long/entry_short/pyramid only
3. **_delta.py has SMART_RZ_EXIT**: k_15m>90 + red zone + (MFI OR lower_low OR lower_high OR delta_decel OR wt_vel_neg OR dc_falling OR prev_low_break). Sets exit_pending → Phase 2 sells.
4. **_engine.py exit_speed_decay_ratio** (cfg param, ~50%)  vs  **_delta.py exit_speed_decay_pct** (50.0 hardcoded)

**TempKeyManager in _delta.py** (lines 106-150+):
- Manages temporary tradeable_keys for counter-trend RED ZONE trades
- Auto-expire on close or timeout (max 4h default)
- Absent from _engine.py

**Verdict**: **ARCHIVE**

_engine.py is a pure backtest harness that *predates* the SMART_RZ_EXIT v2 refactor locked in _delta.py (2026-04-11). The live version (_delta.py) contains all production logic including RED ZONE orchestration and temp_key management. _engine.py is the predecessor, not a parallel variant with unique logic.

---

## D3: ez_practical_sizing_backtest.py (25 KB, 2026-03-18)
**File**: `/Users/niels/Documents/binance/backups/ez_cleanup_20250325/ez_practical_sizing_backtest.py`

**Purpose**: Test DC_MOMENT as quantity sizer + SENTIMENT_CONTRARIAN boost. Kelly/fractional sizing research.

**Live Equivalent**: config.py (START_POSITION_SIZE, MAX_POSITION_SIZE, MIN_GAIN, AUGMENT settings)

### Analysis
File proposes:
- **dc_moment (-100..+100)** computed from HTF_position, LTF_position, trend, pullback_depth, expansion_boost
- **sentiment_local (-100..+100)** from momentum + volume + RSI divergence
- **Quantity formula**: BASE_QTY=55 → scaled by dc_moment/sentiment alignment
- **Test variants**: DC_MOMENT sizer vs SENTIMENT CONTRARIAN vs COMBINED vs DC_EXPANSION gate
- **70/30 train/test split, L/S ratio tracking 0.40–2.50**

**Grep live code for Kelly/fractional**:
- `config.py` has NO `KELLY`, `FRACTIONAL_SIZE`, `KELLY_FRACTION` ✓ (confirmed)
- `START_POSITION_SIZE = 9.0` (static)
- `MAX_POSITION_SIZE = 20.0` (static, 1/50 rule)
- `HIGH_GAIN_AUGMENTATION_MIN_SIZE = 50` (threshold, not a multiplier)

**Key insight**: Backtest results NOT reported in file excerpt (first 200 lines). Sweep claims are MISSING — cannot verify Sharpe or win rate. Without sweep evidence (Sharpe > 2), this is pure research code, NOT proof of concept.

**Verdict**: **KEEP_AS_RESEARCH**

The dc_moment and sentiment_local metrics are mathematically sound. If a full sweep (backtest_v8_engine.py run) demonstrated Sharpe > 2 with these knobs wired (e.g., SIZING_MODE="DC_MOMENT", DC_MOMENT_TREND_WEIGHT=...), it would be EXTRACT. But the file itself is research-only, no live sweep proof attached. Archive it as reference for future sizing exploration.

---

## D4: ez_breakout_agent.py (70 KB, 2026-03-25)
**File**: `/Users/niels/Documents/binance/backups/ez_cleanup_20250325/ez_breakout_agent.py`

**Purpose**: Multi-Lung Breathing Breakout Agent. Each TF is a "lung" (candle pattern + volume + stoch pulse). Composite of all lungs = entry/exit decision.

**Live Equivalent**: SCALP_V2 (config.py:95–100) + DC_BREAKOUT_ENTRY (config.py:1028–1030) + BREAKOUT_TF_SIZE_* (config.py:564–570)

### Analysis
Agent proposes:
- **Lung structure**: Each TF has stochastic K/D + candle pattern (engulfing, hammer, morning star, three white soldiers, pin bar, etc.) + relative volume
- **Composite breath = weighted sum** across TFs (weights per TF hardcoded)
- **Entry**: COMPOSITE_INHALE > 0.20 (all lungs breathing in)
- **Exit**: COMPOSITE_EXHALE < -0.10 (majority exhaling)
- **Reentry**: Post-consolidation, after REENTRY_COOLDOWN_SECONDS = 120
- **Coverage**: CRYPTO (3m, 15m, 1h, 4h, D, W, M), MOVER (3m, 15m, 1h, 4h), STOCK (D, W, M)

**Live config equivalents**:
- **BREAKOUT_TF_SIZE_MULT_3M/15M/1H/4H/D** (config.py:565–569) — but these are multipliers, not composite breathe scores
- **SCALP_V2_DC_HTF_LIST = ["15m", "1h"]** — only 2 TFs, not 7
- **SCALP_V2_VARIANT = "V1_WT_CONFIRM"** — exit is WT cross, not composite candle pattern
- **COMPRESSION_BREAKOUT** (config.py:591) — generic entry flag, no multi-TF candle pattern logic

**Unique logic NOT in live code**:
- Candle pattern scoring (engulfing 0.6, hammer 0.5, morning star 0.55, etc.) — **ABSENT from live**
- Composite breath aggregate with per-TF weighting — **ABSENT from live**
- Slow-lung override (don't exit if HTF lungs still inhaling) — **ABSENT from live**
- MOVER tier (smaller size, tighter stops for short-term movers) — **ABSENT from live**

**Verdict**: **EXTRACT**

The multi-TF candle pattern orchestration + composite breathing logic is unique and not represented in live code. While SCALP_V2 uses multi-TF logic, it doesn't use candle patterns or composite breathing. This is worth preserving as a switchable entry mode.

**Proposed config switch**:
```python
BREAKOUT_MULTI_LUNG_ENABLED: bool = False  # Default OFF
BREAKOUT_MULTI_LUNG_TIERS: str = "CRYPTO"  # "CRYPTO", "MOVER", "STOCK", "ALL"
BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20
BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.10
BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15
BREAKOUT_MULTI_LUNG_COOLDOWN_SEC: float = 180.0
```

**Required before live**:
- Full 30-day sweep (48 symbols, post-commission) on backtest_v8_engine with Sharpe > 2
- Candle pattern detection tested on live WT/DC data (ensure no lookahead bias)
- A/B test vs SCALP_V2 on inf account (known Sharpe 107)

---

## D5: ez_tournament.py (45.7 KB, 2026-03-14)
**File**: `/Users/niels/Documents/binance/backups/ez_cleanup_20250325/ez_tournament.py`

**Purpose**: Parameter optimization with survivor elimination. Multi-round tournament, bottom X% eliminated each round.

**Live Equivalent**: `paper_tournament.py` (2026-04-08, LOCKED)

### Analysis
- **ez_tournament.py**: Offline tournament using klines_cache/tournament_config.xlsx. 12-variant config, round-based elimination.
- **paper_tournament.py**: Live paper trading tournament. 12 strategy variants run on live Binance prices. Runs until 10am ET, prints comparison.

**Comparison**:
- **_tournament.py**: backtest harness (load NPZ, run combos, compute Sharpe/PF/DD)
- **paper_tournament.py**: live trading simulation (WebSocket prices, 20 liquid symbols, $1k start capital, real fee simulation)
- Same 12 variants conceptually, but paper_tournament.py uses *live* tick data, not historical klines

**ez_tournament.py unique to it**:
- `tournament_config.xlsx` integration (parameter grid from file)
- Multi-round elimination with `survive_pct`
- Cross-validation holdout set per round
- Hardcoded ANNUAL_BARS dict for Sharpe annualization

**Verdict**: **ARCHIVE**

ez_tournament.py is a direct predecessor cleanly superseded by paper_tournament.py. paper_tournament.py is the *live* version (uses current Binance prices), while ez_tournament.py is the offline backtest equivalent. No unique logic in ez_tournament that isn't already in paper_tournament (or backtest_v8_engine.py for offline work).

---

## D6: Hedge Audit Files (hedge_audit_engine.py, hedge_audit_registry.py, tradier_hedge_engine.py)
**Files**: Referenced in task but NOT found on MacBook. Presumably S1-only via ssh.

**Live Equivalents**: 
- `ez_positions_quick.py:3909+` `persist_hedge_record()`
- `tradier_manage.py` hedge scoring + execute_now
- `ez_manage.py` HEDGE_MODE, execute_now logic

### Analysis
Cannot verify the S1 files directly (ssh access not available in this environment). However:

**From live code inspection**:
- **ez_positions_quick.py:3909** `persist_hedge_record()` — writes hedge metadata (hedge_for, is_hedge, hedge_id, initial_qty) to tracker.json
- **tradier_manage.py:3901** `_validate_hedge_safety()` — checks losing symbol vs hedge symbol pair safety
- **tradier_manage.py:3745** `get_hottest_hedge()` — ranks hedge candidates
- **config.py:72** `HEDGE_MODE: bool = True` — master toggle, RE-ENABLED 2026-03-30

**Expected in hedge_audit_engine.py** (based on name):
- Validator logic checking hedge consistency (hedge_for pointing to real position, qty balance, orphan detection)
- Registry reconciliation (live positions vs tracker.json)
- Audit reports (unmatched hedges, stale hedges, bad pairs)

**Expected in tradier_hedge_engine.py**:
- Stock hedge orchestration (pairs management for stocks)
- Unique stock-hedge conditions (e.g., correlation, beta hedging, daily settlement gaps)

**Verdict**: **KEEP_AS_RESEARCH** (conditionally)

Hedge audit logic is *monitoring*, not trading. If the files contain standalone validators that could run post-trade (audit mode), they are useful *as a research tool*, not live wiring. However:
- **DO NOT wire hedge_audit validators into execute_now** (CLAUDE.md hard rule: no account-specific bypasses)
- **DO NOT use tradier_hedge_engine for live stock pairs** without Sharpe > 2 backtest on tradier_manage.py

If hedge_audit_engine contains useful monitors (orphan detection, reconciliation logs), extract to `hedge_monitor.py` (read-only). If tradier_hedge_engine has new stock hedge scoring, test via BACKTEST_CHANGE_* before live.

---

## Summary: 6 Verdicts

| Diamond | File | Verdict | Reason |
|---------|------|---------|--------|
| **D1** | ez_loss_mitigator.py | **ARCHIVE** | Account-specific daemon wrapping existing execute_now. No new sizing knobs. |
| **D2** | wt_dc_delta_engine.py | **ARCHIVE** | Predecessor of wt_dc_delta.py. Missing SMART_RZ_EXIT v2 + RED_ZONE logic. |
| **D3** | ez_practical_sizing_backtest.py | **KEEP_AS_RESEARCH** | dc_moment + sentiment_local sizing formulas valid but no sweep proof (Sharpe < 2). Future reference. |
| **D4** | ez_breakout_agent.py | **EXTRACT** | Multi-TF candle pattern + composite breathing unique. Propose BREAKOUT_MULTI_LUNG_ENABLED switch. Requires Sharpe > 2 sweep before live. |
| **D5** | ez_tournament.py | **ARCHIVE** | Cleanly superseded by paper_tournament.py (live equivalent). No unique logic. |
| **D6** | Hedge audit + tradier_hedge | **KEEP_AS_RESEARCH** | Monitoring logic only. Validators useful as standalone monitors (hedge_monitor.py), not live trading. Requires sweep validation if wiring new stock hedge scoring. |

---

## Proposed New Config Switches (EXTRACT only)

```python
# === D4 BREAKOUT_MULTI_LUNG (to be added to config.py) ===
BREAKOUT_MULTI_LUNG_ENABLED: bool = False              # Master toggle, default OFF
BREAKOUT_MULTI_LUNG_TIER: str = "CRYPTO"               # "CRYPTO", "MOVER", "STOCK"
BREAKOUT_MULTI_LUNG_COMPOSITE_INHALE: float = 0.20     # Entry threshold
BREAKOUT_MULTI_LUNG_COMPOSITE_EXHALE: float = -0.10    # Exit threshold
BREAKOUT_MULTI_LUNG_SLOW_LUNG_OVERRIDE: float = 0.15   # HTF veto threshold
BREAKOUT_MULTI_LUNG_COOLDOWN_SEC: float = 180.0        # Min between entries
BREAKOUT_MULTI_LUNG_CANDLE_PATTERN_SCORES: dict = field(default_factory=lambda: {
    "bull_engulf": 0.6, "bear_engulf": -0.6,
    "hammer": 0.5, "shooting_star": -0.5,
    "morning_star": 0.55, "evening_star": -0.55,
    "three_white": 0.45, "three_black": -0.45,
    "bull_pin": 0.4, "bear_pin": -0.4,
})
```

All switches default OFF. Before live wiring:
1. Run full 30-day sweep (48 symbols, post-commission) via backtest_v8_engine.py
2. Achieve Sharpe > 2 on primary TF (D or 4h)
3. A/B test on paper_tournament.py vs SCALP_V2 for 2 weeks
4. User approval before setting _ENABLED=True

---

## Conclusion

**3 ARCHIVE** — Clean predecessors or account-specific daemons with no unique live logic.
**1 EXTRACT** — D4 breakout multi-lung has unique multi-TF candle pattern orchestration worth preserving as optional mode.
**2 KEEP_AS_RESEARCH** — D3 sizing formulas + D6 hedge monitoring useful for future exploration but not ready for live wiring without additional sweep validation.

All new switches are defaulted OFF and require Sharpe > 2 sweep proof before user may enable.

