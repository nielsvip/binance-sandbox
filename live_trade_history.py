#!/usr/bin/env python3
"""live_trade_history — shared reader for per-account trade ledgers.

Both venues append {ts, type, qty, price, value, reason} JSON lines to
  {DATA_DIR}/history/{acct}/{SYM}_{SIDE}.jsonl
with a byte-identical mirror under {BASE}/data/history/{acct}/. This module
reads one root (tradier first, data/history as fallback) and dedupes, so
callers never double-count the mirror. No venue imports: ez_rankings and
tradier_rankings both use it without import cycles.
"""
from pathlib import Path

ACCOUNT_ARROW_ORDER = ["trb", "ang", "inf", "flz", "men", "fin"]
ACCOUNT_ARROW_COLORS = {"ang": "#2ecc71", "inf": "#42a5f5", "flz": "#ff5252", "men": "#ffa726", "fin": "#9e9e9e"}
TRB_BUY_COLOR = "#2ecc71"
TRB_SELL_COLOR = "#ff5252"
VEC_ONLY_COLOR = "rgba(248,113,113,0.55)"
LIVE_ONLY_COLOR = "rgba(74,222,128,0.55)"

_BUY_TOKENS = ("OPEN", "AUGMENT", "ADD", "REOPEN", "BUY")
_SELL_TOKENS = ("REDUCE", "CLOSE", "SELL", "EXIT", "COVER", "TAKE")


def event_direction(event_type, side):
    t = str(event_type or "").upper()
    if any(k in t for k in _SELL_TOKENS):
        base = "sell"
    elif any(k in t for k in _BUY_TOKENS):
        base = "buy"
    else:
        base = "buy" if side == "LONG" else "sell"
    if side == "SHORT":
        base = "sell" if base == "buy" else "buy"
    return base


def account_color(acct, direction):
    if acct == "trb":
        return TRB_BUY_COLOR if direction == "buy" else TRB_SELL_COLOR
    return ACCOUNT_ARROW_COLORS[acct]


def fetch_trades(symbol, accounts, hist_dirs):
    import json
    import pandas as pd
    out = {}
    sym = str(symbol or "").upper()
    if not sym:
        return out
    for acct in accounts:
        events = []
        for hdir in hist_dirs:
            adir = Path(hdir) / acct
            if not adir.is_dir():
                continue
            for side in ("LONG", "SHORT"):
                fpath = adir / f"{sym}_{side}.jsonl"
                if not fpath.is_file():
                    continue
                try:
                    lines = fpath.read_text().splitlines()[-400:]
                except Exception:
                    continue
                for line in lines:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    try:
                        ts = pd.to_datetime(rec.get("ts"), utc=True)
                    except Exception:
                        continue
                    if pd.isna(ts):
                        continue
                    typ = str(rec.get("type") or "")
                    events.append({"timestamp": ts, "direction": event_direction(typ, side),
                                   "type": typ, "reason": str(rec.get("reason") or "")[:80]})
        if events:
            seen = set()
            uniq = []
            for ev in events:
                key = (ev["timestamp"].isoformat(), ev["type"], ev["direction"])
                if key not in seen:
                    seen.add(key)
                    uniq.append(ev)
            uniq.sort(key=lambda e: e["timestamp"])
            out[acct] = uniq
    return out


def greedy_match(vec_ts, live_ts, tol_s):
    matched_v, matched_l = set(), set()
    for i, vt in enumerate(vec_ts):
        best, best_dt = None, tol_s + 1
        for j, lt in enumerate(live_ts):
            if j in matched_l:
                continue
            try:
                dt = abs((lt - vt).total_seconds())
            except Exception:
                continue
            if dt <= tol_s and dt < best_dt:
                best, best_dt = j, dt
        if best is not None:
            matched_v.add(i)
            matched_l.add(best)
    return matched_v, matched_l
