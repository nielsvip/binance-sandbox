#!/usr/bin/env python3
"""V18 switch profile ("encyclopedia"): one profile per cat_side and template switch, built ONLY from evidence that already exists.

Reads (read-only): data/brain/v18_brain_<CS>.json (nodes / unary rows / pair rows / regime rows / sym rows), optionally the brain
state pickle (exact n_sym_pos, sum_neg, pair n_sym_pos), data/learn/digest_<CS>.json (winners, filter_veto), the replayed board
ledgers (data/learn/trade_ledgers/<CS>/*.jsonl + replay_sets.jsonl.gz) and the cat_side TEMPLATE (v18_learn_common.template_index).
Writes NEW files only: data/learn/switch_profile_<CS>.{json,md}, data/learn/needed_filters_<CS>.json and the fleet summary doc.

Four cat_sides are four separate computations; nothing is ever averaged or pooled across them. A quantity that the evidence cannot
give is written as null / "not derivable" with the log that would give it. Verdicts carry n.

    python3 tools/v18_switch_profile.py [--cat-side CRYPTO_LONG ...] [--state data/brain_scratch/v18_brain_state.pkl] [--doc]
"""
import argparse
import collections
import gzip
import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
VERDICTS = ("WORKS", "SOMETIMES", "INERT", "DAMAGING", "UNTRIED")
SCHEMA = "v18_switch_profile/1"
MAX_PROFILE_BYTES = 10 * 1024 * 1024
M_SHRINK = 8.0
P0 = 0.05

# Thresholds = the older switch_causal_profile classify() (USEFUL/SOMETIMES/DEAD/HARMFUL/USELESS/NEUTRAL), re-expressed on the
# brain's counters. Deviations are listed in THRESHOLD_NOTES and printed at the top of every md.
THRESH = {
    "min_sym": 3,          # n_sym < 3 -> UNTRIED (old INSUFFICIENT)
    "works_pos_sym": 3, "works_rate": 0.15,    # pos_sym >= 3, pos_sym/n_sym >= .15, mean delta > 0 (when known) -> WORKS (old USEFUL)
    "some_pos_sym": 2, "some_rate": 0.10,      # pos_sym >= 2, pos_sym/n_sym >= .10 -> SOMETIMES
    "big_sym": 10,         # DAMAGING / INERT need n_sym >= 10
    "inert_pos_sym_max": 1, "inert_rate": 0.05,   # pos_sym <= 1 and < 5% -> INERT (old DEAD / USELESS)
    "dmg_neg_evals": 3, "dmg_inv_evals": 3,       # pos_sym == 0 and (neg evals >= 3 or invalid evals >= 3) -> DAMAGING (old HARMFUL)
    "needed_min_pos_sym": 2,                      # filter=opt cell positive with the row on >= 2 sym_sides -> NEEDED
    "suffocate_min_flags": 2,                     # n_rescue + n_kill + abl_kill >= 2 -> SUFFOCATING (digest veto entries always qualify)
}
THRESHOLD_NOTES = [
    "Old NEUTRAL (n_sym >= 3, none of the other classes) is folded: n_sym >= 10 -> INERT (weak or no effect), 3 <= n_sym < 10 -> UNTRIED with evidence_class THIN.",
    "neg_sym (sym_sides with a negative delta) is not in the brain; DAMAGING uses negative / invalid EVAL counts instead of sym_side counts.",
    "mean delta > 0 for WORKS is applied only when the brain state pickle supplies sum_neg; otherwise mean_delta is null and the test is skipped (flagged mean_delta_known=false).",
    "pos_sym = sym_sides with at least one positive eval of the row (state n_sym_pos, else sym-scope rows with wpos > 0).",
    "A default (bold) row with no valid evals is the reference (delta 0 by construction): verdict UNTRIED, sub DEFAULT_REFERENCE. A default row that does carry evals (the chain on that board held another value, so flipping to it was a real change) is classified like any other row, with is_default=true.",
    "Switch-level verdict = best row verdict in the order WORKS > SOMETIMES > DAMAGING > INERT > UNTRIED, except that DAMAGING needs the DAMAGING rows to hold >= 50% of the valid evals of the INERT+DAMAGING rows (else INERT); reference rows without evals are left out.",
]


def utc_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def shrunk(wpos, wn):
    return (wpos + M_SHRINK * P0) / (wn + M_SHRINK)


def split_key(node):
    """'R:SW=cand' -> ('SW', 'cand'); 'F:FILTER=opt' -> ('FILTER', 'opt')."""
    body = node.split(":", 1)[1]
    sw, _, cand = body.partition("=")
    return sw, cand


# ------------------------------------------------------------------------------------------------ evidence loading

def load_brain_evidence(blob):
    """Normalise a v18_brain export into plain dicts. All numbers are the export's own."""
    nodes = blob["nodes"]
    cols = blob["_meta"]["cs_cols"]
    rows, filt = {}, {}
    for r in blob.get("cs", []):
        node = nodes[r[0]]
        d = dict(zip(cols, r))
        if node.startswith("R:"):
            rows[node[2:]] = d
        elif node.startswith("F:"):
            filt[node[2:]] = d
    pcols = blob["_meta"]["pair_cols"]
    pairs = collections.defaultdict(dict)
    for r in blob.get("pair", []):
        f, row = nodes[r[0]], nodes[r[1]]
        if not (f.startswith("F:") and row.startswith("R:")):
            continue
        d = dict(zip(pcols[2:], r[2:]))
        pairs[row[2:]][f[2:]] = d
    regimes = collections.defaultdict(dict)
    for r in blob.get("regime", []):
        node = nodes[r[0]]
        if node.startswith("R:"):
            regimes[node[2:]][r[1]] = (float(r[2]), float(r[3]))
    sym_n, sym_pos = collections.Counter(), collections.Counter()
    for _ss, lst in (blob.get("sym") or {}).items():
        for node_i, wpos, wn in lst:
            node = nodes[node_i]
            if not node.startswith("R:"):
                continue
            if wn > 0:
                sym_n[node[2:]] += 1
            if wpos > 0:
                sym_pos[node[2:]] += 1
    return {"rows": rows, "filters": filt, "pairs": pairs, "regimes": regimes, "sym_n": sym_n, "sym_pos": sym_pos,
            "meta": blob["_meta"], "coverage": blob.get("coverage"), "forward_eval": blob.get("forward_eval")}


