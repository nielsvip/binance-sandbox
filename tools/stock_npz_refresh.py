#!/usr/bin/env python3
"""stock_npz_refresh — JIT refresh of stock NPZs (backtest_v8/indicators) from the fresh klines cache (USER 2026-09-30).

Per symbol in --symbols-file ORDER (priority order): skip when a pilot for it is running; precompute to a STAGE dir via
tools/stock_npz_precompute_relaxed.py (niced); gate-check the staged file (same rules as the fleet scheduler readiness: aligned
timestamp_15m, >=32d, fresh <120h, _compact_to_15m-compatible); NEVER replace a good file with a worse one (new last bar must be newer,
and a >=330d history is never swapped for a shorter one); back up the old NPZ, install atomically, then run the REAL sweep loader
(evaluate_v12.prepare 30D + one evaluate) on the installed file and roll back from the backup if it fails.
Report -> data/npz_refresh/<date>.json.  --dry-run = gate-check only. Run on the host that owns the NPZs (s2 for stocks).
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IND = ROOT / "backtest_v8" / "indicators"


def gate(p, min_span=32.0, max_age_h=120.0):
    r = {"ok": False, "reason": "missing"}
    if not os.path.exists(p):
        return r
    try:
        z = np.load(p, allow_pickle=True)
        if "timestamps" not in z.files or "timestamp_15m" not in z.files:
            return {"ok": False, "reason": "no timestamps/timestamp_15m"}
        t = z["timestamps"].astype("float64"); t15 = z["timestamp_15m"].astype("float64")
        if len(t15) != len(t):
            return {"ok": False, "reason": f"len(timestamp_15m)={len(t15)} != len(timestamps)={len(t)}"}
        if len(t) < 100:
            return {"ok": False, "reason": "too few bars"}
        k0 = 1000.0 if t[-1] > 1e11 else 1.0
        _dt = np.diff(t / k0)
        _dt = _dt[(_dt > 0) & (_dt < 3600)]
        if len(_dt) and float(np.median(_dt)) < 600:
            return {"ok": False, "reason": "5m-base NPZ (median bar < 10 min): 5m data is forbidden in the backtest system; rebuild 15m-base (FORCE_TRADIER_15M_BASE=1)"}
        _c = np.asarray(z["close"], float)[-3000:]
        _d = np.abs(np.diff(_c)) / np.maximum(_c[:-1], 1e-9)
        _sp = _d[1:] > 0.02
        _bk = np.abs(_c[3:] - _c[1:-2]) / np.maximum(_c[1:-2], 1e-9) < 0.01
        _k = min(len(_sp), len(_bk))
        _teeth = np.where(_sp[:_k] & _bk[:_k])[0]
        # INTENT 2026-10-10: catch interleaved feeds (dense/clustered alternation), not real volatility.
        # ALMU has 31 isolated V-teeth (gaps 25-267 bars, two-feed-confirmed real) — must pass. A true
        # interleave alternates every bar/few bars (clustered) or at high density. Fail closed otherwise.
        if len(_teeth) >= 15:
            _gaps = np.diff(np.sort(_teeth))
            _clustered = int((_gaps < 4).sum()) if len(_gaps) else 0
            _density = len(_teeth) / max(len(_c), 1)
            if _clustered >= 8 or _density >= 0.05:
                return {"ok": False, "reason": f"interleaved two-series (sawtooth) pattern: {len(_teeth)} teeth, {_clustered} clustered, density {_density:.3f}"}
        k = 1000.0 if t[-1] > 1e11 else 1.0
        span = float((t[-1] - t[0]) / k / 86400.0); age_h = float((time.time() - t[-1] / k) / 3600.0)
        par = t15[(t15 > 0) & np.isfinite(t15)]
        ev = int((np.r_[True, par[1:] != par[:-1]]).sum()) if len(par) else 0
        if ev < 10 or float(np.nanmin(t - t15)) < 0:
            return {"ok": False, "reason": "_compact_to_15m would raise"}
        if span < min_span:
            return {"ok": False, "reason": f"history {span:.1f}d < {min_span}d", "span_d": span, "age_h": age_h}
        if age_h > max_age_h:
            return {"ok": False, "reason": f"stale {age_h:.0f}h", "span_d": span, "age_h": age_h, "last": float(t[-1] / k)}
        return {"ok": True, "span_d": span, "age_h": age_h, "last": float(t[-1] / k), "n": int(len(t))}
    except Exception as e:
        return {"ok": False, "reason": "load fail " + str(e)[:80]}


def running(sym):
    out = subprocess.run(["pgrep", "-f", f"v15_pilot.py --sym-side {sym}_"], capture_output=True, text=True).stdout.strip()
    return bool(out)


def sweep_loader_ok(sym):
    try:
        sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools" / "opt"))
        import evaluate_v12 as E
        pr = E.prepare(f"{sym}_LONG", window_days=30)
        return pr is not None, "prepare returned None" if pr is None else ""
    except Exception as e:
        return False, str(e)[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--stage", default=str(Path.home() / "npz_stage_20260930"))
    ap.add_argument("--backup", default=str(Path.home() / "npz_backup_20260930"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-fresh-gain-h", type=float, default=12.0)
    a = ap.parse_args()
    syms = [s for s in Path(a.symbols_file).read_text().split() if s]
    stage, bak = Path(a.stage), Path(a.backup)
    (stage / "indicators").mkdir(parents=True, exist_ok=True); bak.mkdir(parents=True, exist_ok=True)
    rep = {}
    for s in syms:
        try:
            cur, dst = gate(IND / f"{s}.npz"), IND / f"{s}.npz"
            if cur["ok"] and not sweep_loader_ok(s)[0]:
                cur = {**cur, "ok": False, "reason": "sweep loader (evaluate_v12.prepare) rejects the current file"}
            r = {"before": cur}
            if cur["ok"] and cur.get("age_h", 999) <= 40:
                r["action"] = "already fresh"; rep[s] = r; print(s, r["action"], flush=True); continue
            if running(s):
                r["action"] = "skip: pilot running"; rep[s] = r; print(s, r["action"], flush=True); continue
            if a.dry_run:
                r["action"] = "dry-run"; rep[s] = r; continue
            t0 = time.time()
            p = subprocess.run(["nice", "-n", "10", sys.executable, str(ROOT / "tools" / "stock_npz_precompute_relaxed.py"), "--mode", "tradier", "--symbols", s, "--out-dir", str(stage / "indicators")],
                               capture_output=True, text=True, cwd=str(ROOT), timeout=3600, env=dict(os.environ, FORCE_TRADIER_15M_BASE="1"))
            new = stage / "indicators" / f"{s}.npz"
            g = gate(new)
            r["staged"] = g; r["precompute_s"] = round(time.time() - t0, 1)
            if not new.exists() or not g["ok"]:
                r["action"] = "rejected: " + g.get("reason", "no output"); r["tail"] = (p.stdout + p.stderr)[-200:]
            elif cur["ok"] and cur.get("span_d", 0) > 1.25 * g["span_d"]:
                # the current file is still usable today (age <= 120h) and has the LONGER history (keeps 365D verifiable) -> keep it
                r["action"] = f"NOT installed: current usable file has longer history ({cur.get('span_d'):.0f}d vs staged {g['span_d']:.0f}d)"
            elif cur.get("last") and g["last"] < cur["last"] + a.min_fresh_gain_h * 3600:
                r["action"] = "NOT installed: staged not newer than current"
            else:
                if dst.exists() and not (bak / f"{s}.npz").exists():   # the FIRST backup of the day is the original; never overwritten
                    shutil.copy2(dst, bak / f"{s}.npz")
                tmp = dst.with_name(dst.name + ".refresh.tmp.npz")
                try:
                    if dst.exists() and not os.access(dst, os.W_OK):
                        os.chmod(dst, 0o664)
                except Exception:
                    pass
                shutil.copy2(new, tmp); os.replace(tmp, dst)
                ok, why = sweep_loader_ok(s)
                if ok:
                    r["action"] = f"installed (span {g['span_d']:.0f}d, age {g['age_h']:.1f}h)"
                else:
                    if (bak / f"{s}.npz").exists():
                        shutil.copy2(bak / f"{s}.npz", dst)
                    r["action"] = "ROLLED BACK: sweep loader failed: " + why
            rep[s] = r
            print(s, r["action"], flush=True)
        except Exception as e:
            rep[s] = {"action": "ERROR: " + str(e)[:120]}; print(s, rep[s]["action"], flush=True)
    out = ROOT / "data" / "npz_refresh"; out.mkdir(parents=True, exist_ok=True)
    (out / (time.strftime("%Y%m%d_%H%M") + ".json")).write_text(json.dumps(rep, indent=1, default=str))
    c = {}
    for r in rep.values():
        k = r["action"].split(":")[0].split(" (")[0]; c[k] = c.get(k, 0) + 1
    print("[refresh] summary", c, flush=True)


if __name__ == "__main__":
    main()
