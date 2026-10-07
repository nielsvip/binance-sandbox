#!/usr/bin/env python3
"""ang_reentry_monitor.py — Patient re-entry monitor for zeroed ang positions.

Watches indicators continuously. Only flags re-entry when ALL conditions align:
- LONG: K_15m < 20 AND K_1h < 30 AND HA_15m turning green AND price <= original entry
- SHORT: K_15m > 80 AND K_1h > 70 AND HA_15m turning red AND price >= original entry

Logs opportunities but does NOT auto-execute. Alerts for manual review.
Runs as a service, checks every 60s, persists state across restarts.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("reentry")

INDICATOR_FILE = Path("./data/latest_market_data.json")
DATA_DIR = Path("./data/poly/ang_reentry")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"
ALERTS_FILE = DATA_DIR / "alerts.jsonl"

LONGS = {
    "1000FLOKIUSDT": 0.0296, "1000PEPEUSDC": 0.0039, "1000SHIBUSDC": 0.0062,
    "AIUSDT": 0.024, "ARKMUSDT": 0.1137, "AWEUSDT": 0.0538, "BANDUSDT": 0.2399,
    "BEAMXUSDT": 0.0021, "BSVUSDT": 15.2567, "BTCDOMUSDT": 5003.59,
    "C98USDT": 0.03, "CAKEUSDT": 1.4998, "CHZUSDT": 0.0395, "COMPUSDT": 19.5956,
    "ETHFIUSDC": 0.5803, "HYPEUSDT": 38.98, "MORPHOUSDT": 1.8699,
    "PAXGUSDT": 4982.79, "QNTUSDT": 68.058, "RIVERUSDT": 22.441,
    "SIRENUSDT": 0.4648, "TAOUSDT": 283.62, "TRXUSDT": 0.2967,
    "XAIUSDT": 0.0109, "XAUUSDT": 4990.19, "XRPUSDC": 1.5286, "ZILUSDT": 0.0043,
}
SHORTS = {
    "1000FLOKIUSDT": 0.032, "1000SHIBUSDC": 0.0061, "AAVEUSDC": 121.6345,
    "ACHUSDT": 0.0073, "ADAUSDC": 0.2867, "AGLDUSDT": 0.251, "ALTUSDT": 0.0079,
    "APEUSDT": 0.1028, "ARBUSDC": 0.109, "ARKMUSDT": 0.1221, "ARUSDT": 1.928,
    "BATUSDT": 0.1042, "BSVUSDT": 15.255, "CHRUSDT": 0.0165, "DASHUSDT": 34.9427,
    "DYDXUSDT": 0.0935, "EIGENUSDT": 0.2262, "FARTCOINUSDT": 0.1834,
    "FILUSDC": 0.955, "IMXUSDT": 0.1734, "INJUSDT": 3.247, "KSMUSDT": 4.408,
    "TRUMPUSDC": 3.883, "UMAUSDT": 0.4636, "WIFUSDC": 0.19, "WLDUSDC": 0.3929,
}


def load_indicators():
    try:
        with open(INDICATOR_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def get_sym(name, indicators):
    for s in [name, name.replace("USDT", "USDC"), name.replace("USDC", "USDT")]:
        if s in indicators:
            return s
    return None


def check_long_entry(sym, entry_price, indicators):
    ind = indicators.get(sym, {})
    price = ind.get("current_price", 0)
    if not price:
        return None
    k15m = ind.get("stoch_k_15m", 50)
    k1h = ind.get("stoch_k_1h", 50)
    k4h = ind.get("stoch_k_4h", 50)
    k3m = ind.get("stoch_k_3m", ind.get("k_3m_prev", 50))
    k15m_prev = ind.get("k_15m_prev", k15m)
    ha3 = ind.get("ha_3m", "")
    ha15 = ind.get("ha_15m", "")
    ha1h = ind.get("ha_1h", "")
    diff_pct = (price - entry_price) / entry_price * 100
    # STRICT conditions for LONG re-entry:
    # 1. Stochastic oversold on 15m OR 1h (< 25)
    stoch_os = k15m < 25 or k1h < 30
    # 2. Stochastic turning UP (k > k_prev on 15m)
    stoch_turning = k15m > k15m_prev
    # 3. HA confirmation (at least 15m green, or 3m green + 1h green)
    ha_confirm = ha15 == "green" or (ha3 == "green" and ha1h == "green")
    # 4. Price at or below original entry (don't buy more expensive)
    price_ok = price <= entry_price * 1.02  # 2% tolerance
    # Score
    score = 0
    reasons = []
    if k15m < 20:
        score += 3; reasons.append(f"k15m={k15m:.0f}_DEEP_OS")
    elif k15m < 30:
        score += 2; reasons.append(f"k15m={k15m:.0f}_OS")
    if k1h < 25:
        score += 3; reasons.append(f"k1h={k1h:.0f}_DEEP_OS")
    elif k1h < 40:
        score += 1; reasons.append(f"k1h={k1h:.0f}_OS")
    if k4h < 25:
        score += 2; reasons.append(f"k4h={k4h:.0f}_OS")
    if stoch_turning:
        score += 1; reasons.append("k15m_turning_UP")
    if ha_confirm:
        score += 2; reasons.append(f"HA_confirm({ha3}/{ha15}/{ha1h})")
    if diff_pct < -5:
        score += 2; reasons.append(f"cheaper_{diff_pct:.1f}%")
    elif diff_pct > 3:
        score -= 3; reasons.append(f"expensive_{diff_pct:+.1f}%")
    if k15m > 75:
        score -= 3; reasons.append("k15m_OB_SKIP")
    # Threshold: score >= 6 = READY
    ready = score >= 6 and stoch_os and (ha_confirm or k1h < 20)
    return {"sym": sym, "side": "LONG", "entry": entry_price, "price": price, "diff_pct": round(diff_pct, 1), "score": score, "ready": ready, "k15m": round(k15m, 1), "k1h": round(k1h, 1), "k4h": round(k4h, 1), "ha": f"{ha3}/{ha15}/{ha1h}", "reasons": reasons}


def check_short_entry(sym, entry_price, indicators):
    ind = indicators.get(sym, {})
    price = ind.get("current_price", 0)
    if not price:
        return None
    k15m = ind.get("stoch_k_15m", 50)
    k1h = ind.get("stoch_k_1h", 50)
    k4h = ind.get("stoch_k_4h", 50)
    k3m = ind.get("stoch_k_3m", ind.get("k_3m_prev", 50))
    k15m_prev = ind.get("k_15m_prev", k15m)
    ha3 = ind.get("ha_3m", "")
    ha15 = ind.get("ha_15m", "")
    ha1h = ind.get("ha_1h", "")
    diff_pct = (price - entry_price) / entry_price * 100
    # STRICT conditions for SHORT re-entry:
    stoch_ob = k15m > 75 or k1h > 70
    stoch_turning = k15m < k15m_prev
    ha_confirm = ha15 == "red" or (ha3 == "red" and ha1h == "red")
    price_ok = price >= entry_price * 0.98
    score = 0
    reasons = []
    if k15m > 80:
        score += 3; reasons.append(f"k15m={k15m:.0f}_DEEP_OB")
    elif k15m > 70:
        score += 2; reasons.append(f"k15m={k15m:.0f}_OB")
    if k1h > 75:
        score += 3; reasons.append(f"k1h={k1h:.0f}_DEEP_OB")
    elif k1h > 60:
        score += 1; reasons.append(f"k1h={k1h:.0f}_OB")
    if k4h > 75:
        score += 2; reasons.append(f"k4h={k4h:.0f}_OB")
    if stoch_turning:
        score += 1; reasons.append("k15m_turning_DOWN")
    if ha_confirm:
        score += 2; reasons.append(f"HA_confirm({ha3}/{ha15}/{ha1h})")
    if diff_pct > 3:
        score += 2; reasons.append(f"higher_{diff_pct:+.1f}%")
    elif diff_pct < -10:
        score -= 3; reasons.append(f"much_lower_{diff_pct:.1f}%")
    if k15m < 25:
        score -= 3; reasons.append("k15m_OS_SKIP")
    ready = score >= 6 and stoch_ob and (ha_confirm or k1h > 80)
    return {"sym": sym, "side": "SHORT", "entry": entry_price, "price": price, "diff_pct": round(diff_pct, 1), "score": score, "ready": ready, "k15m": round(k15m, 1), "k1h": round(k1h, 1), "k4h": round(k4h, 1), "ha": f"{ha3}/{ha15}/{ha1h}", "reasons": reasons}


def main():
    logger.info(f"Re-entry monitor started | {len(LONGS)} longs + {len(SHORTS)} shorts to watch")
    alerted = set()
    if STATE_FILE.exists():
        try:
            alerted = set(json.loads(STATE_FILE.read_text()).get("alerted", []))
        except Exception:
            pass
    while True:
        indicators = load_indicators()
        if not indicators:
            time.sleep(30)
            continue
        ready_count = 0
        for name, entry in LONGS.items():
            sym = get_sym(name, indicators)
            if not sym:
                continue
            result = check_long_entry(sym, entry, indicators)
            if result and result["ready"]:
                ready_count += 1
                key = f"{sym}_LONG"
                if key not in alerted:
                    alerted.add(key)
                    logger.info(f"READY LONG {sym} score={result['score']} | price=${result['price']:.4f} entry=${entry:.4f} ({result['diff_pct']:+.1f}%) | k15m={result['k15m']:.0f} k1h={result['k1h']:.0f} k4h={result['k4h']:.0f} HA={result['ha']} | {' '.join(result['reasons'])}")
                    with open(ALERTS_FILE, "a") as f:
                        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **result}) + "\n")
        for name, entry in SHORTS.items():
            sym = get_sym(name, indicators)
            if not sym:
                continue
            result = check_short_entry(sym, entry, indicators)
            if result and result["ready"]:
                ready_count += 1
                key = f"{sym}_SHORT"
                if key not in alerted:
                    alerted.add(key)
                    logger.info(f"READY SHORT {sym} score={result['score']} | price=${result['price']:.4f} entry=${entry:.4f} ({result['diff_pct']:+.1f}%) | k15m={result['k15m']:.0f} k1h={result['k1h']:.0f} k4h={result['k4h']:.0f} HA={result['ha']} | {' '.join(result['reasons'])}")
                    with open(ALERTS_FILE, "a") as f:
                        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **result}) + "\n")
        # Reset alerts when conditions change (allow re-alerting)
        for key in list(alerted):
            sym, side = key.rsplit("_", 1)
            ind = indicators.get(sym, {})
            k15m = ind.get("stoch_k_15m", 50)
            if side == "LONG" and k15m > 60:
                alerted.discard(key)
            elif side == "SHORT" and k15m < 40:
                alerted.discard(key)
        # Save state
        with open(STATE_FILE, "w") as f:
            json.dump({"alerted": list(alerted), "last_check": datetime.now(timezone.utc).isoformat(), "ready_count": ready_count}, f)
        if ready_count > 0:
            logger.info(f"Check complete: {ready_count} positions READY for re-entry")
        time.sleep(60)


if __name__ == "__main__":
    main()
