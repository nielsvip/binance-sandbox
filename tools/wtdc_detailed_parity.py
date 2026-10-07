#!/usr/bin/env python
"""wtdc_detailed_parity.py — verify WT_DC-detailed live==vec on the 7 held stock configs.

For each sym_side in an overrides JSON ({SYM_SIDE: {overrides...}}, WT_DC_DETAILED_SCORER_ENABLED
forced True), runs the vector engine (v12_quick_engine) and the live-faithful verifier
(backtest_v12_engine via tools/_v12_runone.py subprocess) over the same 30D NPZ window, then
compares. This validates the threshold-alignment fix (detailed entry now fires live at 43 instead
of never crossing 55/85). RUNS ON s2 (needs stock NPZ + full env). Read-only, no orders.

Usage (on s2):  .venv/bin/python tools/wtdc_detailed_parity.py <overrides.json> [out.json]
"""
import os, sys, json, subprocess, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import numpy as np
import v12_quick_engine as V


def _is_long(ss):
    return ss.endswith("_LONG")


def _sym(ss):
    return ss[:-5] if ss.endswith("_LONG") else ss[:-6]


def _cfg(ov):
    c = V.QuickConfig(); c.MODE = "tradier"
    for k, v in ov.items():
        try: setattr(c, k, v)
        except Exception: pass
    return c


def _vec(sym, is_long, ov):
    stores = V.load_npz("tradier", [sym], "2024-01-01")
    npz = stores.get(sym)
    if npz is None or len(npz.get("timestamps", [])) < 100:
        return None
    ts = np.asarray(npz["timestamps"]); end = int(ts[-1]); start = end - 30 * 86400
    lo = int(np.searchsorted(ts, start)); n = len(ts)
    sl = {}
    for k, v in npz.items():
        sl[k] = v[lo:].copy() if isinstance(v, np.ndarray) and v.ndim > 0 and len(v) == n else v
    r = V.simulate_one(sl, sym, is_long, _cfg(ov))
    if not r:
        return {"gain": 0.0, "trades": 0}
    return {"gain": float(r.get("gain_pct_2000norm", 0.0)), "trades": int(r.get("trades", 0))}


def _live(ss, ov):
    tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in ov.items()}, tf)
    tf.close()
    runner = os.path.join(ROOT, "tools", "_v12_runone.py")
    env = dict(os.environ)
    env["TEST_RATE_GUARD_MIN_PER_DAY"] = "0"   # do not abort low-rate runs early — we WANT the true trade count
    env["PYTHONHASHSEED"] = "0"; env["V8_HASHSEED_LOCKED"] = "1"  # avoid the module-level re-exec
    try:
        r = subprocess.run([sys.executable, runner, ss, tf.name, "30"], capture_output=True, text=True, timeout=600, env=env)
        for ln in r.stdout.splitlines():
            if ln.startswith("V12RESULT "):
                d = json.loads(ln[10:])
                return {"gain": float(d.get("gain_pct") or 0), "trades": int(d.get("trades") or 0),
                        "valid": bool(d.get("valid")), "sharpe": float(d.get("pool_sharpe") or 0)}
        return {"error": "no-result", "stderr": (r.stderr or "")[-200:]}
    except subprocess.TimeoutExpired:
        return {"error": "timeout600"}
    finally:
        try: os.unlink(tf.name)
        except Exception: pass


def _parity(live, vec):
    if not live or "error" in live or not live.get("valid"):
        return False, f"live invalid: {live.get('error') if live else 'none'}"
    lt, vt = live["trades"], vec["trades"]
    if lt == 0 and vt == 0:
        return True, "both 0 trades (no fire)"
    if lt == 0 or vt == 0:
        return False, f"one-sided trades live={lt} vec={vt}"
    ratio = vt / lt
    if not (0.80 <= ratio <= 1.25):
        return False, f"trade ratio {ratio:.2f} (live={lt} vec={vt})"
    lg, vg = live["gain"], vec["gain"]
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live={lg:.2f} vec={vg:.2f}"
    return True, f"OK ratio={ratio:.2f} live_g={lg:.2f} vec_g={vg:.2f}"


def main():
    ovf = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "data", "reports", "wtdc7_parity.json")
    cfgs = json.load(open(ovf))
    results = []
    for ss, ov in cfgs.items():
        ov = dict(ov); ov["WT_DC_DETAILED_SCORER_ENABLED"] = True
        vec = _vec(_sym(ss), _is_long(ss), ov)
        if vec is None:
            r = {"sym_side": ss, "pass": False, "reason": "npz missing"}
            results.append(r); print(f"{ss}: NPZ MISSING", flush=True); continue
        live = _live(ss, ov)
        ok, why = _parity(live, vec)
        r = {"sym_side": ss, "pass": bool(ok), "reason": why,
             "vec": vec, "live": live,
             "wt_dc": {k: ov[k] for k in ov if "WT_DC" in k}}
        results.append(r)
        print(f"[{'PASS' if ok else '----'}] {ss}: {why} | vec {vec['trades']}t/{vec['gain']:.2f}% "
              f"live {live.get('trades','?')}t/{live.get('gain','?')}", flush=True)
    npass = sum(1 for r in results if r.get("pass"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"n_pass": npass, "n": len(results), "results": results}, open(out, "w"), indent=1)
    print(f"\n[wtdc7_parity] {npass}/{len(results)} PASS -> {out}", flush=True)


if __name__ == "__main__":
    main()