def load_state_extra(state_path, cat_side, ev):
    """Exact n_sym_pos / sum_neg for rows and n_sym_pos / mean_marg / syn_neg for pairs from the brain's own state pickle.
    Entries whose eval count differs from the export are dropped (stale pickle), never mixed."""
    sys.path.insert(0, str(ROOT / "tools"))
    import v18_brain as B
    st = B.load_state(str(state_path))
    sb = st["brains"].get(cat_side)
    if sb is None:
        return None
    V = B.get_view(sb)
    C = B.C
    rows, pairs, dropped = {}, collections.defaultdict(dict), 0
    for (s, na, nb), _i, E in V.edges(lambda s, na, nb: s == 0):
        a = V.nodes[na]
        if nb == -1 and a.startswith("R:"):
            key = a[2:]
            rd = ev["rows"].get(key)
            if rd is None or int(E[C["n"]]) != int(rd["n"]):
                dropped += 1
                continue
            nv = max(1.0, E[C["n"]] - E[C["inv"]])
            rows[key] = {"n_sym_pos": int(E[C["n_sym_pos"]]), "n_sym": int(E[C["n_sym"]]), "sum_neg": float(E[C["sum_neg"]]),
                         "mean_delta": round(float((E[C["sum_pos"]] + E[C["sum_neg"]]) / nv), 4)}
        elif nb >= 0 and a.startswith("F:") and V.nodes[nb].startswith("R:"):
            key, fo = V.nodes[nb][2:], a[2:]
            pd = ev["pairs"].get(key, {}).get(fo)
            if pd is None or int(E[C["n"]]) != int(pd["n"]):
                dropped += 1
                continue
            nv = max(1.0, E[C["n"]] - E[C["inv"]])
            pairs[key][fo] = {"n_sym_pos": int(E[C["n_sym_pos"]]), "syn_neg": int(E[C["syn_neg"]]), "n_inv": int(E[C["inv"]]),
                              "mean_marg": round(float((E[C["sum_pos"]] + E[C["sum_neg"]]) / nv), 4)}
    return {"rows": rows, "pairs": pairs, "dropped": dropped, "state_meta": st.get("meta")}


def ledger_exit_tables(cat_side, tidx, learn_dir):
    """Per template key (switch=cand): exit families of the replayed board trades whose set carries that key (co-occurrence, not
    causation). Returns {"keys": {key: {fam: [n, pnl_pp, n_loss, loss_sum_pp]}}, "syms": {key: n_sym_sides}, "switch": {...}, "n_ledgers", "n_trades"}."""
    from v18_learn_common import template_cand
    learn_dir = Path(learn_dir)
    sets_by_sha = {}
    sp = learn_dir / "replay_sets.jsonl.gz"
    if sp.exists():
        with gzip.open(sp, "rt") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("cat_side") == cat_side and isinstance(d.get("set"), dict):
                    sets_by_sha[d.get("set_sha256")] = d["set"]
    per_sym, sym_set = {}, {}
    n_trades = 0
    for f in sorted((learn_dir / "trade_ledgers" / cat_side).glob("*.jsonl")):
        meta, fams = None, collections.defaultdict(lambda: [0, 0.0, 0, 0.0])
        with open(f, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("kind") == "meta":
                    meta = d
                elif d.get("kind") == "trade" and meta is not None:
                    pnl = float(d.get("pnl_pp") or 0.0)
                    t = fams[d.get("exit_family") or "UNKNOWN"]
                    t[0] += 1
                    t[1] += pnl
                    if pnl < 0:
                        t[2] += 1
                        t[3] += pnl
                    n_trades += 1
        if meta is None or meta.get("set_sha256") not in sets_by_sha:
            continue
        ss = meta.get("sym_side")
        per_sym[ss] = fams
        keys = set()
        for k, v in sets_by_sha[meta["set_sha256"]].items():
            if isinstance(v, (list, dict)):
                continue
            c = template_cand(k, str(v), tidx)
            if c is not None:
                keys.add(f"{k}={c}")
        sym_set[ss] = keys
    out_keys = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0, 0.0]))
    out_sw = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0, 0, 0.0]))
    syms_k, syms_sw = collections.Counter(), collections.defaultdict(set)
    for ss, keys in sym_set.items():
        for k in keys:
            syms_k[k] += 1
            syms_sw[k.partition("=")[0]].add(ss)
            for fam, t in per_sym[ss].items():
                a = out_keys[k][fam]
                for i in range(4):
                    a[i] += t[i]
    for sw, sset in syms_sw.items():
        for ss in sset:
            for fam, t in per_sym[ss].items():
                a = out_sw[sw][fam]
                for i in range(4):
                    a[i] += t[i]
    return {"keys": out_keys, "syms": syms_k, "switch": out_sw, "switch_syms": {k: len(v) for k, v in syms_sw.items()},
            "n_ledgers": len(per_sym), "n_trades": n_trades}


def exit_rows(fams, top=6):
    tot_loss = sum(t[3] for t in fams.values())
    rows = []
    for fam, t in fams.items():
        rows.append({"exit_family": fam, "trades": t[0], "pnl_pp": round(t[1], 3), "losers": t[2], "loss_sum_pp": round(t[3], 3),
                     "loss_share": round(t[3] / tot_loss, 4) if tot_loss < 0 else None})
    rows.sort(key=lambda r: (r["loss_sum_pp"], -r["trades"]))
    return rows[:top]


# ------------------------------------------------------------------------------------------------ verdicts

