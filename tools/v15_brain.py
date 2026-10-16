#!/usr/bin/env python3
"""V15 BRAIN — one learned knowledge graph + one eval allocator for the v15 pilot (USER 2026-10-14).

Replaces the six competing sampling rules (POSSYM, coarse cap, prior verdicts, ZERO-FORMULA, AUTOPSY-INERT rotation,
V17 p) with ONE evidence model and ONE per-1000 budget dial. Stdlib only (runs on S1/S2 system python3).

Graph
  nodes  = SWITCH=cand (row, "naked") and FILTER=opt (yellow cell)
  edges  = (scope, a, b): scope is GLOBAL / cat_side / sym_side, b is "" for a unary node or the row key for a
           filter-on-row connection (the yellow's marginal effect ON THAT ROW — the profile pools it across rows).
  each edge carries real engine outcomes only: n, pos, neg, sum delta, sum positive delta, d_tim, d_trades,
  d_dd, d_sharpe (when the log has them), eval-seconds, last seen.
Model
  P(positive) = hierarchical Beta posterior, shrunk sym_side -> cat_side -> GLOBAL (strength V15_BRAIN_M, default 8).
  2026-10-14 replay: pos-rate predicts the next board (Spearman +0.55..+0.73); mean_delta is ANTI-predictive
  (-0.50..-0.75) — so ranking never uses the signed mean, only P(pos) x E[delta | pos].
  value/sec = P(pos) * E[delta | pos] / cost_s.
Allocator (per-1000 dial)
  p_eval = clip(P(pos) / P_ref, floor/1000, 1); P_ref = the P at quantile V15_BRAIN_QREF of evidenced keys.
  Top keys are always evaluated, the weakest are evaluated `floor` times per 1000 (never zero -> the graph keeps
  learning, never a hollow cell). Draws are deterministic (sha1 sym_side|tab|key|round) and p is returned so every
  skip/eval is logged with its propensity (inverse-propensity weighting keeps the learned rates unbiased).

CLI
  build    mine delta logs -> data/brain/brain_edges.json (+ .md summary)
  replay   chronological holdout: current rule vs brain at several floors -> evals saved / positives kept
  suggest  top keys for a sym_side / cat_side and a metric (gain, tim, trades, dd, sharpe)
  export   prequential-train over every log -> data/brain/brain_<CAT_SIDE>.json (weights + evidence tables)
  compare  champion vs challenger model files on last-3-days prequential capture (verdict only, never replaces)
Nothing here writes sheets, progress JSONs, templates or configs. NO-LIES: every count is a real logged eval.

Forward learning (USER 2026-10-14, tasks 04.2/04.3): the pilot logs FORWARD delta-log records
{key, d_gain, d_trades, d_dd, d_tim, d_sharpe, unseen_bars, prev_npz_end} plus HOLDOUT lines and
BOARD_CTX lines {tier, bh, baseline_gain, ...}. FORWARD/HOLDOUT lines are consumed here as learning
labels P(key helps forward | context) alongside the fit-delta labels, with forward labels weighted
higher (WEIGHT_FORWARD default 1.0, fit WEIGHT_FIT default 0.25). Absent keys are skipped, never crash.
Multi-metric heads (04.4): dd/tim/trades/sharpe logistic heads next to the gain heads; suggest answers
from forward/live evidence and says 'no evidence' explicitly when there is none. Regime context (04.5):
BOARD_CTX tier/bh/holdout become key x regime interaction features, APPENDED only (x[12..16]) so old
12-long weight files keep loading (zero-padded). Champion/challenger (04.6): compare_models() returns a
verdict dict; promotion happens only on strict improvement and is never wired here.
"""
import argparse
import collections
import glob
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

EPS = 1e-9
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
DENY = ("MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED")
SKIP_LABEL_PREFIX = ("FINAL_", "DIAG_", "REPAIR365", "SACRED", "COARSE", "JOINT:", "STANDALONE:", "REF_BASELINE", "RECALC", "ENDGAME", "FOCUS")
PROFILE_SKIP = ("DEAD", "USELESS", "HARMFUL")
M_SHRINK = float(os.environ.get("V15_BRAIN_M", "8"))
Q_REF = float(os.environ.get("V15_BRAIN_QREF", "0.80"))
GLOBAL = "GLOBAL"
METRICS = ("d", "dtim", "dtr", "ddd", "dsh")
WEIGHT_FORWARD = float(os.environ.get("V15_BRAIN_WEIGHT_FORWARD", "1.0"))
WEIGHT_FIT = float(os.environ.get("V15_BRAIN_WEIGHT_FIT", "0.25"))
METRIC_HEADS = {"dd": ("ddd", -1.0), "tim": ("dtim", 1.0), "trades": ("dtr", 1.0), "sharpe": ("dsh", 1.0)}
TIER_CODE = {"W": 1.0, "WINNER": 1.0, "M": 0.0, "MEDIOCRE": 0.0, "L": -1.0, "LOSER": -1.0, "LOST_CAUSE": -1.0}
BH_SCALE = 25.0
HO_ALPHA = 0.1


def tier_code(tier):
    try:
        return TIER_CODE.get(str(tier).strip().upper(), 0.0)
    except Exception:
        return 0.0


def bh_code(bh):
    try:
        return max(-1.0, min(1.0, float(bh) / BH_SCALE))
    except Exception:
        return 0.0


def metric_label(metric, rec):
    """0/1 improvement label for a metric head from a fit or forward rec, None when the metric is absent.
    dd improves downward, tim/trades/sharpe improve upward."""
    spec = METRIC_HEADS.get(metric)
    if spec is None or rec is None:
        return None
    key, sign = spec
    try:
        v = rec.get(key)
    except Exception:
        return None
    if v is None:
        return None
    try:
        v = float(v)
    except Exception:
        return None
    return 1.0 if sign * v > EPS else 0.0


def cat_side_of(sym_side):
    sym, _, side = str(sym_side).rpartition("_")
    cat = "CRYPTO" if sym.upper().endswith(CRYPTO_SUFFIX) else "STOCKS"
    return f"{cat}_{side}"


def sym_side_of_path(path):
    name = os.path.basename(path).replace(".jsonl", "")
    sym_side = name.split("_jump")[0].split("_sequential")[0]
    if not sym_side.endswith(("_LONG", "_SHORT")):
        sym_side = "_".join(name.split("_")[:2])
    return sym_side


def _f(x):
    try:
        return None if x is None else float(x)
    except Exception:
        return None


class Edge:
    """Real-outcome accumulator of one graph edge. pos/neg counted on valid evals only (bible §19)."""
    __slots__ = ("n", "pos", "neg", "inv", "sum", "sum_pos", "sums", "cnts", "secs", "syms", "last")

    def __init__(self):
        self.n = self.pos = self.neg = self.inv = 0
        self.sum = self.sum_pos = self.secs = 0.0
        self.sums = [0.0] * len(METRICS)
        self.cnts = [0] * len(METRICS)
        self.syms = set()
        self.last = ""

    def add(self, sym_side, rec, ts):
        d, valid, secs = rec["d"], rec["valid"], rec["secs"]
        self.n += 1
        self.syms.add(sym_side)
        if ts > self.last:
            self.last = ts
        if secs is not None:
            self.secs += secs
        if not valid:
            self.inv += 1
            return
        if d is None:
            return
        self.sum += d
        if d > EPS:
            self.pos += 1
            self.sum_pos += d
        elif d < -EPS:
            self.neg += 1
        for i, m in enumerate(METRICS):
            v = rec.get(m)
            if v is not None:
                self.sums[i] += v
                self.cnts[i] += 1

    def to_json(self):
        out = {"n": self.n, "n_sym": len(self.syms), "pos": self.pos, "neg": self.neg, "inv": self.inv, "sum_pos": round(self.sum_pos, 4), "mean_secs": round(self.secs / self.n, 4) if self.n else None, "last": self.last}
        for i, m in enumerate(METRICS):
            out[f"mean_{m}"] = round(self.sums[i] / self.cnts[i], 4) if self.cnts[i] else None
        return out


