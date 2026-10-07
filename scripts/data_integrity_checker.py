#!/home/niels/.conda/envs/binance_env/bin/python3
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from config import Config
from utils import get_simple_redis_manager, ensure_tz

logger = logging.getLogger("data_integrity_checker")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(handler)

SKEW_THRESHOLD_SECONDS = 1.0


def _decode(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        return json.loads(raw)
    except Exception:
        return None


def _extract_timestamp(payload: Any) -> Optional[datetime]:
    if not isinstance(payload, (dict, list)):
        return None
    if isinstance(payload, list):
        timestamps = [_extract_timestamp(item) for item in payload]
        timestamps = [ts for ts in timestamps if ts]
        return max(timestamps) if timestamps else None
    for key in ("timestamp", "generated_at", "updated_at", "last_updated", "time", "ts"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            divisor = 1000.0 if value > 1e12 else 1.0
            return datetime.fromtimestamp(value / divisor, tz=timezone.utc)
        if isinstance(value, str):
            try:
                if value.endswith("Z"):
                    return ensure_tz(datetime.fromisoformat(value.replace("Z", "+00:00")))
                return ensure_tz(datetime.fromisoformat(value))
            except Exception:
                continue
    nested_candidates = []
    for nested in payload.values():
        nested_ts = _extract_timestamp(nested) if isinstance(nested, (dict, list)) else None
        if nested_ts:
            nested_candidates.append(nested_ts)
    return max(nested_candidates) if nested_candidates else None


def _positions_file_paths(base_path: Path, account_key: str) -> Tuple[Path, Path]:
    account_dir = base_path / account_key
    return account_dir / "long_positions.json", account_dir / "short_positions.json"


def _load_positions_timestamp(file_path: Path) -> Optional[datetime]:
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    timestamps = []
    for entry in payload.values():
        if isinstance(entry, dict):
            ts = _extract_timestamp(entry)
            if ts:
                timestamps.append(ts)
    file_ts = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
    timestamps.append(file_ts)
    return max(timestamps) if timestamps else file_ts


def _delta_seconds(reference: Optional[datetime], candidate: Optional[datetime]) -> Optional[float]:
    if not reference or not candidate:
        return None
    return abs((candidate - reference).total_seconds())


def _split_positions_by_side(account_key: str, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    long_entries: Dict[str, Any] = {}
    short_entries: Dict[str, Any] = {}
    prefix = f"{account_key}:"
    for position_key, position_payload in payload.items():
        if not isinstance(position_key, str) or not position_key.startswith(prefix):
            continue
        key_upper = position_key.upper()
        target = None
        if key_upper.endswith("_LONG"):
            target = long_entries
        elif key_upper.endswith("_SHORT"):
            target = short_entries
        if target is not None:
            target[position_key] = position_payload
    return long_entries, short_entries


async def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"), ensure_ascii=False)
    tmp.replace(path)


async def ensure_market_data(config: Config, redis_manager) -> None:
    latest_file = Path(config.LATEST_MARKET_DATA_FILE)
    file_payload: Optional[Dict[str, Any]] = None
    file_ts: Optional[datetime] = None
    if latest_file.exists():
        with open(latest_file, "r", encoding="utf-8") as handle:
            try:
                file_payload = json.load(handle) or {}
            except Exception:
                file_payload = {}
        file_ts = _extract_timestamp(file_payload) or datetime.fromtimestamp(latest_file.stat().st_mtime, tz=timezone.utc)
    raw = await redis_manager.get(config.REDIS_KEY_MARKET_DATA)
    redis_payload = _decode(raw) or {}
    redis_ts = _extract_timestamp(redis_payload)

    if file_payload is None and not redis_payload:
        logger.warning("No market data available in file or redis; nothing to push.")
        return

    if file_payload is None and redis_payload:
        await _write_json(latest_file, redis_payload)
        logger.info("Market data file restored from redis snapshot.")
        return

    if redis_ts is None and file_payload:
        await redis_manager.set(config.REDIS_KEY_MARKET_DATA, json.dumps(file_payload))
        logger.info("Redis market data backfilled from file (missing timestamp).")
        return

    if not file_payload:
        await redis_manager.set(config.REDIS_KEY_MARKET_DATA, json.dumps(redis_payload))
        logger.info("Redis market data ensured; file empty.")
        return

    file_ts = file_ts or datetime.now(timezone.utc)
    skew = _delta_seconds(file_ts, redis_ts) if redis_ts else None
    if skew is None or skew <= SKEW_THRESHOLD_SECONDS:
        if redis_ts is None or file_ts >= (redis_ts or file_ts):
            await redis_manager.set(config.REDIS_KEY_MARKET_DATA, json.dumps(file_payload))
        else:
            await _write_json(latest_file, redis_payload)
        return

    if redis_ts and redis_ts > file_ts:
        await _write_json(latest_file, redis_payload)
        logger.info(f"Market data file updated from redis (skew {skew:.3f}s).")
    else:
        await redis_manager.set(config.REDIS_KEY_MARKET_DATA, json.dumps(file_payload))
        logger.info(f"Redis market data refreshed from file (skew {skew:.3f}s).")


async def ensure_positions(config: Config, redis_manager) -> None:
    base_path = Path(config.BASE_PATH)
    accounts = getattr(config, "ACCOUNT_KEYS", [])
    for account_key in accounts:
        long_path, short_path = _positions_file_paths(base_path, account_key)
        file_payload: Dict[str, Dict[str, Any]] = {}
        file_ts_candidates: list[datetime] = []
        for path in (long_path, short_path):
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as handle:
                        content = json.load(handle) or {}
                        if isinstance(content, dict):
                            file_payload.update(content)
                    ts = _load_positions_timestamp(path)
                    if ts:
                        file_ts_candidates.append(ts)
                except Exception:
                    continue
        file_ts = max(file_ts_candidates) if file_ts_candidates else None
        redis_key = f"positions:{account_key}"
        raw = await redis_manager.get(redis_key)
        redis_payload = _decode(raw) or {}
        redis_ts = _extract_timestamp(redis_payload)

        if not redis_payload and not file_payload:
            continue

        if not redis_payload and file_payload:
            await redis_manager.set(redis_key, json.dumps(file_payload))
            logger.info(f"Redis positions backfilled for {account_key} from files.")
            continue

        if not file_payload and redis_payload:
            long_entries, short_entries = _split_positions_by_side(account_key, redis_payload)
            await _write_json(long_path, long_entries)
            await _write_json(short_path, short_entries)
            logger.info(f"Position files rebuilt from redis for {account_key}.")
            continue

        skew = _delta_seconds(file_ts, redis_ts) if (file_ts and redis_ts) else None
        if skew is None or skew <= SKEW_THRESHOLD_SECONDS:
            continue

        if redis_ts and (not file_ts or redis_ts > file_ts):
            long_entries, short_entries = _split_positions_by_side(account_key, redis_payload)
            await _write_json(long_path, long_entries)
            await _write_json(short_path, short_entries)
            logger.info(f"Position files refreshed from redis for {account_key} (skew {skew:.3f}s).")
        else:
            await redis_manager.set(redis_key, json.dumps(file_payload))
            logger.info(f"Redis positions refreshed from files for {account_key} (skew {skew:.3f}s).")


async def run_checks() -> int:
    config = Config()
    redis_manager = await get_simple_redis_manager()
    if not redis_manager or not getattr(redis_manager, "_initialized", False):
        logger.error("Redis manager not available")
        return 1
    try:
        await ensure_market_data(config, redis_manager)
        await ensure_positions(config, redis_manager)
    finally:
        connections = getattr(redis_manager, "connections", {}) or {}
        close_tasks = [conn.close() for conn in connections.values() if hasattr(conn, "close")]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(run_checks())
    sys.exit(exit_code)
