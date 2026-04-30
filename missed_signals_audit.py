#!/usr/bin/env python3
"""missed_signals_audit — find big jumps/falls the engine ignored, propose param fixes.

User directive 2026-04-30: "Add an agent that investigates what is missing and why
trades are or are not happening, whether any jumps or falls were missed in the time
period and add params to catch them in the next run."

Per cycle, for each BTC-cluster symbol:
  1. Pull last 7 days of klines from NPZ.
  2. Detect "big moves": ≥1.0% over 1h, ≥2% over 4h, ≥4% over 24h. Each is a
     potential trade opportunity.
  3. For each big move, check the live decisions JSONL — was a trade taken in
     the same direction within 30min before/after the move? If not → MISSED.
  4. For each missed move, classify what gate likely blocked it:
       - was BTC_DIVERGENCE_BLOCK_AGAINST blocking it?
       - was BTC_BREAKOUT_REQUIRE_HTF_ALIGNED requiring more TF alignment?
       - was BTC_RZ_PROXIMITY_PCT too tight?
       - was MIN_HOLD_BARS keeping prior position open?
  5. Propose a param mutation that would (likely) catch the move; persist to
     data/hourly_reconfig/_missed_signals/<account>/<sym>_<date>.json
  6. The btc_settings_search agent picks these mutations as priority candidates.

ALL Sharpe writes (none here directly — we only count + propose) bypass through
metrics_guard if they ever get displayed.

Usage:
  python3 missed_signals_audit.py --account flz --once
  python3 missed_signals_audit.py --account flz --daemon
"""
from __future__ import annotations
import argparse, calendar, gc, glob, json, os, sys, time, traceback
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
DECISIONS_DIR = ROOT / "data" / "decisions"
OUT_BASE = ROOT / "data" / "hourly_reconfig" / "_missed_signals"

BTC_CLUSTER = ("BTCUSDC", "BTCDOMUSDT", "ETHUSDC", "BNBUSDC", "SOLUSDC", "XRPUSDC", "DOGEUSDC", "ZECUSDC")

# Move magnitude thresholds (percent change over time horizon)
MOVE_THRESHOLDS = [
    (3600, 1.0),    # 1.0% in 1h
    (4 * 3600, 2.0),  # 2.0% in 4h
    (24 * 3600, 4.0),  # 4.0% in 24h
]


