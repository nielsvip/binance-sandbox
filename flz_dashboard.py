#!/usr/bin/env python3
"""flz_dashboard — port 5057.

LIVE TRADES DASHBOARD: every number on this page comes from real
data/history/<acct>/<SYMBOL>_<SIDE>.jsonl trade events. Decisions JSONLs
(data/decisions/) are NOT read — they are decision logs (every-cycle position
recommendations), not actual trade outcomes, and would be a lying source for
P&L / win-rate / sharpe.

Data sources (the only ones):
  - data/history/<acct>/<SYMBOL>_<SIDE>.jsonl   (the LIVE trade event log,
                                                 OPEN/AUGMENT/REDUCE/CLOSE)
  - data/autonomous/<pool>/<wXXX>/autonomous_*_winners.jsonl   (sweep iters
                                                 — for the backtest leaderboard
                                                 only; no live numbers come
                                                 from here)
  - data/hourly_reconfig/<acct>/opinions.json   (the 7D-agent's per-symbol
                                                 LONG/SHORT/HOLD recommendation
                                                 — display only, not numbers)

Every metric routed through metrics_guard. Sub-floor results tagged
[DIAGNOSTIC]; inflated (|sharpe|>5 with trades<5000) flagged. Read-only.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request, redirect

import metrics_guard

BASE_DIR = Path(__file__).resolve().parent
AUTONOMOUS_DIR = BASE_DIR / "data" / "autonomous"
HISTORY_DIR = BASE_DIR / "data" / "history"
# data/history/<acct> is the canonical LIVE trade-event log for ALL accounts.
# trb/trc/tra are symlinks into data/tradier/history/<acct> — stocks history
# resolves transparently through the same path. NEVER read from data/decisions/
# for numbers: that is a position-recommendation log, not a trade outcome.
QUARANTINE_DIR = BASE_DIR / "data" / "_legacy_unverified"

CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc", "tra"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS

# Heuristic: parse n_syms from directory name like "tradier_2p4860_114sym"
_NSYM_RE = re.compile(r"_(\d+)sym")
# Heuristic: parse mode from directory name (crypto / tradier)
_MODE_CRYPTO_RE = re.compile(r"^crypto[_/]", re.IGNORECASE)
_MODE_TRADIER_RE = re.compile(r"^tradier[_/]", re.IGNORECASE)

# Cache: refresh every 1 hour per user spec
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SEC = int(os.environ.get("FLZ_DASHBOARD_CACHE_TTL", "3600"))


def _cache_get(key: str):
    rec = _CACHE.get(key)
    if not rec:
        return None
    ts, val = rec
    if time.time() - ts > _CACHE_TTL_SEC:
        return None
    return val


def _cache_set(key: str, val):
    _CACHE[key] = (time.time(), val)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────
# Iteration discovery + audit
# ─────────────────────────────────────────────────────────────────────────

def _infer_mode_from_path(p: Path) -> str:
    s = str(p)
    if "tradier" in s.lower():
        return "tradier"
    if "crypto" in s.lower():
        return "crypto"
    return "unknown"


def _infer_n_syms(p: Path, mode: str, override_n: Optional[int] = None) -> Optional[int]:
    """n_syms hint from directory name (e.g. crypto_2p6365_50sym → 50). None if absent."""
    if override_n:
        return int(override_n)
    for part in p.parts:
        m = _NSYM_RE.search(part)
        if m:
            return int(m.group(1))
    return None


def _infer_years(start_iso: str = "2022-01-01") -> float:
    try:
        start = datetime.fromisoformat(start_iso).replace(tzinfo=timezone.utc)
    except Exception:
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - start).total_seconds() / (86400.0 * 365.25)


def _audit_iter(row: Dict[str, Any], n_syms_hint: Optional[int],
                years_hint: float, mode: str) -> Dict[str, Any]:
    """Apply the canonical audit per metrics_guard / CLAUDE.md rules.

    Returns row enriched with:
      - n_syms / years (from hints if absent)
      - tier (Discard / Noise / Directional / Best-of-current / Strong / Aspirational)
      - inflated (bool)  — pool_sharpe / sym_sharpe absolute > 5 with trades < 5000
      - sample_floor_ok (bool)
      - publishable (bool) — meets sample floor AND not inflated
      - tags (list of str)
    """
    out = dict(row)
    pool_sharpe = float(out.get("pool_sharpe", 0) or 0)
    sym_sharpe = float(out.get("sym_sharpe", 0) or 0)
    trades = int(out.get("trades", 0) or 0)
    n_syms = out.get("n_syms")
    if n_syms is None:
        n_syms = n_syms_hint
    years = out.get("years")
    if years is None:
        years = years_hint
    out["n_syms"] = n_syms or 0
    out["years"] = float(years or 0)

    floor_syms = metrics_guard.MIN_SYMS_STOCKS if mode == "tradier" else metrics_guard.MIN_SYMS_CRYPTO
    min_trades_per_sym = metrics_guard.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE  # 30
    trades_per_sym = (trades / out["n_syms"]) if out["n_syms"] else 0
    syms_ok = out["n_syms"] >= floor_syms
    years_ok = out["years"] >= metrics_guard.MIN_YEARS
    density_ok = trades_per_sym >= min_trades_per_sym
    sample_floor_ok = syms_ok and years_ok and density_ok
    inflated_pool = abs(pool_sharpe) > metrics_guard.PER_SYM_SHARPE_CAP and trades < 5000
    inflated_sym = abs(sym_sharpe) > metrics_guard.PER_SYM_SHARPE_CAP and trades < 5000
    inflated = inflated_pool or inflated_sym

    out["sample_floor_ok"] = sample_floor_ok
    out["trades_per_sym"] = round(trades_per_sym, 2)
    out["inflated"] = inflated
    out["inflated_pool"] = inflated_pool
    out["inflated_sym"] = inflated_sym
    out["tier"] = metrics_guard.tier_name(pool_sharpe)
    tags: List[str] = []
    if not syms_ok:
        tags.append(f"SYMS_LOW (n_syms={out['n_syms']}/{floor_syms})")
    if not years_ok:
        tags.append(f"YEARS_LOW ({out['years']:.2f}y/{metrics_guard.MIN_YEARS}y)")
    if not density_ok:
        tags.append(f"DENSITY_LOW ({trades_per_sym:.1f} trades/sym, need ≥{min_trades_per_sym})")
    if not sample_floor_ok:
        tags.append("DIAGNOSTIC")
    if inflated_pool:
        tags.append(f"INFLATED_POOL ({pool_sharpe:.2f} >5 with {trades} trades)")
    if inflated_sym:
        tags.append(f"INFLATED_SYM ({sym_sharpe:.2f} >5 with {trades} trades)")
    # Publishable = sample floor + not inflated + pool_sharpe >= 0.7 (per user directive:
    # "run baseline >0.7 for all stocks and crypto as a generalized setting").
    sharpe_ok = pool_sharpe >= 0.7
    if not sharpe_ok:
        tags.append(f"BELOW_FLOOR (pool_sharpe={pool_sharpe:.4f} <0.7)")
    out["publishable"] = sample_floor_ok and (not inflated_pool) and sharpe_ok
    out["tags"] = tags
    out["mode"] = mode
    return out


def _read_jsonl(p: Path, mode: str, n_syms_hint: Optional[int],
                years_hint: float) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            rec["_source_path"] = str(p.relative_to(BASE_DIR)) if p.is_relative_to(BASE_DIR) else str(p)
            rec["_pool"] = p.parent.parent.name if p.parent.parent != AUTONOMOUS_DIR else p.parent.name
            rec["_worker"] = p.parent.name
            rows.append(_audit_iter(rec, n_syms_hint, years_hint, mode))
    except Exception:
        pass
    return rows


def discover_iters() -> List[Dict[str, Any]]:
    """Walk autonomous_*_winners.jsonl on MB local. Skip _legacy_unverified."""
    cached = _cache_get("iters")
    if cached is not None:
        return cached
    out: List[Dict[str, Any]] = []
    if not AUTONOMOUS_DIR.exists():
        _cache_set("iters", out)
        return out
    years_hint = _infer_years("2022-01-01")
    for p in AUTONOMOUS_DIR.rglob("autonomous_*_winners.jsonl"):
        s = str(p)
        if "_legacy_unverified" in s or "_NOLIES_HOLD_" in s:
            continue
        mode = _infer_mode_from_path(p)
        n_syms = _infer_n_syms(p, mode)
        out.extend(_read_jsonl(p, mode, n_syms, years_hint))
    _cache_set("iters", out)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Top-10 + diagnostic tail
# ─────────────────────────────────────────────────────────────────────────

def top_10(mode_filter: Optional[str] = None) -> Dict[str, Any]:
    """Return top-10 publishable iterations (pool_sharpe descending) + a
    diagnostic-tail of sub-floor / inflated rows excluded from top-10."""
    iters = discover_iters()
    if mode_filter in ("crypto", "tradier"):
        iters = [r for r in iters if r.get("mode") == mode_filter]
    publishable = [r for r in iters if r.get("publishable")]
    publishable.sort(key=lambda r: float(r.get("pool_sharpe", 0)), reverse=True)
    diagnostic = [r for r in iters if not r.get("publishable")]
    diagnostic.sort(key=lambda r: float(r.get("pool_sharpe", 0)), reverse=True)
    top = publishable[:10]
    summary = {
        "n_iters_loaded": len(iters),
        "n_publishable": len(publishable),
        "n_diagnostic": len(diagnostic),
        "by_mode": {},
        "generated_utc": _now_iso(),
        "next_refresh_utc": (datetime.now(timezone.utc) + timedelta(seconds=_CACHE_TTL_SEC)).isoformat(),
        "cache_ttl_sec": _CACHE_TTL_SEC,
    }
    for r in iters:
        summary["by_mode"].setdefault(r.get("mode", "?"), 0)
        summary["by_mode"][r.get("mode", "?")] += 1
    return {
        "summary": summary,
        "top_10": top,
        "diagnostic_tail": diagnostic[:50],
    }


# ─────────────────────────────────────────────────────────────────────────
# Live trades — closed rounds reconstruction
# ─────────────────────────────────────────────────────────────────────────

def _reconstruct_trades(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Walk OPEN/AUGMENT/REDUCE/CLOSE events into closed rounds keyed by
    (symbol, side). Per-symbol-per-side keying is required: an account holds
    many symbols simultaneously and earlier code keyed only by side, which
    collided BTC AUGMENT with ETH REDUCE and produced 0 closed rounds despite
    12k events. Pseudo-close rule: a REDUCE that takes residual qty below 0.5%
    of running entry-weighted qty closes the round (real exchanges leave dust)."""
    rounds: Dict[Tuple[str, str], Optional[Dict[str, Any]]] = {}
    closed: List[Dict[str, Any]] = []
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
        if kind in ("OPEN", "AUGMENT"):
            if rd is None or rd.get("qty", 0) <= 0:
                rd = {"side": side, "account": ev.get("account"), "symbol": sym,
                      "entry_ts": ts, "entry_price": price, "qty": qty,
                      "peak_qty": qty,
                      "entry_reason": ev.get("reason", "")}
                rounds[key] = rd
            else:
                new_qty = rd["qty"] + qty
                rd["entry_price"] = (rd["entry_price"] * rd["qty"] + price * qty) / new_qty
                rd["qty"] = new_qty
                rd["peak_qty"] = max(rd.get("peak_qty", 0), new_qty)
        elif kind in ("REDUCE", "CLOSE"):
            if rd is None or rd.get("qty", 0) <= 0:
                continue
            close_qty = min(qty, rd["qty"])
            if side == "LONG":
                pnl_pct = (price - rd["entry_price"]) / rd["entry_price"] * 100.0
            else:
                pnl_pct = (rd["entry_price"] - price) / rd["entry_price"] * 100.0
            rd["qty"] -= close_qty
            dust_threshold = max(1e-9, rd.get("peak_qty", 0) * 0.005)
            if rd["qty"] <= dust_threshold or kind == "CLOSE":
                closed.append({
                    "account": rd["account"], "symbol": rd.get("symbol", ""), "side": side,
                    "entry_ts": rd["entry_ts"], "entry_price": rd["entry_price"],
                    "exit_ts": ts, "exit_price": price, "pnl_pct": pnl_pct,
                    "duration_sec": ts - rd["entry_ts"],
                    "entry_reason": rd["entry_reason"], "exit_reason": ev.get("reason", ""),
                })
                rounds[key] = None
    return closed


