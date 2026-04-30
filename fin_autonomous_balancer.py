#!/usr/bin/env python3
"""fin_autonomous_balancer.py — standalone L/S ratio balancer for the fin account.

Completely independent of ez_manage.py / ez_positions_quick.py / execute_now.
Ignores STRICT_NO_LOSS, DC-high gates, WT rules, hedge rules, everything.

Logic:
  1. Every loop (default 0.5s), pull live fin positions from Binance (authoritative).
  2. Read 0market_sentiment_score from Redis (50 = neutral, >50 bullish).
  3. Target long-count share among open positions = sentiment (clamped 10-90%).
  4. If imbalance > deadband, close the single biggest winner on the overweight
     side — but ONLY if its gain is >= MIN_GAIN_TO_CLOSE_PCT. Never closes at a loss.
  5. One close per loop, then reassess next tick.

Hedge/real distinction is intentionally ignored per user spec: every open position
counts equally, biggest gainer gets taken first.

Run:   python3 fin_autonomous_balancer.py                # live
       python3 fin_autonomous_balancer.py --dry-run      # log only
Kill:  touch /Users/niels/Documents/binance/data/fin_balancer/KILL
       or: export FIN_BALANCER_KILL=1
"""
import argparse
import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List

ACCOUNT_KEY = "fin"
LOOP_INTERVAL_SEC = 0.5
MIN_GAIN_TO_CLOSE_PCT = 1.0
RATIO_DEADBAND_PP = 3.0
POSITIONS_REFRESH_SEC = 1.0
BASE_PATH = Path("/Users/niels/Documents/binance")
BALANCER_DIR = BASE_PATH / "data" / "fin_balancer"
BALANCER_DIR.mkdir(parents=True, exist_ok=True)
KILL_FILE = BALANCER_DIR / "KILL"
LOG_FILE = BALANCER_DIR / "balancer.log"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S", handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
logger = logging.getLogger("fin_balancer")

sys.path.insert(0, str(BASE_PATH))
from utils import load_environment_from_gpg
import redis
from binance.client import Client

load_environment_from_gpg(logger)

_api_key = os.environ.get("fin_API_KEY") or os.environ.get("FIN_API_KEY")
_api_secret = os.environ.get("fin_API_SECRET") or os.environ.get("FIN_API_SECRET")
if not _api_key or not _api_secret:
    logger.critical("fin API credentials not found (fin_API_KEY / fin_API_SECRET). Decrypt .env.gpg failed?")
    sys.exit(1)

client = Client(api_key=_api_key, api_secret=_api_secret)
redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

_filter_cache: Dict[str, Dict] = {}
_exchange_info_loaded = False

def _load_exchange_info():
    global _exchange_info_loaded
    if _exchange_info_loaded:
        return
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            lot = next((f for f in s["filters"] if f["filterType"] == "LOT_SIZE"), None)
            if not lot:
                continue
            _filter_cache[s["symbol"]] = {"quantityPrecision": int(s.get("quantityPrecision", 3)), "stepSize": float(lot["stepSize"]), "minQty": float(lot["minQty"])}
        _exchange_info_loaded = True
        logger.info(f"exchange_info loaded for {len(_filter_cache)} symbols")
    except Exception as e:
        logger.error(f"_load_exchange_info failed: {e}")

def format_qty(symbol: str, qty: float) -> str:
    _load_exchange_info()
    f = _filter_cache.get(symbol, {"quantityPrecision": 3, "stepSize": 0.001, "minQty": 0.001})
    precision = f["quantityPrecision"]
    step = f["stepSize"]
    if step > 0:
        qty = (int(qty / step)) * step
    return f"{qty:.{precision}f}"

def load_positions() -> List[Dict]:
    """Pull live fin positions directly from Binance — authoritative, not disk."""
    out: List[Dict] = []
    try:
        data = client.futures_position_information()
    except Exception as e:
        logger.error(f"futures_position_information failed: {e}")
        return out
    for p in data:
        amt = float(p.get("positionAmt", 0) or 0)
        if abs(amt) <= 0:
            continue
        entry = float(p.get("entryPrice", 0) or 0)
        mark = float(p.get("markPrice", 0) or 0)
        if entry <= 0 or mark <= 0:
            continue
        position_side = p.get("positionSide", "BOTH")
        if position_side == "BOTH":
            position_side = "LONG" if amt > 0 else "SHORT"
        if position_side == "LONG":
            gain = ((mark - entry) / entry) * 100.0
        else:
            gain = ((entry - mark) / entry) * 100.0
        out.append({"symbol": p["symbol"], "position_side": position_side, "positionAmt": amt, "entry_price": entry, "mark_price": mark, "gain": gain})
    return out

def get_sentiment() -> float:
    """Return sentiment 0-100. Primary: 0sentiment_rank (percentile).
    Fallback: 0market_sentiment_score mapped from [-1,+1] → [0,100]. Default 50."""
    try:
        raw = redis_client.get("latest_market_data")
        if not raw:
            return 50.0
        data = json.loads(raw)
        for key_candidate in ("BTCUSDC", "BTCUSDC", "BTC"):
            section = data.get(key_candidate)
            if not isinstance(section, dict):
                continue
            rank = section.get("0sentiment_rank")
            if rank is not None:
                try:
                    return max(0.0, min(100.0, float(rank)))
                except (TypeError, ValueError):
                    pass
            score = section.get("0market_sentiment_score")
            if score is not None:
                try:
                    s = float(score)
                    if -1.5 <= s <= 1.5:
                        return max(0.0, min(100.0, 50.0 + s * 50.0))
                    return max(0.0, min(100.0, s))
                except (TypeError, ValueError):
                    pass
    except Exception as e:
        logger.warning(f"get_sentiment failed: {e}")
    return 50.0

