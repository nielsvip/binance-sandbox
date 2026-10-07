"""
HEDGE_PROTECT_OPPOSITE + BALANCE_FLOOR_HALT + OVERTRADE_GUARD vectorization.

Mirrors the three gates in ez_manage.py:13421-13574 so backtest engines can
evaluate them WITHOUT touching ez_manage.py and WITHOUT writing to
position_evaluator.py.

USER CONTRACT (mirrors live):
  - REENTRY / HEDGE bypass HEDGE_PROTECT_OPPOSITE (never-fail mandate).
  - BALANCE_FLOOR_HALT defaults DISABLED in backtest (no real balance).
  - OVERTRADE_GUARD enforced on all entries (including REENTRY/HEDGE) as sanity.

API:
  evaluate_protect_balance_overtrade_core(...)  -> (blocked, reason, gate_fired)
  evaluate_protect_balance_overtrade_vec(rows, config) -> list of tuples

Inputs (core):
  action: str (e.g. "OPEN_LONG", "CLOSE_FULL", "AUGMENT_LONG", "REDUCE_LONG",
                "HEDGE_OPEN_SHORT", "REENTRY_LONG", "BUY")
  position_key: str  "<acct>:<SYM>_<SIDE>"
  account_key: Optional[str]
  positions_dict: dict[str, obj]   obj has .positionAmt, .gain attributes
                                   OR dict-like keys "positionAmt", "gain"
  balance_sentinel_path: Optional[str|Path]  if not None and file exists -> HALT
  decisions_events: Optional[Iterable[dict]]  in-memory event stream
                                              each event {ts:int, pkey:str, action:str}
                                              used to count today's opens/augments
  now_ts: int (UTC epoch seconds)
  config: dict-like with:
     HEDGE_PROTECT_OPPOSITE_ENABLED       (default True)
     BALANCE_FLOOR_HALT_ENABLED           (default False — backtest off)
     OVERTRADE_GUARD_ENABLED              (default True)
     TRADES_PER_SYM_PER_DAY_MAX           (default 8)
     OVERTRADE_GUARD_DAILY_MAX            (alias for TRADES_PER_SYM_PER_DAY_MAX)
     OVERTRADE_BYPASS_EMERGENCY           (default True)
  reason: str entry/exit reason — used only for OVERTRADE emergency bypass parity
  is_full_close: bool
  is_hedge: bool

Returns: tuple(blocked: bool, reason: str, gate_fired: str)
   gate_fired ∈ {"", "BALANCE_FLOOR_HALT", "OVERTRADE_GUARD", "HEDGE_PROTECT_OPPOSITE"}
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple


_EMERG_TOKENS = (
    "RIDICULOUS",
    "BREAK_REVERSE",
    "ALL_TF_AGAINST",
    "INTERVENTION",
    "MANUAL",
)


_HPO_BYPASS_TOKENS = (
    "EMERGENCY",
    "HARD_STOP",
    "MAX_AGE",
    "ORPHAN",
    "LIQ",
    "STRUCTURAL",
    "STDEV_BREAKOUT",
    "KEY_LEVEL",
    "PARABOLIC",
    "AGENT",
    "MANUAL",
    "USER",
    "BALANCE_FLOOR",
)


def _cfg_get(config: Any, key: str, default: Any) -> Any:
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _coerce_float(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _pos_attr(pos_obj: Any, key: str, default: Any = 0) -> Any:
    if pos_obj is None:
        return default
    if isinstance(pos_obj, dict):
        return pos_obj.get(key, default)
    return getattr(pos_obj, key, default)


def _is_entry_action(action: str) -> bool:
    act = (action or "").upper()
    is_open_like = ("OPEN" in act) or ("AUGMENT" in act) or ("ENTRY" in act) or (act == "BUY")
    is_close_like = ("CLOSE" in act) or ("REDUCE" in act)
    return is_open_like and not is_close_like


def _is_close_action(action: str, is_full_close: bool) -> bool:
    act = (action or "").upper()
    return ("CLOSE" in act) or ("REDUCE" in act) or bool(is_full_close)


def _is_reentry_or_hedge(action: str, is_hedge: bool, reason: str) -> bool:
    act = (action or "").upper()
    rs = (reason or "").upper()
    if is_hedge:
        return True
    if "REENTRY" in act or "HEDGE" in act:
        return True
    if "REENTRY" in rs or "HEDGE" in rs:
        return True
    return False


def _account_of(position_key: Optional[str], account_key: Optional[str]) -> Optional[str]:
    if account_key:
        return account_key
    if position_key and ":" in position_key:
        return position_key.split(":", 1)[0]
    return None


def _opposite_pkey(position_key: str) -> Optional[str]:
    if not position_key:
        return None
    if position_key.endswith("_LONG"):
        return position_key[: -len("_LONG")] + "_SHORT"
    if position_key.endswith("_SHORT"):
        return position_key[: -len("_SHORT")] + "_LONG"
    return None


def evaluate_protect_balance_overtrade_core(
    action: str,
    position_key: str,
    account_key: Optional[str],
    positions_dict: Optional[dict],
    balance_sentinel_path: Optional[Any],
    decisions_events: Optional[Iterable[dict]],
    now_ts: int,
    config: Any = None,
    reason: str = "",
    is_full_close: bool = False,
    is_hedge: bool = False,
) -> Tuple[bool, str, str]:
    """Returns (blocked, reason, gate_fired)."""
    # Order mirrors live ez_manage.py: BALANCE_FLOOR_HALT -> OVERTRADE_GUARD -> HEDGE_PROTECT_OPPOSITE.
    act_up = (action or "").upper()
    is_entry = _is_entry_action(act_up)
    # ---------------- BALANCE_FLOOR_HALT ----------------
    if bool(_cfg_get(config, "BALANCE_FLOOR_HALT_ENABLED", False)) and is_entry:
        acct = _account_of(position_key, account_key)
        if acct and balance_sentinel_path:
            try:
                p = Path(str(balance_sentinel_path))
                if p.exists():
                    return True, f"BLOCKED_BALANCE_FLOOR_HALT_{acct}", "BALANCE_FLOOR_HALT"
            except Exception:
                pass
    # ---------------- OVERTRADE_GUARD ----------------
    if bool(_cfg_get(config, "OVERTRADE_GUARD_ENABLED", True)) and is_entry:
        ot_max = int(
            _cfg_get(
                config,
                "OVERTRADE_GUARD_DAILY_MAX",
                _cfg_get(config, "TRADES_PER_SYM_PER_DAY_MAX", 8),
            )
        )
        rs_up = (reason or "").upper()
        bypass_emerg = bool(_cfg_get(config, "OVERTRADE_BYPASS_EMERGENCY", True))
        is_emerg = bypass_emerg and any(tok in rs_up for tok in _EMERG_TOKENS)
        if ot_max > 0 and not is_emerg:
            # Count today's entries for this position_key from in-memory event stream.
            try:
                today_str = datetime.fromtimestamp(int(now_ts), tz=timezone.utc).strftime("%Y%m%d")
            except Exception:
                today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            count_today = 0
            if decisions_events is not None:
                for ev in decisions_events:
                    try:
                        if ev.get("pkey") != position_key:
                            continue
                        ev_act = (ev.get("action") or "").upper()
                        if not _is_entry_action(ev_act):
                            continue
                        ev_ts = int(ev.get("ts", 0))
                        ev_day = datetime.fromtimestamp(ev_ts, tz=timezone.utc).strftime("%Y%m%d")
                        if ev_day == today_str:
                            count_today += 1
                    except Exception:
                        continue
            if count_today >= ot_max:
                return (
                    True,
                    f"BLOCKED_OVERTRADE_{count_today}_OF_{ot_max}",
                    "OVERTRADE_GUARD",
                )
    # ---------------- HEDGE_PROTECT_OPPOSITE ----------------
    # User contract: REENTRY/HEDGE bypass HEDGE_PROTECT_OPPOSITE per never-fail mandate.
    if bool(_cfg_get(config, "HEDGE_PROTECT_OPPOSITE_ENABLED", True)):
        is_close = _is_close_action(act_up, is_full_close)
        if is_close and position_key and ":" in position_key:
            if not _is_reentry_or_hedge(act_up, is_hedge, reason):
                rs_up = (reason or "").upper()
                bypass = any(tok in rs_up for tok in _HPO_BYPASS_TOKENS)
                if not bypass:
                    other_pk = _opposite_pkey(position_key)
                    if other_pk and positions_dict is not None:
                        other = positions_dict.get(other_pk)
                        if other is not None:
                            other_amt = abs(_coerce_float(_pos_attr(other, "positionAmt", 0), 0.0))
                            if other_amt > 0.0001:
                                return (
                                    True,
                                    f"BLOCKED_HEDGE_PROTECT_OPPOSITE_LOSER_hedge_exists_oppamt{other_amt:.4f}",
                                    "HEDGE_PROTECT_OPPOSITE",
                                )
    return False, "", ""


def evaluate_protect_balance_overtrade_vec(rows: Iterable[dict], config: Any = None) -> list:
    """Vectorized batch: each row is the kwargs dict for the core fn."""
    out = []
    for r in rows:
        out.append(
            evaluate_protect_balance_overtrade_core(
                action=r.get("action", ""),
                position_key=r.get("position_key", ""),
                account_key=r.get("account_key"),
                positions_dict=r.get("positions_dict") or {},
                balance_sentinel_path=r.get("balance_sentinel_path"),
                decisions_events=r.get("decisions_events"),
                now_ts=int(r.get("now_ts", 0)),
                config=config if config is not None else r.get("config"),
                reason=r.get("reason", ""),
                is_full_close=bool(r.get("is_full_close", False)),
                is_hedge=bool(r.get("is_hedge", False)),
            )
        )
    return out
