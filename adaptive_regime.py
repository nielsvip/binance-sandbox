#!/usr/bin/env python3
"""
Adaptive Regime v2 — Breakout Detector + Position Watcher

TWO JOBS:
1. DETECT breakouts: dc_position >0.90 on 2+ TFs → signal GO (loosen config for ez_positions_quick)
2. WATCH positions every second: the moment price < low_3m_prev while overextended → signal TIGHTEN

Two-tier loop:
  FAST (1s): outliers diverging >3% from BTC + symbols with active positions + snapping symbols
  SLOW (30s): everything else

Usage:
    python3 adaptive_regime.py --daemon     # Run continuously
    python3 adaptive_regime.py --status     # Show breakouts + watched positions
"""
import argparse
import json
import logging
import math
import os
import signal
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config
from wt_dc_entry_scorer import score_entry
from wt_dc_exit_scorer import score_exit

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = BASE_PATH / "data" / "adaptive_regime"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PAPER_LOG_DIR = BASE_PATH / "data" / "decisions"
STATE_FILE = DATA_DIR / "state.json"
OVERRIDE_SNAPSHOT = DATA_DIR / "current_overrides.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [REGIME] %(message)s")
logger = logging.getLogger("adaptive_regime")

TFS = ["3m", "15m", "1h", "4h", "D"]
DC_BREAKOUT = 0.90
DC_BREAKDOWN = 0.10
BTC_OUTLIER_1H = 3.0  # % divergence from BTC to be an outlier
BTC_OUTLIER_4H = 5.0


def get_redis():
    import redis
    return redis.Redis(host="localhost", port=config.REDIS_PORT, db=0)


def load_tradeable_keys():
    path = BASE_PATH / "tradeable_keys.json"
    if not path.exists():
        return {}
    with open(path) as f:
        keys = json.load(f)
    result = {}
    for k in keys:
        if ":" not in k:
            continue
        acct, pk = k.split(":", 1)
        if acct in ("trb", "trc"):
            continue
        if pk.endswith("_LONG"):
            sym, side = pk[:-5], "LONG"
        elif pk.endswith("_SHORT"):
            sym, side = pk[:-6], "SHORT"
        else:
            continue
        result.setdefault(acct, {}).setdefault(sym, set()).add(side)
    return result


def _sf(v, default=0.0):
    try:
        f = float(v or default)
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def _sanitize_for_scorer(metrics: dict) -> dict:
    """Convert string indicator fields to numeric for entry/exit scorers."""
    out = {}
    for k, v in metrics.items():
        if isinstance(v, str):
            if k.startswith("wt_structure_"):
                out[k] = 1 if v in ("HL", "HH") else (-1 if v in ("LH", "LL") else 0)
            elif k.startswith("wt_momentum_state_"):
                out[k] = 2 if "IMPULSE" in v else (-2 if "EXHAUST" in v else 0)
            elif k.startswith("wt_wave_phase_"):
                out[k] = 1 if v == "BULLISH" else (-1 if v == "BEARISH" else 0)
            elif k.startswith("ha_") and not k.startswith("ha_streak"):
                out[k] = 1 if v == "green" else (-1 if v == "red" else 0)
            elif k == "wt_composite_bias":
                out[k] = 1 if v == "LONG" else (-1 if v == "SHORT" else 0)
            else:
                out[k] = v
        else:
            out[k] = v
    return out


# ═══════════════════════════════════════════════════════════════════════
# CORE: per-symbol state
# ═══════════════════════════════════════════════════════════════════════

class SymbolState:
    """Tracks one symbol's regime, breakout status, and snap-back detection."""
    __slots__ = ("symbol", "regime", "heat", "dc", "k", "price", "low_3m_prev", "high_3m_prev",
                 "dc_low_3m", "dc_high_3m", "ob_count", "os_count", "entry_L", "entry_S",
                 "exit_L", "exit_S", "wt_bias", "vs_btc_1h", "vs_btc_4h", "is_outlier",
                 "strength", "tier", "rank", "signals", "overrides", "last_update", "pct_1h", "pct_4h",
                 "k3m_prev", "k3m_rising", "wt_vel_3m")

    def __init__(self, symbol):
        self.symbol = symbol
        self.regime = "UNKNOWN"
        self.heat = 0.0
        self.dc = {}
        self.k = {}
        self.price = 0.0
        self.low_3m_prev = 0.0
        self.high_3m_prev = 0.0
        self.dc_low_3m = 0.0
        self.dc_high_3m = 0.0
        self.ob_count = 0
        self.os_count = 0
        self.entry_L = 0.0
        self.entry_S = 0.0
        self.exit_L = 0.0
        self.exit_S = 0.0
        self.wt_bias = ""
        self.vs_btc_1h = 0.0
        self.vs_btc_4h = 0.0
        self.pct_1h = 0.0
        self.pct_4h = 0.0
        self.is_outlier = False
        self.strength = 0.0
        self.tier = ""
        self.rank = 0
        self.signals = []
        self.overrides = {}
        self.last_update = 0.0
        self.k3m_prev = 50.0
        self.k3m_rising = False
        self.wt_vel_3m = 0.0


# ═══════════════════════════════════════════════════════════════════════
# PAPER PORTFOLIO — virtual positions with real entry/exit prices + PnL
# ═══════════════════════════════════════════════════════════════════════

PAPER_CAPITAL_PER_ACCOUNT = 1000.0  # $1k virtual per account
PAPER_MAX_POSITION_SIZE = 20.0  # Match real MAX_POSITION_SIZE
PAPER_ENTRY_SCORE_MIN = 45  # Min entry score to open (from wt_dc_entry_scorer backtest)
PAPER_EXIT_SCORE_MIN = 25  # Min exit score to close (from wt_dc_exit_scorer optimal)
PAPER_TRADES_DIR = BASE_PATH / "data" / "decisions"

# LIVE ACCOUNTS — regime config overrides flow into REAL trading logic for these accounts.
# Other accounts stay paper (overrides logged but not applied).
# Add accounts here one at a time after paper proof.
LIVE_ACCOUNTS = set()  # ALL ACCOUNTS PAPER ONLY. Live overrides DISABLED until paper proves profitable for 7 days.


class PaperPosition:
    __slots__ = ("key", "account", "symbol", "side", "entry_price", "qty", "entry_time",
                 "entry_reason", "entry_score", "regime_at_entry", "tier_at_entry", "max_gain", "min_gain")

    def __init__(self, key, account, symbol, side, entry_price, qty, reason, score, regime, tier):
        self.key = key
        self.account = account
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.qty = qty
        self.entry_time = datetime.now(timezone.utc)
        self.entry_reason = reason
        self.entry_score = score
        self.regime_at_entry = regime
        self.tier_at_entry = tier
        self.max_gain = 0.0
        self.min_gain = 0.0

    def pnl_pct(self, current_price):
        if self.entry_price <= 0:
            return 0.0
        if self.side == "LONG":
            return (current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - current_price) / self.entry_price * 100

    def notional(self, current_price):
        return abs(self.qty) * current_price


