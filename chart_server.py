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


@app.route("/heatmap")
@app.route("/heatmap.html")
def heatmap_page():
    return send_from_directory(app.static_folder, "heatmap.html")


@app.route("/leaderboard")
@app.route("/leaderboard.html")
def leaderboard_page():
    return send_from_directory(app.static_folder, "leaderboard.html")


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


@app.route("/per_sym_best")
def per_sym_best():
    """Returns the current best run per symbol (highest churn-penalized score per-sym).
    Reflects what quality_optimizer.py --per-sym would auto-promote.
    """
    if not TRADES_DIR.exists():
        return jsonify([])
    by_sym: Dict[str, list] = {}
    for p in sorted(TRADES_DIR.glob("*__*.jsonl")):
        run, _, sym = p.stem.partition("__")
        if not run or not sym: continue
        trades = []
        for line in p.read_text().splitlines():
            if line.strip():
                try: trades.append(json.loads(line))
                except Exception: pass
        if len(trades) < 5: continue
        pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
        n = len(pnls)
        wins = sum(1 for x in pnls if x > 0)
        wr = wins / n if n else 0
        total_gain = sum(pnls)
        sorted_t = sorted(trades, key=lambda t: int(t.get("entry_ts", 0)))
        chained = 0
        for j in range(1, len(sorted_t)):
            prev, cur = sorted_t[j - 1], sorted_t[j]
            if prev.get("side") == cur.get("side") and 0 <= int(cur.get("entry_ts", 0)) - int(prev.get("exit_ts", 0)) <= 3600:
                chained += 1
        chained_pct = chained / n if n else 0
        score = total_gain * wr * max(0.05, 1.0 - chained_pct)
        ts_first = min(int(t.get("entry_ts", 0)) for t in trades if t.get("entry_ts"))
        ts_last = max(int(t.get("exit_ts", 0)) for t in trades if t.get("exit_ts"))
        win_yrs = max(0.01, (ts_last - ts_first) / (86400.0 * 365.25))
        by_sym.setdefault(sym, []).append({
            "run": run, "score": round(score, 1),
            "trades": n, "wr": round(wr, 3),
            "total_gain_pct": round(total_gain, 1),
            "chained_pct": round(chained_pct, 3),
            "gain_per_year_pct": round(total_gain / win_yrs, 1),
        })
    out = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: -r["score"])
        out.append({"sym": sym, "best": rows[0], "top5": rows[:5]})
    out.sort(key=lambda x: x["sym"])
    return jsonify(out)


@app.route("/runs_ranked")
def runs_ranked():
    """Return runs sorted by composite score (best first) with per-symbol stats.
    Composite = total_gain × win_rate / (1 + churn%) — favors high gain + high WR + low churn.
    Optional ?sym=X to filter.
    """
    sym_filter = request.args.get("sym", "").upper()
    if not TRADES_DIR.exists():
        return jsonify([])
    by_run = {}
    for p in sorted(TRADES_DIR.glob("*__*.jsonl")):
        run, _, sym = p.stem.partition("__")
        if not run or not sym: continue
        if sym_filter and sym.upper() != sym_filter: continue
        trades = []
        for line in p.read_text().splitlines():
            if line.strip():
                try: trades.append(json.loads(line))
                except Exception: pass
        if len(trades) < 5: continue
        pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
        n = len(pnls)
        wins = sum(1 for x in pnls if x > 0)
        wr = wins / n if n else 0
        total_gain = sum(pnls)
        # Quick churn estimate: chained same-side trades within 60min
        sorted_t = sorted(trades, key=lambda t: int(t.get("entry_ts", 0)))
        chained = 0
        for j in range(1, len(sorted_t)):
            prev, cur = sorted_t[j - 1], sorted_t[j]
            if prev.get("side") == cur.get("side") and 0 <= int(cur.get("entry_ts", 0)) - int(prev.get("exit_ts", 0)) <= 3600:
                chained += 1
        chained_pct = chained / n if n else 0
        # Composite score — heavily penalize churn (user 2026-04-29: stop showing
        # configs with 8 trades / half-hour at the top). Multiplicative penalty so
        # 65% churn → 0.35× multiplier, 30% churn → 0.70× multiplier.
        score = total_gain * wr * max(0.05, 1.0 - chained_pct)
        by_run.setdefault(run, []).append({
            "sym": sym, "trades": n, "wr": round(wr, 3),
            "total_gain_pct": round(total_gain, 1),
            "chained_pct": round(chained_pct, 3),
            "score": round(score, 1),
        })
    # Aggregate per-run: sum scores across symbols
    out = []
    for run, sym_rows in by_run.items():
        agg_score = sum(r["score"] for r in sym_rows)
        agg_trades = sum(r["trades"] for r in sym_rows)
        agg_gain = sum(r["total_gain_pct"] for r in sym_rows)
        agg_wr = sum(r["wr"] * r["trades"] for r in sym_rows) / agg_trades if agg_trades else 0
        agg_churn = sum(r["chained_pct"] * r["trades"] for r in sym_rows) / agg_trades if agg_trades else 0
        out.append({
            "run": run,
            "score": round(agg_score, 1),
            "trades": agg_trades,
            "wr": round(agg_wr, 3),
            "total_gain_pct": round(agg_gain, 1),
            "chained_pct": round(agg_churn, 3),
            "n_syms": len(sym_rows),
            "per_symbol": sym_rows,
        })
    out.sort(key=lambda r: -r["score"])
    return jsonify(out)


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


