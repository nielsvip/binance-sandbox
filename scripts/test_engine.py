import asyncio
import logging

from ez_market_data import MarketDataEngine

logging.basicConfig(level=logging.INFO)

async def test_engine():
    engine = MarketDataEngine()
    engine.symbols = {'BTCUSDC', 'ETHUSDC'}
    for s in engine.symbols:
        engine.market.init_symbol_from_disk(s)
    
    # Run the engine for 10 seconds to see if it receives websocket data and populates last_tick_ts
    task = asyncio.create_task(engine.ws_connect())
    
    for i in range(10):
        await asyncio.sleep(1)
        store = engine.market.data.get('BTCUSDC')
        if store:
            print(f"Second {i}: BTCUSDC last_tick_ts = {store.get('last_tick_ts')}")
        else:
            print(f"Second {i}: No store for BTCUSDC")
            
    task.cancel()

if __name__ == '__main__':
    asyncio.run(test_engine())