class PaperPortfolio:
    """Virtual paper trading portfolio. Tracks positions, logs trades, computes PnL."""

    def __init__(self):
        self.positions: dict[str, PaperPosition] = {}  # key = "account:SYMBOL_SIDE"
        self.balance: dict[str, float] = {}  # account → available capital
        self.closed_pnl: dict[str, float] = {}  # account → cumulative realized PnL $
        self.trade_count: dict[str, int] = {}  # account → total trades
        self._load()

    def _state_path(self):
        return DATA_DIR / "paper_portfolio.json"

    def _load(self):
        path = self._state_path()
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                self.balance = data.get("balance", {})
                self.closed_pnl = data.get("closed_pnl", {})
                self.trade_count = data.get("trade_count", {})
                for k, p in data.get("positions", {}).items():
                    pp = PaperPosition(k, p["account"], p["symbol"], p["side"],
                                       p["entry_price"], p["qty"], p["entry_reason"],
                                       p.get("entry_score", 0), p.get("regime", ""), p.get("tier", ""))
                    pp.entry_time = datetime.fromisoformat(p["entry_time"])
                    pp.max_gain = p.get("max_gain", 0)
                    pp.min_gain = p.get("min_gain", 0)
                    self.positions[k] = pp
                logger.info(f"Paper portfolio loaded: {len(self.positions)} positions, balances={self.balance}")
            except Exception as e:
                logger.warning(f"Paper portfolio load failed: {e}")

    def save(self):
        data = {
            "balance": self.balance,
            "closed_pnl": self.closed_pnl,
            "trade_count": self.trade_count,
            "positions": {},
        }
        for k, pp in self.positions.items():
            data["positions"][k] = {
                "account": pp.account, "symbol": pp.symbol, "side": pp.side,
                "entry_price": pp.entry_price, "qty": pp.qty,
                "entry_time": pp.entry_time.isoformat(),
                "entry_reason": pp.entry_reason, "entry_score": pp.entry_score,
                "regime": pp.regime_at_entry, "tier": pp.tier_at_entry,
                "max_gain": pp.max_gain, "min_gain": pp.min_gain,
            }
        try:
            with open(self._state_path(), "w") as f:
                json.dump(data, f, indent=1, default=str)
        except Exception:
            pass

    def _init_account(self, account: str):
        if account not in self.balance:
            self.balance[account] = PAPER_CAPITAL_PER_ACCOUNT
            self.closed_pnl[account] = 0.0
            self.trade_count[account] = 0

    def _log_trade(self, account: str, action: str, position_key: str, price: float, qty: float,
                   reason: str, pnl_pct: float = 0.0, pnl_usd: float = 0.0, ss: SymbolState = None):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "position_key": position_key,
            "account": account,
            "action": action,
            "reason": reason,
            "price": price,
            "qty": round(qty, 8),
            "notional": round(price * abs(qty), 2),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_usd": round(pnl_usd, 4),
            "snapshot": {
                "regime": ss.regime if ss else "",
                "tier": ss.tier if ss else "",
                "rank": ss.rank if ss else 0,
                "vs_btc_1h": ss.vs_btc_1h if ss else 0,
                "entry_L": round(ss.entry_L, 1) if ss else 0,
                "entry_S": round(ss.entry_S, 1) if ss else 0,
                "exit_L": round(ss.exit_L, 1) if ss else 0,
                "exit_S": round(ss.exit_S, 1) if ss else 0,
                "k": ss.k if ss else {},
                "dc": {tf: round(v, 3) for tf, v in (ss.dc if ss else {}).items()},
                "ob": ss.ob_count if ss else 0,
                "signals": ss.signals if ss else [],
            },
        }
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        try:
            with open(PAPER_TRADES_DIR / f"paper_trades_{account}_{date_str}.jsonl", "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    def should_open(self, account: str, sym: str, side: str, ss: SymbolState) -> str | None:
        """Check if we should open a paper position. Returns reason or None.
        CORE PRINCIPLE: enter the moment a symbol JUMPS OUT — dc_position surging on 2+ TFs."""
        pk = f"{account}:{sym}_{side}"
        if pk in self.positions:
            return None
        self._init_account(account)
        if self.balance[account] < PAPER_MAX_POSITION_SIZE:
            return None
        # COOLDOWN: don't re-enter same key within N seconds of closing
        cooldown_key = f"_cooldown_{pk}"
        last_close = getattr(self, cooldown_key, 0)
        cooldown_s = 30 if account == "inf" else 60  # inf = faster re-entry
        if time.time() - last_close < cooldown_s:
            return None
        is_long = side == "LONG"
        entry_score = ss.entry_L if is_long else ss.entry_S
        k3m = ss.k.get("3m", 50)
        k15m = ss.k.get("15m", 50)
        dc_3m = ss.dc.get("3m", 0.5)
        dc_15m = ss.dc.get("15m", 0.5)
        dc_1h = ss.dc.get("1h", 0.5)
        dc_4h = ss.dc.get("4h", 0.5)
        # DON'T ENTER IF ALREADY SNAPPING — overextended = wait for pullback to settle
        if is_long and ss.ob_count >= 2:
            return None
        if not is_long and ss.os_count >= 2:
            return None
        # DON'T ENTER IF MOMENTUM IS FADING — k3m falling for longs, rising for shorts
        if is_long and not ss.k3m_rising and k3m < 60:
            # k3m not rising AND below 60 = no momentum building, skip
            if ss.wt_vel_3m < 0:  # WT velocity also negative = definitely fading
                return None
        if not is_long and ss.k3m_rising and k3m > 40:
            if ss.wt_vel_3m > 0:
                return None
        # ════════════════════════════════════════════════════════════════
        # INF ACCOUNT — AGGRESSIVE MODE
        # Lower thresholds, more entry paths, faster reaction to outliers
        # inf has the fast movers (WR/LR) — needs to catch every move
        # ════════════════════════════════════════════════════════════════
        if account == "inf":
            # INF OUTLIER: >1.5% from BTC (not 3%) — these ARE the opportunities
            if abs(ss.vs_btc_1h) > 1.5:
                if is_long and ss.vs_btc_1h > 1.5 and dc_3m >= 0.50 and k3m > 30:
                    return f"INF_OUTLIER_L(vs_btc={ss.vs_btc_1h:+.1f}%,dc3m={dc_3m:.2f},k3m={k3m:.0f})"
                if not is_long and ss.vs_btc_1h < -1.5 and dc_3m <= 0.50 and k3m < 70:
                    return f"INF_OUTLIER_S(vs_btc={ss.vs_btc_1h:+.1f}%,dc3m={dc_3m:.2f},k3m={k3m:.0f})"
            # INF MOMENTUM: dc_1h expanding + k3m rising = ride it
            if is_long and dc_1h >= 0.75 and dc_15m >= 0.65 and k3m > 40 and k3m < 85:
                return f"INF_MOMENTUM_L(dc_1h={dc_1h:.2f},dc_15m={dc_15m:.2f},k3m={k3m:.0f})"
            if not is_long and dc_1h <= 0.25 and dc_15m <= 0.35 and k3m < 60 and k3m > 15:
                return f"INF_MOMENTUM_S(dc_1h={dc_1h:.2f},dc_15m={dc_15m:.2f},k3m={k3m:.0f})"
            # INF DC EXPANSION: 3m just broke above 15m channel = fresh breakout
            if is_long and dc_3m >= 0.90 and dc_15m >= 0.70 and k3m > 50 and k3m < 90:
                return f"INF_FRESH_BREAK_L(dc3m={dc_3m:.2f},dc15m={dc_15m:.2f},k3m={k3m:.0f})"
            if not is_long and dc_3m <= 0.10 and dc_15m <= 0.30 and k3m < 50 and k3m > 10:
                return f"INF_FRESH_BREAK_S(dc3m={dc_3m:.2f},dc15m={dc_15m:.2f},k3m={k3m:.0f})"
        # ════════════════════════════════════════════════════════════════
        # ALL ACCOUNTS — standard entries
        # ════════════════════════════════════════════════════════════════
        # BREAKOUT ENTRY: dc_position surging on 2+ TFs
        if is_long:
            breakout_tfs = sum(1 for tf in ["3m", "15m", "1h"] if ss.dc.get(tf, 0.5) >= 0.85)
            if breakout_tfs >= 2 and k3m > 40:
                return f"BREAKOUT_L(dc=[{dc_3m:.2f},{dc_15m:.2f},{dc_1h:.2f}],k3m={k3m:.0f},score={entry_score:.0f})"
        else:
            breakdown_tfs = sum(1 for tf in ["3m", "15m", "1h"] if ss.dc.get(tf, 0.5) <= 0.15)
            if breakdown_tfs >= 2 and k3m < 60:
                return f"BREAKDOWN_S(dc=[{dc_3m:.2f},{dc_15m:.2f},{dc_1h:.2f}],k3m={k3m:.0f},score={entry_score:.0f})"
        # LEADER BREAKOUT: rally leader with strong momentum
        if ss.tier in ("LEADER", "STRONG") and "TRENDING" in ss.regime:
            if is_long and "UP" in ss.regime and dc_1h >= 0.80 and k3m > 30:
                return f"LEADER_L(tier={ss.tier},dc_1h={dc_1h:.2f},k3m={k3m:.0f},score={entry_score:.0f})"
            if not is_long and "DOWN" in ss.regime and dc_1h <= 0.20 and k3m < 70:
                return f"LEADER_S(tier={ss.tier},dc_1h={dc_1h:.2f},k3m={k3m:.0f},score={entry_score:.0f})"
        # OUTLIER ENTRY: diverging >3% from BTC
        if ss.is_outlier:
            if is_long and ss.vs_btc_1h > BTC_OUTLIER_1H and dc_3m >= 0.70:
                return f"OUTLIER_L(vs_btc={ss.vs_btc_1h:+.1f}%,dc3m={dc_3m:.2f})"
            if not is_long and ss.vs_btc_1h < -BTC_OUTLIER_1H and dc_3m <= 0.30:
                return f"OUTLIER_S(vs_btc={ss.vs_btc_1h:+.1f}%,dc3m={dc_3m:.2f})"
        # SCORE-BASED: high entry score confirms technical setup
        if entry_score >= PAPER_ENTRY_SCORE_MIN and "TRENDING" in ss.regime:
            if is_long and "UP" in ss.regime:
                return f"SCORE_L(score={entry_score:.0f},regime={ss.regime})"
            if not is_long and "DOWN" in ss.regime:
                return f"SCORE_S(score={entry_score:.0f},regime={ss.regime})"
        return None

    def should_close(self, pp: PaperPosition, ss: SymbolState) -> str | None:
        """Check if we should close a paper position. Returns reason or None.
        CORE PRINCIPLE: NEVER let a winning trade go negative. Close before PnL hits zero."""
        pnl = pp.pnl_pct(ss.price)
        is_long = pp.side == "LONG"
        pp.max_gain = max(pp.max_gain, pnl)
        pp.min_gain = min(pp.min_gain, pnl)
        exit_score = ss.exit_L if is_long else ss.exit_S
        hold_seconds = (datetime.now(timezone.utc) - pp.entry_time).total_seconds()
        # ════════════════════════════════════════════════════════════════
        # RULE #1: NEVER GO NEGATIVE AFTER BEING POSITIVE
        # The MOMENT pnl drops below 50% of max_gain, we're out.
        # For small gains (0.1-0.5%): close at 50% giveback
        # For medium gains (0.5-2%): close at 40% giveback
        # For large gains (2%+): close at 25% giveback
        # This replaces both PROTECT_GAIN and TRAILING — one unified rule.
        # ════════════════════════════════════════════════════════════════
        if pp.max_gain >= 0.10:
            if pp.max_gain >= 2.0:
                floor = pp.max_gain * 0.75  # Keep 75% of 2%+ gains
            elif pp.max_gain >= 0.5:
                floor = pp.max_gain * 0.60  # Keep 60% of 0.5-2% gains
            elif pp.max_gain >= 0.20:
                floor = pp.max_gain * 0.40  # Keep 40% of 0.2-0.5% gains
            else:
                floor = 0.02  # Was up 0.10-0.20% → close at 0.02% (above zero)
            if pnl <= floor:
                return f"PROTECT(max={pp.max_gain:.2f}%,floor={floor:.2f}%,now={pnl:.2f}%)"
        # ════════════════════════════════════════════════════════════════
        # RULE #3: SNAP-BACK — overextended + price broke prev candle = GET OUT
        # ════════════════════════════════════════════════════════════════
        if is_long and ss.ob_count >= 2 and ss.low_3m_prev > 0 and ss.price < ss.low_3m_prev:
            return f"SNAP_L(pnl={pnl:.2f}%,ob={ss.ob_count})"
        if not is_long and ss.os_count >= 2 and ss.high_3m_prev > 0 and ss.price > ss.high_3m_prev:
            return f"SNAP_S(pnl={pnl:.2f}%,os={ss.os_count})"
        # ════════════════════════════════════════════════════════════════
        # RULE #4: WT EXIT SIGNAL — velocity decelerating (the early warning)
        # ════════════════════════════════════════════════════════════════
        if exit_score >= PAPER_EXIT_SCORE_MIN and pnl > 0.05:
            return f"WT_EXIT(score={exit_score:.0f},pnl={pnl:.2f}%)"
        # ════════════════════════════════════════════════════════════════
        # RULE #5: REGIME FLIP — entered trending, now ranging/reversed
        # ════════════════════════════════════════════════════════════════
        if "TRENDING_UP" in pp.regime_at_entry and ("DOWN" in ss.regime or ss.regime == "RANGING"):
            return f"REGIME_FLIP(was={pp.regime_at_entry},now={ss.regime},pnl={pnl:.2f}%)"
        if "TRENDING_DOWN" in pp.regime_at_entry and ("UP" in ss.regime or ss.regime == "RANGING"):
            return f"REGIME_FLIP(was={pp.regime_at_entry},now={ss.regime},pnl={pnl:.2f}%)"
        # ════════════════════════════════════════════════════════════════
        # RULE #6: DC BREACH — price fell out of channel
        # ════════════════════════════════════════════════════════════════
        if is_long and ss.dc_low_3m > 0 and ss.price < ss.dc_low_3m:
            return f"DC_BREACH_L(pnl={pnl:.2f}%)"
        if not is_long and ss.dc_high_3m > 0 and ss.price > ss.dc_high_3m:
            return f"DC_BREACH_S(pnl={pnl:.2f}%)"
        # ════════════════════════════════════════════════════════════════
        # RULE #7: EARLY CUT — only if clearly going wrong AND momentum confirms
        # 3 min + never profitable + loss deepening + momentum against us
        # ════════════════════════════════════════════════════════════════
        if hold_seconds > 180 and pnl < -0.3 and pp.max_gain < 0.05:
            # Momentum must be against us too — don't cut if it's just choppy
            is_long = pp.side == "LONG"
            mom_against = (is_long and not ss.k3m_rising and ss.wt_vel_3m < -2) or (not is_long and ss.k3m_rising and ss.wt_vel_3m > 2)
            if mom_against:
                return f"EARLY_CUT(pnl={pnl:.2f}%,hold={hold_seconds:.0f}s,mom_against)"
        # ════════════════════════════════════════════════════════════════
        # RULE #8: TIME STOP — 2 hours with no meaningful profit
        # ════════════════════════════════════════════════════════════════
        if hold_seconds > 7200 and pnl < 0.2:
            return f"TIME_STOP(hold={hold_seconds/60:.0f}min,pnl={pnl:.2f}%)"
        return None

    def open_position(self, account: str, sym: str, side: str, ss: SymbolState, reason: str):
        """Open a paper position."""
        pk = f"{account}:{sym}_{side}"
        self._init_account(account)
        size = min(PAPER_MAX_POSITION_SIZE, self.balance[account])
        qty = size / ss.price
        pp = PaperPosition(pk, account, sym, side, ss.price, qty, reason,
                           ss.entry_L if side == "LONG" else ss.entry_S, ss.regime, ss.tier)
        self.positions[pk] = pp
        self.balance[account] -= size
        self.trade_count[account] = self.trade_count.get(account, 0) + 1
        self._log_trade(account, "OPEN", pk, ss.price, qty, reason, ss=ss)
        logger.info(f"[PAPER OPEN] {pk} {side} @ {ss.price:.6g} ${size:.1f} | {reason}")

    def close_position(self, pp: PaperPosition, ss: SymbolState, reason: str):
        """Close a paper position and realize PnL."""
        pnl_pct = pp.pnl_pct(ss.price)
        notional = pp.notional(ss.price)
        pnl_usd = notional * pnl_pct / 100
        self.balance[pp.account] = self.balance.get(pp.account, 0) + notional
        self.closed_pnl[pp.account] = self.closed_pnl.get(pp.account, 0) + pnl_usd
        self._log_trade(pp.account, "CLOSE", pp.key, ss.price, pp.qty, reason, pnl_pct, pnl_usd, ss)
        hold_min = (datetime.now(timezone.utc) - pp.entry_time).total_seconds() / 60
        logger.info(f"[PAPER CLOSE] {pp.key} @ {ss.price:.6g} pnl={pnl_pct:+.2f}% ${pnl_usd:+.2f} hold={hold_min:.0f}min max_gain={pp.max_gain:.2f}% | {reason}")
        # Set cooldown to prevent immediate re-entry
        setattr(self, f"_cooldown_{pp.key}", time.time())
        del self.positions[pp.key]

    def evaluate(self, account: str, sym: str, sides: set, ss: SymbolState):
        """Check entries and exits for one symbol across all sides."""
        for side in sides:
            pk = f"{account}:{sym}_{side}"
            if pk in self.positions:
                # CHECK EXIT
                pp = self.positions[pk]
                pp.max_gain = max(pp.max_gain, pp.pnl_pct(ss.price))
                pp.min_gain = min(pp.min_gain, pp.pnl_pct(ss.price))
                close_reason = self.should_close(pp, ss)
                if close_reason:
                    self.close_position(pp, ss, close_reason)
            else:
                # CHECK ENTRY
                open_reason = self.should_open(account, sym, side, ss)
                if open_reason:
                    self.open_position(account, sym, side, ss, open_reason)

    def summary(self) -> str:
        """One-line summary for logging."""
        n_pos = len(self.positions)
        total_pnl = sum(self.closed_pnl.values())
        total_trades = sum(self.trade_count.values())
        unrealized = 0.0
        for pp in self.positions.values():
            ss = None  # Can't compute without current price here — will be 0
        return f"positions={n_pos} trades={total_trades} realized=${total_pnl:+.2f}"


# ═══════════════════════════════════════════════════════════════════════
# DAEMON
# ═══════════════════════════════════════════════════════════════════════

class AdaptiveRegimeDaemon:

    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.symbols: dict[str, SymbolState] = {}
        self.tradeable = {}
        self.portfolio = PaperPortfolio()
        self.btc_pct_1h = 0.0
        self.btc_pct_4h = 0.0
        self._running = True
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, '_running', False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, '_running', False))

    # ── Data loading ──────────────────────────────────────────────────

    def _load_all(self, r) -> dict:
        raw = r.get("latest_market_data")
        return json.loads(raw) if raw else {}

    def _load_tradeable(self):
        self.tradeable = load_tradeable_keys()
        self._tradeable_syms = set()
        for syms in self.tradeable.values():
            self._tradeable_syms.update(syms.keys())
        # _all_syms = ALL symbols from latest_market_data (not just tradeable)
        # This ensures we detect outliers even if they're not in tradeable_keys
        self._all_syms = set()  # Populated on first data load

    # ── Classify one symbol ───────────────────────────────────────────

    def _classify(self, sym: str, m: dict) -> SymbolState:
        """Full classification: regime, breakout, overextension, scores, config."""
        ss = self.symbols.get(sym) or SymbolState(sym)
        ss.price = _sf(m.get("current_price"))
        if ss.price <= 0:
            return ss
        # DC positions across TFs
        breakout_up = 0
        breakout_dn = 0
        heat = 0.0
        tf_weights = {"3m": 1, "15m": 2, "1h": 4, "4h": 8, "D": 16}
        for tf in TFS:
            dc = _sf(m.get(f"dc_position_{tf}"), 0.5)
            if dc > 1: dc /= 100
            dc = max(0.0, min(1.0, dc))
            ss.dc[tf] = dc
            w = tf_weights[tf]
            if dc >= DC_BREAKOUT:
                breakout_up += 1
                heat += w * (dc - DC_BREAKOUT) / (1.0 - DC_BREAKOUT)
            elif dc <= DC_BREAKDOWN:
                breakout_dn += 1
                heat += w * (DC_BREAKDOWN - dc) / DC_BREAKDOWN
        ss.heat = round(heat, 2)
        if breakout_up + breakout_dn >= 2:
            ss.regime = "TRENDING_UP" if breakout_up > breakout_dn else "TRENDING_DOWN"
        elif breakout_up + breakout_dn == 1 and heat > 5:
            ss.regime = "TRENDING_WEAK_UP" if breakout_up else "TRENDING_WEAK_DOWN"
        else:
            ss.regime = "RANGING"
        # Stoch K across LTFs
        ss.k["1m"] = _sf(m.get("stoch_k_1m", m.get("k_1m")), 50)
        ss.k["3m"] = _sf(m.get("stoch_k_3m", m.get("k_3m")), 50)
        ss.k["15m"] = _sf(m.get("stoch_k_15m"), 50)
        ss.k["1h"] = _sf(m.get("stoch_k_1h"), 50)
        ss.ob_count = sum(1 for v in ss.k.values() if v > 80)
        ss.os_count = sum(1 for v in ss.k.values() if v < 20)
        # K3m direction — is momentum building or fading?
        new_k3m = ss.k.get("3m", 50)
        ss.k3m_rising = new_k3m > ss.k3m_prev + 1.0  # Must rise by >1 point to count
        ss.k3m_prev = new_k3m
        ss.wt_vel_3m = _sf(m.get("wt_velocity_3m"), 0)
        # Price action levels
        ss.low_3m_prev = _sf(m.get("low_3m_prev"))
        ss.high_3m_prev = _sf(m.get("high_3m_prev"))
        ss.dc_low_3m = _sf(m.get("dc_low_3m"))
        ss.dc_high_3m = _sf(m.get("dc_high_3m"))
        # BTC-relative move
        close_1h = _sf(m.get("close_1h"))
        close_4h = _sf(m.get("close_4h"))
        ss.pct_1h = ((ss.price - close_1h) / close_1h * 100) if close_1h > 0 else 0
        ss.pct_4h = ((ss.price - close_4h) / close_4h * 100) if close_4h > 0 else 0
        ss.vs_btc_1h = round(ss.pct_1h - self.btc_pct_1h, 2)
        ss.vs_btc_4h = round(ss.pct_4h - self.btc_pct_4h, 2)
        ss.is_outlier = abs(ss.vs_btc_1h) > BTC_OUTLIER_1H or abs(ss.vs_btc_4h) > BTC_OUTLIER_4H
        # Entry/exit scores
        safe = _sanitize_for_scorer(m)
        try:
            ss.entry_L, _ = score_entry(safe, True, ss.price)
            ss.entry_S, _ = score_entry(safe, False, ss.price)
        except Exception:
            ss.entry_L = ss.entry_S = 0
        try:
            ss.exit_L, _ = score_exit(safe, True, ss.price)
            ss.exit_S, _ = score_exit(safe, False, ss.price)
        except Exception:
            ss.exit_L = ss.exit_S = 0
        ss.wt_bias = str(m.get("wt_composite_bias", ""))
        ss.last_update = time.time()
        # ── Generate signals + config overrides ───────────────────────
        ss.signals = []
        ov = {}
        is_trending = "TRENDING" in ss.regime
        # CONFIG: trending vs ranging
        if is_trending:
            ov["K3M_CAP"] = 95
            ov["HTF_STRICT"] = False
            ov["ENTRY_ATR_PCT_MIN"] = 0.0
            ov["MTS_BOTTOM_MIN"] = 5.0
            ov["TF_ALIGNMENT_MIN_TOTAL"] = 3
            ov["NOLOSS_MIN_PROFIT_PCT"] = 0.0
            ov["FAST_CUT_LOSS_THRESHOLD"] = -2.0
        else:
            ov["K3M_CAP"] = 60
            ov["HTF_STRICT"] = True
            ov["ENTRY_ATR_PCT_MIN"] = 0.3
            ov["MTS_BOTTOM_MIN"] = 20.0
            ov["TF_ALIGNMENT_MIN_TOTAL"] = 5
            ov["NOLOSS_MIN_PROFIT_PCT"] = 0.2
            ov["FAST_CUT_LOSS_THRESHOLD"] = -0.5
        # RALLY TIER adjustments (set later by rank_all)
        if ss.tier == "LEADER" and is_trending:
            ov["K3M_CAP"] = 100
            ov["MTS_BOTTOM_MIN"] = 3.0
            ov["TF_ALIGNMENT_MIN_TOTAL"] = 2
        elif ss.tier == "WEAKEST" and is_trending:
            ov["NOLOSS_MIN_PROFIT_PCT"] = 0.15
            ov["FAST_CUT_LOSS_THRESHOLD"] = -0.8
        # SNAP-BACK DETECTION: overextended + price breaking prev candle low/high
        if ss.ob_count >= 2 and ss.low_3m_prev > 0 and ss.price < ss.low_3m_prev:
            ov["NOLOSS_MIN_PROFIT_PCT"] = 0.01
            ov["FAST_CUT_LOSS_THRESHOLD"] = -0.3
            ov["K3M_CAP"] = 50
            ss.signals.append(f"SNAP_LONG(p={ss.price:.6g}<low3prev={ss.low_3m_prev:.6g},ob={ss.ob_count}TF)")
        if ss.os_count >= 2 and ss.high_3m_prev > 0 and ss.price > ss.high_3m_prev:
            ov["NOLOSS_MIN_PROFIT_PCT"] = 0.01
            ov["FAST_CUT_LOSS_THRESHOLD"] = -0.3
            ov["K3M_CAP"] = 50
            ss.signals.append(f"SNAP_SHORT(p={ss.price:.6g}>hi3prev={ss.high_3m_prev:.6g},os={ss.os_count}TF)")
        # EMERGENCY: 3+ TFs overextended + candle break
        if ss.ob_count >= 3 and ss.low_3m_prev > 0 and ss.price < ss.low_3m_prev:
            ov["FAST_CUT_LOSS_THRESHOLD"] = -0.15
            ss.signals.append(f"EMERGENCY_SNAP({ss.ob_count}OB)")
        if ss.os_count >= 3 and ss.high_3m_prev > 0 and ss.price > ss.high_3m_prev:
            ov["FAST_CUT_LOSS_THRESHOLD"] = -0.15
            ss.signals.append(f"EMERGENCY_SNAP({ss.os_count}OS)")
        # EXIT SCORE HIGH → tighten
        if ss.exit_L >= 50:
            ov["NOLOSS_MIN_PROFIT_PCT"] = min(ov.get("NOLOSS_MIN_PROFIT_PCT", 1), 0.05)
            ss.signals.append(f"EXIT_HOT_L({ss.exit_L:.0f})")
        if ss.exit_S >= 50:
            ov["NOLOSS_MIN_PROFIT_PCT"] = min(ov.get("NOLOSS_MIN_PROFIT_PCT", 1), 0.05)
            ss.signals.append(f"EXIT_HOT_S({ss.exit_S:.0f})")
        # OUTLIER tag
        if ss.is_outlier:
            ss.signals.append(f"OUTLIER(vs_btc={ss.vs_btc_1h:+.1f}%)")
        ss.overrides = ov
        self.symbols[sym] = ss
        return ss

    # ── Rank all symbols by strength ──────────────────────────────────

    def _rank_all(self, all_metrics: dict):
        """Rank symbols by rally strength. Must be called AFTER classifying all symbols."""
        scores = {}
        tf_w = {"3m": 1, "15m": 2, "1h": 4, "4h": 8, "D": 16}
        for sym, ss in self.symbols.items():
            s = 0.0
            for tf, w in tf_w.items():
                s += ss.dc.get(tf, 0.5) * w
            m = all_metrics.get(sym, {})
            for tf in TFS:
                vel = _sf(m.get(f"wt_velocity_{tf}"))
                s += max(0, vel) * tf_w.get(tf, 1) * 0.3
            rvol = _sf(m.get("relative_volume_1h"), 1.0)
            if rvol > 1.5:
                s *= min(rvol * 0.5 + 0.5, 2.0)
            scores[sym] = s
        sorted_s = sorted(scores.items(), key=lambda x: -x[1])
        n = max(len(sorted_s), 1)
        for i, (sym, score) in enumerate(sorted_s):
            pct = (n - i) / n
            tier = "LEADER" if pct >= 0.8 else ("STRONG" if pct >= 0.5 else ("LAGGING" if pct >= 0.2 else "WEAKEST"))
            ss = self.symbols.get(sym)
            if ss:
                ss.strength = round(score, 1)
                ss.tier = tier
                ss.rank = i + 1

    # ── Publish config overrides ──────────────────────────────────────

    def _publish(self, account: str, sym: str, sides: set, ss: SymbolState):
        """Push config to Config class + Redis.
        LIVE_ACCOUNTS get _paper=False → overrides flow into real trading logic.
        Other accounts get _paper=True → logged but not applied."""
        is_live = account in LIVE_ACCOUNTS
        for side in sides:
            pk = f"{account}:{sym}_{side}"
            payload = {**ss.overrides, "_regime": ss.regime, "_heat": ss.heat, "_tier": ss.tier,
                       "_rank": ss.rank, "_vs_btc": ss.vs_btc_1h, "_signals": ss.signals,
                       "_updated_utc": datetime.now(timezone.utc).isoformat(), "_paper": not is_live}
            Config.set_regime_override(pk, payload, source="regime_live" if is_live else "regime_paper")
        # Redis — per account:symbol_side key so _paper flag is per-account
        try:
            r = get_redis()
            for side in sides:
                pk = f"{account}:{sym}_{side}"
                r.set(f"regime_cfg:{pk}", json.dumps({**ss.overrides, "_paper": not is_live,
                    "_regime": ss.regime, "_tier": ss.tier, "ts": time.time()}, default=str), ex=120)
        except Exception:
            pass

    def _log_paper(self, account: str, sym: str, ss: SymbolState):
        if not ss.signals:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "sym": sym, "acct": account,
                 "regime": ss.regime, "tier": ss.tier, "rank": ss.rank,
                 "vs_btc": ss.vs_btc_1h, "signals": ss.signals, "overrides": ss.overrides,
                 "k": ss.k, "dc": ss.dc, "price": ss.price,
                 "entry_L": round(ss.entry_L, 1), "entry_S": round(ss.entry_S, 1),
                 "exit_L": round(ss.exit_L, 1), "exit_S": round(ss.exit_S, 1)}
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        try:
            with open(PAPER_LOG_DIR / f"paper_regime_{account}_{date_str}.jsonl", "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    # ── Process a set of symbols ──────────────────────────────────────

    def _process(self, all_metrics: dict, symbol_filter: set = None):
        """Classify + publish + evaluate paper trades for ALL symbols.
        Tradeable symbols get real config overrides. Non-tradeable get paper trades only."""
        published = 0
        snaps = 0
        # 1. Process tradeable symbols (real config overrides + paper trades)
        for account, symbols in self.tradeable.items():
            for sym, sides in symbols.items():
                if symbol_filter and sym not in symbol_filter:
                    continue
                m = all_metrics.get(sym)
                if not m:
                    continue
                ss = self._classify(sym, m)
                self._publish(account, sym, sides, ss)
                self.portfolio.evaluate(account, sym, sides, ss)
                published += 1
                if any("SNAP" in s or "EMERGENCY" in s for s in ss.signals):
                    snaps += 1
                    logger.warning(f"[{sym}] {' | '.join(ss.signals)} regime={ss.regime} tier={ss.tier} vs_btc={ss.vs_btc_1h:+.1f}%")
                self._log_paper(account, sym, ss)
        # 2. Scan ALL non-tradeable symbols for outliers — paper trade under "scan" account
        for sym in self._all_syms:
            if sym in self._tradeable_syms:
                continue  # Already processed above
            if symbol_filter and sym not in symbol_filter:
                continue
            m = all_metrics.get(sym)
            if not m:
                continue
            ss = self._classify(sym, m)
            # Paper trade outliers + breakouts under "scan" virtual account
            if ss.is_outlier or "TRENDING" in ss.regime or ss.heat > 5:
                self.portfolio.evaluate("scan", sym, {"LONG", "SHORT"}, ss)
                published += 1
                if ss.is_outlier or ("TRENDING" in ss.regime and ss.heat > 5):
                    self._inject_into_tradeable_keys(sym, ss)
        return published, snaps

    def _inject_into_tradeable_keys(self, sym: str, ss: SymbolState):
        """Inject outlier symbol into tradeable_keys.json for LIVE accounts.
        Called when a non-tradeable symbol is breaking out or diverging from BTC."""
        if not hasattr(self, '_injected_syms'):
            self._injected_syms = set()
        if sym in self._injected_syms:
            return  # Already injected this session
        # Determine side: outperforming = LONG, underperforming = SHORT
        sides = []
        if ss.vs_btc_1h > 1.0 or (ss.dc.get("1h", 0.5) > 0.80 and "UP" in ss.regime):
            sides.append("LONG")
        if ss.vs_btc_1h < -1.0 or (ss.dc.get("1h", 0.5) < 0.20 and "DOWN" in ss.regime):
            sides.append("SHORT")
        if not sides:
            sides = ["LONG", "SHORT"]  # Both sides if unclear
        # Read current tradeable_keys
        tk_path = BASE_PATH / "tradeable_keys.json"
        try:
            with open(tk_path) as f:
                current_keys = json.load(f)
        except Exception:
            return
        added = []
        for account in LIVE_ACCOUNTS:
            for side in sides:
                new_key = f"{account}:{sym}_{side}"
                if new_key not in current_keys:
                    current_keys.append(new_key)
                    added.append(new_key)
        if added:
            # Write back atomically (temp + os.replace) — matches ez_positions_service.py /
            # ez_outlier_hunter.py convention. 2026-07-22: direct `open(tk_path, "w")` here was
            # the only non-atomic writer of this shared file and produced torn/concatenated JSON
            # ("[KEYS] Error reading tradeable_keys.json: Extra data...") when racing the other
            # writers' atomic os.replace().
            try:
                tmp_path = tk_path.with_suffix(".tmp")
                with open(tmp_path, "w") as f:
                    json.dump(sorted(set(current_keys)), f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, tk_path)
                # Also push to Redis for faster pickup
                r = get_redis()
                r.set("tradeable_keys", json.dumps(sorted(set(current_keys))))
            except Exception as e:
                logger.error(f"Failed to write tradeable_keys: {e}")
                return
            self._injected_syms.add(sym)
            self._tradeable_syms.add(sym)
            # Update our own tradeable dict
            for account in LIVE_ACCOUNTS:
                if account not in self.tradeable:
                    self.tradeable[account] = {}
                self.tradeable[account].setdefault(sym, set()).update(sides)
            logger.warning(f"[INJECTED] {sym} → tradeable_keys: {added} (vs_btc={ss.vs_btc_1h:+.1f}%, dc_1h={ss.dc.get('1h', 0):.2f}, regime={ss.regime})")
            # Log to file for tracking
            try:
                with open(DATA_DIR / "injected_symbols.jsonl", "a") as f:
                    f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "sym": sym,
                        "added": added, "vs_btc": ss.vs_btc_1h, "regime": ss.regime, "dc": ss.dc}, default=str) + "\n")
            except Exception:
                pass

    # ── Identify which symbols need fast monitoring ───────────────────

    def _get_fast_symbols(self) -> set:
        """Symbols that need second-by-second attention:
        - OPEN PAPER POSITIONS (always watch what we hold)
        - Outliers (>3% from BTC on 1h)
        - Active snap-backs
        - High exit scores (>40)
        - Leaders + Weakest in trending"""
        fast = set()
        # Always fast-track symbols we hold paper positions in
        for pk in self.portfolio.positions:
            sym = pk.split(":")[1].rsplit("_", 1)[0] if ":" in pk else pk.rsplit("_", 1)[0]
            fast.add(sym)
        # ALL inf symbols are fast-tracked — inf has fast movers, needs second-by-second
        inf_syms = self.tradeable.get("inf", {})
        fast.update(inf_syms.keys())
        for sym, ss in self.symbols.items():
            if ss.is_outlier:
                fast.add(sym)
            if any("SNAP" in s or "EMERGENCY" in s for s in ss.signals):
                fast.add(sym)
            if ss.exit_L > 40 or ss.exit_S > 40:
                fast.add(sym)
            if ss.tier == "LEADER" and "TRENDING" in ss.regime:
                fast.add(sym)
            if ss.tier == "WEAKEST" and "TRENDING" in ss.regime:
                fast.add(sym)
        return fast

    # ── Main loop ─────────────────────────────────────────────────────

    def run(self):
        logger.info(f"Adaptive Regime v2 starting (paper={self.paper_mode})")
        logger.info(f"FAST=1s (outliers + snaps + leaders + weakest), SLOW=30s (rest)")
        r = get_redis()
        self._load_tradeable()
        # Initial full scan — ALL symbols from latest_market_data
        all_m = self._load_all(r)
        self._all_syms = set(all_m.keys())  # ALL 196+ symbols, not just tradeable
        logger.info(f"Scanning ALL {len(self._all_syms)} symbols ({len(self._tradeable_syms)} tradeable, {len(self._all_syms) - len(self._tradeable_syms)} scan-only)")
        btc = all_m.get("BTCUSDC", all_m.get("BTCUSDC", {}))
        btc_close_1h = _sf(btc.get("close_1h"))
        btc_close_4h = _sf(btc.get("close_4h"))
        btc_price = _sf(btc.get("current_price"))
        self.btc_pct_1h = ((btc_price - btc_close_1h) / btc_close_1h * 100) if btc_close_1h > 0 else 0
        self.btc_pct_4h = ((btc_price - btc_close_4h) / btc_close_4h * 100) if btc_close_4h > 0 else 0
        for sym in self._all_syms:
            m = all_m.get(sym)
            if m:
                self._classify(sym, m)
        self._rank_all(all_m)
        # Re-classify with ranks applied (so tier-based overrides work)
        pub, snp = self._process(all_m)
        fast_syms = self._get_fast_symbols()
        logger.info(f"Initial: {pub} symbols, {snp} snaps, {len(fast_syms)} fast-tracked, BTC {self.btc_pct_1h:+.1f}%/1h")
        # Loop
        last_slow = time.time()
        last_tradeable_reload = time.time()
        fast_cycle = 0
        slow_cycle = 0
        while self._running:
            try:
                now = time.time()
                all_m = self._load_all(r)
                if not all_m:
                    time.sleep(1)
                    continue
                # Update BTC baseline
                btc = all_m.get("BTCUSDC", all_m.get("BTCUSDC", {}))
                btc_price = _sf(btc.get("current_price"))
                btc_c1 = _sf(btc.get("close_1h"))
                btc_c4 = _sf(btc.get("close_4h"))
                self.btc_pct_1h = ((btc_price - btc_c1) / btc_c1 * 100) if btc_c1 > 0 else 0
                self.btc_pct_4h = ((btc_price - btc_c4) / btc_c4 * 100) if btc_c4 > 0 else 0
                # FAST: outliers + snaps + leaders + weakest every 1s
                if fast_syms:
                    t0 = time.time()
                    fp, fs = self._process(all_m, fast_syms)
                    fast_cycle += 1
                    if fast_cycle % 60 == 0:
                        names = sorted(fast_syms)[:6]
                        logger.info(f"FAST #{fast_cycle}: {len(fast_syms)} syms in {time.time()-t0:.2f}s, {fs} snaps | {names}{'...' if len(fast_syms) > 6 else ''}")
                # SLOW: full scan every 30s
                if now - last_slow >= 30:
                    t0 = time.time()
                    for sym in self._all_syms:
                        m = all_m.get(sym)
                        if m:
                            self._classify(sym, m)
                    self._rank_all(all_m)
                    sp, ss_cnt = self._process(all_m)
                    fast_syms = self._get_fast_symbols()
                    slow_cycle += 1
                    last_slow = now
                    regimes = defaultdict(int)
                    for s in self.symbols.values():
                        regimes[s.regime] += 1
                    r_str = " ".join(f"{k}={v}" for k, v in sorted(regimes.items()))
                    p_sum = self.portfolio.summary()
                    logger.info(f"SLOW #{slow_cycle}: {sp} syms, {ss_cnt} snaps, fast={len(fast_syms)} | BTC {self.btc_pct_1h:+.1f}%/1h | {r_str} | PAPER: {p_sum}")
                    self._save_state()
                # Reload tradeable keys every 5 min
                if now - last_tradeable_reload > 300:
                    self._load_tradeable()
                    last_tradeable_reload = now
                time.sleep(1.0)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                time.sleep(3)
        self._save_state()
        logger.info("Stopped.")

    def _save_state(self):
        state = {}
        for sym, ss in self.symbols.items():
            state[sym] = {
                "regime": ss.regime, "heat": ss.heat, "tier": ss.tier,
                "rank": ss.rank, "strength": ss.strength,
                "vs_btc_1h": ss.vs_btc_1h, "outlier": ss.is_outlier,
                "dc": ss.dc, "k": ss.k, "price": ss.price,
                "entry_L": round(ss.entry_L, 1), "entry_S": round(ss.entry_S, 1),
                "exit_L": round(ss.exit_L, 1), "exit_S": round(ss.exit_S, 1),
                "signals": ss.signals, "ob": ss.ob_count, "os": ss.os_count,
            }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=1, default=str)
            with open(OVERRIDE_SNAPSHOT, "w") as f:
                overrides = {sym: ss.overrides for sym, ss in self.symbols.items() if ss.overrides}
                json.dump(overrides, f, indent=1, default=str)
        except Exception:
            pass
        self.portfolio.save()


