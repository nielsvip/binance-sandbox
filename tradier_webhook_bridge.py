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
import time
from flask import Flask, request, jsonify
from datetime import datetime, timezone
import sys
import os
from pathlib import Path
import threading
import subprocess
from io import StringIO
from dotenv import dotenv_values
import redis

# === ORDER DEDUPE (2026-04-27 — webhook burst protection) ===
# Webhooks can fire in bursts (TV alert spam). Reject same (account, symbol, side)
# within cooldown window so a flood of identical alerts only fires one order.
_ORDER_DEDUPE_LOG: dict = {}
_ORDER_DEDUPE_SEC: float = 60.0

def _order_allowed(key: str, log) -> bool:
    now = time.time()
    last = _ORDER_DEDUPE_LOG.get(key, 0.0)
    if now - last < _ORDER_DEDUPE_SEC:
        log.warning(f"[ORDER_DEDUPE_SKIP] {key} — last attempt {now-last:.0f}s ago < {_ORDER_DEDUPE_SEC:.0f}s")
        return False
    _ORDER_DEDUPE_LOG[key] = now
    return True

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

# TradingView webhook source IPs (per https://www.tradingview.com/support/solutions/43000529348)
# Defense-in-depth: nginx allow-list at edge, plus this app-level check.
TRADINGVIEW_IPS = {
    "52.89.214.238",
    "34.212.75.30",
    "54.218.53.128",
    "52.32.178.7",
}

def get_client_ip() -> str:
    """Resolve real client IP behind nginx reverse proxy."""
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""

def validate_source_ip() -> bool:
    """Return True if request comes from TradingView or localhost (for testing)."""
    ip = get_client_ip()
    if ip in TRADINGVIEW_IPS:
        return True
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if ip.startswith("10.0.0."):  # internal network
        return True
    logger.warning(f"[WEBHOOK_REJECT_IP] Rejected request from {ip}")
    return False

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

        # Dedupe: refuse identical (account, symbol, side) within 60s.
        if not _order_allowed(f"{account}|{symbol}|{final_side}", logger):
            return {"skipped": "dedupe", "key": f"{account}|{symbol}|{final_side}"}
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
        if not validate_source_ip():
            return jsonify({"error": "Forbidden"}), 403
        data = request.get_json(silent=True) or request.form.to_dict()
        logger.info(f"[WEBHOOK_RAW] from={get_client_ip()} {json.dumps(data, default=str)}")

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

        logger.info(f"[WEBHOOK_RESULT] {order_params.get('symbol')} {order_params.get('raw_side')} qty={order_params.get('quantity')} → {json.dumps(result, default=str)}")
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Webhook Fatal: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

CRYPTO_REDIS = None
def get_crypto_redis():
    global CRYPTO_REDIS
    if CRYPTO_REDIS is None:
        CRYPTO_REDIS = redis.Redis(host='127.0.0.1', port=6379, decode_responses=True, socket_connect_timeout=3)
    return CRYPTO_REDIS

CRYPTO_SIDE_MAP = {
    "BUY": {"event_type": "wt_crossover", "action": "BUY"},
    "SELL": {"event_type": "wt_crossunder", "action": "SELL"},
    "CLOSE_LONG": {"event_type": "wt_crossunder", "action": "CLOSE"},
    "CLOSE_SHORT": {"event_type": "wt_crossover", "action": "CLOSE"},
    "REDUCE": {"event_type": "wt_crossunder", "action": "REDUCE"},
}

@app.route('/crypto', methods=['POST'])
def crypto_webhook():
    try:
        if not validate_source_ip():
            return jsonify({"error": "Forbidden"}), 403
        data = request.get_json(silent=True) or request.form.to_dict()
        secret = data.get('secret') or data.get('webhook_secret')
        if not validate_secret(secret):
            return jsonify({"error": "Unauthorized"}), 401
        symbol = (data.get('symbol') or data.get('ticker') or '').strip().upper()
        if not symbol:
            return jsonify({"error": "Symbol missing"}), 400
        side = (data.get('side') or 'BUY').strip().upper()
        mapping = CRYPTO_SIDE_MAP.get(side, CRYPTO_SIDE_MAP["BUY"])
        price = float(data['price']) if data.get('price') else 0
        signal = {
            "event_type": data.get('event_type') or mapping["event_type"],
            "symbol": symbol,
            "action": data.get('action') or mapping["action"],
            "price": price,
            "source": "tradingview_webhook",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "event_type": data.get('event_type') or mapping["event_type"],
                "symbol": symbol,
                "position_side": data.get('position_side', ''),
                "account": data.get('account', ''),
            }
        }
        r = get_crypto_redis()
        r.publish("signals_data", json.dumps(signal))
        logger.info(f"[CRYPTO] Published {symbol} {side} → Redis signals_data")
        return jsonify({"success": True, "symbol": symbol, "side": side, "published": True})
    except redis.ConnectionError as e:
        logger.error(f"[CRYPTO] Redis connection failed: {e}")
        return jsonify({"error": "Redis unavailable"}), 503
    except Exception as e:
        logger.error(f"[CRYPTO] Error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    logger.info("="*60)
    logger.info("Starting Tradier Webhook Bridge (Fixed Logic)")
    app.run(host='0.0.0.0', port=8002, debug=False)