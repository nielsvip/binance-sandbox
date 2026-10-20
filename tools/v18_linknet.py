#!/usr/bin/env python3
"""v18_linknet — the GENERALISING NETWORK of the V18 self-learning system (E8b, USER 2026-10-20: "a neural network of trillions
of links"). Numpy only. One model per cat_side, strictly apart (principle 13).

WHAT IT DOES. tools/v18_brain.py counts links that were measured (88% of a cat_side's template pairs were never tried as a
pair). This network predicts the outcome of EVERY link — (switch=cand), (switch=cand, filter=opt), in every regime, and
settings that have no template row yet — by generalising from the FEATURES of the keys (tools/v18_features.py: indicator
family, TF, role, direction, level, value bucket, tab, parents) instead of their names alone. Every number it emits is a
PREDICTION and is labelled so (`PREDICTED_NOT_MEASURED`); nothing here adopts anything — the real engine does.

CORPUS. The brain's own reader: the chronological spills written by `v18_brain.py extract` (delta logs of every host and
archive, rebuilt db ctx), sorted by `v18_brain.sorted_events`, deduped by the brain's 64-bit row key, routed by
`v18_brain.cat_side_of`. Units consumed (the brain's evidence units, nothing else):
  R   naked row: (SWITCH=cand | chain) -> delta, pos, d_tim / d_dd / d_wr vs the chain reference
  F   yellow cell: (SWITCH=cand, FILTER=opt | chain) -> marginal delta (cell - naked), pos, marginal metrics
  PH  single-key phase evals (SACRED_REPASS, FOCUS_GRID, ...) vs the then chain (training evidence; reported separately)
  FWD next-board persistence of a promotion (the brain's rule, CatBrain._resolve): y = 1 when no other cand of the switch is
      positive on the sym_side's next board and its own fresh naked (if any) is >= 0
Identity lines (`cached`) never become examples (the brain's assembler already collapses them).

MODEL (per cat_side). Factorisation machine + small MLP over three pooled embedding groups:
  S = setting features (ID: S.sw, S.set + ID-free attributes), F = filter-option features (F.fl, F.fo + attributes),
  C = context (sym_side, regime = board bh bucket, tier, kind R/F/PH, phase).
  logit P(pos) = w0 + sum w_i x_i + FM pairwise <v_i, v_j> x_i x_j + MLP_0([s, f, c, s*f, s*c, f*c])
  heads: P(pos), log1p E[delta | pos], d_tim, d_dd, d_wr (regression, scaled), P(forward persistence).
  Training: Adam, minibatch, ID dropout (switch/setting/filter IDs, sym_side, regime, tier are dropped at random so the
  ID-free path learns to predict keys it has never seen, and cat_side-level / regime-free scoring is in-distribution),
  early stopping on a CHRONOLOGICAL validation slice of boards. ID features never seen in the fit are masked at predict time.

HONEST EVALUATION (per cat_side). Boards ordered by start time: fit < q70, validation q70..q80 (early stopping), test =
boards started >= q80 (held out). The test model is refit on everything before q80 with the early-stopped epoch count.
Reported on the held-out boards: AUC of P(pos) and Spearman (p vs realised delta; key-level p vs realised pos rate) for R
and F, separately on pairs/keys NEVER seen before q80 (the generalisation claim), and cold-start (all IDs masked); each
against the brain's count-based P(pos) (Beta shrink m = v18_brain.M_SHRINK to the cat_side base rate; pair -> filter-option
-> prior backoff) computed on the SAME pre-q80 data. The scoring model is then refit on the whole corpus.

CLI
  dataset   --spill-dir D [--spill-dir D2] --tmp-dir T --out-dir data/learn/linknet        one pass, all four cat_sides
  train     --cat-side CS [...] --ds-dir data/learn/linknet                                   holdout report + final model
  score     --cat-side CS [...]   -> data/learn/linknet_scores_<CS>.npz + data/learn/linknet_proposals_<CS>.json
  calibrate --spill-dir NEW --since ISO                                                       -> data/learn/linknet_calibration.jsonl
Nothing here writes templates, configs, sheets, progress JSONs, gate flags or crontabs.
"""
from __future__ import annotations

import argparse
import array
import collections
import glob
import gzip
import json
import math
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V18_ROOT") or Path(__file__).resolve().parents[1])
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

from tools import v18_brain as VB  # noqa: E402  (the corpus reader: sorted_events, cat_side_of, regime_of, epoch_of, M_SHRINK)
from tools import v18_features as FT  # noqa: E402

SCHEMA = "v18_linknet/1"
CAT_SIDES = VB.CAT_SIDES
EPS = VB.EPS
LEARN_DIR = ROOT / "data" / "learn"
DS_DIR = LEARN_DIR / "linknet"
REGIMES = ("ALL", "FLAT", "UP", "DOWN", "STRONG_UP", "STRONG_DOWN")
KINDS = ("R", "F", "PH")
HEADS = ("p_pos", "log_e_delta_pos", "d_tim", "d_dd", "d_wr", "p_forward")
LS, LF, LC = 32, 32, 6
MSCALE = 5.0
PRED_LABEL = "PREDICTED_NOT_MEASURED"
EXCLUDE_PREFIX = ("ABLATION_",)
EXCLUDE_KEYS = frozenset({"MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED"})
PROPOSALS_K = int(os.environ.get("V18_PROPOSALS_K", "500"))
PROPOSALS_NEEDS_ROW_K = int(os.environ.get("V18_PROPOSALS_NEEDS_ROW_K", "50"))


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _log(*a):
    print(*a, flush=True)


STATUS_PATH = LEARN_DIR / "linknet_status.json"


def write_status(cat_side, stage, path=None, **fields):
    """data/learn/linknet_status.json: one block per cat_side, merged, atomic (progress visible before the handover)."""
    p = Path(path or STATUS_PATH)
    try:
        st = json.loads(p.read_text())
    except Exception:
        st = {}
    st.setdefault("definitions", {
        "rows_consumed": "real engine evals used as training examples (naked rows R + yellow cells F + single-key phase evals PH; identity rows dropped)",
        "nodes_embedded": "vocabulary entries with a learned embedding vector (switch/setting/filter/option IDs, their attributes, default-in-force relations, sym_sides, regimes, tiers, kinds)",
        "links_scored_explicit": "predictions actually computed and stored: every template (switch=cand) row and every template (switch=cand, filter=opt) cell x 6 regime contexts (ALL + 5 bh regimes), 5 heads each, in linknet_scores_<CS>.npz",
        "links_implied": "combinations that have a predicted value BY CONSTRUCTION of the factorisation (any of them can be computed on demand): settings x (filter options + none) x regimes x (sym_sides + none) x tiers(4); settings = every distinct switch=value the cat_side's corpus, template or beyond-template enumeration knows",
        "feature_pair_links": "pairwise FM interactions between embedded nodes = V*(V-1)/2",
        "label": "every number derived from these links is a PREDICTION, never a measurement"})
    blk = st.setdefault(cat_side, {})
    if "started" not in blk:
        blk["started"] = utc_now()
    blk.update(fields)
    blk["stage"] = stage
    blk["cat_side"] = cat_side
    blk["updated"] = utc_now()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1, default=str))
    os.replace(tmp, p)


def _tier(t):
    s = str(t or "").strip().upper()
    return {"W": "W", "WINNER": "W", "M": "M", "MEDIOCRE": "M", "L": "L", "LOSER": "L", "LOST_CAUSE": "L"}.get(s, "U")


# ------------------------------------------------------------------------------------------------ dataset

class _Cols:
    """Compact column store (array.array) of one cat_side's examples."""

    F_COLS = ("kind", "set_i", "filt_i", "sc_i", "fc_i", "inf_src", "ctx_i", "ts", "b_t0", "board_i", "y", "d", "valid", "m_tim", "m_dd", "m_wr", "w", "x_i", "prov")
    TYPES = {"kind": "b", "set_i": "i", "filt_i": "i", "sc_i": "i", "fc_i": "i", "inf_src": "b", "ctx_i": "i", "ts": "d", "b_t0": "d", "board_i": "i", "y": "b", "d": "f", "valid": "b",
             "m_tim": "f", "m_dd": "f", "m_wr": "f", "w": "f", "x_i": "i", "prov": "b"}
    DEFAULTS = {"x_i": -1, "prov": 0}

    def __init__(self):
        self.c = {k: array.array(self.TYPES[k]) for k in self.F_COLS}

    def add(self, **kw):
        for k in self.F_COLS:
            self.c[k].append(kw[k] if k in kw else self.DEFAULTS[k])

    def np(self):
        return {k: np.frombuffer(v, dtype={"b": np.int8, "i": np.int32, "d": np.float64, "f": np.float32}[v.typecode]).copy() for k, v in self.c.items()}


class Interner:
    def __init__(self):
        self.ix = {}
        self.keys = []

    def __call__(self, k):
        i = self.ix.get(k)
        if i is None:
            i = self.ix[k] = len(self.keys)
            self.keys.append(k)
        return i


class CatDS:
    """Examples of ONE cat_side, built in time order from the brain's events."""

    INF_SRC = {"T": 0, "P": 1, "U": 2, "Z": 3, "A": 4, "N": 5}
    # precision-extract chain codes (tools/v18_precision_extract.py, per-board two-pass resolution) -> our in-force source
    CK_MAP = {"P": "P", "B": "T", "BI": "T", "Z": "Z", "A": "A", "NB": "N", "U": "U"}

    PROV = {"log": 0, "native": 1}

    def __init__(self, cs, defaults=None, ctxj=None, enc=None):
        self.cs = cs
        self.ctxj = ctxj
        self.enc = enc if (enc is not None or ctxj is None) else FT.ContextEncoder(FT.load_context(cs))
        self.xctx = Interner()
        self.bpromo = collections.defaultdict(dict)
        self.last_base = {}
        self.xcache = {}
        self.board_t0 = {}
        self.ex = _Cols()
        self.sets, self.filts, self.ctxs, self.boards = Interner(), Interner(), Interner(), Interner()
        self.scombo, self.fcombo = Interner(), Interner()
        self.defaults = dict(defaults or {})
        self.inforce = collections.defaultdict(dict)
        self.fwd = collections.defaultdict(list)
        self.board_of = {}
        self.cur_promos = collections.defaultdict(dict)
        self.pending = {}
        self.sheet_of = {}
        self.c = collections.Counter()

    @staticmethod
    def _f(x):
        return float("nan") if x is None else float(x)

    def _ctx(self, kind, regime, sym, tier, ph="", prov="log"):
        return self.ctxs((kind, regime or "UNK", sym, _tier(tier), ph or "", prov))

    def _xctx(self, sym, base_ci):
        """chain context id: the store's chain set (base_ci) overlaid with this board's promotions so far (idempotent for the
        promotions the stored base already holds; adds the later ones, e.g. phases whose stored base froze after the main pass)."""
        if base_ci is None or self.ctxj is None:
            return -1
        promos = self.bpromo.get(sym) or {}
        ck = (base_ci, tuple(sorted(promos.items())))
        x = self.xcache.get(ck)
        if x is None:
            feats = self.ctxj["ctxs"][base_ci]
            if promos:
                d = {}
                for f in feats:
                    k, _, v = f[4:].partition("=")
                    d[k] = v
                d = self.enc.overlay(d, promos)
                feats = self.enc.feats(d)
            x = self.xcache[ck] = self.xctx(tuple(feats))
        return x

    def _base_of(self, ev, t):
        """store chain set of a delta-log row event: (sym_side, cum_before) -> the base set whose ts range holds (or is nearest to) t."""
        if self.ctxj is None:
            return None
        if str(ev.get("b") or "").startswith("NAT|"):
            ci = self.ctxj["natmap"].get(ev.get("rk"))
            self.c["ctx_native_joined" if ci is not None else "ctx_native_unjoined"] += 1
            return ci
        c = self.ctxj["cbmap"].get((ev["s"], _cb_key(ev.get("cb"))))
        if not c:
            self.c["ctx_log_unjoined"] += 1
            return None
        if len(c) == 1:
            self.c["ctx_log_joined"] += 1
            return c[0][0]
        best = min(c, key=lambda r: (0.0 if r[2] - 1 <= t <= r[3] + 1 else min(abs(t - r[2]), abs(t - r[3])), -r[1]))
        self.c["ctx_log_joined_ambiguous_cb"] += 1
        return best[0]

    def _new_board(self, sym, b):
        old = self.board_of.get(sym)
        if old == b:
            return
        if old is not None and str(b).rsplit("|", 1)[-1] < str(old).rsplit("|", 1)[-1]:
            return
        self._resolve(sym, b)
        self.pending[sym] = {sw: dict(v, alt=[], own=[]) for sw, v in self.cur_promos.pop(sym, {}).items()}
        self.board_of[sym] = b
        self.bpromo[sym] = {}
        self.last_base.pop(sym, None)

    def _resolve(self, sym, new_b):
        pend = self.pending.pop(sym, None)
        if not pend:
            return
        for sw, v in pend.items():
            if not v["alt"] and not v["own"]:
                self.c["fwd_unobserved"] += 1
                continue
            y = 1 if (max(v["alt"], default=-1e9) <= EPS and min(v["own"], default=0.0) >= -EPS) else 0
            t_res = VB.epoch_of(str(self.board_of.get(sym) or "").rsplit("|", 1)[-1]) or v["ts"]
            for k, val in (("set_i", v["set_i"]), ("ctx_i", v["ctx_i"]), ("ts", v["ts"]), ("t_res", t_res), ("y", y)):
                self.fwd[k].append(val)
            self.c["fwd_labels"] += 1
            self.c["fwd_pos"] += y

    def finish(self):
        for sym in list(self.pending):
            self._resolve(sym, None)

    def board_event(self, ev):
        t0 = VB.epoch_of(ev.get("t"))
        if t0 is not None and ev.get("b") is not None:
            self.board_t0.setdefault(ev["b"], t0)
        self._new_board(ev["s"], ev.get("b"))

    def _b_t0(self, b, t):
        """board start: the board key's own timestamp, else its board event's time (native contexts 'NAT|<hash>'), else the row ts."""
        return VB.epoch_of(str(b).rsplit("|", 1)[-1]) or self.board_t0.get(b) or t

    def in_force(self, sym, key):
        """DEFAULT IN FORCE of a key on this sym_side at this moment: its last promotion on the sym_side (P), else the
        template bold (T), else unknown (U). USER LAW 2026-10-20: targets are relative to it (the delta-log delta already is:
        it is measured against the chain), and candidate == in-force is identity (no label)."""
        v = self.inforce[sym].get(key)
        if v is not None:
            return v, "P"
        v = self.defaults.get(key)
        return (v, "T") if v is not None else (None, "U")

    def row(self, ev, ts):
        sym = ev["s"]
        b = ev.get("b")
        nat = str(b or "").startswith("NAT|")
        if nat and self.cs.startswith("STOCKS"):
            # coordinator 2026-10-20: stocks native cache rows are positive 6.57% / 3.10% vs 1.0% / 0.82% in the delta logs of
            # the same days -> not the same measurement; excluded from training and evaluation (crypto native: 1.93% vs 1.84%, kept)
            self.c["stocks_native_rows_excluded"] += 1
            return
        prov = "native" if nat else "log"
        cur = self.board_of.get(sym)
        foreign = False
        if cur != b:
            if cur is None or str(b).rsplit("|", 1)[-1] > str(cur).rsplit("|", 1)[-1]:
                self._new_board(sym, b)
            else:
                foreign = True
                self.c["rows_of_superseded_board"] += 1
        sheet, key = ev["sh"], ev["k"]
        sw, _, cand = key.partition("=")
        self.sheet_of.setdefault(sw, sheet)
        if sw in EXCLUDE_KEYS:
            return
        t = VB.epoch_of(ts) or 0.0
        b_t0 = self._b_t0(b, t)
        bi = self.boards(b)
        si = self.sets(f"{sheet}|{sw}={cand}")
        base = self._base_of(ev, t)
        if base is not None and not foreign:
            self.last_base[sym] = base
        xi = self._xctx(sym, base)
        if "ck" in ev:
            src = self.CK_MAP.get(ev.get("ck"), "U")
            inf = ev.get("cv") if src not in ("N", "U") else None
            self.c[f"inforce_pinned_{ev.get('ck')}"] += 1
        else:
            inf, src = self.in_force(sym, sw)
            self.c["inforce_inferred_causal"] += 1
        reg, tier = ev.get("g"), ev.get("ti")
        naked = ev.get("n")
        nd0 = naked[0] if (naked is not None and naked[1]) else None
        if src == "A" and inf is not None and str(cand) in str(inf).split("|"):
            inf = cand  # one of several zero-delta (inert) cands: identity with the chain
        if inf is not None and str(cand) == str(inf) and nd0 is not None and abs(nd0) > 1e-6:
            # the engine says the chain differs from the inferred default (the board's best-start set is not in the log):
            # the inference is contradicted -> in-force UNKNOWN, the eval is kept
            self.c["inforce_contradicted_by_nonzero_delta"] += 1
            inf, src = None, "U"
        sci = self.scombo(f"{sheet}|{sw}={cand}|INF={inf}|{src}")
        if naked is not None and naked[-1]:
            d = naked[0] if naked[1] else None
            y = 1 if (d is not None and d > EPS) else 0
            if inf is not None and str(cand) == str(inf):
                self.c["R_identity_cand_eq_inforce"] += 1
            else:
                self.ex.add(kind=0, set_i=si, filt_i=-1, sc_i=sci, fc_i=-1, inf_src=self.INF_SRC[src], ctx_i=self._ctx("R", reg, sym, tier, prov=prov), ts=t, b_t0=b_t0,
                            board_i=bi, y=y, d=self._f(d if d is not None else 0.0), valid=int(bool(naked[1])), m_tim=self._f(naked[3]),
                            m_dd=self._f(naked[5]), m_wr=self._f(naked[7]), w=float(naked[8] or 1.0), x_i=xi, prov=self.PROV[prov])
                self.c["R"] += 1
                self.c["R_with_context" if xi >= 0 else "R_without_context"] += 1
                self.c[f"R_prov_{prov}"] += 1
                self.c["R_pos"] += y
                self.c[f"R_inf_{src}"] += 1
        ci = None
        for c in ev.get("y") or []:
            hdr, md, valid = str(c[0]).strip(), c[1], c[2]
            fk = hdr.partition("=")[0]
            if fk in EXCLUDE_KEYS:
                continue
            if ci is None:
                ci = self._ctx("F", reg, sym, tier, prov=prov)
            fopt = hdr.partition("=")[2]
            finf, fsrc = self.in_force(sym, fk)
            if finf is not None and str(fopt) == str(finf):
                if md is not None and abs(md) > 1e-6:
                    self.c["F_inforce_contradicted_by_nonzero_marginal"] += 1
                    finf, fsrc = None, "U"
                else:
                    self.c["F_identity_opt_eq_inforce"] += 1
                    continue
            y = 1 if (valid and md is not None and md > EPS) else 0
            self.c[f"F_inf_{src}"] += 1
            self.ex.add(kind=1, set_i=si, filt_i=self.filts(hdr), sc_i=sci, fc_i=self.fcombo(f"{hdr}|INF={finf}|{fsrc}"), inf_src=self.INF_SRC[src], ctx_i=ci, ts=t, b_t0=b_t0, board_i=bi, y=y, d=self._f(md if md is not None else 0.0),
                        valid=int(bool(valid)), m_tim=self._f(c[4]), m_dd=self._f(c[6]), m_wr=self._f(c[8]), w=float(c[9] or 1.0), x_i=xi, prov=self.PROV[prov])
            self.c["F"] += 1
            self.c["F_with_context" if xi >= 0 else "F_without_context"] += 1
            self.c[f"F_prov_{prov}"] += 1
            self.c["F_pos"] += y
        pend = None if foreign else (self.pending.get(sym) or {}).get(sw)
        if pend is not None and naked is not None and naked[-1] and naked[1] and naked[0] is not None:
            (pend["own"] if cand == pend["cand"] else pend["alt"]).append(naked[0])
        if ev.get("p") and not foreign:
            self.cur_promos[sym][sw] = {"cand": cand, "set_i": sci, "ctx_i": self._ctx("R", reg, sym, tier), "ts": t}
            self.inforce[sym][sw] = cand
            self.bpromo[sym][sw] = cand
            self.c["promotions"] += 1

    def phase(self, ev, ts):
        sw, _, cand = str(ev["k"]).partition("=")
        if sw in EXCLUDE_KEYS:
            return
        sheet = ev.get("sh") or self.sheet_of.get(sw) or "NA"
        t = VB.epoch_of(ts) or 0.0
        d = ev.get("d")
        v = bool(ev.get("v"))
        y = 1 if (v and d is not None and d > EPS) else 0
        inf, src = self.in_force(ev["s"], sw)
        if inf is not None and str(cand) == str(inf):
            if d is not None and abs(d) > 1e-6:
                self.c["PH_inforce_contradicted_by_nonzero_delta"] += 1
                inf, src = None, "U"
            else:
                self.c["PH_identity_cand_eq_inforce"] += 1
                return
        m = ev.get("m") or [None] * 5
        b = self.board_of.get(ev["s"]) or f"{ev['s']}|{ts}"
        b_t0 = self._b_t0(b, t)
        xi = self._xctx(ev["s"], self.last_base.get(ev["s"]))
        self.ex.add(kind=2, set_i=self.sets(f"{sheet}|{sw}={cand}"), filt_i=-1, sc_i=self.scombo(f"{sheet}|{sw}={cand}|INF={inf}|{src}"), fc_i=-1,
                    inf_src=self.INF_SRC[src], ctx_i=self._ctx("PH", None, ev["s"], None, ev.get("ph")), ts=t,
                    b_t0=b_t0, board_i=self.boards(b), y=y, d=self._f(d if d is not None else 0.0), valid=int(v), m_tim=self._f(m[0]),
                    m_dd=self._f(m[2]), m_wr=self._f(m[4]), w=float(ev.get("w") or 1.0), x_i=xi, prov=0)
        self.c["PH"] += 1
        self.c["PH_with_context" if xi >= 0 else "PH_without_context"] += 1
        self.c["PH_pos"] += y

    def save(self, out_dir):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cols = self.ex.np()
        fw = {f"fwd_{k}": np.asarray(v, dtype=np.float64 if k in ("ts", "t_res") else np.int32) for k, v in self.fwd.items()}
        p = out_dir / f"ds_{self.cs}.npz"
        tmp = out_dir / f".ds_{self.cs}.tmp.npz"
        np.savez_compressed(tmp, **cols, **fw)
        os.replace(tmp, p)
        keys = {"cat_side": self.cs, "sets": self.sets.keys, "filts": self.filts.keys, "set_combos": self.scombo.keys, "filt_combos": self.fcombo.keys, "ctxs": [list(c) for c in self.ctxs.keys], "boards": self.boards.keys,
                "xctxs": [list(c) for c in self.xctx.keys], "counters": dict(self.c), "built_at": utc_now()}
        with gzip.open(out_dir / f"ds_{self.cs}_keys.json.gz", "wt") as fh:
            json.dump(keys, fh)
        return p


def _template_defaults(cs, a=None):
    """Template bold defaults of the cat_side (the in-force value of a key no promotion has changed). Tests inject them."""
    inj = getattr(a, "defaults", None) if a is not None else None
    if inj is not None:
        return inj.get(cs, {})
    try:
        from tools import v18_learn_common as C
        return dict(C.template_index(cs).get("defaults") or {})
    except Exception:
        return {}


