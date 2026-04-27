#!/home/niels/.conda/envs/binance_env/bin/python3
import asyncio
import glob
import json
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import aiohttp
import websockets
import pandas as pd
sys.path.append('.')
import netifaces
import config
from utils import get_current_environment, orjson_default

def log(msg): print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
cfg=config.Config()
env_info=get_current_environment()
env=env_info['env']
interval_minutes={'1m':1,'3m':3,'15m':15,'1h':60,'4h':240,'D':1440}
issues_classification=[]
AUTO_CONFIRM=True
SCHEDULE_MINUTES=[2,7,12,17,22,27,32,37,42,47,52,57]
SECOND_WINDOW_SECONDS=15
CHECK_INTERVAL_SECONDS=5
RUN_AT_START=True
RUN_WINDOW_SECONDS=10
API_MAX_PER_SECOND=cfg.EZ_KLINES_API_MAX_PER_SECOND.get(env,2)
API_MAX_PER_MINUTE=cfg.EZ_KLINES_API_MAX_PER_MINUTE.get(env,50)
MAX_CONCURRENT_REQUESTS=cfg.EZ_KLINES_MAX_CONCURRENT.get(env,2)
MIN_KLINES_PER_INTERVAL={'3m': 220, '15m': 250, '1h': 250, '4h': 250, 'D': 50}
MIN_KLINES_THRESHOLD=200
MAX_DATA_FILE=cfg.BASE_PATH/'klines_maxed_symbols.json'
MAX_DATA_RETRY_HOURS=168
_throttle_state={'per_second':deque(),'per_minute':deque(),'lock':None}
_maxed_cache=None
ENVIRONMENT=get_current_environment()['env']
NETIFACES_AVAILABLE=True
try: import netifaces
except ImportError: NETIFACES_AVAILABLE=False
def _resolve_local_bind_kwargs():
    potential_ips=[]
    if os.environ.get("RUN_LOCALLY","").lower() in ("1","true","yes"): pass
    elif ENVIRONMENT=="server" and not os.environ.get("RUN_LOCALLY"): potential_ips.extend(["49.13.32.80", "157.180.125.52"])
    env_ip=os.environ.get("BINANCE_LOCAL_IP")
    if env_ip: potential_ips.append(env_ip)
    if NETIFACES_AVAILABLE:
        try:
            for interface in netifaces.interfaces():
                try:
                    addresses=netifaces.ifaddresses(interface)
                    for addr_info in addresses.get(netifaces.AF_INET,[]):
                        ip=addr_info['addr']
                        if (ip not in ['127.0.0.1','localhost','10.0.0.1'] and not ip.startswith('169.254.') and not ip.startswith('172.') and ip.count('.')==3): potential_ips.append(ip)
                except Exception: continue
        except Exception: pass
    for ip in potential_ips:
        try:
            import socket
            sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            sock.settimeout(1)
            result=sock.bind((ip,0))
            sock.close()
            return {'local_addr':(ip,0)}
        except Exception: continue
    return {}
def add_issue(symbol,interval,status,severity,detail=''):
    issues_classification.append({'symbol':symbol,'interval':interval,'status':status,'severity':severity,'detail':detail})
expected_columns=['timestamp','open','high','low','close','volume']
def _normalize_df(df):
    if df is None or df.empty: return pd.DataFrame(columns=expected_columns)
    for col in expected_columns:
        if col not in df: df[col]=pd.NA
    df=df[expected_columns]
    df['timestamp']=pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
    for col in expected_columns[1:]: df[col]=pd.to_numeric(df[col], errors='coerce')
    df=df.dropna(subset=['timestamp'])
    if df.empty: return pd.DataFrame(columns=expected_columns)
    return df.sort_values('timestamp').drop_duplicates(subset=['timestamp'], keep='last').reset_index(drop=True)
def _read_existing_df(file_path):
    if not os.path.exists(file_path): return pd.DataFrame(columns=expected_columns)
    try:
        with open(file_path,'r') as f: payload=json.load(f)
    except Exception:
        return pd.DataFrame(columns=expected_columns)
    if isinstance(payload,list):
        try:
            return _normalize_df(pd.DataFrame(payload))
        except Exception:
            return pd.DataFrame(columns=expected_columns)
    return pd.DataFrame(columns=expected_columns)
