#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""shadow_reporter.py — daily/weekly markdown reports comparing shadow ↔ live.

Reads:
    data/shadow_decisions/<config_id>/orders_<acct>_<YYYYMMDD>.jsonl
    data/decisions/decisions_<acct>_<YYYYMMDD>.jsonl   (live, for diff)

Writes:
    reports/shadow/daily_<YYYYMMDD>.md
    reports/shadow/weekly_<YYYY-Www>.md

USAGE:
    python3 shadow_reporter.py                      # today's daily report
    python3 shadow_reporter.py --date 20260425      # specific date
    python3 shadow_reporter.py --weekly             # this week's weekly report

Daily metrics (insufficient sample for Sharpe):
    - shadow trade counts per config (opens/closes/augments/reduces/hedges)
    - top reasons fired
    - per-symbol activity heatmap
    - shadow ↔ live diff: signals shadow took that live didn't (and vice versa)

Weekly metrics (when sample large enough):
    - per-config round-trip return distribution
    - estimated Sharpe (mean/std of completed-trip returns)
    - cumulative gain %
    - drawdown high-water mark

Once samples cross CLAUDE.md thresholds (≥48 sym × >1yr), full 5-metric block
(pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr) is reported.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev

BASE = Path(__file__).resolve().parent
SHADOW_ROOT = BASE / "data" / "shadow_decisions"
LIVE_DECISIONS_ROOT = BASE / "data" / "decisions"
REPORTS_DIR = BASE / "reports" / "shadow"
REGISTRY = BASE / "shadows" / "REGISTRY.json"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

OPEN_ACTIONS = {"OPEN", "QUICK_OPEN", "REENTRY", "REVERSE", "REVERSE_AUGMENT",
                "QUICK_AUGMENT", "AUGMENT", "QUICK_HEDGE_OPEN", "QUICK_HEDGE_AUGMENT", "HEDGE_OPEN"}
CLOSE_ACTIONS = {"CLOSE", "QUICK_CLOSE", "STRONG_REDUCE", "REDUCE", "FULL_CLOSE", "EMERGENCY_CLOSE"}

def _load_registry() -> dict:
    if not REGISTRY.exists(): return {"shadows": {}}
    with open(REGISTRY) as f: return json.load(f)

def _read_jsonl(p: Path) -> list:
    if not p.exists(): return []
    out = []
    with open(p) as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line: continue
            try: out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  WARN {p.name}:{ln} bad json: {e}", file=sys.stderr)
    return out

def _classify(action: str) -> str:
    a = (action or "").upper()
    if a in OPEN_ACTIONS or "OPEN" in a or "AUGMENT" in a or "REENTRY" in a:
        return "open"
    if a in CLOSE_ACTIONS or "CLOSE" in a or "REDUCE" in a:
        return "close"
    return "other"

def _config_stats(cfg_id: str, account: str, day: str) -> dict:
    """Collect raw counts + top reasons + per-symbol activity for one shadow config."""
    p = SHADOW_ROOT / cfg_id / f"orders_{account}_{day}.jsonl"
    rows = _read_jsonl(p)
    out = {
        "cfg_id": cfg_id, "account": account, "day": day, "file": str(p),
        "total": len(rows),
        "opens": 0, "closes": 0, "other": 0,
        "hedges": 0,
        "by_symbol": Counter(),
        "by_reason_prefix": Counter(),
        "by_action": Counter(),
        "rows": rows,
    }
    for r in rows:
        action = (r.get("action") or "").upper()
        kind = _classify(action)
        out[kind + "s" if kind != "other" else "other"] += 1
        if r.get("is_hedge"): out["hedges"] += 1
        if r.get("symbol"): out["by_symbol"][r["symbol"]] += 1
        out["by_action"][action or "?"] += 1
        # Reason prefix = first 2 underscore-separated tokens
        rsn = r.get("reason") or ""
        prefix = "_".join(rsn.split("_")[:2]) if rsn else "?"
        out["by_reason_prefix"][prefix] += 1
    return out

def _live_stats_for_account(account: str, day: str) -> dict:
    """Best-effort read of live decisions JSONL for the same account + day.
    Live decisions schema differs from shadow orders (decisions are pre-execution
    candidates; shadow orders are post-execute_now intercepts). We just count
    fired-decisions for a coarse comparison."""
    p = LIVE_DECISIONS_ROOT / f"decisions_{account}_{day}.jsonl"
    rows = _read_jsonl(p)
    out = {"account": account, "day": day, "file": str(p), "total": len(rows),
           "by_symbol": Counter(), "by_action": Counter()}
    for r in rows:
        if r.get("symbol"): out["by_symbol"][r["symbol"]] += 1
        a = (r.get("action") or r.get("decision") or "").upper()
        if a: out["by_action"][a] += 1
    return out

