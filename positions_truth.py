"""POSITIONS_TRUTH — broker-confirmed position state for every order decision (2026-10-06 USER, real money).

Trigger: a vec REDUCE_TO_FLAT was sent with qty 0 and reported SUCCESS; the live position never went flat and every
later open was lost because live believed it was still in a position.  This module makes that class of failure
structurally impossible:

  (a) freshness contract  — every positions file write leaves a sidecar ``.<name>.meta.json`` with ``written_at``.
      Readers treat data older than POSITIONS_MAX_AGE_S (default 1.0 s for decisions that send orders) as STALE.
      Missing/unreadable meta = STALE (fail closed).
  (b) stale -> broker      — on stale data the manager polls the broker directly for that account (single-flight,
      cached <= POSITIONS_BROKER_CACHE_S, IP-ban aware) and uses broker truth.  Logged as POSITIONS_STALE_FALLBACK.
  (c) confirmed SUCCESS    — an order result counts as SUCCESS only if the broker position afterwards moved in the
      right direction by the broker-confirmed filled qty (ORDER_DEDUPE_GUARD ledger), or is flat for a close.
      Otherwise the result is rewritten to ``UNCONFIRMED_BY_BROKER_<code>`` (never contains "SUCCESS").
      A zero / negative / NaN quantity is refused before anything is sent (BLOCKED_ZERO_QTY).
  (d) no second network path for orders — order status comes from the order_dedupe_guard ledger and its broker
      get-order query; position reads reuse the same broker client objects (odg_inner) the guard wraps.

Every behaviour sits behind a config switch whose default is the SAFE behaviour.  No live orders are ever sent here.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

_LOG = logging.getLogger("positions_truth")

DEFAULTS: Dict[str, Any] = {
    "POSITIONS_TRUTH_ENABLED": True,  # master switch (False = legacy behaviour, logged CRITICAL on every order)
    "POSITIONS_MAX_AGE_S": 1.0,  # decisions that send orders: local data older than this is STALE
    "POSITIONS_BROKER_CACHE_S": 1.0,  # broker snapshot reuse window (rate-limit safety)
    "POSITIONS_BROKER_TIMEOUT_S": 8.0,
    "POSITIONS_CONFIRM_ENABLED": True,  # (c) SUCCESS only after broker-confirmed position change
    "POSITIONS_CONFIRM_TIMEOUT_S": 8.0,
    "POSITIONS_CONFIRM_POLL_S": 0.5,
    "POSITIONS_REFUSE_ZERO_QTY": True,  # zero/negative/NaN qty never sent, never SUCCESS
    "POSITIONS_MISMATCH_BLOCKS_ENTRIES": True,  # local != broker -> refuse position-increasing orders (exits proceed)
    "POSITIONS_META_ENABLED": True,  # writers leave .<file>.meta.json with written_at
    "POSITIONS_FILE_BROKER_TRUTH_ONLY": True,  # tradier_positions: never clamp broker qty in the positions file
    "POSITIONS_QTY_ABS_TOL": 1e-9,
    "POSITIONS_QTY_REL_TOL": 1e-6,
}

CLOSE_ACTIONS = {"CLOSE", "FULL_CLOSE", "QUICK_CLOSE", "HEDGE_CLOSE", "STOP_FUNCTIONS_KILL", "REDUCE_TO_FLAT"}
EXIT_TOKENS = ("CLOSE", "REDUCE", "PROFIT_TAKE", "SELL_TOP", "EXIT", "TO_FLAT")
UNCONFIRMED_PREFIX = "UNCONFIRMED_BY_BROKER_"


def cfg(config: Any, name: str):
    try:
        if config is not None and hasattr(config, name):
            return getattr(config, name)
    except Exception:
        pass
    return DEFAULTS[name]


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _epoch(ts: Any) -> Optional[float]:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts) if math.isfinite(float(ts)) and ts > 0 else None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.timestamp()
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def is_zero_qty(qty: Any) -> bool:
    try:
        q = float(qty)
    except (TypeError, ValueError):
        return True
    return (not math.isfinite(q)) or q <= 0.0


def is_exit_action(action: Any, reason: Any = "") -> bool:
    a = str(action or "").upper()
    return any(t in a for t in EXIT_TOKENS) or "TO_FLAT" in str(reason or "").upper()


def classify_kind(action: Any, is_full_close: bool = False, reason: Any = "", side: Any = "", position_side: Any = "") -> str:
    """close (must end flat) | reduce | increase."""
    a = str(action or "").upper()
    r = str(reason or "").upper()
    if is_full_close or a in CLOSE_ACTIONS or "TO_FLAT" in a or "TO_FLAT" in r:
        return "close"
    if is_exit_action(a):
        return "reduce"
    s, ps = str(side or "").upper(), str(position_side or "").upper()
    if (ps == "LONG" and s.startswith("SELL")) or (ps == "SHORT" and s.startswith("BUY")):
        return "reduce"
    return "increase"


def qty_tol(config: Any, *amounts: float) -> float:
    m = max([abs(_f(a)) for a in amounts] + [0.0])
    return max(float(cfg(config, "POSITIONS_QTY_ABS_TOL")), float(cfg(config, "POSITIONS_QTY_REL_TOL")) * m)


# ───────────────────────────── (a) freshness contract ─────────────────────────────
def meta_path(path: Any) -> Path:
    p = Path(path)
    return p.with_name(f".{p.name}.meta.json")


def is_positions_file(path: Any) -> bool:
    n = Path(path).name.lower()
    return n in ("long_positions.json", "short_positions.json", "positions_long.json", "positions_short.json")


def write_meta(path: Any, writer: str, broker_synced_at: Any = None, config: Any = None, now: Optional[float] = None) -> bool:
    """Called by a writer right AFTER the positions file was atomically replaced. Never raises.
    Order (data first, meta second) is fail-safe: a crash between the two leaves meta OLDER than data -> reads stale."""
    if not bool(cfg(config, "POSITIONS_META_ENABLED")):
        return False
    try:
        mp = meta_path(path)
        t = time.time() if now is None else float(now)
        body = {"written_at": t, "written_at_iso": datetime.fromtimestamp(t, timezone.utc).isoformat(), "writer": writer, "pid": os.getpid(), "file": Path(path).name}
        bs = _epoch(broker_synced_at)
        if bs is not None:
            body["broker_synced_at"] = bs
        tmp = mp.with_name(f"{mp.name}.{os.getpid()}.tmp")
        with open(tmp, "w") as fh:
            json.dump(body, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, mp)
        return True
    except Exception as e:
        _LOG.warning(f"[POSITIONS_META] write failed for {path}: {e!r}")
        return False


def read_written_at(path: Any) -> Optional[float]:
    """written_at of a positions file. None = unknown (caller must treat as STALE).
    If the data file is NEWER than its meta (a writer that does not stamp meta replaced it) the meta time is used,
    i.e. the answer is never fresher than the last stamped broker-derived write."""
    try:
        mp = meta_path(path)
        if not mp.exists() or not Path(path).exists():
            return None
        with open(mp) as fh:
            body = json.load(fh)
        wa = _f(body.get("written_at"), 0.0)
        if wa <= 0:
            return None
        return min(wa, Path(path).stat().st_mtime + 0.5)
    except Exception:
        return None


def age_s(written_at: Optional[float], now: Optional[float] = None) -> float:
    if written_at is None:
        return float("inf")
    return max(0.0, (time.time() if now is None else now) - float(written_at))


def is_stale(written_at: Optional[float], config: Any = None, now: Optional[float] = None, max_age: Optional[float] = None) -> bool:
    lim = float(cfg(config, "POSITIONS_MAX_AGE_S") if max_age is None else max_age)
    return age_s(written_at, now) > lim


# ───────────────────────────── (b) broker snapshot cache ─────────────────────────────
@dataclass
class BrokerSnapshot:
    broker: str
    account: str
    fetched_at: float
    amounts: Dict[Tuple[str, str], float] = field(default_factory=dict)
    entries: Dict[Tuple[str, str], float] = field(default_factory=dict)

    def amount(self, symbol: str, position_side: str) -> float:
        return float(self.amounts.get((str(symbol).upper(), str(position_side).upper()), 0.0))


def normalize_binance(rows: List[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str], float], Dict[Tuple[str, str], float]]:
    amounts: Dict[Tuple[str, str], float] = {}
    entries: Dict[Tuple[str, str], float] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol", "")).upper()
        amt = _f(r.get("positionAmt"))
        ps = str(r.get("positionSide", "BOTH") or "BOTH").upper()
        if ps not in ("LONG", "SHORT"):
            if amt == 0:
                continue
            ps = "LONG" if amt > 0 else "SHORT"
        k = (sym, ps)
        amounts[k] = amounts.get(k, 0.0) + abs(amt)
        if abs(amt) > 0:
            entries[k] = _f(r.get("entryPrice"))
    return amounts, entries


def normalize_tradier(rows: List[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str], float], Dict[Tuple[str, str], float]]:
    amounts: Dict[Tuple[str, str], float] = {}
    entries: Dict[Tuple[str, str], float] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        sym = str(r.get("symbol", "")).strip().upper()
        q = _f(r.get("quantity"))
        if not sym or q == 0:
            continue
        k = (sym, "LONG" if q > 0 else "SHORT")
        amounts[k] = amounts.get(k, 0.0) + abs(q)
        cb = _f(r.get("cost_basis"))
        entries[k] = abs(cb / q) if q else 0.0
    return amounts, entries


class BrokerPositions:
    """Per (broker, account) broker snapshot. Single-flight (concurrent callers share one request), cached
    <= POSITIONS_BROKER_CACHE_S, refuses to call while an IP ban is active. fetch() returns the raw row list,
    [] for a CONFIRMED empty account, None (or raises) on failure -> snapshot None (callers fail closed)."""

    def __init__(self, broker: str, account: str, fetch: Callable[[], Awaitable[Optional[List[Dict[str, Any]]]]], config: Any = None, clock: Callable[[], float] = time.time, ban_remaining: Optional[Callable[[], float]] = None, logger: Optional[logging.Logger] = None):
        self.broker = broker
        self.account = account
        self.fetch = fetch
        self.config = config
        self.clock = clock
        self.ban_remaining = ban_remaining
        self.log = logger or _LOG
        self.last: Optional[BrokerSnapshot] = None
        self.calls = 0
        self._lock: Optional[asyncio.Lock] = None

    def _alock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _fresh(self) -> bool:
        return self.last is not None and (self.clock() - self.last.fetched_at) <= float(cfg(self.config, "POSITIONS_BROKER_CACHE_S"))

    async def snapshot(self, force: bool = False, reason: str = "") -> Optional[BrokerSnapshot]:
        if not force and self._fresh():
            return self.last
        async with self._alock():
            if not force and self._fresh():
                return self.last
            try:
                ban = float(self.ban_remaining()) if self.ban_remaining else 0.0
            except Exception:
                ban = 0.0
            if ban > 0:
                self.log.critical(f"🛑 [POSITIONS_BROKER_BANNED] {self.broker}:{self.account} broker position poll refused (IP ban {ban:.0f}s left) reason={reason[:60]}")
                return None
            t0 = self.clock()
            self.calls += 1
            try:
                rows = await asyncio.wait_for(self.fetch(), timeout=float(cfg(self.config, "POSITIONS_BROKER_TIMEOUT_S")))
            except Exception as e:
                self.log.critical(f"🛑 [POSITIONS_BROKER_FETCH_FAILED] {self.broker}:{self.account} {e!r} reason={reason[:60]}")
                return None
            if rows is None or not isinstance(rows, list):
                self.log.critical(f"🛑 [POSITIONS_BROKER_FETCH_FAILED] {self.broker}:{self.account} unparseable={str(rows)[:120]} reason={reason[:60]}")
                return None
            amounts, entries = (normalize_binance if self.broker == "binance" else normalize_tradier)(rows)
            self.last = BrokerSnapshot(self.broker, self.account, t0, amounts, entries)
            return self.last


_CACHES: Dict[Tuple[str, str], BrokerPositions] = {}


def seed_snapshot(broker: str, account: str, rows: Any, fetched_at: Optional[float] = None) -> bool:
    """A writer that just fetched broker rows (e.g. fetch_positions whose processing then hung) seeds the shared broker
    snapshot so order decisions keep using broker truth while local state is being reconciled."""
    try:
        if not isinstance(rows, list):
            return False
        amounts, entries = (normalize_binance if broker == "binance" else normalize_tradier)(rows)
        bp = _CACHES.get((broker, account))
        snap = BrokerSnapshot(broker, account, time.time() if fetched_at is None else fetched_at, amounts, entries)
        if bp is not None:
            bp.last = snap
        else:
            bp = _CACHES[(broker, account)] = BrokerPositions(broker, account, lambda: asyncio.sleep(0, result=None))
            bp.last = snap
        return True
    except Exception:
        return False


def hang_reconcile(service: Any, account_key: str, rows: Any, where: str, config: Any = None, logger: Optional[logging.Logger] = None) -> bool:
    """INVARIANT 6: a hung / failed position update must NOT os._exit and lose state. Instead: seed broker truth for order
    decisions, age the local sync stamp so every order-bound read falls back to the broker (ez_pre_order_check), and count
    consecutive hangs. Returns True when the caller should give up and exit (POSITIONS_HANG_EXIT_AFTER consecutive)."""
    log = logger or _LOG
    seeded = seed_snapshot("binance", account_key, rows)
    try:
        service.positions_last_sync = datetime.fromtimestamp(0, timezone.utc)
    except Exception:
        pass
    cnt = service.__dict__.setdefault("_ptruth_hangs", {})
    cnt[account_key] = cnt.get(account_key, 0) + 1
    limit = int(getattr(config, "POSITIONS_HANG_EXIT_AFTER", 3) if config is not None else 3)
    log.critical(f"🩹 [POSITIONS_HANG_RECONCILE] {account_key}: {where} — broker snapshot seeded={seeded}, local marked STALE (orders use broker truth), consecutive={cnt[account_key]}/{limit}")
    return cnt[account_key] >= limit


def hang_ok(service: Any, account_key: str) -> None:
    try:
        service.__dict__.setdefault("_ptruth_hangs", {})[account_key] = 0
    except Exception:
        pass


def get_broker_positions(broker: str, account: str, fetch: Callable[[], Awaitable[Any]], config: Any = None, ban_remaining: Optional[Callable[[], float]] = None, logger: Optional[logging.Logger] = None) -> BrokerPositions:
    k = (broker, account)
    bp = _CACHES.get(k)
    if bp is None:
        bp = _CACHES[k] = BrokerPositions(broker, account, fetch, config=config, ban_remaining=ban_remaining, logger=logger)
    else:
        bp.fetch = fetch
    return bp


def binance_fetcher(client: Any, config: Any = None) -> Callable[[], Awaitable[Optional[List[Dict[str, Any]]]]]:
    """positionRisk via the SAME python-binance client object order_dedupe_guard wraps (non-order call, passes through)."""
    inner = getattr(client, "odg_inner", client)

    async def _fetch():
        if inner is None:
            raise RuntimeError("no binance client")
        return await asyncio.to_thread(inner.futures_position_information)

    return _fetch


def tradier_fetcher(client_factory: Callable[[], Any], account: str) -> Callable[[], Awaitable[Optional[List[Dict[str, Any]]]]]:
    """GET /accounts/{id}/positions via TradierAPIClient.get_account_positions (None = failure, [] = confirmed empty)."""

    async def _fetch():
        cli = client_factory()
        try:
            await cli.connect()
            return await cli.get_account_positions(account)
        finally:
            try:
                await cli.close()
            except Exception:
                pass

    return _fetch


# ───────────────────────────── (c) post-order confirmation ─────────────────────────────
@dataclass
class Confirm:
    ok: bool
    code: str
    pre: Optional[float]
    post: Optional[float]
    expected: Optional[float]
    filled: Optional[float]
    kind: str
    polls: int = 0


def evaluate(pre: Optional[float], post: Optional[float], kind: str, requested: float, filled: Optional[float], tol: float) -> Tuple[bool, str, Optional[float]]:
    if post is None:
        return False, "BROKER_UNREACHABLE", None
    if kind == "close":
        return (post <= tol, "FLAT_CONFIRMED" if post <= tol else "NOT_FLAT", 0.0)
    if pre is None:
        return False, "NO_PRE_SNAPSHOT", None
    sign = -1.0 if kind == "reduce" else 1.0
    if filled is not None:
        if filled <= tol:
            return False, "NO_FILL_CONFIRMED", pre
        expected = max(0.0, pre + sign * filled)
        return (abs(post - expected) <= tol, "MATCH_FILLED" if abs(post - expected) <= tol else "MISMATCH_FILLED", expected)
    expected = max(0.0, pre + sign * abs(requested))
    if abs(post - expected) <= tol:
        return True, "MATCH_REQUESTED", expected
    moved = (post - pre) * sign
    return False, ("PARTIAL_UNVERIFIED" if moved > tol else "NO_CHANGE"), expected


async def confirm_position_change(get_amount: Callable[[bool], Awaitable[Optional[float]]], pre: Optional[float], kind: str, requested: float, filled_fn: Optional[Callable[[], Awaitable[Optional[float]]]] = None, config: Any = None, clock: Callable[[], float] = time.time, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep, timeout: Optional[float] = None) -> Confirm:
    deadline = clock() + float(cfg(config, "POSITIONS_CONFIRM_TIMEOUT_S") if timeout is None else timeout)
    poll = float(cfg(config, "POSITIONS_CONFIRM_POLL_S"))
    polls = 0
    while True:
        polls += 1
        post = await get_amount(True)
        filled = None
        if filled_fn is not None:
            try:
                filled = await filled_fn()
            except Exception:
                filled = None
        tol = qty_tol(config, pre or 0.0, requested, filled or 0.0)
        ok, code, expected = evaluate(pre, post, kind, requested, filled, tol)
        if ok or clock() >= deadline:
            return Confirm(ok, code, pre, post, expected, filled, kind, polls)
        await sleep(poll)


def _row_increases(broker: str, row: Dict[str, Any], position_side: str) -> Optional[bool]:
    s = str(row.get("side", "")).lower()
    ps = str(position_side).upper()
    if broker == "binance":
        if s not in ("buy", "sell"):
            return None
        return (s == "buy") == (ps == "LONG")
    if s in ("buy", "sell_short") and ps == ("LONG" if s == "buy" else "SHORT"):
        return True
    if s in ("sell", "buy_to_cover") and ps == ("LONG" if s == "sell" else "SHORT"):
        return False
    if s == "buy" and ps == "SHORT":
        return False
    if s == "sell" and ps == "SHORT":
        return True
    return None


async def ledger_filled_since(ledger: Any, broker: str, symbol: str, position_side: str, kind: str, since_ts: float, refresh: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None) -> Optional[float]:
    """Broker-confirmed filled qty of OUR orders on symbol submitted at/after since_ts in the direction of `kind`.
    Non-final rows are refreshed through `refresh` (the guard's own broker get-order query). None = no order in the
    ledger window (caller falls back to the requested qty) — a row whose fill is still unknown counts 0."""
    try:
        rows = [o for o in ledger.orders(symbol) if _f(o.get("submitted_at")) >= since_ts - 1.0]
    except Exception:
        return None
    want_increase = kind == "increase"
    rel = []
    for o in rows:
        if broker == "binance" and o.get("position_side") and str(o.get("position_side")).upper() != str(position_side).upper():
            continue
        inc = _row_increases(broker, o, position_side)
        if inc is None or inc != want_increase:
            continue
        rel.append(o)
    if not rel:
        return None
    if refresh is not None and any(not o.get("final") for o in rel):
        for o in [o for o in rel if not o.get("final")]:
            try:
                await refresh(o)
            except Exception:
                pass
        try:
            ids = {(o.get("client_order_id"), o.get("local_id"), str(o.get("order_id"))) for o in rel}
            rel = [o for o in ledger.orders(symbol) if (o.get("client_order_id"), o.get("local_id"), str(o.get("order_id"))) in ids]
        except Exception:
            pass
    return sum(_f(o.get("executed_qty")) for o in rel)


# ───────────────────────────── integration: ez_manage (Binance futures) ─────────────────────────────
def _ban_remaining_binance() -> float:
    try:
        import ez_positions_service as _eps  # lazy: only inside ez_manage

        return float(_eps._ban_remaining_seconds())
    except Exception:
        return 0.0


def _ez_client(mgr: Any, account_key: str):
    try:
        return getattr(mgr.accounts.get(account_key), "client", None)
    except Exception:
        return None


def _ez_local(mgr: Any, position_key: str) -> Tuple[Optional[float], Optional[float]]:
    """(local abs amount, local broker-sync epoch). Local = positions_service in-memory dict (WS + REST fed)."""
    svc = getattr(mgr, "positions_service", None)
    pos = None
    try:
        pos = svc.positions.get(position_key) if svc is not None else None
    except Exception:
        pos = None
    if pos is None:
        try:
            pos = mgr.positions.get(position_key)
        except Exception:
            pos = None
    amt = abs(_f(getattr(pos, "positionAmt", 0.0))) if pos is not None else 0.0
    ts = _epoch(getattr(svc, "positions_last_sync", None)) if svc is not None else None
    return amt, ts


def ez_broker(mgr: Any, account_key: str, config: Any = None, logger: Optional[logging.Logger] = None) -> BrokerPositions:
    return get_broker_positions("binance", account_key, binance_fetcher(_ez_client(mgr, account_key), config), config=config, ban_remaining=_ban_remaining_binance, logger=logger)


def _ez_resync(mgr: Any, account_key: str) -> None:
    try:
        svc = getattr(mgr, "positions_service", None)
        if svc is not None and hasattr(svc, "fetch_positions"):
            asyncio.get_running_loop().create_task(svc.fetch_positions(account_key))
    except Exception:
        pass


async def ez_pre_order_check(mgr: Any, account_key: str, symbol: str, position_key: str, position_side: str, action: Any, reason: Any = "", config: Any = None, logger: Optional[logging.Logger] = None, now: Optional[float] = None) -> Optional[str]:
    """Called inside execute_now right before the position is read for sizing (after gates + ORDER_DEDUPE preflight,
    so only order-bound calls pay a broker request). Returns a block/skip result string, or None to continue.
    Records the broker-truth pre-order amount for the post-order confirmation."""
    log = logger or _LOG
    if not bool(cfg(config, "POSITIONS_TRUTH_ENABLED")):
        return None
    store = mgr.__dict__.setdefault("_ptruth_pre", {})
    local_amt, local_ts = _ez_local(mgr, position_key)
    t = time.time() if now is None else now
    age = age_s(local_ts, t)
    exit_like = is_exit_action(action, reason)
    if age <= float(cfg(config, "POSITIONS_MAX_AGE_S")):
        store[position_key] = {"pre": local_amt, "local": local_amt, "source": "local", "ts": t}
        return None
    log.warning(f"[POSITIONS_STALE_FALLBACK] {position_key}: local positions age {age:.2f}s > {float(cfg(config, 'POSITIONS_MAX_AGE_S')):.2f}s — polling broker directly (action={action})")
    snap = await ez_broker(mgr, account_key, config, log).snapshot(reason=f"pre_order {position_key} {action}")
    if snap is None:
        store[position_key] = {"pre": None, "local": local_amt, "source": "none", "ts": t}
        if exit_like:
            log.critical(f"⚠️ [POSITIONS_BROKER_UNREACHABLE] {position_key}: broker poll failed — EXIT {action} proceeds (exits never blocked); result must still be broker-confirmed")
            return None
        return "BLOCKED_POSITIONS_BROKER_UNREACHABLE"
    broker_amt = snap.amount(symbol, position_side)
    store[position_key] = {"pre": broker_amt, "local": local_amt, "source": "broker", "ts": t, "fetched_at": snap.fetched_at}
    if abs(broker_amt - local_amt) > qty_tol(config, broker_amt, local_amt):
        log.critical(f"🚨 [POSITIONS_BROKER_MISMATCH] {position_key}: local={local_amt:.8f} broker={broker_amt:.8f} (local age {age:.1f}s) action={action} — broker truth wins; resync requested")
        _ez_resync(mgr, account_key)
        if exit_like and broker_amt <= qty_tol(config, local_amt):
            return "SKIP_BROKER_FLAT"
        if not exit_like and bool(cfg(config, "POSITIONS_MISMATCH_BLOCKS_ENTRIES")):
            return "BLOCKED_POSITIONS_BROKER_MISMATCH"
    return None


async def ez_broker_moved(mgr: Any, account_key: str, symbol: str, position_side: str, baseline: float, kind: str, config: Any = None, logger: Optional[logging.Logger] = None) -> bool:
    """FRESH broker read (forced): did the position move from `baseline` in the `kind` direction ("increase"/"reduce")?
    Used instead of the lagging WS/local verifier before any fallback order is considered. Broker unreachable -> False."""
    snap = await ez_broker(mgr, account_key, config, logger).snapshot(force=True, reason=f"moved? {symbol} {kind}")
    if snap is None:
        return False
    amt = snap.amount(symbol, position_side)
    tol = qty_tol(config, amt, baseline)
    return (amt < abs(baseline) - tol) if kind == "reduce" else (amt > abs(baseline) + tol)


def ez_leftover_position(mgr: Any, account_key: str, symbol: str, position_side: str) -> Optional[float]:
    """Synchronous peek at the last broker snapshot (<= POSITIONS_BROKER_CACHE_S old) — None if unknown."""
    bp = _CACHES.get(("binance", account_key))
    if bp is None or not bp._fresh():
        return None
    return bp.last.amount(symbol, position_side)


def _bind(core: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
    try:
        ba = inspect.signature(core).bind(*args, **kwargs)
        ba.apply_defaults()
        return dict(ba.arguments)
    except Exception:
        return dict(kwargs)


async def ez_execute_now_guarded(mgr: Any, core: Callable[..., Awaitable[Any]], args: tuple, kwargs: dict, config: Any = None, logger: Optional[logging.Logger] = None, is_sandbox: Optional[Callable[[str], bool]] = None, ledger_dir: Optional[Path] = None) -> Any:
    log = logger or _LOG
    if not bool(cfg(config, "POSITIONS_TRUTH_ENABLED")):
        log.critical("⚠️ [POSITIONS_TRUTH_DISABLED] execute_now result NOT broker-confirmed (config POSITIONS_TRUTH_ENABLED=False)")
        return await core(*args, **kwargs)
    a = _bind(core, args, kwargs)
    account_key, symbol = a.get("account_key"), a.get("symbol")
    position_side = str(a.get("position_side") or "LONG").upper()
    position_key = a.get("position_key") or (f"{account_key}:{symbol}_{position_side}" if account_key and symbol else None)
    qty, full = a.get("quantity"), bool(a.get("is_full_close"))
    action, reason = a.get("action"), a.get("reason") or ""
    if bool(cfg(config, "POSITIONS_REFUSE_ZERO_QTY")) and is_zero_qty(qty) and not full:
        log.critical(f"🛑 [POSITIONS_ZERO_QTY_REFUSED] {position_key}: action={action} qty={qty!r} reason={str(reason)[:80]} — zero/invalid quantity is never sent and never SUCCESS")
        return "BLOCKED_ZERO_QTY"
    sandbox = False
    try:
        sandbox = bool(is_sandbox(account_key)) if (is_sandbox and account_key) else False
    except Exception:
        sandbox = False
    start = time.time()
    store = mgr.__dict__.setdefault("_ptruth_pre", {})
    if position_key:
        store.pop(position_key, None)
    result = await core(*args, **kwargs)
    if sandbox or not isinstance(result, str) or "SUCCESS" not in result.upper() or "SANDBOX" in result.upper():
        return result
    if not bool(cfg(config, "POSITIONS_CONFIRM_ENABLED")):
        log.critical(f"⚠️ [POSITIONS_CONFIRM_DISABLED] {position_key}: {result} NOT broker-confirmed (config)")
        return result
    pre_rec = store.pop(position_key, None) or {}
    pre = pre_rec.get("pre", pre_rec.get("local")) if pre_rec else None
    kind = classify_kind(action, full, reason, a.get("side"), position_side)
    bp = ez_broker(mgr, account_key, config, log)

    async def _amt(force: bool) -> Optional[float]:
        snap = await bp.snapshot(force=force, reason=f"confirm {position_key}")
        return None if snap is None else snap.amount(symbol, position_side)

    filled_fn = None
    try:
        import order_dedupe_guard as _odg

        guard = _odg.get_binance_guard(account_key, cfg=config, logger=log, ledger_dir=ledger_dir)
        client = _ez_client(mgr, account_key)
        inner = getattr(client, "odg_inner", client)

        async def _refresh(row: Dict[str, Any]):
            kw = {"orderId": int(row["order_id"])} if row.get("order_id") else {"origClientOrderId": row.get("client_order_id")}
            resp = await asyncio.to_thread(inner.futures_get_order, symbol=str(symbol).upper(), **kw)
            guard.observe(str(symbol).upper(), resp, order_id=row.get("order_id"), client_order_id=row.get("client_order_id"))

        async def filled_fn():
            return await ledger_filled_since(guard.ledger, "binance", str(symbol).upper(), position_side, kind, start, _refresh if inner is not None else None)

    except Exception:
        filled_fn = None
    conf = await confirm_position_change(_amt, pre, kind, _f(qty), filled_fn, config)
    if conf.ok:
        log.info(f"✅ [POSITIONS_CONFIRMED] {position_key}: {result} kind={kind} pre={conf.pre} post={conf.post} filled={conf.filled} code={conf.code}")
        _unlock_key(account_key, symbol)
        return result
    log.critical(f"🚨 [POSITIONS_UNCONFIRMED] {position_key}: core returned {result} but broker shows pre={conf.pre} post={conf.post} expected={conf.expected} filled={conf.filled} kind={kind} code={conf.code} — NOT SUCCESS")
    _ez_resync(mgr, account_key)
    _lock_key("binance", account_key, symbol, conf.code)
    return f"{UNCONFIRMED_PREFIX}{conf.code}"


# ───────────────────────────── integration: tradier_manage (stocks) ─────────────────────────────
_TRADIER_PRE: Dict[Tuple[str, str, str], Dict[str, Any]] = {}


def tradier_pre_order(account_key: str, symbol: str, position_side: str, action: Any, broker_qty: float, local_qty: Optional[float], local_written_at: Optional[float] = None, reason: Any = "", config: Any = None, logger: Optional[logging.Logger] = None, now: Optional[float] = None) -> Optional[str]:
    """Called in place_order right after it fetched TRUE API holdings (no extra network call). Records the broker
    pre-order amount for confirmation and refuses position-increasing orders when local state disagrees with the broker."""
    log = logger or _LOG
    if not bool(cfg(config, "POSITIONS_TRUTH_ENABLED")):
        return None
    t = time.time() if now is None else now
    k = (str(account_key), str(symbol).upper(), str(position_side or "LONG").upper())
    _TRADIER_PRE[k] = {"pre": abs(_f(broker_qty)), "ts": t, "local": local_qty}
    age = age_s(local_written_at, t)
    if age > float(cfg(config, "POSITIONS_MAX_AGE_S")):
        log.warning(f"[POSITIONS_STALE_FALLBACK] {account_key}:{symbol}_{k[2]}: local positions age {age:.1f}s — using broker holdings {abs(_f(broker_qty)):.4f} (action={action})")
    if local_qty is None:
        return None
    lq, bq = abs(_f(local_qty)), abs(_f(broker_qty))
    if abs(lq - bq) > max(qty_tol(config, lq, bq), 1e-6):
        log.critical(f"🚨 [POSITIONS_BROKER_MISMATCH] {account_key}:{symbol}_{k[2]}: local={lq:.6f} broker={bq:.6f} action={action} — broker truth wins")
        if not is_exit_action(action, reason) and bool(cfg(config, "POSITIONS_MISMATCH_BLOCKS_ENTRIES")):
            return "POSITIONS_BROKER_MISMATCH"
    return None


async def tradier_execute_now_guarded(mgr: Any, core: Callable[..., Awaitable[Any]], args: tuple, kwargs: dict, client_factory: Callable[[str], Any], config: Any = None, logger: Optional[logging.Logger] = None, ledger_dir: Optional[Path] = None) -> Any:
    log = logger or _LOG
    if not bool(cfg(config, "POSITIONS_TRUTH_ENABLED")):
        log.critical("⚠️ [POSITIONS_TRUTH_DISABLED] tradier execute_now result NOT broker-confirmed")
        return await core(*args, **kwargs)
    a = _bind(core, args, kwargs)
    account_key, symbol = a.get("account_key"), str(a.get("symbol") or "").upper()
    position_side = str(a.get("position_side") or "LONG").upper()
    position_key = a.get("position_key") or f"{account_key}:{symbol}_{position_side}"
    qty, full = a.get("quantity"), bool(a.get("is_full_close"))
    action, reason = a.get("action"), a.get("reason") or ""
    if bool(cfg(config, "POSITIONS_REFUSE_ZERO_QTY")) and is_zero_qty(qty) and not full:
        log.critical(f"🛑 [POSITIONS_ZERO_QTY_REFUSED] {position_key}: action={action} qty={qty!r} — never sent, never SUCCESS")
        return "BLOCKED_ZERO_QTY"
    start = time.time()
    k = (str(account_key), symbol, position_side)
    _TRADIER_PRE.pop(k, None)
    result = await core(*args, **kwargs)
    if not isinstance(result, str) or "SUCCESS" not in result.upper():
        return result
    if not bool(cfg(config, "POSITIONS_CONFIRM_ENABLED")):
        log.critical(f"⚠️ [POSITIONS_CONFIRM_DISABLED] {position_key}: {result} NOT broker-confirmed (config)")
        return result
    rec = _TRADIER_PRE.pop(k, None)
    pre = rec.get("pre") if rec and _f(rec.get("ts")) >= start - 1.0 else None
    if pre is None:
        try:
            local = mgr.position_manager.get_position(position_key)
            pre = abs(_f(getattr(local, "positionAmt", 0.0))) if local is not None else None
        except Exception:
            pre = None
    kind = classify_kind(action, full, reason, a.get("side"), position_side)
    bp = get_broker_positions("tradier", str(account_key), tradier_fetcher(lambda: client_factory(account_key), str(account_key)), config=config, logger=log)

    async def _amt(force: bool) -> Optional[float]:
        snap = await bp.snapshot(force=force, reason=f"confirm {position_key}")
        return None if snap is None else snap.amount(symbol, position_side)

    filled_fn = None
    try:
        import order_dedupe_guard as _odg

        guard = _odg.get_tradier_guard(str(account_key), cfg=config, logger=log, ledger_dir=ledger_dir)

        async def _refresh(row: Dict[str, Any]):
            if not row.get("order_id"):
                return
            cli = client_factory(account_key)
            try:
                await cli.connect()
                order = await guard._get_order(cli, row["order_id"])
                guard.observe(symbol, order, order_id=row["order_id"])
            finally:
                try:
                    await cli.close()
                except Exception:
                    pass

        async def filled_fn():
            return await ledger_filled_since(guard.ledger, "tradier", symbol, position_side, kind, start, _refresh)

    except Exception:
        filled_fn = None
    conf = await confirm_position_change(_amt, pre, kind, _f(qty), filled_fn, config)
    if conf.ok:
        log.info(f"✅ [POSITIONS_CONFIRMED] {position_key}: {result} kind={kind} pre={conf.pre} post={conf.post} filled={conf.filled} code={conf.code}")
        _unlock_key(account_key, symbol)
        return result
    log.critical(f"🚨 [POSITIONS_UNCONFIRMED] {position_key}: core returned {result} but broker shows pre={conf.pre} post={conf.post} expected={conf.expected} filled={conf.filled} kind={kind} code={conf.code} — NOT SUCCESS")
    _lock_key("tradier", account_key, symbol, conf.code)
    return f"{UNCONFIRMED_PREFIX}{conf.code}"


def _unlock_key(account: Any, symbol: Any) -> None:
    try:
        import order_dedupe_guard as _odg

        for b in ("binance", "tradier"):
            _odg.clear_unconfirmed(b, str(account), str(symbol))
    except Exception:
        pass


def _lock_key(broker: str, account: Any, symbol: Any, code: str) -> None:
    try:
        import order_dedupe_guard as _odg

        _odg.mark_unconfirmed(broker, str(account), str(symbol), code)
    except Exception:
        pass


def ez_confirmed(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Decorator for ez_manage.MultiAccountTradeManager.execute_now (one line, no rename; inspect.getsource still
    returns the core via __wrapped__). config / logger / is_sandbox_account are resolved from ez_manage's globals."""
    import functools

    g = fn.__globals__

    @functools.wraps(fn)
    async def _wrapped(self, *args, **kwargs):
        sb = g.get("is_sandbox_account")
        conf = g.get("config")
        return await ez_execute_now_guarded(self, functools.partial(fn, self), args, kwargs, config=conf, logger=g.get("logger"), is_sandbox=(lambda a: sb(conf, a)) if sb else None)

    return _wrapped


def tradier_confirmed(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Decorator for tradier_manage execute_now. Broker reads use a fresh TradierAPIClient(config, account_key=...)."""
    import functools

    g = fn.__globals__

    @functools.wraps(fn)
    async def _wrapped(self, *args, **kwargs):
        conf = g.get("config")
        factory = lambda a: g["TradierAPIClient"](conf, account_key=a)  # noqa: E731
        return await tradier_execute_now_guarded(self, functools.partial(fn, self), args, kwargs, client_factory=factory, config=conf, logger=g.get("logger"))

    return _wrapped


import threading as _threading

_OFFLOOP_LOCK = _threading.Lock()


async def offloop_serialized(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Run a blocking CPU call (vec twin: backtest_v8_precompute.compute_symbol ~20 s/symbol) in a worker thread,
    one at a time, so the asyncio loop keeps servicing broker position sync. A blocked loop made
    process_account_update exceed PAU_TIMEOUT_SEC -> os._exit(42) (men restart loop 2026-10-06 19:00:39Z)."""

    def _run():
        with _OFFLOOP_LOCK:
            return fn(*args, **kwargs)

    return await asyncio.to_thread(_run)


def tradier_reader_written_at(wrapper: Any) -> Optional[float]:
    """Redis wrapper {'timestamp': epoch, ...} written by tradier_positions.broadcast_to_redis (after a successful API sync)."""
    if isinstance(wrapper, dict):
        return _epoch(wrapper.get("timestamp"))
    return None
