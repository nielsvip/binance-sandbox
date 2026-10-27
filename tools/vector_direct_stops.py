"""Protective STOP MARKET for every non-exit order tools/vector_direct_trader.py places.

USER 2026-10-27 (verbatim): "the vector trading script now also has the obligation to place the stop market orders at
dc_15m_low - 0.4% vv shorts for every non exit order it places."

  LONG   stop = dc_low_15m  x (1 - 0.004)      SHORT  stop = dc_high_15m x (1 + 0.004)
  dc_*_15m = the latest CLOSED 15m bar in the host's own NPZ store (.tail sidecar when newer than the zip).
  OPEN / AUGMENT  -> cancel this side's resting stop(s) on the symbol, place one stop for the WHOLE position at the current level.
  REDUCE          -> crypto: nothing (closePosition stop follows the position size); stocks: re-place at the SAME price for
                     the reduced share count (a Tradier stop has a fixed quantity).
  CLOSE           -> cancel this side's resting stop(s).
Binance: algo endpoint (STOP_MARKET is refused on /fapi/v1/order, -4120), closePosition=true, workingType=MARK_PRICE — the
same shape ez_manage's FIXED_STOP uses. Tradier: type=stop, duration=gtc, closing side. A level on the wrong side of the
market (it would fire at once) is not placed: logged + alerted instead.
"""
import datetime as dt
import hashlib
import json
import math
import time
from pathlib import Path

STOP_BUFFER = 0.004


def stop_level(side, dc_low, dc_high):
    if side == "LONG":
        return float(dc_low) * (1.0 - STOP_BUFFER) if dc_low and dc_low > 0 else None
    return float(dc_high) * (1.0 + STOP_BUFFER) if dc_high and dc_high > 0 else None


def round_to_tick(px, tick, side):
    """Round AWAY from the market so the buffer is never shrunk: LONG stop down, SHORT stop up."""
    if not tick or tick <= 0:
        return px
    n = math.floor(px / tick + 1e-9) if side == "LONG" else math.ceil(px / tick - 1e-9)
    dec = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return round(n * tick, dec)


def dc15(npz_dir, symbol):
    """Latest closed-bar dc_low_15m / dc_high_15m for symbol from the host's NPZ store."""
    d = Path(npz_dir).expanduser()
    out = {}
    tp = d / ".tail" / f"{symbol}.jsonl"
    if tp.exists():
        for line in reversed(tp.read_text().splitlines()):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("dc_low_15m") is not None and row.get("dc_high_15m") is not None:
                out = {"bar_ts": int(row.get("timestamps") or 0), "dc_low_15m": float(row["dc_low_15m"]), "dc_high_15m": float(row["dc_high_15m"]), "src": "tail"}
            break
    zp = d / f"{symbol}.npz"
    if zp.exists():
        import numpy as np
        with np.load(str(zp), allow_pickle=False) as z:
            ts = int(z["timestamps"][-1])
            if ts > int(out.get("bar_ts") or 0):
                out = {"bar_ts": ts, "dc_low_15m": float(z["dc_low_15m"][-1]), "dc_high_15m": float(z["dc_high_15m"][-1]), "src": "zip"}
    return out


