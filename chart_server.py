#!/usr/bin/env python3
"""Tiny Flask server for visualizing backtest trades + historic live trades on charts.

Endpoints:
  GET /                           → chart.html
  GET /symbols                    → ["BTCUSDC", "ETHUSDC", ...]
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
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

# Single chokepoint for canonical Sharpe per CLAUDE.md NO-LIES MANDATE.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_guard  # noqa: E402

BASE_PATH = Path(os.environ.get("BASE_PATH", "/Users/niels/Documents/binance"))
NPZ_DIR = BASE_PATH / "backtest_v8" / "indicators"
TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))
HISTORY_DIR = BASE_PATH / "data" / "history"
STOCK_ACCOUNT_KEYS = {"trb", "trc", "tra"}
# data/history/<acct> is the canonical per-account live trade log for ALL
# accounts (crypto + stocks). Stocks dirs (trb/trc/tra) are symlinks into
# data/tradier/history/<acct> where tradier_positions.append_to_position_history_file
# physically writes them — so reads via data/history/<acct> resolve transparently.
ACCOUNTS = ["ang", "inf", "flz", "men", "fin", "trb", "trc"]
SEVEN_D_AGENT_ACCOUNTS = {"inf", "fin", "trc"}  # NEW agent system (hourly 7d recalcs)


def _history_dir_for(account: str) -> Path:
    """Single canonical location: data/history/<account>. trb/trc/tra are
    symlinks into data/tradier/history/<acct> created 2026-05-01."""
    return HISTORY_DIR / account

# Extra trade-JSONL roots scanned in addition to TRADES_DIR (added 2026-04-30 per
# user: "make sure I can see and select the latest 7d tests AND all the big sweeps").
# Each entry is a glob pattern relative to BASE_PATH. We walk recursively and pick
# up *__*.jsonl files. A registry cache (rebuilt every 30s) maps run_id → file path.
_DEFAULT_EXTRA_ROOTS = [
    "data/hourly_reconfig/*/runs/*",       # latest hourly cycles per account
    "data/canonical_trades/*",             # big-sweep canonical trade JSONLs
]
EXTRA_ROOTS_CFG = os.environ.get("V8_TRADES_EXTRA_ROOTS", "").strip()
if EXTRA_ROOTS_CFG:
    EXTRA_ROOT_GLOBS = [g.strip() for g in EXTRA_ROOTS_CFG.split(",") if g.strip()]
else:
    EXTRA_ROOT_GLOBS = _DEFAULT_EXTRA_ROOTS

# Registry: run_id → Path. Refreshed every REGISTRY_TTL seconds.
_run_registry: Dict[str, Path] = {}
_run_registry_built_at: float = 0.0
REGISTRY_TTL = 30.0


def _all_trade_roots() -> List[Path]:
    seen: set = set()
    roots: List[Path] = []
    if TRADES_DIR.exists():
        roots.append(TRADES_DIR)
        seen.add(str(TRADES_DIR))
    for g in EXTRA_ROOT_GLOBS:
        for p in BASE_PATH.glob(g):
            if p.is_dir():
                key = str(p)
                if key in seen:
                    continue
                seen.add(key)
                roots.append(p)
    return roots


def _build_run_registry() -> Dict[str, Path]:
    """Walk every trade root, collect *__*.jsonl. run_id is derived from the file
    stem (everything before the LAST __<SYM>). Source-tagged so chart UI can group:
      hourly_reconfig/<acct>/runs/<cycle>/<run>__<sym>.jsonl  →  hr_<acct>::<run>
      canonical_trades/<run>/<run>__<sym>.jsonl              →  bigsweep::<run>
      legacy /tmp/v8_trades/<run>__<sym>.jsonl               →  <run>  (no prefix)
    Latest mtime wins on collision.
    """
    reg: Dict[str, Tuple[Path, float]] = {}
    for root in _all_trade_roots():
        try:
            for p in root.rglob("*__*.jsonl"):
                stem = p.stem
                idx = stem.rfind("__")
                if idx <= 0:
                    continue
                run_id = stem[:idx]
                s = str(p)
                if "/hourly_reconfig/" in s:
                    try:
                        parts = p.parts
                        i = parts.index("hourly_reconfig")
                        acct = parts[i + 1]
                        run_id_keyed = f"hr_{acct}::{run_id}"
                    except Exception:
                        run_id_keyed = run_id
                elif "/canonical_trades/" in s:
                    run_id_keyed = f"bigsweep::{run_id}"
                else:
                    run_id_keyed = run_id  # legacy /tmp/v8_trades
                try:
                    mtime = p.stat().st_mtime
                except Exception:
                    mtime = 0.0
                if run_id_keyed not in reg or mtime > reg[run_id_keyed][1]:
                    reg[run_id_keyed] = (p, mtime)
        except Exception:
            continue
    return {rid: pair[0] for rid, pair in reg.items()}


def _get_run_registry(force: bool = False) -> Dict[str, Path]:
    global _run_registry, _run_registry_built_at
    now = time.time()
    if force or now - _run_registry_built_at > REGISTRY_TTL:
        _run_registry = _build_run_registry()
        _run_registry_built_at = now
    return _run_registry


def _resolve_trade_path(run: str, sym: str) -> Optional[Path]:
    """Find the JSONL for (run, sym). Strips registry prefix (hr_<acct>:: or
    bigsweep::) before constructing the file path within the resolved parent dir.
    """
    legacy = TRADES_DIR / f"{run}__{sym}.jsonl"
    if legacy.exists():
        return legacy
    reg = _get_run_registry()
    # Strip prefix if present — bare run_id is used in filename.
    bare_run = run.split("::", 1)[1] if "::" in run else run
    if run in reg:
        p = reg[run]
        candidate = p.parent / f"{bare_run}__{sym}.jsonl"
        if candidate.exists():
            return candidate
        return p if p.stem.endswith(f"__{sym}") else None
    return None
TF_SECONDS = {"3m": 180, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400}

app = Flask(__name__, static_folder=str(BASE_PATH / "chart_static"))

_npz_cache: Dict[str, Any] = {}
_response_cache: Dict[str, Any] = {}  # {key: (expires_unix, json_str)}
_precomputed: Dict[str, str] = {}  # {endpoint: json_body}  written by background thread
_precompute_lock = __import__("threading").Lock()
# Per-file aggregate cache: {path_str: (mtime, agg_dict)}
# Lets us avoid re-parsing 3.7GB of trade jsonls every cycle — only re-read files
# whose mtime has changed since last cycle.
_file_agg_cache: Dict[str, Any] = {}


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


def _no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/")
def index():
    return _no_cache(send_from_directory(app.static_folder, "chart.html"))


@app.route("/heatmap")
@app.route("/heatmap.html")
def heatmap_page():
    return _no_cache(send_from_directory(app.static_folder, "heatmap.html"))


@app.route("/leaderboard")
@app.route("/leaderboard.html")
def leaderboard_page():
    return _no_cache(send_from_directory(app.static_folder, "leaderboard.html"))


@app.route("/symbols")
def symbols():
    syms = sorted(p.stem for p in NPZ_DIR.glob("*.npz"))
    return jsonify(syms)


@app.route("/runs")
def runs():
    """Return all known run_ids across legacy TRADES_DIR + extra roots.
    Latest hourly_reconfig cycles + canonical_trades dirs auto-included.
    """
    out = set()
    if TRADES_DIR.exists():
        for p in TRADES_DIR.glob("*__*.jsonl"):
            run, _, _ = p.stem.partition("__")
            out.add(run)
    reg = _get_run_registry()
    for rid in reg:
        out.add(rid)
    return jsonify(sorted(out))


@app.route("/runs_grouped")
def runs_grouped():
    """Return runs grouped by source (latest hourly per account vs big sweeps).
    User-friendly for the chart UI to show separated lists.
    """
    groups: Dict[str, List[str]] = {"hourly_flz": [], "hourly_fin": [], "hourly_inf": [],
                                     "hourly_trc": [], "hourly_trb": [],
                                     "big_sweep": [], "legacy": [], "other": []}
    reg = _get_run_registry()
    for rid in reg:
        if rid.startswith("hr_flz::"):
            groups["hourly_flz"].append(rid)
        elif rid.startswith("hr_fin::"):
            groups["hourly_fin"].append(rid)
        elif rid.startswith("hr_inf::"):
            groups["hourly_inf"].append(rid)
        elif rid.startswith("hr_trc::"):
            groups["hourly_trc"].append(rid)
        elif rid.startswith("hr_trb::"):
            groups["hourly_trb"].append(rid)
        elif rid.startswith("bigsweep::"):
            groups["big_sweep"].append(rid)
        elif "::" not in rid:
            groups["legacy"].append(rid)
        else:
            groups["other"].append(rid)
    for k in groups:
        groups[k] = sorted(set(groups[k]))
    groups["_roots"] = [str(r) for r in _all_trade_roots()]
    return jsonify(groups)


def _iter_all_trade_files() -> List[Tuple[str, str, Path]]:
    """Walk every known run-trade JSONL across all roots.

    Yields (run_id_keyed, sym, path) tuples. run_id_keyed matches what
    `_build_run_registry` produces (hr_<acct>::, bigsweep::, or bare for legacy).
    Multi-sym runs surface every sym — the registry alone keeps only one Path
    per run_id (latest mtime), but for ranking we need every sibling file.
    """
    out: List[Tuple[str, str, Path]] = []
    seen: set = set()
    for root in _all_trade_roots():
        try:
            for p in root.rglob("*__*.jsonl"):
                stem = p.stem
                idx = stem.rfind("__")
                if idx <= 0:
                    continue
                run_id = stem[:idx]
                sym = stem[idx + 2:]
                if not sym:
                    continue
                s = str(p)
                if "/hourly_reconfig/" in s:
                    try:
                        parts = p.parts
                        i = parts.index("hourly_reconfig")
                        acct = parts[i + 1]
                        run_keyed = f"hr_{acct}::{run_id}"
                    except Exception:
                        run_keyed = run_id
                elif "/canonical_trades/" in s:
                    run_keyed = f"bigsweep::{run_id}"
                else:
                    run_keyed = run_id
                key = (run_keyed, sym, str(p))
                if key in seen:
                    continue
                seen.add(key)
                out.append((run_keyed, sym, p))
        except Exception:
            continue
    return out


def _compute_per_sym_best(sym_filter: str = "") -> str:
    """Pure compute across ALL trade roots (legacy + hourly_reconfig + canonical_trades)."""
    by_sym: Dict[str, list] = {}
    for run, sym, p in _iter_all_trade_files():
        if sym_filter and sym.upper() != sym_filter.upper():
            continue
        agg = _file_aggregates(p)
        if not agg:
            continue
        n = agg["n"]
        wr = agg["wr"]
        total_gain = agg["total_gain"]
        chained_pct = agg["chained_pct"]
        score = total_gain * wr * max(0.05, 1.0 - chained_pct)
        ts_first = agg["ts_first"]
        ts_last = agg["ts_last"]
        win_yrs = max(0.01, (ts_last - ts_first) / (86400.0 * 365.25))
        by_sym.setdefault(sym, []).append({
            "run": run, "score": round(score, 1),
            "trades": n, "wr": round(wr, 3),
            "total_gain_pct": round(total_gain, 1),
            "chained_pct": round(chained_pct, 3),
            "gain_per_year_pct": round(total_gain / win_yrs, 1),
            "sym_sharpe": round(agg["sym_sharpe_capped"], 4),
        })
    out = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: -r["score"])
        out.append({"sym": sym, "best": rows[0], "top5": rows[:5]})
    out.sort(key=lambda x: x["sym"])
    return json.dumps(out)


@app.route("/per_sym_best")
def per_sym_best():
    """Cached endpoint. ?sym=X filters to one symbol's top runs.
    Returns []+'warming' header if not yet populated."""
    sym_filter = request.args.get("sym", "").upper()
    if sym_filter:
        body = _compute_per_sym_best(sym_filter)
        return app.response_class(body, mimetype="application/json")
    with _precompute_lock:
        pre = _precomputed.get("per_sym_best")
    if pre:
        return app.response_class(pre, mimetype="application/json")
    resp = app.response_class("[]", mimetype="application/json")
    resp.headers["X-Cache-Status"] = "warming"
    return resp


def _file_aggregates(p):
    """Return aggregate dict for a single jsonl file, using mtime cache.
    Per CLAUDE.md STANDARD METRIC SET: stores sum_pnl + sum_pnl_sq so we can
    pool variance across symbols correctly when aggregating runs."""
    sp = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    cached = _file_agg_cache.get(sp)
    if cached and cached[0] == mtime:
        return cached[1]
    trades = []
    try:
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        return None
    if len(trades) < 5:
        _file_agg_cache[sp] = (mtime, None)
        return None
    pnls = [float(t.get("pnl_pct", 0) or 0) for t in trades]
    n = len(pnls)
    wins = sum(1 for x in pnls if x > 0)
    total_gain = sum(pnls)
    sum_pnl_sq = sum(x * x for x in pnls)
    avg_pnl = total_gain / n if n else 0
    var = (sum_pnl_sq / n - avg_pnl * avg_pnl) if n > 1 else 0
    sym_std = var ** 0.5 if var > 0 else 0
    sym_sharpe = (avg_pnl / sym_std) if sym_std > 0 else 0
    # CLAUDE.md cap: ±5.0 for diagnostic per-symbol sharpe
    sym_sharpe_capped = max(-5.0, min(5.0, sym_sharpe))
    sorted_t = sorted(trades, key=lambda t: int(t.get("entry_ts", 0)))
    chained = 0
    for j in range(1, len(sorted_t)):
        prev, cur = sorted_t[j - 1], sorted_t[j]
        if prev.get("side") == cur.get("side") and 0 <= int(cur.get("entry_ts", 0)) - int(prev.get("exit_ts", 0)) <= 3600:
            chained += 1
    ts_first = min((int(t.get("entry_ts", 0)) for t in trades if t.get("entry_ts")), default=0)
    ts_last = max((int(t.get("exit_ts", 0)) for t in trades if t.get("exit_ts")), default=0)
    agg = {
        "n": n,
        "wins": wins,
        "wr": wins / n if n else 0,
        "total_gain": total_gain,
        "sum_pnl": total_gain,
        "sum_pnl_sq": sum_pnl_sq,
        "avg_pnl": avg_pnl,
        "sym_sharpe": sym_sharpe,
        "sym_sharpe_capped": sym_sharpe_capped,
        "chained": chained,
        "chained_pct": chained / n if n else 0,
        "ts_first": ts_first,
        "ts_last": ts_last,
    }
    _file_agg_cache[sp] = (mtime, agg)
    return agg


def _compute_runs_ranked(sym_filter: str = "") -> str:
    """Pure compute. Per CLAUDE.md STANDARD METRIC SET, every row carries:
        pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr,
        total_gain_pct, trades, wr, chained_pct, max_dd_pct (TBD)
    Sorts by pool_sharpe (the canonical metric) descending.
    Walks ALL trade roots (legacy + hourly_reconfig + canonical_trades)."""
    by_run = {}
    by_run_mtime: Dict[str, float] = {}
    for run, sym, p in _iter_all_trade_files():
        if sym_filter and sym.upper() != sym_filter:
            continue
        agg = _file_aggregates(p)
        if not agg:
            continue
        # Carry full agg through so we can pool variance across syms
        by_run.setdefault(run, []).append({
            "sym": sym, "trades": agg["n"], "wr": round(agg["wr"], 3),
            "total_gain_pct": round(agg["total_gain"], 1),
            "chained_pct": round(agg["chained_pct"], 3),
            "sym_sharpe": round(agg["sym_sharpe_capped"], 4),
            "_n": agg["n"],
            "_sum": agg["sum_pnl"],
            "_sum_sq": agg["sum_pnl_sq"],
            "_ts_first": agg["ts_first"],
            "_ts_last": agg["ts_last"],
        })
        try:
            mt = p.stat().st_mtime
            if mt > by_run_mtime.get(run, 0):
                by_run_mtime[run] = mt
        except Exception:
            pass
    out = []
    for run, sym_rows in by_run.items():
        # Pool across all symbols: pool_sharpe = mean(all_returns) / std(all_returns)
        total_n = sum(r["_n"] for r in sym_rows)
        total_sum = sum(r["_sum"] for r in sym_rows)
        total_sum_sq = sum(r["_sum_sq"] for r in sym_rows)
        if total_n > 1:
            pool_avg = total_sum / total_n
            pool_var = total_sum_sq / total_n - pool_avg * pool_avg
            pool_std = pool_var ** 0.5 if pool_var > 0 else 0
            pool_sharpe = (pool_avg / pool_std) if pool_std > 0 else 0
        else:
            pool_avg = 0
            pool_sharpe = 0
        # Per-sym-avg sharpe — diagnostic only, exclude syms with <30 trades
        eligible = [r for r in sym_rows if r["_n"] >= 30]
        sym_sharpe_avg = (sum(r["sym_sharpe"] for r in eligible) / len(eligible)) if eligible else 0
        # Time-window metrics
        ts_min = min((r["_ts_first"] for r in sym_rows if r["_ts_first"]), default=0)
        ts_max = max((r["_ts_last"] for r in sym_rows if r["_ts_last"]), default=0)
        years = max(0.01, (ts_max - ts_min) / (86400 * 365.25)) if ts_max > ts_min else 0.01
        agg_trades = total_n
        agg_gain = total_sum
        avg_gain_trade = (agg_gain / agg_trades) if agg_trades else 0
        gain_per_yr = agg_gain / years
        gain_sym_yr = (agg_gain / max(1, len(sym_rows))) / years
        agg_wr = sum(r["wr"] * r["_n"] for r in sym_rows) / agg_trades if agg_trades else 0
        agg_churn = sum(r["chained_pct"] * r["_n"] for r in sym_rows) / agg_trades if agg_trades else 0
        # Strip private _ keys before serialization
        clean_per_sym = [{k: v for k, v in r.items() if not k.startswith("_")} for r in sym_rows]
        # Legacy composite score (kept for backward compat / chart UI tooltip)
        legacy_score = agg_gain * agg_wr * max(0.05, 1.0 - agg_churn)
        out.append({
            "run": run,
            # ===== CANONICAL METRICS (CLAUDE.md STANDARD METRIC SET) =====
            "pool_sharpe": round(pool_sharpe, 4),
            "sym_sharpe": round(sym_sharpe_avg, 4),
            "avg_gain_trade": round(avg_gain_trade, 4),
            "gain_per_yr": round(gain_per_yr, 1),
            "gain_sym_yr": round(gain_sym_yr, 2),
            # ===== Existing fields (kept for compatibility) =====
            "score": round(legacy_score, 1),
            "trades": agg_trades,
            "wr": round(agg_wr, 3),
            "total_gain_pct": round(agg_gain, 1),
            "chained_pct": round(agg_churn, 3),
            "n_syms": len(sym_rows),
            "n_years": round(years, 2),
            "per_symbol": clean_per_sym,
            "mtime": int(by_run_mtime.get(run, 0)),
            "syms": sorted([r["sym"] for r in sym_rows]),
        })
    # SORT BY POOL_SHARPE descending — the canonical CLAUDE.md ranking metric
    out.sort(key=lambda r: -r["pool_sharpe"])
    return json.dumps(out)


@app.route("/runs_ranked")
def runs_ranked():
    """Cached endpoint. Returns 503+'warming up' if cache not yet populated to
    avoid blocking the request thread on the 67s cold scan (parallel scans = 5x slower)."""
    import time as _time
    sym_filter = request.args.get("sym", "").upper()
    if not sym_filter:
        with _precompute_lock:
            pre = _precomputed.get("runs_ranked")
        if pre:
            return app.response_class(pre, mimetype="application/json")
        # No cache yet — return empty list with a header so client knows to retry
        resp = app.response_class("[]", mimetype="application/json")
        resp.headers["X-Cache-Status"] = "warming"
        return resp
    cache_key = f"runs_ranked:{sym_filter}"
    now = _time.time()
    cached = _response_cache.get(cache_key)
    if cached and cached[0] > now:
        return app.response_class(cached[1], mimetype="application/json")
    body = _compute_runs_ranked(sym_filter)
    _response_cache[cache_key] = (now + 30, body)
    return app.response_class(body, mimetype="application/json")


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
    max_trades = int(request.args.get("max", 8000))
    if not run or not sym:
        return jsonify({"error": "run and sym required"}), 400
    path = _resolve_trade_path(run, sym)
    if path is None or not path.exists():
        return jsonify({"trades": [], "stats": {}, "missing_run": run, "missing_sym": sym})
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
    stats = _trade_stats(trades)
    truncated = False
    if len(trades) > max_trades:
        trades = trades[-max_trades:]
        truncated = True
    return jsonify({"trades": trades, "stats": stats, "truncated": truncated})


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
        path = _resolve_trade_path(run, sym)
        if path is None or not path.exists():
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
        base = _history_dir_for(acct)
        long_path = base / f"{sym}_LONG.jsonl"
        short_path = base / f"{sym}_SHORT.jsonl"
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
    """Walk OPEN/AUGMENT/REDUCE/CLOSE events into closed-trade rounds keyed by
    (symbol, side). Per-symbol-per-side keying is required: an account holds
    many symbols at once. Earlier code keyed only by side, which collided
    AUGMENTs/REDUCEs across symbols and produced 0 closed rounds for accounts
    with mixed activity. Dust threshold (0.5% of peak qty) closes a round
    when residual is small — real exchanges leave fractional residuals.
    """
    rounds: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    closed: List[Dict[str, Any]] = []
    for ev in events:
        side = ev.get("side")
        sym = ev.get("symbol") or ""
        kind = (ev.get("type") or "").upper()
        qty = float(ev.get("qty") or 0)
        price = float(ev.get("price") or 0)
        ts = ev.get("unix_ts", 0)
        reason = ev.get("reason", "")
        if not side or not sym or qty <= 0 or price <= 0:
            continue
        key = (sym, side)
        rd = rounds.get(key)
        if kind in ("OPEN", "AUGMENT"):
            if rd is None or rd.get("qty", 0) <= 0:
                rd = {
                    "side": side, "account": ev.get("account"), "symbol": sym,
                    "entry_ts": ts, "entry_price": price, "qty": qty,
                    "peak_qty": qty,
                    "entry_reason": reason, "events": [ev],
                }
                rounds[key] = rd
            else:
                new_qty = rd["qty"] + qty
                rd["entry_price"] = (rd["entry_price"] * rd["qty"] + price * qty) / new_qty
                rd["qty"] = new_qty
                rd["peak_qty"] = max(rd.get("peak_qty", 0), new_qty)
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
            dust_threshold = max(1e-9, rd.get("peak_qty", 0) * 0.005)
            if rd["qty"] <= dust_threshold or kind == "CLOSE":
                closed.append({
                    "account": rd["account"], "symbol": rd.get("symbol", ""), "side": side,
                    "entry_ts": rd["entry_ts"], "entry_price": rd["entry_price"],
                    "exit_ts": ts, "exit_price": price,
                    "entry_reason": rd["entry_reason"], "exit_reason": reason,
                    "pnl_pct": pnl_pct, "pnl_usd": (pnl_pct / 100.0) * (rd["entry_price"] * close_qty),
                    "duration_sec": ts - rd["entry_ts"],
                    "stream": "historic",
                })
                rounds[key] = None
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
    # CANONICAL_METRICS.md: NO sharpe_annual emission anywhere — banned for being feel-good frequency inflation.
    trades_per_year = n / window_years if window_years > 0 else 0
    # Apply CLAUDE.md ±5 cap per inflation rule (single-symbol slices easily blow past ±5)
    inflated = abs(sharpe_pt) > metrics_guard.PER_SYM_SHARPE_CAP and n < 5000
    tier = metrics_guard.tier_name(sharpe_pt)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else 0.0,
        "total_gain_pct": total_gain,
        "avg_pnl_pct": avg,
        "std_pnl_pct": std,
        "pool_sharpe": sharpe_pt,
        "pool_sharpe_per_trade": sharpe_pt,  # alias — chart.html reads this name
        "tier": tier,
        "inflated": inflated,
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


_acct_pool_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_ACCT_POOL_TTL_SEC = 300  # 5min — Live changes minute-to-minute


def _load_account_history_all_syms(account: str) -> List[Dict[str, Any]]:
    """Load every trade event for the account (all symbols, all sides). Crypto
    lives in data/history/<acct>; stocks in data/tradier/history/<acct>.
    Returns chronological list."""
    base = _history_dir_for(account)
    events: List[Dict[str, Any]] = []
    if not base.exists():
        return events
    for jsonl_file in sorted(base.glob("*.jsonl")):
        stem = jsonl_file.stem
        parts = stem.rsplit("_", 1)
        symbol = parts[0] if len(parts) == 2 else stem
        side = parts[1] if len(parts) == 2 else "UNKNOWN"
        try:
            for line in jsonl_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ev["symbol"] = symbol
                ev["side"] = side
                ev["account"] = account
                ts_str = ev.get("ts") or ev.get("timestamp")
                try:
                    ev["unix_ts"] = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
                except Exception:
                    ev["unix_ts"] = 0
                events.append(ev)
        except Exception:
            continue
    events.sort(key=lambda e: e.get("unix_ts", 0))
    return events


def _account_pool_stats(account: str) -> Dict[str, Any]:
    """Canonical 9-field metric set for account aggregated across ALL symbols.
    pool_sharpe = mean(all_trade_returns)/std pooled per CLAUDE.md rule 4."""
    rec = _acct_pool_cache.get(account)
    if rec and (time.time() - rec[0]) < _ACCT_POOL_TTL_SEC:
        return rec[1]
    events = _load_account_history_all_syms(account)
    closed = _reconstruct_trades_from_events(events)
    if not closed:
        result = {
            "account": account, "trades": 0, "n_syms": 0, "years": 0.0,
            "pool_sharpe": 0.0, "pool_sharpe_per_trade": 0.0,
            "sym_sharpe": 0.0, "avg_gain_trade": 0.0,
            "gain_per_yr": 0.0, "gain_sym_yr": 0.0, "max_dd_pct": 0.0,
            "tier": "Noise", "inflated": False, "tags": ["NO_TRADES"],
        }
        _acct_pool_cache[account] = (time.time(), result)
        return result
    by_sym: Dict[str, List[float]] = defaultdict(list)
    for t in closed:
        sym = t.get("symbol") or "?"
        by_sym[sym].append(float(t.get("pnl_pct", 0) or 0))
    ts_list = sorted([int(t.get("entry_ts", 0)) for t in closed if t.get("entry_ts")])
    ex_list = sorted([int(t.get("exit_ts", 0)) for t in closed if t.get("exit_ts")])
    years = max(0.01, (ex_list[-1] - ts_list[0]) / (86400.0 * 365.25)) if ts_list and ex_list else 0.01
    metrics = metrics_guard.standard_metric_set(by_sym, years)
    chrono = sorted(closed, key=lambda t: int(t.get("exit_ts", 0)))
    eq, cum = [], 0.0
    for t in chrono:
        cum += float(t.get("pnl_pct", 0) or 0)
        eq.append(cum)
    peak = -1e18; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        if peak - v > max_dd: max_dd = peak - v
    pool_sharpe = float(metrics["pool_sharpe"])
    trades = int(metrics["trades"])
    inflated = abs(pool_sharpe) > metrics_guard.PER_SYM_SHARPE_CAP and trades < 5000
    tags = []
    if inflated:
        tags.append(f"INFLATED ({pool_sharpe:.2f} >5 with {trades} trades)")
    n_syms_v = int(metrics["n_syms"])
    floor = metrics_guard.MIN_SYMS_STOCKS if account in STOCK_ACCOUNT_KEYS else metrics_guard.MIN_SYMS_CRYPTO
    if n_syms_v < floor:
        tags.append(f"DIAGNOSTIC (n_syms={n_syms_v}/{floor})")
    result = {
        "account": account,
        "trades": trades,
        "n_syms": n_syms_v,
        "years": round(years, 4),
        "pool_sharpe": round(pool_sharpe, 4),
        "pool_sharpe_per_trade": round(pool_sharpe, 4),  # alias
        "sym_sharpe": round(float(metrics["sym_sharpe"]), 4),
        "avg_gain_trade": round(float(metrics["avg_gain_trade"]), 4),
        "gain_per_yr": round(float(metrics["gain_per_yr"]), 2),
        "gain_sym_yr": round(float(metrics["gain_sym_yr"]), 4),
        "max_dd_pct": round(max_dd, 4),
        "total_gain_pct": round(float(metrics.get("total_gain_pct", 0)), 2),
        "tier": metrics_guard.tier_name(pool_sharpe),
        "inflated": inflated,
        "tags": tags,
    }
    _acct_pool_cache[account] = (time.time(), result)
    return result


_sweep_status_cache: Tuple[float, Dict[str, Any]] = (0.0, {})
_SWEEP_STATUS_TTL_SEC = 60  # seconds — page polls this; want freshness


def _read_sweep_status_from_disk() -> Dict[str, Any]:
    """Walk autonomous winners JSONLs (S1+S2 mirrored to MB local _s1/_s2),
    apply same canonical audit as flz_dashboard, return concise summary +
    publishable top-5 + sweep-process status (best-effort SSH probe with
    short timeout)."""
    import subprocess as _sp
    base = BASE_PATH / "data" / "autonomous"
    findings: List[Dict[str, Any]] = []
    if base.exists():
        from collections import Counter
        for p in base.rglob("autonomous_*_winners.jsonl"):
            s = str(p)
            if "_legacy_unverified" in s or "_NOLIES_HOLD_" in s:
                continue
            mode = "tradier" if "tradier" in s.lower() else ("crypto" if "crypto" in s.lower() else "?")
            n_syms_hint = None
            for part in p.parts:
                m = re.search(r"_(\d+)sym", part)
                if m:
                    n_syms_hint = int(m.group(1))
                    break
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    rec["_mode"] = mode
                    rec["_pool"] = p.parent.parent.name
                    rec["_n_syms_hint"] = n_syms_hint
                    findings.append(rec)
            except Exception:
                continue
    # Apply canonical filters: pool_sharpe in publishable range,
    # n_syms hint ≥ floor, trades/sym ≥ 30 if n_syms known.
    def _is_publishable(r):
        ps = float(r.get("pool_sharpe", 0) or 0)
        sym_s = float(r.get("sym_sharpe", 0) or 0)
        tr = int(r.get("trades", 0) or 0)
        n = r.get("_n_syms_hint") or 0
        mode = r.get("_mode")
        floor = metrics_guard.MIN_SYMS_STOCKS if mode == "tradier" else metrics_guard.MIN_SYMS_CRYPTO
        if abs(ps) > metrics_guard.PER_SYM_SHARPE_CAP and tr < 5000:
            return False  # inflated
        if n < floor:
            return False
        if n and tr / n < metrics_guard.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE:
            return False
        return True
    publishable = [r for r in findings if _is_publishable(r)]
    publishable.sort(key=lambda r: float(r.get("pool_sharpe", 0)), reverse=True)
    top = publishable[:10]
    # Probe S1+S2 for live sweep procs (short timeout)
    procs = {}
    for host in ("s1-int", "s2-int"):
        try:
            r = _sp.run(["ssh", "-o", "ConnectTimeout=2", "-o", "BatchMode=yes",
                         host, "pgrep -afc 'autonomous_search\\|v8_quick_sweep'"],
                        capture_output=True, text=True, timeout=5)
            procs[host] = int((r.stdout or "0").strip() or 0)
        except Exception:
            procs[host] = -1  # unknown
    return {
        "n_iters_total": len(findings),
        "n_publishable": len(publishable),
        "n_diagnostic": len(findings) - len(publishable),
        "top_10": [
            {
                "tier": metrics_guard.tier_name(float(r.get("pool_sharpe", 0))),
                "mode": r.get("_mode"),
                "pool_sharpe": round(float(r.get("pool_sharpe", 0) or 0), 4),
                "sym_sharpe": round(float(r.get("sym_sharpe", 0) or 0), 4),
                "trades": int(r.get("trades", 0) or 0),
                "n_syms": r.get("_n_syms_hint"),
                "acc_gain_pct": round(float(r.get("acc_gain_pct", 0) or 0), 2),
                "max_dd_pct": round(float(r.get("max_dd_pct", 0) or 0), 2),
                "pool": r.get("_pool"),
            }
            for r in top
        ],
        "live_sweep_procs": procs,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }


def _read_top_configs_with_overrides(limit: int = 10):
    """Same audit as _read_sweep_status_from_disk but ALSO carry the
    `overrides` dict for each surviving iter so the user can see what
    strategy choices differ between configs."""
    base = BASE_PATH / "data" / "autonomous"
    findings: List[Dict[str, Any]] = []
    if base.exists():
        for p in base.rglob("autonomous_*_winners.jsonl"):
            s = str(p)
            if "_legacy_unverified" in s or "_NOLIES_HOLD_" in s:
                continue
            mode = "tradier" if "tradier" in s.lower() else ("crypto" if "crypto" in s.lower() else "?")
            n_syms_hint = None
            for part in p.parts:
                m = re.search(r"_(\d+)sym", part)
                if m:
                    n_syms_hint = int(m.group(1))
                    break
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    rec["_mode"] = mode
                    rec["_pool"] = p.parent.parent.name
                    rec["_worker"] = p.parent.name
                    rec["_n_syms_hint"] = n_syms_hint
                    findings.append(rec)
            except Exception:
                continue
    def _is_publishable(r):
        ps = float(r.get("pool_sharpe", 0) or 0)
        tr = int(r.get("trades", 0) or 0)
        n = r.get("_n_syms_hint") or 0
        mode = r.get("_mode")
        floor = metrics_guard.MIN_SYMS_STOCKS if mode == "tradier" else metrics_guard.MIN_SYMS_CRYPTO
        if abs(ps) > metrics_guard.PER_SYM_SHARPE_CAP and tr < 5000:
            return False
        if n < floor:
            return False
        if n and tr / n < metrics_guard.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE:
            return False
        return True
    pub = [r for r in findings if _is_publishable(r)]
    pub.sort(key=lambda r: float(r.get("pool_sharpe", 0)), reverse=True)
    return pub[:limit]


@app.route("/canonical_top")
def canonical_top_html():
    """Side-by-side HTML comparison of top-10 publishable configs.
    Every override that DIFFERS between top-10 highlighted; common keys collapsed.
    For 'point out what is wrong' use case."""
    mode_filter = request.args.get("mode")
    top = _read_top_configs_with_overrides(limit=20)
    if mode_filter in ("crypto", "tradier"):
        top = [r for r in top if r.get("_mode") == mode_filter]
    top = top[:10]
    # Collect every override key seen; mark which differ.
    all_keys: Dict[str, set] = {}
    for r in top:
        for k, v in (r.get("overrides") or {}).items():
            if k.startswith("_"):
                continue
            all_keys.setdefault(k, set()).add(repr(v))
    diff_keys = sorted([k for k, vs in all_keys.items() if len(vs) > 1])
    common_keys = sorted([k for k, vs in all_keys.items() if len(vs) == 1])
    rows_html = ""
    for i, r in enumerate(top, 1):
        ovr = r.get("overrides") or {}
        ps = float(r.get("pool_sharpe", 0) or 0)
        tier = metrics_guard.tier_name(ps)
        tier_color = {"Discard": "#dc6c6c", "Noise": "#888", "Directional": "#cdb86c",
                      "Best-of-current": "#7fb069", "Strong": "#3fb950", "Aspirational": "#58a6ff"}.get(tier, "#888")
        diff_cells = "".join(
            f'<td title="{html_escape(k)}">{html_escape(str(ovr.get(k, "—")))}</td>'
            for k in diff_keys
        )
        rows_html += (
            f"<tr><td>#{i}</td>"
            f"<td><span style='color:{tier_color}'>{tier}</span></td>"
            f"<td>{r.get('_mode')}</td>"
            f"<td>{ps:+.4f}</td>"
            f"<td>{float(r.get('sym_sharpe', 0)):+.4f}</td>"
            f"<td>{int(r.get('trades', 0)):,}</td>"
            f"<td>{r.get('_n_syms_hint') or '?'}</td>"
            f"<td>{float(r.get('acc_gain_pct', 0)):+.1f}%</td>"
            f"<td>{float(r.get('max_dd_pct', 0)):.1f}%</td>"
            f"<td title='{html_escape(r.get('_pool', ''))}/{html_escape(r.get('_worker', ''))}' style='font-size:.78em;color:#7a8590'>{html_escape((r.get('_pool') or '')[:18])}</td>"
            f"{diff_cells}</tr>\n"
        )
    diff_th = "".join(f'<th title="{html_escape(k)}">{html_escape(k[:20])}</th>' for k in diff_keys)
    common_html = "<br>".join(f"<code>{html_escape(k)}</code> = <code>{html_escape(repr(next(iter(all_keys[k]))))}</code>" for k in common_keys[:80])
    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>canonical top-{len(top)} configs · audited</title>
<style>
  body {{font-family:-apple-system,'SF Mono',Menlo,monospace;background:#0d1117;color:#c9d1d9;padding:14px;}}
  h1 {{font-size:1.2em;color:#58a6ff;margin-bottom:6px}}
  .audit {{background:#1c2837;border:1px solid #2f4f6f;padding:8px 12px;border-radius:5px;margin-bottom:12px;font-size:.86em;color:#a4b8d0}}
  table {{border-collapse:collapse;font-size:.78em;width:100%}}
  th, td {{padding:4px 7px;border-bottom:1px solid #21262d;text-align:left;white-space:nowrap}}
  th {{background:#161b22;color:#8b949e;position:sticky;top:0}}
  tr:hover {{background:#1c2129}}
  .common {{margin-top:14px;padding:10px;background:#161b22;border:1px solid #30363d;border-radius:5px;font-size:.78em;color:#a4b8d0}}
  .common code {{color:#dcc26b}}
  a {{color:#58a6ff}}
</style></head><body>
<h1>canonical top-{len(top)} configs <span style="color:#7a8590;font-size:.7em">— audited via metrics_guard, sample-floor + density enforced</span></h1>
<div class="audit">
  Each row = one publishable backtest iteration. <b>Differing override columns highlighted</b> — those are
  the strategy choices that change between top configs. <b>Common overrides</b> (same value across all
  top-{len(top)}) are listed below the table.
  Use this view to compare strategies and identify what's working / what's wrong.
  <a href="/">← back to chart</a> &nbsp;|&nbsp; <a href="http://127.0.0.1:5057/" target="_blank">full dashboard 5057</a>
  &nbsp;|&nbsp; mode filter: <a href="/canonical_top">all</a> · <a href="/canonical_top?mode=crypto">crypto</a> · <a href="/canonical_top?mode=tradier">tradier</a>
</div>
<table>
<thead><tr>
  <th>#</th><th>tier</th><th>mode</th><th>pool_sharpe</th><th>sym_sharpe</th><th>trades</th><th>n_syms</th><th>gain</th><th>dd</th><th>pool</th>
  {diff_th}
</tr></thead>
<tbody>{rows_html}</tbody>
</table>

<div class="common">
  <b>Common overrides (same value across all top-{len(top)}):</b><br>{common_html or "(none)"}
</div>
</body></html>"""
    return page


