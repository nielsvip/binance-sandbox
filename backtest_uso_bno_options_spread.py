#!/usr/bin/env python3
"""
USO/BNO Options Spread Backtest — buy underpriced options on divergence.

When USO/BNO ratio z-score > threshold:
  → Buy USO puts (USO overextended, will drop relative to BNO)
  → Buy BNO calls (BNO underperforming, will catch up)
When z-score < -threshold:
  → Buy USO calls
  → Buy BNO puts

Uses BS model to estimate option P/L from underlying moves.
Options advantage: defined risk (max loss = premium), leverage on the reversion.
"""
import json
import math
import statistics
import sys
from pathlib import Path
from collections import defaultdict

BASE = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE))


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
    """Estimate annualized IV from recent price returns."""
    if len(prices) < window + 1:
        return 0.30
    returns = [(prices[i] / prices[i-1] - 1) for i in range(len(prices)-window, len(prices))]
    if not returns:
        return 0.30
    std = statistics.stdev(returns) if len(returns) > 1 else 0.01
    # Annualize: hourly bars → sqrt(252 * 6.5) ≈ 40.5 trading hours/year
    return std * math.sqrt(252 * 6.5)


def load_1h(symbol):
    with open(BASE / "klines_cache" / "tradier" / f"{symbol}_1h.json") as f:
        return json.load(f)


def run_options_backtest(aligned, uso_prices, bno_prices, lookback=100, z_entry=2.5,
                         z_exit=0.3, max_hold_bars=200, budget_per_leg=1000,
                         dte_target=30, iv_discount=0.85, r=0.043):
    """
    At entry: buy ATM options with ~30 DTE, priced at IV_discount * realized_IV.
    At exit: reprice with new underlying + remaining DTE.
    P/L = (exit_option_price - entry_option_price) * contracts * 100
    """
    ratios = [a["ratio"] for a in aligned]
    # Z-scores
    zscores = []
    for i in range(len(ratios)):
        if i < lookback:
            zscores.append(0)
            continue
        w = ratios[i-lookback:i]
        m = statistics.mean(w)
        s = statistics.stdev(w) if len(w) > 1 else 0.001
        zscores.append((ratios[i] - m) / max(s, 0.0001))

    trades = []
    position = None

    for i in range(lookback, len(aligned)):
        z = zscores[i]
        bar = aligned[i]

        # Exit check
        if position:
            hold_bars = i - position["entry_bar"]
            remaining_dte = max(1, position["dte_at_entry"] - hold_bars / 6.5)  # 6.5 trading hours/day
            T_remain = remaining_dte / 365.0

            exit_reason = None
            if position["direction"] == "SHORT_SPREAD":
                if z <= z_exit:
                    exit_reason = "REVERT"
                elif z >= 4.0:
                    exit_reason = "STOP"
                elif hold_bars >= max_hold_bars:
                    exit_reason = "MAX_HOLD"
            else:
                if z >= -z_exit:
                    exit_reason = "REVERT"
                elif z <= -4.0:
                    exit_reason = "STOP"
                elif hold_bars >= max_hold_bars:
                    exit_reason = "MAX_HOLD"

            if exit_reason:
                # Reprice options at exit
                uso_now = bar["uso"]
                bno_now = bar["bno"]
                uso_iv_now = estimate_iv(uso_prices[max(0,i-30):i+1])
                bno_iv_now = estimate_iv(bno_prices[max(0,i-30):i+1])

                if position["direction"] == "SHORT_SPREAD":
                    # Held USO puts + BNO calls
                    uso_put_exit = bs_price(uso_now, position["uso_strike"], T_remain, r, uso_iv_now, False)
                    bno_call_exit = bs_price(bno_now, position["bno_strike"], T_remain, r, bno_iv_now, True)
                else:
                    # Held USO calls + BNO puts
                    uso_put_exit = bs_price(uso_now, position["uso_strike"], T_remain, r, uso_iv_now, True)
                    bno_call_exit = bs_price(bno_now, position["bno_strike"], T_remain, r, bno_iv_now, False)

                uso_pnl = (uso_put_exit - position["uso_option_entry"]) * position["uso_contracts"] * 100
                bno_pnl = (bno_call_exit - position["bno_option_entry"]) * position["bno_contracts"] * 100
                total_pnl = uso_pnl + bno_pnl
                cost = position["total_cost"]

                trades.append({
                    "direction": position["direction"],
                    "entry_time": position["entry_time"],
                    "exit_time": bar["ts"],
                    "entry_z": position["entry_z"],
                    "exit_z": z,
                    "hold_bars": hold_bars,
                    "hold_days": hold_bars / 6.5,
                    "uso_pnl": uso_pnl,
                    "bno_pnl": bno_pnl,
                    "total_pnl": total_pnl,
                    "cost": cost,
                    "pnl_pct": (total_pnl / cost * 100) if cost > 0 else 0,
                    "exit_reason": exit_reason,
                    "uso_move": (bar["uso"] - position["uso_entry"]) / position["uso_entry"] * 100,
                    "bno_move": (bar["bno"] - position["bno_entry"]) / position["bno_entry"] * 100,
                    "theta_drag_days": hold_bars / 6.5,
                    "remaining_dte": remaining_dte
                })
                position = None

        # Entry check
        if position is None and abs(z) > z_entry:
            uso_now = bar["uso"]
            bno_now = bar["bno"]

            # Estimate IVs (use realized vol as proxy, apply discount for "underpriced" options)
            uso_iv = estimate_iv(uso_prices[max(0,i-30):i+1]) * iv_discount
            bno_iv = estimate_iv(bno_prices[max(0,i-30):i+1]) * iv_discount

            # Set strikes ATM
            uso_strike = round(uso_now)
            bno_strike = round(bno_now)
            T = dte_target / 365.0

            if z > z_entry:
                # SHORT_SPREAD: buy USO puts + BNO calls
                uso_opt_price = bs_price(uso_now, uso_strike, T, r, uso_iv, False)
                bno_opt_price = bs_price(bno_now, bno_strike, T, r, bno_iv, True)
            else:
                # LONG_SPREAD: buy USO calls + BNO puts
                uso_opt_price = bs_price(uso_now, uso_strike, T, r, uso_iv, True)
                bno_opt_price = bs_price(bno_now, bno_strike, T, r, bno_iv, False)

            if uso_opt_price <= 0.10 or bno_opt_price <= 0.10:
                continue

            uso_contracts = max(1, int(budget_per_leg / (uso_opt_price * 100)))
            bno_contracts = max(1, int(budget_per_leg / (bno_opt_price * 100)))
            total_cost = uso_opt_price * uso_contracts * 100 + bno_opt_price * bno_contracts * 100

            position = {
                "direction": "SHORT_SPREAD" if z > z_entry else "LONG_SPREAD",
                "entry_bar": i,
                "entry_time": bar["ts"],
                "entry_z": z,
                "uso_entry": uso_now,
                "bno_entry": bno_now,
                "uso_strike": uso_strike,
                "bno_strike": bno_strike,
                "uso_option_entry": uso_opt_price,
                "bno_option_entry": bno_opt_price,
                "uso_contracts": uso_contracts,
                "bno_contracts": bno_contracts,
                "total_cost": total_cost,
                "dte_at_entry": dte_target,
                "uso_iv": uso_iv,
                "bno_iv": bno_iv
            }

    return trades


