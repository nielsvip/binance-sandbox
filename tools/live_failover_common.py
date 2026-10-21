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
import math
import os
import re
import time
from datetime import datetime, timezone, timedelta, time as dt_time
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

KLINE_STALE_S = float(os.environ.get("LF_KLINE_STALE_S", "600"))  # USER 2026-10-14: 180 -> 600 ("we are not trading <15m tf's"); 180 flapped failover 16x/day
SNAPSHOT_STALE_S = float(os.environ.get("LF_SNAPSHOT_STALE_S", "90"))  # USER 2026-10-15: 600 -> 90 "ez_indicators
# has to start earlier, as soon as market_data files stop coming in". This is the CLEAN signal — the write age of
# the newest Mac-own market_data_*.json — and it drives the DATA failover only (live_failover_s1.data_failover
# goes ACTIVE on the FIRST stale reading, no confirm), so S1 has fresh indicators ready within ~90 s + 1 tick
# instead of 10 minutes. Starting a producer is cheap and reversible, so eagerness here costs nothing.
# KLINE_STALE_S / CRYPTO_3M_STALE_S deliberately stay at 600: those are the noisy median-bars-behind measures
# that flapped the failover 16x/day at 180 (USER 2026-10-14). Moving TRADING still needs the heartbeat absent
# (LF_TAKEOVER_HB_S) plus LF_TAKEOVER_CONFIRM consecutive ticks — data staleness alone never moves trading.
TRADING_SNAPSHOT_STALE_S = float(os.environ.get("LF_TRADING_SNAPSHOT_STALE_S", "420"))  # 2026-10-19 USER
# ("if macbook goes offline or ... stops generating market data for any reason at all s1 has to kick in immediately
# ... and stop again immediately when macbook comes back"): the 90 s bar above is deliberately eager because starting
# an S1 PRODUCER is cheap and reversible. MOVING OR HOLDING THE TRADING LEASE is not: 90 s is inside the normal
# dedupe-gated write cadence of latest_market_data.json (measured 2026-10-19 21:0x-21:2xZ: 60-140 s between writes,
# one 13-min gap), so a 90 s trading bar means the Mac is "stale" at random and can never prove itself healthy
# enough to take its own venue back. 420 s = ~3x the worst normal gap and still well inside the 600 s the
# not-trading kinds allow. Only the SNAPSHOT bar is relaxed: every other staleness reason (klines behind, ban
# lines, producer errors) blocks the trading lease exactly as before.
CRYPTO_3M_STALE_S = float(os.environ.get("LF_CRYPTO_3M_STALE_S", "600"))  # USER 2026-10-14: 600 (no <15m trading)
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
    """Write age of the freshest Mac-own crypto indicator snapshot.

    2026-10-19: this used to scan ONLY market_data_<ts>.json, which ez_indicators writes as an
    HOURLY BACKUP (`backup_file`, and not at all when WORKER_TOTAL_INSTANCES > 1). The live snapshot
    every engine actually reads is latest_market_data.json (config.LATEST_MARKET_DATA_FILE), rewritten
    every ~60-140 s. Measured against SNAPSHOT_STALE_S=90 the hourly backup made the Mac read "stale"
    for ~98% of every hour even with klines 0 s behind and zero ban lines, so live_failover_s1's
    `healthy` gate could never be satisfied and the Mac could never get its trading lease back
    (2026-10-19 22:2xZ: holder=s1, readings.crypto.stale=true, sole reason snapshot_age=102s, while
    latest_market_data.json was 79 s old and k15/k3 were 0 s behind). Both files now count and the
    FRESHEST wins: a genuinely stalled producer still ages both (the 21:34->22:27Z indicators stall
    showed up in latest_market_data.json too), so nothing is hidden — only the hourly artefact is."""
    newest = 0.0
    try:
        newest = max(newest, (Path(data_dir) / "latest_market_data.json").stat().st_mtime)
    except OSError:
        pass
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
        return (now - newest) if newest else None
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
    trading_reasons = []
    if snap_age is None or snap_age > SNAPSHOT_STALE_S:
        reasons.append(f"snapshot_age={None if snap_age is None else int(snap_age)}s")
    if snap_age is None or snap_age > TRADING_SNAPSHOT_STALE_S:
        trading_reasons.append(f"snapshot_age={None if snap_age is None else int(snap_age)}s")
    if k15["n"] and k15["median_behind_s"] > KLINE_STALE_S:
        reasons.append(f"15m_behind={int(k15['median_behind_s'])}s")
    if k3["n"] and k3["median_behind_s"] > CRYPTO_3M_STALE_S:
        reasons.append(f"3m_behind={int(k3['median_behind_s'])}s")
    if not k15["n"] and not k3["n"]:
        reasons.append("no_own_klines")
    if bans:
        reasons.append(f"producer_ban_lines={bans}")
    trading_reasons += [r for r in reasons if not r.startswith("snapshot_age=")]
    return {"applicable": True, "stale": bool(reasons), "reasons": reasons, "snapshot_age_s": snap_age,
            "trading_stale": bool(trading_reasons), "trading_reasons": trading_reasons,
            "k15": k15, "k3": k3, "producer_ban_lines": bans, "n_symbols": len(symbols)}


