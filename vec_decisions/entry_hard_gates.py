"""ENTRY_HARD_GATES — vector twins of the live scorer HARD gates (return WAIT/BOYCOTT before any score is added). Agent C2, staged b2.

Live entries are SCORE based; the scorer first applies hard gates that remove a candidate outright. The vector engine had none of the
default-ON ones, so it traded setups live refuses (live default: SHORT_RSI_MIN_1H=40, LONG_STOCH_CHASE_BLOCK=True, ENTRY_ATR_PCT_MIN=1.5,
stocks BACKTEST_VALIDATED_GATES_TRADIER=True ...). Pure numpy over the NPZ arrays (15m-floor convention of the engine: no 3m/5m arrays, so
K3M_CAP / WT_CHOP / EXHAUST(3m) gates are NOT ported — UNWIRABLE_NO_3M, listed in MANIFEST).

CRYPTO  live source = ez_positions_quick.AdvancedSignalRater.rate (ez_positions_quick.py, line numbers below are ABSOLUTE in that file =
        rate_fn_line + 2115):
  SMA200D extreme (hard-coded, no switch)    rate 693-694  -> abs 2808-2809 : LONG dist>+35%, SHORT dist<-35% of sma_200_D
  ADX_REGIME_FILTER_ENABLED/ADX_TF/ADX_RANGING_THRESHOLD   rate 570-577 : adx_{TF}<thr and NOT (k_focus<20 long / >80 short)  [only if REGIME_DETECTION_ENABLED False]
  SHORT_RSI_MIN_1H           rate 721 : SHORT blocked when rsi_1h < min (>0)
  LONG_STOCH_CHASE_BLOCK     rate 722-727 : LONG blocked when k_1h>70 and ha_streak_1h>2
  ENTRY_ATR_PCT_MIN          rate 729-731 : blocked when atr_1h/price*100 < min
  WT_COMPOSITE_DELTA_GATE_*  rate 151-158 : LONG blocked when wt_composite_delta < LONG_MIN, SHORT when > SHORT_MAX
  BASIS_CONDITION            rate 66-91  : LONG needs close>=dc_basis_15m,1h ; k_15m>=d_15m ; close>=sma_200_1h ; SHORT mirror (needs all 4 basis>0)
STOCKS  live source = tradier_manage.TradierManager.calculate_signal_score (tradier_manage.py:16916+):
  BACKTEST_VALIDATED_GATES_TRADIER (default True) 16938-16970 : ATR%4h>3, ATR%1h>2.5, MFI4h OB(>85 long)/OS(<15 short), |wt1|>60 on 1h or 4h,
                               K4h>90 long / <10 short, DC width squeeze (4h<1.5 or 1h<1.0), DC pos 4h top >0.9 long / bottom <0.1 short, wt_velocity_1h <-5 long / >5 short
  CT_WT_VELOCITY_GATE_ENABLED (default False) 16967-16970
  ENTRY_ATR_PCT_MIN 16971-16975, ENTRY_VOL_MIN_RATIO 16976-16980 (live max(rvol_5m,rvol_15m); NPZ has rel_vol_15m only -> 15m)
  LONG/SHORT_STOCH_CHASE_BLOCK (veto path) 13337-13343
"""
from __future__ import annotations

import numpy as np


def _f(cfg, k, d):
    v = getattr(cfg, k, d)
    return d if v is None else v


