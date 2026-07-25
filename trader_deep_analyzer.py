#!/usr/bin/env python3
"""Deep analysis of copy trading master traders: winner vs loser indicators, pattern extraction, blowup detection, regime correlation."""
import argparse
import json
import logging
import math
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config
from ez_indicators import (
    adx_value,
    atr_series,
    bb_features,
    choppiness_index,
    donchian,
    ema_pair,
    ha_streak_count,
    heikin_ashi,
    macd_values,
    mfi_value,
    relative_volume,
    rsi_series,
    rsi_value,
    sma_pair,
    stoch_rsi,
)

config = Config()
BASE_PATH = config.BASE_PATH
KLINES_DIR = config.KLINES_CACHE_DIR
OUTPUT_DIR = config.DATA_DIR / "trader_analysis"

logger = logging.getLogger("trader_deep_analyzer")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

SYMBOL_MAP_CACHE: Dict[str, Optional[str]] = {}
KLINE_DF_CACHE: Dict[str, Optional[pd.DataFrame]] = {}
MIN_KLINE_BARS = 210


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — DATA INGESTION
# ═══════════════════════════════════════════════════════════════════

class TradeRecord:
    __slots__ = ["trader_id", "symbol", "side", "entry_price", "exit_price", "entry_time", "exit_time", "pnl", "pnl_pct", "leverage", "position_size_usd", "indicators"]
    def __init__(self, trader_id: str, symbol: str, side: str, entry_price: float, exit_price: float, entry_time: datetime, exit_time: datetime, pnl: float, pnl_pct: float, leverage: float = 1.0, position_size_usd: float = 0.0):
        normalized_symbol = str(symbol or "").strip().upper()
        normalized_side = str(side or "").strip().upper()
        if not normalized_symbol:
            raise ValueError("trade symbol is required")
        if normalized_side not in ("LONG", "SHORT"):
            raise ValueError(f"invalid position_side={side!r}; expected LONG or SHORT")
        self.trader_id = str(trader_id or "unknown")
        self.symbol = normalized_symbol
        self.side = normalized_side
        self.entry_price = entry_price
        self.exit_price = exit_price
        self.entry_time = entry_time
        self.exit_time = exit_time
        self.pnl = pnl
        self.pnl_pct = pnl_pct
        self.leverage = leverage
        self.position_size_usd = position_size_usd
        self.indicators: Dict[str, Any] = {}

    def hold_hours(self) -> float:
        if self.entry_time and self.exit_time:
            return max(0.0, (self.exit_time - self.entry_time).total_seconds() / 3600.0)
        return 0.0

    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def position_side(self) -> str:
        """Canonical position direction; never infer this from an order side."""
        return self.side

    def to_dict(self) -> dict:
        return {
            "source_account": self.trader_id,
            "trader_id": self.trader_id,
            "symbol": self.symbol,
            "position_side": self.position_side,
            "entry_order_side": "BUY" if self.position_side == "LONG" else "SELL",
            "exit_order_side": "SELL" if self.position_side == "LONG" else "BUY",
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "entry_time": str(self.entry_time),
            "exit_time": str(self.exit_time),
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "pnl_formula": (
                "(exit-entry)/entry*100"
                if self.position_side == "LONG"
                else "(entry-exit)/entry*100"
            ),
            "leverage": self.leverage,
            "position_size_usd": self.position_size_usd,
            "hold_hours": self.hold_hours(),
        }


class XLSXIngester:
    """Parse Finandy-style XLSX with trader sheets."""
    def ingest(self, path: str) -> List[TradeRecord]:
        trades = []
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            df_raw = xl.parse(sheet, header=None)
            trader_id = sheet.strip()
            # Finandy format: trade data starts after "TRADE HISTORY" row, next row is header
            header_row = None
            for idx in range(len(df_raw)):
                val = str(df_raw.iloc[idx, 0]).strip().upper()
                if val in ("TRADE HISTORY", "POSITION ID"):
                    header_row = idx if val == "POSITION ID" else idx + 1
                    break
            if header_row is not None and header_row < len(df_raw):
                df = df_raw.iloc[header_row:].reset_index(drop=True)
                df.columns = [str(c).strip().lower().replace(" ", "_").replace("(", "").replace(")", "") for c in df.iloc[0]]
                df = df.iloc[1:].reset_index(drop=True)
            else:
                df = xl.parse(sheet)
                df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
            col_map = self._detect_columns(df)
            if not col_map:
                logger.warning(f"Sheet '{sheet}' — could not detect required columns, skipping")
                continue
            for _, row in df.iterrows():
                try:
                    tr = self._parse_row(row, col_map, trader_id)
                    if tr:
                        trades.append(tr)
                except Exception:
                    continue
        logger.info(f"XLSX ingested: {len(trades)} trades from {path}")
        return trades

    def _detect_columns(self, df: pd.DataFrame) -> Optional[Dict[str, str]]:
        cols = set(df.columns)
        mapping = {}
        for target, candidates in [("symbol", ["symbol", "pair", "coin", "market", "instrument"]), ("side", ["side", "direction", "type", "long/short", "position_side"]), ("entry_price", ["entry_price", "open_price", "entry", "avg_entry", "avg_entry_price"]), ("exit_price", ["exit_price", "close_price", "exit", "avg_exit", "avg_exit_price"]), ("entry_time", ["entry_time", "open_time", "opened", "open_date", "entry_date", "entry_datetime_utc"]), ("exit_time", ["exit_time", "close_time", "closed", "close_date", "exit_date", "exit_datetime_utc"]), ("pnl", ["pnl", "profit", "realized_pnl", "net_profit", "p&l", "pnl_usd"]), ("pnl_pct", ["pnl_pct", "pnl_%", "profit_%", "roi", "return_%", "pnl_percent", "pnl_%"]), ("leverage", ["leverage", "lev"]), ("position_size_usd", ["position_size_usd", "size", "notional", "volume_usd", "size_usd", "amount_usd", "volume_usd"])]:
            for c in candidates:
                if c in cols:
                    mapping[target] = c
                    break
        required = {"symbol", "side"}
        if not required.issubset(set(mapping.keys())) or not ("pnl" in mapping or "pnl_pct" in mapping):
            return None
        return mapping

    def _parse_row(self, row: pd.Series, col_map: Dict[str, str], trader_id: str) -> Optional[TradeRecord]:
        symbol = str(row[col_map["symbol"]]).strip()
        if not symbol or symbol == "nan":
            return None
        side = str(row[col_map["side"]]).strip().upper()
        if side not in ("LONG", "SHORT", "BUY", "SELL"):
            return None
        if side == "BUY":
            side = "LONG"
        elif side == "SELL":
            side = "SHORT"
        entry_price = self._parse_num(row.get(col_map.get("entry_price", ""), 0))
        exit_price = self._parse_num(row.get(col_map.get("exit_price", ""), 0))
        if (
            ("entry_price" in col_map and entry_price <= 0)
            or ("exit_price" in col_map and exit_price <= 0)
        ):
            return None
        pnl = self._parse_num(row.get(col_map.get("pnl", ""), 0))
        pnl_pct = self._parse_num(row.get(col_map.get("pnl_pct", ""), 0))
        if pnl_pct == 0 and entry_price > 0 and exit_price > 0:
            if side == "LONG":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0
            else:
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100.0
        _lev_raw = str(row.get(col_map.get("leverage", ""), "1") or "1").strip().lower().replace("x", "").replace(",", "")
        leverage = float(_lev_raw) if _lev_raw and _lev_raw != "nan" else 1.0
        size_usd = self._parse_num(row.get(col_map.get("position_size_usd", ""), 0))
        entry_time = self._parse_time(row.get(col_map.get("entry_time", ""), None))
        exit_time = self._parse_time(row.get(col_map.get("exit_time", ""), None))
        return TradeRecord(trader_id, symbol, side, entry_price, exit_price, entry_time, exit_time, pnl, pnl_pct, leverage, size_usd)

    def _parse_num(self, val) -> float:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip().replace(",", "").replace(" ", "").replace("%", "").replace("+", "").replace("−", "-").replace("–", "-")
        if not s or s == "nan" or s == "(still" or "open" in s.lower():
            return 0.0
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _parse_time(self, val) -> Optional[datetime]:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return None
        if isinstance(val, datetime):
            return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
        s = str(val).strip()
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d", "%H:%M:%S %d.%m.%Y UTC", "%H:%M:%S %d.%m.%Y"]:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            return pd.to_datetime(s, utc=True).to_pydatetime()
        except Exception:
            return None


