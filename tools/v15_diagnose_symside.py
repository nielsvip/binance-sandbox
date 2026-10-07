#!/usr/bin/env python3
"""v15_diagnose_symside — "what is wrong with this sym_side?" in one command (ENCYCLOPEDIA.md §3/§6).

  python tools/v15_diagnose_symside.py AXSUSDT_LONG                 # re-evaluate the final set (needs full NPZ: S1/S2/S5)
  python tools/v15_diagnose_symside.py AXSUSDT_LONG --no-eval       # offline: manifest / progress / DIAGNOSE_REPAIR report only
  python tools/v15_diagnose_symside.py AXSUSDT_LONG --json          # machine-readable

Final set = progress `cumulative_overrides` (V15_PROGRESS_DIR or data/reports/lifecycle_pilot), else --overrides-json.
Metrics + faults use tools/v15_diagnose_repair (same thresholds as the pilot's DIAGNOSE_REPAIR phase); levers come from
the encyclopedia playbook; if the pilot wrote {progress_dir}/v15_diag_repair/{SS}.json its lever map (real Δ of every
candidate on THIS sym_side) and gaps are shown — that beats the generic playbook.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import v15_diagnose_repair as DR  # noqa: E402

# ENCYCLOPEDIA.md §3 — first levers per fault (direction encoded in the value); always confirm on THIS sym_side
PLAYBOOK = {
    "TOO_FEW_TRADES": ["HTF_DIRECTION_GATE_ENABLED=False", "WT_DIV_ENTRY_GATE_ENABLED=False", "STRENGTH_FILTER_ENABLED=False", "CT_WT_VELOCITY_GATE_ENABLED=False", "HTF_ALIGNMENT_ENABLED=False", "EMA50_15M_ENTRY_FILTER_ENABLED=False", "WT_DC_ENTRY_THRESHOLD lower", "check RSI_ENTRY_VETO_ENABLED (crypto)"],
    "FEW_TRADES": ["see TOO_FEW_TRADES — least gain-cost adders first (docs/encyclopedia/09)"],
    "TOO_MANY_TRADES": ["TARGET_DC_IMMEDIATE_REENTRY_ENABLED=False", "HARDCODED_RALLY_REENTRY_REQUIRE_WT=True", "HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN=False", "DAYTRADE_DC_TARGET_TF=1h", "WT_15M_BOUNCE_OPEN_ENABLED=False (stocks)", "ALL_TF_AGAINST_CLOSE_MIN_TFS=5"],
    "LOW_EDGE": ["remove the smallest-|Δgain| trade adders; fee drag from churn"],
    "TIM_HIGH": ["WT_LOWER_CROSS_EXIT_TF=1h|15m", "TECHNICAL_DC_STOP_TF=1h", "VEC_EXIT_SIG_IS_TRIGGER=True", "DAYTRADE_DC_TARGET_TF=15m,1h", "MI_EXIT_ENABLED=True", "MTF_ATR_TRAIL_ENABLED=True"],
    "TIM_LOW": ["DAYTRADE_DC_TARGET_TF=1h|4h", "PARTIAL_PROFIT_LOCK_ENABLED=False (crypto)", "MOMENTUM_TP_ENABLED=False", "soften entry filters (TOO_FEW_TRADES list)"],
    "DD_HIGH": ["DAYTRADE_DC_STOP_TF=15m|1h", "VIGILANCE_GUARD_ENABLED=True", "NEWBORN_LOSS_KILL_ENABLED=True", "BOTTOM_EXIT_HTF_WT_VETO_ENABLED=False", "HAIKU_WINNER_ENABLED=False", "DC_HARD_STOP_TF=4h"],
    "DD_WARN": ["see DD_HIGH — 365D fails mostly on DD (a 30D rally never needed the stop)"],
    "GAIN_NEG": ["HTF/TREND entry gates on", "disable/replace the LOSING_EXIT family", "REENTRY_ENTRY_FILTER_ENABLED=True"],
    "BELOW_BH_MATERIAL": ["slower DAYTRADE_DC_TARGET_TF", "MTF_BB_REJECT_EXIT_ENABLED=False", "HLR_TOP_EXIT_LIVE_SANCTIONED stays False unless §63 confirmation", "HLR_TOP_RECROSS_BYPASS_ENABLED=True (SELL_TOP cases)"],
    "LOW_WR": ["entry confirmation gates (HTF direction, KG, EMA_9_21)"],
    "LOSING_EXIT": ["the named exit family: disable/raise its threshold; add an earlier loss exit"],
    "LOSING_ENTRY": ["the named entry path: add its confirmation filter (HARDCODED_RALLY_REENTRY_REQUIRE_WT=True / REENTRY_ENTRY_FILTER_ENABLED=True for reentries)"],
    "INVALID": ["fix the invalidating gate first (TIM>80 → exits, DD>30 → stops, trades<10 → soften)"],
}


def _progress_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("V15_PROGRESS_DIR") or ROOT / "data" / "reports" / "lifecycle_pilot")


def load(ss: str, ov_path: str | None) -> tuple[dict, dict]:
    prog = {}
    pj = _progress_dir() / f"{ss}_v14_progress.json"
    if pj.exists():
        prog = json.loads(pj.read_text())
    if ov_path:
        d = json.loads(pathlib.Path(ov_path).read_text())
        return d.get("cumulative_overrides") or d.get("best_overrides") or d, prog
    return dict(prog.get("cumulative_overrides") or {}), prog


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sym_side")
    ap.add_argument("--overrides-json")
    ap.add_argument("--no-eval", action="store_true")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    ss = a.sym_side
    ov, prog = load(ss, a.overrides_json)
    cat = ("CRYPTO" if ss.rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD")) else "STOCKS") + "_" + ss.rsplit("_", 1)[1]
    out = {"sym_side": ss, "cat_side": cat, "n_overrides": len(ov), "progress_final_gain": prog.get("final_gain_fresh_vec"), "bh": prog.get("bh")}
    m, mix = None, {}
    if not a.no_eval:
        from tools.opt.v12_pilot import evaluate_sanitized
        res = evaluate_sanitized(ss, dict(ov), window_days=a.window_days, include_ledger=True)
        m = DR.metrics(res)
        mix = DR.ledger_mix({"ledger": [t for t in (res.get("ledger") or []) if isinstance(t, dict)]})
    else:
        diag = (prog.get("diagnose_repair") or {}).get("after")
        man = sorted((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL").glob(f"{ss}_bh*_manifest.json"), key=lambda p: p.stat().st_mtime)
        if diag:
            m = diag
        elif man:
            mm = json.loads(man[-1].read_text()).get("metrics") or {}
            m = DR.metrics({"gain_pct": mm.get("gain_pct"), "trades": mm.get("trades"), "tim_pct": mm.get("tim_pct"), "max_dd_pct": mm.get("max_dd_pct"), "valid": mm.get("valid"), "invalid_reason": mm.get("invalid_reason"), "bh_pct": mm.get("bh")})
    if m is None:
        print(json.dumps({**out, "error": "no metrics: run with full NPZ (S1/S2/S5) or after a DIAGNOSE_REPAIR / manifest exists"}, indent=1))
        return
    faults = DR.diagnose(m, mix, cat)
    out.update({"metrics": m, "faults": [{"fault": f[0], "detail": f[1], "lever_family": f[2], "first_levers": PLAYBOOK.get(f[0].split(":", 1)[0], [])} for f in faults],
                "exit_mix": mix.get("exit"), "entry_mix": mix.get("entry"), "median_bars_held": mix.get("median_bars_held")})
    rp = _progress_dir() / "v15_diag_repair" / f"{ss}.json"
    if rp.exists():
        r = json.loads(rp.read_text())
        lm = [x for x in (r.get("lever_map") or []) if x.get("d") and not x.get("blocked")]
        out["this_symside_lever_map"] = {
            "top_gain": sorted(lm, key=lambda x: -(x["d"].get("gain") or -1e9))[:12],
            "top_trade_adders": sorted(lm, key=lambda x: -(x["d"].get("trades") or 0))[:8],
            "top_tim_cutters": sorted((x for x in lm if x["d"].get("tim") is not None), key=lambda x: x["d"]["tim"])[:8],
            "top_dd_cutters": sorted((x for x in lm if x["d"].get("dd") is not None), key=lambda x: x["d"]["dd"])[:8]}
        out["repair"] = {k: r.get(k) for k in ("accepted", "accept_reason", "changes", "gaps", "origin_365")}
    if a.json:
        print(json.dumps(out, indent=1, default=str))
        return
    print(f"== {ss} ({cat})  overrides={len(ov)}  progress_final={out['progress_final_gain']}  bh={out['bh']}")
    print(f"   gain={m['gain']} bh={m['bh']} trades={m['trades']} TIM={m['tim']} DD={m['dd']} WR={m['wr']} valid={m['valid']} {m['reason']}")
    if mix.get("median_bars_held") is not None:
        print(f"   median hold {mix['median_bars_held']} bars; exit mix: " + ", ".join(f"{k} {v['n']} ({v['mean_pnl_pct']:+.2f}%)" for k, v in list((mix.get('exit') or {}).items())[:6]))
        print("   entry mix: " + ", ".join(f"{k} {v['n']} ({v['mean_pnl_pct']:+.2f}%)" for k, v in list((mix.get('entry') or {}).items())[:6]))
    if not faults:
        print("   no faults by the §3 thresholds")
    for f in out["faults"]:
        print(f" - {f['fault']}: {f['detail']}  -> {f['lever_family']}")
        for lv in f["first_levers"][:6]:
            print(f"      · {lv}")
    if "this_symside_lever_map" in out:
        print("   THIS sym_side (DIAGNOSE_REPAIR lever map, real Δ vs final set):")
        for x in out["this_symside_lever_map"]["top_gain"][:8]:
            print(f"      {x['switch']}={x['cand']}  Δgain {x['d'].get('gain')}  Δtrades {x['d'].get('trades')}  ΔTIM {x['d'].get('tim')}  ΔDD {x['d'].get('dd')}")
        for g in (out["repair"].get("gaps") or []):
            print(f"   GAP {g['fault']}: {g['status']} {g.get('best') or g.get('note')}")


if __name__ == "__main__":
    main()