def html_escape(s):
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@app.route("/sweep_status")
def sweep_status_route():
    """Surface canonical sweep status + top-10 publishable on 5077.
    Uses 60s cache to keep the chart page snappy."""
    global _sweep_status_cache
    now = time.time()
    ts, data = _sweep_status_cache
    if (now - ts) < _SWEEP_STATUS_TTL_SEC and data:
        return jsonify({**data, "_cache_age_sec": int(now - ts)})
    data = _read_sweep_status_from_disk()
    _sweep_status_cache = (now, data)
    return jsonify({**data, "_cache_age_sec": 0})


@app.route("/account_pool_stats")
def account_pool_stats_route():
    """?accounts=inf,flz,...  Returns canonical aggregate pool_sharpe across
    ALL symbols per account. Replacement for the misleading single-symbol
    'Live [inf]: 0' that's been there forever."""
    accts_raw = request.args.get("accounts", ",".join(ACCOUNTS))
    accts = [a.strip() for a in accts_raw.split(",") if a.strip() and a.strip() in ACCOUNTS]
    out = {a: _account_pool_stats(a) for a in accts}
    return jsonify({"per_account": out, "generated_utc": datetime.now(timezone.utc).isoformat()})


@app.route("/best_runs_for_sym")
def best_runs_for_sym():
    """Top-N backtest runs that actually have trades for ?sym=X, ranked by
    pool_sharpe of the run on that specific symbol (sym-level Sharpe, capped ±5).
    Used by the chart to one-click-overlay the best backtest results onto live.

    Output: [{run, sym_sharpe, trades, total_gain_pct, wr, gain_per_year_pct, machine, category}]
    """
    sym = request.args.get("sym", "").upper()
    n = int(request.args.get("n", 3))
    if not sym:
        return jsonify({"error": "sym required"}), 400
    rows: List[Dict[str, Any]] = []
    for run, s, p in _iter_all_trade_files():
        if s.upper() != sym:
            continue
        agg = _file_aggregates(p)
        if not agg or agg["n"] < 5:
            continue
        machine, category = _classify_run(run, [s])
        rows.append({
            "run": run,
            "machine": machine,
            "category": category,
            "trades": agg["n"],
            "wr": round(agg["wr"], 3),
            "total_gain_pct": round(agg["total_gain"], 2),
            "sym_sharpe": round(agg["sym_sharpe_capped"], 4),
            "ts_first": agg["ts_first"],
            "ts_last": agg["ts_last"],
            "gain_per_year_pct": round(
                agg["total_gain"] / max(0.01, (agg["ts_last"] - agg["ts_first"]) / (86400.0 * 365.25)),
                1,
            ),
        })
    rows.sort(key=lambda r: -r["sym_sharpe"])
    return jsonify({"sym": sym, "n_runs": len(rows), "top": rows[:n]})