def classify(n_sym, pos_sym, neg, inv, mean_delta, T=THRESH):
    """-> (verdict, sub). Mirrors the older switch_causal_profile.classify (see THRESHOLD_NOTES)."""
    if n_sym < T["min_sym"]:
        return "UNTRIED", "INSUFFICIENT"
    rate = pos_sym / n_sym
    if pos_sym >= T["works_pos_sym"] and rate >= T["works_rate"] and (mean_delta is None or mean_delta > 0):
        return "WORKS", "USEFUL"
    if pos_sym >= T["some_pos_sym"] and rate >= T["some_rate"]:
        return "SOMETIMES", "SOMETIMES"
    if n_sym >= T["big_sym"]:
        if pos_sym == 0 and (neg >= T["dmg_neg_evals"] or inv >= T["dmg_inv_evals"]):
            return "DAMAGING", "HARMFUL"
        if pos_sym == 0 and neg == 0 and inv == 0:
            return "INERT", "DEAD"
        if pos_sym <= T["inert_pos_sym_max"] and rate < T["inert_rate"]:
            return "INERT", "USELESS"
        return "INERT", "NEUTRAL_FOLDED"
    return "UNTRIED", "THIN"


def worst_to_best_rank(v):
    return {"WORKS": 0, "SOMETIMES": 1, "DAMAGING": 2, "INERT": 3, "UNTRIED": 4}[v]


def switch_verdict(rows):
    """rows = [(verdict, n_valid)]. Best verdict in the order WORKS > SOMETIMES > DAMAGING > INERT > UNTRIED; DAMAGING only when the
    DAMAGING rows hold >= 50% of the valid evals of the INERT+DAMAGING rows."""
    if not rows:
        return "UNTRIED"
    best = min((v for v, _n in rows), key=worst_to_best_rank)
    if best == "DAMAGING":
        dmg = sum(n for v, n in rows if v == "DAMAGING")
        tried = sum(n for v, n in rows if v in ("DAMAGING", "INERT"))
        if dmg < 0.5 * tried:
            return "INERT"
    return best


# ------------------------------------------------------------------------------------------------ per-row lists

def pair_view(fo, pd, extra, fu):
    n = int(pd["n"])
    pos = int(pd["pos"])
    ex = extra or {}
    sym_pos = ex.get("n_sym_pos")
    basis = "state" if sym_pos is not None else "proxy_min(pos,n_sym)"
    if sym_pos is None:
        sym_pos = min(pos, int(pd.get("n_sym") or 0))
    rate = pos / n if n else 0.0
    f_rate = None
    if fu and (fu.get("n", 0) - fu.get("inv", 0)) > 0:
        f_rate = fu["pos"] / (fu["n"] - fu["inv"])
    syn_neg = ex.get("syn_neg")
    if syn_neg is None:
        syn_neg = int(pd.get("n_syn", 0)) if (pd.get("n_syn", 0) >= 3 and (pd.get("syn_sum") or 0) < 0) else 0
    return {"filter_opt": fo, "n": n, "pos": pos, "pos_rate": round(rate, 4), "pos_syms": int(sym_pos), "pos_syms_basis": basis,
            "n_sym": int(pd.get("n_sym") or 0), "score": round(rate * n, 4), "mean_marg": ex.get("mean_marg"),
            "lift_vs_filter": round(rate / f_rate, 3) if f_rate else None,
            "n_rescue": int(pd.get("n_rescue", 0)), "n_kill": int(pd.get("n_kill", 0)), "abl_n": int(pd.get("abl_n", 0)),
            "abl_kill": int(pd.get("abl_kill", 0)), "syn_neg": int(syn_neg), "n_syn": int(pd.get("n_syn", 0)),
            "syn_sum": round(float(pd.get("syn_sum") or 0.0), 3)}


def split_pairs(views, veto_for_row, T=THRESH):
    needed, suff = [], []
    for v in views:
        if v["pos_syms"] >= T["needed_min_pos_sym"] and v["pos"] > 0:
            needed.append(v)
        kinds = []
        if v["n_rescue"] > 0:
            kinds.append("rescue")
        if v["n_kill"] > 0:
            kinds.append("kill")
        if v["abl_kill"] > 0:
            kinds.append("ablation")
        if v["syn_neg"] > 0:
            kinds.append("neg_synergy")
        flags = v["n_rescue"] + v["n_kill"] + v["abl_kill"]
        vt = veto_for_row.get(v["filter_opt"])
        if vt is not None:
            kinds.append("veto_map")
        if kinds and (flags >= T["suffocate_min_flags"] or vt is not None):
            suff.append(dict(kind=kinds, flags=flags + v["syn_neg"], **{k: v[k] for k in ("filter_opt", "n", "n_rescue", "n_kill", "abl_kill", "syn_neg", "n_sym")}))
    needed.sort(key=lambda v: (-v["score"], -v["pos_syms"], -(v["mean_marg"] or 0.0), v["filter_opt"]))
    suff.sort(key=lambda s: (-s["flags"], s["filter_opt"]))
    return needed, suff


def veto_remedies(digest, cat_side):
    """{row_key: {filter_opt: {remedy kind, override, support_syms, ...}}} from the digest's filter_veto (the first remedy is the digest's pick)."""
    out = collections.defaultdict(dict)
    for e in (digest or {}).get("filter_veto", []) or []:
        rk = f"{e.get('switch')}={e.get('cand')}"
        fo = f"{e.get('filter')}={e.get('opt')}"
        rem = e.get("remedy") or {}
        out[rk][fo] = {"remedy": rem.get("kind"), "override": rem.get("override"), "support_syms": e.get("support_syms"),
                       "vetoed_in_missed_moves": e.get("vetoed_in_missed_moves"), "vetoed_losers": e.get("vetoed_losers")}
    return out


def winner_companions(digest):
    out = collections.defaultdict(list)
    for w in (digest or {}).get("winners", []) or []:
        rk = f"{w.get('switch')}={w.get('cand')}"
        for c in w.get("companions") or []:
            out[rk].append(f"{c.get('filter')}={c.get('opt')}")
    return out


