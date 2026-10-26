import numpy as np
import pandas as pd


def weighted_moving_average(series, length):
    if len(series) < length or length <= 0:
        return pd.Series(index=series.index, dtype=float)
    values = series.to_numpy(dtype='float64', na_value=np.nan)
    weights = np.arange(1, length + 1)
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    result = np.full(len(series), np.nan)
    denominator = np.sum(weights)
    for left in range(0, len(windows), 4096):
        block = windows[left:left + 4096]
        with np.errstate(invalid='ignore'):
            averaged = np.sum(weights * block, axis=1) / denominator
        averaged[~np.isfinite(block).all(axis=1)] = np.nan
        result[length - 1 + left:length - 1 + left + len(block)] = averaged
    return pd.Series(result, index=series.index, name=series.name)


def confirmed_pivots(arr, lookback=5):
    n = len(arr)
    high, low = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    if n <= 2 * lookback:
        return high, low
    windows = np.lib.stride_tricks.sliding_window_view(arr, 2 * lookback + 1)
    center = windows[:, lookback]
    left, right = windows[:, :lookback], windows[:, lookback + 1:]
    high[lookback:n - lookback] = (center > left.max(axis=1)) & (center > right.max(axis=1))
    low[lookback:n - lookback] = (center < left.min(axis=1)) & (center < right.min(axis=1))
    return high, low