def _load_account_history(account: str) -> List[Dict[str, Any]]:
    """Load every LIVE trade event for the account.

    Source: data/history/<acct>/*.jsonl ONLY. For trb/trc/tra this resolves
    through symlink into data/tradier/history/<acct>. Never reads decisions/."""
    base = HISTORY_DIR / account
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


def _account_canonical_stats(account: str) -> Dict[str, Any]:
    """Compute canonical 9-field metric set for account across ALL symbols."""
    cache_key = f"acct_stats_{account}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    events = _load_account_history(account)
    closed = _reconstruct_trades(events)
    if not closed:
        result = {
            "account": account, "trades": 0, "n_syms": 0, "years": 0.0,
            "pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0,
            "gain_per_yr": 0.0, "gain_sym_yr": 0.0, "max_dd_pct": 0.0,
            "tier": "Noise", "tags": ["NO_TRADES"], "publishable": False,
        }
        _cache_set(cache_key, result)
        return result
    # Group by symbol for sym_sharpe
    by_sym: Dict[str, List[float]] = defaultdict(list)
    for t in closed:
        sym = t.get("symbol") or "?"
        by_sym[sym].append(float(t.get("pnl_pct", 0) or 0))
    # Time window
    ts_list = sorted([int(t.get("entry_ts", 0)) for t in closed if t.get("entry_ts")])
    ex_list = sorted([int(t.get("exit_ts", 0)) for t in closed if t.get("exit_ts")])
    years = max(0.01, (ex_list[-1] - ts_list[0]) / (86400.0 * 365.25)) if ts_list and ex_list else 0.01
    metrics = metrics_guard.standard_metric_set(by_sym, years)
    # Drawdown — pool equity curve from all returns in chronological order
    chrono = sorted(closed, key=lambda t: int(t.get("exit_ts", 0)))
    eq, cum = [], 0.0
    for t in chrono:
        cum += float(t.get("pnl_pct", 0) or 0)
        eq.append(cum)
    peak = -1e18; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        if peak - v > max_dd: max_dd = peak - v
    metrics["max_dd_pct"] = round(max_dd, 4)
    pool_sharpe = float(metrics["pool_sharpe"])
    n_syms = int(metrics["n_syms"])
    trades = int(metrics["trades"])
    is_stock = account in STOCK_ACCOUNTS
    floor_syms = metrics_guard.MIN_SYMS_STOCKS if is_stock else metrics_guard.MIN_SYMS_CRYPTO
    sample_floor_ok = (n_syms >= floor_syms) and (years >= metrics_guard.MIN_YEARS)
    inflated_pool = abs(pool_sharpe) > metrics_guard.PER_SYM_SHARPE_CAP and trades < 5000
    tags = []
    if not sample_floor_ok:
        tags.append(f"DIAGNOSTIC (n_syms={n_syms}/{floor_syms} · {years:.2f}y)")
    if inflated_pool:
        tags.append(f"INFLATED_POOL ({pool_sharpe:.2f} >5 with {trades} trades)")
    result = {
        "account": account,
        "trades": trades,
        "n_syms": n_syms,
        "years": round(years, 4),
        "pool_sharpe": round(pool_sharpe, 4),
        "sym_sharpe": round(float(metrics["sym_sharpe"]), 4),
        "avg_gain_trade": round(float(metrics["avg_gain_trade"]), 4),
        "gain_per_yr": round(float(metrics["gain_per_yr"]), 2),
        "gain_sym_yr": round(float(metrics["gain_sym_yr"]), 4),
        "max_dd_pct": round(max_dd, 4),
        "tier": metrics_guard.tier_name(pool_sharpe),
        "tags": tags,
        "publishable": sample_floor_ok and (not inflated_pool),
        "_first_trade_ts": ts_list[0] if ts_list else 0,
        "_last_trade_ts": ex_list[-1] if ex_list else 0,
    }
    _cache_set(cache_key, result)
    return result


