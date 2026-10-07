"""
vec_paths/reentry.py — Vectorized REENTRY entry paths.

Models 4 REENTRY variants that appear in live trading history:

  REENTRY_BREAKOUT  — price reclaimed DC_HIGH_15m + DC_BASIS_5m (backup logic);
                      mirrors tradier_manage (backup era) + the REENTRY_BREAKOUT
                      reason seen in March 2026 GOOGL history.

  REENTRY_PULLBACK  — profit-taking reduce followed by >=DROP_PCT drop + WT3m
                      oversold + cross; mirrors ez_reentry_pullback.py logic.

  REENTRY_TREND     — HA green/red + WT cross + stoch cross on 5m + 15m for
                      crypto/tradier; mirrors backup tradier_manage.py REENTRY_TREND.

  REENTRY_PROBE     — HA aligned + safety_vol, smallest size; mirrors
                      backup tradier_manage.py REENTRY_PROBE.

All 4 are gated by pos_state.last_close_ts + cfg.ENTRY_COOLDOWN_SEC so they
won't double-fire within the cooldown window.

Returns dict:
  {
    'side':      'LONG' | 'SHORT',
    'size_mult': float,
    'reason':    str,     # e.g. 'REENTRY_BREAKOUT', 'REENTRY_PULLBACK', ...
  }
or None.

NOTE: REENTRY_PULLBACK requires pos_state to carry last_reduce_price — a field
we add to _PositionState in vec_engine_v1.  When last_reduce_price == 0 this
variant is skipped (no reduce on record yet).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from vec_engine_v1 import _NPZStore, VecConfig, _PositionState


def check_reentry_entry(
    store: "_NPZStore",
    bar_idx: int,
    sym: str,
    pos_state: "_PositionState",
    side: str,
    cfg: "VecConfig",
) -> Optional[Dict]:
    """Evaluate all 4 REENTRY variants for a position that is currently CLOSED.

    Args:
        store:      NPZ indicator store for the symbol.
        bar_idx:    Current bar index in the store.
        sym:        Symbol string (for logging).
        pos_state:  _PositionState for this (sym, side) — must have open=False.
        side:       'LONG' or 'SHORT'.
        cfg:        VecConfig instance.

    Returns winning dict or None.
    """
    if pos_state.open:
        return None

    if not getattr(cfg, "REENTRY_ENABLED", True):
        return None

    is_long = side == "LONG"
    price = store.f("close", bar_idx, 0.0)
    if price <= 0:
        return None

    sf_b = lambda k, d=False: store.b(k, bar_idx, d)
    sf_f = lambda k, d=0.0: store.f(k, bar_idx, d)
    sf_s = lambda k, d="": store.s(k, bar_idx, d)

    btf = "5m"

    rel_vol = sf_f(f"relative_volume_{btf}", 1.0)
    safety_vol = rel_vol > 0.6

    # ── REENTRY_BREAKOUT ────────────────────────────────────────
    # price > dc_high_15m AND price > dc_basis_5m (for LONG)
    # mfi_15m < 75 (for LONG) / < 60 (for SHORT)
    if getattr(cfg, "REENTRY_BREAKOUT_ENABLED", True):
        dc_high_15m = sf_f("dc_high_15m", 0.0)
        dc_low_15m = sf_f("dc_low_15m", 0.0)
        dc_basis_5m = sf_f("dc_basis_5m", 0.0)
        mfi_15m = sf_f("mfi_15m", 50.0)
        if is_long:
            breakout = (dc_high_15m > 0 and price > dc_high_15m and dc_basis_5m > 0 and price > dc_basis_5m)
            mfi_ok = mfi_15m < 75
        else:
            breakout = (dc_low_15m > 0 and price < dc_low_15m and dc_basis_5m > 0 and price < dc_basis_5m)
            mfi_ok = mfi_15m < 60
        if breakout and mfi_ok and safety_vol:
            dc_level = dc_high_15m if is_long else dc_low_15m
            return {
                "side": side,
                "size_mult": 1.1,
                "reason": f"REENTRY_BREAKOUT dc15={dc_level:.2f}",
            }

    # ── REENTRY_PULLBACK ─────────────────────────────────────────
    # Requires: last_reduce_price recorded, price dropped >= DROP_PCT from reduce,
    # WT3m oversold + cross, optional WT15m cross + vel.
    if getattr(cfg, "REENTRY_PULLBACK_ENABLED", True):
        last_reduce_price = getattr(pos_state, "last_reduce_price", 0.0)
        drop_pct_thr = float(getattr(cfg, "REENTRY_PULLBACK_DROP_PCT", 3.0)) / 100.0
        os_thresh = float(getattr(cfg, "REENTRY_PULLBACK_WT3M_OS_THRESH", -10.0))
        require_15m = bool(getattr(cfg, "REENTRY_PULLBACK_REQUIRE_15M", True))
        require_vel = bool(getattr(cfg, "REENTRY_PULLBACK_REQUIRE_VEL", True))
        if last_reduce_price > 0:
            pulled_back = (
                (is_long and price <= last_reduce_price * (1.0 - drop_pct_thr)) or
                (not is_long and price >= last_reduce_price * (1.0 + drop_pct_thr))
            )
            if pulled_back:
                wt1_3m = sf_f("wt1_3m", 0.0)
                wt2_3m = sf_f("wt2_3m", 0.0)
                wt1_15m = sf_f("wt1_15m", 0.0)
                wt2_15m = sf_f("wt2_15m", 0.0)
                wt_vel_3m = sf_f("wt_velocity_3m", 0.0)
                prev_idx = max(0, bar_idx - 1)
                wt1_3m_prev = store.f("wt1_3m", prev_idx, wt1_3m)
                if is_long:
                    was_oversold = wt1_3m_prev < os_thresh
                    crossed_3m = wt1_3m > wt2_3m
                    crossed_15m = wt1_15m > wt2_15m
                    vel_ok = wt_vel_3m > 0
                else:
                    was_oversold = wt1_3m_prev > abs(os_thresh)
                    crossed_3m = wt1_3m < wt2_3m
                    crossed_15m = wt1_15m < wt2_15m
                    vel_ok = wt_vel_3m < 0
                signal_ok = was_oversold and crossed_3m
                if require_15m:
                    signal_ok = signal_ok and crossed_15m
                if require_vel:
                    signal_ok = signal_ok and vel_ok
                if signal_ok:
                    drop_actual = abs(last_reduce_price / max(price, 1e-9) - 1) * 100 if is_long else abs(price / max(last_reduce_price, 1e-9) - 1) * 100
                    return {
                        "side": side,
                        "size_mult": 1.0,
                        "reason": f"REENTRY_PULLBACK drop={drop_actual:.1f}% wt3m={wt1_3m:.1f}",
                    }

    # ── REENTRY_TREND ────────────────────────────────────────────
    # HA green/red on 5m + 15m + WT cross in direction + stoch cross
    if getattr(cfg, "REENTRY_TREND_ENABLED", True):
        ha_5m = sf_s("ha_color_5m") or sf_s("ha_5m")
        ha_15m = sf_s("ha_color_15m") or sf_s("ha_15m")
        wt1_5m = sf_f("wt1_5m", 0.0)
        wt2_5m = sf_f("wt2_5m", 0.0)
        k_5m = sf_f("stoch_k_5m", 50.0)
        d_5m = sf_f("stoch_d_5m", 50.0)
        if is_long:
            trend_resume = (ha_5m == "green" and ha_15m == "green" and wt1_5m > wt2_5m and k_5m > d_5m)
        else:
            trend_resume = (ha_5m == "red" and ha_15m == "red" and wt1_5m < wt2_5m and k_5m < d_5m)
        if trend_resume and safety_vol:
            return {
                "side": side,
                "size_mult": 1.0,
                "reason": "REENTRY_TREND ha+wt+stoch",
            }

    # ── REENTRY_PROBE ────────────────────────────────────────────
    # HA aligned only, smallest size
    if getattr(cfg, "REENTRY_PROBE_ENABLED", True):
        ha_5m = sf_s("ha_color_5m") or sf_s("ha_5m")
        ha_aligned = (is_long and ha_5m == "green") or (not is_long and ha_5m == "red")
        if ha_aligned and safety_vol:
            return {
                "side": side,
                "size_mult": 0.6,
                "reason": "REENTRY_PROBE ha_aligned",
            }

    # ── REENTRY_WT15M_CROSS (FIX: was declared in VecConfig but not implemented) ──
    # Source: tradier_manage.py REENTRY_WT15M_CROSS path — fires a size-mult entry
    # when wt1_15m freshly crosses wt2_15m in the trade direction (current > prev).
    # REENTRY_WT15M_HTF_FAVOR_REQUIRED: additionally requires HTF WT aligned on 1h/4h.
    # Size mult from REENTRY_WT15M_SIZE_MULT (default 1.5); K gate from REENTRY_WT15M_K_MAX.
    if getattr(cfg, "REENTRY_WT15M_CROSS_ENABLED", True):
        wt1_15m = sf_f("wt1_15m", 0.0)
        wt2_15m = sf_f("wt2_15m", 0.0)
        prev_idx = max(0, bar_idx - 1)
        wt1_15m_prev = store.f("wt1_15m", prev_idx, wt1_15m)
        wt2_15m_prev = store.f("wt2_15m", prev_idx, wt2_15m)
        k_15m = sf_f("stoch_k_15m", 50.0)
        _wt15m_k_max = float(getattr(cfg, "REENTRY_WT15M_K_MAX", 50.0))
        if is_long:
            _wt15m_cross = (wt1_15m > wt2_15m) and (wt1_15m_prev <= wt2_15m_prev)
            _wt15m_k_ok = k_15m < _wt15m_k_max
        else:
            _wt15m_cross = (wt1_15m < wt2_15m) and (wt1_15m_prev >= wt2_15m_prev)
            _wt15m_k_ok = k_15m > (100.0 - _wt15m_k_max)
        if _wt15m_cross and _wt15m_k_ok:
            _htf_ok = True
            if bool(getattr(cfg, "REENTRY_WT15M_HTF_FAVOR_REQUIRED", True)):
                wt1_1h = sf_f("wt1_1h", 0.0)
                wt2_1h = sf_f("wt2_1h", 0.0)
                wt1_4h = sf_f("wt1_4h", 0.0)
                wt2_4h = sf_f("wt2_4h", 0.0)
                _htf_ok = is_long and (wt1_1h > wt2_1h or wt1_4h > wt2_4h)
                if not is_long:
                    _htf_ok = (wt1_1h < wt2_1h or wt1_4h < wt2_4h)
            if _htf_ok:
                _re15m_mult = float(getattr(cfg, "REENTRY_WT15M_SIZE_MULT", 1.5))
                return {
                    "side": side,
                    "size_mult": _re15m_mult,
                    "reason": f"REENTRY_WT15M_CROSS wt15m={wt1_15m:.1f} k15m={k_15m:.1f}",
                }

    # ── REENTRY_K15M_PARTIAL (FIX: was declared in VecConfig but not implemented) ──
    # Source: tradier_manage.py — partial-size reentry when stoch_k_15m crosses threshold.
    # Requires OVERSOLD cross (K < 30 for LONG, K > 70 for SHORT) AND rising K + WT 15m aligned.
    # Only fires a SMALLER position (mult=REENTRY_K15M_PARTIAL_MULT, default 0.5) to
    # scale in gradually as K recovers. Threshold from REENTRY_K15M_PARTIAL_THRESHOLD (50).
    if getattr(cfg, "REENTRY_K15M_PARTIAL_ENABLED", True):
        k_15m = sf_f("stoch_k_15m", 50.0)
        d_15m = sf_f("stoch_d_15m", 50.0)
        prev_idx = max(0, bar_idx - 1)
        k_15m_prev = store.f("stoch_k_15m", prev_idx, k_15m)
        d_15m_prev = store.f("stoch_d_15m", prev_idx, d_15m)
        wt1_15m_k = sf_f("wt1_15m", 0.0)
        wt2_15m_k = sf_f("wt2_15m", 0.0)
        _k15m_thr = float(getattr(cfg, "REENTRY_K15M_PARTIAL_THRESHOLD", 50.0))
        # Require oversold/overbought zone + fresh K cross + WT aligned
        if is_long:
            _k15m_os_zone = k_15m < 30.0  # oversold zone
            _k15m_cross = (k_15m > d_15m) and (k_15m_prev <= d_15m_prev)
            _k15m_range = k_15m < _k15m_thr
            _wt15m_aligned = wt1_15m_k > wt2_15m_k
        else:
            _k15m_os_zone = k_15m > 70.0  # overbought zone
            _k15m_cross = (k_15m < d_15m) and (k_15m_prev >= d_15m_prev)
            _k15m_range = k_15m > (100.0 - _k15m_thr)
            _wt15m_aligned = wt1_15m_k < wt2_15m_k
        if _k15m_cross and _k15m_range and _k15m_os_zone and _wt15m_aligned:
            _k15m_mult = float(getattr(cfg, "REENTRY_K15M_PARTIAL_MULT", 0.5))
            return {
                "side": side,
                "size_mult": _k15m_mult,
                "reason": f"REENTRY_K15M_PARTIAL k15m={k_15m:.1f} d15m={d_15m:.1f}",
            }

    # ── REENTRY_POST_CONSOL (FIX: was declared in VecConfig but not implemented) ──
    # Source: tradier_manage.py — fires after price consolidates (low ATR) then expands.
    # Requires: ATR < REENTRY_POST_CONSOL_ATR_THRESHOLD relative to price (low vol period),
    # AND WT crosses in direction on REENTRY_POST_CONSOL_TFS_REQUIRED TFs.
    # Size mult from REENTRY_POST_CONSOL_MULT (default 1.5).
    if getattr(cfg, "REENTRY_POST_CONSOL_ENABLED", False):
        btf_for_consol = "5m"
        atr = sf_f(f"atr_{btf_for_consol}", 0.0)
        price_here = store.price(bar_idx)
        _atr_thr = float(getattr(cfg, "REENTRY_POST_CONSOL_ATR_THRESHOLD", 0.005))
        _tfs_req = int(getattr(cfg, "REENTRY_POST_CONSOL_TFS_REQUIRED", 2))
        if price_here > 0 and atr > 0:
            atr_rel = atr / price_here
            _consol_low_vol = atr_rel < _atr_thr
            if _consol_low_vol:
                _consol_tfs = ["5m", "15m", "1h", "4h"]
                _wt_cross_ok = 0
                for _ctf in _consol_tfs:
                    _wt1_c = sf_f(f"wt1_{_ctf}", 0.0)
                    _wt2_c = sf_f(f"wt2_{_ctf}", 0.0)
                    if is_long and _wt1_c > _wt2_c:
                        _wt_cross_ok += 1
                    elif not is_long and _wt1_c < _wt2_c:
                        _wt_cross_ok += 1
                if _wt_cross_ok >= _tfs_req:
                    _consol_mult = float(getattr(cfg, "REENTRY_POST_CONSOL_MULT", 1.5))
                    return {
                        "side": side,
                        "size_mult": _consol_mult,
                        "reason": f"REENTRY_POST_CONSOL atr_rel={atr_rel:.4f} wt_ok={_wt_cross_ok}TF",
                    }

    return None
