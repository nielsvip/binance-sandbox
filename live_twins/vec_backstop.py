"""VEC forward-state backstop (USER 2026-10-25): pure divergence logic, no orders.

The forward recompute (tools/v15_forward_state.py) publishes per-sym_side
open/closed state to data/forward_state/<SYM_SIDE>.json. This module reads one
file and reports divergence vs live. Callers place any resulting order through
execute_now() exclusively. Fail-open everywhere: unknown/stale/missing state
never acts. Kill switch: VEC_BACKSTOP_ENABLED=0.
"""
import json
import os
import time
from pathlib import Path

REASON_OPEN = "VEC_FWD_MISSING_OPEN"
REASON_FLAT = "VEC_FWD_MISSING_FLAT"
_DEFAULT_MAX_AGE_S = 1800.0
_seen = {}
_served_tail = {}
_revisions = {}


def enabled(cfg=None):
    try:
        if cfg is not None and hasattr(cfg, "VEC_BACKSTOP_ENABLED"):
            return bool(cfg.VEC_BACKSTOP_ENABLED)
    except Exception:
        pass
    try:
        import config as _c
        return bool(getattr(_c.Config, "VEC_BACKSTOP_ENABLED", True))
    except Exception:
        pass
    return os.environ.get("VEC_BACKSTOP_ENABLED", "1") == "1"


def state_dir(base_path=None):
    try:
        if base_path:
            return Path(base_path) / "data" / "forward_state"
    except Exception:
        pass
    try:
        import config as _c
        return Path(getattr(_c, "BASE_PATH", ".")).joinpath("data", "forward_state")
    except Exception:
        return Path("data", "forward_state")


def _state_file(sym_side, base_path=None):
    if base_path is not None and str(base_path).upper().endswith(("_LONG", "_SHORT")) and Path(sym_side).name == "forward_state":
        return Path(sym_side) / f"{str(base_path).upper()}.json", str(base_path).upper()
    return state_dir(base_path) / f"{str(sym_side).upper()}.json", str(sym_side).upper()


def read_state(sym_side, base_path=None, max_age_s=_DEFAULT_MAX_AGE_S):
    try:
        p, _ = _state_file(sym_side, base_path)
        st = p.stat()
        if max_age_s > 0 and time.time() - st.st_mtime > max_age_s:
            return None
        raw = json.loads(p.read_text())
        return raw if isinstance(raw, dict) and raw.get("ok") is True and raw.get("valid") is True and raw.get("audit_only") is False else None
    except Exception:
        return None


def target_open(state):
    try:
        if not isinstance(state, dict) or not state.get("ok"):
            return None
        o = state.get("open")
        return bool(o) if isinstance(o, bool) else None
    except Exception:
        return None


def divergence(live_open, state):
    stock_call = isinstance(state, bool) and (isinstance(live_open, dict) or live_open is None)
    if stock_call:
        live_open, state = state, live_open
    want = target_open(state)
    if want is None:
        return None if stock_call else (None, "")
    try:
        src = str(state.get("src") or "unknown")
        tail = str(state.get("npz_tail") or "?")
    except Exception:
        src, tail = "unknown", "?"
    if want and not live_open:
        return "OPEN" if stock_call else ("OPEN", f"{REASON_OPEN}:{src}@{tail}")
    if not want and live_open:
        return "FLAT" if stock_call else ("FLAT", f"{REASON_FLAT}:{src}@{tail}")
    return None if stock_call else (None, "")


def position_for_account(manager, account_key, position_key):
    """Resolve a position from its exact account book before any flat fallback."""
    try:
        key = str(position_key or "")
        account = str(account_key or "")
        key_account = key.split(":", 1)[0] if ":" in key else ""
        if account and key_account and account != key_account:
            return None
        account = account or key_account
        service = getattr(manager, "positions_service", None)
        books = (
            getattr(service, "positions_by_account", None),
            getattr(manager, "positions_by_account", None),
        )
        for books_by_account in books:
            if not isinstance(books_by_account, dict):
                continue
            book = books_by_account.get(account, {})
            if isinstance(book, dict) and key in book:
                return book[key]
        for source in (service, manager):
            positions = getattr(source, "positions", None)
            if not isinstance(positions, dict):
                continue
            candidate = positions.get(key)
            if candidate is not None:
                return candidate
    except Exception:
        return None
    return None


def fresh_state(sym_side, base_path=None, max_age_s=_DEFAULT_MAX_AGE_S):
    """State file only when its tail is NEWER than last served (event edge).

    The engine calls this on its hot path: None most calls (zero cost beyond
    one read), a dict the moment a fresh NPZ recompute lands. Fail-open."""
    try:
        p, key = _state_file(sym_side, base_path)
        try:
            mt = p.stat().st_mtime
        except Exception:
            return None
        if _served_tail.get(key + ":mtime") == mt:
            return None
        st = read_state(sym_side, base_path, max_age_s)
        if not isinstance(st, dict):
            return None
        _served_tail[key + ":mtime"] = mt
        tail = str(st.get("npz_tail") or "?")
        revision = (tail, str(st.get("settings_sha256") or ""), str(st.get("engine_ast_sha256") or ""))
        if _served_tail.get(key) == revision:
            return None
        _served_tail[key] = revision
        _revisions[key] = revision
        return st
    except Exception:
        return None


def record(base_path=None, entry=None, **kwargs):
    try:
        if entry is None:
            entry = {"ts": time.time(), **kwargs}
        d = state_dir(base_path).parent / "vec_backstop_actions.jsonl"
        d.parent.mkdir(parents=True, exist_ok=True)
        with open(d, "a") as f:
            f.write(json.dumps(dict(entry)) + "\n")
    except Exception:
        pass


def due(sym_side, tail_bar, retry_s=120.0):
    """True when this (sym_side, tail) may enforce now — first sighting, or
    re-armed after retry_s while the divergence persists.

    USER 2026-10-25 FINAL: one-shot edges let a single failed/refused force
    rot forever (measured 5h live-vs-flat divergences). The caller only asks
    while divergence() is non-empty, so re-arming == retrying a live
    discrepancy, never spam: convergence stops the calls. Fail-open.
    """
    try:
        if isinstance(retry_s, dict):
            state = retry_s
            sym_side = state.get("sym_side") or sym_side
            tail_bar = state.get("npz_tail") or tail_bar
            retry_s = 120.0
        symbol_side = str(sym_side).upper()
        key = (symbol_side, str(tail_bar), _revisions.get(symbol_side))
        prev = _seen.get(key)
        now = time.time()
        try:
            gap = float(retry_s)
        except (TypeError, ValueError):
            gap = 120.0
        if prev is not None and (now - float(prev)) < max(1.0, gap):
            return False
        _seen[key] = now
        if len(_seen) > 4096:
            old = sorted(_seen.items(), key=lambda kv: kv[1])[:2048]
            for k, _ in old:
                _seen.pop(k, None)
        return True
    except Exception:
        return True
