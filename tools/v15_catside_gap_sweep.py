#!/usr/bin/env python3
"""v15_catside_gap_sweep — blanket (cat_side-level) gap-threshold test.

USER 2026-10-28: the gap decision is not per symbol. Test every option on every sym_side of a cat_side and set the
best positive MEAN delta as the cat_side default (data/cat_side_defaults_4.json + the TEMPLATE bold rows).

Options (28): open-gap {OFF, 0.1, 0.3, 0.6} x close-gap {OFF, SHORTS|BOTH x 0.1|0.3|0.6}. Each option sets the on/off flag
and the threshold together.
Baseline per sym_side = the cat_side default set only (no per_sym overrides), so the delta is the blanket effect.

Run ON S2 or S6 (stock NPZ live there, updated every 15m; S5 is the live stock host and is not used for sweeps):
  python3 tools/v15_catside_gap_sweep.py --cat-side STOCKS_LONG --syms-file symbols_trb_long.json --out data/reports/catside_gap_STOCKS_LONG_<host>.json

NO-LIES: every number is simulate_one output on frozen NPZ via tools.dc_simple_8_sweep.eval_gain. Nothing is annualized.
A sym_side with 0 trades in the window is counted as zero delta, never dropped silently.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OPEN_THRS = [0.1, 0.3, 0.6]
CLOSE_MODES = ["OFF", "SHORTS", "BOTH"]
CLOSE_THRS = [0.1, 0.3, 0.6]


def options():
    """Open-gap mode {OFF, 0.1, 0.3, 0.6} x close-gap mode {OFF, SHORTS x thr, BOTH x thr}. The on/off flags are
    part of every option: the cat_side default for STOCKS_LONG has GAP_MOC_EXIT_ENABLED=False, so a threshold alone
    changes nothing for longs (found by the 2-symbol smoke test, 2026-10-28)."""
    out = []
    close_opts = [("OFF", None, {"GAP_CLOSE_MOC_EXIT_ENABLED": False})]
    for m in CLOSE_MODES[1:]:
        for c in CLOSE_THRS:
            close_opts.append((m, c, {
                "GAP_CLOSE_MOC_EXIT_ENABLED": True,
                "GAP_CLOSE_MOC_ONLY_FOR_SHORTS": m == "SHORTS",
                "GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT": c,
            }))
    open_opts = [("OFF", None, {"GAP_MOC_EXIT_ENABLED": False})]
    for o in OPEN_THRS:
        open_opts.append((o, None, {"GAP_MOC_EXIT_ENABLED": True, "GAP_PER_SYMBOL_AVG_THRESH_PCT": o}))
    for on, _, ov_o in open_opts:
        for cm, cc, ov_c in close_opts:
            ov = {**ov_o, **ov_c}
            out.append({"name": f"open_{on}__close_{cm}_{cc if cc is not None else ''}", "ov": ov})
    return out


def baseline_overrides(cat_side, defaults_path):
    import v12_quick_engine as V

    d = json.loads(Path(defaults_path).read_text())
    base = d[cat_side]
    cfg = V.QuickConfig()
    return {k: v for k, v in base.items() if k.isupper() and hasattr(cfg, k)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat-side", required=True, choices=["STOCKS_LONG", "STOCKS_SHORT"])
    ap.add_argument("--syms-file", required=True)
    ap.add_argument("--window", type=int, default=30)
    ap.add_argument("--out", required=True)
    ap.add_argument("--defaults", default=str(ROOT / "data" / "cat_side_defaults_4.json"))
    a = ap.parse_args()

    import v12_quick_engine as V
    from tools.dc_simple_8_sweep import eval_gain

    is_long = a.cat_side == "STOCKS_LONG"
    syms = [str(s).upper() for s in json.loads((ROOT / a.syms_file).read_text())]
    base_ov = baseline_overrides(a.cat_side, a.defaults)
    opts = options()
    rows = {}
    for sym in syms:
        try:
            stores = V.load_npz("tradier", [sym], "2024-01-01")
            npz = stores.get(sym) if isinstance(stores, dict) else stores
            if npz is None or len(npz.get("timestamps", [])) < 10:
                rows[sym] = {"error": "npz missing"}
                continue
        except Exception as e:
            rows[sym] = {"error": f"load_npz {e}"}
            continue
        base_r, err = eval_gain(npz, sym, is_long, dict(base_ov), a.window)
        if base_r is None:
            rows[sym] = {"error": f"baseline {err}"}
            continue
        base_g = float(base_r.get("gain_pct_2000norm", 0.0))
        res = {"base_gain": base_g, "base_trades": int(base_r.get("trades", 0)), "opts": {}}
        for op in opts:
            ov = dict(base_ov)
            ov.update(op["ov"])
            r, err = eval_gain(npz, sym, is_long, ov, a.window)
            if r is None:
                res["opts"][op["name"]] = {"error": str(err)}
                continue
            g = float(r.get("gain_pct_2000norm", 0.0))
            res["opts"][op["name"]] = {"gain": g, "delta": round(g - base_g, 6), "trades": int(r.get("trades", 0))}
        rows[sym] = res
        print(f"[{a.cat_side}] {sym} done", flush=True)

    summary = {}
    for op in opts:
        deltas, pos, n = [], 0, 0
        for sym, res in rows.items():
            if "opts" not in res:
                continue
            o = res["opts"].get(op["name"], {})
            if "delta" not in o:
                continue
            deltas.append(o["delta"])
            n += 1
            if o["delta"] > 0:
                pos += 1
        summary[op["name"]] = {
            "mean_delta": round(sum(deltas) / n, 6) if n else None,
            "n_sym": n,
            "n_positive": pos,
        }
    ranked = sorted(
        [(k, v) for k, v in summary.items() if v["mean_delta"] is not None],
        key=lambda kv: kv[1]["mean_delta"],
        reverse=True,
    )
    out = {
        "cat_side": a.cat_side,
        "window_days": a.window,
        "n_syms": len(syms),
        "baseline_overrides": base_ov,
        "defaults_file": a.defaults,
        "summary_ranked": ranked,
        "per_sym": rows,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote {a.out}; top: {ranked[:3]}", flush=True)


if __name__ == "__main__":
    main()
