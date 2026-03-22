#!/usr/bin/env python3
"""
ez_haiku_agent.py — 24/7 AI Oversight Agent
Monitors all trading decisions (crypto, tradier, polymarket) and:
  1. Reverses any action Haiku deems stupid (bad stoch/RSI, augmenting losers, bad news)
  2. Augments any position in >3% gain with 10% per candle until <3%
Runs as a systemd service. Uses Claude Haiku for fast, cheap LLM judgement.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import aiofiles
import redis.asyncio as aioredis

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import load_environment_from_gpg, parse_position_key, pk_is_long, pk_is_short, safe_fetch_float

load_environment_from_gpg(None)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s [HAIKU_AGENT] %(message)s", handlers=[logging.FileHandler("/home/niels/logs/ez_haiku_agent.log"), logging.StreamHandler()])
logger = logging.getLogger("haiku_agent")

CRYPTO_ACCOUNTS = ["ang", "inf", "men", "fin", "flz"]
TRADIER_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + TRADIER_ACCOUNTS
DECISION_DIR = Path("/home/niels/binance/data/decisions")
POLL_INTERVAL = 5
AUGMENT_INTERVAL = 60  # check gains every 60s
AUGMENT_GAIN_THRESHOLD = 3.0  # augment anything above 3%
REDUCE_GAIN_THRESHOLD = 2.5  # reduce augmented positions if gain drops below 2.5%
AUGMENT_FRACTION = 0.10  # 10% of current position size
HAIKU_MODEL = "claude-haiku-4-5-20251001"
COOLDOWN_SECONDS = 300  # 5min cooldown per position after reversal
MIN_POSITION_VALUE = 5.0  # don't augment tiny positions

# Track what we've already processed
_processed_decisions: Set[str] = set()
_reversal_cooldowns: Dict[str, float] = {}
_augment_cooldowns: Dict[str, float] = {}
# Track positions we augmented so we can reduce/re-enter
# {pk: {"augmented_at_gain": 4.2, "total_augmented_qty": 0.5, "reduced": False, "reduce_price": 0.0}}
_haiku_managed: Dict[str, dict] = {}


def _decision_fingerprint(d: dict) -> str:
    return f"{d.get('timestamp','')}__{d.get('position_key','')}__{d.get('action','')}"


async def call_haiku(prompt: str) -> Optional[dict]:
    """Call Claude Haiku and return parsed JSON response."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=HAIKU_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Extract JSON from response
        if "{" in text:
            json_str = text[text.index("{"):text.rindex("}") + 1]
            return json.loads(json_str)
        return None
    except Exception as e:
        logger.error(f"Haiku call failed: {e}")
        return None


