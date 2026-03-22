#!/usr/bin/env python3
"""
Tradier Webhook Bridge - Receives TradingView webhooks and forwards to Tradier API
Similar to simple_ibkr_bridge.py but for Tradier

Webhook Format (TradingView):
- symbol: Stock symbol (e.g., "AAPL")
- side: "BUY" or "SELL"
- position_side: "LONG" or "SHORT" (optional but recommended)
- qty/quantity: Number of shares
- orderType: "market" or "limit" (or "MKT"/"LMT")
- price: Limit price (required for limit orders)
- action: "OPEN", "CLOSE", "REDUCE" (optional, defaults to "OPEN")
- secret: Webhook secret (MUST start with "tra" to distinguish from IBKR orders)

Translation Logic:
- side="BUY" + position_side="LONG" → Tradier side="buy"
- side="SELL" + position_side="SHORT" → Tradier side="sell_short"
- side="SELL" + position_side="LONG"  → Tradier side="sell"
- side="BUY" + position_side="SHORT"  → Tradier side="buy_to_cover"
"""
#!/usr/bin/env python3
"""
Tradier Webhook Bridge (Fixed Logic)
- Fixes 'NoneType' crash on missing action
- Implements Side + PositionSide logic
- Fallback to Account State check if PositionSide missing
"""

import asyncio
import json
import logging
from flask import Flask, request, jsonify
from datetime import datetime
import sys
import os
from pathlib import Path
import threading
import subprocess
from io import StringIO
from dotenv import dotenv_values

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from tradier_api import TradierAPIClient

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- GPG ENV LOADER ---
ENV_LOADED = False
def load_environment_from_gpg():
    global ENV_LOADED
    if ENV_LOADED: return True
    possible_env_paths = [
        os.path.join(os.getcwd(), ".env.gpg"),
        os.path.join(str(Path(__file__).parent), ".env.gpg")
    ]
    for env_path in possible_env_paths:
        if os.path.exists(env_path):
            try:
                cmd = ["gpg", "--batch", "--yes", "--decrypt", env_path]
                gpg_process = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if gpg_process.returncode == 0:
                    env_vars = dotenv_values(stream=StringIO(gpg_process.stdout))
                    for k, v in env_vars.items():
                        if v: os.environ[k] = v
                    ENV_LOADED = True
                    return True
            except Exception: pass
    return False

load_environment_from_gpg()
from config_tradier import TradierConfig

app = Flask(__name__)
config = TradierConfig()
CLIENT_CACHE = {}
_shared_loop = None
WEBHOOK_SECRET = os.getenv("TRADIER_WEBHOOK_SECRET") or os.getenv("TRA_WEBHOOK_SECRET", "")

def get_shared_loop():
    global _shared_loop
    if _shared_loop is None or _shared_loop.is_closed():
        _shared_loop = asyncio.new_event_loop()
        threading.Thread(target=_shared_loop.run_forever, daemon=True).start()
    return _shared_loop

def get_client_for_account(account_key: str):
    if account_key not in CLIENT_CACHE:
        CLIENT_CACHE[account_key] = TradierAPIClient(config, account_key=account_key)
    return CLIENT_CACHE[account_key]

def validate_secret(secret: str) -> bool:
    if not WEBHOOK_SECRET: return True
    if not secret: return False
    return secret == WEBHOOK_SECRET or (secret.startswith("tra") and WEBHOOK_SECRET.startswith("tra"))

async def get_position_for_account(account_key: str, symbol: str) -> float:
    try:
        client = get_client_for_account(account_key)
        await client.connect()
        positions = await client.get_account_positions()
        for p in positions:
            if p.get('symbol') == symbol:
                return float(p.get('quantity', 0.0))
        return 0.0
    except Exception as e:
        logger.error(f"[{account_key}] Error fetching position for {symbol}: {e}")
        return 0.0

