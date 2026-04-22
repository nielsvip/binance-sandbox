"""
scalp_v3_paper.py — Forward-test V3 scalper in paper mode.

Polls Binance futures 1m klines every N seconds for inf-account symbols.
Computes K on 1m/3m/15m/1h/4h in memory. Invokes scalp_v3 entry/exit/reentry.
Logs hypothetical trades to data/scalp_v3_paper/trades_<date>.jsonl.

Does NOT place real orders. Does NOT touch live positions, Redis, or tradeable_keys.

Usage:
    python3 scalp_v3_paper.py                           # default: 1M_ONLY, all inf syms
    python3 scalp_v3_paper.py --tf-mode 1M_AND_3M
    python3 scalp_v3_paper.py --exit-mode 1M_ONLY
    python3 scalp_v3_paper.py --symbols BTCUSDT,ETHUSDT
    python3 scalp_v3_paper.py --poll-sec 60 --max-symbols 50
"""
from __future__ import annotations
import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from binance import AsyncClient
from config import Config
from scalp_v3 import (
    V3ExitState, V3Input, V3Position,
    check_scalp_v3_entry, check_scalp_v3_exit, check_scalp_v3_reentry_allowed,
)

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "data" / "scalp_v3_paper"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = OUT_DIR / "state.json"
TRADEABLE_FILE = BASE / "tradeable_keys.json"

STOCH_PERIOD = 14
FEE_PCT = 0.04  # round-trip


class CfgOverride:
    def __init__(self, base, **overrides):
        self._base = base
        self._overrides = overrides
    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)


def load_inf_universe(limit: int = 0) -> List[str]:
    """Read inf:SYM_SIDE keys from tradeable_keys.json. Dynamic — ez_rankings updates this file."""
    if not TRADEABLE_FILE.exists(): return []
    try:
        with open(TRADEABLE_FILE) as f:
            keys = json.load(f)
    except Exception:
        return []
    syms = sorted({k.split(":", 1)[1].rsplit("_", 1)[0] for k in keys if k.startswith("inf:")})
    return syms[:limit] if limit else syms


def today_trades_file() -> Path:
    return OUT_DIR / f"trades_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"


def log_event(event: dict):
    event["_ts_iso"] = datetime.now(timezone.utc).isoformat()
    with open(today_trades_file(), "a") as f:
        f.write(json.dumps(event) + "\n")


