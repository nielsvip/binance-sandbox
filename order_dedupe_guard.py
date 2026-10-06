"""ORDER_DEDUPE_GUARD — broker-confirmed "previous order is final" gate for EVERY order (2026-10-06 USER, real money).

History: a $2.5k order once produced $70k of executions from duplicate / repeated orders.

Rule enforced here (fail closed):
  Before ANY new order on (account, symbol) is sent, the broker must confirm that
    (a) there is NO open / pending / partially-filled order for that symbol, and
    (b) every order this system previously placed on that symbol (persisted ledger, survives restarts)
        has a FINAL status (filled / canceled / rejected / expired) with its filled quantity known.
  Broker unreachable, query error, unknown or non-final status  ->  NO order (ORDER_DEDUPE_BLOCK).

The ledger entry is written as SUBMITTING *before* the wire call and is never cleared by an exception after it:
only a later broker query that returns a FINAL status releases it.  Over-fill guard: consecutive same-direction
orders inside ORDER_DEDUPE_INTENT_WINDOW_SEC are one intent; a follow-up order is clamped to (intent qty - qty the
broker confirmed filled) and refused once the intent is fully filled (blocks "filled, then re-sent").

Enforcement points (wire level — every submit, every origin, vec-decided and emergency included):
  * GuardedBinanceClient  — wraps python-binance Client; futures_create_order runs the gate (sync; callers already
                            invoke it through asyncio.to_thread).
  * GuardedTradierClient  — wraps tradier_api.TradierAPIClient; place_order runs the gate (async).
Plus execute_now-level early refusal helpers (binance_execute_now_preflight / tradier_execute_now_preflight).
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import fcntl
import functools
import hashlib
import inspect
import json
import math
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_LOG = logging.getLogger("order_dedupe_guard")

BINANCE_FINAL = {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH", "NOT_FOUND", "REJECTED_BY_BROKER"}
TRADIER_FINAL = {"filled", "canceled", "expired", "rejected", "error", "not_found", "rejected_by_broker"}
# Binance error codes where the request may still have executed (status unknown) — never treated as a rejection.
BINANCE_UNKNOWN_CODES = {-1000, -1001, -1006, -1007, -1008}
BINANCE_ORDER_NOT_FOUND = -2013
DEDUPE_BLOCK_BINANCE_CODE = -2027  # existing maker-chase handler (ez_manage place_maker_order) BREAKS on -2027 instead of spinning
DEFAULTS = {
    "ORDER_DEDUPE_GUARD_ENABLED": True,
    "ORDER_DEDUPE_SCOPE": "symbol",  # "symbol" = any open order on the symbol blocks (user rule); "position_side" = hedge legs independent
    "ORDER_DEDUPE_NOTFOUND_GRACE_SEC": 60.0,  # broker "order does not exist" only counts as final after this age
    "ORDER_DEDUPE_CONFIRM_WAIT_SEC": 3.0,  # bounded poll for OUR OWN just-cancelled order to become final (never for foreign orders)
    "ORDER_DEDUPE_CANCEL_RECENT_SEC": 15.0,
    "ORDER_DEDUPE_INTENT_WINDOW_SEC": 20.0,  # covers maker-unverified -> market-fallback (~7-12s after the fill)
    "ORDER_DEDUPE_BLOCK_BACKOFF_SEC": 2.0,  # repeat checks inside this window return BLOCK without new broker calls (no REST hammering)
    "ORDER_DEDUPE_QUERY_TIMEOUT_SEC": 10.0,
    "ORDER_DEDUPE_LEDGER_KEEP": 40,
    "ORDER_DEDUPE_IGNORE_ORDER_TYPES": (),  # e.g. ("STOP_MARKET",) — empty = strict (every open order blocks)
    "ORDER_DEDUPE_AUTO_CANCEL_OWN_AFTER_SEC": 120.0,  # cancel-and-confirm for OUR OWN ledger orders left working this long (0 = off). Foreign orders: never.
    # ── POSITIONS REVAMP 2026-10-06 (IBIT incident): wire-level exposure gate ──
    "WIRE_EXPOSURE_GATE_ENABLED": True,  # master switch for the three checks below (False = logged CRITICAL on every order)
    "WIRE_REQUIRE_EXECUTE_NOW": True,  # exposure-INCREASING orders must carry the execute_now context token (no bypass path can open/augment)
    "WIRE_REFUSE_OPEN_ON_NONZERO": True,  # pure OPEN actions (decided as "from flat") are refused when the broker shows a same-side position
    "WIRE_AUGMENT_MIN_GAIN_PCT": None,  # None -> max(2.5, MIN_GAIN_TO_BUY_AGGRESSIVELY); gain vs max(broker avg entry, last same-direction fill)
    "WIRE_AUGMENT_LAST_FILL_LOOKBACK_SEC": 3 * 86400.0,
    "WIRE_REFUSE_BAD_QTY": True,  # zero / negative / NaN / inf quantity never sent
}


def _cfg(cfg: Any, name: str):
    try:
        if cfg is not None and hasattr(cfg, name):
            return getattr(cfg, name)
    except Exception:
        pass
    return DEFAULTS[name]


def _default_ledger_dir() -> Path:
    env = os.environ.get("ORDER_DEDUPE_LEDGER_DIR")
    if env:
        return Path(env)
    base = None
    try:
        import config as _c  # noqa: WPS433 — lazy, optional
        base = getattr(_c, "BASE_PATH", None)
    except Exception:
        base = None
    return Path(base or Path(__file__).resolve().parent) / "data" / "safety" / "order_ledger"


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


@dataclass
class Decision:
    allowed: bool
    code: str = "OK"
    evidence: Dict[str, Any] = field(default_factory=dict)
    clamp_qty: Optional[float] = None


class OrderDedupeBlocked(Exception):
    """Raised (Binance proxy) when the guard refuses. Subclassed as BinanceAPIException when python-binance is present."""


try:  # make the block look like a broker rejection so existing `except BinanceAPIException` paths fail closed
    from binance.exceptions import BinanceAPIException as _BAE

    class OrderDedupeBlockedBinance(_BAE, OrderDedupeBlocked):  # type: ignore[misc]
        def __init__(self, decision: Decision):
            self.decision = decision
            self.order_dedupe_block = True
            _BAE.__init__(self, None, 400, json.dumps({"code": DEDUPE_BLOCK_BINANCE_CODE, "msg": f"ORDER_DEDUPE_BLOCK:{decision.code}"}))

        def __str__(self):
            return f"APIError(code={self.code}): ORDER_DEDUPE_BLOCK:{self.decision.code}"

except Exception:  # pragma: no cover — python-binance absent

    class OrderDedupeBlockedBinance(OrderDedupeBlocked):  # type: ignore[no-redef]
        def __init__(self, decision: Decision):
            self.decision = decision
            self.order_dedupe_block = True
            self.code = DEDUPE_BLOCK_BINANCE_CODE
            self.status_code = 400
            self.message = f"ORDER_DEDUPE_BLOCK:{decision.code}"
            super().__init__(self.message)


# ───────────────────────────── persisted ledger ─────────────────────────────
class OrderLedger:
    """Per (broker, account) JSON ledger: symbol -> recent orders. flock-guarded read-modify-write, atomic replace."""

    def __init__(self, broker: str, account: str, ledger_dir: Optional[Path] = None, keep: int = 12):
        self.broker = broker
        self.account = account
        self.dir = Path(ledger_dir) if ledger_dir else _default_ledger_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"order_ledger_{broker}_{account}.json"
        self.lock_path = self.dir / f".order_ledger_{broker}_{account}.lock"
        self.keep = keep

    @contextlib.contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        with open(self.path) as fh:
            txt = fh.read()
        if not txt.strip():
            return {}
        data = json.loads(txt)  # corrupt ledger raises -> caller fails closed
        if not isinstance(data, dict):
            raise ValueError("ledger root is not a dict")
        return data

    def _write(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_suffix(f".tmp.{os.getpid()}.{threading.get_ident()}")
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)

    def orders(self, symbol: str) -> List[Dict[str, Any]]:
        with self._locked():
            return list((self._read().get(symbol.upper()) or {}).get("orders") or [])

    def version(self, symbol: str) -> int:
        with self._locked():
            return int((self._read().get(symbol.upper()) or {}).get("v", 0))

    def mutate(self, symbol: str, fn: Callable[[List[Dict[str, Any]]], Optional[bool]], expect_version: Optional[int] = None) -> bool:
        """Apply fn to the symbol's order list under the file lock. Returns False if expect_version mismatched."""
        sym = symbol.upper()
        with self._locked():
            data = self._read()
            row = data.get(sym) or {"orders": [], "v": 0}
            if expect_version is not None and int(row.get("v", 0)) != int(expect_version):
                return False
            orders = list(row.get("orders") or [])
            fn(orders)
            row["orders"] = orders[-self.keep:]
            row["v"] = int(row.get("v", 0)) + 1
            data[sym] = row
            self._write(data)
            return True

    def update_order(self, symbol: str, match: Callable[[Dict[str, Any]], bool], **fields) -> bool:
        hit = {"n": 0}

        def _fn(orders):
            for o in orders:
                if match(o):
                    o.update(fields)
                    o["updated_at"] = time.time()
                    hit["n"] += 1

        self.mutate(symbol, _fn)
        return hit["n"] > 0

    def nonfinal(self, symbol: str) -> List[Dict[str, Any]]:
        return [o for o in self.orders(symbol) if not o.get("final")]


