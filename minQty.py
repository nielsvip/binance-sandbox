import json
import aiohttp
import logging
import asyncio

MIN_QTY_FILE = "/Users/niels/Documents/binance/min_qty.json"
BASE_URL = "https://fapi.binance.com/fapi/v1"
MIN_ORDER_VALUE = 5.5  # Default minimum order value in USD

logging.basicConfig(level=logging.INFO)

async def fetch_min_qty():
    min_qty_dict = {}
    async with aiohttp.ClientSession() as session:
        # Fetch exchange info to get minQty and minNotional values
        async with session.get(f"{BASE_URL}/exchangeInfo") as response:
            if response.status == 200:
                data = await response.json()
                for symbol_info in data['symbols']:
                    symbol = symbol_info['symbol']
                    min_qty = None
                    min_notional = None
                    for filter in symbol_info['filters']:
                        if filter['filterType'] == 'LOT_SIZE':
                            min_qty = float(filter['minQty'])
                        elif filter['filterType'] == 'MIN_NOTIONAL':
                            min_notional = float(filter['notional'])
                    if min_qty is not None:
                        min_qty_dict[symbol] = {
                            "minQty": min_qty,
                            "minNotional": min_notional if min_notional is not None else MIN_ORDER_VALUE
                        }
            else:
                logging.error("Failed to fetch exchange info from Binance API")
                return

        # Fetch current prices and adjust minQty based on order value
        async with session.get(f"{BASE_URL}/ticker/price") as price_response:
            if price_response.status == 200:
                prices = await price_response.json()
                for price_info in prices:
                    symbol = price_info['symbol']
                    if symbol in min_qty_dict:
                        current_price = float(price_info['price'])
                        min_qty = min_qty_dict[symbol]["minQty"]
                        min_notional = min_qty_dict[symbol]["minNotional"]
                        # Calculate minimum quantity for specified order value
                        min_qty_for_order_value = min_notional / current_price if current_price else 0
                        # Store the higher of minQty or the calculated value
                        min_qty_dict[symbol]["minQty"] = float(max(min_qty, min_qty_for_order_value))
            else:
                logging.error("Failed to fetch current prices from Binance API")
                return

    # Save the final minQty and minNotional data to JSON
    with open(MIN_QTY_FILE, "w") as file:
        json.dump(min_qty_dict, file, indent=4)
    logging.info(f"Saved adjusted minQty and minNotional for {len(min_qty_dict)} symbols to {MIN_QTY_FILE}")

def load_min_qty():
    try:
        with open(MIN_QTY_FILE, "r") as file:
            min_qty_data = json.load(file)
            logging.info(f"Loaded minQty data for {len(min_qty_data)} symbols.")
            return min_qty_data
    except FileNotFoundError:
        logging.error(f"{MIN_QTY_FILE} not found. Please run `fetch_min_qty()` to create it.")
        return {}

async def main():
    await fetch_min_qty()

# Run the main function
if __name__ == "__main__":
    asyncio.run(main())