def measure_stocks(base, now=None, tradier_klines_log=None, delivered=None):
    now = now_s() if now is None else now
    base = Path(base)
    if not stocks_applicable(now):
        return {"applicable": False, "stale": False, "trading_stale": False, "reasons": ["market_closed"]}
    session = nyse_session(now)
    symbols = symbols_for(base, STOCK_ACCOUNTS)
    exclude = (lambda path: str(path) in delivered) if delivered else None
    kdir = base / "klines_cache" / "tradier"
    k5 = kline_lag(kdir, symbols, "5m", now, session, exclude)
    k15 = kline_lag(kdir, symbols, "15m", now, session, exclude)
    ind_path = base / "data" / "tradier" / "tradier_indicators_latest.json"
    ind_age = None if (delivered and str(ind_path) in delivered) else file_age(ind_path, now)
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
    trading_reasons = [r for r in reasons if not r.startswith("indicators_age=")]
    if ind_age is None or ind_age > TRADING_SNAPSHOT_STALE_S:
        trading_reasons.append(f"indicators_age={None if ind_age is None else int(ind_age)}s")
    return {"applicable": True, "stale": bool(reasons), "reasons": reasons, "indicators_age_s": ind_age,
            "trading_stale": bool(trading_reasons), "trading_reasons": trading_reasons,
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
            staged_rows = json.loads(payload)
        except (OSError, ValueError):
            continue
        # USER 2026-10-21 "NOTHING destroys klines": a newer tail used to REPLACE the whole file, dropping every dest bar
        # the staged copy lacked (shorter history). Now the staged bars newer than dest's newest are APPENDED to dest's
        # own rows; a dest that is unreadable / not a list is still replaced (that is a repair, not a loss).
        try:
            with open(dest, "rb") as handle:
                dest_rows = json.loads(handle.read()) if dest_ts is not None else None
        except (OSError, ValueError):
            dest_rows = None
        if isinstance(dest_rows, list) and dest_rows and isinstance(staged_rows, list):
            cut = str(dest_rows[-1].get("timestamp", ""))[:19].replace(" ", "T") if isinstance(dest_rows[-1], dict) else ""
            add = [r for r in staged_rows if isinstance(r, dict) and str(r.get("timestamp", ""))[:19].replace(" ", "T") > cut]
            if not add:
                continue
            payload = json.dumps(dest_rows + add).encode()
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


def write_identity_seed(account_dir, position_files, source):
    """USER 2026-10-14 "THE PULL SHOWS YOU WHEN IT OPENED": hand the next single writer the real opened_at of
    every HELD position from the last authoritative writer's files. Writes ONLY <acct>/.identity_seed.json
    (consumed by position_writer_guard.seeded_disk_rows); never touches a *_positions.json file."""
    rows = {}
    for path in position_files:
        data = read_json(path, {}) or {}
        for key, row in data.items():
            try:
                held = isinstance(row, dict) and abs(float(row.get("positionAmt") or 0)) > 0 and row.get("opened_at")
            except (TypeError, ValueError):
                held = False
            if held:
                rows[key] = {field: row.get(field) for field in ("opened_at", "entry_price", "max_gain", "positionAmt", "symbol", "position_side") if field in row}
    payload = {"written_epoch": time.time(), "written_at": iso(time.time()), "source": source, "rows": rows}
    atomic_write_json(Path(account_dir) / ".identity_seed.json", payload)
    return len(rows)


# ---------------------------------------------------------------- TRADING HEALTH (USER 2026-10-14)
# "s1 is supposed to take over immediately if macbook stops trading ... it should also detect other reasons (like ip bans)".
# Measured on the Mac from the logs its live processes already write (no extra broker calls, no API weight):
#   crypto : per account the single position writer's REST poll (ez_positions_service_<acct>.log + the failover writer
#            ez_positions_realtime_<acct>.log): "FETCHED and SYNCED" = broker reachable, "Fetch error" = failed poll
#            (-2015/401 IP-or-key, 418/-1003 ban, 429 rate limit). ez_manage_<acct>.log: OPENs refused with
#            BLOCKED_POSITIONS_BROKER_UNREACHABLE, POSITIONS_BROKER_FETCH_FAILED. ez_manage process alive.
#   stocks : (NYSE session only) per account tradier_manage_<acct>.log: ORDER_STALL_SKIP (order exceeded 120 s), LOOP_LAG
#            "event loop frozen Xs", ORDER_RESULT ... SUBMITTED (an order reached the broker), POSITIONS_BROKER_FETCH_FAILED,
#            401/Unauthorized; tradier_manage process alive; log silence while alive (frozen).
# Venue kinds: broker_unreachable | order_path_dead | traders_dead. S1 (tools/live_failover_s1.py) turns a sustained kind
# into a venue takeover after verifying its OWN broker access.
BROKER_DEAD_S = float(os.environ.get("LF_BROKER_DEAD_S", "120"))
BROKER_ERR_MIN = int(os.environ.get("LF_BROKER_ERR_MIN", "3"))
BROKER_DEAD_FRAC = float(os.environ.get("LF_BROKER_DEAD_FRAC", "0.6"))
ORDER_WINDOW_S = float(os.environ.get("LF_ORDER_WINDOW_S", "300"))
LOOP_LAG_DEAD_S = float(os.environ.get("LF_LOOP_LAG_DEAD_S", "60"))
STOCK_SILENT_S = float(os.environ.get("LF_STOCK_SILENT_S", "180"))
ORDER_DEAD_FRAC = float(os.environ.get("LF_ORDER_DEAD_FRAC", "0.5"))
# 2026-10-16 USER 3-MINUTE INVARIANT, per-account escalation (see the account_dead kind in TradingHealth._crypto).
# MANAGE_SILENT_S: an ez_manage that is alive but has written nothing for this long is WEDGED, i.e. dead for our
# purposes (a busy engine still logs every ~60-90 s, so 180 s is unambiguous).
# ACCT_DEAD_S: how long ONE account must stay dead before S1 is told. Deliberately above the Mac's local repair path
# (mac_pos_writer_keepalive.sh runs */1 min and replaces a childless wrapper at 45 s / a silent engine at 150 s), so
# this fires only after local repair has already had several chances and failed — it escalates, it does not race.
MANAGE_SILENT_S = float(os.environ.get("LF_MANAGE_SILENT_S", "180"))
ACCT_DEAD_S = float(os.environ.get("LF_ACCT_DEAD_S", "240"))
HEALTH_KEEP_S = 3600.0
# 2026-10-17 ANTI-FLAP (USER "no flapping allowed you need to find and fix this"). A freshly (re)started engine was
# judged against log history that PREDATES it, so every takeover manufactured the condition for the next one:
#   takeover -> Mac fenced -> its engines killed -> engines restart -> the new process has no successful broker poll
#   yet, so `last_ok is None` and no_ok_s falls back to `now - min(first_seen)` off the OLD log tail -> that is
#   instantly >= BROKER_DEAD_S -> broker_unreachable -> takeover again.
# MEASURED 2026-10-17: lease gen 23 -> 31 in ~45 min, and at 03:44Z BOTH hosts sat at 0 engines with 39 open
# positions. The same lag produced a bogus `men engine_dead_s=1238` on an engine that had restarted 60 s earlier
# (manage_log_silent_s is measured from the last LOG LINE, and ez_manage takes ~80 s to initialise).
# An engine younger than this grace is STARTING, not dead: it cannot be counted broker_dead or engine_dead. The
# value must exceed ez_manage's ~80 s startup AND BROKER_DEAD_S (120 s), or a restart is still judged mid-boot.
ENGINE_STARTUP_GRACE_S = float(os.environ.get("LF_ENGINE_STARTUP_GRACE_S", "240"))


def parse_etime(text):
    """`ps -o etime` -> seconds. Formats: SS, MM:SS, HH:MM:SS, DD-HH:MM:SS. None when unparseable.
    etime (not etimes) because macOS ps silently ignores etimes while Linux supports both."""
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        try:
            days = int(head)
        except ValueError:
            return None
    try:
        parts = [int(p) for p in text.split(":")]
    except ValueError:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    return days * 86400 + parts[-3] * 3600 + parts[-2] * 60 + parts[-1]
VENUES = ("crypto", "stocks")
STOCK_SCRIPTS = ("tradier_manage.py", "tradier_positions.py")
_TS_FULL = re.compile(r"^\[?(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)")
_TS_MD = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d) - ")
_TS_D = re.compile(r"^(\d\d) (\d\d):(\d\d):(\d\d) - ")
_LAG_RX = re.compile(r"event loop frozen ([0-9.]+)s")
AUTH_RX = re.compile(r"-2015|Invalid API-key|status 401|<Response \[401\]>|\b401\b.*Unauthori|Unauthorized", re.I)
BANLINE_RX = re.compile(r"\b418\b|-1003|banned until|Way too many requests")
RATE_RX = re.compile(r"\b429\b|Too Many Requests|-1015")


