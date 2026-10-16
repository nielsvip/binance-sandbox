"""Tradier broker truth snapshot — starvation-proof.

USER 2026-10-16: "none of the stop market orders we place when we buy are executed ... ALL rejected because the
quantity is more than the existing quantity on Tradier ... positions need to keep TOTAL AND COMPLETE SYNCHRONICITY
with what is on the exchange".

WHY THIS MODULE EXISTS (proven from ~/logs/tradier_manage_trb.log, 2026-10-16 session):
  193 x "[LOOP_LAG] event loop frozen ... (max 81.5s) - broker awaits starve while loop parked"
  202 x "[FIXED_STOP_BROKER_VERIFY_TR] ...: verify failed () - unverified"
  102 x "[FIXED_STOP_BROKER_LIST_TR] ...: order listing failed () - unverified"
The empty "()" is str(asyncio.TimeoutError()). The Tradier calls were NOT slow: the main event loop was parked in
synchronous work (per_sym_store deep copies, pathlib.glob in is_data_fresh, subprocess.run in the options watchdog,
json.loads in execute_now), so every `asyncio.wait_for(..., timeout=10)` on that same loop expired before its request
could be serviced. Every single rejected stop of 2026-10-16 (XOM/CRK/VLO/QCOM/APO/RRC, "order is for more shares than
your current position") was preceded within ~25 s by one of those unverified listings: the ensure path could not see
the stop that was already resting, so it placed a second one and the broker rejected it.

A read that must be right cannot share a loop with work that can park it. This module owns ONE daemon THREAD running
its OWN event loop and one long-lived client per account, and refreshes:
    positions  -> /accounts/{id}/positions
    orders     -> /accounts/{id}/orders   (ALL pages, limit=100, terminated on a short page)
into a lock-protected snapshot. Readers on the main loop call plain synchronous getters: no await, no timeout, and
therefore nothing for a frozen loop to break. A refresh that FAILS never replaces a good snapshot and never writes an
empty one - the previous snapshot simply ages, and `age()` tells the caller exactly how stale its truth is. A caller
that cannot get a fresh snapshot must refuse to act, never guess.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger("tradier_broker_snapshot")

PROTECTIVE_TYPES = ("stop", "stop_limit")
CLOSING_SIDES = ("sell", "buy_to_cover")
LIVE_ORDER_STATUSES = ("open", "pending", "partially_filled")

_LOCK = threading.RLock()
_SNAP: Dict[str, Dict[str, Any]] = {}
_WANT: Dict[str, float] = {}
_THREAD: Optional[threading.Thread] = None
_LOOP: Optional[asyncio.AbstractEventLoop] = None
_STARTED = threading.Event()


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _interval() -> float:
    """Seconds between refreshes, per account. One refresh = 1 positions call + 1 order page (a normal day is ~50
    orders, well inside one 100-row page), so 5 s is ~24 account-endpoint calls/min — far below Tradier's limit and
    an order of magnitude below what it replaces (the old path fired a per-SYMBOL positions listing whenever the
    broker read was FLAT or failed, which is the storm the 2026-10-15 comments describe). invalidate() still forces
    an immediate refresh after any order mutation, so this interval only bounds passive staleness."""
    try:
        return max(1.0, float(os.getenv("TRADIER_SNAPSHOT_INTERVAL_SEC", "5") or 5))
    except (TypeError, ValueError):
        return 5.0


def _order_pages_max() -> int:
    try:
        return max(1, int(os.getenv("TRADIER_SNAPSHOT_ORDER_PAGES", "40") or 40))
    except (TypeError, ValueError):
        return 40


async def _fetch_orders_all_pages(client) -> Optional[List[Dict]]:
    """Every page of /accounts/{id}/orders. 2026-10-14 proved page 1 alone hides the resting stops behind the day's
    rejects; the caller-side loop that replaced it broke on `len(page) < 1500` while the endpoint pages at 100, so it
    still stopped after page 1. Explicit limit=100, stop on the first short or empty page. None on any failure."""
    out: List[Dict] = []
    for page in range(1, _order_pages_max() + 1):
        res = await client._request("GET", f"/accounts/{client._current_id}/orders", params={"page": page, "limit": 100}, use_data_context=False)
        if not isinstance(res, dict) or "orders" not in res:
            return None
        inner = res.get("orders")
        if inner in ("null", None):
            break
        if not isinstance(inner, dict) or "order" not in inner:
            return None
        got = inner["order"]
        got = got if isinstance(got, list) else [got]
        out.extend(got)
        if len(got) < 100:
            break
    return out


async def _refresh_one(client, account_key: str) -> None:
    positions = None
    orders = None
    try:
        positions = await client.get_account_positions(account_key)
    except Exception as exc:
        logger.warning(f"[SNAPSHOT] {account_key}: positions fetch failed ({exc!r})")
    try:
        orders = await _fetch_orders_all_pages(client)
    except Exception as exc:
        logger.warning(f"[SNAPSHOT] {account_key}: orders fetch failed ({exc!r})")
    if positions is None or orders is None:
        # NEVER overwrite a good snapshot with a failed read, and never publish an empty one: an empty order list
        # reads as "no resting stop" and duplicates every backstop; an empty position list reads as "flat".
        with _LOCK:
            prev = _SNAP.get(account_key)
            if prev is not None:
                prev["last_fail_ts"] = time.time()
                prev["consecutive_fails"] = int(prev.get("consecutive_fails", 0)) + 1
        return
    qty: Dict[str, float] = {}
    for row in positions:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol", "")).upper()
        if sym:
            qty[sym] = qty.get(sym, 0.0) + _f(row.get("quantity"))
    with _LOCK:
        _SNAP[account_key] = {
            "ts": time.time(),
            "qty": qty,
            "orders": orders,
            "last_fail_ts": 0.0,
            "consecutive_fails": 0,
        }


async def _account_loop(account_key: str) -> None:
    from tradier_api import TradierAPIClient  # local import: module must stay importable without a live config

    client = None
    while True:
        try:
            if client is None or getattr(client, "session", None) is None or client.session.closed:
                client = TradierAPIClient(account_key=account_key)
                await client.connect()
            await _refresh_one(client, account_key)
        except Exception as exc:
            logger.warning(f"[SNAPSHOT] {account_key}: refresh cycle failed ({exc!r})")
            try:
                if client is not None:
                    await client.close()
            except Exception:
                pass
            client = None
        # An invalidate() request (something mutated the order book) refreshes immediately instead of waiting out the interval.
        deadline = time.time() + _interval()
        while time.time() < deadline:
            with _LOCK:
                want = _WANT.pop(account_key, 0.0)
            if want:
                break
            await asyncio.sleep(0.2)


def _thread_main(accounts: Tuple[str, ...]) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    global _LOOP
    _LOOP = loop
    _STARTED.set()
    for acct in accounts:
        loop.create_task(_account_loop(acct))
    try:
        loop.run_forever()
    except Exception as exc:
        logger.error(f"[SNAPSHOT] thread loop died ({exc!r})")


def start(accounts) -> None:
    """Idempotent. Call once per process with the accounts it owns."""
    global _THREAD
    accts = tuple(str(a).strip().lower() for a in (accounts or []) if str(a).strip())
    if not accts:
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_thread_main, args=(accts,), name="tradier-broker-snapshot", daemon=True)
        _THREAD.start()
    _STARTED.wait(timeout=5.0)
    logger.critical(f"[SNAPSHOT] broker truth thread started for {list(accts)} interval={_interval()}s")


def running() -> bool:
    return _THREAD is not None and _THREAD.is_alive()


def invalidate(account_key: str) -> None:
    """Order book mutated — refresh this account's snapshot now instead of at the next interval."""
    with _LOCK:
        _WANT[str(account_key or "").lower()] = time.time()


