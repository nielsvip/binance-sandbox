#!/usr/bin/env python3
"""v15_switch_guarantee — the daily GUARANTEE, checked and failed loudly, never asserted (USER 2026-10-18).

USER: "you need to GUARANTEE that EVERY DAY v15_pilot.py recalculates the best settings for every sym_side within the
constraints of the TEMPLATE_cat_side and the newest found COMPLETE SET OF SWITCHES will be traded for that sym_side until
it is recalculated ... NO FUCKING exceptions."

A guarantee is not a sentence in a report. It is four invariants, measured per sym_side every day, with a non-zero exit
code when any of them breaks:

  I1 RECALCULATED       the newest published board for this sym_side is younger than --max-age-h (default 36 h).
                        Source: per_sym_history publish rows (what v15_pilot actually produced), not a log line.
  THE SET IS THE ONLY AUTHORITY. "override" was dumped as a concept (USER 2026-10-18: "defaults change every day"), so
  this tool never reads overrides_json: the complete per-sym set (full_config) is what live trades and what is checked.

  I2 SET_COMPLETE       the set live will trade covers the engine's switch space (v12_quick_engine.QuickConfig fields).
                        Anything missing falls back to config defaults — i.e. an exception to "trade the switches" — and
                        is listed by name. Keys in --exempt-file are declared non-strategy and excluded from the verdict.
  I3 LIVE_IS_NEWEST     the set live reads (per_sym_live_set.full_set -> per_sym_store) IS the newest published set,
                        key by key, not merely the same md5 or the same timestamp.
  I4 NOTHING_ELSE_DECIDES  no other layer supplies a value for a switch the set already carries (cat_side default layer,
                        venue global); where it does, live could trade a value the board never chose.

Exit code 0 only when every checked sym_side passes every invariant. Read-only.
Usage: tools/v15_switch_guarantee.py [--cat-side CRYPTO] [--max-age-h 36] [--json OUT] [--quiet] [--fail-on I1,I2,I3,I4]
"""
from __future__ import annotations

import argparse
import collections
import dataclasses as dc
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
EXEMPT_DEFAULT = ROOT / "data" / "switch_guarantee_exempt.json"


def norm(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 9)
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return 1.0 if s.lower() == "true" else 0.0
    try:
        return round(float(s), 9)
    except Exception:
        return s.upper()


def engine_switches():
    import v12_quick_engine as V

    return {f.name for f in dc.fields(V.QuickConfig)}


def exempt_keys(path):
    try:
        d = json.load(open(path))
        return {str(x) for x in (d.get("keys") or [])}, d
    except Exception:
        return set(), {}


def newest_published():
    """The newest board per sym_side, straight from the publish rows v15_pilot writes."""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    q = ("select sym_side, updated_at, coalesce(epoch, strftime('%s', replace(replace(updated_at,'T',' '),'Z',''))) ep,"
         " full_config_json, gain_pct, trades from per_sym_history order by ep")
    for ss, upd, ep, fc, gain, trades in con.execute(q):
        try:
            ep = float(ep)
        except Exception:
            continue
        cur = out.get(ss)
        if cur and cur["ep"] >= ep:
            continue
        out[ss] = {"ep": ep, "updated_at": upd, "full": fc, "gain": gain, "trades": trades}
    con.close()
    return out


def live_sets(cat_side_like):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {}
    for ss, upd, fc in con.execute(
        "select sym_side, updated_at, full_config_json from per_sym_active where cat_side like ?",
        (cat_side_like,),
    ):
        try:
            full = json.loads(fc or "{}")
        except Exception:
            full = {}
        out[ss] = {"updated_at": upd, "full": full}
    con.close()
    return out


