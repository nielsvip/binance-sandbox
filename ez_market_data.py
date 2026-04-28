import asyncio
import logging
import os
import platform
import random
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import aiohttp
import numpy as np
import orjson
from dateutil.parser import isoparse
from redis.asyncio import Redis

from config import Config
from utils import get_current_environment

config = Config()
BASE_PATH = Path(config.BASE_PATH)
env = get_current_environment()

# --- CONFIGURATION ---
def _resolve_base_path() -> Path:
    env_base = os.environ.get("BASE_PATH")
    if env_base:
        base = Path(env_base).expanduser()
        candidates = [base]
        if base.name.lower() != "binance":
            candidates.append(base / "binance")
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate
            except Exception:
                pass
        return candidates[0]
    system_name = platform.system()
    if system_name == "Darwin": # MacBook
        return Path("/Users/niels/Documents/binance")
    if system_name == "Linux": # Server
        return Path("/home/niels/binance")
    return Path.home() / "Documents" / "binance"

BASE_PATH = _resolve_base_path()
KLINES_DIRS = [
    BASE_PATH / "klines_cache",
    BASE_PATH / "klines_cache_gateway",
    BASE_PATH / "klines_cache_macbook"
]
SYMBOLS_FILES = ["symbols_active.json", "symbols.json"]
REDIS_URL = "redis://127.0.0.1:6379/0"
LOG_FILE = Path.home() / "logs" / "ez_market_data.log"

# --- IMPORT SHARED MEMORY CLIENT ---
try:
    from ez_share_ind import get_shared_memory_client
except ImportError:
    get_shared_memory_client = None

# --- LOGGING ---
Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("DataEngine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _fh = logging.FileHandler(LOG_FILE)
    _fh.setFormatter(_fmt)
    _sh = logging.StreamHandler()
    _sh.setFormatter(_fmt)
    logger.addHandler(_fh)
    logger.addHandler(_sh)

def resilient_json_load(path: Path):
    try:
        if not path.exists(): return None
        with open(path, 'rb') as f:
            raw_content = f.read()
        if not raw_content: return None
        try: return orjson.loads(raw_content) 
        except orjson.JSONDecodeError: pass
        text = raw_content.decode('utf-8', errors='ignore').strip()
        if not text.startswith('['): return None
        last_object_end = text.rfind('}')
        if last_object_end == -1: return None
        fixed_text = text[:last_object_end+1] + "]"
        try: return orjson.loads(fixed_text)
        except Exception: return None
    except Exception as e:
        logger.error(f"❌ Read error {path.name}: {e}")
        return None

def z_to_ts(z) -> float:
    """Robust ISO-8601 or Epoch to Float timestamp converter."""
    if isinstance(z, (int, float)): return float(z)
    if isinstance(z, str):
        try: return isoparse(z).timestamp()
        except Exception: pass
    return 0.0

# --- FAST NUMPY MATH ---
def wavetrend_numpy(highs, lows, closes, n1=10, n2=21, smooth=3):
    """Fast WaveTrend for hot_metrics. Returns dict with all WT fields or None."""
    n = len(closes)
    if n < n2 + smooth + 5: return None
    src = np.array(closes, dtype=np.float64)
    h = np.array(highs, dtype=np.float64); l = np.array(lows, dtype=np.float64)
    hlc3 = (h + l + src) / 3.0
    alpha1 = 2.0 / (n1 + 1)
    esa = np.zeros(n); esa[0] = hlc3[0]
    for i in range(1, n): esa[i] = alpha1 * hlc3[i] + (1 - alpha1) * esa[i-1]
    d = np.abs(hlc3 - esa)
    de = np.zeros(n); de[0] = d[0]
    for i in range(1, n): de[i] = alpha1 * d[i] + (1 - alpha1) * de[i-1]
    ci = np.where(de > 0, (hlc3 - esa) / (0.015 * de), 0.0)
    alpha3 = 2.0 / (n2 + 1)
    wt1 = np.zeros(n); wt1[0] = ci[0]
    for i in range(1, n): wt1[i] = alpha3 * ci[i] + (1 - alpha3) * wt1[i-1]
    wt2 = np.convolve(wt1, np.ones(smooth)/smooth, mode='same')
    # Cross detection (current bar)
    cross_bull_arr = (wt1[1:] > wt2[1:]) & (wt1[:-1] <= wt2[:-1])
    cross_bear_arr = (wt1[1:] < wt2[1:]) & (wt1[:-1] >= wt2[:-1])
    bull_idx = np.where(cross_bull_arr)[0] + 1
    bear_idx = np.where(cross_bear_arr)[0] + 1
    # Most recent cross direction, value, and bars ago
    recent_cross = None; cross_value = None; cross_prev_value = None; cross_rising = None; bars_ago = 999
    if len(bull_idx) > 0 and len(bear_idx) > 0:
        if bull_idx[-1] > bear_idx[-1]:
            recent_cross = "BULL"; bars_ago = n - 1 - bull_idx[-1]; cross_value = round(float(wt1[bull_idx[-1]]), 2)
            if len(bull_idx) >= 2: cross_prev_value = round(float(wt1[bull_idx[-2]]), 2); cross_rising = cross_value > cross_prev_value
        else:
            recent_cross = "BEAR"; bars_ago = n - 1 - bear_idx[-1]; cross_value = round(float(wt1[bear_idx[-1]]), 2)
            if len(bear_idx) >= 2: cross_prev_value = round(float(wt1[bear_idx[-2]]), 2); cross_rising = cross_value < cross_prev_value
    elif len(bull_idx) > 0:
        recent_cross = "BULL"; bars_ago = n - 1 - bull_idx[-1]; cross_value = round(float(wt1[bull_idx[-1]]), 2)
        if len(bull_idx) >= 2: cross_prev_value = round(float(wt1[bull_idx[-2]]), 2); cross_rising = cross_value > cross_prev_value
    elif len(bear_idx) > 0:
        recent_cross = "BEAR"; bars_ago = n - 1 - bear_idx[-1]; cross_value = round(float(wt1[bear_idx[-1]]), 2)
        if len(bear_idx) >= 2: cross_prev_value = round(float(wt1[bear_idx[-2]]), 2); cross_rising = cross_value < cross_prev_value
    # Velocity (3-bar lag)
    velocity = float(wt1[-1] - wt1[-4]) if n > 4 else 0.0
    return {"wt1": round(float(wt1[-1]), 2), "wt2": round(float(wt2[-1]), 2), "wt1_prev": round(float(wt1[-2]), 2), "wt2_prev": round(float(wt2[-2]), 2), "cross_bull": bool(cross_bull_arr[-1]) if len(cross_bull_arr) > 0 else False, "cross_bear": bool(cross_bear_arr[-1]) if len(cross_bear_arr) > 0 else False, "cross": recent_cross, "cross_value": cross_value, "cross_prev_value": cross_prev_value, "cross_rising": cross_rising, "cross_bars_ago": bars_ago, "bullish": bool(wt1[-1] > wt2[-1]), "score": round(float(wt1[-1] - wt2[-1]), 2), "velocity": round(velocity, 2)}

def stoch_rsi_numpy(closes, period=14, k_window=3, d_window=3):
    n = len(closes)
    if n < period + k_window + d_window + 2: return None
    
    src = np.array(closes, dtype=np.float64)
    deltas = np.diff(src)
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)

    avg_gain = np.empty(len(gains))
    avg_loss = np.empty(len(losses))
    avg_gain[0] = gains[0] 
    avg_loss[0] = losses[0]
    
    alpha = 1.0 / period
    w = 1.0 - alpha
    
    for i in range(1, len(gains)):
        avg_gain[i] = w * avg_gain[i-1] + alpha * gains[i]
        avg_loss[i] = w * avg_loss[i-1] + alpha * losses[i]

    with np.errstate(divide='ignore', invalid='ignore'):
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
    
    rsi[avg_loss == 0] = 100.0
    rsi = np.nan_to_num(rsi, nan=50.0)

    calc_start = period
    stoch_series = np.zeros(len(rsi))
    for i in range(calc_start, len(rsi)):
        window = rsi[i-period+1 : i+1]
        min_val = np.min(window)
        max_val = np.max(window)
        if max_val == min_val:
            stoch_series[i] = 100.0 if min_val > 50 else 0.0
        else:
            stoch_series[i] = (rsi[i] - min_val) / (max_val - min_val) * 100.0

    ones_k = np.ones(k_window) / k_window
    k_line = np.convolve(stoch_series, ones_k, mode='valid')
    ones_d = np.ones(d_window) / d_window
    d_line = np.convolve(k_line, ones_d, mode='valid')

    if len(d_line) < 2: return None
    return k_line[-1], d_line[-1], k_line[-2], d_line[-2], k_line[-1] - k_line[-2]

def cpu_bound_calculation(closes_1m, closes_3m, last_price, last_tick_ts):
    try:
        # 1. Calculate 1m Stoch
        r1 = stoch_rsi_numpy(closes_1m)
        if r1 is None: return None
        k1, d1, k1p, d1p, k1_slope = r1

        # 2. Calculate 3m Stoch
        r3 = stoch_rsi_numpy(closes_3m)
        if r3:
            k3, d3, k3p, d3p, k3_slope = r3
        else:
            k3 = d3 = k3p = d3p = 50.0

        result = {
            'price': last_price,
            '_tick_ts': last_tick_ts,
            '_calc_ts': last_tick_ts,  # Computation freshness = data freshness (mark price timestamp, NOT wall clock)
            'timestamp_1m': last_tick_ts,
            'is_partial_1m': True,
            'is_partial_3m': True,
            'k_1m': round(float(k1), 2), 'd_1m': round(float(d1), 2),
            'k_1m_prev': round(float(k1p), 2), 'd_1m_prev': round(float(d1p), 2),
            'k_3m': round(float(k3), 2), 'd_3m': round(float(d3), 2),
            'k_3m_prev': round(float(k3p), 2), 'd_3m_prev': round(float(d3p), 2),
            'valid': True,
            '_hot_source': 'metrics'
        }

        # 3. WaveTrend 1m — FULL intelligence (cross value, rising, bars_ago, velocity)
        wt1m = wavetrend_numpy(closes_1m, closes_1m, closes_1m)
        if wt1m:
            result['wt1_1m'] = wt1m['wt1']; result['wt2_1m'] = wt1m['wt2']
            result['wt1_1m_prev'] = wt1m['wt1_prev']; result['wt2_1m_prev'] = wt1m['wt2_prev']
            result['wt_cross_bull_1m'] = wt1m['cross_bull']; result['wt_cross_bear_1m'] = wt1m['cross_bear']
            result['wt_bullish_1m'] = wt1m['bullish']; result['wt_score_1m'] = wt1m['score']
            result['wt_cross_1m'] = wt1m['cross']; result['wt_cross_value_1m'] = wt1m['cross_value']
            result['wt_cross_prev_value_1m'] = wt1m['cross_prev_value']
            result['wt_cross_rising_1m'] = wt1m['cross_rising']
            result['wt_cross_bars_ago_1m'] = wt1m['cross_bars_ago']
            result['wt_velocity_1m'] = wt1m['velocity']

        # 4. WaveTrend 3m — FULL intelligence
        # Fallback: if insufficient 3m bars, resample 1m→3m (every 3rd close)
        # I-2 DESIGN NOTE (2026-04-18): this hot-tick writer intentionally OVERWRITES the bar-close
        # wt1_3m / wt2_3m / wt_cross_3m / wt_velocity_3m values produced by ez_indicators.py. Hot is
        # authoritative in live trading. NPZ/sandbox has no tick data — backtests therefore read
        # bar-close values computed with WT_TF_PARAMS['3m'] (esa=6,chan=10,sig=12) rather than the
        # numpy defaults (n1=10,n2=21,smooth=3) used here. Known drift; sandbox WT values are
        # bar-close-pessimistic vs live.
        _c3m = closes_3m if len(closes_3m) >= 29 else ([closes_1m[i] for i in range(2, len(closes_1m), 3)] if len(closes_1m) >= 87 else closes_3m)
        wt3m = wavetrend_numpy(_c3m, _c3m, _c3m)
        if wt3m:
            result['wt1_3m'] = wt3m['wt1']; result['wt2_3m'] = wt3m['wt2']
            result['wt1_3m_prev'] = wt3m['wt1_prev']; result['wt2_3m_prev'] = wt3m['wt2_prev']
            result['wt_cross_bull_3m'] = wt3m['cross_bull']; result['wt_cross_bear_3m'] = wt3m['cross_bear']
            result['wt_bullish_3m'] = wt3m['bullish']; result['wt_score_3m'] = wt3m['score']
            result['wt_cross_3m'] = wt3m['cross']; result['wt_cross_value_3m'] = wt3m['cross_value']
            result['wt_cross_prev_value_3m'] = wt3m['cross_prev_value']
            result['wt_cross_rising_3m'] = wt3m['cross_rising']
            result['wt_cross_bars_ago_3m'] = wt3m['cross_bars_ago']
            result['wt_velocity_3m'] = wt3m['velocity']

        return result
    except Exception as e:
        return None

# --- FAST CANDLE BUILDER ---
class KlineLoader:
    @staticmethod
    def load_history(symbol: str, interval: str) -> dict:
        filenames = [f"{symbol}_{interval}.json", f"{symbol.lower()}_{interval}.json"]
        for cache_dir in KLINES_DIRS:
            base_dir = Path(cache_dir).expanduser().resolve()
            for fname in filenames:
                path = base_dir / fname
                if not path.exists(): continue
                data = resilient_json_load(path)
                if not data or not isinstance(data, list) or len(data) == 0: continue
                
                history_map = {}
                for item in data[-1000:]: 
                    try:
                        ts = 0.0
                        close = 0.0
                        if isinstance(item, dict):
                            raw_ts = item.get('timestamp') or item.get('time')
                            close = float(item.get('close', 0))
                            if isinstance(raw_ts, str):
                                if raw_ts.endswith('Z'): raw_ts = raw_ts.replace('Z', '+00:00')
                                ts = isoparse(raw_ts).timestamp()
                            else:
                                ts = float(raw_ts)
                        elif isinstance(item, list) and len(item) >= 5:
                            ts = float(item[0])
                            close = float(item[4])
                        if ts > 1e11: ts /= 1000.0
                        if ts > 0 and close > 0:
                            history_map[int(ts)] = close
                    except Exception: continue
                if history_map:
                    return history_map  
        return {}

def update_1m_candle(store, price, tick_ts):
    minute_ts = int(tick_ts - (tick_ts % 60))
    store['closes_1m'][minute_ts] = price
    store['last_tick_ts'] = tick_ts
    while len(store['closes_1m']) > 180:
        store['closes_1m'].popitem(last=False)

def update_3m_candle(store, price, tick_ts):
    tf_ts = int(tick_ts - (tick_ts % 180))
    store['closes_3m'][tf_ts] = price
    while len(store['closes_3m']) > 180:
        store['closes_3m'].popitem(last=False)

class HighResMarketData:
    def __init__(self):
        self.data = {} 

    def init_symbol_from_disk(self, symbol: str):
        if symbol in self.data: return
        hist_1m = KlineLoader.load_history(symbol, '1m')
        hist_3m = KlineLoader.load_history(symbol, '3m')
        closes_1m = OrderedDict()
        closes_3m = OrderedDict()
        for ts in sorted(hist_1m.keys()): closes_1m[int(ts)] = float(hist_1m[ts])
        for ts in sorted(hist_3m.keys()): closes_3m[int(ts)] = float(hist_3m[ts])
        # WT needs ≥29 3m bars, stoch needs ≥22 1m bars. If 1m/3m files missing,
        # derive synthetic candles from 15m history (exists for all symbols).
        # Each 15m bar → 15 synthetic 1m bars + 5 synthetic 3m bars (same close).
        # Seeded values are replaced by live ticks within minutes — accuracy not required here.
        if len(closes_1m) < 50 or len(closes_3m) < 30:
            hist_15m = KlineLoader.load_history(symbol, '15m')
            if hist_15m:
                sorted_15m_keys = sorted(hist_15m.keys())[-40:]  # last 40 15m bars = 10 hours
                for ts_15m in sorted_15m_keys:
                    close = float(hist_15m[ts_15m])
                    ts_base = int(ts_15m)
                    if len(closes_1m) < 50:
                        for m in range(15):
                            closes_1m[ts_base + m * 60] = close
                    if len(closes_3m) < 30:
                        for t in range(5):
                            closes_3m[ts_base + t * 180] = close
        while len(closes_1m) > 180: closes_1m.popitem(last=False)
        while len(closes_3m) > 180: closes_3m.popitem(last=False)

        last_price = list(closes_1m.values())[-1] if closes_1m else 0.0
        last_ts = 0.0 

        store = {
            'closes_1m': closes_1m,
            'closes_3m': closes_3m,
            'last_price': last_price,
            'last_tick_ts': last_ts,
            'last_calc_ts': 0.0  # Prevents redundant calculations
        }
        self.data[symbol] = store

    def update(self, symbol: str, price: float, timestamp: float):
        store = self.data.get(symbol)
        if not store: return
        store['last_price'] = price
        store['last_tick_ts'] = timestamp
        update_1m_candle(store, price, timestamp)
        update_3m_candle(store, price, timestamp)
    def get_snapshot(self, symbol):
        store = self.data.get(symbol)
        if not store or len(store['closes_1m']) < 40: return None
        
        # 1. Get the latest source data (updated by broadcast_loop)
        lp = store['last_price']
        tick_ts = store['last_tick_ts']
        
        if tick_ts == 0.0: return None # Skip until first live tick arrives

        # 2. Prepare copies of historical data
        c1 = list(store['closes_1m'].values())
        c3 = list(store['closes_3m'].values())

        # 3. Time-based Injection Logic
        # Calculate which "bucket" the current tick belongs to
        current_1m_bucket = int(tick_ts - (tick_ts % 60))
        current_3m_bucket = int(tick_ts - (tick_ts % 180))

        # Get the timestamp of the last stored history candle
        last_stored_1m = list(store['closes_1m'].keys())[-1]
        last_stored_3m = list(store['closes_3m'].keys())[-1]

        # 1M: If tick is in a NEW minute, append. Else, overwrite last (current forming).
        if current_1m_bucket > last_stored_1m:
            c1.append(lp)
        else:
            c1[-1] = lp 

        # 3M: Same logic
        if current_3m_bucket > last_stored_3m:
            c3.append(lp)
        else:
            c3[-1] = lp

        return c1, c3, lp, tick_ts
