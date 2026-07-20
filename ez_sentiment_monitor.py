#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
SENTIMENT-REACTIVE POSITION MONITOR — Adapts hold/exit based on market sentiment.

Rules:
  - BULLISH sentiment (>25): Extend hold time for longs, tighten shorts
  - BEARISH sentiment (<-25): Close longs even at <1% gain, extend shorts
  - REVERSAL detection: If sentiment flips sign while holding, emergency exit

Reads: latest_market_data.json (sentiment), position files (gains)
Writes: Redis signals for ez_positions_quick to act on

Runs as a background loop (5s interval). Designed for binance-sandbox first.
"""
import asyncio
import json
import logging
import os
import platform
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s [SENT_MON] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")

try:
    sys.path.insert(0, str(BASE_PATH))
    from config import Config
    config = Config()
except Exception:
    config = None

SCAN_INTERVAL = 5.0
SENTIMENT_SMOOTHING_ALPHA = 0.1  # EMA smoothing for sentiment changes
shutdown_flag = False


def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True
    logger.info("Shutdown requested...")

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══ SENTIMENT THRESHOLDS (configurable) ════════════════════════════════════

class SentimentConfig:
    # Sentiment classification thresholds
    BULLISH_THRESHOLD = 25.0           # Global sentiment > 25 = bullish
    BEARISH_THRESHOLD = -25.0          # Global sentiment < -25 = bearish
    STRONG_BULLISH_THRESHOLD = 50.0
    STRONG_BEARISH_THRESHOLD = -50.0
    EXTREME_BULLISH_THRESHOLD = 75.0
    EXTREME_BEARISH_THRESHOLD = -75.0
    # Hold extension for longs in bullish markets
    BULLISH_LONG_HOLD_MULT = 2.0       # 2x hold time in bullish sentiment
    STRONG_BULLISH_LONG_HOLD_MULT = 3.0
    # Early exit thresholds
    BEARISH_LONG_EXIT_GAIN = 0.5       # Exit longs at 0.5% if sentiment turns bearish
    STRONG_BEARISH_LONG_EXIT_GAIN = 0.0  # Exit longs at ANY gain if strong bearish
    BEARISH_SHORT_HOLD_MULT = 2.0      # 2x hold time for shorts in bearish sentiment
    # Reversal detection
    REVERSAL_LOOKBACK = 5              # Check last 5 readings for sign flip
    REVERSAL_MIN_MAGNITUDE = 30.0      # Sentiment must swing 30+ points to count as reversal
    # Velocity thresholds
    VELOCITY_FAST_DROP = -5.0          # Sentiment dropping 5+ points per reading = fast reversal
    VELOCITY_FAST_RISE = 5.0           # Sentiment rising 5+ points per reading = fast recovery


# ═══ DATA LOADING ═══════════════════════════════════════════════════════════

def load_sentiment() -> Dict[str, float]:
    """Load current market sentiment from latest_market_data.json or Redis."""
    result = {"global": 0.0, "global_ema": 0.0, "classification": "NEUTRAL"}
    # Try Redis first
    try:
        import redis
        r = redis.Redis(host='127.0.0.1', port=6379 if platform.system() != "Darwin" else 6381, db=0, decode_responses=True)
        raw = r.get('latest_market_data')
        if raw:
            data = json.loads(raw)
            # Get global sentiment from first symbol with data
            for sym, fields in data.items():
                if isinstance(fields, dict) and '0market_sentiment_score' in fields:
                    result["global"] = float(fields.get('0market_sentiment_score', 0) or 0)
                    result["global_ema"] = float(fields.get('0market_sentiment_score_ema', 0) or 0)
                    result["classification"] = str(fields.get('0sentiment_classification', 'NEUTRAL') or 'NEUTRAL')
                    break
            return result
    except Exception:
        pass
    # Fallback: disk
    try:
        md_path = BASE_PATH / "data" / "latest_market_data.json"
        if md_path.exists():
            data = json.loads(md_path.read_text())
            for sym, fields in data.items():
                if isinstance(fields, dict) and '0market_sentiment_score' in fields:
                    result["global"] = float(fields.get('0market_sentiment_score', 0) or 0)
                    result["global_ema"] = float(fields.get('0market_sentiment_score_ema', 0) or 0)
                    result["classification"] = str(fields.get('0sentiment_classification', 'NEUTRAL') or 'NEUTRAL')
                    break
    except Exception:
        pass
    return result


def load_positions(account_keys=None) -> Dict[str, dict]:
    """Load all positions across accounts."""
    if account_keys is None:
        account_keys = getattr(config, 'ACCOUNT_KEYS', ['ang', 'inf', 'flz', 'men', 'fin']) if config else ['ang', 'inf', 'flz', 'men', 'fin']
    positions = {}
    for ak in account_keys:
        for side in ['long', 'short']:
            path = BASE_PATH / ak / f"{side}_positions.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
                for pk, pos in data.items():
                    if not isinstance(pos, dict):
                        continue
                    amt = abs(float(pos.get('positionAmt', 0) or 0))
                    if amt <= 0:
                        continue
                    gain = float(pos.get('gain', 0) or 0)
                    entry_price = float(pos.get('entry_price', 0) or 0)
                    max_gain = float(pos.get('max_gain', 0) or 0)
                    positions[pk] = {"account": ak, "side": "LONG" if side == "long" else "SHORT", "gain": gain, "max_gain": max_gain, "entry_price": entry_price, "positionAmt": amt, "symbol": pos.get('symbol', pk.split(':')[1].replace('_LONG', '').replace('_SHORT', '') if ':' in pk else '')}
            except Exception as e:
                logger.debug(f"Error loading {path}: {e}")
    return positions


def publish_action(position_key: str, action: str, reason: str):
    """Publish a sentiment-driven action to Redis for ez_positions_quick to pick up."""
    try:
        import redis
        r = redis.Redis(host='127.0.0.1', port=6379 if platform.system() != "Darwin" else 6381, db=0, decode_responses=True)
        signal_data = json.dumps({"position_key": position_key, "action": action, "reason": reason, "source": "sentiment_monitor", "timestamp": datetime.now(timezone.utc).isoformat()})
        r.publish('sentiment_monitor_signals', signal_data)
        r.set(f'sentiment_action:{position_key}', signal_data, ex=60)
        logger.info(f"[ACTION] {position_key}: {action} — {reason}")
    except Exception as e:
        logger.warning(f"Redis publish failed: {e}")


# ═══ SENTIMENT MONITOR ENGINE ══════════════════════════════════════════════

class SentimentMonitor:
    def __init__(self):
        self.cfg = SentimentConfig()
        self.sentiment_history = []       # Last N sentiment readings
        self.smoothed_sentiment = 0.0     # EMA of sentiment
        self.prev_sentiment = 0.0
        self.actions_taken = {}           # {pk: timestamp} cooldown tracker
        self.ACTION_COOLDOWN = 120.0      # 2 min between actions per position
        self.held_positions = {}          # {pk: {"entry_sentiment": float, "hold_extended": bool}}
        self.stats = {"scans": 0, "actions": 0, "holds_extended": 0, "early_exits": 0, "reversal_exits": 0}

    def classify_sentiment(self, score: float) -> str:
        if score >= self.cfg.EXTREME_BULLISH_THRESHOLD: return "EXTREME_BULLISH"
        if score >= self.cfg.STRONG_BULLISH_THRESHOLD: return "STRONG_BULLISH"
        if score >= self.cfg.BULLISH_THRESHOLD: return "BULLISH"
        if score <= self.cfg.EXTREME_BEARISH_THRESHOLD: return "EXTREME_BEARISH"
        if score <= self.cfg.STRONG_BEARISH_THRESHOLD: return "STRONG_BEARISH"
        if score <= self.cfg.BEARISH_THRESHOLD: return "BEARISH"
        return "NEUTRAL"

    def detect_reversal(self) -> Optional[str]:
        """Detect sentiment reversal from recent history."""
        if len(self.sentiment_history) < self.cfg.REVERSAL_LOOKBACK:
            return None
        recent = self.sentiment_history[-self.cfg.REVERSAL_LOOKBACK:]
        oldest = recent[0]
        newest = recent[-1]
        swing = newest - oldest
        if abs(swing) >= self.cfg.REVERSAL_MIN_MAGNITUDE:
            if oldest > 0 and newest < 0:
                return "BULL_TO_BEAR"
            elif oldest < 0 and newest > 0:
                return "BEAR_TO_BULL"
        return None

    def compute_velocity(self) -> float:
        """Compute rate of sentiment change."""
        if len(self.sentiment_history) < 2:
            return 0.0
        return self.sentiment_history[-1] - self.sentiment_history[-2]

    def should_act(self, position_key: str) -> bool:
        """Check cooldown."""
        last = self.actions_taken.get(position_key, 0)
        return (time.time() - last) > self.ACTION_COOLDOWN

    def record_action(self, position_key: str):
        self.actions_taken[position_key] = time.time()
        self.stats["actions"] += 1

    def scan(self):
        """Main scan cycle — evaluate all positions against current sentiment."""
        self.stats["scans"] += 1
        # Load sentiment
        sentiment = load_sentiment()
        global_score = sentiment["global"]
        global_ema = sentiment["global_ema"]
        # Update history
        self.sentiment_history.append(global_score)
        if len(self.sentiment_history) > 60:
            self.sentiment_history = self.sentiment_history[-60:]
        # Smooth
        self.smoothed_sentiment = self.smoothed_sentiment * (1 - SENTIMENT_SMOOTHING_ALPHA) + global_score * SENTIMENT_SMOOTHING_ALPHA
        velocity = self.compute_velocity()
        reversal = self.detect_reversal()
        regime = self.classify_sentiment(self.smoothed_sentiment)
        if self.stats["scans"] % 60 == 1:
            logger.info(f"[REGIME] Sentiment={self.smoothed_sentiment:.1f} (raw={global_score:.1f} ema={global_ema:.1f}) Velocity={velocity:+.1f} Regime={regime} Reversal={reversal or 'NONE'}")
        # Load positions
        positions = load_positions()
        if not positions:
            return
        for pk, pos in positions.items():
            if not self.should_act(pk):
                continue
            is_long = pos["side"] == "LONG"
            gain = pos["gain"]
            max_gain = pos["max_gain"]
            # Track entry sentiment for new positions
            if pk not in self.held_positions:
                self.held_positions[pk] = {"entry_sentiment": self.smoothed_sentiment, "hold_extended": False, "entry_regime": regime}
            held_info = self.held_positions[pk]
            entry_sentiment = held_info["entry_sentiment"]
            sentiment_shift = self.smoothed_sentiment - entry_sentiment
            # ══ RULE 1: REVERSAL EXIT ══
            # If sentiment flipped sign since entry AND position has any gain, exit immediately
            if reversal:
                if is_long and reversal == "BULL_TO_BEAR" and gain > 0:
                    publish_action(pk, "QUICK_CLOSE", f"SENTIMENT_REVERSAL_{reversal}_gain{gain:.2f}%_sent{self.smoothed_sentiment:.0f}")
                    self.record_action(pk)
                    self.stats["reversal_exits"] += 1
                    logger.warning(f"[REVERSAL_EXIT] {pk}: {reversal} while LONG at {gain:.2f}% gain. Sentiment {entry_sentiment:.0f}→{self.smoothed_sentiment:.0f}")
                    continue
                elif not is_long and reversal == "BEAR_TO_BULL" and gain > 0:
                    publish_action(pk, "QUICK_CLOSE", f"SENTIMENT_REVERSAL_{reversal}_gain{gain:.2f}%_sent{self.smoothed_sentiment:.0f}")
                    self.record_action(pk)
                    self.stats["reversal_exits"] += 1
                    logger.warning(f"[REVERSAL_EXIT] {pk}: {reversal} while SHORT at {gain:.2f}% gain. Sentiment {entry_sentiment:.0f}→{self.smoothed_sentiment:.0f}")
                    continue
            # ══ RULE 2: BEARISH MARKET → EXIT LONGS EARLY ══
            if is_long and self.smoothed_sentiment < self.cfg.BEARISH_THRESHOLD:
                exit_threshold = self.cfg.BEARISH_LONG_EXIT_GAIN
                if self.smoothed_sentiment < self.cfg.STRONG_BEARISH_THRESHOLD:
                    exit_threshold = self.cfg.STRONG_BEARISH_LONG_EXIT_GAIN
                if gain >= exit_threshold and gain > 0:
                    publish_action(pk, "QUICK_CLOSE", f"BEARISH_EARLY_EXIT_sent{self.smoothed_sentiment:.0f}_gain{gain:.2f}%_thresh{exit_threshold}%")
                    self.record_action(pk)
                    self.stats["early_exits"] += 1
                    logger.warning(f"[EARLY_EXIT] {pk}: LONG in BEARISH market (sent={self.smoothed_sentiment:.0f}). Gain {gain:.2f}% >= {exit_threshold}%. Taking profit early.")
                    continue
            # ══ RULE 3: BULLISH MARKET → EXIT SHORTS EARLY ══
            if not is_long and self.smoothed_sentiment > self.cfg.BULLISH_THRESHOLD:
                exit_threshold = self.cfg.BEARISH_LONG_EXIT_GAIN  # Same threshold, symmetric
                if self.smoothed_sentiment > self.cfg.STRONG_BULLISH_THRESHOLD:
                    exit_threshold = self.cfg.STRONG_BEARISH_LONG_EXIT_GAIN
                if gain >= exit_threshold and gain > 0:
                    publish_action(pk, "QUICK_CLOSE", f"BULLISH_SHORT_EXIT_sent{self.smoothed_sentiment:.0f}_gain{gain:.2f}%")
                    self.record_action(pk)
                    self.stats["early_exits"] += 1
                    logger.warning(f"[EARLY_EXIT] {pk}: SHORT in BULLISH market (sent={self.smoothed_sentiment:.0f}). Gain {gain:.2f}%. Taking profit early.")
                    continue
            # ══ RULE 4: FAST VELOCITY DROP → DEFENSIVE EXIT ══
            if velocity < self.cfg.VELOCITY_FAST_DROP and is_long and gain > 0:
                publish_action(pk, "QUICK_CLOSE", f"VELOCITY_DROP_sent{self.smoothed_sentiment:.0f}_v{velocity:+.1f}_gain{gain:.2f}%")
                self.record_action(pk)
                self.stats["early_exits"] += 1
                logger.warning(f"[VELOCITY_EXIT] {pk}: Sentiment dropping fast (v={velocity:+.1f}). LONG at {gain:.2f}%. Defensive exit.")
                continue
            if velocity > self.cfg.VELOCITY_FAST_RISE and not is_long and gain > 0:
                publish_action(pk, "QUICK_CLOSE", f"VELOCITY_RISE_sent{self.smoothed_sentiment:.0f}_v{velocity:+.1f}_gain{gain:.2f}%")
                self.record_action(pk)
                self.stats["early_exits"] += 1
                logger.warning(f"[VELOCITY_EXIT] {pk}: Sentiment rising fast (v={velocity:+.1f}). SHORT at {gain:.2f}%. Defensive exit.")
                continue
            # ══ RULE 5: FAVORABLE SENTIMENT → EXTEND HOLD (log only, don't interfere) ══
            if is_long and self.smoothed_sentiment > self.cfg.BULLISH_THRESHOLD and not held_info["hold_extended"]:
                held_info["hold_extended"] = True
                self.stats["holds_extended"] += 1
                logger.info(f"[HOLD_EXTENDED] {pk}: LONG in BULLISH market (sent={self.smoothed_sentiment:.0f}). Extending hold time.")
            elif not is_long and self.smoothed_sentiment < self.cfg.BEARISH_THRESHOLD and not held_info["hold_extended"]:
                held_info["hold_extended"] = True
                self.stats["holds_extended"] += 1
                logger.info(f"[HOLD_EXTENDED] {pk}: SHORT in BEARISH market (sent={self.smoothed_sentiment:.0f}). Extending hold time.")
        # Clean up closed positions
        active_pks = set(positions.keys())
        for pk in list(self.held_positions.keys()):
            if pk not in active_pks:
                del self.held_positions[pk]
        self.prev_sentiment = global_score

    def print_stats(self):
        logger.info(f"[STATS] Scans={self.stats['scans']} Actions={self.stats['actions']} HoldsExtended={self.stats['holds_extended']} EarlyExits={self.stats['early_exits']} ReversalExits={self.stats['reversal_exits']}")


# ═══ MAIN ════════════════════════════════════════════════════════════════════

async def main():
    logger.info("Sentiment Monitor starting...")
    logger.info(f"  Base path: {BASE_PATH}")
    logger.info(f"  Scan interval: {SCAN_INTERVAL}s")
    logger.info(f"  Bullish threshold: {SentimentConfig.BULLISH_THRESHOLD}")
    logger.info(f"  Bearish threshold: {SentimentConfig.BEARISH_THRESHOLD}")
    logger.info(f"  Reversal magnitude: {SentimentConfig.REVERSAL_MIN_MAGNITUDE}")
    monitor = SentimentMonitor()
    scan_count = 0
    while not shutdown_flag:
        try:
            monitor.scan()
            scan_count += 1
            if scan_count % 120 == 0:  # Every 10 min
                monitor.print_stats()
        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)
    monitor.print_stats()
    logger.info("Sentiment Monitor stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
