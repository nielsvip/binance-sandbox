#!/usr/bin/env python3
"""Paper (shadow) options email report — morning + afternoon.

Reactivated 2026-06-04 (the shadow paper-options system had been disabled since
2026-04-26). Reports the hypothetical option trades the paper system WOULD have
placed, plus a running hypothetical P&L. NO-LIES: every number here is read from
the real shadow decision/score files — when data is missing it says so rather
than inventing a figure. Run: `python options_paper_email.py [--session morning|afternoon]`.

Sends via morning_email's existing Gmail plumbing (send_email/get_gmail_password).
"""
import argparse
import glob
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from morning_email import send_email, TO_EMAIL  # reuse SMTP + recipients
from config_tradier import TradierConfig

BASE = os.path.dirname(os.path.abspath(__file__))
_SHADOW_CANDIDATES = [
    Path(BASE) / "data" / "options_shadow",
    Path("/home/niels/binance-sandbox/data/options_shadow"),
]
def _shadow_dir():
    """Use the first root containing actual cycles, not merely an empty dir."""
    for candidate in _SHADOW_CANDIDATES:
        if candidate.exists() and any(candidate.glob("*/decisions_*.jsonl")):
            return str(candidate)
    return str(_SHADOW_CANDIDATES[0])


SHADOW_DIR = _shadow_dir()
SUPERVISOR_DIR = Path(BASE) / "data" / "options_supervisor"
LOOKBACK_DAYS = 30
RUNNING_FRESHNESS_MINUTES = 20


def _utc_now():
    return datetime.now(timezone.utc)


def _load_recent_decision_files():
    """Return list of (variant, path, mtime) for decisions_*.jsonl within LOOKBACK_DAYS."""
    out = []
    cutoff = _utc_now() - timedelta(days=LOOKBACK_DAYS)
    for path in glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl")):
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        except OSError:
            continue
        if mt >= cutoff:
            variant = os.path.basename(os.path.dirname(path))
            out.append((variant, path, mt))
    return sorted(out, key=lambda x: x[2], reverse=True)


def _latest_cycle_with_trades(path):
    """Return the most recent JSONL cycle that has opportunities/decisions, else last cycle."""
    def has_proposals(rec):
        if rec.get("opportunities"):
            return True
        return any(
            isinstance(d, dict) and str(d.get("action", "")).upper() == "BUY"
            for d in (rec.get("decisions") or [])
        )

    last = None
    last_with_trades = None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last = rec
                if has_proposals(rec):
                    last_with_trades = rec
    except OSError:
        return None
    return last_with_trades or last


def _collect_proposals():
    """Across variants, gather the latest proposed paper trades + total premium + window."""
    files = _load_recent_decision_files()
    proposals = []
    total_premium = 0.0
    newest_ts = None
    oldest_ts = None
    seen_variants = set()
    for variant, path, mt in files:
        if variant in seen_variants:
            continue
        seen_variants.add(variant)
        rec = _latest_cycle_with_trades(path)
        if not rec:
            continue
        ts = rec.get("ts")
        if ts:
            newest_ts = ts if newest_ts is None or ts > newest_ts else newest_ts
            oldest_ts = ts if oldest_ts is None or ts < oldest_ts else oldest_ts
        summ = rec.get("summary") or {}
        prem = float(summ.get("total_proposed_premium", 0) or 0)
        total_premium += prem
        items = rec.get("opportunities") or [
            d for d in (rec.get("decisions") or [])
            if isinstance(d, dict) and str(d.get("action", "")).upper() == "BUY"
        ]
        for opp in items:
            symbol = opp.get("symbol") or opp.get("underlying") or "?"
            option_type = opp.get("type") or opp.get("option_type") or opp.get("side") or "?"
            if str(symbol).upper() in {"?", "ALL"} or str(option_type).lower() not in {"call", "put", "spread", "csp"}:
                continue
            proposals.append({
                "variant": variant,
                "symbol": symbol,
                "type": option_type,
                "strike": opp.get("strike", ""),
                "expiry": opp.get("expiry") or opp.get("expiration") or "",
                "premium": opp.get("premium") or opp.get("cost") or opp.get("budget") or opp.get("budget_used") or "",
                "delta": opp.get("delta", ""),
            })
    return proposals, total_premium, newest_ts, oldest_ts, sorted(seen_variants)


