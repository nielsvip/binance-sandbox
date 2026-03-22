# Trade Mechanisms — Complete Breakdown
## Every Buy/Sell, When It Activates, How Quantity Is Defined

*Generated 2026-03-16 from codebase analysis*

---

# CRYPTO SYSTEM (ez_ files)

## ENTRY MECHANISMS (Buy/Open)

### 1. DC_BREAKOUT_ENTRY — Highest Priority
- **Trigger**: Price breaks above/below Donchian channel on any TF
  - Long: `price > dc_high_{tf}`
  - Short: `price < dc_low_{tf}`
  - Checks TFs in order: 4h (3.0x), 1h (2.0x), 15m (1.5x), 3m (1.0x)
- **Direction**: LONG on high break, SHORT on low break
- **Quantity**: `START_POSITION_SIZE × tier_mult` (1.0x–3.0x based on TF that broke)
- **Gates**: k_3m < 80 (long) or k_3m > 20 (short), indicators < 45min old
- **Bypasses**: WAIT_BLOCK, BASIS_CONDITION, all signal gates
- **File**: `ez_positions_quick.py` ~line 7620

### 2. RATIO_RECOVERY — Forced Rebalancing
- **Trigger**: L/S ratio out of bounds
  - LONG needed: ratio < 0.40 (too many shorts)
  - SHORT needed: ratio > 2.50 (too many longs)
- **Direction**: Whichever side is underweight
- **Quantity**: `START_POSITION_SIZE / price` (minimum opening unit)
- **Gates**: LS_RATIO_ENFORCE=True, k_3m < 70 (long) or > 30 (short), 45s cooldown/account
- **Bypasses**: WAIT_BLOCK, BASIS_CONDITION, SCALP_BOYCOTT
- **File**: `ez_positions_quick.py` ~line 7688

### 3. WR_PULLBACK / LR_SHORTTOP — Multi-TF Pullback Entry
- **Trigger**: HTF trend up + LTF stochastics pulled back to extremes + 1m turning
  - WR (Long): ha_4h != red, k_1h < 45, k_15m < 45, k_3m < 40, k_1m bouncing
  - LR (Short): mirror
- **Direction**: LONG or SHORT based on symbol classification
- **Quantity**: Base `START_POSITION_SIZE`
- **Gates**: Symbol in account list, 300s cooldown per symbol, standard signal checks
- **File**: `ez_positions_quick.py` ~line 7717

### 4. STANDARD_SIGNAL — Rating System (AdvancedSignalRater)
- **Trigger**: `rate()` function score >= 4 or recommendation = "BUY"/"SELL"
  - Scores: stochastic level, DC position, MA crosses, mean-reversion, volume
  - Filtered by account type (scalp vs trend vs no-loss)
- **Direction**: LONG (score > 0) or SHORT (score < 0)
- **Quantity**: `calculate_dynamic_quantity()`:
  - Base: `START_POSITION_SIZE`
  - × symbol volatility (DC width: 0.5x–2.0x)
  - × heat multiplier (many open positions: 0.5x–1.0x)
  - × account type (SCALP: 0.4x)
  - × DC momentum (breakout: 1x–3x tier)
- **Gates**: k_1m must agree, HTF conviction (trend accounts), BASIS_CONDITION, SCALP_BOYCOTT, data < 2min
- **File**: `ez_positions_quick.py` ~line 7613

### 5. SENTIMENT_PYRAMID — News-Driven Entry
- **Trigger**: News sentiment ranking elevated for symbol (CoinGecko + Finnhub + RSS + F&G)
- **Direction**: Per sentiment direction
- **Quantity**: Base to 2x depending on sentiment strength
- **Gates**: Symbol has recent news, ranking score >= 20 (or >= 8 for quick fallback)
- **File**: `ez_positions_quick.py` ~line 6355

---

## EXIT MECHANISMS (Sell/Close)

### 1. EMERGENCY_DEEP_LOSS — Absolute Floor (Priority: ABSOLUTE)
- **Trigger**: `gain < -8.0%`
- **Quantity**: 100% (full close)
- **Bypasses**: ALL rules including STRICT_NO_LOSS
- **File**: `ez_positions_quick.py` ~line 7226