def cmd_dataset(a):
    t0 = time.time()
    tmp = Path(a.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    srt = tmp / "events_sorted.tsv"
    files = VB.sorted_events(a.spill_dir, tmp, srt)
    t_sort = time.time() - t0
    ctx_dir = getattr(a, "ctx_dir", None)
    dss = {}
    for cs in CAT_SIDES:
        cj = load_ctxjoin(cs, ctx_dir) if ctx_dir else None
        if ctx_dir and cj is None:
            print(f"[dataset] {cs}: no ctxjoin in {ctx_dir} -> rows without chain context", flush=True)
        dss[cs] = CatDS(cs, defaults=_template_defaults(cs, a), ctxj=cj, enc=getattr(a, "encoders", {}).get(cs) if hasattr(a, "encoders") else None)
    seen = set()
    cnt = collections.Counter()
    n = 0
    first = last = None
    with open(srt) as fh:
        for line in fh:
            n += 1
            ts, _, js = line.partition("\t")
            if '"e":"row"' not in js[:12] and '"e":"phase"' not in js[:14] and '"e":"board"' not in js[:14]:
                cnt["skipped_event_type"] += 1
                continue
            try:
                ev = json.loads(js)
            except Exception:
                cnt["bad_event"] += 1
                continue
            rk = ev.get("rk")
            if rk is not None:
                if rk in seen:
                    cnt["dup_" + str(ev.get("e"))] += 1
                    continue
                seen.add(rk)
            cs = VB.cat_side_of(ev.get("s"))
            if cs is None:
                cnt["refused_cat_side"] += 1
                continue
            e = ev.get("e")
            if e == "row":
                dss[cs].row(ev, ts)
            elif e == "phase":
                dss[cs].phase(ev, ts)
            elif e == "board":
                dss[cs].board_event(ev)
            cnt[f"{e}_{cs}"] += 1
            first = first or ts
            last = ts
            if n % 2000000 == 0:
                print(f"[dataset] {n:,} lines {time.time() - t0:.0f}s", flush=True)
    out = {"files": files, "lines": n, "first_ts": first, "last_ts": last, "sort_secs": round(t_sort, 1), "counters": dict(cnt), "per_cat_side": {}}
    for cs, ds in dss.items():
        ds.finish()
        p = ds.save(a.out_dir)
        out["per_cat_side"][cs] = {"file": str(p), "bytes": p.stat().st_size, **dict(ds.c), "sets": len(ds.sets.keys), "filters": len(ds.filts.keys),
                                   "ctxs": len(ds.ctxs.keys), "boards": len(ds.boards.keys)}
    out["secs"] = round(time.time() - t0, 1)
    Path(a.out_dir, "dataset_report.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out["per_cat_side"], indent=1))
    if not a.keep_sorted:
        try:
            os.remove(srt)
        except OSError:
            pass
    return 0


# ------------------------------------------------------------------------------------------------ v2: chain context join (E8d)

def _cb_key(x):
    return None if x is None else round(float(x), 3)


def _vstr(v):
    if isinstance(v, bool):
        return "True" if v else "False"
    return str(v)


def build_ctx_join(cs, store, fctx=None, native=None, log=_log):
    """FULL SET IN FORCE of every eval of one cat_side, from the experience store (read-only). -> dict
      ctxs    [feature tuple]  every distinct chain context (ContextEncoder.feats of the set's diff vs the template bold)
      cbmap   {(sym_side, round(cum_before, 3)): [(ctx_id, n, ts_min, ts_max)]}   rebuilt + log rows: base_set_id = the chain
              the eval ran on (the pilot's set before the row's switch=cand); joined to delta-log events by sym_side + cum_before
      natmap  {rk: ctx_id}   crypto native rows: the PARENT set of each one-key child, rebuilt exactly as
              tools/v18_precision_extract.run_store does (same grouping, same rk = h64('NATR|X|P|switch')), so the precision
              events' native rows join by their own row key. Stocks native rows are not joined (coordinator 2026-10-20: they do
              not measure what the delta logs measure; 6.57% / 3.10% positive vs 1.0% / 0.82%) and are excluded from training.
    Nothing is written to the store."""
    import sqlite3
    t0 = time.time()
    fctx = fctx or FT.load_context(cs)
    enc = FT.ContextEncoder(fctx)
    native = cs.startswith("CRYPTO") if native is None else native
    ctxs = Interner()
    cbmap = collections.defaultdict(list)
    natmap = {}
    cnt = collections.Counter()
    base_ctx = {}
    for shard in sorted(Path(store).glob(f"experience_{cs}_*.db")):
        con = sqlite3.connect(f"file:{shard}?mode=ro", uri=True, timeout=600)
        rows = con.execute("SELECT sym_side, cum_before, base_set_id, count(*), min(ts), max(ts) FROM evals WHERE provenance IN ('rebuilt','log') "
                           "AND base_set_id IS NOT NULL AND cum_before IS NOT NULL GROUP BY sym_side, round(cum_before, 3), base_set_id").fetchall()
        cnt["cb_groups"] += len(rows)
        need = {r[2] for r in rows} - set(base_ctx)
        bases = {}

        def load_set(sid):
            row = con.execute("SELECT gz FROM sets WHERE set_id = ?", (sid,)).fetchone()
            if row is None:
                return None
            try:
                return json.loads(gzip.decompress(row[0]))
            except Exception:
                try:
                    return json.loads(row[0])
                except Exception:
                    return None
        for sid in need:
            s = load_set(sid)
            if s is None:
                cnt["base_set_missing"] += 1
                continue
            bases[sid] = enc.diff(s)
            base_ctx[sid] = ctxs(enc.feats(bases[sid]))
        for sym, cb, sid, n, tmin, tmax in rows:
            ci = base_ctx.get(sid)
            if ci is None:
                continue
            cbmap[(sym, _cb_key(cb))].append((ci, int(n), float(tmin or 0), float(tmax or 0)))
            cnt["evals_with_chain_set"] += int(n)
        if native:
            groups = collections.defaultdict(dict)
            nb = set()
            q = ("SELECT eval_id, sym_side, base_set_id, window_days, offset_days, bh_pct, bars, delta, gain FROM evals "
                 "WHERE provenance='native' AND delta_rule='explicit'")
            for eid, ss, bid, wd, od, bh, bars, delta, gain in con.execute(q):
                if gain is None:
                    continue
                try:
                    dj = json.loads(delta) if delta else {}
                except ValueError:
                    continue
                items = [(k, _vstr(v)) for k, v in (dj.get("set") or {}).items() if k not in VB.DENY]
                items += [(k, "<absent>") for k in (dj.get("del") or [])]
                fs = frozenset(items)
                g = groups[(ss, bid, wd, od, None if bh is None else round(float(bh), 6), bars)]
                if fs not in g:
                    g[fs] = eid
                    nb.add(bid)
            ndiff = {}
            for bid in nb:
                s = load_set(bid)
                ndiff[bid] = enc.diff(s) if s is not None else None
            pcache = {}
            for ctx, g in groups.items():
                bd = ndiff.get(ctx[1])
                if bd is None:
                    cnt["native_base_missing_groups"] += 1
                    continue
                for fs, x_eid in g.items():
                    for it in fs:
                        par = fs - {it}
                        p_eid = g.get(par)
                        if p_eid is None:
                            continue
                        ck = (ctx[1], par)
                        ci = pcache.get(ck)
                        if ci is None:
                            ci = pcache[ck] = ctxs(enc.feats(enc.overlay(bd, dict(par))))
                        natmap[VB.h64(f"NATR|{x_eid}|{p_eid}|{it[0]}")] = ci
                        cnt["native_one_key_children"] += 1
            cnt["native_groups"] += len(groups)
            del groups
        con.close()
        log(f"[ctxjoin] {cs} {shard.name}: {dict(cnt)} ctxs {len(ctxs.keys):,} {time.time() - t0:.0f}s")
    sizes = np.array([len(c) for c in ctxs.keys]) if ctxs.keys else np.zeros(1)
    rep = {"cat_side": cs, "store": str(store), "contexts": len(ctxs.keys), "cb_keys": len(cbmap), "native_children": len(natmap),
           "ignored_description_values": {"keys": len(enc.ignored_values), "values": int(sum(enc.ignored_values.values())), "top": dict(enc.ignored_values.most_common(10))},
           "context_size_mean": round(float(sizes.mean()), 1), "context_size_p50": float(np.median(sizes)), "context_size_max": int(sizes.max()),
           "counters": dict(cnt), "secs": round(time.time() - t0, 1), "built_at": utc_now()}
    return {"ctxs": ctxs.keys, "cbmap": dict(cbmap), "natmap": natmap, "report": rep}


def cmd_ctxjoin(a):
    import pickle
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    reps = {}
    for cs in a.cat_side or CAT_SIDES:
        j = build_ctx_join(cs, a.store)
        p = out_dir / f"ctxjoin_{cs}.pkl.gz"
        tmp = out_dir / f".ctxjoin_{cs}.tmp"
        with gzip.open(tmp, "wb", compresslevel=3) as fh:
            pickle.dump(j, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, p)
        reps[cs] = dict(j["report"], file=str(p), bytes=p.stat().st_size)
        print(json.dumps(reps[cs]), flush=True)
    (out_dir / "ctxjoin_report.json").write_text(json.dumps(reps, indent=1))
    return 0


def load_ctxjoin(cs, ctx_dir):
    import pickle
    p = Path(ctx_dir) / f"ctxjoin_{cs}.pkl.gz"
    if not p.exists():
        return None
    with gzip.open(p, "rb") as fh:
        return pickle.load(fh)


def load_ds(cs, ds_dir=DS_DIR):
    z = np.load(Path(ds_dir) / f"ds_{cs}.npz")
    d = {k: z[k] for k in z.files}
    with gzip.open(Path(ds_dir) / f"ds_{cs}_keys.json.gz", "rt") as fh:
        keys = json.load(fh)
    return d, keys


# ------------------------------------------------------------------------------------------------ features -> tables

class Featurizer:
    """Key strings -> padded (idx, x) tables over a vocabulary. Slot 0/1 of S and F are the ID features; slot 0 of C = sym_side."""

    def __init__(self, cs, fctx=None):
        self.cs = cs
        self.fctx = fctx or FT.load_context(cs)
        self.vocab = {"<pad>": 0}
        self.names = ["<pad>"]
        self.unknown_tokens = collections.Counter()

    def vid(self, f):
        i = self.vocab.get(f)
        if i is None:
            i = self.vocab[f] = len(self.names)
            self.names.append(f)
        return i

    @staticmethod
    def split_combo(key):
        """'TAB|SW=cand|INF=v|P' -> (base 'TAB|SW=cand', inf, src); a bare key -> (key, None, None) (scored vs the template bold)."""
        if "|INF=" in key:
            base, _, rest = key.partition("|INF=")
            inf, _, src = rest.rpartition("|")
            return base, (None if inf == "None" else inf), src
        return key, None, None

    def set_feats(self, key):
        base, inf, src = self.split_combo(key)
        tab, _, kv = base.partition("|")
        sw, _, cand = kv.partition("=")
        if src is None:
            inf = self.fctx.default(sw)
            src = "T" if inf is not None else "U"
        a = FT.name_attrs(sw, self.fctx, tab=None if tab == "NA" else tab)
        self.unknown_tokens.update(a["unknown_tokens"])
        f = FT.features(sw, cand, self.fctx, tab=None if tab == "NA" else tab, attrs=a)
        return f[:2] + FT.relation_features(sw, cand, inf, src, self.fctx) + f[2:]

    def filt_feats(self, hdr):
        base, inf, src = self.split_combo(hdr)
        f_, _, o = base.partition("=")
        if src is None:
            inf = self.fctx.default(f_, is_filter=True)
            src = "T" if inf is not None else "U"
        a = FT.name_attrs(f_, self.fctx, is_filter=True)
        self.unknown_tokens.update(a["unknown_tokens"])
        f = FT.features(f_, o, self.fctx, is_filter=True, attrs=a)
        return f[:2] + FT.relation_features(f_, o, inf, src, self.fctx, is_filter=True) + f[2:]

    @staticmethod
    def ctx_feats(ctx):
        kind, reg, sym, tier, ph = ctx[:5]
        prov = ctx[5] if len(ctx) > 5 else "log"
        out = [("C.sym:" + sym, 1.0) if sym else ("<pad>", 0.0), ("C.reg:" + reg, 1.0) if reg and reg != "ALL" else ("<pad>", 0.0),
               ("C.tier:" + tier, 1.0) if tier and tier != "U" else ("<pad>", 0.0), ("C.kind:" + kind, 1.0), ("C.ph:" + ph, 1.0) if ph else ("<pad>", 0.0),
               ("C.prov:" + prov, 1.0) if prov and prov != "log" else ("<pad>", 0.0)]
        return out

    def table(self, feat_lists, L):
        n = len(feat_lists)
        idx = np.zeros((n, L), dtype=np.int32)
        x = np.zeros((n, L), dtype=np.float32)
        for r, fl in enumerate(feat_lists):
            for j, (f, v) in enumerate(fl[:L]):
                if f == "<pad>":
                    continue
                idx[r, j] = self.vid(f)
                x[r, j] = v
        return idx, x


def seen_values(keys_sets):
    out = collections.defaultdict(list)
    for k in keys_sets:
        k = Featurizer.split_combo(k)[0]
        _tab, _, kv = k.partition("|")
        sw, _, cand = kv.partition("=")
        if cand not in out[sw]:
            out[sw].append(cand)
    return out


# ------------------------------------------------------------------------------------------------ the network

def sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


class LinkNet:
    def __init__(self, V, k=16, H=64, T=len(HEADS), seed=0):
        rng = np.random.default_rng(seed)
        self.k, self.H, self.T, self.V = k, H, T, V
        self.p = {"E": (rng.normal(0, 0.01, (V, k))).astype(np.float32), "w": np.zeros(V, np.float32),
                  "W1": (rng.normal(0, math.sqrt(2.0 / (6 * k)), (6 * k, H))).astype(np.float32), "b1": np.zeros(H, np.float32),
                  "W2": (rng.normal(0, 0.05, (H, T))).astype(np.float32), "b2": np.zeros(T, np.float32)}
        self.p["E"][0] = 0.0
        self.m = {n: np.zeros_like(v) for n, v in self.p.items()}
        self.v = {n: np.zeros_like(v) for n, v in self.p.items()}
        self.t = 0
        self.seen = np.ones(V, dtype=np.float32)

    def copy_params(self):
        return {n: v.copy() for n, v in self.p.items()}

    # pooled group terms: s (B,k), sq (B,k), lin (B,)
    def pool(self, idx, x):
        e = self.p["E"][idx] * (x * self.seen[idx])[..., None]
        return e.sum(1), (e * e).sum(1), (self.p["w"][idx] * x * self.seen[idx]).sum(1)

    def heads(self, s, f, c, sq, lin):
        tot = s + f + c
        fm = 0.5 * (tot * tot - sq).sum(1)
        h0 = np.concatenate([s, f, c, s * f, s * c, f * c], 1)
        a1 = np.maximum(h0 @ self.p["W1"] + self.p["b1"], 0.0)
        out = a1 @ self.p["W2"] + self.p["b2"]
        out[:, 0] += fm + lin
        return out

    def predict_tables(self, S, F, C):
        """S/F/C = (idx, x) arrays with the same B. -> out (B,T) raw heads."""
        s, sqs, ls = self.pool(*S)
        f, sqf, lf = self.pool(*F)
        c, sqc, lc = self.pool(*C)
        return self.heads(s, f, c, sqs + sqf + sqc, ls + lf + lc)

    def forward_train(self, idx, x, gs, gf):
        E = self.p["E"]
        e = E[idx] * x[..., None]
        s, f, c = e[:, :gs].sum(1), e[:, gs:gf].sum(1), e[:, gf:].sum(1)
        tot = s + f + c
        sq = (e * e).sum(1)
        fm = 0.5 * (tot * tot - sq).sum(1)
        lin = (self.p["w"][idx] * x).sum(1)
        h0 = np.concatenate([s, f, c, s * f, s * c, f * c], 1)
        z1 = h0 @ self.p["W1"] + self.p["b1"]
        a1 = np.maximum(z1, 0.0)
        out = a1 @ self.p["W2"] + self.p["b2"]
        out[:, 0] += fm + lin
        return out, (idx, x, e, s, f, c, tot, h0, z1, a1, gs, gf)

    def backward(self, g, cache, l2=1e-6):
        idx, x, e, s, f, c, tot, h0, z1, a1, gs, gf = cache
        k = self.k
        P = self.p
        G = {}
        G["W2"] = a1.T @ g
        G["b2"] = g.sum(0)
        dz1 = (g @ P["W2"].T) * (z1 > 0)
        G["W1"] = h0.T @ dz1
        G["b1"] = dz1.sum(0)
        dh0 = dz1 @ P["W1"].T
        dsf, dsc, dfc = dh0[:, 3 * k:4 * k], dh0[:, 4 * k:5 * k], dh0[:, 5 * k:6 * k]
        ds = dh0[:, :k] + dsf * f + dsc * c
        df = dh0[:, k:2 * k] + dsf * s + dfc * c
        dc = dh0[:, 2 * k:3 * k] + dsc * s + dfc * f
        g0 = g[:, 0]
        de = g0[:, None, None] * (tot[:, None, :] - e)
        de[:, :gs] += ds[:, None, :]
        de[:, gs:gf] += df[:, None, :]
        de[:, gf:] += dc[:, None, :]
        dE = de * x[..., None]
        fi = idx.ravel()
        gE = np.empty((self.V, k), np.float32)
        flat = dE.reshape(-1, k)
        for j in range(k):
            gE[:, j] = np.bincount(fi, weights=flat[:, j], minlength=self.V)
        G["E"] = gE
        G["w"] = np.bincount(fi, weights=(g0[:, None] * x).ravel(), minlength=self.V).astype(np.float32)
        for n in ("W1", "W2"):
            G[n] = G[n] + l2 * P[n]
        G["E"] += l2 * P["E"]
        return G

    def adam(self, G, lr=2e-3, b1=0.9, b2=0.999, eps=1e-8):
        self.t += 1
        for n, g in G.items():
            g = g.astype(np.float32)
            self.m[n] = b1 * self.m[n] + (1 - b1) * g
            self.v[n] = b2 * self.v[n] + (1 - b2) * g * g
            mh = self.m[n] / (1 - b1 ** self.t)
            vh = self.v[n] / (1 - b2 ** self.t)
            self.p[n] -= (lr * mh / (np.sqrt(vh) + eps)).astype(np.float32)
        self.p["E"][0] = 0.0
        self.p["w"][0] = 0.0

    def save(self, path, meta):
        np.savez_compressed(path, **{f"p_{n}": v for n, v in self.p.items()}, seen=self.seen, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))

    @classmethod
    def load(cls, path):
        z = np.load(path)
        E = z["p_E"]
        net = cls(E.shape[0], k=E.shape[1], H=z["p_b1"].shape[0], T=z["p_b2"].shape[0])
        for n in net.p:
            net.p[n] = z[f"p_{n}"]
        net.seen = z["seen"]
        meta = json.loads(bytes(z["meta"]).decode())
        return net, meta


def loss_grad(out, Y, M, lam=(1.0, 0.5, 0.2, 0.2, 0.2, 1.0)):
    """BCE on heads 0 and 5, MSE on heads 1-4. M = per-head mask (incl. sample weight). -> (loss, dL/dout)."""
    g = np.zeros_like(out)
    loss = 0.0
    for h in range(out.shape[1]):
        m = M[:, h]
        sm = float(m.sum())
        if sm <= 0:
            continue
        if h in (0, 5):
            p = sigmoid(out[:, h])
            y = Y[:, h]
            loss += lam[h] * float(-(m * (y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))).sum() / sm)
            g[:, h] = lam[h] * m * (p - y) / sm
        else:
            r = out[:, h] - Y[:, h]
            loss += lam[h] * float((m * r * r).sum() / sm)
            g[:, h] = lam[h] * 2.0 * m * r / sm
    return loss, g


# ------------------------------------------------------------------------------------------------ metrics

def fit_platt(p, y, iters=50):
    """Platt scaling on logit(p): -> (a, b) with P = sigmoid(a * logit(p) + b), Newton steps on the log-loss. Fitted on the
    HELD-OUT boards of the refit model (never on its own training rows)."""
    p = np.clip(np.asarray(p, np.float64), 1e-7, 1 - 1e-7)
    y = np.asarray(y, np.float64)
    if len(p) < 50 or y.sum() < 5 or y.sum() > len(y) - 5:
        return 1.0, 0.0
    z = np.log(p / (1 - p))
    a, b = 1.0, 0.0
    for _ in range(iters):
        q = sigmoid(a * z + b)
        g = np.array([((q - y) * z).sum(), (q - y).sum()])
        w = q * (1 - q) + 1e-12
        H = np.array([[(w * z * z).sum(), (w * z).sum()], [(w * z).sum(), w.sum()]]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, g)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(a), float(b)


def apply_platt(p, ab):
    a, b = ab
    p = np.clip(np.asarray(p, np.float64), 1e-7, 1 - 1e-7)
    return sigmoid(a * np.log(p / (1 - p)) + b)


def rankdata(a):
    a = np.asarray(a, dtype=np.float64)
    sorter = np.argsort(a, kind="mergesort")
    inv = np.empty(sorter.size, dtype=np.intp)
    inv[sorter] = np.arange(sorter.size)
    s = a[sorter]
    obs = np.r_[True, s[1:] != s[:-1]]
    dense = obs.cumsum()[inv]
    cnt = np.r_[np.nonzero(obs)[0], len(obs)]
    return 0.5 * (cnt[dense] + cnt[dense - 1] + 1)


def auc(score, y):
    y = np.asarray(y) > 0.5
    npos, nneg = int(y.sum()), int((~y).sum())
    if not npos or not nneg:
        return None
    r = rankdata(score)
    return round(float((r[y].sum() - npos * (npos + 1) / 2.0) / (npos * nneg)), 4)


def spearman(a, b):
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    if len(a) < 3:
        return None
    ra, rb = rankdata(a), rankdata(b)
    ra -= ra.mean()
    rb -= rb.mean()
    den = math.sqrt(float((ra * ra).sum()) * float((rb * rb).sum()))
    return round(float((ra * rb).sum() / den), 4) if den > 0 else None


# ------------------------------------------------------------------------------------------------ training

