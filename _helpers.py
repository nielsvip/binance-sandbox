def linreg_features(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if series is None or len(series) < length:
        return None, None
    window = series.iloc[-length:]
    x = np.arange(len(window))
    y = window.values.astype(float)
    x_mean = np.mean(x)
    y_mean = np.mean(y)
    denominator = np.sum((x - x_mean) ** 2)
    if denominator == 0:
        return None, None
    slope = np.sum((x - x_mean) * (y - y_mean)) / denominator
    y_fit = x_mean + slope * (x - x_mean)
    residuals = y - y_fit
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y - y_mean) ** 2)
    linearity = 1 - ss_res / ss_tot if ss_tot != 0 else 0
    return float(slope), float(linearity)

def donchian(high: pd.Series, low: pd.Series, window: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(high) < window or len(low) < window:
        return None, None, None
    high_series = high.rolling(window, min_periods=window).max()
    low_series = low.rolling(window, min_periods=window).min()
    if high_series.empty or low_series.empty:
        return None, None, None
    high_val = high_series.iloc[-1]
    low_val = low_series.iloc[-1]
    if pd.isna(high_val) or pd.isna(low_val):
        return None, None, None
    basis_val = (high_val + low_val) / 2.0
    return float(high_val), float(low_val), float(basis_val)

def donchian_prev(high: pd.Series, low: pd.Series, window: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(high) < window + 1 or len(low) < window + 1:
        return None, None, None
    high_series = high.rolling(window, min_periods=window).max()
    low_series = low.rolling(window, min_periods=window).min()
    if len(high_series) < 2 or len(low_series) < 2:
        return None, None, None
    high_val = high_series.iloc[-2]
    low_val = low_series.iloc[-2]
    if pd.isna(high_val) or pd.isna(low_val):
        return None, None, None
    basis_val = (high_val + low_val) / 2.0
    return float(high_val), float(low_val), float(basis_val)

def wavetrend(df: pd.DataFrame) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    WT_N1 = 10
    WT_N2 = 21
    if len(df) < max(WT_N1, WT_N2):
        return None, None
    typical = (df["high"] + df["low"] + df["close"]) / 3
    esa = typical.ewm(span=WT_N1, adjust=False).mean()
    d = (typical - esa).abs().ewm(span=WT_N1, adjust=False).mean()
    ci = (typical - esa) / (0.015 * d.replace(0, 1e-10))
    wt1 = ci.ewm(span=WT_N2, adjust=False).mean()
    wt2 = wt1.rolling(window=3, min_periods=1).mean()
    return wt1, wt2

def heikin_ashi(df: pd.DataFrame) -> Tuple[str, Optional[str]]:
    if df.empty:
        return "neutral", None
    ha_close = (df["open"].iloc[-1] + df["high"].iloc[-1] + df["low"].iloc[-1] + df["close"].iloc[-1]) / 4.0
    if len(df) >= 2:
        prev_close = (df["open"].iloc[-2] + df["high"].iloc[-2] + df["low"].iloc[-2] + df["close"].iloc[-2]) / 4.0
        prev_open = (df["open"].iloc[-2] + df["close"].iloc[-2]) / 2.0
    else:
        prev_close = df["close"].iloc[-1]
        prev_open = df["open"].iloc[-1]
    ha_open = (prev_close + prev_open) / 2.0
    current_color = "green" if ha_close >= ha_open else "red"
    prev_color = None
    if len(df) >= 2:
        if len(df) >= 3:
            p_close = (df["open"].iloc[-3] + df["high"].iloc[-3] + df["low"].iloc[-3] + df["close"].iloc[-3]) / 4.0
            p_open = (df["open"].iloc[-3] + df["close"].iloc[-3]) / 2.0
        else:
            p_close = df["close"].iloc[-2]
            p_open = df["open"].iloc[-2]
        prev_color = "green" if p_close >= p_open else "red"
    return current_color, prev_color

def wma(series: pd.Series, length: int) -> pd.Series:
    """Weighted Moving Average"""
    if len(series) < length or length <= 0: return pd.Series(index=series.index, dtype=float)
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(lambda x: np.sum(weights * x) / np.sum(weights), raw=True)

def hma(series: pd.Series, length: int) -> pd.Series:
    """Hull Moving Average"""
    if len(series) < length: return pd.Series(index=series.index, dtype=float)
    half_length = max(1, int(length / 2))
    sqrt_length = max(1, int(np.sqrt(length)))
    wma_half = wma(series, half_length)
    wma_full = wma(series, length)
    diff = 2 * wma_half - wma_full
    return wma(diff, sqrt_length)

def thma(series: pd.Series, length: int) -> pd.Series:
    """Triple Hull Moving Average"""
    if len(series) < length: return pd.Series(index=series.index, dtype=float)
    len_6, len_4, len_2 = max(1, length // 6), max(1, length // 4), max(1, length // 2)
    wma_6, wma_4, wma_2 = wma(series, len_6), wma(series, len_4), wma(series, len_2)
    return wma(3 * wma_6 - wma_4 - wma_2, len_2)

def hull_trend_indicators(close_series: pd.Series, length_short: int = 9, length_long: int = 21) -> Tuple[Optional[bool], Optional[bool], Optional[bool]]:
    if len(close_series) < max(length_long, 3): return None, None, None
    try:
        thma_short = thma(close_series, length_short)
        thma_long = thma(close_series, length_long)
        hulle_short = hma(thma_short, 3)
        hulle_long = hma(thma_long, 3)
        if len(hulle_short) < 3: return None, None, None
        
        # Trend Up?
        t_up = bool(hulle_short.iloc[-1] > hulle_long.iloc[-1])
        
        # Swing Signals
        cur, prev, prev2 = hulle_short.iloc[-1], hulle_short.iloc[-2], hulle_short.iloc[-3]
        swingbuy = (cur >= prev) and (prev < prev2)
        swingsell = (cur <= prev) and (prev > prev2)
        
        return t_up, swingbuy, swingsell
    except Exception: return None, None, None

STOCH_LEN = 14
STOCH_K = 3
STOCH_D = 3

def stoch_result(series: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], bool, bool]:
    def fallback(window: pd.Series) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], bool, bool]:
        if window.empty:
            return None, None, None, None, False, False
        max_val = float(window.max())
        min_val = float(window.min())
        if max_val == min_val:
            k_curr = d_curr = 50.0
            k_prev = d_prev = 50.0
        else:
            k_curr = float((window.iloc[-1] - min_val) / (max_val - min_val) * 100.0)
            prev_idx = -2 if len(window) > 1 else -1
            prev_val = window.iloc[prev_idx]
            k_prev = float((prev_val - min_val) / (max_val - min_val) * 100.0)
            d_curr = k_curr
            d_prev = k_prev
        crossover = k_prev <= d_prev and k_curr > d_curr
        crossunder = k_prev >= d_prev and k_curr < d_curr
        return k_curr, d_curr, k_prev, d_prev, crossover, crossunder
    length = STOCH_LEN
    if len(series) >= length: 
        stoch = stoch_rsi(series, length=length, k=STOCH_K, d=STOCH_D)
        if stoch is not None and not stoch.empty:
            k = stoch.iloc[:, 0].clip(lower=0, upper=100)
            d = stoch.iloc[:, 1].clip(lower=0, upper=100)
            
            # Ensure we have valid data at the end
            if not k.empty and pd.notna(k.iloc[-1]):
                k_curr = float(k.iloc[-1])
                d_curr = float(d.iloc[-1]) if not d.empty and pd.notna(d.iloc[-1]) else k_curr
                
                # Handle previous values safely
                if len(k) > 1 and pd.notna(k.iloc[-2]):
                    k_prev = float(k.iloc[-2])
                else:
                    k_prev = k_curr
                    
                if len(d) > 1 and pd.notna(d.iloc[-2]):
                    d_prev = float(d.iloc[-2])
                else:
                    d_prev = d_curr

                crossover = k_prev <= d_prev and k_curr > d_curr
                crossunder = k_prev >= d_prev and k_curr < d_curr
                
                return k_curr, d_curr, k_prev, d_prev, crossover, crossunder
    window = series.iloc[-min(len(series), max(2, STOCH_LEN)) :]
    return fallback(window)

