#!/usr/bin/env python3
"""v18_winners_pack — B2 of doc 17 §3.5: the per-cat_side WINNERS PACK the pilot tests on every sym_side in rounds 1 and 2.

EVIDENCE. data/learn/evidence/evidence_<CAT_SIDE>.json.gz (tools/v18_evidence_mine.py over the hot delta logs on S2 + the S1
copy, last 14 days; unit = (key, sym_side), last measurement wins). With --store DIR the V18 experience store
(tools/v18_experience.py shards) is read INSTEAD (never both: both are built from the same logs, so using both would double
count).

SCORE (v15_brain law: mean delta is anti-predictive, never used):
  P(pos)     = (pos_syms + M * p0) / (valid_syms + M)   Beta posterior shrunk to the cat_side's own base rate p0 = sum(pos) /
               sum(valid) over the cat_side's eligible keys, M = V15_BRAIN_M (8). No GLOBAL scope (principle 13).
  E[d | pos] = sum of the positive last-measured deltas / pos_syms (pct-points of board gain vs the chain)
  forward    = share of the key's FORWARD/HOLDOUT outcomes with delta >= 0 (FORWARD key match), or hermetic live outcomes
               attributable through a /history `prov` block (switch=cand); unknown = 0.5, never a bonus
  score      = P(pos) * E[d | pos] * forward
ELIGIBLE: n_syms >= 5 distinct sym_sides with a valid measurement in the window; the switch AND the candidate are rows of the
cat_side's TEMPLATE; not ABLATION_* / MODE / VENUE / IS_TRADIER / SIMPLE_PRICE_GT0_ENABLED / GLOBAL_ONLY_KEYS / MAXAUG sentinel;
accepted by tools/switch_types.coerce; tokenized stocks never reach the evidence (the miner skips their logs).
COMPANIONS: the yellow cells of that row that were positive WITH the switch (pair delta > 0 and above the naked delta) on the
most sym_sides; top 3.
XPOLL (principle 13): up to V18_XPOLL_K (5) of the OTHER cat_sides' top winners whose switch=cand is also a template row here,
round-robin over the source cat_sides, labelled from_cat_side, with the score they earned THERE (never re-scored, never averaged).

usage: python3 tools/v18_winners_pack.py [--cat-side CS] [--top 60] [--apply]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v18_learn_common as C  # noqa: E402

MIN_SYMS = 5
TOP = 60
N_COMP = 3


def load_evidence(cat_side, ev_dir):
    return C.read_json(Path(ev_dir) / f"evidence_{cat_side}.json.gz", None)


def evidence_from_store(cat_side, store_dir, since_epoch):
    """Same shape as the miner's evidence, from the V18 store's evals (provenance log/rebuilt/native, main-pass labels)."""
    naked, pairs, last = {}, {}, {}
    for p in sorted(Path(store_dir).glob(f"experience_{cat_side}_*.db")):
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        q = ("SELECT switch, cand, filter_key, filter_val, sym_side, ts, gain, cum_before, valid, label FROM evals "
             "WHERE switch IS NOT NULL AND cum_before IS NOT NULL AND ts >= ? AND (label = 'naked' OR filter_key IS NOT NULL)")
        for sw, cand, fk, fv, ss, ts, gain, cb, valid, label in con.execute(q, (since_epoch,)):
            if gain is None:
                continue
            key = f"{sw}={cand}" + (f"|{fk}={fv}" if fk else "")
            prev = last.get((key, ss))
            if prev is None or ts >= prev[0]:
                last[(key, ss)] = (ts, gain - cb, 1 if valid else 0)
        con.close()
    for (key, ss), (_ts, d, v) in last.items():
        tgt = pairs if "|" in key else naked
        e = tgt.setdefault(key, [0, 0, 0, 0, 0.0, 0, 0, 0, None, 0] if tgt is naked else [0, 0, 0, 0.0, 0, 0.0, 0, 0, 0])
        e[0] += 1
        e[1] += v
        if v and d > C.EPS:
            e[2] += 1
            if tgt is naked:
                e[4] += d
            else:
                e[3] += d
    return {"meta": {"source": f"store:{store_dir}", "note": "store evals; pair companion marginals need the row's naked -> not derived here"},
            "naked": naked, "pairs": pairs, "forward": {}}


def live_forward(cat_side):
    """Hermetic /history outcomes attributable to a switch=cand through a `prov` block -> {key: (n, n_ok)}.
    Until the prov block ships (work item E1) nothing is attributable; reason strings name an exit family, not a switch=cand."""
    from tools.v18_exit_loss_rank import live_lifecycles
    rows, _c = live_lifecycles(cat_side, ROOT, None)
    out = defaultdict(lambda: [0, 0])
    for r in rows:
        for prov in (r.get("prov"),):
            if isinstance(prov, dict) and prov.get("switch"):
                k = f"{prov['switch']}={prov.get('cand')}"
                out[k][0] += 1
                out[k][1] += 1 if (r.get("pnl_pp") or 0.0) >= 0 else 0
    return {k: tuple(v) for k, v in out.items()}


def forward_table(ev, live):
    fwd = defaultdict(lambda: [0, 0])
    for k, (n, ok) in (ev.get("forward") or {}).items():
        kind, _, key = k.partition("|")
        if kind == "FORWARD":
            fwd[key][0] += n
            fwd[key][1] += ok
    for k, (n, ok) in (live or {}).items():
        fwd[k][0] += n
        fwd[k][1] += ok
    return fwd


def eligible(cat_side, sw, cand, tidx):
    why = C.excluded_key_reason(sw, cand)
    if why:
        return why
    if sw not in tidx["switches"]:
        return "switch_not_in_template"
    if cand not in tidx["switches"][sw]:
        return "cand_not_in_template"
    return C.type_refused(sw, cand, cat_side)


def companions_index(ev):
    by = defaultdict(list)
    for k, v in (ev.get("pairs") or {}).items():
        swc, _, filt = k.partition("|")
        if len(v) > 4 and v[4] > 0:
            f, _, opt = filt.partition("=")
            by[swc].append({"filter": f, "opt": opt, "support_syms": v[4], "sum_marginal": v[5]})
    for lst in by.values():
        lst.sort(key=lambda c: (-c["support_syms"], -c["sum_marginal"]))
    return by


def build_pack(cat_side, ev, tidx, fwd, top=TOP, min_syms=MIN_SYMS):
    refused = defaultdict(int)
    cand_rows = []
    for key, v in (ev.get("naked") or {}).items():
        sw, _, cand = key.partition("=")
        n_syms, valid, pos, sum_pos = v[0], v[1], v[2], v[4]
        n_boards = v[9] if len(v) > 9 and v[9] is not None else n_syms
        why = eligible(cat_side, sw, cand, tidx)
        if why:
            refused[why.split(":")[0][:60]] += 1
            continue
        cand_rows.append((sw, cand, n_syms, valid, pos, sum_pos, n_boards))
    p0 = (sum(r[4] for r in cand_rows) / sum(r[3] for r in cand_rows)) if any(r[3] for r in cand_rows) else 0.0
    comps = companions_index(ev)
    singles = []
    for sw, cand, n_syms, valid, pos, sum_pos, n_boards in cand_rows:
        if valid < min_syms:
            refused["n_syms_lt_min"] += 1
            continue
        p = C.p_pos_shrunk(pos, valid, p0)
        e = (sum_pos / pos) if pos else 0.0
        f = fwd.get(f"{sw}={cand}", (0, 0))
        ff = C.forward_factor(*f)
        score = p * e * ff
        singles.append({"switch": sw, "cand": cand, "companions": comps.get(f"{sw}={cand}", [])[:N_COMP], "p_pos": round(p, 6),
                        "e_delta_pos": round(e, 6), "n_boards": n_boards, "n_syms": valid, "pos_syms": pos,
                        "forward_factor": ff, "n_forward": f[0], "score": round(score, 6)})
    singles.sort(key=lambda r: -r["score"])
    winners = [r for r in singles if r["score"] > 0][:top]
    pairs = []
    pp0_n = pp0_pos = 0
    prow = []
    for key, v in (ev.get("pairs") or {}).items():
        swc, _, filt = key.partition("|")
        sw, _, cand = swc.partition("=")
        f, _, opt = filt.partition("=")
        if eligible(cat_side, sw, cand, tidx) or C.excluded_key_reason(f, opt):
            continue
        pp0_n += v[1]
        pp0_pos += v[2]
        if v[1] >= min_syms:
            prow.append((sw, cand, f, opt, v))
    pp0 = pp0_pos / pp0_n if pp0_n else 0.0
    for sw, cand, f, opt, v in prow:
        p = C.p_pos_shrunk(v[2], v[1], pp0)
        e = (v[3] / v[2]) if v[2] else 0.0
        fw = fwd.get(f"{sw}={cand}|{f}={opt}", (0, 0))
        ff = C.forward_factor(*fw)
        s = p * e * ff
        if s > 0:
            pairs.append({"switch": sw, "cand": cand, "filter": f, "opt": opt, "p_pos": round(p, 6), "e_delta_pos": round(e, 6),
                          "n_boards": v[8] if len(v) > 8 else v[0], "n_syms": v[1], "pos_syms": v[2], "forward_factor": ff, "score": round(s, 6)})
    pairs.sort(key=lambda r: -r["score"])
    stats = {"eligible_keys": len(cand_rows), "scored_keys": len(singles), "positive_score_keys": sum(1 for r in singles if r["score"] > 0),
             "p0_naked": round(p0, 6), "p0_pairs": round(pp0, 6), "pairs_scored": len(prow), "pairs_positive": len(pairs),
             "refused": dict(refused), "forward_keys_known": sum(1 for r in singles if r["n_forward"])}
    return winners, pairs[:top], stats


def xpoll_for(cat_side, packs, tidxs, k=C.XPOLL_K):
    """Round-robin over the other cat_sides' ranked winners; keep a switch=cand only if it is a template row here and not
    already in this cat_side's own pack."""
    own = {(w["switch"], w["cand"]) for w in packs.get(cat_side, [])}
    queues = {cs: [w for w in packs.get(cs, []) if (w["switch"], w["cand"]) not in own
                   and w["cand"] in (tidxs[cat_side]["switches"].get(w["switch"]) or [])
                   and not C.type_refused(w["switch"], w["cand"], cat_side)] for cs in C.CAT_SIDES if cs != cat_side}
    out, seen = [], set()
    while len(out) < k and any(queues.values()):
        for cs in sorted(queues):
            while queues[cs]:
                w = queues[cs].pop(0)
                if (w["switch"], w["cand"]) in seen:
                    continue
                seen.add((w["switch"], w["cand"]))
                out.append({"from_cat_side": cs, "switch": w["switch"], "cand": w["cand"], "score": w["score"], "label": f"XPOLL:{cs}"})
                break
            if len(out) >= k:
                break
    return out


