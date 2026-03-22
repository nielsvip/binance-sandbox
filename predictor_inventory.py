#!/opt/anaconda3/envs/binance_env/bin/python
"""Predictor Inventory — Master reference of ALL predictors, their purpose, target timeframe,
and how they gate trading decisions.

Generates: audit_reports/predictor_inventory.xlsx
  - Sheet per category (Rating Registry, Rankings, Market Data, Stoch/HA/RSI, Winners/Losers, DC Complex)
  - TARGET_TF column highlighted in yellow = the timeframe this predictor MUST score high on
  - Used by prediction_tracker.py daily optimizer to focus evaluation on correct horizons

Usage:
  python predictor_inventory.py              # Generate inventory Excel
  python predictor_inventory.py --json       # Dump as JSON (for prediction_tracker import)
"""

import json
import os
import sys
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("ERROR: openpyxl required. pip install openpyxl")
    sys.exit(1)

BASE_PATH = Path(__file__).parent
REPORT_DIR = BASE_PATH / "audit_reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

YELLOW_FILL = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
BOLD_FONT = Font(bold=True, size=11)
WRAP = Alignment(wrap_text=True, vertical="top")
THIN_BORDER = Border(bottom=Side(style="thin", color="CCCCCC"))

# ═══════════════════════════════════════════════════════════════════════════════
# MASTER PREDICTOR INVENTORY
# ═══════════════════════════════════════════════════════════════════════════════