def build_judgement_prompt(decision: dict) -> str:
    """Build prompt for Haiku to judge a trading decision."""
    snap = decision.get("snapshot") or decision.get("indicators") or {}
    action = decision.get("action", "")
    reason = decision.get("reason") or decision.get("reason_text", "")
    pk = decision.get("position_key", "")
    is_long = pk.endswith("_LONG") or "_YES" in pk
    is_short = pk.endswith("_SHORT") or "_NO" in pk
    k15 = snap.get("k_15m") or snap.get("stoch_k_15m")
    d15 = snap.get("d_15m") or snap.get("stoch_d_15m")
    k1m = snap.get("k_1m") or snap.get("stoch_k_1m")
    k3m = snap.get("k_3m") or snap.get("stoch_k_3m")
    sentiment = snap.get("sentiment") or snap.get("sentiment_score")
    ha_15m = snap.get("ha_15m") or snap.get("ha_5m")
    price = snap.get("price") or snap.get("current_price")
    return f"""You are a trading risk overseer. Judge this trade decision. Respond with ONLY valid JSON.

DECISION:
- Position: {pk}
- Action: {action}
- Reason: {reason}
- Direction: {"LONG" if is_long else "SHORT" if is_short else "UNKNOWN"}

INDICATORS AT DECISION TIME:
- Price: {price}
- Stoch K 15m: {k15}, D 15m: {d15}
- Stoch K 1m: {k1m}, K 3m: {k3m}
- Heikin-Ashi 15m: {ha_15m}
- Sentiment: {sentiment}

RULES FOR STUPID TRADES (reverse these):
1. OPENING/AUGMENTING a LONG when stoch K_15m > 85 (overbought = about to drop)
2. OPENING/AUGMENTING a SHORT when stoch K_15m < 15 (oversold = about to bounce)
3. AUGMENTING any position that is losing (reason contains "loss" or negative gain indicators)
4. OPENING into terrible sentiment (sentiment < -50) for longs or (sentiment > 50) for shorts
5. AUGMENTING when ALL stoch timeframes (1m, 3m, 15m) are against the direction
6. Any action where the reason itself indicates desperation (RATIO_RECOVERY into overbought, etc.)

NOT stupid (do NOT reverse):
- CLOSE/REDUCE actions (taking profit or cutting = fine)
- HEDGE opens (these are protective)
- Positions with reason containing REENTRY, DC_BREAKOUT, FORCE

Respond: {{"verdict": "REVERSE" or "OK", "confidence": 0.0-1.0, "reason": "brief explanation"}}
Only say REVERSE if confidence >= 0.75. Otherwise say OK."""


async def get_redis() -> aioredis.Redis:
    return aioredis.Redis(host='127.0.0.1', port=6379, decode_responses=True)


async def reverse_crypto_trade(redis_client: aioredis.Redis, decision: dict, haiku_reason: str):
    """Reverse a crypto trade by publishing a counter-order to Redis."""
    pk = decision.get("position_key", "")
    action = decision.get("action", "")
    account = decision.get("account", "")
    snap = decision.get("snapshot") or {}
    price = safe_fetch_float(snap.get("price"), 0)
    if not pk or not account or price <= 0:
        logger.warning(f"Cannot reverse {pk}: missing data")
        return
    # For OPEN/AUGMENT → we want to REDUCE the same amount
    # We publish a reversal command to Redis that ez_positions_quick picks up
    reversal = {
        "position_key": pk,
        "account_key": account,
        "action": "REDUCE",
        "reason": f"HAIKU_REVERSAL: {haiku_reason}",
        "price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "haiku_agent",
    }
    await redis_client.publish("haiku_agent_reversals", json.dumps(reversal))
    await redis_client.set(f"haiku_reversal:{pk}", json.dumps(reversal), ex=600)
    # Also write to quarantine to prevent re-entry for 5 minutes
    await redis_client.set(f"haiku_cooldown:{pk}", "1", ex=COOLDOWN_SECONDS)
    logger.warning(f"REVERSED {action} on {pk}: {haiku_reason}")


async def reverse_tradier_trade(redis_client: aioredis.Redis, decision: dict, haiku_reason: str):
    """Reverse a tradier trade."""
    pk = decision.get("position_key", "")
    account = decision.get("account_key", "")
    reversal = {
        "position_key": pk,
        "account_key": account,
        "action": "REDUCE",
        "reason": f"HAIKU_REVERSAL: {haiku_reason}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "haiku_agent",
    }
    await redis_client.publish("haiku_agent_reversals_tradier", json.dumps(reversal))
    await redis_client.set(f"haiku_reversal:{pk}", json.dumps(reversal), ex=600)
    await redis_client.set(f"haiku_cooldown:{pk}", "1", ex=COOLDOWN_SECONDS)
    logger.warning(f"REVERSED TRADIER {decision.get('action','')} on {pk}: {haiku_reason}")


