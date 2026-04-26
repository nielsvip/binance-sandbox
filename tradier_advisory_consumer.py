# pylint: disable=W,C,R,I
"""tradier_advisory_consumer.py — local consumer for stock-account routine advisories.

Mirror of fin_advisory_consumer.py for the tradier (stocks) side. Serves tra, trb, trc.

The remote `{account}-hourly-supervisor` Claude routine writes per-account advisories to
~/binance-agent-handoff/{account}_advisories.json. This module reads them, caches per-account
for 30s, ignores expired entries, and exposes a single short-circuit helper for tradier_manage.py
to call at the top of process_position.

Decision log path is distinct from crypto so analytics don't conflate streams:
~/binance-agent-handoff/logs/tradier_advisory_decisions_log.jsonl

Advisory schema (per-account file):
{
  "schema_version": 1,
  "generated_at_utc": "...",
  "advisories": {
    "AAPL_LONG": {
      "action": "force_close|hold|block_entry|force_open|block_augment|force_augment",
      "scope": "position|symbol|global",
      "size_override_usd": 50.0,
      "reason": "...",
      "thesis": {...},
      "expires_at_utc": "2026-04-26T18:00:00Z",
      "iteration_id": 42
    }
  }
}
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_ACCOUNTS = {"tra", "trb", "trc"}
HANDOFF_DIR = Path.home() / "binance-agent-handoff"
DECISION_LOG_PATH = HANDOFF_DIR / "logs" / "tradier_advisory_decisions_log.jsonl"
CACHE_TTL_SEC = 30
log = logging.getLogger("tradier_agent_advisory")
_cache = {}
_cache_lock = threading.Lock()


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _advisory_path(account_key):
    return HANDOFF_DIR / f"{account_key}_advisories.json"


def _load_advisories(account_key):
    path = _advisory_path(account_key)
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    now_ts = _now().timestamp()
    with _cache_lock:
        c = _cache.get(account_key)
        if c is not None and now_ts - c["loaded_at"] < CACHE_TTL_SEC and c["mtime"] == st.st_mtime:
            return c["data"]
        try:
            with path.open() as f:
                doc = json.load(f)
        except Exception as e:
            log.warning("advisory load %s failed: %s", path, e)
            return (c or {}).get("data") or {}
        adv = doc.get("advisories") or {}
        _cache[account_key] = {"data": adv, "loaded_at": now_ts, "mtime": st.st_mtime}
        return adv


def _lookup(advisories, position_key, symbol):
    if not advisories:
        return None
    if position_key in advisories:
        return advisories[position_key]
    if symbol in advisories:
        return advisories[symbol]
    g = advisories.get("__global__")
    if g and g.get("scope") == "global":
        return g
    return None


def _is_active(adv):
    if not isinstance(adv, dict):
        return False
    exp = _parse_iso(adv.get("expires_at_utc"))
    if exp is None:
        return False
    return _now() < exp


def _log_decision(record):
    try:
        DECISION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DECISION_LOG_PATH.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except Exception as e:
        log.warning("decision log write failed: %s", e)


def check(account_key, symbol, position_side, decision_kind):
    """Return advisory dict if active+applicable, else None.

    decision_kind: 'stop' | 'open' | 'augment' | 'reentry' | 'process'
    Caller short-circuits the evaluate_* return based on adv['action'].
    """
    if account_key not in SUPPORTED_ACCOUNTS:
        return None
    if not symbol:
        return None
    pkey = f"{symbol}_{position_side}" if position_side else symbol
    advisories = _load_advisories(account_key)
    adv = _lookup(advisories, pkey, symbol)
    if not adv or not _is_active(adv):
        return None
    return adv


def log_application(account_key, symbol, position_side, decision_kind, action_taken, advisory, scripted_decision=None):
    if account_key not in SUPPORTED_ACCOUNTS:
        return
    record = {
        "timestamp": _now().isoformat(timespec="seconds"),
        "account": account_key,
        "symbol": symbol,
        "position_side": position_side,
        "decision_kind": decision_kind,
        "action_taken": action_taken,
        "advisory_action": (advisory or {}).get("action"),
        "advisory_reason": (advisory or {}).get("reason"),
        "advisory_iteration_id": (advisory or {}).get("iteration_id"),
        "scripted_decision": scripted_decision,
        "pid": os.getpid(),
    }
    _log_decision(record)
