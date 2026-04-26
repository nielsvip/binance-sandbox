"""paper_forward_runner.py — 2-week+ paper-only forward test.

Runs 3 arms concurrently in one asyncio process:
  A) full symbols.json universe (~195 syms) — real V3 logic via scalp_v3_live
  B) symbols_inf_long ∪ symbols_inf_short subset — real V3 logic via scalp_v3_live
  C) full Binance perp universe (~400 syms) — primitive Donchian-20 from in-memory 1m candles

Why this design:
  - Arms A and B read the same Redis key `latest_market_data` (the full indicator dict
    that ez_indicators publishes ~once per second). Same indicators, same V3 logic, only
    the eligible-symbol set differs. Apples-to-apples test of whether the symbols_inf_*
    filter is keeping winners or just dropping noise.
  - Arm C subscribes to Binance WS `!miniTicker@arr` (free, no auth, all symbols in one
    stream). Builds 1m candles in-memory. After 20-bar warmup runs Donchian-20 breakout.
    Tests whether the curated symbols.json universe is doing anything vs raw market.

Crash safety: each arm snapshots state every 60s. On restart, open positions reload
and continue MTM. Arm C candle history is lost on restart (acceptable; ~20min warmup).

ZERO touch to live system. No `execute_now` path. No real orders. Read-only on Redis.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import orjson
import redis.asyncio as aioredis

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from scalp_v3_live import check_scalp_v3_live_entry, check_scalp_v3_live_exit
import config as live_config

CFG = live_config.Config()

DATA_DIR = BASE_PATH / "data" / "paper_forward"
LOG_DIR = BASE_PATH / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

POSITION_USD = 10.0
MAX_CONCURRENT = 25
PAPER_FEES_PER_SIDE = 0.0004
STARTING_EQUITY = 1000.0
ENTRY_LOOP_SEC = 10.0
EXIT_LOOP_SEC = 5.0
EQUITY_LOG_SEC = 60.0
STATE_SNAPSHOT_SEC = 60.0
HEARTBEAT_SEC = 30.0

# V3 stall override for arms A/B: ride out until V3's technical exits fire
# (k>95 + bar reversal = WT/DC slowdown at top). 24h backstop prevents zombies.
V3_PAPER_MAX_HOLD_MIN = 1440.0
V3_PAPER_STALL_GAIN_MAX_PCT = -5.0

# Hedge-promotion (PnD protection) — arms A/B only. EXPLICITLY FORBIDDEN in live
# accounts. Logic: if V3 would fire opposite side on an underwater position, open
# the hedge; once hedge gains >= HEDGE_PROMOTION_GAIN_PCT, close the original at
# its realized loss and promote the hedge to primary.
PAPER_HEDGE_PROMOTION_ENABLED = True
HEDGE_TRIGGER_LOSS_PCT = -0.5
HEDGE_PROMOTION_GAIN_PCT = 0.2

ARM_C_CANDLE_BARS = 60
ARM_C_DC_LOOKBACK = 20
ARM_C_EXIT_STRUCTURE_BARS = 5  # close on N-bar high/low break — "structural exit"
ARM_C_MIN_24H_QUOTE_VOL = 1_000_000.0
ARM_C_MAX_HOLD_MIN = 1440.0  # 24h zombie backstop only
ARM_C_TICK_BUF_FLUSH_SEC = 1.0

LATEST_MARKET_DATA_KEY = "latest_market_data"
SYMBOLS_FILE = BASE_PATH / "symbols.json"
SYMBOLS_INF_LONG_FILE = BASE_PATH / "symbols_inf_long.json"
SYMBOLS_INF_SHORT_FILE = BASE_PATH / "symbols_inf_short.json"

SIDE_MODE = os.environ.get("PAPER_FWD_SIDE_MODE", "LONG_ONLY").upper()


def _ts() -> float:
    return time.time()


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log(msg: str) -> None:
    line = f"{_utc_iso()}  {msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    try:
        with (LOG_DIR / "paper_forward.log").open("a") as f:
            f.write(line)
    except Exception:
        pass


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


class FakePosition:
    """Duck-typed stand-in for the live Position object that scalp_v3_live expects."""

    def __init__(self, symbol: str, side: str, entry_price: float, qty: float,
                 opened_at: float, augment_reason: str):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.positionAmt = qty if side == "LONG" else -qty
        self.opened_at = opened_at
        self.augment_reason = augment_reason
        self.gain = 0.0
        self.is_hedge = False


class PaperBook:
    """Per-arm paper trading book. JSONL trade log, atomic state snapshots."""

    def __init__(self, arm_id: str):
        self.arm_id = arm_id
        self.dir = DATA_DIR / f"arm_{arm_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.positions: Dict[str, FakePosition] = {}
        self.trades_file = self.dir / "trades.jsonl"
        self.equity_file = self.dir / "equity.jsonl"
        self.state_file = self.dir / "state.json"
        self.realized_pnl = 0.0
        self.n_opens = 0
        self.n_closes = 0
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            state = json.loads(self.state_file.read_text())
            self.realized_pnl = float(state.get("realized_pnl", 0.0))
            self.n_opens = int(state.get("n_opens", 0))
            self.n_closes = int(state.get("n_closes", 0))
            for pk, p in (state.get("positions") or {}).items():
                self.positions[pk] = FakePosition(
                    symbol=p["symbol"], side=p["side"],
                    entry_price=float(p["entry_price"]), qty=float(p["qty"]),
                    opened_at=float(p["opened_at"]),
                    augment_reason=p.get("augment_reason", "SCALP_V3_OPEN_LONG_RESUMED"),
                )
            _log(f"[{self.arm_id}] state restored: realized={self.realized_pnl:.2f} open={len(self.positions)}")
        except Exception as e:
            _log(f"[{self.arm_id}] state load error: {e}")

    def save_state(self) -> None:
        state = {
            "arm_id": self.arm_id,
            "ts": _ts(),
            "realized_pnl": self.realized_pnl,
            "n_opens": self.n_opens,
            "n_closes": self.n_closes,
            "positions": {
                pk: {"symbol": p.symbol, "side": p.side,
                     "entry_price": p.entry_price,
                     "qty": abs(p.positionAmt),
                     "opened_at": p.opened_at,
                     "augment_reason": p.augment_reason}
                for pk, p in self.positions.items()
            },
        }
        try:
            _atomic_write(self.state_file, orjson.dumps(state, option=orjson.OPT_INDENT_2))
        except Exception as e:
            _log(f"[{self.arm_id}] state save error: {e}")

    def can_open(self) -> bool:
        return len(self.positions) < MAX_CONCURRENT

    def has_position(self, symbol: str) -> bool:
        return f"{symbol}_LONG" in self.positions or f"{symbol}_SHORT" in self.positions

    def open(self, symbol: str, side: str, price: float, reason: str) -> bool:
        if price <= 0:
            return False
        pk = f"{symbol}_{side}"
        if pk in self.positions:
            return False
        if not self.can_open():
            return False
        qty = POSITION_USD / price
        self.positions[pk] = FakePosition(symbol, side, price, qty, _ts(), reason)
        self.n_opens += 1
        self._append_trade({
            "ts": _ts(), "iso": _utc_iso(), "arm": self.arm_id, "action": "OPEN",
            "symbol": symbol, "side": side, "price": price, "qty": qty,
            "reason": reason,
        })
        return True

    def close(self, position_key: str, price: float, reason: str) -> Optional[float]:
        if price <= 0 or position_key not in self.positions:
            return None
        pos = self.positions.pop(position_key)
        gross_pct = ((price - pos.entry_price) / pos.entry_price * 100.0
                     if pos.side == "LONG"
                     else (pos.entry_price - price) / pos.entry_price * 100.0)
        fees_pct = PAPER_FEES_PER_SIDE * 100.0 * 2.0
        net_pct = gross_pct - fees_pct
        pnl_usd = POSITION_USD * (net_pct / 100.0)
        self.realized_pnl += pnl_usd
        self.n_closes += 1
        self._append_trade({
            "ts": _ts(), "iso": _utc_iso(), "arm": self.arm_id, "action": "CLOSE",
            "symbol": pos.symbol, "side": pos.side,
            "entry_price": pos.entry_price, "exit_price": price,
            "qty": abs(pos.positionAmt),
            "gross_pct": gross_pct, "fees_pct": fees_pct, "net_pct": net_pct,
            "pnl_usd": pnl_usd,
            "age_sec": _ts() - pos.opened_at,
            "reason": reason,
        })
        return net_pct

    def mtm_equity(self, price_lookup) -> float:
        unrealized = 0.0
        for _pk, p in self.positions.items():
            mp = price_lookup(p.symbol)
            if mp is None or mp <= 0:
                continue
            gp = ((mp - p.entry_price) / p.entry_price * 100.0
                  if p.side == "LONG"
                  else (p.entry_price - mp) / p.entry_price * 100.0)
            unrealized += POSITION_USD * (gp / 100.0)
        return STARTING_EQUITY + self.realized_pnl + unrealized

    def log_equity(self, price_lookup) -> None:
        rec = {
            "ts": _ts(), "iso": _utc_iso(), "arm": self.arm_id,
            "equity": self.mtm_equity(price_lookup),
            "realized_pnl": self.realized_pnl,
            "n_open": len(self.positions),
            "n_opens_total": self.n_opens,
            "n_closes_total": self.n_closes,
        }
        try:
            with self.equity_file.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:
            pass

    def _append_trade(self, rec: dict) -> None:
        try:
            with self.trades_file.open("a") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception as e:
            _log(f"[{self.arm_id}] trade log error: {e}")


class IndicatorSource:
    """Single shared reader for the `latest_market_data` Redis key. Caches the dict so
    arms A and B don't both incur the parse cost on the same tick."""

    def __init__(self, redis_client: aioredis.Redis):
        self.redis = redis_client
        self.dict: Dict[str, dict] = {}
        self.last_fetch_ts = 0.0
        self.last_ok_ts = 0.0
        self._lock = asyncio.Lock()

    async def refresh(self, max_age_sec: float = 5.0) -> Dict[str, dict]:
        async with self._lock:
            if (_ts() - self.last_fetch_ts) < max_age_sec and self.dict:
                return self.dict
            try:
                raw = await self.redis.get(LATEST_MARKET_DATA_KEY)
                if raw:
                    self.dict = orjson.loads(raw)
                    self.last_ok_ts = _ts()
                self.last_fetch_ts = _ts()
            except Exception as e:
                _log(f"[indicator_src] redis read error: {e}")
            return self.dict

    def price(self, symbol: str) -> Optional[float]:
        sd = self.dict.get(symbol)
        if not sd:
            return None
        try:
            p = float(sd.get("current_price", 0) or 0)
            return p if p > 0 else None
        except Exception:
            return None


