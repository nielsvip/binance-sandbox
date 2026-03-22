# pylint: disable=W,C,R,I
"""ez_breakout_agent v3 — Multi-Lung Breathing Breakout Agent.
Markets breathe like lungs. Every timeframe is its own lung.
Each lung measures: stochastic pulse, volume capacity, candle momentum.
Composite of all lungs = the decision:
  ALL lungs inhaling → FULL ENTRY (maximum conviction)
  Fast lungs exhale, slow still inhale → HOLD (pullback within trend)
  Majority exhaling → FULL EXIT
  Fast lungs inhale again → FULL RE-ENTRY
Crypto = full in / full out (no partial). The rhythm IS the edge:
  higher high → exhale → higher low → inhale → higher high → exhale ...
Monitors BTC, ETH (crypto) and NVDA, MSTR (stocks).
Usage: python ez_breakout_agent.py [--once] [--paper] [--verbose]
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import aiohttp
import numpy as np
import redis.asyncio as aioredis
from config import Config
from config_tradier import TradierConfig
from utils import get_simple_redis_manager, load_environment_from_gpg, setup_logger

config = Config()
tradier_config = TradierConfig()
logger = setup_logger("ez_breakout_agent", str(config.LOG_DIR / "ez_breakout_agent.log"), logging.INFO)
load_environment_from_gpg(logger)
BASE_PATH = Path(os.getenv("EZ_BASE_PATH", str(config.BASE_PATH)))
DATA_DIR = BASE_PATH / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "breakout_agent_state.json"
DECISIONS_DIR = DATA_DIR / "decisions"
shutdown_event = asyncio.Event()

# ============================================================
# BIG CAP WATCHLIST (always monitored)
# ============================================================
CRYPTO_WATCHLIST_STATIC = {
    "BTCUSDT": {"name": "Bitcoin", "max_position_usd": 6000, "base_size_usd": 280, "trail_pct": 0.008, "breakeven_pct": 0.003, "tier": "bigcap"},
    "ETHUSDT": {"name": "Ethereum", "max_position_usd": 4000, "base_size_usd": 200, "trail_pct": 0.008, "breakeven_pct": 0.003, "tier": "bigcap"},
}
STOCK_WATCHLIST = {
    "NVDA": {"name": "NVIDIA", "max_position_usd": 10000, "base_size_shares": 20, "trail_pct": 0.008, "breakeven_pct": 0.003},
    "MSTR": {"name": "MicroStrategy", "max_position_usd": 10000, "base_size_shares": 10, "trail_pct": 0.010, "breakeven_pct": 0.004},
}
# Account routing:
# inf = short-term winners/losers (movers)
# ang = general (scalp)
# men = news-driven
# fin = manual picks
# flz = big caps only
CRYPTO_ACCOUNTS_BIGCAP = ["flz", "ang"]     # Big caps route here
CRYPTO_ACCOUNTS_MOVER = ["inf", "ang"]       # Short-term movers route here
CRYPTO_ACCOUNTS_ALL = ["ang", "inf", "men", "fin", "flz"]  # Fallback
STOCK_ACCOUNT = "trb"
# Dynamic scanner: how many short-term winners/losers to track from rankings
DYNAMIC_TOP_N = 8         # Top N short-term winners → potential LONG breakouts
DYNAMIC_BOTTOM_N = 8      # Bottom N short-term losers → potential SHORT breakouts
# Movers config: smaller size, tighter stops (quick in / quick out)
MOVER_CONFIG = {"max_position_usd": 120, "base_size_usd": 60, "trail_pct": 0.005, "breakeven_pct": 0.002, "tier": "mover"}
# Rankings + symbol list files
RANKINGS_FILE = DATA_DIR / "rankings.json"
SYMBOL_LIST_FILES = ["symbols_inf_long.json", "symbols_inf_short.json", "symbols_ang_long.json", "symbols_ang_short.json", "symbols_men.json"]

# ============================================================
# MULTI-LUNG ARCHITECTURE
# Each timeframe is a lung. Faster lungs breathe faster.
# ============================================================
CRYPTO_LUNGS = [
    # ALL lungs unified: candle + rel_volume = the move, stoch K = exhaustion warning
    # Stoch doesn't decide. It warns. The candle confirms or denies.
    {"tf": "3m",  "weight": 0.06, "dc_period": 20, "label": "3m"},
    {"tf": "15m", "weight": 0.12, "dc_period": 20, "label": "15m"},
    {"tf": "1h",  "weight": 0.18, "dc_period": 20, "label": "1h"},
    {"tf": "4h",  "weight": 0.18, "dc_period": 20, "label": "4h"},
    {"tf": "1d",  "weight": 0.22, "dc_period": 20, "label": "D"},
    {"tf": "1w",  "weight": 0.14, "dc_period": 20, "label": "W"},
    {"tf": "1M",  "weight": 0.10, "dc_period": 12, "label": "M"},
]
MOVER_LUNGS = [
    {"tf": "3m",  "weight": 0.10, "dc_period": 20, "label": "3m"},
    {"tf": "15m", "weight": 0.20, "dc_period": 20, "label": "15m"},
    {"tf": "1h",  "weight": 0.35, "dc_period": 20, "label": "1h"},
    {"tf": "4h",  "weight": 0.35, "dc_period": 20, "label": "4h"},
]
STOCK_LUNGS = [
    {"tf": "daily",  "weight": 0.50, "dc_period": 20, "label": "D"},
    {"tf": "weekly", "weight": 0.35, "dc_period": 20, "label": "W"},
    {"tf": "monthly","weight": 0.15, "dc_period": 12, "label": "M"},
]
# Breathing thresholds
COMPOSITE_INHALE = 0.20           # Composite breath > this → enter / re-enter full
COMPOSITE_EXHALE = -0.10          # Composite breath < this → full exit
COMPOSITE_THESIS_DEAD = -0.50     # Breakout thesis invalidated
# Slow-lung override: if ALL slow lungs still inhaling, don't exit on fast-lung exhale
SLOW_LUNG_OVERRIDE_THRESHOLD = 0.15
# Donchian breakout needs 1h AND 4h to confirm initial entry
DC_BREAKOUT_VOLUME_MULT = 1.5
NEWS_BOOST_THRESHOLD = 0.3
# Timing
BREATH_COOLDOWN_SECONDS = 60      # Between breath-driven actions per symbol
REENTRY_COOLDOWN_SECONDS = 120    # After exit, wait before re-entry
BREAKOUT_COOLDOWN_SECONDS = 180   # Between initial breakout entries
MAX_TRADES_PER_HOUR = 10          # Per symbol
PULLBACK_REENTRY_PCT = 0.005
# Stochastic params
STOCH_K_PERIOD = 14
STOCH_D_PERIOD = 3
STOCH_OVERBOUGHT = 78
STOCH_OVERSOLD = 22


# ============================================================
# UNIFIED LUNG — every TF reads the same signals
# Candle pattern + relative volume = THE MOVE (the decision)
# Stochastic K = EXHAUSTION WARNING (the guideline)
# Stoch warns "running out of breath". Candle confirms or denies.
# A shooting star at K=90 = EXIT. A strong green at K=90 = still going.
# ============================================================
class Lung:
    @staticmethod
    def _detect_candle_patterns(bars: List[dict], direction: str) -> Tuple[float, str]:
        """Read the last 3 candles for reversal/continuation patterns."""
        if len(bars) < 3:
            return 0.0, "none"
        c0, c1, c2 = bars[-1], bars[-2], bars[-3]
        body0 = c0["close"] - c0["open"]
        body1 = c1["close"] - c1["open"]
        range0 = max(c0["high"] - c0["low"], 0.0001)
        range1 = max(c1["high"] - c1["low"], 0.0001)
        abs_body0, abs_body1 = abs(body0), abs(body1)
        upper_wick0 = c0["high"] - max(c0["open"], c0["close"])
        lower_wick0 = min(c0["open"], c0["close"]) - c0["low"]
        bull_score, bear_score, pattern = 0.0, 0.0, "none"
        # BULLISH ENGULFING
        if body0 > 0 and body1 < 0 and abs_body0 > abs_body1 * 1.1 and c0["close"] > c1["open"] and c0["open"] <= c1["close"]:
            bull_score = 0.6
            pattern = "bull_engulf"
        # HAMMER (long lower wick after red candle)
        elif abs_body0 < range0 * 0.35 and lower_wick0 >= abs_body0 * 2.0 and upper_wick0 < abs_body0 * 0.5 and body1 < 0:
            bull_score = 0.5
            pattern = "hammer"
        # MORNING STAR (red → doji → green)
        elif c2["close"] < c2["open"] and abs(c1["close"] - c1["open"]) < range1 * 0.2 and body0 > 0 and c0["close"] > (c2["open"] + c2["close"]) / 2:
            bull_score = 0.55
            pattern = "morning_star"
        # THREE WHITE SOLDIERS
        elif body0 > 0 and body1 > 0 and (c2["close"] - c2["open"]) > 0 and c0["close"] > c1["close"] > c2["close"]:
            bull_score = 0.45
            pattern = "three_white"
        # BULLISH PIN BAR (long lower wick)
        elif lower_wick0 > range0 * 0.6 and abs_body0 < range0 * 0.25:
            bull_score = 0.4
            pattern = "bull_pin"
        # BEARISH ENGULFING
        if body0 < 0 and body1 > 0 and abs_body0 > abs_body1 * 1.1 and c0["open"] > c1["close"] and c0["close"] <= c1["open"]:
            bear_score = 0.6
            pattern = "bear_engulf"
        # SHOOTING STAR (long upper wick after green)
        elif abs_body0 < range0 * 0.35 and upper_wick0 >= abs_body0 * 2.0 and lower_wick0 < abs_body0 * 0.5 and body1 > 0:
            bear_score = 0.5
            pattern = "shooting_star"
        # EVENING STAR
        elif (c2["close"] - c2["open"]) > 0 and abs(c1["close"] - c1["open"]) < range1 * 0.2 and body0 < 0 and c0["close"] < (c2["open"] + c2["close"]) / 2:
            bear_score = 0.55
            pattern = "evening_star"
        # THREE BLACK CROWS
        elif body0 < 0 and body1 < 0 and (c2["close"] - c2["open"]) < 0 and c0["close"] < c1["close"] < c2["close"]:
            bear_score = 0.45
            pattern = "three_black"
        # BEARISH PIN BAR
        elif upper_wick0 > range0 * 0.6 and abs_body0 < range0 * 0.25:
            bear_score = 0.4
            pattern = "bear_pin"
        # DOJI = indecision, slight pause
        if abs_body0 < range0 * 0.1 and pattern == "none":
            return -0.1, "doji"
        net = (bull_score - bear_score) if direction == "LONG" else (bear_score - bull_score)
        return max(-1.0, min(1.0, net)), pattern

    @staticmethod
    def _stochastic(bars: List[dict]) -> Tuple[float, float, float]:
        """Returns (K, D, slope)."""
        if len(bars) < STOCH_K_PERIOD + STOCH_D_PERIOD + 2:
            return 50.0, 50.0, 0.0
        k_values = []
        for i in range(STOCH_D_PERIOD + 3):
            idx = len(bars) - 1 - (STOCH_D_PERIOD + 2 - i)
            if idx < 0:
                k_values.append(50.0)
                continue
            window = bars[max(0, idx - STOCH_K_PERIOD + 1):idx + 1]
            h = max(b["high"] for b in window) if window else 0
            lo = min(b["low"] for b in window) if window else 0
            k_values.append((bars[idx]["close"] - lo) / (h - lo) * 100 if h != lo else 50.0)
        k = k_values[-1]
        d = sum(k_values[-STOCH_D_PERIOD:]) / STOCH_D_PERIOD
        slope = (k_values[-1] - k_values[-3]) / 2 if len(k_values) >= 3 else 0.0
        return k, d, slope

    @staticmethod
    def _rel_volume(bars: List[dict]) -> Tuple[float, float]:
        """Relative volume change — expanding = real move, contracting = fake.
        Returns (vol_score, vol_ratio). Key: volume CONFIRMS or DENIES the candle."""
        lookback = min(20, len(bars) - 1)
        if lookback < 2:
            return 0.0, 1.0
        avg_vol = sum(b["volume"] for b in bars[-(lookback + 1):-1]) / lookback
        vol_ratio = bars[-1]["volume"] / avg_vol if avg_vol > 0 else 1.0
        # Also check if volume is EXPANDING (last 3 bars rising) or CONTRACTING
        if len(bars) >= 4:
            v3 = [b["volume"] for b in bars[-3:]]
            expanding = v3[-1] > v3[-2] > v3[-3]
            contracting = v3[-1] < v3[-2] < v3[-3]
        else:
            expanding, contracting = False, False
        if vol_ratio >= 2.5:
            score = 0.5  # Huge volume = conviction
        elif vol_ratio >= 1.8:
            score = 0.35
        elif vol_ratio >= 1.3:
            score = 0.2 + (0.1 if expanding else 0)
        elif vol_ratio >= 0.8:
            score = 0.0
        elif vol_ratio >= 0.5:
            score = -0.15 + (-0.1 if contracting else 0)
        else:
            score = -0.3
        return score, vol_ratio

    @staticmethod
    def _structure(bars: List[dict], direction: str) -> float:
        """HH/HL (LONG) or LL/LH (SHORT)."""
        if len(bars) < 10:
            return 0.0
        highs = [b["high"] for b in bars[-10:]]
        lows = [b["low"] for b in bars[-10:]]
        rh, oh = max(highs[-3:]), max(highs[-6:-3])
        rl, ol = min(lows[-3:]), min(lows[-6:-3])
        if direction == "LONG":
            hh, hl = rh > oh, rl > ol
            if hh and hl: return 0.3
            if hh: return 0.15
            if hl: return 0.1
            return -0.2
        ll, lh = rl < ol, rh < oh
        if ll and lh: return 0.3
        if ll: return 0.15
        if lh: return 0.1
        return -0.2

    @staticmethod
    def measure(bars: List[dict], direction: str, tf_label: str, **kwargs) -> Tuple[float, dict]:
        """UNIFIED lung measurement — same logic for ALL timeframes.
        The candle + rel volume = THE MOVE (primary decision).
        Stoch K = EXHAUSTION GUIDELINE (warns when move is running out of breath).
        The interaction: stoch warns → candle confirms → THEN we act.
        A bearish engulfing at K=90 = strong exhale (both agree).
        A strong green candle at K=90 = hold/mild (stoch warns but candle denies).
        A shooting star at K=50 = mild exhale (candle warns but stoch doesn't care).
        """
        if len(bars) < STOCH_K_PERIOD + STOCH_D_PERIOD + 2:
            return 0.0, {"tf": tf_label, "reason": "no_data"}
        # --- 1. CANDLE PATTERN: what is the market saying? ---
        pattern_score, pattern_name = Lung._detect_candle_patterns(bars, direction)
        # --- 2. REL VOLUME: is this move real? ---
        vol_score, vol_ratio = Lung._rel_volume(bars)
        # --- 3. CANDLE + VOLUME COMBINED: the primary signal ---
        # A candle pattern WITH volume = strong conviction
        # A candle pattern WITHOUT volume = weak / suspect
        if abs(pattern_score) > 0.1:
            if vol_ratio >= 1.3:
                candle_vol_signal = pattern_score * 1.3  # Volume confirms candle → amplify
            elif vol_ratio <= 0.6:
                candle_vol_signal = pattern_score * 0.5  # No volume → dampen candle signal
            else:
                candle_vol_signal = pattern_score  # Normal volume → take candle at face value
        else:
            candle_vol_signal = pattern_score + vol_score * 0.3  # No pattern → volume speaks alone (weakly)
        candle_vol_signal = max(-1.0, min(1.0, candle_vol_signal))
        # --- 4. STOCH K: exhaustion guideline ---
        stoch_k, stoch_d, stoch_slope = Lung._stochastic(bars)
        # Stoch doesn't score by itself. It MODIFIES the candle signal.
        # When stoch says exhaustion AND candle agrees → amplify
        # When stoch says exhaustion BUT candle is strong → dampen the warning
        if direction == "LONG":
            exhausted = stoch_k > STOCH_OVERBOUGHT
            spring_loaded = stoch_k < STOCH_OVERSOLD
            turning_down = stoch_slope < -1.5
            turning_up = stoch_slope > 1.5
        else:
            exhausted = stoch_k < STOCH_OVERSOLD
            spring_loaded = stoch_k > STOCH_OVERBOUGHT
            turning_down = stoch_slope > 1.5
            turning_up = stoch_slope < -1.5
        stoch_modifier = 0.0
        if exhausted and candle_vol_signal < 0:
            stoch_modifier = -0.25  # Both agree: exhausted + bearish candle = strong exhale
        elif exhausted and candle_vol_signal > 0.2:
            stoch_modifier = 0.05   # Stoch warns but candle is strong green = mild, don't fight the candle
        elif exhausted:
            stoch_modifier = -0.10  # Exhausted, no clear candle = mild warning
        elif spring_loaded and candle_vol_signal > 0:
            stoch_modifier = 0.20   # Oversold + bullish candle = spring loading
        elif spring_loaded:
            stoch_modifier = 0.05   # Oversold but no bullish candle = mild hope
        elif turning_down and candle_vol_signal <= 0:
            stoch_modifier = -0.10  # Momentum fading + weak candle
        elif turning_up and candle_vol_signal >= 0:
            stoch_modifier = 0.10   # Momentum building + ok candle
        # --- 5. STRUCTURE: higher highs / higher lows ---
        structure = Lung._structure(bars, direction)
        # --- 6. STREAK: consecutive directional candles ---
        if len(bars) >= 5:
            recent = bars[-5:]
            streak, anti = 0, 0
            for b in reversed(recent):
                is_with = (b["close"] >= b["open"]) if direction == "LONG" else (b["close"] <= b["open"])
                if is_with and anti == 0:
                    streak += 1
                elif not is_with and streak == 0:
                    anti += 1
                else:
                    break
            streak_score = min(0.25, streak * 0.07) - min(0.25, anti * 0.08)
        else:
            streak_score = 0.0
        # --- 7. SWEEP WINNERS: BB squeeze + SMA200 distance (proven across 11+ symbols) ---
        sweep_boost = 0.0
        bb_squeeze_val = 0.0
        sma200_dist_val = 0.0
        if len(bars) >= 20:
            c_arr = np.array([b["close"] for b in bars])
            # BB squeeze: (upper - lower) / middle — tight bands = imminent explosion
            mid = np.mean(c_arr[-20:])
            std_val = np.std(c_arr[-20:])
            if mid > 0:
                bb_squeeze_val = (2 * 2.0 * std_val) / mid  # Width as pct of price
                if bb_squeeze_val < 0.03:
                    sweep_boost += 0.15  # Squeeze detected — amplify any directional signal
            # SMA200 distance: how far is price from SMA200
            if len(c_arr) >= 200:
                sma200 = np.mean(c_arr[-200:])
                if sma200 > 0:
                    sma200_dist_val = (c_arr[-1] - sma200) / sma200 * 100
                    # Mean reversion boost: extended from SMA200 = expect reversal
                    if direction == "LONG" and sma200_dist_val < -5:
                        sweep_boost += 0.10  # Price far below SMA200 + going long = deep value
                    elif direction == "SHORT" and sma200_dist_val > 5:
                        sweep_boost += 0.10  # Price far above SMA200 + going short = mean revert
        # === FINAL COMPOSITION ===
        # Candle+Volume is THE decision (40%)
        # Stoch modifier adjusts based on exhaustion context (13%)
        # Structure confirms trend health (22%)
        # Streak adds momentum info (13%)
        # Sweep winners boost (12%) — proven across 11+ symbols with Sharpe 75+
        breath = candle_vol_signal * 0.40 + stoch_modifier * 0.13 + structure * 0.22 + streak_score * 0.13 + sweep_boost * 0.12
        breath = max(-1.0, min(1.0, breath))
        # Stoch K/D cross bonus (small, just a timing hint)
        k_values_2 = []
        for offset in [1, 0]:
            idx = len(bars) - 1 - offset
            window = bars[max(0, idx - STOCH_K_PERIOD + 1):idx + 1]
            h = max(b["high"] for b in window) if window else 0
            lo = min(b["low"] for b in window) if window else 0
            k_values_2.append((bars[idx]["close"] - lo) / (h - lo) * 100 if h != lo else 50.0)
        cross = 0.0
        if direction == "LONG":
            if k_values_2[0] <= stoch_d and stoch_k > stoch_d: cross = 0.06
            elif k_values_2[0] >= stoch_d and stoch_k < stoch_d: cross = -0.06
        else:
            if k_values_2[0] >= stoch_d and stoch_k < stoch_d: cross = 0.06
            elif k_values_2[0] <= stoch_d and stoch_k > stoch_d: cross = -0.06
        breath = max(-1.0, min(1.0, breath + cross))
        exh_tag = "!" if exhausted else ("+" if spring_loaded else "")
        components = {"tf": tf_label, "breath": round(breath, 3), "stoch_k": round(stoch_k, 1), "exh": exh_tag, "vol_ratio": round(vol_ratio, 2), "cv_sig": round(candle_vol_signal, 3), "stoch_mod": round(stoch_modifier, 3), "structure": round(structure, 3), "streak": round(streak_score, 3), "pattern": pattern_name, "pat_score": round(pattern_score, 3), "bb_sq": round(bb_squeeze_val, 4), "sma200d": round(sma200_dist_val, 1), "sweep": round(sweep_boost, 3)}
        return breath, components


# ============================================================
# STATE
# ============================================================
class BreakoutState:
    def __init__(self):
        self.active_breakouts: Dict[str, dict] = {}
        self.last_trade_time: Dict[str, float] = {}
        self.last_breath_time: Dict[str, float] = {}
        self.hourly_trades: Dict[str, list] = {}
        self.dc_highs: Dict[str, float] = {}
        self.dc_lows: Dict[str, float] = {}
        self.dc_highs_4h: Dict[str, float] = {}
        self.dc_lows_4h: Dict[str, float] = {}
        self.news_sentiment: Dict[str, float] = {}
        self.exited_symbols: Dict[str, dict] = {}  # symbol -> {direction, breakout_level, exit_time, exit_price}

    def save(self, path: Path):
        data = {"active_breakouts": self.active_breakouts, "last_trade_time": self.last_trade_time, "last_breath_time": self.last_breath_time, "dc_highs": self.dc_highs, "dc_lows": self.dc_lows, "dc_highs_4h": self.dc_highs_4h, "dc_lows_4h": self.dc_lows_4h, "exited_symbols": self.exited_symbols}
        try:
            path.write_text(json.dumps(data, default=str))
        except Exception as e:
            logger.error(f"[STATE_SAVE] {e}")

    def load(self, path: Path):
        try:
            if path.exists():
                data = json.loads(path.read_text())
                self.active_breakouts = data.get("active_breakouts", {})
                self.last_trade_time = {k: float(v) for k, v in data.get("last_trade_time", {}).items()}
                self.last_breath_time = {k: float(v) for k, v in data.get("last_breath_time", {}).items()}
                self.dc_highs = data.get("dc_highs", {})
                self.dc_lows = data.get("dc_lows", {})
                self.dc_highs_4h = data.get("dc_highs_4h", {})
                self.dc_lows_4h = data.get("dc_lows_4h", {})
                self.exited_symbols = data.get("exited_symbols", {})
                logger.info(f"[STATE_LOAD] Loaded {len(self.active_breakouts)} active, {len(self.exited_symbols)} watching for re-entry")
        except Exception as e:
            logger.error(f"[STATE_LOAD] {e}")


# ============================================================
# MAIN AGENT
# ============================================================
class BreakoutAgent:
    def __init__(self, paper_mode: bool = False, verbose: bool = False):
        self.paper_mode = paper_mode
        self.verbose = verbose
        self.state = BreakoutState()
        self.state.load(STATE_FILE)
        self.redis: Optional[aioredis.Redis] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.binance_api_key = os.getenv("BINANCE_API_KEY", "")
        self.binance_api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.finnhub_key = os.getenv("FINNHUB_API_KEY", "")
        self.crypto_watchlist: Dict[str, dict] = dict(CRYPTO_WATCHLIST_STATIC)  # Dynamic, rebuilt each cycle
        self._last_watchlist_refresh = 0.0

    async def start(self):
        self.redis = aioredis.Redis(host="localhost", port=6379, decode_responses=True)
        self.session = aiohttp.ClientSession()
        logger.info(f"🫁 [BREAKOUT_v3] Multi-Lung started. Paper={self.paper_mode}. Stocks={list(STOCK_WATCHLIST.keys())}")

    async def stop(self):
        self.state.save(STATE_FILE)
        if self.session:
            await self.session.close()
        if self.redis:
            await self.redis.close()
        logger.info("[BREAKOUT_v3] Stopped. State saved.")

    # ============================================================
    # DYNAMIC WATCHLIST — scan rankings for short-term movers
    # ============================================================
    def refresh_watchlist(self):
        """Rebuild crypto watchlist: static big-caps + dynamic short-term movers from rankings.
        Movers = symbols that just jumped (winners) or dumped (losers) — ride the momentum."""
        now = time.time()
        if now - self._last_watchlist_refresh < 60:
            return  # Refresh at most every 60s
        self._last_watchlist_refresh = now
        # Start with static big caps
        watchlist = dict(CRYPTO_WATCHLIST_STATIC)
        # Read rankings for short-term winners/losers
        try:
            if RANKINGS_FILE.exists():
                rankings = json.loads(RANKINGS_FILE.read_text())
                by_st = sorted(rankings.items(), key=lambda x: x[1].get("st_rank", 999))
                # Top N winners → potential LONG breakouts
                for symbol, data in by_st[:DYNAMIC_TOP_N]:
                    if symbol not in watchlist:
                        score = data.get("final_score_recent_norm", 0)
                        watchlist[symbol] = {**MOVER_CONFIG, "name": f"mover_long_{symbol}", "mover_direction": "LONG", "st_score": score}
                # Bottom N losers → potential SHORT breakouts
                for symbol, data in by_st[-DYNAMIC_BOTTOM_N:]:
                    if symbol not in watchlist:
                        score = data.get("final_score_recent_norm", 0)
                        watchlist[symbol] = {**MOVER_CONFIG, "name": f"mover_short_{symbol}", "mover_direction": "SHORT", "st_score": score}
        except Exception as e:
            logger.error(f"[RANKINGS_LOAD] {e}")
        # Also pull symbols from inf account lists (the ST winners/losers account)
        # Only add symbols that are in rankings (confirmed by the system)
        try:
            rankings = json.loads(RANKINGS_FILE.read_text()) if RANKINGS_FILE.exists() else {}
        except Exception:
            rankings = {}
        for list_file in ["symbols_inf_long.json", "symbols_inf_short.json"]:
            try:
                path = BASE_PATH / list_file
                if path.exists():
                    symbols = json.loads(path.read_text())
                    direction_hint = "LONG" if "long" in list_file else "SHORT"
                    for sym in symbols:
                        if sym not in watchlist and sym in rankings:
                            watchlist[sym] = {**MOVER_CONFIG, "name": f"inf_{sym}", "mover_direction": direction_hint}
            except Exception:
                pass
        prev_count = len(self.crypto_watchlist)
        self.crypto_watchlist = watchlist
        # Log changes
        movers = [s for s, c in watchlist.items() if c.get("tier") == "mover"]
        if len(watchlist) != prev_count or self.verbose:
            logger.info(f"[WATCHLIST] {len(watchlist)} symbols ({len(CRYPTO_WATCHLIST_STATIC)} bigcap + {len(movers)} movers + {len(watchlist) - len(CRYPTO_WATCHLIST_STATIC) - len(movers)} acct-list)")

    # ============================================================
    async def get_dc_signals(self, symbol: str) -> Tuple[float, float]:
        """Read 0dc_moment and 0dc_qty from Redis latest_market_data.
        dc_moment: -100 (perfect short) to +100 (perfect long) — WHEN to enter
        dc_qty: -100 (all-in short) to +100 (all-in long) — HOW MUCH
        These are computed by ez_indicators from dc_width/dc_position across all TFs."""
        try:
            raw = await self.redis.get("latest_market_data")
            if raw:
                data = json.loads(raw)
                sym_data = data.get(symbol, {})
                moment = float(sym_data.get("0dc_moment", 0))
                qty = float(sym_data.get("0dc_qty", 0))
                return moment, qty
        except Exception:
            pass
        return 0.0, 0.0

    def _is_vetted_symbol(self, symbol: str) -> bool:
        """Is this symbol already in ang/inf lists? If so, the system already vetted it as a winner/loser.
        Give it credit on pullbacks — lower the entry bar."""
        for list_file in ["symbols_inf_long.json", "symbols_inf_short.json", "symbols_ang_long.json", "symbols_ang_short.json"]:
            try:
                path = BASE_PATH / list_file
                if path.exists():
                    symbols = json.loads(path.read_text())
                    if symbol in symbols:
                        return True
            except Exception:
                pass
        return False

    def _get_inhale_threshold(self, symbol: str, dc_moment: float = 0) -> float:
        """Entry threshold adjusted by vetted status + dc_moment strength.
        Vetted symbols get lower bar. Strong dc_moment lowers it further."""
        threshold = COMPOSITE_INHALE  # 0.20 default
        if self._is_vetted_symbol(symbol):
            threshold *= 0.6  # 0.12 — already proven winner/loser
        if abs(dc_moment) >= 40:
            threshold *= 0.7  # dc_moment strongly agrees = even lower bar
        return threshold

    # PRICE + KLINE FETCHING
    # ============================================================
    async def get_crypto_price(self, symbol: str) -> Optional[float]:
        try:
            raw = await self.redis.get(f"price_cache:{symbol}")
            if raw:
                data = json.loads(raw)
                return float(data.get("price", data.get("markPrice", 0)))
            raw = await self.redis.hget("price_cache", symbol)
            if raw:
                return float(json.loads(raw).get("price", 0))
        except Exception:
            pass
        try:
            async with self.session.get(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return float((await resp.json()).get("price", 0))
        except Exception as e:
            logger.error(f"[PRICE] {symbol}: {e}")
        return None

    async def get_stock_price(self, symbol: str) -> Optional[float]:
        try:
            acct_cfg = tradier_config.ACCOUNTS.get(STOCK_ACCOUNT, {})
            headers = {"Authorization": f"Bearer {acct_cfg.get('key', '')}", "Accept": "application/json"}
            base_url = "https://api.tradier.com/v1" if acct_cfg.get("env") == "live" else "https://sandbox.tradier.com/v1"
            async with self.session.get(f"{base_url}/markets/quotes?symbols={symbol}", headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    quotes = (await resp.json()).get("quotes", {}).get("quote", {})
                    if isinstance(quotes, list):
                        quotes = quotes[0]
                    return float(quotes.get("last", quotes.get("close", 0)))
        except Exception as e:
            logger.error(f"[STOCK_PRICE] {symbol}: {e}")
        return None

    async def get_price(self, symbol: str) -> Optional[float]:
        # Crypto and stocks are separate universes — never confuse them
        is_stock = symbol in STOCK_WATCHLIST
        return await self.get_stock_price(symbol) if is_stock else await self.get_crypto_price(symbol)

    async def get_crypto_klines(self, symbol: str, interval: str, limit: int = 40) -> List[dict]:
        # Map Binance intervals to cache file labels
        tf_file_map = {"1d": "D", "1w": "W", "1M": "M", "D": "D", "W": "W", "M": "M"}
        tf_label = tf_file_map.get(interval)
        # For D/W/M: read from klines_cache files (built by ez_klines_htf.py, kept forever)
        if tf_label in ("D", "W", "M"):
            cache_path = BASE_PATH / "klines_cache" / f"{symbol}_{tf_label}.json"
            try:
                if cache_path.exists():
                    bars = json.loads(cache_path.read_text())
                    if isinstance(bars, list) and len(bars) >= 3:
                        return [{"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b.get("volume", 0), "time": b.get("timestamp", "")} for b in bars[-limit:]]
            except Exception:
                pass
        # For intraday TFs: try Redis first, then Binance API
        try:
            raw = await self.redis.get(f"klines:{symbol}:{interval}")
            if raw:
                bars = json.loads(raw)
                if isinstance(bars, list) and len(bars) >= limit:
                    return [{"open": float(b[1]), "high": float(b[2]), "low": float(b[3]), "close": float(b[4]), "volume": float(b[5]), "time": int(b[0])} for b in bars[-limit:]]
        except Exception:
            pass
        # Also check klines_cache file for intraday TFs (3m, 15m, 1h, 4h)
        intraday_map = {"3m": "3m", "15m": "15m", "1h": "1h", "4h": "4h"}
        if interval in intraday_map:
            cache_path = BASE_PATH / "klines_cache" / f"{symbol}_{intraday_map[interval]}.json"
            try:
                if cache_path.exists():
                    bars = json.loads(cache_path.read_text())
                    if isinstance(bars, list) and len(bars) >= limit:
                        return [{"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b.get("volume", 0), "time": b.get("timestamp", "")} for b in bars[-limit:]]
            except Exception:
                pass
        # Fallback: Binance API
        binance_interval = {"D": "1d", "W": "1w", "M": "1M"}.get(interval, interval)
        try:
            async with self.session.get(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={binance_interval}&limit={limit}", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return [{"open": float(b[1]), "high": float(b[2]), "low": float(b[3]), "close": float(b[4]), "volume": float(b[5]), "time": int(b[0])} for b in await resp.json()]
        except Exception as e:
            logger.error(f"[KLINES] {symbol} {interval}: {e}")
        return []

    async def get_stock_klines(self, symbol: str, interval: str = "daily", limit: int = 40) -> List[dict]:
        """Fetch stock bars. Reads from tradier_bars/ cache first, then Tradier API."""
        # Try cache files first (built by ez_klines_htf.py)
        tf_map = {"daily": "D", "weekly": "W", "monthly": "M", "D": "D", "W": "W", "M": "M"}
        tf_label = tf_map.get(interval)
        if tf_label:
            cache_path = BASE_PATH / "data" / "tradier_bars" / f"{symbol}_{tf_label}.json"
            try:
                if cache_path.exists():
                    bars = json.loads(cache_path.read_text())
                    if isinstance(bars, list) and len(bars) >= 3:
                        return [{"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "volume": b.get("volume", 0), "time": b.get("timestamp", "")} for b in bars[-limit:]]
            except Exception:
                pass
        # Fallback: Tradier API
        try:
            acct_cfg = tradier_config.ACCOUNTS.get(STOCK_ACCOUNT, {})
            headers = {"Authorization": f"Bearer {acct_cfg.get('key', '')}", "Accept": "application/json"}
            base_url = "https://api.tradier.com/v1" if acct_cfg.get("env") == "live" else "https://sandbox.tradier.com/v1"
            end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if interval == "monthly":
                lookback_days = limit * 35
            elif interval == "weekly":
                lookback_days = limit * 8
            else:
                lookback_days = limit + 10
            start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            async with self.session.get(f"{base_url}/markets/history?symbol={symbol}&interval={interval}&start={start}&end={end}", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    hist = (await resp.json()).get("history", {})
                    if not hist:
                        return []
                    days = hist.get("day", [])
                    if isinstance(days, dict):
                        days = [days]
                    return [{"open": float(d.get("open", d["close"])), "high": float(d["high"]), "low": float(d["low"]), "close": float(d["close"]), "volume": float(d.get("volume", 0)), "time": d.get("date", "")} for d in days[-limit:]]
        except Exception as e:
            logger.error(f"[STOCK_KLINES] {symbol} {interval}: {e}")
        return []

    # ============================================================
    # MULTI-LUNG BREATH MEASUREMENT
    # ============================================================
    async def measure_all_lungs(self, symbol: str, direction: str) -> Tuple[float, List[dict], float, float]:
        """Measure all lungs for a symbol. Returns (composite_breath, lung_details, slow_breath, fast_breath)."""
        is_crypto = symbol not in STOCK_WATCHLIST  # Crypto and stocks are separate universes
        cfg = self.crypto_watchlist.get(symbol, STOCK_WATCHLIST.get(symbol, {}))
        tier = cfg.get("tier", "")
        if not is_crypto:
            lungs = STOCK_LUNGS
        elif tier == "mover":
            lungs = MOVER_LUNGS  # Movers: skip W/M, heavier on fast lungs
        else:
            lungs = CRYPTO_LUNGS  # Big caps: full 7-lung system
        lung_results = []
        total_weight = 0.0
        composite = 0.0
        slow_sum = 0.0
        slow_weight = 0.0
        fast_sum = 0.0
        fast_weight = 0.0
        for lung_cfg in lungs:
            tf = lung_cfg["tf"]
            weight = lung_cfg["weight"]
            limit = lung_cfg["dc_period"] + STOCH_K_PERIOD + STOCH_D_PERIOD + 5
            if is_crypto:
                bars = await self.get_crypto_klines(symbol, tf, limit)
            else:
                bars = await self.get_stock_klines(symbol, tf, limit)
            if len(bars) < STOCH_K_PERIOD + STOCH_D_PERIOD + 2:
                lung_results.append({"tf": lung_cfg["label"], "breath": 0.0, "reason": "no_data"})
                continue
            breath, components = Lung.measure(bars, direction, lung_cfg["label"])
            lung_results.append(components)
            composite += breath * weight
            total_weight += weight
            # D/W/M = higher TF group, 3m-4h = lower TF group
            if lung_cfg["label"] in ("D", "W", "M"):
                slow_sum += breath * weight
                slow_weight += weight
            else:
                fast_sum += breath * weight
                fast_weight += weight
        if total_weight > 0:
            composite /= total_weight
        slow_breath = slow_sum / slow_weight if slow_weight > 0 else 0.0
        fast_breath = fast_sum / fast_weight if fast_weight > 0 else 0.0
        return composite, lung_results, slow_breath, fast_breath

    def decide_action(self, composite: float, slow_breath: float, fast_breath: float, is_in_position: bool, inhale_threshold: float = None) -> str:
        """Decide what to do based on multi-lung composite.
        Returns: 'ENTER', 'EXIT', 'HOLD', 'THESIS_DEAD'
        """
        threshold = inhale_threshold if inhale_threshold is not None else COMPOSITE_INHALE
        if composite <= COMPOSITE_THESIS_DEAD:
            return "THESIS_DEAD"
        if is_in_position:
            if composite < COMPOSITE_EXHALE:
                if slow_breath >= SLOW_LUNG_OVERRIDE_THRESHOLD:
                    return "HOLD"
                return "EXIT"
            return "HOLD"
        else:
            if composite >= threshold:
                return "ENTER"
            return "HOLD"

    # ============================================================
    # NEWS
    # ============================================================
    async def fetch_news_sentiment(self) -> Dict[str, float]:
        sentiment = {}
        try:
            for key in ("news_sentiment_crypto", "news_sentiment_stocks"):
                raw = await self.redis.get(key)
                if raw:
                    for sym, score in json.loads(raw).items():
                        sentiment[sym] = float(score) if isinstance(score, (int, float, str)) else 0.0
        except Exception as e:
            logger.warning(f"[NEWS_REDIS] {e}")
        if self.finnhub_key:
            for symbol in STOCK_WATCHLIST:
                try:
                    async with self.session.get(f"https://finnhub.io/api/v1/news-sentiment?symbol={symbol}&token={self.finnhub_key}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            sent = data.get("companyNewsScore", 0.5)
                            buzz = 0.2 if data.get("buzz", {}).get("buzzHigh", False) else 0
                            sentiment[symbol] = max(sentiment.get(symbol, 0), (sent - 0.5) * 2 + buzz)
                except Exception:
                    pass
        for symbol in self.crypto_watchlist:
            base = symbol.replace("USDT", "")
            if base in sentiment:
                sentiment[symbol] = sentiment[base]
            elif symbol not in sentiment:
                sentiment[symbol] = 0.0
        self.state.news_sentiment = sentiment
        return sentiment

    # ============================================================
    # BREAKOUT DETECTION (initial entry only — needs 1h + 4h DC break)
    # ============================================================
    async def detect_breakouts(self) -> List[dict]:
        signals = []
        all_symbols = list(self.crypto_watchlist.items()) + [(s, {**c, "asset_type": "stock"}) for s, c in STOCK_WATCHLIST.items()]
        for symbol, cfg in all_symbols:
            if symbol in self.state.active_breakouts:
                continue
            price = await self.get_price(symbol)
            if not price or price <= 0:
                continue
            is_crypto = symbol not in STOCK_WATCHLIST
            bars_1h = await (self.get_crypto_klines(symbol, "1h", 40) if is_crypto else self.get_stock_klines(symbol, "daily", 40))
            bars_4h = await self.get_crypto_klines(symbol, "4h", 40) if is_crypto else bars_1h
            if len(bars_1h) < 20 or len(bars_4h) < 20:
                continue
            dc_h1, dc_l1, _ = self._donchian(bars_1h, 20)
            dc_h4, dc_l4, _ = self._donchian(bars_4h, 20)
            vol = self._vol_ratio(bars_1h, 20)
            news = self.state.news_sentiment.get(symbol, 0.0)
            self.state.dc_highs[symbol] = dc_h1
            self.state.dc_lows[symbol] = dc_l1
            self.state.dc_highs_4h[symbol] = dc_h4
            self.state.dc_lows_4h[symbol] = dc_l4
            direction = None
            breakout_level = 0.0
            is_mover = cfg.get("tier") == "mover"
            mover_hint = cfg.get("mover_direction")  # Rankings already told us the direction
            # Skip symbols with no valid DC data
            if dc_h1 <= 0 or dc_l1 <= 0:
                continue
            # Big caps: need both 1h AND 4h DC break + volume/news
            # Movers: just 1h DC break is enough (they're already confirmed movers from rankings)
            if price > dc_h1 and (price > dc_h4 or is_mover):
                if vol >= DC_BREAKOUT_VOLUME_MULT or news > NEWS_BOOST_THRESHOLD or is_mover:
                    direction = "LONG"
                    breakout_level = dc_h1
            elif price < dc_l1 and (price < dc_l4 or is_mover):
                if vol >= DC_BREAKOUT_VOLUME_MULT or news < -NEWS_BOOST_THRESHOLD or is_mover:
                    direction = "SHORT"
                    breakout_level = dc_l1
            # dc_moment override: if ez_indicators says strong entry, trust it even without DC break
            dc_moment, dc_qty = await self.get_dc_signals(symbol)
            if not direction and abs(dc_moment) >= 50:
                direction = "LONG" if dc_moment > 0 else "SHORT"
                breakout_level = dc_h1 if direction == "LONG" else dc_l1
            # For movers with a hint: only enter in the hinted direction
            if direction and mover_hint and direction != mover_hint:
                direction = None
            # dc_moment disagrees with direction = veto (the cross-TF analysis says no)
            if direction and abs(dc_moment) > 20:
                if (direction == "LONG" and dc_moment < -20) or (direction == "SHORT" and dc_moment > 20):
                    direction = None  # dc_moment vetoes this direction
            if direction:
                tag = "MOVER" if is_mover else ("DC_MOM" if abs(dc_moment) >= 50 else "BREAKOUT")
                logger.info(f"🔥 [{tag}_{direction}] {symbol} @ {price:.2f} | dc_h1={dc_h1:.2f} dc_l1={dc_l1:.2f} vol={vol:.1f}x dcM={dc_moment:+.0f} dcQ={dc_qty:+.0f}")
                signals.append({"symbol": symbol, "direction": direction, "price": price, "breakout_level": breakout_level, "vol": vol, "news": news, "cfg": cfg, "dc_moment": dc_moment, "dc_qty": dc_qty})
        return signals

    def _donchian(self, bars, period):
        if len(bars) < period:
            return 0, 0, 0
        w = bars[-period:]
        h = max(b["high"] for b in w)
        lo = min(b["low"] for b in w)
        return h, lo, (h + lo) / 2

    def _vol_ratio(self, bars, lookback):
        if len(bars) < lookback + 1:
            return 0
        avg = sum(b["volume"] for b in bars[-(lookback + 1):-1]) / lookback
        return bars[-1]["volume"] / avg if avg > 0 else 0

    # ============================================================
    # ORDER EXECUTION
    # ============================================================
    def _get_accounts_for_symbol(self, symbol: str) -> List[str]:
        """Route symbol to the right accounts. Big caps → flz/ang. Movers → inf/ang."""
        cfg = self.crypto_watchlist.get(symbol, {})
        tier = cfg.get("tier", "")
        if tier == "bigcap":
            return CRYPTO_ACCOUNTS_BIGCAP
        if tier == "mover":
            return CRYPTO_ACCOUNTS_MOVER
        return CRYPTO_ACCOUNTS_MOVER  # Default: inf + ang

    async def _send_crypto_order(self, symbol: str, side: str, direction: str, qty: float, price: float, reason: str) -> bool:
        success = False
        accounts = self._get_accounts_for_symbol(symbol)
        for account_key in accounts:
            position_key = f"{account_key}:{symbol}_{direction}"
            is_close = side == ("SELL" if direction == "LONG" else "BUY")
            action = f"QUICK_{'CLOSE' if is_close else 'OPEN'}_{direction}"
            signal_data = {"position_key": position_key, "account_key": account_key, "symbol": symbol, "side": side, "position_side": direction, "quantity": qty, "price": price, "reason": f"BREAKOUT_AGENT_FORCE_{reason}", "action": action, "override": True, "is_hedge": False, "force": True, "is_full_close": is_close, "timestamp": datetime.now(timezone.utc).isoformat()}
            if self.paper_mode:
                logger.info(f"📝 [PAPER] {side} {qty} {symbol} on {account_key} | {reason}")
                success = True
                continue
            try:
                await self.redis.publish("trading_signals", json.dumps(signal_data))
                logger.info(f"✅ [SIGNAL] {account_key} {side} {qty} {symbol} | {reason}")
                success = True
            except Exception as e:
                logger.error(f"[SIGNAL_FAIL] {account_key} {symbol}: {e}")
            try:
                api_key = os.getenv(f"BINANCE_API_KEY_{account_key.upper()}", self.binance_api_key)
                api_secret = os.getenv(f"BINANCE_API_SECRET_{account_key.upper()}", self.binance_api_secret)
                if api_key and api_secret:
                    import hashlib, hmac, urllib.parse
                    params = {"symbol": symbol, "side": side, "positionSide": direction, "type": "MARKET", "quantity": str(qty), "timestamp": str(int(time.time() * 1000)), "recvWindow": "5000"}
                    query = urllib.parse.urlencode(params)
                    params["signature"] = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                    async with self.session.post("https://fapi.binance.com/fapi/v1/order", data=params, headers={"X-MBX-APIKEY": api_key}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        result = await resp.json()
                        if resp.status == 200:
                            logger.info(f"✅ [ORDER] {account_key} {side} {qty} {symbol} id={result.get('orderId')}")
                            success = True
                        else:
                            logger.warning(f"[ORDER_FAIL] {account_key} {symbol}: {result}")
            except Exception as e:
                logger.error(f"[ORDER_ERR] {account_key} {symbol}: {e}")
        return success

    async def _send_stock_order(self, symbol: str, side: str, shares: int, price: float, reason: str) -> bool:
        if self.paper_mode:
            logger.info(f"📝 [PAPER_STOCK] {side} {shares} {symbol} @ {price} | {reason}")
            return True
        try:
            acct_cfg = tradier_config.ACCOUNTS.get(STOCK_ACCOUNT, {})
            base_url = "https://api.tradier.com/v1" if acct_cfg.get("env") == "live" else "https://sandbox.tradier.com/v1"
            headers = {"Authorization": f"Bearer {acct_cfg.get('key', '')}", "Accept": "application/json"}
            async with self.session.post(f"{base_url}/accounts/{acct_cfg.get('id', '')}/orders", data={"class": "equity", "symbol": symbol, "side": side, "quantity": str(int(shares)), "type": "market", "duration": "day"}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = await resp.json()
                if resp.status == 200:
                    logger.info(f"✅ [STOCK] {side} {shares} {symbol} id={result.get('order', {}).get('id')} | {reason}")
                    return True
                logger.error(f"[STOCK_FAIL] {symbol}: {result}")
        except Exception as e:
            logger.error(f"[STOCK_ERR] {symbol}: {e}")
        return False

    def _check_hourly_brake(self, symbol: str) -> bool:
        now = time.time()
        hourly = [t for t in self.state.hourly_trades.get(symbol, []) if now - t < 3600]
        self.state.hourly_trades[symbol] = hourly
        if len(hourly) >= MAX_TRADES_PER_HOUR:
            logger.warning(f"[BRAKE] {symbol} hit {MAX_TRADES_PER_HOUR}/hour")
            return False
        return True

    def _record_trade(self, symbol: str):
        now = time.time()
        hourly = self.state.hourly_trades.get(symbol, [])
        hourly.append(now)
        self.state.hourly_trades[symbol] = hourly
        self.state.last_trade_time[symbol] = now
        self.state.last_breath_time[symbol] = now

    # ============================================================
    # FULL INHALE / FULL EXHALE — crypto is binary
    # ============================================================
    async def full_inhale(self, symbol: str, direction: str, price: float, cfg: dict, breakout_level: float, reason: str) -> bool:
        """Full entry — breathe in completely."""
        if not self._check_hourly_brake(symbol):
            return False
        is_stock = cfg.get("asset_type") == "stock"
        if is_stock:
            shares = cfg.get("base_size_shares", 10)
            side = "buy" if direction == "LONG" else "sell_short"
            success = await self._send_stock_order(symbol, side, shares, price, reason)
        else:
            size_usd = cfg.get("base_size_usd", 200)
            qty = round(size_usd / price, 3) if price > 0 else 0
            if qty <= 0:
                return False
            side = "BUY" if direction == "LONG" else "SELL"
            success = await self._send_crypto_order(symbol, side, direction, qty, price, reason)
        if success:
            self._record_trade(symbol)
            trail_pct = cfg.get("trail_pct", 0.008)
            self.state.active_breakouts[symbol] = {"direction": direction, "entry_price": price, "breakout_level": breakout_level, "peak_price": price, "trailing_stop": price * (1 - trail_pct * 2) if direction == "LONG" else price * (1 + trail_pct * 2), "entry_time": time.time(), "asset_type": cfg.get("asset_type", "crypto"), "size_usd": cfg.get("base_size_usd", 0), "size_shares": cfg.get("base_size_shares", 0), "reentry_count": self.state.active_breakouts.get(symbol, {}).get("reentry_count", 0)}
            if symbol in self.state.exited_symbols:
                self.state.active_breakouts[symbol]["reentry_count"] = self.state.exited_symbols[symbol].get("reentry_count", 0) + 1
                del self.state.exited_symbols[symbol]
            self._log_decision(symbol, direction, "INHALE_FULL", price, reason)
            logger.info(f"🫁💨 [FULL_INHALE] {symbol} {direction} @ {price:.2f} | {reason}")
        return success

    async def full_exhale(self, symbol: str, bo: dict, price: float, reason: str) -> bool:
        """Full exit — breathe out completely."""
        if not self._check_hourly_brake(symbol):
            return False
        direction = bo["direction"]
        is_stock = bo.get("asset_type") == "stock"
        if is_stock:
            shares = bo.get("size_shares", 10)
            side = "sell" if direction == "LONG" else "buy_to_cover"
            success = await self._send_stock_order(symbol, side, shares, price, reason)
        else:
            size_usd = bo.get("size_usd", 200)
            qty = round(size_usd / price, 3) if price > 0 else 0
            if qty <= 0:
                return False
            side = "SELL" if direction == "LONG" else "BUY"
            success = await self._send_crypto_order(symbol, side, direction, qty, price, reason)
        if success:
            self._record_trade(symbol)
            entry = bo["entry_price"]
            pnl = ((price - entry) / entry * 100) if direction == "LONG" else ((entry - price) / entry * 100)
            self.state.exited_symbols[symbol] = {"direction": direction, "breakout_level": bo["breakout_level"], "exit_time": time.time(), "exit_price": price, "pnl_pct": pnl, "reentry_count": bo.get("reentry_count", 0)}
            self._log_decision(symbol, direction, "EXHALE_FULL", price, reason)
            logger.info(f"🫁💤 [FULL_EXHALE] {symbol} {direction} @ {price:.2f} PnL={pnl:.2f}% | {reason}")
        return success

    # ============================================================
    # BREATHING CYCLE — the core loop for active positions
    # ============================================================
    async def breathing_cycle(self):
        to_remove = []
        for symbol, bo in list(self.state.active_breakouts.items()):
            now = time.time()
            if now - self.state.last_breath_time.get(symbol, 0) < BREATH_COOLDOWN_SECONDS:
                continue
            cfg = self.crypto_watchlist.get(symbol, STOCK_WATCHLIST.get(symbol, {}))
            if not cfg:
                continue
            price = await self.get_price(symbol)
            if not price or price <= 0:
                continue
            direction = bo["direction"]
            entry = bo["entry_price"]
            # --- Trailing stop (always enforced, independent of lungs) ---
            trail_pct = cfg["trail_pct"]
            be_pct = cfg["breakeven_pct"]
            peak = bo.get("peak_price", entry)
            if direction == "LONG":
                if price > peak:
                    bo["peak_price"] = price
                    peak = price
                gain_pct = (price - entry) / entry if entry > 0 else 0
                if gain_pct >= be_pct:
                    new_stop = max(entry * 1.001, peak * (1 - trail_pct))
                else:
                    new_stop = entry * (1 - be_pct * 2)
                bo["trailing_stop"] = max(bo.get("trailing_stop", 0), new_stop)
                if price <= bo["trailing_stop"]:
                    logger.warning(f"🛑 [TRAIL_STOP] {symbol} LONG @ {price:.2f} stop={bo['trailing_stop']:.2f} gain={gain_pct*100:.2f}%")
                    await self.full_exhale(symbol, bo, price, f"TRAILING_STOP_gain{gain_pct*100:.1f}pct")
                    to_remove.append(symbol)
                    continue
            else:
                if price < peak:
                    bo["peak_price"] = price
                    peak = price
                gain_pct = (entry - price) / entry if entry > 0 else 0
                if gain_pct >= be_pct:
                    new_stop = min(entry * 0.999, peak * (1 + trail_pct))
                else:
                    new_stop = entry * (1 + be_pct * 2)
                cur_stop = bo.get("trailing_stop", 0)
                bo["trailing_stop"] = min(cur_stop, new_stop) if cur_stop > 0 else new_stop
                if price >= bo["trailing_stop"]:
                    logger.warning(f"🛑 [TRAIL_STOP] {symbol} SHORT @ {price:.2f} stop={bo['trailing_stop']:.2f} gain={gain_pct*100:.2f}%")
                    await self.full_exhale(symbol, bo, price, f"TRAILING_STOP_gain{gain_pct*100:.1f}pct")
                    to_remove.append(symbol)
                    continue
            # --- Multi-lung breath measurement ---
            composite, lung_details, slow_breath, fast_breath = await self.measure_all_lungs(symbol, direction)
            # --- dc_moment/dc_qty integration: cross-TF × cross-symbol signal from ez_indicators ---
            dc_moment, dc_qty = await self.get_dc_signals(symbol)
            # dc_moment adjusts composite: strong agreement amplifies, disagreement dampens
            dc_adj = 0.0
            if abs(dc_moment) > 10:
                if (direction == "LONG" and dc_moment > 0) or (direction == "SHORT" and dc_moment < 0):
                    dc_adj = min(0.15, abs(dc_moment) / 200)  # Agreement: boost up to +0.15
                else:
                    dc_adj = -min(0.15, abs(dc_moment) / 200)  # Disagreement: dampen up to -0.15
            adjusted_composite = max(-1.0, min(1.0, composite + dc_adj))
            action = self.decide_action(adjusted_composite, slow_breath, fast_breath, is_in_position=True)
            # Format lung summary
            lung_str = " | ".join(f"{l['tf']}={l['breath']:+.2f}[K{l.get('stoch_k',0):.0f}]" for l in lung_details if "breath" in l and isinstance(l.get("breath"), (int, float)))
            dc_str = f" dcM={dc_moment:+.0f} dcQ={dc_qty:+.0f}" if abs(dc_moment) > 5 else ""
            if action == "EXIT":
                logger.info(f"🫁💤 [EXHALE] {symbol} {direction} c={adjusted_composite:+.3f} slow={slow_breath:+.2f} fast={fast_breath:+.2f}{dc_str} | {lung_str}")
                await self.full_exhale(symbol, bo, price, f"BREATH_EXHALE_c{adjusted_composite:+.2f}_dcM{dc_moment:+.0f}")
                to_remove.append(symbol)
            elif action == "THESIS_DEAD":
                logger.warning(f"💀 [DEAD] {symbol} {direction} c={adjusted_composite:+.3f}{dc_str} | {lung_str}")
                await self.full_exhale(symbol, bo, price, f"THESIS_DEAD_c{adjusted_composite:+.2f}")
                to_remove.append(symbol)
            else:
                if self.verbose:
                    logger.info(f"[HOLD] {symbol} {direction} g={gain_pct*100:.2f}% c={adjusted_composite:+.3f}{dc_str} | {lung_str}")
                self.state.last_breath_time[symbol] = now
            bo["last_composite"] = composite
            bo["last_slow"] = slow_breath
            bo["last_fast"] = fast_breath
        for s in to_remove:
            if s in self.state.active_breakouts:
                del self.state.active_breakouts[s]

    # ============================================================
    # RE-ENTRY — lungs fill again after exhale
    # ============================================================
    async def check_reentries(self):
        """After a full exhale, monitor for lungs to start inhaling again → full re-entry."""
        to_remove = []
        for symbol, ex in list(self.state.exited_symbols.items()):
            now = time.time()
            exit_time = ex.get("exit_time", 0)
            if now - exit_time < REENTRY_COOLDOWN_SECONDS:
                continue
            if symbol in self.state.active_breakouts:
                to_remove.append(symbol)
                continue
            # Expire old exits (>24h)
            if now - exit_time > 86400:
                to_remove.append(symbol)
                continue
            direction = ex["direction"]
            breakout_level = ex["breakout_level"]
            cfg = self.crypto_watchlist.get(symbol, STOCK_WATCHLIST.get(symbol, {}))
            if not cfg:
                to_remove.append(symbol)
                continue
            price = await self.get_price(symbol)
            if not price or price <= 0:
                continue
            # Thesis check: price must still be on the right side of breakout
            if direction == "LONG" and price < breakout_level * 0.995:
                continue  # Below breakout = don't re-enter
            if direction == "SHORT" and price > breakout_level * 1.005:
                continue
            # Measure all lungs + dc signals — are they inhaling again?
            composite, lung_details, slow_breath, fast_breath = await self.measure_all_lungs(symbol, direction)
            dc_moment, dc_qty = await self.get_dc_signals(symbol)
            dc_adj = 0.0
            if abs(dc_moment) > 10:
                if (direction == "LONG" and dc_moment > 0) or (direction == "SHORT" and dc_moment < 0):
                    dc_adj = min(0.15, abs(dc_moment) / 200)
                else:
                    dc_adj = -min(0.15, abs(dc_moment) / 200)
            adjusted = max(-1.0, min(1.0, composite + dc_adj))
            threshold = self._get_inhale_threshold(symbol, dc_moment)
            action = self.decide_action(adjusted, slow_breath, fast_breath, is_in_position=False, inhale_threshold=threshold)
            vetted_tag = "V" if self._is_vetted_symbol(symbol) else ""
            dc_tag = f" dcM={dc_moment:+.0f}" if abs(dc_moment) > 5 else ""
            if action == "ENTER":
                lung_str = " | ".join(f"{l['tf']}={l['breath']:+.2f}[K{l.get('stoch_k',0):.0f}{l.get('exh','')}]" for l in lung_details if "breath" in l and isinstance(l.get("breath"), (int, float)))
                logger.info(f"🫁💨 [RE-INHALE{vetted_tag}] {symbol} {direction} c={adjusted:+.3f}(thr={threshold:.2f}){dc_tag} | {lung_str}")
                success = await self.full_inhale(symbol, direction, price, cfg, breakout_level, f"REENTRY_c{adjusted:+.2f}_dcM{dc_moment:+.0f}_n{ex.get('reentry_count',0)+1}")
                if success:
                    to_remove.append(symbol)
            elif self.verbose:
                logger.info(f"[WATCH{vetted_tag}] {symbol} {direction} c={composite:+.3f} (need>{threshold:.2f}) waiting")
        for s in to_remove:
            if s in self.state.exited_symbols:
                del self.state.exited_symbols[s]

    # ============================================================
    # PULLBACK RE-ENTRY (fresh breakout retest)
    # ============================================================
    async def check_pullback_reentries(self):
        for symbol in list(self.crypto_watchlist.keys()) + list(STOCK_WATCHLIST.keys()):
            if symbol in self.state.active_breakouts or symbol in self.state.exited_symbols:
                continue
            dc_high = self.state.dc_highs.get(symbol)
            dc_low = self.state.dc_lows.get(symbol)
            if not dc_high or not dc_low:
                continue
            now = time.time()
            if now - self.state.last_trade_time.get(symbol, 0) < BREAKOUT_COOLDOWN_SECONDS:
                continue
            price = await self.get_price(symbol)
            if not price or price <= 0:
                continue
            cfg = self.crypto_watchlist.get(symbol, STOCK_WATCHLIST.get(symbol, {}))
            if not cfg:
                continue
            direction = None
            breakout_level = 0
            if price >= dc_high * (1 - PULLBACK_REENTRY_PCT) and price <= dc_high * (1 + PULLBACK_REENTRY_PCT * 2):
                dc_h4 = self.state.dc_highs_4h.get(symbol, 0)
                if price > dc_h4 * 0.99 or dc_h4 == 0:
                    direction = "LONG"
                    breakout_level = dc_high
            elif price <= dc_low * (1 + PULLBACK_REENTRY_PCT) and price >= dc_low * (1 - PULLBACK_REENTRY_PCT * 2):
                dc_l4 = self.state.dc_lows_4h.get(symbol, 0)
                if price < dc_l4 * 1.01 or dc_l4 == 0:
                    direction = "SHORT"
                    breakout_level = dc_low
            if direction:
                composite, lung_details, slow_breath, fast_breath = await self.measure_all_lungs(symbol, direction)
                threshold = self._get_inhale_threshold(symbol)
                if composite >= threshold:
                    vetted = "V" if self._is_vetted_symbol(symbol) else ""
                    lung_str = " | ".join(f"{l['tf']}={l['breath']:+.2f}" for l in lung_details if isinstance(l.get("breath"), (int, float)))
                    logger.info(f"🔄 [PULLBACK{vetted}_{direction}] {symbol} @ {price:.2f} DC={breakout_level:.2f} c={composite:+.2f}(thr={threshold:.2f}) | {lung_str}")
                    await self.full_inhale(symbol, direction, price, cfg, breakout_level, f"PULLBACK_{direction}_c{composite:+.2f}")

    # ============================================================
    # DECISION LOG
    # ============================================================
    def _log_decision(self, symbol: str, direction: str, action: str, price: float, reason: str):
        DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        filepath = DECISIONS_DIR / f"breakout_agent_{day}.jsonl"
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "direction": direction, "action": action, "price": price, "reason": reason, "news": self.state.news_sentiment.get(symbol, 0.0), "paper": self.paper_mode}
        try:
            with open(filepath, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"[LOG] {e}")

    # ============================================================
    # MAIN LOOP
    # ============================================================
    async def run_once(self):
        try:
            self.refresh_watchlist()
            await self.fetch_news_sentiment()
            signals = await self.detect_breakouts()
            for sig in signals:
                if sig["symbol"] not in self.state.active_breakouts:
                    composite, lungs, slow_b, fast_b = await self.measure_all_lungs(sig["symbol"], sig["direction"])
                    threshold = self._get_inhale_threshold(sig["symbol"])
                    vetted = "V" if self._is_vetted_symbol(sig["symbol"]) else ""
                    if composite >= threshold:
                        lung_str = " | ".join(f"{l['tf']}={l['breath']:+.2f}[K{l.get('stoch_k',0):.0f}{l.get('exh','')}]" for l in lungs if isinstance(l.get("breath"), (int, float)))
                        logger.info(f"🫁 [ENTRY{vetted}] {sig['symbol']} {sig['direction']} c={composite:+.3f}(thr={threshold:.2f}) | {lung_str}")
                        await self.full_inhale(sig["symbol"], sig["direction"], sig["price"], sig["cfg"], sig["breakout_level"], f"BREAKOUT_{sig['direction']}_c{composite:+.2f}_vol{sig['vol']:.1f}")
                    elif self.verbose:
                        logger.info(f"[WEAK{vetted}] {sig['symbol']} {sig['direction']} c={composite:+.3f} < {threshold:.2f}")
            await self.breathing_cycle()
            await self.check_reentries()
            await self.check_pullback_reentries()
            self.state.save(STATE_FILE)
            active_count = len(self.state.active_breakouts)
            watching = len(self.state.exited_symbols)
            if active_count > 0 or watching > 0 or self.verbose:
                parts = []
                for s, b in self.state.active_breakouts.items():
                    c = b.get("last_composite", 0)
                    g = 0
                    p = b.get("entry_price", 0)
                    if p > 0:
                        price = await self.get_price(s)
                        if price:
                            g = ((price - p) / p * 100) if b["direction"] == "LONG" else ((p - price) / p * 100)
                    parts.append(f"{s}={b['direction']}[c={c:+.2f}|g={g:+.1f}%]")
                watch_parts = [f"{s}={e['direction']}(watching)" for s, e in self.state.exited_symbols.items()]
                summary = ", ".join(parts + watch_parts)
                logger.info(f"[SCAN] IN:{active_count} WATCH:{watching} | {summary}")
        except Exception as e:
            logger.error(f"[SCAN_ERROR] {e}", exc_info=True)

    async def run_daemon(self, interval: int = 30):
        await self.start()
        lung_labels = [l["label"] for l in CRYPTO_LUNGS]
        logger.info(f"[DAEMON] Multi-lung breathing every {interval}s. Lungs: {lung_labels}")
        try:
            while not shutdown_event.is_set():
                await self.run_once()
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.stop()


def handle_signal(sig, frame):
    shutdown_event.set()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Lung Breathing Breakout Agent v3")
    parser.add_argument("--once", action="store_true", help="Single scan then exit")
    parser.add_argument("--paper", action="store_true", help="Paper trading (no real orders)")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--interval", type=int, default=30, help="Scan interval seconds")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    agent = BreakoutAgent(paper_mode=args.paper, verbose=args.verbose)
    if args.once:
        async def _once():
            await agent.start()
            await agent.run_once()
            await agent.stop()
        asyncio.run(_once())
    else:
        asyncio.run(agent.run_daemon(interval=args.interval))


if __name__ == "__main__":
    main()
