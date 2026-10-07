import asyncio
import time

import orjson
from dateutil.parser import isoparse

from ez_share_ind import get_shared_memory_client
from utils import get_simple_redis_manager


async def test_pipeline():
    shm_mgr = get_shared_memory_client()
    rm = await get_simple_redis_manager()
    redis = rm.connections.get("local")

    now = time.time()
    
    shm_data = None
    if shm_mgr:
        store = shm_mgr.get_store()
        shm_data = store.get_symbol('ACEUSDT')

    redis_data = None
    if redis:
        raw_metrics = await redis.get(f"hot_metrics:ACEUSDT")
        if raw_metrics:
            redis_data = orjson.loads(raw_metrics)

    print(f"System Time: {now}")
    
    if shm_data:
        shm_ts = shm_data.get('_tick_ts', 0)
        print(f"SHM _tick_ts: {shm_ts} (Lag: {now - shm_ts:.3f}s) | k_1m: {shm_data.get('k_1m')}")
        
    if redis_data:
        redis_ts = redis_data.get('_tick_ts', 0)
        print(f"REDIS _tick_ts: {redis_ts} (Lag: {now - redis_ts:.3f}s) | k_1m: {redis_data.get('k_1m')}")

if __name__ == '__main__':
    asyncio.run(test_pipeline())