class Trainer:
    """One cat_side: tables, splits, count baseline, fit with early stopping, holdout report, final refit."""

    def __init__(self, cs, ds_dir=DS_DIR, k=16, H=64, batch=4096, lr=2e-3, max_epochs=12, patience=2, seed=7, max_train=None, log=_log, fctx=None):
        self.cs, self.k, self.H, self.batch, self.lr, self.max_epochs, self.patience, self.seed = cs, k, H, batch, lr, max_epochs, patience, seed
        self.log = log
        self.max_train = max_train
        self.D, self.K = load_ds(cs, ds_dir)
        self.fz = Featurizer(cs, fctx=fctx)
        for sw, vals in seen_values(self.K["sets"]).items():
            if sw not in self.fz.fctx.cands:
                self.fz.fctx.add_seen_values(sw, vals)
        self.TS = self.fz.table([self.fz.set_feats(k) for k in self.K["set_combos"]], LS)
        filt_lists = [[]] + [self.fz.filt_feats(h) for h in self.K["filt_combos"]]
        self.TF = self.fz.table(filt_lists, LF)
        self.TC = self.fz.table([Featurizer.ctx_feats(tuple(c)) for c in self.K["ctxs"]], LC)
        self.reg_slot_vals = None
        self._targets()

    # -- targets / masks
    def _targets(self):
        D = self.D
        n = len(D["kind"])
        Y = np.zeros((n, len(HEADS)), np.float32)
        M = np.zeros((n, len(HEADS)), np.float32)
        y = D["y"].astype(np.float32)
        w = np.clip(D["w"], 0.0, 1000.0)
        Y[:, 0] = y
        M[:, 0] = w
        dpos = np.where(y > 0, np.log1p(np.maximum(D["d"], 0.0)), 0.0)
        Y[:, 1] = dpos
        M[:, 1] = y * w
        valid = D["valid"] > 0
        for h, col in ((2, "m_tim"), (3, "m_dd"), (4, "m_wr")):
            v = D[col]
            ok = valid & np.isfinite(v)
            Y[:, h] = np.where(ok, np.clip(np.nan_to_num(v), -10, 10) / MSCALE, 0.0)
            M[:, h] = ok * w
        self.Y, self.M = Y, M
        nf = len(D.get("fwd_y", []))
        self.FY = np.zeros((nf, len(HEADS)), np.float32)
        self.FM = np.zeros((nf, len(HEADS)), np.float32)
        if nf:
            self.FY[:, 5] = D["fwd_y"]
            self.FM[:, 5] = 1.0

    # -- splits (chronological by board start)
    def splits(self, q_val=0.70, q_test=0.80):
        D = self.D
        bt = D["b_t0"]
        boards_t0 = np.unique(np.round(bt[D["kind"] < 2]))
        cut_val = float(np.quantile(boards_t0, q_val))
        cut_test = float(np.quantile(boards_t0, q_test))
        ts = D["ts"]
        fit = (bt < cut_val) & (ts < cut_val)
        val = (bt >= cut_val) & (bt < cut_test) & (ts < cut_test) & (D["kind"] < 2)
        pre_test = (bt < cut_test) & (ts < cut_test)
        test = (bt >= cut_test) & (D["kind"] < 2)
        test_ph = (ts >= cut_test) & (D["kind"] == 2)
        self.cut_val, self.cut_test = cut_val, cut_test
        f_ts = D.get("fwd_ts", np.zeros(0))
        f_res = D.get("fwd_t_res", np.zeros(0))
        fsp = {"fit": (f_res < cut_val), "pre_test": (f_res < cut_test), "test": (f_ts >= cut_test)}
        return {"fit": fit, "val": val, "pre_test": pre_test, "test": test, "test_ph": test_ph, "all": np.ones(len(ts), bool)}, fsp

    # -- batches
    def _gather(self, rows, fwd_rows=None, drop=True, rng=None):
        D = self.D
        si, fi, ci = D["sc_i"][rows], D["fc_i"][rows] + 1, D["ctx_i"][rows]
        Si, Sx = self.TS[0][si], self.TS[1][si].copy()
        Fi, Fx = self.TF[0][fi], self.TF[1][fi].copy()
        Ci, Cx = self.TC[0][ci], self.TC[1][ci].copy()
        Y, M = self.Y[rows], self.M[rows]
        if fwd_rows is not None and len(fwd_rows):
            fs, fc = D["fwd_set_i"][fwd_rows], D["fwd_ctx_i"][fwd_rows]
            Si = np.concatenate([Si, self.TS[0][fs]])
            Sx = np.concatenate([Sx, self.TS[1][fs]])
            Fi = np.concatenate([Fi, self.TF[0][np.zeros(len(fs), np.int64)]])
            Fx = np.concatenate([Fx, self.TF[1][np.zeros(len(fs), np.int64)]])
            Ci = np.concatenate([Ci, self.TC[0][fc]])
            Cx = np.concatenate([Cx, self.TC[1][fc]])
            Y = np.concatenate([Y, self.FY[fwd_rows]])
            M = np.concatenate([M, self.FM[fwd_rows]])
        if drop and rng is not None:
            B = len(Sx)
            for X, slot, p in ((Sx, 0, 0.15), (Sx, 1, 0.30), (Sx, 2, 0.30), (Fx, 0, 0.15), (Fx, 1, 0.30), (Fx, 2, 0.30), (Cx, 0, 0.35), (Cx, 1, 0.25), (Cx, 2, 0.35)):
                X[rng.random(B) < p, slot] = 0.0
        idx = np.concatenate([Si, Fi, Ci], 1)
        x = np.concatenate([Sx, Fx, Cx], 1)
        return idx, x, Y, M

    def predict(self, net, rows, cold=False, chunk=65536):
        D = self.D
        out = np.zeros((len(rows), len(HEADS)), np.float32)
        for a in range(0, len(rows), chunk):
            r = rows[a:a + chunk]
            si, fi, ci = D["sc_i"][r], D["fc_i"][r] + 1, D["ctx_i"][r]
            S = (self.TS[0][si], self.TS[1][si].copy())
            F = (self.TF[0][fi], self.TF[1][fi].copy())
            Cc = (self.TC[0][ci], self.TC[1][ci].copy())
            if cold:
                S[1][:, :3] = 0.0
                F[1][:, :3] = 0.0
                Cc[1][:, 0] = 0.0
            out[a:a + len(r)] = net.predict_tables(S, F, Cc)
        return out

    def predict_fwd(self, net, frows):
        D = self.D
        fs, fc = D["fwd_set_i"][frows], D["fwd_ctx_i"][frows]
        z = np.zeros(len(fs), np.int64)
        return net.predict_tables((self.TS[0][fs], self.TS[1][fs]), (self.TF[0][z], self.TF[1][z]), (self.TC[0][fc], self.TC[1][fc]))

    def _seen_mask(self, net, rows):
        """Vocabulary entries that occur in the fit rows (ID features never seen are masked at predict time)."""
        D = self.D
        used = np.zeros(net.V, bool)
        for tab, col, off in ((self.TS, "sc_i", 0), (self.TF, "fc_i", 1), (self.TC, "ctx_i", 0)):
            u = np.unique(D[col][rows] + off)
            ii = tab[0][u]
            xx = tab[1][u]
            used[ii[xx != 0]] = True
        used[0] = False
        net.seen = used.astype(np.float32)

    def fit(self, rows, epochs=None, val_rows=None, fwd_rows=None, tag="fit"):
        rng = np.random.default_rng(self.seed)
        V = len(self.fz.names)
        net = LinkNet(V, k=self.k, H=self.H, seed=self.seed)
        rows = np.asarray(rows)
        if self.max_train and len(rows) > self.max_train:
            rows = np.sort(rng.choice(rows, self.max_train, replace=False))
        fwd_rows = np.asarray(fwd_rows if fwd_rows is not None else [], dtype=np.int64)
        gs, gf = LS, LS + LF
        best, best_ep, bad, hist = None, 0, 0, []
        n_ep = epochs or self.max_epochs
        t0 = time.time()
        for ep in range(1, n_ep + 1):
            perm = rng.permutation(len(rows))
            tot_l, nb = 0.0, 0
            net.seen = np.ones(V, np.float32)
            for a in range(0, len(perm), self.batch):
                r = rows[perm[a:a + self.batch]]
                fr = rng.choice(fwd_rows, min(len(fwd_rows), 128), replace=True) if len(fwd_rows) else None
                idx, x, Y, M = self._gather(r, fr, drop=True, rng=rng)
                out, cache = net.forward_train(idx, x, gs, gf)
                l, g = loss_grad(out, Y, M)
                net.adam(net.backward(g, cache), lr=self.lr)
                tot_l += l
                nb += 1
            self._seen_mask(net, rows)
            rec = {"epoch": ep, "loss": round(tot_l / max(1, nb), 5), "secs": round(time.time() - t0, 1)}
            if val_rows is not None and len(val_rows):
                pv = self.predict(net, val_rows)[:, 0]
                rec["val_auc"] = auc(pv, self.D["y"][val_rows])
                score = rec["val_auc"] or 0.0
                if best is None or score > best[0] + 1e-4:
                    best, best_ep, bad = (score, net.copy_params(), net.seen.copy()), ep, 0
                else:
                    bad += 1
            hist.append(rec)
            self.log(f"[linknet] {self.cs} {tag} {rec}")
            if val_rows is not None and len(val_rows) and bad >= self.patience:
                break
        if best is not None:
            net.p = best[1]
            net.seen = best[2]
        return net, (best_ep or n_ep), hist

    # -- count baseline (v18_brain shrinkage on the same pre-test data)
    def count_baseline(self, fit_rows, eval_rows):
        D = self.D
        m = VB.M_SHRINK
        out = np.zeros(len(eval_rows), np.float64)
        e_pos = np.zeros(len(eval_rows), np.float64)
        n_out = np.zeros(len(eval_rows), np.float64)
        p_out = np.zeros(len(eval_rows), np.float64)
        pr_out = np.zeros(len(eval_rows), np.float64)
        kind_f = D["kind"][fit_rows]
        y_f = D["y"][fit_rows].astype(np.float64)
        d_f = D["d"][fit_rows].astype(np.float64)
        for kind in (0, 1, 2):
            sel_e = D["kind"][eval_rows] == kind
            if not sel_e.any():
                continue
            fk = fit_rows[kind_f == kind]
            yk, dk = y_f[kind_f == kind], d_f[kind_f == kind]
            p0 = (yk.sum() + 1.0) / (len(yk) + 2.0)
            mpos0 = float(dk[yk > 0].mean()) if (yk > 0).any() else 0.0
            if kind == 1:
                pk = D["set_i"][fk].astype(np.int64) * 1000003 + D["filt_i"][fk]
                ek = D["set_i"][eval_rows][sel_e].astype(np.int64) * 1000003 + D["filt_i"][eval_rows][sel_e]
                fk_f = D["filt_i"][fk]
                ef = D["filt_i"][eval_rows][sel_e]
                nF = np.bincount(fk_f, minlength=len(self.K["filts"]) + 1)
                pF = np.bincount(fk_f, weights=yk, minlength=len(nF))
                pF_rate = (pF + m * p0) / (nF + m)
                up, inv = np.unique(pk, return_inverse=True)
                nP = np.bincount(inv, minlength=len(up))
                pP = np.bincount(inv, weights=yk, minlength=len(up))
                sP = np.bincount(inv, weights=np.where(yk > 0, dk, 0.0), minlength=len(up))
                pos_ix = np.searchsorted(up, ek)
                pos_ix = np.clip(pos_ix, 0, len(up) - 1)
                hit = up[pos_ix] == ek
                prior = pF_rate[np.clip(ef, 0, len(nF) - 1)]
                n_e = np.where(hit, nP[pos_ix], 0)
                p_e = np.where(hit, pP[pos_ix], 0)
                out[sel_e] = (p_e + m * prior) / (n_e + m)
                n_out[sel_e], p_out[sel_e], pr_out[sel_e] = n_e, p_e, prior
                e_pos[sel_e] = np.where(hit & (p_e > 0), sP[pos_ix] / np.maximum(p_e, 1), mpos0)
            else:
                sk = D["set_i"][fk]
                es = D["set_i"][eval_rows][sel_e]
                nS = np.bincount(sk, minlength=len(self.K["sets"]))
                pS = np.bincount(sk, weights=yk, minlength=len(nS))
                sS = np.bincount(sk, weights=np.where(yk > 0, dk, 0.0), minlength=len(nS))
                out[sel_e] = (pS[es] + m * p0) / (nS[es] + m)
                n_out[sel_e], p_out[sel_e], pr_out[sel_e] = nS[es], pS[es], p0
                e_pos[sel_e] = np.where(pS[es] > 0, sS[es] / np.maximum(pS[es], 1), mpos0)
        self._last_counts = (n_out, p_out, pr_out)
        return out, e_pos

    def holdout_report(self):
        sp, fsp = self.splits()
        D = self.D
        idx_all = np.arange(len(D["kind"]))
        fit_rows, val_rows = idx_all[sp["fit"]], idx_all[sp["val"]]
        pre_rows, test_rows = idx_all[sp["pre_test"]], idx_all[sp["test"]]
        fw_idx = np.arange(len(D.get("fwd_y", [])))
        t0 = time.time()
        net_es, best_ep, hist = self.fit(fit_rows, val_rows=val_rows, fwd_rows=fw_idx[fsp["fit"]] if len(fw_idx) else None, tag="early-stop")
        t_es = time.time() - t0
        t1 = time.time()
        net, _, hist2 = self.fit(pre_rows, epochs=best_ep, fwd_rows=fw_idx[fsp["pre_test"]] if len(fw_idx) else None, tag="refit<q80")
        t_refit = time.time() - t1
        rep = {"cut_val": VB.iso_of(self.cut_val), "cut_test": VB.iso_of(self.cut_test), "best_epoch": best_ep, "early_stop_history": hist,
               "refit_history": hist2, "secs_early_stop": round(t_es, 1), "secs_refit": round(t_refit, 1),
               "n_fit": int(len(fit_rows)), "n_val": int(len(val_rows)), "n_pre_test": int(len(pre_rows)), "n_test": int(len(test_rows)),
               "boards_test": int(len(np.unique(D["board_i"][test_rows]))), "boards_pre_test": int(len(np.unique(D["board_i"][pre_rows])))}
        out = self.predict(net, test_rows)
        cold = self.predict(net, test_rows, cold=True)
        cb, cb_e = self.count_baseline(pre_rows, test_rows)
        n_k, pos_k, prior_k = self._last_counts
        p_ln = sigmoid(out[:, 0])
        lg = lambda q: np.log(np.clip(q, 1e-6, 1 - 1e-6) / (1 - np.clip(q, 1e-6, 1 - 1e-6)))
        hprior = sigmoid(0.5 * lg(p_ln) + 0.5 * lg(prior_k))  # linknet x the brain's own backoff prior (filter-option / cat_side)
        p_hy = (pos_k + VB.M_SHRINK * hprior) / (n_k + VB.M_SHRINK)
        p_cold = sigmoid(cold[:, 0])
        y = D["y"][test_rows].astype(np.float64)
        d = D["d"][test_rows].astype(np.float64)
        kind = D["kind"][test_rows]
        pre_sets = set(np.unique(D["set_i"][pre_rows]).tolist())
        pre_pairs = set((D["set_i"][pre_rows][D["kind"][pre_rows] == 1].astype(np.int64) * 1000003 + D["filt_i"][pre_rows][D["kind"][pre_rows] == 1]).tolist())
        pre_filts = set(np.unique(D["filt_i"][pre_rows][D["kind"][pre_rows] == 1]).tolist())
        never = np.zeros(len(test_rows), bool)
        pk = D["set_i"][test_rows].astype(np.int64) * 1000003 + D["filt_i"][test_rows]
        for i in range(len(test_rows)):
            if kind[i] == 1:
                never[i] = int(pk[i]) not in pre_pairs
            else:
                never[i] = int(D["set_i"][test_rows[i]]) not in pre_sets
        never_set = np.array([int(s) not in pre_sets for s in D["set_i"][test_rows]])
        never_filt_opt = np.array([kind[i] == 1 and int(D["filt_i"][test_rows[i]]) not in pre_filts for i in range(len(test_rows))])

        def block(sel, keys=None):
            n = int(sel.sum())
            r = {"n": n, "pos": int(y[sel].sum())}
            if n == 0:
                return r
            r["auc_linknet"] = auc(p_ln[sel], y[sel])
            r["auc_count"] = auc(cb[sel], y[sel])
            r["auc_linknet_cold_start"] = auc(p_cold[sel], y[sel])
            r["auc_hybrid_linknet_prior_counts"] = auc(p_hy[sel], y[sel])
            r["spearman_p_vs_delta_linknet"] = spearman(p_ln[sel], d[sel])
            r["spearman_p_vs_delta_count"] = spearman(cb[sel], d[sel])
            sc_ln = p_ln * np.expm1(np.clip(out[:, 1], 0, 6))
            sc_cb = cb * cb_e
            r["spearman_score_vs_delta_linknet"] = spearman(sc_ln[sel], d[sel])
            r["spearman_score_vs_delta_count"] = spearman(sc_cb[sel], d[sel])
            ps = sel & (y > 0)
            if ps.sum() >= 10:
                r["spearman_e_delta_pos_linknet"] = spearman(np.expm1(out[ps, 1]), d[ps])
                r["spearman_e_delta_pos_count"] = spearman(cb_e[ps], d[ps])
            if keys is not None:
                kk = keys[sel]
                u, inv = np.unique(kk, return_inverse=True)
                cnt = np.bincount(inv)
                ok = cnt >= 3
                if ok.sum() >= 5:
                    rate = np.bincount(inv, weights=y[sel]) / cnt
                    mp = np.bincount(inv, weights=p_ln[sel]) / cnt
                    mc = np.bincount(inv, weights=cb[sel]) / cnt
                    r["keys_ge3"] = int(ok.sum())
                    r["key_spearman_linknet"] = spearman(mp[ok], rate[ok])
                    r["key_spearman_count"] = spearman(mc[ok], rate[ok])
                    r["key_spearman_hybrid"] = spearman((np.bincount(inv, weights=p_hy[sel]) / cnt)[ok], rate[ok])
            return r

        keys = np.where(kind == 1, pk, D["set_i"][test_rows].astype(np.int64))
        res = {}
        for kn, kv in (("R", 0), ("F", 1)):
            sel = kind == kv
            res[kn] = {"held_out_boards": block(sel, keys), "never_seen_before_cut": block(sel & never, keys)}
            if kv == 1:
                res[kn]["never_seen_switch_setting"] = block(sel & never_set, keys)
                res[kn]["never_seen_filter_option"] = block(sel & never_filt_opt, keys)
        # metric heads on held-out valid rows
        mh = {}
        for h, col in ((2, "m_tim"), (3, "m_dd"), (4, "m_wr")):
            v = D[col][test_rows]
            ok = (D["valid"][test_rows] > 0) & np.isfinite(v)
            mh[HEADS[h]] = {"n": int(ok.sum()), "spearman": spearman(out[ok, h], v[ok]) if ok.sum() >= 10 else None}
        res["metric_heads"] = mh
        # calibration of P(pos) on held-out
        cal = []
        qs = np.quantile(p_ln, np.linspace(0, 1, 11))
        for i in range(10):
            sel = (p_ln >= qs[i]) & ((p_ln < qs[i + 1]) if i < 9 else (p_ln <= qs[i + 1]))
            if sel.sum():
                cal.append({"decile": i + 1, "n": int(sel.sum()), "mean_pred": round(float(p_ln[sel].mean()), 5), "realised": round(float(y[sel].mean()), 5)})
        res["calibration_deciles"] = cal
        platt = {}
        for kn, kv in (("R", 0), ("F", 1)):
            sel = kind == kv
            platt[kn] = fit_platt(p_ln[sel], y[sel])
        rep["platt"] = platt
        pc = np.where(kind == 0, apply_platt(p_ln, platt["R"]), apply_platt(p_ln, platt["F"]))
        cal2 = []
        qs2 = np.quantile(pc, np.linspace(0, 1, 11))
        for i in range(10):
            sel = (pc >= qs2[i]) & ((pc < qs2[i + 1]) if i < 9 else (pc <= qs2[i + 1]))
            if sel.sum():
                cal2.append({"decile": i + 1, "n": int(sel.sum()), "mean_pred": round(float(pc[sel].mean()), 5), "realised": round(float(y[sel].mean()), 5)})
        res["calibration_deciles_after_platt_fitted_on_this_holdout"] = cal2
        res["mean_pred_vs_realised"] = {"mean_pred": round(float(p_ln.mean()), 5), "realised": round(float(y.mean()), 5)}
        # phase evals held out by time
        ph_rows = idx_all[sp["test_ph"]]
        if len(ph_rows):
            po = sigmoid(self.predict(net, ph_rows)[:, 0])
            pc, _ = self.count_baseline(pre_rows, ph_rows)
            res["PH_by_time"] = {"n": int(len(ph_rows)), "pos": int(D["y"][ph_rows].sum()), "auc_linknet": auc(po, D["y"][ph_rows]), "auc_count": auc(pc, D["y"][ph_rows])}
        # forward head
        if len(fw_idx):
            ft = fw_idx[fsp["test"]]
            if len(ft):
                pf = sigmoid(self.predict_fwd(net, ft)[:, 5])
                pfit = sigmoid(self.predict_fwd(net, ft)[:, 0])
                yf = D["fwd_y"][ft]
                res["forward"] = {"labels_total": int(len(fw_idx)), "labels_test": int(len(ft)), "pos_test": int(yf.sum()), "auc_forward_head": auc(pf, yf),
                                  "auc_p_pos_head_same_moment": auc(pfit, yf)}
        rep["results"] = res
        return rep, best_ep

    def final(self, epochs):
        D = self.D
        rows = np.arange(len(D["kind"]))
        fw = np.arange(len(D.get("fwd_y", [])))
        t0 = time.time()
        net, _, hist = self.fit(rows, epochs=epochs, fwd_rows=fw if len(fw) else None, tag="final")
        return net, hist, round(time.time() - t0, 1)


def inherent_report(D, K):
    """USER LAW 2026-10-20: 'almost every switch has to be negative as we turn the positives into defaults'. Splits the
    negative mass (valid evals with delta < 0, delta measured vs the default in force) into
      by_inforce_source: the in-force default was promoted on this sym_side (P) / the template bold (T) / unknown (U)
      inherent: the candidate (R) or pair (F) beats the in-force default in at least one other measured context of this
                cat_side -> the negative here is its loss to a promoted default, not 'the switch is bad'
      genuine:  never positive in any measured context of this cat_side (n >= 3 evals)
      thin:     never positive, n < 3 (unknown)
    plus the identity rows dropped (candidate == default in force)."""
    out = {}
    for kn, kv in (("R", 0), ("F", 1), ("PH", 2)):
        m = D["kind"] == kv
        if not m.any():
            continue
        key = D["set_i"][m].astype(np.int64) * (1000003 if kv == 1 else 1) + (D["filt_i"][m] if kv == 1 else 0)
        u, inv = np.unique(key, return_inverse=True)
        npos = np.bincount(inv, weights=D["y"][m].astype(np.float64))
        nn = np.bincount(inv)
        d = D["d"][m].astype(np.float64)
        neg = (D["valid"][m] > 0) & (d < -EPS)
        cls = np.where(npos[inv] > 0, "inherent", np.where(nn[inv] >= 3, "genuine", "thin"))
        blk = {"evals": int(m.sum()), "negatives": int(neg.sum()), "neg_mass_sum_delta": round(float(d[neg].sum()), 2), "zero_delta": int(((np.abs(d) <= EPS) & (D["valid"][m] > 0)).sum()),
               "invalid": int((D["valid"][m] == 0).sum())}
        for c in ("inherent", "genuine", "thin"):
            s_ = neg & (cls == c)
            blk[c] = {"n": int(s_.sum()), "share_n": round(float(s_.sum()) / max(1, int(neg.sum())), 4), "sum_delta": round(float(d[s_].sum()), 2),
                      "share_mass": round(float(d[s_].sum()) / min(-1e-9, float(d[neg].sum())), 4)}
        src = D["inf_src"][m]
        blk["by_inforce_source"] = {nm_: {"n": int((neg & (src == code)).sum()), "sum_delta": round(float(d[neg & (src == code)].sum()), 2)}
                                    for nm_, code in (("promoted_P", 1), ("template_bold_T", 0), ("unknown_U", 2), ("start_set_override_Z", 3),
                                                      ("ambiguous_inert_set_A", 4), ("non_template_NB", 5))}
        out[kn] = blk
    c = K.get("counters") or {}
    out["identity_dropped"] = {"R_cand_eq_inforce": c.get("R_identity_cand_eq_inforce", 0), "F_opt_eq_inforce": c.get("F_identity_opt_eq_inforce", 0),
                               "PH_cand_eq_inforce": c.get("PH_identity_cand_eq_inforce", 0),
                               "kept_as_inforce_unknown_R": c.get("inforce_contradicted_by_nonzero_delta", 0),
                               "kept_as_inforce_unknown_F": c.get("F_inforce_contradicted_by_nonzero_marginal", 0),
                               "kept_as_inforce_unknown_PH": c.get("PH_inforce_contradicted_by_nonzero_delta", 0),
                               "rule": "candidate == inferred in-force AND |delta| <= 1e-6 -> identity (dropped); a non-zero delta contradicts the inference -> kept, in-force UNKNOWN"}
    return out


