#!/usr/bin/env python3
"""v18_switch_proposer — brief 20 §6.4 "new switches on every upgrade" (WP6), design 22_V20_NETWORK_V3 §2-§4.

Per cat_side, strictly separate (law: four cat_sides apart), writes data/learn/proposals_v3_<CS>.json with at most
--quota proposals (default 20), every one labelled PREDICTION. Three types:
  A  a NEW CANDIDATE VALUE of an existing, wired switch (template row add only): linknet v3u NEEDS_ROW items
     (threshold / TF / level neighbours) + intermediates between the bold and a measured-positive neighbour + TF-list unions
     of measured-positive single TFs the template lacks. Install path: tools/v15_switch_add.py --extend (queue mode skips
     switches already present, see `queue`).
  B  a FILTER attached to a template row where the binding map says BINDS but the template has no yellow cell
     (unshaded binding pair). No template write is involved: the pilot measures any header of a row it is given.
  C  a NEW SWITCH that needs engine wiring (4-surface law). Proposed with a full wiring spec, never wired here.
Every candidate is scored p_pos_pred (linknet v3u score where one exists, else a counts fallback, `p_source` says which)
x evidence support (positive sym_sides of the nearest measured key), refused classes are counted in meta.refused.
Selection: C first (at most --max-c, by score), at least --min-b of B, the rest by score over A u B; per-switch and
per-filter caps keep the list diverse. Nothing here is a measurement and nothing is adopted except by the real engine.

  python3 tools/v18_switch_proposer.py build --cat-side all [--quota 20] [--no-calibration]
  python3 tools/v18_switch_proposer.py queue --cat-side all       # v15_switch_add dry-runs for the A items (no --apply)
"""
from __future__ import annotations

import argparse
import ast
import collections
import gzip
import json
import math
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import v18_binding_map as BM  # noqa: E402
from tools import v18_learn_common as LC  # noqa: E402

CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SCHEMA = "v18_switch_proposals/3"
MODEL = "v3u_20261022"
LEARN = ROOT / "data" / "learn"
V3U = LEARN / "v3u_20261022"
CALIBRATION = LEARN / "proposal_calibration.jsonl"
LABEL = "PREDICTION"
M_SHRINK = 8.0
SUPPORT_FLOOR = 0.25
SUPPORT_FULL_AT = 10
MAX_PER_SWITCH = 2
MAX_PER_FILTER = 2
TF_RANK = {"1m": 0, "3m": 1, "5m": 2, "15m": 3, "30m": 4, "1h": 5, "2h": 6, "4h": 7, "6h": 8, "8h": 9, "12h": 10, "D": 11, "W": 12}
STOP_EXEC = ("VEC_STALE_FLATTEN", "VEC_EXIT_OWED", "VEC_CONVERGE")


class CatSideRefused(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path):
    p = Path(path)
    if p.suffix == ".gz":
        with gzip.open(p, "rt") as fh:
            return json.load(fh)
    return json.loads(p.read_text())


def _check_cs(doc_cs, cs, what):
    if doc_cs is not None and doc_cs != cs:
        raise CatSideRefused(f"CAT_SIDE_MISMATCH {what}: want={cs} got={doc_cs}")


def _class_fields(path, cls):
    """Field names declared in class `cls` of `path` (AST only, nothing imported)."""
    try:
        tree = ast.parse(Path(path).read_text(errors="replace"))
    except (OSError, SyntaxError):
        return set()
    out = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    out.add(st.target.id)
                elif isinstance(st, ast.Assign):
                    out.update(t.id for t in st.targets if isinstance(t, ast.Name))
    return out


def load_code_fields(root=ROOT):
    return {"CRYPTO": _class_fields(root / "config.py", "Config"), "STOCKS": _class_fields(root / "config_tradier.py", "TradierConfig"), "QC": _class_fields(root / "v12_quick_engine.py", "QuickConfig")}


def load_inputs(cs, learn_dir=LEARN, v3u_dir=V3U, root=ROOT):
    """Every input of one cat_side; any file that names another cat_side is refused (CatSideRefused)."""
    learn_dir, v3u_dir = Path(learn_dir), Path(v3u_dir)
    ln = _read(v3u_dir / f"linknet_proposals_{cs}.json")
    _check_cs((ln.get("meta") or {}).get("cat_side"), cs, "linknet_proposals")
    bm = _read(learn_dir / f"binding_map_{cs}.json")
    _check_cs((bm.get("meta") or {}).get("cat_side"), cs, "binding_map")
    feats = _read(v3u_dir / f"features_{cs}.json")
    _check_cs((feats.get("meta") or {}).get("cat_side"), cs, "features")
    cards = _read(v3u_dir / f"switch_cards_{cs}.json")
    _check_cs((cards.get("_meta") or {}).get("cat_side"), cs, "switch_cards")
    pa = _read(v3u_dir / f"positives_all_{cs}.json.gz")
    _check_cs((pa.get("_meta") or {}).get("cat_side"), cs, "positives_all")
    nf = _read(v3u_dir / f"needed_filters_{cs}.json")  # no meta block: cat_side by file name only
    elr_p = learn_dir / f"exit_loss_rank_{cs}.json"
    elr = _read(elr_p) if elr_p.exists() else {}
    _check_cs((elr.get("meta") or {}).get("cat_side"), cs, "exit_loss_rank")
    bible_p = root / "data" / "SWITCH_BIBLE.json"
    bible = _read(bible_p)["switches"] if bible_p.exists() else {}
    npz = None
    npz_p = v3u_dir / f"linknet_scores_{cs}.npz"
    if npz_p.exists():
        npz = load_npz_scores(npz_p, cs)
    srcs = {str(p): LC.md5_file(p) for p in [v3u_dir / f"linknet_proposals_{cs}.json", learn_dir / f"binding_map_{cs}.json", v3u_dir / f"features_{cs}.json", v3u_dir / f"switch_cards_{cs}.json", v3u_dir / f"positives_all_{cs}.json.gz", v3u_dir / f"needed_filters_{cs}.json", npz_p, elr_p, bible_p] if Path(p).exists()}
    tpl = root / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{cs}.xlsx"
    bm_tpl = [v for k, v in ((bm.get("meta") or {}).get("sources") or {}).items() if k.endswith(f"TEMPLATE_{cs}.xlsx")]
    tpl_ok = (LC.md5_file(tpl) == bm_tpl[0]) if (tpl.exists() and bm_tpl) else None
    return {"cs": cs, "template_md5_ok": tpl_ok, "linknet": ln, "bm": bm, "features": feats, "cards": cards.get("cards") or {}, "positives": pa, "needed": nf, "elr": elr, "bible": bible, "npz": npz, "code": load_code_fields(root), "sources": srcs}


