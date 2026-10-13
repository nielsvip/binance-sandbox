#!/usr/bin/env python3
"""Shared helpers for the LIVE FAILOVER S1 system (2026-10-13 USER: "tradier_indicators and
ez_indicators on s1 kick in immediately if the live trading has >3min of no latest data ...
if the macbook is not producing new klines or indicators for either cat, the entire trading
system in /binance/ is started immediately and follows seamlessly").

Stdlib only (runs under the Mac conda python and the S1 conda python). Used by
tools/mac_failover_agent.py (Mac) and tools/live_failover_s1.py (S1). See BACKTEST_BIBLE.md
"LIVE FAILOVER S1 (2026-10-13)".

Freshness model (per venue):
  crypto  : Mac-own indicator snapshot write age (newest market_data_*.json, not *_s1 / worker)
            > SNAPSHOT_STALE_S, OR median over live crypto symbols of "15m bars behind the last
            closed 15m bar" > KLINE_STALE_S, OR median 3m behind > CRYPTO_3M_STALE_S (Mac writes
            3m bars in batches of up to 3), OR Binance ban/rate-limit lines (418/429/-1003) in
            the producer log within PRODUCER_LOOKBACK_S.
  stocks  : only inside the NYSE session (holiday aware when pandas_market_calendars or
            exchange_calendars is importable). Median 5m/15m behind the last closed session bar
            > KLINE_STALE_S, OR tradier_indicators_latest.json write age > SNAPSHOT_STALE_S, OR
            >= TRADIER_ERR_MIN fetch timeout/429 lines in the producer log within the lookback.
"behind" = (close time of the last bar that has closed by now) - (close time of the newest
bar in the file), clamped at 0; bar timestamps are OPEN times (klines_cache convention).
"""
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta, time as dt_time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

KLINE_STALE_S = float(os.environ.get("LF_KLINE_STALE_S", "180"))
SNAPSHOT_STALE_S = float(os.environ.get("LF_SNAPSHOT_STALE_S", "180"))
CRYPTO_3M_STALE_S = float(os.environ.get("LF_CRYPTO_3M_STALE_S", "540"))
PRODUCER_LOOKBACK_S = float(os.environ.get("LF_PRODUCER_LOOKBACK_S", "180"))
TRADIER_ERR_MIN = int(os.environ.get("LF_TRADIER_ERR_MIN", "5"))
CRYPTO_ACCOUNTS = ("ang", "inf", "flz", "men", "fin")
STOCK_ACCOUNTS = ("trb", "trc", "tra")
TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
CRYPTO_DELIVER_TFS = ("3m", "15m", "1h", "4h")
STOCK_DELIVER_TFS = ("1m", "5m", "15m", "1h")
BAN_RX = re.compile(r"status 418|status 429|\"code\":-1003|banned until|RATE-LIMIT CIRCUIT BREAKER|Way too many requests")
TRADIER_ERR_RX = re.compile(r"fetch failed/timeout|status 429|Too Many Requests|rate limit", re.I)
LOG_TS_RX = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
TS_RX = re.compile(rb'"timestamp"\s*:\s*"([^"]+)"')
_ET = ZoneInfo("America/New_York") if ZoneInfo else None


def now_s():
    return float(os.environ.get("LF_FAKE_NOW") or time.time())


def iso(ts=None):
    return datetime.fromtimestamp(now_s() if ts is None else ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value):
    try:
        text = value.decode() if isinstance(value, bytes) else str(value)
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def newest_bar_open(path):
    """Open time (epoch) of the LAST row of a klines_cache JSON list, read from the file tail."""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 1024))
            tail = handle.read()
    except OSError:
        return None
    found = TS_RX.findall(tail)
    return parse_ts(found[-1]) if found else None


def symbols_for(base, accounts):
    symbols = set()
    for account in accounts:
        for side in ("long", "short"):
            data = read_json(Path(base) / f"symbols_{account}_{side}.json", [])
            if isinstance(data, dict):
                data = data.get("symbols", list(data.keys()))
            if isinstance(data, list):
                symbols.update(str(item) for item in data if isinstance(item, str))
    return sorted(symbols)


