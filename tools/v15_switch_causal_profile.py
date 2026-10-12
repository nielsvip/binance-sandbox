#!/usr/bin/env python3
"""Mine every v15 delta log (real engine evals, NO-LIES source of truth, bible §20)
into a per-cat_side causal profile of every SWITCH=cand and every yellow FILTER=opt:
how often it moves the ledger, in which direction, and what it does to TIM and trades.

Stdlib only (runs on S1 with the system python3). Output:
  data/causal/switch_causal_profile.json   — machine-readable priors for the pilot
  data/causal/switch_causal_profile.md     — human report (top useful / harmful / dead)

Delta-log records carry gain_pct, trades, tim, valid, cum_before, delta, secs. The
baseline TIM/trades at any point are taken from the latest VALID no-op eval
(|delta|<1e-9 reproduces the running set exactly, bible §14.1), so d_tim/d_trades are
real engine differences, never estimates. DD and WR are NOT in the logs — the engine
flip study (`tools/v15_switch_flip_study.py`) measures those.
"""
import argparse
import collections
import glob
import json
import os
import statistics
import sys
import time

EPS = 1e-9
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
DENY = ("MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED")


def cat_side_of(sym_side):
    sym, _, side = sym_side.rpartition("_")
    cat = "CRYPTO" if sym.upper().endswith(CRYPTO_SUFFIX) else "STOCKS"
    return f"{cat}_{side}"


def _f(x):
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


class Agg:
    __slots__ = ("per_sym",)

    def __init__(self):
        self.per_sym = {}

    def add(self, sym_side, delta, d_tim, d_trades, valid, secs, promoted):
        # last measurement per sym_side wins (refill passes re-measure the same key)
        self.per_sym[sym_side] = (delta, d_tim, d_trades, valid, secs, promoted)

    def summary(self):
        vals = list(self.per_sym.values())
        n = len(vals)
        deltas = [v[0] for v in vals if v[0] is not None]
        valid_deltas = [v[0] for v in vals if v[0] is not None and v[3]]
        pos = sum(1 for d in valid_deltas if d > EPS)
        neg = sum(1 for d in valid_deltas if d < -EPS)
        zero = sum(1 for d in deltas if abs(d) <= EPS)
        dt = [v[1] for v in vals if v[1] is not None and v[3]]
        dtr = [v[2] for v in vals if v[2] is not None and v[3]]
        secs = [v[4] for v in vals if v[4] is not None]
        invalid = sum(1 for v in vals if not v[3])
        promoted = sum(1 for v in vals if v[5])
        out = {
            "n_sym": n,
            "pos_sym": pos,
            "neg_sym": neg,
            "zero_sym": zero,
            "invalid_sym": invalid,
            "promoted_sym": promoted,
            "mean_delta": round(statistics.fmean(valid_deltas), 4) if valid_deltas else None,
            "median_delta": round(statistics.median(valid_deltas), 4) if valid_deltas else None,
            "sum_pos_delta": round(sum(d for d in valid_deltas if d > EPS), 3),
            "sum_neg_delta": round(sum(d for d in valid_deltas if d < -EPS), 3),
            "mean_d_tim": round(statistics.fmean(dt), 3) if dt else None,
            "mean_d_trades": round(statistics.fmean(dtr), 3) if dtr else None,
            "mean_secs": round(statistics.fmean(secs), 4) if secs else None,
            "total_secs": round(sum(secs), 1),
        }
        out["pos_rate"] = round(pos / n, 3) if n else None
        out["verdict"] = classify(out)
        return out


def classify(s):
    n, pos, neg, zero = s["n_sym"], s["pos_sym"], s["neg_sym"], s["zero_sym"]
    if n < 3:
        return "INSUFFICIENT"
    if pos >= 3 and (s["mean_delta"] or 0) > 0 and pos / n >= 0.15:
        return "USEFUL"
    if pos >= 2 and pos / n >= 0.10:
        return "SOMETIMES"
    if n >= 10 and pos == 0 and neg == 0 and s["invalid_sym"] == 0:
        return "DEAD"
    if n >= 10 and pos == 0 and (neg >= 3 or s["invalid_sym"] >= 3):
        return "HARMFUL"
    if n >= 10 and pos <= 1 and (pos / n) < 0.05:
        return "USELESS"
    return "NEUTRAL"