def crypto_block(npz, n, is_long, cfg, close, safe, k_focus):
    """True = candidate removed by a live rate() hard gate."""
    blk = np.zeros(n, dtype=bool)
    px_ok = close > 0
    sma_d = safe(npz, 'sma_200_D', n, 0.0)
    ok = (sma_d > 0) & px_ok
    dist = np.where(ok, (close - sma_d) / np.where(sma_d > 0, sma_d, 1.0), 0.0)
    blk |= ok & ((dist > 0.35) if is_long else (dist < -0.35))
    if bool(_f(cfg, 'ADX_REGIME_FILTER_ENABLED', False)) and not bool(_f(cfg, 'REGIME_DETECTION_ENABLED', False)):
        adx = safe(npz, f"adx_{str(_f(cfg, 'ADX_TF', '1h'))}", n, 50.0)
        thr = float(_f(cfg, 'ADX_RANGING_THRESHOLD', 20.0))
        extreme = (k_focus < 20) if is_long else (k_focus > 80)
        blk |= (adx < thr) & ~extreme
    smin = float(_f(cfg, 'SHORT_RSI_MIN_1H', 0) or 0)
    if (not is_long) and smin > 0:
        blk |= safe(npz, 'rsi_1h', n, 50.0) < smin
    # LONG_STOCH_CHASE_BLOCK already lives in compute_entry_signals.extra_ok (both venues) — not duplicated here
    amin = float(_f(cfg, 'ENTRY_ATR_PCT_MIN', 0) or 0)
    if amin > 0:
        atr = safe(npz, 'atr_1h', n, 0.0)
        blk |= (atr > 0) & px_ok & ((atr / np.where(px_ok, close, 1.0)) * 100.0 < amin)
    if bool(_f(cfg, 'WT_COMPOSITE_DELTA_GATE_ENABLED', True)) and 'wt_composite_delta' in npz:
        cd = safe(npz, 'wt_composite_delta', n, 0.0)
        blk |= (cd < float(_f(cfg, 'WT_COMPOSITE_DELTA_LONG_MIN', -100.0))) if is_long else (cd > float(_f(cfg, 'WT_COMPOSITE_DELTA_SHORT_MAX', 100.0)))
    if bool(_f(cfg, 'BASIS_CONDITION', False)):
        b15, b1h, b4h, bd = (safe(npz, f'dc_basis_{t}', n, 0.0) for t in ('15m', '1h', '4h', 'D'))
        have = (b15 > 0) & (b1h > 0) & (b4h > 0) & (bd > 0)
        k15, d15 = safe(npz, 'stoch_k_15m', n, 50.0), safe(npz, 'stoch_d_15m', n, 50.0)
        s1h = safe(npz, 'sma_200_1h', n, 0.0)
        if is_long:
            bad = (close < b15) | (close < b1h) | (k15 < d15) | ((s1h > 0) & (close < s1h))
        else:
            bad = (close > b15) | (close > b1h) | (close > b4h) | (close > bd) | (k15 > d15) | ((s1h > 0) & (close > s1h))
        blk |= have & bad
    return blk


def tradier_block(npz, n, is_long, cfg, close, safe):
    """True = candidate removed by a live calculate_signal_score hard gate (stocks)."""
    blk = np.zeros(n, dtype=bool)
    px_ok = close > 0
    pxd = np.where(px_ok, close, 1.0)
    atr1, atr4 = safe(npz, 'atr_1h', n, 0.0), safe(npz, 'atr_4h', n, 0.0)
    wt_vel = safe(npz, 'wt_velocity_1h', n, 0.0)
    if bool(_f(cfg, 'BACKTEST_VALIDATED_GATES_TRADIER', True)):
        mfi4, w1h, w4h, k4 = safe(npz, 'mfi_4h', n, 50.0), safe(npz, 'wt1_1h', n, 0.0), safe(npz, 'wt1_4h', n, 0.0), safe(npz, 'stoch_k_4h', n, 50.0)
        dcw1, dcw4, dcp4 = safe(npz, 'dc_width_1h', n, 10.0), safe(npz, 'dc_width_4h', n, 10.0), safe(npz, 'dc_position_4h', n, 0.5)
        blk |= (atr4 > 0) & px_ok & (atr4 / pxd * 100.0 > 3.0)
        blk |= (atr1 > 0) & px_ok & (atr1 / pxd * 100.0 > 2.5)
        blk |= (dcw4 < 1.5) | (dcw1 < 1.0)
        if is_long:
            blk |= (mfi4 > 85) | (w1h > 60) | (w4h > 60) | (k4 > 90) | (dcp4 > 0.9) | (wt_vel < -5)
        else:
            blk |= (mfi4 < 15) | (w1h < -60) | (w4h < -60) | (k4 < 10) | (dcp4 < 0.1) | (wt_vel > 5)
        if bool(_f(cfg, 'CT_WT_VELOCITY_GATE_ENABLED', False)):
            vmin = float(_f(cfg, 'CT_WT_VELOCITY_1H_MIN', 0.0))
            blk |= (wt_vel < vmin) if is_long else (wt_vel > -vmin)
        amin = float(_f(cfg, 'ENTRY_ATR_PCT_MIN', 0.0) or 0.0)   # live: inside the BV block (tradier_manage.py:16971-16980)
        if amin > 0:
            blk |= (atr1 > 0) & px_ok & (atr1 / pxd * 100.0 < amin)
        vmin_r = float(_f(cfg, 'ENTRY_VOL_MIN_RATIO', 0.0) or 0.0)
        if vmin_r > 0:
            rv = np.maximum(safe(npz, 'rel_vol_5m', n, 0.0), safe(npz, 'rel_vol_15m', n, 0.0))
            blk |= rv < vmin_r
    return blk
