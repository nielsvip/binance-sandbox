#!/usr/bin/env python3
"""v17_priors_build — merge per-host eval_cache partials (tools/v17_priors_mine.py) + the causal
learner extract into data/causal/v17_priors.json + data/causal/v17_priors.md.

Every number is aggregated from real cached engine evals (differences of two evals on the same
price slice). Per-sym first (mean over that sym_side's measurements), then across sym_sides.
Sample sizes are always carried (n = sym_sides). Below n<3 -> INSUFFICIENT, never a verdict.
"""
import argparse
import collections
import json
import os
import statistics
import time

EPS = 1e-9
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SCHEMA_VERSION = 1
MIN_PAIR_N = 2


def r4(x):
    return None if x is None else round(float(x), 4)


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def median(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def verdict(n, pos, neg, zero, mean_d):
    if n < 3:
        return "INSUFFICIENT"
    rate = pos / n if n else 0.0
    if pos >= 3 and rate >= 0.15 and (mean_d or 0) > 0:
        return "USEFUL"
    if n >= 10 and pos == 0 and zero == n:
        return "DEAD"
    if n >= 10 and pos == 0 and neg >= 3:
        return "HARMFUL"
    if n >= 10 and pos <= 1 and rate < 0.05:
        return "USELESS"
    if pos >= 1:
        return "SOMETIMES"
    return "NEUTRAL"


def build_single(rows):
    per = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0, 0.0, 0]))
    for cs, label, sym, v in rows:
        a = per[(cs, label)][sym]
        for i in range(11):
            a[i] += v[i]
    out = collections.defaultdict(dict)
    for (cs, label), syms in per.items():
        g, tr, tim, dd, wr = [], [], [], [], []
        invalid = 0
        n_new = n_old_only = agree = both = 0
        n_evals = 0
        for sym, a in syms.items():
            if a[0] == 0:
                if a[10] > 0:
                    invalid += 1
                continue
            n_evals += a[0]
            g.append(a[1] / a[0])
            tr.append(a[2] / a[0])
            tim.append(a[3] / a[0])
            dd.append(a[4] / a[0])
            wr.append(a[5] / a[0])
            if a[8] > 0:
                n_new += 1
            elif a[6] > 0:
                n_old_only += 1
            if a[6] > 0 and a[8] > 0:
                both += 1
                so, sn = a[7] / a[6], a[9] / a[8]
                if (so > EPS) == (sn > EPS) and (so < -EPS) == (sn < -EPS):
                    agree += 1
        n = len(g)
        if n == 0 and invalid == 0:
            continue
        pos = sum(1 for d in g if d > EPS)
        neg = sum(1 for d in g if d < -EPS)
        zero = n - pos - neg
        m = mean(g)
        out[cs][label] = {
            "n": n, "pos": pos, "neg": neg, "zero": zero, "invalid": invalid,
            "mean": r4(m), "median": r4(median(g)), "sum_pos": r4(sum(d for d in g if d > EPS)),
            "d_trades": r4(mean(tr)), "d_tim": r4(mean(tim)), "d_dd": r4(mean(dd)), "d_wr": r4(mean(wr)),
            "n_evals": n_evals, "n_sym_new": n_new, "n_sym_old_only": n_old_only,
            "old_new_both": both, "old_new_sign_agree": agree,
            "verdict": verdict(n, pos, neg, zero, m),
            "absent_value": label.endswith("=<absent>"),
        }
    return out