def mine_file(path, switch_agg, filter_agg, name_agg, stats):
    sym_side = os.path.basename(path).split("_jump")[0]
    if not sym_side.endswith(("_LONG", "_SHORT")):
        parts = os.path.basename(path).replace(".jsonl", "").split("_")
        sym_side = "_".join(parts[:2])
    cs = cat_side_of(sym_side)
    base_tim = None
    base_trades = None
    row_buf = {}
    n_lines = 0
    with open(path, "r", errors="replace") as fh:
        for line in fh:
            n_lines += 1
            try:
                r = json.loads(line)
            except Exception:
                stats["bad_lines"] += 1
                continue
            label = r.get("label")
            if label == "ROW_DONE":
                key = (r.get("sheet"), r.get("row"))
                buf = row_buf.pop(key, None)
                if not buf:
                    continue
                naked = buf.get("naked")
                promoted = bool(r.get("promoted"))
                sw, cand = r.get("switch"), str(r.get("cand"))
                if sw in DENY or sw is None:
                    continue
                if naked is not None:
                    d, dt, dtr, valid, secs = naked
                    switch_agg[cs][(sw, cand)].add(sym_side, d, dt, dtr, valid, secs, promoted)
                    name_agg[cs][sw].add(sym_side, d, dt, dtr, valid, secs, promoted)
                for hdr, y in buf.get("yellows", {}).items():
                    d, dt, dtr, valid, secs = y
                    if naked is not None and naked[0] is not None and d is not None:
                        marg = d - naked[0]
                        marg_t = (dt - naked[1]) if (dt is not None and naked[1] is not None) else None
                        marg_tr = (dtr - naked[2]) if (dtr is not None and naked[2] is not None) else None
                    else:
                        marg, marg_t, marg_tr = d, dt, dtr
                    filter_agg[cs][hdr].add(sym_side, marg, marg_t, marg_tr, valid, secs, False)
                continue
            if label is None or label.startswith(("FINAL_", "DIAG_", "REPAIR365", "SACRED", "COARSE")):
                stats["other_labels"] += 1
                continue
            delta = _f(r.get("delta"))
            tim = _f(r.get("tim"))
            trades = _f(r.get("trades"))
            valid = bool(r.get("valid"))
            secs = _f(r.get("secs"))
            if secs is not None:
                stats["total_secs"] += secs
                stats["n_evals"] += 1
            if valid and delta is not None and abs(delta) <= EPS and tim is not None:
                base_tim = tim
                base_trades = trades
            d_tim = (tim - base_tim) if (tim is not None and base_tim is not None and valid) else None
            d_tr = (trades - base_trades) if (trades is not None and base_trades is not None and valid) else None
            key = (r.get("sheet"), r.get("row"))
            buf = row_buf.setdefault(key, {"yellows": {}})
            rec = (delta, d_tim, d_tr, valid, secs)
            if label == "naked":
                buf["naked"] = rec
            else:
                buf["yellows"][label] = rec
    stats["lines"] += n_lines
    stats["files"] += 1
    stats["sym_sides"].add(sym_side)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", action="append", default=None, help="delta-log glob(s); default: ~/v15_run*/progress/v15_delta_log/*.jsonl + lifecycle_pilot")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--limit", type=int, default=0, help="debug: only first N files")
    args = ap.parse_args()
    home = os.path.expanduser("~")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = args.out_dir or os.path.join(base, "data", "causal")
    os.makedirs(out_dir, exist_ok=True)
    globs = args.glob or [
        os.path.join(home, "v15_run*", "progress", "v15_delta_log", "*.jsonl"),
        os.path.join(home, "v15_run*", "v15_delta_log", "*.jsonl"),
        os.path.join(base, "data", "reports", "lifecycle_pilot", "v15_delta_log", "*.jsonl"),
    ]
    files = []
    for g in globs:
        files.extend(glob.glob(g))
    files = sorted(set(files), key=lambda p: os.path.getmtime(p))
    if args.limit:
        files = files[-args.limit:]
    if not files:
        print("no delta logs found", file=sys.stderr)
        return 2
    switch_agg = collections.defaultdict(lambda: collections.defaultdict(Agg))
    filter_agg = collections.defaultdict(lambda: collections.defaultdict(Agg))
    name_agg = collections.defaultdict(lambda: collections.defaultdict(Agg))
    stats = {"files": 0, "lines": 0, "bad_lines": 0, "other_labels": 0, "n_evals": 0, "total_secs": 0.0, "sym_sides": set()}
    t0 = time.time()
    for i, p in enumerate(files):
        try:
            mine_file(p, switch_agg, filter_agg, name_agg, stats)
        except Exception as e:
            print(f"[warn] {p}: {e}", file=sys.stderr)
        if (i + 1) % 25 == 0:
            print(f"[{i+1}/{len(files)}] lines={stats['lines']} {time.time()-t0:.0f}s", file=sys.stderr, flush=True)
    profile = {"_meta": {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": stats["files"], "lines": stats["lines"],
                         "bad_lines": stats["bad_lines"], "n_evals": stats["n_evals"], "total_eval_secs": round(stats["total_secs"], 1),
                         "sym_sides": len(stats["sym_sides"]), "globs": globs, "source": "v15 delta logs (real engine evals)"},
               "cat_sides": {}}
    savings = {}
    for cs in sorted(set(switch_agg) | set(filter_agg)):
        sw = {f"{k[0]}={k[1]}": a.summary() for k, a in switch_agg[cs].items()}
        fl = {k: a.summary() for k, a in filter_agg[cs].items()}
        nm = {k: a.summary() for k, a in name_agg[cs].items()}
        profile["cat_sides"][cs] = {"switch_cand": sw, "filter_opt": fl, "switch_name": nm}
        tot = sum(v["total_secs"] for v in sw.values()) + sum(v["total_secs"] for v in fl.values())
        waste = sum(v["total_secs"] for v in list(sw.values()) + list(fl.values()) if v["verdict"] in ("DEAD", "HARMFUL", "USELESS"))
        savings[cs] = {"total_secs": round(tot, 1), "dead_harmful_useless_secs": round(waste, 1), "share": round(waste / tot, 3) if tot else None,
                       "n_keys": len(sw) + len(fl), "n_useful": sum(1 for v in list(sw.values()) + list(fl.values()) if v["verdict"] in ("USEFUL", "SOMETIMES"))}
    profile["compute_savings_if_skipped"] = savings
    jp = os.path.join(out_dir, "switch_causal_profile.json")
    with open(jp + ".tmp", "w") as fh:
        json.dump(profile, fh, indent=1, sort_keys=True)
    os.replace(jp + ".tmp", jp)
    write_md(profile, os.path.join(out_dir, "switch_causal_profile.md"))
    print(json.dumps({"out": jp, **profile["_meta"], "savings": savings}, indent=1))
    return 0


def write_md(profile, path):
    m = profile["_meta"]
    L = [f"# Switch causal profile — {m['at']}", "",
         f"Source: {m['files']} delta logs, {m['lines']:,} lines, {m['n_evals']:,} real engine evals ({m['total_eval_secs']/3600:.1f} eval-hours), {m['sym_sides']} sym_sides.",
         "Every number is a real `v12_quick_engine` result from the fleet delta logs (bible §20). d_tim / d_trades are engine differences vs the latest reproduced baseline. DD/WR are not logged — see the flip study.", ""]
    for cs, blk in profile["cat_sides"].items():
        sv = profile["compute_savings_if_skipped"][cs]
        L += [f"## {cs}", "",
              f"keys {sv['n_keys']} · useful/sometimes {sv['n_useful']} · eval time on DEAD/HARMFUL/USELESS keys: {sv['dead_harmful_useless_secs']/3600:.1f}h of {sv['total_secs']/3600:.1f}h = **{(sv['share'] or 0)*100:.0f}%**", ""]
        for title, d in (("Switch=cand", blk["switch_cand"]), ("Filter=opt (marginal vs naked)", blk["filter_opt"])):
            useful = sorted([(k, v) for k, v in d.items() if v["verdict"] in ("USEFUL", "SOMETIMES")], key=lambda kv: -(kv[1]["sum_pos_delta"]))
            harm = sorted([(k, v) for k, v in d.items() if v["verdict"] == "HARMFUL"], key=lambda kv: kv[1]["sum_neg_delta"])
            dead = [k for k, v in d.items() if v["verdict"] == "DEAD"]
            useless = [k for k, v in d.items() if v["verdict"] == "USELESS"]
            L += [f"### {title} — top USEFUL (by summed positive delta)", "", "| key | n_sym | pos | neg | pos_rate | mean_delta | sum_pos | d_tim | d_trades | promoted |", "|---|---|---|---|---|---|---|---|---|---|"]
            for k, v in useful[:40]:
                L.append(f"| `{k}` | {v['n_sym']} | {v['pos_sym']} | {v['neg_sym']} | {v['pos_rate']} | {v['mean_delta']} | {v['sum_pos_delta']} | {v['mean_d_tim']} | {v['mean_d_trades']} | {v['promoted_sym']} |")
            L += ["", f"### {title} — HARMFUL (never positive, ≥3 negative/invalid) — {len(harm)}", ""]
            for k, v in harm[:25]:
                L.append(f"- `{k}` n={v['n_sym']} neg={v['neg_sym']} invalid={v['invalid_sym']} mean={v['mean_delta']} d_tim={v['mean_d_tim']} d_trades={v['mean_d_trades']}")
            L += ["", f"### {title} — DEAD (never moves the ledger, n≥10): {len(dead)} · USELESS (≤1 pos in ≥10, <5%): {len(useless)}", ""]
            L.append(", ".join(f"`{k}`" for k in sorted(dead)[:60]) or "_none_")
            L.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
