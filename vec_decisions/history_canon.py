"""history_canon — one real trade = one row when reading data/history/<acct>/<SYM_SIDE>.jsonl.

USER 2026-10-27 (verbatim): "Every REAL trade has to be added to the /history/ folder on macbook with the quantity $ value and
reason for the trade."

Writers of a history file since 2026-10-27:
  * order rows    — carry "order_id"; tools/vector_direct_history_import.py appends one per S1/S5 vector_direct fill
                    (prov.src S1_VECTOR_DIRECT / S5_VECTOR_DIRECT, broker fill price + qty, reason = the trader's `why`).
  * reconciled    — tools/forward_parity/live_guardian.py "BROKER_RECONCILED_<GAP|STOP> order=<id>" (one per broker trade).
  * native rows   — the Mac positions service (ez_positions_service.py, LOCKED) logs every broker position CHANGE. When S1/S5
                    traded, that row is an ECHO of the order (measured 2026-10-27: 76 of 94 S1 orders echoed as AUGMENT/REDUCE,
                    13-857 s late, stale reason/indicators, mark not fill price), a NETTED echo (two orders seen as one change),
                    or after a Mac engine restart a RE-LOG of an echo already written (27 rows; broker has no trade then).
Rows are never deleted or rewritten (history is audit). Readers call canonical() instead, which drops exactly those shadows.
A file without order rows is returned unchanged (only order rows can claim shadows), so legacy behaviour is untouched.
"""

from __future__ import annotations

import datetime as _dt
import re

EXTERNAL_SRCS = ("S1_VECTOR_DIRECT", "S5_VECTOR_DIRECT")
OPEN_TYPES = frozenset(("OPEN", "AUGMENT", "REENTRY", "QUICK_OPEN", "QUICK_AUGMENT"))
CLOSE_TYPES = frozenset(("REDUCE", "CLOSE", "GHOST_CLOSE"))
ECHO_BEFORE_S = 120.0
ECHO_AFTER_S = 1800.0
RELOG_WINDOW_S = 6 * 3600.0
QTY_TOL = 0.005
_RECON = re.compile(r"^BROKER_RECONCILED_[A-Z]+ order=(\S+)")


def ts_of(h) -> float:
    v = h.get("ts") if isinstance(h, dict) else None
    if v is None:
        v = h.get("timestamp") if isinstance(h, dict) else None
    try:
        n = float(v)
        return n / 1000.0 if n > 1e11 else n
    except (TypeError, ValueError):
        pass
    try:
        return _dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def order_id(h) -> str | None:
    v = h.get("order_id")
    return str(v) if v not in (None, "") else None


def reconciled_order(h) -> str | None:
    m = _RECON.match(str(h.get("reason") or ""))
    return m.group(1) if m else None


def is_external(h) -> bool:
    prov = h.get("prov") if isinstance(h.get("prov"), dict) else {}
    return str(prov.get("src") or "") in EXTERNAL_SRCS


def _cls(h):
    t = str(h.get("type") or "").upper()
    return 1 if t in OPEN_TYPES else (-1 if t in CLOSE_TYPES else 0)


def _qty(h) -> float:
    try:
        return abs(float(h.get("qty") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _same_qty(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, max(abs(a), abs(b)) * QTY_TOL)


def classify(rows: list) -> dict:
    """{index: (cause, order_id)} for every shadow row. Pure. Causes: DUP_ORDER_ROW, RECONCILED_DUP, FILL_ECHO, NET_ECHO,
    RESTART_RELOG."""
    shadows: dict = {}
    seen_ids: dict = {}
    ext = []
    for i, h in enumerate(rows):
        if not isinstance(h, dict):
            continue
        oid = order_id(h)
        if oid:
            if oid in seen_ids:
                shadows[i] = ("DUP_ORDER_ROW", oid)
                continue
            seen_ids[oid] = i
            if is_external(h):
                ext.append(i)
    if not seen_ids:
        return shadows
    native = []
    for i, h in enumerate(rows):
        if not isinstance(h, dict) or i in shadows or order_id(h):
            continue
        rid = reconciled_order(h)
        if rid is not None:
            if rid in seen_ids:
                shadows[i] = ("RECONCILED_DUP", rid)
            continue
        if _cls(h) != 0:
            native.append(i)
    if not ext:
        return shadows
    t = {i: ts_of(rows[i]) for i in ext + native}
    ext.sort(key=lambda i: t[i])
    claimed = set()
    echoed = set()
    for o in ext:
        oc, oq = _cls(rows[o]), _qty(rows[o])
        best = None
        for n in native:
            if n in claimed or _cls(rows[n]) != oc or not _same_qty(_qty(rows[n]), oq):
                continue
            d = t[n] - t[o]
            if -ECHO_BEFORE_S <= d <= ECHO_AFTER_S and (
                best is None or abs(d) < best[1]
            ):
                best = (n, abs(d))
        if best:
            claimed.add(best[0])
            echoed.add(o)
            shadows[best[0]] = ("FILL_ECHO", order_id(rows[o]))
    free = [o for o in ext if o not in echoed]
    for n in native:
        if n in claimed:
            continue
        signed_n = _cls(rows[n]) * _qty(rows[n])
        win = [o for o in free if t[n] - ECHO_AFTER_S <= t[o] <= t[n] + ECHO_BEFORE_S]
        hit = None
        for a in range(len(win)):
            s = 0.0
            for b in range(a, min(len(win), a + 4)):
                s += _cls(rows[win[b]]) * _qty(rows[win[b]])
                if b > a and _same_qty(s, signed_n) and (s > 0) == (signed_n > 0):
                    hit = win[a : b + 1]
                    break
            if hit:
                break
        if hit:
            claimed.add(n)
            for o in hit:
                free.remove(o)
            shadows[n] = ("NET_ECHO", ",".join(order_id(rows[o]) for o in hit))
    for n in native:
        if n in claimed:
            continue
        h = rows[n]
        for e in claimed:
            g = rows[e]
            if shadows.get(e, ("",))[0] not in ("FILL_ECHO", "NET_ECHO"):
                continue
            if (
                0 < t[n] - t[e] <= RELOG_WINDOW_S
                and str(g.get("type")) == str(h.get("type"))
                and _qty(g) == _qty(h)
                and g.get("reason") == h.get("reason")
                and g.get("indicators") == h.get("indicators")
            ):
                shadows[n] = ("RESTART_RELOG", shadows[e][1])
                break
    return shadows


def canonical(rows: list, sort: bool = True) -> list:
    """The rows that are real trades (order rows + everything no order row explains), oldest first when sort=True."""
    sh = classify(rows)
    keep = [h for i, h in enumerate(rows) if i not in sh and isinstance(h, dict)]
    if sort and (sh or any(order_id(h) for h in keep)):
        keep.sort(key=ts_of)
    return keep
