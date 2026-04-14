#!/usr/bin/env python3
"""Recovery agent for positions destroyed by HARD_STOP_LOSS_MAX_PAIN on 2026-03-24.
Monitors each symbol with WT intelligence and re-enters at optimal moment."""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config
config = Config()
BASE_PATH = config.BASE_PATH
logger = logging.getLogger("tradier_recovery")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)
LOG_DIR = Path(os.path.expanduser("~/logs"))
LOG_DIR.mkdir(exist_ok=True)
fh = logging.FileHandler(LOG_DIR / "tradier_recovery_agent.log", mode='a')
fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(fh)

# Positions destroyed on 2026-03-24 by HARD_STOP_LOSS_MAX_PAIN
RECOVERY_TARGETS = [
    {"symbol": "ASTS", "side": "LONG", "original_entry": 91.91, "original_qty": 20, "sold_at": 85.32, "remaining_qty": 0, "recover_qty": 20, "status": "WAITING"},
    {"symbol": "SNDK", "side": "LONG", "original_entry": 744.60, "original_qty": 7, "sold_at": 697.56, "remaining_qty": 4, "recover_qty": 3, "status": "WAITING"},
    {"symbol": "STZ", "side": "SHORT", "original_entry": 151.28, "original_qty": 11, "sold_at": 155.26, "remaining_qty": 0, "recover_qty": 11, "status": "WAITING"},
    {"symbol": "USO", "side": "LONG", "original_entry": 114.89, "original_qty": 60, "sold_at": 114.91, "remaining_qty": 40, "recover_qty": 20, "status": "WAITING"},
    {"symbol": "MU", "side": "LONG", "original_entry": 392.42, "original_qty": 4, "sold_at": 392.42, "remaining_qty": 0, "recover_qty": 4, "status": "WAITING"},
    {"symbol": "FIVN", "side": "SHORT", "original_entry": 15.41, "original_qty": 280, "sold_at": 15.41, "remaining_qty": 0, "recover_qty": 280, "status": "WAITING"},
    {"symbol": "GLD", "side": "LONG", "original_entry": 406.92, "original_qty": 6, "sold_at": 406.92, "remaining_qty": 0, "recover_qty": 6, "status": "WAITING"},
]
STATE_FILE = BASE_PATH / "data" / "tradier_recovery_state.json"

def load_state() -> List[Dict]:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return RECOVERY_TARGETS.copy()

