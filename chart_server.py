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

# External archive roots (absolute paths, not under BASE_PATH). Used to merge in
# results from S2 (now dead — archived to toshiba_ext). Drive may not be mounted,
# in which case we silently skip. Add more paths via V8_EXTRA_ABS_ROOTS env var
# (comma-separated absolute paths to dirs containing *__*.jsonl trade files).
_DEFAULT_ABS_ROOTS = [
    # 2026-05-09: corrected TOSHIBA_EXT paths after S2 archive layout discovery.
    # Old paths (binance_s2_archive/) never existed on disk — directory layout is:
    #   /Volumes/TOSHIBA_EXT/binance_archive/{data,klines_cache,klines_cache_tradier,...}
    #   /Volumes/TOSHIBA_EXT/s2_backup_20260508/binance-sandbox/  (S2's full sandbox snapshot)
    #   /Volumes/TOSHIBA_EXT/sweep_results_distilled/  (202MB distilled CSVs)
    "/Volumes/TOSHIBA_EXT/binance_archive/data/sweep_results",
    "/Volumes/TOSHIBA_EXT/s2_backup_20260508/binance-sandbox/data/sweep_results",
    "/Volumes/TOSHIBA_EXT/s2_backup_20260508/binance-sandbox/data/canonical_trades",
    "/Volumes/TOSHIBA_EXT/s2_backup_20260508/binance-sandbox/data/hourly_reconfig",
    "/Volumes/TOSHIBA_EXT/sweep_results_distilled",
]
EXTRA_ABS_ROOTS_CFG = os.environ.get("V8_EXTRA_ABS_ROOTS", "").strip()
if EXTRA_ABS_ROOTS_CFG:
    EXTRA_ABS_ROOTS = [Path(p.strip()) for p in EXTRA_ABS_ROOTS_CFG.split(",") if p.strip()]
else:
    EXTRA_ABS_ROOTS = [Path(p) for p in _DEFAULT_ABS_ROOTS]

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
    # External archives (toshiba_ext etc) — only included when actually mounted.
    for abs_root in EXTRA_ABS_ROOTS:
        try:
            if abs_root.exists() and abs_root.is_dir():
                key = str(abs_root)
                if key not in seen:
                    seen.add(key)
                    roots.append(abs_root)
        except (OSError, PermissionError):
            continue
    return roots


# ============== ASSET CLASS DETECTION ==============
# Crypto symbols are quoted as <BASE>USDC or <BASE>USDT (Binance Futures perps).
# Stock symbols are bare tickers (AAPL, NVDA, ...). The chart UI uses this to
# split everything into Crypto / Stocks tabs — accounts, symbol list, sweeps.
def _is_crypto_sym(sym: str) -> bool:
    s = (sym or "").upper()
    return s.endswith("USDT") or s.endswith("USDC")


def _asset_class_for_sym(sym: str) -> str:
    return "crypto" if _is_crypto_sym(sym) else "stocks"


def _asset_class_for_run(syms: List[str]) -> str:
    """A run is 'crypto' if any symbol is a crypto perp. Mixed-class runs are
    extremely rare; if they happen, crypto wins (it's the more permissive set)."""
    for s in (syms or []):
        if _is_crypto_sym(s):
            return "crypto"
    return "stocks"


CRYPTO_ACCOUNT_KEYS = {"ang", "inf", "flz", "men", "fin"}
STOCK_ACCOUNTS_ORDER = ["tra", "trb", "trc"]
CRYPTO_ACCOUNTS_ORDER = ["ang", "inf", "flz", "men", "fin"]


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
    """All symbols with NPZ indicators on disk. Optional ?asset_class=crypto|stocks
    filters to that class only. The chart UI uses this to populate the symbol
    dropdown after the user picks a top-level tab."""
    asset_class = (request.args.get("asset_class") or "").strip().lower()
    syms = sorted(p.stem for p in NPZ_DIR.glob("*.npz"))
    if asset_class == "crypto":
        syms = [s for s in syms if _is_crypto_sym(s)]
    elif asset_class == "stocks":
        syms = [s for s in syms if not _is_crypto_sym(s)]
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


_iter_files_cache: Tuple[float, List[Tuple[str, str, Path]]] = (0.0, [])
_ITER_FILES_TTL = 60.0  # 60s — enough for a hot UI session, short enough to pick up new sweep results


