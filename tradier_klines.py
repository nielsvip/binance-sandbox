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
from tools.npz_cutoffs import stock_session_bounds

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
    if not fast:
        return df.loc[(local.time >= dt_time(9, 30)) & (local.time <= dt_time(16, 0))]
    keep = []
    for stamp in local:
        bounds = stock_session_bounds(stamp.date())
        if bounds is None:
            keep.append(False)
            continue
        epoch = stamp.tz_convert(UTC).timestamp()
        premarket_open = ET.localize(datetime.combine(stamp.date(), dt_time(4, 0))).timestamp()
        keep.append(premarket_open <= epoch < bounds[1])
    return df.loc[keep]

def parse_intraday(raw):
    # STKT 2026-10-01: Tradier 'time' is a NAIVE America/New_York string; the epoch 'timestamp' is true UTC.
    # The previous version parsed 'time' with utc=True (ET mis-tagged as UTC: 4-5h label shift) -> wrong/misplaced cache rows.
    df = pd.DataFrame(raw or [])
    if df.empty: return pd.DataFrame()
    dt = None
    if "timestamp" in df:
        ep = pd.to_numeric(df["timestamp"], errors="coerce")
        if ep.notna().any() and (ep.dropna() > 1e9).all():
            dt = pd.to_datetime(ep, unit="s", utc=True, errors="coerce")
        else:
            dt = pd.to_datetime(df["timestamp"], format="mixed", utc=True, errors="coerce")  # our own ISO-UTC exports
    if (dt is None or dt.isna().all()) and "time" in df:
        naive = pd.to_datetime(df["time"], format="mixed", errors="coerce")
        dt = naive.dt.tz_localize(ET, ambiguous="NaT", nonexistent="NaT").dt.tz_convert(UTC)
    if dt is None:
        return pd.DataFrame()
    valid = dt.notna()
    if not valid.any():
        return pd.DataFrame()
    df = df.loc[valid]
    df.index = pd.DatetimeIndex(dt[valid])
    for col in ("open", "high", "low", "close", "volume"): df[col] = pd.to_numeric(df.get(col), errors="coerce")
    return df[~df.index.duplicated(keep="last")].sort_index()

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
    # STKT: Tradier daily 'date' is the session date itself (YYYY-MM-DD); parsing it as UTC midnight and converting to ET mislabelled it one day early
    raw_d = df["date"].astype(str).str[:10] if "date" in df else None
    sess = [(pd.Timestamp(raw_d.loc[i]).date() if raw_d is not None and len(str(raw_d.loc[i])) == 10 and str(raw_d.loc[i])[4] == "-" and "T" not in str(df["date"].loc[i]) else (d.tz_convert(ET).date() if d.tzinfo else d.date())) for i, d in zip(df.index, dates)]
    df.index = pd.DatetimeIndex([ET.localize(datetime.combine(d, dt_time(16, 0))).astimezone(UTC) for d in sess])
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

def mark_native_finality(frame, span_s, now_s=None):
    if frame.empty:
        return frame
    observed = datetime.now(timezone.utc).timestamp() if now_s is None else float(now_s)
    frame = frame.copy()
    closed = frame.index + pd.Timedelta(seconds=span_s) <= pd.Timestamp(observed, unit='s', tz='UTC')
    frame['_closed'] = closed
    frame['_provisional'] = ~closed
    frame['_closed_verified_at'] = [observed if value else None for value in closed]
    frame['_source'] = f'tradier_native_{span_s // 60}m'
    return frame


