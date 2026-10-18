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
  decision   the live twin's own bars and its own action extraction (`live_twins.vec_exact.actions_at(..., overrides=board)`)
             -> the exact code path the sheets measure. This script implements NO trading logic of its own.
  execution  --paper (default): nothing is sent; the intent and a paper position are recorded.
             --live: every order goes through `ez_manage` `execute_now` (the only sanctioned gate, CLAUDE.md). It REFUSES to
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
    return sorted(out)


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
    """Orders only ever leave through ez_manage.execute_trade_action -> execute_now (the one sanctioned gate)."""
    sym = ss.rsplit("_", 1)[0]
    side = "LONG" if ss.endswith("_LONG") else "SHORT"
    a = str(action).upper()
    opening = a in ("OPEN", "AUGMENT")
    oside = ("BUY" if side == "LONG" else "SELL") if opening else ("SELL" if side == "LONG" else "BUY")
    return await tm.execute_trade_action(account_key=acct, position_key=f"{acct}:{ss}", symbol=sym, quantity=abs(float(qty)),
                                         current_price=float(price), side=oside, position_side=side,
                                         reason=f"{reason} |PURE_BOARD", is_reduce=(not opening), is_full_close=(a == "CLOSE"))


FROZEN = Path(os.environ.get("VEC_EXACT_FROZEN_DIR", "/tmp/vec_exact_frozen"))


def prepared_from_frozen(symside: str):
    """The twin's own append-only frozen bars = exactly what live decided on, without paying its ~20 s live rebuild."""
    import pickle

    from live_twins import vec_exact as VX

    path = FROZEN / f"{symside.rsplit('_', 1)[0]}.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as fh:
        raw = pickle.load(fh)
    return VX._prepare_from_arrays(symside, dict(raw))


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


def run_bar(uni, bds, book, bar_ts, live_tm=None, ledger=None, workers=4):
    from concurrent.futures import ThreadPoolExecutor
    from live_twins import vec_exact as VX

    stats = collections.Counter()
    intents = []
    by_ss = collections.defaultdict(list)
    for acct, ss in uni:
        by_ss[ss].append(acct)

    def one(ss):
        pb = bds.get(ss)
        if not pb or not (pb.get("overrides") or {}):
            return (ss, {"status": "SKIP_NO_PUBLISHED_BOARD", "actions": []}, None)
        ov = pb["overrides"]
        sym, side = ss.rsplit("_", 1)
        try:
            prep = prepared_from_frozen(ss)
            if prep is None:
                return (ss, {"status": "SKIP_NO_TWIN_STORE", "actions": []}, pb)
            res = VX.actions_at(sym, side, bar_ts + BAR_S - 1, overrides=ov, prepared=prep)
        except Exception as exc:
            return (ss, {"status": f"EVAL_ERROR:{type(exc).__name__}", "actions": [], "err": str(exc)[:160]}, pb)
        return (ss, res, pb)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, sorted(by_ss)))
    for ss, res, pb in results:
        status = res.get("status", "?")
        stats[status] += 1
        acts = res.get("actions") or []
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
    if ledger is not None and intents:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a") as fh:
            for r in intents:
                fh.write(json.dumps(r, default=str, separators=(",", ":")) + "\n")
    book.save()
    return stats, intents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default=",".join(ACCOUNTS))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--live", action="store_true", help="send real orders through ez_manage.execute_now (the account's own ez_manage must be stopped)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--bar", type=float, default=0.0, help="evaluate this bar ts instead of the last closed one")
    a = ap.parse_args()
    accounts = [x.strip() for x in a.accounts.split(",") if x.strip()]
    uni = universe(accounts)
    bds = boards()
    missing = sorted({ss for _ac, ss in uni if ss not in bds})
    log(f"universe {len(uni)} keys / {len({s for _a, s in uni})} sym_sides over accounts {accounts}; published boards {len(bds)}; without a board: {len(missing)}")
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
        day = dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime("%Y-%m-%d")
        t0 = time.time()
        stats, intents = run_bar(uni, bds, book, bar, live_tm=live_tm, ledger=OUT / f"intents_{day}.jsonl", workers=a.workers)
        acts = sum(v for k, v in stats.items() if k.startswith("ACT_"))
        log(f"bar {dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime('%m-%d %H:%M')}Z evaluated in {time.time() - t0:.0f}s: "
            f"{acts} actions, paper book {len(book.pos)} open / {len(book.closed)} closed, "
            f"status {dict(sorted((k, v) for k, v in stats.items() if not k.startswith(('ACT_', 'PAPER_'))))}")
        if acts:
            log("  " + "; ".join(f"{r['acct']}:{r['sym_side']} {r['action']} {r['reason'][:40]} -> {r.get('paper')}" for r in intents if r.get("action"))[:800])
        if not a.loop:
            return 0
        nxt = last_closed_bar(time.time()) + 2 * BAR_S + 20.0
        time.sleep(max(20.0, nxt - time.time()))


if __name__ == "__main__":
    sys.exit(main() or 0)