def _holiday(day):
    for loader in (_pmc_holiday, _xcals_holiday):
        verdict = loader(day)
        if verdict is not None:
            return verdict
    return False


_HOLIDAY_CACHE = {}


def _pmc_holiday(day):
    key = ("pmc", day)
    if key in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[key]
    try:
        import pandas_market_calendars as pmc
        schedule = pmc.get_calendar("NYSE").schedule(start_date=day.isoformat(), end_date=day.isoformat())
        verdict = schedule.empty
    except Exception:
        verdict = None
    _HOLIDAY_CACHE[key] = verdict
    return verdict


def _xcals_holiday(day):
    key = ("xcals", day)
    if key in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[key]
    try:
        import exchange_calendars as xcals
        verdict = not xcals.get_calendar("XNYS").is_session(day.isoformat())
    except Exception:
        verdict = None
    _HOLIDAY_CACHE[key] = verdict
    return verdict


def nyse_session(now=None):
    """(open_epoch, close_epoch) of today's NYSE regular session, or None (weekend/holiday)."""
    now = now_s() if now is None else now
    local = datetime.fromtimestamp(now, tz=_ET)
    if local.weekday() >= 5 or _holiday(local.date()):
        return None
    opened = datetime.combine(local.date(), dt_time(9, 30), tzinfo=_ET).timestamp()
    closed = datetime.combine(local.date(), dt_time(16, 0), tzinfo=_ET).timestamp()
    return opened, closed


def stocks_applicable(now=None, grace_s=None):
    """Stocks freshness is judged from open+5m+grace until close+5m (aware of closed market)."""
    now = now_s() if now is None else now
    grace_s = KLINE_STALE_S if grace_s is None else grace_s
    session = nyse_session(now)
    if not session:
        return False
    opened, closed = session
    return opened + 300 + grace_s <= now <= closed + 300