def cmd_train(a):
    out_dir = Path(a.ds_dir)
    reports = {}
    for cs in a.cat_side or CAT_SIDES:
        t0 = time.time()
        tr = Trainer(cs, ds_dir=a.ds_dir, k=a.k, H=a.hidden, batch=a.batch, lr=a.lr, max_epochs=a.max_epochs, max_train=a.max_train)
        write_status(cs, "training", rows_consumed=int(len(tr.D["kind"])), nodes_embedded=len(tr.fz.names))
        print(f"[linknet] {cs}: examples {len(tr.D['kind']):,} sets {len(tr.K['sets']):,} filters {len(tr.K['filts']):,} ctxs {len(tr.K['ctxs']):,} vocab {len(tr.fz.names):,} "
              f"fwd labels {len(tr.D.get('fwd_y', [])):,} ({time.time() - t0:.1f}s)", flush=True)
        rep, best_ep = tr.holdout_report()
        net, hist, secs = tr.final(best_ep)
        meta = {"schema": SCHEMA, "cat_side": cs, "trained_at": utc_now(), "epochs": best_ep, "k": tr.k, "H": tr.H, "vocab": tr.fz.names,
                "corpus_last_ts": VB.iso_of(float(tr.D["ts"].max())), "corpus_first_ts": VB.iso_of(float(tr.D["ts"][tr.D["ts"] > 0].min())),
                "n_examples": int(len(tr.D["kind"])), "label": PRED_LABEL, "platt": rep.get("platt")}
        mp = out_dir / f"linknet_model_{cs}.npz"
        net.save(mp, meta)
        n_params = int(sum(v.size for v in net.p.values()))
        rep.update({"cat_side": cs, "model_file": str(mp), "model_bytes": mp.stat().st_size, "n_params": n_params, "vocab": len(tr.fz.names),
                    "final_history": hist, "secs_final": secs, "secs_total": round(time.time() - t0, 1),
                    "corpus": {"examples": int(len(tr.D["kind"])), "R": int((tr.D["kind"] == 0).sum()), "F": int((tr.D["kind"] == 1).sum()),
                               "PH": int((tr.D["kind"] == 2).sum()), "pos_R": int(tr.D["y"][tr.D["kind"] == 0].sum()), "pos_F": int(tr.D["y"][tr.D["kind"] == 1].sum()),
                               "pos_PH": int(tr.D["y"][tr.D["kind"] == 2].sum()), "fwd_labels": int(len(tr.D.get("fwd_y", []))),
                               "sets": len(tr.K["sets"]), "filters": len(tr.K["filts"]), "boards": len(tr.K["boards"]), "first_ts": meta["corpus_first_ts"],
                               "last_ts": meta["corpus_last_ts"]},
                    "unknown_tokens_in_corpus_keys": dict(tr.fz.unknown_tokens.most_common(40))})
        rep["inherent_vs_genuine_negatives"] = inherent_report(tr.D, tr.K)
        reports[cs] = rep
        r = rep["results"]
        (out_dir / f"linknet_report_{cs}.json").write_text(json.dumps(rep, indent=1, default=str))
        write_status(cs, "trained", rows_consumed=int(len(tr.D["kind"])), nodes_embedded=len(tr.fz.names), n_params=n_params,
                     holdout_auc_seen={"R": r["R"]["held_out_boards"].get("auc_linknet"), "F": r["F"]["held_out_boards"].get("auc_linknet")},
                     holdout_auc_unseen={"R": r["R"]["never_seen_before_cut"].get("auc_linknet"), "F": r["F"]["never_seen_before_cut"].get("auc_linknet"),
                                         "F_cold_start_all_ids_masked": r["F"]["held_out_boards"].get("auc_linknet_cold_start")},
                     holdout_auc_count_baseline={"R": r["R"]["held_out_boards"].get("auc_count"), "F": r["F"]["held_out_boards"].get("auc_count"),
                                                 "F_unseen": r["F"]["never_seen_before_cut"].get("auc_count")},
                     model_trained_at=meta["trained_at"])
        (out_dir / f"linknet_report_{cs}.json").write_text(json.dumps(rep, indent=1, default=str))
        r = rep["results"]
        print(f"[linknet] {cs} DONE {rep['secs_total']}s  R auc ln/count {r['R']['held_out_boards'].get('auc_linknet')}/{r['R']['held_out_boards'].get('auc_count')}  "
              f"F auc {r['F']['held_out_boards'].get('auc_linknet')}/{r['F']['held_out_boards'].get('auc_count')}  "
              f"F never-seen {r['F']['never_seen_before_cut'].get('auc_linknet')}/{r['F']['never_seen_before_cut'].get('auc_count')}", flush=True)
    return 0


# ------------------------------------------------------------------------------------------------ v2: conditional network (E8d)
#
# USER LAW 2026-10-20: "a switch is only as good as the filters it is paired with; what filter and setting has ever had a good
# result and how good, in all of the data -- THAT is what needs to be applied." Every eval runs under a chain = the full set in
# force. v2 adds that chain as a fourth embedding group X (sparse multi-hot, tools/v18_features.ContextEncoder: the active filter
# options X.f: and the switch settings X.s: that differ from the cat_side's template bold) to the v1 factorisation:
#   logit P(pos) = w0 + sum w_i x_i + FM over ALL embedded nodes (S, F, C and X: candidate . each active-context node, filter .
#                  each active-context node) + MLP([s, f, c, x, s*f, s*c, f*c, s*x, f*x, s*f*x])
# so switch x filter x (filter / setting in force) effects are learned where the chains vary. X is pooled with a scipy sparse
# product (exact gradients, finite-difference checked in tests/test_v18_linknet.py). Context features are dropped at train time
# (each with X_DROP, the whole context with X_DROP_ALL) so that unseen contexts and context-free scoring stay in distribution;
# context features never seen in the fit are masked at predict time.

SCHEMA2 = "v18_linknet/2"
V2_DIR = DS_DIR / "v2"
X_DROP = 0.2
X_DROP_ALL = 0.1
NB2 = 10
SCORERS = ("v1", "v2", "counts", "hybrid_v1", "hybrid_v2")
SEL_SUBSETS = (("R", "held_out"), ("F", "held_out"), ("F", "never_tried_pair"), ("F", "never_tried_context"))


def ctx_csr(rows_feats, vocab, add=True):
    """feature tuples -> scipy CSR (n x |vocab|), binary. Unknown features are added to `vocab` (add=True) or dropped."""
    import scipy.sparse as sp
    indptr, ind = [0], []
    for fl in rows_feats:
        for f in fl:
            j = vocab.get(f)
            if j is None:
                if not add:
                    continue
                j = vocab[f] = len(vocab)
            ind.append(j)
        indptr.append(len(ind))
    return sp.csr_matrix((np.ones(len(ind), np.float32), np.asarray(ind, np.int64), np.asarray(indptr, np.int64)), shape=(len(rows_feats), max(1, len(vocab))))


class LinkNet2(LinkNet):
    """v1 network + the chain-context group X (separate embedding table EX / linear wx over the context vocabulary)."""

    def __init__(self, V, VX, k=16, H=64, T=len(HEADS), seed=0):
        rng = np.random.default_rng(seed)
        self.k, self.H, self.T, self.V, self.VX = k, H, T, V, VX
        self.p = {"E": (rng.normal(0, 0.01, (V, k))).astype(np.float32), "w": np.zeros(V, np.float32),
                  "EX": (rng.normal(0, 0.01, (VX, k))).astype(np.float32), "wx": np.zeros(VX, np.float32),
                  "W1": (rng.normal(0, math.sqrt(2.0 / (NB2 * k)), (NB2 * k, H))).astype(np.float32), "b1": np.zeros(H, np.float32),
                  "W2": (rng.normal(0, 0.05, (H, T))).astype(np.float32), "b2": np.zeros(T, np.float32)}
        self.p["E"][0] = 0.0
        self.m = {n: np.zeros_like(v) for n, v in self.p.items()}
        self.v = {n: np.zeros_like(v) for n, v in self.p.items()}
        self.t = 0
        self.seen = np.ones(V, dtype=np.float32)
        self.seen_x = np.ones(VX, dtype=np.float32)

    def xpool(self, Xb):
        """sparse context rows (B x VX) -> (x (B,k), sq (B,k), lin (B,)) with never-seen context features masked."""
        EX = self.p["EX"] * self.seen_x[:, None]
        return np.asarray(Xb @ EX, np.float32), np.asarray(Xb @ (EX * EX), np.float32), np.asarray(Xb @ (self.p["wx"] * self.seen_x), np.float32).ravel()

    def heads2(self, s, f, c, xs, sq, lin):
        tot = s + f + c + xs
        fm = 0.5 * (tot * tot - sq).sum(1)
        h0 = np.concatenate([s, f, c, xs, s * f, s * c, f * c, s * xs, f * xs, s * f * xs], 1)
        a1 = np.maximum(h0 @ self.p["W1"] + self.p["b1"], 0.0)
        out = a1 @ self.p["W2"] + self.p["b2"]
        out[:, 0] += fm + lin
        return out

    def predict_tables2(self, S, F, C, XP):
        s, sqs, ls = self.pool(*S)
        f, sqf, lf = self.pool(*F)
        c, sqc, lc = self.pool(*C)
        xs, sqx, lx = XP
        return self.heads2(s, f, c, xs, sqs + sqf + sqc + sqx, ls + lf + lc + lx)

    def forward_train2(self, idx, x, gs, gf, Xb):
        E, EX = self.p["E"], self.p["EX"]
        e = E[idx] * x[..., None]
        s, f, c = e[:, :gs].sum(1), e[:, gs:gf].sum(1), e[:, gf:].sum(1)
        dt = EX.dtype
        xs = np.asarray(Xb @ EX, dt)
        xsq = np.asarray(Xb @ (EX * EX), dt)
        xl = np.asarray(Xb @ self.p["wx"], dt).ravel()
        tot = s + f + c + xs
        sq = (e * e).sum(1) + xsq
        fm = 0.5 * (tot * tot - sq).sum(1)
        lin = (self.p["w"][idx] * x).sum(1) + xl
        h0 = np.concatenate([s, f, c, xs, s * f, s * c, f * c, s * xs, f * xs, s * f * xs], 1)
        z1 = h0 @ self.p["W1"] + self.p["b1"]
        a1 = np.maximum(z1, 0.0)
        out = a1 @ self.p["W2"] + self.p["b2"]
        out[:, 0] += fm + lin
        return out, (idx, x, e, s, f, c, xs, tot, h0, z1, a1, gs, gf, Xb)

    def backward2(self, g, cache, l2=1e-6):
        idx, x, e, s, f, c, xs, tot, h0, z1, a1, gs, gf, Xb = cache
        k = self.k
        P = self.p
        G = {"W2": a1.T @ g, "b2": g.sum(0)}
        dz1 = (g @ P["W2"].T) * (z1 > 0)
        G["W1"] = h0.T @ dz1
        G["b1"] = dz1.sum(0)
        dh0 = dz1 @ P["W1"].T
        B = [dh0[:, i * k:(i + 1) * k] for i in range(NB2)]
        ds = B[0] + B[4] * f + B[5] * c + B[7] * xs + B[9] * (f * xs)
        df = B[1] + B[4] * s + B[6] * c + B[8] * xs + B[9] * (s * xs)
        dc = B[2] + B[5] * s + B[6] * f
        dxs = B[3] + B[7] * s + B[8] * f + B[9] * (s * f)
        g0 = g[:, 0]
        de = g0[:, None, None] * (tot[:, None, :] - e)
        de[:, :gs] += ds[:, None, :]
        de[:, gs:gf] += df[:, None, :]
        de[:, gf:] += dc[:, None, :]
        dE = de * x[..., None]
        fi = idx.ravel()
        gE = np.empty((self.V, k), P["E"].dtype)
        flat = dE.reshape(-1, k)
        for j in range(k):
            gE[:, j] = np.bincount(fi, weights=flat[:, j], minlength=self.V)
        G["E"] = gE + l2 * P["E"]
        G["w"] = np.bincount(fi, weights=(g0[:, None] * x).ravel(), minlength=self.V).astype(P["w"].dtype)
        XT = Xb.T.tocsr()
        # FM part for a binary context node j: sum_i v_ij g0_i (tot_i - v_ij EX_j) (v^2 = v); MLP part: X^T dxs
        dt = P["EX"].dtype
        G["EX"] = np.asarray(XT @ (g0[:, None] * tot + dxs), dt) - np.asarray(XT @ g0, dt).reshape(-1, 1) * P["EX"] + l2 * P["EX"]
        G["wx"] = np.asarray(XT @ g0, dt).ravel()
        for n in ("W1", "W2"):
            G[n] = G[n] + l2 * P[n]
        return G

    def save(self, path, meta):
        np.savez_compressed(path, **{f"p_{n}": v for n, v in self.p.items()}, seen=self.seen, seen_x=self.seen_x,
                            meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8))

    @classmethod
    def load(cls, path):
        z = np.load(path)
        E, EX = z["p_E"], z["p_EX"]
        net = cls(E.shape[0], EX.shape[0], k=E.shape[1], H=z["p_b1"].shape[0], T=z["p_b2"].shape[0])
        for n in net.p:
            net.p[n] = z[f"p_{n}"]
        net.seen = z["seen"]
        net.seen_x = z["seen_x"]
        return net, json.loads(bytes(z["meta"]).decode())


def hybrid_p(p_net, n, pos, prior, m=None):
    """counts shrunk toward (network x the brain's backoff prior): (pos + m * sigmoid(.5 logit p_net + .5 logit prior)) / (n + m)."""
    m = VB.M_SHRINK if m is None else m
    q = lambda v: np.clip(np.asarray(v, np.float64), 1e-6, 1 - 1e-6)
    lg = lambda v: np.log(q(v) / (1 - q(v)))
    hp = sigmoid(0.5 * lg(p_net) + 0.5 * lg(prior))
    return (np.asarray(pos, np.float64) + m * hp) / (np.asarray(n, np.float64) + m)


class Trainer2(Trainer):
    """v1 vs v2 on the SAME conditional dataset, the same chronological splits and the same count baseline."""

    def __init__(self, cs, ds_dir=V2_DIR, **kw):
        super().__init__(cs, ds_dir=ds_dir, **kw)
        xc = [tuple(c) for c in (self.K.get("xctxs") or [])]
        self.xvocab = {"X:unknown": 0}
        self.XC = ctx_csr(xc + [("X:unknown",)], self.xvocab)
        self.n_x = len(xc)
        xi = self.D["x_i"] if "x_i" in self.D else np.full(len(self.D["kind"]), -1, np.int32)
        self.has_ctx = xi >= 0
        self.x_rows = np.where(self.has_ctx, xi, self.n_x).astype(np.int64)
        sig_in = Interner()
        sig = np.array([sig_in(FT.ContextEncoder.filter_signature(c)) for c in xc] + [-1], np.int64)
        self.x_sig = sig[self.x_rows]
        self.n_sig = len(sig_in.keys)
        self.use_ctx = True

    def _xb(self, rows, n_fwd=0, drop=False, rng=None):
        import scipy.sparse as sp
        Xb = self.XC[self.x_rows[rows]]
        if n_fwd:
            Xb = sp.vstack([Xb, self.XC[np.full(n_fwd, self.n_x)]], format="csr")
        if drop and rng is not None:
            Xb = Xb.copy()
            Xb.data = Xb.data * (rng.random(len(Xb.data)) >= X_DROP).astype(np.float32)
            keep = (rng.random(Xb.shape[0]) >= X_DROP_ALL).astype(np.float32)
            Xb = sp.diags(keep) @ Xb
        return Xb

    def predict(self, net, rows, cold=False, chunk=65536):
        if not isinstance(net, LinkNet2):
            return super().predict(net, rows, cold=cold, chunk=chunk)
        D = self.D
        out = np.zeros((len(rows), len(HEADS)), np.float32)
        for a in range(0, len(rows), chunk):
            r = rows[a:a + chunk]
            si, fi, ci = D["sc_i"][r], D["fc_i"][r] + 1, D["ctx_i"][r]
            S = (self.TS[0][si], self.TS[1][si].copy())
            F = (self.TF[0][fi], self.TF[1][fi].copy())
            Cc = (self.TC[0][ci], self.TC[1][ci].copy())
            out[a:a + len(r)] = net.predict_tables2(S, F, Cc, net.xpool(self._xb(r)))
        return out

    def predict_fwd(self, net, frows):
        if not isinstance(net, LinkNet2):
            return super().predict_fwd(net, frows)
        D = self.D
        fs, fc = D["fwd_set_i"][frows], D["fwd_ctx_i"][frows]
        z = np.zeros(len(fs), np.int64)
        return net.predict_tables2((self.TS[0][fs], self.TS[1][fs]), (self.TF[0][z], self.TF[1][z]), (self.TC[0][fc], self.TC[1][fc]),
                                   net.xpool(self.XC[np.full(len(fs), self.n_x)]))

    def _seen_mask(self, net, rows):
        super()._seen_mask(net, rows)
        if isinstance(net, LinkNet2):
            u = np.unique(self.x_rows[rows])
            used = np.zeros(net.VX, bool)
            used[np.unique(self.XC[u].indices)] = True
            net.seen_x = used.astype(np.float32)

    def fit(self, rows, epochs=None, val_rows=None, fwd_rows=None, tag="fit"):
        if not self.use_ctx:
            return super().fit(rows, epochs=epochs, val_rows=val_rows, fwd_rows=fwd_rows, tag=tag + "/v1")
        rng = np.random.default_rng(self.seed)
        V, VX = len(self.fz.names), self.XC.shape[1]
        net = LinkNet2(V, VX, k=self.k, H=self.H, seed=self.seed)
        rows = np.asarray(rows)
        if self.max_train and len(rows) > self.max_train:
            rows = np.sort(rng.choice(rows, self.max_train, replace=False))
        fwd_rows = np.asarray(fwd_rows if fwd_rows is not None else [], dtype=np.int64)
        gs, gf = LS, LS + LF
        best, best_ep, bad, hist = None, 0, 0, []
        n_ep = epochs or self.max_epochs
        t0 = time.time()
        for ep in range(1, n_ep + 1):
            perm = rng.permutation(len(rows))
            tot_l, nb = 0.0, 0
            net.seen = np.ones(V, np.float32)
            net.seen_x = np.ones(VX, np.float32)
            for a in range(0, len(perm), self.batch):
                r = rows[perm[a:a + self.batch]]
                fr = rng.choice(fwd_rows, min(len(fwd_rows), 128), replace=True) if len(fwd_rows) else None
                idx, x, Y, M = self._gather(r, fr, drop=True, rng=rng)
                Xb = self._xb(r, n_fwd=0 if fr is None else len(fr), drop=True, rng=rng)
                out, cache = net.forward_train2(idx, x, gs, gf, Xb)
                l, g = loss_grad(out, Y, M)
                net.adam(net.backward2(g, cache), lr=self.lr)
                tot_l += l
                nb += 1
            self._seen_mask(net, rows)
            rec = {"epoch": ep, "loss": round(tot_l / max(1, nb), 5), "secs": round(time.time() - t0, 1)}
            if val_rows is not None and len(val_rows):
                pv = self.predict(net, val_rows)[:, 0]
                rec["val_auc"] = auc(pv, self.D["y"][val_rows])
                score = rec["val_auc"] or 0.0
                if best is None or score > best[0] + 1e-4:
                    best, best_ep, bad = (score, net.copy_params(), net.seen.copy(), net.seen_x.copy()), ep, 0
                else:
                    bad += 1
            hist.append(rec)
            self.log(f"[linknet2] {self.cs} {tag}/v2 {rec}")
            if val_rows is not None and len(val_rows) and bad >= self.patience:
                break
        if best is not None:
            net.p, net.seen, net.seen_x = best[1], best[2], best[3]
        return net, (best_ep or n_ep), hist

    def fit_both(self, rows, epochs=(None, None), val_rows=None, fwd_rows=None, tag="fit"):
        out = {}
        for name, use, ep in (("v1", False, epochs[0]), ("v2", True, epochs[1])):
            self.use_ctx = use
            out[name] = self.fit(rows, epochs=ep, val_rows=val_rows, fwd_rows=fwd_rows, tag=tag)
        self.use_ctx = True
        return out

    # -- subsets: seen / never-tried pairs / never-tried contexts (relative to the reference rows the model saw)
    def subsets(self, ref_rows, ev_rows):
        D = self.D
        kind_r, kind_e = D["kind"][ref_rows], D["kind"][ev_rows]

        def cand_key(rows, kind):
            return np.where(kind == 1, D["set_i"][rows].astype(np.int64) * 1000003 + D["filt_i"][rows] + 1, D["set_i"][rows].astype(np.int64))

        ck_r, ck_e = cand_key(ref_rows, kind_r), cand_key(ev_rows, kind_e)
        mult = np.int64(self.n_sig + 2)
        sk_r = ck_r * mult + (self.x_sig[ref_rows] + 1)
        sk_e = ck_e * mult + (self.x_sig[ev_rows] + 1)
        seen_c = np.isin(ck_e, np.unique(ck_r))
        seen_cs = np.isin(sk_e, np.unique(sk_r))
        seen_sig = np.isin(self.x_sig[ev_rows], np.unique(self.x_sig[ref_rows][self.has_ctx[ref_rows]]))
        hc = self.has_ctx[ev_rows]
        out = {}
        for kn, kv in (("R", 0), ("F", 1)):
            k = kind_e == kv
            out[(kn, "held_out")] = k
            out[(kn, "never_tried_pair")] = k & ~seen_c
            out[(kn, "seen_pair")] = k & seen_c
            out[(kn, "never_tried_context")] = k & seen_c & hc & ~seen_cs
            out[(kn, "never_seen_filter_set")] = k & seen_c & hc & ~seen_sig
            out[(kn, "with_context")] = k & hc
            out[(kn, "without_context")] = k & ~hc
        return out

    def scores(self, nets, ref_rows, ev_rows):
        """-> {scorer: P(pos)}, raw outputs of both nets on ev_rows; counts / hybrids from ref_rows (the data the nets saw)."""
        o1 = self.predict(nets["v1"], ev_rows)
        o2 = self.predict(nets["v2"], ev_rows)
        cb, _cb_e = self.count_baseline(ref_rows, ev_rows)
        n_k, pos_k, prior_k = self._last_counts
        p1, p2 = sigmoid(o1[:, 0]), sigmoid(o2[:, 0])
        return {"v1": p1, "v2": p2, "counts": cb, "hybrid_v1": hybrid_p(p1, n_k, pos_k, prior_k), "hybrid_v2": hybrid_p(p2, n_k, pos_k, prior_k)}, o1, o2

    def auc_table(self, sc, sub, ev_rows):
        y = self.D["y"][ev_rows].astype(np.float64)
        tab = {}
        for (kn, nm), m in sub.items():
            blk = {"n": int(m.sum()), "pos": int(y[m].sum())}
            for s_, p in sc.items():
                blk[f"auc_{s_}"] = auc(p[m], y[m]) if m.any() else None
            tab.setdefault(kn, {})[nm] = blk
        return tab

    @staticmethod
    def select(val_tab):
        """production scorer = best mean validation AUC over SEL_SUBSETS (subsets where every scorer has an AUC)."""
        per = {}
        for s_ in SCORERS:
            v = [val_tab.get(kn, {}).get(nm, {}).get(f"auc_{s_}") for kn, nm in SEL_SUBSETS]
            per[s_] = v
        ok = [i for i in range(len(SEL_SUBSETS)) if all(per[s_][i] is not None for s_ in SCORERS)]
        mean = {s_: (round(float(np.mean([per[s_][i] for i in ok])), 4) if ok else None) for s_ in SCORERS}
        best = max(SCORERS, key=lambda s_: (mean[s_] if mean[s_] is not None else -1, -SCORERS.index(s_)))
        return best, mean, [f"{SEL_SUBSETS[i][0]}/{SEL_SUBSETS[i][1]}" for i in ok]

    def holdout_v2(self):
        sp, fsp = self.splits()
        D = self.D
        idx_all = np.arange(len(D["kind"]))
        fit_rows, val_rows = idx_all[sp["fit"]], idx_all[sp["val"]]
        pre_rows, test_rows = idx_all[sp["pre_test"]], idx_all[sp["test"]]
        fw_idx = np.arange(len(D.get("fwd_y", [])))
        t0 = time.time()
        es = self.fit_both(fit_rows, val_rows=val_rows, fwd_rows=fw_idx[fsp["fit"]] if len(fw_idx) else None, tag="early-stop")
        ep = (es["v1"][1], es["v2"][1])
        t_es = time.time() - t0
        sc_v, _o1v, _o2v = self.scores({"v1": es["v1"][0], "v2": es["v2"][0]}, fit_rows, val_rows)
        val_tab = self.auc_table(sc_v, self.subsets(fit_rows, val_rows), val_rows)
        prod, sel_mean, sel_used = self.select(val_tab)
        t1 = time.time()
        rf = self.fit_both(pre_rows, epochs=ep, fwd_rows=fw_idx[fsp["pre_test"]] if len(fw_idx) else None, tag="refit<q80")
        t_rf = time.time() - t1
        nets = {"v1": rf["v1"][0], "v2": rf["v2"][0]}
        sc_t, o1, o2 = self.scores(nets, pre_rows, test_rows)
        sub_t = self.subsets(pre_rows, test_rows)
        test_tab = self.auc_table(sc_t, sub_t, test_rows)
        kind = D["kind"][test_rows]
        y = D["y"][test_rows].astype(np.float64)
        d = D["d"][test_rows].astype(np.float64)
        platt = {s_: {kn: fit_platt(sc_t[s_][kind == kv], y[kind == kv]) for kn, kv in (("R", 0), ("F", 1))} for s_ in SCORERS}
        heads = {}
        for nm, o in (("v1", o1), ("v2", o2)):
            hb = {}
            ps = y > 0
            hb["spearman_e_delta_pos"] = spearman(np.expm1(o[ps, 1]), d[ps]) if ps.sum() >= 10 else None
            for h, col in ((2, "m_tim"), (3, "m_dd"), (4, "m_wr")):
                v = D[col][test_rows]
                ok = (D["valid"][test_rows] > 0) & np.isfinite(v)
                hb[HEADS[h]] = spearman(o[ok, h], v[ok]) if ok.sum() >= 10 else None
            heads[nm] = hb
        ph_rows = idx_all[sp["test_ph"]]
        ph = None
        if len(ph_rows):
            sp_, _a, _b = self.scores(nets, pre_rows, ph_rows)
            yph = D["y"][ph_rows]
            ph = {"n": int(len(ph_rows)), "pos": int(yph.sum()), **{f"auc_{s_}": auc(sp_[s_], yph) for s_ in SCORERS}}
        fwd = None
        if len(fw_idx):
            ft = fw_idx[fsp["test"]]
            if len(ft):
                yf = D["fwd_y"][ft]
                fwd = {"labels_test": int(len(ft)), "pos_test": int(yf.sum()), "auc_forward_head_v1": auc(sigmoid(self.predict_fwd(nets["v1"], ft)[:, 5]), yf),
                       "auc_forward_head_v2": auc(sigmoid(self.predict_fwd(nets["v2"], ft)[:, 5]), yf)}
        mean_pred = {s_: {"mean_pred": _r(float(np.mean(sc_t[s_]))), "realised": _r(float(y.mean()))} for s_ in SCORERS}
        rep = {"cut_val": VB.iso_of(self.cut_val), "cut_test": VB.iso_of(self.cut_test), "epochs": {"v1": ep[0], "v2": ep[1]},
               "history": {"early_stop_v1": es["v1"][2], "early_stop_v2": es["v2"][2], "refit_v1": rf["v1"][2], "refit_v2": rf["v2"][2]},
               "secs_early_stop": round(t_es, 1), "secs_refit": round(t_rf, 1), "n_fit": int(len(fit_rows)), "n_val": int(len(val_rows)),
               "n_pre_test": int(len(pre_rows)), "n_test": int(len(test_rows)), "boards_test": int(len(np.unique(D["board_i"][test_rows]))),
               "validation": val_tab, "selection": {"rule": "best mean VALIDATION AUC (models early-stopped on fit < q70, counts from fit) over " + ", ".join(sel_used),
                                                    "mean_val_auc": sel_mean, "production_scorer": prod},
               "test": test_tab, "platt_on_test": platt, "heads": heads, "PH_by_time": ph, "forward": fwd, "mean_pred_vs_realised_test": mean_pred}
        return rep, ep, prod

    def final_v2(self, ep, which=("v1", "v2")):
        D = self.D
        rows = np.arange(len(D["kind"]))
        fw = np.arange(len(D.get("fwd_y", [])))
        out = {}
        for nm in which:
            self.use_ctx = nm == "v2"
            t0 = time.time()
            net, _, hist = self.fit(rows, epochs=ep[0 if nm == "v1" else 1], fwd_rows=fw if len(fw) else None, tag="final")
            out[nm] = (net, hist, round(time.time() - t0, 1))
        self.use_ctx = True
        return out


