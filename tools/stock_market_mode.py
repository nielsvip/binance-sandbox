#!/usr/bin/env python3
"""Stock market-mode detector: the crypto ez_rankings market index applied to the stock universe.

Same formula as ez_rankings.calculate_market_index (per-symbol movement components: ATR%, relative volume,
RSI and stochastic momentum; volume-weighted average; scaled by the 95th percentile; smoothed over the
last three readings) and the same thresholds (LIGHT below 12, EXTREME above 40, NORMAL between).
Input: the last closed 15m row of every stock NPZ in backtest_v8/indicators (stock 3m fields are not stored, so those
components are skipped exactly as the crypto code skips a missing 3m value).
Output: data/market_mode_stocks.json, written atomically. tools/vector_direct_trader.py reads it every pass.
"""
import glob
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LIGHT_THRESHOLD = 12.0
EXTREME_THRESHOLD = 40.0
HISTORY_WINDOW = 3
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


KEYS = ("close_15m", "atr_15m", "atr_3m", "relative_volume_15m", "relative_volume_3m", "rsi_15m", "rsi_3m",
        "stoch_k_15m", "stoch_k_3m", "timestamps")


def last_row(path):
    import numpy as np
    try:
        with np.load(path, allow_pickle=False) as z:
            row = {k: float(z[k][-1]) for k in KEYS if k in z.files and len(z[k])}
        return row if "close_15m" in row else None
    except Exception:
        return None


def symbol_score(d):
    price = d.get("close_15m")
    components = []
    if price and price > 0:
        atr15 = float(d.get("atr_15m") or 0)
        atr3 = float(d.get("atr_3m") or 0)
        score = 0.0
        if atr15:
            score += min((atr15 / price) * 100 * 10, 50)
            components.append(1)
        if atr3:
            score += min((atr3 / price) * 100 * 10, 30)
            components.append(1)
    else:
        score = 0.0
    rv15 = float(d.get("relative_volume_15m") or 1.0)
    rv3 = float(d.get("relative_volume_3m") or 1.0)
    if rv15 > 1.0:
        score += (rv15 - 1.0) * 20
        components.append(1)
    if rv3 > 1.0:
        score += (rv3 - 1.0) * 10
        components.append(1)
    rsi15 = float(d.get("rsi_15m") or 50)
    rsi3 = float(d.get("rsi_3m") or 50)
    score += abs(rsi15 - 50) / 50.0 * 10 + abs(rsi3 - 50) / 50.0 * 5
    k15 = float(d.get("stoch_k_15m") or 50)
    k3 = float(d.get("stoch_k_3m") or 50)
    score += abs(k15 - 50) / 50.0 * 5 + abs(k3 - 50) / 50.0 * 3
    components += [1, 1, 1, 1]
    if not components:
        return None, 0.0
    return min(score / len(components), 100.0), max(rv15, rv3, 1.0)


def market_index(rows):
    comp, weights = [], []
    for d in rows:
        s, w = symbol_score(d)
        if s is None:
            continue
        comp.append(s)
        weights.append(w)
    if not comp:
        return None, 0
    total = sum(weights)
    raw = sum(c * w for c, w in zip(comp, weights)) / total if total > 0 else sum(comp) / len(comp)
    srt = sorted(comp)
    p95 = srt[min(len(srt) - 1, int(math.ceil(0.95 * len(srt))) - 1)]
    scale = 100.0 / max(50.0, p95)
    return max(0.0, min(100.0, raw * scale)), len(comp)


def main(argv):
    root = Path(argv[1]).expanduser() if len(argv) > 1 else Path.home() / "binance"
    tails = Path(argv[2]).expanduser() if len(argv) > 2 else Path.home() / "binance-sandbox" / "backtest_v8" / "indicators"
    out = root / "data" / "market_mode_stocks.json"
    rows = []
    for p in glob.glob(str(tails / "*.npz")):
        sym = Path(p).stem.upper()
        if sym.endswith(CRYPTO_SUFFIXES):
            continue
        d = last_row(p)
        if d:
            rows.append(d)
    raw, n = market_index(rows)
    prev = read_history(out)
    hist = (prev.get("history") or [])[-(HISTORY_WINDOW - 1):] if raw is not None else []
    if raw is not None:
        hist.append(raw)
    smoothed = sum(hist) / len(hist) if len(hist) >= 3 else (raw if raw is not None else 50.0)
    if raw is None:
        mode = "LIGHT_MODE"
    else:
        mode = "EXTREME_MODE" if smoothed > EXTREME_THRESHOLD else "LIGHT_MODE" if smoothed < LIGHT_THRESHOLD else "NORMAL_MODE"
    doc = {"market_index": smoothed, "raw_index": raw, "mode": mode, "symbols": n,
           "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "history": hist,
           "thresholds": {"extreme": EXTREME_THRESHOLD, "light": LIGHT_THRESHOLD},
           "mode_changed": mode != prev.get("mode"), "source": "stock_npz_tails"}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1))
    os.replace(tmp, out)
    print(json.dumps({k: doc[k] for k in ("market_index", "raw_index", "mode", "symbols", "mode_changed")}))
    return 0


def read_history(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
