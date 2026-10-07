#!/usr/bin/env python3
"""v15_prune_orange — SAFELY prune per-tab orange (GENERAL) rows to the empirically-measured set.

Reads V15_FILTER_DELTAS_{catside}.csv (+ .summary.json). Per cat_side, classifies each GENERAL
filter across the sampled symbols (UNION):
  - NONZERO      : |gain delta|>1e-9 on ANY sample            -> KEEP (real effect)
  - EXERCISED_0  : never nonzero, but exercised (trade set changed) on >=1 sample, 0 gain effect -> PRUNE
  - UNEXERCISED  : never nonzero AND never exercised in the sample (never bound/fired) -> KEEP (flagged; NOT dead)
  - UNMEASURED   : timeout/invalid on all samples (not measured) -> KEEP
Only EXERCISED_0 filters are pruned (measured, real 0). Prune runs ONLY for cleanly-completed
cat_sides (summary.clean). Deletes matching orange rows (col-A fill FFE699) per applicable tab
(sheets_app->tab), bottom-up, preserving all kept rows' styling so _orange_block still detects them.
Backs up each template + writes a reversible manifest data/orange_prune_manifest_{ts}.json.
"""
import sys as _sys_guard
_sys_guard.exit("REFUSED (USER 2026-09-30): no script may add or remove template/sheet rows")
import argparse, csv, json, os, pathlib, shutil, sys, time, zipfile
import openpyxl
ROOT = pathlib.Path("/Users/niels/Documents/binance")
SP = ROOT / "SPREADSHEETS"; sys.path.insert(0, str(ROOT)); os.environ.setdefault("BASE_PATH", str(ROOT))
import v15_pilot as P
THRESH = 1e-9
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
                 "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND",
                 "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
CAT_TO_TABS = {"ENTRY": ["ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES"],
               "EXIT": ["EXIT_STRUCTURAL", "EXIT_VELOCITY"], "STOCKS_EXIT": ["EXIT_STRUCTURAL", "EXIT_VELOCITY"],
               "REENTRY": ["REENTRY_WINDOWED", "REENTRY_ADAPTIVE"], "AUGMENT": ["AUGMENT_TREND", "AUGMENT_RISK_SIZING"],
               "REDUCE": ["REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"], "GLOBAL_CHECK": ["GLOBAL_RISK_GATES"],
               "STDEV": ["STDEV_SLOPE_SIZING"], "UNIVERSAL": SWITCH_SHEETS}
TMPL = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx",
        "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}


def tabs_for(sa):
    sa = (sa or "").upper(); t = set()
    for c, tl in CAT_TO_TABS.items():
        if c in sa:
            t.update(tl)
    return t


def classify(csvp):
    nz, exd, unex = set(), set(), set()
    seen = set()
    with open(csvp) as fh:
        for r in csv.DictReader(fh):
            if r["kind"] != "orange":
                continue
            f = r["filter"]; seen.add(f)
            d = r.get("delta"); ex = r.get("exercised")
            try:
                ad = abs(float(d)) if d not in (None, "") else 0.0
            except Exception:
                ad = 0.0
            if ad > THRESH:
                nz.add(f)
            elif ex == "yes":
                exd.add(f)
            elif ex == "no":
                unex.add(f)
    nz_final = nz
    exd_final = (exd - nz)
    unex_final = (unex - nz - exd)
    return {"nonzero": sorted(nz_final), "exercised_zero": sorted(exd_final), "unexercised": sorted(unex_final), "seen": sorted(seen)}


def verify(p):
    with zipfile.ZipFile(p) as z:
        if len(z.namelist()) < 10 or z.testzip() is not None:
            return False
    wb = openpyxl.load_workbook(p); ok = len(wb.sheetnames) > 5; wb.close(); return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catsides", default="STOCKS_LONG,STOCKS_SHORT,CRYPTO_LONG,CRYPTO_SHORT")
    ap.add_argument("--csv-dir", default=str(SP))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    fd = P._load_filter_dictionary()
    sa_of = {}
    for e in fd:
        f = (e.get("filter") or "").strip()
        if f:
            sa_of.setdefault(f, e.get("sheets_app") or "")
    ts = time.strftime("%Y%m%d%H%M")
    manifest = {"ts": ts, "thresh": THRESH, "catsides": {}}
    for cs in [c.strip() for c in args.catsides.split(",") if c.strip()]:
        csvp = os.path.join(args.csv_dir, f"V15_FILTER_DELTAS_{cs}.csv")
        sump = csvp.replace(".csv", ".summary.json")
        if not os.path.exists(csvp) or not os.path.exists(sump):
            manifest["catsides"][cs] = {"status": "DEFERRED_no_csv"}; print(f"{cs}: DEFERRED (no csv/summary)"); continue
        summ = json.loads(open(sump).read())
        if not summ.get("clean"):
            manifest["catsides"][cs] = {"status": "DEFERRED_not_clean", "used": summ.get("n_used"), "timeouts": summ.get("eval_timeouts")}
            print(f"{cs}: DEFERRED (not clean: used={summ.get('n_used')} timeouts={summ.get('eval_timeouts')})"); continue
        cls = classify(csvp)
        prune_set = set(cls["exercised_zero"])
        p = SP / TMPL[cs]
        shutil.copy2(p, ROOT / "backups" / f"before_prune_orange_{ts}_{TMPL[cs]}")
        wb = openpyxl.load_workbook(p)
        per_tab = {}
        for sh in SWITCH_SHEETS:
            if sh not in wb.sheetnames:
                continue
            ws = wb[sh]
            todel = []
            for r in range(3, ws.max_row + 1):
                a = ws.cell(row=r, column=1)
                if not str(a.fill.fgColor.rgb or "").upper().endswith("FFE699"):
                    continue
                f = a.value
                if f in prune_set and sh in tabs_for(sa_of.get(f, "")):
                    todel.append(r)
            for r in sorted(todel, reverse=True):
                if not args.dry_run:
                    ws.delete_rows(r, 1)
            if todel:
                per_tab[sh] = len(todel)
        manifest["catsides"][cs] = {"status": ("DRY" if args.dry_run else "PRUNED"), "used_symbols": summ.get("used_symbols"),
                                    "coverage_days": summ.get("coverage_days"), "window": summ.get("max_window"),
                                    "pruned_filters_measured_zero": sorted(prune_set), "kept_nonzero": cls["nonzero"],
                                    "kept_unexercised_flagged": cls["unexercised"], "pruned_rows_per_tab": per_tab}
        if not args.dry_run:
            tmp = str(p) + ".build.xlsx"
            wb.save(tmp); wb.close()
            if verify(tmp):
                os.replace(tmp, p); print(f"{cs}: PRUNED prune_filters={len(prune_set)} rows_removed={sum(per_tab.values())} kept_nonzero={len(cls['nonzero'])} kept_unexercised={len(cls['unexercised'])}")
            else:
                print(f"{cs}: VERIFY FAIL — original kept")
        else:
            wb.close(); print(f"{cs}: DRY prune_filters={len(prune_set)} would_remove_rows={sum(per_tab.values())} kept_nonzero={len(cls['nonzero'])} kept_unexercised={len(cls['unexercised'])}")
    mp = ROOT / "data" / f"orange_prune_manifest_{ts}.json"
    mp.write_text(json.dumps(manifest, indent=1))
    print(f"MANIFEST {mp}")


if __name__ == "__main__":
    main()