def build_blocks(rows, exemptions, top):
    per = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0]))
    for cs, s, f, fon, sym, v in rows:
        a = per[(cs, s, f, fon)][sym]
        for i in range(3):
            a[i] += v[i]
    out = collections.defaultdict(list)
    totals = collections.Counter()
    for (cs, s, f, fon), syms in per.items():
        offs, ons, unl = [], [], []
        pos = 0
        for sym, a in syms.items():
            if a[0] == 0:
                continue
            d_off, d_on = a[1] / a[0], a[2] / a[0]
            offs.append(d_off)
            ons.append(d_on)
            unl.append(d_off - d_on)
            if d_off > EPS and d_on <= EPS:
                pos += 1
        totals[cs] += 1
        if pos == 0 or len(offs) < MIN_PAIR_N:
            continue
        sw = s.split("=", 1)[0]
        ex = (exemptions.get(sw) or {}).get(f)
        out[cs].append({
            "switch": s, "switch_key": sw, "filter": f, "filter_on_value": fon, "n": len(offs), "pos_sym": pos,
            "mean_unlock": r4(mean(unl)), "mean_d_switch_filter_off": r4(mean(offs)), "mean_d_switch_filter_on": r4(mean(ons)),
            "exemption_override": ex, "has_exemption_key": ex is not None,
        })
    for cs in out:
        out[cs].sort(key=lambda e: (-e["pos_sym"], -(e["mean_unlock"] or 0)))
        out[cs] = out[cs][:top]
    return out, dict(totals)


def build_synergy(rows, top):
    per = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0]))
    for cs, l1, l2, sym, v in rows:
        a = per[(cs, l1, l2)][sym]
        for i in range(5):
            a[i] += v[i]
    syn = collections.defaultdict(list)
    anti = collections.defaultdict(list)
    totals = collections.Counter()
    for (cs, l1, l2), syms in per.items():
        J, A, B, I = [], [], [], []
        syn_sym = anti_sym = 0
        for sym, a in syms.items():
            j, s1, s2, it = a[1] / a[0], a[2] / a[0], a[3] / a[0], a[4] / a[0]
            J.append(j)
            A.append(s1)
            B.append(s2)
            I.append(it)
            if it > EPS and j > EPS and j > max(s1, s2) + EPS:
                syn_sym += 1
            if it < -EPS:
                anti_sym += 1
        totals[cs] += 1
        if len(J) < MIN_PAIR_N:
            continue
        e = {"a": l1, "b": l2, "n": len(J), "mean_joint": r4(mean(J)), "mean_single_a": r4(mean(A)), "mean_single_b": r4(mean(B)),
             "mean_interaction": r4(mean(I)), "syn_sym": syn_sym, "anti_sym": anti_sym}
        if syn_sym:
            syn[cs].append(e)
        if anti_sym:
            anti[cs].append(e)
    for cs in list(syn):
        syn[cs].sort(key=lambda e: (-e["syn_sym"], -(e["mean_interaction"] or 0)))
        syn[cs] = syn[cs][:top]
    for cs in list(anti):
        anti[cs].sort(key=lambda e: (-e["anti_sym"], e["mean_interaction"] or 0))
        anti[cs] = anti[cs][:top]
    return syn, anti, dict(totals)


def _norm_label(label):
    k, _, v = label.partition("=")
    try:
        return f"{k}={float(v):g}"
    except Exception:
        return f"{k}={v}"


def conflicts_with_learner(single, cl):
    flags = collections.defaultdict(list)
    if not cl:
        return flags
    naked = cl.get("naked_cells") or {}
    for cs in CAT_SIDES:
        sn = {_norm_label(k): (k, v) for k, v in (single.get(cs) or {}).items()}
        for label, c in (naked.get(cs) or {}).items():
            hit = sn.get(_norm_label(label))
            if not hit:
                continue
            k, s = hit
            if s["n"] < 3 or c["n"] < 3:
                continue
            cl_pos = c["pos"] >= 3 and c["pos"] / c["n"] >= 0.15 and c["mean"] > 0
            cl_bad = c["pos"] == 0 and c["neg"] >= 3
            if cl_pos and s["verdict"] in ("HARMFUL", "DEAD", "USELESS"):
                flags[cs].append({"label": k, "kind": "learner_positive_vs_cache_" + s["verdict"].lower(), "learner": c, "cache": {x: s[x] for x in ("n", "pos", "neg", "mean")}})
            elif cl_bad and s["verdict"] == "USEFUL":
                flags[cs].append({"label": k, "kind": "learner_harmful_vs_cache_useful", "learner": c, "cache": {x: s[x] for x in ("n", "pos", "neg", "mean")}})
    for f in cl.get("fixes") or []:
        if f.get("kind") != "disable_switch" or f.get("status") != "confirmed":
            continue
        sw = f["target"]
        for cs in CAT_SIDES:
            for label, s in (single.get(cs) or {}).items():
                if label.split("=", 1)[0] == sw and s["pos"] >= 3 and (s["mean"] or 0) > 0:
                    flags[cs].append({"label": label, "kind": "learner_confirmed_disable_vs_cache_positive", "fix_id": f["id"], "cache": {x: s[x] for x in ("n", "pos", "neg", "mean")}})
    return flags


