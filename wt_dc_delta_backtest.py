#!/usr/bin/env python3
"""
WT/DC Delta 3-Month Backtest
==============================
Tests the delta speed strategy on the LAST 3 MONTHS of all 48 crypto NPZ files.
Uses wt_dc_delta.compute_npz_signals() — same code that will run live.
Flat-sized (no compounding) for realistic Sharpe.
"""
import numpy as np
import os
import sys
import json
from datetime import datetime
from wt_dc_delta import compute_npz_signals, DEFAULT_CFG, TFS

NPZ_DIR = "backtest_v4/indicators"
RESULTS_DIR = "data/wt_dc_research"
BARS_3MO = 8640  # 90 days * 96 bars/day at 15m


def run_trades(close, sig, cfg, start_bar=0):
    """Trade state machine with pyramiding. Flat sizing."""
    n = len(close)
    trades = []
    pos = None
    cooldown = 0
    max_hold = cfg.get("max_hold_bars", 384)
    pyr_max = cfg.get("pyramid_max", 8)
    pyr_min_bars = cfg.get("pyramid_min_bars", 8)
    pyr_tol = cfg.get("pyramid_price_tolerance", 0.02)
    pyr_qty_mult = cfg.get("pyramid_qty_mult", 1.5)
    pyr_accel = cfg.get("pyramid_accel_threshold", 0.2)
    exit_decay = cfg.get("exit_speed_decay_ratio", 0.15)
    exit_accel_t = cfg.get("exit_accel_threshold", -0.1)
    exit_opp = cfg.get("exit_opposing_ratio", 1.5)
    exit_min_tf = cfg.get("exit_min_tf_lost", 2)
    min_hold = cfg.get("exit_min_hold", 4)
    entry_min_tf = cfg.get("entry_min_tf", 4)
    base_qty = 1.0
    fee_rate = cfg.get("fee_pct", 0.075) / 100 * 2
    cd_bars = cfg.get("cooldown_bars", 4)

    tbz = sig["total_bull_z"]
    tez = sig["total_bear_z"]
    ba = sig["bull_accel"]
    bea = sig["bear_accel"]
    btf = sig["bull_tf_count"]
    betf = sig["bear_tf_count"]
    el = sig["entry_long"]
    es = sig["entry_short"]

    for i in range(start_bar, n):
        if cooldown > 0:
            cooldown -= 1

        if pos is None:
            if cooldown > 0:
                continue
            if el[i]:
                pos = {"s": "L", "ep": close[i], "eb": i, "q": base_qty, "c": close[i] * base_qty,
                       "ne": 1, "ms": tbz[i], "leb": i, "lep": close[i]}
            elif es[i]:
                pos = {"s": "S", "ep": close[i], "eb": i, "q": base_qty, "c": close[i] * base_qty,
                       "ne": 1, "ms": tez[i], "leb": i, "lep": close[i]}
            continue

        held = i - pos["eb"]
        is_long = pos["s"] == "L"
        avg = pos["c"] / pos["q"]

        if is_long:
            spd = tbz[i]
            acc = ba[i]
            opp = tez[i]
            pnl = (close[i] - avg) / avg
            tf_active = btf[i]
            tf_ok = btf[i] >= entry_min_tf
        else:
            spd = tez[i]
            acc = bea[i]
            opp = tbz[i]
            pnl = (avg - close[i]) / avg
            tf_active = betf[i]
            tf_ok = betf[i] >= entry_min_tf

        pos["ms"] = max(pos["ms"], spd)

        # Pyramid: re-enter with MORE qty at better price
        if pos["ne"] < pyr_max and (i - pos["leb"]) >= pyr_min_bars:
            accel_ok = (ba[i] if is_long else bea[i]) > pyr_accel
            if is_long:
                price_ok = close[i] <= pos["lep"] * (1 + pyr_tol)
            else:
                price_ok = close[i] >= pos["lep"] * (1 - pyr_tol)
            if accel_ok and tf_ok and price_ok:
                add_qty = base_qty * pyr_qty_mult * pos["ne"]  # BIGGER each time
                pos["q"] += add_qty
                pos["c"] += close[i] * add_qty
                pos["ne"] += 1
                pos["leb"] = i
                pos["lep"] = close[i]

        # Exit: speed dying — LOOSE, only 2 TF confirmations needed
        ms = pos["ms"]
        ratio = spd / ms if ms > 0.01 else 0
        dying = ratio < exit_decay and acc < exit_accel_t
        opp_strong = (opp > spd * exit_opp and acc < exit_accel_t) if spd > 0.1 else opp > 1.0
        tf_lost = tf_active < exit_min_tf

        should_exit = False
        reason = ""
        if dying:
            should_exit = True
            reason = "SPEED_DIED"
        elif opp_strong:
            should_exit = True
            reason = "OPPOSING"
        elif held >= max_hold:
            should_exit = True
            reason = "MAX_HOLD"
        elif acc < exit_accel_t and held > min_hold and tf_lost:
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
            cooldown = cd_bars

    # Close open at end
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
    w = pnls[pnls > 0]
    lo = pnls[pnls < 0]
    gains = w.sum() if len(w) else 0
    losses = abs(lo.sum()) if len(lo) else 0

    # FLAT equity curve — no compounding, just cumulative PnL
    flat_eq = np.cumsum(pnls) + 1
    peak = np.maximum.accumulate(flat_eq)
    dd = (flat_eq - peak) / np.where(peak > 0, peak, 1)

    avg_bars = np.mean([t["h"] for t in trades])
    tpy = 35040 / max(avg_bars, 1)
    sharpe = float(pnls.mean() / pnls.std() * np.sqrt(tpy)) if len(pnls) > 1 and pnls.std() > 0 else 0

    reasons = {}
    for t in trades:
        reasons[t["r"]] = reasons.get(t["r"], 0) + 1

    return {
        "n": len(trades),
        "sh": round(sharpe, 2),
        "ret": round(float(flat_eq[-1] - 1) * 100, 2),
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
    """WHALE-based configs tuned for the delta strategy."""
    whale = {**DEFAULT_CFG,
        "speed_smooth": 3,
        "accel_lookback": 5,
        "entry_min_tf": 4,
        "entry_z_threshold": 1.5,
        "entry_accel_threshold": 0.2,
        "exit_speed_decay_ratio": 0.15,
        "exit_accel_threshold": -0.1,
        "exit_opposing_ratio": 1.5,
        "exit_min_tf_lost": 2,
        "exit_min_hold": 4,
        "pyramid_max": 8,
        "pyramid_min_bars": 8,
        "pyramid_price_tolerance": 0.02,
        "pyramid_qty_mult": 1.5,
        "pyramid_accel_threshold": 0.2,
        "max_hold_bars": 1920,
        "cooldown_bars": 4,
        "fee_pct": 0.075,
    }
    cfgs = {"WHALE": whale}

    # Stricter entry (5 TFs)
    cfgs["WHALE_5TF"] = {**whale, "entry_min_tf": 5}

    # Faster exit
    cfgs["WHALE_FAST_EXIT"] = {**whale, "exit_speed_decay_ratio": 0.25, "exit_min_tf_lost": 3}

    # Bigger pyramids
    cfgs["WHALE_BIG_PYR"] = {**whale, "pyramid_qty_mult": 2.5, "pyramid_max": 10}

    # Tighter entry z
    cfgs["WHALE_Z2"] = {**whale, "entry_z_threshold": 2.0}

    # LTF heavy
    cfgs["WHALE_LTF"] = {**whale, "tf_weights": {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5}}

    # HTF heavy (original WHALE)
    cfgs["WHALE_HTF"] = {**whale, "tf_weights": {"3m": 0.2, "15m": 0.5, "1h": 1.0, "4h": 4.0, "D": 6.0}}

    # Quick scalp exits
    cfgs["WHALE_SCALP_EXIT"] = {**whale, "exit_speed_decay_ratio": 0.3, "exit_accel_threshold": 0.0, "max_hold_bars": 192}

    return cfgs


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    npz_dir = NPZ_DIR
    files = sorted([f for f in os.listdir(npz_dir) if f.endswith(".npz")])

    use_3mo = "--full" not in sys.argv
    period = "LAST 3 MONTHS" if use_3mo else "FULL HISTORY"

    print(f"{'='*80}")
    print(f"WT/DC DELTA BACKTEST — {len(files)} symbols, {period}, FLAT SIZING")
    print(f"Entry: 4+ TF speed ramp | Exit: 2 TF speed dying | Pyramid: bigger on re-entry")
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
        print(f"  Entry: z>{cfg['entry_z_threshold']} accel>{cfg['entry_accel_threshold']} TFs>={cfg['entry_min_tf']}")
        print(f"  Exit: decay<{cfg['exit_speed_decay_ratio']} accel<{cfg['exit_accel_threshold']} tf_lost<{cfg['exit_min_tf_lost']}")
        print(f"  Pyramid: max={cfg['pyramid_max']} mult={cfg['pyramid_qty_mult']}x")
        print(f"{'#'*80}\n")

        all_trades = []
        for fname in files:
            sym = fname.replace(".npz", "")
            d = np.load(os.path.join(npz_dir, fname), allow_pickle=True)
            n = len(d["close"])

            # Compute signals on FULL data (z-score needs history)
            sig = compute_npz_signals(d, cfg)

            # But only trade the last 3 months
            if use_3mo:
                start = max(n - BARS_3MO, 500)
            else:
                start = 500

            close = d["close"].astype(np.float64)
            trades = run_trades(close, sig, cfg, start_bar=start)
            st = compute_stats(trades)
            all_trades.extend(trades)

            if st["n"] > 0:
                print(f"  {sym:<16} t={st['n']:>4} Sh={st['sh']:>7.2f} "
                      f"Ret={st['ret']:>8.2f}% WR={st['wr']:>5.1f}% "
                      f"PF={st['pf']:>6.2f} DD={st['dd']:>7.2f}% "
                      f"py={st['py']:>3} ae={st['ae']:.1f} "
                      f"L/S={st['lo']}/{st['so']}")
            else:
                print(f"  {sym:<16} NO TRADES (n={n})")

        agg = compute_stats(all_trades)
        all_results[cfg_name] = agg

        print(f"\n  {'='*60}")
        print(f"  AGGREGATE: {cfg_name} ({period})")
        print(f"  {'='*60}")
        print(f"  Trades:    {agg['n']}")
        print(f"  Sharpe:    {agg['sh']}")
        print(f"  Return:    {agg['ret']}% (flat, no compounding)")
        print(f"  Win Rate:  {agg['wr']}%")
        print(f"  PF:        {agg['pf']}")
        print(f"  Max DD:    {agg['dd']}%")
        print(f"  Avg PnL:   {agg['apnl']}%")
        print(f"  Avg Winner:{agg['aw']}%")
        print(f"  Avg Loser: {agg['al']}%")
        print(f"  Avg Hold:  {agg['ah']} bars ({agg['ah'] * 15 / 60:.1f}h)")
        print(f"  L/S:       {agg['lo']}/{agg['so']}")
        print(f"  Pyramided: {agg['py']}")
        print(f"  Avg Entries:{agg['ae']}")
        print(f"  Exits:     {agg['rx']}")

    if len(all_results) > 1:
        print(f"\n{'='*80}")
        print(f"FINAL RANKING — {period}")
        print(f"{'='*80}")
        ranked = sorted(all_results.items(), key=lambda x: x[1]["sh"], reverse=True)
        print(f"\n{'Config':<18} {'Sharpe':>7} {'Return':>9} {'Trades':>7} {'WR':>6} {'PF':>6} {'MaxDD':>8} {'Pyr':>5} {'AvgPnL':>8}")
        print("-" * 82)
        for name, s in ranked:
            print(f"{name:<18} {s['sh']:>7.2f} {s['ret']:>8.1f}% {s['n']:>7} "
                  f"{s['wr']:>5.1f}% {s['pf']:>6.2f} {s['dd']:>7.2f}% {s['py']:>5} {s['apnl']:>7.3f}%")

        with open(os.path.join(RESULTS_DIR, "delta_3mo_results.json"), "w") as f:
            json.dump({
                "timestamp": datetime.utcnow().isoformat(),
                "period": period,
                "n_symbols": len(files),
                "ranking": [{"config": n, **{k: v for k, v in s.items() if k != "rx"}} for n, s in ranked],
            }, f, indent=2, default=str)

        best = ranked[0]
        print(f"\nBEST: {best[0]} (Sharpe {best[1]['sh']}, WR {best[1]['wr']}%, PF {best[1]['pf']})")
        print(f"Saved to {RESULTS_DIR}/delta_3mo_results.json")

        if best[1]["sh"] > 0 and best[1]["ret"] > 0:
            print(f"\n*** RESULTS POSITIVE — ready to wire into live ***")
        else:
            print(f"\n*** RESULTS NEGATIVE — do NOT deploy ***")


if __name__ == "__main__":
    main()
