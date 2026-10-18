#!/usr/bin/env python3
"""promise_audit — did live trade what the vector promised? Nothing more, nothing less. (USER 2026-10-18)

Reads the append-only ledger written by the live trader (`promise_ledger.py`: PROMISE rows from the vec twin,
DELIVERY/BROKEN rows from the live dispatch in `tools/vx_tracked_exec.py`) and states, per account and per sym_side:

  promises            vec acts emitted at a closed 15m bar (OPEN / AUGMENT / REDUCE / CLOSE)
  kept_in_bar         a delivery with a SUCCESS-class outcome inside the bar the promise was made for (lag <= 900 s)
  kept_late           delivered, but after its bar closed -> the trade happened at a price the board never saw
  broken              no delivery at all, or a BROKEN row (wedged exit forced, refusal, skip that never recovered)
  skipped_inflight    deliveries answered SKIPPED_VX_INFLIGHT (the old failure mode; EXIT rows here are the rule breach)
  preempted / forced  the 2026-10-18 enforcement firing: an exit that overtook a non-exit / escalated to the market leg

Exit promises are reported separately from entry promises, because an exit that misses its bar is the one that costs
money (measured 2026-10-17: 23 VEC_STALE_FLATTEN closes = -13.31 $ over 7 days).

Read-only. Usage: tools/promise_audit.py [--days 1] [--acct men] [--sym-side XTZUSDT_LONG] [--json OUT] [--top 25]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LED = ROOT / "data" / "promise_ledger"
BAR_S = 900.0
OK_OUTCOMES = ("SUCCESS", "FILLED", "TIMEOUT_EXIT_MARKET", "TIMEOUT_EXIT_FORCE", "EXIT_FORCE_ESCALATION", "FORCE_CLOSE")
SKIP_OUTCOMES = ("SKIPPED_VX_INFLIGHT",)


def is_ok(outcome: str) -> bool:
    o = str(outcome or "").upper()
    return any(t in o for t in OK_OUTCOMES)


def load(days: float):
    cut = dt.datetime.now(dt.timezone.utc).timestamp() - days * 86400.0
    promises, deliveries, broken = [], collections.defaultdict(list), []
    for fp in sorted(glob.glob(str(LED / "*.jsonl"))):
        try:
            for line in open(fp, errors="ignore"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if float(r.get("ts") or 0) < cut:
                    continue
                k = str(r.get("kind") or "")
                if k == "PROMISE":
                    promises.append(r)
                elif k == "DELIVERY":
                    deliveries[r.get("key")].append(r)
                elif k == "BROKEN":
                    broken.append(r)
        except Exception:
            continue
    for v in deliveries.values():
        v.sort(key=lambda r: float(r.get("ts") or 0))
    return promises, deliveries, broken


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--acct", default="")
    ap.add_argument("--sym-side", default="")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", default="")
    ap.add_argument("--match-window-s", type=float, default=3600.0, help="how long after its bar a delivery may still be attributed to a promise")
    a = ap.parse_args()
    promises, deliveries, broken = load(a.days)
    if a.acct:
        promises = [p for p in promises if p.get("acct") == a.acct]
    if a.sym_side:
        promises = [p for p in promises if p.get("sym_side") == a.sym_side.upper()]
    if not promises:
        print(f"[promise_audit] no PROMISE rows in {LED} for the last {a.days} d — is the live trader running the 2026-10-18 build (PROMISE_LEDGER=1)?")
        return
    per = collections.defaultdict(collections.Counter)
    lags = collections.defaultdict(list)
    used = collections.defaultdict(set)
    detail = []
    for p in sorted(promises, key=lambda r: float(r.get("ts") or 0)):
        key, bar = p.get("key"), float(p.get("bar_ts") or 0)
        is_exit = bool(p.get("is_exit"))
        grp = (p.get("acct") or "?", p.get("sym_side") or "?")
        kind = "exit" if is_exit else "entry"
        per[grp][f"{kind}_promises"] += 1
        rows = deliveries.get(key) or []
        ts_list = [float(r.get("ts") or 0) for r in rows]
        i = bisect.bisect_left(ts_list, bar)
        hit, state = None, "broken_no_delivery"
        for j in range(i, len(rows)):
            r = rows[j]
            if j in used[key] or bool(r.get("is_exit")) != is_exit:
                continue
            if float(r.get("ts") or 0) > bar + BAR_S + a.match_window_s:
                break
            o = str(r.get("outcome") or "")
            if is_ok(o):
                hit, state = r, ("kept_in_bar" if float(r.get("ts") or 0) <= bar + 2 * BAR_S else "kept_late")
                used[key].add(j)
                break
            if any(t in o.upper() for t in SKIP_OUTCOMES):
                per[grp][f"{kind}_skipped_inflight"] += 1
            if "PREEMPT" in o.upper():
                per[grp][f"{kind}_preempted"] += 1
            if "FORCE_ESCALATION" in o.upper():
                per[grp][f"{kind}_forced"] += 1
        per[grp][f"{kind}_{state}"] += 1
        if hit is not None:
            lag = (float(hit.get("ts")) - (bar + BAR_S)) / 60.0
            lags[(grp, kind)].append(lag)
            if state == "kept_late":
                detail.append({"acct": grp[0], "sym_side": grp[1], "kind": kind, "bar": dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime("%m-%d %H:%M"),
                               "late_min": round(lag, 1), "action": p.get("action"), "reason": str(p.get("reason"))[:60], "outcome": str(hit.get("outcome"))[:40]})
        elif state == "broken_no_delivery":
            detail.append({"acct": grp[0], "sym_side": grp[1], "kind": kind, "bar": dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime("%m-%d %H:%M"),
                           "late_min": None, "action": p.get("action"), "reason": str(p.get("reason"))[:60], "outcome": "NO_DELIVERY"})
    tot = collections.Counter()
    for c in per.values():
        tot.update(c)
    print(f"=== promise_audit  last {a.days} d  ledger={LED}  keys={len(per)} ===")
    for kind in ("exit", "entry"):
        p_, ib, late, nb = tot[f"{kind}_promises"], tot[f"{kind}_kept_in_bar"], tot[f"{kind}_kept_late"], tot[f"{kind}_broken_no_delivery"]
        allk = [x for (g, k), v in lags.items() if k == kind for x in v]
        med = sorted(allk)[len(allk) // 2] if allk else None
        print(f"{kind.upper():5s} promises {p_:6d} | kept in bar {ib:6d} ({100.0 * ib / max(1, p_):5.1f}%) | kept late {late:5d} | no delivery {nb:5d}"
              f" | skipped_inflight {tot[f'{kind}_skipped_inflight']:5d} | preempted {tot[f'{kind}_preempted']:4d} | forced {tot[f'{kind}_forced']:4d}"
              f" | median lag {('%.1f min' % med) if med is not None else 'n/a'}")
    print(f"\nBROKEN rows written by the live dispatch: {len(broken)}")
    for b in broken[-5:]:
        print(f"  {dt.datetime.fromtimestamp(float(b['ts']), dt.timezone.utc).strftime('%m-%d %H:%M')} {b.get('key')} {b.get('why')} {str(b.get('label'))[:60]} {str(b.get('detail'))[:60]}")
    print("\n=== per account ===")
    acc = collections.defaultdict(collections.Counter)
    for (ac, _ss), c in per.items():
        acc[ac].update(c)
    print(f"{'acct':6s} {'exit prom':>9s} {'in bar':>7s} {'late':>5s} {'none':>5s} {'entry prom':>10s} {'in bar':>7s} {'late':>5s} {'none':>5s}")
    for ac in sorted(acc):
        c = acc[ac]
        print(f"{ac:6s} {c['exit_promises']:9d} {c['exit_kept_in_bar']:7d} {c['exit_kept_late']:5d} {c['exit_broken_no_delivery']:5d}"
              f" {c['entry_promises']:10d} {c['entry_kept_in_bar']:7d} {c['entry_kept_late']:5d} {c['entry_broken_no_delivery']:5d}")
    worst = sorted(per.items(), key=lambda kv: -(kv[1]["exit_kept_late"] + kv[1]["exit_broken_no_delivery"]))[: a.top]
    print(f"\n=== worst {len(worst)} sym_sides by exit promises not kept in their bar ===")
    print(f"{'acct':5s} {'sym_side':22s} {'exit':>5s} {'inbar':>6s} {'late':>5s} {'none':>5s} {'skip':>5s} {'entry':>6s} {'inbar':>6s} {'late':>5s} {'none':>5s}")
    for (ac, ss), c in worst:
        if not (c["exit_kept_late"] + c["exit_broken_no_delivery"]):
            continue
        print(f"{ac:5s} {ss:22s} {c['exit_promises']:5d} {c['exit_kept_in_bar']:6d} {c['exit_kept_late']:5d} {c['exit_broken_no_delivery']:5d} {c['exit_skipped_inflight']:5d}"
              f" {c['entry_promises']:6d} {c['entry_kept_in_bar']:6d} {c['entry_kept_late']:5d} {c['entry_broken_no_delivery']:5d}")
    late_rows = [d for d in detail if d["kind"] == "exit"]
    print(f"\n=== exit promises kept late / never (worst 15 by delay) ===")
    for d in sorted(late_rows, key=lambda d: -(d["late_min"] or 1e9))[:15]:
        print(f"  {d['acct']:4s} {d['sym_side']:20s} bar {d['bar']}  {'NO DELIVERY' if d['late_min'] is None else str(d['late_min']) + ' min late'}  {d['action']} {d['reason']}  -> {d['outcome']}")
    if a.json:
        Path(a.json).write_text(json.dumps({"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "days": a.days,
                                            "totals": dict(tot), "per_key": {f"{k[0]}:{k[1]}": dict(v) for k, v in per.items()},
                                            "detail": detail, "broken": broken}, default=str, indent=1))
        print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