def _equity_curve(account: str, max_points: int = 500) -> List[List[Any]]:
    """Return equity curve [[unix_ts, cum_pnl_pct], ...] downsampled to max_points."""
    events = _load_account_history(account)
    closed = _reconstruct_trades(events)
    closed.sort(key=lambda t: int(t.get("exit_ts", 0)))
    pts: List[List[Any]] = []
    cum = 0.0
    for t in closed:
        cum += float(t.get("pnl_pct", 0) or 0)
        pts.append([int(t.get("exit_ts", 0)), round(cum, 4)])
    if len(pts) > max_points:
        step = len(pts) // max_points
        pts = pts[::step]
    return pts


# ─────────────────────────────────────────────────────────────────────────
# Flask app
# ─────────────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))


@app.route("/")
def index():
    return render_template("flz_dashboard.html")


@app.route("/api/top10")
def api_top10():
    mode = request.args.get("mode")
    if mode not in ("crypto", "tradier"):
        mode = None
    return jsonify(top_10(mode))


@app.route("/api/accounts")
def api_accounts():
    out = []
    for acct in ALL_ACCOUNTS:
        out.append(_account_canonical_stats(acct))
    return jsonify({"accounts": out, "generated_utc": _now_iso()})


@app.route("/api/account/<account>")
def api_account_detail(account):
    if account not in ALL_ACCOUNTS:
        return jsonify({"error": f"unknown account {account}"}), 404
    stats = _account_canonical_stats(account)
    events = _load_account_history(account)
    closed = _reconstruct_trades(events)
    return jsonify({
        "stats": stats,
        "trades_tail": closed[-200:],  # last 200 trades
        "equity_curve": _equity_curve(account),
        "generated_utc": _now_iso(),
    })


@app.route("/api/iter/<int:idx>")
def api_iter_detail(idx):
    iters = discover_iters()
    iters_pub = [r for r in iters if r.get("publishable")]
    iters_pub.sort(key=lambda r: float(r.get("pool_sharpe", 0)), reverse=True)
    if idx < 0 or idx >= len(iters_pub):
        return jsonify({"error": "out of range"}), 404
    return jsonify(iters_pub[idx])


SEVEN_D_AGENT_ACCOUNTS = ["inf", "fin", "trc"]  # NEW agent system


def _seven_d_window_stats(account: str, days: float = 7.0) -> Dict[str, Any]:
    """Canonical metrics over the last N days of LIVE trades for this account."""
    events = _load_account_history(account)
    closed = _reconstruct_trades(events)
    if not closed:
        return {"trades": 0, "n_syms": 0, "pool_sharpe": 0.0,
                "tier": "Noise", "total_gain_pct": 0.0, "win_rate": 0.0}
    cutoff = time.time() - days * 86400
    in_window = [t for t in closed if int(t.get("exit_ts", 0) or 0) >= cutoff]
    if not in_window:
        return {"trades": 0, "n_syms": 0, "pool_sharpe": 0.0,
                "tier": "Noise", "total_gain_pct": 0.0, "win_rate": 0.0,
                "window_days": days}
    by_sym: Dict[str, List[float]] = {}
    for t in in_window:
        by_sym.setdefault(t.get("symbol") or "?", []).append(float(t.get("pnl_pct", 0) or 0))
    metrics = metrics_guard.standard_metric_set(by_sym, years=max(days / 365.25, 0.01))
    pool_s = float(metrics.get("pool_sharpe", 0))
    pnls = [p for arr in by_sym.values() for p in arr]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "window_days": days,
        "trades": int(metrics.get("trades", 0)),
        "n_syms": int(metrics.get("n_syms", 0)),
        "pool_sharpe": round(pool_s, 4),
        "sym_sharpe": round(float(metrics.get("sym_sharpe", 0)), 4),
        "avg_gain_trade": round(float(metrics.get("avg_gain_trade", 0)), 4),
        "total_gain_pct": round(sum(pnls), 4),
        "win_rate": round(wins / max(1, len(pnls)), 4),
        "tier": metrics_guard.tier_name(pool_s),
        "first_ts": min(int(t.get("entry_ts", 0)) for t in in_window),
        "last_ts": max(int(t.get("exit_ts", 0)) for t in in_window),
    }


def _seven_d_opinions_summary(account: str) -> Dict[str, Any]:
    """Read hourly_reconfig/<acct>/opinions.json and return a compact summary."""
    p = HOURLY_RECONFIG_DIR / account / "opinions.json"
    if not p.exists():
        return {"available": False, "reason": "opinions.json missing — agent not running"}
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return {"available": False, "reason": f"parse error: {e}"}
    syms = data.get("syms", {})
    ranked = sorted(syms.items(), key=lambda kv: -float(kv[1].get("wsharpe", 0)))
    actionable_long = sum(1 for v in syms.values() if v.get("opinion") == "LONG")
    actionable_short = sum(1 for v in syms.values() if v.get("opinion") == "SHORT")
    top = []
    for k, v in ranked[:8]:
        top.append({
            "sym_side": k,
            "opinion": v.get("opinion"),
            "wsharpe": round(float(v.get("wsharpe", 0)), 4),
            "winning_tag": v.get("winning_tag"),
            "tier": v.get("tier") or metrics_guard.tier_name(float(v.get("wsharpe", 0))),
        })
    return {
        "available": True,
        "cycle_id": data.get("_cycle_id"),
        "now_ts": data.get("_now_ts"),
        "n_total": len(syms),
        "n_actionable_long": actionable_long,
        "n_actionable_short": actionable_short,
        "top": top,
    }


