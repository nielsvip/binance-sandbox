#!/usr/bin/env python3
"""Backtest V4 Phase 1 — TRADIER (Stocks) Indicator Precomputation.

Same architecture as backtest_v4_precompute.py but for stocks:
- Uses 5m (not 3m) as micro timeframe
- Stock-specific WT parameters (from tradier_indicators.py)
- Market hours filtering (9:30-16:00 ET = 13:30-20:00 UTC)
- EMA [20, 50, 200] instead of [9, 14, 20]
- $70k on the table — this is the priority system

Usage:
    python backtest_v4_precompute_tradier.py                   # All stock symbols
    python backtest_v4_precompute_tradier.py --symbols 10      # Quick test
    python backtest_v4_precompute_tradier.py --inspect AAPL
    python backtest_v4_precompute_tradier.py --inspect NVDA --at "2024-06-15 15:00" --fields stoch_k_15m,wt1_1h
"""
import argparse, json, logging, os, platform, sqlite3, sys, time
from datetime import datetime, timezone, timedelta
from multiprocessing import Pool, cpu_count
from pathlib import Path
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("v4_precompute_tradier")

IS_SERVER = platform.system() == "Linux"
if IS_SERVER:
    BASE = Path("/home/niels/binance-sandbox")
    KLINES_DIR = BASE / "klines_cache"
else:
    BASE = Path("/Users/niels/Documents/binance")
    KLINES_DIR = BASE / "klines_cache"
OUT_DIR = BASE / "backtest_v4_tradier"
INDICATORS_DIR = OUT_DIR / "indicators"
DB_PATH = OUT_DIR / "backtest.db"
START_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Stock-specific WT parameters — from tradier_indicators.py:421-427
# ---------------------------------------------------------------------------
WT_TF_PARAMS_STOCK = {
    "5m": {"esa": 8, "chan": 12, "sig": 15, "smooth": 3, "ci": 0.010},
    "15m": {"esa": 8, "chan": 14, "sig": 21, "smooth": 3, "ci": 0.010},
    "1h": {"esa": 10, "chan": 10, "sig": 21, "smooth": 3, "ci": 0.010},
    "4h": {"esa": 10, "chan": 14, "sig": 21, "smooth": 4, "ci": 0.010},
    "D": {"esa": 10, "chan": 18, "sig": 25, "smooth": 4, "ci": 0.010},
}
TF_CONFIG_STOCK = {
    "5m": {"dc": 20, "ema": [20, 50, 200], "sma": 200, "atr": 14},
    "15m": {"dc": 20, "ema": [20, 50, 200], "sma": 200, "atr": 14},
    "1h": {"dc": 20, "ema": [20, 50, 200], "sma": 200, "atr": 14},
    "4h": {"dc": 20, "ema": [20, 50, 200], "sma": 200, "atr": 14},
    "D": {"dc": 20, "ema": [20, 50, 200], "sma": 200, "atr": 14},
}
BACKTEST_TFS = ["5m", "15m", "1h", "4h", "D"]

# ---------------------------------------------------------------------------
# Import indicator functions from the crypto precompute (same math)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(BASE if IS_SERVER else Path("/Users/niels/Documents/binance")))
from backtest_v8_precompute import (
    setup_db as _setup_db_base, load_klines, resample_tf,
    compute_rsi, compute_stoch_rsi, compute_atr, compute_donchian,
    compute_heikin_ashi, compute_macd, compute_bb, compute_mfi,
    compute_adx, compute_choppiness, compute_rel_volume, compute_linreg_slope,
    compute_hull_trend, compute_crossovers, compute_wt_intelligence,
    map_htf_to_15m, inspect_symbol as _inspect_base,
)

