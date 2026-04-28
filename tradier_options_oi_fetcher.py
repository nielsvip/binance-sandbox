"""
tradier_options_oi_fetcher.py — READ-ONLY options-chain OI sentiment fetcher for stocks.

USER DIRECTIVE 2026-04-27 (feedback_oi_signal_only_no_options_trading_20260427.md):
  "We are NOT opening options for now — UNTIL we have recovered the $20k losses.
   The OI is for finding the best equity trades on Tradier and the best futures
   trades on Binance."

Tradier exposes no L2 depth. Options-chain OI is the only sentiment proxy with:
  • a real money commitment behind each contract (vs. analyst opinions)
  • strike-level resolution (heavy call OI = options-implied resistance ceiling;
    heavy put OI = options-implied support floor — the equity-side red zones)

This module ONLY consumes /markets/options/{expirations,chains}. It NEVER:
  • places, cancels, or modifies any order
  • routes to any tradier_manage entry/exit path
  • opens an option position

Outputs to data/stocks_oi_cache/<SYM>.json (atomic write):
{
  "sym": "AAPL",
  "ts": <epoch>,
  "ts_iso": "2026-04-27T22:30:00Z",
  "underlying_price": 184.32,
  "expirations_used": ["2026-05-02","2026-05-09"],
  "n_contracts": 142,
  "total_call_oi": 458320,
  "total_put_oi": 312901,
  "pc_ratio": 0.683,                # < 0.7 = bullish, > 1.0 = bearish (consensus rule)
  "max_call_oi_strike": 185.0,      # options-implied resistance ceiling
  "max_call_oi_value": 18432,
  "max_call_oi_distance_pct": 0.37,
  "max_put_oi_strike": 180.0,       # options-implied support floor
  "max_put_oi_value": 14201,
  "max_put_oi_distance_pct": 2.34,
  "near_money_pc_ratio": 0.71,      # P/C limited to ±5% strikes — purer near-term sentiment
  "call_oi_change_24h_pct": null,   # filled on next-day refresh from previous cache
  "put_oi_change_24h_pct": null
}

Run modes:
  python3 tradier_options_oi_fetcher.py --once          # one cycle, log + exit
  python3 tradier_options_oi_fetcher.py                 # daemon, refresh every OI_REFRESH_SEC
  python3 tradier_options_oi_fetcher.py --syms AAPL,MSFT --once  # specific syms

Universe: union of symbols_trb_long.json + symbols_trb_short.json + symbols_trc_long.json + symbols_trc_short.json (~118 syms 2026-04-27).

Rate limit: stagger via asyncio Semaphore + per-call sleep. Tradier data API ~120/min
sustained. We call 2 endpoints per sym (expirations + chain), so ~60 syms/min ceiling.
For 118 syms, ~2 min/cycle. Safe.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
CACHE_DIR = BASE / "data" / "stocks_oi_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

OI_REFRESH_SEC = 3600          # full-universe refresh cadence (1h)
NEAREST_EXP_COUNT = 4          # 2026-04-28: widened 2→4 to capture ±50% deep heatmap (user request)
NEAR_MONEY_BAND_PCT = 5.0      # ±5% strike band for near_money_pc_ratio
DEEP_RANGE_PCT = 50.0          # 2026-04-28: ±50% strike range for deep heatmap (top-N walls within this range)
TOP_N_WALLS = 5                # 2026-04-28: capture top 5 highest-OI strikes per side (vs single max) for full S/R shelves
WALL_BUCKET_PCT = 5.0          # 2026-04-28: bucketize OI into 5%-wide strike bins (heatmap density)
PER_SYM_SLEEP_SEC = 0.4        # ~150 syms/min ceiling — keeps us under Tradier 120/min
MAX_PARALLEL_SYMS = 1          # serial — Tradier hates parallel chain fetches
LOG_FILE = BASE / "logs" / "tradier_options_oi_fetcher.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("tradier_oi_fetcher")
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


def load_universe(syms_arg: Optional[str] = None) -> List[str]:
    if syms_arg:
        return [s.strip().upper() for s in syms_arg.split(",") if s.strip()]
    universe: set = set()
    for fname in ("symbols_trb_long.json", "symbols_trb_short.json",
                  "symbols_trc_long.json", "symbols_trc_short.json"):
        try:
            data = json.load(open(BASE / fname))
            if isinstance(data, list):
                universe |= {str(s).upper() for s in data if isinstance(s, str)}
        except Exception as e:
            logger.warning(f"load_universe {fname}: {e}")
    return sorted(universe)


def _read_prev_cache(sym: str) -> Optional[dict]:
    try:
        p = CACHE_DIR / f"{sym}.json"
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache_atomic(sym: str, payload: dict):
    p = CACHE_DIR / f"{sym}.json"
    tmp = p.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        logger.warning(f"_write_cache_atomic {sym}: {e}")
        try: tmp.unlink()
        except Exception: pass


def _aggregate_chain(chain: List[dict], underlying_price: float) -> dict:
    """Aggregate option chain into per-sym OI sentiment + DEEP HEATMAP across ±50% range.

    2026-04-28 user request: same utility as OI but over the next 50% up or down instead of
    the open orders for the next minute. Captures top-N highest-OI strikes per side as
    options-implied S/R shelves, plus density bucketization for full heatmap visualization.
    """
    total_call_oi = 0
    total_put_oi = 0
    near_call_oi = 0
    near_put_oi = 0
    n_contracts = 0
    band_low = underlying_price * (1.0 - NEAR_MONEY_BAND_PCT / 100.0)
    band_high = underlying_price * (1.0 + NEAR_MONEY_BAND_PCT / 100.0)
    deep_low = underlying_price * (1.0 - DEEP_RANGE_PCT / 100.0)
    deep_high = underlying_price * (1.0 + DEEP_RANGE_PCT / 100.0)
    # Per-strike OI accumulator: { strike → {'call_oi':int, 'put_oi':int} }
    by_strike: Dict[float, Dict[str, int]] = {}
    for opt in chain:
        try:
            otype = (opt.get("option_type") or "").lower()
            strike = float(opt.get("strike") or 0)
            oi = int(opt.get("open_interest") or 0)
            if oi <= 0 or strike <= 0:
                continue
            # Drop strikes outside ±50% (filters spurious far-OTM tails that don't affect price)
            if strike < deep_low or strike > deep_high:
                continue
            n_contracts += 1
            in_band = band_low <= strike <= band_high
            entry = by_strike.setdefault(strike, {"call_oi": 0, "put_oi": 0})
            if otype == "call":
                entry["call_oi"] += oi
                total_call_oi += oi
                if in_band: near_call_oi += oi
            elif otype == "put":
                entry["put_oi"] += oi
                total_put_oi += oi
                if in_band: near_put_oi += oi
        except Exception:
            continue
    # Top-N walls per side
    call_walls = sorted([(s, d["call_oi"]) for s, d in by_strike.items() if d["call_oi"] > 0],
                        key=lambda x: -x[1])[:TOP_N_WALLS]
    put_walls = sorted([(s, d["put_oi"]) for s, d in by_strike.items() if d["put_oi"] > 0],
                       key=lambda x: -x[1])[:TOP_N_WALLS]
    # Bucketed heatmap (dollar volume normalized to % distance from underlying)
    # Buckets centered on underlying, ±DEEP_RANGE_PCT in WALL_BUCKET_PCT-wide bins.
    n_buckets = int(DEEP_RANGE_PCT / WALL_BUCKET_PCT) * 2  # both sides
    heatmap_buckets: List[dict] = []
    for i in range(n_buckets):
        # bucket i covers (-DEEP_RANGE_PCT + i*WALL_BUCKET_PCT, -DEEP_RANGE_PCT + (i+1)*WALL_BUCKET_PCT)
        lo_pct = -DEEP_RANGE_PCT + i * WALL_BUCKET_PCT
        hi_pct = lo_pct + WALL_BUCKET_PCT
        lo_px = underlying_price * (1.0 + lo_pct / 100.0)
        hi_px = underlying_price * (1.0 + hi_pct / 100.0)
        c_sum = sum(d["call_oi"] for s, d in by_strike.items() if lo_px <= s < hi_px)
        p_sum = sum(d["put_oi"] for s, d in by_strike.items() if lo_px <= s < hi_px)
        if c_sum + p_sum > 0:
            heatmap_buckets.append({
                "lo_pct": round(lo_pct, 1), "hi_pct": round(hi_pct, 1),
                "mid_pct": round((lo_pct + hi_pct) / 2.0, 1),
                "call_oi": c_sum, "put_oi": p_sum,
            })
    pc_ratio = (total_put_oi / total_call_oi) if total_call_oi > 0 else None
    near_pc = (near_put_oi / near_call_oi) if near_call_oi > 0 else None
    out = {
        "n_contracts": n_contracts,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "pc_ratio": round(pc_ratio, 4) if pc_ratio is not None else None,
        "near_money_pc_ratio": round(near_pc, 4) if near_pc is not None else None,
        "near_money_call_oi": near_call_oi,
        "near_money_put_oi": near_put_oi,
        # Backward-compat single max (existing consumers in tradier_rankings + tradier_manage)
        "max_call_oi_strike": call_walls[0][0] if call_walls else None,
        "max_call_oi_value": call_walls[0][1] if call_walls else 0,
        "max_put_oi_strike": put_walls[0][0] if put_walls else None,
        "max_put_oi_value": put_walls[0][1] if put_walls else 0,
        # 2026-04-28 DEEP HEATMAP fields — top-N walls + density buckets
        "top_call_walls": [{"strike": s, "oi": v,
                            "dist_pct": round((s - underlying_price) / underlying_price * 100.0, 3)}
                           for s, v in call_walls],
        "top_put_walls":  [{"strike": s, "oi": v,
                            "dist_pct": round((underlying_price - s) / underlying_price * 100.0, 3)}
                           for s, v in put_walls],
        "heatmap_buckets": heatmap_buckets,
        "deep_range_pct": DEEP_RANGE_PCT,
    }
    if call_walls and underlying_price > 0:
        out["max_call_oi_distance_pct"] = round((call_walls[0][0] - underlying_price) / underlying_price * 100.0, 3)
    if put_walls and underlying_price > 0:
        out["max_put_oi_distance_pct"] = round((underlying_price - put_walls[0][0]) / underlying_price * 100.0, 3)
    return out


async def fetch_one_symbol(api, sym: str) -> Optional[dict]:
    """Fetch nearest expirations + chain, aggregate, return payload. NEVER places orders."""
    try:
        # 1) underlying quote
        quote = await api.get_quote(sym)
        if not quote:
            logger.debug(f"{sym}: no quote, skip")
            return None
        underlying = float(quote.get("last") or quote.get("close") or 0)
        if underlying <= 0:
            logger.debug(f"{sym}: zero underlying, skip")
            return None
        # 2) expirations
        exps = await api.get_option_expirations(sym)
        if not exps:
            logger.debug(f"{sym}: no expirations")
            return None
        nearest = exps[:NEAREST_EXP_COUNT]
        # 3) walk chains for each nearest exp, accumulate
        all_contracts: List[dict] = []
        for exp in nearest:
            try:
                chain = await api.get_option_chain(sym, exp, greeks=False)
                if chain:
                    all_contracts.extend(chain)
            except Exception as e:
                logger.debug(f"{sym} {exp} chain err: {e}")
        if not all_contracts:
            logger.debug(f"{sym}: empty chain across {len(nearest)} exps")
            return None
        agg = _aggregate_chain(all_contracts, underlying)
        # 4) deltas vs. previous cache (24h-equivalent — fetcher refreshes hourly so it's the previous-cycle delta)
        prev = _read_prev_cache(sym)
        if prev:
            try:
                if prev.get("total_call_oi"):
                    agg["call_oi_change_pct"] = round(
                        (agg["total_call_oi"] - prev["total_call_oi"]) / prev["total_call_oi"] * 100.0, 3)
                if prev.get("total_put_oi"):
                    agg["put_oi_change_pct"] = round(
                        (agg["total_put_oi"] - prev["total_put_oi"]) / prev["total_put_oi"] * 100.0, 3)
                # delta windowed by elapsed seconds for sanity
                agg["prev_ts"] = prev.get("ts")
                agg["prev_age_sec"] = int(time.time() - (prev.get("ts") or 0)) if prev.get("ts") else None
            except Exception:
                pass
        agg.update({
            "sym": sym,
            "ts": int(time.time()),
            "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "underlying_price": round(underlying, 4),
            "expirations_used": nearest,
        })
        return agg
    except Exception as e:
        logger.warning(f"fetch_one_symbol {sym}: {type(e).__name__} {e}")
        return None


def _summarize_run(results: Dict[str, Optional[dict]]) -> str:
    ok = [r for r in results.values() if r]
    if not ok:
        return "all-fail"
    bullish = [r for r in ok if (r.get("pc_ratio") or 99) < 0.7]
    bearish = [r for r in ok if (r.get("pc_ratio") or 0) > 1.0]
    return (f"ok={len(ok)}/{len(results)} bullish_pc<0.7={len(bullish)} bearish_pc>1.0={len(bearish)} "
            f"sample_bullish={[(r['sym'], r['pc_ratio']) for r in bullish[:3]]} "
            f"sample_bearish={[(r['sym'], r['pc_ratio']) for r in bearish[:3]]}")


async def _run_cycle(syms: List[str]) -> Dict[str, Optional[dict]]:
    # Lazy import — avoids import cost at module load time and keeps us in READ-ONLY API surface
    from tradier_api import TradierAPIClient
    api = TradierAPIClient(account_key="trb")
    results: Dict[str, Optional[dict]] = {}
    t0 = time.time()
    for i, sym in enumerate(syms, 1):
        if not sym or any(c in sym for c in (" ", "/", ":", ",", "?")):
            results[sym] = None
            continue
        payload = await fetch_one_symbol(api, sym)
        results[sym] = payload
        if payload:
            _write_cache_atomic(sym, payload)
        if i % 10 == 0 or i == len(syms):
            logger.info(f"progress {i}/{len(syms)} elapsed={time.time()-t0:.0f}s last={sym} "
                        f"pc_ratio={payload.get('pc_ratio') if payload else None}")
        await asyncio.sleep(PER_SYM_SLEEP_SEC)
    try:
        await api.close() if hasattr(api, "close") else None
    except Exception:
        pass
    return results


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one cycle then exit")
    ap.add_argument("--syms", help="comma-separated symbol list (defaults to trb+trc tradeable union)")
    ap.add_argument("--refresh-sec", type=int, default=OI_REFRESH_SEC)
    args = ap.parse_args()
    syms = load_universe(args.syms)
    if not syms:
        logger.error("no symbols loaded — universe empty")
        sys.exit(2)
    logger.info(f"universe={len(syms)} syms refresh={args.refresh_sec}s once={args.once}")

    stop = asyncio.Event()
    def _on_signal(*_):
        logger.info("signal — stopping after current cycle")
        stop.set()
    try:
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
        loop.add_signal_handler(signal.SIGINT, _on_signal)
    except Exception:
        pass

    while not stop.is_set():
        t0 = time.time()
        results = await _run_cycle(syms)
        elapsed = time.time() - t0
        logger.info(f"cycle done in {elapsed:.0f}s | {_summarize_run(results)}")
        if args.once or stop.is_set():
            return
        sleep_for = max(60.0, args.refresh_sec - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