def merge_and_write(symbol, interval, rows):
    file_path=f'{cfg.KLINES_CACHE_DIR}/{symbol}_{interval}.json'
    new_df=_normalize_df(pd.DataFrame(rows))
    if new_df.empty: return 0
    existing_df=_read_existing_df(file_path)
    if existing_df.empty:
        merged=new_df
    else:
        merged=_normalize_df(pd.concat([existing_df,new_df], ignore_index=True))
    before=len(existing_df)
    added=max(len(merged)-before,0)
    if env != 'server':
        _trim = {'1m': (14000, 18000), '3m': (5000, 7000), '15m': (1500, 2000),
                 '1h': (1700, 2500), '4h': (1700, 2500), 'D': (2000, 3000),
                 'W': (300, 500), 'M': (60, 100)}
        keep, cap = _trim.get(interval, (1200, 1800))
        if len(merged) > cap:
            merged = merged.tail(keep).reset_index(drop=True)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    df_out=merged.copy()
    df_out['timestamp']=df_out['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
    tmp_path=f'{file_path}.tmp'
    with open(tmp_path,'w') as tmp: tmp.write(df_out.to_json(orient='records', indent=2))
    os.replace(tmp_path,file_path)
    return added
async def throttle_api_request():
    state=_throttle_state
    # if state['lock'] is None: state['lock']=asyncio.Lock()
    # async with state['lock']:
    per_second=state['per_second']
    per_minute=state['per_minute']
    while True:
        now=time.monotonic()
        while per_second and now-per_second[0]>=1: per_second.popleft()
        while per_minute and now-per_minute[0]>=60: per_minute.popleft()
        if len(per_second)<API_MAX_PER_SECOND and len(per_minute)<API_MAX_PER_MINUTE:
            per_second.append(now)
            per_minute.append(now)
            return
        waits=[]
        if per_second: waits.append(1-(now-per_second[0]))
        if per_minute: waits.append(60-(now-per_minute[0]))
        delay=max((w for w in waits if w>0), default=0.05)
        await asyncio.sleep(delay)
        
def print_issue_report():
    if not issues_classification:
        log('FINAL REPORT: All tracked klines up to date ✓')
        return
    log('=== FINAL STATUS REPORT ===')
    icons={'RED':'🔴','YELLOW':'🟡','INFO':'🔵'}
    for severity in ('RED','YELLOW','INFO'):
        rows=[row for row in issues_classification if row['severity']==severity]
        if rows:
            log(f"{icons.get(severity,'')} {severity}: {len(rows)}")
            for row in rows:
                suffix=f" - {row['detail']}" if row['detail'] else ''
                log(f"  {icons.get(severity,'')} {row['symbol']} {row['interval']} {row['status']}{suffix}")

def get_klines_directories():
    env=get_current_environment()['env']
    dirs=[]
    if env=='macbook':
        dirs=[cfg.BASE_PATH/'klines_cache',cfg.BASE_PATH/'klines_cache_gateway']
    elif env=='gateway':
        dirs=[Path('/home/niels/binance/klines_cache')]
    elif env=='server':
        dirs=[Path('/home/niels/binance/klines_cache'),Path('/home/niels/binance/klines_cache_gateway'),Path('/home/niels/binance/klines_cache_macbook')]
    return [d for d in dirs if d.exists()]
def get_symbols():
    symbols=set()
    for klines_dir in get_klines_directories():
        all_files=[]
        for pattern in ['*_3m.json','*_15m.json','*_1h.json','*_4h.json','*_D.json']:
            files=glob.glob(f'{klines_dir}/{pattern}')
            all_files.extend(files)
        for f in all_files:
            parts=os.path.basename(f).split('_')
            if len(parts)>=2:
                symbol=parts[0]
                symbols.add(symbol)
    return sorted(symbols)

def get_active_symbols():
    """Get symbols that should be actively tracked from symbols.json"""
    try:
        with open(cfg.SYMBOLS_FILE, 'r') as f:
            active_symbols = json.load(f)
        return set(active_symbols)
    except Exception as e:
        log(f"Warning: Could not read symbols.json: {e}")
        return set()

def cleanup_orphaned_klines(auto_confirm=AUTO_CONFIRM):
    log("\n=== CLEANUP: Checking for orphaned klines files ===")
    klines_symbols=set(get_symbols())
    active_symbols=get_active_symbols()
    orphaned_symbols=klines_symbols-active_symbols
    if not orphaned_symbols:
        log("No orphaned klines files found.")
        return 0
    log(f"Found {len(orphaned_symbols)} orphaned symbols (KEPT — never delete klines for backtesting):")
    for symbol in sorted(orphaned_symbols): log(f"  [kept] {symbol}")
    log(f"Cleanup complete: 0 files deleted (klines preserved for backtesting).")
    return 0

def load_maxed_symbols():
    global _maxed_cache
    if _maxed_cache is not None: return _maxed_cache
    if not MAX_DATA_FILE.exists():
        _maxed_cache={}
        return _maxed_cache
    try:
        with open(MAX_DATA_FILE,'r') as f: _maxed_cache=json.load(f)
        return _maxed_cache
    except Exception:
        _maxed_cache={}
        return _maxed_cache
def save_maxed_symbols(maxed_data):
    global _maxed_cache
    _maxed_cache=maxed_data
    try:
        tmp_path=f'{MAX_DATA_FILE}.tmp'
        with open(tmp_path,'w') as f: json.dump(maxed_data,f,indent=2)
        os.replace(tmp_path,MAX_DATA_FILE)
    except Exception as e: log(f"Error saving maxed symbols: {e}")
    
def is_symbol_maxed(symbol,interval):
    maxed=load_maxed_symbols()
    key=f'{symbol}_{interval}'
    if key not in maxed: return False
    try:
        from dateutil.parser import isoparse
        last_check=isoparse(maxed[key]['last_check'])
        now=datetime.now(timezone.utc)
        hours_since=(now-last_check).total_seconds()/3600
        return hours_since<MAX_DATA_RETRY_HOURS
    except Exception: return False
def mark_symbol_maxed(symbol,interval,bar_count):
    maxed=load_maxed_symbols()
    key=f'{symbol}_{interval}'
    maxed[key]={'last_check':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),'bar_count':bar_count,'reason':'max_data_reached'}
    save_maxed_symbols(maxed)
    log(f"📌 Marked {symbol} {interval} as maxed ({bar_count} bars) - won't retry for {MAX_DATA_RETRY_HOURS}h")
    