V1_COUNTERPART = {"v2": "v1", "hybrid_v2": "hybrid_v1"}
GATE_SUBSETS = (("F", "never_tried_pair"), ("F", "never_tried_context"))


def v1_gate(rep):
    """USER RULE (E8d brief): 'if v2 does not beat v1 on never-tried pairs or contexts, say so plainly and keep v1 as the scorer'.
    The validation-selected scorer stands only if it is v1-free OR its v2 form beats its v1 counterpart (v2 vs v1, hybrid_v2 vs
    hybrid_v1) on the held-out boards in BOTH F never-tried pairs and F never-tried contexts; otherwise the v1 counterpart is the
    production scorer. Writes the verdict into rep['selection'] and returns the production scorer. (The gate reads the test
    numbers: the final choice is therefore not test-blind; stated in the report.)"""
    sel = rep["selection"]
    chosen = sel.get("validation_choice") or sel["production_scorer"]
    sel["validation_choice"] = chosen
    v1c = V1_COUNTERPART.get(chosen)
    verdict = {"rule": "v2-based choice kept only if it beats its v1 counterpart on the held-out boards in BOTH " + ", ".join(f"{k}/{n}" for k, n in GATE_SUBSETS)}
    prod = chosen
    if v1c is not None:
        cmp_ = {}
        for kn, nm in GATE_SUBSETS:
            b = rep["test"][kn][nm]
            cmp_[f"{kn}/{nm}"] = {chosen: b.get(f"auc_{chosen}"), v1c: b.get(f"auc_{v1c}"), "n": b.get("n"), "pos": b.get("pos")}
        beats = all((c[chosen] is not None and c[v1c] is not None and c[chosen] > c[v1c]) for c in cmp_.values())
        verdict.update({"compare": cmp_, "v2_beats_v1": beats})
        if not beats:
            prod = v1c
            verdict["outcome"] = f"{chosen} does NOT beat {v1c} on both -> production = {v1c} (v1 kept as the network)"
        else:
            verdict["outcome"] = f"{chosen} beats {v1c} on both -> production = {chosen}"
    else:
        verdict["outcome"] = f"validation choice {chosen} does not use v2 -> kept"
    sel["v1_gate"] = verdict
    sel["production_scorer"] = prod
    return prod


def cmd_redecide(a):
    """apply v1_gate to an existing report_v2_<CS>.json; train the missing final model (v1) when the gate falls back to it."""
    for cs in a.cat_side or CAT_SIDES:
        out_dir = Path(a.ds_dir)
        rp = out_dir / f"report_v2_{cs}.json"
        rep = json.loads(rp.read_text())
        prod = v1_gate(rep)
        files = dict(rep.get("models") or {})
        if prod in ("v1", "hybrid_v1") and "v1" not in files:
            tr = Trainer2(cs, ds_dir=out_dir, k=a.k, H=a.hidden, batch=a.batch, lr=a.lr, max_epochs=a.max_epochs)
            ep = (rep["epochs"]["v1"], rep["epochs"]["v2"])
            net, hist, secs = tr.final_v2(ep, which=("v1",))["v1"]
            meta = {"schema": SCHEMA2, "cat_side": cs, "net": "v1", "trained_at": utc_now(), "epochs": ep[0], "k": tr.k, "H": tr.H, "vocab": tr.fz.names, "xvocab": None,
                    "corpus_last_ts": VB.iso_of(float(tr.D["ts"].max())), "n_examples": int(len(tr.D["kind"])), "label": PRED_LABEL, "production_scorer": prod,
                    "platt": rep["platt_on_test"].get(prod)}
            mp = out_dir / f"model_v1_{cs}.npz"
            net.save(mp, meta)
            files["v1"] = {"file": str(mp), "bytes": mp.stat().st_size, "n_params": int(sum(v.size for v in net.p.values())), "secs_final": secs, "final_history": hist}
        rep["models"] = files
        rp.write_text(json.dumps(rep, indent=1, default=str))
        prod_doc = {"cat_side": cs, "production_scorer": prod, "selection": rep["selection"], "platt": rep["platt_on_test"].get(prod),
                    "models": {k: v["file"] for k, v in files.items()}, "decided_at": utc_now(), "label": PRED_LABEL}
        (out_dir / f"production_{cs}.json").write_text(json.dumps(prod_doc, indent=1))
        write_status(cs, "trained_v2", production_scorer=prod, v1_gate=rep["selection"]["v1_gate"]["outcome"])
        print(f"[redecide] {cs}: {rep['selection']['v1_gate']['outcome']}", flush=True)
    return 0


def cmd_train2(a):
    for cs in a.cat_side or CAT_SIDES:
        train2_one(cs, a.ds_dir, k=a.k, H=a.hidden, batch=a.batch, lr=a.lr, max_epochs=a.max_epochs, max_train=a.max_train)
    return 0


def train2_one(cs, ds_dir, fctx=None, log=_log, status=True, **kw):
    """v1 vs v2 holdout + selection + final model(s) of ONE cat_side -> report dict (files under ds_dir)."""
    out_dir = Path(ds_dir)
    if True:
        t0 = time.time()
        tr = Trainer2(cs, ds_dir=ds_dir, fctx=fctx, log=log, **kw)
        D = tr.D
        (write_status if status else (lambda *a_, **k_: None))(cs, "training_v2", rows_consumed_v2=int(len(D["kind"])), rows_with_context=int(tr.has_ctx.sum()), context_vocab=int(tr.XC.shape[1]))
        log(f"[linknet2] {cs}: examples {len(D['kind']):,} with context {int(tr.has_ctx.sum()):,} contexts {tr.n_x:,} filter-sets {tr.n_sig:,} "
              f"context vocab {tr.XC.shape[1]:,} vocab {len(tr.fz.names):,}")
        rep, ep, prod = tr.holdout_v2()
        prod = v1_gate(rep)
        which = ("v2",) if prod in ("v2", "hybrid_v2", "counts") else ("v1", "v2")
        fin = tr.final_v2(ep, which=which)
        xv = [None] * tr.XC.shape[1]
        for f_, j in tr.xvocab.items():
            xv[j] = f_
        files = {}
        for nm, (net, hist, secs) in fin.items():
            meta = {"schema": SCHEMA2, "cat_side": cs, "net": nm, "trained_at": utc_now(), "epochs": ep[0 if nm == "v1" else 1], "k": tr.k, "H": tr.H,
                    "vocab": tr.fz.names, "xvocab": xv if nm == "v2" else None, "corpus_last_ts": VB.iso_of(float(D["ts"].max())),
                    "n_examples": int(len(D["kind"])), "label": PRED_LABEL, "production_scorer": prod, "platt": rep["platt_on_test"].get(prod)}
            mp = out_dir / f"model_{nm}_{cs}.npz"
            net.save(mp, meta)
            files[nm] = {"file": str(mp), "bytes": mp.stat().st_size, "n_params": int(sum(v.size for v in net.p.values())), "secs_final": secs, "final_history": hist}
        rep.update({"cat_side": cs, "models": files, "secs_total": round(time.time() - t0, 1), "vocab": len(tr.fz.names), "context_vocab": int(tr.XC.shape[1]),
                    "corpus": {"examples": int(len(D["kind"])), "R": int((D["kind"] == 0).sum()), "F": int((D["kind"] == 1).sum()), "PH": int((D["kind"] == 2).sum()),
                               "with_context": {kn: int(((D["kind"] == kv) & tr.has_ctx).sum()) for kn, kv in (("R", 0), ("F", 1), ("PH", 2))},
                               "without_context": {kn: int(((D["kind"] == kv) & ~tr.has_ctx).sum()) for kn, kv in (("R", 0), ("F", 1), ("PH", 2))},
                               "native_rows": int((D["prov"] == 1).sum()) if "prov" in D else 0, "contexts": tr.n_x, "filter_sets": tr.n_sig,
                               "pos": {kn: int(D["y"][D["kind"] == kv].sum()) for kn, kv in (("R", 0), ("F", 1), ("PH", 2))}}})
        rep["inherent_vs_genuine_negatives"] = inherent_report(tr.D, tr.K)
        (out_dir / f"report_v2_{cs}.json").write_text(json.dumps(rep, indent=1, default=str))
        prod_doc = {"cat_side": cs, "production_scorer": prod, "selection": rep["selection"], "platt": rep["platt_on_test"].get(prod),
                    "models": {k: v["file"] for k, v in files.items()}, "decided_at": utc_now(), "label": PRED_LABEL}
        (out_dir / f"production_{cs}.json").write_text(json.dumps(prod_doc, indent=1))
        T = rep["test"]
        (write_status if status else (lambda *a_, **k_: None))(cs, "trained_v2", production_scorer=prod, selection_mean_val_auc=rep["selection"]["mean_val_auc"],
                     holdout_auc_v2={f"{kn}/{nm}": {s_: T[kn][nm].get(f"auc_{s_}") for s_ in SCORERS} for kn, nm in
                                     (("R", "held_out"), ("F", "held_out"), ("F", "never_tried_pair"), ("F", "never_tried_context"), ("R", "never_tried_context"))},
                     model_v2_trained_at=utc_now())
        log(f"[linknet2] {cs} DONE {rep['secs_total']}s production={prod} sel={rep['selection']['mean_val_auc']}")
        for kn in ("R", "F"):
            for nm in ("held_out", "never_tried_pair", "never_tried_context"):
                b = T[kn][nm]
                log(f"   {kn} {nm:22s} n {b['n']:>8,} pos {b['pos']:>6,} " + " ".join(f"{s_} {b.get('auc_' + s_)}" for s_ in SCORERS))
    return rep


# ------------------------------------------------------------------------------------------------ scoring + proposals

def template_status(fctx, switch, cand):
    return "HAS_TEMPLATE_ROW" if cand in (fctx.cands.get(switch) or []) else "NEEDS_ROW"


def _fmt_num(x, ints):
    if ints:
        return str(int(round(x)))
    s = f"{x:.6g}"
    return s


FIXED_STOP_FAMILY = {
    "LONG": {"FIXED_STOP_REF_FIELD": (["dc_low", "dc_basis", "bb_low", "bb_basis"], "dc_low"), "FIXED_STOP_REF_TF": (["15m", "1h", "4h"], "15m"),
             "FIXED_STOP_BUFFER_PCT": (["0.15", "0.25", "0.35", "0.5"], "0.25"), "FIXED_STOP_TIGHTEN_MODE": (["off", "breakeven_after_pct", "trail_ref"], "off")},
    # canonical names stay dc_low/... on shorts too (the engine mirrors them); 2026-10-20 the family became template rows
    "SHORT": {"FIXED_STOP_REF_FIELD": (["dc_low", "dc_basis", "bb_low", "bb_basis"], "dc_low"), "FIXED_STOP_REF_TF": (["15m", "1h", "4h"], "15m"),
              "FIXED_STOP_BUFFER_PCT": (["0.15", "0.25", "0.35", "0.5"], "0.25"), "FIXED_STOP_TIGHTEN_MODE": (["off", "breakeven_after_pct", "trail_ref"], "off")},
}
for _side in ("LONG", "SHORT"):
    FIXED_STOP_FAMILY[_side]["FIXED_STOP_TIGHTEN_TRIGGER_PCT"] = (["0.5", "1", "2", "3"], None)
LEVEL_SIBLINGS = {"LONG": ["dc_low", "dc_basis", "bb_lower", "bb_basis"], "SHORT": ["dc_high", "dc_basis", "bb_upper", "bb_basis"]}
BARE_LEVELS = ["lower", "basis", "upper"]
PROPOSE_ROLES = frozenset({"stop", "target", "reference", "threshold"})