def close_position(pos: Dict, reason: str, dry_run: bool) -> bool:
    symbol = pos["symbol"]
    position_side = pos["position_side"]
    qty = abs(pos["positionAmt"])
    side = "SELL" if position_side == "LONG" else "BUY"
    qty_str = format_qty(symbol, qty)
    if float(qty_str) <= 0:
        logger.warning(f"close_position({symbol} {position_side}): qty rounds to 0, skip")
        return False
    client_oid = f"FINBAL_{int(time.time() * 1000) % 1_000_000_000}"
    if dry_run:
        logger.info(f"[DRY] would CLOSE {symbol} {position_side} qty={qty_str} gain={pos['gain']:.2f}% reason={reason}")
        return True
    try:
        order = client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty_str, positionSide=position_side, newClientOrderId=client_oid)
        logger.info(f"[CLOSE] {symbol} {position_side} qty={qty_str} gain={pos['gain']:.2f}% reason={reason} orderId={order.get('orderId')} cid={client_oid}")
        return True
    except Exception as e:
        logger.error(f"[CLOSE_FAIL] {symbol} {position_side} qty={qty_str} error={e}")
        return False

def should_stop() -> bool:
    if KILL_FILE.exists():
        logger.warning(f"KILL file present at {KILL_FILE}, stopping")
        return True
    if os.environ.get("FIN_BALANCER_KILL") == "1":
        logger.warning("FIN_BALANCER_KILL=1, stopping")
        return True
    return False

_last_positions_refresh = 0.0
_cached_positions: List[Dict] = []

def _get_positions_cached() -> List[Dict]:
    global _last_positions_refresh, _cached_positions
    now = time.time()
    if now - _last_positions_refresh >= POSITIONS_REFRESH_SEC:
        _cached_positions = load_positions()
        _last_positions_refresh = now
    return _cached_positions

def rebalance_once(dry_run: bool) -> bool:
    positions = _get_positions_cached()
    if not positions:
        return False
    sentiment = get_sentiment()
    target_long_pct = max(10.0, min(90.0, sentiment))
    longs = [p for p in positions if p["position_side"] == "LONG"]
    shorts = [p for p in positions if p["position_side"] == "SHORT"]
    total = len(positions)
    current_long_pct = (len(longs) / total) * 100.0
    delta = current_long_pct - target_long_pct
    logger.info(f"state pos={total} L={len(longs)} S={len(shorts)} sent={sentiment:.1f} tgtL%={target_long_pct:.1f} curL%={current_long_pct:.1f} delta={delta:+.1f}pp")
    if abs(delta) <= RATIO_DEADBAND_PP:
        return False
    overweight_side = "LONG" if delta > 0 else "SHORT"
    pool = longs if delta > 0 else shorts
    winners = [p for p in pool if p["gain"] >= MIN_GAIN_TO_CLOSE_PCT]
    if not winners:
        logger.info(f"imbalance {delta:+.1f}pp but no {overweight_side} winner >= {MIN_GAIN_TO_CLOSE_PCT}% — skip (ONLY GAINS)")
        return False
    winners.sort(key=lambda x: x["gain"], reverse=True)
    target = winners[0]
    reason = f"RATIO_BALANCE sent={sentiment:.1f} curL%={current_long_pct:.1f} tgtL%={target_long_pct:.1f} close_biggest_{overweight_side}_winner"
    closed = close_position(target, reason, dry_run)
    if closed:
        global _last_positions_refresh
        _last_positions_refresh = 0.0
    return closed

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Log actions, do not place orders")
    parser.add_argument("--interval", type=float, default=LOOP_INTERVAL_SEC)
    global MIN_GAIN_TO_CLOSE_PCT, RATIO_DEADBAND_PP
    parser.add_argument("--min-gain", type=float, default=MIN_GAIN_TO_CLOSE_PCT)
    parser.add_argument("--deadband", type=float, default=RATIO_DEADBAND_PP)
    args = parser.parse_args()
    MIN_GAIN_TO_CLOSE_PCT = args.min_gain
    RATIO_DEADBAND_PP = args.deadband
    logger.info("=" * 70)
    logger.info(f"fin_autonomous_balancer starting  dry_run={args.dry_run} interval={args.interval}s")
    logger.info(f"MIN_GAIN_TO_CLOSE={MIN_GAIN_TO_CLOSE_PCT}%  DEADBAND={RATIO_DEADBAND_PP}pp  account={ACCOUNT_KEY}")
    logger.info("Bypasses ALL gates. Closes at gain only. L/S count ratio follows sentiment.")
    logger.info(f"KILL: touch {KILL_FILE}  OR  export FIN_BALANCER_KILL=1")
    logger.info("=" * 70)
    def handle_signal(sig, _):
        logger.info(f"signal {sig} received, exiting")
        sys.exit(0)
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    _load_exchange_info()
    while True:
        if should_stop():
            sys.exit(0)
        try:
            rebalance_once(dry_run=args.dry_run)
        except Exception as e:
            logger.error(f"rebalance_once error: {e}\n{traceback.format_exc()}")
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
