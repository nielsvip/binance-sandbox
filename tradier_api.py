import asyncio
import json
import logging
import os
import random
import re
import socket
import time
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import websockets

from config_tradier import TradierConfig

current_account = ContextVar("current_account", default="unknown")
logger = logging.getLogger("tradier_api")
logger.setLevel(logging.DEBUG)
logger.propagate = False
config=TradierConfig()

if not logger.handlers:
    # UID-free: PROHIBITED to use HOME/expanduser - use EZ_LOG_DIR or /tmp only
    log_dir = os.environ.get("EZ_LOG_DIR") or os.environ.get("TRADIER_API_LOG_DIR") or "/tmp"
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        log_dir = "/tmp"
    api_log_file = os.path.join(log_dir, "tradier_api.log")
    file_handler = RotatingFileHandler(api_log_file, maxBytes=50*1024*1024, backupCount=30, encoding='utf-8', mode='a')
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

class TradierAPIClient:
    _global_ban_expires: float = 0.0
    _ban_lock = asyncio.Lock()
    _shared_ip_cycle: Optional[deque] = None
    _shared_banned_ips: Dict[str, float] = {}
    _token_quarantine: Dict[str, float] = {} 

    def __init__(self, config: TradierConfig = None, account_key: str = None, override_config: Dict = None):
        self.config = config if config is not None else TradierConfig()
        
        self.LIVE_URL = "https://api.tradier.com/v1"
        self.SANDBOX_URL = "https://sandbox.tradier.com/v1"

        target_key = account_key or "tra"
        
        # 1. Determine Context (Sandbox vs Live)
        if target_key.lower() == 'trc':
            self._current_url = self.SANDBOX_URL
            is_sandbox = True
        else:
            self._current_url = self.LIVE_URL
            is_sandbox = False

        # 2. Resolve Credentials
        if override_config:
            self._current_key = override_config.get('api_key')
            self._current_id = override_config.get('account_id')
        else:
            cfg = self.config.get_account_config(target_key)
            if cfg:
                self._current_key = cfg['api_key']
                self._current_id = cfg['account_id']
            else:
                env_key_name = f"TRADIER_API_KEY_{target_key.upper()}"
                self._current_key = os.getenv(env_key_name)
                self._current_id = os.getenv(f"TRADIER_ACCOUNT_ID_{target_key.upper()}")

        if self._current_key:
            self._current_key = str(self._current_key).strip()

        # Debug Log for Initialization
        mask_key = f"...{self._current_key[-4:]}" if self._current_key else "None"
        # logger.info(f"[{target_key}] Client Init: ID={self._current_id} Key={mask_key} URL={'SANDBOX' if is_sandbox else 'LIVE'}")

        # 3. Resolve Data Context
        trb_cfg = self.config.get_account_config('trb')
        tra_cfg = self.config.get_account_config('tra')
        
        data_key_candidate = trb_cfg.get('api_key') if trb_cfg else os.getenv("TRADIER_API_KEY_TRB")
        if not data_key_candidate:
            data_key_candidate = tra_cfg.get('api_key') if tra_cfg else os.getenv("TRADIER_API_KEY_TRA")

        self._data_key = data_key_candidate
        
        if self._data_key:
            self._data_key = str(self._data_key).strip()
            self._data_url = self.LIVE_URL
        else:
            self._data_key = self._current_key
            self._data_url = self._current_url

        self.session = None
        self._rate_limiter = asyncio.Semaphore(self.config.API_RATE_LIMIT_PER_SECOND)
        self._last_request_time = 0.0
        self._min_request_interval = 1.0 / self.config.API_RATE_LIMIT_PER_SECOND
        self._current_ip = None

        available_ips = getattr(self.config, 'AVAILABLE_IPS', ["49.13.39.233"])#,"157.180.125.52"])# "5.75.211.216", 
        import sys
        if TradierAPIClient._shared_ip_cycle is None:
            if sys.platform != "darwin" and available_ips and os.getenv("EZ_DISABLE_IP_BINDING") != "1":
                shuffled = list(available_ips)
                random.shuffle(shuffled)
                TradierAPIClient._shared_ip_cycle = deque([]) #deque(shuffled)
            else:
                TradierAPIClient._shared_ip_cycle = deque([])

    def _mark_ip_banned(self, ip: str, cooldown_seconds: int = 300):
        if not ip or ip == "SYSTEM_DEFAULT": return
        TradierAPIClient._shared_banned_ips[ip] = time.time() + cooldown_seconds
        logger.warning(f"🚫 [IP_MGR] IP {ip} put on cooldown for {cooldown_seconds}s")

    def _probe_ip(self, ip: str) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(2)
            sock.bind((ip, 0))
            sock.close()
            return True
        except Exception: return False
    
    async def get_orders(self, account_key: str = None) -> List[Dict]:
        if not self._current_id: return []
        res = await self._request("GET", f"/accounts/{self._current_id}/orders", use_data_context=False)
        if res and isinstance(res, dict) and 'orders' in res:
            inner = res['orders']
            if inner == 'null' or inner is None: return []
            if isinstance(inner, dict) and 'order' in inner:
                o = inner['order']
                return o if isinstance(o, list) else [o]
        return []

    async def get_order_status(self, account_key: str, order_id: Any) -> Dict:
        if not self._current_id: return {}
        res = await self._request("GET", f"/accounts/{self._current_id}/orders/{order_id}", use_data_context=False)
        if res and 'order' in res:
            return res['order']
        return {}
        
    def _get_next_valid_ip(self) -> Optional[str]:
        if not TradierAPIClient._shared_ip_cycle or not len(TradierAPIClient._shared_ip_cycle): return None
        now = time.time()
        attempts = 0
        max_attempts = len(TradierAPIClient._shared_ip_cycle)
        while attempts < max_attempts:
            candidate = TradierAPIClient._shared_ip_cycle[0]
            cooldown_until = TradierAPIClient._shared_banned_ips.get(candidate, 0)
            if cooldown_until > now:
                TradierAPIClient._shared_ip_cycle.rotate(-1)
                attempts += 1
                continue
            if self._probe_ip(candidate): return candidate
            else:
                self._mark_ip_banned(candidate, cooldown_seconds=60)
                TradierAPIClient._shared_ip_cycle.rotate(-1)
                attempts += 1
                continue
        return None

    async def connect(self):
        if self.session is not None:
            if not self.session.closed:
                await self.session.close()
            self.session = None

        timeout = aiohttp.ClientTimeout(total=30, connect=5) 
        target_ip = self._get_next_valid_ip()
        
        if target_ip:
            self._current_ip = target_ip
            connector = aiohttp.TCPConnector(limit=50, limit_per_host=10, ssl=False, local_addr=(target_ip, 0), force_close=True, enable_cleanup_closed=True)
        else:
            self._current_ip = "SYSTEM_DEFAULT"
            connector = aiohttp.TCPConnector(limit=50, ssl=False, force_close=True)
        
        self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        logger.info(f"🔌 [CONNECT] Session created via {self._current_ip}")

    async def close(self):
        if self.session and not self.session.closed:
            try: await self.session.close()
            except Exception: pass
            finally: self.session = None

    async def trigger_rotation(self, reason: str = "Error"):
        await asyncio.sleep(2.0)
        if self._current_ip and self._current_ip != "SYSTEM_DEFAULT":
            logger.warning(f"♻️ [ROTATION] Triggered by {reason} on {self._current_ip}")
            self._mark_ip_banned(self._current_ip, cooldown_seconds=600)
            if TradierAPIClient._shared_ip_cycle: TradierAPIClient._shared_ip_cycle.rotate(-1)
        await self.close()

    async def _rate_limit(self):
        async with self._rate_limiter:
            now = time.time()
            elapsed = now - self._last_request_time
            if elapsed < self._min_request_interval: await asyncio.sleep(self._min_request_interval - elapsed)
            self._last_request_time = time.time()

    async def _request(self, method: str, endpoint: str, params: Dict = None, data: Dict = None, use_data_context: bool = False, headers: Dict = None, retry_count: int = 0) -> Dict:     
        if retry_count > 5:
            logger.error(f"❌ [GIVE UP] {endpoint} failed after 5 retries.")
            return {}
        if retry_count > 0:
            await asyncio.sleep(min(1.0 * (2 ** retry_count), 15))

        await self._rate_limit()
        token = self._data_key if use_data_context else self._current_key
        if token in TradierAPIClient._token_quarantine:
            if time.time() < TradierAPIClient._token_quarantine[token]:
                return {}
            else:
                del TradierAPIClient._token_quarantine[token]
        async with TradierAPIClient._ban_lock:
            if time.time() < TradierAPIClient._global_ban_expires:
                await asyncio.sleep(5) # Slow down the whole system
                return {}

        await self._rate_limit()
        if self.session is None or self.session.closed:
            await self.connect()

        if use_data_context:
            token = self._data_key
            base_url = self._data_url
            label = "DATA"
        else:
            token = self._current_key
            base_url = self._current_url
            label = "TRADE"

        if not token:
            logger.error(f"❌ Missing Token for {label}.")
            return {}

        url = f"{base_url}{endpoint}"
        req_headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if headers: req_headers.update(headers)
            
        try:
            local_session = self.session
            if local_session is None:
                await self.connect()
                local_session = self.session

            timeout = aiohttp.ClientTimeout(total=45) 
            
            async with local_session.request(method, url, params=params, data=data, headers=req_headers, timeout=timeout) as response:
                text = await response.text()
                if response.status != 200:
                    logger.error(f"❌ [API ERROR] Status: {response.status} | URL: {url} | Response: {text}")
                if response.status == 200:
                    try: return json.loads(text)
                    except Exception: return {}
                if response.status == 400 and "Quota Violation" in text:
                    logger.warning(f"🚫 [QUOTA] on {self._current_ip}")
                    TradierAPIClient._global_ban_expires = time.time() + 60
                    match = re.search(r'Expires\s+(\d+)', text)
                    if match: 
                        TradierAPIClient._global_ban_expires = int(match.group(1)) / 1000.0
                    await self.trigger_rotation("Quota Violation")
                    return await self._request(method, endpoint, params, data, use_data_context, headers, retry_count + 1)
                
                if response.status in [502, 503, 504]:
                    return await self._request(method, endpoint, params, data, use_data_context, headers, retry_count + 1)
                if response.status == 401:
                    async with TradierAPIClient._ban_lock:
                        # Quarantine the token for 1 hour
                        TradierAPIClient._token_quarantine[token] = time.time() + 3600
                        # Ban the IP for 1 hour
                        if self._current_ip and self._current_ip != "SYSTEM_DEFAULT":
                            self._mark_ip_banned(self._current_ip, cooldown_seconds=3600)
                        # Freeze ALL requests for 2 minutes to let Tradier's firewall reset
                        TradierAPIClient._global_ban_expires = time.time() + 120
                    
                    logger.critical(f"🚨 [CIRCUIT BREAKER] 401 Auth Failure. Killed Token ...{str(token)[-4:]}. IP {self._current_ip} banned.")
                    await self.trigger_rotation("401 Hard Stop")
                    return {}
                if response.status == 429:
                    # Rate limit? Stop everything for 30 seconds
                    async with TradierAPIClient._ban_lock:
                        TradierAPIClient._global_ban_expires = time.time() + 30
                    await self.trigger_rotation("429 Rate Limit")
                    return {} # Don't retry inside the loop, let the loop handle the empty dict

                return {}
                # if response.status == 401:
                #     logger.error(f"🚨 [401 UNAUTHORIZED] Revoking access for token: ...{token[-4:]} on {self._current_ip}")
                #     TradierAPIClient._token_quarantine[token] = time.time() + 360
                    
                #     if self._current_ip and self._current_ip != "SYSTEM_DEFAULT":
                #         self._mark_ip_banned(self._current_ip, cooldown_seconds=360)
                #     TradierAPIClient._global_ban_expires = time.time() + 60
                #     await self.trigger_rotation("401 Auth Failure")
                #     return {} 

                # if response.status == 400:
                #     logger.error(f"🚨 [400] Bad Request: {text}")
                #     return {}
                
                # logger.error(f"[API_FAIL] {response.status} {url}: {text[:100]}")
                # return {}

        except (AttributeError, aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError, asyncio.TimeoutError) as e:
            logger.warning(f"⚠️ Net Error ({type(e).__name__}: {e}) on {self._current_ip} for {method} {endpoint}. Reconnecting... (retry {retry_count})")
            await self.connect()
            return await self._request(method, endpoint, params, data, use_data_context, headers, retry_count + 1)

        except Exception as e:
            logger.error(f"❌ Unexpected Error: {repr(e)}")
            return {}

    async def get_account_positions(self, account_key: str = None) -> Optional[List[Dict]]:
        # 2026-05-16 GHOST_CLOSE fix: return None on API failure / unparseable response;
        # return [] ONLY on confirmed-empty broker. Caller (fetch_positions_from_api in
        # tradier_positions.py) treats None as "do NOT touch local state" and [] as
        # "broker truly empty — run absence logic". Previous behavior returned [] on both,
        # contributing to 697 phantom ghost-closes in 30d on NVDA/GOOGL/GLD.
        if not self._current_id: return None
        res = await self._request("GET", f"/accounts/{self._current_id}/positions", use_data_context=False)
        if res is None or not isinstance(res, dict) or not res:
            return None
        if 'positions' in res:
            inner = res['positions']
            if inner == 'null' or inner is None: return []  # CONFIRMED empty broker
            if isinstance(inner, dict) and 'position' in inner:
                p = inner['position']
                return p if isinstance(p, list) else [p]
            if isinstance(inner, list): return inner
        if 'symbol' in res: return [res]
        return None  # Unparseable shape — treat as API failure, do not act

    async def get_account_balances(self, account_key: str = None) -> Dict:
        if not self._current_id: return {}
        res = await self._request("GET", f"/accounts/{self._current_id}/balances", use_data_context=False)
        return res.get('balances', {}) if res else {}

    async def place_order(self, account_key: str, symbol: str, side: str, quantity: float, order_type: str="market", price: float=None, stop: float=None, duration: str="day") -> Dict:
        if not self._current_id: 
            return {"error": "Missing Account ID"}
        data = { "class": "equity", "symbol": symbol.upper(), "side": side.lower(),
            "quantity": str(int(quantity)), "type": order_type.lower(), "duration": duration.lower()  }
        if price: data["price"] = f"{float(price):.2f}"
        if stop: data["stop"] = f"{float(stop):.2f}"
        res = await self._request("POST", f"/accounts/{self._current_id}/orders", data=data, use_data_context=False)
        if not res:
            return {"status": "error", "reason": "Gateway Rejected / Bad Request"}
            
        return res
    async def place_option_order(self, account_key: str, symbol: str, option_symbol: str, side: str, quantity: int, order_type: str = "limit", price: float = None, duration: str = "day") -> Dict:
        """Place an option order. side: buy_to_open, sell_to_close, buy_to_close, sell_to_open."""
        if not self._current_id:
            return {"error": "Missing Account ID"}
        data = {"class": "option", "symbol": symbol.upper(), "option_symbol": option_symbol, "side": side.lower(), "quantity": str(int(quantity)), "type": order_type.lower(), "duration": duration.lower()}
        if price is not None:
            data["price"] = f"{float(price):.2f}"
        res = await self._request("POST", f"/accounts/{self._current_id}/orders", data=data, use_data_context=False)
        if not res:
            return {"status": "error", "reason": "Gateway Rejected / Bad Request"}
        return res

    async def place_multileg_option_order(self, symbol: str, legs: List[Dict], order_type: str = "credit", price: float = None, duration: str = "day") -> Dict:
        """Place a multi-leg option order (e.g. bull put credit spread).
        legs = [{'option_symbol': OCC, 'side': 'sell_to_open', 'quantity': 1}, ...]
        order_type: 'credit' (receive net credit, price > 0), 'debit' (pay), 'even', 'market'.
        For a bull put credit spread: order_type='credit', price = net credit amount (positive)."""
        if not self._current_id:
            return {"error": "Missing Account ID"}
        if not legs or len(legs) < 2:
            return {"error": "Multileg requires >=2 legs"}
        data = {"class": "multileg", "symbol": symbol.upper(), "type": order_type.lower(), "duration": duration.lower()}
        if price is not None:
            data["price"] = f"{float(price):.2f}"
        for i, leg in enumerate(legs):
            data[f"option_symbol[{i}]"] = leg["option_symbol"]
            data[f"side[{i}]"] = leg["side"].lower()
            data[f"quantity[{i}]"] = str(int(leg.get("quantity", 1)))
        res = await self._request("POST", f"/accounts/{self._current_id}/orders", data=data, use_data_context=False)
        if not res:
            return {"status": "error", "reason": "Gateway Rejected / Bad Request"}
        return res

    async def get_option_expirations(self, symbol: str) -> List[str]:
        """Get all available expiration dates for a symbol's options."""
        res = await self._request("GET", "/markets/options/expirations", params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"}, use_data_context=True)
        if res and "expirations" in res and res["expirations"]:
            dates = res["expirations"].get("date", [])
            if isinstance(dates, str):
                return [dates]
            return dates if isinstance(dates, list) else []
        return []

    async def get_option_chain(self, symbol: str, expiration: str, greeks: bool = True) -> List[Dict]:
        """Fetch full option chain for a symbol and expiration."""
        res = await self._request("GET", "/markets/options/chains", params={"symbol": symbol, "expiration": expiration, "greeks": "true" if greeks else "false"}, use_data_context=True)
        if res and "options" in res and res["options"]:
            chain = res["options"].get("option", [])
            if isinstance(chain, dict):
                return [chain]
            return chain if isinstance(chain, list) else []
        return []

    async def get_option_strikes(self, symbol: str, expiration: str) -> List[float]:
        """Get available strikes for a symbol and expiration."""
        res = await self._request("GET", "/markets/options/strikes", params={"symbol": symbol, "expiration": expiration}, use_data_context=True)
        if res and "strikes" in res and res["strikes"]:
            strikes = res["strikes"].get("strike", [])
            if isinstance(strikes, (int, float)):
                return [float(strikes)]
            return [float(s) for s in strikes] if isinstance(strikes, list) else []
        return []

    async def cancel_order(self, account_key: str, order_id: Any) -> Dict:
        if not self._current_id: return {}
        return await self._request("DELETE", f"/accounts/{self._current_id}/orders/{order_id}", use_data_context=False) or {}

    async def get_quotes(self, symbols: List[str]) -> Dict[str, Dict]:
        if not symbols: return {}
        res = await self._request("GET", "/markets/quotes", params={"symbols": ",".join(symbols)}, use_data_context=True)
        quotes = {}
        if res and 'quotes' in res and 'quote' in res['quotes']:
            q = res['quotes'].get('quote', [])
            if not isinstance(q, list): q = [q]
            for item in q:
                if 'symbol' in item: quotes[item['symbol']] = item
        return quotes

    async def get_quote(self, symbol: str) -> Dict:
        qs = await self.get_quotes([symbol])
        return qs.get(symbol, {})

    async def get_timesales(self, symbol: str, interval: str = "1min", start: str = None, end: str = None) -> List[Dict]:
        params = {"symbol": symbol, "interval": interval}
        
        # IGNORE the passed start parameter - always use 26 days ago
        from datetime import datetime, timedelta
        start_date = datetime.now() - timedelta(days=26)
        calculated_start = start_date.strftime("%Y-%m-%d 00:00:00")
        
        # Ensure minimum date
        min_date = datetime(2026, 5, 12)
        if start_date < min_date:
            calculated_start = "2026-05-12 00:00:00"
        
        params["start"] = calculated_start
        if end: params["end"] = end
        
        
        res = await self._request("GET", "/markets/timesales", params=params, use_data_context=True)
        if res and 'series' in res and res['series'] is not None:
            data = res['series'].get('data', [])
            return data if isinstance(data, list) else [data]
        return []
    
    # async def get_timesales(self, symbol: str, interval: str = "1min", start: str = None, end: str = None) -> List[Dict]:
    #     params = {"symbol": symbol, "interval": interval}
    #     if start: params["start"] = start
    #     if end: params["end"] = end
    #     res = await self._request("GET", "/markets/timesales", params=params, use_data_context=True)
    #     if res and 'series' in res and res['series'] is not None:
    #          data = res['series'].get('data', [])
    #          return data if isinstance(data, list) else [data]
    #     return []

    async def get_history(self, symbol: str, start: str = None, end: str = None, interval: str = 'daily') -> List[Dict]:
        params = {"symbol": symbol, "interval": interval}
        if start: params["start"] = start
        if end: params["end"] = end
        res = await self._request("GET", "/markets/history", params=params, use_data_context=True)
        if res and 'history' in res and res['history'] is not None:
             data = res['history'].get('day', [])
             return data if isinstance(data, list) else [data]
        return []

    async def create_market_session(self) -> str:
        res = await self._request("POST", "/markets/events/session", use_data_context=True)
        if res and 'stream' in res and res['stream'] is not None:
            return res['stream'].get('sessionid')
        return None

    async def stream_quotes(self, symbols: List[str], callback: Callable[[Dict], None]):
        ws_endpoint = f"{self.config.TRADIER_WS_URL}/markets/events"
        while True:
            try:
                session_id = await self.create_market_session()
                if not session_id:
                    await asyncio.sleep(10)
                    continue
                async with websockets.connect(ws_endpoint, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({
                        "symbols": ",".join(symbols), "filter": "trade", "sessionid": session_id
                    }))
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            if data.get("type") != "error": callback(data)
                        except Exception: pass
            except Exception:
                await asyncio.sleep(self.config.WS_RECONNECT_DELAY)