# ---------------------------------------------------------------------------
# Database setup (separate DB for stocks)
# ---------------------------------------------------------------------------
def setup_db():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INDICATORS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS symbols (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS precompute_status (symbol TEXT PRIMARY KEY, n_bars INTEGER, start_ts INTEGER, end_ts INTEGER, n_indicators INTEGER, completed_at TEXT);
        CREATE TABLE IF NOT EXISTS indicator_columns (name TEXT PRIMARY KEY, timeframe TEXT, category TEXT);
        CREATE TABLE IF NOT EXISTS rankings (timestamp INTEGER PRIMARY KEY, long_top TEXT, short_top TEXT, regime TEXT, scores_json TEXT);
        CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, config_json TEXT, started_at TEXT, finished_at TEXT, sharpe REAL, total_pnl_pct REAL, max_drawdown_pct REAL, total_trades INTEGER, win_rate REAL, profit_factor REAL);
        CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, timestamp INTEGER, symbol TEXT, side TEXT, action TEXT, price REAL, quantity REAL, gain_pct REAL, reason TEXT, position_value REAL);
    """)
    conn.commit()
    return conn

# ---------------------------------------------------------------------------
# Stock-specific WT computation (uses EMA not DEMA)
# ---------------------------------------------------------------------------
def compute_wavetrend_stock(high, low, close, tf):
    p = WT_TF_PARAMS_STOCK.get(tf, WT_TF_PARAMS_STOCK["1h"])
    src = (high + low + close) / 3.0
    esa = src.ewm(span=p["esa"], adjust=False).mean()
    d_val = (src - esa).abs().ewm(span=p["chan"], adjust=False).mean()
    ci = (src - esa) / (p["ci"] * d_val + 1e-10)
    wt1 = ci.ewm(span=p["sig"], adjust=False).mean()
    wt2 = wt1.rolling(p["smooth"], min_periods=1).mean()
    return wt1.values.astype(np.float32), wt2.values.astype(np.float32)

# ---------------------------------------------------------------------------
# Fabricate 5m from 15m (for stocks without 5m data)
# ---------------------------------------------------------------------------
def fabricate_5m(df_15m):
    """Fabricate 5m bars from 15m: 3 sub-bars per 15m bar, last sub-bar timestamp = 15m timestamp.
    The key fix: the 3rd sub-bar's timestamp MUST match the 15m timestamp exactly so
    map_htf_to_15m (searchsorted) finds valid stoch values. Previously stoch was NaN because
    the fabricated 5m DataFrame only had ~12 bars in early portions and stoch_rsi(14,7,7)
    needs 34+ bars of history. With full fabrication (all 15m bars), we get 3×N bars which
    is always sufficient."""
    rows = []
    closes = df_15m["close"].values
    opens = df_15m["open"].values
    highs = df_15m["high"].values
    lows = df_15m["low"].values
    vols = df_15m["volume"].values
    timestamps = df_15m.index
    for i in range(len(df_15m)):
        ts = timestamps[i]
        o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
        prev_c = closes[i - 1] if i > 0 else o
        for j in range(3):
            frac = (j + 1) / 3.0
            prev_frac = j / 3.0
            sub_c = prev_c + (c - prev_c) * frac
            sub_o = prev_c + (c - prev_c) * prev_frac
            spread = max(h - l, 0.001)
            if j == 1:
                sub_h = max(sub_o, sub_c) + spread * 0.3
                sub_l = min(sub_o, sub_c) - spread * 0.3
            else:
                sub_h = max(sub_o, sub_c) + spread * 0.08
                sub_l = min(sub_o, sub_c) - spread * 0.08
            # j=0: -10min, j=1: -5min, j=2: 0min (exact 15m timestamp)
            sub_ts = ts - pd.Timedelta(minutes=10) + pd.Timedelta(minutes=5 * j)
            rows.append({"timestamp_dt": sub_ts, "open": sub_o, "high": sub_h, "low": sub_l, "close": sub_c, "volume": v / 3.0})
    df = pd.DataFrame(rows).set_index("timestamp_dt").sort_index()
    return df

# ---------------------------------------------------------------------------
# Full indicator computation for one TF (stock version)
# ---------------------------------------------------------------------------
def compute_tf_indicators_stock(df, tf):
    if df is None or len(df) < 30:
        return {}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    volume = df["volume"]
    cfg = TF_CONFIG_STOCK.get(tf, TF_CONFIG_STOCK["1h"])
    out = {}
    out[f"open_{tf}"] = open_.values.astype(np.float32)
    out[f"high_{tf}"] = high.values.astype(np.float32)
    out[f"low_{tf}"] = low.values.astype(np.float32)
    out[f"close_{tf}"] = close.values.astype(np.float32)
    out[f"volume_{tf}"] = volume.values.astype(np.float32)
    out[f"rsi_{tf}"] = compute_rsi(close, 14)
    if tf in ("1h", "4h"):
        out[f"rsi_2_{tf}"] = compute_rsi(close, 2)
    k, d = compute_stoch_rsi(close, 14, 7, 7)
    out[f"stoch_k_{tf}"] = k
    out[f"stoch_d_{tf}"] = d
    out[f"k_{tf}_prev"] = np.roll(k, 1); out[f"k_{tf}_prev"][0] = k[0]
    out[f"d_{tf}_prev"] = np.roll(d, 1); out[f"d_{tf}_prev"][0] = d[0]
    stoch_co, stoch_cu = compute_crossovers(k, d)
    out[f"stoch_crossover_{tf}"] = stoch_co
    out[f"stoch_crossunder_{tf}"] = stoch_cu
    atr = compute_atr(high, low, close, cfg["atr"])
    out[f"atr_{tf}"] = atr
    wt1, wt2 = compute_wavetrend_stock(high, low, close, tf)
    out[f"wt1_{tf}"] = wt1
    out[f"wt2_{tf}"] = wt2
    wt_intel = compute_wt_intelligence(wt1, wt2, close.values, tf)
    out.update(wt_intel)
    dc_h, dc_l, dc_b, dc_pos, dc_w = compute_donchian(high, low, close, cfg["dc"])
    out[f"dc_high_{tf}"] = dc_h
    out[f"dc_low_{tf}"] = dc_l
    out[f"dc_basis_{tf}"] = dc_b
    out[f"dc_position_{tf}"] = dc_pos
    out[f"dc_width_{tf}"] = dc_w
    out[f"dc_high_{tf}_prev"] = np.roll(dc_h, 1); out[f"dc_high_{tf}_prev"][0] = dc_h[0]
    out[f"dc_low_{tf}_prev"] = np.roll(dc_l, 1); out[f"dc_low_{tf}_prev"][0] = dc_l[0]
    out[f"dc_basis_{tf}_prev"] = np.roll(dc_b, 1); out[f"dc_basis_{tf}_prev"][0] = dc_b[0]
    ant_shift = min(22, len(dc_h) - 1)
    out[f"dc_high_{tf}_ant"] = np.roll(dc_h, ant_shift); out[f"dc_high_{tf}_ant"][:ant_shift] = dc_h[0]
    out[f"dc_low_{tf}_ant"] = np.roll(dc_l, ant_shift); out[f"dc_low_{tf}_ant"][:ant_shift] = dc_l[0]
    dc_b_co, dc_b_cu = compute_crossovers(close.values, dc_b)
    out[f"dc_basis_crossover_{tf}"] = dc_b_co
    out[f"dc_basis_crossunder_{tf}"] = dc_b_cu
    dc_h_co, dc_h_cu = compute_crossovers(close.values, dc_h)
    out[f"dc_high_crossover_{tf}"] = dc_h_co
    out[f"dc_high_crossunder_{tf}"] = dc_h_cu
    dc_l_co, dc_l_cu = compute_crossovers(close.values, dc_l)
    out[f"dc_low_crossover_{tf}"] = dc_l_co
    out[f"dc_low_crossunder_{tf}"] = dc_l_cu
    ha_color, ha_streak = compute_heikin_ashi(open_, high, low, close)
    out[f"ha_{tf}"] = ha_color
    out[f"ha_streak_{tf}"] = ha_streak
    sma = close.rolling(cfg["sma"], min_periods=1).mean().values.astype(np.float32)
    out[f"sma_200_{tf}"] = sma
    out[f"sma_200_{tf}_prev"] = np.roll(sma, 1); out[f"sma_200_{tf}_prev"][0] = sma[0]
    sma_co, sma_cu = compute_crossovers(close.values, sma)
    out[f"sma_crossover_{tf}"] = sma_co
    out[f"sma_crossunder_{tf}"] = sma_cu
    for ema_len in cfg["ema"]:
        ema = close.ewm(span=ema_len, adjust=False).mean().values.astype(np.float32)
        out[f"ema_{ema_len}_{tf}"] = ema
        out[f"ema_{ema_len}_{tf}_prev"] = np.roll(ema, 1); out[f"ema_{ema_len}_{tf}_prev"][0] = ema[0]
    if 20 in cfg["ema"]:
        ema20 = out[f"ema_20_{tf}"]
        out[f"ema_dist_{tf}"] = ((close.values - ema20) / (ema20 + 1e-10) * 100.0).astype(np.float32)
    out[f"relative_volume_{tf}"] = compute_rel_volume(volume)
    out[f"mfi_{tf}"] = compute_mfi(high, low, close, volume)
    if tf in ("1h", "4h", "D"):
        m, s, h = compute_macd(close)
        out[f"macd_{tf}"] = m
        out[f"macd_signal_{tf}"] = s
        out[f"macd_hist_{tf}"] = h
        macd_co, macd_cu = compute_crossovers(m, s)
        out[f"macd_crossover_{tf}"] = macd_co
        out[f"macd_crossunder_{tf}"] = macd_cu
        out[f"adx_{tf}"] = compute_adx(high, low, close)
        out[f"choppiness_{tf}"] = compute_choppiness(high, low, close)
        bb_u, bb_l, bb_pctb, bb_w = compute_bb(close)
        out[f"bb_upper_{tf}"] = bb_u
        out[f"bb_lower_{tf}"] = bb_l
        out[f"bb_pct_b_{tf}"] = bb_pctb
        out[f"bb_width_{tf}"] = bb_w
        if tf == "D":
            vwap_d = ((high.values + low.values + close.values) / 3.0).astype(np.float32)
            out["vwap_D"] = vwap_d
    out[f"lr_trend_{tf}"] = compute_linreg_slope(close, 50)
    if tf in ("5m", "15m"):
        t_up, tco, tcu = compute_hull_trend(close)
        out[f"t_up_{tf}"] = t_up
        out[f"tco_{tf}"] = tco
        out[f"tcu_{tf}"] = tcu
    high_prev = np.roll(high.values, 1).astype(np.float32); high_prev[0] = high.values[0]
    low_prev = np.roll(low.values, 1).astype(np.float32); low_prev[0] = low.values[0]
    out[f"high_{tf}_prev"] = high_prev
    out[f"low_{tf}_prev"] = low_prev
    return out

# ---------------------------------------------------------------------------
# WT Composite for stocks
# ---------------------------------------------------------------------------
def compute_wt_composite_stock(all_tf_arrays, timestamps, tf_list=None):
    tfs = tf_list or ["5m", "15m", "1h", "4h", "D"]
    n = len(timestamps)
    comp_long = np.zeros(n, dtype=np.float32)
    comp_short = np.zeros(n, dtype=np.float32)
    bull_align = np.zeros(n, dtype=np.int8)
    bear_align = np.zeros(n, dtype=np.int8)
    n_tfs = len(tfs)
    mom_weights = {"D": 5, "4h": 4, "1h": 3, "15m": 2, "5m": 1}
    for i in range(n):
        long_s, short_s = 0.0, 0.0
        b_cnt, br_cnt = 0, 0
        for tf in tfs:
            w1_key, w2_key = f"wt1_{tf}", f"wt2_{tf}"
            if w1_key not in all_tf_arrays or w2_key not in all_tf_arrays:
                continue
            w1 = float(all_tf_arrays[w1_key][min(i, len(all_tf_arrays[w1_key]) - 1)])
            w2 = float(all_tf_arrays[w2_key][min(i, len(all_tf_arrays[w2_key]) - 1)])
            if w1 > w2:
                b_cnt += 1
            else:
                br_cnt += 1
            vel = float(all_tf_arrays.get(f"wt_velocity_{tf}", np.zeros(1))[min(i, len(all_tf_arrays.get(f"wt_velocity_{tf}", np.zeros(1))) - 1)])
            pct = float(all_tf_arrays.get(f"wt_percentile_{tf}", np.full(1, 50))[min(i, len(all_tf_arrays.get(f"wt_percentile_{tf}", np.full(1, 50))) - 1)])
            mom = int(all_tf_arrays.get(f"wt_momentum_state_{tf}", np.zeros(1, dtype=np.int8))[min(i, len(all_tf_arrays.get(f"wt_momentum_state_{tf}", np.zeros(1))) - 1)])
            wt = mom_weights.get(tf, 1)
            if vel > 0:
                long_s += 2.0
            else:
                short_s += 2.0
            if pct < 20:
                long_s += 5.0
            elif pct > 80:
                short_s += 5.0
            if mom == 1:
                long_s += wt * 0.7
            elif mom == 2:
                long_s += wt * 0.3
            elif mom == -1:
                short_s += wt * 0.7
            elif mom == -2:
                short_s += wt * 0.3
        align_score = (b_cnt - n_tfs / 2.0) * 10.0
        long_s += max(-25, min(25, align_score))
        short_s += max(-25, min(25, -align_score))
        comp_long[i] = max(-100, min(100, long_s))
        comp_short[i] = max(-100, min(100, short_s))
        bull_align[i] = b_cnt
        bear_align[i] = br_cnt
    return {"wt_composite_long": comp_long, "wt_composite_short": comp_short, "wt_composite_delta": (comp_long - comp_short).astype(np.float32), "wt_bull_alignment": bull_align, "wt_bear_alignment": bear_align}

# ---------------------------------------------------------------------------
# Process one stock symbol
# ---------------------------------------------------------------------------
def process_symbol(args):
    symbol, klines_dir, out_dir, start_ts, resume = args
    npz_path = Path(out_dir) / "indicators" / f"{symbol}.npz"
    if resume and npz_path.exists():
        log.info(f"[SKIP] {symbol} — already computed")
        return symbol, 0, "skipped"
    t0 = time.time()
    df_15m = load_klines(symbol, "15m", klines_dir)
    df_1h = load_klines(symbol, "1h", klines_dir)
    df_4h = load_klines(symbol, "4h", klines_dir)
    df_D = load_klines(symbol, "D", klines_dir)
    use_1h_as_base = (df_15m is None or len(df_15m) < 100) or (df_1h is not None and len(df_1h) >= 200 and (df_15m is None or len(df_1h) > len(df_15m) * 2))
    if use_1h_as_base and df_1h is not None and len(df_1h) >= 200:
        log.info(f"  {symbol}: 15m short ({0 if df_15m is None else len(df_15m)}), deriving from 1h ({len(df_1h)} bars)")
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        df_15m_from_1h = pd.DataFrame()
        rows = []
        for idx_ts, row in df_1h.iterrows():
            for j in range(4):
                frac = (j + 1) / 4.0
                prev_frac = j / 4.0
                sub_c = row["open"] + (row["close"] - row["open"]) * frac
                sub_o = row["open"] + (row["close"] - row["open"]) * prev_frac
                spread = row["high"] - row["low"]
                sub_h = max(sub_o, sub_c) + spread * (0.25 if j in (1, 2) else 0.05)
                sub_l = min(sub_o, sub_c) - spread * (0.25 if j in (1, 2) else 0.05)
                sub_ts = idx_ts - pd.Timedelta(minutes=45) + pd.Timedelta(minutes=15 * j)
                rows.append({"timestamp_dt": sub_ts, "open": sub_o, "high": sub_h, "low": sub_l, "close": sub_c, "volume": row["volume"] / 4.0})
        df_15m = pd.DataFrame(rows).set_index("timestamp_dt").sort_index()
    if df_15m is None or len(df_15m) < 100:
        log.info(f"[SKIP] {symbol} — insufficient data ({0 if df_15m is None else len(df_15m)} bars)")
        return symbol, 0, "no_data"
    df_15m = df_15m[df_15m.index >= start_ts - timedelta(days=400)]
    df_5m_raw = load_klines(symbol, "5m", klines_dir)
    # CRITICAL FIX: if real 5m covers less than 50% of 15m period, fabricate from 15m
    # Bug: real 5m is only 3 months (Dec 2025+) while 15m is 35 months → 91% of stoch_k_5m
    # becomes NaN because map_htf_to_15m maps pre-5m bars to first (warmup) 5m bar
    if df_5m_raw is not None and len(df_5m_raw) >= 50:
        coverage_5m = len(df_5m_raw) / max(1, len(df_15m) * 3)  # 3 5m bars per 15m
        if coverage_5m >= 0.5:
            df_5m = df_5m_raw[df_5m_raw.index >= start_ts - timedelta(days=400)]
            log.info(f"  {symbol}: using real 5m data ({len(df_5m)} bars, {coverage_5m*100:.0f}% coverage)")
        else:
            df_5m = fabricate_5m(df_15m)
            log.info(f"  {symbol}: fabricating 5m (real 5m only {coverage_5m*100:.0f}% coverage, {len(df_5m_raw)} bars vs {len(df_15m)*3} needed)")
    else:
        df_5m = fabricate_5m(df_15m)
    if df_1h is None or len(df_1h) < 50:
        df_1h = resample_tf(df_15m, "1h")
    if df_4h is None or len(df_4h) < 50:
        df_4h = resample_tf(df_15m, "4h")
    if df_D is None or len(df_D) < 50:
        df_D = resample_tf(df_15m, "D")
    for df in [df_5m, df_1h, df_4h, df_D]:
        if df is not None:
            mask = df.index >= start_ts - timedelta(days=200)
            df = df[mask] if mask.any() else df
    log.info(f"[COMPUTE] {symbol}: 15m={len(df_15m)}, 5m={len(df_5m) if df_5m is not None else 0}, 1h={len(df_1h) if df_1h is not None else 0}, 4h={len(df_4h) if df_4h is not None else 0}, D={len(df_D) if df_D is not None else 0}")
    ind_5m = compute_tf_indicators_stock(df_5m, "5m")
    ind_15m = compute_tf_indicators_stock(df_15m, "15m")
    ind_1h = compute_tf_indicators_stock(df_1h, "1h")
    ind_4h = compute_tf_indicators_stock(df_4h, "4h")
    ind_D = compute_tf_indicators_stock(df_D, "D")
    ts_15m = df_15m.index.values
    ts_15m_epoch = (ts_15m - np.datetime64("1970-01-01T00:00:00")) // np.timedelta64(1, "s")
    merged = {"timestamps": ts_15m_epoch.astype(np.int64), "close": df_15m["close"].values.astype(np.float32)}
    for key, arr in ind_15m.items():
        if len(arr) == len(ts_15m):
            merged[key] = arr
    for ind_data, df_source in [(ind_5m, df_5m), (ind_1h, df_1h), (ind_4h, df_4h), (ind_D, df_D)]:
        if ind_data and df_source is not None:
            ts_src = df_source.index.values
            ts_src_epoch = (ts_src - np.datetime64("1970-01-01T00:00:00")) // np.timedelta64(1, "s")
            mapped = map_htf_to_15m(ind_data, ts_src_epoch, ts_15m_epoch)
            for key, arr in mapped.items():
                merged[key] = arr
    comp = compute_wt_composite_stock(merged, ts_15m_epoch)
    merged.update(comp)
    np.savez_compressed(str(npz_path), **merged)
    elapsed = time.time() - t0
    n_ind = len([k for k in merged if k not in ("timestamps", "close")])
    start_epoch = int(start_ts.timestamp())
    n_bars = int((ts_15m_epoch >= start_epoch).sum())
    log.info(f"  {symbol}: {n_bars} bars, {n_ind} indicators, {elapsed:.1f}s -> {npz_path.name}")
    return symbol, n_bars, "ok"


def _inject_market_sentiment(indicators_dir, symbols):
    """Post-pass: compute cross-symbol WT breadth per bar, inject market_sentiment_score
    into every NPZ. score = 50 + (bull_count - bear_count) / total * 50, range [0, 100]."""
    from collections import defaultdict
    indicators_dir = Path(indicators_dir)
    sym_data = {}
    for sym in symbols:
        p = indicators_dir / f"{sym}.npz"
        if not p.exists():
            continue
        try:
            z = dict(np.load(str(p), allow_pickle=True))
            ts = z.get('timestamps')
            bias = z.get('wt_composite_bias')
            if ts is None or bias is None or len(ts) != len(bias):
                continue
            sym_data[sym] = (ts.astype(np.int64), bias.astype(np.int8), z)
        except Exception as e:
            log.warning(f"[SENTIMENT_INJECT] load failed {sym}: {e}")
    if not sym_data:
        log.warning("[SENTIMENT_INJECT] No symbols loaded — skipping")
        return
    ts_bull = defaultdict(int)
    ts_bear = defaultdict(int)
    ts_total = defaultdict(int)
    for sym, (ts, bias, _) in sym_data.items():
        for t, b in zip(ts.tolist(), bias.tolist()):
            ts_total[t] += 1
            if b == 1:
                ts_bull[t] += 1
            elif b == -1:
                ts_bear[t] += 1
    ts_score = {t: float(50.0 + (ts_bull[t] - ts_bear[t]) / ts_total[t] * 50.0) for t in ts_total}
    updated = 0
    for sym, (ts, _, z) in sym_data.items():
        p = indicators_dir / f"{sym}.npz"
        mss = np.array([ts_score.get(int(t), 50.0) for t in ts], dtype=np.float32)
        z['market_sentiment_score'] = mss
        np.savez_compressed(str(p), **z)
        updated += 1
    log.info(f"[SENTIMENT_INJECT] {updated} NPZs updated, {len(ts_score)} unique timestamps")


# ---------------------------------------------------------------------------
# Get stock symbol list
# ---------------------------------------------------------------------------
def get_stock_symbols(klines_dir):
    """Find all stock symbols (non-USDT/USDC) with 15m kline data."""
    symbols = []
    for f in sorted(Path(klines_dir).glob("*_15m.json")):
        sym = f.stem.replace("_15m", "")
        if "USDT" not in sym and "USDC" not in sym and "BUSD" not in sym:
            if f.stat().st_size > 100:
                symbols.append(sym)
    return sorted(symbols)

# ---------------------------------------------------------------------------
# Rankings for stocks
# ---------------------------------------------------------------------------
def compute_rankings_for_db(symbols, out_dir, conn):
    log.info("Computing stock rankings from precomputed indicators...")
    symbol_data = {}
    for sym in symbols:
        npz_path = Path(out_dir) / "indicators" / f"{sym}.npz"
        if not npz_path.exists():
            continue
        symbol_data[sym] = dict(np.load(str(npz_path), allow_pickle=True))
    if not symbol_data:
        return
    ref_sym = next(iter(symbol_data))
    timestamps = symbol_data[ref_sym]["timestamps"]
    hour_mask = (timestamps % 3600) == 0
    hour_indices = np.where(hour_mask)[0]
    log.info(f"  Computing rankings at {len(hour_indices)} hourly boundaries for {len(symbol_data)} stocks")
    batch = []
    for idx in hour_indices:
        ts = int(timestamps[idx])
        scores_long, scores_short = {}, {}
        for sym, data in symbol_data.items():
            if idx >= len(data.get("timestamps", [])):
                continue
            sl, ss = 0.0, 0.0
            for tf in ["15m", "1h", "4h", "D"]:
                wt_key = f"wt_score_{tf}"
                if wt_key in data and idx < len(data[wt_key]):
                    wts = float(data[wt_key][idx])
                    sl += max(0, wts) * {"15m": 2, "1h": 3, "4h": 4, "D": 5}.get(tf, 1)
                    ss += max(0, -wts) * {"15m": 2, "1h": 3, "4h": 4, "D": 5}.get(tf, 1)
                k_key = f"stoch_k_{tf}"
                if k_key in data and idx < len(data[k_key]):
                    kv = float(data[k_key][idx])
                    if kv < 25:
                        sl += (25 - kv) * 0.3
                    if kv > 75:
                        ss += (kv - 75) * 0.3
            scores_long[sym] = round(sl, 2)
            scores_short[sym] = round(ss, 2)
        top_long = sorted(scores_long.items(), key=lambda x: -x[1])[:20]
        top_short = sorted(scores_short.items(), key=lambda x: -x[1])[:20]
        batch.append((ts, json.dumps([s for s, _ in top_long]), json.dumps([s for s, _ in top_short]), "NORMAL", json.dumps({**{f"L_{s}": v for s, v in top_long}, **{f"S_{s}": v for s, v in top_short}})))
        if len(batch) >= 500:
            conn.executemany("INSERT OR REPLACE INTO rankings VALUES (?,?,?,?,?)", batch)
            conn.commit()
            batch.clear()
    if batch:
        conn.executemany("INSERT OR REPLACE INTO rankings VALUES (?,?,?,?,?)", batch)
        conn.commit()
    log.info(f"  Rankings: {len(hour_indices)} hourly snapshots stored")

# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
def inspect_symbol(symbol, at_time=None, fields=None):
    npz_path = INDICATORS_DIR / f"{symbol}.npz"
    if not npz_path.exists():
        print(f"No data for {symbol}. Run precompute first.")
        return
    _inspect_base(symbol)
    if at_time:
        data = dict(np.load(str(npz_path), allow_pickle=True))
        timestamps = data["timestamps"]
        dt = pd.Timestamp(at_time, tz="UTC")
        target_ts = int(dt.timestamp())
        idx = np.searchsorted(timestamps, target_ts)
        idx = min(idx, len(timestamps) - 1)
        actual_dt = datetime.fromtimestamp(timestamps[idx], tz=timezone.utc)
        print(f"\n{symbol} @ {actual_dt.isoformat()} (bar {idx}):")
        show_fields = fields.split(",") if fields else sorted(k for k in data.keys() if k != "timestamps")
        for k in sorted(show_fields):
            k = k.strip()
            if k in data and idx < len(data[k]):
                val = data[k][idx]
                if isinstance(val, (np.floating, float)):
                    print(f"  {k:40s} = {val:>12.4f}")
                else:
                    print(f"  {k:40s} = {val}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Backtest V4 — Tradier (Stock) Indicator Precompute")
    parser.add_argument("--symbols", type=int, default=0, help="Number of symbols (0=all)")
    parser.add_argument("--workers", type=int, default=0, help="Parallel workers (0=auto)")
    parser.add_argument("--start", type=str, default="2022-01-01", help="Start date")
    parser.add_argument("--resume", action="store_true", help="Skip completed symbols")
    parser.add_argument("--klines-dir", type=str, default=None, help="Override klines directory")
    parser.add_argument("--inspect", type=str, default=None, metavar="SYMBOL")
    parser.add_argument("--at", type=str, default=None)
    parser.add_argument("--fields", type=str, default=None)
    parser.add_argument("--rankings", action="store_true")
    args = parser.parse_args()
    global INDICATORS_DIR
    if args.inspect:
        inspect_symbol(args.inspect, args.at, args.fields)
        return
    start_ts = pd.Timestamp(args.start, tz="UTC").to_pydatetime()
    klines_dir = Path(args.klines_dir) if args.klines_dir else KLINES_DIR
    symbols = get_stock_symbols(klines_dir)
    if args.symbols > 0:
        symbols = symbols[:args.symbols]
    log.info(f"Backtest V4 Tradier Precompute: {len(symbols)} stock symbols, start={args.start}")
    conn = setup_db()
    for i, sym in enumerate(symbols):
        conn.execute("INSERT OR IGNORE INTO symbols VALUES (?,?)", (i, sym))
    conn.commit()
    n_workers = args.workers if args.workers > 0 else min(cpu_count(), len(symbols), 16)
    tasks = [(sym, str(klines_dir), str(OUT_DIR), start_ts, args.resume) for sym in symbols]
    t_start = time.time()
    if n_workers <= 1:
        results = [process_symbol(t) for t in tasks]
    else:
        log.info(f"Using {n_workers} parallel workers")
        with Pool(n_workers) as pool:
            results = pool.map(process_symbol, tasks)
    ok = sum(1 for _, _, s in results if s == "ok")
    skip = sum(1 for _, _, s in results if s == "skipped")
    fail = sum(1 for _, _, s in results if s not in ("ok", "skipped"))
    total_bars = sum(n for _, n, s in results if s == "ok")
    elapsed = time.time() - t_start
    log.info(f"\nPrecompute complete: {ok} stocks OK, {skip} skipped, {fail} failed")
    log.info(f"Total bars: {total_bars:,}, Time: {elapsed:.0f}s ({elapsed/60:.1f}m)")
    for sym, n_bars, status in results:
        if status == "ok":
            conn.execute("INSERT OR REPLACE INTO precompute_status VALUES (?,?,?,?,?,?)", (sym, n_bars, int(start_ts.timestamp()), int(time.time()), 0, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    if args.rankings or ok > 0:
        ok_syms = [sym for sym, _, s in results if s in ("ok", "skipped")]
        compute_rankings_for_db(ok_syms, str(OUT_DIR), conn)
    conn.close()
    log.info(f"\nOutput: {OUT_DIR}")
    log.info(f"  Database: {DB_PATH}")
    log.info(f"  Indicators: {INDICATORS_DIR}/ ({ok} NPZ files)")

if __name__ == "__main__":
    main()