@app.route("/equity_curves")
def equity_curves():
    """Cumulative equity curves per run + buy&hold curve for the symbol.

    Output: {"runs": {run: [{t, v}, ...]}, "buy_hold": [{t, v}, ...],
             "buy_hold_pct": float, "run_totals": {run: total_pct}}
    Equity values are cumulative pnl_pct (sum of per-trade %); B&H is %change-from-window-start.
    """
    runs_arg = request.args.get("runs", "")
    sym = request.args.get("sym", "").upper()
    start = _ts_to_unix(request.args.get("start"))
    end = _ts_to_unix(request.args.get("end"))
    max_pts = int(request.args.get("max", 2000))
    if not runs_arg or not sym:
        return jsonify({"error": "runs and sym required"}), 400
    runs = [r.strip() for r in runs_arg.split(",") if r.strip()]
    out_runs: Dict[str, List[Dict[str, float]]] = {}
    run_totals: Dict[str, float] = {}
    anchor_ts = start if start is not None else 0
    for run in runs:
        path = TRADES_DIR / f"{run}__{sym}.jsonl"
        if not path.exists():
            out_runs[run] = []
            run_totals[run] = 0.0
            continue
        trades: List[Dict[str, Any]] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            ets = int(t.get("exit_ts", 0) or 0)
            if start is not None and ets < start:
                continue
            if end is not None and ets > end:
                continue
            trades.append(t)
        trades.sort(key=lambda t: int(t.get("exit_ts", 0) or 0))
        if not trades:
            out_runs[run] = []
            run_totals[run] = 0.0
            continue
        a_ts = anchor_ts or (int(trades[0].get("entry_ts", 0) or 0) - 1)
        eq: List[Dict[str, float]] = [{"t": int(a_ts), "v": 0.0}]
        cum = 0.0
        for t in trades:
            cum += float(t.get("pnl_pct", 0) or 0)
            eq.append({"t": int(t.get("exit_ts", 0) or 0), "v": cum})
        if len(eq) > max_pts:
            step = len(eq) // max_pts + 1
            keep = eq[::step]
            if keep[-1]["t"] != eq[-1]["t"]:
                keep.append(eq[-1])
            eq = keep
        out_runs[run] = eq
        run_totals[run] = round(cum, 4)
    bh: List[Dict[str, float]] = []
    bh_total = 0.0
    z = _load_npz(sym)
    if z is not None and "timestamps" in z.files and "close_3m" in z.files:
        ts_arr = z["timestamps"]
        c_arr = z["close_3m"]
        n = len(ts_arr)
        lo = int(np.searchsorted(ts_arr, start)) if start is not None else 0
        hi = int(np.searchsorted(ts_arr, end)) if end is not None else n
        lo = max(lo, 0)
        hi = min(hi, n)
        if hi > lo:
            base = 0.0
            for j in range(lo, min(lo + 10, hi)):
                cv = c_arr[j]
                if cv is not None and float(cv) > 0:
                    base = float(cv)
                    break
            if base > 0:
                step = max(1, (hi - lo) // max_pts)
                last_v = 0.0
                for i in range(lo, hi, step):
                    cv = c_arr[i]
                    if cv is None or float(cv) <= 0:
                        continue
                    v = (float(cv) / base - 1.0) * 100.0
                    last_v = v
                    bh.append({"t": int(ts_arr[i]), "v": v})
                bh_total = last_v
    return jsonify({
        "runs": out_runs,
        "buy_hold": bh,
        "buy_hold_pct": round(bh_total, 4),
        "run_totals": run_totals,
    })


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
    # Time-window normalization
    ts_list = sorted([int(t.get("entry_ts", 0)) for t in trades if t.get("entry_ts")])
    ex_list = sorted([int(t.get("exit_ts", 0)) for t in trades if t.get("exit_ts")])
    first_ts = ts_list[0] if ts_list else 0
    last_ts = ex_list[-1] if ex_list else 0
    window_sec = max(1, last_ts - first_ts)
    window_days = window_sec / 86400.0
    window_weeks = window_sec / (86400.0 * 7)
    window_months = window_sec / (86400.0 * 30.44)
    window_years = window_sec / (86400.0 * 365.25)
    # Equity-curve max drawdown
    eq = []; cum = 0
    for p in pnls: cum += p; eq.append(cum)
    peak = -1e9; max_dd = 0
    for v in eq:
        if v > peak: peak = v
        if peak - v > max_dd: max_dd = peak - v
    # Annualized "diagnostic" Sharpe — per CLAUDE.md rule 3 this is INVALID for ranking, label clearly.
    trades_per_year = n / window_years if window_years > 0 else 0
    import math
    sharpe_annual_diag = sharpe_pt * math.sqrt(trades_per_year) if trades_per_year > 0 else 0
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else 0.0,
        "total_gain_pct": total_gain,
        "avg_pnl_pct": avg,
        "std_pnl_pct": std,
        "pool_sharpe_per_trade": sharpe_pt,
        "sharpe_annual_diagnostic": sharpe_annual_diag,
        "max_dd_pct": max_dd,
        "best_pct": max(pnls),
        "worst_pct": min(pnls),
        "long_count": len(longs),
        "short_count": len(shorts),
        "long_total_gain_pct": sum(t.get("pnl_pct", 0) for t in longs),
        "short_total_gain_pct": sum(t.get("pnl_pct", 0) for t in shorts),
        "first_entry_ts": first_ts,
        "last_exit_ts": last_ts,
        "window_days": round(window_days, 1),
        "window_weeks": round(window_weeks, 1),
        "window_months": round(window_months, 1),
        "window_years": round(window_years, 2),
        "trades_per_day": round(n / window_days, 2) if window_days > 0 else 0,
        "trades_per_week": round(n / window_weeks, 1) if window_weeks > 0 else 0,
        "trades_per_month": round(n / window_months, 1) if window_months > 0 else 0,
        "trades_per_year": round(trades_per_year, 0),
        "gain_per_day_pct": round(total_gain / window_days, 4) if window_days > 0 else 0,
        "gain_per_week_pct": round(total_gain / window_weeks, 3) if window_weeks > 0 else 0,
        "gain_per_month_pct": round(total_gain / window_months, 2) if window_months > 0 else 0,
        "gain_per_year_pct": round(total_gain / window_years, 1) if window_years > 0 else 0,
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


@app.route("/run_diff")
def run_diff():
    """Match trades across two runs by entry_ts proximity + side. Returns unique/shared splits.

    Query: ?run_a=X&run_b=Y&sym=Z[&match_window_sec=180&match_side=true&start=&end=]
    """
    run_a = request.args.get("run_a", "")
    run_b = request.args.get("run_b", "")
    sym = request.args.get("sym", "").upper()
    match_window = int(request.args.get("match_window_sec", 180))
    match_side = request.args.get("match_side", "true").lower() != "false"
    start = _ts_to_unix(request.args.get("start"))
    end = _ts_to_unix(request.args.get("end"))
    if not run_a or not run_b or not sym:
        return jsonify({"error": "run_a, run_b, sym required"}), 400

    def _load(run: str):
        path = TRADES_DIR / f"{run}__{sym}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                t = json.loads(line)
            except Exception:
                continue
            if start is not None and t.get("entry_ts", 0) < start: continue
            if end is not None and t.get("exit_ts", 0) > end: continue
            out.append(t)
        return out

    a_trades = _load(run_a)
    b_trades = _load(run_b)
    # Build lookup keyed by side → list of (entry_ts, idx)
    def _index(trades):
        idx = {"LONG": [], "SHORT": []}
        for i, t in enumerate(trades):
            side = t.get("side", "LONG")
            idx.setdefault(side, []).append((int(t.get("entry_ts", 0)), i))
        for s in idx:
            idx[s].sort()
        return idx
    b_idx = _index(b_trades)

    def _has_match_in_b(t):
        side = t.get("side", "LONG") if match_side else "ANY"
        candidates = b_idx.get(side, []) if match_side else (b_idx.get("LONG", []) + b_idx.get("SHORT", []))
        ts = int(t.get("entry_ts", 0))
        # Linear scan for simplicity — most lookups are O(small)
        for bts, bi in candidates:
            if abs(bts - ts) <= match_window:
                return bi
            if bts > ts + match_window:
                break
        return None

    a_idx = _index(a_trades)
    def _has_match_in_a(t):
        side = t.get("side", "LONG") if match_side else "ANY"
        candidates = a_idx.get(side, []) if match_side else (a_idx.get("LONG", []) + a_idx.get("SHORT", []))
        ts = int(t.get("entry_ts", 0))
        for ats, ai in candidates:
            if abs(ats - ts) <= match_window:
                return ai
            if ats > ts + match_window:
                break
        return None

    unique_a, shared_pairs = [], []
    matched_b_indices = set()
    for ti, t in enumerate(a_trades):
        m = _has_match_in_b(t)
        if m is None:
            unique_a.append(t)
        else:
            shared_pairs.append({"a": t, "b": b_trades[m]})
            matched_b_indices.add(m)
    unique_b = [b_trades[i] for i in range(len(b_trades)) if i not in matched_b_indices]

    def _stats(trades):
        if not trades: return {"trades": 0}
        pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        std = statistics.stdev(pnls) if n > 1 else 0.0
        return {
            "trades": n,
            "win_rate": wins / n if n else 0.0,
            "total_gain_pct": sum(pnls),
            "avg_pnl_pct": sum(pnls) / n if n else 0.0,
            "pool_sharpe_per_trade": (sum(pnls) / n) / std if std > 0 else 0.0,
        }

    return jsonify({
        "match_window_sec": match_window,
        "match_side": match_side,
        "a_total": len(a_trades),
        "b_total": len(b_trades),
        "unique_a": unique_a,
        "unique_b": unique_b,
        "shared_a": [p["a"] for p in shared_pairs],
        "shared_b": [p["b"] for p in shared_pairs],
        "stats": {
            "unique_a": _stats(unique_a),
            "unique_b": _stats(unique_b),
            "shared_a": _stats([p["a"] for p in shared_pairs]),
            "shared_b": _stats([p["b"] for p in shared_pairs]),
        },
    })


@app.route("/tier_diff")
def tier_diff():
    """Categorized Tier-1 vs Tier-2 disagreement report.
    Query: ?run_t1=X&run_t2=Y&sym=Z[&match_window_sec=180]
    Returns categorized buckets + reason cross-tab.
    """
    run_t1 = request.args.get("run_t1", "")
    run_t2 = request.args.get("run_t2", "")
    sym = request.args.get("sym", "").upper()
    match_window = int(request.args.get("match_window_sec", 180))
    if not run_t1 or not run_t2 or not sym:
        return jsonify({"error": "run_t1, run_t2, sym required"}), 400

    def _load(run):
        path = TRADES_DIR / f"{run}__{sym}.jsonl"
        if not path.exists(): return []
        out = []
        for line in path.read_text().splitlines():
            if line.strip():
                try: out.append(json.loads(line))
                except Exception: pass
        return out

    t1 = _load(run_t1)
    t2 = _load(run_t2)
    if not t1 and not t2:
        return jsonify({"error": f"no trades for {run_t1} or {run_t2} on {sym}"}), 404

    def _index_by_side(trades):
        idx = {"LONG": [], "SHORT": []}
        for i, t in enumerate(trades):
            side = t.get("side", "LONG")
            idx.setdefault(side, []).append((int(t.get("entry_ts", 0)), i))
        for s in idx: idx[s].sort()
        return idx
    t2_idx = _index_by_side(t2)

    # Match T1 → T2
    t1_only = []
    shared_t1 = []  # paired with t2 partner
    matched_t2 = set()
    for ti, t in enumerate(t1):
        side = t.get("side", "LONG")
        ts = int(t.get("entry_ts", 0))
        match_idx = None
        for bts, bi in t2_idx.get(side, []):
            if abs(bts - ts) <= match_window:
                match_idx = bi; break
            if bts > ts + match_window: break
        if match_idx is None:
            t1_only.append(t)
        else:
            shared_t1.append({"t1": t, "t2": t2[match_idx]})
            matched_t2.add(match_idx)
    t2_only = [t2[i] for i in range(len(t2)) if i not in matched_t2]

    # Stats
    def _stats(trades):
        if not trades: return {"trades": 0}
        pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        std = statistics.stdev(pnls) if n > 1 else 0
        return {"trades": n, "win_rate": wins / n if n else 0, "total_gain_pct": sum(pnls),
                "avg_pnl_pct": sum(pnls) / n if n else 0, "pool_sharpe_per_trade": (sum(pnls)/n)/std if std > 0 else 0}

    # Reason cross-tab on shared (where T1 and T2 BOTH entered, what reasons matched/diverged)
    crosstab_entry = {}  # (t1_reason, t2_reason) -> count
    crosstab_exit  = {}
    for pair in shared_t1:
        ek = (pair["t1"].get("entry_reason", "?"), pair["t2"].get("entry_reason", "?"))
        xk = (pair["t1"].get("exit_reason", "?"), pair["t2"].get("exit_reason", "?"))
        crosstab_entry[ek] = crosstab_entry.get(ek, 0) + 1
        crosstab_exit[xk] = crosstab_exit.get(xk, 0) + 1

    # Diverged exits: same entry, different exit_ts (>match_window apart)
    exit_diverged = []
    for pair in shared_t1:
        ext1 = int(pair["t1"].get("exit_ts", 0))
        ext2 = int(pair["t2"].get("exit_ts", 0))
        if abs(ext1 - ext2) > match_window:
            exit_diverged.append(pair)

    # Top reason buckets in unique sets
    def _reason_counts(trades, kind):
        c = {}
        for t in trades:
            r = t.get(f"{kind}_reason", "?")
            c[r] = c.get(r, 0) + 1
        return sorted(c.items(), key=lambda x: -x[1])[:10]

    return jsonify({
        "match_window_sec": match_window,
        "totals": {"t1": len(t1), "t2": len(t2), "shared": len(shared_t1),
                   "t1_only": len(t1_only), "t2_only": len(t2_only),
                   "shared_exit_diverged": len(exit_diverged)},
        "stats": {
            "t1_only": _stats(t1_only),
            "t2_only": _stats(t2_only),
            "shared_t1": _stats([p["t1"] for p in shared_t1]),
            "shared_t2": _stats([p["t2"] for p in shared_t1]),
            "shared_exit_diverged_t1": _stats([p["t1"] for p in exit_diverged]),
            "shared_exit_diverged_t2": _stats([p["t2"] for p in exit_diverged]),
        },
        "top_t1_only_entry_reasons": _reason_counts(t1_only, "entry"),
        "top_t1_only_exit_reasons":  _reason_counts(t1_only, "exit"),
        "top_t2_only_entry_reasons": _reason_counts(t2_only, "entry"),
        "top_t2_only_exit_reasons":  _reason_counts(t2_only, "exit"),
        "exit_crosstab_top": sorted([(k, v) for k, v in crosstab_exit.items()], key=lambda x: -x[1])[:15],
        "entry_crosstab_top": sorted([(k, v) for k, v in crosstab_entry.items()], key=lambda x: -x[1])[:15],
    })


OVERRIDE_DIRS = [
    BASE_PATH / "backtest_v8" / "btc_loop_results",
    TRADES_DIR / "auto_overrides",
]


@app.route("/config_heatmap")
def config_heatmap():
    """Cross-correlate config switches with backtest outcomes across all runs.
    For each switch that appears in ≥2 runs with different values, report:
    {switch: {value: [{run, sym, trades, wr, sharpe_pt, total_gain_pct}, ...]}}
    """
    sym_filter = request.args.get("sym", "").upper()
    # Map run_id → override config (try both override dirs)
    run_to_cfg: Dict[str, Dict[str, Any]] = {}
    for d in OVERRIDE_DIRS:
        if not d.exists(): continue
        for p in d.glob("*.json"):
            stem = p.stem
            run_id = stem
            if stem.startswith("override_"):
                run_id = stem[len("override_"):]
            try:
                cfg = json.loads(p.read_text())
                run_to_cfg[run_id] = cfg
            except Exception:
                continue
    # For each trade JSONL, compute stats
    run_stats: Dict[str, Dict[str, Any]] = {}
    for p in sorted(TRADES_DIR.glob("*__*.jsonl")):
        run_id, _, sym = p.stem.partition("__")
        if sym_filter and sym.upper() != sym_filter: continue
        trades = []
        for line in p.read_text().splitlines():
            if line.strip():
                try: trades.append(json.loads(line))
                except Exception: pass
        if len(trades) < 5: continue
        pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
        n = len(pnls)
        wins = sum(1 for x in pnls if x > 0)
        std = statistics.stdev(pnls) if n > 1 else 0
        run_stats.setdefault(run_id, {})[sym] = {
            "trades": n, "wr": wins / n, "total_gain_pct": sum(pnls),
            "avg_pnl_pct": sum(pnls) / n, "sharpe_pt": (sum(pnls) / n) / std if std > 0 else 0,
        }
    # Build heatmap: collect every switch:value combo seen across runs
    switch_values: Dict[str, Dict[str, list]] = {}
    for run_id, sym_map in run_stats.items():
        cfg = run_to_cfg.get(run_id, {})
        if not cfg: continue
        for sym, st in sym_map.items():
            for k, v in cfg.items():
                if k.startswith("_"): continue
                # Only flat scalar values (skip lists/dicts)
                if isinstance(v, (list, dict)): continue
                key = str(v)
                switch_values.setdefault(k, {}).setdefault(key, []).append({
                    "run": run_id, "sym": sym,
                    **st,
                })
    # Trim to switches that vary (≥2 distinct values, ≥2 runs)
    out_switches = {}
    for switch, by_val in switch_values.items():
        if len(by_val) < 2: continue
        total_runs = sum(len(v) for v in by_val.values())
        if total_runs < 2: continue
        # Aggregate per value: mean sharpe_pt, mean wr, total trades, run count
        rows = []
        for val, lst in by_val.items():
            n_runs = len(lst)
            tot_trades = sum(r["trades"] for r in lst)
            mean_sharpe = sum(r["sharpe_pt"] for r in lst) / n_runs
            mean_wr = sum(r["wr"] for r in lst) / n_runs
            mean_gain = sum(r["total_gain_pct"] for r in lst) / n_runs
            rows.append({
                "value": val, "n_runs": n_runs, "trades": tot_trades,
                "mean_sharpe_pt": round(mean_sharpe, 3), "mean_wr": round(mean_wr, 3),
                "mean_total_gain_pct": round(mean_gain, 1),
                "runs": [r["run"] for r in lst],
            })
        rows.sort(key=lambda r: -r["mean_sharpe_pt"])
        # Effect size: (best - worst) Sharpe spread
        spread = rows[0]["mean_sharpe_pt"] - rows[-1]["mean_sharpe_pt"] if len(rows) > 1 else 0
        out_switches[switch] = {"rows": rows, "sharpe_spread": round(spread, 3)}
    # Sort switches by spread desc (most impactful first)
    sorted_switches = sorted(out_switches.items(), key=lambda kv: -kv[1]["sharpe_spread"])
    return jsonify({
        "n_runs": len(run_stats),
        "n_switches_varying": len(out_switches),
        "switches": [{"name": k, **v} for k, v in sorted_switches],
    })


@app.route("/suggestions")
def suggestions():
    """Return the latest SUGGESTIONS_latest.md as plain text for in-page rendering."""
    p = TRADES_DIR / "SUGGESTIONS_latest.md"
    if not p.exists():
        return "No suggestions report yet. Run `python3 suggestion_engine.py` to generate.", 404, {"Content-Type": "text/plain"}
    return p.read_text(), 200, {"Content-Type": "text/markdown"}


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