# ───────────────────────────── shared logic ─────────────────────────────
class _GuardBase:
    broker = "?"
    final_set: set = set()

    def __init__(self, account: str, ledger: Optional[OrderLedger] = None, cfg: Any = None, logger: Optional[logging.Logger] = None, clock: Callable[[], float] = time.time, ledger_dir: Optional[Path] = None):
        self.account = account
        self.cfg = cfg
        self.log = logger or _LOG
        self.clock = clock
        self.ledger = ledger or OrderLedger(self.broker, account, ledger_dir, int(_cfg(cfg, "ORDER_DEDUPE_LEDGER_KEEP")))
        self._backoff: Dict[str, tuple] = {}
        self._tlocks: Dict[str, threading.Lock] = {}
        self._tl_guard = threading.Lock()

    def enabled(self) -> bool:
        return bool(_cfg(self.cfg, "ORDER_DEDUPE_GUARD_ENABLED"))

    def key_lock(self, symbol: str) -> threading.Lock:
        with self._tl_guard:
            return self._tlocks.setdefault(symbol.upper(), threading.Lock())

    def _is_final_status(self, status: Any) -> bool:
        s = str(status or "")
        return (s.upper() if self.broker == "binance" else s.lower()) in self.final_set

    def _block(self, symbol: str, code: str, evidence: Dict[str, Any], origin: str = "") -> Decision:
        d = Decision(False, code, evidence)
        self._backoff[symbol.upper()] = (self.clock(), d)
        self.log.critical(f"🛑 [ORDER_DEDUPE_BLOCK] {self.broker}:{self.account}:{symbol} code={code} origin={origin[:80]} evidence={json.dumps(evidence, default=str)[:900]}")
        try:
            with open(self.ledger.dir / "order_dedupe_blocks.jsonl", "a") as fh:
                fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "broker": self.broker, "account": self.account, "symbol": symbol, "code": code, "origin": origin[:200], "evidence": evidence}, default=str) + "\n")
        except Exception:
            pass
        return d

    def _backoff_hit(self, symbol: str) -> Optional[Decision]:
        rec = self._backoff.get(symbol.upper())
        if rec and (self.clock() - rec[0]) < float(_cfg(self.cfg, "ORDER_DEDUPE_BLOCK_BACKOFF_SEC")):
            return Decision(False, rec[1].code, {"backoff": True, **rec[1].evidence})
        return None

    def intent_check(self, symbol: str, side: str, position_side: str, qty: float) -> Decision:
        """Same-direction chain inside the intent window: refuse once the broker-confirmed fills cover the intent; clamp otherwise."""
        orders = self.ledger.orders(symbol)
        window = float(_cfg(self.cfg, "ORDER_DEDUPE_INTENT_WINDOW_SEC"))
        if window <= 0 or not orders:
            return Decision(True)
        chain: List[Dict[str, Any]] = []
        nxt_ts = self.clock()
        for o in reversed(orders):
            if str(o.get("side", "")).upper() != str(side).upper() or str(o.get("position_side", "") or "").upper() != str(position_side or "").upper():
                break
            ts = _f(o.get("submitted_at"), 0.0)
            if nxt_ts - ts > window:
                break
            chain.append(o)
            nxt_ts = ts
        if not chain:
            return Decision(True)
        intent_d = _d(chain[-1].get("qty"))
        filled_d = sum((_d(o.get("executed_qty")) for o in chain), Decimal(0))
        intent_qty, filled = float(intent_d), float(filled_d)
        ev = {"intent_qty": intent_qty, "filled_confirmed": filled, "chain_ids": [o.get("order_id") or o.get("client_order_id") or o.get("local_id") for o in chain], "requested": qty}
        if intent_d > 0 and filled_d >= intent_d * Decimal("0.999"):
            return Decision(False, "INTENT_ALREADY_FILLED", ev)
        remaining_d = intent_d - filled_d
        if intent_d > 0 and _d(qty) > remaining_d:
            return Decision(True, "CLAMP", ev, clamp_qty=float(remaining_d))
        return Decision(True)


# ─────────────── execute_now context token + wire exposure gate (POSITIONS REVAMP 2026-10-06) ───────────────
# INVARIANT 2 (open only from zero) and INVARIANT 5 (execute_now is the only gate) enforced at the broker wire:
#   * execute_now (both venues) is decorated with execute_now_gate(); it sets a ContextVar token for its duration.
#     asyncio.to_thread / create_task copy the context, so every wire call made on behalf of that execute_now sees it.
#   * An exposure-INCREASING order without a token (REENTRY_MONITOR, copilot, webhook, helper scripts) is refused.
#   * The first increasing leg of a call is judged against a FRESH broker position query (never a file, never a cache):
#       broker flat  -> OPEN allowed;  broker non-zero same side -> it is an AUGMENT: pure-OPEN actions are refused,
#       every other action must pass the augment gain floor vs max(broker avg entry, last same-direction fill).
#   * Later legs of the SAME execute_now (chase re-place, market fallback) may only fill the remainder of the first
#     leg's quantity: broker-confirmed delta (and ledger fills) already >= intent -> refused, else clamped.
_ORDER_AUTH: contextvars.ContextVar = contextvars.ContextVar("odg_execute_now_token", default=None)
PURE_OPEN_ACTIONS = {"OPEN", "QUICK_OPEN", "REENTRY_OPEN", "HEDGE_OPEN", "REVERSE", "FRESH_OPEN", "ENTRY"}
_EXEC_ARG_NAMES = ("position_key", "account_key", "symbol", "position_side", "action", "reason", "quantity", "side")


class ExposureBlocked(Exception):
    pass


def current_token() -> Optional[Dict[str, Any]]:
    return _ORDER_AUTH.get()


def _token_from_args(venue: str, args: Dict[str, Any]) -> Dict[str, Any]:
    pk = str(args.get("position_key") or "")
    acct = args.get("account_key") or (pk.split(":", 1)[0] if ":" in pk else None)
    sym = args.get("symbol")
    ps = args.get("position_side")
    if not sym and pk:
        tail = pk.split(":", 1)[-1]
        if "_" in tail:
            sym, ps2 = tail.rsplit("_", 1)
            ps = ps or ps2
    return {"venue": venue, "account": acct, "symbol": str(sym or "").upper(), "position_side": str(ps or "").upper(), "action": str(args.get("action") or "").upper(), "reason": str(args.get("reason") or "")[:160], "ts": time.time(), "keys": {}, "orders": []}


def execute_now_gate(venue: str):
    """Decorator for execute_now: stamps the context token used by the wire gate. Transparent otherwise (functools.wraps keeps
    inspect.getsource / signature pointing at the real execute_now)."""

    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(*a, **kw):
            try:
                bound = sig.bind_partial(*a, **kw).arguments
            except Exception:
                bound = dict(kw)
            tok = _ORDER_AUTH.set(_token_from_args(venue, {k: bound.get(k) for k in _EXEC_ARG_NAMES}))
            try:
                return await fn(*a, **kw)
            finally:
                _ORDER_AUTH.reset(tok)

        wrapper.__odg_execute_now_gate__ = True
        return wrapper

    return deco


@contextlib.contextmanager
def execute_now_context(venue: str, **args):
    """Same token as the decorator, for tests / tools that must emulate an execute_now call."""
    tok = _ORDER_AUTH.set(_token_from_args(venue, args))
    try:
        yield _ORDER_AUTH.get()
    finally:
        _ORDER_AUTH.reset(tok)


def _bad_qty(q: Any) -> bool:
    try:
        v = float(q)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(v) or v <= 0


def _augment_floor(cfg: Any) -> float:
    v = _cfg(cfg, "WIRE_AUGMENT_MIN_GAIN_PCT")
    if v is None:
        base = 3.0
        try:
            if cfg is not None and hasattr(cfg, "MIN_GAIN_TO_BUY_AGGRESSIVELY"):
                base = float(getattr(cfg, "MIN_GAIN_TO_BUY_AGGRESSIVELY"))
        except Exception:
            base = 3.0
        return max(2.5, base)
    return max(2.5, float(v))  # CLAUDE.md: never below 2.5


def _gain_pct(is_long: bool, ref: float, mark: float) -> Optional[float]:
    if not (ref > 0 and mark > 0 and math.isfinite(ref) and math.isfinite(mark)):
        return None
    return (mark - ref) / ref * 100.0 if is_long else (ref - mark) / ref * 100.0