class CSVIngester:
    """Parse CSV with standard columns."""
    def ingest(self, path: str) -> List[TradeRecord]:
        df = pd.read_csv(path)
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
        xlsx_ing = XLSXIngester()
        col_map = xlsx_ing._detect_columns(df)
        if not col_map:
            logger.error(f"CSV {path} — could not detect required columns")
            return []
        trades = []
        for _, row in df.iterrows():
            try:
                tr = xlsx_ing._parse_row(row, col_map, row.get("trader_id", "unknown"))
                if tr:
                    trades.append(tr)
            except Exception:
                continue
        logger.info(f"CSV ingested: {len(trades)} trades from {path}")
        return trades


class TrackerLogIngester:
    """Parse hgnx-style JSON tracker logs."""
    def ingest(self, path: str) -> List[TradeRecord]:
        trades = []
        p = Path(path)
        files = list(p.glob("*.json")) if p.is_dir() else [p]
        for f in files:
            try:
                with open(f) as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    for entry in data:
                        tr = self._parse_entry(entry)
                        if tr:
                            trades.append(tr)
                elif isinstance(data, dict):
                    for trader_id, trader_trades in data.items():
                        if isinstance(trader_trades, list):
                            for entry in trader_trades:
                                entry.setdefault("trader_id", trader_id)
                                tr = self._parse_entry(entry)
                                if tr:
                                    trades.append(tr)
            except Exception as e:
                logger.warning(f"Failed to parse {f}: {e}")
        logger.info(f"Tracker logs ingested: {len(trades)} trades from {path}")
        return trades

    def _parse_entry(self, entry: dict) -> Optional[TradeRecord]:
        symbol = entry.get("symbol", entry.get("pair", ""))
        if not symbol:
            return None
        side = str(entry.get("position_side", entry.get("side", entry.get("direction", "")))).upper()
        if side in ("BUY", "LONG"):
            side = "LONG"
        elif side in ("SELL", "SHORT"):
            side = "SHORT"
        else:
            logger.warning(
                "Tracker entry rejected: missing/unknown position_side=%r symbol=%r",
                side,
                symbol,
            )
            return None
        entry_price = float(entry.get("entry_price", entry.get("entryPrice", 0)))
        exit_price = float(entry.get("exit_price", entry.get("exitPrice", 0)))
        pnl = float(entry.get("pnl", entry.get("profit", 0)))
        pnl_pct = float(entry.get("pnl_pct", entry.get("roe", entry.get("roi", 0))))
        leverage = float(entry.get("leverage", 1))
        size_usd = float(entry.get("position_size_usd", entry.get("size", entry.get("notional", 0))))
        trader_id = str(entry.get("trader_id", entry.get("traderId", "unknown")))
        xlsx_ing = XLSXIngester()
        entry_time = xlsx_ing._parse_time(entry.get("entry_time", entry.get("entryTime", entry.get("openTime"))))
        exit_time = xlsx_ing._parse_time(entry.get("exit_time", entry.get("exitTime", entry.get("closeTime"))))
        return TradeRecord(trader_id, symbol, side, entry_price, exit_price, entry_time, exit_time, pnl, pnl_pct, leverage, size_usd)


class BitgetIngester:
    """Stub for Bitget API — not implemented yet."""
    def ingest(self, api_key: str = "", api_secret: str = "", passphrase: str = "") -> List[TradeRecord]:
        logger.warning("BitgetIngester is a stub — not implemented yet. Provide XLSX/CSV data instead.")
        return []


def ingest_trades(input_path: str) -> List[TradeRecord]:
    p = Path(input_path)
    if not p.exists():
        logger.error(f"Input path does not exist: {input_path}")
        return []
    if p.suffix.lower() in (".xlsx", ".xls"):
        return XLSXIngester().ingest(input_path)
    elif p.suffix.lower() == ".csv":
        return CSVIngester().ingest(input_path)
    elif p.is_dir() or p.suffix.lower() == ".json":
        return TrackerLogIngester().ingest(input_path)
    else:
        logger.error(f"Unknown file type: {p.suffix}")
        return []


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — KLINE LOADING + INDICATOR OVERLAY
# ═══════════════════════════════════════════════════════════════════