def _iter_all_trade_files() -> List[Tuple[str, str, Path]]:
    """Walk every known run-trade JSONL across all roots, cached for 60s.

    Yields (run_id_keyed, sym, path) tuples. run_id_keyed matches what
    `_build_run_registry` produces (hr_<acct>::, bigsweep::, or bare for legacy).
    Multi-sym runs surface every sym — the registry alone keeps only one Path
    per run_id (latest mtime), but for ranking we need every sibling file.
    Without caching, /tests_for_symbol scans tens of thousands of jsonl paths
    on every call, which made first-load timeouts routine.
    """
    global _iter_files_cache
    now = time.time()
    cached_at, cached = _iter_files_cache
    if cached and (now - cached_at) < _ITER_FILES_TTL:
        return cached
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
    _iter_files_cache = (now, out)
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
    # Determine the dominant side from actual trade content. Filename can lie
    # (verified: hourly_reconfig writes the same data into both LONG and SHORT
    # filenames). MIXED if both sides present; the badge in the UI surfaces it.
    sides_seen: Dict[str, int] = {}
    for t in trades:
        s = (t.get("side") or "").upper()
        if s in ("LONG", "SHORT"):
            sides_seen[s] = sides_seen.get(s, 0) + 1
    if not sides_seen:
        content_side = ""
    elif len(sides_seen) == 1:
        content_side = next(iter(sides_seen))
    else:
        # Pick the dominant side, mark MIXED if neither >= 90%
        long_n, short_n = sides_seen.get("LONG", 0), sides_seen.get("SHORT", 0)
        total = long_n + short_n
        if long_n / total >= 0.9:
            content_side = "LONG"
        elif short_n / total >= 0.9:
            content_side = "SHORT"
        else:
            content_side = "MIXED"
    # Trade durations — surface so user can see at a glance whether the test
    # is realistic scalping vs garbage same-bar churning. Compute from actual
    # exit-entry timestamps (seconds), in case duration_bars is missing.
    dur_secs: List[int] = []
    for t in trades:
        e, x = int(t.get("entry_ts", 0) or 0), int(t.get("exit_ts", 0) or 0)
        if e > 0 and x >= e:
            dur_secs.append(x - e)
    dur_secs.sort()
    if dur_secs:
        m = len(dur_secs) // 2
        median_dur_sec = dur_secs[m]
        p25_dur_sec = dur_secs[len(dur_secs) // 4]
        p75_dur_sec = dur_secs[(3 * len(dur_secs)) // 4]
        max_dur_sec = dur_secs[-1]
        n_zero_dur = sum(1 for d in dur_secs if d == 0)
        # Use the most-common duration as a proxy for "1 bar". Anything below
        # that threshold is sub-bar (impossible in a backtest sim).
        n_under_5min = sum(1 for d in dur_secs if d < 300)
    else:
        median_dur_sec = p25_dur_sec = p75_dur_sec = max_dur_sec = 0
        n_zero_dur = n_under_5min = 0
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
        "content_side": content_side,
        "median_dur_sec": median_dur_sec,
        "p25_dur_sec": p25_dur_sec,
        "p75_dur_sec": p75_dur_sec,
        "max_dur_sec": max_dur_sec,
        "n_zero_dur": n_zero_dur,
        "n_under_5min": n_under_5min,
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
                ev["symbol"] = sym  # 2026-05-08: BUG FIX — _reconstruct_trades_from_events skips events with empty symbol
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


@app.route("/local_runs")
def local_runs():
    """List all local backtest runs in TRADES_DIR for a given symbol.
    Filename pattern: <run_id>__<SYMBOL>.jsonl
    Each entry: {run, trades, window_days, pool_sharpe, win_rate, gain_per_day_pct, gain_per_trade_pct}
    Per NO-LIES MANDATE: NO annualized fields. Only per-day, per-trade, window_days.
    """
    target_sym = (request.args.get("sym") or "").upper()
    if not target_sym:
        return jsonify({"runs": [], "error": "sym required"})
    out = []
    if not TRADES_DIR.exists():
        return jsonify({"runs": []})
    for path in sorted(TRADES_DIR.glob(f"*__{target_sym}.jsonl")):
        run_id = path.stem.rsplit(f"__{target_sym}", 1)[0]
        if not run_id:
            continue
        trades = []
        try:
            for ln in path.read_text(encoding="utf-8").splitlines():
                if not ln.strip():
                    continue
                try:
                    trades.append(json.loads(ln))
                except Exception:
                    continue
        except Exception:
            continue
        if not trades:
            continue
        stats = _trade_stats(trades)
        out.append({
            "run": run_id,
            "trades": stats.get("trades", 0),
            "window_days": stats.get("window_days", 0),
            "pool_sharpe": stats.get("pool_sharpe", 0),
            "win_rate": stats.get("win_rate", 0),
            "gain_per_day_pct": stats.get("gain_per_day_pct", 0),
            "gain_per_trade_pct": stats.get("gain_per_trade_pct", 0),
            "total_gain_pct": stats.get("total_gain_pct", 0),
            "tier": stats.get("tier", "Noise"),
            "inflated": stats.get("inflated", False),
        })
    out.sort(key=lambda r: -r.get("pool_sharpe", 0))
    return jsonify({"runs": out, "count": len(out), "trades_dir": str(TRADES_DIR)})


@app.route("/parity_chart")
def parity_chart_page():
    """2026-05-08: Parity overlay — sym + test dropdown + acct checkboxes + auto-zoom + trade table.
    Tabs: Crypto (ang/inf/flz/men/fin) | Stocks (tra/trb/trc).
    Stats use per-day, per-trade, window_days only — NO annualized extrapolation per NO-LIES MANDATE.
    URL: /parity_chart (defaults to BTCUSDC crypto)
    """
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Parity overlay</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body{{font-family:-apple-system,'SF Mono',Menlo,monospace;background:#0d1117;color:#c9d1d9;margin:0;padding:0;font-size:13px}}
  .header{{background:#161b22;border-bottom:1px solid #30363d;padding:6px 16px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
  h1{{font-size:1.05em;color:#58a6ff;margin:0;font-weight:700}}
  .tabs{{display:flex;gap:0;margin-left:8px}}
  .tab{{padding:6px 14px;background:#0d1117;border:1px solid #30363d;color:#8b949e;cursor:pointer;font-size:12px;font-family:inherit;border-radius:3px 3px 0 0;border-bottom:0}}
  .tab.active{{color:#58a6ff;background:#161b22;font-weight:600}}
  input,select{{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:4px 8px;font-family:inherit;font-size:12px;border-radius:3px}}
  select.run{{min-width:520px}}
  button{{background:#1f6feb;color:white;border:0;padding:4px 12px;cursor:pointer;border-radius:3px;font-family:inherit;font-size:12px}}
  label.cb{{display:inline-flex;align-items:center;gap:3px;padding:2px 6px;background:#161b22;border:1px solid #30363d;border-radius:3px;cursor:pointer;font-size:11.5px}}
  label.cb input{{margin:0}}
  .legend{{padding:6px 16px;color:#a4b8d0;font-size:11px;background:#0d1117;border-bottom:1px solid #21262d}}
  .legend b{{color:#dcc26b}}
  #chart{{width:100%;height:52vh}}
  .stats{{padding:6px 16px;color:#a4b8d0;font-size:11.5px;background:#0d1117;border-bottom:1px solid #21262d;line-height:1.7}}
  .stats span{{margin-right:14px;display:inline-block}}
  .stats .pos{{color:#3fb950}} .stats .neg{{color:#f85149}}
  .stats .tag-inflated{{color:#dc6c6c;background:#2d0f0f;padding:1px 6px;border-radius:3px;font-weight:700;margin-right:8px}}
  table{{width:100%;border-collapse:collapse;font-size:11px;font-family:'SF Mono',Menlo,monospace}}
  th,td{{padding:3px 8px;text-align:left;border-bottom:1px solid #21262d;white-space:nowrap}}
  th{{background:#161b22;color:#a4b8d0;font-weight:600;position:sticky;top:0}}
  tr.bt{{background:rgba(63,185,80,0.06)}} tr.live{{background:rgba(220,194,107,0.06)}}
  td.long{{color:#3fb950}} td.short{{color:#f85149}}
  td.pnl-pos{{color:#3fb950}} td.pnl-neg{{color:#f85149}}
  .tbl-wrap{{max-height:34vh;overflow-y:auto;padding:0 16px}}
  a{{color:#58a6ff;text-decoration:none}}
  .row{{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:4px 16px;border-bottom:1px solid #21262d;background:#0a0d12}}
  .row .lbl{{color:#7a8590;font-size:11px;margin-right:4px}}
</style></head><body>

<div class="header">
  <h1>📊 Parity overlay</h1>
  <div class="tabs">
    <button class="tab active" data-mode="crypto">🟢 Crypto</button>
    <button class="tab" data-mode="stocks">🟡 Stocks</button>
  </div>
  <span style="color:#7a8590;font-size:11px">syms via dropdown ·</span>
  <a href="/" style="font-size:11px">← chart</a>
  <a href="/live" style="font-size:11px">→ live</a>
</div>

<div class="row">
  <span class="lbl">SYM</span>
  <select id="sym" class="run" style="min-width:160px"></select>
  <span class="lbl">TF</span>
  <select id="tf"><option value="3m">3m</option><option value="15m" selected>15m</option><option value="1h">1h</option><option value="4h">4h</option><option value="D">D</option></select>
  <span class="lbl">TEST</span>
  <select id="run" class="run"></select>
  <button onclick="loadAll()">reload</button>
</div>

<div class="row" id="acct_row">
  <span class="lbl">LIVE acct overlays</span>
  <span id="acct_checks"></span>
  <span class="lbl" style="margin-left:14px">zoom</span>
  <label class="cb"><input type="checkbox" id="zoomTest" checked> auto-zoom to test window</label>
</div>

<div class="legend">
  🟢▲▼ backtest entry · 🔴✕ backtest exit · <b>▲</b>/<b>▼</b> LIVE entry/exit · klines from NPZ
</div>

<div id="chart"></div>

<div class="stats" id="stats">choose a symbol and test…</div>

<div class="tbl-wrap">
  <table id="trades_table">
    <thead><tr><th>src</th><th>acct</th><th>ts (UTC)</th><th>side</th><th>price</th><th>qty</th><th>pnl%</th><th>reason</th></tr></thead>
    <tbody id="trades_tbody"></tbody>
  </table>
</div>

<script>
const CRYPTO_ACCTS = ['ang','inf','flz','men','fin'];
const STOCK_ACCTS = ['tra','trb','trc'];
const CRYPTO_SYMS = ['BTCUSDC','ETHUSDC','ZECUSDC','TONUSDT','SOLUSDC','BNBUSDC','XRPUSDC','ADAUSDC','AVAXUSDC','LINKUSDC','LTCUSDC','UNIUSDC'];
const STOCK_SYMS = ['AAPL','MSFT','NVDA','AMZN','META','GOOGL','TSLA','AMD','AVGO','PLTR','SPY','QQQ'];
let MODE = 'crypto';

const fmt = (n, d=2) => (n===null||n===undefined||isNaN(n)) ? '—' : Number(n).toFixed(d);
const fmtTs = (t) => new Date(t * 1000).toISOString().slice(0,19).replace('T',' ');

function setMode(m) {{
  MODE = m;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.mode===m));
  // Sym dropdown
  const symSel = document.getElementById('sym');
  symSel.innerHTML = '';
  const syms = m==='crypto' ? CRYPTO_SYMS : STOCK_SYMS;
  for (const s of syms) {{ const o=document.createElement('option'); o.value=s; o.textContent=s; symSel.appendChild(o); }}
  // Acct checkboxes
  const accts = m==='crypto' ? CRYPTO_ACCTS : STOCK_ACCTS;
  const wrap = document.getElementById('acct_checks');
  wrap.innerHTML = '';
  for (const a of accts) {{
    const lbl = document.createElement('label'); lbl.className='cb';
    lbl.innerHTML = `<input type="checkbox" data-acct="${{a}}" checked> ${{a}}`;
    wrap.appendChild(lbl);
  }}
  // After mode change: refresh runs for default sym
  refreshRuns();
}}

function selectedAccts() {{
  return Array.from(document.querySelectorAll('#acct_checks input:checked')).map(c => c.dataset.acct);
}}

async function refreshRuns() {{
  const sym = document.getElementById('sym').value;
  const runSel = document.getElementById('run');
  runSel.innerHTML = '<option value="">loading…</option>';
  try {{
    const [r, local] = await Promise.all([
      fetch(`/run_unique?sym=${{sym}}&limit=200`).then(r=>r.json()).catch(()=>({{tests:[]}})),
      fetch(`/local_runs?sym=${{sym}}`).then(r=>r.json()).catch(()=>({{runs:[]}}))
    ]);
    const tests = (r && r.tests) || [];
    const localRuns = (local && local.runs) || [];
    runSel.innerHTML = '';
    // Local parity runs first (most relevant)
    if (localRuns.length) {{
      const og = document.createElement('optgroup'); og.label = `LOCAL (${{localRuns.length}})`;
      for (const lr of localRuns) {{
        const o = document.createElement('option'); o.value = lr.run;
        const ps = lr.pool_sharpe != null ? lr.pool_sharpe.toFixed(3) : '?';
        const wd = lr.window_days != null ? lr.window_days.toFixed(1) : '?';
        const gpd = lr.gain_per_day_pct != null ? lr.gain_per_day_pct.toFixed(3) : '?';
        const gpt = lr.gain_per_trade_pct != null ? lr.gain_per_trade_pct.toFixed(3) : '?';
        const wr = lr.win_rate != null ? (lr.win_rate*100).toFixed(0) : '?';
        o.textContent = `${{lr.run}} | ${{wd}}d ${{lr.trades||0}}tr pool=${{ps}} WR=${{wr}}% gain/d=${{gpd}}% gain/tr=${{gpt}}%`;
        og.appendChild(o);
      }}
      runSel.appendChild(og);
    }}
    // Registry tests
    if (tests.length) {{
      const og = document.createElement('optgroup'); og.label = `Registry (${{tests.length}})`;
      for (const t of tests) {{
        const o = document.createElement('option'); o.value = t.run;
        const wd = t.years ? Math.round(t.years * 365) : (t.sym_stats && t.sym_stats.window_days ? t.sym_stats.window_days.toFixed(0) : '?');
        const ps = t.pool_sharpe != null ? t.pool_sharpe.toFixed(3) : '?';
        const ss = t.sym_sharpe != null ? t.sym_sharpe.toFixed(3) : '?';
        const sym_tr = t.sym_stats && t.sym_stats.sym_trades ? t.sym_stats.sym_trades : '?';
        const sym_gain = t.sym_stats && t.sym_stats.sym_total_gain_pct != null ? t.sym_stats.sym_total_gain_pct.toFixed(1) : '?';
        const sym_wr = t.sym_stats && t.sym_stats.sym_wr != null ? (t.sym_stats.sym_wr*100).toFixed(0) : '?';
        const sym_dd = t.sym_stats && t.sym_stats.sym_dd_pct != null ? t.sym_stats.sym_dd_pct.toFixed(2) : '?';
        const gpt = t.avg_gain_trade != null ? t.avg_gain_trade.toFixed(3) : '?';
        // Strip noisy hr_<acct>:: prefix + redundant SYM__SIDE__ to make name readable
        const cleanRun = t.run.replace(/^hr_(ang|inf|flz|men|fin)::/, '').replace(new RegExp(`${{sym}}__(LONG|SHORT)__`), '');
        // 2026-05-08: dropped total `gain=` (no value across different windows). Keep gain/trade.
        o.textContent = `${{cleanRun}} | ${{wd}}d ${{sym_tr}}tr pool=${{ps}} sym=${{ss}} WR=${{sym_wr}}% dd=${{sym_dd}}% gain/tr=${{gpt}}%`;
        og.appendChild(o);
      }}
      runSel.appendChild(og);
    }}
    if (!localRuns.length && !tests.length) {{
      const o = document.createElement('option'); o.value=''; o.textContent='(no tests for this sym)'; runSel.appendChild(o);
    }}
    const oC = document.createElement('option'); oC.value='__custom__'; oC.textContent='— enter custom run id —'; runSel.appendChild(oC);
  }} catch (e) {{
    runSel.innerHTML = `<option value="">err: ${{e}}</option>`;
  }}
  loadAll();
}}

async function loadAll() {{
  const sym = document.getElementById('sym').value;
  let run = document.getElementById('run').value;
  if (run === '__custom__') {{
    run = prompt('Enter run_id (e.g. parity_apr21_ang)', 'parity_apr21_ang');
    if (!run) return;
  }}
  const tf = document.getElementById('tf').value;
  const accts = selectedAccts().join(',');
  const zoomToTest = document.getElementById('zoomTest').checked;

  // STEP 1: Fetch backtest trades FIRST to know the test window
  const bt = run ? await fetch(`/backtest_trades?run=${{run}}&sym=${{sym}}`).then(r=>r.json()).catch(()=>({{trades:[]}})) : {{trades:[]}};
  const btTrades = (bt && bt.trades) || [];

  // STEP 2: Compute test window from BT trades
  let winStartTs = null, winEndTs = null;
  if (btTrades.length > 0) {{
    const allTs = [...btTrades.map(t=>t.entry_ts), ...btTrades.map(t=>t.exit_ts)].filter(x=>x>0);
    if (allTs.length) {{
      const minT = Math.min(...allTs);
      const maxT = Math.max(...allTs);
      const padding = Math.max((maxT - minT) * 0.10, 1800);  // 10% pad or 30min
      winStartTs = minT - padding;
      winEndTs = maxT + padding;
    }}
  }}

  // STEP 3: Fetch klines with window — KEY FIX: pass start/end so we get only the test-window bars
  // This makes 3m TF actually look like 3m candles instead of compressed to fit 2 years.
  let klinesUrl = `/klines?sym=${{sym}}&tf=${{tf}}&max=5000`;
  if (zoomToTest && winStartTs) {{
    klinesUrl += `&start=${{winStartTs}}&end=${{winEndTs}}`;
  }} else if (!winStartTs) {{
    // No backtest trades — default to last 30 days so we don't show all-of-history
    const now = Math.floor(Date.now()/1000);
    klinesUrl += `&start=${{now - 30*86400}}&end=${{now}}`;
  }}

  const liveP = accts ? fetch(`/historic_trades?sym=${{sym}}&accounts=${{accts}}`).then(r=>r.json()).catch(()=>({{}})) : Promise.resolve({{}});
  const klinesP = fetch(klinesUrl).then(r=>r.json()).catch(()=>[]);
  const [klines, live] = await Promise.all([klinesP, liveP]);
  const k = klines || [];

  // X-axis range = test window (Plotly uses this directly)
  const xMin = winStartTs ? new Date(winStartTs * 1000) : null;
  const xMax = winEndTs ? new Date(winEndTs * 1000) : null;

  const traces = [{{
    x: k.map(b => new Date(b.t * 1000)),
    open: k.map(b=>b.o), high: k.map(b=>b.h), low: k.map(b=>b.l), close: k.map(b=>b.c),
    type: 'candlestick', name: sym,
    increasing:{{line:{{color:'#3fb950'}}}}, decreasing:{{line:{{color:'#f85149'}}}}
  }}];

  if (btTrades.length) {{
    traces.push({{
      x: btTrades.map(t => new Date(t.entry_ts * 1000)),
      y: btTrades.map(t => t.entry_price),
      mode:'markers', type:'scatter', name:'BT entry',
      marker:{{symbol: btTrades.map(t => t.side==='LONG' ? 'triangle-up' : 'triangle-down'),
              size:11, color:'#3fb950', line:{{width:1,color:'#000'}}}}
    }});
    traces.push({{
      x: btTrades.map(t => new Date(t.exit_ts * 1000)),
      y: btTrades.map(t => t.exit_price),
      mode:'markers', type:'scatter', name:'BT exit',
      marker:{{symbol:'x', size:9, color:'#f85149', line:{{width:1,color:'#000'}}}}
    }});
  }}

  // Live events per acct (one trace per acct so legend can toggle individually)
  const liveTrades = [];
  let totalLiveEvents = 0;
  const palette = ['#dcc26b','#58a6ff','#bd93f9','#ff79c6','#50fa7b'];
  let pi = 0;
  for (const acct in (live || {{}})) {{
    const events = (live[acct] && live[acct].events) || [];
    totalLiveEvents += events.length;
    if (!events.length) {{ pi++; continue; }}
    const color = palette[pi % palette.length]; pi++;
    traces.push({{
      x: events.map(e => new Date(e.unix_ts * 1000)),
      y: events.map(e => parseFloat(e.price)),
      mode:'markers', type:'scatter', name:`LIVE-${{acct}} (${{events.length}})`,
      marker:{{symbol: events.map(e => (e.type==='AUGMENT'||e.type==='OPEN') ? 'triangle-up-open' : 'triangle-down-open'),
              size:13, color: color, line:{{width:2, color: color}}}}
    }});
    for (const e of events) liveTrades.push({{...e, acct}});
  }}

  const layout = {{
    paper_bgcolor:'#0d1117', plot_bgcolor:'#0d1117', font:{{color:'#c9d1d9',family:'monospace'}},
    margin:{{t:8,r:8,b:30,l:60}}, xaxis:{{rangeslider:{{visible:false}}}},
    showlegend:true, legend:{{orientation:'h', y:-0.12}}
  }};
  if (xMin && xMax) layout.xaxis.range = [xMin, xMax];

  Plotly.newPlot('chart', traces, layout, {{responsive:true}});

  // Stats — HONEST: per-day, per-trade, window_days. NO annualized.
  const s = (bt && bt.stats) || {{}};
  const inflTag = s.inflated ? `<span class="tag-inflated">⚠ DIAGNOSTIC (window<30d, sub-floor sample)</span>` : '';
  const wd = s.window_days || 0;
  const tpd = s.trades_per_day || 0;
  const gpd = s.gain_per_day_pct || 0;
  const gpt = s.gain_per_trade_pct != null ? s.gain_per_trade_pct : (s.avg_pnl_pct || 0);
  document.getElementById('stats').innerHTML =
    `${{inflTag}}` +
    `<span>BT trades=<b>${{btTrades.length}}</b></span>` +
    `<span>LIVE events=<b>${{totalLiveEvents}}</b></span>` +
    `<span>window=<b>${{fmt(wd,1)}}d</b></span>` +
    `<span>pool_sharpe=<b class="${{s.pool_sharpe>=0?'pos':'neg'}}">${{fmt(s.pool_sharpe,3)}}</b></span>` +
    `<span>WR=<b>${{fmt((s.win_rate||0)*100,1)}}%</b></span>` +
    `<span>total_gain=<b class="${{s.total_gain_pct>=0?'pos':'neg'}}">${{fmt(s.total_gain_pct,2)}}%</b></span>` +
    `<span>gain/day=<b class="${{gpd>=0?'pos':'neg'}}">${{fmt(gpd,4)}}%</b></span>` +
    `<span>gain/trade=<b class="${{gpt>=0?'pos':'neg'}}">${{fmt(gpt,4)}}%</b></span>` +
    `<span>trades/day=<b>${{fmt(tpd,2)}}</b></span>` +
    `<span>tier=<b>${{s.tier||'—'}}</b></span>`;

  // Trade table
  const rows = [];
  for (const t of btTrades) {{
    rows.push({{src:'BT', acct:'-', ts:t.entry_ts, side:t.side, price:t.entry_price, qty:'-', pnl:null, reason:t.entry_reason}});
    rows.push({{src:'BT', acct:'-', ts:t.exit_ts, side:t.side, price:t.exit_price, qty:'-', pnl:t.pnl_pct, reason:t.exit_reason}});
  }}
  for (const e of liveTrades) {{
    rows.push({{src:'LIVE', acct:e.acct, ts:e.unix_ts, side:e.side, price:parseFloat(e.price)||0, qty:e.qty, pnl:null, reason:e.reason}});
  }}
  rows.sort((a,b) => a.ts - b.ts);
  document.getElementById('trades_tbody').innerHTML = rows.map(r => {{
    const sideCls = r.side==='LONG' ? 'long' : 'short';
    const pnlCls = r.pnl===null ? '' : (r.pnl>=0 ? 'pnl-pos' : 'pnl-neg');
    const pnlStr = r.pnl===null ? '' : fmt(r.pnl,2)+'%';
    return `<tr class="${{r.src==='BT'?'bt':'live'}}"><td>${{r.src}}</td><td>${{r.acct}}</td><td>${{fmtTs(r.ts)}}</td><td class="${{sideCls}}">${{r.side||''}}</td><td>${{fmt(r.price,4)}}</td><td>${{r.qty}}</td><td class="${{pnlCls}}">${{pnlStr}}</td><td style="font-size:10.5px;color:#7a8590">${{(r.reason||'').slice(0,90)}}</td></tr>`;
  }}).join('');
}}

// Wiring
document.querySelectorAll('.tab').forEach(t => t.onclick = () => setMode(t.dataset.mode));
document.getElementById('sym').onchange = refreshRuns;
document.getElementById('run').onchange = loadAll;
document.getElementById('tf').onchange = loadAll;
document.getElementById('zoomTest').onchange = loadAll;
document.getElementById('acct_row').addEventListener('change', e => {{
  if (e.target.matches('input[data-acct]')) loadAll();
}});

setMode('crypto');
</script>
</body></html>"""


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
    # 2026-05-08: ALSO mark inflated when window < 30 days (CLAUDE.md NO-LIES MANDATE — annualized
    # extrapolation from a sub-month window is the exact lying-numbers pattern that wiped 80% of net worth).
    _short_window = window_days < 30.0
    inflated = (abs(sharpe_pt) > metrics_guard.PER_SYM_SHARPE_CAP and n < 5000) or _short_window
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
        "gain_per_day_pct": round(total_gain / window_days, 4) if window_days > 0 else 0,
        "gain_per_trade_pct": round(avg, 4),
        # 2026-05-08 NO-LIES MANDATE: trades_per_week/month/year + gain_per_week/month/year REMOVED.
        # Annualizing a sub-month window is the lying-numbers pattern. Use per-day + per-trade + window_days.
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


_overrides_index: Dict[str, Dict[str, Any]] = {}
_overrides_index_built_at: float = 0.0
_OVERRIDES_INDEX_TTL = 300.0  # 5 min — sweep winners barely change


def _build_overrides_index() -> Dict[str, Dict[str, Any]]:
    """One pass over every autonomous_*_winners.jsonl: return {run_id → overrides}.
    Replaces the per-run scan that was O(runs × files) and timed out
    /tests_for_symbol. Cached at module level."""
    out: Dict[str, Dict[str, Any]] = {}
    base_aut = BASE_PATH / "data" / "autonomous"
    if not base_aut.exists():
        return out
    try:
        for p in base_aut.rglob("autonomous_*_winners.jsonl"):
            s = str(p)
            if "_legacy_unverified" in s or "_NOLIES_HOLD_" in s:
                continue
            try:
                txt = p.read_text(encoding="utf-8")
            except Exception:
                continue
            for line in txt.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ovr = rec.get("overrides") or rec.get("overrides_json") or {}
                if not ovr:
                    continue
                for k in ("iter", "run", "id", "run_id"):
                    rid = rec.get(k)
                    if rid:
                        out.setdefault(str(rid), ovr)
    except Exception:
        pass
    return out


def _overrides_for_run(run: str) -> Dict[str, Any]:
    """Cheap lookup: bare run-id (post `::` split) → overrides dict.
    Index rebuilt at most once per _OVERRIDES_INDEX_TTL seconds."""
    global _overrides_index, _overrides_index_built_at
    now = time.time()
    if now - _overrides_index_built_at > _OVERRIDES_INDEX_TTL or not _overrides_index:
        _overrides_index = _build_overrides_index()
        _overrides_index_built_at = now
    bare = run.split("::", 1)[1] if "::" in run else run
    return _overrides_index.get(bare, {}) or _overrides_index.get(run, {})


def _hourly_reconfig_overrides(acct: str, sym: str, side: str, tag: str) -> Dict[str, Any]:
    """Look up overrides for an hourly_reconfig (sym, side, tag) tuple. The
    current cycle's active_config.json holds the WINNING tag's overrides per
    (sym, side). For non-winning tags we have no per-tag overrides on disk
    after the cycle ends — they're discarded post-decision."""
    p = BASE_PATH / "data" / "hourly_reconfig" / acct / "active_config.json"
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    key = f"{sym.upper()}_{side.upper()}"
    rec = data.get(key) or {}
    if rec.get("winning_tag") == tag:
        return rec.get("overrides") or {}
    return {}


def _parse_run_for_display(run_keyed: str, sym: str) -> Tuple[str, str, str]:
    """Return (display_name, side, source_label).

    Hourly-reconfig run files are named `<SYM>__<SIDE>__<tag>__<SYM>.jsonl`.
    The leading `<SYM>__<SIDE>__` is redundant in the per-symbol test list
    (symbol is the column we're filtered on; side gets its own badge). Strip
    it so users see the actual config tag, e.g. `extra_btc_BTCUSDC_LONG_top`
    instead of `BTCUSDC__LONG__extra_btc_BTCUSDC_LONG_top`."""
    bare = run_keyed.split("::", 1)[1] if "::" in run_keyed else run_keyed
    side = ""
    source = run_keyed.split("::", 1)[0] if "::" in run_keyed else "legacy"
    sym_u = sym.upper()
    for s in ("LONG", "SHORT"):
        prefix = f"{sym_u}__{s}__"
        if bare.startswith(prefix):
            return bare[len(prefix):], s, source
    return bare, side, source


@app.route("/tests_for_symbol")
def tests_for_symbol():
    """Every backtest that has trades for ?sym=X, deduplicated by trade-list
    content. The hourly_reconfig system writes ~10 'tag' variants per
    (sym, side), but many tags produce IDENTICAL trade lists when their
    override changes don't affect the symbol — without dedup the user sees
    127 rows of repeating numbers. We collapse files with the same content
    signature (n, sum_pnl, ts_first, ts_last) into ONE row, with `aliases`
    listing the other tags that share the same trade list.

    Output row schema:
        run             short display name (config tag, side prefix stripped)
        run_keyed       original run_id (used by /backtest_trades, /run_info)
        side            LONG | SHORT | "" (when extractable from filename)
        source          hr_<acct> | bigsweep | legacy
        category        _classify_run output
        sym_sharpe      per-sym Sharpe capped ±5
        trades, wr, total_gain_pct, gain_per_yr, gain_per_mo, ts_first/last, years
        diff_summary    first 3 override keys, or "(no overrides recorded)"
        diff_keys       full overrides dict
        aliases         list of other config tags with the same trade list
        n_aliases       len(aliases)

    Sort: sym_sharpe desc. Sources merged: MB + S1 + toshiba_ext (when mounted)."""
    sym = request.args.get("sym", "").upper()
    if not sym:
        return jsonify({"error": "sym required"}), 400
    # Content-based signature only. The filename's `__LONG__` / `__SHORT__`
    # token is unreliable: hourly_reconfig writes the SAME trade content into
    # both LONG and SHORT files for the same config (verified 2026-05-08:
    # md5 of *__LONG__extra_btc_ETHUSDC_LONG_top__*.jsonl equals md5 of
    # *__SHORT__extra_btc_ETHUSDC_LONG_top__*.jsonl, and trades inside both
    # files carry side="LONG"). Trusting filename-side would inflate the test
    # count 2× with phantom "SHORT" rows that overlay LONG markers anyway.
    by_sig: Dict[Tuple[int, int, int, int], List[Tuple[str, Path, Dict[str, Any]]]] = {}
    for run, s, p in _iter_all_trade_files():
        if s.upper() != sym:
            continue
        agg = _file_aggregates(p)
        if not agg or agg["n"] < 5:
            continue
        sig = (
            agg["n"],
            int(round(agg["sum_pnl"] * 100)),
            int(agg["ts_first"]),
            int(agg["ts_last"]),
        )
        by_sig.setdefault(sig, []).append((run, p, agg))
    rows: List[Dict[str, Any]] = []
    for sig, members in by_sig.items():
        # Pick representative: latest mtime first (so a current 7D-cycle wins
        # over a stale legacy file with the same signature), tie-break by
        # shortest display name (cleaner UI label).
        def _rank(item):
            run_keyed, path, agg = item
            try:
                mt = path.stat().st_mtime
            except Exception:
                mt = 0
            display, _side, _src = _parse_run_for_display(run_keyed, sym)
            return (-mt, len(display))
        members.sort(key=_rank)
        primary_run, primary_path, agg = members[0]
        primary_display, _filename_side, source = _parse_run_for_display(primary_run, sym)
        # Use content side over filename side — see _file_aggregates note.
        side = agg.get("content_side") or _filename_side
        machine, category = _classify_run(primary_run, [sym])
        years = max(0.01, (agg["ts_last"] - agg["ts_first"]) / (86400.0 * 365.25))
        gain_per_yr = agg["total_gain"] / years
        gain_per_mo = gain_per_yr / 12.0
        aliases: List[str] = []
        for run_keyed, _p, _a in members[1:]:
            disp, _s, _src = _parse_run_for_display(run_keyed, sym)
            if disp != primary_display and disp not in aliases:
                aliases.append(disp)
        overrides: Dict[str, Any] = {}
        if source.startswith("hr_"):
            acct = source[3:]
            overrides = _hourly_reconfig_overrides(acct, sym, side or "LONG", primary_display)
        if not overrides:
            overrides = _overrides_for_run(primary_run) or {}
        if overrides:
            sample = list(overrides.items())[:3]
            diff_summary = ", ".join(f"{k}={v}" for k, v in sample)
            if len(overrides) > 3:
                diff_summary += f", +{len(overrides) - 3} more"
        else:
            diff_summary = "(no overrides recorded — non-winning variant or baseline)"
        rows.append({
            "run": primary_display,
            "run_keyed": primary_run,
            "side": side,
            "source": source,
            "category": category,
            "trades": agg["n"],
            "wr": round(agg["wr"], 4),
            "total_gain_pct": round(agg["total_gain"], 2),
            "sym_sharpe": round(agg["sym_sharpe_capped"], 4),
            "ts_first": agg["ts_first"],
            "ts_last": agg["ts_last"],
            "years": round(years, 4),
            "gain_per_yr": round(gain_per_yr, 2),
            "gain_per_mo": round(gain_per_mo, 2),
            "median_dur_sec": agg.get("median_dur_sec", 0),
            "p25_dur_sec": agg.get("p25_dur_sec", 0),
            "p75_dur_sec": agg.get("p75_dur_sec", 0),
            "max_dur_sec": agg.get("max_dur_sec", 0),
            "n_zero_dur": agg.get("n_zero_dur", 0),
            "n_under_5min": agg.get("n_under_5min", 0),
            "diff_summary": diff_summary,
            "diff_keys": overrides,
            "aliases": aliases,
            "n_aliases": len(aliases),
        })
    rows.sort(key=lambda r: -r["sym_sharpe"])
    return jsonify({"sym": sym, "n_tests": len(rows), "tests": rows})


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


# ============== BACKTEST REVIEW (per-symbol, TF-locked, no marker cap) ==============
# 2026-05-18: User-requested page after struct_v4_review put 3m trades on D charts.
# Mandate: crypto trades plot ONLY on 3m candles, stock trades plot ONLY on 5m.
# Zoom-out happens on the x-axis (more bars visible at smaller width) — NEVER by
# switching to a higher TF candle. Trade markers must always sit on the bar they
# fired on; no resampling. Separate page per asset class (no mixing in one URL).

_CONFIG_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _load_config_for(asset: str) -> Dict[str, Any]:
    """Parse config.py (crypto) or config_tradier.py (stocks) into a {KEY: value}
    dict, using ast.literal_eval for simple literal RHS values. Non-literal
    assignments (computed/lambdas/dicts-with-calls) are skipped — they're not
    useful as "untouched defaults" anyway. Cached on file mtime."""
    fname = "config.py" if asset == "crypto" else "config_tradier.py"
    path = BASE_PATH / fname
    try:
        mt = path.stat().st_mtime
    except OSError:
        return {}
    cached = _CONFIG_CACHE.get(asset)
    if cached and cached[0] == mt:
        return cached[1]
    import ast as _ast
    out: Dict[str, Any] = {}

    def _walk(body):
        # Walk both module-level and class-bodies / function bodies so we pick
        # up Config-class fields (KEY: type = value → AnnAssign) and any plain
        # KEY = value at module level. Functions are walked because some knob
        # defaults live inside get_config()/__init__-style helpers.
        for node in body:
            if isinstance(node, _ast.Assign):
                try:
                    v = _ast.literal_eval(node.value)
                except Exception:
                    continue
                for t in node.targets:
                    if isinstance(t, _ast.Name) and t.id.isupper() and len(t.id) >= 2:
                        out[t.id] = v
            elif isinstance(node, _ast.AnnAssign):
                if node.value is None:
                    continue
                t = node.target
                if not (isinstance(t, _ast.Name) and t.id.isupper() and len(t.id) >= 2):
                    continue
                try:
                    out[t.id] = _ast.literal_eval(node.value)
                except Exception:
                    pass
            elif isinstance(node, (_ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)):
                _walk(node.body)

    try:
        tree = _ast.parse(path.read_text())
        _walk(tree.body)
    except Exception:
        pass
    _CONFIG_CACHE[asset] = (mt, out)
    return out


@app.route("/run_knobs")
def run_knobs():
    """Knob diff for one run: which knobs were EXPLICITLY TESTED via overrides,
    and what the current LIVE-config value is for everything else.

    {
      tested:    [{key, v_run, v_live, differs}],   # the overrides_json of the run
      untouched: [{key, v_live}],                    # current config values, filtered
      overrides_present: bool,                       # false → run had no recorded overrides
      asset:    crypto|stocks,
      run:      <run_id>,
    }

    `untouched` defaults to a curated "important knob" subset (heuristic: matches
    common knob substrings like ENABLED, _PCT, WT_, R1_/R2_/R3_, HEDGE_, HTF_,
    STDEV_, DC_, ATR_, MFI_, RSI_, etc.). Pass ?include_all=1 to get every parsed
    UPPER_CASE attribute (large)."""
    run = request.args.get("run", "")
    asset = request.args.get("asset", "crypto").lower()
    if asset not in ("crypto", "stocks"):
        return jsonify({"error": "asset must be crypto or stocks"}), 400
    if not run:
        return jsonify({"error": "run required"}), 400
    # Mirror /tests_for_symbol dual-lookup: hourly_reconfig active_config wins for
    # hr_<acct>:: runs whose tag matched, autonomous_winners.jsonl is the fallback.
    overrides: Dict[str, Any] = {}
    sym_arg = request.args.get("sym", "").upper()
    side_arg = request.args.get("side", "").upper()
    if run.startswith("hr_") and "::" in run:
        try:
            acct = run.split("::", 1)[0][3:]
            bare = run.split("::", 1)[1]
            # bare = "<SYM>__<SIDE>__<tag>"
            parts = bare.split("__", 2)
            if len(parts) == 3:
                sym_parsed, side_parsed, tag = parts
                sym_use = sym_arg or sym_parsed
                side_use = side_arg or side_parsed
                overrides = _hourly_reconfig_overrides(acct, sym_use, side_use, tag) or {}
        except Exception:
            overrides = {}
    if not overrides:
        overrides = _overrides_for_run(run) or {}
    live = _load_config_for(asset)
    tested: List[Dict[str, Any]] = []
    for k, v in overrides.items():
        v_live = live.get(k, None)
        in_config = k in live
        tested.append({
            "key": k,
            "v_run": v,
            "v_live": v_live if in_config else "<not in config>",
            "differs": (in_config and v != v_live),
            "in_config": in_config,
        })
    tested.sort(key=lambda r: (not r["differs"], r["key"]))
    include_all = request.args.get("include_all", "0") == "1"
    untouched_keys = [k for k in live.keys() if k not in overrides]
    if not include_all:
        SCOPES = ("ENABLED", "_PCT", "_THRESHOLD", "_BAND", "_RATIO", "_MIN", "_MAX",
                  "_HOURS", "_BARS", "_TFS", "_GATE", "_MODE", "_SLOW", "_FAST",
                  "HEDGE_", "WT_", "DC_", "R1_", "R2_", "R3_", "HTF_", "STDEV_",
                  "MFI_", "RSI_", "ADX_", "BB_", "ATR_", "SCALP_", "BREAKOUT_",
                  "RULE_", "OBLIGATORY_", "NOLOSS_", "STRICT_", "FORCE_OPEN",
                  "DUP_GUARD", "BREAKEVEN_", "PARTIAL_PROFIT", "TRAIL_")
        untouched_keys = [k for k in untouched_keys if any(s in k for s in SCOPES)]
    untouched_keys.sort()
    untouched = [{"key": k, "v_live": live.get(k)} for k in untouched_keys]
    return jsonify({
        "run": run,
        "asset": asset,
        "n_tested": len(tested),
        "n_untouched": len(untouched),
        "n_config_keys": len(live),
        "tested": tested,
        "untouched": untouched,
        "overrides_present": bool(overrides),
    })


_BACKTEST_REVIEW_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg:#0d1117; --bg2:#161b22; --bd:#30363d; --fg:#c9d1d9; --mute:#8b949e;
    --acc:#58a6ff; --win:#3fb950; --los:#f85149; --warn:#d29922;
  }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         margin: 0; padding: 0; background: var(--bg); color: var(--fg); font-size: 13px; }
  header { padding: 8px 16px; background: var(--bg2); border-bottom: 1px solid var(--bd);
           display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
  header h1 { margin: 0; font-size: 16px; color: var(--acc); }
  header .tflock { background:#3d1114; color:var(--los); padding:2px 8px; border-radius:4px;
                   font-size:11px; font-weight:bold; }
  header .pill { background: var(--bg); border: 1px solid var(--bd); padding: 3px 8px;
                 border-radius: 4px; font-size: 11px; color: var(--mute); }
  header a { color: var(--acc); text-decoration: none; }
  header a:hover { text-decoration: underline; }
  select, input, button { background: var(--bg); color: var(--fg); border: 1px solid var(--bd);
                          padding: 4px 8px; border-radius: 4px; font-size: 12px; }
  button { cursor: pointer; }
  button:hover { background: var(--bd); }
  .layout { display: grid; grid-template-columns: 1fr 360px; gap: 0; height: calc(100vh - 49px); }
  .left { display: flex; flex-direction: column; min-width: 0; }
  .right { background: var(--bg2); border-left: 1px solid var(--bd); overflow-y: auto; padding: 12px; }
  #chart { flex: 0 0 56%; min-height: 320px; border-bottom: 1px solid var(--bd); }
  #equity { flex: 0 0 14%; min-height: 90px; border-bottom: 1px solid var(--bd); }
  #tradetable-wrap { flex: 1 1 30%; overflow-y: auto; }
  table.trades { width: 100%; border-collapse: collapse; font-size: 11px; }
  table.trades th { position: sticky; top: 0; background: var(--bg2); border-bottom: 1px solid var(--bd);
                    padding: 5px 6px; text-align: left; color: var(--acc); cursor: pointer; user-select: none; }
  table.trades th:hover { background: var(--bd); }
  table.trades th.sorted::after { content: ' \25BE'; color: var(--acc); }
  table.trades th.sorted-asc::after { content: ' \25B4'; }
  table.trades td { padding: 3px 6px; border-bottom: 1px solid #21262d; font-family: ui-monospace, monospace; }
  table.trades tr:hover { background: var(--bg2); cursor: pointer; }
  table.trades tr.flash { background: #2d2400 !important; }
  td.win { color: var(--win); }
  td.los { color: var(--los); }
  td.long { color: var(--acc); }
  td.short { color: var(--warn); }
  .banner { padding: 6px 12px; font-size: 12px; }
  .banner.diag { background: #3d1114; color: var(--los); font-weight: bold; }
  .banner.ok { background: #0d2818; color: var(--win); }
  .right h3 { margin: 8px 0 6px; font-size: 12px; color: var(--acc); border-bottom: 1px solid var(--bd); padding-bottom: 4px; }
  .right h3:first-child { margin-top: 0; }
  table.knobs { width: 100%; border-collapse: collapse; font-size: 11px; }
  table.knobs td { padding: 2px 4px; border-bottom: 1px solid #21262d; font-family: ui-monospace, monospace; vertical-align: top; }
  table.knobs td.k { color: var(--fg); word-break: break-all; }
  table.knobs td.v { color: var(--mute); text-align: right; max-width: 110px; word-break: break-all; }
  table.knobs tr.differ td.k { color: var(--win); font-weight: bold; }
  table.knobs tr.differ td.v { color: var(--win); }
  table.knobs tr.differ td.live { color: var(--mute); text-decoration: line-through; }
  table.runs { width: 100%; border-collapse: collapse; font-size: 11px; }
  table.runs th { background: var(--bg); border-bottom: 1px solid var(--bd); padding: 4px 6px;
                  cursor: pointer; user-select: none; text-align: left; color: var(--acc); position: sticky; top: 0; }
  table.runs th:hover { background: var(--bd); }
  table.runs th.sorted::after { content: ' \25BE'; }
  table.runs th.sorted-asc::after { content: ' \25B4'; }
  table.runs td { padding: 3px 6px; border-bottom: 1px solid #21262d; font-family: ui-monospace, monospace; }
  table.runs tr { cursor: pointer; }
  table.runs tr:hover { background: var(--bd); }
  table.runs tr.active { background: #0d1b2e; }
  .runlist-wrap { max-height: 240px; overflow-y: auto; margin-bottom: 10px; border: 1px solid var(--bd); border-radius: 4px; }
  .pill-row { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 8px; }
  .pill-row .pill { padding: 2px 6px; font-size: 10px; }
  .empty { color: var(--mute); font-style: italic; padding: 12px; text-align: center; }
  .ts-mode-toggle { font-size: 10px; color: var(--mute); }
  .ts-mode-toggle label { cursor: pointer; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__</h1>
  <span class="tflock">TF LOCKED: __BASE_TF__ (struct_v4_review lesson — no 3m markers on D charts)</span>
  <label>Symbol: <select id="symPicker"></select></label>
  <span class="pill" id="runCount">— runs</span>
  <span class="pill" id="resultStats">—</span>
  <label class="ts-mode-toggle"><input type="checkbox" id="utcMode" checked> UTC timestamps</label>
  <span style="margin-left:auto"><a href="/">← chart_server index</a> &nbsp;|&nbsp;
    <a href="/backtest-review/__OTHER_ASSET__">switch to __OTHER_ASSET__</a></span>
</header>
<div id="diagBanner" class="banner" style="display:none"></div>
<div class="layout">
  <div class="left">
    <div id="chart"></div>
    <div id="equity"></div>
    <div id="tradetable-wrap">
      <table class="trades" id="tradeTable">
        <thead><tr>
          <th data-col="idx">#</th>
          <th data-col="entry_ts">Entry</th>
          <th data-col="exit_ts">Exit</th>
          <th data-col="side">Side</th>
          <th data-col="entry_price">Entry $</th>
          <th data-col="exit_price">Exit $</th>
          <th data-col="pnl_pct">PnL %</th>
          <th data-col="duration">Dur</th>
          <th data-col="entry_reason">Entry reason</th>
          <th data-col="exit_reason">Exit reason</th>
        </tr></thead>
        <tbody><tr><td colspan="10" class="empty">Pick a symbol then a run.</td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="right">
    <h3>Runs for this symbol <span style="float:right; font-size:10px; color:var(--mute)" id="runListMeta"></span></h3>
    <div class="pill-row">
      <span class="pill">Sort:</span>
      <select id="runSort" style="font-size:10px;padding:1px 4px">
        <option value="ts_last">Date (most recent)</option>
        <option value="sym_sharpe">Pool/Sym Sharpe</option>
        <option value="gain_per_mo">Gain / month</option>
        <option value="gain_per_yr">Gain / year</option>
        <option value="trades">Trades</option>
        <option value="wr">Win rate</option>
      </select>
      <input id="runFilter" placeholder="filter…" style="font-size:10px;padding:1px 4px;flex:1;min-width:60px">
    </div>
    <div class="runlist-wrap">
      <table class="runs" id="runTable">
        <thead><tr>
          <th data-col="run">Run</th>
          <th data-col="side">Side</th>
          <th data-col="ts_last">Date</th>
          <th data-col="sym_sharpe">SymSh</th>
          <th data-col="gain_per_mo">%/mo</th>
          <th data-col="wr">WR</th>
          <th data-col="trades">N</th>
        </tr></thead>
        <tbody><tr><td colspan="7" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>

    <h3>Knobs TESTED in this run <span id="knobTestedCount" style="float:right;font-size:10px;color:var(--mute)"></span></h3>
    <table class="knobs" id="knobsTested"><tbody><tr><td colspan="3" class="empty">Pick a run.</td></tr></tbody></table>

    <h3>Knobs UNTOUCHED (live config defaults) <span id="knobUntouchedCount" style="float:right;font-size:10px;color:var(--mute)"></span></h3>
    <div style="max-height:260px; overflow-y:auto">
      <table class="knobs" id="knobsUntouched"><tbody><tr><td colspan="2" class="empty">—</td></tr></tbody></table>
    </div>
    <div style="margin-top:6px"><label style="font-size:10px;color:var(--mute)"><input type="checkbox" id="knobsAll"> show ALL config keys (not just heuristic important set)</label></div>
  </div>
</div>

<script>
const ASSET = "__ASSET__";
const BASE_TF = "__BASE_TF__";
const DEFAULT_SYM = "__DEFAULT_SYM__";
const TF_SECONDS = { "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "D": 86400 };

let allRuns = [];
let currentSym = null;
let currentRun = null;
let currentTrades = [];
let tradeSortCol = "entry_ts";
let tradeSortDir = 1;
let runSortCol = "ts_last";
let runSortDir = -1;
let tradeSeries = null;
let candleSeries = null;
let equityChart = null, equitySeries = null, bhSeries = null;
let chart = null;

function fmtTs(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  const utc = document.getElementById("utcMode").checked;
  if (utc) {
    return d.toISOString().slice(0,16).replace("T", " ");
  }
  return d.toLocaleString();
}
function fmtDur(secs) {
  if (!secs || secs < 0) return "";
  if (secs < 3600) return Math.round(secs/60) + "m";
  if (secs < 86400) return (secs/3600).toFixed(1) + "h";
  return (secs/86400).toFixed(1) + "d";
}
function fmtPct(x, digits=2) { if (x === null || x === undefined || isNaN(x)) return ""; return (x>=0?"+":"") + x.toFixed(digits) + "%"; }
function fmtNum(x, digits=4) { if (x === null || x === undefined || isNaN(x)) return ""; return Number(x).toFixed(digits); }

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return await r.json();
}

async function loadSymbols() {
  const sel = document.getElementById("symPicker");
  let syms = await fetchJSON("/symbols");
  // Filter by asset class.
  syms = syms.filter(s => {
    const isCrypto = s.endsWith("USDC") || s.endsWith("USDT");
    return ASSET === "crypto" ? isCrypto : !isCrypto;
  });
  syms.sort();
  sel.innerHTML = syms.map(s => `<option value="${s}">${s}</option>`).join("");
  const initial = syms.includes(DEFAULT_SYM) ? DEFAULT_SYM : syms[0];
  sel.value = initial;
  sel.addEventListener("change", () => onSymbolChange(sel.value));
  return initial;
}

async function onSymbolChange(sym) {
  currentSym = sym;
  currentRun = null;
  currentTrades = [];
  document.getElementById("resultStats").textContent = "—";
  document.getElementById("diagBanner").style.display = "none";
  await loadRuns(sym);
  // Auto-pick top of sorted list
  if (allRuns.length) {
    await onRunChange(allRuns[0]);
  } else {
    renderTradeTable([]);
    renderKnobs(null);
    drawChartBars();
  }
}

async function loadRuns(sym) {
  const tbody = document.querySelector("#runTable tbody");
  tbody.innerHTML = `<tr><td colspan="7" class="empty">Loading runs for ${sym}…</td></tr>`;
  try {
    const data = await fetchJSON(`/tests_for_symbol?sym=${encodeURIComponent(sym)}`);
    allRuns = data.tests || [];
    document.getElementById("runCount").textContent = `${allRuns.length} runs`;
    document.getElementById("runListMeta").textContent = `(content-dedup'd)`;
    renderRunList();
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">Error: ${e.message}</td></tr>`;
    allRuns = [];
  }
}

function renderRunList() {
  const tbody = document.querySelector("#runTable tbody");
  const flt = (document.getElementById("runFilter").value || "").toLowerCase();
  let rows = allRuns.filter(r => !flt
                                  || (r.run||"").toLowerCase().includes(flt)
                                  || (r.diff_summary||"").toLowerCase().includes(flt));
  rows.sort((a, b) => {
    const av = a[runSortCol], bv = b[runSortCol];
    if (av === bv) return 0;
    if (av === null || av === undefined) return 1;
    if (bv === null || bv === undefined) return -1;
    return runSortDir * (av < bv ? -1 : 1);
  });
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty">No runs match.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const dateStr = r.ts_last ? new Date(r.ts_last*1000).toISOString().slice(0,10) : "—";
    const cls = (currentRun && r.run_keyed === currentRun) ? "active" : "";
    const sideClr = r.side === "LONG" ? "long" : (r.side === "SHORT" ? "short" : "");
    const shCls = r.sym_sharpe >= 1 ? "win" : (r.sym_sharpe < 0 ? "los" : "");
    return `<tr class="${cls}" data-run="${r.run_keyed}" title="${r.diff_summary||''}">
      <td>${r.run}</td>
      <td class="${sideClr}">${r.side||''}</td>
      <td>${dateStr}</td>
      <td class="${shCls}">${fmtNum(r.sym_sharpe,3)}</td>
      <td class="${r.gain_per_mo>=0?'win':'los'}">${fmtNum(r.gain_per_mo,2)}</td>
      <td>${(r.wr*100).toFixed(1)}%</td>
      <td>${r.trades}</td>
    </tr>`;
  }).join("");
  // Update sort header marker
  document.querySelectorAll("#runTable th").forEach(th => {
    th.classList.remove("sorted","sorted-asc");
    if (th.dataset.col === runSortCol) th.classList.add(runSortDir<0 ? "sorted" : "sorted-asc");
  });
  // Wire clicks
  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => {
      const run = allRuns.find(r => r.run_keyed === tr.dataset.run);
      if (run) onRunChange(run);
    });
  });
}

async function onRunChange(runRow) {
  currentRun = runRow.run_keyed;
  document.querySelectorAll("#runTable tr").forEach(tr => tr.classList.toggle("active", tr.dataset.run === currentRun));
  // Result stats banner
  const tot = (runRow.gain_per_yr||0).toFixed(1);
  document.getElementById("resultStats").textContent =
    `${runRow.trades} trades · WR ${(runRow.wr*100).toFixed(1)}% · SymSh ${fmtNum(runRow.sym_sharpe,3)} · ${tot}%/yr · ${fmtNum(runRow.gain_per_mo,2)}%/mo`;
  // Diagnostic banner per CLAUDE.md sample floor
  const minSym = ASSET === "crypto" ? 48 : 100;
  const banner = document.getElementById("diagBanner");
  if (runRow.trades < 30 || (runRow.years||0) < 1.0) {
    banner.className = "banner diag";
    banner.style.display = "block";
    banner.textContent = `[DIAGNOSTIC ONLY] trades=${runRow.trades} years=${(runRow.years||0).toFixed(2)} — below sample floor. DO NOT promote to live.`;
  } else {
    banner.style.display = "none";
  }
  await Promise.all([
    loadAndDrawTrades(runRow),
    loadAndDrawKnobs(runRow),
  ]);
}

async function loadAndDrawTrades(runRow) {
  const data = await fetchJSON(`/backtest_trades?run=${encodeURIComponent(runRow.run_keyed)}&sym=${encodeURIComponent(currentSym)}&max=999999`);
  currentTrades = data.trades || [];
  await drawChartBars(runRow);
  renderTradeTable(currentTrades);
  await drawEquity(runRow);
}

function tradeWindow(trades) {
  // Default visible: span the run, padded ±10% of duration on each side.
  if (!trades.length) return [null, null];
  let lo = Infinity, hi = -Infinity;
  for (const t of trades) {
    const e = t.entry_ts || 0;
    const x = t.exit_ts || e;
    if (e && e < lo) lo = e;
    if (x && x > hi) hi = x;
  }
  if (!isFinite(lo) || !isFinite(hi)) return [null, null];
  const pad = Math.max(86400, (hi - lo) * 0.05);
  return [lo - pad, hi + pad];
}

async function drawChartBars(runRow) {
  if (!chart) initCharts();
  const [lo, hi] = tradeWindow(currentTrades);
  let url = `/klines?sym=${encodeURIComponent(currentSym)}&tf=${BASE_TF}&max=30000`;
  if (lo && hi) url += `&start=${lo}&end=${hi}`;
  let bars = [];
  try { bars = await fetchJSON(url); } catch(e) { bars = []; }
  if (!Array.isArray(bars)) bars = [];
  candleSeries.setData(bars.map(b => ({ time: b.t, open: b.o, high: b.h, low: b.l, close: b.c })));
  // Markers on base TF only — explicitly no resampling. Each trade emits TWO markers.
  // No cap: render every trade. lightweight-charts handles ~50k markers comfortably.
  const markers = [];
  for (const t of currentTrades) {
    const isLong = (t.side || "LONG").toUpperCase() === "LONG";
    const isWin = (t.pnl_pct || 0) > 0;
    if (t.entry_ts) {
      markers.push({
        time: t.entry_ts,
        position: isLong ? "belowBar" : "aboveBar",
        color: isLong ? "#58a6ff" : "#d29922",
        shape: isLong ? "arrowUp" : "arrowDown",
        text: isLong ? "L" : "S",
      });
    }
    if (t.exit_ts) {
      markers.push({
        time: t.exit_ts,
        position: isLong ? "aboveBar" : "belowBar",
        color: isWin ? "#3fb950" : "#f85149",
        shape: "circle",
        text: (t.pnl_pct>=0?"+":"") + (t.pnl_pct||0).toFixed(1) + "%",
      });
    }
  }
  // LWC needs markers sorted by time.
  markers.sort((a,b) => a.time - b.time);
  candleSeries.setMarkers(markers);
  if (lo && hi) {
    chart.timeScale().setVisibleRange({ from: lo, to: hi });
  }
}

async function drawEquity(runRow) {
  const url = `/equity_curves?runs=${encodeURIComponent(runRow.run_keyed)}&sym=${encodeURIComponent(currentSym)}&max=5000`;
  let data = {};
  try { data = await fetchJSON(url); } catch(e) { return; }
  const eq = (data.runs && data.runs[runRow.run_keyed]) || [];
  equitySeries.setData(eq.map(p => ({ time: p.t, value: p.v })));
  const bh = data.buy_hold || [];
  bhSeries.setData(bh.map(p => ({ time: p.t, value: p.v })));
}

function initCharts() {
  const opts = {
    layout: { background: { color: "#0d1117" }, textColor: "#c9d1d9" },
    grid: { vertLines: { color: "#21262d" }, horzLines: { color: "#21262d" } },
    timeScale: { borderColor: "#30363d", timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: "#30363d" },
    crosshair: { mode: 1 },
  };
  chart = LightweightCharts.createChart(document.getElementById("chart"), opts);
  candleSeries = chart.addCandlestickSeries({
    upColor: "#3fb950", downColor: "#f85149", borderVisible: false,
    wickUpColor: "#3fb950", wickDownColor: "#f85149",
  });
  equityChart = LightweightCharts.createChart(document.getElementById("equity"), {
    ...opts,
    timeScale: { ...opts.timeScale, visible: false },
  });
  equitySeries = equityChart.addLineSeries({ color: "#58a6ff", lineWidth: 2, priceLineVisible: false });
  bhSeries = equityChart.addLineSeries({ color: "#8b949e", lineWidth: 1, priceLineVisible: false, lineStyle: 2 });
  // Sync x-axis with main chart.
  chart.timeScale().subscribeVisibleTimeRangeChange(r => {
    if (r) try { equityChart.timeScale().setVisibleRange(r); } catch(e){}
  });
  window.addEventListener("resize", () => {
    if (chart) chart.applyOptions({ width: document.getElementById("chart").clientWidth });
    if (equityChart) equityChart.applyOptions({ width: document.getElementById("equity").clientWidth });
  });
  setTimeout(() => {
    chart.applyOptions({ width: document.getElementById("chart").clientWidth, height: document.getElementById("chart").clientHeight });
    equityChart.applyOptions({ width: document.getElementById("equity").clientWidth, height: document.getElementById("equity").clientHeight });
  }, 50);
}

function renderTradeTable(trades) {
  const tbody = document.querySelector("#tradeTable tbody");
  if (!trades.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty">No trades for this run.</td></tr>`;
    return;
  }
  const sorted = [...trades];
  sorted.sort((a, b) => {
    let av, bv;
    if (tradeSortCol === "duration") { av = (a.exit_ts||0) - (a.entry_ts||0); bv = (b.exit_ts||0) - (b.entry_ts||0); }
    else if (tradeSortCol === "idx") { av = trades.indexOf(a); bv = trades.indexOf(b); }
    else { av = a[tradeSortCol]; bv = b[tradeSortCol]; }
    if (av === bv) return 0;
    if (av === undefined || av === null) return 1;
    if (bv === undefined || bv === null) return -1;
    return tradeSortDir * (av < bv ? -1 : 1);
  });
  const html = sorted.map((t, i) => {
    const dur = (t.exit_ts||0) - (t.entry_ts||0);
    const pcCls = (t.pnl_pct||0) >= 0 ? "win" : "los";
    const sideCls = (t.side||"LONG").toUpperCase() === "LONG" ? "long" : "short";
    return `<tr data-entry="${t.entry_ts||0}" data-exit="${t.exit_ts||0}">
      <td>${trades.indexOf(t)+1}</td>
      <td>${fmtTs(t.entry_ts)}</td>
      <td>${fmtTs(t.exit_ts)}</td>
      <td class="${sideCls}">${t.side||""}</td>
      <td>${fmtNum(t.entry_price,6)}</td>
      <td>${fmtNum(t.exit_price,6)}</td>
      <td class="${pcCls}">${fmtPct(t.pnl_pct)}</td>
      <td>${fmtDur(dur)}</td>
      <td>${t.entry_reason||t.entry_type||""}</td>
      <td>${t.exit_reason||""}</td>
    </tr>`;
  }).join("");
  tbody.innerHTML = html;
  document.querySelectorAll("#tradeTable th").forEach(th => {
    th.classList.remove("sorted","sorted-asc");
    if (th.dataset.col === tradeSortCol) th.classList.add(tradeSortDir<0 ? "sorted" : "sorted-asc");
  });
  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => {
      const e = Number(tr.dataset.entry||0), x = Number(tr.dataset.exit||0);
      if (e && x && chart) {
        const pad = Math.max(TF_SECONDS[BASE_TF]*30, (x-e) * 3);
        chart.timeScale().setVisibleRange({ from: e - pad, to: x + pad });
      }
      document.querySelectorAll("#tradeTable tr").forEach(r => r.classList.remove("flash"));
      tr.classList.add("flash");
    });
  });
}

async function loadAndDrawKnobs(runRow) {
  const all = document.getElementById("knobsAll").checked ? "&include_all=1" : "";
  let data;
  try {
    const sideParam = runRow.side ? `&side=${encodeURIComponent(runRow.side)}` : "";
    data = await fetchJSON(`/run_knobs?run=${encodeURIComponent(runRow.run_keyed)}&asset=${ASSET}&sym=${encodeURIComponent(currentSym)}${sideParam}${all}`);
  } catch(e) {
    document.querySelector("#knobsTested tbody").innerHTML = `<tr><td class="empty">Error loading knobs.</td></tr>`;
    return;
  }
  renderKnobs(data);
}

function renderKnobs(data) {
  const tt = document.querySelector("#knobsTested tbody");
  const tu = document.querySelector("#knobsUntouched tbody");
  if (!data) { tt.innerHTML = `<tr><td colspan="3" class="empty">—</td></tr>`; tu.innerHTML = `<tr><td colspan="2" class="empty">—</td></tr>`; return; }
  document.getElementById("knobTestedCount").textContent = data.n_tested ? `${data.n_tested} keys` : "(none recorded)";
  document.getElementById("knobUntouchedCount").textContent = `${data.n_untouched} / ${data.n_config_keys} live keys`;
  if (!data.overrides_present) {
    tt.innerHTML = `<tr><td colspan="3" class="empty">No overrides recorded for this run.<br><span style="font-size:10px">(baseline run, or hourly_reconfig non-winning tag — see CLAUDE.md)</span></td></tr>`;
  } else {
    tt.innerHTML = data.tested.map(k => `
      <tr class="${k.differs?'differ':''}">
        <td class="k">${k.key}</td>
        <td class="v"><b>${escapeHtml(k.v_run)}</b></td>
        <td class="v live">${k.in_config ? escapeHtml(k.v_live) : '<i style="color:var(--warn)">not in config</i>'}</td>
      </tr>
    `).join("");
  }
  if (!data.untouched.length) {
    tu.innerHTML = `<tr><td colspan="2" class="empty">—</td></tr>`;
  } else {
    tu.innerHTML = data.untouched.map(k => `
      <tr>
        <td class="k">${k.key}</td>
        <td class="v">${escapeHtml(k.v_live)}</td>
      </tr>
    `).join("");
  }
}

function escapeHtml(v) {
  if (v === null || v === undefined) return "—";
  return String(v).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

// Wire up sort headers
document.querySelectorAll("#tradeTable th").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (tradeSortCol === col) tradeSortDir *= -1; else { tradeSortCol = col; tradeSortDir = 1; }
    renderTradeTable(currentTrades);
  });
});
document.querySelectorAll("#runTable th").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (runSortCol === col) runSortDir *= -1; else { runSortCol = col; runSortDir = -1; }
    document.getElementById("runSort").value = col;
    renderRunList();
  });
});
document.getElementById("runSort").addEventListener("change", e => {
  runSortCol = e.target.value;
  runSortDir = (runSortCol === "ts_last" || runSortCol === "sym_sharpe" || runSortCol === "gain_per_mo"
                || runSortCol === "gain_per_yr" || runSortCol === "wr" || runSortCol === "trades") ? -1 : 1;
  renderRunList();
});
document.getElementById("runFilter").addEventListener("input", renderRunList);
document.getElementById("knobsAll").addEventListener("change", () => { if (currentRun) {
  const runRow = allRuns.find(r => r.run_keyed === currentRun);
  if (runRow) loadAndDrawKnobs(runRow);
}});
document.getElementById("utcMode").addEventListener("change", () => renderTradeTable(currentTrades));

(async () => {
  const sym = await loadSymbols();
  await onSymbolChange(sym);
})();
</script>
</body>
</html>
"""


@app.route("/backtest-review/<asset>")
def backtest_review_page(asset: str):
    asset = asset.lower()
    if asset not in ("crypto", "stocks"):
        return f"<p>Unknown asset class: {asset}. Use /backtest-review/crypto or /backtest-review/stocks.</p>", 404
    base_tf = "3m" if asset == "crypto" else "5m"
    title = "Crypto Backtest Review" if asset == "crypto" else "Stocks Backtest Review"
    default_sym = "BTCUSDC" if asset == "crypto" else "NVDA"
    other = "stocks" if asset == "crypto" else "crypto"
    html = (_BACKTEST_REVIEW_HTML
            .replace("__TITLE__", title)
            .replace("__BASE_TF__", base_tf)
            .replace("__ASSET__", asset)
            .replace("__DEFAULT_SYM__", default_sym)
            .replace("__OTHER_ASSET__", other))
    from flask import make_response
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    return _no_cache(resp)


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
            # Warm overrides index so /tests_for_symbol responds quickly.
            global _overrides_index, _overrides_index_built_at
            t2 = _t.time()
            _overrides_index = _build_overrides_index()
            _overrides_index_built_at = _t.time()
            print(f"[precompute] overrides_index={len(_overrides_index)} runs in {_t.time()-t2:.1f}s", flush=True)
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