def save_state(targets: List[Dict]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(targets, f, indent=2)

def get_indicators(symbol: str) -> Optional[Dict]:
    """Read latest indicators from Redis tradier_indicators_latest (dict keyed by symbol)."""
    try:
        import redis
        import platform
        port = 6379 if platform.system() == "Linux" else 6381
        r = redis.Redis(host='localhost', port=port, db=0, socket_timeout=10)
        raw = r.get('tradier_indicators_latest')
        if raw:
            all_ind = json.loads(raw)
            if isinstance(all_ind, dict) and symbol in all_ind:
                return all_ind[symbol]
    except Exception:
        pass
    return None

def evaluate_recovery_entry(target: Dict, indicators: Dict) -> tuple:
    """Use WT intelligence to decide if NOW is the right time to re-enter."""
    symbol = target["symbol"]
    side = target["side"]
    is_long = side == "LONG"
    # WT Intelligence fields
    wt1_1h = float(indicators.get('wt1_1h', 0) or 0)
    wt2_1h = float(indicators.get('wt2_1h', 0) or 0)
    wt_cross = indicators.get('wt_cross_1h')
    wt_momentum = indicators.get('wt_momentum_state_1h', '')
    wt_divergence = indicators.get('wt_divergence_1h')
    wt_percentile = float(indicators.get('wt_percentile_1h', 50) or 50)
    wt_trough_struct = indicators.get('wt_trough_structure_1h', '')
    wt_peak_struct = indicators.get('wt_peak_structure_1h', '')
    # Stochastic
    k_5m = float(indicators.get('stoch_k_5m', 50) or 50)
    k_15m = float(indicators.get('stoch_k_15m', 50) or 50)
    k_1h = float(indicators.get('stoch_k_1h', 50) or 50)
    # HA
    ha_5m = indicators.get('ha_5m', 'neutral')
    ha_15m = indicators.get('ha_15m', 'neutral')
    score = 0
    reasons = []
    if is_long:
        # LONG recovery — wait for oversold + WT bullish setup
        if wt_percentile < 20: score += 3; reasons.append(f"WT_OS({wt_percentile:.0f}pct)")
        if wt_cross == 'BULL': score += 4; reasons.append("WT_BULL_CROSS")
        if wt_momentum == 'EXHAUST_DOWN': score += 3; reasons.append("WT_EXHAUST_DOWN")
        if wt_momentum == 'IMPULSE_UP': score += 2; reasons.append("WT_IMPULSE_UP")
        if wt_divergence in ('BULL', 'HIDDEN_BULL'): score += 4; reasons.append(f"WT_DIV_{wt_divergence}")
        if wt_trough_struct == 'HL': score += 2; reasons.append("WT_TROUGH_HL")
        if wt1_1h > wt2_1h: score += 1; reasons.append("WT1>WT2")
        if k_5m < 30: score += 1; reasons.append(f"K5m_OS({k_5m:.0f})")
        if k_15m < 35 and k_15m > float(indicators.get('stoch_d_15m', 50) or 50): score += 2; reasons.append("K15m_CROSS_UP")
        if ha_5m == 'green' and ha_15m == 'green': score += 1; reasons.append("HA_GREEN")
        # Block if overbought (don't chase)
        if wt_percentile > 75: score -= 5; reasons.append(f"WT_OB_BLOCK({wt_percentile:.0f})")
        if wt_momentum == 'IMPULSE_DOWN': score -= 4; reasons.append("WT_IMPULSE_DOWN_BLOCK")
    else:
        # SHORT recovery — wait for overbought + WT bearish setup
        if wt_percentile > 80: score += 3; reasons.append(f"WT_OB({wt_percentile:.0f}pct)")
        if wt_cross == 'BEAR': score += 4; reasons.append("WT_BEAR_CROSS")
        if wt_momentum == 'EXHAUST_UP': score += 3; reasons.append("WT_EXHAUST_UP")
        if wt_momentum == 'IMPULSE_DOWN': score += 2; reasons.append("WT_IMPULSE_DOWN")
        if wt_divergence in ('BEAR', 'HIDDEN_BEAR'): score += 4; reasons.append(f"WT_DIV_{wt_divergence}")
        if wt_peak_struct == 'LH': score += 2; reasons.append("WT_PEAK_LH")
        if wt1_1h < wt2_1h: score += 1; reasons.append("WT1<WT2")
        if k_5m > 70: score += 1; reasons.append(f"K5m_OB({k_5m:.0f})")
        if k_15m > 65 and k_15m < float(indicators.get('stoch_d_15m', 50) or 50): score += 2; reasons.append("K15m_CROSS_DN")
        if ha_5m == 'red' and ha_15m == 'red': score += 1; reasons.append("HA_RED")
        if wt_percentile < 25: score -= 5; reasons.append(f"WT_OS_BLOCK({wt_percentile:.0f})")
        if wt_momentum == 'IMPULSE_UP': score -= 4; reasons.append("WT_IMPULSE_UP_BLOCK")
    threshold = 8  # Need strong confirmation before recovery entry
    return score >= threshold, score, reasons

async def execute_recovery_order(target: Dict) -> bool:
    """Place the actual order through Tradier API."""
    try:
        from tradier_api import TradierAPI
        from config_tradier import TradierConfig
        api = TradierAPI(config=TradierConfig(), account_key="trb")
        symbol = target["symbol"]
        side = target["side"]
        qty = target["recover_qty"]
        if side == "LONG":
            order_side = "buy"
        else:
            order_side = "sell_short"
        logger.info(f"EXECUTING: {order_side} {qty} {symbol}")
        result = await api.place_order("trb", symbol, order_side, qty, order_type="market")
        if result and "error" not in str(result).lower():
            logger.info(f"✅ ORDER PLACED: {symbol} {order_side} {qty} — result={result}")
            target["status"] = "RECOVERED"
            target["recovery_time"] = datetime.now(timezone.utc).isoformat()
            target["order_result"] = str(result)[:200]
            return True
        else:
            logger.error(f"❌ ORDER FAILED: {symbol} — {result}")
            return False
    except Exception as e:
        logger.error(f"❌ ORDER EXCEPTION: {target['symbol']} — {e}")
        return False

def print_status(targets: List[Dict]):
    print(f"\n{'='*80}")
    print(f" TRADIER RECOVERY AGENT — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*80}")
    for t in targets:
        status_icon = "✅" if t["status"] == "RECOVERED" else "⏳" if t["status"] == "WAITING" else "🔄"
        print(f"  {status_icon} {t['symbol']:6s} {t['side']:5s}  orig_entry=${t['original_entry']:<8.2f}  sold@${t['sold_at']:<8.2f}  recover_qty={t['recover_qty']:<4d}  status={t['status']}")
    waiting = sum(1 for t in targets if t["status"] == "WAITING")
    recovered = sum(1 for t in targets if t["status"] == "RECOVERED")
    print(f"\n  Waiting: {waiting} | Recovered: {recovered} | Total: {len(targets)}")
    print(f"{'='*80}\n")

async def recovery_loop():
    targets = load_state()
    logger.info(f"Recovery agent started — {len(targets)} positions to recover")
    print_status(targets)
    cycle = 0
    while True:
        cycle += 1
        waiting = [t for t in targets if t["status"] == "WAITING"]
        if not waiting:
            logger.info("All positions recovered!")
            print_status(targets)
            break
        for t in waiting:
            indicators = get_indicators(t["symbol"])
            if not indicators:
                if cycle % 10 == 0:
                    logger.warning(f"No indicators for {t['symbol']} — waiting for data")
                continue
            should_enter, score, reasons = evaluate_recovery_entry(t, indicators)
            current_price = float(indicators.get('close_5m', indicators.get('current_price', 0)) or 0)
            if should_enter:
                logger.info(f"🎯 RECOVERY SIGNAL: {t['symbol']} {t['side']} score={score} price=${current_price:.2f} reasons={', '.join(reasons)}")
                logger.info(f"   → EXECUTING: {t['recover_qty']} shares of {t['symbol']} {t['side']} @ ~${current_price:.2f}")
                t["signal_price"] = current_price
                t["signal_time"] = datetime.now(timezone.utc).isoformat()
                t["signal_score"] = score
                t["signal_reasons"] = reasons
                success = await execute_recovery_order(t)
                if success:
                    logger.info(f"✅ RECOVERED: {t['symbol']} {t['side']} {t['recover_qty']} shares")
                else:
                    t["status"] = "SIGNAL_READY"
                    logger.warning(f"⚠️ ORDER FAILED for {t['symbol']} — will retry next cycle")
                save_state(targets)
                print_status(targets)
            elif cycle % 60 == 0:
                logger.info(f"  {t['symbol']:6s} {t['side']:5s}: score={score:<3d} (need 8) price=${current_price:.2f} | {', '.join(reasons[:3])}")
        await asyncio.sleep(30)  # Check every 30 seconds
    save_state(targets)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Tradier position recovery agent")
    parser.add_argument("--status", action="store_true", help="Show current recovery status")
    parser.add_argument("--reset", action="store_true", help="Reset all to WAITING")
    args = parser.parse_args()
    if args.status:
        targets = load_state()
        print_status(targets)
        return
    if args.reset:
        save_state(RECOVERY_TARGETS.copy())
        print("Reset all targets to WAITING")
        return
    asyncio.run(recovery_loop())

if __name__ == "__main__":
    main()