def load_predicted(learn_dir, cat_side, digest):
    """PREDICTED column (never mixed with measured): digest 'proposals' key or data/learn/linknet_scores_<CS>.npz if another agent
    has produced them. Returns ({row_key: [..]}, note)."""
    out, notes = collections.defaultdict(list), []
    props = (digest or {}).get("proposals")
    if props:
        n = 0
        for p in props if isinstance(props, list) else []:
            if isinstance(p, dict) and p.get("switch") is not None:
                out[f"{p.get('switch')}={p.get('cand')}"].append({k: v for k, v in p.items() if k not in ("switch", "cand")})
                n += 1
        notes.append(f"digest proposals: {n} entries attached as PREDICTED")
    npz = Path(learn_dir) / f"linknet_scores_{cat_side}.npz"
    if npz.exists():
        try:
            import numpy as np
            z = np.load(npz, allow_pickle=False)
            if "keys" in z.files and "scores" in z.files:
                for k, s in zip(z["keys"].tolist(), z["scores"].tolist()):
                    out[str(k)].append({"linknet_score": float(s)})
                notes.append(f"linknet npz: {len(z['keys'])} scores attached as PREDICTED")
            else:
                notes.append(f"linknet npz present but arrays {z.files} have no keys/scores contract - not attached")
        except Exception as exc:
            notes.append(f"linknet npz unreadable: {exc}")
    return out, notes


# ------------------------------------------------------------------------------------------------ build

