#!/usr/bin/env python3
"""Aggregate positive avg_delta winners per {CAT}_{SIDE} -> data/cat_side_best_defaults.json (Job 2).

MECHANISM ONLY — populate from CLEAN post-fix runs. Refuses to run without
--confirm-clean-data because the pre-2026-09-28 progress JSONs were produced with
corrupted yellow cells; setting real-money-adjacent defaults from them is a NO-LIES risk.

Aggregation: for each sym_side's progress 'done' map, take each switch's best promoted
candidate (max delta). Across all syms of a cat_side, a switch's avg_delta = mean of those
best deltas; winner value = the candidate achieving the best mean. A switch is written only
when avg_delta > --min-avg-delta over >= --min-syms syms. Protected knobs are never written.
"""
from __future__ import annotations
import argparse, json, re, glob
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cat_side_best_defaults.json"
PROTECTED = {"MIN_GAIN_TO_BUY_AGGRESSIVELY", "RATIO_MULTIPLIER", "HARD_STOP_LOSS_MAX_PAIN"}
CRYPTO_SUF = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")


def cat_side(sym: str, side: str) -> str:
    is_crypto = sym.upper().endswith(CRYPTO_SUF)
    return f"{'CRYPTO' if is_crypto else 'STOCKS'}_{side}"


def parse_key(k: str):
    # "SHEET!row:SWITCH=cand"
    m = re.match(r"[^!]+!\d+:(.+?)=(.*)$", k)
    return (m.group(1), m.group(2)) if m else (None, None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress-glob", default=str(ROOT / "data/reports/lifecycle_pilot/*_progress.json"))
    ap.add_argument("--min-syms", type=int, default=10)
    ap.add_argument("--min-avg-delta", type=float, default=0.05)
    ap.add_argument("--confirm-clean-data", action="store_true", help="REQUIRED: assert the progress data is from clean post-fix runs")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.confirm_clean_data:
        raise SystemExit("refusing: pass --confirm-clean-data only when the progress JSONs are from CLEAN post-yellow-fix runs (pre-fix data is corrupted; NO-LIES).")
    files = glob.glob(args.progress_glob)
    # per cat_side: switch -> cand -> [best deltas per sym]
    agg = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for f in files:
        b = Path(f).name
        m = re.match(r"(.+?)_(LONG|SHORT)_", b)
        if not m:
            continue
        cs = cat_side(m.group(1), m.group(2))
        try:
            done = (json.loads(Path(f).read_text()) or {}).get("done", {})
        except Exception:
            continue
        best = {}  # switch -> (cand, delta)
        for k, v in done.items():
            sw, cand = parse_key(k)
            if not sw or sw in PROTECTED:
                continue
            d = float(v.get("delta") or 0)
            if sw not in best or d > best[sw][1]:
                best[sw] = (cand, d)
        for sw, (cand, d) in best.items():
            if d > 0:
                agg[cs][sw][cand].append(d)
    out = json.loads(OUT.read_text()) if OUT.exists() else {}
    out.setdefault("_meta", {})
    for cs, sws in agg.items():
        winners = {}
        for sw, cands in sws.items():
            best_cand, best_mean, best_n = None, 0.0, 0
            n_syms = len({id(x) for c in cands.values() for x in c})  # approx
            total_n = sum(len(v) for v in cands.values())
            for cand, deltas in cands.items():
                mean = sum(deltas) / len(deltas)
                if mean > best_mean:
                    best_cand, best_mean, best_n = cand, mean, len(deltas)
            if best_cand is not None and best_mean > args.min_avg_delta and best_n >= args.min_syms:
                winners[sw] = {"value": best_cand, "avg_delta": round(best_mean, 5), "n_syms": best_n}
        out[cs] = {k: v["value"] for k, v in winners.items()}
        out.setdefault("_provenance", {})[cs] = winners
    out["_meta"]["populated"] = True
    out["_meta"]["min_syms"] = args.min_syms
    out["_meta"]["min_avg_delta"] = args.min_avg_delta
    if args.dry_run:
        for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
            print(f"{cs}: {len(out.get(cs, {}))} winners")
        return
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
