"""EXIT GUARANTEE (USER 2026-10-14: "ledger relogging ok, but make sure at least EXITS will never be compromised again").

Wraps execute_now (both venues) between the wire-token decorator and the broker-confirmation decorator:

    @_odg.execute_now_gate(venue)        # token (outer)
    @exit_guarantee.wrap_execute_now(..) # THIS
    @_ptruth.*_confirmed                 # SUCCESS only when the broker-confirmed position moved
    async def execute_now(...)

For every exit-class call (CLOSE / REDUCE / FULL_CLOSE / HEDGE_CLOSE / KILL / PROFIT_TAKE ...):
  * result OK (broker-confirmed SUCCESS)      -> confirmed hook: the per-trade ledger row is written ONCE (idempotency key =
                                                 account + position lifecycle + fill id), loss guards fed once per lifecycle.
  * FULL close refused/unconfirmed, no other  -> immediate escalation inside execute_now: the FORCE_CLOSE_MARKET action
    exit call in flight for the key              re-reads exchange truth (retry + last confirmed snapshot fallback), cancels our
                                                 own resting orders on that leg, sends ONE market reduce-only close of the broker
                                                 quantity, verifies flat. Still open -> stuck watcher retries + ALERT.
  * PARTIAL reduce refused                    -> retried with backoff (EXIT_GUARANTEE_REDUCE_RETRY_SEC) while the broker shows
                                                 the reduce did not happen and the lifecycle is unchanged; exhausted -> ALERT.
  * policy refusals (exact-mode native exit suppression, other box owns the account, WAIT, wrong account) and genuine
    no-ops are left alone; "local says flat" on a full close is verified with a forced broker read (stale snapshots lie).
Every decision/result/escalation is appended to data/safety/exit_events/{venue}_{YYYYMMDD}.jsonl — the input of
tools/exit_never_blocked_monitor.py (close decision without a confirmed fill within N minutes -> alert + desktop notify).
Kill switch: EXIT_GUARANTEE_ENABLED (config); FORCE leg: EXIT_GUARANTEE_FORCE_ENABLED. Never raises into execute_now.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_LOG = logging.getLogger("exit_guarantee")
FORCE_ACTION = "FORCE_CLOSE_MARKET"
STOP_ACTIONS = ("PLACE_STOP", "CANCEL_STOP")
FULL_ACTIONS = {"CLOSE", "QUICK_CLOSE", "FULL_CLOSE", "HEDGE_CLOSE", "STOP_FUNCTIONS_KILL", FORCE_ACTION}
EXIT_TOKENS = ("CLOSE", "REDUCE", "PROFIT_TAKE", "KILL", "EXIT")
NOT_EXIT_TOKENS = ("OPEN", "AUGMENT", "REENTRY", "REVERSE", "ENTRY")
DEFAULTS = {
    "EXIT_GUARANTEE_ENABLED": True,
    "EXIT_GUARANTEE_FORCE_ENABLED": True,
    "EXIT_GUARANTEE_REDUCE_RETRY_SEC": "15,30,60,120,240",
    "EXIT_GUARANTEE_STUCK_CHECK_SEC": "20,60,180",
    "EXIT_GUARANTEE_VERIFY_FLAT_MIN_INTERVAL_S": 60.0,
    "EXIT_GUARANTEE_NOTIFY": True,
    "EXIT_DUST_USD": 5.0,
}
# result codes that are deliberate policy (never escalated): the decision is not authorised to trade on this box / key
POLICY_PREFIXES = ("BLOCKED_PARITY_NATIVE_", "BLOCKED_VEC_DRIVEN_NATIVE_SUPPRESSED", "BLOCKED_VEC_PARITY_", "SKIPPED_SCALP_REDUCE_DISABLED", "BLOCKED_SERVER_HEARTBEAT_ACTIVE", "BLOCK WRONG ACCOUNT KEY", "BLOCKED_ACCOUNT_NOT_MANAGED", "SKIPPED_SANDBOX", "MARKET_CLOSED", "BLOCKED_MARKET_CLOSED")
POLICY_CONTAINS = ("WAIT MEANS WAIT",)
# strategy refusals of a NATIVE close (noloss / hedge regime / newborn / exit confirmation gates). A vec-decided, emergency,
# stop, sync or flatten close is authorised -> these become ordinary refusals and are escalated.
STRATEGY_PREFIXES = ("BLOCKED_BY_UNIVERSAL_NOLOSS_GATE", "BLOCKED_NOLOSS_MIN_", "BLOCKED_BY_HEDGE_MODE", "BLOCKED_NEWBORN_PROTECT_", "BLOCKED_EXIT_LH_LL_REQUIRED", "BLOCKED_WT_EXIT_MIN_TFS_", "BLOCKED_HEDGE_MISIDENT", "BLOCKED_LOW_GAIN_DRAIN_PROTECTION", "ABORT STOP DRAINING", "BLOCKED_NOLOSS", "NOLOSS", "LOSS_PROTECTION_BLOCK")
# local state says there is nothing to close: verified with a forced broker read on a FULL close
VERIFY_PREFIXES = ("BLOCK_POS_ALREADY_CLOSED", "SKIP_REDUCE_EMPTY_POSITION", "SKIP_BROKER_FLAT", "TIMEOUT_ALREADY_FLAT", "SKIPPED_NO_POSITION", "NO_POSITION", "SKIP_NO_POSITION")
NOOP_PREFIXES = ("FORCE_NOTHING_OPEN", "NO QUANTITY LEFT", "SKIPPED_STOP", "SKIPPED_FORCE_", "SUCCESS_SANDBOX", "SKIPPED_VX_INFLIGHT")
AUTH_TOKENS = ("STOP", "FLATTEN", "MAX_LOSS", "HEDGE_FAILED_FALLBACK", "KILL", "TIMEOUT_EXIT", "EXIT_GUARANTEE", "EMERGENCY", "LIQUIDAT", "MARGIN", "MANUAL", "FORCE", "VEC_DRIVEN_", "|VEC_EXACT", "BROKER", "SYNC", "ORPHAN", "DUST")
_CTX: contextvars.ContextVar = contextvars.ContextVar("exit_guarantee_ctx", default=None)
_DEPTH: contextvars.ContextVar = contextvars.ContextVar("exit_guarantee_depth", default=0)
_INFLIGHT: Dict[str, int] = {}
_WATCHERS: Dict[str, Any] = {}
_LAST_VERIFY: Dict[str, float] = {}
_NOTIFY_LAST: Dict[str, float] = {}
_LOCK = threading.Lock()


def _cfg(cfg: Any, name: str):
    try:
        if cfg is not None and hasattr(cfg, name):
            v = getattr(cfg, name)
            if v is not None:
                return v
    except Exception:
        pass
    return DEFAULTS[name]


def _secs(v) -> list:
    try:
        if isinstance(v, (list, tuple)):
            return [float(x) for x in v]
        return [float(x) for x in str(v).split(",") if str(x).strip()]
    except Exception:
        return [20.0, 60.0, 180.0]


def base_path(cfg: Any = None) -> Path:
    try:
        b = getattr(cfg, "BASE_PATH", None) if cfg is not None else None
        if b and Path(b).exists():
            return Path(b)
    except Exception:
        pass
    env = os.environ.get("EXIT_GUARANTEE_BASE")
    return Path(env) if env else Path(__file__).resolve().parent


# ───────────────────────────── pure classification ─────────────────────────────
def is_exit_action(action: Any, is_full_close: bool = False) -> bool:
    a = str(action or "").upper()
    if a in STOP_ACTIONS:
        return False
    if a == FORCE_ACTION or bool(is_full_close):
        return True
    if any(t in a for t in NOT_EXIT_TOKENS):
        return False
    return a == "SELL" or any(t in a for t in EXIT_TOKENS)


def is_full_close(action: Any, is_full_close: bool = False) -> bool:
    a = str(action or "").upper()
    if a in STOP_ACTIONS:
        return False
    return bool(is_full_close) or a in FULL_ACTIONS


def is_authorized_close(reason: Any) -> bool:
    r = str(reason or "").upper()
    return any(t in r for t in AUTH_TOKENS)


def classify(result: Any, reason: Any = "", full: bool = True) -> str:
    """OK | NOOP | POLICY | VERIFY | REFUSED. Pure (unit-tested)."""
    r = str(result if result is not None else "").strip()
    u = r.upper()
    if not u:
        return "REFUSED"
    if any(u.startswith(p) for p in NOOP_PREFIXES):
        return "NOOP"
    if u.startswith("SUCCESS"):
        return "OK"
    if any(u.startswith(p.upper()) for p in POLICY_PREFIXES) or any(c in u for c in POLICY_CONTAINS):
        return "POLICY"
    if any(u.startswith(p) for p in VERIFY_PREFIXES):
        return "VERIFY" if full else "NOOP"
    if any(u.startswith(p.upper()) for p in STRATEGY_PREFIXES):
        return "REFUSED" if is_authorized_close(reason) else "POLICY"
    if u.startswith("BLOCKED_ZERO_QTY") and not full:
        return "NOOP"
    return "REFUSED"


def lifecycle_id(account: Any, symbol: Any, side: Any, opened_at: Any = None, entry: Any = None) -> str:
    """account|SYMBOL|SIDE|<opened_at ISO minute or entry price>. One position lifecycle = one id."""
    oa = ""
    try:
        if isinstance(opened_at, datetime):
            d = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=timezone.utc)
            oa = d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        elif opened_at:
            d = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
            d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            oa = d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    except Exception:
        oa = str(opened_at)[:16]
    if not oa:
        try:
            e = float(entry or 0.0)
            oa = f"ep{e:.10g}" if e > 0 else "unknown"
        except Exception:
            oa = "unknown"
    return f"{account}|{str(symbol or '').upper()}|{str(side or '').upper()}|{oa}"


def parse_force_result(result: Any) -> Dict[str, Any]:
    """SUCCESS_FORCE_CLOSE_CONFIRMED|oid=..|px=..|qty=..|entry=.. -> dict (floats where numeric)."""
    out: Dict[str, Any] = {}
    for part in str(result or "").split("|")[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k] = float(v) if k != "oid" else v
            except ValueError:
                out[k] = v
    return out


# ───────────────────────────── events / alerts ─────────────────────────────
def log_event(venue: str, event: str, cfg: Any = None, **fields) -> None:
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("EXIT_GUARANTEE_BASE"):
        return
    try:
        d = base_path(cfg) / "data" / "safety" / "exit_events"
        d.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        rec = {"ts": now.isoformat(), "venue": venue, "event": event, **fields}
        with open(d / f"{venue}_{now.strftime('%Y%m%d')}.jsonl", "a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def notify(title: str, msg: str, key: str = "", cfg: Any = None, throttle_s: float = 300.0) -> None:
    """Desktop notification (macOS osascript), throttled per key, non-blocking, never under pytest."""
    try:
        if os.environ.get("PYTEST_CURRENT_TEST") or not bool(_cfg(cfg, "EXIT_GUARANTEE_NOTIFY")) or sys.platform != "darwin":
            return
        k = key or title
        now = time.time()
        if now - _NOTIFY_LAST.get(k, 0.0) < throttle_s:
            return
        _NOTIFY_LAST[k] = now
        safe = lambda s: str(s).replace("\\", "").replace('"', "'")[:180]  # noqa: E731
        subprocess.Popen(["osascript", "-e", f'display notification "{safe(msg)}" with title "{safe(title)}" sound name "Basso"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def alert(venue: str, key: str, msg: str, cfg: Any = None, logger: Optional[logging.Logger] = None, **fields) -> None:
    log = logger or _LOG
    try:
        log.critical(f"🚨🚨 [EXIT_GUARANTEE_ALERT] {venue}:{key} — {msg}")
    except Exception:
        pass
    log_event(venue, "ALERT", cfg, key=key, msg=msg, **fields)
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("EXIT_GUARANTEE_BASE"):
        return
    try:
        p = base_path(cfg) / "data" / "alerts" / "exit_never_blocked.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "venue": venue, "key": key, "msg": msg, **fields}, default=str) + "\n")
    except Exception:
        pass
    notify(f"EXIT STUCK {venue}", f"{key}: {msg}", key=f"{venue}:{key}", cfg=cfg)


# ───────────────────────────── body helpers ─────────────────────────────
def stash(**fields) -> bool:
    """execute_now body -> wrapper: position facts known at decision time (entry, opened_at, gain, qty, price, amt).
    Returns True when a guarantee wrapper is active for this call (the body then leaves ledger/loss-guard recording to the
    wrapper's confirmed hook instead of logging the attempt as a trade)."""
    c = _CTX.get()
    if c is None:
        return False
    try:
        c.update({k: v for k, v in fields.items()})
    except Exception:
        return False
    return True


def active() -> bool:
    return _CTX.get() is not None


def inflight(key: str) -> int:
    return int(_INFLIGHT.get(key, 0))


def _mark_token(full: bool, force: bool = False) -> None:
    try:
        import order_dedupe_guard as _odg  # noqa: WPS433

        tok = _odg.current_token()
        if tok is not None:
            tok["exit"] = True
            if full or force:
                tok["exit_priority"] = True
            if force:
                tok["exit_force"] = True
    except Exception:
        pass


def _token_orders() -> list:
    try:
        import order_dedupe_guard as _odg  # noqa: WPS433

        tok = _odg.current_token()
        return list((tok or {}).get("orders") or [])
    except Exception:
        return []


def _pk(a: Dict[str, Any]) -> str:
    pk = a.get("position_key")
    if pk:
        return str(pk)
    return f"{a.get('account_key')}:{a.get('symbol')}_{str(a.get('position_side') or '').upper()}"


async def _safe(coro_or_val):
    try:
        if inspect.isawaitable(coro_or_val):
            return await coro_or_val
        return coro_or_val
    except Exception as e:  # noqa: BLE001
        _LOG.error(f"[EXIT_GUARANTEE] hook error: {e!r}")
        return None


class Hooks:
    """Venue adapter. All callables take the manager first.
    force(mgr, args, ctx, why)                  -> result str (must call mgr.execute_now(action=FORCE_ACTION, ...))
    broker(mgr, account, symbol, position_side) -> {"amt": float, "entry": float} | None   (fresh exchange truth)
    confirmed(mgr, args, ctx, result)           -> None   (ledger once + loss guards, idempotent)
    cfg()                                       -> config object
    logger()                                    -> logger"""

    def __init__(self, force: Callable, broker: Callable, confirmed: Callable, cfg: Callable = lambda: None, logger: Callable = lambda: _LOG):
        self.force, self.broker, self.confirmed, self.cfg, self.logger = force, broker, confirmed, cfg, logger


# ───────────────────────────── the wrapper ─────────────────────────────
def wrap_execute_now(venue: str, hooks_getter: Callable[[], Hooks]):
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def wrapper(self, *a, **kw):
            try:
                hooks = hooks_getter()
                cfg = hooks.cfg()
            except Exception:
                return await fn(self, *a, **kw)
            try:
                args = {k: v for k, v in sig.bind_partial(self, *a, **kw).arguments.items() if k != "self"}
            except Exception:
                args = dict(kw)
            action = str(args.get("action") or "").upper()
            full_flag = bool(args.get("is_full_close"))
            if not bool(_cfg(cfg, "EXIT_GUARANTEE_ENABLED")) or not is_exit_action(action, full_flag):
                return await fn(self, *a, **kw)
            force = action == FORCE_ACTION
            key = _pk(args)
            full = is_full_close(action, full_flag)
            reason = str(args.get("reason") or "")
            depth = _DEPTH.get()
            c: Dict[str, Any] = {"venue": venue, "key": key, "full": full, "t0": time.time(), "action": action}
            ct = _CTX.set(c)
            with _LOCK:
                _INFLIGHT[key] = _INFLIGHT.get(key, 0) + 1
            res = None
            try:
                _mark_token(full, force)
                res = await fn(self, *a, **kw)
                c["orders"] = _token_orders()
            finally:
                with _LOCK:
                    n = _INFLIGHT.get(key, 0) - 1
                    if n > 0:
                        _INFLIGHT[key] = n
                    else:
                        _INFLIGHT.pop(key, None)
                _CTX.reset(ct)
            if force:
                if depth == 0 and classify(res, reason, True) == "OK":  # direct FORCE caller (vec timeout leg): ledger once here
                    try:
                        c["force"] = parse_force_result(res)
                        await _safe(hooks.confirmed(self, args, c, res))
                        log_event(venue, "CONFIRMED", cfg, key=key, full=True, result=str(res)[:160], via="DIRECT_FORCE")
                    except Exception:
                        pass
                return res
            try:
                return await _after(self, hooks, cfg, venue, args, c, res, depth)
            except Exception as e:  # noqa: BLE001
                try:
                    hooks.logger().error(f"[EXIT_GUARANTEE] {key}: post-processing error {e!r} — result {res} returned unchanged")
                except Exception:
                    pass
                return res

        wrapper.__exit_guarantee__ = True
        return wrapper

    return deco


async def _after(mgr, hooks: Hooks, cfg, venue: str, args: Dict[str, Any], c: Dict[str, Any], res: Any, depth: int):
    key, full = c["key"], c["full"]
    reason = str(args.get("reason") or "")
    log = hooks.logger()
    cls = classify(res, reason, full)
    log_event(venue, "RESULT", cfg, key=key, action=c.get("action"), full=full, reason=reason[:120], result=str(res)[:160], cls=cls, depth=depth, qty=args.get("quantity"))
    if cls == "OK":
        await _safe(hooks.confirmed(mgr, args, c, res))
        log_event(venue, "CONFIRMED", cfg, key=key, full=full, result=str(res)[:160])
        if full and depth == 0 and inflight(key) == 0:  # dust remainder of a "successful" full close is force-closed, never stranded
            b = await _safe(hooks.broker(mgr, args.get("account_key") or key.split(":", 1)[0], args.get("symbol"), str(args.get("position_side") or "").upper()))
            if b is not None and float(b.get("amt") or 0.0) > 0:
                be, ce = float(b.get("entry") or 0.0), float(c.get("entry_price") or 0.0)
                if not (be and ce and abs(be - ce) / ce > 0.002):
                    log.critical(f"🧹 [EXIT_GUARANTEE_REMAINDER] {key}: full close confirmed but broker still holds {b.get('amt')} — FORCE the remainder")
                    fres = await _escalate(mgr, hooks, cfg, venue, args, c, f"REMAINDER_{b.get('amt')}")
                    if classify(fres, reason, True) != "OK" and not str(fres or "").upper().startswith("FORCE_NOTHING_OPEN"):
                        _arm_watcher(mgr, hooks, cfg, venue, args, c, f"REMAINDER_FORCE_FAILED:{str(fres)[:60]}")
        return res
    if depth > 0 or cls in ("NOOP", "POLICY"):
        return res
    if inflight(key) > 0:
        log.warning(f"[EXIT_GUARANTEE] {key}: {res} while another exit call for the key is in flight — that call owns the close (watcher armed)")
        _arm_watcher(mgr, hooks, cfg, venue, args, c, "INFLIGHT_DEFER")
        return res
    if full:
        if cls == "VERIFY":
            now = time.time()
            if now - _LAST_VERIFY.get(key, 0.0) < float(_cfg(cfg, "EXIT_GUARANTEE_VERIFY_FLAT_MIN_INTERVAL_S")):
                return res
            _LAST_VERIFY[key] = now
        fres = await _escalate(mgr, hooks, cfg, venue, args, c, f"{cls}:{str(res)[:80]}")
        if classify(fres, reason, True) == "OK":
            return fres
        if str(fres or "").upper().startswith("FORCE_NOTHING_OPEN"):
            if c.get("orders"):  # our order of this call filled after the confirmation window: broker flat = confirmed close
                await _safe(hooks.confirmed(mgr, args, c, res))
                log_event(venue, "CONFIRMED", cfg, key=key, full=True, result=str(res)[:160], via="LATE_FILL_BROKER_FLAT")
            return res
        _arm_watcher(mgr, hooks, cfg, venue, args, c, f"FORCE_FAILED:{str(fres)[:60]}")
        return res
    _arm_watcher(mgr, hooks, cfg, venue, args, c, f"REDUCE_REFUSED:{str(res)[:60]}")
    return res


async def _escalate(mgr, hooks: Hooks, cfg, venue: str, args: Dict[str, Any], c: Dict[str, Any], why: str):
    key = c["key"]
    log = hooks.logger()
    if not bool(_cfg(cfg, "EXIT_GUARANTEE_FORCE_ENABLED")):
        alert(venue, key, f"FULL close refused ({why}) and FORCE leg disabled by config", cfg, log)
        return "FORCE_DISABLED"
    log.critical(f"🧯 [EXIT_GUARANTEE_ESCALATE] {key}: full close not done ({why}) — FORCE_CLOSE_MARKET from exchange truth (inside execute_now)")
    log_event(venue, "ESCALATE", cfg, key=key, why=why, reason=str(args.get("reason") or "")[:120])
    dt = _DEPTH.set(_DEPTH.get() + 1)
    try:
        fres = await _safe(hooks.force(mgr, args, c, why))
    finally:
        _DEPTH.reset(dt)
    log_event(venue, "FORCE_RESULT", cfg, key=key, result=str(fres)[:200])
    if classify(fres, args.get("reason"), True) == "OK":
        c2 = dict(c)
        c2["force"] = parse_force_result(fres)
        await _safe(hooks.confirmed(mgr, args, c2, fres))
        log_event(venue, "CONFIRMED", cfg, key=key, full=True, result=str(fres)[:160], via="FORCE")
    return fres


def _arm_watcher(mgr, hooks: Hooks, cfg, venue: str, args: Dict[str, Any], c: Dict[str, Any], why: str) -> None:
    key = c["key"]
    t = _WATCHERS.get(key)
    if t is not None and not t.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    log_event(venue, "WATCH", cfg, key=key, why=why, full=c["full"])
    _WATCHERS[key] = loop.create_task(_watch(mgr, hooks, cfg, venue, dict(args), dict(c), why))


async def _watch(mgr, hooks: Hooks, cfg, venue: str, args: Dict[str, Any], c: Dict[str, Any], why: str):
    """Stuck-exit watcher. FULL: re-check exchange truth on EXIT_GUARANTEE_STUCK_CHECK_SEC, FORCE while open. PARTIAL: re-send
    the reduce on EXIT_GUARANTEE_REDUCE_RETRY_SEC while the broker shows it did not happen. Exhausted -> ALERT."""
    key, full = c["key"], c["full"]
    log = hooks.logger()
    sched = _secs(_cfg(cfg, "EXIT_GUARANTEE_STUCK_CHECK_SEC" if full else "EXIT_GUARANTEE_REDUCE_RETRY_SEC"))
    acct, sym, ps = args.get("account_key"), args.get("symbol"), str(args.get("position_side") or "").upper()
    if not acct and ":" in key:
        acct = key.split(":", 1)[0]
    base_amt = c.get("real_amt")
    base_entry = c.get("entry_price")
    qty = abs(float(args.get("quantity") or 0.0))
    last = None
    try:
        for i, d in enumerate(sched):
            await asyncio.sleep(d)
            if inflight(key) > 0:
                continue
            b = await _safe(hooks.broker(mgr, acct, sym, ps))
            if b is not None:
                amt, entry = float(b.get("amt") or 0.0), float(b.get("entry") or 0.0)
                if amt <= 0:
                    if full and c.get("orders"):
                        await _safe(hooks.confirmed(mgr, args, c, "BROKER_FLAT"))
                        log_event(venue, "CONFIRMED", cfg, key=key, full=True, via="WATCH_BROKER_FLAT")
                    log_event(venue, "WATCH_DONE", cfg, key=key, why="BROKER_FLAT", attempt=i)
                    return "FLAT"
                if base_entry and entry and abs(entry - float(base_entry)) / float(base_entry) > 0.002 and (base_amt is None or amt > float(base_amt) * 1.001):
                    log_event(venue, "WATCH_DONE", cfg, key=key, why="LIFECYCLE_CHANGED", entry_then=base_entry, entry_now=entry)
                    return "LIFECYCLE_CHANGED"
                if not full and base_amt and qty > 0 and amt <= float(base_amt) - 0.5 * qty:
                    log_event(venue, "WATCH_DONE", cfg, key=key, why="REDUCE_HAPPENED", amt=amt, base=base_amt)
                    return "REDUCED"
            if full:
                last = await _escalate(mgr, hooks, cfg, venue, args, c, f"STUCK_{why}_t{i}")
                lu = str(last or "").upper()
                if classify(last, args.get("reason"), True) == "OK" or lu.startswith("FORCE_NOTHING_OPEN"):
                    log_event(venue, "WATCH_DONE", cfg, key=key, why=lu[:40], attempt=i)
                    return last
            else:
                dt = _DEPTH.set(_DEPTH.get() + 1)
                try:
                    log.warning(f"🔁 [EXIT_GUARANTEE_RETRY] {key}: partial REDUCE retry {i + 1}/{len(sched)} ({why})")
                    log_event(venue, "RETRY", cfg, key=key, attempt=i + 1, why=why)
                    last = await mgr.execute_now(**args)
                finally:
                    _DEPTH.reset(dt)
                cl = classify(last, args.get("reason"), False)
                if cl in ("OK", "NOOP", "POLICY"):
                    log_event(venue, "WATCH_DONE", cfg, key=key, why=f"RETRY_{cl}", attempt=i + 1, result=str(last)[:120])
                    return last
        alert(venue, key, f"{'FULL CLOSE' if full else 'REDUCE'} still not done after {len(sched)} checks ({why}); last={str(last)[:120]}", cfg, log, reason=str(args.get("reason") or "")[:120])
        return last
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        alert(venue, key, f"watcher crashed: {e!r}", cfg, log)
        return None
    finally:
        _WATCHERS.pop(key, None)


def _reset_for_tests() -> None:
    _INFLIGHT.clear()
    _WATCHERS.clear()
    _LAST_VERIFY.clear()
    _NOTIFY_LAST.clear()
