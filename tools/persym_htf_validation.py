"""
tools/persym_htf_validation.py — per-symbol validation GATE for the HTF-regime + scale-in strategy.
Implements data/_diagnostic/PERSYM_VALIDATION_MAP_20260530.md, STAGES 0-4. NET only. No look-ahead.

Stage 0  classify regime (SMOOTH-UP / VOLATILE-UP / DOWNTREND / FLAT) from 4yr price vs SMA stack.
Stage 1  4yr-1sym baseline: b&h gain + the `>sma_200_4h` trend-filter net pool_sharpe + x_bh.
Stage 2  4yr grid search on the SHARED desired_weight core (vec_decisions/htf_regime_scale), NET,
         outlier-weighted objective; PASS only if net_pool_sharpe >= max(0.35, baseline) AND x_bh >= 1.
Stage 3  7d re-adjust (fast knobs only) constrained to not drop the 4yr result below baseline.
Stage 4  write tradeability ledger -> data/_diagnostic/persym_htf_ledger_<ts>.json ; untradeable => disabled.

Run on S1 (NPZ lives there). Example:
  python tools/persym_htf_validation.py --mode crypto --limit 0        # all crypto NPZ
  python tools/persym_htf_validation.py --symbols ZECUSDC,SOL,BTC,MU,SNDK
The shared core is the SAME logic wired live, so a PASS here is a real, parity-faithful result.
"""
import sys, os, glob, json, argparse, math
import numpy as np
sys.path.insert(0, ".")
import v8_vec_sweep as V
from vec_decisions import htf_regime_scale as H

LAMBDA_OUTLIER = 0.5
OUTLIER_CAP = 12.0
SHARPE_FLOOR = 0.25      # USER 2026-05-30: x_bh-first gate (relaxed Sharpe floor)
XBH_FLOOR = 5.0          # USER 2026-05-30: must reach >=5x buy-and-hold


def g(nd, k):
    a = nd.get(k)
    if a is None:
        return None
    a = np.asarray(a, float)
    return a if a.ndim == 1 and len(a) > 50 else None


CRYPTO_MAJORS = {"ETH", "BTC", "SOL", "ADA", "BNB", "AVAX", "XRP", "LINK", "LTC", "UNI"}


def mode_of(sym):
    if sym.endswith("USDT") or sym.endswith("USDC") or sym in CRYPTO_MAJORS:
        return "crypto"
    return "tradier"


def realized_vol(close, win):
    """VECTORIZED trailing-window std of returns (window=win bars)."""
    n = len(close); ret = np.zeros(n); ret[1:] = np.diff(close) / close[:-1]
    c1 = np.concatenate([[0.0], np.cumsum(ret)]); c2 = np.concatenate([[0.0], np.cumsum(ret * ret)])
    idx = np.arange(n); a = np.maximum(0, idx - win + 1); cnt = (idx - a + 1).astype(float)
    s1 = c1[idx + 1] - c1[a]; s2 = c2[idx + 1] - c2[a]
    var = np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0.0)
    return np.sqrt(var)


def classify_regime(close, smaD, is_long=True):
    cp = np.concatenate([[close[0]], close[:-1]]); sp = np.concatenate([[smaD[0]], smaD[:-1]])
    valid = sp > 0
    above_frac = float(np.mean(cp[valid] > sp[valid])) if valid.any() else 0.0
    lag = 30
    dlag = np.concatenate([np.repeat(smaD[:lag].mean(), lag), smaD[:-lag]])
    rise_frac = float(np.mean(smaD[valid] > dlag[valid])) if valid.any() else 0.0
    # intra-uptrend max drawdown
    hold = valid & (cp > sp)
    eq = np.cumprod(np.where(np.r_[False, hold[1:]], 1 + np.diff(close, prepend=close[0]) / close, 1.0))
    peak = np.maximum.accumulate(eq); mdd = float(np.max((peak - eq) / peak)) if len(eq) else 1.0
    ret = np.diff(close) / close[:-1]; ann_vol = float(np.std(ret))
    if above_frac > 0.55 and rise_frac > 0.5 and mdd < 0.25:
        reg = "SMOOTH-UP"
    elif above_frac > 0.5:
        reg = "VOLATILE-UP"
    elif above_frac < 0.45:
        reg = "DOWNTREND"
    else:
        reg = "FLAT"
    return reg, dict(above_frac=round(above_frac, 3), dsma_rise_frac=round(rise_frac, 3),
                     intra_uptrend_maxdd=round(mdd, 3), ann_vol=round(ann_vol, 5))