def find_kline_symbol(raw_symbol: str) -> Optional[str]:
    if raw_symbol in SYMBOL_MAP_CACHE:
        return SYMBOL_MAP_CACHE[raw_symbol]
    base = raw_symbol.replace("/", "").replace("-", "").upper()
    for candidate in [base, base.replace("USDT", "USDC"), base + "USDT", base + "USDC"]:
        if (KLINES_DIR / f"{candidate}_1h.json").exists():
            SYMBOL_MAP_CACHE[raw_symbol] = candidate
            return candidate
    for candidate in [base, base.replace("USDT", "USDC")]:
        if (KLINES_DIR / f"{candidate}_4h.json").exists():
            SYMBOL_MAP_CACHE[raw_symbol] = candidate
            return candidate
    SYMBOL_MAP_CACHE[raw_symbol] = None
    return None


def load_klines(symbol: str, timeframe: str = "1h") -> Optional[pd.DataFrame]:
    cache_key = f"{symbol}_{timeframe}"
    if cache_key in KLINE_DF_CACHE:
        return KLINE_DF_CACHE[cache_key]
    path = KLINES_DIR / f"{symbol}_{timeframe}.json"
    if not path.exists():
        KLINE_DF_CACHE[cache_key] = None
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        if df.empty:
            KLINE_DF_CACHE[cache_key] = None
            return None
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.sort_values("timestamp").reset_index(drop=True)
        KLINE_DF_CACHE[cache_key] = df
        return df
    except Exception as e:
        logger.warning(f"Failed to load klines {path}: {e}")
        KLINE_DF_CACHE[cache_key] = None
        return None


def get_klines_at_time(symbol: str, timestamp: datetime, timeframe: str = "1h", lookback: int = 250) -> Optional[pd.DataFrame]:
    """Get klines up to and including the bar containing `timestamp`."""
    df = load_klines(symbol, timeframe)
    if df is None or df.empty:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    mask = df["timestamp"] <= timestamp
    subset = df[mask]
    if len(subset) < MIN_KLINE_BARS:
        return None
    return subset.iloc[-lookback:].copy().reset_index(drop=True)


def compute_all_indicators(df: pd.DataFrame, price: float) -> Dict[str, Any]:
    """Compute ALL indicators at the last bar of df. Returns flat dict."""
    ind: Dict[str, Any] = {}
    if df is None or len(df) < MIN_KLINE_BARS:
        return ind
    close = df["close"]
    high = df["high"]
    low = df["low"]
    rsi14 = rsi_value(close, 14)
    ind["rsi_14"] = rsi14
    rsi2 = rsi_value(close, 2)
    ind["rsi_2"] = rsi2
    stoch = stoch_rsi(close, 14, 5, 5)
    if stoch is not None and not stoch.empty:
        k_val = stoch["k"].iloc[-1]
        d_val = stoch["d"].iloc[-1]
        ind["stoch_k"] = float(k_val) if pd.notna(k_val) else None
        ind["stoch_d"] = float(d_val) if pd.notna(d_val) else None
    else:
        ind["stoch_k"] = None
        ind["stoch_d"] = None
    for length in [9, 14, 20]:
        curr, prev = ema_pair(close, length)
        ind[f"ema_{length}"] = curr
        ind[f"ema_{length}_prev"] = prev
    sma200_curr, sma200_prev = sma_pair(close, 200)
    ind["sma_200"] = sma200_curr
    ind["sma_200_prev"] = sma200_prev
    if sma200_curr and sma200_curr > 0 and price > 0:
        ind["pct_from_sma200"] = ((price - sma200_curr) / sma200_curr) * 100.0
        ind["above_sma200"] = 1 if price > sma200_curr else 0
    else:
        ind["pct_from_sma200"] = None
        ind["above_sma200"] = None
    atr = atr_series(df, 14)
    if atr is not None and not atr.empty:
        atr_val = atr.iloc[-1]
        ind["atr_14"] = float(atr_val) if pd.notna(atr_val) else None
        if price > 0 and pd.notna(atr_val):
            ind["atr_pct"] = (float(atr_val) / price) * 100.0
        else:
            ind["atr_pct"] = None
    else:
        ind["atr_14"] = None
        ind["atr_pct"] = None
    macd_line, signal, hist, crossover, crossunder = macd_values(close)
    ind["macd_line"] = macd_line
    ind["macd_signal"] = signal
    ind["macd_hist"] = hist
    ind["macd_crossover"] = 1 if crossover else 0
    ind["macd_crossunder"] = 1 if crossunder else 0
    adx = adx_value(df, 14)
    ind["adx_14"] = adx
    bb_upper, bb_lower, bb_pct_b = bb_features(close, 20, 2.0)
    ind["bb_upper"] = bb_upper
    ind["bb_lower"] = bb_lower
    ind["bb_pct_b"] = bb_pct_b
    if bb_upper is not None and bb_lower is not None and price > 0:
        ind["bb_width"] = ((bb_upper - bb_lower) / price) * 100.0
    else:
        ind["bb_width"] = None
    dc_high, dc_low, dc_basis = donchian(high, low, 20)
    ind["dc_high"] = dc_high
    ind["dc_low"] = dc_low
    ind["dc_basis"] = dc_basis
    if dc_high is not None and dc_low is not None and price > 0:
        ind["dc_width"] = ((dc_high - dc_low) / price) * 100.0
    else:
        ind["dc_width"] = None
    ha_streak = ha_streak_count(df)
    ind["ha_streak"] = ha_streak
    ha_color, ha_prev_color = heikin_ashi(df)
    ind["ha_color"] = 1 if ha_color == "green" else -1
    ind["ha_prev_color"] = (1 if ha_prev_color == "green" else -1) if ha_prev_color else 0
    chop = choppiness_index(df, 14)
    ind["choppiness"] = chop
    rvol = relative_volume(df, 20)
    ind["rvol"] = rvol
    mfi = mfi_value(df)
    ind["mfi"] = mfi
    if price > 0:
        ema9 = ind.get("ema_9")
        ema20 = ind.get("ema_20")
        if ema9 and ema20:
            ind["ema_9_20_spread"] = ((ema9 - ema20) / price) * 100.0
        else:
            ind["ema_9_20_spread"] = None
    return ind


def overlay_indicators(trades: List[TradeRecord]) -> int:
    """Compute indicators for each trade at entry time. Returns count of successfully overlaid trades."""
    success = 0
    total = len(trades)
    for i, trade in enumerate(trades):
        if not trade.entry_time:
            continue
        kline_sym = find_kline_symbol(trade.symbol)
        if not kline_sym:
            continue
        for tf in ["1h", "4h"]:
            df = get_klines_at_time(kline_sym, trade.entry_time, tf)
            if df is None:
                continue
            suffix = f"_{tf}" if tf != "1h" else ""
            indicators = compute_all_indicators(df, trade.entry_price)
            for k, v in indicators.items():
                trade.indicators[f"{k}{suffix}"] = v
        if trade.indicators:
            success += 1
        if (i + 1) % 100 == 0:
            logger.info(f"Indicator overlay progress: {i+1}/{total}")
    logger.info(f"Indicator overlay complete: {success}/{total} trades enriched")
    return success


# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — WINNER vs LOSER ANALYSIS
# ═══════════════════════════════════════════════════════════════════

def winner_loser_analysis(trades: List[TradeRecord]) -> Dict[str, Any]:
    """Statistical comparison of indicator values for winners vs losers."""
    from scipy.stats import ttest_ind
    winners = [t for t in trades if t.is_winner() and t.indicators]
    losers = [t for t in trades if not t.is_winner() and t.indicators]
    if len(winners) < 5 or len(losers) < 5:
        logger.warning(f"Not enough trades with indicators for W/L analysis (W={len(winners)}, L={len(losers)})")
        return {}
    all_keys = set()
    for t in winners + losers:
        all_keys.update(t.indicators.keys())
    numeric_keys = set()
    for k in all_keys:
        sample_vals = [t.indicators.get(k) for t in (winners + losers)[:50] if t.indicators.get(k) is not None]
        if sample_vals and all(isinstance(v, (int, float)) for v in sample_vals):
            numeric_keys.add(k)
    results = {}
    for key in sorted(numeric_keys):
        w_vals = [t.indicators[key] for t in winners if key in t.indicators and t.indicators[key] is not None and isinstance(t.indicators[key], (int, float))]
        l_vals = [t.indicators[key] for t in losers if key in t.indicators and t.indicators[key] is not None and isinstance(t.indicators[key], (int, float))]
        if len(w_vals) < 5 or len(l_vals) < 5:
            continue
        w_mean = np.mean(w_vals)
        l_mean = np.mean(l_vals)
        w_median = np.median(w_vals)
        l_median = np.median(l_vals)
        w_std = np.std(w_vals, ddof=1) if len(w_vals) > 1 else 0.001
        l_std = np.std(l_vals, ddof=1) if len(l_vals) > 1 else 0.001
        pooled_std = math.sqrt(((len(w_vals) - 1) * w_std**2 + (len(l_vals) - 1) * l_std**2) / (len(w_vals) + len(l_vals) - 2))
        cohens_d = (w_mean - l_mean) / pooled_std if pooled_std > 0 else 0.0
        try:
            t_stat, p_value = ttest_ind(w_vals, l_vals, equal_var=False)
        except Exception:
            p_value = 1.0
            t_stat = 0.0
        results[key] = {"winner_mean": round(w_mean, 4), "loser_mean": round(l_mean, 4), "winner_median": round(w_median, 4), "loser_median": round(l_median, 4), "cohens_d": round(cohens_d, 4), "p_value": round(p_value, 6), "t_stat": round(t_stat, 4), "n_winners": len(w_vals), "n_losers": len(l_vals), "discriminative_power": round(abs(cohens_d) * (1 - min(p_value, 1.0)), 4)}
    sorted_results = dict(sorted(results.items(), key=lambda x: x[1]["discriminative_power"], reverse=True))
    return sorted_results