def _last_fill_price(guard: "_GuardBase", symbol: str, side: str, position_side: str) -> Optional[float]:
    lookback = float(_cfg(guard.cfg, "WIRE_AUGMENT_LAST_FILL_LOOKBACK_SEC"))
    now = guard.clock()
    for o in reversed(guard.ledger.orders(symbol)):
        if str(o.get("side", "")).upper() != str(side).upper() or str(o.get("position_side", "") or "").upper() != str(position_side or "").upper():
            continue
        if now - _f(o.get("submitted_at"), 0.0) > lookback:
            break
        if _f(o.get("executed_qty")) > 0 and _f(o.get("avg_price")) > 0:
            return _f(o.get("avg_price"))
    return None


def exposure_decision(guard: "_GuardBase", symbol: str, side: str, position_side: str, is_long: bool, qty: float, broker: Dict[str, Any], origin: str = "") -> Decision:
    """Pure decision for an exposure-INCREASING order. broker = {"amt": abs same-side qty, "entry": avg entry, "mark": mark}."""
    cfg = guard.cfg
    tok = _ORDER_AUTH.get()
    sym = symbol.upper()
    if tok is None or (tok.get("symbol") and tok.get("symbol") != sym):
        if bool(_cfg(cfg, "WIRE_REQUIRE_EXECUTE_NOW")):
            return Decision(False, "NOT_VIA_EXECUTE_NOW", {"token": {k: (tok or {}).get(k) for k in ("symbol", "action", "reason")}, "origin": origin[:120]})
        guard.log.critical(f"⚠️ [WIRE_NO_TOKEN] {guard.broker}:{guard.account}:{sym} increasing order outside execute_now allowed by config")
        tok = {"action": "", "keys": {}, "orders": []}
    key = f"{sym}|{str(position_side or '').upper()}"
    amt = abs(_f(broker.get("amt")))
    st = tok.setdefault("keys", {}).get(key)
    action = str(tok.get("action") or "").upper()
    ev = {"broker_amt": amt, "broker_entry": broker.get("entry"), "mark": broker.get("mark"), "action": action, "requested": qty, "reason": str(tok.get("reason") or "")[:100]}
    if st is None:  # first increasing leg of this execute_now call
        if amt > 1e-12:
            if action in PURE_OPEN_ACTIONS and bool(_cfg(cfg, "WIRE_REFUSE_OPEN_ON_NONZERO")):
                return Decision(False, "OPEN_ON_NONZERO_POSITION", ev)
            floor = _augment_floor(cfg)
            entry = _f(broker.get("entry"))
            last = _last_fill_price(guard, sym, side, position_side)
            refs = [r for r in (entry, last) if r and r > 0]
            if not refs:
                return Decision(False, "AUGMENT_REF_PRICE_UNKNOWN", ev)
            ref = max(refs) if is_long else min(refs)
            gain = _gain_pct(is_long, ref, _f(broker.get("mark")))
            ev.update({"ref_price": ref, "last_fill": last, "gain_pct": gain, "floor_pct": floor})
            if gain is None:
                return Decision(False, "AUGMENT_MARK_UNKNOWN", ev)
            if gain < floor:
                return Decision(False, "AUGMENT_GAIN_GATE", ev)
        tok["keys"][key] = {"baseline": amt, "intent": float(qty), "first_ts": time.time()}
        return Decision(True, "OPEN_FROM_ZERO" if amt <= 1e-12 else "AUGMENT_OK", ev)
    # follow-up leg in the same call: only the remainder of the first leg's intent
    delta = max(0.0, amt - _f(st.get("baseline")))
    ledger_filled = 0.0
    try:
        ids = set(tok.get("orders") or [])
        ledger_filled = sum(_f(o.get("executed_qty")) for o in guard.ledger.orders(sym) if (o.get("client_order_id") or o.get("local_id")) in ids)
    except Exception:
        pass
    filled = max(delta, ledger_filled)
    intent = _f(st.get("intent"))
    remaining = intent - filled
    ev.update({"intent": intent, "broker_delta": delta, "ledger_filled": ledger_filled, "remaining": remaining})
    if remaining <= max(1e-12, intent * 0.001):
        return Decision(False, "INTENT_FILLED_IN_CALL", ev)
    if qty > remaining * 1.0000001:
        return Decision(True, "CLAMP", ev, clamp_qty=remaining)
    return Decision(True, "FOLLOWUP_OK", ev)


def _remember_order(ident: Optional[str]) -> None:
    tok = _ORDER_AUTH.get()
    if tok is not None and ident:
        tok.setdefault("orders", []).append(ident)


