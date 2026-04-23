"""
ez_orderbook.py — Live L2 orderbook feature service (Binance futures).

Subscribes to @depth20@100ms for the union of tradeable symbols across all
crypto accounts (inf/ang/flz/men/fin). Maintains in-memory top-20 books.
Computes features every 250ms and writes to Redis `orderbook:<SYM>` (ex=10s):

  ob_ts_ms            event time (ms)
  ob_mid              (best_bid + best_ask) / 2
  ob_microprice       size-weighted equilibrium
  ob_spread_bps       (best_ask - best_bid) / mid * 10000
  ob_bid_ask_imb_5    sum(bid_qty[:5]) / (sum(bid_qty[:5]) + sum(ask_qty[:5]))   (0..1, >0.5 bid-heavy)
  ob_bid_ask_imb_10   same for top 10
  ob_bid_ask_imb_20   same for top 20
  ob_top_bid_qty      qty at best bid
  ob_top_ask_qty      qty at best ask
  ob_ofi_1s           signed top-of-book size delta over last 1s
  ob_bid_surge_60s    top_bid_qty / ema60s(top_bid_qty)
  ob_ask_surge_60s    top_ask_qty / ema60s(top_ask_qty)

Reloads symbol universe every 5 min (tradeable_keys.json). Reconnects with
backoff on WS drop. Heartbeat to Redis `orderbook:_heartbeat` every 30s.

No live-trading side-effects. Readers opt-in by reading `orderbook:<SYM>`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import orjson
import redis.asyncio as aioredis
import websockets

BASE = Path(__file__).resolve().parent
TRADEABLE_FILE = BASE / "tradeable_keys.json"
CRYPTO_ACCOUNTS = ("inf", "ang", "flz", "men", "fin")

WS_BASE = "wss://fstream.binance.com/stream"
STREAM_SUFFIX = "@depth20@100ms"
WRITE_INTERVAL_SEC = 0.25
HEARTBEAT_SEC = 30.0
RELOAD_SEC = 300.0
HISTORY_LEN = 100            # top-of-book snapshots per symbol (10s at 100ms)
EMA_ALPHA = 1.0 / 600.0      # 60s half-life over 100ms updates
MAX_SYMS_PER_WS = 200        # combined stream safe chunk size

logger = logging.getLogger("ez_orderbook")
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_h)
logger.setLevel(logging.INFO)


def load_symbols() -> List[str]:
    """Union of symbols across all crypto account keys in tradeable_keys.json."""
    try:
        with open(TRADEABLE_FILE) as f:
            keys = json.load(f)
    except Exception as e:
        logger.warning(f"tradeable_keys load failed: {e}")
        return []
    syms: set = set()
    for k in keys:
        if not isinstance(k, str) or ":" not in k:
            continue
        acct, rest = k.split(":", 1)
        if acct not in CRYPTO_ACCOUNTS:
            continue
        if rest.endswith("_LONG"):
            syms.add(rest[:-5])
        elif rest.endswith("_SHORT"):
            syms.add(rest[:-6])
    return sorted(syms)


class OrderbookTracker:
    def __init__(self):
        self.books: Dict[str, dict] = {}
        self.tob_history: Dict[str, deque] = {}
        self.ema_bid: Dict[str, float] = {}
        self.ema_ask: Dict[str, float] = {}

    def update(self, sym: str, bids: List[Tuple[float, float]], asks: List[Tuple[float, float]], ts_ms: int):
        self.books[sym] = {"bids": bids, "asks": asks, "ts_ms": ts_ms}
        tb = bids[0][1] if bids else 0.0
        ta = asks[0][1] if asks else 0.0
        prev_b = self.ema_bid.get(sym, tb)
        prev_a = self.ema_ask.get(sym, ta)
        self.ema_bid[sym] = EMA_ALPHA * tb + (1.0 - EMA_ALPHA) * prev_b
        self.ema_ask[sym] = EMA_ALPHA * ta + (1.0 - EMA_ALPHA) * prev_a
        hist = self.tob_history.setdefault(sym, deque(maxlen=HISTORY_LEN))
        hist.append((ts_ms,
                     bids[0][0] if bids else 0.0, bids[0][1] if bids else 0.0,
                     asks[0][0] if asks else 0.0, asks[0][1] if asks else 0.0))

    def compute(self, sym: str) -> Optional[dict]:
        book = self.books.get(sym)
        if not book:
            return None
        bids = book["bids"]; asks = book["asks"]
        if not bids or not asks:
            return None
        top_bp, top_bq = bids[0]
        top_ap, top_aq = asks[0]
        if top_bp <= 0 or top_ap <= 0:
            return None
        mid = (top_bp + top_ap) / 2.0
        denom = top_bq + top_aq
        microprice = (top_bq * top_ap + top_aq * top_bp) / denom if denom > 0 else mid
        spread_bps = (top_ap - top_bp) / mid * 10000.0

        def imb(n: int) -> float:
            sb = sum(q for _, q in bids[:n])
            sa = sum(q for _, q in asks[:n])
            return sb / (sb + sa) if (sb + sa) > 0 else 0.5

        ofi_1s = 0.0
        hist = self.tob_history.get(sym)
        if hist and len(hist) >= 2:
            cutoff = book["ts_ms"] - 1000
            slice_ = [h for h in hist if h[0] >= cutoff]
            for i in range(1, len(slice_)):
                _, bp0, bq0, ap0, aq0 = slice_[i - 1]
                _, bp1, bq1, ap1, aq1 = slice_[i]
                if bp1 > bp0: dbid = bq1
                elif bp1 < bp0: dbid = -bq0
                else: dbid = bq1 - bq0
                if ap1 < ap0: dask = aq1
                elif ap1 > ap0: dask = -aq0
                else: dask = aq1 - aq0
                ofi_1s += dbid - dask

        ema_b = self.ema_bid.get(sym, top_bq) or top_bq or 1e-9
        ema_a = self.ema_ask.get(sym, top_aq) or top_aq or 1e-9
        return {
            "ob_ts_ms": book["ts_ms"],
            "ob_mid": round(mid, 8),
            "ob_microprice": round(microprice, 8),
            "ob_spread_bps": round(spread_bps, 4),
            "ob_bid_ask_imb_5": round(imb(5), 4),
            "ob_bid_ask_imb_10": round(imb(10), 4),
            "ob_bid_ask_imb_20": round(imb(20), 4),
            "ob_top_bid_qty": round(top_bq, 6),
            "ob_top_ask_qty": round(top_aq, 6),
            "ob_ofi_1s": round(ofi_1s, 4),
            "ob_bid_surge_60s": round(top_bq / ema_b, 3),
            "ob_ask_surge_60s": round(top_aq / ema_a, 3),
        }


async def stream_chunk(symbols: List[str], tracker: OrderbookTracker, stop: asyncio.Event):
    streams = "/".join(f"{s.lower()}{STREAM_SUFFIX}" for s in symbols)
    url = f"{WS_BASE}?streams={streams}"
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10, max_size=10_000_000) as ws:
                logger.info(f"WS connected chunk={len(symbols)}syms first={symbols[0]} last={symbols[-1]}")
                backoff = 1.0
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"WS idle 30s chunk={symbols[0]}.. reconnecting")
                        break
                    msg = orjson.loads(raw)
                    data = msg.get("data", {})
                    sym = data.get("s")
                    if not sym:
                        continue
                    try:
                        bids = [(float(p), float(q)) for p, q in data.get("b", [])]
                        asks = [(float(p), float(q)) for p, q in data.get("a", [])]
                    except Exception:
                        continue
                    ts_ms = int(data.get("E") or data.get("T") or (time.time() * 1000))
                    tracker.update(sym, bids, asks, ts_ms)
        except Exception as e:
            if stop.is_set():
                break
            logger.warning(f"WS error chunk={symbols[0]}..: {type(e).__name__} {e} — reconnect in {backoff:.1f}s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(60.0, backoff * 2.0)


async def write_loop(tracker: OrderbookTracker, redis: aioredis.Redis, get_symbols, stop: asyncio.Event):
    last_heartbeat = 0.0
    updates_written = 0
    cycle = 0
    while not stop.is_set():
        t0 = time.time()
        syms = get_symbols()
        for sym in syms:
            feat = tracker.compute(sym)
            if feat is None:
                continue
            try:
                await redis.set(f"orderbook:{sym}", orjson.dumps(feat), ex=10)
                updates_written += 1
            except Exception as e:
                logger.debug(f"redis write failed {sym}: {e}")
        cycle += 1
        if t0 - last_heartbeat >= HEARTBEAT_SEC:
            active = sum(1 for s in syms if s in tracker.books)
            try:
                await redis.set("orderbook:_heartbeat", orjson.dumps({"ts": int(t0), "active_syms": active, "tracked_syms": len(syms), "writes_total": updates_written, "cycles": cycle}), ex=120)
            except Exception:
                pass
            logger.info(f"heartbeat cycles={cycle} active={active}/{len(syms)} writes={updates_written}")
            last_heartbeat = t0
        elapsed = time.time() - t0
        sleep = max(0.05, WRITE_INTERVAL_SEC - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep)
        except asyncio.TimeoutError:
            pass


async def symbol_reload_loop(get_active: callable, on_change: callable, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=RELOAD_SEC)
        except asyncio.TimeoutError:
            new_syms = load_symbols()
            cur = get_active()
            if set(new_syms) != set(cur):
                added = set(new_syms) - set(cur)
                removed = set(cur) - set(new_syms)
                logger.info(f"universe reload: {len(new_syms)} (+{len(added)} -{len(removed)}) added={sorted(added)[:10]}")
                on_change(new_syms)


async def main():
    syms = load_symbols()
    if not syms:
        logger.error("no symbols from tradeable_keys.json for crypto accounts — exiting")
        return
    logger.info(f"starting ez_orderbook: universe={len(syms)} first={syms[0]} last={syms[-1]}")

    tracker = OrderbookTracker()
    redis = aioredis.from_url("redis://localhost:6379/0", decode_responses=False)
    await redis.ping()

    stop = asyncio.Event()

    def _handle(sig):
        logger.info(f"signal {sig} → stopping")
        stop.set()

    loop = asyncio.get_event_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _handle, s)

    active_syms: List[str] = list(syms)
    ws_tasks: List[asyncio.Task] = []

    def spawn_ws():
        for t in ws_tasks:
            t.cancel()
        ws_tasks.clear()
        chunks = [active_syms[i:i + MAX_SYMS_PER_WS] for i in range(0, len(active_syms), MAX_SYMS_PER_WS)]
        for chunk in chunks:
            ws_tasks.append(asyncio.create_task(stream_chunk(chunk, tracker, stop)))
        logger.info(f"spawned {len(ws_tasks)} ws tasks ({len(active_syms)} syms)")

    def on_universe_change(new_syms: List[str]):
        nonlocal active_syms
        active_syms = list(new_syms)
        spawn_ws()

    spawn_ws()

    writer = asyncio.create_task(write_loop(tracker, redis, lambda: list(active_syms), stop))
    reloader = asyncio.create_task(symbol_reload_loop(lambda: list(active_syms), on_universe_change, stop))

    try:
        await stop.wait()
    finally:
        writer.cancel()
        reloader.cancel()
        for t in ws_tasks:
            t.cancel()
        await asyncio.gather(*ws_tasks, writer, reloader, return_exceptions=True)
        try:
            await redis.close()
        except Exception:
            pass
        logger.info("shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
