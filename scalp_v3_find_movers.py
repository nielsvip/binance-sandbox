"""
scalp_v3_find_movers.py — Identify symbols that actually "jumped out" in the 25h window.

Per user directive (2026-04-22): only symbols with real ultra-short movement are worth
testing against V3. The rest are noise that dilutes signal.

Scoring per symbol:
  - max_1h_move:   max absolute % move in any 60min rolling window (primary)
  - max_15m_move:  max absolute % move in any 15min rolling window
  - max_5m_move:   max absolute % move in any 5min window
  - explosive_bars:  count of 1m bars with (|return| > 0.5% AND vol > 2× 20-bar avg)
  - total_vol:     liquidity gate (reject micro-cap wiggles)

Rank = (max_1h_move × explosive_bars^0.5 × log(total_vol)).
Output JSON with top-N + per-symbol profile.

Usage:
  python3 scalp_v3_find_movers.py --top 30
  python3 scalp_v3_find_movers.py --top 50 --min-1h-move 3.0
"""
from __future__ import annotations
import argparse
import glob
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

BASE = Path(__file__).resolve().parent
KLINES_DIR = BASE / "klines_cache"
OUT_DIR = BASE / "data" / "scalp_v3_sweep"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _iso_to_epoch(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def load_1m(symbol: str) -> Optional[np.ndarray]:
    path = KLINES_DIR / f"{symbol}_1m.json"
    if not path.exists(): return None
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception:
        return None
    if not raw or len(raw) < 200: return None
    out = np.zeros((len(raw), 6), dtype=np.float64)
    for i, b in enumerate(raw):
        out[i, 0] = _iso_to_epoch(b["timestamp"])
        out[i, 1] = b["open"]; out[i, 2] = b["high"]; out[i, 3] = b["low"]
        out[i, 4] = b["close"]; out[i, 5] = b["volume"]
    return out


def rolling_max_abs_move(closes: np.ndarray, window: int) -> float:
    """Max |pct move| over any window of N bars."""
    if len(closes) < window + 1: return 0.0
    mx = 0.0
    for i in range(window, len(closes)):
        if closes[i - window] <= 0: continue
        move = abs(closes[i] - closes[i - window]) / closes[i - window] * 100.0
        if move > mx: mx = move
    return mx


def score_symbol(bars: np.ndarray) -> Dict:
    closes = bars[:, 4]; vols = bars[:, 5]
    if len(closes) < 60: return {}
    rets_1m = np.diff(closes) / np.maximum(closes[:-1], 1e-12) * 100.0
    vol_20 = np.convolve(vols, np.ones(20) / 20, mode="valid")
    # explosive bars: |ret| > 0.5% AND vol > 2× trailing 20-bar avg
    exp_count = 0
    for i in range(20, len(rets_1m)):
        if abs(rets_1m[i]) > 0.5 and vols[i + 1] > 2.0 * vol_20[i - 20 + 1]:
            exp_count += 1
    max_1h = rolling_max_abs_move(closes, 60)
    max_15m = rolling_max_abs_move(closes, 15)
    max_5m = rolling_max_abs_move(closes, 5)
    total_vol = float(vols.sum() * closes.mean())  # approximate quote volume
    score = max_1h * math.sqrt(max(1, exp_count)) * math.log10(max(1.0, total_vol))
    return {
        "max_1h_move": round(max_1h, 2),
        "max_15m_move": round(max_15m, 2),
        "max_5m_move": round(max_5m, 2),
        "explosive_bars": int(exp_count),
        "approx_quote_vol": round(total_vol, 0),
        "score": round(score, 2),
        "bars": len(bars),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--min-1h-move", type=float, default=2.0)
    ap.add_argument("--min-explosive", type=int, default=3)
    ap.add_argument("--min-quote-vol", type=float, default=1e7)
    ap.add_argument("--out", type=str, default=str(OUT_DIR / "movers_shortlist.json"))
    args = ap.parse_args()
    files = sorted(glob.glob(str(KLINES_DIR / "*_1m.json")))
    # Crypto-only for inf: USDT / USDC pairs. Skip stocks (no exchange suffix).
    files = [p for p in files if Path(p).stem.replace("_1m", "").endswith(("USDT", "USDC"))]
    print(f"[movers] scanning {len(files)} crypto symbols (USDT/USDC) ...")
    rows = []
    for i, path in enumerate(files):
        sym = Path(path).stem.replace("_1m", "")
        bars = load_1m(sym)
        if bars is None: continue
        s = score_symbol(bars)
        if not s: continue
        s["symbol"] = sym
        rows.append(s)
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(files)}")
    print(f"[movers] scored {len(rows)} symbols")
    # Hard filters
    filtered = [r for r in rows if r["max_1h_move"] >= args.min_1h_move
                and r["explosive_bars"] >= args.min_explosive
                and r["approx_quote_vol"] >= args.min_quote_vol]
    print(f"[movers] {len(filtered)} pass hard filters (1h≥{args.min_1h_move}%, exp≥{args.min_explosive}, qvol≥{args.min_quote_vol:.0e})")
    filtered.sort(key=lambda x: x["score"], reverse=True)
    top = filtered[: args.top]
    print(f"\n=== TOP {len(top)} MOVERS ===")
    print(f"{'symbol':16s} {'1h%':>7s} {'15m%':>6s} {'5m%':>6s} {'exp':>4s} {'score':>10s}")
    for r in top:
        print(f"  {r['symbol']:16s} {r['max_1h_move']:>6.2f}% {r['max_15m_move']:>5.2f}% {r['max_5m_move']:>5.2f}% {r['explosive_bars']:>4d} {r['score']:>10.1f}")
    with open(args.out, "w") as f:
        json.dump({"top": top, "filtered_count": len(filtered), "total_scanned": len(rows)}, f, indent=2)
    print(f"\nSaved → {args.out}")
    print(f"Symbols list: {','.join(r['symbol'] for r in top)}")


if __name__ == "__main__":
    main()
