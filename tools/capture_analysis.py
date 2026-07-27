#!/usr/bin/env python3
"""capture_analysis.py v2 (2026-07-11) — PER-TRADE PERFECT-CAPTURE AUDIT.

USER MANDATE: "analyze every trade and why it did not get out at the top and in
at the bottom without ever missing a beat". Per key:
- PERFECT-CAPTURE CEILING = sum of favorable swing amplitudes (exceeds b&h; the
  +1000%+/+5000%+ numbers) and coverage% = strategy gain / ceiling.
- Per TRADE one audit row: entry vs preceding swing LOW (entry_lag_pct,
  bars_late, gap activity where evaluable), exit vs following swing HIGH
  (giveback_pct, exit reason verbatim, post_exit_runup_pct), verdict
  OK / BOUGHT_LATE / SOLD_EARLY / BOTH / COUNTER_SWING.
- Knob retune ranking = summed giveback pp attributed per exit-reason knob family.
Outputs: markdown artifact + machine-readable <KEY>.trades.jsonl.
[DIAGNOSTIC ONLY - n_syms=1] — feeds per-key retuning, never promotion claims.
"""
import argparse
import glob
import json
import os
from datetime import datetime, timezone

import numpy as np

SBX = "/home/niels/binance-sandbox"

KNOB_MAP = [
    ("MTF_ATR_TRAIL", "MTF_ATR_TRAIL_MULT / MTF_ATR_TRAIL_TF(_TRADIER)"),
    ("R1_DC", "R1_DC_LOW4_*_EMERGENCY / R1_NEWBORN_WINDOW_MIN / R1_TF"),
    ("R2_WT_VEL", "WT_15M_VEL_SLOW_* / WT_VEL_DECEL_RATIO / R2_TF_LIST"),
    ("WT_15M_VEL", "R2 legacy knob family"),
    ("FROZEN_STOP", "BB_FROZEN_STOP_* / DC_LOW_FROZEN_STOP_*"),
    ("BB_FROZEN", "BB_FROZEN_STOP_TF / _FIELD"),
    ("DC_LOW4_STOP", "DC_LOW4_STOP_ENABLED"),
    ("GAIN_EROSION", "GAIN_EROSION thresholds"),
    ("PARTIAL", "PARTIAL_PROFIT_LOCK_*"),
    ("QUICK_REDUCE", "QUICK_REDUCE_*"),
    ("HYBRID_STRUCT_EXIT", "hybrid structure-exit TF"),
    ("ALL_TF_AGAINST", "ALL_TF_AGAINST_CLOSE_MIN_TFS"),
    ("CLENOW_REBALANCE", "CLENOW_REBALANCE_DAYS"),
    ("WT_CROSSUNDER", "TRADIER_WT_EXIT_TFS_TRADIER (WT exit TF set)"),
    ("GR_HTF_DIRECT_EXIT", "GR_HTF exit knobs (GOLDEN_RULE_*)"),
    ("DELTA_EXIT", "wt_dc_delta DELTA/RZ exit knobs (rz_top_exit_require_confirm)"),
    ("TOP_EXIT", "wt_dc_delta TOP_EXIT (rz_top_exit_require_confirm; DEFECT-3 fix 1c8b4a83)"),
    ("STRUCTURAL_RANGE_SHIFT", "STRUCTURAL_RANGE_SHIFT_EXIT / _TF (SRS exits)"),
    ("SENTIMENT_FADE", "sentiment-fade exit knobs (market_sentiment ideal/cur)"),
    ("DYNAMIC_SCORE_COUNTER_EXIT", "DYNAMIC_SCORE counter-exit threshold"),
    ("MTF_DC_REJECT", "MTF compound DC-reject"),
    ("MTF_BB_REJECT", "MTF compound BB-reject"),
]


def knob_for(reason):
    for pat, knob in KNOB_MAP:
        if pat in reason:
            return knob
    return "unmapped — inspect code path"


