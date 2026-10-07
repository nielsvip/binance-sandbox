#!/usr/bin/env python3
"""v15_catside_stack — turn the fleet causal map into ONE cat_side default stack that raises gain on MANY sym_sides at once
(director 2026-10-06). STAGED proposal only: nothing is written to TEMPLATE / config / live books by this tool.

Inputs: data/causal/causal_map_{CAT_SIDE}.csv (tools/v15_causal_map.py) + the per-sym_side repair JSONs it was built from
(tools/v15_repair_driver.py: base_overrides, best_overrides, lever_map).

Method (every number is a real v12_quick_engine evaluation through tools/opt/v12_pilot.evaluate_prepared_sanitized,
the same path tools/v15_repair_driver.py uses; each sym_side prepared ONCE in RAM, fork pool shares it):
  candidates  = robust csv rows (n >= n_symsides/3, mean_dgain > 0, share_pos >= 0.5), forbidden levers dropped,
                ordered by mean_dgain*share_pos, collapsed to one group per switch (best value first, rest = fallbacks)
  greedy      = for each switch group in order, for each value: trial = stack + switch=value applied on top of each
                sym_side's base_overrides; accept iff n_invalid (TIM>80 | DD>30 | trades<10, 30D) does not rise AND
                (median 30D gain rises, or median ties and the count of sym_sides improved vs base rises)
  report      = per sym_side base / stack / own best / stack+best 30D, then 365D check (valid, gain>0, trades>=80)

usage (repo root on S1/S5/S2-cut6):
  nice -n 5 .venv/bin/python tools/v15_catside_stack.py --cat-side CRYPTO_SHORT --raw-dir ~/v15_catside_20261006/input/raw \
      --csv ~/v15_catside_20261006/input/causal_map_CRYPTO_SHORT.csv --out ~/v15_catside_20261006 --workers 6
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import glob
import json
import math
import multiprocessing as mp
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
PREPS = {}
FORBIDDEN_TOKENS = ("SIZING", "SIZE_MULT", "POSITION_SIZE", "ORDER_VALUE")
FORBIDDEN_EXACT = ("SIMPLE_PRICE_GT0_ENABLED", "STOCKS_RTH_ONLY_ENABLED", "MODE")
MIN_TIM = 0.0
EXTRA_FORBIDDEN = set()


def cat_side_of(symside: str) -> str:
    sym, _, side = symside.rpartition("_")
    return ("CRYPTO" if sym.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD")) else "STOCKS") + "_" + side


def forbidden(key: str, val) -> bool:
    k = str(key).upper()
    if k in EXTRA_FORBIDDEN:
        return True
    if any(t in k for t in FORBIDDEN_TOKENS) or k in FORBIDDEN_EXACT:
        return True
    return k.startswith("ABLATION_") and str(val).strip().lower() in ("true", "1", "1.0")


def metrics(r: dict | None) -> dict | None:
    if r is None:
        return None
    m = {"gain": r.get("gain_pct"), "trades": int(r.get("trades") or 0), "tim": r.get("tim_pct"), "dd": r.get("max_dd_pct"), "valid": bool(r.get("valid")), "reason": r.get("invalid_reason") or "", "bh": r.get("bh_pct"), "wr": r.get("wr_pct"), "pool_sharpe": r.get("pool_sharpe")}
    m["gain"] = float(m["gain"]) if m["gain"] is not None else None
    return m


def spec_invalid(m: dict | None) -> bool:
    if m is None or m.get("gain") is None:
        return True
    return float(m.get("tim") or 0) > 80.0 or float(m.get("tim") or 0) < MIN_TIM or float(m.get("dd") or 0) > 30.0 or int(m.get("trades") or 0) < 10


def _ev30(task):
    from tools.opt import v12_pilot as VP
    ss, ov = task
    return metrics(VP.evaluate_prepared_sanitized(PREPS[ss], ov, 30))


def _ev365(task):
    from tools.opt import v12_pilot as VP
    import v15_pilot as P
    ss, sets = task
    t0 = time.time()
    prep = VP.prepare_batch(ss, 365)
    span = P._npz_span_days(ss)
    out = {}
    for lab, ov in sets.items():
        m = metrics(VP.evaluate_prepared_sanitized(prep, ov, 365))
        m["pass_365"] = bool(m["valid"] and m["gain"] is not None and m["gain"] > 0 and m["trades"] >= 80)
        m["qual_365_prorata"] = bool(P._qualifies_365d({"valid": m["valid"], "trades": m["trades"], "gain_pct": m["gain"], "invalid_reason": m["reason"]}, span)[0])
        out[lab] = m
    return ss, {"span_days": span, "sets": out, "secs": round(time.time() - t0, 1)}


def load_raw(raw_dir: str, cat: str, only: set) -> dict:
    runs = {}
    for p in glob.glob(os.path.join(os.path.expanduser(raw_dir), "**", "*.json"), recursive=True):
        try:
            r = json.loads(Path(p).read_text())
        except Exception:
            continue
        ss = (r.get("summary") or {}).get("symside") or Path(p).stem
        if cat_side_of(ss) != cat or not r.get("lever_map") or (only and ss not in only):
            continue
        if ss not in runs or len(r["lever_map"]) >= len(runs[ss]["lever_map"]):
            runs[ss] = {"base": dict(r.get("base_overrides") or {}), "best": dict(r.get("best_overrides") or {}), "lever_map": r["lever_map"], "src": p, "before": r.get("before") or {}, "after": r.get("after") or {}}
    return runs


def load_candidates(csv_path: str, n_ss: int) -> tuple:
    rows, dropped = [], []
    with open(os.path.expanduser(csv_path)) as fh:
        for x in csv.DictReader(fh):
            n, mg, sp = int(x["n"]), float(x["mean_dgain"]), float(x["share_pos"])
            if n < max(3, n_ss // 3) or mg <= 0 or sp < 0.5:
                continue
            sw, _, cand = x["lever"].partition("=")
            if forbidden(sw, cand):
                dropped.append(x["lever"])
                continue
            rows.append({"lever": x["lever"], "switch": sw, "cand": cand, "tab": x["tab"], "row": x["row"], "n": n, "mean_dgain": mg, "share_pos": sp, "score": mg * sp, "mean_dtrades": float(x["mean_dtrades"]), "mean_dtim": float(x["mean_dtim"]), "mean_ddd": float(x["mean_ddd"])})
    rows.sort(key=lambda r: -r["score"])
    groups, order = {}, []
    for r in rows:
        if r["switch"] not in groups:
            groups[r["switch"]] = []
            order.append(r["switch"])
        groups[r["switch"]].append(r)
    return [groups[s] for s in order], dropped


def main():
    global PREPS, MIN_TIM, EXTRA_FORBIDDEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--cat-side", required=True)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--workers-365", type=int, default=3)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--symsides", default="")
    ap.add_argument("--no-365", action="store_true")
    ap.add_argument("--min-tim", type=float, default=0.0, help="optional extra invalid rule TIM < min-tim (CLAUDE.md TIM 20-80 gate)")
    ap.add_argument("--tag", default="")
    ap.add_argument("--exclude", default="", help="comma list of extra forbidden switch names")
    a = ap.parse_args()
    MIN_TIM = a.min_tim
    EXTRA_FORBIDDEN = {x.strip().upper() for x in a.exclude.split(",") if x.strip()}
    import hashlib
    code_md5 = {f: hashlib.md5((ROOT / f).read_bytes()).hexdigest() for f in ("v12_quick_engine.py", "v15_pilot.py", "tools/opt/evaluate_v12.py", "tools/opt/v12_pilot.py") if (ROOT / f).exists()}
    import v15_pilot as P
    from tools.opt import v12_pilot as VP
    cat = a.cat_side.upper()
    out = Path(os.path.expanduser(a.out)) / (cat + (f"_{a.tag}" if a.tag else ""))
    out.mkdir(parents=True, exist_ok=True)
    steps_path = out / "steps.jsonl"
    steps_path.write_text("")
    log = lambda m: print(f"[catside {cat} {time.strftime('%H:%M:%S')}] {m}", flush=True)
    runs = load_raw(a.raw_dir, cat, {s for s in a.symsides.split(",") if s})
    sss = sorted(runs)
    n_ss_map = sum(1 for ss in sss if any(not x.get("blocked") and x.get("valid") is not False and (x.get("d") or {}).get("gain") is not None for x in runs[ss]["lever_map"]))
    groups, dropped = load_candidates(a.csv, n_ss_map)  # same robust rule as tools/v15_causal_map.py: n >= max(3, n_ss//3)
    log(f"{len(sss)} sym_sides ({n_ss_map} with measured levers), {sum(len(g) for g in groups)} robust levers in {len(groups)} switch groups, forbidden dropped: {dropped}")
    npz_mt = {}
    for ss in sss:
        try:
            npz_mt[ss] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(str(P._npz_path(ss)))))
        except Exception:
            npz_mt[ss] = None
    log(f"code md5 {code_md5}")
    defaults, prep_fail = {}, []
    t0 = time.time()
    for ss in sss:
        defaults[ss] = P.get_defaults_for_symside(ss)
        PREPS[ss] = VP.prepare_batch(ss, 30)
        if PREPS[ss] is None:
            prep_fail.append(ss)
    for ss in prep_fail:
        sss.remove(ss)
        PREPS.pop(ss, None)
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6
    except Exception:
        rss = None
    log(f"prepared {len(sss)} sym_sides in {time.time() - t0:.0f}s, parent maxrss {rss} GB, prep failures {prep_fail}")
    san = {ss: (lambda ov, _d=defaults[ss]: P.sanitize_overrides(ov, _d)[0]) for ss in sss}

    def lever_ov(ss, sw, cand):
        try:
            ov = P._switch_overrides(sw, P._parse_opt_value(cand, defaults[ss].get(sw)))
        except Exception:
            return None
        return None if any(forbidden(k, v) for k, v in (ov or {}).items()) else ov

    def stack_ov(ss, stack):
        ov = {}
        for sw, cand in stack:
            ov.update(lever_ov(ss, sw, cand) or {})
        return ov

    pool = cf.ProcessPoolExecutor(max_workers=a.workers, mp_context=mp.get_context("fork"))
    list(pool.map(abs, range(a.workers * 2)))

    def eval_sets(sets: dict) -> dict:
        futs = {ss: pool.submit(_ev30, (ss, ov)) for ss, ov in sets.items()}
        res = {}
        for ss, f in futs.items():
            try:
                res[ss] = f.result(timeout=300)
            except Exception as e:
                res[ss] = None
                log(f"eval error {ss}: {str(e)[:120]}")
        return res

    def summarize(res: dict, base_res: dict | None) -> dict:
        gains = [r["gain"] for r in res.values() if r and r.get("gain") is not None]
        ok = len(gains) == len(sss)
        return {"median": statistics.median(gains) if gains else None, "mean": statistics.mean(gains) if gains else None,
                "n_improved": sum(1 for ss, r in res.items() if base_res and r and r["gain"] is not None and r["gain"] > base_res[ss]["gain"] + 1e-9) if base_res else 0,
                "n_worsened": sum(1 for ss, r in res.items() if base_res and r and r["gain"] is not None and r["gain"] < base_res[ss]["gain"] - 1e-9) if base_res else 0,
                "n_invalid": sum(1 for r in res.values() if spec_invalid(r)), "complete": ok,
                "trades_sum": sum(r["trades"] for r in res.values() if r), "mean_tim": statistics.mean([float(r["tim"] or 0) for r in res.values() if r]) if res else None}

    base_res = eval_sets({ss: san[ss](runs[ss]["base"]) for ss in sss})
    if any(v is None for v in base_res.values()):
        log("base eval failed for some sym_sides -> drop them: " + ",".join(s for s, v in base_res.items() if v is None))
        sss = [s for s in sss if base_res[s] is not None]
        base_res = {s: base_res[s] for s in sss}
    cur = summarize(base_res, base_res)
    log(f"BASE median {cur['median']:.3f} mean {cur['mean']:.3f} n_invalid {cur['n_invalid']}")
    with open(steps_path, "a") as fh:
        fh.write(json.dumps({"step": 0, "lever": "BASE", "accepted": True, **cur, "per_ss": {ss: base_res[ss] for ss in sss}}, default=str) + "\n")
    stack, stack_meta, cur_res = [], [], base_res
    step = 0
    pending = list(groups)
    for pas in range(1, a.passes + 1):
        added, rejected = 0, []
        for g in pending:
            took = False
            for alt_i, lev in enumerate(g):
                trial = stack + [(lev["switch"], lev["cand"])]
                sets, skipped = {}, []
                for ss in sss:
                    if lever_ov(ss, lev["switch"], lev["cand"]) is None:
                        skipped.append(ss)
                    sets[ss] = san[ss]({**runs[ss]["base"], **stack_ov(ss, trial)})
                step += 1
                res = eval_sets(sets)
                s = summarize(res, base_res)
                acc = s["complete"] and s["n_invalid"] <= cur["n_invalid"] and (s["median"] > cur["median"] + 1e-9 or (abs(s["median"] - cur["median"]) <= 1e-9 and s["n_improved"] > cur["n_improved"]))
                rec = {"step": step, "pass": pas, "lever": lev["lever"], "alt_index": alt_i, "tab": lev["tab"], "row": lev["row"], "csv_mean_dgain": lev["mean_dgain"], "csv_share_pos": lev["share_pos"], "accepted": acc,
                       "prev_median": cur["median"], "prev_n_invalid": cur["n_invalid"], **s, "skipped_ss": skipped, "per_ss": {ss: res[ss] for ss in sss}}
                with open(steps_path, "a") as fh:
                    fh.write(json.dumps(rec, default=str) + "\n")
                log(f"step {step} p{pas} {'ACCEPT' if acc else 'reject'} {lev['lever']} median {s['median']:.3f} (was {cur['median']:.3f}) mean {s['mean']:.3f} improved {s['n_improved']}/{len(sss)} invalid {s['n_invalid']} (was {cur['n_invalid']})")
                if acc:
                    stack, cur, cur_res = trial, s, res
                    stack_meta.append({k: lev[k] for k in ("lever", "switch", "cand", "tab", "row", "mean_dgain", "share_pos")} | {"step": step, "pass": pas, "median_after": s["median"], "mean_after": s["mean"], "n_improved": s["n_improved"], "n_invalid": s["n_invalid"]})
                    took, added = True, added + 1
                    break
            if not took:
                rejected.append(g)
        log(f"pass {pas} done: added {added}, stack size {len(stack)}")
        if added == 0:
            break
        pending = [[lv for lv in g if lv["switch"] not in {s for s, _ in stack}] for g in rejected]
        pending = [g for g in pending if g]
    variants = {"base": {ss: san[ss](runs[ss]["base"]) for ss in sss},
                "stack": {ss: san[ss]({**runs[ss]["base"], **stack_ov(ss, stack)}) for ss in sss},
                "own_best": {ss: san[ss]({**runs[ss]["base"], **runs[ss]["best"]}) for ss in sss},
                "stack_plus_best": {ss: san[ss]({**runs[ss]["base"], **stack_ov(ss, stack), **runs[ss]["best"]}) for ss in sss},
                "best_plus_stack": {ss: san[ss]({**runs[ss]["base"], **runs[ss]["best"], **stack_ov(ss, stack)}) for ss in sss}}
    v30 = {lab: eval_sets(sets) for lab, sets in variants.items()}
    vsum = {lab: summarize(res, base_res) for lab, res in v30.items()}
    for lab, s in vsum.items():
        log(f"30D {lab}: median {s['median']:.3f} mean {s['mean']:.3f} improved {s['n_improved']} worsened {s['n_worsened']} invalid {s['n_invalid']}")
    pool.shutdown(wait=True, cancel_futures=True)
    PREPS = {}
    v365 = {}
    if not a.no_365:
        log(f"365D check of {len(sss)} sym_sides, {a.workers_365} parallel")
        with cf.ProcessPoolExecutor(max_workers=a.workers_365, mp_context=mp.get_context("fork")) as ex:
            futs = [ex.submit(_ev365, (ss, {lab: variants[lab][ss] for lab in variants})) for ss in sss]
            for f in futs:
                try:
                    ss, r = f.result(timeout=3600)
                    v365[ss] = r
                    log(f"365D {ss}: " + " | ".join(f"{lab} {m['gain']:.2f}/{m['trades']}tr {'PASS' if m['pass_365'] else 'fail'}" for lab, m in r["sets"].items()))
                except Exception as e:
                    log(f"365D error: {str(e)[:200]}")
    per_ss = {ss: {"src": runs[ss]["src"], "repair_json_before_gain": runs[ss]["before"].get("gain"), "repair_json_after_gain": runs[ss]["after"].get("gain"),
                   "base_overrides": runs[ss]["base"], "best_overrides": runs[ss]["best"], "stack_overrides": stack_ov(ss, stack),
                   "d30": {lab: v30[lab][ss] for lab in v30}, "d365": v365.get(ss)} for ss in sss}
    pass365 = {lab: sum(1 for ss in sss if v365.get(ss) and v365[ss]["sets"][lab]["pass_365"]) for lab in variants} if v365 else {}
    result = {"cat_side": cat, "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "root": str(ROOT), "symsides": sss,
              "n_candidates": sum(len(g) for g in groups), "n_groups": len(groups), "forbidden_dropped": dropped, "stack": stack_meta,
              "summary_30d": vsum, "pass365": pass365, "per_ss": per_ss, "n_steps": step, "secs": round(time.time() - t0, 1), "code_md5_at_start": code_md5, "npz_mtime_at_start": npz_mt, "min_tim": MIN_TIM, "extra_forbidden": sorted(EXTRA_FORBIDDEN), "status": "STAGED_PROPOSAL_NOT_PROMOTED"}
    (out / "result.json").write_text(json.dumps(result, default=str, indent=1))
    log(f"DONE stack={[m['lever'] for m in stack_meta]} median {vsum['base']['median']:.3f} -> {vsum['stack']['median']:.3f} improved {vsum['stack']['n_improved']}/{len(sss)} 365 pass {pass365}")


if __name__ == "__main__":
    main()
