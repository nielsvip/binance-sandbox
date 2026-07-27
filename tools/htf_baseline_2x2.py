"""
tools/htf_baseline_2x2.py — "big sweep" baseline of OUR established strategy: 2 LONG variants + 2 SHORT
variants over every tradeable key (crypto + stocks), NET, no-lookahead. USER 2026-05-30.

Runs the SAME parity-proven cores that go live (vec_decisions/htf_regime_scale = staircase long,
vec_decisions/short_elevator = elevator short) — backtest_v8_engine does NOT implement this strategy, so the
harness IS the engine for it. Produces per-key per-variant baseline rows so per_sym can polish from them.

Variants (derived from the full-universe winning-config distribution):
  LONG-A  flat trend-ride : regime=D exit=15m vol_target=0.006 scale_in=False        (most common winner)
  LONG-B  scale-in bottoms: regime=D exit=15m add=0.75 cap=3 vol_target=0.006 scale_in=True (10x lever)
  SHORT-A strict elevator : regime=D  fast=15m vol_k=1.5 rsi_cover=30 size_cap=1
  SHORT-B faster elevator : regime=4h fast=15m vol_k=1.1 rsi_cover=30 size_cap=1
"""
import sys, os, json, csv, argparse
import numpy as np
sys.path.insert(0, ".")
import v8_vec_sweep as V
from vec_decisions import htf_regime_scale as H
from vec_decisions import short_elevator as SE
from tools.persym_htf_validation import g, mode_of, weight_to_trade_returns, net_sharpe, classify_regime

LONG_VARIANTS = [
    ("LONG-A_flat", dict(regime_tf="D", exit_tf="15m", add_mult=0.0, size_cap=1.0, vol_target=0.006, scale_in=False)),
    ("LONG-B_scalein", dict(regime_tf="D", exit_tf="15m", add_mult=0.75, size_cap=3.0, vol_target=0.006, scale_in=True)),
]
SHORT_VARIANTS = [
    ("SHORT-A_strict", dict(regime_tf="D", fast_tf="15m", vol_k=1.5, rsi_cover=30.0, size_cap=1.0)),
    ("SHORT-B_faster", dict(regime_tf="4h", fast_tf="15m", vol_k=1.1, rsi_cover=30.0, size_cap=1.0)),
]


def tradeable_keys(ledger_long, ledger_short):
    L = json.load(open(ledger_long)); S = json.load(open(ledger_short))
    longs = [r["symbol"] for r in L if r.get("side") == "LONG" and r.get("tradeable")]
    shorts = [r["symbol"] for r in S if r.get("side") == "SHORT" and r.get("tradeable")]
    return longs, shorts


def run_long(sym, cfg):
    mode = mode_of(sym)
    try:
        nd, ts = V.load_npz(sym, mode)
    except Exception:
        return None
    close = g(nd, "close")
    if close is None or g(nd, f"sma_200_{cfg['regime_tf']}") is None:
        return None
    cm = V._vec_round_trip_cost_for_sym(sym, V.SweepConfig()) / 100.0
    bh = close[-1] / close[0] - 1
    ladder = ("1h", "4h", "D") if mode == "tradier" else ("15m", "1h", "4h")
    rv = None
    if cfg["vol_target"] > 0:
        ret = np.zeros(len(close)); ret[1:] = np.diff(close) / close[:-1]
        rv = np.array([np.std(ret[max(0, i - 96):i + 1]) for i in range(0, len(close), 1)]) if len(close) < 5000 else _rollstd(close, 96)
    w = H.desired_weight_vec(nd, True, regime_tf=cfg["regime_tf"], exit_tf=cfg["exit_tf"], add_mult=cfg["add_mult"],
                             size_cap=cfg["size_cap"], vol_target=cfg["vol_target"], scale_in=cfg["scale_in"],
                             ladder_tfs=ladder, realized_vol=rv)
    tr, gain = weight_to_trade_returns(close, w, cm, direction=1)
    if len(tr) < 3:
        return None
    return dict(pool_sharpe=round(net_sharpe(tr), 4), x_bh=round(gain / bh, 3) if bh > 0 else 0.0,
                gain_pct=round(gain * 100, 1), trades=len(tr), n_years=round(len(close) / (96 if mode == "tradier" else 480) / 365.0, 2))


