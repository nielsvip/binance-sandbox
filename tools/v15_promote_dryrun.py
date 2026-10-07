#!/usr/bin/env python3
"""v15_promote_dryrun — Stage 2 (DRY RUN, changes nothing) of DAILY_OPTIMIZATION_PLAN.md.

From the latest round's real deltas (progress JSON 'done' entries), decide — per cat_side — which
switch VALUE / filter should be PROMOTED to the new default, and emit a review plan. Reads nothing
from the templates (avoids racing concurrent template edits) and writes NO config/template — it only
produces data/reports/promotion_plan_<date>.{json,md} for operator review before any real apply.

Promotion rule (data-justified 2026-09-29; median-over-all was degenerate — see plan Q2):
  driver = avg (arithmetic mean) of the delta for a (switch,value) or filter, gated by
  pos_sym >= MIN_POS and breadth pos_sym/n >= MIN_FRAC (guards against a single lucky symbol).
  For a switch, the promoted VALUE is the candidate value with the best gated avg.
"""
import argparse, datetime, glob, json, os, pathlib, statistics

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROG = ROOT / "data" / "reports" / "lifecycle_pilot"
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
EPS = 1e-9


def cat_side_of(symside):
    s = symside.upper()
    side = "LONG" if s.endswith("_LONG") else "SHORT" if s.endswith("_SHORT") else None
    if not side:
        return None
    base = s[: -(len(side) + 1)]
    venue = "CRYPTO" if base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "STOCKS"
    return f"{venue}_{side}"


def parse_switch(key):
    # "TAB!row:SWITCH=value" -> (SWITCH, value)
    try:
        expr = key.split(":", 1)[1]
        name, _, val = expr.partition("=")
        return name.strip(), val.strip()
    except Exception:
        return None, None


def collect():
    # switch: {cat_side: {switch: {value: [deltas]}}}; filter: {cat_side: {hdr: [deltas]}}
    sw = {cs: {} for cs in CAT_SIDES}
    fl = {cs: {} for cs in CAT_SIDES}
    for pj in glob.glob(str(PROG / "*_v14_progress.json")):
        symside = os.path.basename(pj)[: -len("_v14_progress.json")]
        cs = cat_side_of(symside)
        if cs is None:
            continue
        try:
            done = (json.load(open(pj)) or {}).get("done", {})
        except Exception:
            continue
        for key, e in (done.items() if isinstance(done, dict) else []):
            if not isinstance(e, dict):
                continue
            name, val = parse_switch(key)
            if name:
                try:
                    sw[cs].setdefault(name, {}).setdefault(val, []).append(float(e.get("delta") or 0.0))
                except Exception:
                    pass
            for hdr, d in (e.get("yellows") or {}).items():
                try:
                    fl[cs].setdefault(hdr, []).append(float(d or 0.0))
                except Exception:
                    pass
    return sw, fl


# Sizing params inflate absolute gain via quantity (not a real edge) → EXCLUDE from promotion.
# STDEV_SLOPE_SIZING is the sanctioned dynamic-sizing strategy exception (operator 2026-09-29).
SIZING_TOKENS = ("START_POSITION_SIZE", "MAX_POSITION_SIZE", "MIN_POSITION_SIZE", "MAX_ORDER_VALUE",
                 "POSITION_SIZE", "NOTIONAL", "TARGET_USD", "SIZE_USD", "BREAKOUT_SIZE", "ORDER_VALUE")


def is_sizing_excluded(name):
    if name.startswith("STDEV_SLOPE_SIZING") or name.startswith("STDEV"):
        return False
    return any(t in name for t in SIZING_TOKENS)