async def fetch_klines_api(symbol, interval, semaphore, session):
    """Fetch up to 1500 bars for a symbol/interval from Binance API"""
    try:
        # Map interval to Binance API format
        interval_map = {
            '1m': '1m',
            '3m': '3m',
            '15m': '15m',
            '1h': '1h',
            '4h': '4h',
            'D': '1d' }

        binance_interval = interval_map.get(interval, '1h')

        # API endpoint
        url = f"https://fapi.binance.com/fapi/v1/klines"

        # Parameters - fetch last 1500 bars
        params = {
            'symbol': symbol,
            'interval': binance_interval,
            'limit': 1500
        }

        await throttle_api_request()
        if semaphore:
            async with semaphore:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Convert to our format
                        klines_data = []
                        for kline in data:
                            try:
                                klines_data.append({'timestamp': datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),'open': float(kline[1]),'high': float(kline[2]),'low': float(kline[3]),'close': float(kline[4]),'volume': float(kline[5])})
                            except (ValueError, TypeError, IndexError):
                                continue

                        return symbol, interval, klines_data
                    else:
                        text = await response.text()
                        log(f"API Error for {symbol} {interval}: {response.status} {text}")
                        add_issue(symbol,interval,'API_ERROR','RED',f'status {response.status}')
                        return symbol, interval, None
        else:
            async with session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()

                    # Convert to our format
                    klines_data = []
                    for kline in data:
                        try:
                            klines_data.append({'timestamp': datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),'open': float(kline[1]),'high': float(kline[2]),'low': float(kline[3]),'close': float(kline[4]),'volume': float(kline[5])})
                        except (ValueError, TypeError, IndexError):
                            continue

                    return symbol, interval, klines_data
                else:
                    text = await response.text()
                    log(f"API Error for {symbol} {interval}: {response.status} {text}")
                    add_issue(symbol,interval,'API_ERROR','RED',f'status {response.status}')
                    return symbol, interval, None

    except Exception as e:
        log(f"Error fetching {symbol} {interval}: {e}")
        add_issue(symbol,interval,'API_EXCEPTION','RED',str(e))
        return symbol, interval, None

