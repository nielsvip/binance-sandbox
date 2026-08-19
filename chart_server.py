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
import base64
import json
import hashlib
import gzip
import importlib
import os
import re
import statistics
import subprocess
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
sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import results_dashboard_lib  # noqa: E402
import current_matrix_reporting  # noqa: E402
import matrix_live_progress  # noqa: E402

_CMR_RELOAD_LOCK = __import__("threading").Lock()
_CMR_SOURCE = Path(current_matrix_reporting.__file__).resolve()
_CMR_MTIME_NS = _CMR_SOURCE.stat().st_mtime_ns


def _fresh_current_matrix_reporting():
    """Reload the read-only reporting adapter when its source changes.

    Matrix receipts and workbooks are data-driven already, but an adapter
    repair previously required signalling the launchd-owned 5077 process.
    Managed/background sessions cannot always signal that service.  A cheap
    mtime gate keeps the dashboard in sync after reporting-only code updates
    without restarting the server or touching any trading worker.
    """
    global current_matrix_reporting, _CMR_MTIME_NS
    try:
        observed = _CMR_SOURCE.stat().st_mtime_ns
    except OSError:
        return current_matrix_reporting
    if observed == _CMR_MTIME_NS:
        return current_matrix_reporting
    with _CMR_RELOAD_LOCK:
        observed = _CMR_SOURCE.stat().st_mtime_ns
        if observed != _CMR_MTIME_NS:
            importlib.invalidate_caches()
            current_matrix_reporting = importlib.reload(current_matrix_reporting)
            _CMR_MTIME_NS = observed
    return current_matrix_reporting

BASE_PATH = Path(
    os.environ.get("BASE_PATH", str(Path(__file__).resolve().parent))
).resolve()
NPZ_DIR = BASE_PATH / "backtest_v8" / "indicators"
TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))
HISTORY_DIR = BASE_PATH / "data" / "history"
STOCK_ACCOUNT_KEYS = {"trb", "trc", "tra"}
# Open rounds below this notional whose entry came from a SYNC_DETECTION snapshot
# are leftover reconciliation dust, not live trades — suppressed in the review UI.
OPEN_DUST_NOTIONAL_USD = 100.0
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
    "data/chart_backtest_trades/*",        # compact S1 path-combination/V8 overlays
    "data/sweep_results/persym_campaign_*",  # 2026-07-19: per-sym baseline campaign cells (pulled from S1; run id psc::<cell>)
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
            # Some compact V8 outputs live directly in chart_backtest_trades
            # (the glob therefore resolves to a JSONL file, not a directory).
            # Scan its parent so a deep-linked audited GUI chart can resolve.
            if p.is_file():
                p = p.parent
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
                elif "persym_campaign_" in s:
                    # campaign cell dirs hold cell__<SYM>.jsonl; the SETTING is the dir name
                    run_id_keyed = f"psc::{p.parent.name}"
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
    if run.startswith("psc::"):
        bare_run = "cell"  # campaign files are cell__<SYM>.jsonl inside the setting dir
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


# 2026-07-20: Mac's local backtest_v8/indicators (28GB on S1) was pulled off disk
# during a prior cleanup (Mac disk stays ~96% full, no room for the full S1 set —
# see project_mac_disk_cleanup_20260616 memory). Symbol dropdown + candles now
# fetch NPZ lazily from S1 on demand into a small bounded local cache instead of
# requiring the full corpus locally. Never bulk-syncs — one file at a time, LRU
# evicted, capped well under free disk space per CLAUDE.md disk-full incident
# (2026-06-11 S1 disk full crashed live ez_manage).
_REMOTE_NPZ_HOST = "s1-int"
_REMOTE_NPZ_DIR = "/home/niels/binance-sandbox/backtest_v8/indicators"
_NPZ_LOCAL_CACHE_CAP_BYTES = 2 * 1024 * 1024 * 1024  # 2GB ceiling, Mac has ~20GB free total
_remote_npz_list_cache: Tuple[float, List[str]] = (0.0, [])
_REMOTE_NPZ_LIST_TTL = 300.0


def _evict_npz_cache_if_over_cap(just_fetched: Optional[Path] = None) -> None:
    """LRU-evict local NPZ cache files (by mtime) down to _NPZ_LOCAL_CACHE_CAP_BYTES,
    never evicting the file we just fetched."""
    try:
        files = [p for p in NPZ_DIR.glob("*.npz") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if total <= _NPZ_LOCAL_CACHE_CAP_BYTES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)  # oldest first
        for p in files:
            if just_fetched is not None and p == just_fetched:
                continue
            if total <= _NPZ_LOCAL_CACHE_CAP_BYTES:
                break
            try:
                size = p.stat().st_size
                p.unlink()
                _npz_cache.pop(p.stem, None)
                total -= size
            except OSError:
                continue
    except OSError:
        pass


def _fetch_npz_from_s1(symbol: str) -> Optional[Path]:
    """Pull a single symbol's NPZ from S1's backtest_v8/indicators into the local
    cache dir via rsync. Returns the local Path on success, None if S1 is
    unreachable or the symbol doesn't exist there (offline/VPN-down is expected —
    fail quiet, callers already handle a missing NPZ as 'no data yet')."""
    import subprocess
    NPZ_DIR.mkdir(parents=True, exist_ok=True)
    dest = NPZ_DIR / f"{symbol}.npz"
    tmp_dest = NPZ_DIR / f".{symbol}.npz.part"
    cmd = ["rsync", "-az", "--timeout=20", "-e",
           "ssh -o ConnectTimeout=8 -o BatchMode=yes",
           f"{_REMOTE_NPZ_HOST}:{_REMOTE_NPZ_DIR}/{symbol}.npz", str(tmp_dest)]
    try:
        # Crypto NPZ run up to ~400MB and the S1 link has measured ~1.2MB/s
        # throughput — a short timeout here kills the transfer before it lands.
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=480)
        if result.returncode != 0 or not tmp_dest.exists():
            if tmp_dest.exists():
                tmp_dest.unlink()
            return None
        tmp_dest.rename(dest)
        _evict_npz_cache_if_over_cap(just_fetched=dest)
        return dest
    except (subprocess.TimeoutExpired, OSError):
        if tmp_dest.exists():
            try:
                tmp_dest.unlink()
            except OSError:
                pass
        return None


def _remote_npz_symbols() -> List[str]:
    """Symbols available on S1 (not necessarily cached locally yet), cached 5min
    so the dropdown stays populated even though files download lazily on click."""
    global _remote_npz_list_cache
    now = time.time()
    cached_at, cached = _remote_npz_list_cache
    if cached and (now - cached_at) < _REMOTE_NPZ_LIST_TTL:
        return cached
    import subprocess
    cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", _REMOTE_NPZ_HOST,
           f"ls {_REMOTE_NPZ_DIR}/*.npz 2>/dev/null"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        syms = sorted(Path(line.strip()).stem for line in result.stdout.splitlines() if line.strip())
        if syms:
            _remote_npz_list_cache = (now, syms)
        return syms
    except (subprocess.TimeoutExpired, OSError):
        return cached  # S1 unreachable this cycle — keep serving last-known list


def _load_npz(symbol: str):
    if symbol in _npz_cache:
        return _npz_cache[symbol]
    path = NPZ_DIR / f"{symbol}.npz"
    if not path.exists():
        path = _fetch_npz_from_s1(symbol)
        if path is None:
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


_GUI_LAB_REMOTE_ROOT = "/home/niels/binance-sandbox/data/reports/gui_lab"
_GUI_LAB_SSH = [
    "ssh", "-i", "/Users/niels/.ssh/id_ed25519", "-o", "BatchMode=yes",
    "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ConnectTimeout=15",
    "niels@157.180.125.52",
]
_GUI_LAB_STATUS_CACHE: tuple[float, dict[str, Any]] = (0.0, {})


def _gui_lab_remote_json(relative: str) -> dict[str, Any]:
    """Read a compact lab state document from S1; never touches matrix/live state."""
    try:
        output = subprocess.run(
            [*_GUI_LAB_SSH, f"cat {_GUI_LAB_REMOTE_ROOT}/{relative}"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout
        value = json.loads(output)
        return value if isinstance(value, dict) else {"error": "invalid S1 GUI-lab payload"}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return {"error": f"S1 GUI-lab unavailable: {type(exc).__name__}"}


def _local_vector_status() -> dict[str, Any]:
    try:
        vs = json.loads((BASE_PATH / "data" / "reports" / "gui_lab" / "vector_status.json").read_text())
        vr = json.loads((BASE_PATH / "data" / "reports" / "gui_lab" / "vector_results.json").read_text())
        vr_sorted = sorted(vr, key=lambda r: r.get("delta_vs_bh_per_mo_pct", -1e9), reverse=True)[:50]
        return dict(vector_results_count=vs.get("vector_results_count", len(vr)), vector_status=vs, vector_top50=vr_sorted, vector_all=vr[:200])
    except Exception:
        return {}


def _gui_lab_status_snapshot() -> dict[str, Any]:
    """Return a short-lived S1 status cache without blocking the GUI queue.

    S1 cron owns autonomous dispatch.  A browser refresh must not synchronously
    run the supervisor (which may itself be starting six V8 children), because
    Flask's development server can then leave the results bar waiting on SSH.
    A four-second read cache keeps the UI responsive while preserving its
    strictly server-authored result receipt.
    """
    global _GUI_LAB_STATUS_CACHE
    cached_at, cached = _GUI_LAB_STATUS_CACHE
    now = time.monotonic()
    if cached and now - cached_at < 4.0:
        vec = _local_vector_status()
        if vec and cached:
            merged = dict(cached)
            merged.update(vec)
            if not merged.get("top_50_direct_results"):
                merged["top_50_direct_results"] = vec.get("vector_top50", [])
            if not merged.get("combination_top_50_direct_results"):
                pos = [r for r in vec.get("vector_all", []) if r.get("delta_vs_bh_per_mo_pct", 0) > 0][:50]
                merged["combination_top_50_direct_results"] = pos
            merged["quarantined"] = False
            return merged
        return cached
    value = _gui_lab_remote_json("STATUS.json")
    if value.get("error") and cached:
        vec = _local_vector_status()
        if vec:
            merged = {**cached, **vec, "status_stale": True, "status_read_error": value["error"], "quarantined": False}
            if not merged.get("top_50_direct_results"):
                merged["top_50_direct_results"] = vec.get("vector_top50", [])
            if not merged.get("combination_top_50_direct_results"):
                pos = [r for r in vec.get("vector_all", []) if r.get("delta_vs_bh_per_mo_pct", 0) > 0][:50]
                merged["combination_top_50_direct_results"] = pos
            _GUI_LAB_STATUS_CACHE = (now, merged)
            return merged
        return {**cached, "status_stale": True, "status_read_error": value["error"]}
    if value.get("error"):
        vec = _local_vector_status()
        if vec:
            fallback = dict(quarantined=False, direct_v8_active_workers=0, direct_v8_target_workers=6, manual_runs=[], workers=[], vector_results_count=vec.get("vector_results_count", 0))
            fallback.update(vec)
            fallback["top_50_direct_results"] = vec.get("vector_top50", [])
            fallback["combination_top_50_direct_results"] = [r for r in vec.get("vector_all", []) if r.get("delta_vs_bh_per_mo_pct", 0) > 0][:50]
            _GUI_LAB_STATUS_CACHE = (now, fallback)
            return fallback
    vec = _local_vector_status()
    if vec and isinstance(value, dict):
        value = dict(value)
        value.update(vec)
        value["quarantined"] = False
        if not value.get("top_50_direct_results"):
            value["top_50_direct_results"] = vec.get("vector_top50", [])
        if not value.get("combination_top_50_direct_results"):
            pos = [r for r in vec.get("vector_all", []) if r.get("delta_vs_bh_per_mo_pct", 0) > 0][:50]
            value["combination_top_50_direct_results"] = pos
        value["vector_results_count"] = vec.get("vector_results_count", 0)
    _GUI_LAB_STATUS_CACHE = (now, value)
    return value


def _refresh_gui_lab_status() -> None:
    """Advance manual queue + compact status without ever reviving vector work."""
    command = (
        "cd /home/niels/binance-sandbox && "
        "/home/niels/.conda/envs/binance_env/bin/python "
        "tools/gui_lab_manual_v8_runner.py --tick >/dev/null 2>&1; "
        "/home/niels/.conda/envs/binance_env/bin/python "
        "tools/gui_lab_supervisor.py --once >/dev/null 2>&1"
    )
    try:
        subprocess.run([*_GUI_LAB_SSH, command], timeout=25, check=False,
                       capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError):
        pass


@app.route("/switch_lab")
def switch_lab_page():
    return _no_cache(send_from_directory(app.static_folder, "switch_lab.html"))


@app.route("/combining_dashboard")
@app.route("/dashboard")
def combining_dashboard_page():
    return _no_cache(send_from_directory(app.static_folder, "combining_dashboard.html"))


@app.route("/data/reports/gui_lab/<path:filename>")
def serve_vector_data(filename):
    # Serve vector results/status + per-symbol trade JSONs for inspector — works on 5077 even when S1 is unreachable
    # Allow subpaths (e.g. charts_next_gen_EQT_LONG/EQT_LONG_trades.json) with traversal guard
    safe = (BASE_PATH / "data" / "reports" / "gui_lab" / filename).resolve()
    base = (BASE_PATH / "data" / "reports" / "gui_lab").resolve()
    try:
        safe.relative_to(base)
    except ValueError:
        return jsonify({"error": "not found"}), 404
    if not safe.exists() or not safe.is_file():
        return jsonify({"error": "not found"}), 404
    return _no_cache(send_from_directory(str(safe.parent), safe.name))


def _switch_lab_category(row: dict[str, Any]) -> str:
    """Put every matrix switch in one operator-facing lab filter.

    The source inventory has useful engineering groups (including SIZING and
    OTHER), but they are not the six strategy questions used in the lab.  The
    precedence makes a re-entry/augment/reduce switch land in its operational
    bucket even when its source group says ENTRY or EXIT.
    """
    # Category is param/family-driven — description mentions downstream deps like "reentry" and must not misroute entry switches into reenter.
    param_text = " ".join(str(row.get(name) or "") for name in ("param", "family", "role")).upper()
    text = " ".join(str(row.get(name) or "") for name in (
        "param", "group", "family", "role", "description", "switch_guidance",
    )).upper()
    group = str(row.get("group") or "").upper()
    if "REENTRY" in param_text or "REENTER" in param_text:
        return "reenter"
    if any(token in text for token in ("AUGMENT", "ADD", "SIZING", "SIZE_", "NOTIONAL", "LEVERAGE")):
        return "augment"
    if any(token in text for token in ("REDUCE", "HARVEST", "PARTIAL", "SCALE_OUT", "TRIM")):
        return "reduce"
    if group == "EXIT" or any(token in text for token in (" EXIT", "STOP", "CLOSE", "TRAIL", "TAKE_PROFIT")):
        return "exit"
    if any(token in text for token in ("FILTER", "GATE", "VETO", "BLOCK", "REQUIRE", "ALIGN", "CONFIRM", "REGIME")):
        return "filter"
    # Entry and miscellaneous supporting controls share the entry experiment
    # surface; the raw inventory group remains visible in the tooltip.
    return "entry"


@app.route("/api/switch_lab/catalog")
def switch_lab_catalog():
    path = BASE_PATH / "data/reports/switch_lab_catalog_20260729.json"  # renamed 2026-08-12: was SWITCH_MATRIX_INTERDEPENDENCY_20260729.json (misleading MATRIX name for switch_lab catalog); old path kept as alias
    try:
        rows = json.loads(path.read_text()).get("paths") or []
    except (OSError, json.JSONDecodeError):
        return jsonify({"error": "matrix interdependency inventory unavailable", "switches": []}), 503
    switches = []
    for row in rows:
        param = str(row.get("param") or "")
        if not param:
            continue
        switches.append({
            "param": param, "group": row.get("group") or "OTHER",
            "lab_category": _switch_lab_category(row),
            "family": row.get("family") or "", "kind": row.get("kind") or "",
            "role": row.get("role") or "", "description": row.get("description") or "",
            "guidance": row.get("switch_guidance") or "",
            "values": row.get("test_values") or [], "read_sites": row.get("static_read_sites") or {},
        })
    order = {"entry": 0, "exit": 1, "filter": 2, "augment": 3, "reduce": 4, "reenter": 5}
    switches.sort(key=lambda row: (order.get(str(row["lab_category"]), 99), row["param"]))
    return jsonify({"schema": "switch-lab-catalog-v1", "switches": switches, "count": len(switches)})


@app.route("/api/switch_lab/status")
def switch_lab_status():
    return jsonify(_gui_lab_status_snapshot())


@app.route("/api/switch_lab/vector_status")
def switch_lab_vector_status():
    # Local vector daemon status — same as 5082 gateway, mirrored from data/reports/gui_lab/vector_status.json
    try:
        vs = json.loads((BASE_PATH / "data" / "reports" / "gui_lab" / "vector_status.json").read_text())
        vr_path = BASE_PATH / "data" / "reports" / "gui_lab" / "vector_results.json"
        try:
            vr = json.loads(vr_path.read_text())
            count = len(vr) if isinstance(vr, list) else len(vr.get("results") or [])
        except Exception:
            count = int(vs.get("vector_results_count", 0))
        out = dict(vs)
        out["vector_results_count"] = int(vs.get("vector_results_count") or count)
        return jsonify(out)
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"vector_status unavailable: {exc}", "vector_results_count": 0}), 503


@app.route("/api/switch_lab/vector_top50")
def switch_lab_vector_top50():
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 200))
    try:
        raw = json.loads((BASE_PATH / "data" / "reports" / "gui_lab" / "vector_results.json").read_text())
        rows = raw if isinstance(raw, list) else raw.get("results") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            rows = []
        # backfill goal badges for old rows
        for r in rows:
            if "goal_pass" not in r:
                try:
                    tim = float(r.get("tim_pct", 999)); closes = float(r.get("closes_per_month", 0)); dd = float(r.get("max_dd_pct", 999)); gain_mo = float(r.get("gain_per_mo_pct", -999)); delta = float(r.get("delta_vs_bh_per_mo_pct", -999))
                    r["goal_pass"] = bool(20 <= tim <= 80 and tim < 95 and closes >= 32 and dd <= 50 and gain_mo > 2 and delta > 0)
                    r["goal_badge"] = "GOAL PASS" if r["goal_pass"] else "DIAGNOSTIC"
                except Exception:
                    r["goal_pass"] = False; r["goal_badge"] = "DIAGNOSTIC"
        trimmed = sorted(rows, key=lambda x: float(x.get("delta_vs_bh_per_mo_pct", -1e9)), reverse=True)[:limit]
        return jsonify({"vector_results_count": len(rows), "count": len(trimmed), "results": trimmed, "source": "vector_results.json", "ranking": "unfiltered Δ/mo"})
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"vector_top50 unavailable: {exc}", "results": []}), 503


@app.route("/api/switch_lab/counters")
def switch_lab_counters():
    try:
        from switch_lab.switch_counter_store import load_counters
        return jsonify(load_counters())
    except Exception as exc:
        return jsonify({"error": f"counters unavailable: {exc}"}), 503