# ───────────────────────────── Binance futures ─────────────────────────────
class BinanceDedupeGuard(_GuardBase):
    broker = "binance"
    final_set = BINANCE_FINAL

    def _order_ev(self, o: Dict[str, Any]) -> Dict[str, Any]:
        return {k: o.get(k) for k in ("orderId", "clientOrderId", "status", "side", "positionSide", "type", "origQty", "executedQty", "price", "time", "updateTime") if k in o}

    def _query(self, client, symbol: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        if entry.get("order_id"):
            return client.futures_get_order(symbol=symbol, orderId=int(entry["order_id"]))
        return client.futures_get_order(symbol=symbol, origClientOrderId=entry["client_order_id"])

    def _check_once(self, client, symbol: str, position_side: Optional[str]) -> tuple:
        """Returns (decision_or_None, own_cancel_pending: bool). None decision = clean."""
        sym = symbol.upper()
        scope = str(_cfg(self.cfg, "ORDER_DEDUPE_SCOPE"))
        ignore = {str(t).upper() for t in (_cfg(self.cfg, "ORDER_DEDUPE_IGNORE_ORDER_TYPES") or ())}
        now = self.clock()
        cancel_recent = float(_cfg(self.cfg, "ORDER_DEDUPE_CANCEL_RECENT_SEC"))
        ledger_rows = self.ledger.orders(sym)
        own_cancel_ids = {str(o.get("order_id")) for o in ledger_rows if o.get("cancel_requested_at") and now - _f(o.get("cancel_requested_at")) < cancel_recent}
        own_cancel_ids |= {str(o.get("client_order_id")) for o in ledger_rows if o.get("cancel_requested_at") and now - _f(o.get("cancel_requested_at")) < cancel_recent}
        # (a) broker open orders on the symbol
        try:
            oo = client.futures_get_open_orders(symbol=sym)
        except Exception as e:
            return Decision(False, "BROKER_UNREACHABLE_OPEN_ORDERS", {"error": repr(e)[:300]}), False
        if not isinstance(oo, list):
            return Decision(False, "BROKER_OPEN_ORDERS_UNPARSEABLE", {"raw": str(oo)[:300]}), False
        live = []
        for o in oo:
            if str(o.get("symbol", "")).upper() != sym:
                continue
            if str(o.get("type", "")).upper() in ignore:
                continue
            if scope == "position_side" and position_side and str(o.get("positionSide", "")).upper() not in ("", "BOTH", str(position_side).upper()):
                continue
            live.append(o)
        if live:
            own = all(str(o.get("orderId")) in own_cancel_ids or str(o.get("clientOrderId")) in own_cancel_ids for o in live)
            return Decision(False, "OPEN_ORDER_EXISTS", {"open_orders": [self._order_ev(o) for o in live]}), own
        # (b) every non-final order this system placed on the symbol
        grace = float(_cfg(self.cfg, "ORDER_DEDUPE_NOTFOUND_GRACE_SEC"))
        own_pending = False
        for entry in [o for o in ledger_rows if not o.get("final")]:
            ident = {"order_id": entry.get("order_id"), "client_order_id": entry.get("client_order_id"), "state": entry.get("state"), "submitted_at": entry.get("submitted_at")}
            if not entry.get("order_id") and not entry.get("client_order_id"):
                return Decision(False, "LAST_ORDER_UNIDENTIFIABLE", ident), False
            try:
                resp = self._query(client, sym, entry)
            except Exception as e:
                code = getattr(e, "code", None)
                age = now - _f(entry.get("submitted_at"), now)
                if code == BINANCE_ORDER_NOT_FOUND and age >= grace:
                    self._mark(sym, entry, status="NOT_FOUND", executed_qty=0.0, final=True, state="FINAL", evidence=f"broker -2013 after {age:.0f}s")
                    continue
                return Decision(False, "LAST_ORDER_STATUS_UNKNOWN", {**ident, "error": repr(e)[:300], "age_s": round(age, 1)}), False
            st = str((resp or {}).get("status", "")).upper()
            exq = (resp or {}).get("executedQty")
            if st in BINANCE_FINAL and exq is not None:
                self._mark(sym, entry, status=st, executed_qty=_f(exq), final=True, state="FINAL", order_id=resp.get("orderId") or entry.get("order_id"), avg_price=_f(resp.get("avgPrice"), 0.0))
                continue
            is_own_cancel = bool(entry.get("cancel_requested_at")) and now - _f(entry.get("cancel_requested_at")) < cancel_recent
            own_pending = own_pending or is_own_cancel
            return Decision(False, "LAST_ORDER_NOT_FINAL", {**ident, "broker": self._order_ev(resp or {})}), is_own_cancel
        return None, own_pending

    def _mark(self, symbol: str, entry: Dict[str, Any], **fields) -> None:
        cid = entry.get("client_order_id")
        oid = entry.get("order_id")
        self.ledger.update_order(symbol, lambda o: (cid and o.get("client_order_id") == cid) or (oid and str(o.get("order_id")) == str(oid)), **fields)

    def preflight(self, client, symbol: str, position_side: Optional[str] = None, origin: str = "", use_backoff: bool = True) -> Decision:
        if not self.enabled():
            self.log.critical(f"⚠️ [ORDER_DEDUPE_DISABLED] {self.account}:{symbol} guard disabled by config — order NOT broker-verified ({origin[:60]})")
            return Decision(True, "DISABLED")
        if client is None:
            return self._block(symbol, "NO_BROKER_CLIENT", {}, origin)
        if use_backoff:
            hit = self._backoff_hit(symbol)
            if hit:
                return hit
        deadline = self.clock() + float(_cfg(self.cfg, "ORDER_DEDUPE_CONFIRM_WAIT_SEC"))
        while True:
            try:
                dec, own_pending = self._check_once(client, symbol, position_side)
            except Exception as e:  # corrupt ledger etc.
                return self._block(symbol, "GUARD_INTERNAL_ERROR", {"error": repr(e)[:300]}, origin)
            if dec is None:
                self._backoff.pop(symbol.upper(), None)
                return Decision(True)
            if own_pending and self.clock() < deadline:
                time.sleep(0.25)
                continue
            self._cancel_own_stale(client, symbol, dec)
            return self._block(symbol, dec.code, dec.evidence, origin)

    def _cancel_own_stale(self, client, symbol: str, dec: Decision) -> None:
        """Cancel-and-confirm (step 1 of 2): cancel OUR OWN stale working orders; this call still BLOCKS, the next decision
        cycle's preflight must see the broker confirm CANCELED (with filled qty) before anything is sent."""
        after = float(_cfg(self.cfg, "ORDER_DEDUPE_AUTO_CANCEL_OWN_AFTER_SEC") or 0)
        if after <= 0 or dec.code not in ("OPEN_ORDER_EXISTS", "LAST_ORDER_NOT_FINAL"):
            return
        now = self.clock()
        rows = self.ledger.orders(symbol)
        cands = list(dec.evidence.get("open_orders") or [])
        if dec.evidence.get("broker"):
            cands.append(dec.evidence["broker"])
        for o in cands:
            cid, oid = o.get("clientOrderId"), o.get("orderId")
            entry = next((r for r in rows if (cid and r.get("client_order_id") == cid) or (oid is not None and str(r.get("order_id")) == str(oid))), None)
            if entry is None:
                continue  # foreign order (manual / other system) — never auto-cancelled
            if now - _f(entry.get("submitted_at"), now) < after:
                continue
            if entry.get("cancel_requested_at") and now - _f(entry.get("cancel_requested_at")) < after:
                continue
            try:
                self._mark(symbol, entry, cancel_requested_at=now)
                kw = {"orderId": int(oid)} if oid is not None else {"origClientOrderId": cid}
                resp = client.futures_cancel_order(symbol=symbol.upper(), **kw)
                self.observe(symbol.upper(), resp, order_id=oid, client_order_id=cid)
                dec.evidence["auto_cancel"] = {"orderId": oid, "clientOrderId": cid, "broker_status": (resp or {}).get("status")}
                self.log.critical(f"🧹 [ORDER_DEDUPE_AUTO_CANCEL] binance:{self.account}:{symbol} own stale order {oid or cid} cancel sent -> {(resp or {}).get('status')} (next cycle must confirm FINAL)")
            except Exception as e:
                dec.evidence["auto_cancel_error"] = repr(e)[:200]

    # wire-level submit -------------------------------------------------------
    def new_client_order_id(self) -> str:
        return f"odg{self.account[:4]}{int(time.time() * 1000) % 10**11}{uuid.uuid4().hex[:8]}"[:36]

    def guarded_create(self, inner_client, params: Dict[str, Any], origin: str = "") -> Dict[str, Any]:
        symbol = str(params.get("symbol", "")).upper()
        side = str(params.get("side", "")).upper()
        ps = str(params.get("positionSide", "") or "").upper()
        with self.key_lock(symbol):
            dec = self.preflight(inner_client, symbol, ps, origin=origin or f"create {side}/{ps}")
            if not dec.allowed:
                raise OrderDedupeBlockedBinance(dec)
            qty_raw = params.get("quantity")
            qty = _f(qty_raw, 0.0)
            closing_flag = str(params.get("closePosition", "")).lower() == "true" or params.get("closePosition") is True
            if bool(_cfg(self.cfg, "WIRE_REFUSE_BAD_QTY")) and not closing_flag and _bad_qty(qty_raw):
                raise OrderDedupeBlockedBinance(self._block(symbol, "BAD_QTY", {"quantity": str(qty_raw)}, origin))
            if bool(_cfg(self.cfg, "WIRE_EXPOSURE_GATE_ENABLED")):
                edec = self.exposure_gate(inner_client, symbol, side, ps, qty, params, origin)
                if not edec.allowed:
                    raise OrderDedupeBlockedBinance(self._block(symbol, edec.code, edec.evidence, origin))
                if edec.clamp_qty is not None:
                    new_q = _fmt_like(qty_raw, edec.clamp_qty)
                    if _f(new_q, 0.0) <= 0:
                        raise OrderDedupeBlockedBinance(self._block(symbol, "INTENT_REMAINDER_ZERO", edec.evidence, origin))
                    self.log.warning(f"✂️ [WIRE_EXPOSURE_CLAMP] binance:{self.account}:{symbol} {side}/{ps} qty {qty_raw} -> {new_q} {edec.evidence}")
                    params["quantity"] = new_q
                    qty_raw = new_q
                    qty = _f(new_q)
            else:
                self.log.critical(f"⚠️ [WIRE_EXPOSURE_GATE_DISABLED] binance:{self.account}:{symbol} — open-from-zero / execute_now token NOT enforced")
            if dec.code != "DISABLED" and qty > 0:
                idec = self.intent_check(symbol, side, ps, qty)
                if not idec.allowed:
                    raise OrderDedupeBlockedBinance(self._block(symbol, idec.code, idec.evidence, origin))
                if idec.clamp_qty is not None:
                    new_q = _fmt_like(qty_raw, idec.clamp_qty)
                    if _f(new_q, 0.0) <= 0:
                        raise OrderDedupeBlockedBinance(self._block(symbol, "INTENT_REMAINDER_ZERO", idec.evidence, origin))
                    self.log.warning(f"✂️ [ORDER_DEDUPE_CLAMP] binance:{self.account}:{symbol} {side}/{ps} qty {qty_raw} -> {new_q} (broker-confirmed fills {idec.evidence})")
                    params["quantity"] = new_q
                    qty = _f(new_q)
            cid = params.get("newClientOrderId") or self.new_client_order_id()
            params["newClientOrderId"] = cid
            entry = {"client_order_id": cid, "order_id": None, "side": side, "position_side": ps, "qty": qty, "type": str(params.get("type", "")), "submitted_at": self.clock(), "state": "SUBMITTING", "status": None, "executed_qty": 0.0, "final": False, "origin": origin[:120]}
            self.ledger.mutate(symbol, lambda orders: orders.append(entry))
            _remember_order(cid)
        try:
            resp = _raw_binance_create(inner_client, params)
        except Exception as e:
            code = getattr(e, "code", None)
            status_code = getattr(e, "status_code", None)
            definitive = code is not None and code not in BINANCE_UNKNOWN_CODES and isinstance(status_code, int) and 400 <= status_code < 500 and code != 0
            if definitive:
                self._mark(symbol, entry, state="FINAL", status="REJECTED_BY_BROKER", final=True, executed_qty=0.0, evidence=f"code={code} {str(e)[:160]}")
            else:
                self._mark(symbol, entry, state="UNKNOWN", evidence=repr(e)[:200])
                self.log.critical(f"🚨 [ORDER_DEDUPE_UNKNOWN] binance:{self.account}:{symbol} submit raised {e!r} — status UNKNOWN; key frozen until broker confirms cid={cid}")
            raise
        st = str((resp or {}).get("status", "")).upper()
        exq = (resp or {}).get("executedQty")
        final = st in BINANCE_FINAL and exq is not None
        self._mark(symbol, entry, state="FINAL" if final else "SUBMITTED", order_id=(resp or {}).get("orderId"), status=st or None, executed_qty=_f(exq, 0.0), final=final, avg_price=_f((resp or {}).get("avgPrice"), 0.0))
        return resp

    def broker_position(self, inner_client, symbol: str, position_side: str) -> Dict[str, Any]:
        """FRESH broker read (positionRisk for the symbol). Raises on failure — callers fail closed."""
        rows = inner_client.futures_position_information(symbol=symbol.upper())
        if not isinstance(rows, list):
            raise RuntimeError(f"positionRisk unparseable: {str(rows)[:200]}")
        ps = str(position_side or "").upper() or "BOTH"
        for r in rows:
            if str(r.get("symbol", "")).upper() != symbol.upper():
                continue
            if str(r.get("positionSide", "BOTH")).upper() == ps:
                return {"signed": _f(r.get("positionAmt")), "amt": abs(_f(r.get("positionAmt"))), "entry": _f(r.get("entryPrice")), "mark": _f(r.get("markPrice"))}
        return {"signed": 0.0, "amt": 0.0, "entry": 0.0, "mark": 0.0}

    def exposure_gate(self, inner_client, symbol: str, side: str, ps: str, qty: float, params: Dict[str, Any], origin: str = "") -> Decision:
        reduce_only = str(params.get("reduceOnly", "")).lower() == "true" or params.get("reduceOnly") is True
        close_pos = str(params.get("closePosition", "")).lower() == "true" or params.get("closePosition") is True
        if reduce_only or close_pos:
            return Decision(True, "DECREASING")
        hedge_side = ps in ("LONG", "SHORT")
        if hedge_side and not ((ps == "LONG" and side == "BUY") or (ps == "SHORT" and side == "SELL")):
            if _ORDER_AUTH.get() is None:
                self.log.critical(f"⚠️ [ORDER_OUTSIDE_EXECUTE_NOW] binance:{self.account}:{symbol} decreasing {side}/{ps} qty={qty} origin={origin[:80]} — allowed (exits never stranded), route it through execute_now")
            return Decision(True, "DECREASING")
        try:
            bp = self.broker_position(inner_client, symbol, ps)
        except Exception as e:
            return Decision(False, "BROKER_POSITION_UNKNOWN", {"error": repr(e)[:300]})
        if not hedge_side:  # one-way mode: increasing iff flat or same sign
            signed = bp["signed"]
            if signed != 0 and ((signed > 0) != (side == "BUY")):
                return Decision(True, "DECREASING")
        is_long = (ps == "LONG") if hedge_side else (side == "BUY")
        return exposure_decision(self, symbol, side, ps, is_long, qty, bp, origin)

    def observe(self, symbol: str, resp: Any, cancel_requested: bool = False, order_id=None, client_order_id=None) -> None:
        """Record broker-returned order state (cancel / get_order responses)."""
        try:
            oid = (resp or {}).get("orderId") if isinstance(resp, dict) else None
            oid = oid or order_id
            cid = ((resp or {}).get("clientOrderId") if isinstance(resp, dict) else None) or client_order_id
            match = lambda o: (oid is not None and str(o.get("order_id")) == str(oid)) or (cid and o.get("client_order_id") == cid)  # noqa: E731
            if cancel_requested:
                self.ledger.update_order(symbol, match, cancel_requested_at=self.clock())
            if isinstance(resp, dict) and resp.get("status"):
                st = str(resp.get("status")).upper()
                exq = resp.get("executedQty")
                final = st in BINANCE_FINAL and exq is not None
                fields = {"status": st, "executed_qty": _f(exq, 0.0)}
                if _f(resp.get("avgPrice"), 0.0) > 0:
                    fields["avg_price"] = _f(resp.get("avgPrice"))
                if final:
                    fields.update(final=True, state="FINAL")
                if oid is not None:
                    fields["order_id"] = oid
                self.ledger.update_order(symbol, match, **fields)
        except Exception as e:
            self.log.warning(f"[ORDER_DEDUPE_OBSERVE] {symbol}: {e!r}")


def _d(x: Any) -> Decimal:
    try:
        return Decimal(str(x if x is not None else 0))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _fmt_like(raw: Any, value: float) -> str:
    """Format value (rounded DOWN) with the same number of decimals as the caller's already step-quantized quantity."""
    try:
        exp = Decimal(str(raw)).as_tuple().exponent
        places = -exp if isinstance(exp, int) and exp < 0 else 0
        q = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)
        return f"{q:f}"
    except (InvalidOperation, ValueError):
        return str(value)


