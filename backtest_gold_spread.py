#!/usr/bin/env python3
"""
Gold Spread Backtest — miner/streamer premium vs gold spot ETF.

Pairs to test:
  NEM/GLD  = Newmont (largest gold miner) vs gold spot ETF
  GDX/GLD  = Gold Miners ETF vs gold spot ETF (diversified miner basket)
  NEM/GDX  = Newmont vs miner basket (alpha extraction)
  AEM/GLD  = Agnico Eagle vs gold spot
  RGLD/GLD = Royal Gold (streamer) vs gold spot
  WPM/GLD  = Wheaton Precious (streamer) vs gold spot

Same mean-reversion thesis: miners trade at oscillating premium/discount to gold.
When premium overextends → short miner, long gold (and vice versa).
"""
import json
import math
import sys
import statistics
from pathlib import Path
from collections import defaultdict

BASE = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE))

PAIRS = [
    ("NEM", "GLD", "NEM/GLD"),
    ("GDX", "GLD", "GDX/GLD"),
    ("NEM", "GDX", "NEM/GDX"),
    ("AEM", "GLD", "AEM/GLD"),
    ("RGLD", "GLD", "RGLD/GLD"),
    ("WPM", "GLD", "WPM/GLD"),
]


def load_daily(symbol):
    path = BASE / "klines_cache" / "tradier" / f"{symbol}_D.json"
    if not path.exists():
        return {}
    with open(path) as f:
        bars = json.load(f)
    result = {}
    for b in bars:
        ts = b["timestamp"]
        if "T" in ts:
            ts = ts.replace("T", " ")
        ts = ts[:10]
        if b.get("close", 0) > 0:
            result[ts] = b["close"]
    return result


def compute_zscore(values, lookback):
    zscores = []
    for i in range(len(values)):
        if i < lookback:
            zscores.append(0)
            continue
        window = values[i - lookback:i]
        mean = statistics.mean(window)
        std = statistics.stdev(window) if len(window) > 1 else 0.001
        if std < 0.0001:
            std = 0.0001
        zscores.append((values[i] - mean) / std)
    return zscores


def run_pair_backtest(dates, prices_a, prices_b, lookback=15, z_entry=2.0, z_exit=0.5,
                      z_stop=4.5, max_hold=30, size=5000):
    ratios = [prices_a[i] / prices_b[i] for i in range(len(dates))]
    zscores = compute_zscore(ratios, lookback)
    trades = []
    pos = None
    for i in range(lookback, len(dates)):
        z = zscores[i]
        if pos:
            hold = i - pos["entry_bar"]
            exit_reason = None
            if pos["dir"] == "SHORT_A":
                if z <= z_exit:
                    exit_reason = "REVERT"
                elif z >= z_stop:
                    exit_reason = "STOP"
                elif hold >= max_hold:
                    exit_reason = "MAX_HOLD"
            else:
                if z >= -z_exit:
                    exit_reason = "REVERT"
                elif z <= -z_stop:
                    exit_reason = "STOP"
                elif hold >= max_hold:
                    exit_reason = "MAX_HOLD"
            if exit_reason:
                if pos["dir"] == "SHORT_A":
                    a_pnl = (pos["a_entry"] - prices_a[i]) * pos["a_qty"]
                    b_pnl = (prices_b[i] - pos["b_entry"]) * pos["b_qty"]
                else:
                    a_pnl = (prices_a[i] - pos["a_entry"]) * pos["a_qty"]
                    b_pnl = (pos["b_entry"] - prices_b[i]) * pos["b_qty"]
                total = a_pnl + b_pnl
                trades.append({"dir": pos["dir"], "entry_date": pos["entry_date"], "exit_date": dates[i], "entry_z": pos["entry_z"], "exit_z": z, "hold": hold, "total_pnl": total, "pnl_pct": total / (size * 2) * 100, "exit_reason": exit_reason, "a_move": (prices_a[i] - pos["a_entry"]) / pos["a_entry"] * 100, "b_move": (prices_b[i] - pos["b_entry"]) / pos["b_entry"] * 100})
                pos = None
        if pos is None:
            if z > z_entry:
                a_qty = int(size / prices_a[i]) if prices_a[i] > 0 else 0
                b_qty = int(size / prices_b[i]) if prices_b[i] > 0 else 0
                if a_qty > 0 and b_qty > 0:
                    pos = {"dir": "SHORT_A", "entry_bar": i, "entry_date": dates[i], "entry_z": z, "a_entry": prices_a[i], "b_entry": prices_b[i], "a_qty": a_qty, "b_qty": b_qty}
            elif z < -z_entry:
                a_qty = int(size / prices_a[i]) if prices_a[i] > 0 else 0
                b_qty = int(size / prices_b[i]) if prices_b[i] > 0 else 0
                if a_qty > 0 and b_qty > 0:
                    pos = {"dir": "LONG_A", "entry_bar": i, "entry_date": dates[i], "entry_z": z, "a_entry": prices_a[i], "b_entry": prices_b[i], "a_qty": a_qty, "b_qty": b_qty}
    return trades