async def fetch_missing_klines(missing_bars, auto_confirm=AUTO_CONFIRM):
    if not missing_bars:
        log("\n=== API FETCH: No missing or stale bars to fetch ===")
        return
    log(f"\n=== API FETCH: Fetching {len(missing_bars)} missing/stale bars ===")
    
    # We filter for things that actually need data
    needs_repair = [bar for bar in missing_bars if any(flag in bar for flag in ('FILE MISSING','EMPTY FILE','STALE','NO DATA','LOW BAR COUNT'))]
    
    if not needs_repair:
        log("No files need repair.")
        return

    if auto_confirm:
        log("Auto-confirm enabled: refreshing data.")
        response = 'y'
    else:
        # log(f"\nFetch/refresh data for {len(needs_repair)} symbols? (y/N): ", end="")
        try:
            response = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            log("Non-interactive mode detected - API fetch cancelled.")
            return
            
    if response != 'y':
        log("API fetch cancelled.")
        return

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    timeout = aiohttp.ClientTimeout(total=30)
    bind_kwargs = _resolve_local_bind_kwargs()
    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_REQUESTS, limit=MAX_CONCURRENT_REQUESTS, **bind_kwargs)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        fetch_plan = []
        seen = set()
        for bar in needs_repair:
            parts = bar.split(' - ')
            if len(parts) >= 2:
                symbol_interval = parts[0].strip()
                symbol, interval = symbol_interval.rsplit(' ', 1)
                key = f'{symbol} {interval}'
                if key in seen: continue
                
                # REMOVED: The block that used to "continue" (skip) if is_symbol_maxed
                # We always want to fetch to get the NEWEST bars.
                
                seen.add(key)
                fetch_plan.append((symbol, interval))

        if not fetch_plan:
            return

        log(f"Fetching data for {len(fetch_plan)} symbol/timeframe combinations (parallel, sem={MAX_CONCURRENT_REQUESTS})...")
        saved_count = 0
        async def _fetch_one(symbol, interval):
            nonlocal saved_count
            try:
                result = await fetch_klines_api(symbol, interval, semaphore, session)
                if not result: return
                _, _, data = result
                if data:
                    added = merge_and_write(symbol, interval, data)
                    if added > 0: log(f"Saved {len(data)} bars for {symbol} {interval} (+{added})")
                    saved_count += 1
                    add_issue(symbol, interval, 'REFRESHED', 'INFO', f'{len(data)} bars fetched (+{added})')
                    fetch_threshold = MIN_KLINES_PER_INTERVAL.get(interval, MIN_KLINES_THRESHOLD)
                    if len(data) < fetch_threshold and added <= 1:
                        mark_symbol_maxed(symbol, interval, len(data))
            except Exception as e:
                log(f"Error updating {symbol} {interval}: {e}")
        # Run all fetches concurrently — semaphore + throttle control concurrency
        await asyncio.gather(*[_fetch_one(s, i) for s, i in fetch_plan])
        log(f"\nAPI update complete: {saved_count}/{len(fetch_plan)} files updated.")


# async def fetch_missing_klines(missing_bars,auto_confirm=AUTO_CONFIRM):
#     if not missing_bars:
#         log("\n=== API FETCH: No missing or stale bars to fetch ===")
#         return
#     log(f"\n=== API FETCH: Fetching {len(missing_bars)} missing/stale bars ===")
#     needs_repair=[bar for bar in missing_bars if any(flag in bar for flag in ('FILE MISSING','EMPTY FILE','STALE','NO DATA','LOW BAR COUNT'))]
#     if not needs_repair:
#         log("No files need repair (only errors or other issues found).")
#         return
#     log(f"Repairing {len(needs_repair)} files (<{MIN_KLINES_THRESHOLD} bars, missing files, empty files, or stale data)...")
#     if auto_confirm:
#         log("Auto-confirm enabled: refreshing data.")
#         response='y'
#     else:
#         log(f"\nFetch/refresh data for {len(needs_repair)} symbols? (y/N): ",end="")
#         try:
#             response=input().strip().lower()
#         except (EOFError,KeyboardInterrupt):
#             log("Non-interactive mode detected - API fetch cancelled for safety.")
#             return
#     if response!='y':
#         log("API fetch cancelled.")
#         return

#     # Prepare API requests
#     semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
#     timeout = aiohttp.ClientTimeout(total=30)
#     bind_kwargs=_resolve_local_bind_kwargs()
#     connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_REQUESTS,limit=MAX_CONCURRENT_REQUESTS,**bind_kwargs)

