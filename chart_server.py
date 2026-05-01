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
TRADIER_HISTORY_DIR = BASE_PATH / "data" / "tradier" / "history"
STOCK_ACCOUNT_KEYS = {"trb", "trc", "tra"}
ACCOUNTS = ["ang", "inf", "flz", "men", "fin", "trb", "trc"]


def _history_dir_for(account: str) -> Path:
    """Crypto accounts (ang/inf/flz/men/fin) live under data/history/<acct>;
    stocks (trb/trc/tra) live under data/tradier/history/<acct>. Both share the
    same per-event JSONL schema written by ez_positions_quick / tradier_positions."""
    if account in STOCK_ACCOUNT_KEYS:
        return TRADIER_HISTORY_DIR / account
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
    "7D_macbook_crypto": "7-Day hourly reconfig (crypto live accounts: ang/inf/flz/men/fin). Re-optimized every hour, ACTIVATES LIVE IMMEDIATELY — most urgent to monitor.",
    "7D_macbook_stocks": "7-Day hourly reconfig (stock live accounts: trb/trc). Re-optimized every hour, ACTIVATES LIVE IMMEDIATELY for stocks.",
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
        if acct in CRYPTO_ACCOUNTS:
            return ("macbook", "7D_macbook_crypto")
        if acct in STOCK_ACCOUNTS:
            return ("macbook", "7D_macbook_stocks")
        return ("macbook", "7D_macbook_crypto")
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
        tier = metrics_guard.tier_name(ps)
        canonical_line = (
            f"pool_sharpe={ps:+.4f} | sym_sharpe={float(r.get('sym_sharpe', 0)):+.4f} | "
            f"avg_gain_trade={float(r.get('avg_gain_trade', 0)):.4f}%/trade | "
            f"gain_per_yr={float(r.get('gain_per_yr', 0)):.1f}%/yr | "
            f"gain_sym_yr={float(r.get('gain_sym_yr', 0)):.4f}%/sym/yr | "
            f"trades={trades} | n_syms={n_syms} | years={n_years:.2f}"
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
            seven_d = 0 if k.startswith("7D_") else 1
            return (seven_d, -(c.get("best_pool_sharpe") or 0))
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