# ── Options backtest ─────────────────────────────────────────────────────────

def norm_cdf(x):
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1.0 if x >= 0 else -1.0
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2.0)
    return 0.5 * (1.0 + sign * y)


def bs_price(S, K, T, r, sigma, is_call):
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1)


def estimate_iv(prices_list, window=20):
    if len(prices_list) < window + 1:
        return 0.35
    returns = [(prices_list[i] / prices_list[i - 1] - 1) for i in range(len(prices_list) - window, len(prices_list))]
    if not returns:
        return 0.35
    std = statistics.stdev(returns) if len(returns) > 1 else 0.015
    return std * math.sqrt(252)


def strike_round(price, symbol):
    if price > 200:
        return round(price / 5) * 5
    elif price > 50:
        return round(price)
    else:
        return round(price * 2) / 2


def run_options_backtest(dates, pa, pb, sym_a, sym_b, lookback=15, z_entry=2.0,
                         z_exit=0.5, max_hold=30, budget=1000, dte=60, iv_disc=0.80, r=0.043):
    ratios = [pa[i] / pb[i] for i in range(len(dates))]
    zscores = compute_zscore(ratios, lookback)
    trades = []
    pos = None
    for i in range(lookback, len(dates)):
        z = zscores[i]
        if pos:
            hold = i - pos["entry_bar"]
            remaining_dte = max(1, pos["dte"] - hold)
            T_remain = remaining_dte / 365.0
            exit_reason = None
            if pos["dir"] == "SHORT_A":
                if z <= z_exit:
                    exit_reason = "REVERT"
                elif z >= 4.5:
                    exit_reason = "STOP"
                elif hold >= max_hold:
                    exit_reason = "MAX_HOLD"
            else:
                if z >= -z_exit:
                    exit_reason = "REVERT"
                elif z <= -4.5:
                    exit_reason = "STOP"
                elif hold >= max_hold:
                    exit_reason = "MAX_HOLD"
            if exit_reason:
                a_iv = estimate_iv(pa[max(0, i - 30):i + 1])
                b_iv = estimate_iv(pb[max(0, i - 30):i + 1])
                if pos["dir"] == "SHORT_A":
                    a_exit = bs_price(pa[i], pos["a_strike"], T_remain, r, a_iv, False)
                    b_exit = bs_price(pb[i], pos["b_strike"], T_remain, r, b_iv, True)
                else:
                    a_exit = bs_price(pa[i], pos["a_strike"], T_remain, r, a_iv, True)
                    b_exit = bs_price(pb[i], pos["b_strike"], T_remain, r, b_iv, False)
                a_pnl = (a_exit - pos["a_opt"]) * pos["a_c"] * 100
                b_pnl = (b_exit - pos["b_opt"]) * pos["b_c"] * 100
                total = a_pnl + b_pnl
                cost = pos["cost"]
                trades.append({"dir": pos["dir"], "entry_date": pos["entry_date"], "exit_date": dates[i], "entry_z": pos["entry_z"], "exit_z": z, "hold": hold, "total_pnl": total, "cost": cost, "pnl_pct": (total / cost * 100) if cost > 0 else 0, "exit_reason": exit_reason, "a_move": (pa[i] - pos["a_entry"]) / pos["a_entry"] * 100, "b_move": (pb[i] - pos["b_entry"]) / pos["b_entry"] * 100, "remaining_dte": remaining_dte})
                pos = None
        if pos is None and abs(z) > z_entry:
            a_iv = estimate_iv(pa[max(0, i - 30):i + 1]) * iv_disc
            b_iv = estimate_iv(pb[max(0, i - 30):i + 1]) * iv_disc
            a_strike = strike_round(pa[i], sym_a)
            b_strike = strike_round(pb[i], sym_b)
            T = dte / 365.0
            if z > z_entry:
                a_opt = bs_price(pa[i], a_strike, T, r, a_iv, False)
                b_opt = bs_price(pb[i], b_strike, T, r, b_iv, True)
            else:
                a_opt = bs_price(pa[i], a_strike, T, r, a_iv, True)
                b_opt = bs_price(pb[i], b_strike, T, r, b_iv, False)
            if a_opt <= 0.15 or b_opt <= 0.10:
                continue
            a_c = max(1, int(budget / (a_opt * 100)))
            b_c = max(1, int(budget / (b_opt * 100)))
            cost = a_opt * a_c * 100 + b_opt * b_c * 100
            pos = {"dir": "SHORT_A" if z > z_entry else "LONG_A", "entry_bar": i, "entry_date": dates[i], "entry_z": z, "a_entry": pa[i], "b_entry": pb[i], "a_strike": a_strike, "b_strike": b_strike, "a_opt": a_opt, "b_opt": b_opt, "a_c": a_c, "b_c": b_c, "cost": cost, "dte": dte, "a_iv": a_iv, "b_iv": b_iv}
    return trades


