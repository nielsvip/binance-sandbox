# Trader Tactics Research — Implementation Blueprint

> Generated: 2026-03-23
> Source: 9 researched strategies from master trader analysis, cross-referenced with existing system indicators and config conventions.
> Purpose: Ready-to-implement specifications following BACKTEST_CHANGE_1XX convention (starting at 105, skipping existing numbers).

---

## CURRENT SYSTEM INVENTORY

### Indicators Available in `ez_indicators.py` (live, per-TF)

| Indicator | Key Format | Timeframes | Notes |
|-----------|-----------|------------|-------|
| Stoch K/D (14,3,3) | `stoch_k_{tf}`, `stoch_d_{tf}` | 3m,15m,1h,4h,D | + crossover/crossunder flags, prev values |
| RSI(14) | `rsi_{tf}` | 3m,15m,1h,4h,D | Single period only |
| EMA(20) | `ema_20_{tf}` | 3m,15m,1h,4h,D | Only EMA period computed |
| SMA(200) | `sma_200_{tf}` | 1m,15m,1h,4h,D | + prev values, crossover flags |
| ATR(14) | `atr_{tf}` | 3m,15m,1h,4h,D | + prev values |
| BB(20,2) | `bb_upper_{tf}`, `bb_lower_{tf}`, `bb_pct_b_{tf}` | per TF | %B available |
| DC(20) | `dc_high_{tf}`, `dc_low_{tf}`, `dc_basis_{tf}` | 3m,15m,1h,4h,D | + width, position, crossovers |
| Heikin-Ashi | `ha_{tf}` | 3m,15m,1h,4h,D | green/red/neutral |
| WaveTrend | `wt1_{tf}`, `wt2_{tf}`, `wt_signal_{tf}` | 3m,15m,1h,4h,D | |
| MFI | `mfi_{tf}` | 3m,15m,1h,4h,D | |
| Relative Volume | `relative_volume_{tf}` | 3m,15m,1h | |
| LR Trend | `lr_trend_{tf}` | 15m,1h,4h | up/down |

### Indicators NOT Currently Computed (Must Add to `ez_indicators.py`)

| Indicator | Needed By | Implementation Cost |
|-----------|-----------|-------------------|
| **MACD (12,26,9)** | Strategies 1,5,6 | Medium — add EMA(12), EMA(26), MACD line, signal, histogram per TF |
| **StochRSI** | Strategies 3,4 | Medium — RSI of RSI, then Stoch on that |
| **RSI(2)** | Strategy 2 | Low — second RSI call with period=2 |
| **EMA(9)** | Strategies 4,5 | Low — add 9 to `"ema": [20]` config |
| **EMA(14)** | Strategy 4 | Low — add 14 to `"ema": [20]` config |
| **EMA(50)** | Sizing factors | Low — add 50 to `"ema": [20]` config |
| **EMA(200)** | Strategies 3,6,7 | Medium — currently SMA(200) exists, need EMA variant |
| **Candle body ratio** | Strategies 3,9 | Low — `abs(close-open) / (high-low)` |

### Entry Scoring Architecture

- **`rate()`** in `ez_positions_quick.py` (line 948): Returns `(score, action, reason)`. Score-based entry with boycotts/gates.
- **`calculate_final_order_quantity()`** in `ez_manage.py` (line 16691): Sizing factors list — each factor adds/subtracts from sizing score.
- **Config gates**: `RSI_ENTRY_GATE_ENABLED` (RSI<37 LONG), `K_ZONE_ENTRY_ENABLED` (K<35 LONG), `LONG_STOCH_CHASE_BLOCK`, `ENTRY_VOL_MIN_RATIO`, `ENTRY_ATR_PCT_MIN`.
- **Existing BACKTEST_CHANGE numbers used**: 1-9, 12, 14-25, 27, 31-35, 38, 40-45, 48, 100-116, 119, 121-122. Next available: **125+**.

---

## STRATEGY 1: MACD + RSI + Stochastic Triple Confirmation

**Source**: 73% WR, 235 trades backtested. Mean-reversion. Drops to 55% in trends.

### Config Parameters

```python
# config.py — TradingConfig dataclass
TRIPLE_CONF_ENABLED: bool = False  # BACKTEST_CHANGE_125: MACD+RSI+Stoch triple confirmation entry
TRIPLE_CONF_RSI_LONG: float = 30.0  # BACKTEST_CHANGE_125: RSI(14) must be below this for LONG
TRIPLE_CONF_RSI_SHORT: float = 70.0  # BACKTEST_CHANGE_125: RSI(14) must be above this for SHORT
TRIPLE_CONF_STOCH_LONG: float = 20.0  # BACKTEST_CHANGE_125: Stoch K must be below this for LONG
TRIPLE_CONF_STOCH_SHORT: float = 80.0  # BACKTEST_CHANGE_125: Stoch K must be above this for SHORT
TRIPLE_CONF_SCORE_BONUS: int = 30  # BACKTEST_CHANGE_125: Score bonus when all 3 align
TRIPLE_CONF_MACD_EXIT: bool = False  # BACKTEST_CHANGE_125: Exit on MACD cross-back instead of fixed TP
```

### Implementation Location

**File**: `ez_positions_quick.py`, function `rate()`, after the existing entry gates (~line 1097)

**Prerequisite**: Add MACD computation to `ez_indicators.py` — add to `compute_indicators_for_timeframe()`:
- Compute EMA(12), EMA(26) on close
- `macd_line = ema12 - ema26`
- `macd_signal = EMA(9) of macd_line`
- `macd_hist = macd_line - macd_signal`
- Store as `macd_{tf}`, `macd_signal_{tf}`, `macd_hist_{tf}`, `macd_crossover_{tf}`, `macd_crossunder_{tf}`

### Entry Logic (Python pseudocode)

```python
# In rate(), entry scoring section
if getattr(config, 'TRIPLE_CONF_ENABLED', False) and not is_exit:
    _rsi_tf = safe_fetch_float(ind.get(f'rsi_{config.TF_FOCUS}'), 50)
    _stoch_k_tf = safe_fetch_float(ind.get(f'stoch_k_{config.TF_FOCUS}'), 50)
    _macd_crossover = ind.get(f'macd_crossover_{config.TF_FOCUS}', False)
    _macd_crossunder = ind.get(f'macd_crossunder_{config.TF_FOCUS}', False)
    if is_long and _rsi_tf < config.TRIPLE_CONF_RSI_LONG and _stoch_k_tf < config.TRIPLE_CONF_STOCH_LONG and _macd_crossover:
        score += config.TRIPLE_CONF_SCORE_BONUS
        reasons.append(f"TRIPLE_CONF_LONG(rsi={_rsi_tf:.0f},k={_stoch_k_tf:.0f},macd_cross)")
    elif not is_long and _rsi_tf > config.TRIPLE_CONF_RSI_SHORT and _stoch_k_tf > config.TRIPLE_CONF_STOCH_SHORT and _macd_crossunder:
        score += config.TRIPLE_CONF_SCORE_BONUS
        reasons.append(f"TRIPLE_CONF_SHORT(rsi={_rsi_tf:.0f},k={_stoch_k_tf:.0f},macd_cross)")
```

### Exit Logic (if TRIPLE_CONF_MACD_EXIT enabled)

```python
# In rate(), exit section — override fixed TP when MACD exit enabled
if getattr(config, 'TRIPLE_CONF_MACD_EXIT', False) and is_exit and gain > 0:
    _macd_crossunder = ind.get(f'macd_crossunder_{config.TF_FOCUS}', False)
    _macd_crossover = ind.get(f'macd_crossover_{config.TF_FOCUS}', False)
    if is_long and _macd_crossunder:
        return 100, "REDUCE", f"MACD_EXIT_LONG(gain={gain:.2f}%)"
    elif not is_long and _macd_crossover:
        return 100, "REDUCE", f"MACD_EXIT_SHORT(gain={gain:.2f}%)"
```

### Expected Impact

- **Pro**: High-confidence entries (3 indicators must agree). Filters out 60%+ of bad entries.
- **Con**: Very few signals — 235 trades over full backtest period. May produce 1-2 entries/day across 350 symbols.
- **Risk**: 55% WR in trends. Must combine with market regime filter (see Cross-Cutting section).
- **Recommendation**: Use as a score BONUS (not a gate) so it enhances entries found by existing K-zone/RSI logic. Start with `TRIPLE_CONF_SCORE_BONUS = 30` (high conviction additive, not multiplicative).

---

## STRATEGY 2: RSI(2) Mean Reversion

**Source**: 91% WR on daily, 0.82% avg gain, 33% max drawdown. Larry Connors style.

### Config Parameters

