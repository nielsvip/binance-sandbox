#!/usr/bin/env python3
"""
ez_reentry_daemon.py — Standalone reentry executor process (Phase 2, 2026-05-08).

Runs OUTSIDE ez_manage.py so reentry execution is fully independent from the
trading loop.  Kill this process to stop all daemon-sourced reentries while
trading continues uninterrupted.

Architecture:
  This daemon evaluates price-cross reentry candidates from disk JSON files
  (account/long_reentry.json, account/short_reentry.json) using Redis for
  live prices and WT indicators, then writes command files to
  data/reentry_queue/{account}/.

  ez_manage.py has a companion _reentry_queue_consumer_loop that reads those
  command files and calls execute_now (the ONLY order gate per CLAUDE.md).

  The split ensures:
    kill daemon         → no new commands → reentries stop, trading continues
    touch /tmp/REENTRY_DAEMON_HOLD → daemon pauses evaluation (stays alive)
    EZ_REENTRY_INLINE_ENABLED=False → disable fallback inline loops in ez_manage
    kill ez_manage      → queued commands persist for next restart

CLI:
  python3 ez_reentry_daemon.py             # 24/7 mode, all crypto accounts
  python3 ez_reentry_daemon.py --once      # one pass then exit (smoke test)
  python3 ez_reentry_daemon.py --dry-run   # evaluate but do NOT write commands

Sentinel files (touch to activate, rm to release):
  /tmp/REENTRY_DAEMON_HOLD       — pause all evaluation
  /tmp/REENTRY_HOLD_{account}    — pause evaluation for one account

Environment:
  EZ_REENTRY_DAEMON_INTERVAL_S — loop interval (default 5s)
  REDIS_HOST / REDIS_PORT       — Redis target (defaults localhost:6379)
  BINANCE_BASE_PATH             — override base path
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG_PATH = Path.home() / "logs" / "ez_reentry_daemon.log"
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

_HOLD_GLOBAL = Path("/tmp/REENTRY_DAEMON_HOLD")
_CMD_TTL_S = 10.0 * 365.0 * 86400.0  # command files expire after 10 years (effectively infinite)
_MIN_GAP_S = 60.0   # per-position dedup: don't re-queue within 60s
_MAX_FIRES_PER_TICK = 20
_SOFT_EXIT_RE = None  # compiled on first use


def _get_soft_exit_re():
    global _SOFT_EXIT_RE
    if _SOFT_EXIT_RE is None:
        import re
        _SOFT_EXIT_RE = re.compile(r"WT_CROSS_EXIT|DELTA_EXIT|E_1_WT_DELTA_EXIT|STRUCT_LH3M|STRUCT_HL3M|Lower_High|Higher_Low", re.IGNORECASE)
    return _SOFT_EXIT_RE


def _get_redis():
    try:
        import redis
        host = os.environ.get("REDIS_HOST", "localhost")
        port = int(os.environ.get("REDIS_PORT", "6379"))
        client = redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=2.0)
        client.ping()
        return client
    except Exception as e:
        logger.warning(f"redis unavailable ({e}); running without Redis (file-only mode)")
        return None


def _accounts_from_config() -> List[str]:
    fallback = ["ang", "inf", "flz", "men", "fin"]
    try:
        import config as cfg
        ks = list(getattr(cfg, "ACCOUNT_KEYS", []) or [])
        if ks:
            return [k for k in ks if isinstance(k, str)]
        cls = getattr(cfg, "Config", None)
        if cls is not None:
            ks = list(getattr(cls(), "ACCOUNT_KEYS", []) or [])
            if ks:
                return [k for k in ks if isinstance(k, str)]
    except Exception:
        pass
    return fallback


def _cfg_float(attr: str, default: float) -> float:
    try:
        import config as cfg
        return float(getattr(cfg, attr, default))
    except Exception:
        return default


def _cfg_bool(attr: str, default: bool) -> bool:
    try:
        import config as cfg
        return bool(getattr(cfg, attr, default))
    except Exception:
        return default


def _cfg_str(attr: str, default: str) -> str:
    try: import config as cfg; return str(getattr(cfg, attr, default))
    except Exception: return default


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


def _ts_to_epoch(v) -> float:
    if not v:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _get_positions_from_redis(redis_client, account_key: str) -> Dict[str, float]:
    """Returns {position_key: positionAmt} for all positions in account."""
    if redis_client is None:
        return {}
    try:
        raw = redis_client.get(f"positions:{account_key}")
        if not raw:
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        result = {}
        for pk, pos in data.items():
            if isinstance(pos, dict):
                try:
                    result[pk] = abs(float(pos.get("positionAmt", 0) or 0))
                except (TypeError, ValueError):
                    result[pk] = 0.0
        return result
    except Exception as e:
        logger.debug(f"redis positions:{account_key}: {e}")
        return {}


def _get_market_prices(redis_client, base_path: Path) -> Dict[str, float]:
    """Returns {symbol: current_price} from price_cache_1/2/3.json (live, <2s old).
    Falls back to mark_prices Redis hash if files are stale or missing."""
    result: Dict[str, float] = {}
    now = time.time()
    max_age_s = 90.0
    try:
        import config as _cfg
        cache_files = [
            getattr(_cfg, "PRICE_CACHE_FILE", base_path / "price_cache_1.json"),
            getattr(_cfg, "PRICE_CACHE_FILE_2", base_path / "price_cache_2.json"),
            getattr(_cfg, "PRICE_CACHE_FILE_3", base_path / "price_cache_3.json"),
        ]
    except Exception:
        cache_files = [base_path / f"price_cache_{i}.json" for i in (1, 2, 3)]
    for cache_file in cache_files:
        try:
            with open(cache_file, "r") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            for sym, entry in data.items():
                if sym in result:
                    continue
                if not isinstance(entry, dict):
                    continue
                try:
                    price = float(entry.get("price", 0) or 0)
                    if price <= 0:
                        continue
                    ts_str = entry.get("timestamp", "")
                    if ts_str:
                        from datetime import datetime, timezone
                        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00")).timestamp()
                        if now - ts > max_age_s:
                            continue
                    result[sym] = price
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.debug(f"price cache {cache_file}: {e}")
    if not result and redis_client is not None:
        try:
            all_pairs = redis_client.hgetall("mark_prices")
            for sym, raw in (all_pairs or {}).items():
                try:
                    obj = json.loads(raw)
                    price = float(obj.get("price", 0) or 0)
                    if price > 0:
                        result[sym] = price
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"redis mark_prices fallback: {e}")
    return result


def _get_indicators(redis_client, sym: str) -> Dict:
    """Returns indicator dict for symbol from Redis indicators:{sym}."""
    if redis_client is None or not sym:
        return {}
    try:
        raw = redis_client.get(f"indicators:{sym}")
        if not raw:
            return {}
        return json.loads(raw) or {}
    except Exception:
        return {}


def _collect_candidates(base_path: Path, accounts: List[str]) -> List[Tuple]:
    """Collect [(pk, is_long, exit_px, exit_amt, exit_ts, exit_reason)] from disk files."""
    by_key: dict = {}
    # 2026-06-03: skip NON-TRADEABLE keys at the SOURCE. The reentry files accumulate every
    # symbol the account ever held (700+/acct), but only the hand-picked tradeable_keys (~23)
    # can actually reenter — the consumer skips the rest. Queuing the 700+ each tick floods the
    # queue (ang backlog 210) and starves the real reentries. Filter here so only tradeable keys
    # are queued. Fail-OPEN: if tradeable_keys.json is missing/empty, queue everything (old behavior).
    _tradeable = set()
    try:
        _tk_path = base_path / "tradeable_keys.json"
        if _tk_path.exists():
            _tk = json.loads(_tk_path.read_text())
            if isinstance(_tk, list) and len(_tk) > 5:
                _tradeable = set(_tk)
    except Exception:
        _tradeable = set()
    for acc in accounts:
        acc_dir = base_path / acc
        if not acc_dir.exists():
            continue
        for side_name, is_long in (("long", True), ("short", False)):
            data = _load_reentry_file(acc_dir / f"{side_name}_reentry.json")
            for pk, rd in data.items():
                if not isinstance(rd, dict):
                    continue
                if _tradeable and pk not in _tradeable:
                    continue  # non-tradeable — consumer would skip anyway; don't flood the queue
                try:
                    exit_px = float(rd.get("reentry_level") or rd.get("exit_price") or 0.0)
                    exit_amt = float(rd.get("reentry_amount", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if exit_px <= 0:
                    continue
                exit_ts = _ts_to_epoch(rd.get("timestamp") or rd.get("exit_timestamp"))
                exit_reason = str(rd.get("reduction_reason") or rd.get("reason") or "")
                by_key[pk] = (is_long, exit_px, exit_amt, exit_ts, exit_reason)
    return [(pk,) + v for pk, v in by_key.items()]


def _parse_pk(pk: str) -> Tuple[str, str, str]:
    """Returns (account_key, symbol, position_side) from 'acc:SYM_LONG'."""
    parts = pk.split(":", 1)
    account_key = parts[0] if len(parts) == 2 else ""
    rest = parts[1] if len(parts) == 2 else pk
    sym_parts = rest.split("_", 1)
    symbol = sym_parts[0]
    side = sym_parts[1] if len(sym_parts) == 2 else "LONG"
    return account_key, symbol, side


def _write_reentry_command(queue_dir: Path, pk: str, account_key: str, symbol: str, pos_side: str, qty: float, exit_px: float, cur_px: float, sizing_tag: str, reason: str, now: float) -> bool:
    """Write a reentry command file. Returns True on success."""
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
        done_dir = queue_dir / "done"
        done_dir.mkdir(exist_ok=True)
        uid = f"DAEMON_PRICE_CROSS_{int(now * 1000)}_{pk.replace(':', '_')}"
        cmd = {
            "version": 2,
            "created_at": now,
            "expires_at": now + _CMD_TTL_S,
            "position_key": pk,
            "account_key": account_key,
            "symbol": symbol,
            "side": "BUY" if pos_side == "LONG" else "SELL",
            "position_side": pos_side,
            "quantity": qty,
            "exit_price": exit_px,
            "current_price": cur_px,
            "sizing_tag": sizing_tag,
            "reason": reason,
            "unique_id": uid,
            "source": "ez_reentry_daemon_v2",
        }
        pk_safe = pk.replace(":", "_").replace("/", "_")
        fname = f"cmd_{int(now * 1000)}_{pk_safe}.json"
        tmp_path = queue_dir / f".tmp_{fname}"
        with open(tmp_path, "w") as f:
            json.dump(cmd, f)
        tmp_path.rename(queue_dir / fname)
        return True
    except Exception as e:
        logger.error(f"[DAEMON] write command failed for {pk}: {e}")
        return False


def _evaluate_and_queue(redis_client, base_path: Path, queue_base: Path, accounts: List[str], last_fire: Dict[str, float], dry_run: bool) -> int:
    """Main evaluation loop: find price-cross candidates and queue reentry commands."""
    if _HOLD_GLOBAL.exists():
        logger.debug("[DAEMON] REENTRY_DAEMON_HOLD active — skipping evaluation")
        return 0
    if not _cfg_bool("EZ_REENTRY_DAEMON_ENABLED", True):
        return 0
    min_gap_s = _cfg_float("EZ_REENTRY_PRICE_CROSS_MIN_GAP_S", _MIN_GAP_S)
    cross_pct = _cfg_float("EZ_REENTRY_PRICE_CROSS_PCT", 0.0)
    max_fires = int(_cfg_float("EZ_REENTRY_PRICE_CROSS_MAX_FIRES_PER_TICK", _MAX_FIRES_PER_TICK))
    start_size = _cfg_float("START_POSITION_SIZE", 18.0)
    now = time.time()
    prices = _get_market_prices(redis_client, base_path)
    candidates = _collect_candidates(base_path, accounts)
    if not candidates:
        return 0
    fired = 0
    for pk, is_long, exit_px, exit_amt, exit_ts, exit_reason in candidates:
        if fired >= max_fires:
            break
        account_key, symbol, pos_side = _parse_pk(pk)
        if not account_key or not symbol:
            continue
        hold_path = Path(f"/tmp/REENTRY_HOLD_{account_key}")
        if hold_path.exists():
            logger.debug(f"[DAEMON] {account_key} REENTRY_HOLD active — skipping")
            continue
        last = last_fire.get(pk, 0.0)
        if now - last < min_gap_s:
            continue
        cur_px = prices.get(symbol, 0.0)
        if cur_px <= 0:
            continue
        _ind = None
        if _cfg_bool("REENTRY_CONFIRMATION_GATES_ENABLED", True) or _cfg_bool("REENTRY2_DC_BREAK_ENABLED", True):
            _ind = _get_indicators(redis_client, symbol)
        if cross_pct > 0:
            crossed = (is_long and cur_px > exit_px * (1.0 + cross_pct)) or (not is_long and cur_px < exit_px * (1.0 - cross_pct))
        else:
            crossed = (is_long and cur_px > exit_px) or (not is_long and cur_px < exit_px)
        is_dc_breakout = False
        dc_tf_used = ""
        if not crossed and _cfg_bool("REENTRY2_DC_BREAK_ENABLED", True) and _ind:
            _buf = 0.001
            _sf = lambda val, d=0.0: float(val) if val is not None else d
            _dc_allow_15m_re = _cfg_bool("REENTRY2_DC_BREAK_ALLOW_15M", True)
            _dc_req_k_re = _cfg_bool("REENTRY2_DC_BREAK_REQUIRE_K_FILTER", True)
            _dc_req_wt_re = _cfg_bool("REENTRY2_DC_BREAK_REQUIRE_WT_FILTER", False)
            _dc_ftf_re = _cfg_str("REENTRY2_DC_BREAK_FILTER_TF", "3m")
            _dc_fk_re = _sf(_ind.get(f"stoch_k_{_dc_ftf_re}", 0), 0.0)
            _dc_fd_re = _sf(_ind.get(f"stoch_d_{_dc_ftf_re}", 0), 0.0)
            _dc_fw1_re = _sf(_ind.get(f"wt1_{_dc_ftf_re}", 0), 0.0)
            _dc_fw2_re = _sf(_ind.get(f"wt2_{_dc_ftf_re}", 0), 0.0)
            _dc_kdata_re = abs(_dc_fk_re) > 1e-9 or abs(_dc_fd_re) > 1e-9
            _dc_wtdata_re = abs(_dc_fw1_re) > 1e-9 or abs(_dc_fw2_re) > 1e-9
            dc_high_3m_re = _sf(_ind.get("dc_high_3m", 0), 0.0)
            dc_low_3m_re = _sf(_ind.get("dc_low_3m", 0), 0.0)
            dc_high_1h = _sf(_ind.get("dc_high_1h", 0), 0.0)
            dc_low_1h = _sf(_ind.get("dc_low_1h", 0), 0.0)
            dc_high_15m = _sf(_ind.get("dc_high_15m", 0), 0.0)
            dc_low_15m = _sf(_ind.get("dc_low_15m", 0), 0.0)
            if is_long:
                _dc_3m_long_re = dc_high_3m_re > 0 and cur_px > dc_high_3m_re * (1 + _buf)
                _dc_1h15_long_re = (dc_high_1h > 0 and cur_px > dc_high_1h * (1 + _buf)) or (_dc_allow_15m_re and dc_high_15m > 0 and cur_px > dc_high_15m * (1 + _buf))
                if _dc_3m_long_re or (_dc_1h15_long_re and ((_dc_fk_re > _dc_fd_re) if (_dc_req_k_re and _dc_kdata_re) else True) and ((_dc_fw1_re > _dc_fw2_re) if (_dc_req_wt_re and _dc_wtdata_re) else True)):
                    is_dc_breakout = True
                    dc_tf_used = "3M" if _dc_3m_long_re else ("1H" if (dc_high_1h > 0 and cur_px > dc_high_1h * (1 + _buf)) else "15M")
            else:
                _dc_3m_short_re = dc_low_3m_re > 0 and cur_px < dc_low_3m_re * (1 - _buf)
                _dc_1h15_short_re = (dc_low_1h > 0 and cur_px < dc_low_1h * (1 - _buf)) or (_dc_allow_15m_re and dc_low_15m > 0 and cur_px < dc_low_15m * (1 - _buf))
                if _dc_3m_short_re or (_dc_1h15_short_re and ((_dc_fk_re < _dc_fd_re) if (_dc_req_k_re and _dc_kdata_re) else True) and ((_dc_fw1_re < _dc_fw2_re) if (_dc_req_wt_re and _dc_wtdata_re) else True)):
                    is_dc_breakout = True
                    dc_tf_used = "3M" if _dc_3m_short_re else ("1H" if (dc_low_1h > 0 and cur_px < dc_low_1h * (1 - _buf)) else "15M")
        if not crossed and not is_dc_breakout:
            continue
        # CHURN GUARD (user 2026-06-02): for the first REENTRY_CHURN_GUARD_WINDOW_S after exit,
        # a bare exit-price cross-back is NOT enough — require a real Donchian breakout
        # (dc_high4_3m/dc_low4_3m 4-bar by default, or dc_high_3m/dc_low_3m 1-bar via _USE_4BAR)
        # so the daemon stops re-buying tiny cross-backs (the churn). After the window the
        # exit-price cross fires as before. Flip _USE_4BAR to A/B which churns less.
        if _cfg_bool("REENTRY_CHURN_GUARD_ENABLED", True) and exit_ts > 0 and (now - exit_ts) < _cfg_float("REENTRY_CHURN_GUARD_WINDOW_S", 3600.0):
            _cg_4bar = _cfg_bool("REENTRY_CHURN_GUARD_USE_4BAR", True)
            _cg_lvl = 0.0
            if _ind:
                _cg_key = ("dc_high4_3m" if _cg_4bar else "dc_high_3m") if is_long else ("dc_low4_3m" if _cg_4bar else "dc_low_3m")
                try:
                    _cg_lvl = float(_ind.get(_cg_key, 0) or 0)
                except Exception:
                    _cg_lvl = 0.0
            # FAIL-OPEN: only ENFORCE the churn-guard when we actually have the dc level. If the
            # field is missing (lvl<=0, e.g. dc_high4_3m not in Redis), DO NOT block — otherwise a
            # missing indicator silently kills EVERY reentry inside the window. (2026-06-03 regression fix.)
            if _cg_lvl > 0:
                _cg_ok = (cur_px > _cg_lvl * 1.001) if is_long else (cur_px < _cg_lvl * 0.999)
                if not _cg_ok:
                    logger.info(f"[DAEMON] CHURN_GUARD blocked {pk}: {now - exit_ts:.0f}s since exit (<{_cfg_float('REENTRY_CHURN_GUARD_WINDOW_S', 3600.0):.0f}s), needs {_cg_key} breakout (cur={cur_px:g} lvl={_cg_lvl:g})")
                    continue
        _gate_reason_tag = "CONFIRM_DISABLED"
        if not is_dc_breakout and _cfg_bool("REENTRY_CONFIRMATION_GATES_ENABLED", True) and _ind:
            from ez_reentry import check_reentry_confirmation as _chk_re
            _gate_ok, _gate_reason = _chk_re(_ind, is_long)
            if not _gate_ok:
                logger.info(f"[DAEMON] Reentry gate BLOCKED {pk}: {_gate_reason}")
                continue
            _gate_reason_tag = _gate_reason
        if is_dc_breakout:
            _gate_reason_tag = f"DC_BREAKOUT_{dc_tf_used}"
        positions = _get_positions_from_redis(redis_client, account_key)
        pos_amt = positions.get(pk, 0.0)
        _partial_thresh = _cfg_float("EZ_REENTRY_PARTIAL_AUGMENT_THRESHOLD", 0.5)
        _is_partial_augment = (pos_amt > 0 and exit_amt > 0 and pos_amt < _partial_thresh * exit_amt)
        if pos_amt != 0.0 and not _is_partial_augment:
            continue
        if _is_partial_augment:
            sizing_frac = 1.0
            sizing_tag = f"price_cross_partial_augment_pos{pos_amt:.4f}_lt_thr{_partial_thresh:.2f}x{exit_amt:.4f}"
            fire_qty = max(0.0, exit_amt - pos_amt)
        else:
            sizing_frac = 1.0
            sizing_tag = "price_cross_pending_tier_eval"
            fire_qty = (exit_amt if exit_amt > 0 else (start_size / max(cur_px, 1e-9))) * sizing_frac
        if fire_qty <= 0:
            continue
        xr_tag = (exit_reason[:40] or "unk").replace(" ", "_")
        if is_dc_breakout:
            reason = f"DAEMON_DC_BREAKOUT_REENTRY_{dc_tf_used}_exit{exit_px:.6f}_cur{cur_px:.6f}_{sizing_tag}_xr{xr_tag}"
        else:
            reason = f"DAEMON_PRICE_CROSS_REENTRY_exit{exit_px:.6f}_cur{cur_px:.6f}_{sizing_tag}_{_gate_reason_tag}_xr{xr_tag}"
        queue_dir = queue_base / account_key
        if dry_run:
            logger.info(f"[DAEMON DRY-RUN] would queue {pk} qty={fire_qty:.4f} {sizing_tag} cross={exit_px:.6f}→{cur_px:.6f}")
            last_fire[pk] = now
            fired += 1
        elif _write_reentry_command(queue_dir, pk, account_key, symbol, pos_side, fire_qty, exit_px, cur_px, sizing_tag, reason, now):
            logger.info(f"[DAEMON] queued REENTRY {pk} qty={fire_qty:.4f} {sizing_tag} cross={exit_px:.6f}→{cur_px:.6f}")
            last_fire[pk] = now
            fired += 1
    return fired


def _heartbeat(redis_client, interval: float) -> None:
    if redis_client is None:
        return
    try:
        redis_client.set("ez_reentry:heartbeat:daemon", str(time.time()), ex=max(int(interval * 3), 90))
    except Exception:
        pass


def run(once: bool = False, dry_run: bool = False) -> None:
    redis_client = _get_redis()
    base_path = Path(os.environ.get("BINANCE_BASE_PATH", "/Users/niels/Documents/binance"))
    queue_base = base_path / "data" / "reentry_queue"
    queue_base.mkdir(parents=True, exist_ok=True)
    interval = float(os.environ.get("EZ_REENTRY_DAEMON_INTERVAL_S", "5"))
    accounts = _accounts_from_config()
    last_fire: Dict[str, float] = {}
    mode = "DRY-RUN" if dry_run else "ACTIVE"
    logger.info(f"[DAEMON] started {mode} — accounts={accounts} interval={interval}s queue={queue_base}")
    while True:
        cycle_start = time.time()
        try:
            _heartbeat(redis_client, interval)
            n = _evaluate_and_queue(redis_client, base_path, queue_base, accounts, last_fire, dry_run)
            if n:
                logger.info(f"[DAEMON] tick: {n} command(s) queued")
        except Exception as e:
            logger.error(f"[DAEMON] loop error: {e}", exc_info=True)
        if once:
            return
        sleep_for = max(0.1, interval - (time.time() - cycle_start))
        time.sleep(sleep_for)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="single pass then exit")
    parser.add_argument("--dry-run", action="store_true", help="evaluate but do NOT write commands")
    args = parser.parse_args()
    try:
        run(once=args.once, dry_run=args.dry_run)
    except KeyboardInterrupt:
        logger.info("[DAEMON] interrupted, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