@app.route("/live_status")
def live_status():
    """Per-account file-counts for both crypto (data/history) and stocks
    (data/tradier/history). Lets the chart show which accounts have data and
    where it lives — instead of silently empty panes."""
    out: Dict[str, Any] = {}
    for acct in ACCOUNTS:
        base = _history_dir_for(acct)
        n_files = 0
        n_syms = 0
        if base.exists():
            try:
                files = list(base.glob("*.jsonl"))
                n_files = len(files)
                n_syms = len({p.stem.rsplit("_", 1)[0] for p in files})
            except Exception:
                pass
        out[acct] = {
            "history_dir": str(base),
            "exists": base.exists(),
            "n_files": n_files,
            "n_symbols": n_syms,
            "kind": "stocks" if acct in STOCK_ACCOUNT_KEYS else "crypto",
        }
    return jsonify(out)


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
        path = _resolve_trade_path(run, sym)
        if path is None or not path.exists():
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
            "pool_sharpe": (sum(pnls) / n) / std if std > 0 else 0.0,
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
        path = _resolve_trade_path(run, sym)
        if path is None or not path.exists(): return []
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
                "avg_pnl_pct": sum(pnls) / n if n else 0, "pool_sharpe": (sum(pnls)/n)/std if std > 0 else 0}

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


