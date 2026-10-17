"""OVERSIZE_TRIM — trim a platform-opened position back to the active position cap, ONLY while it is at a loss
(USER 2026-10-17: "oversized platform positions may be trimmed ... not at a gain but definitely at a loss"; "manually placed
orders should be exempt of this while they are subject to all normal reductions"). This is an explicit user exception to
CLAUDE.md "never close at a loss" for the oversized excess of platform positions only.

Why it exists: the vector never holds more than its position cap (QTY_LIVE_POSITION_CAP_MULT), so a live position above the
active mode's MAX_POSITION_SIZE (mode switched to LIGHT, a cap lowered, a sizing drift) is a divergence from the board. Trimming
the excess converges live to the size the board measured. Only the PLATFORM's share is ever trimmed: the platform quantity of
the current lifecycle comes from the bot's own broker-confirmed order ledger (order_dedupe_guard), entries minus exits since the
position opened. A position with no platform fills is manual and exempt; manual quantity inside a mixed position is never cut.
Unknown attribution (no opened_at, empty ledger) -> no trim (fail-safe).

Pure functions — the live hooks (ez_manage / tradier_manage) supply ledger rows, broker amount, price and cap.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

REASON = "OVERSIZE_TRIM_LOSS"


def _num(v, d=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _get(cfg, k, d):
    v = cfg(k, d) if callable(cfg) else getattr(cfg, k, d)
    return d if v is None else v


def enabled(cfg) -> bool:
    v = _get(cfg, "OVERSIZE_TRIM_ENABLED", True)
    return (str(v).strip().lower() in ("1", "true", "yes")) if isinstance(v, str) else bool(v)


def epoch(v):
    """opened_at (datetime / ISO / epoch) -> epoch seconds or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f / 1000.0 if f > 1e11 else f
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).timestamp()
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
    except ValueError:
        return None


def platform_qty(rows, is_long: bool, position_side: str, since_ts, venue: str = "binance") -> float:
    """Net platform quantity of the current lifecycle from ledger rows: confirmed entry fills minus exit fills since the open.
    binance rows: side BUY/SELL + position_side LONG/SHORT. tradier rows: side buy/sell (long) or sell_short/buy_to_cover
    (short), position_side blank. since_ts None -> 0.0 (attribution unknown -> nothing is the platform's to trim)."""
    if since_ts is None:
        return 0.0
    if str(venue).lower() == "tradier":
        entry, exit_ = (("BUY",), ("SELL",)) if is_long else (("SELL_SHORT",), ("BUY_TO_COVER",))
    else:
        entry, exit_ = (("BUY",), ("SELL",)) if is_long else (("SELL",), ("BUY",))
    ps = str(position_side or "").upper()
    q = 0.0
    for o in rows or []:
        try:
            if str(venue).lower() != "tradier" and str(o.get("position_side", "") or "").upper() not in ("", ps):
                continue
            ts = _num(o.get("submitted_at"), 0.0)
            ts = ts / 1000.0 if ts > 1e11 else ts
            if ts + 120.0 < float(since_ts):  # 2 min slack: the open order is submitted just before the position exists
                continue
            ex = _num(o.get("executed_qty"), 0.0)
            if ex <= 0:
                continue
            sd = str(o.get("side", "")).upper()
            if sd in entry:
                q += ex
            elif sd in exit_:
                q -= ex
        except Exception:
            continue
    return max(0.0, q)


def decide(cfg, is_long: bool, broker_qty: float, plat_qty: float, entry: float, px: float, cap_usd: float,
           min_notional: float = 5.0, whole_shares: bool = False):
    """(trim_qty, why). trim_qty > 0 only when: enabled, at a LOSS, notional > cap x (1 + tolerance), platform quantity > 0.
    trim_qty = min(excess quantity down to the cap, platform quantity); whole shares for stocks; never below min notional."""
    if not enabled(cfg):
        return 0.0, "off"
    bq, pq, e, p, cap = abs(_num(broker_qty)), abs(_num(plat_qty)), _num(entry), _num(px), _num(cap_usd)
    if bq <= 0 or e <= 0 or p <= 0 or cap <= 0:
        return 0.0, "no data"
    gain = (p - e) / e * 100.0 if is_long else (e - p) / e * 100.0
    if gain >= 0:
        return 0.0, f"at a gain ({gain:+.2f}%) - never trimmed"
    tol = abs(_num(_get(cfg, "OVERSIZE_TRIM_TOLERANCE_PCT", 10.0), 10.0)) / 100.0
    notional = bq * p
    if notional <= cap * (1.0 + tol):
        return 0.0, "within cap"
    if pq <= 0:
        return 0.0, "no platform quantity (manual position) - exempt"
    excess_q = (notional - cap) / p
    q = min(excess_q, pq, bq)
    if whole_shares:
        q = float(int(q))
    if q <= 0 or q * p < _num(min_notional, 5.0):
        return 0.0, f"excess {q * p:.2f} below min notional"
    return q, f"notional {notional:.2f} > cap {cap:.2f} x{1 + tol:.2f} at {gain:+.2f}% -> trim {q:.6f} of platform {pq:.6f} (broker {bq:.6f})"
