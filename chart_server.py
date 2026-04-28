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


@app.route("/indicator")
def indicator():
    """Return time-series for one or more NPZ fields. ?sym=X&fields=a,b,c[&start=&end=&max=]"""
    sym = request.args.get("sym", "").upper()
    fields_raw = request.args.get("fields", "")
    fields = [f.strip() for f in fields_raw.split(",") if f.strip()]
    start = _ts_to_unix(request.args.get("start"))
    end = _ts_to_unix(request.args.get("end"))
    max_pts = int(request.args.get("max", 5000))
    z = _load_npz(sym)
    if z is None:
        return jsonify({"error": f"no NPZ for {sym}"}), 404
    ts_arr = z["timestamps"] if "timestamps" in z.files else None
    if ts_arr is None:
        return jsonify({"error": "no timestamps"}), 500
    n = len(ts_arr)
    lo = int(np.searchsorted(ts_arr, start)) if start is not None else 0
    hi = int(np.searchsorted(ts_arr, end)) if end is not None else n
    lo = max(lo, 0); hi = min(hi, n)
    if hi <= lo:
        return jsonify({"timestamps": [], "fields": {}})
    step = max(1, (hi - lo) // max_pts + 1)
    out_ts = ts_arr[lo:hi:step].tolist()
    out_fields: Dict[str, List[float]] = {}
    missing = []
    for f in fields:
        if f not in z.files:
            missing.append(f)
            continue
        arr = z[f]
        if len(arr) < n:
            # pad with zeros if shorter (rare but possible)
            arr = np.pad(arr, (0, n - len(arr)), constant_values=0)
        out_fields[f] = [float(x) if x == x else None for x in arr[lo:hi:step].tolist()]
    return jsonify({
        "timestamps": [int(t) for t in out_ts],
        "fields": out_fields,
        "missing": missing,
        "step": step,
    })


@app.route("/available_fields")
def available_fields():
    """List all NPZ fields available for a symbol."""
    sym = request.args.get("sym", "").upper()
    z = _load_npz(sym)
    if z is None:
        return jsonify({"error": f"no NPZ for {sym}"}), 404
    return jsonify(sorted(z.files))


SNAPSHOT_FIELDS = [
    # OHLC
    "open_3m", "high_3m", "low_3m", "close_3m",
    # Stoch K/D per TF
    "stoch_k_3m", "stoch_d_3m", "stoch_k_15m", "stoch_d_15m",
    "stoch_k_1h", "stoch_d_1h", "stoch_k_4h", "stoch_d_4h", "stoch_k_D", "stoch_d_D",
    # WaveTrend
    "wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h",
    "wt1_4h", "wt2_4h", "wt1_D", "wt2_D",
    "wt_velocity_3m", "wt_velocity_15m", "wt_velocity_1h", "wt_velocity_4h", "wt_velocity_D",
    "wt_acceleration_3m", "wt_acceleration_15m", "wt_acceleration_1h", "wt_acceleration_4h", "wt_acceleration_D",
    # Donchian
    "dc_high_3m", "dc_low_3m", "dc_high_15m", "dc_low_15m",
    "dc_high_1h", "dc_low_1h", "dc_high_4h", "dc_low_4h", "dc_high_D", "dc_low_D",
    # EMA / SMA
    "ema_200_3m", "ema_200_15m", "ema_200_1h", "ema_200_4h", "ema_200_D",
    "sma_200_4h", "sma_200_D",
    # Bollinger
    "bb_upper_15m", "bb_lower_15m", "bb_upper_1h", "bb_lower_1h",
    "bb_upper_4h", "bb_lower_4h", "bb_upper_D", "bb_lower_D",
    # ATR/ADX/MFI/RSI
    "atr_15m", "atr_1h", "atr_4h", "atr_D",
    "adx_1h", "adx_4h",
    "mfi_15m", "mfi_1h", "mfi_4h", "mfi_D",
    "rsi_15m", "rsi_1h", "rsi_4h", "rsi_D",
    # Heikin Ashi
    "ha_3m", "ha_15m", "ha_1h", "ha_4h", "ha_D",
    # Sentiment
    "funding_rate_3m", "oi_3m", "oi_change_15m_3m", "oi_change_1h_3m",
    "0market_sentiment_score", "0final_score_norm", "0sentiment_classification",
    # Squeeze / KC
    "squeeze_3m", "squeeze_15m", "squeeze_1h", "squeeze_4h",
    # Divergence flags
    "div_reg_bull_wt_15m", "div_reg_bear_wt_15m", "div_reg_bull_wt_1h", "div_reg_bear_wt_1h",
    "div_reg_bull_wt_4h", "div_reg_bear_wt_4h",
    "div_hid_bull_wt_15m", "div_hid_bear_wt_15m",
]


@app.route("/trade_snapshot")
def trade_snapshot():
    """Return indicator values at entry_ts and exit_ts for one trade.
    Query: ?sym=X&entry_ts=N&exit_ts=N[&fields=a,b,c]"""
    sym = request.args.get("sym", "").upper()
    entry_ts = _ts_to_unix(request.args.get("entry_ts"))
    exit_ts = _ts_to_unix(request.args.get("exit_ts"))
    fields_raw = request.args.get("fields", "")
    fields = [f.strip() for f in fields_raw.split(",") if f.strip()] if fields_raw else SNAPSHOT_FIELDS
    z = _load_npz(sym)
    if z is None:
        return jsonify({"error": f"no NPZ for {sym}"}), 404
    if entry_ts is None or exit_ts is None:
        return jsonify({"error": "entry_ts and exit_ts required"}), 400
    ts_arr = z["timestamps"] if "timestamps" in z.files else None
    if ts_arr is None:
        return jsonify({"error": "no timestamps"}), 500
    n = len(ts_arr)
    en_idx = int(np.searchsorted(ts_arr, entry_ts))
    ex_idx = int(np.searchsorted(ts_arr, exit_ts))
    en_idx = max(0, min(en_idx, n - 1))
    ex_idx = max(0, min(ex_idx, n - 1))
    out = {"entry": {"ts": int(ts_arr[en_idx]), "bar": en_idx, "values": {}},
           "exit":  {"ts": int(ts_arr[ex_idx]), "bar": ex_idx, "values": {}},
           "missing": []}
    for f in fields:
        if f not in z.files:
            out["missing"].append(f)
            continue
        arr = z[f]
        if len(arr) <= max(en_idx, ex_idx):
            out["missing"].append(f)
            continue
        try:
            ev = arr[en_idx]
            xv = arr[ex_idx]
            # Convert numpy scalars to native python (and handle non-finite)
            ev = ev.item() if hasattr(ev, "item") else ev
            xv = xv.item() if hasattr(xv, "item") else xv
            if isinstance(ev, float) and not np.isfinite(ev): ev = None
            if isinstance(xv, float) and not np.isfinite(xv): xv = None
            out["entry"]["values"][f] = ev
            out["exit"]["values"][f] = xv
        except Exception:
            out["missing"].append(f)
    return jsonify(out)


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