def load_recent_decisions(account: str, days: int = 7) -> List[Dict]:
    """Load decision events from the last `days` days for `account`.

    Schema (live ez_manage):
      timestamp = ISO UTC string
      position_key = "<account>:<SYMBOL>_<SIDE>"  e.g. "flz:BTCUSDC_LONG"
      action = OPEN/CLOSE/REDUCE/STRONG_REDUCE/QUICK_CLOSE
    Parses sym + side out of position_key.
    """
    cutoff = time.time() - days * 86400
    decisions: List[Dict] = []
    for path in sorted(glob.glob(str(DECISIONS_DIR / f"decisions_{account}_*.jsonl"))):
        if os.path.getmtime(path) < cutoff - 86400:
            continue
        try:
            with open(path) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        ts = rec.get("ts") or rec.get("timestamp")
                        if isinstance(ts, str):
                            try:
                                # Treat as UTC (ISO timestamps with Z or +00:00 suffix).
                                ts = int(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")))
                            except Exception:
                                continue
                        if not ts or ts < cutoff:
                            continue
                        rec["_ts"] = int(ts)
                        # Parse position_key into symbol + side
                        pk = rec.get("position_key", "") or ""
                        if ":" in pk:
                            pk = pk.split(":", 1)[1]
                        if pk.endswith("_LONG"):
                            rec["_sym"] = pk[:-5]
                            rec["_side"] = "LONG"
                        elif pk.endswith("_SHORT"):
                            rec["_sym"] = pk[:-6]
                            rec["_side"] = "SHORT"
                        else:
                            rec["_sym"] = ""
                            rec["_side"] = ""
                        decisions.append(rec)
                    except Exception:
                        pass
        except Exception:
            pass
    return decisions


def find_big_moves(sym: str, days: int = 7) -> List[Dict]:
    """Return list of {ts, horizon_s, pct_change, direction} for big moves in the window."""
    npz_path = NPZ_DIR / f"{sym}.npz"
    if not npz_path.exists():
        return []
    z = np.load(str(npz_path))
    try:
        ts_arr = z["timestamps"] if "timestamps" in z.files else z[z.files[0]]
        close = z["close"] if "close" in z.files else z["close_3m"]
    except Exception:
        try: z.close()
        except Exception: pass
        return []
    end_ts = int(ts_arr[-1])
    cutoff = end_ts - days * 86400
    keep = ts_arr >= cutoff
    if keep.sum() < 100:
        try: z.close()
        except Exception: pass
        return []
    ts_arr = ts_arr[keep]
    close = close[keep]
    moves: List[Dict] = []
    bar_seconds = 180  # crypto base TF = 3m
    for horizon, threshold in MOVE_THRESHOLDS:
        bars_back = max(1, horizon // bar_seconds)
        if bars_back >= len(close):
            continue
        for i in range(bars_back, len(close)):
            pct = (float(close[i]) - float(close[i - bars_back])) / float(close[i - bars_back]) * 100.0
            if abs(pct) < threshold:
                continue
            # Dedup: skip if a larger move already in the same window
            t = int(ts_arr[i])
            if any(abs(t - m["ts"]) < bar_seconds * 5 and abs(m["pct"]) >= abs(pct) for m in moves):
                continue
            moves.append({
                "ts": t,
                "horizon_s": horizon,
                "pct": round(pct, 3),
                "direction": "UP" if pct > 0 else "DOWN",
                "price": round(float(close[i]), 6),
            })
    try: z.close()
    except Exception: pass
    return moves


def trade_within(decisions: List[Dict], sym: str, ts: int, direction: str,
                 window_s: int = 1800) -> bool:
    """Was there an OPEN action OR an existing-position-aligned action within ±window_s
    of ts that aligns with the direction of the move?

    For UP moves we want LONG positions (open or already open); for DOWN, SHORT.
    A REDUCE/CLOSE on an aligned side counts as 'we had it' — only counts a missed
    move if the side was completely silent.
    """
    target_side = "LONG" if direction == "UP" else "SHORT"
    for d in decisions:
        if abs(d.get("_ts", 0) - ts) > window_s:
            continue
        if d.get("_sym", "") != sym:
            continue
        if d.get("_side", "") == target_side:
            # Any decision for this sym/side near this time = position existed,
            # we count the move as caught (regardless of OPEN vs REDUCE).
            return True
    return False


def classify_likely_block(move: Dict) -> List[str]:
    """Heuristic classification of which gates likely blocked this trade."""
    proposals: List[str] = []
    direction = move["direction"]
    horizon = move["horizon_s"]
    if horizon == 3600:
        # Short-horizon move — likely blocked by warmup / cooldown
        proposals.append("BTC_BREAKOUT_COOLDOWN_BARS=1")
        proposals.append("MIN_HOLD_BARS=5")
        proposals.append("BTC_BREAKOUT_MIN_HOLD_BARS=1")
    elif horizon == 4 * 3600:
        # Medium-horizon — likely RZ / accel ramp gate
        proposals.append("BTC_RZ_PROXIMITY_PCT=1.0")
        proposals.append("BTC_ACCEL_RAMP_MIN_TFS=1")
        proposals.append("BTC_BREAKOUT_HTF_MIN_ALIGNED=1")
    else:
        # 24h move — likely divergence block or HTF alignment
        if direction == "UP":
            proposals.append("BTC_DIVERGENCE_BLOCK_AGAINST=False")
            proposals.append("BTC_BREAKOUT_REQUIRE_HTF_ALIGNED=False")
        else:
            proposals.append("BTC_DIVERGENCE_EXIT_AGAINST=True")
            proposals.append("BTC_BREAKOUT_BLOCK_OPPOSING_DIV=False")
    return proposals


def audit_one_account(account: str) -> Dict:
    out_dir = OUT_BASE / account
    out_dir.mkdir(parents=True, exist_ok=True)
    decisions = load_recent_decisions(account, days=7)
    print(f"[missed] {account}: {len(decisions)} live decisions in last 7d", flush=True)
    summary: Dict = {
        "account": account,
        "now_ts": int(time.time()),
        "n_decisions": len(decisions),
        "syms": {},
    }
    for sym in BTC_CLUSTER:
        moves = find_big_moves(sym, days=7)
        missed: List[Dict] = []
        caught: List[Dict] = []
        for m in moves:
            if trade_within(decisions, sym, m["ts"], m["direction"]):
                caught.append(m)
            else:
                m["likely_blocks"] = classify_likely_block(m)
                missed.append(m)
        summary["syms"][sym] = {
            "n_moves": len(moves),
            "n_caught": len(caught),
            "n_missed": len(missed),
            "missed_pct": round(100.0 * len(missed) / max(1, len(moves)), 1),
            "missed_samples": missed[:5],  # cap for readability
        }
        if missed:
            sym_path = out_dir / f"{sym}_latest.json"
            sym_path.write_text(json.dumps({
                "sym": sym,
                "now_ts": int(time.time()),
                "n_moves": len(moves),
                "n_missed": len(missed),
                "missed": missed,
                "caught": caught,
            }, indent=2, default=str))
        # Aggregate proposed mutations (count frequency across missed moves)
        prop_counts: Dict[str, int] = {}
        for m in missed:
            for p in m.get("likely_blocks", []):
                prop_counts[p] = prop_counts.get(p, 0) + 1
        summary["syms"][sym]["top_proposals"] = sorted(
            prop_counts.items(), key=lambda kv: -kv[1]
        )[:5]
    summary_path = out_dir / "summary_latest.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    # Concise printout
    print(f"[missed] === audit summary for {account} ===", flush=True)
    for sym, d in summary["syms"].items():
        print(f"  {sym:>14}: moves={d['n_moves']:>3} caught={d['n_caught']:>3} missed={d['n_missed']:>3} "
              f"({d['missed_pct']:>4.1f}% missed) top_prop={d['top_proposals'][:2]}", flush=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, choices=("flz", "fin", "inf", "ang", "men", "trc", "trb"))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--cycle-secs", type=int, default=3600)
    args = ap.parse_args()
    if not (args.once or args.daemon):
        ap.error("specify --once or --daemon")
    while True:
        try:
            t0 = time.time()
            audit_one_account(args.account)
            elapsed = time.time() - t0
            if args.once:
                return 0
            time.sleep(max(60, args.cycle_secs - int(elapsed)))
        except KeyboardInterrupt:
            return 0
        except Exception:
            traceback.print_exc()
            time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
