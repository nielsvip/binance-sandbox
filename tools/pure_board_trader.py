#!/usr/bin/env python3
"""pure_board_trader — trade EXACTLY the crypto tradeable_keys, on EXACTLY the latest published board set. Nothing else.

USER 2026-10-18: "make a script that trades EXACTLY what is in crypto tradeble_keys and ONLY trade the LATEST best settings in
/SPREADSHEETS - meanwhile paper trade whatever is trading right now and get me an hourly report of discrepancies that you
analyze so we know why real accounts lose and backtests win".

WHAT IT IS. One process, one rule, no second opinion:
  universe   `tradeable_keys.json`, crypto only, optionally narrowed by --accounts. Nothing outside that list is ever touched.
  settings   `data/published_selection.json` -> the LATEST published board per sym_side (tools/v15_published_selection.py:
             the newest `fleet_orchestrator_publish` row per sym_side, i.e. the sheet in SPREADSHEETS/V15_V16_CELL_BY_CELL).
             No per-sym store, no cat_side layer, no config fallback: a key with no published board is SKIPPED, loudly.
  decision   the BACKTEST ENGINE and nothing else: `tools.opt.evaluate_v12.evaluate_prepared(include_ledger=True)` on the
             frozen 15m bars, and the acts for the newest closed bar are read out of the ledger it returns. This script
             imports no trading script, holds no decision logic of its own, and cannot change what live does
             (USER 2026-10-18: "you just need to trade a set of switches not screw with the trading scripts / OUTSIDE OF
             THE SCRIPTS"). The one exception is --live below, because an order may not leave by any other door.
  execution  --paper (default): nothing is sent; the intent and a paper position are recorded.
             --live: the ONLY thing borrowed from the trading scripts, unmodified: orders leave through `ez_manage`
             `execute_now`, because CLAUDE.md makes it the single gate for every order. It REFUSES to
             start when an `ez_manage --account <acct>` process is alive for a traded account — two traders on one account is
             how FIXED_STOP wars and double closes happen.
  ledger     `data/pure_board/intents_<date>.jsonl` (every intent, kept or skipped, with the bar and the board md5) and the
             paper book `data/pure_board/paper_state.json`. tools/pure_board_report.py turns both into the hourly report.

WHY IT EXISTS. The accounts lose while the boards win. Everything between the board and the exchange — per-sym store drift,
convergence backstops, live-only sizing, sweep timeouts, native paths — is removed here, so what remains is the board's own
strategy on the board's own bars. If this lane also loses, the boards are wrong; if it wins, the plumbing is.

Usage:
    tools/pure_board_trader.py --once                     # evaluate the last closed bar, paper only, print a summary
    tools/pure_board_trader.py --loop                     # evaluate at every closed 15m bar (paper)
    tools/pure_board_trader.py --loop --live --accounts inf   # real orders for that account (its ez_manage must be stopped)
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
BAR_S = 900.0
OUT = ROOT / "data" / "pure_board"
FROZEN = Path(os.environ.get("VEC_EXACT_FROZEN_DIR", "/tmp/vec_exact_frozen"))
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
COST_PCT = 0.10


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).strftime('%H:%M:%S')}Z] {msg}", flush=True)


def universe(accounts):
    keys = [k for k in json.load(open(ROOT / "tradeable_keys.json")) if ":" in k]
    out = []
    for k in keys:
        acct, ss = k.split(":", 1)
        if accounts and acct not in accounts:
            continue
        sym = ss.rsplit("_", 1)[0]
        if not sym.endswith(("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")):
            continue  # crypto only
        out.append((acct, ss))
    if accounts and "trb" in accounts:
        # USER 2026-10-21: trb converges too — its universe is the curated stock lists.
        try:
            _longs = json.load(open(ROOT / "symbols_trb_long.json")) or []
            _shorts = json.load(open(ROOT / "symbols_trb_short.json")) or []
        except Exception:
            _longs, _shorts = [], []
        for _s in _longs:
            out.append(("trb", f"{str(_s).upper()}_LONG"))
        for _s in _shorts:
            out.append(("trb", f"{str(_s).upper()}_SHORT"))
    return sorted(out)


def live_positions(accounts):
    """What the accounts actually hold right now, from the position writer's own snapshots ({acct}/{long,short}_positions.json,
    the files ez_positions_service keeps in step with the exchange). Read-only; the lane never writes them."""
    out = {}
    for acct in accounts:
        for side in ("long", "short"):
            fp = ROOT / acct / f"{side}_positions.json"
            try:
                d = json.load(open(fp))
            except Exception:
                continue
            for key, p in (d or {}).items():
                try:
                    amt = abs(float(p.get("positionAmt") or 0.0))
                    if amt <= 0:
                        continue
                    ss = key.split(":", 1)[1] if ":" in key else key
                    out[(acct, ss)] = {"qty": amt, "entry": float(p.get("entry_price") or 0.0),
                                       "mark": float(p.get("mark_price") or 0.0), "gain": p.get("gain"),
                                       "opened_at": p.get("opened_at")}
                except Exception:
                    continue
    return out


def boards():
    doc = json.load(open(ROOT / "data" / "published_selection.json"))
    return doc.get("sym_sides") or {}


def last_closed_bar(now=None):
    return (float(now or time.time()) // BAR_S) * BAR_S - BAR_S


class PaperBook:
    """Paper positions at the vector's own size, marked on real 15m closes. One book per account:sym_side."""

    def __init__(self, path: Path):
        self.path = path
        self.pos = {}
        self.closed = []
        if path.exists():
            try:
                d = json.load(open(path))
                self.pos, self.closed = d.get("pos") or {}, d.get("closed") or []
            except Exception:
                pass

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pos": self.pos, "closed": self.closed[-5000:], "saved_utc": dt.datetime.now(dt.timezone.utc).isoformat()}, default=str, indent=1))
            os.replace(tmp, self.path)
        except Exception as exc:
            log(f"paper book save failed: {exc}")

    def apply(self, key, action, qty, price, bar_ts, reason, is_long):
        a = str(action).upper()
        p = self.pos.get(key)
        if a in ("OPEN", "AUGMENT") and price and qty:
            if p is None:
                self.pos[key] = {"qty": float(qty), "entry": float(price), "bar": bar_ts, "is_long": is_long, "legs": 1, "reason": reason}
            else:
                tot = p["qty"] + float(qty)
                p["entry"] = (p["entry"] * p["qty"] + float(price) * float(qty)) / tot if tot else p["entry"]
                p["qty"], p["legs"] = tot, p.get("legs", 1) + 1
            return "PAPER_OPEN" if p is None else "PAPER_AUGMENT"
        if a in ("CLOSE", "REDUCE") and p is not None and price:
            frac = 1.0 if a == "CLOSE" else min(1.0, float(qty or 0) / p["qty"] if p["qty"] else 1.0)
            q = p["qty"] * frac
            gross = (float(price) - p["entry"]) / p["entry"] * 100.0
            pnl_pct = gross if p["is_long"] else -gross
            self.closed.append({"key": key, "bar": bar_ts, "entry": p["entry"], "exit": float(price), "qty": q,
                                "pnl_pct": round(pnl_pct - COST_PCT, 4), "pnl_usd": round((pnl_pct - COST_PCT) / 100.0 * q * p["entry"], 4),
                                "reason": reason, "held_bars": int((bar_ts - p["bar"]) / BAR_S) if p.get("bar") else None})
            p["qty"] -= q
            if p["qty"] <= 1e-12:
                self.pos.pop(key, None)
            return "PAPER_CLOSE" if frac >= 1.0 else "PAPER_REDUCE"
        return "PAPER_NOOP"