def iter_rows(path):
    """Yield (sym_side, ts, sheet, row_key, naked_rec|None, {filter: marginal_rec}) per finished main-pass row.
    marginal = yellow outcome minus the naked outcome of the same row and the same cum_before (real engine differences)."""
    sym_side = sym_side_of_path(path)
    base = {"tim": None, "trades": None, "dd": None, "sh": None}
    buf = {}
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            label = r.get("label")
            key = (r.get("sheet"), r.get("row"))
            if label == "ROW_DONE":
                b = buf.pop(key, None)
                sw = r.get("switch")
                if not b or sw is None or sw in DENY:
                    continue
                naked = b.get("naked")
                marg = {}
                for hdr, y in b["y"].items():
                    if naked is not None and naked["d"] is not None and y["d"] is not None:
                        m = {"d": y["d"] - naked["d"], "valid": y["valid"], "secs": y["secs"]}
                        for k in METRICS[1:]:
                            m[k] = (y[k] - naked[k]) if (y.get(k) is not None and naked.get(k) is not None) else None
                        marg[hdr] = m
                yield sym_side, str(r.get("ts") or ""), r.get("sheet"), f"{sw}={r.get('cand')}", naked, marg
                continue
            if label is None or label in ("hustle", "BOARD_CTX", "FORWARD", "HOLDOUT") or str(label).startswith(SKIP_LABEL_PREFIX) or r.get("cached"):
                continue
            if str(label).startswith("RED_RETRY:"):
                label = label[len("RED_RETRY:"):]
            delta, valid = _f(r.get("delta")), bool(r.get("valid"))
            tim, trades, dd, sh = _f(r.get("tim")), _f(r.get("trades")), _f(r.get("max_dd_pct")), _f(r.get("pool_sharpe"))
            if valid and delta is not None and abs(delta) <= EPS and tim is not None:
                base = {"tim": tim, "trades": trades, "dd": dd, "sh": sh}
            def _diff(v, k):
                return (v - base[k]) if (valid and v is not None and base[k] is not None) else None
            rec = {"d": delta, "valid": valid, "secs": _f(r.get("secs")), "dtim": _diff(tim, "tim"), "dtr": _diff(trades, "trades"), "ddd": _diff(dd, "dd"), "dsh": _diff(sh, "sh"), "ts": str(r.get("ts") or "")}
            b = buf.setdefault(key, {"y": {}})
            if label == "naked":
                b["naked"] = rec
            else:
                b["y"][label] = rec


def forward_node(key):
    """Normalize a FORWARD record key to a graph node. R:/F:/N: prefixes pass through; SWITCH=cand is a naked
    row node; a bare switch name is sibling (name-level) evidence. None/empty -> None (skip, never crash)."""
    if key is None:
        return None
    try:
        k = str(key).strip()
    except Exception:
        return None
    if not k:
        return None
    if k.startswith(("R:", "F:", "N:")):
        return k
    if "=" in k:
        return "R:" + k
    return "N:" + k


def iter_forward(path):
    """Yield (sym_side, ts, node, rec, ctx) per FORWARD delta-log record. rec uses the same internal keys as
    iter_rows (d/dtim/dtr/ddd/dsh/valid/secs); ctx carries unseen_bars/prev_npz_end. Missing key -> skip."""
    default_sym = sym_side_of_path(path)
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                if r.get("label") != "FORWARD":
                    continue
                node = forward_node(r.get("key"))
                if node is None:
                    continue
                sym_side = r.get("sym_side") or default_sym
                ts = str(r.get("ts") or "")
                rec = {"d": _f(r.get("d_gain")), "valid": True, "secs": _f(r.get("secs")), "dtim": _f(r.get("d_tim")), "dtr": _f(r.get("d_trades")), "ddd": _f(r.get("d_dd")), "dsh": _f(r.get("d_sharpe")), "ts": ts}
                ctx = {"unseen_bars": r.get("unseen_bars"), "prev_npz_end": r.get("prev_npz_end")}
                yield sym_side, ts, node, rec, ctx
            except Exception:
                continue


def iter_ctx(path):
    """Yield (sym_side, ts, ctx) per BOARD_CTX line. Missing tier/bh/baseline -> None (never crash)."""
    default_sym = sym_side_of_path(path)
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                if r.get("label") != "BOARD_CTX":
                    continue
                sym_side = r.get("sym_side") or default_sym
                yield sym_side, str(r.get("ts") or ""), {"tier": r.get("tier"), "bh": _f(r.get("bh")), "baseline_gain": _f(r.get("baseline_gain")), "baseline_trades": _f(r.get("baseline_trades"))}
            except Exception:
                continue


def iter_holdout(path):
    """Yield (sym_side, ts, won, info) per HOLDOUT line. won = final set beat start OOS (final_minus_start > 0).
    Lines without a computable verdict are still yielded with won=None (board context, never a label)."""
    default_sym = sym_side_of_path(path)
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                if r.get("label") != "HOLDOUT":
                    continue
                sym_side = r.get("sym_side") or default_sym
                fms = _f(r.get("final_minus_start"))
                if fms is None:
                    try:
                        fg = (r.get("final") or {}).get("gain_pct")
                        sg = (r.get("start") or {}).get("gain_pct")
                        fms = float(fg) - float(sg) if fg is not None and sg is not None else None
                    except Exception:
                        fms = None
                won = (fms > EPS) if fms is not None else None
                yield sym_side, str(r.get("ts") or ""), won, {"final_minus_start": fms, "days": _f(r.get("days"))}
            except Exception:
                continue


class Fwd:
    """Weighted forward-label accumulator for one (scope, node). Raw n/pos stay integer counts (NO-LIES);
    wn/wpos carry the WEIGHT_FORWARD-scaled mass the model learns from."""

    __slots__ = ("n", "pos", "wn", "wpos", "last")

    def __init__(self):
        self.n = self.pos = 0
        self.wn = self.wpos = 0.0
        self.last = ""

    def add(self, y, w, ts):
        self.n += 1
        self.wn += w
        if y:
            self.pos += 1
            self.wpos += w
        if ts > self.last:
            self.last = ts

    def to_json(self):
        return {"n": self.n, "pos": self.pos, "wn": round(self.wn, 4), "wpos": round(self.wpos, 4), "last": self.last}