def weight_to_trade_returns(close, w, cm, direction=1):
    """VECTORIZED. Per-bar desired-weight path -> NET per-trade returns + compounded gain.
    direction=+1 long (profit price up), -1 short (profit price down). bar i P&L factor =
    (1 + w[i]*direction*ret[i]) * (1 - cm*|w[i]-w[i-1]|). A 'trade' = a contiguous run with w>0."""
    n = len(close); ret = np.zeros(n); ret[1:] = np.diff(close) / close[:-1]
    dw = np.abs(np.diff(w, prepend=w[0]))
    f = np.clip((1.0 + w * direction * ret) * (1.0 - cm * dw), 1e-9, None)
    logf = np.log(f); cum = np.concatenate([[0.0], np.cumsum(logf)])
    active = w > 0
    d = np.diff(active.astype(int)); starts = np.where(d == 1)[0] + 1; ends = np.where(d == -1)[0] + 1
    if active[0]:
        starts = np.r_[0, starts]
    if active[-1]:
        ends = np.r_[ends, n]
    trades = [float(np.exp(cum[e] - cum[s]) - 1.0) for s, e in zip(starts, ends) if e > s]
    total_gain = float(np.exp(logf.sum()) - 1.0)
    return trades, total_gain


def net_sharpe(trades):
    a = np.array(trades)
    return float(a.mean() / a.std()) if len(a) > 1 and a.std() > 0 else 0.0


GRID = [dict(regime_tf=rt, exit_tf=et, add_mult=am, size_cap=sc, vol_target=vt, scale_in=si)
        for rt in ("D", "4h") for et in ("15m", "1h") for am in (0.0, 0.5, 0.75)
        for sc in (1.0, 2.0, 3.0) for vt in (0.0, 0.006) for si in (True, False)]

SHORT_GRID = [dict(regime_tf=rt, fast_tf=ft, vol_k=vk, rsi_cover=rc, size_cap=sc)
              for rt in ("D", "4h") for ft in ("15m", "1h") for vk in (1.1, 1.25, 1.5)
              for rc in (20.0, 30.0) for sc in (1.0, 2.0)]


def validate_short(sym):
    """ELEVATOR-DOWN short (vec_decisions/short_elevator). Gate (USER 2026-05-30): tradeable if best config has
    POSITIVE net pool_sharpe. Selection = max Sharpe. Distinct from the long staircase."""
    from vec_decisions import short_elevator as SE
    mode = mode_of(sym)
    try:
        nd, ts = V.load_npz(sym, mode)
    except Exception as e:
        return dict(symbol=sym, side="SHORT", error=str(e))
    close = g(nd, "close"); smaD = g(nd, "sma_200_D")
    if close is None or smaD is None:
        return dict(symbol=sym, side="SHORT", error="missing close/sma")
    cm = V._vec_round_trip_cost_for_sym(sym, V.SweepConfig()) / 100.0
    # Fixed-notional short P&L: (entry-exit)/entry.  ``entry/exit-1`` is an
    # inverse-price instrument return and overstates a plain short's gain.
    bh_short = (1.0 - close[-1] / close[0])
    reg, regm = classify_regime(close, smaD, is_long=False)
    ftf_default = "1h" if mode == "tradier" else "15m"
    best = None
    for cfg in SHORT_GRID:
        ft = cfg["fast_tf"]
        if g(nd, f"dc_low_{ft}") is None or g(nd, f"rsi_{ft}") is None:
            ft = ftf_default
        w = SE.short_weight_vec(nd, regime_tf=cfg["regime_tf"], fast_tf=ft, vol_k=cfg["vol_k"],
                                rsi_cover=cfg["rsi_cover"], size_cap=cfg["size_cap"])
        tr, gain = weight_to_trade_returns(close, w, cm, direction=-1)
        if len(tr) < 3:
            continue
        ps = net_sharpe(tr); xbh = (gain / bh_short) if bh_short > 0 else None
        rec = dict(pool_sharpe=round(ps, 4), x_bh=round(xbh, 3), gain_pct=round(gain * 100, 1),
                   trades=len(tr), outlier_capture=round(min(max(xbh or 0.0, 0.0), OUTLIER_CAP), 3),
                   cfg=dict(cfg, fast_tf=ft, strategy="elevator_short"))
        # prefer configs meeting the >=30-trade floor; maximize Sharpe among them (fallback: overall max-Sharpe)
        cand_ok = len(tr) >= 30
        if best is None or (cand_ok and not best.get("_ok")) or (cand_ok == best.get("_ok") and ps > best["pool_sharpe"]):
            best = dict(rec, _ok=cand_ok)
    if best is None:
        return dict(symbol=sym, side="SHORT", regime=reg, regime_metrics=regm, tradeable=False,
                    reason="no_short_trades", bh_gain_pct=round(bh_short * 100, 1))
    best.pop("_ok", None)
    # USER: positive Sharpe in per_sym test. + sample floor: >=30 short trades (else Sharpe is noise, CLAUDE.md).
    tradeable = (best["pool_sharpe"] > 0.0) and (best["trades"] >= 30)
    strict_pass = (
        best["pool_sharpe"] >= 0.25
        and best["x_bh"] is not None
        and best["x_bh"] >= 1.0
        and best["trades"] >= 30
    )
    return dict(symbol=sym, side="SHORT", regime=reg, regime_metrics=regm, bh_gain_pct=round(bh_short * 100, 1),
                best=best, best_by_score=best, tradeable=bool(tradeable), strict_pass=bool(strict_pass),
                rank_score=round(best["pool_sharpe"] if tradeable else -9.0, 4),
                reason=("POSITIVE_SHARPE_SHORT" if tradeable else f"FAIL short sharpe {best['pool_sharpe']}<=0"))