def analyze(trades, label=""):
    if not trades:
        print(f"  {label}: NO TRADES")
        return None
    pnls = [t["total_pnl"] for t in trades]
    costs = [t["cost"] for t in trades]
    total = sum(pnls)
    total_cost = sum(costs)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    wr = len(winners) / len(pnls) * 100
    sh = (statistics.mean(pnls) / statistics.stdev(pnls)) if len(pnls) > 1 and statistics.stdev(pnls) > 0 else 0
    avg_hold = statistics.mean([t["hold_days"] for t in trades])
    avg_pnl_pct = statistics.mean([t["pnl_pct"] for t in trades])
    max_loss = min(pnls)
    max_win = max(pnls)
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["total_pnl"])

    print(f"  {label}")
    print(f"    Trades: {len(trades)} | P/L: ${total:+,.0f} | WR: {wr:.0f}% | Sharpe: {sh:.2f} | Avg P/L%: {avg_pnl_pct:+.1f}%")
    print(f"    Avg hold: {avg_hold:.1f}d | Max win: ${max_win:+,.0f} | Max loss: ${max_loss:+,.0f} | Total invested: ${total_cost:,.0f}")
    for reason, pnl_list in sorted(by_reason.items()):
        print(f"      {reason}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}, WR {len([p for p in pnl_list if p > 0])/len(pnl_list)*100:.0f}%")
    return {"sharpe": sh, "total_pnl": total, "n": len(trades), "wr": wr, "avg_pnl_pct": avg_pnl_pct, "label": label}


def main():
    print("Loading 1h data...")
    uso = load_1h("USO")
    bno = load_1h("BNO")

    bno_map = {b["timestamp"]: b for b in bno}
    aligned = []
    uso_prices = []
    bno_prices = []
    for u in uso:
        ts = u["timestamp"]
        if ts in bno_map:
            b = bno_map[ts]
            if b["close"] > 0:
                aligned.append({"ts": ts, "uso": u["close"], "bno": b["close"], "ratio": u["close"]/b["close"], "date": ts[:10]})
                uso_prices.append(u["close"])
                bno_prices.append(b["close"])

    print(f"Aligned: {len(aligned)} bars ({aligned[0]['ts'][:10]} → {aligned[-1]['ts'][:10]})")
    print(f"\n{'='*90}")
    print(f"OPTIONS SPREAD SWEEP — USO puts/calls + BNO calls/puts on ratio divergence")
    print(f"{'='*90}\n")

    results = []
    for lb in [50, 100, 200]:
        for z_entry in [2.0, 2.5, 3.0]:
            for z_exit in [0.0, 0.3, 0.5]:
                for dte in [21, 45, 60]:
                    for iv_disc in [0.80, 0.85, 0.90]:
                        for budget in [500, 1000]:
                            label = f"LB={lb:>3} Z={z_entry} Zx={z_exit} DTE={dte:>2} IV={iv_disc} ${budget}"
                            trades = run_options_backtest(
                                aligned, uso_prices, bno_prices,
                                lookback=lb, z_entry=z_entry, z_exit=z_exit,
                                dte_target=dte, iv_discount=iv_disc,
                                budget_per_leg=budget
                            )
                            if trades and len(trades) >= 5:
                                r = analyze(trades, label)
                                if r:
                                    results.append(r)

    results.sort(key=lambda x: x["sharpe"], reverse=True)
    print(f"\n{'='*90}")
    print(f"TOP 15 BY SHARPE (min 5 trades)")
    print(f"{'='*90}")
    for r in results[:15]:
        print(f"  Sharpe={r['sharpe']:>5.2f} P/L=${r['total_pnl']:>+7,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% Avg={r['avg_pnl_pct']:>+5.1f}%  {r['label']}")

    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    print(f"\nTOP 15 BY P/L")
    for r in results[:15]:
        print(f"  P/L=${r['total_pnl']:>+7,.0f} Sharpe={r['sharpe']:>5.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% Avg={r['avg_pnl_pct']:>+5.1f}%  {r['label']}")


if __name__ == "__main__":
    main()