def load_npz_scores(path, cs):
    import numpy as np
    z = np.load(path, allow_pickle=False)
    meta = {}
    try:
        meta = json.loads(bytes(z["meta"]).decode())
    except Exception:
        pass
    _check_cs(meta.get("cat_side"), cs, "linknet_scores")
    rows = [str(x) for x in z["row_keys"]]
    yel = [str(x) for x in z["yellows"]]
    ny = len(yel)
    code = z["cell_row"].astype("int64") * ny + z["cell_yellow"].astype("int64")
    order = np.argsort(code)
    rn, rp = z["row_n_measured"], z["row_pos_measured"]
    return {"row_idx": {k: i for i, k in enumerate(rows)}, "yel_idx": {k: i for i, k in enumerate(yel)}, "ny": ny, "code": code[order], "order": order, "cell_p": z["cell_p_pos"][:, 0].astype("float64"), "cell_e": z["cell_e_delta"][:, 0].astype("float64"), "cell_n": z["cell_n_measured"], "row_p": z["row_p_pos"][:, 0].astype("float64"), "row_n": rn, "row_pos": rp, "p0": float(rp.sum()) / float(max(1, rn.sum()))}


def npz_cell(npz, tab, sw, cand, header):
    if not npz:
        return None
    ri = npz["row_idx"].get(f"{tab}|{sw}={cand}")
    yi = npz["yel_idx"].get(header)
    if ri is None or yi is None:
        return None
    import numpy as np
    c = ri * npz["ny"] + yi
    j = int(np.searchsorted(npz["code"], c))
    if j >= len(npz["code"]) or int(npz["code"][j]) != c:
        return None
    k = int(npz["order"][j])
    return {"p": round(float(npz["cell_p"][k]), 5), "e": round(float(npz["cell_e"][k]), 4), "n": int(npz["cell_n"][k])}


def support(pos_syms):
    """Evidence support in [SUPPORT_FLOOR, 1]: never zero (floor law), full at SUPPORT_FULL_AT positive sym_sides."""
    return round(SUPPORT_FLOOR + (1 - SUPPORT_FLOOR) * min(1.0, math.log1p(max(0, pos_syms)) / math.log1p(SUPPORT_FULL_AT)), 4)


def shrunk(pos, n, p0, m=M_SHRINK):
    return (float(pos) + m * float(p0)) / (max(0.0, float(n)) + m)


def num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def fmt_num(x, ints):
    if ints and abs(x - round(x)) < 1e-12:
        return str(int(round(x)))
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def tfs_of(v):
    return [t for t in re.split(r"[,+]", str(v)) if t]


class Ev:
    """Measured evidence lookups of one cat_side (cards + positives_all + needed_filters), tokenized sym_sides dropped."""

    def __init__(self, ins, refused):
        self.cards = ins["cards"]
        pa = ins["positives"]
        self.syms = pa.get("sym_sides") or []
        self.rows = {}
        for r in pa.get("rows") or []:
            ps = self._clean(r.get("pos_syms") or [], refused)
            self.rows[r["key"]] = {"n": int(r.get("n") or 0), "pos": int(r.get("n_pos") or 0), "pos_syms": len(ps), "best_delta": r.get("best_delta")}
        self.fmarg = collections.defaultdict(lambda: {"n": 0, "pos": 0, "syms": set()})
        self.pairs = {}
        for r in pa.get("pairs") or []:
            ps = self._clean(r.get("pos_syms") or [], refused)
            f = self.fmarg[r["filter"]]
            f["n"] += int(r.get("n") or 0)
            f["pos"] += int(r.get("n_pos") or 0)
            f["syms"].update(ps)
            self.pairs[(r["key"], r["filter"])] = {"n": int(r.get("n") or 0), "pos": int(r.get("n_pos") or 0), "pos_syms": len(ps), "lift": r.get("lift"), "lift_ci": r.get("lift_ci")}
        self.needed = ins["needed"]

    @staticmethod
    def _clean(syms, refused):
        out = [s for s in syms if not LC.is_tokenized(s)]
        if len(out) != len(syms):
            refused["tokenized_stock_evidence_dropped"] += len(syms) - len(out)
        return out

    def key(self, sw, cand):
        """Measured evidence of the naked row SW=cand: card cands entry + positives_all row (pos evals / pos sym_sides)."""
        k = f"{sw}={cand}"
        card = self.cards.get(sw) or {}
        c = (card.get("cands") or {}).get(str(cand))
        r = self.rows.get(k)
        out = {"key": k, "n": 0, "pos": 0, "pos_syms": 0, "interval_p_pos": None, "verdict": None, "source": []}
        if c:
            out.update({"n": int(c[1]), "pos_syms": int(c[2]), "interval_p_pos": [c[3], c[4]], "verdict": c[0]})
            out["source"].append("switch_cards")
        if r:
            out["n"] = max(out["n"], r["n"])
            out["pos"] = r["pos"]
            out["pos_syms"] = max(out["pos_syms"], r["pos_syms"])
            out["best_delta"] = r["best_delta"]
            out["source"].append("positives_all")
        if str(card.get("default")) == str(cand):
            out["is_default"] = True
        return out

    def filt(self, header):
        f = self.fmarg.get(header)
        if not f:
            return {"key": header, "n": 0, "pos": 0, "pos_syms": 0}
        return {"key": header, "n": f["n"], "pos": f["pos"], "pos_syms": len(f["syms"])}


def wired_status(sw, venue, ins):
    """None = wired on this venue (config field + QuickConfig field + bible WIRED_BOTH*), else the reason."""
    code = ins["code"]
    if sw not in code[venue]:
        return f"not_in_{'config.py' if venue == 'CRYPTO' else 'config_tradier.py'}"
    if sw not in code["QC"]:
        return "not_in_QuickConfig"
    e = ins["bible"].get(sw)
    if not e:
        return "no_SWITCH_BIBLE_entry"
    st = e.get("status")
    st = st.get(venue.lower() if venue == "CRYPTO" else "stocks") if isinstance(st, dict) else st
    if not str(st or "").startswith("WIRED_BOTH"):
        return f"bible_status_{st}"
    return None


