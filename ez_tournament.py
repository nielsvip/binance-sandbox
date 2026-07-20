#!/usr/bin/env python3
"""
STRATEGY TOURNAMENT — Parameter optimization with survivor elimination
======================================================================
Usage:
  python3 ez_tournament.py build               # create tournament_config.xlsx
  python3 ez_tournament.py run [--rounds N]    # run tournament from config
  python3 ez_tournament.py report              # print current standings

How it works:
  Round 1: All enabled parameter combinations tested on all symbols.
           Each combination is scored by avg Sharpe across symbols.
           Bottom (100-survive_pct)% are eliminated.
  Round 2: Survivors tested on a different (holdout) symbol set.
           Bottom half eliminated again.
  Round N: Final survivors get full cross-validation report.

Config is in tournament_config.xlsx — edit it to add/remove parameter options.
"""
import sys, os, json, math, time, sqlite3, argparse, itertools
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import numpy as np

BASE_PATH = Path(os.environ.get('BASE_PATH', '/home/niels/binance-sandbox'))
KLINES_DIR = BASE_PATH / 'klines_cache'
RESULTS_DIR = BASE_PATH / 'backtest_framework' / 'results'
CONFIG_XLS = BASE_PATH / 'tournament_config.xlsx'
DB_PATH = RESULTS_DIR / 'tournament.db'
FEE = 0.0004

# Bars per year per TF (crypto 24/7/365)
ANNUAL_BARS = {'D': 365, '4h': 2190, '1h': 8760, '15m': 35040}
MIN_BARS_TF = {'D': 80, '4h': 250, '1h': 400, '15m': 800}
WARMUP = 200


# ═══════════════════════════════════════════════════════════════════════
# INDICATOR ENGINE
# ═══════════════════════════════════════════════════════════════════════

def load_tf(sym, tf):
    p = KLINES_DIR / f'{sym}_{tf}.json'
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        if not data:
            return None
        if isinstance(data[0], dict):
            arr = np.array([[d['open'], d['high'], d['low'], d['close']] for d in data], dtype=np.float64)
        else:
            raw = np.array(data, dtype=np.float64)
            arr = raw[:, 1:5] if raw.ndim == 2 and raw.shape[1] >= 5 else raw[:, :4]
        return arr
    except Exception:
        return None