def build_profile(cat_side, blob, tidx, digest=None, extra=None, ledger=None, predicted=None, needed_max=24, T=THRESH):
    ev = load_brain_evidence(blob)
    if extra is None:
        extra = {"rows": {}, "pairs": {}}
    pred, pred_notes = predicted if predicted is not None else ({}, [])
    veto = veto_remedies(digest, cat_side)
    comp = winner_companions(digest)
    yellows_of_tab = {tab: list(blk["yellows"]) for tab, blk in tidx["tabs"].items()}
    key_tabs = collections.defaultdict(list)
    sw_tabs = collections.defaultdict(list)
    for tab, blk in tidx["tabs"].items():
        for _r, sw, cand, _d in blk["rows"]:
            k = f"{sw}={cand}"
            if tab not in key_tabs[k]:
                key_tabs[k].append(tab)
            if tab not in sw_tabs[sw]:
                sw_tabs[sw].append(tab)
    defaults = tidx.get("defaults", {})
    rows_out, pilot = {}, {}
    sw_rows = collections.defaultdict(list)
    for k in key_tabs:
        sw, _, cand = k.partition("=")
        sw_rows[sw].append(cand)
    totals = {"pairs_total": 0, "pilot_listed": 0, "rows_with_list": 0, "rows": 0, "pairs_n_ge3": 0, "pairs_untried": 0,
              "pair_evals_all": 0, "pair_evals_listed": 0, "pair_pos_all": 0, "pair_pos_listed": 0}
    for sw in sorted(sw_rows):
        for cand in sw_rows[sw]:
            k = f"{sw}={cand}"
            r = ev["rows"].get(k)
            ex = extra["rows"].get(k, {})
            is_def = defaults.get(sw) == cand
            tabs = key_tabs[k]
            yellows = sorted({y for t in tabs for y in yellows_of_tab.get(t, [])})
            n = int(r["n"]) if r else 0
            inv = int(r["inv"]) if r else 0
            pos = int(r["pos"]) if r else 0
            neg = int(r["neg"]) if r else 0
            n_valid = max(0, n - inv)
            n_sym = int(r["n_sym"]) if r else 0
            if ex.get("n_sym_pos") is not None:
                pos_sym, ps_basis = int(ex["n_sym_pos"]), "state"
            else:
                pos_sym, ps_basis = min(int(ev["sym_pos"].get(k, 0)), n_sym), "sym_rows_wpos>0"
            mean_delta = ex.get("mean_delta")
            if is_def and n_valid == 0:
                verdict, sub = "UNTRIED", "DEFAULT_REFERENCE"
            else:
                verdict, sub = classify(n_sym, pos_sym, neg, inv, mean_delta, T)
            pairs = ev["pairs"].get(k, {})
            pextra = extra["pairs"].get(k, {})
            views = [pair_view(fo, pd, pextra.get(fo), ev["filters"].get(fo)) for fo, pd in pairs.items() if fo in set(yellows)]
            needed, suff = split_pairs(views, veto.get(k, {}), T)
            seen_in_pairs = {v["filter_opt"] for v in views}
            for fo in veto.get(k, {}):
                if fo in set(yellows) and fo not in {s["filter_opt"] for s in suff}:
                    suff.append({"filter_opt": fo, "kind": ["veto_map"], "flags": 0, "n": 0, "n_rescue": 0, "n_kill": 0, "abl_kill": 0, "syn_neg": 0, "n_sym": 0})
            for s in suff:
                s["remedy"] = veto.get(k, {}).get(s["filter_opt"])
            n_ge3 = sum(1 for v in views if v["n"] >= 3)
            n_seen = sum(1 for v in views if v["n"] >= 1)
            pos_views = [v for v in views if v["pos"] > 0]
            pos_views.sort(key=lambda v: (-v["pos"], -v["pos_syms"], v["filter_opt"]))
            ev_pos_all = [v["filter_opt"] for v in pos_views]
            for fo in comp.get(k, []):
                if fo in set(yellows) and fo not in ev_pos_all:
                    ev_pos_all.append(fo)
            # pilot list: needed -> suffocating -> evidence_pos, disjoint, union <= needed_max
            picked, seen = {"needed": [], "suffocating": [], "evidence_pos": []}, set()
            for name, src in (("needed", [v["filter_opt"] for v in needed]), ("suffocating", [s["filter_opt"] for s in suff]), ("evidence_pos", ev_pos_all)):
                for fo in src:
                    if fo in seen or len(seen) >= needed_max:
                        continue
                    seen.add(fo)
                    picked[name].append(fo)
            untried = len(yellows) - n_seen
            pilot[k] = {"needed": picked["needed"], "suffocating": picked["suffocating"], "evidence_pos": picked["evidence_pos"], "untried_count": untried}
            totals["rows"] += 1
            totals["pairs_total"] += len(yellows)
            totals["pairs_n_ge3"] += n_ge3
            totals["pairs_untried"] += untried
            totals["pilot_listed"] += len(seen)
            totals["pair_evals_all"] += sum(v["n"] for v in views)
            totals["pair_pos_all"] += sum(v["pos"] for v in views)
            totals["pair_evals_listed"] += sum(v["n"] for v in views if v["filter_opt"] in seen)
            totals["pair_pos_listed"] += sum(v["pos"] for v in views if v["filter_opt"] in seen)
            totals["rows_with_list"] += 1 if seen else 0
            exrows = None
            if ledger and k in ledger["keys"]:
                exrows = {"n_ledger_syms": ledger["syms"].get(k, 0), "families": exit_rows(ledger["keys"][k])}
            rg = ev["regimes"].get(k, {})
            fw = r["fwd_n"] if r else 0
            rows_out[k] = {
                "switch": sw, "cand": cand, "is_default": is_def, "tabs": tabs, "verdict": verdict, "verdict_sub": sub,
                "n_evals": n, "n_invalid": inv, "n_valid": n_valid, "n_syms": n_sym, "pos_syms": pos_sym, "pos_syms_basis": ps_basis,
                "n_pos_evals": pos, "n_neg_evals": neg,
                "pos_rate": round(pos / n_valid, 4) if n_valid else None, "pos_rate_sym": round(pos_sym / n_sym, 4) if n_sym else None,
                "sum_pos_delta": round(float(r["sum_pos"]), 3) if r else 0.0, "mean_delta": mean_delta, "mean_delta_known": mean_delta is not None,
                "mean_d_tim": r["d_tim"] if r else None, "mean_d_dd": r["d_dd"] if r else None, "mean_d_wr": r["d_wr"] if r else None,
                "mean_d_trades": r["d_trades"] if r else None, "mean_d_sharpe": r["d_sharpe"] if r else None,
                "forward": {"n": int(r["fwd_n"]), "pos": int(r["fwd_pos"])} if r else {"n": 0, "pos": 0},
                "live": {"n": int(r["live_n"]), "pos": int(r["live_pos"]), "pnl_pp": r["live_pnl_pp"]} if r else {"n": 0, "pos": 0, "pnl_pp": 0.0},
                "regimes": {g: {"wn": wn, "wpos": wpos, "p_pos_shrunk": round(shrunk(wpos, wn), 4)} for g, (wpos, wn) in sorted(rg.items())},
                "needed": [dict((kk, vv) for kk, vv in v.items() if kk in ("filter_opt", "n", "pos", "pos_rate", "pos_syms", "pos_syms_basis", "score", "mean_marg", "lift_vs_filter")) for v in needed[:8]],
                "suffocating": suff[:8],
                "exits_cooccurrence": exrows,
                "coverage": {"pairs_total": len(yellows), "pairs_n_ge3": n_ge3, "pairs_n_1_2": n_seen - n_ge3, "pairs_untried_n0": untried},
                "pilot_list": {"needed": len(picked["needed"]), "suffocating": len(picked["suffocating"]), "evidence_pos": len(picked["evidence_pos"])},
            }
            if k in pred:
                rows_out[k]["predicted"] = pred[k]
    # switch level
    switches = {}
    for sw in sorted(sw_rows):
        rks = [f"{sw}={c}" for c in sw_rows[sw]]
        nd = [rows_out[k] for k in rks if not (rows_out[k]["is_default"] and rows_out[k]["verdict_sub"] == "DEFAULT_REFERENCE")]
        verd = switch_verdict([(r["verdict"], r["n_valid"]) for r in nd])
        cnt = collections.Counter(r["verdict"] for r in nd)
        reg = collections.defaultdict(list)
        for r in nd:
            for g, d in r["regimes"].items():
                if d["wn"] >= 3:
                    reg[g].append((d["p_pos_shrunk"], d["wn"], r["cand"], d["wpos"]))
        best_reg = {g: {"cand": max(v)[2], "p_pos_shrunk": max(v)[0], "n": max(v)[1], "wpos": max(v)[3]} for g, v in sorted(reg.items())}
        agg_need, agg_suff = collections.defaultdict(lambda: [0, 0.0]), collections.defaultdict(lambda: [0, 0])
        for k in rks:
            for v in rows_out[k]["needed"]:
                a = agg_need[v["filter_opt"]]
                a[0] += 1
                a[1] += v["score"]
            for s in rows_out[k]["suffocating"]:
                a = agg_suff[s["filter_opt"]]
                a[0] += 1
                a[1] += s["flags"]
        best_row = max(nd, key=lambda r: (r["pos_syms"], r["sum_pos_delta"]), default=None)
        sw_exits = None
        if ledger and sw in ledger["switch"]:
            sw_exits = {"n_ledger_syms": ledger["switch_syms"].get(sw, 0), "families": exit_rows(ledger["switch"][sw])}
        switches[sw] = {
            "tabs": sw_tabs[sw], "default": defaults.get(sw), "verdict": verd, "row_verdict_counts": dict(cnt),
            "n_rows": len(rks), "n_evals": sum(r["n_evals"] for r in nd), "n_valid": sum(r["n_valid"] for r in nd),
            "n_syms_max_row": max((r["n_syms"] for r in nd), default=0), "pos_syms_max_row": max((r["pos_syms"] for r in nd), default=0),
            "sum_pos_delta": round(sum(r["sum_pos_delta"] for r in nd), 3), "best_cand": best_row["cand"] if best_row and best_row["n_syms"] else None,
            "best_by_regime": best_reg,
            "needed_filters": [{"filter_opt": f, "rows": a[0], "score_sum": round(a[1], 3)} for f, a in sorted(agg_need.items(), key=lambda x: (-x[1][1], x[0]))[:10]],
            "suffocating_filters": [{"filter_opt": f, "rows": a[0], "flags": a[1]} for f, a in sorted(agg_suff.items(), key=lambda x: (-x[1][1], x[0]))[:10]],
            "forward": {"n": sum(r["forward"]["n"] for r in nd), "pos": sum(r["forward"]["pos"] for r in nd)},
            "live": {"n": sum(r["live"]["n"] for r in nd), "pos": sum(r["live"]["pos"] for r in nd), "pnl_pp": round(sum(r["live"]["pnl_pp"] or 0.0 for r in nd), 3)},
            "exits_cooccurrence": sw_exits,
            "coverage": {"pairs_total": sum(rows_out[k]["coverage"]["pairs_total"] for k in rks), "pairs_n_ge3": sum(rows_out[k]["coverage"]["pairs_n_ge3"] for k in rks),
                         "pairs_untried_n0": sum(rows_out[k]["coverage"]["pairs_untried_n0"] for k in rks)},
        }
    verdict_counts_switch = collections.Counter(s["verdict"] for s in switches.values())
    verdict_counts_rows = collections.Counter(r["verdict"] for r in rows_out.values() if r["verdict_sub"] != "DEFAULT_REFERENCE")
    profile = {
        "_meta": {"schema": SCHEMA, "cat_side": cat_side, "built_at": utc_now(), "brain_at": ev["meta"].get("at"), "brain_last_ts": ev["meta"].get("last_ts"),
                  "brain_counters": {k: ev["meta"]["counters"].get(k) for k in ("rows", "boards", "ledger_trades") if k in ev["meta"].get("counters", {})},
                  "template": tidx.get("path"), "template_md5": tidx.get("md5"), "thresholds": T, "threshold_notes": THRESHOLD_NOTES,
                  "needed_max": needed_max, "state_enrichment": bool(extra["rows"]), "state_entries_dropped_stale": extra.get("dropped", 0),
                  "ledger": {"ledgers_with_set": ledger["n_ledgers"], "trades": ledger["n_trades"]} if ledger else None,
                  "predicted_notes": pred_notes, "totals": totals,
                  "verdict_counts_switch": {v: verdict_counts_switch.get(v, 0) for v in VERDICTS},
                  "verdict_counts_rows_non_reference": {v: verdict_counts_rows.get(v, 0) for v in VERDICTS},
                  "not_derivable": NOT_DERIVABLE},
        "switches": switches, "rows": rows_out}
    return profile, pilot