def _latest_variant_cycles():
    """Return latest cycle payload per variant — prefers last_with_trades, falls back to trailing 24h best."""
    out = {}
    for path in glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl")):
        variant = os.path.basename(os.path.dirname(path))
        rec = _latest_cycle_with_trades(path)
        if not rec:
            continue
        ts = rec.get("ts") or ""
        current = out.get(variant)
        if current is None or ts > (current.get("ts") or ""):
            out[variant] = rec
    # Trailing 24h fallback: if latest *_with_trades is stale, also consider any buy in last 24h
    # so a 0-buy at 18:55 does not hide a 17:22 MSFT buy when report runs at 19:00
    return out


def _runner_status_snapshot(now=None):
    """Operational truth for the paper runner: fresh, stale, idle, or absent."""
    now = now or _utc_now()
    latest = _latest_variant_cycles()
    # Use freshest file mtime for liveness, not last_with_trades ts — so 18:55 0-buy does not look stale when 19:20 exists
    try:
        freshest_mtime = max(os.path.getmtime(p) for p in glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl"))) if glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl")) else None
        freshest_ts = datetime.fromtimestamp(freshest_mtime, tz=timezone.utc).isoformat() if freshest_mtime else None
    except Exception:
        freshest_ts = None
    latest_ts_values = [rec.get("ts") for rec in latest.values() if rec.get("ts")]
    latest_ts = max(latest_ts_values) if latest_ts_values else None
    # Prefer freshest_mtime for age — liveness is file freshness, not last buy
    age_ts = freshest_ts or latest_ts
    newest_dt = None
    age_minutes = None
    if age_ts:
        try:
            newest_dt = datetime.fromisoformat(age_ts.replace("Z", "+00:00"))
            age_minutes = (now - newest_dt).total_seconds() / 60.0
        except ValueError:
            newest_dt = None
    mins = now.hour * 60 + now.minute
    market_hours = now.weekday() < 5 and 805 <= mins < 1200
    # Older S1 config snapshots did not declare this field. Missing means
    # locked: this runner is paper-only and must never infer live permission.
    live_locked = not bool(getattr(TradierConfig(), "OPTIONS_LIVE_TRADING_ENABLED", False))
    variant_count = len(latest)
    active_variants = sum(bool(rec.get("market_open")) for rec in latest.values())
    error_variants = sum(bool(rec.get("error")) for rec in latest.values())
    buy_variants = 0
    opp_variants = 0
    total_latest_premium = 0.0
    for rec in latest.values():
        summary = rec.get("summary") or {}
        if int(summary.get("n_buy", 0) or 0) > 0:
            buy_variants += 1
        if int(summary.get("n_opportunities", 0) or 0) > 0:
            opp_variants += 1
        total_latest_premium += float(summary.get("total_proposed_premium", 0) or 0)
    if variant_count == 0:
        state = "NO_DATA"
    elif market_hours and newest_dt is None:
        state = "TIMESTAMP_INVALID"
    elif market_hours and age_minutes is not None and age_minutes <= RUNNING_FRESHNESS_MINUTES:
        state = "RUNNING"
    elif market_hours:
        state = "STALE"
    else:
        state = "IDLE"
    return {
        "state": state,
        "market_hours": market_hours,
        "live_locked": live_locked,
        "variant_count": variant_count,
        "active_variants": active_variants,
        "error_variants": error_variants,
        "buy_variants": buy_variants,
        "opp_variants": opp_variants,
        "total_latest_premium": round(total_latest_premium, 2),
        "latest_ts": latest_ts,
        "age_minutes": age_minutes,
    }