_ORIG_BINANCE_CREATE: Optional[Callable] = None
_ORIG_BINANCE_BATCH: Optional[Callable] = None
_BINANCE_KEY_ACCOUNTS: Dict[str, str] = {}


def _raw_binance_create(client, params: Dict[str, Any]):
    """The ONLY call that reaches python-binance's real futures_create_order (unpatched original when the class guard is installed)."""
    if _ORIG_BINANCE_CREATE is not None and not isinstance(client, GuardedBinanceClient) and isinstance(client, _binance_client_cls() or ()):
        return _ORIG_BINANCE_CREATE(client, **params)
    return client.futures_create_order(**params)


def _binance_client_cls():
    try:
        from binance.client import Client as _C  # noqa: WPS433
        return _C
    except Exception:
        return None


def _binance_account_for(client) -> str:
    acct = getattr(client, "_odg_account", None)
    if acct:
        return acct
    key = str(getattr(client, "API_KEY", "") or "")
    if key in _BINANCE_KEY_ACCOUNTS:
        return _BINANCE_KEY_ACCOUNTS[key]
    return "key_" + hashlib.sha1(key.encode()).hexdigest()[:10]


def install_binance_class_guard(cfg: Any = None, logger: Optional[logging.Logger] = None) -> bool:
    """Patch python-binance Client so EVERY instance in this process (raw Client() objects in helpers included) goes through
    the guard: futures_create_order -> guarded_create, futures_place_batch_order -> refused. Idempotent."""
    global _ORIG_BINANCE_CREATE, _ORIG_BINANCE_BATCH
    C = _binance_client_cls()
    if C is None or _ORIG_BINANCE_CREATE is not None:
        return _ORIG_BINANCE_CREATE is not None
    _ORIG_BINANCE_CREATE = C.futures_create_order
    _ORIG_BINANCE_BATCH = getattr(C, "futures_place_batch_order", None)

    def futures_create_order(self, **params):
        g = get_binance_guard(_binance_account_for(self), cfg, logger)
        return g.guarded_create(self, params, origin="class:" + str(params.get("newClientOrderId") or ""))

    def futures_place_batch_order(self, **params):
        g = get_binance_guard(_binance_account_for(self), cfg, logger)
        raise OrderDedupeBlockedBinance(g._block(str(params.get("symbol", "?")), "BATCH_ORDERS_NOT_ALLOWED", {}, "class-batch"))

    futures_create_order.__odg_patched__ = True
    C.futures_create_order = futures_create_order
    C.futures_place_batch_order = futures_place_batch_order
    return True


class GuardedBinanceClient:
    """Transparent proxy over python-binance Client. Only order-mutating calls are intercepted."""

    def __init__(self, inner, guard: BinanceDedupeGuard):
        object.__setattr__(self, "_odg_inner", inner)
        object.__setattr__(self, "_odg_guard", guard)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_odg_inner"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_odg_inner"), name, value)

    @property
    def odg_inner(self):
        return object.__getattribute__(self, "_odg_inner")

    def futures_create_order(self, **params):
        return self._odg_guard.guarded_create(self._odg_inner, params, origin=str(params.get("newClientOrderId") or ""))

    def futures_place_batch_order(self, **params):
        raise OrderDedupeBlockedBinance(self._odg_guard._block(str(params.get("symbol", "?")), "BATCH_ORDERS_NOT_ALLOWED", {}, "batch"))

    def futures_cancel_order(self, **params):
        sym = str(params.get("symbol", "")).upper()
        self._odg_guard.observe(sym, None, cancel_requested=True, order_id=params.get("orderId"), client_order_id=params.get("origClientOrderId"))
        resp = self._odg_inner.futures_cancel_order(**params)
        self._odg_guard.observe(sym, resp, order_id=params.get("orderId"), client_order_id=params.get("origClientOrderId"))
        return resp

    def futures_get_order(self, **params):
        resp = self._odg_inner.futures_get_order(**params)
        self._odg_guard.observe(str(params.get("symbol", "")).upper(), resp, order_id=params.get("orderId"), client_order_id=params.get("origClientOrderId"))
        return resp


_BINANCE_GUARDS: Dict[str, BinanceDedupeGuard] = {}
_TRADIER_GUARDS: Dict[str, "TradierDedupeGuard"] = {}


def get_binance_guard(account: str, cfg: Any = None, logger: Optional[logging.Logger] = None, ledger_dir: Optional[Path] = None) -> BinanceDedupeGuard:
    g = _BINANCE_GUARDS.get(account)
    if g is None:
        g = _BINANCE_GUARDS[account] = BinanceDedupeGuard(account, cfg=cfg, logger=logger, ledger_dir=ledger_dir)
    else:
        if cfg is not None and g.cfg is None:
            g.cfg = cfg
        if logger is not None and g.log is _LOG:
            g.log = logger
    return g


def wrap_binance_client(client, account: str, cfg: Any = None, logger: Optional[logging.Logger] = None, ledger_dir: Optional[Path] = None):
    if client is None or isinstance(client, GuardedBinanceClient):
        return client
    try:
        key = str(getattr(client, "API_KEY", "") or "")
        if key:
            _BINANCE_KEY_ACCOUNTS[key] = account
        client._odg_account = account
    except Exception:
        pass
    return GuardedBinanceClient(client, get_binance_guard(account, cfg, logger, ledger_dir))


