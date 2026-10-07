import asyncio
import logging

from ez_market_data import MarketDataEngine

logging.basicConfig(level=logging.INFO)

async def test_engine():
    engine = MarketDataEngine()
    engine.symbols = {'BTCUSDC'}
    for s in engine.symbols:
        engine.market.init_symbol_from_disk(s)
        
    task = asyncio.create_task(engine.ws_connect())
    
    for i in range(15):
        await asyncio.sleep(1)
        store = engine.market.data.get('BTCUSDC')
        if store:
            c1, c3, price, ts = engine.market.get_snapshot('BTCUSDC')
            if c1 is not None:
                print(f"Second {i}: c1 len: {len(c1)}, c3 len: {len(c3)}, t: {ts}")
            else:
                print(f"Second {i}: c1 is None")
            
    task.cancel()

if __name__ == '__main__':
    asyncio.run(test_engine())
