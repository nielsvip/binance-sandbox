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
import fcntl
import json
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
                self._mark(sym, entry, status=st, executed_qty=_f(exq), final=True, state="FINAL", order_id=resp.get("orderId") or entry.get("order_id"))
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
        try:
            resp = inner_client.futures_create_order(**params)
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
        self._mark(symbol, entry, state="FINAL" if final else "SUBMITTED", order_id=(resp or {}).get("orderId"), status=st or None, executed_qty=_f(exq, 0.0), final=final)
        return resp

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
    return g


def wrap_binance_client(client, account: str, cfg: Any = None, logger: Optional[logging.Logger] = None, ledger_dir: Optional[Path] = None):
    if client is None or isinstance(client, GuardedBinanceClient):
        return client
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
                self._mark(sym, entry, status=st, executed_qty=_f(exq), final=True, state="FINAL")
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
        try:
            res = await inner_client.place_order(account_key=account_key, symbol=symbol, side=side, quantity=quantity, order_type=order_type, price=price, stop=stop, duration=duration)
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


if __name__ == "__main__":  # local, read-only ledger dump (no broker calls)
    d = _default_ledger_dir()
    for p in sorted(d.glob("order_ledger_*.json")):
        data = json.loads(p.read_text() or "{}")
        nf = {s: [o for o in r.get("orders", []) if not o.get("final")] for s, r in data.items()}
        print(p.name, "symbols:", len(data), "non-final:", {s: len(v) for s, v in nf.items() if v})
