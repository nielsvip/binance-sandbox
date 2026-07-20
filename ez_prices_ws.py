#!/home/niels/.conda/envs/binance_env/bin/python3
"""ez_prices_ws.py — Real-time 3m kline feed via Binance futures WebSocket.
Subscribes to kline_3m streams for all symbols, writes to klines_cache, publishes to Redis.
ez_prices.py (resampler) reads from klines:{symbol}:3m in Redis (use_external_3m=True).
"""
import asyncio
import json
import os
import signal
import ssl
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import aiofiles
import pandas as pd
import redis.asyncio as redis
import websockets
from redis import exceptions as redis_exceptions

from config import Config
from utils import setup_logger_with_rotation

config = Config()
logger = setup_logger_with_rotation("ez_prices_ws", "ez_prices_ws.log")

MAX_BARS = 2400
TARGET_BARS = 1800
REDIS_EXPIRY = config.REDIS_EXPIRY_SECONDS * 6  # ~18 min
WS_CHUNK_SIZE = 150  # symbols per WebSocket connection (Binance max 200)
HEARTBEAT_INTERVAL = 60


class KlineWSFeed:
    def __init__(self):
        self.symbols: List[str] = []
        self.api_to_original: Dict[str, str] = {}
        self.redis_client: Optional[redis.Redis] = None
        self._locks: Dict[str, asyncio.Lock] = {}
        self._shutdown = asyncio.Event()
        self._write_sem = asyncio.Semaphore(50)

    async def init(self):
        with open(config.SYMBOLS_FILE) as f:
            self.symbols = sorted(set(json.load(f)))
        for s in self.symbols:
            api_sym = s.replace("USDC", "USDT") if s.endswith("USDC") else s
            self.api_to_original[api_sym] = s
        host = "127.0.0.1" if config.REDIS_HOST in ["localhost", "127.0.0.1"] else config.REDIS_HOST
        self.redis_client = redis.Redis(host=host, port=config.REDIS_PORT, db=config.REDIS_DB, decode_responses=True, socket_connect_timeout=30, socket_timeout=60, health_check_interval=30, max_connections=3000, retry_on_timeout=True, retry_on_error=[redis_exceptions.TimeoutError, redis_exceptions.ConnectionError])
        n_chunks = (len(self.symbols) + WS_CHUNK_SIZE - 1) // WS_CHUNK_SIZE
        logger.info(f"🚀 ez_prices_ws initialized: {len(self.symbols)} symbols, {n_chunks} WS connections")

    def _lock(self, symbol: str) -> asyncio.Lock:
        if symbol not in self._locks:
            self._locks[symbol] = asyncio.Lock()
        return self._locks[symbol]

    def _cache_path(self, symbol: str) -> Path:
        return config.KLINES_CACHE_DIR / f"{symbol}_3m.json"

    async def _read_klines(self, symbol: str) -> pd.DataFrame:
        fp = self._cache_path(symbol)
        if not fp.exists() or fp.stat().st_size < 10:
            return pd.DataFrame()
        try:
            raw = await asyncio.to_thread(fp.read_text)
            df = pd.DataFrame(json.loads(raw))
            if "timestamp" not in df.columns:
                return pd.DataFrame()
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce").dt.floor("3min")
            return df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
        except Exception as e:
            logger.debug(f"Read {symbol}: {e}")
            return pd.DataFrame()

    async def _write_klines(self, symbol: str, df: pd.DataFrame):
        if df.empty:
            return
        fp = self._cache_path(symbol)
        fp.parent.mkdir(parents=True, exist_ok=True)
        if len(df) > MAX_BARS:
            df = df.tail(TARGET_BARS).copy()
        df_out = df.copy()
        df_out["timestamp"] = df_out["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        data = df_out[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
        tmp = fp.with_name(f".{fp.name}.{uuid.uuid4().hex}.atom")
        try:
            async with self._write_sem:
                async with aiofiles.open(tmp, "w") as f:
                    await f.write(json.dumps(data, indent=2))
                await asyncio.to_thread(os.replace, str(tmp), str(fp))
        except Exception as e:
            logger.error(f"Write {symbol}: {e}")
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    async def _publish_to_redis(self, symbol: str, df: pd.DataFrame):
        if not self.redis_client or df.empty:
            return
        try:
            rows = df.tail(TARGET_BARS).copy()
            rows["timestamp"] = rows["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            klines = rows[["timestamp", "open", "high", "low", "close", "volume"]].to_dict("records")
            payload = {"symbol": symbol, "interval": "3m", "published_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "klines": klines, "count": len(klines), "last_timestamp": klines[-1]["timestamp"] if klines else None, "source": "ez_prices_ws"}
            body = json.dumps(payload)
            await self.redis_client.setex(f"klines:{symbol}:3m", REDIS_EXPIRY, body)
            await self.redis_client.publish(f"klines:{symbol}:3m", body)
        except Exception as e:
            logger.debug(f"Redis publish {symbol}: {e}")

    async def _publish_latest_kline_event(self, symbol: str, kline_ts: datetime, close: float):
        """Notify ez_prices.py resampler that a new 3m bar closed."""
        if not self.redis_client:
            return
        try:
            ts_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            kline_payload = {"symbol": symbol, "interval": "3m", "timestamp": kline_ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "close": close, "published_at": ts_now, "source": "ez_prices_ws"}
            await self.redis_client.setex(f"latest_kline:{symbol}", 300, json.dumps(kline_payload))
            notification = {"symbol": symbol, "published_at_utc": ts_now, "action": "latest_kline_updated", "source": "ez_prices_ws"}
            note_body = json.dumps(notification)
            await self.redis_client.setex("latest_kline_updates", 300, note_body)
            await self.redis_client.publish("latest_kline_updates", note_body)
        except Exception as e:
            logger.debug(f"Publish latest kline {symbol}: {e}")

    async def _on_closed_kline(self, original_symbol: str, kline_ts: datetime, o: float, h: float, lo: float, c: float, v: float):
        async with self._lock(original_symbol):
            df = await self._read_klines(original_symbol)
            new_row = pd.DataFrame([{"timestamp": kline_ts, "open": o, "high": h, "low": lo, "close": c, "volume": v}])
            new_row["timestamp"] = pd.to_datetime(new_row["timestamp"], utc=True).dt.floor("3min")
            if not df.empty:
                df = pd.concat([df, new_row], ignore_index=True).drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
            else:
                df = new_row
            await self._write_klines(original_symbol, df)
            await self._publish_to_redis(original_symbol, df)
        await self._publish_latest_kline_event(original_symbol, kline_ts, c)
        logger.debug(f"📊 {original_symbol} 3m closed @ {c}")

    async def _startup_populate_redis(self):
        logger.info(f"📤 Startup: publishing {len(self.symbols)} symbols to Redis...")
        count = 0
        for symbol in self.symbols:
            if self._shutdown.is_set():
                break
            df = await self._read_klines(symbol)
            if not df.empty:
                await self._publish_to_redis(symbol, df)
                count += 1
            await asyncio.sleep(0.005)
        logger.info(f"✅ Startup: populated Redis for {count}/{len(self.symbols)} symbols")

    async def _run_chunk(self, chunk: List[str]):
        """Run WebSocket connection for one chunk of symbols, reconnecting forever."""
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        streams = "/".join(f"{(s.replace('USDC', 'USDT') if s.endswith('USDC') else s).lower()}@kline_3m" for s in chunk)
        url = f"wss://fstream.binance.com/stream?streams={streams}"
        delay = 5
        while not self._shutdown.is_set():
            try:
                async with websockets.connect(url, ssl=ssl_ctx, ping_interval=20, ping_timeout=30, close_timeout=10, max_size=2 ** 20) as ws:
                    delay = 5
                    logger.info(f"🔌 WS connected: {len(chunk)} symbols")
                    async for raw in ws:
                        if self._shutdown.is_set():
                            break
                        try:
                            data = json.loads(raw).get("data", {})
                            if data.get("e") != "kline":
                                continue
                            k = data["k"]
                            if not k.get("x"):
                                continue
                            original = self.api_to_original.get(k["s"], k["s"])
                            kline_ts = datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc)
                            asyncio.create_task(self._on_closed_kline(original, kline_ts, float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"])))
                        except Exception as e:
                            logger.debug(f"Msg error: {e}")
            except Exception as e:
                if self._shutdown.is_set():
                    break
                logger.warning(f"⚠️ WS error: {e} — reconnect in {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _heartbeat(self):
        while not self._shutdown.is_set():
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            logger.info(f"❤️ ez_prices_ws alive — {len(self.symbols)} symbols, {len(self._locks)} cached")

    async def run(self):
        await self.init()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown.set)
        asyncio.create_task(self._startup_populate_redis())
        asyncio.create_task(self._heartbeat())
        chunks = [self.symbols[i:i + WS_CHUNK_SIZE] for i in range(0, len(self.symbols), WS_CHUNK_SIZE)]
        logger.info(f"🚀 Starting {len(chunks)} WebSocket connections")
        tasks = [asyncio.create_task(self._run_chunk(chunk)) for chunk in chunks]
        await self._shutdown.wait()
        logger.info("🛑 Shutting down")
        for t in tasks:
            t.cancel()
        if self.redis_client:
            await self.redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(KlineWSFeed().run())
