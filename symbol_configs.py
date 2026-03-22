import asyncio
import json
import os
from binance import AsyncClient
from pathlib import Path
BASE_PATH:  str = Path.home() / "Documents" / "binance"
SYMBOL_CONFIGS_FILE = os.path.join(BASE_PATH, "symbol_configs.json")


async def fetch_symbol_configs(api_key, api_secret):
    """
    Fetches and saves symbol configurations for all Binance Futures symbols.
    """
    client = await AsyncClient.create(api_key, api_secret)
    try:
        exchange_info = await client.futures_exchange_info()
        symbol_configs = {}

        for symbol_data in exchange_info['symbols']:
            filters = symbol_data['filters']
            tick_size = float(next((f for f in filters if f['filterType'] == 'PRICE_FILTER'), {}).get('tickSize', 0))
            step_size = float(next((f for f in filters if f['filterType'] == 'LOT_SIZE'), {}).get('stepSize', 0))
            price_precision = int(symbol_data['pricePrecision'])
            quantity_precision = int(symbol_data['quantityPrecision'])

            if tick_size == 0 or step_size == 0:
                print(f"Skipping invalid symbol {symbol_data['symbol']} with tick_size={tick_size} and step_size={step_size}.")
                continue

            symbol_configs[symbol_data['symbol']] = {
                'price_precision': price_precision,
                'quantity_precision': quantity_precision,
                'tick_size': tick_size,
                'step_size': step_size
            }

        # Save to file
        save_symbol_configs(symbol_configs)
        print(f"Symbol configurations saved for {len(symbol_configs)} symbols.")
    except Exception as e:
        print(f"Error fetching symbol configurations: {e}")
    finally:
        await client.close_connection()


def save_symbol_configs(symbol_configs):
    """
    Save symbol configurations to a JSON file.
    """
    try:
        with open(SYMBOL_CONFIGS_FILE, 'w') as f:
            json.dump(symbol_configs, f, indent=4)
        print(f"Symbol configurations saved to {SYMBOL_CONFIGS_FILE}.")
    except Exception as e:
        print(f"Error saving symbol configurations: {e}")


if __name__ == "__main__":
    # Replace these with your Binance API key and secret
    API_KEY = "ks4ztJJU3QOc5AOxFdLgPprZAL9WPFZNyPVhVQ32q4zpQmkg0KYAkirww6QEEBQB"
    API_SECRET = "JyHOKhAodCawd25YCsBqDpnufWe6Y3pbYTkMeIl1YvFxGkBD1Skph1gNgRiCAhVZ"

    asyncio.run(fetch_symbol_configs(API_KEY, API_SECRET))
