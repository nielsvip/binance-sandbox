#!/usr/bin/env python3
"""v18_filter_veto_map — ABL-0 of doc 17 §5 (B3): per cat_side, which FILTER suffocates which SWITCH, and the remedy to try.

Ablation is a procedure, never a setting (USER 2026-10-20): strip -> find the switch that turns positive -> find the filter
that kills it -> exempt / re-set / (labelled) switch off that filter FOR THAT SWITCH -> restore -> adopt only above best.
This tool is the offline half: it ranks (switch=cand, filter) pairs from three real-engine sources and emits remedies in
the order the user set.

EVIDENCE (all real engine answers, per cat_side, never pooled across cat_sides)
  abl       V17 ablation records of the delta logs (tools/v18_evidence_mine.py): discovery delta d of switch=cand on the
            strip base; eff = g(strip+F+switch) - g(strip+F); F is a KILLER on that sym_side when eff < 0.5 d (the pilot's
            v17_killers rule). Remedy outcomes the pilot measured: ABL_EXEMPT / ABL_PROXY / ABL_RESTORE deltas vs the chain.
  quartet   v17_priors_mine's filter-blocks-switch quartet on main-pass rows: d_on = naked delta of S, d_off =
            (chain+S+F=off) - (chain+F=off); F blocks S on that sym_side when d_off > 0 and d_off > d_on.
  ledger    `measure` (S2, real include_ledger evals): base = latest published final set + switch=cand, candidate = base +
            F at its off value; the trades the candidate ADDS are the entries F vetoed. vetoed_in_missed_moves = added
            entries inside a missed favourable move of the base (tools/v15_trade_autopsy.missed_moves), vetoed_losers =
            added entries that lost. A pair is a veto worth remedying when vetoed_in_missed_moves > vetoed_losers.
            Unmeasured pairs carry null in both fields (never a guessed count).
REMEDIES (first applicable becomes `remedy`; all are listed in the json)
  exemption  {"FILTER_EXEMPTIONS": {path_id: [F]}} via data/v17_filter_exemptions_schema.json (switch_to_path + valid filter
             set), or the existing scoped key from data/v17_filter_exemptions.json for that (switch, F)
  reset      F's next-looser TEMPLATE option (TF one step finer, D -> 4h -> 1h -> 15m, as doc 17 §0.2's example); numeric /
             bool filters have no direction-safe reset and skip this step
  global_off F at its off value (bool False / TF 'OFF') — a labelled GLOBAL_OFF candidate, never a silent proxy
data/v17_filter_exemptions.json is written ADDITIVELY (backup first): existing entries are never dropped or changed; a
missing (switch, F) exemption override is added; support counts + source live under the top-level `_v18_evidence` key
(per cat_side), never inside an override dict (the pilot applies override dicts verbatim).

usage
  python3 tools/v18_filter_veto_map.py build [--apply]                                   (Mac)
  python3 tools/v18_filter_veto_map.py measure --root ~/binance-sandbox --evidence ~/v18_learn/evidence \
      --sets ~/v18_learn/replay_sets.jsonl.gz --out ~/v18_learn/veto_measure --pairs 30 --syms 4 --workers 2 --apply   (S2)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v18_learn_common as C  # noqa: E402

EXEMPT_FILE = ROOT / "data" / "v17_filter_exemptions.json"
SCHEMA_FILE = ROOT / "data" / "v17_filter_exemptions_schema.json"
TF_LOOSER = {"D": "4h", "1d": "4h", "4h": "1h", "1h": "15m"}
TOP = int(os.environ.get("V18_VETO_TOP", "200"))


def off_value(v):
    if isinstance(v, bool) or str(v) in ("True", "False"):
        return False
    if isinstance(v, str):
        return "OFF"
    return None


def candidate_pairs(ev):
    """Union of abl killer pairs and quartet block pairs -> {(swc, F): record}."""
    out = {}
    for k, a in (ev.get("abl") or {}).items():
        swc, _, f = k.partition("|")
        if a.get("killer_syms", 0) <= 0:
            continue
        r = out.setdefault((swc, f), {"switch_cand": swc, "filter": f, "abl": None, "quartet": None})
        r["abl"] = a
    for k, q in (ev.get("quartets") or {}).items():
        swc, _, f = k.partition("|")
        if q.get("block_syms", 0) <= 0:
            continue
        r = out.setdefault((swc, f), {"switch_cand": swc, "filter": f, "abl": None, "quartet": None})
        r["quartet"] = q
    for r in out.values():
        syms = set((r["abl"] or {}).get("killer_sym_list") or []) | set((r["quartet"] or {}).get("block_sym_list") or [])
        r["support_sym_list"] = sorted(syms)
        r["support_syms"] = len(syms)
        r["support_n"] = max((r["abl"] or {}).get("n_syms", 0), (r["quartet"] or {}).get("n_syms", 0))
        r["sum_effect"] = round(((r["abl"] or {}).get("sum_disc_d", 0.0) - (r["abl"] or {}).get("sum_eff", 0.0))
                                + (r["quartet"] or {}).get("sum_off_minus_on", 0.0), 6)
    return out


def remedies(cat_side, sw, cand, f, tidx, schema, exm):
    out = []
    pid = (schema.get("switch_to_path") or {}).get(sw)
    ex_sw = exm.get(f"{sw}={cand}") or exm.get(sw) or {}
    existing = ex_sw.get(f) if isinstance(ex_sw, dict) else None
    valid_f = f in (schema.get("filters") or {}) or f in ((schema.get("mechanism") or {}).get("augment_gate_keys") or [])
    if isinstance(existing, dict) and existing:
        out.append({"kind": "exemption", "override": existing, "source": "existing_exemptions_file"})
    elif pid and valid_f:
        out.append({"kind": "exemption", "override": {"FILTER_EXEMPTIONS": {pid: [f]}}, "source": "schema_switch_to_path"})
    dflt = tidx["defaults"].get(f)
    opts = tidx["switches"].get(f) or []
    if dflt in TF_LOOSER:
        nxt = TF_LOOSER[dflt]
        while nxt and nxt not in opts:
            nxt = TF_LOOSER.get(nxt)
        if nxt:
            out.append({"kind": "reset", "override": {f: nxt}, "source": f"template option one step looser than default {dflt}"})
    ov = off_value(dflt if dflt is not None else "OFF")
    if ov is not None:
        out.append({"kind": "global_off", "override": {f: ov}, "source": "GLOBAL_OFF (labelled candidate, never a silent proxy)"})
    return out


def build(a):
    schema = C.read_json(SCHEMA_FILE, {}) or {}
    exm = C.read_json(EXEMPT_FILE, {}) or {}
    measured_all = {}
    docs = {}
    for cs in C.CAT_SIDES:
        ev = C.read_json(Path(a.evidence) / f"evidence_{cs}.json.gz", {}) or {}
        tidx = C.template_index(cs)
        meas = (C.read_json(Path(a.measured) / f"veto_measure_{cs}.json", {}) or {}).get("pairs", {}) if a.measured else {}
        measured_all[cs] = meas
        rows, refused = [], defaultdict(int)
        for (swc, f), r in candidate_pairs(ev).items():
            sw, _, cand = swc.partition("=")
            why = C.excluded_key_reason(sw, cand) or (None if sw in tidx["switches"] else "switch_not_in_template")
            if why:
                refused[why] += 1
                continue
            m = meas.get(f"{swc}|{f}")
            rem = remedies(cs, sw, cand, f, tidx, schema, exm)
            rows.append({"switch": sw, "cand": cand, "filter": f, "opt": str(off_value(tidx["defaults"].get(f, "OFF")) if off_value(tidx["defaults"].get(f, "OFF")) is not None else ""),
                         "filter_default": tidx["defaults"].get(f), "vetoed_in_missed_moves": m["vetoed_in_missed_moves"] if m else None,
                         "vetoed_losers": m["vetoed_losers"] if m else None, "support_syms": r["support_syms"], "support_n": r["support_n"],
                         "support_sym_list": r["support_sym_list"], "sum_effect": r["sum_effect"], "abl": r["abl"], "quartet": r["quartet"],
                         "ledger": m, "remedy": rem[0] if rem else None, "remedies": rem,
                         "source": "+".join(s for s, v in (("abl", r["abl"]), ("quartet", r["quartet"]), ("ledger", m)) if v)})

        def key(x):
            led = x["vetoed_in_missed_moves"] is not None
            margin = (x["vetoed_in_missed_moves"] - x["vetoed_losers"]) if led else 0
            return (-(1 if led and margin > 0 else 0), -margin, -x["support_syms"], -x["sum_effect"])
        rows.sort(key=key)
        docs[cs] = {"meta": {"cat_side": cs, "built_at": C.utc(), "tool": "tools/v18_filter_veto_map.py", "evidence_built_at": (ev.get("meta") or {}).get("built_at"),
                             "n_candidates": len(rows), "refused": dict(refused), "n_ledger_measured": sum(1 for x in rows if x["ledger"]),
                             "n_ledger_veto_confirmed": sum(1 for x in rows if x["ledger"] and x["vetoed_in_missed_moves"] > x["vetoed_losers"]),
                             "kill_frac": 0.5, "remedy_order": ["exemption", "reset", "global_off"]},
                    "pairs": rows[:TOP]}
    if a.apply:
        for cs, d in docs.items():
            C.write_json(C.LEARN_DIR / f"filter_veto_{cs}.json", d)
            (C.LEARN_DIR / f"filter_veto_{cs}.md").write_text(render_md(cs, d))
        write_exemptions(docs, exm)
    for cs, d in docs.items():
        print(f"[veto] {cs}: {json.dumps(d['meta'])}", flush=True)
    return 0


def write_exemptions(docs, exm):
    """Additive merge into data/v17_filter_exemptions.json (backup first)."""
    bak = C.backup_file(EXEMPT_FILE, "v18_filter_veto_exemptions")
    before = sum(len(v) for k, v in exm.items() if isinstance(v, dict) and not k.startswith("_"))
    added = []
    ev_block = exm.get("_v18_evidence") if isinstance(exm.get("_v18_evidence"), dict) else {}
    for cs, d in docs.items():
        blk = {}
        for x in d["pairs"]:
            rem = x["remedy"] or {}
            if rem.get("kind") == "exemption" and rem.get("source") == "schema_switch_to_path":
                row = exm.setdefault(x["switch"], {})
                if x["filter"] not in row:
                    row[x["filter"]] = rem["override"]
                    added.append([cs, x["switch"], x["filter"]])
            blk[f"{x['switch']}={x['cand']}|{x['filter']}"] = {"support_syms": x["support_syms"], "support_n": x["support_n"], "source": x["source"],
                                                               "vetoed_in_missed_moves": x["vetoed_in_missed_moves"], "vetoed_losers": x["vetoed_losers"],
                                                               "remedy_kind": rem.get("kind"), "built_at": d["meta"]["built_at"]}
        ev_block[cs] = blk
    exm["_v18_evidence"] = ev_block
    after = sum(len(v) for k, v in exm.items() if isinstance(v, dict) and not k.startswith("_"))
    assert after >= before, "additive write would drop entries"
    tmp = EXEMPT_FILE.with_name(EXEMPT_FILE.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(exm, fh, indent=0, sort_keys=True)
    os.replace(tmp, EXEMPT_FILE)
    print(f"[veto] exemptions: {before} -> {after} (switch, filter) entries, added {len(added)}; backup {bak}", flush=True)


def render_md(cs, d):
    m = d["meta"]
    L = [f"# Filter-veto map (ABL-0) — {cs} — {m['built_at']}", "",
         f"Candidates {m['n_candidates']} · ledger-measured {m['n_ledger_measured']} · veto confirmed by ledgers "
         f"(vetoed entries inside missed moves > vetoed losers) {m['n_ledger_veto_confirmed']}. Remedy order: exemption -> reset -> global_off.", "",
         C.md_table(["switch=cand", "filter", "support_syms", "in_missed_moves", "losers", "remedy", "source"],
                    [[f"`{x['switch']}={x['cand']}`", f"`{x['filter']}`", x["support_syms"], x["vetoed_in_missed_moves"], x["vetoed_losers"],
                      (x["remedy"] or {}).get("kind"), x["source"]] for x in d["pairs"][:30]])]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------------------------------------- measure (S2)


def measure_one(job):
    rec, items, root = job
    from tools import v15_trade_autopsy as TA
    from tools.opt import v12_pilot as VP
    from tools.switch_types import coerce
    from tools.v18_ledger_replay import ledger_rows, wait_for_load
    wait_for_load()
    prep = VP.prepare_batch(rec["sym_side"], 30)
    if prep is None or (isinstance(prep, dict) and prep.get("_stale")):
        return rec["sym_side"], [{"skip": "NPZ_STALE_OR_MISSING"}]
    npz = prep.get("npz_prepared") or {}
    is_long = bool(prep.get("is_long"))
    close = [float(x) for x in npz["close"]]
    venue = "tradier" if rec["cat_side"].startswith("STOCKS") else "crypto"
    out = []
    for swc, f in items:
        sw, _, cand = swc.partition("=")
        ok, cv, _w = coerce(sw, cand, venue)
        if not ok:
            out.append({"pair": f"{swc}|{f}", "skip": "type_refused"})
            continue
        base_ov = dict(rec["set"])
        base_ov[sw] = cv
        cur = base_ov.get(f)
        offv = off_value(cur if cur is not None else "OFF")
        if offv is None:
            out.append({"pair": f"{swc}|{f}", "skip": "filter_value_has_no_off"})
            continue
        if cur == offv:
            out.append({"pair": f"{swc}|{f}", "skip": "filter_already_off_in_set"})
            continue
        cand_ov = dict(base_ov)
        cand_ov[f] = offv
        rb = VP.evaluate_prepared_sanitized(prep, base_ov, 30, include_ledger=True)
        rc = VP.evaluate_prepared_sanitized(prep, cand_ov, 30, include_ledger=True)
        brows, _ = ledger_rows(rb, npz, is_long, "b")
        crows, _ = ledger_rows(rc, npz, is_long, "c")
        base_tr = [{"be": r["bar_entry"], "bx": r["bar_exit"]} for r in brows]
        moves = TA.missed_moves(base_tr, close, is_long)
        bset = {r["bar_entry"] for r in brows}
        added = [r for r in crows if r["bar_entry"] not in bset]
        in_move = [r for r in added if any(m["b0"] <= r["bar_entry"] <= m["b1"] for m in moves)]
        out.append({"pair": f"{swc}|{f}", "off_value": offv, "n_added": len(added), "vetoed_in_missed_moves": len(in_move),
                    "vetoed_in_missed_moves_won": sum(1 for r in in_move if (r["pnl_pp"] or 0) > 0),
                    "vetoed_losers": sum(1 for r in added if (r["pnl_pp"] or 0) < 0), "added_pnl_pp": round(sum(r["pnl_pp"] or 0 for r in added), 6),
                    "n_removed": len(bset - {r["bar_entry"] for r in crows}), "missed_moves_base": len(moves),
                    "d_gain": round((rc.get("gain_pct") or 0) - (rb.get("gain_pct") or 0), 6), "valid": bool(rc.get("valid"))})
    return rec["sym_side"], out


def measure(a):
    root = Path(os.path.expanduser(a.root)).resolve()
    os.chdir(root)
    with gzip.open(os.path.expanduser(a.sets), "rt") as fh:
        sets = {}
        for line in fh:
            if line.strip():
                d = json.loads(line)
                sets[d["sym_side"]] = d
    out_dir = Path(os.path.expanduser(a.out))
    out_dir.mkdir(parents=True, exist_ok=True)
    for cs in ([a.cat_side] if a.cat_side else C.CAT_SIDES):
        ev = C.read_json(Path(os.path.expanduser(a.evidence)) / f"evidence_{cs}.json.gz", {}) or {}
        cands = sorted(candidate_pairs(ev).values(), key=lambda r: (-r["support_syms"], -r["sum_effect"]))
        jobs = defaultdict(list)
        n_pairs = 0
        for r in cands:
            sw = r["switch_cand"].partition("=")[0]
            if C.excluded_key_reason(sw):
                continue
            syms = [s for s in r["support_sym_list"] if s in sets][: a.syms]
            if not syms:
                continue
            n_pairs += 1
            for s in syms:
                jobs[s].append((r["switch_cand"], r["filter"]))
            if n_pairs >= a.pairs:
                break
        todo = [(sets[s], items, str(root)) for s, items in jobs.items()]
        print(f"[veto-measure] {cs}: {n_pairs} pairs over {len(todo)} sym_sides, {2 * sum(len(i) for _s, i, _r in todo)} ledger evals {C.utc()}", flush=True)
        if not a.apply:
            continue
        raw = {}
        if a.workers > 1:
            import multiprocessing as mp
            with mp.get_context("fork").Pool(min(a.workers, 4), maxtasksperchild=10) as pool:
                for s, res in pool.imap_unordered(measure_one, todo):
                    raw[s] = res
        else:
            for t in todo:
                s, res = measure_one(t)
                raw[s] = res
        pairs = {}
        for s, res in raw.items():
            for r in res:
                if r.get("skip"):
                    continue
                p = pairs.setdefault(r["pair"], {"n_syms": 0, "vetoed_in_missed_moves": 0, "vetoed_in_missed_moves_won": 0, "vetoed_losers": 0,
                                                 "added": 0, "added_pnl_pp": 0.0, "sum_d_gain": 0.0, "syms": []})
                p["n_syms"] += 1
                for k in ("vetoed_in_missed_moves", "vetoed_in_missed_moves_won", "vetoed_losers"):
                    p[k] += r[k]
                p["added"] += r["n_added"]
                p["added_pnl_pp"] = round(p["added_pnl_pp"] + r["added_pnl_pp"], 6)
                p["sum_d_gain"] = round(p["sum_d_gain"] + r["d_gain"], 6)
                p["syms"].append(s)
        C.write_json(out_dir / f"veto_measure_{cs}.json", {"meta": {"cat_side": cs, "at": C.utc(), "host": os.uname().nodename,
                                                                    "rule": "base = published final set + switch=cand; candidate = base + filter at its off value; "
                                                                            "added entries = the filter's vetoes; missed moves of the base (v15_trade_autopsy)"},
                                                           "pairs": pairs, "raw": raw})
        print(f"[veto-measure] {cs} written {len(pairs)} pairs", flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--evidence", default=str(C.LEARN_DIR / "evidence"))
    p.add_argument("--measured", default=str(C.LEARN_DIR / "veto_measure"))
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("measure")
    p.add_argument("--cat-side", default="")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--evidence", required=True)
    p.add_argument("--sets", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--pairs", type=int, default=30)
    p.add_argument("--syms", type=int, default=4)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else measure(a)


if __name__ == "__main__":
    sys.exit(main())