class Brain:
    """The graph. edges[(scope, a, b)] -> Edge. a = node key ("R:SWITCH=cand" or "F:FILTER=opt"), b = "" or the row node."""

    def __init__(self, pair_min_scope=("cat",)):
        self.edges = collections.defaultdict(Edge)
        self.fwd = collections.defaultdict(Fwd)
        self.ho_ema = collections.defaultdict(lambda: 0.5)
        self.ho_seen = set()
        self.pair_scopes = pair_min_scope

    def observe_forward(self, sym_side, ts, node, y, w=WEIGHT_FORWARD):
        """One forward label P(node helps forward): y truthy = helped. Stored GLOBAL/cat_side/sym_side."""
        cs = cat_side_of(sym_side)
        for scope in (GLOBAL, cs, sym_side):
            self.fwd[(scope, node)].add(bool(y), w, ts)

    def observe_holdout(self, sym_side, ts, won, alpha=HO_ALPHA):
        """Board-level OOS verdict: the final set beat start on unseen days. won=None = no verdict, ignored."""
        if won is None:
            return
        self.ho_seen.add(sym_side)
        e = self.ho_ema[sym_side]
        self.ho_ema[sym_side] = min(0.98, max(0.02, (1 - alpha) * e + alpha * (1.0 if won else 0.0)))

    def holdout_rate(self, sym_side):
        return self.ho_ema[sym_side] if sym_side in self.ho_seen else None

    def p_forward(self, sym_side, node):
        """Hierarchical Beta posterior P(node helps forward | context) over weighted forward labels."""
        cs = cat_side_of(sym_side)
        p, wn = 0.5, 0.0
        first = True
        for scope in (GLOBAL, cs, sym_side):
            e = self.fwd.get((scope, node))
            if e is None:
                continue
            m = 1.0 if first else M_SHRINK
            p = (e.wpos + m * p) / (e.wn + m)
            wn += e.wn
            first = False
        return p, wn

    def forward_json(self, min_n=1):
        out = collections.defaultdict(dict)
        for (scope, node), e in self.fwd.items():
            if e.n >= min_n:
                out[scope][node] = e.to_json()
        return out

    def observe_row(self, sym_side, ts, row_key, naked, marg):
        cs = cat_side_of(sym_side)
        rn = "R:" + row_key
        if naked is not None:
            for scope in (GLOBAL, cs, sym_side):
                self.edges[(scope, rn, "")].add(sym_side, naked, naked.get("ts") or ts)
        for hdr, m in marg.items():
            fn = "F:" + str(hdr).strip()
            for scope in (GLOBAL, cs, sym_side):
                self.edges[(scope, fn, "")].add(sym_side, m, ts)
            self.edges[(cs, fn, rn)].add(sym_side, m, ts)

    def edge(self, scope, a, b=""):
        return self.edges.get((scope, a, b))

    def p_pos(self, sym_side, a, b=""):
        """Hierarchical Beta posterior P(delta > 0): GLOBAL prior (Beta(0.5,0.5)-seeded) -> cat_side -> (pair) -> sym_side."""
        cs = cat_side_of(sym_side)
        chain = [(GLOBAL, a, ""), (cs, a, "")]
        if b:
            chain.append((cs, a, b))
        chain.append((sym_side, a, ""))
        p, n_ev = 0.5, 0
        first = True
        for k in chain:
            e = self.edges.get(k)
            if e is None:
                continue
            valid_n = max(0, e.n - e.inv)
            m = 1.0 if first else M_SHRINK
            p = (e.pos + m * p) / (valid_n + m)
            n_ev += valid_n
            first = False
        return p, n_ev

    def e_gain_pos(self, sym_side, a, b=""):
        cs = cat_side_of(sym_side)
        for k in ((cs, a, b), (cs, a, ""), (GLOBAL, a, "")):
            e = self.edges.get(k)
            if e is not None and e.pos:
                return e.sum_pos / e.pos
        return 0.5

    def cost(self, sym_side, a):
        cs = cat_side_of(sym_side)
        for k in ((cs, a, ""), (GLOBAL, a, "")):
            e = self.edges.get(k)
            if e is not None and e.n and e.secs:
                return max(0.01, e.secs / e.n)
        return 0.5

    def score(self, sym_side, a, b=""):
        p, n_ev = self.p_pos(sym_side, a, b)
        g = self.e_gain_pos(sym_side, a, b)
        c = self.cost(sym_side, a)
        return {"p": p, "n": n_ev, "e_gain": g, "cost": c, "value_s": p * g / c}

    def p_ref(self, kind="R", scope=None, q=Q_REF):
        ps = sorted(self._unary_p(k[0], k[1]) for k in self.edges if k[2] == "" and k[1].startswith(kind + ":") and (scope is None or k[0] == scope))
        if not ps:
            return 0.05
        return max(1e-4, ps[min(len(ps) - 1, int(q * len(ps)))])

    def _unary_p(self, scope, a):
        e = self.edges[(scope, a, "")]
        return (e.pos + 0.5) / (e.n - e.inv + 1.0)

    def to_json(self, min_n=1):
        out = collections.defaultdict(dict)
        for (scope, a, b), e in self.edges.items():
            if e.n < min_n or (scope not in (GLOBAL,) and not scope.startswith(("CRYPTO_", "STOCKS_")) and e.pos == 0):
                continue
            out[scope][a + ("|" + b if b else "")] = e.to_json()
        return out


def draw(sym_side, tab, key, round_id):
    return int(hashlib.sha1(f"{sym_side}|{tab}|{key}|{round_id}".encode()).hexdigest()[:8], 16) / float(1 << 32)


def p_eval(p_pos, p_ref, floor_per_1000, n_ev, min_n=3):
    """Pure. Unevidenced (n_ev < min_n) -> 1.0 (fail-open, always computed). Else clip(p_pos / p_ref, floor/1000, 1)."""
    if n_ev < min_n:
        return 1.0
    return max(floor_per_1000 / 1000.0, min(1.0, p_pos / max(1e-9, p_ref)))


def decide(brain, sym_side, tab, row_key, filter_key, round_id, floor_per_1000, p_ref):
    """Pure allocator decision for one candidate eval -> (compute: bool, p: float, why: str). filter_key None = the naked row."""
    rn = "R:" + row_key
    if filter_key is None:
        p, n_ev = brain.p_pos(sym_side, rn)
        pe = p_eval(p, p_ref, floor_per_1000, n_ev)
        u = draw(sym_side, tab, row_key, round_id)
    else:
        fn = "F:" + str(filter_key).strip()
        p, n_ev = brain.p_pos(sym_side, fn, rn)
        pe = p_eval(p, p_ref, floor_per_1000, n_ev)
        u = draw(sym_side, tab, row_key + "|" + str(filter_key), round_id)
    return u < pe, pe, f"BRAIN p_pos={p:.4f} n={n_ev} p_eval={pe:.4f}"


def collect_files(globs):
    files = set()
    for g in globs:
        files.update(glob.glob(os.path.expanduser(g)))
    return sorted(files, key=os.path.getmtime)


DEFAULT_GLOBS = ["~/v15_run*/progress/v15_delta_log/*.jsonl", "~/v15_run*/v15_delta_log/*.jsonl"]


def stream_rows(files, lo="", hi="~"):
    """Stream rows with lo <= ts < hi, file by file (no row list in RAM — servers are RAM-bound)."""
    for f in files:
        try:
            for item in iter_rows(f):
                if lo <= item[1] < hi:
                    yield item
        except Exception as e:
            print(f"[warn] {f}: {e}", file=sys.stderr)


def cmd_build(args):
    files = collect_files(args.glob or DEFAULT_GLOBS)
    brain = Brain()
    n = nf = nh = 0
    for f in files:
        for sym_side, ts, _sheet, row_key, naked, marg in iter_rows(f):
            brain.observe_row(sym_side, ts, row_key, naked, marg)
            n += 1
        for sym_side, ts, node, rec, _ctx in iter_forward(f):
            if rec.get("d") is None:
                continue
            brain.observe_forward(sym_side, ts, node, rec["d"] > EPS)
            nf += 1
        for sym_side, ts, won, _info in iter_holdout(f):
            brain.observe_holdout(sym_side, ts, won)
            nh += 1
    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "brain")
    os.makedirs(out_dir, exist_ok=True)
    blob = {"_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": len(files), "rows": n, "forward_labels": nf, "holdout_lines": nh, "edges": len(brain.edges), "m_shrink": M_SHRINK, "w_forward": WEIGHT_FORWARD, "w_fit": WEIGHT_FIT, "source": "v15 delta logs (real engine evals, cache hits excluded)"}, "edges": brain.to_json(), "forward": brain.forward_json()}
    p = os.path.join(out_dir, "brain_edges.json")
    with open(p + ".tmp", "w") as fh:
        json.dump(blob, fh)
    os.replace(p + ".tmp", p)
    print(json.dumps(blob["_meta"]))
    return 0


def _slim(rec):
    return None if rec is None else (rec["d"], rec["dtim"], rec["dtr"], rec["valid"], rec["secs"])


