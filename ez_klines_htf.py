# pylint: disable=W,C,R,I
"""ez_klines_htf — Resample Daily candles to Weekly (W) and Monthly (M).
Reads existing _D.json files from klines_cache/, resamples to W/M, writes back.
Also fetches D/W/M for big-cap symbols (BTCUSDT, ETHUSDT) directly from Binance
since they may not be in the regular klines pipeline.
For stocks: fetches D/W/M from Tradier and writes to data/tradier_bars/.
All data is kept FOREVER (no truncation) — these are slow TFs with tiny files.
Runs as: daemon (every 6h), cron, or one-shot.
Usage: python ez_klines_htf.py [--once] [--daemon] [--interval 21600]
"""
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
import aiohttp
from config import Config
from config_tradier import TradierConfig
from utils import load_environment_from_gpg, setup_logger

config = Config()
tradier_config = TradierConfig()
logger = setup_logger("ez_klines_htf", str(config.LOG_DIR / "ez_klines_htf.log"), logging.INFO)
load_environment_from_gpg(logger)
BASE_PATH = Path(os.getenv("EZ_BASE_PATH", str(config.BASE_PATH)))
KLINES_DIR = BASE_PATH / "klines_cache"
TRADIER_BARS_DIR = BASE_PATH / "data" / "tradier_bars"
TRADIER_BARS_DIR.mkdir(parents=True, exist_ok=True)
shutdown_event = asyncio.Event()

# Big-cap symbols to fetch directly from Binance (may not be in symbols.json)
BIGCAP_CRYPTO = ["BTCUSDT", "ETHUSDT", "BTCUSDC", "ETHUSDC", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
# Stock symbols
STOCK_SYMBOLS = ["NVDA", "MSTR", "AAPL", "MSFT", "GOOG", "META", "TSLA", "AMD", "AMZN", "GLD", "USO", "PLTR", "IBIT"]
# Resample map
RESAMPLE_MAP = {"W": 7, "M": 30}
# Binance API intervals for direct fetch
BINANCE_HTF_INTERVALS = {"D": "1d", "W": "1w", "M": "1M"}
# Tradier API intervals
TRADIER_HTF_INTERVALS = {"D": "daily", "W": "weekly", "M": "monthly"}


def load_json_bars(path: Path) -> List[dict]:
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.error(f"[LOAD] {path}: {e}")
    return []


def save_json_bars(path: Path, bars: List[dict]):
    """Save bars to JSON. No truncation — keep forever."""
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bars, indent=None))
        os.replace(str(tmp), str(path))
    except Exception as e:
        logger.error(f"[SAVE] {path}: {e}")


def merge_bars(existing: List[dict], new_bars: List[dict]) -> List[dict]:
    """Merge two bar lists, dedup by timestamp, sort chronologically."""
    seen = {}
    for b in existing + new_bars:
        ts = b.get("timestamp", "")
        if ts:
            seen[ts] = b
    result = sorted(seen.values(), key=lambda x: x.get("timestamp", ""))
    return result


