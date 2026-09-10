#!/usr/bin/env python3
"""Build append-only stock 1m bars from the continuously updated price file."""
import json, os, time, platform
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path('/home/niels/binance-sandbox') if platform.system() == 'Linux' else Path('/Users/niels/Documents/binance')
PRICE_FILE = BASE / 'data/tradier/tradier_prices_latest.json'
CACHE = BASE / 'klines_cache/tradier'
SYMBOLS = BASE / 'symbols_tradier.json'

def minute_key(ts):
    return ts.astimezone(timezone.utc).replace(second=0, microsecond=0)

def load_last(symbol):
    path = CACHE / f'{symbol}_1m.json'
    try:
        rows = json.loads(path.read_text())
        return rows if isinstance(rows, list) else []
    except Exception:
        return []

def atomic_write(path, rows):
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(rows, separators=(',', ':')))
    os.replace(tmp, path)

def flush(symbol, bar):
    if not bar:
        return
    path = CACHE / f'{symbol}_1m.json'
    rows = load_last(symbol)
    key = bar['timestamp']
    merged = {str(r.get('timestamp')): r for r in rows if isinstance(r, dict) and r.get('timestamp')}
    merged[key] = bar
    atomic_write(path, [merged[k] for k in sorted(merged)])
    derive(symbol, rows + [bar])

