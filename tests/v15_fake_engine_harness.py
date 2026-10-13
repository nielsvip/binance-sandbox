"""Fake-engine harness for the REAL v15_pilot._spec_fill_workbook (no NPZ needed) — used by tests/test_v15_scout.py and
by hand (`python tests/v15_fake_engine_harness.py [--scout 0|1] [--route 0|1]`) to measure eval counts per stage.

The fake engine (injected as tools.opt.v12_pilot before the pilot forks its pool) is deterministic: only a handful of
template keys move the ledger; STRENGTH_FILTER_ENABLED=True (config default) masks the RZ_BREAKOUT_ENTRY_ENABLED entry path
(the 'filters disabled the path' case the ROUTE round must find); MOM3_FILTER_TF=1h is a good filter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVALS = {"n": 0}
LOG = Path(os.environ.get("V15_FAKE_EVAL_LOG", "/dev/null"))


def _v(ov, k, d=None):
    return ov.get(k, d)


def _is(v, *opts):
    return str(v).strip().lower() in {str(o).lower() for o in opts}


def fake_eval(prepared, ov, window_days=30, ledger=False):
    """defaults mirror config (absent key = config default): STRENGTH_FILTER_ENABLED True (masks RZ_BREAKOUT_ENTRY),
    MOM3_FILTER_TF OFF, WT_DIV_ENTRY_GATE_ENABLED True, EXIT_VELOCITY_WT_ENABLED True, COOLDOWN_BARS 0."""
    EVALS["n"] += 1
    _lp = os.environ.get("V15_FAKE_EVAL_LOG")
    if _lp:
        with open(_lp, "a") as _fh:  # O_APPEND: safe across the forked pool workers
            _fh.write("1\n")
    g, tr = 2.0, 25
    rel = {}
    strength = not _is(_v(ov, "STRENGTH_FILTER_ENABLED", True), "false", "0")
    if _is(_v(ov, "MOM3_FILTER_TF", "OFF"), "1h"):
        g += 1.5
        rel["m"] = 1
    if _is(_v(ov, "WT_DIV_ENTRY_GATE_ENABLED", True), "false", "0"):
        g += 0.4
        rel["w"] = 1
    if _is(_v(ov, "EXIT_VELOCITY_WT_ENABLED", True), "false", "0"):
        g += 0.6
        rel["e"] = 1
    if not strength:
        g -= 0.8  # stripping the filter alone hurts
        tr += 6
        rel["s"] = 1
        if _is(_v(ov, "RZ_BREAKOUT_ENTRY_ENABLED", False), "true", "1"):
            g += 3.0  # masked path: only binds with the strength filter off
            tr += 9
            rel["rz"] = 1
    try:
        cb = int(float(_v(ov, "COOLDOWN_BARS", 0)))
        g += {3: 0.2, 6: 0.25}.get(cb, 0.0)
        rel["c"] = cb
    except Exception:
        pass
    fp = hashlib.md5(json.dumps(rel, sort_keys=True).encode()).hexdigest()
    time.sleep(0.001)
    res = {"gain_pct": round(g, 6), "trades": tr, "tim_pct": 40.0, "max_dd_pct": 4.0, "wr_pct": 55.0, "valid": True, "invalid_reason": None, "behavior_fingerprint": fp, "bh_pct": 5.0, "pool_sharpe": 0.3}
    if ledger:
        res["ledger"] = []
    return res


def install_fake():
    m = types.ModuleType("tools.opt.v12_pilot")
    m.evaluate_prepared_sanitized = fake_eval
    m.evaluate_sanitized = lambda sym, ov, window_days=30: fake_eval(None, ov, window_days)
    m.prepare_batch = lambda sym, days: object()
    pkg = types.ModuleType("tools.opt")
    pkg.__path__ = [str(ROOT / "tools" / "opt")]
    pkg.v12_pilot = m
    sys.modules["tools.opt"] = pkg
    sys.modules["tools.opt.v12_pilot"] = m


def run(scout: str = "1", route: str = "1", workers: int = 4, symside: str = "TESTUSDT_SHORT", kdir: str | None = None, template: str = "TEMPLATE_CRYPTO_SHORT.xlsx") -> dict:
    os.environ.update({"V15_SCOUT": scout, "V15_ROUTE": route, "V15_DIAG_REPAIR": "0", "V15_SKIP_LIVE_AT_DONE": "1", "V15_SKIP_365D_AT_DONE": "1", "V15_FILL_SLICE_S": "0", "V15_SACRED_REPASS": "1", "V15_ROUTE_S_LOSER": "120", "V15_ROUTE_S_MEDIOCRE": "120", "V15_ROUTE_S_WINNER": "120", "V15_ROUTE_S_BHCHASE": "120"})
    tmp = Path(tempfile.mkdtemp(prefix="v15fake_"))
    os.environ["V15_PROGRESS_DIR"] = str(tmp)
    os.environ["V15_KNOWLEDGE_DIR"] = kdir or str(tmp / "know")
    os.environ["V15_FAKE_EVAL_LOG"] = str(tmp / "engine_calls.log")
    install_fake()
    import importlib
    import v15_pilot as P
    importlib.reload(P)
    tmpl = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / template
    wbp = tmp / f"{symside}_30d_matrix.xlsx"
    shutil.copy(tmpl, wbp)
    defaults = P.get_defaults_for_symside(symside)
    base = fake_eval(None, {})
    EVALS["n"] = 0
    try:
        (tmp / "engine_calls.log").unlink()
    except Exception:
        pass
    progress = {"done": {}, "baseline_gain": base["gain_pct"]}
    args = argparse.Namespace(workers=workers, window_days=30, seq_mode="worst2best", nav_mode="jump", fast_switches=None)
    prepared = object()
    t0 = time.time()
    try:
        out = P._spec_fill_workbook(symside, wbp, progress, tmp / f"{symside}_v14_progress.json", tmp / "flags.md", base["gain_pct"], {}, defaults, base["gain_pct"], 5.0, prepared, args, base, {})
    except SystemExit as e:
        out = (None, None, progress)
        print("SystemExit", e)
    secs = time.time() - t0
    prog = out[2] if out and out[2] is not None else progress
    # count evals from the delta log (forked workers increment their own counters)
    dl = list((tmp / "v15_delta_log").glob("*.jsonl"))
    lab = {}
    n_real = 0
    for p in dl:
        for ln in open(p):
            r = json.loads(ln)
            if r.get("label") == "ROW_DONE":
                continue
            if r.get("cached"):
                continue
            n_real += 1
            k = str(r.get("label")).split(":")[0]
            k = "yellow" if "=" in k else k
            lab[k] = lab.get(k, 0) + 1
    try:
        n_engine = sum(1 for _ in open(tmp / "engine_calls.log"))
    except Exception:
        n_engine = None
    labs_all = {}
    for p in dl:
        for ln in open(p):
            r = json.loads(ln)
            if r.get("label") == "ROW_DONE":
                continue
            k = str(r.get("label")).split(":")[0]
            k = "yellow" if "=" in k else k
            labs_all[k] = labs_all.get(k, 0) + 1
    return {"secs": round(secs, 1), "engine_calls": n_engine, "evals_logged_all": dict(sorted(labs_all.items(), key=lambda t: -t[1])[:20]), "evals": n_real, "by_label": dict(sorted(lab.items(), key=lambda t: -t[1])[:20]), "final_gain": prog.get("cumulative_gain"), "route": {k: (prog.get("route") or {}).get(k) for k in ("steps", "lift", "final_gain", "skipped", "error")}, "scout": prog.get("scout"), "overrides": {k: v for k, v in (prog.get("cumulative_overrides") or {}).items() if k in ("STRENGTH_FILTER_ENABLED", "RZ_BREAKOUT_ENTRY_ENABLED", "MOM3_FILTER_TF", "EXIT_VELOCITY_WT_ENABLED", "COOLDOWN_BARS", "WT_DIV_ENTRY_GATE_ENABLED")}, "tmp": str(tmp)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scout", default="1")
    ap.add_argument("--route", default="1")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    print(json.dumps(run(a.scout, a.route, a.workers), indent=1, default=str))