@app.route("/api/seven_d_priority")
def api_seven_d_priority():
    """🔴 7D AGENT priority panel — first-priority view of the NEW agent
    system on inf/fin/trc. For each account: last-7-day live canonical stats,
    latest opinions summary, equity curve last 7d, latest cycle age."""
    days = float(request.args.get("days", 7))
    out_per_acct: Dict[str, Any] = {}
    for acct in SEVEN_D_AGENT_ACCOUNTS:
        # Latest cycle age
        runs_dir = HOURLY_RECONFIG_DIR / acct / "runs"
        latest_cycle = None
        cycle_age_min = None
        if runs_dir.exists():
            cycles = sorted([p for p in runs_dir.iterdir() if p.is_dir()], reverse=True)
            if cycles:
                latest_cycle = cycles[0].name
                try:
                    cycle_age_min = round((time.time() - cycles[0].stat().st_mtime) / 60.0, 1)
                except Exception:
                    pass
        out_per_acct[acct] = {
            "account": acct,
            "kind": "stocks" if acct in STOCK_ACCOUNTS else "crypto",
            "live_stats_7d": _seven_d_window_stats(acct, days=days),
            "live_stats_alltime": _account_canonical_stats(acct),
            "opinions": _seven_d_opinions_summary(acct),
            "latest_cycle": latest_cycle,
            "cycle_age_min": cycle_age_min,
        }
    return jsonify({
        "accounts": out_per_acct,
        "agent_accounts": SEVEN_D_AGENT_ACCOUNTS,
        "window_days": days,
        "generated_utc": _now_iso(),
    })


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    _CACHE.clear()
    return jsonify({"ok": True, "cleared_at_utc": _now_iso()})


@app.route("/api/health")
def api_health():
    return jsonify({
        "service": "flz_dashboard",
        "port": 5057,
        "metrics_guard_md5_check": "see metrics_guard.py",
        "cache_ttl_sec": _CACHE_TTL_SEC,
        "now_utc": _now_iso(),
        "n_iters_indexed": len(discover_iters()),
        "accounts_known": ALL_ACCOUNTS,
    })


# ─────────────────────────────────────────────────────────────────────────
# Hourly reconfig endpoints (added 2026-05-01) — surface the 7d daemon outputs
# so user can verify on chart with metrics_guard recalculation.
# ─────────────────────────────────────────────────────────────────────────

HOURLY_CSV_DIR = BASE_DIR / "data" / "sweep_results"
HOURLY_RECONFIG_DIR = BASE_DIR / "data" / "hourly_reconfig"
PER_SYM_ACTIVE_CONFIG = HOURLY_RECONFIG_DIR / "per_sym_active_config.json"


def _read_hourly_canonical_csv(account: str) -> List[Dict[str, Any]]:
    """Read canonical_hourly_<account>.csv. Returns list of row dicts."""
    import csv
    p = HOURLY_CSV_DIR / f"canonical_hourly_{account}.csv"
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    with p.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            for k, v in list(row.items()):
                try:
                    row[k] = float(v)
                except Exception:
                    pass
            out.append(row)
    return out


@app.route("/api/hourly_canonical/<account>")
def api_hourly_canonical(account):
    """Return canonical_hourly_<account>.csv as JSON list, newest last."""
    if account not in ALL_ACCOUNTS:
        return jsonify({"error": f"unknown account {account}"}), 404
    rows = _read_hourly_canonical_csv(account)
    return jsonify({
        "account": account,
        "n_rows": len(rows),
        "rows": rows,
        "csv_path": str(HOURLY_CSV_DIR / f"canonical_hourly_{account}.csv"),
        "generated_utc": _now_iso(),
    })


@app.route("/api/opinions/<account>")
def api_opinions(account):
    """Return current opinions.json with per-sym wsharpe + winning_tag + tier."""
    if account not in ALL_ACCOUNTS:
        return jsonify({"error": f"unknown account {account}"}), 404
    p = HOURLY_RECONFIG_DIR / account / "opinions.json"
    if not p.exists():
        return jsonify({"account": account, "opinions": None, "error": "no opinions.json yet"}), 200
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return jsonify({"account": account, "error": f"parse error: {e}"}), 500
    syms = data.get("syms", {})
    ranked = sorted(syms.items(), key=lambda kv: -kv[1].get("wsharpe", 0))
    return jsonify({
        "account": account,
        "cycle_id": data.get("_cycle_id"),
        "now_ts": data.get("_now_ts"),
        "ranked": [{"sym_side": k, **v} for k, v in ranked],
        "n_total": len(syms),
        "n_actionable": sum(1 for v in syms.values() if v.get("opinion") in ("LONG", "SHORT")),
        "generated_utc": _now_iso(),
    })


@app.route("/api/recalc/<account>/<sym>/<side>")
def api_recalc(account, sym, side):
    """Recompute pool_sharpe + standard_metric_set on the fly from the per-trade
    JSONL of the current cycle's winning candidate for (sym, side). User-verifiable.
    Window default: last 7 days (override with ?days=N).
    """
    if account not in ALL_ACCOUNTS:
        return jsonify({"error": f"unknown account {account}"}), 404
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        return jsonify({"error": "side must be LONG or SHORT"}), 400
    sym = sym.upper()
    days = float(request.args.get("days", 7))
    # Find the latest cycle dir
    runs_dir = HOURLY_RECONFIG_DIR / account / "runs"
    if not runs_dir.exists():
        return jsonify({"error": "no runs dir yet"}), 404
    cycles = sorted([p for p in runs_dir.iterdir() if p.is_dir()], reverse=True)
    if not cycles:
        return jsonify({"error": "no completed cycles"}), 404
    # Find the active_config to know the winning_tag (may not exist yet on first cycle)
    active_path = HOURLY_RECONFIG_DIR / account / "active_config.json"
    winning_tag = None
    if active_path.exists():
        try:
            ac = json.loads(active_path.read_text())
            winning_tag = ac.get(f"{sym}_{side}", {}).get("winning_tag")
        except Exception:
            pass
    # Walk newest cycle first; use first cycle that has the JSONL
    latest = cycles[0]
    matched_files: List[Path] = []
    for c in cycles[:5]:  # up to 5 most recent cycles
        files = list(c.glob(f"*__{side}__*__{sym}.jsonl"))
        if files:
            matched_files = files
            latest = c
            break
    if winning_tag and matched_files:
        wf = [f for f in matched_files if f.stem.endswith(f"__{side}__{winning_tag}__{sym}")]
        if wf:
            matched_files = wf
    if not matched_files:
        return jsonify({"error": f"no JSONL for {sym}_{side} in last 5 cycles"}), 404
    # Recompute via metrics_guard
    cutoff = time.time() - days * 86400
    rets: List[float] = []
    n_total = 0
    for f in matched_files:
        with f.open() as fh:
            for ln in fh:
                try:
                    rec = json.loads(ln)
                    if (rec.get("side") or "").upper() != side:
                        continue
                    n_total += 1
                    ets = int(rec.get("exit_ts", 0) or 0)
                    if ets < cutoff:
                        continue
                    rets.append(float(rec.get("pnl_pct", 0)))
                except Exception:
                    pass
    if not rets:
        return jsonify({
            "account": account, "sym": sym, "side": side,
            "winning_tag": winning_tag, "cycle": latest.name,
            "n_in_window": 0, "n_total_in_jsonl": n_total,
            "error": "0 trades in window",
        }), 200
    # Compute via metrics_guard's pool_sharpe + standard_metric_set
    returns_by_sym = {sym: rets}
    metrics = metrics_guard.standard_metric_set(returns_by_sym, years=max(days / 365.25, 0.01))
    metrics["tier"] = metrics_guard.tier_name(metrics["pool_sharpe"])
    metrics["window_days"] = days
    metrics["winning_tag"] = winning_tag
    metrics["jsonl_files"] = [f.name for f in matched_files]
    metrics["cycle"] = latest.name
    metrics["n_total_in_jsonl"] = n_total
    return jsonify({
        "account": account, "sym": sym, "side": side,
        "metrics": metrics,
        "generated_utc": _now_iso(),
    })