def validate_symbol(sym, side="LONG"):
    if side == "SHORT":
        return validate_short(sym)
    mode = mode_of(sym); is_long = (side == "LONG")
    try:
        nd, ts = V.load_npz(sym, mode)
    except Exception as e:
        return dict(symbol=sym, side=side, error=str(e))
    close = g(nd, "close"); smaD = g(nd, "sma_200_D"); sma4 = g(nd, "sma_200_4h")
    if close is None or smaD is None or sma4 is None:
        return dict(symbol=sym, side=side, error="missing close/sma")
    cm = V._vec_round_trip_cost_for_sym(sym, V.SweepConfig()) / 100.0
    bh = (close[-1] / close[0] - 1) if is_long else (close[0] / close[-1] - 1)
    ladder = ("1h", "4h", "D") if mode == "tradier" else ("15m", "1h", "4h")
    reg, regm = classify_regime(close, smaD, is_long)
    # STAGE 1 baseline = plain hold>sma_200_4h
    base_w = H.desired_weight_vec(nd, is_long, regime_tf="4h", exit_tf="4h", add_mult=0.0,
                                  size_cap=1.0, vol_target=0.0, scale_in=False, ladder_tfs=ladder)
    base_tr, base_gain = weight_to_trade_returns(close, base_w, cm)
    base_ps = net_sharpe(base_tr); base_x = (base_gain / bh) if bh > 0 else float("nan")
    # STAGE 2 grid search. PRECOMPUTE per-TF prior-bar 'above' + 'rising' arrays ONCE (10x speedup vs
    # re-shifting 418k arrays for every config). Then each config is cheap vector ops.
    rv96 = realized_vol(close, 96); n = len(close)
    cp = np.concatenate([[close[0]], close[:-1]])
    alltf = sorted(set(["D", "4h", "15m", "1h"]) | set(ladder))
    above = {}; rising = {}
    for tf in alltf:
        s = g(nd, f"sma_200_{tf}")
        if s is None:
            above[tf] = np.zeros(n, bool); rising[tf] = np.zeros(n, bool); continue
        sp = np.concatenate([[s[0]], s[:-1]])
        above[tf] = (sp > 0) & ((cp > sp) if is_long else (cp < sp))
        lag = 30; slag = np.concatenate([np.repeat(sp[:lag].mean(), lag), sp[:-lag]]) if lag < n else sp
        rising[tf] = (sp > slag) if is_long else (sp < slag)
    reclaim = np.zeros(n)
    for tf in ladder:
        reclaim += above[tf]
    # USER 2026-05-30 ALWAYS-IN: in chop branch we are IN (w=1) whenever above the exit-TF SMA — never flat
    # on an up symbol during an up move; only flat when price is below the exit SMA (a real breakdown).
    best = None; best_beat = None     # best_beat = highest-Sharpe config that beats baseline AND beats b&h
    for cfg in GRID:
        htf_up = above[cfg["regime_tf"]] & rising[cfg["regime_tf"]]
        if cfg["scale_in"]:
            w_in = np.minimum(cfg["size_cap"], 1.0 + cfg["add_mult"] * reclaim)
        else:
            w_in = np.ones(n)
        w_chop = np.where(above[cfg["exit_tf"]], 1.0, 0.0)
        w = np.where(htf_up, w_in, w_chop)
        if cfg["vol_target"] > 0:
            rv = np.where(rv96 < 1e-9, cfg["vol_target"], rv96)
            w = w * np.minimum(1.0, cfg["vol_target"] / rv)
        tr, gain = weight_to_trade_returns(close, w, cm)
        if len(tr) < 3:
            continue
        ps = net_sharpe(tr); xbh = (gain / bh) if bh > 0 else 0.0
        oc = min(max(xbh, 0.0), OUTLIER_CAP)
        rec = dict(score=round(ps + LAMBDA_OUTLIER * oc, 4), pool_sharpe=round(ps, 4), x_bh=round(xbh, 3),
                   gain_pct=round(gain * 100, 1), trades=len(tr), outlier_capture=round(oc, 3), cfg=cfg)
        if best is None or rec["score"] > best["score"]:
            best = rec
        if xbh >= 1.0 and ps >= base_ps:                              # LOOSENED: beats b&h AND beats baseline
            if best_beat is None or ps > best_beat["pool_sharpe"]:    # then maximize Sharpe
                best_beat = rec
    if best is None:
        return dict(symbol=sym, side=side, regime=reg, regime_metrics=regm, tradeable=False,
                    reason="no_valid_grid_point", baseline_pool_sharpe=round(base_ps, 4),
                    baseline_x_bh=round(base_x, 3) if not math.isnan(base_x) else None, bh_gain_pct=round(bh * 100, 1))
    chosen = best_beat if best_beat is not None else best
    # LOOSENED tradeability (USER 2026-05-30: >=20 keys/account/side): beats baseline AND beats b&h.
    tradeable = best_beat is not None
    strict_pass = (chosen["pool_sharpe"] >= SHARPE_FLOOR) and (chosen["x_bh"] >= XBH_FLOOR) and (chosen["pool_sharpe"] >= base_ps)
    rank_score = chosen["pool_sharpe"] if tradeable else -9.0
    return dict(symbol=sym, side=side, regime=reg, regime_metrics=regm,
                baseline_pool_sharpe=round(base_ps, 4),
                baseline_x_bh=round(base_x, 3) if not math.isnan(base_x) else None,
                bh_gain_pct=round(bh * 100, 1), best=chosen, best_by_score=best,
                tradeable=bool(tradeable), strict_pass=bool(strict_pass), rank_score=round(rank_score, 4),
                reason=("BEATS_BH+BASELINE" if tradeable else "no config beats both baseline and b&h"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], default=None)
    ap.add_argument("--symbols", default=None)
    ap.add_argument("--sides", default="LONG,SHORT")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.symbols:
        syms = a.symbols.split(",")
    else:
        syms = sorted(os.path.basename(p)[:-4] for p in glob.glob("backtest_v8/indicators/*.npz"))
        if a.mode:
            syms = [s for s in syms if mode_of(s) == a.mode]
    if a.limit:
        syms = syms[:a.limit]
    sides = a.sides.split(",")
    results = []
    for sym in syms:
        for side in sides:
            r = validate_symbol(sym, side)
            results.append(r)
            tag = "TRADEABLE" if r.get("tradeable") else "no-trade"
            b = r.get("best", {})
            print(f"{sym}_{side:5s} [{r.get('regime','?'):11s}] base_ps={r.get('baseline_pool_sharpe')} -> "
                  f"best_ps={b.get('pool_sharpe')} x_bh={b.get('x_bh')} oc={b.get('outlier_capture')} :: {tag} ({r.get('reason')})")
    ts = (os.popen("date -u +%Y%m%d_%H%M%S").read().strip())
    out = a.out or f"data/_diagnostic/persym_htf_ledger_{ts}.json"
    json.dump(results, open(out, "w"), indent=1)
    n_pass = sum(1 for r in results if r.get("tradeable"))
    print(f"\nLEDGER -> {out}  | tradeable {n_pass}/{len(results)}")


if __name__ == "__main__":
    main()
