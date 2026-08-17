#!/usr/bin/env python3
"""switch_ladder_lab.py — additive-switch search on top of the bare 5m WT-cross system.

USER 2026-07-20: "if this is really only just that, start adding all other switches one by
one — like not trading against HTF stoch rsi direction etc."

Baseline L0 = every 5m green arrow in / red arrow out (NVDA 9.57x b&h, MU 2.49x). This tool
adds ONE switch at a time, measures delta vs that baseline per symbol, then STACKS the
switches that helped (greedy, best-first) and reports the stack curve. Every switch is a
real indicator relationship read from the NPZ, per TF, causal.

Switch families (each per-TF where it makes sense):
  STOCH_DIR_<tf>    don't buy when k_<tf> < d_<tf>   (HTF stoch pointing down)
  STOCH_OB_<tf>     don't buy when k_<tf> > 80       (overbought)
  WT_DIR_<tf>       don't buy when wt1_<tf> < wt2_<tf>
  RSI_DIR_<tf>      don't buy when rsi_<tf> < 50
  MFI_DIR_<tf>      don't buy when mfi_<tf> < 50
  ADX_MIN_<tf>      require adx_<tf> >= 20 (trend strength)
  EMA_<tf>          require close > ema_50_<tf>
  SMA200_<tf>       require close > sma_200_<tf>
  DCPOS_<tf>        require dc_position_<tf> <= 0.8 (not buying the very top)
  BB_<tf>           require bb_pct_b_<tf> <= 0.9
  EXIT_HOLD_<n>     ignore red arrows for n bars after entry (min-hold)

Usage (S1): python tools/switch_ladder_lab.py NVDA MU ARM ROKU --single
            python tools/switch_ladder_lab.py NVDA --stack
[DIAGNOSTIC ONLY n_syms=1 per run.]
"""
import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np

SBX = Path("/home/niels/binance-sandbox")
OUT = SBX / "data" / "_diagnostic" / "switch_ladder"
COST = 0.10  # USER 2026-07-20: 0.1% round-trip covers slippage/spread (Tradier, no commission)
TFS = ["15m", "1h", "4h", "D"]


def load(sym, start="2024-01-01"):
    z = np.load(SBX / "backtest_v8" / "indicators" / f"{sym}.npz", allow_pickle=True)
    ts = z["timestamps"].astype(np.float64)
    i0 = int(np.searchsorted(ts, dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc).timestamp()))
    d = {"ts": ts[i0:], "close": z["close"].astype(np.float64)[i0:],
         "green": z["wt_cross_bull_5m"][i0:].astype(bool), "red": z["wt_cross_bear_5m"][i0:].astype(bool)}
    for tf in TFS + ["5m"]:
        for f in ("k", "d", "rsi", "mfi", "adx", "wt1", "wt2", "ema_50", "sma_200", "dc_position",
                  "bb_pct_b", "lrL_pct_b", "lrL_slope", "lr_trend"):
            key = f"{f}_{tf}"
            if key in z.files:
                d[key] = z[key].astype(np.float64)[i0:]
    return d


def real_param_for(switch):
    """Map a lab mask switch ('NAME=value') to the REAL config knob it corresponds to
    (USER 2026-07-21: matrices must live in real-param space — SWITCH_MATRIX is the
    reference). ONLY code-verified correspondences are mapped; everything else returns
    None and MUST be presented as IDEA:, never under a real knob name."""
    if "=" not in switch:
        return None
    name, val = switch.rsplit("=", 1)
    if name == "ARROW_SCORE_MIN":
        return ("MTF_ARROW_THETA", val)
    if name == "ARROW_SCORE_MIN_1H":
        return ("MTF_ARROW_THETA[weights=1h_heavy]", val)
    if name == "ARROW_CONFIRM":
        return ("MTF_ARROW_CONFIRM_PCT", val)
    if name.startswith("LRB_MAX_"):
        return (f"LR_BAND_ENTRY_LO[TF={name.rsplit('_', 1)[1]}]", val)
    if name in ("DCPOS_MAX_1h", "DCPOS_MAX_4h"):
        return ("DC_POSITION_ENTRY_THRESHOLD", val)
    if name == "STOCH_MAX_4h":
        return ("K_ZONE_LONG_THRESHOLD_TRADIER", val)
    if name == "WT1_CROSS":
        return ("WT_3M_FORCE_OPEN", val)
    return None


