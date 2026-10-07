"""SMA20_D — derive the live `sma_20_D` series from the NPZ `close_D` array (Agent UNW-V, queue UNWV/002).

The frozen NPZ carries close_D / sma_200_D but NO sma_20_D, so BULL_HOLD_* / BEAR_HOLD_* / AUGMENT_BULL_KILL (close_D vs sma_20_D regime) were inert in the vector.
Live (ez_indicators.py ~2436 `sma_pair(close_series, 20)`, tradier_manage.py ~22196 `sum(closes[-20:])/20`) = mean of the last 20 DAILY closes including the current daily bar.
Here: per bar, (sum of the 19 last COMPLETED days' final close_D + the current close_D) / 20; fewer than 19 completed days -> returns close_d unchanged (regime false, same as 'no data').
Day key = UTC date of the bar timestamp (crypto 24h; stocks RTH bars 13:30-20:00 UTC fall inside one UTC date)."""
import numpy as np


def sma20_d(npz, n, close_d):
    try:
        ts = npz.get('timestamps')
        if ts is None or len(ts) < n:
            return close_d
        ts = np.asarray(ts[:n], dtype=np.float64)
        if ts.size and ts.max() > 1e12:
            ts = ts / 1000.0
        day = (ts // 86400.0).astype(np.int64)
        cd = np.asarray(close_d, dtype=np.float64)[:n]
        change = np.concatenate([[True], day[1:] != day[:-1]])
        didx = np.cumsum(change) - 1                       # day ordinal per bar
        nd = int(didx[-1]) + 1
        last_close = np.zeros(nd, dtype=np.float64)
        last_close[didx] = cd                              # later bars of the same day overwrite: final close of the day
        csum = np.concatenate([[0.0], np.cumsum(last_close)])   # csum[k] = sum of days 0..k-1
        d = didx
        ok = d >= 19
        prev19 = np.where(ok, csum[np.maximum(d, 0)] - csum[np.maximum(d - 19, 0)], 0.0)
        out = np.where(ok, (prev19 + cd) / 20.0, cd)
        out = np.where(np.isfinite(out) & (out > 0), out, cd)
        if out.shape[0] < len(close_d):
            out = np.concatenate([out, np.asarray(close_d)[out.shape[0]:]])
        return out
    except Exception:
        return close_d
