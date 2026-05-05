"""
ez_orderbook.py — DEEP L2 orderbook service (Binance futures).

Maintains full order books via REST snapshot + `@depth@100ms` diff stream per
symbol. Buckets volumes at 0.5% steps from mid out to ±10%. Writes per-symbol
features to Redis `orderbook:<SYM>` every 250 ms:

  # Best levels / mid
  ob_ts_ms          event time
  ob_mid            (best_bid + best_ask) / 2
  ob_microprice     size-weighted equilibrium at top-of-book
  ob_spread_bps     (best_ask − best_bid) / mid × 10000
  ob_bid_levels     # of bid price levels currently held
  ob_ask_levels     # of ask price levels currently held

  # Top-of-book + short-window imbalance
  ob_bid_ask_imb_5  bid_notional[:5 lvls] / total   (0..1, >0.5 bid-heavy)
  ob_bid_ask_imb_10 same top 10
  ob_bid_ask_imb_20 same top 20
  ob_top_bid_qty
  ob_top_ask_qty
  ob_ofi_1s         signed top-of-book size delta over last 1 s

  # Deep microstructure (±10% range, 0.5% buckets = 40 rows)
  ob_imb_5pct         bid_notional / total inside ±5%
  ob_imb_10pct        bid_notional / total inside ±10%
  ob_bid_wall_pct     distance % to nearest bid bucket with >3× mean bid-bucket vol (support floor)
  ob_bid_wall_size    notional in that wall
  ob_ask_wall_pct     distance % to nearest ask bucket with >3× mean ask-bucket vol (resistance)
  ob_ask_wall_size    notional in that wall
  ob_bid_void_pct     distance % to nearest bid bucket with <0.2× mean within 5% (gap-down zone)
  ob_ask_void_pct     distance % to nearest ask bucket with <0.2× mean within 5% (gap-up zone)
  ob_bid_vol_buckets  list of 20 bid-side notional values (first 10% in 0.5% slices)
  ob_ask_vol_buckets  list of 20 ask-side notional values

  # Composite entry signals (0..100)
  ob_long_score     = wall_below_close (30) + void_above_close (30) + top-imb_bid_lean (0..40)
  ob_short_score    = wall_above_close (30) + void_below_close (30) + top-imb_ask_lean (0..40)

Designed for SCALP_V3 scanner: high long_score / high short_score is the
entry signal INSTEAD OF waiting for stoch K / candle confirmation.

Runs standalone (no Redis rate-limit risk, websocket only after one-time
snapshot fetch per symbol with 250 ms stagger). Reloads symbol universe
every 5 min from tradeable_keys.json (crypto accounts union).
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

import aiohttp
import orjson
import redis.asyncio as aioredis
import websockets

BASE = Path(__file__).resolve().parent
SYMBOLS_FILE = BASE / "symbols.json"

WS_BASE = "wss://fstream.binance.com/stream"
REST_BASE = "https://fapi.binance.com/fapi/v1/depth"
WRITE_INTERVAL_SEC = 0.25
HEARTBEAT_SEC = 30.0
RELOAD_SEC = 300.0
# 2026-04-23 late: `@depth20@100ms` is a PARTIAL-book stream — gives top 20 levels
# snapshot every 100ms, NO REST snapshot needed, NO IP-ban risk. We lose the 10%
# bucket depth, but compute top-of-book imbalance + close-in walls/voids anyway.
# For ±10% bucketing we'd need the diff stream + REST seed which hits -1003.
USE_PARTIAL_STREAM = True    # False → diff-stream+REST (burns IP weight)
PARTIAL_DEPTH = 20
STREAM_SUFFIX = f"@depth{PARTIAL_DEPTH}@100ms" if USE_PARTIAL_STREAM else "@depth@100ms"
SNAPSHOT_LIMIT = 1000
SNAPSHOT_STAGGER_MS = 250
BUCKET_PCT = 0.25             # 0.25% steps (20 levels usually covers ~0.5-3%)
RANGE_PCT = 5.0              # ±5% from mid (top-20 rarely reaches 10% anyway)
N_BUCKETS = int(RANGE_PCT / BUCKET_PCT)
HISTORY_LEN = 100
EMA_ALPHA = 1.0 / 600.0

logger = logging.getLogger("ez_orderbook")
_h = logging.StreamHandler(sys.stdout)
_h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_h)
logger.setLevel(logging.INFO)


def load_symbols() -> List[str]:
    try:
        with open(SYMBOLS_FILE) as f:
            syms = json.load(f)
        return sorted(s for s in syms if isinstance(s, str) and ("USDT" in s or "USDC" in s))
    except Exception as e:
        logger.warning(f"symbols.json load failed: {e}")
        return []


class DeepBook:
    """Full L2 book for one symbol. Seeded via REST then maintained via diff stream."""
    __slots__ = ("symbol", "bids", "asks", "last_update_id", "snapshot_applied",
                 "buffered_updates", "tob_history", "ema_bid_qty", "ema_ask_qty",
                 "last_event_ts_ms")

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.last_update_id: int = 0
        self.snapshot_applied: bool = False
        self.buffered_updates: List[dict] = []
        self.tob_history: deque = deque(maxlen=HISTORY_LEN)
        self.ema_bid_qty: float = 0.0
        self.ema_ask_qty: float = 0.0
        self.last_event_ts_ms: int = 0

    def apply_snapshot(self, data: dict):
        self.last_update_id = int(data["lastUpdateId"])
        self.bids = {float(p): float(q) for p, q in data.get("bids", []) if float(q) > 0}
        self.asks = {float(p): float(q) for p, q in data.get("asks", []) if float(q) > 0}
        # Replay buffered diffs that came in while snapshot was in flight
        drained = 0
        for u in self.buffered_updates:
            if int(u.get("u", 0)) < self.last_update_id:
                continue
            self._apply_diff(u)
            drained += 1
        self.buffered_updates.clear()
        self.snapshot_applied = True
        logger.info(f"seeded {self.symbol}: {len(self.bids)} bids, {len(self.asks)} asks, drained {drained} buffered diffs")

    def apply_update(self, u: dict):
        if USE_PARTIAL_STREAM:
            # @depth20@100ms = full partial-book snapshot each tick (NOT a diff).
            # Replace book entirely with the top 20 levels.
            try:
                self.bids = {float(p): float(q) for p, q in u.get("b", []) if float(q) > 0}
                self.asks = {float(p): float(q) for p, q in u.get("a", []) if float(q) > 0}
            except (ValueError, TypeError):
                return
            self.last_event_ts_ms = int(u.get("E", u.get("T", 0)) or 0)
            self.snapshot_applied = True   # no REST needed on partial stream
            if self.bids and self.asks:
                best_bid = max(self.bids.keys())
                best_ask = min(self.asks.keys())
                top_bid_q = self.bids.get(best_bid, 0.0)
                top_ask_q = self.asks.get(best_ask, 0.0)
                prev_b = self.ema_bid_qty or top_bid_q
                prev_a = self.ema_ask_qty or top_ask_q
                self.ema_bid_qty = EMA_ALPHA * top_bid_q + (1.0 - EMA_ALPHA) * prev_b
                self.ema_ask_qty = EMA_ALPHA * top_ask_q + (1.0 - EMA_ALPHA) * prev_a
                self.tob_history.append((self.last_event_ts_ms, best_bid, top_bid_q, best_ask, top_ask_q))
            return
        # Diff-stream path (needs REST seed)
        if not self.snapshot_applied:
            self.buffered_updates.append(u)
            if len(self.buffered_updates) > 1000:
                self.buffered_updates = self.buffered_updates[-500:]
            return
        self._apply_diff(u)

    def _apply_diff(self, u: dict):
        # Standard Binance futures depth diff — {b: [[p,q]...], a: [...], U, u, E, T}
        for p, q in u.get("b", []):
            try:
                p_f = float(p); q_f = float(q)
            except (ValueError, TypeError):
                continue
            if q_f == 0:
                self.bids.pop(p_f, None)
            else:
                self.bids[p_f] = q_f
        for p, q in u.get("a", []):
            try:
                p_f = float(p); q_f = float(q)
            except (ValueError, TypeError):
                continue
            if q_f == 0:
                self.asks.pop(p_f, None)
            else:
                self.asks[p_f] = q_f
        self.last_update_id = int(u.get("u", self.last_update_id))
        self.last_event_ts_ms = int(u.get("E", u.get("T", 0)) or 0)
        # Update top-of-book EMA and history for OFI
        if self.bids and self.asks:
            best_bid = max(self.bids.keys())
            best_ask = min(self.asks.keys())
            top_bid_q = self.bids.get(best_bid, 0.0)
            top_ask_q = self.asks.get(best_ask, 0.0)
            prev_b = self.ema_bid_qty or top_bid_q
            prev_a = self.ema_ask_qty or top_ask_q
            self.ema_bid_qty = EMA_ALPHA * top_bid_q + (1.0 - EMA_ALPHA) * prev_b
            self.ema_ask_qty = EMA_ALPHA * top_ask_q + (1.0 - EMA_ALPHA) * prev_a
            self.tob_history.append((self.last_event_ts_ms, best_bid, top_bid_q, best_ask, top_ask_q))

    def compute_features(self) -> Optional[dict]:
        if not self.snapshot_applied or not self.bids or not self.asks:
            return None
        best_bid = max(self.bids.keys())
        best_ask = min(self.asks.keys())
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return None
        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10000.0
        top_bid_q = self.bids[best_bid]
        top_ask_q = self.asks[best_ask]
        denom = top_bid_q + top_ask_q
        microprice = (top_bid_q * best_ask + top_ask_q * best_bid) / denom if denom > 0 else mid

        # Sort level lists ONCE for bucketing + top-N imbalance
        bid_items = sorted(self.bids.items(), key=lambda x: -x[0])   # highest price first
        ask_items = sorted(self.asks.items(), key=lambda x: x[0])    # lowest price first

        # Top-N notional imbalance
        def top_imb(n: int) -> float:
            sb = sum(p * q for p, q in bid_items[:n])
            sa = sum(p * q for p, q in ask_items[:n])
            return sb / (sb + sa) if (sb + sa) > 0 else 0.5

        # Bucket NOTIONAL (qty × price) by distance from mid
        bid_vol = [0.0] * N_BUCKETS
        ask_vol = [0.0] * N_BUCKETS
        for p, q in bid_items:
            d = (mid - p) / mid * 100.0
            if d <= 0 or d > RANGE_PCT:
                if d > RANGE_PCT: break  # sorted descending, further prices only get worse
                continue
            idx = min(int(d / BUCKET_PCT), N_BUCKETS - 1)
            bid_vol[idx] += q * p
        for p, q in ask_items:
            d = (p - mid) / mid * 100.0
            if d <= 0 or d > RANGE_PCT:
                if d > RANGE_PCT: break
                continue
            idx = min(int(d / BUCKET_PCT), N_BUCKETS - 1)
            ask_vol[idx] += q * p

        # Wall/void detection: EXCLUDE bucket 0 (top-of-book concentration naturally
        # dominates; counts as a wall for everything if included). Use mean of the
        # *rest* of the buckets as the baseline. Threshold 4× (was 3×) for stronger
        # walls. Skip first bucket when scanning too.
        bid_rest = bid_vol[1:]
        ask_rest = ask_vol[1:]
        mean_bid_rest = (sum(bid_rest) / len(bid_rest)) if bid_rest else 0.0
        mean_ask_rest = (sum(ask_rest) / len(ask_rest)) if ask_rest else 0.0

        bid_wall_pct = None; bid_wall_size = 0.0
        for i in range(1, N_BUCKETS):
            v = bid_vol[i]
            if v > 4.0 * mean_bid_rest and v > 0:
                bid_wall_pct = (i + 0.5) * BUCKET_PCT
                bid_wall_size = v
                break
        ask_wall_pct = None; ask_wall_size = 0.0
        for i in range(1, N_BUCKETS):
            v = ask_vol[i]
            if v > 4.0 * mean_ask_rest and v > 0:
                ask_wall_pct = (i + 0.5) * BUCKET_PCT
                ask_wall_size = v
                break

        # Voids (<0.15× mean_rest) within buckets 1..10 (0.5% to 5%). Skip first bucket.
        n_short = int(5.0 / BUCKET_PCT)
        bid_void_pct = None
        for i in range(1, n_short):
            if bid_vol[i] < 0.15 * mean_bid_rest and mean_bid_rest > 0:
                bid_void_pct = (i + 0.5) * BUCKET_PCT
                break
        ask_void_pct = None
        for i in range(1, n_short):
            if ask_vol[i] < 0.15 * mean_ask_rest and mean_ask_rest > 0:
                ask_void_pct = (i + 0.5) * BUCKET_PCT
                break

        # ±5% and ±10% composite imbalances (notional based)
        bid_sum_5 = sum(bid_vol[:n_short]); ask_sum_5 = sum(ask_vol[:n_short])
        bid_sum_10 = sum(bid_vol); ask_sum_10 = sum(ask_vol)
        imb_5pct = bid_sum_5 / (bid_sum_5 + ask_sum_5) if (bid_sum_5 + ask_sum_5) > 0 else 0.5
        imb_10pct = bid_sum_10 / (bid_sum_10 + ask_sum_10) if (bid_sum_10 + ask_sum_10) > 0 else 0.5

        # OFI over last 1 s from TOB history
        ofi_1s = 0.0
        if len(self.tob_history) >= 2:
            cutoff = self.last_event_ts_ms - 1000
            recent = [h for h in self.tob_history if h[0] >= cutoff]
            for i in range(1, len(recent)):
                _, bp0, bq0, ap0, aq0 = recent[i - 1]
                _, bp1, bq1, ap1, aq1 = recent[i]
                if bp1 > bp0: dbid = bq1
                elif bp1 < bp0: dbid = -bq0
                else: dbid = bq1 - bq0
                if ap1 < ap0: dask = aq1
                elif ap1 > ap0: dask = -aq0
                else: dask = aq1 - aq0
                ofi_1s += dbid - dask

        bid_surge = top_bid_q / self.ema_bid_qty if self.ema_bid_qty > 0 else 1.0
        ask_surge = top_ask_q / self.ema_ask_qty if self.ema_ask_qty > 0 else 1.0

        # Composite entry signals (each 0..165)
        # 2026-04-27: Original score = static geometry only (walls/voids/imbalance).
        # That fires when book is balanced + S/R close — NOT at extremes/reversals.
        # Added OFI / surge / microprice = LEADING flow signals that fire BEFORE
        # WT/K confirm. Values clamp safely; original geometry component preserved.
        long_score = 0.0; short_score = 0.0
        # ── Static geometry (original, unchanged) ──
        # A close bid-wall means price has a floor here — good for LONG. Loosened
        # gate from ≤2% to ≤4% because real S/R levels often sit 1-3% out.
        _wall_max = 4.0
        if bid_wall_pct is not None and bid_wall_pct <= _wall_max:
            long_score += max(0, 30.0 * (_wall_max - bid_wall_pct) / _wall_max + 15)
        # A close ask-void means thin liquidity above — price likely to jet through
        if ask_void_pct is not None and ask_void_pct <= _wall_max:
            long_score += max(0, 30.0 * (_wall_max - ask_void_pct) / _wall_max + 15)
        # Bid-heavy top-of-book adds 0..40
        imb5 = top_imb(5)
        if imb5 > 0.55:
            long_score += min(40.0, (imb5 - 0.5) * 200.0)
        # Symmetric SHORT
        if ask_wall_pct is not None and ask_wall_pct <= _wall_max:
            short_score += max(0, 30.0 * (_wall_max - ask_wall_pct) / _wall_max + 15)
        if bid_void_pct is not None and bid_void_pct <= _wall_max:
            short_score += max(0, 30.0 * (_wall_max - bid_void_pct) / _wall_max + 15)
        if imb5 < 0.45:
            short_score += min(40.0, (0.5 - imb5) * 200.0)
        # ── Leading flow signals (NEW 2026-04-27) ──
        # OFI lean (0..25 per side) — signed flow imbalance over last 1s, normalized
        # by current top-of-book size. Positive OFI = net bid-side aggression =
        # leading LONG signal. Fires BEFORE 3m candle prints / WT crosses.
        denom_qty = top_bid_q + top_ask_q
        if denom_qty > 0:
            ofi_norm = ofi_1s / denom_qty
            if ofi_norm > 0:
                long_score  += min(25.0, ofi_norm * 50.0)
            elif ofi_norm < 0:
                short_score += min(25.0, -ofi_norm * 50.0)
        # Surge (0..25 per side) — top-of-book qty spike vs 600s EMA = absorption
        # signal. Bid surge during sell-off = bottom forming; ask surge during
        # rally = top forming.
        if bid_surge > 1.5:
            long_score  += min(25.0, (bid_surge - 1.0) * 25.0)
        if ask_surge > 1.5:
            short_score += min(25.0, (ask_surge - 1.0) * 25.0)
        # Microprice lean (0..15 per side) — size-weighted equilibrium tilt vs mid.
        # Microprice > mid = bid is bigger AND price is leaning up via execution.
        mp_bps = (microprice - mid) / mid * 10000.0
        if mp_bps > 0:
            long_score  += min(15.0, mp_bps * 5.0)
        elif mp_bps < 0:
            short_score += min(15.0, -mp_bps * 5.0)

        return {
            "ob_ts_ms": self.last_event_ts_ms,
            "ob_mid": round(mid, 8),
            "ob_microprice": round(microprice, 8),
            "ob_spread_bps": round(spread_bps, 4),
            "ob_bid_levels": len(self.bids),
            "ob_ask_levels": len(self.asks),
            "ob_bid_ask_imb_5": round(top_imb(5), 4),
            "ob_bid_ask_imb_10": round(top_imb(10), 4),
            "ob_bid_ask_imb_20": round(top_imb(20), 4),
            "ob_top_bid_qty": round(top_bid_q, 6),
            "ob_top_ask_qty": round(top_ask_q, 6),
            "ob_ofi_1s": round(ofi_1s, 4),
            "ob_bid_surge_60s": round(bid_surge, 3),
            "ob_ask_surge_60s": round(ask_surge, 3),
            "ob_imb_5pct": round(imb_5pct, 4),
            "ob_imb_10pct": round(imb_10pct, 4),
            "ob_bid_wall_pct": None if bid_wall_pct is None else round(bid_wall_pct, 2),
            "ob_bid_wall_size": round(bid_wall_size, 2),
            "ob_ask_wall_pct": None if ask_wall_pct is None else round(ask_wall_pct, 2),
            "ob_ask_wall_size": round(ask_wall_size, 2),
            "ob_bid_void_pct": None if bid_void_pct is None else round(bid_void_pct, 2),
            "ob_ask_void_pct": None if ask_void_pct is None else round(ask_void_pct, 2),
            "ob_bid_vol_buckets": [round(v, 2) for v in bid_vol],
            "ob_ask_vol_buckets": [round(v, 2) for v in ask_vol],
            "ob_long_score": round(long_score, 2),
            "ob_short_score": round(short_score, 2),
        }


async def fetch_snapshot(session: aiohttp.ClientSession, symbol: str, limit: int = SNAPSHOT_LIMIT) -> Optional[dict]:
    try:
        async with session.get(REST_BASE, params={"symbol": symbol, "limit": limit}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning(f"snapshot {symbol} HTTP {resp.status}: {body[:200]}")
                return None
            return await resp.json()
    except Exception as e:
        logger.warning(f"snapshot {symbol} error: {type(e).__name__} {e}")
        return None


async def seed_book(session: aiohttp.ClientSession, book: DeepBook):
    # Retry with exponential backoff if IP-banned or rate-limited
    backoff = 5.0
    for attempt in range(6):
        snap = await fetch_snapshot(session, book.symbol, SNAPSHOT_LIMIT)
        if snap:
            book.apply_snapshot(snap)
            return True
        await asyncio.sleep(backoff)
        backoff = min(120.0, backoff * 2.0)
    logger.warning(f"seed_book gave up on {book.symbol}")
    return False


async def stream_chunk(symbols: List[str], books: Dict[str, DeepBook], stop: asyncio.Event):
    """One websocket connection carrying partial-book or diff streams per symbol."""
    streams = "/".join(f"{s.lower()}{STREAM_SUFFIX}" for s in symbols)
    url = f"{WS_BASE}?streams={streams}"
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10, max_size=50_000_000) as ws:
                logger.info(f"WS connected chunk={len(symbols)}syms first={symbols[0]} last={symbols[-1]}")
                backoff = 1.0
                while not stop.is_set():
                    try:
                        # 2026-04-26: was 45s — caused 19k+ reconnects on chunks containing low-volume pairs
                        # like 1000MOGUSDT. Real overnight idle is regularly 60-90s on these. Bumped to 120s.
                        raw = await asyncio.wait_for(ws.recv(), timeout=120.0)
                    except asyncio.TimeoutError:
                        logger.warning(f"WS idle 120s first={symbols[0]} — reconnecting")
                        break
                    try:
                        msg = orjson.loads(raw)
                    except Exception:
                        continue
                    data = msg.get("data")
                    if not data:
                        continue
                    sym = data.get("s")
                    if not sym:
                        continue
                    book = books.get(sym)
                    if book is None:
                        continue
                    book.apply_update(data)
        except Exception as e:
            if stop.is_set():
                break
            logger.warning(f"WS error chunk={symbols[0]}..: {type(e).__name__} {e} — reconnect in {backoff:.1f}s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(60.0, backoff * 2.0)


async def write_loop(books: Dict[str, DeepBook], redis: aioredis.Redis, get_symbols, stop: asyncio.Event):
    last_heartbeat = 0.0
    updates_written = 0
    cycle = 0
    while not stop.is_set():
        t0 = time.time()
        syms = get_symbols()
        wrote_this_cycle = 0
        for sym in syms:
            book = books.get(sym)
            if book is None:
                continue
            feat = book.compute_features()
            if feat is None:
                continue
            try:
                await redis.set(f"orderbook:{sym}", orjson.dumps(feat), ex=90)
                updates_written += 1
                wrote_this_cycle += 1
            except Exception as e:
                logger.debug(f"redis write {sym}: {e}")
        cycle += 1
        if t0 - last_heartbeat >= HEARTBEAT_SEC:
            seeded = sum(1 for s in syms if (books.get(s) and books[s].snapshot_applied))
            try:
                await redis.set("orderbook:_heartbeat", orjson.dumps({
                    "ts": int(t0), "seeded": seeded, "tracked": len(syms),
                    "writes_total": updates_written, "cycles": cycle,
                    "last_cycle_wrote": wrote_this_cycle,
                }), ex=120)
            except Exception:
                pass
            logger.info(f"heartbeat cycles={cycle} seeded={seeded}/{len(syms)} writes_cum={updates_written} last_cycle={wrote_this_cycle}")
            last_heartbeat = t0
        elapsed = time.time() - t0
        sleep = max(0.05, WRITE_INTERVAL_SEC - elapsed)
        try:
            await asyncio.wait_for(stop.wait(), timeout=sleep)
        except asyncio.TimeoutError:
            pass


async def symbol_reload_loop(get_active, on_change, stop: asyncio.Event):
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=RELOAD_SEC)
        except asyncio.TimeoutError:
            new_syms = load_symbols()
            cur = get_active()
            if set(new_syms) != set(cur):
                added = set(new_syms) - set(cur)
                removed = set(cur) - set(new_syms)
                logger.info(f"universe reload: {len(new_syms)} (+{len(added)} -{len(removed)})")
                await on_change(new_syms)


async def main():
    syms = load_symbols()
    if not syms:
        logger.error("no symbols from tradeable_keys.json — exiting")
        return
    logger.info(f"starting ez_orderbook DEEP: universe={len(syms)} first={syms[0]} last={syms[-1]}")

    books: Dict[str, DeepBook] = {s: DeepBook(s) for s in syms}
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
    # 2026-04-24: WebSocket-only mode. NEVER open HTTP session in partial-stream mode
    # to guarantee no possible REST call can ever be made by this service. User directive:
    # "eliminate api calls, websocket streaming only if not we will keep getting killed".
    http_session = None if USE_PARTIAL_STREAM else aiohttp.ClientSession()

    MAX_SYMS_PER_WS = 100    # be conservative — diff stream is chattier than partial

    def spawn_ws_for(symbols: List[str]):
        for t in ws_tasks:
            t.cancel()
        ws_tasks.clear()
        chunks = [symbols[i:i + MAX_SYMS_PER_WS] for i in range(0, len(symbols), MAX_SYMS_PER_WS)]
        for chunk in chunks:
            ws_tasks.append(asyncio.create_task(stream_chunk(chunk, books, stop)))
        logger.info(f"spawned {len(ws_tasks)} ws tasks over {len(symbols)} syms")

    async def seed_all(symbols: List[str]):
        # Stagger snapshot REST calls to avoid weight burst (-1003)
        for i, s in enumerate(symbols):
            if stop.is_set(): return
            if books[s].snapshot_applied: continue
            await seed_book(http_session, books[s])
            await asyncio.sleep(SNAPSHOT_STAGGER_MS / 1000.0)
        done = sum(1 for s in symbols if books[s].snapshot_applied)
        logger.info(f"initial snapshot seeding complete: {done}/{len(symbols)} seeded")

    async def on_universe_change(new_syms: List[str]):
        nonlocal active_syms
        # Create books for new symbols; keep old ones (maybe still in flight)
        for s in new_syms:
            if s not in books:
                books[s] = DeepBook(s)
        active_syms = list(new_syms)
        spawn_ws_for(active_syms)
        # Seed any new ones in background
        new_only = [s for s in new_syms if not books[s].snapshot_applied]
        if new_only:
            asyncio.create_task(seed_all(new_only))

    # Start WS first — partial-stream mode needs NO REST seeding.
    spawn_ws_for(active_syms)
    if not USE_PARTIAL_STREAM:
        # Diff-stream path needs a REST snapshot per symbol (weight 20 each).
        # CAUTION: ~27s stagger × 100 syms on /fapi/v1/depth hits -1003 unless
        # the IP has public-endpoint weight (Surfshark whitelist doesn't cover public).
        asyncio.create_task(seed_all(active_syms))
    else:
        logger.info(f"PARTIAL STREAM mode (@depth{PARTIAL_DEPTH}@100ms) — no REST seeding needed")

    writer = asyncio.create_task(write_loop(books, redis, lambda: list(active_syms), stop))
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
            if http_session is not None:
                await http_session.close()
        except Exception:
            pass
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