def switch_masks_valued(d):
    """USER 2026-07-20: before declaring a function broken, sweep its FULL numeric range in
    4-8 steps — a negative verdict at a narrow range usually means the range was wrong, not
    the function. Every gate below spans its entire domain (0-100 oscillators, full band
    range, wide ADX/distance bands) in 8 steps, and BOTH directions (min-gate and max-gate)
    so an inverted relationship is discovered rather than assumed away."""
    m = {}
    c = d["close"]
    OSC8 = (5, 20, 35, 50, 65, 80, 90, 95)          # full 0-100 oscillator domain
    BAND8 = (0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95, 1.1)
    ADX8 = (0, 5, 10, 15, 20, 30, 40, 50)
    DIST8 = (-0.20, -0.12, -0.07, -0.03, 0.0, 0.03, 0.07, 0.12)
    WT8 = (-80, -60, -40, -20, 0, 20, 40, 60)
    for tf in TFS:
        if f"k_{tf}" in d:
            for v in OSC8:
                m[f"STOCH_MAX_{tf}={v}"] = d[f"k_{tf}"] <= v
                m[f"STOCH_MIN_{tf}={v}"] = d[f"k_{tf}"] >= v
        if f"rsi_{tf}" in d:
            for v in OSC8:
                m[f"RSI_MAX_{tf}={v}"] = d[f"rsi_{tf}"] <= v
                m[f"RSI_MIN_{tf}={v}"] = d[f"rsi_{tf}"] >= v
        if f"mfi_{tf}" in d:
            for v in OSC8:
                m[f"MFI_MAX_{tf}={v}"] = d[f"mfi_{tf}"] <= v
                m[f"MFI_MIN_{tf}={v}"] = d[f"mfi_{tf}"] >= v
        if f"adx_{tf}" in d:
            for v in ADX8:
                m[f"ADX_MIN_{tf}={v}"] = d[f"adx_{tf}"] >= v
                m[f"ADX_MAX_{tf}={v}"] = d[f"adx_{tf}"] <= v
        if f"dc_position_{tf}" in d:
            for v in BAND8:
                m[f"DCPOS_MAX_{tf}={v}"] = d[f"dc_position_{tf}"] <= v
                m[f"DCPOS_MIN_{tf}={v}"] = d[f"dc_position_{tf}"] >= v
        if f"bb_pct_b_{tf}" in d:
            for v in BAND8:
                m[f"BB_MAX_{tf}={v}"] = d[f"bb_pct_b_{tf}"] <= v
                m[f"BB_MIN_{tf}={v}"] = d[f"bb_pct_b_{tf}"] >= v
        if f"lrL_pct_b_{tf}" in d:
            for v in BAND8:
                m[f"LRB_MAX_{tf}={v}"] = d[f"lrL_pct_b_{tf}"] <= v
                m[f"LRB_MIN_{tf}={v}"] = d[f"lrL_pct_b_{tf}"] >= v
        if f"ema_50_{tf}" in d:
            for v in DIST8:
                m[f"EMA_DIST_{tf}={v}"] = c >= d[f"ema_50_{tf}"] * (1.0 + v)
        if f"sma_200_{tf}" in d:
            for v in DIST8:
                m[f"SMA200_DIST_{tf}={v}"] = c >= d[f"sma_200_{tf}"] * (1.0 + v)
        if f"wt1_{tf}" in d:
            m[f"WT_DIR_{tf}=on"] = d[f"wt1_{tf}"] >= d[f"wt2_{tf}"]
            m[f"WT_DIR_{tf}=inv"] = d[f"wt1_{tf}"] < d[f"wt2_{tf}"]
            for v in WT8:
                m[f"WT_LEVEL_MAX_{tf}={v}"] = d[f"wt1_{tf}"] <= v
                m[f"WT_LEVEL_MIN_{tf}={v}"] = d[f"wt1_{tf}"] >= v
    # MTF_ARROW (2026-07-21): lab-faithful HTF depth+slope score as a valued entry gate
    # (mirrors tradier_manage.mtf_arrow_score / tools/mtf_arrow_lab phase_b — ARM 5.81x
    # b&h sized). Two weight profiles; theta ladder spans the score domain. CONFIRM masks
    # approximate the running-low reversal trigger with a 1-day rolling min (78 5m bars) —
    # labeled approximation, the engine packs test the exact stateful version.
    _arrow_profiles = {"": {"1h": 0.35, "4h": 0.35, "D": 0.30},
                       "_1H": {"1h": 0.5, "4h": 0.25, "D": 0.25}}
    for _sfx, _aw in _arrow_profiles.items():
        if not all(f"lrL_pct_b_{tf}" in d and f"lrL_slope_{tf}" in d for tf in _aw):
            continue
        _asc = np.zeros_like(c, dtype=float)
        for tf, w in _aw.items():
            _dep = np.clip(1.0 - d[f"lrL_pct_b_{tf}"], 0.0, 1.2)
            _sl = np.clip(d[f"lrL_slope_{tf}"], -2.0, 2.0)
            _asc += w * (_dep + np.clip(_sl, 0.0, 2.0)) - w * 0.5 * (_sl < 0)
        for v in (0.2, 0.35, 0.5, 0.65, 0.8, 1.0, 1.2, 1.5):
            m[f"ARROW_SCORE_MIN{_sfx}={v}"] = _asc >= v
    _acw = 78
    if len(c) > _acw:
        _rmin = np.empty_like(c)
        _rmin[:_acw] = np.minimum.accumulate(c[:_acw])
        _rmin[_acw:] = np.min(np.lib.stride_tricks.sliding_window_view(c, _acw), axis=1)[:len(c) - _acw]
        for v in (0.5, 1.0, 2.0, 3.0):
            m[f"ARROW_CONFIRM={v}"] = c >= _rmin * (1.0 + v / 100.0)
    return m


