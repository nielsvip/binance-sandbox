#!/usr/bin/env python3
"""
ABLATION BACKTEST v3 — Proper deep-history test on binance-sandbox klines
=========================================================================
Uses 1h as PRIMARY simulation TF (8 months of data, ~3000 bars per symbol).
Uses 4h and D as HTF filters (2+ years).
Tests each BACKTEST_CHANGE via drop-one-out from ALL_CHANGES config.

Output: Markdown report + SQLite DB.
Usage: python3 ablation_backtest_v3.py [--workers 14]
"""
import json, os, sys, time, math, sqlite3, argparse
from pathlib import Path
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np

BASE = Path("/home/niels/binance-sandbox")
KLINES_DIR = BASE / "klines_cache"
RESULTS_DIR = BASE / "backtest_framework" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "ablation_v3.db"
REPORT_PATH = RESULTS_DIR / "ABLATION_V3_REPORT.md"

FEE = 0.0008
WARMUP = 200
MIN_TRADES = 10

# Simulation TFs: primary=1h, HTF=4h,D
PRIMARY_TF = "15m"
HTF_LIST = ["1h", "4h"]

# ═══════════════════════════════════════════════════════════════════════
# BACKTEST_CHANGE definitions (baseline=March 4 vs current)
# Only include params that affect entry/exit logic on 1h+ timeframes
# ═══════════════════════════════════════════════════════════════════════
CHANGES = [
    # --- Entry Filters ---
    ("BC_103", "ENTRY_ATR_PCT_MIN", 0.0, 1.5, "Min ATR%: none→1.5%"),
    ("BC_100", "ENTRY_VOL_MIN_RATIO", 1.0, 1.3, "Min volume ratio: 1.0→1.3"),
    ("BC_6", "BB_ENTRY_LONG_THRESHOLD", -999.0, -0.2, "BB %B entry threshold"),
    ("BC_7", "SMA200_DIST_LONG_THRESHOLD", -2.0, -3.0, "SMA200 dist: -2→-3%"),
    ("BC_111_RSI", "RSI_ENTRY_GATE_ENABLED", False, True, "RSI<37 entry gate"),
    ("BC_111_RSI_TH", "RSI_ENTRY_MAX_LONG", 100.0, 37.0, "RSI entry max: 100→37"),
    ("BC_102", "LONG_STOCH_CHASE_BLOCK", False, True, "Block chasing overbought longs"),
    ("BC_101_RSI", "SHORT_RSI_MIN_1H", 0, 40, "Short RSI gate: none→40"),
    ("BC_31", "HA_ENTRY_WEIGHT", 0.0, -0.5, "HA contrarian weight: 0→-0.5"),
    ("BC_9", "K_FLOOR", 0, 30, "K floor for shorts: none→30"),
    ("BC_109", "K_ZONE_ENTRY_ENABLED", False, True, "K-zone entry (no crossover wait)"),
    ("BC_101_KZ", "K_ZONE_LONG_THRESHOLD", 35, 90, "K-zone long: 35→90"),
    ("BC_101_KS", "K_ZONE_SHORT_THRESHOLD", 65, 10, "K-zone short: 65→10"),
    ("BC_113b", "MOMENTUM_FADE_ENABLED", False, True, "Momentum fade entry"),
    # --- Exit/Hold Logic ---
    ("BC_17", "STOP_LOSS_THRESHOLD", 0.2, 999.0, "Stop loss: 0.2%→disabled"),
    ("BC_113", "STOP_MAJOR_LOSS_BLOCK_ENABLED", False, True, "Block STOP_MAJOR_LOSS"),
    ("BC_114", "IMMEDIATE_WRONG_WAY_ENABLED", True, False, "Wrong-way exit→disabled"),
    ("BC_101_TP", "ACCOUNT_TP_PCT", 0.02, 0.005, "TP: 2%→0.5%"),
    ("BC_112", "EXIT_GAIN_THRESHOLD_MIN", 0.15, 1.0, "Gain threshold: 0.15→1.0"),
    ("BC_105_TRAIL", "WIN_TRAIL_EROSION_PCT", 1.0, 0.30, "Trail erosion: 100%→30%"),
    ("BC_15", "OPTIMAL_HOLD_BARS", 999, 21, "Max hold: unlimited→21 bars"),
    # --- HTF Confirmation ---
    ("BC_35", "HTF_CONF", True, False, "HTF confirmation: ON→OFF"),
    ("BC_32", "ALIGNMENT_MIN", 1, 3, "Alignment min: 1→3"),
    # --- Hedge/Loss ---
    ("BC_115", "FAST_RISER_DOUBLE_ENABLED", True, False, "Fast riser double→disabled"),
    ("BC_116", "AUGMENT_PYRAMID_ENABLED", True, False, "Pyramid augment→disabled"),
]