#     async with aiohttp.ClientSession(timeout=timeout,connector=connector) as session:
#         fetch_plan=[]
#         seen=set()
#         for bar in needs_repair:
#             parts=bar.split(' - ')
#             if len(parts)>=2:
#                 symbol_interval=parts[0].strip()
#                 symbol,interval=symbol_interval.rsplit(' ',1)
#                 key=f'{symbol} {interval}'
#                 if key in seen: continue
#                 if is_symbol_maxed(symbol,interval):
#                     log(f"⏭️  Skipping {symbol} {interval} - marked as maxed (retry after {MAX_DATA_RETRY_HOURS}h)")
#                     add_issue(symbol,interval,'SKIPPED_MAXED','INFO',f'maxed data skip')
#                     continue
#                 seen.add(key)
#                 fetch_plan.append((symbol,interval))
#         if not fetch_plan:
#             log("No valid files found to repair.")
#             return
#         log(f"Fetching data for {len(fetch_plan)} symbol/timeframe combinations (throttled)...")
#         saved_count=0
#         for symbol,interval in fetch_plan:
#             try:
#                 result=await fetch_klines_api(symbol,interval,semaphore,session)
#             except Exception as e:
#                 log(f"Task failed: {e}")
#                 continue
#             if not result or len(result)!=3: continue
#             _,_,data=result
#             if data:
#                 try:
#                     added=merge_and_write(symbol,interval,data)
#                     log(f"Saved {len(data)} bars for {symbol} {interval} (+{added})")
#                     saved_count+=1
#                     add_issue(symbol,interval,'REFRESHED','INFO',f'{len(data)} bars fetched (+{added})')
#                     if len(data)<MIN_KLINES_THRESHOLD and added==0:
#                         mark_symbol_maxed(symbol,interval,len(data))
#                 except Exception as e:
#                     log(f"Error saving {symbol} {interval}: {e}")
#                     add_issue(symbol,interval,'SAVE_ERROR','RED',str(e))
#         log(f"\nAPI repair complete: {saved_count}/{len(fetch_plan)} files refreshed.")

def get_max_age_for_interval(interval):
    """Get the maximum age allowed for a timeframe to be considered complete"""
    now = datetime.now(timezone.utc)
    return now - timedelta(minutes=interval_minutes.get(interval, 60))

def get_latest_bar_in_file(data):
    """Get the latest timestamp from the file data. Bars are time-sorted, so check last element first."""
    if not data:
        return None
    from dateutil.parser import isoparse
    # Fast path: bars are sorted by time, last element is the latest
    for bar in reversed(data):
        ts = bar.get('timestamp')
        if ts:
            try:
                isoparse(ts)  # validate it parses
                return ts
            except Exception:
                continue
    return None

def get_best_klines_file(symbol,interval):
    best_file=None
    best_count=0
    best_latest=None
    from dateutil.parser import isoparse
    for klines_dir in get_klines_directories():
        file_path=f'{klines_dir}/{symbol}_{interval}.json'
        if not os.path.exists(file_path): continue
        try:
            # Quick file-mod-time pre-check: skip if older than best AND smaller
            if best_file and best_latest:
                mod_time = os.path.getmtime(file_path)
                mod_dt = datetime.fromtimestamp(mod_time, tz=timezone.utc)
                if mod_dt < best_latest - timedelta(hours=1):
                    continue
            with open(file_path,'r') as f: data=json.load(f)
            if not data: continue
            count=len(data)
            latest=get_latest_bar_in_file(data)
            if latest:
                latest_dt=isoparse(latest)
                if best_latest is None or latest_dt>best_latest or (latest_dt==best_latest and count>best_count):
                    best_file=file_path
                    best_count=count
                    best_latest=latest_dt
            elif count>best_count:
                best_file=file_path
                best_count=count
        except Exception: continue
    return best_file,best_count,best_latest


def check_completeness():
    missing_bars = []
    symbols = get_symbols()
    active = get_active_symbols()
    log("=== KLINES CACHE COMPLETENESS CHECK ===")
    from dateutil.parser import isoparse
    now = datetime.now(timezone.utc)
    maxed_symbols = load_maxed_symbols()
    for symbol in symbols:
        if symbol not in active: continue
        for interval in ['3m', '15m', '1h', '4h', 'D']:
            file_path, bar_count, latest_dt = get_best_klines_file(symbol, interval)
            min_threshold = MIN_KLINES_PER_INTERVAL.get(interval, MIN_KLINES_THRESHOLD)
            maxed_info = maxed_symbols.get(f'{symbol}_{interval}')
            if not file_path:
                missing_bars.append(f'{symbol} {interval} - FILE MISSING')
                add_issue(symbol, interval, 'FILE_MISSING', 'RED', 'cache file missing')
                continue
            try:
                max_age_dt = get_max_age_for_interval(interval)
                # Use latest_dt from get_best_klines_file — no need to re-read the file
                is_stale = latest_dt is None or latest_dt.astimezone(timezone.utc) < max_age_dt
                if is_stale:
                    age_str = "Unknown"
                    if latest_dt:
                        age_minutes = (now - latest_dt.astimezone(timezone.utc)).total_seconds() / 60
                        age_str = f'{age_minutes:.0f}min'
                    missing_bars.append(f'{symbol} {interval} - STALE: {age_str} old')
                    add_issue(symbol, interval, 'STALE', 'RED', f'Latest bar {age_str} old')
                if bar_count < min_threshold:
                    if not maxed_info:
                        missing_bars.append(f'{symbol} {interval} - LOW BAR COUNT: {bar_count} bars (need {min_threshold})')
                        add_issue(symbol, interval, 'LOW_BARS', 'RED', f'only {bar_count}/{min_threshold} bars')
            except Exception as e:
                missing_bars.append(f'{symbol} {interval} - ERROR: {str(e)}')
                add_issue(symbol, interval, 'ERROR', 'RED', str(e))
    return missing_bars