def _profile_from_rows(rows):
    """Re-derive the CURRENT production rule (causal-profile verdicts, n_sym>=10) on the train rows only (no leakage)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import v15_switch_causal_profile as P
    sw = collections.defaultdict(lambda: collections.defaultdict(P.Agg))
    fl = collections.defaultdict(lambda: collections.defaultdict(P.Agg))
    for sym_side, _ts, _sheet, row_key, naked, marg in rows:
        cs = cat_side_of(sym_side)
        if naked is not None:
            sw[cs][row_key].add(sym_side, *naked, False)
        for hdr, m in marg.items():
            fl[cs][str(hdr).strip()].add(sym_side, *m, False)
    out = {}
    for cs in set(sw) | set(fl):
        out[cs] = {"R": {k: a.summary() for k, a in sw[cs].items()}, "F": {k: a.summary() for k, a in fl[cs].items()}}
    return out


def _cur_skip(prof, cs, kind, key, min_n=10):
    e = ((prof.get(cs) or {}).get(kind) or {}).get(key)
    return bool(e) and e.get("verdict") in PROFILE_SKIP and int(e.get("n_sym") or 0) >= min_n


def cmd_replay(args):
    """Honest offline replay on real logged evals. Train = rows before --cut, test = rows at/after.
    For every test eval: was it positive, how many seconds did it cost, would policy X have computed it.
    Policies: ALL (today's eval set as logged), CURRENT (profile verdict skip, naked always), CURRENT+NAKED (verdict skip also on
    naked), BRAIN@floor for each --floors value. Reports share of eval-seconds kept, positives kept, positive-delta kept.
    Caveat (printed): the test set only contains evals the production policy chose to run."""
    files = collect_files(args.glob or DEFAULT_GLOBS)
    brain = Brain()
    prof_rows = []
    n_train = 0
    for item in stream_rows(files, args.since or "", args.cut):
        sym_side, ts, _sheet, row_key, naked, marg = item
        brain.observe_row(sym_side, ts, row_key, naked, marg)
        prof_rows.append((sym_side, ts, _sheet, row_key, _slim(naked), {h: _slim(m) for h, m in marg.items()}))
        n_train += 1
    prof = _profile_from_rows(prof_rows)
    del prof_rows
    n_test = 0
    pref = {"R": brain.p_ref("R", GLOBAL, args.qref), "F": brain.p_ref("F", GLOBAL, args.qref)}
    floors = [float(x) for x in args.floors.split(",")]
    pols = ["ALL", "CURRENT", "CURRENT+NAKED"] + [f"BRAIN@{f:g}" for f in floors]
    acc = {k: {p: {"secs": 0.0, "n": 0, "pos": 0, "pos_d": 0.0, "evals": 0.0} for p in pols} for k in ("R", "F")}
    tot = {k: {"secs": 0.0, "n": 0, "pos": 0, "pos_d": 0.0} for k in ("R", "F")}
    rid = "replay"
    for sym_side, ts, sheet, row_key, naked, marg in stream_rows(files, args.cut, "~"):
        n_test += 1
        cs = cat_side_of(sym_side)
        items = []
        if naked is not None:
            items.append(("R", None, naked))
        items.extend(("F", h, m) for h, m in marg.items())
        for kind, hdr, rec in items:
            secs = rec["secs"] or 0.0
            d = rec["d"] if rec["valid"] else None
            is_pos = d is not None and d > EPS
            t = tot[kind]
            t["secs"] += secs
            t["n"] += 1
            t["pos"] += int(is_pos)
            t["pos_d"] += d if is_pos else 0.0
            key = row_key if kind == "R" else str(hdr).strip()
            cur = not _cur_skip(prof, cs, kind, key) if kind == "F" else True
            cur_nk = not _cur_skip(prof, cs, kind, key)
            dec = {"ALL": 1.0, "CURRENT": 1.0 if cur else 0.0, "CURRENT+NAKED": 1.0 if cur_nk else 0.0}
            for fl in floors:
                _c, pe, _w = decide(brain, sym_side, sheet, row_key, hdr, rid, fl, pref[kind])
                dec[f"BRAIN@{fl:g}"] = pe
            for pol, w in dec.items():
                a = acc[kind][pol]
                a["secs"] += w * secs
                a["evals"] += w
                a["pos"] += w * int(is_pos)
                a["pos_d"] += w * (d if is_pos else 0.0)
    rep = {"_meta": {"cut": args.cut, "since": args.since, "train_rows": n_train, "test_rows": n_test, "p_ref": pref, "qref": args.qref, "m_shrink": M_SHRINK, "note": "expected values: a policy with p_eval keeps p x (secs, positives) of every test eval (unbiased for a deterministic hash draw). Test set = evals production actually ran."}, "kinds": {}}
    for kind in ("R", "F"):
        t = tot[kind]
        rep["kinds"][kind] = {"total": {k: round(v, 2) if isinstance(v, float) else v for k, v in t.items()}, "policies": {}}
        for pol in pols:
            a = acc[kind][pol]
            rep["kinds"][kind]["policies"][pol] = {"secs_kept": round(a["secs"] / t["secs"], 4) if t["secs"] else None, "evals_kept": round(a["evals"] / t["n"], 4) if t["n"] else None, "pos_kept": round(a["pos"] / t["pos"], 4) if t["pos"] else None, "pos_delta_kept": round(a["pos_d"] / t["pos_d"], 4) if t["pos_d"] else None}
    print(json.dumps(rep, indent=1))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(rep, fh, indent=1)
    return 0


def _lo(pos, n, a=0.5):
    """smoothed log-odds of a hit rate"""
    return math.log((pos + a) / (max(0.0, n - pos) + a))


class Online:
    """One learned layer over graph evidence (USER 2026-10-14 "like a neural network that keeps making connections"):
    logistic regression, SGD, predict-THEN-learn on every real eval (prequential = honest out-of-sample at every step).
    Features per candidate (kind R = naked row, F = yellow cell):
      x[0..11] (frozen since 2026-10-14): bias, key log-odds @GLOBAL / @cat_side / @sym_side (shrunk by n),
      switch-name log-odds @cat_side (sibling cands), filter-on-row pair log-odds (F), log1p(n) evidence mass,
      board running hit-rate logit (EMA), tab-on-board running logit (EMA), naked-result features for F
      (sign + clipped delta: the row's own naked eval, known before deferred yellows).
      x[12..16] (ADDITIVE ONLY, 2026-10-15 regime context from BOARD_CTX): tier x key-evidence, bh x key-evidence,
      tier main, bh main, board holdout-OOS logit. Old 12-long weight files load zero-padded.
    Heads: gain heads w[R/F/F0] plus one logistic head per metric (mh[metric][kind]) predicting P(metric improves).
    learn() takes a sample weight w (fit-delta labels learn with WEIGHT_FIT, forward labels with WEIGHT_FORWARD)."""
    NF = 17
    NF0 = 12

    def __init__(self, lr=0.03, l2=1e-5):
        self.w = {"R": [0.0] * self.NF, "F": [0.0] * self.NF, "F0": [0.0] * self.NF}
        self.w["R"][0] = self.w["F"][0] = self.w["F0"][0] = -4.0
        self.mh = {m: {k: [0.0] * self.NF for k in ("R", "F", "F0")} for m in METRIC_HEADS}
        for m in METRIC_HEADS:
            for k in ("R", "F", "F0"):
                self.mh[m][k][0] = -4.0
        self.lr, self.l2 = lr, l2
        self.ema = collections.defaultdict(lambda: 0.02)

    @staticmethod
    def _sig(z):
        return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))

    @staticmethod
    def static_x(x):
        """Naked-blind variant (no row-result features): x[10..11] zeroed, regime tail kept."""
        return x[:10] + [0.0, 0.0] + x[12:]

    def feats(self, brain, kind, sym_side, sheet, row_key, hdr, naked_d, regime=None):
        cs = cat_side_of(sym_side)
        a = ("R:" + row_key) if kind == "R" else ("F:" + str(hdr).strip())
        x = [1.0] + [0.0] * (self.NF - 1)
        def ev(scope, k, b=""):
            e = brain.edges.get((scope, k, b))
            return (e.pos, max(0, e.n - e.inv)) if e is not None else (0, 0)
        gp, gn = ev(GLOBAL, a)
        cp, cn = ev(cs, a)
        sp, sn = ev(sym_side, a)
        x[1] = _lo(gp, gn) * gn / (gn + 3.0)
        x[2] = _lo(cp, cn) * cn / (cn + 3.0)
        x[3] = (_lo(sp, sn) - x[2]) * sn / (sn + 2.0)
        name = "N:" + row_key.split("=", 1)[0] if kind == "R" else "N:" + str(hdr).split("=", 1)[0].strip()
        np_, nn = ev(cs, name)
        x[4] = _lo(np_, nn) * nn / (nn + 3.0)
        if kind == "F":
            pp, pn = ev(cs, a, "R:" + row_key)
            x[5] = _lo(pp, pn) * pn / (pn + 2.0)
        x[6] = math.log1p(cn)
        x[7] = 1.0 if cn < 3 else 0.0
        be = self.ema[sym_side]
        x[8] = math.log(be / (1.0 - be))
        te = self.ema[(sym_side, sheet)]
        x[9] = math.log(te / (1.0 - te))
        if kind == "F" and naked_d is not None:
            x[10] = 1.0 if naked_d > EPS else (-1.0 if naked_d < -EPS else 0.0)
            x[11] = max(-3.0, min(3.0, naked_d))
        tc = tier_code((regime or {}).get("tier")) if regime else 0.0
        bc = bh_code((regime or {}).get("bh")) if regime else 0.0
        x[12] = tc * x[2]
        x[13] = bc * x[2]
        x[14] = tc
        x[15] = bc
        try:
            ho = brain.ho_ema.get(sym_side, 0.5) if brain is not None else 0.5
        except Exception:
            ho = 0.5
        ho = min(0.98, max(0.02, ho))
        x[16] = math.log(ho / (1.0 - ho))
        return x

    def predict(self, kind, x):
        return self._sig(sum(w * v for w, v in zip(self.w[kind], x)))

    def learn(self, kind, x, y, p, w=1.0):
        g = (y - p) * w
        ws = self.w[kind]
        for i, v in enumerate(x):
            ws[i] += self.lr * (g * v - self.l2 * ws[i])

    def predict_metric(self, metric, kind, x):
        return self._sig(sum(w * v for w, v in zip(self.mh[metric][kind], x)))

    def learn_metric(self, metric, kind, x, y, p, w=1.0):
        g = (y - p) * w
        ws = self.mh[metric][kind]
        for i, v in enumerate(x):
            ws[i] += self.lr * (g * v - self.l2 * ws[i])

    def bump(self, sym_side, sheet, y, alpha=0.02):
        for k in (sym_side, (sym_side, sheet)):
            self.ema[k] = min(0.98, max(0.002, (1 - alpha) * self.ema[k] + alpha * y))


def observe_names(brain, sym_side, ts, row_key, naked, marg):
    cs = cat_side_of(sym_side)
    if naked is not None:
        brain.edges[(cs, "N:" + row_key.split("=", 1)[0], "")].add(sym_side, naked, ts)
    for hdr, m in marg.items():
        brain.edges[(cs, "N:" + str(hdr).split("=", 1)[0].strip(), "")].add(sym_side, m, ts)


def stream_events(files, since=""):
    """Chronological merge of fit rows, FORWARD labels, BOARD_CTX regimes and HOLDOUT verdicts.
    Yields ("row", ts, item) / ("fwd", ts, item) / ("ctx", ts, item) / ("ho", ts, item). Never raises on bad lines."""
    events = []
    for f in files:
        try:
            for it in iter_rows(f):
                if it[1] >= since:
                    events.append(("row", it[1], it))
        except Exception as e:
            print(f"[warn] {f}: {e}", file=sys.stderr)
        try:
            for it in iter_forward(f):
                if it[1] >= since:
                    events.append(("fwd", it[1], it))
        except Exception as e:
            print(f"[warn] {f} forward: {e}", file=sys.stderr)
        try:
            for it in iter_ctx(f):
                if it[1] >= since:
                    events.append(("ctx", it[1], it))
        except Exception as e:
            print(f"[warn] {f} ctx: {e}", file=sys.stderr)
        try:
            for it in iter_holdout(f):
                if it[1] >= since:
                    events.append(("ho", it[1], it))
        except Exception as e:
            print(f"[warn] {f} holdout: {e}", file=sys.stderr)
    events.sort(key=lambda e: e[1])
    return events


def learn_fit_eval(model, brain, kind, sym_side, sheet, row_key, hdr, r, nd, regime, w=WEIGHT_FIT):
    """SGD step on one fit-delta eval: gain head + every metric head whose metric is present."""
    x = model.feats(brain, kind, sym_side, sheet, row_key, hdr, nd, regime)
    d = r["d"] if r["valid"] else None
    y = 1.0 if (d is not None and d > EPS) else 0.0
    model.learn(kind, x, y, model.predict(kind, x), w)
    x0 = Online.static_x(x) if kind == "F" else None
    if kind == "F":
        model.learn("F0", x0, y, model.predict("F0", x0), w)
    for m in METRIC_HEADS:
        yl = metric_label(m, r)
        if yl is not None:
            model.learn_metric(m, kind, x, yl, model.predict_metric(m, kind, x), w)
            if kind == "F":
                model.learn_metric(m, "F0", x0, yl, model.predict_metric(m, "F0", x0), w)
    return x


def learn_forward_label(model, brain, sym_side, node, rec, regime, w=WEIGHT_FORWARD):
    """SGD step on one FORWARD label: gain head (needs d_gain) + every metric head whose metric is present.
    Returns False when the record carries no usable label."""
    d = rec.get("d")
    if d is None:
        return False
    y = 1.0 if d > EPS else 0.0
    if node.startswith("F:"):
        kind, hdr, row_key = "F", node[2:], ""
    elif node.startswith("N:"):
        kind, hdr, row_key = "R", None, node[2:]
    else:
        kind, hdr, row_key = "R", None, node[2:] if node.startswith("R:") else node
    x = model.feats(brain, kind, sym_side, "", row_key, hdr, None, regime)
    model.learn(kind, x, y, model.predict(kind, x), w)
    if kind == "F":
        x0 = Online.static_x(x)
        model.learn("F0", x0, y, model.predict("F0", x0), w)
    for m in METRIC_HEADS:
        yl = metric_label(m, rec)
        if yl is not None:
            model.learn_metric(m, kind, x, yl, model.predict_metric(m, kind, x), w)
            if kind == "F":
                model.learn_metric(m, "F0", x0, yl, model.predict_metric(m, "F0", x0), w)
    brain.observe_forward(sym_side, rec.get("ts") or "", node, y, w)
    return True


def cmd_prequential(args):
    """Predict-then-learn over the whole chronological stream. Every logged real eval is scored BEFORE the graph and the
    weights learn from it. Report for evals at/after --cut (warm-up before): the cost/recall frontier of
    p_eval = clip(pred / p_ref, floor/1000, 1) for many p_ref, vs today's verdict rule; AUC-style capture curve."""
    files = collect_files(args.glob or DEFAULT_GLOBS)
    events = stream_events(files, args.since or "")
    brain, model = Brain(), Online()
    regimes = {}
    prof_sw = collections.defaultdict(lambda: collections.defaultdict(lambda: [set(), set(), set()]))
    rec = {"R": [], "F": [], "F0R": []}
    n_row = n_fwd = 0
    for etyp, _ets, item in events:
        if etyp == "ctx":
            sym_side, _ts, ctx = item
            regimes[sym_side] = ctx
            continue
        if etyp == "ho":
            sym_side, ts, won, _info = item
            brain.observe_holdout(sym_side, ts, won)
            continue
        if etyp == "fwd":
            sym_side, _ts, node, frec, _fctx = item
            learn_forward_label(model, brain, sym_side, node, frec, regimes.get(sym_side))
            n_fwd += 1
            continue
        sym_side, ts, sheet, row_key, naked, marg = item
        n_row += 1
        cs = cat_side_of(sym_side)
        test = ts >= args.cut
        regime = regimes.get(sym_side)
        nd = naked["d"] if (naked is not None and naked["valid"]) else None
        cand = []
        if naked is not None:
            cand.append(("R", None, naked))
        cand.extend(("F", h, m) for h, m in marg.items())
        for kind, hdr, r in cand:
            x = model.feats(brain, kind, sym_side, sheet, row_key, hdr, nd, regime)
            p = model.predict(kind, x)
            d = r["d"] if r["valid"] else None
            y = 1.0 if (d is not None and d > EPS) else 0.0
            if kind == "F":
                x0 = Online.static_x(x)
                p0 = model.predict("F0", x0)
            if test:
                key = row_key if kind == "R" else str(hdr).strip()
                ps = prof_sw[cs][(kind, key)]
                n_sym, pos_sym = len(ps[0]), len(ps[1])
                cur_skip = n_sym >= 10 and pos_sym <= max(1, int(0.05 * n_sym)) and (pos_sym == 0 or pos_sym / n_sym < 0.05)
                rec[kind].append((p, r["secs"] or 0.0, y, d if y else 0.0, cur_skip))
                if kind == "F":
                    rec["F0R"].append((p0, r["secs"] or 0.0, y, d if y else 0.0, cur_skip, nd is not None and nd > EPS))
            learn_fit_eval(model, brain, kind, sym_side, sheet, row_key, hdr, r, nd, regime)
            ps = prof_sw[cs][(kind, row_key if kind == "R" else str(hdr).strip())]
            ps[0].add(sym_side)
            if y:
                ps[1].add(sym_side)
        brain.observe_row(sym_side, ts, row_key, naked, marg)
        observe_names(brain, sym_side, ts, row_key, naked, marg)
        if naked is not None:
            model.bump(sym_side, sheet, 1.0 if (nd is not None and nd > EPS) else 0.0)
    out = {"_meta": {"since": args.since, "cut": args.cut, "items": n_row, "rows": n_row, "forward_labels": n_fwd, "weights": model.w, "floor_per_1000": args.floor, "w_forward": WEIGHT_FORWARD, "w_fit": WEIGHT_FIT, "note": "prequential: each eval predicted before learned; current rule = never-positive / <5% pos on >=10 sym_sides (profile DEAD/USELESS/HARMFUL proxy) applied to rows AND cells; fit labels learn at w_fit, FORWARD labels at w_forward"}, "kinds": {}}
    for kind in ("R", "F", "F0R"):
        R = rec[kind]
        if not R:
            continue
        if kind == "F0R":  # static cell model + revive-all on a positive naked (the pilot's PRIOR-REVIVED path): weight 1 when revived
            R = [(1.0 if rv else p, sec, y, d, c) for p, sec, y, d, c, rv in R]
        S = sum(r[1] for r in R) or 1.0
        P = sum(r[2] for r in R) or 1.0
        D = sum(r[3] for r in R) or 1.0
        cur = [r for r in R if not r[4]]
        curve = []
        for pr in [float(v) for v in args.prefs.split(",")]:
            fl = args.floor / 1000.0
            ks = kp = kd = 0.0
            for p, sec, y, d, _c in R:
                w = 1.0 if p >= 1.0 else max(fl, min(1.0, p / pr))
                ks += w * sec
                kp += w * y
                kd += w * d
            curve.append({"p_ref": pr, "secs_kept": round(ks / S, 4), "pos_kept": round(kp / P, 4), "pos_delta_kept": round(kd / D, 4)})
        ranked = sorted(R, key=lambda r: -r[0])
        cap = {}
        cs_ = cp_ = 0.0
        targets = [0.9, 0.95, 0.98, 0.99]
        for p, sec, y, d, _c in ranked:
            cs_ += sec
            cp_ += y
            for t in targets:
                if t not in cap and cp_ / P >= t:
                    cap[t] = round(cs_ / S, 4)
        out["kinds"][kind] = {"n": len(R), "pos": int(P), "secs": round(S, 1), "current_rule": {"secs_kept": round(sum(r[1] for r in cur) / S, 4), "pos_kept": round(sum(r[2] for r in cur) / P, 4), "pos_delta_kept": round(sum(r[3] for r in cur) / D, 4)}, "brain_curve": curve, "oracle_rank_secs_for_pos_share": {str(k): v for k, v in cap.items()}}
    print(json.dumps(out, indent=1))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=1)
    return 0