### 2. GLOBAL_HARD_STOP — Loss Limit (Priority: ABSOLUTE)
- **Trigger**: `gain < -2.5%`
- **Quantity**: 100%
- **Bypasses**: STRICT_NO_LOSS on standard accounts; skipped on no-loss accounts
- **File**: `ez_positions_quick.py` ~line 7209

### 3. EMERGENCY_DC1H_BREACH — Structural Breakdown (Priority: CRITICAL)
- **Trigger**: Price breaches 1h DC low/high by 0.3% + loss >= -1.5%
- **Quantity**: 100%
- **Bypasses**: STRICT_NO_LOSS
- **File**: `ez_positions_quick.py` ~line 7222

### 4. BREAK_EVEN_GUARD — Peak Protection (Priority: HIGH)
- **Trigger**:
  - `max_gain > 0.5%` AND `current_gain <= 0.20%` → CLOSE
  - `max_gain > 1.5%` AND `current_gain < max_gain × 0.5` → CLOSE
- **Quantity**: 100%
- **File**: `ez_positions_quick.py` ~line 7235

### 5. GAIN_DECAY / TRAILING_STOP — Erosion Protection (Priority: HIGH)
- **Trigger**:
  - `max_gain >= 3.0%` AND `current_gain < 2.5%` → REDUCE to min
  - `max_gain >= 2.5%` AND `current_gain <= 2.0%` → CLOSE
  - `max_gain > 2.0%` AND erosion > 0.8% → CLOSE
- **Quantity**: REDUCE to MIN_POSITION_SIZE, or 100% CLOSE
- **File**: `ez_positions_quick.py` ~line 7286

### 6. CYCLE_TP_15M — Stochastic Cycle Exit (Priority: MEDIUM)
- **Trigger**: `gain > CYCLE_TP_PCT (3%)` AND k_15m crossunder (long) or crossover (short)
- **Quantity**: 100%
- **File**: `ez_positions_quick.py` ~line 7258

### 7. ACCOUNT_TP — Fixed Take Profit (Priority: MEDIUM)
- **Trigger**: `gain >= ACCOUNT_TP_PCT[account]`
  - ang/inf/men/fin: 2.0%
  - flz: 1.0%
- **Quantity**: 100%
- **File**: `ez_positions_quick.py` ~line 7266

### 8. STOCH_PROFIT_EXIT — 3-LTF Confirmation (Priority: MEDIUM, no-loss only)
- **Trigger**: `0.03% <= gain < 0.50%` + all 3 stochastics turned against direction
- **Quantity**: 100%
- **Gates**: STRICT_NO_LOSS accounts only
- **File**: `ez_positions_quick.py` ~line 7316

### 9. SCALP_REDUCE — Quick Scalp Exit (Priority: MEDIUM, scalp only)
- **Trigger**: Multiple scalp conditions:
  - Decent gain (>= 2%) → quick exit
  - Profit rescue (>= 0.5%, holding > 5min)
  - k_1m cross against direction with micro profit
  - Momentum flip
- **Quantity**: 50–100%
- **Gates**: SCALP_ACCOUNTS only, position < 5min old or gain >= 2%
- **File**: `ez_positions_quick.py` ~line 1371

### 10. AUGMENTED_DC_BREAK_REDUCE — Protect Augmented Positions
- **Trigger**: Augmented position (`amt > 1.2 × min_qty`) + 3m DC break + gain > 0.1%
- **Quantity**: Reduce to MIN_POSITION_SIZE (keep core)
- **File**: `ez_positions_quick.py` ~line 7314

---

## AUGMENTATION MECHANISMS (Add to Position)

### 1. DC_BREAKOUT_AUGMENT
- **Trigger**: DC break happened + gain >= 0% + last augment > 30s ago
- **Quantity**: Full START_POSITION_SIZE
- **Bypasses**: MIN_GAIN gate, ONE_AUG_RULE
- **File**: `ez_positions_quick.py` ~line 6789

### 2. ALL_TF_CONFLUENCE_AUGMENT — Emergency Saver
- **Trigger**: gain < -0.5% BUT all 5 TFs aligned (k_1m through k_4h + ha_D)
- **Quantity**: Full START_POSITION_SIZE
- **Bypasses**: MIN_GAIN gate
- **File**: `ez_positions_quick.py` ~line 6791

### 3. MAX_GAIN_3PCT_AUGMENT — Winner Continuation
- **Trigger**: gain >= 3.0%
- **Quantity**: `max(START_SIZE / price, position × 0.1)`
- **File**: `ez_positions_quick.py` ~line 7325

