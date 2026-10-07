"""Stocks DISASTER GUARD — vec twin of tradier_manage._disaster_guard_for_entry (:27019) (lane D parity, 2026-10-06).

Live: called from execute_now for trb/trc/tra entry actions (OPEN, REENTRY, REENTRY_OPEN, QUICK_OPEN, REVERSE,
HEDGE_OPEN) on a flat key, except mandatory reclaim / LR_BAND parity reasons. Master DISASTER_GUARD_ENABLED
(config_tradier True). Controls modelled (all per-bar, single-symbol):
  PENNY  LONG px < PENNY_STOCK_LONG_BLOCK_PRICE_USD (PENNY_STOCK_LONG_BLOCK_ENABLED)
  1/2    day return vs day open: SHORT >= +DG_DAILY_GAIN_BLOCK_SHORT_PCT, LONG <= -DG_DAILY_LOSS_BLOCK_LONG_PCT
  3      D / 4h (/1h) candle bias against (SHORT: bias == bull unless px < sma_200_15m; LONG: bias == bear)
  4      RSI: SHORT rsi_15m/rsi_1h >= 65, LONG rsi_15m/rsi_1h <= 35
  10     SHORT needs D or 4h bearish (or px < sma_200_15m)  (DG_WT_3M_REQUIRE_HTF_CONFIRM)
  11     SHORT blocked when wt1_D > wt2_D (both non-zero) unless px < sma_200_15m
NOT modelled (portfolio / broker / force-open only): 5, 6, 7, 8, 9.
DATA NOTE (documented approximation): live open_D/close_D/open_4h/close_4h are the FORMING candles
(tradier_indicators iloc[-1]); the NPZ carries COMPLETED HTF candles. The twin rebuilds the forming DAY candle
from the 15m bars (day open = open of the first RTH 15m bar of the ET date; close = bar close). The 4h/1h
candles, rsi_1h and wt1/2_D use the NPZ (completed) values.
"""
from __future__ import annotations

import numpy as np


def _f(cfg, k, d):
    try:
        v = getattr(cfg, k, d)
        return float(d) if isinstance(v, bool) else float(v)
    except Exception:
        return float(d)


def _b(cfg, k, d):
    try:
        return bool(getattr(cfg, k, d))
    except Exception:
        return bool(d)


def _bias(c, o):
    c = np.asarray(c, dtype=float); o = np.asarray(o, dtype=float)
    ok = (c > 0) & (o > 0)
    return np.where(ok, np.sign(c - o), 0.0).astype(int)


def forming_day_open(ts_label, open_15m, open_D_completed, rth_mask):
    """Day open = open of the first RTH 15m bar of each ET date; before it, fall back to the NPZ open_D."""
    from vec_decisions.stocks_live_session_gates import _NY
    import datetime as _dt
    ts = np.asarray(ts_label, dtype=float)
    n = len(ts)
    out = np.asarray(open_D_completed, dtype=float).copy()
    if _NY is None:
        return out
    cur_day, cur_open = None, 0.0
    for i in range(n):
        if not np.isfinite(ts[i]) or ts[i] <= 0:
            continue
        d = _dt.datetime.fromtimestamp(ts[i], _NY).date()
        if d != cur_day:
            cur_day, cur_open = d, 0.0
        if cur_open <= 0 and bool(rth_mask[i]) and float(open_15m[i]) > 0:
            cur_open = float(open_15m[i])
        if cur_open > 0:
            out[i] = cur_open
    return out


