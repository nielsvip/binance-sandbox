#!/usr/bin/env python3
"""Keep data/exchange_fills/** fresh so broker truth can self-certify.

Why this exists (measured 2026-10-27): the fill cache had NO refresher. The
only caller of `exchange_fills.refresh_symbol` was the chart-plotting path in
`ez_rankings.py` (`fetch_exchange_events`), so a symbol's fills were pulled
only when somebody happened to draw its chart. 225 of 334 caches were stale,
median age 16.1h and max 64.7h, which forced `broker_truth` to label every
figure `[UNVERIFIED]` -- the exchange is the arbiter for all our money numbers
and it was being read from a cache nobody was filling.

Rate discipline. `/fapi/v1/userTrades` is weight 5 per symbol against a 2400/min
IP budget, and this account set has been IP-banned before (70x HTTP 418, see
the klines ban-state convention this file reuses). So:
  * symbols are refreshed in PRIORITY order, newest need first, and a run is
    capped by --max-requests; it is cheap and frequent rather than exhaustive;
  * a cache younger than --max-age-h is skipped outright (the pull is
    incremental anyway: `refresh_symbol` asks only for time after cached_to_ms);
  * HTTP 418/429 writes data/exchange_fills_ban_state.json and the run aborts;
    later runs refuse to start until that ban expires.

Priority: (1) sym_sides held right now, (2) symbols the live executor actually
filled recently, (3) everything else oldest-cache-first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CRYPTO_ACCOUNTS = ("ang", "fin", "men", "inf", "flz")
BAN_STATE = ROOT / "data" / "exchange_fills_ban_state.json"
DEFAULT_BAN_S = 15 * 60


def _load_ban() -> float:
    try:
        return float(json.loads(BAN_STATE.read_text()).get("ban_until_ms") or 0)
    except Exception:
        return 0.0


def _save_ban(until_ms: float, why: str) -> None:
    try:
        BAN_STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = BAN_STATE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"ban_until_ms": until_ms, "why": why, "by": "tools/exchange_fills_refresh.py"}))
        os.replace(tmp, BAN_STATE)
    except Exception:
        pass


def _held_symbols() -> set:
    out = set()
    for name in ("held_sides.json", "held_sides_stocks.json"):
        try:
            d = json.loads((ROOT / "data" / "vec_live" / name).read_text())
            for ss in d.get("held") or []:
                out.add(str(ss).rsplit("_", 1)[0].upper())
        except Exception:
            pass
    return out


def _recently_filled(hours: float = 36.0) -> set:
    """Symbols the live executor actually got a fill on -- those have new rows."""
    cutoff = time.time() - hours * 3600.0
    out = set()
    for acct in CRYPTO_ACCOUNTS:
        p = ROOT / "data" / "vec_live" / f"live_exec_{acct}.jsonl"
        if not p.exists():
            continue
        try:
            with open(p, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                back = min(fh.tell(), 4 * 1024 * 1024)
                fh.seek(-back, os.SEEK_END)
                blob = fh.read().decode("utf-8", "replace")
        except Exception:
            continue
        for line in blob.splitlines():
            if '"SUCCESS' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if float(r.get("ts") or 0) < cutoff:
                continue
            ss = str(r.get("ss") or "")
            if "_" in ss:
                out.add(ss.rsplit("_", 1)[0].upper())
    return out


def _cached(acct: str) -> dict:
    """{SYMBOL: cached_to_ms} for an account's existing caches."""
    out = {}
    d = ROOT / "data" / "exchange_fills" / acct
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        try:
            out[p.stem.upper()] = int(json.loads(p.read_text()).get("cached_to_ms") or 0)
        except Exception:
            out[p.stem.upper()] = 0
    return out