def print_top_indicators(analysis: Dict[str, Any], top_n: int = 15):
    if not analysis:
        print("\n  No indicator analysis available.")
        return
    print(f"\n{'='*100}")
    print(f"  TOP {top_n} DISCRIMINATIVE INDICATORS (Winners vs Losers)")
    print(f"{'='*100}")
    print(f"  {'Indicator':<30} {'W Mean':>10} {'L Mean':>10} {'Cohen d':>10} {'p-value':>10} {'Power':>10} {'n_W':>6} {'n_L':>6}")
    print(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*6} {'-'*6}")
    for i, (key, vals) in enumerate(analysis.items()):
        if i >= top_n:
            break
        sig = "***" if vals["p_value"] < 0.001 else "**" if vals["p_value"] < 0.01 else "*" if vals["p_value"] < 0.05 else ""
        print(f"  {key:<30} {vals['winner_mean']:>10.3f} {vals['loser_mean']:>10.3f} {vals['cohens_d']:>+10.3f} {vals['p_value']:>9.4f}{sig:1s} {vals['discriminative_power']:>10.4f} {vals['n_winners']:>6} {vals['n_losers']:>6}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 4 — PATTERN EXTRACTION (Decision Trees)
# ═══════════════════════════════════════════════════════════════════

def extract_patterns(trades: List[TradeRecord], min_samples_leaf: int = 20) -> List[Dict[str, Any]]:
    """Find rule-based patterns using one independent tree per position side.

    A pooled LONG/SHORT tree is not meaningful because the same indicator
    threshold can have opposite directional meaning.  Each returned leaf is
    therefore side-pure by construction; ``dominant_side`` is retained only
    for compatibility with older report readers.
    """
    patterns: List[Dict[str, Any]] = []
    for position_side in ("LONG", "SHORT"):
        side_patterns = _extract_patterns_one_side(
            [t for t in trades if t.position_side == position_side],
            position_side,
            min_samples_leaf,
        )
        patterns.extend(side_patterns)
    patterns.sort(key=lambda p: (p["win_rate"], p["n_trades"]), reverse=True)
    logger.info(
        "Pattern extraction: %d side-pure high-WR patterns found",
        len(patterns),
    )
    return patterns


def _extract_patterns_one_side(
    trades: List[TradeRecord],
    position_side: str,
    min_samples_leaf: int,
) -> List[Dict[str, Any]]:
    """Train one decision tree for one canonical LONG or SHORT population."""
    from sklearn.tree import DecisionTreeClassifier
    enriched = [t for t in trades if len(t.indicators) >= 5]
    if len(enriched) < 50:
        logger.warning(
            "Not enough enriched %s trades for pattern extraction (%d)",
            position_side,
            len(enriched),
        )
        return []
    all_keys = set()
    for t in enriched:
        for k, v in t.indicators.items():
            if isinstance(v, (int, float)):
                all_keys.add(k)
    feature_cols = sorted(all_keys)
    rows = []
    labels = []
    for t in enriched:
        row = {}
        skip = False
        for k in feature_cols:
            val = t.indicators.get(k)
            if val is None or not isinstance(val, (int, float)):
                row[k] = np.nan
            else:
                row[k] = float(val)
        rows.append(row)
        labels.append(1 if t.is_winner() else 0)
    X = pd.DataFrame(rows, columns=feature_cols)
    y = np.array(labels)
    X = X.fillna(X.median())
    valid_cols = X.columns[X.std() > 1e-10].tolist()
    if len(valid_cols) < 3:
        logger.warning("Not enough valid feature columns for decision tree")
        return []
    X = X[valid_cols]
    tree = DecisionTreeClassifier(max_depth=4, min_samples_leaf=min_samples_leaf, min_samples_split=min_samples_leaf * 2, class_weight="balanced", random_state=42)
    tree.fit(X, y)
    patterns = _extract_tree_rules(tree, valid_cols, X, y, enriched)
    patterns = [p for p in patterns if p["win_rate"] >= 0.70 and p["n_trades"] >= min_samples_leaf]
    for pattern in patterns:
        pattern.update(
            {
                "position_side": position_side,
                "dominant_side": position_side,
                "side_pure": True,
                "symbol_scope": "ALL_SYMBOLS",
            }
        )
    return patterns


def _extract_tree_rules(tree, feature_names: List[str], X: pd.DataFrame, y: np.ndarray, trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Walk decision tree leaves and extract human-readable rules."""
    from sklearn.tree import _tree
    tree_ = tree.tree_
    patterns = []
    def recurse(node_id, rules):
        if tree_.feature[node_id] == _tree.TREE_UNDEFINED:
            leaf_mask = tree.apply(X) == node_id
            n_total = int(leaf_mask.sum())
            if n_total < 10:
                return
            n_win = int(y[leaf_mask].sum())
            wr = n_win / n_total if n_total > 0 else 0
            leaf_trades = [trades[i] for i in range(min(len(trades), len(leaf_mask))) if leaf_mask[i]]
            sides = {}
            for lt in leaf_trades:
                sides[lt.side] = sides.get(lt.side, 0) + 1
            dominant_side = max(sides, key=sides.get) if sides else "UNKNOWN"
            avg_pnl_pct = np.mean([t.pnl_pct for t in leaf_trades]) if leaf_trades else 0
            rule_str = " AND ".join(rules)
            patterns.append(
                {
                    "rules": rules.copy(),
                    "rule_str": rule_str,
                    "win_rate": round(wr, 4),
                    "n_trades": n_total,
                    "n_winners": n_win,
                    "dominant_side": dominant_side,
                    "side_counts": sides,
                    "avg_pnl_pct": round(avg_pnl_pct, 4),
                    "prediction": "WIN" if wr >= 0.5 else "LOSE",
                }
            )
            return
        feat = feature_names[tree_.feature[node_id]]
        thresh = round(tree_.threshold[node_id], 4)
        rules.append(f"{feat} <= {thresh}")
        recurse(tree_.children_left[node_id], rules)
        rules.pop()
        rules.append(f"{feat} > {thresh}")
        recurse(tree_.children_right[node_id], rules)
        rules.pop()
    recurse(0, [])
    return patterns


def print_patterns(patterns: List[Dict[str, Any]], top_n: int = 15):
    if not patterns:
        print("\n  No high-WR patterns found.")
        return
    print(f"\n{'='*100}")
    print(f"  EXTRACTED ENTRY PATTERNS (>70% WR, min 20 trades)")
    print(f"{'='*100}")
    for i, p in enumerate(patterns[:top_n]):
        print(f"\n  Pattern #{i+1}: WR={p['win_rate']*100:.1f}% (n={p['n_trades']}, winners={p['n_winners']}) | Side={p['dominant_side']} | Avg PnL%={p['avg_pnl_pct']:.2f}%")
        print(f"    → {p['rule_str']}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 5 — BLOWUP DETECTION
# ═══════════════════════════════════════════════════════════════════

def compute_trader_health(trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    """Analyze each trader for blowup warning signs. Returns health score per trader."""
    traders: Dict[str, List[TradeRecord]] = {}
    for t in trades:
        traders.setdefault(t.trader_id, []).append(t)
    health_scores = {}
    for trader_id, ttrades in traders.items():
        ttrades.sort(key=lambda t: t.entry_time or datetime.min.replace(tzinfo=timezone.utc))
        if len(ttrades) < 5:
            continue
        score = 100.0
        warnings_list = []
        recent_n = min(10, len(ttrades))
        prev_n = max(0, len(ttrades) - recent_n)
        recent = ttrades[-recent_n:]
        previous = ttrades[:prev_n] if prev_n > 0 else ttrades[:len(ttrades)//2]
        if not previous:
            previous = recent
        # 1. Leverage creep
        recent_lev = np.mean([t.leverage for t in recent if t.leverage > 0])
        prev_lev = np.mean([t.leverage for t in previous if t.leverage > 0]) if previous else recent_lev
        if prev_lev > 0 and recent_lev > prev_lev * 1.3:
            penalty = min(20, (recent_lev / prev_lev - 1) * 30)
            score -= penalty
            warnings_list.append(f"LEVERAGE_CREEP: avg {prev_lev:.1f}x → {recent_lev:.1f}x (+{((recent_lev/prev_lev)-1)*100:.0f}%)")
        # 2. Position size inflation
        recent_size = np.mean([t.position_size_usd for t in recent if t.position_size_usd > 0])
        prev_size = np.mean([t.position_size_usd for t in previous if t.position_size_usd > 0]) if previous else recent_size
        if prev_size > 0 and recent_size > prev_size * 1.5:
            penalty = min(15, (recent_size / prev_size - 1) * 20)
            score -= penalty
            warnings_list.append(f"SIZE_INFLATION: avg ${prev_size:.0f} → ${recent_size:.0f} (+{((recent_size/prev_size)-1)*100:.0f}%)")
        # 3. Win streak overconfidence
        recent_wr = sum(1 for t in recent if t.is_winner()) / len(recent) if recent else 0
        if recent_wr > 0.9 and len(recent) >= 8:
            score -= 10
            warnings_list.append(f"OVERCONFIDENCE: {recent_wr*100:.0f}% WR last {len(recent)} trades (unsustainable)")
        # 4. Frequency spike
        if len(recent) >= 5 and len(previous) >= 5:
            recent_with_time = [t for t in recent if t.entry_time]
            prev_with_time = [t for t in previous if t.entry_time]
            if len(recent_with_time) >= 3 and len(prev_with_time) >= 3:
                recent_span = (recent_with_time[-1].entry_time - recent_with_time[0].entry_time).total_seconds() / 86400.0
                prev_span = (prev_with_time[-1].entry_time - prev_with_time[0].entry_time).total_seconds() / 86400.0
                recent_freq = len(recent_with_time) / max(recent_span, 0.1)
                prev_freq = len(prev_with_time) / max(prev_span, 0.1)
                if prev_freq > 0 and recent_freq > prev_freq * 2.0:
                    penalty = min(15, (recent_freq / prev_freq - 1) * 10)
                    score -= penalty
                    warnings_list.append(f"FREQUENCY_SPIKE: {prev_freq:.1f} → {recent_freq:.1f} trades/day (+{((recent_freq/prev_freq)-1)*100:.0f}%)")
        # 5. Direction bias shift
        recent_longs = sum(1 for t in recent if t.side == "LONG")
        recent_shorts = len(recent) - recent_longs
        if len(recent) >= 5:
            bias = max(recent_longs, recent_shorts) / len(recent)
            if bias > 0.8:
                score -= 10
                dominant = "LONG" if recent_longs > recent_shorts else "SHORT"
                warnings_list.append(f"DIRECTION_BIAS: {bias*100:.0f}% {dominant} last {len(recent)} trades")
        # 6. Hold time compression
        recent_holds = [t.hold_hours() for t in recent if t.hold_hours() > 0]
        prev_holds = [t.hold_hours() for t in previous if t.hold_hours() > 0]
        if recent_holds and prev_holds:
            avg_recent_hold = np.mean(recent_holds)
            avg_prev_hold = np.mean(prev_holds)
            if avg_prev_hold > 0 and avg_recent_hold < avg_prev_hold * 0.3:
                score -= 15
                warnings_list.append(f"HOLD_COMPRESSION: avg {avg_prev_hold:.1f}h → {avg_recent_hold:.1f}h ({((avg_recent_hold/avg_prev_hold)-1)*100:.0f}%)")
        # 7. Recovery chasing (loss → immediately bigger position)
        revenge_count = 0
        for i in range(1, len(ttrades)):
            if not ttrades[i-1].is_winner() and ttrades[i].position_size_usd > ttrades[i-1].position_size_usd * 1.5 and ttrades[i].position_size_usd > 0 and ttrades[i-1].position_size_usd > 0:
                revenge_count += 1
        if revenge_count > 0:
            pct = revenge_count / max(1, len(ttrades) - 1)
            if pct > 0.1:
                penalty = min(20, pct * 100)
                score -= penalty
                warnings_list.append(f"REVENGE_TRADING: {revenge_count} instances ({pct*100:.0f}% of trades follow loss with bigger size)")
        # 8. New symbol exploration
        prev_symbols = set(t.symbol for t in previous)
        recent_symbols = set(t.symbol for t in recent)
        new_symbols = recent_symbols - prev_symbols
        if len(new_symbols) > len(recent_symbols) * 0.5 and len(new_symbols) >= 3:
            score -= 10
            warnings_list.append(f"NEW_SYMBOLS: {len(new_symbols)} new symbols in recent trades ({', '.join(list(new_symbols)[:5])})")
        # 9. Against-trend entries
        against_trend = 0
        with_trend = 0
        for t in recent:
            adx_val = t.indicators.get("adx_14")
            above_sma = t.indicators.get("above_sma200")
            if adx_val is not None and adx_val > 25 and above_sma is not None:
                if (above_sma == 1 and t.side == "SHORT") or (above_sma == 0 and t.side == "LONG"):
                    against_trend += 1
                else:
                    with_trend += 1
        if against_trend + with_trend > 0 and against_trend > with_trend:
            penalty = min(15, (against_trend / (against_trend + with_trend)) * 20)
            score -= penalty
            warnings_list.append(f"AGAINST_TREND: {against_trend}/{against_trend+with_trend} entries against ADX trend")
        # 10. Drawdown trajectory
        cumulative_pnl = []
        running = 0.0
        for t in ttrades:
            running += t.pnl
            cumulative_pnl.append(running)
        if cumulative_pnl:
            peak = max(cumulative_pnl)
            current = cumulative_pnl[-1]
            if peak > 0:
                drawdown_pct = ((peak - current) / peak) * 100.0
                if drawdown_pct > 20:
                    penalty = min(20, drawdown_pct * 0.5)
                    score -= penalty
                    warnings_list.append(f"DRAWDOWN: {drawdown_pct:.1f}% from peak (peak=${peak:.0f}, current=${current:.0f})")
        score = max(0, min(100, score))
        if score >= 70:
            status = "GREEN"
        elif score >= 40:
            status = "YELLOW"
        else:
            status = "RED"
        total_pnl = sum(t.pnl for t in ttrades)
        total_wr = sum(1 for t in ttrades if t.is_winner()) / len(ttrades) * 100
        health_scores[trader_id] = {"score": round(score, 1), "status": status, "warnings": warnings_list, "n_trades": len(ttrades), "total_pnl": round(total_pnl, 2), "win_rate": round(total_wr, 1), "avg_pnl_pct": round(np.mean([t.pnl_pct for t in ttrades]), 3)}
    return health_scores


def print_health_scores(health: Dict[str, Dict[str, Any]]):
    if not health:
        print("\n  No trader health data available.")
        return
    print(f"\n{'='*100}")
    print(f"  TRADER HEALTH SCORES (Blowup Detection)")
    print(f"{'='*100}")
    sorted_traders = sorted(health.items(), key=lambda x: x[1]["score"])
    for trader_id, h in sorted_traders:
        status_icon = {"GREEN": "[OK]", "YELLOW": "[!!]", "RED": "[XX]"}[h["status"]]
        print(f"\n  {status_icon} {trader_id}: Score={h['score']}/100 ({h['status']}) | Trades={h['n_trades']} | WR={h['win_rate']:.1f}% | PnL=${h['total_pnl']:.2f}")
        for w in h["warnings"]:
            print(f"      ⚠ {w}")
        if not h["warnings"]:
            print(f"      No warnings — trader appears healthy")


# ═══════════════════════════════════════════════════════════════════
# SECTION 6 — REGIME CORRELATION
# ═══════════════════════════════════════════════════════════════════

def classify_regime(indicators: Dict[str, Any]) -> str:
    """Classify market regime from indicator snapshot."""
    adx = indicators.get("adx_14")
    bb_w = indicators.get("bb_width")
    chop = indicators.get("choppiness")
    # Derive above_sma from price vs sma_200 if not pre-computed
    above_sma = indicators.get("above_sma200")
    if above_sma is None:
        price = indicators.get("current_price") or indicators.get("close")
        sma200 = indicators.get("sma_200")
        if price and sma200 and sma200 > 0:
            above_sma = 1 if float(price) > float(sma200) else 0
    if adx is not None and adx > 25:
        if above_sma == 1:
            return "BULL_TREND"
        elif above_sma == 0:
            return "BEAR_TREND"
        else:
            return "TRENDING"
    if adx is not None and adx < 20:
        return "RANGING"
    if bb_w is not None and bb_w > 5.0:
        return "HIGH_VOL"
    if bb_w is not None and bb_w < 1.5:
        return "LOW_VOL"
    if chop is not None and chop > 61.8:
        return "RANGING"
    if chop is not None and chop < 38.2:
        return "TRENDING"
    return "UNKNOWN"


def regime_correlation(trades: List[TradeRecord]) -> Dict[str, Dict[str, Any]]:
    """Analyze trader performance per market regime."""
    traders: Dict[str, Dict[str, List[TradeRecord]]] = {}
    for t in trades:
        if not t.indicators:
            continue
        regime = classify_regime(t.indicators)
        traders.setdefault(t.trader_id, {}).setdefault(regime, []).append(t)
    results = {}
    for trader_id, regime_trades in traders.items():
        trader_results = {}
        for regime, rtrades in regime_trades.items():
            if len(rtrades) < 3:
                continue
            wins = sum(1 for t in rtrades if t.is_winner())
            wr = wins / len(rtrades) * 100
            avg_pnl = np.mean([t.pnl_pct for t in rtrades])
            total_pnl = sum(t.pnl for t in rtrades)
            trader_results[regime] = {"n_trades": len(rtrades), "win_rate": round(wr, 1), "avg_pnl_pct": round(avg_pnl, 3), "total_pnl": round(total_pnl, 2), "avg_hold_hours": round(np.mean([t.hold_hours() for t in rtrades if t.hold_hours() > 0]), 1) if any(t.hold_hours() > 0 for t in rtrades) else 0}
        results[trader_id] = trader_results
    return results


def print_regime_correlation(regime_data: Dict[str, Dict[str, Any]]):
    if not regime_data:
        print("\n  No regime correlation data available.")
        return
    print(f"\n{'='*100}")
    print(f"  REGIME CORRELATION (Trader Performance by Market Condition)")
    print(f"{'='*100}")
    for trader_id, regimes in regime_data.items():
        print(f"\n  Trader: {trader_id}")
        print(f"    {'Regime':<15} {'Trades':>8} {'WR%':>8} {'Avg PnL%':>10} {'Total PnL':>12} {'Avg Hold':>10}")
        print(f"    {'-'*15} {'-'*8} {'-'*8} {'-'*10} {'-'*12} {'-'*10}")
        for regime in ["BULL_TREND", "BEAR_TREND", "TRENDING", "RANGING", "HIGH_VOL", "LOW_VOL", "UNKNOWN"]:
            if regime in regimes:
                r = regimes[regime]
                print(f"    {regime:<15} {r['n_trades']:>8} {r['win_rate']:>7.1f}% {r['avg_pnl_pct']:>+10.3f} {r['total_pnl']:>11.2f}$ {r['avg_hold_hours']:>9.1f}h")


# ═══════════════════════════════════════════════════════════════════
# SECTION 7 — REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════

def generate_report(trades: List[TradeRecord], indicator_analysis: Dict, patterns: List[Dict], health: Dict, regime_data: Dict):
    """Generate all output files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    datestamp = datetime.now().strftime("%Y%m%d")
    # 1. Full markdown report
    report_path = OUTPUT_DIR / f"analysis_{datestamp}.md"
    with open(report_path, "w") as f:
        f.write(f"# Trader Deep Analysis — {datestamp}\n\n")
        f.write(f"## Summary\n")
        f.write(f"- **Total trades analyzed**: {len(trades)}\n")
        enriched = sum(1 for t in trades if t.indicators)
        f.write(f"- **Trades with indicator overlay**: {enriched}\n")
        traders = set(t.trader_id for t in trades)
        f.write(f"- **Traders**: {len(traders)} ({', '.join(sorted(traders))})\n")
        total_pnl = sum(t.pnl for t in trades)
        overall_wr = sum(1 for t in trades if t.is_winner()) / len(trades) * 100 if trades else 0
        f.write(f"- **Total PnL**: ${total_pnl:.2f}\n")
        f.write(f"- **Overall Win Rate**: {overall_wr:.1f}%\n\n")
        f.write(f"## Top Discriminative Indicators\n\n")
        if indicator_analysis:
            f.write(f"| Indicator | Winner Mean | Loser Mean | Cohen's d | p-value | Power |\n")
            f.write(f"|-----------|------------|------------|----------|---------|-------|\n")
            for i, (key, vals) in enumerate(indicator_analysis.items()):
                if i >= 20:
                    break
                f.write(f"| {key} | {vals['winner_mean']:.4f} | {vals['loser_mean']:.4f} | {vals['cohens_d']:+.4f} | {vals['p_value']:.6f} | {vals['discriminative_power']:.4f} |\n")
        f.write(f"\n## Extracted Patterns\n\n")
        for i, p in enumerate(patterns[:15]):
            f.write(f"### Pattern #{i+1}: WR={p['win_rate']*100:.1f}% (n={p['n_trades']})\n")
            f.write(f"- Side: {p['dominant_side']} | Avg PnL%: {p['avg_pnl_pct']:.2f}%\n")
            f.write(f"- Rule: `{p['rule_str']}`\n\n")
        f.write(f"## Trader Health Scores\n\n")
        for trader_id, h in sorted(health.items(), key=lambda x: x[1]["score"]):
            f.write(f"### {trader_id}: {h['score']}/100 ({h['status']})\n")
            f.write(f"- Trades: {h['n_trades']} | WR: {h['win_rate']:.1f}% | PnL: ${h['total_pnl']:.2f}\n")
            for w in h["warnings"]:
                f.write(f"- WARNING: {w}\n")
            f.write(f"\n")
        f.write(f"## Regime Correlation\n\n")
        for trader_id, regimes in regime_data.items():
            f.write(f"### {trader_id}\n")
            f.write(f"| Regime | Trades | WR% | Avg PnL% | Total PnL |\n")
            f.write(f"|--------|--------|-----|----------|----------|\n")
            for regime, r in regimes.items():
                f.write(f"| {regime} | {r['n_trades']} | {r['win_rate']:.1f}% | {r['avg_pnl_pct']:+.3f} | ${r['total_pnl']:.2f} |\n")
            f.write(f"\n")
    logger.info(f"Report written: {report_path}")
    # 2. Patterns JSON
    patterns_path = OUTPUT_DIR / "patterns.json"
    with open(patterns_path, "w") as f:
        json.dump(patterns, f, indent=2, default=str)
    logger.info(f"Patterns written: {patterns_path}")
    # 3. Health JSON
    health_path = OUTPUT_DIR / "trader_health.json"
    with open(health_path, "w") as f:
        json.dump(health, f, indent=2, default=str)
    logger.info(f"Health scores written: {health_path}")
    # 4. Indicator importance JSON
    importance_path = OUTPUT_DIR / "indicator_importance.json"
    with open(importance_path, "w") as f:
        json.dump(indicator_analysis, f, indent=2, default=str)
    logger.info(f"Indicator importance written: {importance_path}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 8 — PER-TRADER BREAKDOWN
# ═══════════════════════════════════════════════════════════════════

def per_trader_summary(trades: List[TradeRecord]):
    """Print per-trader summary table."""
    traders: Dict[str, List[TradeRecord]] = {}
    for t in trades:
        traders.setdefault(t.trader_id, []).append(t)
    print(f"\n{'='*120}")
    print(f"  PER-TRADER SUMMARY")
    print(f"{'='*120}")
    print(f"  {'Trader':<20} {'Trades':>7} {'WR%':>7} {'PnL':>12} {'Avg PnL%':>10} {'Avg Lev':>8} {'Avg Hold':>10} {'Longs':>7} {'Shorts':>7} {'Symbols':>8}")
    print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*8} {'-'*10} {'-'*7} {'-'*7} {'-'*8}")
    for trader_id in sorted(traders.keys()):
        tt = traders[trader_id]
        n = len(tt)
        wr = sum(1 for t in tt if t.is_winner()) / n * 100
        pnl = sum(t.pnl for t in tt)
        avg_pnl_pct = np.mean([t.pnl_pct for t in tt])
        avg_lev = np.mean([t.leverage for t in tt if t.leverage > 0])
        holds = [t.hold_hours() for t in tt if t.hold_hours() > 0]
        avg_hold = np.mean(holds) if holds else 0
        n_long = sum(1 for t in tt if t.side == "LONG")
        n_short = n - n_long
        n_sym = len(set(t.symbol for t in tt))
        print(f"  {trader_id:<20} {n:>7} {wr:>6.1f}% {pnl:>+11.2f}$ {avg_pnl_pct:>+10.3f} {avg_lev:>7.1f}x {avg_hold:>9.1f}h {n_long:>7} {n_short:>7} {n_sym:>8}")


# ═══════════════════════════════════════════════════════════════════
# SECTION 9 — CLI
# ═══════════════════════════════════════════════════════════════════

def show_latest_report():
    """Show the latest analysis report."""
    if not OUTPUT_DIR.exists():
        print("No analysis output directory found. Run analysis first.")
        return
    reports = sorted(OUTPUT_DIR.glob("analysis_*.md"), reverse=True)
    if not reports:
        print("No analysis reports found. Run analysis first.")
        return
    with open(reports[0]) as f:
        print(f.read())


def show_health():
    """Show latest trader health scores."""
    path = OUTPUT_DIR / "trader_health.json"
    if not path.exists():
        print("No health data found. Run analysis first.")
        return
    with open(path) as f:
        health = json.load(f)
    print_health_scores(health)


def show_patterns():
    """Show latest extracted patterns."""
    path = OUTPUT_DIR / "patterns.json"
    if not path.exists():
        print("No patterns found. Run analysis first.")
        return
    with open(path) as f:
        patterns = json.load(f)
    print_patterns(patterns)


def main():
    parser = argparse.ArgumentParser(description="Deep analysis of copy trading master traders")
    parser.add_argument("--input", "-i", type=str, help="Input file (XLSX, CSV) or directory (tracker logs)")
    parser.add_argument("--report", action="store_true", help="Show latest analysis report")
    parser.add_argument("--health", action="store_true", help="Show trader health scores")
    parser.add_argument("--patterns", action="store_true", help="Show extracted patterns")
    parser.add_argument("--skip-indicators", action="store_true", help="Skip indicator overlay (faster, less analysis)")
    parser.add_argument("--min-leaf", type=int, default=20, help="Minimum trades per decision tree leaf (default: 20)")
    parser.add_argument("--top-n", type=int, default=15, help="Number of top results to display (default: 15)")
    args = parser.parse_args()
    if args.report:
        show_latest_report()
        return
    if args.health:
        show_health()
        return
    if args.patterns:
        show_patterns()
        return
    if not args.input:
        parser.print_help()
        print("\nError: --input is required for analysis. Provide XLSX, CSV, or tracker log directory.")
        sys.exit(1)
    print(f"\n{'='*100}")
    print(f"  TRADER DEEP ANALYZER")
    print(f"  Input: {args.input}")
    print(f"  Klines: {KLINES_DIR}")
    print(f"  Output: {OUTPUT_DIR}")
    print(f"{'='*100}")
    # 1. Ingest trades
    trades = ingest_trades(args.input)
    if not trades:
        print("\nNo trades ingested. Check input file format.")
        sys.exit(1)
    print(f"\n  Ingested {len(trades)} trades from {len(set(t.trader_id for t in trades))} trader(s)")
    # 2. Indicator overlay
    if not args.skip_indicators:
        logger.info("Starting indicator overlay...")
        overlay_indicators(trades)
    else:
        logger.info("Skipping indicator overlay (--skip-indicators)")
    # 3. Per-trader summary
    per_trader_summary(trades)
    # 4. Winner vs Loser analysis
    indicator_analysis = winner_loser_analysis(trades)
    print_top_indicators(indicator_analysis, args.top_n)
    # 5. Pattern extraction
    patterns = extract_patterns(trades, min_samples_leaf=args.min_leaf)
    print_patterns(patterns, args.top_n)
    # 6. Blowup detection
    health = compute_trader_health(trades)
    print_health_scores(health)
    # 7. Regime correlation
    regime_data = regime_correlation(trades)
    print_regime_correlation(regime_data)
    # 8. Generate output files
    generate_report(trades, indicator_analysis, patterns, health, regime_data)
    print(f"\n{'='*100}")
    print(f"  ANALYSIS COMPLETE")
    print(f"  Output files in: {OUTPUT_DIR}")
    print(f"{'='*100}\n")


if __name__ == "__main__":
    main()