def resample(bars_1m: np.ndarray, tf_min: int) -> np.ndarray:
    if tf_min == 1: return bars_1m
    tf_sec = tf_min * 60
    bucket = (bars_1m[:, 0] // tf_sec).astype(np.int64)
    uniq, first_idx = np.unique(bucket, return_index=True)
    if len(uniq) < 2: return np.zeros((0, 6))
    out = np.zeros((len(uniq) - 1, 6), dtype=np.float64)
    for i in range(len(uniq) - 1):
        s = first_idx[i]; e = first_idx[i + 1]
        sub = bars_1m[s:e]
        out[i, 0] = uniq[i] * tf_sec
        out[i, 1] = sub[0, 1]
        out[i, 2] = sub[:, 2].max()
        out[i, 3] = sub[:, 3].min()
        out[i, 4] = sub[-1, 4]
        out[i, 5] = sub[:, 5].sum()
    return out


def stoch_k(bars: np.ndarray, period: int = STOCH_PERIOD) -> np.ndarray:
    n = len(bars)
    if n == 0: return np.array([])
    k = np.full(n, 50.0)
    for i in range(period - 1, n):
        hi = bars[i - period + 1 : i + 1, 2].max()
        lo = bars[i - period + 1 : i + 1, 3].min()
        rng = hi - lo
        if rng > 0:
            k[i] = (bars[i, 4] - lo) / rng * 100.0
    return k


def load_state() -> dict:
    if not STATE_FILE.exists(): return {"positions": {}, "exit_states": {}}
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except Exception:
        return {"positions": {}, "exit_states": {}}


def save_state(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f: json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)


async def fetch_1m(client, symbol: str, limit: int = 60) -> Optional[np.ndarray]:
    try:
        raw = await client.futures_klines(symbol=symbol, interval="1m", limit=limit)
    except Exception as e:
        msg = str(e)
        if "-1003" in msg or "banned" in msg.lower():
            # Silently skip banned periods — live ez_manage may have hit the limit
            return None
        print(f"[fetch] {symbol}: {e}")
        return None
    # Polite delay to reduce ban risk when running alongside live ez_manage
    await asyncio.sleep(0.15)
    if not raw or len(raw) < 30: return None
    out = np.zeros((len(raw), 6), dtype=np.float64)
    for i, b in enumerate(raw):
        out[i, 0] = int(b[0]) / 1000.0
        out[i, 1] = float(b[1]); out[i, 2] = float(b[2]); out[i, 3] = float(b[3])
        out[i, 4] = float(b[4]); out[i, 5] = float(b[5])
    return out[:-1]  # drop last (incomplete)


def evaluate_symbol(symbol: str, bars_1m: np.ndarray, state: dict, cfg) -> List[dict]:
    events = []
    bars_3m = resample(bars_1m, 3)
    bars_15m = resample(bars_1m, 15)
    bars_1h = resample(bars_1m, 60)
    bars_4h = resample(bars_1m, 240)
    if len(bars_3m) < 3 or len(bars_15m) < 3: return events
    k_1m = stoch_k(bars_1m)
    k_3m = stoch_k(bars_3m)
    k_15m = stoch_k(bars_15m)
    k_1h = stoch_k(bars_1h) if len(bars_1h) >= STOCH_PERIOD else np.full(len(bars_1h), 50.0)
    k_4h = stoch_k(bars_4h) if len(bars_4h) >= STOCH_PERIOD else np.full(len(bars_4h), 50.0)
    now_ts = float(bars_1m[-1, 0])
    price = float(bars_1m[-1, 4])
    bars_1m_view = [tuple(b) for b in bars_1m[-22:]]
    bars_3m_view = [tuple(b) for b in bars_3m[-6:]]
    bars_15m_view = [tuple(b) for b in bars_15m[-4:]]
    for side in ("LONG", "SHORT"):
        key = f"{symbol}_{side}"
        pos_data = state["positions"].get(key)
        exit_state_data = state["exit_states"].get(key)
        inp = V3Input(
            symbol=symbol, side=side,
            bars_1m=bars_1m_view, bars_3m=bars_3m_view, bars_15m=bars_15m_view,
            k_1m=float(k_1m[-1]), k_1m_prev=float(k_1m[-2]),
            k_3m=float(k_3m[-1]), k_3m_prev=float(k_3m[-2]),
            k_15m=float(k_15m[-1]), k_15m_prev=float(k_15m[-2]),
            k_15m_prev2=float(k_15m[-3]) if len(k_15m) >= 3 else 50.0,
            k_1h=float(k_1h[-1]) if len(k_1h) else 50.0,
            k_4h=float(k_4h[-1]) if len(k_4h) else 50.0,
            now_ts=now_ts, current_price=price,
        )
        if pos_data:
            pos = V3Position(**pos_data)
            ok, reason = check_scalp_v3_exit(pos, inp, cfg)
            if ok:
                gain = (price - pos.entry_price) / pos.entry_price * 100.0
                if pos.side == "SHORT": gain = -gain
                gain -= FEE_PCT
                events.append({
                    "type": "PAPER_EXIT", "symbol": symbol, "side": side,
                    "entry_price": pos.entry_price, "exit_price": price,
                    "entry_ts": pos.entry_ts, "exit_ts": now_ts,
                    "hold_min": round((now_ts - pos.entry_ts) / 60.0, 2),
                    "gain_pct": round(gain, 4), "reason": reason,
                })
                del state["positions"][key]
                state["exit_states"][key] = asdict_lite(V3ExitState(
                    last_exit_ts=now_ts, k_15m_at_exit=inp.k_15m, k_15m_prev_at_exit=inp.k_15m_prev))
        else:
            exit_state = V3ExitState(**exit_state_data) if exit_state_data else None
            if exit_state is not None:
                allowed, re_reason = check_scalp_v3_reentry_allowed(exit_state, inp, cfg)
                if not allowed:
                    continue
            ok, reason = check_scalp_v3_entry(inp, cfg)
            if ok:
                events.append({
                    "type": "PAPER_ENTRY", "symbol": symbol, "side": side,
                    "entry_price": price, "entry_ts": now_ts,
                    "k_1m": round(inp.k_1m, 1), "k_3m": round(inp.k_3m, 1),
                    "k_15m": round(inp.k_15m, 1), "k_1h": round(inp.k_1h, 1), "k_4h": round(inp.k_4h, 1),
                    "reason": reason,
                })
                state["positions"][key] = {
                    "side": side, "entry_price": price, "entry_ts": now_ts,
                    "entry_k_15m": float(inp.k_15m),
                }
    return events


def asdict_lite(obj) -> dict:
    return {k: getattr(obj, k) for k in obj.__dataclass_fields__}


async def poll_cycle(client, symbols: List[str], state: dict, cfg, concurrency: int = 10) -> List[dict]:
    sem = asyncio.Semaphore(concurrency)
    all_events: List[dict] = []
    async def one(sym):
        async with sem:
            bars = await fetch_1m(client, sym, limit=60)
            if bars is None: return
            try:
                evs = evaluate_symbol(sym, bars, state, cfg)
            except Exception as e:
                print(f"[eval] {sym}: {e}")
                return
            all_events.extend(evs)
    await asyncio.gather(*(one(s) for s in symbols))
    return all_events


def print_summary(state: dict, counters: dict):
    open_longs = sum(1 for k in state["positions"] if k.endswith("_LONG"))
    open_shorts = sum(1 for k in state["positions"] if k.endswith("_SHORT"))
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
          f"cycle#{counters['cycles']} entries={counters['entries']} exits={counters['exits']} "
          f"open=L{open_longs}/S{open_shorts} realized_pct={counters['realized_pct']:+.2f}%")