```python
# config.py — TradingConfig dataclass
RSI2_MEAN_REVERSION_ENABLED: bool = False  # BACKTEST_CHANGE_126: RSI(2) ultra-oversold mean reversion on 1h/4h
RSI2_LONG_THRESHOLD: float = 15.0  # BACKTEST_CHANGE_126: RSI(2) below this = LONG entry
RSI2_SHORT_THRESHOLD: float = 85.0  # BACKTEST_CHANGE_126: RSI(2) above this = SHORT entry
RSI2_EXIT_LONG: float = 85.0  # BACKTEST_CHANGE_126: RSI(2) above this = exit LONG
RSI2_EXIT_SHORT: float = 15.0  # BACKTEST_CHANGE_126: RSI(2) below this = exit SHORT
RSI2_SCORE_BONUS: int = 20  # BACKTEST_CHANGE_126: Score bonus for RSI(2) extreme
RSI2_TIMEFRAME: str = "1h"  # BACKTEST_CHANGE_126: TF for RSI(2) — daily too slow for crypto, use 1h
```

### Implementation Location

**File**: `ez_indicators.py`, in `compute_indicators_for_timeframe()` — add RSI(2) computation:
```python
rsi2 = rsi_value(close_series, 2)
if rsi2 is not None:
    result[f"rsi_2_{timeframe}"] = round(rsi2, 2)
```

**File**: `ez_positions_quick.py`, function `rate()`, entry scoring section

### Entry Logic

```python
if getattr(config, 'RSI2_MEAN_REVERSION_ENABLED', False) and not is_exit:
    _rsi2_tf = config.RSI2_TIMEFRAME
    _rsi2 = safe_fetch_float(ind.get(f'rsi_2_{_rsi2_tf}'), 50)
    if is_long and _rsi2 < config.RSI2_LONG_THRESHOLD:
        score += config.RSI2_SCORE_BONUS
        reasons.append(f"RSI2_EXTREME_LONG({_rsi2:.1f}<{config.RSI2_LONG_THRESHOLD})")
    elif not is_long and _rsi2 > config.RSI2_SHORT_THRESHOLD:
        score += config.RSI2_SCORE_BONUS
        reasons.append(f"RSI2_EXTREME_SHORT({_rsi2:.1f}>{config.RSI2_SHORT_THRESHOLD})")
```

### Exit Logic

```python
if getattr(config, 'RSI2_MEAN_REVERSION_ENABLED', False) and is_exit and gain > 0:
    _rsi2 = safe_fetch_float(ind.get(f'rsi_2_{config.RSI2_TIMEFRAME}'), 50)
    if is_long and _rsi2 > config.RSI2_EXIT_LONG:
        return 100, "REDUCE", f"RSI2_EXIT_LONG(rsi2={_rsi2:.1f})"
    elif not is_long and _rsi2 < config.RSI2_EXIT_SHORT:
        return 100, "REDUCE", f"RSI2_EXIT_SHORT(rsi2={_rsi2:.1f})"
```

### Expected Impact

- **Pro**: 91% WR is exceptional. Ultra-clean signal. Aligns with system's mean-reversion core.
- **Con**: 0.82% avg gain is barely above our 0.5% TP. 33% drawdown means some trades sit underwater for long periods — BUT system has STRICT_NO_LOSS so that is fine (we hold until profit).
- **Risk**: On daily TF, too slow for crypto. Adapt to 1h. RSI(2) on 1h will fire more often but with lower WR.
- **Recommendation**: Score bonus only, not a standalone strategy. Complements existing `RSI_ENTRY_GATE_ENABLED` (RSI<37 on 14-period). RSI(2)<15 is a much rarer/stronger signal.

---

## STRATEGY 3: EMA200 + StochRSI Reversal

**Source**: Trend-filtered reversal with candle body confirmation.

### Config Parameters

```python
# config.py — TradingConfig dataclass
EMA200_STOCHRSI_ENABLED: bool = False  # BACKTEST_CHANGE_127: EMA200 trend + StochRSI reversal entry
EMA200_STOCHRSI_K_LONG: float = 20.0  # BACKTEST_CHANGE_127: StochRSI K below this for LONG
EMA200_STOCHRSI_K_SHORT: float = 80.0  # BACKTEST_CHANGE_127: StochRSI K above this for SHORT
EMA200_STOCHRSI_BODY_MULT: float = 1.05  # BACKTEST_CHANGE_127: Candle body must be 5%+ larger than prev
EMA200_STOCHRSI_SCORE_BONUS: int = 25  # BACKTEST_CHANGE_127: Score bonus when all conditions met
EMA200_STOCHRSI_TF: str = "1h"  # BACKTEST_CHANGE_127: Timeframe for StochRSI check
```

### Implementation Location

**File**: `ez_indicators.py` — add StochRSI computation:
```python
# StochRSI = Stochastic of RSI(14) over 14 periods, then smooth K(3), D(3)
rsi14 = rsi_series(close_series, 14)
if rsi14 is not None and len(rsi14) >= 14:
    rsi_min = rsi14.rolling(14).min()
    rsi_max = rsi14.rolling(14).max()
    denom = rsi_max - rsi_min
    stochrsi_raw = ((rsi14 - rsi_min) / denom).where(denom > 0, 0.5) * 100
    stochrsi_k = stochrsi_raw.rolling(3).mean()
    stochrsi_d = stochrsi_k.rolling(3).mean()
    result[f"stochrsi_k_{timeframe}"] = round(float(stochrsi_k.iloc[-1]), 2)
    result[f"stochrsi_d_{timeframe}"] = round(float(stochrsi_d.iloc[-1]), 2)
    result[f"stochrsi_k_{timeframe}_prev"] = round(float(stochrsi_k.iloc[-2]), 2)
    # Crossover flags
    result[f"stochrsi_crossover_{timeframe}"] = bool(stochrsi_k.iloc[-1] > stochrsi_d.iloc[-1] and stochrsi_k.iloc[-2] <= stochrsi_d.iloc[-2])
    result[f"stochrsi_crossunder_{timeframe}"] = bool(stochrsi_k.iloc[-1] < stochrsi_d.iloc[-1] and stochrsi_k.iloc[-2] >= stochrsi_d.iloc[-2])
```

Also need candle body ratio:
```python
# In compute_indicators_for_timeframe
body = abs(float(close_series.iloc[-1]) - float(open_series.iloc[-1]))
body_prev = abs(float(close_series.iloc[-2]) - float(open_series.iloc[-2]))
result[f"candle_body_{timeframe}"] = body
result[f"candle_body_prev_{timeframe}"] = body_prev
result[f"candle_body_ratio_{timeframe}"] = round(body / body_prev, 3) if body_prev > 0 else 1.0
```

**File**: `ez_positions_quick.py`, function `rate()`

### Entry Logic

```python
if getattr(config, 'EMA200_STOCHRSI_ENABLED', False) and not is_exit:
    _tf = config.EMA200_STOCHRSI_TF
    _sma200 = safe_fetch_float(ind.get(f'sma_200_{_tf}'), 0)  # Using SMA200 as EMA200 proxy until EMA200 added
    _stochrsi_k = safe_fetch_float(ind.get(f'stochrsi_k_{_tf}'), 50)
    _stochrsi_crossover = ind.get(f'stochrsi_crossover_{_tf}', False)
    _stochrsi_crossunder = ind.get(f'stochrsi_crossunder_{_tf}', False)
    _body_ratio = safe_fetch_float(ind.get(f'candle_body_ratio_{_tf}'), 1.0)
    if _sma200 > 0:
        if is_long and current_price > _sma200 and _stochrsi_k < config.EMA200_STOCHRSI_K_LONG and _stochrsi_crossover and _body_ratio >= config.EMA200_STOCHRSI_BODY_MULT:
            score += config.EMA200_STOCHRSI_SCORE_BONUS
            reasons.append(f"EMA200_STOCHRSI_LONG(k={_stochrsi_k:.0f},body={_body_ratio:.2f}x)")
        elif not is_long and current_price < _sma200 and _stochrsi_k > config.EMA200_STOCHRSI_K_SHORT and _stochrsi_crossunder and _body_ratio >= config.EMA200_STOCHRSI_BODY_MULT:
            score += config.EMA200_STOCHRSI_SCORE_BONUS
            reasons.append(f"EMA200_STOCHRSI_SHORT(k={_stochrsi_k:.0f},body={_body_ratio:.2f}x)")
```

### Expected Impact

- **Pro**: Strong trend filter (EMA200) + oversold reversal + candle confirmation = high-conviction.
- **Con**: Candle body 5%+ larger than prev is restrictive. May fire rarely.
- **Risk**: StochRSI is noisier than Stochastic — more false signals in choppy markets.
- **Recommendation**: Start with `EMA200_STOCHRSI_TF = "1h"` for cleaner signals. The candle body filter is the real edge here — it confirms institutional participation.

---

## STRATEGY 4: EMA Pullback + StochRSI (MOST RELEVANT)

**Source**: Pullback-in-trend. Matches the "SMA200 retest-and-launch" edge from MEMORY.md.

### Config Parameters