def cmd_export(args):
    """Prequential-train over every log, then write one runtime file per cat_side for the pilot: weights + the evidence
    tables the features read (GLOBAL/cat/name/pair/sym edges as [pos, n]). data/brain/brain_<CAT_SIDE>.json.
    Format is backward compatible: old readers ignore the new mh/fwd/ho keys; gain weights stay x[0..11]-first."""
    files = collect_files(args.glob or DEFAULT_GLOBS)
    events = stream_events(files, args.since or "")
    brain, model = Brain(), Online()
    regimes = {}
    n_row = n_fwd = 0
    for etyp, _ets, item in events:
        if etyp == "ctx":
            regimes[item[0]] = item[2]
            continue
        if etyp == "ho":
            brain.observe_holdout(item[0], item[1], item[2])
            continue
        if etyp == "fwd":
            sym_side, _ts, node, frec, _fctx = item
            learn_forward_label(model, brain, sym_side, node, frec, regimes.get(sym_side))
            n_fwd += 1
            continue
        sym_side, ts, sheet, row_key, naked, marg = item
        n_row += 1
        regime = regimes.get(sym_side)
        nd = naked["d"] if (naked is not None and naked["valid"]) else None
        cand = ([("R", None, naked)] if naked is not None else []) + [("F", h, m) for h, m in marg.items()]
        for kind, hdr, r in cand:
            learn_fit_eval(model, brain, kind, sym_side, sheet, row_key, hdr, r, nd, regime)
        brain.observe_row(sym_side, ts, row_key, naked, marg)
        observe_names(brain, sym_side, ts, row_key, naked, marg)
        if naked is not None:
            model.bump(sym_side, sheet, 1.0 if (nd is not None and nd > EPS) else 0.0)
    out_dir = Path(args.out_dir) if args.out_dir else Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data" / "brain"
    out_dir.mkdir(parents=True, exist_ok=True)
    per = collections.defaultdict(lambda: {"cat": {}, "pair": {}, "sym": collections.defaultdict(dict), "fwd": {"global": {}, "cat": {}, "sym": collections.defaultdict(dict)}, "ho": {}})
    glob_t = {}
    for (scope, a, b), e in brain.edges.items():
        pn = [e.pos, max(0, e.n - e.inv)]
        if scope == GLOBAL:
            glob_t[a] = pn
        elif scope.startswith(("CRYPTO_", "STOCKS_")):
            (per[scope]["pair"] if b else per[scope]["cat"])[(a + "|" + b) if b else a] = pn
        else:
            per[cat_side_of(scope)]["sym"][scope][a] = pn
    fwd_global = {}
    for (scope, node), e in brain.fwd.items():
        pn = [e.pos, e.n, round(e.wpos, 4), round(e.wn, 4)]
        if scope == GLOBAL:
            fwd_global[node] = pn
        elif scope.startswith(("CRYPTO_", "STOCKS_")):
            per[scope]["fwd"]["cat"][node] = pn
        else:
            per[cat_side_of(scope)]["fwd"]["sym"][scope][node] = pn
    for sym_side in brain.ho_seen:
        per[cat_side_of(sym_side)]["ho"][sym_side] = round(brain.ho_ema[sym_side], 4)
    meta = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rows": n_row, "forward_labels": n_fwd, "since": args.since, "nf": Online.NF, "w_forward": WEIGHT_FORWARD, "w_fit": WEIGHT_FIT, "p_ref": {"R": args.p_ref_r, "F": args.p_ref_f}, "floor_per_1000": args.floor}
    for cs, blk in per.items():
        blob = {"_meta": dict(meta, cat_side=cs), "w": model.w, "mh": model.mh, "global": glob_t, "cat": blk["cat"], "pair": blk["pair"], "sym": blk["sym"], "fwd": {"global": fwd_global, "cat": blk["fwd"]["cat"], "sym": blk["fwd"]["sym"]}, "ho": blk["ho"], "ema": {k: v for k, v in model.ema.items() if isinstance(k, str) and cat_side_of(k) == cs}}
        p = out_dir / f"brain_{cs}.json"
        with open(str(p) + ".tmp", "w") as fh:
            json.dump(blob, fh)
        os.replace(str(p) + ".tmp", p)
        print(json.dumps({"out": str(p), "cat": len(blk["cat"]), "pair": len(blk["pair"]), "sym_sides": len(blk["sym"]), "fwd": sum(len(v) for v in (blk["fwd"]["global"], blk["fwd"]["cat"])) + sum(len(v) for v in blk["fwd"]["sym"].values()), "bytes": p.stat().st_size}))
    return 0