async def binance_execute_now_preflight(client, account: str, symbol: str, position_side: Optional[str], origin: str = "", cfg: Any = None, logger: Optional[logging.Logger] = None) -> Decision:
    """execute_now-level early refusal (the wire-level proxy re-checks + claims atomically at submit)."""
    inner = client.odg_inner if isinstance(client, GuardedBinanceClient) else client
    guard = client._odg_guard if isinstance(client, GuardedBinanceClient) else get_binance_guard(account, cfg, logger)
    try:
        return await asyncio.wait_for(asyncio.to_thread(guard.preflight, inner, str(symbol).upper(), position_side, origin), timeout=float(_cfg(cfg, "ORDER_DEDUPE_QUERY_TIMEOUT_SEC")) + float(_cfg(cfg, "ORDER_DEDUPE_CONFIRM_WAIT_SEC")))
    except Exception as e:
        return guard._block(str(symbol), "PREFLIGHT_TIMEOUT_OR_ERROR", {"error": repr(e)[:300]}, origin)


def ledger_has_nonfinal(broker: str, account: str, symbol: str, ledger_dir: Optional[Path] = None) -> bool:
    """True = an order on this key may still be live → callers must NOT release their exec lock. Errors → True (fail closed)."""
    try:
        g = (_BINANCE_GUARDS if broker == "binance" else _TRADIER_GUARDS).get(account)
        led = g.ledger if g else OrderLedger(broker, account, ledger_dir)
        return bool(led.nonfinal(str(symbol).upper()))
    except Exception:
        return True