def export_atomic(df, path, replace_from=None):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(f'.{path.name}.mark.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        written = _export_atomic_locked(df, path, replace_from)
    if written and path.stem.endswith('_15m'):
        from closed_bar_feed import is_verified_closed_native, publish_closed_15m
        from tools.npz_cutoffs import is_rth_bar_start
        candidates = df[df.index + pd.Timedelta(seconds=900) <= pd.Timestamp.now(tz='UTC')]
        candidates = candidates.loc[[is_rth_bar_start(stamp.timestamp()) for stamp in candidates.index]]
        if not candidates.empty:
            native = candidates.iloc[-1].to_dict()
            stamp = candidates.index[-1]
            native['timestamp'] = stamp.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            if is_verified_closed_native(native, int(stamp.timestamp()) + 900) and is_rth_bar_start(stamp.timestamp()):
                publish_closed_15m(path, native, None, path.parent.parent.parent, 'tradier', path.stem[:-4])
    return written


def _export_atomic_locked(df, path, replace_from=None):
    if df.empty: return False
    out = df.copy(); out.index = pd.DatetimeIndex(out.index)
    out["timestamp"] = out.index.tz_convert(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    columns = ["timestamp","open","high","low","close","volume"] + [key for key in out.columns if str(key).startswith('_')]
    records = out[columns].astype(object).where(pd.notna(out[columns]), None).to_dict("records")
    if path.stem.endswith('_15m'):
        from closed_bar_feed import timestamp_epoch
        record_key = lambda row: timestamp_epoch(row['timestamp'])
    else:
        record_key = lambda row: str(row['timestamp'])
    # Append-only contract: preserve every valid historical row already on
    # disk. A repeated timestamp is an in-place correction of the open candle;
    # no older timestamp may be discarded.
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as handle:
                existing = json.load(handle)
            if isinstance(existing, list):
                # 2026-10-28 operator law: one canonical UTC Z stamp everywhere. Legacy rows ("YYYY-MM-DD", "...+00:00", "NaT" secondary stamps) are converted
                # BEFORE the merge so they key the same way as the rows written now; convert-only, no row is ever dropped (klines_guard / klines_vault forbid shrinking).
                import kline_stamp as _ks
                existing, _stamp_stats = _ks.normalize_rows(existing)
                if _stamp_stats["converted"] or _stamp_stats["secondary_nulled"]:
                    logger.info("kline_stamp: %s converted %d primary / %d secondary stamps to canonical Z", path.name, _stamp_stats["converted"], _stamp_stats["secondary_nulled"])
                merged = {}
                for row in existing:
                    if isinstance(row, dict) and row.get("timestamp"):
                        # STKT repair pass: rows inside the re-fetched window are replaced wholesale by the authoritative bars
                        if replace_from and str(row["timestamp"]) >= replace_from and not row.get("_closed"):
                            continue
                        merged[record_key(row)] = row
                for row in records:
                    key = record_key(row)
                    prev = merged.get(key)
                    if path.stem.endswith('_15m') and prev is not None:
                        from closed_bar_feed import preferred_closed_row
                        row = preferred_closed_row(prev, row)
                        merged[key] = row
                        continue
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
    # USER 2026-10-16 ("are we finally never erasing another kline on s1"): this writer was the one still destroying
    # them. `replace_from` drops EVERY existing row at/after the window start and keeps only what the fresh response
    # returned, and refresh_daily additionally filters `volume > 0` — so a reply with fewer bars in the window silently
    # deletes history. Measured on s1: MSTR_D 5560->5552 and IBIT_D 1852->1844 rows at 21:16Z (span unchanged, 8 days
    # gone each), and 29 such events since 10-13 totalling ~1500 file-shrinks. klines_guard --tripwire restored every
    # one within the minute, so nothing was lost permanently, but the write/restore tug-of-war is why re-downloading
    # never catches up. klines_guard's own contract says all canonical-cache writers MUST go through guarded_write;
    # this one never did. It refuses a write that drops rows from a >=400d file or truncates the span, and leaves the
    # file untouched. TRADIER_KLINES_ALLOW_SHRINK=1 restores the old unguarded write for a deliberate repair pass.
    if os.environ.get("TRADIER_KLINES_ALLOW_SHRINK", "0") != "1":
        try:
            import sys as _sys
            if os.path.dirname(os.path.abspath(__file__)) + "/tools" not in _sys.path:
                _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
            from klines_guard import guarded_write as _guarded_write
        except Exception as _ge:
            logger.warning("klines_guard unavailable (%s) — writing %s unguarded", _ge, path.name)
        else:
            ok, why = _guarded_write(path, records)
            if not ok:
                logger.error("REFUSED shrinking write to %s: %s", path.name, why)
            return bool(ok)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(records, handle, separators=(",", ":")); handle.flush(); os.fsync(handle.fileno())
    os.replace(tmp, path)
    return True

async def refresh_daily(symbol, days):
    """STKT: the D cache had no writer after 2026-08-26 (146 symbols stalled) and held provisional vol-0 rows; re-fetch Tradier daily history."""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        raw = await asyncio.wait_for(api_client.get_history(symbol, start=start), timeout=30)
    except Exception as exc:
        logger.error("%s D history failed: %s", symbol, exc)
        return False
    frame = daily_frame(raw)
    if frame.empty or len(frame) < 3:
        return False
    frame = frame[frame["volume"] > 0]
    ws = pd.Timestamp(start, tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return export_atomic(frame, config.KLINES_CACHE_DIR / f"{symbol}_D.json", replace_from=ws)

async def process_symbol(symbol, repair=False):
    now = datetime.now(timezone.utc)
    rwin = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    def load_cached(tf):
        try:
            with (config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json").open(encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return []
    def cached_start(tf, fallback):
        # UTC ONLY (2026-10-13 revision): Tradier misreads a UTC wall clock 4-5h late and rejects
        # UTC offsets outright (0 rows back), so the RTH tail was never returned (tails froze
        # fleet-wide at 17:30Z). We therefore ask by UTC calendar DATE only — midnight-to-now
        # covers the whole session under any vendor reading — and do all filtering client-side
        # in UTC. Overlap is harmless (merge dedups by UTC stamp). Never add a time component
        # or any non-UTC zone to these request strings.
        path = config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json"
        try:
            with path.open(encoding="utf-8") as handle:
                rows = json.load(handle)
            if rows:
                stamp = rows[-1].get("timestamp")
                if stamp:
                    return pd.to_datetime(stamp, utc=True).strftime("%Y-%m-%d")
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
    await refresh_daily(symbol, 45 if repair else 10)
    daily = daily_frame(load_cached("D"))
    # Fetch each intraday TF natively via Time & Sales (1min/5min/15min) — each has authoritative volume.
    # Only resample as fallback when native returns <10 bars (API failure).
    f1 = mark_native_finality(parse_intraday(await fetch(api_client.get_timesales(symbol, interval="1min", start=cached_start("1m", (now-timedelta(days=1)).strftime("%Y-%m-%d"))), "1m")), 60)
    if f1.empty:
        f1 = parse_intraday(load_cached("1m"))
    f5_native = mark_native_finality(parse_intraday(await fetch(api_client.get_timesales(symbol, interval="5min", start=(rwin if repair else cached_start("5m", (now-timedelta(days=5)).strftime("%Y-%m-%d")))), "5m")), 300)
    f15_native = mark_native_finality(parse_intraday(await fetch(api_client.get_timesales(symbol, interval="15min", start=(rwin if repair else cached_start("15m", (now-timedelta(days=5)).strftime("%Y-%m-%d")))), "15min")), 900)
    # Fallback resampling preserves volume by summing 1m volumes; native with volume preferred
    if f5_native.empty or len(f5_native) < 10:
        f5 = resample(f1, "5min") if not f1.empty else parse_intraday(load_cached("5m"))
    else:
        f5 = f5_native
    if f15_native.empty:
        f15 = resample(f5, "15min") if not f5.empty else parse_intraday(load_cached("15m"))
    else:
        f15 = f15_native
    daily = market_hours(daily); f15, f5, f1 = market_hours(f15, True), market_hours(f5, True), market_hours(f1, True)
    # 1h/4h/D counted from 15m which has authoritative volume when Tradier succeeded.
    # Never overwrite better OHLCV (volume>0) with provisional OHLC (volume 0) — handle in export_atomic merge, but also here:
    bundle = {"D":daily,"15m":f15,"5m":f5,"1m":f1,"1h":pad_daily(resample(f15,"1h"),daily),"4h":pad_daily(resample(f15,"4h"),daily)}
    written = 0
    ws_iso = pd.Timestamp(rwin, tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for tf, frame in bundle.items():
        rf = None
        # repair pass: only replace a window the authoritative frame really covers (never delete rows we cannot re-supply)
        if repair and tf in ("5m", "15m", "1h", "4h") and not frame.empty and frame.index.min() <= pd.Timestamp(rwin, tz="UTC") + pd.Timedelta(days=2):
            rf = ws_iso
        if not frame.empty and export_atomic(frame, config.KLINES_CACHE_DIR / f"{symbol}_{tf}.json", replace_from=rf): written += 1
    if written != 6: logger.warning("%s incomplete: wrote %d/6 timeframes", symbol, written)
    return written >= 4  # STKT: 1m/5m of thin names may legitimately be empty; 15m+1h+4h+D are the backtest-critical frames

async def main():
    # Collect native OHLCV from 04:00 ET premarket through the exchange close.
    # The 15m forward clock and NPZ decision rows remain RTH-only.
    # Outside the data session, run ONE repair pass per day that re-fetches the trailing 7 days and replaces
    # provisional / mislabeled rows with the authoritative Time&Sales bars.
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    session = stock_session_bounds(now_et.date())
    premarket_open = ET.localize(datetime.combine(now_et.date(), dt_time(4, 0))).timestamp()
    market_open = session is not None and premarket_open <= now_utc.timestamp() < session[1]
    repair = False
    if not market_open:
        marker = config.KLINES_CACHE_DIR / f".stkt_repair_{now_utc.strftime('%Y%m%d')}"
        after_close = now_et.weekday() >= 5 or (session is not None and now_utc.timestamp() >= session[1]) or (session is None and now_et.time() >= dt_time(16, 0))
        if after_close and not marker.exists():
            repair = True
        else:
            logger.info("Outside stock data hours (%s ET); no klines added", now_et.strftime("%H:%M"))
            return
    await api_client.connect()
    try:
        with open(config.SYMBOLS_FILE, encoding="utf-8") as handle: symbols = [str(s).upper() for s in json.load(handle)]
        sem = asyncio.Semaphore(16 if repair else 64)
        async def run(symbol):
            async with sem:
                try: return await process_symbol(symbol, repair=repair)
                except Exception as exc: logger.error("%s failed: %s", symbol, exc); return False
        results = await asyncio.gather(*(run(s) for s in symbols))
        logger.info("Tradier stock kline %s cycle complete: %d/%d symbols with all 6 timeframes", "REPAIR" if repair else "PREMARKET+RTH", sum(results), len(results))
        if repair and sum(results) >= 0.9 * len(results):
            try: marker.write_text(now_utc.isoformat())
            except OSError: pass
    finally: await api_client.close()

if __name__ == "__main__": asyncio.run(main())