# ═══════════════════════════════════════════════════════════════════════
# SYMBOLS
# ═══════════════════════════════════════════════════════════════════════
def get_symbols():
    syms = json.load(open(BASE / "symbols.json"))
    return syms

# ═══════════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════════
def stoch_kd(h, l, c, k_period=14, smooth=3):
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hi = np.max(h[i - k_period + 1:i + 1])
        li = np.min(l[i - k_period + 1:i + 1])
        raw_k[i] = (c[i] - li) / (hi - li) * 100.0 if hi > li else 50.0
    k = np.full(n, 50.0)
    for i in range(smooth - 1, n):
        k[i] = raw_k[i - smooth + 1:i + 1].mean()
    d = np.full(n, 50.0)
    for i in range(smooth - 1, n):
        d[i] = k[i - smooth + 1:i + 1].mean()
    return k, d

def rsi_calc(c, period=14):
    n = len(c)
    out = np.full(n, 50.0)
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = np.zeros(n)
    avg_l = np.zeros(n)
    if period < n:
        avg_g[period] = gain[1:period + 1].mean()
        avg_l[period] = loss[1:period + 1].mean()
        for i in range(period + 1, n):
            avg_g[i] = (avg_g[i - 1] * (period - 1) + gain[i]) / period
            avg_l[i] = (avg_l[i - 1] * (period - 1) + loss[i]) / period
        for i in range(period, n):
            if avg_l[i] > 1e-10:
                out[i] = 100.0 - 100.0 / (1.0 + avg_g[i] / avg_l[i])
            elif avg_g[i] > 0:
                out[i] = 100.0
    return out

def ema(c, period):
    n = len(c)
    out = np.zeros(n)
    if n == 0:
        return out
    out[0] = c[0]
    k = 2.0 / (period + 1)
    for i in range(1, n):
        out[i] = c[i] * k + out[i - 1] * (1 - k)
    return out

def sma(c, period):
    n = len(c)
    out = np.zeros(n)
    cs = np.cumsum(c)
    out[period - 1:] = (cs[period - 1:] - np.concatenate([[0], cs[:-period]])) / period
    return out