def script_venue(script):
    return "stocks" if os.path.basename(str(script)) in STOCK_SCRIPTS else "crypto"


def lease_venues(lease):
    """Venues a holder=s1 lease covers. A lease without the field (pre-2026-10-14 / heartbeat-absent takeover) = all."""
    raw = (lease or {}).get("venues")
    if raw is None or raw == "":
        return list(VENUES) if raw is None else []
    return [v for v in VENUES if v in str(raw).split(",")]


def venues_str(venues):
    return ",".join(v for v in VENUES if v in set(venues))


def line_epoch(line, now):
    """Epoch of a log line in any of the live formats: [YYYY-MM-DD HH:MM:SS..., 'MM-DD HH:MM:SS - ' (tradier_manage),
    'DD HH:MM:SS - ' (ez_manage). Year/month are inferred from `now` (all logs are UTC on the Mac)."""
    ref = datetime.fromtimestamp(now, tz=timezone.utc)
    try:
        m = _TS_FULL.match(line)
        if m:
            return datetime(*map(int, m.groups()), tzinfo=timezone.utc).timestamp()
        m = _TS_MD.match(line)
        if m:
            mo, d, hh, mi, ss = map(int, m.groups())
            year = ref.year - (1 if mo > ref.month else 0)
            return datetime(year, mo, d, hh, mi, ss, tzinfo=timezone.utc).timestamp()
        m = _TS_D.match(line)
        if m:
            d, hh, mi, ss = map(int, m.groups())
            year, mo = ref.year, ref.month
            if d > ref.day:
                mo -= 1
                if mo == 0:
                    year, mo = year - 1, 12
            return datetime(year, mo, d, hh, mi, ss, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None
    return None


def classify_error(text):
    if AUTH_RX.search(text):
        return "auth_ip"
    if BANLINE_RX.search(text):
        return "ban"
    if RATE_RX.search(text):
        return "rate"
    return "other"


def _crypto_pos_events(line):
    if "FETCHED and SYNCED" in line:
        return [("ok", None)]
    if "Fetch error" in line:
        return [("err", classify_error(line))]
    return []


def _crypto_manage_events(line):
    out = []
    if "BLOCKED_POSITIONS_BROKER_UNREACHABLE" in line and "WH_TRACE_2_done" in line:
        out.append(("blocked", None))
    if "POSITIONS_BROKER_FETCH_FAILED" in line:
        out.append(("fetch_failed", classify_error(line)))
    return out


def _stock_events(line):
    out = []
    if "[ORDER_STALL_SKIP]" in line:
        out.append(("stall", None))
    if "[LOOP_LAG]" in line:
        m = _LAG_RX.search(line)
        if m:
            out.append(("lag", float(m.group(1))))
    if "[ORDER_RESULT]" in line and "SUBMITTED" in line:
        out.append(("submitted", None))
    if "POSITIONS_BROKER_FETCH_FAILED" in line:
        out.append(("fetch_failed", classify_error(line)))
    elif "tradier" in line.lower() and AUTH_RX.search(line):
        out.append(("auth", None))
    return out


class LogFollower:
    """Incremental tail of one log: first read = last `initial_bytes`, then only appended bytes (rotation/truncation aware).
    Keeps (epoch, kind, value) events for HEALTH_KEEP_S plus the last epoch per kind and of any timestamped line."""

    def __init__(self, path, extractor, initial_bytes=524288):
        self.path = Path(path)
        self.extractor = extractor
        self.initial_bytes = initial_bytes
        self.offset = None
        self.inode = None
        self.partial = b""
        self.events = []
        self.last = {}
        self.last_any = None
        self.first_seen = None

    def poll(self, now):
        try:
            st = self.path.stat()
        except OSError:
            return self
        if self.inode != st.st_ino or self.offset is None or st.st_size < self.offset:
            self.inode, self.partial = st.st_ino, b""
            self.offset = max(0, st.st_size - self.initial_bytes) if self.offset is None else 0
            skip_first = self.offset > 0
        else:
            skip_first = False
        if st.st_size > self.offset:
            try:
                with open(self.path, "rb") as handle:
                    handle.seek(self.offset)
                    chunk = handle.read(min(st.st_size - self.offset, 8 * 1048576))
            except OSError:
                return self
            self.offset += len(chunk)
            data = self.partial + chunk
            lines = data.split(b"\n")
            self.partial = lines.pop()
            if skip_first and lines:
                lines = lines[1:]
            for raw in lines:
                line = raw.decode("utf-8", "replace")
                stamp = line_epoch(line, now)
                if stamp is None:
                    continue
                self.last_any = stamp if self.last_any is None else max(self.last_any, stamp)
                if self.first_seen is None:
                    self.first_seen = stamp
                for kind, value in self.extractor(line):
                    self.events.append((stamp, kind, value))
                    self.last[kind] = max(self.last.get(kind, 0.0), stamp)
        cutoff = now - HEALTH_KEEP_S
        if self.events and self.events[0][0] < cutoff:
            self.events = [e for e in self.events if e[0] >= cutoff]
        return self

    def window(self, now, seconds, kind):
        return [e for e in self.events if e[1] == kind and now - e[0] <= seconds]


def trader_procs(ps_text=None):
    """{'crypto': {acct: n}, 'stocks': {acct: n}} from ONE `ps` call (live traders are `... ez_manage.py --account X`),
    plus '_ages': {'crypto': {acct: oldest_engine_age_s}, 'stocks': {...}} used for the restart grace (see
    ENGINE_STARTUP_GRACE_S). '_ages' is an extra key, so existing `procs.get("crypto")` callers are unaffected."""
    if ps_text is None:
        try:
            import subprocess
            ps_text = subprocess.run(["ps", "-axo", "pid=,etime=,command="], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            ps_text = ""
    out = {"crypto": {a: 0 for a in CRYPTO_ACCOUNTS}, "stocks": {a: 0 for a in STOCK_ACCOUNTS},
           "_ages": {"crypto": {}, "stocks": {}}}
    for line in ps_text.splitlines():
        if "python" not in line:
            continue
        # `pid etime command`; etime is absent when the caller passed a pid=,command= ps_text (tests) — then age is None
        age = None
        fields = line.split(None, 2)
        if len(fields) >= 3 and ":" in fields[1]:
            age = parse_etime(fields[1])
        for venue, pat in (("crypto", r"ez_manage\.py --account (\w+)"), ("stocks", r"tradier_manage\.py --accounts (\w+)")):
            m = re.search(pat, line)
            if m and m.group(1) in out[venue]:
                acct = m.group(1)
                out[venue][acct] += 1
                if age is not None:
                    prev = out["_ages"][venue].get(acct)
                    out["_ages"][venue][acct] = age if prev is None else max(prev, age)
                break
    return out


class TradingHealth:
    """Mac-side trading-health meter (one instance lives in the Mac agent so the log tails stay incremental)."""

    def __init__(self, logdir):
        logdir = Path(logdir)
        self.crypto = {a: {"svc": LogFollower(logdir / f"ez_positions_service_{a}.log", _crypto_pos_events),
                           "rt": LogFollower(logdir / f"ez_positions_realtime_{a}.log", _crypto_pos_events),
                           "mgr": LogFollower(logdir / f"ez_manage_{a}.log", _crypto_manage_events, 1048576)} for a in CRYPTO_ACCOUNTS}
        self.stocks = {a: LogFollower(logdir / f"tradier_manage_{a}.log", _stock_events, 1048576) for a in STOCK_ACCOUNTS}
        self._engine_dead_since = {}

    def _engine_dead_s(self, acct, now, n_alive, mgr_last_any, starting=False):
        """Seconds this account's DECISION ENGINE has been continuously dead, 0 when healthy. Dead = no ez_manage
        process at all, or a process that has written nothing for MANAGE_SILENT_S (wedged). Measured here, per
        account, because nothing else was: traders_dead needs all five gone and broker_unreachable needs 3 of 5."""
        # ANTI-FLAP: a just-restarted engine has not had time to write its first log line, and silence is
        # measured from the last LINE — without this it reads as long-dead the instant it comes back.
        dead = (not starting) and (n_alive <= 0 or (mgr_last_any is not None and now - mgr_last_any >= MANAGE_SILENT_S))
        if not dead:
            self._engine_dead_since.pop(acct, None)
            return 0.0
        since = self._engine_dead_since.setdefault(acct, now)
        return round(max(0.0, now - since), 1)

    def measure(self, now=None, procs=None, fenced=()):
        now = now_s() if now is None else now
        procs = trader_procs() if procs is None else procs
        _ages = procs.get("_ages") or {}
        return {"crypto": self._crypto(now, procs.get("crypto", {}), "crypto" in fenced, _ages.get("crypto") or {}),
                "stocks": self._stocks(now, procs.get("stocks", {}), "stocks" in fenced, _ages.get("stocks") or {}),
                "measured_at": iso(now)}

    def _crypto(self, now, alive, fenced, ages=None):
        ages = ages or {}
        accounts, dead, considered = {}, [], 0
        for acct, f in self.crypto.items():
            for follower in f.values():
                follower.poll(now)
            svc, rt, mgr = f["svc"], f["rt"], f["mgr"]
            oks = [x for x in (svc.last.get("ok"), rt.last.get("ok")) if x]
            last_ok = max(oks) if oks else None
            errs = [e for e in svc.events + rt.events if e[1] == "err"]
            consec = sum(1 for e in errs if last_ok is None or e[0] > last_ok)
            seen = [x for x in (svc.first_seen, rt.first_seen) if x]
            anys = [x for x in (svc.last_any, rt.last_any) if x]
            if last_ok is not None:
                no_ok_s = now - last_ok
            elif seen:
                no_ok_s = now - min(seen)  # lower bound: no success anywhere in the tail
            else:
                no_ok_s = None
            silent_s = (now - max(anys)) if anys else None
            classes = {}
            for e in errs:
                if now - e[0] <= ORDER_WINDOW_S:
                    classes[e[2]] = classes.get(e[2], 0) + 1
            n_alive = int(alive.get(acct, 0))
            active = n_alive > 0 or (silent_s is not None and silent_s < HEALTH_KEEP_S)
            # ANTI-FLAP: an engine younger than the grace is STARTING, not broker-dead (see ENGINE_STARTUP_GRACE_S).
            _age = ages.get(acct)
            starting = _age is not None and _age < ENGINE_STARTUP_GRACE_S
            broker_dead = bool(active and not starting and no_ok_s is not None and no_ok_s >= BROKER_DEAD_S and (consec >= BROKER_ERR_MIN or (silent_s is not None and silent_s >= BROKER_DEAD_S)))
            considered += 1 if active else 0
            if broker_dead:
                dead.append(acct)
            accounts[acct] = {"trader_alive": n_alive, "last_broker_ok_age_s": None if last_ok is None else round(now - last_ok, 1),
                              "no_broker_ok_s": None if no_ok_s is None else round(no_ok_s, 1), "consec_broker_errors": consec,
                              "writer_log_silent_s": None if silent_s is None else round(silent_s, 1), "error_classes_window": classes,
                              "opens_blocked_broker_unreachable_window": len(mgr.window(now, ORDER_WINDOW_S, "blocked")),
                              "manage_broker_fetch_failed_window": len(mgr.window(now, ORDER_WINDOW_S, "fetch_failed")), "broker_dead": broker_dead,
                              "manage_log_silent_s": None if mgr.last_any is None else round(now - mgr.last_any, 1),
                              "engine_dead_s": self._engine_dead_s(acct, now, n_alive, mgr.last_any, starting)}
        kinds, reasons = [], []
        # 2026-10-15 SELF-LOCKING FENCE (USER live takeover test): a FENCED venue is deliberately not trading, so
        # its silence is never a fault. Once ez_positions_service_main/ez_positions_realtime joined the fence list,
        # the fenced Mac stopped polling the broker and this meter reported "broker_unreachable 5/5 accounts" about
        # a perfectly healthy Mac. live_failover_s1 feeds those kinds into nt_left, and failback requires nt_left to
        # be empty -> the Mac could NEVER come back: fenced, therefore faulty, therefore keep it fenced. Only
        # traders_dead was guarded by `not fenced`; every kind must be.
        need = max(1, math.ceil(BROKER_DEAD_FRAC * considered - 1e-9)) if considered else 0
        if not fenced and considered and len(dead) >= need:
            kinds.append("broker_unreachable")
            reasons.append(f"broker_unreachable {len(dead)}/{considered} accounts ({','.join(dead)}) no successful broker poll >= {int(BROKER_DEAD_S)}s")
        traders = sum(1 for a in accounts.values() if a["trader_alive"])
        if not fenced and traders == 0:
            kinds.append("traders_dead")
            reasons.append("no crypto ez_manage alive")
        # 2026-10-16 USER ("NO account can be inactive for >3min without s1 kicking in"): PER-ACCOUNT blind spot.
        # Before this, a single stopped account was invisible to every cross-host detector. traders_dead needs ALL
        # FIVE engines gone; broker_unreachable needs BROKER_DEAD_FRAC=0.6 of them, i.e. need=3 of 5 — so one or two
        # accounts could sit dead forever and S1 would never learn of it (the Mac's own heartbeat kept reporting
        # not_trading=false). engine_dead_s is measured per account from the ez_manage process count AND its log
        # write age, so a WEDGED engine (alive, silent) counts as dead too. ANY ONE account over the bar raises the
        # kind. The bar is deliberately LONGER than the Mac's local repair path (mac_pos_writer_keepalive.sh, */1 min,
        # MAX_MGR_NOCHILD=45 s / MAX_MGR_LOG_AGE=150 s): this is the ESCALATION for when local repair has already
        # failed several times, not a competitor to it, which is why it cannot flap on an ordinary restart.
        acct_dead = sorted(a for a, v in accounts.items() if (v.get("engine_dead_s") or 0) >= ACCT_DEAD_S)
        if not fenced and traders and acct_dead:
            kinds.append("account_dead")
            reasons.append(f"account_dead {len(acct_dead)}/{len(accounts)} ({','.join(acct_dead)}) no ez_manage process and/or no engine log line for >= {int(ACCT_DEAD_S)}s while other accounts still trade — local repair failed")
        return {"applicable": True, "fenced": fenced, "not_trading": bool(kinds), "kinds": kinds, "reasons": reasons,
                "traders_alive": traders, "accounts_considered": considered, "broker_dead_accounts": dead,
                "account_dead_accounts": acct_dead, "accounts": accounts}

    def _stocks(self, now, alive, fenced, ages=None):
        ages = ages or {}
        for follower in self.stocks.values():
            follower.poll(now)
        if not stocks_applicable(now):
            return {"applicable": False, "fenced": fenced, "not_trading": False, "kinds": [], "reasons": ["market_closed"]}
        accounts, opd, bun, considered = {}, [], [], 0
        for acct, f in self.stocks.items():
            n_alive = int(alive.get(acct, 0))
            stalls = len(f.window(now, ORDER_WINDOW_S, "stall"))
            lags = [e[2] for e in f.window(now, ORDER_WINDOW_S, "lag")]
            submitted = len(f.window(now, ORDER_WINDOW_S, "submitted"))
            fetch_failed = len(f.window(now, ORDER_WINDOW_S, "fetch_failed"))
            auth = len(f.window(now, ORDER_WINDOW_S, "auth")) + sum(1 for e in f.window(now, ORDER_WINDOW_S, "fetch_failed") if e[2] == "auth_ip")
            silent_s = (now - f.last_any) if f.last_any else None
            last_sub = f.last.get("submitted")
            lag_max = max(lags) if lags else 0.0
            # ANTI-FLAP (same reasoning as crypto): a just-restarted stock trader has submitted nothing YET;
            # judging it before ENGINE_STARTUP_GRACE_S makes every takeover manufacture the next one.
            _age = ages.get(acct)
            starting = _age is not None and _age < ENGINE_STARTUP_GRACE_S
            order_dead = bool(n_alive and not starting and submitted == 0 and (stalls >= 1 or lag_max >= LOOP_LAG_DEAD_S or (silent_s is not None and silent_s >= STOCK_SILENT_S)))
            broker_bad = bool(n_alive and not starting and submitted == 0 and (auth >= 1 or fetch_failed >= BROKER_ERR_MIN))
            considered += 1 if n_alive else 0
            if order_dead:
                opd.append(acct)
            if broker_bad:
                bun.append(acct)
            accounts[acct] = {"trader_alive": n_alive, "order_stall_skips_window": stalls, "loop_lag_max_s_window": lag_max,
                              "orders_submitted_window": submitted, "last_order_submitted_age_s": None if last_sub is None else round(now - last_sub, 1),
                              "broker_fetch_failed_window": fetch_failed, "auth_errors_window": auth,
                              "log_silent_s": None if silent_s is None else round(silent_s, 1), "order_path_dead": order_dead, "broker_unreachable": broker_bad}
        kinds, reasons = [], []
        need = max(1, math.ceil(ORDER_DEAD_FRAC * considered - 1e-9)) if considered else 0
        if not fenced and considered and len(bun) >= need:
            kinds.append("broker_unreachable")
            reasons.append(f"broker_unreachable {len(bun)}/{considered} accounts ({','.join(bun)})")
        if not fenced and considered and len(opd) >= need:
            kinds.append("order_path_dead")
            reasons.append(f"order_path_dead {len(opd)}/{considered} accounts ({','.join(opd)}): 0 orders reached the broker in {int(ORDER_WINDOW_S)}s with stalls/loop lag >= {int(LOOP_LAG_DEAD_S)}s/silence")
        if not fenced and considered == 0:
            kinds.append("traders_dead")
            reasons.append("no tradier_manage alive in session")
        return {"applicable": True, "fenced": fenced, "not_trading": bool(kinds), "kinds": kinds, "reasons": reasons,
                "traders_alive": considered, "accounts": accounts}