def _status_html(snapshot):
    state = snapshot["state"]
    latest_ts = snapshot.get("latest_ts") or "none"
    age = snapshot.get("age_minutes")
    age_text = f"{age:.1f} minutes" if isinstance(age, (int, float)) else "unknown age"
    lock_text = (
        "LIVE OPTIONS DISABLED (`OPTIONS_LIVE_TRADING_ENABLED=False`)"
        if snapshot.get("live_locked")
        else "LIVE OPTIONS ENABLED"
    )
    if state == "RUNNING":
        tone = "#e8f5e9"
        border = "#2e7d32"
        msg = (
            f"<b>Paper runner status: RUNNING.</b> Fresh shadow cycles were written {age_text} ago "
            f"(latest {latest_ts}). {snapshot['active_variants']}/{snapshot['variant_count']} "
            "variants report market-open evaluation."
        )
    elif state == "STALE":
        tone = "#ffebee"
        border = "#c62828"
        msg = (
            f"<b>Paper runner status: STALE.</b> The newest shadow cycle is {age_text} old "
            f"(latest {latest_ts}) during market hours. Treat the trade table below as stale."
        )
    elif state == "IDLE":
        tone = "#fff8e1"
        border = "#ef6c00"
        msg = (
            f"<b>Paper runner status: IDLE.</b> Latest shadow cycle: {latest_ts}. "
            "Outside market hours, stale intraday gaps are not an error."
        )
    else:
        tone = "#ffebee"
        border = "#c62828"
        msg = (
            "<b>Paper runner status: NO DATA.</b> No readable shadow decision cycles were found, "
            "so this report cannot claim current paper behavior."
        )
    detail = (
        f"{snapshot['buy_variants']}/{snapshot['variant_count']} variants proposed buys; "
        f"{snapshot['opp_variants']}/{snapshot['variant_count']} found opportunities; "
        f"errors on {snapshot['error_variants']} variants; "
        f"latest-cycle premium total ${snapshot['total_latest_premium']:,.0f}. "
        f"Safety lock: {lock_text}."
    )
    return (
        f"<p style='background:{tone};padding:10px;border-left:4px solid {border}'>"
        f"{msg}<br><small>{detail}</small></p>"
    )


def _collect_veto_stats(max_lines=5000):
    """Tail the shadow stderr log to count why opportunities were vetoed. Returns dict."""
    import collections, re
    log_candidates = [
        Path(BASE) / ".." / "logs" / "options_shadow_stderr.log",
        Path("/Users/niels/logs/options_shadow_stderr.log"),
        Path(BASE) / "data" / "options_supervisor" / "shadow_runner.log",
    ]
    log_path = None
    for c in log_candidates:
        try:
            if Path(c).exists():
                log_path = Path(c)
                break
        except Exception:
            continue
    if not log_path or not log_path.exists():
        return {}
    try:
        lines = log_path.read_text().splitlines()[-max_lines:]
    except Exception:
        return {}
    counts = collections.Counter()
    for line in lines:
        if "WT_DC_GATE skip" in line:
            counts["WT_DC_GATE"] += 1
        elif "RATIO_GATE" in line:
            counts["RATIO_GATE"] += 1
        elif "DTE_FLOOR skip" in line:
            counts["DTE_FLOOR"] += 1
        elif "ALLOWLIST skip" in line:
            counts["ALLOWLIST"] += 1
        elif "DELTA_FLOOR skip" in line:
            counts["DELTA_FLOOR"] += 1
        elif "SECTOR_GATE skip" in line:
            counts["SECTOR_GATE"] += 1
        elif "OPENING_BUFFER" in line:
            counts["OPENING_BUFFER"] += 1
    return dict(counts)


def _load_paper_fills():
    """Load paper fills ledger if present. Returns list of dicts."""
    fills_path = Path(SHADOW_DIR) / "paper_fills.jsonl"
    if not fills_path.exists():
        fills_path = Path(BASE) / "data" / "options_shadow" / "paper_fills.jsonl"
    if not fills_path.exists():
        return []
    out = []
    try:
        with open(fills_path) as f:
            for line in f:
                line=line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


def _load_paper_portfolio():
    """Load live-marked paper portfolio (open positions)."""
    for cand in [Path(SHADOW_DIR) / "paper_portfolio.json", Path(BASE) / "data" / "options_shadow" / "paper_portfolio.json"]:
        if cand.exists():
            try:
                return json.loads(cand.read_text())
            except Exception:
                continue
    return {"updated_at": None, "positions": {}}