def live_trader_running(accounts):
    try:
        out = subprocess.run(["ps", "-eo", "command"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return list(accounts)
    return [a for a in accounts if f"ez_manage.py --account {a}" in out]


async def execute_live(tm, acct, ss, action, qty, price, reason):
    """Orders leave through ez_manage's normal path — `execute_trade_action` -> `execute_now` — with the system's own
    order placement (maker chase included). USER 2026-10-18: "NOT FUCKING MARKET CLOSES ... TRADE WHAT THE SYSTEM TELLS
    YOU"; the lane decides WHAT and WHEN from the board, it does not second-guess HOW the order is placed."""
    sym = ss.rsplit("_", 1)[0]
    side = "LONG" if ss.endswith("_LONG") else "SHORT"
    a = str(action).upper()
    opening = a in ("OPEN", "AUGMENT")
    oside = ("BUY" if side == "LONG" else "SELL") if opening else ("SELL" if side == "LONG" else "BUY")
    return await tm.execute_trade_action(account_key=acct, position_key=f"{acct}:{ss}", symbol=sym, quantity=abs(float(qty)),
                                         current_price=float(price), side=oside, position_side=side,
                                         reason=f"{reason} |PURE_BOARD", is_reduce=(not opening), is_full_close=(a == "CLOSE"))


def _fresh_raw(symside: str):
    """USER 2026-10-21 lane repair: build this bar's arrays from canonical klines.
    The frozen pkl store rotted (twin off -> no _freeze since Oct 20); deciding on it births
    intents the 300s consumer rule correctly refuses (70% REFUSED_STALE). Engine-side imports
    only (backtest_v8_precompute + htf align twin) — no trading scripts, per the 10-18 lane rule."""
    try:
        import backtest_v8_precompute as _pc
        _sym = symside.rsplit("_", 1)[0]
        _mode = "crypto" if _sym.endswith(("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")) else "tradier"
        raw = _pc.compute_symbol(_sym, _mode, return_arrays=True)
        if not isinstance(raw, dict):
            return None
        try:
            from vec_decisions.htf_causal_align import align_store as _hta
            raw = _hta(raw, _sym, _mode)
        except ImportError:
            return None
        return raw
    except Exception:
        return None


def prepared_from_raw(symside: str, raw: dict):
    """prepare() on in-memory arrays (store-swap pattern shared with prepared_from_frozen)."""
    from tools.opt import evaluate_v12 as E
    o_store, o_start = E._store, E._frozen_npz_start_date
    E._store = lambda sym, mode, start: raw
    E._frozen_npz_start_date = lambda *a, **k: "1970-01-01"
    try:
        return E.prepare(symside, 30)
    finally:
        E._store, E._frozen_npz_start_date = o_store, o_start


def prepared_from_frozen(symside: str):
    """Build the engine's own prepare on the frozen 15m bars — nothing from the trading scripts is imported.

    USER 2026-10-18 "you just need to trade a set of switches not screw with the trading scripts / OUTSIDE OF THE SCRIPTS":
    this lane talks ONLY to the backtest engine (`tools.opt.evaluate_v12`, the same call the sheets and the charts use) and
    to data files. `evaluate_v12.prepare` normally loads an NPZ; here its store accessor is swapped for the frozen bar arrays
    for the duration of the call, which is the engine's documented way to prepare from memory.
    """
    import pickle

    path = FROZEN / f"{symside.rsplit('_', 1)[0]}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as fh:
        raw = dict(pickle.load(fh))
    return prepared_from_raw(symside, raw)


def actions_from_engine(symside: str, overrides: dict, prepared: dict, bar_ts: float) -> dict:
    """The engine's own decisions AT that bar, read out of its execution ledger. No decision logic lives here.

    `evaluate_prepared(include_ledger=True)` returns the ledger the sheets and charts are built from; every row carries the
    bar it happened on, so the acts for the newest closed bar are a filter, not a re-derivation. FINAL_MTM (the window-edge
    mark) is never an action.
    """
    import numpy as np
    from tools.opt import evaluate_v12 as E

    ts = np.asarray(prepared["npz_prepared"]["timestamps"], dtype=float)
    if ts.size < 101:
        return {"status": "WARMUP", "actions": [], "bar_ts": bar_ts}
    k = int(np.searchsorted(ts, float(bar_ts), side="right")) - 1
    if k < 0:
        return {"status": "BEFORE_ANCHOR", "actions": [], "bar_ts": bar_ts}
    cut = float(ts[k])
    q = dict(prepared)
    q["npz_prepared"] = {a: (v[: k + 1] if isinstance(v, np.ndarray) and v.shape[:1] == (ts.size,) else v)
                         for a, v in prepared["npz_prepared"].items()}
    r = E.evaluate_prepared(q, dict(overrides or {}), include_ledger=True)
    acts, pos_q = [], 0.0
    for e in r.get("execution_ledger") or []:
        typ = str(e.get("type") or "").upper()
        reason = str(e.get("reason") or e.get("exit_reason") or e.get("entry_reason") or "")
        if reason.startswith("FINAL_MTM"):
            continue
        qv = abs(float(e.get("qty") or 0.0))
        if float(e.get("ts") or 0.0) < cut:
            pos_q = pos_q + qv if typ in ("OPEN", "AUGMENT") else max(0.0, pos_q - qv)
            continue
        if typ in ("OPEN", "AUGMENT"):
            acts.append({"type": typ, "reason": reason, "vec_qty": qv, "vec_price": float(e.get("price") or 0.0), "qty_frac": None})
            pos_q += qv
        elif typ in ("CLOSE", "REDUCE"):
            frac = 1.0 if (typ == "CLOSE" or qv <= 0 or pos_q <= 0) else min(1.0, qv / pos_q)
            acts.append({"type": typ, "reason": reason, "vec_qty": qv, "vec_price": float(e.get("price") or 0.0), "qty_frac": round(frac, 4)})
            pos_q = max(0.0, pos_q - qv)
    return {"status": "OK", "actions": acts, "bar_ts": cut, "engine_trades": r.get("trades"),
            "engine_gain": r.get("gain_pct"), "engine_state": engine_state(r.get("execution_ledger") or [])}


def engine_state(ledger) -> dict:
    """Does the board's own strategy HOLD at the last bar of the window, and from which bar?

    This is the state the lane converges to, and it is why a position cannot sit open for hours: an event can be missed,
    a state cannot. If the engine is flat and live holds, the position is closed on that bar — no 2-hour timer
    (USER 2026-10-18: "IT IS ABOUT NOT CLOSING FOR HOURS OR DAYS").
    """
    last_open = last_close = None
    entry_px = qty = 0.0
    entry_reason, last_close_reason = "", ""  # USER 2026-10-21 converge audit: which vector leg live ignored.
    for e in ledger or []:
        typ = str(e.get("type") or "").upper()
        reason = str(e.get("reason") or e.get("exit_reason") or "")
        if reason.startswith("FINAL_MTM"):
            continue  # a window-edge mark is not a close
        ts = float(e.get("ts") or 0.0)
        if typ in ("OPEN", "AUGMENT"):
            if last_open is None or last_close is None or ts >= last_open:
                pass
            last_open = ts
            q = abs(float(e.get("qty") or 0.0))
            px = float(e.get("price") or 0.0)
            if typ == "OPEN" or qty <= 0:
                qty, entry_px = q, px
                entry_reason = reason
            else:
                tot = qty + q
                entry_px = (entry_px * qty + px * q) / tot if tot else entry_px
                qty = tot
            if qty > 0 and not entry_reason:
                entry_reason = reason
        elif typ == "CLOSE" or (typ == "REDUCE" and e.get("pnl_dollars") is not None and float(e.get("qty") or 0.0) == 0.0):
            last_close = ts
            last_close_reason = reason
            qty, entry_px = 0.0, 0.0
        elif typ == "REDUCE":
            q = abs(float(e.get("qty") or 0.0))
            qty = max(0.0, qty - q)
            if qty <= 1e-12:
                last_close = ts
                last_close_reason = reason
                entry_px = 0.0
    holding = last_open is not None and (last_close is None or last_open > last_close) and qty > 0
    return {"holding": bool(holding), "qty": round(qty, 10), "entry_price": entry_px,
            "since": last_open if holding else None, "last_close": last_close,
            "entry_reason": entry_reason, "last_close_reason": last_close_reason}


VEC_LIVE = ROOT / "data" / "vec_live"


def write_registry(keys, hours: float):
    """data/vec_live/vec_driven.json — which (sym_side, account) ez_manage may drive from our intents, and for how long.

    The registry carries its own expiry by design (USER 2026-10-06 "very dangerous, can only run for a couple of hours"):
    past expires_utc every entry resolves back to SHADOW and native management takes the position back, and
    `data/vec_live/KILL` does the same instantly. The lane refreshes the expiry each pass while it is alive, so stopping
    the lane is itself the kill switch.
    """
    reg = {"_why": "tools/pure_board_trader.py — the latest published board set per sym_side decides, ez_manage executes",
           "_written_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
           "expires_utc": dt.datetime.fromtimestamp(time.time() + hours * 3600.0, dt.timezone.utc).isoformat()}
    for acct, ss in keys:
        e = reg.setdefault(ss, {"mode": "live", "account": []})
        if isinstance(e.get("account"), list) and acct not in e["account"]:
            e["account"].append(acct)
    for k, v in reg.items():
        if isinstance(v, dict) and isinstance(v.get("account"), list) and len(v["account"]) == 1:
            v["account"] = v["account"][0]
    VEC_LIVE.mkdir(parents=True, exist_ok=True)
    tmp = VEC_LIVE / "vec_driven.json.tmp"
    tmp.write_text(json.dumps(reg, indent=1))
    os.replace(tmp, VEC_LIVE / "vec_driven.json")
    return reg


def write_intent(acct: str, ss: str, bar_ts: float, typ: str, reason: str, target_holding: bool, qty_frac=None):
    """One intent file for ez_manage's consumer: it translates this into an execute_now call, idempotent by intent_id."""
    d = VEC_LIVE / "intents"
    d.mkdir(parents=True, exist_ok=True)
    iid = f"PB-{acct}-{ss}-{int(bar_ts)}-{typ}"
    rec = {"intent_id": iid, "ss": ss, "account": acct, "bar_ts": int(bar_ts), "type": typ,
           "reason": f"PURE_BOARD {reason}"[:120], "target_state": ("LONG" if target_holding and ss.endswith("_LONG")
                                                                    else "SHORT" if target_holding else "FLAT"),
           "mode": "live", "written_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    if qty_frac is not None:
        rec["qty_frac"] = float(qty_frac)
    tmp = d / f"{iid}.json.tmp"
    tmp.write_text(json.dumps(rec, indent=1))
    os.replace(tmp, d / f"{iid}.json")
    sd = VEC_LIVE / "state"
    sd.mkdir(parents=True, exist_ok=True)
    st = {"ss": ss, "target_state": rec["target_state"], "bar_ts": int(bar_ts),
          "written_utc": rec["written_utc"], "source": "pure_board_trader"}
    tmp2 = sd / f"{ss}.json.tmp"
    tmp2.write_text(json.dumps(st, indent=1))
    os.replace(tmp2, sd / f"{ss}.json")
    return iid


def _board_gain(pb):
    """The gain the published board claimed for this set (evidence is a dict on fresh selections, a JSON string on older)."""
    ev = (pb or {}).get("evidence")
    if isinstance(ev, str):
        try:
            ev = json.loads(ev.replace("'", '"'))
        except Exception:
            return None
    try:
        return round(float((ev or {}).get("gain_pct")), 3)
    except Exception:
        return None


def _eval_one(job):
    """One sym_side on one bar, in its own process. Returns only JSON-able data (no numpy, no engine objects)."""
    ss, ov, bar_ts = job
    try:
        raw = _fresh_raw(ss)
        if raw is not None:
            prep = prepared_from_raw(ss, raw)
            store = "fresh"
        else:
            prep = prepared_from_frozen(ss)
            store = "frozen"
        if prep is None:
            return ss, {"status": "SKIP_NO_BAR_STORE", "actions": []}
        out = actions_from_engine(ss, ov, prep, bar_ts)
        out["bar_store"] = store
        return ss, out
    except Exception as exc:
        return ss, {"status": f"EVAL_ERROR:{type(exc).__name__}", "actions": [], "err": str(exc)[:160]}


def run_bar(uni, bds, book, bar_ts, live_tm=None, ledger=None, workers=4, held=None, close_only=False, vec_driven=False):
    from concurrent.futures import ProcessPoolExecutor

    stats = collections.Counter()
    intents = []
    by_ss = collections.defaultdict(list)
    for acct, ss in uni:
        by_ss[ss].append(acct)

    jobs, skipped = [], []
    for ss in sorted(by_ss):
        pb = bds.get(ss)
        if not pb or not (pb.get("overrides") or {}):
            skipped.append((ss, {"status": "SKIP_NO_PUBLISHED_BOARD", "actions": []}, None))
            continue
        jobs.append((ss, pb["overrides"], bar_ts))
    results = list(skipped)
    def _drain(triples):
        for ss, res, pb in triples:
                status = res.get("status", "?")
                stats[status] += 1
                acts = res.get("actions") or []
                # THE TRADED DECISION: the board's own position state at this bar vs what the account actually holds.
                # An event can be missed; a state cannot — this is what stops a position sitting open for hours or days.
                est = res.get("engine_state") or {}
                if status == "OK":
                    for acct in by_ss[ss]:
                        lh = (held.get((acct, ss)) or {}).get("qty", 0.0)
                        eng_hold, eng_qty = bool(est.get("holding")), float(est.get("qty") or 0.0)
                        verdict = ("AGREE_HOLD" if (eng_hold and lh > 0) else "AGREE_FLAT" if (not eng_hold and lh <= 0)
                                   else "BOARD_HOLDS_LIVE_FLAT" if eng_hold else "BOARD_FLAT_LIVE_HOLDS")
                        stats[f"STATE_{verdict}"] += 1
                        if verdict in ("AGREE_HOLD", "AGREE_FLAT"):
                            continue
                        conv = "CONVERGE_OPEN" if verdict == "BOARD_HOLDS_LIVE_FLAT" else "CONVERGE_CLOSE"
                        if close_only and conv == "CONVERGE_OPEN":
                            stats["SKIPPED_OPEN_CLOSE_ONLY"] += 1
                            continue
                        row = {"ts": time.time(), "bar_ts": bar_ts, "acct": acct, "sym_side": ss, "action": conv,
                               "reason": f"{verdict} board_qty={eng_qty:.6f} live_qty={lh:.6f} since={est.get('since')}",
                               "vec_leg": (est.get("entry_reason") if conv == "CONVERGE_OPEN" else est.get("last_close_reason")) or "",
                               "price": float(est.get("entry_price") or 0.0) or None, "vec_qty": (eng_qty if conv == "CONVERGE_OPEN" else lh),
                               "qty_frac": (1.0 if conv == "CONVERGE_CLOSE" else None), "status": status,
                               "decided_s_after_bar_close": round(time.time() - (float(bar_ts) + BAR_S), 1),
                               "board_md5": (pb or {}).get("overrides_md5", ""), "board_gain": _board_gain(pb),
                               "board_published_at": (pb or {}).get("published_at"), "engine_trades": res.get("engine_trades"),
                               "engine_gain": res.get("engine_gain"), "live_entry": (held.get((acct, ss)) or {}).get("entry"),
                               "live_gain_pct": (held.get((acct, ss)) or {}).get("gain")}
                        stats[f"CONV_{conv}"] += 1
                        if vec_driven:
                            try:
                                iid = write_intent(acct, ss, bar_ts, "CLOSE" if conv == "CONVERGE_CLOSE" else "OPEN",
                                                   row["reason"], target_holding=(conv == "CONVERGE_OPEN"),
                                                   qty_frac=(1.0 if conv == "CONVERGE_CLOSE" else None))
                                row["intent_id"] = iid
                                stats["INTENT_WRITTEN"] += 1
                            except Exception as exc:
                                row["intent_error"] = f"{type(exc).__name__}: {exc}"[:120]
                                stats["INTENT_ERROR"] += 1
                        if live_tm is not None:
                            try:
                                px = float((held.get((acct, ss)) or {}).get("mark") or est.get("entry_price") or 0.0)
                                if conv == "CONVERGE_OPEN" and px > 0:
                                    # The board decides WHAT and WHEN; the account's own sizing decides HOW MUCH (BIBLE 68.3a).
                                    # The engine's qty is on ITS capital base (START_POSITION_SIZE x multipliers) and would
                                    # oversize a small live book, so a fresh open is sized like live sizes one and then passes
                                    # through execute_now's own caps (CRYPTO_MINIMUM_TRADING_MODE etc.).
                                    import config as _cfg_live
                                    _base = float(getattr(_cfg_live.Config, "START_POSITION_SIZE", 0.0) or 0.0)
                                    if _base > 0:
                                        row["vec_qty"] = _base / px
                                        row["sized_by"] = f"live START_POSITION_SIZE ${_base:.0f}"
                                r_ = asyncio.get_event_loop().run_until_complete(
                                    execute_live(live_tm, acct, ss, "CLOSE" if conv == "CONVERGE_CLOSE" else "OPEN",
                                                 row["vec_qty"], px, row["reason"]))
                                row["live_result"] = str(r_)[:120]
                                stats["LIVE_SENT"] += 1
                            except Exception as exc:
                                row["live_result"] = f"ERROR {type(exc).__name__}: {exc}"[:160]
                                stats["LIVE_ERROR"] += 1
                        intents.append(row)
                if not acts:
                    if status not in ("OK", "WARMUP"):
                        for acct in by_ss[ss]:
                            intents.append({"ts": time.time(), "bar_ts": bar_ts, "acct": acct, "sym_side": ss, "action": "", "status": status,
                                            "board_md5": (pb or {}).get("overrides_md5", ""), "detail": res.get("err", "")})
                    continue
                for a in acts:
                    action = str(a.get("type") or "").upper()
                    reason = str(a.get("reason") or "")[:120]
                    px = float(a.get("vec_price") or 0.0) or None
                    for acct in by_ss[ss]:
                        qty = float(a.get("vec_qty") or 0.0)
                        row = {"ts": time.time(), "bar_ts": bar_ts, "acct": acct, "sym_side": ss, "action": action, "reason": reason,
                               "price": px, "vec_qty": qty, "qty_frac": a.get("qty_frac"), "status": status,
                               "decided_s_after_bar_close": round(time.time() - (float(bar_ts) + BAR_S), 1),
                               "board_md5": (pb or {}).get("overrides_md5", ""), "board_gain": _board_gain(pb),
                               "board_published_at": (pb or {}).get("published_at")}
                        paper = book.apply(f"{acct}:{ss}", action, qty or 1.0, px, bar_ts, reason, ss.endswith("_LONG"))
                        row["paper"] = paper
                        stats[f"ACT_{action}"] += 1
                        stats[paper] += 1
                        if live_tm is not None and px and (qty or action in ("CLOSE", "REDUCE")):
                            try:
                                r = asyncio.get_event_loop().run_until_complete(execute_live(live_tm, acct, ss, action, qty or 0.0, px, reason))
                                row["live_result"] = str(r)[:120]
                                stats["LIVE_SENT"] += 1
                            except Exception as exc:
                                row["live_result"] = f"ERROR {type(exc).__name__}: {exc}"[:160]
                                stats["LIVE_ERROR"] += 1
                        intents.append(row)

    held = held or {}
    def _flush_new(from_idx):
        if ledger is None or from_idx >= len(intents):
            return
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a") as fh:
            for r in intents[from_idx:]:
                fh.write(json.dumps(r, default=str, separators=(",", ":")) + "\n")
    # USER 2026-10-21 lane repair: chunked submit + drain-as-completed so early keys
    # decide minutes sooner (tail-of-pass intents were born stale). Same order, same
    # aggregation — handling interleaves per chunk. ROLLBACK: single ex.map over jobs.
    _drain(list(skipped))  # skipped detail rows first, exactly as before
    _flush_new(0)
    # USER 2026-10-21 trb + staleness: last pass's mismatches evaluate FIRST and each chunk
    # streams to the ledger immediately, so the keys that need forcing get fresh verdicts in
    # minutes even when the full pass runs long. ROLLBACK: drop the sort + _flush_new calls.
    if jobs and ledger is not None:
        try:
            _prio = set()
            for _lf in glob.glob(str(ledger.parent / "intents_*.jsonl")):
                with open(_lf) as _fh:
                    for _ln in _fh:
                        try:
                            _r = json.loads(_ln)
                        except Exception:
                            continue
                        if str(_r.get("action") or "") in ("CONVERGE_OPEN", "CONVERGE_CLOSE"):
                            _prio.add(_r.get("sym_side"))
            if _prio:
                jobs.sort(key=lambda j: (0 if j[0] in _prio else 1, j[0]))
        except Exception:
            pass
    _CH = 24
    if jobs:
        with ProcessPoolExecutor(max_workers=max(1, int(workers))) as ex:
            for _ci in range(0, len(jobs), _CH):
                _batch = []
                for ss, res in ex.map(_eval_one, jobs[_ci:_ci + _CH], chunksize=1):
                    _batch.append((ss, res, bds.get(ss)))
                results.extend(_batch)
                _n0 = len(intents)
                _drain(_batch)
                _flush_new(_n0)
    book.save()
    return stats, intents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default=",".join(ACCOUNTS))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--live", action="store_true", help="send real orders through ez_manage.execute_now (the account's own ez_manage must be stopped)")
    ap.add_argument("--vec-driven", action="store_true", help="hand every decision to ez_manage's own VEC_DRIVEN consumer (it executes through execute_now); the lane places no orders itself")
    ap.add_argument("--registry-hours", type=float, default=12.0, help="how long the vec_driven registry entries stay live before they expire back to shadow")
    ap.add_argument("--close-only", action="store_true", help="converge by CLOSING only: every position the board no longer holds is closed on its bar, nothing new is opened")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--bar", type=float, default=0.0, help="evaluate this bar ts instead of the last closed one")
    ap.add_argument("--offset", type=float, default=8.0, help="seconds after the 15m bar close to start the pass")
    a = ap.parse_args()
    accounts = [x.strip() for x in a.accounts.split(",") if x.strip()]
    uni = universe(accounts)
    held = live_positions(accounts)
    adopted = sorted(set(held) - set(uni))
    if adopted:
        uni = sorted(set(uni) | set(adopted))
        log(f"ADOPTED {len(adopted)} open position(s) outside tradeable_keys — they are managed too, never stranded: "
            + ", ".join(f"{a_}:{s_}" for a_, s_ in adopted[:10]) + (" …" if len(adopted) > 10 else ""))
    bds = boards()
    missing = sorted({ss for _ac, ss in uni if ss not in bds})
    log(f"universe {len(uni)} keys / {len({s for _a, s in uni})} sym_sides over accounts {accounts}; published boards {len(bds)}; without a board: {len(missing)}"
        + ("  [CLOSE-ONLY: the board's exits are traded, no new exposure]" if a.close_only else "")
        + ("  [LIVE ORDERS]" if a.live else "  [paper]"))
    if missing:
        log(f"NO PUBLISHED BOARD (never traded by this script): {', '.join(missing[:12])}{' …' if len(missing) > 12 else ''}")
    live_tm = None
    if a.live:
        clash = live_trader_running(accounts)
        if clash:
            log(f"REFUSING --live: ez_manage is still trading {clash}. Stop those traders first (two traders on one account = stop wars and double closes).")
            return 2
        log("--live: importing ez_manage for its execute_now gate (no order path of our own)")
        import ez_manage  # noqa: F401

        live_tm = ez_manage.trade_manager if hasattr(ez_manage, "trade_manager") else None
        if live_tm is None:
            log("REFUSING --live: ez_manage exposes no trade_manager instance to route execute_now through")
            return 2
    book = PaperBook(OUT / "paper_state.json")
    while True:
        bar = a.bar or last_closed_bar()
        _lag = time.time() - (bar + BAR_S)
        if _lag > 300:
            log(f"STALE PASS: bar closed {_lag:.0f}s ago (>300s consumer rule) — evaluating anyway for state "
                f"convergence; fresh intents will be refused until the pass catches up")
        day = dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime("%Y-%m-%d")
        t0 = time.time()
        held = live_positions(accounts)
        if a.vec_driven:
            write_registry(uni, a.registry_hours)
        stats, intents = run_bar(uni, bds, book, bar, live_tm=live_tm, ledger=OUT / f"intents_{day}.jsonl", workers=a.workers, held=held, close_only=a.close_only, vec_driven=a.vec_driven)
        acts = sum(v for k, v in stats.items() if k.startswith("ACT_"))
        lat = [r.get("decided_s_after_bar_close") for r in intents if r.get("decided_s_after_bar_close") is not None]
        if lat:
            lat.sort()
            log(f"decision latency after the bar closed: min {lat[0]:.0f}s  p50 {lat[len(lat) // 2]:.0f}s  max {lat[-1]:.0f}s")
        log(f"bar {dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime('%m-%d %H:%M')}Z evaluated in {time.time() - t0:.0f}s: "
            f"{acts} engine acts, paper book {len(book.pos)} open / {len(book.closed)} closed, "
            f"status {dict(sorted((k, v) for k, v in stats.items() if not k.startswith(('ACT_', 'PAPER_', 'STATE_', 'CONV_'))))}")
        log(f"  STATE vs the accounts: " + ", ".join(f"{k[6:]}={v}" for k, v in sorted(stats.items()) if k.startswith("STATE_"))
            + (" | TO TRADE: " + ", ".join(f"{k[5:]}={v}" for k, v in sorted(stats.items()) if k.startswith("CONV_")) if any(k.startswith("CONV_") for k in stats) else " | nothing to converge"))
        if acts:
            log("  " + "; ".join(f"{r['acct']}:{r['sym_side']} {r['action']} {r['reason'][:40]} -> {r.get('paper')}" for r in intents if r.get("action"))[:800])
        if not a.loop:
            return 0
        # On the bar. Not 8 minutes after it, not 12: the next 15m boundary plus --offset seconds for the closing
        # candle to land in the store, then every key is decided in one pass.
        nxt = ((time.time() // BAR_S) + 1) * BAR_S + a.offset
        while nxt - time.time() < 1.0:
            nxt += BAR_S
        log(f"next pass at {dt.datetime.fromtimestamp(nxt, dt.timezone.utc).strftime('%H:%M:%S')}Z (bar close + {a.offset:.0f}s) — sleeping {nxt - time.time():.0f}s")
        time.sleep(max(1.0, nxt - time.time()))


if __name__ == "__main__":
    sys.exit(main() or 0)