# def check_completeness():
#     missing_bars=[]
#     symbols=get_symbols()
#     active=get_active_symbols()
#     klines_dirs=get_klines_directories()
#     log("=== KLINES CACHE COMPLETENESS CHECK ===")
#     log(f"Environment: {get_current_environment()['env']}")
#     log(f"Checking directories: {[str(d) for d in klines_dirs]}")
#     log(f"Checking {len(symbols)} symbols across 5 timeframes...")
#     log("")
#     for symbol in symbols:
#         if symbol not in active: continue
#         for interval in ['3m','15m','1h','4h','D']:
#             file_path,bar_count,latest_dt=get_best_klines_file(symbol,interval)
#             if not file_path:
#                 missing_bars.append(f'{symbol} {interval} - FILE MISSING')
#                 log(f'{symbol} {interval}: FILE MISSING')
#                 add_issue(symbol,interval,'FILE_MISSING','RED','cache file missing')
#                 continue
#             try:
#                 with open(file_path,'r') as f: data=json.load(f)
#                 if not data:
#                     missing_bars.append(f'{symbol} {interval} - EMPTY FILE')
#                     log(f'{symbol} {interval}: EMPTY FILE [0 bars]')
#                     add_issue(symbol,interval,'EMPTY','RED','empty cache file')
#                     continue
#                 actual_latest=get_latest_bar_in_file(data)
#                 max_age_dt=get_max_age_for_interval(interval)
#                 found=False
#                 actual_dt=None
#                 if actual_latest:
#                     try:
#                         from dateutil.parser import isoparse
#                         actual_dt=isoparse(actual_latest)
#                         found=actual_dt>=max_age_dt
#                     except Exception as e:
#                         log(f"Warning: Could not parse timestamp '{actual_latest}': {e}")
#                         found=False
#                         add_issue(symbol,interval,'PARSE','YELLOW',f'bad ts {actual_latest}')
#                 status="✓" if found else "✗"
#                 max_age_str=max_age_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
#                 actual_str=actual_latest or "NONE"
#                 if actual_latest:
#                     try:
#                         from dateutil.parser import isoparse
#                         actual_dt=isoparse(actual_latest)
#                         actual_str=actual_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
#                     except: pass
#                 log(f'{symbol} {interval}: {status} [{bar_count} bars] Max Age: {max_age_str} | Latest: {actual_str} | {file_path}')
#                 if bar_count<MIN_KLINES_THRESHOLD:
#                     missing_bars.append(f'{symbol} {interval} - LOW BAR COUNT: Only {bar_count} bars (need {MIN_KLINES_THRESHOLD})')
#                     log(f'    └─ Only {bar_count} bars (need at least {MIN_KLINES_THRESHOLD})')
#                     add_issue(symbol,interval,'LOW_BARS','RED',f'only {bar_count}/{MIN_KLINES_THRESHOLD} bars')
#                 if not found:
#                     if actual_latest:
#                         try:
#                             from dateutil.parser import isoparse
#                             actual_dt=isoparse(actual_latest)
#                             now=datetime.now(timezone.utc)
#                             age_minutes=(now-actual_dt).total_seconds()/60
#                             deficit=int(age_minutes/interval_minutes.get(interval,1))
#                             age_str=f'{age_minutes:.0f}min'
#                             missing_bars.append(f'{symbol} {interval} - STALE: Latest bar {actual_latest} is {age_str} old')
#                             log(f'    └─ Latest bar is {age_str} old (should be < {max_age_dt.strftime("%H:%M")} for {interval})')
#                             severity='YELLOW' if deficit<=1 else 'RED'
#                             add_issue(symbol,interval,'STALE',severity,f'behind {deficit} bars since {actual_str}')
#                         except:
#                             missing_bars.append(f'{symbol} {interval} - STALE DATA: Latest bar {actual_latest} is too old')
#                             add_issue(symbol,interval,'STALE_PARSE','RED',f'latest {actual_latest}')
#                     else:
#                         missing_bars.append(f'{symbol} {interval} - NO DATA: No bars found')
#                         log(f'    └─ No data bars found in file')
#                         add_issue(symbol,interval,'NO_DATA','RED','file contains no bars')
#             except Exception as e:
#                 missing_bars.append(f'{symbol} {interval} - ERROR: {str(e)}')
#                 log(f'{symbol} {interval}: ERROR - {str(e)}')
#                 add_issue(symbol,interval,'ERROR','RED',str(e))
#     log("")
#     return missing_bars

