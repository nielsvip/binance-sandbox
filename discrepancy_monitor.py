#!/usr/bin/env python3
"""discrepancy_monitor — flags live trb trades that diverge from backtest expectations.

Run this at end-of-day or from cron after market close.

Four discrepancy classes detected:

  WRONG_SIDE     — trade is on the opposite side from per-sym winner config
                   (e.g. LONG trade on a symbol whose winner is SHORT_ONLY)

  RATE_BLOAT     — live trade count >> backtest expectation
                   (per-sym winner has N trades / 2yr → expected daily ~N/500)
                   Threshold: live_count > 5× expected_daily

  NEG_WSHARPE    — symbol×side has wsharpe < 0 in active_config.json at time of trade
                   (7D agent said this is a bad period; live traded anyway)

  UNMODELED_EXIT — exit reason is DELTA_EXIT_TOP / SMART_RZ_EXIT (wt_dc_delta.py)
                   This path is NOT simulated by v8_quick_engine; outcomes are blind spots.
                   Any loss from this exit is in a regime the backtest cannot predict.

Usage:
  python3 discrepancy_monitor.py                     # today
  python3 discrepancy_monitor.py --days 3            # last 3 days
  python3 discrepancy_monitor.py --account trb       # default
  python3 discrepancy_monitor.py --account trc       # paper baseline
  python3 discrepancy_monitor.py --alert-only        # print only loss-bearing discrepancies
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
DECISIONS_DIR = ROOT / "data" / "decisions"
ACTIVE_CFG_TRB = ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json"
ACTIVE_CFG_TRC = ROOT / "data" / "hourly_reconfig" / "trc" / "active_config.json"
CAND_DIR_TRB = ROOT / "data" / "hourly_reconfig" / "trb" / "_candidates"

RATE_BLOAT_FACTOR = 5.0
BACKTEST_YEARS = 2.0
TRADING_DAYS_PER_YEAR = 250.0

# Exits that v8_quick_engine does NOT simulate (wt_dc_delta.py paths)
UNMODELED_EXIT_PREFIXES = (
    "DELTA_EXIT_TOP",
    "DELTA_EXIT_TRANSIT",
    "SMART_RZ_EXIT",
)

# Entry labels from backtest (these map to WT_DC_ENTRY in v8_quick)
MODELED_ENTRY_PREFIXES = (
    "WT_DC_ENTRY",
    "WT_CROSS",
    "DC_BREAKOUT",
    "STDEV_BREAKOUT",
    "STDEV_BOUNCE",
    "BOUNCE",
    "BREAKOUT",
)


def load_per_sym_configs(cand_dir: Path) -> Dict[str, Dict]:
    """Returns {sym -> {side: "SHORT_ONLY"/"LONG_ONLY"/"BOTH", pool, total_gain, dd, trades}}"""
    configs = {}
    if not cand_dir.exists():
        return configs
    for p in cand_dir.glob("trb_*_winner.json"):
        try:
            d = json.loads(p.read_text())
            stem = p.stem  # trb_SYM_SIDE_winner
            parts = stem.split("_")
            sym = parts[1]
            side = "_".join(parts[2:-1])  # LONG_ONLY / SHORT_ONLY / BOTH
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
    """Returns {SYM_LONG or SYM_SHORT -> {wsharpe, trades, ...}}"""
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
    """True if live traded the opposite side from winner config."""
    if winner_side == "BOTH":
        return False
    if winner_side == "LONG_ONLY" and live_side == "SHORT":
        return True
    if winner_side == "SHORT_ONLY" and live_side == "LONG":
        return True
    return False


def load_decisions(account: str, dates: List[str]) -> List[Dict]:
    recs = []
    for date in dates:
        path = DECISIONS_DIR / f"decisions_{account}_{date}.jsonl"
        if not path.exists():
            continue
        with path.open() as f:
            for line in f:
                try:
                    d = json.loads(line)
                    recs.append(d)
                except Exception:
                    pass
    return recs


def is_options(pk: str) -> bool:
    return any(x in pk for x in ("P000", "C000", "260618", "260919"))


def parse_pk(pk: str) -> Tuple[str, str]:
    """Returns (sym, side) from 'trb:AAPL_LONG' or 'trb:AAPL_SHORT'."""
    raw = pk.split(":")[-1]
    if raw.endswith("_LONG"):
        return raw[:-5], "LONG"
    if raw.endswith("_SHORT"):
        return raw[:-6], "SHORT"
    return raw, "UNKNOWN"


def run_analysis(account: str, days: int, alert_only: bool) -> None:
    active_cfg_path = ACTIVE_CFG_TRB if account == "trb" else ACTIVE_CFG_TRC

    today = datetime.now(tz=timezone.utc)
    date_strs = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]

    print(f"\n{'='*72}")
    print(f"discrepancy_monitor — account={account} days={days}")
    print(f"{'='*72}")

    per_sym = load_per_sym_configs(CAND_DIR_TRB)
    active_cfg = load_active_config(active_cfg_path)
    recs = load_decisions(account, date_strs)

    if not recs:
        print("No decision records found.")
        return

    # Collect open and close trades per symbol
    daily_opens: Dict[str, int] = defaultdict(int)   # sym -> count opened today
    daily_closes: Dict[str, int] = defaultdict(int)  # sym -> count closed today
    close_pnl: Dict[str, List[float]] = defaultdict(list)  # sym -> [pnl]
    close_records: List[Dict] = []

    for d in recs:
        pk = d.get("position_key", "")
        if is_options(pk):
            continue
        sym, side = parse_pk(pk)
        act = d.get("action", "")
        trade = d.get("trade", {})
        gain = trade.get("gain_pct")

        if "BUY" in act or "SELL" in act:
            if "CLOSE" not in act:
                daily_opens[sym] += 1

        if "CLOSE" in act and gain is not None:
            daily_closes[sym] += 1
            close_pnl[sym].append(float(gain))
            close_records.append({
                "sym": sym, "side": side, "gain": float(gain),
                "reason": d.get("reason_text", "")[:80],
                "ts": d.get("timestamp", "")[:19],
                "pk": pk,
            })

    # Build discrepancy report
    flags: List[Dict] = []

    # ── 1. WRONG_SIDE + RATE_BLOAT ──────────────────────────────────────────
    all_syms = set(daily_closes.keys()) | set(daily_opens.keys())
    for sym in sorted(all_syms):
        cfg = per_sym.get(sym)
        if not cfg:
            continue

        winner_side = cfg["side"]
        bt_trades = cfg["trades"]
        expected_day = expected_daily_trades(bt_trades)

        live_n = daily_closes.get(sym, 0)
        pnl_today = sum(close_pnl.get(sym, []))
        pnl_list = close_pnl.get(sym, [])

        # RATE_BLOAT
        if expected_day > 0 and live_n > expected_day * RATE_BLOAT_FACTOR:
            flags.append({
                "type": "RATE_BLOAT",
                "sym": sym,
                "winner_side": winner_side,
                "live_closes": live_n,
                "expected_daily": f"{expected_day:.1f}",
                "ratio": f"{live_n / expected_day:.0f}×",
                "pnl_today": pnl_today,
                "detail": f"live={live_n} vs expected~{expected_day:.1f}/day "
                          f"(bt_trades={bt_trades}/{BACKTEST_YEARS:.0f}yr). "
                          f"Total PnL today: {pnl_today:+.1f}%",
            })

        # WRONG_SIDE (check each individual close record)
        for rec in close_records:
            if rec["sym"] != sym:
                continue
            if is_wrong_side(rec["side"], winner_side):
                flags.append({
                    "type": "WRONG_SIDE",
                    "sym": sym,
                    "live_side": rec["side"],
                    "winner_side": winner_side,
                    "gain": rec["gain"],
                    "reason": rec["reason"],
                    "ts": rec["ts"],
                    "detail": f"winner={winner_side} but traded {rec['side']} "
                              f"gain={rec['gain']:+.2f}%",
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
                live_n = daily_closes.get(sym, 0)
                if live_n > 0:
                    pnl_today = sum(close_pnl.get(sym, []))
                    flags.append({
                        "type": "NEG_WSHARPE",
                        "sym": sym,
                        "side": side_sfx,
                        "wsharpe": wsharpe,
                        "live_closes": live_n,
                        "pnl_today": pnl_today,
                        "detail": f"active_config wsharpe={wsharpe:+.4f} (7D-agent says bad period) "
                                  f"but {live_n} trades fired, PnL={pnl_today:+.1f}%",
                    })

    # ── 3. UNMODELED_EXIT ───────────────────────────────────────────────────
    unmodeled_losses = []
    for rec in close_records:
        if rec["gain"] >= 0:
            continue
        reason = rec["reason"]
        if any(reason.startswith(pfx) for pfx in UNMODELED_EXIT_PREFIXES):
            unmodeled_losses.append(rec)

    if unmodeled_losses:
        by_sym: Dict[str, List] = defaultdict(list)
        for rec in unmodeled_losses:
            by_sym[rec["sym"]].append(rec)
        for sym, recs in sorted(by_sym.items(), key=lambda x: sum(r["gain"] for r in x[1])):
            total = sum(r["gain"] for r in recs)
            flags.append({
                "type": "UNMODELED_EXIT",
                "sym": sym,
                "n_losses": len(recs),
                "total_loss": total,
                "example_reason": recs[0]["reason"][:60],
                "detail": f"{len(recs)} loss-closes via DELTA_EXIT/SMART_RZ (NOT in backtest). "
                          f"Total loss: {total:+.1f}%",
            })

    # ── Print report ─────────────────────────────────────────────────────────
    if alert_only:
        flags = [f for f in flags if (
            f["type"] == "WRONG_SIDE" and f.get("gain", 0) < 0
            or f["type"] == "RATE_BLOAT" and f.get("pnl_today", 0) < -5
            or f["type"] == "NEG_WSHARPE" and f.get("pnl_today", 0) < 0
            or f["type"] == "UNMODELED_EXIT"
        )]

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
    total_closes = sum(daily_closes.values())
    total_pnl = sum(p for ps in close_pnl.values() for p in ps)
    losing_trades = sum(1 for ps in close_pnl.values() for p in ps if p < 0)
    print(f"\n{'─'*72}")
    print(f"  Summary: {total_closes} closes across {len(daily_closes)} symbols")
    print(f"  Total PnL: {total_pnl:+.1f}% | Losing trades: {losing_trades}")
    print(f"  Discrepancy flags: {len(flags)}")
    print(f"{'='*72}")

    # ── Known engine gaps note ─────────────────────────────────────────────────
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
    ap.add_argument("--alert-only", action="store_true",
                    help="Only print loss-bearing flags")
    args = ap.parse_args()
    run_analysis(args.account, args.days, args.alert_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
