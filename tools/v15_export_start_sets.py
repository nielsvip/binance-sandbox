#!/usr/bin/env python3
"""v15_export_start_sets — export the LIVE per_sym full sets so a worker can MEASURE on the set live trades.

WHY (measured 2026-10-15 23:4xZ): the crypto NPZ lives on S1 (318 files; the Mac has 21, so a Mac run returns
DATA_ERROR), but the per_sym truth lives on the Mac — S1's sandbox store held 444 rows against the Mac's 740, and
the three crypto tier-1 targets resolved every suspect switch out of their per_sym full set, not out of any
cat_side layer. A rerun started from S1's own store would therefore baseline off a set live does not run.

So: export here, measure on S1 with `--start-sets`, apply back here with `--apply-from` (per_sym has ONE writer).
`get_overrides` is used on purpose — it is the same accessor `_psym_get`/`_cfg` resolve live reads through, so the
export carries exactly the values live would read (after `_mandate_sanitize` strips MODE/VENUE/ABLATION_*).
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import per_sym_store as PSS


def export_published(args):
    """One row per sym_side: the latest publish by updated_at (the same rule tools/v15_published_selection.py:49
    uses — an after-the-fact insert must never win by id), plus the board's own metrics and the md5 the manifest is
    bound by. Read-only."""
    import hashlib

    con = sqlite3.connect(f"file:{PSS.DB_PATH}?mode=ro", uri=True)
    rows = con.execute("select sym_side, updated_at, gain_pct, trades, bh_pct, max_dd_pct, tim_pct, pool_sharpe, defaults_round, overrides_json from per_sym_history where json_extract(meta_json,'$.source')='fleet_orchestrator_publish' order by updated_at, id").fetchall()
    con.close()
    want = {s.strip().upper() for s in args.sym_sides.split(",") if s.strip()}
    sets = {}
    for (ss, upd, gain, trades, bh, dd, tim, psh, dround, ovj) in rows:
        ss = str(ss).upper()
        if want and ss not in want:
            continue
        if args.venue != "all":
            is_crypto = ss.rsplit("_", 1)[0].endswith(("USDT", "USDC", "BUSD", "FDUSD", "TUSD"))
            if (args.venue == "crypto") != is_crypto:
                continue
        try:
            ov = json.loads(ovj) if ovj else None
        except Exception:
            ov = None
        if not isinstance(ov, dict) or not ov:
            continue
        row = {"overrides": ov, "updated_at": upd, "gain_pct": gain, "trades": trades, "bh_pct": bh, "max_dd_pct": dd, "tim_pct": tim, "pool_sharpe": psh, "defaults_round": dround, "overrides_md5": hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()}
        prev = sets.get(ss)
        # The LATEST publish is what go-live would apply, the HIGHEST-gain publish is what 06 §1 calls the best start —
        # and those two gains were measured on different NPZ windows, so neither can be preferred on paper. Both ship;
        # the top-up re-measures them on today's NPZ and keeps whichever is actually higher (alt = the other one).
        if prev is not None:
            best_alt = prev if float(prev["gain_pct"] or -1e9) >= float(gain or -1e9) else row
            keep_alt = prev.get("alt")
            if keep_alt and float(keep_alt.get("gain_pct") or -1e9) > float(best_alt["gain_pct"] or -1e9):
                best_alt = keep_alt
            row["alt"] = {k: v for k, v in best_alt.items() if k != "alt"} if best_alt["overrides_md5"] != row["overrides_md5"] else None
        sets[ss] = row
    payload = {"exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "db": str(PSS.DB_PATH), "source": "per_sym_history source=fleet_orchestrator_publish, latest updated_at per sym_side", "n": len(sets), "keys_min": min((len(v["overrides"]) for v in sets.values()), default=0), "keys_max": max((len(v["overrides"]) for v in sets.values()), default=0), "publish_rows_scanned": len(rows), "sets": sets}
    Path(args.out).write_text(json.dumps(payload, indent=1, default=str))
    print(f"{args.out}: {len(sets)} published sym_sides ({payload['keys_min']}-{payload['keys_max']} keys each) from {len(rows)} publish rows")
    missing = sorted(want - set(sets))
    if missing:
        print(f"  REQUESTED BUT NEVER PUBLISHED: {', '.join(missing)}")
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="export live per_sym full sets for a remote measurement run")
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "v15_start_sets.json"))
    ap.add_argument("--sym-sides", default="", help="comma list; default every row in per_sym_active")
    ap.add_argument("--venue", default="all", choices=["all", "crypto", "stocks"])
    ap.add_argument("--published", action="store_true", help="export the LATEST PUBLISHED board set per sym_side (per_sym_history source=fleet_orchestrator_publish) instead of the live per_sym set — run on S1, the only host that holds per_sym_history (767 rows / 428 sym_sides; s2 holds 0), so a worker can top up boards it cannot recover locally")
    args = ap.parse_args()
    if args.published:
        return export_published(args)
    con = sqlite3.connect(f"file:{PSS.DB_PATH}?mode=ro", uri=True)
    rows = [(r[0], r[1]) for r in con.execute("select sym_side, cat_side from per_sym_active")]
    con.close()
    want = {s.strip().upper() for s in args.sym_sides.split(",") if s.strip()}
    sets, skipped = {}, []
    for sym_side, cat_side in rows:
        ss = str(sym_side).upper()
        if want and ss not in want:
            continue
        if args.venue != "all" and not str(cat_side or "").upper().startswith(args.venue.upper()[:6]):
            continue
        ov = PSS.get_overrides(ss)
        if not ov:
            skipped.append(ss)
            continue
        sets[ss] = ov
    missing = sorted(want - set(sets)) if want else []
    payload = {"exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "db": str(PSS.DB_PATH), "db_mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(PSS.DB_PATH.stat().st_mtime)), "source": "per_sym_store.get_overrides (the live read path)", "n": len(sets), "keys_min": min((len(v) for v in sets.values()), default=0), "keys_max": max((len(v) for v in sets.values()), default=0), "skipped_empty": skipped, "requested_missing": missing, "sets": sets}
    Path(args.out).write_text(json.dumps(payload, indent=1, default=str))
    print(f"{args.out}: {len(sets)} sym_sides, {payload['keys_min']}-{payload['keys_max']} keys each, db {payload['db_mtime']}")
    if skipped:
        print(f"  {len(skipped)} rows had no resolvable set: {', '.join(skipped[:10])}{' ...' if len(skipped) > 10 else ''}")
    if missing:
        print(f"  REQUESTED BUT MISSING: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
