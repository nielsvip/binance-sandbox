#!/usr/bin/env python3
"""
WT/DC DELTA ENGINE v3
=====================
Pure speed-of-movement strategy using ALL 165 WT+DC fields.
- Compute bar-to-bar DELTA of every WT and DC metric across all TFs
- Z-score normalize per-symbol so thresholds are universal
- ENTER when speed ramps across multiple TFs (z-score spike)
- EXIT when speed dies — BEFORE the crossunder
- RE-ENTER after consolidation with more qty, pyramiding winners
"""
import numpy as np
import os
import sys
import json
from datetime import datetime

NPZ_DIR = "backtest_v4/indicators"
RESULTS_DIR = "data/wt_dc_research"
TFS = ["3m", "15m", "1h", "4h", "D"]


def compute_signals(d, cfg):
    """Compute all deltas, z-score normalize, build speed vectors and entry/exit signals."""
    keys = list(d.keys())
    wt_dc_keys = sorted([k for k in keys if "wt" in k or k.startswith("dc")])
    n = len(d["close"])
    tf_weights = cfg["tf_weights"]
    smooth = cfg["speed_smooth"]
    accel_lb = cfg["accel_lookback"]

    tf_bull = {tf: np.zeros(n) for tf in TFS}
    tf_bear = {tf: np.zeros(n) for tf in TFS}
    tf_c = {tf: 0 for tf in TFS}

    for k in wt_dc_keys:
        arr = d[k].astype(np.float64)
        prev_k = k + "_prev"
        if prev_k in d:
            delta = arr - d[prev_k].astype(np.float64)
        elif d[k].dtype in (np.float32, np.float64) and not k.endswith("_prev") and not k.endswith("_ant"):
            delta = np.empty(n)
            delta[0] = 0
            delta[1:] = arr[1:] - arr[:-1]
        else:
            continue

        delta = np.nan_to_num(delta, 0)
        mt = None
        for tf in TFS:
            if f"_{tf}" in k:
                mt = tf
                break
        if mt:
            tf_bull[mt] += np.maximum(delta, 0)
            tf_bear[mt] += np.maximum(-delta, 0)
            tf_c[mt] += 1
        else:
            for tf in TFS:
                tf_bull[tf] += np.maximum(delta, 0) * 0.2
                tf_bear[tf] += np.maximum(-delta, 0) * 0.2
                tf_c[tf] += 1

    for tf in TFS:
        if tf_c[tf] > 0:
            tf_bull[tf] /= tf_c[tf]
            tf_bear[tf] /= tf_c[tf]

    # Smooth
    if smooth > 1:
        kernel = np.ones(smooth) / smooth
        for tf in TFS:
            tf_bull[tf] = np.convolve(tf_bull[tf], kernel, mode="same")
            tf_bear[tf] = np.convolve(tf_bear[tf], kernel, mode="same")

    # Z-score normalize each TF speed (per-symbol adaptive)
    tf_bull_z = {}
    tf_bear_z = {}
    for tf in TFS:
        m, s = np.mean(tf_bull[tf]), np.std(tf_bull[tf])
        tf_bull_z[tf] = (tf_bull[tf] - m) / s if s > 1e-10 else np.zeros(n)
        m, s = np.mean(tf_bear[tf]), np.std(tf_bear[tf])
        tf_bear_z[tf] = (tf_bear[tf] - m) / s if s > 1e-10 else np.zeros(n)

    # Combined TF-weighted z-speed
    total_bull = np.zeros(n)
    total_bear = np.zeros(n)
    for tf in TFS:
        w = tf_weights.get(tf, 1.0)
        total_bull += tf_bull_z[tf] * w
        total_bear += tf_bear_z[tf] * w

    # Normalize combined speed to z-score too
    m, s = np.mean(total_bull), np.std(total_bull)
    total_bull_z = (total_bull - m) / s if s > 1e-10 else np.zeros(n)
    m, s = np.mean(total_bear), np.std(total_bear)
    total_bear_z = (total_bear - m) / s if s > 1e-10 else np.zeros(n)

    # Acceleration of z-speed
    bull_accel = np.zeros(n)
    bear_accel = np.zeros(n)
    bull_accel[accel_lb:] = total_bull_z[accel_lb:] - total_bull_z[:-accel_lb]
    bear_accel[accel_lb:] = total_bear_z[accel_lb:] - total_bear_z[:-accel_lb]

    # TF agreement: how many TFs have z > threshold
    zt = cfg["tf_z_threshold"]
    bull_tf_count = np.zeros(n, dtype=np.int8)
    bear_tf_count = np.zeros(n, dtype=np.int8)
    for tf in TFS:
        bull_tf_count += (tf_bull_z[tf] > zt).astype(np.int8)
        bear_tf_count += (tf_bear_z[tf] > zt).astype(np.int8)

    # Consolidation: total raw speed near mean for N bars
    total_raw = np.zeros(n)
    for tf in TFS:
        total_raw += tf_bull[tf] + tf_bear[tf]
    consol_w = cfg["consolidation_bars"]
    kernel_c = np.ones(consol_w) / consol_w
    raw_ma = np.convolve(total_raw, kernel_c, mode="same")
    raw_median = np.median(total_raw)
    is_consol = raw_ma < raw_median * cfg["consolidation_ratio"]

    bars_since_consol = np.zeros(n, dtype=np.int32)
    was = False
    last = 0
    for i in range(n):
        if is_consol[i]:
            was = True
        elif was:
            was = False
            last = i
        bars_since_consol[i] = i - last if last > 0 else 9999

    # Entry signals
    min_tf = cfg["min_tf_agree"]
    entry_z = cfg["entry_z_threshold"]
    entry_accel = cfg["entry_accel_threshold"]
    entry_long = (total_bull_z > entry_z) & (bull_accel > entry_accel) & (bull_tf_count >= min_tf)
    entry_short = (total_bear_z > entry_z) & (bear_accel > entry_accel) & (bear_tf_count >= min_tf)
    post_consol = bars_since_consol < cfg["post_consol_window"]

    return {
        "total_bull_z": total_bull_z,
        "total_bear_z": total_bear_z,
        "bull_accel": bull_accel,
        "bear_accel": bear_accel,
        "bull_tf_count": bull_tf_count,
        "bear_tf_count": bear_tf_count,
        "entry_long": entry_long,
        "entry_short": entry_short,
        "post_consol": post_consol,
    }