class Stops:
    def __init__(self, trader):
        self.t = trader

    def _npz_dir(self):
        a = getattr(self.t, "a", None)
        if a is not None and getattr(a, "npz_dir", ""):
            return Path(a.npz_dir).expanduser()
        return Path(self.t.state_dir).expanduser().parents[1] / "backtest_v8" / "indicators"

    def _row(self, **kw):
        row = {"event": "STOP", **kw}
        try:
            self.t._ledger(row)
        except Exception:
            pass
        return row

    def after_fill(self, acct, o, qty, held_before, price):
        """Called once an order is SENT. Returns a short status string for the log. Never raises."""
        try:
            act = o["act"]
            symbol, side = o["ss"].rsplit("_", 1)
            symbol, side = symbol.upper(), side.upper()
            if act == "CLOSE":
                n = self.cancel(acct, symbol, side)
                self._row(acct=acct, ss=o["ss"], act="CANCEL_ON_CLOSE", cancelled=n, key=o["key"])
                return f"stops cancelled {n}"
            if act == "REDUCE" and self.t.venue == "crypto":
                return "closePosition stop follows the size"
            new_qty = max(0.0, held_before + qty) if act in ("OPEN", "AUGMENT") else max(0.0, held_before - qty)
            if act == "REDUCE":
                lvl = self.resting_level(acct, symbol, side)
                if lvl is None:
                    return "reduce: no resting stop to resize"
                src = {"src": "pinned_on_reduce"}
            else:
                src = dc15(self._npz_dir(), symbol)
                lvl = stop_level(side, src.get("dc_low_15m"), src.get("dc_high_15m"))
                if lvl is None:
                    self.t.alert("VD_STOP_NO_DC15", account=acct, sym_side=o["ss"], act=act)
                    self._row(acct=acct, ss=o["ss"], act=act, status="NO_DC15", key=o["key"])
                    return "NO dc_15m -> no stop"
            mark = float(price or 0.0)
            if mark > 0 and ((side == "LONG" and lvl >= mark) or (side == "SHORT" and lvl <= mark)):
                self.t.alert("VD_STOP_WRONG_SIDE", account=acct, sym_side=o["ss"], level=lvl, mark=mark, dc=src)
                self._row(acct=acct, ss=o["ss"], act=act, status="WRONG_SIDE", level=lvl, mark=mark, key=o["key"])
                return f"level {lvl} on the wrong side of mark {mark} -> not placed"
            self.cancel(acct, symbol, side)
            resp = self.place(acct, symbol, side, lvl, new_qty, o["key"])
            self._row(acct=acct, ss=o["ss"], act=act, status="PLACED", level=resp.get("level"), qty=new_qty, dc=src, resp=resp, key=o["key"])
            return f"stop {resp.get('level')} placed ({resp.get('id')})"
        except Exception as exc:
            try:
                self.t.alert("VD_STOP_FAILED", account=acct, sym_side=o.get("ss"), act=o.get("act"), error=str(exc)[:200])
                self._row(acct=acct, ss=o.get("ss"), act=o.get("act"), status="ERROR", detail=str(exc)[:300], key=o.get("key"))
            except Exception:
                pass
            return f"STOP FAILED {str(exc)[:120]}"

    # ── venue specifics ──
    def _tick(self, symbol):
        b = self.t.broker
        cache = b.out / "exchange_ticks_crypto.json"
        doc = getattr(self, "_ticks", None)
        if doc is None or time.time() - float(doc.get("_fetched") or 0) > 6 * 3600:
            try:
                doc = json.loads(cache.read_text())
            except Exception:
                doc = {}
            if time.time() - float(doc.get("_fetched") or 0) > 6 * 3600:
                raw = b._req("GET", "/fapi/v1/exchangeInfo")
                doc = {"_fetched": time.time(), "ticks": {s["symbol"]: float(next((f["tickSize"] for f in s.get("filters") or [] if f["filterType"] == "PRICE_FILTER"), 0) or 0) for s in raw.get("symbols") or []}}
                cache.write_text(json.dumps(doc))
            self._ticks = doc
        return float(doc["ticks"].get(symbol) or 0.0)

    def _algo_open(self, acct, symbol):
        rows = self.t.broker._req("GET", "/fapi/v1/openAlgoOrders", acct, {"symbol": symbol})
        return rows if isinstance(rows, list) else (rows.get("orders") or rows.get("rows") or [])

    def _tr_orders(self, acct, symbol):
        aid, _k, _b = self.t.broker.account(acct)
        doc = self.t.broker._req("GET", acct, f"/accounts/{aid}/orders")
        rows = (doc.get("orders") or {}) if isinstance(doc.get("orders"), dict) else {}
        rows = rows.get("order") or []
        rows = [rows] if isinstance(rows, dict) else rows
        return [r for r in rows if str(r.get("symbol")).upper() == symbol and str(r.get("status")).lower() in ("open", "pending", "partially_filled")
                and str(r.get("type")).lower() in ("stop", "stop_limit")]

    def resting_level(self, acct, symbol, side):
        if self.t.venue == "crypto":
            for r in self._algo_open(acct, symbol):
                if str(r.get("positionSide") or "").upper() == side and str(r.get("orderType") or r.get("type") or "").upper() == "STOP_MARKET":
                    return float(r.get("triggerPrice") or 0) or None
            return None
        close_side = "sell" if side == "LONG" else "buy_to_cover"
        for r in self._tr_orders(acct, symbol):
            if str(r.get("side")).lower() == close_side:
                return float(r.get("stop_price") or r.get("stop") or 0) or None
        return None

    def cancel(self, acct, symbol, side):
        n = 0
        if self.t.venue == "crypto":
            for r in self._algo_open(acct, symbol):
                if str(r.get("positionSide") or "").upper() in (side, "BOTH") and str(r.get("orderType") or r.get("type") or "").upper() in ("STOP_MARKET", "STOP"):
                    self.t.broker._req("DELETE", "/fapi/v1/algoOrder", acct, {"symbol": symbol, "algoId": r.get("algoId")})
                    n += 1
            return n
        aid, _k, _b = self.t.broker.account(acct)
        close_side = "sell" if side == "LONG" else "buy_to_cover"
        for r in self._tr_orders(acct, symbol):
            if str(r.get("side")).lower() == close_side:
                self.t.broker._req("DELETE", acct, f"/accounts/{aid}/orders/{r.get('id')}")
                n += 1
        return n

    def place(self, acct, symbol, side, lvl, qty, key):
        cid = "vds" + hashlib.sha1(f"{key}|{time.time()}".encode()).hexdigest()[:24]
        if self.t.venue == "crypto":
            level = round_to_tick(lvl, self._tick(symbol), side)
            params = {"algoType": "CONDITIONAL", "symbol": symbol, "side": "SELL" if side == "LONG" else "BUY", "type": "STOP_MARKET",
                      "triggerPrice": f"{level:.10f}".rstrip("0").rstrip("."), "closePosition": "true", "workingType": "MARK_PRICE", "clientAlgoId": cid}
            if self.t.broker.is_dual(acct):
                params["positionSide"] = side
            r = self.t.broker._req("POST", "/fapi/v1/algoOrder", acct, params)
            return {"id": r.get("algoId"), "level": level, "status": r.get("algoStatus") or r.get("status")}
        aid, _k, _b = self.t.broker.account(acct)
        level = round_to_tick(lvl, 0.01, side)
        data = {"class": "equity", "symbol": symbol, "side": "sell" if side == "LONG" else "buy_to_cover", "quantity": str(int(qty)),
                "type": "stop", "stop": f"{level:.2f}", "duration": "gtc", "tag": cid[:24]}
        if int(qty) < 1:
            raise RuntimeError(f"stop qty {qty} < 1 share")
        r = self.t.broker._req("POST", acct, f"/accounts/{aid}/orders", data=data)
        return {"id": (r.get("order") or {}).get("id"), "level": level, "status": (r.get("order") or {}).get("status")}