async def fetch_historical_paginated(symbol, interval, session, semaphore):
    """Fetch maximum historical bars by paginating backwards from earliest existing data."""
    from dateutil.parser import isoparse
    interval_map = {'1m': '1m', '3m': '3m', '15m': '15m', '1h': '1h', '4h': '4h', 'D': '1d'}
    binance_interval = interval_map.get(interval, '15m')
    url = "https://fapi.binance.com/fapi/v1/klines"
    total_added = 0
    best_file, bar_count, _ = get_best_klines_file(symbol, interval)
    earliest_ts = None
    if best_file:
        try:
            with open(best_file, 'r') as f: data = json.load(f)
            if data:
                ts_list = [isoparse(bar['timestamp']) for bar in data if 'timestamp' in bar]
                if ts_list: earliest_ts = min(ts_list)
        except Exception: pass
    max_pages = 500
    for _ in range(max_pages):
        params = {'symbol': symbol, 'interval': binance_interval, 'limit': 1500}
        if earliest_ts: params['endTime'] = int(earliest_ts.timestamp() * 1000) - 1
        await throttle_api_request()
        try:
            async with semaphore:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status != 200: break
                    data = await response.json()
        except Exception: break
        if not data: break
        rows = []
        for kline in data:
            try: rows.append({'timestamp': datetime.fromtimestamp(kline[0] / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'open': float(kline[1]), 'high': float(kline[2]), 'low': float(kline[3]), 'close': float(kline[4]), 'volume': float(kline[5])})
            except Exception: continue
        if not rows: break
        added = merge_and_write(symbol, interval, rows)
        total_added += added
        new_earliest = min(isoparse(r['timestamp']) for r in rows)
        if earliest_ts and new_earliest >= earliest_ts: break
        earliest_ts = new_earliest
        if added == 0: break
    return total_added

async def fetch_all_max_historical(intervals=None):
    """Fetch maximum historical klines for all active symbols across all requested intervals.
    Runs once at startup as background task — builds deep history for backtesting.
    Default intervals: 15m, 1h, 4h, D (best ROI for multi-TF backtesting)."""
    if intervals is None:
        intervals = ['15m', '1h', '4h', 'D']
    symbols = sorted(get_active_symbols())
    if not symbols: log('fetch_all_max_historical: no active symbols'); return
    log(f'fetch_all_max_historical: building max history for {len(symbols)} symbols x {intervals}...')
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    bind_kwargs = _resolve_local_bind_kwargs()
    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_REQUESTS, limit=MAX_CONCURRENT_REQUESTS, **bind_kwargs)
    async with aiohttp.ClientSession(connector=connector) as session:
        for interval in intervals:
            total = 0
            log(f'fetch_all_max_historical: starting {interval}...')
            for i, symbol in enumerate(symbols):
                try:
                    added = await fetch_historical_paginated(symbol, interval, session, semaphore)
                    if added > 0: log(f'  [{i+1}/{len(symbols)}] {symbol} {interval}: +{added} bars')
                    total += added
                except Exception as e: log(f'  [{i+1}/{len(symbols)}] {symbol} {interval}: error {e}')
            log(f'fetch_all_max_historical: {interval} done — +{total} bars added')
    log('fetch_all_max_historical: all intervals complete')