async def main_async(args):
    base_cfg = Config()
    # Apply sweep-winning defaults as CfgOverride — beats the base config defaults for V3.
    cfg = CfgOverride(base_cfg,
                      SCALP_V3_ENTRY_TF_MODE=args.tf_mode,
                      SCALP_V3_EXIT_TF_MODE=args.exit_mode,
                      SCALP_V3_ENTRY_K_1M_MAX=args.k_1m_max,
                      SCALP_V3_EXIT_1M_K_MIN=args.exit_k_1m_min,
                      SCALP_V3_ENTRY_VOL_SPIKE_MULT=args.vol_mult,
                      SCALP_V3_MAX_HOLD_MIN=args.max_hold_min,
                      SCALP_V3_STALL_GAIN_MAX_PCT=args.stall_gain)
    symbols_fixed = args.symbols.strip()
    if symbols_fixed:
        symbols = [s.strip().upper() for s in symbols_fixed.split(",") if s.strip()]
        dynamic_universe = False
    else:
        symbols = load_inf_universe(limit=args.max_symbols)
        dynamic_universe = True
    if not symbols:
        print("No symbols — check tradeable_keys.json for inf entries.")
        return
    print(f"[paper] universe={len(symbols)} tf_mode={args.tf_mode} exit_mode={args.exit_mode} poll={args.poll_sec}s dynamic={dynamic_universe}")
    print(f"[paper] sweep_winner_params: k1m_max={args.k_1m_max} ek1m={args.exit_k_1m_min} vol={args.vol_mult} hold={args.max_hold_min}min stall={args.stall_gain}")
    print(f"[paper] first 5: {symbols[:5]}")
    state = load_state()
    counters = {"cycles": 0, "entries": 0, "exits": 0, "realized_pct": 0.0}
    stop = False
    def _sig(*_): nonlocal_stop()
    def nonlocal_stop():
        nonlocal stop; stop = True
    for s in (signal.SIGINT, signal.SIGTERM):
        signal.signal(s, _sig)
    client = await AsyncClient.create()
    last_universe_reload = time.time()
    try:
        while not stop:
            t0 = time.time()
            if dynamic_universe and (t0 - last_universe_reload) >= args.reload_sec:
                new_syms = load_inf_universe(limit=args.max_symbols)
                if new_syms and set(new_syms) != set(symbols):
                    added = set(new_syms) - set(symbols)
                    removed = set(symbols) - set(new_syms)
                    symbols = new_syms
                    print(f"[paper] universe reload: {len(symbols)} syms (+{len(added)} -{len(removed)})")
                    if added: print(f"  +added: {sorted(added)[:10]}")
                    if removed: print(f"  -removed: {sorted(removed)[:10]}")
                last_universe_reload = t0
            try:
                events = await poll_cycle(client, symbols, state, cfg, concurrency=args.concurrency)
            except Exception as e:
                print(f"[cycle] error: {e}")
                events = []
            for ev in events:
                log_event(ev)
                if ev["type"] == "PAPER_ENTRY":
                    counters["entries"] += 1
                    print(f"  ENTRY {ev['side']:5s} {ev['symbol']:14s} @ {ev['entry_price']:.6g}  k1m={ev['k_1m']} k3m={ev['k_3m']} k15={ev['k_15m']}")
                else:
                    counters["exits"] += 1
                    counters["realized_pct"] += ev["gain_pct"]
                    print(f"  EXIT  {ev['side']:5s} {ev['symbol']:14s} @ {ev['exit_price']:.6g}  gain={ev['gain_pct']:+.3f}% hold={ev['hold_min']}m  [{ev['reason']}]")
            save_state(state)
            counters["cycles"] += 1
            if counters["cycles"] % 5 == 0:
                print_summary(state, counters)
            elapsed = time.time() - t0
            sleep_for = max(5.0, args.poll_sec - elapsed)
            for _ in range(int(sleep_for)):
                if stop: break
                await asyncio.sleep(1)
    finally:
        await client.close_connection()
        save_state(state)
        print_summary(state, counters)
        print("[paper] stopped.")


