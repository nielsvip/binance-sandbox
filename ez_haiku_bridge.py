#!/usr/bin/env python3
"""
ez_haiku_bridge.py — Executes Haiku agent commands (reversals + augments)
Subscribes to Redis channels from ez_haiku_agent.py and executes via TradeManager.
Runs inside each ez_manage process (imported and started as a background task).

Can also run standalone for testing.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import redis.asyncio as aioredis

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import parse_position_key, safe_fetch_float

logger = logging.getLogger("haiku_bridge")


class HaikuBridge:
    def __init__(self, trade_manager=None, tracker_manager=None, hedge_engine=None, data_manager=None, allowed_accounts=None):
        self.trade_manager = trade_manager
        self.tracker_manager = tracker_manager
        self.hedge_engine = hedge_engine
        self.data_manager = data_manager
        self.allowed_accounts = set(allowed_accounts or [])
        self._running = False
        self._redis = None

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.info(f"[HAIKU_BRIDGE] Starting listener for accounts: {self.allowed_accounts}")
        self._redis = aioredis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
        asyncio.create_task(self._listen_reversals())
        asyncio.create_task(self._listen_augments())

    async def _listen_reversals(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("haiku_agent_reversals", "haiku_agent_reversals_tradier")
        logger.info("[HAIKU_BRIDGE] Subscribed to reversal channels")
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                await self._handle_reversal(data)
            except Exception as e:
                logger.error(f"[HAIKU_BRIDGE] Reversal error: {e}")

    async def _listen_augments(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe("haiku_agent_augments")
        logger.info("[HAIKU_BRIDGE] Subscribed to augment channel")
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                await self._handle_augment(data)
            except Exception as e:
                logger.error(f"[HAIKU_BRIDGE] Augment error: {e}")

    async def _handle_reversal(self, data: dict):
        pk = data.get("position_key", "")
        acct = data.get("account_key", "")
        if self.allowed_accounts and acct not in self.allowed_accounts:
            return
        if not self.trade_manager:
            logger.warning(f"[HAIKU_BRIDGE] No trade_manager — cannot reverse {pk}")
            return
        reason = data.get("reason", "HAIKU_REVERSAL")
        price = safe_fetch_float(data.get("price", 0), 0)
        logger.warning(f"[HAIKU_BRIDGE] Executing REVERSAL: {pk} — {reason}")
        try:
            # Import here to avoid circular imports at module level
            from ez_positions_quick import execute_trade_wrapper
            pos = await self.tracker_manager.get_position(pk)
            if not pos:
                logger.warning(f"[HAIKU_BRIDGE] Position {pk} not found — may already be closed")
                return
            pos_amt = abs(safe_fetch_float(pos.positionAmt, 0))
            if pos_amt <= 0:
                return
            # Reduce 100% of what was just added (or 50% if we can't determine exact amount)
            reduce_qty = pos_amt * 0.5
            current_price = price if price > 0 else safe_fetch_float(pos.markPrice, 0)
            if current_price <= 0:
                logger.error(f"[HAIKU_BRIDGE] No price for {pk}")
                return
            success, msg = await execute_trade_wrapper(self.trade_manager, self.tracker_manager, self.hedge_engine, acct, pk, pos_amt, 'REDUCE', current_price, reduce_qty, reason, is_hedge=False, data_manager=self.data_manager)
            if success:
                logger.warning(f"[HAIKU_BRIDGE] REVERSAL SUCCESS: {pk} reduced by {reduce_qty}")
            else:
                logger.error(f"[HAIKU_BRIDGE] REVERSAL FAILED: {pk} — {msg}")
        except Exception as e:
            logger.error(f"[HAIKU_BRIDGE] REVERSAL EXCEPTION: {pk} — {e}")

    async def _handle_augment(self, data: dict):
        pk = data.get("position_key", "")
        acct = data.get("account_key", "")
        if self.allowed_accounts and acct not in self.allowed_accounts:
            return
        if not self.trade_manager:
            logger.warning(f"[HAIKU_BRIDGE] No trade_manager — cannot augment {pk}")
            return
        qty = safe_fetch_float(data.get("qty", 0), 0)
        price = safe_fetch_float(data.get("price", 0), 0)
        reason = data.get("reason", "HAIKU_AUGMENT")
        if qty <= 0 or price <= 0:
            return
        logger.info(f"[HAIKU_BRIDGE] Executing AUGMENT: {pk} +{qty:.4f} @ {price} — {reason}")
        try:
            from ez_positions_quick import execute_trade_wrapper
            pos = await self.tracker_manager.get_position(pk)
            if not pos:
                logger.warning(f"[HAIKU_BRIDGE] Position {pk} not found for augment")
                return
            pos_amt = abs(safe_fetch_float(pos.positionAmt, 0))
            current_price = price
            success, msg = await execute_trade_wrapper(self.trade_manager, self.tracker_manager, self.hedge_engine, acct, pk, pos_amt, 'AUGMENT', current_price, qty, reason, is_hedge=False, data_manager=self.data_manager)
            if success:
                logger.info(f"[HAIKU_BRIDGE] AUGMENT SUCCESS: {pk} +{qty:.4f}")
            else:
                logger.error(f"[HAIKU_BRIDGE] AUGMENT FAILED: {pk} — {msg}")
        except Exception as e:
            logger.error(f"[HAIKU_BRIDGE] AUGMENT EXCEPTION: {pk} — {e}")


async def standalone_test():
    """Standalone mode — just logs what it receives, no execution."""
    logger.info("[HAIKU_BRIDGE] Running in standalone/test mode (log only, no execution)")
    r = aioredis.Redis(host='127.0.0.1', port=6379, decode_responses=True)
    pubsub = r.pubsub()
    await pubsub.subscribe("haiku_agent_reversals", "haiku_agent_reversals_tradier", "haiku_agent_augments")
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        channel = message["channel"]
        data = json.loads(message["data"])
        logger.info(f"[HAIKU_BRIDGE][{channel}] {json.dumps(data, indent=2)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    asyncio.run(standalone_test())
