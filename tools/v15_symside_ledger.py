#!/usr/bin/env python3
"""SYM_SIDE LEDGER + CADENCE (USER 2026-10-12: "all the biggest gainers need a daily recalc while the rest can be weekly on
avg … bh near zero is mostly a lost case … 2 servers"). Stdlib only, runs on S1 (cron) and the Mac.

Scans the newest progress JSON per sym_side (~/v15_run*/progress + data/reports/lifecycle_pilot) and writes
data/v15_symside_ledger.json: per sym_side the last measured final gain / bh / trades / tim / dd / verdict / pilot tier,
a potential score, a scheduler tier (W = DAILY, M = WEEKLY, L = BIWEEKLY) and next_due. tools/v15_fleet_scheduler.py reads
it (ledger wins over its own gain-only ranking). Every number is copied from pilot output — nothing is estimated.
"""
import argparse
import glob
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
CADENCE_D = {"W": 1.0, "M": 5.0, "L": 10.0}


def cat_side_of(ss):
    sym, _, side = ss.rpartition("_")
    return f"{'CRYPTO' if sym.upper().endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}"


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _f(x, d=None):
    try:
        return float(x) if _num(x) else d
    except Exception:
        return d


def read_progress(path):
    """Cheap top-level extraction (progress files are 3–35 MB; json.loads of the whole file is acceptable at cron cadence)."""
    try:
        p = json.load(open(path))
    except Exception:
        return None
    if not isinstance(p, dict):
        return None
    tier = p.get("tier") if isinstance(p.get("tier"), dict) else {}
    dr = p.get("diagnose_repair") if isinstance(p.get("diagnose_repair"), dict) else {}
    after = dr.get("after") if isinstance(dr.get("after"), dict) else {}
    f365 = p.get("final_365d") if isinstance(p.get("final_365d"), dict) else {}
    done_rows = p.get("done") if isinstance(p.get("done"), dict) else {}
    return {
        "final_gain": _f(p.get("final_gain")),
        "cumulative_gain": _f(p.get("cumulative_gain")),
        "baseline_gain": _f(p.get("baseline_gain")),
        "bh": _f(p.get("bh")),
        "trades": _f(p.get("final_trades")) or _f(after.get("trades")) or _f(tier.get("trades")),
        "tim": _f(after.get("tim")),
        "dd": _f(after.get("dd")),
        "wr": _f(after.get("wr")),
        "gain_365d": _f(f365.get("gain_pct")),
        "valid_365d": f365.get("valid"),
        "verdict": p.get("verdict"),
        "pilot_tier": tier.get("tier"),
        "needs_redo": bool(p.get("needs_redo")),
        "done_rows": len(done_rows),
        "promoted_rows": sum(1 for v in done_rows.values() if isinstance(v, dict) and v.get("promoted")),
        "result_done_utc": p.get("result_done_utc"),
        "focus_promotions": len(((p.get("focus") or {}).get("promotions") or [])) if isinstance(p.get("focus"), dict) else 0,
    }


def _done_ts(s):
    """epoch seconds of a recorded result_done_utc ('2026-10-12T22:31:05Z'), else None. Pure."""
    try:
        t = str(s or "").strip().replace("Z", "+00:00")
        if not t:
            return None
        import datetime
        d = datetime.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def potential_of(e):
    vals = [v for v in (e.get("final_gain"), e.get("cumulative_gain"), e.get("baseline_gain")) if v is not None]
    return max(vals) if vals else None


def sched_tier(e, bh_lost=2.0, lost_gain=5.0, big_mover=25.0, floor_trades=10):
    """Pure. W candidates are ranked later by the caller (daily cap); here: L = lost cause / loser, else M (W decided by rank)."""
    pot = e.get("potential")
    bh = e.get("bh")
    tr = e.get("trades") or 0
    if e.get("verdict") in ("IMPOSSIBLE", "NO_TRADES"):
        return "L", f"verdict {e.get('verdict')}"
    if bh is not None and abs(bh) >= big_mover:
        return "M", f"big mover |bh| {bh:.1f}"
    if pot is None:
        return "M", "unmeasured"
    if bh is not None and abs(bh) < bh_lost and pot < lost_gain:
        return "L", f"lost cause |bh| {bh:.2f} < {bh_lost}, potential {pot:.2f} < {lost_gain}"
    if pot <= 0 or tr < floor_trades:
        return "L", f"loser potential {pot:.2f} / trades {int(tr)}"
    return "M", f"potential {pot:.2f}"