def row_verdict(bm, sw):
    """Binding verdict of a NEW naked row of `sw`: NEVER when the switch is dead, CONDITIONAL when a master is off by default."""
    s = (bm.get("switches") or {}).get(sw)
    if s is None:
        return {"verdict": "UNKNOWN", "reason": "switch not in binding map (fail-open)"}
    if s.get("dead"):
        return {"verdict": "NEVER", "reason": "DEAD_ROW"}
    md = (bm.get("meta") or {}).get("masters_default") or {}
    off = [m for m in s.get("masters") or [] if m in md and not BM.truthy(md[m].get("value"))]
    if off:
        return {"verdict": "CONDITIONAL", "reason": "MASTER_OFF", "condition_keys": [f"{m}=True" for m in off]}
    return {"verdict": "BINDS", "reason": "ROW_LIVE", "stage": s.get("stage"), "family": s.get("family")}


def template_cands(bm):
    """{switch: {cand: (tab, default_row)}} from the binding map rows (= the template)."""
    out = collections.defaultdict(dict)
    for rk, r in (bm.get("rows") or {}).items():
        tab, rest = rk.split("!", 1)
        sw, cand = rest.split("=", 1)
        out[sw].setdefault(cand, (tab, bool(r.get("default_row"))))
    return out


def bold_of(tc, sw):
    for cand, (_t, d) in tc.get(sw, {}).items():
        if d:
            return cand
    return None


def fixed_stop_domain(sw, cand, existing):
    """FIXED_STOP_* values the engine normalises away: None = real new value, else the refusal class."""
    if not sw.startswith("FIXED_STOP_"):
        return None
    try:
        from vec_decisions import fixed_stop as FS
    except Exception:
        return None
    pos = {"FIXED_STOP_REF_FIELD": 0, "FIXED_STOP_REF_TF": 1, "FIXED_STOP_BUFFER_PCT": 2, "FIXED_STOP_TIGHTEN_MODE": 3, "FIXED_STOP_TIGHTEN_TRIGGER_PCT": 4}.get(sw)
    if pos is None:
        return None
    args = [None] * 5
    args[pos] = cand
    got = FS.normalize_spec(*args)[pos]
    if str(got) == str(cand) or (num(got) is not None and num(got) == num(cand)):
        return None
    for e in existing:
        if str(got) == str(e) or (num(got) is not None and num(got) == num(e)):
            return f"alias_of_existing:{sw}={e}"
    return "engine_domain_normalises_to_default"


def nearest_same_switch(sw, cand, ins, ev):
    """Closest existing template value of the same switch by the features map (numeric distance, TF rank, enum tokens)."""
    sets = (ins["features"].get("settings") or {}).get(sw) or {}
    best, bd = None, None
    cv = num(cand)
    ct = tfs_of(cand)
    for v, f in sets.items():
        if f.get("kind") == "num" and cv is not None and num(v) is not None:
            d = abs(num(v) - cv)
        elif f.get("kind") == "tf":
            a = {TF_RANK.get(t, -1) for t in ct}
            b = {TF_RANK.get(t, -1) for t in tfs_of(v)}
            d = len(a ^ b) + abs((sum(a) / max(1, len(a))) - (sum(b) / max(1, len(b)))) / 10.0
        else:
            tok = set(re.split(r"[_\s,]+", str(cand).lower()))
            d = 1.0 - len(tok & set(re.split(r"[_\s,]+", str(v).lower()))) / max(1, len(tok))
        if bd is None or d < bd:
            best, bd = v, d
    if best is None:
        return None
    out = ev.key(sw, best)
    out["value_bucket"] = {k: sets[best].get(k) for k in ("kind", "rel", "q") if k in sets[best]}
    out["distance"] = round(bd, 6)
    return out


def _excluded(sw, cand, refused):
    why = LC.excluded_key_reason(sw, cand)
    if why:
        refused[f"artefact_{why}"] += 1
        return True
    if sw.startswith(STOP_EXEC):
        refused["stop_execution_no_vector_surface"] += 1
        return True
    return False


def gen_a(ins, ev, tc, refused):
    """Type A candidates: linknet NEEDS_ROW + intermediates + TF-list unions, wired switches only."""
    cs, bm = ins["cs"], ins["bm"]
    venue = LC.venue_of(cs)
    out, seen = [], set()

    def add(sw, cand, tab, rule, p, p_src, note, extra=None, near=None):
        if (sw, cand) in seen:
            return
        seen.add((sw, cand))
        if _excluded(sw, cand, refused):
            return
        if cand in tc.get(sw, {}):
            refused["already_in_template"] += 1
            return
        dom = fixed_stop_domain(sw, cand, list(tc.get(sw, {})))
        if dom:
            refused["already_in_template" if dom.startswith("alias") else dom] += 1
            return
        tr = LC.type_refused(sw, cand, cs)
        if tr:
            refused["type_refused"] += 1
            return
        rv = row_verdict(bm, sw)
        if rv["verdict"] == "NEVER":
            refused["dead_row"] += 1
            return
        ws = wired_status(sw, venue, ins)
        if ws:
            refused["not_wired_needs_C"] += 1
            return
        near = near or nearest_same_switch(sw, cand, ins, ev)
        bold = bold_of(tc, sw)
        if p is None:
            p, p_src = shrunk(near["pos"] if near else 0, near["n"] if near else 0, ins["p0"]), "counts_fallback:nearest_same_switch"
        sup = support(near["pos_syms"] if near else 0)
        item = {"type": "A", "switch": sw, "cand": cand, "filter": None, "opt": None, "tab": tab, "rule": rule, "bold": bold, "p_pos_pred": round(float(p), 5), "p_source": p_src, "support": sup, "nearest_measured": near, "bind_verdict": rv, "note": note, "wiring_needed": False}
        item.update(extra or {})
        out.append(item)

    for x in (ins["linknet"].get("needs_row") or []):
        tab = x.get("tab") or next(iter(tc.get(x["switch"], {}).values()), ("", False))[0]
        nb = (x.get("nearest_evidence") or [None])[0]
        add(x["switch"], str(x["cand"]), tab, x.get("rule"), x.get("p_pos_pred"), "linknet_v3u:needs_row", x.get("note") or "", {"e_delta_pred": x.get("e_delta_pred"), "nearest_features_cos": nb})
    for sw, cands in tc.items():
        tab = next(iter(cands.values()))[0]
        bold = bold_of(tc, sw)
        vals = list(cands)
        if bold is None:
            continue
        nums = {v: num(v) for v in vals}
        if all(nums[v] is not None for v in vals) and len(vals) >= 2 and nums[bold] is not None:
            ints = all(re.match(r"^-?\d+$", v) for v in vals)
            srt = sorted(vals, key=lambda x: nums[x])
            bi = srt.index(bold)
            for v in [srt[j] for j in (bi - 1, bi + 1) if 0 <= j < len(srt)]:  # adjacent neighbours only: the gap is unsampled
                e = ev.key(sw, v)
                if e["pos_syms"] < 2:
                    continue
                lo_, hi_ = sorted((abs(nums[bold]), abs(nums[v])))
                if lo_ > 0 and hi_ / lo_ > 20:
                    refused["intermediate_scale_gap"] += 1  # neighbours 20x apart: different units/sentinels, a midpoint means nothing
                    continue
                mid = (nums[bold] + nums[v]) / 2.0
                if ints:
                    mid = float(round(mid))
                c = fmt_num(mid, ints)
                if c in vals or num(c) in {nums[x] for x in vals}:
                    continue
                p = shrunk(e["pos"], e["n"], ins["p0"])
                add(sw, c, tab, "intermediate_bold_to_positive", p, "counts_fallback:adjacent_positive_neighbour", f"midpoint of bold {bold} and adjacent {v} (positive on {e['pos_syms']} sym_sides)", near=e)
        kinds = [(ins["features"].get("settings") or {}).get(sw, {}).get(v, {}).get("kind") for v in vals]
        if kinds and all(k == "tf" for k in kinds) and any("," in v for v in vals):
            singles = [v for v in vals if "," not in v and v != "OFF" and v in TF_RANK and ev.key(sw, v)["pos_syms"] >= 1]
            for i, a in enumerate(singles):
                for b in singles[i + 1:]:
                    c = ",".join(sorted({a, b}, key=lambda t: TF_RANK[t]))
                    if c in vals:
                        continue
                    ea, eb = ev.key(sw, a), ev.key(sw, b)
                    p = (shrunk(ea["pos"], ea["n"], ins["p0"]) + shrunk(eb["pos"], eb["n"], ins["p0"])) / 2.0
                    add(sw, c, tab, "tf_union_of_positive_singles", p, "counts_fallback:mean(single_tfs)", f"{a} positive on {ea['pos_syms']}, {b} on {eb['pos_syms']} sym_sides; union not in template")
    return out