class PilotBrain:
    """Runtime twin of Online for one board (the pilot imports this). Loads data/brain/brain_<CAT_SIDE>.json; learns online
    inside the board (board/tab EMA + this sym_side's own evidence) from every naked it sees. Pure decisions: p_row / p_cell
    return (p_eval, p_pos, n_evidence); the caller draws with draw() and books the propensity."""

    def __init__(self, path, sym_side):
        blob = json.load(open(path))
        self.sym_side, self.cs = sym_side, cat_side_of(sym_side)
        self.w = {k: (list(v) + [0.0] * Online.NF)[:Online.NF] for k, v in (blob.get("w") or {}).items()}
        for k in ("R", "F", "F0"):
            self.w.setdefault(k, ([-4.0] + [0.0] * (Online.NF - 1)))
        self.mh = {}
        for m, kinds in ((blob.get("mh") or {}).items()):
            try:
                self.mh[m] = {k: (list(v) + [0.0] * Online.NF)[:Online.NF] for k, v in kinds.items()}
            except Exception:
                continue
        self.glob, self.cat, self.pair = blob.get("global") or {}, blob.get("cat") or {}, blob.get("pair") or {}
        self.sym = dict((blob.get("sym") or {}).get(sym_side) or {})
        self.ho = blob.get("ho") or {}
        self.meta = blob.get("_meta") or {}
        self.ctx = {"tier": 0.0, "bh": 0.0}
        self.ema = collections.defaultdict(lambda: 0.02)
        e0 = (blob.get("ema") or {}).get(sym_side)
        if e0:
            self.ema[sym_side] = float(e0)
        self.p_ref = {"R": float(os.environ.get("V15_BRAIN_PREF_R", self.meta.get("p_ref", {}).get("R", 0.005))), "F": float(os.environ.get("V15_BRAIN_PREF_F", self.meta.get("p_ref", {}).get("F", 0.005)))}
        self.floor = float(os.environ.get("V15_BRAIN_FLOOR", self.meta.get("floor_per_1000", 5.0))) / 1000.0

    def observe_ctx(self, tier=None, bh=None):
        """Board regime context (from the BOARD_CTX line). Additive only: zeros = model behaves as before."""
        self.ctx = {"tier": tier_code(tier), "bh": bh_code(bh)}

    def _x(self, kind, sheet, row_key, hdr):
        a = ("R:" + row_key) if kind == "R" else ("F:" + str(hdr).strip())
        x = [1.0] + [0.0] * (Online.NF - 1)
        gp, gn = self.glob.get(a, (0, 0))
        cp, cn = self.cat.get(a, (0, 0))
        sp, sn = self.sym.get(a, (0, 0))
        x[1] = _lo(gp, gn) * gn / (gn + 3.0)
        x[2] = _lo(cp, cn) * cn / (cn + 3.0)
        x[3] = (_lo(sp, sn) - x[2]) * sn / (sn + 2.0)
        name = "N:" + (row_key.split("=", 1)[0] if kind == "R" else str(hdr).split("=", 1)[0].strip())
        np_, nn = self.cat.get(name, (0, 0))
        x[4] = _lo(np_, nn) * nn / (nn + 3.0)
        if kind != "R":
            pp, pn = self.pair.get(a + "|R:" + row_key, (0, 0))
            x[5] = _lo(pp, pn) * pn / (pn + 2.0)
        x[6] = math.log1p(cn)
        x[7] = 1.0 if cn < 3 else 0.0
        be, te = self.ema[self.sym_side], self.ema[(self.sym_side, sheet)]
        x[8] = math.log(be / (1.0 - be))
        x[9] = math.log(te / (1.0 - te))
        tc, bc = self.ctx.get("tier", 0.0), self.ctx.get("bh", 0.0)
        x[12] = tc * x[2]
        x[13] = bc * x[2]
        x[14] = tc
        x[15] = bc
        try:
            ho = min(0.98, max(0.02, float(self.ho.get(self.sym_side, 0.5))))
        except Exception:
            ho = 0.5
        x[16] = math.log(ho / (1.0 - ho))
        return x, cn

    def _p(self, kind, x):
        return Online._sig(sum(w * v for w, v in zip(self.w[kind], x)))

    def p_metric_head(self, metric, kind, sheet, row_key, hdr=None):
        """P(metric improves) from the metric head. kind R = naked row, F = yellow cell (static F0 weights)."""
        if metric not in self.mh:
            return None
        wk = "R" if kind == "R" else "F0"
        if wk not in self.mh[metric]:
            return None
        x, _cn = self._x("R" if kind == "R" else "F", sheet, row_key, hdr)
        return Online._sig(sum(w * v for w, v in zip(self.mh[metric][wk], x)))

    def p_row(self, sheet, row_key):
        x, cn = self._x("R", sheet, row_key, None)
        p = self._p("R", x)
        pe = 1.0 if cn < 3 else max(self.floor, min(1.0, p / self.p_ref["R"]))
        return pe, p, cn

    def p_cell(self, sheet, row_key, hdr):
        x, cn = self._x("F", sheet, row_key, hdr)
        p = self._p("F0", x)
        pe = 1.0 if cn < 3 else max(self.floor, min(1.0, p / self.p_ref["F"]))
        return pe, p, cn

    def observe_naked(self, sheet, row_key, delta, alpha=0.02):
        y = 1.0 if (delta is not None and delta > EPS) else 0.0
        for k in (self.sym_side, (self.sym_side, sheet)):
            self.ema[k] = min(0.98, max(0.002, (1 - alpha) * self.ema[k] + alpha * y))
        if delta is not None:
            a = "R:" + row_key
            pos, n = self.sym.get(a, (0, 0))
            self.sym[a] = (pos + int(y), n + 1)