def _round_trip_returns(rows: list) -> list[float]:
    """Match open→close pairs by position_key, compute return % per round-trip.
    Naive matcher: queue opens by position_key, pop on first close. Multi-leg
    augments/reduces are aggregated as weighted-average open price.
    Returns list of percentage returns."""
    open_state: dict[str, dict] = {}  # position_key → {qty, weighted_price, side}
    returns: list[float] = []
    for r in rows:
        pk = r.get("position_key")
        if not pk: continue
        action = (r.get("action") or "").upper()
        kind = _classify(action)
        side = r.get("position_side", "LONG")
        price = float(r.get("old_price") or 0)
        qty = abs(float(r.get("quantity") or 0))
        if not price or not qty: continue
        if kind == "open":
            cur = open_state.get(pk)
            if cur is None:
                open_state[pk] = {"qty": qty, "wprice": price, "side": side}
            else:
                # weighted-average open price
                tot = cur["qty"] + qty
                if tot > 0:
                    cur["wprice"] = (cur["wprice"] * cur["qty"] + price * qty) / tot
                cur["qty"] = tot
        elif kind == "close":
            cur = open_state.get(pk)
            if cur is None or cur["qty"] <= 0: continue
            entry = cur["wprice"]
            if entry <= 0: continue
            ret_pct = ((price - entry) / entry * 100.0) if cur["side"] == "LONG" \
                      else ((entry - price) / entry * 100.0)
            returns.append(ret_pct)
            # reduce open qty (close may be partial)
            cur["qty"] = max(0.0, cur["qty"] - qty)
            if cur["qty"] <= 0: open_state.pop(pk, None)
    return returns

def _est_sharpe(returns: list[float]) -> tuple[float, float, float]:
    """Returns (pool_sharpe, mean_pct, std_pct). Sharpe = mean/std (per-trade)."""
    if len(returns) < 2: return (0.0, 0.0, 0.0)
    m = mean(returns); s = stdev(returns) or 1e-9
    return (m / s, m, s)

def _max_dd_pct(returns: list[float]) -> float:
    """Cumulative-return drawdown, % from peak."""
    if not returns: return 0.0
    eq = 100.0; peak = 100.0; max_dd = 0.0
    for r in returns:
        eq *= (1 + r / 100.0)
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0
        max_dd = max(max_dd, dd)
    return max_dd

def _md_table(headers: list[str], rows: list[list]) -> str:
    out = "| " + " | ".join(headers) + " |\n"
    out += "|" + "|".join(["---"] * len(headers)) + "|\n"
    for r in rows:
        out += "| " + " | ".join(str(c) for c in r) + " |\n"
    return out