def enumerate_beyond(fctx, coerce=None):
    """Settings the indicator family admits but the TEMPLATE has no row for, for every switch whose role is
    stop/target/reference/threshold. -> [{switch, cand, tab, rule, status, config_field, note}] (status NEEDS_ROW).
    Rules: level siblings (dc_low -> dc_basis / bb_lower / bb_basis, shorts mirrored; bare lower -> basis / upper), TF
    neighbours (every TF of this cat_side's TEMPLATE universe the row lacks), threshold neighbours (one grid step below the
    lowest / above the highest candidate), and the doc-17 §3.7 FIXED_STOP reference family (no template row, no config
    field yet: needs tools/v15_switch_add.py --queue + 4-surface wiring)."""
    side = fctx.side
    out, seen = [], set()
    tf_universe = set()
    for vals in fctx.cands.values():
        for v in vals:
            if FT.value_kind(v) == "tf":
                for t in re.split(r"[,+]", str(v)):
                    if t and t != "OFF" and t in FT.TF_RANK and FT.TF_RANK[t] >= FT.TF_RANK["15m"]:
                        tf_universe.add(t)
    refused = collections.Counter()

    def push(sw, cand, tab, rule, note=""):
        if (sw, cand) in seen or cand in (fctx.cands.get(sw) or []):
            return
        if coerce is not None and sw in fctx.bible:
            ok, why = coerce(sw, cand)
            if not ok:
                refused[f"{rule}:{why}"[:80]] += 1
                return
        seen.add((sw, cand))
        b = fctx.bible.get(sw) or {}
        in_cfg = any((b.get("in_config") or {}).values()) if b else False
        out.append({"switch": sw, "cand": cand, "tab": tab, "rule": rule, "status": "NEEDS_ROW", "config_field": bool(in_cfg),
                    "note": note or ("new candidate on an existing template switch: needs a template row via tools/v15_switch_add.py --queue" if in_cfg else
                                     "no config field: needs a template row via tools/v15_switch_add.py --queue + 4-surface wiring (vec + ez_manage + tradier_manage + config/config_tradier)")})

    for sw, cands in fctx.cands.items():
        if sw.startswith(EXCLUDE_PREFIX) or sw in EXCLUDE_KEYS:
            continue
        a = FT.name_attrs(sw, fctx)
        if not (set(a["role"]) & PROPOSE_ROLES):
            continue
        tab = (fctx.tabs_of.get(sw) or ["EXIT_STRUCTURAL"])[0]
        kinds = {FT.value_kind(c) for c in cands}
        if kinds == {"enum"}:
            vt = {t for c in cands for t in re.split(r"[_\s,]+", str(c).lower()) if t}
            if vt & {"low", "high", "basis", "lower", "upper", "mid"}:
                if any("_" in str(c) for c in cands):
                    for s2 in LEVEL_SIBLINGS["LONG"] + LEVEL_SIBLINGS["SHORT"]:
                        if s2 in LEVEL_SIBLINGS[side] or s2 == "dc_basis" or s2 == "bb_basis":
                            push(sw, s2, tab, "level_sibling")
                else:
                    for s2 in BARE_LEVELS:
                        push(sw, s2, tab, "level_sibling")
        elif kinds <= {"tf"} and "tf" in kinds:
            single = all("," not in str(c) for c in cands)
            if single:
                for t in sorted(tf_universe, key=lambda x: FT.TF_RANK[x]):
                    push(sw, t, tab, "tf_neighbour")
        elif kinds == {"num"} and len(cands) >= 2:
            nums = sorted({float(c) for c in cands})
            if len(nums) < 2:
                continue
            gaps = sorted(b - a_ for a_, b in zip(nums, nums[1:]))
            step = gaps[len(gaps) // 2]
            ints = all(re.match(r"^-?\d+$", str(c)) for c in cands)
            lo, hi = nums[0] - step, nums[-1] + step
            if nums[0] > 0 and lo <= 0:
                lo = nums[0] / 2.0 if not ints else None
            elif nums[0] == 0 and lo < 0:
                lo = None  # never cross from a non-negative span into negative values (sentinels, counts)
            if sw.startswith("MAX_AUGMENTS") or "MAXAUG" in sw:
                continue  # MAXAUG sentinels are never settings (doc 17 principle 6)
            if lo is not None and not (ints and lo == nums[0]):
                push(sw, _fmt_num(lo, ints), tab, "threshold_neighbour_below", f"one grid step ({_fmt_num(step, ints)}) below the template span")
            push(sw, _fmt_num(hi, ints), tab, "threshold_neighbour_above", f"one grid step ({_fmt_num(step, ints)}) above the template span")
    fam = FIXED_STOP_FAMILY[side]
    for sw, (vals, dflt) in fam.items():
        for v in vals:
            if v == dflt:
                continue
            push(sw, v, "EXIT_STRUCTURAL", "fixed_stop_family_doc17_3_7",
                 f"doc 17 §3.7 stop-reference family (default {sw}={dflt} = today's stop); no template row, no config field: needs tools/v15_switch_add.py --queue + 4-surface wiring + SWITCH_BIBLE + S2 scalar-parity lane + version gate")
    return out, dict(refused)


def _coerce_fn(cs):
    try:
        from tools.switch_types import coerce
    except Exception:
        return None
    venue = "tradier" if cs.startswith("STOCKS") else "crypto"

    def f(sw, cand):
        try:
            ok, _v, why = coerce(sw, cand, venue)
        except Exception as e:
            return False, f"coerce_error:{e}"
        return bool(ok), why
    return f


class Scorer:
    def __init__(self, cs, ds_dir=DS_DIR, fctx=None):
        self.cs = cs
        self.net, self.meta = LinkNet.load(Path(ds_dir) / f"linknet_model_{cs}.npz")
        if self.meta.get("cat_side") != cs:
            raise ValueError(f"model of {self.meta.get('cat_side')} refused for {cs} (principle 13)")
        self.fz = Featurizer(cs, fctx=fctx)
        self.fz.vocab = {n: i for i, n in enumerate(self.meta["vocab"])}
        self.fz.names = list(self.meta["vocab"])
        self.frozen_V = len(self.fz.names)
        self.D, self.K = load_ds(cs, ds_dir)
        for sw, vals in seen_values(self.K["sets"]).items():
            if sw not in self.fz.fctx.cands:
                self.fz.fctx.add_seen_values(sw, vals)
        self.unknown_feature_hits = collections.Counter()

    def table(self, feat_lists, L):
        """Like Featurizer.table but against the frozen vocabulary: a feature the model never saw is dropped AND counted."""
        n = len(feat_lists)
        idx = np.zeros((n, L), np.int32)
        x = np.zeros((n, L), np.float32)
        for r, fl in enumerate(feat_lists):
            for j, (f, v) in enumerate(fl[:L]):
                if f == "<pad>":
                    continue
                i = self.fz.vocab.get(f)
                if i is None:
                    self.unknown_feature_hits[f.split(":", 1)[0]] += 1
                    continue
                idx[r, j] = i
                x[r, j] = v
        return idx, x

    def set_table(self, keys):
        return self.table([self.fz.set_feats(k) for k in keys], LS)

    def filt_table(self, hdrs):
        return self.table([self.fz.filt_feats(h) if h else [] for h in hdrs], LF)

    def ctx_table(self, kind):
        return self.table([Featurizer.ctx_feats((kind, g, "", "U", "")) for g in REGIMES], LC)

    def evidence(self):
        """Measured counts over the WHOLE corpus (the evidence the predictions are judged against, never a prediction)."""
        D, K = self.D, self.K
        R = D["kind"] != 1
        si = D["set_i"]
        n_set = np.bincount(si[R], minlength=len(K["sets"]))
        p_set = np.bincount(si[R], weights=D["y"][R].astype(np.float64), minlength=len(K["sets"]))
        s_set = np.bincount(si[R], weights=np.where(D["y"][R] > 0, D["d"][R], 0.0).astype(np.float64), minlength=len(K["sets"]))
        Fm = D["kind"] == 1
        pk = si[Fm].astype(np.int64) * 1000003 + D["filt_i"][Fm]
        up, inv = np.unique(pk, return_inverse=True)
        n_p = np.bincount(inv)
        p_p = np.bincount(inv, weights=D["y"][Fm].astype(np.float64))
        s_p = np.bincount(inv, weights=np.where(D["y"][Fm] > 0, D["d"][Fm], 0.0).astype(np.float64))
        nfo = np.bincount(D["filt_i"][Fm], minlength=len(K["filts"]))
        pfo = np.bincount(D["filt_i"][Fm], weights=D["y"][Fm].astype(np.float64), minlength=len(K["filts"]))
        self.ev = {"n_set": n_set, "p_set": p_set, "s_set": s_set, "pairs": up, "n_p": n_p, "p_p": p_p, "s_p": s_p, "nfo": nfo, "pfo": pfo}
        self.set_ix = {k: i for i, k in enumerate(K["sets"])}
        self.filt_ix = {h: i for i, h in enumerate(K["filts"])}

    def pair_ev(self, set_key, hdr):
        si, fi = self.set_ix.get(set_key), self.filt_ix.get(hdr)
        if si is None or fi is None:
            return 0, 0, 0.0
        k = si * 1000003 + fi
        j = np.searchsorted(self.ev["pairs"], k)
        if j < len(self.ev["pairs"]) and self.ev["pairs"][j] == k:
            return int(self.ev["n_p"][j]), int(self.ev["p_p"][j]), float(self.ev["s_p"][j])
        return 0, 0, 0.0

    def set_ev(self, set_key):
        si = self.set_ix.get(set_key)
        if si is None:
            return 0, 0, 0.0
        return int(self.ev["n_set"][si]), int(self.ev["p_set"][si]), float(self.ev["s_set"][si])

    def cal(self, p, kind):
        ab = (self.meta.get("platt") or {}).get(kind)
        return apply_platt(p, ab) if ab else p

    def heads(self, S, F, C, kind="F"):
        out = self.net.predict_tables(S, F, C)
        return {"p_pos": self.cal(sigmoid(out[:, 0]), kind), "p_pos_raw": sigmoid(out[:, 0]), "e_delta": np.expm1(np.clip(out[:, 1], 0, 6)), "d_tim": out[:, 2] * MSCALE, "d_dd": out[:, 3] * MSCALE,
                "d_wr": out[:, 4] * MSCALE, "fwd": sigmoid(out[:, 5])}

    def score_template(self, out_npz, chunk_rows=64):
        tidx = self.fz.fctx.tidx
        row_keys, row_tabs = [], []
        for tab, blk in tidx["tabs"].items():
            for _r, sw, cand, _d in blk["rows"]:
                if sw.startswith(EXCLUDE_PREFIX) or sw in EXCLUDE_KEYS:
                    continue
                k = f"{tab}|{sw}={cand}"
                if k not in row_keys:
                    row_keys.append(k)
                    row_tabs.append(tab)
        row_ix = {k: i for i, k in enumerate(row_keys)}
        yel = sorted({y for blk in tidx["tabs"].values() for y in blk["yellows"]})
        yel_ix = {y: i for i, y in enumerate(yel)}
        ST = self.set_table(row_keys)
        FTb = self.filt_table(yel)
        FT0 = self.filt_table([""])
        G = len(REGIMES)
        CR, CF = self.ctx_table("R"), self.ctx_table("F")
        s_all = self.net.pool(*ST)
        f_all = self.net.pool(*FTb)
        f0 = self.net.pool(*FT0)
        cR, cF = self.net.pool(*CR), self.net.pool(*CF)
        nR = len(row_keys)
        rows_out = {h: np.zeros((nR, G), np.float32) for h in ("p_pos", "e_delta", "d_tim", "d_dd", "d_wr", "fwd")}
        for g in range(G):
            hd = self.net.heads(s_all[0], np.repeat(f0[0], nR, 0), np.repeat(cR[0][g:g + 1], nR, 0), s_all[1] + f0[1] + cR[1][g], s_all[2] + f0[2] + cR[2][g])
            for h, v in zip(("p_pos", "e_delta", "d_tim", "d_dd", "d_wr", "fwd"), (self.cal(sigmoid(hd[:, 0]), "R"), np.expm1(np.clip(hd[:, 1], 0, 6)), hd[:, 2] * MSCALE,
                                                                                hd[:, 3] * MSCALE, hd[:, 4] * MSCALE, sigmoid(hd[:, 5]))):
                rows_out[h][:, g] = v
        cell_r, cell_y = [], []
        for tab, blk in tidx["tabs"].items():
            ys = [yel_ix[y] for y in blk["yellows"]]
            if not ys:
                continue
            seen_r = set()
            for _r, sw, cand, _d in blk["rows"]:
                k = f"{tab}|{sw}={cand}"
                ri = row_ix.get(k)
                if ri is None or ri in seen_r:
                    continue
                seen_r.add(ri)
                cell_r.extend([ri] * len(ys))
                cell_y.extend(ys)
        cell_r = np.asarray(cell_r, np.int32)
        cell_y = np.asarray(cell_y, np.int32)
        N = len(cell_r)
        cells = {h: np.zeros((N, G), np.float16) for h in ("p_pos", "e_delta", "d_tim", "d_dd", "d_wr")}
        t0 = time.time()
        CH = 200000
        for a in range(0, N, CH):
            r, y = cell_r[a:a + CH], cell_y[a:a + CH]
            s, sqs, ls = s_all[0][r], s_all[1][r], s_all[2][r]
            f, sqf, lf = f_all[0][y], f_all[1][y], f_all[2][y]
            for g in range(G):
                c = np.broadcast_to(cF[0][g], s.shape)
                hd = self.net.heads(s, f, c, sqs + sqf + cF[1][g], ls + lf + cF[2][g])
                cells["p_pos"][a:a + CH, g] = self.cal(sigmoid(hd[:, 0]), "F")
                cells["e_delta"][a:a + CH, g] = np.expm1(np.clip(hd[:, 1], 0, 6))
                cells["d_tim"][a:a + CH, g] = hd[:, 2] * MSCALE
                cells["d_dd"][a:a + CH, g] = hd[:, 3] * MSCALE
                cells["d_wr"][a:a + CH, g] = hd[:, 4] * MSCALE
        secs = time.time() - t0
        n_meas = np.zeros(N, np.int32)
        pos_meas = np.zeros(N, np.int32)
        for i in range(N):
            n, p, _s = self.pair_ev(row_keys[cell_r[i]], yel[cell_y[i]])
            if n:
                n_meas[i], pos_meas[i] = n, p
        fc = self.fz.fctx
        row_ident = np.array([str(k.partition("|")[2].partition("=")[2]) == str(fc.default(k.partition("|")[2].partition("=")[0])) for k in row_keys], bool)
        y_ident = np.array([str(y.partition("=")[2]) == str(fc.default(y.partition("=")[0], is_filter=True)) for y in yel], bool)
        cell_ident = row_ident[cell_r] | y_ident[cell_y]
        row_n = np.array([self.set_ev(k)[0] for k in row_keys], np.int32)
        row_pos = np.array([self.set_ev(k)[1] for k in row_keys], np.int32)
        np.savez_compressed(out_npz, label=np.frombuffer(PRED_LABEL.encode(), np.uint8), regimes=np.array(REGIMES), row_keys=np.array(row_keys),
                            yellows=np.array(yel), cell_row=cell_r, cell_yellow=cell_y, **{f"cell_{h}": v for h, v in cells.items()},
                            cell_n_measured=n_meas, cell_pos_measured=pos_meas, **{f"row_{h}": v.astype(np.float16) for h, v in rows_out.items()},
                            row_n_measured=row_n, row_pos_measured=row_pos, row_identity=row_ident, cell_identity=cell_ident,
                            meta=np.frombuffer(json.dumps({"cat_side": self.cs, "model_trained_at": self.meta["trained_at"], "scored_at": utc_now(),
                                                           "label": PRED_LABEL, "platt": self.meta.get("platt"), "heads": ["p_pos", "e_delta(E[delta|pos], pp)", "d_tim", "d_dd", "d_wr", "fwd (rows only)"],
                                                           "regimes": REGIMES, "n_cells": int(N), "n_rows": int(nR), "predictions": int(N * G * 5 + nR * G * 6)}).encode(), np.uint8))
        return {"row_keys": row_keys, "yellows": yel, "cell_row": cell_r, "cell_yellow": cell_y, "cells": cells, "rows": rows_out, "n_meas": n_meas,
                "pos_meas": pos_meas, "row_n": row_n, "row_pos": row_pos, "row_ident": row_ident, "cell_ident": cell_ident, "secs_cells": round(secs, 1), "n_predictions": int(N * G * 5 + nR * G * 6),
                "s_all": s_all, "f_all": f_all, "ST": ST, "FTb": FTb}

    # -- nearest tested evidence by ID-free embedding (cosine)
    def token_vec(self, idx, x):
        x = x.copy()
        x[:, :3] = 0.0
        return self.net.pool(idx, x)[0]

    def prep_neighbours(self, min_n=3):
        K = self.K
        keys = [k for i, k in enumerate(K["sets"]) if self.ev["n_set"][i] >= min_n and not k.partition("|")[2].startswith(EXCLUDE_PREFIX)]
        T = self.set_table(keys)
        V = self.token_vec(*T)
        self.nb_keys = keys
        self.nb_vec = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-9)

    def nearest(self, set_key, k=5, exclude_switch=None):
        T = self.set_table([set_key])
        v = self.token_vec(*T)[0]
        v = v / (np.linalg.norm(v) + 1e-9)
        sim = self.nb_vec @ v
        order = np.argsort(-sim)
        out = []
        for j in order:
            key = self.nb_keys[j]
            sw = key.partition("|")[2].partition("=")[0]
            if exclude_switch and sw == exclude_switch:
                continue
            n, p, s = self.set_ev(key)
            out.append({"key": key.partition("|")[2], "tab": key.partition("|")[0], "cos": round(float(sim[j]), 3), "n": n, "pos": p,
                        "pos_rate": round(p / n, 4) if n else None, "mean_delta_pos": round(s / p, 4) if p else None, "kind": "MEASURED"})
            if len(out) >= k:
                break
        return out

    def predict_keys(self, set_keys, hdrs=None, regime_g=0):
        ST = self.set_table(set_keys)
        FTb = self.filt_table(hdrs if hdrs is not None else [""] * len(set_keys))
        kind = "F" if hdrs is not None else "R"
        C = self.ctx_table(kind)
        Cg = (np.repeat(C[0][regime_g:regime_g + 1], len(set_keys), 0), np.repeat(C[1][regime_g:regime_g + 1], len(set_keys), 0))
        return self.heads(ST, FTb, Cg, kind=kind)


def _r(x, nd=5):
    return None if x is None else round(float(x), nd)


