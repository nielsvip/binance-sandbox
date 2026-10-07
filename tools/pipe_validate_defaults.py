#!/usr/bin/env python3
"""pipe_validate_defaults — validate a candidate default set against the current defaults on the real vector engine (PIPE 2026-10-01).
Run INSIDE ~/binance-sandbox on a host (imports v12_quick_engine + tools.opt.v12_pilot like tools/v15_row365_filters.py); read-only (never writes the sandbox).
usage: pipe_validate_defaults.py CANDIDATES.json OUT.json [--sample-file S.json] [--procs N]
CANDIDATES.json: {CAT_SIDE: {KEY: value, ...}}  (keys wired in both live and vector; value already coerced)
Per cat_side: sample sym_sides (>=12 per venue, finished sheets, crypto + verifiable stocks), evaluate on 30D and (365D when span >= 330 d):
baseline = current defaults ({} overrides), then GREEDY: add candidate keys one by one in the given order, accept a key only if the mean 30D gain over the sample rises AND the mean 365D gain does not drop by more than TOL365 AND the number of sym_sides that get worse than baseline does not exceed half. Writes accepted keys, per-key trace and the final before/after table."""
import json, os, sys, time, multiprocessing as mp
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), "tools"))
TOL365 = 0.5


def coerce(v, ref):
    return v


def eval_ss(args):
    ss, window, cand_sets = args  # cand_sets: list of override dicts
    os.nice(5)
    os.environ.setdefault("V12_NPZ_CACHE", "2")
    from tools.opt import v12_pilot as P
    prep = P.prepare_batch(ss, window)
    if prep is None:
        return ss, window, None
    if window >= 365:
        try:
            ts = prep["npz_prepared"]["timestamps"]
            span = (float(ts[-1]) - float(ts[0])) / (1000.0 if float(ts[-1]) > 1e11 else 1.0) / 86400.0
        except Exception:
            span = 0.0
        if span < 330:
            return ss, window, None
    import v15_newx_scan as NX
    cfgd = prep.get("base_cfg_dict", {})
    out = []
    for ov in cand_sets:
        ov = {k: NX.coerce(v, cfgd.get(k)) for k, v in ov.items()}
        r = P.evaluate_prepared_sanitized(prep, ov, window)
        out.append({"gain": r.get("gain_pct"), "trades": r.get("trades"), "valid": bool(r.get("valid")), "tim": r.get("tim_pct"), "dd": r.get("max_dd_pct")})
    return ss, window, out


def main():
    cand = json.load(open(sys.argv[1])); outp = sys.argv[2]
    sample = json.load(open(sys.argv[sys.argv.index("--sample-file") + 1])) if "--sample-file" in sys.argv else {}
    procs = int(sys.argv[sys.argv.index("--procs") + 1]) if "--procs" in sys.argv else 8
    res = {}
    for cs, keys in cand.items():
        ss_list = sample.get(cs) or []
        if not ss_list or not keys:
            res[cs] = {"accepted": {}, "note": "no sample or no candidates"}; continue
        order = list(keys.items())
        # build override sets: baseline + each key alone
        sets = [{}] + [{k: v} for k, v in order]
        with mp.Pool(procs) as pool:
            jobs = [(ss, w, sets) for ss in ss_list for w in (30, 365)]
            outs = pool.map(eval_ss, jobs, chunksize=1)
        tab = {}
        for ss, w, o in outs:
            if o is not None:
                tab[(ss, w)] = o
        def mean(w, idx, ok=None):
            v = [tab[(ss, w)][idx]["gain"] for ss in ss_list if (ss, w) in tab and tab[(ss, w)][idx]["gain"] is not None]
            return sum(v) / len(v) if v else None
        base30, base365 = mean(30, 0), mean(365, 0)
        trace = []
        for i, (k, v) in enumerate(order, start=1):
            g30, g365 = mean(30, i), mean(365, i)
            worse = sum(1 for ss in ss_list if (ss, 30) in tab and tab[(ss, 30)][i]["gain"] is not None and tab[(ss, 30)][0]["gain"] is not None and tab[(ss, 30)][i]["gain"] < tab[(ss, 30)][0]["gain"] - 1e-9)
            n = sum(1 for ss in ss_list if (ss, 30) in tab)
            ok = g30 is not None and base30 is not None and g30 > base30 + 1e-9 and (g365 is None or base365 is None or g365 >= base365 - TOL365) and worse * 2 <= n
            trace.append({"key": k, "value": v, "alone_30D": g30, "alone_365D": g365, "n_worse_30D": worse, "n": n, "accept_alone": ok})
        # greedy joint
        acc = {}
        best_ss = {ss: tab[(ss, 30)][0]["gain"] for ss in ss_list if (ss, 30) in tab}
        res[cs] = {"base30": base30, "base365": base365, "alone": trace, "sample": ss_list}
        cands = [t for t in trace if t["accept_alone"]]
        cands.sort(key=lambda t: -(t["alone_30D"] - base30))
        # joint evaluation of accepted-alone set, then drop keys that hurt jointly
        cur = {}
        jobs_sets = []
        for t in cands:
            cur = dict(cur); cur[t["key"]] = t["value"]; jobs_sets.append(dict(cur))
        if jobs_sets:
            with mp.Pool(procs) as pool:
                outs2 = pool.map(eval_ss, [(ss, w, [{}] + jobs_sets) for ss in ss_list for w in (30, 365)], chunksize=1)
            tab2 = {(ss, w): o for ss, w, o in outs2 if o is not None}
            def m2(w, idx):
                v = [tab2[(ss, w)][idx]["gain"] for ss in ss_list if (ss, w) in tab2 and tab2[(ss, w)][idx]["gain"] is not None]
                return sum(v) / len(v) if v else None
            prev30, prev365, keep_n = m2(30, 0), m2(365, 0), 0
            seq = []
            for j, t in enumerate(cands, start=1):
                g30, g365 = m2(30, j), m2(365, j)
                good = g30 is not None and g30 > prev30 + 1e-9 and (g365 is None or prev365 is None or g365 >= prev365 - TOL365)
                seq.append({"key": t["key"], "joint30": g30, "joint365": g365, "accepted": bool(good)})
                if good:
                    acc[t["key"]] = t["value"]; prev30, prev365 = g30, (g365 if g365 is not None else prev365)
                else:
                    # the chain continues from the last accepted joint state only if later keys are re-evaluated: simple rule = stop adding keys after the first joint failure
                    break
            res[cs]["joint_sequence"] = seq
            res[cs]["final30"], res[cs]["final365"] = prev30, prev365
        res[cs]["accepted"] = acc
        print(cs, "base30", base30, "base365", base365, "accepted", len(acc), "of", len(order), flush=True)
        json.dump(res, open(outp, "w"), indent=1)
    json.dump(res, open(outp, "w"), indent=1)


if __name__ == "__main__":
    main()
