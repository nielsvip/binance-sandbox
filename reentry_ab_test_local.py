#!/usr/bin/env python3
"""
Reentry A/B Test — 3 variants on qty sizing after a partial exit
A: LAST_REDUCTION — reentry qty = amount just sold
B: MAX_QUANTITY   — reentry qty = peak qty during holding period
C: START_SIZE     — reentry qty = fixed START_POSITION_SIZE / price

Primary TF: 15m  |  Multi-TF confirmation: 15m + 1h + 4h
Entry: DC breakout (price > dc_high_15m * 1.002) + 2/3 stoch aligned
Exit: Stoch cross against direction OR 500-bar timeout
Reentry trigger: price within 0.5% of exit price + stoch turning back + within 200 bars
NO_LOSS: hold reentry until breakeven or 500-bar timeout

Results saved to: backtest_framework/results/reentry_ab_test.db
"""
import json
import logging
import math
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("/home/niels/binance-sandbox/backtest_framework/results/reentry_ab_test.log", mode="w"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("reentry_ab")

KLINES_DIR = Path("/home/niels/binance-sandbox/klines_cache")
RESULTS_DIR = Path("/home/niels/binance-sandbox/backtest_framework/results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = RESULTS_DIR / "reentry_ab_test.db"

COMMISSION_RT = 0.0020  # 0.20% round-trip
START_POSITION_SIZE = 280.0  # USD notional — matches sandbox Portfolio.max_ord
DC_PERIOD = 20
STOCH_PERIOD = 14
TRAIN_RATIO = 0.70
MIN_TRADES = 50
MAX_HOLD_BARS = 500
REENTRY_WINDOW_BARS = 200
REENTRY_PRICE_TOL = 0.005  # 0.5%
SIDES = ["LONG", "SHORT"]
VARIANTS = ["LAST_REDUCTION", "MAX_QUANTITY", "START_SIZE"]


# ── klines loading ─────────────────────────────────────────────────────────────

def load_klines(symbol: str, tf: str) -> list:
    path = KLINES_DIR / f"{symbol}_{tf}.json"
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = []
        for ts, v in data.items():
            if isinstance(v, (list, tuple)) and len(v) >= 5:
                rows.append({"timestamp": ts, "open": float(v[0]), "high": float(v[1]), "low": float(v[2]), "close": float(v[3]), "volume": float(v[4])})
        rows.sort(key=lambda x: x["timestamp"])
        return rows
    return []


# ── indicator computation ──────────────────────────────────────────────────────

def compute_indicators(candles: list, dc_period: int = DC_PERIOD, stoch_period: int = STOCH_PERIOD) -> list:
    n = len(candles)
    if n < dc_period + stoch_period + 5:
        return candles
    closes = np.array([float(c["close"]) for c in candles])
    highs = np.array([float(c["high"]) for c in candles])
    lows = np.array([float(c["low"]) for c in candles])
    for i in range(dc_period, n):
        wh = highs[i - dc_period:i]
        wl = lows[i - dc_period:i]
        candles[i]["dc_high"] = float(np.max(wh))
        candles[i]["dc_low"] = float(np.min(wl))
        candles[i]["dc_basis"] = (candles[i]["dc_high"] + candles[i]["dc_low"]) / 2.0
    for i in range(stoch_period, n):
        win_lo = float(np.min(lows[i - stoch_period:i + 1]))
        win_hi = float(np.max(highs[i - stoch_period:i + 1]))
        candles[i]["k"] = float((closes[i] - win_lo) / (win_hi - win_lo) * 100) if win_hi > win_lo else 50.0
    for i in range(stoch_period + 2, n):
        candles[i]["d"] = float(np.mean([candles[j].get("k", 50.0) for j in range(i - 2, i + 1)]))
    for i in range(1, n):
        if "k" in candles[i - 1]:
            candles[i]["k_prev"] = candles[i - 1]["k"]
        if "d" in candles[i - 1]:
            candles[i]["d_prev"] = candles[i - 1]["d"]
    return candles


def build_ts_map(candles: list) -> dict:
    """Map timestamp prefix (minute resolution) → candle"""
    m = {}
    for c in candles:
        ts = c.get("timestamp", "")
        if ts:
            m[ts[:16]] = c
    return m


def get_htf_candle(ts: str, htf_map: dict) -> dict:
    """Nearest HTF candle at or before ts (within 4h window)."""
    prefix = ts[:16]
    if prefix in htf_map:
        return htf_map[prefix]
    # linear scan back up to 240 minutes
    base_date = ts[:10]
    base_hour = int(ts[11:13]) if len(ts) >= 13 else 0
    base_min = int(ts[14:16]) if len(ts) >= 16 else 0
    total_min = base_hour * 60 + base_min
    for delta in range(1, 241):
        m = total_min - delta
        if m < 0:
            break
        h, mi = divmod(m, 60)
        key = f"{base_date}T{h:02d}:{mi:02d}"
        if key in htf_map:
            return htf_map[key]
    return {}


# ── stoch helpers ──────────────────────────────────────────────────────────────

def stoch_aligned_long(c15m: dict, c1h: dict, c4h: dict) -> int:
    """Returns count of TFs where k > d (bullish stoch alignment)."""
    count = 0
    for c in (c15m, c1h, c4h):
        k = c.get("k", 50.0)
        d = c.get("d", 50.0)
        if k > d:
            count += 1
    return count


def stoch_aligned_short(c15m: dict, c1h: dict, c4h: dict) -> int:
    """Returns count of TFs where k < d (bearish stoch alignment)."""
    count = 0
    for c in (c15m, c1h, c4h):
        k = c.get("k", 50.0)
        d = c.get("d", 50.0)
        if k < d:
            count += 1
    return count


def stoch_cross_against_long(c: dict) -> bool:
    """k crossed below d (bearish cross — exit signal for LONG)."""
    k = c.get("k", 50.0)
    d = c.get("d", 50.0)
    k_prev = c.get("k_prev", k)
    d_prev = c.get("d_prev", d)
    return k_prev >= d_prev and k < d


def stoch_cross_against_short(c: dict) -> bool:
    """k crossed above d (bullish cross — exit signal for SHORT)."""
    k = c.get("k", 50.0)
    d = c.get("d", 50.0)
    k_prev = c.get("k_prev", k)
    d_prev = c.get("d_prev", d)
    return k_prev <= d_prev and k > d


def stoch_turning_long(c: dict) -> bool:
    """k crossed above d on 15m (re-entry confirmation for LONG)."""
    k = c.get("k", 50.0)
    d = c.get("d", 50.0)
    k_prev = c.get("k_prev", k)
    d_prev = c.get("d_prev", d)
    return k_prev <= d_prev and k > d


def stoch_turning_short(c: dict) -> bool:
    """k crossed below d on 15m (re-entry confirmation for SHORT)."""
    k = c.get("k", 50.0)
    d = c.get("d", 50.0)
    k_prev = c.get("k_prev", k)
    d_prev = c.get("d_prev", d)
    return k_prev >= d_prev and k < d


# ── core simulation ────────────────────────────────────────────────────────────

def simulate(candles_15m: list, candles_1h: list, candles_4h: list, side: str, start_idx: int, end_idx: int) -> dict:
    """
    Run simulation on candles_15m[start_idx:end_idx].
    Returns dict with per-variant reentry trade lists.
    """
    is_long = side == "LONG"
    map_1h = build_ts_map(candles_1h)
    map_4h = build_ts_map(candles_4h)
    window = candles_15m[start_idx:end_idx]
    n = len(window)

    # variant_trades[variant] = list of reentry trade dicts
    variant_trades = {v: [] for v in VARIANTS}

    STATE_WAIT = 0
    STATE_IN = 1
    STATE_EXITED = 2  # after gain exit, looking for reentry

    state = STATE_WAIT
    entry_price = 0.0
    entry_qty = 0.0
    max_qty = 0.0
    entry_bar = 0
    exit_price = 0.0
    exit_qty = 0.0
    last_reduction_qty = 0.0
    exit_bar = 0

    # reentry state per variant
    re_state = {v: STATE_WAIT for v in VARIANTS}
    re_entry_price = {v: 0.0 for v in VARIANTS}
    re_entry_qty = {v: 0.0 for v in VARIANTS}
    re_entry_bar = {v: 0 for v in VARIANTS}
    re_entry_cost = {v: 0.0 for v in VARIANTS}

    for i, c in enumerate(window):
        ts = c.get("timestamp", "")
        price = float(c["close"])
        dc_high = c.get("dc_high", 0.0)
        dc_low = c.get("dc_low", 0.0)
        c1h = get_htf_candle(ts, map_1h)
        c4h = get_htf_candle(ts, map_4h)

        # ── STATE_WAIT: look for entry ──────────────────────────────────────────
        if state == STATE_WAIT:
            if is_long:
                breakout = dc_high > 0 and price > dc_high * 1.002
                aligned = stoch_aligned_long(c, c1h, c4h) >= 2
                if breakout and aligned:
                    entry_price = price
                    entry_qty = START_POSITION_SIZE / price
                    max_qty = entry_qty
                    entry_bar = i
                    state = STATE_IN
            else:
                breakout = dc_low > 0 and price < dc_low * 0.998
                aligned = stoch_aligned_short(c, c1h, c4h) >= 2
                if breakout and aligned:
                    entry_price = price
                    entry_qty = START_POSITION_SIZE / price
                    max_qty = entry_qty
                    entry_bar = i
                    state = STATE_IN

        # ── STATE_IN: manage position ──────────────────────────────────────────
        elif state == STATE_IN:
            hold_bars = i - entry_bar
            if is_long:
                pnl_pct = (price - entry_price) / entry_price
                exit_signal = stoch_cross_against_long(c) or hold_bars >= MAX_HOLD_BARS
            else:
                pnl_pct = (entry_price - price) / entry_price
                exit_signal = stoch_cross_against_short(c) or hold_bars >= MAX_HOLD_BARS

            if exit_signal:
                net_pnl = pnl_pct * entry_qty * entry_price - COMMISSION_RT * entry_qty * entry_price
                if net_pnl > 0:
                    # Gain exit — record and transition to EXITED for reentry search
                    exit_price = price
                    exit_qty = entry_qty
                    last_reduction_qty = entry_qty
                    exit_bar = i
                    state = STATE_EXITED
                    # Arm all variants
                    for v in VARIANTS:
                        re_state[v] = STATE_EXITED
                        if v == "LAST_REDUCTION":
                            re_entry_qty[v] = last_reduction_qty
                        elif v == "MAX_QUANTITY":
                            re_entry_qty[v] = max_qty
                        else:  # START_SIZE
                            re_entry_qty[v] = START_POSITION_SIZE / price if price > 0 else 0.0
                        re_entry_price[v] = 0.0
                        re_entry_bar[v] = 0
                        re_entry_cost[v] = 0.0
                else:
                    # Loss exit — just reset, no reentry
                    state = STATE_WAIT
                    for v in VARIANTS:
                        re_state[v] = STATE_WAIT

        # ── STATE_EXITED: look for reentry (per variant) ──────────────────────
        if state == STATE_EXITED:
            bars_since_exit = i - exit_bar
            if bars_since_exit > REENTRY_WINDOW_BARS:
                # Window expired — reset all
                state = STATE_WAIT
                for v in VARIANTS:
                    re_state[v] = STATE_WAIT
            else:
                for v in VARIANTS:
                    if re_state[v] != STATE_EXITED:
                        continue
                    # Check reentry trigger
                    price_near_exit = abs(price - exit_price) / exit_price <= REENTRY_PRICE_TOL
                    if is_long:
                        stoch_ok = stoch_turning_long(c)
                    else:
                        stoch_ok = stoch_turning_short(c)
                    if price_near_exit and stoch_ok:
                        re_entry_price[v] = price
                        re_entry_bar[v] = i
                        re_entry_cost[v] = re_entry_qty[v] * price * (COMMISSION_RT / 2)
                        re_state[v] = STATE_IN

        # ── reentry positions: manage ──────────────────────────────────────────
        for v in VARIANTS:
            if re_state[v] != STATE_IN:
                continue
            hold_bars_re = i - re_entry_bar[v]
            ep = re_entry_price[v]
            qty = re_entry_qty[v]
            if qty <= 0 or ep <= 0:
                re_state[v] = STATE_WAIT
                continue
            if is_long:
                pnl_pct = (price - ep) / ep
            else:
                pnl_pct = (ep - price) / ep
            gross = pnl_pct * qty * ep
            net = gross - COMMISSION_RT * qty * ep
            # NO_LOSS: hold until breakeven or timeout
            at_breakeven = net >= 0
            timed_out = hold_bars_re >= MAX_HOLD_BARS
            if at_breakeven or timed_out:
                variant_trades[v].append({
                    "pnl": net,
                    "win": net > 0,
                    "hold_bars": hold_bars_re,
                    "qty": qty,
                    "entry_bar": re_entry_bar[v],
                })
                re_state[v] = STATE_WAIT
                # If all variants resolved and main state can reset
                if all(re_state[vv] == STATE_WAIT for vv in VARIANTS):
                    state = STATE_WAIT

    return variant_trades


# ── metrics ────────────────────────────────────────────────────────────────────

def compute_metrics(trades: list) -> dict:
    if not trades:
        return {"sharpe": 0.0, "trades": 0, "total_pnl": 0.0, "win_rate": 0.0, "avg_pnl": 0.0, "avg_qty": 0.0, "avg_hold_bars": 0.0}
    pnls = [t["pnl"] for t in trades]
    wins = [t["win"] for t in trades]
    qtys = [t["qty"] for t in trades]
    holds = [t["hold_bars"] for t in trades]
    total = sum(pnls)
    wr = sum(wins) / len(wins)
    avg_pnl = total / len(pnls)
    avg_qty = sum(qtys) / len(qtys)
    avg_hold = sum(holds) / len(holds)
    std = float(np.std(pnls)) if len(pnls) > 1 else 0.0
    sharpe = (avg_pnl / std * math.sqrt(252)) if std > 0 else 0.0
    return {"sharpe": round(sharpe, 4), "trades": len(trades), "total_pnl": round(total, 4), "win_rate": round(wr, 4), "avg_pnl": round(avg_pnl, 4), "avg_qty": round(avg_qty, 6), "avg_hold_bars": round(avg_hold, 2)}


# ── top 50 symbols by 15m file size ───────────────────────────────────────────

def get_top_symbols(n: int = 50) -> list:
    files = list(KLINES_DIR.glob("*_15m.json"))
    sized = sorted(files, key=lambda p: p.stat().st_size, reverse=True)
    symbols = []
    for p in sized:
        sym = p.name.replace("_15m.json", "")
        if (KLINES_DIR / f"{sym}_1h.json").exists() and (KLINES_DIR / f"{sym}_4h.json").exists():
            symbols.append(sym)
        if len(symbols) >= n:
            break
    return symbols


# ── db init ────────────────────────────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reentry_ab_results (
            symbol TEXT,
            side TEXT,
            variant TEXT,
            train_sharpe REAL,
            train_trades INTEGER,
            train_total_pnl REAL,
            train_wr REAL,
            test_sharpe REAL,
            test_trades INTEGER,
            test_total_pnl REAL,
            test_wr REAL,
            avg_reentry_qty REAL,
            avg_hold_bars REAL,
            PRIMARY KEY (symbol, side, variant)
        )
    """)
    conn.commit()
    return conn


def upsert_row(conn: sqlite3.Connection, symbol: str, side: str, variant: str, train: dict, test: dict) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO reentry_ab_results VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (symbol, side, variant, train["sharpe"], train["trades"], train["total_pnl"], train["win_rate"],
          test["sharpe"], test["trades"], test["total_pnl"], test["win_rate"],
          (train["avg_qty"] + test["avg_qty"]) / 2.0,
          (train["avg_hold_bars"] + test["avg_hold_bars"]) / 2.0))
    conn.commit()


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    logger.info("=== Reentry A/B Test starting ===")
    logger.info(f"DB: {DB_PATH}")
    symbols = get_top_symbols(50)
    logger.info(f"Top {len(symbols)} symbols: {symbols[:10]} ...")
    conn = init_db(DB_PATH)
    total_processed = 0
    summary = {v: {"train_pnl": 0.0, "test_pnl": 0.0, "train_trades": 0, "test_trades": 0} for v in VARIANTS}

    for sym_idx, symbol in enumerate(symbols):
        logger.info(f"[{sym_idx+1}/{len(symbols)}] {symbol}")
        c15m = compute_indicators(load_klines(symbol, "15m"))
        c1h = compute_indicators(load_klines(symbol, "1h"))
        c4h = compute_indicators(load_klines(symbol, "4h"))
        if len(c15m) < 200:
            logger.warning(f"  {symbol}: too few 15m bars ({len(c15m)}), skipping")
            continue
        split = int(len(c15m) * TRAIN_RATIO)
        for side in SIDES:
            train_trades_all = simulate(c15m, c1h, c4h, side, 0, split)
            test_trades_all = simulate(c15m, c1h, c4h, side, split, len(c15m))
            any_sufficient = False
            for v in VARIANTS:
                t_tr = train_trades_all[v]
                t_te = test_trades_all[v]
                if len(t_tr) < 1 and len(t_te) < 1:
                    continue
                m_tr = compute_metrics(t_tr)
                m_te = compute_metrics(t_te)
                upsert_row(conn, symbol, side, v, m_tr, m_te)
                summary[v]["train_pnl"] += m_tr["total_pnl"]
                summary[v]["test_pnl"] += m_te["total_pnl"]
                summary[v]["train_trades"] += m_tr["trades"]
                summary[v]["test_trades"] += m_te["trades"]
                any_sufficient = True
            if any_sufficient:
                total_processed += 1
        if (sym_idx + 1) % 10 == 0:
            logger.info("--- Interim summary ---")
            for v in VARIANTS:
                logger.info(f"  {v}: train_pnl={summary[v]['train_pnl']:.2f} test_pnl={summary[v]['test_pnl']:.2f} train_trades={summary[v]['train_trades']} test_trades={summary[v]['test_trades']}")

    logger.info("=== FINAL RESULTS ===")
    for v in VARIANTS:
        logger.info(f"{v}: train_pnl={summary[v]['train_pnl']:.4f} test_pnl={summary[v]['test_pnl']:.4f} train_trades={summary[v]['train_trades']} test_trades={summary[v]['test_trades']}")

    # Per-variant aggregate from DB
    logger.info("--- DB aggregates (test set, all symbols) ---")
    for v in VARIANTS:
        rows = conn.execute("SELECT COUNT(*), SUM(test_total_pnl), AVG(test_wr), AVG(test_sharpe), SUM(test_trades) FROM reentry_ab_results WHERE variant=? AND test_trades>=1", (v,)).fetchone()
        logger.info(f"  {v}: symbols={rows[0]} total_pnl={rows[1]:.4f if rows[1] else 0:.4f} avg_wr={rows[2]:.4f if rows[2] else 0:.4f} avg_sharpe={rows[3]:.4f if rows[3] else 0:.4f} total_trades={rows[4]}")

    # Filter to MIN_TRADES symbols
    logger.info(f"--- Filtered (test_trades >= {MIN_TRADES}) ---")
    for v in VARIANTS:
        rows = conn.execute("SELECT COUNT(*), SUM(test_total_pnl), AVG(test_wr), AVG(test_sharpe), SUM(test_trades) FROM reentry_ab_results WHERE variant=? AND test_trades>=?", (v, MIN_TRADES)).fetchone()
        logger.info(f"  {v}: qualifying_symbols={rows[0]} total_pnl={rows[1]:.4f if rows[1] else 0:.4f} avg_wr={rows[2]:.4f if rows[2] else 0:.4f} avg_sharpe={rows[3]:.4f if rows[3] else 0:.4f}")

    conn.close()
    logger.info(f"Done. Results in {DB_PATH}")


if __name__ == "__main__":
    main()
