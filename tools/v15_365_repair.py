#!/usr/bin/env python3
"""365D repair loop for a finished v15 30D sheet (USER 2026-09-29, BACKTEST_BIBLE §58).

A negative / invalid 365D result is NOT a disqualification — it diagnoses a faulty 30D sheet: on a 30D window
that trends one way, exits never give a positive 30D delta, so the sheet never tests/keeps them and the set rides
one position (e.g. SOLUSDC_LONG 365D -37.81%, TIM 96.4%, 20 trades). 365D punishes that.

Loop: start from the sheet's 30D winning set; each step evaluates every candidate row from the template's EXIT_*,
REDUCE_* and REENTRY_* tabs (more exits = more round trips) plus ENTRY_* tabs while trades are short, on BOTH the
30D and the 365D slice (NPZ prepared once, forked workers share it copy-on-write), and applies the single row that
most improves the joint objective. Stops when both windows are valid (TIM 20-80, DD<=30, >=10 trades) and positive,
or after --max-steps. Writes {SS}_365_repair.json (every step + both windows' metrics) — the repaired set is then
re-run as a 30D sheet (v15_pilot V15_START_OVERRIDES=<json>) and re-verified on 365D.

Usage: python3 tools/v15_365_repair.py --progress <SS>_v14_progress.json --template SPREADSHEETS/TEMPLATE_X_Y.xlsx
       [--out DIR] [--workers 8] [--max-steps 8]
"""
import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_P30 = _P365 = None
TABS_EXITS = ("EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE")
TABS_ENTRY = ("ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES")
FLOOR = 10
FLOOR30, FLOOR365, TARGET30 = 10, 80, 30  # USER 2026-09-29: >=10 trades/mo AND >=80/yr hard; aim >=30/mo (soften) if results not worse


def _eval_pair(ov):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized as eps
    out = []
    for p, wd in ((_P30, 30), (_P365, 365)):
        try:
            r = eps(p, ov, wd) or {}
        except Exception as e:
            r = {"gain_pct": None, "valid": False, "invalid_reason": f"ERR {e}"[:60], "trades": 0}
        out.append({k: r.get(k) for k in ("gain_pct", "trades", "valid", "invalid_reason", "tim_pct", "max_dd_pct", "bh_pct", "pool_sharpe")})
    return out


def _worker(args):
    lab, ov = args
    return lab, ov, _eval_pair(ov)


def ok(w, floor=FLOOR30):
    return bool(w.get("valid")) and (w.get("gain_pct") or 0) > 0 and int(w.get("trades") or 0) >= floor


def ok_pair(pair):
    return ok(pair[0], FLOOR30) and ok(pair[1], FLOOR365)


def _g(w):
    return float(w["gain_pct"]) if w.get("gain_pct") is not None else -1e9


def score(pair):
    a, b = pair
    # 1) windows valid+positive+above trade floor, 2) windows above trade floor, 3) number valid, 4) worst gain, 5) 365D gain
    return (ok(a, FLOOR30) + ok(b, FLOOR365), (int(a.get("trades") or 0) >= FLOOR30) + (int(b.get("trades") or 0) >= FLOOR365), bool(a.get("valid")) + bool(b.get("valid")), min(_g(a), _g(b)), _g(b))


def more_trade_cands(cur, defaults, rows_entry, P):
    """Soften filters + open entry/reentry paths (same token rules as v15_pilot._credible_baseline)."""
    out = []
    for tab, sw, cand in rows_entry:
        ch = P._switch_overrides(sw, P._parse_opt_value(cand, defaults.get(sw)))
        if ch and not all(cur.get(k, defaults.get(k)) == v for k, v in ch.items()):
            out.append((f"{tab}:{sw}={cand}", ch))
    for k, dv in defaults.items():
        c = cur.get(k, dv)
        if not isinstance(dv, bool) or not isinstance(c, bool) or k == "SIMPLE_PRICE_GT0_ENABLED" or k in P.DEAD_VEC_SWITCHES or k in P.LIVE_ONLY_SWITCHES:
            continue
        if c and any(t in k for t in P._ADAPT_SOFTEN_TOKENS):
            out.append((f"SOFTEN:{k}=False", {k: False}))
        elif not c and k.endswith("_ENABLED") and any(t in k for t in P._ADAPT_ENTRY_TOKENS) and not any(t in k for t in P._ADAPT_SOFTEN_TOKENS):
            out.append((f"ENTRY_PATH:{k}=True", {k: True}))
    return out


