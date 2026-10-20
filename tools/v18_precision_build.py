#!/usr/bin/env python3
"""v18_precision_build - precise, concise, DEFAULT-RELATIVE switch cards per cat_side from the complete evaluation corpus.

USER LAW (2026-10-20) at the top of every output: "ALMOST EVERY SWITCH has to be negative as we turn the positives into
defaults, so they will not produce a result until an alternative is tested - KEEP THAT CLEAR." Every delta in the corpus is a
candidate measured against the DEFAULT IN FORCE at that eval (the chain value, resolved per row by
tools/v18_precision_extract.py: P promoted on the board / B,BI template bold / Z,A,NB start-set override). A negative delta
against a promoted default is the expected outcome, not damage. Verdicts are therefore per CANDIDATE VALUE relative to the
default, and switch-level states are SETTLED_WINNING / SETTLED_OFF / CONTESTED / UNTESTED_DEFAULT / INERT / UNTRIED.

USER LAW (2026-10-20): "if a certain switch/filter combination worked on another sym_side for the same cat_side it HAS to be
tried on any other sym_side that has not tried it yet" -> data/learn/positives_all_<CS>.json(.gz): every key and pair with
>= 1 positive eval, its pos_syms, tried_on, untried_on (complete, no cap).

Inputs (read-only): data/learn/precision/extract/*.tsv.gz (all hosts, deduped by the brain row key `rk`), the cat_side
TEMPLATE (v18_learn_common.template_index), data/brain/v18_brain_<CS>.json (live co-occurrence per key), the coarse
data/learn/switch_profile_<CS>.json (before-numbers, exits co-occurrence), data/learn/menu_measure + veto_measure (paired
real evals), data/learn/precision/exitshift_*.json (causal exit shifts), data/brain_scratch/spill_mac/spill_native_*.tsv.gz
(native in-context quartets), data/learn/replay_sets.jsonl.gz (published sets, universe).

Writes: data/learn/switch_cards_<CS>.{json,md}, data/learn/positives_all_<CS>.json[.gz], data/learn/precision/stats_<CS>.json,
and (with --replace, after backups/) data/learn/switch_profile_<CS>.{json,md} + data/learn/needed_filters_<CS>.json in place
(same keys + new fields). Four cat_sides = four computations; nothing is pooled across them.

    python3 tools/v18_precision_build.py split            # dedupe all extract spills -> per-cat_side event files
    python3 tools/v18_precision_build.py build [--cat-side CS] [--replace]
"""
import argparse
import collections
import glob
import gzip
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
from scipy import stats as SS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
LEARN = ROOT / "data" / "learn"
PREC = LEARN / "precision"
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD")
EPS = 1e-9
SCHEMA = "v18_switch_profile/2-precision"
CARD_SCHEMA = "v18_switch_cards/1"
LAW_DEFAULTS = ("USER LAW 2026-10-20: \"ALMOST EVERY SWITCH has to be negative as we turn the positives into defaults, so they will not "
                "produce a result until an alternative is tested - KEEP THAT CLEAR.\" Every delta here is a candidate measured against the "
                "default IN FORCE at that eval (promoted chain value / template bold / start-set override). A negative delta against a "
                "promoted default is the expected outcome, not damage: verdicts are per candidate value relative to that default; a switch "
                "whose default won and whose alternatives lose is SETTLED_WINNING (it works), not damaging.")
LAW_POSITIVES = ("USER LAW 2026-10-20: \"if a certain switch/filter combination worked on another sym_side for the same cat_side it HAS to be "
                 "tried on any other sym_side that has not tried it yet\" -> positives_all_<CAT_SIDE>.json(.gz) lists every positive key and pair "
                 "with tried_on / untried_on (complete, uncapped).")
# Thresholds (stated in every output; calibrated in the report against the fleet's known cases)
T = {
    "ci": 0.90,            # credible / confidence level of every interval
    "m0": 8.0,             # Beta prior strength at cat_side level (the brain's M_SHRINK)
    "p_w": 0.10,           # WORKS: lower bound of P(pos) > p_w  (and shrunk mean delta > 0)
    "p_c": 0.03,           # CONTESTED: an alternative with >= 2 positive sym_sides and P(pos) lower bound > p_c, or a regime LB > p_w
    "p_d": 0.10,           # DAMAGING: upper bound of P(pos) < p_d AND mean-delta upper bound < 0 AND Σdelta < 0 with n >= n_min
    "n_min": 10,           # evals for any settled verdict
    "s_min": 3,            # sym_sides for any settled verdict / any interval on a mean
    "eps": 1e-6,           # |delta| below eps = no effect (INERT)
    "inert_share": 0.90,   # INERT: >= 90% of the evals |delta| < eps on >= s_min sym_sides
    "fdr_q": 0.10,         # Benjamini-Hochberg q across every template pair of the cat_side
    "sd_floor": 0.01,      # pp; floor on the sd of cluster means in t-tests (degenerate identical deltas)
    "needed_max": 24,
    "card_bytes": 2048,
    "cards_file_bytes": 10 * 1024 * 1024,
    "md_lines": 300,
}
LOOSE = frozenset({"OFF", "False", "0", "0.0", "None", "", "none", "off"})
Z90 = SS.norm.ppf(0.95)


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def cat_side_of(ss):
    s = str(ss or "")
    if "_" not in s:
        return None
    sym, side = s.rsplit("_", 1)
    if side not in ("LONG", "SHORT") or not sym:
        return None
    return ("CRYPTO" if sym.upper().endswith(CRYPTO_QUOTES) else "STOCKS") + "_" + side


def r4(x, nd=4):
    if x is None:
        return None
    try:
        if not math.isfinite(float(x)):
            return None
    except Exception:
        return None
    return round(float(x), nd)


# ------------------------------------------------------------------------------------------------ split (dedupe)

