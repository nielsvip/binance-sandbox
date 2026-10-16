#!/usr/bin/env python3
"""v15_reentry_probe — prove, switch by switch, what is eating the mandatory reentry on a sym_side.

USER 2026-10-16 (BATUSDT_LONG: BH +174.18%, board +47.92%, TIM 32.9%): *"these are TERRIBLE NUMBERS that prove the
mandatory reentry is NOT being respected ... rerun and make sure we can NEVER skip reentry (testing all applicable
filters in yellow boxes so we can never enter against the trend)"*.

The board set says `REENTRY_MANDATORY=True` and `OBLIGATORY_REENTRY_ENABLED=True`, yet the position is flat for
days at a time inside a +174% rally. "Mandatory" is gated by a stack of vetoes, and two of them were promoted AWAY
from the engine default in the blocking direction:

    REENTRY_RALLY_K15M_MAX          engine default 30.0   board 0.15   (stoch K is 0-100; <=0.15 never fires)
    REENTRY_APPLY_ENTRY_GATES_...   engine default False  board True   (every reentry must clear the entry gates)

This measures each veto in isolation and in combination on the REAL engine (`lifecycle_pilot.evaluate_month`, the
same ledger path the chart is drawn from), then measures the trend filters on top, so reentry can be forced open
WITHOUT letting entries happen against the trend. Nothing is promoted here — it prints evidence.

USAGE (S1 for crypto, s2 for stocks)
    .venv/bin/python -u tools/v15_reentry_probe.py --sym-side BATUSDT_LONG --overrides-json /tmp/BATUSDT_LONG_live_overrides.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_NPZ_CACHE", "32")

# Every switch that can veto a reentry, with the value that OPENS it. Engine defaults are quoted where the board
# set was found deviating in the blocking direction.
VETOES = [
    ("REENTRY_RALLY_K15M_MAX", 30.0, "engine default 30.0 — K is 0-100, a board value like 0.15 never fires"),
    ("REENTRY_APPLY_ENTRY_GATES_ENABLED", False, "engine default False — True makes every reentry clear the entry gates"),
    ("MANDATORY_REENTRY_WT_FILTER_ENABLED", False, "WT confirmation veto on the price-cross reentry"),
    ("REENTRY_CONFIRMATION_GATES_ENABLED", False, "confirmation gates veto"),
    ("GUARANTEED_REENTRY_STRICT_CONFIRMATION", False, "strict confirmation veto"),
    ("REENTRY_STOCH_K_MAX_LONG", 100.0, "blocks longs when momentum is strong = refuses trend continuation"),
    ("REENTRY_PULLBACK_GR_SCORE_MIN", 0, "GR score floor"),
    ("REENTRY_CHURN_GUARD_ENABLED", False, "churn guard veto"),
    ("REENTRY_COOLDOWN_S", 0.0, "post-exit cooldown"),
    ("REENTRY_MIN_GAP_MINUTES", 0.0, "minimum gap between reentries"),
]

# Trend filters — the yellow-box guards that must keep us OUT of counter-trend entries once reentry is forced open.
TREND_FILTERS = [
    ({"EMA50_15M_ENTRY_FILTER_ENABLED": True}, "EMA50 15m entry filter"),
    ({"HTF_GATE_SIGNALS_SMA200D": True}, "SMA200 D gate"),
    ({"MOM3_FILTER_TF": "4h"}, "MOM3 4h"),
    ({"MOM3_FILTER_TF": "1h"}, "MOM3 1h"),
    ({"ADX_REGIME_FILTER_ENABLED": True}, "ADX regime filter"),
    ({"EMA_9_21_FILTER_ENABLED": True}, "EMA 9/21 filter"),
    ({"COUNTER_TREND_SMA200_BYPASS_ENABLED": False}, "no counter-trend SMA200 bypass"),
]


def ev(symside, ov, window_days):
    from tools.opt.lifecycle_pilot import evaluate_month

    t0 = time.time()
    r = evaluate_month(symside, dict(ov), include_ledger=True, window_days=window_days) or {}
    led = r.get("execution_ledger") or r.get("ledger") or []
    try:
        from metrics_guard import realised_closes

        closes = realised_closes(led)
    except Exception:
        closes = [t for t in led if isinstance(t, dict) and t.get("pnl_dollars") is not None]
    return {
        "gain_pct": round(float(r.get("gain_pct") or 0.0), 4),
        "trades": len(closes),
        "tim_pct": round(float(r.get("tim_pct") or 0.0), 2),
        "max_dd_pct": round(float(r.get("max_dd_pct") or 0.0), 2),
        "bh_pct": round(float(r.get("bh_pct") or 0.0), 2),
        "wr_pct": round(float(r.get("wr_pct") or 0.0), 1),
        "valid": bool(r.get("valid")),
        "secs": round(time.time() - t0, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--overrides-json", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    ss = a.sym_side
    base_ov = json.loads(Path(a.overrides_json).read_text())
    rows = []

    def run(label, ov, note=""):
        m = ev(ss, ov, a.window_days)
        rows.append({"label": label, "note": note, **m})
        print(f"{label:52s} gain {m['gain_pct']:+8.2f}%  tr {m['trades']:4d}  TIM {m['tim_pct']:5.1f}%  DD {m['max_dd_pct']:5.2f}  WR {m['wr_pct']:5.1f}  bh {m['bh_pct']:+7.2f}  {m['secs']}s", flush=True)
        return m

    base = run("BASE (the published board set)", base_ov)
    print(f"  [base] {ss}: board carries " + ", ".join(f"{k}={base_ov.get(k)!r}" for k, _v, _n in VETOES if k in base_ov))
    print()
    print("--- each veto OPENED on its own ---", flush=True)
    singles = []
    for k, open_val, note in VETOES:
        if k not in base_ov or base_ov.get(k) == open_val:
            continue
        m = run(f"  open {k}={open_val!r}", dict(base_ov, **{k: open_val}), note)
        singles.append((k, open_val, m["gain_pct"] - base["gain_pct"], m))
    print()
    print("--- ALL vetoes opened together (reentry can never be skipped) ---", flush=True)
    allopen = dict(base_ov)
    for k, v, _n in VETOES:
        if k in base_ov:
            allopen[k] = v
    forced = run("  ALL REENTRY VETOES OPEN", allopen)
    print()
    print("--- trend filters ON TOP of forced reentry (never enter against the trend) ---", flush=True)
    for mv, note in TREND_FILTERS:
        if not all(k in base_ov for k in mv):
            continue
        if all(base_ov.get(k) == v for k, v in mv.items()):
            continue
        run("  forced + " + note, dict(allopen, **mv), note)
    out = Path(a.out) if a.out else ROOT / "data" / "reports" / f"v15_reentry_probe_{ss}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"sym_side": ss, "window_days": a.window_days, "base": base, "forced": forced, "singles": [{"switch": k, "open_value": v, "delta": round(d, 4)} for k, v, d, _m in singles], "rows": rows}, indent=1, default=str))
    print()
    print(f"[probe] base {base['gain_pct']:+.2f}% / TIM {base['tim_pct']}%  ->  all-vetoes-open {forced['gain_pct']:+.2f}% / TIM {forced['tim_pct']}%  (bh {base['bh_pct']:+.2f}%)")
    for k, v, d, _m in sorted(singles, key=lambda x: -x[2])[:6]:
        print(f"[probe] opening {k}={v!r} alone: {d:+.2f}pp")
    print(f"[probe] evidence {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