```python
# config.py — TradingConfig dataclass
EMA_PULLBACK_ENABLED: bool = False  # BACKTEST_CHANGE_128: EMA pullback + StochRSI — pullback in trend entry
EMA_PULLBACK_STOCHRSI_LONG: float = 20.0  # BACKTEST_CHANGE_128: StochRSI below this for LONG pullback
EMA_PULLBACK_STOCHRSI_SHORT: float = 80.0  # BACKTEST_CHANGE_128: StochRSI above this for SHORT pullback
EMA_PULLBACK_SCORE_BONUS: int = 35  # BACKTEST_CHANGE_128: High score — this matches our core edge
EMA_PULLBACK_TF: str = "15m"  # BACKTEST_CHANGE_128: 15m for pullback detection (3m too noisy)
```

### Implementation Location

**File**: `ez_indicators.py` — add EMA(9), EMA(14) to the `"ema"` lists:
```python
# Change from:
"3m": {"seconds": 180, "half": 90, "dc_window": 20, "ema": [20], ...}
# To:
"3m": {"seconds": 180, "half": 90, "dc_window": 20, "ema": [9, 14, 20], ...}
# Same for all TFs
```

This will produce `ema_9_{tf}`, `ema_14_{tf}`, `ema_20_{tf}` in indicator output.

**File**: `ez_positions_quick.py`, function `rate()`

### Entry Logic

```python
if getattr(config, 'EMA_PULLBACK_ENABLED', False) and not is_exit:
    _tf = config.EMA_PULLBACK_TF
    _ema9 = safe_fetch_float(ind.get(f'ema_9_{_tf}'), 0)
    _ema14 = safe_fetch_float(ind.get(f'ema_14_{_tf}'), 0)
    _ema20 = safe_fetch_float(ind.get(f'ema_20_{_tf}'), 0)
    _stochrsi_k = safe_fetch_float(ind.get(f'stochrsi_k_{_tf}'), 50)
    if _ema9 > 0 and _ema14 > 0 and _ema20 > 0:
        # LONG pullback: price above EMA20 (in uptrend) but pulled back below EMA9 and EMA14
        if is_long and current_price > _ema20 and current_price < _ema9 and current_price < _ema14 and _stochrsi_k < config.EMA_PULLBACK_STOCHRSI_LONG:
            score += config.EMA_PULLBACK_SCORE_BONUS
            reasons.append(f"EMA_PULLBACK_LONG(price>{_ema20:.2f},<ema9={_ema9:.2f},stochrsi={_stochrsi_k:.0f})")
        # SHORT pullback: price below EMA20 (in downtrend) but bounced above EMA9 and EMA14
        elif not is_long and current_price < _ema20 and current_price > _ema9 and current_price > _ema14 and _stochrsi_k > config.EMA_PULLBACK_STOCHRSI_SHORT:
            score += config.EMA_PULLBACK_SCORE_BONUS
            reasons.append(f"EMA_PULLBACK_SHORT(price<{_ema20:.2f},>ema9={_ema9:.2f},stochrsi={_stochrsi_k:.0f})")
```

### Expected Impact

- **Pro**: This IS our core edge. The "SMA200 retest-and-launch" insight says the real signal is price returning to a base (EMA20) after being far away, then launching. This strategy captures exactly that — pullback to EMA9/14 within an EMA20 uptrend, confirmed by oversold StochRSI.
- **Con**: Requires 3 new EMA computations per TF (9, 14 already proposed). Marginal CPU cost.
- **Risk**: In sideways markets, price oscillates around EMAs causing false pullback signals. StochRSI filter mitigates this.
- **Recommendation**: **HIGHEST PRIORITY for implementation.** Score bonus of 35 (highest of all strategies) because it matches the empirically validated edge. Use 15m TF to balance signal quality vs frequency.

---

## STRATEGY 5: RSI-MACD-EMA with ATR Adaptive Stop

**Source**: Relaxed RSI thresholds (35/65 not 30/70) generate more trades. ATR-based stops outperform fixed %.

### Config Parameters

```python
# config.py — TradingConfig dataclass
RSI_MACD_EMA_ENABLED: bool = False  # BACKTEST_CHANGE_129: RSI+MACD+EMA9 cross combined entry
RSI_MACD_EMA_RSI_LONG: float = 35.0  # BACKTEST_CHANGE_129: RELAXED RSI — 35 not 30 (more trades, similar WR)
RSI_MACD_EMA_RSI_SHORT: float = 65.0  # BACKTEST_CHANGE_129: RELAXED RSI — 65 not 70
RSI_MACD_EMA_SCORE_BONUS: int = 25  # BACKTEST_CHANGE_129: Score bonus when all 3 align
ATR_ADAPTIVE_STOP_ENABLED: bool = False  # BACKTEST_CHANGE_130: ATR-based stop instead of fixed %
ATR_ADAPTIVE_STOP_MULT: float = 2.0  # BACKTEST_CHANGE_130: SL at ATR(14) x 2.0 from entry
ATR_ADAPTIVE_STOP_TF: str = "1h"  # BACKTEST_CHANGE_130: ATR TF for stop calculation
```

### Implementation Location

**Entry**: `ez_positions_quick.py`, function `rate()`
**ATR Stop**: `ez_manage.py` or `ez_gain_protector.py` — wherever stop logic lives

### Entry Logic

```python
if getattr(config, 'RSI_MACD_EMA_ENABLED', False) and not is_exit:
    _rsi = safe_fetch_float(ind.get(f'rsi_{config.TF_FOCUS}'), 50)
    _ema9 = safe_fetch_float(ind.get(f'ema_9_{config.TF_FOCUS}'), 0)
    _ema9_prev = safe_fetch_float(ind.get(f'ema_9_{config.TF_FOCUS}_prev'), 0)  # Need prev value
    _macd_crossover = ind.get(f'macd_crossover_{config.TF_FOCUS}', False)
    _macd_crossunder = ind.get(f'macd_crossunder_{config.TF_FOCUS}', False)
    if _ema9 > 0:
        # LONG: price crosses ABOVE EMA9 AND MACD crosses above signal AND RSI < 35
        _price_cross_above_ema9 = current_price > _ema9  # Simplified — ideally check prev_price < _ema9_prev
        _price_cross_below_ema9 = current_price < _ema9
        if is_long and _price_cross_above_ema9 and _macd_crossover and _rsi < config.RSI_MACD_EMA_RSI_LONG:
            score += config.RSI_MACD_EMA_SCORE_BONUS
            reasons.append(f"RSI_MACD_EMA_LONG(rsi={_rsi:.0f},ema9_cross,macd_cross)")
        elif not is_long and _price_cross_below_ema9 and _macd_crossunder and _rsi > config.RSI_MACD_EMA_RSI_SHORT:
            score += config.RSI_MACD_EMA_SCORE_BONUS
            reasons.append(f"RSI_MACD_EMA_SHORT(rsi={_rsi:.0f},ema9_cross,macd_cross)")
```

### ATR Adaptive Stop Logic

```python
# In ez_manage.py or wherever stop/SL is evaluated
if getattr(config, 'ATR_ADAPTIVE_STOP_ENABLED', False):
    _atr = safe_fetch_float(ind.get(f'atr_{config.ATR_ADAPTIVE_STOP_TF}'), 0)
    if _atr > 0 and entry_price > 0:
        _atr_stop_dist = _atr * config.ATR_ADAPTIVE_STOP_MULT
        if is_long:
            _atr_stop_price = entry_price - _atr_stop_dist
            _atr_stop_pct = ((current_price - _atr_stop_price) / _atr_stop_price - 1) * 100
        else:
            _atr_stop_price = entry_price + _atr_stop_dist
            _atr_stop_pct = ((_atr_stop_price - current_price) / current_price - 1) * 100
        # NOTE: Under STRICT_NO_LOSS, this only applies as a WARNING or position-size reduction
        # NOT as an actual stop-loss exit. L/S ratio IS the hedge.
```

### Expected Impact

- **Pro**: Relaxed RSI (35/65) generates ~40% more trades than strict (30/70) per existing research. Current system uses RSI<37 (`RSI_ENTRY_MAX_LONG`), so this is already close. ATR stops adapt to volatility.
- **Con**: Under STRICT_NO_LOSS, ATR stops cannot trigger exits. Use for sizing reduction instead.
- **Risk**: Relaxed thresholds produce more trades but slightly lower per-trade WR.
- **Recommendation**: The relaxed RSI insight is already partially implemented (system uses 37, not 30). ATR adaptive stop is useful for SIZING, not exits. Reduce position size when ATR-implied risk is high.

---

## STRATEGY 6: MACD Below-Zero Crossover + EMA200

**Source**: 60% WR, 200 trades. MACD crossover below zero = early reversal signal.

### Config Parameters

```python
# config.py — TradingConfig dataclass
MACD_ZERO_CROSS_ENABLED: bool = False  # BACKTEST_CHANGE_131: MACD below-zero crossover + EMA200 trend
MACD_ZERO_CROSS_SCORE_BONUS: int = 20  # BACKTEST_CHANGE_131: Score bonus
MACD_ZERO_CROSS_TF: str = "1h"  # BACKTEST_CHANGE_131: TF — 1h for signal quality
MACD_ZERO_CROSS_TP_MULT: float = 1.5  # BACKTEST_CHANGE_131: TP at 1.5x SL distance (R:R)
```

### Implementation Location

**File**: `ez_positions_quick.py`, function `rate()`