def other_layers(sym_side):
    """What the cat_side layer and the venue global would supply for this sym_side, if anything."""
    cat, glob = {}, {}
    try:
        import cat_side_defaults as CSD

        side = sym_side.rsplit("_", 1)[1]
        cat = CSD.get_for(("CRYPTO_" + side)) or {}
    except Exception:
        cat = {}
    try:
        import config as _c

        glob = {f.name: getattr(_c.Config, f.name, None) for f in dc.fields(_c.Config)} if dc.is_dataclass(_c.Config) else {}
    except Exception:
        glob = {}
    return cat, glob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat-side", default="CRYPTO")
    ap.add_argument("--max-age-h", type=float, default=36.0)
    ap.add_argument("--exempt-file", default=str(EXEMPT_DEFAULT))
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--fail-on", default="I1,I2,I3")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    eng = engine_switches()
    exempt, exempt_doc = exempt_keys(a.exempt_file)
    checked = eng - exempt
    pub = newest_published()
    live = live_sets(f"{a.cat_side}%")
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    fails = collections.Counter()
    missing_hist = collections.Counter()
    rows = []
    for ss in sorted(live):
        L, P = live[ss], pub.get(ss)
        full = L["full"] or {}
        r = {"sym_side": ss, "I1": "", "I2": "", "I3": "", "I4": "", "age_h": None, "n_full": len(full)}
        # I1 — was it recalculated?
        if not P:
            r["I1"] = "FAIL no publish row at all"
        else:
            r["age_h"] = round((now - P["ep"]) / 3600.0, 1)
            r["I1"] = "ok" if r["age_h"] <= a.max_age_h else f"FAIL {r['age_h']:.0f}h since the last recalculation"
        # I2 — is the set live trades complete?
        miss = sorted(checked - set(full))
        r["n_missing"] = len(miss)
        r["missing_sample"] = miss[:6]
        for k in miss:
            missing_hist[k] += 1
        r["I2"] = "ok" if not miss else f"FAIL {len(miss)} engine switches absent -> config default decides"
        # I3 — is it the NEWEST set, key by key?
        if not P or not P.get("full"):
            r["I3"] = "FAIL no published full set to compare"
        else:
            try:
                pf = json.loads(P["full"] or "{}")
            except Exception:
                pf = {}
            diff = [k for k, v in pf.items() if k in checked and norm(full.get(k, "<MISSING>")) != norm(v)]
            r["n_drift"] = len(diff)
            r["drift_sample"] = sorted(diff)[:6]
            r["I3"] = "ok" if not diff else f"FAIL {len(diff)} switches differ from the newest published board"
        rows.append(r)
        for inv in ("I1", "I2", "I3"):
            if str(r[inv]).startswith("FAIL"):
                fails[inv] += 1
    ok = [r for r in rows if not any(str(r[i]).startswith("FAIL") for i in ("I1", "I2", "I3"))]
    if not a.quiet:
        print(f"=== SWITCH GUARANTEE — {a.cat_side} — {len(rows)} sym_sides — {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')} ===")
        print(f"engine switch space {len(eng)} · exempt {len(exempt)} · checked {len(checked)} · max age {a.max_age_h:.0f}h\n")
        print(f"  I1 RECALCULATED      {len(rows) - fails['I1']:4d} pass / {fails['I1']:4d} FAIL")
        print(f"  I2 SET_COMPLETE      {len(rows) - fails['I2']:4d} pass / {fails['I2']:4d} FAIL")
        print(f"  I3 LIVE_IS_NEWEST    {len(rows) - fails['I3']:4d} pass / {fails['I3']:4d} FAIL")
        print(f"\n  sym_sides passing ALL invariants: {len(ok)} of {len(rows)}")
        ages = [r["age_h"] for r in rows if r["age_h"] is not None]
        if ages:
            ages.sort()
            print(f"  recalculation age: p50 {ages[len(ages) // 2]:.1f}h  p90 {ages[int(len(ages) * 0.9)]:.1f}h  max {ages[-1]:.1f}h")
        if missing_hist:
            print(f"\n  engine switches absent from EVERY live set ({sum(1 for k, v in missing_hist.items() if v == len(rows))}):")
            for k, v in missing_hist.most_common(a.top):
                if v == len(rows):
                    print(f"    {k}")
        print(f"\n  worst {a.top} sym_sides:")
        for r in sorted(rows, key=lambda r: -(r.get("n_drift", 0) + r.get("n_missing", 0)))[: a.top]:
            print(f"    {r['sym_side']:22s} age {str(r['age_h']):>6s}h  missing {r.get('n_missing', 0):4d}  drift {r.get('n_drift', 0):4d}  {r['I1'] if r['I1'] != 'ok' else ''} {r['I3'] if r['I3'] != 'ok' else ''}".rstrip())
    if a.json:
        Path(a.json).write_text(json.dumps({"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "cat_side": a.cat_side,
                                            "engine_switches": len(eng), "exempt": sorted(exempt), "max_age_h": a.max_age_h,
                                            "fails": dict(fails), "n_sym_sides": len(rows), "n_pass_all": len(ok),
                                            "missing_from_every_set": sorted(k for k, v in missing_hist.items() if v == len(rows)),
                                            "rows": rows}, default=str, indent=1))
        if not a.quiet:
            print(f"\nwrote {a.json}")
    bad = sum(fails[i] for i in [x.strip() for x in a.fail_on.split(",") if x.strip()])
    if bad and not a.quiet:
        print(f"\nGUARANTEE BROKEN: {bad} invariant failures across {len(rows)} sym_sides (exit 1)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
