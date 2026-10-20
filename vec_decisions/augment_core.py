"""AUGMENT_CORE — ONE scalar decision core for the vector-only augment sources, shared by tradier_manage (stocks) and
ez_manage (crypto) so every augment the vector can fire is also executable live (USER 2026-10-20: "SHOP_LONG is completely
skipping all augment functions ... revise").

WHAT THE VECTOR FIRES (v12_quick_engine.simulate_one augment block, ~20767-21250) that live had NO producer for:
  LADDER   vec_decisions.gain_ladder_augment.gain_ladder_fire — UAG obligatory tier (gain since last add >= effective min gain,
           MIN_GAIN_TO_BUY_AGGRESSIVELY floor 2.5 / AUGMENT_MIN_GAIN_PCT override) + PULLBACK dip (peak >= min, gain >= 0.5*min,
           peak-gain >= PULLBACK_AUGMENT_REVERSAL_MIN). Vec reason tokens: "UAG_LADDER ..." / "PULLBACK_AUG ...".
  PYRAMID  compute_augment_signals_ex pyramid_sig (PYRAMID_ENABLED): dc_position_15m >= PYRAMID_MIN_DC_POS_15M and
           wt_velocity_1h >= PYRAMID_MIN_WT_VEL_1H (short mirror), then the UAG gate (uagain_gate_pass). Size PYRAMID_SIZE_MULT.
  BOUNCE   compute_augment_signals_ex bounce_sig (BOUNCE_AUGMENT_ENABLED + _K_D_CROSSING_UP): stoch K crosses D below
           BOUNCE_AUGMENT_K_D_THRESHOLD within BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE of dc_low_4h (short mirror), then the UAG gate.
           Needs the previous base-TF K (k_prev); without it the source cannot fire (fail-closed, never a guess).
Order, gates and sizing follow the vector: profit gate (AUGMENT_ONLY_WHEN_PROFITABLE[_TRADIER] -> gain > 0), cooldown
AUGMENTATION_COOLDOWN_SECONDS, add = position qty x 0.5 (vector augment_mult) or PYRAMID_SIZE_MULT. The venue quantizers (whole
shares, qty_quantizers caps) are applied by each manager's normal sizing path after this, exactly as for every other augment.

WHEN LIVE MAY USE IT (default = today's behaviour on both venues, nothing fires until a switch is flipped):
  stocks  live_enabled("tradier") is True only when STOCK_AUGMENT_VENUE_GUARD_ENABLED is False AND STOCK_LIVE_AUGMENT_ONLY is
          False — the same predicate that lets the vector fire these sources on a tradier sim (v12 `_n4_live_only`), so the
          vector and live open/close this pathway together. Defaults True / False -> guard on -> inert.
  crypto  live_enabled("crypto") is AUGMENT_CORE_LIVE_ENABLED (default False). The crypto vector always fires LADDER (that
          is the board today); live crypto native has had no LADDER producer since PARITY_VEC_EXACT_MODE=False (2026-10-20).

Pure functions, no I/O, no state. The caller owns order placement (execute_now via its normal queue) and provenance.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional, Tuple

from vec_decisions import gain_ladder_augment as _gla
from vec_decisions import uagain_gate as _uag

Get = Callable[[str, Any], Any]
VEC_AUGMENT_MULT = 0.5  # v12_quick_engine compute_augment_signals_ex: mult = np.full(n, 0.5)


class _Cfg:
    """getattr adapter over a (key, default) getter so the existing vec predicates (which take a config object) run unchanged."""

    def __init__(self, get: Get):
        self._get = get

    def __getattr__(self, k):
        if k.startswith("_"):
            raise AttributeError(k)
        _miss = object()
        v = self._get(k, _miss)
        if v is _miss:
            raise AttributeError(k)
        return v


def _b(get: Get, k: str, d: bool) -> bool:
    try:
        v = get(k, d)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    except Exception:
        return d


def _f(v: Any, d: float = 0.0) -> float:
    try:
        x = float(v)
        return x if x == x else d
    except (TypeError, ValueError):
        return d


def live_enabled(get: Get, venue: str) -> bool:
    """True when the vector-only augment sources are live-executable on this venue for this key (see module doc)."""
    if str(venue).lower() in ("tradier", "stocks", "stock"):
        return (not _b(get, "STOCK_AUGMENT_VENUE_GUARD_ENABLED", True)) and (not _b(get, "STOCK_LIVE_AUGMENT_ONLY", False))
    return _b(get, "AUGMENT_CORE_LIVE_ENABLED", False)


def profit_ok(get: Get, gain_pct: float) -> bool:
    """v12 `_augment_allowed` for the LADDER/PYRAMID/BOUNCE sources: AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER -> gain > 0, else
    gain <= BOUNCE_AUGMENT_MIN_LOSS_PCT (the vector's literal fallback)."""
    if _b(get, "AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER", False):
        return gain_pct > 0
    return gain_pct <= _f(get("BOUNCE_AUGMENT_MIN_LOSS_PCT", -0.5), -0.5)


def pyramid_sig(get: Get, ind: Mapping[str, Any], is_long: bool) -> bool:
    if not _b(get, "PYRAMID_ENABLED", False):
        return False
    dc = _f(ind.get("dc_position_15m"), 0.0)
    vel = _f(ind.get("wt_velocity_1h"), 0.0)
    if is_long:
        return dc >= _f(get("PYRAMID_MIN_DC_POS_15M", 0.7), 0.7) and vel >= _f(get("PYRAMID_MIN_WT_VEL_1H", 2.0), 2.0)
    return dc <= _f(get("PYRAMID_MAX_DC_POS_15M_SHORT", 0.3), 0.3) and vel <= -_f(get("PYRAMID_MIN_WT_VEL_1H", 2.0), 2.0)


def bounce_sig(get: Get, ind: Mapping[str, Any], is_long: bool, px: float, k_prev: Optional[float]) -> bool:
    if not (_b(get, "BOUNCE_AUGMENT_ENABLED", False) and _b(get, "BOUNCE_AUGMENT_K_D_CROSSING_UP", True)):
        return False
    if k_prev is None:
        return False
    k = _f(ind.get("stoch_k_15m", ind.get("stoch_k")), 50.0)
    d = _f(ind.get("stoch_d_15m", ind.get("stoch_d")), 50.0)
    kp = _f(k_prev, k)
    thr = _f(get("BOUNCE_AUGMENT_K_D_THRESHOLD", 20.0), 20.0)
    tol = _f(get("BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE", 0.02), 0.02)
    if is_long:
        lvl = _f(ind.get("dc_low_4h"), 0.0)
        cross = kp <= d and k > d and k < thr
    else:
        lvl = _f(ind.get("dc_high_4h"), 0.0)
        cross = kp >= d and k < d and k > (100.0 - thr)
    return bool(cross and lvl > 0 and abs(px - lvl) / max(lvl, 1e-9) <= tol)


def cooldown_ok(get: Get, secs_since_last_aug: Optional[float]) -> bool:
    if secs_since_last_aug is None:
        return True
    return secs_since_last_aug >= _f(get("AUGMENTATION_COOLDOWN_SECONDS", 300.0), 300.0)


def decide(get: Get, is_long: bool, px: float, entry_px: float, last_aug_px: float, gain_pct: float, max_gain_pct: float, pos_qty: float, ind: Optional[Mapping[str, Any]] = None, k_prev: Optional[float] = None, secs_since_last_aug: Optional[float] = None) -> Tuple[bool, str, str, float]:
    """(fire, source, reason, add_qty) for ONE bar of ONE open position. source in {"", "LADDER", "PYRAMID", "BOUNCE"}.

    Same order as the vector: LADDER first (its own profit gate inside gain_ladder_fire), then the signal sources through the
    UAG gate. The common profit gate and cooldown are applied after, as in the vector's choke point."""
    m = ind or {}
    if px <= 0 or pos_qty <= 0:
        return False, "", "", 0.0
    cfg = _Cfg(get)
    ref = _f(last_aug_px, 0.0) or _f(entry_px, 0.0)
    fire, reason = _gla.gain_ladder_fire(cfg, is_long, px, ref, gain_pct, max_gain_pct)
    source, mult = ("LADDER", VEC_AUGMENT_MULT) if fire else ("", 0.0)
    if not fire:
        py = pyramid_sig(get, m, is_long)
        bo = (not py) and bounce_sig(get, m, is_long, px, k_prev)
        if (py or bo) and _uag.uagain_gate_pass(cfg, is_long, px, ref, gain_pct, max_gain_pct, None):
            fire = True
            source = "PYRAMID" if py else "BOUNCE"
            mult = _f(get("PYRAMID_SIZE_MULT", 0.5), 0.5) if py else VEC_AUGMENT_MULT
            reason = "VEC_AUGMENT_SIG " + source
    if not fire:
        return False, "", "", 0.0
    if not profit_ok(get, gain_pct) or not cooldown_ok(get, secs_since_last_aug):
        return False, "", "", 0.0
    return True, source, f"AUGMENT_CORE_{source} {reason}", float(pos_qty) * float(mult)