def age(account_key: str) -> Optional[float]:
    """Seconds since this account's last SUCCESSFUL refresh, or None when there has never been one."""
    with _LOCK:
        snap = _SNAP.get(str(account_key or "").lower())
    if not snap:
        return None
    return time.time() - float(snap.get("ts", 0.0))


def _fresh(account_key: str, max_age: float) -> Optional[Dict[str, Any]]:
    with _LOCK:
        snap = _SNAP.get(str(account_key or "").lower())
    if not snap:
        return None
    if time.time() - float(snap.get("ts", 0.0)) > float(max_age):
        return None
    return snap


def positions(account_key: str, max_age: float = 20.0) -> Optional[Dict[str, float]]:
    """{SYMBOL: signed_qty} as the broker holds it, or None when no snapshot is fresh enough. None is NOT 'flat'."""
    snap = _fresh(account_key, max_age)
    return dict(snap["qty"]) if snap else None


def quantity(account_key: str, symbol: str, max_age: float = 20.0) -> Optional[float]:
    """Signed broker quantity for one symbol: 0.0 when the broker confirms flat, None when unverified."""
    snap = _fresh(account_key, max_age)
    if snap is None:
        return None
    return float(snap["qty"].get(str(symbol or "").upper(), 0.0))


def orders(account_key: str, max_age: float = 20.0) -> Optional[List[Dict]]:
    snap = _fresh(account_key, max_age)
    return list(snap["orders"]) if snap else None


def protective_stops(account_key: str, symbol: str, position_side: str, max_age: float = 20.0) -> Optional[List[Dict]]:
    """Live resting protective stops for this position (closing-side stop / stop_limit, open|pending|partially_filled).
    None = UNVERIFIED (no fresh snapshot). [] = verified: this position has no resting stop."""
    snap = _fresh(account_key, max_age)
    if snap is None:
        return None
    sym = str(symbol or "").upper()
    close_side = "sell" if str(position_side or "").upper() == "LONG" else "buy_to_cover"
    out = []
    for o in snap["orders"]:
        if not isinstance(o, dict):
            continue
        if str(o.get("symbol", "")).upper() != sym:
            continue
        if str(o.get("side", "")).lower() != close_side:
            continue
        if str(o.get("type", "")).lower() not in PROTECTIVE_TYPES:
            continue
        if str(o.get("status", "")).lower() not in LIVE_ORDER_STATUSES:
            continue
        out.append(o)
    return out


def order_status(account_key: str, order_id: Any, max_age: float = 20.0) -> Optional[str]:
    """Broker status of one order id, '' when the fresh snapshot does not contain it, None when unverified."""
    snap = _fresh(account_key, max_age)
    if snap is None:
        return None
    oid = str(order_id or "")
    for o in snap["orders"]:
        if isinstance(o, dict) and str(o.get("id", "")) == oid:
            return str(o.get("status", "")).lower()
    return ""


def health() -> Dict[str, Any]:
    with _LOCK:
        return {
            "running": running(),
            "accounts": {
                a: {
                    "age_s": round(time.time() - float(s.get("ts", 0.0)), 1),
                    "positions": len(s.get("qty") or {}),
                    "orders": len(s.get("orders") or []),
                    "consecutive_fails": int(s.get("consecutive_fails", 0)),
                }
                for a, s in _SNAP.items()
            },
        }
