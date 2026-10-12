#!/usr/bin/env python3
"""Engine-verified causal FLIP STUDY (USER 2026-10-12 "deep dive into each useful switch and its causal effect on tim dd gain wr").

The fleet delta logs (tools/v15_switch_causal_profile.py) carry gain / TIM / trades per eval but no DD or WR. This study
takes the switches the profile flags (USEFUL / SOMETIMES / HARMFUL / strong TIM levers) and re-measures each flip on real
sym_side bases with the real engine (tools.opt.v12_pilot.evaluate_prepared_sanitized over tools.opt.evaluate_v12.prepare):
base set vs base+flip -> d_gain, d_tim, d_dd, d_wr, d_trades, validity. Nothing is estimated or reimplemented.

Runs on a fleet host that holds NPZ + autopsy bases (s5/s6 idle). Output:
  data/causal/switch_flip_study.json / .md
"""
import argparse
import collections
import concurrent.futures as cf
import glob
import json
import multiprocessing as mp
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DENY = {"MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED"}
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
METRICS = ("gain_pct", "tim_pct", "max_dd_pct", "wr_pct", "engine_wr_RAW", "trades", "valid", "invalid_reason", "bh_pct")


def cat_side_of(ss):
    sym, _, side = ss.rpartition("_")
    return f"{'CRYPTO' if sym.upper().endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}"


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).strip().lower() == str(b).strip().lower()
    try:
        return abs(float(a) - float(b)) < 1e-12
    except Exception:
        return str(a).strip() == str(b).strip()


def _m(r):
    return {k: (r or {}).get(k) for k in METRICS}


def _w_symside(task):
    """fork worker: one sym_side, prepare once, eval base + every flip. Module-level (picklable)."""
    ss, base_ov, cands = task
    try:
        import dataclasses
        import v12_quick_engine as VQ
        import v15_pilot as P
        from tools.opt import evaluate_v12 as E
        from tools.opt import v12_pilot as VP
        qd = {f.name: f.default for f in dataclasses.fields(VQ.QuickConfig)}
        prep = E.prepare(ss, 30)
        if prep is None:
            return ss, None, "prepare failed (no npz)"
        base_ov = {k: v for k, v in dict(base_ov).items() if k not in DENY and not str(k).startswith("ABLATION_")}
        base_ov = P.sanitize_overrides(base_ov, qd)[0]
        b = VP.evaluate_prepared_sanitized(prep, base_ov, 30)
        out = {"base": _m(b), "flips": {}}
        for key, sw, val in cands:
            try:
                pv = P._parse_opt_value(val, qd.get(sw))
                sw_ov = P._switch_overrides(sw, pv)
            except Exception as e:
                out["flips"][key] = {"err": str(e)[:80]}
                continue
            if all(_same(base_ov.get(k, qd.get(k)), v) for k, v in sw_ov.items()):
                out["flips"][key] = {"identity": True}
                continue
            ov = dict(base_ov)
            ov.update(sw_ov)
            ov = P.sanitize_overrides(ov, qd)[0]
            t0 = time.time()
            r = VP.evaluate_prepared_sanitized(prep, ov, 30)
            rec = _m(r)
            rec["secs"] = round(time.time() - t0, 3)
            out["flips"][key] = rec
        return ss, out, ""
    except Exception as e:
        return ss, None, f"{type(e).__name__}: {e}"[:160]


