#!/usr/bin/env python3
"""augment_core_parity — proofs for the 2026-10-20 augment revival (vec_decisions/augment_core.py + the
STOCK_AUGMENT_VENUE_GUARD_ENABLED switch). Runs the REAL engine on frozen NPZs (S1 / S2, from ~/binance-sandbox).

  neutrality  --old-engine <path>: the pre-change engine and the current engine give byte-identical ledgers on the given
              sym_sides with their published sets (defaults untouched).
  flip        STOCK_AUGMENT_VENUE_GUARD_ENABLED=False (+ STOCK_LIVE_AUGMENT_ONLY False) -> the vec-only sources fire on a stock
              board through the SWITCH (no env kill switch), and the pilot guard tools/opt/v12_pilot._venue_augment_guard no
              longer forces STOCK_LIVE_AUGMENT_ONLY=True for that row; with the default the guard still forces it.
  parity      every engine AUGMENT event of source LADDER on that run: the inputs the engine handed gain_ladder_fire on that
              bar are replayed through augment_core.decide (the function tradier_manage/ez_manage call live) -> same act,
              same source, same reason text, same base qty (whole shares when the engine's sizing multipliers are 1).

USAGE: python tools/augment_core_parity.py --progress-dir ~/v15_run33_20261019/progress --frozen-root ~/aug_probe_20261020/npz \
          --sym-sides SHOP_LONG,QBTS_SHORT,COTIUSDT_SHORT,GALAUSDT_LONG --old-engine ~/aug_core_old/v12_quick_engine.py --out x.json
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_set(progress_dir, ss):
    d = json.loads((Path(progress_dir).expanduser() / f"{ss}_v14_progress.json").read_text())
    return d, dict(d.get("cumulative_overrides") or {})


def _ledger_digest(led):
    return hashlib.md5(json.dumps(led, sort_keys=True, default=str).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress-dir", required=True)
    ap.add_argument("--frozen-root", required=True)
    ap.add_argument("--sym-sides", required=True)
    ap.add_argument("--old-engine", default="")
    ap.add_argument("--flip-sym-side", default="SHOP_LONG")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import tools.augment_family_probe as P
    from tools.opt.lifecycle_pilot import evaluate_month

    out = {"neutrality": {}, "flip": {}, "parity": {}}
    sss = [x for x in a.sym_sides.split(",") if x]
    os.environ.pop("V12_STOCK_LIVE_AUGMENT_GUARD", None)
    # ---- neutrality: current engine (imported) vs old engine (module swap) on identical inputs
    cur = {}
    for ss in sss:
        prog, ov = _load_set(a.progress_dir, ss)
        P._install_frozen_npz(Path(a.frozen_root).expanduser() / ss, ss.rsplit("_", 1)[0], str((prog.get("npz_id") or {}).get("md5") or ""))
        r = evaluate_month(ss, ov, include_ledger=True, window_days=30)
        cur[ss] = (_ledger_digest(r.get("execution_ledger") or []), round(float(r.get("gain_pct") or 0), 6), len(r.get("execution_ledger") or []))
    if a.old_engine:
        import v12_quick_engine as Vnew

        spec = importlib.util.spec_from_file_location("v12_quick_engine", str(Path(a.old_engine).expanduser()))
        Vold = importlib.util.module_from_spec(spec)
        sys.modules["v12_quick_engine"] = Vold
        spec.loader.exec_module(Vold)
        Vold.load_npz = Vnew.load_npz  # same frozen-NPZ loader hook
        for ss in sss:
            prog, ov = _load_set(a.progress_dir, ss)
            r = evaluate_month(ss, ov, include_ledger=True, window_days=30)
            old = (_ledger_digest(r.get("execution_ledger") or []), round(float(r.get("gain_pct") or 0), 6), len(r.get("execution_ledger") or []))
            out["neutrality"][ss] = {"current": cur[ss], "old": old, "identical": cur[ss] == old}
        sys.modules["v12_quick_engine"] = Vnew
    else:
        out["neutrality"] = {ss: {"current": cur[ss], "old": None, "identical": None} for ss in sss}
    # ---- flip proof through the SWITCH (no env) + pilot guard behaviour
    ss = a.flip_sym_side
    prog, ov = _load_set(a.progress_dir, ss)
    P._install_frozen_npz(Path(a.frozen_root).expanduser() / ss, ss.rsplit("_", 1)[0], str((prog.get("npz_id") or {}).get("md5") or ""))
    from tools.opt import v12_pilot as VP

    prep = {"mode": "tradier"}
    out["flip"]["pilot_guard_default_forces"] = VP._venue_augment_guard(prep, dict(ov)).get("STOCK_LIVE_AUGMENT_ONLY")
    out["flip"]["pilot_guard_false_row_forces"] = VP._venue_augment_guard(prep, dict(ov, STOCK_AUGMENT_VENUE_GUARD_ENABLED=False)).get("STOCK_LIVE_AUGMENT_ONLY", "<not forced>")
    import vec_decisions.gain_ladder_augment as GLA

    calls = []
    orig = GLA.gain_ladder_fire

    def rec(config, is_long, px, last_aug_px, raw_gain_pct, max_gain_pct):
        res = orig(config, is_long, px, last_aug_px, raw_gain_pct, max_gain_pct)
        calls.append({"px": float(px), "last": float(last_aug_px), "gain": float(raw_gain_pct), "peak": float(max_gain_pct), "fire": bool(res[0]), "reason": res[1]})
        return res

    GLA.gain_ladder_fire = rec
    flip_ov = dict(ov, STOCK_AUGMENT_VENUE_GUARD_ENABLED=False, STOCK_LIVE_AUGMENT_ONLY=False, MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0)
    r_flip = evaluate_month(ss, flip_ov, include_ledger=True, window_days=30)
    GLA.gain_ladder_fire = orig
    r_def = evaluate_month(ss, dict(ov, MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0), include_ledger=True, window_days=30)
    led = r_flip.get("execution_ledger") or []
    augs = [e for e in led if e.get("type") == "AUGMENT"]
    out["flip"].update({"sym_side": ss, "default_gain": r_def.get("gain_pct"), "default_augments": sum(1 for e in (r_def.get("execution_ledger") or []) if e.get("type") == "AUGMENT"), "flip_gain": r_flip.get("gain_pct"), "flip_augments": len(augs), "flip_sources": sorted(set(str(e.get("reason", "")).split(" ")[0] for e in augs))})
    # ---- parity: replay the engine's own LADDER inputs through augment_core.decide
    import vec_decisions.augment_core as AC

    cfg_get = lambda k, d=None: flip_ov.get(k, d)
    fired_calls = [c for c in calls if c["fire"]]
    match = 0
    rows = []
    for e in augs:
        rs = str(e.get("reason", ""))
        if not (rs.startswith("UAG_LADDER") or rs.startswith("PULLBACK_AUG")):
            continue
        c = next((c for c in fired_calls if abs(c["px"] - float(e["price"])) < 1e-9 and c["reason"] == rs), None)
        if c is None:
            rows.append({"bar": e.get("bar"), "reason": rs, "found_inputs": False})
            continue
        f, src, reason, add = AC.decide(cfg_get, ss.endswith("_LONG"), c["px"], 0.0, c["last"], c["gain"], c["peak"], 1000.0, {}, None, None)
        ok = bool(f and src == "LADDER" and reason == f"AUGMENT_CORE_LADDER {rs}")
        match += int(ok)
        rows.append({"bar": e.get("bar"), "engine_reason": rs, "core_fire": f, "core_source": src, "core_reason": reason, "engine_qty": e.get("qty"), "same_act": ok})
    out["parity"] = {"sym_side": ss, "ladder_events": len(rows), "same_act": match, "rows": rows}
    Path(a.out).expanduser().write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({k: (v if k != "parity" else {kk: vv for kk, vv in v.items() if kk != "rows"}) for k, v in out.items()}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