def main():
    global _P30, _P365
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "v15_365_repair"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-steps", type=int, default=8)
    a = ap.parse_args()
    import v15_pilot as P
    from tools.opt import evaluate_v12 as E
    prog = json.load(open(a.progress))
    ss = prog.get("symside") or Path(a.progress).name.replace("_v14_progress.json", "")
    defaults = P.get_defaults_for_symside(ss)
    cur, _ = P.sanitize_overrides(dict(prog.get("cumulative_overrides") or {}), defaults)
    _P30, _P365 = E.prepare(ss, 30), E.prepare(ss, 365)
    if _P30 is None or _P365 is None:
        print(f"[365-REPAIR] {ss}: cannot prepare 30D/365D NPZ", flush=True)
        return 2
    _t = _P365["npz_prepared"]["timestamps"]
    _t = _t / 1000 if _t[-1] > 1e11 else _t
    _span = float((_t[-1] - _t[0]) / 86400)
    if _span < 330:
        print(f"[365-REPAIR] {ss}: UNVERIFIABLE — 365D slice covers only {_span:.1f}d of NPZ history (< 330d); no repair until full-history NPZ", flush=True)
        return 3
    rows_exit = P._adapt_template_rows(a.template, [t for t in P.SWITCH_SHEETS if t in TABS_EXITS])
    rows_entry = P._adapt_template_rows(a.template, [t for t in P.SWITCH_SHEETS if t in TABS_ENTRY])
    cur_pair = _eval_pair(cur)
    rep = {"sym_side": ss, "progress": a.progress, "start": {"n_overrides": len(cur), "w30": cur_pair[0], "w365": cur_pair[1]}, "steps": []}
    print(f"[365-REPAIR] {ss} start 30D {cur_pair[0]['gain_pct']} ({cur_pair[0]['trades']}tr TIM {cur_pair[0].get('tim_pct')} valid {cur_pair[0]['valid']}) | 365D {cur_pair[1]['gain_pct']} ({cur_pair[1]['trades']}tr TIM {cur_pair[1].get('tim_pct')} valid {cur_pair[1]['valid']} {cur_pair[1]['invalid_reason']})", flush=True)
    ctx = mp.get_context("fork")
    t0 = time.time()
    for step in range(a.max_steps):
        if ok_pair(cur_pair):
            break
        short = int(cur_pair[0].get("trades") or 0) < FLOOR30 or int(cur_pair[1].get("trades") or 0) < FLOOR365
        raw = []
        for tab, sw, cand in rows_exit:
            ch = P._switch_overrides(sw, P._parse_opt_value(cand, defaults.get(sw)))
            if ch and not all(cur.get(k, defaults.get(k)) == v for k, v in ch.items()):
                raw.append((f"{tab}:{sw}={cand}", ch))
        if short or min(int(cur_pair[0].get("trades") or 0), int(cur_pair[1].get("trades") or 0)) < 3 * FLOOR:
            raw += more_trade_cands(cur, defaults, rows_entry, P)
        cands = [(lab, P.sanitize_overrides({**cur, **ch}, defaults)[0]) for lab, ch in raw]
        with ctx.Pool(a.workers) as pool:
            res = pool.map(_worker, cands, chunksize=4)
        best = max(res, key=lambda x: score(x[2]), default=None)
        if best is None or score(best[2]) <= score(cur_pair):
            rep["steps"].append({"step": step, "n_cands": len(cands), "result": "no improving row"})
            print(f"[365-REPAIR] {ss} step {step}: {len(cands)} candidates, none improves", flush=True)
            break
        lab, cur, cur_pair = best
        rep["steps"].append({"step": step, "n_cands": len(cands), "applied": lab, "w30": cur_pair[0], "w365": cur_pair[1]})
        print(f"[365-REPAIR] {ss} step {step} ({len(cands)} cands, {time.time()-t0:.0f}s): {lab} -> 30D {cur_pair[0]['gain_pct']:+.2f} {cur_pair[0]['trades']}tr TIM {cur_pair[0].get('tim_pct')} | 365D {cur_pair[1]['gain_pct']:+.2f} {cur_pair[1]['trades']}tr TIM {cur_pair[1].get('tim_pct')} valid {cur_pair[1]['valid']}", flush=True)
    # BOOST (USER): both windows fine but < TARGET30 trades/mo -> soften / open entries; keep a step only if both windows
    # stay valid+positive+floored and the worst-window gain does not get worse. Otherwise live with >= FLOOR30.
    rep["boost"] = []
    while ok_pair(cur_pair) and int(cur_pair[0].get("trades") or 0) < TARGET30 and len(rep["boost"]) < a.max_steps:
        cands = [(lab, P.sanitize_overrides({**cur, **ch}, defaults)[0]) for lab, ch in more_trade_cands(cur, defaults, rows_entry, P)]
        with ctx.Pool(a.workers) as pool:
            res = pool.map(_worker, cands, chunksize=4)
        t0c, worst = int(cur_pair[0].get("trades") or 0), min(_g(cur_pair[0]), _g(cur_pair[1]))
        acc = [x for x in res if ok_pair(x[2]) and int(x[2][0].get("trades") or 0) > t0c and min(_g(x[2][0]), _g(x[2][1])) >= worst]
        if not acc:
            rep["boost"].append({"n_cands": len(cands), "result": f"no softer set keeps results (stays at {t0c} trades/30D)"})
            print(f"[365-REPAIR] {ss} boost: {len(cands)} candidates, none adds trades without worse results — live at {t0c} trades/30D", flush=True)
            break
        lab, cur, cur_pair = max(acc, key=lambda x: (min(int(x[2][0].get("trades") or 0), TARGET30), min(_g(x[2][0]), _g(x[2][1]))))
        rep["boost"].append({"applied": lab, "w30": cur_pair[0], "w365": cur_pair[1]})
        print(f"[365-REPAIR] {ss} boost: {lab} -> 30D {cur_pair[0]['gain_pct']:+.2f} {cur_pair[0]['trades']}tr | 365D {cur_pair[1]['gain_pct']:+.2f} {cur_pair[1]['trades']}tr", flush=True)
    rep["final"] = {"both_positive_valid": ok_pair(cur_pair), "w30": cur_pair[0], "w365": cur_pair[1], "overrides": cur, "n_overrides": len(cur), "secs": round(time.time() - t0)}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    op = Path(a.out) / f"{ss}_365_repair.json"
    op.write_text(json.dumps(rep, indent=1, default=str))
    print(f"[365-REPAIR] {ss} DONE both_positive_valid={rep['final']['both_positive_valid']} 30D {cur_pair[0]['gain_pct']} | 365D {cur_pair[1]['gain_pct']} -> {op}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
