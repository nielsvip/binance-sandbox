# poly_api.py
import os
import sys
import time
from py_clob_client.client import ClobClient
from py_clob_client.models import OrderArgs
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg
class PolyAPIClient:
    def __init__(self, is_paper=True):
        self.is_paper = is_paper
        # Source from your env.gpg decryption
        self.pk = os.getenv("POLY_API_WALLET_PK") 
        self.funder = os.getenv("POLY_API_WALLET_ADDRESS")
        
        # Polymarket uses different hosts for different regions
        self.host = "https://clob.polymarket.com"
        self.chain_id = 137 # Polygon
        
        # Official SDK Client
        self.client = ClobClient(
            self.host, 
            key=self.pk, 
            funder=self.funder, 
            chain_id=self.chain_id
        )

    async def get_klines(self, token_id, interval='1m'):
        """Fetches OHLC-like data from Poly history"""
        # Polymarket returns [timestamp, price] pairs. 
        # You'll need to aggregate these into OHLC for your indicators.
        res = await self.client.get_prices_history(
            market=token_id, 
            interval=interval
        )
        return res # List of {'t': timestamp, 'p': price}

    async def place_order(self, token_id, side, price, qty):
        if self.is_paper:
            # LOG and save to local paper_db
            return {"status": "PAPER_OK", "id": "sim-123"}
        
        # REAL Order logic
        return await self.client.create_and_post_order(OrderArgs(
            price=price,
            size=qty,
            side="BUY" if side == "LONG" else "SELL",
            token_id=token_id
        ))

async def poly_scan_winners():
    # Filter for active markets with high liquidity (safe entry/exit)
    # Order by 24h volume change
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "liquidity_num_min": 10000, # At least $10k in order book
        "volume_num_min": 50000,    # At least $50k traded today
        "limit": 50,
        "order": "volume_24h",
        "ascending": "false"
    }
    # Response includes 'last_trade_price' and 'price_change_24h'
    # Use 'price_change_24h' to find your "Fastest Movers"