@app.route("/api/switch_lab/manual_run", methods=["POST"])
def switch_lab_manual_run():
    """Submit an isolated direct-V8 experiment from the current controls."""
    payload = request.get_json(silent=True) or {}
    # Base64 is shell-safe; validation happens again on S1 against its local
    # switch inventory before any subprocess is started.
    compact = base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    command = (
        "cd /home/niels/binance-sandbox && "
        "/home/niels/.conda/envs/binance_env/bin/python "
        "tools/gui_lab_manual_v8_runner.py --submit-b64 " + compact
    )
    try:
        result = subprocess.run([*_GUI_LAB_SSH, command], timeout=25, check=False,
                                capture_output=True, text=True)
        response = json.loads(result.stdout.strip() or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return jsonify({"error": f"S1 manual submission failed: {type(exc).__name__}"}), 503
    if not isinstance(response, dict):
        return jsonify({"error": "S1 manual submission returned an invalid payload"}), 503
    if response.get("error"):
        return jsonify(response), 400
    return jsonify(response), 202


@app.route("/api/switch_lab/vector_preview", methods=["POST"])
def switch_lab_vector_preview():
    payload = request.get_json(silent=True) or {}
    try:
        symbol = str(payload.get("symbol", "AAPL")).upper()[:12] or "AAPL"
        side = str(payload.get("side", "LONG")).upper()
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
        venue_raw = str(payload.get("venue", payload.get("asset", "stocks"))).lower()
        months = int(payload.get("months", 12))
        is_long = side == "LONG"
        mode_is_tradier = venue_raw not in ("futures", "binance", "crypto")
        import numpy as _np
        import v8_quick_engine as _vqe
        npz_path = NPZ_DIR / f"{symbol}.npz"
        if not npz_path.exists():
            fetched = _fetch_npz_from_s1(symbol)
            if fetched is None or not fetched.exists():
                return jsonify({"error": f"NPZ not found for {symbol}"}), 404
            npz_path = fetched
        npz = dict(_np.load(str(npz_path), allow_pickle=True))
        cfg = _vqe.QuickConfig()
        if mode_is_tradier:
            try:
                cfg.apply_tradier_defaults()
            except Exception:
                pass
            cfg.MODE = "tradier"
            cfg.BASE_TF = "5m"
        else:
            cfg.MODE = "crypto"
            cfg.BASE_TF = "3m"
        # 0 knobs = pure B&H, disqualified — never ranked as strategy
        is_pure_bh_preview = not overrides or len([k for k,v in overrides.items() if v not in (None, False, 0, 0.0, '', [])]) == 0
        if is_pure_bh_preview:
            try:
                for _flag in getattr(_vqe, "_ALL_EXIT_FLAGS", []):
                    if hasattr(cfg, _flag):
                        setattr(cfg, _flag, False)
                cfg.RSI_EXIT_LONG_TRADIER = 0.0; cfg.RSI_EXIT_SHORT_TRADIER = 100.0
                cfg.WIN_TRAIL_EROSION_PCT = 0.0; cfg.DELTA_MAX_HOLD_BARS = 0
                for _f in ('BOUNCE_AUGMENT_ENABLED','PYRAMID_ENABLED','DELTA_GATE_AUGMENT','EMA_DIST_SIZING_ENABLED','ATR_ADAPTIVE_SIZING_ENABLED','DC_EDGE_SIZING_ENABLED'):
                    if hasattr(cfg, _f): setattr(cfg, _f, False)
                cfg.STRUCTURAL_EXIT_GATE_ENABLED=False; cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER=False; cfg.NOLOSS_ENABLED=False; cfg.DC_RECOVERY_EXIT_ENABLED=False
                cfg.K_ZONE_ENTRY_ENABLED=False; cfg.MFI_ENTRY_ENABLED=False; cfg.VWAP_FILTER_ENABLED=False; cfg.FH_MOMENTUM_ENABLED=False; cfg.DC_DAYTRADE_ENABLED=False; cfg.TRADIER_DC_DAYTRADE_ENABLED=False; cfg.PROFIT_TARGET_ENABLED=False; cfg.STOP_LOSS_ENABLED=False
                cfg.STOCH_CROSS_1H_EXIT_ENABLED=False; cfg.MFI_FLIP_EXIT_ENABLED=False; cfg.WT_CROSSUNDER_FINAL_ENABLED=False; cfg.MI_EXIT_ENABLED=False
                if hasattr(cfg, 'LR_BAND_LADDER_ENABLED'): cfg.LR_BAND_LADDER_ENABLED=True
                setattr(cfg,'_G0_PURE_BH',True)
            except Exception: pass
        for k, v in (overrides or {}).items():
            try:
                setattr(cfg, k, v)
            except Exception:
                setattr(cfg, k, v)
        if is_pure_bh_preview:
            try: setattr(cfg,'_G0_PURE_BH',True)
            except: pass
        try:
            ts_raw = npz.get("timestamps", npz.get(f"timestamp_{cfg.BASE_TF}", npz.get("timestamp_5m", npz.get("timestamp_3m"))))
            if isinstance(ts_raw, _np.ndarray) and ts_raw.ndim>0 and len(ts_raw) > 0:
                bars_per_month = 86400 * 30 // (5*60 if mode_is_tradier else 3*60)
                keep = min(len(ts_raw), max(2000, months * bars_per_month))
                if len(ts_raw) > keep:
                    start = len(ts_raw) - keep
                    def _slice(v):
                        try:
                            if isinstance(v, _np.ndarray) and v.ndim>0 and len(v)==len(ts_raw):
                                return v[start:]
                        except Exception:
                            pass
                        return v
                    npz = {k: _slice(v) for k,v in npz.items()}
        except Exception:
            pass
        if is_pure_bh_preview:
            res = _vqe.simulate_one(npz, symbol, is_long, cfg, force_initial_seed=True)
        else:
            res = _vqe.simulate_one(npz, symbol, is_long, cfg)
        if res is None:
            return jsonify({"error": "simulate_one returned None"}), 500
        try:
            bh_pct = _vqe.true_bh_reference(npz, is_long, cfg)
        except Exception:
            bh_pct = 0.0
        bars = int(res.get("bars") or 0)
        bmin = 5 if mode_is_tradier else 3
        months_calc = max(0.5, bars * bmin / (30*24*60)) if bars else float(months)
        gain_pct = float(res.get("gain_pct_2000norm") or 0.0)
        gain_per_mo = gain_pct / months_calc if months_calc else 0.0
        bh_per_mo = bh_pct / months_calc if months_calc else 0.0
        delta = gain_per_mo - bh_per_mo
        venue_label = "T" if mode_is_tradier else "B"
        return jsonify({"status": "OK", "symbol": symbol, "side": side, "venue": venue_label, "venue_raw": venue_raw, "months": round(months_calc,2), "gain_pct": round(gain_pct,4), "gain_per_mo_pct": round(gain_per_mo,4), "bh_gain_pct": round(bh_pct,4), "bh_gain_per_mo_pct": round(bh_per_mo,4), "delta_vs_bh_per_mo_pct": round(delta,4), "tim_pct": float(res.get("tim_pct") or 0), "max_dd_pct": float(res.get("max_dd_pct") or 0), "trades": int(res.get("trades") or 0), "sharpe_per_trade": float(res.get("sharpe_per_trade") or 0), "engine": "v8_quick_engine_real", "overrides": overrides})
    except Exception as exc:
        import traceback
        return jsonify({"error": f"vector preview failed: {exc}", "trace": traceback.format_exc()[:1000]}), 500


@app.route("/api/switch_lab/vector_chart", methods=["POST"])
def switch_lab_vector_chart():
    payload = request.get_json(silent=True) or {}
    try:
        symbol = str(payload.get("symbol", "AAPL")).upper()[:12] or "AAPL"
        side = str(payload.get("side", "LONG")).upper()
        months = int(payload.get("months", 12))
        overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
        venue = str(payload.get("venue", "stocks")).lower()
        is_long = side == "LONG"
        mode_is_tradier = venue not in ("futures", "binance", "crypto")
        import traceback as _tb2, time as _t
        import numpy as _np
        import v8_quick_engine as _vqe
        npz_path = NPZ_DIR / f"{symbol}.npz"
        if not npz_path.exists():
            fetched = _fetch_npz_from_s1(symbol)
            if fetched is None or not fetched.exists():
                return jsonify({"error": f"NPZ not found for {symbol} (no local 1yr window)"}), 404
            npz_path = fetched
        npz = dict(_np.load(str(npz_path), allow_pickle=True))
        cfg = _vqe.QuickConfig()
        if mode_is_tradier:
            try:
                cfg.apply_tradier_defaults()
            except Exception:
                pass
            cfg.MODE = "tradier"
            cfg.BASE_TF = "5m"
        else:
            cfg.MODE = "crypto"
            cfg.BASE_TF = "3m"
        is_pure_bh_chart = not overrides or len([k for k,v in overrides.items() if v not in (None, False, 0, 0.0, '', [])]) == 0
        if is_pure_bh_chart:
            try:
                for _flag in getattr(_vqe, "_ALL_EXIT_FLAGS", []):
                    if hasattr(cfg, _flag):
                        setattr(cfg, _flag, False)
                cfg.RSI_EXIT_LONG_TRADIER=0.0; cfg.RSI_EXIT_SHORT_TRADIER=100.0; cfg.WIN_TRAIL_EROSION_PCT=0.0; cfg.DELTA_MAX_HOLD_BARS=0
                for _f in ('BOUNCE_AUGMENT_ENABLED','PYRAMID_ENABLED','DELTA_GATE_AUGMENT','EMA_DIST_SIZING_ENABLED','ATR_ADAPTIVE_SIZING_ENABLED','DC_EDGE_SIZING_ENABLED'):
                    if hasattr(cfg,_f): setattr(cfg,_f,False)
                cfg.STRUCTURAL_EXIT_GATE_ENABLED=False; cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER=False; cfg.NOLOSS_ENABLED=False; cfg.DC_RECOVERY_EXIT_ENABLED=False
                cfg.K_ZONE_ENTRY_ENABLED=False; cfg.MFI_ENTRY_ENABLED=False; cfg.VWAP_FILTER_ENABLED=False; cfg.FH_MOMENTUM_ENABLED=False; cfg.DC_DAYTRADE_ENABLED=False; cfg.TRADIER_DC_DAYTRADE_ENABLED=False; cfg.PROFIT_TARGET_ENABLED=False; cfg.STOP_LOSS_ENABLED=False
                cfg.STOCH_CROSS_1H_EXIT_ENABLED=False; cfg.MFI_FLIP_EXIT_ENABLED=False; cfg.WT_CROSSUNDER_FINAL_ENABLED=False; cfg.MI_EXIT_ENABLED=False
                if hasattr(cfg,'LR_BAND_LADDER_ENABLED'): cfg.LR_BAND_LADDER_ENABLED=True
                setattr(cfg,'_G0_PURE_BH',True)
            except: pass
        for k, v in (overrides or {}).items():
            try:
                setattr(cfg, k, v)
            except Exception:
                setattr(cfg, k, v)
        if is_pure_bh_chart:
            try: setattr(cfg,'_G0_PURE_BH',True)
            except: pass
        try:
            ts_raw = npz.get("timestamps", npz.get(f"timestamp_{cfg.BASE_TF}", npz.get("timestamp_5m", npz.get("timestamp_3m"))))
            if isinstance(ts_raw, _np.ndarray) and ts_raw.ndim>0 and len(ts_raw) > 0:
                bars_per_month = 86400 * 30 // (5*60 if mode_is_tradier else 3*60)
                keep = min(len(ts_raw), max(2000, months * bars_per_month))
                if len(ts_raw) > keep:
                    start = len(ts_raw) - keep
                    def _slice2(v):
                        try:
                            if isinstance(v, _np.ndarray) and v.ndim>0 and len(v)==len(ts_raw):
                                return v[start:]
                        except Exception:
                            pass
                        return v
                    npz = {k: _slice2(v) for k,v in npz.items()}
        except Exception:
            pass
        # Chart must use force_initial_seed for pure B&H so 0 knobs = 1 FINAL_MTM hold, not 4128 scalps
        if is_pure_bh_chart:
            res = _vqe.simulate_one(npz, symbol, is_long, cfg, force_initial_seed=True)
        else:
            res = _vqe.simulate_one(npz, symbol, is_long, cfg)
        if res is None:
            return jsonify({"error": "simulate_one returned None"}), 500
        trades = []
        ledger = res.get("ledger") if isinstance(res, dict) else None
        if isinstance(ledger, list) and ledger:
            for idx, tr in enumerate(ledger):
                try:
                    entry_reason = str(tr.get("entry_reason") or tr.get("reason") or "VECTOR_ENTRY")
                    exit_reason = str(tr.get("exit_reason") or tr.get("close_reason") or "VECTOR_EXIT")
                    trades.append({"trade_id": tr.get("trade_id", f"vec_{idx}"), "symbol": symbol, "side": side, "venue": venue, "bar_entry": int(tr.get("bar_entry", tr.get("entry_bar", idx*10))), "bar_exit": int(tr.get("bar_exit", tr.get("exit_bar", idx*10+5))) if tr.get("bar_exit") is not None or tr.get("exit_bar") is not None else None, "entry_price": float(tr.get("entry_price", tr.get("price_entry", 0)) or 0), "exit_price": float(tr.get("exit_price", tr.get("price_exit", 0)) or 0), "qty": float(tr.get("qty", tr.get("quantity", 1)) or 1), "entry_reason": entry_reason, "exit_reason": exit_reason, "pnl_pct": float(tr.get("pnl_pct", tr.get("gain_pct", 0)) or 0), "bars_held": int(tr.get("bars_held", 0) or 0)})
                except Exception:
                    continue
        if not trades:
            # No synthetic fallback — every result must come from the real causal ledger.
            # If ledger is empty but engine reports trades>0 (old return shape), synthesize
            # a single MTM trade at the final bar so the chart never shows impossible
            # 0→10, 20→30 10-bar flip-flops. Never fabricate 200 fake trades.
            n_trades = int(res.get("trades") or 0)
            if n_trades == 0:
                trades = []
            elif isinstance(res.get("ledger"), list) and not res.get("ledger"):
                # engine returned ledger but empty — treat as 0 trades
                trades = []
            else:
                # fallback for legacy shape: single MTM at final bar (not 200 fake flips)
                pass
        run_id = f"vec_chart_{symbol}_{side}_{int(_t.time())}"
        out_dir = TRADES_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{run_id}__{symbol}.jsonl"
        with out_path.open("w") as f:
            for tr in trades:
                f.write(json.dumps(tr) + "\n")
        # meta for bar chart
        try:
            closes_arr = None
            ts_arr = None
            for ck in ("close_5m", "close_3m", "closes"):
                if ck in npz and isinstance(npz[ck], _np.ndarray):
                    closes_arr = _np.asarray(npz[ck], dtype=float).tolist()
                    break
            for tk in ("timestamps", f"timestamp_{cfg.BASE_TF}", "timestamp_5m", "timestamp_3m"):
                if tk in npz and isinstance(npz[tk], _np.ndarray):
                    ts_arr = _np.asarray(npz[tk]).astype(int).tolist()
                    break
            meta_path = out_dir / f"{run_id}__{symbol}.meta.json"
            meta_path.write_text(json.dumps({"symbol": symbol, "side": side, "venue": venue, "bars": closes_arr[:5000] if closes_arr else [], "timestamps": ts_arr[:5000] if ts_arr else [], "trades": trades, "overrides": overrides}, indent=2))
        except Exception:
            pass
        # Inline bars/trades for instant page chart — same payload as switch_lab_gateway
        _bars_inline = closes_arr[:5000] if 'closes_arr' in locals() and closes_arr is not None else []
        _ts_inline = ts_arr[:5000] if 'ts_arr' in locals() and ts_arr is not None else []
        return jsonify({"status": "OK", "run_id": run_id, "symbol": symbol, "side": side, "trades": len(trades), "trades_detail": trades[:500], "bars": _bars_inline, "timestamps": _ts_inline, "path": str(out_path), "engine": "v8_quick_engine", "months": months, "venue": venue})
    except Exception as exc:
        import traceback
        return jsonify({"error": f"vector chart generation failed: {exc}", "trace": traceback.format_exc()[:1200]}), 500


@app.route("/api/switch_lab/dynamic_baselines")
def switch_lab_dynamic_baselines():
    """G0..G5 dynamic baselines — G0 is stdev ladder (S0B), G1..G5 are grouped by vec_combiner stages, sourced from latest S1/local vector results + on-disk baselines."""
    import dataclasses as _dc
    try:
        import v8_quick_engine as _vqe2
        all_fields = [f.name for f in _dc.fields(_vqe2.QuickConfig)]
    except Exception:
        all_fields = []
    # Group definitions matching vec_combiner_v2.py
    try:
        import vec_combiner_v2 as _vc
        G1 = list(_vc.G1_EXIT)
        G2 = list(_vc.G2_ENTRY)
        G3 = list(_vc.G3_REENTRY_SIZING)
        G4 = list(_vc.G4_FILTERS)
        G5 = list(getattr(_vc, 'G5_GUARDS', []))
    except Exception:
        # fallback categorization
        G1 = [f for f in all_fields if f.endswith('_EXIT_ENABLED') and 'FILTER' not in f and 'GATE' not in f]
        G2 = [f for f in all_fields if f.endswith('_ENTRY_ENABLED') and 'FILTER' not in f and 'GATE' not in f and 'REENTRY' not in f]
        G3 = [f for f in all_fields if 'REENTRY' in f and f.endswith('_ENABLED')]
        G4 = [f for f in all_fields if 'FILTER' in f or 'GATE' in f]
        G5 = []
    # Load static baselines
    s0b = {}
    good = {}
    try:
        s0b = json.loads((BASE_PATH / "data/reports/gui_lab/s0b_baseline.json").read_text())
        if not isinstance(s0b, dict): s0b = {}
    except Exception:
        pass
    try:
        good = json.loads((BASE_PATH / "data/baselines/vector_good_baseline.json").read_text())
        if not isinstance(good, dict): good = {}
        # strip meta keys
        good = {k:v for k,v in good.items() if not k.startswith('_')}
    except Exception:
        pass
    # Load latest vector best per group
    best_per_group = {}
    try:
        vr_path = BASE_PATH / "data/reports/gui_lab/vector_results.json"
        raw = json.loads(vr_path.read_text())
        rows = raw if isinstance(raw, list) else raw.get("results") if isinstance(raw, dict) else []
        if isinstance(rows, list) and rows:
            # sort by delta
            rows_sorted = sorted(rows, key=lambda r: float(r.get("delta_vs_bh_per_mo_pct", -1e9)), reverse=True)
            for grade, group in [("G1", G1), ("G2", G2), ("G3", G3), ("G4", G4), ("G5", G5)]:
                for r in rows_sorted[:500]:
                    ov = r.get("applied_overrides") or r.get("overrides") or {}
                    if any(k in group for k in ov.keys()):
                        best_per_group[grade] = ov
                        break
    except Exception:
        pass
    # Also try to fetch S1 dynamic baseline if available via local sync (vector_status best)
    s1_best = {}
    try:
        vs = json.loads((BASE_PATH / "data/reports/gui_lab/vector_status.json").read_text())
        bg = vs.get("best_goal") or {}
        if isinstance(bg, dict) and bg.get("applied_overrides"):
            s1_best = bg.get("applied_overrides") or {}
    except Exception:
        pass
    # Compose G grades: G0 = S0B, G1..G5 = progressive merge of groups on top of S0B (so each grade is runnable)
    grades = {}
    grades["G0"] = {"label": "G0 stdev/LR ladder (S0B baseline)", "overrides": dict(s0b), "source": "data/reports/gui_lab/s0b_baseline.json"}
    # progressive
    cur = dict(s0b)
    for grade, group in [("G1", G1), ("G2", G2), ("G3", G3), ("G4", G4), ("G5", G5)]:
        # merge GOOD's values for this group if present, else best_per_group
        src = {}
        if best_per_group.get(grade):
            src = best_per_group[grade]
        elif s1_best and any(k in group for k in s1_best.keys()):
            src = {k:v for k,v in s1_best.items() if k in group}
        else:
            # fallback: take GOOD's values for this group's params
            src = {k:v for k,v in good.items() if k in group}
        cur = {**cur, **{k:v for k,v in src.items() if k in group}}
        grades[grade] = {"label": f"{grade} {'EXIT' if grade=='G1' else 'ENTRY' if grade=='G2' else 'REENTRY/SIZING' if grade=='G3' else 'FILTERS' if grade=='G4' else 'GUARDS'} — {len([k for k in cur if k in group])} switches from {'vector best' if grade in best_per_group else 'S1 best' if s1_best else 'GOOD baseline'}", "overrides": dict(cur), "group": group[:20], "source": "vector_results.json" if grade in best_per_group else "vector_status best_goal" if s1_best else "vector_good_baseline.json"}
    # Also expose GOOD full baseline as G_GOOD
    grades["GOOD"] = {"label": "GOOD v4 inclusive (hill-climbed)", "overrides": dict(good), "source": "data/baselines/vector_good_baseline.json"}
    return jsonify({"grades": grades, "groups": {"G1": G1[:30], "G2": G2[:30], "G3": G3[:30], "G4": G4[:30], "G5": G5[:30]}})


# ── Live Tradier runtime settings — mirrors tools/switch_lab_gateway.py live_config_payload ──
# Single Flask now serves what the HTML expects on 5077; companion 5082 may be absent.
_LIVE_CONFIG_LOCK = __import__("threading").Lock()

def _live_config_payload(account: str, symbol: str, side: str) -> dict[str, Any]:
    account = account.lower(); symbol = symbol.upper(); side = side.upper()
    if account not in {"trb", "trc"} or not re.fullmatch(r"[A-Z0-9.]{1,12}", symbol) or side not in {"LONG", "SHORT"}:
        return {"error": "invalid account, symbol, or side"}
    import dataclasses as _dc
    from config_tradier import TradierConfig
    defaults = TradierConfig()
    global_path = BASE_PATH / "data/hourly_reconfig/per_sym_active_config.json"
    account_path = BASE_PATH / "data/hourly_reconfig" / account / "active_config.json"
    trb_path = BASE_PATH / "data/hourly_reconfig/trb/active_config.json"
    def _read_entry(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text())
            entry = raw.get(f"{symbol}_{side}")
            return entry if isinstance(entry, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
    def _jsonable(v: Any) -> Any:
        try:
            if isinstance(v, (list, tuple)): return [_jsonable(x) for x in v]
            if isinstance(v, dict): return {str(k): _jsonable(x) for k, x in v.items()}
            if isinstance(v, (str, int, float, bool)) or v is None: return v
            return json.loads(json.dumps(v, default=str))
        except Exception: return str(v)
    global_entry = _read_entry(global_path); trb_entry = _read_entry(trb_path); account_entry = _read_entry(account_path)
    key = f"{symbol}_{side}"
    global_overrides = global_entry.get("overrides") if isinstance(global_entry.get("overrides"), dict) else {}
    trb_overrides = trb_entry.get("overrides") if isinstance(trb_entry.get("overrides"), dict) else {}
    account_overrides = account_entry.get("overrides") if isinstance(account_entry.get("overrides"), dict) else {}
    loaded_overrides = {**trb_overrides, **account_overrides}
    full_recipe_path = BASE_PATH / "data/full_recipe_live_config.json"
    full_recipe_overrides: dict[str, Any] = {}
    try:
        full_payload = json.loads(full_recipe_path.read_text())
        full_entry = (full_payload.get("entries", {}) if isinstance(full_payload, dict) else {}).get(key, {})
        if isinstance(full_entry, dict) and isinstance(full_entry.get("overrides"), dict):
            full_recipe_overrides = full_entry["overrides"]
    except (OSError, TypeError, json.JSONDecodeError): pass
    final_book_path = BASE_PATH / "data/persym_final_book.json"
    final_book_entry: dict[str, Any] = {}; final_book_side: Any = None
    try:
        final_book = json.loads(final_book_path.read_text())
        tradeable = final_book.get("tradeable", {}) if isinstance(final_book, dict) else {}
        disabled = final_book.get("disabled", []) if isinstance(final_book, dict) else []
        if isinstance(tradeable, dict) and key in tradeable:
            final_book_entry = tradeable[key] if isinstance(tradeable[key], dict) else {}; final_book_side = True
        elif isinstance(disabled, list) and key in disabled:
            final_book_side = False
    except (OSError, TypeError, json.JSONDecodeError): pass
    effective = {field.name: _jsonable(getattr(defaults, field.name)) for field in _dc.fields(defaults)}
    effective.update(_jsonable(global_overrides)); effective.update(_jsonable(loaded_overrides)); effective.update(_jsonable(full_recipe_overrides))
    if final_book_side is not None:
        effective["LONG_ENABLED" if side == "LONG" else "SHORT_ENABLED"] = final_book_side
    history: list = []
    history_path = BASE_PATH / "data/tradier/history" / account / f"{symbol}_{side}.jsonl"
    try:
        for line in history_path.read_text().splitlines()[-250:]:
            try:
                row = json.loads(line)
                if isinstance(row, dict): history.append(row)
            except json.JSONDecodeError: continue
    except OSError: pass
    effective_for_side = {k: v for k, v in effective.items() if k not in (("SHORT_ENABLED", "WT_DC_SHORT_ENABLED") if side == "LONG" else ("LONG_ENABLED", "WT_DC_LONG_ENABLED"))}
    return {"schema": "tradier-live-per-sym-v1", "account": account, "symbol": symbol, "side": side, "key": key, "effective": effective, "effective_for_side": effective_for_side, "account_overrides": account_overrides, "trb_overrides": trb_overrides, "loaded_overrides": loaded_overrides, "global_overrides": global_overrides, "account_entry": {k: v for k, v in account_entry.items() if k != "raw_returns"}, "global_entry": {k: v for k, v in global_entry.items() if k != "raw_returns"}, "history": history, "config_path": str(account_path), "account_config_path": str(account_path), "trb_baseline_config_path": str(trb_path), "global_config_path": str(global_path), "full_recipe_config_path": str(full_recipe_path), "final_book_config_path": str(final_book_path), "full_recipe_overrides": full_recipe_overrides, "final_book_entry": final_book_entry, "final_book_authorized": final_book_side, "source_label": f"TRB baseline → {account} punctual symbol/side overlay → shared per-symbol/defaults → final-book/full-recipe gates", "loaded_at": time.time()}

def _update_live_config(payload: dict[str, Any]) -> dict[str, Any]:
    account = str(payload.get("account", "trb")).lower(); symbol = str(payload.get("symbol", "")).upper(); side = str(payload.get("side", "LONG")).upper(); overrides = payload.get("overrides")
    if account not in {"trb", "trc"} or not re.fullmatch(r"[A-Z0-9.]{1,12}", symbol) or side not in {"LONG", "SHORT"} or not isinstance(overrides, dict):
        raise ValueError("account/symbol/side/overrides invalid")
    import dataclasses as _dc2
    from config_tradier import TradierConfig
    allowed = {field.name for field in _dc2.fields(TradierConfig())}
    allowed.update({"LONG_ENABLED","SHORT_ENABLED","NEWBORN_DC_STOP_ENABLED","NEWBORN_DC_STOP_MAX_AGE_MIN","NEWBORN_DC_STOP_FIELD","EMERGENCY_BRAKE_DC_STOP_ENABLED","EMERGENCY_BRAKE_DC_STOP_FIELD","HARD_MAX_SYMBOL_VALUE_TRADIER","BB4H_BREAKOUT_LADDER_ENABLED","BB4H_BREAKOUT_LADDER_TARGET_USD","BB4H_BREAKOUT_LADDER_BREAKOUT_PCT","BB4H_BREAKOUT_LADDER_BASIS_PCT","BB4H_BREAKOUT_LADDER_WT_CROSS_PCT","BB4H_BREAKOUT_LADDER_STOCK_MAX_NOTIONAL_USD","BB4H_BREAKOUT_LADDER_MAX_STOCK_SHARES","FAVORABLE_SLOPE_HOLD_ENABLED","STRUCTURE_FLIP_REENTRY_ENABLED","STRUCTURE_FLIP_REENTRY_TF","STRUCTURE_FLIP_REENTRY_BASIS_RESTRICTION_ENABLED","STRUCTURE_FLIP_REENTRY_BASIS_TF","BREAKEVEN_EXIT_AFTER_BARS_ENABLED","BREAKEVEN_EXIT_AFTER_BARS","BREAKEVEN_EXIT_AFTER_BARS_TF","BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT","BREAKEVEN_EXIT_REQUIRE_WT15M_STRUCTURE"})
    if any(str(k) not in allowed for k in overrides):
        raise ValueError("override contains an unknown Tradier setting")
    path = BASE_PATH / "data/hourly_reconfig" / account / "active_config.json"
    key = f"{symbol}_{side}"
    with _LIVE_CONFIG_LOCK:
        try: raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError): raw = {}
        entry = raw.get(key) if isinstance(raw.get(key), dict) else {}
        entry["overrides"] = {str(k): v for k, v in overrides.items()}; entry["live_switch_lab_updated_at"] = time.time(); entry["live_switch_lab_source"] = "switch_lab"
        raw[key] = entry
        tmp = path.with_suffix(".switch_lab.tmp"); tmp.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n"); os.replace(tmp, path)
        audit = BASE_PATH / "data/hourly_reconfig/switch_lab_live_updates.jsonl"
        try:
            with audit.open("a") as stream: stream.write(json.dumps({"ts": time.time(), "account": account, "key": key, "overrides": overrides}) + "\n")
        except OSError: pass
    return _live_config_payload(account, symbol, side)

@app.route("/api/switch_lab/live_config", methods=["GET"])
def switch_lab_live_config_get():
    account = request.args.get("account", "trb"); symbol = request.args.get("symbol", "GOOGL"); side = request.args.get("side", "LONG")
    payload = _live_config_payload(account, symbol, side)
    return jsonify(payload), 200 if "error" not in payload else 400

@app.route("/api/switch_lab/live_config", methods=["POST"])
def switch_lab_live_config_post():
    payload = request.get_json(silent=True) or {}
    try:
        result = _update_live_config(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"live config update failed: {exc}"}), 500
    return jsonify({"status": "APPLIED", "note": "tradier_manage reloads this file by mtime on its next decision", **result})