### 4. PULLBACK_AUGMENT — Oversold Dip Buy
- **Trigger**: Small position + gain <= 0% + k_3m < 35 (long) or > 65 (short)
- **Quantity**: Full START_POSITION_SIZE
- **File**: `ez_positions_quick.py` ~line 7597

### 5. SENTIMENT_AUGMENT — News Boost
- **Trigger**: Symbol in rankings with elevated sentiment
- **Quantity**: Base to 2x
- **File**: `ez_positions_quick.py` ~line 6390

### 6. ONE_AUG_RULE (Gate)
- **Rule**: Second augment BLOCKED until first generates 0.3% gain
- **Exceptions**: DC_BREAKOUT, ALL_TF_CONFLUENCE (score >= 4), WR_PULLBACK

---

## HEDGE MECHANISMS

### 1. IMMEDIATE_HEDGE — Same-Symbol Opposite
- **Trigger**: Position in loss between -0.1% and -3.0%
- **Quantity**: `original_qty × 1.1` (110% to outrun original)
- **Side**: Opposite of original (losing LONG → SHORT hedge)
- **Accounts**: inf, fin, men (HEDGE_ACCOUNTS)
- **Exit**: Original recovers to -0.05%, OR hedge gains >= 0.15%, OR hedge declines
- **File**: `ez_positions_quick.py` ~line 2485

### 2. CROSS_SYMBOL_HEDGE — Different Symbol
- **Trigger**: Loss > -0.3% + same-symbol hedge unavailable
- **Candidates**: Top performers from rankings, inverse symbols
- **Quantity**: `loss_value / volatility_factor`
- **Max**: $5000 per account
- **File**: `ez_positions_quick.py` ~line 3104

---

## REENTRY MECHANISMS

### 1. QUICK_REENTRY — Gap Fill
- **Trigger**: Position closed via reduce, price returns to basis within 20min
- **Quantity**: START_POSITION_SIZE
- **Gates**: < 20min since exit, price between DC_LOW and DC_BASIS, stoch confirms
- **File**: `ez_positions_quick.py` (rate function)

### 2. SCALP_REENTRY — 1m Bounce
- **Trigger**: Scalp closed, k_1m bouncing from extreme within 5min
- **Quantity**: 0.5x–0.75x START_SIZE
- **Gates**: SCALP_MODE, 1m crossover confirmation
- **File**: `ez_positions_quick.py` ~line 1482

---

## DC WIDTH SIZING — Position Size Multiplier

### compute_dc_width_sizing()
- **Input**: dc_moment, dc_qty from ez_indicators (via rankings.json)
- **dc_moment** (-100 to +100): When to enter. Cross-TF analysis of trend + pullback + expansion
- **dc_qty** (-100 to +100): How much. dc_moment × width_rank (cross-symbol)
- **Output**: 2x–8x multiplier on position size
  - `dc_qty > +10` → LONG sizing boost
  - `dc_qty < -10` → SHORT sizing boost
  - abs(dc_qty) determines multiplier magnitude
- **File**: `ez_positions_quick.py` ~line 526

---

# BREAKOUT AGENT (ez_breakout_agent.py) — Autonomous

### FULL_INHALE — Breakout Entry
- **Trigger**: Donchian channel break (1h + 4h for bigcaps, 1h for movers) + multi-lung composite >= threshold
  - Threshold: 0.20 (default), 0.12 (vetted symbols), lower with strong dc_moment
- **Direction**: LONG or SHORT based on DC break + dc_moment
- **Quantity**:
  - Big caps: `base_size_usd / price` (BTC: $280, ETH: $200)
  - Movers: `$60 / price`
  - Stocks: `base_size_shares` (NVDA: 20, MSTR: 10)
- **Accounts**: Big caps → flz, ang. Movers → inf, ang.
- **File**: `ez_breakout_agent.py`

### FULL_EXHALE — Breathing Exit
- **Trigger** (any of):
  - Trailing stop hit (breakeven at +0.3%, then 0.8% trail from peak)
  - Composite breath < -0.10 AND slow lungs not overriding
  - Thesis dead: composite < -0.50
- **Quantity**: 100% (full close — crypto is binary in/out)
- **File**: `ez_breakout_agent.py`