def gen_b(ins, ev, refused):
    """Type B candidates: unshaded (row, filter=opt) pairs whose binding verdict is BINDS."""
    cs, bm, npz = ins["cs"], ins["bm"], ins["npz"]
    meta = bm.get("meta") or {}
    sws = bm.get("switches") or {}
    ctx = BM.Ctx(cs, {k: v.get("family", "UNKNOWN") for k, v in sws.items()}, bm.get("filters") or {}, {m: (v or {}).get("value") for m, v in (meta.get("masters_default") or {}).items()}, {k for k, v in sws.items() if v.get("dead")}, meta.get("augment_gate_keys") or ())
    opts = ins["features"].get("options") or {}
    headers = [(f, o) for f in (bm.get("filters") or {}) for o, of in (opts.get(f) or {}).items() if of.get("rel") != "default"]
    out = []
    excluded_sw = {}
    for rk, r in (bm.get("rows") or {}).items():
        tab, rest = rk.split("!", 1)
        sw, cand = rest.split("=", 1)
        if sw not in excluded_sw:
            excluded_sw[sw] = bool(LC.excluded_key_reason(sw, cand)) or sw.startswith(STOP_EXEC)
        if excluded_sw[sw]:
            refused["B_rows_artefact"] += 1
            continue
        if r.get("dead_row"):
            refused["dead_row"] += 1
            continue
        if r.get("default_row"):
            continue  # a filter on the bold row is the filter's own marginal, not a row proposal
        cells = r.get("cells") or {}
        nd = ins["needed"].get(f"{sw}={cand}") or {}
        needed = set(nd.get("needed") or [])
        rowev = None
        for f, o in headers:
            h = f"{f}={o}"
            if h in cells:
                refused["already_in_template"] += 1
                continue
            v = BM.verdict_for(ctx, sw, cand, h)
            if v["verdict"] == "NEVER":
                refused["NEVER_verdict"] += 1
                continue
            if v["verdict"] != "BINDS":
                refused[f"B_not_BINDS_{v['verdict']}"] += 1
                continue
            if rowev is None:
                rowev = ev.key(sw, cand)
            if rowev["n"] < 3:
                refused["B_row_unmeasured_n_lt_3"] += 1
                continue
            fe = ev.filt(h)
            cell = npz_cell(npz, tab, sw, cand, h)
            pe = ev.pairs.get((f"{sw}={cand}", h))
            if cell is not None:
                p, src = cell["p"], "linknet_v3u:npz_cell"
            else:
                pf = shrunk(fe["pos"], fe["n"], ins["p0"])
                p, src = shrunk(rowev["pos"], rowev["n"], pf), "counts_fallback:row_shrunk_to_filter_marginal"
            sup = 1.0 if h in needed else round((support(rowev["pos_syms"]) + support(fe["pos_syms"])) / 2.0, 4)
            out.append({"type": "B", "switch": sw, "cand": cand, "filter": f, "opt": o, "tab": tab, "p_pos_pred": round(float(p), 5), "p_source": src, "support": sup, "needed_listed": h in needed, "evidence_thin": rowev["n"] < 10, "e_delta_pred": cell["e"] if cell else None, "n_measured_pair": (pe or {}).get("n", cell["n"] if cell else 0), "nearest_measured": {"row": rowev, "filter_marginal": fe, "pair": pe}, "bind_verdict": v, "wiring_needed": False})
    return out