def stoch_kd(h, l, c, k_period=14, k_smooth=3, d_smooth=3):
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = h[i - k_period + 1:i + 1].max()
        ll = l[i - k_period + 1:i + 1].min()
        raw_k[i] = (c[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
    k = np.convolve(raw_k, np.ones(k_smooth) / k_smooth, mode='same')
    d = np.convolve(k, np.ones(d_smooth) / d_smooth, mode='same')
    return k.astype(np.float32), d.astype(np.float32)


def heikin_ashi_color(o, h, l, c):
    hac = (o + h + l + c) / 4
    hao = np.empty(len(o))
    hao[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2
    return (hac > hao).astype(np.int8)


def donchian_mid(h, l, period=20):
    n = len(h)
    mid = np.full(n, np.nan)
    for i in range(period - 1, n):
        mid[i] = (h[i - period + 1:i + 1].max() + l[i - period + 1:i + 1].min()) / 2
    return mid


def make_idx_map(n_primary, n_tf):
    return np.clip(
        (np.arange(n_primary) * n_tf / n_primary).astype(np.int32),
        0, n_tf - 1
    )


def compute_metrics(trades, primary_tf):
    if len(trades) < 2:
        return None
    rets = np.array(trades, dtype=np.float64)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    if len(losses) == 0:
        pf = 99.0
    elif len(wins) == 0:
        pf = 0.0
    else:
        pf = wins.sum() / max(abs(losses.sum()), 1e-9)
    mean_r = rets.mean()
    std_r = rets.std()
    annual_factor = ANNUAL_BARS.get(primary_tf, 365)
    sharpe = mean_r / std_r if std_r > 1e-9 else 0.0  # per-trade pool_sharpe (sqrt(annual_factor) stripped 2026-04-29 per CLAUDE.md rule 4)
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    return {
        'sharpe': round(float(sharpe), 4),
        'win_rate': round(float((rets > 0).mean()), 4),
        'pf': round(float(pf), 4),
        'max_dd': round(float(max_dd), 4),
        'total_return': round(float(rets.sum()), 4),
        'n_trades': int(len(trades)),
    }


# ═══════════════════════════════════════════════════════════════════════
# SIMULATION CORE
# ═══════════════════════════════════════════════════════════════════════

def run_combo(sym, is_long, params):
    """Run one parameter combo on one symbol. Returns metrics dict or None."""
    stoch_k = int(params['stoch_k'])
    stoch_sk = int(params['stoch_smooth'])
    stoch_sd = int(params['stoch_d_smooth'])
    tp_pct = float(params['tp_pct'])
    tf_combo_str = str(params['tf_combo'])  # e.g. "D+4h+1h+15m"
    entry_gate = str(params['entry_gate'])  # "cross", "cross+ha", "cross+ha+dc"
    exit_mode = str(params['exit_mode'])    # "fixed", "dynamic", "dynamic_or_tp"
    k_thresh = int(params.get('k_thresh', 80))
    max_bars_map = {'D': 30, '4h': 60, '1h': 120, '15m': 250}
    tfs = tf_combo_str.split('+')
    primary_tf = tfs[-1]
    htf_list = tfs[:-1]
    min_b = MIN_BARS_TF.get(primary_tf, 300)
    primary = load_tf(sym, primary_tf)
    if primary is None or len(primary) < min_b + WARMUP:
        return None
    n = len(primary)
    o, h, l, c = primary[:, 0], primary[:, 1], primary[:, 2], primary[:, 3]
    k, d = stoch_kd(h, l, c, stoch_k, stoch_sk, stoch_sd)
    ha = heikin_ashi_color(o, h, l, c)
    dc_mid = donchian_mid(h, l, 20) if 'dc' in entry_gate else None
    k_prev = np.roll(k, 1)
    d_prev = np.roll(d, 1)
    co = (k > d) & (k_prev <= d_prev)
    cu = (k < d) & (k_prev >= d_prev)
    co[0] = False; cu[0] = False
    # Build HTF arrays (indexed to primary length)
    htf_data = {}
    for htf in htf_list:
        data = load_tf(sym, htf)
        if data is None or len(data) < 50:
            return None  # missing required HTF — skip this combo entirely
        nt = len(data)
        idx = make_idx_map(n, nt)
        o2, h2, l2, c2 = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
        k2, d2 = stoch_kd(h2, l2, c2, stoch_k, stoch_sk, stoch_sd)
        ha2 = heikin_ashi_color(o2, h2, l2, c2)
        htf_data[htf] = {
            'k': k2[idx].astype(np.float32),
            'd': d2[idx].astype(np.float32),
            'ha': ha2[idx],
        }
    max_bars = max_bars_map.get(primary_tf, 250)
    trades = []
    in_trade = False
    ep = 0.0
    eb = 0
    k_arr = k
    d_arr = d
    # Also precompute k prev for dynamic exit
    for bar in range(WARMUP, n - 1):
        price = c[bar]
        if price <= 0:
            continue
        if in_trade:
            gain = (price - ep) / ep * 100.0 if is_long else (ep - price) / ep * 100.0
            timeout = (bar - eb) >= max_bars
            should_exit = False
            if exit_mode == 'fixed':
                should_exit = gain >= tp_pct
            elif exit_mode == 'dynamic':
                kv = float(k_arr[bar])
                kv_prev = float(k_arr[max(0, bar - 1)])
                if is_long:
                    should_exit = (kv >= k_thresh and kv < kv_prev) or gain >= tp_pct * 3
                else:
                    should_exit = (kv <= (100 - k_thresh) and kv > kv_prev) or gain >= tp_pct * 3
            elif exit_mode == 'dynamic_or_tp':
                kv = float(k_arr[bar])
                kv_prev = float(k_arr[max(0, bar - 1)])
                if is_long:
                    should_exit = gain >= tp_pct or (kv >= k_thresh and kv < kv_prev)
                else:
                    should_exit = gain >= tp_pct or (kv <= (100 - k_thresh) and kv > kv_prev)
            if should_exit or timeout:
                ret = ((price - ep) / ep if is_long else (ep - price) / ep) - 2 * FEE
                trades.append(ret * 100.0)
                in_trade = False
        else:
            # Entry trigger
            if is_long and not co[bar]:
                continue
            if not is_long and not cu[bar]:
                continue
            # Primary TF alignment
            kv = float(k_arr[bar])
            dv = float(d_arr[bar])
            if is_long and kv <= dv:
                continue
            if not is_long and kv >= dv:
                continue
            # HA alignment (if requested)
            if 'ha' in entry_gate:
                hav = int(ha[bar])
                if is_long and hav != 1:
                    continue
                if not is_long and hav != 0:
                    continue
            # DC alignment (price above/below DC midpoint)
            if 'dc' in entry_gate and dc_mid is not None:
                dcv = float(dc_mid[bar])
                if dcv > 0:
                    if is_long and price <= dcv:
                        continue
                    if not is_long and price >= dcv:
                        continue
            # k_3m overbought cap (if k3m_cap > 0)
            k3m_cap = int(params.get('k3m_cap', 0))
            if k3m_cap > 0:
                # Use primary TF stoch as proxy for k_3m-style cap
                if is_long and kv >= k3m_cap:
                    continue
                if not is_long and kv <= (100 - k3m_cap):
                    continue
            # HTF alignment gates
            htf_strict = str(params.get('htf_strict', 'kd_only'))
            aligned = True
            htf_votes = 0
            for htf in htf_list:
                hd = htf_data[htf]
                hkv = float(hd['k'][bar])
                hdv = float(hd['d'][bar])
                hhav = int(hd['ha'][bar])
                kd_ok = (hkv > hdv) if is_long else (hkv < hdv)
                ha_ok = (hhav == 1) if is_long else (hhav == 0)
                if htf_strict == 'strict':
                    if not (kd_ok and ha_ok):
                        aligned = False
                        break
                elif htf_strict == 'kd_only':
                    if not kd_ok:
                        aligned = False
                        break
                elif htf_strict == '2of3':
                    if kd_ok:
                        htf_votes += 1
            if htf_strict == '2of3' and len(htf_list) >= 2 and htf_votes < 2:
                aligned = False
            if not aligned:
                continue
            in_trade = True
            ep = price
            eb = bar
    if in_trade:
        price = c[n - 1]
        ret = ((price - ep) / ep if is_long else (ep - price) / ep) - 2 * FEE
        trades.append(ret * 100.0)
    return compute_metrics(trades, primary_tf)


# ═══════════════════════════════════════════════════════════════════════
# TOURNAMENT WORKER (subprocess)
# ═══════════════════════════════════════════════════════════════════════

def tournament_worker(args):
    combo_id, sym, is_long, params = args
    metrics = run_combo(sym, is_long, params)
    return combo_id, sym, is_long, metrics


# ═══════════════════════════════════════════════════════════════════════
# BUILD CONFIG XLSX
# ═══════════════════════════════════════════════════════════════════════

def build_config():
    """Create tournament_config.xlsx with all known parameter variations."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill, Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("ERROR: openpyxl required. Run: pip install openpyxl")
        sys.exit(1)

    wb = Workbook()

    # ── Sheet 1: PARAMS ────────────────────────────────────────────────
    ws = wb.active
    ws.title = "PARAMS"

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(color="FFFFFF", bold=True, size=11)
    alt_fill = PatternFill("solid", fgColor="D6E4F0")

    # Column headers
    headers = ["enabled", "category", "param", "value", "description"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center')

    # Column widths
    for col, w in enumerate([9, 18, 20, 10, 45], 1):
        ws.column_dimensions[get_column_letter(col)].width = w

    PARAM_ROWS = [
        # category, param, value, description
        # ── Stochastic K period ──
        ("STOCH_K",      "stoch_k",        9,      "Stochastic K lookback period — faster"),
        ("STOCH_K",      "stoch_k",        14,     "Stochastic K lookback period — standard (live baseline)"),
        ("STOCH_K",      "stoch_k",        21,     "Stochastic K lookback period — slower"),
        # ── Stochastic smooth ──
        ("STOCH_SMOOTH", "stoch_smooth",   2,      "Stoch K smoothing — fast"),
        ("STOCH_SMOOTH", "stoch_smooth",   3,      "Stoch K smoothing — standard (live baseline)"),
        ("STOCH_SMOOTH", "stoch_smooth",   5,      "Stoch K smoothing — slow"),
        # ── Stochastic D smooth ──
        ("STOCH_D",      "stoch_d_smooth", 2,      "Stoch D smoothing — fast"),
        ("STOCH_D",      "stoch_d_smooth", 3,      "Stoch D smoothing — standard (live baseline)"),
        ("STOCH_D",      "stoch_d_smooth", 5,      "Stoch D smoothing — slow"),
        # ── Take profit ──
        ("TP_PCT",       "tp_pct",         0.5,    "Take profit 0.5%"),
        ("TP_PCT",       "tp_pct",         1.0,    "Take profit 1.0% (live baseline)"),
        ("TP_PCT",       "tp_pct",         1.5,    "Take profit 1.5%"),
        ("TP_PCT",       "tp_pct",         2.0,    "Take profit 2.0%"),
        ("TP_PCT",       "tp_pct",         3.0,    "Take profit 3.0%"),
        # ── Exit mode ──
        ("EXIT_MODE",    "exit_mode",      "fixed",          "Exit at exact TP% — simple (live baseline)"),
        ("EXIT_MODE",    "exit_mode",      "dynamic",        "Exit when stoch exhausted OR 3×TP"),
        ("EXIT_MODE",    "exit_mode",      "dynamic_or_tp",  "Stoch exhaustion OR TP — whichever first"),
        # ── Dynamic exit K threshold ──
        ("K_THRESH",     "k_thresh",       70,     "Stoch K overbought/oversold threshold — aggressive"),
        ("K_THRESH",     "k_thresh",       75,     "Stoch K threshold — moderate"),
        ("K_THRESH",     "k_thresh",       80,     "Stoch K threshold — standard (live baseline)"),
        # ── Entry gate conditions ──
        ("ENTRY_GATE",   "entry_gate",     "cross",          "Entry: stoch K/D cross only"),
        ("ENTRY_GATE",   "entry_gate",     "cross+ha",       "Entry: stoch cross + HA aligned (live baseline)"),
        ("ENTRY_GATE",   "entry_gate",     "cross+ha+dc",    "Entry: stoch cross + HA + price above/below DC mid"),
        # ── Entry K_3m overbought cap (live: k_3m < 80 for longs, > 20 for shorts) ──
        ("K3M_CAP",      "k3m_cap",        0,      "No k_3m overbought cap (ignore)"),
        ("K3M_CAP",      "k3m_cap",        70,     "Cap entries at k_3m < 70 (conservative, live baseline-like)"),
        ("K3M_CAP",      "k3m_cap",        80,     "Cap entries at k_3m < 80 (live baseline)"),
        # ── HTF alignment strictness ──
        ("HTF_STRICT",   "htf_strict",     "strict",  "All HTFs must be K>D+HA aligned (strictest)"),
        ("HTF_STRICT",   "htf_strict",     "kd_only", "HTFs must be K>D only, no HA requirement (live baseline)"),
        ("HTF_STRICT",   "htf_strict",     "2of3",    "2 out of 3 HTFs must align (most relaxed)"),
        # ── TF combo ──
        ("TF_COMBO",     "tf_combo",       "D",              "Daily only — longest range"),
        ("TF_COMBO",     "tf_combo",       "D+4h",           "Daily + 4h alignment"),
        ("TF_COMBO",     "tf_combo",       "D+4h+1h",        "Daily + 4h + 1h alignment"),
        ("TF_COMBO",     "tf_combo",       "D+4h+1h+15m",    "All TFs — most filtered"),
    ]

    # Which are enabled by default
    # Live system baseline: stoch 14/3/3, TP 1%, fixed exit, k3m cap 80, kd_only HTF, cross+ha gate, D+4h+1h+15m
    ENABLED_BY_DEFAULT = {
        ("STOCH_K",      14),
        ("STOCH_K",      9),
        ("STOCH_SMOOTH", 3),
        ("STOCH_SMOOTH", 5),
        ("STOCH_D",      3),
        ("STOCH_D",      5),
        ("TP_PCT",       1.0),
        ("TP_PCT",       1.5),
        ("TP_PCT",       2.0),
        ("EXIT_MODE",    "fixed"),
        ("EXIT_MODE",    "dynamic_or_tp"),
        ("K_THRESH",     80),
        ("K_THRESH",     75),
        ("ENTRY_GATE",   "cross+ha"),
        ("ENTRY_GATE",   "cross+ha+dc"),
        ("K3M_CAP",      70),
        ("K3M_CAP",      80),
        ("HTF_STRICT",   "kd_only"),
        ("HTF_STRICT",   "strict"),
        ("TF_COMBO",     "D+4h"),
        ("TF_COMBO",     "D+4h+1h"),
        ("TF_COMBO",     "D+4h+1h+15m"),
    }

    for row_idx, (cat, param, val, desc) in enumerate(PARAM_ROWS, 2):
        enabled = (cat, val) in ENABLED_BY_DEFAULT
        ws.cell(row=row_idx, column=1, value="YES" if enabled else "no")
        ws.cell(row=row_idx, column=2, value=cat)
        ws.cell(row=row_idx, column=3, value=param)
        ws.cell(row=row_idx, column=4, value=val)
        ws.cell(row=row_idx, column=5, value=desc)
        if row_idx % 2 == 0:
            for col in range(1, 6):
                ws.cell(row=row_idx, column=col).fill = alt_fill

    # ── Sheet 2: SYMBOLS ──────────────────────────────────────────────
    ws2 = wb.create_sheet("SYMBOLS")
    for col, h in enumerate(["enabled", "symbol", "side", "note"], 1):
        cell = ws2.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font

    long_syms, short_syms = _get_symbols_from_files()
    r = 2
    for sym in long_syms:
        ws2.cell(row=r, column=1, value="YES")
        ws2.cell(row=r, column=2, value=sym)
        ws2.cell(row=r, column=3, value="LONG")
        r += 1
    for sym in short_syms:
        ws2.cell(row=r, column=1, value="YES")
        ws2.cell(row=r, column=2, value=sym)
        ws2.cell(row=r, column=3, value="SHORT")
        r += 1

    ws2.column_dimensions['A'].width = 9
    ws2.column_dimensions['B'].width = 18
    ws2.column_dimensions['C'].width = 8
    ws2.column_dimensions['D'].width = 35

    # ── Sheet 3: CONFIG ───────────────────────────────────────────────
    ws3 = wb.create_sheet("CONFIG")
    for col, h in enumerate(["setting", "value", "description"], 1):
        cell = ws3.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font

    config_rows = [
        ("min_trades",   8,     "Minimum trades for a result to count"),
        ("survive_pct",  25,    "% of combos that survive each round (top N%)"),
        ("max_rounds",   3,     "Maximum tournament rounds"),
        ("workers",      0,     "CPU workers (0 = auto)"),
        ("long_only",    0,     "1 = test LONG side only"),
        ("short_only",   0,     "1 = test SHORT side only"),
    ]
    for r, (s, v, d) in enumerate(config_rows, 2):
        ws3.cell(row=r, column=1, value=s)
        ws3.cell(row=r, column=2, value=v)
        ws3.cell(row=r, column=3, value=d)
    ws3.column_dimensions['A'].width = 16
    ws3.column_dimensions['B'].width = 8
    ws3.column_dimensions['C'].width = 50

    # ── Sheet 4: ACCOUNT_PROFILES ──────────────────────────────────────
    ws_ap = wb.create_sheet("ACCOUNT_PROFILES")
    for col, h in enumerate(["account", "style", "tf_preference", "tp_preference", "note"], 1):
        cell = ws_ap.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
    profiles = [
        ("ang",  "LT_winners_losers", "D+4h",       "1.5-2.0",  "LT long/short pairs — prefers D+4h alignment, higher TP"),
        ("inf",  "ST_winners_losers", "D+4h+1h",    "1.0-1.5",  "ST long/short pairs — 1h entry, moderate TP"),
        ("flz",  "blue_chips",        "D+4h+1h+15m","1.0",      "Blue chips, no hedging — tightest filter, small TP"),
        ("men",  "fixed_new_imports", "D+4h+1h",    "1.0-1.5",  "Fixed list + new imports — balanced settings"),
        ("fin",  "fixed_manual",      "D+4h",       "2.0-3.0",  "Fixed list, manual trading — fewer larger moves"),
    ]
    for r, row in enumerate(profiles, 2):
        for c, v in enumerate(row, 1):
            ws_ap.cell(row=r, column=c, value=v)
    for col, w in enumerate([8, 20, 16, 12, 55], 1):
        ws_ap.column_dimensions[get_column_letter(col)].width = w

    # ── Sheet 5: RESULTS (placeholder) ────────────────────────────────
    ws4 = wb.create_sheet("RESULTS")
    result_headers = ["round", "rank", "combo_id", "tf_combo", "stoch_k",
                      "stoch_smooth", "stoch_d", "tp_pct", "exit_mode", "k_thresh",
                      "entry_gate", "k3m_cap", "htf_strict", "avg_sharpe", "avg_wr",
                      "avg_pf", "avg_maxdd", "avg_return", "total_trades", "n_symbols", "status"]
    for col, h in enumerate(result_headers, 1):
        cell = ws4.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        ws4.column_dimensions[get_column_letter(col)].width = 13

    # ── Sheet 6: ACCOUNT_WINNERS (populated after run) ─────────────────
    ws_aw = wb.create_sheet("ACCOUNT_WINNERS")
    aw_headers = ["account", "rank", "tf_combo", "stoch_k", "stoch_smooth", "stoch_d",
                  "tp_pct", "exit_mode", "k_thresh", "entry_gate", "k3m_cap", "htf_strict",
                  "avg_sharpe", "avg_wr", "avg_pf", "avg_maxdd", "n_trades", "n_symbols"]
    for col, h in enumerate(aw_headers, 1):
        cell = ws_aw.cell(row=1, column=col, value=h)
        cell.fill = header_fill
        cell.font = header_font
        ws_aw.column_dimensions[get_column_letter(col)].width = 12

    wb.save(str(CONFIG_XLS))
    print(f"Config created: {CONFIG_XLS}")
    total_combos = _count_combos(wb)
    print(f"Enabled combinations: {total_combos}")
    print(f"  Symbols: {len(long_syms)} long + {len(short_syms)} short")
    print(f"  5 account profiles: ang (LT), inf (ST), flz (blue chips), men (fixed+new), fin (manual)")
    print(f"  Edit PARAMS sheet: change 'YES'/'no' in column A to enable/disable options")
    print(f"  Edit ACCOUNT_PROFILES sheet: adjust TF/TP preferences per account")
    print(f"  Add new rows to PARAMS to test new values")
    return wb


def _count_combos(wb_or_path):
    """Count total enabled parameter combinations."""
    try:
        from openpyxl import load_workbook
        if isinstance(wb_or_path, (str, Path)):
            wb = load_workbook(str(wb_or_path))
        else:
            wb = wb_or_path
        ws = wb["PARAMS"]
        categories = {}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row[0] or str(row[0]).strip().upper() != 'YES':
                continue
            cat, param, val = str(row[1]), str(row[2]), row[3]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append((param, val))
        total = 1
        for cat, opts in categories.items():
            total *= len(opts)
        return total
    except Exception:
        return -1


def _get_symbols_from_files():
    long_syms, short_syms = [], []
    for fname in ['symbols_ang_long.json', 'symbols_inf_long.json']:
        fp = BASE_PATH / fname
        if fp.exists():
            with open(fp) as f:
                long_syms = json.load(f)
            break
    for fname in ['symbols_ang_short.json', 'symbols_inf_short.json']:
        fp = BASE_PATH / fname
        if fp.exists():
            with open(fp) as f:
                short_syms = json.load(f)
            break
    if not long_syms or not short_syms:
        sf = BASE_PATH / 'symbols.json'
        if sf.exists():
            with open(sf) as f:
                data = json.load(f)
            syms = data if isinstance(data, list) else list(data.keys())
            syms = [s for s in syms if (KLINES_DIR / f'{s}_15m.json').exists()][:60]
            long_syms = long_syms or syms[:40]
            short_syms = short_syms or syms[:40]
    return long_syms, short_syms


# ═══════════════════════════════════════════════════════════════════════
# LOAD CONFIG FROM XLSX
# ═══════════════════════════════════════════════════════════════════════

def load_config():
    """Load params, symbols, and tournament config from XLSX."""
    from openpyxl import load_workbook
    wb = load_workbook(str(CONFIG_XLS))

    # Params
    ws = wb["PARAMS"]
    categories = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0] or str(row[0]).strip().upper() != 'YES':
            continue
        cat, param, val = str(row[1]), str(row[2]), row[3]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((param, val))

    # Generate all combos (Cartesian product across categories)
    cat_names = sorted(categories.keys())
    all_combos = []
    for cat_combo in itertools.product(*[categories[c] for c in cat_names]):
        combo_params = {}
        for (param_name, val) in cat_combo:
            combo_params[param_name] = val
        all_combos.append(combo_params)

    # Symbols
    ws2 = wb["SYMBOLS"]
    long_syms = []
    short_syms = []
    for row in ws2.iter_rows(min_row=2, values_only=True):
        if not row[0] or str(row[0]).strip().upper() != 'YES':
            continue
        sym = str(row[1]).strip()
        side = str(row[2]).strip().upper()
        if side == 'LONG':
            long_syms.append(sym)
        elif side == 'SHORT':
            short_syms.append(sym)

    # Config
    ws3 = wb["CONFIG"]
    cfg = {}
    for row in ws3.iter_rows(min_row=2, values_only=True):
        if row[0] and row[1] is not None:
            cfg[str(row[0]).strip()] = row[1]

    return all_combos, long_syms, short_syms, cfg


# ═══════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════

def init_db():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round INTEGER, combo_id INTEGER, symbol TEXT, side TEXT,
        tf_combo TEXT, stoch_k INTEGER, stoch_smooth INTEGER, stoch_d_smooth INTEGER,
        tp_pct REAL, exit_mode TEXT, k_thresh INTEGER, entry_gate TEXT,
        sharpe REAL, win_rate REAL, pf REAL, max_dd REAL, total_return REAL,
        n_trades INTEGER, ts TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS combo_scores (
        round INTEGER, combo_id INTEGER, combo_label TEXT,
        avg_sharpe REAL, avg_wr REAL, avg_pf REAL, avg_maxdd REAL,
        avg_return REAL, total_trades INTEGER, n_symbols INTEGER,
        survived INTEGER, ts TEXT,
        PRIMARY KEY (round, combo_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_r ON results(round)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_c ON results(combo_id)")
    conn.commit()
    return conn


def store_results(conn, round_num, combo_id, results_list):
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for r in results_list:
        p = r['params']
        m = r['metrics']
        rows.append((
            round_num, combo_id, r['symbol'], r['side'],
            str(p.get('tf_combo', '')), int(p.get('stoch_k', 14)),
            int(p.get('stoch_smooth', 3)), int(p.get('stoch_d_smooth', 3)),
            float(p.get('tp_pct', 1.0)), str(p.get('exit_mode', 'fixed')),
            int(p.get('k_thresh', 80)), str(p.get('entry_gate', 'cross+ha')),
            m['sharpe'], m['win_rate'], m['pf'], m['max_dd'], m['total_return'],
            m['n_trades'], now
        ))
    conn.executemany("""INSERT INTO results
        (round, combo_id, symbol, side, tf_combo, stoch_k, stoch_smooth, stoch_d_smooth,
         tp_pct, exit_mode, k_thresh, entry_gate, sharpe, win_rate, pf, max_dd,
         total_return, n_trades, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()


def score_round(conn, round_num, min_trades, all_combos):
    """Compute avg metrics per combo_id for this round."""
    cur = conn.cursor()
    scored = []
    for i, params in enumerate(all_combos):
        rows = cur.execute("""SELECT sharpe, win_rate, pf, max_dd, total_return, n_trades
            FROM results WHERE round=? AND combo_id=? AND n_trades>=?""",
            (round_num, i, min_trades)).fetchall()
        if not rows:
            continue
        sharpes = [r[0] for r in rows]
        wrs = [r[1] for r in rows]
        pfs = [r[2] for r in rows]
        dds = [r[3] for r in rows]
        rets = [r[4] for r in rows]
        trades = [r[5] for r in rows]
        label = ",".join(f"{k}={v}" for k, v in sorted(params.items()))
        scored.append({
            'combo_id': i,
            'label': label,
            'params': params,
            'avg_sharpe': sum(sharpes) / len(sharpes),
            'avg_wr': sum(wrs) / len(wrs),
            'avg_pf': sum(pfs) / len(pfs),
            'avg_maxdd': sum(dds) / len(dds),
            'avg_return': sum(rets) / len(rets),
            'total_trades': sum(trades),
            'n_symbols': len(rows),
        })
    scored.sort(key=lambda x: x['avg_sharpe'], reverse=True)
    return scored


def write_results_to_xlsx(conn, all_combos):
    """Update RESULTS sheet in config XLSX with current standings."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(CONFIG_XLS))
        ws = wb["RESULTS"]
        # Clear existing data
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.value = None
        cur = conn.cursor()
        round_max = cur.execute("SELECT MAX(round) FROM combo_scores").fetchone()[0]
        if round_max is None:
            wb.save(str(CONFIG_XLS))
            return
        rows = cur.execute("""SELECT round, combo_id, combo_label, avg_sharpe, avg_wr, avg_pf,
            avg_maxdd, avg_return, total_trades, n_symbols, survived
            FROM combo_scores ORDER BY round, avg_sharpe DESC""").fetchall()
        for r_idx, row in enumerate(rows, 2):
            round_num, cid, label, sharpe, wr, pf, dd, ret, trades, n_syms, survived = row
            p = all_combos[cid] if cid < len(all_combos) else {}
            ws.cell(row=r_idx, column=1, value=round_num)
            ws.cell(row=r_idx, column=2, value=r_idx - 1)
            ws.cell(row=r_idx, column=3, value=cid)
            ws.cell(row=r_idx, column=4, value=p.get('tf_combo', ''))
            ws.cell(row=r_idx, column=5, value=p.get('stoch_k', ''))
            ws.cell(row=r_idx, column=6, value=p.get('stoch_smooth', ''))
            ws.cell(row=r_idx, column=7, value=p.get('stoch_d_smooth', ''))
            ws.cell(row=r_idx, column=8, value=p.get('tp_pct', ''))
            ws.cell(row=r_idx, column=9, value=p.get('exit_mode', ''))
            ws.cell(row=r_idx, column=10, value=p.get('k_thresh', ''))
            ws.cell(row=r_idx, column=11, value=p.get('entry_gate', ''))
            ws.cell(row=r_idx, column=12, value=p.get('k3m_cap', ''))
            ws.cell(row=r_idx, column=13, value=p.get('htf_strict', ''))
            ws.cell(row=r_idx, column=14, value=round(sharpe, 4) if sharpe else '')
            ws.cell(row=r_idx, column=15, value=f"{wr*100:.1f}%" if wr else '')
            ws.cell(row=r_idx, column=16, value=round(pf, 2) if pf else '')
            ws.cell(row=r_idx, column=17, value=round(dd, 2) if dd else '')
            ws.cell(row=r_idx, column=18, value=round(ret, 2) if ret else '')
            ws.cell(row=r_idx, column=19, value=trades)
            ws.cell(row=r_idx, column=20, value=n_syms)
            ws.cell(row=r_idx, column=21, value="SURVIVED" if survived else "ELIMINATED")
        # ── ACCOUNT_WINNERS: top 3 combos per account based on TF preference ──
        try:
            ws_aw = wb["ACCOUNT_WINNERS"]
            for row in ws_aw.iter_rows(min_row=2):
                for cell in row:
                    cell.value = None
            ws_ap = wb["ACCOUNT_PROFILES"]
            r_idx = 2
            for profile_row in ws_ap.iter_rows(min_row=2, values_only=True):
                if not profile_row[0]:
                    continue
                acct, style, tf_pref, tp_pref, note = profile_row
                # Find top combos matching this account's TF preference
                top = cur.execute("""SELECT combo_id, avg_sharpe, avg_wr, avg_pf, avg_maxdd, total_trades, n_symbols
                    FROM combo_scores WHERE survived=1 ORDER BY avg_sharpe DESC""").fetchall()
                rank = 0
                for t in top:
                    cid, sharpe, wr, pf, dd, trades, n_syms = t
                    p = all_combos[cid] if cid < len(all_combos) else {}
                    # Match TF preference (partial match OK)
                    tf = str(p.get('tf_combo', ''))
                    if tf_pref and tf_pref not in tf:
                        continue
                    rank += 1
                    ws_aw.cell(row=r_idx, column=1, value=acct)
                    ws_aw.cell(row=r_idx, column=2, value=rank)
                    ws_aw.cell(row=r_idx, column=3, value=tf)
                    ws_aw.cell(row=r_idx, column=4, value=p.get('stoch_k', ''))
                    ws_aw.cell(row=r_idx, column=5, value=p.get('stoch_smooth', ''))
                    ws_aw.cell(row=r_idx, column=6, value=p.get('stoch_d_smooth', ''))
                    ws_aw.cell(row=r_idx, column=7, value=p.get('tp_pct', ''))
                    ws_aw.cell(row=r_idx, column=8, value=p.get('exit_mode', ''))
                    ws_aw.cell(row=r_idx, column=9, value=p.get('k_thresh', ''))
                    ws_aw.cell(row=r_idx, column=10, value=p.get('entry_gate', ''))
                    ws_aw.cell(row=r_idx, column=11, value=p.get('k3m_cap', ''))
                    ws_aw.cell(row=r_idx, column=12, value=p.get('htf_strict', ''))
                    ws_aw.cell(row=r_idx, column=13, value=round(sharpe, 4) if sharpe else '')
                    ws_aw.cell(row=r_idx, column=14, value=f"{wr*100:.1f}%" if wr else '')
                    ws_aw.cell(row=r_idx, column=15, value=round(pf, 2) if pf else '')
                    ws_aw.cell(row=r_idx, column=16, value=round(dd, 2) if dd else '')
                    ws_aw.cell(row=r_idx, column=17, value=trades)
                    ws_aw.cell(row=r_idx, column=18, value=n_syms)
                    r_idx += 1
                    if rank >= 3:
                        break
        except Exception as e2:
            print(f"Warning: could not write account winners: {e2}")
        wb.save(str(CONFIG_XLS))
        print(f"Results written back to {CONFIG_XLS} (RESULTS + ACCOUNT_WINNERS sheets)")
    except Exception as e:
        print(f"Warning: could not write XLSX results: {e}")


# ═══════════════════════════════════════════════════════════════════════
# RUN TOURNAMENT
# ═══════════════════════════════════════════════════════════════════════

def run_tournament(max_rounds=3, workers=0, report_only=False):
    if not CONFIG_XLS.exists():
        print(f"Config not found: {CONFIG_XLS}")
        print("Run: python3 ez_tournament.py build")
        sys.exit(1)

    all_combos, long_syms, short_syms, cfg = load_config()
    min_trades = int(cfg.get('min_trades', 8))
    survive_pct = float(cfg.get('survive_pct', 25)) / 100.0
    if cfg.get('max_rounds'):
        max_rounds = int(cfg['max_rounds'])
    long_only = int(cfg.get('long_only', 0)) == 1
    short_only = int(cfg.get('short_only', 0)) == 1
    if workers <= 0:
        import multiprocessing
        if Path('/home/niels/binance_scripts_paused').exists():
            workers = max(multiprocessing.cpu_count() - 1, 4)
        else:
            load = os.getloadavg()[0]
            workers = max(2, min(8, int(multiprocessing.cpu_count() / 2 - load)))

    conn = init_db()

    if report_only:
        _print_report(conn, all_combos, min_trades)
        return

    print("=" * 70)
    print(f"STRATEGY TOURNAMENT")
    print(f"Combos: {len(all_combos)} | Symbols: {len(long_syms)}L+{len(short_syms)}S | Workers: {workers}")
    print(f"Survive per round: top {survive_pct*100:.0f}% | Max rounds: {max_rounds}")
    print("=" * 70)

    surviving_combos = list(range(len(all_combos)))
    all_syms_long = long_syms if not short_only else []
    all_syms_short = short_syms if not long_only else []

    for round_num in range(1, max_rounds + 1):
        print(f"\n{'='*70}")
        print(f"ROUND {round_num}  ({len(surviving_combos)} combos × {len(all_syms_long)+len(all_syms_short)} syms)")
        print(f"{'='*70}")

        # Build tasks: (combo_id, sym, is_long, params)
        tasks = []
        for cid in surviving_combos:
            params = all_combos[cid]
            for sym in all_syms_long:
                tasks.append((cid, sym, True, params))
            for sym in all_syms_short:
                tasks.append((cid, sym, False, params))

        t0 = time.time()
        done = 0
        combo_results = {}  # combo_id → list of {symbol, side, params, metrics}
        print(f"Running {len(tasks)} tasks...", flush=True)

        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(tournament_worker, t): t for t in tasks}
            for future in as_completed(futures):
                done += 1
                try:
                    combo_id, sym, is_long, metrics = future.result()
                    if metrics and metrics['n_trades'] >= min_trades:
                        if combo_id not in combo_results:
                            combo_results[combo_id] = []
                        combo_results[combo_id].append({
                            'symbol': sym,
                            'side': 'LONG' if is_long else 'SHORT',
                            'params': all_combos[combo_id],
                            'metrics': metrics,
                        })
                    if done % 100 == 0 or done == len(tasks):
                        elapsed = time.time() - t0
                        pct = done / len(tasks) * 100
                        print(f"  {done}/{len(tasks)} ({pct:.0f}%)  {elapsed:.0f}s", flush=True)
                except Exception as e:
                    pass  # silent skip

        # Store results
        now = datetime.now(timezone.utc).isoformat()
        for cid, res_list in combo_results.items():
            store_results(conn, round_num, cid, res_list)

        # Score and rank
        scored = score_round(conn, round_num, min_trades, all_combos)

        # Determine survivors
        n_survive = max(1, int(len(scored) * survive_pct))
        survivors = scored[:n_survive]
        eliminated = scored[n_survive:]

        # Store scores in DB
        for s in scored:
            survived = s['combo_id'] in {x['combo_id'] for x in survivors}
            conn.execute("""INSERT OR REPLACE INTO combo_scores
                (round, combo_id, combo_label, avg_sharpe, avg_wr, avg_pf, avg_maxdd,
                 avg_return, total_trades, n_symbols, survived, ts)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (round_num, s['combo_id'], s['label'], s['avg_sharpe'], s['avg_wr'],
                 s['avg_pf'], s['avg_maxdd'], s['avg_return'], s['total_trades'],
                 s['n_symbols'], 1 if survived else 0, now))
        conn.commit()

        elapsed = time.time() - t0
        print(f"\nRound {round_num} complete in {elapsed:.1f}s")
        print(f"  Tested: {len(scored)} combos with valid results")
        print(f"  Survivors ({n_survive}):   Sharpe {survivors[0]['avg_sharpe']:.3f} → {survivors[-1]['avg_sharpe']:.3f}")
        if eliminated:
            print(f"  Eliminated ({len(eliminated)}): Sharpe {eliminated[0]['avg_sharpe']:.3f} → {eliminated[-1]['avg_sharpe']:.3f}")

        surviving_combos = [s['combo_id'] for s in survivors]
        if len(surviving_combos) <= 1:
            print("\nOnly 1 combo remaining — tournament complete.")
            break

    # Final report
    print(f"\n{'='*70}")
    print("FINAL STANDINGS")
    print(f"{'='*70}")
    _print_report(conn, all_combos, min_trades)
    write_results_to_xlsx(conn, all_combos)
    conn.close()


def _print_report(conn, all_combos, min_trades):
    cur = conn.cursor()
    round_max = cur.execute("SELECT MAX(round) FROM combo_scores WHERE survived=1").fetchone()[0]
    if round_max is None:
        print("No results yet.")
        return
    rows = cur.execute("""SELECT combo_id, combo_label, avg_sharpe, avg_wr, avg_pf,
        avg_maxdd, avg_return, total_trades, n_symbols
        FROM combo_scores WHERE round=? AND survived=1
        ORDER BY avg_sharpe DESC LIMIT 30""", (round_max,)).fetchall()
    print(f"\nTop survivors after Round {round_max} (top 30 by Sharpe):")
    print(f"{'#':<4} {'TF':<14} {'K':>4} {'SK':>3} {'SD':>3} {'TP%':>5} {'EXIT':<16} {'GATE':<14} {'Sharpe':>8} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Syms':>5}")
    print("-" * 110)
    for rank, row in enumerate(rows, 1):
        cid = row[0]
        p = all_combos[cid] if cid < len(all_combos) else {}
        print(f"{rank:<4} {str(p.get('tf_combo','')):<14} {str(p.get('stoch_k','')):<4} "
              f"{str(p.get('stoch_smooth','')):<3} {str(p.get('stoch_d_smooth','')):<3} "
              f"{str(p.get('tp_pct','')):<5} {str(p.get('exit_mode','')):<16} "
              f"{str(p.get('entry_gate','')):<14} "
              f"{row[2]:>8.3f} {row[3]*100:>5.1f}% {row[4]:>6.2f} {row[5]:>5.2f}% {row[8]:>5}")


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd')
    sub.add_parser('build', help='Create tournament_config.xlsx')
    run_p = sub.add_parser('run', help='Run tournament from config')
    run_p.add_argument('--rounds', type=int, default=3)
    run_p.add_argument('--workers', type=int, default=0)
    sub.add_parser('report', help='Print current standings')
    args = parser.parse_args()
    if args.cmd == 'build':
        build_config()
    elif args.cmd == 'run':
        run_tournament(max_rounds=args.rounds, workers=args.workers)
    elif args.cmd == 'report':
        conn = sqlite3.connect(str(DB_PATH)) if DB_PATH.exists() else None
        if conn:
            all_combos, *_ = load_config()
            _print_report(conn, all_combos, 8)
        else:
            print("No DB found. Run the tournament first.")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
