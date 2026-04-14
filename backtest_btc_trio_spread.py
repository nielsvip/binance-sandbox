#!/usr/bin/env python3
"""
BTC Trio Spread Backtest — MSTR / IBIT / COIN pairs + triangle trades.

Three distinct pair dynamics:
  MSTR/IBIT = BTC holder premium vs BTC spot (NAV premium oscillation)
  COIN/IBIT = exchange revenue premium vs BTC spot (volume/sentiment cycle)
  COIN/MSTR = exchange vs holder (crypto industry premium rotation)

Plus triangle: when one is cheap vs BOTH others, take the 2-leg trade.

Tests underlying pairs (daily bars, dollar-neutral) and options versions.
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
    ("MSTR", "IBIT", "MSTR/IBIT"),
    ("COIN", "IBIT", "COIN/IBIT"),
    ("COIN", "MSTR", "COIN/MSTR"),
]


def load_daily(symbol):
    path = BASE / "klines_cache" / "tradier" / f"{symbol}_D.json"
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


def align_three(mstr, ibit, coin):
    """Align all three symbols by date, return list of dicts."""
    common_dates = sorted(set(mstr.keys()) & set(ibit.keys()) & set(coin.keys()))
    aligned = []
    for d in common_dates:
        aligned.append({"date": d, "MSTR": mstr[d], "IBIT": ibit[d], "COIN": coin[d]})
    return aligned


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


def run_pair_backtest(aligned, sym_a, sym_b, lookback=15, z_entry=2.0, z_exit=0.5,
                      z_stop=4.5, max_hold=30, size=5000):
    ratios = [bar[sym_a] / bar[sym_b] for bar in aligned]
    zscores = compute_zscore(ratios, lookback)
    trades = []
    pos = None
    for i in range(lookback, len(aligned)):
        bar = aligned[i]
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
                    a_pnl = (pos["a_entry"] - bar[sym_a]) * pos["a_qty"]
                    b_pnl = (bar[sym_b] - pos["b_entry"]) * pos["b_qty"]
                else:
                    a_pnl = (bar[sym_a] - pos["a_entry"]) * pos["a_qty"]
                    b_pnl = (pos["b_entry"] - bar[sym_b]) * pos["b_qty"]
                total = a_pnl + b_pnl
                trades.append({"dir": pos["dir"], "entry_date": pos["entry_date"], "exit_date": bar["date"], "entry_z": pos["entry_z"], "exit_z": z, "hold": hold, "a_pnl": a_pnl, "b_pnl": b_pnl, "total_pnl": total, "pnl_pct": total / (size * 2) * 100, "exit_reason": exit_reason, "a_move": (bar[sym_a] - pos["a_entry"]) / pos["a_entry"] * 100, "b_move": (bar[sym_b] - pos["b_entry"]) / pos["b_entry"] * 100})
                pos = None
        if pos is None:
            if z > z_entry:
                a_qty = int(size / bar[sym_a]) if bar[sym_a] > 0 else 0
                b_qty = int(size / bar[sym_b]) if bar[sym_b] > 0 else 0
                if a_qty > 0 and b_qty > 0:
                    pos = {"dir": "SHORT_A", "entry_bar": i, "entry_date": bar["date"], "entry_z": z, "a_entry": bar[sym_a], "b_entry": bar[sym_b], "a_qty": a_qty, "b_qty": b_qty}
            elif z < -z_entry:
                a_qty = int(size / bar[sym_a]) if bar[sym_a] > 0 else 0
                b_qty = int(size / bar[sym_b]) if bar[sym_b] > 0 else 0
                if a_qty > 0 and b_qty > 0:
                    pos = {"dir": "LONG_A", "entry_bar": i, "entry_date": bar["date"], "entry_z": z, "a_entry": bar[sym_a], "b_entry": bar[sym_b], "a_qty": a_qty, "b_qty": b_qty}
    return trades


def run_triangle_backtest(aligned, lookback=15, z_entry=2.0, z_exit=0.5, z_stop=4.5,
                          max_hold=30, size=5000):
    """Triangle: find the most extreme divergence across all 3 pairs, trade it.
    Only enter when one symbol is extreme vs BOTH others (confirming divergence).
    """
    # Compute z-scores for all 3 ratios
    r_mi = [bar["MSTR"] / bar["IBIT"] for bar in aligned]
    r_ci = [bar["COIN"] / bar["IBIT"] for bar in aligned]
    r_cm = [bar["COIN"] / bar["MSTR"] for bar in aligned]
    z_mi = compute_zscore(r_mi, lookback)
    z_ci = compute_zscore(r_ci, lookback)
    z_cm = compute_zscore(r_cm, lookback)
    trades = []
    pos = None
    for i in range(lookback, len(aligned)):
        bar = aligned[i]
        zmi, zci, zcm = z_mi[i], z_ci[i], z_cm[i]
        if pos:
            hold = i - pos["entry_bar"]
            exit_reason = None
            # Use the z-score of the primary pair
            primary_z = {"MSTR/IBIT": zmi, "COIN/IBIT": zci, "COIN/MSTR": zcm}[pos["pair"]]
            if pos["dir"].startswith("SHORT"):
                if primary_z <= z_exit:
                    exit_reason = "REVERT"
                elif primary_z >= z_stop:
                    exit_reason = "STOP"
                elif hold >= max_hold:
                    exit_reason = "MAX_HOLD"
            else:
                if primary_z >= -z_exit:
                    exit_reason = "REVERT"
                elif primary_z <= -z_stop:
                    exit_reason = "STOP"
                elif hold >= max_hold:
                    exit_reason = "MAX_HOLD"
            if exit_reason:
                sym_a, sym_b = pos["sym_a"], pos["sym_b"]
                if pos["dir"].startswith("SHORT"):
                    a_pnl = (pos["a_entry"] - bar[sym_a]) * pos["a_qty"]
                    b_pnl = (bar[sym_b] - pos["b_entry"]) * pos["b_qty"]
                else:
                    a_pnl = (bar[sym_a] - pos["a_entry"]) * pos["a_qty"]
                    b_pnl = (pos["b_entry"] - bar[sym_b]) * pos["b_qty"]
                total = a_pnl + b_pnl
                trades.append({"pair": pos["pair"], "dir": pos["dir"], "entry_date": pos["entry_date"], "exit_date": bar["date"], "entry_z": pos["entry_z"], "exit_z": primary_z, "hold": hold, "total_pnl": total, "pnl_pct": total / (size * 2) * 100, "exit_reason": exit_reason})
                pos = None
        if pos is None:
            # Find the pair with strongest confirming divergence
            candidates = []
            # MSTR overextended vs IBIT AND vs COIN
            if zmi > z_entry and zcm < -z_entry * 0.5:
                candidates.append(("MSTR/IBIT", "MSTR", "IBIT", "SHORT_A", zmi))
            if zmi < -z_entry and zcm > z_entry * 0.5:
                candidates.append(("MSTR/IBIT", "MSTR", "IBIT", "LONG_A", zmi))
            # COIN overextended vs IBIT AND vs MSTR
            if zci > z_entry and zcm > z_entry * 0.5:
                candidates.append(("COIN/IBIT", "COIN", "IBIT", "SHORT_A", zci))
            if zci < -z_entry and zcm < -z_entry * 0.5:
                candidates.append(("COIN/IBIT", "COIN", "IBIT", "LONG_A", zci))
            # COIN overextended vs MSTR (less common)
            if zcm > z_entry and zci > z_entry * 0.5:
                candidates.append(("COIN/MSTR", "COIN", "MSTR", "SHORT_A", zcm))
            if zcm < -z_entry and zci < -z_entry * 0.5:
                candidates.append(("COIN/MSTR", "COIN", "MSTR", "LONG_A", zcm))
            if candidates:
                # Pick the most extreme
                candidates.sort(key=lambda x: abs(x[4]), reverse=True)
                pair, sym_a, sym_b, direction, entry_z = candidates[0]
                a_qty = int(size / bar[sym_a]) if bar[sym_a] > 0 else 0
                b_qty = int(size / bar[sym_b]) if bar[sym_b] > 0 else 0
                if a_qty > 0 and b_qty > 0:
                    pos = {"pair": pair, "sym_a": sym_a, "sym_b": sym_b, "dir": direction, "entry_bar": i, "entry_date": bar["date"], "entry_z": entry_z, "a_entry": bar[sym_a], "b_entry": bar[sym_b], "a_qty": a_qty, "b_qty": b_qty}
    return trades


def analyze(trades, label="", verbose=True):
    if not trades:
        if verbose:
            print(f"  {label}: NO TRADES")
        return None
    pnls = [t["total_pnl"] for t in trades]
    total = sum(pnls)
    winners = [p for p in pnls if p > 0]
    wr = len(winners) / len(pnls) * 100
    sharpe = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0
    avg_hold = statistics.mean([t.get("hold", t.get("hold_days", 0)) for t in trades])
    pnl_pcts = [t["pnl_pct"] for t in trades]
    avg_ret = statistics.mean(pnl_pcts) if pnl_pcts else 0
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["total_pnl"])
    if verbose:
        print(f"  {label}")
        print(f"    Trades: {len(trades)} | P/L: ${total:+,.0f} | WR: {wr:.0f}% | Sharpe: {sharpe:.2f} | Avg ret: {avg_ret:+.2f}%")
        print(f"    Avg hold: {avg_hold:.1f}d | Max win: ${max(pnls):+,.0f} | Max loss: ${min(pnls):+,.0f}")
        for reason, pnl_list in sorted(by_reason.items()):
            wr_r = len([p for p in pnl_list if p > 0]) / len(pnl_list) * 100
            print(f"      {reason}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}, WR {wr_r:.0f}%")
    return {"sharpe": sharpe, "total_pnl": total, "n": len(trades), "wr": wr, "avg_ret": avg_ret, "label": label, "avg_hold": avg_hold}


# ── Options version ─────────────────────────────────────────────────────────

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


def estimate_iv(prices, window=20):
    if len(prices) < window + 1:
        return 0.50
    returns = [(prices[i] / prices[i - 1] - 1) for i in range(len(prices) - window, len(prices))]
    if not returns:
        return 0.50
    std = statistics.stdev(returns) if len(returns) > 1 else 0.02
    return std * math.sqrt(252)


def strike_round(price, symbol):
    """Round to typical strike intervals per symbol."""
    if symbol == "IBIT":
        return round(price)
    elif symbol == "MSTR":
        return round(price / 5) * 5
    else:  # COIN
        return round(price / 5) * 5


def run_pair_options_backtest(aligned, sym_a, sym_b, prices_a, prices_b,
                              lookback=15, z_entry=2.0, z_exit=0.5, max_hold=30,
                              budget=1000, dte=60, iv_disc=0.80, r=0.043):
    ratios = [bar[sym_a] / bar[sym_b] for bar in aligned]
    zscores = compute_zscore(ratios, lookback)
    trades = []
    pos = None
    for i in range(lookback, len(aligned)):
        z = zscores[i]
        bar = aligned[i]
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
                a_now = bar[sym_a]
                b_now = bar[sym_b]
                a_iv = estimate_iv(prices_a[max(0, i - 30):i + 1])
                b_iv = estimate_iv(prices_b[max(0, i - 30):i + 1])
                if pos["dir"] == "SHORT_A":
                    a_exit = bs_price(a_now, pos["a_strike"], T_remain, r, a_iv, False)
                    b_exit = bs_price(b_now, pos["b_strike"], T_remain, r, b_iv, True)
                else:
                    a_exit = bs_price(a_now, pos["a_strike"], T_remain, r, a_iv, True)
                    b_exit = bs_price(b_now, pos["b_strike"], T_remain, r, b_iv, False)
                a_pnl = (a_exit - pos["a_opt"]) * pos["a_contracts"] * 100
                b_pnl = (b_exit - pos["b_opt"]) * pos["b_contracts"] * 100
                total = a_pnl + b_pnl
                cost = pos["cost"]
                trades.append({"dir": pos["dir"], "entry_date": pos["entry_date"], "exit_date": bar["date"], "entry_z": pos["entry_z"], "exit_z": z, "hold": hold, "total_pnl": total, "cost": cost, "pnl_pct": (total / cost * 100) if cost > 0 else 0, "exit_reason": exit_reason, "a_move": (a_now - pos["a_entry"]) / pos["a_entry"] * 100, "b_move": (b_now - pos["b_entry"]) / pos["b_entry"] * 100, "remaining_dte": remaining_dte})
                pos = None
        if pos is None and abs(z) > z_entry:
            a_now = bar[sym_a]
            b_now = bar[sym_b]
            a_iv = estimate_iv(prices_a[max(0, i - 30):i + 1]) * iv_disc
            b_iv = estimate_iv(prices_b[max(0, i - 30):i + 1]) * iv_disc
            a_strike = strike_round(a_now, sym_a)
            b_strike = strike_round(b_now, sym_b)
            T = dte / 365.0
            if z > z_entry:
                a_opt = bs_price(a_now, a_strike, T, r, a_iv, False)
                b_opt = bs_price(b_now, b_strike, T, r, b_iv, True)
            else:
                a_opt = bs_price(a_now, a_strike, T, r, a_iv, True)
                b_opt = bs_price(b_now, b_strike, T, r, b_iv, False)
            if a_opt <= 0.20 or b_opt <= 0.10:
                continue
            a_contracts = max(1, int(budget / (a_opt * 100)))
            b_contracts = max(1, int(budget / (b_opt * 100)))
            cost = a_opt * a_contracts * 100 + b_opt * b_contracts * 100
            pos = {"dir": "SHORT_A" if z > z_entry else "LONG_A", "entry_bar": i, "entry_date": bar["date"], "entry_z": z, "a_entry": a_now, "b_entry": b_now, "a_strike": a_strike, "b_strike": b_strike, "a_opt": a_opt, "b_opt": b_opt, "a_contracts": a_contracts, "b_contracts": b_contracts, "cost": cost, "dte": dte, "a_iv": a_iv, "b_iv": b_iv}
    return trades


def analyze_options(trades, label="", verbose=True):
    if not trades:
        if verbose:
            print(f"  {label}: NO TRADES")
        return None
    pnls = [t["total_pnl"] for t in trades]
    costs = [t["cost"] for t in trades]
    total = sum(pnls)
    total_cost = sum(costs)
    wr = len([p for p in pnls if p > 0]) / len(pnls) * 100
    sh = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0
    avg_hold = statistics.mean([t["hold"] for t in trades])
    avg_pnl_pct = statistics.mean([t["pnl_pct"] for t in trades])
    roi = total / total_cost * 100 if total_cost > 0 else 0
    if verbose:
        by_reason = defaultdict(list)
        for t in trades:
            by_reason[t["exit_reason"]].append(t["total_pnl"])
        print(f"  {label}")
        print(f"    Trades: {len(trades)} | P/L: ${total:+,.0f} | WR: {wr:.0f}% | Sharpe: {sh:.2f} | Avg P/L%: {avg_pnl_pct:+.1f}% | ROI: {roi:+.1f}%")
        print(f"    Avg hold: {avg_hold:.1f}d | Invested: ${total_cost:,.0f}")
        for reason, pnl_list in sorted(by_reason.items()):
            wr_r = len([p for p in pnl_list if p > 0]) / len(pnl_list) * 100
            print(f"      {reason}: {len(pnl_list)}, ${sum(pnl_list):+,.0f}, WR {wr_r:.0f}%")
    return {"sharpe": sh, "total_pnl": total, "n": len(trades), "wr": wr, "avg_pnl_pct": avg_pnl_pct, "roi": roi, "label": label, "avg_hold": avg_hold}


def main():
    print("=" * 100)
    print("BTC TRIO SPREAD BACKTEST — MSTR / IBIT / COIN")
    print("=" * 100)
    mstr_map = load_daily("MSTR")
    ibit_map = load_daily("IBIT")
    coin_map = load_daily("COIN")
    aligned = align_three(mstr_map, ibit_map, coin_map)
    print(f"Three-way aligned: {len(aligned)} trading days ({aligned[0]['date']} -> {aligned[-1]['date']})")
    # Show ratio stats
    for sym_a, sym_b, pair_name in PAIRS:
        ratios = [bar[sym_a] / bar[sym_b] for bar in aligned]
        print(f"  {pair_name}: min={min(ratios):.2f} max={max(ratios):.2f} mean={statistics.mean(ratios):.2f} std={statistics.stdev(ratios):.2f}")
    # ── PART 1: Underlying pairs ──────────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("PART 1: UNDERLYING PAIRS TRADE (all 3 pairs)")
    print(f"{'=' * 100}\n")
    all_results = {}
    for sym_a, sym_b, pair_name in PAIRS:
        print(f"\n--- {pair_name} ---")
        results = []
        for lb in [10, 15, 20, 30, 40]:
            for ze in [1.0, 1.5, 2.0, 2.5]:
                for zx in [0.0, 0.3, 0.5]:
                    for mh in [10, 20, 30]:
                        trades = run_pair_backtest(aligned, sym_a, sym_b, lookback=lb, z_entry=ze, z_exit=zx, max_hold=mh)
                        if trades and len(trades) >= 3:
                            label = f"LB={lb:>2} Ze={ze} Zx={zx} MH={mh:>2}"
                            r = analyze(trades, label, verbose=False)
                            if r:
                                results.append(r)
        if results:
            results.sort(key=lambda x: x["sharpe"], reverse=True)
            print(f"  TOP 5 BY SHARPE (min 3 trades):")
            for r in results[:5]:
                print(f"    Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+8,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.2f}% Hold={r['avg_hold']:>4.1f}d  {r['label']}")
            results.sort(key=lambda x: x["total_pnl"], reverse=True)
            print(f"  TOP 5 BY P/L:")
            for r in results[:5]:
                print(f"    P/L=${r['total_pnl']:>+8,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.2f}%  {r['label']}")
            all_results[pair_name] = results[0]  # best P/L
        else:
            print(f"  NO VIABLE CONFIGS")
    # ── PART 1b: Triangle ─────────────────────────────────────────────────────
    print(f"\n--- TRIANGLE (confirmed divergence across 2+ pairs) ---")
    tri_results = []
    for lb in [10, 15, 20, 30]:
        for ze in [1.5, 2.0, 2.5]:
            for zx in [0.3, 0.5]:
                for mh in [15, 20, 30]:
                    trades = run_triangle_backtest(aligned, lookback=lb, z_entry=ze, z_exit=zx, max_hold=mh)
                    if trades and len(trades) >= 3:
                        label = f"TRI LB={lb:>2} Ze={ze} Zx={zx} MH={mh:>2}"
                        r = analyze(trades, label, verbose=False)
                        if r:
                            tri_results.append(r)
    if tri_results:
        tri_results.sort(key=lambda x: x["sharpe"], reverse=True)
        print(f"  TOP 5 BY SHARPE:")
        for r in tri_results[:5]:
            print(f"    Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+8,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgRet={r['avg_ret']:>+6.2f}% Hold={r['avg_hold']:>4.1f}d  {r['label']}")
        tri_results.sort(key=lambda x: x["total_pnl"], reverse=True)
        print(f"  TOP 5 BY P/L:")
        for r in tri_results[:5]:
            print(f"    P/L=${r['total_pnl']:>+8,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}%  {r['label']}")
    # ── PART 2: Options pairs ─────────────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("PART 2: OPTIONS SPREAD (all 3 pairs)")
    print(f"{'=' * 100}\n")
    # Build price arrays indexed same as aligned
    prices = {}
    for sym in ["MSTR", "IBIT", "COIN"]:
        prices[sym] = [bar[sym] for bar in aligned]
    opt_all_results = {}
    for sym_a, sym_b, pair_name in PAIRS:
        print(f"\n--- {pair_name} OPTIONS ---")
        # Show IV
        if len(prices[sym_a]) > 30:
            iv_a = estimate_iv(prices[sym_a][-30:])
            iv_b = estimate_iv(prices[sym_b][-30:])
            print(f"  Recent IV: {sym_a}={iv_a:.0%} {sym_b}={iv_b:.0%}")
        results = []
        for lb in [10, 15, 20, 30]:
            for ze in [1.5, 2.0, 2.5]:
                for zx in [0.3, 0.5]:
                    for dte in [30, 45, 60]:
                        for iv_d in [0.80, 0.85]:
                            label = f"LB={lb:>2} Z={ze} Zx={zx} DTE={dte:>2} IV={iv_d}"
                            trades = run_pair_options_backtest(aligned, sym_a, sym_b, prices[sym_a], prices[sym_b], lookback=lb, z_entry=ze, z_exit=zx, dte=dte, iv_disc=iv_d, budget=1000)
                            if trades and len(trades) >= 3:
                                r = analyze_options(trades, label, verbose=False)
                                if r:
                                    results.append(r)
        if results:
            results.sort(key=lambda x: x["sharpe"], reverse=True)
            print(f"  TOP 10 BY SHARPE (min 3 trades):")
            for r in results[:10]:
                print(f"    Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+8,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgPnL={r['avg_pnl_pct']:>+6.1f}% ROI={r['roi']:>+6.1f}%  {r['label']}")
            results.sort(key=lambda x: x["total_pnl"], reverse=True)
            print(f"  TOP 10 BY P/L:")
            for r in results[:10]:
                print(f"    P/L=${r['total_pnl']:>+8,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgPnL={r['avg_pnl_pct']:>+6.1f}% ROI={r['roi']:>+6.1f}%  {r['label']}")
            # Best balanced
            best = [r for r in results if r["sharpe"] > 0.2 and r["wr"] > 50 and r["total_pnl"] > 0 and r["n"] >= 5]
            if best:
                best.sort(key=lambda x: x["sharpe"] * x["roi"], reverse=True)
                print(f"  BEST BALANCED (Sharpe>0.2, WR>50%, P/L>0, 5+ trades):")
                for r in best[:5]:
                    print(f"    Sharpe={r['sharpe']:>6.2f} WR={r['wr']:>4.0f}% P/L=${r['total_pnl']:>+8,.0f} ROI={r['roi']:>+6.1f}%  {r['label']}")
            opt_all_results[pair_name] = results[0]
        else:
            print(f"  NO VIABLE CONFIGS")
    # ── PART 3: Trade log for best of each pair ───────────────────────────────
    print(f"\n{'=' * 100}")
    print("PART 3: TRADE LOGS — Best config per pair")
    print(f"{'=' * 100}")
    for sym_a, sym_b, pair_name in PAIRS:
        if pair_name not in opt_all_results:
            continue
        best = opt_all_results[pair_name]
        parts = best["label"].split()
        lb = int(parts[0].split("=")[1])
        ze = float(parts[1].split("=")[1])
        zx = float(parts[2].split("=")[1])
        dte_v = int(parts[3].split("=")[1])
        iv_d = float(parts[4].split("=")[1])
        print(f"\n--- {pair_name} — {best['label']} ---")
        trades = run_pair_options_backtest(aligned, sym_a, sym_b, prices[sym_a], prices[sym_b], lookback=lb, z_entry=ze, z_exit=zx, dte=dte_v, iv_disc=iv_d, budget=1000)
        cum = 0
        for t in trades:
            cum += t["total_pnl"]
            print(f"  {t['entry_date']} -> {t['exit_date']} | {t['dir']:<8} | z: {t['entry_z']:>+5.2f}->{t['exit_z']:>+5.2f} | {t['exit_reason']:<9} | Cost: ${t['cost']:>6,.0f} | P/L: ${t['total_pnl']:>+7,.0f} ({t['pnl_pct']:>+5.1f}%) | {sym_a} {t['a_move']:>+5.1f}% {sym_b} {t['b_move']:>+5.1f}% | Cum: ${cum:>+8,.0f}")
    # ── PART 4: Cross-pair comparison ─────────────────────────────────────────
    print(f"\n{'=' * 100}")
    print("CROSS-PAIR COMPARISON — Best options config per pair")
    print(f"{'=' * 100}")
    print(f"  {'Pair':<12} {'Sharpe':>7} {'P/L':>10} {'Trades':>7} {'WR':>5} {'AvgPnL%':>9} {'ROI':>7} {'Config'}")
    print(f"  {'-'*80}")
    for _, _, pair_name in PAIRS:
        if pair_name in opt_all_results:
            r = opt_all_results[pair_name]
            print(f"  {pair_name:<12} {r['sharpe']:>7.2f} ${r['total_pnl']:>+8,.0f} {r['n']:>7} {r['wr']:>4.0f}% {r['avg_pnl_pct']:>+8.1f}% {r['roi']:>+6.1f}%  {r['label']}")


if __name__ == "__main__":
    main()