def atr_calc(h, l, c, period=14):
    n = len(c)
    tr = np.empty(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = np.zeros(n)
    if period < n:
        a[period] = tr[:period + 1].mean()
        for i in range(period + 1, n):
            a[i] = (a[i - 1] * (period - 1) + tr[i]) / period
    return a

def bb_pctb(c, period=20, std_mult=2.0):
    n = len(c)
    out = np.full(n, 0.5)
    s = sma(c, period)
    for i in range(period - 1, n):
        std = np.std(c[i - period + 1:i + 1])
        if std > 1e-10:
            upper = s[i] + std_mult * std
            lower = s[i] - std_mult * std
            out[i] = (c[i] - lower) / (upper - lower) if upper > lower else 0.5
    return out

def heikin_ashi(o, h, l, c):
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.empty(len(c))
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, len(c)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    return ha_o, ha_c

def make_idx_map(n_primary, n_tf):
    if n_tf <= 0:
        return np.zeros(n_primary, dtype=np.int32)
    ratio = n_tf / n_primary
    idx = (np.arange(n_primary) * ratio - 1).astype(np.int32)
    np.clip(idx, 0, n_tf - 1, out=idx)
    return idx

# ═══════════════════════════════════════════════════════════════════════
# LOAD + PRECOMPUTE
# ═══════════════════════════════════════════════════════════════════════
def load_tf(sym, tf):
    p = KLINES_DIR / (sym + "_" + tf + ".json")
    if not p.exists():
        return None
    try:
        with open(p) as f:
            raw = json.load(f)
    except:
        return None
    if len(raw) < 50:
        return None
    o = np.array([float(x["open"]) for x in raw], dtype=np.float64)
    h = np.array([float(x["high"]) for x in raw], dtype=np.float64)
    l = np.array([float(x["low"]) for x in raw], dtype=np.float64)
    c = np.array([float(x["close"]) for x in raw], dtype=np.float64)
    v = np.array([float(x["volume"]) for x in raw], dtype=np.float64)
    return {"o": o, "h": h, "l": l, "c": c, "v": v}

def precompute_symbol(sym):
    tfs_needed = [PRIMARY_TF] + HTF_LIST
    data = {}
    for tf in tfs_needed:
        d = load_tf(sym, tf)
        if d is not None:
            data[tf] = d
    if PRIMARY_TF not in data:
        return None
    n = len(data[PRIMARY_TF]["c"])
    if n < WARMUP + 50:
        return None
    result = {"data": data, "indicators": {}, "n": n}
    for tf in tfs_needed:
        if tf not in data:
            continue
        d = data[tf]
        k, dd = stoch_kd(d["h"], d["l"], d["c"])
        r = rsi_calc(d["c"], 14)
        ha_o, ha_c = heikin_ashi(d["o"], d["h"], d["l"], d["c"])
        atr_v = atr_calc(d["h"], d["l"], d["c"], 14)
        ema20 = ema(d["c"], 20)
        sma200 = sma(d["c"], 200)
        bb = bb_pctb(d["c"], 20)
        vol_sma = sma(d["v"], 20)
        result["indicators"][tf] = {"k": k, "d": dd, "rsi": r, "ha_o": ha_o, "ha_c": ha_c, "atr": atr_v, "ema20": ema20, "sma200": sma200, "bb": bb, "vol_sma": vol_sma}
    return result

# ═══════════════════════════════════════════════════════════════════════
# SIMULATE
# ═══════════════════════════════════════════════════════════════════════
def simulate_config(sym_data, params):
    d = sym_data["data"][PRIMARY_TF]
    ind = sym_data["indicators"][PRIMARY_TF]
    c, h, l, o, v = d["c"], d["h"], d["l"], d["o"], d["v"]
    n = len(c)
    k, dd = ind["k"], ind["d"]
    r = ind["rsi"]
    ha_o, ha_c = ind["ha_o"], ind["ha_c"]
    atr_v = ind["atr"]
    ema20 = ind["ema20"]
    sma200 = ind["sma200"]
    bb = ind["bb"]
    vol_sma = ind["vol_sma"]
    # HTF data mapped to primary
    htf_mapped = {}
    for htf in HTF_LIST:
        if htf in sym_data["indicators"]:
            htf_n = len(sym_data["data"][htf]["c"])
            idx_map = make_idx_map(n, htf_n)
            htf_mapped[htf] = {key: val[idx_map] for key, val in sym_data["indicators"][htf].items()}
    # === ENTRY SIGNALS ===
    k_prev = np.roll(k, 1); k_prev[0] = k[0]
    d_prev = np.roll(dd, 1); d_prev[0] = dd[0]
    xover_long = (k > dd) & (k_prev <= d_prev)
    xover_short = (k < dd) & (k_prev >= d_prev)
    xover_long[:WARMUP] = False
    xover_short[:WARMUP] = False
    # K-zone entry
    if params.get("K_ZONE_ENTRY_ENABLED", False):
        kz_long = params.get("K_ZONE_LONG_THRESHOLD", 35)
        kz_short = params.get("K_ZONE_SHORT_THRESHOLD", 65)
        k_turn_up = (k > k_prev) & (k < kz_long)
        k_turn_down = (k < k_prev) & (k > kz_short)
        xover_long = xover_long | k_turn_up
        xover_short = xover_short | k_turn_down
    # RSI gate
    if params.get("RSI_ENTRY_GATE_ENABLED", False):
        rsi_max = params.get("RSI_ENTRY_MAX_LONG", 37.0)
        rsi_min_short = 100.0 - rsi_max
        xover_long = xover_long & (r < rsi_max)
        xover_short = xover_short & (r > rsi_min_short)
    # Volume filter
    vol_min = params.get("ENTRY_VOL_MIN_RATIO", 1.0)
    if vol_min > 1.0:
        vol_ok = np.zeros(n, dtype=bool)
        for i in range(20, n):
            if vol_sma[i] > 0:
                vol_ok[i] = v[i] / vol_sma[i] >= vol_min
        xover_long = xover_long & vol_ok
        xover_short = xover_short & vol_ok
    # ATR% filter
    atr_min = params.get("ENTRY_ATR_PCT_MIN", 0.0)
    if atr_min > 0:
        atr_pct = np.zeros(n)
        for i in range(14, n):
            if c[i] > 0:
                atr_pct[i] = atr_v[i] / c[i] * 100.0
        xover_long = xover_long & (atr_pct >= atr_min)
        xover_short = xover_short & (atr_pct >= atr_min)
    # BB filter
    bb_th = params.get("BB_ENTRY_LONG_THRESHOLD", -999.0)
    if bb_th > -100:
        xover_long = xover_long & (bb < (0.5 + bb_th))
        xover_short = xover_short & (bb > (0.5 - bb_th))
    # SMA200 dist
    sma200_th = params.get("SMA200_DIST_LONG_THRESHOLD", -2.0)
    if sma200_th > -100:
        sma200_dist = np.zeros(n)
        for i in range(200, n):
            if sma200[i] > 0:
                sma200_dist[i] = (c[i] - sma200[i]) / sma200[i] * 100.0
        xover_long = xover_long & (sma200_dist >= sma200_th)
    # Short RSI gate
    short_rsi_min = params.get("SHORT_RSI_MIN_1H", 0)
    if short_rsi_min > 0:
        xover_short = xover_short & (r >= short_rsi_min)
    # Long stoch chase block
    if params.get("LONG_STOCH_CHASE_BLOCK", False):
        ha_streak = np.zeros(n)
        for i in range(1, n):
            if ha_c[i] > ha_o[i]:
                ha_streak[i] = ha_streak[i - 1] + 1
        xover_long = xover_long & ~((k > 70) & (ha_streak > 2))
    # K floor for shorts
    k_floor = params.get("K_FLOOR", 0)
    if k_floor > 0:
        xover_short = xover_short & (k > k_floor)
    # HTF confirmation
    if params.get("HTF_CONF", True):
        for htf in HTF_LIST:
            if htf in htf_mapped:
                htf_k = htf_mapped[htf]["k"]
                htf_d = htf_mapped[htf]["d"]
                xover_long = xover_long & (htf_k > htf_d)
                xover_short = xover_short & (htf_k < htf_d)
    # Alignment filter
    align_min = params.get("ALIGNMENT_MIN", 1)
    if align_min > 1:
        aligned_long = np.ones(n, dtype=np.int32)
        aligned_short = np.ones(n, dtype=np.int32)
        for htf in HTF_LIST:
            if htf in htf_mapped:
                aligned_long += (htf_mapped[htf]["k"] > htf_mapped[htf]["d"]).astype(np.int32)
                aligned_short += (htf_mapped[htf]["k"] < htf_mapped[htf]["d"]).astype(np.int32)
        xover_long = xover_long & (aligned_long >= align_min)
        xover_short = xover_short & (aligned_short >= align_min)
    # HA contrarian
    ha_weight = params.get("HA_ENTRY_WEIGHT", 0.0)
    if ha_weight < 0:
        ha_green = ha_c > ha_o
        xover_long = xover_long & ~ha_green
        xover_short = xover_short & ha_green
    # Momentum fade
    if params.get("MOMENTUM_FADE_ENABLED", False):
        for i in range(20, n):
            if atr_v[i] > 0 and vol_sma[i] > 0:
                body = abs(c[i] - o[i])
                if body >= 2.0 * atr_v[i] and v[i] >= 2.0 * vol_sma[i]:
                    if c[i] > o[i] and k[i] > 60:
                        xover_short[i] = True
                    elif c[i] < o[i] and k[i] < 40:
                        xover_long[i] = True
    # === SIMULATE TRADES ===
    tp_pct = params.get("ACCOUNT_TP_PCT", 0.02)
    noloss = 0.005
    stop_loss_th = params.get("STOP_LOSS_THRESHOLD", 0.2) / 100.0
    gain_th_low = params.get("EXIT_GAIN_THRESHOLD_MIN", 0.15) / 100.0
    max_hold = params.get("OPTIMAL_HOLD_BARS", 999)
    trail_erosion = params.get("WIN_TRAIL_EROSION_PCT", 1.0)
    stop_major_block = params.get("STOP_MAJOR_LOSS_BLOCK_ENABLED", False)
    wrong_way = params.get("IMMEDIATE_WRONG_WAY_ENABLED", True)
    long_entries = np.nonzero(xover_long)[0]
    short_entries = np.nonzero(xover_short)[0]
    trades = []
    def run_trades(entries, is_long):
        last_exit = -1
        for ei in entries:
            if ei <= last_exit or ei >= n - 2:
                continue
            ep = c[ei]
            if ep <= 0:
                continue
            max_gain_pct = 0.0
            for bi in range(ei + 1, min(ei + max_hold + 1, n)):
                if is_long:
                    pnl_pct = (c[bi] - ep) / ep
                    high_pnl = (h[bi] - ep) / ep
                else:
                    pnl_pct = (ep - c[bi]) / ep
                    high_pnl = (ep - l[bi]) / ep
                max_gain_pct = max(max_gain_pct, high_pnl)
                if high_pnl >= tp_pct:
                    ret = tp_pct - 2 * FEE
                    trades.append(ret)
                    last_exit = bi
                    break
                if trail_erosion < 1.0 and max_gain_pct > noloss:
                    if pnl_pct < max_gain_pct * (1 - trail_erosion):
                        trades.append(pnl_pct - 2 * FEE)
                        last_exit = bi
                        break
                if max_gain_pct >= noloss and pnl_pct <= gain_th_low:
                    trades.append(pnl_pct - 2 * FEE)
                    last_exit = bi
                    break
                if not stop_major_block and stop_loss_th < 100:
                    if max_gain_pct >= stop_loss_th and pnl_pct <= 0:
                        trades.append(pnl_pct - 2 * FEE)
                        last_exit = bi
                        break
                if wrong_way and bi == ei + 1 and pnl_pct < -0.005:
                    trades.append(pnl_pct - 2 * FEE)
                    last_exit = bi
                    break
            else:
                pnl_final = (c[min(ei + max_hold, n - 1)] - ep) / ep if is_long else (ep - c[min(ei + max_hold, n - 1)]) / ep
                trades.append(pnl_final - 2 * FEE)
                last_exit = min(ei + max_hold, n - 1)
    run_trades(long_entries, True)
    run_trades(short_entries, False)
    if len(trades) < MIN_TRADES:
        return None
    t = np.array(trades)
    wr = np.mean(t > 0) * 100
    total_pnl = np.sum(t) * 100
    wins = t[t > 0]
    losses = t[t <= 0]
    pf = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 99.0
    sharpe = np.mean(t) / np.std(t) * math.sqrt(len(t)) if np.std(t) > 1e-10 else 0.0
    return {"sharpe": round(sharpe, 4), "wr": round(wr, 2), "total_pnl": round(total_pnl, 2), "pf": round(pf, 3), "trades": len(trades)}

# ═══════════════════════════════════════════════════════════════════════
# CONFIGS
# ═══════════════════════════════════════════════════════════════════════
def build_all_changes():
    baseline = {c[1]: c[2] for c in CHANGES}
    all_ch = dict(baseline)
    for c in CHANGES:
        all_ch[c[1]] = c[3]
    return baseline, all_ch

# ═══════════════════════════════════════════════════════════════════════
# WORKER: test all changes on one symbol
# ═══════════════════════════════════════════════════════════════════════
def test_symbol(sym):
    sd = precompute_symbol(sym)
    if sd is None:
        return None
    _, all_changes = build_all_changes()
    res_all = simulate_config(sd, all_changes)
    if res_all is None:
        return None
    results = [("ALL_CHANGES", sym, res_all["sharpe"], res_all["wr"], res_all["total_pnl"], res_all["pf"], res_all["trades"], 0.0)]
    for change in CHANGES:
        cid, param, bval, cval, desc = change
        if bval == cval:
            continue
        dropped = dict(all_changes)
        dropped[param] = bval
        try:
            res_drop = simulate_config(sd, dropped)
        except:
            continue
        if res_drop is None:
            continue
        delta = res_drop["sharpe"] - res_all["sharpe"]
        results.append((cid, sym, res_drop["sharpe"], res_drop["wr"], res_drop["total_pnl"], res_drop["pf"], res_drop["trades"], delta))
    return results

# ═══════════════════════════════════════════════════════════════════════
# DB + REPORT
# ═══════════════════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DROP TABLE IF EXISTS ablation")
    conn.execute("""CREATE TABLE ablation (
        change_id TEXT, symbol TEXT, sharpe REAL, wr REAL, total_pnl REAL, pf REAL, trades INT, sharpe_delta REAL
    )""")
    conn.commit()
    return conn

def write_report(conn):
    # ALL_CHANGES baseline stats
    base = conn.execute("SELECT AVG(sharpe), AVG(wr), SUM(total_pnl), COUNT(*) FROM ablation WHERE change_id='ALL_CHANGES'").fetchone()
    rows = conn.execute("""
        SELECT change_id,
               AVG(sharpe_delta) as avg_delta,
               SUM(CASE WHEN sharpe_delta > 0 THEN 1 ELSE 0 END) as improved,
               COUNT(*) as tested,
               AVG(sharpe) as avg_sharpe,
               AVG(wr) as avg_wr
        FROM ablation WHERE change_id != 'ALL_CHANGES'
        GROUP BY change_id ORDER BY avg_delta DESC
    """).fetchall()
    change_map = {c[0]: c[4] for c in CHANGES}
    lines = [
        "# ABLATION v3 — Deep History (1h primary, 4h+D HTF)",
        "**Generated:** " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "**Primary TF:** 15m (up to 100k bars, 3 years) | **HTF:** 1h, 4h",
        "**Method:** Drop-one-out from ALL_CHANGES config",
        "",
        "## ALL_CHANGES Baseline",
        "| Avg Sharpe | Avg WR | Total PnL | Symbols |",
        "|------------|--------|-----------|---------|",
        "| %.3f | %.1f%% | %.1f%% | %d |" % (base[0] or 0, base[1] or 0, base[2] or 0, base[3] or 0),
        "",
        "## Drop-One-Out Rankings",
        "**Positive Sharpe Δ = removing the change HELPS (change was hurting)**",
        "",
        "| # | Change | Description | Avg Sharpe Δ | Improved | Verdict |",
        "|---|--------|-------------|-------------|----------|---------|",
    ]
    for i, row in enumerate(rows):
        cid, avg_d, imp, tested, avg_s, avg_w = row
        pct = imp / tested * 100 if tested > 0 else 0
        desc = change_map.get(cid, cid)
        if avg_d > 0.5 and pct > 55:
            verdict = "**REVERT**"
        elif avg_d < -0.5 and pct < 45:
            verdict = "**KEEP**"
        else:
            verdict = "NEUTRAL"
        lines.append("| %d | %s | %s | %+.3f | %d/%d (%.0f%%) | %s |" % (i + 1, cid, desc, avg_d, imp, tested, pct, verdict))
    lines.append("")
    revert = [r for r in rows if r[1] > 0.5 and r[2] / r[3] > 0.55]
    keep = [r for r in rows if r[1] < -0.5 and r[2] / r[3] < 0.45]
    lines.append("## Summary: **%d REVERT, %d KEEP, %d NEUTRAL**" % (len(revert), len(keep), len(rows) - len(revert) - len(keep)))
    if revert:
        lines.append("\n### REVERT these (removing them helps):")
        for r in revert:
            lines.append("- **%s**: %s — Sharpe Δ %+.3f" % (r[0], change_map.get(r[0], ""), r[1]))
    if keep:
        lines.append("\n### KEEP these (removing them hurts):")
        for r in keep:
            lines.append("- **%s**: %s — Sharpe Δ %+.3f" % (r[0], change_map.get(r[0], ""), r[1]))
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print("\nReport: " + str(REPORT_PATH))

# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=14)
    args = parser.parse_args()
    symbols = get_symbols()
    print("Ablation v3: %d symbols, %d changes, 1h primary TF (deep history)" % (len(symbols), len(CHANGES)))
    print("Workers: %d" % args.workers)
    conn = init_db()
    done = 0
    total_rows = 0
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(test_symbol, s): s for s in symbols}
        for fut in as_completed(futures):
            done += 1
            sym = futures[fut]
            try:
                results = fut.result()
                if results:
                    for r in results:
                        conn.execute("INSERT INTO ablation VALUES (?,?,?,?,?,?,?,?)", r)
                    total_rows += len(results)
                    conn.commit()
            except Exception as e:
                print("  ERROR %s: %s" % (sym, e))
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(symbols) - done) / rate / 60 if rate > 0 else 0
            print("  [%d/%d] %s: %d rows — %.1f sym/s — ETA %.1fm" % (done, len(symbols), sym, total_rows, rate, eta))
    print("\nDone! %d rows from %d symbols in %.1f minutes" % (total_rows, done, (time.time() - t0) / 60))
    write_report(conn)
    conn.close()

if __name__ == "__main__":
    main()
