#!/usr/bin/env python3
"""Primary Mac stock Tradier OHLCV producer (stocks only)."""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone, time as dt_time

import pandas as pd
import pytz

from utils import load_environment_from_gpg
load_environment_from_gpg(None)
from config_tradier import TradierConfig
from tradier_api import TradierAPIClient

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("tradier_klines")
ET = pytz.timezone("America/New_York")
UTC = pytz.UTC
config = TradierConfig()
api_client = TradierAPIClient(config, account_key="tra")

def market_hours(df, fast=False):
    if df.empty: return df
    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex): df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None: df.index = df.index.tz_localize("UTC")
    local = df.index.tz_convert(ET)
    start = dt_time(8, 30) if fast else dt_time(9, 30)
    return df.loc[(local.time >= start) & (local.time <= dt_time(16, 0))]

def parse_intraday(raw):
    df = pd.DataFrame(raw or [])
    if df.empty: return pd.DataFrame()
    # Handle both "time" (Tradier native) and "timestamp" (our export) - use whichever is present
    time_col = df.get("time")
    if time_col is None or time_col.isna().all():
        if "timestamp" in df:
            time_col = df["timestamp"]
        else:
            return pd.DataFrame()
    else:
        # Fill NaN time entries from timestamp (provisional rows)
        if "timestamp" in df:
            time_col = time_col.fillna(df["timestamp"])
    dt = pd.to_datetime(time_col, format="mixed", utc=True, errors="coerce")
    # Drop rows where timestamp failed to parse
    valid = dt.notna()
    if not valid.any():
        return pd.DataFrame()
    dt = dt[valid]
    df = df.loc[valid]
    if dt.dt.tz is None: dt = dt.dt.tz_localize(ET, ambiguous="infer").dt.tz_convert(UTC)
    else: dt = dt.dt.tz_convert(UTC)
    df.index = pd.DatetimeIndex(dt)
    for col in ("open", "high", "low", "close", "volume"): df[col] = pd.to_numeric(df.get(col), errors="coerce")
    return df.sort_index()

def daily_frame(raw):
    df = pd.DataFrame(raw or [])
    if df.empty: return pd.DataFrame()
    if "date" in df:
        # Use timestamp fallback for rows without date (provisional intraday with volume 0)
        # and format='mixed' to handle both YYYY-MM-DD and ISO8601 timestamps
        date_col = df["date"].fillna(df.get("timestamp", pd.Series([pd.NaT]*len(df))))
        dates = pd.to_datetime(date_col, format="mixed", utc=True, errors="coerce")
    elif "timestamp" in df:
        dates = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    else:
        return pd.DataFrame()
    # Drop NaT before localize to avoid OverflowError on NaT.date()
    dates = dates.dropna()
    if dates.empty:
        return pd.DataFrame()
    # Convert UTC dates to ET date then 16:00 ET -> UTC
    df = df.loc[dates.index] if len(dates) != len(df) else df
    df.index = pd.DatetimeIndex([ET.localize(datetime.combine(d.tz_convert(ET).date() if d.tzinfo else d.date(), dt_time(16, 0))).astimezone(UTC) for d in dates])
    for col in ("open", "high", "low", "close", "volume"): df[col] = pd.to_numeric(df.get(col), errors="coerce")
    return df.sort_index()

def resample(df, rule):
    if df.empty: return df
    if rule == "4h":
        local = df.copy(); local.index = local.index.tz_convert(ET)
        local = local[(local.index.time >= dt_time(9, 30)) & (local.index.time <= dt_time(16, 0))]
        labels = []
        for stamp in local.index:
            # Required stock-session anchors: 09:30, 13:00, 16:00 ET.
            anchor = (dt_time(9, 30) if stamp.time() < dt_time(13, 0)
                      else dt_time(13, 0) if stamp.time() < dt_time(16, 0)
                      else dt_time(16, 0))
            labels.append(ET.localize(datetime.combine(stamp.date(), anchor)))
        local["_label"] = labels
        out = local.groupby("_label").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
        out.index = pd.DatetimeIndex(out.index).tz_convert(UTC)
        return out
    local = df.copy(); local.index = local.index.tz_convert(ET) - pd.Timedelta(hours=9, minutes=30)
    out = local.resample(rule, label="left", closed="left").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna(subset=["close"])
    out.index = (out.index + pd.Timedelta(hours=9, minutes=30)).tz_convert(UTC)
    return out

def pad_daily(target, daily):
    if target.empty: return daily.copy()
    if daily.empty: return target
    first = target.index.min().tz_convert(ET).date()
    pad = daily[daily.index.tz_convert(ET).date < first]
    return pd.concat([pad, target]).sort_index() if not pad.empty else target