class FileModel:
    """Frozen scorer for one exported brain_<CAT_SIDE>.json blob. Mirrors PilotBrain math (gain heads R/F0) so two
    model files can be compared on the same evals. Tolerates old files (12-long weights, no mh/ho/fwd)."""

    def __init__(self, blob):
        self.w = {k: (list(v) + [0.0] * Online.NF)[:Online.NF] for k, v in (blob.get("w") or {}).items()}
        for k in ("R", "F", "F0"):
            self.w.setdefault(k, ([-4.0] + [0.0] * (Online.NF - 1)))
        self.glob, self.cat, self.pair = blob.get("global") or {}, blob.get("cat") or {}, blob.get("pair") or {}
        self.sym = blob.get("sym") or {}
        self.ho = blob.get("ho") or {}
        self.ctx = {}

    def set_ctx(self, sym_side, ctx):
        self.ctx[sym_side] = ctx or {}

    def score(self, kind, sym_side, sheet, row_key, hdr):
        cs = cat_side_of(sym_side)
        a = ("R:" + row_key) if kind == "R" else ("F:" + str(hdr).strip())
        sym_t = self.sym.get(sym_side) or {}
        gp, gn = self.glob.get(a, (0, 0))
        cp, cn = self.cat.get(a, (0, 0))
        sp, sn = sym_t.get(a, (0, 0))
        x = [1.0] + [0.0] * (Online.NF - 1)
        x[1] = _lo(gp, gn) * gn / (gn + 3.0)
        x[2] = _lo(cp, cn) * cn / (cn + 3.0)
        x[3] = (_lo(sp, sn) - x[2]) * sn / (sn + 2.0)
        name = "N:" + (row_key.split("=", 1)[0] if kind == "R" else str(hdr).split("=", 1)[0].strip())
        np_, nn = self.cat.get(name, (0, 0))
        x[4] = _lo(np_, nn) * nn / (nn + 3.0)
        if kind != "R":
            pp, pn = self.pair.get(a + "|R:" + row_key, (0, 0))
            x[5] = _lo(pp, pn) * pn / (pn + 2.0)
        x[6] = math.log1p(cn)
        x[7] = 1.0 if cn < 3 else 0.0
        x[8] = math.log(0.02 / 0.98)
        x[9] = math.log(0.02 / 0.98)
        ctx = self.ctx.get(sym_side) or {}
        tc, bc = tier_code(ctx.get("tier")), bh_code(ctx.get("bh"))
        x[12] = tc * x[2]
        x[13] = bc * x[2]
        x[14] = tc
        x[15] = bc
        try:
            ho = min(0.98, max(0.02, float((self.ho.get(sym_side, 0.5)))))
        except Exception:
            ho = 0.5
        x[16] = math.log(ho / (1.0 - ho))
        wk = "R" if kind == "R" else "F0"
        return Online._sig(sum(w * v for w, v in zip(self.w[wk], x)))


def _capture_at_budget(scored, budget):
    """Positives kept walking p-desc until budget eval-seconds are spent (a secs-0 eval costs 1 unit).
    scored = [(p, secs, y)]. Fractional take on the boundary eval keeps the comparison exact."""
    pos = spent = 0.0
    for p, secs, y in sorted(scored, key=lambda r: -r[0]):
        if spent >= budget:
            break
        unit = secs if secs > 0 else 1.0
        take = min(1.0, (budget - spent) / unit)
        pos += take * y
        spent += take * unit
    return pos


