#!/usr/bin/env python3
"""Exit-vectorization A/B across ALL live-book sym_sides (S1, vec engine only).

For each unique SYM_SIDE in tradeable_keys.json, run tools/opt/evaluate_v12 three
ways on the frozen 30d NPZ:
    base       — defaults (VIGILANCE_DC4 15m ON, MTF_ATR_TRAIL ON per live config.py)
    vig_off    — VIGILANCE_DC4_STOP_TF=OFF
    trail_off  — MTF_ATR_TRAIL_ENABLED=False

Records gain/trades plus per-exit-family fire counts from the execution ledger, so
the switch deltas are attributable (fires>0) instead of null-test noise. DIAGNOSTIC
output (gains + fire counts only, no Sharpe columns) →
data/reports/exit_vec_ab_all_symsides.json.

Usage: python3 tools/exit_vec_ab_all_symsides.py [--workers N] [--limit N]
"""
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.opt.evaluate_v12 import evaluate

WINDOW = 30
CASES = {
    "base": {},
    "vig_on": {"VIGILANCE_GUARD_ENABLED": True},  # 4th mandate: default OFF, ON is the variant
    "trail_off": {"MTF_ATR_TRAIL_ENABLED": False},
    "dc_off": {"MTF_DC_REJECT_EXIT_ENABLED": False},
    "bb_off": {"MTF_BB_REJECT_EXIT_ENABLED": False},
    "wt_off": {"MTF_GR_EXIT_GATE_ENABLED": False},
    "dc4_on": {"MTF_DC_REJECT_USE_DC4": True},
}
FAMILIES = ("VIGILANCE_DC4", "MTF_ATR_TRAIL", "MTF_DC_REJECT", "MTF_BB_REJECT", "MTF_GR_WT_EXIT")


def uniq_symsides():
    keys = json.load(open(ROOT / "tradeable_keys.json"))
    out = []
    seen = set()
    for k in keys:
        ss = k.split(":", 1)[1] if ":" in k else k
        if ss not in seen:
            seen.add(ss)
            out.append(ss)
    return out


def run_one(symside):
    row = {}
    for case, ov in CASES.items():
        try:
            r = evaluate(symside, dict(ov), window_days=WINDOW, include_ledger=True)
            led = r.get("execution_ledger") or []
            fires = Counter()
            for t in led:
                if t.get("type") != "CLOSE":
                    continue
                rs = str(t.get("exit_reason", "") or t.get("reason", ""))
                for fam in FAMILIES:
                    if fam in rs:
                        fires[fam] += 1
            row[case] = {
                "gain_pct": round(float(r.get("gain_pct") or 0.0), 4),
                "trades": int(r.get("trades") or 0),
                "valid": bool(r.get("valid")),
                "invalid_reason": str(r.get("invalid_reason") or "")[:60],
                "fires": dict(fires),
            }
        except Exception as e:
            row[case] = {"error": f"{type(e).__name__}: {e}"[:200]}
    b = row.get("base", {})
    for case, label in (("vig_on", "vig_delta"), ("trail_off", "trail_delta"), ("dc_off", "dc_delta"),
                        ("bb_off", "bb_delta"), ("wt_off", "wt_delta"), ("dc4_on", "dc4_delta")):
        c = row.get(case, {})
        if "gain_pct" in b and "gain_pct" in c:
            row[label] = round(b["gain_pct"] - c["gain_pct"], 4)
    return symside, row


def main():
    workers = 4
    limit = 0
    args = sys.argv[1:]
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    if "--limit" in args:
        limit = int(args[args.index("--limit") + 1])
    symsides = uniq_symsides()
    if limit:
        symsides = symsides[:limit]
    print(f"{len(symsides)} sym_sides, workers={workers}", flush=True)
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, ss): ss for ss in symsides}
        for f in as_completed(futs):
            ss, row = f.result()
            results[ss] = row
            done += 1
            b = row.get("base", {})
            print(f"[{done}/{len(symsides)}] {ss}: base g={b.get('gain_pct')} tr={b.get('trades')} "
                  f"fires={b.get('fires')} deltas v={row.get('vig_delta')} t={row.get('trail_delta')} dc={row.get('dc_delta')} bb={row.get('bb_delta')} wt={row.get('wt_delta')} dc4={row.get('dc4_delta')}",
                  flush=True)
    dest = ROOT / "data/reports/exit_vec_ab_all_symsides.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"window_days": WINDOW, "cases": {k: v for k, v in CASES.items()},
                                "results": results}, indent=1, default=str))
    n_vig = sum(1 for r in results.values() if (r.get("base", {}).get("fires", {}) or {}).get("VIGILANCE_DC4"))
    n_trail = sum(1 for r in results.values() if (r.get("base", {}).get("fires", {}) or {}).get("MTF_ATR_TRAIL"))
    n_err = sum(1 for r in results.values() if "error" in r.get("base", {}))
    print(f"WROTE {dest} — VIGILANCE fires on {n_vig}, ATR_TRAIL fires on {n_trail}, errors {n_err} "
          f"of {len(results)} sym_sides", flush=True)


if __name__ == "__main__":
    main()