def resample_daily_to_weekly(daily_bars: List[dict]) -> List[dict]:
    """Resample daily bars to weekly (Monday-Sunday, labeled by Monday)."""
    if not daily_bars:
        return []
    from collections import defaultdict
    weeks = defaultdict(list)
    for bar in daily_bars:
        ts_str = bar.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        # ISO week: Monday = 0. Get the Monday of this week.
        monday = dt - timedelta(days=dt.weekday())
        week_key = monday.strftime("%Y-%m-%dT00:00:00.000000Z")
        weeks[week_key].append(bar)
    result = []
    for week_ts in sorted(weeks.keys()):
        bars = weeks[week_ts]
        result.append({"timestamp": week_ts, "open": bars[0]["open"], "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars), "close": bars[-1]["close"], "volume": sum(b.get("volume", 0) for b in bars)})
    return result


def resample_daily_to_monthly(daily_bars: List[dict]) -> List[dict]:
    """Resample daily bars to monthly (1st of month)."""
    if not daily_bars:
        return []
    from collections import defaultdict
    months = defaultdict(list)
    for bar in daily_bars:
        ts_str = bar.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        month_key = dt.strftime("%Y-%m-01T00:00:00.000000Z")
        months[month_key].append(bar)
    result = []
    for month_ts in sorted(months.keys()):
        bars = months[month_ts]
        result.append({"timestamp": month_ts, "open": bars[0]["open"], "high": max(b["high"] for b in bars), "low": min(b["low"] for b in bars), "close": bars[-1]["close"], "volume": sum(b.get("volume", 0) for b in bars)})
    return result


async def fetch_binance_klines(session: aiohttp.ClientSession, symbol: str, interval: str, limit: int = 1500) -> List[dict]:
    """Fetch klines from Binance Futures API and convert to our format."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                raw = await resp.json()
                bars = []
                for b in raw:
                    ts = datetime.fromtimestamp(b[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    bars.append({"timestamp": ts, "open": float(b[1]), "high": float(b[2]), "low": float(b[3]), "close": float(b[4]), "volume": float(b[5])})
                return bars
            else:
                logger.warning(f"[BINANCE] {symbol} {interval}: HTTP {resp.status}")
    except Exception as e:
        logger.error(f"[BINANCE] {symbol} {interval}: {e}")
    return []


async def fetch_tradier_klines(session: aiohttp.ClientSession, symbol: str, interval: str, limit: int = 1500) -> List[dict]:
    """Fetch stock bars from Tradier API."""
    try:
        acct_cfg = tradier_config.ACCOUNTS.get("trb", {})
        api_key = acct_cfg.get("key", os.getenv("TRADIER_API_KEY", ""))
        base_url = "https://api.tradier.com/v1" if acct_cfg.get("env") == "live" else "https://sandbox.tradier.com/v1"
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        if interval == "monthly":
            lookback = limit * 35
        elif interval == "weekly":
            lookback = limit * 8
        else:
            lookback = limit + 10
        end = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")
        url = f"{base_url}/markets/history?symbol={symbol}&interval={interval}&start={start}&end={end}"
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                data = await resp.json()
                hist = data.get("history", {})
                if not hist:
                    return []
                days = hist.get("day", [])
                if isinstance(days, dict):
                    days = [days]
                bars = []
                for d in days:
                    ts = d.get("date", "")
                    if ts:
                        ts = f"{ts}T00:00:00.000000Z"
                    bars.append({"timestamp": ts, "open": float(d.get("open", d["close"])), "high": float(d["high"]), "low": float(d["low"]), "close": float(d["close"]), "volume": float(d.get("volume", 0))})
                return bars
            else:
                logger.warning(f"[TRADIER] {symbol} {interval}: HTTP {resp.status}")
    except Exception as e:
        logger.error(f"[TRADIER] {symbol} {interval}: {e}")
    return []


async def process_crypto(session: aiohttp.ClientSession):
    """Process all crypto symbols: resample existing D→W/M + fetch big-caps directly."""
    # 1. Resample all existing _D.json → _W.json + _M.json
    d_files = sorted(KLINES_DIR.glob("*_D.json"))
    resampled_w = 0
    resampled_m = 0
    for d_file in d_files:
        symbol = d_file.stem.replace("_D", "")
        daily_bars = load_json_bars(d_file)
        if len(daily_bars) < 7:
            continue
        # Weekly
        w_path = KLINES_DIR / f"{symbol}_W.json"
        existing_w = load_json_bars(w_path)
        new_w = resample_daily_to_weekly(daily_bars)
        merged_w = merge_bars(existing_w, new_w)
        if len(merged_w) > len(existing_w) or not w_path.exists():
            save_json_bars(w_path, merged_w)
            resampled_w += 1
        # Monthly
        m_path = KLINES_DIR / f"{symbol}_M.json"
        existing_m = load_json_bars(m_path)
        new_m = resample_daily_to_monthly(daily_bars)
        merged_m = merge_bars(existing_m, new_m)
        if len(merged_m) > len(existing_m) or not m_path.exists():
            save_json_bars(m_path, merged_m)
            resampled_m += 1
    logger.info(f"[CRYPTO_RESAMPLE] Resampled {resampled_w} W files, {resampled_m} M files from {len(d_files)} D files")
    # 2. Fetch big-cap D/W/M directly from Binance (they may not have _D.json if not in symbols.json)
    fetched = 0
    for symbol in BIGCAP_CRYPTO:
        for tf_label, binance_interval in BINANCE_HTF_INTERVALS.items():
            file_path = KLINES_DIR / f"{symbol}_{tf_label}.json"
            existing = load_json_bars(file_path)
            new_bars = await fetch_binance_klines(session, symbol, binance_interval, 1500)
            if new_bars:
                merged = merge_bars(existing, new_bars)
                save_json_bars(file_path, merged)
                fetched += 1
                if len(merged) != len(existing):
                    logger.info(f"[BIGCAP_FETCH] {symbol}_{tf_label}: {len(existing)}→{len(merged)} bars")
        # Also resample the fetched D to W/M for consistency
        d_path = KLINES_DIR / f"{symbol}_D.json"
        daily = load_json_bars(d_path)
        if len(daily) >= 7:
            w_path = KLINES_DIR / f"{symbol}_W.json"
            w_bars = resample_daily_to_weekly(daily)
            existing_w = load_json_bars(w_path)
            save_json_bars(w_path, merge_bars(existing_w, w_bars))
            m_path = KLINES_DIR / f"{symbol}_M.json"
            m_bars = resample_daily_to_monthly(daily)
            existing_m = load_json_bars(m_path)
            save_json_bars(m_path, merge_bars(existing_m, m_bars))
        await asyncio.sleep(0.2)  # Rate limit
    logger.info(f"[BIGCAP_CRYPTO] Fetched D/W/M for {len(BIGCAP_CRYPTO)} big-cap symbols ({fetched} API calls)")


async def process_stocks(session: aiohttp.ClientSession):
    """Fetch D/W/M for stock symbols from Tradier and save to tradier_bars/."""
    fetched = 0
    for symbol in STOCK_SYMBOLS:
        for tf_label, tradier_interval in TRADIER_HTF_INTERVALS.items():
            file_path = TRADIER_BARS_DIR / f"{symbol}_{tf_label}.json"
            existing = load_json_bars(file_path)
            new_bars = await fetch_tradier_klines(session, symbol, tradier_interval)
            if new_bars:
                merged = merge_bars(existing, new_bars)
                save_json_bars(file_path, merged)
                fetched += 1
                if len(merged) != len(existing):
                    logger.info(f"[STOCK_FETCH] {symbol}_{tf_label}: {len(existing)}→{len(merged)} bars")
        # Also resample D→W/M locally as backup
        d_path = TRADIER_BARS_DIR / f"{symbol}_D.json"
        daily = load_json_bars(d_path)
        if len(daily) >= 7:
            w_from_d = resample_daily_to_weekly(daily)
            w_path = TRADIER_BARS_DIR / f"{symbol}_W.json"
            existing_w = load_json_bars(w_path)
            save_json_bars(w_path, merge_bars(existing_w, w_from_d))
            m_from_d = resample_daily_to_monthly(daily)
            m_path = TRADIER_BARS_DIR / f"{symbol}_M.json"
            existing_m = load_json_bars(m_path)
            save_json_bars(m_path, merge_bars(existing_m, m_from_d))
        await asyncio.sleep(0.5)  # Tradier rate limit (10/sec)
    logger.info(f"[STOCKS] Fetched D/W/M for {len(STOCK_SYMBOLS)} stocks ({fetched} API calls)")


async def run_once(session: aiohttp.ClientSession):
    logger.info("[HTF_KLINES] Starting D/W/M candle build...")
    start = time.time()
    await process_crypto(session)
    await process_stocks(session)
    elapsed = time.time() - start
    # Count totals
    w_count = len(list(KLINES_DIR.glob("*_W.json")))
    m_count = len(list(KLINES_DIR.glob("*_M.json")))
    stock_count = len(list(TRADIER_BARS_DIR.glob("*_W.json")))
    logger.info(f"[HTF_KLINES] Done in {elapsed:.1f}s. Crypto: {w_count} W files, {m_count} M files. Stocks: {stock_count} W files.")


async def main_async(once: bool = False, interval: int = 21600):
    async with aiohttp.ClientSession() as session:
        if once:
            await run_once(session)
        else:
            logger.info(f"[DAEMON] Running every {interval}s ({interval/3600:.1f}h)")
            while not shutdown_event.is_set():
                await run_once(session)
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass


def handle_signal(sig, frame):
    shutdown_event.set()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="HTF Klines Builder (D/W/M)")
    parser.add_argument("--once", action="store_true", help="Run once then exit")
    parser.add_argument("--interval", type=int, default=21600, help="Daemon interval (default 6h)")
    args = parser.parse_args()
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    asyncio.run(main_async(once=args.once, interval=args.interval))


if __name__ == "__main__":
    main()