def render_md(cs, doc):
    m = doc["meta"]
    L = [f"# Winners pack — {cs} — {m['built_at']}", "",
         f"Evidence: {m['evidence_source']} · window since {m.get('since')} · eligible keys {m['stats']['eligible_keys']} · "
         f"scored (n_syms >= {MIN_SYMS}) {m['stats']['scored_keys']} · positive {m['stats']['positive_score_keys']} · p0 {m['stats']['p0_naked']}.",
         "score = P(pos) (Beta-shrunk to the cat_side base rate) x E[delta | pos] x forward_factor (0.5 = unknown; "
         f"{m['stats']['forward_keys_known']} keys have forward evidence). In-sample board deltas, not money.", "",
         C.md_table(["#", "switch=cand", "p_pos", "E[d|pos]", "n_syms", "pos", "ff", "score", "companions"],
                    [[i + 1, f"`{w['switch']}={w['cand']}`", w["p_pos"], w["e_delta_pos"], w["n_syms"], w["pos_syms"], w["forward_factor"],
                      w["score"], ", ".join(f"{c['filter']}={c['opt']} ({c['support_syms']})" for c in w["companions"])]
                     for i, w in enumerate(doc["winners"][:30])]), "",
         "## top pair cells (switch=cand + filter=opt)", "",
         C.md_table(["switch=cand", "filter=opt", "p_pos", "E[d|pos]", "n_syms", "pos", "score"],
                    [[f"`{p['switch']}={p['cand']}`", f"`{p['filter']}={p['opt']}`", p["p_pos"], p["e_delta_pos"], p["n_syms"], p["pos_syms"], p["score"]]
                     for p in doc["pairs"][:20]]), "",
         "## xpoll (labelled cross-pollination candidates)", ""]
    L += [f"- `{x['switch']}={x['cand']}` from {x['from_cat_side']} (score there {x['score']})" for x in doc["xpoll"]] or ["- none"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cat-side", default="")
    ap.add_argument("--evidence", default=str(C.LEARN_DIR / "evidence"))
    ap.add_argument("--store", default="")
    ap.add_argument("--top", type=int, default=TOP)
    ap.add_argument("--min-syms", type=int, default=MIN_SYMS)
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    packs, docs, tidxs = {}, {}, {}
    import time as _t
    since_epoch = _t.time() - C.WINDOW_DAYS * 86400
    for cs in C.CAT_SIDES:
        tidxs[cs] = C.template_index(cs)
        ev = evidence_from_store(cs, a.store, since_epoch) if a.store else load_evidence(cs, a.evidence)
        if not ev:
            print(f"[winners] {cs}: no evidence", flush=True)
            packs[cs] = []
            continue
        live = {} if a.no_live else live_forward(cs)
        fwd = forward_table(ev, live)
        winners, pairs, stats = build_pack(cs, ev, tidxs[cs], fwd, a.top, a.min_syms)
        packs[cs] = winners
        docs[cs] = {"meta": {"cat_side": cs, "built_at": C.utc(), "tool": "tools/v18_winners_pack.py",
                             "evidence_source": (ev.get("meta") or {}).get("tool") or (ev.get("meta") or {}).get("source"),
                             "since": (ev.get("meta") or {}).get("since"), "evidence_built_at": (ev.get("meta") or {}).get("built_at"),
                             "n_board_ctx": (ev.get("meta") or {}).get("n_board_ctx"), "n_sym_sides_evidence": (ev.get("meta") or {}).get("n_sym_sides_evidence"),
                             "min_syms": a.min_syms, "m_shrink": C.M_SHRINK, "stats": stats, "template_md5": tidxs[cs]["md5"],
                             "live_prov_attributable": sum(n for n, _ok in live.values())},
                    "winners": winners, "pairs": pairs}
    for cs, doc in docs.items():
        doc["xpoll"] = xpoll_for(cs, packs, tidxs)
        if a.apply:
            C.write_json(C.LEARN_DIR / f"winners_{cs}.json", doc)
            (C.LEARN_DIR / f"winners_{cs}.md").write_text(render_md(cs, doc))
        print(f"[winners] {cs}: winners {len(doc['winners'])} pairs {len(doc['pairs'])} xpoll {len(doc['xpoll'])} stats {json.dumps(doc['meta']['stats'])}", flush=True)
        if not a.apply:
            print(render_md(cs, doc)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