async def execute_smart_order(params: dict):
    symbol = params['symbol']
    qty = params['quantity']
    account = params.get('account_key', 'trb') 
    raw_side = (params.get('raw_side') or 'BUY').upper()
    pos_side = (params.get('position_side') or '').upper()
    action = (params.get('action') or '').upper()

    try:
        client = get_client_for_account(account)
        await client.connect()
        current_pos = await get_position_for_account(account, symbol)
        
        final_side = None
        final_qty = qty

        logger.info(f"[{account}] {symbol} | Req: {raw_side} {pos_side} | Net: {current_pos}")

        # --- LOGIC BRANCHING ---

        # 1. explicit 'FLAT' command
        if action == "FLAT":
            if current_pos == 0:
                logger.info(f"[{account}] Already flat {symbol}. Skipping.")
                return {"status": "skipped", "reason": "already_flat"}
            final_qty = abs(current_pos)
            final_side = "sell" if current_pos > 0 else "buy_to_cover"

        # 2. EXPLICIT STRATEGY LOGIC (If position_side is provided)
        elif pos_side in ["LONG", "SHORT"]:
            if raw_side == "BUY" and pos_side == "LONG":
                final_side = "buy"          # Open Long
            elif raw_side == "SELL" and pos_side == "SHORT":
                final_side = "sell_short"   # Open Short
            elif raw_side == "SELL" and pos_side == "LONG":
                final_side = "sell"         # Close Long
            elif raw_side == "BUY" and pos_side == "SHORT":
                final_side = "buy_to_cover" # Close Short
            else:
                # Catch-all for unusual combos, default to raw side
                logger.warning(f"[{account}] Ambiguous side: {raw_side}/{pos_side}. Defaulting to {raw_side.lower()}.")
                final_side = raw_side.lower()

        # 3. SMART FALLBACK (If position_side is MISSING)
        else:
            logger.info(f"[{account}] No position_side provided. Using Smart Logic based on current net: {current_pos}")
            if raw_side == "BUY":
                if current_pos < 0:
                    final_side = "buy_to_cover" # We are Short -> Cover
                else:
                    final_side = "buy"          # Flat/Long -> Buy more
            
            elif raw_side == "SELL":
                if current_pos > 0:
                    final_side = "sell"         # We are Long -> Sell
                else:
                    final_side = "sell_short"   # Flat/Short -> Short more

        if not final_side:
            raise ValueError(f"Could not determine order side for {raw_side} {pos_side}")

        # Execute
        result = await client.place_order(
            account_key=account,
            symbol=symbol,
            side=final_side,
            quantity=final_qty,
            order_type=params['order_type'],
            price=params['price'],
            stop=params['stop'],
            duration=params['duration']
        )
        return result

    except Exception as e:
        logger.error(f"[{account}] Execution Error: {e}")
        raise

def parse_payload(data: dict) -> dict:
    account = data.get('account') or data.get('acc') or data.get('account_key') or "trb"
    symbol = (data.get('symbol') or data.get('ticker') or '').strip().upper()
    if not symbol: raise ValueError("Symbol missing")

    # Extract Side and Position Side
    side = (data.get('side') or 'BUY').strip()
    pos_side = data.get('position_side') or data.get('pos_side') # Can be None

    try:
        q_val = data.get('qty') or data.get('quantity') or data.get('size')
        qty = int(float(q_val)) if q_val else 1
    except Exception:
        qty = 1

    otype = (data.get('orderType') or data.get('type') or 'MKT').upper()
    
    return {
        'account_key': account,
        'symbol': symbol,
        'raw_side': side,
        'position_side': pos_side,
        'quantity': qty,
        'order_type': 'market' if 'MKT' in otype else 'limit' if 'LMT' in otype else 'stop',
        'price': float(data['price']) if data.get('price') else None,
        'stop': float(data['stop']) if data.get('stop') else None,
        'duration': 'gtc' if str(data.get('tif')).upper() == 'GTC' else 'day',
        'action': data.get('action') or data.get('orderAction') # Optional now
    }

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or request.form.to_dict()
        
        secret = data.get('secret') or data.get('webhook_secret')
        if not validate_secret(secret):
            return jsonify({"error": "Unauthorized"}), 401
            
        try:
            order_params = parse_payload(data)
        except ValueError as e:
            logger.error(f"❌ Payload Error: {e}")
            return jsonify({"error": str(e)}), 400
            
        loop = get_shared_loop()
        future = asyncio.run_coroutine_threadsafe(execute_smart_order(order_params), loop)
        result = future.result(timeout=30)
        
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Webhook Fatal: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    logger.info("="*60)
    logger.info("Starting Tradier Webhook Bridge (Fixed Logic)")
    app.run(host='0.0.0.0', port=8002, debug=False)