CRYPTO_ACCOUNTS = {"ang", "inf", "flz", "men", "fin"}
STOCK_ACCOUNTS = {"trb", "trc"}

CATEGORY_DESCRIPTIONS = {
    "7D_agent_crypto": "🔴 7D AGENT (NEW system) — agent-based trading on inf, fin (crypto). Hourly 7-day recalculations. Promoted config ACTIVATES LIVE IMMEDIATELY. FIRST PRIORITY to monitor.",
    "7D_agent_stocks": "🔴 7D AGENT (NEW system) — agent-based trading on trc (stocks). Hourly 7-day recalculations. Promoted config ACTIVATES LIVE IMMEDIATELY. FIRST PRIORITY to monitor.",
    "legacy_hourly_testing": "Legacy hourly_reconfig runs for non-agent accounts (flz, trb, men, ang). Old testing infrastructure — DO NOT confuse with the 7D agent system. Kept for historical comparison only.",
    "v3_paper": "Scalp V3 paper-trading variants (data/scalp_v3_paper). Live decisions feed but no real-money execution.",
    "v3_shadow": "Scalp V3 shadow A/B variants (data/scalp_v3_shadow). Each variant runs live in shadow with different switches; decisions logged but not executed.",
    "s1_crypto_canonical": "S1 crypto canonical top-N replays (canon_crypto_*). Top-ranked autonomous-search winners replayed via populate_canonical_top10.",
    "s1_crypto_vec": "S1 crypto vectorized backtests (vec_p*). Quick-engine sweeps on the v8 NPZ.",
    "s1_crypto_bigsweep": "S1 crypto big-sweep canonical 48-sym universes (canonical_48sym_*).",
    "s2_stocks_canonical": "S2 stocks canonical top-N replays (canon_tradier_*). Top-ranked autonomous-search winners for tradier.",
    "s2_stocks_bigsweep": "S2 stocks big-sweep canonical replays.",
    "legacy_other": "Legacy / uncategorized runs.",
}


def _classify_run(run: str, syms: List[str]) -> Tuple[str, str]:
    """Return (machine, category_key) for a run.
    machine ∈ {"macbook", "s1", "s2"}.
    """
    rl = run.lower()
    syms_set = {s.upper() for s in (syms or [])}
    is_stocks = bool(syms_set) and not any(s.endswith(("USDC", "USDT", "USD", "BTC", "ETH")) for s in syms_set)
    is_crypto = bool(syms_set) and any(s.endswith(("USDC", "USDT")) for s in syms_set)
    if run.startswith("hr_"):
        try:
            acct = run.split("::", 1)[0].split("_", 1)[1]
        except Exception:
            acct = ""
        if acct in SEVEN_D_AGENT_ACCOUNTS:
            if acct in STOCK_ACCOUNTS:
                return ("macbook", "7D_agent_stocks")
            return ("macbook", "7D_agent_crypto")
        # Non-agent hr_* runs (flz, trb, men, ang) are legacy hourly testing —
        # NOT the new 7D agent system. Keep separate per user instruction.
        return ("macbook", "legacy_hourly_testing")
    if rl.startswith("v3_paper::") or rl.startswith("paper_"):
        return ("macbook", "v3_paper")
    if rl.startswith("v3_shadow::"):
        return ("macbook", "v3_shadow")
    if run.startswith("canon_tradier_"):
        return ("s2", "s2_stocks_canonical")
    if run.startswith("canon_crypto_"):
        return ("s1", "s1_crypto_canonical")
    if run.startswith("vec_"):
        if is_stocks:
            return ("s2", "s2_stocks_canonical")
        return ("s1", "s1_crypto_vec")
    if run.startswith("bigsweep::"):
        if is_stocks:
            return ("s2", "s2_stocks_bigsweep")
        return ("s1", "s1_crypto_bigsweep")
    if is_stocks:
        return ("s2", "legacy_other")
    if is_crypto:
        return ("s1", "legacy_other")
    return ("s1", "legacy_other")


def _v3_paper_runs() -> List[Dict[str, Any]]:
    """Surface v3 paper/shadow as virtual runs (no chart trade arrows yet —
    PAPER_ENTRY/V3_EXIT events use a different format; show as catalog entries
    with a description block so the user sees them next to backtest runs)."""
    out: List[Dict[str, Any]] = []
    paper_dir = BASE_PATH / "data" / "scalp_v3_paper"
    if paper_dir.exists():
        variants: Dict[str, List[Path]] = {}
        for p in paper_dir.glob("trades_*.jsonl"):
            stem = p.stem  # trades_<variant>_<date> OR trades_<date>
            tail = stem[len("trades_"):]
            parts = tail.rsplit("_", 1)
            variant = parts[0] if len(parts) == 2 and parts[1].isdigit() else "default"
            variants.setdefault(variant, []).append(p)
        for variant, paths in sorted(variants.items()):
            paths.sort(key=lambda x: x.stat().st_mtime)
            n_events = 0
            ts_first = ts_last = 0
            for p in paths:
                try:
                    lines = p.read_text().splitlines()
                except Exception:
                    continue
                n_events += len([l for l in lines if l.strip()])
            try:
                mt = max(p.stat().st_mtime for p in paths)
                ts_first = int(min(p.stat().st_mtime for p in paths))
                ts_last = int(mt)
            except Exception:
                mt = 0
            out.append({
                "run": f"v3_paper::{variant}",
                "category": "v3_paper",
                "machine": "macbook",
                "description": f"V3 paper variant: {variant}. {len(paths)} day-files, {n_events} events.",
                "n_events": n_events,
                "mtime": int(mt),
                "ts_first": ts_first,
                "ts_last": ts_last,
                "files": [str(p) for p in paths],
            })
    shadow_dir = BASE_PATH / "data" / "scalp_v3_shadow"
    if shadow_dir.exists():
        variants_s: Dict[str, List[Path]] = {}
        for p in shadow_dir.glob("*_decisions_*.jsonl"):
            stem = p.stem  # <variant>_decisions_<date>
            idx = stem.find("_decisions_")
            if idx <= 0:
                continue
            variant = stem[:idx]
            variants_s.setdefault(variant, []).append(p)
        for variant, paths in sorted(variants_s.items()):
            paths.sort(key=lambda x: x.stat().st_mtime)
            n_events = 0
            for p in paths:
                try:
                    n_events += len([l for l in p.read_text().splitlines() if l.strip()])
                except Exception:
                    pass
            try:
                mt = max(p.stat().st_mtime for p in paths)
            except Exception:
                mt = 0
            out.append({
                "run": f"v3_shadow::{variant}",
                "category": "v3_shadow",
                "machine": "macbook",
                "description": f"V3 shadow variant: {variant}. {len(paths)} day-files, {n_events} decisions.",
                "n_events": n_events,
                "mtime": int(mt),
                "files": [str(p) for p in paths],
            })
    return out


@app.route("/run_unique")
def run_unique():
    """Deduplicated test list — one row per unique config result.

    Dedup key: (pool_sharpe rounded 3, sym_sharpe rounded 3, trades, n_syms).
    Optional ?sym=X — when given, also computes per-symbol stats by reading
    /tmp/v8_trades/<run>__<sym>.jsonl + extra roots, surfacing only tests that
    actually have trades for that symbol.

    Returns a clean list, sorted by:
      1. Has trades on requested sym (if sym given) DESC
      2. pool_sharpe DESC
    Each entry: {run, pool_sharpe, sym_sharpe (overall), trades, n_syms,
                 years, tier, sym_trades (on requested sym), sym_pool_sharpe (on
                 requested sym), sym_dd_pct (on requested sym), primary_sym}.
    """
    target_sym = (request.args.get("sym") or "").upper()
    with _precompute_lock:
        rr_body = _precomputed.get("runs_ranked")
    runs_data: List[Dict[str, Any]] = []
    if rr_body:
        try:
            runs_data = json.loads(rr_body)
        except Exception:
            runs_data = []
    seen_keys: set = set()
    deduped: List[Dict[str, Any]] = []
    for r in runs_data:
        ps = round(float(r.get("pool_sharpe", 0) or 0), 3)
        ss = round(float(r.get("sym_sharpe", 0) or 0), 3)
        tr = int(r.get("trades", 0) or 0)
        ns = int(r.get("n_syms", 0) or 0)
        key = (ps, ss, tr, ns)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(r)
    out: List[Dict[str, Any]] = []
    for r in deduped:
        run = r.get("run", "")
        ps = float(r.get("pool_sharpe", 0) or 0)
        trades = int(r.get("trades", 0) or 0)
        n_syms = int(r.get("n_syms", 0) or 0)
        n_years = float(r.get("n_years", 0) or 0)
        tier = metrics_guard.tier_name(ps)
        sym_stats = None
        primary_sym = None
        if target_sym:
            jsonl_path = _find_run_trades_for_sym(run, target_sym)
            if jsonl_path and jsonl_path.exists():
                pnls = []
                try:
                    for ln in jsonl_path.read_text(encoding="utf-8").splitlines():
                        if not ln.strip():
                            continue
                        try:
                            t = json.loads(ln)
                        except Exception:
                            continue
                        pnls.append(float(t.get("pnl_pct", 0) or 0))
                except Exception:
                    pass
                if pnls:
                    avg = sum(pnls) / len(pnls)
                    sd = (sum((x - avg) ** 2 for x in pnls) / len(pnls)) ** 0.5
                    sym_ps = (avg / sd) if sd > 0 else 0.0
                    eq = []
                    cum = 0.0
                    peak = -1e18
                    max_dd = 0.0
                    for p in pnls:
                        cum += p
                        eq.append(cum)
                        if cum > peak:
                            peak = cum
                        if peak - cum > max_dd:
                            max_dd = peak - cum
                    wins = sum(1 for p in pnls if p > 0)
                    sym_stats = {
                        "sym_trades": len(pnls),
                        "sym_pool_sharpe": round(sym_ps, 4),
                        "sym_dd_pct": round(max_dd, 3),
                        "sym_total_gain_pct": round(sum(pnls), 2),
                        "sym_wr": round(wins / len(pnls), 3),
                        "sym_tier": metrics_guard.tier_name(sym_ps),
                        "sym_inflated": abs(sym_ps) > 5 and len(pnls) < 5000,
                    }
        # primary sym = first sym in the registry (best-effort)
        syms = r.get("syms", []) or []
        if syms:
            primary_sym = syms[0]
        out.append({
            "run": run,
            "tier": tier,
            "pool_sharpe": round(ps, 4),
            "sym_sharpe": round(float(r.get("sym_sharpe", 0) or 0), 4),
            "avg_gain_trade": round(float(r.get("avg_gain_trade", 0) or 0), 4),
            "gain_per_yr": round(float(r.get("gain_per_yr", 0) or 0), 1),
            "trades": trades,
            "n_syms": n_syms,
            "years": round(n_years, 2),
            "primary_sym": primary_sym,
            "sym_target": target_sym or None,
            "sym_stats": sym_stats,  # None if no trades on target_sym
            "has_trades_on_sym": bool(sym_stats and sym_stats.get("sym_trades", 0) > 0),
        })
    # MODE filter: crypto sym → drop stock-acct runs; stock sym → drop crypto-acct runs.
    # 2026-05-01: user "diff a/b mentioning stocks absolutely irrelevant in BTC context" — fix.
    is_crypto_sym = bool(re.search(r"(USDC|USDT|BUSD|FDUSD|TUSD|USDP)$", target_sym or "", re.I))
    is_stock_sym = bool(target_sym) and not is_crypto_sym
    def _matches_mode(r):
        run = r["run"]
        if is_crypto_sym:
            # exclude stock-acct runs (hr_trb/hr_trc/hr_tra) and tradier canon
            if run.startswith(("hr_trb::", "hr_trc::", "hr_tra::")): return False
            if "canon_tradier" in run or "tradier_" in run: return False
            return True
        if is_stock_sym:
            # exclude crypto-acct runs and crypto canon
            if run.startswith(("hr_fin::", "hr_flz::", "hr_inf::", "hr_ang::", "hr_men::")): return False
            if "canon_crypto" in run or "crypto_" in run.lower(): return False
            return True
        return True
    if target_sym:
        out = [r for r in out if _matches_mode(r)]
    # Sort: tests-with-actual-sym-trades first, then by sym_pool_sharpe (the relevant metric on this chart),
    # then by overall pool_sharpe.
    if target_sym:
        out.sort(key=lambda r: (
            r["has_trades_on_sym"],
            (r.get("sym_stats") or {}).get("sym_pool_sharpe", -999) if r["has_trades_on_sym"] else -999,
            r["pool_sharpe"],
        ), reverse=True)
    else:
        out.sort(key=lambda r: r["pool_sharpe"], reverse=True)
    # Hard cap top-50 by default for clean view.
    cap = int(request.args.get("limit", "50"))
    capped = out[:cap]
    return jsonify({
        "n_unique_total": len(out),
        "n_returned": len(capped),
        "n_with_trades_on_sym": sum(1 for r in out if r["has_trades_on_sym"]),
        "sym_target": target_sym or None,
        "sym_mode": "crypto" if is_crypto_sym else ("stocks" if is_stock_sym else "unknown"),
        "tests": capped,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    })