### Entry Logic

```python
if getattr(config, 'MACD_ZERO_CROSS_ENABLED', False) and not is_exit:
    _tf = config.MACD_ZERO_CROSS_TF
    _sma200 = safe_fetch_float(ind.get(f'sma_200_{_tf}'), 0)
    _macd = safe_fetch_float(ind.get(f'macd_{_tf}'), 0)
    _macd_crossover = ind.get(f'macd_crossover_{_tf}', False)
    _macd_crossunder = ind.get(f'macd_crossunder_{_tf}', False)
    if _sma200 > 0:
        # LONG: Price > SMA200 AND MACD crosses above signal AND crossover happens BELOW zero line
        if is_long and current_price > _sma200 and _macd_crossover and _macd < 0:
            score += config.MACD_ZERO_CROSS_SCORE_BONUS
            reasons.append(f"MACD_ZERO_CROSS_LONG(macd={_macd:.4f}<0,price>sma200)")
        # SHORT: Price < SMA200 AND MACD crosses below signal AND crossover happens ABOVE zero line
        elif not is_long and current_price < _sma200 and _macd_crossunder and _macd > 0:
            score += config.MACD_ZERO_CROSS_SCORE_BONUS
            reasons.append(f"MACD_ZERO_CROSS_SHORT(macd={_macd:.4f}>0,price<sma200)")
```

### Expected Impact

- **Pro**: Below-zero crossover is a higher-quality signal than standard MACD cross — it catches the early phase of a reversal before MACD goes positive.
- **Con**: 60% WR is below our system's 95%+ target. Only useful as a score bonus, not a standalone entry.
- **Risk**: In strong downtrends, MACD can cross above signal below zero multiple times (head fakes).
- **Recommendation**: Moderate priority. The "below zero" filter is the key insight — standard MACD crosses are too noisy. Use 1h TF.

---

## STRATEGY 7: BB Breakout + EMA200

**Source**: Highest WR in trends. Breakout, NOT mean reversion.

### Config Parameters

```python
# config.py — TradingConfig dataclass
BB_BREAKOUT_ENABLED: bool = False  # BACKTEST_CHANGE_132: BB breakout + EMA200 trend filter
BB_BREAKOUT_SCORE_BONUS: int = 20  # BACKTEST_CHANGE_132: Score bonus for breakout
BB_BREAKOUT_TF: str = "1h"  # BACKTEST_CHANGE_132: TF for BB breakout detection
BB_BREAKOUT_REGIME_FILTER: bool = True  # BACKTEST_CHANGE_132: Only allow in trending regime
```

### Implementation Location

**File**: `ez_positions_quick.py`, function `rate()`

### Entry Logic

```python
if getattr(config, 'BB_BREAKOUT_ENABLED', False) and not is_exit:
    _tf = config.BB_BREAKOUT_TF
    _sma200 = safe_fetch_float(ind.get(f'sma_200_{_tf}'), 0)
    _bb_upper = safe_fetch_float(ind.get(f'bb_upper_{_tf}'), 0)
    _bb_lower = safe_fetch_float(ind.get(f'bb_lower_{_tf}'), 0)
    _close = current_price  # Using current_price as close proxy
    if _sma200 > 0 and _bb_upper > 0 and _bb_lower > 0:
        # LONG breakout: Price > EMA200 AND candle CLOSES above upper BB
        if is_long and _close > _sma200 and _close > _bb_upper:
            score += config.BB_BREAKOUT_SCORE_BONUS
            reasons.append(f"BB_BREAKOUT_LONG(close>{_bb_upper:.2f},>sma200)")
        # SHORT breakout: Price < EMA200 AND candle closes below lower BB
        elif not is_long and _close < _sma200 and _close < _bb_lower:
            score += config.BB_BREAKOUT_SCORE_BONUS
            reasons.append(f"BB_BREAKOUT_SHORT(close<{_bb_lower:.2f},<sma200)")
```

### Expected Impact

- **Pro**: Breakout entries in trends capture big moves. Complements the mean-reversion core.
- **Con**: **CONFLICTS with existing BB mean-reversion logic** (`BB_ENTRY_LONG_THRESHOLD = -0.2`). The existing system buys when BB%B is LOW (mean reversion). This strategy buys when price CLOSES ABOVE upper BB (breakout). These are OPPOSITE signals.
- **Risk**: Must NOT be enabled simultaneously with `BB_ENTRY_LONG_THRESHOLD` mean-reversion. Requires market regime detection.
- **Recommendation**: Low priority until market regime filter exists. The system's mean-reversion core is working (97%+ WR). Adding breakout entries without regime awareness will degrade performance. Could be useful for a dedicated trend-following account (e.g., `flz`).

---

## STRATEGY 8: Donchian Channel Breakout

**Source**: 30% WR in ranging, 60%+ in trends. Must combine with trend filter.

### Config Parameters

```python
# config.py — TradingConfig dataclass
DC_BREAKOUT_ENTRY_ENABLED: bool = False  # BACKTEST_CHANGE_133: Donchian breakout entry (trend-following)
DC_BREAKOUT_SCORE_BONUS: int = 15  # BACKTEST_CHANGE_133: Score bonus (conservative — 30% WR in ranging)
DC_BREAKOUT_TF: str = "1h"  # BACKTEST_CHANGE_133: TF for breakout detection
DC_BREAKOUT_TREND_REQUIRED: bool = True  # BACKTEST_CHANGE_133: Must have HTF trend alignment
```

### Implementation Location

**File**: `ez_positions_quick.py`, function `rate()`

### Entry Logic

```python
if getattr(config, 'DC_BREAKOUT_ENTRY_ENABLED', False) and not is_exit:
    _tf = config.DC_BREAKOUT_TF
    _dc_high = safe_fetch_float(ind.get(f'dc_high_{_tf}'), 0)
    _dc_low = safe_fetch_float(ind.get(f'dc_low_{_tf}'), 0)
    _dc_high_prev = safe_fetch_float(ind.get(f'dc_high_{_tf}_prev'), 0)
    _dc_low_prev = safe_fetch_float(ind.get(f'dc_low_{_tf}_prev'), 0)
    # Only allow with trend alignment (30% WR without, 60%+ with)
    _trend_ok = not config.DC_BREAKOUT_TREND_REQUIRED or htf_trend_bullish if is_long else htf_trend_bearish
    if _dc_high > 0 and _dc_low > 0 and _trend_ok:
        # LONG: Price breaks above upper DC(20)
        if is_long and current_price > _dc_high and (prev_cross_price <= _dc_high_prev if _dc_high_prev > 0 else True):
            score += config.DC_BREAKOUT_SCORE_BONUS
            reasons.append(f"DC_BREAKOUT_LONG(price>{_dc_high:.2f})")
        # SHORT: Price breaks below lower DC(20)
        elif not is_long and current_price < _dc_low and (prev_cross_price >= _dc_low_prev if _dc_low_prev > 0 else True):
            score += config.DC_BREAKOUT_SCORE_BONUS
            reasons.append(f"DC_BREAKOUT_SHORT(price<{_dc_low:.2f})")
```

### Expected Impact

- **Pro**: DC breakout is already partially in the system (DC channels computed, DC width sizing exists). This adds directional entry on breakout.
- **Con**: 30% WR in ranging markets is terrible. Must have trend filter or will destroy PnL.
- **Risk**: The system already uses DC for sizing (`DC_EDGE_SIZING_ENABLED`). Adding DC breakout entry on top creates correlation — both signals fire at DC edges.
- **Recommendation**: Low priority. The existing `DC_EDGE_SIZING` already captures the trend edge by sizing up at DC extremes. Adding a separate breakout entry is redundant. Only consider for dedicated trend account.

---

## STRATEGY 9: BB + RSI + Stoch Scalp

**Source**: 65-70% WR with strict rules. Fixed 0.15% SL / 0.3% TP (2:1 RR).

### Config Parameters

```python
# config.py — TradingConfig dataclass
BB_RSI_STOCH_SCALP_ENABLED: bool = False  # BACKTEST_CHANGE_134: BB+RSI+Stoch triple-confirmed scalp
BB_RSI_STOCH_SCALP_RSI_LONG: float = 30.0  # BACKTEST_CHANGE_134: RSI below this for LONG scalp
BB_RSI_STOCH_SCALP_RSI_SHORT: float = 70.0  # BACKTEST_CHANGE_134: RSI above this for SHORT scalp
BB_RSI_STOCH_SCALP_STOCH_LONG: float = 20.0  # BACKTEST_CHANGE_134: Stoch K below this for LONG scalp
BB_RSI_STOCH_SCALP_STOCH_SHORT: float = 80.0  # BACKTEST_CHANGE_134: Stoch K above this for SHORT scalp
BB_RSI_STOCH_SCALP_TP_PCT: float = 0.3  # BACKTEST_CHANGE_134: Fixed 0.3% TP for scalp
BB_RSI_STOCH_SCALP_SCORE_BONUS: int = 25  # BACKTEST_CHANGE_134: Score bonus for scalp signal
BB_RSI_STOCH_SCALP_ACCOUNTS: list = field(default_factory=lambda: ["ang", "men", "flz"])  # Scalp accounts only
```