C_DESIGNS = [
    {"switch": "EXIT_LADDER_COEXIST_ENABLED", "kind": "bool", "default": "False", "cands": ["False", "True"], "tab": "EXIT_STRUCTURAL", "stage": "EXIT", "risk": "exit",
     "analogues": ["WIN_TRAIL_EROSION_PCT", "DELTA_MAX_HOLD_BARS"], "unlocks_reason": "DC_DAYTRADE_BRANCH",
     "rationale": "DELTA_MAX_HOLD / WIN_TRAIL_EROSION legs are unreachable behind the DC daytrade elif; erosion filter cells are NEVER(DC_DAYTRADE_BRANCH)",
     "spec": {"vector": "v12_quick_engine.py simulate_one bar loop, exit if/elif ladder (PROFIT_TARGET -> STOP_LOSS -> DC daytrade `elif daytrade_on and (_dd_stop_dcs or _dd_tgt_dcs)` :21926 -> ATR daytrade :21957 -> `elif max_hold_bars > 0` :21974 -> WIN_TRAIL_EROSION :21976-21986). Insertion: directly after the ladder (before `if (not closed) and _ty_fire` :21988) add ONE call `closed, reason = vec_decisions.exit_ladder_coexist.coexist_legs(cfg, closed, reason, held_bars, max_hold_bars, trail_erosion, pos, live_pnl_pct, _gftf, i)` that re-evaluates the two legs only when `not closed` and the switch is True. New pure module vec_decisions/exit_ladder_coexist.py (predicate only).",
              "live_crypto": "ez_manage.py process_position (:52319) exit-candidate section next to the DC daytrade exit call (:52988 `_dc_channel_exits.daytrade_dc_exit`): when the DC leg did not fire and the switch is True, evaluate vec_decisions.twin_p0_crypto_b.win_trail_erosion_fire (bible live read) and a held-bars check against DELTA_MAX_HOLD_BARS; close through execute_now() only. Today crypto has NO live call of either leg in process_position (ez_manage only lists them in _FULL_COVERAGE_PARAMS_EZ) — the live half is new code, not a flag flip.",
              "live_stocks": "tradier_manage.py process_position (:15059) after the daytrade DC exit (:16050 / :61414 `_dc_channel_exits.daytrade_dc_exit`); the existing reads at :28519 / :30619 are research gates hard-wired `and False` and must not be reused.",
              "config": "config.py Config + config_tradier.py TradierConfig: EXIT_LADDER_COEXIST_ENABLED: bool = False; QuickConfig same field/default.",
              "default_identity": "False = today's elif ladder byte for byte (the call returns (closed, reason) unchanged); also requires DELTA_ENGINE_ENABLED for the max-hold leg (v12:17398) and WIN_TRAIL_EROSION_PCT>0 for the erosion leg.",
              "kill_switch": "EXIT_LADDER_COEXIST_ENABLED=False"}},
    {"switch": "UNCOND_DC_HARD_STOP_BUFFER_PCT", "kind": "float", "default": "0.25", "cands": ["0.25", "0.15", "0.35", "0.5"], "tab": "EXIT_STRUCTURAL", "stage": "EXIT_STOP", "risk": "exit (stop level)",
     "analogues": ["FIXED_STOP_BUFFER_PCT"], "elr_families": ("UNCOND_DC_LOW", "UNCOND_DC_HIGH"),
     "rationale": "UNCOND_DC_* hard stop buffer is hard-coded 0.25% (v12 0.9975/1.0025) with no switch surface; FIXED_STOP_BUFFER_PCT is the measured analogue (E7 worked example)",
     "spec": {"vector": "v12_quick_engine.py simulate_one UNCOND block :20543-20585 (`px <= _uh_llvl4 * 0.9975`, `px >= _uh_lvl4 * 1.0025`, same for 1h/15m). Insertion: replace the six literals by ONE factor pair computed once before the bar loop by a new pure function vec_decisions/uncond_dc_stop.py::buffer_factors(cfg) -> (0.9975, 1.0025) at the default.",
              "live_crypto": "ez_manage.py _pp_unconditional_hard_stops (:51688, runs before the VEC_EXACT delegation) and ez_positions_quick.py ULTIMATE_DC_HARD_STOP (:25882, check_exit_candidates_for_account :25522): same buffer_factors() call; the exchange-resting DC_HARD_STOP_15M stop order (ez_manage EXCHANGE_RESTING_STOPS :8282, USER 2026-10-16 'can NEVER be switched') keeps its own placement rule — this switch moves the software trigger only and can never disable any leg.",
              "live_stocks": "tradier_manage.py process_position UNCOND_DC_HARD_STOP block (:15690-15736, `short_dc_high_stop_fires` / long twin in vec_decisions) — pass the factor into the twin predicate.",
              "config": "config.py + config_tradier.py + QuickConfig: UNCOND_DC_HARD_STOP_BUFFER_PCT: float = 0.25 (domain 0-2, invalid -> 0.25).",
              "default_identity": "0.25 -> factors (0.9975, 1.0025) = today's literals byte for byte.",
              "kill_switch": "UNCOND_DC_HARD_STOP_BUFFER_PCT=0.25"}},
]
C_REENTRY_ARM = {"kind": "bool", "default": "False", "cands": ["False", "True"], "tab": "REENTRY_WINDOWED", "stage": "REENTRY", "risk": "entry (reentry admission)",
                 "spec": {"vector": "v12_quick_engine.py simulate_one reentry admission :19278-19285 (`if fire and _reentry_filter_on and has_closed_before and not entry_sig[i]` counts ALL active entry masks). Insertion: one call before that block, `fire = vec_decisions.reentry_mask_arm.armed_block(cfg, fire, has_closed_before, entry_sig[i], _fx_G['_entry_filter_masks_by_name'], i)` that ANDs only the named family's mask into reentry fires when REENTRY_MASK_ARM_<FAM>_ENABLED is True (independent of REENTRY_ENTRY_FILTER_ENABLED, which stays the all-mask lenient gate). Needs build_masks (:16640) to keep a name->mask dict next to the list.",
                          "live_crypto": "crypto reentry admission (bible live read of REENTRY_ENTRY_FILTER_ENABLED is the twin vec_decisions/twin_p0_crypto_b.py:849 reentry_fire_allowed) — extend that predicate with the per-family arm and call it from the ez_manage REENTRY open path before execute_now().",
                          "live_stocks": "tradier_manage.py process_position reentry filter (:19148 `_cfg_auto('REENTRY_ENTRY_FILTER_ENABLED', False)`): add the per-family check next to it.",
                          "config": "config.py + config_tradier.py + QuickConfig: REENTRY_MASK_ARM_<FAM>_ENABLED: bool = False.",
                          "default_identity": "False = the family's mask is not applied to reentries (today: reentries skip masks unless REENTRY_ENTRY_FILTER_ENABLED).",
                          "kill_switch": "REENTRY_MASK_ARM_<FAM>_ENABLED=False"}}


def c_reentry_families(ins, k=2):
    """The k ENTRY_TF_MASK filter families with the most interval-proven NEEDED rows in this cat_side (needed_filters)."""
    cls = {f: v.get("class") for f, v in ((ins["bm"].get("filters")) or {}).items()}
    cnt = collections.Counter()
    for rk, v in (ins["needed"] or {}).items():
        for h in v.get("needed") or []:
            f = h.split("=", 1)[0]
            iv = (v.get("intervals") or {}).get(h)
            if cls.get(f) == "ENTRY_TF_MASK" and iv and iv[0] is not None and iv[0] > 0:
                cnt[f] += 1
    return cnt.most_common(k)


