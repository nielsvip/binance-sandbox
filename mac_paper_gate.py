"""MAC PAPER GATE — while data/MAC_PAPER_TRADING exists next to this file, NO order-mutating broker call leaves this process.

USER 2026-10-27 (verbatim): "we should 'paper trade' macbook for now until all the discrepancies in the scripts and the
execution delays have been completely fixed ... So keep it traidng without execution power" + "unlock and make the edits
make sure all can be reverted to real trading with one switch."

ONE SWITCH: tools/live_mode.sh {paper|real|status}. paper = this flag on the Mac + S1/S5 vector_direct pins (they execute);
real = S1/S5 release their accounts first, then this flag is removed and the Mac executes again.

Installed by order_dedupe_guard at import (every ez_manage / tradier_manage process imports it). It patches the lowest
broker layer, so every path is covered — execute_now, maker chase, FIXED_STOP algo orders, cancels, raw Client objects:
  * python-binance Client._request_futures_api: POST/PUT/DELETE refused (listenKey excepted — the WS position stream needs it)
  * tradier_api.TradierAPIClient._request: POST/PUT/DELETE on .../orders refused
The engine sees an ordinary broker rejection (Binance code -2027 "MAC_PAPER_TRADING", Tradier errors list) and keeps
running, so its decisions and VEC_ logs continue. Every refused call is written to data/mac_paper/orders_<venue>_<day>.jsonl.
The flag does not exist on S1/S5, so the gate is inert there.
"""
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
FLAG = BASE / "data" / "MAC_PAPER_TRADING"
OUT = BASE / "data" / "mac_paper"
_cache = {"t": 0.0, "on": False}
_lock = threading.Lock()
_installed = {"binance": False, "tradier": False}


def paper_on() -> bool:
    now = time.time()
    if now - _cache["t"] > 1.0:
        _cache["on"] = FLAG.exists()
        _cache["t"] = now
    return _cache["on"]


def _record(venue, row):
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        row = {"iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ts": time.time(), "pid": os.getpid(), "venue": venue, **row}
        with _lock, open(OUT / f"orders_{venue}_{datetime.now(timezone.utc):%Y-%m-%d}.jsonl", "a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception:
        pass


def _install_binance():
    from binance.client import Client
    from binance.exceptions import BinanceAPIException
    orig = Client._request_futures_api
    if getattr(orig, "__mac_paper__", False):
        return

    def _request_futures_api(self, method, path, signed=False, version=1, **kwargs):
        if str(method).lower() in ("post", "put", "delete") and not str(path).startswith("listenKey") and paper_on():
            acct = None
            try:
                import order_dedupe_guard as _odg
                acct = _odg._binance_account_for(self)
            except Exception:
                pass
            _record("crypto", {"account": acct, "method": str(method).upper(), "path": path, "params": kwargs.get("data") or kwargs.get("params")})
            raise BinanceAPIException(None, 400, json.dumps({"code": -2027, "msg": "MAC_PAPER_TRADING: order not sent (S1 executes; tools/live_mode.sh real to revert)"}))
        return orig(self, method, path, signed, version, **kwargs)

    _request_futures_api.__mac_paper__ = True
    Client._request_futures_api = _request_futures_api
    _installed["binance"] = True


def _install_tradier():
    import tradier_api
    cls = tradier_api.TradierAPIClient
    orig = cls._request
    if getattr(orig, "__mac_paper__", False):
        return

    async def _request(self, method, endpoint, *args, **kwargs):
        if str(method).upper() in ("POST", "PUT", "DELETE") and "/orders" in str(endpoint) and paper_on():
            _record("stocks", {"account": getattr(self, "account_key", None), "method": str(method).upper(), "endpoint": endpoint, "params": kwargs.get("data") or kwargs.get("params") or (args[1] if len(args) > 1 else None)})
            return {"errors": {"error": ["MAC_PAPER_TRADING: order not sent (S5 executes; tools/live_mode.sh real to revert)"]}}
        return await orig(self, method, endpoint, *args, **kwargs)

    _request.__mac_paper__ = True
    cls._request = _request
    _installed["tradier"] = True


def install():
    """Idempotent. tradier_api is patched only once it is imported (it imports order_dedupe_guard lazily, and a crypto
    process must not pull config_tradier in); order_dedupe_guard.get_tradier_guard calls install() again for that."""
    import sys
    fns = [_install_binance] + ([_install_tradier] if "tradier_api" in sys.modules and hasattr(sys.modules["tradier_api"], "TradierAPIClient") else [])
    for fn in fns:
        try:
            fn()
        except Exception:
            pass
    try:  # proof per process: tools/live_mode.sh status / the operator can confirm every running engine is gated
        (OUT / "installed").mkdir(parents=True, exist_ok=True)
        (OUT / "installed" / f"{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid(), "iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **_installed}))
    except Exception:
        pass
    return dict(_installed)