def _load_per_sym_report() -> List[Dict[str, Any]]:
    """Build per-symbol overview: 4yr sweep winner + 7D hourly opinion + live + paper trade stats."""
    cached = _cache_get("symbol_report")
    if cached is not None:
        return cached

    # 1. Load per_sym_active_config (4yr sweep winners)
    per_sym: Dict[str, Any] = {}
    if PER_SYM_ACTIVE_CONFIG.exists():
        try:
            per_sym = json.loads(PER_SYM_ACTIVE_CONFIG.read_text())
        except Exception:
            pass

    # 2. Load all hourly_reconfig active_config.json files (7D opinions) + opinions.json
    hourly_configs: Dict[str, Dict[str, Any]] = {}
    opinions_map: Dict[str, str] = {}  # key → opinion string (LONG/SHORT/FLAT/BELOW_THRESHOLD)
    for acct_dir in HOURLY_RECONFIG_DIR.iterdir():
        if not acct_dir.is_dir():
            continue
        ac_path = acct_dir / "active_config.json"
        if ac_path.exists():
            try:
                ac = json.loads(ac_path.read_text())
                for k, v in ac.items():
                    hourly_configs[k] = {**v, "_account": acct_dir.name}
            except Exception:
                pass
        op_path = acct_dir / "opinions.json"
        if op_path.exists():
            try:
                op_data = json.loads(op_path.read_text())
                for k, v in op_data.get("syms", {}).items():
                    opinions_map[k] = v.get("opinion", "")
            except Exception:
                pass

    # 3. Count live CLOSED ROUNDS per (sym_side) from history JSONLs (last 30 days)
    live_counts: Dict[str, int] = defaultdict(int)
    live_pnl: Dict[str, float] = defaultdict(float)
    cutoff_30d = time.time() - 30 * 86400
    for acct in ALL_ACCOUNTS:
        try:
            events = _load_account_history(acct)
            closed = _reconstruct_trades(events)
            for t in closed:
                ets = int(t.get("exit_ts") or 0)
                if ets < cutoff_30d:
                    continue
                sym_s = (t.get("symbol") or "").upper()
                side_s = (t.get("side") or "").upper()
                if not sym_s or side_s not in ("LONG", "SHORT"):
                    continue
                key = f"{sym_s}_{side_s}"
                live_counts[key] += 1
                live_pnl[key] += float(t.get("pnl_pct") or 0)
        except Exception:
            continue

    # 4. Aggregate paper forward trades (CLOSE records, last 30 days, all variants+arms)
    paper_closes: Dict[str, int] = defaultdict(int)
    paper_pnl: Dict[str, float] = defaultdict(float)
    paper_forward_dir = BASE_DIR / "data" / "paper_forward"
    if paper_forward_dir.exists():
        for variant_dir in paper_forward_dir.iterdir():
            if not variant_dir.is_dir():
                continue
            for arm_dir in variant_dir.iterdir():
                if not arm_dir.is_dir() or not arm_dir.name.startswith("arm_"):
                    continue
                tf = arm_dir / "trades.jsonl"
                if not tf.exists():
                    continue
                try:
                    for line in tf.read_text().splitlines():
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("action") != "CLOSE":
                            continue
                        if float(rec.get("ts") or 0) < cutoff_30d:
                            continue
                        sym_p = (rec.get("symbol") or "").upper()
                        side_p = (rec.get("side") or "").upper()
                        if not sym_p or side_p not in ("LONG", "SHORT"):
                            continue
                        pk = f"{sym_p}_{side_p}"
                        paper_closes[pk] += 1
                        paper_pnl[pk] += float(rec.get("net_pct") or 0)
                except Exception:
                    continue

    # 5. Merge into unified rows
    all_keys = sorted(set(list(per_sym.keys()) + list(hourly_configs.keys())))
    rows: List[Dict[str, Any]] = []
    for key in all_keys:
        ps = per_sym.get(key, {})
        hc = hourly_configs.get(key, {})
        sym_part = key.rsplit("_", 1)[0] if "_" in key else key
        side_part = key.rsplit("_", 1)[1] if "_" in key else "?"
        acct = hc.get("_account", ps.get("account", ""))
        mode = "tradier" if acct in STOCK_ACCOUNTS else "crypto"
        if not acct:
            mode = "crypto" if (sym_part.endswith("USDC") or sym_part.endswith("USDT")) else "tradier"
        lc = live_counts.get(key, 0)
        lp = live_pnl.get(key, 0.0)
        pc = paper_closes.get(key, 0)
        pp = paper_pnl.get(key, 0.0)
        # Active overrides: merge per_sym overrides + hourly overrides (hourly takes precedence)
        ps_ovr = {k: v for k, v in ps.get("overrides", {}).items() if not k.startswith("_")}
        hc_ovr = {k: v for k, v in hc.get("overrides", {}).items() if not k.startswith("_")}
        merged_ovr = {**ps_ovr, **hc_ovr}
        trade_gate = hc.get("_trade_gate", "")
        long_enabled = merged_ovr.get("LONG_ENABLED", None)
        short_enabled = merged_ovr.get("SHORT_ENABLED", None)
        opinion = opinions_map.get(key, "")
        row = {
            "key": key,
            "sym": sym_part,
            "side": side_part,
            "mode": mode,
            "account": acct or "?",
            # 4yr sweep (per_sym)
            "per_sym_wsharpe": round(float(ps.get("wsharpe", 0) or 0), 4),
            "per_sym_trades": int(ps.get("trades", 0) or 0),
            "per_sym_tag": ps.get("winning_tag", ""),
            "per_sym_sample": ps.get("sample_tag", ""),
            # 7D hourly reconfig
            "hourly_wsharpe": round(float(hc.get("wsharpe", 0) or 0), 4),
            "hourly_trades": int(hc.get("trades", 0) or 0),
            "hourly_tag": hc.get("winning_tag", ""),
            "trade_gate": trade_gate,
            "opinion": opinion,
            # Live closed rounds (30d)
            "live_closes_30d": lc,
            "live_pnl_30d": round(lp, 2),
            "live_avg_pnl": round(lp / lc, 4) if lc > 0 else 0.0,
            # Paper forward trades (30d, all variants+arms)
            "paper_closes_30d": pc,
            "paper_pnl_30d": round(pp, 2),
            "paper_avg_pnl": round(pp / pc, 4) if pc > 0 else 0.0,
            # Active settings
            "long_enabled": long_enabled,
            "short_enabled": short_enabled,
            "overrides": merged_ovr,
            "override_count": len(merged_ovr),
            "has_per_sym": bool(ps),
            "has_hourly": bool(hc) and (hc.get("trades", 0) or 0) > 0,
        }
        row["tier_per_sym"] = metrics_guard.tier_name(row["per_sym_wsharpe"]) if row["per_sym_wsharpe"] else "—"
        row["tier_hourly"] = metrics_guard.tier_name(row["hourly_wsharpe"]) if row["hourly_wsharpe"] else "—"
        rows.append(row)

    _cache_set("symbol_report", rows)
    return rows


