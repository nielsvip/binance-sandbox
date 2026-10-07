"""grey_wire_entries — ONE predicate set for fresh-entry gates wired 2026-09-30 (grey-switch wiring job), shared by
LIVE stocks (tradier_manage.queue_trade_action OPEN gate, next to EMA_BLANKET_FILTER) and the vector engine
(v12_quick_engine.simulate_one entry-filter stack). Crypto live keeps its own original implementation (source of
truth cited per predicate). Every enable defaults OFF -> the gate returns None (vec) / pass (live) -> zero change.

Scalar contract: fn(g, c, price, is_long) -> (passed: bool, detail: str); g(key, default) = live snapshot getter,
c(key, default) = per-sym config getter. Vec contract: fn(npz, n, is_long, cfg, close, safe) -> allow mask | None.
"""
import numpy as np

import vec_decisions.wave4_families as _w4


def _f(v, d=0.0) -> float:
    try:
        return float(d) if v is None else float(v)
    except (TypeError, ValueError):
        return float(d)


# ── WT_PERCENTILE_ENTRY_GATE (ez_positions_quick.rate R-G6 ~2282-2289): LONG blocked when wt_percentile_D > OB_D,
#    SHORT blocked when wt_percentile_D < OS_D; missing percentile -> no block.
def wt_percentile_entry_live_pass(g, c, price, is_long):
    if not bool(c("WT_PERCENTILE_ENTRY_GATE_ENABLED", False)):
        return True, ""
    raw = g("wt_percentile_D", None)
    if raw is None:
        return True, ""
    p = _f(raw)
    if is_long and p > _f(c("WT_PERCENTILE_ENTRY_OB_D", 90.0), 90.0):
        return False, f"WT_PCT_LONG_OB_D={p:.0f}"
    if (not is_long) and p < _f(c("WT_PERCENTILE_ENTRY_OS_D", 10.0), 10.0):
        return False, f"WT_PCT_SHORT_OS_D={p:.0f}"
    return True, ""


def wt_percentile_entry_gate(npz, n, is_long, cfg, close, safe):
    if not bool(getattr(cfg, "WT_PERCENTILE_ENTRY_GATE_ENABLED", False)) or "wt_percentile_D" not in npz:
        return None
    p = safe(npz, "wt_percentile_D", n, 50.0).astype(float)
    if is_long:
        return ~(p > float(getattr(cfg, "WT_PERCENTILE_ENTRY_OB_D", 90.0)))
    return ~(p < float(getattr(cfg, "WT_PERCENTILE_ENTRY_OS_D", 10.0)))


# ── HTF_DIRECTION_GATE (+HTF_GATE_SIGNALS_SMA200D / HTF_GATE_D_MANDATORY / HTF_GATE_MIN_CONFIRMATIONS): scalar twin of
#    wave4_families.htf_direction_gate (vec, already in simulate_one; live crypto source ez_positions_quick ~12092-12140).
def htf_direction_live_pass(g, c, price, is_long):
    if not bool(c("HTF_DIRECTION_GATE_ENABLED", False)):
        return True, ""
    sigs = []
    for tf in ("D", "4h", "1h"):
        w1, w2 = _f(g(f"wt1_{tf}", 0.0)), _f(g(f"wt2_{tf}", 0.0))
        sigs.append(bool(((w1 > w2) if is_long else (w1 < w2)) and w1 != 0 and w2 != 0))
    met = sum(int(s) for s in sigs)
    if bool(c("HTF_GATE_SIGNALS_SMA200D", True)):
        sma = _f(g("sma_200_D", 0.0))
        if sma > 0 and ((price > sma) if is_long else (price < sma)):
            met += 1
    need = int(_f(c("HTF_GATE_MIN_CONFIRMATIONS", 3), 3) or 3)
    ok = met >= need
    if bool(c("HTF_GATE_D_MANDATORY", True)):
        ok = ok and sigs[0]
    return ok, f"HTF_DIRECTION_met{met}/{need}_D{int(sigs[0])}"


def htf_direction_gate(npz, n, is_long, cfg, close, safe):
    """the vec gate (unchanged, lives in wave4_families) — re-exported so both call sites import one module."""
    return _w4.htf_direction_gate(npz, n, is_long, cfg, close, safe)


LIVE_OPEN_GATES = (wt_percentile_entry_live_pass, htf_direction_live_pass)
VEC_ENTRY_GATES = (wt_percentile_entry_gate,)  # htf_direction_gate is already applied by simulate_one via wave4


def live_open_pass(g, c, price, is_long):
    """all grey-wire OPEN gates for a fresh stock entry; first block wins."""
    for fn in LIVE_OPEN_GATES:
        ok, detail = fn(g, c, price, is_long)
        if not ok:
            return False, detail
    return True, ""
