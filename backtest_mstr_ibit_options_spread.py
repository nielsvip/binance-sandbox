#!/usr/bin/env python3
"""
MSTR/IBIT Options Spread Backtest — buy underpriced options on BTC exposure divergence.

When MSTR/IBIT ratio z-score > threshold (MSTR premium overextended):
  -> Buy MSTR puts (MSTR will drop relative to IBIT)
  -> Buy IBIT calls (IBIT underperforming, will catch up)
When z-score < -threshold (MSTR discount overextended):
  -> Buy MSTR calls
  -> Buy IBIT puts

Uses BS model to estimate option P/L from underlying moves.
Options advantage: defined risk (max loss = premium), leverage on the reversion.

Key differences from USO/BNO:
  - MSTR is MUCH more volatile (~3-4x IBIT daily moves)
  - MSTR IV is structurally higher -> options more expensive -> need wider z-scores
  - Premium/discount swings can be 20-50% -> bigger moves per trade
  - IBIT is newer (Jan 2024) -> less data but very liquid options
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
    if len(prices) < window + 1:
        return 0.50  # MSTR/IBIT baseline higher than oil
    returns = [(prices[i] / prices[i-1] - 1) for i in range(len(prices) - window, len(prices))]
    if not returns:
        return 0.50
    std = statistics.stdev(returns) if len(returns) > 1 else 0.02
    return std * math.sqrt(252)  # Daily bars -> annualize


def load_daily(symbol):
    path = BASE / "klines_cache" / "tradier" / f"{symbol}_D.json"
    with open(path) as f:
        bars = json.load(f)
    for b in bars:
        ts = b["timestamp"]
        if "T" in ts:
            ts = ts.replace("T", " ")
        b["timestamp"] = ts[:10]
    return bars


def run_options_backtest(aligned, mstr_prices, ibit_prices, lookback=20, z_entry=1.5,
                         z_exit=0.3, max_hold_days=30, budget_per_leg=1000,
                         dte_target=45, iv_discount=0.85, r=0.043):
    ratios = [a["ratio"] for a in aligned]
    zscores = []
    for i in range(len(ratios)):
        if i < lookback:
            zscores.append(0)
            continue
        w = ratios[i - lookback:i]
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
            hold = i - position["entry_bar"]
            remaining_dte = max(1, position["dte_at_entry"] - hold)
            T_remain = remaining_dte / 365.0
            exit_reason = None
            if position["direction"] == "SHORT_MSTR":
                if z <= z_exit:
                    exit_reason = "REVERT"
                elif z >= 4.5:
                    exit_reason = "STOP"
                elif hold >= max_hold_days:
                    exit_reason = "MAX_HOLD"
            else:
                if z >= -z_exit:
                    exit_reason = "REVERT"
                elif z <= -4.5:
                    exit_reason = "STOP"
                elif hold >= max_hold_days:
                    exit_reason = "MAX_HOLD"
            if exit_reason:
                mstr_now = bar["mstr"]
                ibit_now = bar["ibit"]
                mstr_iv_now = estimate_iv(mstr_prices[max(0, i - 30):i + 1])
                ibit_iv_now = estimate_iv(ibit_prices[max(0, i - 30):i + 1])
                if position["direction"] == "SHORT_MSTR":
                    mstr_exit = bs_price(mstr_now, position["mstr_strike"], T_remain, r, mstr_iv_now, False)
                    ibit_exit = bs_price(ibit_now, position["ibit_strike"], T_remain, r, ibit_iv_now, True)
                else:
                    mstr_exit = bs_price(mstr_now, position["mstr_strike"], T_remain, r, mstr_iv_now, True)
                    ibit_exit = bs_price(ibit_now, position["ibit_strike"], T_remain, r, ibit_iv_now, False)
                mstr_pnl = (mstr_exit - position["mstr_opt_entry"]) * position["mstr_contracts"] * 100
                ibit_pnl = (ibit_exit - position["ibit_opt_entry"]) * position["ibit_contracts"] * 100
                total_pnl = mstr_pnl + ibit_pnl
                cost = position["total_cost"]
                trades.append({
                    "direction": position["direction"],
                    "entry_date": position["entry_date"],
                    "exit_date": bar["date"],
                    "entry_z": position["entry_z"],
                    "exit_z": z,
                    "hold_days": hold,
                    "mstr_pnl": mstr_pnl,
                    "ibit_pnl": ibit_pnl,
                    "total_pnl": total_pnl,
                    "cost": cost,
                    "pnl_pct": (total_pnl / cost * 100) if cost > 0 else 0,
                    "exit_reason": exit_reason,
                    "mstr_move": (bar["mstr"] - position["mstr_entry"]) / position["mstr_entry"] * 100,
                    "ibit_move": (bar["ibit"] - position["ibit_entry"]) / position["ibit_entry"] * 100,
                    "remaining_dte": remaining_dte,
                    "mstr_iv_entry": position["mstr_iv"],
                    "ibit_iv_entry": position["ibit_iv"],
                })
                position = None
        # Entry check
        if position is None and abs(z) > z_entry:
            mstr_now = bar["mstr"]
            ibit_now = bar["ibit"]
            mstr_iv = estimate_iv(mstr_prices[max(0, i - 30):i + 1]) * iv_discount
            ibit_iv = estimate_iv(ibit_prices[max(0, i - 30):i + 1]) * iv_discount
            # MSTR strikes round to $5, IBIT to $1
            mstr_strike = round(mstr_now / 5) * 5
            ibit_strike = round(ibit_now)
            T = dte_target / 365.0
            if z > z_entry:
                mstr_opt = bs_price(mstr_now, mstr_strike, T, r, mstr_iv, False)
                ibit_opt = bs_price(ibit_now, ibit_strike, T, r, ibit_iv, True)
            else:
                mstr_opt = bs_price(mstr_now, mstr_strike, T, r, mstr_iv, True)
                ibit_opt = bs_price(ibit_now, ibit_strike, T, r, ibit_iv, False)
            if mstr_opt <= 0.20 or ibit_opt <= 0.10:
                continue
            mstr_contracts = max(1, int(budget_per_leg / (mstr_opt * 100)))
            ibit_contracts = max(1, int(budget_per_leg / (ibit_opt * 100)))
            total_cost = mstr_opt * mstr_contracts * 100 + ibit_opt * ibit_contracts * 100
            position = {
                "direction": "SHORT_MSTR" if z > z_entry else "LONG_MSTR",
                "entry_bar": i,
                "entry_date": bar["date"],
                "entry_z": z,
                "mstr_entry": mstr_now,
                "ibit_entry": ibit_now,
                "mstr_strike": mstr_strike,
                "ibit_strike": ibit_strike,
                "mstr_opt_entry": mstr_opt,
                "ibit_opt_entry": ibit_opt,
                "mstr_contracts": mstr_contracts,
                "ibit_contracts": ibit_contracts,
                "total_cost": total_cost,
                "dte_at_entry": dte_target,
                "mstr_iv": mstr_iv,
                "ibit_iv": ibit_iv,
            }
    return trades


def analyze(trades, label=""):
    if not trades:
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
    roi = total / total_cost * 100 if total_cost > 0 else 0
    by_reason = defaultdict(list)
    for t in trades:
        by_reason[t["exit_reason"]].append(t["total_pnl"])
    print(f"  {label}")
    print(f"    Trades: {len(trades)} | P/L: ${total:+,.0f} | WR: {wr:.0f}% | Sharpe: {sh:.2f} | Avg P/L%: {avg_pnl_pct:+.1f}% | ROI: {roi:+.1f}%")
    print(f"    Avg hold: {avg_hold:.1f}d | Invested: ${total_cost:,.0f}")
    for reason, pnl_list in sorted(by_reason.items()):
        wr_r = len([p for p in pnl_list if p > 0]) / len(pnl_list) * 100
        print(f"      {reason}: {len(pnl_list)} trades, ${sum(pnl_list):+,.0f}, WR {wr_r:.0f}%")
    return {"sharpe": sh, "total_pnl": total, "n": len(trades), "wr": wr, "avg_pnl_pct": avg_pnl_pct, "roi": roi, "label": label}


def main():
    print("=" * 100)
    print("MSTR/IBIT OPTIONS SPREAD BACKTEST — Buy puts/calls on BTC exposure divergence")
    print("=" * 100)
    mstr_bars = load_daily("MSTR")
    ibit_bars = load_daily("IBIT")
    ibit_map = {b["timestamp"]: b for b in ibit_bars}
    aligned = []
    mstr_prices = []
    ibit_prices = []
    for m in mstr_bars:
        ts = m["timestamp"]
        if ts in ibit_map:
            ib = ibit_map[ts]
            if ib["close"] > 0 and m["close"] > 0:
                aligned.append({"date": ts, "mstr": m["close"], "ibit": ib["close"], "ratio": m["close"] / ib["close"]})
                mstr_prices.append(m["close"])
                ibit_prices.append(ib["close"])
    print(f"Aligned: {len(aligned)} trading days ({aligned[0]['date']} -> {aligned[-1]['date']})")
    # Show IV estimates
    if len(mstr_prices) > 30:
        mstr_iv = estimate_iv(mstr_prices[-30:])
        ibit_iv = estimate_iv(ibit_prices[-30:])
        print(f"Recent IV estimates: MSTR={mstr_iv:.0%} | IBIT={ibit_iv:.0%}")
    print(f"\n{'=' * 100}")
    print("OPTIONS SWEEP — MSTR puts/calls + IBIT calls/puts on ratio divergence")
    print(f"{'=' * 100}\n")
    results = []
    for lb in [10, 15, 20, 30, 40, 60]:
        for z_entry in [1.0, 1.5, 2.0, 2.5]:
            for z_exit in [0.0, 0.3, 0.5]:
                for dte in [21, 30, 45, 60]:
                    for iv_disc in [0.80, 0.85, 0.90]:
                        for budget in [500, 750, 1000]:
                            label = f"LB={lb:>2} Z={z_entry} Zx={z_exit} DTE={dte:>2} IV={iv_disc} ${budget}"
                            trades = run_options_backtest(
                                aligned, mstr_prices, ibit_prices,
                                lookback=lb, z_entry=z_entry, z_exit=z_exit,
                                dte_target=dte, iv_discount=iv_disc,
                                budget_per_leg=budget
                            )
                            if trades and len(trades) >= 3:
                                r = analyze(trades, label)
                                if r:
                                    results.append(r)
    if not results:
        print("NO CONFIGS PRODUCED 3+ TRADES")
        return
    results.sort(key=lambda x: x["sharpe"], reverse=True)
    print(f"\n{'=' * 100}")
    print(f"TOP 20 BY SHARPE (min 3 trades)")
    print(f"{'=' * 100}")
    for r in results[:20]:
        print(f"  Sharpe={r['sharpe']:>6.2f} P/L=${r['total_pnl']:>+8,.0f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgPnL={r['avg_pnl_pct']:>+6.1f}% ROI={r['roi']:>+6.1f}%  {r['label']}")
    results.sort(key=lambda x: x["total_pnl"], reverse=True)
    print(f"\nTOP 20 BY P/L")
    for r in results[:20]:
        print(f"  P/L=${r['total_pnl']:>+8,.0f} Sharpe={r['sharpe']:>6.2f} Trades={r['n']:>3} WR={r['wr']:>4.0f}% AvgPnL={r['avg_pnl_pct']:>+6.1f}% ROI={r['roi']:>+6.1f}%  {r['label']}")
    # Best balanced
    best = [r for r in results if r["sharpe"] > 0.2 and r["wr"] > 50 and r["total_pnl"] > 0 and r["n"] >= 5]
    if best:
        best.sort(key=lambda x: x["sharpe"] * x["roi"], reverse=True)
        print(f"\nBEST BALANCED (Sharpe>0.2, WR>50%, P/L>0, 5+ trades)")
        for r in best[:15]:
            print(f"  Sharpe={r['sharpe']:>6.2f} WR={r['wr']:>4.0f}% P/L=${r['total_pnl']:>+8,.0f} ROI={r['roi']:>+6.1f}% Trades={r['n']:>3}  {r['label']}")
    # Show trade log for top config
    if results:
        top = results[0]
        parts = top["label"].split()
        lb = int(parts[0].split("=")[1])
        ze = float(parts[1].split("=")[1])
        zx = float(parts[2].split("=")[1])
        dte = int(parts[3].split("=")[1])
        iv_d = float(parts[4].split("=")[1])
        budget = int(parts[5].lstrip("$"))
        print(f"\n{'=' * 100}")
        print(f"TRADE LOG — Top P/L: {top['label']}")
        print(f"{'=' * 100}")
        trades = run_options_backtest(aligned, mstr_prices, ibit_prices, lookback=lb,
                                      z_entry=ze, z_exit=zx, dte_target=dte,
                                      iv_discount=iv_d, budget_per_leg=budget)
        cum = 0
        for t in trades:
            cum += t["total_pnl"]
            print(f"  {t['entry_date']} -> {t['exit_date']} | {t['direction']:<12} | z: {t['entry_z']:>+5.2f}->{t['exit_z']:>+5.2f} | {t['exit_reason']:<9} | Cost: ${t['cost']:>6,.0f} | P/L: ${t['total_pnl']:>+7,.0f} ({t['pnl_pct']:>+5.1f}%) | MSTR {t['mstr_move']:>+5.1f}% IBIT {t['ibit_move']:>+5.1f}% | DTE left: {t['remaining_dte']:.0f} | Cum: ${cum:>+8,.0f}")


if __name__ == "__main__":
    main()