async def scan_decisions(redis_client: aioredis.Redis):
    """Scan decision JSONL files for new entries and judge them."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    files_to_scan = []
    for acct in ALL_ACCOUNTS:
        for day in [today, yesterday]:
            f = DECISION_DIR / f"decisions_{acct}_{day}.jsonl"
            if f.exists():
                files_to_scan.append((acct, f))
    new_decisions = []
    for acct, filepath in files_to_scan:
        try:
            async with aiofiles.open(filepath, "r") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    fp = _decision_fingerprint(d)
                    if fp in _processed_decisions:
                        continue
                    _processed_decisions.add(fp)
                    # Only judge recent decisions (last 30s)
                    ts_str = d.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        age = (datetime.now(timezone.utc) - ts).total_seconds()
                        if age > 30:
                            continue
                    except Exception:
                        continue
                    new_decisions.append(d)
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
    for d in new_decisions:
        pk = d.get("position_key", "")
        action = d.get("action", "")
        # Skip actions we never reverse
        if any(skip in action.upper() for skip in ["CLOSE", "REDUCE", "WEAK_REDUCE", "PROFIT_TAKE"]):
            continue
        if d.get("extra", {}).get("is_hedge"):
            continue
        reason = d.get("reason") or d.get("reason_text", "")
        if any(skip in reason.upper() for skip in ["REENTRY", "DC_BREAKOUT", "FORCE", "HAIKU"]):
            continue
        # Check cooldown
        cd = await redis_client.get(f"haiku_cooldown:{pk}")
        if cd:
            continue
        # Ask Haiku
        prompt = build_judgement_prompt(d)
        verdict = await call_haiku(prompt)
        if not verdict:
            continue
        v = verdict.get("verdict", "OK").upper()
        conf = safe_fetch_float(verdict.get("confidence", 0), 0)
        reason_text = verdict.get("reason", "no reason")
        logger.info(f"HAIKU VERDICT for {pk} {action}: {v} (conf={conf:.2f}) — {reason_text}")
        if v == "REVERSE" and conf >= 0.75:
            is_tradier = any(pk.startswith(f"{ta}:") for ta in TRADIER_ACCOUNTS)
            if is_tradier:
                await reverse_tradier_trade(redis_client, d, reason_text)
            else:
                await reverse_crypto_trade(redis_client, d, reason_text)
            # Record the reversal as a decision too
            reversal_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "position_key": pk,
                "account": d.get("account") or d.get("account_key"),
                "action": "HAIKU_REVERSAL",
                "reason": f"Reversed {action}: {reason_text} (conf={conf:.2f})",
                "snapshot": d.get("snapshot") or d.get("indicators") or {},
                "extra": {"original_action": action, "original_reason": reason, "haiku_confidence": conf},
            }
            acct = d.get("account") or d.get("account_key", "unknown")
            log_path = DECISION_DIR / f"decisions_{acct}_{today}.jsonl"
            async with aiofiles.open(log_path, "a") as f:
                await f.write(json.dumps(reversal_record, default=str) + "\n")


async def manage_winner_positions(redis_client: aioredis.Redis):
    """Full winner lifecycle: augment >3%, reduce if <2.5%, re-enter if back above reduction level."""
    now = time.time()
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for acct in CRYPTO_ACCOUNTS:
        for side_file in ["long_positions.json", "short_positions.json"]:
            pos_path = Path(f"/home/niels/binance/{acct}/{side_file}")
            if not pos_path.exists():
                continue
            try:
                async with aiofiles.open(pos_path, "r") as f:
                    content = await f.read()
                positions = json.loads(content)
            except Exception:
                continue
            for pk, pos in positions.items():
                gain = safe_fetch_float(pos.get("gain", 0), 0)
                pos_amt = abs(safe_fetch_float(pos.get("positionAmt", 0), 0))
                entry_price = safe_fetch_float(pos.get("entryPrice") or pos.get("entry_price", 0), 0)
                mark_price = safe_fetch_float(pos.get("markPrice") or pos.get("mark_price", 0), 0)
                if entry_price <= 0 or mark_price <= 0 or pos_amt <= 0:
                    continue
                current_value = pos_amt * mark_price
                if current_value < MIN_POSITION_VALUE:
                    continue
                managed = _haiku_managed.get(pk)
                # === PHASE 1: AUGMENT if gain > 3% (10% per candle) ===
                if gain >= AUGMENT_GAIN_THRESHOLD:
                    cd_key = f"haiku_aug:{pk}"
                    if now - _augment_cooldowns.get(pk, 0) < AUGMENT_INTERVAL:
                        continue
                    cd = await redis_client.get(cd_key)
                    if cd:
                        continue
                    aug_qty = pos_amt * AUGMENT_FRACTION
                    aug_value = aug_qty * mark_price
                    if aug_value < 1.0:
                        continue
                    augment_cmd = {"position_key": pk, "account_key": acct, "action": "AUGMENT", "reason": f"HAIKU_WINNER_AUG_{gain:.1f}pct", "price": mark_price, "qty": aug_qty, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "haiku_agent"}
                    await redis_client.publish("haiku_agent_augments", json.dumps(augment_cmd))
                    await redis_client.set(cd_key, "1", ex=AUGMENT_INTERVAL)
                    _augment_cooldowns[pk] = now
                    # Track what we augmented
                    if pk not in _haiku_managed:
                        _haiku_managed[pk] = {"augmented_at_gain": gain, "total_augmented_qty": 0.0, "reduced": False, "reduce_price": 0.0}
                    _haiku_managed[pk]["total_augmented_qty"] += aug_qty
                    _haiku_managed[pk]["augmented_at_gain"] = gain
                    logger.info(f"AUGMENT WINNER {pk}: gain={gain:.2f}%, adding {aug_qty:.4f} ({aug_value:.2f} USDT), total_aug={_haiku_managed[pk]['total_augmented_qty']:.4f}")
                    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "position_key": pk, "account": acct, "action": "HAIKU_AUGMENT", "reason": f"Winner augment: {gain:.1f}% > {AUGMENT_GAIN_THRESHOLD}%, adding 10%", "snapshot": {"price": mark_price, "gain": gain, "pos_amt": pos_amt, "aug_qty": aug_qty}, "extra": {"source": "haiku_agent"}}
                    log_path = DECISION_DIR / f"decisions_{acct}_{today}.jsonl"
                    async with aiofiles.open(log_path, "a") as f:
                        await f.write(json.dumps(record, default=str) + "\n")
                # === PHASE 2: REDUCE if gain drops below 2.5% (give back augmented qty) ===
                elif gain < REDUCE_GAIN_THRESHOLD and managed and not managed.get("reduced") and managed.get("total_augmented_qty", 0) > 0:
                    reduce_qty = managed["total_augmented_qty"]
                    reduce_value = reduce_qty * mark_price
                    if reduce_value < 1.0:
                        continue
                    reduce_cmd = {"position_key": pk, "account_key": acct, "action": "REDUCE", "reason": f"HAIKU_WINNER_REDUCE_{gain:.1f}pct<{REDUCE_GAIN_THRESHOLD}pct", "price": mark_price, "qty": reduce_qty, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "haiku_agent"}
                    await redis_client.publish("haiku_agent_reversals", json.dumps(reduce_cmd))
                    managed["reduced"] = True
                    managed["reduce_price"] = mark_price
                    logger.warning(f"REDUCE WINNER {pk}: gain dropped to {gain:.2f}% < {REDUCE_GAIN_THRESHOLD}%, reducing {reduce_qty:.4f} ({reduce_value:.2f} USDT)")
                    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "position_key": pk, "account": acct, "action": "HAIKU_REDUCE", "reason": f"Winner reduce: {gain:.1f}% < {REDUCE_GAIN_THRESHOLD}%, removing augmented qty", "snapshot": {"price": mark_price, "gain": gain, "pos_amt": pos_amt, "reduce_qty": reduce_qty}, "extra": {"source": "haiku_agent"}}
                    log_path = DECISION_DIR / f"decisions_{acct}_{today}.jsonl"
                    async with aiofiles.open(log_path, "a") as f:
                        await f.write(json.dumps(record, default=str) + "\n")
                # === PHASE 3: RE-ENTER if gain comes back above 3% after reduction ===
                elif gain >= AUGMENT_GAIN_THRESHOLD and managed and managed.get("reduced") and managed.get("total_augmented_qty", 0) > 0:
                    cd_key = f"haiku_aug:{pk}"
                    if now - _augment_cooldowns.get(pk, 0) < AUGMENT_INTERVAL:
                        continue
                    cd = await redis_client.get(cd_key)
                    if cd:
                        continue
                    reenter_qty = managed["total_augmented_qty"]
                    reenter_value = reenter_qty * mark_price
                    if reenter_value < 1.0:
                        continue
                    reenter_cmd = {"position_key": pk, "account_key": acct, "action": "AUGMENT", "reason": f"HAIKU_REENTER_{gain:.1f}pct_above_reduce", "price": mark_price, "qty": reenter_qty, "timestamp": datetime.now(timezone.utc).isoformat(), "source": "haiku_agent"}
                    await redis_client.publish("haiku_agent_augments", json.dumps(reenter_cmd))
                    await redis_client.set(cd_key, "1", ex=AUGMENT_INTERVAL)
                    _augment_cooldowns[pk] = now
                    managed["reduced"] = False
                    logger.info(f"RE-ENTER WINNER {pk}: gain={gain:.2f}% back above {AUGMENT_GAIN_THRESHOLD}%, re-adding {reenter_qty:.4f} ({reenter_value:.2f} USDT)")
                    record = {"timestamp": datetime.now(timezone.utc).isoformat(), "position_key": pk, "account": acct, "action": "HAIKU_REENTER", "reason": f"Winner re-entry: {gain:.1f}% back above {AUGMENT_GAIN_THRESHOLD}%", "snapshot": {"price": mark_price, "gain": gain, "pos_amt": pos_amt, "reenter_qty": reenter_qty}, "extra": {"source": "haiku_agent"}}
                    log_path = DECISION_DIR / f"decisions_{acct}_{today}.jsonl"
                    async with aiofiles.open(log_path, "a") as f:
                        await f.write(json.dumps(record, default=str) + "\n")
    # Cleanup: remove managed entries for positions that no longer exist
    stale_keys = [pk for pk in _haiku_managed if not any(pk.startswith(f"{a}:") for a in CRYPTO_ACCOUNTS)]
    for k in stale_keys:
        del _haiku_managed[k]


async def main():
    logger.info("=" * 60)
    logger.info("HAIKU OVERSIGHT AGENT STARTING")
    logger.info(f"Monitoring accounts: {ALL_ACCOUNTS}")
    logger.info(f"Decision dir: {DECISION_DIR}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s | Augment interval: {AUGMENT_INTERVAL}s")
    logger.info(f"Augment threshold: >{AUGMENT_GAIN_THRESHOLD}% gain, fraction: {AUGMENT_FRACTION*100}%")
    logger.info("=" * 60)
    redis_client = await get_redis()
    # Pre-populate processed decisions so we don't judge old ones on startup
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for acct in ALL_ACCOUNTS:
        f = DECISION_DIR / f"decisions_{acct}_{today}.jsonl"
        if f.exists():
            try:
                async with aiofiles.open(f, "r") as fh:
                    async for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                d = json.loads(line)
                                _processed_decisions.add(_decision_fingerprint(d))
                            except Exception:
                                pass
            except Exception:
                pass
    logger.info(f"Pre-loaded {len(_processed_decisions)} existing decisions (will not re-judge)")
    last_augment_check = 0
    while True:
        try:
            await scan_decisions(redis_client)
            now = time.time()
            if now - last_augment_check >= AUGMENT_INTERVAL:
                await manage_winner_positions(redis_client)
                last_augment_check = now
        except Exception as e:
            logger.error(f"Main loop error: {e}", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