def block_mask(npz, n, is_long, cfg, close, safe, ts_label, rth_mask):
    """bool[n] True = live disaster guard REFUSES the entry on that bar."""
    if not _b(cfg, 'DISASTER_GUARD_ENABLED', True):
        return None
    close = np.asarray(close, dtype=float)
    blk = np.zeros(n, dtype=bool)
    if is_long and _b(cfg, 'PENNY_STOCK_LONG_BLOCK_ENABLED', False):
        thr = _f(cfg, 'PENNY_STOCK_LONG_BLOCK_PRICE_USD', 5.0)
        if thr > 0:
            blk |= (close > 0) & (close < thr)
    open_15m = np.asarray(safe(npz, 'open_15m', n, 0.0), dtype=float)
    open_D_c = np.asarray(safe(npz, 'open_D', n, 0.0), dtype=float)
    day_open = forming_day_open(ts_label, open_15m, open_D_c, rth_mask)
    sma200 = np.asarray(safe(npz, 'sma_200_15m', n, 0.0), dtype=float)
    below_sma = (not is_long) & _b(cfg, 'DG_SMA200_SHORT_BYPASS', True) & (sma200 > 0) & (close < sma200)
    # 1/2 day return
    with np.errstate(divide='ignore', invalid='ignore'):
        day_ret = np.where(day_open > 0, (close - day_open) / np.where(day_open > 0, day_open, 1.0) * 100.0, 0.0)
    if is_long:
        blk |= (day_open > 0) & (day_ret <= -_f(cfg, 'DG_DAILY_LOSS_BLOCK_LONG_PCT', 2.5))
    else:
        blk |= (day_open > 0) & (day_ret >= _f(cfg, 'DG_DAILY_GAIN_BLOCK_SHORT_PCT', 2.5))
    # 3 candle bias (forming D from 15m bars; 4h/1h from NPZ completed candles)
    bias_D = _bias(close, day_open)
    bias_4h = _bias(safe(npz, 'close_4h', n, 0.0), safe(npz, 'open_4h', n, 0.0))
    bias_1h = _bias(safe(npz, 'close_1h', n, 0.0), safe(npz, 'open_1h', n, 0.0))
    want = 1 if is_long else -1
    if _b(cfg, 'DG_HTF_ALIGN_REQUIRE_D', True):
        blk |= ((bias_D == 1) & ~below_sma) if not is_long else (bias_D == -1)
    if _b(cfg, 'DG_HTF_ALIGN_REQUIRE_4H', True):
        blk |= ((bias_4h == 1) & ~below_sma) if not is_long else (bias_4h == -1)
    if _b(cfg, 'DG_HTF_ALIGN_REQUIRE_1H', False):
        blk |= (bias_1h != 0) & (bias_1h == -want)
    # 4 RSI (live safe_fetch_float(get(k, 50)): missing -> 50)
    r15 = np.asarray(safe(npz, 'rsi_15m', n, 50.0), dtype=float)
    r1 = np.asarray(safe(npz, 'rsi_1h', n, 50.0), dtype=float)
    if is_long:
        blk |= (r15 <= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG', 35.0)) | (r1 <= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG', 35.0))
    else:
        blk |= (r15 >= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT', 65.0)) | (r1 >= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT', 65.0))
    if not is_long:
        # 10 short needs D or 4h bearish (or below sma200)
        if _b(cfg, 'DG_WT_3M_REQUIRE_HTF_CONFIRM', True):
            agree = (bias_D == want) | (bias_4h == want) | below_sma
            blk |= ~agree
        # 11 WT daily bullish blocks shorts unless below sma200
        w1 = np.asarray(safe(npz, 'wt1_D', n, 0.0), dtype=float)
        w2 = np.asarray(safe(npz, 'wt2_D', n, 0.0), dtype=float)
        blk |= (~below_sma) & (w1 != 0) & (w2 != 0) & (w1 > w2)
    return blk


def live_scalar(ind, px, is_long, cfg):
    """Scalar replica of the modelled live controls on one indicator dict (penny,1,2,3,4,10,11)."""
    if not _b(cfg, 'DISASTER_GUARD_ENABLED', True):
        return False
    if _b(cfg, 'PENNY_STOCK_LONG_BLOCK_ENABLED', False):
        thr = _f(cfg, 'PENNY_STOCK_LONG_BLOCK_PRICE_USD', 5.0)
        if is_long and thr > 0 and 0 < px < thr:
            return True
    g = lambda k, d=0.0: float(ind.get(k, d) or 0.0) if ind.get(k, d) is not None else 0.0
    sma = g('sma_200_15m')
    below = (not is_long) and _b(cfg, 'DG_SMA200_SHORT_BYPASS', True) and sma > 0 and px < sma
    day_open = g('open_D') or g('open_1h')
    if day_open > 0:
        r = (px - day_open) / day_open * 100.0
        if (not is_long) and r >= _f(cfg, 'DG_DAILY_GAIN_BLOCK_SHORT_PCT', 2.5):
            return True
        if is_long and r <= -_f(cfg, 'DG_DAILY_LOSS_BLOCK_LONG_PCT', 2.5):
            return True

    def bias(c, o):
        if c <= 0 or o <= 0:
            return 0
        return 1 if c > o else (-1 if c < o else 0)
    bD = bias(g('close_D'), g('open_D')); b4 = bias(g('close_4h'), g('open_4h')); b1 = bias(g('close_1h'), g('open_1h'))
    want = 1 if is_long else -1
    if _b(cfg, 'DG_HTF_ALIGN_REQUIRE_D', True):
        if (not is_long) and bD == 1 and not below:
            return True
        if is_long and bD != 0 and bD == -want:
            return True
    if _b(cfg, 'DG_HTF_ALIGN_REQUIRE_4H', True):
        if (not is_long) and b4 == 1 and not below:
            return True
        if is_long and b4 != 0 and b4 == -want:
            return True
    if _b(cfg, 'DG_HTF_ALIGN_REQUIRE_1H', False) and b1 != 0 and b1 == -want:
        return True
    r15 = float(ind.get('rsi_15m', 50) or 0.0); r1 = float(ind.get('rsi_1h', 50) or 0.0)
    if (not is_long) and (r15 >= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI15M_FOR_SHORT', 65.0) or r1 >= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI1H_FOR_SHORT', 65.0)):
        return True
    if is_long and (r15 <= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI15M_FOR_LONG', 35.0) or r1 <= _f(cfg, 'DG_MOMENTUM_BLOCK_RSI1H_FOR_LONG', 35.0)):
        return True
    if (not is_long) and _b(cfg, 'DG_WT_3M_REQUIRE_HTF_CONFIRM', True):
        if not ((bD == want and bD != 0) or (b4 == want and b4 != 0) or below):
            return True
    if (not is_long) and not below:
        w1 = g('wt1_D'); w2 = g('wt2_D')
        if w1 != 0 and w2 != 0 and w1 > w2:
            return True
    return False