def gen_c(ins, ev, refused):
    cs, bm = ins["cs"], ins["bm"]
    code = ins["code"]
    out = []
    designs = [dict(d) for d in C_DESIGNS]
    for fam, n_needed in c_reentry_families(ins):
        base = fam.replace("_FILTER_TF", "")
        d = dict(C_REENTRY_ARM)
        d["switch"] = f"REENTRY_MASK_ARM_{base}_ENABLED"
        d["filter_family"] = fam
        d["analogues"] = ["REENTRY_ENTRY_FILTER_ENABLED"]
        d["rationale"] = f"{fam} is an interval-proven NEEDED entry mask on {n_needed} rows of this cat_side; reentry rows read every entry mask as CONDITIONAL(REENTRY_MASKS_OFF) — arm this family alone"
        d["spec"] = {k: v.replace("<FAM>", base) for k, v in C_REENTRY_ARM["spec"].items()}
        designs.append(d)
    refused["stop_execution_no_vector_surface"] += len(STOP_EXEC)  # VEC_* execution families: live-only mechanics, never board switches (L1 law)
    for d in designs:
        sw = d["switch"]
        if sw in code["CRYPTO"] or sw in code["STOCKS"] or sw in code["QC"] or sw in ins["bible"]:
            refused["C_already_exists"] += 1
            continue
        evs = []
        for a in d.get("analogues") or []:
            card = ev.cards.get(a) or {}
            for cand in (card.get("cands") or {}):
                evs.append(ev.key(a, cand))
        if d.get("filter_family"):
            fm = [ev.filt(f"{d['filter_family']}={o}") for o in ((ins["features"].get("options") or {}).get(d["filter_family"]) or {})]
            evs.extend(x for x in fm if x["n"])
        best = max(evs, key=lambda e: (e["pos_syms"], e["n"]), default=None)
        n = sum(e["n"] for e in evs)
        pos = sum(e["pos"] for e in evs)
        p = shrunk(pos, n, ins["p0"])
        unlock = None
        if d.get("unlocks_reason"):
            unlock = sum(1 for r in (bm.get("rows") or {}).values() for c in (r.get("cells") or {}).values() if c.get("reason") == d["unlocks_reason"])
        elif d.get("filter_family"):
            fam_of = {k: v.get("family") for k, v in (bm.get("switches") or {}).items()}
            unlock = sum(1 for rk, r in (bm.get("rows") or {}).items() if not r.get("dead_row") and fam_of.get(rk.split("!", 1)[1].split("=", 1)[0]) == "REENTRY")
        elr = None
        if d.get("elr_families"):
            rows = ((ins["elr"].get("columns") or {}).get("board") or [])
            fams = [r for r in rows if str(r.get("family", "")).startswith(d["elr_families"])]
            if fams:
                elr = {"families": [r["family"] for r in fams], "trades": sum(r["n"] for r in fams), "losers": sum(r["losers"] for r in fams), "loss_sum_pct": round(sum(r["loss_sum_pct"] for r in fams), 4), "basis": "board ledgers, exit_loss_rank_<CS>.json (co-occurrence, not causal)"}
        out.append({"type": "C", "switch": sw, "cand": d["cands"][1], "candidates": d["cands"], "default": d["default"], "filter": None, "opt": None, "tab": d["tab"], "stage": d["stage"], "p_pos_pred": round(p, 5), "p_source": "counts_fallback:analogue_rows (no network score exists for an unwired switch)", "support": support(best["pos_syms"] if best else 0), "nearest_measured": best, "analogue_totals": {"n": n, "pos": pos}, "bind_verdict": {"verdict": "NEW_SWITCH", "reason": "no template row or map entry yet", "unlocks": unlock, "unlocks_what": (f"shaded cells now NEVER({d['unlocks_reason']})" if d.get("unlocks_reason") else ("non-dead REENTRY-family template rows on which this mask family would bind (today CONDITIONAL REENTRY_MASKS_OFF or unshaded)" if d.get("filter_family") else None))}, "analogue_cards": {a: {k: (ev.cards.get(a) or {}).get(k) for k in ("default", "state", "why", "n_evals", "n_syms")} for a in d.get("analogues") or []}, "exit_loss_evidence": elr, "rationale": d["rationale"], "risk": d["risk"], "wiring_needed": True, "wiring_spec": d["spec"]})
    return out


def _family(item, bm):
    s = (bm.get("switches") or {}).get(item["switch"]) or {}
    return s.get("family") or item.get("stage") or "UNKNOWN"


def _risk(item, bm):
    if item.get("risk"):
        return item["risk"]
    fam = _family(item, bm)
    return {"ENTRY": "entry", "REENTRY": "entry (reentry)", "AUGMENT": "sizing (augment)", "REDUCE": "sizing (reduce)", "SIZING": "sizing", "GLOBAL": "entry/global gate"}.get(fam, "exit" if fam.startswith("EXIT") else fam.lower())


def _rationale(item):
    if item.get("rationale"):
        return item["rationale"]
    nm = item.get("nearest_measured") or {}
    if item["type"] == "A":
        return f"{item.get('rule')}: {item['switch']} {item.get('bold')}->{item['cand']}; nearest {nm.get('key')} n={nm.get('n')} pos_syms={nm.get('pos_syms')}" + (f"; {item['note']}" if item.get("note") and "needs a template row" not in item["note"] else "")
    r, f = nm.get("row") or {}, nm.get("filter_marginal") or {}
    return f"unshaded BINDS pair ({item['bind_verdict'].get('reason')}); row pos_syms={r.get('pos_syms')} n={r.get('n')}; filter marginal pos_syms={f.get('pos_syms')} n={f.get('n')}" + ("; filter is interval-proven NEEDED on this row" if item.get("needed_listed") else "")