### RE-INHALE — Breathing Re-entry
- **Trigger**: After full exhale, multi-lung composite recovers above threshold
  - Vetted symbols: threshold = 0.12
  - dc_moment >= 40: threshold × 0.7
  - Must still be on right side of breakout level
- **Quantity**: Full size again (same as initial entry)
- **File**: `ez_breakout_agent.py`

### PULLBACK_REENTRY — DC Retest
- **Trigger**: Price returns within 0.5% of DC high/low after a previous breakout
- **Quantity**: Full size
- **Gates**: Multi-lung composite must be >= threshold

---

# STOCK SYSTEM (tradier_ files)

## ENTRY MECHANISMS

### 1. STRATEGY_EVALUATE_OPEN — Main Entry
- **Trigger**: RSI levels + stochastic alignment + volume confirmation + swing low/high retest
- **Direction**: LONG or SHORT
- **Quantity**: Base shares per `config_tradier.START_POSITION_SIZE / price`
  - EXCEPTIONS symbols (NVDA, MSTR, GOOGL, etc): 4x normal size
  - Swing: START_SIZE = $2,000
  - Scalp: START_SIZE = $2,000 but MAX_POSITION = $2,000
- **Gates**: Not in drawdown, not blacklisted, trading hours, buying power
- **File**: `tradier_manage.py` ~line 970

### 2. REENTRY_STRATEGY — Gap Retest
- **Trigger**: Position closed via swing exit, price returns to support/resistance within 2h
- **Quantity**: Previous exit size if available, else base qty
- **Gates**: < 2h since exit, price within multiplier range, stoch confirms
- **File**: `tradier_manage.py` ~line 856

### 3. AUGMENT_STRATEGY — Add to Winner
- **Trigger**: gain > 0.5% + price at new swing high + volume spike
- **Quantity**: 50–150% of initial entry
- **Gates**: Position exists, profitable, last augment > 4h ago
- **File**: `tradier_manage.py` ~line 920

## EXIT MECHANISMS

### 1. STOP_LOSS — Hard Risk Limit
- **Trigger**: Price hits calculated stop level
  - Long: `entry × (1 - stop_pct)` (2–5%)
  - Short: `entry × (1 + stop_pct)`
  - Stochastic-adjusted: tighter stop if k > 80 or k < 20
- **Quantity**: 100%
- **File**: `tradier_manage.py` ~line 5808

### 2. SHOULD_EXIT — Strategy Exit
- **Trigger**: Stoch overbought/oversold + MA bearish/bullish cross + volume drop
- **Quantity**: 100%
- **File**: `tradier_manage.py` ~line 5675

### 3. SWING_PROFIT_TARGET — Per-Symbol TP
- **Trigger**: Price reaches swing high + 0.5–2.0%
- **Quantity**: 100% or 50% (partial)
- **File**: `tradier_manage.py` strategy evaluate_exit

### 4. TIME_DECAY_EXIT — Holding Limit
- **Trigger**: Swing > 24h, Position > 5d without target
- **Quantity**: 100%
- **File**: `tradier_manage.py` strategy-specific

### 5. SCALP_EXIT — Rapid Mini-Trade
- **Trigger**: 5–15min in trade, gain 0.5–1.5%, 1m stoch reverses
- **Quantity**: 100%
- **Gates**: SCALP_MODE, time limit
- **File**: `tradier_manage.py` ~line 7718

## STOCK QUANTITY CALCULATION

`calculate_quantity_complex()`:
1. Base: `BASE_ORDER_VALUE / price` (shares)
2. MAX_ORDER_VALUE cap ($10,000)
3. EXCEPTIONS symbols: 4× normal size (NVDA, MSTR, GOOGL, MSFT, etc)
4. Volatility adjustment (tighter stops → smaller qty)
5. Buying power check
6. Drawdown reduction

---

# UNIVERSAL GATES (Both Systems)

