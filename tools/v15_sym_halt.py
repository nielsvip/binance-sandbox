#!/usr/bin/env python3
"""v15_sym_halt — USER 2026-10-15: per-pair halt after N trailing net losers.

Ladder on top of the 2-loss 365D escalation (tools/v15_loss_streak_watch.py,
read-only): 3 trailing net losers (pnl_pct - fees_pct < 0) on CLOSED trades
newer than per_sym_active.updated_at halts new OPEN/AUGMENT for that
(account, sym_side) pair only. Exits/reduces/everything else trade on.

Auto-release (any): new settings land (streak re-anchors at the new
updated_at); confirmed_365d passes for the set after halted_at; 24 h expiry
with baseline reset (no instant re-halt, no stuck halt).

Writer: python3 tools/v15_sym_halt.py [--dry-run] [--accounts ang,fin]
Manager predicate: sym_halt_active(account_key, sym_side) -> (hit, why),
fail-open on ANY error, V15_SYM_HALT_ENABLED=0 kill-switch.
"""

import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("PER_SYM_STORE_DB") or os.path.join(ROOT, "data", "hourly_reconfig", "per_sym_store.db")
LEDGER_DIR = os.path.join(ROOT, "data", "per_trade_returns")
CONFIRMED = os.path.join(ROOT, "data", "confirmed_365d.json")
OUT = os.path.join(ROOT, "data", "v15_sym_halt.json")
CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
HALT_N = int(os.environ.get("V15_SYM_HALT_N", "3"))
EXPIRY_S = int(os.environ.get("V15_SYM_HALT_EXPIRY_S", str(24 * 3600)))
LEDGER_DAYS = int(os.environ.get("V15_SYM_HALT_LEDGER_DAYS", "14"))

_cache = {}


def _iso(ts):
    try:
        if isinstance(ts, (int, float)):
            f = float(ts)
            return f / 1000.0 if f > 1e11 else f
        t = str(ts or "").strip()
        if not t:
            return None
        d = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=datetime.timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def NET(trade):
    """Net return of one live closed trade, fees included. Pure."""
    try:
        return float(trade.get("pnl_pct") or 0.0) - float(trade.get("fees_pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trailing_losers(trades, since_ts=None):
    """Count of trailing closed trades with net < 0, newer than since_ts. Pure."""
    n = 0
    for t in sorted(trades, key=lambda r: (_iso(r.get("ts")) or 0)):
        ts = _iso(t.get("ts"))
        if since_ts is not None and (ts is None or ts <= since_ts):
            continue
        n = n + 1 if NET(t) < 0 else 0
    return n


def _applied_map():
    out = {}
    try:
        c = sqlite3.connect(DB)
        for sym, upd in c.execute("select sym_side, updated_at from per_sym_active"):
            ts = _iso(upd)
            if sym and ts:
                out[str(sym)] = ts
        c.close()
    except Exception:
        pass
    return out


def _ledgers(accounts, days):
    out = {}
    for acct in accounts:
        files = sorted(glob.glob(os.path.join(LEDGER_DIR, f"{acct}_*.jsonl")))[-days:]
        per = {}
        for f in files:
            try:
                for line in open(f):
                    try:
                        t = json.loads(line)
                    except Exception:
                        continue
                    sym = f"{t.get('symbol')}_{t.get('side')}"
                    per.setdefault(sym, []).append(t)
            except Exception:
                continue
        out[acct] = per
    return out


def _confirmed_map():
    try:
        d = json.load(open(CONFIRMED))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def compute_halts(ledgers, applied, prev_pairs, confirmed, n=HALT_N, now=None, expiry_s=EXPIRY_S):
    """Pure core: (ledgers, applied_at map, previous halt pairs, 365D map) -> next state.

    Returns {"pairs": {acct: {sym: info}}, "reanchored": [[acct, sym]]}.
    """
    now = now if now is not None else datetime.datetime.now(datetime.timezone.utc).timestamp()
    pairs = {a: dict(s) for a, s in (prev_pairs or {}).items() if a != "_baselines"}
    bases = dict((prev_pairs or {}).get("_baselines", {}))
    reanchored = []
    for acct, per in (ledgers or {}).items():
        for sym, trades in per.items():
            base = (applied or {}).get(sym)
            bb = bases.get(acct, {}).get(sym)
            if bb and (not base or bb > base):
                base = bb
            prev = (prev_pairs or {}).get(acct, {}).get(sym) or {}
            if prev.get("halted_at") and not base:
                base = prev.get("baseline_ts")
            streak = trailing_losers(trades, since_ts=base)
            if streak >= n:
                halted_at = prev.get("halted_at", now)
                rec = {"streak": streak, "halted_at": halted_at, "baseline_ts": base,
                       "reason": f"trailing {streak} net losers since apply"}
                conf = (confirmed or {}).get(sym) or {}
                cts = _iso(conf.get("confirmed_at"))
                if cts and cts > halted_at and float(conf.get("gain_365d") or 0) > 0:
                    pairs.get(acct, {}).pop(sym, None)
                    continue
                if now - halted_at > expiry_s:
                    reanchored.append([acct, sym])
                    bases.setdefault(acct, {})[sym] = now
                    pairs.get(acct, {}).pop(sym, None)
                    continue
                pairs.setdefault(acct, {})[sym] = rec
            else:
                pairs.get(acct, {}).pop(sym, None)
    pairs = {a: s for a, s in pairs.items() if s}
    if bases:
        pairs["_baselines"] = {a: s for a, s in bases.items() if s}
    return {"pairs": pairs, "reanchored": reanchored}


def _read_state(flag_path=OUT):
    try:
        with open(flag_path) as fh:
            d = json.load(fh)
        return d.get("pairs", {}) if isinstance(d, dict) else {}
    except Exception:
        return {}


def sym_halt_active(account_key, sym_side, flag_path=OUT):
    """Manager predicate: (hit, why). Fail-open on ANY error. Pure-ish (60 s cache)."""
    try:
        if str(os.environ.get("V15_SYM_HALT_ENABLED", "1")).lower() in ("0", "false", "no"):
            return False, "disabled"
        key = (str(flag_path), int(datetime.datetime.now(datetime.timezone.utc).timestamp() // 60))
        if key not in _cache:
            _cache.clear()
            _cache[key] = _read_state(flag_path)
        rec = (_cache[key].get(str(account_key)) or {}).get(str(sym_side))
        if rec and not rec.get("reanchor_only"):
            return True, f"sym-halted streak {rec.get('streak')} since apply"
        return False, "not halted"
    except Exception as e:
        return False, f"fail-open ({e})"


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--accounts", default=",".join(CRYPTO_ACCOUNTS))
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--out", default=OUT)
    o = a.parse_args(argv)
    accounts = [x for x in o.accounts.split(",") if x]
    applied = _applied_map()
    ledgers = _ledgers(accounts, LEDGER_DAYS)
    prev = _read_state(o.out)
    res = compute_halts(ledgers, applied, prev, _confirmed_map())
    if o.dry_run:
        flat = [(ac, s, r["streak"]) for ac, m in res["pairs"].items() for s, r in m.items()]
        print(json.dumps({"would_halt": sorted(flat), "reanchored": res["reanchored"]}, indent=1))
        return 0
    with open(o.out, "w") as fh:
        json.dump({"at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "pairs": res["pairs"]}, fh, indent=1)
    print(json.dumps({"halted": sum(len(m) for m in res["pairs"].values()),
                      "reanchored": res["reanchored"], "out": o.out}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