def last_closed_bar_close(now, tf, session=None):
    sec = TF_SECONDS[tf]
    if session is None:
        return (now // sec) * sec
    opened, closed = session
    if now < opened + sec:
        return None
    return min(closed, opened + ((min(now, closed) - opened) // sec) * sec)


def behind_seconds(path, tf, now, session=None):
    opened_at = newest_bar_open(path)
    if opened_at is None:
        return None
    last_close = last_closed_bar_close(now, tf, session)
    if last_close is None:
        return None
    return max(0.0, last_close - (opened_at + TF_SECONDS[tf]))


def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def kline_lag(kdir, symbols, tf, now, session=None, exclude=None):
    lags = []
    for symbol in symbols:
        path = Path(kdir) / f"{symbol}_{tf}.json"
        if exclude and exclude(path):
            continue
        lag = behind_seconds(path, tf, now, session)
        if lag is not None:
            lags.append(lag)
    return {"n": len(lags), "median_behind_s": median(lags), "n_behind_gt_180": sum(1 for lag in lags if lag > 180)}


def log_hits(path, rx, now, lookback_s=None):
    lookback_s = PRODUCER_LOOKBACK_S if lookback_s is None else lookback_s
    try:
        with open(path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 262144))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return 0
    hits = 0
    for line in reversed(lines):
        match = LOG_TS_RX.match(line)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if now - stamp > lookback_s:
            break
        if rx.search(line):
            hits += 1
    return hits


def newest_own_snapshot_age(data_dir, now):
    newest = 0.0
    try:
        with os.scandir(data_dir) as entries:
            for entry in entries:
                name = entry.name
                if not name.startswith("market_data_") or not name.endswith(".json"):
                    continue
                if name.endswith("_s1.json") or "worker" in name:
                    continue
                try:
                    newest = max(newest, entry.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        return None
    return (now - newest) if newest else None


def file_age(path, now):
    try:
        return now - Path(path).stat().st_mtime
    except OSError:
        return None


def measure_crypto(base, now=None, ez_klines_log=None, delivered=None):
    now = now_s() if now is None else now
    base = Path(base)
    symbols = [s for s in symbols_for(base, CRYPTO_ACCOUNTS)]
    exclude = (lambda path: str(path) in delivered) if delivered else None
    k15 = kline_lag(base / "klines_cache", symbols, "15m", now, exclude=exclude)
    k3 = kline_lag(base / "klines_cache", symbols, "3m", now, exclude=exclude)
    snap_age = newest_own_snapshot_age(base / "data", now)
    bans = log_hits(ez_klines_log, BAN_RX, now) if ez_klines_log else 0
    reasons = []
    if snap_age is None or snap_age > SNAPSHOT_STALE_S:
        reasons.append(f"snapshot_age={None if snap_age is None else int(snap_age)}s")
    if k15["n"] and k15["median_behind_s"] > KLINE_STALE_S:
        reasons.append(f"15m_behind={int(k15['median_behind_s'])}s")
    if k3["n"] and k3["median_behind_s"] > CRYPTO_3M_STALE_S:
        reasons.append(f"3m_behind={int(k3['median_behind_s'])}s")
    if not k15["n"] and not k3["n"]:
        reasons.append("no_own_klines")
    if bans:
        reasons.append(f"producer_ban_lines={bans}")
    return {"applicable": True, "stale": bool(reasons), "reasons": reasons, "snapshot_age_s": snap_age,
            "k15": k15, "k3": k3, "producer_ban_lines": bans, "n_symbols": len(symbols)}


def measure_stocks(base, now=None, tradier_klines_log=None, delivered=None):
    now = now_s() if now is None else now
    base = Path(base)
    if not stocks_applicable(now):
        return {"applicable": False, "stale": False, "reasons": ["market_closed"]}
    session = nyse_session(now)
    symbols = symbols_for(base, STOCK_ACCOUNTS)
    exclude = (lambda path: str(path) in delivered) if delivered else None
    kdir = base / "klines_cache" / "tradier"
    k5 = kline_lag(kdir, symbols, "5m", now, session, exclude)
    k15 = kline_lag(kdir, symbols, "15m", now, session, exclude)
    ind_age = file_age(base / "data" / "tradier" / "tradier_indicators_latest.json", now)
    errors = log_hits(tradier_klines_log, TRADIER_ERR_RX, now) if tradier_klines_log else 0
    reasons = []
    if k5["n"] and k5["median_behind_s"] > KLINE_STALE_S:
        reasons.append(f"5m_behind={int(k5['median_behind_s'])}s")
    if k15["n"] and k15["median_behind_s"] > KLINE_STALE_S:
        reasons.append(f"15m_behind={int(k15['median_behind_s'])}s")
    if not k5["n"] and not k15["n"]:
        reasons.append("no_own_klines")
    if ind_age is None or ind_age > SNAPSHOT_STALE_S:
        reasons.append(f"indicators_age={None if ind_age is None else int(ind_age)}s")
    if errors >= TRADIER_ERR_MIN:
        reasons.append(f"producer_error_lines={errors}")
    return {"applicable": True, "stale": bool(reasons), "reasons": reasons, "indicators_age_s": ind_age,
            "k5": k5, "k15": k15, "producer_error_lines": errors, "n_symbols": len(symbols)}


def apply_newer_klines(stage_dir, dest_dir, log_rows=None, provenance="S1"):
    """Atomically replace dest klines files with staged ones ONLY when the staged file's newest
    bar is strictly newer than the destination's. Never deletes, never touches other files.
    Returns {dest_path: newest_open} for every file replaced."""
    replaced = {}
    stage_dir = Path(stage_dir)
    dest_dir = Path(dest_dir)
    if not stage_dir.is_dir():
        return replaced
    dest_dir.mkdir(parents=True, exist_ok=True)
    for staged in sorted(stage_dir.glob("*.json")):
        staged_ts = newest_bar_open(staged)
        if staged_ts is None:
            continue
        dest = dest_dir / staged.name
        dest_ts = newest_bar_open(dest) if dest.exists() else None
        if dest_ts is not None and staged_ts <= dest_ts:
            continue
        try:
            with open(staged, "rb") as handle:
                payload = handle.read()
            json.loads(payload)
        except (OSError, ValueError):
            continue
        tmp = dest.with_name(f".{dest.name}.lf.{os.getpid()}.tmp")
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dest)
        replaced[str(dest)] = staged_ts
        if log_rows is not None:
            log_rows.append({"file": str(dest), "newest_bar": iso(staged_ts), "prev_newest_bar": iso(dest_ts) if dest_ts else None, "src": provenance})
    return replaced