def gated(deltas, min_pos, min_frac, cap):
    n = len(deltas)
    pos = sum(1 for d in deltas if d > EPS)
    if n == 0 or pos < min_pos or pos / n < min_frac:
        return None
    # winsorize: cap each delta's magnitude at ±cap so one lucky/unlucky symbol can't drive a promotion.
    capped = [max(-cap, min(cap, d)) for d in deltas]
    winsor = statistics.fmean(capped)
    if winsor <= EPS:
        return None
    return {"score": round(winsor, 6), "avg_raw": round(statistics.fmean(deltas), 6),
            "median": round(statistics.median(deltas), 6), "pos_sym": pos, "n": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pos", type=int, default=2)
    ap.add_argument("--min-frac", type=float, default=0.05)
    ap.add_argument("--cap", type=float, default=10.0, help="winsorize each delta at +/- this (pp) to defang outliers")
    ap.add_argument("--date", default=datetime.datetime.utcnow().strftime("%Y%m%d"))
    args = ap.parse_args()
    sw, fl = collect()
    plan = {}
    excluded = {}
    for cs in CAT_SIDES:
        promotions = []
        excl = []
        # switches: best gated value per switch (skip sizing params except STDEV)
        for name, byval in sw[cs].items():
            if is_sizing_excluded(name):
                excl.append(name)
                continue
            best = None
            for val, deltas in byval.items():
                g = gated(deltas, args.min_pos, args.min_frac, args.cap)
                if g and (best is None or g["score"] > best["score"]):
                    best = {"kind": "switch", "name": name, "promote_value": val, **g}
            if best:
                promotions.append(best)
        # filters: hdr already encodes value
        for hdr, deltas in fl[cs].items():
            if is_sizing_excluded(hdr):
                excl.append(hdr)
                continue
            g = gated(deltas, args.min_pos, args.min_frac, args.cap)
            if g:
                promotions.append({"kind": "filter", "name": hdr, "promote_value": hdr, **g})
        promotions.sort(key=lambda p: -p["score"])
        plan[cs] = promotions
        excluded[cs] = sorted(set(excl))
        print(f"[{cs}] promote_candidates={len(promotions)} "
              f"(switch={sum(1 for p in promotions if p['kind']=='switch')} "
              f"filter={sum(1 for p in promotions if p['kind']=='filter')}) sizing_excluded={len(excluded[cs])}")
        for p in promotions[:6]:
            print(f"    {p['kind']:6} {p['name']}={p['promote_value']}  score={p['score']:+.4f} "
                  f"(raw_avg={p['avg_raw']:+.4f} median={p['median']:+.4f}) pos_sym={p['pos_sym']}/{p['n']}")
    outdir = ROOT / "data" / "reports"
    outdir.mkdir(parents=True, exist_ok=True)
    jpath = outdir / f"promotion_plan_{args.date}.json"
    jpath.write_text(json.dumps(plan, indent=2))
    mpath = outdir / f"promotion_plan_{args.date}.md"
    lines = [f"# Promotion plan {args.date} (DRY RUN — nothing applied)",
             f"Driver = winsorized mean (each delta capped at +/-{args.cap}pp so no single symbol drives a promotion); "
             f"gate: score>0 AND pos_sym>={args.min_pos} AND pos_sym/n>={args.min_frac}. raw_avg + median shown as cross-checks. "
             f"Sizing params EXCLUDED from promotion (gain-inflation via quantity, not a real edge) except STDEV_SLOPE_SIZING.", ""]
    for cs in CAT_SIDES:
        lines.append(f"## {cs} — {len(plan[cs])} candidates (sizing_excluded: {len(excluded[cs])})")
        lines.append("| kind | name | promote_value | score(winsor) | raw_avg | pos_sym/n | median |")
        lines.append("|---|---|---|---|---|---|---|")
        for p in plan[cs]:
            lines.append(f"| {p['kind']} | {p['name']} | {p['promote_value']} | {p['score']:+.4f} | {p['avg_raw']:+.4f} | {p['pos_sym']}/{p['n']} | {p['median']:+.4f} |")
        if excluded[cs]:
            lines.append(f"\n_sizing-excluded (not promoted): {', '.join(excluded[cs])}_")
        lines.append("")
    mpath.write_text("\n".join(lines))
    print(f"[plan] {jpath}")
    print(f"[plan] {mpath}")


if __name__ == "__main__":
    main()
