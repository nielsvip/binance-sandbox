#!/usr/bin/env python3
"""slp_worker_rules — worker side of v15_slope_sizing_autoset (run on s2/s5 inside a scratch overlay of the DEPLOYED engine).
usage: slp_worker_rules.py OVERLAY_NAME SYM_SIDE[,SYM_SIDE...] OUT.jsonl [OFFSETS=0]
For every sym_side/offset window evaluates the slope-sizing rule library (30D) and writes one JSON line:
{ss, off, feat{...}, res{rule: [gain_pct, trades, valid, tim_pct, max_dd_pct]}}.  Rules = deployed switches only, plus
cfg-based gates when the engine supports QuickConfig.SLOPE_SIZING_GATE (queue SLP/001)."""
import json, os, sys, time
overlay, syms, outp = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
offs = [int(x) for x in (sys.argv[4] if len(sys.argv) > 4 else "0").split(",")]
os.chdir("/tmp/" + overlay); sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), "tools", "opt"))
import numpy as np
import evaluate_v12 as E
import v12_quick_engine as V
TW = {"SLOPE_SIZING_LIVE_TWIN_ENABLED": True}
LIB = {"OFF": {}, "NONE": {"STDEV_SLOPE_SIZING_ENABLED": False, "BAND_SLOPE_SIZING_V2_ENABLED": False}, "ON": dict(TW),
       "ON_depth": {**TW, "STDEV_SLOPE_SIZING_MODE": "depth"}, "ON_btt": {**TW, "STDEV_SLOPE_SIZING_MODE": "bottom_to_top"},
       "ON_D4": {**TW, "STDEV_SLOPE_SIZING_D_MAX": 4.0}, "ON_D2": {**TW, "STDEV_SLOPE_SIZING_D_MAX": 2.0},
       "ON_4h": {**TW, "BAND_SLOPE_SIZING_V2_TF": "4h"}, "ON_bandonly": {**TW, "STDEV_SLOPE_SIZING_ENABLED": False}}
if hasattr(V.QuickConfig(), "SLOPE_SIZING_GATE"):
    for g in ("LOCFAV", "SLOPEFAV", "SLOPEFAV_LOCFAV", "VOLTOP", "VOLHI", "VOLLO", "HALF"):
        LIB["ON_" + g] = {**TW, "SLOPE_SIZING_GATE": g}
f = open(outp, "a")
for ss in syms:
    for off in offs:
        try:
            p = E.prepare(ss, 30, offset_days=off)
            if p is None:
                f.write(json.dumps({"ss": ss, "off": off, "err": "NO_PREPARE"}) + "\n"); f.flush(); continue
            n = p["npz_prepared"]; close = np.asarray(n["close"], float); ret = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-12)
            sl = np.asarray(n.get("lrL_slope_D", np.zeros(len(close))), float); pb = np.asarray(n.get("lrL_pct_b_D", np.full(len(close), 0.5)), float)
            atr = np.asarray(n.get("atr_1h", np.zeros(len(close))), float)
            feat = {"vol15m_pct": float(ret.std() * 100), "atr1h_pct": float(np.nanmean(np.where(close > 0, atr / close * 100, np.nan))),
                    "abs_slope_D": float(np.nanmean(np.abs(sl))), "range_pct": float((close.max() / close.min() - 1) * 100),
                    "bh_pct": float((close[-1] / close[0] - 1) * 100), "frac_pb_gt_half": float(np.nanmean(pb > 0.5))}
            out = {"ss": ss, "off": off, "feat": feat, "res": {}, "engine": os.popen("md5sum v12_quick_engine.py").read()[:8]}
            for k, ov in LIB.items():
                r = E.evaluate_prepared(p, dict(ov)); out["res"][k] = [r.get("gain_pct"), r.get("trades"), r.get("valid"), r.get("tim_pct"), r.get("max_dd_pct")]
            f.write(json.dumps(out) + "\n"); f.flush()
        except Exception as e:
            f.write(json.dumps({"ss": ss, "off": off, "err": repr(e)[:200]}) + "\n"); f.flush()
print("DONE", len(syms))
