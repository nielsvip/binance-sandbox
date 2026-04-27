#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""backtest_local_advisory.py — directional-only backtest of local_advisory_generator.py
decision logic vs a no-advisory baseline.

READ-ONLY: does NOT modify any live code. Imports nothing from ez_*/tradier_*/v8_*.

Sharpe rules (CLAUDE.md):
  pool_sharpe = mean(returns)/std(returns) per-trade across all syms; NEVER × sqrt(N).
  Open positions at end-of-test marked-to-market and counted.
  Per-symbol Sharpe capped at +/-5.0; per-symbol-avg excludes syms with <30 trades.

Standard 5-metric set: pool_sharpe, sym_sharpe, avg_gain_trade, gain_per_yr, gain_sym_yr.
"""
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BASE = Path("/Users/niels/Documents/binance")
CACHE = BASE / "klines_cache"
TRADEABLE_KEYS_FILE = BASE / "tradeable_keys.json"
OUT_DIR = BASE / "data"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Advisory rule constants (mirrored from local_advisory_generator.py) ---
ENTRY_SCORE_MIN = 75.0
ENTRY_MTF_MIN = 4
ENTRY_NEAR_DIST_MAX_PCT = 1.5
LOSER_GAIN_THRESHOLD = -0.5
SR_BREAK_GAIN_THRESHOLD = 0.0
HOLD_MIN_MTF_ALIGN = 3
HOLD_MIN_GAIN_PCT = 0.0
ENTRY_EXPIRES_BARS = 2  # 30min / 15m

# Backtest settings
ENTRY_SIZE_USD = 50.0
ACCOUNT_FEE_PCT = 0.04 * 2  # taker round-trip ~0.08% (2x 0.04%)
SLIPPAGE_PCT = 0.02 * 2  # round-trip est
TOTAL_COST_PCT = ACCOUNT_FEE_PCT + SLIPPAGE_PCT  # ~0.12%
BASELINE_HOLD_BARS = 16  # 4h hold on 15m base
ADV_MAX_HOLD_BARS = 96  # 24h cap; in practice closes via MTF/SR

# Per-symbol Sharpe cap and min-trades floor
SHARPE_CAP = 5.0
MIN_TRADES_FOR_SYM_AVG = 30


def load_klines(sym):
    p = CACHE / f"{sym}_15m.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
    except Exception:
        return None
    if not d:
        return None
    n = len(d)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    v = np.empty(n, dtype=np.float64)
    for i, b in enumerate(d):
        o[i] = b["open"]
        h[i] = b["high"]
        l[i] = b["low"]
        c[i] = b["close"]
        v[i] = b["volume"]
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "n": n}


def resample_15m_to(close_15m, factor):
    """Take last close in each bucket of `factor` 15m bars (== HTF close)."""
    n = len(close_15m)
    n_out = n // factor
    if n_out == 0:
        return np.array([])
    truncated = close_15m[: n_out * factor]
    # OHLC-like: last close per bucket (cheap proxy)
    return truncated.reshape(n_out, factor)[:, -1]


def resample_high_low(high_15m, low_15m, factor):
    n = len(high_15m)
    n_out = n // factor
    if n_out == 0:
        return np.array([]), np.array([])
    h = high_15m[: n_out * factor].reshape(n_out, factor).max(axis=1)
    l = low_15m[: n_out * factor].reshape(n_out, factor).min(axis=1)
    return h, l


def wt_lazybear(hlc3, n1=10, n2=21):
    """LazyBear WaveTrend. Returns (wt1, wt2). Same as ez_indicators."""
    n = len(hlc3)
    if n < n2 + 4:
        return np.full(n, np.nan), np.full(n, np.nan)
    # ESA = EMA(hlc3, n1)
    esa = np.full(n, np.nan)
    alpha1 = 2.0 / (n1 + 1)
    esa[0] = hlc3[0]
    for i in range(1, n):
        esa[i] = alpha1 * hlc3[i] + (1 - alpha1) * esa[i - 1]
    # D = EMA(|hlc3-esa|, n1)
    d = np.full(n, np.nan)
    abs_diff = np.abs(hlc3 - esa)
    d[0] = abs_diff[0]
    for i in range(1, n):
        d[i] = alpha1 * abs_diff[i] + (1 - alpha1) * d[i - 1]
    # CI = (hlc3 - esa) / (0.015 * D)
    with np.errstate(divide="ignore", invalid="ignore"):
        ci = (hlc3 - esa) / (0.015 * d)
    ci = np.nan_to_num(ci, nan=0.0, posinf=0.0, neginf=0.0)
    # TCI = EMA(CI, n2) = wt1
    wt1 = np.full(n, np.nan)
    alpha2 = 2.0 / (n2 + 1)
    wt1[0] = ci[0]
    for i in range(1, n):
        wt1[i] = alpha2 * ci[i] + (1 - alpha2) * wt1[i - 1]
    # wt2 = SMA(wt1, 4)
    wt2 = np.full(n, np.nan)
    for i in range(3, n):
        wt2[i] = (wt1[i] + wt1[i - 1] + wt1[i - 2] + wt1[i - 3]) / 4.0
    return wt1, wt2


def map_htf_to_15m(htf_arr, factor, n_15m):
    """Map HTF bar values back to per-15m timeline (forward-fill at completion of each bucket)."""
    n_htf = len(htf_arr)
    out = np.full(n_15m, np.nan)
    for j in range(n_htf):
        # the HTF bar j completes at 15m index (j+1)*factor - 1
        end_idx = (j + 1) * factor - 1
        if end_idx >= n_15m:
            break
        out[end_idx:] = htf_arr[j]  # forward-fill
    return out


def precompute_indicators(k):
    """Compute WT1/WT2 across 5 TFs and dc_high_4h / dc_low_4h on 15m timeline."""
    o = k["open"]
    h = k["high"]
    l = k["low"]
    c = k["close"]
    n = len(c)
    hlc3_15m = (h + l + c) / 3.0
    # TF factors over 15m base: 3m would be sub-bar (skip — use 15m as proxy "fast TF")
    # Treat: tf3m = 15m (proxy), tf15m = 15m, tf1h = 4 bars, tf4h = 16 bars, tfD = 96 bars
    tfs = {"3m": 1, "15m": 1, "1h": 4, "4h": 16, "D": 96}
    wt = {}
    for tf, factor in tfs.items():
        if factor == 1:
            w1, w2 = wt_lazybear(hlc3_15m)
        else:
            n_out = n // factor
            if n_out < 30:
                wt[tf] = (np.full(n, np.nan), np.full(n, np.nan))
                continue
            h_htf, l_htf = resample_high_low(h, l, factor)
            c_htf = resample_15m_to(c, factor)
            hlc3_htf = (h_htf + l_htf + c_htf) / 3.0
            w1_htf, w2_htf = wt_lazybear(hlc3_htf)
            w1 = map_htf_to_15m(w1_htf, factor, n)
            w2 = map_htf_to_15m(w2_htf, factor, n)
        wt[tf] = (w1, w2)
    # Differentiate 3m vs 15m: 3m proxy uses raw 15m close (same as 15m here — no real 3m data
    # so we set 3m = 15m). This penalises advisory slightly (less "fast TF" signal), which is
    # a conservative bias — fine for directional test.
    # Donchian 4h: rolling max(high)/min(low) over last N 4h bars on 15m timeline.
    DC_LEN = 20  # standard Donchian length
    factor4h = 16
    n_4h = n // factor4h
    if n_4h >= DC_LEN:
        h_4h, l_4h = resample_high_low(h, l, factor4h)
        dc_high_4h_htf = np.full(n_4h, np.nan)
        dc_low_4h_htf = np.full(n_4h, np.nan)
        for j in range(DC_LEN, n_4h):
            dc_high_4h_htf[j] = h_4h[j - DC_LEN : j].max()
            dc_low_4h_htf[j] = l_4h[j - DC_LEN : j].min()
        dc_high_4h = map_htf_to_15m(dc_high_4h_htf, factor4h, n)
        dc_low_4h = map_htf_to_15m(dc_low_4h_htf, factor4h, n)
    else:
        dc_high_4h = np.full(n, np.nan)
        dc_low_4h = np.full(n, np.nan)
    return {"wt": wt, "dc_high_4h": dc_high_4h, "dc_low_4h": dc_low_4h}


def mtf_align_at(ind, side, idx):
    """Count WT-aligned TFs at bar idx (out of 5: 3m,15m,1h,4h,D)."""
    n = 0
    seen = 0
    for tf in ("3m", "15m", "1h", "4h", "D"):
        w1, w2 = ind["wt"][tf]
        if np.isnan(w1[idx]) or np.isnan(w2[idx]):
            continue
        seen += 1
        if side == "LONG" and w1[idx] > w2[idx]:
            n += 1
        elif side == "SHORT" and w1[idx] < w2[idx]:
            n += 1
    return n, seen


def is_fresh_setup(ind, k, side, idx):
    """Approximate fresh_setup: mtf_align>=4, near a DC level (distance<=1.5%), supportive=True."""
    n_align, seen = mtf_align_at(ind, side, idx)
    if seen < 4 or n_align < ENTRY_MTF_MIN:
        return False, {}
    price = k["close"][idx]
    if side == "LONG":
        lvl = ind["dc_low_4h"][idx]
    else:
        lvl = ind["dc_high_4h"][idx]
    if np.isnan(lvl) or lvl <= 0:
        return False, {}
    dist_pct = abs(price - lvl) / lvl * 100.0
    if dist_pct > ENTRY_NEAR_DIST_MAX_PCT:
        return False, {}
    # supportive = price on the right side of the level
    if side == "LONG" and price < lvl:
        # below support — buying the breakdown — NOT supportive
        return False, {}
    if side == "SHORT" and price > lvl:
        return False, {}
    # Score proxy (MTF + supportive structure scores high). We require both:
    score = 50 + n_align * 10  # rough proxy: 4 aligned -> 90, 5 -> 100
    if score < ENTRY_SCORE_MIN:
        return False, {}
    return True, {"mtf_align": n_align, "mtf_seen": seen, "distance_pct": dist_pct, "score": score}


def should_force_close(ind, k, side, idx, entry_price):
    """Return (close, reason). Mirrors local_advisory_generator force_close logic."""
    cur_price = k["close"][idx]
    if side == "LONG":
        gain_pct = (cur_price - entry_price) / entry_price * 100.0
    else:
        gain_pct = (entry_price - cur_price) / entry_price * 100.0
    # MTF_AGAINST_LOSER: gain<-0.5% AND 4+ TFs against
    opp = "SHORT" if side == "LONG" else "LONG"
    against_n, against_seen = mtf_align_at(ind, opp, idx)
    with_n, with_seen = mtf_align_at(ind, side, idx)
    if gain_pct < LOSER_GAIN_THRESHOLD and against_seen >= 4 and against_n >= 4 and with_n <= 1:
        return True, "MTF_AGAINST_LOSER"
    # SR_BREAK_AGAINST: gain<0% AND price through dc_4h
    if gain_pct < SR_BREAK_GAIN_THRESHOLD:
        if side == "SHORT":
            lvl = ind["dc_high_4h"][idx]
            if not np.isnan(lvl) and cur_price >= lvl:
                return True, "SR_BREAK_AGAINST"
        else:
            lvl = ind["dc_low_4h"][idx]
            if not np.isnan(lvl) and cur_price <= lvl:
                return True, "SR_BREAK_AGAINST"
    return False, None


def should_hold(ind, k, side, idx, entry_price):
    """gain>=0% AND >=3 MTF aligned with side."""
    cur_price = k["close"][idx]
    if side == "LONG":
        gain_pct = (cur_price - entry_price) / entry_price * 100.0
    else:
        gain_pct = (entry_price - cur_price) / entry_price * 100.0
    if gain_pct < HOLD_MIN_GAIN_PCT:
        return False
    with_n, with_seen = mtf_align_at(ind, side, idx)
    if with_seen < 3 or with_n < HOLD_MIN_MTF_ALIGN:
        return False
    return True


def backtest_advisory(sym, k, ind, sides=("LONG", "SHORT")):
    """Walk through bars; on every bar, check fresh_setup. If fresh, open. Then on each
    subsequent bar manage with force_close / hold / max-hold timeout.
    Trade overlap: at most 1 open position per (sym, side). Entry locked out for 4 bars after exit."""
    trades = []
    open_pos_by_side = {}
    cooldown = {}  # side -> idx unlock
    n = k["n"]
    for idx in range(96 + 30, n - 1):  # warmup: ~D-tf needs ≥30 D bars; we have ~16-18d so D is mostly NaN
        for side in sides:
            # Manage open
            if side in open_pos_by_side:
                pos = open_pos_by_side[side]
                close_now, reason = should_force_close(ind, k, side, idx, pos["entry_price"])
                age = idx - pos["entry_idx"]
                hold_ok = should_hold(ind, k, side, idx, pos["entry_price"])
                # default exit: when no force_close + no hold signal AND age>=BASELINE_HOLD_BARS
                if close_now or age >= ADV_MAX_HOLD_BARS or (not hold_ok and age >= BASELINE_HOLD_BARS):
                    exit_price = k["close"][idx]
                    if side == "LONG":
                        ret_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100.0
                    else:
                        ret_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100.0
                    ret_pct -= TOTAL_COST_PCT
                    trades.append({"sym": sym, "side": side, "entry_idx": pos["entry_idx"], "exit_idx": idx,
                                   "entry": pos["entry_price"], "exit": exit_price, "ret_pct": ret_pct,
                                   "exit_reason": reason or ("max_hold" if age >= ADV_MAX_HOLD_BARS else "no_signal_timeout")})
                    cooldown[side] = idx + 4
                    del open_pos_by_side[side]
                    continue
            # Try to open
            if side in open_pos_by_side:
                continue
            if cooldown.get(side, 0) > idx:
                continue
            ok, info = is_fresh_setup(ind, k, side, idx)
            if not ok:
                continue
            open_pos_by_side[side] = {"entry_idx": idx, "entry_price": k["close"][idx], "info": info}
    # Close any open at end-of-test (mark-to-market)
    last_idx = n - 1
    for side, pos in open_pos_by_side.items():
        exit_price = k["close"][last_idx]
        if side == "LONG":
            ret_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100.0
        else:
            ret_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100.0
        ret_pct -= TOTAL_COST_PCT
        trades.append({"sym": sym, "side": side, "entry_idx": pos["entry_idx"], "exit_idx": last_idx,
                       "entry": pos["entry_price"], "exit": exit_price, "ret_pct": ret_pct,
                       "exit_reason": "EOT_MTM"})
    return trades


def backtest_baseline(sym, k, sides=("LONG", "SHORT")):
    """Baseline: enter every BASELINE_HOLD_BARS bars (one per side); hold BASELINE_HOLD_BARS bars; exit.
    Comparable trade count to advisory. Uses same fee/slip costs."""
    trades = []
    n = k["n"]
    step = BASELINE_HOLD_BARS  # roughly: open then exit after step bars
    for side in sides:
        # offset starts so we don't open every side at same index
        offset = 0 if side == "LONG" else step // 2
        idx = 96 + 30 + offset
        while idx < n - step - 1:
            entry_price = k["close"][idx]
            exit_idx = idx + step
            exit_price = k["close"][exit_idx]
            if side == "LONG":
                ret_pct = (exit_price - entry_price) / entry_price * 100.0
            else:
                ret_pct = (entry_price - exit_price) / entry_price * 100.0
            ret_pct -= TOTAL_COST_PCT
            trades.append({"sym": sym, "side": side, "entry_idx": idx, "exit_idx": exit_idx,
                           "entry": entry_price, "exit": exit_price, "ret_pct": ret_pct,
                           "exit_reason": "fixed_hold"})
            idx = exit_idx + 1
    return trades


def compute_metrics(trades, n_syms, n_years):
    if not trades:
        return {"trades": 0, "pool_sharpe": 0.0, "sym_sharpe": 0.0, "avg_gain_trade": 0.0,
                "gain_per_yr": 0.0, "gain_sym_yr": 0.0, "max_dd_pct": 0.0, "hit_rate": 0.0,
                "acc_gain_pct": 0.0}
    rets = np.array([t["ret_pct"] for t in trades], dtype=np.float64)
    pool_mean = float(rets.mean())
    pool_std = float(rets.std(ddof=0))
    pool_sharpe = pool_mean / pool_std if pool_std > 1e-9 else 0.0
    # Per-symbol Sharpe (capped, with min-trades floor)
    sym_returns = {}
    for t in trades:
        sym_returns.setdefault(t["sym"], []).append(t["ret_pct"])
    sym_sharpes = []
    for s, r in sym_returns.items():
        if len(r) < MIN_TRADES_FOR_SYM_AVG:
            continue
        ar = np.array(r)
        mu = ar.mean()
        sd = ar.std(ddof=0)
        if sd < 1e-9:
            continue
        sh = max(-SHARPE_CAP, min(SHARPE_CAP, mu / sd))
        sym_sharpes.append(sh)
    sym_sharpe = float(np.mean(sym_sharpes)) if sym_sharpes else 0.0
    # Equity curve & DD (per-trade pct, treated as add-on to equity that starts at 100)
    equity = 100.0
    peaks = 100.0
    max_dd = 0.0
    for r in rets:
        equity *= (1.0 + r / 100.0)
        peaks = max(peaks, equity)
        dd = (peaks - equity) / peaks * 100.0
        max_dd = max(max_dd, dd)
    acc_gain = float(rets.sum())
    n_trades = len(rets)
    avg_gain_trade = acc_gain / n_trades if n_trades else 0.0
    gain_per_yr = acc_gain / n_years if n_years > 0 else 0.0
    gain_sym_yr = (acc_gain / n_syms / n_years) if (n_syms > 0 and n_years > 0) else 0.0
    hit_rate = float((rets > 0).sum()) / n_trades * 100.0
    return {
        "trades": n_trades,
        "pool_sharpe": round(pool_sharpe, 4),
        "sym_sharpe": round(sym_sharpe, 4),
        "sym_sharpes_count_qualified": len(sym_sharpes),
        "avg_gain_trade": round(avg_gain_trade, 4),
        "gain_per_yr": round(gain_per_yr, 2),
        "gain_sym_yr": round(gain_sym_yr, 4),
        "max_dd_pct": round(max_dd, 2),
        "hit_rate": round(hit_rate, 2),
        "acc_gain_pct": round(acc_gain, 2),
    }


def main():
    from test_rate_guard import RateGuard
    keys = json.loads(TRADEABLE_KEYS_FILE.read_text())
    syms = sorted({k[4:].rsplit("_", 1)[0] for k in keys if k.startswith("fin:") or k.startswith("ang:")})
    print(f"[init] {len(syms)} fin+ang symbols")
    guard = RateGuard(n_accts=2, label="backtest_local_advisory_advisory_arm")
    all_adv = []
    all_base = []
    n_loaded = 0
    n_skipped = 0
    n_bars_min = float("inf")
    n_bars_max = 0
    for s in syms:
        k = load_klines(s)
        if k is None or k["n"] < 96 + 30 + 32:
            n_skipped += 1
            continue
        n_loaded += 1
        n_bars_min = min(n_bars_min, k["n"])
        n_bars_max = max(n_bars_max, k["n"])
        ind = precompute_indicators(k)
        adv = backtest_advisory(s, k, ind)
        base = backtest_baseline(s, k)
        all_adv.extend(adv)
        all_base.extend(base)
        guard.tick(len(all_adv))
    days = n_bars_max / 96.0
    n_years = days / 365.25
    print(f"[run] loaded={n_loaded} skipped={n_skipped} bar_range=[{n_bars_min},{n_bars_max}] days~{days:.1f} years~{n_years:.4f}")
    guard.final_check(len(all_adv), test_window_days=days)
    adv_m = compute_metrics(all_adv, n_loaded, n_years)
    base_m = compute_metrics(all_base, n_loaded, n_years)
    # Write outputs
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    out_json = OUT_DIR / f"backtest_local_advisory_{today}.json"
    out_md = OUT_DIR / f"BACKTEST_LOCAL_ADVISORY_{today}.md"
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "syms_in_scope": len(syms),
        "syms_loaded": n_loaded,
        "syms_skipped": n_skipped,
        "bars_per_sym_min": n_bars_min if n_bars_min != float("inf") else 0,
        "bars_per_sym_max": n_bars_max,
        "approx_days": round(days, 1),
        "approx_years": round(n_years, 4),
        "advisory_metrics": adv_m,
        "baseline_metrics": base_m,
        "compare": {
            "delta_pool_sharpe": round(adv_m["pool_sharpe"] - base_m["pool_sharpe"], 4),
            "delta_avg_gain_trade": round(adv_m["avg_gain_trade"] - base_m["avg_gain_trade"], 4),
            "delta_hit_rate": round(adv_m["hit_rate"] - base_m["hit_rate"], 2),
            "delta_max_dd_pct": round(adv_m["max_dd_pct"] - base_m["max_dd_pct"], 2),
        },
        "rule_constants": {
            "ENTRY_SCORE_MIN": ENTRY_SCORE_MIN,
            "ENTRY_MTF_MIN": ENTRY_MTF_MIN,
            "ENTRY_NEAR_DIST_MAX_PCT": ENTRY_NEAR_DIST_MAX_PCT,
            "LOSER_GAIN_THRESHOLD": LOSER_GAIN_THRESHOLD,
            "SR_BREAK_GAIN_THRESHOLD": SR_BREAK_GAIN_THRESHOLD,
            "HOLD_MIN_MTF_ALIGN": HOLD_MIN_MTF_ALIGN,
            "TOTAL_COST_PCT": TOTAL_COST_PCT,
            "BASELINE_HOLD_BARS": BASELINE_HOLD_BARS,
            "ADV_MAX_HOLD_BARS": ADV_MAX_HOLD_BARS,
            "SHARPE_CAP": SHARPE_CAP,
            "MIN_TRADES_FOR_SYM_AVG": MIN_TRADES_FOR_SYM_AVG,
        },
    }
    out_json.write_text(json.dumps(payload, indent=2, default=str))
    print(f"[out] wrote {out_json}")
    # Markdown summary
    md = []
    md.append(f"# Local Advisory Generator — Backtest {today}")
    md.append("")
    md.append(f"**Generated:** {payload['generated_at_utc']}")
    md.append(f"**Scope:** {n_loaded}/{len(syms)} fin+ang symbols, {days:.1f}d klines (~{n_years:.4f}yr)")
    md.append("")
    md.append("## CLAUDE.md compliance")
    md.append(f"- ≥48 syms × >1yr publication floor: **VIOLATED** ({n_loaded} syms × {days:.1f}d). Directional only.")
    md.append(f"- Sharpe per-trade only, no sqrt(N) annualization")
    md.append(f"- Open positions at end-of-test marked-to-market (exit_reason='EOT_MTM')")
    md.append(f"- Per-symbol Sharpe capped at +/-{SHARPE_CAP}, syms with <{MIN_TRADES_FOR_SYM_AVG} trades excluded from sym_sharpe")
    md.append("")
    md.append("## ADVISORY (force_open + force_close + hold)")
    md.append(f"`pool_sharpe={adv_m['pool_sharpe']} | avg_gain_trade={adv_m['avg_gain_trade']}%/trade | gain_per_yr={adv_m['gain_per_yr']}%/yr | gain_sym_yr={adv_m['gain_sym_yr']}%/sym/yr | trades={adv_m['trades']} | dd={adv_m['max_dd_pct']}%`")
    md.append(f"- sym_sharpe (qualified syms={adv_m['sym_sharpes_count_qualified']}): {adv_m['sym_sharpe']}")
    md.append(f"- hit_rate: {adv_m['hit_rate']}%")
    md.append(f"- accumulated gain: {adv_m['acc_gain_pct']}%")
    md.append("")
    md.append("## BASELINE (random fixed-hold every 4h, no advisory rules)")
    md.append(f"`pool_sharpe={base_m['pool_sharpe']} | avg_gain_trade={base_m['avg_gain_trade']}%/trade | gain_per_yr={base_m['gain_per_yr']}%/yr | gain_sym_yr={base_m['gain_sym_yr']}%/sym/yr | trades={base_m['trades']} | dd={base_m['max_dd_pct']}%`")
    md.append(f"- sym_sharpe (qualified syms={base_m['sym_sharpes_count_qualified']}): {base_m['sym_sharpe']}")
    md.append(f"- hit_rate: {base_m['hit_rate']}%")
    md.append(f"- accumulated gain: {base_m['acc_gain_pct']}%")
    md.append("")
    md.append("## DELTA (advisory − baseline)")
    md.append(f"- delta_pool_sharpe: {payload['compare']['delta_pool_sharpe']}")
    md.append(f"- delta_avg_gain_trade: {payload['compare']['delta_avg_gain_trade']}%")
    md.append(f"- delta_hit_rate: {payload['compare']['delta_hit_rate']}pp")
    md.append(f"- delta_max_dd_pct: {payload['compare']['delta_max_dd_pct']}pp")
    md.append("")
    md.append("## VERDICT")
    if adv_m["pool_sharpe"] < 1.0:
        md.append("- **TRASH per CLAUDE.md feedback_sharpe_2_baseline.md** — advisory pool_sharpe < 1.0.")
    if base_m["pool_sharpe"] < 2.0:
        md.append("- **TRASH baseline per CLAUDE.md** — baseline pool_sharpe < 2.0; comparison is on a weak baseline.")
    md.append("- Sample size below 48-sym × 1-yr publication floor — NOT publishable, directional-only.")
    out_md.write_text("\n".join(md))
    print(f"[out] wrote {out_md}")
    print("")
    print("=" * 80)
    print("ADVISORY:", json.dumps(adv_m, indent=2))
    print("BASELINE:", json.dumps(base_m, indent=2))
    print("DELTA:", json.dumps(payload["compare"], indent=2))


if __name__ == "__main__":
    main()