| Gate | Blocks | Bypassed By |
|------|--------|-------------|
| WAIT_BLOCK | All entries when `WAIT` in recommendation | RATIO_RECOVERY, DC_BREAKOUT, REENTRY |
| BASIS_CONDITION | Entries where price wrong side of dc_basis | DC_BREAKOUT, RATIO_RECOVERY |
| LS_RATIO_ENFORCE | Opens that breach hard limits (0.25–4.0) | Nothing (absolute) |
| STRICT_NO_LOSS | Closing positions at loss | EMERGENCY (-8%), HEDGE closes, DC1H_BREACH |
| TRADE_COOLDOWN | Rapid-fire trades (5–300s) | DC_BREAKOUT (30s), RATIO_RECOVERY (45s) |
| STALE_INDICATORS | Data > 2min old | Bridge fallback (hot_metrics._tick_ts < 120s) |
| ONE_AUG_RULE | 2nd augment before 1st shows 0.3% gain | DC_BREAKOUT, ALL_TF_CONFLUENCE, WR_PULLBACK |
| EMERGENCY_BRAKE | >20 opens/h, >50 trades/h, >5/symbol/h | Nothing (absolute) |
| K3M_CAP | Entry when k_3m >= 80 (long) or <= 20 (short) | Nothing |

---

# PRIORITY HIERARCHY

```
TIER 1 — ABSOLUTE (prevent catastrophe)
  EMERGENCY_DEEP_LOSS (gain < -8%)
  GLOBAL_HARD_STOP (gain < -2.5%)
  EMERGENCY_DC1H_BREACH (1h DC break + loss)
  EMERGENCY_BRAKE (rate limiting)

TIER 2 — CRITICAL (protect capital)
  BREAK_EVEN_GUARD (preserve peaked gains)
  GAIN_DECAY / TRAILING_STOP (erosion protection)
  STOP_LOSS (stocks)

TIER 3 — PRIMARY ENTRIES (main strategy)
  DC_BREAKOUT (highest entry priority, bypasses most gates)
  RATIO_RECOVERY (forced rebalancing)
  WR_PULLBACK / LR_SHORTTOP (multi-TF pullback)
  STANDARD_SIGNAL (AdvancedSignalRater score)
  STRATEGY_EVALUATE_OPEN (stocks)

TIER 4 — MANAGED EXITS
  CYCLE_TP_15M (stochastic cycle)
  ACCOUNT_TP (fixed per-account TP)
  STOCH_PROFIT_EXIT (3-LTF confirmation)
  SCALP_REDUCE
  SHOULD_EXIT (stocks)

TIER 5 — AUGMENTATION
  DC_BREAKOUT_AUGMENT
  ALL_TF_CONFLUENCE_AUGMENT
  MAX_GAIN_3PCT_AUGMENT
  PULLBACK_AUGMENT
  SENTIMENT_AUGMENT

TIER 6 — HEDGING
  IMMEDIATE_HEDGE (same symbol)
  CROSS_SYMBOL_HEDGE (different symbol)

TIER 7 — REENTRY
  QUICK_REENTRY (gap fill)
  SCALP_REENTRY (1m bounce)
  RE-INHALE (breakout agent breathing)
  PULLBACK_REENTRY (DC retest)

TIER 8 — AUTONOMOUS (breakout agent)
  FULL_INHALE (DC break + multi-lung confirmation)
  FULL_EXHALE (trailing stop / breath composite)
  DC_MOM override (dc_moment >= 50 from indicators)
```

---

# KEY SIZING PARAMETERS

| Parameter | Value | Where Used |
|-----------|-------|------------|
| START_POSITION_SIZE | $55 (normal), $8 (light), $70 (extreme) | All crypto entries |
| MAX_ORDER_VALUE | $280 (ang), $240 (men), $120 (fin) | Per-trade cap |
| MAX_POSITION_SIZE | $800 (default), $6000 (BTC), $4000 (fin), $1200 (men) | Per-position cap |
| MIN_POSITION_SIZE | $0.90 | Below this → forced close |
| ACCOUNT_TP_PCT | ang/inf/men/fin: 2%, flz: 1% | Fixed take-profit |
| CYCLE_TP_PCT | 3% | 15m stoch cycle exit |
| DC_WIDTH_MAX_MULT | 8x | Max sizing boost from DC width |
| HEDGE_OVERSIZE_RATIO | 1.1x | Hedge must be 110% of losing position |
| Stock START_SIZE | $2,000 (swing), $2,000 (scalp) | Stock entries |
| Stock MAX_POSITION | $10,000 (swing), $2,000 (scalp) | Stock caps |
| Stock EXCEPTIONS | 4x for NVDA, MSTR, GOOGL, MSFT, etc | Special sizing |
| Breakout Agent BTC | $280 entry, $6000 max | Big cap sizing |
| Breakout Agent Movers | $60 entry, $120 max, 0.5% trail | Short-term plays |
