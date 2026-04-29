"""
Config Tradier Sweep — Exhaustive one-at-a-time parameter sweep
================================================================
FAST version: Pre-computes ALL entry/exit scores once, then sweeps thresholds
and config gates by filtering pre-computed arrays.

Usage: python3 config_tradier_sweep.py
Output: config_sweep_results.txt
"""
import sys
import os
import time
import datetime
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wt_dc_entry_scorer import score_entry
from wt_dc_exit_scorer import score_exit

# ============================================================
# CONFIG
# ============================================================
BASE_PATH = "/Users/niels/Documents/binance"
NPZ_DIR = os.path.join(BASE_PATH, "backtest_v8/indicators")
SYMBOLS = ["MU", "AAPL", "TTD", "FIVN", "AMZN", "MRVL", "XOM", "CVX", "GLD", "USO", "NVDA", "MSFT", "ASTS"]
START_TS = 1704067200  # 2024-01-01 UTC
MARKET_OPEN_UTC = 13 * 60 + 30  # 13:30 UTC
MARKET_CLOSE_UTC = 20 * 60  # 20:00 UTC
MAX_HOLD_BARS = 96
BAR_STEP = 2
OUTPUT_FILE = os.path.join(BASE_PATH, "config_sweep_results.txt")

# Flush prints immediately
import functools
print = functools.partial(print, flush=True)


def load_and_precompute(symbols):
    """Load all NPZ data and precompute entry/exit scores for every valid bar."""
    all_sym = {}
    for sym in symbols:
        path = os.path.join(NPZ_DIR, f"{sym}.npz")
        if not os.path.exists(path):
            print(f"  {sym}: MISSING NPZ")
            continue
        print(f"  Loading {sym}...", end=" ")
        f = np.load(path, allow_pickle=True)
        data = {}
        float_keys = []
        for k in f.files:
            arr = f[k]
            if arr.dtype.kind == 'f':
                data[k] = arr.astype(np.float32)
                float_keys.append(k)
            else:
                data[k] = arr
        f.close()

        ts = data.get("timestamps", np.array([]))
        n = len(ts)

        # Vectorized filter: after START_TS, weekdays, trading hours
        valid_mask = np.zeros(n, dtype=bool)
        for i in range(n):
            t = int(ts[i])
            if t < START_TS:
                continue
            dt = datetime.datetime.utcfromtimestamp(t)
            if dt.weekday() >= 5:
                continue
            m = dt.hour * 60 + dt.minute
            if m < MARKET_OPEN_UTC or m >= MARKET_CLOSE_UTC:
                continue
            valid_mask[i] = True

        valid_idx = np.where(valid_mask)[0]
        sampled = valid_idx[::BAR_STEP]
        ns = len(sampled)
        if ns < 100:
            print(f"only {ns} bars, skipping")
            continue

        # Precompute scores for every sampled bar
        entry_long = np.zeros(ns, dtype=np.float32)
        entry_short = np.zeros(ns, dtype=np.float32)
        exit_long = np.zeros(ns, dtype=np.float32)
        exit_short = np.zeros(ns, dtype=np.float32)
        close_arr = data.get("close", np.zeros(n, dtype=np.float32))
        stoch_k_1h = data.get("stoch_k_1h", np.full(n, 50.0, dtype=np.float32))
        stoch_k_1h_prev = data.get("stoch_k_1h_prev", np.full(n, 50.0, dtype=np.float32))
        stoch_k_4h = data.get("stoch_k_4h", np.full(n, 50.0, dtype=np.float32))
        stoch_k_4h_prev = data.get("stoch_k_4h_prev", np.full(n, 50.0, dtype=np.float32))
        mfi_1h_arr = data.get("mfi_1h", np.full(n, 50.0, dtype=np.float32))
        # Velocity arrays for TF-against counting
        vel_5m = data.get("wt_velocity_5m", np.zeros(n, dtype=np.float32))
        vel_15m = data.get("wt_velocity_15m", np.zeros(n, dtype=np.float32))
        vel_1h = data.get("wt_velocity_1h", np.zeros(n, dtype=np.float32))
        vel_4h = data.get("wt_velocity_4h", np.zeros(n, dtype=np.float32))
        vel_D = data.get("wt_velocity_D", np.zeros(n, dtype=np.float32))
        timestamps_arr = ts

        # Week strings for weekly Sharpe
        week_strs = []
        for j in range(ns):
            idx = sampled[j]
            dt = datetime.datetime.utcfromtimestamp(int(ts[idx]))
            iso = dt.isocalendar()
            week_strs.append(f"{iso[0]}-W{iso[1]:02d}")

        t_score = time.time()
        for j in range(ns):
            idx = sampled[j]
            # Build indicator dict from float arrays only
            ind = {k: float(data[k][idx]) for k in float_keys}
            el, _ = score_entry(ind, True)
            es, _ = score_entry(ind, False)
            xl, _ = score_exit(ind, True)
            xs, _ = score_exit(ind, False)
            entry_long[j] = el
            entry_short[j] = es
            exit_long[j] = xl
            exit_short[j] = xs

        dur = time.time() - t_score
        print(f"{ns} bars, scored in {dur:.1f}s")

        all_sym[sym] = {
            "sampled": sampled,
            "entry_long": entry_long,
            "entry_short": entry_short,
            "exit_long": exit_long,
            "exit_short": exit_short,
            "close": close_arr,
            "stoch_k_1h": stoch_k_1h,
            "stoch_k_1h_prev": stoch_k_1h_prev,
            "stoch_k_4h": stoch_k_4h,
            "stoch_k_4h_prev": stoch_k_4h_prev,
            "mfi_1h": mfi_1h_arr,
            "vel_5m": vel_5m, "vel_15m": vel_15m, "vel_1h": vel_1h,
            "vel_4h": vel_4h, "vel_D": vel_D,
            "timestamps": timestamps_arr,
            "week_strs": week_strs,
            "ns": ns,
        }
    return all_sym