# --- ENGINE WITH MULTI-SOURCE INJECTORS ---
class MarketDataEngine:
    def __init__(self):
        self.redis = Redis.from_url(REDIS_URL, decode_responses=True)
        self.market = HighResMarketData()
        self.symbols = set()
        self.running = True
        self.shared_proxy = None
        self.executor = ThreadPoolExecutor(max_workers=4)
        
        # THE BRAIN: Stores the freshest price found across all sources
        self.price_buffer = {} # { 'BTCUSDT': {'p': 50.0, 't': 1700.0} }
        
        self._connect_shared()

    def _connect_shared(self):
        if not hasattr(self, '_shared_mem_last_attempt'):
            self._shared_mem_last_attempt = 0.0
        now = time.time()
        if now - self._shared_mem_last_attempt < 60.0:
            return
        self._shared_mem_last_attempt = now
        if get_shared_memory_client:
            try:
                # Use 127.0.0.1
                mgr = get_shared_memory_client(address=('127.0.0.1', 50005))
                if mgr:
                    self.shared_proxy = mgr.get_store()
                    self.shared_proxy.health_check()
                    logger.info("✅ Connected to Shared Indicator Memory")
                else:
                    self.shared_proxy = None
            except Exception:
                self.shared_proxy = None
    async def shared_mem_watchdog(self):
        while self.running:
            if not self.shared_proxy:
                try: self._connect_shared()
                except Exception: pass
            await asyncio.sleep(5)

    async def load_symbols(self):
        logger.info(f"📚 Loading symbols from {BASE_PATH}...")
        universe = set()
        for fname in SYMBOLS_FILES:
            f = BASE_PATH / fname
            if f.exists():
                d = resilient_json_load(f)
                if isinstance(d, list): 
                    for x in d: universe.add(x.split(':')[-1].upper() if ':' in x else x.upper())
                elif isinstance(d, dict):
                    vals = d.get('symbols') or d.get('pairs') or []
                    for x in vals: universe.add(str(x).upper())
        self.symbols = {s for s in universe if "USDT" in s or "USDC" in s}
        
        for s in self.symbols: 
            self.market.init_symbol_from_disk(s)
        logger.info(f"✅ Ready. Monitoring {len(self.symbols)} symbols.")

    # ==========================================
    # PRICE INJECTORS (WS, REDIS, JSON)
    # ==========================================
    
    async def ws_injector(self):
        """Source 1: Primary High-Speed Websocket"""
        url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
        while self.running:
            try:
                session_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60)
                async with aiohttp.ClientSession(timeout=session_timeout) as session:
                    async with session.ws_connect(url, heartbeat=30, autoping=True, receive_timeout=60) as ws:
                        logger.info("🔌 WS Connected to Binance Stream")
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = orjson.loads(msg.data)
                                    if isinstance(data, list):
                                        for p in data:
                                            sym = p.get('s')
                                            if sym and sym in self.symbols:
                                                ts = float(p.get('E', 0)) / 1000.0
                                                price = p.get('p') or p.get('markPrice')
                                                if price:
                                                    self.price_buffer[sym] = {'p': float(price), 't': ts}
                                except Exception as ex:
                                    logger.debug(f"WS parse error: {ex}")
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSING):
                                logger.warning(f"WS msg type {msg.type} — reconnecting")
                                break
            except Exception as e:
                logger.error(f"WS Connection failed: {e}")
                await asyncio.sleep(3)

    async def redis_injector(self):
        """Source 2: Redis Fallback Poll (ALL price sources from ALL scripts)"""
        while self.running:
            try:
                if self.redis:
                    symbols_list = list(self.symbols)
                    # Source 2a: mark_price:{sym} (written by ez_positions_service from WS)
                    async with self.redis.pipeline() as pipe:
                        for s in symbols_list:
                            pipe.get(f"mark_price:{s}")
                        results = await pipe.execute()
                    for i, raw in enumerate(results):
                        if raw:
                            try:
                                obj = orjson.loads(raw)
                                sym = symbols_list[i]
                                ts = z_to_ts(obj.get('timestamp'))
                                if ts > self.price_buffer.get(sym, {}).get('t', 0):
                                    p = float(obj.get('price', obj.get('last', 0)))
                                    if p > 0: self.price_buffer[sym] = {'p': p, 't': ts}
                            except Exception: pass
                    # Source 2b: mark_prices hash (written by ez_mark_prices)
                    try:
                        all_marks = await self.redis.hgetall("mark_prices")
                        if all_marks:
                            for sym_bytes, val_bytes in all_marks.items():
                                try:
                                    sym = sym_bytes.decode() if isinstance(sym_bytes, bytes) else str(sym_bytes)
                                    if sym not in self.symbols: continue
                                    obj = orjson.loads(val_bytes)
                                    ts = z_to_ts(obj.get('timestamp', obj.get('ts', obj.get('time'))))
                                    if ts > self.price_buffer.get(sym, {}).get('t', 0):
                                        p = float(obj.get('price', obj.get('mark_price', obj.get('last', 0))))
                                        if p > 0: self.price_buffer[sym] = {'p': p, 't': ts}
                                except Exception: pass
                    except Exception: pass
            except Exception:
                pass
            await asyncio.sleep(1.0)  # Was 1.5s — tighter polling for fresher data

    async def json_injector(self):
        """Source 3: Disk Cache Fallback (Slowest, but failsafe)"""
        while self.running:
            for i in range(1, 4):
                try:
                    path = BASE_PATH / f"price_cache_{i}.json"
                    if not path.exists(): continue
                    async with aiofiles.open(path, 'rb') as f:
                        data = orjson.loads(await f.read())
                        for sym, entry in data.items():
                            if sym in self.symbols:
                                ts = z_to_ts(entry.get('timestamp'))
                                # Only inject if Disk data is NEWER than what's in our buffer
                                if ts > self.price_buffer.get(sym, {}).get('t', 0):
                                    p = float(entry.get('price', entry.get('last', 0)))
                                    self.price_buffer[sym] = {'p': p, 't': ts}
                except Exception:
                    pass
            await asyncio.sleep(2.0)

    # ==========================================
    # BROADCAST ENGINE
    # ==========================================
    async def broadcast_loop(self):
        logger.info("📡 Starting Calculation Loop (Buffered Real-Time Injection)...")
        loop = asyncio.get_running_loop()
        
        while self.running:
            active_symbols = list(self.market.data.keys())
            tasks_data = []

            for sym in active_symbols:
                store = self.market.data.get(sym)
                if not store: continue

                # 1. READ FROM CACHE (Buffer)
                buf = self.price_buffer.get(sym)
                if not buf: continue

                # 2. STRICT SYNC CHECK
                # Only proceed if this price is newer than the last one we CALCULATED with.
                # This prevents "updating without recalculating".
                if buf['t'] <= store.get('last_calc_ts', 0.0):
                    continue
                _now_ts = time.time()
                _buf_age = _now_ts - float(buf.get('t', 0) or 0)
                if _buf_age > 3.0:
                    _last_warn = self._stale_warn_throttle.get(sym, 0.0) if hasattr(self, "_stale_warn_throttle") else 0.0
                    if _now_ts - _last_warn > 30.0:
                        if not hasattr(self, "_stale_warn_throttle"): self._stale_warn_throttle = {}
                        self._stale_warn_throttle[sym] = _now_ts
                        try: logger.error(f"[mark_price_freshness] CRITICAL ez_market_data {sym} buf_age={_buf_age:.1f}s>3.0s — WS likely dead, calculating with stale price")
                        except Exception: pass

                # 3. ATOMIC UPDATE & SNAPSHOT
                # We update the store NOW, knowing we will immediately snapshot it.
                # This replaces the last close with the current Mark Price.
                self.market.update(sym, buf['p'], buf['t'])
                
                # 4. GET ARRAYS
                # Because we just called update(), c1[-1] is guaranteed to be buf['p']
                snap = self.market.get_snapshot(sym)
                if not snap: continue
                c1, c3, price, tick_ts = snap

                # 5. QUEUE CALCULATION
                # We pass 'tick_ts' (which equals buf['t']) explicitly.
                tasks_data.append((sym, c1, c3, price, tick_ts))

            if not tasks_data:
                await asyncio.sleep(0.5) # Wait for new price ticks
                continue

            # 4. Execute Threads
            results = []
            chunk_size = 50
            for i in range(0, len(tasks_data), chunk_size):
                chunk = tasks_data[i:i+chunk_size]
                futures = []
                for item in chunk:
                    sym, c1, c3, p, t = item
                    fut = loop.run_in_executor(self.executor, cpu_bound_calculation, c1, c3, p, t)
                    futures.append((sym, fut, t)) # Track timestamp to update store
                
                for sym, fut, t in futures:
                    try:
                        res = await fut
                        if res: 
                            results.append((sym, res))
                            self.market.data[sym]['last_calc_ts'] = t # Mark as calculated
                    except Exception as e:
                        logger.error(f"Calc error {sym}: {e}")

            # 5. Publish Results
            if results:
                # ═══ 2026-04-27 FUNDING RATE + OI PROPAGATION ═══
                # Inject funding_rate + OI from market.data into per-symbol payload so they flow through
                # Redis hot_metrics:{sym} → data_manager.get_hot_state → execute_trade_wrapper._gate_ind.
                # Without this, funding_rate_loop / open_interest_loop stored values are invisible to gates.
                for sym, payload in results:
                    try:
                        store = self.market.data.get(sym, {})
                        _fr = store.get('funding_rate')
                        if _fr is not None:
                            payload['funding_rate'] = _fr
                            payload['funding_rate_ts'] = store.get('funding_rate_ts', 0)
                        _oichg = store.get('oi_change_1h_pct')
                        if _oichg is not None:
                            payload['oi_change_1h_pct'] = _oichg
                            payload['oi_value'] = store.get('oi_value', 0.0)
                            payload['oi_ts'] = store.get('oi_ts', 0)
                    except Exception:
                        pass
                # ═══ 2026-04-27 ORDER-BOOK RED-ZONE / WALL PROPAGATION ═══
                # ez_orderbook.py writes per-sym features to Redis `orderbook:<SYM>` (TTL 10s).
                # Batched MGET → inject ob_bid_wall_pct, ob_ask_wall_pct, ob_*_wall_size, ob_long/short_score,
                # ob_imb_5pct, ob_ts_ms into hot_metrics so RED_ZONE_GATE in execute_trade_wrapper can read.
                try:
                    _ob_keys = [f"orderbook:{sym}" for sym, _ in results]
                    _ob_blobs = await self.redis.mget(*_ob_keys) if _ob_keys else []
                    for (sym, payload), blob in zip(results, _ob_blobs):
                        if not blob:
                            continue
                        try:
                            _ob = orjson.loads(blob)
                        except Exception:
                            continue
                        for _k in ('ob_bid_wall_pct', 'ob_bid_wall_size',
                                   'ob_ask_wall_pct', 'ob_ask_wall_size',
                                   'ob_bid_void_pct', 'ob_ask_void_pct',
                                   'ob_long_score', 'ob_short_score',
                                   'ob_imb_5pct', 'ob_imb_10pct',
                                   'ob_ts_ms', 'ob_spread_bps'):
                            _v = _ob.get(_k)
                            if _v is not None:
                                payload[_k] = _v
                except Exception:
                    pass
                # Update Bridge
                if self.shared_proxy:
                    try:
                        batch_payload = {sym: payload for sym, payload in results}
                        await asyncio.to_thread(self.shared_proxy.update_batch, batch_payload)
                    except Exception:
                        self.shared_proxy = None
                # Update Redis Pipeline
                async with self.redis.pipeline() as pipe:
                    for sym, payload in results:
                        pipe.set(f"hot_metrics_ts:{sym}", payload['_tick_ts'], ex=60)
                        pipe.set(f"hot_metrics:{sym}", orjson.dumps(payload, option=orjson.OPT_SERIALIZE_NUMPY), ex=60)
                    await pipe.execute()

            # Ensure we don't spin uncontrollably
            await asyncio.sleep(0.1)

    async def heartbeat(self):
        while self.running:
            status = "Bridge Linked 🟢" if self.shared_proxy else "Redis Only 🟠"
            logger.info(f"💓 Engine Alive | Symbols: {len(self.market.data)} | {status}")
            await asyncio.sleep(30)

    async def funding_rate_loop(self):
        """═══ 2026-04-27 FUNDING-RATE LIVE REFRESH (USER DIRECTIVE) ═══
        Polls Binance Futures /fapi/v1/fundingRate every FUNDING_LIVE_REFRESH_HOURS for tracked symbols
        and stores the latest 8h funding rate into market.data[sym]['funding_rate']. The entry-gate logic
        in ez_positions_quick.execute_trade_wrapper reads this field via _gate_ind.get('funding_rate').
        Falls back to disk cache (data/funding_cache/{sym}.json populated by binance_funding_fetcher.py).
        """
        try:
            import requests as _frq
        except ImportError:
            logger.warning("[FUNDING_LOOP] requests not available — funding rates disabled")
            return
        cache_dir = BASE_PATH / "data" / "funding_cache"
        endpoint = "https://fapi.binance.com/fapi/v1/fundingRate"
        # Initial wait — let market_data finish bootstrap so self.market.data is populated.
        await asyncio.sleep(30)
        while self.running:
            try:
                refresh_hrs = float(getattr(__import__('config'), 'FUNDING_LIVE_REFRESH_HOURS', 1.0))
                symbols = list(self.market.data.keys())
                n_updated = 0
                n_cache = 0
                for sym in symbols:
                    fr_val = 0.0
                    fr_ts = 0
                    # 1) Try fresh API fetch (limit=1, latest rate)
                    try:
                        r = await asyncio.to_thread(_frq.get, endpoint, params={"symbol": sym, "limit": 1}, timeout=10)
                        if r.status_code == 200:
                            batch = r.json()
                            if isinstance(batch, list) and batch:
                                rec = batch[-1]
                                fr_val = float(rec.get("fundingRate", 0.0))
                                fr_ts = int(rec.get("fundingTime", 0))
                                n_updated += 1
                    except Exception:
                        pass
                    # 2) Fallback: disk cache from binance_funding_fetcher.py
                    if fr_val == 0.0 and fr_ts == 0:
                        cache_path = cache_dir / f"{sym}.json"
                        if cache_path.exists():
                            try:
                                async with aiofiles.open(cache_path, "rb") as f:
                                    recs = orjson.loads(await f.read())
                                if recs:
                                    rec = recs[-1]
                                    fr_val = float(rec.get("fundingRate", 0.0))
                                    fr_ts = int(rec.get("fundingTime", 0))
                                    n_cache += 1
                            except Exception:
                                pass
                    # Store on market store so it's visible to indicator computation
                    if sym in self.market.data:
                        try:
                            self.market.data[sym]["funding_rate"] = fr_val
                            self.market.data[sym]["funding_rate_ts"] = fr_ts
                        except Exception:
                            pass
                    # API courtesy: 200ms between symbols (Binance limits 2400 weight/min, fundingRate weight 1)
                    await asyncio.sleep(0.2)
                logger.info(f"[FUNDING_LOOP] refreshed: {n_updated} from API, {n_cache} from cache, total {len(symbols)} syms. Next in {refresh_hrs}h.")
                await asyncio.sleep(refresh_hrs * 3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[FUNDING_LOOP] crash: {e}", exc_info=True)
                await asyncio.sleep(300)

    async def open_interest_loop(self):
        """═══ 2026-04-27 OPEN INTEREST LIVE REFRESH ═══
        Polls Binance Futures /fapi/v1/openInterestHist every OI_LIVE_REFRESH_HOURS for tracked symbols
        and stores latest OI + 1h-pct-change into market.data[sym]['oi_value', 'oi_change_1h_pct'].
        Read by execute_trade_wrapper via _gate_ind.get('oi_change_1h_pct') for OI_CONFIRM_ENABLED gate.
        Falls back to disk cache (data/oi_cache/{sym}.json populated by binance_oi_fetcher.py).
        Binance limits OI history to ~30d so cache + API blend keeps freshness.
        """
        try:
            import requests as _oirq
        except ImportError:
            logger.warning("[OI_LOOP] requests not available — OI disabled")
            return
        cache_dir = BASE_PATH / "data" / "oi_cache"
        endpoint = "https://fapi.binance.com/fapi/v1/openInterestHist"
        await asyncio.sleep(45)  # stagger 15s after funding_rate_loop init
        while self.running:
            try:
                refresh_hrs = float(getattr(__import__('config'), 'OI_LIVE_REFRESH_HOURS', 1.0))
                symbols = list(self.market.data.keys())
                n_updated = 0; n_cache = 0
                for sym in symbols:
                    oi_now = 0.0; oi_value = 0.0; oi_1h_ago = 0.0; oi_ts = 0
                    # API: latest 12 × 5min OI = 1h window
                    try:
                        r = await asyncio.to_thread(_oirq.get, endpoint, params={"symbol": sym, "period": "5m", "limit": 13}, timeout=10)
                        if r.status_code == 200:
                            batch = r.json()
                            if isinstance(batch, list) and len(batch) >= 2:
                                oi_now = float(batch[-1].get("sumOpenInterest", 0.0))
                                oi_value = float(batch[-1].get("sumOpenInterestValue", 0.0))
                                oi_1h_ago = float(batch[0].get("sumOpenInterest", 0.0))
                                oi_ts = int(batch[-1].get("timestamp", 0))
                                n_updated += 1
                    except Exception:
                        pass
                    # Fallback to disk cache
                    if oi_now == 0.0 and oi_ts == 0:
                        cache_path = cache_dir / f"{sym}.json"
                        if cache_path.exists():
                            try:
                                async with aiofiles.open(cache_path, "rb") as f:
                                    recs = orjson.loads(await f.read())
                                if recs and len(recs) >= 13:
                                    oi_now = float(recs[-1].get("sumOpenInterest", 0.0))
                                    oi_value = float(recs[-1].get("sumOpenInterestValue", 0.0))
                                    oi_1h_ago = float(recs[-13].get("sumOpenInterest", 0.0))
                                    oi_ts = int(recs[-1].get("timestamp", 0))
                                    n_cache += 1
                            except Exception:
                                pass
                    oi_change_1h_pct = ((oi_now - oi_1h_ago) / oi_1h_ago * 100.0) if oi_1h_ago > 0 else 0.0
                    if sym in self.market.data:
                        try:
                            self.market.data[sym]["oi_value"] = oi_value
                            self.market.data[sym]["oi_change_1h_pct"] = oi_change_1h_pct
                            self.market.data[sym]["oi_ts"] = oi_ts
                        except Exception:
                            pass
                    await asyncio.sleep(0.2)
                logger.info(f"[OI_LOOP] refreshed: {n_updated} from API, {n_cache} from cache, total {len(symbols)} syms. Next in {refresh_hrs}h.")
                await asyncio.sleep(refresh_hrs * 3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[OI_LOOP] crash: {e}", exc_info=True)
                await asyncio.sleep(300)

    async def klines_cache_writeback(self):
        """Periodically merge composed 1m/3m candles back to klines_cache so ez_indicators stays fresh."""
        cache_dir = BASE_PATH / "klines_cache"
        await asyncio.sleep(60)
        while self.running:
            try:
                written = 0
                for symbol, store in self.market.data.items():
                    for tf, closes_key in [("1m", "closes_1m"), ("3m", "closes_3m")]:
                        closes = store.get(closes_key)
                        if not closes or len(closes) < 10:
                            continue
                        path = cache_dir / f"{symbol}_{tf}.json"
                        existing = []
                        if path.exists():
                            try:
                                async with aiofiles.open(path, "rb") as f:
                                    existing = orjson.loads(await f.read())
                                if not isinstance(existing, list):
                                    existing = []
                            except Exception:
                                existing = []
                        existing_ts = set()
                        for bar in existing:
                            if isinstance(bar, dict):
                                ts_raw = bar.get("timestamp", "")
                                try:
                                    existing_ts.add(int(isoparse(ts_raw).timestamp()))
                                except Exception:
                                    pass
                        new_bars = []
                        for ts_epoch, close_price in closes.items():
                            if ts_epoch not in existing_ts:
                                ts_iso = datetime.fromtimestamp(ts_epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                                new_bars.append({"timestamp": ts_iso, "open": close_price, "high": close_price, "low": close_price, "close": close_price, "volume": 0})
                            else:
                                for bar in existing:
                                    if isinstance(bar, dict):
                                        try:
                                            bar_ts = int(isoparse(bar.get("timestamp", "")).timestamp())
                                        except Exception:
                                            continue
                                        if bar_ts == ts_epoch:
                                            bar["close"] = close_price
                                            bar["high"] = max(float(bar.get("high", close_price)), close_price)
                                            bar["low"] = min(float(bar.get("low", close_price)), close_price)
                                            break
                        if new_bars:
                            merged = existing + new_bars
                            merged.sort(key=lambda b: b.get("timestamp", ""))
                            tmp = path.with_suffix(".tmp")
                            async with aiofiles.open(tmp, "wb") as f:
                                await f.write(orjson.dumps(merged))
                            os.replace(tmp, path)
                            written += 1
                        elif existing:
                            tmp = path.with_suffix(".tmp")
                            async with aiofiles.open(tmp, "wb") as f:
                                await f.write(orjson.dumps(existing))
                            os.replace(tmp, path)
                if written > 0:
                    logger.info(f"📝 [KLINES_WRITEBACK] Updated {written} klines_cache files")
            except Exception as e:
                logger.error(f"[KLINES_WRITEBACK] Error: {e}")
            await asyncio.sleep(60)

    async def main(self):
        await self.load_symbols()
        try:
            # Run all injectors alongside the broadcast engine
            await asyncio.gather(
                self.ws_injector(),
                self.redis_injector(),
                self.json_injector(),
                self.broadcast_loop(),
                self.heartbeat(),
                self.shared_mem_watchdog(),
                self.klines_cache_writeback(),
                self.funding_rate_loop(),  # 2026-04-27: live funding-rate refresh into market.data[sym]['funding_rate']
                self.open_interest_loop(),  # 2026-04-27: live OI refresh into market.data[sym]['oi_value','oi_change_1h_pct']
            )
        except KeyboardInterrupt:
            self.running = False
            self.executor.shutdown(wait=False)
            logger.info("Stopping Engine...")

if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(MarketDataEngine().main())
    
    
    
# import asyncio
# import logging
# import time
# import os
# import orjson
# import aiohttp
# import platform
# import numpy as np 
# from collections import deque
# from redis.asyncio import Redis
# from pathlib import Path
# from dateutil.parser import isoparse
# from config import Config
# from utils import get_current_environment, orjson_default
# config = Config()
# BASE_PATH = Path(config.BASE_PATH)
# env = get_current_environment()
# # --- CONFIGURATION ---
# def _resolve_base_path() -> Path:
#     env_base = os.environ.get("BASE_PATH")
#     if env_base:
#         base = Path(env_base).expanduser()
#         candidates = [base]
#         if base.name.lower() != "binance":
#             candidates.append(base / "binance")
#         for candidate in candidates:
#             try:
#                 if candidate.exists():
#                     return candidate
#             except Exception:
#                 pass
#         return candidates[0]
#     system_name = platform.system()
#     if system_name == "Darwin": # MacBook
#         return Path("/Users/niels/Documents/binance")
#     if system_name == "Linux": # Server
#         return Path("/home/niels/binance")
#     return Path.home() / "Documents" / "binance"
# BASE_PATH = _resolve_base_path()
# KLINES_DIRS = [
#     BASE_PATH / "klines_cache",
#     BASE_PATH / "klines_cache_gateway",
#     BASE_PATH / "klines_cache_macbook"]
# SYMBOLS_FILES = ["symbols_active.json"]
# REDIS_URL = "redis://127.0.0.1:6379/0"
# LOG_FILE = Path.home() / "logs" / "ez_market_data.log"

# # --- IMPORT SHARED MEMORY CLIENT ---
# try:
#     from ez_share_ind import get_shared_memory_client
# except ImportError:
#     get_shared_memory_client = None

# # --- LOGGING ---
# Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])
# logger = logging.getLogger("DataEngine")

# def resilient_json_load(path: Path):
#     """
#     Tries to load JSON. If truncated, attempts to repair by closing the list.
#     Returns: The loaded data (list/dict) or None.
#     """
#     try:
#         if not path.exists():
#             return None
            
#         # Read as bytes
#         with open(path, 'rb') as f:
#             raw_content = f.read()
            
#         if not raw_content:
#             return None
            
#         # 1. Try Standard Load
#         try:
#             return orjson.loads(raw_content)  # pylint: disable=no-member
#         except orjson.JSONDecodeError:  # pylint: disable=no-member
#             pass # Corruption detected, proceed to repair
            
#         # 2. Repair Truncated File
#         text = raw_content.decode('utf-8', errors='ignore').strip()
        
#         # Must act like a list of klines
#         if not text.startswith('['):
#             return None
            
#         # Find the last valid closing brace for an object
#         last_object_end = text.rfind('}')
#         if last_object_end == -1:
#             return None
            
#         # Cut off garbage and close the list
#         fixed_text = text[:last_object_end+1] + "]"
        
#         try:
#             data = orjson.loads(fixed_text)  # pylint: disable=no-member
#             # logger.warning(f"🔧 [JSON_REPAIR] repaired {path.name} ({len(data)} records)")
#             return data
#         except:
#             return None
            
#     except Exception as e:
#         logger.error(f"❌ Read error {path.name}: {e}")
#         return None

# def stoch_rsi_numpy(closes, period=14, k_window=3, d_window=3):
#     closes = np.asarray(closes[-60:], dtype=np.float64)  # HARD CAP

#     n = closes.size
#     if n < period + k_window + d_window + 1:
#         return None

#     deltas = np.diff(closes)
#     gains = np.maximum(deltas, 0.0)
#     losses = np.maximum(-deltas, 0.0)

#     avg_gain = np.empty(n)
#     avg_loss = np.empty(n)
#     avg_gain[:period] = 0.0
#     avg_loss[:period] = 0.0

#     avg_gain[period] = gains[:period].mean()
#     avg_loss[period] = losses[:period].mean()

#     for i in range(period + 1, n):
#         avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
#         avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

#     rs = avg_gain / np.maximum(avg_loss, 1e-9)
#     rsi = 100 - (100 / (1 + rs))

#     stoch = np.empty(n)
#     for i in range(period, n):
#         lo = rsi[i - period + 1:i + 1].min()
#         hi = rsi[i - period + 1:i + 1].max()
#         stoch[i] = 0.0 if hi == lo else (rsi[i] - lo) / (hi - lo) * 100.0

#     k = np.convolve(stoch, np.ones(k_window) / k_window, mode="valid")
#     d = np.convolve(k, np.ones(d_window) / d_window, mode="valid")

#     k_curr, k_prev = k[-1], k[-2]
#     d_curr, d_prev = d[-1], d[-2]

#     return (
#         round(float(k_curr), 2),
#         round(float(d_curr), 2),
#         round(float(k_prev), 2),
#         round(float(d_prev), 2),
#         round(float(k_curr - k_prev), 3),
#     )

# def cpu_bound_calculation(
#     closes_1m,
#     closes_3m,
#     last_price,
#     last_tick_ts,
# ):
#     try:
#         r1 = stoch_rsi_numpy(closes_1m)
#         if r1 is None:
#             return None

#         k1, d1, k1p, d1p, k1_slope = r1

#         r3 = stoch_rsi_numpy(closes_3m)
#         if r3:
#             k3, d3, k3p, d3p, k3_slope = r3
#         else:
#             k3 = d3 = k3p = d3p = 50.0

#         payload = {
#             # --- Price ---
#             'price': last_price,
#             '_tick_ts': last_tick_ts,
#             'timestamp': last_tick_ts,
#             'is_partial_1m': True,
#             'is_partial_3m': True,
#             'k_1m': k1, 'd_1m': d1,
#             'k_1m_p': k1p, 'd_1m_p': d1p, 
#             'k_3m': k3, 'd_3m': d3,
#             'k_3m_p': k3p, 'd_3m_p': d3p, 
#             'valid': True
#         }

#         return payload

#     except Exception as e:
#         # It is helpful to log exceptions here to debug calculation errors
#         # logger.error(f"Calc Error: {e}")
#         return None

# def cpu_bound_calculation(
#     closes_1m,
#     closes_3m,
#     last_price,
#     last_tick_ts,
#     # dc_low4_1m,
#     # dc_high4_1m,
#     # dc_low4_3m=None,
#     # dc_high4_3m=None,
# ):
#     try:
#         r1 = stoch_rsi_numpy(closes_1m)
#         if r1 is None:
#             return None

#         k1, d1, k1p, d1p, k1_slope = r1

#         r3 = stoch_rsi_numpy(closes_3m)
#         if r3:
#             k3, d3, k3p, d3p, k3_slope = r3
#         else:
#             k3 = d3 = k3p = d3p = 50.0

#         # --- Cross detection ---
#         # k1_cross_up   = k1p < d1p and k1 > d1
#         # k1_cross_down = k1p > d1p and k1 < d1

#         # k3_cross_up   = k3p < d3p and k3 > d3
#         # k3_cross_down = k3p > d3p and k3 < d3

#         # compression_1m = None
#         # if dc_low4_1m and dc_high4_1m and last_price:
#         #     compression_1m = (dc_high4_1m - dc_low4_1m) / last_price

#         # rank_score = None
#         # if compression_1m is not None:
#         #     rank_score = abs(k1_slope) * (1 - compression_1m) * (1 - abs(k1 - 50) / 50)

#         # payload['compression_1m'] = compression_1m
#         # payload['rank_score_comp'] = rank_score

#         # --- Zones ---
#         def zone(v):
#             if v < 20: return "OS"
#             if v > 80: return "OB"
#             return "MID"

#         payload = {
#             # --- Price ---
#             'price': last_price,
#             '_tick_ts': last_tick_ts,
#             'timestamp': last_tick_ts,
#             # 'candle_ts_1m': current_minute_ts,
#             # 'candle_ts_3m': current_3m_ts,
#             'is_partial_1m': True,
#             'is_partial_3m': True,

#             # --- 1m StochRSI ---
#             'k_1m': k1, 'd_1m': d1,
#             'k_1m_prev': k1p, 'd_1m_prev': d1p,
#             # 'k_1m_cross_up': k1_cross_up,
#             # 'k_1m_cross_down': k1_cross_down,
#             # 'k_1m_zone': zone(k1),

#             # --- 3m StochRSI ---
#             'k_3m': k3, 'd_3m': d3,
#             'k_3m_prev': k3p, 'd_3m_prev': d3p,
#             # 'k_3m_cross_up': k3_cross_up,
#             # 'k_3m_cross_down': k3_cross_down,
#             # 'k_3m_zone': zone(k3),

#             # # --- Donchian ---
#             # 'dc_low4_1m': dc_low4_1m,
#             # 'dc_high4_1m': dc_high4_1m,
#             # 'dc_mid4_1m': (
#             #     (dc_low4_1m + dc_high4_1m) / 2
#             #     if dc_low4_1m is not None and dc_high4_1m is not None
#             #     else None
#             # ),

#             # 'dc_low4_3m': dc_low4_3m,
#             # 'dc_high4_3m': dc_high4_3m,
#             # 'dc_mid4_3m': (
#             #     (dc_low4_3m + dc_high4_3m) / 2
#             #     if dc_low4_3m is not None and dc_high4_3m is not None
#             #     else None
#             # ),

#             'valid': True
#         }

#         return payload

#     except Exception:
#         return None

#         # def calc_rsi(closes, period=14, k_window=3, d_window=3):
#         #     # We need enough data for current AND previous candle
#         #     if not closes or len(closes) < period + k_window + d_window + 1: 
#         #         return None, None, None
#         #     series = pd.Series(closes)

#         #     # Freeze RSI history: everything except the last close
#         #     hist = series.iloc[:-1]
#         #     live_close = series.iloc[-1]

#         #     # Build RSI on historical closes
#         #     delta = hist.diff()
#         #     gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
#         #     loss = -delta.clip(upper=0).ewm(alpha=1/period, adjust=False).mean()

#         #     rs = gain / loss.replace(0.0, 1e-9)
#         #     rsi_hist = 100 - (100 / (1 + rs))
#         #     prev_close = hist.iloc[-1]
#         #     delta_live = live_close - prev_close

#         #     gain_live = max(delta_live, 0)
#         #     loss_live = max(-delta_live, 0)

#         #     avg_gain = gain.iloc[-1]
#         #     avg_loss = loss.iloc[-1]

#         #     avg_gain = (avg_gain * (period - 1) + gain_live) / period
#         #     avg_loss = (avg_loss * (period - 1) + loss_live) / period

#         #     rs_live = avg_gain / max(avg_loss, 1e-9)
#         #     rsi_live = 100 - (100 / (1 + rs_live))
#         #     rsi = pd.concat([rsi_hist, pd.Series([rsi_live])], ignore_index=True)

#         #     min_rsi = rsi.rolling(period).min()
#         #     max_rsi = rsi.rolling(period).max()
#         #     denom = (max_rsi - min_rsi).replace(0.0, 1e-9)

#         #     stoch = (rsi - min_rsi) / denom * 100
#         #     k = stoch.rolling(k_window).mean()
#         #     d = k.rolling(d_window).mean()

#         #     k_curr = k.iat[-1]
#         #     d_curr = d.iat[-1]
#         #     k_prev = k.iat[-2]
#         #     d_prev = d.iat[-2] 

#         #     if pd.isna(k_curr) or pd.isna(d_curr): return None, None, None
#         #     if pd.isna(k_prev): k_prev = k_curr

#         #     return k_curr, d_curr, k_prev, d_prev

#         # 1. Calculate 1m
#     #     k_1m, d_1m, k_1m_p, d_1m_p = calc_rsi(closes_1m)
#     #     if k_1m is None: return None

#     #     # 2. Calculate 3m
#     #     k_3m, d_3m, k_3m_p, d_3m_p = calc_rsi(closes_3m)
#     #     if k_3m is None: 
#     #         k_3m, d_3m, k_3m_p, d_3m_p = 50.0, 50.0, 50.0, 50.0

#     #     # 3. Build Payload
#     #     return {
#     #         'k_1m': round(float(k_1m), 2), 
#     #         'd_1m': round(float(d_1m), 2),
#     #         'k_1m_p': round(float(k_1m_p), 2),'d_1m_p': round(float(d_1m_p), 2),
#     #         'k_3m': round(float(k_3m), 2), 
#     #         'd_3m': round(float(d_3m), 2),'d_3m_p': round(float(d_3m_p), 2),
#     #         'k_3m_p': round(float(k_3m_p), 2),
#     #         'price': last_price,
#     #         'ts1': int(time.time()),
#     #         '_tick_ts': last_tick_ts,
#     #         'timestamp': last_tick_ts,
#     #         'valid': True,
#     #         'dc_low4_1m': round(dc_low4, 6) if dc_low4 else None,
#     #         'dc_high4_1m': round(dc_high4, 6) if dc_high4 else None,
#     #         'is_live': True
#     #     }
#     # except Exception as e:
#     #     return None

# class KlineLoader:
#     @staticmethod
#     def load_history(symbol: str, interval: str) -> dict:
#         """ 
#         Scans KLINES_DIRS. Returns the first valid dataset found.
#         """
#         filenames = [f"{symbol}_{interval}.json", f"{symbol.lower()}_{interval}.json"]
        
#         # Iterate through your configured folders in priority order
#         for cache_dir in KLINES_DIRS:
#             # Expand path for safety
#             base_dir = Path(cache_dir).expanduser().resolve()
            
#             for fname in filenames:
#                 path = base_dir / fname
#                 if not path.exists(): 
#                     continue
                
#                 # Load (Repairing if necessary)
#                 data = resilient_json_load(path)
                
#                 # Validation: Must be a non-empty list
#                 if not data or not isinstance(data, list) or len(data) == 0: 
#                     continue
                
#                 # Parse Data
#                 history_map = {}
#                 # Take last 1000 candles
#                 for item in data[-1000:]: 
#                     try:
#                         ts = 0.0
#                         close = 0.0
                        
#                         # Handle Dict Format {"timestamp": "...", "close": ...}
#                         if isinstance(item, dict):
#                             raw_ts = item.get('timestamp') or item.get('time')
#                             close = float(item.get('close', 0))
                            
#                             if isinstance(raw_ts, str):
#                                 # Fast ISO parsing
#                                 if raw_ts.endswith('Z'): raw_ts = raw_ts.replace('Z', '+00:00')
#                                 ts = isoparse(raw_ts).timestamp()
#                             else:
#                                 ts = float(raw_ts)

#                         # Handle List Format [time, open, high, low, close]
#                         elif isinstance(item, list) and len(item) >= 5:
#                             ts = float(item[0])
#                             close = float(item[4])
                        
#                         # Normalize MS to Seconds
#                         if ts > 1e11: ts /= 1000.0
                        
#                         if ts > 0 and close > 0:
#                             history_map[int(ts)] = close
#                     except: 
#                         continue
#                 if history_map:
#                     return history_map  
#         return {}

# class HighResMarketData:
#     def __init__(self):
#         self.data = {} 
#         self._lock = asyncio.Lock() # Not strictly used in sync methods, but good practice concept


#     def init_symbol_from_disk(self, symbol: str):
#         """ 
#         Synchronous loader intended to be run in a thread.
#         """
#         if symbol in self.data: return

#         # Load history from disk
#         hist_1m = KlineLoader.load_history(symbol, '1m')
#         hist_3m = KlineLoader.load_history(symbol, '3m')
        
#         # Calculate last known state from file
#         last_price = 0.0
#         last_ts = 0
        
#         # Prefer 1m data for latest price
#         if hist_1m:
#             last_ts = max(hist_1m.keys())
#             last_price = hist_1m[last_ts]
#         elif hist_3m:
#             last_ts = max(hist_3m.keys())
#             last_price = hist_3m[last_ts]

#         self.data[symbol] = {
#             'last_price': None,
#             'last_tick_ts': None,
#             'minute_cache': {},         
#             'seconds_buffer': {},   
#         }
        
#         # Debug Log to prove data loaded
#         if len(hist_1m) > 0:
#             logger.info(f"✅ Loaded {symbol}: {len(hist_1m)} 1m candles, Last: {last_price}")
#         else:
#             logger.warning(f"⚠️ {symbol}: NO DATA loaded from disk")

#     def get_last_n_seconds(self, symbol: str, n: int = 60):
#         store = self.data.get(symbol)
#         if not store:
#             return None

#         last_ts = store.get('last_tick_ts')
#         if last_ts is None:
#             return None  # symbol not ready yet

#         buf = store.get('seconds_buffer')
#         if not buf:
#             return None

#         cutoff = last_ts - n
#         return [
#             price for ts, price in buf.items()
#             if ts >= cutoff
#         ]



#     def update(self, symbol: str, price: float, timestamp: float):
#         if symbol not in self.data:
#             return

#         store = self.data[symbol]

#         ts = int(timestamp)
#         store['last_price'] = price
#         store['last_tick_ts'] = ts

#         # --- seconds buffer (MAX 120s) ---
#         sb = store.setdefault('seconds_buffer', {})
#         sb[ts] = price
#         cutoff = ts - 120
#         for k in list(sb.keys()):
#             if k < cutoff:
#                 del sb[k]

#         # --- minute cache ---
#         minute = (ts // 60) * 60
#         mc = store.setdefault('minute_cache', {})

#         c = mc.setdefault(minute, {
#             'open': price,
#             'high': price,
#             'low': price,
#             'close': price,
#         })

#         c['high'] = max(c['high'], price)
#         c['low'] = min(c['low'], price)
#         c['close'] = price

#         # prune > 40 minutes
#         cutoff_min = minute - 2400
#         for k in list(mc.keys()):
#             if k < cutoff_min:
#                 del mc[k]


                
#     def get_snapshot(self, symbol):
#         store = self.data.get(symbol)
#         if not store:
#             return None

#         if store.get('last_price') is None:
#             return None

#         c1 = self._build_candles(store, '1m')
#         c3 = self._build_candles(store, '3m')

#         if not c1:
#             return None

#         # dc_low4, dc_high4 = self.get_dc_last_4m(store)

#         return (
#             c1,
#             c3,
#             store['last_price'],
#             store['last_tick_ts'],
#             # dc_low4,
#             # dc_high4,
#         )


#     def _build_candles(self, store, tf):
#         if store.get('last_price') is None:
#             return None

#         interval = 60 if tf == '1m' else 180
#         needed = 60

#         now = store['last_tick_ts']
#         base = now - (now % interval)

#         minutes = store.get('minute_cache', {})
#         closes = []

#         for i in range(needed):
#             ts = base - (needed - 1 - i) * interval
#             if ts in minutes:
#                 closes.append(minutes[ts]['close'])
#             elif closes:
#                 closes.append(closes[-1])

#         if not closes:
#             return None

#         # live candle overwrite
#         closes[-1] = store['last_price']

#         # HARD CAP
#         if len(closes) > needed:
#             closes = closes[-needed:]

#         return closes


#     # def _build_candles(self, store, tf):
#     #     needed = 60
#     #     interval = 60 if tf == '1m' else 180
#     #     now_ts = time.time()
#     #     current_grid_ts = int(now_ts // interval) * interval
#     #     timestamps = [current_grid_ts - (i * interval) for i in range(needed)]
#     #     timestamps.reverse()
        
#     #     final_closes = []
#     #     file_history = store[f'file_history_{tf}']
#     #     sec_buffer = store['seconds_buffer']
#     #     cutoff_live = now_ts - 2400 # 30 mins

#     #     for target_ts in timestamps:
#     #         price = None
            
#     #         # 1. Check Live Buffer (Precise second match)
#     #         if target_ts in sec_buffer:
#     #             price = sec_buffer[target_ts]
            
#     #         # 2. Check File History (If buffer missing)
#     #         elif target_ts in file_history:
#     #             price = file_history[target_ts]
#     #         else:
#     #             if target_ts in sec_buffer:
#     #                 price = sec_buffer[target_ts]
#     #             else:
#     #                 prev_sec = sec_buffer.get(target_ts - 1)
#     #                 next_sec = sec_buffer.get(target_ts + 1)
#     #                 if prev_sec and next_sec:
#     #                     price = (prev_sec + next_sec) / 2.0
#     #                 elif prev_sec:
#     #                     price = prev_sec
#     #                 elif next_sec:
#     #                     price = next_sec
#     #             if price is None:
#     #                 for offset in [1, -1, 2, -2, 3, -3, 4, -4]:
#     #                     if (target_ts + offset) in sec_buffer:
#     #                         price = sec_buffer[target_ts + offset]
#     #                         break
#     #         if price:
#     #             final_closes.append(price)
#     #         elif final_closes:
#     #             final_closes.append(final_closes[-1])
#     #         else:
#     #             final_closes.append(0.0) 
#     #     final_closes = [x for x in final_closes if x > 0]
#     #     expected_ts = int(time.time() // interval) * interval
#     #     if len(final_closes) > 0 and store['last_price'] > 0:
#     #         final_closes[-1] = store['last_price']
#     #     if timestamps[-1] != expected_ts:
#     #         final_closes.append(store['last_price'])
#     #     return final_closes


# class MarketDataEngine:
#     def __init__(self):
#         self.redis = Redis.from_url(REDIS_URL, decode_responses=True)
#         self.market = HighResMarketData()
#         self.symbols = set()
#         self.running = True
#         self.shared_proxy = None
#         self._connect_shared()

#     def _connect_shared(self):
#         if get_shared_memory_client:
#             try:
#                 mgr = get_shared_memory_client()
#                 if mgr: self.shared_proxy = mgr.get_store()  # pylint: disable=no-member
#             except: pass
#     async def load_symbols(self):
#         logger.info(f"📚 Loading symbols from {BASE_PATH}...")
#         universe = set()
        
#         for fname in SYMBOLS_FILES:
#             f = BASE_PATH / fname
#             if f.exists():
#                 # resilient_json_load returns a LIST/DICT now, so this works
#                 d = resilient_json_load(f)
#                 if isinstance(d, list): 
#                     for x in d: universe.add(x.split(':')[-1].upper() if ':' in x else x.upper())
#                 elif isinstance(d, dict):
#                     # Handle {"symbols": [...]} format
#                     vals = d.get('symbols') or d.get('pairs') or []
#                     for x in vals: universe.add(str(x).upper())
#             else:
#                 logger.warning(f"⚠️ Symbol file missing: {f}")
#         self.symbols = {s for s in universe if "USDT" in s or "USDC" in s}
#         logger.info(f"✅ Monitoring {len(self.symbols)} symbols")
#     # async def load_symbols(self):
#     #     logger.info("📚 Loading symbols...")
#     #     universe = set()
#     #     for fname in SYMBOLS_FILES:
#     #         f = BASE_PATH / fname
#     #         if f.exists():
#     #             d = resilient_json_load(f)
#     #             if isinstance(d, list): 
#     #                 for x in d: universe.add(x.split(':')[-1].upper() if ':' in x else x.upper())
#     #             elif isinstance(d, dict):
#     #                 for x in d.get('symbols', []): universe.add(str(x).upper())
#     #     self.symbols = {s for s in universe if "USDT" in s or "USDC" in s}
        
#         logger.info(f"⚡ Initializing buffers for {len(self.symbols)} symbols...")
#         for s in self.symbols: 
#             self.market.init_symbol_from_disk(s)
#         logger.info(f"✅ Ready.")


#     async def broadcast_loop(self):
#         logger.info("📡 Starting Calculation Loop (Threaded)...")
#         loop = asyncio.get_running_loop()
        
#         while self.running:
#             loop_start = time.time()
#             active_symbols = list(self.market.data.keys())

#             tasks_data = []
#             for sym in active_symbols:
#                 snap = self.market.get_snapshot(sym)
#                 if not snap:
#                     continue  # skip symbols that are not ready

#                 c1, c3, price, ts, = snap
#                 # if c1 and len(c1) > 20:
#                 #     tasks_data.append((sym, c1, c3, price, ts, dc_low4, dc_high4))

#             if not tasks_data:
#                 await asyncio.sleep(1)
#                 continue

#             # --- 2. EXECUTE MATH (Thread Pool) ---
#             results = []
#             chunk_size = 50
#             for i in range(0, len(tasks_data), chunk_size):
#                 chunk = tasks_data[i:i+chunk_size]
#                 futures = []

#                 for item in chunk:
#                     sym, c1, c3, p, t= item
#                     # if you want to keep it sync for now:
#                     res = cpu_bound_calculation(c1, c3, p, t)
#                     if res:
#                         results.append((sym, res))
#     # async def broadcast_loop(self):
#     #     logger.info("📡 Starting Calculation Loop (Threaded)...")
#     #     loop = asyncio.get_running_loop()
        
#     #     while self.running:
#     #         loop_start = time.time()
#     #         active_symbols = list(self.market.data.keys())
            
#     #         # --- 1. PREPARE DATA (Main Thread) ---
#     #         # Extract raw data lists quickly so we don't block WS updates
#     #         tasks_data = []
#     #         for sym in active_symbols:
#     #             c1, c3, price, ts, dc_low4, dc_high4 = self.market.get_snapshot(sym)
#     #             if c1 and len(c1) > 20:
#     #                 tasks_data.append((sym, c1, c3, price, ts, dc_low4, dc_high4))
            
#     #         if not tasks_data:
#     #             await asyncio.sleep(1)
#     #             continue

#     #         # --- 2. EXECUTE MATH (Thread Pool) ---
#     #         # We submit calculation tasks to threads to keep Event Loop free
#     #         results = []
            
#     #         # Split into chunks if too many symbols (prevents clogging executor)
#     #         chunk_size = 50
#     #         for i in range(0, len(tasks_data), chunk_size):
#     #             chunk = tasks_data[i:i+chunk_size]
#     #             futures = []
                
#     #             for item in chunk:
#     #                 sym, c1, c3, p, t, dcl, dch = item
#     #                 # Run CPU bound function in executor
#     #                 res = cpu_bound_calculation(c1, c3, p, t, dcl, dch)

#     #                 # fut = loop.run_in_executor(
#     #                 #     self.executor, 
#     #                 #     cpu_bound_calculation, 
#     #                 #     c1, c3, p, t, dcl, dch
#     #                 # )
#     #                 futures.append((sym, fut))
                
#                 # Await this chunk
#                 for sym, fut in futures:
#                     try:
#                         res = await fut
#                         if res: results.append((sym, res))
#                     except Exception as e:
#                         logger.error(f"Calc error {sym}: {e}")

#             # --- 3. PUBLISH (Async Redis) ---
#             if results:
#                 async with self.redis.pipeline() as pipe:
#                     for sym, payload in results:
#                         if self.shared_proxy: 
#                             self.shared_proxy.update_symbol(sym, payload)
                        
#                         pipe.set(f"hot_metrics_ts:{sym}", payload['_tick_ts'], ex=60)
#                         pipe.set(f"hot_metrics:{sym}", orjson.dumps(payload), ex=60)  # pylint: disable=no-member
                    
#                     await pipe.execute()
#                 ranked = [
#                     (sym, p['rank_score'])
#                     for sym, p in results
#                     if p.get('rank_score') is not None
#                 ]

#                 ranked.sort(key=lambda x: x[1], reverse=True)
#                 top = ranked[:20]

#                 async with self.redis.pipeline() as pipe:
#                     pipe.set("hot_ranked_symbols", orjson.dumps(top), ex=5)  # pylint: disable=no-member
#                     await pipe.execute()
#             # --- 4. HEALTH CHECK ---
#             elapsed = time.time() - loop_start
#             if elapsed > 2.0:
#                 logger.warning(f"⚠️ Loop lag: {elapsed:.2f}s for {len(results)} symbols")
#             await asyncio.sleep(max(0.1, 0.5 - elapsed))

#     async def heartbeat(self):
#         """Just prints a alive message so you know it's not frozen"""
#         while self.running:
#             logger.info(f"💓 Engine Alive | Symbols: {len(self.market.data)} | Pool: Active")
#             await asyncio.sleep(30)

#     async def ws_connect(self): 
#         url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
#         while self.running:
#             try:
#                 session_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=10)
#                 async with aiohttp.ClientSession(timeout=session_timeout) as session:
#                     async with session.ws_connect(url, heartbeat=15) as ws:
#                         logger.info("🔌 WS Connected")
#                         async for msg in ws:
#                             if msg.type == aiohttp.WSMsgType.TEXT:
#                                 try:
#                                     data = orjson.loads(msg.data)  # pylint: disable=no-member
#                                     ts_now = time.time()
#                                     count = 0
#                                     for p in data:
#                                         s = p['s']
#                                         if s in self.symbols:
#                                             # Using event time
#                                             self.market.update(s, float(p['p']), p['E']/1000.0)
#                                             count += 1
#                                 except Exception as e: 
#                                     logger.error(f"Parse error: {e}")
#                             elif msg.type == aiohttp.WSMsgType.ERROR:
#                                 logger.error("WS Error")
#                                 break
#             except Exception as e:
#                 logger.error(f"WS Connection failed: {e}")
#                 await asyncio.sleep(5)

#     async def main(self):
#         await self.load_symbols()
#         try:
#             # Run everything together
#             await asyncio.gather(
#                 self.ws_connect(), 
#                 self.broadcast_loop(),
#                 self.heartbeat()
#             )
#         except KeyboardInterrupt:
#             self.running = False
#             self.executor.shutdown(wait=False)
#             logger.info("Stopping...")

# if __name__ == "__main__":
#     asyncio.run(MarketDataEngine().main())
    
# # import asyncio
# # import logging
# # import math
# # import os
# # import platform
# # import time
# # from collections import OrderedDict
# # from concurrent.futures import ThreadPoolExecutor
# # from datetime import datetime, timezone
# # from logging.handlers import RotatingFileHandler
# # from pathlib import Path
# # import aiofiles
# # import aiohttp
# # import numpy as np
# # import orjson
# # from dateutil.parser import isoparse
# # from redis.asyncio import Redis

# # from config import Config
# # from utils import (get_current_environment, get_simple_redis_manager,
# #                    orjson_default)

# # # --- CONFIGURATION ---
# # config = Config()
# # BASE_PATH = Path(config.BASE_PATH)
# # env = get_current_environment()

# # def _resolve_base_path() -> Path:
# #     env_base = os.environ.get("BASE_PATH")
# #     if env_base:
# #         base = Path(env_base).expanduser()
# #         candidates = [base]
# #         if base.name.lower() != "binance":
# #             candidates.append(base / "binance")
# #         for candidate in candidates:
# #             try:
# #                 if candidate.exists(): return candidate
# #             except: pass
# #         return candidates[0]
# #     system_name = platform.system()
# #     if system_name == "Darwin": return Path("/Users/niels/Documents/binance")
# #     if system_name == "Linux": return Path("/home/niels/binance")
# #     return Path.home() / "Documents" / "binance"

# # BASE_PATH = _resolve_base_path()
# # KLINES_DIRS = [
# #     BASE_PATH / "klines_cache",
# #     BASE_PATH / "klines_cache_gateway",
# #     BASE_PATH / "klines_cache_macbook"]

# # # Fallback cache directories
# # PRICE_CACHE_JSON_DICTS = [
# #     BASE_PATH / "price_cache_1",
# #     BASE_PATH / "price_cache_2",
# #     BASE_PATH / "price_cache_3"
# # ]

# # SYMBOLS_FILES = ["symbols_active.json"]

# # LOG_FILE = Path.home() / "logs" / "ez_market_data.log"

# # # --- SHARED MEMORY ---
# # try:
# #     from ez_share_ind import get_shared_memory_client
# # except ImportError:
# #     get_shared_memory_client = None

# # # --- LOGGING ---
# # Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
# # logging.basicConfig(
# #     level=logging.INFO,
# #     format="%(asctime)s [%(levelname)s] %(message)s",
# #     handlers=[RotatingFileHandler(LOG_FILE, maxBytes=20971520, backupCount=5), logging.StreamHandler()])
# # logger = logging.getLogger("DataEngine")

# # def resilient_json_load(path: Path):
# #     try:
# #         if not path.exists(): return None
# #         with open(path, 'rb') as f: raw = f.read()
# #         if not raw: return None
# #         try: return orjson.loads(raw)  # pylint: disable=no-member
# #         except: pass
# #         text = raw.decode('utf-8', errors='ignore').strip()
# #         if not text.startswith('['): return None
# #         last_obj = text.rfind('}')
# #         if last_obj == -1: return None
# #         return orjson.loads(text[:last_obj+1] + "]")  # pylint: disable=no-member
# #     except: return None

# # def ts_to_z(ts: float) -> str:
# #     """Epoch seconds → ISO-8601 UTC Z (ms precision)"""
# #     return (
# #         datetime
# #         .fromtimestamp(ts, tz=timezone.utc)
# #         .isoformat(timespec="milliseconds")
# #         .replace("+00:00", "Z")  )

# # def z_to_ts(z: str) -> float:
# #     """ISO-8601 Z → epoch seconds"""
# #     return isoparse(z).timestamp()

# # def update_1m_candle(store, price, tick_ts):
# #     minute_ts = tick_ts - (tick_ts % 60)
# #     store['closes_1m'][minute_ts] = price
# #     store['last_tick_ts'] = tick_ts
# #     while len(store['closes_1m']) > 180:
# #         old_ts, _ = store['closes_1m'].popitem(last=False)
# #         store['synthetic_1m'].discard(old_ts)


# # def update_3m_candle(store, price, tick_ts):
# #     tf_ts = tick_ts - (tick_ts % 180)
# #     store['closes_3m'][tf_ts] = price
# #     while len(store['closes_3m']) > 180:
# #         old_ts, _ = store['closes_3m'].popitem(last=False)
# #         store['synthetic_3m'].discard(old_ts)

# # def interpolate_missing_1m(store):
# #     c1 = store['closes_1m']
# #     c3 = store['closes_3m']
    
# #     # If 1m is missing but we have 3m, seed 1m with 3m
# #     if not c1 and c3:
# #         for t, p in c3.items():
# #             c1[t] = p
# #             c1[t+60] = p
# #             c1[t+120] = p
# #             store['synthetic_1m'].update([t, t+60, t+120])
            
# #     if len(c1) < 2 or len(c3) < 2:
# #         return
# #     timestamps = list(c1.keys())
# #     for i in range(1, len(timestamps)):
# #         prev_ts = timestamps[i - 1]
# #         curr_ts = timestamps[i]
# #         expected = prev_ts + 60
# #         if curr_ts == expected:
# #             continue
# #         ts = expected
# #         while ts < curr_ts:
# #             prev_3m = max((t for t in c3 if t <= ts), default=None)
# #             next_3m = min((t for t in c3 if t >= ts), default=None)
# #             if prev_3m and next_3m and prev_3m != next_3m:
# #                 p0 = c3[prev_3m]
# #                 p1 = c3[next_3m]
# #                 alpha = (ts - prev_3m) / (next_3m - prev_3m)
# #                 price = p0 + alpha * (p1 - p0)
# #             else:
# #                 price = c1[prev_ts]
# #             c1[ts] = price
# #             store['synthetic_1m'].add(ts)
# #             ts += 60
# # def rebuild_3m_from_1m(store):
# #     c1 = store['closes_1m']
# #     c3 = store['closes_3m']

# #     for ts in list(c1.keys()):
# #         tf_ts = ts - (ts % 180)
# #         if tf_ts not in c3:
# #             window = [
# #                 c1.get(tf_ts),
# #                 c1.get(tf_ts + 60),
# #                 c1.get(tf_ts + 120),
# #             ]
# #             window = [p for p in window if p is not None]
# #             if window:
# #                 c3[tf_ts] = sum(window) / len(window)
# #                 store['synthetic_3m'].add(tf_ts)



# # # -----------------------------------------------------------------------------
# # # 1. PURE NUMPY CALCULATION (Non-Blocking, Allocation Efficient)
# # # -----------------------------------------------------------------------------
# # def sma(values, period):
# #     if len(values) < period:
# #         return []

# #     out = []
# #     for i in range(period - 1, len(values)):
# #         out.append(sum(values[i - period + 1:i + 1]) / period)
# #     return out

# # # def compute_rsi(closes, period=14):
# # #     if len(closes) < period + 1:
# # #         return []

# # #     gains = []
# # #     losses = []

# # #     for i in range(1, len(closes)):
# # #         diff = closes[i] - closes[i - 1]
# # #         gains.append(max(diff, 0))
# # #         losses.append(max(-diff, 0))

# # #     avg_gain = sum(gains[:period]) / period
# # #     avg_loss = sum(losses[:period]) / period

# # #     rsi = []

# # #     for i in range(period, len(gains)):
# # #         avg_gain = (avg_gain * (period - 1) + gains[i]) / period
# # #         avg_loss = (avg_loss * (period - 1) + losses[i]) / period

# # #         if avg_loss == 0:
# # #             rsi.append(100.0)
# # #         else:
# # #             rs = avg_gain / avg_loss
# # #             rsi.append(100 - (100 / (1 + rs)))
# # #     return rsi

# # def stoch_rsi_numpy(closes, period=14, k_window=3, d_window=3):
# #     n = len(closes)
# #     if n < period + k_window + d_window + 2: return None
    
# #     # Copy to ensure we don't mutate shared memory (just in case)
# #     src = np.array(closes, dtype=np.float64)
# #     deltas = np.diff(src)
# #     gains = np.maximum(deltas, 0.0)
# #     losses = np.maximum(-deltas, 0.0)

# #     # Fast Wilder's Smoothing
# #     avg_gain = np.empty(len(gains))
# #     avg_loss = np.empty(len(losses))
# #     avg_gain[0] = gains[0] 
# #     avg_loss[0] = losses[0]
    
# #     alpha = 1.0 / period
# #     w = 1.0 - alpha
    
# #     for i in range(1, len(gains)):
# #         avg_gain[i] = w * avg_gain[i-1] + alpha * gains[i]
# #         avg_loss[i] = w * avg_loss[i-1] + alpha * losses[i]

# #     with np.errstate(divide='ignore', invalid='ignore'):
# #         rs = avg_gain / avg_loss
# #         rsi = 100.0 - (100.0 / (1.0 + rs))
    
# #     # Fix flatlining
# #     rsi[avg_loss == 0] = 100.0
# #     rsi = np.nan_to_num(rsi, nan=50.0)

# #     # Stoch Calc (Valid tail only)
# #     calc_start = period
# #     stoch_series = np.zeros(len(rsi))
# #     for i in range(calc_start, len(rsi)):
# #         window = rsi[i-period+1 : i+1]
# #         min_val = np.min(window)
# #         max_val = np.max(window)
# #         if max_val == min_val:
# #             stoch_series[i] = 100.0 if min_val > 50 else 0.0
# #         else:
# #             stoch_series[i] = (rsi[i] - min_val) / (max_val - min_val) * 100.0

# #     # SMA (K & D)
# #     ones_k = np.ones(k_window) / k_window
# #     k_line = np.convolve(stoch_series, ones_k, mode='valid')
    
# #     ones_d = np.ones(d_window) / d_window
# #     d_line = np.convolve(k_line, ones_d, mode='valid')

# #     if len(d_line) < 2: return None
# #     return k_line[-1], d_line[-1], k_line[-2], d_line[-2]

# # def cpu_bound_calculation(closes_1m, closes_3m, last_price, last_tick_ts):
# #     try:
        
# #         res1 = stoch_rsi_numpy(closes_1m)
# #         if res1 is None: return None
# #         k1, d1, k1p, d1p = res1
        
# #         # 3m Calc
# #         res3 = stoch_rsi_numpy(closes_3m)
# #         if res3: 
# #             k3, d3, k3p, d3p = res3
# #         else: 
# #             k3, d3, k3p, d3p = 50.0, 50.0, 50.0, 50.0

# #         return {
# #             'price': last_price,
# #             '_tick_ts': last_tick_ts,
# #             'timestamp': ts_to_z(last_tick_ts),
# #             'ts1': ts_to_z(last_tick_ts),

# #             'valid': True,

# #             'k_1m': round(float(k1), 2),
# #             'd_1m': round(float(d1), 2),
# #             'k_1m_prev': round(float(k1p), 2),
# #             'd_1m_prev': round(float(d1p), 2),

# #             'k_3m': round(float(k3), 2),
# #             'd_3m': round(float(d3), 2),
# #             'k_3m_prev': round(float(k3p), 2),
# #             'd_3m_prev': round(float(d3p), 2),
# #         }

# #     except Exception:
# #         return None



# # class KlineLoader:
# #     @staticmethod
# #     def load_history(symbol: str, interval: str) -> dict:
# #         filenames = [f"{symbol}_{interval}.json", f"{symbol.lower()}_{interval}.json"]
# #         for cache_dir in KLINES_DIRS:
# #             base_dir = Path(cache_dir).expanduser().resolve()
# #             for fname in filenames:
# #                 path = base_dir / fname
# #                 data = resilient_json_load(path)
# #                 if not data or not isinstance(data, list): continue
                
# #                 history_map = {}
# #                 # Optimized: We only need last ~80 for calculation
# #                 for item in data[-100:]: 
# #                     try:
# #                         ts = 0.0
# #                         close = 0.0
# #                         if isinstance(item, dict):
# #                             raw_ts = item.get('timestamp') or item.get('time')
# #                             close = float(item.get('close', 0))
# #                             if isinstance(raw_ts, str):
# #                                 if raw_ts.endswith('Z'): raw_ts = raw_ts.replace('Z', '+00:00')
# #                                 ts = isoparse(raw_ts).timestamp()
# #                             else: ts = float(raw_ts)
# #                         elif isinstance(item, list) and len(item) >= 5:
# #                             ts = float(item[0])
# #                             close = float(item[4])
# #                         if ts > 1e11: ts /= 1000.0
# #                         if ts > 0 and close > 0: history_map[int(ts)] = close
# #                     except: continue
# #                 if history_map: return history_map
# #         return {}

# # def init_empty_store():
# #     return {
# #         'closes_1m': OrderedDict(),   # ts -> close
# #         'closes_3m': OrderedDict(),   # ts -> close
# #         'synthetic_1m': set(),
# #         'synthetic_3m': set(),
# #         'last_tick_ts': None,
# #         'last_calc_ts': 0.0, }

# # def should_recalculate(store, now):
# #     last_used_ts = store.get('last_calc_ts', 0)
# #     last_tick_ts = store.get('last_tick_ts', 0)
# #     if last_tick_ts > last_used_ts:
# #         return True
# #     if (now - last_used_ts) >= 10.0:
# #         return True
# #     return False


# # # -----------------------------------------------------------------------------
# # # 2. MARKET DATA STORE (Fast Updates, Zero Locking)
# # # -----------------------------------------------------------------------------
# # class HighResMarketData:
# #     def __init__(self):
# #         self.data = {} 


# #     def init_symbol_from_disk(self, symbol: str):
# #         if symbol in self.data:
# #             return

# #         hist_1m_map = KlineLoader.load_history(symbol, '1m')  # ts -> close
# #         hist_3m_map = KlineLoader.load_history(symbol, '3m')  # ts -> close

# #         closes_1m = OrderedDict()
# #         closes_3m = OrderedDict()

# #         # load 1m with timestamps
# #         for ts in sorted(hist_1m_map.keys()):
# #             closes_1m[int(ts)] = float(hist_1m_map[ts])

# #         # load 3m with timestamps
# #         for ts in sorted(hist_3m_map.keys()):
# #             closes_3m[int(ts)] = float(hist_3m_map[ts])

# #         # keep last ~1800 (you said they always exist)
# #         while len(closes_1m) > 1800:
# #             closes_1m.popitem(last=False)

# #         while len(closes_3m) > 1800:
# #             closes_3m.popitem(last=False)

# #         store = {
# #             'closes_1m': closes_1m,
# #             'closes_3m': closes_3m,
# #             'synthetic_1m': set(),
# #             'synthetic_3m': set(),
# #             'last_price': list(closes_1m.values())[-1] if closes_1m else 0.0,
# #             'last_tick_ts': time.time(),
# #             'last_calc_ts': 0.0,
# #         }

# #         # 🔥 FIX continuity problems at startup
# #         interpolate_missing_1m(store)
# #         rebuild_3m_from_1m(store)

# #         self.data[symbol] = store

# #         logger.info(
# #             f"✅ Loaded {symbol}: "
# #             f"{len(store['closes_1m'])}×1m / {len(store['closes_3m'])}×3m "
# #             f"P:{store['last_price']} " )

# #     def resolve_hot_price(self, symbol, price, tick_ts):
# #         store = self.data.get(symbol)
# #         if not store:
# #             return

# #         # 🔥 THIS is where rolling candles are built
# #         update_1m_candle(store, price, tick_ts)
# #         update_3m_candle(store, price, tick_ts)

# #         store['last_price'] = price

# #     # def update(self, symbol: str, price: float, timestamp: float):
# #     #     store = self.data.get(symbol)
# #     #     if not store: return

# #     #     ts = int(timestamp)
# #     #     self.resolve_hot_price(self, symbol, price, timestamp)
# #     #     store['closes_1m'] = OrderedDict()  # ts -> close
# #     #     store['closes_3m'] = OrderedDict()

# #     #     # --- 1 Minute Rollover ---
# #     #     # "REPLACE the last kline logic":
# #     #     # If we cross the boundary, the last known price IS the close of the previous minute.
# #     #     bucket_1m = ts // 60
# #     #     if bucket_1m > store['last_bucket_1m']:
# #     #         store['closes_1m'].append(price) # Use current price as close of prev (approximation, but fast)
# #     #         if len(store['closes_1m']) > 80: store['closes_1m'].pop(0)
# #     #         store['last_bucket_1m'] = bucket_1m

# #     #     # --- 3 Minute Rollover ---
# #     #     bucket_3m = ts // 180
# #     #     if bucket_3m > store['last_bucket_3m']:
# #     #         store['closes_3m'].append(price)
# #     #         if len(store['closes_3m']) > 40: store['closes_3m'].pop(0)
# #     #         store['last_bucket_3m'] = bucket_3m

# #     def get_snapshot(self, symbol: str):
# #         store = self.data.get(symbol)
# #         if not store or not store['closes_1m']: return None, None, 0, 0
# #         c1 = list(store['closes_1m'].values())
# #         c3 = list(store['closes_3m'].values())
# #         c1.append(store['last_price']) # Live Candle
# #         c3.append(store['last_price']) # Live Candle

# #         return c1, c3, store['last_price'], store['last_tick_ts']
# # class DummyLock:
# #     async def __aenter__(self):
# #         pass
# #     async def __aexit__(self, exc_type, exc_val, exc_tb):
# #         pass
# # # -----------------------------------------------------------------------------
# # # 3. ENGINE (Async, Threaded Fallback, Non-Blocking)
# # # -----------------------------------------------------------------------------

# # class MarketDataEngine:
# #     def __init__(self):
# #         self.redis = None
# #         self.market = HighResMarketData()
# #         self.symbols = set()
# #         self.running = True
# #         self.shared_proxy = None  
# #         self.shm_lock = DummyLock      
# #         self.executor = ThreadPoolExecutor(max_workers=4) 
# #         self._connect_shared()

# #     def _connect_shared(self):
# #         if get_shared_memory_client:
# #             try:
# #                 mgr = get_shared_memory_client()
# #                 if mgr: self.shared_proxy = mgr.get_store() 
# #             except: pass

# #     async def shared_mem_watchdog(self):
# #         while self.running:
# #             if not self.shared_proxy:
# #                 try:
# #                     self._connect_shared()
# #                     if self.shared_proxy:
# #                         logger.info("🔁 Shared memory reconnected")
# #                 except:
# #                     pass
# #             await asyncio.sleep(5)

# #     async def load_symbols(self):
# #         logger.info("📚 Loading symbols...")
# #         universe = set()
# #         for fname in SYMBOLS_FILES:
# #             f = BASE_PATH / fname
# #             if f.exists():
# #                 d = resilient_json_load(f)
# #                 if isinstance(d, list): 
# #                     for x in d: universe.add(x.split(':')[-1].upper() if ':' in x else x.upper())
# #                 elif isinstance(d, dict):
# #                     for x in d.get('symbols', []): universe.add(str(x).upper())
# #         self.symbols = {s for s in universe if "USDT" in s or "USDC" in s}
        
# #         logger.info(f"⚡ Initializing buffers for {len(self.symbols)} symbols...")
# #         for s in self.symbols: self.market.init_symbol_from_disk(s)
# #         logger.info(f"✅ Ready.")

# #     async def _redis_fallback_poll(self):
# #         """
# #         Polls Redis for symbols that haven't received WS updates in > 4s.
# #         Uses robust timestamp parsing.
# #         """
# #         while self.running:
# #             try:
# #                 now = time.time()
# #                 # Identify stale symbols (no tick in 4s)
# #                 stale_symbols = [
# #                     sym for sym, data in self.market.data.items()
# #                     if (now - data.get('last_tick_ts', 0)) > 4.0
# #                 ]

# #                 if stale_symbols:
# #                     for s in stale_symbols:
# #                         try:
# #                             raw = await self.redis.get(f"mark_price:{s}")
# #                             if raw:
# #                                 obj = orjson.loads(raw)  # pylint: disable=no-member
# #                                 p = float(obj.get('price', 0))
                                
# #                                 # Robust Parsing
# #                                 ts_raw = obj.get('timestamp')
# #                                 ts = 0.0
# #                                 if isinstance(ts_raw, (int, float)):
# #                                     ts = float(ts_raw)
# #                                 elif isinstance(ts_raw, str):
# #                                     try: ts = isoparse(ts_raw).timestamp()
# #                                     except: pass

# #                                 if p > 0 and ts > 0:
# #                                     # Update local market store
# #                                     self.market.resolve_hot_price(s, p, ts)
# #                         except Exception: pass

# #                     # If still very stale (>15s), try disk fallback via thread pool
# #                     very_stale = [s for s in stale_symbols if (now - self.market.data[s]['last_tick_ts']) > 15.0]
# #                     # (Existing logic for _sync_read_file_fallback would go here if needed, 
# #                     # but usually Redis is sufficient for fallback)

# #             except Exception as e:
# #                 logger.error(f"Fallback error: {e}")
            
# #             await asyncio.sleep(0.5)

# #     async def heartbeat(self):
# #         while self.running:
# #             logger.info(f"💓 Alive | Symbols: {len(self.market.data)}")
# #             await asyncio.sleep(30)
            
# #     async def ws_connect(self): 
# #         url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
# #         while self.running:
# #             try:
# #                 # Keep session alive
# #                 session_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=10)
# #                 async with aiohttp.ClientSession(timeout=session_timeout) as session:
# #                     async with session.ws_connect(url, heartbeat=15) as ws:
# #                         logger.info("🔌 WS Connected (Fast-Path Enabled)")
                        
# #                         async for msg in ws:
# #                             if msg.type == aiohttp.WSMsgType.TEXT:
# #                                 try:
# #                                     # 1. Parse High-Speed Stream
# #                                     data = orjson.loads(msg.data)  # pylint: disable=no-member
                                    
# #                                     # 2. Build Fast Batch
# #                                     fast_batch_shm = {}
# #                                     fast_batch_redis = {}
                                    
# #                                     for p in data:
# #                                         s = p['s']
# #                                         if s in self.symbols:
# #                                             price = float(p['p'])
# #                                             # Use EVENT TIME (E) to detect true market age
# #                                             event_ts = p['E'] / 1000.0
                                            
# #                                             # Update Internal Memory (Primary Source)
# #                                             self.market.resolve_hot_price(s, price, event_ts)

                                            
# #                                             # Prepare Fast Payload
# #                                             payload = {
# #                                                 'current_price': price,
# #                                                 'price': price, 
# #                                                 'mark_price': price, 
# #                                                 '_tick_ts': event_ts 
# #                                             }
                                            
# #                                             fast_batch_shm[s] = payload
# #                                             fast_batch_redis[s] = price

# #                                     # 3. PUSH UPDATES
# #                                     if self.shared_proxy and fast_batch_shm and self.shm_lock:
# #                                         try:
# #                                             # Proxy calls block the event loop! Use to_thread
# #                                             async with self.shm_lock:
# #                                                 await asyncio.to_thread(self.shared_proxy.update_batch, fast_batch_shm)
# #                                         except EOFError:
# #                                             logger.error("🧨 Shared memory batch EOF. Disabling shared memory.")
# #                                             self.shared_proxy = None
# #                                         except Exception as e:
# #                                             logger.error(f"⚠️ Shared memory batch error: {e}")
# #                                             self.shared_proxy = None

# #                                 except Exception as e: 
# #                                     logger.error(f"WS Parse error: {e}")
# #                             elif msg.type == aiohttp.WSMsgType.ERROR:
# #                                 logger.error("WS Error")
# #                                 break
# #             except Exception as e:
# #                 logger.error(f"WS Connection failed: {e}")
# #                 await asyncio.sleep(5)

# #     async def resolve_hot_price(self, sym: str, min_ts: float):
# #         """
# #         Fetches best available price from Memory -> Redis -> Disk.
# #         Stops and returns IMMEDIATELY if a source provides data < 3s old.
# #         """
# #         now = time.time()
# #         best_p = None
# #         best_ts = min_ts or 0.0

# #         # Inline robust timestamp parser
# #         def parse_ts(raw):
# #             if isinstance(raw, (int, float)): return float(raw)
# #             if isinstance(raw, str):
# #                 try: return isoparse(raw).timestamp()
# #                 except: pass
# #             return 0.0

# #         # 1. Local Memory (WS Stream) - Fastest & Freshest
# #         store = self.market.data.get(sym)
# #         if store:
# #             ts = store.get('last_tick_ts', 0)
# #             if ts > best_ts:
# #                 best_p = store['last_price']
# #                 best_ts = ts
# #             # If local is fresh (< 3s), we are done.
# #             if (now - ts) < 3.0:
# #                 return best_p, best_ts

# #         # 2. Redis mark_price (Shared Cache)
# #         try:
# #             raw = await self.redis.get(f"mark_price:{sym}")
# #             if raw:
# #                 obj = orjson.loads(raw)  # pylint: disable=no-member
# #                 p = float(obj.get('price', 0) or obj.get('last', 0))
# #                 ts = parse_ts(obj.get('timestamp'))

# #                 if p > 0 and ts > best_ts:
# #                     best_p = p
# #                     best_ts = ts
                
# #                 # If Redis is fresh (< 3s), we are done.
# #                 if (now - ts) < 3.0:
# #                     return best_p, best_ts
# #         except Exception: pass

# #         # 3. Disk Cache (Fallback)
# #         # Only scan if we still don't have fresh data
# #         for i in range(1, 4):
# #             try:
# #                 cache_path = config.BASE_PATH / f"price_cache_{i}.json"
# #                 if not cache_path.exists(): continue
                
# #                 # Optimization: Don't read file if it hasn't been touched in 3s
# #                 if (now - cache_path.stat().st_mtime) > 3.0: continue

# #                 async with aiofiles.open(cache_path, 'rb') as f:
# #                     content = await f.read()
# #                     data = orjson.loads(content)  # pylint: disable=no-member
# #                     entry = data.get(sym)
                    
# #                     if entry:
# #                         p = float(entry.get('price', 0))
# #                         ts = parse_ts(entry.get('timestamp'))
                        
# #                         if p > 0 and ts > best_ts:
# #                             best_p = p
# #                             best_ts = ts
                        
# #                         # If Disk is fresh (< 3s), we are done.
# #                         if (now - ts) < 3.0:
# #                             return best_p, best_ts
# #             except: pass

# #         return best_p, best_ts


# #     async def _fetch_reliable_price(self, clean_sym, current_stale_ts):
# #         """
# #         Finds the NEWEST price available across all sources (Shared -> Redis -> Disk).
# #         Returns (price, ts) if a newer one is found, else (0, 0).
# #         """
# #         best_p = 0.0
# #         # Start with the stale timestamp we currently have. 
# #         # We only want data NEWER than this.
# #         best_ts = current_stale_ts 
# #         found_better = False
# #         now = time.time()

# #         # 1. SHARED MEMORY
# #         if self.shared_proxy:
# #             try:
# #                 raw = self.shared_proxy.get_symbol(clean_sym)
# #                 if raw:
# #                     ts = float(raw.get('_tick_ts', 0))
# #                     # If this source is newer than what we have, take it
# #                     if ts > best_ts:
# #                         p = float(raw.get('price', 0) or raw.get('mark_price', 0) or raw.get('current_price', 0))
# #                         if p > 0:
# #                             best_p = p
# #                             best_ts = ts
# #                             found_better = True
# #             except: pass
# #         await self._redis_fallback_poll()
# #         # 2. REDIS        
# #         if not self.redis:
# #             rm = await get_simple_redis_manager()
# #             self.redis = rm.connections.get("local")
# #         if self.redis_manager:
# #             try:
# #                 client = self.redis_manager.connections.get("local")
# #                 if client:
# #                     raw = await self.redis.get(f"mark_price:{clean_sym}")
# #                     if raw:
# #                         d = orjson.loads(raw)  # pylint: disable=no-member
# #                         p_val = float(d['price'])
# #                         ts_val = z_to_ts(d['timestamp'])
# #                     if p_val:
# #                         r_ts = float(ts_val) if ts_val else 0
# #                         if r_ts > best_ts:
# #                             best_p = float(p_val)
# #                             best_ts = r_ts
# #                             found_better = True
# #             except: pass

# #         # 3. DISK CACHE (Only if desperate)
# #         # We only check disk if we haven't found anything fresh in RAM/Redis
# #         if not found_better:
# #             for i in range(1, 4):
# #                 try:
# #                     cache_path = config.BASE_PATH / f"price_cache_{i}.json"
# #                     if not cache_path.exists(): continue
                    
# #                     stat = cache_path.stat()
# #                     file_ts = stat.st_mtime
                    
# #                     if file_ts > best_ts:
# #                         async with aiofiles.open(cache_path, 'rb') as f:
# #                             content = await f.read()
# #                             data = orjson.loads(content)  # pylint: disable=no-member
# #                             entry = data.get(clean_sym)
# #                             if entry:
# #                                 p = float(entry.get('p') if isinstance(entry, dict) else entry)
# #                                 if p > 0:
# #                                     best_p = p
# #                                     best_ts = file_ts
# #                                     found_better = True
# #                                     break 
# #                 except: pass

# #         if found_better:
# #             return best_p, best_ts
# #         return 0.0, 0.0

# #     async def run_calc(self, loop, c1, c3, p, t):
# #         return await loop.run_in_executor(self.executor, cpu_bound_calculation, c1, c3, p, t)

# #     async def broadcast_loop(self):
# #         logger.info("📡 Starting Calculation Loop (Real-Time Injection)...")
# #         loop = asyncio.get_running_loop()

# #         while self.running:
# #             # Snapshot keys to allow safe iteration
# #             active_symbols = list(self.market.data.keys())
# #             tasks_data = []
            
# #             # Capture loop start time to measure data freshness against 'now'
# #             loop_start = time.time()

# #             for sym in active_symbols:
# #                 store = self.market.data.get(sym)
# #                 if not store: continue

# #                 # --- 1. FRESHNESS CHECK & INJECTION ---
# #                 # Check the timestamp of the data currently in memory
# #                 current_ts = store.get('last_tick_ts', 0)
                
# #                 # If local data is stale (>1.0s), try to fetch fresher data from Redis/Disk
# #                 if (loop_start - current_ts) > 1.0:
# #                     # Async lookup (Fast: Memory -> Redis -> Disk)
# #                     fresh_p, fresh_ts = await self.resolve_hot_price(sym, current_ts)
                    
# #                     # If we found newer data, INJECT IT IMMEDIATELY
# #                     # This calls update_1m_candle / update_3m_candle
# #                     if fresh_p and fresh_ts > current_ts:
# #                         self.market.resolve_hot_price(sym, fresh_p, fresh_ts)
                
# #                 # --- 2. GENERATE SNAPSHOT ---
# #                 # Now get the snapshot. Because we just called resolve_hot_price above,
# #                 # c1 and c3 contain the very latest tick.
# #                 c1, c3, price, ts = self.market.get_snapshot(sym)
                
# #                 # --- 3. VALIDATION & GATEKEEPING ---
# #                 if not c1 or len(c1) < 20 or price <= 0: continue
                
# #                 # Only calculate if data changed or heartbeat expired (1.5s)
# #                 # 'last_calc_ts' helps us avoid recalculating the exact same candle state
# #                 if not should_recalculate(store, loop_start): 
# #                     continue

# #                 tasks_data.append((sym, c1, c3, price, ts))

# #             if not tasks_data:
# #                 await asyncio.sleep(0.01)
# #                 continue

# #             # --- 4. EXECUTION & IMMEDIATE PUBLISH ---
# #             # Process in chunks to manage CPU load
# #             chunk_size = 50
            
# #             for i in range(0, len(tasks_data), chunk_size):
# #                 chunk = tasks_data[i:i+chunk_size]
# #                 futures = []
# #                 results = []

# #                 # Schedule CPU tasks
# #                 for item in chunk:
# #                     sym, c1, c3, p, t = item
# #                     fut = asyncio.create_task(self.run_calc(loop, c1, c3, p, t))
# #                     futures.append((sym, fut, t))
                
# #                 # Gather Results
# #                 for sym, fut, used_ts in futures:
# #                     try:
# #                         res = await fut
# #                         if res:
# #                             # Tag result with the exact tick time used
# #                             res['_tick_ts'] = used_ts
# #                             res['timestamp'] = used_ts
                            
# #                             # Update calc timestamp in store to prevent re-calc
# #                             store = self.market.data.get(sym)
# #                             if store: store['last_calc_ts'] = used_ts
                            
# #                             results.append((sym, res))
# #                     except: pass

# #                 # PUBLISH CHUNK IMMEDIATELY
# #                 # We do not wait for the outer loop to finish.
# #                 if results:
# #                     async with self.redis.pipeline() as pipe:
# #                         for sym, payload in results:
# #                             # 1. Update Shared Memory (Bridge)
# #                             if self.shared_proxy and self.shm_lock:
# #                                 try: 
# #                                     async with self.shm_lock:
# #                                         await asyncio.to_thread(self.shared_proxy.update_symbol, sym, payload)
# #                                 except: 
# #                                     self.shared_proxy = None
# #                             dumped = orjson.dumps(payload)  # pylint: disable=no-member
# #                             pipe.set(f"hot_metrics:{sym}", dumped, ex=60)
# #                         await pipe.execute()
# #             await asyncio.sleep(0.01)

# #     async def main(self):
# #         self.shm_lock = asyncio.Lock()
# #         rm = await get_simple_redis_manager()
# #         self.redis = rm.connections.get("local")
# #         if not self.redis:
# #             logger.error("❌ Could not connect to local Redis via manager!")
          
# #         await self.load_symbols()
# #         try:
# #             await asyncio.gather(
# #                 self.ws_connect(), 
# #                 self.broadcast_loop(),
# #                 self.heartbeat(), self.shared_mem_watchdog()
# #             )
# #         except KeyboardInterrupt:
# #             self.running = False
# #             self.executor.shutdown(wait=False)

# # if __name__ == "__main__":
# #     asyncio.run(MarketDataEngine().main())
    
# #     # import asyncio
# # # import logging
# # # import math
# # # import os
# # # import platform
# # # import time
# # # from collections import OrderedDict
# # # from concurrent.futures import ThreadPoolExecutor
# # # from datetime import datetime, timezone
# # # from logging.handlers import RotatingFileHandler
# # # from pathlib import Path
# # # import aiofiles
# # # import aiohttp
# # # import numpy as np
# # # import orjson
# # # from dateutil.parser import isoparse
# # # from redis.asyncio import Redis

# # # from config import Config
# # # from utils import (get_current_environment, get_simple_redis_manager,
# # #                    orjson_default)

# # # # --- CONFIGURATION ---
# # # config = Config()
# # # BASE_PATH = Path(config.BASE_PATH)
# # # env = get_current_environment()

# # # def _resolve_base_path() -> Path:
# # #     env_base = os.environ.get("BASE_PATH")
# # #     if env_base:
# # #         base = Path(env_base).expanduser()
# # #         candidates = [base]
# # #         if base.name.lower() != "binance":
# # #             candidates.append(base / "binance")
# # #         for candidate in candidates:
# # #             try:
# # #                 if candidate.exists(): return candidate
# # #             except: pass
# # #         return candidates[0]
# # #     system_name = platform.system()
# # #     if system_name == "Darwin": return Path("/Users/niels/Documents/binance")
# # #     if system_name == "Linux": return Path("/home/niels/binance")
# # #     return Path.home() / "Documents" / "binance"

# # # BASE_PATH = _resolve_base_path()
# # # KLINES_DIRS = [
# # #     BASE_PATH / "klines_cache",
# # #     BASE_PATH / "klines_cache_gateway",
# # #     BASE_PATH / "klines_cache_macbook"]

# # # # Fallback cache directories
# # # PRICE_CACHE_JSON_DICTS = [
# # #     BASE_PATH / "price_cache_1",
# # #     BASE_PATH / "price_cache_2",
# # #     BASE_PATH / "price_cache_3"
# # # ]

# # # SYMBOLS_FILES = ["symbols_active.json"]

# # # LOG_FILE = Path.home() / "logs" / "ez_market_data.log"

# # # # --- SHARED MEMORY ---
# # # try:
# # #     from ez_share_ind import get_shared_memory_client
# # # except ImportError:
# # #     get_shared_memory_client = None

# # # # --- LOGGING ---
# # # Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
# # # logging.basicConfig(
# # #     level=logging.INFO,
# # #     format="%(asctime)s [%(levelname)s] %(message)s",
# # #     handlers=[RotatingFileHandler(LOG_FILE, maxBytes=20971520, backupCount=5), logging.StreamHandler()])
# # # logger = logging.getLogger("DataEngine")

# # # def resilient_json_load(path: Path):
# # #     try:
# # #         if not path.exists(): return None
# # #         with open(path, 'rb') as f: raw = f.read()
# # #         if not raw: return None
# # #         try: return orjson.loads(raw)  # pylint: disable=no-member
# # #         except: pass
# # #         text = raw.decode('utf-8', errors='ignore').strip()
# # #         if not text.startswith('['): return None
# # #         last_obj = text.rfind('}')
# # #         if last_obj == -1: return None
# # #         return orjson.loads(text[:last_obj+1] + "]")  # pylint: disable=no-member
# # #     except: return None

# # # def ts_to_z(ts: float) -> str:
# # #     """Epoch seconds → ISO-8601 UTC Z (ms precision)"""
# # #     return (
# # #         datetime
# # #         .fromtimestamp(ts, tz=timezone.utc)
# # #         .isoformat(timespec="milliseconds")
# # #         .replace("+00:00", "Z")  )

# # # def z_to_ts(z: str) -> float:
# # #     """ISO-8601 Z → epoch seconds"""
# # #     return isoparse(z).timestamp()
# # # # --- ez_market_data.py ---

# # # # --- ez_market_data.py ---

# # # def update_1m_candle(store, price, tick_ts):
# # #     """Updates the minute bucket and ensures history is kept."""
# # #     minute_ts = int(tick_ts - (tick_ts % 60))
# # #     store['closes_1m'][minute_ts] = price # This bucket will be overwritten by live price in get_snapshot
# # #     store['last_tick_ts'] = tick_ts
# # #     while len(store['closes_1m']) > 180:
# # #         old_ts, _ = store['closes_1m'].popitem(last=False)

# # #         store['synthetic_1m'].discard(old_ts)

# # # def update_3m_candle(store, price, tick_ts):
# # #     """Updates the 3-minute bucket."""
# # #     tf_ts = tick_ts - (tick_ts % 180)
# # #     store['closes_3m'][tf_ts] = price
# # #     while len(store['closes_3m']) > 180:
# # #         old_ts, _ = store['closes_3m'].popitem(last=False)
# # #         store['synthetic_3m'].discard(old_ts)
# # # # def update_1m_candle(store, price, tick_ts):
# # # #     minute_ts = tick_ts - (tick_ts % 60)
# # # #     store['closes_1m'][minute_ts] = price
# # # #     store['last_tick_ts'] = tick_ts
# # # #     while len(store['closes_1m']) > 180:
# # # #         old_ts, _ = store['closes_1m'].popitem(last=False)
# # # #         store['synthetic_1m'].discard(old_ts)


# # # # def update_3m_candle(store, price, tick_ts):
# # # #     tf_ts = tick_ts - (tick_ts % 180)
# # # #     store['closes_3m'][tf_ts] = price
# # # #     while len(store['closes_3m']) > 180:
# # # #         old_ts, _ = store['closes_3m'].popitem(last=False)
# # # #         store['synthetic_3m'].discard(old_ts)

# # # def interpolate_missing_1m(store):
# # #     c1 = store['closes_1m']
# # #     c3 = store['closes_3m']
    
# # #     # If 1m is missing but we have 3m, seed 1m with 3m
# # #     if not c1 and c3:
# # #         for t, p in c3.items():
# # #             c1[t] = p
# # #             c1[t+60] = p
# # #             c1[t+120] = p
# # #             store['synthetic_1m'].update([t, t+60, t+120])
            
# # #     if len(c1) < 2 or len(c3) < 2:
# # #         return
# # #     timestamps = list(c1.keys())
# # #     for i in range(1, len(timestamps)):
# # #         prev_ts = timestamps[i - 1]
# # #         curr_ts = timestamps[i]
# # #         expected = prev_ts + 60
# # #         if curr_ts == expected:
# # #             continue
# # #         ts = expected
# # #         while ts < curr_ts:
# # #             prev_3m = max((t for t in c3 if t <= ts), default=None)
# # #             next_3m = min((t for t in c3 if t >= ts), default=None)
# # #             if prev_3m and next_3m and prev_3m != next_3m:
# # #                 p0 = c3[prev_3m]
# # #                 p1 = c3[next_3m]
# # #                 alpha = (ts - prev_3m) / (next_3m - prev_3m)
# # #                 price = p0 + alpha * (p1 - p0)
# # #             else:
# # #                 price = c1[prev_ts]
# # #             c1[ts] = price
# # #             store['synthetic_1m'].add(ts)
# # #             ts += 60
# # # def rebuild_3m_from_1m(store):
# # #     c1 = store['closes_1m']
# # #     c3 = store['closes_3m']

# # #     for ts in list(c1.keys()):
# # #         tf_ts = ts - (ts % 180)
# # #         if tf_ts not in c3:
# # #             window = [
# # #                 c1.get(tf_ts),
# # #                 c1.get(tf_ts + 60),
# # #                 c1.get(tf_ts + 120),
# # #             ]
# # #             window = [p for p in window if p is not None]
# # #             if window:
# # #                 c3[tf_ts] = sum(window) / len(window)
# # #                 store['synthetic_3m'].add(tf_ts)



# # # # -----------------------------------------------------------------------------
# # # # 1. PURE NUMPY CALCULATION (Non-Blocking, Allocation Efficient)
# # # # -----------------------------------------------------------------------------
# # # def sma(values, period):
# # #     if len(values) < period:
# # #         return []

# # #     out = []
# # #     for i in range(period - 1, len(values)):
# # #         out.append(sum(values[i - period + 1:i + 1]) / period)
# # #     return out

# # # # def compute_rsi(closes, period=14):
# # # #     if len(closes) < period + 1:
# # # #         return []

# # # #     gains = []
# # # #     losses = []

# # # #     for i in range(1, len(closes)):
# # # #         diff = closes[i] - closes[i - 1]
# # # #         gains.append(max(diff, 0))
# # # #         losses.append(max(-diff, 0))

# # # #     avg_gain = sum(gains[:period]) / period
# # # #     avg_loss = sum(losses[:period]) / period

# # # #     rsi = []

# # # #     for i in range(period, len(gains)):
# # # #         avg_gain = (avg_gain * (period - 1) + gains[i]) / period
# # # #         avg_loss = (avg_loss * (period - 1) + losses[i]) / period

# # # #         if avg_loss == 0:
# # # #             rsi.append(100.0)
# # # #         else:
# # # #             rs = avg_gain / avg_loss
# # # #             rsi.append(100 - (100 / (1 + rs)))
# # # #     return rsi

# # # def stoch_rsi_numpy(closes, period=14, k_window=3, d_window=3):
# # #     n = len(closes)
# # #     if n < period + k_window + d_window + 2: return None
    
# # #     # Copy to ensure we don't mutate shared memory (just in case)
# # #     src = np.array(closes, dtype=np.float64)
# # #     deltas = np.diff(src)
# # #     gains = np.maximum(deltas, 0.0)
# # #     losses = np.maximum(-deltas, 0.0)

# # #     # Fast Wilder's Smoothing
# # #     avg_gain = np.empty(len(gains))
# # #     avg_loss = np.empty(len(losses))
# # #     avg_gain[0] = gains[0] 
# # #     avg_loss[0] = losses[0]
    
# # #     alpha = 1.0 / period
# # #     w = 1.0 - alpha
    
# # #     for i in range(1, len(gains)):
# # #         avg_gain[i] = w * avg_gain[i-1] + alpha * gains[i]
# # #         avg_loss[i] = w * avg_loss[i-1] + alpha * losses[i]

# # #     with np.errstate(divide='ignore', invalid='ignore'):
# # #         rs = avg_gain / avg_loss
# # #         rsi = 100.0 - (100.0 / (1.0 + rs))
    
# # #     # Fix flatlining
# # #     rsi[avg_loss == 0] = 100.0
# # #     rsi = np.nan_to_num(rsi, nan=50.0)

# # #     # Stoch Calc (Valid tail only)
# # #     calc_start = period
# # #     stoch_series = np.zeros(len(rsi))
# # #     for i in range(calc_start, len(rsi)):
# # #         window = rsi[i-period+1 : i+1]
# # #         min_val = np.min(window)
# # #         max_val = np.max(window)
# # #         if max_val == min_val:
# # #             stoch_series[i] = 100.0 if min_val > 50 else 0.0
# # #         else:
# # #             stoch_series[i] = (rsi[i] - min_val) / (max_val - min_val) * 100.0

# # #     # SMA (K & D)
# # #     ones_k = np.ones(k_window) / k_window
# # #     k_line = np.convolve(stoch_series, ones_k, mode='valid')
    
# # #     ones_d = np.ones(d_window) / d_window
# # #     d_line = np.convolve(k_line, ones_d, mode='valid')

# # #     if len(d_line) < 2: return None
# # #     return k_line[-1], d_line[-1], k_line[-2], d_line[-2]

# # # def cpu_bound_calculation(closes_1m, closes_3m, last_price, last_tick_ts):
# # #     try:
        
# # #         res1 = stoch_rsi_numpy(closes_1m)
# # #         if res1 is None: return None
# # #         k1, d1, k1p, d1p = res1
        
# # #         # 3m Calc
# # #         res3 = stoch_rsi_numpy(closes_3m)
# # #         if res3: 
# # #             k3, d3, k3p, d3p = res3
# # #         else: 
# # #             k3, d3, k3p, d3p = 50.0, 50.0, 50.0, 50.0

# # #         return {
# # #             'price': last_price,
# # #             '_tick_ts': last_tick_ts,
# # #             'timestamp': ts_to_z(last_tick_ts),
# # #             'ts1': ts_to_z(last_tick_ts),

# # #             'valid': True,

# # #             'k_1m': round(float(k1), 2),
# # #             'd_1m': round(float(d1), 2),
# # #             'k_1m_prev': round(float(k1p), 2),
# # #             'd_1m_prev': round(float(d1p), 2),

# # #             'k_3m': round(float(k3), 2),
# # #             'd_3m': round(float(d3), 2),
# # #             'k_3m_prev': round(float(k3p), 2),
# # #             'd_3m_prev': round(float(d3p), 2),
# # #         }

# # #     except Exception:
# # #         return None



# # # class KlineLoader:
# # #     @staticmethod
# # #     def load_history(symbol: str, interval: str) -> dict:
# # #         filenames = [f"{symbol}_{interval}.json", f"{symbol.lower()}_{interval}.json"]
# # #         for cache_dir in KLINES_DIRS:
# # #             base_dir = Path(cache_dir).expanduser().resolve()
# # #             for fname in filenames:
# # #                 path = base_dir / fname
# # #                 data = resilient_json_load(path)
# # #                 if not data or not isinstance(data, list): continue
                
# # #                 history_map = {}
# # #                 # Optimized: We only need last ~80 for calculation
# # #                 for item in data[-100:]: 
# # #                     try:
# # #                         ts = 0.0
# # #                         close = 0.0
# # #                         if isinstance(item, dict):
# # #                             raw_ts = item.get('timestamp') or item.get('time')
# # #                             close = float(item.get('close', 0))
# # #                             if isinstance(raw_ts, str):
# # #                                 if raw_ts.endswith('Z'): raw_ts = raw_ts.replace('Z', '+00:00')
# # #                                 ts = isoparse(raw_ts).timestamp()
# # #                             else: ts = float(raw_ts)
# # #                         elif isinstance(item, list) and len(item) >= 5:
# # #                             ts = float(item[0])
# # #                             close = float(item[4])
# # #                         if ts > 1e11: ts /= 1000.0
# # #                         if ts > 0 and close > 0: history_map[int(ts)] = close
# # #                     except: continue
# # #                 if history_map: return history_map
# # #         return {}

# # # def init_empty_store():
# # #     return {
# # #         'closes_1m': OrderedDict(),   # ts -> close
# # #         'closes_3m': OrderedDict(),   # ts -> close
# # #         'synthetic_1m': set(),
# # #         'synthetic_3m': set(),
# # #         'last_tick_ts': None,
# # #         'last_calc_ts': 0.0, }

# # # def should_recalculate(store, now):
# # #     last_used_ts = store.get('last_calc_ts', 0)
# # #     last_tick_ts = store.get('last_tick_ts', 0)
# # #     if last_tick_ts > last_used_ts:
# # #         return True
# # #     if (now - last_used_ts) >= 10.0:
# # #         return True
# # #     return False


# # # # -----------------------------------------------------------------------------
# # # # 2. MARKET DATA STORE (Fast Updates, Zero Locking)
# # # # -----------------------------------------------------------------------------

# # # class DummyLock:
# # #     async def __aenter__(self):
# # #         pass
# # #     async def __aexit__(self, exc_type, exc_val, exc_tb):
# # #         pass


# # # class HighResMarketData:
    
# # #     def __init__(self):
# # #         self.data = {} 

# # #     def resolve_hot_price(self, symbol, price, tick_ts):
# # #         """Websocket Injector: Updates internal memory only."""
# # #         if symbol not in self.data: return
# # #         store = self.data[symbol]
        
# # #         # 1. Update the 'Live' Price
# # #         store['last_price'] = price
# # #         store['last_tick_ts'] = tick_ts

# # #         # 2. Update the rolling buckets
# # #         update_1m_candle(store, price, tick_ts)
# # #         update_3m_candle(store, price, tick_ts)

# # #     def get_snapshot(self, symbol: str):
# # #         """Prepares lists where the last item is ALWAYS the latest mark price."""
# # #         store = self.data.get(symbol)
# # #         if not store or len(store['closes_1m']) < 40: 
# # #             return None, None, 0, 0
        
# # #         lp = store['last_price']
        
# # #         # Convert OrderedDicts to lists for Numpy
# # #         c1 = list(store['closes_1m'].values())
# # #         c3 = list(store['closes_3m'].values())

# # #         # CRITICAL: Replace the last bucket's close with the LATEST live mark price
# # #         # This makes the Stoch RSI react to every single tick.
# # #         c1[-1] = lp
# # #         c3[-1] = lp

# # #         return c1, c3, lp, store['last_tick_ts']


# # # # class HighResMarketData:
# # # #     def __init__(self):
# # # #         self.data = {} 


# # # #     def init_symbol_from_disk(self, symbol: str):
# # # #         if symbol in self.data:
# # # #             return

# # # #         hist_1m_map = KlineLoader.load_history(symbol, '1m')  # ts -> close
# # # #         hist_3m_map = KlineLoader.load_history(symbol, '3m')  # ts -> close

# # # #         closes_1m = OrderedDict()
# # # #         closes_3m = OrderedDict()

# # # #         # load 1m with timestamps
# # # #         for ts in sorted(hist_1m_map.keys()):
# # # #             closes_1m[int(ts)] = float(hist_1m_map[ts])

# # # #         # load 3m with timestamps
# # # #         for ts in sorted(hist_3m_map.keys()):
# # # #             closes_3m[int(ts)] = float(hist_3m_map[ts])

# # # #         # keep last ~1800 (you said they always exist)
# # # #         while len(closes_1m) > 1800:
# # # #             closes_1m.popitem(last=False)

# # # #         while len(closes_3m) > 1800:
# # # #             closes_3m.popitem(last=False)

# # # #         store = {
# # # #             'closes_1m': closes_1m,
# # # #             'closes_3m': closes_3m,
# # # #             'synthetic_1m': set(),
# # # #             'synthetic_3m': set(),
# # # #             'last_price': list(closes_1m.values())[-1] if closes_1m else 0.0,
# # # #             'last_tick_ts': time.time(),
# # # #             'last_calc_ts': 0.0,
# # # #         }

# # # #         # 🔥 FIX continuity problems at startup
# # # #         interpolate_missing_1m(store)
# # # #         rebuild_3m_from_1m(store)

# # # #         self.data[symbol] = store

# # # #         logger.info(
# # # #             f"✅ Loaded {symbol}: "
# # # #             f"{len(store['closes_1m'])}×1m / {len(store['closes_3m'])}×3m "
# # # #             f"P:{store['last_price']} " )

# # # #     def resolve_hot_price(self, symbol, price, tick_ts):
# # # #         store = self.data.get(symbol)
# # # #         if not store:
# # # #             return

# # # #         # 🔥 THIS is where rolling candles are built
# # # #         update_1m_candle(store, price, tick_ts)
# # # #         update_3m_candle(store, price, tick_ts)

# # # #         store['last_price'] = price

# # #     # def update(self, symbol: str, price: float, timestamp: float):
# # #     #     store = self.data.get(symbol)
# # #     #     if not store: return

# # #     #     ts = int(timestamp)
# # #     #     self.resolve_hot_price(self, symbol, price, timestamp)
# # #     #     store['closes_1m'] = OrderedDict()  # ts -> close
# # #     #     store['closes_3m'] = OrderedDict()

# # #     #     # --- 1 Minute Rollover ---
# # #     #     # "REPLACE the last kline logic":
# # #     #     # If we cross the boundary, the last known price IS the close of the previous minute.
# # #     #     bucket_1m = ts // 60
# # #     #     if bucket_1m > store['last_bucket_1m']:
# # #     #         store['closes_1m'].append(price) # Use current price as close of prev (approximation, but fast)
# # #     #         if len(store['closes_1m']) > 80: store['closes_1m'].pop(0)
# # #     #         store['last_bucket_1m'] = bucket_1m

# # #     #     # --- 3 Minute Rollover ---
# # #     #     bucket_3m = ts // 180
# # #     #     if bucket_3m > store['last_bucket_3m']:
# # #     #         store['closes_3m'].append(price)
# # #     #         if len(store['closes_3m']) > 40: store['closes_3m'].pop(0)
# # #     #         store['last_bucket_3m'] = bucket_3m

# # #     # def get_snapshot(self, symbol: str):
# # #     #     store = self.data.get(symbol)
# # #     #     if not store or len(store['closes_1m']) < 30: 
# # #     #         return None, None, 0, 0
# # #     #     lp = store['last_price']
# # #     #     c1 = list(store['closes_1m'].values())
# # #     #     c3 = list(store['closes_3m'].values())

# # #     #     return c1, c3, lp, store['last_tick_ts']

# # #     #     return c1, c3, store['last_price'], store['last_tick_ts']

# # # # -----------------------------------------------------------------------------
# # # # 3. ENGINE (Async, Threaded Fallback, Non-Blocking)
# # # # -----------------------------------------------------------------------------
# # # class MarketDataEngine:
# # #     def __init__(self):
# # #         self.redis = None
# # #         self.market = HighResMarketData()
# # #         self.symbols = set()
# # #         self.symbols_list = [] # For indexing pipeline results
# # #         self.running = True
# # #         self.shared_proxy = None  
# # #         self.shm_lock = DummyLock()      
# # #         self.executor = ThreadPoolExecutor(max_workers=8) 
# # #         self.price_buffer = {} # THE BRAIN: { 'BTCUSDT': {'p': 50.0, 't': 1700.0} }

# # #         self._connect_shared()

# # #     def _connect_shared(self):
# # #         if get_shared_memory_client:
# # #             try:
# # #                 mgr = get_shared_memory_client()
# # #                 if mgr:
# # #                     # Check if 'get_store' exists, fallback to 'get_dict' or others
# # #                     if hasattr(mgr, 'get_store'):
# # #                         self.shared_proxy = mgr.get_store()
# # #                     elif hasattr(mgr, 'get_dict'):
# # #                         self.shared_proxy = mgr.get_dict()
# # #                     else:
# # #                         # If the manager itself is the proxy-like object
# # #                         self.shared_proxy = mgr 
                    
# # #                     logger.info(f"✅ Shared Memory Proxy Linked: {type(self.shared_proxy)}")
# # #             except Exception as e:
# # #                 logger.error(f"❌ Shared Memory Connection Failed: {e}")
# # #                 self.shared_proxy = None

# # #     async def shared_mem_watchdog(self):
# # #         while self.running:
# # #             if not self.shared_proxy:
# # #                 try:
# # #                     self._connect_shared()
# # #                     if self.shared_proxy:
# # #                         logger.info("🔁 Shared memory reconnected")
# # #                 except:
# # #                     pass
# # #             await asyncio.sleep(5)

# # #     async def ws_injector(self):
# # #         url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
# # #         async with aiohttp.ClientSession() as session:
# # #             async with session.ws_connect(url) as ws:
# # #                 async for msg in ws:
# # #                     data = orjson.loads(msg.data)
# # #                     for p in data:
# # #                         sym = p['s']
# # #                         if sym in self.symbols:
# # #                             self.price_buffer[sym] = {
# # #                                 'p': float(p['p']),
# # #                                 't': p['E'] / 1000.0  }

# # #     # ---------------------------------------------------------
# # #     # SOURCE 2: REDIS POLL (Every 1.5s - Secondary Source)
# # #     # ---------------------------------------------------------
# # #     async def redis_injector(self):
# # #         while self.running:
# # #             try:
# # #                 # Get all mark prices in one shot (MGET is better, but keys are dynamic)
# # #                 # We iterate our symbol list and grab from redis
# # #                 async with self.redis.pipeline() as pipe:
# # #                     for s in self.symbols:
# # #                         pipe.get(f"mark_price:{s}")
# # #                     results = await pipe.execute()
                
# # #                 for i, raw in enumerate(results):
# # #                     if raw:
# # #                         obj = orjson.loads(raw)
# # #                         sym = self.symbols_list[i] # need list for index
# # #                         ts = z_to_ts(obj['timestamp'])
# # #                         # ONLY update buffer if Redis data is NEWER than what's in RAM
# # #                         if ts > self.price_buffer.get(sym, {}).get('t', 0):
# # #                             self.price_buffer[sym] = {'p': float(obj['price']), 't': ts}
# # #             except: pass
# # #             await asyncio.sleep(1.5)

# # #     # ---------------------------------------------------------
# # #     # SOURCE 3: JSON CACHE POLL (Every 2s - Fail-safe)
# # #     # ---------------------------------------------------------
# # #     async def json_injector(self):
# # #         while self.running:
# # #             for i in range(1, 4):
# # #                 try:
# # #                     path = BASE_PATH / f"price_cache_{i}.json"
# # #                     if not path.exists(): continue
# # #                     async with aiofiles.open(path, 'rb') as f:
# # #                         data = orjson.loads(await f.read())
# # #                         for sym, entry in data.items():
# # #                             if sym in self.symbols:
# # #                                 ts = z_to_ts(entry['timestamp'])
# # #                                 if ts > self.price_buffer.get(sym, {}).get('t', 0):
# # #                                     self.price_buffer[sym] = {'p': float(entry['price']), 't': ts}
# # #                 except: pass
# # #             await asyncio.sleep(2.0)



# # #     def update_ohlc_from_buffer(self, symbol, price, ts):
# # #         store = self.data[symbol]
# # #         m_ts = int(ts - (ts % 60))
# # #         store['closes_1m'][m_ts] = price
# # #         t_ts = int(ts - (ts % 180))
# # #         store['closes_3m'][t_ts] = price
# # #         store['last_price'] = price
# # #         store['last_tick_ts'] = ts

# # #         while len(store['closes_1m']) > 200: store['closes_1m'].popitem(last=False)
# # #         while len(store['closes_3m']) > 200: store['closes_3m'].popitem(last=False)

# # #     def get_snapshot(self, symbol):
# # #         store = self.data[symbol]
# # #         if len(store['closes_1m']) < 40: return None, None, 0, 0
        
# # #         c1 = list(store['closes_1m'].values())
# # #         c3 = list(store['closes_3m'].values())
        
# # #         # LIVE SLOT REPLACEMENT:
# # #         # Replace the last element (the open candle) with the most recent buffered price
# # #         c1[-1] = store['last_price']
# # #         c3[-1] = store['last_price']
        
# # #         return c1, c3, store['last_price'], store['last_tick_ts']

# # #     async def load_symbols(self):
# # #         logger.info("📚 Loading symbols...")
# # #         universe = set()
# # #         for fname in SYMBOLS_FILES:
# # #             f = BASE_PATH / fname
# # #             if f.exists():
# # #                 d = resilient_json_load(f)
# # #                 if isinstance(d, list): 
# # #                     for x in d: universe.add(x.split(':')[-1].upper() if ':' in x else x.upper())
# # #                 elif isinstance(d, dict):
# # #                     for x in d.get('symbols', []): universe.add(str(x).upper())
# # #         self.symbols = {s for s in universe if "USDT" in s or "USDC" in s}
        
# # #         logger.info(f"⚡ Initializing buffers for {len(self.symbols)} symbols...")
# # #         for s in self.symbols: self.market.init_symbol_from_disk(s)
# # #         logger.info(f"✅ Ready.")

# # #     async def _redis_fallback_poll(self):
# # #         """
# # #         Polls Redis for symbols that haven't received WS updates in > 4s.
# # #         Uses robust timestamp parsing.
# # #         """
# # #         while self.running:
# # #             try:
# # #                 now = time.time()
# # #                 # Identify stale symbols (no tick in 4s)
# # #                 stale_symbols = [
# # #                     sym for sym, data in self.market.data.items()
# # #                     if (now - data.get('last_tick_ts', 0)) > 4.0
# # #                 ]

# # #                 if stale_symbols:
# # #                     for s in stale_symbols:
# # #                         try:
# # #                             raw = await self.redis.get(f"mark_price:{s}")
# # #                             if raw:
# # #                                 obj = orjson.loads(raw)  # pylint: disable=no-member
# # #                                 p = float(obj.get('price', 0))
                                
# # #                                 # Robust Parsing
# # #                                 ts_raw = obj.get('timestamp')
# # #                                 ts = 0.0
# # #                                 if isinstance(ts_raw, (int, float)):
# # #                                     ts = float(ts_raw)
# # #                                 elif isinstance(ts_raw, str):
# # #                                     try: ts = isoparse(ts_raw).timestamp()
# # #                                     except: pass

# # #                                 if p > 0 and ts > 0:
# # #                                     # Update local market store
# # #                                     self.market.resolve_hot_price(s, p, ts)
# # #                         except Exception: pass

# # #                     # If still very stale (>15s), try disk fallback via thread pool
# # #                     very_stale = [s for s in stale_symbols if (now - self.market.data[s]['last_tick_ts']) > 15.0]
# # #                     # (Existing logic for _sync_read_file_fallback would go here if needed, 
# # #                     # but usually Redis is sufficient for fallback)

# # #             except Exception as e:
# # #                 logger.error(f"Fallback error: {e}")
            
# # #             await asyncio.sleep(0.5)

# # #     async def heartbeat(self):
# # #         while self.running:
# # #             logger.info(f"💓 Alive | Symbols: {len(self.market.data)}")
# # #             await asyncio.sleep(30)

# # #     # async def ws_connect(self): 
# # #     #     """The only price source allowed during runtime."""
# # #     #     url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
# # #     #     while self.running:
# # #     #         try:
# # #     #             async with aiohttp.ClientSession() as session:
# # #     #                 async with session.ws_connect(url, heartbeat=15) as ws:
# # #     #                     logger.info("🔌 WS Connected: Receiving all prices...")
# # #     #                     async for msg in ws:
# # #     #                         if msg.type == aiohttp.WSMsgType.TEXT:
# # #     #                             data = orjson.loads(msg.data)
# # #     #                             for p in data:
# # #     #                                 s = p['s']
# # #     #                                 if s in self.symbols:
# # #     #                                     # INJECT into internal memory immediately
# # #     #                                     self.market.resolve_hot_price(s, float(p['p']), p['E'] / 1000.0)
# # #     #         except Exception as e:
# # #     #             logger.error(f"WS Error: {e}")
# # #     #             await asyncio.sleep(5)
# # #     async def ws_connect(self): 
# # #         url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"
# # #         while self.running:
# # #             try:
# # #                 # Keep session alive
# # #                 session_timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=10)
# # #                 async with aiohttp.ClientSession(timeout=session_timeout) as session:
# # #                     async with session.ws_connect(url, heartbeat=15) as ws:
# # #                         logger.info("🔌 WS Connected (Fast-Path Enabled)")
                        
# # #                         async for msg in ws:
# # #                             if msg.type == aiohttp.WSMsgType.TEXT:
# # #                                 try:
# # #                                     # 1. Parse High-Speed Stream
# # #                                     data = orjson.loads(msg.data)  # pylint: disable=no-member
                                    
# # #                                     # 2. Build Fast Batch
# # #                                     fast_batch_shm = {}
# # #                                     fast_batch_redis = {}
                                    
# # #                                     for p in data:
# # #                                         s = p['s']
# # #                                         if s in self.symbols:
# # #                                             price = float(p['p'])
# # #                                             # Use EVENT TIME (E) to detect true market age
# # #                                             event_ts = p['E'] / 1000.0
                                            
# # #                                             # Update Internal Memory (Primary Source)
# # #                                             self.market.resolve_hot_price(s, price, event_ts)

                                            
# # #                                             # Prepare Fast Payload
# # #                                             payload = {
# # #                                                 'current_price': price,
# # #                                                 'price': price, 
# # #                                                 'mark_price': price, 
# # #                                                 '_tick_ts': event_ts 
# # #                                             }
                                            
# # #                                             fast_batch_shm[s] = payload
# # #                                             fast_batch_redis[s] = price

# # #                                     # 3. PUSH UPDATES
# # #                                     if self.shared_proxy and fast_batch_shm and self.shm_lock:
# # #                                         try:
# # #                                             # Proxy calls block the event loop! Use to_thread
# # #                                             async with self.shm_lock:
# # #                                                 await asyncio.to_thread(self.shared_proxy.update_batch, fast_batch_shm)
# # #                                         except EOFError:
# # #                                             logger.error("🧨 Shared memory batch EOF. Disabling shared memory.")
# # #                                             self.shared_proxy = None
# # #                                         except Exception as e:
# # #                                             logger.error(f"⚠️ Shared memory batch error: {e}")
# # #                                             self.shared_proxy = None

# # #                                 except Exception as e: 
# # #                                     logger.error(f"WS Parse error: {e}")
# # #                             elif msg.type == aiohttp.WSMsgType.ERROR:
# # #                                 logger.error("WS Error")
# # #                                 break
# # #             except Exception as e:
# # #                 logger.error(f"WS Connection failed: {e}")
# # #                 await asyncio.sleep(5)

# # #     async def resolve_hot_price(self, sym: str, min_ts: float):
# # #         """
# # #         Fetches best available price from Memory -> Redis -> Disk.
# # #         Stops and returns IMMEDIATELY if a source provides data < 3s old.
# # #         """
# # #         now = time.time()
# # #         best_p = None
# # #         best_ts = min_ts or 0.0

# # #         # Inline robust timestamp parser
# # #         def parse_ts(raw):
# # #             if isinstance(raw, (int, float)): return float(raw)
# # #             if isinstance(raw, str):
# # #                 try: return isoparse(raw).timestamp()
# # #                 except: pass
# # #             return 0.0

# # #         # 1. Local Memory (WS Stream) - Fastest & Freshest
# # #         store = self.market.data.get(sym)
# # #         if store:
# # #             ts = store.get('last_tick_ts', 0)
# # #             if ts > best_ts:
# # #                 best_p = store['last_price']
# # #                 best_ts = ts
# # #             # If local is fresh (< 3s), we are done.
# # #             if (now - ts) < 3.0:
# # #                 return best_p, best_ts

# # #         # 2. Redis mark_price (Shared Cache)
# # #         try:
# # #             raw = await self.redis.get(f"mark_price:{sym}")
# # #             if raw:
# # #                 obj = orjson.loads(raw)  # pylint: disable=no-member
# # #                 p = float(obj.get('price', 0) or obj.get('last', 0))
# # #                 ts = parse_ts(obj.get('timestamp'))

# # #                 if p > 0 and ts > best_ts:
# # #                     best_p = p
# # #                     best_ts = ts
                
# # #                 # If Redis is fresh (< 3s), we are done.
# # #                 if (now - ts) < 3.0:
# # #                     return best_p, best_ts
# # #         except Exception: pass

# # #         # 3. Disk Cache (Fallback)
# # #         # Only scan if we still don't have fresh data
# # #         for i in range(1, 4):
# # #             try:
# # #                 cache_path = config.BASE_PATH / f"price_cache_{i}.json"
# # #                 if not cache_path.exists(): continue
                
# # #                 # Optimization: Don't read file if it hasn't been touched in 3s
# # #                 if (now - cache_path.stat().st_mtime) > 3.0: continue

# # #                 async with aiofiles.open(cache_path, 'rb') as f:
# # #                     content = await f.read()
# # #                     data = orjson.loads(content)  # pylint: disable=no-member
# # #                     entry = data.get(sym)
                    
# # #                     if entry:
# # #                         p = float(entry.get('price', 0))
# # #                         ts = parse_ts(entry.get('timestamp'))
                        
# # #                         if p > 0 and ts > best_ts:
# # #                             best_p = p
# # #                             best_ts = ts
                        
# # #                         # If Disk is fresh (< 3s), we are done.
# # #                         if (now - ts) < 3.0:
# # #                             return best_p, best_ts
# # #             except: pass

# # #         return best_p, best_ts


# # #     async def _fetch_reliable_price(self, clean_sym, current_stale_ts):
# # #         """
# # #         Finds the NEWEST price available across all sources (Shared -> Redis -> Disk).
# # #         Returns (price, ts) if a newer one is found, else (0, 0).
# # #         """
# # #         best_p = 0.0
# # #         # Start with the stale timestamp we currently have. 
# # #         # We only want data NEWER than this.
# # #         best_ts = current_stale_ts 
# # #         found_better = False
# # #         now = time.time()

# # #         # 1. SHARED MEMORY
# # #         if self.shared_proxy:
# # #             try:
# # #                 raw = self.shared_proxy.get_symbol(clean_sym)
# # #                 if raw:
# # #                     ts = float(raw.get('_tick_ts', 0))
# # #                     # If this source is newer than what we have, take it
# # #                     if ts > best_ts:
# # #                         p = float(raw.get('price', 0) or raw.get('mark_price', 0) or raw.get('current_price', 0))
# # #                         if p > 0:
# # #                             best_p = p
# # #                             best_ts = ts
# # #                             found_better = True
# # #             except: pass
# # #         await self._redis_fallback_poll()
# # #         # 2. REDIS        
# # #         if not self.redis:
# # #             rm = await get_simple_redis_manager()
# # #             self.redis = rm.connections.get("local")
# # #         if self.redis_manager:
# # #             try:
# # #                 client = self.redis_manager.connections.get("local")
# # #                 if client:
# # #                     raw = await self.redis.get(f"mark_price:{clean_sym}")
# # #                     if raw:
# # #                         d = orjson.loads(raw)  # pylint: disable=no-member
# # #                         p_val = float(d['price'])
# # #                         ts_val = z_to_ts(d['timestamp'])
# # #                     if p_val:
# # #                         r_ts = float(ts_val) if ts_val else 0
# # #                         if r_ts > best_ts:
# # #                             best_p = float(p_val)
# # #                             best_ts = r_ts
# # #                             found_better = True
# # #             except: pass

# # #         # 3. DISK CACHE (Only if desperate)
# # #         # We only check disk if we haven't found anything fresh in RAM/Redis
# # #         if not found_better:
# # #             for i in range(1, 4):
# # #                 try:
# # #                     cache_path = config.BASE_PATH / f"price_cache_{i}.json"
# # #                     if not cache_path.exists(): continue
                    
# # #                     stat = cache_path.stat()
# # #                     file_ts = stat.st_mtime
                    
# # #                     if file_ts > best_ts:
# # #                         async with aiofiles.open(cache_path, 'rb') as f:
# # #                             content = await f.read()
# # #                             data = orjson.loads(content)  # pylint: disable=no-member
# # #                             entry = data.get(clean_sym)
# # #                             if entry:
# # #                                 p = float(entry.get('p') if isinstance(entry, dict) else entry)
# # #                                 if p > 0:
# # #                                     best_p = p
# # #                                     best_ts = file_ts
# # #                                     found_better = True
# # #                                     break 
# # #                 except: pass

# # #         if found_better:
# # #             return best_p, best_ts
# # #         return 0.0, 0.0

# # #     async def run_calc(self, loop, c1, c3, p, t):
# # #         return await loop.run_in_executor(self.executor, cpu_bound_calculation, c1, c3, p, t)


# # #     # async def broadcast_loop(self):
# # #     #     logger.info("📡 Starting Calculation Loop...")
# # #     #     loop = asyncio.get_running_loop()

# # #     #     while self.running:
# # #     #         # 1. Gather all symbols that have fresh buffered prices
# # #     #         active_symbols = list(self.market.data.keys())
# # #     #         tasks_to_run = []
# # #     #         loop_start = time.time()

# # #     #         for sym in active_symbols:
# # #     #             buf = self.price_buffer.get(sym)
# # #     #             if not buf: continue
                
# # #     #             if (loop_start - current_ts) > 1.0:
# # #     #                 self.market.resolve_hot_price(sym, buf['p'], buf['t'])
                    
# # #     #             c1, c3, price, ts = self.market.get_snapshot(sym)
                
# # #     #             # --- 3. VALIDATION & GATEKEEPING ---
# # #     #             if not c1 or len(c1) < 20 or price <= 0: continue
# # #     #             if not should_recalculate(store, loop_start): 
# # #     #                 continue                
# # #     #             c1, c3, price, tick_ts = self.market.get_snapshot(sym)
# # #     #             if not c1 or len(c1) < 40: continue

# # #     #             tasks_to_run.append((sym, c1, c3, price, tick_ts))

# # #     #         if not tasks_to_run:
# # #     #             await asyncio.sleep(0.1); continue

# # #     #         # 2. RUN CALCULATIONS (Numpy)
# # #     #         chunk_size = 50
# # #     #         for i in range(0, len(tasks_to_run), chunk_size):
# # #     #             chunk = tasks_to_run[i:i+chunk_size]
# # #     #             results_raw = await asyncio.gather(*futures)
# # #     #             futures = []

# # #     #             results = []
                
# # #     #             # futures = [self.run_calc(loop, c1, c3, p, t) for _, c1, c3, p, t in chunk]
# # #     #             for item in chunk:
# # #     #                 sym, c1, c3, p, t = item
# # #     #                 fut = asyncio.create_task(self.run_calc(loop, c1, c3, p, t))
# # #     #                 futures.append((sym, fut, t))
                
# # #     #             # Gather Results
# # #     #             for sym, fut, used_ts in futures:
# # #     #                 try:
# # #     #                     res = await fut
# # #     #                     if res:
# # #     #                         # Tag result with the exact tick time used
# # #     #                         res['_tick_ts'] = used_ts
# # #     #                         res['timestamp'] = used_ts
                            
# # #     #                         # Update calc timestamp in store to prevent re-calc
# # #     #                         store = self.market.data.get(sym)
# # #     #                         if store: store['last_calc_ts'] = used_ts
                            
# # #     #                         results.append((sym, res))
# # #     #                 except: pass
# # #     #             if results:
# # #     #                 if self.shared_proxy:
# # #     #                     try:
# # #     #                         batch = {s: p for s, p in results}
# # #     #                         asyncio.create_task(asyncio.to_thread(self.shared_proxy.update_batch, batch))
# # #     #                     except Exception: 
# # #     #                         self.shared_proxy = None 
# # #     #                 try:
# # #     #                     async with self.redis.pipeline() as pipe:
# # #     #                         for sym, payload in results:
# # #     #                             pipe.set(f"hot_metrics:{sym}", orjson.dumps(payload), ex=60)
# # #     #                         await pipe.execute()
# # #     #                 except Exception as e: logger.error(f"Redis Error: {e}")

# # #     #                 # 4. TIER 1 UPDATE: BRIDGE (Fire and Forget)
# # #     #                 if self.shared_proxy:
# # #     #                     try:
# # #     #                         batch = {s: p for s, p in results}
# # #     #                         asyncio.create_task(asyncio.to_thread(self.shared_proxy.update_batch, batch))
# # #     #                     except Exception: 
# # #     #                         self.shared_proxy = None # Disable if broken

# # #     #                 async with self.redis.pipeline() as pipe:
# # #     #                     for sym, payload in results:
# # #     #                         # 1. Update Shared Memory (Bridge)
# # #     #                         if self.shared_proxy and self.shm_lock:
# # #     #                             try: 
# # #     #                                 async with self.shm_lock:
# # #     #                                     await asyncio.to_thread(self.shared_proxy.update_symbol, sym, payload)
# # #     #                             except: 
# # #     #                                 self.shared_proxy = None
# # #     #                         dumped = orjson.dumps(payload)  # pylint: disable=no-member
# # #     #                         pipe.set(f"hot_metrics:{sym}", dumped, ex=60)
# # #     #                     await pipe.execute()

# # #     #         await asyncio.sleep(0.01)

# # #     async def broadcast_loop(self):

# # #         logger.info("📡 Starting Calculation Loop (Real-Time Injection)...")
# # #         loop = asyncio.get_running_loop()

# # #         while self.running:
# # #             # Snapshot keys to allow safe iteration
# # #             active_symbols = list(self.market.data.keys())
# # #             tasks_data = []
            
# # #             # Capture loop start time to measure data freshness against 'now'
# # #             loop_start = time.time()

# # #             for sym in active_symbols:
# # #                 store = self.market.data.get(sym)
# # #                 if not store: continue

# # #                 # --- 1. FRESHNESS CHECK & INJECTION ---
# # #                 # Check the timestamp of the data currently in memory
# # #                 current_ts = store.get('last_tick_ts', 0)
                
# # #                 # If local data is stale (>1.0s), try to fetch fresher data from Redis/Disk
# # #                 if (loop_start - current_ts) > 1.0:
# # #                     # Async lookup (Fast: Memory -> Redis -> Disk)
# # #                     fresh_p, fresh_ts = await self.resolve_hot_price(sym, current_ts)
                    
# # #                     # If we found newer data, INJECT IT IMMEDIATELY
# # #                     # This calls update_1m_candle / update_3m_candle
# # #                     if fresh_p and fresh_ts > current_ts:
# # #                         self.market.resolve_hot_price(sym, fresh_p, fresh_ts)
                
# # #                 # --- 2. GENERATE SNAPSHOT ---
# # #                 # Now get the snapshot. Because we just called resolve_hot_price above,
# # #                 # c1 and c3 contain the very latest tick.
# # #                 c1, c3, price, ts = self.market.get_snapshot(sym)
                
# # #                 # --- 3. VALIDATION & GATEKEEPING ---
# # #                 if not c1 or len(c1) < 20 or price <= 0: continue
                
# # #                 # Only calculate if data changed or heartbeat expired (1.5s)
# # #                 # 'last_calc_ts' helps us avoid recalculating the exact same candle state
# # #                 if not should_recalculate(store, loop_start): 
# # #                     continue

# # #                 tasks_data.append((sym, c1, c3, price, ts))

# # #             if not tasks_data:
# # #                 await asyncio.sleep(0.01)
# # #                 continue

# # #             # --- 4. EXECUTION & IMMEDIATE PUBLISH ---
# # #             # Process in chunks to manage CPU load
# # #             chunk_size = 50
            
# # #             for i in range(0, len(tasks_data), chunk_size):
# # #                 chunk = tasks_data[i:i+chunk_size]
# # #                 futures = []
# # #                 results = []

# # #                 # Schedule CPU tasks
# # #                 for item in chunk:
# # #                     sym, c1, c3, p, t = item
# # #                     fut = asyncio.create_task(self.run_calc(loop, c1, c3, p, t))
# # #                     futures.append((sym, fut, t))
                
# # #                 # Gather Results
# # #                 for sym, fut, used_ts in futures:
# # #                     try:
# # #                         res = await fut
# # #                         if res:
# # #                             # Tag result with the exact tick time used
# # #                             res['_tick_ts'] = used_ts
# # #                             res['timestamp'] = used_ts
                            
# # #                             # Update calc timestamp in store to prevent re-calc
# # #                             store = self.market.data.get(sym)
# # #                             if store: store['last_calc_ts'] = used_ts
                            
# # #                             results.append((sym, res))
# # #                     except: pass

# # #             if results:
# # #                 # 1. Update Redis (Pipeline is fast and non-blocking)
# # #                 async with self.redis.pipeline() as pipe:
# # #                     for sym, payload in results:
# # #                         pipe.set(f"hot_metrics:{sym}", orjson.dumps(payload), ex=60)
# # #                     await pipe.execute()

# # #                 # 2. Update Shared Memory in background so it doesn't stall the calc loop
# # #                 if self.shared_proxy:
# # #                     batch_payload = {s: p for s, p in results}
# # #                     # Use a task so we don't wait for the socket IO
# # #                     asyncio.create_task(asyncio.to_thread(self.shared_proxy.update_batch, batch_payload))
# # #                     try:
# # #                         async with self.redis.pipeline() as pipe:
# # #                             for sym, payload in results:
# # #                                 pipe.set(f"hot_metrics:{sym}", orjson.dumps(payload), ex=60)
# # #                             await pipe.execute()
# # #                     except Exception as e: logger.error(f"Redis Error: {e}")

# # #             await asyncio.sleep(0.1)

# # #     async def main(self):
# # #         self.shm_lock = DummyLock()
# # #         rm = await get_simple_redis_manager()
# # #         self.redis = rm.connections.get("local")
# # #         if not self.redis:
# # #             logger.error("❌ Could not connect to local Redis via manager!")
          
# # #         await self.load_symbols()
# # #         self.symbols_list = list(self.symbols)
# # #         try:
# # #             await asyncio.gather(
# # #                 self.ws_connect(), 
       
# # #                 self.ws_injector(),      # Task 1
# # #                 self.redis_injector(),   # Task 2
# # #                 self.json_injector(),    # Task 3
# # #                 self.broadcast_loop(),   # Task 4 (The Engine)
# # #                 self.heartbeat(),
# # #                 self.shared_mem_watchdog()
# # #             )
# # #         except KeyboardInterrupt:
# # #             self.running = False
# # #             self.executor.shutdown(wait=False)

# # # if __name__ == "__main__":
# # #     asyncio.run(MarketDataEngine().main())