@app.route("/api/switch_lab/reservation", methods=["POST"])
def switch_lab_reservation():
    """Reserve 0..6 S1 worker slots for GUI jobs without reviving legacy tests."""
    payload = request.get_json(silent=True) or {}
    try:
        reserved = int(payload.get("manual_reserved_workers"))
    except (TypeError, ValueError):
        return jsonify({"error": "manual_reserved_workers must be an integer"}), 400
    if not 0 <= reserved <= 6:
        return jsonify({"error": "manual_reserved_workers must be 0 through 6"}), 400
    compact = base64.b64encode(json.dumps({"manual_reserved_workers": reserved}).encode()).decode()
    remote_python = (
        "import base64,json,os; from pathlib import Path; "
        "p=Path('/home/niels/binance-sandbox/data/reports/gui_lab/lab_config.json'); "
        "p.parent.mkdir(parents=True,exist_ok=True); old=json.loads(p.read_text()) if p.exists() else {}; "
        f"old.update(json.loads(base64.b64decode('{compact}'))); old['updated_at']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(); "
        "t=p.with_suffix('.tmp'); t.write_text(json.dumps(old,indent=2,sort_keys=True)+'\\n'); os.replace(t,p)"
    )
    try:
        subprocess.run([*_GUI_LAB_SSH, f"python3 -c \"{remote_python}\""], timeout=20, check=True,
                       capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return jsonify({"error": f"S1 reservation update failed: {type(exc).__name__}"}), 503
    return jsonify({"status": "QUEUED", "manual_reserved_workers": reserved,
                    "note": "The S1 supervisor applies the reservation within one minute."})


@app.route("/focus4")
def focus4_page():
    """LIVE test-progress board (USER 2026-07-20): focus-4 ladder cells, swing coverage,
    OFAT cell counts and the LEAN-baseline arms — auto-refreshing so a run can be followed
    without SSH. Data pulled from S1 by cron into data/_diagnostic + data/reports."""
    import json as _j
    base = BASE_PATH
    def _load(rel, default=None):
        try:
            return _j.loads((base / rel).read_text())
        except Exception:
            return default
    board = (_load("data/_diagnostic/focus4_scoreboard.json", {}) or {}).get("board", {})
    cap = _load("data/reports/stocks_bh_capture.json", {}) or {}
    cov_rows = (_load("data/_diagnostic/focus4_coverage/_combined.json", {}) or {}).get("rows", [])
    cov_by = {}
    for r in cov_rows:
        cov_by[(r.get("sym"), r.get("tag"))] = r
    html = ["<!DOCTYPE html><html><head><meta charset=utf-8><title>FOCUS-4 live</title>",
            "<meta http-equiv=refresh content=60>",
            "<style>body{background:#0c0e12;color:#e6e8eb;font-family:-apple-system,system-ui,sans-serif;margin:0;padding:16px}",
            "h1{font-size:16px}h2{font-size:13px;color:#8cf;margin-top:18px}",
            "table{border-collapse:collapse;font-size:12px;margin-bottom:10px}",
            "th,td{padding:4px 10px;border-bottom:1px solid #1c2029;text-align:right}",
            "th{color:#6a7383;font-size:10px;text-transform:uppercase}td.l,th.l{text-align:left}",
            "a{color:#7fb069}.g{color:#5a9060}.r{color:#a04040}.dim{color:#6a7383;font-size:11px}</style></head><body>",
            "<h1>FOCUS-4 live test board <span class=dim>(auto-refresh 60s · DIAGNOSTIC n_syms=1 per key)</span></h1>",
            "<div class=dim><b>HISTORICAL PRE-C4 DIAGNOSTIC / NOT CURRENT MATRIX RANKING.</b> "
            "Current stock truth is the c5 <a href='/results'>/results</a> matrix panel. "
            "Nothing on this page is a qualifier or promotion input.</div>",
            "<div class=dim>goal: gain_vs_bh &ge; 2.0 (target 10.0) &middot; up_capture = share of every &ge;10%% up-swing actually held</div>"]
    for sym in ("MU", "ARM", "ROKU", "NVDA"):
        cells = board.get(sym) or {}
        if not cells:
            continue
        bh = next(iter(cells.values())).get("bh_pct")
        html.append("<h2>%s_LONG &mdash; b&amp;h %s%%</h2><table><tr><th class=l>cell</th><th>gain%%</th>"
                    "<th>vs b&amp;h</th><th>trades</th><th>up_capture</th><th>missed</th><th>down_abs</th>"
                    "<th class=l>chart</th></tr>" % (sym, bh))
        for tag, s in sorted(cells.items(), key=lambda kv: -(kv[1].get("gain_vs_bh") or -9)):
            c = cov_by.get((sym, tag), {})
            vs = s.get("gain_vs_bh")
            cls = "g" if (vs or 0) >= 2 else ("r" if (vs or 0) < 0 else "")
            html.append("<tr><td class=l>%s</td><td>%s</td><td class='%s'><b>%s</b></td><td>%s</td>"
                        "<td>%s</td><td>%s/%s</td><td>%s</td>"
                        "<td class=l><a href='/?run=psc::%s&sym=%s' target=_blank>trades</a></td></tr>" % (
                            tag, s.get("gain_long_pct"), cls, vs, s.get("trades_long"),
                            s.get("up_capture_ratio", c.get("up_capture_ratio", "&mdash;")),
                            s.get("n_up_missed", c.get("n_up_missed", "?")), s.get("n_up", c.get("n_up", "?")),
                            s.get("down_absorbed", c.get("down_absorbed_total", "&mdash;")), tag, sym))
        html.append("</table>")
    rows = (cap.get("rows") or [])[:15]
    if rows:
        html.append("<h2>Historical B&amp;H capture scoreboard (pre-c4 diagnostic; qualifier floor 2x)</h2><table>"
                    "<tr><th class=l>key</th><th>b&amp;h/mo</th><th>gain/mo</th><th>capture</th><th>trades</th></tr>")
        for r in rows:
            c = r.get("capture_vs_bh")
            html.append("<tr><td class=l>%s</td><td>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td></tr>" % (
                r.get("key"), r.get("bh_per_mo"), r.get("gain_per_mo"),
                "g" if isinstance(c, (int, float)) and c >= 2 else "r", c, r.get("trades")))
        html.append("</table>")
    import glob as _g
    sw = {}
    for _f in _g.glob(str(base / "data/_diagnostic/switch_ladder/*_valued.json")):
        try:
            sw[Path(_f).name.replace("_valued.json", "")] = _j.loads(Path(_f).read_text())
        except Exception:
            pass
    if not sw:
        sw = _load("data/_diagnostic/switch_ladder/_all.json", {}) or {}
    if sw:
        html.append("<h2>HISTORICAL ADDITIVE-SWITCH TRACK &mdash; baseline = bare 5m WT-cross system "
                    "<span class=dim>(pre-c4 diagnostic; incremental improvement is not promotion)</span></h2>")
        for sym, blk in sw.items():
            b = blk.get("baseline", {})
            html.append("<h2 style='color:#7fb069'>%s &mdash; baseline %s&times; b&amp;h "
                        "<span class=dim>(gain %s%% / b&amp;h %s%% &middot; ps=%s &middot; dd=%s%% &middot; tr=%s)</span></h2>" % (
                            sym, b.get("gain_vs_bh"), b.get("gain_pct"), b.get("bh_pct"),
                            b.get("pool_sharpe"), b.get("max_dd_pct"), b.get("trades")))
            st = blk.get("stack") or []
            if st:
                html.append("<table><tr><th class=l>+switch</th><th>vs b&amp;h</th><th>gain%%</th>"
                            "<th>trades</th><th>ps</th><th>dd%%</th><th>tim%%</th></tr>")
                for r in st:
                    html.append("<tr><td class=l>%s</td><td class=g><b>%s</b></td><td>%s</td><td>%s</td>"
                                "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                                    (r.get("stack") or [""])[-1], r.get("gain_vs_bh"), r.get("gain_pct"),
                                    r.get("trades"), r.get("pool_sharpe"), r.get("max_dd_pct"),
                                    r.get("time_in_mkt_pct")))
                html.append("</table>")
            tops = [r for r in (blk.get("singles") or []) if (r.get("delta_vs_bh") or 0) > 0][:12] or (blk.get("singles") or [])[:8]
            if tops:
                html.append("<table><tr><th class=l>single switch</th><th>vs b&amp;h</th><th>&Delta;</th>"
                            "<th>trades</th><th>dd%%</th></tr>")
                for r in tops:
                    cls = "g" if (r.get("delta_vs_bh") or 0) > 0 else "r"
                    html.append("<tr><td class=l>%s</td><td>%s</td><td class='%s'>%+.3f</td><td>%s</td>"
                                "<td>%s</td></tr>" % (r.get("switch"), r.get("gain_vs_bh"), cls,
                                                      r.get("delta_vs_bh") or 0, r.get("trades"),
                                                      r.get("max_dd_pct")))
                html.append("</table>")
    live = _load("data/_diagnostic/ofat_live.json", {}) or {}
    if live:
        al = live.get("alarms") or []
        if al:
            html.append("<h2 style='color:#ff6b6b'>&#9888; EMERGENCY-BRAKE ALARMS</h2><ul>"
                        + "".join("<li style='color:#ff6b6b'>%s</li>" % a for a in al) + "</ul>")
        html.append("<h2>OFAT LIVE &mdash; %s</h2><p class=dim>cells=%s &middot; params_covered=%s &middot; "
                    "zero-delta=%s &middot; zero-trade=%s &middot; better=%s / worse=%s</p>" % (
                        live.get("campaign"), live.get("cells"), live.get("params_covered"),
                        live.get("zero_delta"), live.get("zero_trades"), live.get("better"), live.get("worse")))
        pk = live.get("per_key") or {}
        if pk:
            html.append("<table><tr><th class=l>key</th><th>cells</th><th>avg &Delta;gain/mo</th>"
                        "<th>min</th><th>max</th></tr>")
            for k, v in sorted(pk.items()):
                cls = "g" if (v.get("avg_delta") or 0) > 0 else ("r" if (v.get("avg_delta") or 0) < 0 else "")
                html.append("<tr><td class=l>%s</td><td>%s</td><td class='%s'>%s</td><td>%s</td><td>%s</td></tr>" % (
                    k, v.get("cells"), cls, v.get("avg_delta"), v.get("min_delta"), v.get("max_delta")))
            html.append("</table>")
        lat = live.get("latest") or []
        if lat:
            html.append("<h2>latest cells (newest first)</h2><table><tr><th class=l>key</th><th class=l>param</th>"
                        "<th class=l>value</th><th>gain/mo</th><th>&Delta;</th><th>trades</th><th class=l>when</th></tr>")
            for r in lat:
                d0 = r.get("delta")
                cls = "g" if (d0 or 0) > 0 else ("r" if (d0 or 0) < 0 else "dim")
                html.append("<tr><td class=l>%s_%s</td><td class=l>%s</td><td class=l>%s</td><td>%s</td>"
                            "<td class='%s'>%s</td><td>%s</td><td class=l>%s</td></tr>" % (
                                r.get("symbol"), r.get("side"), r.get("param"), r.get("value"),
                                r.get("gain_mo"), cls, d0, r.get("trades"), (r.get("ts") or "")[:19]))
            html.append("</table>")
    prog = ""
    try:
        prog = (base / "data/_diagnostic/ofat_progress.txt").read_text()[:4000]
    except Exception:
        prog = "(no ofat_progress.txt yet — pulled from S1 by cron)"
    html.append("<h2>OFAT / campaign progress</h2><pre class=dim style='white-space:pre-wrap'>%s</pre>" % prog)
    html.append("<div class=dim>sources: data/_diagnostic/focus4_scoreboard.json &middot; "
                "focus4_coverage/_combined.json &middot; data/reports/stocks_bh_capture.json</div>")
    html.append("</body></html>")
    return "".join(html)