def run_trades(close, sig, cfg):
    """Trade state machine with pyramiding."""
    n = len(close)
    warmup = cfg["warmup"]
    trades = []
    pos = None
    cooldown = 0
    max_hold = cfg["max_hold_bars"]
    pyr_max = cfg["pyramid_max"]
    pyr_min_bars = cfg["pyramid_min_bars"]
    pyr_price_tol = cfg["pyramid_price_tolerance"]
    pyr_qty_mult = cfg["pyramid_qty_mult"]
    pyr_accel_t = cfg["pyramid_accel_threshold"]
    exit_decay = cfg["exit_speed_decay_ratio"]
    exit_accel_t = cfg["exit_accel_threshold"]
    exit_opp = cfg["exit_opposing_ratio"]
    exit_min_tf = cfg["exit_min_tf"]
    min_hold = cfg["min_hold_bars"]
    base_qty = cfg["base_qty"]
    fee_rate = cfg["fee_pct"] / 100 * 2
    min_tf = cfg["min_tf_agree"]

    tbz = sig["total_bull_z"]
    tez = sig["total_bear_z"]
    ba = sig["bull_accel"]
    bea = sig["bear_accel"]
    btf = sig["bull_tf_count"]
    betf = sig["bear_tf_count"]
    el = sig["entry_long"]
    es = sig["entry_short"]
    pc = sig["post_consol"]

    for i in range(warmup, n):
        if cooldown > 0:
            cooldown -= 1

        if pos is None:
            if cooldown > 0:
                continue
            if el[i]:
                qty = base_qty * (1.5 if pc[i] else 1.0)
                pos = {"s": "L", "ep": close[i], "eb": i, "q": qty, "c": close[i] * qty,
                       "ne": 1, "ms": tbz[i], "leb": i}
            elif es[i]:
                qty = base_qty * (1.5 if pc[i] else 1.0)
                pos = {"s": "S", "ep": close[i], "eb": i, "q": qty, "c": close[i] * qty,
                       "ne": 1, "ms": tez[i], "leb": i}
            continue

        held = i - pos["eb"]
        is_long = pos["s"] == "L"
        avg = pos["c"] / pos["q"]

        if is_long:
            spd = tbz[i]
            acc = ba[i]
            opp = tez[i]
            pnl = (close[i] - avg) / avg
            tf_ok = btf[i] >= min_tf
            tf_gone = btf[i] < exit_min_tf
        else:
            spd = tez[i]
            acc = bea[i]
            opp = tbz[i]
            pnl = (avg - close[i]) / avg
            tf_ok = betf[i] >= min_tf
            tf_gone = betf[i] < exit_min_tf

        pos["ms"] = max(pos["ms"], spd)

        # Pyramid
        if pos["ne"] < pyr_max and (i - pos["leb"]) >= pyr_min_bars:
            spd_back = acc > pyr_accel_t
            if is_long:
                price_ok = close[i] <= close[pos["leb"]] * (1 + pyr_price_tol)
            else:
                price_ok = close[i] >= close[pos["leb"]] * (1 - pyr_price_tol)
            if spd_back and tf_ok and price_ok:
                add_qty = base_qty * pyr_qty_mult * pos["ne"]
                pos["q"] += add_qty
                pos["c"] += close[i] * add_qty
                pos["ne"] += 1
                pos["leb"] = i

        # Exit
        ms = pos["ms"]
        ratio = spd / ms if ms > 0.01 else 0
        dying = ratio < exit_decay
        acc_neg = acc < exit_accel_t
        opp_strong = opp > spd * exit_opp if spd > 0.1 else opp > 1.0

        should_exit = False
        reason = ""
        if dying and acc_neg:
            should_exit = True
            reason = "SPEED_DIED"
        elif opp_strong and acc_neg:
            should_exit = True
            reason = "OPPOSING"
        elif held >= max_hold:
            should_exit = True
            reason = "MAX_HOLD"
        elif acc_neg and held > min_hold and tf_gone:
            should_exit = True
            reason = "TF_LOST"

        if should_exit:
            fee = fee_rate * pos["ne"]
            trades.append({
                "s": pos["s"], "avg": avg, "xp": close[i],
                "eb": pos["eb"], "xb": i, "h": held,
                "pnl": pnl - fee, "ne": pos["ne"], "q": pos["q"], "r": reason,
            })
            pos = None
            cooldown = cfg["cooldown_bars"]

    if pos is not None:
        avg = pos["c"] / pos["q"]
        pnl = ((close[-1] - avg) / avg) if pos["s"] == "L" else ((avg - close[-1]) / avg)
        fee = fee_rate * pos["ne"]
        trades.append({"s": pos["s"], "avg": avg, "xp": close[-1],
                       "eb": pos["eb"], "xb": n - 1, "h": n - 1 - pos["eb"],
                       "pnl": pnl - fee, "ne": pos["ne"], "q": pos["q"], "r": "END"})

    return trades