def analyze(trades, label="", verbose=True):
    if not trades:
        return None
    pnls = [t["total_pnl"] for t in trades]
    total = sum(pnls)
    wr = len([p for p in pnls if p > 0]) / len(pnls) * 100
    sharpe = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0
    avg_hold = statistics.mean([t["hold"] for t in trades])
    pnl_pcts = [t["pnl_pct"] for t in trades]
    avg_ret = statistics.mean(pnl_pcts)
    has_cost = "cost" in trades[0]
    roi = 0
    if has_cost:
        total_cost = sum(t["cost"] for t in trades)
        roi = total / total_cost * 100 if total_cost > 0 else 0
    if verbose:
        by_reason = defaultdict(list)
        for t in trades:
            by_reason[t["exit_reason"]].append(t["total_pnl"])
        extra = f" | ROI: {roi:+.1f}%" if has_cost else ""
        print(f"  {label}")
        print(f"    Trades: {len(trades)} | P/L: ${total:+,.0f} | WR: {wr:.0f}% | Sharpe: {sharpe:.2f} | Avg ret: {avg_ret:+.2f}%{extra}")
        print(f"    Avg hold: {avg_hold:.1f}d | Max win: ${max(pnls):+,.0f} | Max loss: ${min(pnls):+,.0f}")
        for reason, pnl_list in sorted(by_reason.items()):
            wr_r = len([p for p in pnl_list if p > 0]) / len(pnl_list) * 100
            print(f"      {reason}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}, WR {wr_r:.0f}%")
    return {"sharpe": sharpe, "total_pnl": total, "n": len(trades), "wr": wr, "avg_ret": avg_ret, "roi": roi, "label": label, "avg_hold": avg_hold}