INVENTORY = {
    "Rating_Registry": {
        "description": "Central symbol ranking hub for new position discovery & hedge selection. Scores 0-60, refreshed every 5s from AdvancedSignalRater.rate(). Lives in rating_registry.json.",
        "predictors": [
            {"field": "net_score", "source": "rating_registry.json", "range": "-60 to +60", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Symbol discovery — auto-inject new positions when >= 30", "gate_type": "ENTRY", "direction": "LONG if positive, SHORT if negative", "threshold": ">= 30 for discovery, >= 8 for hedge pool", "notes": "Uses 3m/15m/1h/4h/D stoch + SMA200 + DC + HA + RSI. HTF trend gate (SMA200_D) also applies."},
            {"field": "long_score", "source": "rating_registry.json", "range": "0-60", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "LONG entry strength — higher = more conviction for buy", "gate_type": "ENTRY", "direction": "LONG if high", "threshold": ">= 24 GOOD_BUY, >= 29 STRONG_BUY", "notes": "Pullback patterns (DC lows), stoch exhaustion (K<20), MTF alignment, trend reversal signals, volume confirmation"},
            {"field": "short_score", "source": "rating_registry.json", "range": "0-60", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "SHORT entry strength — higher = more conviction for sell", "gate_type": "ENTRY", "direction": "SHORT if high", "threshold": ">= 24 GOOD_SELL, >= 29 STRONG_SELL", "notes": "Recovery patterns (DC highs), stoch exhaustion (K>80), MTF alignment, extreme shorts (SMA200_D 12%+ above)"},
        ],
    },
    "Rankings_Sizing": {
        "description": "Position sizing multipliers from rankings.json. Updated every 6h by ez_rankings.py. Determines HOW MUCH to trade, not WHEN.",
        "predictors": [
            {"field": "final_score_norm", "source": "rankings.json", "range": "-100 to +100", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "PRIMARY sizing driver (0.4 weight in exit/aug). Sorts winners/losers lists", "gate_type": "SIZING", "direction": "LONG if > 0, SHORT if < 0", "threshold": "> 80 = +1 entry score, > 90 = +2", "notes": "Combines linearity (4x), trend, SMA proximity, band quality, volume, gains. 14-day lookback"},
            {"field": "final_score_recent_norm", "source": "rankings.json", "range": "-100 to +100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Recent momentum for sizing (15% weight in order_multiplier)", "gate_type": "SIZING", "direction": "LONG if > 0, SHORT if < 0", "threshold": "Feeds into order_multiplier range 0.5-2.0", "notes": "15-day short-term lookback. Weights scalp vs swing decision"},
            {"field": "combined_percentile", "source": "rankings.json", "range": "0-1", "target_tf": "D", "secondary_tf": "4h", "trading_decision": "Rank-based ordering — 35% weight in order_multiplier", "gate_type": "SIZING", "direction": "Higher = bigger position", "threshold": "0.4 + percentile * 1.6 → rank_multiplier", "notes": "60% LT + 40% ST blend. Gates entry priority across all symbols"},
            {"field": "order_multiplier", "source": "rankings.json", "range": "0.3-2.5x", "target_tf": "D", "secondary_tf": "4h", "trading_decision": "MASTER position sizing gate — applied to EVERY order", "gate_type": "SIZING", "direction": "Higher = bigger position", "threshold": "Composite: rank(35%) + score(25%) + recent(15%) + trend(15%) + vol(10%)", "notes": "This IS the final multiplier. Every open/aug/reentry qty *= order_multiplier"},
            {"field": "trend_val_norm_lt", "source": "rankings.json", "range": "0-100", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "Trend strength for sizing (15% weight in order_multiplier)", "gate_type": "SIZING", "direction": "Higher abs = bigger position", "threshold": "0.7 + (abs/100)*0.6 → trend_multiplier", "notes": "14-day LT trend. Magnitude matters, not direction"},
            {"field": "trend_val_norm_st", "source": "rankings.json", "range": "0-100", "target_tf": "1h", "secondary_tf": "15m", "trading_decision": "Short-term trend strength for scalp sizing", "gate_type": "SIZING", "direction": "Higher abs = bigger position", "threshold": "Same formula as LT, lower weight", "notes": "15-day ST trend"},
            {"field": "proximity_score_norm", "source": "rankings.json", "range": "-100 to +100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Distance to support/resistance bands (0.1 weight in sizing)", "gate_type": "ENTRY_QUALITY", "direction": "+100 = at support (buy), -100 = at resistance (sell)", "threshold": "Weighted: D×4, 4h×4, 1h×2, 15m×1, 3m×0.5", "notes": "WHERE in the band structure. Pair with band_score for confidence"},
            {"field": "band_score", "source": "rankings.json", "range": "-100 to +100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Regression band tightness / channel quality (0.2 weight in sizing)", "gate_type": "ENTRY_QUALITY", "direction": "Higher = tighter channel = more predictable", "threshold": "Applied as: sizing_score += band_score * 0.2", "notes": "HOW CONFIDENT in the band position. Pair with proximity_score"},
            {"field": "rel_vol_raw", "source": "rankings.json", "range": "0.7-1.3", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Volume confirmation (10% weight in order_multiplier)", "gate_type": "SIZING", "direction": "Higher = more volume = bigger position", "threshold": "Clamped min(1.3, max(0.7, raw))", "notes": "20-day average comparison"},
        ],
    },
    "Market_Sentiment": {
        "description": "0-prefixed sentiment indicators from ez_indicators.py → latest_market_data.json. Updated every 3-5 min. Drive entry scoring and position sizing.",
        "predictors": [
            {"field": "0market_sentiment_local", "source": "market_data_*.json", "range": "-100 to +100", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "Per-symbol bullish/bearish score. 40% weight in augmentation trend_score", "gate_type": "ENTRY + SIZING", "direction": "LONG if positive, SHORT if negative", "threshold": "Is_SHORT + local < -20 → crash_mult 2.5x (max boost)", "notes": "Normalized vs loudest symbol. Components: WaveTrend + Hull + SMA + Stoch + HA + Volume + LR"},
            {"field": "0market_sentiment_score", "source": "market_data_*.json", "range": "-100 to +100", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Global market direction — risk on/off gauge", "gate_type": "SIZING", "direction": "Negative = risk-off (boost shorts)", "threshold": "Is_LONG + global < -40 → crash_mult 1.5x", "notes": "Aggregate across ALL symbols. Macro context for sizing"},
            {"field": "0market_sentiment_score_ema", "source": "market_data_*.json", "range": "-100 to +100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Smoothed global sentiment — trend confirmation (sustained vs noise)", "gate_type": "CONFIRMATION", "direction": "Same as 0market_sentiment_score but lagged", "threshold": "50-period EMA. Divergence from raw = potential reversal", "notes": "Reduces false reversals in sentiment"},
            {"field": "0sentiment_rank", "source": "market_data_*.json", "range": "1-N (integer)", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "Percentile rank among all symbols by sentiment", "gate_type": "ENTRY", "direction": "1 = most bullish, N = most bearish", "threshold": "Top 10 → 0is_top_sentiment, Bottom 10 → 0is_bottom_sentiment", "notes": "Drives the boolean flags below"},
            {"field": "0is_top_sentiment", "source": "market_data_*.json", "range": "bool", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "Top 10 bullish — +2 LONG score, +0.2 augmentation bonus", "gate_type": "ENTRY", "direction": "True = momentum name, ride it LONG", "threshold": "+2.0 entry score for LONG, +0.2 (20%) aug trend_score", "notes": "Immediate entry bonus when symbol is in sentiment leaders"},
            {"field": "0is_bottom_sentiment", "source": "market_data_*.json", "range": "bool", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "Bottom 10 bearish — +2 SHORT score, -5 LONG penalty", "gate_type": "ENTRY", "direction": "True = sell pressure, SHORT it or avoid LONG", "threshold": "+2.0 SHORT score, -5.0 LONG penalty (strong veto)", "notes": "-5 penalty is the strongest single-indicator veto in the system"},
            {"field": "0sentiment_strength", "source": "market_data_*.json", "range": "0-100", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "Magnitude of divergence from market average", "gate_type": "CONFIRMATION", "direction": "Higher = more extreme sentiment (either direction)", "threshold": "No direct threshold, feeds classification", "notes": "abs(0market_sentiment_local)"},
            {"field": "0sentiment_classification", "source": "market_data_*.json", "range": "categorical", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "EXTREME_BULLISH → +2 LONG; NEUTRAL → -1 penalty", "gate_type": "ENTRY", "direction": "Label-based bonus/penalty", "threshold": "EXTREME_BULLISH/VERY_BULLISH/BULLISH/NEUTRAL/BEARISH/etc", "notes": "Categorical — triggers secondary signals even if numeric score is moderate"},
            {"field": "0ranking_points", "source": "market_data_*.json (from ranking_points.json)", "range": "0-100", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Sentiment-based rank. > 80 → +1.5 entry score; > 50 → +0.5. 30% weight in aug", "gate_type": "ENTRY + AUG", "direction": "Higher = better momentum rank", "threshold": "> 70 for HIGH priority signal broadcast", "notes": "Inverted percentile: 100 = top sentiment, 0 = worst. Updated every 3-5min"},
            {"field": "0ranking_points_global", "source": "market_data_*.json", "range": "0-100", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Global ranking points (10% weight in augmentation)", "gate_type": "AUG", "direction": "Higher = better global rank", "threshold": "ranking_points_global / 100 → 0-1 scale, 10% of trend_score", "notes": "Alias for 0ranking_points in current implementation"},
            {"field": "0final_score_norm", "source": "market_data_*.json (from rankings.json)", "range": "-100 to +100", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "AI symbol strength. 0.4 weight in exit sizing. > 90 → +2 hedge score", "gate_type": "SIZING + HEDGE", "direction": "LONG if > 0, SHORT if < 0", "threshold": "> 80 = +1 entry, > 90 = +2 entry; 0.4 weight in augmentation sizing", "notes": "Injected from rankings.json. Most important single indicator for sizing (40% weight)"},
        ],
    },
    "DC_Complex": {
        "description": "Donchian Channel cross-timeframe indicators. Calculated in ez_indicators.py from 3m/15m/1h/4h/D channel positions. Drive momentum scoring and position scaling.",
        "predictors": [
            {"field": "0dc_moment", "source": "market_data_*.json", "range": "-100 to +100", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Cross-TF momentum — synthesized from position + expansion + trend", "gate_type": "MOMENTUM", "direction": "Positive = bullish momentum, Negative = bearish", "threshold": "trend × position_blend × expansion_boost × 100", "notes": "Extreme moves early (high expansion) = higher momentum; mature moves = lower"},
            {"field": "0dc_qty", "source": "market_data_*.json", "range": "-100 to +100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Position quantity gate — LONG disqualified if <= 10, SHORT if >= -10", "gate_type": "SIZING", "direction": "Positive = size LONG bigger, Negative = size SHORT bigger", "threshold": "dc_moment × width_rank. Multiplier: 1.0 + (strength × (max_mult-1.0)), can 2-8x qty", "notes": "The strongest single quantity multiplier in the system. Can multiply qty by 2-8x on golden setups"},
            {"field": "0dc_expansion", "source": "market_data_*.json", "range": "0.7-1.3", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "LTF breaking out above HTF — volatility squeeze release detection", "gate_type": "BREAKOUT", "direction": "> 1.0 = LTF expanding vs HTF (breakout), < 1.0 = compressing", "threshold": "Feeds into dc_moment calculation as expansion_boost", "notes": "LTF width / HTF width ratio. High expansion + high dc_moment = highest conviction entry"},
            {"field": "0dc_htf_pos", "source": "market_data_*.json", "range": "0-1", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "HTF channel position — trend direction in higher timeframes", "gate_type": "TREND", "direction": "1.0 = at channel high (bullish HTF), 0.0 = at channel low (bearish HTF)", "threshold": "D×0.5 + 4h×0.3 + 1h×0.2 weighted average", "notes": "Structural trend indicator. Used for MTF alignment confirmation"},
            {"field": "0dc_ltf_pos", "source": "market_data_*.json", "range": "0-1", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "LTF channel position — immediate entry timing within channel", "gate_type": "ENTRY_TIMING", "direction": "0.0 = pullback zone (buy dip), 1.0 = breakout zone (chase)", "threshold": "3m×0.5 + 15m×0.5 weighted average", "notes": "Pair with dc_htf_pos: LTF at 0 + HTF at 1 = perfect pullback buy in uptrend"},
            {"field": "0dc_width_composite", "source": "market_data_*.json", "range": "0+", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Multi-TF volatility — breakout readiness gauge", "gate_type": "VOLATILITY", "direction": "Higher = wider channels = more volatile = bigger moves", "threshold": "D×0.50 + 4h×0.30 + 1h×0.20", "notes": "Used for regime detection: narrow = range-bound, wide = trending"},
            {"field": "dc_moment (rankings)", "source": "rankings.json", "range": "-100 to +100", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Same as 0dc_moment but from rankings context for sizing", "gate_type": "SIZING", "direction": "Same as 0dc_moment", "threshold": "Feeds order quantity calculations", "notes": "Duplicate in rankings.json for sizing pipeline access"},
            {"field": "dc_qty (rankings)", "source": "rankings.json", "range": "-100 to +100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Rank-weighted momentum for position quantity", "gate_type": "SIZING", "direction": "Same as 0dc_qty", "threshold": "Same 2-8x multiplier logic", "notes": "Rankings-pipeline version"},
            {"field": "dc_width_composite (rankings)", "source": "rankings.json", "range": "0+", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Volatility regime detection for position sizing", "gate_type": "SIZING", "direction": "Higher = more volatile = bigger swings", "threshold": "Sum of HTF widths", "notes": "Rankings-pipeline version"},
            {"field": "dc_expansion (rankings)", "source": "rankings.json", "range": "0-1", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Channel expansion rate for breakout detection", "gate_type": "BREAKOUT", "direction": "Rising = breakout imminent", "threshold": "Feeds dc_moment", "notes": "Rankings-pipeline version"},
            {"field": "dc_htf_pos (rankings)", "source": "rankings.json", "range": "0-1", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "HTF structural position for trend confirmation", "gate_type": "TREND", "direction": "1 = bullish HTF, 0 = bearish HTF", "threshold": "Same as 0dc_htf_pos", "notes": "Rankings-pipeline version"},
            {"field": "dc_ltf_pos (rankings)", "source": "rankings.json", "range": "0-1", "target_tf": "3m", "secondary_tf": "15m", "trading_decision": "LTF entry timing within channel", "gate_type": "ENTRY_TIMING", "direction": "0 = pullback, 1 = breakout", "threshold": "Same as 0dc_ltf_pos", "notes": "Rankings-pipeline version"},
        ],
    },
    "Stoch_HA_RSI": {
        "description": "Direct technical indicators used as entry/exit gates. These are the TIMING layer — they determine WHEN to act.",
        "predictors": [
            {"field": "stoch_k_3m", "source": "market_data_*.json", "range": "0-100", "target_tf": "3m", "secondary_tf": "3m", "trading_decision": "PRIMARY entry trigger — crossover k>d = BUY, crossunder k<d = SELL", "gate_type": "ENTRY_TRIGGER", "direction": "< 20 oversold (buy), > 80 overbought (sell)", "threshold": "Crossover required for entry. K<20 = +10-25 points in registry", "notes": "The #1 entry timing signal. Every trade starts with a 3m stoch crossover"},
            {"field": "stoch_k_15m", "source": "market_data_*.json", "range": "0-100", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Entry QUALITY gate — HIGH_QUALITY if K<=32 (LONG). Sizing reduction if K>85", "gate_type": "ENTRY_QUALITY", "direction": "< 30 = oversold recovery (LONG), > 70 = overbought (SHORT)", "threshold": "K<=32+rsi_ok → HIGH_QUALITY. K>85 → -40% size. K<15 (SHORT) → -40% size", "notes": "Second most important stoch. Gates entry quality AND sizing simultaneously"},
            {"field": "stoch_k_1h", "source": "market_data_*.json", "range": "0-100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Medium-term exhaustion/alignment. Exit trigger for high-gain positions", "gate_type": "TREND_CONFIRM + EXIT", "direction": "Trend: k>d = bullish, k<d = bearish. Exhaustion: >85 or <15", "threshold": "Exit HIGH_GAIN if k<d+ha_red (LONG) or k>d+ha_green (SHORT)", "notes": "Drives hedge scoring: LONG hedge penalized if k_15m < d_15m (×0.2 multiplier)"},
            {"field": "stoch_k_4h", "source": "market_data_*.json", "range": "0-100", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "HTF structural alignment gate", "gate_type": "HTF_GATE", "direction": "k>d = bullish structure, k<d = bearish structure", "threshold": "+2 bull score if k>55, -2 if k<45", "notes": "Structural — rarely triggers trades alone but blocks misaligned entries"},
            {"field": "stoch_k_d", "source": "market_data_*.json", "range": "0-100", "target_tf": "D", "secondary_tf": "W", "trading_decision": "Daily structural trend direction", "gate_type": "HTF_GATE", "direction": "Same as 4h but slower — weekly bias", "threshold": "Used in SMA200 trend gate combination", "notes": "Slowest stoch. Changes direction = major regime shift"},
            {"field": "ha_3m", "source": "market_data_*.json", "range": "green/red", "target_tf": "3m", "secondary_tf": "3m", "trading_decision": "Trend color confirmation for entry. SHORT boost: ha_3m='red' → +3 points", "gate_type": "ENTRY_CONFIRM", "direction": "green = bullish, red = bearish", "threshold": "+3 sizing points per red candle (SHORT entry)", "notes": "Simple but effective. Confirms stoch crossover direction"},
            {"field": "ha_15m", "source": "market_data_*.json", "range": "green/red", "target_tf": "15m", "secondary_tf": "1h", "trading_decision": "Exit acceleration — +1.5 score if aligned, -2.0 if opposing", "gate_type": "EXIT_ACCEL", "direction": "green = bullish hold, red = bearish exit pressure", "threshold": "LONG: +1.5 if green, -2.0 if red. SHORT: reversed", "notes": "The -2.0 penalty is significant — can flip an exit decision"},
            {"field": "ha_1h", "source": "market_data_*.json", "range": "green/red", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Momentum flip detection for hedge closure + high-gain exits", "gate_type": "EXIT + HEDGE_CLOSE", "direction": "green = close short hedge, red = close long hedge", "threshold": "Hedge close: k_1h > d_1h OR ha_1h='green' (LONG bounce)", "notes": "Key in hedge closure logic — combined with stoch for bounce detection"},
            {"field": "rsi_1h", "source": "market_data_*.json", "range": "0-100", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Mean-reversion quality. LONG: +15 if <50, -20 if >70. SHORT: reversed", "gate_type": "SIZING + QUALITY", "direction": "< 50 = LONG quality, > 50 = SHORT quality", "threshold": "< 50 = +15, > 70 = -20 (LONG). SHORT_RSI_MIN gate: winners avg 55, losers avg 41", "notes": "Combined with rsi_4h for structural exhaustion. SHORT_RSI_MIN is data-driven from master traders"},
            {"field": "rsi_4h", "source": "market_data_*.json", "range": "0-100", "target_tf": "4h", "secondary_tf": "D", "trading_decision": "Structural exhaustion. +25 if LONG & <50. -30 if SHORT & <30", "gate_type": "SIZING + GUARD", "direction": "Same as rsi_1h but stronger weights (±25-30)", "threshold": "< 50 = +25 LONG, > 50 = +25 SHORT. Guards augmentation reduces", "notes": "Most impactful RSI for sizing. Combined +15+25+15 = +55 when both RSIs aligned"},
        ],
    },
    "Winners_Losers": {
        "description": "Symbol selection lists from ez_rankings.py. Define WHICH symbols to trade per account. Updated every 15-30 min.",
        "predictors": [
            {"field": "winners_20_final_score", "source": "data/winners_20_final_score*.json", "range": "list of 20 symbols", "target_tf": "D", "secondary_tf": "4h", "trading_decision": "Top 20 LT gainers → prioritized for LONG entry across all accounts", "gate_type": "SYMBOL_SELECT", "direction": "These symbols get is_priority=True for LONG", "threshold": "Top 20 by final_score_norm (14-day lookback)", "notes": "Monitor long entries. ANG account primary source"},
            {"field": "losers_20_final_score", "source": "data/losers_20_final_score*.json", "range": "list of 20 symbols", "target_tf": "D", "secondary_tf": "4h", "trading_decision": "Bottom 20 LT losers → prioritized for SHORT entry across all accounts", "gate_type": "SYMBOL_SELECT", "direction": "These symbols get is_priority=True for SHORT", "threshold": "Bottom 20 by final_score_norm", "notes": "Monitor short entries"},
            {"field": "winners_30r", "source": "data/winners_30r*.json", "range": "list of 30 symbols", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Top 30 ST gainers → INF account symbol selection", "gate_type": "SYMBOL_SELECT", "direction": "SHORT-TERM momentum longs", "threshold": "Top 30 by final_score_recent_norm (15-day)", "notes": "INF = short-term inflection account. Uses recent, not LT scores"},
            {"field": "losers_30r", "source": "data/losers_30r*.json", "range": "list of 30 symbols", "target_tf": "1h", "secondary_tf": "4h", "trading_decision": "Bottom 30 ST losers → INF account SHORT selection", "gate_type": "SYMBOL_SELECT", "direction": "SHORT-TERM momentum shorts", "threshold": "Bottom 30 by final_score_recent_norm", "notes": "INF short-term losers"},
            {"field": "winners_15m", "source": "data/winners_15m*.json", "range": "list of ~15 symbols", "target_tf": "15m", "secondary_tf": "3m", "trading_decision": "Mean-reversion LONG candidates — symbols up >1% in 15m from top 100 LT pool", "gate_type": "SYMBOL_SELECT", "direction": "Overextended symbols that will revert (LONG the dip after)", "threshold": "Returns > 1.0% in 15m OR > 0.9% in 3m", "notes": "Ultra-short mean-reversion. INF account scalp longs"},
            {"field": "losers_15m", "source": "data/losers_15m*.json", "range": "list of ~15 symbols", "target_tf": "15m", "secondary_tf": "3m", "trading_decision": "Mean-reversion SHORT candidates — symbols down >1% in 15m", "gate_type": "SYMBOL_SELECT", "direction": "Overextended down symbols to SHORT the bounce", "threshold": "Returns < -1.0% in 15m OR < -0.9% in 3m", "notes": "Ultra-short mean-reversion. INF account scalp shorts"},
        ],
    },
}

# Flat lookup for prediction_tracker integration
def get_target_tf_map():
    """Return {field_name: target_tf} for all predictors."""
    result = {}
    for category, cat_data in INVENTORY.items():
        for pred in cat_data["predictors"]:
            result[pred["field"]] = pred["target_tf"]
    return result


def get_full_inventory():
    """Return the full INVENTORY dict."""
    return INVENTORY


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def generate_inventory_excel():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    columns = ["field", "source", "range", "target_tf", "secondary_tf", "trading_decision", "gate_type", "direction", "threshold", "notes"]
    col_widths = [28, 30, 18, 12, 12, 55, 18, 40, 50, 60]
    for cat_name, cat_data in INVENTORY.items():
        ws = wb.create_sheet(cat_name)
        ws.append(["CATEGORY: " + cat_name])
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
        desc_cell = ws.cell(row=1, column=1)
        desc_cell.font = Font(bold=True, size=13, color="FFFFFF")
        desc_cell.fill = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
        ws.append([cat_data["description"]])
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
        ws.cell(row=2, column=1).font = Font(italic=True, size=10)
        ws.cell(row=2, column=1).alignment = WRAP
        ws.append([])
        headers = ["Field", "Source", "Range", "TARGET TF ★", "Secondary TF", "Trading Decision", "Gate Type", "Direction", "Threshold", "Notes"]
        ws.append(headers)
        header_row = 4
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=header_row, column=col_idx)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
            if col_idx == 4:
                cell.fill = YELLOW_FILL
                cell.font = Font(bold=True, size=11, color="000000")
        for pred in cat_data["predictors"]:
            row_data = [pred.get(c, "") for c in columns]
            ws.append(row_data)
            row_num = ws.max_row
            ws.cell(row=row_num, column=4).fill = YELLOW_FILL
            ws.cell(row=row_num, column=4).font = BOLD_FONT
            ws.cell(row=row_num, column=4).alignment = Alignment(horizontal="center")
            ws.cell(row=row_num, column=5).fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
            ws.cell(row=row_num, column=5).alignment = Alignment(horizontal="center")
            ws.cell(row=row_num, column=1).font = BOLD_FONT
            for col_idx in range(1, len(columns) + 1):
                ws.cell(row=row_num, column=col_idx).alignment = WRAP
                ws.cell(row=row_num, column=col_idx).border = THIN_BORDER
        for col_idx, width in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = width
        ws.sheet_properties.tabColor = {"Rating_Registry": "FF0000", "Rankings_Sizing": "4472C4", "Market_Sentiment": "70AD47", "DC_Complex": "ED7D31", "Stoch_HA_RSI": "7030A0", "Winners_Losers": "FFC000"}.get(cat_name, "000000")
    # Summary sheet
    ws_sum = wb.create_sheet("SUMMARY", 0)
    ws_sum.append(["PREDICTOR INVENTORY — TARGET TIMEFRAME SUMMARY"])
    ws_sum.merge_cells("A1:F1")
    ws_sum.cell(row=1, column=1).font = Font(bold=True, size=14)
    ws_sum.append([])
    ws_sum.append(["Field", "Category", "TARGET TF ★", "Gate Type", "Trading Decision"])
    for col_idx in range(1, 6):
        ws_sum.cell(row=3, column=col_idx).fill = HEADER_FILL
        ws_sum.cell(row=3, column=col_idx).font = HEADER_FONT
    tf_order = {"1m": 0, "3m": 1, "15m": 2, "1h": 3, "4h": 4, "D": 5, "W": 6, "M": 7}
    all_preds = []
    for cat_name, cat_data in INVENTORY.items():
        for pred in cat_data["predictors"]:
            all_preds.append((tf_order.get(pred["target_tf"], 99), pred["field"], cat_name, pred["target_tf"], pred["gate_type"], pred["trading_decision"]))
    all_preds.sort()
    current_tf = None
    for _, field, cat, tf, gate, decision in all_preds:
        if tf != current_tf:
            current_tf = tf
            ws_sum.append([])
            ws_sum.append([f"═══ {tf} PREDICTORS ═══"])
            ws_sum.merge_cells(start_row=ws_sum.max_row, start_column=1, end_row=ws_sum.max_row, end_column=5)
            ws_sum.cell(row=ws_sum.max_row, column=1).font = Font(bold=True, size=12, color="2F5496")
        ws_sum.append([field, cat, tf, gate, decision[:80]])
        ws_sum.cell(row=ws_sum.max_row, column=3).fill = YELLOW_FILL
        ws_sum.cell(row=ws_sum.max_row, column=3).font = BOLD_FONT
    ws_sum.column_dimensions["A"].width = 30
    ws_sum.column_dimensions["B"].width = 20
    ws_sum.column_dimensions["C"].width = 14
    ws_sum.column_dimensions["D"].width = 18
    ws_sum.column_dimensions["E"].width = 85
    out_path = REPORT_DIR / "predictor_inventory.xlsx"
    wb.save(str(out_path))
    print(f"Inventory written to {out_path}")
    return out_path


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(get_target_tf_map(), indent=2))
    else:
        generate_inventory_excel()
