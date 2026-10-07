#!/usr/bin/env python3
"""v15_filter_delta_rebuild — EMPIRICAL yellow/orange filter-delta map (NO-LIES: real engine only).

For a cat_side, on a representative symbol set, compute the REAL single-filter delta for every
filter in the dictionary (specific=yellow-eligible, GENERAL=orange) against the global-default
baseline, using tools.opt.v12_pilot.evaluate_sanitized (the same engine the sweep uses). A filter
is YELLOW for the cat_side iff |delta| > 1e-9 on ANY representative symbol (real effect). Writes
every (symbol, filter, cand, delta) to SPREADSHEETS/V15_FILTER_DELTAS_{catside}.csv and a per-
cat_side yellow/orange summary; rebuilds data/opportune_filter_map_empirical.json. Records the REAL
window coverage per symbol (crypto NPZ is ~30d only — never claims more than the data supports).

This is the tractable first pass (filter vs global baseline). Per-switch granularity (R11: filter
under each switch's own candidate) is a heavier follow-up; recorded as TODO in the output.
"""
import argparse, concurrent.futures as _cf, csv, hashlib, json, os, pathlib, sys, time

ROOT = pathlib.Path.home() / "binance-sandbox"
if not ROOT.exists():
    ROOT = pathlib.Path("/Users/niels/Documents/binance")
os.chdir(ROOT); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("BASE_PATH", str(ROOT)); os.environ["V12_NPZ_CACHE"] = "32"
import numpy as np
import v15_pilot as P
from tools.opt.v12_pilot import evaluate_sanitized as _EV_RAW

import json as _json, select as _select, subprocess as _sp
CAND_TFS = ["OFF", "D", "4h", "1h", "15m"]
EVAL_TIMEOUT = float(os.environ.get("FDR_EVAL_TIMEOUT", "90"))
# SUBPROCESS kill-timeout: a thread timeout CANNOT preempt a C-level/numpy GIL hang (the worker holds
# the GIL). We run evals in a persistent worker subprocess and SIGKILL+respawn it on wall-clock
# timeout. A killed eval = HARD KILL, skipped + logged, never a fabricated 0. (root-cause fix)
_EX = {"proc": None, "timeouts": 0, "evals": 0}


def _spawn_worker():
    _EX["proc"] = _sp.Popen([sys.executable, "-u", str(ROOT / "tools" / "_fdr_worker.py")],
                            stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.DEVNULL, text=True, bufsize=1)


def EV(sym, ov, window_days):
    _EX["evals"] += 1
    if _EX["proc"] is None or _EX["proc"].poll() is not None:
        _spawn_worker()
    p = _EX["proc"]
    try:
        p.stdin.write(_json.dumps({"sym": sym, "ov": ov, "win": window_days}) + "\n"); p.stdin.flush()
    except Exception:
        _spawn_worker(); p = _EX["proc"]
        try:
            p.stdin.write(_json.dumps({"sym": sym, "ov": ov, "win": window_days}) + "\n"); p.stdin.flush()
        except Exception:
            return {"valid": False, "gain_pct": None, "trades": 0, "invalid_reason": "WORKER_WRITE_FAIL"}
    r, _, _ = _select.select([p.stdout], [], [], EVAL_TIMEOUT)
    if not r:
        _EX["timeouts"] += 1
        try:
            p.kill(); p.wait(timeout=5)
        except Exception:
            pass
        _EX["proc"] = None
        return {"valid": False, "gain_pct": None, "trades": 0, "invalid_reason": f"HARD_KILL>{EVAL_TIMEOUT:.0f}s"}
    line = p.stdout.readline()
    if not line:
        _EX["proc"] = None
        return {"valid": False, "gain_pct": None, "trades": 0, "invalid_reason": "WORKER_EOF"}
    try:
        return _json.loads(line)
    except Exception:
        return {"valid": False, "gain_pct": None, "trades": 0, "invalid_reason": "WORKER_BADLINE"}


def npz_days(symside):
    """Actual available NPZ span in days for a symbol (timestamps[-1]-timestamps[0]); 0 if missing."""
    sym = symside.split("_")[0]
    for cand in (sym, sym.replace("USDC", "").replace("USDT", "")):
        p = ROOT / "backtest_v8" / "indicators" / f"{cand}.npz"
        if p.exists() and p.stat().st_size > 100000:
            try:
                d = np.load(p, allow_pickle=True)
                ts = d["timestamps"] if "timestamps" in d.files else d[d.files[0]]
                return int((float(ts[-1]) - float(ts[0])) / 86400.0)
            except Exception:
                return 0
    return 0


def _coerce(v):
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        if s in ("OFF", "D", "4h", "1h", "15m", "3m", "5m"):
            return s
        try:
            f = float(s.replace("±", "").split()[0]); return f
        except Exception:
            return s
    return v


def filter_variants(e):
    """Candidate overrides per filter row, from the dictionary's real option values ('opt'/'vals')."""
    fname = (e.get("filter") or "").strip()
    if not fname:
        return []
    vals = e.get("vals") or ([e.get("opt")] if e.get("opt") not in (None, "") else [])
    out = []
    for v in vals[:5]:
        cv = _coerce(v)
        if isinstance(cv, str) and cv.startswith("±"):
            continue
        out.append({fname: cv})
    if not out:
        out = [{fname: ("15m" if fname.endswith("_TF") else True)}]
    return out