def switch_masks(d):
    """Each switch -> boolean array: True = ENTRY ALLOWED at that bar."""
    m = {}
    c = d["close"]
    for tf in TFS:
        if f"k_{tf}" in d and f"d_{tf}" in d:
            m[f"STOCH_DIR_{tf}"] = d[f"k_{tf}"] >= d[f"d_{tf}"]
            m[f"STOCH_OB_{tf}"] = d[f"k_{tf}"] <= 80.0
            m[f"STOCH_OS_{tf}"] = d[f"k_{tf}"] <= 50.0
        if f"wt1_{tf}" in d:
            m[f"WT_DIR_{tf}"] = d[f"wt1_{tf}"] >= d[f"wt2_{tf}"]
        if f"rsi_{tf}" in d:
            m[f"RSI_DIR_{tf}"] = d[f"rsi_{tf}"] >= 50.0
        if f"mfi_{tf}" in d:
            m[f"MFI_DIR_{tf}"] = d[f"mfi_{tf}"] >= 50.0
        if f"adx_{tf}" in d:
            m[f"ADX_MIN_{tf}"] = d[f"adx_{tf}"] >= 20.0
        if f"ema_50_{tf}" in d:
            m[f"EMA_{tf}"] = c > d[f"ema_50_{tf}"]
        if f"sma_200_{tf}" in d:
            m[f"SMA200_{tf}"] = c > d[f"sma_200_{tf}"]
        if f"dc_position_{tf}" in d:
            m[f"DCPOS_{tf}"] = d[f"dc_position_{tf}"] <= 0.8
        if f"bb_pct_b_{tf}" in d:
            m[f"BB_{tf}"] = d[f"bb_pct_b_{tf}"] <= 0.9
    return m


def exit_masks(d, side="LONG"):
    """USER 2026-07-20 (TIM floor): entry vetoes can only REDUCE exposure, so they are
    inadmissible once time-in-market is the binding constraint. These switches instead
    SUPPRESS EXITS while the higher TF still supports the position — they RAISE exposure.
    Mask semantics: True = exit ALLOWED at that bar."""
    m = {}
    c = d["close"]
    long_side = (side == "LONG")
    for tf in TFS:
        if f"wt1_{tf}" in d:
            up = d[f"wt1_{tf}"] >= d[f"wt2_{tf}"]
            m[f"XCONF_WT_{tf}"] = ~up if long_side else up
        if f"k_{tf}" in d and f"d_{tf}" in d:
            kup = d[f"k_{tf}"] >= d[f"d_{tf}"]
            m[f"XCONF_STOCH_{tf}"] = ~kup if long_side else kup
        if f"rsi_{tf}" in d:
            rup = d[f"rsi_{tf}"] >= 50.0
            m[f"XCONF_RSI_{tf}"] = ~rup if long_side else rup
        if f"ema_50_{tf}" in d:
            above = c > d[f"ema_50_{tf}"]
            m[f"XCONF_EMA_{tf}"] = ~above if long_side else above
        if f"dc_position_{tf}" in d:
            hi = d[f"dc_position_{tf}"] >= 0.5
            m[f"XCONF_DC_{tf}"] = ~hi if long_side else hi
    return m