def compare_models(champ_path, chal_path, globs=None, days=3, budget_frac=0.5, min_pos=10):
    """Champion/challenger promotion verdict on last-`days` days of real logged evals. Both models score every eval;
    at EQUAL eval-seconds (budget_frac of total) the positives-kept are compared. The challenger promotes ONLY on
    strict improvement. Returns a verdict dict; never writes or replaces any file (no auto-replace wiring)."""
    try:
        champ = FileModel(json.load(open(champ_path)))
    except Exception as e:
        return {"winner": "error", "promote": False, "error": f"champion unreadable: {e}"}
    try:
        chal = FileModel(json.load(open(chal_path)))
    except Exception as e:
        return {"winner": "error", "promote": False, "error": f"challenger unreadable: {e}"}
    try:
        import datetime
        cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        cut = ""
    files = collect_files(globs or DEFAULT_GLOBS)
    ctxs = {}
    for f in files:
        try:
            for sym_side, ts, ctx in iter_ctx(f):
                if ts >= cut and (sym_side not in ctxs or ts >= ctxs[sym_side][0]):
                    ctxs[sym_side] = (ts, ctx)
        except Exception:
            continue
    for sym_side, (_ts, ctx) in ctxs.items():
        champ.set_ctx(sym_side, ctx)
        chal.set_ctx(sym_side, ctx)
    scored_c, scored_h = [], []
    n = 0
    for f in files:
        try:
            rows = [it for it in iter_rows(f) if it[1] >= cut]
        except Exception:
            continue
        for sym_side, _ts, sheet, row_key, naked, marg in rows:
            cands = ([("R", None, naked)] if naked is not None else []) + [("F", h, m) for h, m in marg.items()]
            for kind, hdr, r in cands:
                d = r["d"] if r["valid"] else None
                if d is None:
                    continue
                y = 1.0 if d > EPS else 0.0
                secs = r["secs"] or 0.0
                try:
                    pc = champ.score(kind, sym_side, sheet, row_key, hdr)
                    ph = chal.score(kind, sym_side, sheet, row_key, hdr)
                except Exception:
                    continue
                scored_c.append((pc, secs, y))
                scored_h.append((ph, secs, y))
                n += 1
    pos = sum(s[2] for s in scored_c)
    secs = sum(s[1] for s in scored_c)
    verdict = {"winner": "insufficient", "promote": False, "champion": champ_path, "challenger": chal_path, "days": days, "cut": cut, "n": n, "pos": int(pos), "eval_secs": round(secs, 1), "budget_frac": budget_frac}
    if n == 0 or pos < min_pos:
        verdict["note"] = f"only {n} evals / {int(pos)} positives in the last {days}d (min_pos={min_pos})"
        return verdict
    budget = budget_frac * sum((s[1] if s[1] > 0 else 1.0) for s in scored_c)
    cp = _capture_at_budget(scored_c, budget)
    hp = _capture_at_budget(scored_h, budget)
    verdict.update({"budget_secs": round(budget, 1), "champ_pos_kept": round(cp, 2), "chal_pos_kept": round(hp, 2)})
    if hp > cp:
        verdict.update({"winner": "challenger", "promote": True})
    elif cp > hp:
        verdict.update({"winner": "champion", "promote": False})
    else:
        verdict.update({"winner": "tie", "promote": False})
    return verdict


def cmd_compare(args):
    print(json.dumps(compare_models(args.champ, args.chal, args.glob, args.days, args.budget, args.min_pos), indent=1))
    return 0


def cmd_suggest(args):
    """Top nodes for a metric from brain_edges.json (scope sym_side if evidenced, else its cat_side). Ranking = P(pos) x E[d|pos]
    for gain, mean metric difference for tim/trades/dd/sharpe (dd: most negative first). FORWARD labels are shown as
    fwd_n/fwd_p columns when present. Empty evidence prints an explicit 'no evidence' line — never invented rows."""
    blob = json.load(open(args.edges))
    edges = blob.get("edges") or {}
    fwd = blob.get("forward") or {}
    scope = getattr(args, "sym_side", None) or args.scope
    cs = cat_side_of(scope) if scope.endswith(("_LONG", "_SHORT")) else scope
    rows = []
    for k, e in (edges.get(cs) or {}).items():
        if "|" in k or e.get("n_sym", 0) < args.min_n:
            continue
        own = (edges.get(scope) or {}).get(k) if scope != cs else None
        valid = max(1, e["n"] - e.get("inv", 0))
        p = (e["pos"] + 1.0) / (valid + 2.0)
        if own:
            p = (own["pos"] + M_SHRINK * p) / (max(1, own["n"] - own.get("inv", 0)) + M_SHRINK)
        eg = e["sum_pos"] / e["pos"] if e["pos"] else 0.0
        mkey = {"gain": None, "tim": "mean_dtim", "trades": "mean_dtr", "dd": "mean_ddd", "sharpe": "mean_dsh"}[args.metric]
        v = p * eg if mkey is None else e.get(mkey)
        if v is None:
            continue
        fe = (fwd.get(scope) or {}).get(k) or (fwd.get(cs) or {}).get(k) or (fwd.get(GLOBAL) or {}).get(k)
        fp, fn = (None, 0)
        if fe:
            fn = fe.get("n", 0)
            fp = (fe.get("wpos", 0.0) + 0.5) / (fe.get("wn", 0.0) + 1.0)
        rows.append((v, k, p, eg, e, fp, fn))
    if not rows:
        print(f"no evidence: scope={scope} metric={args.metric} (n=0)")
        return 0
    rev = args.metric != "dd"
    rows.sort(key=lambda x: x[0], reverse=rev)
    for v, k, p, eg, e, fp, fn in rows[: args.top]:
        fw = f" fwd_p={fp:.3f} fwd_n={fn}" if fp is not None else " fwd_p=- fwd_n=0"
        print(f"{v:+9.4f}  {k:70s} p_pos={p:.3f} E[d|pos]={eg:.2f} n_sym={e['n_sym']} pos={e['pos']} d_tim={e.get('mean_dtim')} d_trades={e.get('mean_dtr')} d_dd={e.get('mean_ddd')}{fw}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--glob", action="append")
    b.add_argument("--out-dir")
    r = sub.add_parser("replay")
    r.add_argument("--glob", action="append")
    r.add_argument("--cut", required=True, help="ISO ts: train < cut <= test")
    r.add_argument("--since", default="", help="ignore rows before this ISO ts")
    r.add_argument("--floors", default="1,2,5,10,20,50")
    r.add_argument("--qref", type=float, default=Q_REF)
    r.add_argument("--out")
    q = sub.add_parser("prequential")
    q.add_argument("--glob", action="append")
    q.add_argument("--since", default="")
    q.add_argument("--cut", required=True)
    q.add_argument("--floor", type=float, default=5.0, help="evals per 1000 for the weakest candidates")
    q.add_argument("--prefs", default="0.002,0.005,0.01,0.02,0.03,0.05,0.08,0.12,0.2")
    q.add_argument("--out")
    e = sub.add_parser("export")
    e.add_argument("--glob", action="append")
    e.add_argument("--since", default="")
    e.add_argument("--out-dir")
    e.add_argument("--p-ref-r", type=float, default=0.005)
    e.add_argument("--p-ref-f", type=float, default=0.005)
    e.add_argument("--floor", type=float, default=5.0)
    s = sub.add_parser("suggest")
    s.add_argument("--edges", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "brain", "brain_edges.json"))
    s.add_argument("--scope", default=None, help="sym_side (e.g. BTCUSDC_LONG) or cat_side (CRYPTO_LONG)")
    s.add_argument("--sym-side", default=None, help="sym_side to answer for (e.g. BTCUSDC_LONG); overrides --scope")
    s.add_argument("--metric", default="gain", choices=["gain", "tim", "trades", "dd", "sharpe"])
    s.add_argument("--min-n", type=int, default=5)
    s.add_argument("--top", type=int, default=25)
    c = sub.add_parser("compare")
    c.add_argument("--champ", required=True, help="champion model file (brain_<CAT_SIDE>.json)")
    c.add_argument("--chal", required=True, help="challenger model file")
    c.add_argument("--glob", action="append")
    c.add_argument("--days", type=float, default=3.0)
    c.add_argument("--budget", type=float, default=0.5)
    c.add_argument("--min-pos", type=int, default=10)
    args = ap.parse_args(argv)
    if args.cmd == "suggest" and not (args.scope or args.sym_side):
        ap.error("suggest needs --sym-side SS or --scope SCOPE")
    return {"build": cmd_build, "replay": cmd_replay, "prequential": cmd_prequential, "export": cmd_export, "suggest": cmd_suggest, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
