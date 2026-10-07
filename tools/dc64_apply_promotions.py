#!/usr/bin/env python
"""dc64_apply_promotions.py — apply verified greedy winners to the LIVE per_sym config (Mac).

Reads data/reports/dc64_promotions.json (from tools/dc64_verify_promote.py on the servers).
For each result with pass==True, merges its `applied` switches into the live per_sym overrides
(crypto -> data/hourly_reconfig/per_sym_active_config.json, stocks -> ..._stocks.json).
ALWAYS backs up both configs first. Logs every change to data/reports/dc64_applied.md.
Only ADDS/updates the winning switches; never removes existing keys. Dry-run by default.

Usage:
  python tools/dc64_apply_promotions.py            # dry-run: show diffs, write nothing
  python tools/dc64_apply_promotions.py --apply    # write to live per_sym config (real money)
"""
import os, sys, json, argparse, datetime, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRYPTO = os.path.join(ROOT, "data", "hourly_reconfig", "per_sym_active_config.json")
STOCKS = os.path.join(ROOT, "data", "hourly_reconfig", "per_sym_active_config_stocks.json")
PROMO = os.path.join(ROOT, "data", "reports", "dc64_promotions.json")
LOG = os.path.join(ROOT, "data", "reports", "dc64_applied.md")


def _load(p):
    return json.load(open(p)) if os.path.exists(p) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write live config (else dry-run)")
    a = ap.parse_args()
    promo = _load(PROMO)
    passers = [r for r in promo.get("results", []) if r.get("pass")]
    if not passers:
        print("no passing promotions in", PROMO); return
    crypto = _load(CRYPTO); stocks = _load(STOCKS)
    ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    changes = []
    for r in passers:
        ss = r["sym_side"]; applied = r.get("applied", {})
        cfg = crypto if r.get("venue") == "crypto" else stocks
        entry = cfg.setdefault(ss, {"overrides": {}})
        ov = entry.setdefault("overrides", {})
        diff = {k: (ov.get(k), v) for k, v in applied.items() if ov.get(k) != v}
        if diff:
            changes.append((ss, r.get("venue"), r, diff))
            if a.apply:
                ov.update(applied)
                entry["_dc64_promoted"] = {"ts": ts, "d30": r.get("d30"), "d365": r.get("d365"), "live_gain30": r.get("live_gain30")}
    # report
    print(f"{'APPLYING' if a.apply else 'DRY-RUN'}: {len(changes)} sym_sides would change (of {len(passers)} passers)")
    for ss, venue, r, diff in changes[:40]:
        print(f"  {ss} [{venue}] base{r.get('base_gain30')}->NEW{r.get('final_gain30')} 365Δ{r.get('d365')} live{r.get('live_gain30')}")
        for k, (old, new) in diff.items():
            print(f"      {k}: {old} -> {new}")
    if not a.apply:
        print("\nDRY-RUN — nothing written. Re-run with --apply to write live per_sym config.")
        return
    # backup then write
    for p in (CRYPTO, STOCKS):
        if os.path.exists(p):
            shutil.copy(p, os.path.join(ROOT, "backups", f"{os.path.basename(p)}.BEFORE_apply_{ts}.json"))
    json.dump(crypto, open(CRYPTO, "w"), indent=1)
    json.dump(stocks, open(STOCKS, "w"), indent=1)
    with open(LOG, "a") as f:
        f.write(f"\n## dc64 promotion apply {ts} — {len(changes)} sym_sides\n")
        for ss, venue, r, diff in changes:
            f.write(f"- {ss} [{venue}] base{r.get('base_gain30')}->NEW{r.get('final_gain30')} d365={r.get('d365')} live={r.get('live_gain30')} sharpe={r.get('live_sharpe')} :: {json.dumps(r.get('applied'))}\n")
    print(f"\nAPPLIED {len(changes)} sym_sides to live per_sym config. Backups + log written. Live picks up on next reconfig cycle.")


if __name__ == "__main__":
    main()