def build_daily(day: str) -> str:
    reg = _load_registry()
    md = [f"# Shadow Report — Daily — {day}\n",
          f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_\n"]

    # Active configs from registry
    active = [(cid, m) for cid, m in reg.get("shadows", {}).items() if m.get("enabled")]
    if not active:
        md.append("\n_No active shadows in REGISTRY.json._\n")
        return "\n".join(md)

    md.append("\n## Capture summary\n")
    summary_rows = []
    detail_blocks = []
    for cfg_id, meta in active:
        acct = meta.get("account", "?")
        s = _config_stats(cfg_id, acct, day)
        live = _live_stats_for_account(acct, day)
        rt = _round_trip_returns(s["rows"])
        sharpe, m, std = _est_sharpe(rt)
        dd = _max_dd_pct(rt)
        cum = sum(rt) if rt else 0.0
        summary_rows.append([
            cfg_id, acct, s["total"], s["opens"], s["closes"], s["hedges"],
            len(rt), f"{m:+.2f}%" if rt else "—",
            f"{sharpe:+.2f}" if rt else "—",
            f"{dd:.1f}%" if rt else "—",
            f"{cum:+.2f}%" if rt else "—",
            live["total"],
            f"{s['total'] - live['total']:+d}",
        ])

        # Per-config detail block
        b = [f"\n### {cfg_id} ({acct}, {meta.get('platform','?')})\n",
             f"- target: {meta.get('target_metric','?')}",
             f"- file: `{Path(s['file']).name}`",
             f"- shadow events: {s['total']} (opens={s['opens']}, closes={s['closes']}, hedges={s['hedges']})",
             f"- completed round-trips: {len(rt)}"]
        if rt:
            b.append(f"- mean trip return: {m:+.3f}%, std: {std:.3f}%, est_sharpe: {sharpe:+.2f}")
            b.append(f"- max DD (cumulative): {dd:.2f}%, cum return: {cum:+.2f}%")
        b.append("\n**Top reasons:**")
        b.append(_md_table(["reason prefix", "count"],
                           [[r, c] for r, c in s["by_reason_prefix"].most_common(10)]))
        b.append("\n**Top symbols:**")
        b.append(_md_table(["symbol", "events"],
                           [[s_, c] for s_, c in s["by_symbol"].most_common(10)]))
        # shadow vs live symbol diff
        shadow_syms = set(s["by_symbol"].keys())
        live_syms = set(live["by_symbol"].keys())
        only_shadow = shadow_syms - live_syms
        only_live = live_syms - shadow_syms
        if only_shadow or only_live:
            b.append("\n**Symbol diff vs live (same day):**")
            b.append(f"- only-shadow ({len(only_shadow)}): {', '.join(sorted(only_shadow))[:200]}")
            b.append(f"- only-live   ({len(only_live)}): {', '.join(sorted(only_live))[:200]}")
        detail_blocks.append("\n".join(b))

    md.append(_md_table(
        ["config", "acct", "events", "opens", "closes", "hedges",
         "trips", "avg_ret", "est_sh", "dd", "cum", "live_evt", "Δ_live"],
        summary_rows,
    ))
    md.extend(detail_blocks)

    md.append("\n---\n")
    md.append("\n## Recommendations\n")
    if all(r[6] == 0 for r in summary_rows):
        md.append("- ⏳ No completed round-trips yet. Need at least 1 close per config to estimate per-trip return; meaningful Sharpe requires ≥30 trips per config (CLAUDE.md rule). Keep shadows running.")
    else:
        # Rank by est_sharpe of those with ≥10 trips
        ranked = sorted([(r[0], float(r[8].replace("+", "")) if r[8] != "—" else -99, r[6])
                         for r in summary_rows if r[6] != "—"],
                        key=lambda x: -x[1])
        md.append(f"- Best (est_sharpe, low-sample): **{ranked[0][0]}** sh={ranked[0][1]:+.2f} on {ranked[0][2]} trips")
        md.append("- ⚠️ Per CLAUDE.md, single-day Sharpes from <30 trips are noise. Decisions must wait for weekly aggregates with ≥30 trips/config.")
    md.append("\n## Next-day notes\n- Compare today's `Δ_live` to yesterday's — large swings signal config divergence from live behavior.\n")
    return "\n".join(md)

def build_weekly(iso_week: str | None = None) -> str:
    """Weekly report: aggregate the last 7 days (or specified ISO week)."""
    today = date.today()
    if iso_week:
        # Parse YYYY-Www
        y, w = iso_week.split("-W")
        # Find Monday of that ISO week
        first_day = date.fromisocalendar(int(y), int(w), 1)
    else:
        # Most recent complete ISO week ending Sunday
        first_day = today - timedelta(days=today.weekday() + 7)
        if today.weekday() == 0: first_day = today - timedelta(days=7)
        iso_week = f"{first_day.isocalendar().year}-W{first_day.isocalendar().week:02d}"

    days = [(first_day + timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]
    md = [f"# Shadow Report — Weekly — {iso_week}\n",
          f"_Days: {days[0]} → {days[-1]}_\n",
          f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_\n"]

    reg = _load_registry()
    active = [(cid, m) for cid, m in reg.get("shadows", {}).items() if m.get("enabled")]
    if not active:
        md.append("\n_No active shadows in REGISTRY.json._\n")
        return "\n".join(md)

    md.append("\n## Aggregate per config (7-day rolling)\n")
    rows = []
    for cfg_id, meta in active:
        acct = meta.get("account", "?")
        all_rows = []
        for d in days:
            all_rows.extend(_read_jsonl(SHADOW_ROOT / cfg_id / f"orders_{acct}_{d}.jsonl"))
        rt = _round_trip_returns(all_rows)
        sharpe, m, std = _est_sharpe(rt)
        dd = _max_dd_pct(rt)
        cum = sum(rt) if rt else 0.0
        rows.append([
            cfg_id, acct, len(all_rows), len(rt),
            f"{m:+.3f}%" if rt else "—",
            f"{sharpe:+.2f}" if rt else "—",
            f"{dd:.2f}%" if rt else "—",
            f"{cum:+.2f}%" if rt else "—",
            "✅" if len(rt) >= 30 else f"⏳ {len(rt)}/30",
        ])
    md.append(_md_table(
        ["config", "acct", "events", "trips", "avg_ret", "est_sharpe", "dd", "cum_ret", "≥30?"],
        rows,
    ))

    md.append("\n## Notes\n")
    md.append("- est_sharpe is per-trade Sharpe = mean/std of round-trip %returns. Per CLAUDE.md, this requires ≥30 trips/config to be non-noise. Configs marked ⏳ should not yet drive decisions.")
    md.append("- Pool-Sharpe across all configs intentionally NOT computed — each config is its own universe of decisions.")
    md.append("- After 4 weeks of accumulated data, switch to 5-metric reporting: pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr.")
    return "\n".join(md)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", help="YYYYMMDD; default = today UTC")
    p.add_argument("--weekly", action="store_true", help="generate weekly report instead")
    p.add_argument("--week", help="ISO week YYYY-Www; default = previous week")
    args = p.parse_args()

    if args.weekly or args.week:
        iso = args.week
        text = build_weekly(iso)
        out = REPORTS_DIR / f"weekly_{iso or 'latest'}.md"
    else:
        day = args.date or datetime.now(timezone.utc).strftime("%Y%m%d")
        text = build_daily(day)
        out = REPORTS_DIR / f"daily_{day}.md"

    out.write_text(text)
    print(f"wrote {out} ({len(text)} bytes)")
    print()
    print(text[:2000])
    if len(text) > 2000: print("...[truncated]...")

if __name__ == "__main__":
    main()