def _rollstd(close, win):
    n = len(close); ret = np.zeros(n); ret[1:] = np.diff(close) / close[:-1]
    c1 = np.concatenate([[0.0], np.cumsum(ret)]); c2 = np.concatenate([[0.0], np.cumsum(ret * ret)])
    i = np.arange(n); a = np.maximum(0, i - win + 1); cnt = (i - a + 1).astype(float)
    s1 = c1[i + 1] - c1[a]; s2 = c2[i + 1] - c2[a]
    return np.sqrt(np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0.0))


def run_short(sym, cfg):
    mode = mode_of(sym)
    try:
        nd, ts = V.load_npz(sym, mode)
    except Exception:
        return None
    close = g(nd, "close")
    if close is None or g(nd, "sma_200_D") is None:
        return None
    cm = V._vec_round_trip_cost_for_sym(sym, V.SweepConfig()) / 100.0
    # Dollar-PnL return of shorting one fixed initial notional.  The reciprocal
    # formula overstates gains after declines and is not a short-and-hold P&L.
    bh_short = 1.0 - close[-1] / close[0]
    ft = cfg["fast_tf"]
    if g(nd, f"dc_low_{ft}") is None or g(nd, f"rsi_{ft}") is None:
        ft = "1h" if mode == "tradier" else "15m"
    w = SE.short_weight_vec(nd, regime_tf=cfg["regime_tf"], fast_tf=ft, vol_k=cfg["vol_k"],
                            rsi_cover=cfg["rsi_cover"], size_cap=cfg["size_cap"])
    tr, gain = weight_to_trade_returns(close, w, cm, direction=-1)
    if len(tr) < 3:
        return None
    return dict(pool_sharpe=round(net_sharpe(tr), 4), x_bh=round(gain / bh_short, 3) if bh_short > 0 else None,
                gain_pct=round(gain * 100, 1), trades=len(tr), n_years=round(len(close) / (96 if mode == "tradier" else 480) / 365.0, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger-long", default="data/_diagnostic/persym_htf_ledger_full.json")
    ap.add_argument("--ledger-short", default="data/_diagnostic/persym_htf_short_full.json")
    ap.add_argument("--out", default="data/sweep_results/HTF_BASELINE_2x2.csv")
    a = ap.parse_args()
    longs, shorts = tradeable_keys(a.ledger_long, a.ledger_short)
    rows = []
    for sym in longs:
        for vname, cfg in LONG_VARIANTS:
            r = run_long(sym, cfg)
            if r:
                rows.append(dict(symbol=sym, side="LONG", variant=vname, **r))
    for sym in shorts:
        for vname, cfg in SHORT_VARIANTS:
            r = run_short(sym, cfg)
            if r:
                rows.append(dict(symbol=sym, side="SHORT", variant=vname, **r))
    cols = ["symbol", "side", "variant", "pool_sharpe", "x_bh", "gain_pct", "trades", "n_years"]
    with open(a.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow(r)
    # aggregate per (asset, side, variant)
    def isc(s): return s.endswith("USDT") or s.endswith("USDC") or s in {"ETH", "BTC", "SOL", "ADA", "BNB", "AVAX", "XRP", "LINK", "LTC", "UNI"}
    print("asset  side   variant            n   med_ps  med_xbh  med_trades")
    for asset, crit in [("crypto", True), ("stocks", False)]:
        for vname, _ in LONG_VARIANTS + SHORT_VARIANTS:
            sub = [r for r in rows if r["variant"] == vname and isc(r["symbol"]) == crit]
            if not sub:
                continue
            ps = np.median([r["pool_sharpe"] for r in sub]); xb = np.median([r["x_bh"] for r in sub]); tr = np.median([r["trades"] for r in sub])
            print("%-6s %-5s %-18s %3d  %6.3f  %6.2f  %8d" % (asset, sub[0]["side"], vname, len(sub), ps, xb, tr))
    print(f"\nROWS={len(rows)} -> {a.out}")


if __name__ == "__main__":
    main()