def _matrix_progress_snapshot_path() -> Path:
    configured = os.environ.get("MATRIX_PROGRESS_SNAPSHOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # Select the newest valid request across both vector pipelines. An old
    # lifecycle selector must never hide a newer scalar campaign, and an old
    # scalar selector must not hide a newer lifecycle campaign. Prefer the
    # immutable request-bound pull snapshot; use the global producer snapshot
    # only when it carries the exact selected request identity.
    specs = (
        (
            "VECTOR_DISCOVERY",
            "data/sync/VECTOR_DISCOVERY_PULL_REQUEST.json",
            "vector-discovery-pull-request-v1",
            "WATCH_FIXED_VECTOR_DISCOVERY_FROM_S1",
            r"vd-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}",
            "data/sync/vector_discovery_progress",
        ),
        (
            "VECTOR_APPROX_SCALAR",
            "data/sync/VECTOR_APPROX_SCALAR_PULL_REQUEST.json",
            "vector-approx-scalar-pull-request-v1",
            "WATCH_FIXED_SIX_SLOT_VECTOR_APPROX_SCALAR_FROM_S1",
            r"vas-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}",
            "data/sync/vector_approx_scalar_progress",
        ),
    )
    candidates = []
    now = time.time()
    for (
        kind,
        selector_rel,
        schema,
        action,
        request_pattern,
        progress_rel,
    ) in specs:
        selector = Path(BASE_PATH) / selector_rel
        try:
            request = json.loads(selector.read_text(encoding="utf-8"))
            request_id = str(request["request_id"])
            requested_at = float(request["requested_at_epoch"])
            if (
                selector.is_symlink()
                or request.get("schema") != schema
                or request.get("action") != action
                or not re.fullmatch(request_pattern, request_id)
                or requested_at != requested_at
                or requested_at <= 0
                or requested_at > now + 300
            ):
                continue
            bound = Path(BASE_PATH) / progress_rel / f"{request_id}.json"
            bound_valid = False
            if bound.is_file() and not bound.is_symlink():
                snapshot = json.loads(bound.read_text(encoding="utf-8"))
                bound_valid = snapshot.get("producer_request_id") == request_id
            candidates.append(
                {
                    "kind": kind,
                    "request_id": request_id,
                    "requested_at_epoch": requested_at,
                    "bound": bound if bound_valid else None,
                }
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ):
            continue
    candidates.sort(
        key=lambda row: (row["requested_at_epoch"], row["request_id"]),
        reverse=True,
    )
    global_snapshot = matrix_live_progress.default_snapshot_path(BASE_PATH)
    global_request_id = None
    try:
        if global_snapshot.is_file() and not global_snapshot.is_symlink():
            global_request_id = json.loads(
                global_snapshot.read_text(encoding="utf-8")
            ).get("producer_request_id")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    for candidate in candidates:
        if candidate["bound"] is not None:
            return candidate["bound"]
        if global_request_id == candidate["request_id"]:
            return global_snapshot
    return global_snapshot


def _overlay_vector_terminal_summary(payload: dict) -> dict:
    """Attach request-bound terminal counters; never infer them from rates."""
    request_id = str(payload.get("producer_request_id") or "")
    lifecycle = bool(
        re.fullmatch(r"vd-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}", request_id)
    )
    scalar = bool(
        re.fullmatch(r"vas-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}", request_id)
    )
    if not lifecycle and not scalar:
        return payload
    if lifecycle:
        if payload.get("producer_state") != "VECTOR_DISCOVERY_COMPLETE":
            return payload
        path = (
            Path(BASE_PATH)
            / "data/sync/vector_discovery_terminal_summary"
            / f"{request_id}.json"
        )
        schema = "vector-discovery-terminal-summary-v1"
    else:
        path = (
            Path(BASE_PATH)
            / "data/sync/vector_approx_scalar_terminal_summary"
            / f"{request_id}.json"
        )
        schema = "vector-approx-scalar-terminal-summary-v1"
    try:
        if path.is_symlink():
            return payload
        summary = json.loads(path.read_text(encoding="utf-8"))
        if (
            summary.get("schema") != schema
            or summary.get("request_id") != request_id
            or not str(summary.get("s1_status") or "").startswith(
                ("COMPLETE_", "TERMINAL_")
            )
        ):
            return payload
        if lifecycle:
            fields = (
                "cohort_key_count",
                "ready_key_count",
                "candidate_recipe_count",
                "accepted_vector_recipe_count",
                "authoritative_engine_pass_cell_count",
                "authoritative_matrix_write_count",
            )
        else:
            fields = (
                "key_count",
                "terminal_key_count",
                "keys_remaining",
                "strict_passed_cell_count",
                "raw_result_row_count",
                "authoritative_engine_pass_cell_count",
                "authoritative_matrix_write_count",
            )
        counters = {field: int(summary[field]) for field in fields}
        if any(value < 0 for value in counters.values()):
            return payload
        if lifecycle and (
            counters["ready_key_count"] > counters["cohort_key_count"]
            or counters["accepted_vector_recipe_count"]
            > counters["candidate_recipe_count"]
        ):
            return payload
        if scalar and (
            counters["terminal_key_count"] > counters["key_count"]
            or counters["keys_remaining"]
            != counters["key_count"] - counters["terminal_key_count"]
            or counters["raw_result_row_count"]
            < counters["strict_passed_cell_count"]
        ):
            return payload
        counter_payload = {
            "authority": summary.get("authority"),
            "request_id": request_id,
            "source_receipt_sha256": summary.get(
                "source_receipt_sha256"
            ),
            **counters,
        }
        if lifecycle:
            payload["terminal_campaign_counters"] = counter_payload
        else:
            for optional in (
                "ranked_exact_candidate_count",
                "moved_cell_count",
                "inert_cell_count",
                "zero_trade_cell_count",
            ):
                if optional in summary:
                    value = int(summary[optional])
                    if value < 0:
                        return payload
                    counter_payload[optional] = value
            payload["terminal_scalar_counters"] = counter_payload
            ready = str(summary.get("s1_status")) == "COMPLETE_READY"
            payload["producer_state"] = (
                "VECTOR_APPROX_SCALAR_COMPLETE"
                if ready
                else "VECTOR_APPROX_SCALAR_TERMINAL_BLOCKED"
            )
            payload["producer_terminal"] = True
            payload["producer_health_status"] = (
                "TERMINAL_COMPLETE" if ready else "TERMINAL_BLOCKED"
            )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        if scalar:
            # A preflight failure has no fleet receipt, therefore no terminal
            # scalar summary. The request-bound pulled S1 status is still
            # authoritative for terminality and must not be displayed as a
            # running six-worker campaign.
            status_path = (
                Path(BASE_PATH)
                / "data/reports/vector_approx_scalar_s1_pulls"
                / request_id
                / "S1_VECTOR_APPROX_SCALAR_STATUS.json"
            )
            try:
                terminal = json.loads(status_path.read_text(encoding="utf-8"))
                terminal_status = str(terminal.get("status") or "")
                if (
                    status_path.is_symlink()
                    or terminal.get("request_id") != request_id
                    or not terminal_status.startswith(("COMPLETE_", "TERMINAL_"))
                    or any(
                        terminal.get(field) is not False
                        for field in (
                            "exact_v8_invoked",
                            "live_config_write_attempted",
                            "database_write_attempted",
                            "workbook_write_attempted",
                            "matrix_write_attempted",
                            "canonical_write_attempted",
                        )
                    )
                ):
                    return payload
                ready = terminal_status == "COMPLETE_READY"
                payload["producer_state"] = (
                    "VECTOR_APPROX_SCALAR_COMPLETE"
                    if ready
                    else "VECTOR_APPROX_SCALAR_TERMINAL_BLOCKED"
                )
                payload["producer_terminal"] = True
                payload["producer_health_status"] = (
                    "TERMINAL_COMPLETE" if ready else "TERMINAL_BLOCKED"
                )
                payload["producer_terminal_reason"] = terminal.get("reason")
                payload["accepted_cells_per_minute"] = 0.0
                payload["candidate_recipes_per_minute"] = 0.0
                payload["accepted_recipes_per_minute"] = 0.0
                for worker in payload.get("workers") or []:
                    worker["status"] = "TERMINAL"
                    worker["stage"] = "TERMINAL_BLOCKED"
                    worker["candidate_recipes_per_minute"] = 0.0
                    worker["accepted_recipes_per_minute"] = 0.0
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
    return payload


def _overlay_matrix_canonical_summary(payload: dict) -> dict:
    """Overlay a tiny precomputed count receipt; never query the matrix here."""
    path = Path(BASE_PATH) / "chart_static/matrix_canonical_summary.json"
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
        if (
            summary.get("schema") != "switch-matrix-canonical-summary-v1"
            or summary.get("status") != "PASS"
        ):
            return payload
        for field in (
            "total_cells",
            "exact_verified",
            "provisional_filled",
            "cells_remaining",
            "exact_cells_remaining",
            "quarantined_cells",
        ):
            value = int(summary[field])
            if value < 0:
                return payload
            payload[field] = value
        payload["canonical_summary_updated_at_epoch"] = float(
            summary["updated_at_epoch"]
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        pass
    return payload


def _normalize_scalar_request_rates(payload: dict) -> dict:
    """Remove rates carried from a prior campaign during scalar setup stages."""
    request_id = str(payload.get("producer_request_id") or "")
    if not re.fullmatch(
        r"vas-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}", request_id
    ):
        return payload
    setup_stages = {
        "WAITING_SYNC",
        "CAUSAL_NPZ_PRECOMPUTE",
        "CORE_LADDER_FLOOR_VALIDATION",
    }
    workers = payload.get("workers") or []
    for worker in workers:
        if str(worker.get("stage") or "") in setup_stages:
            worker["candidate_recipes_per_minute"] = 0.0
            worker["accepted_recipes_per_minute"] = 0.0
            worker["candidate_recipe_count"] = 0
            worker["accepted_recipe_count"] = 0
    payload["candidate_recipes_per_minute"] = sum(
        float(worker.get("candidate_recipes_per_minute") or 0.0)
        for worker in workers
    )
    payload["accepted_recipes_per_minute"] = sum(
        float(worker.get("accepted_recipes_per_minute") or 0.0)
        for worker in workers
    )
    return payload


def _overlay_lifecycle_branch_progress(payload: dict) -> dict:
    """Expose durable pulled branch batches while a lifecycle key is running.

    The S1 runner publishes final per-key counters only when a key terminates.
    Each beam log line is nevertheless a durable request-bound receipt for one
    evaluated entry-family batch. Count those recipes as vector candidates,
    never as accepted recipes, matrix cells, or ENGINE evidence.
    """
    request_id = str(payload.get("producer_request_id") or "")
    if (
        not re.fullmatch(r"vd-[0-9]{8}t[0-9]{6}z-[a-f0-9]{8}", request_id)
        or str(payload.get("producer_state") or "")
        != "VECTOR_DISCOVERY_RUNNING"
    ):
        return payload
    selector = Path(BASE_PATH) / "data/sync/VECTOR_DISCOVERY_PULL_REQUEST.json"
    try:
        request = json.loads(selector.read_text(encoding="utf-8"))
        if request.get("request_id") != request_id:
            return payload
        elapsed_minutes = max(
            (time.time() - float(request["requested_at_epoch"])) / 60.0,
            1.0 / 60.0,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return payload
    keys_root = (
        Path(BASE_PATH)
        / "data/reports/vector_discovery_pulls"
        / request_id
        / "path_productivity_hotlist/keys"
    )
    total_candidates = 0
    workers = payload.get("workers") or []
    for worker in workers:
        symbol = str(worker.get("symbol") or "").upper()
        side = str(worker.get("side") or "").upper()
        if not symbol or side not in {"LONG", "SHORT"}:
            continue
        log = keys_root / f"{symbol}_{side}" / "beam.log"
        candidates = 0
        strict_survivors = 0
        last_entry = None
        try:
            if log.is_symlink():
                continue
            for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]:
                try:
                    row = json.loads(line)
                    count = int(row.get("candidates", 0))
                    survivors = int(row.get("strict_survivors", 0))
                    if count < 0 or survivors < 0 or survivors > count:
                        continue
                    if count:
                        candidates += count
                        strict_survivors += survivors
                        last_entry = str(row.get("entry") or last_entry or "")
                except (ValueError, TypeError, json.JSONDecodeError):
                    continue
        except OSError:
            continue
        if not candidates:
            continue
        total_candidates += candidates
        worker["parameter"] = last_entry or worker.get("parameter")
        worker["combination"] = "ENTRY×BOUNDED_EXIT_BEAM×REENTER"
        worker["candidate_recipe_count"] = candidates
        worker["candidate_recipes_per_minute"] = round(
            candidates / elapsed_minutes, 3
        )
        worker["branch_strict_survivor_count"] = strict_survivors
        worker["candidate_throughput_source"] = (
            "REQUEST_BOUND_PULLED_BEAM_LOG_CUMULATIVE_SINCE_REQUEST"
        )
    if total_candidates:
        payload["candidate_recipes_per_minute"] = round(
            total_candidates / elapsed_minutes, 3
        )
        payload["candidate_recipe_count"] = total_candidates
        payload["candidate_throughput_source"] = (
            "REQUEST_BOUND_PULLED_BEAM_LOG_CUMULATIVE_SINCE_REQUEST"
        )
    return payload


@app.route("/api/matrix_live_progress")
@app.route("/api/matrix_progress")
def matrix_live_progress_api():
    """Serve one precomputed JSON snapshot; never query matrix databases here."""
    try:
        stale_after = int(os.environ.get("MATRIX_PROGRESS_STALE_SECONDS", "90"))
        payload = matrix_live_progress.dashboard_snapshot(
            _matrix_progress_snapshot_path(), stale_after_seconds=stale_after
        )
        payload = _normalize_scalar_request_rates(payload)
        payload = _overlay_lifecycle_branch_progress(payload)
        payload = _overlay_vector_terminal_summary(payload)
        payload = _overlay_matrix_canonical_summary(payload)
        return _no_cache(jsonify(payload))
    except Exception as exc:
        return _no_cache(
            jsonify(
                {
                    "schema": matrix_live_progress.SCHEMA,
                    "error": "matrix progress snapshot unavailable",
                    "detail": str(exc),
                    "workers": [],
                }
            )
        ), 500


@app.route("/matrix_live_progress")
@app.route("/matrix_progress")
def matrix_live_progress_page():
    """Six-slot SWITCH_MATRIX_TRB progress window, refreshed every three seconds."""
    page = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SWITCH_MATRIX_TRB live progress</title>
<style>
:root{color-scheme:dark}body{margin:0;background:#0b0e13;color:#e8edf4;font:14px system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1500px;margin:auto;padding:20px}h1{font-size:22px;margin:0 0 4px}.sub{color:#8d99a8;font-size:12px}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0}.card{background:#141923;border:1px solid #293240;border-radius:8px;padding:10px 14px;min-width:150px}
.value{font-size:24px;font-weight:700}.label{color:#8d99a8;font-size:11px;text-transform:uppercase}.badge{display:inline-block;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:700}
.fresh{background:#173e2b;color:#70e6a6}.stale{background:#56251e;color:#ff9a87}.waiting{background:#303745;color:#b9c1cc}.complete{background:#173653;color:#8dccff}
.tablewrap{overflow-x:auto;border:1px solid #293240;border-radius:8px}table{border-collapse:collapse;width:100%;min-width:1180px}th,td{padding:9px 10px;border-bottom:1px solid #222a35;text-align:left;vertical-align:top}
th{color:#88c7ff;background:#141923;font-size:11px;text-transform:uppercase}tr:last-child td{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.error{color:#ff9a87}
a{color:#88c7ff}
</style></head><body><main>
<h1>SWITCH_MATRIX_TRB live progress <span id="health" class="badge waiting">LOADING</span></h1>
<div class="sub">Six worker slots · worker-emitted throughput only · sync/dashboard freshness never counts as production · polling every 3 seconds · <a href="/results">current results</a></div>
<div class="summary">
 <div class="card"><div id="producer" class="value">—</div><div class="label">producer state</div></div>
 <div class="card"><div id="accepted" class="value">0</div><div class="label">last worker-emitted accepted cells / minute (ENGINE PASS)</div></div>
 <div class="card"><div id="candidates" class="value">0</div><div class="label">last worker-emitted vector candidates / minute</div></div>
 <div class="card"><div id="vectorAccepted" class="value">0</div><div class="label">last worker-emitted vector accepted / minute</div></div>
 <div class="card"><div id="candidateTotal" class="value">—</div><div class="label">terminal cumulative vector candidates</div></div>
 <div class="card"><div id="vectorAcceptedTotal" class="value">—</div><div class="label">terminal cumulative vector accepted</div></div>
 <div class="card"><div id="enginePassTotal" class="value">—</div><div class="label">terminal cumulative engine-pass cells</div></div>
 <div class="card"><div id="scalarStrictTotal" class="value">—</div><div class="label">terminal scalar strict-pass cells</div></div>
 <div class="card"><div id="scalarExactQueueTotal" class="value">—</div><div class="label">terminal scalar exact-queue candidates</div></div>
 <div class="card"><div id="scalarKeysTotal" class="value">—</div><div class="label">terminal scalar keys complete</div></div>
 <div class="card"><div id="rejected" class="value">0</div><div class="label">run-window rejected</div></div>
 <div class="card"><div id="quarantined" class="value">0</div><div class="label">run-window quarantined</div></div>
 <div class="card"><div id="filled" class="value">—</div><div class="label">provisional filled</div></div>
 <div class="card"><div id="exact" class="value">—</div><div class="label">exact verified</div></div>
 <div class="card"><div id="remaining" class="value">—</div><div class="label">authoritative matrix cells remaining</div></div>
 <div class="card"><div id="vectorRemaining" class="value">—</div><div class="label">vector keys remaining</div></div>
 <div class="card"><div id="updated" class="value" style="font-size:14px">—</div><div class="label">active worker timestamp</div></div>
</div>
<div id="freshnessDetail" class="sub" style="margin:-10px 0 18px">—</div>
<div class="card" style="margin-bottom:18px"><div class="label">uniqueness blocker · states never merged</div><div id="canonicalBlocker" class="value" style="font-size:17px">—</div><div id="prospectiveAudit" class="sub">prospective repaired-code audit: —</div><div id="syncState" class="sub">sync: —</div></div>
<div id="error" class="error"></div>
<div class="tablewrap"><table><thead><tr><th>slot</th><th>worker</th><th>run class</th><th>parameter / path family</th><th>value</th><th>symbol</th><th>side</th><th>combination</th><th>stage</th><th>status</th><th>ENGINE PASS cells/min</th><th>vector candidates/min</th><th>vector accepted/min</th><th>acceptance proof</th><th>rejected</th><th>quarantined</th><th>last update</th><th>matrix cells remaining</th><th>vector keys remaining</th><th>health</th></tr></thead><tbody id="workers"></tbody></table></div>
<script>
const dash=x=>(x===null||x===undefined||x==='')?'—':String(x);
function cell(row,value,cls=''){const td=document.createElement('td');td.textContent=dash(value);if(cls)td.className=cls;row.appendChild(td)}
function badge(stale,waiting,paused=false,complete=false){const span=document.createElement('span');span.className='badge '+(complete?'complete':paused||waiting?'waiting':stale?'stale':'fresh');span.textContent=complete?'COMPLETE · HEARTBEAT STOPPED':paused?'PAUSED':waiting?'WAITING':stale?'STALE':'LIVE';return span}
function count(value,total){return value==null?'—':`${value}${total==null?'':' / '+total}`}
function render(data){
 const paused=data.producer_state==='PAUSED';const terminalComplete=data.producer_health_status==='TERMINAL_COMPLETE';const terminalBlocked=data.producer_health_status==='TERMINAL_BLOCKED';const state=String(data.producer_state||'');const vectorState=state.startsWith('VECTOR_DISCOVERY')||state.startsWith('VECTOR_APPROX_SCALAR');const total=data.total_cells;const terminal=data.terminal_campaign_counters||{};const scalarTerminal=data.terminal_scalar_counters||{};document.getElementById('producer').textContent=dash(data.producer_state);document.getElementById('accepted').textContent=paused?'0':dash(data.accepted_cells_per_minute);document.getElementById('candidates').textContent=dash(data.candidate_recipes_per_minute);document.getElementById('vectorAccepted').textContent=dash(data.accepted_recipes_per_minute);document.getElementById('candidateTotal').textContent=dash(terminal.candidate_recipe_count);document.getElementById('vectorAcceptedTotal').textContent=dash(terminal.accepted_vector_recipe_count);document.getElementById('enginePassTotal').textContent=dash(terminal.authoritative_engine_pass_cell_count??scalarTerminal.authoritative_engine_pass_cell_count);document.getElementById('scalarStrictTotal').textContent=dash(scalarTerminal.strict_passed_cell_count);document.getElementById('scalarExactQueueTotal').textContent=dash(scalarTerminal.ranked_exact_candidate_count);document.getElementById('scalarKeysTotal').textContent=scalarTerminal.terminal_key_count==null?'—':`${scalarTerminal.terminal_key_count} / ${dash(scalarTerminal.key_count)}`;document.getElementById('rejected').textContent=dash(data.rejected_cells);document.getElementById('quarantined').textContent=dash(data.quarantined_cells);document.getElementById('filled').textContent=count(data.provisional_filled,total);document.getElementById('exact').textContent=count(data.exact_verified,total);document.getElementById('remaining').textContent=dash(data.cells_remaining);document.getElementById('vectorRemaining').textContent=dash(data.vector_keys_remaining);document.getElementById('updated').textContent=dash(vectorState?data.vector_updated_at:data.production_updated_at);document.getElementById('freshnessDetail').textContent=terminalComplete?`terminal receipt complete · cumulative counters are receipt-backed · worker heartbeat stopped as expected · heartbeat stale: ${dash(data.heartbeat_stale)} · age: ${dash(data.snapshot_age_seconds)}s`:terminalBlocked?`terminal receipt blocked · no running heartbeat · heartbeat stale: ${dash(data.heartbeat_stale)} · age: ${dash(data.snapshot_age_seconds)}s`:`producer health: ${dash(data.producer_health_status)} · heartbeat stale: ${dash(data.heartbeat_stale)} · age: ${dash(data.snapshot_age_seconds)}s`;
 const panel=data.blocker_panel||{};const canonical=panel.canonical_uniqueness||{};const prospective=panel.prospective_repaired_code_audit||{};const sync=panel.sync||{};document.getElementById('canonicalBlocker').textContent=`CANONICAL ${dash(canonical.status)} · ${dash(canonical.quarantined_numeric_overlays)} quarantined · ${dash(canonical.metric_collision_groups)} metric groups · ${dash(canonical.fingerprint_collision_groups)} action groups`;document.getElementById('prospectiveAudit').textContent=`prospective repaired-code audit: ${dash(prospective.status)} (non-canonical; never substituted)`;document.getElementById('syncState').textContent=`report receipt: ${dash(sync.status)} · continuous transport: ${dash(sync.transport_status)} / ${dash(sync.transport_phase)} · independent of production and uniqueness`;
 const health=document.getElementById('health');health.className='badge '+(terminalComplete?'complete':terminalBlocked?'stale':paused?'waiting':data.stale?'stale':'fresh');health.textContent=terminalComplete?'COMPLETE':terminalBlocked?'TERMINAL BLOCKED':paused?'PAUSED':data.stale?'STALE':'LIVE';document.getElementById('error').textContent=data.error||'';
 const body=document.getElementById('workers');body.replaceChildren();for(const w of (data.workers||[])){const row=document.createElement('tr');const wp=String(w.status||'').startsWith('PAUSED');const wc=terminalComplete&&['COMPLETE','COMPLETE_STRICT','RESUMED_COMPLETE'].includes(w.status);cell(row,w.slot);cell(row,w.worker_id,'mono');cell(row,w.run_class);cell(row,w.parameter,'mono');cell(row,w.value,'mono');cell(row,w.symbol);cell(row,w.side);cell(row,w.combination,'mono');cell(row,w.stage);cell(row,w.status);cell(row,wp?0:w.accepted_cells_per_minute);cell(row,w.candidate_recipes_per_minute);cell(row,w.accepted_recipes_per_minute);cell(row,w.accepted_throughput_status);cell(row,w.rejected_cells);cell(row,w.quarantined_cells);cell(row,w.last_update,'mono');cell(row,w.cells_remaining);cell(row,w.keys_remaining);const td=document.createElement('td');td.appendChild(badge(w.stale,w.last_update_epoch==null,wp,wc));row.appendChild(td);body.appendChild(row)}
}
async function refresh(){try{const response=await fetch('/api/matrix_live_progress',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.detail||data.error||`HTTP ${response.status}`);render(data)}catch(error){const h=document.getElementById('health');h.className='badge stale';h.textContent='STALE';document.getElementById('error').textContent=String(error)}}
refresh();setInterval(refresh,3000);
</script></main></body></html>"""
    return _no_cache(app.response_class(page, mimetype="text/html"))


@app.route("/heatmap")
@app.route("/heatmap.html")
def heatmap_page():
    return _no_cache(send_from_directory(app.static_folder, "heatmap.html"))


@app.route("/leaderboard")
@app.route("/leaderboard.html")
def leaderboard_page():
    return _no_cache(send_from_directory(app.static_folder, "leaderboard.html"))


@app.route("/trb_review")
@app.route("/trb_review/<mode>")
def trb_review(mode=None):
    """Audited per_sym-applied-to-live review (stocks trb/trc/tra + crypto). Shows each
    symbol's applied config, previous config, and backtest sharpe/gain. Filter via
    /trb_review/<trb|trc|tra|crypto>."""
    if mode is None and not request.args.get("mode"):
        return _no_cache(
            send_from_directory(app.static_folder, "trb_review.html")
        )
    import json as _json
    CFG = {"crypto": BASE_PATH / "data" / "hourly_reconfig" / "per_sym_active_config.json",
           "trb": BASE_PATH / "data" / "hourly_reconfig" / "trb" / "active_config.json",
           "trc": BASE_PATH / "data" / "hourly_reconfig" / "trc" / "active_config.json",
           "tra": BASE_PATH / "data" / "hourly_reconfig" / "tra" / "active_config.json"}
    want = (mode or request.args.get("mode") or "").lower()
    modes = [want] if want in CFG else list(CFG)
    rows = []
    for m in modes:
        try: d = _json.load(open(CFG[m]))
        except Exception: d = {}
        want_crypto = (m == "crypto")
        for key, v in d.items():
            if key.startswith("_") or not isinstance(v, dict):
                continue
            base = key.rsplit("_", 1)[0] if (key.endswith("_LONG") or key.endswith("_SHORT")) else key
            # HARD SEPARATION: crypto (config.py, USDT/USDC) and stocks (config_tradier.py)
            # are completely different systems — never mix, never compare. A stock key that
            # leaked into the crypto file (or vice-versa) is dropped from the wrong tab.
            if _is_crypto_sym(base) != want_crypto:
                continue
            meta = v.get("_meta", {}) if isinstance(v.get("_meta"), dict) else {}
            rows.append({"mode": m, "key": key, "wsharpe": v.get("wsharpe"),
                         "trades": v.get("trades"), "pnl": v.get("total_pnl_pct"),
                         "tag": v.get("winning_tag", ""), "applied": meta.get("promoted_ts_utc", meta.get("source_ts_utc", "")),
                         "overrides": v.get("overrides", {})})
    # flag SUSPECT per-side results: LONG and SHORT byte-identical = the old
    # tradier_hourly_reconfig both-sides-copy bug (not a real short backtest).
    _bysym = {}
    for r in rows:
        k = r.get("key", "")
        if k.endswith("_LONG") or k.endswith("_SHORT"):
            base, side = k.rsplit("_", 1)
            _bysym.setdefault((r.get("mode"), base), {})[side] = (r.get("wsharpe"), r.get("trades"), r.get("pnl"))
    for r in rows:
        k = r.get("key", "")
        if k.endswith("_LONG") or k.endswith("_SHORT"):
            base, side = k.rsplit("_", 1)
            pair = _bysym.get((r.get("mode"), base), {})
            identical = ("LONG" in pair and "SHORT" in pair and pair["LONG"] == pair["SHORT"])
            _ltr = (pair.get("LONG") or (None, 0, None))[1] or 0
            r["suspect"] = bool(identical and _ltr > 0)
    def _wf(r):
        try: return float(r.get("wsharpe") or -9)
        except Exception: return -9
    rows.sort(key=_wf, reverse=True)
    def _f(v):
        try: return "%.3f" % float(v)
        except Exception: return "—"
    n_pos = sum(1 for r in rows if _wf(r) > 0)
    n_05 = sum(1 for r in rows if _wf(r) > 0.5)
    n_suspect = sum(1 for r in rows if r.get("suspect"))
    _warn = ""
    if want == "crypto" and not rows:
        _warn = ("<div style='background:#502;color:#fbb;padding:10px;border-radius:5px;margin:8px 0'>"
                 "⚠️ No crypto (USDT/USDC) per_sym keys found. The crypto live per_sym file "
                 "<code>data/hourly_reconfig/per_sym_active_config.json</code> is currently populated "
                 "with STOCK keys (config_tradier system) — crypto per_sym overrides are NOT being "
                 "applied to live crypto. This file is read by ez_manage + ez_positions_quick for "
                 "crypto; it must contain only USDT/USDC keys. Needs repair (trace the producer "
                 "mis-writing stock keys here).</div>")
    elif want in CFG and not rows and not CFG[want].exists():
        _warn = ("<div style='background:#432;color:#fd8;padding:10px;border-radius:5px;margin:8px 0'>"
                 "⚠️ No active_config.json found yet for <b>%s</b> at <code>%s</code> — this account has "
                 "no per_sym overrides applied to live yet (not an error; just nothing to show).</div>"
                 ) % (want, CFG[want])
    tabs = "".join('<a href="/trb_review/%s" style="margin:0 8px;padding:4px 10px;background:#223;color:#8cf;border-radius:4px;text-decoration:none">%s</a>' % (m, m.upper()) for m in ["trb", "trc", "tra", "crypto"])
    trs = []
    for r in rows:
        w = _wf(r)
        color = "#3c3" if w > 0.5 else ("#cc3" if w > 0 else "#c66")
        susp = "<span style='color:#f80;font-weight:bold' title='LONG==SHORT identical = old both-sides-copy bug, not a real per-side result'>⚠️ SUSPECT</span>" if r.get("suspect") else ""
        key = r.get("key", "")
        base_sym = key.rsplit("_", 1)[0] if (key.endswith("_LONG") or key.endswith("_SHORT")) else key
        chart_class = "crypto" if _is_crypto_sym(base_sym) else "stocks"
        chart_link = "<a href='/?sym=%s&class=%s' target='_blank' style='color:#8cf'>chart</a>" % (base_sym, chart_class)
        trs.append(
            "<tr%s><td>%s</td><td><b>%s</b> %s</td><td style='color:%s'><b>%s</b></td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td style='font:11px monospace;max-width:520px;overflow:hidden'>%s</td><td>%s</td></tr>" % (
                " style='opacity:0.5'" if r.get("suspect") else "", r.get("mode", ""), r.get("key", ""), susp, color,
                _f(r.get("wsharpe")), r.get("trades", "—"),
                _f(r.get("pnl")), r.get("tag", "")[:28], str(r.get("applied", ""))[:19],
                _json.dumps(r.get("overrides", {}))[:520], chart_link))
    html = ("<html><head><title>per_sym applied review</title><meta http-equiv=refresh content=120></head>"
            "<body style='background:#111;color:#ddd;font-family:sans-serif;padding:16px'>"
            "<h2>per_sym → live APPLIED configs <span style='font-size:13px;color:#888'>(auto-refresh 120s · %d keys · %d positive · %d >0.5 · <span style='color:#f80'>%d SUSPECT (fake per-side)</span>)</span></h2>"
            "<div style='margin:10px 0'>Filter: <a href='/trb_review'>ALL</a> %s · <a href='/symbols_overview' style='color:#7fb069'>📁 full symbols overview + XLS</a></div>"
            "%s"
            "<table cellpadding=6 style='border-collapse:collapse;width:100%%'>"
            "<tr style='background:#223;color:#8cf'><th>mode</th><th>symbol_key</th><th>wsharpe</th>"
            "<th>trades</th><th>pnl%%</th><th>winning_tag</th><th>applied</th><th>config (overrides)</th><th>trades/chart</th></tr>%s</table>"
            "<p style='color:#888'>green=wsharpe&gt;0.5 · yellow=positive · red=&le;0 · these are LIVE on the Mac now · XLS: SPREADSHEETS/PERSYM_APPLIED_REVIEW.xlsx · full backtest results: <a href='/symbols_overview' style='color:#8cf'>/symbols_overview</a></p>"
            "</body></html>") % (len(rows), n_pos, n_05, n_suspect, tabs, _warn, "".join(trs))
    return _no_cache(app.response_class(html, mimetype="text/html"))


SPREADSHEETS_DIR = BASE_PATH / "SPREADSHEETS"


@app.route("/spreadsheets/<path:fname>")
def spreadsheets_download(fname):
    """Serve a file out of SPREADSHEETS/ for download (XLS/CSV outputs referenced
    by /symbols_overview and /trb_review). send_from_directory rejects path
    traversal on its own."""
    return send_from_directory(str(SPREADSHEETS_DIR), fname, as_attachment=True)


def _results_fmt_sharpe(v):
    return "%.4f" % v if isinstance(v, (int, float)) else "&mdash;"


def _results_fmt_pct(v):
    return "%.2f%%" % v if isinstance(v, (int, float)) else "&mdash;"


def _results_section_html(rep):
    mode_label = "CRYPTO (USDT/USDC)" if rep["mode"] == "crypto" else "STOCKS"
    cost_pct = float(
        rep.get(
            "round_trip_cost_pct",
            results_dashboard_lib.ROUND_TRIP_COST_PCT[rep["mode"]],
        )
    )
    min_bh_multiple = float(
        rep.get("minimum_bh_multiple", results_dashboard_lib.MIN_BH_MULTIPLE)
    )
    cov_pct = (100.0 * rep["real_engine_confirmed_keys"] / rep["vec_screened_keys"]) if rep["vec_screened_keys"] else 0.0
    cards = "".join(
        "<div class='card'><div class='card_n'>%s</div><div class='card_l'>%s</div></div>" % (n, l)
        for n, l in [
            (rep["total_keys"], "total keys (live-applied config)"),
            (rep["enabled_keys"], "ENABLED (trading)"),
            (rep["gated_off_keys"], "GATED OFF (real-engine negative)"),
            (rep["winners_count"], "qualifiers (&gt;0.5 Sharpe + &ge;2x B&amp;H)"),
            (rep["mid_count"], "0&ndash;0.5 (real-engine, sub-threshold)"),
            (rep["rescued_count"], "rescued + &ge;2x B&amp;H this run"),
        ])
    def _wrow(w):
        return ("<tr><td>%s</td><td class='g'><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td></tr>" %
                (w["key"], _results_fmt_sharpe(w.get("real_sharpe")),
                 ("%.3fx" % w["bh_multiple"]) if isinstance(w.get("bh_multiple"), (int, float)) else "&mdash;",
                 w.get("trades", "&mdash;"), (w.get("date") or "")[:19]))
    def _grow(g):
        return ("<tr><td>%s</td><td class='r'>%s</td><td>%s</td><td>%s</td></tr>" %
                (g["key"], _results_fmt_sharpe(g.get("real_sharpe")), g.get("trades", "&mdash;"), (g.get("date") or "")[:19]))
    def _rrow(r):
        changed = ", ".join("%s=%s" % (k, v) for k, v in (r.get("changed") or {}).items())
        return ("<tr><td>%s</td><td>%s &rarr; <b class='g'>%s</b></td><td>%s</td><td style='font:11px monospace'>%s</td></tr>" %
                (r["key"], _results_fmt_sharpe(r.get("before_sharpe")), _results_fmt_sharpe(r.get("real_sharpe")),
                 (r.get("date") or "")[:19], changed[:200]))
    winners_rows = "".join(_wrow(w) for w in rep["winners"]) or "<tr><td colspan=5 style='color:#888'>none yet</td></tr>"
    gated_rows = "".join(_grow(g) for g in rep["gated"][:60]) or "<tr><td colspan=4 style='color:#888'>none</td></tr>"
    rescued_rows = "".join(_rrow(r) for r in rep["rescued"]) or "<tr><td colspan=4 style='color:#888'>none yet this run</td></tr>"
    return """
    <section class='modeblock'>
      <h2>%s</h2>
      <div class='cards'>%s</div>
      <p class='cov'>reporting contract: <b>%.2f%% round trip</b> for %s;
      a qualifier requires an actual B&amp;H multiple &ge;%.1fx. A percentage-point
      difference is never relabeled as a multiple.</p>
      <p class='cov'>coverage: <b>%d/%d</b> vec-screened keys real-engine-confirmed (%.0f%%) &middot; %d still vec-screen-only [DIAGNOSTIC]</p>
      <h3>Qualifiers &mdash; real-engine <code>real_sharpe_1sym</code> &gt; 0.5 and actual B&amp;H multiple &ge;%.1fx (sorted best-first)</h3>
      <table class='t'><tr><th>key</th><th>real_sharpe_1sym</th><th>B&amp;H multiple</th><th>trades</th><th>confirmed</th></tr>%s</table>
      <h3>Gated off &mdash; real-engine negative, NOT trading live</h3>
      <table class='t'><tr><th>key</th><th>real_sharpe_1sym</th><th>trades</th><th>gated</th></tr>%s</table>
      <h3>Rescued this run &mdash; reopt search lifted vec false-negatives above Sharpe and 2x B&amp;H floors</h3>
      <table class='t'><tr><th>key</th><th>before &rarr; after real_sharpe_1sym</th><th>when</th><th>what changed</th></tr>%s</table>
    </section>""" % (
        mode_label,
        cards,
        cost_pct,
        rep["mode"],
        min_bh_multiple,
        rep["real_engine_confirmed_keys"],
        rep["vec_screened_keys"],
        cov_pct,
        rep["vec_only_keys"],
        min_bh_multiple,
        winners_rows,
        gated_rows,
        rescued_rows,
    )


def _current_stock_matrix_html():
    """Current stock truth for /results.

    The former stock panel read the July-8 reopt/vec-baseline files and called a
    positive single-symbol Sharpe a winner even when it was below B&H.  Those
    files are a separate legacy diagnostic lane, not the repaired matrix.
    """
    path = BASE_PATH / "data" / "reports" / "SWITCH_MATRIX_TRB_DIGEST.md"
    provenance_path = path.with_suffix(path.suffix + ".provenance.json")
    matrix_path = (
        BASE_PATH / "data" / "reports" / "SWITCH_MATRIX_TRB.csv.gz"
    )
    current_contract = "CURRENT_CONTRACT_UNAVAILABLE"
    try:
        body = path.read_text(errors="replace")
        provenance = json.loads(provenance_path.read_text())
        with gzip.open(matrix_path, "rt") as handle:
            matrix_metadata = {}
            for line in handle:
                if not line.startswith("#"):
                    break
                if "=" in line:
                    name, value = line[1:].strip().split("=", 1)
                    matrix_metadata[name.strip()] = value.strip()
        age_s = max(0.0, time.time() - path.stat().st_mtime)
        age = "%.1fh" % (age_s / 3600.0)
        source = str(path.relative_to(BASE_PATH))
        current_campaign = next(
            iter(results_dashboard_lib.CURRENT_STOCK_CAMPAIGNS)
        )
        required_markers = (
            "campaign `%s`" % current_campaign,
            "Current repaired-contract ENGINE rows",
            "Historical/pre-fix ENGINE rows quarantined",
        )
        errors = []
        if not all(marker in body for marker in required_markers):
            errors.append("required current/quarantine markers missing")
        if provenance.get("schema") != "switch-matrix-trb-current-digest-v1":
            errors.append("wrong provenance schema")
        if provenance.get("campaign") != current_campaign:
            errors.append("wrong campaign")
        current_contract = str(provenance.get("contract_version") or "")
        if (
            not current_contract.startswith("tradier-matrix-exec-c")
            or matrix_metadata.get("CURRENT_CONTRACT_VERSION")
            != current_contract
        ):
            errors.append("digest/matrix exact contract mismatch")
        if (
            matrix_metadata.get("CURRENT_CAMPAIGN")
            != current_campaign
            or matrix_metadata.get("CURRENT_MATRIX_SCOPE")
            != "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY"
            or matrix_metadata.get("CANONICAL_MATRIX")
            != "data/reports/SWITCH_MATRIX_TRB.csv.gz"
        ):
            errors.append("canonical matrix metadata mismatch")
        if (
            provenance.get("matrix_scope")
            != "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY"
        ):
            errors.append("wrong matrix scope")
        if (
            provenance.get("canonical_matrix")
            != "data/reports/SWITCH_MATRIX_TRB.csv.gz"
        ):
            errors.append("wrong canonical matrix path")
        if (
            hashlib.sha256(path.read_bytes()).hexdigest()
            != provenance.get("digest_sha256")
        ):
            errors.append("digest hash mismatch")
        if (
            not matrix_path.is_file()
            or hashlib.sha256(matrix_path.read_bytes()).hexdigest()
            != provenance.get("canonical_matrix_sha256")
        ):
            errors.append("canonical matrix missing or hash mismatch")
        policy = provenance.get("tim_policy")
        if not isinstance(policy, dict) or len(policy) != int(
            provenance.get("active_key_count") or -1
        ):
            errors.append("TIM policy missing/incomplete")
        else:
            ranks_by_side = {"LONG": [], "SHORT": []}
            for key, row in policy.items():
                try:
                    side = str(key).rsplit("_", 1)[1]
                    rank = int(row["rank"])
                    observed = (
                        float(row["tim_min_pct"]),
                        float(row["tim_max_pct"]),
                    )
                    expected = (
                        (50.0, 80.0)
                        if rank <= 10
                        else (20.0, 60.0)
                    )
                    if side not in ranks_by_side or observed != expected:
                        raise ValueError
                    ranks_by_side[side].append(rank)
                except (KeyError, TypeError, ValueError):
                    errors.append("invalid ranked TIM policy")
                    break
            if not errors:
                for side, ranks in ranks_by_side.items():
                    if sorted(ranks) != list(range(1, len(ranks) + 1)):
                        errors.append(f"non-contiguous {side} TIM ranks")
                    elif len(ranks) < 10:
                        errors.append(f"fewer than 10 {side} ranked keys")
        if errors:
            body = "MATRIX DIGEST INTEGRITY WARNING: " + "; ".join(errors)
        else:
            body = "Matrix metadata hash/contract verified."
    except Exception as exc:
        body = "CURRENT C5 DIGEST REJECTED: %s" % exc
        age = "unknown"
        source = str(path)
    return """
    <section class='modeblock'>
      <h2>STOCKS &mdash; CURRENT REPAIRED MATRIX</h2>
      <p class='cov'>campaign <code>stocks_repaired_20260730_c5</code> &middot;
      exact contract <code>%s</code> &middot;
      source <code>%s</code> &middot; age %s</p>
      <p class='cov'>ordinary stock cost contract: <b>0.05%% round trip</b>
      (zero equity commission assumption plus aggregate spread/slippage);
      current research: <b>0 commission + 2.5 bps adverse slippage each way</b>.
      Historical 5+2 bps research receipts remain quarantined under their
      declared 14 bps effective round trip and are not silently relabeled.</p>
      <p><b>Exact TRB promotion rule:</b> receipt-valid gain/month must be
      <b>&gt;2%%</b>, meet the hard monthly real-close activity floor, and have
      either positive same-side B&amp;H delta or exact normalized strategy
      drawdown &le;50%% of exact normalized B&amp;H drawdown. Capacity,
      no-lookahead, side-isolation and exact-close validity still apply.</p>
      <p class='cov'>%s</p>
      %s
    </section>""" % (
        html_escape(current_contract),
        html_escape(source),
        html_escape(age),
        html_escape(body),
        _fresh_current_matrix_reporting().html_section(BASE_PATH),
    )


def _historical_stock_quarantine_html():
    return """
    <section class='modeblock'>
      <h2>STOCKS &mdash; HISTORICAL QUARANTINE (NOT CURRENT C5)</h2>
      <p class='cov'>The frozen <code>STOCKS_BASELINE_V2_S4H</code> export,
      pre-c4 engine rows, July capture scoreboards, and historical 5+2 bps
      receipts are preserved only as rollback/diagnostic evidence. They are
      excluded from current rankings, qualifiers, candidates, matrix coverage,
      and live promotion. Historical costs remain labeled at their recorded
      14 bps effective round trip; they are not relabeled as current 0.05%.</p>
    </section>"""


@app.route("/results")
def results_dashboard():
    """Current repaired stock matrix plus the separate crypto reopt monitor."""
    try:
        rep_crypto = results_dashboard_lib.build_report("crypto")
        live = results_dashboard_lib.get_s1_liveness()
    except Exception as e:
        return _no_cache(app.response_class("<pre>results_dashboard error: %s</pre>" % e, mimetype="text/html", status=500))
    prog_c = (live.get("progress") or {}).get("crypto", {})
    liveness_html = (
        "<p class='cov'>S1: %s &middot; %s &middot; legacy crypto reopt queue %s/%s "
        "done (rescued=%s enabled=%s hopeless=%s)</p>" % (
            html_escape(live.get("cpu_line") or "unreachable"), html_escape(live.get("mem_line") or ""),
            prog_c.get("done", "?"), prog_c.get("queue_total", "?"), prog_c.get("rescued", 0),
            prog_c.get("enabled", 0), prog_c.get("hopeless", 0)))
    xls_links = "".join(
        "<a href='/spreadsheets/%s' target='_blank'>%s</a>" % (f, f)
        for f in ["SYMBOL_OVERVIEW_crypto.xlsx", "SYMBOL_OVERVIEW_stocks.xlsx", "PERSYM_APPLIED_REVIEW.xlsx",
                   "FULL_PARAM_MATRIX_crypto.xlsx", "FULL_PARAM_MATRIX_stocks.xlsx"])
    html = """<html><head><title>Results — live optimization state</title>
    <meta http-equiv=refresh content=120>
    <style>
    body{background:#0d0d0f;color:#ddd;font-family:-apple-system,Segoe UI,Arial,sans-serif;padding:18px;max-width:1300px;margin:0 auto}
    h1{color:#fff;margin-bottom:2px} h2{color:#8cf;margin-top:6px;border-bottom:1px solid #334;padding-bottom:4px}
    h3{color:#bbb;margin:14px 0 6px 0;font-size:14px}
    a{color:#8cf} .nav{margin:8px 0 16px 0} .nav a{margin-right:14px}
    .cards{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
    .card{background:#181820;border:1px solid #2a2a35;border-radius:8px;padding:10px 16px;min-width:150px}
    .card_n{font-size:26px;font-weight:700;color:#fff} .card_l{font-size:11px;color:#999;margin-top:2px}
    table.t{border-collapse:collapse;width:100%%;margin-bottom:6px}
    table.t th{background:#1a1a24;color:#8cf;text-align:left;padding:5px 8px;font-size:11px}
    table.t td{padding:4px 8px;border-bottom:1px solid #222;font-size:12px}
    .g{color:#3c3} .r{color:#c66} .cov{color:#999;font-size:12px}
    .modeblock{margin-bottom:28px;padding-bottom:10px;border-bottom:2px solid #223}
    .footer{color:#888;font-size:11.5px;margin-top:22px;padding:10px;background:#181820;border-radius:6px;line-height:1.5}
    .ts{color:#666;font-size:11px}
    .current-matrix-results{overflow-x:auto}.current-matrix-results table{border-collapse:collapse;width:100%%;min-width:1750px;margin:8px 0}
    .current-matrix-results th{background:#1a1a24;color:#8cf;text-align:left;padding:5px 8px;font-size:11px;white-space:nowrap}
    .current-matrix-results td{padding:5px 8px;border-bottom:1px solid #222;font-size:11px;vertical-align:top}
    .current-matrix-results details{min-width:350px;max-width:650px}.current-matrix-results summary{cursor:pointer;color:#dcc26b}
    .current-matrix-results .recipe{max-height:240px;max-width:620px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#0d0d12;padding:7px;color:#ccd4df}
    .current-matrix-results .recipe-note{font-size:10px;color:#999}
    </style></head>
    <body>
    <h1>Optimization machine &mdash; current results</h1>
    <div class='nav'>
      <a href='/trb_review/crypto'>trb_review/crypto</a>
      <a href='/trb_review/trb'>trb_review/trb</a>
      <a href='/trb_review/trc'>trb_review/trc</a>
      <a href='/symbols_overview'>symbols_overview</a>
      <a href='/matrix_live_progress'>matrix live progress</a>
      %s
    </div>
    %s
    %s
    %s
    %s
    <div class='footer'>
      <b>Crypto legacy-monitor caveat:</b> every real_sharpe_1sym number in the
      crypto section is a SINGLE-SYMBOL real-engine backtest
      (n_syms=1) &mdash; below the 48-crypto / 100-stock sample floor, so per CLAUDE.md rule 5 it is
      [DIAGNOSTIC] and must not be used alone to promote/deploy/recommend; it exists here to confirm
      or refute the vec-screen false-negative/false-positive on that one symbol. Sharpe values are
      capped at &plusmn;5.0 (metrics_guard.PER_SYM_SHARPE_CAP). &quot;ENABLED&quot; = currently applied
      to live via active_config overrides; keys with no explicit *_ENABLED override default to enabled
      (not confirmed gated off). Vec-screen-only keys (not yet real-engine-confirmed) are excluded from
      winners/gated/rescued tables entirely &mdash; they are a screen, not a truth.
      <div class='ts'>vec_baselines: %s &middot; gating_corrections: %s &middot; rescues: %s &middot; page generated %s UTC</div>
    </div>
    </body></html>""" % (xls_links, liveness_html, _current_stock_matrix_html(),
                          _historical_stock_quarantine_html(), _results_section_html(rep_crypto),
                          rep_crypto.get("vec_mtime"), rep_crypto.get("gating_mtime"), rep_crypto.get("rescues_mtime"),
                          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    return _no_cache(app.response_class(html, mimetype="text/html"))


@app.route("/current_matrix_best")
def current_matrix_best():
    """Canonical stock results for 5077 clients.

    This is deliberately separate from the legacy run catalogue: it exposes
    only provenance-backed matrix best cells and their producing override
    recipes.  It has no vector/baseline proxy fallback.
    """
    try:
        return _no_cache(jsonify(
            _fresh_current_matrix_reporting().dashboard_payload(BASE_PATH)
        ))
    except Exception as exc:
        return _no_cache(jsonify({
            "schema": "current-matrix-best-v2",
            "error": "canonical matrix reporting unavailable",
            "detail": str(exc),
            "rows": [],
        })), 500


@app.route("/symbols_overview")
def symbols_overview_page():
    """Full per-symbol picture — current settings + best-known backtest result —
    for BOTH crypto and stocks, everywhere EXCEPT the applied-config review
    (/trb_review covers that). Crypto and stocks are NEVER mixed in one table
    (classified by USDT/USDC suffix per CLAUDE.md USDC-over-USDT policy).
    Pulls: per_sym active configs (current settings) + data/test_results_central.db
    (best backtest result per symbol, canonical columns only). Links out to the
    full re-runnable XLS built by tools/build_symbol_overview_xls.py."""
    # Reuse the exact same DB-read + rule-6-compliant selection logic as
    # tools/build_symbol_overview_xls.py (single source of truth — no drift
    # between the XLS and this live page). That module caps sym_sharpe at
    # +/-5.0, prefers >=30-trade samples over cherry-picked few-trade noise,
    # and NEVER labels the result bare "pool_sharpe" per CLAUDE.md rule 8.
    sys.path.insert(0, str(BASE_PATH / "tools"))
    import build_symbol_overview_xls as _sov
    by_key = _sov.load_all_from_db()
    best_by_sym: Dict[str, Dict[str, Any]] = {}
    db_note = "" if _sov.DB_PATH.exists() else f"central DB not found at {_sov.DB_PATH} (pull from S1: data/test_results_central.db)"
    for (sym, side), candidates in by_key.items():
        if not candidates:
            continue
        best, capped, status = _sov.pick_best(candidates)
        cur_best = best_by_sym.get(sym)
        if cur_best is None or capped > cur_best["sym_sharpe_capped"]:
            best_by_sym[sym] = {"side": side, "sym_sharpe_capped": capped, "status": status,
                                "acc_gain_pct": best.get("acc_gain_pct"), "avg_gain_trade": best.get("avg_gain_trade"),
                                "max_dd_pct": best.get("max_dd_pct"), "gain_vs_bh": best.get("gain_vs_bh"),
                                "years": best.get("years"), "trades": best.get("trades"), "run_id": best.get("run_id")}

    def _load_cfg(path: Path) -> Dict[str, Any]:
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}

    cfg_sources = {
        "crypto": BASE_PATH / "data" / "hourly_reconfig" / "per_sym_active_config.json",
        "trb": BASE_PATH / "data" / "hourly_reconfig" / "trb" / "active_config.json",
        "trc": BASE_PATH / "data" / "hourly_reconfig" / "trc" / "active_config.json",
        "tra": BASE_PATH / "data" / "hourly_reconfig" / "tra" / "active_config.json",
    }
    applied: Dict[str, Dict[str, Any]] = {}
    for acct, path in cfg_sources.items():
        for key, v in _load_cfg(path).items():
            if key.startswith("_") or not isinstance(v, dict):
                continue
            applied[key] = {"acct": acct, "wsharpe": v.get("wsharpe"), "trades": v.get("trades"),
                            "overrides": v.get("overrides", {})}

    all_syms = sorted(set(best_by_sym) | {k.rsplit("_", 1)[0] if (k.endswith("_LONG") or k.endswith("_SHORT")) else k for k in applied})
    crypto_syms = [s for s in all_syms if _is_crypto_sym(s)]
    stock_syms = [s for s in all_syms if not _is_crypto_sym(s)]

    def _row(sym: str) -> str:
        best = best_by_sym.get(sym, {})
        applied_keys = [k for k in applied if k.rsplit("_", 1)[0] == sym or k == sym]
        aw = ", ".join(f"{k}={applied[k].get('wsharpe')}" for k in applied_keys) if applied_keys else "—"
        chart_class = "crypto" if _is_crypto_sym(sym) else "stocks"
        ps = best.get("sym_sharpe_capped")
        is_diag = isinstance(best.get("status"), str) and best["status"].startswith("[DIAGNOSTIC")
        color = "#888" if is_diag else ("#3c3" if (ps or 0) > 0.5 else ("#cc3" if (ps or 0) > 0 else "#c66"))
        return ("<tr><td><a href='/?sym=%s&class=%s' target='_blank' style='color:#8cf'>%s</a></td>"
                "<td style='color:%s'>%s</td><td style='font-size:11px;color:#999'>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td style='font:11px monospace'>%s</td></tr>") % (
            sym, chart_class, sym, color,
            ("sym_sharpe=%.4f" % ps) if ps is not None else "—",
            best.get("status", "no_backtest_data"),
            "%.1f" % best["max_dd_pct"] if best.get("max_dd_pct") is not None else "—",
            best.get("trades", "—"),
            "%.1f" % best["gain_vs_bh"] if best.get("gain_vs_bh") is not None else "—",
            best.get("run_id", "—")[:24] if best.get("run_id") else "—", aw)

    def _section(title: str, syms: List[str]) -> str:
        rows_html = "".join(_row(s) for s in syms)
        return ("<h3>%s <span style='color:#888;font-size:12px'>(%d symbols)</span></h3>"
                "<table cellpadding=6 style='border-collapse:collapse;width:100%%'>"
                "<tr style='background:#223;color:#8cf'><th>symbol</th><th>best sym_sharpe (capped &plusmn;5)</th>"
                "<th>sample status</th><th>max_dd%%</th><th>trades</th><th>gain_vs_bh%%</th><th>best run_id</th>"
                "<th>applied per_sym (wsharpe)</th></tr>%s</table>") % (title, len(syms), rows_html)

    xls_links = ("<p>XLS downloads (re-run <code>tools/build_symbol_overview_xls.py</code> any time): "
                 "<a href='/spreadsheets/SYMBOL_OVERVIEW_crypto.xlsx' style='color:#8cf'>SYMBOL_OVERVIEW_crypto.xlsx</a> · "
                 "<a href='/spreadsheets/SYMBOL_OVERVIEW_stocks.xlsx' style='color:#8cf'>SYMBOL_OVERVIEW_stocks.xlsx</a> · "
                 "<a href='/spreadsheets/PRIORITY_config_crypto.xlsx' style='color:#8cf'>PRIORITY_config_crypto.xlsx</a> · "
                 "<a href='/spreadsheets/PRIORITY_config_tradier.xlsx' style='color:#8cf'>PRIORITY_config_tradier.xlsx</a> · "
                 "<a href='/spreadsheets/PERSYM_APPLIED_REVIEW.xlsx' style='color:#8cf'>PERSYM_APPLIED_REVIEW.xlsx (applied-config audit)</a> · "
                 "<a href='/spreadsheets/FULL_PARAM_MATRIX_crypto.xlsx' style='color:#8cf'>FULL_PARAM_MATRIX_crypto.xlsx (every param x every symbol, re-run tools/build_full_param_matrix_xls.py)</a> · "
                 "<a href='/spreadsheets/FULL_PARAM_MATRIX_stocks.xlsx' style='color:#8cf'>FULL_PARAM_MATRIX_stocks.xlsx</a></p>")
    db_warn = ("<p style='color:#fd8'>⚠️ %s</p>" % db_note) if db_note else ""
    html = ("<html><head><title>symbols overview</title></head>"
            "<body style='background:#111;color:#ddd;font-family:sans-serif;padding:16px'>"
            "<h2>Symbols Overview — current settings + best backtest result per symbol"
            "<span style='font-size:13px;color:#888'> (crypto/stocks NEVER mixed; excludes /trb_review "
            "which is the applied-config audit, not full history)</span></h2>"
            "%s%s"
            "<div style='margin:10px 0'><a href='/trb_review' style='color:#dcc26b'>&larr; back to trb_review (applied configs)</a> · "
            "<a href='/' style='color:#dcc26b'>&larr; back to chart</a></div>"
            "%s%s"
            "<p style='color:#888'>green=sym_sharpe&gt;0.5 · yellow=positive · red=&le;0 · grey=[DIAGNOSTIC ONLY] "
            "(best available candidate had &lt;30 trades — noise, not a real edge) · sym_sharpe is per-symbol "
            "per-trade Sharpe CAPPED AT &plusmn;5.0, preferring &ge;30-trade samples, per CLAUDE.md rule 6 — never "
            "a cherry-picked few-trade outlier and never labeled bare 'pool_sharpe' · best backtest pulled from "
            "data/test_results_central.db · 'applied' column is the live per_sym config, NOT necessarily the best-ever result</p>"
            "</body></html>") % (xls_links, db_warn, _section("CRYPTO (USDT/USDC)", crypto_syms),
                                  _section("STOCKS", stock_syms))
    return _no_cache(app.response_class(html, mimetype="text/html"))


@app.route("/symbols")
def symbols():
    """All symbols with NPZ indicators on disk. Optional ?asset_class=crypto|stocks
    filters to that class only. The chart UI uses this to populate the symbol
    dropdown after the user picks a top-level tab."""
    asset_class = (request.args.get("asset_class") or "").strip().lower()
    local_syms = set(p.stem for p in NPZ_DIR.glob("*.npz"))
    syms = sorted(local_syms | set(_remote_npz_symbols()))
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


@app.route("/rundown")
def rundown():
    """Suggestions-vs-fills rundown for a stock account (default trb).
    ?acct=trb&day=YYYYMMDD (day optional, defaults to today UTC). Read-only:
    rebuilds from decisions JSONL + /history/ ledger + per_sym book on each call."""
    acct = request.args.get("acct", "trb").lower()
    day = request.args.get("day") or None
    try:
        import importlib, sys as _sys
        _tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
        if _tools not in _sys.path:
            _sys.path.insert(0, _tools)
        import trb_suggestion_rundown as _r
        importlib.reload(_r)
        _r.build(acct, day)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", f"{acct}_rundown.json")) as fh:
            return app.response_class(fh.read(), mimetype="application/json")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _pair_trade_events(events):
    """Convert a raw OPEN/AUGMENT/CLOSE/REDUCE event stream (schema: {ts, type,
    side, price, pnl_pct}) into canonical paired trades (schema: {entry_ts,
    exit_ts, side, pnl_pct}) so `_file_aggregates` counts real round-trips
    instead of raw event lines. AUGMENT is folded into the open trade (does not
    close it); REDUCE without a prior OPEN is dropped (partial-fill artifact,
    can't attribute an entry). pnl_pct on the closing event is treated as the
    round-trip return, matching how this event schema is actually written."""
    def _unix(ev):
        ts = str(ev.get("ts", "") or ev.get("timestamp", ""))
        try:
            return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        except Exception:
            return 0
    out = []
    open_trade = None
    for ev in events:
        etype = ev.get("type")
        if etype == "OPEN":
            open_trade = {"entry_ts": _unix(ev), "side": ev.get("side", ""), "entry_price": ev.get("price")}
        elif etype == "AUGMENT":
            continue
        elif etype in ("CLOSE", "REDUCE"):
            if open_trade is None:
                continue
            open_trade["exit_ts"] = _unix(ev)
            open_trade["exit_price"] = ev.get("price")
            open_trade["pnl_pct"] = ev.get("pnl_pct", 0)
            out.append(open_trade)
            open_trade = None
    return out


def _maybe_pair_events(trades: list) -> list:
    """Same event-stream detection as `_file_aggregates` (see there for the
    2026-07-06 root-cause note), exposed standalone for the other trade-loading
    call sites (/backtest_trades, /equity_curves) that read a run's JSONL
    directly instead of going through `_file_aggregates`. A no-op for the
    canonical entry_ts/exit_ts schema used by every current trade producer."""
    if trades and not any(t.get("entry_ts") for t in trades) and any(t.get("type") in ("OPEN", "CLOSE", "REDUCE", "AUGMENT") for t in trades):
        return _pair_trade_events(trades)
    return trades


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
    if trades and not any(t.get("entry_ts") for t in trades) and any(t.get("type") in ("OPEN", "CLOSE", "REDUCE", "AUGMENT") for t in trades):
        # Event-stream schema (one line per OPEN/AUGMENT/CLOSE/REDUCE event, keyed
        # by "ts" ISO string) instead of the canonical paired-trade schema
        # (entry_ts/exit_ts unix ints). Found 2026-07-06 in the only files
        # currently under V8_TRADES_OUT_DIR (tr_trend_v1_validation_20260517__*)
        # — treating raw event lines as if they were trades doubled the trade
        # count, halved the win-rate, and left ts_first/ts_last at 0 (which then
        # produced nonsense gain_per_day figures downstream). Pair events into
        # real round-trip trades before aggregating.
        trades = _pair_trade_events(trades)
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
    raw = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            raw.append(json.loads(line))
        except Exception:
            continue
    # See _maybe_pair_events: some legacy trade dumps under V8_TRADES_OUT_DIR are a
    # raw OPEN/CLOSE event stream, not canonical entry_ts/exit_ts trades — pairing
    # must happen BEFORE the start/end filter below (which reads entry_ts/exit_ts).
    raw = _maybe_pair_events(raw)
    trades = []
    for t in raw:
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
        raw: List[Dict[str, Any]] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                raw.append(json.loads(line))
            except Exception:
                continue
        raw = _maybe_pair_events(raw)  # see _maybe_pair_events: legacy event-stream dumps
        trades: List[Dict[str, Any]] = []
        for t in raw:
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
        open_rounds = _find_open_rounds(events)
        all_trades = sorted(trades + open_rounds, key=lambda t: t.get("entry_ts", 0))
        out_per_acct[acct] = {
            "events": events,
            "trades": all_trades,
            "stats": _trade_stats(trades),
        }
    return jsonify(out_per_acct)


def _find_open_rounds(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return currently-open (unclosed) trade rounds from an event stream.
    Replays OPEN/AUGMENT/REDUCE/CLOSE to find rounds that never closed."""
    rounds: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    for ev in events:
        side = ev.get("side")
        sym = ev.get("symbol") or ""
        kind = (ev.get("type") or "").upper()
        qty = float(ev.get("qty") or 0)
        price = float(ev.get("price") or 0)
        ts = ev.get("unix_ts", 0)
        if not side or not sym or qty <= 0 or price <= 0:
            continue
        key = (sym, side)
        rd = rounds.get(key)
        is_sync = "SYNC_DETECTION" in str(ev.get("reason", ""))
        if kind in ("OPEN", "AUGMENT", "REENTRY"):
            if rd is None or rd.get("qty", 0) <= 0:
                rounds[key] = {"side": side, "account": ev.get("account"), "symbol": sym,
                               "entry_ts": ts, "entry_price": price, "qty": qty, "peak_qty": qty,
                               "entry_reason": ev.get("reason", ""), "last_sync": is_sync}
            elif is_sync:
                # SYNC_DETECTION = absolute position snapshot (see _reconstruct_trades_from_events).
                rd["entry_price"] = price
                rd["qty"] = qty
                rd["peak_qty"] = max(rd.get("peak_qty", 0), qty)
                rd["last_sync"] = True
            else:
                new_qty = rd["qty"] + qty
                rd["entry_price"] = (rd["entry_price"] * rd["qty"] + price * qty) / new_qty
                rd["qty"] = new_qty
                rd["peak_qty"] = max(rd.get("peak_qty", 0), new_qty)
                rd["last_sync"] = False
        elif kind in ("REDUCE", "CLOSE") and rd is not None:
            close_qty = min(qty, rd["qty"])
            rd["qty"] -= close_qty
            rd["last_sync"] = False
            dust = max(1e-9, rd.get("peak_qty", 0) * 0.005)
            if rd["qty"] <= dust or kind == "CLOSE":
                rounds[key] = None
    out = []
    for rd in rounds.values():
        if rd is None:
            continue
        # Suppress phantom dust: a sub-$DUST position whose CURRENT size was last set
        # by a SYNC_DETECTION reconciliation snapshot is a leftover artifact (e.g. BNO
        # 1 share @ 52.46 after the real position was whittled away + ghost-closed),
        # NOT a live trade. Real fills (DC_BREAK, WT_*, REENTRY...) are always kept.
        notional = (rd.get("qty") or 0) * (rd.get("entry_price") or 0)
        if notional < OPEN_DUST_NOTIONAL_USD and rd.get("last_sync"):
            continue
        out.append(
            {"account": rd["account"], "symbol": rd["symbol"], "side": rd["side"],
             "entry_ts": rd["entry_ts"], "entry_price": rd["entry_price"],
             "exit_ts": None, "exit_price": None, "pnl_pct": None,
             "qty": rd.get("qty", 0), "peak_qty": rd.get("peak_qty", 0),
             "entry_reason": rd["entry_reason"], "exit_reason": None, "status": "OPEN"}
        )
    return out


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
        is_sync = "SYNC_DETECTION" in str(reason)
        if kind in ("OPEN", "AUGMENT", "REENTRY"):
            if rd is None or rd.get("qty", 0) <= 0:
                rd = {
                    "side": side, "account": ev.get("account"), "symbol": sym,
                    "entry_ts": ts, "entry_price": price, "qty": qty,
                    "peak_qty": qty,
                    "entry_reason": reason, "events": [ev],
                }
                rounds[key] = rd
            elif is_sync:
                # SYNC_DETECTION reports the ABSOLUTE current position (total qty +
                # avg entry price), NOT a delta fill. Earlier code added it as an
                # augment, inflating size up to 6x (e.g. LSCC_LONG: 291 snapshots →
                # phantom 89 shares vs real 14). SET the round to the snapshot;
                # preserve the original entry_ts.
                rd["entry_price"] = price
                rd["qty"] = qty
                rd["peak_qty"] = max(rd.get("peak_qty", 0), qty)
                rd["events"].append(ev)
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
                    "qty": close_qty, "peak_qty": rd.get("peak_qty", close_qty),
                    "entry_reason": rd["entry_reason"], "exit_reason": reason,
                    "pnl_pct": pnl_pct, "pnl_usd": (pnl_pct / 100.0) * (rd["entry_price"] * close_qty),
                    "duration_sec": ts - rd["entry_ts"],
                    "stream": "historic",
                })
                rounds[key] = None
    return closed


@app.route("/local_runs")
def local_runs():
    """List all selectable backtest runs for a given symbol.
    Filename pattern: <run_id>__<SYMBOL>.jsonl
    Each entry: {run, trades, window_days, pool_sharpe, win_rate, gain_per_day_pct, gain_per_trade_pct}
    Per NO-LIES MANDATE: NO annualized fields. Only per-day, per-trade, window_days.
    """
    target_sym = (request.args.get("sym") or "").upper()
    if not target_sym:
        return jsonify({"runs": [], "error": "sym required"})
    out = []
    selectable: Dict[str, Path] = {}
    if TRADES_DIR.exists():
        for path in sorted(TRADES_DIR.glob(f"*__{target_sym}.jsonl")):
            run_id = path.stem.rsplit(f"__{target_sym}", 1)[0]
            if run_id:
                selectable[run_id] = path
    # The chart loader has supported extra roots for years, but the dropdown
    # only scanned /tmp/v8_trades.  That made compact S1/vector overlays
    # loadable by a hand-written URL yet invisible and unselectable in the UI.
    for run_id, indexed_path in _get_run_registry().items():
        path = _resolve_trade_path(run_id, target_sym)
        if path is None or not path.exists():
            continue
        if not path.stem.endswith(f"__{target_sym}"):
            continue
        selectable.setdefault(run_id, path)
    for run_id, path in sorted(selectable.items()):
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
    return jsonify({
        "runs": out,
        "count": len(out),
        "trades_dir": str(TRADES_DIR),
        "extra_roots_scanned": len(_all_trade_roots()),
    })


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
    for host in ("s1-int",):  # 2026-05-28 S2 DEAD permanently — s2-int removed
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
  header .pill.net { background:#0d2818; color:var(--win); border-color:var(--win); font-weight:bold; }
  .live-row td { color: #8b949e; font-style: italic; }
  .live-row td.live-tag { color: #8b949e; font-weight: bold; }
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
  /* 2026-05-18: per-symbol LIVE override pane — red because this is the
     actual customized live trading config for this symbol RIGHT NOW. */
  table.live-overrides td { color: var(--los); }
  table.live-overrides td.k { color: var(--los); font-weight: bold; }
  table.live-overrides td.tag { color: var(--mute); font-size: 10px; }
  table.live-overrides tr:hover { background: #2d0c0e; }
  .right h3.live-h3 { color: var(--los); border-bottom-color: var(--los); }
  .right h3.live-h3 .tag-acct { background: #3d1114; color: var(--los); padding: 1px 5px;
                                border-radius: 3px; font-size: 10px; margin-left: 4px; }
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
  <span class="pill net" title="Engines now write pnl_pct NET of round-trip cost (crypto 0.08% / stocks 0.05%, configurable). pnl_pct_gross preserved for audit.">NET of cost</span>
  <label>Symbol: <select id="symPicker"></select></label>
  <span class="pill" id="runCount">— runs</span>
  <span class="pill" id="resultStats">—</span>
  <label class="ts-mode-toggle"><input type="checkbox" id="utcMode" checked> UTC ts</label>
  <label class="ts-mode-toggle" id="liveOverlayLabel" style="display:none"><input type="checkbox" id="liveOverlay"> 7D live overlay</label>
  <span class="pill" id="liveOverlayInfo" style="display:none"></span>
  <span style="margin-left:auto">
    <a href="/backtest-review/__ASSET__/top">▦ leaderboard</a> &nbsp;|&nbsp;
    <a href="/">← chart_server</a> &nbsp;|&nbsp;
    <a href="/backtest-review/__OTHER_ASSET__">→ __OTHER_ASSET__</a>
  </span>
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
          <th data-col="src">Src</th>
          <th data-col="entry_ts">Entry</th>
          <th data-col="exit_ts">Exit</th>
          <th data-col="side">Side</th>
          <th data-col="entry_price">Entry $</th>
          <th data-col="exit_price">Exit $</th>
          <th data-col="pnl_pct" title="Net of round-trip cost (the metric used everywhere on this page)">Net %</th>
          <th data-col="pnl_pct_gross" title="Raw price-only return; pnl_pct_gross - round_trip_cost_pct = pnl_pct. Lets you see what the engine produced before costs.">Gross %</th>
          <th data-col="duration">Dur</th>
          <th data-col="entry_reason">Entry reason</th>
          <th data-col="exit_reason">Exit reason</th>
        </tr></thead>
        <tbody><tr><td colspan="12" class="empty">Pick a symbol then a run.</td></tr></tbody>
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

    <div id="liveOverridesWrap" style="display:none">
      <h3 class="live-h3">PER-SYM LIVE OVERRIDES (7D, customized) <span id="liveOverridesMeta" style="float:right;font-size:10px;color:var(--mute)"></span></h3>
      <div style="font-size:10px;color:var(--mute);margin-bottom:4px">
        These are the knob overrides currently driving live trading for this symbol per <code>data/hourly_reconfig/&lt;acct&gt;/active_config.json</code>. Shown in red because they override <code>config.py</code> defaults and change hourly.
      </div>
      <div style="max-height:240px; overflow-y:auto">
        <table class="knobs live-overrides" id="liveOverridesTable"><tbody></tbody></table>
      </div>
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
let liveOverlay = []; // 7D live rounds, reconstructed
let liveOverlayActive = false;
let liveOverlayAccts = []; // accts with hourly_reconfig active configs for this sym
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
  // /symbols_with_runs returns only symbols that have ≥1 backtest run with
  // trades — no empty entries in the picker. Falls back to /symbols if the
  // precompute cache hasn't warmed yet (HTTP 503).
  let syms = [];
  try {
    const r = await fetch(`/symbols_with_runs?asset=${ASSET}`);
    if (r.ok) {
      const data = await r.json();
      syms = data.symbols || [];
    }
  } catch(_) {}
  if (!syms.length) {
    try {
      let all = await fetchJSON("/symbols");
      syms = all.filter(s => {
        const isCrypto = s.endsWith("USDC") || s.endsWith("USDT");
        return ASSET === "crypto" ? isCrypto : !isCrypto;
      });
    } catch(_) { syms = []; }
  }
  syms.sort();
  sel.innerHTML = syms.map(s => `<option value="${s}">${s}</option>`).join("");
  const initial = syms.includes(DEFAULT_SYM) ? DEFAULT_SYM : syms[0];
  if (initial) sel.value = initial;
  sel.addEventListener("change", () => onSymbolChange(sel.value));
  return initial;
}

async function onSymbolChange(sym) {
  currentSym = sym;
  currentRun = null;
  currentTrades = [];
  liveOverlay = [];
  document.getElementById("resultStats").textContent = "—";
  document.getElementById("diagBanner").style.display = "none";
  await Promise.all([loadRuns(sym), loadLiveOverlayMeta(sym)]);
  // Auto-pick top of sorted list
  if (allRuns.length) {
    await onRunChange(allRuns[0]);
  } else {
    renderTradeTable([]);
    renderKnobs(null);
    drawChartBars();
  }
}

async function loadLiveOverlayMeta(sym) {
  // Probe which accounts have an hourly_reconfig active_config for this sym.
  // If none → hide the toggle AND the red overrides pane. Both surfaces are
  // only meaningful for symbols whose live behavior is driven by per-sym
  // custom hourly settings.
  liveOverlayAccts = [];
  const lbl = document.getElementById("liveOverlayLabel");
  const info = document.getElementById("liveOverlayInfo");
  const wrap = document.getElementById("liveOverridesWrap");
  const tbody = document.querySelector("#liveOverridesTable tbody");
  const meta = document.getElementById("liveOverridesMeta");
  try {
    const data = await fetchJSON(`/historic_recent_overlay?sym=${encodeURIComponent(sym)}&days=7`);
    liveOverlayAccts = data.accounts || [];
    const overridesByAS = data.overrides_by_acct_side || {};
    if (liveOverlayAccts.length) {
      lbl.style.display = "";
      info.style.display = "";
      info.textContent = `acct: ${liveOverlayAccts.join(', ')}`;
    } else {
      lbl.style.display = "none";
      info.style.display = "none";
    }
    // Red pane: collapse all overrides across accounts × sides into a single
    // table. If a key has the same value across accts/sides, show one row
    // with combined tags; otherwise one row per variant.
    const keys = Object.keys(overridesByAS);
    if (!keys.length) {
      wrap.style.display = "none";
      tbody.innerHTML = "";
      return;
    }
    // Group: knob_key -> {value -> [acct:side, ...]}
    const grouped = {};
    let total_n = 0;
    for (const ackey of keys) {
      const rec = overridesByAS[ackey] || {};
      const ovr = rec.overrides || {};
      for (const k of Object.keys(ovr)) {
        const v = ovr[k];
        const vk = JSON.stringify(v);
        if (!grouped[k]) grouped[k] = {};
        if (!grouped[k][vk]) grouped[k][vk] = { value: v, tags: [], tag_str: "" };
        grouped[k][vk].tags.push(ackey);
        total_n++;
      }
    }
    meta.textContent = `${Object.keys(grouped).length} keys · ${keys.length} acct·side variants`;
    // Render: sorted by key, with grouping
    const rows = [];
    const ks = Object.keys(grouped).sort();
    for (const k of ks) {
      const variants = grouped[k];
      for (const vk of Object.keys(variants)) {
        const ent = variants[vk];
        const tagStr = ent.tags.join(', ');
        rows.push(`<tr title="customized for: ${tagStr}">
          <td class="k">${k}</td>
          <td>${escapeHtml(ent.value)}</td>
          <td class="tag">${tagStr}</td>
        </tr>`);
      }
    }
    tbody.innerHTML = rows.join("");
    wrap.style.display = "";
  } catch(e) {
    lbl.style.display = "none";
    info.style.display = "none";
    wrap.style.display = "none";
  }
}

async function refreshLiveOverlay() {
  if (!liveOverlayActive || !currentSym) {
    liveOverlay = [];
    return;
  }
  try {
    const data = await fetchJSON(`/historic_recent_overlay?sym=${encodeURIComponent(currentSym)}&days=7`);
    liveOverlay = data.events || [];
  } catch(e) {
    liveOverlay = [];
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
  const [bt, _] = await Promise.all([
    fetchJSON(`/backtest_trades?run=${encodeURIComponent(runRow.run_keyed)}&sym=${encodeURIComponent(currentSym)}&max=999999`),
    refreshLiveOverlay(),
  ]);
  currentTrades = bt.trades || [];
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
  // 7D LIVE OVERLAY — grayscale hollow diamonds, visually quieter than backtest.
  // Plotted on top of backtest markers so user can see "live did X here, backtest did Y."
  // Mandate (2026-05-18): visually subdued = "reference only," not primary data.
  if (liveOverlayActive) {
    for (const t of liveOverlay) {
      const isLong = (t.side || "LONG").toUpperCase() === "LONG";
      if (t.entry_ts) {
        markers.push({
          time: t.entry_ts,
          position: isLong ? "belowBar" : "aboveBar",
          color: "#8b949e",
          shape: "circle",
          text: `◇L·${t.account||""}`,
          size: 0,
        });
      }
      if (t.exit_ts) {
        markers.push({
          time: t.exit_ts,
          position: isLong ? "aboveBar" : "belowBar",
          color: "#8b949e",
          shape: "circle",
          text: `◇X·${(t.pnl_pct>=0?"+":"") + (t.pnl_pct||0).toFixed(2)}%`,
          size: 0,
        });
      }
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
  // Build merged list: backtest trades + (optionally) live overlay rounds
  const merged = trades.map(t => ({...t, _src: "BT"}));
  if (liveOverlayActive) {
    for (const t of liveOverlay) {
      merged.push({...t, _src: `LIVE:${t.account||"?"}`});
    }
  }
  if (!merged.length) {
    tbody.innerHTML = `<tr><td colspan="12" class="empty">No trades for this run.</td></tr>`;
    return;
  }
  merged.sort((a, b) => {
    let av, bv;
    if (tradeSortCol === "duration") { av = (a.exit_ts||0) - (a.entry_ts||0); bv = (b.exit_ts||0) - (b.entry_ts||0); }
    else if (tradeSortCol === "idx") { av = merged.indexOf(a); bv = merged.indexOf(b); }
    else if (tradeSortCol === "src") { av = a._src; bv = b._src; }
    else { av = a[tradeSortCol]; bv = b[tradeSortCol]; }
    if (av === bv) return 0;
    if (av === undefined || av === null) return 1;
    if (bv === undefined || bv === null) return -1;
    return tradeSortDir * (av < bv ? -1 : 1);
  });
  const html = merged.map((t) => {
    const dur = (t.exit_ts||0) - (t.entry_ts||0);
    const pcCls = (t.pnl_pct||0) >= 0 ? "win" : "los";
    const sideCls = (t.side||"LONG").toUpperCase() === "LONG" ? "long" : "short";
    const isLive = t._src && t._src.startsWith("LIVE");
    const rowCls = isLive ? "live-row" : "";
    const grossCell = (t.pnl_pct_gross !== undefined && t.pnl_pct_gross !== null)
                     ? `<td class="${pcCls}">${fmtPct(t.pnl_pct_gross)}</td>`
                     : `<td class="${pcCls}" title="No gross recorded (older file or live event)">${isLive ? '—' : fmtPct(t.pnl_pct)}</td>`;
    const srcCell = isLive
      ? `<td class="live-tag">${t._src}</td>`
      : `<td>BT</td>`;
    return `<tr class="${rowCls}" data-entry="${t.entry_ts||0}" data-exit="${t.exit_ts||0}">
      <td>${merged.indexOf(t)+1}</td>
      ${srcCell}
      <td>${fmtTs(t.entry_ts)}</td>
      <td>${fmtTs(t.exit_ts)}</td>
      <td class="${sideCls}">${t.side||""}</td>
      <td>${fmtNum(t.entry_price,6)}</td>
      <td>${fmtNum(t.exit_price,6)}</td>
      <td class="${pcCls}">${fmtPct(t.pnl_pct)}</td>
      ${grossCell}
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
document.getElementById("liveOverlay").addEventListener("change", async (e) => {
  liveOverlayActive = e.target.checked;
  await refreshLiveOverlay();
  const runRow = currentRun ? allRuns.find(r => r.run_keyed === currentRun) : null;
  if (runRow) await drawChartBars(runRow);
  renderTradeTable(currentTrades);
});

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


@app.route("/symbols_with_runs")
def symbols_with_runs():
    """Sorted list of symbols that have ≥1 backtest run with trades, filtered
    by asset class. Used by the /backtest-review/<asset> symbol picker so the
    user doesn't see empty entries (e.g. PEPEUSDC with no run files yet).
    Backed by the precomputed runs_ranked cache so it's O(1) at request time."""
    asset = request.args.get("asset", "").lower()
    body = _precomputed.get("runs_ranked")
    if not body:
        return jsonify({"warming": True, "symbols": []}), 503
    try:
        rows = json.loads(body)
    except Exception:
        return jsonify({"error": "cache parse"}), 500
    seen: set = set()
    for r in rows:
        for s in (r.get("syms") or []):
            su = (s or "").upper()
            if not su:
                continue
            if asset == "crypto" and not _is_crypto_sym(su):
                continue
            if asset == "stocks" and _is_crypto_sym(su):
                continue
            seen.add(su)
    return jsonify({"symbols": sorted(seen), "n": len(seen), "asset": asset or "any"})


@app.route("/backtest_review_top")
def backtest_review_top_data():
    """Pool-level leaderboard for the /backtest-review/<asset>/top page.
    Reads precomputed runs_ranked (refreshed every 30s by _precompute_loop),
    filters by asset class via run's symbol list, applies CLAUDE.md sample
    floors as flags (NOT silent filtering — diagnostic stays visible but
    badged so user can't promote them by mistake)."""
    asset = request.args.get("asset", "crypto").lower()
    if asset not in ("crypto", "stocks"):
        return jsonify({"error": "asset must be crypto or stocks"}), 400
    sort_by = request.args.get("sort", "pool_sharpe")
    limit = int(request.args.get("limit", 100))
    min_trades = int(request.args.get("min_trades", 0))
    body = _precomputed.get("runs_ranked")
    if not body:
        return jsonify({"warming": True, "rows": []}), 503
    try:
        rows = json.loads(body)
    except Exception:
        return jsonify({"error": "cache parse"}), 500
    # Per-row asset class via syms (crypto if any USDC/USDT, else stocks).
    def _row_asset(r):
        for s in (r.get("syms") or []):
            if _is_crypto_sym(s):
                return "crypto"
        return "stocks"
    rows = [r for r in rows if _row_asset(r) == asset]
    if min_trades > 0:
        rows = [r for r in rows if r.get("trades", 0) >= min_trades]
    # Sample-floor flag per CLAUDE.md: ≥48 crypto syms or ≥100 stocks, ≥1yr,
    # ≥30 trades per sym (approximated as total_trades / n_syms ≥ 30).
    min_syms = 48 if asset == "crypto" else 100
    for r in rows:
        n_syms = r.get("n_syms", 0)
        years = r.get("n_years", 0)
        trades = r.get("trades", 0)
        avg_per_sym = trades / max(1, n_syms)
        sample_ok = (n_syms >= min_syms) and (years >= 1.0) and (avg_per_sym >= 30)
        r["sample_ok"] = bool(sample_ok)
        bh_multiple = results_dashboard_lib._bh_multiple(r)
        r["bh_multiple"] = bh_multiple
        # Fail closed: runs_ranked currently has no canonical B&H series, so a
        # high-Sharpe row is diagnostic until it carries an explicit/derivable
        # strategy/B&H multiple. DD also remains unwired.
        r["promotable"] = bool(
            sample_ok
            and r.get("pool_sharpe", 0) > 1.0
            and bh_multiple is not None
            and bh_multiple >= results_dashboard_lib.MIN_BH_MULTIPLE
        )
        blockers = []
        if not sample_ok:
            blockers.append("SAMPLE_FLOOR")
        if r.get("pool_sharpe", 0) <= 1.0:
            blockers.append("POOL_SHARPE_LE_1")
        if bh_multiple is None:
            blockers.append("BH_MULTIPLE_MISSING")
        elif bh_multiple < results_dashboard_lib.MIN_BH_MULTIPLE:
            blockers.append("BELOW_2X_BH")
        r["promotion_blockers"] = blockers
        r["gain_per_mo"] = round(r.get("gain_per_yr", 0) / 12.0, 2)
        r["machine_hint"] = ""  # placeholder for future ext-archive labeling
    # Sort
    SORT_DESC = {"pool_sharpe", "sym_sharpe", "gain_per_yr", "gain_per_mo",
                 "gain_sym_yr", "avg_gain_trade", "trades", "wr",
                 "total_gain_pct", "mtime", "n_syms", "n_years",
                 "bh_multiple"}
    rev = sort_by in SORT_DESC
    rows.sort(key=lambda r: (r.get(sort_by) is None, -r.get(sort_by, 0) if rev else r.get(sort_by, 0)))
    rows = rows[:limit]
    return jsonify({
        "asset": asset,
        "sort": sort_by,
        "n_rows": len(rows),
        "rows": rows,
        "claude_md_sample_floor": {
            "min_syms": min_syms,
            "min_years": 1.0,
            "min_trades_per_sym": 30,
        },
        "promotion_contract": {
            "min_pool_sharpe": 1.0,
            "min_bh_multiple": results_dashboard_lib.MIN_BH_MULTIPLE,
            "max_dd_required": False,
            "max_dd_status": "UNWIRED_DIAGNOSTIC_WARNING",
            "fail_closed_when_bh_multiple_missing": True,
        },
    })


@app.route("/historic_recent_overlay")
def historic_recent_overlay():
    """7D live overlay for the per-symbol /backtest-review page.
    Returns reconstructed live rounds for a symbol, filtered to the last 7
    days, ONLY for accounts that have a winning hourly_reconfig active_config
    entry for this (sym, side). The premise is: per CLAUDE.md, custom hourly
    overrides should be sanity-checked against the live trade outcomes they
    produced. Symbols without active per-sym configs return empty — no live
    overlay needed there.

    Output:
      {
        accounts: ["fin","inf",...],
        active_configs: {acct: {sym_side: {winning_tag, n_overrides}}},
        events: [...flat list of reconstructed close events keyed by acct+side...]
      }
    """
    sym = request.args.get("sym", "").upper()
    if not sym:
        return jsonify({"error": "sym required"}), 400
    days = int(request.args.get("days", 7))
    cutoff = int(time.time()) - days * 86400
    is_crypto = _is_crypto_sym(sym)
    candidate_accts = list(CRYPTO_ACCOUNTS_ORDER) if is_crypto else list(STOCK_ACCOUNTS_ORDER)
    active_configs: Dict[str, Dict[str, Dict[str, Any]]] = {}
    overrides_by_acct_side: Dict[str, Dict[str, Any]] = {}
    for acct in candidate_accts:
        p = BASE_PATH / "data" / "hourly_reconfig" / acct / "active_config.json"
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        per_side: Dict[str, Dict[str, Any]] = {}
        for side in ("LONG", "SHORT"):
            rec = data.get(f"{sym}_{side}") or {}
            wt = rec.get("winning_tag")
            overrides = rec.get("overrides") or {}
            if wt:
                per_side[side] = {"winning_tag": wt, "n_overrides": len(overrides)}
                if overrides:
                    overrides_by_acct_side[f"{acct}:{side}"] = {
                        "winning_tag": wt,
                        "overrides": overrides,
                    }
        if per_side:
            active_configs[acct] = per_side
    # Pull recent live events from history dirs (only for accts with active configs)
    flat_events: List[Dict[str, Any]] = []
    rounds_by_acct_side: Dict[str, List[Dict[str, Any]]] = {}
    for acct in active_configs:
        base = _history_dir_for(acct)
        for side in ("LONG", "SHORT"):
            if side not in active_configs[acct]:
                continue
            p = base / f"{sym}_{side}.jsonl"
            if not p.exists():
                continue
            events: List[Dict[str, Any]] = []
            try:
                txt = p.read_text()
            except Exception:
                continue
            for line in txt.splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                ts_str = ev.get("ts", "")
                try:
                    unix_ts = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
                except Exception:
                    unix_ts = 0
                if unix_ts < cutoff:
                    continue
                ev["unix_ts"] = unix_ts
                ev["side"] = side
                ev["account"] = acct
                ev["symbol"] = sym
                events.append(ev)
            events.sort(key=lambda e: e.get("unix_ts", 0))
            rounds = _reconstruct_trades_from_events(events)
            for r in rounds:
                r["account"] = acct
                r["live"] = True
                flat_events.append(r)
            rounds_by_acct_side[f"{acct}:{side}"] = rounds
    return jsonify({
        "sym": sym,
        "days": days,
        "cutoff_ts": cutoff,
        "accounts": list(active_configs.keys()),
        "active_configs": active_configs,
        "overrides_by_acct_side": overrides_by_acct_side,
        "events": flat_events,
        "rounds_by_acct_side": rounds_by_acct_side,
    })


_BACKTEST_REVIEW_TOP_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>__TITLE__</title>
<style>
  :root { --bg:#0d1117; --bg2:#161b22; --bd:#30363d; --fg:#c9d1d9; --mute:#8b949e;
          --acc:#58a6ff; --win:#3fb950; --los:#f85149; --warn:#d29922; }
  * { box-sizing: border-box; }
  body { font-family:-apple-system,sans-serif; margin:0; background:var(--bg); color:var(--fg); font-size:13px; }
  header { padding:8px 16px; background:var(--bg2); border-bottom:1px solid var(--bd);
           display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
  header h1 { margin:0; font-size:16px; color:var(--acc); }
  header a { color:var(--acc); text-decoration:none; }
  header a:hover { text-decoration: underline; }
  .pill { background:var(--bg); border:1px solid var(--bd); padding:3px 8px;
          border-radius:4px; font-size:11px; color:var(--mute); }
  .pill.net { background:#0d2818; color:var(--win); border-color:var(--win); }
  .pill.diag { background:#3d1114; color:var(--los); border-color:var(--los); }
  .pill.prom { background:#0d2818; color:var(--win); font-weight:bold; }
  select, input, button { background:var(--bg); color:var(--fg); border:1px solid var(--bd);
                          padding:4px 8px; border-radius:4px; font-size:12px; }
  main { padding:12px 16px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th { position:sticky; top:0; background:var(--bg2); border-bottom:1px solid var(--bd);
       padding:6px 8px; text-align:left; color:var(--acc); cursor:pointer; user-select:none; }
  th:hover { background:var(--bd); }
  th.sorted::after { content:' \25BE'; }
  th.sorted-asc::after { content:' \25B4'; }
  td { padding:4px 8px; border-bottom:1px solid #21262d; font-family:ui-monospace,monospace; }
  tr:hover td { background:var(--bg2); cursor:pointer; }
  tr.promotable td { background:#0d2818; }
  td.win { color:var(--win); }
  td.los { color:var(--los); }
  .tagcol { display:flex; gap:4px; flex-wrap:wrap; }
  .stagebar { background:var(--bg2); padding:8px 16px; border-bottom:1px solid var(--bd); display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .empty { color:var(--mute); padding:24px; text-align:center; }
</style>
</head>
<body>
<header>
  <h1>__TITLE__ — Top Runs</h1>
  <span class="pill net">All numbers NET of round-trip cost (~__RT_COST__% per trade)</span>
  <span style="margin-left:auto">
    <a href="/backtest-review/__ASSET__">← per-symbol review</a> &nbsp;|&nbsp;
    <a href="/backtest-review/__OTHER_ASSET__/top">switch to __OTHER_ASSET__</a>
  </span>
</header>
<div class="stagebar">
  <label>Sort: <select id="sortPick">
    <option value="pool_sharpe">pool_sharpe (canonical)</option>
    <option value="sym_sharpe">sym_sharpe (avg of per-sym, ±5 cap)</option>
    <option value="gain_per_mo">gain / month</option>
    <option value="gain_per_yr">gain / year</option>
    <option value="gain_sym_yr">gain / sym / year</option>
    <option value="bh_multiple">B&amp;H multiple</option>
    <option value="avg_gain_trade">avg gain / trade</option>
    <option value="wr">win rate</option>
    <option value="trades">trade count</option>
    <option value="mtime">most recent</option>
  </select></label>
  <label>Min trades: <input id="minTrades" type="number" value="0" min="0" style="width:80px"></label>
  <label>Limit: <input id="limit" type="number" value="100" min="10" max="500" style="width:80px"></label>
  <label><input type="checkbox" id="promotableOnly"> Promotable only (sample floor + pool_sh &gt; 1.0 + &ge;2x B&amp;H; DD pending)</label>
  <label><input type="checkbox" id="sampleOk"> Sample-floor-cleared only</label>
  <span class="pill" id="resultsLbl">—</span>
</div>
<main>
  <table id="topTable">
    <thead><tr>
      <th data-col="run">Run</th>
      <th data-col="tags">Tags</th>
      <th data-col="pool_sharpe">pool_sharpe</th>
      <th data-col="sym_sharpe">sym_sharpe</th>
      <th data-col="avg_gain_trade">avg/trade %</th>
      <th data-col="gain_per_mo">% / month</th>
      <th data-col="gain_per_yr">% / year</th>
      <th data-col="gain_sym_yr">% / sym / yr</th>
      <th data-col="bh_multiple">B&amp;H multiple</th>
      <th data-col="trades">trades</th>
      <th data-col="n_syms">syms</th>
      <th data-col="n_years">years</th>
      <th data-col="wr">WR</th>
      <th data-col="mtime">date</th>
    </tr></thead>
    <tbody><tr><td colspan="14" class="empty">Loading…</td></tr></tbody>
  </table>
</main>
<script>
const ASSET = "__ASSET__";
let rawRows = [];
let sortCol = "pool_sharpe";
let sortDir = -1;

function fmtNum(x, d=4) { if (x===null||x===undefined||isNaN(x)) return ""; return Number(x).toFixed(d); }
function fmtDate(t) { if (!t) return "—"; return new Date(t*1000).toISOString().slice(0,10); }

async function loadAndRender() {
  const sortBy = document.getElementById("sortPick").value;
  const minT = document.getElementById("minTrades").value || 0;
  const lim = document.getElementById("limit").value || 100;
  sortCol = sortBy;
  const url = `/backtest_review_top?asset=${ASSET}&sort=${sortBy}&min_trades=${minT}&limit=${lim}`;
  const tbody = document.querySelector("#topTable tbody");
  tbody.innerHTML = `<tr><td colspan="14" class="empty">Loading…</td></tr>`;
  let data;
  try { data = await fetch(url).then(r => r.json()); }
  catch(e) { tbody.innerHTML = `<tr><td colspan="14" class="empty">Error: ${e.message}</td></tr>`; return; }
  if (data.warming) {
    tbody.innerHTML = `<tr><td colspan="14" class="empty">Precompute warming up… refresh in a few seconds.</td></tr>`;
    return;
  }
  rawRows = data.rows || [];
  render();
}

function render() {
  const tbody = document.querySelector("#topTable tbody");
  const promOnly = document.getElementById("promotableOnly").checked;
  const sfOnly = document.getElementById("sampleOk").checked;
  let rows = rawRows;
  if (promOnly) rows = rows.filter(r => r.promotable);
  else if (sfOnly) rows = rows.filter(r => r.sample_ok);
  document.getElementById("resultsLbl").textContent = `${rows.length} rows · sorted by ${sortCol} ${sortDir<0?'▾':'▴'}`;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="14" class="empty">No rows match current filters.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(r => {
    const promCls = r.promotable ? "promotable" : "";
    const tags = [];
    if (r.promotable) tags.push(`<span class="pill prom">PROMOTABLE ≥2x B&H</span>`);
    else if (r.sample_ok) tags.push(`<span class="pill">sample-floor ✓</span>`);
    else tags.push(`<span class="pill diag">DIAGNOSTIC</span>`);
    // Click-through: first sym in run → per-symbol page
    const sym0 = (r.syms && r.syms[0]) || "";
    const link = sym0 ? `/backtest-review/${ASSET}#sym=${encodeURIComponent(sym0)}` : `/backtest-review/${ASSET}`;
    const shCls = r.pool_sharpe >= 1 ? "win" : (r.pool_sharpe < 0 ? "los" : "");
    return `<tr class="${promCls}" data-link="${link}" title="blockers=${(r.promotion_blockers||[]).join(',')} · syms=${(r.syms||[]).slice(0,8).join(', ')}${r.syms && r.syms.length>8?'…':''}">
      <td>${r.run}</td>
      <td class="tagcol">${tags.join('')}</td>
      <td class="${shCls}">${fmtNum(r.pool_sharpe,4)}</td>
      <td>${fmtNum(r.sym_sharpe,4)}</td>
      <td>${fmtNum(r.avg_gain_trade,3)}</td>
      <td>${fmtNum(r.gain_per_mo,2)}</td>
      <td>${fmtNum(r.gain_per_yr,1)}</td>
      <td>${fmtNum(r.gain_sym_yr,2)}</td>
      <td>${r.bh_multiple==null?'—':fmtNum(r.bh_multiple,3)+'x'}</td>
      <td>${r.trades}</td>
      <td>${r.n_syms}</td>
      <td>${fmtNum(r.n_years,2)}</td>
      <td>${(r.wr*100).toFixed(1)}%</td>
      <td>${fmtDate(r.mtime)}</td>
    </tr>`;
  }).join("");
  document.querySelectorAll("#topTable th").forEach(th => {
    th.classList.remove("sorted","sorted-asc");
    if (th.dataset.col === sortCol) th.classList.add(sortDir<0 ? "sorted" : "sorted-asc");
  });
  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => { window.location.href = tr.dataset.link; });
  });
}

document.querySelectorAll("#topTable th").forEach(th => {
  th.addEventListener("click", () => {
    const col = th.dataset.col;
    if (col === "run" || col === "tags") return;
    document.getElementById("sortPick").value = col;
    loadAndRender();
  });
});
document.getElementById("sortPick").addEventListener("change", loadAndRender);
document.getElementById("minTrades").addEventListener("change", loadAndRender);
document.getElementById("limit").addEventListener("change", loadAndRender);
document.getElementById("promotableOnly").addEventListener("change", render);
document.getElementById("sampleOk").addEventListener("change", render);
loadAndRender();
</script>
</body>
</html>
"""


@app.route("/backtest-review/<asset>/top")
def backtest_review_top_page(asset: str):
    asset = asset.lower()
    if asset not in ("crypto", "stocks"):
        return f"<p>Unknown asset class: {asset}.</p>", 404
    title = "Crypto Backtest" if asset == "crypto" else "Stocks Backtest"
    other = "stocks" if asset == "crypto" else "crypto"
    rt_cost = "0.08" if asset == "crypto" else "0.05"
    html = (_BACKTEST_REVIEW_TOP_HTML
            .replace("__TITLE__", title)
            .replace("__ASSET__", asset)
            .replace("__OTHER_ASSET__", other)
            .replace("__RT_COST__", rt_cost))
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
        "npz_remote_count": len(_remote_npz_symbols()),
        "trade_files": len(list(TRADES_DIR.glob("*.jsonl"))) if TRADES_DIR.exists() else 0,
    })


@app.route("/persym_backtest_trades")
def persym_backtest_trades():
    # 2026-06-02: serve the per_sym backtest "should-have-traded" events for a symbol, from the
    # latest per_sym vec backtest trade dump (data/sweep_results/persym_stock_backtest_trades.jsonl).
    # Same shape as /historic_trades so the dashboard can render a SECOND marker layer (backtest vs live).
    sym = request.args.get("sym", "").upper()
    if not sym:
        return jsonify({"error": "sym required"}), 400
    # PREFER Tier-2 (backtest_v8_engine) trades — Tier-2 calls the REAL live entry code, so it makes the
    # SAME entries as live (true parity). data/tier2_trades/<run>__<SYM>.jsonl. Fall back to the vec dump
    # only if no Tier-2 file exists yet (the vec dump is a fast SHORTLIST and does NOT match live entries).
    src_tag = "tier2_live_parity"
    t2_dir = BASE_PATH / "data" / "tier2_trades"
    t2_files = sorted(t2_dir.glob(f"*__{sym}.jsonl"), key=lambda p: p.stat().st_mtime) if t2_dir.exists() else []
    if t2_files:
        lines = t2_files[-1].read_text(errors="ignore").splitlines()
    else:
        src_tag = "vec_shortlist_NOT_live_parity"
        vec = BASE_PATH / "data" / "sweep_results" / "persym_stock_backtest_trades.jsonl"
        if not vec.exists():
            return jsonify({"events": [], "source": src_tag, "missing": str(t2_dir)})
        lines = [ln for ln in vec.read_text(errors="ignore").splitlines() if sym in ln]
    events = []
    for ln in lines:
        if '"symbol"' not in ln and '"type"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if str(d.get("symbol", "")).upper() != sym:
            continue
        ts = str(d.get("ts", "") or d.get("timestamp", ""))
        try:
            unix_ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        except Exception:
            unix_ts = None
        events.append({"symbol": sym, "side": d.get("side"), "type": d.get("type"), "price": d.get("price"), "qty": d.get("qty"), "value": d.get("value"), "reason": d.get("reason"), "pnl_pct": d.get("pnl_pct"), "ts": ts, "unix_ts": unix_ts, "source": "backtest"})
    return jsonify({"events": events, "source": src_tag})


@app.route("/trb_gen_bt_trades")
def trb_gen_bt_trades():
    """Return exact trades from the latest per_sym_20d_agent backtest for this symbol.
    Reads from data/hourly_reconfig/{acct}/_trade_lists/{sym}_trades.json
    which is written by per_sym_20d_agent_stocks.py on every cycle and synced via autosync.
    Falls back to SSH-based simulation only if the file doesn't exist yet.
    """
    import subprocess, time as _time
    sym = request.args.get("sym", "").upper()
    acct = request.args.get("acct", "trb")
    days = int(request.args.get("days", 180))
    force = request.args.get("force", "0") == "1"
    if not sym:
        return jsonify({"error": "sym required"}), 400

    # Primary: exact trades from the per_sym_20d_agent backtest
    trade_list_path = BASE_PATH / "data" / "hourly_reconfig" / acct / "_trade_lists" / f"{sym}_trades.json"
    if not force and trade_list_path.exists():
        try:
            data = json.loads(trade_list_path.read_text())
            trades = data.get("trades", [])
            # pnl_pct from per_sym_engine is percentage (e.g. 3.6), frontend expects fraction (0.036)
            for t in trades:
                if t.get("pnl_pct") is not None:
                    t["pnl_pct"] = t["pnl_pct"] / 100.0
                if t.get("pnl_gross_pct") is not None:
                    t["pnl_gross_pct"] = t["pnl_gross_pct"] / 100.0
            return jsonify({"sym": sym, "acct": acct, "source": "persym_agent",
                            "updated_at": data.get("updated_at"),
                            "window_days": data.get("window_days"),
                            "n_trades": len(trades),
                            "trades": trades})
        except Exception:
            pass

    # Fallback: SSH to S1 to run approximation script (cache 2h)
    cache_dir = BASE_PATH / "data" / "hourly_reconfig" / "_pending_review"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{sym}_BT_{acct}_trades_cache.json"
    if not force and cache_file.exists():
        age = _time.time() - cache_file.stat().st_mtime
        if age < 7200:
            try:
                return jsonify(json.loads(cache_file.read_text()))
            except Exception:
                pass

    # SSH to S1 and run approximation script
    remote_script = "/home/niels/binance/tools/gen_persym_bt_trades.py"
    python_bin = "/home/niels/.conda/envs/binance_env/bin/python"
    cmd = ["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", "s1-int",
           f"{python_bin} {remote_script} --sym {sym} --acct {acct} --days {days}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            return jsonify({"error": "ssh_failed", "stderr": result.stderr[:300], "trades": []})
        raw = result.stdout.strip()
        # Find the last JSON line (in case there's logging before it)
        json_line = next((ln for ln in reversed(raw.splitlines()) if ln.startswith("{")), None)
        if not json_line:
            return jsonify({"error": "no_json_output", "stdout": raw[:300], "trades": []})
        data = json.loads(json_line)
        cache_file.write_text(json.dumps(data))
        return jsonify(data)
    except subprocess.TimeoutExpired:
        return jsonify({"error": "ssh_timeout", "trades": []})
    except Exception as e:
        return jsonify({"error": str(e), "trades": []})


@app.route("/klines_cache")
def klines_cache():
    sym = request.args.get("sym", "").upper()
    tf = request.args.get("tf", "15m")
    max_bars = int(request.args.get("max", 5000))
    if not sym:
        return jsonify({"error": "sym required"}), 400
    candidates = [
        BASE_PATH / "klines_cache_backtest" / "tradier" / f"{sym}_{tf}.json",
        BASE_PATH / "klines_cache_backtest" / f"{sym}_{tf}.json",
        BASE_PATH / "klines_cache" / f"{sym}_{tf}.json",
    ]
    # 2026-06-02: prefer the FULL-HISTORY backtest klines (live klines_cache is short ~677 bars / stale,
    # so trades older than ~2 weeks had no candles to land on). Pick the existing candidate with the
    # MOST bars so the chart spans the whole trade date range and live/backtest markers render.
    existing = [p for p in candidates if p.exists()]
    path = max(existing, key=lambda p: p.stat().st_size) if existing else None
    if path is None:
        return jsonify({"error": f"no klines_cache for {sym} tf={tf}", "tried": [str(p) for p in candidates]}), 404
    try:
        rows = json.loads(path.read_text())
    except Exception as e:
        return jsonify({"error": f"parse {path}: {e}"}), 500
    out = []
    seen = set()
    for r in rows:
        ts_raw = r.get("timestamp") or r.get("close_time")
        if not ts_raw:
            continue
        try:
            unix_ts = int(datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        try:
            c = float(r.get("close"))
        except Exception:
            continue
        if c <= 0 or unix_ts in seen:
            continue
        seen.add(unix_ts)
        out.append({
            "t": unix_ts,
            "o": float(r.get("open", c)),
            "h": float(r.get("high", c)),
            "l": float(r.get("low", c)),
            "c": c,
            "v": float(r.get("volume", 0) or 0),
        })
    out.sort(key=lambda x: x["t"])
    if len(out) > max_bars:
        out = out[-max_bars:]
    return jsonify(out)


@app.route("/trb_categories")
def trb_categories():
    def _load_list(p):
        try:
            d = json.loads(Path(p).read_text())
            return d if isinstance(d, list) else list(d)
        except Exception:
            return []
    brief = {}
    try:
        brief = json.loads((BASE_PATH / "data" / "tv_morning_brief.json").read_text())
    except Exception:
        brief = {}
    daily_buys = [s.get("symbol") for s in brief.get("top_buys", []) if s.get("symbol")]
    daily_shorts = [s.get("symbol") for s in brief.get("top_shorts", []) if s.get("symbol")]
    all_longs = _load_list(BASE_PATH / "symbols_trb_long.json")
    all_shorts = _load_list(BASE_PATH / "symbols_trb_short.json")
    return jsonify({
        "categories": [
            {"id": "daily-buys", "label": "DAILY LONG BUYS", "side": "LONG", "symbols": daily_buys},
            {"id": "daily-shorts", "label": "DAILY SHORT SHORTS", "side": "SHORT", "symbols": daily_shorts},
            {"id": "all-longs", "label": "ALL TRB LONGS", "side": "LONG", "symbols": all_longs},
            {"id": "all-shorts", "label": "ALL TRB SHORTS", "side": "SHORT", "symbols": all_shorts},
        ],
        "generated_at": brief.get("generated_at", ""),
        "market_bias": brief.get("market_bias", ""),
    })


@app.route("/per_sym_symbols")
def per_sym_symbols():
    accts_raw = request.args.get("accounts", "trb,trc")
    accts = [a.strip() for a in accts_raw.split(",") if a.strip()]
    out: Dict[str, Any] = {}
    symbols: Dict[str, set] = {}
    for acct in accts:
        p = BASE_PATH / "data" / "hourly_reconfig" / acct / "active_config.json"
        keys: List[str] = []
        if p.exists():
            try:
                d = json.loads(p.read_text())
                keys = sorted(d.keys()) if isinstance(d, dict) else sorted(d)
            except Exception:
                keys = []
        out[acct] = keys
        for k in keys:
            if k.endswith("_LONG"):
                sym = k[:-5]
                side = "LONG"
            elif k.endswith("_SHORT"):
                sym = k[:-6]
                side = "SHORT"
            else:
                continue
            symbols.setdefault(sym, set()).add(side)
    merged = {sym: sorted(sides) for sym, sides in sorted(symbols.items())}
    return jsonify({"by_account": out, "symbols": merged})


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


# ============================================================
# TRB REVIEW PAGE — /trb_review
# ============================================================
# yfinance klines cache: {sym -> (expires_unix, bars_list)}
_yf_cache: Dict[str, Any] = {}
_YF_TTL = 300  # 5 minutes


def _parse_disk_klines(path: Path, interval: str = "5m") -> Dict[int, Dict]:
    """Parse a klines_cache JSON file into {unix_ts: bar} dict. Handles both
    tradier format (timestamp ISO string) and generic {t,o,h,l,c,v} format."""
    out: Dict[int, Dict] = {}
    try:
        rows = json.loads(path.read_text())
    except Exception:
        return out
    for r in rows:
        ts_raw = r.get("timestamp") or r.get("close_time") or r.get("t")
        if not ts_raw:
            continue
        try:
            if isinstance(ts_raw, (int, float)):
                unix_ts = int(ts_raw)
            else:
                unix_ts = int(datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        try:
            c = float(r.get("close") or r.get("c") or 0)
        except Exception:
            continue
        if c <= 0 or unix_ts in out:
            continue
        out[unix_ts] = {
            "t": unix_ts,
            "o": float(r.get("open") or r.get("o") or c),
            "h": float(r.get("high") or r.get("h") or c),
            "l": float(r.get("low") or r.get("l") or c),
            "c": c,
            "v": float(r.get("volume") or r.get("v") or 0),
        }
    return out


def _yf_klines(sym: str, interval: str = "5m") -> List[Dict]:
    """Fetch last 60d of OHLCV from yfinance (5-min in-memory TTL, ~20min delayed).
    Returns list of {t,o,h,l,c,v} dicts. 5m/15m yfinance cap = 60 days."""
    now = time.time()
    cache_key = f"{sym}_yf_{interval}"
    cached = _yf_cache.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]
    try:
        import yfinance as yf
        tk = yf.Ticker(sym)
        df = tk.history(period="60d", interval=interval, auto_adjust=True)
        if df is None or df.empty:
            _yf_cache[cache_key] = (now + 60, [])
            return []
        out = []
        for idx, row in df.iterrows():
            out.append({
                "t": int(idx.timestamp()),
                "o": round(float(row["Open"]), 4),
                "h": round(float(row["High"]), 4),
                "l": round(float(row["Low"]), 4),
                "c": round(float(row["Close"]), 4),
                "v": int(row.get("Volume", 0) or 0),
            })
        _yf_cache[cache_key] = (now + _YF_TTL, out)
        return out
    except Exception as e:
        print(f"[trb_review] yfinance {sym}: {e}", flush=True)
        _yf_cache[cache_key] = (now + 60, [])
        return []


def _merged_stock_klines(
    sym: str,
    interval: str = "5m",
    max_bars: int = 5000,
    start: int | None = None,
    end: int | None = None,
) -> Tuple[List[Dict], str]:
    """Merge all disk klines sources + yfinance into a single deduplicated series.

    Priority (all merged — later sources fill gaps in earlier):
      1. klines_cache/tradier/<SYM>_<tf>.json   — S1-synced, freshest (Jun 2026)
      2. klines_cache/<SYM>_<tf>.json           — S1 live sync (Mar 2026)
      3. klines_cache_backtest/tradier/<SYM>_<tf>.json  — backtest (May 2026)
      4. klines_cache_backtest/<SYM>_<tf>.json  — backtest flat
      5. yfinance                               — last 60d, ~20min delayed

    Merged bars give the widest time window without relying on any single source.
    """
    merged: Dict[int, Dict] = {}
    sources_used: List[str] = []
    disk_candidates = [
        BASE_PATH / "klines_cache" / "tradier" / f"{sym}_{interval}.json",
        BASE_PATH / "klines_cache" / f"{sym}_{interval}.json",
        BASE_PATH / "klines_cache_backtest" / "tradier" / f"{sym}_{interval}.json",
        BASE_PATH / "klines_cache_backtest" / f"{sym}_{interval}.json",
    ]
    for p in disk_candidates:
        if p.exists():
            bars = _parse_disk_klines(p, interval)
            if bars:
                merged.update(bars)
                sources_used.append(p.parts[-2] + "/" + p.name)
    yf_bars = _yf_klines(sym, interval)
    if yf_bars:
        for b in yf_bars:
            merged[b["t"]] = b
        sources_used.append("yfinance")
    out = sorted(
        (
            bar for bar in merged.values()
            if (start is None or int(bar["t"]) >= start)
            and (end is None or int(bar["t"]) <= end)
        ),
        key=lambda b: b["t"],
    )
    if len(out) > max_bars:
        out = out[-max_bars:]
    return out, "+".join(sources_used) if sources_used else "none"


def _is_stock_sym(sym: str) -> bool:
    """True if sym looks like a plain stock ticker (no USDT/USDC suffix, no option chain)."""
    if not sym:
        return False
    if sym.endswith("USDT") or sym.endswith("USDC"):
        return False
    # Filter out options like ABT260618C00097500
    import re
    if re.search(r'\d{6}[CP]\d{5}', sym):
        return False
    return True


def _trb_symbol_list() -> List[str]:
    """Union of symbols from history/trb/, symbols_trb_long/short.json, active_config (trb+trc)."""
    syms: set = set()
    history_dir = _history_dir_for("trb")
    if history_dir.exists():
        for p in history_dir.glob("*.jsonl"):
            parts = p.stem.rsplit("_", 1)
            if len(parts) == 2 and parts[1] in ("LONG", "SHORT"):
                sym = parts[0]
                if _is_stock_sym(sym):
                    syms.add(sym)
    for fname in ("symbols_trb_long.json", "symbols_trb_short.json"):
        try:
            lst = json.loads((BASE_PATH / fname).read_text())
            for s in lst:
                if _is_stock_sym(s):
                    syms.add(s)
        except Exception:
            pass
    for acct in ("trb", "trc"):
        ac_path = BASE_PATH / "data" / "hourly_reconfig" / acct / "active_config.json"
        if ac_path.exists():
            try:
                ac = json.loads(ac_path.read_text())
                for k in ac:
                    sym = k.rsplit("_", 1)[0]
                    if _is_stock_sym(sym):
                        syms.add(sym)
            except Exception:
                pass
    return sorted(syms)


def _per_sym_meta_for(acct: str) -> Dict[str, Dict]:
    """Return dict keyed by SYM_SIDE from active_config for an account."""
    ac_path = BASE_PATH / "data" / "hourly_reconfig" / acct / "active_config.json"
    if not ac_path.exists():
        return {}
    try:
        return json.loads(ac_path.read_text())
    except Exception:
        return {}


def _pending_review_trades(sym: str, side: str) -> List[Dict]:
    """Read _pending_review/<SYM>_<SIDE>_trades.jsonl if exists."""
    p = BASE_PATH / "data" / "hourly_reconfig" / "_pending_review" / f"{sym}_{side}_trades.jsonl"
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return out


@app.route("/trb_best_backtest_trades")
def trb_best_backtest_trades():
    """All exact trades from the best current c5 row for a stock symbol/side.

    Selection and ledger validation live in one read-only reporting adapter so
    the UI and all three mail surfaces use the same result lineage.
    """
    sym = request.args.get("sym", "").strip().upper()
    side = request.args.get("side", "").strip().upper()
    if not sym:
        return jsonify({"error": "sym required"}), 400
    if side and side not in {"LONG", "SHORT", "BOTH"}:
        return jsonify({"error": "side must be LONG, SHORT, or BOTH"}), 400
    try:
        payload = _fresh_current_matrix_reporting().read_best_trades(
            sym,
            None if side in {"", "BOTH"} else side,
            BASE_PATH,
        )
    except Exception as exc:
        return jsonify(
            {
                "error": "current matrix trade lookup failed",
                "detail": str(exc),
                "symbol": sym,
            }
        ), 500
    return jsonify(payload)


def _live_trade_summary(sym: str, acct: str) -> Dict:
    """Count open/close events for (sym, acct) and return last_ts, total_closes."""
    base = _history_dir_for(acct)
    n_events = 0
    n_closes = 0
    last_ts = 0
    for side in ("LONG", "SHORT"):
        p = base / f"{sym}_{side}.jsonl"
        if not p.exists():
            continue
        try:
            for line in p.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    n_events += 1
                    if ev.get("type") in ("CLOSE", "REDUCE"):
                        n_closes += 1
                    ts_str = ev.get("ts", "")
                    try:
                        t = int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp())
                        if t > last_ts:
                            last_ts = t
                    except Exception:
                        pass
                except Exception:
                    pass
        except Exception:
            pass
    return {"n_events": n_events, "n_closes": n_closes, "last_ts": last_ts}


@app.route("/trb_review.html")
def trb_review_page():
    # NOTE: legacy static SPA. The bare "/trb_review" URL is owned by the
    # dynamic table-based `trb_review()` view above (line ~278) — it was
    # ALSO registered here before 2026-07-06, which silently made this static
    # file unreachable at "/trb_review" (Flask/Werkzeug just picked whichever
    # rule matched first). Removed the duplicate registration; this legacy
    # page is now only reachable via the explicit ".html" suffix.
    return _no_cache(send_from_directory(app.static_folder, "trb_review.html"))


@app.route("/trb_symbols_overview")
def trb_symbols_overview():
    """Return all trb symbols with per_sym metrics + live trade summaries + direction classification.
    Called every 5 minutes by the trb_review page to keep data fresh."""
    trb_meta = _per_sym_meta_for("trb")
    trc_meta = _per_sym_meta_for("trc")
    best_matrix = {
        row["key"]: row
        for row in _fresh_current_matrix_reporting().best_rows(BASE_PATH)
    }
    all_syms = _trb_symbol_list()
    # Load long/short candidate lists for direction tabs
    def _load_list(fname: str) -> set:
        try:
            return set(json.loads((BASE_PATH / fname).read_text()))
        except Exception:
            return set()
    long_candidates = _load_list("symbols_trb_long.json")
    short_candidates = _load_list("symbols_trb_short.json")
    rows = []
    pending_dir = BASE_PATH / "data" / "hourly_reconfig" / "_pending_review"
    for sym in all_syms:
        trb_long = trb_meta.get(f"{sym}_LONG", {})
        trb_short = trb_meta.get(f"{sym}_SHORT", {})
        trc_long = trc_meta.get(f"{sym}_LONG", {})
        trc_short = trc_meta.get(f"{sym}_SHORT", {})
        trb_summary = _live_trade_summary(sym, "trb")
        trc_summary = _live_trade_summary(sym, "trc")
        pending = {}
        for side in ("LONG", "SHORT"):
            pfile = pending_dir / f"{sym}_{side}_trades.jsonl"
            if pfile.exists():
                try:
                    n = sum(1 for line in pfile.read_text().splitlines() if line.strip())
                    pending[side] = {"n_trades": n}
                except Exception:
                    pending[side] = {"n_trades": 0}
        rows.append({
            "sym": sym,
            "long_candidate": sym in long_candidates,
            "short_candidate": sym in short_candidates,
            "trb_long": {"wsharpe": trb_long.get("wsharpe"), "trades": trb_long.get("trades"), "winning_tag": trb_long.get("winning_tag"), "sample_tag": trb_long.get("sample_tag")},
            "trb_short": {"wsharpe": trb_short.get("wsharpe"), "trades": trb_short.get("trades"), "winning_tag": trb_short.get("winning_tag"), "sample_tag": trb_short.get("sample_tag")},
            "trc_long": {"wsharpe": trc_long.get("wsharpe"), "trades": trc_long.get("trades")},
            "trc_short": {"wsharpe": trc_short.get("wsharpe"), "trades": trc_short.get("trades")},
            "trb_live": trb_summary,
            "trc_live": trc_summary,
            "pending_review": pending,
            "best_matrix_long": best_matrix.get(f"{sym}_LONG"),
            "best_matrix_short": best_matrix.get(f"{sym}_SHORT"),
        })
    rows.sort(key=lambda r: -(r["trb_live"]["last_ts"] or 0))
    return jsonify({
        "symbols": rows,
        "generated_at": int(time.time()),
        "n": len(rows),
        "n_long_candidates": len(long_candidates),
        "n_short_candidates": len(short_candidates),
    })


@app.route("/trb_klines_live")
def trb_klines_live():
    """Merged stock klines from all disk sources + yfinance (5-min TTL).
    Sources tried in order: klines_cache/tradier/, klines_cache/, klines_cache_backtest/tradier/,
    klines_cache_backtest/, then yfinance for the last 60d (~20min delayed).
    All are merged and deduplicated to give maximum time coverage.
    ?sym=X&interval=5m&max=N&start=UNIX_OR_ISO&end=UNIX_OR_ISO"""
    sym = request.args.get("sym", "").upper()
    interval = request.args.get("interval", "5m")
    max_bars = int(request.args.get("max", 5000))
    start = _ts_to_unix(request.args.get("start"))
    end = _ts_to_unix(request.args.get("end"))
    if not sym:
        return jsonify({"error": "sym required"}), 400
    bars, source = _merged_stock_klines(
        sym, interval=interval, max_bars=max_bars, start=start, end=end
    )
    return jsonify({"bars": bars, "source": source, "sym": sym, "n": len(bars)})


@app.route("/trb_pending_trades")
def trb_pending_trades():
    """Per-sym backtest trade records from _pending_review JSONL.
    ?sym=X&side=LONG|SHORT|BOTH"""
    sym = request.args.get("sym", "").upper()
    side_filter = request.args.get("side", "BOTH").upper()
    if not sym:
        return jsonify({"error": "sym required"}), 400
    sides = ["LONG", "SHORT"] if side_filter in ("BOTH", "") else [side_filter]
    all_trades = []
    for side in sides:
        trades = _pending_review_trades(sym, side)
        for t in trades:
            t["_side"] = side
            all_trades.append(t)
    all_trades.sort(key=lambda t: int(t.get("entry_ts", 0)))
    return jsonify({"trades": all_trades, "n": len(all_trades), "sym": sym})


# ============================================================
# LIVE (past baseline) vs SANDBOX-BT (new per-sym config) per-symbol comparison
# ============================================================
# For each symbol that actually traded live in the last N days, compares:
#   LIVE  = realized /history/ trades (the config that was live then = "past baseline")
#   BT    = real per_sym engine (simulate_dual_stocks) over the SAME N-day window using
#           the CURRENT active_config overrides (= "new sandbox baseline now running"),
#           run on S1 where the NPZ live. Symbols without a per-sym override fall back to
#           engine-default params (≈ the global config_tradier baseline) — flagged src=default.
# HONESTY (CLAUDE.md NO-LIES): per-symbol sharpe is n_syms=1 → DIAGNOSTIC ONLY, never a
# promotion signal. Live typically trades 1-3x/mo (no-loss few-exit policy) so its sharpe is
# noise (<30 trades) — the solid live signal is realized $. The headline metric is usually
# BT churn (trades/mo) and per-trade edge (avg%/trade, sharpe over n>=30).
import threading as _threading

_LVB_LOCK = _threading.Lock()
_LVB_JOBS: Dict[str, bool] = {}
_LVB_TTL = 6 * 3600
_LVB_DAYS = 35


def _lvb_cache_path(acct: str, scope: str = "all") -> Path:
    d = BASE_PATH / "data" / "hourly_reconfig" / "_review"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"live_vs_bt_{acct}_{scope}.json"


def _ret_stats(returns_pct: List[float]) -> Dict[str, Any]:
    """Stats over a list of per-trade returns expressed in PERCENT."""
    n = len(returns_pct)
    if n == 0:
        return {"n": 0, "win": None, "avg": None, "sharpe": None, "tot": None}
    wins = sum(1 for r in returns_pct if r > 0)
    avg = sum(returns_pct) / n
    sd = (sum((r - avg) ** 2 for r in returns_pct) / n) ** 0.5 if n > 1 else 0.0
    sharpe = (avg / sd) if sd > 1e-9 else 0.0
    return {"n": n, "win": 100.0 * wins / n, "avg": avg, "sharpe": sharpe, "tot": sum(returns_pct)}


def _live_closed_in_window(acct: str, days: float) -> Dict[str, Dict[str, Any]]:
    """{sym: {rets:[pct], usd}} for closed live trades whose exit is within `days`."""
    cut = time.time() - days * 86400
    base = _history_dir_for(acct)
    out: Dict[str, Dict[str, Any]] = {}
    if not base.exists():
        return out
    for p in base.glob("*.jsonl"):
        stem = p.stem
        if not (stem.endswith("_LONG") or stem.endswith("_SHORT")):
            continue
        sym, side = stem.rsplit("_", 1)
        if not _is_stock_sym(sym):
            continue
        events: List[Dict[str, Any]] = []
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            ev["side"] = side
            ev["account"] = acct
            ev["symbol"] = sym
            try:
                ev["unix_ts"] = int(datetime.fromisoformat(str(ev.get("ts")).replace("Z", "+00:00")).timestamp())
            except Exception:
                ev["unix_ts"] = 0
            events.append(ev)
        events.sort(key=lambda e: e.get("unix_ts", 0))
        for t in _reconstruct_trades_from_events(events):
            if (t.get("exit_ts") or 0) < cut or t.get("pnl_pct") is None:
                continue
            rec = out.setdefault(sym, {"rets": [], "usd": 0.0})
            rec["rets"].append(t["pnl_pct"])
            rec["usd"] += (t.get("pnl_usd") or 0.0)
    return out


def _bt_batch_s1(acct: str, syms: List[str], days: float) -> Dict[str, Dict[str, Any]]:
    """One SSH to S1 → persym_bt_batch.py (engine loaded once, COMPACT output).
    The old per-symbol gen loop overflowed SSH stdout at ~100-sym scale (full trade
    lists) and silently dropped most symbols. {sym: stats + rets(%) + src + window}."""
    import subprocess
    syms = [s for s in syms if s and s.replace(".", "").isalnum()]
    if not syms:
        return {}
    py = "/home/niels/.conda/envs/binance_env/bin/python"
    remote = (f"cd /home/niels/binance && {py} tools/persym_bt_batch.py "
              f"--acct {acct} --days {days} --syms {','.join(syms)} 2>/dev/null")
    out: Dict[str, Dict[str, Any]] = {}
    try:
        res = subprocess.run(["ssh", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", "s1-int", remote],
                             capture_output=True, text=True, timeout=1800)
    except Exception as e:
        return {"_error": str(e)}
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        rets = [(r or 0) * 100.0 for r in d.get("rets", [])]  # fraction -> percent
        st = _ret_stats(rets)
        st["window"] = days
        st["rets"] = rets
        st["src"] = d.get("src")
        out[d.get("sym")] = st
    return out


def _candidate_syms(acct: str) -> List[str]:
    """Union of symbols_<acct>_long.json + symbols_<acct>_short.json (the full traded universe)."""
    syms: set = set()
    for side in ("long", "short"):
        p = BASE_PATH / f"symbols_{acct}_{side}.json"
        if not p.exists():
            continue
        try:
            for s in json.loads(p.read_text()):
                if isinstance(s, str) and _is_stock_sym(s):
                    syms.add(s)
        except Exception:
            pass
    return sorted(syms)


def _pool_sharpe(returns: List[float]) -> Optional[float]:
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    sd = (sum((r - mean) ** 2 for r in returns) / len(returns)) ** 0.5
    return (mean / sd) if sd > 1e-9 else 0.0


def _override_source(acct: str, sym: str) -> str:
    ac = _per_sym_meta_for(acct)
    le = ac.get(f"{sym}_LONG", {})
    se = ac.get(f"{sym}_SHORT", {})
    lo = le.get("overrides", {}) if isinstance(le, dict) else {}
    so = se.get("overrides", {}) if isinstance(se, dict) else {}
    return "persym" if (lo or so) else "default"


def _claimed_persym(acct: str, sym: str) -> Dict[str, Any]:
    """The per_sym agent's CLAIMED winner metrics for this symbol (best side), from
    active_config. These are what the sandbox reports as 'best' — often on tiny samples."""
    ac = _per_sym_meta_for(acct)
    best = {"wsharpe": None, "trades": None}
    for side in ("LONG", "SHORT"):
        e = ac.get(f"{sym}_{side}", {})
        if not isinstance(e, dict):
            continue
        ws = e.get("wsharpe")
        if ws is None:
            continue
        if best["wsharpe"] is None or ws > best["wsharpe"]:
            best = {"wsharpe": ws, "trades": e.get("trades")}
    return best


def _build_live_vs_bt(acct: str, days: float = _LVB_DAYS, scope: str = "all") -> Dict[str, Any]:
    live = _live_closed_in_window(acct, days)
    if scope == "live":
        syms = sorted(live.keys())
    else:
        # FULL universe: every symbols_<acct>_long/short candidate ∪ anything traded live.
        syms = sorted(set(_candidate_syms(acct)) | set(live.keys()))
    bt = _bt_batch_s1(acct, syms, days)
    bt_err = bt.get("_error")
    pooled_bt: List[float] = []
    pooled_live: List[float] = []
    rows = []
    agg = {"bt_better": 0, "live_better": 0, "same": 0, "bt_diag": 0, "insufficient": 0}
    agg_keep = {"keep": 0, "ditch": 0, "nodata": 0}
    live_usd_total = 0.0
    for sym in syms:
        live.setdefault(sym, {"rets": [], "usd": 0.0})
        L = _ret_stats(live[sym]["rets"])
        Lusd = live[sym]["usd"]
        live_usd_total += Lusd
        B = bt.get(sym, {"n": 0, "win": None, "avg": None, "sharpe": None, "tot": None, "window": days})
        src = B.get("src") or _override_source(acct, sym)
        verdict = "insufficient"
        note = ""
        if L["n"] >= 5 and B["n"] >= 30 and L["sharpe"] is not None:
            ds = B["sharpe"] - L["sharpe"]
            verdict = "bt_better" if ds > 0.05 else "live_better" if ds < -0.05 else "same"
        elif B["n"] >= 30:
            verdict = "bt_diag"
            note = f"live n={L['n']} too small for sharpe — judge on realized ${Lusd:.0f} + BT churn"
        else:
            note = f"both samples small (live n={L['n']}, bt n={B['n']})"
        agg[verdict] = agg.get(verdict, 0) + 1
        pooled_bt += bt.get(sym, {}).get("rets", [])
        pooled_live += [r * 100.0 for r in live[sym]["rets"]]
        claimed = _claimed_persym(acct, sym)
        # KEEP/DITCH per USER policy: a config is only worth keeping if its REAL backtest
        # sharpe clears 0.5 (baseline 0.58) on a real sample (>=30 trades). Tiny-sample or
        # sub-0.5 = ditch. claimed wsharpe shown alongside to expose sandbox inflation.
        if B["sharpe"] is None or B["n"] < 30:
            keep = "nodata"
        elif B["sharpe"] >= 0.5:
            keep = "keep"
        else:
            keep = "ditch"
        agg_keep[keep] = agg_keep.get(keep, 0) + 1
        rows.append({
            "sym": sym, "src": src,
            "live": {"n": L["n"], "win": L["win"], "avg": L["avg"], "sharpe": L["sharpe"], "usd": Lusd},
            "bt": {"n": B["n"], "win": B.get("win"), "avg": B.get("avg"), "sharpe": B.get("sharpe"),
                   "tot": B.get("tot"), "window": B.get("window")},
            "claimed": claimed,
            "churn": (B["n"] / L["n"]) if (L["n"] and B["n"]) else None,
            "verdict": verdict, "keep": keep, "note": note,
        })
    rows.sort(key=lambda r: -((r["bt"]["n"] or 0)))
    n_traded = sum(1 for r in rows if r["live"]["n"] > 0)
    n_bt = sum(1 for r in rows if r["bt"]["n"] > 0)
    payload = {"acct": acct, "days": days, "scope": scope,
               "generated_at": int(time.time()), "n": len(rows), "n_traded": n_traded, "n_bt": n_bt,
               "summary": agg, "keep_summary": agg_keep, "live_usd_total": round(live_usd_total, 2),
               "pool_sharpe_bt": _pool_sharpe(pooled_bt), "pool_trades_bt": len(pooled_bt),
               "pool_sharpe_live": _pool_sharpe(pooled_live), "pool_trades_live": len(pooled_live),
               "bt_error": bt_err, "rows": rows}
    try:
        _lvb_cache_path(acct, scope).write_text(json.dumps(payload))
    except Exception:
        pass
    return payload


@app.route("/trb_live_vs_bt")
def trb_live_vs_bt():
    """Serve cached live-vs-sandbox-BT comparison; regen in background if stale/forced.
    ?acct=trb|trc&force=1"""
    acct = request.args.get("acct", "trb")
    scope = request.args.get("scope", "all")
    if scope not in ("all", "live"):
        scope = "all"
    force = request.args.get("force", "0") == "1"
    jobkey = f"{acct}:{scope}"
    cache = None
    path = _lvb_cache_path(acct, scope)
    if path.exists():
        try:
            cache = json.loads(path.read_text())
        except Exception:
            cache = None
    fresh = bool(cache) and (time.time() - cache.get("generated_at", 0) < _LVB_TTL)
    running = _LVB_JOBS.get(jobkey, False)
    if fresh and not force:
        return jsonify({"status": "ready", "running": running, "data": cache})

    def _job(a=acct, sc=scope, jk=jobkey):
        try:
            _build_live_vs_bt(a, scope=sc)
        finally:
            _LVB_JOBS[jk] = False
    # Single-flight GLOBALLY: only one S1 BT batch at a time. Running two concurrent
    # 100-symbol batches on a memory-starved S1 (sweeps + ~200MB free) silently OOMs
    # most per-symbol runs → lost coverage. Serialize so each batch gets clean results.
    msg = "generating"
    with _LVB_LOCK:
        any_running = any(_LVB_JOBS.values())
        if _LVB_JOBS.get(jobkey):
            running = True
        elif any_running:
            running = True
            msg = "queued"  # another acct/scope batch is running; this one waits
        else:
            _LVB_JOBS[jobkey] = True
            _threading.Thread(target=_job, daemon=True).start()
            running = True
    return jsonify({"status": msg, "running": running, "data": cache})


@app.route("/trb_vs_trc_data")
def trb_vs_trc_data():
    """trb(per_sym) vs trc(7D) live A/B: latest snapshot + full time series from the tracker CSV."""
    csv_path = BASE_PATH / "data" / "reports" / "trb_vs_trc_timeseries.csv"
    start_path = BASE_PATH / "data" / "reports" / "trb_vs_trc_start.json"
    series, latest = [], {"trb": {}, "trc": {}}
    rows_by_snap: Dict[str, Dict[str, Any]] = {}
    if csv_path.exists():
        lines = csv_path.read_text().splitlines()
        hdr = lines[0].split(",") if lines else []
        for ln in lines[1:]:
            parts = ln.split(",")
            if len(parts) < len(hdr):
                continue
            row = dict(zip(hdr, parts))
            snap, acct = row.get("snapshot_utc"), row.get("acct")
            def fnum(v):
                try:
                    return float(v)
                except Exception:
                    return None
            rec = {"trades": fnum(row.get("trades")), "win_pct": fnum(row.get("win_pct")),
                   "cumul_pct": fnum(row.get("cumul_pct")), "sharpe": fnum(row.get("sharpe")),
                   "max_dd_pct": fnum(row.get("max_dd_pct")), "usd": fnum(row.get("realized_usd")),
                   "avg_pct": fnum(row.get("avg_pct"))}
            latest[acct] = rec
            rows_by_snap.setdefault(snap, {})[acct] = rec.get("cumul_pct")
            rows_by_snap[snap][acct + "_sharpe"] = rec.get("sharpe")
        for snap in sorted(rows_by_snap):
            d = rows_by_snap[snap]
            series.append({"t": snap, "trb": d.get("trb"), "trc": d.get("trc"),
                           "trb_sharpe": d.get("trb_sharpe"), "trc_sharpe": d.get("trc_sharpe")})
    start = {}
    if start_path.exists():
        try:
            start = json.loads(start_path.read_text())
        except Exception:
            start = {}
    return jsonify({"latest": latest, "series": series, "start": start.get("start_iso", "")})


@app.route("/trb_vs_trc")
def trb_vs_trc_page():
    html = """<!DOCTYPE html><html><head><meta charset=utf-8><title>trb vs trc — 7D A/B</title>
<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>body{background:#0c0e12;color:#e6e8eb;font-family:-apple-system,system-ui,sans-serif;margin:0;padding:14px}
h1{font-size:15px}.sub{color:#6a7383;font-size:11px;margin-bottom:10px}
table{border-collapse:collapse;font-size:12px;margin-bottom:14px}th,td{padding:5px 12px;border-bottom:1px solid #1c2029;text-align:right}
th{color:#6a7383;font-size:10px;text-transform:uppercase}td.l,th.l{text-align:left}
.pos{color:#5a9060}.neg{color:#a04040}.trb{color:#7fb069}.trc{color:#4a90e2}
#chart{height:340px;border:1px solid #1c2029;border-radius:5px}</style></head><body>
<h1>⚖ trb (per_sym 4yr) vs trc (7D recency) — live A/B</h1>
<div class=sub id=sub>loading…</div>
<table id=tbl></table>
<div id=chart></div>
<div class=sub style="margin-top:8px">Verdict metric = per-trade return + pool_sharpe (fair; $ differs by account size — trb $70k vs trc $30k). Backtest said 7D is recency-overfit (4yr per_sym≥7D); this is the forward check. Refreshes every 60s; tracker snapshots hourly.</div>
<script>
const BASE=location.origin;
function n(x,d){return x==null?"—":(x>=0&&d>0?"+":"")+(+x).toFixed(d)}
async function load(){
 const r=await fetch(BASE+"/trb_vs_trc_data").then(x=>x.json()).catch(()=>null); if(!r)return;
 const b=r.latest.trb||{},c=r.latest.trc||{};
 document.getElementById("sub").textContent=`A/B start ${r.start||"?"} · ${r.series.length} snapshots`;
 const rows=[["trades","trades",0],["win %","win_pct",1],["avg %/trade","avg_pct",3],["cumulative %","cumul_pct",3],["pool_sharpe","sharpe",4],["max drawdown %","max_dd_pct",2],["realized $","usd",0]];
 let h=`<tr><th class=l>metric</th><th class=trb>trb · per_sym</th><th class=trc>trc · 7D</th><th>7D ahead?</th></tr>`;
 for(const[label,k,d]of rows){const vb=b[k],vc=c[k];let win="";if(typeof vb=="number"&&typeof vc=="number"){const better=(k=="max_dd_pct")?(vc>vb):(vc>vb);win=vc==vb?"=":(better?"<span class=trc>✓ 7D</span>":"<span class=trb>✗</span>")}
  h+=`<tr><td class=l>${label}</td><td>${n(vb,d)}</td><td>${n(vc,d)}</td><td>${win}</td></tr>`}
 document.getElementById("tbl").innerHTML=h;
 if(!window._ch){window._ch=LightweightCharts.createChart(document.getElementById("chart"),{layout:{background:{color:"#0c0e12"},textColor:"#5a6470"},grid:{vertLines:{color:"#0f1215"},horzLines:{color:"#0f1215"}},rightPriceScale:{borderColor:"#1c2029"},timeScale:{borderColor:"#1c2029",timeVisible:true}});window._tb=window._ch.addLineSeries({color:"#7fb069",lineWidth:2,title:"trb per_sym cumul%"});window._tc=window._ch.addLineSeries({color:"#4a90e2",lineWidth:2,title:"trc 7D cumul%"});}
 const toT=s=>Math.floor(new Date(s).getTime()/1000);
 const seen=new Set();const tb=[],tc=[];
 for(const p of r.series){const t=toT(p.t);if(seen.has(t))continue;seen.add(t);if(p.trb!=null)tb.push({time:t,value:p.trb});if(p.trc!=null)tc.push({time:t,value:p.trc});}
 if(tb.length)window._tb.setData(tb);if(tc.length)window._tc.setData(tc);if(tb.length||tc.length)window._ch.timeScale().fitContent();
}
load();setInterval(load,60000);
</script></body></html>"""
    return _no_cache(app.response_class(html, mimetype="text/html"))


if __name__ == "__main__":
    port = int(os.environ.get("CHART_PORT", 5077))
    print(f"Chart server on http://127.0.0.1:{port}")
    _load_cache_from_disk()
    import threading
    threading.Thread(target=_precompute_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
