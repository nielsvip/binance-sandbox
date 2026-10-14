#!/usr/bin/env python3
"""stock_npz_gap_guard — refuse/flag stock NPZs (or 15m frames) that are short or holed in the recent window (2026-10-14).

Incident: 33/162 stock NPZs on S1 had 1-20 sessions in the last 60 days (hole 2026-08-26 -> 2026-09-30 in the Tradier 15m
cache; 1-row stubs written by npz_live_generator after a failed rebuild). The 30D vector window (30 sessions from 60 calendar
days, tools/opt/evaluate_v12.py) then failed or silently measured over the hole.

Rule: in the last WINDOW_DAYS (60) calendar days before the newest bar there must be >= MIN_SESSIONS (30) distinct ET sessions
and no gap between consecutive sessions > MAX_GAP_DAYS (5; weekend + holiday is 4). On violation: logger.error line + JSON
alert data/alerts/stock_npz_gap_<SYMBOL>.json (overwritten per event, history appended to data/alerts/stock_npz_gap.jsonl).
Env STOCK_NPZ_GAP_GUARD=0 disables refusal (alert still written).

CLI: python tools/stock_npz_gap_guard.py [--dir backtest_v8/indicators] [SYM ...]  -> prints failures, exit 1 if any.
"""
import json
import logging
import os
import sys
import time
from pathlib import Path
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
ALERT_DIR = ROOT / "data" / "alerts"
WINDOW_DAYS = int(os.environ.get("STOCK_NPZ_GAP_WINDOW_DAYS", "60"))
MIN_SESSIONS = int(os.environ.get("STOCK_NPZ_GAP_MIN_SESSIONS", "30"))
MAX_GAP_DAYS = int(os.environ.get("STOCK_NPZ_GAP_MAX_GAP_DAYS", "5"))
logger = logging.getLogger("stock_npz_gap_guard")


def enforced():
    return os.environ.get("STOCK_NPZ_GAP_GUARD", "1") != "0"


def check_timestamps(timestamps, window_days=WINDOW_DAYS, min_sessions=MIN_SESSIONS, max_gap_days=MAX_GAP_DAYS):
    """timestamps: epoch seconds (or ms) array / DatetimeIndex. Window anchored on the newest bar."""
    if isinstance(timestamps, pd.DatetimeIndex):
        idx = timestamps.tz_localize("UTC") if timestamps.tz is None else timestamps
    else:
        arr = np.asarray(timestamps, dtype=np.int64)
        if arr.size and arr.max() > 10**11:
            arr = arr // 1000
        idx = pd.to_datetime(arr, unit="s", utc=True)
    if len(idx) == 0:
        return {"ok": False, "sessions": 0, "max_gap_days": None, "gaps": [], "last": None, "reason": "no bars"}
    last = idx.max()
    recent = idx[idx >= last - pd.Timedelta(days=window_days)]
    days = sorted(set(recent.tz_convert("America/New_York").date))
    gaps = [(str(a), str(b), (b - a).days) for a, b in zip(days, days[1:]) if (b - a).days > max_gap_days]
    max_gap = max([(b - a).days for a, b in zip(days, days[1:])] or [0])
    reasons = []
    if len(days) < min_sessions:
        reasons.append(f"{len(days)} sessions in last {window_days}d (< {min_sessions})")
    if gaps:
        reasons.append(f"max intra-window gap {max_gap}d (> {max_gap_days}d): {gaps}")
    return {"ok": not reasons, "sessions": len(days), "max_gap_days": int(max_gap), "gaps": gaps, "last": str(last), "reason": "; ".join(reasons)}


def alert(symbol, info, source, refused):
    rec = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "symbol": symbol, "source": source, "refused": bool(refused), "host": os.uname().nodename, **info}
    logger.error(f"STOCK_NPZ_GAP {symbol} [{source}] {'REFUSED' if refused else 'FLAGGED'}: {info.get('reason')}")
    try:
        ALERT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ALERT_DIR / f".stock_npz_gap_{symbol}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(rec, indent=1))
        os.replace(tmp, ALERT_DIR / f"stock_npz_gap_{symbol}.json")
        with open(ALERT_DIR / "stock_npz_gap.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError as exc:
        logger.error(f"STOCK_NPZ_GAP alert write failed for {symbol}: {exc}")
    return rec


def clear(symbol):
    try:
        (ALERT_DIR / f"stock_npz_gap_{symbol}.json").unlink()
    except OSError:
        pass


def gate(symbol, timestamps, source):
    """True = allowed. Violations always alert; refusal unless STOCK_NPZ_GAP_GUARD=0."""
    info = check_timestamps(timestamps)
    if info["ok"]:
        clear(symbol)
        return True, info
    refused = enforced()
    alert(symbol, info, source, refused)
    return (not refused), info


def gate_npz(symbol, path, source):
    try:
        with np.load(str(path), allow_pickle=True) as z:
            ts = z["timestamps"]
    except Exception as exc:
        info = {"ok": False, "sessions": 0, "max_gap_days": None, "gaps": [], "last": None, "reason": f"unreadable NPZ: {exc}"}
        refused = enforced()
        alert(symbol, info, source, refused)
        return (not refused), info
    return gate(symbol, ts, source)


def main(argv):
    d = ROOT / "backtest_v8" / "indicators"
    if "--dir" in argv:
        i = argv.index("--dir")
        d = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    syms = argv or sorted(p.stem for p in d.glob("*.npz") if not p.stem.endswith(("USDT", "USDC")))
    bad = 0
    for s in syms:
        p = d / f"{s}.npz"
        if not p.exists():
            continue
        try:
            with np.load(str(p), allow_pickle=True) as z:
                info = check_timestamps(z["timestamps"])
        except Exception as exc:
            info = {"ok": False, "reason": f"unreadable: {exc}", "sessions": 0, "max_gap_days": None, "last": None}
        if not info["ok"]:
            bad += 1
            print(json.dumps({"symbol": s, **{k: info[k] for k in ("sessions", "max_gap_days", "last", "reason")}}))
    print(f"checked {len(syms)} failing {bad}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