@app.route("/api/symbol_report")
def api_symbol_report():
    """Per-symbol overview: 4yr sweep winner + 7D hourly config + live 30d stats."""
    rows = _load_per_sym_report()
    mode = request.args.get("mode")
    if mode in ("crypto", "tradier"):
        rows = [r for r in rows if r.get("mode") == mode]
    return jsonify({
        "rows": rows,
        "n_total": len(rows),
        "generated_utc": _now_iso(),
    })


@app.route("/api/symbol_chart/<sym>")
def api_symbol_chart(sym: str):
    """Per-symbol equity curve: live closed rounds + paper forward CLOSE records (90d)."""
    sym_upper = sym.upper()
    cutoff = time.time() - 90 * 86400

    # Live trades for this symbol (all accounts, both sides)
    live_long: List[Tuple[float, float]] = []  # (exit_ts, pnl_pct)
    live_short: List[Tuple[float, float]] = []
    live_trades_list: List[Dict] = []
    for acct in ALL_ACCOUNTS:
        try:
            events = _load_account_history(acct)
            closed = _reconstruct_trades(events)
            for t in closed:
                ts = float(t.get("exit_ts") or 0)
                if ts < cutoff:
                    continue
                t_sym = (t.get("symbol") or "").upper()
                if t_sym != sym_upper:
                    continue
                side = (t.get("side") or "").upper()
                pnl = float(t.get("pnl_pct") or 0)
                if side == "LONG":
                    live_long.append((ts, pnl))
                elif side == "SHORT":
                    live_short.append((ts, pnl))
                live_trades_list.append({
                    "ts": ts,
                    "side": side,
                    "entry_price": t.get("entry_price"),
                    "exit_price": t.get("exit_price"),
                    "pnl_pct": round(pnl, 4),
                    "duration_sec": t.get("duration_sec"),
                    "exit_reason": (t.get("exit_reason") or "")[:80],
                    "account": acct,
                })
        except Exception:
            continue

    # Paper forward trades for this symbol (all variants + arms, both sides)
    paper_long: List[Tuple[float, float]] = []
    paper_short: List[Tuple[float, float]] = []
    paper_forward_dir = BASE_DIR / "data" / "paper_forward"
    if paper_forward_dir.exists():
        for variant_dir in paper_forward_dir.iterdir():
            if not variant_dir.is_dir():
                continue
            for arm_dir in variant_dir.iterdir():
                if not arm_dir.is_dir() or not arm_dir.name.startswith("arm_"):
                    continue
                tf = arm_dir / "trades.jsonl"
                if not tf.exists():
                    continue
                try:
                    for line in tf.read_text().splitlines():
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("action") != "CLOSE":
                            continue
                        ts = float(rec.get("ts") or 0)
                        if ts < cutoff:
                            continue
                        if (rec.get("symbol") or "").upper() != sym_upper:
                            continue
                        side = (rec.get("side") or "").upper()
                        net = float(rec.get("net_pct") or 0)
                        if side == "LONG":
                            paper_long.append((ts, net))
                        elif side == "SHORT":
                            paper_short.append((ts, net))
                except Exception:
                    continue

    def _equity_curve(pairs: List[Tuple[float, float]]) -> List[List]:
        pairs.sort(key=lambda x: x[0])
        cum = 0.0
        out = []
        for ts, pnl in pairs:
            cum += pnl
            out.append([ts, round(cum, 4)])
        return out

    live_trades_list.sort(key=lambda x: x["ts"])
    return jsonify({
        "sym": sym_upper,
        "live_long_curve": _equity_curve(live_long),
        "live_short_curve": _equity_curve(live_short),
        "paper_long_curve": _equity_curve(paper_long),
        "paper_short_curve": _equity_curve(paper_short),
        "live_trades": live_trades_list[-100:],
        "n_live_long": len(live_long),
        "n_live_short": len(live_short),
        "n_paper_long": len(paper_long),
        "n_paper_short": len(paper_short),
        "generated_utc": _now_iso(),
    })


# ─────────────────────────────────────────────────────────────────────────
# BACKTEST REGISTRY (rewritten 2026-05-17): per-symbol filtered backtest
# index over data/canonical_trades/ + data/research_*/v8_vec_sweep_*_trades
# ─────────────────────────────────────────────────────────────────────────

CANON_TRADES_DIR = BASE_DIR / "data" / "canonical_trades"
VEC_RESEARCH_DIRS = [BASE_DIR / "data" / "research_20260516"]
KLINES_BT_DIR = BASE_DIR / "klines_cache_backtest"
KLINES_LIVE_DIR = BASE_DIR / "klines_cache"

_RUN_EPOCH_RE = re.compile(r"_(17\d{8})$")
_RUN_VEC_FILE_RE = re.compile(r"^v8_vec_sweep_(\d+)_trades\.jsonl$")


def _humanize_run_name(raw: str) -> str:
    base = _RUN_EPOCH_RE.sub("", raw)
    m_run = re.search(r"_run(\d+)$", base)
    run_suffix = f" (run {m_run.group(1)})" if m_run else ""
    if m_run:
        base = base[:m_run.start()]
    m_ver = re.search(r"_v(\d+)$", base)
    ver_suffix = f" v{m_ver.group(1)}" if m_ver else ""
    if m_ver:
        base = base[:m_ver.start()]
    name = base.replace("_", " ")
    name = re.sub(r"(\d+)sym", r"\1-sym", name, flags=re.IGNORECASE)
    repls = {
        "canonical": "Canonical", "flz8": "FLZ-8", "dedv3": "ded-v3",
        "best": "BEST", "loose": "LOOSE", "smoke": "smoke",
        "baseline": "baseline", "tradier": "Tradier", "crypto": "Crypto",
    }
    out_tokens = []
    for tok in name.split():
        out_tokens.append(repls.get(tok.lower(), tok))
    return (" ".join(out_tokens) + ver_suffix + run_suffix).strip()


def _family_from_raw(raw: str) -> str:
    base = _RUN_EPOCH_RE.sub("", raw)
    base = re.sub(r"_run\d+$", "", base)
    base = re.sub(r"_v\d+$", "", base)
    return base


def _date_from_raw(raw: str) -> Optional[datetime]:
    m = _RUN_EPOCH_RE.search(raw)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)
        except Exception:
            return None
    return None