class V3Arm:
    """Shared body for arms A and B — they only differ in eligible_symbols()."""

    def __init__(self, arm_id: str, indicator_src: IndicatorSource,
                 eligible_fn, side_mode: str = SIDE_MODE):
        self.arm_id = arm_id
        self.book = PaperBook(arm_id)
        self.indicator_src = indicator_src
        self._eligible_fn = eligible_fn
        self.side_mode = side_mode
        self._stop = asyncio.Event()
        self._patched_cfg = self._build_arm_config()

    def _build_arm_config(self):
        """Clone CFG and force SIDE_MODE for this arm without mutating the global."""
        class _Shim:
            pass
        s = _Shim()
        for attr in dir(CFG):
            if attr.startswith("_"):
                continue
            try:
                setattr(s, attr, getattr(CFG, attr))
            except Exception:
                pass
        s.SCALP_V3_SIDE_MODE = self.side_mode
        return s

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        _log(f"[{self.arm_id}] arm started side_mode={self.side_mode}")
        last_equity_log = 0.0
        last_state_save = 0.0
        while not self._stop.is_set():
            try:
                await self.indicator_src.refresh()
                await self._exit_pass()
                await self._entry_pass()
                if (_ts() - last_equity_log) >= EQUITY_LOG_SEC:
                    self.book.log_equity(self.indicator_src.price)
                    last_equity_log = _ts()
                if (_ts() - last_state_save) >= STATE_SNAPSHOT_SEC:
                    self.book.save_state()
                    last_state_save = _ts()
            except Exception as e:
                _log(f"[{self.arm_id}] loop error: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=EXIT_LOOP_SEC)
            except asyncio.TimeoutError:
                pass
        self.book.save_state()
        _log(f"[{self.arm_id}] arm stopped")

    async def _exit_pass(self) -> None:
        for pk in list(self.book.positions.keys()):
            pos = self.book.positions.get(pk)
            if not pos:
                continue
            ind = self.indicator_src.dict.get(pos.symbol)
            if not ind:
                continue
            price = self.indicator_src.price(pos.symbol)
            if price is None:
                continue
            try:
                decision = check_scalp_v3_live_exit(pk, ind, price, pos, self._patched_cfg)
            except Exception as e:
                _log(f"[{self.arm_id}] exit check err {pk}: {e}")
                continue
            if decision and decision.get("reason"):
                self.book.close(pk, price, decision["reason"])

    async def _entry_pass(self) -> None:
        if not self.book.can_open():
            return
        eligible = self._eligible_fn()
        avail = self.indicator_src.dict
        for sym in eligible:
            if not self.book.can_open():
                break
            if self.book.has_position(sym):
                continue
            ind = avail.get(sym)
            if not ind:
                continue
            price = self.indicator_src.price(sym)
            if price is None:
                continue
            try:
                decision = check_scalp_v3_live_entry(
                    sym, f"{sym}_LONG", ind, price, None, "inf", self._patched_cfg
                )
            except Exception as e:
                _log(f"[{self.arm_id}] entry check err {sym}: {e}")
                continue
            if not decision:
                continue
            side = decision.get("side")
            reason = decision.get("reason", "SCALP_V3_OPEN_UNK")
            if side not in ("LONG", "SHORT"):
                continue
            self.book.open(sym, side, price, reason)


def _eligible_arm_a(indicator_src: IndicatorSource, symbols_json_set):
    def _fn():
        return [s for s in symbols_json_set if s in indicator_src.dict]
    return _fn


def _eligible_arm_b(indicator_src: IndicatorSource):
    cache = {"ts": 0.0, "set": set()}

    def _fn():
        if (_ts() - cache["ts"]) > 30.0:
            longs = _load_json(SYMBOLS_INF_LONG_FILE, [])
            shorts = _load_json(SYMBOLS_INF_SHORT_FILE, [])
            cache["set"] = set(longs) | set(shorts)
            cache["ts"] = _ts()
        return [s for s in cache["set"] if s in indicator_src.dict]
    return _fn


class ArmC_Universe:
    """Independent arm — full Binance perp universe via WS, primitive Donchian-20."""

    WS_URL = "wss://fstream.binance.com/ws/!miniTicker@arr"

    def __init__(self):
        self.book = PaperBook("C")
        self.candles: Dict[str, deque] = {}
        self.cur_bar: Dict[str, dict] = {}
        self.last_price: Dict[str, float] = {}
        self.symbol_quote_vol: Dict[str, float] = {}
        self._stop = asyncio.Event()

    async def stop(self) -> None:
        self._stop.set()

    def _get_dq(self, sym: str) -> deque:
        d = self.candles.get(sym)
        if d is None:
            d = deque(maxlen=ARM_C_CANDLE_BARS)
            self.candles[sym] = d
        return d

    def _ingest_tick(self, sym: str, close: float, high: float, low: float,
                     vol: float, quote_vol: float, evt_ms: int) -> None:
        if close <= 0:
            return
        self.last_price[sym] = close
        self.symbol_quote_vol[sym] = quote_vol
        bar_min_idx = evt_ms // 60000
        cb = self.cur_bar.get(sym)
        if cb is None or cb["bar"] != bar_min_idx:
            if cb is not None:
                dq = self._get_dq(sym)
                dq.append({"o": cb["o"], "h": cb["h"], "l": cb["l"], "c": cb["c"], "v": cb["v"]})
            self.cur_bar[sym] = {
                "bar": bar_min_idx, "o": close, "h": high, "l": low, "c": close, "v": vol,
            }
        else:
            cb["c"] = close
            cb["h"] = max(cb["h"], high)
            cb["l"] = min(cb["l"], low) if cb["l"] > 0 else low
            cb["v"] = vol

    def _check_strategy(self, sym: str) -> Tuple[Optional[str], str]:
        dq = self.candles.get(sym)
        if not dq or len(dq) < ARM_C_DC_LOOKBACK:
            return None, ""
        if self.symbol_quote_vol.get(sym, 0.0) < ARM_C_MIN_24H_QUOTE_VOL:
            return None, ""
        last_close = self.last_price.get(sym, 0.0)
        if last_close <= 0:
            return None, ""
        bars = list(dq)[-ARM_C_DC_LOOKBACK:]
        prior_high = max(b["h"] for b in bars[:-1]) if len(bars) > 1 else bars[-1]["h"]
        prior_low = min(b["l"] for b in bars[:-1]) if len(bars) > 1 else bars[-1]["l"]
        if SIDE_MODE in ("LONG_ONLY", "BOTH") and last_close > prior_high * 1.0002:
            return "LONG", f"DC20_BREAKOUT_HIGH={prior_high:.6g}"
        if SIDE_MODE in ("SHORT_ONLY", "BOTH") and last_close < prior_low * 0.9998:
            return "SHORT", f"DC20_BREAKDOWN_LOW={prior_low:.6g}"
        return None, ""

    def _check_exit(self, pk: str) -> Tuple[bool, str]:
        pos = self.book.positions.get(pk)
        if not pos:
            return False, ""
        last = self.last_price.get(pos.symbol, 0.0)
        if last <= 0:
            return False, ""
        age_min = (_ts() - pos.opened_at) / 60.0
        if age_min >= ARM_C_MAX_HOLD_MIN:
            return True, f"DC20_TIME_STOP_age{age_min:.1f}m"
        dq = self.candles.get(pos.symbol)
        if not dq or len(dq) < ARM_C_DC_LOOKBACK:
            return False, ""
        bars = list(dq)[-ARM_C_DC_LOOKBACK:]
        if pos.side == "LONG":
            prior_low = min(b["l"] for b in bars[:-1]) if len(bars) > 1 else bars[-1]["l"]
            if last < prior_low:
                return True, f"DC20_EXIT_LONG_break_low={prior_low:.6g}"
        else:
            prior_high = max(b["h"] for b in bars[:-1]) if len(bars) > 1 else bars[-1]["h"]
            if last > prior_high:
                return True, f"DC20_EXIT_SHORT_break_high={prior_high:.6g}"
        return False, ""

    def _process_strategy_pass(self) -> None:
        # Exits first.
        for pk in list(self.book.positions.keys()):
            should, reason = self._check_exit(pk)
            if should:
                pos = self.book.positions.get(pk)
                if pos:
                    px = self.last_price.get(pos.symbol, 0.0)
                    self.book.close(pk, px, reason)
        # Entries.
        if not self.book.can_open():
            return
        candidates = []
        for sym in list(self.candles.keys()):
            if not self.book.can_open():
                break
            if self.book.has_position(sym):
                continue
            side, reason = self._check_strategy(sym)
            if side is None:
                continue
            candidates.append((sym, side, reason, self.symbol_quote_vol.get(sym, 0.0)))
        # Pick highest-volume movers first if cap is tight.
        candidates.sort(key=lambda x: x[3], reverse=True)
        for sym, side, reason, _qv in candidates:
            if not self.book.can_open():
                break
            px = self.last_price.get(sym, 0.0)
            if px > 0:
                self.book.open(sym, side, px, "DC20_OPEN_" + side + "_" + reason)

    def price_lookup(self, symbol: str) -> Optional[float]:
        p = self.last_price.get(symbol)
        return p if p and p > 0 else None

    async def run(self) -> None:
        _log("[C] arm started — connecting to Binance WS")
        last_equity_log = 0.0
        last_state_save = 0.0
        last_strategy_pass = 0.0
        while not self._stop.is_set():
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.ws_connect(self.WS_URL, heartbeat=15, autoping=True,
                                                max_msg_size=16 * 1024 * 1024) as ws:
                        _log("[C] WS connected")
                        async for msg in ws:
                            if self._stop.is_set():
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = orjson.loads(msg.data)
                                except Exception:
                                    continue
                                payloads = data if isinstance(data, list) else data.get("data", [])
                                for p in payloads or []:
                                    sym = p.get("s")
                                    if not sym:
                                        continue
                                    if not (sym.endswith("USDT") or sym.endswith("USDC")):
                                        continue
                                    try:
                                        close = float(p.get("c", 0) or 0)
                                        high = float(p.get("h", 0) or 0)
                                        low = float(p.get("l", 0) or 0)
                                        vol = float(p.get("v", 0) or 0)
                                        qv = float(p.get("q", 0) or 0)
                                        evt_ms = int(p.get("E", 0) or 0)
                                    except Exception:
                                        continue
                                    self._ingest_tick(sym, close, high, low, vol, qv, evt_ms)
                                if (_ts() - last_strategy_pass) >= ENTRY_LOOP_SEC:
                                    self._process_strategy_pass()
                                    last_strategy_pass = _ts()
                                if (_ts() - last_equity_log) >= EQUITY_LOG_SEC:
                                    self.book.log_equity(self.price_lookup)
                                    last_equity_log = _ts()
                                if (_ts() - last_state_save) >= STATE_SNAPSHOT_SEC:
                                    self.book.save_state()
                                    last_state_save = _ts()
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                _log("[C] WS closed/error — reconnecting")
                                break
            except Exception as e:
                _log(f"[C] WS exception: {e} — backoff 5s")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
        self.book.save_state()
        _log("[C] arm stopped")


async def heartbeat_loop(arms: list, indicator_src: IndicatorSource):
    hb_file = DATA_DIR / "HEARTBEAT.json"
    while True:
        rec = {
            "ts": _ts(),
            "iso": _utc_iso(),
            "indicator_last_ok_age_sec": (_ts() - indicator_src.last_ok_ts) if indicator_src.last_ok_ts else None,
            "indicator_n_syms": len(indicator_src.dict),
            "arms": [
                {"arm": a.book.arm_id, "n_open": len(a.book.positions),
                 "n_opens_total": a.book.n_opens, "n_closes_total": a.book.n_closes,
                 "realized_pnl": a.book.realized_pnl,
                 "equity": a.book.mtm_equity(
                     indicator_src.price if a.book.arm_id != "C" else (
                         arms[-1].price_lookup if isinstance(arms[-1], ArmC_Universe) else (lambda s: None)
                     )
                 )}
                for a in arms
            ],
            "side_mode": SIDE_MODE,
        }
        try:
            _atomic_write(hb_file, orjson.dumps(rec, option=orjson.OPT_INDENT_2))
        except Exception:
            pass
        try:
            await asyncio.sleep(HEARTBEAT_SEC)
        except asyncio.CancelledError:
            return


async def main() -> None:
    _log("=" * 78)
    _log(f"paper_forward_runner starting | side_mode={SIDE_MODE} | pos_usd={POSITION_USD} max={MAX_CONCURRENT}")
    _log(f"data_dir={DATA_DIR}")

    redis_client = aioredis.from_url("redis://localhost:6379", decode_responses=False,
                                      socket_connect_timeout=5, socket_timeout=5)
    try:
        await redis_client.ping()
        _log("redis ping OK")
    except Exception as e:
        _log(f"FATAL: redis unreachable: {e}")
        sys.exit(2)

    indicator_src = IndicatorSource(redis_client)
    await indicator_src.refresh()
    if not indicator_src.dict:
        _log("WARN: latest_market_data Redis key empty on startup — arms A/B will idle until ez_indicators publishes")
    else:
        _log(f"latest_market_data has {len(indicator_src.dict)} symbols on startup")

    symbols_json_list = _load_json(SYMBOLS_FILE, [])
    if not symbols_json_list:
        _log(f"FATAL: {SYMBOLS_FILE} empty/unreadable")
        sys.exit(3)
    symbols_json_set = set(symbols_json_list)
    _log(f"symbols.json has {len(symbols_json_set)} curated symbols")

    arm_a = V3Arm("A", indicator_src, _eligible_arm_a(indicator_src, symbols_json_set))
    arm_b = V3Arm("B", indicator_src, _eligible_arm_b(indicator_src))
    arm_c = ArmC_Universe()
    arms = [arm_a, arm_b, arm_c]

    stop_event = asyncio.Event()

    def _handle_signal():
        _log("signal received → stopping arms")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(arm_a.run(), name="arm_A"),
        asyncio.create_task(arm_b.run(), name="arm_B"),
        asyncio.create_task(arm_c.run(), name="arm_C"),
        asyncio.create_task(heartbeat_loop(arms, indicator_src), name="heartbeat"),
    ]

    await stop_event.wait()
    for arm in arms:
        await arm.stop()
    for t in tasks:
        try:
            await asyncio.wait_for(t, timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            t.cancel()

    try:
        await redis_client.aclose()
    except Exception:
        pass
    _log("paper_forward_runner stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
