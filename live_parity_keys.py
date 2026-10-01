"""live_parity_keys — LIVE-side producers for indicator keys that the vectorized engine (NPZ precompute) has and that live consumers read, but that
no live code ever produced (Agent D, 2026-10-01; see data/live_parity/report.md). Pure functions, no I/O, no config reads: they only ADD keys to a dict
the live indicator pipeline already builds. Formulas are the NPZ-precompute formulas (backtest_v8_precompute.py) so live and vec see the same value.

derive_alias_keys(ind)   alias keys derived from keys live already has (called from normalize_indicator_aliases, which runs on the final dict):
    wt_peak_value_{tf}   := wt_peak_{tf}            (live consumers: ez_manage/tradier_manage evaluate_multi_tf_exit WT_DIV_EXIT)
    wt_trough_value_{tf} := wt_trough_{tf}
    ha_green_{tf}        := +1.0 green / -1.0 red (neutral/absent: key not set)   == NPZ ha_{tf} int8 (+1/-1/0) encoding.
        NOTE the live consumer reads g(f'ha_green_{tf}', 0.5) with g = float(i.get(k,d) or d): 0.0 is falsy -> 0.5, so the red/green encoding MUST be
        -1.0/+1.0 (not 0/1): red -> -1.0 (<0.5: long STRUCT point), green -> +1.0 (>0.5: short STRUCT point). The vec twin must apply the same
        'value or 0.5' rule on NPZ ha_{tf} (1/-1/0).
derive_bar_keys(df, tf, result)  keys that need the bar DataFrame (called at the end of IndicatorCalculator.compute):
    close_3bar_{tf} / close_5bar_{tf}  rolling(3|5, min_periods=1).mean() of close incl. the current bar   (MOM3/MOM5 entry + MOM3_FILTER_TF)
    ema_9_above_21_{tf}                int(ewm9(adjust=False) > ewm21(adjust=False)) of the last bar
    volume_sma_1h                      volume.rolling(20, min_periods=1).mean() (1h only, as in the NPZ)
    choppiness_4h                      LazyBear choppiness over the last 14 4h bars (live choppiness_index formula; NOTE the NPZ computes it over 14 BASE
                                       bars of the broadcast 4h arrays -> SEMANTIC_MISMATCH, vec must be corrected to this)
"""
import math

TFS = ("1m", "3m", "5m", "15m", "1h", "4h", "D", "W")


def derive_alias_keys(ind):
    if not isinstance(ind, dict):
        return ind
    for tf in TFS:
        pk = ind.get(f"wt_peak_{tf}")
        if pk is not None and f"wt_peak_value_{tf}" not in ind:
            ind[f"wt_peak_value_{tf}"] = pk
        tr = ind.get(f"wt_trough_{tf}")
        if tr is not None and f"wt_trough_value_{tf}" not in ind:
            ind[f"wt_trough_value_{tf}"] = tr
        ha = ind.get(f"ha_{tf}")
        if isinstance(ha, str) and f"ha_green_{tf}" not in ind:
            h = ha.strip().lower()
            if h == "green":
                ind[f"ha_green_{tf}"] = 1.0
            elif h == "red":
                ind[f"ha_green_{tf}"] = -1.0
    return ind


def _chop_4h(df, length=14):
    if df is None or len(df) < length + 2:
        return None
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = (h - l).combine((h - c.shift(1)).abs(), max).combine((l - c.shift(1)).abs(), max)
    tr.iloc[0] = float(h.iloc[0] - l.iloc[0])
    atr_sum = float(tr.iloc[-length:].sum())
    rng = float(h.iloc[-length:].max()) - float(l.iloc[-length:].min())
    if rng <= 0 or atr_sum <= 0:
        return None
    return max(0.0, min(100.0, 100.0 * math.log10(atr_sum / rng) / math.log10(float(length))))


def derive_bar_keys(df, tf, result):
    try:
        if df is None or len(df) < 2 or not isinstance(result, dict):
            return result
        close = df["close"].astype(float)
        result[f"close_3bar_{tf}"] = float(close.rolling(3, min_periods=1).mean().iloc[-1])
        result[f"close_5bar_{tf}"] = float(close.rolling(5, min_periods=1).mean().iloc[-1])
        e9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        e21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        result[f"ema_9_above_21_{tf}"] = int(e9 > e21)
        if tf == "1h" and "volume" in df.columns:
            result["volume_sma_1h"] = float(df["volume"].astype(float).rolling(20, min_periods=1).mean().iloc[-1])
        if tf == "4h":
            ch = _chop_4h(df)
            if ch is not None:
                result["choppiness_4h"] = ch
    except Exception:
        pass  # a producer must never break the live indicator cycle
    return result