def run_backtest_fast(all_sym, entry_threshold=37, exit_threshold=20,
                      max_hold=MAX_HOLD_BARS, min_hold=0, cooldown=0,
                      noloss_min_pct=0.0, k_zone_enabled=True,
                      k_zone_long=35, k_zone_short=65, k_zone_bonus=25,
                      mfi_exit_enabled=True, mfi_exit_long=70, mfi_exit_short=30,
                      stoch_exit_enabled=True, min_exit_tf_against=2):
    """Fast backtest using precomputed score arrays."""
    trades = []
    weekly_pnl = defaultdict(float)

    for sym, sd in all_sym.items():
        sampled = sd["sampled"]
        ns = sd["ns"]
        close = sd["close"]
        stoch = sd["stoch_k_1h"]
        stoch_prev = sd["stoch_k_1h_prev"]
        stoch4 = sd["stoch_k_4h"]
        stoch4_prev = sd["stoch_k_4h_prev"]
        mfi = sd["mfi_1h"]
        v5 = sd["vel_5m"]
        v15 = sd["vel_15m"]
        v1h = sd["vel_1h"]
        v4h = sd["vel_4h"]
        vD = sd["vel_D"]
        weeks = sd["week_strs"]

        for is_long in (True, False):
            entry_scores = sd["entry_long"] if is_long else sd["entry_short"]
            exit_scores = sd["exit_long"] if is_long else sd["exit_short"]

            pos_entry_price = 0.0
            pos_entry_j = -1
            in_pos = False
            last_exit_j = -9999

            for j in range(ns):
                idx = sampled[j]
                price = float(close[idx])
                if price <= 0 or np.isnan(price):
                    continue

                if not in_pos:
                    if j - last_exit_j < cooldown:
                        continue
                    score = float(entry_scores[j])
                    if k_zone_enabled:
                        k_val = float(stoch[idx])
                        if is_long and k_val < k_zone_long:
                            score += k_zone_bonus
                        elif not is_long and k_val > k_zone_short:
                            score += k_zone_bonus
                    if score >= entry_threshold:
                        pos_entry_price = price
                        pos_entry_j = j
                        in_pos = True
                else:
                    bars = j - pos_entry_j
                    if bars < min_hold:
                        continue
                    if is_long:
                        gain = (price - pos_entry_price) / pos_entry_price * 100
                    else:
                        gain = (pos_entry_price - price) / pos_entry_price * 100

                    should_exit = False

                    # Exit scorer
                    ex_score = float(exit_scores[j])
                    if ex_score >= exit_threshold:
                        tf_against = 0
                        if is_long:
                            if float(v5[idx]) < 0: tf_against += 1
                            if float(v15[idx]) < 0: tf_against += 1
                            if float(v1h[idx]) < 0: tf_against += 1
                            if float(v4h[idx]) < 0: tf_against += 1
                            if float(vD[idx]) < 0: tf_against += 1
                        else:
                            if float(v5[idx]) > 0: tf_against += 1
                            if float(v15[idx]) > 0: tf_against += 1
                            if float(v1h[idx]) > 0: tf_against += 1
                            if float(v4h[idx]) > 0: tf_against += 1
                            if float(vD[idx]) > 0: tf_against += 1
                        if tf_against >= min_exit_tf_against:
                            should_exit = True

                    # MFI exit
                    if mfi_exit_enabled:
                        m = float(mfi[idx])
                        if is_long and m > mfi_exit_long:
                            should_exit = True
                        elif not is_long and m < mfi_exit_short:
                            should_exit = True

                    # Stoch cross exit
                    if stoch_exit_enabled:
                        sk = float(stoch[idx])
                        sp = float(stoch_prev[idx])
                        if is_long and sk > 70 and sk < sp:
                            should_exit = True
                        elif not is_long and sk < 30 and sk > sp:
                            should_exit = True

                    # Max hold
                    if bars >= max_hold:
                        should_exit = True

                    # Noloss gate
                    if should_exit and noloss_min_pct > 0 and gain < noloss_min_pct:
                        if bars < max_hold:
                            should_exit = False

                    if should_exit:
                        trades.append(gain)
                        weekly_pnl[weeks[j]] += gain
                        last_exit_j = j
                        in_pos = False
                        pos_entry_price = 0.0

    return trades, weekly_pnl