def derive(symbol, one_minute_rows):
    """Persist 5m/15m/1h/4h/D aggregates when native files are absent/stale.
    Chain: price(1s) -> 1m (flush) -> 5m->15m (resample) -> 1h/4h from 15m -> D (16:00 ET).
    Second-by-second 1m is provisional (volume 0) and erased when real ohlcv arrives (volume>0).
    HTF are only calculated mid-bar if enough time to spare, else at next 15m/1h 1st bar.
    Schedule: 1h at 09:30,10:30,11:30,12:30,13:30,14:30,15:30,16:30 ET (8 bars); 4h at 09:30/13:00/16:00 ET (3 bars); D at 16:00 ET."""
    # HTF mid-bar spare time gate: if within 60s of next 15m/1h boundary, defer to next bar
    now = datetime.now(timezone.utc)
    def _enough_spare_for(tf):
        # tf in ('15m','1h','4h','D')
        # Check if we are mid-bar with <60s to next boundary -> not enough spare
        now_et = now.astimezone(pytz.timezone("America/New_York"))
        if tf == '15m':
            next_min = ((now_et.minute // 15) + 1) * 15
            if next_min >= 60:
                secs_to_next = (60 - now_et.minute) * 60 - now_et.second
            else:
                secs_to_next = (next_min - now_et.minute) * 60 - now_et.second
            return secs_to_next > 60
        if tf == '1h':
            # 1h buckets at :30
            mins_past = (now_et.minute - 30) % 60
            secs_to_next = (60 - mins_past) * 60 - now_et.second if mins_past != 0 else 60*60 - now_et.second
            # Simplistic: if <90s to next 1h boundary, defer
            return secs_to_next > 90
        if tf == '4h':
            # 4h buckets at 09:30,13:00,16:00
            boundaries = [9*60+30, 13*60, 16*60]
            now_mins = now_et.hour*60 + now_et.minute
            next_b = next((b for b in boundaries if b > now_mins), None)
            if next_b is None:
                return False  # after 16:00, defer to next day
            secs_to_next = (next_b - now_mins)*60 - now_et.second
            return secs_to_next > 90
        return True

    parsed = []
    for row in one_minute_rows[-120:]:
        try:
            ts = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            parsed.append((ts, row))
        except Exception:
            continue
    # Build 5m and 15m sequentially: 1m -> 5m -> 15m (15m resampled from 5m/1m, not directly from thirst)
    # First 5m from 1m
    five_min_rows = []
    for minutes in (5, 15):
        if not parsed:
            continue
        latest = max(ts for ts, _ in parsed)
        bucket_minute = (latest.minute // minutes) * minutes
        bucket = latest.replace(minute=bucket_minute, second=0, microsecond=0)
        rows = [row for ts, row in parsed if bucket <= ts < bucket + timedelta(minutes=minutes)]
        if not rows:
            continue
        out = {'timestamp': bucket.strftime('%Y-%m-%dT%H:%M:%S.000000Z'),
               'open': float(rows[0]['open']), 'high': max(float(r['high']) for r in rows),
               'low': min(float(r['low']) for r in rows), 'close': float(rows[-1]['close']),
               'volume': sum(float(r.get('volume', 0)) for r in rows),
               'provisional': True}
        path = CACHE / f'{symbol}_{minutes}m.json'
        old = []
        try:
            old = json.loads(path.read_text())
        except Exception:
            pass
        if out['volume'] <= 0:
            prior = next((r for r in reversed(old) if isinstance(r, dict) and not r.get('provisional') and float(r.get('volume', 0) or 0) > 0), None)
            if prior:
                out['volume'] = float(prior['volume'])
        existing_vol = float((next((r for r in old if str(r.get('timestamp'))==out['timestamp']), {}) or {}).get('volume',0) or 0)
        new_vol = float(out.get('volume',0) or 0)
        if not (existing_vol > 0 and new_vol == 0):
            merged = {str(r.get('timestamp')): r for r in old if isinstance(r, dict) and r.get('timestamp')}
            merged[out['timestamp']] = out
            atomic_write(path, [merged[k] for k in sorted(merged)])
        if minutes == 5:
            # Keep 5m rows for 15m resample
            five_min_rows = rows
    # 1h/4h/D from 15m (not 1m) — 1h at 09:30,10:30,11:30,12:30,13:30,14:30,15:30,16:30 ET (8 bars); 4h at 09:30/13:00/16:00 ET (3 bars); D at 16:00 ET
    # Only calculate HTF mid-bar if enough spare time (>90s to next boundary), else defer to next 15m/1h 1st bar
    try:
        import pytz
        ET = pytz.timezone("America/New_York")
        if parsed:
            # Build 15m cache path for HTF source — read back the 15m we just wrote
            # Re-read 15m file to ensure 15m exists for HTF building
            try:
                path15 = CACHE / f'{symbol}_15m.json'
                rows15_raw = json.loads(path15.read_text()) if path15.exists() else []
                # Convert to datetime for filtering
                fifteen_parsed = []
                for r in rows15_raw[-32:]:
                    try:
                        ts = datetime.fromisoformat(r['timestamp'].replace('Z', '+00:00'))
                        fifteen_parsed.append((ts, r))
                    except: continue
            except: fifteen_parsed = []
            # Use fifteen_parsed if available, else fallback to parsed 1m
            htf_source = fifteen_parsed if len(fifteen_parsed) >= 2 else parsed
            latest = max(ts for ts, _ in htf_source)
            latest_et = latest.astimezone(ET)
            from datetime import time as dt_time
            # Spare-time gate for HTF: if <90s to next boundary, defer
            now_et = datetime.now(timezone.utc).astimezone(ET)
            def _spare_ok(tf):
                if tf == '1h':
                    # Next 1h boundary at :30
                    cur_mins = now_et.hour*60 + now_et.minute
                    # Find next :30
                    next_30 = ((now_et.hour + (1 if now_et.minute >= 30 else 0)) % 24) * 60 + 30
                    # Handle wrap past 16:30 -> next day 09:30
                    if now_et.hour >= 16 and now_et.minute >= 30:
                        return False
                    secs = (next_30 - cur_mins)*60 - now_et.second
                    return secs > 90
                if tf == '4h':
                    bounds = [9*60+30, 13*60, 16*60]
                    cur = now_et.hour*60 + now_et.minute
                    nxt = next((b for b in bounds if b > cur), None)
                    if nxt is None: return False
                    return (nxt - cur)*60 - now_et.second > 90
                return True

            # 1h bucket at :30
            if _spare_ok('1h'):
                bucket_et = latest_et.replace(minute=30, second=0, microsecond=0)
                if latest_et.minute < 30:
                    bucket_et = bucket_et - timedelta(hours=1)
                # Clamp to 09:30-16:30 range
                if bucket_et.time() < dt_time(9,30):
                    bucket_et = ET.localize(datetime.combine(latest_et.date(), dt_time(9,30)))
                elif bucket_et.time() > dt_time(16,30):
                    bucket_et = ET.localize(datetime.combine(latest_et.date(), dt_time(16,30)))
                bucket = bucket_et.astimezone(timezone.utc)
                rows_1h = [row for ts, row in htf_source if bucket <= ts < bucket + timedelta(hours=1)]
                if rows_1h:
                    out1h = {'timestamp': bucket.strftime('%Y-%m-%dT%H:%M:%S.000000Z'),
                             'open': float(rows_1h[0]['open'] if 'open' in rows_1h[0] else rows_1h[0].get('close',0)), 'high': max(float(r['high'] if 'high' in r else r.get('close',0)) for r in rows_1h),
                             'low': min(float(r['low'] if 'low' in r else r.get('close',0)) for r in rows_1h), 'close': float(rows_1h[-1]['close'] if 'close' in rows_1h[-1] else rows_1h[-1].get('close',0)),
                             'volume': sum(float(r.get('volume', 0)) for r in rows_1h), 'provisional': True}
                    path1h = CACHE / f'{symbol}_1h.json'
                    old1h = []
                    try: old1h = json.loads(path1h.read_text())
                    except: pass
                    _ev = float((next((r for r in old1h if str(r.get('timestamp'))==out1h['timestamp']), {}) or {}).get('volume',0) or 0)
                    if not (_ev > 0 and float(out1h.get('volume',0) or 0)==0):
                        merged1h = {str(r.get('timestamp')): r for r in old1h if isinstance(r, dict) and r.get('timestamp')}
                        merged1h[out1h['timestamp']] = out1h
                        atomic_write(path1h, [merged1h[k] for k in sorted(merged1h)])
            # 4h ET-anchored 09:30/13:00/16:00
            if _spare_ok('4h'):
                t = latest_et.time()
                if t < dt_time(13,0):
                    anchor = dt_time(9,30)
                elif t < dt_time(16,0):
                    anchor = dt_time(13,0)
                else:
                    anchor = dt_time(16,0)
                bucket4_et = ET.localize(datetime.combine(latest_et.date(), anchor))
                bucket4 = bucket4_et.astimezone(timezone.utc)
                rows_4h = [row for ts, row in htf_source if bucket4 <= ts < bucket4 + timedelta(hours=4)]
                if rows_4h:
                    out4h = {'timestamp': bucket4.strftime('%Y-%m-%dT%H:%M:%S.000000Z'),
                             'open': float(rows_4h[0]['open'] if 'open' in rows_4h[0] else rows_4h[0].get('close',0)), 'high': max(float(r['high'] if 'high' in r else r.get('close',0)) for r in rows_4h),
                             'low': min(float(r['low'] if 'low' in r else r.get('close',0)) for r in rows_4h), 'close': float(rows_4h[-1]['close'] if 'close' in rows_4h[-1] else rows_4h[-1].get('close',0)),
                             'volume': sum(float(r.get('volume', 0)) for r in rows_4h), 'provisional': True}
                    path4h = CACHE / f'{symbol}_4h.json'
                    old4h = []
                    try: old4h = json.loads(path4h.read_text())
                    except: pass
                    _ev4 = float((next((r for r in old4h if str(r.get('timestamp'))==out4h['timestamp']), {}) or {}).get('volume',0) or 0)
                    if not (_ev4 > 0 and float(out4h.get('volume',0) or 0)==0):
                        merged4h = {str(r.get('timestamp')): r for r in old4h if isinstance(r, dict) and r.get('timestamp')}
                        merged4h[out4h['timestamp']] = out4h
                        atomic_write(path4h, [merged4h[k] for k in sorted(merged4h)])
            # D at 16:00 ET
            bucketD_et = ET.localize(datetime.combine(latest_et.date(), dt_time(16,0)))
            bucketD = bucketD_et.astimezone(timezone.utc)
            rows_D = [row for ts, row in parsed if ts.date() == latest_et.date()]
            if rows_D:
                outD = {'timestamp': bucketD.strftime('%Y-%m-%dT%H:%M:%S.000000Z'),
                        'open': float(rows_D[0]['open']), 'high': max(float(r['high']) for r in rows_D),
                        'low': min(float(r['low']) for r in rows_D), 'close': float(rows_D[-1]['close']),
                        'volume': sum(float(r.get('volume', 0)) for r in rows_D), 'provisional': True}
                pathD = CACHE / f'{symbol}_D.json'
                oldD = []
                try: oldD = json.loads(pathD.read_text())
                except: pass
                _evD = float((next((r for r in oldD if str(r.get('timestamp'))==outD['timestamp']), {}) or {}).get('volume',0) or 0)
                if not (_evD > 0 and float(outD.get('volume',0) or 0)==0):
                    mergedD = {str(r.get('timestamp')): r for r in oldD if isinstance(r, dict) and r.get('timestamp')}
                    mergedD[outD['timestamp']] = outD
                    atomic_write(pathD, [mergedD[k] for k in sorted(mergedD)])
    except Exception as _e:
        # Fallback: log but don't crash flush
        try: import logging as _lg; _lg.getLogger("tradier_price_tracker").debug(f"derive HTF error: {_e}")
        except: pass

def main():
    symbols = [str(s).upper() for s in json.loads(SYMBOLS.read_text())]
    # Remove only synthetic rows from an earlier tracker run; real historical
    # rows are never deleted.
    for tf in ('1m', '5m', '15m'):
        for path in CACHE.glob(f'*_{tf}.json'):
            try:
                rows = json.loads(path.read_text())
                clean = [r for r in rows if not (isinstance(r, dict) and r.get('provisional'))]
                if len(clean) != len(rows): atomic_write(path, clean)
            except Exception:
                pass
    bars = {}
    last_mtime = 0
    dirty = set()
    last_flush = 0
    while True:
        try:
            mtime = PRICE_FILE.stat().st_mtime
            if mtime != last_mtime and time.time() - mtime <= 5:
                payload = json.loads(PRICE_FILE.read_text())
                quotes = payload.get('data', payload) if isinstance(payload, dict) else {}
                now = datetime.now(timezone.utc)
                # Do not manufacture stock candles overnight/weekends from
                # carried-forward quote values.
                if now.weekday() >= 5 or not ((now.hour > 13 or (now.hour == 13 and now.minute >= 30)) and now.hour < 20):
                    last_mtime = mtime
                    time.sleep(1)
                    continue
                current = minute_key(now)
                for symbol in symbols:
                    q = quotes.get(symbol)
                    if not isinstance(q, dict): continue
                    raw_ts = q.get('timestamp') or q.get('date')
                    try:
                        quote_ts = datetime.fromisoformat(str(raw_ts).replace('Z', '+00:00'))
                        if (now - quote_ts.astimezone(timezone.utc)).total_seconds() > 10:
                            continue
                    except Exception:
                        continue
                    try: price = float(q.get('price') or q.get('last') or 0)
                    except (TypeError, ValueError): continue
                    if price <= 0: continue
                    old = bars.get(symbol)
                    if old and old['_minute'] != current:
                        flush(symbol, {k:v for k,v in old.items() if k != '_minute'})
                        old = None
                    if old is None:
                        bars[symbol] = {'_minute': current, 'timestamp': current.strftime('%Y-%m-%dT%H:%M:%S.000000Z'), 'open': price, 'high': price, 'low': price, 'close': price, 'volume': 0.0, 'provisional': True}
                    else:
                        old['high'] = max(old['high'], price); old['low'] = min(old['low'], price); old['close'] = price
                    dirty.add(symbol)
                last_mtime = mtime
        except Exception:
            pass
        if dirty and time.time() - last_flush >= 5:
            for symbol in list(dirty):
                flush(symbol, {k:v for k,v in bars[symbol].items() if k != '_minute'})
            dirty.clear(); last_flush = time.time()
        time.sleep(1)

if __name__ == '__main__': main()