NOT_DERIVABLE = {
    "neg_sym (sym_sides with negative delta)": "not derivable from the brain; would need per-sym_side delta signs (v15_delta_log eval lines grouped by sym_side)",
    "regime n (integer evals per regime)": "export carries the weighted n (wn) only; integer eval counts per regime need the brain state edge n at the R|G pair scope",
    "pair-level positive sym_sides without the state pickle": "proxy min(pos, n_sym) is used and flagged pos_syms_basis; exact value needs data/brain_scratch/v18_brain_state.pkl",
    "which exit family a switch CAUSES": "exit families are board-ledger co-occurrence (set carries switch=cand); causation needs paired ledger evals with the switch flipped (tools/v18_exit_loss_rank.py measure on S2)",
    "settings x regime where regime is not board-bh": "regime = board bh bucket only; v17_combo_engine.classify_regime needs NPZ bars the corpus does not hold",
}


def trim_to_size(profile, max_bytes=MAX_PROFILE_BYTES):
    """Size law: shrink per-row detail lists (8 -> 4 -> 2 -> 0) before anything else; the pilot list is separate and never trimmed."""
    txt = json.dumps(profile, separators=(",", ":"))
    for keep in (4, 2, 1, 0):
        if len(txt) <= max_bytes:
            break
        for r in profile["rows"].values():
            r["needed"] = r["needed"][:keep]
            r["suffocating"] = r["suffocating"][:keep]
        profile["_meta"]["trimmed_row_detail_to"] = keep
        txt = json.dumps(profile, separators=(",", ":"))
    return txt


# ------------------------------------------------------------------------------------------------ rendering

def fmt(v, nd=2):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def threshold_md(T=THRESH):
    L = ["## Verdict thresholds (reused from `tools/v15_switch_causal_profile.py classify()`, re-expressed on the brain counters)", "",
         f"- UNTRIED: n_sym < {T['min_sym']} (INSUFFICIENT); also 3 <= n_sym < {T['big_sym']} without WORKS/SOMETIMES (THIN); also every default (bold) row (reference, delta 0 by construction)",
         f"- WORKS: pos_sym >= {T['works_pos_sym']} and pos_sym/n_sym >= {T['works_rate']} and mean delta > 0 (when known)",
         f"- SOMETIMES: pos_sym >= {T['some_pos_sym']} and pos_sym/n_sym >= {T['some_rate']}",
         f"- DAMAGING: n_sym >= {T['big_sym']}, pos_sym == 0 and (negative evals >= {T['dmg_neg_evals']} or invalid evals >= {T['dmg_inv_evals']})",
         f"- INERT: n_sym >= {T['big_sym']} and none of the above (DEAD = no pos/neg/invalid; USELESS = pos_sym <= {T['inert_pos_sym_max']} and < {int(T['inert_rate'] * 100)}%; remainder NEUTRAL_FOLDED)",
         f"- NEEDED filter=opt: positive together with the row on >= {T['needed_min_pos_sym']} sym_sides, ranked pos_rate x n",
         f"- SUFFOCATING filter=opt: n_rescue + n_kill + abl_kill >= {T['suffocate_min_flags']}, or listed in the digest filter_veto (remedy attached)",
         "", "Deviations from the older logic:"]
    L += [f"- {n}" for n in THRESHOLD_NOTES]
    return L