def zigzag(px, pct=15.0):
    # 2026-07-11 FIX: direction must be seeded (direction=0 left both tracking
    # branches active -> ext_i followed every bar -> move never reached +/-pct ->
    # the state machine never flipped and the whole series collapsed into ONE
    # degenerate swing (ceiling wrongly == b&h in all pre-fix artifacts).
    swings = []
    if len(px) < 3:
        return swings
    piv_i = 0
    direction = 1 if px[1] >= px[0] else -1
    ext_i = 0
    for i in range(1, len(px)):
        if direction >= 0 and px[i] > px[ext_i]:
            ext_i = i
        elif direction <= 0 and px[i] < px[ext_i]:
            ext_i = i
        move = (px[i] - px[ext_i]) / px[ext_i] * 100.0
        if direction >= 0 and move <= -pct:
            swings.append((piv_i, ext_i, (px[ext_i] - px[piv_i]) / px[piv_i] * 100.0))
            piv_i, ext_i, direction = ext_i, i, -1
        elif direction <= 0 and move >= pct:
            swings.append((piv_i, ext_i, (px[ext_i] - px[piv_i]) / px[piv_i] * 100.0))
            piv_i, ext_i, direction = ext_i, i, 1
    swings.append((piv_i, ext_i, (px[ext_i] - px[piv_i]) / px[piv_i] * 100.0))
    return [s for s in swings if abs(s[2]) >= pct]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--trades-glob", required=True)
    ap.add_argument("--npz-dir", default=f"{SBX}/backtest_v8/indicators")
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--swing-pct", type=float, default=15.0)
    ap.add_argument("--out-dir", default=f"{SBX}/data/_diagnostic/capture")
    ap.add_argument("--tier", default="T0")
    ap.add_argument("--era", default="", help="provenance label printed in the artifact header")
    ap.add_argument("--override-file", default="", help="Bible 12.5: override file used for the run (verbatim + effective diff embedded)")
    a = ap.parse_args()
    symbol, side = a.key.rsplit("_", 1)
    is_long = side == "LONG"
    start_ts = datetime.strptime(a.start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    d = np.load(os.path.join(a.npz_dir, f"{symbol}.npz"), allow_pickle=True)
    ts = d["timestamps"].astype(np.int64)
    px = d["close"].astype(float)
    m = ts >= start_ts
    ts, px = ts[m], px[m]
    day = (ts // 86400).astype(np.int64)
    _, last_idx = np.unique(day[::-1], return_index=True)
    idx_daily = len(ts) - 1 - last_idx
    dts, dpx = ts[idx_daily], px[idx_daily]
    bh = (dpx[-1] - dpx[0]) / dpx[0] * 100.0
    bh_key = bh if is_long else -bh
    swings = zigzag(dpx, a.swing_pct)
    fav = [(i0, i1, p) for i0, i1, p in swings if (p > 0) == is_long]
    adv = [(i0, i1, p) for i0, i1, p in swings if (p > 0) != is_long]
    ceiling = sum(abs(p) for _, _, p in fav)  # non-compounded, comparable to summed per-trade pnl_pct
    ceiling_compound = 1.0
    for _, _, p in fav:
        ceiling_compound *= 1.0 + abs(p) / 100.0
    ceiling_compound = (ceiling_compound - 1.0) * 100.0  # ride-every-swing compounded (the user's +1000%/+5000% number)
    trades = []
    for path in glob.glob(a.trades_glob):
        if f"__{symbol}.jsonl" not in path:
            continue
        for line in open(path):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("side") == side:
                trades.append(r)
    trades.sort(key=lambda r: r.get("entry_ts", 0))
    total = sum(float(r.get("pnl_pct", 0)) for r in trades)
    # A multiple of a non-positive benchmark is undefined.  In a bull sample,
    # SHORT B&H is negative; dividing by it flips signs and can make a losing
    # short look attractive.  Report raw return and the cash/opportunity floor
    # instead, while retaining the side-specific B&H number.
    ratio = (total / bh_key) if bh_key > 0 else float("nan")
    opportunity_benchmark = max(0.0, bh_key)
    beats_opportunity = total > opportunity_benchmark
    coverage = (total / ceiling * 100.0) if ceiling else 0.0
    import sys
    sys.path.insert(0, SBX)
    import metrics_guard
    rets = [float(r.get("pnl_pct", 0)) for r in trades]
    psh = metrics_guard.pool_sharpe(rets) if len(rets) >= 2 else 0.0
    fav_pivots = [(int(dts[i0]), float(dpx[i0]), int(dts[i1]), float(dpx[i1]), p) for i0, i1, p in fav]
    audit = []
    verdicts = {"OK": 0, "BOUGHT_LATE": 0, "SOLD_EARLY": 0, "BOTH": 0, "COUNTER_SWING": 0}
    giveback_by_knob = {}
    prev_exit_ts = None
    for r in trades:
        e_ts, x_ts = int(r.get("entry_ts", 0)), int(r.get("exit_ts", 0))
        e_px, x_px = float(r.get("entry_price", 0) or 0), float(r.get("exit_price", 0) or 0)
        reason = str(r.get("exit_reason", ""))
        sw = None
        for lo_ts, lo_px, hi_ts, hi_px, p in fav_pivots:
            if lo_ts <= e_ts <= hi_ts:
                sw = (lo_ts, lo_px, hi_ts, hi_px, p)
                break
        if sw is None:
            cands = [s for s in fav_pivots if s[0] <= e_ts]
            sw = cands[-1] if cands else None
        if sw is None:
            verdicts["COUNTER_SWING"] += 1
            audit.append({"entry_ts": e_ts, "exit_ts": x_ts, "entry_price": e_px, "exit_price": x_px,
                          "pnl_pct": float(r.get("pnl_pct", 0)), "exit_reason": reason[:80],
                          "verdict": "COUNTER_SWING", "entry_lag_pct": None, "bars_late": None,
                          "giveback_pct": None, "post_exit_runup_pct": None, "gap_note": "",
                          "knob": knob_for(reason)})
            continue
        lo_ts, lo_px, hi_ts, hi_px, swing_p = sw
        in_counter = any(int(dts[i0]) <= e_ts <= int(dts[i1]) for i0, i1, _ in adv)
        if is_long:
            entry_lag = (e_px - lo_px) / lo_px * 100.0 if lo_px else 0.0
            giveback = (hi_px - x_px) / hi_px * 100.0 if hi_px and x_px else 0.0
        else:
            entry_lag = (lo_px - e_px) / lo_px * 100.0 if lo_px else 0.0
            giveback = (x_px - hi_px) / hi_px * 100.0 if hi_px and x_px else 0.0
        bars_late = int(np.searchsorted(dts, e_ts) - np.searchsorted(dts, lo_ts))
        seg = px[(ts > x_ts) & (ts <= hi_ts)]
        if len(seg) and x_px:
            runup = (float(seg.max()) - x_px) / x_px * 100.0 if is_long else (x_px - float(seg.min())) / x_px * 100.0
        else:
            runup = 0.0
        gap_note = ""
        if prev_exit_ts is not None and prev_exit_ts >= lo_ts:
            gap_note = f"prior trade exited {max(0, (e_ts - prev_exit_ts) // 86400)}d before this entry (churn gap)"
        elif bars_late > 0:
            gap_note = f"NO position for {bars_late} bars after swing low — no OPEN in trade set (gate attribution needs decision-log probe)"
        late = entry_lag > max(3.0, 0.2 * abs(swing_p))
        early = (giveback > max(3.0, 0.2 * abs(swing_p))) or runup > 5.0
        verdict = "BOTH" if late and early else "BOUGHT_LATE" if late else "SOLD_EARLY" if early else ("COUNTER_SWING" if in_counter else "OK")
        verdicts[verdict] += 1
        knob = knob_for(reason)
        if verdict in ("SOLD_EARLY", "BOTH"):
            giveback_by_knob[knob] = giveback_by_knob.get(knob, 0.0) + max(giveback, runup)
        audit.append({"entry_ts": e_ts, "exit_ts": x_ts, "entry_price": e_px, "exit_price": x_px,
                      "pnl_pct": float(r.get("pnl_pct", 0)), "exit_reason": reason[:80], "verdict": verdict,
                      "entry_lag_pct": round(entry_lag, 2), "bars_late": bars_late,
                      "giveback_pct": round(giveback, 2), "post_exit_runup_pct": round(runup, 2),
                      "swing": f"{abs(swing_p):.0f}%@{datetime.fromtimestamp(lo_ts, timezone.utc).strftime('%Y-%m-%d')}",
                      "gap_note": gap_note, "knob": knob})
        prev_exit_ts = x_ts
    os.makedirs(a.out_dir, exist_ok=True)
    name = f"{a.key}.md" if a.tier == "T0" else f"{a.key}.{a.tier}.md"
    out = os.path.join(a.out_dir, name)
    jl = os.path.join(a.out_dir, f"{a.key}.trades.jsonl" if a.tier == "T0" else f"{a.key}.{a.tier}.trades.jsonl")
    with open(jl, "w") as f:
        for row in audit:
            f.write(json.dumps(row) + "\n")
    # Machine-readable side-aware verdict summary — THE TRADE GATE source
    # (apply_gainmo_gate_and_mult.post_parity_verdict reads these).
    months = max(0.1, (datetime.now(timezone.utc).timestamp() - start_ts) / 2629800.0)
    summary = {
        "key": a.key, "tier": a.tier, "era": a.era, "ts": datetime.now(timezone.utc).timestamp(),
        "long_bh_pct": round(bh, 2), "bh_key": round(bh_key, 2),
        "cash_benchmark_pct": 0.0,
        "opportunity_benchmark_pct": round(opportunity_benchmark, 2),
        "beats_opportunity_benchmark": bool(beats_opportunity),
        "total_gain_pct": round(total, 2),
        "gain_vs_bh": round(ratio, 4) if ratio == ratio else None,
        "gain_mo": round(total / months, 4), "pool_sharpe": round(psh, 4), "trades": len(trades),
        "ceiling_compound": round(ceiling_compound, 1), "ceiling_sum": round(ceiling, 1),
        "coverage_pct": round(coverage, 3),
    }
    sys.path.insert(0, f"{SBX}/tools")
    import provenance_lib
    summary["provenance"] = provenance_lib.provenance(a.override_file, "tradier" if not symbol.endswith(("USDT", "USDC")) else "crypto", a.start, symbol)
    sname = f"{a.key}.summary.json" if a.tier == "T0" else f"{a.key}.{a.tier}.summary.json"
    with open(os.path.join(a.out_dir, sname), "w") as f:
        json.dump(summary, f, indent=1)
    with open(out, "w") as f:
        f.write(f"# CAPTURE ANALYSIS v2 — {a.key} · tier {a.tier}{' · ' + a.era if a.era else ''} [DIAGNOSTIC ONLY - n_syms=1]\n")
        f.write(f"Generated {datetime.now(timezone.utc).isoformat()} · window {a.start}→now\n")
        f.write(f"Settings (12.5): {provenance_lib.compact_settings(summary['provenance'])}\n\n")
        f.write(f"- **PERFECT-CAPTURE CEILING (compounded, ride every swing): {ceiling_compound:+.1f}%** | non-compounded swing sum: {ceiling:+.1f}% ({len(fav)} favorable swings >= {a.swing_pct}%) | b&h ({side}): {bh_key:+.1f}%\n")
        ratio_text = f"{ratio:.2f}" if ratio == ratio else "N/A (side B&H <= 0; cash floor applies)"
        f.write(f"- strategy: {total:+.1f}% | **coverage: {coverage:.2f}% of ceiling** | gain_vs_bh: {ratio_text} | cash: +0.0% | opportunity benchmark: {opportunity_benchmark:+.1f}% | beats opportunity: {beats_opportunity} | trades: {len(trades)} | pool_sharpe(per-trade): {psh:.4f}\n")
        f.write(f"- verdicts: OK={verdicts['OK']} BOUGHT_LATE={verdicts['BOUGHT_LATE']} SOLD_EARLY={verdicts['SOLD_EARLY']} BOTH={verdicts['BOTH']} COUNTER_SWING={verdicts['COUNTER_SWING']}\n\n")
        f.write("## Knob retune ranking (summed giveback pp from SOLD_EARLY/BOTH — adjust top first)\n")
        for knob, pp in sorted(giveback_by_knob.items(), key=lambda kv: -kv[1]):
            f.write(f"- {pp:8.1f}pp  {knob}\n")
        f.write("\n## Per-trade audit\n")
        f.write("| entry (UTC) | +lag% | bars_late | exit (UTC) | giveback% | runup_after% | pnl% | verdict | exit_reason | gap |\n|---|---|---|---|---|---|---|---|---|---|\n")
        for row in audit:
            e = datetime.fromtimestamp(row["entry_ts"], timezone.utc).strftime("%y-%m-%d %H:%M") if row["entry_ts"] else "?"
            x = datetime.fromtimestamp(row["exit_ts"], timezone.utc).strftime("%y-%m-%d %H:%M") if row["exit_ts"] else "?"
            f.write(f"| {e} | {row['entry_lag_pct']} | {row['bars_late']} | {x} | {row['giveback_pct']} | {row['post_exit_runup_pct']} | {row['pnl_pct']:+.2f} | {row['verdict']} | {row['exit_reason'][:44]} | {row.get('gap_note', '')[:60]} |\n")
        f.write("\n## Next step per mandate\nThe knob ranking above IS the retune order. BOUGHT_LATE / no-OPEN gaps need the decision-log probe to name the blocking gate.\n")
    ratio_text = f"{ratio:.2f}" if ratio == ratio else "N/A"
    print(f"[capture] {a.key} tier={a.tier}: ceiling_compound={ceiling_compound:+.1f}% ceiling_sum={ceiling:+.1f}% bh={bh_key:+.1f}% strat={total:+.1f}% coverage={coverage:.2f}% gain_vs_bh={ratio_text} opportunity={opportunity_benchmark:+.1f}% pool_sharpe={psh:.4f} trades={len(trades)} | OK={verdicts['OK']} LATE={verdicts['BOUGHT_LATE']} EARLY={verdicts['SOLD_EARLY']} BOTH={verdicts['BOTH']} CTR={verdicts['COUNTER_SWING']} -> {out}")


if __name__ == "__main__":
    main()