def size_switches(d, side="LONG"):
    """USER 2026-07-20: SIZE (position quantity) as a swept switch — scaled by where price sits in
    the stdev range (deeper toward the far band = bigger) and by the regression slope (steeper
    with the position = bigger). Returns name -> per-bar multiplier array. Value = the gain
    coefficient, so the spreadsheet shows which sizing strength pays on each key."""
    out = {}
    long_side = (side == "LONG")
    for tf in TFS:
        pb = d.get(f"lrL_pct_b_{tf}")
        if pb is None:
            pb = d.get(f"bb_pct_b_{tf}")
        if pb is not None:
            depth = np.clip(1.0 - pb, 0.0, 1.0) if long_side else np.clip(pb, 0.0, 1.0)
            for g in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0):
                out[f"SIZE_BAND_{tf}={g}"] = np.clip(1.0 + g * (depth - 0.5) * 2.0, 0.1, 20.0)
        sl = d.get(f"lrL_slope_{tf}")
        if sl is None:
            sl = d.get(f"lr_trend_{tf}")
        if sl is not None:
            fav = np.clip(sl, 0, None) if long_side else np.clip(-sl, 0, None)
            norm = np.nanpercentile(np.abs(sl[np.isfinite(sl)]), 80) if np.isfinite(sl).any() else 1.0
            norm = norm if norm and norm > 0 else 1.0
            for g in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 12.0):
                out[f"SIZE_SLOPE_{tf}={g}"] = np.clip(1.0 + g * np.minimum(fav / norm, 1.5), 0.1, 20.0)
    return out