def _find_run_trades_for_sym(run: str, sym: str) -> Optional[Path]:
    """Delegate to chart_server's existing _resolve_trade_path which knows about
    the hr_<acct>::/bigsweep:: prefixes and the registry."""
    return _resolve_trade_path(run, sym)


@app.route("/symbol_config")
def symbol_config_page():
    """Per-symbol config archive viewer (user 2026-05-01 spec):
       data/symbol_configs/<SYM>/  — BEST.json (current ultimate), HISTORY.csv (timeline),
       config_<cycle>__<SIDE>.json (per-cycle snapshots).
    Built by build_symbol_configs.py from hourly_reconfig output."""
    sym = (request.args.get("sym") or "BTCUSDC").upper()
    cfg_dir = BASE_PATH / "data" / "symbol_configs" / sym
    if not cfg_dir.exists():
        # List available syms
        configs_root = BASE_PATH / "data" / "symbol_configs"
        avail = sorted([d.name for d in configs_root.iterdir() if d.is_dir() and not d.name.startswith("_")]) if configs_root.exists() else []
        opts = "".join(f'<option value="{s}">{s}</option>' for s in avail[:200])
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>symbol config</title>
<style>body{{font-family:-apple-system,Menlo,monospace;background:#0d1117;color:#c9d1d9;padding:14px}}
h1{{color:#58a6ff}}.empty{{color:#7a8590;padding:20px}}select{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:5px 9px;font-size:13px}}
a{{color:#58a6ff}}</style></head><body>
<h1>symbol config — pick a symbol</h1>
<p class="empty">No config archive for <b>{html_escape(sym)}</b>. Available ({len(avail)}):</p>
<select onchange="window.location.href='/symbol_config?sym='+this.value">{opts}</select>
&nbsp;<a href="/live">← live</a>
</body></html>"""
    best = {}
    if (cfg_dir / "BEST.json").exists():
        try:
            best = json.loads((cfg_dir / "BEST.json").read_text())
        except Exception:
            pass
    history = []
    hist_path = cfg_dir / "HISTORY.csv"
    if hist_path.exists():
        try:
            with hist_path.open("r", encoding="utf-8") as f:
                rd = csv.DictReader(f)
                for row in rd:
                    history.append(row)
        except Exception:
            pass
    history.sort(key=lambda r: r.get("cycle_iso", ""), reverse=True)
    cycles = sorted([p.name for p in cfg_dir.glob("config_*.json")], reverse=True)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html_escape(sym)} — config archive</title>
<style>
body{{font-family:-apple-system,'SF Mono',Menlo,monospace;background:#0d1117;color:#c9d1d9;padding:14px;font-size:13px}}
h1{{color:#58a6ff;margin:0 0 8px;font-size:1.1em}}
h2{{color:#dcc26b;font-size:.95em;border-bottom:1px solid #21262d;padding-bottom:3px;margin:16px 0 6px}}
.tier-Discard{{color:#dc6c6c}} .tier-Noise{{color:#888}} .tier-Directional{{color:#cdb86c}}
.tier-Best-of-current{{color:#7fb069;background:#1a2617;padding:1px 5px;border-radius:3px}}
.tier-Strong{{color:#3fb950;background:#0f1f15;padding:1px 5px;border-radius:3px}}
.tier-Aspirational{{color:#58a6ff;background:#0f1c2e;padding:1px 5px;border-radius:3px;font-weight:700}}
table{{width:100%;border-collapse:collapse;font-size:11.5px}}
th{{background:#161b22;color:#8b949e;padding:5px 8px;text-align:left;font-weight:600;font-size:10.5px}}
td{{padding:4px 8px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums}}
tr:hover{{background:#1c2129}}
.pos{{color:#3fb950}} .neg{{color:#f85149}}
.audit{{background:#1c2837;border:1px solid #2f4f6f;border-radius:4px;padding:7px 11px;font-size:11.5px;color:#a4b8d0;margin-bottom:10px}}
.best-block{{background:#161b22;border:1px solid #30363d;border-radius:5px;padding:10px;margin-bottom:8px}}
.best-block .side{{font-weight:700;color:#dcc26b;font-size:13px;margin-bottom:4px}}
.best-block code{{background:#0d1117;color:#dcc26b;padding:1px 6px;border-radius:3px;font-size:11px}}
.kv{{display:grid;grid-template-columns:max-content 1fr;gap:6px 12px;font-size:11px;margin-top:4px}}
.kv .k{{color:#7a8590}}
a{{color:#58a6ff;text-decoration:none}} a:hover{{text-decoration:underline}}
.toolbar{{margin-bottom:10px;font-size:11.5px}}
select{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:3px 7px;border-radius:3px;font-size:12px}}
</style></head><body>
<div class="toolbar">
  <a href="/live">← live</a> ·
  <a href="/clean_runs?sym={html_escape(sym)}">→ all tests on {html_escape(sym)}</a> ·
  <a href="/?sym={html_escape(sym)}">→ chart {html_escape(sym)}</a>
  &nbsp;|&nbsp;
  switch sym <select onchange="window.location.href='/symbol_config?sym='+this.value">
""" + "".join(f'<option value="{s}" {"selected" if s == sym else ""}>{s}</option>' for s in sorted({h['symbol'] if 'symbol' in h else sym for h in history} | {sym} | {p.name for p in (BASE_PATH/'data'/'symbol_configs').iterdir() if p.is_dir() and not p.name.startswith('_')})) + f"""</select>
</div>

<h1>{html_escape(sym)} — config archive
  <span style="color:#7a8590;font-size:.78em;font-weight:normal">— per-symbol BEST + HISTORY built from 7D hourly winners</span>
</h1>
<div class="audit">
  Updated by <code>build_symbol_configs.py</code> from <code>data/hourly_reconfig/&lt;acct&gt;/runs/&lt;cycle&gt;/</code> outputs.
  Each cycle (≈hourly) the producer tests ~100 variations on this symbol's tradeable_keys; the winner
  per side (LONG/SHORT) is captured here. <b>weighted_pool_sharpe</b> uses 2x/day decay
  (today=1.0, 6d ago=1/64). The <b>BEST.json</b> is the current ultimate config to use right now.
</div>

<h2>Current BEST <span style="color:#7a8590;font-size:.78em;font-weight:normal">— last updated {html_escape(best.get("last_updated_utc",""))}</span></h2>
""" + "".join(
        f"""<div class="best-block">
  <div class="side">{html_escape(side)} side
    <span class="tier-{html_escape(blk.get("winner_tier","Noise"))}" style="margin-left:6px;font-size:11.5px">{html_escape(blk.get("winner_tier",""))}</span>
    <span style="color:#7a8590;font-size:11px;margin-left:8px">winner: <code>{html_escape(blk.get("winner_variation","?"))}</code> from <code>{html_escape(blk.get("account","?"))}</code> cycle <code>{html_escape(blk.get("cycle_id","?"))}</code></span>
  </div>
  <div class="kv">
    <span class="k">weighted_pool_sharpe</span><span><b>{float(blk.get("winner_weighted_pool_sharpe",0)):+.4f}</b></span>
    <span class="k">trades (last 7d, weighted)</span><span>{int(blk.get("winner_stats",{}).get("trades",0)):,}</span>
    <span class="k">WR</span><span>{float(blk.get("winner_stats",{}).get("wr",0))*100:.1f}%</span>
    <span class="k">max_dd_pct</span><span class="{"neg" if float(blk.get("winner_stats",{}).get("max_dd_pct",0))>0 else "pos"}">{float(blk.get("winner_stats",{}).get("max_dd_pct",0)):.2f}%</span>
    <span class="k">total_gain_pct</span><span class="{"pos" if float(blk.get("winner_stats",{}).get("total_gain_pct",0))>=0 else "neg"}">{float(blk.get("winner_stats",{}).get("total_gain_pct",0)):+.1f}%</span>
    <span class="k">all variations scored this cycle</span><span>{len(blk.get("all_variations_scored",[]))}</span>
  </div>
  <details style="margin-top:6px"><summary style="cursor:pointer;color:#a4b8d0;font-size:11px">all variations ranked this cycle</summary>
    <table style="margin-top:5px"><thead><tr><th>variation</th><th>weighted_pool_sharpe</th><th>trades</th></tr></thead><tbody>
""" + "".join(
        f'<tr><td><code>{html_escape(v.get("variation","?"))}</code></td><td>{float(v.get("weighted_pool_sharpe",0)):+.4f}</td><td>{int(v.get("trades",0)):,}</td></tr>'
        for v in (blk.get("all_variations_scored") or [])[:30]
    ) + """</tbody></table></details></div>"""
        for side, blk in (best.get("by_side") or {}).items()
    ) + f"""
<h2>HISTORY <span style="color:#7a8590;font-size:.78em;font-weight:normal">— winner each cycle ({len(history)} entries)</span></h2>
<table>
<thead><tr>
<th>cycle</th><th>side</th><th>acct</th><th>winner_variation</th><th>tier</th><th>weighted_pool_sharpe</th><th>trades</th><th>WR</th><th>gain%</th><th>dd</th>
</tr></thead><tbody>
""" + "".join(
        f"""<tr>
        <td>{html_escape(r.get("cycle_iso",""))[:16].replace("T"," ")}</td>
        <td>{html_escape(r.get("side",""))}</td>
        <td>{html_escape(r.get("account",""))}</td>
        <td style="font-size:10.5px"><code>{html_escape(r.get("winner_variation",""))}</code></td>
        <td><span class="tier-{html_escape(r.get("tier","Noise"))}">{html_escape(r.get("tier",""))}</span></td>
        <td><b>{r.get("weighted_pool_sharpe","")}</b></td>
        <td>{r.get("trades","")}</td>
        <td>{round(float(r.get("wr",0))*100,1) if r.get("wr") else 0}%</td>
        <td>{r.get("total_gain_pct","")}</td>
        <td>{r.get("max_dd_pct","")}</td>
        </tr>"""
        for r in history[:60]
    ) + f"""</tbody></table>
<p style="color:#7a8590;font-size:11px;margin-top:8px">{len(cycles)} cycle snapshots stored as <code>config_&lt;cycle&gt;__&lt;SIDE&gt;.json</code> in <code>data/symbol_configs/{html_escape(sym)}/</code></p>

</body></html>"""


@app.route("/test_detail")
def test_detail_page():
    """Per-test detail: per-symbol rollup + overrides (where available) + equity vs B&H.
    User-spec 2026-05-01: 'parameter diff vs baseline + per-symbol rollup'."""
    run = request.args.get("run", "")
    if not run:
        return "<h1>error</h1><p>?run=&lt;run_id&gt; required</p>", 400
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>test detail — {html_escape(run)}</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body{{font-family:-apple-system,'SF Mono',Menlo,monospace;background:#0d1117;color:#c9d1d9;padding:14px;font-size:13px}}
  h1{{font-size:1.0em;color:#58a6ff;margin:0 0 8px;font-weight:700;font-family:'SF Mono',Menlo,monospace;word-break:break-all}}
  h2{{font-size:0.95em;color:#dcc26b;margin:14px 0 6px;border-bottom:1px solid #21262d;padding-bottom:3px}}
  .canonical{{background:#161b22;border:1px solid #30363d;padding:8px 12px;border-radius:5px;margin-bottom:10px;font-size:11.5px;color:#a4b8d0;white-space:pre-wrap;line-height:1.5}}
  table{{border-collapse:collapse;font-size:12px;width:100%;margin-top:4px}}
  th{{background:#161b22;color:#8b949e;padding:5px 8px;text-align:left;font-size:11px;font-weight:600}}
  td{{padding:4px 8px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums}}
  tr:hover{{background:#1c2129;cursor:pointer}}
  .pos{{color:#3fb950}} .neg{{color:#f85149}}
  .tier-Discard{{color:#dc6c6c}} .tier-Noise{{color:#888}} .tier-Directional{{color:#cdb86c}}
  .tier-Best-of-current{{color:#7fb069}} .tier-Strong{{color:#3fb950}} .tier-Aspirational{{color:#58a6ff;font-weight:700}}
  .ovr{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:6px}}
  .ovr-pair{{background:#161b22;border:1px solid #21262d;border-radius:4px;padding:5px 8px;font-size:11.5px;display:flex;justify-content:space-between;gap:8px}}
  .ovr-pair .k{{color:#7a8590;font-size:10.5px}}
  .ovr-pair .v{{color:#dcc26b;font-weight:600}}
  .ovr-pair.diff{{border-color:#3a4a18;background:#1a2410}}
  a{{color:#58a6ff;text-decoration:none}} a:hover{{text-decoration:underline}}
  .toolbar{{margin-bottom:10px;font-size:11.5px}}
  .nodata{{color:#7a8590;font-style:italic;padding:8px 0}}
  .equity-box{{height:280px;background:#161b22;border:1px solid #30363d;border-radius:5px;padding:6px;margin-top:6px}}
</style></head><body>

<div class="toolbar">
  <a href="/clean_runs">← back to all tests</a> ·
  <a href="/live">→ live</a> ·
  <a href="#" id="openChart">→ open on full chart</a>
</div>

<h1 id="runName">{html_escape(run)}</h1>
<div id="canonical" class="canonical">loading…</div>

<h2>Per-symbol rollup
  <span style="font-size:.78em;color:#7a8590;font-weight:normal">— how this test performed broken down by symbol</span>
</h2>
<div id="perSym"><div class="nodata">loading…</div></div>

<h2>Equity curve
  <span style="font-size:.78em;color:#7a8590;font-weight:normal">— this test (cum %) vs Buy &amp; Hold (gray dashed)</span>
  <span style="margin-left:10px;font-size:.85em">on sym
    <select id="eqSym" style="background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:2px 6px;border-radius:3px;font-size:11.5px">
      <option value="">(pick from rollup)</option>
    </select>
  </span>
</h2>
<div id="equityBox" class="equity-box"></div>

<h2>Parameter overrides
  <span style="font-size:.78em;color:#7a8590;font-weight:normal">— what THIS test changes vs config defaults</span>
</h2>
<div id="overrides"><div class="nodata">loading…</div></div>

<script>
const RUN = {json.dumps(run)};
const fmt = (n, d=2) => (n===null||n===undefined||isNaN(n)) ? "—" : Number(n).toFixed(d);
const cls = (n) => Number(n) >= 0 ? "pos" : "neg";
function tierClass(t){{return "tier-" + (t || "Noise")}}
function escapeHtml(s){{return (s===null||s===undefined?"":String(s)).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}}

async function load() {{
  const ri = await fetch("/run_info?run=" + encodeURIComponent(RUN)).then(r=>r.json());
  document.getElementById("canonical").textContent = ri.canonical_line || "(no canonical line)";
  const primarySym = (ri.per_symbol||[])[0]?.sym || "BTCUSDC";
  document.getElementById("openChart").href = `/?sym=${{encodeURIComponent(primarySym)}}&runs=${{encodeURIComponent(RUN)}}`;

  const ps = ri.per_symbol || [];
  if (ps.length) {{
    const head = `<tr>
      <th>symbol</th><th>trades</th><th>sym_sharpe</th><th>tier</th>
      <th>total_gain%</th><th>chained%</th><th>WR%</th></tr>`;
    const rows = ps.map(s => {{
      const ss = Number(s.sym_sharpe||0);
      const tier = (ss < 0) ? "Discard" : (ss < 0.3) ? "Noise" : (ss < 0.6) ? "Directional" :
                   (ss < 1.0) ? "Best-of-current" : (ss < 1.5) ? "Strong" : "Aspirational";
      return `<tr onclick="window.location.href='/?sym=${{encodeURIComponent(s.sym)}}&runs=${{encodeURIComponent(RUN)}}'">
        <td><b>${{escapeHtml(s.sym)}}</b></td>
        <td>${{(s.trades||0).toLocaleString()}}</td>
        <td class="${{cls(ss)}}">${{fmt(ss,4)}}</td>
        <td><span class="${{tierClass(tier)}}">${{tier}}</span></td>
        <td class="${{cls(s.total_gain_pct)}}">${{fmt(s.total_gain_pct,1)}}%</td>
        <td class="${{cls(s.chained_pct)}}">${{fmt((s.chained_pct||0)*100,1)}}%</td>
        <td>${{fmt((s.wr||0)*100,0)}}%</td>
      </tr>`;
    }}).join("");
    document.getElementById("perSym").innerHTML = `<table>${{head}}${{rows}}</table>
      <div style="font-size:11px;color:#7a8590;margin-top:4px">click a row → open the chart with this test's trades on that symbol</div>`;
    const eqSel = document.getElementById("eqSym");
    eqSel.innerHTML = `<option value="">(pick from rollup)</option>` + ps.map(s =>
      `<option value="${{escapeHtml(s.sym)}}">${{escapeHtml(s.sym)}} (${{(s.trades||0).toLocaleString()}}tr)</option>`).join("");
    eqSel.value = ps[0].sym;
    eqSel.onchange = loadEquity;
    loadEquity();
  }} else {{
    document.getElementById("perSym").innerHTML = `<div class="nodata">no per-symbol data — this test may not have produced trades yet</div>`;
  }}

  const ovr = ri.overrides || {{}};
  const ovrKeys = Object.keys(ovr).filter(k => !k.startsWith("_"));
  if (ovrKeys.length) {{
    const html = ovrKeys.sort().map(k => {{
      const v = ovr[k];
      return `<div class="ovr-pair">
        <span class="k">${{escapeHtml(k)}}</span>
        <span class="v">${{escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}}</span>
      </div>`;
    }}).join("");
    document.getElementById("overrides").innerHTML = `<div class="ovr">${{html}}</div>`;
  }} else {{
    document.getElementById("overrides").innerHTML = `
      <div class="nodata">No overrides exposed by this test's metadata.<br><br>
      For <b>hourly-7D tests</b>, the variation name itself encodes the strategy:
      <code>BEST</code> = top-pick from this hour's ~100 variations ·
      <code>BEST_more_trades</code> = relaxed-filter sibling ·
      <code>baseline</code> = unmodified config ·
      <code>extra_btc_&lt;sym&gt;_LONG/SHORT_top</code> = forced-extra-position variant<br><br>
      For <b>bigsweep / autonomous</b> tests, overrides are recorded in the source winner JSONL —
      this run may not have a winner-jsonl source.</div>`;
  }}
}}

async function loadEquity() {{
  const sym = document.getElementById("eqSym").value;
  if (!sym) return;
  const r = await fetch(`/equity_curves?sym=${{encodeURIComponent(sym)}}&runs=${{encodeURIComponent(RUN)}}`).then(r=>r.json()).catch(()=>null);
  if (!r) return;
  const traces = [];
  if (Array.isArray(r.buy_hold) && r.buy_hold.length) {{
    traces.push({{
      x: r.buy_hold.map(p => new Date(p.t*1000)),
      y: r.buy_hold.map(p => p.v),
      mode: "lines", type: "scatter", name: "B&H",
      line: {{color: "#888", width: 1.5, dash: "dash"}},
    }});
  }}
  const runArr = (r.runs || {{}})[RUN] || [];
  if (runArr.length) {{
    traces.push({{
      x: runArr.map(p => new Date(p.t*1000)),
      y: runArr.map(p => p.v),
      mode: "lines", type: "scatter", name: "this test",
      line: {{color: "#58a6ff", width: 2}},
    }});
  }}
  Plotly.newPlot("equityBox", traces, {{
    paper_bgcolor:"#161b22", plot_bgcolor:"#161b22",
    font:{{color:"#c9d1d9", size:10}},
    xaxis:{{gridcolor:"#21262d"}}, yaxis:{{gridcolor:"#21262d", title:"cum %"}},
    margin:{{t:14, r:14, b:30, l:50}}, legend:{{orientation:"h", y:-0.2}},
  }}, {{displayModeBar:false, responsive:true}});
}}

load();
</script>
</body></html>"""


@app.route("/live")
def live_page():
    """Priority page (user 2026-05-01): tabs for live monitoring of 7D-best
    strategies overlaid with ACTUAL live trades. Crypto + stocks separate.
    Backtest analysis lives in a separate tab linked to /clean_runs."""
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>live — 7D priorities</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body{font-family:-apple-system,'SF Mono',Menlo,monospace;background:#0d1117;color:#c9d1d9;margin:0;padding:0;font-size:13px}
  .header{background:#161b22;border-bottom:1px solid #30363d;padding:8px 16px;display:flex;align-items:center;gap:14px}
  h1{font-size:1.1em;color:#58a6ff;margin:0;font-weight:700}
  .tabs{display:flex;gap:0;flex:1}
  .tab{padding:8px 16px;background:#0d1117;border:none;color:#8b949e;cursor:pointer;font-size:13px;font-family:inherit;border-bottom:2px solid transparent}
  .tab.active{color:#58a6ff;border-bottom-color:#58a6ff;font-weight:600}
  .tab:hover{color:#c9d1d9}
  .audit{background:#1c2837;border:1px solid #2f4f6f;padding:6px 12px;border-radius:4px;margin:8px 14px;font-size:11.5px;color:#a4b8d0}
  .audit b{color:#dcc26b}
  .panels{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px 14px}
  .panel{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px;display:flex;flex-direction:column}
  .panel-head{font-size:11.5px;color:#a4b8d0;padding:4px 6px;border-bottom:1px solid #21262d;display:flex;justify-content:space-between;flex-wrap:wrap;gap:4px}
  .panel-head .sym{color:#58a6ff;font-weight:700;font-size:13px}
  .tier-Discard{color:#dc6c6c} .tier-Noise{color:#888} .tier-Directional{color:#cdb86c}
  .tier-Best-of-current{color:#7fb069;background:#1a2617;padding:0 4px;border-radius:3px}
  .tier-Strong{color:#3fb950;background:#0f1f15;padding:0 4px;border-radius:3px}
  .tier-Aspirational{color:#58a6ff;background:#0f1c2e;font-weight:700;padding:0 4px;border-radius:3px}
  .panel-stats{font-size:10.5px;color:#a4b8d0;padding:3px 6px;line-height:1.5}
  .panel-stats .pos{color:#3fb950} .panel-stats .neg{color:#f85149}
  .chart-box{height:280px;width:100%}
  .empty{padding:50px;text-align:center;color:#7a8590;font-size:11px}
  .tab-content{display:none}
  .tab-content.active{display:block}
  a{color:#58a6ff;text-decoration:none}
</style></head><body>

<div class="header">
  <h1>📡 LIVE</h1>
  <div class="tabs">
    <button class="tab active" data-tab="crypto">🟢 Crypto live (7D)</button>
    <button class="tab" data-tab="stocks">🟡 Stocks live (7D)</button>
    <button class="tab" data-tab="backtests">📊 All backtests</button>
  </div>
  <a href="/" style="font-size:11px;color:#7a8590">→ full chart</a>
  <a href="http://127.0.0.1:5057/" target="_blank" style="font-size:11px;color:#dcc26b">→ 5057</a>
</div>

<div class="audit">
  Each panel = priority sym + best 7D backtest config (canonical, audited via metrics_guard) + actual live trades from <b>ang/inf/flz/men/fin</b> (crypto) or <b>trb/trc</b> (stocks).
  <span style="color:#3fb950">●</span> backtest entry · <span style="color:#dc6c6c">●</span> backtest exit ·
  <span style="color:#dcc26b">▲</span> LIVE entry · <span style="color:#58a6ff">▼</span> LIVE exit
</div>

<div id="tab-crypto" class="tab-content active"><div class="panels" id="cryptoPanels"></div></div>
<div id="tab-stocks" class="tab-content"><div class="panels" id="stocksPanels"></div></div>
<div id="tab-backtests" class="tab-content">
  <div class="audit">Click any test to open it on the main chart with its trades pre-loaded. Switch the symbol selector to see tests for other symbols.</div>
  <iframe src="/clean_runs?sym=BTCUSDC" style="width:calc(100% - 28px);height:calc(100vh - 110px);border:0;border-radius:6px;margin:0 14px"></iframe>
</div>

<script>
const CRYPTO_SYMS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC"];
const STOCK_SYMS = ["AAPL", "MSFT", "NVDA", "AMZN"];
const CRYPTO_ACCTS = "ang,inf,flz,men,fin";
const STOCK_ACCTS = "trb,trc,tra";

document.querySelectorAll(".tab").forEach(t => {
  t.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(x => x.classList.remove("active"));
    t.classList.add("active");
    document.getElementById(`tab-${t.dataset.tab}`).classList.add("active");
    if (t.dataset.tab === "stocks" && !document.getElementById("stocksPanels").children.length) loadStocksTab();
  };
});

const fmt = (n, d=2) => (n===null||n===undefined||isNaN(n)) ? "—" : Number(n).toFixed(d);
const cls = (n) => Number(n) >= 0 ? "pos" : "neg";
function tierClass(t){return "tier-" + (t || "Noise")}

async function buildPanel(container, sym, accts) {
  const panel = document.createElement("div");
  panel.className = "panel";
  panel.innerHTML = `<div class="panel-head"><span class="sym">${sym}</span><span style="color:#7a8590">loading…</span></div>
                     <div class="chart-box" id="chart_${sym}"></div>
                     <div class="panel-stats" id="stats_${sym}"></div>`;
  container.appendChild(panel);
  const tf = "1h";
  const end = Math.floor(Date.now() / 1000);
  const start = end - 30 * 86400;
  const klinesP = fetch(`/klines?sym=${sym}&tf=${tf}&start=${start}&end=${end}&max=2000`).then(r=>r.json()).catch(()=>[]);
  const topP = fetch(`/run_unique?sym=${sym}&limit=1`).then(r=>r.json()).catch(()=>({tests:[]}));
  const liveP = fetch(`/historic_trades?sym=${sym}&accounts=${accts}`).then(r=>r.json()).catch(()=>({}));
  const [klines, topR, live] = await Promise.all([klinesP, topP, liveP]);
  const head = panel.querySelector(".panel-head");
  if (!Array.isArray(klines) || klines.length === 0) {
    head.innerHTML = `<span class="sym">${sym}</span><span style="color:#dc6c6c">no NPZ</span>`;
    panel.querySelector(".chart-box").innerHTML = `<div class="empty">no klines for ${sym}</div>`;
    return;
  }
  const top = (topR.tests || [])[0] || null;
  let btTrades = [];
  if (top && top.has_trades_on_sym) {
    const bt = await fetch(`/backtest_trades?run=${encodeURIComponent(top.run)}&sym=${sym}`).then(r=>r.json()).catch(()=>({}));
    btTrades = bt.trades || [];
  }
  const liveEntries = [], liveExits = [];
  for (const acct of accts.split(",")) {
    const trades = (live[acct] || {}).trades || [];
    for (const t of trades) {
      liveEntries.push({x: new Date(t.entry_ts*1000), y: t.entry_price, acct, side: t.side, reason: t.entry_reason});
      liveExits.push({x: new Date(t.exit_ts*1000), y: t.exit_price, acct, side: t.side, reason: t.exit_reason, pnl: t.pnl_pct});
    }
  }
  const xs = klines.map(b => new Date(b.t * 1000));
  const traces = [{
    x: xs,
    open: klines.map(b => b.o), high: klines.map(b => b.h),
    low: klines.map(b => b.l), close: klines.map(b => b.c),
    type: "candlestick", name: "price",
    increasing:{line:{color:"#3fb950",width:1}}, decreasing:{line:{color:"#f85149",width:1}},
    showlegend: false,
  }];
  if (btTrades.length) {
    traces.push({
      x: btTrades.map(t => new Date(t.entry_ts*1000)), y: btTrades.map(t => t.entry_price),
      mode:"markers", type:"scatter", name:"BT entry",
      marker:{color:"#3fb950", size:6, symbol:"circle", opacity:0.7},
      hovertext: btTrades.map(t => `BT entry ${t.side} @ ${fmt(t.entry_price,4)} · ${t.entry_reason||""}`),
      hoverinfo:"text",
    });
    traces.push({
      x: btTrades.map(t => new Date(t.exit_ts*1000)), y: btTrades.map(t => t.exit_price),
      mode:"markers", type:"scatter", name:"BT exit",
      marker:{color:"#dc6c6c", size:5, symbol:"circle-open"},
      hovertext: btTrades.map(t => `BT exit @ ${fmt(t.exit_price,4)} pnl=${fmt(t.pnl_pct,3)}% · ${t.exit_reason||""}`),
      hoverinfo:"text",
    });
  }
  if (liveEntries.length) traces.push({
    x: liveEntries.map(p => p.x), y: liveEntries.map(p => p.y),
    mode:"markers", type:"scatter", name:"LIVE entry",
    marker:{color:"#dcc26b", size:11, symbol:"triangle-up", line:{width:1,color:"#000"}},
    hovertext: liveEntries.map(p => `LIVE ${p.acct} ${p.side} @ ${fmt(p.y,4)} · ${p.reason||""}`),
    hoverinfo:"text",
  });
  if (liveExits.length) traces.push({
    x: liveExits.map(p => p.x), y: liveExits.map(p => p.y),
    mode:"markers", type:"scatter", name:"LIVE exit",
    marker:{color:"#58a6ff", size:11, symbol:"triangle-down", line:{width:1,color:"#000"}},
    hovertext: liveExits.map(p => `LIVE ${p.acct} ${p.side} exit @ ${fmt(p.y,4)} pnl=${fmt(p.pnl,3)}% · ${p.reason||""}`),
    hoverinfo:"text",
  });
  Plotly.newPlot(`chart_${sym}`, traces, {
    paper_bgcolor:"#161b22", plot_bgcolor:"#161b22",
    font:{color:"#c9d1d9", size:10},
    xaxis:{gridcolor:"#21262d", rangeslider:{visible:false}},
    yaxis:{gridcolor:"#21262d", side:"right"},
    margin:{t:14, r:50, b:30, l:40},
    showlegend:false,
  }, {displayModeBar:false, responsive:true});
  const ss = top ? (top.sym_stats || {}) : {};
  const tier = top ? top.tier : "—";
  if (top) {
    head.innerHTML = `<span class="sym">${sym}</span>
      <span class="${tierClass(tier)}">${tier}</span>
      <span style="color:#7a8590;font-size:10.5px">${(top.run||"").slice(0, 38)}…</span>`;
  } else {
    head.innerHTML = `<span class="sym">${sym}</span><span style="color:#dc6c6c">no canonical 7D test</span>`;
  }
  const stats = panel.querySelector(`#stats_${sym}`);
  if (top && ss.sym_trades) {
    stats.innerHTML = `
      <b>BT(7D):</b> sym_pool_sharpe <b class="${cls(ss.sym_pool_sharpe)}">${fmt(ss.sym_pool_sharpe,4)}</b>
      · trades ${(ss.sym_trades||0).toLocaleString()}
      · dd ${fmt(ss.sym_dd_pct,2)}%
      · WR ${fmt((ss.sym_wr||0)*100,0)}%
      · gain ${fmt(ss.sym_total_gain_pct,1)}%
      · <span style="color:#7a8590">overall pool_sharpe ${fmt(top.pool_sharpe,4)} on ${top.n_syms} syms</span>
      <br><b>LIVE:</b> ${liveEntries.length} entries · ${liveExits.length} exits across [${accts.replace(/,/g,", ")}]`;
  } else {
    stats.innerHTML = `<b>LIVE:</b> ${liveEntries.length} entries · ${liveExits.length} exits across [${accts.replace(/,/g,", ")}]`;
  }
}
async function loadCryptoTab(){
  const c = document.getElementById("cryptoPanels");
  if (c.children.length) return;
  for (const sym of CRYPTO_SYMS) await buildPanel(c, sym, CRYPTO_ACCTS);
}
async function loadStocksTab(){
  const c = document.getElementById("stocksPanels");
  if (c.children.length) return;
  for (const sym of STOCK_SYMS) await buildPanel(c, sym, STOCK_ACCTS);
}
loadCryptoTab();
</script>
</body></html>"""


@app.route("/clean_runs")
def clean_runs_page():
    """Minimal test-selector page. One row per unique test, click to chart."""
    target_sym = request.args.get("sym", "BTCUSDC")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>tests — pick one</title>
<style>
  body{{font-family:-apple-system,'SF Mono',Menlo,monospace;background:#0d1117;color:#c9d1d9;padding:14px;font-size:13px}}
  h1{{font-size:1.1em;color:#58a6ff;margin-bottom:6px}}
  .toolbar{{display:flex;gap:10px;align-items:center;margin-bottom:10px;font-size:12px}}
  .toolbar label{{color:#8b949e}}
  .toolbar input,.toolbar select{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:4px 7px;border-radius:4px;font-size:12px}}
  .audit{{background:#1c2837;border:1px solid #2f4f6f;padding:7px 12px;border-radius:5px;margin-bottom:10px;font-size:11.5px;color:#a4b8d0}}
  table{{border-collapse:collapse;font-size:12px;width:100%}}
  th{{background:#161b22;color:#8b949e;padding:6px 9px;text-align:left;cursor:pointer;position:sticky;top:0;font-weight:600;font-size:11px}}
  td{{padding:5px 9px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums}}
  tr:hover{{background:#1c2129;cursor:pointer}}
  .tier-Discard{{color:#dc6c6c}} .tier-Noise{{color:#888}} .tier-Directional{{color:#cdb86c}}
  .tier-Best-of-current{{color:#7fb069}} .tier-Strong{{color:#3fb950}} .tier-Aspirational{{color:#58a6ff;font-weight:700}}
  .pos{{color:#3fb950}} .neg{{color:#f85149}}
  .badge{{display:inline-block;padding:1px 5px;border-radius:3px;font-size:10px;background:#21262d;color:#8b949e}}
  .has-trades{{background:#0f1f0f;color:#7fb069}} .no-trades{{background:#1f0f0f;color:#dc6c6c}}
  .inflated{{color:#dc6c6c;text-decoration:line-through}}
  a{{color:#58a6ff}}
</style></head><body>

<h1>tests <span style="color:#7a8590;font-size:.78em">— deduplicated, one row per unique result</span></h1>

<div class="audit">
  <b>pool_sharpe</b> = overall canonical (across all syms) ·
  <b>sym_sharpe</b> = mean of per-sym Sharpes (capped ±5) ·
  <b>on this sym</b> = stats restricted to trades for the chart symbol ·
  Click any row to open the chart with that test's trades overlaid.
  <a href="/" style="margin-left:8px">← chart</a> ·
  <a href="/canonical_top" style="margin-left:8px">canonical_top</a>
</div>

<div class="toolbar">
  <label>chart sym: <input id="sym" value="{target_sym}" style="width:110px" /></label>
  <button id="reload" style="padding:4px 10px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;cursor:pointer">reload</button>
  <label>filter: <input id="filter" type="search" placeholder="run name…" style="width:200px" /></label>
  <label>tier: <select id="tierFilter">
    <option value="">all</option>
    <option value="Aspirational">Aspirational</option>
    <option value="Strong">Strong</option>
    <option value="Best-of-current">Best-of-current</option>
    <option value="Directional">Directional</option>
    <option value="Noise">Noise</option>
  </select></label>
  <label><input type="checkbox" id="onlyWithTrades" checked /> only tests with trades on this sym</label>
  <span id="cnt" style="color:#7a8590;margin-left:auto;font-size:11px"></span>
</div>

<table id="tbl">
<thead><tr>
  <th data-sort="rank">#</th>
  <th data-sort="tier">tier</th>
  <th data-sort="pool_sharpe">pool_sharpe</th>
  <th data-sort="sym_sharpe">sym_sharpe (overall)</th>
  <th data-sort="trades">trades (overall)</th>
  <th data-sort="n_syms">n_syms</th>
  <th data-sort="years">years</th>
  <th data-sort="sym_trades">on this sym</th>
  <th data-sort="sym_pool_sharpe">sym pool_sharpe</th>
  <th data-sort="sym_dd_pct">sym dd</th>
  <th data-sort="run">run</th>
</tr></thead>
<tbody id="rows"></tbody>
</table>

<script>
const fmt = (n, d=4) => (n === null || n === undefined || isNaN(n)) ? "—" : Number(n).toFixed(d);
const cls = (n) => Number(n) >= 0 ? "pos" : "neg";
let _tests = [];

async function load() {{
  const sym = document.getElementById("sym").value.toUpperCase();
  document.getElementById("cnt").textContent = "loading…";
  const r = await fetch("/run_unique?sym=" + encodeURIComponent(sym)).then(r=>r.json());
  _tests = r.tests || [];
  document.getElementById("cnt").textContent =
    `${{r.n_unique}} unique tests · ${{r.n_with_trades_on_sym}} have trades on ${{sym}}`;
  render();
}}

function render() {{
  const filt = (document.getElementById("filter").value || "").toLowerCase();
  const tierF = document.getElementById("tierFilter").value;
  const onlyWith = document.getElementById("onlyWithTrades").checked;
  const sym = document.getElementById("sym").value.toUpperCase();
  const rows = _tests.filter(t => {{
    if (onlyWith && !t.has_trades_on_sym) return false;
    if (tierF && t.tier !== tierF) return false;
    if (filt && !(t.run || "").toLowerCase().includes(filt)) return false;
    return true;
  }});
  const html = rows.map((t, idx) => {{
    const ss = t.sym_stats || {{}};
    const symTier = ss.sym_tier ? `<span class="tier-${{ss.sym_tier}}">${{fmt(ss.sym_pool_sharpe, 4)}}</span>` :
                                  '<span style="color:#555">—</span>';
    const inflClass = ss.sym_inflated ? "inflated" : "";
    return `<tr onclick="openRun('${{(t.run || "").replace(/'/g, "\\\\'")}}', '${{sym}}')">
      <td style="color:#7a8590">${{idx+1}}</td>
      <td><span class="tier-${{t.tier}}">${{t.tier}}</span></td>
      <td>${{fmt(t.pool_sharpe, 4)}}</td>
      <td>${{fmt(t.sym_sharpe, 4)}}</td>
      <td>${{(t.trades||0).toLocaleString()}}</td>
      <td>${{t.n_syms}}</td>
      <td>${{fmt(t.years, 2)}}</td>
      <td class="${{t.has_trades_on_sym ? 'has-trades' : 'no-trades'}}" style="padding:1px 5px">
        ${{t.has_trades_on_sym ? (ss.sym_trades || 0).toLocaleString() : 'no'}}</td>
      <td class="${{inflClass}}">${{symTier}}</td>
      <td class="${{cls(ss.sym_dd_pct)}}">${{fmt(ss.sym_dd_pct, 2)}}%</td>
      <td style="color:#7a8590;font-size:10.5px;font-family:'SF Mono',Menlo,monospace">${{(t.run || "").length>50 ? t.run.slice(0,48)+"…" : t.run}}</td>
    </tr>`;
  }}).join("");
  document.getElementById("rows").innerHTML = html || `<tr><td colspan="11" style="padding:18px;text-align:center;color:#7a8590">no tests match filter</td></tr>`;
  document.getElementById("cnt").textContent =
    `showing ${{rows.length}} / ${{_tests.length}} tests on ${{sym}}`;
}}

function openRun(run, sym) {{
  // Open main chart with this run pre-checked + sym set
  const url = `/?sym=${{encodeURIComponent(sym)}}&runs=${{encodeURIComponent(run)}}`;
  window.location.href = url;
}}

document.getElementById("reload").onclick = load;
document.getElementById("sym").onchange = load;
document.getElementById("filter").oninput = render;
document.getElementById("tierFilter").onchange = render;
document.getElementById("onlyWithTrades").onchange = render;
load();
</script>
</body></html>"""


@app.route("/run_catalog")
def run_catalog():
    """Categorized catalog of all runs partitioned by machine.

    Output:
        {
          "machines": {
            "macbook": {"categories": [{"key": "7D_macbook_crypto", "description": "...", "runs": [...]}, ...]},
            "s1": {...},
            "s2": {...},
          },
          "all_run_count": int, "generated_utc": "..."
        }
    Each run carries: run, category, description (from overrides diff or canonical line),
    pool_sharpe, sym_sharpe, trades, n_syms, n_years, mtime, syms, tier, inflated.

    Categories within a machine are sorted: 7D_* first, then by best run pool_sharpe desc.
    Runs within a category are sorted by pool_sharpe desc with chronological tiebreak (mtime desc).
    """
    with _precompute_lock:
        rr_body = _precomputed.get("runs_ranked")
    runs_data: List[Dict[str, Any]] = []
    if rr_body:
        try:
            runs_data = json.loads(rr_body)
        except Exception:
            runs_data = []
    by_machine: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        "macbook": {}, "s1": {}, "s2": {},
    }
    for r in runs_data:
        run = r.get("run", "")
        syms = r.get("syms", [])
        machine, cat = _classify_run(run, syms)
        ps = float(r.get("pool_sharpe", 0) or 0)
        trades = int(r.get("trades", 0) or 0)
        n_syms = int(r.get("n_syms", 0) or 0)
        n_years = float(r.get("n_years", 0) or 0)
        floor_syms = metrics_guard.MIN_SYMS_STOCKS if machine == "s2" else metrics_guard.MIN_SYMS_CRYPTO
        sample_floor_pass = (n_syms >= floor_syms) and (n_years >= 1.0) and (n_syms == 0 or trades / max(1, n_syms) >= 30)
        inflated = abs(ps) > metrics_guard.PER_SYM_SHARPE_CAP and trades < 5000
        days_span = n_years * 365.25
        trades_per_sym_day = (trades / max(1, n_syms) / days_span) if days_span > 0 else 0
        churn_warn = trades_per_sym_day > 100  # >100 trades/sym/day = scalping unrealism warning
        tier = metrics_guard.tier_name(ps)
        canonical_line = (
            f"pool_sharpe={ps:+.4f} | sym_sharpe={float(r.get('sym_sharpe', 0)):+.4f} | "
            f"avg_gain_trade={float(r.get('avg_gain_trade', 0)):.4f}%/trade | "
            f"gain_per_yr={float(r.get('gain_per_yr', 0)):.1f}%/yr | "
            f"gain_sym_yr={float(r.get('gain_sym_yr', 0)):.4f}%/sym/yr | "
            f"trades={trades} | n_syms={n_syms} | years={n_years:.2f} | "
            f"density={trades_per_sym_day:.1f} tr/sym/day"
        )
        entry = {
            "run": run,
            "category": cat,
            "machine": machine,
            "tier": tier,
            "inflated": inflated,
            "sample_floor_pass": sample_floor_pass,
            "canonical_line": canonical_line,
            "pool_sharpe": ps,
            "sym_sharpe": float(r.get("sym_sharpe", 0) or 0),
            "trades": trades,
            "n_syms": n_syms,
            "n_years": n_years,
            "trades_per_sym_day": round(trades_per_sym_day, 2),
            "churn_warn": churn_warn,
            "max_dd_pct": float(r.get("max_dd_pct", 0) or 0),
            "gain_per_yr": float(r.get("gain_per_yr", 0) or 0),
            "mtime": int(r.get("mtime", 0) or 0),
            "syms": syms,
        }
        by_machine.setdefault(machine, {}).setdefault(cat, []).append(entry)
    for vrun in _v3_paper_runs():
        machine = vrun["machine"]
        cat = vrun["category"]
        vrun.setdefault("pool_sharpe", 0.0)
        vrun.setdefault("trades", vrun.get("n_events", 0))
        vrun.setdefault("n_syms", 0)
        vrun.setdefault("n_years", 0.0)
        vrun.setdefault("tier", "Noise")
        vrun.setdefault("inflated", False)
        vrun.setdefault("sample_floor_pass", False)
        vrun.setdefault("canonical_line", vrun.get("description", ""))
        vrun.setdefault("syms", [])
        vrun.setdefault("max_dd_pct", 0.0)
        vrun.setdefault("gain_per_yr", 0.0)
        vrun.setdefault("sym_sharpe", 0.0)
        by_machine.setdefault(machine, {}).setdefault(cat, []).append(vrun)
    output = {"machines": {}, "generated_utc": datetime.now(timezone.utc).isoformat()}
    total = 0
    for machine in ("macbook", "s1", "s2"):
        cat_dict = by_machine.get(machine, {})
        cats: List[Dict[str, Any]] = []
        for ckey, items in cat_dict.items():
            items.sort(key=lambda r: (-(r.get("pool_sharpe") or 0), -(r.get("mtime") or 0)))
            best_ps = items[0].get("pool_sharpe", 0) if items else 0
            cats.append({
                "key": ckey,
                "description": CATEGORY_DESCRIPTIONS.get(ckey, ckey),
                "best_pool_sharpe": best_ps,
                "n_runs": len(items),
                "runs": items,
            })
        def _cat_sort(c):
            k = c["key"]
            # Priority: 7D agent (NEW system, real-money urgent) > v3 paper/shadow >
            # legacy hourly testing > everything else by best pool_sharpe.
            if k.startswith("7D_agent_"): bucket = 0
            elif k.startswith("v3_"): bucket = 1
            elif k == "legacy_hourly_testing": bucket = 3
            else: bucket = 2
            return (bucket, -(c.get("best_pool_sharpe") or 0))
        cats.sort(key=_cat_sort)
        output["machines"][machine] = {
            "categories": cats,
            "n_runs": sum(c["n_runs"] for c in cats),
        }
        total += sum(c["n_runs"] for c in cats)
    output["all_run_count"] = total
    return jsonify(output)


@app.route("/run_info")
def run_info():
    """Detailed metadata for one run.

    Output:
        run, machine, category, description, canonical_line, syms_with_trades,
        per_symbol [{sym, trades, sym_sharpe, total_gain_pct, ...}],
        overrides (if discoverable from autonomous winners by run_id),
        files (list of trade JSONL paths)
    """
    run = request.args.get("run", "")
    if not run:
        return jsonify({"error": "run required"}), 400
    with _precompute_lock:
        rr_body = _precomputed.get("runs_ranked")
    rr_match = None
    if rr_body:
        try:
            for r in json.loads(rr_body):
                if r.get("run") == run:
                    rr_match = r
                    break
        except Exception:
            pass
    files: List[str] = []
    bare = run.split("::", 1)[1] if "::" in run else run
    for r_keyed, _sym, p in _iter_all_trade_files():
        if r_keyed == run:
            files.append(str(p))
    machine, category = _classify_run(run, (rr_match or {}).get("syms", []))
    overrides_match: Optional[Dict[str, Any]] = None
    pool_origin: Optional[str] = None
    base_aut = BASE_PATH / "data" / "autonomous"
    if base_aut.exists():
        try:
            for p in base_aut.rglob("autonomous_*_winners.jsonl"):
                s = str(p)
                if "_legacy_unverified" in s or "_NOLIES_HOLD_" in s:
                    continue
                try:
                    txt = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                if bare not in txt:
                    continue
                for line in txt.splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    iter_id = rec.get("iter") or rec.get("run") or rec.get("id") or ""
                    if str(iter_id) == bare or rec.get("run_id") == bare:
                        overrides_match = rec.get("overrides") or rec.get("overrides_json") or {}
                        pool_origin = f"{p.parent.parent.name}/{p.parent.name}"
                        break
                if overrides_match:
                    break
        except Exception:
            pass
    if rr_match:
        ps = float(rr_match.get("pool_sharpe", 0) or 0)
        trades = int(rr_match.get("trades", 0) or 0)
        n_syms = int(rr_match.get("n_syms", 0) or 0)
        n_years = float(rr_match.get("n_years", 0) or 0)
        canonical_line = (
            f"pool_sharpe={ps:+.4f} | sym_sharpe={float(rr_match.get('sym_sharpe', 0)):+.4f} | "
            f"avg_gain_trade={float(rr_match.get('avg_gain_trade', 0)):.4f}%/trade | "
            f"gain_per_yr={float(rr_match.get('gain_per_yr', 0)):.1f}%/yr | "
            f"gain_sym_yr={float(rr_match.get('gain_sym_yr', 0)):.4f}%/sym/yr | "
            f"trades={trades} | n_syms={n_syms} | years={n_years:.2f}"
        )
        tier = metrics_guard.tier_name(ps)
    else:
        canonical_line = "(no aggregate stats — run not in /runs_ranked yet)"
        tier = "Noise"
    description_lines = [CATEGORY_DESCRIPTIONS.get(category, category)]
    if overrides_match:
        n_keys = len(overrides_match)
        description_lines.append(
            f"Test parameters: {n_keys} override key{'s' if n_keys != 1 else ''} "
            f"from sweep pool {pool_origin or '?'}"
        )
    return jsonify({
        "run": run,
        "machine": machine,
        "category": category,
        "category_description": CATEGORY_DESCRIPTIONS.get(category, category),
        "description": " · ".join(description_lines),
        "canonical_line": canonical_line,
        "tier": tier,
        "syms_with_trades": sorted({s for r_keyed, s, _p in _iter_all_trade_files() if r_keyed == run}),
        "per_symbol": (rr_match or {}).get("per_symbol", []),
        "overrides": overrides_match or {},
        "pool_origin": pool_origin,
        "files": sorted(files),
        "stats": rr_match or {},
    })


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


_CACHE_DIR = Path("/tmp/chart_cache")
_CACHE_DIR.mkdir(exist_ok=True)


def _load_cache_from_disk():
    """On startup, load any persisted cache so endpoints serve immediately."""
    for key in ("runs_ranked", "per_sym_best"):
        p = _CACHE_DIR / f"{key}.json"
        if p.exists():
            try:
                body = p.read_text()
                with _precompute_lock:
                    _precomputed[key] = body
                print(f"[precompute] loaded {key}={len(body)}B from disk", flush=True)
            except Exception as e:
                print(f"[precompute] disk load {key}: {e}", flush=True)


def _precompute_loop():
    """Background thread: every 30s, recompute aggregates and persist to disk.
    Pure-helper calls, no Flask context. Endpoints serve from `_precomputed` dict."""
    import time as _t
    import sys
    print(f"[precompute] thread started pid={os.getpid()}", flush=True)
    sys.stdout.flush()
    while True:
        try:
            t0 = _t.time()
            print(f"[precompute] cycle starting...", flush=True)
            rr_body = _compute_runs_ranked("")
            with _precompute_lock:
                _precomputed["runs_ranked"] = rr_body
            (_CACHE_DIR / "runs_ranked.json").write_text(rr_body)
            print(f"[precompute] runs_ranked={len(rr_body)}B in {_t.time()-t0:.1f}s", flush=True)
            t1 = _t.time()
            psb_body = _compute_per_sym_best()
            with _precompute_lock:
                _precomputed["per_sym_best"] = psb_body
            (_CACHE_DIR / "per_sym_best.json").write_text(psb_body)
            print(f"[precompute] per_sym_best={len(psb_body)}B in {_t.time()-t1:.1f}s", flush=True)
        except Exception as e:
            import traceback
            print(f"[precompute] error: {e}", flush=True)
            traceback.print_exc()
        _t.sleep(30)


if __name__ == "__main__":
    port = int(os.environ.get("CHART_PORT", 5077))
    print(f"Chart server on http://127.0.0.1:{port}")
    _load_cache_from_disk()
    import threading
    threading.Thread(target=_precompute_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