def compute_metrics(trades, weekly_pnl):
    if not trades:
        return {"trades": 0, "wr": 0.0, "pf": 0.0, "wk_sharpe": 0.0}
    n = len(trades)
    wins = sum(1 for g in trades if g > 0)
    wr = wins / n * 100
    gross_w = sum(g for g in trades if g > 0)
    gross_l = abs(sum(g for g in trades if g <= 0))
    pf = gross_w / gross_l if gross_l > 0.001 else 99.99
    pf = min(pf, 99.99)
    if weekly_pnl:
        vals = list(weekly_pnl.values())
        mu = np.mean(vals)
        sigma = np.std(vals)
        wk_sharpe = (mu / sigma) if sigma > 0 else 0  # per-period pool_sharpe (sqrt(52) annualization stripped 2026-04-29 per CLAUDE.md rule 4)
    else:
        wk_sharpe = 0
    return {"trades": n, "wr": wr, "pf": pf, "pool_sharpe": wk_sharpe}


# ============================================================
# PARAMETER DEFINITIONS
# ============================================================
SWEEP_PARAMS = []

# MOST IMPACTFUL: Entry/Exit thresholds
SWEEP_PARAMS.append(("ENTRY_THRESHOLD", 37, [30, 33, 35, 37, 40, 43, 45, 50]))
SWEEP_PARAMS.append(("EXIT_THRESHOLD", 20, [15, 18, 20, 22, 25, 28, 30]))

# WT settings
SWEEP_PARAMS.append(("MIN_HOLD_BARS", 0, [0, 4, 8, 16, 24, 32, 48]))
SWEEP_PARAMS.append(("COOLDOWN_BARS", 0, [0, 4, 8, 12, 16, 24]))
SWEEP_PARAMS.append(("MAX_HOLD_BARS", 96, [48, 64, 80, 96, 128, 192, 384]))

