#!/usr/bin/env python3
"""discrepancy_ingest — harvest EVERY live-vs-vector divergence into the SQL ledger (USER 2026-10-18).

Read-only on every source; the only thing it writes is data/discrepancy/discrepancy.db (discrepancy_ledger.py).
Idempotent: re-running over the same logs inserts nothing new.

SOURCES -> CLASSES
  data/paper_nonvec/events_*.jsonl            -> GATE_REFUSED          (live decided, the parity gate refused it)
  data/history/<acct>/<SYM>_<SIDE>.jsonl      -> LIVE_ONLY_FILL        (a fill whose reason carries no |VEC_EXACT tag and is
                                                                        not broker bookkeeping: live traded without the vector)
  data/promise_ledger/*.jsonl                 -> PROMISE_LATE / PROMISE_NOT_DELIVERED / EXEC_SKIPPED_INFLIGHT
  ~/logs/ez_manage_*_stderr.log, *.log        -> SWEEP_TIMEOUT         (vec_dispatch: the key was never evaluated this sweep)
  data/reports/retrace/retrace_*.json         -> BAR_MISMATCH          (matched trips whose entry/exit bar differs)

Usage: tools/discrepancy_ingest.py [--days 7] [--retrace data/reports/retrace/retrace_FOCUS32.json] [--quiet]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import discrepancy_ledger as DL  # noqa: E402

BAR_S = 900.0
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
# Bookkeeping = the ledger mirroring a fill the broker already made (no decision of its own).
BOOKKEEPING = ("BROKER_RECONCILED", "SYNC_DETECTION")
# The protective stop is a VECTOR family too (vec_paths allowlist: FIXED_STOP / DAYTRADE_STOP ...); an exchange STOP_MARKET
# fill simply carries no |VEC_EXACT tag because the exchange filled the resting order, not a live decision. Kept in the
# ledger (the bar it fills on still differs from the vector's 15m-close check, BIBLE 106 C1) but attributed separately so
# "live traded something the vector never asked for" stays an honest count.
STOP_FAMILIES = ("FIXED_STOP", "DAYTRADE_STOP", "MAKER_PROFIT_EXIT_FIXED_STOP", "FROZEN_STOP", "PPL_SL")
VEC_TAG = "|VEC_EXACT"


def iso_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def bar_of(ts):
    return (float(ts) // BAR_S) * BAR_S


def ing_gate_refused(con, t_from):
    n = 0
    for fp in sorted(glob.glob(str(ROOT / "data" / "paper_nonvec" / "events_*.jsonl"))):
        for line in open(fp, errors="ignore"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            ts = iso_ts(r.get("ts"))
            if ts is None or ts < t_from:
                continue
            key = str(r.get("position_key") or "")
            acct = str(r.get("account") or (key.split(":", 1)[0] if ":" in key else ""))
            ss = key.split(":", 1)[1] if ":" in key else key
            n += DL.add(con, "GATE_REFUSED", ts, acct, ss, bar_ts=bar_of(ts), venue=str(r.get("venue") or "crypto"),
                        action=str(r.get("action") or ""), side="", who_wanted="LIVE", live_reason=str(r.get("reason") or ""),
                        live_price=r.get("price"), qty=r.get("qty"),
                        notional=((float(r["price"]) * float(r["qty"])) if r.get("price") and r.get("qty") else None),
                        source=f"paper_nonvec:{Path(fp).name}", detail={"fn": r.get("fn"), "gate": r.get("gate"), "kind": r.get("kind")},
                        fp_extra=f"{r.get('fn')}|{r.get('kind')}")
    return n


def ing_live_only_fills(con, t_from):
    n = 0
    for acct in ACCOUNTS:
        for fp in glob.glob(str(ROOT / "data" / "history" / acct / "*.jsonl")):
            ss = Path(fp).stem
            try:
                for line in open(fp, errors="ignore"):
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    ts = iso_ts(r.get("ts"))
                    if ts is None or ts < t_from:
                        continue
                    reason = str(r.get("reason") or "")
                    if VEC_TAG in reason or any(b in reason.upper() for b in BOOKKEEPING):
                        continue
                    px, qty = r.get("price"), r.get("qty")
                    who = "EXCHANGE_STOP" if any(f in reason.upper() for f in STOP_FAMILIES) else "LIVE"
                    n += DL.add(con, "LIVE_ONLY_FILL", ts, acct, ss, bar_ts=bar_of(ts), action=str(r.get("type") or ""),
                                side=("SHORT" if ss.endswith("_SHORT") else "LONG"), who_wanted=who, live_reason=reason,
                                live_price=px, qty=qty, notional=r.get("value"),
                                source=f"history:{acct}", detail={"indicators_price": (r.get("indicators") or {}).get("price")},
                                fp_extra=reason[:40])
            except Exception:
                continue
    return n


def ing_promises(con, t_from, pending_grace=900.0, match_window=3600.0):
    """PROMISE rows joined to their DELIVERY rows: late, never delivered, or refused by the single-flight guard."""
    import bisect

    promises, deliveries = [], {}
    for fp in sorted(glob.glob(str(ROOT / "data" / "promise_ledger" / "*.jsonl"))):
        for line in open(fp, errors="ignore"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if float(r.get("ts") or 0) < t_from:
                continue
            if r.get("kind") == "PROMISE":
                promises.append(r)
            elif r.get("kind") in ("DELIVERY", "BROKEN"):
                deliveries.setdefault(r.get("key"), []).append(r)
    for v in deliveries.values():
        v.sort(key=lambda r: float(r.get("ts") or 0))
    now = time.time()
    n = 0
    for p in promises:
        key, bar = str(p.get("key") or ""), float(p.get("bar_ts") or 0)
        acct = str(p.get("acct") or "")
        ss = str(p.get("sym_side") or "")
        rows = deliveries.get(key) or []
        ts_list = [float(r.get("ts") or 0) for r in rows]
        i = bisect.bisect_left(ts_list, bar)
        hit = None
        for j in range(i, len(rows)):
            r = rows[j]
            if float(r.get("ts") or 0) > bar + BAR_S + match_window:
                break
            if bool(r.get("is_exit")) != bool(p.get("is_exit")):
                continue
            o = str(r.get("outcome") or "")
            if "SKIPPED_VX_INFLIGHT" in o:
                n += DL.add(con, "EXEC_SKIPPED_INFLIGHT", float(r["ts"]), acct, ss, bar_ts=bar, action=str(p.get("action") or ""),
                            who_wanted="VECTOR", vec_reason=str(p.get("reason") or ""), lag_s=r.get("lag_s"),
                            source="promise_ledger", detail={"inflight": r.get("detail")}, fp_extra=f"skip|{r.get('ts')}")
                continue
            if any(t in o.upper() for t in ("SUCCESS", "FILLED", "TIMEOUT_EXIT_MARKET", "TIMEOUT_EXIT_FORCE", "EXIT_FORCE_ESCALATION")):
                hit = r
                break
        if hit is not None:
            lag = float(hit.get("ts")) - (bar + BAR_S)
            if lag > BAR_S:  # delivered, but outside the bar the vector decided on
                n += DL.add(con, "PROMISE_LATE", float(hit["ts"]), acct, ss, bar_ts=bar, action=str(p.get("action") or ""),
                            who_wanted="VECTOR", vec_reason=str(p.get("reason") or ""), lag_s=round(lag, 1),
                            bars_off=int(lag // BAR_S), source="promise_ledger",
                            detail={"outcome": hit.get("outcome")}, fp_extra=f"late|{p.get('action')}")
        elif now - (bar + BAR_S) >= pending_grace:
            n += DL.add(con, "PROMISE_NOT_DELIVERED", bar + BAR_S, acct, ss, bar_ts=bar, action=str(p.get("action") or ""),
                        who_wanted="VECTOR", vec_reason=str(p.get("reason") or ""), source="promise_ledger",
                        detail={"note": "no SUCCESS-class delivery found; a no-op (live state already matched) looks the same until ez_manage records NOOP"},
                        fp_extra=f"nodel|{p.get('action')}")
    return n


SWEEP_RE = re.compile(r"\[(VEC_EXACT[A-Z_]*)\] ((?:ang|fin|flz|inf|men):[A-Z0-9]+_(?:LONG|SHORT)): BLOCKED_DISPATCH_TIMEOUT after (\d+)s \(stuck key skipped")
STAMP_RE = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


def ing_sweep_timeouts(con, t_from, cap_per_key_per_hour=1):
    """vec_dispatch skipped the key in that sweep: the vector never got to decide. Collapsed to <=1 row per key per hour."""
    n = 0
    seen = set()
    for fp in sorted(glob.glob(os.path.expanduser("~/logs/ez_manage_*_stderr.log"))) + sorted(glob.glob(os.path.expanduser("~/logs/ez_manage_*.log"))):
        try:
            for line in open(fp, errors="ignore"):
                if "BLOCKED_DISPATCH_TIMEOUT" not in line or "stuck key skipped" not in line:
                    continue
                m = SWEEP_RE.search(line)
                if not m:
                    continue
                sm = STAMP_RE.match(line)
                if not sm:
                    continue
                try:
                    ts = dt.datetime.strptime(sm.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
                except Exception:
                    continue
                if ts < t_from:
                    continue
                key, waited = m.group(2), int(m.group(3))
                hour = int(ts // 3600)
                if (key, hour) in seen:
                    continue
                seen.add((key, hour))
                acct, ss = key.split(":", 1)
                n += DL.add(con, "SWEEP_TIMEOUT", ts, acct, ss, bar_ts=bar_of(ts), action=m.group(1), who_wanted="VECTOR",
                            vec_reason=f"{m.group(1)} never evaluated this sweep (waited {waited}s)", lag_s=float(waited),
                            source=f"log:{Path(fp).name}", detail={"hour": hour}, fp_extra=f"sweep|{hour}")
        except Exception:
            continue
    return n


def ing_bar_mismatch(con, path, t_from):
    """tools/v15_daily_retrace.py detail: matched trips whose entry or exit bar differs from the vector's."""
    if not path or not Path(path).exists():
        return 0
    doc = json.load(open(path))
    n = 0
    for ss, rec in (doc.get("sym_sides") or {}).items():
        for acct, ar in (rec.get("accounts") or {}).items():
            for t in ar.get("trips") or []:
                if t.get("kind") != "MATCHED":
                    continue
                de, dx = t.get("entry_bar_delta"), t.get("exit_bar_delta")
                if (de in (0, None)) and (dx in (0, -1, None)):
                    continue
                try:
                    ts = dt.datetime.strptime(t["live_entry"], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc).timestamp()
                except Exception:
                    continue
                if ts < t_from:
                    continue
                n += DL.add(con, "BAR_MISMATCH", ts, acct, ss, bar_ts=bar_of(ts), action="TRIP", who_wanted="BOTH",
                            live_reason=f"live entry {t.get('live_entry')} exit {t.get('live_exit')}",
                            vec_reason=f"{t.get('vec_entry_reason')} -> {t.get('vec_exit_reason')}",
                            live_price=t.get("live_notional"), qty=t.get("live_qty"), notional=t.get("live_notional"),
                            live_usd=t.get("live_net_pnl"), bars_off=(dx if dx is not None else de),
                            source=f"retrace:{Path(path).name}",
                            detail={"entry_bar_delta": de, "exit_bar_delta": dx, "exit_match": t.get("exit_match"),
                                    "vec_pct": t.get("vec_pct"), "live_net_pct": t.get("live_net_pct"),
                                    "vec_cycles_inside": t.get("vec_cycles_inside"), "entry_lag_min": t.get("entry_lag_min")},
                            fp_extra=f"trip|{t.get('live_entry')}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7.0)
    ap.add_argument("--retrace", default="")
    ap.add_argument("--db", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if a.db:
        DL.DB_PATH = Path(a.db)
    t_from = time.time() - a.days * 86400.0
    con = DL.connect()
    added = {"GATE_REFUSED": ing_gate_refused(con, t_from), "LIVE_ONLY_FILL": ing_live_only_fills(con, t_from)}
    added.update({k: v for k, v in (("promises", ing_promises(con, t_from)), ("SWEEP_TIMEOUT", ing_sweep_timeouts(con, t_from)))})
    retrace = a.retrace
    if not retrace:
        cands = sorted(glob.glob(str(ROOT / "data" / "reports" / "retrace" / "retrace_*.json")), key=os.path.getmtime)
        retrace = next((c for c in reversed(cands) if "symside" not in c and "daily" not in c), "")
    added["BAR_MISMATCH"] = ing_bar_mismatch(con, retrace, t_from)
    con.commit()
    if not a.quiet:
        print(f"[discrepancy_ingest] window {a.days} d -> {DL.DB_PATH}")
        print(f"[discrepancy_ingest] new rows: " + ", ".join(f"{k}={v}" for k, v in added.items()))
        print(f"[discrepancy_ingest] totals in db:")
        for cls, n, live_usd, cf_usd in DL.counts(con):
            print(f"    {cls:24s} {n:7d} rows   live ${live_usd or 0:+.2f}   counterfactual ${cf_usd or 0:+.2f}")
    con.close()


if __name__ == "__main__":
    main()