async def run_once(symbol_filter=None):
    log('Starting completeness pass')
    missing = check_completeness()
    cleanup_orphaned_klines(AUTO_CONFIRM)
    await fetch_missing_klines(missing)
    log('Completeness pass complete')
    if missing:
        log(f'SUMMARY: Found {len(missing)} issues')
        missing_files = [bar for bar in missing if 'FILE MISSING' in bar]
        empty_files = [bar for bar in missing if 'EMPTY FILE' in bar]
        low_bars = [bar for bar in missing if 'LOW BAR COUNT' in bar]
        stale_data = [bar for bar in missing if 'STALE' in bar]
        no_data = [bar for bar in missing if 'NO DATA' in bar]
        errors = [bar for bar in missing if 'ERROR' in bar]
        if missing_files:
            log(f'❌ Missing files: {len(missing_files)}')
        if empty_files:
            log(f'❌ Empty files: {len(empty_files)}')
        if low_bars:
            log(f'⚠️  Low bar count: {len(low_bars)}')
        if stale_data:
            log(f'⚠️  Stale data: {len(stale_data)}')
        if no_data:
            log(f'❌ No data: {len(no_data)}')
        if errors:
            log(f'❌ Errors: {len(errors)}')
        log("")
        log('=== DETAILED ISSUES ===')
        for bar in missing:
            log(f'  {bar}')
    else:
        log('SUMMARY: All bars complete! ✓')
    print_issue_report()

WS_1M_URL = "wss://fstream.binance.com/stream"
WS_CHUNK_SIZE = 180  # max streams per connection (Binance limit 200)

async def _fetch_stale_1m_rest(symbols):
    """One-time REST fetch for 1m files older than 2 minutes."""
    stale = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    for sym in symbols:
        best_file, _, latest_dt = get_best_klines_file(sym, '1m')
        if best_file is None or latest_dt is None or latest_dt < cutoff:
            stale.append(sym)
    if not stale:
        log(f'1m REST seed: all {len(symbols)} symbols fresh, skipping')
        return
    log(f'1m REST seed: fetching {len(stale)} stale/missing symbols...')
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    timeout = aiohttp.ClientTimeout(total=30)
    bind_kwargs = _resolve_local_bind_kwargs()
    connector = aiohttp.TCPConnector(limit_per_host=MAX_CONCURRENT_REQUESTS, limit=MAX_CONCURRENT_REQUESTS, **bind_kwargs)
    saved = 0
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        for sym in stale:
            try:
                result = await fetch_klines_api(sym, '1m', semaphore, session)
                if result and result[2]:
                    merge_and_write(sym, '1m', result[2])
                    saved += 1
            except Exception:
                continue
    log(f'1m REST seed: {saved}/{len(stale)} symbols seeded')

async def _ws_1m_chunk(symbols_chunk):
    """Maintain WebSocket kline_1m stream for a chunk of symbols, write on candle close."""
    streams = '/'.join(f"{s.lower()}@kline_1m" for s in symbols_chunk)
    url = f"{WS_1M_URL}?streams={streams}"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
                log(f'1m WS connected: {len(symbols_chunk)} symbols')
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        k = msg.get('data', {}).get('k', {})
                        if not k or not k.get('x'):
                            continue
                        sym = k['s']
                        bar = {'timestamp': datetime.fromtimestamp(k['t'] / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'), 'open': float(k['o']), 'high': float(k['h']), 'low': float(k['l']), 'close': float(k['c']), 'volume': float(k['v'])}
                        merge_and_write(sym, '1m', [bar])
                    except Exception:
                        continue
        except Exception as e:
            log(f'1m WS error ({len(symbols_chunk)} syms): {e} — reconnecting in 5s')
            await asyncio.sleep(5)

async def ws_1m_klines():
    """Seed 1m klines via REST then maintain via WebSocket streams."""
    symbols = sorted(get_active_symbols())
    if not symbols:
        log('1m WS: no active symbols, skipping')
        return
    await _fetch_stale_1m_rest(symbols)
    chunks = [symbols[i:i + WS_CHUNK_SIZE] for i in range(0, len(symbols), WS_CHUNK_SIZE)]
    log(f'1m WS: starting {len(chunks)} connection(s) for {len(symbols)} symbols')
    await asyncio.gather(*[_ws_1m_chunk(chunk) for chunk in chunks])

async def scheduler(symbol_filter=None):
    log('Starting ez_klines scheduler...')
    if RUN_AT_START:
        issues_classification.clear()
        await run_once(symbol_filter)
    while True:
        now = datetime.now()
        minute = now.minute
        second = now.second
        if minute in SCHEDULE_MINUTES and second < SECOND_WINDOW_SECONDS:
            log(f"\n=== RUN {now.strftime('%Y-%m-%d %H:%M:%S')} ===")
            issues_classification.clear()
            await run_once(symbol_filter)
            await asyncio.sleep(max(0, SECOND_WINDOW_SECONDS - second))
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

async def _main():
    asyncio.create_task(fetch_all_max_historical(['3m', '15m', '1h', '4h', 'D']))
    await asyncio.gather(scheduler(), ws_1m_klines())

if __name__ == '__main__':
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log('Scheduler stopped.')