def pick_candidates(profile_cs, n_useful, n_harm, n_tim):
    sw = profile_cs.get("switch_cand") or {}
    useful = sorted([(k, v) for k, v in sw.items() if v.get("verdict") in ("USEFUL", "SOMETIMES")], key=lambda kv: -(kv[1].get("sum_pos_delta") or 0))[:n_useful]
    harm = sorted([(k, v) for k, v in sw.items() if v.get("verdict") == "HARMFUL"], key=lambda kv: (kv[1].get("sum_neg_delta") or 0))[:n_harm]
    tim = sorted([(k, v) for k, v in sw.items() if (v.get("n_sym") or 0) >= 10 and v.get("mean_d_tim") is not None], key=lambda kv: -abs(kv[1].get("mean_d_tim") or 0))[:n_tim]
    keys = list(dict.fromkeys([k for k, _ in useful] + [k for k, _ in harm] + [k for k, _ in tim]))
    out = []
    for k in keys:
        s, _, v = k.partition("=")
        if s in DENY or s.startswith("ABLATION_"):
            continue
        out.append((k, s, v))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.path.join(ROOT, "data", "causal", "switch_causal_profile.json"))
    ap.add_argument("--bases", default=os.path.expanduser("~/v15_autopsy_first"))
    ap.add_argument("--per-cat", type=int, default=30)
    ap.add_argument("--n-useful", type=int, default=40)
    ap.add_argument("--n-harm", type=int, default=15)
    ap.add_argument("--n-tim", type=int, default=10)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--cats", default="CRYPTO_LONG,CRYPTO_SHORT,STOCKS_LONG,STOCKS_SHORT")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "causal", "switch_flip_study.json"))
    ap.add_argument("--symsides", default="", help="comma list to force (debug)")
    args = ap.parse_args()
    prof = json.load(open(args.profile))
    cats = [c for c in args.cats.split(",") if c]
    bases = {}
    for p in sorted(glob.glob(os.path.join(args.bases, "*_autopsy_base.json")), key=os.path.getmtime, reverse=True):
        ss = os.path.basename(p)[: -len("_autopsy_base.json")]
        try:
            d = json.load(open(p))
            ov = ((d.get("final") or {}).get("overrides")) or {}
            a = d.get("autopsy") or {}
            if not ov or int(a.get("combo_trades") or a.get("base_trades") or 0) < 10:
                continue
            bases[ss] = ov
        except Exception:
            continue
    tasks = []
    plan = {}
    for cs in cats:
        cands = pick_candidates((prof.get("cat_sides") or {}).get(cs) or {}, args.n_useful, args.n_harm, args.n_tim)
        sss = [s for s in bases if cat_side_of(s) == cs]
        if args.symsides:
            sss = [s for s in args.symsides.split(",") if s in bases and cat_side_of(s) == cs]
        sss = sss[: args.per_cat]
        plan[cs] = {"n_cands": len(cands), "sym_sides": sss}
        for ss in sss:
            tasks.append((ss, bases[ss], cands))
    print(f"[flip-study] {len(tasks)} sym_side tasks, cands per cat: {{c: p['n_cands'] for c, p in plan.items()}}", flush=True)
    results = {}
    errors = {}
    t0 = time.time()
    with cf.ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("fork")) as ex:
        for i, (ss, out, err) in enumerate(ex.map(_w_symside, tasks)):
            if out is None:
                errors[ss] = err
            else:
                results[ss] = out
            if (i + 1) % 5 == 0:
                print(f"[flip-study] {i+1}/{len(tasks)} {time.time()-t0:.0f}s errors={len(errors)}", flush=True)
    agg = {}
    for cs in cats:
        per = collections.defaultdict(list)
        for ss in plan[cs]["sym_sides"]:
            r = results.get(ss)
            if not r or not r.get("base"):
                continue
            b = r["base"]
            for key, f in (r.get("flips") or {}).items():
                if not f or f.get("identity") or f.get("err") or f.get("gain_pct") is None:
                    continue
                def _d(k):
                    try:
                        return float(f.get(k)) - float(b.get(k))
                    except Exception:
                        return None
                per[key].append({"ss": ss, "d_gain": _d("gain_pct"), "d_tim": _d("tim_pct"), "d_dd": _d("max_dd_pct"), "d_wr": _d("wr_pct"), "d_trades": _d("trades"), "valid": bool(f.get("valid")), "base_valid": bool(b.get("valid")), "reason": f.get("invalid_reason")})
        cs_out = {}
        for key, rows in per.items():
            vals = [x for x in rows if x["d_gain"] is not None]
            if not vals:
                continue
            def _mean(k):
                xs = [x[k] for x in vals if x[k] is not None]
                return round(statistics.fmean(xs), 3) if xs else None
            def _med(k):
                xs = [x[k] for x in vals if x[k] is not None]
                return round(statistics.median(xs), 3) if xs else None
            pos = sum(1 for x in vals if x["d_gain"] > 1e-9 and x["valid"])
            neg = sum(1 for x in vals if x["d_gain"] < -1e-9)
            e = {"n": len(vals), "pos": pos, "neg": neg, "invalid": sum(1 for x in vals if not x["valid"]), "mean_d_gain": _mean("d_gain"), "median_d_gain": _med("d_gain"), "mean_d_tim": _mean("d_tim"), "mean_d_dd": _mean("d_dd"), "mean_d_wr": _mean("d_wr"), "mean_d_trades": _mean("d_trades"), "profile_verdict": (((prof.get("cat_sides") or {}).get(cs) or {}).get("switch_cand") or {}).get(key, {}).get("verdict")}
            tags = []
            for k, up, dn, thr in (("mean_d_dd", "DD_UP", "DD_DOWN", 1.5), ("mean_d_wr", "WR_UP", "WR_DOWN", 3.0), ("mean_d_tim", "TIM_UP", "TIM_DOWN", 2.0), ("mean_d_trades", "TRADES_UP", "TRADES_DOWN", 5.0), ("mean_d_gain", "GAIN_UP", "GAIN_DOWN", 1.0)):
                v = e.get(k)
                if v is None:
                    continue
                if v >= thr:
                    tags.append(up)
                elif v <= -thr:
                    tags.append(dn)
            e["effect"] = tags
            cs_out[key] = e
        agg[cs] = cs_out
    out = {"_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "host": os.uname().nodename, "secs": round(time.time() - t0, 1), "tasks": len(tasks), "ok": len(results), "errors": errors, "plan": {c: {"n_cands": p["n_cands"], "n_sym": len(p["sym_sides"])} for c, p in plan.items()}, "engine": "tools.opt.v12_pilot.evaluate_prepared_sanitized (real v12_quick_engine)"},
           "cat_sides": agg, "raw": results}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out + ".tmp", "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(args.out + ".tmp", args.out)
    write_md(out, args.out[:-5] + ".md")
    print(json.dumps(out["_meta"], indent=1))


def write_md(out, path):
    m = out["_meta"]
    L = [f"# Switch flip study (engine-verified DD/WR/TIM/gain/trades) — {m['at']} on {m['host']}", "",
         f"{m['ok']}/{m['tasks']} sym_side bases evaluated in {m['secs']}s ({m['engine']}). Every row = mean engine difference flip − base over n real sym_side bases. Errors: {len(m['errors'])}.", ""]
    for cs, d in out["cat_sides"].items():
        L += [f"## {cs} ({m['plan'][cs]['n_sym']} sym_sides × {m['plan'][cs]['n_cands']} flips)", "", "| switch=cand | profile | n | pos | neg | inv | d_gain | d_tim | d_dd | d_wr | d_trades | effect |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for k, e in sorted(d.items(), key=lambda kv: -(kv[1]["mean_d_gain"] or 0)):
            L.append(f"| `{k}` | {e['profile_verdict']} | {e['n']} | {e['pos']} | {e['neg']} | {e['invalid']} | {e['mean_d_gain']} | {e['mean_d_tim']} | {e['mean_d_dd']} | {e['mean_d_wr']} | {e['mean_d_trades']} | {' '.join(e['effect'])} |")
        L.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    main()
