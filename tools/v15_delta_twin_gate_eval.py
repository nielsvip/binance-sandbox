"""Delta-twin gate promotion rule (USER 2026-10-10).

DELTA_EXIT_SPEED_DECAY_VEC_ENABLED defaults False UNTIL it proves profitable for
a sym_side; if the cat_side avg delta is positive it becomes the default for all
symbols in the cat_side — like every switch. The gate is VEC_ONLY (live always
runs the rich path, so no live read exists and the row-adder correctly refuses
it), therefore promotion CANNOT go through sweep rows. This tool is the sanctioned
substitute: it measures flag on/off deltas per sym_side with the same engine the
sweeps use and applies the same rule (positive -> per-sym enable; cat_side avg
positive -> recommend default flip).

Reads/writes: reads NPZ + live per-sym sets; writes NOTHING unless --apply-per-sym
(upserts per-sym overrides for positive sym_sides, like a promotion) — the config
default flip stays a user order (it changes every board).

Usage (S1, fresh NPZ):
  python3 tools/v15_delta_twin_gate_eval.py --sides LONG,SHORT --max-syms 60
  python3 tools/v15_delta_twin_gate_eval.py --syms ENAUSDC,BTCUSDC --sides LONG
  python3 tools/v15_delta_twin_gate_eval.py --sides LONG,SHORT --apply-per-sym
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GATE = "DELTA_EXIT_SPEED_DECAY_VEC_ENABLED"


def _universe(max_syms: int, only: str, sides: list) -> list:
    if only:
        syms = [s.strip().upper() for s in only.split(",") if s.strip()]
    else:
        ind = ROOT / "backtest_v8" / "indicators"
        syms = sorted(p.stem for p in ind.glob("*.npz"))
    out = []
    for s in syms:
        base = s.upper()
        if base.endswith(("USDT", "USDC")):
            for side in sides:
                out.append(f"{base}_{side}")
        else:
            for side in sides:
                out.append(f"{base}_{side}")
        if max_syms and len(out) >= max_syms * len(sides):
            break
    return out[: max_syms * len(sides)] if max_syms else out


def _prepare_one(symside: str):
    from tools.opt import v12_pilot as vp
    from forward_parity.live_vs_vec import live_overrides
    base = symside.split("_")[0].upper()
    mode = "crypto" if base.endswith(("USDT", "USDC", "USD")) or base[:4].isdigit() else "stocks"
    try:
        prep = vp.prepare_batch(symside)
        if prep is None:
            return symside, None, {"error": "no-prepared"}
        ov, _ = live_overrides(symside, mode)
        return symside, prep, dict(ov or {})
    except Exception as e:
        return symside, None, {"error": str(e)[:160]}


def _eval_pair(job, window: int):
    from tools.opt import v12_pilot as vp
    symside, prep, ov = job
    try:
        r0 = vp.evaluate_prepared_sanitized(prep, ov, window)
        ov = dict(ov)
        ov[GATE] = True
        r1 = vp.evaluate_prepared_sanitized(prep, ov, window)
        g0, g1 = float(r0.get("gain_pct") or 0.0), float(r1.get("gain_pct") or 0.0)
        return symside, {"off": round(g0, 4), "on": round(g1, 4),
                         "delta": round(g1 - g0, 4),
                         "trades_off": int(r0.get("trades") or 0),
                         "trades_on": int(r1.get("trades") or 0)}
    except Exception as e:
        return symside, {"error": str(e)[:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--syms", default="")
    ap.add_argument("--sides", default="LONG,SHORT")
    ap.add_argument("--max-syms", type=int, default=60)
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--apply-per-sym", action="store_true")
    ap.add_argument("--out", default="data/reports/delta_twin_gate_eval.json")
    a = ap.parse_args()
    t0 = time.time()
    sides = [s.strip().upper() for s in a.sides.split(",") if s.strip()]
    uni = _universe(a.max_syms, a.syms, sides)
    res = {}
    jobs = []
    for ss in uni:  # prepare is serial: the NPZ loader keeps racy global state
        ss2, prep, ov = _prepare_one(ss)
        if prep is None:
            res[ss2] = ov
        else:
            jobs.append((ss2, prep, ov))
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for ss, r in ex.map(_eval_pair, jobs, [a.window] * len(jobs)):
            res[ss] = r
    cats: dict = {}
    for ss, r in res.items():
        if "delta" not in r:
            continue
        base = ss.split("_")[0].upper()
        cat = ("CRYPTO" if base.endswith(("USDT", "USDC", "USD")) or base[:4].isdigit() else "STOCKS") + "_" + ss.rsplit("_", 1)[1]
        c = cats.setdefault(cat, {"n": 0, "pos": 0, "sum": 0.0, "syms": []})
        c["n"] += 1
        c["sum"] += r["delta"]
        if r["delta"] > 1e-9:
            c["pos"] += 1
            c["syms"].append(ss)
    for c in cats.values():
        c["avg"] = round(c["sum"] / c["n"], 4) if c["n"] else 0.0
        c["flip_recommend"] = c["avg"] > 0
    applied = []
    if a.apply_per_sym:
        import per_sym_store as pss
        for cat, c in cats.items():
            for ss in c["syms"]:
                ov = dict(pss.get_overrides(ss) or {})
                ov[GATE] = True
                pss.upsert(ss, ov, meta={"gate_promoted": "delta-twin gate eval positive"})
                applied.append(ss)
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "window_days": a.window, "n": len(res), "elapsed_s": round(time.time() - t0, 1),
           "cats": cats, "applied_per_sym": applied, "results": res}
    (ROOT / a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({"cats": {k: {kk: v for kk, v in c.items() if kk != "syms"} for k, c in cats.items()},
                      "applied": len(applied)}, indent=1), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
