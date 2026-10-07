import asyncio
import time
from decimal import Decimal

from binance.client import Client
from binance.exceptions import BinanceAPIException

# --- CONFIG ---
API_KEY = 'OpdmmueRR3ZhZVFS5oJufgBq1Z6hpjUttCW2yD1DRPLgk3hcx2ilKHMxJI8cGLIq'
API_SECRET = 'Uzwmm8ALzjw7eoAG4O9eh6lDnt93hIkIS1iMzaP2c3FPyjB7KGyhJyKj8nbmrk0E'
SYMBOL = 'BNBUSDC'  # USDC-M Futures
BUY_AMOUNT_USDC = 8.0
SIDE = 'SELL' 

async def run_maker_test():
    client = Client(API_KEY, API_SECRET, requests_params={'timeout': 10})
    
    info = await asyncio.to_thread(client.futures_exchange_info)
    symbol_info = next(s for s in info['symbols'] if s['symbol'] == SYMBOL)
    tick_size = Decimal(symbol_info['filters'][0]['tickSize'])
    step_size = Decimal(symbol_info['filters'][1]['stepSize'])

    print(f"Starting Queue-Priority Pulse on {SYMBOL}...")
    
    placement_start_time = time.time()
    active_order_id = None
    active_price = None

    while True:
        try:
            # 1. HEARTBEAT (Safety Net)
            # Set a 5-second fuse so we don't have to refresh it constantly
            await asyncio.to_thread(client.futures_countdown_cancel_all, symbol=SYMBOL, countdownTime=5000)

            # 2. CALCULATE TARGET PRICE
            ticker = await asyncio.to_thread(client.futures_symbol_ticker, symbol=SYMBOL)
            lp = Decimal(ticker['price'])
            elapsed = time.time() - placement_start_time
            
            if elapsed < 5.0:
                book = await asyncio.to_thread(client.futures_order_book, symbol=SYMBOL, limit=5)
                bb, ba = Decimal(book['bids'][0][0]), Decimal(book['asks'][0][0])
                if SIDE == 'BUY':
                    target = bb + tick_size
                    price = target if target < ba else bb
                else:
                    target = ba - tick_size
                    price = target if target > bb else ba
            else:
                price = (lp - tick_size) if SIDE == 'BUY' else (lp + tick_size)

            target_price_str = str(price.quantize(tick_size))

            # 3. QUEUE CHECK: Only replace if the target price changed
            if active_price == target_price_str:
                # Price is still optimal! Check if filled and sleep.
                order_check = await asyncio.to_thread(client.futures_get_order, symbol=SYMBOL, orderId=active_order_id)
                if order_check['status'] == 'FILLED':
                    print(f"\n!!! FILLED at {active_price} !!!")
                    return
                
                print(".", end="", flush=True) # Printing dots to show we are holding the queue
                await asyncio.sleep(0.5) 
                continue

            # 4. PRICE MOVED: Replace Order
            if active_order_id:
                try:
                    await asyncio.to_thread(client.futures_cancel_order, symbol=SYMBOL, orderId=active_order_id)
                except: pass # Order might have filled or expired already

            qty_str = str(Decimal(str(BUY_AMOUNT_USDC / float(target_price_str))).quantize(step_size))
            
            print(f"\nMoving to {target_price_str}...", end=" ", flush=True)
            
            new_order = await asyncio.to_thread(
                client.futures_create_order,
                symbol=SYMBOL,
                side=SIDE,
                positionSide='LONG' if SIDE == 'BUY' else 'SHORT',
                type='LIMIT',
                timeInForce='GTX',
                quantity=qty_str,
                price=target_price_str,
                newOrderRespType='RESULT'
            )

            if new_order['status'] == 'FILLED':
                print("!!! FILLED INSTANTLY !!!")
                return
            
            active_order_id = new_order['orderId']
            active_price = target_price_str

        except BinanceAPIException as e:
            if e.code == -5022:
                active_price = None # Force a re-calculation next loop
                continue
            print(f"\nError {e.code}: {e.message}")
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(run_maker_test())