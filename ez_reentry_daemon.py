#!/usr/bin/env python3
"""
ez_reentry_daemon.py — Standalone 24/7 reentry shadow + watchdog process.

Runs OUTSIDE the per-account ez_manage workers so a worker crash does not
take reentry observability with it. Phase 1 role:

  1. Heartbeat — write a Redis key every 30s under ``ez_reentry:heartbeat:daemon``
     so external watchdogs (and ez_manage's future fallback) can detect a stall.

  2. Shadow evaluator — for each account in ACCOUNT_KEYS that still has live
     reentry_data on disk (account dir's long_reentry.json / short_reentry.json),
     emit a `reentry_signal:<account>:<position_key>` Redis key with a brief
     payload (timestamp, exit price, last-known mark price, suggested action).
     This signal is informational in Phase 1 — ez_manage does not consume it
     yet. It exists so we can A/B compare daemon vs inline decisions.

  3. Loop liveness audit — every 60s, audit Redis for the in-process loop
     heartbeats (ez_manage's `reentry_loop_heartbeat:*` if/when ez_manage
     starts publishing them). Logs warnings when a loop hasn't ticked in 5min.

The daemon NEVER places orders. Order execution stays in ez_manage's
execute_now path per CLAUDE.md ("execute_now is the ONLY gate"). When the user
flips ``EZ_REENTRY_INLINE_ENABLED=False``, an upgrade path (Phase 2) will let
the daemon publish to a Redis command channel that ez_manage subscribes to —
but that wiring is intentionally out of scope here.

CLI:
  python3 ez_reentry_daemon.py             # default: all crypto accounts
  python3 ez_reentry_daemon.py --once      # one pass then exit (smoke test)

Environment:
  EZ_REENTRY_DAEMON_INTERVAL_S — override loop interval (default 30s)
  REDIS_HOST / REDIS_PORT       — Redis target (defaults localhost:6379)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

LOG_PATH = Path("/Users/niels/logs/ez_reentry_daemon.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ez_reentry_daemon")


def _get_redis():
    try:
        import redis
        host = os.environ.get("REDIS_HOST", "localhost")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        client = redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as e:
        logger.warning(f"redis unavailable ({e}); daemon will run heartbeat-less")
        return None


def _accounts_from_config() -> list[str]:
    fallback = ["ang", "inf", "flz", "men", "fin"]
    try:
        import config as cfg
        ks = list(getattr(cfg, "ACCOUNT_KEYS", []) or [])
        if ks:
            return [k for k in ks if isinstance(k, str)]
        cls = getattr(cfg, "Config", None)
        if cls is not None:
            inst = cls()
            ks = list(getattr(inst, "ACCOUNT_KEYS", []) or [])
            if ks:
                return [k for k in ks if isinstance(k, str)]
    except Exception:
        pass
    return fallback


def _load_reentry_file(path: Path) -> dict:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.debug(f"read {path}: {e}")
        return {}


def _shadow_publish(redis_client, account: str, side: str, position_key: str, entry: dict, ttl: int = 600) -> None:
    if redis_client is None:
        return
    payload = {
        "account": account,
        "side": side,
        "position_key": position_key,
        "reentry_level": entry.get("reentry_level") or entry.get("exit_price"),
        "reentry_amount": entry.get("reentry_amount") or 0,
        "exit_reason": entry.get("reason") or entry.get("exit_reason"),
        "exit_timestamp": entry.get("timestamp"),
        "observed_at": int(time.time()),
        "source": "ez_reentry_daemon_shadow",
    }
    try:
        redis_client.set(
            f"reentry_signal:{account}:{position_key}",
            json.dumps(payload),
            ex=ttl,
        )
    except Exception as e:
        logger.debug(f"redis set fail {position_key}: {e}")


def _scan_account(redis_client, base_path: Path, account: str) -> int:
    acc_dir = base_path / account
    if not acc_dir.exists():
        return 0
    total = 0
    for side in ("long", "short"):
        f = acc_dir / f"{side}_reentry.json"
        data = _load_reentry_file(f)
        for pk, entry in data.items():
            if not isinstance(entry, dict):
                continue
            try:
                amt = float(entry.get("reentry_amount", 0) or 0)
            except (TypeError, ValueError):
                amt = 0.0
            if amt <= 0:
                continue
            _shadow_publish(redis_client, account, side, pk, entry)
            total += 1
    return total


def _audit_inline_loops(redis_client) -> None:
    if redis_client is None:
        return
    try:
        keys = list(redis_client.scan_iter("reentry_loop_heartbeat:*"))
    except Exception:
        return
    now = time.time()
    for k in keys:
        try:
            ts = float(redis_client.get(k) or 0)
        except (TypeError, ValueError):
            continue
        age = now - ts
        if age > 300:
            logger.warning(f"loop heartbeat stale: {k} age={age:.0f}s (>5min)")


async def run_loop(once: bool = False) -> None:
    redis_client = _get_redis()
    base_path = Path(os.environ.get("BINANCE_BASE_PATH", "/Users/niels/Documents/binance"))
    interval = int(os.environ.get("EZ_REENTRY_DAEMON_INTERVAL_S", "30"))
    accounts = _accounts_from_config()
    logger.info(f"started; accounts={accounts} interval={interval}s once={once} log={LOG_PATH}")
    while True:
        cycle_start = time.time()
        try:
            import ez_reentry as _ezr
            _ezr.heartbeat(redis_client, role="daemon", ttl=max(interval * 3, 90))
            if not _ezr.daemon_enabled():
                logger.info("daemon disabled via EZ_REENTRY_DAEMON_ENABLED=False — sleeping")
                await asyncio.sleep(interval)
                if once:
                    return
                continue
            total = 0
            for acc in accounts:
                try:
                    total += _scan_account(redis_client, base_path, acc)
                except Exception as e:
                    logger.error(f"scan {acc}: {e}", exc_info=True)
            _audit_inline_loops(redis_client)
            elapsed = time.time() - cycle_start
            logger.info(f"shadow pass: {total} reentry slots observed across {len(accounts)} accounts in {elapsed:.2f}s")
        except Exception as e:
            logger.error(f"loop error: {e}", exc_info=True)
        if once:
            return
        sleep_for = max(0.1, interval - (time.time() - cycle_start))
        await asyncio.sleep(sleep_for)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run a single pass then exit")
    args = parser.parse_args()
    try:
        asyncio.run(run_loop(once=args.once))
    except KeyboardInterrupt:
        logger.info("interrupted, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
