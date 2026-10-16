#!/usr/bin/env python3
"""v15_reentry_gate_discovery — let REENTRY find its OWN gates by adding and testing yellow boxes.

USER 2026-10-16: *"REENTRY_APPLY_ENTRY_GATES <- NOT BY FKN ENTRY GATES BUT It needs to find its own gates BY
ADDING AND TESTING YELLOW BOXES"*.

Context: `REENTRY_APPLY_ENTRY_GATES_ENABLED` and `REENTRY_RALLY_K15M_MAX` are now DEAD everywhere (crypto + stocks)
— the first filtered reentry with the ENTRY gate stack (which makes it an entry, not a reentry), the second was a
stoch_k gate that never admitted or refused a single reentry, only resized. With both gone reentry is unguarded
(BATUSDT_LONG: 252 trades, 109 of them one-bar, WR 42.5%), so it has to discover its own admission filters.

METHOD (BIBLE §14: greedy, positive-delta-only, joint re-measure — nothing fabricated)
  1. baseline  = the sym_side's current set, measured on the ledger path (`lifecycle_pilot.evaluate_month`)
  2. singles   = every yellow cell of REENTRY_WINDOWED + REENTRY_ADAPTIVE (216 cells / 51 families) applied one
                 at a time on top of the baseline; delta = gain - baseline_gain
  3. stack     = the positives in descending delta order, each kept ONLY if the JOINT re-measure improves the
                 running cumulative (a single's delta never counts as proof of the combination)
  4. verify    = the final stacked set is re-measured once more; the accumulation drift is recorded, not assumed
  A candidate that drives trades to zero, or returns invalid, is refused regardless of its gain.

Read-only: prints and writes a report, promotes nothing to any board or to live.

USAGE (S1 for crypto, s2 for stocks)
    .venv/bin/python -u tools/v15_reentry_gate_discovery.py --sym-side BATUSDT_LONG \
        --overrides-json /tmp/BATUSDT_LONG_live_overrides.json --yellows-json /tmp/reentry_yellows.json
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


def coerce(v: str):
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        f = float(s)
        return int(f) if f.is_integer() and "." not in s else f
    except ValueError:
        return s


def ev(symside, ov, window_days):
    from metrics_guard import realised_closes
    from tools.opt.lifecycle_pilot import evaluate_month

    r = evaluate_month(symside, dict(ov), include_ledger=True, window_days=window_days) or {}
    closes = realised_closes(r.get("execution_ledger") or r.get("ledger") or [])
    return {
        "gain_pct": float(r.get("gain_pct") or 0.0),
        "trades": len(closes),
        "one_bar": sum(1 for t in closes if (t.get("bars_held") or 0) <= 1),
        "tim_pct": float(r.get("tim_pct") or 0.0),
        "max_dd_pct": float(r.get("max_dd_pct") or 0.0),
        "wr_pct": float(r.get("wr_pct") or 0.0),
        "bh_pct": float(r.get("bh_pct") or 0.0),
        "valid": bool(r.get("valid")),
    }


def usable(m, min_trades):
    return bool(m.get("valid")) and int(m.get("trades") or 0) >= min_trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side", required=True)
    ap.add_argument("--overrides-json", required=True)
    ap.add_argument("--yellows-json", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--min-trades", type=int, default=10, help="a gate that starves the board is refused whatever its gain")
    ap.add_argument("--min-delta", type=float, default=0.01)
    ap.add_argument("--max-stack", type=int, default=12)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    ss = a.sym_side
    base_ov = json.loads(Path(a.overrides_json).read_text())
    yellows = json.loads(Path(a.yellows_json).read_text())
    t0 = time.time()
    base = ev(ss, base_ov, a.window_days)
    print(f"BASELINE  gain {base['gain_pct']:+8.2f}%  tr {base['trades']:4d}  1bar {base['one_bar']:4d}  TIM {base['tim_pct']:5.1f}%  WR {base['wr_pct']:5.1f}  DD {base['max_dd_pct']:5.2f}  (bh {base['bh_pct']:+.2f}%)", flush=True)
    print(f"testing {len(yellows)} reentry yellow cells one at a time...\n", flush=True)
    singles = []
    for i, cell in enumerate(yellows, 1):
        if "=" not in cell:
            continue
        k, v = cell.split("=", 1)
        k, val = k.strip(), coerce(v)
        if base_ov.get(k) == val:
            continue
        m = ev(ss, dict(base_ov, **{k: val}), a.window_days)
        d = m["gain_pct"] - base["gain_pct"]
        singles.append({"cell": cell, "switch": k, "value": val, "delta": round(d, 4), **m})
        if d >= a.min_delta and usable(m, a.min_trades):
            print(f"  [{i:3d}/{len(yellows)}] +{d:7.3f}pp  {cell:48s} tr {m['trades']:4d} 1bar {m['one_bar']:4d} WR {m['wr_pct']:5.1f}", flush=True)
    pos = sorted([s for s in singles if s["delta"] >= a.min_delta and usable(s, a.min_trades)], key=lambda s: -s["delta"])
    print(f"\n{len(pos)} of {len(singles)} yellow cells improve the baseline. Greedy joint stacking:\n", flush=True)
    cur, cum, kept = dict(base_ov), base["gain_pct"], []
    for s in pos[: a.max_stack * 4]:
        if len(kept) >= a.max_stack:
            break
        if cur.get(s["switch"]) == s["value"]:
            continue
        cand = dict(cur, **{s["switch"]: s["value"]})
        m = ev(ss, cand, a.window_days)
        if m["gain_pct"] > cum + a.min_delta and usable(m, a.min_trades):
            print(f"  KEEP  {s['cell']:48s} {cum:+7.2f} -> {m['gain_pct']:+7.2f}  tr {m['trades']:4d} 1bar {m['one_bar']:4d} WR {m['wr_pct']:5.1f}", flush=True)
            cur, cum = cand, m["gain_pct"]
            kept.append({"cell": s["cell"], "single_delta": s["delta"], "joint_gain_after": round(m["gain_pct"], 4), "trades": m["trades"], "one_bar": m["one_bar"], "wr_pct": m["wr_pct"]})
        else:
            print(f"  drop  {s['cell']:48s} joint {m['gain_pct']:+7.2f} (single said {s['delta']:+.2f}pp)", flush=True)
    final = ev(ss, cur, a.window_days)
    expect = base["gain_pct"] + sum(k["single_delta"] for k in kept)
    print()
    print(f"FINAL     gain {final['gain_pct']:+8.2f}%  tr {final['trades']:4d}  1bar {final['one_bar']:4d}  TIM {final['tim_pct']:5.1f}%  WR {final['wr_pct']:5.1f}  DD {final['max_dd_pct']:5.2f}")
    print(f"          baseline {base['gain_pct']:+.2f}%  ->  {final['gain_pct']:+.2f}%  ({final['gain_pct'] - base['gain_pct']:+.2f}pp from {len(kept)} reentry gate(s))")
    print(f"          sum-of-singles would have predicted {expect:+.2f}% — drift {final['gain_pct'] - expect:+.2f}pp (recorded, never assumed)")
    out = Path(a.out) if a.out else ROOT / "data" / "reports" / f"v15_reentry_gates_{ss}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"sym_side": ss, "window_days": a.window_days, "baseline": base, "final": final, "kept": kept, "discovered_set": {k["cell"].split("=")[0]: coerce(k["cell"].split("=", 1)[1]) for k in kept}, "singles": sorted(singles, key=lambda s: -s["delta"])[:60], "secs": round(time.time() - t0, 1)}, indent=1, default=str))
    print(f"          evidence {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
