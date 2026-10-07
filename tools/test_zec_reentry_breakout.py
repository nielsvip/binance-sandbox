#!/usr/bin/env python3
"""test ZEC reentry+breakout — first batch to add to old system, see if >BH.
Isolated, never overwrites, 1yr 365d + 1mo proof 30d from same calc, honest metrics.
"""
import sys, json, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.opt.v12_pilot import evaluate_sanitized

# Top reentry+breakout from TEMPLATE_V2 P0/P1
BATCH = {
    # REENTRY
    "GUARANTEED_REENTRY_ENABLED": True,
    "BOUNCE_REENTRY_ENABLED": True,
    "BREAKOUT_LEASH_REENTRY_MULT": 1.5,
    "HLR_REENTRY_MULT": 1.5,
    "MTS_GATE_ENABLED": True,
    # BREAKOUT
    "DC_BREAKOUT_TF": "D",
    "DC_BREAKOUT_SCORE": 40,
    "BB_BREAKOUT_SCORE": 40,
    "DELTA_GATE_BB_SQUEEZE": True,
    "BTC_BREAKOUT_ENTRY_ENABLED": True,
}

def test(sym="ZECUSDC", side="LONG"):
    symside=f"{sym}_{side}"
    j=json.load(open(ROOT/"data/hourly_reconfig/per_sym_active_config.json"))
    base_ov=j.get(symside,{}).get("overrides",{})
    print(f"BASE {symside} ov {len(base_ov)}", flush=True)
    for wd, label in [(365,"1yr"), (30,"1mo")]:
        r0=evaluate_sanitized(symside, base_ov, window_days=wd)
        print(f"BASE {label} {wd}d trades={r0.get('trades')} gain={r0.get('gain_pct',0):.2f} bh={r0.get('bh_pct_window',0):.2f} delta={r0.get('gain_pct',0)-r0.get('bh_pct_window',0):.2f} ps={r0.get('pool_sharpe',0):.3f} valid={r0.get('valid')}", flush=True)
    # batch
    ov_batch=dict(base_ov, **BATCH)
    for wd, label in [(365,"1yr"), (30,"1mo")]:
        r=evaluate_sanitized(symside, ov_batch, window_days=wd)
        r0=evaluate_sanitized(symside, base_ov, window_days=wd)
        delta=r.get('gain_pct',0)-r0.get('gain_pct',0)
        print(f"BATCH {label} trades={r.get('trades')} gain={r.get('gain_pct',0):.2f} bh={r.get('bh_pct_window',0):.2f} delta_vs_base={delta:+.2f} vs_bh={r.get('gain_pct',0)-r.get('bh_pct_window',0):.2f} ps={r.get('pool_sharpe',0):.3f} {'BEATS BH!' if r.get('gain_pct',0)>r.get('bh_pct_window',0) else ''}", flush=True)
    # individual deltas for 1yr to see which move
    r0=evaluate_sanitized(symside, base_ov, window_days=365)
    for k,v in BATCH.items():
        ov=dict(base_ov, **{k:v})
        r=evaluate_sanitized(symside, ov, window_days=365)
        delta=r.get('gain_pct',0)-r0.get('gain_pct',0)
        print(f"  {k}={v} delta {delta:+.4f} trades {r.get('trades')} gain {r.get('gain_pct',0):.2f} bh {r.get('bh_pct_window',0):.2f}", flush=True)

if __name__=="__main__":
    test()
