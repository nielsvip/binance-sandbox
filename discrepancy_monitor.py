#!/usr/bin/env python3
"""discrepancy_monitor — flags live trb trades that diverge from backtest expectations.

Run this at end-of-day or from cron after market close.

Reads from data/tradier/history/{account}/{sym}_{side}.jsonl (ACTUAL executions).
NOT from data/decisions/ (which records evaluations that may be filtered pre-execution).

Four discrepancy classes detected:

  WRONG_SIDE     — trade is on the opposite side from per-sym winner config
                   (e.g. LONG trade on a symbol whose winner is SHORT_ONLY)

  RATE_BLOAT     — live OPEN count >> backtest expectation
                   (per-sym winner has N trades / 2yr → expected daily ~N/500)
                   Threshold: live_opens > 5× expected_daily

  NEG_WSHARPE    — symbol×side has wsharpe < 0 in active_config.json at time of trade
                   (7D agent said this is a bad period; live opened anyway)

  UNMODELED_EXIT — exit reason is DELTA_EXIT_TOP / SMART_RZ_EXIT (wt_dc_delta.py)
                   This path is NOT simulated by v8_quick_engine; outcomes are blind spots.

Usage:
  python3 discrepancy_monitor.py                     # today
  python3 discrepancy_monitor.py --days 3            # last 3 days
  python3 discrepancy_monitor.py --account trb       # default
  python3 discrepancy_monitor.py --account trc       # paper baseline
  python3 discrepancy_monitor.py --alert-only        # print only flagged discrepancies
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
HISTORY_DIR = ROOT / "data" / "tradier" / "history"
ACTIVE_CFG_TRB = ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json"
ACTIVE_CFG_TRC = ROOT / "data" / "hourly_reconfig" / "trc" / "active_config.json"
CAND_DIR_TRB = ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates"

RATE_BLOAT_FACTOR = 5.0
BACKTEST_YEARS = 2.0
TRADING_DAYS_PER_YEAR = 250.0

UNMODELED_EXIT_PREFIXES = (
    "DELTA_EXIT_TOP",
    "DELTA_EXIT_TRANSIT",
    "SMART_RZ_EXIT",
)


def load_per_sym_configs(cand_dir: Path) -> Dict[str, Dict]:
    """Returns {sym -> {side, pool, total_gain, dd, trades}}"""
    configs = {}
    if not cand_dir.exists():
        return configs
    for p in cand_dir.glob("trb_*_winner.json"):
        try:
            d = json.loads(p.read_text())
            stem = p.stem
            parts = stem.split("_")
            sym = parts[1]
            side = "_".join(parts[2:-1])
            configs[sym] = {
                "side": side,
                "pool": d.get("_pool_sharpe", 0.0),
                "total_gain": d.get("_total_gain_pct", 0.0),
                "dd": d.get("_dd", 0.0),
                "trades": d.get("_trades", 0),
            }
        except Exception:
            pass
    return configs


def load_active_config(path: Path) -> Dict[str, Dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def expected_daily_trades(backtest_trades: int, years: float = BACKTEST_YEARS) -> float:
    days = years * TRADING_DAYS_PER_YEAR
    return backtest_trades / days if days > 0 else 0.0


def is_wrong_side(live_side: str, winner_side: str) -> bool:
    if winner_side == "BOTH":
        return False
    if winner_side == "LONG_ONLY" and live_side == "SHORT":
        return True
    if winner_side == "SHORT_ONLY" and live_side == "LONG":
        return True
    return False


def is_options(stem: str) -> bool:
    return any(x in stem for x in ("P000", "C000", "260618", "260919"))


def load_history_trades(account: str, dates: List[str]) -> List[Dict]:
    """Load actual executed trades from data/tradier/history/{account}/*.jsonl.

    Returns list of dicts with keys: sym, side, type, ts, price, qty, reason, gain_pct
    """
    hist_dir = HISTORY_DIR / account
    if not hist_dir.exists():
        return []

    date_set = set(dates)
    records = []

    for p in hist_dir.glob("*.jsonl"):
        stem = p.stem
        if is_options(stem):
            continue
        # stem format: SYM_SIDE or SYM_SYM_SIDE (e.g. AAPL_LONG, CL260618C00050000_LONG)
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        sym, side = parts[0], parts[1]
        if side not in ("LONG", "SHORT"):
            continue

        try:
            with p.open() as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        ts_str = d.get("ts", "")
                        if not ts_str:
                            continue
                        # Filter by date: ts format "2026-05-01T22:36:..."
                        date_part = ts_str[:10].replace("-", "")
                        if date_part not in date_set:
                            continue

                        rec_type = d.get("type", "")
                        price = float(d.get("price", 0) or 0)
                        qty = float(d.get("qty", 0) or 0)
                        reason = d.get("reason", "")[:100]

                        # Gain pct: from broker_sync (authoritative), or parse g= from reason string
                        gain_pct = None
                        if "broker_gain_loss_pct" in d:
                            gain_pct = float(d["broker_gain_loss_pct"])
                        if gain_pct is None and reason:
                            import re as _re
                            m = _re.search(r"_g=([+-]?\d+\.?\d*)%", reason)
                            if m:
                                try:
                                    gain_pct = float(m.group(1))
                                except Exception:
                                    pass

                        records.append({
                            "sym": sym,
                            "side": side,
                            "type": rec_type,
                            "ts": ts_str[:19],
                            "price": price,
                            "qty": qty,
                            "reason": reason,
                            "gain_pct": gain_pct,
                            "pk": f"{account}:{sym}_{side}",
                        })
                    except Exception:
                        pass
        except Exception:
            pass

    return records


def run_analysis(account: str, days: int, alert_only: bool) -> None:
    active_cfg_path = ACTIVE_CFG_TRB if account == "trb" else ACTIVE_CFG_TRC

    today = datetime.now(tz=timezone.utc)
    date_strs = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]

    print(f"\n{'='*72}")
    print(f"discrepancy_monitor — account={account} days={days}")
    print(f"Source: data/tradier/history/{account}/ (ACTUAL executions)")
    print(f"{'='*72}")

    per_sym = load_per_sym_configs(CAND_DIR_TRB)
    active_cfg = load_active_config(active_cfg_path)
    recs = load_history_trades(account, date_strs)

    if not recs:
        print("No history records found for the requested date range.")
        return

    # Separate by record type
    opens_by_sym: Dict[str, List[Dict]] = defaultdict(list)
    closes_by_sym: Dict[str, List[Dict]] = defaultdict(list)

    for r in recs:
        sym = r["sym"]
        rec_type = r["type"]
        if rec_type == "OPEN":
            opens_by_sym[sym].append(r)
        elif rec_type in ("CLOSE", "BROKER_CLOSE", "GHOST_CLOSE"):
            closes_by_sym[sym].append(r)

    daily_opens: Dict[str, int] = {sym: len(v) for sym, v in opens_by_sym.items()}
    daily_closes: Dict[str, int] = {sym: len(v) for sym, v in closes_by_sym.items()}

    flags: List[Dict] = []

    # ── 1. WRONG_SIDE + RATE_BLOAT ──────────────────────────────────────────
    all_syms = set(daily_opens.keys()) | set(daily_closes.keys())
    for sym in sorted(all_syms):
        cfg = per_sym.get(sym)
        if not cfg:
            continue

        winner_side = cfg["side"]
        bt_trades = cfg["trades"]
        expected_day = expected_daily_trades(bt_trades)

        live_opens = daily_opens.get(sym, 0)

        # RATE_BLOAT based on actual OPENs
        if expected_day > 0 and live_opens > expected_day * RATE_BLOAT_FACTOR:
            flags.append({
                "type": "RATE_BLOAT",
                "sym": sym,
                "winner_side": winner_side,
                "live_opens": live_opens,
                "expected_daily": f"{expected_day:.1f}",
                "ratio": f"{live_opens / expected_day:.0f}×",
                "detail": f"live_opens={live_opens} vs expected~{expected_day:.1f}/day "
                          f"(bt_trades={bt_trades}/{BACKTEST_YEARS:.0f}yr)",
            })

        # WRONG_SIDE based on actual OPENs
        for rec in opens_by_sym.get(sym, []):
            if is_wrong_side(rec["side"], winner_side):
                flags.append({
                    "type": "WRONG_SIDE",
                    "sym": sym,
                    "live_side": rec["side"],
                    "winner_side": winner_side,
                    "ts": rec["ts"],
                    "reason": rec["reason"],
                    "detail": f"winner={winner_side} but opened {rec['side']} @ ${rec['price']:.2f}",
                })

    # ── 2. NEG_WSHARPE ──────────────────────────────────────────────────────
    for sym in sorted(all_syms):
        for side_sfx in ("LONG", "SHORT"):
            key = f"{sym}_{side_sfx}"
            entry = active_cfg.get(key)
            if not entry:
                continue
            wsharpe = entry.get("wsharpe", 0.0)
            if wsharpe < 0:
                live_opens = sum(1 for r in opens_by_sym.get(sym, []) if r["side"] == side_sfx)
                if live_opens > 0:
                    flags.append({
                        "type": "NEG_WSHARPE",
                        "sym": sym,
                        "side": side_sfx,
                        "wsharpe": wsharpe,
                        "live_opens": live_opens,
                        "detail": f"active_config wsharpe={wsharpe:+.4f} (7D-agent: bad period) "
                                  f"but {live_opens} OPENs fired",
                    })

    # ── 3. UNMODELED_EXIT ───────────────────────────────────────────────────
    unmodeled: List[Dict] = []
    for sym, recs_list in closes_by_sym.items():
        for rec in recs_list:
            reason = rec["reason"]
            if any(reason.startswith(pfx) for pfx in UNMODELED_EXIT_PREFIXES):
                unmodeled.append(rec)

    if unmodeled:
        by_sym: Dict[str, List] = defaultdict(list)
        for rec in unmodeled:
            by_sym[rec["sym"]].append(rec)
        for sym, sym_recs in sorted(by_sym.items()):
            n = len(sym_recs)
            # Use broker_gain_loss_pct where available
            gains = [r["gain_pct"] for r in sym_recs if r["gain_pct"] is not None]
            gain_str = f"total_loss={sum(gains):+.1f}%" if gains else "gain=n/a"
            flags.append({
                "type": "UNMODELED_EXIT",
                "sym": sym,
                "n_exits": n,
                "gain_str": gain_str,
                "example_reason": sym_recs[0]["reason"][:60],
                "detail": f"{n} close(s) via DELTA_EXIT/SMART_RZ (NOT in backtest). "
                          f"{gain_str}",
            })

    # ── Filter alert-only ─────────────────────────────────────────────────────
    if alert_only:
        flags = [f for f in flags if f["type"] in ("WRONG_SIDE", "UNMODELED_EXIT")
                 or (f["type"] == "RATE_BLOAT")
                 or (f["type"] == "NEG_WSHARPE")]

    # ── Print report ─────────────────────────────────────────────────────────
    if not flags:
        print("✓ No discrepancies detected.")
    else:
        by_type: Dict[str, List] = defaultdict(list)
        for f in flags:
            by_type[f["type"]].append(f)

        for ftype in ("WRONG_SIDE", "RATE_BLOAT", "NEG_WSHARPE", "UNMODELED_EXIT"):
            items = by_type.get(ftype, [])
            if not items:
                continue
            print(f"\n{'─'*72}")
            print(f"  {ftype}  ({len(items)} flags)")
            print(f"{'─'*72}")
            for f in items:
                print(f"  {f['sym']:8s}  {f['detail']}")

    # ── Summary stats ─────────────────────────────────────────────────────────
    total_opens = sum(daily_opens.values())
    total_closes = sum(daily_closes.values())
    n_syms_opened = len(daily_opens)
    print(f"\n{'─'*72}")
    print(f"  Summary: {total_opens} opens / {total_closes} closes across {n_syms_opened} symbols")
    print(f"  Discrepancy flags: {len(flags)}")
    print(f"  Date range: {date_strs[-1]}–{date_strs[0]}")
    print(f"{'='*72}")

    print("\n  ENGINE GAPS (structural, not per-trade flags):")
    print("  1. DELTA_EXIT_TOP / SMART_RZ_EXIT — live exit, not in v8_quick_engine")
    print("     WT_CROSSUNDER is the exit the backtest tests; live also uses DELTA_ENGINE")
    print("  2. PEAK_GIVEBACK / BE_EROSION — live exits, off by default in backtest")
    print("     Set USE_PROCESS_POSITION_EXIT_GATES=True in backtest to approximate")
    print("  3. Entry: live uses WT_DC_ENTRY scorer; backtest uses vectorized WT gate")
    print("     Match is ~85% — live fires more frequently at lower score thresholds")
    print("  4. Rate guard disabled in backtest → live trade frequency may be lower")
    print("  5. wt_velocity_1h zero-filled in tradier NPZs → B11/B15 reentry dormant")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="trb", choices=["trb", "trc"])
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--alert-only", action="store_true")
    args = ap.parse_args()
    run_analysis(args.account, args.days, args.alert_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