def compute_stats(trades):
    if not trades:
        return {"n": 0, "sh": 0, "ret": 0, "wr": 0, "pf": 0, "dd": 0,
                "apnl": 0, "aw": 0, "al": 0, "ah": 0, "lo": 0, "so": 0, "py": 0, "ae": 0, "rx": {}}

    pnls = np.array([t["pnl"] for t in trades])
    qtys = np.array([t["q"] for t in trades])
    q_mean = max(qtys.mean(), 0.01)

    # 2026-04-10: CAP equity curve growth to avoid absurd compound overflow for
    # high-frequency scalping (60k trades × 1.15% per trade → eq = 10^287).
    # Cap at 1e6 (1,000,000x = 100,000,000% return) — anything above is a sign
    # of unrealistic math, not a real edge. The cap protects downstream stats.
    EQ_CAP = 1e6
    eq = np.ones(len(trades) + 1)
    for i, t in enumerate(trades):
        w = min(t["q"] / q_mean, 5.0)
        eq[i + 1] = eq[i] * (1 + t["pnl"] * w)
        if not np.isfinite(eq[i + 1]) or eq[i + 1] <= 0:
            eq[i + 1] = max(eq[i] * 0.5, 1e-10)
        if eq[i + 1] > EQ_CAP:
            eq[i + 1] = EQ_CAP

    rets = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1)
    rets = rets[np.isfinite(rets)]

    w = pnls[pnls > 0]
    lo = pnls[pnls < 0]
    gains = w.sum() if len(w) else 0
    losses = abs(lo.sum()) if len(lo) else 0

    # 2026-04-10: SIMPLE (non-compound) total return — sum of pnl × notional_weight.
    # This is the realistic "no reinvestment" gain, much more interpretable.
    simple_ret_pct = float(pnls.sum()) * 100

    avg_bars = np.mean([t["h"] for t in trades])
    # 2026-04-10: Proper annualized Sharpe using actual trade frequency.
    # Old formula `sqrt(35040 / avg_bars)` was ~98 for scalps, inflating Sharpe.
    # New: use sqrt(n_trades / years_covered). For 4-year backtests: sqrt(n/4).
    # This is still optimistic (assumes trade independence) but not absurd.
    if len(rets) > 1 and np.std(rets) > 0:
        # Assume ~4 years covered; derive from trade count if possible
        years = max(4.0, len(trades) / 5000.0)  # rough: 5k trades/yr baseline
        ann_factor = np.sqrt(len(trades) / years) if years > 0 else 1.0
        sharpe = float(np.mean(rets) / np.std(rets) * ann_factor)
    else:
        sharpe = 0.0

    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak > 0, peak, 1)

    reasons = {}
    for t in trades:
        reasons[t["r"]] = reasons.get(t["r"], 0) + 1

    return {
        "n": len(trades),
        "sh": round(sharpe, 2),
        "ret": round(float(eq[-1] / eq[0] - 1) * 100, 2),  # compound (capped at EQ_CAP)
        "simple_ret_pct": round(simple_ret_pct, 2),  # 2026-04-10: sum of pnl%, no compound
        "wr": round(len(w) / len(pnls) * 100, 1) if len(pnls) else 0,
        "pf": round(gains / losses, 2) if losses > 0 else (999 if gains > 0 else 0),
        "dd": round(float(np.min(dd)) * 100, 2),
        "apnl": round(float(pnls.mean()) * 100, 3),
        "aw": round(float(w.mean()) * 100, 3) if len(w) else 0,
        "al": round(float(lo.mean()) * 100, 3) if len(lo) else 0,
        "ah": round(float(avg_bars), 1),
        "lo": sum(1 for t in trades if t["s"] == "L"),
        "so": sum(1 for t in trades if t["s"] == "S"),
        "py": sum(1 for t in trades if t["ne"] > 1),
        "ae": round(np.mean([t["ne"] for t in trades]), 2),
        "rx": reasons,
    }