def plan(max_age_h: float, max_requests: int, recent_hours: float = 36.0,
         backlog_share: float = 0.25) -> list:
    """(account, symbol) pairs to refresh, most-needed first.

    `backlog_share` RESERVES part of the budget for the oldest caches. Without
    it the held + recently-filled buckets (~100 symbols) fill the whole cap on
    every run and a long-stale symbol is never reached: the first full backfill
    left exactly 20 caches behind at 12-66h, clustered by symbol across
    accounts (ETCUSDT, PUMPUSDT, IOTXUSDT, MANAUSDT, AIAUSDT) because they sat
    past the cap, not because they failed. Those are symbols that stopped
    trading, so nothing else will ever pull them in.
    """
    now_ms = time.time() * 1000.0
    held = _held_symbols()
    recent = _recently_filled(recent_hours)
    rows = []
    for acct in CRYPTO_ACCOUNTS:
        cached = _cached(acct)
        universe = set(cached) | held | recent
        for sym in universe:
            age_h = (now_ms - cached.get(sym, 0)) / 3600000.0
            if sym in cached and age_h < max_age_h:
                continue
            if sym in held:
                prio = 0
            elif sym in recent:
                prio = 1
            else:
                prio = 2
            rows.append((prio, -age_h, acct, sym))
    rows.sort()
    cap = max(0, int(max_requests))
    if not rows or cap <= 0:
        return []
    backlog = [r for r in rows if r[0] == 2]
    if not backlog:
        return [(a, s) for _p, _a, a, s in rows][:cap]
    reserve = min(len(backlog), max(1, int(cap * max(0.0, min(1.0, backlog_share)))))
    picked = backlog[:reserve]
    seen = {(r[2], r[3]) for r in picked}
    for r in rows:
        if len(picked) >= cap:
            break
        if (r[2], r[3]) not in seen:
            picked.append(r)
            seen.add((r[2], r[3]))
    picked.sort()
    return [(a, s) for _p, _a, a, s in picked][:cap]


def run(max_age_h: float = 1.0, max_requests: int = 120, pause_s: float = 0.15,
        recent_hours: float = 36.0, dry_run: bool = False) -> dict:
    ban_until = _load_ban()
    if ban_until and time.time() * 1000.0 < ban_until:
        return {"skipped": "banned", "ban_until_ms": ban_until,
                "resumes_in_s": round((ban_until - time.time() * 1000.0) / 1000.0)}
    import exchange_fills as xf

    todo = plan(max_age_h, max_requests, recent_hours)
    done = failed = 0
    errors = {}
    for acct, sym in todo:
        if dry_run:
            done += 1
            continue
        try:
            xf.refresh_symbol(acct, sym, lookback_days=40)
            done += 1
        except Exception as e:
            msg = str(e)
            failed += 1
            errors[f"{acct}:{sym}"] = msg[:160]
            if "418" in msg or "429" in msg or "-1003" in msg:
                until = time.time() * 1000.0 + DEFAULT_BAN_S * 1000.0
                _save_ban(until, msg[:200])
                return {"aborted": "rate_limited", "refreshed": done, "failed": failed,
                        "ban_until_ms": until, "errors": errors}
        time.sleep(max(0.0, pause_s))
    out = {"planned": len(todo), "refreshed": done, "failed": failed}
    if errors:
        out["errors"] = dict(list(errors.items())[:10])
    try:
        import broker_truth as bt

        out["freshness_after"] = bt.freshness()
    except Exception:
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-age-h", type=float, default=1.0, help="skip caches younger than this")
    ap.add_argument("--max-requests", type=int, default=120, help="hard cap on symbol pulls per run")
    ap.add_argument("--pause-s", type=float, default=0.15)
    ap.add_argument("--recent-hours", type=float, default=36.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--clear-ban", action="store_true")
    a = ap.parse_args()
    if a.clear_ban:
        _save_ban(0.0, "cleared by operator")
        print("ban cleared")
        return 0
    res = run(a.max_age_h, a.max_requests, a.pause_s, a.recent_hours, a.dry_run)
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