def cmd_score(a):
    out_dir = Path(a.out_dir)
    for cs in a.cat_side or CAT_SIDES:
        t0 = time.time()
        sc = Scorer(cs, ds_dir=a.ds_dir)
        sc.evidence()
        res = sc.score_template(out_dir / f"linknet_scores_{cs}.npz")
        sc.prep_neighbours()
        fctx = sc.fz.fctx
        G0 = 0
        # A/B: template cells never fairly tried (n < 3), ranked by predicted P(pos) in the regime-free context
        cells = res["cells"]
        p = cells["p_pos"][:, G0].astype(np.float32)
        nm = res["n_meas"]
        cand = np.nonzero((nm < 3) & ~res["cell_ident"])[0]
        order = cand[np.argsort(-p[cand])][: max(PROPOSALS_K * 4, 2000)]
        props = []
        for i in order:
            rk = res["row_keys"][res["cell_row"][i]]
            hdr = res["yellows"][res["cell_yellow"][i]]
            tab, _, kv = rk.partition("|")
            sw, _, cnd = kv.partition("=")
            fk, _, fo = hdr.partition("=")
            gbest = int(np.argmax(cells["p_pos"][i].astype(np.float32)))
            n_r, p_r, s_r = sc.set_ev(rk)
            nf = sc.filt_ix.get(hdr)
            props.append({"switch": sw, "cand": cnd, "filter": fk, "opt": fo, "tab": tab, "cat_side": cs, "regime": "ALL", "label": "PREDICTION",
                          "p_pos_pred": _r(p[i]), "e_delta_pred": _r(cells["e_delta"][i, G0], 4), "forward_pred": _r(res["rows"]["fwd"][res["cell_row"][i], G0], 4),
                          "d_tim_pred": _r(cells["d_tim"][i, G0], 3), "d_dd_pred": _r(cells["d_dd"][i, G0], 3), "d_wr_pred": _r(cells["d_wr"][i, G0], 3),
                          "best_regime": REGIMES[gbest], "p_pos_pred_best_regime": _r(cells["p_pos"][i, gbest]),
                          "status": PRED_LABEL if nm[i] == 0 else "HAS_TEMPLATE_ROW", "n_measured_pair": int(nm[i]), "pos_measured_pair": int(res["pos_meas"][i]),
                          "nearest_evidence": [{"key": kv, "filter": None, "n": n_r, "pos": p_r, "pos_rate": _r(p_r / n_r, 4) if n_r else None, "kind": "MEASURED naked row of this switch=cand"},
                                               {"key": hdr, "n": int(sc.ev["nfo"][nf]) if nf is not None else 0, "pos": int(sc.ev["pfo"][nf]) if nf is not None else 0,
                                                "kind": "MEASURED marginal of this filter=opt across switches"}]})
        # C: beyond the template
        beyond, refused = enumerate_beyond(fctx, coerce=_coerce_fn(cs))
        bkeys = [f"{b['tab']}|{b['switch']}={b['cand']}" for b in beyond]
        nr = []
        if bkeys:
            hd = sc.predict_keys(bkeys)
            yel = res["yellows"]
            for j, b in enumerate(beyond):
                ex_sw = None
                near = sc.nearest(bkeys[j], k=5, exclude_switch=ex_sw)
                nr.append({"switch": b["switch"], "cand": b["cand"], "filter": None, "opt": None, "tab": b["tab"], "cat_side": cs, "regime": "ALL", "label": "PREDICTION",
                           "p_pos_pred": _r(hd["p_pos"][j]), "e_delta_pred": _r(hd["e_delta"][j], 4), "forward_pred": _r(hd["fwd"][j], 4),
                           "d_tim_pred": _r(hd["d_tim"][j], 3), "d_dd_pred": _r(hd["d_dd"][j], 3), "d_wr_pred": _r(hd["d_wr"][j], 3),
                           "status": "NEEDS_ROW", "rule": b["rule"], "config_field": b["config_field"], "note": b["note"], "nearest_evidence": near})
            # percentile of each beyond-template prediction among the template's own naked rows (regime-free)
            rp = np.sort(res["rows"]["p_pos"][:, G0])
            for x in nr:
                x["pct_rank_vs_template_rows"] = _r(float(np.searchsorted(rp, x["p_pos_pred"])) / max(1, len(rp)), 4)
            # best companion filter (predicted) for the top beyond-template rows
            nr.sort(key=lambda x: -(x["p_pos_pred"] or 0))
            for x in nr[:PROPOSALS_NEEDS_ROW_K]:
                ys = [y for y in yel if y in set(fctx.tidx["tabs"].get(x["tab"], {}).get("yellows", []))]
                if ys:
                    h2 = sc.predict_keys([f"{x['tab']}|{x['switch']}={x['cand']}"] * len(ys), ys)
                    j = int(np.argmax(h2["p_pos"]))
                    x["best_companion_pred"] = {"filter": ys[j].partition("=")[0], "opt": ys[j].partition("=")[2], "p_pos_pred": _r(h2["p_pos"][j]),
                                                "e_delta_pred": _r(h2["e_delta"][j], 4), "label": "PREDICTION"}
        fixed = [x for x in nr if x["rule"].startswith("fixed_stop")]
        # the family's TEMPLATE rows (installed 2026-10-20): naked-row predictions vs the bold default, every regime
        fam = FIXED_STOP_FAMILY[fctx.side]
        for ri, rk in enumerate(res["row_keys"]):
            tab, _, kv = rk.partition("|")
            sw, _, cnd = kv.partition("=")
            if sw not in fam:
                continue
            ident = bool(res["row_ident"][ri])
            fixed.append({"switch": sw, "cand": cnd, "filter": None, "opt": None, "tab": tab, "cat_side": cs, "regime": "ALL", "label": "PREDICTION",
                          "p_pos_pred": _r(res["rows"]["p_pos"][ri, G0]), "e_delta_pred": _r(res["rows"]["e_delta"][ri, G0], 4), "forward_pred": _r(res["rows"]["fwd"][ri, G0], 4),
                          "d_tim_pred": _r(res["rows"]["d_tim"][ri, G0], 3), "d_dd_pred": _r(res["rows"]["d_dd"][ri, G0], 3), "d_wr_pred": _r(res["rows"]["d_wr"][ri, G0], 3),
                          "p_pos_pred_by_regime": {g: _r(res["rows"]["p_pos"][ri, j]) for j, g in enumerate(REGIMES)},
                          "status": "IDENTITY_DEFAULT" if ident else "HAS_TEMPLATE_ROW", "rule": "fixed_stop_family_template_row",
                          "default": fctx.default(sw), "n_measured_row": int(res["row_n"][ri]), "pos_measured_row": int(res["row_pos"][ri]),
                          "pct_rank_vs_template_rows": _r(float(np.searchsorted(np.sort(res["rows"]["p_pos"][:, G0]), res["rows"]["p_pos"][ri, G0])) / max(1, len(res["row_keys"])), 4),
                          "nearest_evidence": sc.nearest(rk, k=5)})
        doc = {"meta": {"schema": SCHEMA, "cat_side": cs, "built_at": utc_now(), "model_trained_at": sc.meta["trained_at"], "corpus_last_ts": sc.meta["corpus_last_ts"],
                        "label": "every p_pos_pred / e_delta_pred / forward_pred / d_*_pred here is a PREDICTION of tools/v18_linknet.py, never a measurement; nothing is adopted except by the real engine",
                        "scores_npz": str(out_dir / f"linknet_scores_{cs}.npz"), "n_predictions_scored": res["n_predictions"], "secs_cells": res["secs_cells"],
                        "template_cells": int(len(res["cell_row"])), "template_cells_n_lt_3": int((nm < 3).sum()), "template_cells_never_measured": int((nm == 0).sum()),
                        "beyond_template_enumerated": len(nr), "beyond_template_refused_by_switch_types": refused,
                        "unknown_feature_hits_at_scoring": dict(sc.unknown_feature_hits)},
               "template_untried": props[:PROPOSALS_K * 2], "needs_row": nr,
               "fixed_stop_family": sorted(fixed, key=lambda x: -(x["p_pos_pred"] or 0))}
        p_out = out_dir / f"linknet_proposals_{cs}.json"
        tmp = p_out.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, default=str))
        os.replace(tmp, p_out)
        n_set_all = len({Featurizer.split_combo(k)[0] for k in sc.K["sets"]} | set(res["row_keys"]) | {f"{b['tab']}|{b['switch']}={b['cand']}" for b in beyond})
        n_opt_all = len(set(sc.K["filts"]) | set(res["yellows"]))
        n_sym = len({c[2] for c in sc.K["ctxs"] if c[2]})
        implied = n_set_all * (n_opt_all + 1) * len(REGIMES) * (n_sym + 1) * 4
        V = len(sc.fz.names)
        write_status(cs, "scored", nodes_embedded=V, links_scored_explicit=int((len(res["cell_row"]) + len(res["row_keys"])) * len(REGIMES)),
                     predictions_stored=res["n_predictions"], links_implied=int(implied),
                     links_implied_factors={"settings": n_set_all, "filter_options": n_opt_all, "regimes": len(REGIMES), "sym_sides": n_sym, "tiers": 4},
                     feature_pair_links=int(V * (V - 1) // 2), proposals_file=str(p_out), scores_file=str(out_dir / f"linknet_scores_{cs}.npz"),
                     model_trained_at=sc.meta["trained_at"])
        print(f"[score] {cs}: {res['n_predictions']:,} predictions (cells {len(res['cell_row']):,} x {len(REGIMES)} regimes) in {res['secs_cells']}s; "
              f"untried cells {int((nm < 3).sum()):,}; beyond-template {len(nr)} (fixed-stop {len(fixed)}); {p_out} {p_out.stat().st_size} B; {time.time() - t0:.0f}s", flush=True)
    return 0


# ------------------------------------------------------------------------------------------------ digest adapter

def digest_proposals(cat_side, learn_dir=LEARN_DIR, k=None, k_needs=None):
    """-> (proposals, proposed_rows) in the v18_digest contract shape. Fail-open: no proposals file -> ([], [])."""
    k = PROPOSALS_K if k is None else k
    k_needs = PROPOSALS_NEEDS_ROW_K if k_needs is None else k_needs
    doc = None
    # v2 (E8d) first: conditional on the cat_side's current defaults, production scorer, diversity cap; else v1
    for name in (f"linknet_proposals_v2_{cat_side}.json", f"linknet_proposals_{cat_side}.json"):
        p = Path(learn_dir) / name
        if not p.exists():
            continue
        try:
            d_ = json.loads(p.read_text())
        except Exception:
            continue
        if (d_.get("meta") or {}).get("cat_side") == cat_side:
            doc = d_
            break
    if doc is None:
        return [], []
    v1_ctx = {"name": "TEMPLATE_BOLD (v1 model, context-free)", "hash": None, "n_features": 0, "n_active_filter_options": 0}
    ne_keys = ("key", "tab", "cos", "n", "pos", "pos_rate", "mean_delta_pos", "kind", "board", "delta", "jaccard", "shared_active_filters", "n_context_features",
               "n_measured_contexts", "of")

    def ent(x):
        return {"switch": x["switch"], "cand": str(x["cand"]), "filter": x.get("filter"), "opt": None if x.get("opt") is None else str(x["opt"]), "cat_side": cat_side,
                "regime": x.get("regime") or "ALL", "p_pos_pred": x.get("p_pos_pred"), "e_delta_pred": x.get("e_delta_pred"), "forward_pred": x.get("forward_pred"),
                "nearest_evidence": ([{"key": f"{x['switch']}={x['cand']}" + (f"|{x.get('filter')}={x.get('opt')}" if x.get("filter") else ""), "n": int(x.get("n_measured_pair", x.get("n_measured_row", 0)) or 0),
                                       "pos": int(x.get("pos_measured_pair", x.get("pos_measured_row", 0)) or 0), "kind": "MEASURED this exact link"}] if x.get("status") != "NEEDS_ROW" else [])
                                     + [{kk: v for kk, v in ne.items() if kk in ne_keys} for ne in (x.get("nearest_evidence") or [])][:3],
                "status": x["status"] if x["status"] in ("PREDICTED_NOT_MEASURED", "HAS_TEMPLATE_ROW", "NEEDS_ROW") else "PREDICTED_NOT_MEASURED", "label": "PREDICTION",
                "context": {kk: v for kk, v in (x.get("context") or v1_ctx).items() if kk in ("name", "hash", "n_features", "n_active_filter_options")}}
    needs = sorted(doc.get("needs_row") or [], key=lambda x: -(x.get("p_pos_pred") or 0))
    fixed = [x for x in needs if str(x.get("rule", "")).startswith("fixed_stop")]
    fixed_rows = [x for x in (doc.get("fixed_stop_family") or []) if x.get("rule") == "fixed_stop_family_template_row" and x.get("status") != "IDENTITY_DEFAULT"]
    needs_top = needs[:k_needs]
    for x in fixed:
        if x not in needs_top:
            needs_top.append(x)
    cells = (doc.get("template_untried") or [])[: max(0, k - len(needs_top) - len(fixed_rows))]
    props = [ent(x) for x in cells] + [ent(x) for x in needs_top] + [ent(x) for x in fixed_rows]
    props.sort(key=lambda e: -(e["p_pos_pred"] or 0))
    venue = "STOCKS" if cat_side.startswith("STOCKS") else "CRYPTO"
    side = cat_side.split("_", 1)[1]
    by_sw = collections.OrderedDict()
    for x in needs_top:
        e = by_sw.setdefault(x["switch"], {"switch": x["switch"], "tab": x.get("tab"), "venues": [venue], "sides": [side], "candidates": [], "default": None,
                                           "status": "PROPOSED_NOT_QUEUED", "source": "v18_linknet " + PRED_LABEL, "why": [], "needs_wiring": not x.get("config_field"),
                                           "label": "PREDICTION"})
        e["candidates"].append(str(x["cand"]))
        e["why"].append(f"{x['cand']}: p_pos_pred {x.get('p_pos_pred')} e_delta_pred {x.get('e_delta_pred')} ({x.get('rule')})")
    rows = []
    for sw, e in by_sw.items():
        fam = FIXED_STOP_FAMILY[side].get(sw)
        if fam and fam[1] is not None:
            e["default"] = fam[1]
            e["candidates"] = [fam[1]] + [c for c in e["candidates"] if c != fam[1]]
        e["why"] = "; ".join(e["why"])[:600]
        rows.append(e)
    return props, rows


# ------------------------------------------------------------------------------------------------ calibration

def cmd_calibrate(a):
    """Predicted vs realised P(pos) of evals that arrived AFTER the model's corpus (out-of-sample by time). Reads a fresh
    spill dir (v18_brain extract with a COPY of the brain's watermarks, so only new lines), matches every fresh naked row and
    yellow cell of each cat_side to the scored template, and appends one row per cat_side to linknet_calibration.jsonl."""
    out_p = Path(a.out_dir) / "linknet_calibration.jsonl"
    tmp = Path(a.tmp_dir)
    tmp.mkdir(parents=True, exist_ok=True)
    srt = tmp / "calib_sorted.tsv"
    VB.sorted_events(a.spill_dir, tmp, srt)
    by_cs = collections.defaultdict(list)
    with open(srt) as fh:
        for line in fh:
            ts, _, js = line.partition("\t")
            if '"e":"row"' not in js[:12]:
                continue
            ev = json.loads(js)
            cs = VB.cat_side_of(ev.get("s"))
            if cs is None:
                continue
            by_cs[cs].append((ts, ev))
    os.remove(srt)
    rows_written = []
    for cs in a.cat_side or CAT_SIDES:
        which = getattr(a, "scores", "auto") or "auto"
        v2_ok = (Path(a.out_dir) / f"linknet_scores_v2_{cs}.npz").exists()
        tag = "_v2" if (which == "v2" or (which == "auto" and v2_ok)) else ""
        npz_p = Path(a.out_dir) / f"linknet_scores{tag}_{cs}.npz"
        prop_p = Path(a.out_dir) / f"linknet_proposals{tag}_{cs}.json"
        if not npz_p.exists():
            continue
        zf = np.load(npz_p)
        z = {k: zf[k] for k in ("meta", "row_keys", "yellows", "cell_row", "cell_yellow", "row_p_pos", "cell_p_pos", "cell_n_measured")}
        meta = json.loads(bytes(z["meta"]).decode())
        row_keys = [str(x) for x in z["row_keys"]]
        yel = [str(x) for x in z["yellows"]]
        r_ix = {k: i for i, k in enumerate(row_keys)}
        y_ix = {y: i for i, y in enumerate(yel)}
        cell_ix = {(int(r), int(y)): i for i, (r, y) in enumerate(zip(z["cell_row"], z["cell_yellow"]))}
        prop_keys = set()
        if prop_p.exists():
            pd = json.loads(prop_p.read_text())
            for x in (pd.get("template_untried") or [])[:PROPOSALS_K]:
                prop_keys.add((f"{x['tab']}|{x['switch']}={x['cand']}", f"{x['filter']}={x['opt']}"))
        since = a.since or meta.get("scored_at")
        P, Y, isprop, untried = [], [], [], []
        PR, YR = [], []
        used_ts = []
        for ts, ev in by_cs.get(cs, []):
            if since and ts < since:
                continue
            used_ts.append(ts)
            rk = f"{ev['sh']}|{ev['k']}"
            ri = r_ix.get(rk)
            if ri is None:
                continue
            nk = ev.get("n")
            if nk is not None and nk[-1]:
                PR.append(float(z["row_p_pos"][ri, 0]))
                YR.append(1 if (nk[1] and nk[0] is not None and nk[0] > EPS) else 0)
            for c in ev.get("y") or []:
                yi = y_ix.get(str(c[0]).strip())
                if yi is None:
                    continue
                ci = cell_ix.get((ri, yi))
                if ci is None:
                    continue
                P.append(float(z["cell_p_pos"][ci, 0]))
                Y.append(1 if (c[2] and c[1] is not None and c[1] > EPS) else 0)
                isprop.append((rk, str(c[0]).strip()) in prop_keys)
                untried.append(int(z["cell_n_measured"][ci]) < 3)
        P, Y, isprop, untried = np.array(P), np.array(Y), np.array(isprop, bool), np.array(untried, bool)
        PR, YR = np.array(PR), np.array(YR)

        def blk(sel):
            n = int(sel.sum()) if len(sel) else 0
            if not n:
                return {"n": 0}
            return {"n": n, "pos": int(Y[sel].sum()), "mean_pred": _r(P[sel].mean()), "realised": _r(Y[sel].mean()), "auc": auc(P[sel], Y[sel])}
        row = {"at": utc_now(), "cat_side": cs, "model": "v2:" + str(meta.get("production_scorer")) + " under CAT_SIDE_DEFAULTS" if tag else "v1", "scores_file": str(npz_p),
               "model_trained_at": meta.get("model_trained_at"), "scored_at": meta.get("scored_at"), "evals_since": since,
               "window_first_ts": min(used_ts) if used_ts else None, "window_last_ts": max(used_ts) if used_ts else None, "row_events_in_window": len(used_ts),
               "source": [str(p) for d in a.spill_dir for p in sorted(Path(d).glob("spill_*.tsv*"))],
               "cells_all": blk(np.ones(len(P), bool)), "cells_untried_n_lt_3": blk(untried), "cells_in_top_proposals": blk(isprop),
               "rows": ({"n": int(len(PR)), "pos": int(YR.sum()), "mean_pred": _r(PR.mean()), "realised": _r(YR.mean()), "auc": auc(PR, YR)} if len(PR) else {"n": 0}),
               "note": a.note or "", "label": "realised = measured engine evals; mean_pred = PREDICTION made before those evals"}
        rows_written.append(row)
        with open(out_p, "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
        print(json.dumps(row, default=str))
    return 0


# ------------------------------------------------------------------------------------------------ v2: production scorer + proposals v2

DIVERSITY_CAP = int(os.environ.get("V18_PROPOSALS_ROW_CAP", "5"))
TOP_CHAINS = int(os.environ.get("V18_TOP_LIVE_CHAINS", "20"))
CHAIN_K = int(os.environ.get("V18_PROPOSALS_CHAIN_K", "50"))
PWC_TOP = int(os.environ.get("V18_PAIR_CTX_TOP", "10"))


def diversity_cap(order, row_of, cap=DIVERSITY_CAP, k=None):
    """indices in rank order, at most `cap` per template row (switch=cand row key) -> list (the v1 proposals clustered on few rows)."""
    used = collections.Counter()
    out = []
    for i in order:
        r = row_of(i)
        if used[r] >= cap:
            continue
        used[r] += 1
        out.append(i)
        if k is not None and len(out) >= k:
            break
    return out


def jaccard(a, b):
    if not a and not b:
        return 1.0
    u = len(a | b)
    return len(a & b) / u if u else 0.0


def ctx_hash(feats):
    return f"{VB.h64('|'.join(feats)):016x}"


def live_chains(cs, enc, full_sets=None, excluded=None):
    """per sym_side live set (data/per_sym_full_sets.json, the sets in force live) -> {sym_side: feats}, tokenized stocks excluded."""
    if full_sets is None:
        try:
            full_sets = json.loads((ROOT / "data" / "per_sym_full_sets.json").read_text())
        except Exception:
            full_sets = {}
    if excluded is None:
        try:
            ex = json.loads((ROOT / "data" / "excluded_tokenized_stocks.json").read_text())
            excluded = {str(s).upper() for s in (ex.get("symbols") or ex.get("excluded") or [])}
        except Exception:
            excluded = set()
    out = {}
    for sym, st in full_sets.items():
        if VB.cat_side_of(sym) != cs or not isinstance(st, dict):
            continue
        if sym.rsplit("_", 1)[0].upper() in excluded:
            continue
        out[sym] = enc.feats(enc.diff(st))
    return out


def top_chains(chains, k=TOP_CHAINS):
    """the k most common live chains: distinct chain contexts ranked by sym_sides sharing them; ties (most chains are unique per
    sym_side) broken by mean Jaccard similarity to every other live chain of the cat_side (the most typical chain first)."""
    groups = collections.defaultdict(list)
    for sym, f in chains.items():
        groups[f].append(sym)
    keys = list(groups)
    sets_ = [set(f) for f in keys]
    w = [len(groups[f]) for f in keys]
    tw = float(sum(w)) or 1.0
    typ = []
    for i, a in enumerate(sets_):
        typ.append(sum(w[j] * jaccard(a, b) for j, b in enumerate(sets_) if j != i) / max(1.0, tw - w[i]))
    order = sorted(range(len(keys)), key=lambda i: (-w[i], -typ[i], keys[i]))
    return [{"chain_id": f"LIVE_{n + 1:02d}", "feats": keys[i], "sym_sides": sorted(groups[keys[i]]), "n_sym_sides": w[i], "typicality": round(typ[i], 4),
             "hash": ctx_hash(keys[i])} for n, i in enumerate(order[:k])]


def ctx_descr(name, feats, **kw):
    return {"name": name, "hash": ctx_hash(feats), "n_features": len(feats), "n_active_filter_options": sum(1 for f in feats if f.startswith("X.f:")), **kw}


class Scorer2(Scorer):
    """The PRODUCTION scorer of a cat_side (production_<CS>.json: v1 | v2 | counts | hybrid_v1 | hybrid_v2, chosen on validation
    AUC), every prediction CONDITIONAL on a chain context (feature tuple). Calibrated with the chosen scorer's Platt (R / F)."""

    def __init__(self, cs, ds_dir=V2_DIR, fctx=None):
        self.cs = cs
        pd = json.loads((Path(ds_dir) / f"production_{cs}.json").read_text())
        if pd.get("cat_side") != cs:
            raise ValueError(f"production file of {pd.get('cat_side')} refused for {cs} (principle 13)")
        self.prod = pd["production_scorer"]
        self.platt = pd.get("platt") or {}
        self.net2, self.meta2 = LinkNet2.load(Path(ds_dir) / f"model_v2_{cs}.npz")
        if self.meta2.get("cat_side") != cs:
            raise ValueError(f"model of {self.meta2.get('cat_side')} refused for {cs} (principle 13)")
        self.net1 = None
        if self.prod in ("v1", "hybrid_v1"):
            self.net1, _m1 = LinkNet.load(Path(ds_dir) / f"model_v1_{cs}.npz")
        self.meta = dict(self.meta2, trained_at=self.meta2["trained_at"], platt=self.platt)
        self.net = self.net1 if self.net1 is not None else self.net2
        self.fz = Featurizer(cs, fctx=fctx)
        self.fz.vocab = {n: i for i, n in enumerate(self.meta2["vocab"])}
        self.fz.names = list(self.meta2["vocab"])
        self.xvocab = {f: j for j, f in enumerate(self.meta2["xvocab"] or []) if f is not None}
        self.enc = FT.ContextEncoder(self.fz.fctx)
        self.D, self.K = load_ds(cs, ds_dir)
        for sw, vals in seen_values(self.K["sets"]).items():
            if sw not in self.fz.fctx.cands:
                self.fz.fctx.add_seen_values(sw, vals)
        self.unknown_feature_hits = collections.Counter()
        self.unknown_context_features = collections.Counter()
        try:
            self.platt_all = json.loads((Path(ds_dir) / f"report_v2_{cs}.json").read_text()).get("platt_on_test") or {}
        except Exception:
            self.platt_all = {}
        self.secondary = self.net1 is not None

    def evidence(self):
        super().evidence()
        D = self.D
        R = D["kind"] == 0
        nS = len(self.K["sets"])
        self.ev["n_setR"] = np.bincount(D["set_i"][R], minlength=nS)
        self.ev["p_setR"] = np.bincount(D["set_i"][R], weights=D["y"][R].astype(np.float64), minlength=nS)
        Fm = D["kind"] == 1
        self.p0 = {"R": (float(D["y"][R].sum()) + 1.0) / (float(R.sum()) + 2.0), "F": (float(D["y"][Fm].sum()) + 1.0) / (float(Fm.sum()) + 2.0)}
        m = VB.M_SHRINK
        self.fo_rate = (self.ev["pfo"] + m * self.p0["F"]) / (self.ev["nfo"] + m)
        # measured contexts per candidate (for 'closest measured context')
        xr = D["x_i"]
        self.xsets = [frozenset(c) for c in (self.K.get("xctxs") or [])]
        okR = R & (xr >= 0)
        self.ctx_by_set = collections.defaultdict(list)
        for i in np.nonzero(okR)[0]:
            self.ctx_by_set[int(D["set_i"][i])].append(int(i))
        okF = Fm & (xr >= 0)
        self.ctx_by_pair = collections.defaultdict(list)
        for i in np.nonzero(okF)[0]:
            self.ctx_by_pair[(int(D["set_i"][i]), int(D["filt_i"][i]))].append(int(i))
        self.board_names = self.K["boards"]

    def xpool(self, feats):
        """context feature tuple -> pooled (x, sq, lin) of the v2 net (unknown features dropped + counted)."""
        import scipy.sparse as sp
        cols = []
        for f in feats:
            j = self.xvocab.get(f)
            if j is None:
                self.unknown_context_features[f.split("=", 1)[0]] += 1
                continue
            cols.append(j)
        Xb = sp.csr_matrix((np.ones(len(cols), np.float32), (np.zeros(len(cols), np.int64), np.asarray(cols, np.int64))), shape=(1, self.net2.VX))
        return self.net2.xpool(Xb)

    def raw(self, s, f, c, xp, v2=False):
        """pooled S/F/C (+ context pool xp) -> raw head outputs of the network the production scorer uses (v2=True: the v2 net,
        whose pools must come from self.net2)."""
        if self.net1 is not None and not v2:
            return self.net1.heads(s[0], f[0], c[0], s[1] + f[1] + c[1], s[2] + f[2] + c[2])
        B = len(s[0])
        xs = np.broadcast_to(xp[0], (B, xp[0].shape[1]))
        return self.net2.heads2(s[0], f[0], c[0], xs, s[1] + f[1] + c[1] + xp[1], s[2] + f[2] + c[2] + xp[2])

    def final_p(self, p_net, n, pos, prior, kind):
        if self.prod in ("v1", "v2"):
            p = p_net
        elif self.prod == "counts":
            p = (np.asarray(pos, np.float64) + VB.M_SHRINK * np.asarray(prior, np.float64)) / (np.asarray(n, np.float64) + VB.M_SHRINK)
        else:
            p = hybrid_p(p_net, n, pos, prior)
        ab = self.platt.get(kind)
        return apply_platt(p, ab) if ab else p

    def v2ctx_p(self, out2, n, pos, prior, kind):
        """SECONDARY (only when the production scorer is v1-based): hybrid_v2 = counts shrunk toward the CONTEXT-CONDITIONAL v2 net,
        Platt of hybrid_v2 on the held-out boards. A PREDICTION shown for transparency; never the production ranking of the cells."""
        p = hybrid_p(sigmoid(out2[:, 0]), n, pos, prior)
        ab = (self.platt_all.get("hybrid_v2") or {}).get(kind)
        return apply_platt(p, ab) if ab else p

    def heads_out(self, out, n, pos, prior, kind):
        p_net = sigmoid(out[:, 0])
        return {"p_pos": self.final_p(p_net, n, pos, prior, kind), "p_net": p_net, "e_delta": np.expm1(np.clip(out[:, 1], 0, 6)),
                "d_tim": out[:, 2] * MSCALE, "d_dd": out[:, 3] * MSCALE, "d_wr": out[:, 4] * MSCALE, "fwd": sigmoid(out[:, 5])}

    def closest_context(self, ex_rows, feats, k=1):
        """measured examples (row indices) -> the one(s) whose chain context is closest (Jaccard) to `feats` -> MEASURED dicts."""
        if not ex_rows:
            return []
        q = set(feats)
        D = self.D
        best = sorted(ex_rows, key=lambda i: -jaccard(self.xsets[int(D["x_i"][i])], q))[:k]
        out = []
        for i in best:
            xs = self.xsets[int(D["x_i"][i])]
            out.append({"kind": "MEASURED closest context", "board": self.board_names[int(D["board_i"][i])], "delta": _r(D["d"][i], 4), "pos": int(D["y"][i]),
                        "valid": int(D["valid"][i]), "jaccard": round(jaccard(xs, q), 4), "n_context_features": len(xs),
                        "shared_active_filters": sum(1 for f in (xs & q) if f.startswith("X.f:")), "n_measured_contexts": len(ex_rows)})
        return out

    def grid(self, ctx_list, regimes_for_first=True):
        """every template row / cell x contexts. ctx_list = [(name, feats)]; regimes (6) only for ctx_list[0]; ALL for the rest."""
        tidx = self.fz.fctx.tidx
        row_keys = []
        for tab, blk in tidx["tabs"].items():
            for _r0, sw, cand, _d in blk["rows"]:
                if sw.startswith(EXCLUDE_PREFIX) or sw in EXCLUDE_KEYS:
                    continue
                k_ = f"{tab}|{sw}={cand}"
                if k_ not in row_keys:
                    row_keys.append(k_)
        row_ix = {k_: i for i, k_ in enumerate(row_keys)}
        yel = sorted({y for blk in tidx["tabs"].values() for y in blk["yellows"]})
        yel_ix = {y: i for i, y in enumerate(yel)}
        ST, FTb, FT0 = self.set_table(row_keys), self.filt_table(yel), self.filt_table([""])
        net = self.net
        s_all, f_all, f0 = net.pool(*ST), net.pool(*FTb), net.pool(*FT0)
        CR, CF = self.ctx_table("R"), self.ctx_table("F")
        cR, cF = net.pool(*CR), net.pool(*CF)
        if self.secondary:
            n2 = self.net2
            s2_, f2_, f02, cR2, cF2 = n2.pool(*ST), n2.pool(*FTb), n2.pool(*FT0), n2.pool(*CR), n2.pool(*CF)
        cell_r, cell_y = [], []
        for tab, blk in tidx["tabs"].items():
            ys = [yel_ix[y] for y in blk["yellows"]]
            seen_r = set()
            for _r0, sw, cand, _d in blk["rows"]:
                ri = row_ix.get(f"{tab}|{sw}={cand}")
                if ri is None or ri in seen_r or not ys:
                    continue
                seen_r.add(ri)
                cell_r.extend([ri] * len(ys))
                cell_y.extend(ys)
        cell_r, cell_y = np.asarray(cell_r, np.int32), np.asarray(cell_y, np.int32)
        N, nR = len(cell_r), len(row_keys)
        n_meas, pos_meas = np.zeros(N, np.int32), np.zeros(N, np.int32)
        for i in range(N):
            n_, p_, _s = self.pair_ev(row_keys[cell_r[i]], yel[cell_y[i]])
            n_meas[i], pos_meas[i] = n_, p_
        r_si = np.array([self.set_ix.get(k_, -1) for k_ in row_keys])
        row_n = np.where(r_si >= 0, self.ev["n_setR"][np.maximum(r_si, 0)], 0)
        row_pos = np.where(r_si >= 0, self.ev["p_setR"][np.maximum(r_si, 0)], 0)
        y_fi = np.array([self.filt_ix.get(y, -1) for y in yel])
        y_prior = np.where(y_fi >= 0, self.fo_rate[np.maximum(y_fi, 0)], self.p0["F"])
        cell_prior = y_prior[cell_y]
        fc = self.fz.fctx
        row_ident = np.array([str(k_.partition("|")[2].partition("=")[2]) == str(fc.default(k_.partition("|")[2].partition("=")[0])) for k_ in row_keys], bool)
        y_ident = np.array([str(y.partition("=")[2]) == str(fc.default(y.partition("=")[0], is_filter=True)) for y in yel], bool)
        G = len(REGIMES)
        res = {"row_keys": row_keys, "yellows": yel, "cell_row": cell_r, "cell_yellow": cell_y, "n_meas": n_meas, "pos_meas": pos_meas, "row_n": row_n, "row_pos": row_pos,
               "row_ident": row_ident, "cell_ident": row_ident[cell_r] | y_ident[cell_y], "ctx": []}
        t0 = time.time()
        CH = 200000
        for ci, (name, feats) in enumerate(ctx_list):
            xp = self.xpool(feats)
            gl = range(G) if (ci == 0 and regimes_for_first) else range(1)
            rows = {h: np.zeros((nR, len(gl)), np.float32) for h in ("p_pos", "p_net", "e_delta", "d_tim", "d_dd", "d_wr", "fwd") + (("p_v2ctx",) if self.secondary else ())}
            cells = {h: np.zeros((N, len(gl)), np.float16) for h in (("p_pos", "p_net", "e_delta", "d_tim", "d_dd", "d_wr") if ci == 0 else ("p_pos", "e_delta")) + (("p_v2ctx",) if self.secondary else ())}
            rep1 = lambda P_, g_, n_: (np.repeat(P_[0][g_:g_ + 1], n_, 0), np.repeat(P_[1][g_:g_ + 1], n_, 0), np.repeat(P_[2][g_:g_ + 1], n_, 0))
            for gi, g in enumerate(gl):
                c1 = rep1(cR, g, nR)
                f1 = rep1(f0, 0, nR)
                o = self.heads_out(self.raw(s_all, f1, c1, xp), row_n, row_pos, np.full(nR, self.p0["R"]), "R")
                if self.secondary:
                    o["p_v2ctx"] = self.v2ctx_p(self.raw(s2_, rep1(f02, 0, nR), rep1(cR2, g, nR), xp, v2=True), row_n, row_pos, np.full(nR, self.p0["R"]), "R")
                for h in rows:
                    rows[h][:, gi] = o[h]
                for a in range(0, N, CH):
                    r, y = cell_r[a:a + CH], cell_y[a:a + CH]
                    B = len(r)
                    s = (s_all[0][r], s_all[1][r], s_all[2][r])
                    f = (f_all[0][y], f_all[1][y], f_all[2][y])
                    c = (np.broadcast_to(cF[0][g], (B, cF[0].shape[1])), np.broadcast_to(cF[1][g], (B, cF[1].shape[1])), np.full(B, cF[2][g]))
                    o = self.heads_out(self.raw(s, f, c, xp), n_meas[a:a + CH], pos_meas[a:a + CH], cell_prior[a:a + CH], "F")
                    if self.secondary:
                        c2 = (np.broadcast_to(cF2[0][g], (B, cF2[0].shape[1])), np.broadcast_to(cF2[1][g], (B, cF2[1].shape[1])), np.full(B, cF2[2][g]))
                        o["p_v2ctx"] = self.v2ctx_p(self.raw((s2_[0][r], s2_[1][r], s2_[2][r]), (f2_[0][y], f2_[1][y], f2_[2][y]), c2, xp, v2=True),
                                                    n_meas[a:a + CH], pos_meas[a:a + CH], cell_prior[a:a + CH], "F")
                    for h in cells:
                        cells[h][a:a + B, gi] = o[h]
            res["ctx"].append({"name": name, "feats": feats, "rows": rows, "cells": cells})
        res["secs"] = round(time.time() - t0, 1)
        res["n_predictions"] = int(sum((N * len(c_["cells"]) + nR * len(c_["rows"])) * c_["cells"]["p_pos"].shape[1] for c_ in res["ctx"]))
        res["pools"] = (s_all, f_all, f0, cR, cF)
        return res

    def predict_items(self, items, feats, kind):
        """[(set_key, hdr|None)] under one context, regime ALL -> heads dict (production p_pos calibrated)."""
        keys = [k_ for k_, _h in items]
        hdrs = [h or "" for _k, h in items]
        ST = self.set_table(keys)
        FTb = self.filt_table(hdrs)
        C = self.ctx_table(kind)
        Cg = (np.repeat(C[0][:1], len(keys), 0), np.repeat(C[1][:1], len(keys), 0))
        net = self.net
        s, f, c = net.pool(*ST), net.pool(*FTb), net.pool(*Cg)
        n_, p_, pr = [], [], []
        for k_, h in items:
            if h:
                a, b, _s = self.pair_ev(k_, h)
                fi = self.filt_ix.get(h)
                pr.append(self.fo_rate[fi] if fi is not None else self.p0["F"])
            else:
                si = self.set_ix.get(k_)
                a, b = (int(self.ev["n_setR"][si]), int(self.ev["p_setR"][si])) if si is not None else (0, 0)
                pr.append(self.p0["R"])
            n_.append(a)
            p_.append(b)
        xp = self.xpool(feats)
        n_, p_, pr = np.array(n_), np.array(p_), np.array(pr)
        out = self.heads_out(self.raw(s, f, c, xp), n_, p_, pr, kind)
        if self.secondary:
            n2 = self.net2
            out["p_v2ctx"] = self.v2ctx_p(self.raw(n2.pool(*ST), n2.pool(*FTb), n2.pool(*Cg), xp, v2=True), n_, p_, pr, kind)
        return out


def _tab_for(fctx, sw, flt=None):
    tabs = fctx.tabs_of.get(sw) or []
    if flt:
        for t in tabs:
            if flt in set(fctx.tidx["tabs"].get(t, {}).get("yellows", [])):
                return t
    return tabs[0] if tabs else "NA"


def pair_with_context(sc, chains, dflt_feats, positives, out_path, log=_log):
    """USER LAW 2026-10-20 (positives mandatory across the cat_side): every positives_all row and pair, predicted under the LIVE
    chain of each sym_side that has not tried it (PREDICTION; the pilot uses it only to ORDER the must-set, never to skip one)."""
    t0 = time.time()
    fctx = sc.fz.fctx
    syms = positives.get("sym_sides") or []
    items = []
    for x in positives.get("rows") or []:
        sw = str(x["key"]).partition("=")[0]
        items.append({"key": x["key"], "filter": None, "tab": _tab_for(fctx, sw), "n": x.get("n"), "n_pos": x.get("n_pos"), "untried_on": x.get("untried_on") or []})
    for x in positives.get("pairs") or []:
        sw = str(x["key"]).partition("=")[0]
        items.append({"key": x["key"], "filter": x.get("filter"), "tab": _tab_for(fctx, sw, x.get("filter")), "n": x.get("n"), "n_pos": x.get("n_pos"),
                      "untried_on": x.get("untried_on") or []})
    by_sym = collections.defaultdict(list)
    for ii, it in enumerate(items):
        for s_ in it["untried_on"]:
            if 0 <= s_ < len(syms):
                by_sym[syms[s_]].append(ii)
    per_sym, n_pred, ctx_src = {}, 0, collections.Counter()
    for sym, iis in by_sym.items():
        feats = chains.get(sym)
        src = "LIVE_CHAIN" if feats is not None else "CAT_SIDE_DEFAULTS (no live set for this sym_side)"
        ctx_src[src.split(" ")[0]] += 1
        feats = feats if feats is not None else dflt_feats
        out = []
        for kind in ("R", "F"):
            sel = [ii for ii in iis if (items[ii]["filter"] is None) == (kind == "R")]
            if not sel:
                continue
            hd = sc.predict_items([(f"{items[ii]['tab']}|{items[ii]['key']}", items[ii]["filter"]) for ii in sel], feats, kind)
            for j, ii in enumerate(sel):
                out.append([ii, round(float(hd["p_pos"][j]), 5), round(float(hd["e_delta"][j]), 4)] + ([round(float(hd["p_v2ctx"][j]), 5)] if "p_v2ctx" in hd else []))
            n_pred += len(sel)
        out.sort(key=lambda r: -r[1])
        per_sym[sym] = {"context": ctx_descr(src.split(" ")[0] + ":" + sym, feats), "ranked": out}
    doc = {"meta": {"cat_side": sc.cs, "built_at": utc_now(), "production_scorer": sc.prod, "label": "PREDICTION: p_pos_pred / e_delta_pred of each MUST item under the "
                    "sym_side's own live chain; the must-set stays mandatory (p = 1), the prediction only ORDERS it", "positives_built_at": (positives.get("_meta") or {}).get("built_at"),
                    "n_items": len(items), "n_predictions": n_pred, "context_sources": dict(ctx_src), "secs": round(time.time() - t0, 1),
                    "ranked_row": ["item_index", "p_pos_pred", "e_delta_pred"] + (["p_v2ctx_pred (hybrid_v2 under this live chain; secondary, not the production ranking)"] if getattr(sc, "secondary", False) else [])},
           "items": [{k_: v for k_, v in it.items() if k_ != "untried_on"} for it in items], "per_sym": per_sym}
    tmp = Path(str(out_path) + ".tmp")
    with gzip.open(tmp, "wt") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    os.replace(tmp, out_path)
    log(f"[pair_ctx] {sc.cs}: {len(items):,} must items x untried sym_sides -> {n_pred:,} predictions over {len(per_sym)} sym_sides {time.time() - t0:.0f}s")
    return doc


def cmd_score2(a):
    out_dir = Path(a.out_dir)
    ds_dir = Path(a.ds_dir)
    try:
        current = json.loads((ROOT / "data" / "per_sym_settings.json").read_text())
    except Exception:
        current = {}
    full_sets = None
    for cs in a.cat_side or CAT_SIDES:
        t0 = time.time()
        sc = Scorer2(cs, ds_dir=ds_dir)
        sc.evidence()
        enc = sc.enc
        dflt = enc.feats(enc.diff(current.get(cs) or {}))
        if full_sets is None:
            try:
                full_sets = json.loads((ROOT / "data" / "per_sym_full_sets.json").read_text())
            except Exception:
                full_sets = {}
        chains = live_chains(cs, enc, full_sets)
        tops = top_chains(chains)
        ctx_list = [("CAT_SIDE_DEFAULTS", dflt)] + [(t["chain_id"], t["feats"]) for t in tops]
        res = sc.grid(ctx_list)
        c0 = res["ctx"][0]
        npz = out_dir / f"linknet_scores_v2_{cs}.npz"
        meta = {"cat_side": cs, "schema": SCHEMA2, "model_trained_at": sc.meta2["trained_at"], "scored_at": utc_now(), "label": PRED_LABEL, "production_scorer": sc.prod,
                "platt": sc.platt, "regimes": REGIMES, "contexts": [{"name": n, "hash": ctx_hash(f), "n_features": len(f)} for n, f in ctx_list],
                "heads": ["p_pos (production, calibrated)", "p_net (network alone, uncalibrated)", "e_delta", "d_tim", "d_dd", "d_wr", "fwd (rows only)"],
                "n_cells": int(len(res["cell_row"])), "n_rows": len(res["row_keys"]), "predictions": res["n_predictions"]}
        np.savez_compressed(npz, label=np.frombuffer(PRED_LABEL.encode(), np.uint8), regimes=np.array(REGIMES), row_keys=np.array(res["row_keys"]), yellows=np.array(res["yellows"]),
                            cell_row=res["cell_row"], cell_yellow=res["cell_yellow"], cell_n_measured=res["n_meas"], cell_pos_measured=res["pos_meas"],
                            row_n_measured=res["row_n"], row_pos_measured=res["row_pos"], row_identity=res["row_ident"], cell_identity=res["cell_ident"],
                            **{f"cell_{h}": v for h, v in c0["cells"].items()}, **{f"row_{h}": v.astype(np.float16) for h, v in c0["rows"].items()},
                            chain_cell_p_pos=np.stack([c_["cells"]["p_pos"][:, 0] for c_ in res["ctx"][1:]], 1) if len(res["ctx"]) > 1 else np.zeros((len(res["cell_row"]), 0), np.float16),
                            chain_cell_e_delta=np.stack([c_["cells"]["e_delta"][:, 0] for c_ in res["ctx"][1:]], 1) if len(res["ctx"]) > 1 else np.zeros((len(res["cell_row"]), 0), np.float16),
                            chain_row_p_pos=np.stack([c_["rows"]["p_pos"][:, 0] for c_ in res["ctx"][1:]], 1).astype(np.float16) if len(res["ctx"]) > 1 else np.zeros((len(res["row_keys"]), 0), np.float16),
                            **({"chain_cell_p_v2ctx": np.stack([c_["cells"]["p_v2ctx"][:, 0] for c_ in res["ctx"][1:]], 1)} if sc.secondary and len(res["ctx"]) > 1 else {}),
                            meta=np.frombuffer(json.dumps(meta).encode(), np.uint8))
        nm = res["n_meas"]
        row_of = lambda i: int(res["cell_row"][i])

        def cell_item(i, ctx_name, feats, ci, full=True):
            rk = res["row_keys"][res["cell_row"][i]]
            hdr = res["yellows"][res["cell_yellow"][i]]
            tab, _, kv = rk.partition("|")
            sw, _, cnd = kv.partition("=")
            fk, _, fo = hdr.partition("=")
            C_ = res["ctx"][ci]["cells"]
            it = {"switch": sw, "cand": cnd, "filter": fk, "opt": fo, "tab": tab, "cat_side": cs, "regime": "ALL", "label": "PREDICTION", "scorer": sc.prod,
                  "context": ctx_descr(ctx_name, feats), "p_pos_pred": _r(C_["p_pos"][i, 0]), "e_delta_pred": _r(C_["e_delta"][i, 0], 4),
                  "status": PRED_LABEL if nm[i] == 0 else "HAS_TEMPLATE_ROW", "n_measured_pair": int(nm[i]), "pos_measured_pair": int(res["pos_meas"][i])}
            if "p_v2ctx" in C_:
                it["p_v2ctx_pred"] = _r(C_["p_v2ctx"][i, 0])
            if not full:
                return it
            G0 = C_["p_pos"].shape[1]
            it.update({"p_net_pred": _r(C_["p_net"][i, 0]), "forward_pred": _r(c0["rows"]["fwd"][res["cell_row"][i], 0], 4), "d_tim_pred": _r(C_["d_tim"][i, 0], 3),
                       "d_dd_pred": _r(C_["d_dd"][i, 0], 3), "d_wr_pred": _r(C_["d_wr"][i, 0], 3)})
            if G0 > 1:
                gb = int(np.argmax(C_["p_pos"][i].astype(np.float32)))
                it["best_regime"], it["p_pos_pred_best_regime"] = REGIMES[gb], _r(C_["p_pos"][i, gb])
            n_r, p_r, _s = sc.set_ev(rk)
            nf = sc.filt_ix.get(hdr)
            si = sc.set_ix.get(rk)
            ex = sc.ctx_by_pair.get((si, nf), []) if (si is not None and nf is not None) else []
            closest = sc.closest_context(ex, feats) if ex else sc.closest_context(sc.ctx_by_set.get(si, []) if si is not None else [], feats)
            for cc in closest:
                cc["of"] = "this exact pair" if ex else "the naked row of this switch=cand (the pair itself has no measured context)"
            it["nearest_evidence"] = ([{"key": kv, "filter": None, "n": n_r, "pos": p_r, "pos_rate": _r(p_r / n_r, 4) if n_r else None, "kind": "MEASURED naked row of this switch=cand"},
                                       {"key": hdr, "n": int(sc.ev["nfo"][nf]) if nf is not None else 0, "pos": int(sc.ev["pfo"][nf]) if nf is not None else 0,
                                        "kind": "MEASURED marginal of this filter=opt across switches"}] + closest)
            return it

        p0 = c0["cells"]["p_pos"][:, 0].astype(np.float32)
        cand = np.nonzero((nm < 3) & ~res["cell_ident"])[0]
        order = cand[np.argsort(-p0[cand], kind="stable")]
        top = diversity_cap(order, row_of, k=PROPOSALS_K * 2)
        props = [cell_item(i, "CAT_SIDE_DEFAULTS", dflt, 0) for i in top]
        by_chain = []
        chain_rank_key = "p_v2ctx" if sc.secondary else "p_pos"
        for ci, t in enumerate(tops, start=1):
            pc = res["ctx"][ci]["cells"][chain_rank_key][:, 0].astype(np.float32)
            oc = cand[np.argsort(-pc[cand], kind="stable")]
            tc = diversity_cap(oc, row_of, k=CHAIN_K)
            by_chain.append({"chain_id": t["chain_id"], "ranked_by": ("p_v2ctx_pred: hybrid_v2, CONDITIONAL on this chain; the production scorer " + sc.prod + " is context-free on this cat_side "
                                                                     "(v2 did not beat v1 on held-out never-tried pairs AND contexts), so its ranking is the same under every chain")
                             if sc.secondary else "p_pos_pred (production " + sc.prod + ", conditional on this chain)",
                             "sym_sides": t["sym_sides"], "n_sym_sides": t["n_sym_sides"], "typicality": t["typicality"],
                             "context": ctx_descr(t["chain_id"], t["feats"], sym_sides=t["sym_sides"][:10]), "active_filter_options": [f[4:] for f in t["feats"] if f.startswith("X.f:")],
                             "items": [cell_item(i, t["chain_id"], t["feats"], ci, full=False) for i in tc]})
        rows_untried = []
        rp = c0["rows"]["p_pos"][:, 0]
        rc = np.nonzero((res["row_n"] < 3) & ~res["row_ident"])[0]
        for ri in rc[np.argsort(-rp[rc], kind="stable")][:200]:
            rk = res["row_keys"][ri]
            tab, _, kv = rk.partition("|")
            sw, _, cnd = kv.partition("=")
            si = sc.set_ix.get(rk)
            rows_untried.append({"switch": sw, "cand": cnd, "filter": None, "opt": None, "tab": tab, "cat_side": cs, "regime": "ALL", "label": "PREDICTION", "scorer": sc.prod,
                                 "context": ctx_descr("CAT_SIDE_DEFAULTS", dflt), "p_pos_pred": _r(rp[ri]), "e_delta_pred": _r(c0["rows"]["e_delta"][ri, 0], 4),
                                 "status": PRED_LABEL if res["row_n"][ri] == 0 else "HAS_TEMPLATE_ROW", "n_measured_row": int(res["row_n"][ri]), "pos_measured_row": int(res["row_pos"][ri]),
                                 "nearest_evidence": sc.closest_context(sc.ctx_by_set.get(si, []) if si is not None else [], dflt)})
        # MUST items under each sym_side's live chain
        pwc_path = out_dir / f"linknet_pair_with_context_{cs}.json.gz"
        pwc_top = {}
        try:
            positives = json.loads(gzip.open(ROOT / "data" / "learn" / f"positives_all_{cs}.json.gz", "rt").read())
        except Exception:
            positives = None
        pwc_meta = None
        if positives and (positives.get("_meta") or {}).get("cat_side") in (None, cs):
            pw = pair_with_context(sc, chains, dflt, positives, pwc_path)
            pwc_meta = pw["meta"]
            for sym, blk in pw["per_sym"].items():
                lst = []
                for rr in blk["ranked"][:PWC_TOP]:
                    ii, p_, e_ = rr[:3]
                    it = pw["items"][ii]
                    sw, _, cnd = str(it["key"]).partition("=")
                    fk, _, fo = str(it["filter"] or "").partition("=")
                    lst.append({"switch": sw, "cand": cnd, "filter": fk or None, "opt": fo or None, "tab": it["tab"], "p_pos_pred": p_, "e_delta_pred": e_,
                                "measured_elsewhere_n": it["n"], "measured_elsewhere_pos": it["n_pos"], "label": "PREDICTION", **({"p_v2ctx_pred": rr[3]} if len(rr) > 3 else {})})
                pwc_top[sym] = {"context": blk["context"], "top": lst}
        v1p = LEARN_DIR / f"linknet_proposals_{cs}.json"
        v1doc = {}
        try:
            v1doc = json.loads(v1p.read_text())
        except Exception:
            pass
        # NEEDS_ROW / FIXED_STOP items: enumeration and evidence from the v1 file, p re-predicted by THIS production scorer under the
        # cat_side defaults so every p in the digest is on one scale (the v1 value is kept as p_pos_pred_v1)
        carried = {}
        for sec in ("needs_row", "fixed_stop_family"):
            lst = [dict(x) for x in (v1doc.get(sec) or [])]
            if lst:
                hd = sc.predict_items([(f"{x.get('tab') or _tab_for(sc.fz.fctx, x['switch'])}|{x['switch']}={x['cand']}", None) for x in lst], dflt, "R")
                for j, x in enumerate(lst):
                    x["p_pos_pred_v1"] = x.get("p_pos_pred")
                    x["p_pos_pred"] = _r(hd["p_pos"][j])
                    x["e_delta_pred"] = _r(hd["e_delta"][j], 4)
                    if "p_v2ctx" in hd:
                        x["p_v2ctx_pred"] = _r(hd["p_v2ctx"][j])
                    x["scorer"] = sc.prod
                    x["context"] = ctx_descr("CAT_SIDE_DEFAULTS", dflt)
                lst.sort(key=lambda x: -(x["p_pos_pred"] or 0))
            carried[sec] = lst
        doc = {"meta": {"schema": SCHEMA2, "cat_side": cs, "built_at": utc_now(), "model_trained_at": sc.meta2["trained_at"], "corpus_last_ts": sc.meta2.get("corpus_last_ts"),
                        "production_scorer": sc.prod, "platt": sc.platt, "diversity_cap_per_row": DIVERSITY_CAP,
                        "production_is_context_free": bool(sc.prod in ("v1", "hybrid_v1", "counts")),
                        "secondary_p_v2ctx": ("p_v2ctx_pred = hybrid_v2 conditional on the item's context, shown because the production scorer is v1-based; a PREDICTION, "
                                              "not the ranking of template_untried / pair_with_context") if sc.secondary else None,
                        "label": "every *_pred value here is a PREDICTION of tools/v18_linknet.py (v2, conditional on the chain context named in each item), never a measurement; "
                                 "nearest_evidence items are MEASURED; nothing is adopted except by the real engine",
                        "scores_npz": str(npz), "n_predictions_scored": res["n_predictions"], "secs_grid": res["secs"],
                        "contexts": [ctx_descr("CAT_SIDE_DEFAULTS", dflt, source="data/per_sym_settings.json[" + cs + "] vs template bold")] +
                                    [ctx_descr(t["chain_id"], t["feats"], source="data/per_sym_full_sets.json", sym_sides=t["sym_sides"][:10], n_sym_sides=t["n_sym_sides"]) for t in tops],
                        "live_chains": len(chains), "distinct_live_chains": len(set(chains.values())),
                        "template_cells": int(len(res["cell_row"])), "template_cells_n_lt_3": int((nm < 3).sum()), "template_cells_never_measured": int((nm == 0).sum()),
                        "unknown_feature_hits_at_scoring": dict(sc.unknown_feature_hits), "unknown_context_features_at_scoring": dict(sc.unknown_context_features.most_common(30)),
                        "pair_with_context_file": str(pwc_path) if pwc_meta else None,
                        "needs_row_source": "enumeration + nearest evidence from the v1 proposals file " + str(v1p) + "; p re-predicted by the production scorer (p_pos_pred_v1 kept)"},
               "template_untried": props, "template_untried_by_chain": by_chain, "rows_untried": rows_untried,
               "pair_with_context": {"meta": pwc_meta, "per_sym_top": pwc_top},
               "needs_row": carried.get("needs_row") or [], "fixed_stop_family": carried.get("fixed_stop_family") or []}
        p_out = out_dir / f"linknet_proposals_v2_{cs}.json"
        tmp = p_out.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, default=str))
        os.replace(tmp, p_out)
        # implied links of the conditional model (definitions in linknet_status.json)
        n_set_all = len({Featurizer.split_combo(k_)[0] for k_ in sc.K["sets"]} | set(res["row_keys"]))
        n_opt_all = len(set(sc.K["filts"]) | set(res["yellows"]))
        n_sym = len({c_[2] for c_ in sc.K["ctxs"] if c_[2]})
        n_ctx_known = len(sc.K.get("xctxs") or []) + len(set(chains.values())) + 1
        VX = int(sc.net2.VX)
        base = n_set_all * (n_opt_all + 1) * len(REGIMES) * 4
        write_status(cs, "scored_v2", production_scorer=sc.prod, links_scored_explicit_v2=int((len(res["cell_row"]) + len(res["row_keys"])) * (len(REGIMES) + len(tops))),
                     predictions_stored_v2=res["n_predictions"], links_implied_v2_in_distribution=int(base * n_ctx_known),
                     links_implied_v2_factors={"settings": n_set_all, "filter_options_plus_none": n_opt_all + 1, "regimes": len(REGIMES), "tiers": 4,
                                               "chain_contexts_known (measured + live + defaults; each carries its own sym_side)": n_ctx_known},
                     links_implied_v2_one_context_node=int(base * (n_sym + 1) * (VX + 1)),
                     links_implied_v2_one_context_node_factors={"v1 implied (settings x opts+1 x regimes x sym_sides+1 x tiers)": int(base * (n_sym + 1)), "context_vocab_plus_none": VX + 1},
                     context_vocab=VX, feature_pair_links_v2=int((len(sc.fz.names) + VX) * (len(sc.fz.names) + VX - 1) // 2),
                     proposals_v2_file=str(p_out), scores_v2_file=str(npz), pair_with_context_file=str(pwc_path) if pwc_meta else None)
        print(f"[score2] {cs}: prod={sc.prod} {res['n_predictions']:,} predictions over {len(ctx_list)} contexts in {res['secs']}s; untried {len(props)} (cap {DIVERSITY_CAP}/row); "
              f"chains {len(tops)}; pair_with_context {pwc_meta and pwc_meta['n_predictions']}; {p_out} {p_out.stat().st_size:,} B; {time.time() - t0:.0f}s", flush=True)
    return 0


# ------------------------------------------------------------------------------------------------ CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dataset")
    d.add_argument("--spill-dir", action="append", required=True)
    d.add_argument("--tmp-dir", required=True)
    d.add_argument("--out-dir", default=str(DS_DIR))
    d.add_argument("--keep-sorted", action="store_true")
    d.add_argument("--ctx-dir", default=None, help="v2: data/learn/linknet/v2 (ctxjoin_<CS>.pkl.gz) -> every eval carries its chain context")
    j = sub.add_parser("ctxjoin")
    j.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    j.add_argument("--store", default=str(ROOT / "data" / "experience"))
    j.add_argument("--out-dir", default=str(DS_DIR / "v2"))
    t = sub.add_parser("train")
    t.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    t.add_argument("--ds-dir", default=str(DS_DIR))
    t.add_argument("--k", type=int, default=16)
    t.add_argument("--hidden", type=int, default=64)
    t.add_argument("--batch", type=int, default=4096)
    t.add_argument("--lr", type=float, default=2e-3)
    t.add_argument("--max-epochs", type=int, default=20)
    t.add_argument("--max-train", type=int, default=None)
    t2 = sub.add_parser("train2", help="v2: v1 vs v2 (chain context) on the conditional dataset, production scorer per cat_side")
    t2.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    t2.add_argument("--ds-dir", default=str(DS_DIR / "v2"))
    t2.add_argument("--k", type=int, default=16)
    t2.add_argument("--hidden", type=int, default=64)
    t2.add_argument("--batch", type=int, default=4096)
    t2.add_argument("--lr", type=float, default=2e-3)
    t2.add_argument("--max-epochs", type=int, default=20)
    t2.add_argument("--max-train", type=int, default=None)
    s = sub.add_parser("score")
    s.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    s.add_argument("--ds-dir", default=str(DS_DIR))
    s.add_argument("--out-dir", default=str(LEARN_DIR))
    rd = sub.add_parser("redecide", help="v2: apply the v1 gate to report_v2_<CS>.json (+ train the v1 final model if needed)")
    rd.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    rd.add_argument("--ds-dir", default=str(DS_DIR / "v2"))
    rd.add_argument("--k", type=int, default=16)
    rd.add_argument("--hidden", type=int, default=64)
    rd.add_argument("--batch", type=int, default=4096)
    rd.add_argument("--lr", type=float, default=2e-3)
    rd.add_argument("--max-epochs", type=int, default=20)
    s2 = sub.add_parser("score2", help="v2: production scorer x (cat_side defaults + top live chains) grid, proposals v2, pair_with_context")
    s2.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    s2.add_argument("--ds-dir", default=str(DS_DIR / "v2"))
    s2.add_argument("--out-dir", default=str(LEARN_DIR))
    c = sub.add_parser("calibrate")
    c.add_argument("--spill-dir", action="append", required=True)
    c.add_argument("--tmp-dir", required=True)
    c.add_argument("--out-dir", default=str(LEARN_DIR))
    c.add_argument("--since", default=None)
    c.add_argument("--note", default=None)
    c.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    c.add_argument("--scores", choices=("auto", "v1", "v2"), default="auto", help="auto = linknet_scores_v2_<CS>.npz when present")
    a = ap.parse_args(argv)
    return {"dataset": cmd_dataset, "ctxjoin": cmd_ctxjoin, "train2": cmd_train2, "score2": cmd_score2, "redecide": cmd_redecide, "train": cmd_train, "score": cmd_score, "calibrate": cmd_calibrate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