# ───────────────────────────── Tradier ─────────────────────────────
def _parse_ts(s: Any) -> Optional[float]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class TradierDedupeGuard(_GuardBase):
    broker = "tradier"
    final_set = TRADIER_FINAL

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._alocks: Dict[str, asyncio.Lock] = {}

    def akey_lock(self, symbol: str) -> asyncio.Lock:
        return self._alocks.setdefault(symbol.upper(), asyncio.Lock())

    async def _list_orders(self, client) -> List[Dict[str, Any]]:
        """Raw listing that DISTINGUISHES 'no orders' from 'query failed' (TradierAPIClient.get_orders returns [] for both)."""
        acct_id = getattr(client, "_current_id", None)
        if not acct_id:
            raise RuntimeError("tradier client has no account id")
        res = await asyncio.wait_for(client._request("GET", f"/accounts/{acct_id}/orders", use_data_context=False), timeout=float(_cfg(self.cfg, "ORDER_DEDUPE_QUERY_TIMEOUT_SEC")))
        if not isinstance(res, dict) or "orders" not in res:
            raise RuntimeError(f"orders query failed/unparseable: {str(res)[:200]}")
        inner = res["orders"]
        if inner in ("null", None):
            return []
        if isinstance(inner, dict) and "order" in inner:
            o = inner["order"]
            return o if isinstance(o, list) else [o]
        raise RuntimeError(f"orders payload unparseable: {str(inner)[:200]}")

    async def _get_order(self, client, order_id) -> Dict[str, Any]:
        acct_id = getattr(client, "_current_id", None)
        res = await asyncio.wait_for(client._request("GET", f"/accounts/{acct_id}/orders/{order_id}", use_data_context=False), timeout=float(_cfg(self.cfg, "ORDER_DEDUPE_QUERY_TIMEOUT_SEC")))
        if isinstance(res, dict) and isinstance(res.get("order"), dict):
            return res["order"]
        raise RuntimeError(f"order {order_id} query failed/unparseable: {str(res)[:200]}")

    def _order_ev(self, o: Dict[str, Any]) -> Dict[str, Any]:
        return {k: o.get(k) for k in ("id", "symbol", "side", "type", "status", "quantity", "exec_quantity", "remaining_quantity", "create_date", "transaction_date", "tag") if k in o}

    def _mark(self, symbol: str, entry: Dict[str, Any], **fields) -> None:
        local = entry.get("local_id")
        self.ledger.update_order(symbol, lambda o: o.get("local_id") == local, **fields)

    async def _check_once(self, client, symbol: str) -> tuple:
        sym = symbol.upper()
        now = self.clock()
        cancel_recent = float(_cfg(self.cfg, "ORDER_DEDUPE_CANCEL_RECENT_SEC"))
        ignore = {str(t).lower() for t in (_cfg(self.cfg, "ORDER_DEDUPE_IGNORE_ORDER_TYPES") or ())}
        rows = self.ledger.orders(sym)
        own_cancel_ids = {str(o.get("order_id")) for o in rows if o.get("cancel_requested_at") and now - _f(o.get("cancel_requested_at")) < cancel_recent}
        try:
            orders = await self._list_orders(client)
        except Exception as e:
            return Decision(False, "BROKER_UNREACHABLE_ORDERS", {"error": repr(e)[:300]}), False
        sym_orders = [o for o in orders if str(o.get("symbol", "")).upper() == sym]
        live = [o for o in sym_orders if str(o.get("status", "")).lower() not in TRADIER_FINAL and str(o.get("type", "")).lower() not in ignore]
        if live:
            own = all(str(o.get("id")) in own_cancel_ids for o in live)
            return Decision(False, "OPEN_ORDER_EXISTS", {"open_orders": [self._order_ev(o) for o in live]}), own
        by_id = {str(o.get("id")): o for o in sym_orders}
        claimed = {str(o.get("order_id")) for o in rows if o.get("order_id")}
        grace = float(_cfg(self.cfg, "ORDER_DEDUPE_NOTFOUND_GRACE_SEC"))
        for entry in [o for o in rows if not o.get("final")]:
            ident = {"order_id": entry.get("order_id"), "local_id": entry.get("local_id"), "state": entry.get("state"), "submitted_at": entry.get("submitted_at")}
            age = now - _f(entry.get("submitted_at"), now)
            bo = None
            if entry.get("order_id"):
                bo = by_id.get(str(entry["order_id"]))
                if bo is None:
                    try:
                        bo = await self._get_order(client, entry["order_id"])
                    except Exception as e:
                        return Decision(False, "LAST_ORDER_STATUS_UNKNOWN", {**ident, "error": repr(e)[:300]}), False
            else:
                # submit result unknown (timeout / gateway error): adopt any unclaimed same-side order for the symbol created around the submit
                t0 = _f(entry.get("submitted_at"), now) - 15.0
                cands = [o for o in sym_orders if str(o.get("id")) not in claimed and str(o.get("side", "")).lower() == str(entry.get("side", "")).lower() and (_parse_ts(o.get("create_date")) or 0) >= t0]
                if cands:
                    cands.sort(key=lambda o: _parse_ts(o.get("create_date")) or 0)
                    extra = cands[1:]
                    bo = cands[0]
                    self._mark(sym, entry, order_id=bo.get("id"), adopted=True, adopted_extra=[o.get("id") for o in extra])
                    if extra:
                        self.log.critical(f"🚨 [ORDER_DEDUPE_MULTI_ADOPT] tradier:{self.account}:{sym} unknown submit matched {len(cands)} broker orders {[o.get('id') for o in cands]} — transport retried a POST")
                elif age >= grace:
                    self._mark(sym, entry, status="not_found", executed_qty=0.0, final=True, state="FINAL", evidence=f"no broker order after {age:.0f}s")
                    continue
                else:
                    return Decision(False, "LAST_ORDER_STATUS_UNKNOWN", {**ident, "age_s": round(age, 1), "note": "submit outcome unknown; waiting for broker listing"}), False
            st = str(bo.get("status", "")).lower()
            exq = bo.get("exec_quantity")
            if st in TRADIER_FINAL and exq is not None:
                self._mark(sym, entry, status=st, executed_qty=_f(exq), final=True, state="FINAL", avg_price=_f(bo.get("avg_fill_price"), 0.0))
                continue
            is_own_cancel = bool(entry.get("cancel_requested_at")) and now - _f(entry.get("cancel_requested_at")) < cancel_recent
            return Decision(False, "LAST_ORDER_NOT_FINAL", {**ident, "broker": self._order_ev(bo)}), is_own_cancel
        return None, False

    async def preflight(self, client, symbol: str, origin: str = "", use_backoff: bool = True) -> Decision:
        if not self.enabled():
            self.log.critical(f"⚠️ [ORDER_DEDUPE_DISABLED] tradier:{self.account}:{symbol} guard disabled by config — order NOT broker-verified ({origin[:60]})")
            return Decision(True, "DISABLED")
        if client is None:
            return self._block(symbol, "NO_BROKER_CLIENT", {}, origin)
        if use_backoff:
            hit = self._backoff_hit(symbol)
            if hit:
                return hit
        deadline = self.clock() + float(_cfg(self.cfg, "ORDER_DEDUPE_CONFIRM_WAIT_SEC"))
        while True:
            try:
                dec, own_pending = await self._check_once(client, symbol)
            except Exception as e:
                return self._block(symbol, "GUARD_INTERNAL_ERROR", {"error": repr(e)[:300]}, origin)
            if dec is None:
                self._backoff.pop(symbol.upper(), None)
                return Decision(True)
            if own_pending and self.clock() < deadline:
                await asyncio.sleep(0.5)
                continue
            await self._cancel_own_stale(client, symbol, dec)
            return self._block(symbol, dec.code, dec.evidence, origin)

    async def _cancel_own_stale(self, client, symbol: str, dec: Decision) -> None:
        after = float(_cfg(self.cfg, "ORDER_DEDUPE_AUTO_CANCEL_OWN_AFTER_SEC") or 0)
        if after <= 0 or dec.code not in ("OPEN_ORDER_EXISTS", "LAST_ORDER_NOT_FINAL"):
            return
        now = self.clock()
        rows = self.ledger.orders(symbol)
        cands = list(dec.evidence.get("open_orders") or [])
        if dec.evidence.get("broker"):
            cands.append(dec.evidence["broker"])
        for o in cands:
            oid = o.get("id")
            entry = next((r for r in rows if oid is not None and str(r.get("order_id")) == str(oid)), None)
            if entry is None or now - _f(entry.get("submitted_at"), now) < after:
                continue
            if entry.get("cancel_requested_at") and now - _f(entry.get("cancel_requested_at")) < after:
                continue
            try:
                self._mark(symbol, entry, cancel_requested_at=now)
                await asyncio.wait_for(client.cancel_order(self.account, oid), timeout=float(_cfg(self.cfg, "ORDER_DEDUPE_QUERY_TIMEOUT_SEC")))
                dec.evidence["auto_cancel"] = {"id": oid}
                self.log.critical(f"🧹 [ORDER_DEDUPE_AUTO_CANCEL] tradier:{self.account}:{symbol} own stale order {oid} cancel sent (next cycle must confirm FINAL)")
            except Exception as e:
                dec.evidence["auto_cancel_error"] = repr(e)[:200]

    async def guarded_place(self, inner_client, account_key: str, symbol: str, side: str, quantity: float, order_type: str = "market", price: float = None, stop: float = None, duration: str = "day", origin: str = "") -> Dict[str, Any]:
        sym = str(symbol).upper()
        async with self.akey_lock(sym):
            dec = await self.preflight(inner_client, sym, origin=origin or f"place {side} {order_type}")
            if not dec.allowed:
                return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{dec.code}"]}, "order_dedupe_block": {"code": dec.code, "evidence": dec.evidence}}
            qty = _f(quantity, 0.0)
            if bool(_cfg(self.cfg, "WIRE_REFUSE_BAD_QTY")) and (_bad_qty(quantity) or int(qty) < 1):
                d2 = self._block(sym, "BAD_QTY", {"quantity": str(quantity)}, origin)
                return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{d2.code}"]}, "order_dedupe_block": {"code": d2.code, "evidence": d2.evidence}}
            if bool(_cfg(self.cfg, "WIRE_EXPOSURE_GATE_ENABLED")):
                edec = await self.exposure_gate(inner_client, sym, side, qty, origin)
                if not edec.allowed:
                    d2 = self._block(sym, edec.code, edec.evidence, origin)
                    return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{d2.code}"]}, "order_dedupe_block": {"code": d2.code, "evidence": d2.evidence}}
                if edec.clamp_qty is not None:
                    new_q = float(int(edec.clamp_qty))
                    if new_q < 1:
                        d2 = self._block(sym, "INTENT_REMAINDER_ZERO", edec.evidence, origin)
                        return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{d2.code}"]}, "order_dedupe_block": {"code": d2.code, "evidence": d2.evidence}}
                    self.log.warning(f"✂️ [WIRE_EXPOSURE_CLAMP] tradier:{self.account}:{sym} {side} qty {quantity} -> {new_q} {edec.evidence}")
                    quantity = new_q
                    qty = new_q
            else:
                self.log.critical(f"⚠️ [WIRE_EXPOSURE_GATE_DISABLED] tradier:{self.account}:{sym} — open-from-zero / execute_now token NOT enforced")
            if dec.code != "DISABLED" and qty > 0:
                idec = self.intent_check(sym, side, "", qty)
                if not idec.allowed:
                    d2 = self._block(sym, idec.code, idec.evidence, origin)
                    return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{d2.code}"]}, "order_dedupe_block": {"code": d2.code, "evidence": d2.evidence}}
                if idec.clamp_qty is not None:
                    new_q = float(int(idec.clamp_qty))
                    if new_q <= 0:
                        d2 = self._block(sym, "INTENT_REMAINDER_ZERO", idec.evidence, origin)
                        return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{d2.code}"]}, "order_dedupe_block": {"code": d2.code, "evidence": d2.evidence}}
                    self.log.warning(f"✂️ [ORDER_DEDUPE_CLAMP] tradier:{self.account}:{sym} {side} qty {quantity} -> {new_q} (broker-confirmed fills {idec.evidence})")
                    quantity = new_q
                    qty = new_q
            entry = {"local_id": uuid.uuid4().hex, "order_id": None, "side": str(side).lower(), "position_side": "", "qty": qty, "type": order_type, "submitted_at": self.clock(), "state": "SUBMITTING", "status": None, "executed_qty": 0.0, "final": False, "origin": origin[:120]}
            self.ledger.mutate(sym, lambda orders: orders.append(entry))
            _remember_order(entry["local_id"])
        try:
            raw = getattr(inner_client, "_odg_raw_place_order", None) or inner_client.place_order
            res = await raw(account_key=account_key, symbol=symbol, side=side, quantity=quantity, order_type=order_type, price=price, stop=stop, duration=duration)
        except BaseException as e:
            self._mark(sym, entry, state="UNKNOWN", evidence=repr(e)[:200])
            self.log.critical(f"🚨 [ORDER_DEDUPE_UNKNOWN] tradier:{self.account}:{sym} place_order raised {e!r} — key frozen until broker listing confirms")
            raise
        order = (res or {}).get("order") if isinstance(res, dict) else None
        oid = order.get("id") if isinstance(order, dict) else None
        if oid:
            st = str(order.get("status", "") or "").lower()
            self._mark(sym, entry, order_id=oid, status=st or None, state="SUBMITTED")
        elif isinstance(res, dict) and res.get("errors"):
            # HTTP-200 body with explicit broker errors = definitive rejection (no order created)
            self._mark(sym, entry, state="FINAL", status="rejected_by_broker", final=True, executed_qty=0.0, evidence=str(res.get("errors"))[:200])
        else:
            # transport failure / non-200 / retried POST — outcome UNKNOWN; next preflight reconciles from the broker listing
            self._mark(sym, entry, state="UNKNOWN", evidence=str(res)[:200])
            self.log.critical(f"🚨 [ORDER_DEDUPE_UNKNOWN] tradier:{self.account}:{sym} place_order returned no id ({str(res)[:120]}) — key frozen until broker listing confirms")
        return res

    async def guarded_generic(self, inner_client, symbol: str, side: str, quantity: float, call: Callable[[], Any], origin: str = "") -> Dict[str, Any]:
        """Option / multileg orders: broker-FINAL dedupe on the underlying + ledger + no retry. (Exposure gate is equity-only;
        option strategies run outside execute_now and are logged CRITICAL on every order.)"""
        sym = str(symbol).upper()
        async with self.akey_lock(sym):
            dec = await self.preflight(inner_client, sym, origin=origin)
            if not dec.allowed:
                return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{dec.code}"]}, "order_dedupe_block": {"code": dec.code, "evidence": dec.evidence}}
            if bool(_cfg(self.cfg, "WIRE_REFUSE_BAD_QTY")) and _bad_qty(quantity):
                d2 = self._block(sym, "BAD_QTY", {"quantity": str(quantity)}, origin)
                return {"errors": {"error": [f"ORDER_DEDUPE_BLOCK:{d2.code}"]}, "order_dedupe_block": {"code": d2.code, "evidence": d2.evidence}}
            if _ORDER_AUTH.get() is None:
                self.log.critical(f"⚠️ [ORDER_OUTSIDE_EXECUTE_NOW] tradier:{self.account}:{sym} option order {side} qty={quantity} origin={origin[:80]} — dedupe-guarded only")
            entry = {"local_id": uuid.uuid4().hex, "order_id": None, "side": str(side).lower(), "position_side": "", "qty": _f(quantity), "type": "option", "submitted_at": self.clock(), "state": "SUBMITTING", "status": None, "executed_qty": 0.0, "final": False, "origin": origin[:120]}
            self.ledger.mutate(sym, lambda orders: orders.append(entry))
        try:
            res = await call()
        except BaseException as e:
            self._mark(sym, entry, state="UNKNOWN", evidence=repr(e)[:200])
            raise
        order = (res or {}).get("order") if isinstance(res, dict) else None
        oid = order.get("id") if isinstance(order, dict) else None
        if oid:
            self._mark(sym, entry, order_id=oid, status=str(order.get("status", "") or "").lower() or None, state="SUBMITTED")
        elif isinstance(res, dict) and res.get("errors"):
            self._mark(sym, entry, state="FINAL", status="rejected_by_broker", final=True, executed_qty=0.0, evidence=str(res.get("errors"))[:200])
        else:
            self._mark(sym, entry, state="UNKNOWN", evidence=str(res)[:200])
        return res

    async def broker_position(self, inner_client, symbol: str, is_long: bool) -> Dict[str, Any]:
        """FRESH broker read: GET positions (+ quote for mark when a position exists). Raises on failure (fail closed)."""
        q = float(_cfg(self.cfg, "ORDER_DEDUPE_QUERY_TIMEOUT_SEC"))
        rows = await asyncio.wait_for(inner_client.get_account_positions(self.account), timeout=q)
        if rows is None:
            raise RuntimeError("positions query failed (None)")
        if isinstance(rows, dict):
            p = rows.get("positions", rows)
            rows = p.get("position", []) if isinstance(p, dict) else p
            rows = rows if isinstance(rows, list) else [rows]
        signed = 0.0
        cost = 0.0
        for r in rows or []:
            if str((r or {}).get("symbol", "")).strip().upper() == symbol.upper():
                signed += _f(r.get("quantity"))
                cost += _f(r.get("cost_basis"))
        same = signed if is_long else -signed
        amt = max(0.0, same)
        out = {"signed": signed, "amt": amt, "opposing": max(0.0, -same), "entry": abs(cost) / abs(signed) if signed else 0.0, "mark": 0.0}
        if amt > 0:
            quote = await asyncio.wait_for(inner_client.get_quote(symbol), timeout=q)
            last = _f((quote or {}).get("last"))
            bid, ask = _f((quote or {}).get("bid")), _f((quote or {}).get("ask"))
            out["mark"] = (bid if is_long else ask) or last  # conservative: what we could actually trade at
        return out

    async def exposure_gate(self, inner_client, symbol: str, side: str, qty: float, origin: str = "") -> Decision:
        s = str(side).lower()
        if s not in ("buy", "sell_short"):
            if _ORDER_AUTH.get() is None:
                self.log.critical(f"⚠️ [ORDER_OUTSIDE_EXECUTE_NOW] tradier:{self.account}:{symbol} decreasing {s} qty={qty} origin={origin[:80]} — allowed (exits never stranded), route it through execute_now")
            return Decision(True, "DECREASING")
        is_long = s == "buy"
        try:
            bp = await self.broker_position(inner_client, symbol, is_long)
        except Exception as e:
            return Decision(False, "BROKER_POSITION_UNKNOWN", {"error": repr(e)[:300]})
        if bp.get("opposing", 0) > 0:
            return Decision(False, "OPPOSING_POSITION_HELD", {"broker_signed": bp.get("signed")})
        return exposure_decision(self, symbol, s, "", is_long, qty, bp, origin)

    def observe(self, symbol: str, order: Any, order_id=None, cancel_requested: bool = False) -> None:
        try:
            sym = str(symbol).upper()
            oid = (order or {}).get("id") if isinstance(order, dict) else None
            oid = oid or order_id
            if oid is None:
                return
            match = lambda o: str(o.get("order_id")) == str(oid)  # noqa: E731
            if cancel_requested:
                self.ledger.update_order(sym, match, cancel_requested_at=self.clock())
            if isinstance(order, dict) and order.get("status"):
                st = str(order.get("status")).lower()
                exq = order.get("exec_quantity")
                fields = {"status": st, "executed_qty": _f(exq, 0.0)}
                if _f(order.get("avg_fill_price"), 0.0) > 0:
                    fields["avg_price"] = _f(order.get("avg_fill_price"))
                if st in TRADIER_FINAL and exq is not None:
                    fields.update(final=True, state="FINAL")
                self.ledger.update_order(sym, match, **fields)
        except Exception as e:
            self.log.warning(f"[ORDER_DEDUPE_OBSERVE] tradier {symbol}: {e!r}")


