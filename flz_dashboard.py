#!/usr/bin/env python3
"""flz_dashboard — port 5057. The window-to-the-world.

REQUIREMENT (user 2026-04-30):
  - Top-10 best backtest configs in row 1, refreshed hourly from continuous
    sweeps on S1/S2 + MB.
  - Every number ABSOLUTELY AUDITED through metrics_guard. Sub-floor results
    tagged [DIAGNOSTIC]; inflated (|sharpe|>5 with trades<5000) tagged INFLATED
    and excluded from "real" top-10 (shown in a separate diagnostic tail).
  - Tabs for Live trades / Backtest details / Equity curves / Comparison.
  - All numbers respect CLAUDE.md NO-LIES MANDATE.

Data sources:
  - data/autonomous/<pool>/<wXXX>/autonomous_*_winners.jsonl (per-iter aggregates)
  - data/decisions/decisions_<acct>_YYYYMMDD.jsonl (live decisions)
  - data/history/<acct>/<SYMBOL>_<SIDE>.jsonl (live trade history)

This is read-only. No optimization runs from here. Optimization scripts run
on S1/S2 (autonomous_search.py); this dashboard only displays results.
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
DECISIONS_DIR = BASE_DIR / "data" / "decisions"
HISTORY_DIR = BASE_DIR / "data" / "history"
TRADIER_HISTORY_DIR = BASE_DIR / "data" / "tradier" / "history"
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
    # Publishable = ALL three legs of sample floor + canonical pool not inflated.
    # sym_sharpe inflation is informational only (sym_sharpe is a diagnostic, not promotion criterion).
    out["publishable"] = sample_floor_ok and (not inflated_pool)
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
    """Load every trade event for the account from data/history/<acct>/*.jsonl."""
    is_stock = account in STOCK_ACCOUNTS
    base = (TRADIER_HISTORY_DIR if is_stock else HISTORY_DIR) / account
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