def sim(d, allow=None, min_hold_bars=0, side="LONG", exit_allow=None, size=None):
    """LONG: in on 5m green (wt cross up), out on red. SHORT: exact mirror — in on 5m RED
    (cross down), out on GREEN. b&h is SIDE-AWARE: short-and-hold = -(price change), so a
    stock that fell 60% has short b&h of +60% (USER 2026-07-20)."""
    ts, c = d["ts"], d["close"]
    if side == "SHORT":
        green, red = d["red"], d["green"]
    else:
        green, red = d["green"], d["red"]
    n = len(c)
    trades = []
    inpos = False
    epx = ei = 0
    for i in range(1, n):
        if not inpos:
            if green[i] and (allow is None or allow[i]):
                inpos, epx, ei = True, c[i], i
                _mult = float(size[i]) if size is not None else 1.0
        else:
            _exit_ok = red[i] and (exit_allow is None or exit_allow[i])
            if (_exit_ok and (i - ei) >= min_hold_bars) or i == n - 1:
                raw = (c[i] / epx - 1) * 100.0
                if side == "SHORT":
                    raw = -raw
                trades.append({"entry_ts": float(ts[ei]), "exit_ts": float(ts[i]),
                               "pnl_pct": round((raw - COST) * (_mult if size is not None else 1.0), 4),
                               "mult": round(_mult if size is not None else 1.0, 3)})
                inpos = False
    g = sum(t["pnl_pct"] for t in trades)
    bh = (c[-1] / c[0] - 1) * 100.0
    if side == "SHORT":
        bh = -bh  # short-and-hold benchmark
    rets = [t["pnl_pct"] for t in trades]
    eq = peak = mdd = 0.0
    for r in rets:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    ps = (float(np.mean(rets)) / float(np.std(rets))) if len(rets) > 1 and np.std(rets) > 0 else 0.0
    held = sum(t["exit_ts"] - t["entry_ts"] for t in trades)
    return {"gain_pct": round(g, 1), "bh_pct": round(bh, 1),
            "gain_vs_bh": round(g / bh, 3) if bh > 0 else None,
            "cash_benchmark_pct": 0.0,
            "beats_opportunity_benchmark": g > max(0.0, bh),
            "trades": len(trades),
            "pool_sharpe": round(ps, 4), "max_dd_pct": round(mdd, 1),
            "avg_gain_trade": round(g / max(1, len(trades)), 4),
            "win_rate": round(100.0 * sum(1 for r in rets if r > 0) / len(rets), 1) if rets else None,
            "time_in_mkt_pct": round(100.0 * held / (ts[-1] - ts[0]), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", default=["NVDA"])
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--single", action="store_true")
    ap.add_argument("--stack", action="store_true")
    ap.add_argument("--max-stack", type=int, default=8)
    ap.add_argument("--side", default="LONG", choices=["LONG", "SHORT"])
    ap.add_argument("--valued", action="store_true",
                    help="sweep EVERY switch at EVERY value -> spreadsheet rows")
    ap.add_argument("--min-tim", type=float, default=50.0,
                    help="USER 2026-07-20: a switch may NOT drop time-in-market below this %. "
                         "1.3%% exposure is abstention, not a strategy — if the stock rises we must be in it.")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for sym in (a.symbols or ["NVDA"]):
        d = load(sym, a.start)
        masks = switch_masks(d)
        base = sim(d, side=a.side)
        print(f"===== {sym}_{a.side} baseline L0: gain={base['gain_pct']}% bh={base['bh_pct']}% "
              f"vs_bh={base['gain_vs_bh']}x tr={base['trades']} ps={base['pool_sharpe']} "
              f"dd={base['max_dd_pct']}% tim={base['time_in_mkt_pct']}% [DIAGNOSTIC n_syms=1]", flush=True)
        singles = []
        if a.valued:
            vm = switch_masks_valued(d)
            xm = exit_masks(d, a.side)
            print(f"--- {sym}_{a.side}: sweeping {len(vm)} entry + {len(size_switches(d, a.side))} size + {len(xm)} exit switch-values", flush=True)
            for nm, mk in sorted(vm.items()):
                r = sim(d, mk, side=a.side)
                r["switch"] = nm
                r["family"] = "entry"
                r["delta_vs_bh"] = round((r["gain_vs_bh"] or 0) - (base["gain_vs_bh"] or 0), 3)
                r["tim_ok"] = bool(r["time_in_mkt_pct"] >= min(a.min_tim, base["time_in_mkt_pct"]))
                singles.append(r)
            for nm, sz in sorted(size_switches(d, a.side).items()):
                r = sim(d, None, side=a.side, size=sz)
                r["switch"] = nm
                r["family"] = "size"
                r["delta_vs_bh"] = round((r["gain_vs_bh"] or 0) - (base["gain_vs_bh"] or 0), 3)
                r["tim_ok"] = bool(r["time_in_mkt_pct"] >= min(a.min_tim, base["time_in_mkt_pct"]))
                singles.append(r)
            for nm, mk in sorted(xm.items()):
                r = sim(d, None, side=a.side, exit_allow=mk)
                r["switch"] = nm
                r["family"] = "exit"
                r["delta_vs_bh"] = round((r["gain_vs_bh"] or 0) - (base["gain_vs_bh"] or 0), 3)
                r["tim_ok"] = bool(r["time_in_mkt_pct"] >= min(a.min_tim, base["time_in_mkt_pct"]))
                singles.append(r)
            singles.sort(key=lambda r: -r["delta_vs_bh"])
            (OUT / f"{sym}_{a.side}_valued.json").write_text(json.dumps(
                {"baseline": base, "singles": singles}, indent=1))
            pos = [r for r in singles if r["delta_vs_bh"] > 0 and r["tim_ok"]]
            print(f"    {len(pos)}/{len(singles)} switch-values POSITIVE and TIM-compliant", flush=True)
            for r in pos[:10]:
                print(f"    + {r['switch']:<24} d{r['delta_vs_bh']:+.3f} vs_bh={r['gain_vs_bh']} tim={r['time_in_mkt_pct']}%", flush=True)
            continue
        for name, mask in sorted(masks.items()):
            r = sim(d, mask, side=a.side)
            r["switch"] = name
            r["delta_vs_bh"] = round((r["gain_vs_bh"] or 0) - (base["gain_vs_bh"] or 0), 3)
            r["tim_ok"] = bool(r["time_in_mkt_pct"] >= min(a.min_tim, base["time_in_mkt_pct"]))
            singles.append(r)
        for xname, xmask in sorted(exit_masks(d, a.side).items()):
            r = sim(d, None, side=a.side, exit_allow=xmask)
            r["switch"] = xname
            r["delta_vs_bh"] = round((r["gain_vs_bh"] or 0) - (base["gain_vs_bh"] or 0), 3)
            r["tim_ok"] = bool(r["time_in_mkt_pct"] >= min(a.min_tim, base["time_in_mkt_pct"]))
            singles.append(r)
        for mh, lbl in ((3, "EXIT_HOLD_3"), (12, "EXIT_HOLD_12"), (78, "EXIT_HOLD_78")):
            r = sim(d, None, min_hold_bars=mh, side=a.side)
            r["switch"] = lbl
            r["delta_vs_bh"] = round((r["gain_vs_bh"] or 0) - (base["gain_vs_bh"] or 0), 3)
            singles.append(r)
        singles.sort(key=lambda r: -r["delta_vs_bh"])
        print(f"--- {sym} TOP single switches (delta vs baseline; TIM floor {a.min_tim}%):", flush=True)
        for r in singles[:12]:
            print(f"  {r['switch']:<18} vs_bh={r['gain_vs_bh']:>7} (Δ{r['delta_vs_bh']:+.3f}) "
                  f"gain={r['gain_pct']:>8.1f}% tr={r['trades']:>5} ps={r['pool_sharpe']:>7} "
                  f"dd={r['max_dd_pct']:>7.1f}% tim={r['time_in_mkt_pct']}%"
                  f"{'' if r.get('tim_ok', True) else '  <-- REJECTED: below TIM floor'}", flush=True)
        stack_rows = []
        if a.stack:
            chosen, cur = [], None
            xchosen, xcur = [], None
            best = base
            xmasks = exit_masks(d, a.side)
            for _ in range(a.max_stack):
                cand = None
                for xname, xmask in xmasks.items():
                    if xname in xchosen:
                        continue
                    xtrial = xmask if xcur is None else (xcur | xmask)
                    r = sim(d, cur, side=a.side, exit_allow=xtrial)
                    if r["time_in_mkt_pct"] < min(a.min_tim, base["time_in_mkt_pct"]):
                        continue
                    if (r["gain_vs_bh"] or -9) > (best["gain_vs_bh"] or -9) and (
                            cand is None or (r["gain_vs_bh"] or -9) > (cand[1]["gain_vs_bh"] or -9)):
                        cand = (xname, r, None, xtrial)
                for name, mask in masks.items():
                    if name in chosen:
                        continue
                    trial = mask if cur is None else (cur & mask)
                    r = sim(d, trial, side=a.side)
                    if r["time_in_mkt_pct"] < min(a.min_tim, base["time_in_mkt_pct"]):
                        continue  # TIME-IN-MARKET FLOOR — filter is too tight, reject outright
                    if cand is None or (r["gain_vs_bh"] or -9) > (cand[1]["gain_vs_bh"] or -9):
                        cand = (name, r, trial, None)
                if cand is None or (cand[1]["gain_vs_bh"] or -9) <= (best["gain_vs_bh"] or -9):
                    break
                if cand[3] is not None:
                    xchosen.append(cand[0])
                    xcur = cand[3]
                else:
                    chosen.append(cand[0])
                    cur = cand[2]
                best = cand[1]
                stack_rows.append({"stack": list(chosen) + list(xchosen), **best})
                print(f"  STACK+{cand[0]:<18} vs_bh={best['gain_vs_bh']} gain={best['gain_pct']}% "
                      f"tr={best['trades']} ps={best['pool_sharpe']} dd={best['max_dd_pct']}% "
                      f"tim={best['time_in_mkt_pct']}%", flush=True)
        (OUT / f"{sym}_{a.side}_switches.json").write_text(json.dumps(
            {"baseline": base, "singles": singles, "stack": stack_rows}, indent=1))


if __name__ == "__main__":
    main()