class GuardedTradierClient:
    """Transparent proxy over tradier_api.TradierAPIClient; place_order is gated, cancel/status are observed."""

    def __init__(self, inner, guard: TradierDedupeGuard):
        object.__setattr__(self, "_odg_inner", inner)
        object.__setattr__(self, "_odg_guard", guard)
        object.__setattr__(self, "_odg_order_symbols", {})

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_odg_inner"), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_odg_inner"), name, value)

    @property
    def odg_inner(self):
        return object.__getattribute__(self, "_odg_inner")

    async def place_order(self, account_key: str, symbol: str, side: str, quantity: float, order_type: str = "market", price: float = None, stop: float = None, duration: str = "day") -> Dict:
        res = await self._odg_guard.guarded_place(self._odg_inner, account_key, symbol, side, quantity, order_type, price, stop, duration)
        try:
            oid = ((res or {}).get("order") or {}).get("id")
            if oid:
                self._odg_order_symbols[str(oid)] = str(symbol).upper()
        except Exception:
            pass
        return res

    async def place_option_order(self, *a, **kw):
        return {"errors": {"error": ["ORDER_DEDUPE_BLOCK:OPTION_ORDERS_NOT_GUARDED"]}}

    async def place_multileg_option_order(self, *a, **kw):
        return {"errors": {"error": ["ORDER_DEDUPE_BLOCK:OPTION_ORDERS_NOT_GUARDED"]}}

    async def cancel_order(self, account_key: str, order_id: Any) -> Dict:
        sym = self._odg_order_symbols.get(str(order_id))
        if sym:
            self._odg_guard.observe(sym, None, order_id=order_id, cancel_requested=True)
        return await self._odg_inner.cancel_order(account_key, order_id)

    async def get_order_status(self, account_key: str, order_id: Any) -> Dict:
        res = await self._odg_inner.get_order_status(account_key, order_id)
        sym = self._odg_order_symbols.get(str(order_id)) or (str(res.get("symbol", "")).upper() if isinstance(res, dict) else None)
        if sym:
            self._odg_guard.observe(sym, res, order_id=order_id)
        return res


def get_tradier_guard(account: str, cfg: Any = None, logger: Optional[logging.Logger] = None, ledger_dir: Optional[Path] = None) -> TradierDedupeGuard:
    g = _TRADIER_GUARDS.get(account)
    if g is None:
        g = _TRADIER_GUARDS[account] = TradierDedupeGuard(account, cfg=cfg, logger=logger, ledger_dir=ledger_dir)
    else:
        if cfg is not None and g.cfg is None:
            g.cfg = cfg
        if logger is not None and g.log is _LOG:
            g.log = logger
    return g


def wrap_tradier_client(client, account: str, cfg: Any = None, logger: Optional[logging.Logger] = None, ledger_dir: Optional[Path] = None):
    if client is None or isinstance(client, GuardedTradierClient):
        return client
    return GuardedTradierClient(client, get_tradier_guard(account, cfg, logger, ledger_dir))


async def tradier_execute_now_preflight(client, account: str, symbol: str, origin: str = "", cfg: Any = None, logger: Optional[logging.Logger] = None) -> Decision:
    inner = client.odg_inner if isinstance(client, GuardedTradierClient) else client
    guard = client._odg_guard if isinstance(client, GuardedTradierClient) else get_tradier_guard(account, cfg, logger)
    try:
        return await guard.preflight(inner, str(symbol).upper(), origin=origin)
    except Exception as e:
        return guard._block(str(symbol), "PREFLIGHT_TIMEOUT_OR_ERROR", {"error": repr(e)[:300]}, origin)


async def tradier_preflight_with_factory(factory: Callable[[], Any], account: str, symbol: str, origin: str = "", cfg: Any = None, logger: Optional[logging.Logger] = None) -> Decision:
    """execute_now helper: build a fresh TradierAPIClient, connect, preflight, close. Any failure -> BLOCK (fail closed)."""
    guard = get_tradier_guard(account, cfg, logger)
    cli = None
    try:
        cli = factory()
        await asyncio.wait_for(cli.connect(), timeout=float(_cfg(cfg, "ORDER_DEDUPE_QUERY_TIMEOUT_SEC")))
        return await tradier_execute_now_preflight(cli, account, symbol, origin, cfg, logger)
    except Exception as e:
        return guard._block(str(symbol), "BROKER_CONNECT_FAILED", {"error": repr(e)[:300]}, origin)
    finally:
        if cli is not None:
            with contextlib.suppress(Exception):
                await cli.close()


if os.environ.get("ODG_CLASS_GUARD", "1") != "0":  # every importing process: raw python-binance Client objects are gated too
    try:
        install_binance_class_guard()
    except Exception as _e:  # pragma: no cover
        _LOG.critical(f"[ORDER_DEDUPE] class guard install failed: {_e!r}")


if __name__ == "__main__":  # local, read-only ledger dump (no broker calls)
    d = _default_ledger_dir()
    for p in sorted(d.glob("order_ledger_*.json")):
        data = json.loads(p.read_text() or "{}")
        nf = {s: [o for o in r.get("orders", []) if not o.get("final")] for s, r in data.items()}
        print(p.name, "symbols:", len(data), "non-final:", {s: len(v) for s, v in nf.items() if v})