### Implementation Location

**File**: `ez_positions_quick.py`, function `rate()`

### Entry Logic

```python
if getattr(config, 'BB_RSI_STOCH_SCALP_ENABLED', False) and not is_exit and account_key in getattr(config, 'BB_RSI_STOCH_SCALP_ACCOUNTS', []):
    _tf = config.TF_FOCUS  # 3m for scalp
    _bb_pct = safe_fetch_float(ind.get(f'bb_pct_b_{_tf}', ind.get(f'bb_pct_{_tf}')), 0.5)
    _rsi = safe_fetch_float(ind.get(f'rsi_{_tf}'), 50)
    _stoch_k = safe_fetch_float(ind.get(f'stoch_k_{_tf}'), 50)
    # LONG: Price touches lower BB AND RSI < 30 AND Stoch K < 20
    if is_long and _bb_pct < 0.0 and _rsi < config.BB_RSI_STOCH_SCALP_RSI_LONG and _stoch_k < config.BB_RSI_STOCH_SCALP_STOCH_LONG:
        score += config.BB_RSI_STOCH_SCALP_SCORE_BONUS
        reasons.append(f"BB_RSI_STOCH_SCALP_LONG(bb={_bb_pct:.2f},rsi={_rsi:.0f},k={_stoch_k:.0f})")
    # SHORT: Price touches upper BB AND RSI > 70 AND Stoch K > 80
    elif not is_long and _bb_pct > 1.0 and _rsi > config.BB_RSI_STOCH_SCALP_RSI_SHORT and _stoch_k > config.BB_RSI_STOCH_SCALP_STOCH_SHORT:
        score += config.BB_RSI_STOCH_SCALP_SCORE_BONUS
        reasons.append(f"BB_RSI_STOCH_SCALP_SHORT(bb={_bb_pct:.2f},rsi={_rsi:.0f},k={_stoch_k:.0f})")
```

### Expected Impact

- **Pro**: Triple confirmation (BB + RSI + Stoch) with 2:1 RR is clean. Aligns with existing mean-reversion core. Similar to Strategy 1 but uses BB instead of MACD.
- **Con**: 65-70% WR is below system's 95%+ target. The 0.3% TP is close to system's current 0.5% (`NOLOSS_MIN_PROFIT_PCT`).
- **Risk**: The 0.15% SL conflicts with STRICT_NO_LOSS. Cannot use the SL component.
- **Recommendation**: Medium priority. The entry signal (triple confirmation at BB extreme) is valuable as a score bonus. Ignore the fixed SL/TP — use system's existing exit logic (stoch cross, tiered TP, NOLOSS).

---

## CROSS-CUTTING INSIGHTS — Implementation Notes

### 1. ATR-Based Stops Outperform Fixed % in Crypto

**Current state**: System uses `STRICT_NO_LOSS` — no stop losses at all. L/S ratio IS the hedge.

**How to use ATR**: NOT as a stop-loss, but as a **sizing factor**. When ATR-implied risk is high (wide ATR), reduce position size. When ATR is narrow (low vol), allow larger positions.

**Already partially implemented**: `ENTRY_ATR_PCT_MIN = 1.5` (BACKTEST_CHANGE_103) blocks entries when ATR% < 1.5%. This is the right approach.

**Enhancement**: Scale sizing by ATR inversely:
```python
# In calculate_final_order_quantity
if getattr(config, 'ATR_ADAPTIVE_SIZING_ENABLED', False):
    _atr_pct = (_atr / current_price) * 100
    # Target: 2% ATR = 1.0x, 4% ATR = 0.5x, 1% ATR = 2.0x (capped)
    _atr_size_mult = min(2.0, max(0.5, 2.0 / _atr_pct))
    sizing_score += _atr_size_mult * 10  # Weighted contribution
```

Config:
```python
ATR_ADAPTIVE_SIZING_ENABLED: bool = False  # BACKTEST_CHANGE_135
ATR_ADAPTIVE_SIZING_TARGET_PCT: float = 2.0  # BACKTEST_CHANGE_135: Target ATR%. Size=1x at this ATR.
```

### 2. Relaxed RSI Thresholds (35/65) Generate More Trades

**Current state**: `RSI_ENTRY_MAX_LONG = 37.0` (BACKTEST_CHANGE_111). Already relaxed.

**Insight validated**: The system's existing RSI gate at 37 is in the optimal zone (35-40). No change needed. The research confirms the current setting.

### 3. MACD Exit (Cross Back) Lets Winners Run vs Fixed TP%

**Current state**: System uses tiered TP (0.15% to 3%) + stoch cross exit + NOLOSS min profit.

**How to use**: Add MACD cross-back as an ALTERNATIVE exit signal alongside existing exits. When MACD crosses against the position direction AND position is profitable, allow exit even if other conditions are not met.

**Implementation**: See Strategy 1 exit logic above. This should be a supplementary exit, not a replacement.

Config:
```python
MACD_EXIT_ENABLED: bool = False  # BACKTEST_CHANGE_136: MACD cross-back exit for profitable positions
MACD_EXIT_MIN_GAIN: float = 0.3  # BACKTEST_CHANGE_136: Min gain% before MACD exit allowed
MACD_EXIT_TF: str = "15m"  # BACKTEST_CHANGE_136: TF for MACD exit signal
```

### 4. Market Regime Filter Essential

**Current state**: No explicit regime detection. HTF trend scoring provides partial regime info.

**How to implement**: ADX(14) > 25 = trending, < 20 = ranging. BB squeeze (`BB_SQUEEZE_THRESHOLD_1H`) already partially captures this.

**Enhancement**: Add explicit regime classification:
```python
# In ez_indicators.py — new indicator
# ADX(14) computation
# result[f"adx_{timeframe}"] = adx_value
# result[f"regime_{timeframe}"] = "TRENDING" if adx > 25 else "RANGING" if adx < 20 else "TRANSITIONING"
```

Config:
```python
REGIME_FILTER_ENABLED: bool = False  # BACKTEST_CHANGE_137: Market regime filter
REGIME_ADX_TRENDING: float = 25.0  # BACKTEST_CHANGE_137: ADX above this = trending
REGIME_ADX_RANGING: float = 20.0  # BACKTEST_CHANGE_137: ADX below this = ranging
REGIME_MEAN_REVERT_ONLY_IN_RANGE: bool = True  # BACKTEST_CHANGE_137: Mean reversion only in ranging
REGIME_BREAKOUT_ONLY_IN_TREND: bool = True  # BACKTEST_CHANGE_137: Breakout only in trending
```

This is the **single highest-impact cross-cutting improvement**. All 9 strategies degrade when applied in the wrong regime. Strategy 1 drops from 73% to 55% in trends. Strategies 7/8 drop from 60%+ to 30% in ranges.

### 5. EMA200/SMA200 Trend Filter Is Universal

**Current state**: SMA200 computed per TF. Used in `SMA200_DIST_ENTRY_ENABLED` and sizing factors.

**Insight validated**: All profitable strategies use price vs EMA/SMA200 as a trend filter. The system already does this. No change needed — current implementation is correct.

---

## PRIORITY RANKING

| Priority | Strategy | BACKTEST_CHANGE | Rationale |
|----------|---------|----------------|-----------|
| **1 (HIGHEST)** | Strategy 4: EMA Pullback + StochRSI | 128 | Matches core "retest-and-launch" edge. Pullback-in-trend is the highest-quality entry. |
| **2** | Cross-cutting: Market Regime Filter | 137 | All strategies need this. Single biggest WR improvement across the board. |
| **3** | Strategy 9: BB+RSI+Stoch Scalp | 134 | Triple confirmation at BB extreme. Aligns with mean-reversion core. Good for scalp accounts. |
| **4** | Strategy 1: MACD+RSI+Stoch Triple | 125 | High-confidence entries but low frequency. Best as score bonus. |
| **5** | Cross-cutting: ATR Adaptive Sizing | 135 | Better risk management through sizing, not stops. |
| **6** | Strategy 2: RSI(2) Mean Reversion | 126 | 91% WR but tiny gains. Good supplementary signal. |
| **7** | Strategy 6: MACD Below-Zero Cross | 131 | Clean reversal signal but 60% WR needs more validation. |
| **8** | Strategy 5: RSI-MACD-EMA | 129 | Largely redundant with existing RSI gate (37) + other signals. |
| **9** | Strategy 3: EMA200+StochRSI | 127 | Good signal but candle body filter makes it too rare. |
| **10 (LOWEST)** | Strategy 7: BB Breakout | 132 | Conflicts with mean-reversion core. Needs regime filter first. |
| **11 (LOWEST)** | Strategy 8: DC Breakout | 133 | Redundant with DC_EDGE_SIZING. 30% WR in ranging is dangerous. |

---

## INDICATOR PREREQUISITES (Must implement first)

Before any strategy can be enabled, these indicators need to be added to `ez_indicators.py`:

