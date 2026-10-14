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
Nothing here writes sheets, progress JSONs, templates or configs. NO-LIES: every count is a real logged eval.
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

EPS = 1e-9
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
DENY = ("MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED")
SKIP_LABEL_PREFIX = ("FINAL_", "DIAG_", "REPAIR365", "SACRED", "COARSE", "JOINT:", "STANDALONE:", "REF_BASELINE", "RECALC", "ENDGAME", "FOCUS")
PROFILE_SKIP = ("DEAD", "USELESS", "HARMFUL")
M_SHRINK = float(os.environ.get("V15_BRAIN_M", "8"))
Q_REF = float(os.environ.get("V15_BRAIN_QREF", "0.80"))
GLOBAL = "GLOBAL"
METRICS = ("d", "dtim", "dtr", "ddd", "dsh")


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
            if label is None or label in ("hustle", "BOARD_CTX") or str(label).startswith(SKIP_LABEL_PREFIX) or r.get("cached"):
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


class Brain:
    """The graph. edges[(scope, a, b)] -> Edge. a = node key ("R:SWITCH=cand" or "F:FILTER=opt"), b = "" or the row node."""

    def __init__(self, pair_min_scope=("cat",)):
        self.edges = collections.defaultdict(Edge)
        self.pair_scopes = pair_min_scope

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
    n = 0
    for f in files:
        for sym_side, ts, _sheet, row_key, naked, marg in iter_rows(f):
            brain.observe_row(sym_side, ts, row_key, naked, marg)
            n += 1
    out_dir = args.out_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "brain")
    os.makedirs(out_dir, exist_ok=True)
    blob = {"_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": len(files), "rows": n, "edges": len(brain.edges), "m_shrink": M_SHRINK, "source": "v15 delta logs (real engine evals, cache hits excluded)"}, "edges": brain.to_json()}
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


def cmd_suggest(args):
    """Top nodes for a metric from brain_edges.json (scope sym_side if evidenced, else its cat_side). Ranking = P(pos) x E[d|pos]
    for gain, mean metric difference for tim/trades/dd/sharpe (dd: most negative first). Research view — never a delta."""
    blob = json.load(open(args.edges))
    edges = blob["edges"]
    scope = args.scope
    cs = cat_side_of(scope) if scope.endswith(("_LONG", "_SHORT")) else scope
    rows = []
    for k, e in (edges.get(cs) or {}).items():
        if "|" in k or e["n_sym"] < args.min_n:
            continue
        own = (edges.get(scope) or {}).get(k) if scope != cs else None
        valid = max(1, e["n"] - e["inv"])
        p = (e["pos"] + 1.0) / (valid + 2.0)
        if own:
            p = (own["pos"] + M_SHRINK * p) / (max(1, own["n"] - own["inv"]) + M_SHRINK)
        eg = e["sum_pos"] / e["pos"] if e["pos"] else 0.0
        mkey = {"gain": None, "tim": "mean_dtim", "trades": "mean_dtr", "dd": "mean_ddd", "sharpe": "mean_dsh"}[args.metric]
        v = p * eg if mkey is None else e.get(mkey)
        if v is None:
            continue
        rows.append((v, k, p, eg, e))
    rev = args.metric != "dd"
    rows.sort(key=lambda x: x[0], reverse=rev)
    for v, k, p, eg, e in rows[: args.top]:
        print(f"{v:+9.4f}  {k:70s} p_pos={p:.3f} E[d|pos]={eg:.2f} n_sym={e['n_sym']} pos={e['pos']} d_tim={e.get('mean_dtim')} d_trades={e.get('mean_dtr')} d_dd={e.get('mean_ddd')}")
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
    s = sub.add_parser("suggest")
    s.add_argument("--edges", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "brain", "brain_edges.json"))
    s.add_argument("--scope", required=True, help="sym_side (e.g. BTCUSDC_LONG) or cat_side (CRYPTO_LONG)")
    s.add_argument("--metric", default="gain", choices=["gain", "tim", "trades", "dd", "sharpe"])
    s.add_argument("--min-n", type=int, default=5)
    s.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)
    return {"build": cmd_build, "replay": cmd_replay, "suggest": cmd_suggest}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