def split(a):
    files = sorted(glob.glob(str(PREC / "extract" / "*.tsv.gz")))
    out = {cs: gzip.open(PREC / f"events_{cs}.tsv.gz.part", "wt", compresslevel=3) for cs in CAT_SIDES}
    seen = set()
    cnt = collections.Counter()
    per_src = collections.defaultdict(collections.Counter)
    t0 = time.time()
    for f in files:
        src = Path(f).name
        with gzip.open(f, "rt") as fh:
            for line in fh:
                cnt["lines"] += 1
                ts, _, js = line.partition("\t")
                try:
                    ev = json.loads(js)
                except ValueError:
                    cnt["bad_json"] += 1
                    continue
                e = ev.get("e")
                cs = cat_side_of(ev.get("s"))
                if cs is None:
                    cnt["refused_cat_side"] += 1
                    continue
                rk = ev.get("rk")
                if rk is not None:
                    if (e, rk) in seen:
                        cnt[f"dup_{e}"] += 1
                        per_src[src]["dup_" + str(e)] += 1
                        continue
                    seen.add((e, rk))
                per_src[src][str(e)] += 1
                cnt[f"{cs}:{e}"] += 1
                out[cs].write(line)
        print(f"[split] {src} {time.time() - t0:.0f}s", flush=True)
    for cs, fh in out.items():
        fh.close()
        os.replace(PREC / f"events_{cs}.tsv.gz.part", PREC / f"events_{cs}.tsv.gz")
    rep = {"at": utc(), "files": files, "counters": dict(cnt), "per_source": {k: dict(v) for k, v in per_src.items()}, "secs": round(time.time() - t0, 1)}
    (PREC / "split_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps(rep["counters"], indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ numeric helpers

class Idx:
    def __init__(self):
        self.m, self.names = {}, []

    def __call__(self, k):
        i = self.m.get(k)
        if i is None:
            i = self.m[k] = len(self.names)
            self.names.append(k)
        return i


def beta_ci(a, b, level=0.90):
    lo = SS.beta.ppf((1 - level) / 2, a, b)
    hi = SS.beta.ppf(1 - (1 - level) / 2, a, b)
    return lo, hi


def icc_binary(gkey, n_g, pos_g):
    """ANOVA estimator of the intra-sym_side correlation of positive outcomes, clusters = (key, sym_side) within keys."""
    N = n_g.sum()
    if N < 10:
        return 0.05
    p_g = pos_g / np.maximum(n_g, 1)
    key_n = np.bincount(gkey, weights=n_g)
    key_pos = np.bincount(gkey, weights=pos_g)
    p_k = key_pos / np.maximum(key_n, 1)
    ssw = (pos_g * (1 - p_g) ** 2 + (n_g - pos_g) * p_g ** 2).sum()
    ssb = (n_g * (p_g - p_k[gkey]) ** 2).sum()
    G, K = len(n_g), int((key_n > 0).sum())
    if G - K <= 0 or N - G <= 0:
        return 0.05
    msw, msb = ssw / (N - G), ssb / (G - K)
    n0 = (N - (np.bincount(gkey, weights=n_g ** 2) / np.maximum(key_n, 1)).sum()) / max(1, G - K)
    rho = (msb - msw) / (msb + (n0 - 1) * msw) if (msb + (n0 - 1) * msw) > 0 else 0.05
    return float(min(0.9, max(0.01, rho)))


def cluster_stats(gkey, gval, gw, nkeys):
    """Per key: mean of cluster means (clusters weighted equally where gw > 0), sd, k, CI (normal, Z90 / sqrt k), t one-sided p."""
    ok = gw > 0
    k = np.bincount(gkey[ok], minlength=nkeys).astype(float)
    s1 = np.bincount(gkey[ok], weights=gval[ok], minlength=nkeys)
    s2 = np.bincount(gkey[ok], weights=gval[ok] ** 2, minlength=nkeys)
    mean = np.where(k > 0, s1 / np.maximum(k, 1), np.nan)
    var = np.where(k > 1, (s2 - k * mean ** 2) / np.maximum(k - 1, 1), np.nan)
    sd = np.sqrt(np.maximum(var, 0))
    se = np.maximum(sd, T["sd_floor"]) / np.sqrt(np.maximum(k, 1))
    lo = np.where(k >= T["s_min"], mean - Z90 * se, np.nan)
    hi = np.where(k >= T["s_min"], mean + Z90 * se, np.nan)
    tstat = mean / se
    p_gt = np.where(k >= T["s_min"], SS.t.sf(tstat, np.maximum(k - 1, 1)), 1.0)
    p_lt = np.where(k >= T["s_min"], SS.t.cdf(tstat, np.maximum(k - 1, 1)), 1.0)
    return {"k": k, "mean": mean, "sd": sd, "lo": lo, "hi": hi, "p_gt": np.nan_to_num(p_gt, nan=1.0), "p_lt": np.nan_to_num(p_lt, nan=1.0)}


def bh(pvals, m_total, q):
    """Benjamini-Hochberg over m_total hypotheses (untested ones have p = 1 and never reject). -> boolean mask for pvals."""
    p = np.asarray(pvals, float)
    order = np.argsort(p)
    ranks = np.arange(1, len(p) + 1)
    thr = q * ranks / max(1, m_total)
    passed = p[order] <= thr
    out = np.zeros(len(p), bool)
    if passed.any():
        kmax = np.max(np.nonzero(passed)[0])
        out[order[: kmax + 1]] = True
    return out


def group(keys_tuple):
    """Unique rows of stacked int arrays -> (uniq matrix, inverse)."""
    arr = np.stack(keys_tuple, axis=1)
    u, inv = np.unique(arr, axis=0, return_inverse=True)
    return u, inv.reshape(-1)


# ------------------------------------------------------------------------------------------------ evidence loading

def load_cat_side(cs, bolds):
    """Stream events_<CS>.tsv.gz -> numpy evidence tables + board context + promotions + chain starts."""
    keys, filts, syms, boards = Idx(), Idx(), Idx(), Idx()
    R = collections.defaultdict(list)
    C = collections.defaultdict(list)
    board_meta = {}
    first_cv = {}
    promos = []
    ids = collections.Counter()
    syn = collections.defaultdict(list)
    abl = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
    remedies = collections.defaultdict(lambda: [0, 0, 0.0])
    phase = collections.defaultdict(lambda: [0, 0, 0.0])
    cnt = collections.Counter()
    p = PREC / f"events_{cs}.tsv.gz"
    with gzip.open(p, "rt") as fh:
        for line in fh:
            ev = json.loads(line.partition("\t")[2])
            e = ev.get("e")
            s = ev.get("s")
            if e == "board":
                bi = boards(ev["b"])
                board_meta[bi] = {"s": s, "t": ev.get("t"), "bh": ev.get("bh"), "ti": ev.get("ti"), "rd": ev.get("rd")}
                continue
            if e == "id":
                k = ev.get("k")
                ids[(k, s)] += 1
                cnt["id_rows"] += 1
                b = ev.get("b")
                continue
            if e == "abl":
                a = abl[(ev.get("k"), ev.get("f"))]
                a[0] += 1
                a[1] += 1 if ev.get("kill") else 0
                a[2] += float(ev.get("dd") or 0.0)
                a[3] += float(ev.get("eff") or 0.0)
                continue
            if e == "remedy":
                m = remedies[(ev.get("cls"), ev.get("lab"))]
                m[0] += 1
                m[1] += 1 if (ev.get("v") and (ev.get("d") or 0) > EPS) else 0
                m[2] += float(ev.get("d") or 0.0)
                continue
            if e == "phase":
                ph = phase[(ev.get("k"), ev.get("ph"))]
                ph[0] += 1
                ph[1] += 1 if (ev.get("v") and (ev.get("d") or 0) > EPS) else 0
                ph[2] += float(ev.get("d") or 0.0)
                continue
            if e != "row":
                continue
            cnt["rows"] += 1
            k = ev["k"]
            sw, _, cand = k.partition("=")
            ki = keys(k)
            si = syms(s)
            bname = ev.get("b")
            bi = boards(bname) if bname else -1
            if bi >= 0 and bi not in board_meta:
                board_meta[bi] = {"s": s, "t": ev.get("t"), "bh": ev.get("bh"), "ti": ev.get("ti"), "rd": None}
            ck = ev.get("ck") or "U"
            cv = ev.get("cv")
            bold = bolds.get(sw)
            if bi >= 0 and (bi, sw) not in first_cv and ck != "P":
                first_cv[(bi, sw)] = (cv, ck)
            if ev.get("p"):
                promos.append((bi, sw, cand, ev.get("t")))
            n = ev.get("n")
            if n is not None and n[-1] == 1 and n[0] is not None:
                cnt["fresh_naked"] += 1
                R["key"].append(ki)
                R["sym"].append(si)
                R["board"].append(bi)
                R["d"].append(n[0])
                R["valid"].append(n[1])
                R["secs"].append(n[2] or 0.0)
                for j, nm in enumerate(("tim", "tr", "dd", "sh", "wr")):
                    R[nm].append(np.nan if n[3 + j] is None else n[3 + j])
                R["ck"].append(ck)
                R["cv_bold"].append(1 if (cv is not None and bold is not None and cv == bold) else (0 if cv is not None else -1))
                R["is_bold"].append(1 if cand == bold else 0)
                R["prom"].append(1 if ev.get("p") else 0)
            elif n is not None:
                cnt["cached_naked_rows"] += 1
            for y in ev.get("y") or []:
                hdr, md, cvalid, secs = y[0], y[1], y[2], y[3]
                if md is None:
                    continue
                cnt["fresh_cells"] += 1
                C["key"].append(ki)
                C["filt"].append(filts(hdr))
                C["sym"].append(si)
                C["board"].append(bi)
                C["md"].append(md)
                C["valid"].append(cvalid)
                C["secs"].append(secs or 0.0)
                C["cell"].append(y[10] if len(y) > 10 and y[10] is not None else np.nan)
                C["tim"].append(np.nan if y[4] is None else y[4])
                C["dd"].append(np.nan if y[6] is None else y[6])
            for hdr, sv in ev.get("sy") or []:
                if sv is not None:
                    syn[(ki, hdr)].append((si, sv))
    Ra = {k: np.asarray(v, dtype=(object if k == "ck" else float)) for k, v in R.items()}
    for k in ("key", "sym", "board", "valid", "cv_bold", "is_bold", "prom"):
        if k in Ra:
            Ra[k] = Ra[k].astype(np.int64)
    Ca = {k: np.asarray(v, dtype=float) for k, v in C.items()}
    for k in ("key", "filt", "sym", "board", "valid"):
        if k in Ca:
            Ca[k] = Ca[k].astype(np.int64)
    return {"keys": keys, "filts": filts, "syms": syms, "boards": boards, "R": Ra, "C": Ca, "board_meta": board_meta, "first_cv": first_cv,
            "promos": promos, "ids": ids, "syn": syn, "abl": abl, "remedies": remedies, "phase": phase, "cnt": cnt}


def regime_cuts(board_meta):
    bh = np.array([m["bh"] for m in board_meta.values() if m.get("bh") is not None], float)
    if len(bh) < 10:
        return None
    return [float(x) for x in np.quantile(bh, [0.2, 0.4, 0.6, 0.8])]


# ------------------------------------------------------------------------------------------------ board bh for boards without BOARD_CTX

_KL = {}


def _ep(s):
    from datetime import datetime, timezone
    return datetime.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def _series(sym, stocks):
    if (sym, stocks) in _KL:
        return _KL[(sym, stocks)]
    p = ROOT / "klines_cache" / ("tradier" if stocks else "") / f"{sym}_1h.json"
    out = None
    if p.exists():
        try:
            d = json.loads(p.read_text())
            ts = np.array([_ep(x["timestamp"]) for x in d])
            cl = np.array([float(x["close"]) for x in d])
            o = np.argsort(ts)
            out = (ts[o], cl[o])
        except Exception:
            out = None
    _KL[(sym, stocks)] = out
    return out


def bh_from_klines(sym_side, t, cs):
    """Side-adjusted buy-and-hold of the board window from exchange-native 1h klines (crypto 30 d, stocks 45 calendar d - the
    windows that reproduce the logged BOARD_CTX bh, see fill_bh validation). None when the klines do not cover the window."""
    stocks = cs.startswith("STOCKS")
    days = 45 if stocks else 30
    s = _series(sym_side.rsplit("_", 1)[0], stocks)
    if s is None or t is None:
        return None
    ts, cl = s
    e = _ep(t)
    i = np.searchsorted(ts, e, side="right") - 1
    j = np.searchsorted(ts, e - days * 86400, side="left")
    if i < 0 or j >= len(ts) or j >= i or ts[j] - (e - days * 86400) > 3 * 86400 or e - ts[i] > 3 * 86400:
        return None
    r = (cl[i] / cl[j] - 1) * 100
    return float(-r if cs.endswith("SHORT") else r)


def fill_bh(cs, board_meta):
    err = []
    for m in board_meta.values():
        if m.get("bh") is not None:
            v = bh_from_klines(m["s"], m.get("t"), cs)
            if v is not None:
                err.append(abs(v - m["bh"]))
            m["bh_src"] = "BOARD_CTX"
    filled = 0
    for m in board_meta.values():
        if m.get("bh") is None:
            v = bh_from_klines(m["s"], m.get("t"), cs)
            if v is not None:
                m["bh"], m["bh_src"] = v, "klines_1h"
                filled += 1
            else:
                m["bh_src"] = "none"
    return {"validated_on_boards": len(err), "median_abs_err_pp": r4(float(np.median(err))) if err else None,
            "p90_abs_err_pp": r4(float(np.quantile(err, 0.9))) if err else None, "filled_boards": filled,
            "still_unknown": sum(1 for m in board_meta.values() if m.get("bh") is None)}


REGIMES = ("STRONG_DOWN", "DOWN", "FLAT", "UP", "STRONG_UP", "UNK")


def regime_of(bh, cuts):
    if bh is None or cuts is None:
        return 5
    return int(np.searchsorted(cuts, bh, side="right"))


# ------------------------------------------------------------------------------------------------ estimators

def key_estimates(key, sym, d, valid, nkeys, secs, p0=None, rho=None):
    """Hierarchical Beta (sym_side -> cat_side) P(pos) with design-effect weights + cluster-robust mean-delta / E[d|pos]."""
    pos = ((valid == 1) & (d > EPS)).astype(float)
    neg = ((valid == 1) & (d < -EPS)).astype(float)
    inv = (valid != 1).astype(float)
    zero = ((valid == 1) & (np.abs(d) < T["eps"])).astype(float)
    u, g = group((key, sym))
    gk = u[:, 0]
    n_g = np.bincount(g).astype(float)
    pos_g = np.bincount(g, weights=pos)
    if p0 is None:
        p0 = float(pos.sum() / max(1, len(pos)))
    if rho is None:
        rho = icc_binary(gk, n_g, pos_g)
    f_g = 1.0 / (1.0 + (n_g - 1.0) * rho)
    A = np.bincount(gk, weights=f_g * pos_g, minlength=nkeys)
    Nf = np.bincount(gk, weights=f_g * n_g, minlength=nkeys)
    a_post = T["m0"] * p0 + A
    b_post = T["m0"] * (1 - p0) + Nf - A
    lo, hi = beta_ci(a_post, b_post, T["ci"])
    pm = a_post / (a_post + b_post)
    vd = np.where(valid == 1, d, 0.0)
    nv_g = np.bincount(g, weights=(valid == 1).astype(float))
    mean_g = np.bincount(g, weights=vd) / np.maximum(nv_g, 1)
    cm = cluster_stats(gk, mean_g, nv_g, nkeys)
    pd_ = np.where(pos > 0, d, 0.0)
    npos_g = pos_g
    mpos_g = np.bincount(g, weights=pd_) / np.maximum(npos_g, 1)
    cp = cluster_stats(gk, mpos_g, npos_g, nkeys)
    mu0 = float(np.median(d[pos > 0])) if pos.sum() > 0 else 0.0
    kpos = cp["k"]
    e_pos_shr = np.where(kpos > 0, (kpos * np.nan_to_num(cp["mean"]) + 2 * mu0) / (kpos + 2), np.nan)
    out = {
        "n": np.bincount(key, minlength=nkeys).astype(int), "pos": np.bincount(key, weights=pos, minlength=nkeys).astype(int),
        "neg": np.bincount(key, weights=neg, minlength=nkeys).astype(int), "inv": np.bincount(key, weights=inv, minlength=nkeys).astype(int),
        "zero": np.bincount(key, weights=zero, minlength=nkeys).astype(int),
        "sum_d": np.bincount(key, weights=vd, minlength=nkeys), "sum_pos": np.bincount(key, weights=pd_, minlength=nkeys),
        "secs": np.bincount(key, weights=secs, minlength=nkeys),
        "n_syms": np.bincount(gk, minlength=nkeys).astype(int), "pos_syms": np.bincount(gk, weights=(pos_g > 0).astype(float), minlength=nkeys).astype(int),
        "neg_syms": np.bincount(gk, weights=(np.bincount(g, weights=neg) > 0).astype(float), minlength=nkeys).astype(int),
        "zero_syms": np.bincount(gk, weights=((np.bincount(g, weights=zero) == n_g)).astype(float), minlength=nkeys).astype(int),
        "p_mean": pm, "p_lo": lo, "p_hi": hi, "mean_lo": cm["lo"], "mean_hi": cm["hi"], "mean_c": cm["mean"], "k_valid": cm["k"],
        "p_gt": cm["p_gt"], "p_lt": cm["p_lt"], "epos_c": cp["mean"], "epos_lo": cp["lo"], "epos_hi": cp["hi"], "epos_shr": e_pos_shr,
        "rho": rho, "p0": p0, "groups": (u, g, n_g, pos_g, f_g, mean_g, nv_g),
    }
    # medians per key (sorted segments)
    med = np.full(nkeys, np.nan)
    vm = valid == 1
    if vm.any():
        kk, dd = key[vm], d[vm]
        o = np.lexsort((dd, kk))
        kk, dd = kk[o], dd[o]
        starts = np.r_[0, np.nonzero(np.diff(kk))[0] + 1]
        ends = np.r_[starts[1:], len(kk)]
        for s0, e0 in zip(starts, ends):
            med[kk[s0]] = dd[(s0 + e0 - 1) // 2] if (e0 - s0) % 2 else 0.5 * (dd[(s0 + e0) // 2 - 1] + dd[(s0 + e0) // 2])
    out["median"] = med
    return out


def mean_nan(key, x, nkeys):
    ok = ~np.isnan(x)
    s = np.bincount(key[ok], weights=x[ok], minlength=nkeys)
    c = np.bincount(key[ok], minlength=nkeys)
    return np.where(c > 0, s / np.maximum(c, 1), np.nan)


def candidate_verdict(e, i, is_bold_row):
    """Default-relative verdict of one candidate value (row) from its estimates."""
    n, ns = int(e["n"][i]), int(e["n_syms"][i])
    if n == 0:
        return "UNTRIED", "NO_EVALS"
    lo, hi = e["p_lo"][i], e["p_hi"][i]
    mlo, mhi = e["mean_lo"][i], e["mean_hi"][i]
    if lo > T["p_w"] and e["pos_syms"][i] >= 2:
        if np.isnan(mhi) or mhi >= 0:
            return "WORKS", "P_POS_LB_GT_P_W"
        return "CONTESTED", "P_POS_LB_GT_P_W_BUT_MEAN_UB_LT_0(per sym_side only)"
    if n >= T["n_min"] and ns >= T["s_min"]:
        if e["zero"][i] >= T["inert_share"] * n and e["zero_syms"][i] >= T["s_min"]:
            return "INERT", "NO_EFFECT"
        if hi < T["p_d"] and not np.isnan(mhi) and mhi < 0 and e["sum_d"][i] < 0:
            return "DAMAGING", "MEAN_UB_LT_0"
        if not np.isnan(mhi) and mhi < 0 and e["sum_d"][i] < 0 and lo <= T["p_w"]:
            return "DAMAGING", "MEAN_UB_LT_0_WITH_RARE_WINS"
    if e["pos_syms"][i] >= 2 and lo > T["p_c"]:
        return "CONTESTED", "WINS_ON_SOME_SYM_SIDES"
    if e["pos"][i] > 0:
        return "UNTESTED", "THIN_WITH_POSITIVES"
    if n >= T["n_min"] and ns >= T["s_min"]:
        return "LOSES_TO_DEFAULT", "NO_POSITIVE_INTERVAL_OPEN"
    return "UNTESTED", "THIN"


# ------------------------------------------------------------------------------------------------ build per cat_side

def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def live_cooc(cs):
    b = load_json(ROOT / "data" / "brain" / f"v18_brain_{cs}.json")
    out = {}
    if not b:
        return out
    cols = b["_meta"]["cs_cols"]
    for r in b.get("cs", []):
        d = dict(zip(cols, r))
        node = b["nodes"][r[0]]
        if node.startswith("R:") and (d.get("live_n") or 0) > 0:
            out[node[2:]] = {"n": int(d["live_n"]), "pos": int(d.get("live_pos") or 0), "pnl_pp": r4(d.get("live_pnl_pp"), 3), "basis": "co-occurrence (brain live layer)"}
    return out


def paired_evidence(cs):
    """menu_measure (base final set vs + k=v, real paired evals) -> {key: [(sym, d_gain, valid)]};
    veto_measure (base + switch=cand vs + filter at its off value) -> {(row_key, filter): [(sym, d_gain, valid)]}."""
    mm = load_json(LEARN / "menu_measure" / f"menu_measure_{cs}.json", {}) or {}
    keyev = collections.defaultdict(list)
    seen = set()
    for ss, lst in (mm.get("raw") or {}).items():
        for r in lst:
            if "d_gain" not in r:
                continue
            k = f"{r['switch']}={r['cand']}"
            if (ss, k) in seen:
                continue
            seen.add((ss, k))
            keyev[k].append((ss, float(r["d_gain"]), bool(r.get("valid"))))
    vm = load_json(LEARN / "veto_measure" / f"veto_measure_{cs}.json", {}) or {}
    vev = collections.defaultdict(list)
    for ss, lst in (vm.get("raw") or {}).items():
        for r in lst:
            if "d_gain" not in r or "pair" not in r:
                continue
            rk, _, f = r["pair"].partition("|")
            vev[(rk, f)].append((ss, float(r["d_gain"]), bool(r.get("valid")), r.get("off_value")))
    return keyev, vev


def exit_shift(cs):
    files = sorted(glob.glob(str(PREC / "exitshift_attr_*.json")))
    if not files:
        return {}, None
    d = load_json(files[-1], {})
    return (d.get("per_cat_side") or {}).get(cs, {}), files[-1]


def universe(cs, ev_syms):
    u = set(s for s in ev_syms if cat_side_of(s) == cs)
    try:
        with gzip.open(LEARN / "replay_sets.jsonl.gz", "rt") as fh:
            for line in fh:
                ss = json.loads(line).get("sym_side")
                if cat_side_of(ss) == cs:
                    u.add(ss)
    except Exception:
        pass
    excl = set()
    ex = load_json(ROOT / "data" / "excluded_tokenized_stocks.json", {})
    if isinstance(ex, dict):
        for v in ex.values():
            if isinstance(v, list):
                excl.update(str(x) for x in v)
    elif isinstance(ex, list):
        excl.update(str(x) for x in ex)
    return sorted(s for s in u if s.rsplit("_", 1)[0] not in excl and s not in excl)


def build_cs(cs, a):
    from v18_learn_common import template_index
    t0 = time.time()
    tidx = template_index(cs)
    bolds = tidx["defaults"]
    L = load_cat_side(cs, bolds)
    keys, filts, syms = L["keys"], L["filts"], L["syms"]
    R, C = L["R"], L["C"]
    nk = len(keys.names)
    bh_fill = fill_bh(cs, L["board_meta"])
    cuts = regime_cuts(L["board_meta"])
    breg = {bi: regime_of(m.get("bh"), cuts) for bi, m in L["board_meta"].items()}
    R["reg"] = np.array([breg.get(int(b), 5) for b in R["board"]], np.int64)
    C["reg"] = np.array([breg.get(int(b), 5) for b in C["board"]], np.int64) if len(C.get("key", [])) else np.zeros(0, np.int64)
    # ---------------- template rows
    key_tabs = collections.defaultdict(list)
    sw_rows = collections.defaultdict(list)
    yellows_of_tab = {tab: list(blk["yellows"]) for tab, blk in tidx["tabs"].items()}
    for tab, blk in tidx["tabs"].items():
        for _r, sw, cand, _d in blk["rows"]:
            k = f"{sw}={cand}"
            if tab not in key_tabs[k]:
                key_tabs[k].append(tab)
            if cand not in sw_rows[sw]:
                sw_rows[sw].append(cand)
    for k in key_tabs:
        keys(k)
    nk = len(keys.names)
    # ---------------- row estimates (all evals = vs default in force)
    E = key_estimates(R["key"], R["sym"], R["d"], R["valid"], nk, R["secs"])
    p0_rows, rho_rows = E["p0"], E["rho"]
    m_tim, m_dd, m_wr, m_sh, m_tr = (mean_nan(R["key"], R[x], nk) for x in ("tim", "dd", "wr", "sh", "tr"))
    # vs template bold only (cv == bold) - the default-relative core for SETTLED states
    vb = R["cv_bold"] == 1
    EB = key_estimates(R["key"][vb], R["sym"][vb], R["d"][vb], R["valid"][vb], nk, R["secs"][vb], p0=p0_rows, rho=rho_rows) if vb.any() else None
    # per regime
    ER = {}
    for gi in range(6):
        m = R["reg"] == gi
        if m.sum() > 0:
            ER[gi] = key_estimates(R["key"][m], R["sym"][m], R["d"][m], R["valid"][m], nk, R["secs"][m], p0=p0_rows, rho=rho_rows)
    # default provenance: was the bold value ever a positive challenger (promoted from evidence)?
    bold_pos_syms = collections.defaultdict(set)
    for i in np.nonzero((R["is_bold"] == 1) & (R["valid"] == 1) & (R["d"] > EPS))[0]:
        bold_pos_syms[keys.names[R["key"][i]].partition("=")[0]].add(int(R["sym"][i]))
    # ---------------- negative-mass decomposition (law item 3)
    negmask = (R["valid"] == 1) & (R["d"] < -EPS)
    invmask = R["valid"] != 1
    neg_split = collections.Counter()
    neg_n = collections.Counter()
    for i in np.nonzero(negmask)[0]:
        ck = R["ck"][i]
        sw = keys.names[R["key"][i]].partition("=")[0]
        if ck == "P":
            cls = "inherent:promoted_on_board"
        elif ck in ("Z", "NB", "A"):
            cls = "inherent:start_set_override(earlier promotion / per-sym best)"
        elif ck in ("B", "BI") and sw in bold_pos_syms:
            cls = "inherent:template_bold_with_positive_history"
        elif ck in ("B", "BI"):
            cls = "genuine:vs_untested_template_bold"
        else:
            cls = "unresolved_default"
        neg_split[cls] += float(R["d"][i])
        neg_n[cls] += 1
    inv_mass = float(R["d"][invmask].sum()) if invmask.any() else 0.0
    # ---------------- forward persistence
    boards_by_sym = collections.defaultdict(list)
    for bi, m in L["board_meta"].items():
        boards_by_sym[m["s"]].append((m.get("t") or "", bi))
    nxt = {}
    for s, lst in boards_by_sym.items():
        lst.sort()
        for (t_a, b_a), (t_b, b_b) in zip(lst, lst[1:]):
            nxt[b_a] = b_b
    row_by_board_sw = collections.defaultdict(list)
    sw_of_key = np.array([k.partition("=")[0] for k in keys.names], dtype=object)
    for i in range(len(R["key"])):
        row_by_board_sw[(int(R["board"][i]), sw_of_key[R["key"][i]])].append(i)
    fwd = collections.defaultdict(lambda: {"n": 0, "next_board": 0, "survived": 0, "held": 0, "beaten": 0, "remeasure_n": 0, "remeasure_pos": 0, "remeasure_sum": 0.0})
    for bi, sw, cand, _t in L["promos"]:
        k = f"{sw}={cand}"
        f = fwd[k]
        f["n"] += 1
        nb = nxt.get(bi)
        if nb is None:
            continue
        f["next_board"] += 1
        st = L["first_cv"].get((nb, sw))
        if st is not None and st[0] is not None and (st[0] == cand or cand in str(st[0]).split("|")):
            f["survived"] += 1
        idx = row_by_board_sw.get((nb, sw), [])
        alts = [R["d"][i] for i in idx if keys.names[R["key"][i]] != k and R["valid"][i] == 1]
        if alts:
            if max(alts) > EPS:
                f["beaten"] += 1
            else:
                f["held"] += 1
        own = [R["d"][i] for i in idx if keys.names[R["key"][i]] == k]
        for dv in own:
            f["remeasure_n"] += 1
            f["remeasure_pos"] += 1 if dv > EPS else 0
            f["remeasure_sum"] += dv
    # ---------------- pairs
    nf = len(filts.names)
    if len(C.get("key", [])):
        pk_u, pk_g = group((C["key"], C["filt"]))
        npairs = len(pk_u)
        EP = key_estimates(pk_g, C["sym"], C["md"], C["valid"], npairs, C["secs"])
        cellpos = (C["valid"] == 1) & (np.nan_to_num(C["cell"], nan=-1) > EPS)
        cell_pos_n = np.bincount(pk_g, weights=cellpos.astype(float), minlength=npairs)
        cell_best = np.full(npairs, -np.inf)
        np.maximum.at(cell_best, pk_g, np.where(C["valid"] == 1, np.nan_to_num(C["cell"], nan=-np.inf), -np.inf))
        pair_tim = mean_nan(pk_g, C["tim"], npairs)
        pair_dd = mean_nan(pk_g, C["dd"], npairs)
    else:
        pk_u, pk_g, npairs, EP = np.zeros((0, 2), int), np.zeros(0, int), 0, None
    pair_index = {(int(pk_u[j, 0]), int(pk_u[j, 1])): j for j in range(npairs)}
    # template pair universe for FDR
    tmpl_pairs = 0
    row_yellows = {}
    for k, tabs in key_tabs.items():
        ys = sorted({y for t in tabs for y in yellows_of_tab.get(t, [])})
        row_yellows[k] = ys
        tmpl_pairs += len(ys)
    # p-values: NEEDED (strict option, mean marginal > 0), RESCUE (loose option > 0 => chain filter state suffocates), HARMFUL (strict < 0)
    fname = np.array([filts.names[pk_u[j, 1]].partition("=")[0] for j in range(npairs)], dtype=object) if npairs else np.zeros(0, object)
    fopt = np.array([filts.names[pk_u[j, 1]].partition("=")[2] for j in range(npairs)], dtype=object) if npairs else np.zeros(0, object)
    loose = np.array([o in LOOSE for o in fopt], bool) if npairs else np.zeros(0, bool)
    m_total = max(tmpl_pairs, npairs)
    if npairs:
        rej_gt = bh(EP["p_gt"], m_total, T["fdr_q"])
        rej_lt = bh(EP["p_lt"], m_total, T["fdr_q"])
        tested = EP["k_valid"] >= T["s_min"]
        rej_gt_tested = bh(EP["p_gt"][tested], int(tested.sum()), T["fdr_q"]) if tested.any() else np.zeros(0, bool)
        rej_gt_t = np.zeros(npairs, bool)
        rej_gt_t[np.nonzero(tested)[0]] = rej_gt_tested
    else:
        rej_gt = rej_lt = rej_gt_t = np.zeros(0, bool)
    # filter bold (template default of the filter's own switch) -> DEFAULT_COMPANION
    pair_class = np.empty(npairs, dtype=object)
    for j in range(npairs):
        n_j = EP["n"][j]
        lo_m, hi_m = EP["mean_lo"][j], EP["mean_hi"][j]
        comp = bolds.get(fname[j]) == fopt[j]
        if comp and EP["zero"][j] >= T["inert_share"] * n_j:
            pair_class[j] = "DEFAULT_COMPANION"
        elif not np.isnan(lo_m) and lo_m > 0:
            pair_class[j] = ("RESCUE" if loose[j] else "NEEDED") + ("" if rej_gt[j] else "_UNCONFIRMED_FDR")
        elif not np.isnan(hi_m) and hi_m < 0:
            pair_class[j] = ("HARMFUL_STRICT" if not loose[j] else "HARMFUL_LOOSE") + ("" if rej_lt[j] else "_UNCONFIRMED_FDR")
        elif n_j >= T["n_min"] and EP["n_syms"][j] >= T["s_min"] and EP["zero"][j] >= T["inert_share"] * n_j:
            pair_class[j] = "INERT"
        elif n_j == 0:
            pair_class[j] = "UNTRIED"
        else:
            pair_class[j] = "OPEN"
    # pairs per row
    pairs_of_key = collections.defaultdict(list)
    for j in range(npairs):
        pairs_of_key[int(pk_u[j, 0])].append(j)
    # suffocation: per (row, filter name): strict options with UB < 0 while a loose option >= 0, or loose option LB > 0
    abl, rem = L["abl"], L["remedies"]
    keyev, vev = paired_evidence(cs)
    live = live_cooc(cs)
    xs, xs_file = exit_shift(cs)
    old = load_json(LEARN / "switch_profile_{}.json".format(cs) if not a.before else a.before.format(cs=cs), {}) or {}
    old_rows, old_sw = old.get("rows") or {}, old.get("switches") or {}

    def pair_view(j):
        return {"f": filts.names[pk_u[j, 1]], "n": int(EP["n"][j]), "n_syms": int(EP["n_syms"][j]), "pos": int(EP["pos"][j]), "neg": int(EP["neg"][j]),
                "lift": r4(EP["mean_c"][j]), "lift_ci": [r4(EP["mean_lo"][j]), r4(EP["mean_hi"][j])], "p_pos": r4(EP["p_mean"][j]),
                "p_pos_ci": [r4(EP["p_lo"][j]), r4(EP["p_hi"][j])], "sum_marg": r4(EP["sum_d"][j], 3), "class": pair_class[j],
                "fdr": bool(rej_gt[j] or rej_lt[j]), "q_p": r4(min(EP["p_gt"][j], EP["p_lt"][j]), 6), "cell_pos": int(cell_pos_n[j])}

    def regime_block(i):
        out = {}
        for gi, er in ER.items():
            n = int(er["n"][i])
            if n == 0:
                continue
            out[REGIMES[gi]] = {"n": n, "pos": int(er["pos"][i]), "n_syms": int(er["n_syms"][i]), "p_pos": r4(er["p_mean"][i]),
                                "p_pos_ci": [r4(er["p_lo"][i]), r4(er["p_hi"][i])], "mean_d": r4(er["mean_c"][i]), "sum_d": r4(er["sum_d"][i], 3),
                                "thin": n < 30}
        return out

    rows_out, pilot, positives_rows = {}, {}, []
    keyidx = keys.m
    verdict_cnt = collections.Counter()
    for sw in sorted(sw_rows):
        for cand in sw_rows[sw]:
            k = f"{sw}={cand}"
            i = keyidx[k]
            is_def = bolds.get(sw) == cand
            v, sub = candidate_verdict(E, i, is_def)
            # paired menu evidence (real paired evals, appended as its own block; folded into the DAMAGING check)
            pe = keyev.get(k, [])
            pe_sum = sum(x[1] for x in pe)
            pe_pos = sum(1 for x in pe if x[2] and x[1] > EPS)
            if pe and v not in ("WORKS",) and len(pe) >= 3 and pe_pos == 0 and pe_sum < 0 and (E["sum_d"][i] + pe_sum) < 0 and E["p_lo"][i] <= T["p_w"]:
                v, sub = "DAMAGING", "PAIRED_LEDGER_ALL_NEGATIVE"
            if is_def:
                state = "DEFAULT"
            elif v == "DAMAGING" and E["pos_syms"][i] >= 2 and E["p_lo"][i] > T["p_c"]:
                state, sub = "CONTESTED", sub + ";AS_DEFAULT_DAMAGING_BUT_WINS_PER_SYM_SIDE"
            else:
                state = v
            ps = pairs_of_key.get(i, [])
            views = [pair_view(j) for j in ps]
            need = [x for x in views if x["class"].startswith("NEEDED")]
            need.sort(key=lambda x: (-(x["lift_ci"][0] or -9), -x["n_syms"]))
            resc = [x for x in views if x["class"].startswith("RESCUE")]
            harm = [x for x in views if x["class"].startswith("HARMFUL_STRICT")]
            # suffocators: rescue (loose option frees the row) + strict harmful + ablation kills + veto paired
            suff = []
            for x in resc:
                fnm = x["f"].partition("=")[0]
                suff.append({"f": fnm + "=<chain state>", "remedy": "reset " + x["f"], "remedy_delta": x["lift"], "remedy_ci": x["lift_ci"], "n": x["n"],
                             "n_syms": x["n_syms"], "basis": "yellow rescue (loose option marginal LB > 0)", "fdr": x["fdr"]})
            for x in harm:
                fnm = x["f"].partition("=")[0]
                looser = [y for y in views if y["f"].partition("=")[0] == fnm and y["f"].partition("=")[2] in LOOSE]
                best = max(looser, key=lambda y: (y["lift"] if y["lift"] is not None else -9), default=None)
                if best is not None and (best["lift"] or 0) >= 0:
                    suff.append({"f": x["f"], "remedy": "reset " + best["f"], "remedy_delta": best["lift"], "remedy_ci": best["lift_ci"], "n": x["n"],
                                 "n_syms": x["n_syms"], "basis": "strict option UB < 0, loose option >= 0", "fdr": x["fdr"], "strict_lift": x["lift"], "strict_ci": x["lift_ci"]})
            for (kk, ff), (an, akill, add, aeff) in abl.items():
                if kk == k and akill >= 2:
                    exm = rem.get(("ABL_EXEMPT", f"{k}+{ff}")) or rem.get(("ABL_EXEMPT", ff))
                    suff.append({"f": ff, "remedy": "exemption", "remedy_delta": r4(exm[2] / exm[0]) if exm else r4((add - aeff) / max(1, an)),
                                 "remedy_basis": "ABL_EXEMPT measured on the board" if exm else "STRIPPED-CONTEXT dd - eff per attribution (ablation board, not the live chain)",
                                 "n": an, "kills": akill, "ctx": "board" if exm else "stripped",
                                 "basis": "ablation attribution (killer when restored gain < 0.5 x discovered)", "fdr": None})
            for (rk2, ff), lst in vev.items():
                if rk2 == k and lst:
                    s_d = sum(x[1] for x in lst)
                    if s_d > EPS:
                        suff.append({"f": ff, "remedy": f"off ({lst[0][3]})", "remedy_delta": r4(s_d / len(lst)), "n": len(lst), "n_syms": len({x[0] for x in lst}),
                                     "basis": "paired real evals (veto_measure: base + switch=cand vs + filter off)", "fdr": None})
            suff.sort(key=lambda x: (x.get("ctx") == "stripped", -(x.get("remedy_delta") or 0)))
            pos_views = sorted([x for x in views if x["pos"] > 0], key=lambda x: (-(x["lift_ci"][0] if x["lift_ci"][0] is not None else -9), -x["pos"]))
            # pilot list (contract kept): needed -> suffocating -> evidence_pos, deduped, <= needed_max, ranked by interval lower bound
            picked, seen = {"needed": [], "suffocating": [], "evidence_pos": []}, set()
            ylist = set(row_yellows.get(k, []))
            suff_hdrs = [s["remedy"].split(" ", 1)[1] for s in suff if s["remedy"].startswith("reset ")] + [s["f"] for s in suff if "<chain" not in s["f"] and not s["remedy"].startswith("reset ")]
            for name, src in (("needed", [x["f"] for x in need]), ("suffocating", suff_hdrs), ("evidence_pos", [x["f"] for x in pos_views])):
                for fo in src:
                    if fo in seen or len(seen) >= T["needed_max"] or (ylist and fo not in ylist):
                        continue
                    seen.add(fo)
                    picked[name].append(fo)
            n_seen = len({x["f"] for x in views if x["n"] > 0} & ylist) if ylist else len(views)
            ivs = {x["f"]: [x["lift_ci"][0], x["lift_ci"][1], x["n"], x["n_syms"], x["class"]] for x in views if x["f"] in seen}
            pilot[k] = {"needed": picked["needed"], "suffocating": picked["suffocating"], "evidence_pos": picked["evidence_pos"],
                        "untried_count": max(0, len(ylist) - n_seen), "intervals": ivs, "state": state}
            f = fwd.get(k, {"n": 0, "next_board": 0, "survived": 0, "held": 0, "beaten": 0, "remeasure_n": 0, "remeasure_pos": 0, "remeasure_sum": 0.0})
            n = int(E["n"][i])
            o = old_rows.get(k, {})
            rows_out[k] = {
                "switch": sw, "cand": cand, "is_default": is_def, "tabs": key_tabs.get(k, []),
                "verdict": {"WORKS": "WORKS", "CONTESTED": "SOMETIMES", "DAMAGING": "DAMAGING", "INERT": "INERT"}.get(v, "UNTRIED"), "verdict_sub": sub,
                "state": state, "cand_verdict": v,
                "n_evals": n, "n_invalid": int(E["inv"][i]), "n_valid": n - int(E["inv"][i]), "n_syms": int(E["n_syms"][i]), "pos_syms": int(E["pos_syms"][i]),
                "neg_syms": int(E["neg_syms"][i]), "pos_syms_basis": "exact (per sym_side from the delta logs)",
                "n_pos_evals": int(E["pos"][i]), "n_neg_evals": int(E["neg"][i]), "n_zero_evals": int(E["zero"][i]),
                "pos_rate": r4(E["pos"][i] / n) if n else None, "pos_rate_sym": r4(E["pos_syms"][i] / E["n_syms"][i]) if E["n_syms"][i] else None,
                "sum_pos_delta": r4(E["sum_pos"][i], 3), "sum_delta": r4(E["sum_d"][i], 3), "median_delta": r4(E["median"][i]),
                "mean_delta": r4(E["mean_c"][i]), "mean_delta_known": not np.isnan(E["mean_c"][i]),
                "interval": [r4(E["p_lo"][i]), r4(E["p_hi"][i])], "p_pos": r4(E["p_mean"][i]),
                "interval_mean_delta": [r4(E["mean_lo"][i]), r4(E["mean_hi"][i])], "e_delta_pos": r4(E["epos_shr"][i]),
                "interval_e_delta_pos": [r4(E["epos_lo"][i]), r4(E["epos_hi"][i])],
                "mean_d_tim": r4(m_tim[i]), "mean_d_dd": r4(m_dd[i]), "mean_d_wr": r4(m_wr[i]), "mean_d_trades": r4(m_tr[i]), "mean_d_sharpe": r4(m_sh[i]),
                "eval_secs": r4(E["secs"][i], 2),
                "vs_bold": ({"n": int(EB["n"][i]), "pos": int(EB["pos"][i]), "n_syms": int(EB["n_syms"][i]), "sum_d": r4(EB["sum_d"][i], 3),
                             "interval": [r4(EB["p_lo"][i]), r4(EB["p_hi"][i])], "interval_mean_delta": [r4(EB["mean_lo"][i]), r4(EB["mean_hi"][i])]}
                            if EB is not None and EB["n"][i] else None),
                "paired_ledger": {"n": len(pe), "pos": pe_pos, "sum_d_gain": r4(pe_sum, 3)} if pe else None,
                "forward": {"n": f["next_board"], "pos": f["held"], "promoted": f["n"], "survived_start": f["survived"], "held": f["held"], "beaten": f["beaten"],
                            "remeasure_n": f["remeasure_n"], "remeasure_pos": f["remeasure_pos"], "remeasure_sum": r4(f["remeasure_sum"], 3)},
                "live": live.get(k, {"n": 0, "pos": 0, "pnl_pp": 0.0, "basis": "co-occurrence (brain live layer)"}),
                "regime": regime_block(i),
                "regimes": {g: {"wn": d["n"], "wpos": d["pos"], "p_pos_shrunk": d["p_pos"]} for g, d in regime_block(i).items()},
                "needed": [{"filter_opt": x["f"], "n": x["n"], "pos": x["pos"], "pos_syms": None, "n_syms": x["n_syms"], "lift": x["lift"], "interval": x["lift_ci"],
                            "class": x["class"], "fdr": x["fdr"]} for x in need[:8]],
                "suffocating": suff[:8],
                "default_companions": [x["f"] for x in views if x["class"] == "DEFAULT_COMPANION"][:12],
                "_filters_pos": pos_views[:10],
                "exits_cooccurrence": o.get("exits_cooccurrence"),
                "coverage": {"pairs_total": len(ylist), "pairs_n_ge3": sum(1 for x in views if x["n"] >= 3 and x["f"] in ylist),
                             "pairs_n_1_2": sum(1 for x in views if 1 <= x["n"] < 3 and x["f"] in ylist), "pairs_untried_n0": max(0, len(ylist) - n_seen)},
                "pilot_list": {kk: len(vv) for kk, vv in picked.items()},
            }
            verdict_cnt[v] += 0 if is_def else 1
            if E["pos"][i] > 0:
                positives_rows.append(i)
    # ---------------- switch level
    switches = {}
    for sw in sorted(sw_rows):
        cands = sw_rows[sw]
        bold = bolds.get(sw)
        rr = [rows_out[f"{sw}={c}"] for c in cands]
        alts = [r for r in rr if not r["is_default"]]
        n_all = sum(r["n_evals"] for r in rr)
        n_alt_vb = sum((r["vs_bold"] or {}).get("n", 0) for r in alts)
        syms_alt_vb = max([(r["vs_bold"] or {}).get("n_syms", 0) for r in alts] + [0])
        any_w = [r for r in alts if r["cand_verdict"] == "WORKS"]
        contested = [r for r in alts if r["pos_syms"] >= 2 and (r["interval"][0] or 0) > T["p_c"]]
        reg_w = [(r["cand"], g, d) for r in alts for g, d in r["regime"].items() if (d.get("p_pos_ci") or [0])[0] is not None and d["p_pos_ci"][0] > T["p_w"] and d["pos"] >= 2]
        dmg = [r for r in alts if r["cand_verdict"] == "DAMAGING"]
        inert = [r for r in alts if r["cand_verdict"] == "INERT"]
        bold_row = rows_out.get(f"{sw}={bold}")
        bold_beats = bool(bold_row and bold_row["n_evals"] and (bold_row["interval_mean_delta"][0] or -1) > 0)
        bold_promoted = bool(bold_pos_syms.get(sw)) or bool(bold_row and bold_row["forward"]["promoted"])
        alt_n = sum(r["n_evals"] for r in alts)
        alt_syms = max([r["n_syms"] for r in alts] + [0])
        if n_all == 0:
            state, sub = "UNTRIED", "NO_EVALS"
        elif any_w:
            state, sub = "CONTESTED", "ALTERNATIVE_WORKS:" + ",".join(r["cand"] for r in any_w[:3])
        elif contested or reg_w:
            state, sub = "CONTESTED", ("PER_SYM_SIDE:" + ",".join(r["cand"] for r in contested[:3])) if contested else ("PER_REGIME:" + ",".join(f"{c}@{g}" for c, g, _ in reg_w[:3]))
        elif alt_n < T["n_min"] or alt_syms < T["s_min"]:
            state, sub = "UNTESTED_DEFAULT", f"alternatives n={alt_n} on {alt_syms} sym_sides < n_min {T['n_min']} / s_min {T['s_min']}"
        elif inert and len(inert) == len([r for r in alts if r["n_evals"] >= T["n_min"]]):
            state, sub = "INERT", "every tested alternative has no effect"
        elif str(bold) in LOOSE:
            state, sub = "SETTLED_OFF", "default OFF/neutral, every ON alternative <= 0"
        elif bold_promoted or bold_beats:
            state, sub = "SETTLED_WINNING", "default promoted from positive evidence, alternatives <= 0"
        else:
            state, sub = "SETTLED_WINNING", "default never measured as a challenger (inherited) but every alternative <= 0 against it"
        # best cand per regime (LB of P(pos) among cands with n >= 3 in that regime; the bold is the reference)
        bbr = {}
        for g in REGIMES:
            best = None
            for r in alts:
                d = r["regime"].get(g)
                if not d or d["n"] < 3 or d["pos"] == 0:
                    continue
                key_ = (d["p_pos_ci"][0] or 0, d["mean_d"] or -9)
                if best is None or key_ > best[0]:
                    best = (key_, r["cand"], d)
            if best:
                bbr[g] = {"cand": best[1], "p_pos": best[2]["p_pos"], "p_pos_ci": best[2]["p_pos_ci"], "n": best[2]["n"], "pos": best[2]["pos"], "mean_d": best[2]["mean_d"], "thin": best[2]["thin"]}
        agg_need = collections.defaultdict(lambda: [0, -9.0, None])
        agg_suff = collections.defaultdict(lambda: [0, -9.0, None])
        agg_suff_key = lambda kv: (kv[1][2] is not None and kv[1][2].get("ctx") == "stripped", -kv[1][1])
        best_f = {}
        for r in rr:
            for x in r.get("_filters_pos", []):
                cur = best_f.get(x["f"])
                if cur is None or (x["lift_ci"][0] if x["lift_ci"][0] is not None else -9) > (cur["lift_ci"][0] if cur["lift_ci"][0] is not None else -9):
                    best_f[x["f"]] = dict(x, row=r["cand"])
        for r in rr:
            for x in r["needed"]:
                e = agg_need[x["filter_opt"]]
                e[0] += 1
                if e[2] is None or (x["interval"][0] if x["interval"][0] is not None else -9) > e[1]:
                    e[1], e[2] = (x["interval"][0] if x["interval"][0] is not None else -9), x
            for s in r["suffocating"]:
                e = agg_suff[s["f"]]
                e[0] += 1
                if e[2] is None or (s.get("remedy_delta") if s.get("remedy_delta") is not None else -9) > e[1]:
                    e[1], e[2] = (s.get("remedy_delta") if s.get("remedy_delta") is not None else -9), s
        best_row = max(alts, key=lambda r: ((r["interval"][0] or 0), r["sum_pos_delta"] or 0), default=None)
        o = old_sw.get(sw, {})
        switches[sw] = {
            "tabs": sorted({t for r in rr for t in r["tabs"]}), "default": bold, "state": state, "state_sub": sub,
            "verdict": {"CONTESTED": "SOMETIMES", "SETTLED_WINNING": "WORKS", "SETTLED_OFF": "INERT", "INERT": "INERT"}.get(state, "UNTRIED"),
            "default_promoted_from_evidence": bold_promoted, "default_beats_alternatives": bold_beats,
            "row_verdict_counts": dict(collections.Counter(r["cand_verdict"] for r in alts)),
            "n_rows": len(rr), "n_evals": n_all, "n_valid": sum(r["n_valid"] for r in rr), "n_syms": max([r["n_syms"] for r in rr] + [0]),
            "n_syms_max_row": max([r["n_syms"] for r in rr] + [0]), "pos_syms_max_row": max([r["pos_syms"] for r in rr] + [0]),
            "sum_pos_delta": r4(sum(r["sum_pos_delta"] or 0 for r in rr), 3), "sum_delta": r4(sum(r["sum_delta"] or 0 for r in rr), 3),
            "alt_vs_bold": {"n": n_alt_vb, "max_syms": syms_alt_vb},
            "best_cand": best_row["cand"] if best_row and best_row["n_syms"] else None,
            "interval": best_row["interval"] if best_row else None,
            "best_by_regime": bbr, "regime": bbr,
            "filters_evidence": [{"filter_opt": f, "cand": x["row"], "class": x["class"], "lift": x["lift"], "interval": x["lift_ci"], "n": x["n"], "n_syms": x["n_syms"], "pos": x["pos"], "fdr": x["fdr"]}
                                 for f, x in sorted(best_f.items(), key=lambda kv: -(kv[1]["lift_ci"][0] if kv[1]["lift_ci"][0] is not None else -9))[:10]],
            "damaging_cands": [r["cand"] for r in dmg], "inert_cands": [r["cand"] for r in inert],
            "needed_filters": [{"filter_opt": f, "rows": e[0], "lb": r4(e[1]), "lift": e[2]["lift"], "interval": e[2]["interval"], "fdr": e[2]["fdr"]} for f, e in sorted(agg_need.items(), key=lambda x: -x[1][1])[:10]],
            "suffocating_filters": [{"filter_opt": f, "rows": e[0], "remedy": e[2].get("remedy"), "remedy_delta": e[2].get("remedy_delta"), "basis": e[2].get("basis"), "ctx": e[2].get("ctx", "board")} for f, e in sorted(agg_suff.items(), key=agg_suff_key)[:10]],
            "forward": {"n": sum(r["forward"]["n"] for r in rr), "pos": sum(r["forward"]["pos"] for r in rr), "promoted": sum(r["forward"]["promoted"] for r in rr),
                        "survived_start": sum(r["forward"]["survived_start"] for r in rr), "beaten": sum(r["forward"]["beaten"] for r in rr)},
            "live": {"n": sum(r["live"]["n"] for r in rr), "pos": sum(r["live"]["pos"] for r in rr), "pnl_pp": r4(sum(r["live"]["pnl_pp"] or 0 for r in rr), 3), "basis": "co-occurrence"},
            "exit_shift": xs.get(sw), "exits_cooccurrence": o.get("exits_cooccurrence"),
            "coverage": {"pairs_total": sum(r["coverage"]["pairs_total"] for r in rr), "pairs_n_ge3": sum(r["coverage"]["pairs_n_ge3"] for r in rr),
                         "pairs_untried_n0": sum(r["coverage"]["pairs_untried_n0"] for r in rr)},
            "untried_cands": [r["cand"] for r in alts if r["n_evals"] == 0],
            "eval_secs": r4(sum(r["eval_secs"] or 0 for r in rr), 1),
        }
        for r in rr:
            if r["is_default"]:
                r["state"] = state
                r["verdict"] = switches[sw]["verdict"]
            elif state == "SETTLED_OFF" and r["cand_verdict"] in ("LOSES_TO_DEFAULT", "DAMAGING", "INERT"):
                r["state"] = "SETTLED_OFF" if r["cand_verdict"] != "DAMAGING" else "DAMAGING"
    for r in rows_out.values():
        r["filters_evidence"] = [{"filter_opt": x["f"], "class": x["class"], "lift": x["lift"], "interval": x["lift_ci"], "n": x["n"], "n_syms": x["n_syms"], "pos": x["pos"]} for x in r.pop("_filters_pos", [])[:4]]
    # ---------------- positives_all
    uni = universe(cs, syms.names)
    uidx = {s: i for i, s in enumerate(uni)}
    u, g, n_g, pos_g = E["groups"][0], E["groups"][1], E["groups"][2], E["groups"][3]
    tried = collections.defaultdict(set)
    possy = collections.defaultdict(set)
    for j in range(len(u)):
        tried[int(u[j, 0])].add(syms.names[u[j, 1]])
        if pos_g[j] > 0:
            possy[int(u[j, 0])].add(syms.names[u[j, 1]])
    best_d = np.full(nk, -np.inf)
    np.maximum.at(best_d, R["key"], np.where((R["valid"] == 1), R["d"], -np.inf))
    pa_rows = []
    for i in sorted(set(positives_rows) | {int(x) for x in np.nonzero(E["pos"] > 0)[0]}):
        k = keys.names[i]
        tr_ = sorted(tried[i])
        fw = fwd.get(k, {})
        pa_rows.append({"key": k, "n": int(E["n"][i]), "n_pos": int(E["pos"][i]), "pos_syms": sorted(possy[i]), "tried_on": [uidx[s] for s in tr_ if s in uidx],
                        "untried_on": [uidx[s] for s in uni if s not in tried[i]], "best_delta": r4(best_d[i]),
                        "forward": {"promoted": fw.get("n", 0), "held": fw.get("held", 0), "beaten": fw.get("beaten", 0)},
                        "live": {"n": live.get(k, {}).get("n", 0), "pos": live.get(k, {}).get("pos", 0)}})
    pa_pairs = []
    if npairs:
        up, gp, ngp, posgp = EP["groups"][0], EP["groups"][1], EP["groups"][2], EP["groups"][3]
        cellpos_g = np.bincount(gp, weights=((C["valid"] == 1) & (np.nan_to_num(C["cell"], nan=-1) > EPS)).astype(float))
        trp = collections.defaultdict(set)
        psp = collections.defaultdict(set)
        for j in range(len(up)):
            trp[int(up[j, 0])].add(int(up[j, 1]))
            if cellpos_g[j] > 0:
                psp[int(up[j, 0])].add(int(up[j, 1]))
        for j in np.nonzero(cell_pos_n > 0)[0]:
            j = int(j)
            ts_ = {syms.names[x] for x in trp[j]}
            pa_pairs.append({"key": keys.names[pk_u[j, 0]], "filter": filts.names[pk_u[j, 1]], "n": int(EP["n"][j]), "n_pos": int(cell_pos_n[j]),
                             "n_pos_marginal": int(EP["pos"][j]), "pos_syms": sorted(syms.names[x] for x in psp[j]), "tried_on": sorted(uidx[s] for s in ts_ if s in uidx),
                             "untried_on": [uidx[s] for s in uni if s not in ts_], "best_delta": r4(cell_best[j]), "lift": r4(EP["mean_c"][j]),
                             "lift_ci": [r4(EP["mean_lo"][j]), r4(EP["mean_hi"][j])]})
    pos_doc = {"_meta": {"cat_side": cs, "built_at": utc(), "law": LAW_POSITIVES, "universe_rule": "every sym_side of this cat_side with >= 1 board in the corpus or a published replay set, tokenized stocks excluded",
                         "encoding": "tried_on / untried_on are indices into `sym_sides`; positive = valid eval with delta > 1e-9 vs the default in force (rows: naked delta; pairs: cell delta)",
                         "n_rows": len(pa_rows), "n_pairs": len(pa_pairs)}, "sym_sides": uni, "rows": pa_rows, "pairs": pa_pairs}
    # ---------------- doc 14 §2.2 fact: eval time of keys positive on <= 3 % of their sym_sides
    share = np.where(E["n_syms"] > 0, E["pos_syms"] / np.maximum(E["n_syms"], 1), 0)
    low = (E["n_syms"] > 0) & (share <= 0.03)
    secs_total = float(E["secs"].sum())
    low_secs_rows = float(E["secs"][low].sum())
    if npairs:
        psh = np.where(EP["n_syms"] > 0, EP["pos_syms"] / np.maximum(EP["n_syms"], 1), 0)
        lowp = (EP["n_syms"] > 0) & (psh <= 0.03)
        low_secs_pairs, secs_pairs = float(EP["secs"][lowp].sum()), float(EP["secs"].sum())
    else:
        low_secs_pairs = secs_pairs = 0.0
    fact = {"rows": {"keys_le3pct": int(low.sum()), "keys": int((E["n_syms"] > 0).sum()), "secs_share": r4(low_secs_rows / secs_total) if secs_total else None, "secs": r4(secs_total, 1)},
            "pairs": {"pairs_le3pct": int(lowp.sum()) if npairs else 0, "pairs": int((EP["n_syms"] > 0).sum()) if npairs else 0,
                      "secs_share": r4(low_secs_pairs / secs_pairs) if secs_pairs else None, "secs": r4(secs_pairs, 1)},
            "combined_secs_share": r4((low_secs_rows + low_secs_pairs) / (secs_total + secs_pairs)) if (secs_total + secs_pairs) else None}
    # ---------------- precision stats (before / after)
    stats = precision_stats(cs, E, EP, npairs, old_rows, old_sw, rows_out, switches, tmpl_pairs, rej_gt, rej_lt, rej_gt_t, pair_class, loose, keys, filts, pk_u, row_yellows)
    stats.update({"rho_rows": r4(rho_rows), "p0_rows": r4(p0_rows), "rho_pairs": r4(EP["rho"]) if EP else None, "p0_pairs": r4(EP["p0"]) if EP else None,
                  "regime_cuts_bh": cuts, "bh_fill": bh_fill, "regime_counts_boards": dict(collections.Counter(REGIMES[v] for v in breg.values())),
                  "regime_counts_evals": {REGIMES[gi]: int(er["n"].sum()) for gi, er in ER.items()},
                  "negative_mass": {"valid_negative_by_default_provenance": {k: {"n": neg_n[k], "sum_d": r4(v, 2)} for k, v in neg_split.items()},
                                    "invalid_evals": {"n": int(invmask.sum()), "sum_d": r4(inv_mass, 2), "note": "gate-invalid (e.g. < 10 trades): never adoptable, kept apart from both"},
                                    "positive_mass": {"n": int(E["pos"].sum()), "sum_d": r4(float(E["sum_pos"].sum()), 2)}},
                  "eval_time_fact_doc14_2_2": fact, "chain_kind_counts": dict(collections.Counter(R["ck"].tolist())),
                  "loader": dict(L["cnt"]), "n_boards": len(L["board_meta"]), "n_sym_sides": len(syms.names), "universe": len(uni),
                  "exit_shift_file": xs_file, "build_secs": round(time.time() - t0, 1)})
    return {"rows": rows_out, "switches": switches, "pilot": pilot, "positives": pos_doc, "stats": stats, "tidx": tidx, "L": L, "E": E, "EP": EP}


def precision_stats(cs, E, EP, npairs, old_rows, old_sw, rows_out, switches, tmpl_pairs, rej_gt, rej_lt, rej_gt_t, pair_class, loose, keys, filts, pk_u, row_yellows):
    """Before (coarse brain-export counts) vs after (this corpus) evidence density and interval widths."""
    # before: coarse profile pair n per template pair (from its coverage counters) and row-level coarse counts
    old_pairs_n = []
    for k, r in old_rows.items():
        c = r.get("coverage") or {}
        tot = c.get("pairs_total") or 0
        old_pairs_n.append((tot, c.get("pairs_n_ge3") or 0, (c.get("pairs_n_1_2") or 0)))
    tot_b = sum(x[0] for x in old_pairs_n)
    ge3_b = sum(x[1] for x in old_pairs_n)
    # coarse interval widths: Beta(1 + pos, 1 + n - pos) on the coarse counts (no clustering) - what that evidence can support
    wb_rows = []
    for k, r in old_rows.items():
        n, pos = r.get("n_valid") or 0, r.get("n_pos_evals") or 0
        if n > 0:
            lo, hi = beta_ci(1 + pos, 1 + n - pos)
            wb_rows.append(hi - lo)
    wb_sw = []
    for sw, s in old_sw.items():
        n = s.get("n_valid") or 0
        if n > 0:
            pos = sum((old_rows.get(f"{sw}={c}", {}) or {}).get("n_pos_evals") or 0 for c in [kk.partition("=")[2] for kk in old_rows if kk.startswith(sw + "=")])
            lo, hi = beta_ci(1 + pos, 1 + n - pos)
            wb_sw.append(hi - lo)
    wb_pairs = []
    for k, r in old_rows.items():
        for x in r.get("needed") or []:
            n, pos = x.get("n") or 0, x.get("pos") or 0
            if n > 0:
                lo, hi = beta_ci(1 + pos, 1 + n - pos)
                wb_pairs.append(hi - lo)
    # after
    tmpl_keys = set(rows_out)
    rows_n = np.array([r["n_evals"] for r in rows_out.values()])
    wa_rows = np.array([r["interval"][1] - r["interval"][0] for r in rows_out.values() if r["n_evals"] > 0 and r["interval"][0] is not None])
    wa_rows_flat = []
    for r in rows_out.values():
        if r["n_evals"] > 0:
            n_, p_ = r["n_valid"] + r["n_invalid"], r["n_pos_evals"]
            lo, hi = beta_ci(1 + p_, 1 + n_ - p_)
            wa_rows_flat.append(hi - lo)
    wa_sw = np.array([s["interval"][1] - s["interval"][0] for s in switches.values() if s["interval"] and s["interval"][0] is not None and s["n_evals"] > 0])
    if npairs:
        pn = EP["n"]
        in_t = np.array([keys.names[pk_u[j, 0]] in tmpl_keys and filts.names[pk_u[j, 1]] in set(row_yellows.get(keys.names[pk_u[j, 0]], [])) for j in range(npairs)])
        pn_t = pn[in_t]
        wa_pairs = (EP["p_hi"] - EP["p_lo"])[(pn > 0)]
        wa_pairs_mean = (EP["mean_hi"] - EP["mean_lo"])[~np.isnan(EP["mean_hi"])]
        n_ge3 = int((pn_t >= 3).sum())
        n_seen = int((pn_t > 0).sum())
    else:
        pn_t, wa_pairs, wa_pairs_mean, n_ge3, n_seen = np.zeros(0), np.zeros(0), np.zeros(0), 0, 0
    pcls = collections.Counter(pair_class.tolist()) if npairs else {}
    return {
        "before": {"pairs_template": tot_b, "pairs_n_ge3": ge3_b, "share_pairs_n_ge3": r4(ge3_b / tot_b) if tot_b else None,
                   "median_n_per_seen_pair": "not in the coarse profile (only n>=3 / 1-2 / 0 coverage counters)",
                   "median_ci_width_p_pos_rows": r4(float(np.median(wb_rows))) if wb_rows else None,
                   "median_ci_width_p_pos_switch": r4(float(np.median(wb_sw))) if wb_sw else None,
                   "median_ci_width_p_pos_pairs_listed": r4(float(np.median(wb_pairs))) if wb_pairs else None,
                   "ci_method": "Beta(1+pos, 1+n-pos) 90% on the coarse 14-day brain-export counts (no clustering, no shrinkage)",
                   "verdict_counts_switch": collections.Counter(s.get("verdict") for s in old_sw.values())},
        "after": {"pairs_template": tmpl_pairs, "pairs_seen_in_template": n_seen, "pairs_n_ge3": n_ge3, "share_pairs_n_ge3": r4(n_ge3 / tmpl_pairs) if tmpl_pairs else None,
                  "median_n_per_seen_pair": r4(float(np.median(pn_t[pn_t > 0]))) if (pn_t > 0).any() else None,
                  "median_n_per_template_pair": r4(float(np.median(np.r_[pn_t, np.zeros(max(0, tmpl_pairs - len(pn_t)))]))) if tmpl_pairs else None,
                  "median_n_per_row": r4(float(np.median(rows_n))), "median_n_per_tried_row": r4(float(np.median(rows_n[rows_n > 0]))) if (rows_n > 0).any() else None,
                  "median_ci_width_p_pos_rows": r4(float(np.median(wa_rows))) if len(wa_rows) else None,
                  "median_ci_width_p_pos_switch": r4(float(np.median(wa_sw))) if len(wa_sw) else None,
                  "median_ci_width_p_pos_rows_flat_prior_same_counts": r4(float(np.median(wa_rows_flat))) if wa_rows_flat else None,
                  "median_ci_width_p_pos_rows_n_ge_10": r4(float(np.median([r["interval"][1] - r["interval"][0] for r in rows_out.values() if r["n_evals"] >= 10]))) if any(r["n_evals"] >= 10 for r in rows_out.values()) else None,
                  "median_ci_width_p_pos_pairs": r4(float(np.median(wa_pairs))) if len(wa_pairs) else None,
                  "median_ci_width_mean_marginal_pairs_pp": r4(float(np.median(wa_pairs_mean))) if len(wa_pairs_mean) else None,
                  "ci_method": "hierarchical Beta (m0=8 at cat_side, design-effect weights 1/(1+(n_g-1)rho) per sym_side cluster) 90%; means: cluster-robust normal 90%",
                  "pair_classes": dict(pcls),
                  "fdr": {"m_total": max(tmpl_pairs, npairs), "q": T["fdr_q"], "pairs_with_p_gt_lt_1": int(((EP["p_gt"] < 1) if npairs else np.zeros(0, bool)).sum()),
                          "needed_survive": int(sum(1 for j in range(npairs) if rej_gt[j] and not loose[j])),
                          "rescue_survive": int(sum(1 for j in range(npairs) if rej_gt[j] and loose[j])),
                          "harmful_survive": int(rej_lt.sum()) if npairs else 0,
                          "needed_survive_bh_over_tested_only": int(sum(1 for j in range(npairs) if rej_gt_t[j] and not loose[j])),
                          "needed_lb_gt0_before_fdr": int(sum(1 for j in range(npairs) if (not loose[j]) and not np.isnan(EP["mean_lo"][j]) and EP["mean_lo"][j] > 0))}}}


# ------------------------------------------------------------------------------------------------ cards + md

def make_card(sw, s, rows):
    alts = sorted([r for r in rows if not r["is_default"] and r["n_evals"] > 0], key=lambda r: -((r["interval"][0] or 0) * 100 + (r["sum_pos_delta"] or 0) / 1000))
    best = alts[0] if alts else None
    card = {"sw": sw, "default": s["default"], "state": s["state"], "why": s["state_sub"][:120], "n_evals": s["n_evals"], "n_syms": s["n_syms"],
            "best": ({"cand": best["cand"], "v": best["cand_verdict"], "p_pos": best["p_pos"], "ci": best["interval"], "mean_d": best["mean_delta"],
                      "mean_ci": best["interval_mean_delta"], "e_d_pos": best["e_delta_pos"], "n": best["n_evals"], "syms": best["n_syms"], "pos_syms": best["pos_syms"]} if best else None),
            "cands": {r["cand"]: [r["cand_verdict"][:4], r["n_evals"], r["pos_syms"], r["interval"][0], r["interval"][1]] for r in alts[:6]},
            "regime": {g: [d["cand"], d["p_pos_ci"][0], d["p_pos_ci"][1], d["n"], d["thin"]] for g, d in s["best_by_regime"].items()},
            "needed": [[x["filter_opt"], x["lift"], x["interval"][0] if x["interval"] else None, x["interval"][1] if x["interval"] else None, x["fdr"]] for x in s["needed_filters"][:5]],
            "filters": [[x["filter_opt"], x["class"][:12], x["lift"], x["interval"][0], x["interval"][1], x["n_syms"]] for x in s.get("filters_evidence", [])[:5]],
            "suffocators": [[x["filter_opt"], x["remedy"], x["remedy_delta"], x.get("ctx", "board")] for x in s["suffocating_filters"][:3]],
            "exit_shift": None, "fwd": [s["forward"]["promoted"], s["forward"]["survived_start"], s["forward"]["beaten"]], "live": [s["live"]["n"], s["live"]["pos"], s["live"]["pnl_pp"]],
            "untried_cands": len(s["untried_cands"]), "untried_pair_share": r4(s["coverage"]["pairs_untried_n0"] / s["coverage"]["pairs_total"], 3) if s["coverage"]["pairs_total"] else None,
            "damaging": s["damaging_cands"][:6], "secs": s["eval_secs"]}
    xs = s.get("exit_shift")
    if xs:
        t = xs.get("family_changes", [])[:2]
        card["exit_shift"] = {"moved": xs["moved_share"], "filtered": xs.get("filtered"), "added": xs.get("added"), "top": [[x["from"], x["to"], x["n"], x["d_pnl_pp"]] for x in t],
                              "d_loss": xs["sum_d_loss_pp"], "d_gain": xs["sum_d_gain_pp"], "syms": xs["n_syms"], "basis": "causal"}
    else:
        co = s.get("exits_cooccurrence") or {}
        fams = co.get("families") or []
        if fams:
            card["exit_shift"] = {"basis": "co-occurrence only (board ledgers whose set carries the switch; NOT causal)", "top_loss": [[f["exit_family"], f["trades"], f["loss_sum_pp"]] for f in fams[:2]]}
    txt = json.dumps(card, separators=(",", ":"))
    while len(txt) > T["card_bytes"]:
        for fld, keep in (("cands", 3), ("regime", 3), ("needed", 3), ("damaging", 2), ("suffocators", 2), ("why", 40)):
            v = card.get(fld)
            if isinstance(v, dict) and len(v) > keep:
                card[fld] = dict(list(v.items())[:keep])
            elif isinstance(v, list) and len(v) > keep:
                card[fld] = v[:keep]
            elif isinstance(v, str) and len(v) > keep:
                card[fld] = v[:keep]
            txt = json.dumps(card, separators=(",", ":"))
            if len(txt) <= T["card_bytes"]:
                break
        else:
            break
    return card


def fnum(x, nd=2):
    if x is None:
        return "-"
    if isinstance(x, float):
        if not math.isfinite(x):
            return "-"
        return f"{x:.{nd}f}"
    return str(x)


def ci(v, nd=3):
    if not v or v[0] is None:
        return "[-]"
    return f"[{fnum(v[0], nd)}, {fnum(v[1], nd)}]"


def render_cards_md(cs, res):
    S, Rw, st = res["switches"], res["rows"], res["stats"]
    L = [f"# Switch cards {cs} (built {utc()})", "", "> " + LAW_DEFAULTS, "", "> " + LAW_POSITIVES, "",
         f"Thresholds: CI {int(T['ci'] * 100)}%; WORKS P(pos) LB > {T['p_w']}; CONTESTED LB > {T['p_c']} on >= 2 pos sym_sides (or a regime LB > {T['p_w']}); "
         f"DAMAGING mean-delta UB < 0 and Σdelta < 0, n >= {T['n_min']}, sym_sides >= {T['s_min']}; INERT >= {int(T['inert_share'] * 100)}% |d| < {T['eps']}; "
         f"FDR BH q = {T['fdr_q']} over {st['after']['fdr']['m_total']:,} template pairs. Regimes = board-bh quintiles {', '.join(fnum(c, 2) for c in (st['regime_cuts_bh'] or []))}.", ""]
    cnt = collections.Counter(s["state"] for s in S.values())
    L.append("States: " + ", ".join(f"{k} {v}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])))
    nm = st["negative_mass"]
    inh = sum(v["sum_d"] for k, v in nm["valid_negative_by_default_provenance"].items() if k.startswith("inherent"))
    gen = sum(v["sum_d"] for k, v in nm["valid_negative_by_default_provenance"].items() if k.startswith("genuine"))
    L.append(f"Negative mass (valid evals): inherent (vs a promoted default) {fnum(inh, 1)} pp, vs an untested template bold {fnum(gen, 1)} pp; gate-invalid evals {nm['invalid_evals']['n']:,} ({fnum(nm['invalid_evals']['sum_d'], 1)} pp, kept apart); positive mass +{fnum(nm['positive_mass']['sum_d'], 1)} pp.")
    f = st["eval_time_fact_doc14_2_2"]
    L.append(f"Eval time on keys positive on <= 3% of their sym_sides: rows {fnum((f['rows']['secs_share'] or 0) * 100, 1)}%, pairs {fnum((f['pairs']['secs_share'] or 0) * 100, 1)}%, combined {fnum((f['combined_secs_share'] or 0) * 100, 1)}% of eval-seconds.")
    L += ["", "## 1. Switch ON / change the setting (alternative beats the default in force; with its filters)", "",
          "| switch=cand (default) | verdict | P(pos) [CI] | pos/n syms | n | mean d [CI] | E[d|pos] | best regime | needed filters (lift [CI]) |", "|---|---|---|---|---|---|---|---|---|"]
    win = [r for r in Rw.values() if not r["is_default"] and r["cand_verdict"] in ("WORKS", "CONTESTED")]
    win.sort(key=lambda r: (-(r["interval"][0] or 0) * ((r["e_delta_pos"] or 0) if (r["e_delta_pos"] or 0) > 0 else 0.01), -r["pos_syms"]))
    for r in win[:60]:
        s = S[r["switch"]]
        reg = max(r["regime"].items(), key=lambda kv: (kv[1]["p_pos_ci"][0] or 0), default=None)
        regs = f"{reg[0]} {fnum(reg[1]['p_pos'], 2)} n={reg[1]['n']}" if reg else "-"
        fe = r["needed"][:2] or [x for x in r.get("filters_evidence", []) if (x.get("lift") or 0) > 0][:2]
        nd = "; ".join(f"{x['filter_opt']} {fnum(x['lift'], 2)} {ci(x['interval'], 2)} {str(x.get('class', ''))[:7]}{'*' if x.get('fdr') else ''}" for x in fe) or "-"
        L.append(f"| {r['switch']}={r['cand']} ({s['default']}) | {r['cand_verdict']} | {fnum(r['p_pos'], 3)} {ci(r['interval'])} | {r['pos_syms']}/{r['n_syms']} | {r['n_evals']} | "
                 f"{fnum(r['mean_delta'], 2)} {ci(r['interval_mean_delta'], 2)} | {fnum(r['e_delta_pos'], 2)} | {regs} | {nd} |")
    L += ["", "(filters column: NEEDED* = marginal lift LB > 0 and survives Benjamini-Hochberg; otherwise the best-evidenced filter with its class: NEEDED_U = LB > 0 but not FDR-confirmed, OPEN = interval spans 0)", "", "## 2. Keep / switch OFF: default beats its alternatives (SETTLED_WINNING / SETTLED_OFF), top by alternative evals", "",
          "| switch | default | state | alternatives n / max syms | Σdelta of alternatives | promoted from evidence | fwd promoted/survived/beaten |", "|---|---|---|---|---|---|---|"]
    sett = [(sw, s) for sw, s in S.items() if s["state"] in ("SETTLED_WINNING", "SETTLED_OFF")]
    sett.sort(key=lambda x: -x[1]["n_evals"])
    for sw, s in sett[:40]:
        alt_sum = sum(Rw[f"{sw}={c}"]["sum_delta"] or 0 for c in [kk.partition("=")[2] for kk in Rw if kk.startswith(sw + "=")] if c != s["default"])
        L.append(f"| {sw} | {s['default']} | {s['state']} | {s['n_evals']} / {s['n_syms']} | {fnum(alt_sum, 1)} | {s['default_promoted_from_evidence']} | {s['forward']['promoted']}/{s['forward']['survived_start']}/{s['forward']['beaten']} |")
    L += ["", "## 3. Stop testing except at the floor: DAMAGING candidate values (vs the default in force) and INERT switches, by eval-seconds spent", "",
          "| switch=cand | verdict | n / syms | P(pos) [CI] | mean d [CI] | Σd | eval-secs |", "|---|---|---|---|---|---|---|"]
    stop = [r for r in Rw.values() if not r["is_default"] and r["cand_verdict"] in ("DAMAGING", "INERT")]
    stop.sort(key=lambda r: -(r["eval_secs"] or 0))
    for r in stop[:45]:
        L.append(f"| {r['switch']}={r['cand']} | {r['cand_verdict']} | {r['n_evals']} / {r['n_syms']} | {fnum(r['p_pos'], 3)} {ci(r['interval'])} | {fnum(r['mean_delta'], 2)} {ci(r['interval_mean_delta'], 2)} | {fnum(r['sum_delta'], 1)} | {fnum(r['eval_secs'], 0)} |")
    L += ["", "## 4a. Suffocated switches, ON-BOARD evidence (yellow rescue / strict option harms while the loose option does not / paired veto evals)", "",
          "| switch | suffocator | remedy | remedy delta pp | basis |", "|---|---|---|---|---|"]
    suf = [(sw, x) for sw, s in S.items() for x in s["suffocating_filters"] if x.get("ctx", "board") == "board" and (x.get("remedy_delta") or 0) > 0]
    best = {}
    for sw, x in suf:
        if sw not in best or (x["remedy_delta"] or 0) > (best[sw]["remedy_delta"] or 0):
            best[sw] = x
    for sw, x in sorted(best.items(), key=lambda t: -(t[1]["remedy_delta"] or 0))[:20]:
        L.append(f"| {sw} | {x['filter_opt']} | {x['remedy']} | {fnum(x['remedy_delta'], 2)} | {(x['basis'] or '')[:70]} |")
    L += ["", "## 4b. Suffocators from ablation records (STRIPPED context: the discovered gain with all filters stripped minus the gain with this filter restored; a ranking of killers, not a board gain)", "",
          "| switch | killer filter | kills | stripped-context dd - eff (pp per attribution) |", "|---|---|---|---|"]
    ab = []
    for sw, s in S.items():
        for x in s["suffocating_filters"]:
            if x.get("ctx") == "stripped":
                ab.append((x["remedy_delta"] or 0, sw, x))
                break
    for v, sw, x in sorted(ab, key=lambda t: -t[0])[:15]:
        L.append(f"| {sw} | {x['filter_opt']} | {x.get('rows')} rows | {fnum(v, 2)} |")
    L += ["", "## 5. Causal exit shifts (same set ± one key, same NPZ, trades matched by entry bar)", "",
          "Direction = the published set's value -> the most-evaluated other template value (None = the set does not carry the key). exits moved = base trades whose exit bar, family or pnl changed; FILTERED = base entries gone; ADDED = new entries. Δloss > 0 = fewer losses.", "",
          "| switch | syms | exits moved | filtered / added | top family changes (n, Δpnl pp) | Σ Δloss pp | Σ Δgain pp (+/- syms) |", "|---|---|---|---|---|---|---|"]
    xs = [(sw, s["exit_shift"]) for sw, s in S.items() if s.get("exit_shift")]
    xs.sort(key=lambda t: t[1]["sum_d_gain_pp"])
    for sw, x in xs[:15]:
        fc = "; ".join(f"{t['from']}->{t['to']} {t['n']} ({fnum(t['d_pnl_pp'], 1)})" for t in x.get("family_changes", [])[:2]) or "-"
        L.append(f"| {sw} | {x['n_syms']} | {fnum((x['moved_share'] or 0) * 100, 1)}% | {x.get('filtered')} / {x.get('added')} | {fc} | {fnum(x['sum_d_loss_pp'], 1)} | {fnum(x['sum_d_gain_pp'], 1)} ({x['n_gain_pos']}/{x['n_gain_neg']}) |")
    L += ["", "## 6a. Measured but thin, with positives: worth more trials (these keys are in positives_all and must reach every untried sym_side)", "",
          "| switch=cand (default) | pos evals / n | pos syms / tried syms | P(pos) [CI] | best delta |", "|---|---|---|---|---|"]
    thin = [r for r in Rw.values() if not r["is_default"] and r["cand_verdict"] == "UNTESTED" and r["n_pos_evals"] > 0]
    thin.sort(key=lambda r: (-(r["p_pos"] or 0), -r["n_pos_evals"]))
    for r in thin[:20]:
        L.append(f"| {r['switch']}={r['cand']} ({S[r['switch']]['default']}) | {r['n_pos_evals']} / {r['n_evals']} | {r['pos_syms']} / {r['n_syms']} | {fnum(r['p_pos'], 3)} {ci(r['interval'])} | {fnum(r['sum_pos_delta'], 2)} |")
    L += ["", "## 6b. Untried values, sibling evidence only (NOT measured)", "",
          "| switch | untried cands | sibling with best P(pos) LB | its CI |", "|---|---|---|---|"]
    ut = []
    for sw, s in S.items():
        if not s["untried_cands"]:
            continue
        sib = [Rw[f"{sw}={c}"] for c in [kk.partition("=")[2] for kk in Rw if kk.startswith(sw + "=")] if Rw[f"{sw}={c}"]["n_evals"] > 0 and not Rw[f"{sw}={c}"]["is_default"]]
        b = max(sib, key=lambda r: r["interval"][0] or 0, default=None)
        if b and (b["interval"][0] or 0) > T["p_c"]:
            ut.append((b["interval"][0], sw, s["untried_cands"], b))
    ut.sort(reverse=True)
    for _lb, sw, uc, b in ut[:20]:
        L.append(f"| {sw} | {', '.join(uc[:4])} | {b['cand']} {fnum(b['p_pos'], 3)} | {ci(b['interval'])} |")
    n_un = sum(1 for s in S.values() if s["state"] == "UNTRIED")
    L += ["", f"UNTRIED switches (no eval at all): {n_un}; UNTESTED_DEFAULT: {cnt.get('UNTESTED_DEFAULT', 0)}. Full per-switch numbers: switch_cards_{cs}.json; positives to propagate: positives_all_{cs}.json."]
    return "\n".join(L[: T["md_lines"]]) + "\n"


def write(p, txt):
    p = Path(p)
    tmp = p.with_name(f".{p.name}.tmp.{os.getpid()}")
    tmp.write_text(txt)
    os.replace(tmp, p)


def render_profile_md(cs, res):
    S = res["switches"]
    L = [f"# Switch profile {cs} (precision rebuild {utc()})", "", "> " + LAW_DEFAULTS, "",
         "Built by tools/v18_precision_build.py from the complete corpus; the concise action list is switch_cards_{}.md.".format(cs), "",
         "| switch | default | state | n_evals | n_syms | best cand | P(pos) CI | needed (top 2) | suffocator (top 1) |", "|---|---|---|---|---|---|---|---|---|"]
    for sw in sorted(S, key=lambda x: (-S[x]["n_evals"], x)):
        s = S[sw]
        nd = ", ".join(x["filter_opt"] for x in s["needed_filters"][:2]) or "-"
        sf = s["suffocating_filters"][0]["filter_opt"] if s["suffocating_filters"] else "-"
        L.append(f"| {sw} | {s['default']} | {s['state']} | {s['n_evals']} | {s['n_syms']} | {s['best_cand'] or '-'} | {ci(s['interval'])} | {nd} | {sf} |")
    return "\n".join(L) + "\n"


def build(a):
    PREC.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M", time.gmtime())
    for cs in a.cat_side or CAT_SIDES:
        res = build_cs(cs, a)
        S, Rw = res["switches"], res["rows"]
        cards = {sw: make_card(sw, S[sw], [Rw[f"{sw}={c}"] for c in [kk.partition("=")[2] for kk in Rw if kk.startswith(sw + "=")]]) for sw in S}
        cdoc = {"_meta": {"schema": CARD_SCHEMA, "cat_side": cs, "built_at": utc(), "law_defaults": LAW_DEFAULTS, "law_positives": LAW_POSITIVES, "thresholds": T,
                          "template_md5": res["tidx"]["md5"], "fields": {"cands": "[verdict4, n, pos_syms, P(pos) lb, ub]", "regime": "[best cand, lb, ub, n, thin(n<30)]",
                                                                         "needed": "[filter=opt, lift pp, lb, ub, survives FDR]", "suffocators": "[filter, remedy, remedy delta pp]",
                                                                         "fwd": "[promoted, survived next board start, beaten next board]", "live": "[n, pos, pnl_pp] co-occurrence"}},
                "cards": cards}
        ctxt = json.dumps(cdoc, separators=(",", ":"))
        write(LEARN / f"switch_cards_{cs}.json", ctxt)
        write(LEARN / f"switch_cards_{cs}.md", render_cards_md(cs, res))
        ptxt = json.dumps(res["positives"], separators=(",", ":"))
        if len(ptxt) > 10 * 1024 * 1024:
            with gzip.open(LEARN / f"positives_all_{cs}.json.gz.part", "wt", compresslevel=6) as fh:
                fh.write(ptxt)
            os.replace(LEARN / f"positives_all_{cs}.json.gz.part", LEARN / f"positives_all_{cs}.json.gz")
            pa_path = LEARN / f"positives_all_{cs}.json.gz"
        else:
            write(LEARN / f"positives_all_{cs}.json", ptxt)
            pa_path = LEARN / f"positives_all_{cs}.json"
        st = res["stats"]
        st["cards_bytes"] = len(ctxt)
        st["max_card_bytes"] = max(len(json.dumps(c, separators=(",", ":"))) for c in cards.values())
        st["positives_all"] = {"path": str(pa_path), "rows": res["positives"]["_meta"]["n_rows"], "pairs": res["positives"]["_meta"]["n_pairs"], "json_bytes": len(ptxt)}
        st["state_counts"] = dict(collections.Counter(s["state"] for s in S.values()))
        st["cand_verdict_counts"] = dict(collections.Counter(r["cand_verdict"] for r in Rw.values() if not r["is_default"]))
        st["verdict_changes"] = verdict_changes(cs, S, Rw, a)
        prof = {"_meta": {"schema": SCHEMA, "cat_side": cs, "built_at": utc(), "law_defaults": LAW_DEFAULTS, "template": res["tidx"]["path"], "template_md5": res["tidx"]["md5"],
                          "thresholds": dict(T, min_sym=T["s_min"]), "needed_max": T["needed_max"], "builder": "tools/v18_precision_build.py",
                          "verdict_counts_switch": dict(collections.Counter(s["verdict"] for s in S.values())), "state_counts": st["state_counts"],
                          "fields_added": ["state", "state_sub", "cand_verdict", "interval", "interval_mean_delta", "interval_e_delta_pos", "n_syms", "neg_syms", "regime", "vs_bold",
                                           "paired_ledger", "default_companions", "exit_shift", "median_delta", "sum_delta"]},
                "switches": S, "rows": Rw}
        ptxt2 = json.dumps(prof, separators=(",", ":"))
        trimmed = 0
        while len(ptxt2) > 10 * 1000 * 1000 and trimmed < 3:
            for r in Rw.values():
                r["suffocating"] = r["suffocating"][: 4 >> trimmed]
                r["needed"] = r["needed"][: 4 >> trimmed]
                r.pop("regimes", None)
                r["exits_cooccurrence"] = None if trimmed else r["exits_cooccurrence"]
            trimmed += 1
            ptxt2 = json.dumps(prof, separators=(",", ":"))
        st["profile_bytes"] = len(ptxt2)
        st["profile_trim_passes"] = trimmed
        (PREC / f"stats_{cs}.json").write_text(json.dumps(st, indent=1, default=lambda o: int(o) if isinstance(o, np.integer) else (float(o) if isinstance(o, np.floating) else str(o))))
        if a.replace:
            import shutil
            bk = ROOT / "backups"
            for nm in (f"switch_profile_{cs}.json", f"switch_profile_{cs}.md", f"needed_filters_{cs}.json"):
                src = LEARN / nm
                if src.exists():
                    shutil.copy2(src, bk / f"before_precision_cards_{nm.replace('.', '_')}_{stamp}.{nm.rsplit('.', 1)[1]}")
            write(LEARN / f"switch_profile_{cs}.json", ptxt2)
            write(LEARN / f"switch_profile_{cs}.md", render_profile_md(cs, res))
            write(LEARN / f"needed_filters_{cs}.json", json.dumps(res["pilot"], separators=(",", ":")))
        print(f"{cs}: states {st['state_counts']} cards {len(ctxt):,} B profile {len(ptxt2):,} B positives rows {st['positives_all']['rows']} pairs {st['positives_all']['pairs']} {st['build_secs']}s", flush=True)
    return 0


def verdict_changes(cs, S, Rw, a):
    old = load_json(a.before.format(cs=cs) if a.before else LEARN / f"switch_profile_{cs}.json", {}) or {}
    osw = old.get("switches") or {}
    if (old.get("_meta") or {}).get("schema") == SCHEMA:
        return {"note": "before-file is already a precision build; pass --before backups/<coarse>.json"}
    trans = collections.Counter()
    rev = []
    for sw, s in S.items():
        o = osw.get(sw)
        if not o:
            continue
        ov = o.get("verdict")
        trans[(ov, s["state"])] += 1
        reversal = (ov == "DAMAGING" and s["state"] in ("SETTLED_WINNING", "CONTESTED", "SETTLED_OFF", "INERT", "UNTESTED_DEFAULT")) or \
                   (ov in ("WORKS", "SOMETIMES") and s["state"] in ("SETTLED_WINNING", "SETTLED_OFF", "INERT", "UNTESTED_DEFAULT")) or \
                   (ov in ("INERT", "UNTRIED") and s["state"] == "CONTESTED")
        if reversal:
            bc = Rw.get(f"{sw}={s['best_cand']}") if s["best_cand"] else None
            rev.append({"switch": sw, "before": ov, "after": s["state"], "after_sub": s["state_sub"], "n_evals_before": o.get("n_evals"), "n_evals_after": s["n_evals"],
                        "n_syms_after": s["n_syms"], "default": s["default"], "default_promoted": s["default_promoted_from_evidence"],
                        "best_cand": s["best_cand"], "best_p_pos_ci": bc["interval"] if bc else None, "best_mean_ci": bc["interval_mean_delta"] if bc else None,
                        "best_pos_syms": bc["pos_syms"] if bc else None, "alt_sum_delta": r4(sum(Rw[k]["sum_delta"] or 0 for k in Rw if k.startswith(sw + "=") and not Rw[k]["is_default"]), 2)})
    rev.sort(key=lambda x: -(x["n_evals_after"] or 0))
    return {"transitions": {f"{a_}->{b_}": v for (a_, b_), v in sorted(trans.items(), key=lambda kv: -kv[1])}, "n_changed": sum(v for (a_, b_), v in trans.items() if a_ != b_),
            "reversals_top": rev[:25], "n_reversals": len(rev)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("split")
    b = sub.add_parser("build")
    b.add_argument("--cat-side", action="append", choices=CAT_SIDES)
    b.add_argument("--replace", action="store_true", help="back up and replace switch_profile_<CS>.{json,md} + needed_filters_<CS>.json")
    b.add_argument("--before", help="coarse profile path pattern with {cs} (default: the current data/learn/switch_profile_<CS>.json)")
    a = ap.parse_args(argv)
    return {"split": split, "build": build}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