def show_status():
    if not STATE_FILE.exists():
        print("No state. Run --daemon first.")
        return
    with open(STATE_FILE) as f:
        state = json.load(f)
    print(f"\n{'Symbol':<16} {'Regime':<16} {'Tier':<8} {'Rank':>5} {'vsBTC':>6} {'DC_1h':>6} {'DC_4h':>6} {'K3m':>4} {'OB':>3} {'EntL':>5} {'ExL':>4} {'Signals'}")
    print("-" * 120)
    for sym, info in sorted(state.items(), key=lambda x: -abs(x[1].get("vs_btc_1h", 0))):
        dc = info.get("dc", {})
        k = info.get("k", {})
        sigs = info.get("signals", [])
        sig_str = " | ".join(sigs[:2]) if sigs else ""
        print(f"{sym:<16} {info.get('regime','?'):<16} {info.get('tier',''):<8} {info.get('rank',0):>5} {info.get('vs_btc_1h',0):>+5.1f}% {dc.get('1h',0):>6.3f} {dc.get('4h',0):>6.3f} {k.get('3m',0):>4.0f} {info.get('ob',0):>3} {info.get('entry_L',0):>5.0f} {info.get('exit_L',0):>4.0f} {sig_str}")
    n = len(state)
    outliers = sum(1 for v in state.values() if v.get("outlier"))
    snaps = sum(1 for v in state.values() for s in v.get("signals", []) if "SNAP" in s)
    trending = sum(1 for v in state.values() if "TRENDING" in v.get("regime", ""))
    print(f"\n{n} symbols | {trending} trending | {outliers} outliers (>3% vs BTC) | {snaps} snapping")
    # Paper portfolio
    pp_path = DATA_DIR / "paper_portfolio.json"
    if pp_path.exists():
        with open(pp_path) as f:
            pp = json.load(f)
        positions = pp.get("positions", {})
        balances = pp.get("balance", {})
        closed = pp.get("closed_pnl", {})
        trades = pp.get("trade_count", {})
        print(f"\n{'='*60}")
        print(f"PAPER PORTFOLIO")
        print(f"{'='*60}")
        if positions:
            print(f"\n{'Position':<30} {'Side':<6} {'Entry':>10} {'Current':>10} {'PnL%':>7} {'Hold':>8}")
            print("-" * 80)
            for pk, pos in sorted(positions.items()):
                sym = pos["symbol"]
                cur_price = state.get(sym, {}).get("price", pos["entry_price"])
                if pos["side"] == "LONG":
                    pnl = (cur_price - pos["entry_price"]) / pos["entry_price"] * 100
                else:
                    pnl = (pos["entry_price"] - cur_price) / pos["entry_price"] * 100
                entry_t = datetime.fromisoformat(pos["entry_time"])
                hold = (datetime.now(timezone.utc) - entry_t).total_seconds() / 60
                print(f"{pk:<30} {pos['side']:<6} {pos['entry_price']:>10.6g} {cur_price:>10.6g} {pnl:>+6.2f}% {hold:>7.0f}m")
        else:
            print("\nNo open positions")
        print(f"\n{'Account':<10} {'Balance':>10} {'Realized':>10} {'Trades':>8}")
        print("-" * 45)
        for acct in sorted(set(list(balances.keys()) + list(closed.keys()))):
            print(f"{acct:<10} ${balances.get(acct, 0):>9.2f} ${closed.get(acct, 0):>+9.2f} {trades.get(acct, 0):>8}")
        total_realized = sum(closed.values())
        total_trades = sum(trades.values())
        print(f"{'TOTAL':<10} {'':>10} ${total_realized:>+9.2f} {total_trades:>8}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.status:
        show_status()
    else:
        d = AdaptiveRegimeDaemon(paper_mode=not args.live)
        if args.daemon:
            d.run()
        else:
            r = get_redis()
            all_m = d._load_all(r)
            d._load_tradeable()
            btc = all_m.get("BTCUSDC", {})
            d.btc_pct_1h = ((_sf(btc.get("current_price")) - _sf(btc.get("close_1h"))) / max(_sf(btc.get("close_1h")), 1) * 100)
            for sym in d._all_syms:
                m = all_m.get(sym)
                if m:
                    d._classify(sym, m)
            d._rank_all(all_m)
            d._process(all_m)
            d._save_state()
            show_status()