| Indicator | Function | Keys Produced | Needed By |
|-----------|----------|--------------|-----------|
| **EMA(9), EMA(14)** | Add to `"ema"` list in TIMEFRAMES | `ema_9_{tf}`, `ema_14_{tf}` | Strategies 4, 5 |
| **MACD(12,26,9)** | New computation block | `macd_{tf}`, `macd_signal_{tf}`, `macd_hist_{tf}`, `macd_crossover_{tf}`, `macd_crossunder_{tf}` | Strategies 1, 5, 6, MACD exit |
| **StochRSI(14,14,3,3)** | New computation block | `stochrsi_k_{tf}`, `stochrsi_d_{tf}`, `stochrsi_crossover_{tf}`, `stochrsi_crossunder_{tf}` | Strategies 3, 4 |
| **RSI(2)** | Add `rsi_value(close, 2)` call | `rsi_2_{tf}` | Strategy 2 |
| **Candle body ratio** | New computation | `candle_body_ratio_{tf}` | Strategy 3 |
| **ADX(14)** | New computation | `adx_{tf}`, `regime_{tf}` | Regime filter |

**CPU impact estimate**: Each new indicator adds ~0.5ms per symbol per TF per cycle. With 350 symbols x 5 TFs x 6 new indicators = ~5.25s additional per cycle. Acceptable on 3m TF (180s cycle).

---

## BACKTEST_CHANGE NUMBER ALLOCATION

| Number | Strategy/Feature | Status |
|--------|-----------------|--------|
| 125 | Strategy 1: MACD+RSI+Stoch Triple Confirmation | Proposed |
| 126 | Strategy 2: RSI(2) Mean Reversion | Proposed |
| 127 | Strategy 3: EMA200+StochRSI Reversal | Proposed |
| 128 | Strategy 4: EMA Pullback+StochRSI (HIGHEST PRIORITY) | Proposed |
| 129 | Strategy 5: RSI-MACD-EMA Combined Entry | Proposed |
| 130 | Strategy 5: ATR Adaptive Stop | Proposed |
| 131 | Strategy 6: MACD Below-Zero Crossover | Proposed |
| 132 | Strategy 7: BB Breakout + EMA200 | Proposed |
| 133 | Strategy 8: DC Breakout Entry | Proposed |
| 134 | Strategy 9: BB+RSI+Stoch Scalp | Proposed |
| 135 | Cross-cutting: ATR Adaptive Sizing | Proposed |
| 136 | Cross-cutting: MACD Exit for Winners | Proposed |
| 137 | Cross-cutting: Market Regime Filter (ADX) | Proposed |

---

## IMPLEMENTATION ORDER

1. **Phase A — Indicators** (ez_indicators.py): Add EMA(9,14), MACD, StochRSI, RSI(2), candle body ratio, ADX. All disabled by default.
2. **Phase B — Regime Filter** (BACKTEST_CHANGE_137): Add ADX-based regime classification. This unlocks all strategies.
3. **Phase C — Strategy 4** (BACKTEST_CHANGE_128): EMA Pullback + StochRSI. Highest-priority entry signal.
4. **Phase D — Strategy 9** (BACKTEST_CHANGE_134): BB+RSI+Stoch scalp for scalp accounts.
5. **Phase E — Strategy 1** (BACKTEST_CHANGE_125): Triple confirmation as score bonus.
6. **Phase F — Remaining**: Strategies 2,3,5,6 as score bonuses. Strategies 7,8 only if regime filter proves reliable.

All new features default to `False`/disabled. Enable one at a time in backtests. Follow GENERAL FIRST philosophy — validate across all 350+ symbols before enabling.

---
---

# PART 2: EXTENDED RESEARCH (Academic, Reddit, Twitter/X, Copy Trading, Funding Rate, Regime Detection)

> Generated from 8 parallel research agents covering YouTube, Reddit, Twitter/X, academic papers, copy trading platforms, funding rates, market structure, and volatility regime detection.

---

## CRITICAL CONTRADICTION: RSI IS MOMENTUM ON CRYPTO, NOT MEAN-REVERSION

**Source**: QuantifiedStrategies.com (backtested on BTC), confirmed by multiple crypto-specific studies.

Traditional RSI mean reversion (buy RSI < 30, sell RSI > 70) **does NOT work on crypto**. RSI works on crypto only as a **momentum** indicator:
- RSI > 50 = buy signal (momentum confirmation)
- RSI < 50 = sell signal

**Impact on our system**: The existing `RSI_ENTRY_MAX_LONG = 37` gate (buy when RSI < 37) may be backwards for crypto. The 9 EMA + RSI strategy backtested on BTC showed 284 trades, 2.65% avg gain — but using RSI as momentum (> 50), not oversold (< 30).

**However**: Our system uses STRICT_NO_LOSS and holds until profit. Mean-reversion RSI entries that would normally stop out at a loss instead sit underwater until they recover. This may make mean-reversion RSI viable for us specifically — needs backtest validation.

**BACKTEST_CHANGE_138**: Test RSI momentum mode (buy when RSI > 50, not < 37) as an alternative to current RSI gate.

---

## CRITICAL FINDING: MACD ALONE IS NEGATIVE EV ON CRYPTO

**Source**: QuantifiedStrategies.com — MACD histogram strategy on BTC.

| Market | Win Rate | Profit Factor |
|--------|----------|---------------|
| S&P 500 | 81% | 4.22 |
| **BTC** | **20%** | **-5.88** |

MACD crossovers alone are **negative expectancy on crypto**. The 73% WR from Strategy 1 was backtested on stocks (SPY/SMH), not crypto. On BTC specifically, MACD produces 20% win rate.

**Impact**: Strategies 1, 5, 6 (all MACD-dependent) should use MACD as **confirmation only**, never as primary signal. Reduce MACD score bonuses. The MACD exit idea (Strategy 136) is especially suspect — do not implement without crypto-specific backtest.

---

## ACADEMIC VALIDATION: WHAT ACTUALLY WORKS IN CRYPTO

### Proven with Statistical Significance

| Strategy | Sharpe | Source | Key Detail |
|----------|--------|--------|------------|
| **Donchian Channel ensemble** (9 lookbacks: 5,10,20,30,60,90,150,250,360 days) | >1.5 | Zarattini et al., SSRN 2025 | Volatility-sized, top 20 coins, out-of-sample validated |
| **Volume-weighted momentum** (VWTSMOM) | 2.17 | Huang et al., SSRN 2024 | 0.94%/day, volume confirmation is key differentiator |
| **Risk-managed momentum with stop-losses** | 1.12-1.42 | Springer 2025 | Converts negative skewness to positive |
| **Funding rate arbitrage** (spot long + perp short) | 1.8-3.5 | ScienceDirect 2025 | 38% annualized on BTC, max loss 1.92% |
| **Copula-based pairs trading** | 3.97 | Financial Innovation 2024 | Max DD 7.94%, outperforms linear cointegration |
| **RSI(14) over MACD** | N/A | ResearchGate comparative study | RSI outperformed MACD head-to-head in crypto |

### Declining Effectiveness Warning

Simple MA strategies "yielded significant profits in early subperiods yet lost effectiveness over time." Indicators must be **combined** and **regime-filtered** to maintain edge.

### ML Feature Importance (What Predicts Crypto Returns)

1. **Order book features** — 81.3% importance
2. **Blockchain/on-chain data** — high
3. **RSI, Stochastic, MACD** — moderate (in that order)
4. **Volume metrics** — moderate
5. **Macroeconomic factors** — lowest for short-term

---

## 567,000 BACKTESTS: EXIT STRATEGY META-STUDY

**Source**: KJ Trading Systems — 40 futures markets, 5 bar sizes, 10 years.

### Exit Method Ranking (Best to Worst)

1. **Stop and Reverse** (flip direction on opposing signal) — BEST
2. **Fixed dollar/percent target exits** — SECOND BEST
3. **Breakeven stops** ($500-$1000 threshold) — THIRD
4. **Complex exits** (Parabolic, Chandelier, trailing stops, MA exits) — WORST

### Key Findings

- **Combination exits (stop + target + trailing) performed WORSE than single exits**
- **Larger timeframes produced better results**: Daily > 720min > 360min > 120min > 60min
- **ATR-based exits underperformed simple dollar/percent-based exits**
- Simple fixed TP% targets beat complex trailing logic

**Impact on our system**: Validates current approach of fixed tiered TP% (0.15% to 3%). Do NOT add complex trailing stop logic. The `ez_gain_protector.py` trailing logic should be backtested against simple fixed TP to see if it adds or subtracts value.

**BACKTEST_CHANGE_139**: Test simple fixed TP% vs current tiered/trailing exit logic.

---

## COPY TRADING PLATFORM ANALYSIS: WHAT TOP 1% TRADERS DO

### The Survival Paradox

- **97%** of day traders lose money (University of Sao Paulo, 1,551 traders)
- **65%** had win rates above 50%, yet **82%** still lost money
- Average winner: +1.2% per trade. Average loser: **-2.8%** per trade
- **85% of scalping strategies fail within 6 months**

### The Drawdown Insight (Most Important Finding)

