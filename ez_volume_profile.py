"""
ez_volume_profile.py — DEEP volume-profile heatmap for crypto symbols.

USER REQUEST 2026-04-28: "Did you search find and apply heat maps (basically ez_order_book
but over the next 50% up or down instead of the open orders for the next minute)? same utility
as oi, not applied in our system needs adding and testing."

The complement to ez_orderbook.py:
  • ez_orderbook.py = NEAR-TERM (±5%, top-20 levels, what's queued NOW for the next minute)
  • ez_volume_profile.py = LONG-TERM (±50%, where HISTORICAL trade volume actually executed)

Volume profile from kline history is a battle-tested technique:
  • For each historical bar: distribute its volume uniformly across (low→high) range
  • Bucketize ±50% from current price into N bins
  • Sum per-bucket volume → density heatmap
  • High-Volume Nodes (HVN) = persistent S/R that pin price for hours/days
  • Top-K HVNs above price = resistance shelves; below = support floors

Outputs to Redis `vol_profile:<SYM>` (TTL 3600s) with:
  vp_ts                 epoch
  vp_underlying_price   reference price
  vp_lookback_bars      N bars used (typically 1000–1500 = ~10–16 days at 15m)
  vp_top_hvn_above      top-K HVN buckets above price (resistance shelves)
                        each = {mid_pct, lo_pct, hi_pct, volume, density_z}
  vp_top_hvn_below      top-K HVN buckets below price (support floors)
  vp_bucket_pct         bucket width in % (default 1.0)
  vp_density_threshold  density-z threshold used (HVN if z >= threshold)

Symbols: union of ALL crypto symbols.json (futures USDT pairs).

Refresh every 1h (klines update slowly enough). Each sym: ~30 ms cpu (numpy bucketize).
Full 200-sym cycle: ~6 sec. Tiny memory footprint.

Read-only — never opens orders.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import orjson
import redis.asyncio as aioredis

BASE = Path(__file__).resolve().parent
KLINES_DIR = BASE / "klines_cache"
SYMBOLS_FILE = BASE / "symbols.json"
LOG_FILE = BASE / "logs" / "ez_volume_profile.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

REFRESH_SEC = 3600
RANGE_PCT = 50.0          # ±50% from current price
BUCKET_PCT = 1.0          # 1% bucket = 100 bins total
DENSITY_Z_THRESHOLD = 1.5 # HVN if bucket volume z-score ≥ 1.5 above mean
TOP_K_HVN = 5             # report top-5 HVNs per side
LOOKBACK_BARS = 1500      # ~16 days at 15m
KLINE_TF = "15m"
REDIS_TTL_SEC = 3900      # slightly > REFRESH_SEC so stale gets noticed

logger = logging.getLogger("ez_volume_profile")
_h_console = logging.StreamHandler(sys.stdout)
_h_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_h_console)
try:
    _h_file = logging.FileHandler(LOG_FILE)
    _h_file.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(_h_file)
except Exception:
    pass
logger.setLevel(logging.INFO)


def load_symbols() -> List[str]:
    try:
        with open(SYMBOLS_FILE) as f:
            data = json.load(f)
        if isinstance(data, list):
            return [s for s in data if isinstance(s, str)]
        if isinstance(data, dict):
            return list(data.keys())
    except Exception as e:
        logger.warning(f"load_symbols: {e}")
    return []


def _load_klines(sym: str) -> Optional[List[dict]]:
    p = KLINES_DIR / f"{sym}_{KLINE_TF}.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data[-LOOKBACK_BARS:]
    except Exception as e:
        logger.debug(f"_load_klines {sym}: {e}")
    return None


def _last_close(klines: List[dict]) -> Optional[float]:
    if not klines:
        return None
    try:
        return float(klines[-1].get("close") or 0)
    except Exception:
        return None


def compute_vol_profile(sym: str, klines: List[dict]) -> Optional[dict]:
    """Bucketize trade volume into ±RANGE_PCT bins centered on the most recent close.
    Returns dict ready for Redis or None if data insufficient."""
    if not klines or len(klines) < 50:
        return None
    last_close = _last_close(klines)
    if not last_close or last_close <= 0:
        return None
    # Build numpy arrays
    try:
        highs = np.array([float(b.get("high") or 0) for b in klines])
        lows = np.array([float(b.get("low") or 0) for b in klines])
        vols = np.array([float(b.get("volume") or 0) for b in klines])
    except Exception as e:
        logger.debug(f"{sym} array build: {e}")
        return None
    valid = (highs > 0) & (lows > 0) & (vols > 0) & (highs >= lows)
    if valid.sum() < 30:
        return None
    highs, lows, vols = highs[valid], lows[valid], vols[valid]
    # Bucket boundaries — % offsets centered on last_close
    n_buckets = int(2 * RANGE_PCT / BUCKET_PCT)  # 100 buckets at 1% wide
    edge_pcts = np.linspace(-RANGE_PCT, RANGE_PCT, n_buckets + 1)
    edge_prices = last_close * (1.0 + edge_pcts / 100.0)  # length n_buckets+1
    # For each bar, distribute its volume across overlapping buckets uniformly (proportional to bar's high-low coverage of bucket)
    bucket_vol = np.zeros(n_buckets)
    for i in range(len(highs)):
        h, l, v = highs[i], lows[i], vols[i]
        # Find bucket indices that overlap [l, h]
        # bucket_j covers [edge_prices[j], edge_prices[j+1])
        # Skip if bar entirely outside ±50%
        if h < edge_prices[0] or l > edge_prices[-1]:
            continue
        # Clamp into range
        l_eff = max(l, edge_prices[0])
        h_eff = min(h, edge_prices[-1])
        if h_eff <= l_eff:
            continue
        bar_range = h - l if h > l else 1e-9
        # Indices of buckets the bar overlaps (search edges)
        j_lo = int(np.searchsorted(edge_prices, l_eff, side="right") - 1)
        j_hi = int(np.searchsorted(edge_prices, h_eff, side="right") - 1)
        j_lo = max(0, min(n_buckets - 1, j_lo))
        j_hi = max(0, min(n_buckets - 1, j_hi))
        for j in range(j_lo, j_hi + 1):
            b_lo = edge_prices[j]
            b_hi = edge_prices[j + 1]
            overlap_lo = max(b_lo, l)
            overlap_hi = min(b_hi, h)
            if overlap_hi <= overlap_lo:
                continue
            frac = (overlap_hi - overlap_lo) / bar_range
            bucket_vol[j] += v * frac
    # Density z-score
    if bucket_vol.sum() <= 0:
        return None
    mean_v = bucket_vol.mean()
    std_v = bucket_vol.std() if bucket_vol.std() > 0 else 1.0
    z_scores = (bucket_vol - mean_v) / std_v
    # Build buckets list (only non-zero)
    buckets = []
    for j in range(n_buckets):
        if bucket_vol[j] <= 0:
            continue
        mid_pct = (edge_pcts[j] + edge_pcts[j + 1]) / 2.0
        buckets.append({
            "mid_pct": round(float(mid_pct), 2),
            "lo_pct": round(float(edge_pcts[j]), 2),
            "hi_pct": round(float(edge_pcts[j + 1]), 2),
            "volume": round(float(bucket_vol[j]), 2),
            "density_z": round(float(z_scores[j]), 3),
            "is_hvn": bool(z_scores[j] >= DENSITY_Z_THRESHOLD),
        })
    # Top-K HVNs above (mid_pct > 0) and below (mid_pct < 0), sorted by density_z desc
    above = [b for b in buckets if b["mid_pct"] > 0 and b["is_hvn"]]
    below = [b for b in buckets if b["mid_pct"] < 0 and b["is_hvn"]]
    above_sorted = sorted(above, key=lambda x: -x["density_z"])[:TOP_K_HVN]
    below_sorted = sorted(below, key=lambda x: -x["density_z"])[:TOP_K_HVN]
    return {
        "vp_ts": int(time.time()),
        "vp_underlying_price": round(last_close, 8),
        "vp_lookback_bars": int(valid.sum()),
        "vp_bucket_pct": BUCKET_PCT,
        "vp_density_threshold": DENSITY_Z_THRESHOLD,
        "vp_top_hvn_above": above_sorted,
        "vp_top_hvn_below": below_sorted,
        "vp_total_volume": round(float(bucket_vol.sum()), 2),
    }


async def run_cycle(redis: aioredis.Redis, syms: List[str]) -> Tuple[int, int]:
    ok = 0; skipped = 0
    for sym in syms:
        kl = _load_klines(sym)
        if not kl:
            skipped += 1
            continue
        prof = compute_vol_profile(sym, kl)
        if not prof:
            skipped += 1
            continue
        try:
            await redis.set(f"vol_profile:{sym}", orjson.dumps(prof), ex=REDIS_TTL_SEC)
            ok += 1
        except Exception as e:
            logger.debug(f"{sym} redis write: {e}")
            skipped += 1
    return ok, skipped


async def main():
    syms = load_symbols()
    if not syms:
        logger.error("no symbols loaded — symbols.json missing or invalid")
        sys.exit(2)
    redis = aioredis.from_url("redis://localhost:6379", decode_responses=False)

    stop = asyncio.Event()
    def _on_signal(*_):
        logger.info("signal — stopping")
        stop.set()
    try:
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
        loop.add_signal_handler(signal.SIGINT, _on_signal)
    except Exception:
        pass

    logger.info(f"ez_volume_profile starting | syms={len(syms)} refresh={REFRESH_SEC}s range=±{RANGE_PCT}% bucket={BUCKET_PCT}%")
    while not stop.is_set():
        t0 = time.time()
        try:
            ok, skipped = await run_cycle(redis, syms)
            elapsed = time.time() - t0
            logger.info(f"cycle done {elapsed:.1f}s | wrote={ok} skipped={skipped}")
        except Exception as e:
            logger.warning(f"cycle err: {type(e).__name__} {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=REFRESH_SEC)
        except asyncio.TimeoutError:
            pass
    try:
        await redis.aclose()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