def select(cands, quota=20, min_b=5, max_c=5, min_a=5):
    """C first (<= max_c by score), >= min_b B, >= min_a A, rest by score over A u B.
    Caps: <= MAX_PER_SWITCH per switch, <= MAX_PER_FILTER per filter, one option per (switch, filter)."""
    for c in cands:
        c["score"] = round(float(c["p_pos_pred"]) * float(c["support"]), 6)
    by = lambda t: sorted((c for c in cands if c["type"] == t), key=lambda c: (-c["score"], c["switch"], str(c["cand"]), str(c.get("filter")), str(c.get("opt"))))
    sel, per_sw, per_f, per_sf = [], collections.Counter(), collections.Counter(), set()

    def take(c):
        if per_sw[c["switch"]] >= MAX_PER_SWITCH or (c.get("filter") and (per_f[c["filter"]] >= MAX_PER_FILTER or (c["switch"], c["filter"]) in per_sf)):
            return False
        sel.append(c)
        per_sw[c["switch"]] += 1
        if c.get("filter"):
            per_f[c["filter"]] += 1
            per_sf.add((c["switch"], c["filter"]))
        return True
    for c in by("C"):
        if sum(1 for s in sel if s["type"] == "C") >= min(max_c, quota):
            break
        take(c)
    for t, k in (("B", min_b), ("A", min_a)):
        for c in by(t):
            if sum(1 for s in sel if s["type"] == t) >= k or len(sel) >= quota:
                break
            take(c)
    rest = sorted((c for c in cands if c["type"] in ("A", "B") and c not in sel), key=lambda c: (-c["score"], c["switch"], str(c["cand"]), str(c.get("filter")), str(c.get("opt"))))
    for c in rest:
        if len(sel) >= quota:
            break
        take(c)
    sel.sort(key=lambda c: (-c["score"], c["type"]))
    return sel


def build_cat_side(ins, quota=20, min_b=5, max_c=5, predicted_at=None, min_a=5):
    cs = ins["cs"]
    predicted_at = predicted_at or utc_now()
    refused = collections.Counter()
    if ins.get("npz"):
        ins["p0"] = ins["npz"]["p0"]
    else:
        ins.setdefault("p0", 0.01)
    ev = Ev(ins, refused)
    tc = template_cands(ins["bm"])
    a = gen_a(ins, ev, tc, refused)
    b = gen_b(ins, ev, refused)
    c = gen_c(ins, ev, refused)
    sel = select(a + b + c, quota, min_b, max_c, min_a)
    for i, p in enumerate(sel):
        p["proposal_id"] = f"pv3-{cs}-{i + 1:02d}"
        p["cat_side"] = cs
        p["label"] = LABEL
        p["model"] = MODEL
        p["predicted_at"] = predicted_at
        p["family"] = _family(p, ins["bm"])
        p["stage"] = p.get("stage") or ((ins["bm"].get("switches") or {}).get(p["switch"]) or {}).get("stage")
        p["risk"] = _risk(p, ins["bm"])
        p["rationale"] = _rationale(p)
        p["queue"] = {"status": "NOT_RUN"} if p["type"] == "A" else ({"status": "NOT_QUEUEABLE", "why": "v15_switch_add installs switch ROWS; a yellow cell is a filter column on an existing row — no adder mode exists (TEMPLATE ONE-SCRIPT RULE: shading belongs to v15_template_bookkeeper)"} if p["type"] == "B" else {"status": "BLOCKED_UNTIL_WIRED", "why": "adder refuses unwired switches (not a config/QuickConfig field, no live reads) — wire per wiring_spec first (locked files), then --enqueue"})
    n_by = collections.Counter(x["type"] for x in sel)
    p_pred = [x["p_pos_pred"] for x in sel]
    meta = {"schema": SCHEMA, "cat_side": cs, "built_at": utc_now(), "predicted_at": predicted_at, "model": MODEL, "label": "every p_pos_pred / e_delta_pred here is a PREDICTION (linknet v3u score or a counts fallback, see p_source); nothing is a measurement and nothing is adopted except by the real engine under the gates",
            "quota": quota, "min_b": min_b, "max_c": max_c, "min_a": min_a, "selected": dict(n_by), "candidates": {"A": len(a), "B": len(b), "C": len(c)}, "refused": dict(sorted(refused.items())),
            "p0_cat_side": round(ins["p0"], 6), "scoring": f"score = p_pos_pred x support; support = {SUPPORT_FLOOR} + {1 - SUPPORT_FLOOR} x min(1, ln(1+pos_syms)/ln(1+{SUPPORT_FULL_AT})) of the nearest measured key (B: mean of row and filter-marginal support, 1.0 when the filter is interval-proven NEEDED on the row); counts fallback = Beta mean shrunk to p0 (m={M_SHRINK}); B fallback shrinks the row toward the filter's marginal rate",
            "selection": f"C first (<= {max_c}, all valid designs), >= {min_b} B, >= {min_a} A, rest by score over A u B; <= {MAX_PER_SWITCH} per switch, <= {MAX_PER_FILTER} per filter, one option per (switch, filter). The per-type floors are a choice of this tool: A is scored by a naked-row head and B by a cell head that inherits its row's sign (evidence base §2: lift ~ 0), so a raw cross-type ranking would always fill the quota with B",
            "p_pos_pred_range": [min(p_pred), max(p_pred)] if p_pred else None, "sources": ins["sources"], "template_md5_matches_binding_map": ins.get("template_md5_ok"),
            "caveats": ["STOCKS_LONG: the network loses to counts on never-tried pairs (design 22 §0/§4, AUC 0.639 vs 0.741) — p_pos_pred there is weaker than the counts it is ranked with" if cs == "STOCKS_LONG" else None, "STOCKS_SHORT: the 10-20 proposals were uncalibrated (p_pos_pred 0.74-0.87 vs realised < 1%); treat these as ranks, not probabilities" if cs == "STOCKS_SHORT" else None, "linknet v3u needs_row items all carry config_field=false (bible in_config unavailable on the training host); wiring is re-checked here from config.py/config_tradier.py/QuickConfig AST + SWITCH_BIBLE status"]}
    if ins.get("template_md5_ok") is False:
        meta["caveats"].append("the template changed after binding_map_<CS>.json was built (md5 differs): B 'unshaded' status and verdicts are as of the map build; rebuild the map (tools/v18_binding_map.py) before acting on B items")
    meta["caveats"] = [x for x in meta["caveats"] if x]
    return {"meta": meta, "proposals": sel}