def render_md(profile, pilot):
    m = profile["_meta"]
    cs = m["cat_side"]
    S, R = profile["switches"], profile["rows"]
    L = [f"# Switch profile {cs}", "", f"built {m['built_at']} from brain export {m['brain_at']} (last event {m['brain_last_ts']}); template md5 {m['template_md5']}; "
         f"state enrichment: {m['state_enrichment']}; every number is read from evidence files, none computed from re-implemented logic.", ""]
    L += threshold_md(m["thresholds"]) + [""]
    t = m["totals"]
    L += ["## Counts", "", f"- switches (template): {len(S)}; rows (unique switch=cand): {t['rows']}",
          "- switch-level verdicts: " + ", ".join(f"{v} {m['verdict_counts_switch'][v]}" for v in VERDICTS),
          "- row-level verdicts (excl. reference default rows): " + ", ".join(f"{v} {m['verdict_counts_rows_non_reference'][v]}" for v in VERDICTS),
          f"- yellow pairs (unique row x tab-yellow union): {t['pairs_total']}; with n >= 3: {t['pairs_n_ge3']}; never evaluated (n = 0): {t['pairs_untried']}",
          f"- pilot-facing lists (needed + suffocating + evidence_pos, deduped, <= {m['needed_max']}): {t['pilot_listed']} pairs over {t['rows_with_list']} rows", ""]
    if m.get("predicted_notes"):
        L += ["PREDICTED (separate from measured): " + "; ".join(m["predicted_notes"]), ""]
    L += ["## Switches", "", "| switch | tab | verdict | rows W/S/D/I/U | n_evals | max n_syms | max pos_syms | Σ+delta | best cand | needed (top 3) | suffocated by (top 2) | exits (loss share) | live n/pos | untried pairs |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    order = sorted(S, key=lambda s: (worst_to_best_rank(S[s]["verdict"]), -S[s]["pos_syms_max_row"], s))
    for sw in order:
        s = S[sw]
        c = s["row_verdict_counts"]
        rv = "/".join(str(c.get(v, 0)) for v in ("WORKS", "SOMETIMES", "DAMAGING", "INERT", "UNTRIED"))
        nd = ", ".join(f"{x['filter_opt']} ({x['rows']})" for x in s["needed_filters"][:3]) or "-"
        sf = ", ".join(f"{x['filter_opt']} ({x['rows']})" for x in s["suffocating_filters"][:2]) or "-"
        ex = s["exits_cooccurrence"]
        exs = ", ".join(f"{e['exit_family']} {fmt(e['loss_share'], 2) if e['loss_share'] is not None else '-'}" for e in (ex["families"][:2] if ex else [])) or "-"
        L.append(f"| {sw} | {','.join(s['tabs'])} | {s['verdict']} | {rv} | {s['n_evals']} | {s['n_syms_max_row']} | {s['pos_syms_max_row']} | {fmt(s['sum_pos_delta'])} | "
                 f"{s['best_cand'] or '-'} | {nd} | {sf} | {exs} | {s['live']['n']}/{s['live']['pos']} | {s['coverage']['pairs_untried_n0']} |")
    L += ["", "## Not derivable from the evidence", ""] + [f"- {k}: {v}" for k, v in m["not_derivable"].items()]
    return "\n".join(L) + "\n"


def write_json(path, obj, txt=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(txt if txt is not None else json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)


def render_fleet_doc(profiles, pilots, stamp):
    L = [f"# 19 - Switch profiles ({stamp}): the encyclopedia per cat_side", "",
         "Generated by `tools/v18_switch_profile.py` from the brain exports, the learning-layer digests and the replayed board ledgers. Four cat_sides are four "
         "separate profiles; no number below is pooled across them. Production is unchanged: the pilot-facing lists (`data/learn/needed_filters_<CS>.json`) are "
         "read only by a gated pilot (`V18_NEEDED_MAX`, default 24).", ""]
    L += threshold_md() + [""]
    L += ["## Verdict counts per cat_side", "", "| cat_side | level | " + " | ".join(VERDICTS) + " |", "|---|---|" + "---|" * len(VERDICTS)]
    for cs, p in profiles.items():
        m = p["_meta"]
        L.append(f"| {cs} | switches ({len(p['switches'])}) | " + " | ".join(str(m["verdict_counts_switch"][v]) for v in VERDICTS) + " |")
        L.append(f"| {cs} | rows (excl. reference defaults) | " + " | ".join(str(m["verdict_counts_rows_non_reference"][v]) for v in VERDICTS) + " |")
    L += ["", "## Structural eval reduction (needed-filter lists vs the full yellow box)", "",
          "A row's full box is every yellow of its tab(s) (about 190-217). The pilot-facing list is the evidence-backed subset; the pilot still draws its own floor "
          "sample from the rest, so this is the evidence-ranked part, not a hard cut. `reduction` = 1 - listed/total over all rows; `rows with list` are rows that have any "
          "evidence-backed filter, the others get only the floor sample.", "",
          "In-sample capture (the lists are chosen from the same evidence, so this is a ceiling, not a forecast; the brain's prequential capture is in doc 19 E8 section 5): "
          "`pos captured` = positive pair evals inside the lists / all positive pair evals of the template pairs; `evals share` = pair evals inside the lists / all pair evals.", "",
          "| cat_side | rows | yellow pairs total | listed (needed+suffocating+evidence_pos) | reduction | mean listed / row | rows with a list | pairs n>=3 | pairs never evaluated | pos captured | evals share |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for cs, p in profiles.items():
        t = p["_meta"]["totals"]
        red = 1 - t["pilot_listed"] / t["pairs_total"] if t["pairs_total"] else 0
        L.append(f"| {cs} | {t['rows']} | {t['pairs_total']} | {t['pilot_listed']} | {red * 100:.1f}% | {t['pilot_listed'] / max(1, t['rows']):.2f} | {t['rows_with_list']} | {t['pairs_n_ge3']} | {t['pairs_untried']} | "
                 f"{t['pair_pos_listed']}/{t['pair_pos_all']} ({100 * t['pair_pos_listed'] / max(1, t['pair_pos_all']):.1f}%) | {t['pair_evals_listed']}/{t['pair_evals_all']} ({100 * t['pair_evals_listed'] / max(1, t['pair_evals_all']):.1f}%) |")
    for cs, p in profiles.items():
        S, R = p["switches"], p["rows"]
        L += ["", f"## {cs}", ""]
        for vname, top_n in (("WORKS", 30), ("SOMETIMES", 30)):
            ranked = sorted((s for s in S if S[s]["verdict"] == vname), key=lambda s: (-S[s]["pos_syms_max_row"], -S[s]["sum_pos_delta"], s))[:top_n]
            L += [f"### Top-{top_n} {vname} switches (of {p['_meta']['verdict_counts_switch'][vname]}) with their needed filters", "",
                  "| switch | best cand | max pos_syms / n_syms | n_evals | Σ+delta | needed filters (rows where needed) |", "|---|---|---|---|---|---|"]
            for sw in ranked:
                s = S[sw]
                br = R.get(f"{sw}={s['best_cand']}") if s["best_cand"] else None
                if s["needed_filters"]:
                    nd = "; ".join(f"{x['filter_opt']} ({x['rows']})" for x in s["needed_filters"][:4])
                elif s["coverage"]["pairs_total"] == 0:
                    nd = "n/a (its tab has no yellow filters)"
                else:
                    nd = "none positive on >= 2 sym_sides"
                L.append(f"| {sw} | {s['best_cand']} | {s['pos_syms_max_row']} / {br['n_syms'] if br else '-'} | {s['n_evals']} | {fmt(s['sum_pos_delta'])} | {nd} |")
            L.append("")
        dmg = sorted((s for s in S if S[s]["verdict"] == "DAMAGING"), key=lambda s: (-S[s]["n_evals"], s))[:30]
        L += ["", f"### Top-30 DAMAGING switches (of {p['_meta']['verdict_counts_switch']['DAMAGING']}; ranked by n_evals)", "",
              "| switch | n_evals | max n_syms | rows W/S/D/I/U | live n/pos/pnl_pp |", "|---|---|---|---|---|"]
        for sw in dmg:
            s = S[sw]
            c = s["row_verdict_counts"]
            L.append(f"| {sw} | {s['n_evals']} | {s['n_syms_max_row']} | " + "/".join(str(c.get(v, 0)) for v in ("WORKS", "SOMETIMES", "DAMAGING", "INERT", "UNTRIED")) +
                     f" | {s['live']['n']}/{s['live']['pos']}/{fmt(s['live']['pnl_pp'])} |")
        suf = sorted(S, key=lambda s: -sum(x["flags"] for x in S[s]["suffocating_filters"]))
        suf = [s for s in suf if S[s]["suffocating_filters"]][:15]
        L += ["", "### Most suffocated switches (Σ flags = rescue + kill + ablation + neg-synergy over the top-8 per row) and by what", "",
              "| switch | verdict | Σ flags | suffocated by (rows) |", "|---|---|---|---|"]
        for sw in suf:
            s = S[sw]
            L.append(f"| {sw} | {s['verdict']} | {sum(x['flags'] for x in s['suffocating_filters'])} | " + "; ".join(f"{x['filter_opt']} ({x['rows']})" for x in s["suffocating_filters"][:4]) + " |")
        un = [s for s in S if S[s]["verdict"] == "UNTRIED"]
        never = [s for s in un if S[s]["n_evals"] == 0]
        L += ["", f"### UNTRIED", "",
              f"- switches whose every row is UNTRIED: {len(un)} (of which with zero evals on any row: {len(never)})",
              f"- rows UNTRIED (excl. reference defaults): {p['_meta']['verdict_counts_rows_non_reference']['UNTRIED']} of {p['_meta']['totals']['rows']}",
              "- zero-eval switches (first 40): " + (", ".join(sorted(never)[:40]) or "none")]
    L += ["", "## Not derivable", ""] + [f"- {k}: {v}" for k, v in next(iter(profiles.values()))["_meta"]["not_derivable"].items()]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------------ CLI

def run(cat_side, args):
    from v18_learn_common import template_index
    brain_p = Path(args.brain_dir) / f"v18_brain_{cat_side}.json"
    blob = json.loads(brain_p.read_text())
    if blob["_meta"].get("cat_side") != cat_side:
        raise SystemExit(f"{brain_p} is cat_side {blob['_meta'].get('cat_side')}, expected {cat_side}")
    tidx = template_index(cat_side)
    dp = Path(args.learn_dir) / f"digest_{cat_side}.json"
    digest = json.loads(dp.read_text()) if dp.exists() else None
    if digest and digest.get("meta", {}).get("cat_side") not in (None, cat_side):
        raise SystemExit("digest cat_side mismatch")
    extra = None
    sp = Path(args.state) if args.state else None
    if sp and sp.exists():
        extra = load_state_extra(sp, cat_side, load_brain_evidence(blob))
    ledger = ledger_exit_tables(cat_side, tidx, args.learn_dir)
    predicted = load_predicted(args.learn_dir, cat_side, digest)
    profile, pilot = build_profile(cat_side, blob, tidx, digest, extra, ledger, predicted, args.needed_max)
    txt = trim_to_size(profile)
    out = Path(args.out_dir)
    write_json(out / f"switch_profile_{cat_side}.json", None, txt)
    (out / f"switch_profile_{cat_side}.md").write_text(render_md(profile, pilot))
    write_json(out / f"needed_filters_{cat_side}.json", pilot)
    print(f"{cat_side}: {len(profile['switches'])} switches, {len(profile['rows'])} rows, {len(txt)} bytes, verdicts {profile['_meta']['verdict_counts_switch']}")
    return profile, pilot


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    ap.add_argument("--brain-dir", default=str(ROOT / "data" / "brain"))
    ap.add_argument("--learn-dir", default=str(ROOT / "data" / "learn"))
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "learn"))
    ap.add_argument("--state", default=str(ROOT / "data" / "brain_scratch" / "v18_brain_state.pkl"))
    ap.add_argument("--needed-max", type=int, default=int(os.environ.get("V18_NEEDED_MAX", "24")))
    ap.add_argument("--doc", action="store_true", help="also write the fleet summary doc")
    ap.add_argument("--doc-path", default=str(ROOT / "docs" / "v15_pending" / "19_SWITCH_PROFILES_20261020.md"))
    ap.add_argument("--stamp", default="2026-10-20")
    args = ap.parse_args(argv)
    profiles, pilots = {}, {}
    for cs in args.cat_side or CAT_SIDES:
        profiles[cs], pilots[cs] = run(cs, args)
    if args.doc:
        Path(args.doc_path).write_text(render_fleet_doc(profiles, pilots, args.stamp))
        print("wrote", args.doc_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