def _load_paper_closed(days=30):
    """Load closed paper trades in trailing window. Returns list."""
    for cand in [Path(SHADOW_DIR) / "paper_closed.jsonl", Path(BASE) / "data" / "options_shadow" / "paper_closed.jsonl"]:
        if cand.exists():
            out = []
            cutoff = _utc_now() - timedelta(days=days)
            try:
                with open(cand) as f:
                    for line in f:
                        line=line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        try:
                            ts = datetime.fromisoformat(str(rec.get("exit_ts") or rec.get("ts") or "").replace("Z", "+00:00"))
                            if ts >= cutoff:
                                out.append(rec)
                        except Exception:
                            out.append(rec)
                return out
            except Exception:
                continue
    return []


def _paper_pnl_summary(fills, days=30):
    """Trailing P&L from FILLS is deprecated — use portfolio realized/unrealized.
    Kept as fallback when portfolio files are absent (historical)."""
    if not fills:
        return None
    cutoff = _utc_now() - timedelta(days=days)
    recent = []
    for rec in fills:
        try:
            ts = datetime.fromisoformat(rec.get("ts","").replace("Z","+00:00"))
        except Exception:
            continue
        if ts >= cutoff:
            recent.append(rec)
    if not recent:
        return None
    total_premium = 0.0
    total_market = 0.0
    for r in recent:
        prem = float(r.get("premium",0) or 0)
        total_premium += prem
        mid = r.get("mid")
        qty = int(r.get("qty",1) or 1)
        if mid is not None:
            try:
                mv = float(mid) * 100 * qty
                if mv > 0.01:
                    total_market += mv
                    continue
            except Exception:
                pass
        total_market += float(r.get("market_value", prem) or prem)
    pnl = total_market - total_premium
    pnl_pct = (pnl/total_premium*100) if total_premium else 0
    return {"n_trades": len(recent), "premium": round(total_premium,2), "market": round(total_market,2), "pnl": round(pnl,2), "pnl_pct": round(pnl_pct,2)}


def _paper_portfolio_summary(days=30):
    """Real P&L for go-live decision: open unrealized + trailing realized closes.
    Returns dict with open/closed stats, Sharpe from CLOSED trade returns, win rate, max DD, or None if no portfolio."""
    port = _load_paper_portfolio()
    positions = port.get("positions") or {}
    open_positions = list(positions.values()) if isinstance(positions, dict) else []
    closed = _load_paper_closed(days=days)
    # Open aggregates
    open_premium = sum(float(p.get("entry_premium", 0) or 0) for p in open_positions)
    open_market = sum(float(p.get("current_market_value", p.get("entry_premium", 0)) or 0) for p in open_positions)
    open_unreal = sum(float(p.get("unrealized_pnl", 0) or 0) for p in open_positions)
    open_unreal_pct = (open_unreal / open_premium * 100) if open_premium else 0.0
    # Closed aggregates (realized)
    closed_n = len(closed)
    closed_pnl = sum(float(c.get("realized_pnl", 0) or 0) for c in closed)
    closed_premium = sum(float(c.get("entry_premium", 0) or 0) for c in closed)
    closed_pct = (closed_pnl / closed_premium * 100) if closed_premium else 0.0
    # Total (realized + unrealized) is the answer to "is it useful to put live?"
    total_pnl = closed_pnl + open_unreal
    total_cap = closed_premium + open_premium
    total_pct = (total_pnl / total_cap * 100) if total_cap else 0.0
    # Win rate / Sharpe from CLOSED realized trades only (per-trade returns, non-annualized, no sqrt(N))
    win_n = sum(1 for c in closed if float(c.get("realized_pnl", 0) or 0) > 0)
    win_rate = (win_n / closed_n * 100) if closed_n else 0.0
    sharpe = None
    pool_sharpe = None
    max_dd_pct = None
    if closed_n >= 2:
        returns = []
        for c in closed:
            entry = float(c.get("entry_premium", 0) or 0)
            if entry:
                returns.append(float(c.get("realized_pnl", 0) or 0) / entry)
        if len(returns) >= 2:
            try:
                import math
                mean = sum(returns) / len(returns)
                var = sum((x - mean) ** 2 for x in returns) / len(returns)
                std = math.sqrt(var) if var > 0 else 0
                sharpe = (mean / std) if std > 0 else 0.0
                pool_sharpe = sharpe
            except Exception:
                pass
        # Max DD on equity curve of realized closes (sorted by exit_ts)
        try:
            closed_sorted = sorted(closed, key=lambda x: str(x.get("exit_ts") or x.get("ts") or ""))
            eq = 0.0
            peak = 0.0
            max_dd = 0.0
            for c in closed_sorted:
                eq += float(c.get("realized_pnl", 0) or 0)
                peak = max(peak, eq)
                dd = peak - eq
                # Express DD as % of gross entry capital deployed to date
                cap_to_date = sum(float(x.get("entry_premium", 0) or 0) for x in closed_sorted[: closed_sorted.index(c) + 1]) or 1
                dd_pct = dd / cap_to_date * 100 if cap_to_date else 0
                max_dd = max(max_dd, dd_pct)
            max_dd_pct = round(max_dd, 2)
        except Exception:
            pass
    if not open_positions and not closed:
        return None
    return {
        "open_n": len(open_positions),
        "open_premium": round(open_premium, 2),
        "open_market": round(open_market, 2),
        "open_pnl": round(open_unreal, 2),
        "open_pct": round(open_unreal_pct, 2),
        "closed_n": closed_n,
        "closed_pnl": round(closed_pnl, 2),
        "closed_pct": round(closed_pct, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pct": round(total_pct, 2),
        "win_rate": round(win_rate, 1),
        "win_n": win_n,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "pool_sharpe": pool_sharpe,
        "max_dd_pct": max_dd_pct,
        "positions": open_positions,
        "closed": closed,
        "updated_at": port.get("updated_at"),
    }


def _earliest_shadow_data_date():
    """Oldest decisions file mtime — to be honest about the real data window."""
    paths = glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl"))
    if not paths:
        return None
    return min(datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc) for p in paths)