def main():
    ap = argparse.ArgumentParser()
    # Defaults = 200k-variant sweep winners (2026-04-22):
    #   tf=1M_ONLY exit=15M_ONLY vol=1.0 k1m=40 ek1m=90 hold=30 stall=-0.1
    # Side kept BOTH (user directive: don't discard shorts even though sweep window was a rally).
    ap.add_argument("--tf-mode", type=str, default="1M_ONLY", choices=["1M_ONLY", "3M_ONLY", "1M_AND_3M", "3M_CONFIRMS_1M"])
    ap.add_argument("--exit-mode", type=str, default="15M_ONLY", choices=["ANY", "1M_ONLY", "3M_ONLY", "15M_ONLY"])
    ap.add_argument("--vol-mult", type=float, default=1.0)
    ap.add_argument("--k-1m-max", type=int, default=40)
    ap.add_argument("--exit-k-1m-min", type=int, default=90)
    ap.add_argument("--max-hold-min", type=float, default=30.0)
    ap.add_argument("--stall-gain", type=float, default=-0.1)
    ap.add_argument("--symbols", type=str, default="", help="Fixed comma list (disables dynamic reload)")
    ap.add_argument("--max-symbols", type=int, default=0)
    ap.add_argument("--poll-sec", type=int, default=120, help="Poll frequency (default 120s to stay under Binance weight)")
    ap.add_argument("--reload-sec", type=int, default=300, help="Reload tradeable_keys.json every N sec (0 disables)")
    ap.add_argument("--concurrency", type=int, default=3, help="Concurrent klines fetches (default 3 to avoid bans — live ez_manage already hits Binance)")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
