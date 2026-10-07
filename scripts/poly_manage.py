import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import redis.asyncio as redis
from py_clob_client import OrderArgs
from py_clob_client.client import ClobClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import load_environment_from_gpg

load_environment_from_gpg(None)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("poly_manage")
POLY_WALLET_PK = os.getenv("POLY_API_WALLET_PK")
POLY_WALLET_ADDR = os.getenv("POLY_API_WALLET_ADDRESS")

class PolyPosition:
    def __init__(self, market_id, symbol, side, qty, entry_price, token_id):
        self.market_id = market_id
        self.symbol = symbol  # The question ticker
        self.side = side      # "YES" or "NO"
        self.qty = qty
        self.entry_price = entry_price
        self.mark_price = entry_price
        self.token_id = token_id
        self.opened_at = time.time()
        self.last_update = time.time()
        self.gain = 0.0

class PolyPositionManager:
    def __init__(self, is_paper=True):
        self.is_paper = is_paper
        self.redis_client = None
        self.clob_client = None
        self.positions: Dict[str, PolyPosition] = {}
        self.wallet_addr = os.getenv("POLY_API_WALLET_ADDRESS")
        if self.is_paper:
            self.wallet_addr = os.getenv("POLY_API_WALLET_ADDRESS_PAPER")
        self.data_dir = Path("./data/poly")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.paper_file = self.data_dir / "poly_paper_positions.json"
        
        self.MIN_VOL_THRESHOLD = 50000 # Only trade if $50k+ daily volume
        self.STOP_LOSS_DC_WINDOW = "15m"
        self.TAKE_PROFIT_PCT = 15.0    # 15% Gain target

    async def init_clients(self):
        """Initialize Redis and Polymarket CLOB Client"""
        self.redis_client = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
        
        # Initialize Real CLOB Client (needed for prices even in paper mode)
        self.clob_client = ClobClient(
            host="https://clob.polymarket.com",
            key=POLY_WALLET_PK,
            funder=POLY_WALLET_ADDR,
            chain_id=137
        )
        await self.load_paper_positions()

    async def load_paper_positions(self):
        if self.paper_file.exists():
            try:
                with open(self.paper_file, 'r') as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self.positions[k] = PolyPosition(**v)
            except: pass

    async def save_paper_positions(self):
        data = {k: v.__dict__ for k, v in self.positions.items()}
        with open(self.paper_file, 'w') as f:
            json.dump(data, f, indent=2)

    async def get_clob_midpoint(self, token_id):
        """Gets current Bid/Ask midpoint from Redis (populated by poly_prices.py)"""
        raw = await self.redis_client.get("poly_prices_latest")
        if raw:
            prices = json.loads(raw)
            entry = prices.get(token_id)
            if entry:
                return float(entry['price']), entry['timestamp']
        return None, None

    async def process_market(self, symbol, m_data):
        """Decision Logic for a single market"""
        market_id = m_data['market_id']
        yes_token = m_data['yes_token']
        no_token = m_data['no_token']

        # 0. Apply Core Market Rules
        liq_num = m_data.get('liquidity_num', 0)
        if liq_num < 5000:
            return # Require liquidity threshold
            
        end_date_str = m_data.get('end_date_iso')
        if end_date_str:
            try:
                # end_date_iso is like '2024-11-05T00:00:00Z'
                end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                if (end_dt - datetime.now(timezone.utc)).days < 3:
                    return # Avoid last 3 days before resolution
            except Exception:
                pass

        # 1. Get Indicators (calculated on YES token price)
        raw_ind = await self.redis_client.get(f"poly_indicators:{symbol}")
        if not raw_ind: return
        ind = json.loads(raw_ind)
        
        current_price = ind.get('current_price') or 0.5

        for side in ["YES", "NO"]:
            pos_key = f"{symbol}_{side}"
            if pos_key in self.positions:
                await self.check_exit_logic(self.positions[pos_key], ind)

        if not current_price or current_price < 0.25 or current_price > 0.75:
            return  # Trade only in 25-75 zone, penalize 0/1 bounds
            
        stoch_k = ind.get('stoch_k_15m') or 50
        t_up = ind.get('t_up_1h') if ind.get('t_up_1h') is not None else True

        if len(self.positions) < 5:
            if stoch_k < 20 and t_up:
                await self.execute_trade(symbol, "YES", yes_token, current_price)
            elif stoch_k > 80 and not t_up:
                await self.execute_trade(symbol, "NO", no_token, (1.0 - current_price))

    async def check_exit_logic(self, pos: PolyPosition, ind: dict):
        """Firm Stop Loss at Donchian Channel Low/High"""
        now_price, _ = await self.get_clob_midpoint(pos.token_id)
        if not now_price: return

        pos.mark_price = now_price
        pos.gain = ((now_price - pos.entry_price) / pos.entry_price) * 100

        # EXIT 1: Take Profit
        if pos.gain >= self.TAKE_PROFIT_PCT:
            await self.close_position(pos, f"TP Hit: {pos.gain:.2f}%")

        # EXIT 2: Stop Loss (Firm Donchian)
        # For YES: Exit if YES price < DC_LOW
        # For NO: Exit if YES price > DC_HIGH (Inverse correlation)
        if pos.side == "YES":
            dc_low = ind.get('dc_low_15m') or 0
            if dc_low > 0 and now_price < dc_low:
                await self.close_position(pos, "SL: DC_LOW HIT")
        else:
            dc_high = ind.get('dc_high_15m') or 1
            if (1.0 - now_price) > dc_high:
                await self.close_position(pos, "SL: DC_HIGH_YES HIT")

    async def execute_trade(self, symbol, side, token_id, price):
        pos_key = f"{symbol}_{side}"
        if pos_key in self.positions: return

        # Slippage Factor (0.5% for Poly Orderbook)
        execution_price = price * 1.005 
        
        if self.is_paper:
            logger.info(f"🧪 PAPER BUY: {side} on {symbol} at {execution_price:.4f}")
            self.positions[pos_key] = PolyPosition(
                market_id=None, symbol=symbol, side=side, 
                qty=100, entry_price=execution_price, token_id=token_id
            )
            await self.save_paper_positions()
        else:
            # REAL CLOB TRADE
            try:
                # qty must be integer for shares usually
                resp = self.clob_client.create_and_post_order(OrderArgs(
                    price=round(execution_price, 2),
                    size=100,
                    side="BUY",
                    token_id=token_id
                ))
                logger.info(f"💰 REAL BUY: {side} {symbol} Resp: {resp}")
            except Exception as e:
                logger.error(f"Trade Failed: {e}")

    async def close_position(self, pos: PolyPosition, reason: str):
        logger.info(f"🚩 EXIT {pos.symbol} {pos.side}: {reason} | Final Gain: {pos.gain:.2f}%")
        del self.positions[f"{pos.symbol}_{pos.side}"]
        if self.is_paper:
            await self.save_paper_positions()
        else:
            # Send Sell Order to CLOB
            pass

    async def main_loop(self):
        await self.init_clients()
        logger.info(f"☀️ Polymarket Manager Started {'(PAPER MODE)' if self.is_paper else '(LIVE)'}")
        
        while True:
            try:
                # 1. Get Active Symbols from Scanner
                raw_map = await self.redis_client.get("poly_symbols_map")
                if not raw_map:
                    await asyncio.sleep(10)
                    continue
                
                symbol_map = json.loads(raw_map)
                
                # 2. Process each market
                for symbol, m_data in symbol_map.items():
                    await self.process_market(symbol, m_data)
                
                await asyncio.sleep(15) # Pulse every 15s
            except Exception as e:
                logger.error(f"Manager Loop Error: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Run with real wallet funds")
    args = parser.parse_args()
    
    manager = PolyPositionManager(is_paper=not args.live)
    asyncio.run(manager.main_loop())