def learner_summary(cl):
    if not cl:
        return None
    outs = cl.get("fix_outcomes") or []
    by_fix = collections.defaultdict(collections.Counter)
    for o in outs:
        by_fix[(o["target"], o["kind"], o["scope"])][o["verdict"]] += 1
    return {
        "meta": cl.get("meta"),
        "confirmed_causes": [{"target": f["target"], "scope": f["scope"], "payload": f["payload"], "reason": f["reason"]} for f in cl.get("fixes") or [] if f.get("status") == "confirmed"],
        "rejected_causes": [{"target": f["target"], "scope": f["scope"], "reason": f["reason"]} for f in cl.get("fixes") or [] if f.get("status") == "rejected"],
        "rescue_ablations": [{"target": f["target"], "scope": f["scope"], "verdict": (f["payload"] or {}).get("verdict") if isinstance(f["payload"], dict) else None,
                              "n_loo": (f["payload"] or {}).get("n_loo") if isinstance(f["payload"], dict) else None,
                              "mean_d_gain": (f["payload"] or {}).get("mean_d_gain") if isinstance(f["payload"], dict) else None,
                              "label": "[DIAGNOSTIC ONLY]"} for f in cl.get("fixes") or [] if f.get("kind") == "rescue_ablation"],
        "fix_outcome_counts": [{"target": k[0], "kind": k[1], "scope": k[2], "verdicts": dict(v)} for k, v in sorted(by_fix.items())],
        "fix_ranking_nonzero": cl.get("fix_ranking_nonzero"),
        "proposed_disable_top": cl.get("proposed_top"),
    }


