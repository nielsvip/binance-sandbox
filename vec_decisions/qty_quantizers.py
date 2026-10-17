"""qty_quantizers.py — ONE implementation of order-quantity multipliers for vector AND live (USER 2026-10-16).

Called by v12_quick_engine (vector), ez_manage.execute_trade_action (crypto live) and
tradier_manage.calculate_position_size (stocks live) so all three react identically.

1. STDEV LADDER (master STDEV_SLOPE_SIZING_ENABLED, USER "should be 1-5x"):
   edge = clip(1 - lrL_pct_b_TF, 0, 1) for LONG, clip(lrL_pct_b_TF, 0, 1) for SHORT
   mult = 1 + (STDEV_LADDER_MAX_MULT - 1) * edge  -> 1x at the top (bottom for shorts), 5x at the bottom (top for shorts),
   linear in between. TF = STDEV_LADDER_TF (default D). Missing / non-finite channel -> 1.0 (no sizing, never a guess).
   Bit-identical to the vector's former inline block (D, 5x, clipped edge) for every finite pb.

2. STREAK QUANTIZER (master QTY_STREAK_SIZING_ENABLED, default False):
   a "trade" = one position lifecycle, scored at its FULL close by the side-signed move of the exit price
   vs the AVERAGE entry price (live: exchange avgEntry; vector: pos["avg_price"]).
   Only |move| >= QTY_STREAK_MIN_MOVE_PCT qualifies (1.0 / 0.5); smaller results are ignored entirely
   (they neither count nor break a streak).
   - QTY_STREAK_LOSS_COUNT (1|2) consecutive qualifying losses -> next opens sized at QTY_STREAK_LOSS_MULT (0.8/0.6/0.4).
   - QTY_STREAK_WIN_COUNT (1|2) consecutive qualifying wins -> next opens x(1+QTY_STREAK_WIN_STEP) (0.2/0.5/0.8), and every
     further qualifying win compounds another step: wins=W>=COUNT -> (1+STEP)**(W-COUNT+1), capped at QTY_STREAK_MAX_MULT.
   - a qualifying win resets the loss count, a qualifying loss resets the win count.
   Applies to OPEN / REENTRY sizing only (a new lifecycle), never to augments, reduces, closes or hedges.

Account caps (MAX_ORDER_VALUE / MAX_POSITION_SIZE / HARD_MAX_SYMBOL_VALUE_TRADIER) still bind AFTER these multipliers live;
the vector mirrors that via cap_dollar() with QTY_LIVE_ORDER_CAP_MULT (order cap in START_POSITION_SIZE units, 0 = legacy).
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import numpy as np


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def ladder_on(cfg: Any) -> bool:
    return bool(getattr(cfg, "STDEV_SLOPE_SIZING_ENABLED", False))


def ladder_tf(cfg: Any) -> str:
    return str(getattr(cfg, "STDEV_LADDER_TF", "D") or "D")


def ladder_max(cfg: Any) -> float:
    return max(1.0, _num(getattr(cfg, "STDEV_LADDER_MAX_MULT", 5.0), 5.0))


def ladder_scalar(pct_b: Any, is_long: bool, cfg: Any) -> float:
    """Live ladder multiplier from one lrL_pct_b reading. 1.0 when off or the channel is missing."""
    if not ladder_on(cfg) or pct_b is None:
        return 1.0
    pb = _num(pct_b, float("nan"))
    if not math.isfinite(pb):
        return 1.0
    edge = min(1.0, max(0.0, (1.0 - pb) if is_long else pb))
    return 1.0 + (ladder_max(cfg) - 1.0) * edge


def ladder_from_indicators(ind: Optional[Mapping[str, Any]], is_long: bool, cfg: Any) -> float:
    if not ladder_on(cfg) or not ind:
        return 1.0
    return ladder_scalar(ind.get(f"lrL_pct_b_{ladder_tf(cfg)}"), is_long, cfg)


def ladder_vec(npz: Mapping[str, Any], n: int, is_long: bool, cfg: Any) -> np.ndarray:
    """Vector twin of ladder_scalar over n bars."""
    ones = np.ones(int(n), dtype=np.float64)
    if not ladder_on(cfg):
        return ones
    a = npz.get(f"lrL_pct_b_{ladder_tf(cfg)}") if hasattr(npz, "get") else None
    if a is None or not isinstance(a, np.ndarray) or len(a) != int(n):
        return ones
    pb = a.astype(np.float64)
    edge = np.clip((1.0 - pb) if is_long else pb, 0.0, 1.0)
    m = 1.0 + (ladder_max(cfg) - 1.0) * edge
    return np.where(np.isfinite(m), m, 1.0)


def streak_on(cfg: Any) -> bool:
    return bool(getattr(cfg, "QTY_STREAK_SIZING_ENABLED", False))


def new_state() -> dict:
    return {"wins": 0, "losses": 0}


def trade_move_pct(is_long: bool, avg_entry: Any, exit_price: Any) -> Optional[float]:
    e = _num(avg_entry, 0.0)
    x = _num(exit_price, 0.0)
    if e <= 0 or x <= 0:
        return None
    return (x - e) / e * 100.0 if is_long else (e - x) / e * 100.0


def streak_update(state: dict, move_pct: Any, cfg: Any) -> dict:
    """Fold one full-close result into the streak state (pure; returns the same dict)."""
    g = _num(move_pct, float("nan"))
    if not math.isfinite(g):
        return state
    thr = abs(_num(getattr(cfg, "QTY_STREAK_MIN_MOVE_PCT", 1.0), 1.0))
    if g >= thr and g > 0:
        state["wins"] = int(state.get("wins", 0)) + 1
        state["losses"] = 0
    elif g <= -thr and g < 0:
        state["losses"] = int(state.get("losses", 0)) + 1
        state["wins"] = 0
    return state


def streak_mult(state: Optional[Mapping[str, Any]], cfg: Any) -> float:
    if not streak_on(cfg) or not state:
        return 1.0
    wins = int(state.get("wins", 0) or 0)
    losses = int(state.get("losses", 0) or 0)
    if losses >= max(1, int(_num(getattr(cfg, "QTY_STREAK_LOSS_COUNT", 1), 1))):
        return max(0.0, _num(getattr(cfg, "QTY_STREAK_LOSS_MULT", 0.8), 0.8))
    need = max(1, int(_num(getattr(cfg, "QTY_STREAK_WIN_COUNT", 1), 1)))
    if wins >= need:
        step = max(0.0, _num(getattr(cfg, "QTY_STREAK_WIN_STEP", 0.2), 0.2))
        cap = max(1.0, _num(getattr(cfg, "QTY_STREAK_MAX_MULT", 3.0), 3.0))
        return min(cap, (1.0 + step) ** (wins - need + 1))
    return 1.0


def is_new_lifecycle_action(action: Any) -> bool:
    a = str(action or "").upper()
    if any(t in a for t in ("CLOSE", "REDUCE", "HEDGE", "AUGMENT")):
        return False
    return "OPEN" in a or "REENTRY" in a or "REENTER" in a


def cap_dollar(cfg: Any, dollar: float, legacy_cap: float) -> float:
    """Vector order cap. QTY_LIVE_ORDER_CAP_MULT>0 -> the live cap in START units (live MAX_ORDER_VALUE / START); else legacy."""
    m = _num(getattr(cfg, "QTY_LIVE_ORDER_CAP_MULT", 0.0), 0.0)
    if m > 0:
        return min(float(dollar), _num(getattr(cfg, "START_POSITION_SIZE", 0.0), 0.0) * m)
    return min(float(dollar), float(legacy_cap)) if legacy_cap > 0 else float(dollar)


def cap_add_qty(cfg: Any, pos_qty: float, add_qty: float, px: float, tradier: bool = False) -> float:
    """Vector augment cap. QTY_LIVE_POSITION_CAP_MULT>0 -> position notional <= START x this (live MAX_POSITION_SIZE/START,
    applied by VEC_EXACT_HARD_CAP after every multiplier); 0 = no position cap (legacy)."""
    m = _num(getattr(cfg, "QTY_LIVE_POSITION_CAP_MULT", 0.0), 0.0)
    if m <= 0 or px <= 0 or add_qty <= 0:
        return add_qty
    room = _num(getattr(cfg, "START_POSITION_SIZE", 0.0), 0.0) * m / px - float(pos_qty)
    room = max(0.0, room)
    if tradier:
        room = float(int(room))
    return min(float(add_qty), room)


# ── live streak state: one JSON per venue+account, keyed SYMBOL_SIDE, one count per lifecycle ──
def _state_path(base: Any, venue: str, account: Any):
    from pathlib import Path

    return Path(str(base)) / "data" / "qty_streak" / f"{venue}_{str(account or 'default')}.json"


def live_load(base: Any, venue: str, account: Any) -> dict:
    import json

    p = _state_path(base, venue, account)
    try:
        if p.exists() and p.stat().st_size > 2:
            d = json.loads(p.read_text())
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def live_record_close(base: Any, venue: str, account: Any, symbol: Any, side: Any, avg_entry: Any, exit_price: Any, cfg: Any, lifecycle: Any = None, notional_usd: Any = None) -> Optional[dict]:
    """Record one full close. Runs whether or not the switch is on, so turning it on starts from real history.
    A dust remnant close (0 < notional < EXIT_DUST_USD) is not a trade (the vector has no dust) and is skipped."""
    import json
    import os

    nu = _num(notional_usd, 0.0)
    if 0.0 < nu < _num(getattr(cfg, "EXIT_DUST_USD", 5.0), 5.0):
        return None

    is_long = str(side).upper() == "LONG"
    move = trade_move_pct(is_long, avg_entry, exit_price)
    if move is None:
        return None
    key = f"{str(symbol).upper()}_{str(side).upper()}"
    p = _state_path(base, venue, account)
    d = live_load(base, venue, account)
    st = d.get(key) or new_state()
    if lifecycle and str(st.get("last_lifecycle", "")) == str(lifecycle):
        return st
    streak_update(st, move, cfg)
    if lifecycle:
        st["last_lifecycle"] = str(lifecycle)
    st["last_move_pct"] = round(move, 4)
    d[key] = st
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
        os.replace(tmp, p)
    except Exception:
        pass
    return st


def live_mult(base: Any, venue: str, account: Any, symbol: Any, side: Any, cfg: Any) -> float:
    if not streak_on(cfg):
        return 1.0
    st = live_load(base, venue, account).get(f"{str(symbol).upper()}_{str(side).upper()}")
    return streak_mult(st, cfg)


_MQ_CACHE = {"mtime": None, "data": {}}


def _min_qty_table() -> dict:
    """Live's exchange limits (config.MIN_QTY_FILE = <repo>/min_qty.json: per-symbol minQty + minNotional)."""
    import json
    import os

    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "min_qty.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    if _MQ_CACHE["mtime"] != mt:
        try:
            _MQ_CACHE["data"] = json.load(open(p))
            _MQ_CACHE["mtime"] = mt
        except Exception:
            return _MQ_CACHE["data"]
    return _MQ_CACHE["data"]


def min_viable_qty(symbol: str, price: float, cfg: Any) -> float:
    """2026-10-17 parity: live ez_manage floors every vector OPEN/AUGMENT to the smallest placeable size
    (VEC_EXACT_SIZE_FLOOR_FINAL: max(6 x MIN_POSITION_SIZE / px, _min_viable_qty) where _min_viable_qty =
    max(minQty x 1.2, 6/px, minNotional/px), defaults minQty 0.0001, minNotional 5.0). Boards traded $0.0007 positions live
    can never place (SNXUSDT_LONG 88% of opens < $6). Same formula, same file."""
    px = _num(price, 0.0)
    if px <= 0:
        return 0.0
    d = _min_qty_table().get(str(symbol), {}) or {}
    mq = _num(d.get("minQty", 0.0001), 0.0001) * 1.2
    mn = _num(d.get("minNotional", 5.0), 5.0) / px
    mps = _num(getattr(cfg, "MIN_POSITION_SIZE", 1.0), 1.0) or 1.0
    return max(mq, 6.0 / px, mn, 6.0 * mps / px)
