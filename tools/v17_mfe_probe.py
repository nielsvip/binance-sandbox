#!/usr/bin/env python3
"""V17 MFE PROBE — user theory check (2026-10-13): "we exit on any wt cross so a position never grows to the 2-3% an augment needs".

Real engine: tools.opt.evaluate_v12.prepare + _evaluate_prepared_nocache(include_ledger=True) on worker NPZ (base = prepared
cat_side defaults, no overrides). Per realised CLOSE row: bars held, exit reason, max favourable excursion (MFE) from the
prepared 15m NPZ high/low (close fallback) between bar_entry and bar_exit, measured vs the trade's entry_price. Also the
re-entry structure: for consecutive trades, is the next entry a higher low (LONG: entry > previous entry) / lower high (SHORT).
Output JSON: per sym_side quantiles + share of trades whose MFE reaches 1/2/3/4.5 %% vs the effective augment trigger.
"""
import argparse
import collections
import concurrent.futures as cf
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("V15_EVAL_CACHE", "0")


def one(ss):
    from tools.opt import evaluate_v12 as E
    from tools.opt import v12_pilot as VP
    import vec_decisions.gain_ladder_augment as GL
    prep = E.prepare(ss, 30)
    if prep is None:
        return ss, {"error": "prepare failed"}
    r = E._evaluate_prepared_nocache(prep, VP._venue_augment_guard(prep, {}), include_ledger=True)
    led = r.get("execution_ledger") or []
    npz = prep["npz_prepared"]
    close = np.asarray(npz.get("close"), dtype=float)
    hi = np.asarray(npz.get("high_15m", npz.get("high", close)), dtype=float)  # compacted 15m NPZ carries high_15m/low_15m (no bare high/low)
    lo = np.asarray(npz.get("low_15m", npz.get("low", close)), dtype=float)
    hl_src = "high_15m/low_15m" if "high_15m" in npz else "close"
    is_long = prep["side"] == "LONG"

    class _C:
        pass

    c = _C()
    for k, v in (prep.get("base_cfg_dict") or {}).items():
        setattr(c, k, v)
    min_gain = GL.effective_min_gain(c)
    rows = [t for t in led if isinstance(t, dict) and "pnl_dollars" in t and t.get("bar_entry") is not None]
    mfe, held, reasons, ret = [], [], collections.Counter(), []
    hl_flags = []
    prev_entry = None
    for t in rows:
        be, bx = int(t["bar_entry"]), int(t["bar_exit"])
        ep = float(t.get("entry_price") or 0.0)
        if ep <= 0 or be < 0 or bx >= len(close) or bx < be:
            continue
        seg_h = hi[be: bx + 1]
        seg_l = lo[be: bx + 1]
        m = (np.nanmax(seg_h) / ep - 1.0) * 100.0 if is_long else (1.0 - np.nanmin(seg_l) / ep) * 100.0
        mfe.append(float(m))
        held.append(bx - be)
        reasons[str(t.get("exit_reason") or t.get("reason") or "")[:28]] += 1
        ret.append(float(t.get("pnl_pct") or 0.0))
        if prev_entry is not None:
            hl_flags.append(ep > prev_entry if is_long else ep < prev_entry)
        prev_entry = ep
    mfe_a, held_a = np.asarray(mfe), np.asarray(held)
    if not len(mfe_a):
        return ss, {"error": "no trades", "trades": int(r.get("trades") or 0)}

    def share(th):
        return round(float(np.mean(mfe_a >= th)) * 100.0, 2)

    return ss, {
        "trades": int(len(mfe_a)),
        "mfe_price_source": hl_src,
        "gain_pct": round(float(r.get("gain_pct") or 0.0), 4),
        "eff_augment_min_gain_pct": min_gain,
        "held_bars_15m": {"median": float(np.median(held_a)), "p75": float(np.percentile(held_a, 75)), "share_le_1bar_pct": round(float(np.mean(held_a <= 1)) * 100, 2)},
        "mfe_pct": {"p50": round(float(np.median(mfe_a)), 3), "p75": round(float(np.percentile(mfe_a, 75)), 3), "p90": round(float(np.percentile(mfe_a, 90)), 3), "max": round(float(mfe_a.max()), 3)},
        "share_mfe_ge": {"0.5": share(0.5), "1": share(1.0), "2": share(2.0), "3": share(3.0), "4.5": share(4.5), "eff_min": share(min_gain)},
        "n_mfe_ge_eff_min": int(np.sum(mfe_a >= min_gain)),
        "reentry_higher_low_share_pct": round(float(np.mean(hl_flags)) * 100, 2) if hl_flags else None,
        "top_exit_reasons": dict(reasons.most_common(8)),
        "mean_trade_ret_pct": round(float(np.mean(ret)), 4),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", required=True)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "v17_mfe_probe.json"))
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    sss = [s.strip() for s in a.symsides.split(",") if s.strip()]
    import multiprocessing as mp
    res = {}
    with cf.ProcessPoolExecutor(max_workers=min(a.workers, len(sss)), mp_context=mp.get_context("fork")) as ex:
        for ss, r in ex.map(one, sss):
            res[ss] = r
    res["_meta"] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "engine": "evaluate_v12._evaluate_prepared_nocache (real simulate_one), base = prepared cat_side defaults"}
    json.dump(res, open(a.out, "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
