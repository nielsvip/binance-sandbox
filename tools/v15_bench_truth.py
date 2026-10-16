#!/usr/bin/env python3
"""V15 BENCH TRUTH SCORER (USER 2026-10-16: "make sure the numbers in gain and technical are never exaggerated and match
exactly what is shown in the html charts").

Scores override sets the way the HTML chart proves them (hires_chart / check_board_numbers ledger_truth): every metric from the
engine's FULL execution ledger, the window-edge mark (FINAL_MTM...) counted like the engine/chart do and reported separately, capital base = peak concurrent exposure
replayed over trades AND augment/reduce events. One common code root scores both gate arms on the same frozen NPZ, so a version
can never look better by measuring itself differently.

  python3 v15_bench_truth.py --root RUN_ROOT --side SS --fit-days 20 --holdout-days 7 --set final=PROGRESS.json[:key] ...

Prints one JSON object: {set_name: {"fit": metrics, "oos": metrics}}. Fit = window fit-days on the NPZ truncated at
(last bar - holdout-days) (the pilot's own holdout mechanism, v15_pilot._install_holdout); OOS = the last holdout-days.
"""
import argparse
import json
import os
import sys
from pathlib import Path

MTM_PREFIXES = ("FINAL_MTM", "WINDOW_END_MTM", "MTM_FINAL", "OPEN_AT_WINDOW_END")


def is_mark(row):
    if not isinstance(row, dict):
        return False
    if row.get("unrealised") is True or row.get("is_mtm") is True:
        return True
    return str(row.get("exit_reason") or "").upper().startswith(MTM_PREFIXES)


def ledger_metrics(led, bars, E, pool_sharpe_from_ledger):
    # USER 2026-10-16: the window-edge mark COUNTS (an open loser must never vanish from a headline); it is also reported.
    keep = os.environ.get("METRICS_COUNT_UNREALISED_MTM", "1") == "1"
    countable = [t for t in led if isinstance(t, dict) and not t.get("seeded_prewindow") and (keep or not is_mark(t))]
    closes = [t for t in countable if t.get("pnl_dollars") is not None]
    marks = [t for t in led if isinstance(t, dict) and t.get("pnl_dollars") is not None and is_mark(t)]
    n = len(closes)
    wins = sum(1 for t in closes if float(t.get("pnl_dollars") or 0.0) > 0)
    held = set()
    for t in closes + ([] if keep else marks):
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        if be is not None and bx is not None:
            held.update(range(int(be), int(bx)))
    r = {"ledger": countable, "mean_deployed": None}
    return {
        "gain_pct": round(float(E._honest_gain_pct(r)), 4),
        "trades": n,
        "wr_pct": round(100.0 * wins / n, 2) if n else 0.0,
        "max_dd_pct": round(float(E._honest_max_dd_pct(r)), 4),
        "pool_sharpe": round(float(pool_sharpe_from_ledger(closes)), 4) if n >= 2 else 0.0,
        "tim_pct": round(100.0 * len(held) / bars, 2) if bars else None,
        "unrealised_marks": len(marks),
        "unrealised_usd": round(sum(float(t.get("pnl_dollars") or 0.0) for t in marks), 2),
    }


def load_set(spec):
    path, _, key = spec.partition(":")
    d = json.loads(Path(path).read_text())
    for k in ([key] if key else ["cumulative_overrides", "final_overrides"]):
        if isinstance(d.get(k), dict):
            return dict(d[k])
    return None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--fit-days", type=int, required=True)
    ap.add_argument("--holdout-days", type=int, default=7)
    ap.add_argument("--set", action="append", required=True, help="NAME=PROGRESS_JSON[:key]")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["V12_ALLOW_STALE_NPZ"] = "1"  # frozen snapshot by design
    import numpy as np
    from tools.opt import evaluate_v12 as E
    from tools.opt.v12_pilot import evaluate_prepared_sanitized as eps
    from metrics_guard import pool_sharpe_from_ledger
    sets = {}
    for spec in a.set:
        name, _, src = spec.partition("=")
        try:
            sets[name] = load_set(src)
        except Exception as e:
            sets[name] = None
            print(f"[truth] {name}: {e}", file=sys.stderr)
    orig = E._store
    prep0 = E.prepare(a.side, a.holdout_days)
    if prep0 is None:
        print(json.dumps({"error": "prepare None (no NPZ)"}))
        return 2
    ts_all = np.asarray(prep0["npz_prepared"]["timestamps"], dtype="float64")
    last = float(ts_all[-1]) / (1000.0 if ts_all[-1] > 1e11 else 1.0)
    cut = last - a.holdout_days * 86400.0

    def store_cut(sym, mode, start):
        d = orig(sym, mode, start)
        if d is None:
            return d
        ts = np.asarray(d["timestamps"], dtype="float64")
        sec = ts / 1000.0 if np.nanmax(ts) > 1e11 else ts
        right = int(np.searchsorted(sec, cut + 1e-6, side="right"))
        n = len(ts)
        return {k: (v[:right] if isinstance(v, np.ndarray) and v.ndim and len(v) == n else v) for k, v in d.items()}

    out = {"side": a.side, "cut": cut, "fit_days": a.fit_days, "holdout_days": a.holdout_days, "scorer_root": str(root)}
    for phase, days, store in (("oos", a.holdout_days, orig), ("fit", a.fit_days, store_cut)):
        E._store = store
        try:
            E._STORES.clear()
        except Exception:
            pass
        prep = E.prepare(a.side, days)
        if prep is None:
            out[f"{phase}_error"] = "prepare None"
            continue
        bars = int(len(prep["npz_prepared"]["timestamps"]))
        for name, ov in sets.items():
            if ov is None:
                continue
            r = eps(prep, dict(ov), days, include_ledger=True) or {}
            led = r.get("execution_ledger") or r.get("ledger") or []
            m = ledger_metrics(led, bars, E, pool_sharpe_from_ledger)
            m["engine_gain_pct"] = r.get("gain_pct")  # what the engine itself reports, kept beside the truth for audit
            m["valid"] = r.get("valid")
            out.setdefault(name, {})[phase] = m
    E._store = orig
    print(json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