def atr_values(df: pd.DataFrame, short_len: int, long_len: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(df) < short_len:
        return None, None, None
    atr_short = atr_series(df, short_len)
    atr_long = atr_series(df, long_len) if len(df) >= long_len else None
    if atr_short is None or atr_short.empty:
        return None, None, None
    curr = atr_short.iloc[-1]
    prev = atr_short.iloc[-2] if len(atr_short) > 1 else curr
    long_val = atr_long.iloc[-1] if atr_long is not None and not atr_long.empty else None
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None, float(long_val) if long_val is not None and pd.notna(long_val) else None)

def mfi_value(df: pd.DataFrame) -> Optional[float]:
    if len(df) < 15:
        return None
    data = df.loc[:, ["high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce").astype(np.float64, copy=True)
    data = data.dropna()
    if len(data) < 15:
        return None
    typical_price = (data["high"] + data["low"] + data["close"]) / 3.0
    money_flow = typical_price * data["volume"]
    price_delta = typical_price.diff()
    positive_flow = money_flow.where(price_delta > 0.0, 0.0)
    negative_flow = money_flow.where(price_delta < 0.0, 0.0)
    positive_sum = positive_flow.rolling(14, min_periods=14).sum()
    negative_sum = negative_flow.rolling(14, min_periods=14).sum()
    if positive_sum.empty or negative_sum.empty:
        return None
    pos = positive_sum.iloc[-1]
    neg = negative_sum.iloc[-1]
    if pd.isna(pos) or pd.isna(neg):
        return None
    if pos == 0.0 and neg == 0.0:
        return 50.0
    if neg == 0.0:
        return 100.0
    ratio = pos / neg
    mfi = 100.0 - (100.0 / (1.0 + ratio))
    return float(mfi)

def ema_pair(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if len(series) < length:
        return None, None
    ema_series = series.ewm(span=length, adjust=False).mean()
    curr = ema_series.iloc[-1]
    prev = ema_series.iloc[-2] if len(ema_series) > 1 else curr
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None)

def ema_std(series: pd.Series, ema_length: int, std_window: int = 20) -> Optional[float]:
    if len(series) < max(ema_length, std_window):
        return None
    ema_series = series.ewm(span=ema_length, adjust=False).mean()
    deviation = (series - ema_series).abs()
    std_series = deviation.rolling(window=std_window, min_periods=std_window).std()
    if std_series.empty or pd.isna(std_series.iloc[-1]):
        return None
    return float(std_series.iloc[-1])

def sma_pair(series: pd.Series, length: int) -> Tuple[Optional[float], Optional[float]]:
    if len(series) < length:
        return None, None
    sma_series = series.rolling(length, min_periods=length).mean()
    if sma_series.empty:
        return None, None
    curr = sma_series.iloc[-1]
    prev = sma_series.iloc[-2] if len(sma_series) > 1 else curr
    return (float(curr) if pd.notna(curr) else None, float(prev) if pd.notna(prev) else None)

def relative_volume(df: pd.DataFrame, length: int) -> Optional[float]:
    if len(df) < length + 1:
        return None
    # Use the second-to-last bar (last completed bar) for stable relative volume calculation
    # denom is the average of the 'length' bars before the last one
    volumes = df["volume"].astype(float)
    numerator = volumes.iloc[-2]
    denominator = volumes.iloc[-(length+1):-1].mean()
    if pd.isna(denominator) or denominator == 0:
        return None
    return float(numerator / denominator)

def rsi_value(series: pd.Series, length: int) -> Optional[float]:
    if len(series) < length:
        return None
    rsi_series_obj = rsi_series(series, length)
    if rsi_series_obj is None or rsi_series_obj.empty:
        return None
    value = rsi_series_obj.iloc[-1]
    return float(value) if pd.notna(value) else None

def crossover_flags(price: float, price_prev: float, ref: Optional[float], ref_prev: Optional[float]) -> Tuple[bool, bool]:
    if ref is None or ref_prev is None:
        return False, False
    cross_over = price_prev <= ref_prev and price > ref
    cross_under = price_prev >= ref_prev and price < ref
    return cross_over, cross_under