def append_calibration(docs, path=CALIBRATION):
    """One line per proposal, deduped by (model, proposal_id, switch, cand, filter, opt); realised is filled by the next upgrade."""
    path = Path(path)
    seen = set()
    if path.exists():
        for ln in path.read_text().splitlines():
            try:
                r = json.loads(ln)
                seen.add((r.get("model"), r.get("proposal_id"), r.get("switch"), r.get("cand"), r.get("filter"), r.get("opt")))
            except ValueError:
                continue
    n = 0
    with open(path, "a") as fh:
        for d in docs:
            for p in d["proposals"]:
                k = (p["model"], p["proposal_id"], p["switch"], p["cand"], p.get("filter"), p.get("opt"))
                if k in seen:
                    continue
                fh.write(json.dumps({"predicted_at": p["predicted_at"], "model": p["model"], "cat_side": p["cat_side"], "proposal_id": p["proposal_id"], "type": p["type"], "switch": p["switch"], "cand": p["cand"], "filter": p.get("filter"), "opt": p.get("opt"), "p_pos_pred": p["p_pos_pred"], "p_source": p["p_source"], "label": LABEL, "realised": None}) + "\n")
                n += 1
    return n


def build(cat_side, quota=20, min_b=5, max_c=5, out_dir=LEARN, calibration=True, min_a=5):
    sides = CAT_SIDES if cat_side == "all" else (cat_side,)
    pa = utc_now()
    docs, res = [], {}
    for cs in sides:
        t0 = time.time()
        doc = build_cat_side(load_inputs(cs), quota, min_b, max_c, pa, min_a)
        p = Path(out_dir) / f"proposals_v3_{cs}.json"
        LC.write_json(p, doc)
        docs.append(doc)
        res[cs] = {"path": str(p), "selected": doc["meta"]["selected"], "candidates": doc["meta"]["candidates"], "secs": round(time.time() - t0, 1)}
    if calibration:
        res["calibration_appended"] = append_calibration(docs)
    return res


def queue_entries(doc):
    cs = doc["meta"]["cat_side"]
    venue, side = cs.split("_")
    out = []
    for p in doc["proposals"]:
        if p["type"] != "A":
            continue
        out.append({"switch": p["switch"], "tab": p["tab"], "venues": [venue], "sides": [side], "candidates": [p["bold"], p["cand"]], "default": p["bold"], "status": "PROPOSED", "source": "v18_switch_proposer " + LABEL, "proposal_id": p["proposal_id"], "queued_at": utc_now()})
    return out


def queue(cat_side, out_dir=LEARN, templates_dir=ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM", extend=True):
    """Dry-runs ONLY (never --apply): `--queue <file>` for the A items, then the single-mode `--extend` plan per A item.
    Results are recorded verbatim in each proposal's `queue` block."""
    sides = CAT_SIDES if cat_side == "all" else (cat_side,)
    res = {}
    py = sys.executable
    for cs in sides:
        pp = Path(out_dir) / f"proposals_v3_{cs}.json"
        doc = json.loads(pp.read_text())
        ents = queue_entries(doc)
        qf = Path(out_dir) / f"proposals_v3_queue_{cs}.json"
        LC.write_json(qf, {"version": 1, "doc": "v18_switch_proposer A-type proposals in tools/v15_switch_add.py --queue entry shape (dry-run only; NOT data/template_row_queue.json)", "entries": ents})
        sf = Path(out_dir) / f"proposals_v3_queue_{cs}.summary.json"
        cmd = [py, str(ROOT / "tools" / "v15_switch_add.py"), "--queue", str(qf), "--templates-dirs", str(templates_dir), "--summary-out", str(sf)]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=1800)
        summ = json.loads(sf.read_text()) if sf.exists() else {}
        qlog = (r.stdout + r.stderr).strip().splitlines()
        venue, side = cs.split("_")
        for p in doc["proposals"]:
            if p["type"] != "A":
                continue
            e = (summ.get("entries") or {}).get(p["switch"]) or {}
            q = {"queue_id": f"{qf.name}#{p['proposal_id']}", "queue_cmd": " ".join(cmd[1:]).replace(str(ROOT) + "/", ""), "queue_rc": r.returncode, "queue_refused": e.get("refused"), "queue_present": e.get("present"), "queue_planned": e.get("planned"),
                 "status": "QUEUE_REFUSED" if e.get("refused") else ("QUEUE_PRESENT_NOOP" if e.get("present") else ("QUEUE_PLANNED" if e.get("planned") else "QUEUE_NO_RESULT"))}
            if extend:
                ecmd = [py, str(ROOT / "tools" / "v15_switch_add.py"), "--switch", p["switch"], "--tab", p["tab"], "--candidates", f"{p['bold']},{p['cand']}", "--default", str(p["bold"]), "--venues", venue.lower(), "--sides", side.lower(), "--extend", "--templates-dir", str(templates_dir)]
                er = subprocess.run(ecmd, capture_output=True, text=True, cwd=str(ROOT), timeout=900)
                q["extend_cmd"] = " ".join(ecmd[1:]).replace(str(ROOT) + "/", "")
                q["extend_rc"] = er.returncode
                q["extend_out"] = [ln for ln in (er.stdout + er.stderr).strip().splitlines() if ln.strip()][-4:]
                q["status"] = q["status"] + ("+EXTEND_PLAN_OK" if er.returncode == 0 and "dry-run OK" in er.stdout else "+EXTEND_REFUSED")
            p["queue"] = q
        doc["meta"]["queue"] = {"ran_at": utc_now(), "queue_file": str(qf), "summary_file": str(sf), "rc": r.returncode, "log_tail": qlog[-6:], "mode": "dry-run only (no --apply): templates are LOCKED to the bookkeeper/adder on the S1 chain; Mac --apply is overwritten by the next pull"}
        LC.write_json(pp, doc)
        res[cs] = {"a_items": len(ents), "rc": r.returncode, "statuses": dict(collections.Counter(p["queue"]["status"] for p in doc["proposals"]))}
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--cat-side", required=True, choices=CAT_SIDES + ("all",))
    b.add_argument("--quota", type=int, default=20)
    b.add_argument("--min-b", type=int, default=5)
    b.add_argument("--max-c", type=int, default=5)
    b.add_argument("--min-a", type=int, default=5)
    b.add_argument("--out-dir", default=str(LEARN))
    b.add_argument("--no-calibration", action="store_true")
    q = sub.add_parser("queue")
    q.add_argument("--cat-side", required=True, choices=CAT_SIDES + ("all",))
    q.add_argument("--out-dir", default=str(LEARN))
    q.add_argument("--no-extend", action="store_true")
    a = ap.parse_args()
    if a.cmd == "build":
        print(json.dumps(build(a.cat_side, a.quota, a.min_b, a.max_c, Path(a.out_dir), not a.no_calibration, a.min_a), indent=1))
    else:
        print(json.dumps(queue(a.cat_side, Path(a.out_dir), extend=not a.no_extend), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