def build(progress_globs, daily_cap, now=None):
    now = now or time.time()
    newest = {}
    for g in progress_globs:
        for p in glob.glob(g):
            ss = os.path.basename(p)[: -len("_v14_progress.json")]
            if not ss.endswith(("_LONG", "_SHORT")):
                continue
            mt = os.path.getmtime(p)
            if ss not in newest or mt > newest[ss][0]:
                newest[ss] = (mt, p)
    ledger = {}
    for ss, (mt, p) in newest.items():
        e = read_progress(p)
        if e is None:
            continue
        e["sym_side"] = ss
        e["cat_side"] = cat_side_of(ss)
        # USER 2026-10-13 (illegal re-stamp): file mtime is NOT provenance — tools that re-save a published
        # board (v15_red_fixer did, every 10 min) give an OLD result a fresh timestamp and corrupt the cadence.
        # The board's own recorded completion time wins; mtime is only the fallback when it is absent.
        e["mtime"] = _done_ts(e.get("result_done_utc")) or mt
        e["mtime_src"] = "result_done_utc" if _done_ts(e.get("result_done_utc")) else "file_mtime"
        e["file_mtime"] = mt
        e["path"] = p
        e["potential"] = potential_of(e)
        e["sched_tier"], e["tier_why"] = sched_tier(e)
        ledger[ss] = e
    # W = the biggest gainers (daily cap, ranked by potential; must beat 0, have the sample, and not be a lost cause)
    cands = [e for e in ledger.values() if e["sched_tier"] == "M" and e["potential"] is not None and e["potential"] > 0 and (e["trades"] or 0) >= 10]
    cands.sort(key=lambda e: -(e["potential"] - max(0.0, 0.5 * (e["bh"] or 0.0))))  # reward beating half of B&H, big absolute gain first
    for e in cands[:daily_cap]:
        e["sched_tier"] = "W"
        e["tier_why"] = f"daily: potential {e['potential']:.2f} (rank cap {daily_cap})"
    for e in ledger.values():
        e["cadence_d"] = CADENCE_D[e["sched_tier"]]
        e["next_due"] = e["mtime"] + e["cadence_d"] * 86400.0
        e["due"] = now >= e["next_due"]
    return ledger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "v15_symside_ledger.json"))
    ap.add_argument("--daily-cap", type=int, default=int(os.environ.get("V15_DAILY_CAP", "48")))
    ap.add_argument("--glob", action="append", default=None)
    args = ap.parse_args()
    globs = args.glob or [os.path.expanduser("~/v15_run*/progress/*_v14_progress.json"), os.path.join(ROOT, "data", "reports", "lifecycle_pilot", "*_v14_progress.json")]
    t0 = time.time()
    ledger = build(globs, args.daily_cap)
    counts = {}
    for e in ledger.values():
        k = f"{e['cat_side']}|{e['sched_tier']}"
        counts[k] = counts.get(k, 0) + 1
    out = {"_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "n": len(ledger), "daily_cap": args.daily_cap, "cadence_d": CADENCE_D, "counts": counts, "secs": round(time.time() - t0, 1), "globs": globs}, "sym_sides": ledger}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out + ".tmp", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(args.out + ".tmp", args.out)
    W = sorted([e for e in ledger.values() if e["sched_tier"] == "W"], key=lambda e: -(e["potential"] or 0))
    md = [f"# sym_side ledger — {out['_meta']['at']} ({len(ledger)} sym_sides, daily cap {args.daily_cap})", "", f"counts: {json.dumps(counts, sort_keys=True)}", "", "| DAILY (W) | potential | bh | trades | tim | dd | pilot_tier | age_h |", "|---|---|---|---|---|---|---|---|"]
    for e in W:
        md.append(f"| {e['sym_side']} | {e['potential']:.2f} | {e['bh']} | {e['trades']} | {e['tim']} | {e['dd']} | {e['pilot_tier']} | {(time.time()-e['mtime'])/3600:.1f} |")
    with open(args.out[:-5] + ".md", "w") as fh:
        fh.write("\n".join(md))
    print(json.dumps(out["_meta"], indent=1))


if __name__ == "__main__":
    sys.exit(main())