def discover_backtests() -> List[Dict[str, Any]]:
    """Return list of backtest run records sorted newest-first.
    Each record: {run_id, family, name, date, source, symbols, _symbols_map}.
    Cached for _CACHE_TTL_SEC."""
    cached = _cache_get("backtests")
    if cached is not None:
        return cached
    out: List[Dict[str, Any]] = []
    # 1. data/canonical_trades/<run>/<run>__<SYMBOL>.jsonl  (per-symbol files,
    #    one closed-round per line — backtest_v8_engine canonical schema)
    if CANON_TRADES_DIR.exists():
        for run_dir in sorted(CANON_TRADES_DIR.iterdir()):
            if not run_dir.is_dir():
                continue
            if run_dir.name.startswith("_"):
                continue
            jsonls = sorted(run_dir.glob("*.jsonl"))
            if not jsonls:
                continue
            symbols_map: Dict[str, str] = {}
            for jf in jsonls:
                stem = jf.stem
                if "__" in stem:
                    sym = stem.rsplit("__", 1)[1].upper()
                    symbols_map[sym] = str(jf.relative_to(BASE_DIR))
            if not symbols_map:
                continue
            run_date = _date_from_raw(run_dir.name) or datetime.fromtimestamp(
                run_dir.stat().st_mtime, tz=timezone.utc)
            out.append({
                "run_id": f"canon::{run_dir.name}",
                "family": _family_from_raw(run_dir.name),
                "name": _humanize_run_name(run_dir.name),
                "date": run_date,
                "source": "canonical_engine",
                "symbols": sorted(symbols_map.keys()),
                "_symbols_map": symbols_map,
            })
    # 2. data/research_*/v8_vec_sweep_<epoch>_trades.jsonl  (mixed-symbol
    #    OPEN/CLOSE event stream — v8_vec_sweep engine schema)
    for vec_dir in VEC_RESEARCH_DIRS:
        if not vec_dir.exists():
            continue
        for jf in sorted(vec_dir.glob("v8_vec_sweep_*_trades.jsonl")):
            m = _RUN_VEC_FILE_RE.match(jf.name)
            if not m:
                continue
            epoch = int(m.group(1))
            symbols = set()
            try:
                with jf.open() as fh:
                    for i, line in enumerate(fh):
                        if i >= 8000:
                            break
                        try:
                            r = json.loads(line)
                            sym = (r.get("symbol") or "").upper()
                            if sym:
                                symbols.add(sym)
                        except Exception:
                            pass
            except Exception:
                continue
            if not symbols:
                continue
            run_date = datetime.fromtimestamp(epoch, tz=timezone.utc)
            out.append({
                "run_id": f"vec::{epoch}",
                "family": "v8_vec_sweep",
                "name": f"V8 Vec Sweep · {run_date.strftime('%Y-%m-%d %H:%M')} (#{str(epoch)[-4:]})",
                "date": run_date,
                "source": "vec_engine",
                "symbols": sorted(symbols),
                "_symbols_map": {sym: str(jf.relative_to(BASE_DIR)) for sym in symbols},
            })
    out.sort(key=lambda r: r["date"], reverse=True)
    _cache_set("backtests", out)
    return out