def main():
    print("=" * 100)
    print("GOLD SPREAD BACKTEST — Miners/Streamers vs Gold Spot ETF")
    print("=" * 100)
    # Load all symbols
    all_prices = {}
    for sym in ["NEM", "GLD", "GDX", "AEM", "RGLD", "WPM"]:
        all_prices[sym] = load_daily(sym)
        if all_prices[sym]:
            dates = sorted(all_prices[sym].keys())
            print(f"  {sym}: {len(dates)} bars ({dates[0]} -> {dates[-1]})")
        else:
            print(f"  {sym}: NO DATA")
    # ── PART 1: Underlying pairs ──────────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("PART 1: UNDERLYING PAIRS TRADE")
    print(f"{'=' * 100}")
    underlying_winners = {}
    for sym_a, sym_b, pair_name in PAIRS:
        pa_map = all_prices.get(sym_a, {})
        pb_map = all_prices.get(sym_b, {})
        if not pa_map or not pb_map:
            print(f"\n--- {pair_name}: MISSING DATA ---")
            continue
        common = sorted(set(pa_map.keys()) & set(pb_map.keys()))
        if len(common) < 50:
            print(f"\n--- {pair_name}: Only {len(common)} overlapping days, need 50+ ---")
            continue
        pa = [pa_map[d] for d in common]
        pb = [pb_map[d] for d in common]
        ratios = [pa[i] / pb[i] for i in range(len(common))]
        print(f"\n--- {pair_name} ({len(common)} days, {common[0]} -> {common[-1]}) ---")
        print(f"  Ratio: min={min(ratios):.3f} max={max(ratios):.3f} mean={statistics.mean(ratios):.3f} std={statistics.stdev(ratios):.3f}")
        # Recent IV
        if len(pa) > 30:
            iv_a = estimate_iv(pa[-30:])
            iv_b = estimate_iv(pb[-30:])
            print(f"  Recent IV: {sym_a}={iv_a:.0%} {sym_b}={iv_b:.0%}")
        results = []
        for lb in [10, 15, 20, 30, 40, 60]:
            for ze in [1.0, 1.5, 2.0, 2.5]:
                for zx in [0.0, 0.3, 0.5]:
                    for mh in [10, 20, 30]:
                        trades = run_pair_backtest(common, pa, pb, lookback=lb, z_entry=ze, z_exit=zx, max_hold=mh)
                        if trades and len(trades) >= 3:
                            label = f"LB={lb:>2} Ze={ze} Zx={zx} MH={mh:>2}"
                            r = analyze(trades, label, verbose=False)
                            if r:
                                results.append(r)
        if results:
            results.sort(key=lambda x: x["sharpe"], reverse=True)
            print(f"  TOP 5 BY SHARPE:")
            for r in results[:5]:
                print(f"    Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+8,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.2f}% Hold={r['avg_hold']:>4.1f}d  {r['label']}")
            results.sort(key=lambda x: x["total_pnl"], reverse=True)
            print(f"  TOP 5 BY P/L:")
            for r in results[:5]:
                print(f"    P/L=${r['total_pnl']:>+8,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}%  {r['label']}")
            underlying_winners[pair_name] = results[0]
        else:
            print(f"  NO VIABLE CONFIGS (no config produced 3+ trades)")
    # ── PART 2: Options ───────────────────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("PART 2: OPTIONS SPREAD")
    print(f"{'=' * 100}")
    options_winners = {}
    for sym_a, sym_b, pair_name in PAIRS:
        pa_map = all_prices.get(sym_a, {})
        pb_map = all_prices.get(sym_b, {})
        if not pa_map or not pb_map:
            continue
        common = sorted(set(pa_map.keys()) & set(pb_map.keys()))
        if len(common) < 50:
            continue
        pa = [pa_map[d] for d in common]
        pb = [pb_map[d] for d in common]
        print(f"\n--- {pair_name} OPTIONS ---")
        results = []
        for lb in [10, 15, 20, 30, 40]:
            for ze in [1.5, 2.0, 2.5]:
                for zx in [0.3, 0.5]:
                    for dte_v in [30, 45, 60]:
                        for iv_d in [0.80, 0.85]:
                            label = f"LB={lb:>2} Z={ze} Zx={zx} DTE={dte_v:>2} IV={iv_d}"
                            trades = run_options_backtest(common, pa, pb, sym_a, sym_b, lookback=lb, z_entry=ze, z_exit=zx, dte=dte_v, iv_disc=iv_d, budget=1000)
                            if trades and len(trades) >= 3:
                                r = analyze(trades, label, verbose=False)
                                if r:
                                    results.append(r)
        if results:
            results.sort(key=lambda x: x["sharpe"], reverse=True)
            print(f"  TOP 10 BY SHARPE:")
            for r in results[:10]:
                print(f"    Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+8,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.1f}% ROI={r['roi']:>+6.1f}%  {r['label']}")
            results.sort(key=lambda x: x["total_pnl"], reverse=True)
            print(f"  TOP 5 BY P/L:")
            for r in results[:5]:
                print(f"    P/L=${r['total_pnl']:>+8,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% ROI={r['roi']:>+6.1f}%  {r['label']}")
            best = [r for r in results if r["sharpe"] > 0.2 and r["wr"] > 50 and r["total_pnl"] > 0 and r["n"] >= 5]
            if best:
                best.sort(key=lambda x: x["sharpe"] * x["roi"], reverse=True)
                print(f"  BEST BALANCED:")
                for r in best[:5]:
                    print(f"    Sharpe={r['sharpe']:>6.2f} WR={r['wr']:>4.0f}% P/L=${r['total_pnl']:>+8,.0f} ROI={r['roi']:>+6.1f}% Trades={r['n']:>3}  {r['label']}")
            options_winners[pair_name] = results[0]
        else:
            print(f"  NO VIABLE CONFIGS")
    # ── PART 3: Trade log for best of each ────────────────────────────────────
    for sym_a, sym_b, pair_name in PAIRS:
        if pair_name not in options_winners:
            continue
        pa_map = all_prices.get(sym_a, {})
        pb_map = all_prices.get(sym_b, {})
        common = sorted(set(pa_map.keys()) & set(pb_map.keys()))
        pa = [pa_map[d] for d in common]
        pb = [pb_map[d] for d in common]
        best = options_winners[pair_name]
        parts = best["label"].split()
        lb = int(parts[0].split("=")[1])
        ze = float(parts[1].split("=")[1])
        zx = float(parts[2].split("=")[1])
        dte_v = int(parts[3].split("=")[1])
        iv_d = float(parts[4].split("=")[1])
        print(f"\n{'=' * 100}")
        print(f"TRADE LOG — {pair_name} — {best['label']}")
        print(f"{'=' * 100}")
        trades = run_options_backtest(common, pa, pb, sym_a, sym_b, lookback=lb, z_entry=ze, z_exit=zx, dte=dte_v, iv_disc=iv_d, budget=1000)
        cum = 0
        for t in trades:
            cum += t["total_pnl"]
            print(f"  {t['entry_date']} -> {t['exit_date']} | {t['dir']:<8} | z: {t['entry_z']:>+5.2f}->{t['exit_z']:>+5.2f} | {t['exit_reason']:<9} | Cost: ${t['cost']:>6,.0f} | P/L: ${t['total_pnl']:>+7,.0f} ({t['pnl_pct']:>+5.1f}%) | {sym_a} {t['a_move']:>+5.1f}% {sym_b} {t['b_move']:>+5.1f}% | Cum: ${cum:>+8,.0f}")
    # ── PART 4: Cross-pair comparison ─────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("CROSS-PAIR COMPARISON — Best options config per pair")
    print(f"{'=' * 100}")
    print(f"  {'Pair':<12} {'Sharpe':>7} {'P/L':>10} {'Trades':>7} {'WR':>5} {'AvgPnL%':>9} {'ROI':>7} {'Config'}")
    print(f"  {'-' * 85}")
    for _, _, pair_name in PAIRS:
        if pair_name in options_winners:
            r = options_winners[pair_name]
            print(f"  {pair_name:<12} {r['sharpe']:>7.2f} ${r['total_pnl']:>+8,.0f} {r['n']:>7} {r['wr']:>4.0f}% {r['avg_ret']:>+8.1f}% {r['roi']:>+6.1f}%  {r['label']}")


if __name__ == "__main__":
    main()