# K-ZONE settings
SWEEP_PARAMS.append(("K_ZONE_ENABLED", True, [True, False]))
SWEEP_PARAMS.append(("K_ZONE_LONG_THRESHOLD", 35, [15, 20, 25, 30, 35, 40, 45, 50]))
SWEEP_PARAMS.append(("K_ZONE_SHORT_THRESHOLD", 65, [50, 55, 60, 65, 70, 75, 80, 85]))
SWEEP_PARAMS.append(("K_ZONE_BONUS", 25, [0, 5, 10, 15, 20, 25, 30, 35, 40]))

# MFI EXIT settings
SWEEP_PARAMS.append(("MFI_EXIT_ENABLED", True, [True, False]))
SWEEP_PARAMS.append(("MFI_EXIT_LONG_THRESHOLD", 70, [55, 60, 65, 70, 75, 80, 85, 90]))
SWEEP_PARAMS.append(("MFI_EXIT_SHORT_THRESHOLD", 30, [10, 15, 20, 25, 30, 35, 40, 45]))

# STOCH EXIT
SWEEP_PARAMS.append(("STOCH_EXIT_ENABLED", True, [True, False]))

# TF AGAINST for exit
SWEEP_PARAMS.append(("MIN_EXIT_TF_AGAINST", 2, [0, 1, 2, 3, 4, 5]))

# NOLOSS MIN PCT
SWEEP_PARAMS.append(("NOLOSS_MIN_PCT", 0.0, [0.0, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]))

# COMBINED: entry+exit threshold 2D sweep (most important)
# Added as separate combos
ENTRY_EXITS = []
for et in [30, 35, 37, 40, 45]:
    for xt in [15, 20, 25, 30]:
        if et == 37 and xt == 20:
            continue  # skip baseline
        ENTRY_EXITS.append((f"ENTRY={et}_EXIT={xt}", et, xt))