def render_md(doc):
    L = []
    m = doc["meta"]
    L.append("# V17 priors — mined from eval_cache_v1.db + causal_learner.db")
    L.append("")
    L.append(f"Generated {m['generated_utc']} · schema v{m['schema_version']} · every number is a difference of two cached REAL engine evals on the same price slice; n = sym_sides.")
    L.append("")
    L.append("## Sources")
    for s in m["sources"]:
        L.append(f"- **{s.get('host')}** `{s.get('db')}` {s.get('db_bytes', 0)/1e9:.2f} GB · rows {s.get('rows')} · contexts {s.get('contexts')} · edges {s.get('stats', {}).get('edges')} · quartets {s.get('stats', {}).get('quartets')} · rows >14d {s.get('rows_older_14d')} · created {s.get('created_min_utc')} → {s.get('created_max_utc')} · engine-drift collisions {s.get('stats', {}).get('engine_drift_collisions', 0)}")
    if m.get("causal_learner"):
        cm = m["causal_learner"]
        L.append(f"- **S1 causal_learner.db** {cm.get('db_bytes', 0)/1e9:.2f} GB · lessons {cm.get('lessons')} (tombstones {cm.get('lessons_pruned_tombstones')}) · causes {cm.get('causes')} · fixes {cm.get('fixes')} · fix_outcomes {cm.get('fix_outcomes')} · {cm.get('lesson_ts_range')}")
    L.append("")
    L.append("Caveats: engine version is NOT stored per row (only inside the sha1 key); rows are paired only inside one price slice and within " + str(m.get("max_edge_gap_h")) + " h, identical-override rows with different results are excluded as engine/base drift. 30D windows; per-trade return lists are not in the cache, so Sharpe is not derived here.")
    L.append("")
    for cs in CAT_SIDES:
        d = doc.get(cs) or {}
        single = d.get("single") or {}
        L.append(f"## {cs}")
        L.append(f"single keys {len(single)} · blocks {len(d.get('blocks') or [])} · synergy {len(d.get('synergy') or [])} · anti {len(d.get('anti_synergy') or [])} · deprioritise {len(d.get('deprioritise') or [])}")
        L.append("")
        top = sorted(((k, v) for k, v in single.items() if v["n"] >= 3), key=lambda kv: (-kv[1]["pos"], -(kv[1]["mean"] or 0)))[:20]
        L.append("**Top single keys by pos_sym** (n≥3)")
        L.append("")
        L.append("| key | n | pos | neg | mean | median | d_trades | d_tim | d_dd | d_wr | verdict |")
        L.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for k, v in top:
            L.append(f"| `{k}` | {v['n']} | {v['pos']} | {v['neg']} | {v['mean']} | {v['median']} | {v['d_trades']} | {v['d_tim']} | {v['d_dd']} | {v['d_wr']} | {v['verdict']} |")
        L.append("")
        L.append("**Filter-blocks-switch (top 15)**")
        L.append("")
        L.append("| switch | filter (on value) | n | pos_sym | mean_unlock | d_S filter off | d_S filter on | exemption key |")
        L.append("|---|---|---|---|---|---|---|---|")
        for b in (d.get("blocks") or [])[:15]:
            L.append(f"| `{b['switch']}` | `{b['filter']}`={b['filter_on_value']} | {b['n']} | {b['pos_sym']} | {b['mean_unlock']} | {b['mean_d_switch_filter_off']} | {b['mean_d_switch_filter_on']} | {'yes' if b['has_exemption_key'] else 'no'} |")
        L.append("")
        L.append("**Synergy (top 10)**")
        L.append("")
        L.append("| a | b | n | syn_sym | joint | single a | single b | interaction |")
        L.append("|---|---|---|---|---|---|---|---|")
        for e in (d.get("synergy") or [])[:10]:
            L.append(f"| `{e['a']}` | `{e['b']}` | {e['n']} | {e['syn_sym']} | {e['mean_joint']} | {e['mean_single_a']} | {e['mean_single_b']} | {e['mean_interaction']} |")
        L.append("")
        L.append("**Anti-synergy (top 5)**")
        L.append("")
        for e in (d.get("anti_synergy") or [])[:5]:
            L.append(f"- `{e['a']}` + `{e['b']}` n={e['n']} anti_sym={e['anti_sym']} interaction={e['mean_interaction']}")
        L.append("")
        dep = d.get("deprioritise") or []
        L.append(f"**Deprioritise** ({len(dep)} keys, verdict DEAD/USELESS/HARMFUL at n≥10) — killers first:")
        L.append("")
        for e in [x for x in dep if x["verdict"] == "HARMFUL"][:15]:
            L.append(f"- `{e['key']}` HARMFUL n={e['n']} neg={e['neg']} mean={e['mean']}")
        L.append("")
        fl = d.get("learner_conflicts") or []
        L.append(f"**Conflicts with causal_learner** ({len(fl)})")
        L.append("")
        for f in fl[:15]:
            L.append(f"- `{f['label']}` {f['kind']} cache={f.get('cache')} learner={f.get('learner', f.get('fix_id'))}")
        L.append("")
    ls = doc.get("causal_learner") or {}
    L.append("## causal_learner — confirmed causes / rescue outcomes")
    L.append("")
    for c in ls.get("confirmed_causes") or []:
        L.append(f"- CONFIRMED disable `{c['target']}` — {c['reason']}")
    for c in ls.get("rejected_causes") or []:
        L.append(f"- REJECTED (auto-lifted) `{c['target']}` — {c['reason']}")
    rv = collections.Counter(r.get("verdict") for r in ls.get("rescue_ablations") or [])
    L.append(f"- rescue ablations: {len(ls.get('rescue_ablations') or [])} fixes, verdicts {dict(rv)} [DIAGNOSTIC ONLY]")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partials", nargs="+", required=True)
    ap.add_argument("--cl", default="")
    ap.add_argument("--exemptions", default="data/v17_filter_exemptions.json")
    ap.add_argument("--out", default="data/causal/v17_priors.json")
    ap.add_argument("--md", default="data/causal/v17_priors.md")
    ap.add_argument("--top", type=int, default=300)
    args = ap.parse_args()
    exemptions = json.load(open(args.exemptions)) if os.path.exists(args.exemptions) else {}
    srows, qrows, brows, sources = [], [], [], []
    for p in args.partials:
        d = json.load(open(p))
        mm = d["meta"]
        for k in ("created_min", "created_max"):
            if mm.get(k):
                mm[k + "_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mm[k]))
        sources.append(mm)
        srows += d["single"]
        qrows += d["quart"]
        brows += d["blocks"]
    cl = json.load(open(args.cl)) if args.cl and os.path.exists(args.cl) else None
    single = build_single(srows)
    blocks, btot = build_blocks(brows, exemptions, args.top)
    syn, anti, qtot = build_synergy(qrows, args.top)
    flags = conflicts_with_learner(single, cl)
    doc = {"meta": {
        "schema_version": SCHEMA_VERSION, "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": [{k: v for k, v in s.items() if k != "drift_examples"} for s in sources],
        "rows_total": sum(s.get("rows", 0) for s in sources),
        "rows_older_14d_total": sum(s.get("rows_older_14d", 0) for s in sources),
        "causal_learner": (cl or {}).get("meta"),
        "exemptions_file": args.exemptions, "max_edge_gap_h": (sources[0].get("max_edge_gap_h") if sources else None),
        "block_pairs_measured": btot, "synergy_pairs_measured": qtot,
        "engine_version_note": "eval_cache rows carry no engine md5 (it is hashed into the key only); old-engine rows are evidence with that caveat, drift collisions excluded",
        "eps": EPS, "min_pair_n": MIN_PAIR_N,
        "sym_sides_per_cat_side": {cs: len({sym for c, _l, sym, _v in srows if c == cs}) for cs in CAT_SIDES},
    }}
    for cs in CAT_SIDES:
        s = single.get(cs) or {}
        dep = sorted(({"key": k, "verdict": v["verdict"], "n": v["n"], "pos": v["pos"], "neg": v["neg"], "mean": v["mean"]}
                      for k, v in s.items() if v["verdict"] in ("DEAD", "USELESS", "HARMFUL")), key=lambda e: ({"HARMFUL": 0, "USELESS": 1, "DEAD": 2}[e["verdict"]], e["mean"] or 0))
        doc[cs] = {"single": s, "blocks": blocks.get(cs, []), "synergy": syn.get(cs, []), "anti_synergy": anti.get(cs, []),
                   "deprioritise": dep, "learner_conflicts": flags.get(cs, [])}
    doc["causal_learner"] = learner_summary(cl)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"), sort_keys=False)
    json.load(open(tmp))
    os.replace(tmp, args.out)
    with open(args.md, "w") as fh:
        fh.write(render_md(doc))
    print(f"[build] {args.out} {os.path.getsize(args.out)/1e6:.2f} MB; " + ", ".join(f"{cs}: single={len(doc[cs]['single'])} blocks={len(doc[cs]['blocks'])} syn={len(doc[cs]['synergy'])}" for cs in CAT_SIDES))


if __name__ == "__main__":
    main()