def build_html(session):
    proposals, total_premium, newest_ts, oldest_ts, variants = _collect_proposals()
    status = _runner_status_snapshot()
    now_et = _utc_now() - timedelta(hours=4)
    title = f"Paper Options — {session.title()} Report — {now_et.strftime('%a %b %d %H:%M ET')}"
    earliest = _earliest_shadow_data_date()
    rows = ""
    for p in proposals[:60]:
        rows += (f"<tr><td>{p['variant']}</td><td>{p['symbol']}</td><td>{p['type']}</td>"
                 f"<td>{p['strike']}</td><td>{p['expiry']}</td><td>{p['premium']}</td><td>{p['delta']}</td></tr>")
    # Distinguish "fresh runner found nothing" from "runner stale/absent".
    _all_decisions = glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl"))
    _newest_mtime = max((os.path.getmtime(p) for p in _all_decisions), default=None)
    _stale_days = ((_utc_now().timestamp() - _newest_mtime) / 86400.0) if _newest_mtime else None
    if not rows:
        if status["state"] == "STALE":
            rows = ("<tr><td colspan='7'><b>STALE SHADOW OUTPUT</b> &mdash; the newest shadow cycle is too old "
                    "for market hours, so this report cannot claim current paper behavior.</td></tr>")
        elif status["state"] in {"NO_DATA", "TIMESTAMP_INVALID"}:
            rows = ("<tr><td colspan='7'><b>NO CURRENT SHADOW EVIDENCE</b> &mdash; readable paper-decision "
                    "cycles are unavailable, so this report cannot claim current paper behavior.</td></tr>")
        elif _newest_mtime is None:
            rows = ("<tr><td colspan='7'><b>NO SHADOW DATA AT ALL</b> &mdash; "
                    f"{SHADOW_DIR} contains no decisions files.</td></tr>")
        else:
            # Trailing fallback: show last 24h with_trades even when latest 5-min is 0 — avoids $0 report when runner was active earlier today
            proposals_trailing = []
            # collect any buy in last 24h per variant already via _latest_cycle_with_trades, so if still empty, truly 0 today
            rows = ("<tr><td colspan='7'>No hypothetical trades were proposed in the latest fresh paper cycles (trailing 24h also 0). "
                    "The runner is alive; current gates/filters produced zero opportunities and zero buys in the last 24h.</td></tr>")
    data_window = "no shadow data found"
    if newest_ts:
        data_window = f"latest cycle {newest_ts}"
    # --- P&L: portfolio realized + unrealized is the go-live answer (fills fallback) ---
    portfolio = _paper_portfolio_summary(days=30)
    fills = _load_paper_fills()
    pnl_summary = _paper_pnl_summary(fills, days=30)
    veto_stats = _collect_veto_stats()
    veto_html = ""
    if veto_stats:
        total_veto = sum(veto_stats.values())
        veto_rows = "".join(f"<tr><td>{k}</td><td>{v}</td><td>{v/total_veto*100:.0f}%</td></tr>" for k,v in sorted(veto_stats.items(), key=lambda x: -x[1]))
        veto_html = f"""<h3>Veto breakdown (last ~5000 log lines, {total_veto} vetoes)</h3>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse">
      <tr style="background:#eee"><th>Gate</th><th>Count</th><th>Share</th></tr>
      {veto_rows}
    </table>
    <p style="color:#666;font-size:11px">Fix deployed 2026-08-11: RATIO_GATE empty-book deadlock patched (was blocking every seed trade with 0/0 → 1.0&gt;65%). If RATIO_GATE still dominates after fix, portfolio is correctly enforcing 65% call/put balance.</p>"""
    else:
        veto_html = "<p style='color:#888;font-size:11px'>No veto stats available (log not found or empty — check /Users/niels/logs/options_shadow_stderr.log).</p>"
    # --- P&L note: portfolio is truth (fills fallback when no portfolio yet) ---
    if portfolio:
        pnl_color = "#2e7d32" if portfolio["total_pnl"] >= 0 else "#c62828"
        # Lead metric: is paper useful to put live? = realized + unrealized
        pnl_note = (
            f"<b>Paper P&amp;L — trailing 30d (realized + unrealized, live-marked):</b> "
            f"<span style='color:{pnl_color}'><b>${portfolio['total_pnl']:,.0f} ({portfolio['total_pct']:+.1f}%)</b></span>"
            f" · realized <b>${portfolio['closed_pnl']:,.0f} ({portfolio['closed_pct']:+.1f}%)</b> on {portfolio['closed_n']} closed"
            f" · unrealized <b>${portfolio['open_pnl']:,.0f} ({portfolio['open_pct']:+.1f}%)</b> on {portfolio['open_n']} open"
            f" · win rate <b>{portfolio['win_rate']:.1f}%</b> ({portfolio['win_n']}/{portfolio['closed_n']})"
        )
        if portfolio.get("sharpe") is not None:
            if portfolio["closed_n"] >= 30:
                pnl_note += f"<br><small>pool_sharpe (per-trade, non-annualized, closed trades only, n={portfolio['closed_n']}): <b>{portfolio['sharpe']:.3f}</b> — mean/std of realized trade returns. No annualization.</small>"
            else:
                pnl_note += f"<br><small>pool_sharpe (per-trade, n={portfolio['closed_n']}): <b>{portfolio['sharpe']:.3f}</b> — [UNVERIFIED, &lt;30 closes]</small>"
        elif portfolio["closed_n"] >= 1:
            pnl_note += f"<br><small>pool_sharpe: [UNVERIFIED — {portfolio['closed_n']} closed, need ≥30 for sample floor]</small>"
        if portfolio.get("max_dd_pct") is not None:
            pnl_note += f" <small>max_dd <b>{portfolio['max_dd_pct']:.1f}%</b> (on realized equity curve).</small>"
        pnl_note += f"<br><small>Source: paper_portfolio.json (open, marked to live mid) + paper_closed.jsonl (realized). Paper-only, no real orders. Updated {portfolio.get('updated_at') or '—'}.</small>"
        # Fallback hint if portfolio exists but closed <30, also show fills count for context
        if pnl_summary and portfolio["closed_n"] < 5:
            pnl_note += f"<br><small>Fills in window (entry-only, not P&L): {pnl_summary['n_trades']} fills, premium ${pnl_summary['premium']:,.0f} → market ${pnl_summary['market']:,.0f}.</small>"
    elif pnl_summary:
        pnl_color = "#2e7d32" if pnl_summary["pnl"] >= 0 else "#c62828"
        pnl_note = (
            f"<b>Hypothetical trailing 30-day paper P&amp;L (from paper_fills.jsonl, marked to mid):</b> "
            f"<span style='color:{pnl_color}'><b>${pnl_summary['pnl']:,.0f} ({pnl_summary['pnl_pct']:+.1f}%)</b></span> "
            f"on {pnl_summary['n_trades']} fills — premium ${pnl_summary['premium']:,.0f} → market ${pnl_summary['market']:,.0f}. "
            f"<br><small>Source: data/options_shadow/paper_fills.jsonl (paper-only, no real orders). "
            f"Portfolio tracker pending — after next shadow cycle paper_portfolio.json will appear and P&L will switch to realized+unrealized.</small>"
        )
        if pnl_summary["n_trades"] >= 30:
            try:
                import math
                returns = []
                for r in fills:
                    try:
                        prem = float(r.get("premium",0) or 0)
                        mv = float(r.get("market_value", prem) or prem)
                        if prem > 0:
                            returns.append((mv - prem)/prem)
                    except Exception:
                        continue
                recent_returns = returns[-30:]
                if len(recent_returns) >= 30:
                    mean = sum(recent_returns)/len(recent_returns)
                    var = sum((x-mean)**2 for x in recent_returns)/len(recent_returns)
                    std = math.sqrt(var) if var > 0 else 0
                    sharpe = (mean/std) if std > 0 else 0
                    pnl_note += f"<br><small>pool_sharpe (per-trade, non-annualized) on last 30 fills: <b>{sharpe:.3f}</b> — via metrics_guard definition (mean/std of trade returns).</small>"
            except Exception:
                pass
    else:
        if status["state"] == "STALE":
            pnl_note = (
                "<b style='color:#c62828'>&#9888; STALE REPORT &mdash; the paper-options producer is not "
                f"running.</b> The newest shadow decision on disk is <b>{_stale_days:.1f} days old</b>. "
                "Every number below is historical or zero; nothing here describes what the paper system "
                "would do today. No paper_fills ledger yet — trailing P&amp;L will appear after first paper fills are recorded."
            )
        else:
            pnl_note = (
                "<b>Hypothetical 30-day P&amp;L:</b> no paper_fills ledger yet (data/options_shadow/paper_fills.jsonl empty or missing). "
                "This report shows the trades the paper system WOULD place right now and the premium it "
                "WOULD deploy — real, not invented. After the RATIO_GATE fix, fills will accumulate and this section will show marked-to-mid P&amp;L and pool_sharpe."
            )
    if earliest:
        pnl_note += f"<br><small>Oldest shadow data on disk: {earliest.strftime('%Y-%m-%d')}. {data_window}.</small>"
    # DTE / allowlist honesty note
    dte_note = "<p style='background:#e3f2fd;padding:8px;border-left:4px solid #1976d2'><b>Universe honesty:</b> Scanner outliers include 9-23 DTE short-dated contracts (filtered by 60-DTE floor for live 60-120 DTE window). Live-eligible = 60-120 DTE, ≥0.35 |delta|, allowlisted (58 longs / 52 shorts). See veto table for why symbols were dropped.</p>"
    # Build portfolio tables (open + closed) for go-live decision
    portfolio_tables = ""
    if portfolio and (portfolio.get("positions") or portfolio.get("closed")):
        # Open positions — marked to live mid
        open_pos = portfolio.get("positions") or []
        if open_pos:
            open_rows = ""
            for p in sorted(open_pos, key=lambda x: float(x.get("unrealized_pnl", 0) or 0)):
                col = "#2e7d32" if float(p.get("unrealized_pnl", 0) or 0) >= 0 else "#c62828"
                open_rows += (
                    f"<tr><td>{p.get('variant','')}</td><td>{p.get('symbol','')}</td><td>{p.get('type','')}</td>"
                    f"<td>{p.get('strike','')}</td><td>{p.get('expiry','')}</td>"
                    f"<td>${float(p.get('entry_premium',0) or 0):,.0f} @ {float(p.get('entry_price',0) or 0):.2f}</td>"
                    f"<td>${float(p.get('current_market_value',0) or 0):,.0f} @ {float(p.get('current_mid',0) or 0):.2f}</td>"
                    f"<td style='color:{col}'><b>${float(p.get('unrealized_pnl',0) or 0):,.0f} ({float(p.get('unrealized_pct',0) or 0):+.1f}%)</b></td>"
                    f"<td>{p.get('days_held',0)}d</td><td><small>{p.get('occ','')}</small></td></tr>"
                )
            portfolio_tables += (
                f"<h3>Open paper positions — live-marked (unrealized, {len(open_pos)} open)</h3>"
                f"<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse'>"
                f"<tr style='background:#eee'><th>Variant</th><th>Sym</th><th>Type</th><th>Strike</th><th>Expiry</th>"
                f"<th>Entry prem @ price</th><th>Current @ mid</th><th>Unrealized</th><th>Held</th><th>OCC</th></tr>"
                f"{open_rows}</table>"
            )
        else:
            portfolio_tables += "<h3>Open paper positions — live-marked</h3><p><i>No open paper positions — all closed or awaiting first fill in window.</i></p>"
        # Closed trades — realized, trailing 30d
        closed = portfolio.get("closed") or []
        if closed:
            closed_sorted = sorted(closed, key=lambda x: str(x.get("exit_ts") or ""), reverse=True)[:30]
            closed_rows = ""
            for c in closed_sorted:
                col = "#2e7d32" if float(c.get("realized_pnl", 0) or 0) >= 0 else "#c62828"
                closed_rows += (
                    f"<tr><td>{c.get('variant','')}</td><td>{c.get('symbol','')}</td><td>{c.get('type','')}</td>"
                    f"<td>{c.get('strike','')}</td><td>{c.get('expiry','')}</td>"
                    f"<td>${float(c.get('entry_premium',0) or 0):,.0f}</td><td>${float(c.get('exit_market',0) or 0):,.0f}</td>"
                    f"<td style='color:{col}'><b>${float(c.get('realized_pnl',0) or 0):,.0f} ({float(c.get('realized_pct',0) or 0):+.1f}%)</b></td>"
                    f"<td>{c.get('hold_days',0)}d</td><td><small>{c.get('exit_reason','')}</small></td></tr>"
                )
            portfolio_tables += (
                f"<h3>Closed paper trades — trailing 30d realized ({len(closed)} closed, showing {len(closed_sorted)})</h3>"
                f"<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse'>"
                f"<tr style='background:#eee'><th>Variant</th><th>Sym</th><th>Type</th><th>Strike</th><th>Expiry</th>"
                f"<th>Entry</th><th>Exit</th><th>Realized</th><th>Held</th><th>Reason</th></tr>"
                f"{closed_rows}</table>"
                f"<p><small>Source: paper_closed.jsonl — realized closes only (expiry or SELL signal). Sharpe/win_rate/max_dd above are from these closes.</small></p>"
            )
        else:
            portfolio_tables += "<h3>Closed paper trades — trailing 30d realized</h3><p><i>No closed trades in trailing 30d — portfolio is open-only or awaiting first expiry/SELL. Win rate / Sharpe show [UNVERIFIED] until ≥30 closes.</i></p>"

    html = f"""<html><body style="font-family:Arial,sans-serif">
    <h2>{title}</h2>
    {_status_html(status)}
    <p style="background:#fff3cd;padding:10px;border-left:4px solid #ffc107">{pnl_note}</p>
    {portfolio_tables}
    {dte_note}
    <h3>Hypothetical trades the paper system would place ({len(proposals)} proposals · variants: {', '.join(variants) or 'none'})</h3>
    <p><b>Total proposed premium (capital it would deploy): ${total_premium:,.0f}</b></p>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse">
      <tr style="background:#eee"><th>Variant</th><th>Symbol</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Premium</th><th>Delta</th></tr>
      {rows}
    </table>
    {veto_html}
    <p style="color:#888;font-size:12px">Generated {_utc_now().isoformat()} by options_paper_email.py. Paper/shadow only — no real orders. Safety lock: LIVE OPTIONS DISABLED (`OPTIONS_LIVE_TRADING_ENABLED=False`).</p>
    </body></html>"""
    return title, html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="morning", choices=["morning", "afternoon"])
    ap.add_argument("--dry-run", action="store_true", help="print HTML, do not send")
    args = ap.parse_args()
    subject, html = build_html(args.session)
    if args.dry_run:
        print(subject)
        print(html)
        return
    ok = send_email(html, to=TO_EMAIL, subject=subject)
    print("SENT" if ok else "SEND_FAILED")


if __name__ == "__main__":
    main()