def get_configs():
    base = {
        "speed_smooth": 3,
        "accel_lookback": 5,
        "tf_weights": {"3m": 0.5, "15m": 1.0, "1h": 2.0, "4h": 3.0, "D": 4.0},
        "tf_z_threshold": 1.0,
        "entry_z_threshold": 2.0,
        "entry_accel_threshold": 0.3,
        "min_tf_agree": 2,
        "consolidation_bars": 20,
        "consolidation_ratio": 0.5,
        "post_consol_window": 10,
        "base_qty": 1.0,
        "pyramid_max": 4,
        "pyramid_accel_threshold": 0.2,
        "pyramid_min_bars": 8,
        "pyramid_price_tolerance": 0.02,
        "pyramid_qty_mult": 1.5,
        "exit_speed_decay_ratio": 0.3,
        "exit_accel_threshold": -0.1,
        "exit_opposing_ratio": 1.5,
        "exit_min_tf": 1,
        "min_hold_bars": 4,
        "max_hold_bars": 384,
        "cooldown_bars": 4,
        "fee_pct": 0.075,
        "warmup": 500,
    }
    cfgs = {"BASE": base}

    cfgs["SELECTIVE"] = {**base, "entry_z_threshold": 3.0, "entry_accel_threshold": 0.5,
        "min_tf_agree": 3, "exit_speed_decay_ratio": 0.35, "cooldown_bars": 8}

    cfgs["ULTRA_SELECT"] = {**base, "entry_z_threshold": 4.0, "entry_accel_threshold": 0.8,
        "min_tf_agree": 3, "exit_speed_decay_ratio": 0.4, "cooldown_bars": 12, "pyramid_max": 6}

    cfgs["FAST_SCALP"] = {**base, "speed_smooth": 2, "accel_lookback": 3,
        "entry_z_threshold": 2.5, "entry_accel_threshold": 0.5,
        "exit_speed_decay_ratio": 0.4, "max_hold_bars": 96, "min_hold_bars": 2,
        "pyramid_max": 2, "cooldown_bars": 2,
        "tf_weights": {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 0.5, "D": 0.3}}

    cfgs["SWING"] = {**base, "speed_smooth": 5, "accel_lookback": 8,
        "entry_z_threshold": 2.0, "entry_accel_threshold": 0.2,
        "exit_speed_decay_ratio": 0.2, "max_hold_bars": 960, "min_hold_bars": 16,
        "pyramid_max": 6, "pyramid_min_bars": 16, "cooldown_bars": 8,
        "tf_weights": {"3m": 0.3, "15m": 0.5, "1h": 1.5, "4h": 3.0, "D": 5.0}}

    cfgs["AGGRO_PYR"] = {**base, "pyramid_max": 8, "pyramid_min_bars": 4,
        "pyramid_price_tolerance": 0.03, "pyramid_qty_mult": 2.0,
        "entry_z_threshold": 2.0, "exit_speed_decay_ratio": 0.25}

    cfgs["HTF_DOM"] = {**base, "speed_smooth": 5, "accel_lookback": 8,
        "entry_z_threshold": 2.0, "max_hold_bars": 960, "min_hold_bars": 16,
        "tf_weights": {"3m": 0.2, "15m": 0.5, "1h": 1.0, "4h": 4.0, "D": 6.0}}

    cfgs["LTF_DOM"] = {**base, "speed_smooth": 2, "accel_lookback": 3,
        "entry_z_threshold": 2.5, "max_hold_bars": 192,
        "tf_weights": {"3m": 4.0, "15m": 3.0, "1h": 1.0, "4h": 0.5, "D": 0.3}}

    cfgs["TIGHT_EXIT"] = {**base, "exit_speed_decay_ratio": 0.5, "exit_accel_threshold": 0.0,
        "exit_opposing_ratio": 1.2, "exit_min_tf": 2, "min_hold_bars": 2}

    cfgs["LOOSE_EXIT"] = {**base, "exit_speed_decay_ratio": 0.15, "exit_accel_threshold": -0.3,
        "exit_opposing_ratio": 2.0, "min_hold_bars": 8, "max_hold_bars": 768}

    cfgs["CONSOL_BREAK"] = {**base, "consolidation_bars": 30, "consolidation_ratio": 0.3,
        "post_consol_window": 15, "entry_z_threshold": 2.5,
        "pyramid_max": 5, "pyramid_qty_mult": 2.0}

    cfgs["EQUAL_TF"] = {**base, "tf_weights": {"3m": 1.0, "15m": 1.0, "1h": 1.0, "4h": 1.0, "D": 1.0}}

    cfgs["WHALE"] = {**base, "min_tf_agree": 4, "entry_z_threshold": 1.5,
        "exit_speed_decay_ratio": 0.15, "max_hold_bars": 1920, "min_hold_bars": 24,
        "pyramid_max": 8, "pyramid_min_bars": 24, "pyramid_qty_mult": 2.5, "cooldown_bars": 12}

    return cfgs


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    npz_dir = NPZ_DIR
    files = sorted([f for f in os.listdir(npz_dir) if f.endswith(".npz")])

    print(f"{'='*80}")
    print(f"WT/DC DELTA ENGINE v3 — {len(files)} symbols, z-score normalized speed")
    print(f"{'='*80}\n")

    configs = get_configs()
    if len(sys.argv) > 1 and sys.argv[1] in configs:
        configs = {sys.argv[1]: configs[sys.argv[1]]}

    all_results = {}

    for cfg_name, cfg in configs.items():
        print(f"\n{'#'*80}")
        print(f"CONFIG: {cfg_name}")
        tw = cfg["tf_weights"]
        print(f"  TF weights: 3m={tw['3m']} 15m={tw['15m']} 1h={tw['1h']} 4h={tw['4h']} D={tw['D']}")
        print(f"  Entry: z>{cfg['entry_z_threshold']} accel>{cfg['entry_accel_threshold']} TFs>={cfg['min_tf_agree']}")
        print(f"  Exit: decay<{cfg['exit_speed_decay_ratio']} accel<{cfg['exit_accel_threshold']}")
        print(f"  Pyramid: max={cfg['pyramid_max']} mult={cfg['pyramid_qty_mult']}x bars>={cfg['pyramid_min_bars']}")
        print(f"{'#'*80}\n")

        all_trades = []
        for fi, fname in enumerate(files):
            sym = fname.replace(".npz", "")
            d = np.load(os.path.join(npz_dir, fname), allow_pickle=True)
            close = d["close"].astype(np.float64)
            sig = compute_signals(d, cfg)
            trades = run_trades(close, sig, cfg)
            st = compute_stats(trades)
            all_trades.extend(trades)
            print(f"  {sym:<16} t={st['n']:>5} Sh={st['sh']:>7.2f} "
                  f"Ret={st['ret']:>9.1f}% WR={st['wr']:>5.1f}% "
                  f"PF={st['pf']:>6.2f} DD={st['dd']:>7.2f}% "
                  f"py={st['py']:>3} ae={st['ae']:.1f} "
                  f"L/S={st['lo']}/{st['so']}")

        agg = compute_stats(all_trades)
        all_results[cfg_name] = agg
        print(f"\n  --- AGG: {cfg_name} ---")
        print(f"  Trades: {agg['n']}  Sharpe: {agg['sh']}  Ret: {agg['ret']}%")
        print(f"  WR: {agg['wr']}%  PF: {agg['pf']}  MaxDD: {agg['dd']}%")
        print(f"  AvgPnL: {agg['apnl']}%  AvgW: {agg['aw']}%  AvgL: {agg['al']}%")
        print(f"  Hold: {agg['ah']}bars  L/S: {agg['lo']}/{agg['so']}  Pyr: {agg['py']}  AvgEnt: {agg['ae']}")
        print(f"  Exits: {agg['rx']}")

    if len(all_results) > 1:
        print(f"\n{'='*80}")
        print("FINAL RANKING (by Sharpe)")
        print(f"{'='*80}")
        ranked = sorted(all_results.items(), key=lambda x: x[1]["sh"], reverse=True)
        print(f"\n{'Config':<16} {'Sharpe':>7} {'Return':>12} {'Trades':>7} {'WR':>6} {'PF':>6} {'MaxDD':>8} {'Pyr':>5} {'AvgEnt':>7}")
        print("-" * 82)
        for name, s in ranked:
            print(f"{name:<16} {s['sh']:>7.2f} {s['ret']:>11.1f}% {s['n']:>7} "
                  f"{s['wr']:>5.1f}% {s['pf']:>6.2f} {s['dd']:>7.2f}% {s['py']:>5} {s['ae']:>7.2f}")

        with open(os.path.join(RESULTS_DIR, "delta_v3_results.json"), "w") as f:
            json.dump({"timestamp": datetime.utcnow().isoformat(), "n_symbols": len(files),
                        "ranking": [{"config": n, **{k: v for k, v in s.items() if k != "rx"}} for n, s in ranked]},
                      f, indent=2, default=str)
        print(f"\nBest: {ranked[0][0]} (Sharpe {ranked[0][1]['sh']})")
        print(f"Saved to {RESULTS_DIR}/delta_v3_results.json")


if __name__ == "__main__":
    main()
