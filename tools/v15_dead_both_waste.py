#!/usr/bin/env python3
"""Measure the eval cost that bucket A_dead_both switches consume per board.

Read-only. Parses data/reports/lifecycle_pilot/*_v14_progress.json `done` maps,
attributes every recorded evaluation to its switch, and reports how much of the
real (non-skipped) eval volume belongs to the confirmed-dead-both-sides set in
data/reports/vec_unwired_classification.json.

Usage: python3 tools/v15_dead_both_waste.py [--n 12] [--secs-per-eval 0.07]
"""
import argparse
import collections
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLS = ROOT / "data" / "reports" / "vec_unwired_classification.json"
PROG = ROOT / "data" / "reports" / "lifecycle_pilot"
SKIP_PREFIX = ("CARRIED_DEAD", "RUNNING_IDENTITY", "ZERO_FORMULA_ROW", "NO_CANDIDATE", "NOT_IN_CONFIG", "ABLATION_NEVER_ON", "LIVE_DEAD_KEY", "MAXAUG_NEVER_ZERO", "ZERO_SKIPPED", "PRIOR_SKIP", "COARSE_DEFERRED")


# WT_DC_DETAILED_TF is a v15_pilot COMPOSITE switch (v15_pilot.py:2111): it has no config field of its own but
# expands into WT_DC_ENABLED / WT_DC_DETAILED_SCORER_ENABLED / WT_DC_TF_ENTRY / WT_DC_DC_TF and that expansion DOES
# move the ledger (BTCUSDC_LONG 514 -> 476 trades). It is NOT dead and its evals are NOT waste.
COMPOSITE_RECLASSIFIED = frozenset(("WT_DC_DETAILED_TF",))


def dead_both_set(keep_composites=False):
    d = json.loads(CLS.read_text())
    out = set()
    for entry in d.get("A_dead_both") or []:
        out.add(str(entry[0] if isinstance(entry, (list, tuple)) else entry).strip())
    if not keep_composites:
        out -= set(COMPOSITE_RECLASSIFIED)
    return out


def switch_of(key: str) -> str:
    body = key.split("!", 1)[-1]
    body = body.split(":", 1)[-1] if ":" in body else body
    return body.split("=", 1)[0].strip()


def evals_of(rec: dict) -> int:
    """Number of v12_quick_engine evaluations this record actually paid for."""
    reason = str(rec.get("reason") or "")
    if any(reason.startswith(p) for p in SKIP_PREFIX):
        return 0
    n = 0
    if rec.get("vec_gain") is not None or rec.get("naked_delta") is not None:
        n += 1
    ys = rec.get("yellows") or {}
    n += len(ys)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--secs-per-eval", type=float, default=0.07)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "dead_both_waste.json"))
    ap.add_argument("--keep-composites", action="store_true", help="count the reclassified composite switch as waste too (it is not)")
    a = ap.parse_args()
    dead = dead_both_set(a.keep_composites)
    files = sorted(PROG.glob("*_v14_progress.json"), key=lambda p: p.stat().st_mtime, reverse=True)[: a.n]
    rows = []
    agg = collections.Counter()
    reason_mix = collections.Counter()
    per_switch = collections.Counter()
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception as e:
            print(f"[skip] {f.name}: {e}")
            continue
        done = d.get("done") or {}
        tot_rows = len(done)
        dead_rows = 0
        tot_evals = 0
        dead_evals = 0
        dead_rows_calculated = 0
        for k, v in done.items():
            if not isinstance(v, dict):
                continue
            sw = switch_of(k)
            e = evals_of(v)
            tot_evals += e
            if sw in dead:
                dead_rows += 1
                dead_evals += e
                per_switch[sw] += e
                if e:
                    dead_rows_calculated += 1
                    reason_mix[(str(v.get("reason") or "<none>").split(":", 1)[0])] += 1
        rows.append(
            {
                "sym_side": d.get("symside") or f.name,
                "rows_total": tot_rows,
                "rows_dead_both": dead_rows,
                "rows_dead_both_calculated": dead_rows_calculated,
                "evals_total": tot_evals,
                "evals_dead_both": dead_evals,
                "pct_rows_dead": round(100.0 * dead_rows / tot_rows, 2) if tot_rows else 0.0,
                "pct_evals_dead": round(100.0 * dead_evals / tot_evals, 2) if tot_evals else 0.0,
                "secs_dead": round(dead_evals * a.secs_per_eval, 1),
            }
        )
        agg["rows_total"] += tot_rows
        agg["rows_dead_both"] += dead_rows
        agg["rows_dead_both_calculated"] += dead_rows_calculated
        agg["evals_total"] += tot_evals
        agg["evals_dead_both"] += dead_evals
    out = {
        "files": len(rows),
        "secs_per_eval": a.secs_per_eval,
        "dead_both_names": len(dead),
        "totals": dict(agg),
        "pct_rows_dead": round(100.0 * agg["rows_dead_both"] / max(1, agg["rows_total"]), 2),
        "pct_evals_dead": round(100.0 * agg["evals_dead_both"] / max(1, agg["evals_total"]), 2),
        "mean_secs_dead_per_board": round(agg["evals_dead_both"] * a.secs_per_eval / max(1, len(rows)), 1),
        "reason_mix_calculated": dict(reason_mix.most_common()),
        "top_switches_by_evals": dict(per_switch.most_common(25)),
        "boards": rows,
    }
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items() if k != "boards"}, indent=1))
    for r in rows:
        print(f"  {r['sym_side']:<22} rows {r['rows_dead_both']:>5}/{r['rows_total']:<5} ({r['pct_rows_dead']:>5}%)  evals {r['evals_dead_both']:>6}/{r['evals_total']:<7} ({r['pct_evals_dead']:>5}%)  {r['secs_dead']:>8}s")
    print(f"[out] {a.out}")


if __name__ == "__main__":
    main()
