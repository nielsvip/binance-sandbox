#!/usr/bin/env python3
"""prune_useless_knobs.py — take the provably-dead switches OFF the work-list.

USER 2026-07-22: "disable useless knobs and grey them out at once".

A switch earns removal ONLY on a wiring fact, never on a small number:

  RECONNECT  — every tested value left the trade list BIT-IDENTICAL to the baseline
               (inert=1 on every cell). The engine read the override and behaved exactly
               the same, so the knob is not wired to anything. No value of it can ever
               produce a number; running the rest of its grid is pure waste.
  DEGENERATE — >=2 values tested, all of them differ from baseline but NOT from each
               other. The knob is read but saturates (or its grid sits entirely on one
               side of a threshold), so the remaining values carry no information either.

Deliberately NOT pruned: switches with small-but-real deltas. "Small on this baseline" is
not "useless" — the MU_LONG campaign baseline trades 19 times in 2.3 years, and a knob that
cannot bind on 19 trades may well bind on the 821 the wt_5m-cross baseline produces. Only
bit-identical (inert) evidence is baseline-robust enough to prune on.

Writes data/useless_knobs.json — param_matrix_daemon skips these, export_switch_matrix_xls
greys them. Nothing is deleted from the DB; re-run after a rebaseline to re-open knobs.

Usage (S1):  python tools/prune_useless_knobs.py --syms MU
             python tools/prune_useless_knobs.py --syms MU --dry-run
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not SBX.exists():
    SBX = Path(__file__).resolve().parent.parent
DB = SBX / "data" / "param_results_stocks.db"
OUT = SBX / "data" / "useless_knobs.json"


def gated_off_prefixes():
    """Prefixes of features whose master _ENABLED flag is False in the baseline config.

    A sub-knob of a disabled feature is inert for a reason that has nothing to do with its
    wiring — e.g. every WT_3M_FORCE_OPEN_* knob reads as RECONNECT purely because
    WT_3M_FORCE_OPEN_ENABLED defaults to False (config_tradier.py:2319). Pruning those would
    delete exactly the knobs that matter the moment the feature is switched on — which is what
    happened to MU_LONG live on 2026-07-22. Never prune a sub-knob of a gated-off feature."""
    out = set()
    try:
        sys.path.insert(0, str(SBX))
        import config_tradier
        cfg = config_tradier.TradierConfig
        for name in dir(cfg):
            if name.endswith("_ENABLED") and getattr(cfg, name, None) is False:
                out.add(name[: -len("_ENABLED")])
    except Exception as exc:
        print(f"[warn] could not read config_tradier ({exc}) — pruning nothing gated", flush=True)
    return out


def classify(syms, campaign):
    con = sqlite3.connect(str(DB))
    con.execute("PRAGMA busy_timeout=120000")
    q = ("SELECT param, symbol, side, value_json, delta_vs_baseline_gain_mo, inert "
         "FROM param_cells WHERE mode='tradier' AND tier='ENGINE' "
         f"AND symbol IN ({','.join('?' * len(syms))})")
    args = list(syms)
    if campaign:
        q += " AND campaign=?"
        args.append(campaign)
    per = defaultdict(lambda: defaultdict(dict))
    for param, sym, side, val, delta, inert in con.execute(q, args):
        per[param][f"{sym}_{side}"][str(val)] = (delta, bool(inert))
    con.close()
    gated = gated_off_prefixes()

    def is_gated(param):
        return any(param.startswith(g + "_") or param == g for g in gated)
    reconnect, degenerate, keep = [], [], []
    for param, keys in per.items():
        if is_gated(param):
            keep.append(param)
            continue
        cells = [c for k in keys.values() for c in k.values()]
        if not cells:
            continue
        if all(i for _d, i in cells):
            reconnect.append(param)
            continue
        varies = False
        for key, vals in keys.items():
            scored = {v: d for v, (d, _i) in vals.items() if d is not None}
            if len(scored) >= 2 and len({round(d, 6) for d in scored.values()}) > 1:
                varies = True
                break
        if not varies and any(len(v) >= 2 for v in keys.values()):
            degenerate.append(param)
        else:
            keep.append(param)
    return sorted(reconnect), sorted(degenerate), sorted(keep)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="MU")
    ap.add_argument("--campaign", default=os.environ.get("PSC_CAMPAIGN", "stocks_baseline_v2_s4h"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.campaign.startswith("stocks_repaired_"):
        raise SystemExit(
            "REFUSED: prune_useless_knobs.py is historical-only and does not "
            "bind rows to side/current contract/master/range semantics. Use "
            "tools/exact_wiring_gate.py for repaired campaigns."
        )
    syms = [s.strip().upper() for s in a.syms.split(",") if s.strip()]
    reconnect, degenerate, keep = classify(syms, a.campaign)
    payload = {"generated_from": syms, "campaign": a.campaign,
               "reconnect": reconnect, "degenerate": degenerate,
               "note": "RECONNECT = every value bit-identical to baseline (knob unwired). "
                       "DEGENERATE = values differ from baseline but not from each other. "
                       "Both are skipped by param_matrix_daemon and greyed by the exporter. "
                       "Regenerate after any rebaseline — a knob inert on a 19-trade baseline "
                       "may bind on a trading one."}
    print(f"pilot={syms}  RECONNECT={len(reconnect)}  DEGENERATE={len(degenerate)}  still-useful={len(keep)}")
    if reconnect:
        print("  reconnect e.g.:", ", ".join(reconnect[:8]))
    if degenerate:
        print("  degenerate e.g.:", ", ".join(degenerate[:8]))
    if a.dry_run:
        print("(dry-run — not written)")
        return
    OUT.write_text(json.dumps(payload, indent=1))
    print(f"wrote {OUT} — {len(reconnect) + len(degenerate)} switches now off the work-list")


if __name__ == "__main__":
    main()
