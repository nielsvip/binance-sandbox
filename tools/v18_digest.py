#!/usr/bin/env python3
"""v18_digest — assemble data/learn/digest_<CAT_SIDE>.json, the ONLY learning artefact the pilot reads (doc 17 §3.1, §3.6).

CONTRACT v18_digest_v1 (the pilot agent codes against it; keys are EXACTLY these, nothing else at the top level):
  "meta"           {cat_side, built_at, era_cut, n_boards, n_sym_sides, sources}
  "exit_loss_rank" [{family, column, n, losers, loss_sum_pct, one_bar_share, mean_bars_held, giveback_sum, premature_sum,
                     drift_into_loss_sum}]                              columns board / live_hermetic / stop_execution, never summed
  "repair_menu"    {family: [{switch, cand, companions: [{filter, opt}], source, support_n, support_syms, forward_factor}]}
                   source = curated | token | learned, suffixed ':pending_evidence' / ':measured_not_supported' /
                   ':measured_supported' with the paired-eval verdict of tools/v18_exit_loss_rank.py measure
  "winners"        [{switch, cand, companions: [{filter, opt}], p_pos, e_delta_pos, n_boards, n_syms, forward_factor, score}]
                   sorted by score desc
  "filter_veto"    [{switch, cand, filter, opt, vetoed_in_missed_moves, vetoed_losers, support_syms, remedy: {kind, override}}]
                   vetoed_* are null where no ledger measurement exists (never a guessed count)
  "coverage"       {pairs_total, pairs_n_lt_3, pairs_at_floor, floor_per_1000}
                   pairs = every distinct (switch=cand, filter=opt) cell of the cat_side's TEMPLATE; n = distinct sym_sides with
                   a logged eval of that cell in the evidence window; at_floor = n >= 3 and never positive (the brain
                   allocator's floor rate applies; principle 12: never zero, never closed); floor_per_1000 = V18_FLOOR_PER_1000 (5)
  "xpoll"          [{from_cat_side, switch, cand, score}]               <= V18_XPOLL_K (5), score earned in from_cat_side
Each file <= 10 MB (refused otherwise). One file per cat_side; nothing is averaged across cat_sides.

usage: python3 tools/v18_digest.py [--apply] [--out-dir data/learn]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v18_learn_common as C  # noqa: E402

CONTRACT = "v18_digest_v1"
MAX_BYTES = 10 * 1024 * 1024
TOP_KEYS = ("meta", "exit_loss_rank", "repair_menu", "winners", "filter_veto", "coverage", "xpoll")
META_KEYS = ("cat_side", "built_at", "era_cut", "n_boards", "n_sym_sides", "sources")
RANK_KEYS = ("family", "column", "n", "losers", "loss_sum_pct", "one_bar_share", "mean_bars_held", "giveback_sum", "premature_sum",
             "drift_into_loss_sum")
MENU_KEYS = ("switch", "cand", "companions", "source", "support_n", "support_syms", "forward_factor")
WIN_KEYS = ("switch", "cand", "companions", "p_pos", "e_delta_pos", "n_boards", "n_syms", "forward_factor", "score")
VETO_KEYS = ("switch", "cand", "filter", "opt", "vetoed_in_missed_moves", "vetoed_losers", "support_syms", "remedy")
COV_KEYS = ("pairs_total", "pairs_n_lt_3", "pairs_at_floor", "floor_per_1000")
XPOLL_KEYS = ("from_cat_side", "switch", "cand", "score")
RANK_TOP = 40


def _comp(lst):
    return [{"filter": str(c["filter"]), "opt": str(c["opt"])} for c in (lst or [])]


def coverage(cat_side, tidx, ev, floor=None):
    """Template cells vs evidence. Returns (contract dict, detail dict)."""
    floor = C.FLOOR_PER_1000 if floor is None else floor
    pairs = (ev or {}).get("pairs") or {}
    cells = set()
    per_tab = {}
    for tab, sw, cand, y in C.template_pairs(tidx):
        k = f"{sw}={cand}|{y}"
        if k in cells:
            continue
        cells.add(k)
        t = per_tab.setdefault(tab, Counter())
        v = pairs.get(k)
        n = v[0] if v else 0
        pos = v[2] if v else 0
        t["pairs"] += 1
        t["n_lt_3"] += 1 if n < 3 else 0
        t["at_floor"] += 1 if (n >= 3 and pos == 0) else 0
        t["n_ge_1"] += 1 if n >= 1 else 0
    tot = Counter()
    for t in per_tab.values():
        tot.update(t)
    out = {"pairs_total": tot["pairs"], "pairs_n_lt_3": tot["n_lt_3"], "pairs_at_floor": tot["at_floor"], "floor_per_1000": floor}
    detail = {"per_tab": {k: dict(v) for k, v in per_tab.items()}, "pairs_evaluated_ge_1": tot["n_ge_1"],
              "evidence_pairs_not_in_template": sum(1 for k in pairs if k not in cells),
              "definition": "n = distinct sym_sides with a logged eval of the cell in the evidence window; at_floor = n >= 3 and pos_syms == 0"}
    return out, detail


def assemble(cat_side, learn_dir=None):
    d = Path(learn_dir or C.LEARN_DIR)
    tidx = C.template_index(cat_side)
    ev = C.canonicalize_evidence(C.read_json(d / "evidence" / f"evidence_{cat_side}.json.gz", {}) or {}, tidx)
    rank = C.read_json(d / f"exit_loss_rank_{cat_side}.json", {}) or {}
    menu = C.read_json(d / f"exit_repair_menu_{cat_side}.json", {}) or {}
    win = C.read_json(d / f"winners_{cat_side}.json", {}) or {}
    veto = C.read_json(d / f"filter_veto_{cat_side}.json", {}) or {}
    rows = []
    for col in ("board", "live_hermetic", "stop_execution"):
        for r in ((rank.get("columns") or {}).get(col) or [])[:RANK_TOP]:
            rows.append({"family": r["family"], "column": col, "n": r["n"], "losers": r["losers"], "loss_sum_pct": r["loss_sum_pct"],
                         "one_bar_share": r["one_bar_share"], "mean_bars_held": r["mean_bars_held"], "giveback_sum": r["giveback_sum"],
                         "premature_sum": r["premature_sum"], "drift_into_loss_sum": r["drift_into_loss_sum"]})
    rmenu = {}
    for fam, paths in (menu.get("menu") or {}).items():
        rmenu[fam] = [{"switch": p["switch"], "cand": str(p["cand"]), "companions": _comp(p.get("companions")),
                       "source": p["source"] + (f":{p['learned_status']}" if p.get("learned_status") else ""),
                       "support_n": p.get("support_n"), "support_syms": p.get("support_syms"), "forward_factor": p.get("forward_factor", 0.5)}
                      for p in paths]
    winners = sorted([{"switch": w["switch"], "cand": str(w["cand"]), "companions": _comp(w.get("companions")), "p_pos": w["p_pos"],
                       "e_delta_pos": w["e_delta_pos"], "n_boards": w["n_boards"], "n_syms": w["n_syms"], "forward_factor": w["forward_factor"],
                       "score": w["score"]} for w in (win.get("winners") or [])], key=lambda w: -w["score"])
    fveto = [{"switch": x["switch"], "cand": str(x["cand"]), "filter": x["filter"], "opt": str(x["opt"]),
              "vetoed_in_missed_moves": x["vetoed_in_missed_moves"], "vetoed_losers": x["vetoed_losers"], "support_syms": x["support_syms"],
              "remedy": {"kind": (x.get("remedy") or {}).get("kind"), "override": (x.get("remedy") or {}).get("override")}}
             for x in (veto.get("pairs") or [])]
    cov, cov_detail = coverage(cat_side, tidx, ev)
    xpoll = [{"from_cat_side": x["from_cat_side"], "switch": x["switch"], "cand": str(x["cand"]), "score": x["score"]}
             for x in (win.get("xpoll") or [])][: C.XPOLL_K]
    em = ev.get("meta") or {}
    sources = {"contract": CONTRACT, "evidence": {"file": f"data/learn/evidence/evidence_{cat_side}.json.gz", "built_at": em.get("built_at"),
                                                  "since": em.get("since"), "host": em.get("host"), "files": em.get("files")},
               "exit_loss_rank": {"file": f"data/learn/exit_loss_rank_{cat_side}.json", "built_at": (rank.get("meta") or {}).get("built_at"),
                                  "board_ledgers": (rank.get("meta") or {}).get("board_ledgers")},
               "repair_menu": {"file": f"data/learn/exit_repair_menu_{cat_side}.json", "built_at": (menu.get("meta") or {}).get("built_at")},
               "winners": {"file": f"data/learn/winners_{cat_side}.json", "built_at": (win.get("meta") or {}).get("built_at")},
               "filter_veto": {"file": f"data/learn/filter_veto_{cat_side}.json", "built_at": (veto.get("meta") or {}).get("built_at")},
               "template": {"path": tidx["path"], "md5": tidx["md5"]}, "coverage_detail": cov_detail}
    meta = {"cat_side": cat_side, "built_at": C.utc(), "era_cut": C.ERA_CUT_ISO[C.venue_of(cat_side)],
            "n_boards": em.get("n_board_ctx"), "n_sym_sides": em.get("n_sym_sides_evidence"), "sources": sources}
    return {"meta": meta, "exit_loss_rank": rows, "repair_menu": rmenu, "winners": winners, "filter_veto": fveto, "coverage": cov, "xpoll": xpoll}


def validate(doc):
    """Contract check: exact key sets, types, sort order, size. Returns a list of problems (empty = valid)."""
    bad = []
    if tuple(doc.keys()) != TOP_KEYS:
        bad.append(f"top keys {list(doc.keys())} != {list(TOP_KEYS)}")
    m = doc.get("meta") or {}
    if set(m) != set(META_KEYS):
        bad.append(f"meta keys {sorted(m)}")
    if m.get("cat_side") not in C.CAT_SIDES:
        bad.append("meta.cat_side")
    for r in doc.get("exit_loss_rank") or []:
        if set(r) != set(RANK_KEYS):
            bad.append(f"exit_loss_rank keys {sorted(r)}")
            break
        if r["column"] not in ("board", "live_hermetic", "stop_execution"):
            bad.append(f"column {r['column']}")
            break
    if not isinstance(doc.get("repair_menu"), dict):
        bad.append("repair_menu not a dict")
    for fam, paths in (doc.get("repair_menu") or {}).items():
        for p in paths:
            if set(p) != set(MENU_KEYS) or any(set(c) != {"filter", "opt"} for c in p["companions"]):
                bad.append(f"repair_menu[{fam}] keys {sorted(p)}")
                break
    ws = doc.get("winners") or []
    for w in ws:
        if set(w) != set(WIN_KEYS) or any(set(c) != {"filter", "opt"} for c in w["companions"]):
            bad.append(f"winners keys {sorted(w)}")
            break
        if not (0.0 <= w["forward_factor"] <= 1.0):
            bad.append("winners forward_factor out of [0,1]")
            break
    if any(ws[i]["score"] < ws[i + 1]["score"] for i in range(len(ws) - 1)):
        bad.append("winners not sorted by score desc")
    for x in doc.get("filter_veto") or []:
        if set(x) != set(VETO_KEYS) or set(x["remedy"]) != {"kind", "override"}:
            bad.append(f"filter_veto keys {sorted(x)}")
            break
    if set(doc.get("coverage") or {}) != set(COV_KEYS):
        bad.append("coverage keys")
    for x in doc.get("xpoll") or []:
        if set(x) != set(XPOLL_KEYS):
            bad.append("xpoll keys")
            break
        if x["from_cat_side"] == m.get("cat_side") or x["from_cat_side"] not in C.CAT_SIDES:
            bad.append("xpoll from own cat_side")
            break
    if len(doc.get("xpoll") or []) > C.XPOLL_K:
        bad.append("xpoll longer than V18_XPOLL_K")
    size = len(json.dumps(doc, separators=(",", ":"), default=str))
    if size > MAX_BYTES:
        bad.append(f"size {size} > {MAX_BYTES}")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--learn-dir", default=str(C.LEARN_DIR))
    ap.add_argument("--out-dir", default=str(C.LEARN_DIR))
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    rc = 0
    for cs in C.CAT_SIDES:
        doc = assemble(cs, a.learn_dir)
        bad = validate(doc)
        cov = doc["coverage"]
        line = (f"[digest] {cs}: rank {len(doc['exit_loss_rank'])} · menu families {len(doc['repair_menu'])} · winners {len(doc['winners'])} · "
                f"veto {len(doc['filter_veto'])} · xpoll {len(doc['xpoll'])} · coverage pairs {cov['pairs_total']} n<3 {cov['pairs_n_lt_3']} "
                f"at_floor {cov['pairs_at_floor']} floor {cov['floor_per_1000']}/1000")
        print(line + (f" · INVALID {bad}" if bad else " · contract OK"), flush=True)
        if bad:
            rc = 2
            continue
        if a.apply:
            p = C.write_json(Path(a.out_dir) / f"digest_{cs}.json", doc, indent=None)
            print(f"[digest] wrote {p} ({p.stat().st_size} bytes, md5 {C.md5_file(p)})", flush=True)
    return rc


if __name__ == "__main__":
    sys.exit(main())