def main():
    print("=" * 90)
    print("CONFIG TRADIER SWEEP — Exhaustive Parameter Sweep (Fast Precomputed)")
    print("=" * 90)
    print(f"Symbols: {SYMBOLS}")
    print(f"NPZ dir: {NPZ_DIR}")
    total_single = sum(len(v) for _, _, v in SWEEP_PARAMS)
    total_combo = len(ENTRY_EXITS)
    total = total_single + total_combo
    print(f"Single-param combos: {total_single}")
    print(f"Entry+Exit 2D combos: {total_combo}")
    print(f"Total runs: {total}")
    print()

    # Phase 1: Load and precompute all scores
    print("PHASE 1: Loading NPZ + precomputing ALL entry/exit scores...")
    t0 = time.time()
    all_sym = load_and_precompute(SYMBOLS)
    print(f"Phase 1 done in {time.time() - t0:.1f}s, {len(all_sym)} symbols loaded")
    print()

    # Phase 2: Baseline
    print("PHASE 2: Running BASELINE (entry>=37, exit>=20)...")
    t0 = time.time()
    bl_trades, bl_weekly = run_backtest_fast(all_sym)
    bl = compute_metrics(bl_trades, bl_weekly)
    print(f"  BASELINE: {bl['trades']}T, WR={bl['wr']:.1f}%, PF={bl['pf']:.2f}x, Sharpe={bl['wk_sharpe']:.2f}")
    print(f"  Done in {time.time() - t0:.3f}s")
    print()

    # Phase 3: Single-param sweep
    print("PHASE 3: Single-parameter sweep...")
    results = []
    results.append(("BASELINE", "default", bl["trades"], bl["wr"], bl["pf"], bl["wk_sharpe"], 0.0))

    done = 0
    for param_name, default_val, test_values in SWEEP_PARAMS:
        for val in test_values:
            done += 1
            # Build kwargs, changing only the one param
            kw = {
                "entry_threshold": 37, "exit_threshold": 20,
                "max_hold": 96, "min_hold": 0, "cooldown": 0,
                "noloss_min_pct": 0.0, "k_zone_enabled": True,
                "k_zone_long": 35, "k_zone_short": 65, "k_zone_bonus": 25,
                "mfi_exit_enabled": True, "mfi_exit_long": 70, "mfi_exit_short": 30,
                "stoch_exit_enabled": True, "min_exit_tf_against": 2,
            }
            param_map = {
                "ENTRY_THRESHOLD": "entry_threshold",
                "EXIT_THRESHOLD": "exit_threshold",
                "MIN_HOLD_BARS": "min_hold",
                "COOLDOWN_BARS": "cooldown",
                "MAX_HOLD_BARS": "max_hold",
                "K_ZONE_ENABLED": "k_zone_enabled",
                "K_ZONE_LONG_THRESHOLD": "k_zone_long",
                "K_ZONE_SHORT_THRESHOLD": "k_zone_short",
                "K_ZONE_BONUS": "k_zone_bonus",
                "MFI_EXIT_ENABLED": "mfi_exit_enabled",
                "MFI_EXIT_LONG_THRESHOLD": "mfi_exit_long",
                "MFI_EXIT_SHORT_THRESHOLD": "mfi_exit_short",
                "STOCH_EXIT_ENABLED": "stoch_exit_enabled",
                "MIN_EXIT_TF_AGAINST": "min_exit_tf_against",
                "NOLOSS_MIN_PCT": "noloss_min_pct",
            }
            if param_name in param_map:
                kw[param_map[param_name]] = val

            trades, weekly = run_backtest_fast(all_sym, **kw)
            m = compute_metrics(trades, weekly)
            vs = m["wk_sharpe"] - bl["wk_sharpe"]
            results.append((param_name, val, m["trades"], m["wr"], m["pf"], m["wk_sharpe"], vs))

            if done % 5 == 0 or done <= 2:
                print(f"  [{done}/{total}] {param_name}={val}: "
                      f"{m['trades']}T WR={m['wr']:.1f}% PF={m['pf']:.2f}x Sharpe={m['wk_sharpe']:.2f} ({vs:+.2f})")

    # Phase 4: 2D Entry+Exit sweep
    print()
    print("PHASE 4: Entry+Exit 2D sweep...")
    for label, et, xt in ENTRY_EXITS:
        done += 1
        trades, weekly = run_backtest_fast(all_sym, entry_threshold=et, exit_threshold=xt)
        m = compute_metrics(trades, weekly)
        vs = m["wk_sharpe"] - bl["wk_sharpe"]
        results.append((label, f"e{et}x{xt}", m["trades"], m["wr"], m["pf"], m["wk_sharpe"], vs))
        if done % 5 == 0:
            print(f"  [{done}/{total}] {label}: "
                  f"{m['trades']}T WR={m['wr']:.1f}% PF={m['pf']:.2f}x Sharpe={m['wk_sharpe']:.2f} ({vs:+.2f})")

    # Sort by Sharpe descending
    results_sorted = sorted(results, key=lambda r: r[5], reverse=True)

    # Write output
    print(f"\nWriting results to {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w") as f:
        f.write("=" * 115 + "\n")
        f.write("CONFIG TRADIER SWEEP — Exhaustive One-at-a-Time + 2D Parameter Sweep\n")
        f.write(f"Date: {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write(f"Symbols: {', '.join(SYMBOLS)}\n")
        f.write(f"Data: {NPZ_DIR}\n")
        f.write(f"Range: 2024-01-01 to present | Bar step: every 2nd | Trading hours: 13:30-20:00 UTC\n")
        f.write(f"Baseline: entry>=37, exit>=20, K-zone bonus=25, MFI exit=70/30, stoch exit=on, min_tf_against=2\n")
        f.write(f"Total runs: {len(results)}\n")
        f.write("=" * 115 + "\n\n")

        # SORTED BY SHARPE (BEST FIRST)
        f.write("=" * 115 + "\n")
        f.write("ALL RESULTS SORTED BY WEEKLY SHARPE (BEST FIRST)\n")
        f.write("=" * 115 + "\n")
        f.write(f"{'Setting':<40} {'Value':>10} {'Trades':>7} {'WR':>7} {'PF':>8} {'WkSharpe':>10} {'vs_BL':>8}\n")
        f.write("-" * 115 + "\n")
        for r in results_sorted:
            f.write(f"{r[0]:<40} {str(r[1]):>10} {r[2]:>7} "
                    f"{r[3]:>6.1f}% {r[4]:>7.2f}x {r[5]:>9.2f} {r[6]:>+7.2f}\n")

        # GROUPED BY PARAMETER
        f.write("\n" + "=" * 115 + "\n")
        f.write("RESULTS GROUPED BY PARAMETER\n")
        f.write("=" * 115 + "\n")
        current = None
        for r in results:
            if r[0] != current:
                current = r[0]
                if current != "BASELINE":
                    f.write(f"\n--- {current} ---\n")
                    f.write(f"{'Value':>12} {'Trades':>7} {'WR':>7} {'PF':>8} {'WkSharpe':>10} {'vs_BL':>8}\n")
            if current == "BASELINE":
                f.write(f"\nBASELINE: {r[2]}T, WR={r[3]:.1f}%, PF={r[4]:.2f}x, Sharpe={r[5]:.2f}\n")
            else:
                f.write(f"{str(r[1]):>12} {r[2]:>7} {r[3]:>6.1f}% {r[4]:>7.2f}x {r[5]:>9.2f} {r[6]:>+7.2f}\n")

        # TOP 20
        f.write("\n" + "=" * 115 + "\n")
        f.write("TOP 20 IMPROVEMENTS OVER BASELINE\n")
        f.write("=" * 115 + "\n")
        top20 = [r for r in results_sorted if r[0] != "BASELINE"][:20]
        for i, r in enumerate(top20, 1):
            f.write(f"{i:>3}. {r[0]:<38} = {str(r[1]):>10} | "
                    f"Sharpe={r[5]:>7.2f} ({r[6]:>+6.2f}) | "
                    f"WR={r[3]:.1f}% PF={r[4]:.2f}x {r[2]}T\n")

        # BOTTOM 20
        f.write("\n" + "=" * 115 + "\n")
        f.write("BOTTOM 20 (WORST vs BASELINE)\n")
        f.write("=" * 115 + "\n")
        bottom20 = [r for r in results_sorted if r[0] != "BASELINE"][-20:]
        for i, r in enumerate(bottom20, 1):
            f.write(f"{i:>3}. {r[0]:<38} = {str(r[1]):>10} | "
                    f"Sharpe={r[5]:>7.2f} ({r[6]:>+6.2f}) | "
                    f"WR={r[3]:.1f}% PF={r[4]:.2f}x {r[2]}T\n")

        # 2D MATRIX for Entry x Exit
        f.write("\n" + "=" * 115 + "\n")
        f.write("ENTRY x EXIT 2D MATRIX (Weekly Sharpe)\n")
        f.write("=" * 115 + "\n")
        entry_vals = [30, 35, 37, 40, 45]
        exit_vals = [15, 20, 25, 30]
        # Build lookup
        matrix = {}
        for r in results:
            if r[0] == "BASELINE":
                matrix[("37", "20")] = r[5]
            elif r[0] == "ENTRY_THRESHOLD":
                matrix[(str(r[1]), "20")] = r[5]
            elif r[0] == "EXIT_THRESHOLD":
                matrix[("37", str(r[1]))] = r[5]
            elif r[0].startswith("ENTRY="):
                parts = r[0].split("_")
                et = parts[0].split("=")[1]
                xt = parts[1].split("=")[1]
                matrix[(et, xt)] = r[5]
        line = f"{'':>12}"
        for xt in exit_vals:
            line += f"  exit>={xt:<5}"
        f.write(line + "\n")
        for et in entry_vals:
            line = f"entry>={et:<5}"
            for xt in exit_vals:
                val = matrix.get((str(et), str(xt)), None)
                if val is not None:
                    line += f"  {val:>7.2f}"
                else:
                    line += f"  {'---':>7}"
            f.write(line + "\n")

    print(f"\nDone! Results in {OUTPUT_FILE}")
    print(f"BASELINE: {bl['trades']}T, WR={bl['wr']:.1f}%, PF={bl['pf']:.2f}x, Sharpe={bl['wk_sharpe']:.2f}")
    best = results_sorted[0]
    worst = results_sorted[-1]
    print(f"BEST:  {best[0]}={best[1]} -> Sharpe={best[5]:.2f} ({best[6]:+.2f})")
    print(f"WORST: {worst[0]}={worst[1]} -> Sharpe={worst[5]:.2f} ({worst[6]:+.2f})")


if __name__ == "__main__":
    main()
