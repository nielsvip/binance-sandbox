"""Desktop alerts for emergency exit fires (R1/R2/R3/HEDGE_FAILED).

osascript native notification (macOS only) + JSONL append to data/sweep_alerts/bad_exits.jsonl.
Failures are silent — alerts must NEVER block trading.

THROTTLED 2026-05-09 after BAD_EXIT flood blocked Macbook (8000+ notifications).
- JSONL log writes EVERY event (full audit record).
- Desktop notification fires at most once per (account, position_key, reason_class) per
  ALERT_NOTIFY_THROTTLE_SEC window (default 1800s = 30min). Repeats are silenced.
"""
import json
import os
import subprocess
import time
from datetime import datetime, timezone

ALERT_LOG = "data/sweep_alerts/bad_exits.jsonl"
ALERT_NOTIFY_THROTTLE_SEC = float(os.environ.get("ALERT_NOTIFY_THROTTLE_SEC", "1800"))
_NOTIFY_LAST: dict = {}

# Tags that indicate a "valid reason" for an action to fail. When matched, callers
# should NOT alert — the failure is expected. Match is substring, lowercased.
# 2026-05-10 USER MANDATE: alert MUST mean "agent bug, no valid reason this didn't execute".
# If failure cause is in this list, suppress the alert and log debug only.
_VALID_FAILURE_TAGS = (
    'min notional', 'min_notional', 'minnotional',
    'lot size', 'lot_size', 'lotsize',
    'min qty', 'min_qty', 'minqty',
    'precision', 'rounddown',
    'no qty', 'qty=0', 'positionamt=0', 'positionamt=0.0',
    'already closed', 'already_closed', 'position_closed', 'position not found',
    'blacklist', 'not tradeable', 'not_tradeable', 'symbol not allowed',
    'symbol_not_tradeable', 'tradeable=false',
    'dust', 'reduce_only', 'reduceonly', 'reduce-only',
    'mode_skip', 'mode_config_mismatch',
    'symbol_lock', 'completed_lockout', 'completed_lock', 'newborn_grace',
    'tracker_block', 'hedge_of_hedge', 'hedge_completed_lock',
    'account not allowed', 'account_not_allowed', 'check_account_allowed',
    'in flight', 'in_flight', 'in-flight', 'debounce',
    'duplicate', 'dup_guard', 'cooldown',
    'newborn-position-immunity', 'augment_lock',
    'liquidity', 'no quote', 'no_quote', 'stale price',
    'rate limit', 'rate_limit', '-1003', '-2010', '-2011', '-2018', '-2019',
    'hedge_pending', 'hedge_in_flight',
)


def should_alert_for_failure(err_or_result) -> bool:
    """Return True only when the failure is unexplained (no known-valid tag matches).

    USER MANDATE 2026-05-10: alert ONLY when there is no valid reason for the action
    not to have executed. Min-notional, blacklist, position-already-closed, etc are
    valid reasons → no alert. Unknown/unexpected failures → alert.
    """
    s = str(err_or_result or '').lower()
    if not s:
        return True
    return not any(tag in s for tag in _VALID_FAILURE_TAGS)


def _osascript_notify(title, subtitle, body):
    try:
        msg = f'display notification "{body}" with title "{title}" subtitle "{subtitle}"'
        subprocess.Popen(["osascript", "-e", msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _reason_class(reason: str) -> str:
    r = (reason or "").upper()
    for tag in ("R1_DC_LOW4", "R2_WT_VEL_SLOW", "R3_HEDGE_INVARIANT", "HEDGE_FAILED"):
        if tag in r:
            return tag
    return r[:24] or "UNKNOWN"


def alert_bad_exit(account, position_key, gain, reason, entry_signal, entry_ts, current_price):
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "account": account or "?",
        "position_key": position_key,
        "gain_pct": round(float(gain), 4),
        "exit_reason": reason,
        "entry_signal": str(entry_signal)[:120],
        "entry_ts": str(entry_ts)[:30],
        "current_price": float(current_price) if current_price else 0.0,
    }
    try:
        os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
        with open(ALERT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    try:
        _key = (str(account or "?"), str(position_key), _reason_class(reason))
        _now = time.time()
        _last = _NOTIFY_LAST.get(_key, 0.0)
        if _now - _last < ALERT_NOTIFY_THROTTLE_SEC:
            return
        _NOTIFY_LAST[_key] = _now
        _short_reason = _reason_class(reason)
        _osascript_notify(title=f"{_short_reason} — {position_key}", subtitle=f"{account or '?'} | g={gain:.2f}% | see :5057", body=f"reason={str(reason)[:60]}")
    except Exception:
        pass
