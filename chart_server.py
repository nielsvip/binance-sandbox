#!/usr/bin/env python3
"""Tiny Flask server for visualizing backtest trades + historic live trades on charts.

Endpoints:
  GET /                           → chart.html
  GET /symbols                    → ["BTCUSDT", "ETHUSDT", ...]
  GET /runs                       → ["smoke_5sym_best", "iter18", ...]
  GET /klines?sym=X&tf=3m&start=ISO&end=ISO[&max=N] → OHLCV bars from NPZ
  GET /backtest_trades?run=Y&sym=X[&start=&end=]    → trade JSONL parsed
  GET /historic_trades?sym=X&accounts=inf,flz       → /data/history/<acct>/<sym>_<side>.jsonl
                                                       merged + per-account stats

All timestamps are unix seconds (int).
Trade recorder writes JSONL to V8_TRADES_OUT_DIR (default /tmp/v8_trades).
"""
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

BASE_PATH = Path(os.environ.get("BASE_PATH", "/Users/niels/Documents/binance"))
NPZ_DIR = BASE_PATH / "backtest_v8" / "indicators"
TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))
HISTORY_DIR = BASE_PATH / "data" / "history"
ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
TF_SECONDS = {"3m": 180, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}

app = Flask(__name__, static_folder=str(BASE_PATH / "chart_static"))

_npz_cache: Dict[str, Any] = {}


def _load_npz(symbol: str):
    if symbol in _npz_cache:
        return _npz_cache[symbol]
    path = NPZ_DIR / f"{symbol}.npz"
    if not path.exists():
        return None
    z = np.load(path, mmap_mode="r")
    _npz_cache[symbol] = z
    return z


def _ts_to_unix(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    try:
        if s.isdigit():
            return int(s)
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "chart.html")


@app.route("/symbols")
def symbols():
    syms = sorted(p.stem for p in NPZ_DIR.glob("*.npz"))
    return jsonify(syms)


@app.route("/runs")
def runs():
    if not TRADES_DIR.exists():
        return jsonify([])
    out = set()
    for p in TRADES_DIR.glob("*__*.jsonl"):
        run, _, _ = p.stem.partition("__")
        out.add(run)
    return jsonify(sorted(out))