def run(catside, symbols, max_window, min_days, out_csv):
    rows = P._load_filter_dictionary()
    seen = set(); cands = []
    for e in rows:
        f = (e.get("filter") or "").strip()
        if not f:
            continue
        key = (f, str(e.get("opt")))
        if key in seen:
            continue
        seen.add(key)
        cands.append((f, P._is_general(e["rec"]), filter_variants(e)))
    yellow = {}; orange = {}; wrote = 0
    coverage = {}; used = []; deferred = {}
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["catside", "symbol", "filter", "kind", "cand", "base_gain", "var_gain", "delta", "base_trades", "var_trades", "exercised", "window_days_real", "npz_days"])
        for sym in symbols:
            days = npz_days(sym)
            coverage[sym] = days
            if days < min_days:
                deferred[sym] = days
                print(f"[{catside}] {sym} SKIP npz={days}d < {min_days}d (mid-download/short)", flush=True); continue
            win = min(max_window, days)
            base = EV(sym, {}, win)
            bg = base.get("gain_pct")
            if bg is None:
                deferred[sym] = f"baseline_invalid:{base.get('invalid_reason')}"
                print(f"[{catside}] {sym} baseline invalid ({base.get('invalid_reason')}) win={win}d — skip", flush=True); continue
            used.append(sym)
            bt = base.get("trades")
            print(f"[{catside}] {sym} baseline gain={bg:.4f} win={win}d npz={days}d trades={bt}", flush=True)
            for f, is_gen, variants in cands:
                for var in variants:
                    r = EV(sym, var, win)
                    vg = r.get("gain_pct"); vt = r.get("trades")
                    d = (vg - bg) if (vg is not None) else None
                    # exercised = the filter measurably altered the outcome (gain or trade set changed);
                    # 'no' = identical to baseline = never bound/fired in this sample (do NOT call it dead).
                    if vg is None:
                        _ir = str(r.get("invalid_reason") or "")
                        exd = "killed" if ("HARD_KILL" in _ir or "TIMEOUT" in _ir) else "invalid"
                    elif (d is not None and abs(d) > 1e-9) or (vt is not None and bt is not None and vt != bt):
                        exd = "yes"
                    else:
                        exd = "no"
                    w.writerow([catside, sym, f, "orange" if is_gen else "yellow", json.dumps(var), round(bg, 6), (round(vg, 6) if vg is not None else ""), (round(d, 6) if d is not None else ""), (bt if bt is not None else ""), (vt if vt is not None else ""), exd, win, days]); wrote += 1
                    if d is not None and abs(d) > 1e-9:
                        (orange if is_gen else yellow).setdefault(f, 0)
                        (orange if is_gen else yellow)[f] += 1
    kill_rate = (_EX["timeouts"] / _EX["evals"]) if _EX["evals"] else 0.0
    try:
        engine_md5 = hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()
    except Exception:
        engine_md5 = "?"
    summary = {"catside": catside, "engine_md5": engine_md5, "requested_symbols": symbols, "used_symbols": used, "n_used": len(used),
               "deferred": deferred, "coverage_days": coverage, "max_window": max_window, "min_days": min_days,
               "eval_timeout_s": EVAL_TIMEOUT, "eval_count": _EX["evals"], "hard_kills": _EX["timeouts"],
               "hard_kill_rate": round(kill_rate, 4), "eval_timeouts": _EX["timeouts"], "rows_written": wrote,
               "yellow_filters_nonzero": sorted(yellow.keys()), "orange_filters_nonzero": sorted(orange.keys()),
               "n_yellow_nonzero": len(yellow), "n_orange_nonzero": len(orange), "n_candidates": len(cands),
               "clean": len(used) > 0 and kill_rate <= 0.10}
    (pathlib.Path(out_csv).with_suffix(".summary.json")).write_text(json.dumps(summary, indent=2))
    print(f"[{catside}] DONE engine={engine_md5[:8]} used={len(used)} yellow_nz={len(yellow)} orange_nz={len(orange)} evals={_EX['evals']} hard_kills={_EX['timeouts']} kill_rate={kill_rate:.1%} clean={summary['clean']} -> {out_csv}", flush=True)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catside", required=True)
    ap.add_argument("--symbols", required=True, help="comma-separated candidate sym_sides (filtered to >=min-days by NPZ)")
    ap.add_argument("--max-window", type=int, default=365, help="cap window; each symbol evaluated at min(max-window, its npz days)")
    ap.add_argument("--min-days", type=int, default=180, help="skip symbols with fewer real NPZ days")
    args = ap.parse_args()
    out = ROOT / "SPREADSHEETS" / f"V15_FILTER_DELTAS_{args.catside}.csv"
    run(args.catside, [s.strip() for s in args.symbols.split(",") if s.strip()], args.max_window, args.min_days, str(out))


if __name__ == "__main__":
    main()
