#!/usr/bin/env python3
"""Run the trade autopsy (tools/v15_trade_autopsy.py) on real engine ledgers for one or more sym_sides.

  .venv/bin/python tools/v15_trade_autopsy_run.py --symsides AXSUSDT_LONG,NVDA_LONG --out ~/v15_autopsy_20261006 --workers 8
  (base set = --set-json, else the repair-driver result ~/v15_repair_20261006/{SS}.json best_overrides, else latest best)

Per sym_side writes {out}/{SS}_autopsy.json and {out}/{SS}_autopsy.md:
  every trade with its labels (LOSER / PREMATURE_EXIT / GIVEBACK / MISSED_AUGMENT), the switches that fix it (kind + $),
  missed moves while flat and which switches capture them, a scorecard per switch, the surgical combination VERIFIED by
  real cumulative evals (kept only where the engine confirms a gain), and missing-function rules for what nothing fixes.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
PREP = None


def _w(ov):
    from tools.opt import v12_pilot as VP
    from tools import v15_trade_autopsy as TA
    r = VP.evaluate_prepared_sanitized(PREP, ov, 30, True)
    rows = TA.scaled_rows(r)  # gain-pp units of THIS run
    g = r.get("gain_pct")
    return {"gain": g, "trades": r.get("trades"), "valid": r.get("valid"), "tim": r.get("tim_pct"), "dd": r.get("max_dd_pct"), "rows": rows}


def base_set(ss: str, set_json: str | None, dirs: list) -> tuple:
    if set_json:
        d = json.load(open(os.path.expanduser(set_json)))
        return d.get("best_overrides") or d.get("cumulative_overrides") or d, set_json
    rp = Path(os.path.expanduser(f"~/v15_repair_20261006/{ss}.json"))
    if rp.exists():
        d = json.load(open(rp))
        if d.get("best_overrides") is not None:
            return d["best_overrides"], str(rp)
    from tools.v15_repair_driver import latest_best
    ov, src, _ = latest_best(ss, dirs)
    return ov, src


def run_one(ss: str, out: Path, workers: int, set_json: str | None, dirs: list) -> dict:
    global PREP
    import v15_pilot as P
    from tools import v15_trade_autopsy as TA
    from tools.opt import v12_pilot as VP
    from tools.v15_repair_driver import candidates_for
    t0 = time.time()
    defaults = P.get_defaults_for_symside(ss)
    san = lambda ov: P.sanitize_overrides(ov, defaults)[0]
    is_long = ss.endswith("_LONG")
    PREP = VP.prepare_batch(ss, 30)
    npz = PREP.get("npz_prepared") or {}
    close = [float(x) for x in npz.get("close")]
    n = len(close)
    ov0, src = base_set(ss, set_json, dirs)
    base_ov = san(ov0)
    pool = cf.ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("fork"))
    list(pool.map(abs, range(workers * 2)))
    try:
        b = _w(base_ov)
        base = TA.classify(b["rows"], close, is_long)
        # alignment sanity: engine entry prices must sit on the slice's close path (else bars index another array)
        mis = sum(1 for t in base if 0 <= t["be"] < n and t["entry_price"] and abs(close[t["be"]] - t["entry_price"]) / t["entry_price"] > 0.02)
        moves = TA.missed_moves(base, close, is_long)
        # sizing multipliers scale $ and peak together — notional, not edge (v15_pilot SIZING_FALSE_ALPHA) → excluded
        cands = [c for c in candidates_for(ss, defaults, P) if not c.get("blocked") and not any(t in c["switch"].upper() for t in ("SIZE_MULT", "SIZING", "POSITION_SIZE", "_SIZE"))]
        items = []
        for c in cands:
            v = dict(base_ov)
            v.update(c["ov"])
            v = san(v)
            if v == base_ov:
                continue
            items.append((f"{c['switch']}={c['cand']}", v, c))
        futs = {pool.submit(_w, v): (lab, c) for lab, v, c in items}
        attrs, cards, meta = {}, {}, {}
        for f in cf.as_completed(futs):
            lab, c = futs[f]
            try:
                r = f.result(timeout=120)
            except Exception:
                continue
            a = TA.attribute(base, r["rows"], moves)
            if not a["eff"] and not a["added"]:
                continue
            attrs[lab] = a
            cards[lab] = {**TA.scorecard(base, a), "gain": r["gain"], "d_gain": (r["gain"] or 0) - (b["gain"] or 0), "valid": r["valid"], "trades": r["trades"], "tab": c["tab"], "ov": c["ov"]}
            meta[lab] = c
        # a fix counts only from a switch that does not reduce the run's real gain (non-destructive); ranked by real Δgain
        clean = {lab: a for lab, a in attrs.items() if cards[lab]["valid"] and cards[lab]["d_gain"] >= -1e-9}
        fixes = TA.fixes_per_trade(base, clean)
        for be in fixes:
            fixes[be] = sorted(fixes[be], key=lambda x: -cards[x[0]]["d_gain"])
        # surgical combination, verified: apply picks cumulatively, keep only engine-confirmed gains
        picks = TA.surgical_combo(cards, {k: v for k, v in attrs.items() if cards[k]["valid"]}, max_k=10)
        cur_ov, cur = dict(base_ov), b
        verified = []
        for lab in picks:
            v = dict(cur_ov)
            v.update(meta[lab]["ov"])
            r = _w(san(v))
            ok = r["valid"] and (r["gain"] or -1e9) > (cur["gain"] or -1e9) + 1e-6
            verified.append({"switch": lab, "gain_before": cur["gain"], "gain_after": r["gain"], "trades": r["trades"], "valid": r["valid"], "kept": bool(ok)})
            if ok:
                cur_ov, cur = san(v), r
        unfixed_losers = {t["be"] for t in base if "LOSER" in t["cls"] and t["be"] not in fixes}
        unfixed_premature = {t["be"] for t in base if "PREMATURE_EXIT" in t["cls"] and not any(k == "EXIT_LATER" for _l, k, _v in fixes.get(t["be"], []))}
        miss_entry = TA.missing_functions(base, unfixed_losers, npz, n, "entry")
        miss_exit = TA.missing_functions(base, unfixed_premature, npz, n, "exit")
        fam_fix = {TA.family_of(lab) for lab in attrs}
        for r_ in miss_entry + miss_exit:
            r_["family"] = TA.family_of(r_["feature"])
            r_["status"] = "MISSING" if not any(r_["family"] in lab.upper() for lab in attrs) else "FAMILY_EXISTS_NO_FIX"
        nbad = {k: sum(1 for t in base if k in t["cls"]) for k in ("LOSER", "PREMATURE_EXIT", "GIVEBACK", "MISSED_AUGMENT")}
        rep = {"symside": ss, "set_src": src, "base": {k: b[k] for k in ("gain", "trades", "valid", "tim", "dd")}, "n_trades": len(base), "bad_counts": nbad,
               "fixed_counts": {k: sum(1 for t in base if k in t["cls"] and t["be"] in fixes) for k in nbad}, "units": "gain_pp", "missed_moves": moves,
               "align_mismatch": mis, "n_candidates": len(items), "n_effective": len(attrs),
               "trades": [{**{k: t[k] for k in ("be", "bx", "pnl", "pnl_pct", "entry_reason", "exit_reason", "n_aug", "mfe_pct", "post_pct", "cls")}, "fixes": fixes.get(t["be"], [])} for t in base],
               "top_switches": sorted(({"switch": k, **{kk: vv for kk, vv in v.items() if kk != "ov"}} for k, v in cards.items()), key=lambda x: -x["net"])[:40],
               "captures": sorted(({"switch": k, "captured": v["captured"], "added_pnl": v["added_pnl"]} for k, v in cards.items() if v["captured"]), key=lambda x: -x["added_pnl"])[:15],
               "combo_verified": verified, "combo_gain": cur["gain"], "combo_overrides": {k: v for k, v in cur_ov.items() if base_ov.get(k) != v},
               "missing_entry_filters": miss_entry, "missing_exit_confirmations": miss_exit, "secs": round(time.time() - t0, 1)}
        (out / f"{ss}_autopsy.json").write_text(json.dumps(rep, default=str, indent=1))
        (out / f"{ss}_autopsy.md").write_text(render_md(rep))
        print(f"[autopsy] {ss} trades={len(base)} bad={nbad} fixed={rep['fixed_counts']} effective_switches={len(attrs)}/{len(items)} base {b['gain']} -> combo {cur['gain']} align_mismatch={mis} ({rep['secs']}s)", flush=True)
        return rep
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def render_md(r: dict) -> str:
    L = [f"# Trade autopsy {r['symside']}", "", f"base gain {r['base']['gain']} | trades {r['n_trades']} | TIM {r['base']['tim']} | DD {r['base']['dd']} | set {r['set_src']}",
         f"bad trades {r['bad_counts']} | fixable by an existing switch {r['fixed_counts']} | effective switches {r['n_effective']}/{r['n_candidates']}", "",
         "## Surgical combination (engine-verified)", "", "| switch | gain before | gain after | trades | kept |", "|---|---|---|---|---|"]
    for v in r["combo_verified"]:
        L.append(f"| {v['switch']} | {v['gain_before']} | {v['gain_after']} | {v['trades']} | {v['kept']} |")
    L += ["", f"combo gain: **{r['combo_gain']}** (base {r['base']['gain']})", "", "## Bad trades and their fixes", "", "| entry bar | exit bar | contrib pp | trade pnl% | labels | entry → exit reason | MFE% | after-exit% | best clean fixes (kind, pp) |", "|---|---|---|---|---|---|---|---|---|"]
    for t in r["trades"]:
        if not t["cls"]:
            continue
        fx = "; ".join(f"{l} ({k} {v:+.2f})" for l, k, v in t["fixes"][:3]) or "**none — see missing functions**"
        L.append(f"| {t['be']} | {t['bx']} | {t['pnl']:+.2f} | {t['pnl_pct']:+.2f} | {','.join(t['cls'])} | {t['entry_reason'][:28]} → {t['exit_reason'][:28]} | {t['mfe_pct']} | {t['post_pct']} | {fx} |")
    L += ["", "## Switch scorecard (top by net $)", "", "| switch | tab | losers fixed | saved pp | winners hurt | hurt pp | premature fixed | aug fixed | captured moves | net pp (attrib) | real Δgain | valid |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in r["top_switches"][:25]:
        L.append(f"| {s['switch']} | {s['tab']} | {s['losers_fixed']} | {s['saved']:.2f} | {s['winners_hurt']} | {s['hurt']:.2f} | {s['premature_fixed']} | {s['aug_fixed']} | {s['captured']} | {s['net']:.2f} | {s['d_gain']:+.3f} | {s['valid']} |")
    L += ["", "## Missing functions (nothing existing fixes these trades)", ""]
    for rr in r["missing_entry_filters"] + r["missing_exit_confirmations"]:
        L.append(f"- [{rr['status']}] {rr['rule']}")
    L += ["", f"missed moves while flat: {len(r['missed_moves'])}; captured by: " + ", ".join(f"{c['switch']} ({c['captured']}, {c['added_pnl']:+.2f}$)" for c in r["captures"][:6])]
    return "\n".join(L)


def _child(args):
    ss, out, workers, set_json, dirs = args
    try:
        return run_one(ss, Path(out), workers, set_json, dirs)
    except Exception as e:
        import traceback
        print(f"[autopsy] {ss} ERROR {e}\n{traceback.format_exc()[-1200:]}", flush=True)
        return {"symside": ss, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symsides", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--set-json")
    ap.add_argument("--progress-dirs", default="data/reports/lifecycle_pilot,~/repair_in/s1progress,~/v15_run*/progress")
    a = ap.parse_args()
    dirs = []
    for d in a.progress_dirs.split(","):
        dirs += glob.glob(os.path.expanduser(d.strip())) or [os.path.expanduser(d.strip())]
    out = Path(os.path.expanduser(a.out))
    out.mkdir(parents=True, exist_ok=True)
    sss = [s for s in a.symsides.split(",") if s]
    with cf.ProcessPoolExecutor(max_workers=a.parallel, mp_context=mp.get_context("spawn")) as ex:
        list(ex.map(_child, [(s, str(out), a.workers, a.set_json, dirs) for s in sss]))


if __name__ == "__main__":
    main()