@app.route("/klines")
def klines():
    sym = request.args.get("sym", "").upper()
    tf = request.args.get("tf", "3m")
    start = _ts_to_unix(request.args.get("start"))
    end = _ts_to_unix(request.args.get("end"))
    max_bars = int(request.args.get("max", 5000))
    z = _load_npz(sym)
    if z is None:
        return jsonify({"error": f"no NPZ for {sym}"}), 404
    ts_arr = z["timestamps"] if "timestamps" in z.files else None
    if ts_arr is None:
        return jsonify({"error": "no timestamps in npz"}), 500
    o = z[f"open_{tf}"] if f"open_{tf}" in z.files else None
    h = z[f"high_{tf}"] if f"high_{tf}" in z.files else None
    low = z[f"low_{tf}"] if f"low_{tf}" in z.files else None
    c = z[f"close_{tf}"] if f"close_{tf}" in z.files else None
    v = z[f"volume_{tf}"] if f"volume_{tf}" in z.files else None
    if o is None or h is None or low is None or c is None:
        return jsonify({"error": f"no OHLC for tf={tf}"}), 404
    # NPZ timestamps are at LTF (3m). For higher TFs we still emit at LTF granularity since
    # values were resampled to LTF when precomputed (each LTF bar carries the latest HTF close).
    # For better UX, we downsample timestamps when tf != 3m: keep one bar every N LTF bars.
    step = TF_SECONDS.get(tf, 180) // 180
    if step < 1:
        step = 1
    # Find slice indices
    n = len(ts_arr)
    lo = 0
    hi = n
    if start is not None:
        lo = int(np.searchsorted(ts_arr, start))
    if end is not None:
        hi = int(np.searchsorted(ts_arr, end))
    lo = max(lo, 0)
    hi = min(hi, n)
    if hi <= lo:
        return jsonify([])
    span = (hi - lo) // step
    if span > max_bars:
        step = max(step, (hi - lo) // max_bars + 1)
    out = []
    for i in range(lo, hi, step):
        if c[i] is None or float(c[i]) <= 0:
            continue
        vol = float(v[i]) if v is not None else 0.0
        out.append({
            "t": int(ts_arr[i]),
            "o": float(o[i]),
            "h": float(h[i]),
            "l": float(low[i]),
            "c": float(c[i]),
            "v": vol,
        })
    return jsonify(out)


@app.route("/backtest_trades")
def backtest_trades():
    run = request.args.get("run", "")
    sym = request.args.get("sym", "").upper()
    start = _ts_to_unix(request.args.get("start"))
    end = _ts_to_unix(request.args.get("end"))
    if not run or not sym:
        return jsonify({"error": "run and sym required"}), 400
    path = TRADES_DIR / f"{run}__{sym}.jsonl"
    if not path.exists():
        return jsonify({"trades": [], "stats": {}})
    trades = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        if start is not None and t.get("entry_ts", 0) < start:
            continue
        if end is not None and t.get("exit_ts", 0) > end:
            continue
        trades.append(t)
    return jsonify({"trades": trades, "stats": _trade_stats(trades)})


@app.route("/historic_trades")
def historic_trades():
    sym = request.args.get("sym", "").upper()
    accts_raw = request.args.get("accounts", ",".join(ACCOUNTS))
    accts = [a.strip() for a in accts_raw.split(",") if a.strip()]
    if not sym:
        return jsonify({"error": "sym required"}), 400
    out_per_acct: Dict[str, Any] = {}
    for acct in accts:
        long_path = HISTORY_DIR / acct / f"{sym}_LONG.jsonl"
        short_path = HISTORY_DIR / acct / f"{sym}_SHORT.jsonl"
        events: List[Dict[str, Any]] = []
        for p, side in ((long_path, "LONG"), (short_path, "SHORT")):
            if not p.exists():
                continue
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ev["side"] = side
                ev["account"] = acct
                ts_str = ev.get("ts")
                try:
                    ev["unix_ts"] = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
                except Exception:
                    ev["unix_ts"] = 0
                events.append(ev)
        events.sort(key=lambda e: e.get("unix_ts", 0))
        trades = _reconstruct_trades_from_events(events)
        out_per_acct[acct] = {
            "events": events,
            "trades": trades,
            "stats": _trade_stats(trades),
        }
    return jsonify(out_per_acct)


def _reconstruct_trades_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Walk OPEN/AUGMENT/REDUCE/CLOSE events into closed-trade rounds (per side).

    A "trade" round is from net qty=0 → qty>0 → qty=0. Tracks weighted-avg entry,
    exits give realized pnl_pct based on weighted entry vs exit price.
    """
    rounds_by_side: Dict[str, Dict[str, Any]] = {}
    closed: List[Dict[str, Any]] = []
    for ev in events:
        side = ev.get("side")
        kind = (ev.get("type") or "").upper()
        qty = float(ev.get("qty") or 0)
        price = float(ev.get("price") or 0)
        ts = ev.get("unix_ts", 0)
        reason = ev.get("reason", "")
        if not side or qty <= 0 or price <= 0:
            continue
        rd = rounds_by_side.get(side)
        if kind in ("OPEN", "AUGMENT"):
            if rd is None or rd.get("qty", 0) <= 0:
                rd = {
                    "side": side, "account": ev.get("account"),
                    "entry_ts": ts, "entry_price": price, "qty": qty,
                    "entry_reason": reason, "events": [ev],
                }
                rounds_by_side[side] = rd
            else:
                # weighted-avg entry
                new_qty = rd["qty"] + qty
                rd["entry_price"] = (rd["entry_price"] * rd["qty"] + price * qty) / new_qty
                rd["qty"] = new_qty
                rd["events"].append(ev)
        elif kind in ("REDUCE", "CLOSE"):
            if rd is None or rd.get("qty", 0) <= 0:
                continue
            close_qty = min(qty, rd["qty"])
            if side == "LONG":
                pnl_pct = (price - rd["entry_price"]) / rd["entry_price"] * 100.0
            else:
                pnl_pct = (rd["entry_price"] - price) / rd["entry_price"] * 100.0
            rd["events"].append(ev)
            rd["qty"] -= close_qty
            if rd["qty"] <= 1e-9:
                closed.append({
                    "account": rd["account"], "symbol": ev.get("symbol", ""), "side": side,
                    "entry_ts": rd["entry_ts"], "entry_price": rd["entry_price"],
                    "exit_ts": ts, "exit_price": price,
                    "entry_reason": rd["entry_reason"], "exit_reason": reason,
                    "pnl_pct": pnl_pct, "pnl_usd": (pnl_pct / 100.0) * (rd["entry_price"] * close_qty),
                    "duration_sec": ts - rd["entry_ts"],
                    "stream": "historic",
                })
                rounds_by_side[side] = None
    return closed


def _trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    total_gain = sum(pnls)
    avg = total_gain / n
    std = statistics.stdev(pnls) if n > 1 else 0.0
    sharpe_pt = (avg / std) if std > 0 else 0.0
    longs = [t for t in trades if t.get("side") == "LONG"]
    shorts = [t for t in trades if t.get("side") == "SHORT"]
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else 0.0,
        "total_gain_pct": total_gain,
        "avg_pnl_pct": avg,
        "std_pnl_pct": std,
        "pool_sharpe_per_trade": sharpe_pt,
        "best_pct": max(pnls),
        "worst_pct": min(pnls),
        "long_count": len(longs),
        "short_count": len(shorts),
        "long_total_gain_pct": sum(t.get("pnl_pct", 0) for t in longs),
        "short_total_gain_pct": sum(t.get("pnl_pct", 0) for t in shorts),
    }


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "npz_dir": str(NPZ_DIR),
        "trades_dir": str(TRADES_DIR),
        "history_dir": str(HISTORY_DIR),
        "npz_count": len(list(NPZ_DIR.glob("*.npz"))),
        "trade_files": len(list(TRADES_DIR.glob("*.jsonl"))) if TRADES_DIR.exists() else 0,
    })


if __name__ == "__main__":
    port = int(os.environ.get("CHART_PORT", 5077))
    print(f"Chart server on http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