| Bybit Trader | 30d ROI | Max DD | Follower Profit |
|-------------|---------|--------|-----------------|
| Real World | 25.99% | **5.88%** | **$72,105** (HIGHEST) |
| Holy Grail | 90.00% | 41.93% | $58,163 |
| MASE | 79.84% | 40.98% | $26,932 (LOWEST) |

**The lowest-drawdown trader generated the MOST real follower profit.** High-ROI traders lose followers during drawdowns. This validates STRICT_NO_LOSS — never closing at a loss means low drawdown.

### Winning Profile (Cross-Platform Consensus)

| Parameter | Value |
|-----------|-------|
| Holding time | **1-7 days** (swing, not scalp) |
| Leverage | **5x-10x** (never >20x) |
| Risk per trade | **1-2%** of account |
| Risk:reward | **1:2 minimum** (prefer 1:3) |
| Max drawdown | **< 25%** |
| Stochastic exhaustion | **DEALBREAKER** (confirmed by Bitget data) |
| Partial TP | 50% at TP1, trail remainder |
| Time stop | Close if no movement after 7 days |

### Entry Rules (Most Common Among Winners)

1. Confirm trend on HTF (4H/Daily 200 EMA direction)
2. Wait for pullback to 20 EMA on execution TF (1H)
3. RSI between 30-40 for longs (not at extremes)
4. MACD above zero line (confirmation, not primary)
5. Volume increasing on entry candle
6. Stochastic NOT at exhaustion (dealbreaker)

**Impact**: This is almost exactly our Strategy 4 (EMA Pullback + StochRSI). The copy trading data independently validates the same approach.

---

## FUNDING RATE & MARKET STRUCTURE STRATEGIES

### Funding Rate Arbitrage (Delta-Neutral) — BACKTEST_CHANGE_140

| Parameter | Value |
|-----------|-------|
| Strategy | Long spot + short perp (collect funding) |
| Entry | Funding > 0.03% per 8h for 3+ consecutive periods |
| Exit | Funding drops below 0.01% or basis inverts |
| Annual return (2025) | **19.26%** avg (up from 14.39% in 2024) |
| Best case | **115.9%** over 6 months |
| Max loss | **1.92%** |
| BTC specifically | **38% annualized** (Jan 2020 - Sep 2024) |

This is the **most proven, lowest-risk strategy** found across all research. Requires managing two legs (spot + perp).

### Fear & Greed as Sizing Multiplier — BACKTEST_CHANGE_141

| Parameter | Value |
|-----------|-------|
| Buy signal | F&G < 20 (extreme fear) |
| Sell signal | F&G > 80 (extreme greed) |
| Cumulative return (2018-2025) | **1,240%** vs 680% buy-and-hold |
| Occurrence | Extreme fear on only **14%** of trading days |
| Average hold | 147 days |
| Max drawdown | 52% (LUNA crash) |

Already have F&G in `ez_news_scanner.py`. Wire into sizing: 1.5x size at F&G 25-40, 2x at 10-25, 3x below 10.

### Long/Short Ratio — Contrarian Filter — BACKTEST_CHANGE_142

- L/S ratio > 70% long → suppress new longs (contrarian short signal)
- L/S ratio > 70% short → suppress new shorts (contrarian long signal)
- Works as a **filter**, not standalone entry
- Data: `GET /futures/data/globalLongShortAccountRatio`

### OI Divergence — Confirmation Layer — BACKTEST_CHANGE_143

- Price makes new high but OI 10%+ below recent high → short signal
- Improves prediction accuracy by 30-50% when combined with funding + liquidation data
- Data: `GET /fapi/v1/openInterest` (current), `GET /futures/data/openInterestHist` (30 days only)

### Order Book Imbalance — NOT VIABLE

Requires sub-100ms latency and co-located servers. Skip for our infrastructure.

---

## REGIME DETECTION — HIGHEST-IMPACT CROSS-CUTTING IMPROVEMENT

### ADX Filter: The Single Biggest Edge Found — BACKTEST_CHANGE_137

**ADX trend filter alone improved returns from 36% to 182%** by simply skipping trades when ADX < 25.

| ADX Value | Regime | Strategy |
|-----------|--------|----------|
| < 20 | Ranging | Mean reversion only (RSI extremes, BB bounces) |
| 20-25 | Transitional | Reduce size or stay flat |
| > 25 | Trending | Trend following (MA crosses, breakouts) |
| > 50 | Strong trend | Aggressive trend following |

### Regime Detection Indicators Compared

| Indicator | Best At | Lag | Implementation |
|-----------|---------|-----|----------------|
| **ADX(14)** | Trend strength (best standalone) | Moderate (3-5 candles) | Add to ez_indicators.py |
| **Choppiness Index** | Detecting ranging (CHOP > 61.8) | Low | New computation |
| **BB Width** | Squeeze/breakout detection | Very low | Already have BB data |
| **TTM Squeeze** (BB inside KC) | Breakout timing | Near zero | Needs Keltner Channels |

### Best Combo: ADX + BB Width

- ADX tells you IF there's a trend
- BB Width tells you if volatility is compressed (breakout coming) or expanded (trend in progress)
- When ADX < 20 AND CHOP > 61.8 → high-confidence "ranging" signal → mean reversion only

### Implementation Priority

1. **ADX(14)** — compute per symbol, skip trend entries when ADX < 20 (5x return improvement documented)
2. **BB Width percentile** — already have BB, just need `bbw = (upper - lower) / middle * 100` and 6-month rolling percentile
3. **Choppiness Index** — complementary to ADX, confirms ranging
4. **Market-wide ADX aggregation** — average across all symbols for global regime reading

---

## NEW INDICATORS WORTH ADDING (Priority Order)

| # | Indicator | Why | Effort | Source |
|---|-----------|-----|--------|--------|
| 1 | **ADX(14)** | 36%→182% improvement as filter | Low | Regime agent |
| 2 | **CVD (Cumulative Volume Delta)** | Most-cited edge by pro futures traders | Medium | Twitter/X agent |
| 3 | **MACD(12,26,9)** | Needed by 4 strategies as confirmation | Medium | Distillation agent |
| 4 | **StochRSI(14,14,3,3)** | Strict <0.1 entries, divergence signals | Medium | Reddit agent |
| 5 | **EMA(9), EMA(14)** | Pullback detection (Strategy 4) | Low | Distillation agent |
| 6 | **RSI(2)** | Ultra-short mean reversion (91% WR on daily) | Low | Reddit agent |
| 7 | **BB Width percentile** | Squeeze/regime detection | Low (have BB) | Regime agent |
| 8 | **Choppiness Index** | Ranging confirmation | Low | Regime agent |
| 9 | **Open Interest tracking** | OI divergence signals | Medium (API) | Funding agent |
| 10 | **VWAP** | Pullback entries (intraday) | Medium | Twitter/X agent |

---

## REVISED PRIORITY RANKING (All Research Combined)

| Priority | Change | ID | Evidence Strength | Expected Impact |
|----------|--------|----|-------------------|-----------------|
| **1** | **ADX regime filter** | 137 | 36%→182% documented | Avoid all wrong-regime entries |
| **2** | **EMA Pullback + StochRSI** | 128 | Validated by copy trading + academic | Core "retest-and-launch" edge |
| **3** | **F&G sizing multiplier** | 141 | 1,240% vs 680% B&H | Already have F&G data |
| **4** | **Simple fixed TP% validation** | 139 | 567k backtests | Confirm/simplify exit logic |
| **5** | **Funding rate arb** (delta-neutral) | 140 | 19-38% annual, 1.92% max loss | Separate bot, proven edge |
| **6** | **RSI momentum mode test** | 138 | Crypto-specific RSI reversal | May improve or invalidate RSI gate |
| **7** | **BB+RSI+Stoch scalp** | 134 | 73-77% WR with triple confirm | Good for scalp accounts |
| **8** | **L/S ratio contrarian filter** | 142 | Moderate, good confluence | Suppress crowded-side entries |
| **9** | **OI divergence confirmation** | 143 | 30-50% accuracy improvement | Confirmation layer |
| **10** | **MACD below-zero cross** | 131 | 60% WR, confirmation only | Low priority given MACD=-5.88 PF on crypto |
| **11** | **RSI(2) mean reversion** | 126 | 91% WR but tiny gains | Supplementary signal |
| **12** | **BB/DC breakout** | 132/133 | Needs regime filter first | Only after ADX works |

---

## REVISED IMPLEMENTATION ORDER

### Phase A — Indicators (ez_indicators.py)
Add: ADX(14), EMA(9,14), MACD(12,26,9), StochRSI(14,14,3,3), RSI(2), BB Width, Choppiness Index. All disabled by default.

### Phase B — ADX Regime Filter (BACKTEST_CHANGE_137)
Single biggest impact. Skip entries when ADX < 20. Test on all 350+ symbols.

### Phase C — F&G Sizing Multiplier (BACKTEST_CHANGE_141)
Wire F&G from ez_news_scanner into calculate_final_order_quantity. 1.5x at F&G 25-40, 2x at 10-25.

