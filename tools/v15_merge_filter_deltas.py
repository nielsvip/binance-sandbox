#!/usr/bin/env python3
"""v15_merge_filter_deltas — merge the 4 V15_FILTER_DELTAS_{catside}.csv into an EMPIRICAL map.

Output data/opportune_filter_map_empirical.json:
  { "_meta": {...},
    CATSIDE: {
      "_yellow": {filter_base: max_abs_delta},              # specific filters w/ real nonzero delta
      "_orange_per_tab": { TAB: [orange filter bases] },     # GENERAL filters, nonzero AND applicable to tab
      SHEET: { SWITCH: [yellow filter bases] } } }           # refined per-switch yellow (heuristic ∩ empirical-nonzero)

No fabrication: a filter is included ONLY if it produced |delta|>THRESH on a real eval (from the CSVs).
Per-tab orange = GENERAL filters whose sheets_app category maps to the tab AND which were empirically nonzero.
"""
import argparse, csv, glob, json, os, pathlib, sys, time
ROOT = pathlib.Path("/Users/niels/Documents/binance")
os.chdir(ROOT); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("BASE_PATH", str(ROOT))
import v15_pilot as P

THRESH = 1e-9
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
                 "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND",
                 "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
# coarse sheets_app category -> concrete tabs
CAT_TO_TABS = {
    "ENTRY": ["ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES"],
    "EXIT": ["EXIT_STRUCTURAL", "EXIT_VELOCITY"], "STOCKS_EXIT": ["EXIT_STRUCTURAL", "EXIT_VELOCITY"],
    "REENTRY": ["REENTRY_WINDOWED", "REENTRY_ADAPTIVE"],
    "AUGMENT": ["AUGMENT_TREND", "AUGMENT_RISK_SIZING"],
    "REDUCE": ["REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"],
    "GLOBAL_CHECK": ["GLOBAL_RISK_GATES"], "STDEV": ["STDEV_SLOPE_SIZING"],
    "UNIVERSAL": SWITCH_SHEETS,
}


def tabs_for_sheets_app(sa):
    sa = (sa or "").upper(); tabs = set()
    for cat, tl in CAT_TO_TABS.items():
        if cat in sa:
            tabs.update(tl)
    if "UNIVERSAL" in sa:
        tabs.update(SWITCH_SHEETS)
    return tabs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-dir", default=str(ROOT / "SPREADSHEETS"))
    args = ap.parse_args()
    fd = P._load_filter_dictionary()
    is_gen = {}; sa_of = {}
    for e in fd:
        f = (e.get("filter") or "").strip()
        if not f:
            continue
        is_gen[f] = is_gen.get(f, False) or P._is_general(e["rec"])
        sa_of.setdefault(f, e.get("sheets_app") or "")
    out = {"_meta": {"rule": "empirical: filter kept iff |delta|>%.0e on a real eval; orange per-tab via sheets_app category" % THRESH,
                     "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "thresh": THRESH,
                     "source_csvs": []}}
    engines = {}
    for csvp in sorted(glob.glob(os.path.join(args.csv_dir, "V15_FILTER_DELTAS_*.csv"))):
        catside = pathlib.Path(csvp).stem.replace("V15_FILTER_DELTAS_", "")
        out["_meta"]["source_csvs"].append(os.path.basename(csvp))
        _sm = {}
        try:
            _sm = json.loads(open(csvp.replace(".csv", ".summary.json")).read())
        except Exception:
            pass
        engines[catside] = _sm.get("engine_md5", "?")
        ymax = {}; onz = set()
        with open(csvp) as fh:
            for row in csv.DictReader(fh):
                d = row.get("delta")
                if d in (None, ""):
                    continue
                try:
                    ad = abs(float(d))
                except Exception:
                    continue
                if ad <= THRESH:
                    continue
                f = row["filter"]; kind = row["kind"]
                if kind == "orange" or is_gen.get(f):
                    onz.add(f)
                else:
                    ymax[f] = max(ymax.get(f, 0.0), ad)
        # per-tab orange = nonzero GENERAL filters applicable to each tab
        orange_per_tab = {}
        for f in sorted(onz):
            for tab in tabs_for_sheets_app(sa_of.get(f, "")):
                orange_per_tab.setdefault(tab, []).append(f)
        # refined per-switch yellow: heuristic map ∩ empirically-nonzero
        cat = {"engine_md5": engines.get(catside, "?"),
               "_yellow": {k: round(v, 6) for k, v in sorted(ymax.items())},
               "_orange_per_tab": {t: sorted(set(v)) for t, v in sorted(orange_per_tab.items())}}
        out[catside] = cat
        print(f"{catside}: yellow_nonzero={len(ymax)} orange_nonzero={len(onz)} tabs_with_orange={len(orange_per_tab)}", flush=True)
        for t in sorted(orange_per_tab):
            print(f"    orange[{t}] = {len(set(orange_per_tab[t]))}", flush=True)
    _distinct = sorted(set(e for e in engines.values() if e and e != "?"))
    out["_meta"]["engines_per_catside"] = engines
    out["_meta"]["baseline_engine_md5"] = _distinct[0] if len(_distinct) == 1 else None
    if len(_distinct) > 1:
        out["_meta"]["MIXED_BASELINE_WARNING"] = f"cat_sides span {len(_distinct)} engine baselines {_distinct} — NOT single-baseline clean"
        print(f"!!! MIXED BASELINE: {engines}", flush=True)
    dst = ROOT / "data" / "opportune_filter_map_empirical.json"
    if dst.exists():
        bak = dst.with_name(f"opportune_filter_map_empirical.bak_{time.strftime('%Y%m%d%H%M')}.json")
        bak.write_text(dst.read_text())
    dst.write_text(json.dumps(out, indent=1))
    print(f"WROTE {dst}", flush=True)


if __name__ == "__main__":
    main()