def export_atomic(df, path):
    if df.empty: return False
    out = df.copy(); out.index = pd.DatetimeIndex(out.index)
    out["timestamp"] = out.index.tz_convert(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    records = out[["timestamp","open","high","low","close","volume"]].to_dict("records")
    # Append-only contract: preserve every valid historical row already on
    # disk. A repeated timestamp is an in-place correction of the open candle;
    # no older timestamp may be discarded.
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
            if isinstance(existing, list):
                merged = {}
                for row in existing:
                    if isinstance(row, dict) and row.get("timestamp"):
                        merged[str(row["timestamp"])] = row
                for row in records:
                    key = str(row["timestamp"])
                    prev = merged.get(key)
                    # Never overwrite better OHLCV (volume>0) with provisional OHLC (volume 0)
                    if prev is not None and float(prev.get("volume",0) or 0) > 0 and float(row.get("volume",0) or 0) == 0:
                        continue
                    merged[key] = row
                records = [merged[key] for key in sorted(merged)]
        except (OSError, ValueError, TypeError):
            # Never replace a large unreadable cache with a partial response.
            if path.stat().st_size > 1000:
                logger.error("refusing write to unreadable existing cache %s", path)
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, separators=(",", ":")); handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)
    return True

async def process_symbol(symbol):
    now = datetime.now(timezone.utc)
    def load_cached(tf):
        try:
            with (config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json").open(encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return []
    def cached_start(tf, fallback):
        path = config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json"
        try:
            with path.open(encoding="utf-8") as handle:
                rows = json.load(handle)
            if rows:
                stamp = rows[-1].get("timestamp")
                if stamp:
                    return pd.to_datetime(stamp, utc=True).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        return fallback
    async def fetch(awaitable, label):
        try:
            return await asyncio.wait_for(awaitable, timeout=20)
        except Exception as exc:
            logger.error("%s %s fetch failed/timeout: %s", symbol, label, exc)
            return []
    # Daily/5m/15m are local products. Fetch only the smallest live series;
    # derive the larger intraday bars immediately and keep historical rows.
    daily = daily_frame(load_cached("D"))
    # Fetch each intraday TF natively via Time & Sales (1min/5min/15min) — each has authoritative volume.
    # Only resample as fallback when native returns <10 bars (API failure).
    f1 = parse_intraday(await fetch(api_client.get_timesales(symbol, interval="1min", start=cached_start("1m", (now-timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))), "1m"))
    if f1.empty:
        f1 = parse_intraday(load_cached("1m"))
    f5_native = parse_intraday(await fetch(api_client.get_timesales(symbol, interval="5min", start=cached_start("5m", (now-timedelta(days=5)).strftime("%Y-%m-%d"))), "5m"))
    f15_native = parse_intraday(await fetch(api_client.get_timesales(symbol, interval="15min", start=cached_start("15m", (now-timedelta(days=5)).strftime("%Y-%m-%d"))), "15min"))
    # Fallback resampling preserves volume by summing 1m volumes; native with volume preferred
    if f5_native.empty or len(f5_native) < 10:
        f5 = resample(f1, "5min") if not f1.empty else parse_intraday(load_cached("5m"))
    else:
        f5 = f5_native
    if f15_native.empty or len(f15_native) < 10:
        f15 = resample(f5, "15min") if not f5.empty else parse_intraday(load_cached("15m"))
    else:
        f15 = f15_native
    daily = market_hours(daily); f15, f5, f1 = market_hours(f15, True), market_hours(f5, True), market_hours(f1, True)
    # 1h/4h/D counted from 15m which has authoritative volume when Tradier succeeded.
    # Never overwrite better OHLCV (volume>0) with provisional OHLC (volume 0) — handle in export_atomic merge, but also here:
    bundle = {"D":daily,"15m":f15,"5m":f5,"1m":f1,"1h":pad_daily(resample(f15,"1h"),daily),"4h":pad_daily(resample(f15,"4h"),daily)}
    written = 0
    for tf, frame in bundle.items():
        if not frame.empty and export_atomic(frame, config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json"): written += 1
    if written != 6: logger.warning("%s incomplete: wrote %d/6 timeframes", symbol, written)
    return written == 6

async def main():
    # Stock kline creation is strictly RTH in UTC: Mon-Fri 13:30-20:00Z.
    # Outside this window, leave every cache untouched.
    now_utc = datetime.now(timezone.utc)
    market_open = now_utc.weekday() < 5 and (
        (now_utc.hour > 13 or (now_utc.hour == 13 and now_utc.minute >= 30))
        and now_utc.hour < 20
    )
    if not market_open:
        logger.info("Outside stock market hours (%sZ); no klines added", now_utc.strftime("%H:%M"))
        return
    await api_client.connect()
    try:
        with open(config.SYMBOLS_FILE, encoding="utf-8") as handle: symbols = [str(s).upper() for s in json.load(handle)]
        sem = asyncio.Semaphore(64)
        async def run(symbol):
            async with sem:
                try: return await process_symbol(symbol)
                except Exception as exc: logger.error("%s failed: %s", symbol, exc); return False
        results = await asyncio.gather(*(run(s) for s in symbols))
        logger.info("Tradier stock kline cycle complete: %d/%d symbols with all 6 timeframes", sum(results), len(results))
    finally: await api_client.close()

if __name__ == "__main__": asyncio.run(main())
