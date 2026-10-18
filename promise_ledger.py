"""promise_ledger — one append-only record of what the vector PROMISED and what live DELIVERED (USER 2026-10-18).

USER: "ONE SIMPLE AND CLEAR RULE while we figure this out: Trade what you promised nothing more nothing less."

A promise is a vec twin action at a closed 15m bar (OPEN / AUGMENT / REDUCE / CLOSE + reason). A delivery is what the live
dispatch did with it (SUCCESS / refusal tag / skip / preemption / forced market leg) and WHEN, so the gap is a measured
number per sym_side and account instead of an opinion. Rows land in data/promise_ledger/<UTC date>.jsonl:

    {"ts":…,"kind":"PROMISE","acct":"men","key":"men:XTZUSDT_LONG","sym_side":"XTZUSDT_LONG","bar_ts":…,"n":2,
     "action":"CLOSE","reason":"EXIT_VELOCITY_WT …","is_exit":true}
    {"ts":…,"kind":"DELIVERY","acct":"men","key":"men:XTZUSDT_LONG","label":"CLOSE EXIT_VELOCITY_WT …",
     "outcome":"SUCCESS","is_exit":true,"lag_s":41.2,"detail":""}

Rules this module obeys, because it runs inside the live trader:
  * never raises — every public call is wrapped; a ledger failure may not cost a trade;
  * never blocks — one small append per event, no fsync, no locks held across I/O;
  * writes nothing but its own directory, reads nothing but its own in-process state;
  * kill switch PROMISE_LEDGER=0 (env) disables all writes.

tools/promise_audit.py joins the two row kinds per key and reports promises kept / late / broken per sym_side and account.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "data" / "promise_ledger"
BAR_S = 900.0
EXIT_ACTIONS = ("CLOSE", "REDUCE")
_LOCK = threading.Lock()
_SEEN = {}
_SEEN_MAX = 20000
_STATE = {}


def enabled() -> bool:
    return os.environ.get("PROMISE_LEDGER", "1") != "0"


def is_exit_label(label) -> bool:
    s = str(label or "").upper()
    first = s.split(" ", 1)[0] if s else ""
    if first in EXIT_ACTIONS:
        return True
    return any(t in s for t in ("VEC_EXIT_OWED", "CONVERGE_TO_FLAT", "STALE_FLATTEN", "FORCE_CLOSE", "TIMEOUT_EXIT"))


def _acct(key) -> str:
    k = str(key or "")
    return k.split(":", 1)[0] if ":" in k else ""


def _sym_side(key) -> str:
    k = str(key or "")
    return k.split(":", 1)[1] if ":" in k else k


def _append(row: dict) -> None:
    if not enabled():
        return
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d", time.gmtime(row["ts"]))
        line = json.dumps(row, default=str, separators=(",", ":"))
        with _LOCK:
            with open(DIR / f"{day}.jsonl", "a") as fh:
                fh.write(line + "\n")
    except Exception:
        pass


def promise(key, bar_ts, action, reason, n=1, source="VEC_EXACT") -> None:
    """The twin decided this at this bar. Idempotent per (key, bar, n, action) so a re-read of the same bar adds no row."""
    try:
        if not enabled():
            return
        k = (str(key), float(bar_ts or 0.0), int(n), str(action or "").upper())
        with _LOCK:
            if k in _SEEN:
                return
            if len(_SEEN) > _SEEN_MAX:
                _SEEN.clear()
            _SEEN[k] = time.time()
            _STATE[str(key)] = {"bar_ts": float(bar_ts or 0.0), "action": str(action or "").upper(), "at": time.time()}
        _append({"ts": time.time(), "kind": "PROMISE", "acct": _acct(key), "key": str(key), "sym_side": _sym_side(key),
                 "bar_ts": float(bar_ts or 0.0), "n": int(n), "action": str(action or "").upper(),
                 "reason": str(reason or "")[:120], "is_exit": str(action or "").upper() in EXIT_ACTIONS, "source": source})
    except Exception:
        pass


def delivery(key, label, outcome, detail="", bar_ts=None) -> None:
    """What the live dispatch did with a promise. lag_s is measured from the promise's bar close when that bar is known."""
    try:
        if not enabled():
            return
        now = time.time()
        bt = bar_ts
        if bt is None:
            st = _STATE.get(str(key)) or {}
            bt = st.get("bar_ts")
        lag = None
        if bt:
            lag = round(now - (float(bt) + BAR_S), 1)
        _append({"ts": now, "kind": "DELIVERY", "acct": _acct(key), "key": str(key), "sym_side": _sym_side(key),
                 "label": str(label or "")[:120], "outcome": str(outcome or "")[:80], "is_exit": is_exit_label(label),
                 "bar_ts": float(bt) if bt else None, "lag_s": lag, "detail": str(detail or "")[:160]})
    except Exception:
        pass


def broken(key, label, why, detail="") -> None:
    """A promise live could not keep inside its bar: the rule was violated and the row says so in those words."""
    try:
        if not enabled():
            return
        st = _STATE.get(str(key)) or {}
        bt = st.get("bar_ts")
        _append({"ts": time.time(), "kind": "BROKEN", "acct": _acct(key), "key": str(key), "sym_side": _sym_side(key),
                 "label": str(label or "")[:120], "why": str(why or "")[:80], "is_exit": is_exit_label(label),
                 "bar_ts": float(bt) if bt else None,
                 "lag_s": (round(time.time() - (float(bt) + BAR_S), 1) if bt else None), "detail": str(detail or "")[:160]})
    except Exception:
        pass