### Phase D — Strategy 4: EMA Pullback (BACKTEST_CHANGE_128)
Pullback-in-trend entries. Validated by copy trading platform data.

### Phase E — RSI Mode Test (BACKTEST_CHANGE_138)
Backtest RSI momentum (> 50 = buy) vs current RSI mean-reversion (< 37 = buy) on crypto.

### Phase F — Exit Simplification (BACKTEST_CHANGE_139)
Test simple fixed TP% against current tiered/trailing logic. 567k backtests say simpler wins.

### Phase G — Funding Rate Arb (BACKTEST_CHANGE_140)
Separate delta-neutral bot. Long spot + short perp when funding > 0.03%.

### Phase H — Market Structure Filters (BACKTEST_CHANGE_142-143)
L/S ratio contrarian filter + OI divergence confirmation.

### Phase I — Remaining Strategies
Enable one at a time. MACD strategies deprioritized due to negative EV on crypto.

---

## HEIKIN ASHI OPTIMIZATION — BACKTEST_CHANGE_144

### Key Findings

**Traditional candlestick patterns FAIL in crypto.** An IEEE study testing 68 patterns on 23 cryptocurrencies concluded they are "of little use." Many produce opposite signals from textbook predictions. HA trend-following with momentum filters is a different beast and has better prospects.

### HA Streak: Optimal Entry Length

- **2-3 consecutive same-color candles** is the standard entry trigger
- **Wick quality matters more than streak count**: no counter-trend wick = strongest signal
- No published study quantifies optimal streak length with statistical rigor for crypto

### HA Exit: Color Flip vs Confirmation

| Approach | Rule | When to Use |
|----------|------|-------------|
| Aggressive | Exit on first opposite-color candle | Choppy markets |
| Confirmed | Wait for 2 consecutive opposite candles | Strong trends |
| Wick-based | Exit when counter-trend wick appears | Early warning (1-2 candles before color flip) |

### HA + Indicators: Win Rate Improvement

| Combo | Win Rate | Profit Factor |
|-------|----------|---------------|
| HA alone (S&P monthly) | 49.4% | ~1.6 |
| **HA + EMA filter** | **62.7%** | **1.81** |
| HA + RSI | ~65-70% (estimated) | Higher |
| HA + MACD | "Mostly eliminates many signals and increased performance" | Higher |

**HA alone has mediocre WR (40-50%). Adding one momentum filter pushes it to 60-75%.**

### HA Timeframe: Higher is Better

- **4H and Daily** = sweet spot
- **Bitcoin 8H smoothed HA**: 14.04% CAGR, 7.9% max DD, profit factor 2.019
- **5m/15m HA**: "Noise overwhelming, strong filters absolutely necessary." HA lag is fatal on low TFs.
- **Multi-TF approach**: 4H HA for trend direction, 1H for entry timing

### Implementable Strategies

**Strategy A: HA Color Flip + EMA Trend Filter** (Best General)
```python
# BACKTEST_CHANGE_144a
# Trend: price > SMA200 = longs only, < SMA200 = shorts only
# Entry: first green HA after pullback (long), first red after rally (short)
# Confirm: 2 consecutive same-color, ideally wickless on trend side
# Exit: first opposite-color candle
# TF: 4H primary, 1H timing
# Expected: ~62% WR
```

**Strategy B: HA + RSI Momentum Align** (Best for Entries)
```python
# BACKTEST_CHANGE_144b
# LONG: RSI dips below 30, crosses back above → enter on first green HA candle
# SHORT: RSI rises above 70, crosses back below → enter on first red HA candle
# Exit: RSI divergence (price higher high, RSI lower high)
# TF: 1H-4H
```

**Strategy C: HA Wick Quality Scoring** (Novel, Directly Usable)
```python
# BACKTEST_CHANGE_144c
# Score each HA candle: no counter-trend wick = +2, small wick = +1, large wick = 0
# Sum over last 3-5 candles for "trend quality score"
# Enter when score >= 8/10 (strong wickless streak)
# Exit when any candle scores 0 (large counter-trend wick)
```

**Strategy D: Smoothed HA on 8H** (Best Backtested for BTC)
```python
# BACKTEST_CHANGE_144d
# Double-smoothed HA (HA applied to HA values)
# Buy on green flip, sell on red flip, 8H TF
# Backtest: 14.04% CAGR, PF 2.019, 7.9% max DD, 25.6% WR (low WR but high R:R)
```

### Critical Caveats

1. **HA prices are NOT real prices.** Never place orders at HA open/close — use real candle prices
2. **HA lag** = entries always slightly late. Fatal on 5m, acceptable on 4H
3. **72% prediction accuracy** claim for HA appears in multiple sources but without clear methodology — treat with skepticism

---

## BACKTEST_CHANGE NUMBER ALLOCATION (COMPLETE)

| Number | Strategy/Feature | Evidence | Priority |
|--------|-----------------|----------|----------|
| 125 | MACD+RSI+Stoch Triple Confirmation | 73% WR stocks, **20% WR crypto** — deprioritized | Low |
| 126 | RSI(2) Mean Reversion | 91% WR daily, tiny gains | Medium |
| 127 | EMA200+StochRSI Reversal | Candle body filter too restrictive | Low |
| **128** | **EMA Pullback+StochRSI** | **Validated by academia + copy traders** | **HIGH** |
| 129 | RSI-MACD-EMA Combined Entry | Redundant with existing RSI gate | Low |
| 130 | ATR Adaptive Stop | Can't use as exit (STRICT_NO_LOSS), use for sizing | Medium |
| 131 | MACD Below-Zero Crossover | MACD negative EV on crypto | Low |
| 132 | BB Breakout + EMA200 | Conflicts with mean-reversion core | Low |
| 133 | DC Breakout Entry | Redundant with DC_EDGE_SIZING | Low |
| 134 | BB+RSI+Stoch Scalp | 73-77% WR triple confirm | Medium |
| 135 | ATR Adaptive Sizing | Better risk via sizing not stops | Medium |
| 136 | MACD Exit for Winners | MACD suspect on crypto | Low |
| **137** | **ADX Regime Filter** | **36%→182% documented improvement** | **HIGHEST** |
| **138** | **RSI Momentum vs Mean-Reversion Test** | **Crypto-specific RSI studies** | **HIGH** |
| **139** | **Simple TP% vs Trailing Validation** | **567,000 backtests** | **HIGH** |
| **140** | **Funding Rate Arb (delta-neutral)** | **19-38% annual, 1.92% max loss** | **HIGH** |
| **141** | **F&G Sizing Multiplier** | **1,240% vs 680% B&H** | **HIGH** |
| 142 | L/S Ratio Contrarian Filter | Moderate, good confluence | Medium |
| 143 | OI Divergence Confirmation | 30-50% accuracy improvement | Medium |
| 144a-d | Heikin Ashi Optimizations | HA+EMA 62% WR, smoothed HA PF 2.019 | Medium |

---

## SOURCES (All Agents Combined)

### Academic Papers
- Zarattini et al., "Catching Crypto Trends" (SSRN 5209907, 2025)
- Huang, Sangiorgi & Urquhart, "Volume-Weighted TSMOM" (SSRN 4825389, 2024)
- Copula-Based Pairs Trading (Financial Innovation, Springer, 2024)
- Funding Rate Arbitrage Risk/Return (ScienceDirect, 2025)
- Multi-level Deep Q-Networks (Nature Scientific Reports, 2024)
- IEEE — "Do Candlestick Patterns Work in Cryptocurrency Trading?" (2021)
- SSRN — Bollinger Bands Regime-Dependent BTC Study
- Cambridge JFQA — Trend Factor for Crypto Cross-Section

### Backtesting Studies
- KJ Trading Systems — 567,000 backtests on exit strategies (40 markets, 10 years)
- QuantifiedStrategies.com — RSI, MACD, StochRSI, BB, HA backtests
- Trading Rush — 200-trade manual backtests on MACD, BB, DC
- University of Sao Paulo — 1,551 day traders tracked over 2 years
- ADX trend filter: 36% to 182% (pyquantlab.medium.com)

### Trading Platforms
- Bybit Master Trader leaderboard data (MASE, Holy Grail, Real World, SKDXB)
- Bitget Elite Trader metrics and academy
- Binance Futures Leaderboard (MasterRayn)
- OKX Lead Trader classification system
- 3Commas DCA bot performance data
- Stoic.ai meta strategy

### Strategy Sources
- FMZ Quant (GitHub, Medium) — open source crypto strategies
- FXOpen — 1-minute scalping strategies
- Algomatic Trading — Donchian Channel backtest
- ForexTester — BB+RSI+Stoch scalping backtest
- CryptoProfitCalc — EMA crossover guide
- CryptoCred — technical analysis methodology

### Market Structure
- CoinGlass — funding rate, OI, liquidation heatmaps
- Gate.io — funding rate arbitrage research
- Deribit — DVOL implied volatility index
- Binance API — funding rate, OI, L/S ratio endpoints
- Nasdaq — Fear & Greed Index strategy analysis