def _load_run_symbol_trades(run_id: str, symbol: str) -> List[Dict[str, Any]]:
    """Return normalized closed-trade list for (run, symbol)."""
    runs = discover_backtests()
    rec = next((r for r in runs if r["run_id"] == run_id), None)
    if not rec:
        return []
    sym_u = symbol.upper()
    rel = rec["_symbols_map"].get(sym_u)
    if not rel:
        return []
    p = BASE_DIR / rel
    out: List[Dict[str, Any]] = []
    if run_id.startswith("canon::"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("symbol") or "").upper() != sym_u:
                    continue
                ets = int(r.get("entry_ts") or 0)
                xts = int(r.get("exit_ts") or 0)
                if ets == 0 or xts == 0:
                    continue
                out.append({
                    "entry_ts": ets, "exit_ts": xts,
                    "side": r.get("side", ""),
                    "entry_price": float(r.get("entry_price") or 0),
                    "exit_price": float(r.get("exit_price") or 0),
                    "pnl_pct": float(r.get("pnl_pct") or 0),
                    "duration_min": max(0, (xts - ets) // 60),
                    "entry_reason": (r.get("entry_reason") or "")[:80],
                    "exit_reason": (r.get("exit_reason") or "")[:80],
                })
        except Exception:
            pass
    elif run_id.startswith("vec::"):
        # OPEN / CLOSE event pairing per (sym, side)
        open_state: Dict[Tuple[str, str], Dict[str, Any]] = {}
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if (r.get("symbol") or "").upper() != sym_u:
                    continue
                side = (r.get("side") or "").upper()
                action = (r.get("type") or r.get("action") or "").upper()
                ts_raw = r.get("ts") or r.get("entry_ts") or r.get("exit_ts") or 0
                if isinstance(ts_raw, str):
                    try:
                        ts = int(datetime.fromisoformat(
                            ts_raw.replace("Z", "+00:00")).timestamp())
                    except Exception:
                        ts = 0
                else:
                    try:
                        ts = int(float(ts_raw))
                    except Exception:
                        ts = 0
                key = (sym_u, side)
                if action in ("OPEN", "ENTRY"):
                    open_state[key] = {
                        "entry_ts": ts,
                        "entry_price": float(r.get("price") or 0),
                        "side": side,
                        "entry_reason": (r.get("reason") or "")[:80],
                    }
                elif action in ("CLOSE", "EXIT"):
                    o = open_state.pop(key, None)
                    if not o:
                        continue
                    exit_price = float(r.get("price") or 0)
                    if not o["entry_price"] or not exit_price:
                        continue
                    if side == "LONG":
                        pnl = (exit_price - o["entry_price"]) / o["entry_price"] * 100.0
                    else:
                        pnl = (o["entry_price"] - exit_price) / o["entry_price"] * 100.0
                    if r.get("pnl_pct") is not None:
                        try:
                            pnl = float(r["pnl_pct"])
                        except Exception:
                            pass
                    out.append({
                        "entry_ts": o["entry_ts"], "exit_ts": ts,
                        "side": side,
                        "entry_price": o["entry_price"], "exit_price": exit_price,
                        "pnl_pct": pnl,
                        "duration_min": max(0, (ts - o["entry_ts"]) // 60),
                        "entry_reason": o["entry_reason"],
                        "exit_reason": (r.get("reason") or "")[:80],
                    })
        except Exception:
            pass
    out.sort(key=lambda t: t["entry_ts"])
    return out


def _bh_baseline_pct(symbol: str, start_ts: int, end_ts: int) -> Optional[float]:
    if start_ts >= end_ts:
        return None
    sym_u = symbol.upper()
    candidates = [sym_u]
    if sym_u.endswith("USDC"):
        candidates.append(sym_u[:-4] + "USDT")
    for cache_dir in (KLINES_BT_DIR, KLINES_LIVE_DIR):
        if not cache_dir.exists():
            continue
        for cand in candidates:
            p = cache_dir / f"{cand}_15m.json"
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            if not isinstance(data, list) or not data:
                continue
            first_close = None
            last_close = None
            for bar in data:
                ts_raw = bar.get("timestamp") or bar.get("ts")
                try:
                    if isinstance(ts_raw, str):
                        bts = int(datetime.fromisoformat(
                            ts_raw.replace("Z", "+00:00")).timestamp())
                    else:
                        bts = int(float(ts_raw))
                except Exception:
                    continue
                close = float(bar.get("close") or 0)
                if not close:
                    continue
                if bts >= start_ts and first_close is None:
                    first_close = close
                if bts <= end_ts:
                    last_close = close
                elif bts > end_ts:
                    break
            if first_close and last_close and first_close > 0:
                return (last_close - first_close) / first_close * 100.0
    return None


def _summarize_trades(trades: List[Dict[str, Any]], symbol: str) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0, "wr_pct": 0.0, "total_gain_pct": 0.0,
                "gain_per_mo": 0.0, "max_dd_pct": 0.0, "pool_sharpe": 0.0,
                "bh_pct": None, "first_ts": 0, "last_ts": 0, "years": 0.0,
                "avg_gain_trade": 0.0, "wins": 0, "losses": 0}
    pnls = [float(t.get("pnl_pct") or 0) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p <= 0)
    total = sum(pnls)
    first_ts = int(trades[0].get("entry_ts") or 0)
    last_ts = int(trades[-1].get("exit_ts") or 0)
    span_s = max(1, last_ts - first_ts)
    years = span_s / (86400.0 * 365.25)
    months = max(0.01, years * 12.0)
    gain_mo = total / months
    cum = 0.0
    peak = -1e18
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        if peak - cum > max_dd:
            max_dd = peak - cum
    ps = metrics_guard.pool_sharpe(pnls)
    bh = _bh_baseline_pct(symbol, first_ts, last_ts)
    return {
        "trades": len(pnls),
        "wins": wins,
        "losses": losses,
        "wr_pct": round(100.0 * wins / max(1, len(pnls)), 2),
        "total_gain_pct": round(total, 3),
        "avg_gain_trade": round(total / max(1, len(pnls)), 4),
        "gain_per_mo": round(gain_mo, 3),
        "max_dd_pct": round(max_dd, 3),
        "pool_sharpe": round(float(ps), 4),
        "bh_pct": (round(bh, 3) if bh is not None else None),
        "first_ts": first_ts, "last_ts": last_ts,
        "years": round(years, 3),
    }


@app.route("/api/families")
def api_families():
    runs = discover_backtests()
    fams: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_syms = set()
    for r in runs:
        fams[r["family"]].append({
            "run_id": r["run_id"],
            "name": r["name"],
            "date": r["date"].isoformat() if r["date"] else None,
            "source": r["source"],
            "n_syms": len(r["symbols"]),
            "symbols": r["symbols"],
        })
        all_syms.update(r["symbols"])
    fam_list = [{"family": f, "human": _humanize_run_name(f),
                 "n_runs": len(rs), "runs": rs}
                for f, rs in sorted(fams.items())]
    return jsonify({
        "families": fam_list,
        "all_symbols": sorted(all_syms),
        "n_runs_total": len(runs),
        "generated_utc": _now_iso(),
    })


@app.route("/api/sym_backtests/<sym>")
def api_sym_backtests(sym: str):
    """All backtest runs that contain trades for this symbol, sorted."""
    sym_u = sym.upper()
    sort = request.args.get("sort", "sharpe")
    runs = discover_backtests()
    out: List[Dict[str, Any]] = []
    for r in runs:
        if sym_u not in r["_symbols_map"]:
            continue
        trades = _load_run_symbol_trades(r["run_id"], sym_u)
        s = _summarize_trades(trades, sym_u)
        if s["trades"] == 0:
            continue
        out.append({
            "run_id": r["run_id"], "name": r["name"], "family": r["family"],
            "date": r["date"].isoformat() if r["date"] else None,
            "source": r["source"],
            **s,
        })
    if sort == "gain_mo":
        out.sort(key=lambda x: x["gain_per_mo"], reverse=True)
    else:
        out.sort(key=lambda x: x["pool_sharpe"], reverse=True)
    return jsonify({"symbol": sym_u, "n_runs": len(out), "sort": sort,
                    "runs": out, "generated_utc": _now_iso()})


@app.route("/api/sym_backtest_trades/<path:run_id>/<sym>")
def api_sym_backtest_trades(run_id: str, sym: str):
    """Per-trade list for (run, symbol) + summary + live overlay-able series."""
    sym_u = sym.upper()
    trades = _load_run_symbol_trades(run_id, sym_u)
    s = _summarize_trades(trades, sym_u)
    runs = discover_backtests()
    rec = next((r for r in runs if r["run_id"] == run_id), None)
    return jsonify({
        "symbol": sym_u,
        "run_id": run_id,
        "run_name": rec["name"] if rec else run_id,
        "run_family": rec["family"] if rec else "?",
        "run_date": (rec["date"].isoformat() if rec and rec["date"] else None),
        "run_source": rec["source"] if rec else "?",
        "summary": s,
        "trades": trades,
        "generated_utc": _now_iso(),
    })


@app.route("/api/sym_live_trades/<sym>")
def api_sym_live_trades(sym: str):
    """Live trades for a symbol filtered by account (or all if not given).
    Used as overlay over the backtest chart."""
    sym_u = sym.upper()
    account = request.args.get("account") or None
    accounts = [account] if account in ALL_ACCOUNTS else ALL_ACCOUNTS
    out: List[Dict[str, Any]] = []
    for acct in accounts:
        try:
            events = _load_account_history(acct)
            closed = _reconstruct_trades(events)
            for t in closed:
                if (t.get("symbol") or "").upper() != sym_u:
                    continue
                out.append({
                    "account": acct,
                    "entry_ts": int(t.get("entry_ts") or 0),
                    "exit_ts": int(t.get("exit_ts") or 0),
                    "side": t.get("side", ""),
                    "entry_price": float(t.get("entry_price") or 0),
                    "exit_price": float(t.get("exit_price") or 0),
                    "pnl_pct": float(t.get("pnl_pct") or 0),
                    "duration_sec": int(t.get("duration_sec") or 0),
                    "exit_reason": (t.get("exit_reason") or "")[:80],
                })
        except Exception:
            continue
    out.sort(key=lambda x: x["entry_ts"])
    return jsonify({
        "symbol": sym_u,
        "account_filter": account or "ALL",
        "n_trades": len(out),
        "trades": out,
        "accounts_available": ALL_ACCOUNTS,
        "generated_utc": _now_iso(),
    })


def _kill_port(port: int):
    """Kill any process listening on port (best-effort, like trade_analytics does)."""
    import subprocess
    try:
        out = subprocess.check_output(["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"], stderr=subprocess.DEVNULL)
        for pid in out.decode().split():
            if pid.strip().isdigit():
                subprocess.run(["kill", "-9", pid.strip()], stderr=subprocess.DEVNULL)
        time.sleep(0.5)
    except Exception:
        pass


if __name__ == "__main__":
    _kill_port(5057)
    print(f"flz_dashboard starting on http://127.0.0.1:5057")
    print(f"  metrics_guard: imported (md5 elsewhere)")
    print(f"  cache TTL: {_CACHE_TTL_SEC}s ({_CACHE_TTL_SEC/3600:.1f}h)")
    app.run(host="0.0.0.0", port=5057, debug=False, threaded=True)
